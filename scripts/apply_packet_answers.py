#!/usr/bin/env python3
"""Validate packet answers and atomically extend immutable answers.jsonl.

Usage: python3 scripts/apply_packet_answers.py PACKETS_DIR [WAVE_OR_ANSWERS ...]
           [--answered-by MODEL] [--amend [--reason TEXT]]
Accepted JSON: {"answers": [{"request_id": "...", "answer": "..."}]}, the
list alone, or {"<request_id>": "<answer>"}. Optional response_id, usage and
provider timing fields are preserved; omitted accounting remains unknown.

--answered-by MODEL records the model that answered (an exact model id, e.g.
the session's) on every answer applied that does not name its own
`answered_by`; the run reports of proofread_pages.py count the answers they
replay by it (packetAnsweredBy), so one answering model per book can be
checked.

A recorded answer is never rewritten.  --amend applies an answer for a
request that is answered already as an amendment: a new record that names
the SHA-256 of the answer it supersedes (`amends`, with --reason when given)
and replaces it on replay, the old record kept in answers.jsonl as the audit
trail.  A page already sealed with the old answer keeps its text until it is
proofread again (--overwrite --pages, the pages printed).  Without --amend a
different answer for an answered request is refused (exit 2).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jyut_ocr.proofread.packet_client import (  # noqa: E402
    PACKET_SCHEMA_VERSION, _atomic_write_bytes, _sha256_bytes, amendment_response_id, answer_response_id,
    answer_sha256, load_answers_map, normalize_fused_answer, normalize_usage, validate_answer_compatibility,
)
from jyut_ocr.proofread import packet_events as _packet_events  # noqa: E402

MAX_MODEL_ID = 256


class ApplyAnswersError(RuntimeError):
    """Invalid or incomplete packet answer submission."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ApplyAnswersError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError) as error:
        raise ApplyAnswersError(f"cannot parse JSON {path}: {error}") from error


def find_latest_wave(packets_dir: Path) -> Path:
    waves = sorted((p for p in packets_dir.glob("wave-*")
                    if p.is_dir() and re.fullmatch(r"wave-\d+", p.name)),
                   key=lambda p: int(p.name.split("-")[1]))
    if not waves:
        raise ApplyAnswersError(f"no wave-NN directory found in {packets_dir}")
    return waves[-1]


def load_wave_items(wave_dir: Path) -> dict[str, dict[str, Any]]:
    bundles = sorted(wave_dir.glob("bundle-*.json"))
    if not bundles:
        raise ApplyAnswersError(f"no bundle-*.json files found in {wave_dir}")
    manifest_path = wave_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    if manifest_path.is_file():
        request_ids = manifest.get("request_ids") if isinstance(manifest, Mapping) else None
        if (not isinstance(request_ids, list) or any(not isinstance(rid, str) for rid in request_ids)
                or len(set(request_ids)) != len(request_ids)
                or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] not in (1, PACKET_SCHEMA_VERSION)
                or not isinstance(manifest.get("stage"), str)
                or manifest.get("wave") != wave_dir.name):
            raise ApplyAnswersError(f"invalid published wave manifest: {manifest_path}")
    if manifest.get("schema_version", 1) >= PACKET_SCHEMA_VERSION:
        seals = {Path(row["bundle"]).name: row["sha256"] for row in manifest["bundles"]}
        if set(seals) != {p.name for p in bundles}:
            raise ApplyAnswersError(f"sealed wave bundle inventory changed: {wave_dir}")
        for path in bundles:
            if _sha256_bytes(path.read_bytes()) != seals[path.name]:
                raise ApplyAnswersError(f"sealed wave bundle changed: {path}")
    items: dict[str, dict[str, Any]] = {}
    for bundle_path in bundles:
        data = _read_json(bundle_path)
        if not isinstance(data, Mapping):
            raise ApplyAnswersError(f"invalid packet bundle: {bundle_path}")
        if data.get("schema_version", 1) >= PACKET_SCHEMA_VERSION and manifest.get("schema_version") != data["schema_version"]:
            raise ApplyAnswersError(f"missing or mismatched published wave manifest: {manifest_path}")
        for item in data.get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("request_id"), str):
                raise ApplyAnswersError(f"invalid request item in {bundle_path}")
            req_id = item["request_id"]
            if req_id in items:
                raise ApplyAnswersError(f"duplicate request_id {req_id} in wave bundles")
            items[req_id] = item
    if not items:
        raise ApplyAnswersError(f"bundles in {wave_dir} list zero items")
    if manifest_path.is_file() and set(manifest["request_ids"]) != set(items):
        raise ApplyAnswersError(f"manifest request_ids differ from bundle items: {manifest_path}")
    return items


def parse_answer_file(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, Mapping):
        if "answers" in raw:
            raw_list = raw["answers"]
            if not isinstance(raw_list, list):
                raise ApplyAnswersError(f"answers must be a list in {path}")
        else:
            raw_list = []
            for key, value in raw.items():
                if not re.fullmatch(r"[0-9a-f]{16,64}", key):
                    raise ApplyAnswersError(f"invalid request_id {key!r} in {path}")
                if isinstance(value, Mapping) and "answer" in value:
                    raw_list.append({**value, "request_id": key})
                else:
                    raw_list.append({"request_id": key, "answer": value})
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raise ApplyAnswersError(f"unsupported JSON root in {path}")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_list, start=1):
        if not isinstance(item, Mapping):
            raise ApplyAnswersError(f"entry {index} in {path} is not a JSON object")
        req_id = item.get("request_id")
        if not isinstance(req_id, str) or not re.fullmatch(r"[0-9a-f]{16,64}", req_id):
            raise ApplyAnswersError(f"entry {index} in {path} has invalid request_id")
        if req_id in seen:
            raise ApplyAnswersError(f"duplicate request_id {req_id} in {path}")
        seen.add(req_id)
        entry = dict(item)
        if "answer" not in entry:
            if "census" not in entry and "punctuated" not in entry:
                raise ApplyAnswersError(f"entry {index} ({req_id}) in {path} is missing answer")
            entry["answer"] = {"census": item.get("census", ""), "punctuated": item.get("punctuated", "")}
        entries.append(entry)
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, epilog=_packet_events.HELP,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packets_dir", type=Path)
    parser.add_argument("sources", nargs="*", type=Path, help="wave directory or answer JSON (default: latest wave)")
    parser.add_argument("--allow-partial", action="store_true", help="allow incomplete wave answers")
    parser.add_argument("--allow-unknown-ids", action="store_true", help="allow IDs absent from selected wave")
    parser.add_argument("--answered-by", metavar="MODEL",
                        help="the model id that answered, recorded on each answer that names none")
    parser.add_argument("--amend", action="store_true",
                        help="supersede the recorded answer of a request answered already with the one given "
                             "(the old record stays in answers.jsonl as the audit trail)")
    parser.add_argument("--reason", help="with --amend: why the answer is amended, recorded with it")
    return parser


def model_id(value: Any, where: str) -> str:
    """An answering model's id: a nonempty single-line string."""
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_MODEL_ID
            or any(ord(char) < 32 for char in value)):
        raise ApplyAnswersError(f"answered_by {where} must be a nonempty one-line model id of at most "
                                f"{MAX_MODEL_ID} characters")
    return value.strip()


def _valid_checkpoint_prefix(raw: bytes) -> bytes:
    """Drop only a syntax-broken final legacy append, after load validation."""
    lines = raw.splitlines(keepends=True)
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    if nonempty:
        last = nonempty[-1]
        try:
            json.loads(lines[last])
        except ValueError:
            return b"".join(lines[:last])
    return raw


def _apply(args: argparse.Namespace) -> int:
    packets_dir = args.packets_dir.expanduser().resolve()
    if not packets_dir.is_dir():
        raise ApplyAnswersError(f"packets directory does not exist: {packets_dir}")
    default_model = model_id(args.answered_by, "(--answered-by)") if args.answered_by is not None else None
    if args.reason is not None and not args.amend:
        raise ApplyAnswersError("--reason goes with --amend")
    events = _packet_events.PacketEvents(packets_dir)
    # The same lock covers validation, conflict detection and publication. No
    # competing process can pass validation against stale checkpoint bytes.
    with (packets_dir / ".packets.lock").open("a", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        wave_dirs: list[Path] = []
        answer_files: list[Path] = []
        for src in args.sources or [find_latest_wave(packets_dir)]:
            resolved = src.expanduser().resolve()
            if resolved.is_dir():
                wave_dirs.append(resolved)
                answer_files.extend(sorted(resolved.glob("answers-*.json")))
            elif resolved.is_file():
                answer_files.append(resolved)
                if (resolved.parent / "manifest.json").is_file() and resolved.parent not in wave_dirs:
                    wave_dirs.append(resolved.parent)
            else:
                raise ApplyAnswersError(f"source path does not exist: {resolved}")
        if not answer_files:
            raise ApplyAnswersError("no answers-*.json files found to apply")
        if not wave_dirs and not args.allow_unknown_ids:
            wave_dirs.append(find_latest_wave(packets_dir))
        expected_items: dict[str, dict[str, Any]] = {}
        for wave_dir in dict.fromkeys(wave_dirs):
            for req_id, item in load_wave_items(wave_dir).items():
                if req_id in expected_items and item != expected_items[req_id]:
                    raise ApplyAnswersError(f"conflicting request_id {req_id} across waves")
                expected_items[req_id] = item
        submitted: dict[str, dict[str, Any]] = {}
        pages: dict[str, Any] = {}
        for path in answer_files:
            for entry in parse_answer_file(path):
                req_id = entry["request_id"]
                if req_id in submitted:
                    raise ApplyAnswersError(f"duplicate submitted request_id {req_id}")
                item = expected_items.get(req_id)
                if item is None and not args.allow_unknown_ids:
                    raise ApplyAnswersError(f"unknown request_id {req_id!r} in {path} (not in selected wave)")
                kind = str((item or {}).get("kind") or "unknown")
                answer = entry["answer"]
                if kind == "census+punctuate" or (isinstance(answer, Mapping)
                        and ("census" in answer or "punctuated" in answer)):
                    answer = normalize_fused_answer(answer)
                if not isinstance(answer, str) or not answer.strip():
                    raise ApplyAnswersError(f"answer for {req_id} in {path} must be a nonempty string")
                answered_by = (model_id(entry["answered_by"], f"for {req_id} in {path}")
                               if entry.get("answered_by") is not None else default_model)
                submitted[req_id] = {
                    "schema_version": PACKET_SCHEMA_VERSION, "request_id": req_id,
                    "kind": kind, "answer": answer, **normalize_usage(entry),
                    "response_id": answer_response_id(entry),
                    "finish_reason": entry.get("finish_reason"),
                    **({"answered_by": answered_by} if answered_by else {}),
                }
                pages[req_id] = (item or {}).get("scan_page")
        answers_path = packets_dir / "answers.jsonl"
        already_answered = load_answers_map(answers_path)
        missing = [req_id for req_id in expected_items if req_id not in submitted and req_id not in already_answered]
        if missing and not args.allow_partial:
            raise ApplyAnswersError(f"{len(missing)} of {len(expected_items)} wave item(s) still missing answers "
                                    f"(e.g. {missing[:5]}); submit all bundles or pass --allow-partial")
        responses = {answer_response_id(rec): normalize_usage(rec) for rec in already_answered.values()}
        new_records: list[dict[str, Any]] = []
        amended: list[str] = []
        for req_id, record in submitted.items():
            old = already_answered.get(req_id)
            amendment = False
            if old is not None and old.get("amends") is not None and record["response_id"] == f"packet:{req_id}":
                # The amended answer again, with no response id of its own: the amendment's.
                record["response_id"] = answer_response_id(old)
            if old is not None:
                try:
                    validate_answer_compatibility(old, record)
                except ValueError:
                    if not args.amend:
                        raise
                    amendment = True
            if amendment:
                # A new record superseding OLD, which stays where it is.
                if record["response_id"] == answer_response_id(old):
                    record["response_id"] = amendment_response_id(req_id, record["answer"])
                record["amends"] = {"answer_sha256": answer_sha256(old),
                                    "response_id": answer_response_id(old),
                                    "applied_at": old.get("applied_at"),
                                    **({"reason": args.reason} if args.reason else {})}
            response_id, usage = answer_response_id(record), normalize_usage(record)
            if response_id in responses and responses[response_id] != usage:
                raise ApplyAnswersError(f"conflicting usage for response_id {response_id}")
            responses[response_id] = usage
            if old is None or amendment:
                new_records.append({**record, "applied_at": _utc_now()})
                if amendment:
                    amended.append(req_id)
        if new_records:
            old_bytes = answers_path.read_bytes() if answers_path.is_file() else b""
            prefix = _valid_checkpoint_prefix(old_bytes)
            if prefix != old_bytes:
                # Preserve the exact damaged append evidence before repairing.
                recovery = packets_dir / ".recovery" / f"answers.{_sha256_bytes(old_bytes)}.jsonl"
                if not recovery.is_file():
                    _atomic_write_bytes(recovery, old_bytes)
            if prefix and not prefix.endswith(b"\n"):
                prefix += b"\n"
            new_bytes = b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in new_records)
            _atomic_write_bytes(answers_path, prefix + new_bytes)
        total_known = len(set(already_answered) | set(submitted))
        events.applied(wave_dirs, added=len(new_records) - len(amended), amended=len(amended),
                       unchanged=len(submitted) - len(new_records), remaining=len(missing),
                       checkpoint_answers=total_known)
    models: dict[str, int] = {}
    for record in new_records:
        key = record.get("answered_by") or "unrecorded"
        models[key] = models.get(key, 0) + 1
    print(f"[packets] applied {len(new_records) - len(amended)} new answer(s)"
          + (f" and {len(amended)} amendment(s)" if amended else "")
          + f" ({len(submitted)} submitted, {total_known} total in {answers_path})"
          + (f"; answered by " + ", ".join(f"{model} {n}" for model, n in sorted(models.items()))
             if models else "")
          + (f"; {len(missing)} wave item(s) still pending" if missing else ""), flush=True)
    if amended:
        sealed_pages = sorted({pages[req_id] for req_id in amended if isinstance(pages.get(req_id), int)})
        print(f"[packets] {len(amended)} amended answer(s) supersede the recorded ones (kept in {answers_path.name} "
              "as the audit trail); a page sealed with an old answer keeps its text until it is proofread again"
              + (f": --overwrite --pages {','.join(map(str, sealed_pages))}" if sealed_pages else ""), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _apply(build_parser().parse_args(argv))
    except (ApplyAnswersError, ValueError, OSError, KeyError, TypeError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
