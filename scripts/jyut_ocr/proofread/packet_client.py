"""Checkpoint-replay packet transport (`PacketClient`) for agent/subagent execution.

Instead of blocking on an external paid HTTP VLM server, `book_profile.py` and
`proofread_pages.py` default to `--backend packet`:

1. Every model question is content-addressed (`request_id`) from its inputs
   (system prompt, prompt, image), the model label, max tokens, effort and the
   caller's decision configuration - never from the bytes of the helper code,
   so editing a script does not orphan the answers already given.
2. Answered questions in `<packets_dir>/answers.jsonl` replay without another provider call,
   running through 100% of the deterministic character/mark/table validators
   (`verify_characters`, `restore_merged_characters`, `punctuation_pass_ok`,
   `check_rebuilt_table`, `deleted_runs`).
3. Unanswered questions across all pages/blocks of the current wave are batched
   into `<packets_dir>/wave-NN/bundle-01..KK.json` with deduplicated PNG crops
   in `<packets_dir>/crops/`: `max_bundles` files, or more when a bundle would
   otherwise hold more than `max_items_per_bundle` questions (default
   DEFAULT_MAX_ITEMS_PER_BUNDLE), so no answerer is handed hundreds of page
   images at once.  The script exits with `EXIT_PACKETS_PENDING` (`10`) so the
   session orchestrator can dispatch parallel subagents on the bundles, run
   `apply_packet_answers.py`, and re-run; proofread_pages.py exits
   `EXIT_PACKETS_PENDING_WITH_FAILURES` (`11`) instead when pages also failed
   or were blocked, so a pending wave is never hidden behind exit 1.
4. An answer records the model that gave it (`answered_by`, from
   apply_packet_answers.py --answered-by or the answer itself) and the usage
   its answerer measured; a page's usage counts that usage once
   (proofread_pages.aggregate_usage, PACKET_USAGE_SOURCES).
5. answers.jsonl is append-only.  A recorded answer is changed only by an
   amendment (apply_packet_answers.py --amend): a later record for the same
   request whose `amends` names the SHA-256 of the answer it supersedes.  The
   superseded record stays in the file as the audit trail; load_answers_map
   replays the latest.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import threading
from typing import Any, Mapping, Sequence

from jyut_ocr.paths import script_path

EXIT_PACKETS_PENDING = 10
# proofread_pages.py: answers pending, and pages failed or blocked too.
EXIT_PACKETS_PENDING_WITH_FAILURES = 11
# Questions one wave bundle holds at most.
DEFAULT_MAX_ITEMS_PER_BUNDLE = 20
# The usage_source of a replayed packet answer: its answerer's recorded usage
# (or none recorded), which a page's usage counts (aggregate_usage).
PACKET_USAGE_SOURCES = ("answer-record", "unavailable")
PACKET_SCHEMA_VERSION = 2
REQUEST_CONTRACT_VERSION = 2
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens")
TIMING_FIELDS = ("seconds", "waitSeconds", "serverPromptMs", "serverDecodeMs")
# Compatibility exports: existing callers and answer imports keep these names.
from jyut_ocr.proofread.punctuation_fusion import (
    FUSED_DELIMITER, normalize_fused_answer, split_fused_answer, ask_fused,
)

CURRENT_SCAN_PAGE: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "packet_current_scan_page", default=None
)
CURRENT_RENDER_INFO: contextvars.ContextVar[tuple[Path, str] | None] = contextvars.ContextVar(
    "packet_current_render_info", default=None
)


class PacketPending(Exception):
    """Raised after registering an unanswered VLM request in `PacketClient`."""

    def __init__(self, request_id: str):
        super().__init__(f"packet pending: {request_id}")
        self.request_id = request_id


@dataclass(frozen=True)
class PacketItem:
    request_id: str
    kind: str
    scan_page: int | None
    order_index: int
    system: str
    user_text: str
    image_path: str | None
    image_sha256: str | None
    max_tokens: int
    site: dict[str, Any] | None
    request_config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "request_id": self.request_id,
            "kind": self.kind,
            "scan_page": self.scan_page,
            "system": self.system,
            "user_text": self.user_text,
            "image_path": self.image_path,
            "image_sha256": self.image_sha256,
            "max_tokens": self.max_tokens,
            "request_config": self.request_config,
        }
        if self.site:
            out["site"] = self.site
        return out


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def compute_request_id(
    kind: str,
    start: str | None,
    system: str,
    user_text: str,
    image_sha256: str | None,
    *,
    model: str = "agent-packet",
    max_tokens: int | None = None,
    effort: str | None = None,
    request_config: Mapping[str, Any] | None = None,
) -> str:
    # v1 omitted max_tokens/model/effort and must remain historical: never fall
    # back to its ID when a current request has no answer.
    payload = json.dumps({
        "schema_version": PACKET_SCHEMA_VERSION,
        "contract_version": REQUEST_CONTRACT_VERSION,
        "kind": kind, "start": start, "system": system, "user_text": user_text,
        "image_sha256": image_sha256, "model": model, "max_tokens": max_tokens,
        "effort": effort, "request_config": dict(request_config or {}),
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def normalize_usage(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep unknown provider usage unknown, and validate subset semantics."""
    usage = record.get("usage")
    if usage is not None and not isinstance(usage, Mapping):
        raise ValueError("usage must be an object or null")
    usage = usage or {}
    out: dict[str, Any] = {}
    for field in USAGE_FIELDS + TIMING_FIELDS:
        candidates = [source[field] for source in (record, usage) if field in source]
        details_name = {"cached_tokens": "prompt_tokens_details",
                        "reasoning_tokens": "completion_tokens_details"}.get(field)
        if details_name:
            details = usage.get(details_name) or {}
            if not isinstance(details, Mapping):
                raise ValueError(f"{details_name} must be an object")
            if field in details:
                candidates.append(details[field])
        if any(value != candidates[0] for value in candidates[1:]):
            raise ValueError(f"conflicting {field} usage fields")
        value = candidates[0] if candidates else None
        if value is not None:
            valid = (isinstance(value, int) and not isinstance(value, bool)) if field in USAGE_FIELDS else (
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
            if not valid or value < 0:
                raise ValueError(f"{field} must be a nonnegative number or null")
        out[field] = value
    for subset, total in (("cached_tokens", "prompt_tokens"), ("reasoning_tokens", "completion_tokens")):
        if out[subset] is not None and out[total] is not None and out[subset] > out[total]:
            raise ValueError(f"{subset} cannot exceed {total}")
    return out


def answer_response_id(record: Mapping[str, Any]) -> str:
    response_id = record.get("response_id")
    if response_id is not None:
        if not isinstance(response_id, str) or not response_id.strip():
            raise ValueError("response_id must be a nonempty string")
        return response_id
    return "packet:" + str(record["request_id"])


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def answer_sha256(record: Mapping[str, Any]) -> str:
    """SHA-256 of a recorded answer's text: what an amendment names (`amends`)."""
    return _sha256_bytes(str(record.get("answer", "")).encode("utf-8"))


def amendment_response_id(request_id: str, answer: str) -> str:
    """The response identity of an amendment that names none: not the
    superseded answer's ("packet:<request_id>"), whose usage it does not share."""
    return f"packet:{request_id}:amend:{_sha256_bytes(answer.encode('utf-8'))[:16]}"


def validate_answer_compatibility(old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
    """An answered request is immutable, including its accounting evidence,
    unless it is amended (apply_packet_answers.py --amend)."""
    if old.get("answer") != new.get("answer"):
        raise ValueError(f"conflicting answer for {new['request_id']}: it is answered already; to replace that "
                         "answer, apply the new one with apply_packet_answers.py --amend (the old record stays "
                         "in answers.jsonl as the audit trail)")
    if normalize_usage(old) != normalize_usage(new) or answer_response_id(old) != answer_response_id(new):
        raise ValueError(f"conflicting usage/response identity for {new['request_id']}: it is answered already; "
                         "to replace that record, apply it with apply_packet_answers.py --amend")



def load_answers_map(answers_path: Path) -> dict[str, dict[str, Any]]:
    """Load immutable answers, rejecting conflicting duplicate identities.

    Tolerates a crash-truncated trailing line at EOF with a stderr warning while
    still rejecting corrupt records earlier in the file.
    """
    if not answers_path.is_file():
        return {}
    answers: dict[str, dict[str, Any]] = {}
    responses: dict[str, dict[str, Any]] = {}
    raw_lines = answers_path.read_bytes().splitlines()
    last_nonempty = max(
        (idx for idx, line in enumerate(raw_lines, start=1) if line.strip()),
        default=0,
    )
    for line_no, raw_line in enumerate(raw_lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            if line_no == last_nonempty:
                print(
                    f"WARNING ignoring truncated trailing JSON line at {answers_path}:{line_no}",
                    file=sys.stderr,
                )
                break
            raise ValueError(f"corrupt JSON at {answers_path}:{line_no}: {error}") from error
        if not isinstance(entry, dict) or not isinstance(entry.get("request_id"), str):
            raise ValueError(f"invalid answer record at {answers_path}:{line_no}")
        usage = normalize_usage(entry)
        response_id = answer_response_id(entry)
        if response_id in responses and responses[response_id] != usage:
            raise ValueError(f"conflicting usage for response_id {response_id} at {answers_path}:{line_no}")
        responses[response_id] = usage
        previous = answers.get(entry["request_id"])
        amends = entry.get("amends")
        if amends is not None:
            # An amendment supersedes the answer it names, and only that one.
            if not isinstance(amends, Mapping) or not isinstance(amends.get("answer_sha256"), str):
                raise ValueError(f"invalid amendment record at {answers_path}:{line_no}")
            if previous is None:
                raise ValueError(f"amendment at {answers_path}:{line_no} amends no recorded answer "
                                 f"for {entry['request_id']}")
            if answer_sha256(previous) != amends["answer_sha256"]:
                raise ValueError(f"amendment at {answers_path}:{line_no} does not amend the current answer "
                                 f"for {entry['request_id']}")
            answers[entry["request_id"]] = entry
        elif previous is not None:
            validate_answer_compatibility(previous, entry)
        else:
            answers[entry["request_id"]] = entry
    return answers


@contextlib.contextmanager
def packet_page_context(scan_page: int, render_path: Path | None = None, render_sha256: str | None = None):
    """Bind the current page number and full-page render path for packet emission."""
    tok_page = CURRENT_SCAN_PAGE.set(scan_page)
    tok_render = None
    if render_path is not None and render_sha256 is not None:
        tok_render = CURRENT_RENDER_INFO.set((render_path.resolve(), render_sha256))
    try:
        yield
    finally:
        CURRENT_SCAN_PAGE.reset(tok_page)
        if tok_render is not None:
            CURRENT_RENDER_INFO.reset(tok_render)


class PacketClient:
    """Drop-in replacement for `proofread_pages.Client` using checkpoint-replay packets."""

    supports_fused_punctuate = True

    def __init__(
        self,
        packets_dir: Path,
        *,
        max_bundles: int = 8,
        max_items_per_bundle: int = DEFAULT_MAX_ITEMS_PER_BUNDLE,
        model: str = "agent-packet",
        effort: str | None = None,
        request_config: Mapping[str, Any] | None = None,
        call_ledger_var: contextvars.ContextVar[list[dict[str, Any]] | None] | None = None,
        call_site_var: contextvars.ContextVar[Mapping[str, Any] | None] | None = None,
    ):
        self.packets_dir = packets_dir.expanduser().resolve()
        self.crops_dir = self.packets_dir / "crops"
        self.answers_path = self.packets_dir / "answers.jsonl"
        self.max_bundles = max(1, max_bundles)
        self.max_items_per_bundle = max(1, max_items_per_bundle)
        self.model = model
        self.effort = effort
        # Not the helpers' bytes (user decision of 2026-11-14): a code edit
        # must not orphan stored answers.  The prompts and images, which the
        # ID hashes, change when the questions do.
        self.request_config = {
            "contract_version": REQUEST_CONTRACT_VERSION,
            "caller": dict(request_config or {}),
        }
        # Snapshot caller config so later mutations cannot alter this client's
        # identity. Reject unserializable/non-finite values before emission.
        self.request_config = json.loads(json.dumps(self.request_config, sort_keys=True, allow_nan=False))
        self._ledger_var = call_ledger_var
        self._site_var = call_site_var
        self._lock = threading.Lock()
        self._counter = 0
        self._pending: dict[str, PacketItem] = {}
        # request_id -> the model recorded as answering it, for each answer replayed.
        self._replayed: dict[str, str | None] = {}
        self.answers = load_answers_map(self.answers_path)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def answered_by_summary(self) -> dict[str, int]:
        """Answers replayed by this client, each once, by the model recorded as
        answering them ("unrecorded" for an answer applied without one)."""
        with self._lock:
            models = list(self._replayed.values())
        out: dict[str, int] = {}
        for model in models:
            key = model or "unrecorded"
            out[key] = out.get(key, 0) + 1
        return out

    def pending_items(self) -> list[PacketItem]:
        with self._lock:
            return sorted(
                self._pending.values(),
                key=lambda item: (
                    item.scan_page is None,
                    item.scan_page or 0,
                    item.request_id,
                ),
            )

    def _store_image(self, image_bytes: bytes | None) -> tuple[str | None, str | None]:
        if image_bytes is None:
            return None, None
        img_sha = _sha256_bytes(image_bytes)
        render_info = CURRENT_RENDER_INFO.get()
        if render_info is not None and render_info[1] == img_sha and render_info[0].is_file():
            if _sha256_bytes(render_info[0].read_bytes()) != img_sha:
                raise ValueError(f"full-page render changed: {render_info[0]}")
            return str(render_info[0]), img_sha
        crop_path = self.crops_dir / f"{img_sha[:24]}.png"
        if not crop_path.is_file():
            _atomic_write_bytes(crop_path, image_bytes)
        elif _sha256_bytes(crop_path.read_bytes()) != img_sha:
            raise ValueError(f"content-addressed crop changed: {crop_path}")
        return str(crop_path), img_sha

    def _record_ledger(self, kind: str, max_tokens: int, metrics: Mapping[str, Any]) -> None:
        if self._ledger_var is None:
            return
        ledger = self._ledger_var.get()
        if ledger is None:
            return
        with self._lock:
            if any(row.get("request_id") == metrics.get("request_id") for row in ledger):
                return
            ledger.append({
                "kind": kind,
                "maxTokens": max_tokens,
                "seconds": None,
                "waitSeconds": None,
                "serverPromptMs": None,
                "serverDecodeMs": None,
                "cached_tokens": None,
                "padded": None,
                **metrics,
            })

    def _register_or_replay(
        self,
        *,
        kind: str,
        start: str | None,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        image_path, img_sha = self._store_image(image_bytes)
        request_id = compute_request_id(kind, start, system, user_text, img_sha,
                                        model=self.model, max_tokens=max_tokens,
                                        effort=self.effort, request_config=self.request_config)
        answered = self.answers.get(request_id)
        if answered is not None:
            raw_answer = answered.get("answer", "")
            text = (
                normalize_fused_answer(raw_answer)
                if kind == "census+punctuate"
                else str(raw_answer)
            )
            usage = normalize_usage(answered)
            answered_by = answered.get("answered_by") if isinstance(answered.get("answered_by"), str) else None
            metrics = {
                **usage,
                "finish_reason": answered.get("finish_reason"),
                "effort": self.effort,
                "request_id": request_id,
                "response_id": answer_response_id(answered),
                # Replayed: no call in this process.  Its answerer's usage
                # (usage_source) still counts once in the page's usage.
                "reused": True,
                "charged": False,
                "usage_source": "answer-record" if any(usage[key] is not None
                                                         for key in USAGE_FIELDS) else "unavailable",
                "answered_by": answered_by,
            }
            with self._lock:
                self._replayed[request_id] = answered_by
            self._record_ledger(kind, max_tokens, metrics)
            return text, metrics

        site_raw = self._site_var.get() if self._site_var is not None else None
        site_copy = dict(site_raw) if isinstance(site_raw, Mapping) and site_raw else None
        scan_page = CURRENT_SCAN_PAGE.get()
        with self._lock:
            if request_id not in self._pending:
                self._counter += 1
                self._pending[request_id] = PacketItem(
                    request_id=request_id,
                    kind=kind,
                    scan_page=scan_page,
                    order_index=self._counter,
                    system=system,
                    user_text=user_text,
                    image_path=image_path,
                    image_sha256=img_sha,
                    max_tokens=max_tokens,
                    site=site_copy,
                    request_config={"model": self.model, "effort": self.effort, "start": start,
                                    "max_tokens": max_tokens, **self.request_config},
                )
        raise PacketPending(request_id)

    def ask_answering(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        kind: str = "other",
        start: str | None = "",
    ) -> tuple[str, dict[str, Any]]:
        return self._register_or_replay(
            kind=kind,
            start=start,
            system=system,
            user_text=user_text,
            image_bytes=image_bytes,
            max_tokens=max_tokens,
        )

    def ask(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
        kind: str = "other",
    ) -> tuple[str, dict[str, Any]]:
        return self.ask_answering(
            system, user_text, image_bytes, max_tokens, kind=kind, start=effort_override
        )

    def fused_census_and_punctuate(
        self,
        *,
        crop_bytes: bytes | None,
        span: str,
        hint: str,
        leaders: int | None,
        show_doubled: bool,
        max_tokens: int,
        census_system: str,
        punct_system: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Compatibility wrapper for the shared transport-independent question."""
        return ask_fused(self, crop_bytes=crop_bytes, span=span, hint=hint, leaders=leaders,
                         show_doubled=show_doubled, max_tokens=max_tokens,
                         census_system=census_system, punct_system=punct_system)

    def write_wave_bundles(self, *, stage: str = "proofread") -> dict[str, Any]:
        """Partition all pending items into `<= self.max_bundles` wave bundle files."""
        items = self.pending_items()
        if not items:
            return {"pending": 0, "waveDir": None, "bundles": []}
        self.packets_dir.mkdir(parents=True, exist_ok=True)
        wanted_ids = [item.request_id for item in items]

        with self._open_wave_lock():
            wave_dir = self._select_wave_dir(wanted_ids, stage)
            manifest_path = wave_dir / "manifest.json"
            if manifest_path.is_file():
                # Replaying a wave is read-only: worker seals bind the exact
                # bundle bytes, including generated_at and partitioning.
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
                for summary in manifest["bundles"]:
                    path = Path(summary["bundle"])
                    if _sha256_bytes(path.read_bytes()) != summary["sha256"]:
                        raise ValueError(f"sealed wave bundle changed: {path}")
                return self._wave_result(manifest, wave_dir)

            # Publish all bundles + manifest together. Interrupted construction
            # leaves only an unsealed staging directory, never a partial wave.
            staging = Path(tempfile.mkdtemp(prefix=f".{wave_dir.name}.", dir=self.packets_dir))
            # max_bundles files, or more when one would hold more than
            # max_items_per_bundle questions.
            bundle_count = max(min(len(items), self.max_bundles),
                               math.ceil(len(items) / self.max_items_per_bundle))
            buckets = _partition_items(items, bundle_count)
            bundle_summaries: list[dict[str, Any]] = []
            generated_at = _utc_now()
            for idx, bucket in enumerate(buckets, start=1):
                bundle_name = f"bundle-{idx:02d}.json"
                answers_name = f"answers-{idx:02d}.json"
                bundle_path = wave_dir / bundle_name
                answers_path = wave_dir / answers_name
                payload = {
                    "schema_version": PACKET_SCHEMA_VERSION,
                    "stage": stage,
                    "wave": wave_dir.name,
                    "bundle_index": idx,
                    "bundle_count": len(buckets),
                    "generated_at": generated_at,
                    "expected_answers_file": str(answers_path),
                    "answer_format_instructions": (
                        "Write UTF-8 JSON to expected_answers_file with schema: "
                        '{"answers": [{"request_id": "<id>", "answer": "<exact model reply text>"}, ...]}. '
                        "Each request_id must occur exactly once. Optional response_id and usage "
                        "must come from measured provider metadata; absent usage is unknown, never zero. "
                        "Optional answered_by names the model that answered (else apply_packet_answers.py "
                        "--answered-by records it). "
                        "Inspect every item's image_path visually (when non-null) before answering, "
                        "and follow each item's system and user_text instructions strictly. "
                        "Answer every item: an image that is hard to read, or that does not settle "
                        "the question, is no reason to leave an item or the bundle unanswered. Give "
                        "the answer the item's own instructions name for that case, else your best "
                        "reading; the scripts check every answer and ask again where they must."
                    ),
                    "item_count": len(bucket),
                    "pages": sorted({it.scan_page for it in bucket if it.scan_page is not None}),
                    "kinds": sorted({it.kind for it in bucket}),
                    "items": [it.to_dict() for it in bucket],
                }
                payload_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                _atomic_write_bytes(staging / bundle_name, payload_bytes)
                bundle_summaries.append({
                    "bundle": str(bundle_path), "expectedAnswers": str(answers_path),
                    "itemCount": len(bucket), "pages": payload["pages"], "kinds": payload["kinds"],
                    "sha256": _sha256_bytes(payload_bytes),
                })

            manifest = {
                "schema_version": PACKET_SCHEMA_VERSION,
                "stage": stage, "wave": wave_dir.name, "generated_at": generated_at,
                "packets_dir": str(self.packets_dir), "answers_jsonl": str(self.answers_path),
                "total_items": len(items), "bundle_count": len(buckets),
                "request_ids": wanted_ids, "bundles": bundle_summaries,
                "apply_command": shlex.join([
                    sys.executable, str(script_path("apply_packet_answers.py").resolve()),
                    str(self.packets_dir), str(wave_dir),
                ]),
            }
            _atomic_write_bytes(staging / "manifest.json",
                                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            os.rename(staging, wave_dir)
            _fsync_directory(self.packets_dir)
            return self._wave_result(manifest, wave_dir)

    @staticmethod
    def _wave_result(manifest: Mapping[str, Any], wave_dir: Path) -> dict[str, Any]:
        return {"pending": manifest["total_items"], "wave": wave_dir.name,
                "waveDir": str(wave_dir), "bundles": manifest["bundles"],
                "applyCommand": manifest["apply_command"]}

    @contextlib.contextmanager
    def _open_wave_lock(self):
        self.packets_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.packets_dir / ".packets.lock"
        with open(lock_file, "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _select_wave_dir(self, wanted_ids: Sequence[str], stage: str) -> Path:
        existing_waves = sorted(
            (p for p in self.packets_dir.glob("wave-*") if re.fullmatch(r"wave-\d+", p.name)),
            key=lambda p: int(p.name.split("-")[1]),
        )
        for wave_dir in reversed(existing_waves):
            manifest_path = wave_dir / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
                if (not isinstance(manifest, dict) or not isinstance(manifest.get("request_ids"), list)
                        or type(manifest.get("schema_version")) is not int
                        or manifest["schema_version"] not in (1, PACKET_SCHEMA_VERSION)
                        or not isinstance(manifest.get("stage"), str)
                        or manifest.get("wave") != wave_dir.name):
                    raise ValueError("manifest schema, stage, wave or request_ids are invalid")
                request_ids = manifest["request_ids"]
                if any(not isinstance(rid, str) for rid in request_ids) or len(set(request_ids)) != len(request_ids):
                    raise ValueError("manifest request_ids must contain unique strings")
            except (OSError, ValueError) as error:
                raise ValueError(f"invalid published wave manifest {manifest_path}: {error}") from error
            if (manifest.get("schema_version") == PACKET_SCHEMA_VERSION
                    and manifest.get("stage") == stage
                    and sorted(request_ids) == sorted(wanted_ids)):
                return wave_dir
        next_num = 1
        if existing_waves:
            last_num = max(int(p.name.split("-")[1]) for p in existing_waves)
            next_num = last_num + 1
        return self.packets_dir / f"wave-{next_num:02d}"


def _partition_items(items: Sequence[PacketItem], bundle_count: int) -> list[list[PacketItem]]:
    """Slice ordered `items` into `bundle_count` contiguous balanced buckets so items
    from the same scan page stay together whenever possible."""
    total = len(items)
    buckets: list[list[PacketItem]] = []
    cursor = 0
    for idx in range(bundle_count):
        remaining_items = total - cursor
        remaining_buckets = bundle_count - idx
        take = (remaining_items + remaining_buckets - 1) // remaining_buckets
        buckets.append(list(items[cursor : cursor + take]))
        cursor += take
    return [b for b in buckets if b]
