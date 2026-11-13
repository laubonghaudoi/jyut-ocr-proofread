#!/usr/bin/env python3
"""Validate subagent packet answers and append them to `<packets_dir>/answers.jsonl`.

Usage:
    python3 scripts/apply_packet_answers.py PACKETS_DIR [WAVE_DIR_OR_ANSWERS_JSON ...]

Accepted answer file JSON shapes:
1. `{"answers": [{"request_id": "...", "answer": "..."}, ...]}`
2. `[{"request_id": "...", "answer": "..."}, ...]`
3. `{"<request_id>": "<answer_string_or_fused_object>", ...}`
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _packet_client import normalize_fused_answer  # noqa: E402


class ApplyAnswersError(RuntimeError):
    """Invalid or incomplete packet answer submission."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_latest_wave(packets_dir: Path) -> Path:
    waves = sorted(
        (p for p in packets_dir.glob("wave-*") if p.is_dir() and re.fullmatch(r"wave-\d+", p.name)),
        key=lambda p: int(p.name.split("-")[1]),
    )
    if not waves:
        raise ApplyAnswersError(f"no wave-NN directory found in {packets_dir}")
    return waves[-1]


def load_wave_items(wave_dir: Path) -> dict[str, dict[str, Any]]:
    bundles = sorted(wave_dir.glob("bundle-*.json"))
    if not bundles:
        raise ApplyAnswersError(f"no bundle-*.json files found in {wave_dir}")
    items: dict[str, dict[str, Any]] = {}
    for bundle_path in bundles:
        try:
            data = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ApplyAnswersError(f"unreadable bundle {bundle_path}: {error}") from error
        for raw_item in data.get("items") or []:
            if isinstance(raw_item, dict) and isinstance(raw_item.get("request_id"), str):
                items[raw_item["request_id"]] = raw_item
    if not items:
        raise ApplyAnswersError(f"bundles in {wave_dir} list zero items")
    return items


def parse_answer_file(path: Path) -> list[tuple[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ApplyAnswersError(f"cannot parse JSON answer file {path}: {error}") from error

    entries: list[tuple[str, Any]] = []
    if isinstance(raw, Mapping):
        if "answers" in raw and isinstance(raw["answers"], list):
            raw_list = raw["answers"]
        else:
            for key, value in raw.items():
                if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{16,64}", key):
                    if isinstance(value, Mapping) and "answer" in value and "census" not in value:
                        entries.append((key, value["answer"]))
                    else:
                        entries.append((key, value))
            return entries
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raise ApplyAnswersError(f"unsupported JSON root in {path}")

    for index, item in enumerate(raw_list, start=1):
        if not isinstance(item, Mapping):
            raise ApplyAnswersError(f"entry {index} in {path} is not a JSON object")
        req_id = item.get("request_id")
        if not isinstance(req_id, str) or not req_id:
            raise ApplyAnswersError(f"entry {index} in {path} is missing string 'request_id'")
        if "answer" in item:
            ans = item["answer"]
        elif "census" in item or "punctuated" in item:
            ans = {"census": item.get("census", ""), "punctuated": item.get("punctuated", "")}
        else:
            raise ApplyAnswersError(f"entry {index} ({req_id}) in {path} is missing 'answer'")
        entries.append((req_id, ans))
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("packets_dir", type=Path, help="packets directory containing wave-NN/ and answers.jsonl")
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        help="wave-NN directory and/or answers-*.json files (default: latest wave-NN directory)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="record valid submitted answers even if some items in the wave do not have answers yet",
    )
    parser.add_argument(
        "--allow-unknown-ids",
        action="store_true",
        help="allow request_ids not listed in the selected wave's bundles",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        packets_dir = args.packets_dir.expanduser().resolve()
        if not packets_dir.is_dir():
            raise ApplyAnswersError(f"packets directory does not exist: {packets_dir}")

        wave_dirs: list[Path] = []
        answer_files: list[Path] = []
        if not args.sources:
            wave_dir = find_latest_wave(packets_dir)
            wave_dirs.append(wave_dir)
            answer_files.extend(sorted(wave_dir.glob("answers-*.json")))
        else:
            for src in args.sources:
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

        expected_items: dict[str, dict[str, Any]] = {}
        for wave_dir in wave_dirs:
            expected_items.update(load_wave_items(wave_dir))

        submitted: dict[str, tuple[str, str]] = {}
        for ans_file in answer_files:
            for req_id, raw_ans in parse_answer_file(ans_file):
                item_meta = expected_items.get(req_id)
                if item_meta is None and expected_items and not args.allow_unknown_ids:
                    raise ApplyAnswersError(
                        f"unknown request_id {req_id!r} in {ans_file} (not in selected wave)"
                    )
                kind = str((item_meta or {}).get("kind") or "unknown")
                if kind == "census+punctuate" or isinstance(raw_ans, Mapping):
                    text_ans = normalize_fused_answer(raw_ans)
                else:
                    if not isinstance(raw_ans, str):
                        raise ApplyAnswersError(
                            f"answer for {req_id} in {ans_file} must be a string, got {type(raw_ans).__name__}"
                        )
                    text_ans = raw_ans
                if not text_ans.strip():
                    raise ApplyAnswersError(f"empty answer for {req_id} in {ans_file}")
                submitted[req_id] = (kind, text_ans)

        answers_jsonl = packets_dir / "answers.jsonl"
        lock_file = packets_dir / ".packets.lock"
        with open(lock_file, "a", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                already_answered: dict[str, str] = {}
                if answers_jsonl.is_file():
                    for line in answers_jsonl.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        if isinstance(rec, dict) and isinstance(rec.get("request_id"), str):
                            already_answered[rec["request_id"]] = str(rec.get("answer") or "")

                missing = [
                    req_id
                    for req_id in expected_items
                    if req_id not in submitted and req_id not in already_answered
                ]
                if missing and not args.allow_partial:
                    raise ApplyAnswersError(
                        f"{len(missing)} of {len(expected_items)} wave item(s) still missing answers "
                        f"(e.g. {missing[:5]}); submit all bundles or pass --allow-partial"
                    )

                appended = 0
                now = _utc_now()
                lines_to_append: list[str] = []
                for req_id, (kind, text_ans) in submitted.items():
                    if already_answered.get(req_id) == text_ans:
                        continue
                    record = {
                        "request_id": req_id,
                        "kind": kind,
                        "answer": text_ans,
                        "applied_at": now,
                    }
                    lines_to_append.append(json.dumps(record, ensure_ascii=False))
                    appended += 1

                if lines_to_append:
                    with open(answers_jsonl, "a", encoding="utf-8") as out_handle:
                        out_handle.write("\n".join(lines_to_append) + "\n")
                        out_handle.flush()
                        os.fsync(out_handle.fileno())
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

        total_known = len(set(already_answered) | set(submitted))
        print(
            f"[packets] applied {appended} new answer(s) ({len(submitted)} submitted, "
            f"{total_known} total in {answers_jsonl})"
            + (f"; {len(missing)} wave item(s) still pending" if missing else ""),
            flush=True,
        )
        return 0
    except ApplyAnswersError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
