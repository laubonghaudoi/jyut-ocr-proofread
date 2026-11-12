"""Checkpoint-replay packet transport (`PacketClient`) for agent/subagent execution.

Instead of blocking on an external paid HTTP VLM server, `book_profile.py` and
`proofread_pages.py` default to `--backend packet`:

1. Every model question is content-addressed (`request_id`) from its kind,
   effort-ladder start, system prompt, user text, and image SHA-256.
2. Answered questions in `<packets_dir>/answers.jsonl` replay in 0ms on re-entry,
   running through 100% of the deterministic character/mark/table validators
   (`verify_characters`, `restore_merged_characters`, `punctuation_pass_ok`,
   `check_rebuilt_table`, `deleted_runs`).
3. Unanswered questions across all pages/blocks of the current wave are batched
   into `<packets_dir>/wave-NN/bundle-01..KK.json` (`<= max_bundles` files) with
   deduplicated PNG crops in `<packets_dir>/crops/`, and the script exits with
   `EXIT_PACKETS_PENDING` (`10`) so the session orchestrator can dispatch
   parallel subagents on the bundles, run `apply_packet_answers.py`, and re-run.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any, Mapping, Sequence

EXIT_PACKETS_PENDING = 10
PACKET_SCHEMA_VERSION = 1
FUSED_DELIMITER = "---PUNCTUATED---"

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
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def compute_request_id(
    kind: str,
    start: str | None,
    system: str,
    user_text: str,
    image_sha256: str | None,
) -> str:
    start_token = "" if start == "" else ("none" if start is None else str(start))
    img_token = image_sha256 or ""
    payload = (
        f"v{PACKET_SCHEMA_VERSION}\0{kind}\0{start_token}\0{system}\0{user_text}\0{img_token}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def normalize_fused_answer(raw: Any) -> str:
    """Canonicalize a `census+punctuate` answer into `census\\n---PUNCTUATED---\\npunctuated`."""
    if isinstance(raw, Mapping):
        census_part = str(raw.get("census") or "").strip()
        punct_part = str(raw.get("punctuated") or "").strip()
        return f"{census_part}\n{FUSED_DELIMITER}\n{punct_part}"
    text = str(raw or "").strip()
    if text.startswith("{") and text.endswith("}"):
        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(text)
            if isinstance(parsed, Mapping) and ("census" in parsed or "punctuated" in parsed):
                census_part = str(parsed.get("census") or "").strip()
                punct_part = str(parsed.get("punctuated") or "").strip()
                return f"{census_part}\n{FUSED_DELIMITER}\n{punct_part}"
    return text


def split_fused_answer(text: str, fallback_span: str) -> tuple[str, str]:
    """Split a canonicalized `census+punctuate` answer into `(census_text, punctuated_text)`."""
    normalized = normalize_fused_answer(text)
    if FUSED_DELIMITER in normalized:
        left, _, right = normalized.partition(FUSED_DELIMITER)
        punct = right.strip()
        if punct in ("無標點", "無", "(無標點)"):
            punct = fallback_span
        return left.strip(), punct
    # Graceful fallback if the answer omitted the delimiter after reporting 符號：0
    lines = normalized.splitlines()
    census_lines: list[str] = []
    body_lines: list[str] = []
    in_header = True
    for line in lines:
        if in_header and re.match(r"^\s*(?:符號|雙圈|形狀)[：:]", line):
            census_lines.append(line)
        elif in_header and not line.strip() and census_lines:
            continue
        else:
            in_header = False
            body_lines.append(line)
    census_text = "\n".join(census_lines).strip()
    punct_text = "\n".join(body_lines).strip()
    if punct_text in ("", "無標點", "無", "(無標點)"):
        punct_text = fallback_span
    return census_text, punct_text


def load_answers_map(answers_path: Path) -> dict[str, dict[str, Any]]:
    """Load `<packets_dir>/answers.jsonl` into `{request_id: entry}` (last entry wins).

    Tolerates a crash-truncated trailing line at EOF with a stderr warning while
    still rejecting corrupt records earlier in the file.
    """
    if not answers_path.is_file():
        return {}
    answers: dict[str, dict[str, Any]] = {}
    raw_lines = answers_path.read_text(encoding="utf-8").splitlines()
    last_nonempty = max(
        (idx for idx, line in enumerate(raw_lines, start=1) if line.strip()),
        default=0,
    )
    for line_no, raw_line in enumerate(raw_lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except ValueError as error:
            if line_no == last_nonempty:
                print(
                    f"WARNING ignoring truncated trailing JSON line at {answers_path}:{line_no}",
                    file=sys.stderr,
                )
                break
            raise ValueError(f"corrupt JSON at {answers_path}:{line_no}: {error}") from error
        if not isinstance(entry, dict) or not isinstance(entry.get("request_id"), str):
            if line_no == last_nonempty:
                print(
                    f"WARNING ignoring invalid trailing answer record at {answers_path}:{line_no}",
                    file=sys.stderr,
                )
                break
            raise ValueError(f"invalid answer record at {answers_path}:{line_no}")
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
        model: str = "agent-packet",
        effort: str | None = None,
        call_ledger_var: contextvars.ContextVar[list[dict[str, Any]] | None] | None = None,
        call_site_var: contextvars.ContextVar[Mapping[str, Any] | None] | None = None,
    ):
        self.packets_dir = packets_dir.expanduser().resolve()
        self.crops_dir = self.packets_dir / "crops"
        self.answers_path = self.packets_dir / "answers.jsonl"
        self.max_bundles = max(1, max_bundles)
        self.model = model
        self.effort = effort
        self._ledger_var = call_ledger_var
        self._site_var = call_site_var
        self._lock = threading.Lock()
        self._counter = 0
        self._pending: dict[str, PacketItem] = {}
        self.answers = load_answers_map(self.answers_path)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def pending_items(self) -> list[PacketItem]:
        with self._lock:
            return sorted(
                self._pending.values(),
                key=lambda item: (
                    item.scan_page is None,
                    item.scan_page or 0,
                    item.order_index,
                    item.request_id,
                ),
            )

    def _store_image(self, image_bytes: bytes | None) -> tuple[str | None, str | None]:
        if image_bytes is None:
            return None, None
        img_sha = _sha256_bytes(image_bytes)
        render_info = CURRENT_RENDER_INFO.get()
        if render_info is not None and render_info[1] == img_sha and render_info[0].is_file():
            return str(render_info[0]), img_sha
        crop_path = self.crops_dir / f"{img_sha[:24]}.png"
        if not crop_path.is_file():
            _atomic_write_bytes(crop_path, image_bytes)
        return str(crop_path), img_sha

    def _record_ledger(self, kind: str, max_tokens: int, metrics: Mapping[str, Any]) -> None:
        if self._ledger_var is None:
            return
        ledger = self._ledger_var.get()
        if ledger is None:
            return
        ledger.append(
            {
                "kind": kind,
                "maxTokens": max_tokens,
                "seconds": 0.0,
                "waitSeconds": 0.0,
                "serverPromptMs": 0.0,
                "serverDecodeMs": 0.0,
                "cached_tokens": 0,
                "padded": False,
                **metrics,
            }
        )

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
        request_id = compute_request_id(kind, start, system, user_text, img_sha)
        answered = self.answers.get(request_id)
        if answered is not None:
            raw_answer = answered.get("answer", "")
            text = (
                normalize_fused_answer(raw_answer)
                if kind == "census+punctuate"
                else str(raw_answer)
            )
            metrics = {
                "prompt_tokens": int(answered.get("prompt_tokens") or 0),
                "completion_tokens": int(answered.get("completion_tokens") or 0),
                "reasoning_tokens": int(answered.get("reasoning_tokens") or 0),
                "finish_reason": "stop",
                "effort": self.effort,
            }
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
        """Single-wave combined census + punctuation packet for a block/tile crop."""
        combined_system = (
            "你同時負責「標點點算」同「逐欄加標點」兩步，對住同一張版面裁圖一次過完成。\n\n"
            "【第一步：點算標點】\n"
            f"{census_system}\n\n"
            "【第二步：喺已定嘅字入面加返標點】\n"
            f"{punct_system}\n\n"
            "【輸出格式（嚴格遵守，唔好加 code fence 或解釋）】\n"
            "符號：<印咗符號嘅位置數；雙圈算一處>\n"
            "雙圈：<其中幾多處係雙圈；冇就寫 0>\n"
            "形狀：<出現過嘅符號種類，冇就寫 無>\n"
            f"{FUSED_DELIMITER}\n"
            "<如果符號係 0，原封不動照抄上面已定嘅字；如果符號 > 0，輸出加返標點嘅同一段字（一行，唔准改任何漢字）>"
        )
        leader_note = (
            f"裁圖上量到 {leaders} 串點（導引點線或者刪節號），每串計一處，喺正文寫「……」。\n"
            if leaders
            else ""
        )
        doubled_note = (
            "雙圈（兩個圈連住印）喺點算計一處並報喺「雙圈：」，喺正文寫兩個圈「。。」。\n"
            if show_doubled
            else ""
        )
        user_text = (
            f"已定嘅字（唔准加、刪、改任何漢字，次序唔准變）：\n---\n{span}\n---\n"
            f"{leader_note}{doubled_note}{hint}"
            f"請先輸出三行點算（符號／雙圈／形狀），跟住一行 `{FUSED_DELIMITER}`，再輸出加咗標點嘅正文。"
        )
        raw_text, metrics = self._register_or_replay(
            kind="census+punctuate",
            start="",
            system=combined_system,
            user_text=user_text,
            image_bytes=crop_bytes,
            max_tokens=max_tokens,
        )
        census_text, punct_text = split_fused_answer(raw_text, span)
        return census_text, punct_text, metrics

    def write_wave_bundles(self, *, stage: str = "proofread") -> dict[str, Any]:
        """Partition all pending items into `<= self.max_bundles` wave bundle files."""
        items = self.pending_items()
        if not items:
            return {"pending": 0, "waveDir": None, "bundles": []}
        self.packets_dir.mkdir(parents=True, exist_ok=True)
        wanted_ids = [item.request_id for item in items]

        with self._open_wave_lock():
            wave_dir = self._select_wave_dir(wanted_ids)
            wave_dir.mkdir(parents=True, exist_ok=True)
            for old_bundle in wave_dir.glob("bundle-*.json"):
                if not (wave_dir / old_bundle.name.replace("bundle-", "answers-")).exists():
                    with contextlib.suppress(OSError):
                        old_bundle.unlink()

            bundle_count = min(len(items), self.max_bundles)
            buckets = _partition_items(items, bundle_count)
            bundle_summaries: list[dict[str, Any]] = []
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
                    "generated_at": _utc_now(),
                    "expected_answers_file": str(answers_path),
                    "answer_format_instructions": (
                        "Write UTF-8 JSON to expected_answers_file with schema: "
                        '{"answers": [{"request_id": "<id>", "answer": "<exact model reply text>"}, ...]}. '
                        "Inspect every item's image_path visually (when non-null) before answering, "
                        "and follow each item's system and user_text instructions strictly."
                    ),
                    "item_count": len(bucket),
                    "pages": sorted({it.scan_page for it in bucket if it.scan_page is not None}),
                    "kinds": sorted({it.kind for it in bucket}),
                    "items": [it.to_dict() for it in bucket],
                }
                _atomic_write_bytes(
                    bundle_path,
                    (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
                bundle_summaries.append(
                    {
                        "bundle": str(bundle_path),
                        "expectedAnswers": str(answers_path),
                        "itemCount": len(bucket),
                        "pages": payload["pages"],
                        "kinds": payload["kinds"],
                    }
                )

            manifest = {
                "schema_version": PACKET_SCHEMA_VERSION,
                "stage": stage,
                "wave": wave_dir.name,
                "generated_at": _utc_now(),
                "packets_dir": str(self.packets_dir),
                "answers_jsonl": str(self.answers_path),
                "total_items": len(items),
                "bundle_count": len(buckets),
                "request_ids": wanted_ids,
                "bundles": bundle_summaries,
                "apply_command": (
                    f"python3 scripts/apply_packet_answers.py {self.packets_dir} {wave_dir}"
                ),
            }
            _atomic_write_bytes(
                wave_dir / "manifest.json",
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            return {
                "pending": len(items),
                "wave": wave_dir.name,
                "waveDir": str(wave_dir),
                "bundles": bundle_summaries,
                "applyCommand": manifest["apply_command"],
            }

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

    def _select_wave_dir(self, wanted_ids: Sequence[str]) -> Path:
        existing_waves = sorted(
            p for p in self.packets_dir.glob("wave-*") if re.fullmatch(r"wave-\d+", p.name)
        )
        for wave_dir in reversed(existing_waves):
            manifest_path = wave_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            with contextlib.suppress(OSError, ValueError):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("request_ids") == list(wanted_ids):
                    return wave_dir
            break
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
