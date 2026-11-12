#!/usr/bin/env python3
"""Engine B's second look: read again, from a crop, the blocks its page reading left out.

HunyuanOCR reads a whole page with its document-parsing prompt, which tells it
to leave out headers and footers.  On a book with footnotes or dense bottom
matter it sometimes takes the notes for a footer and stops after the body.
Shown the block alone, the same engine with the same prompt reads it cleanly.

Run after BOTH engines have read the book (it needs engine A's blocks):

    python3 scripts/run_hunyuanocr_second_look.py RENDERS OCR_A_DIR OCR_B_DIR

Supports `schemaVersion: 2`, `SharedHunyuanClient` (`--server-mode shared`,
reusing the already-warmed shared broker socket without reloading weights),
`GPUQueue` lease discipline in `--server-mode dedicated`, and external
`--endpoint` servers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_hunyuanocr as hy  # noqa: E402
from _gpu_queue import GPUQueue, GPUQueueError, GPUQueueTimeout, add_queue_arguments  # noqa: E402
from _phase_budget import EXIT_INCOMPLETE, Budget, exit_on_sigterm  # noqa: E402

SCHEMA_VERSION = 2
SECOND_LOOK_LABEL = "second-look"
FURNITURE_LABELS = frozenset({"header", "footer", "number", "seal", "image", "figure", "chart"})
UNREAD_SHARE = 0.2
PIECE = 4
CROP_PAD = 0.004
LOOP_FACTOR = 3
LOOP_FLOOR = 200
ALIKE_SHARE = 0.5
RECURRING_PAGES = 3

_MARKUP = re.compile(r"\$[^$\n]{0,200}\$|</?[A-Za-z][^<>\n]{0,80}>|\\[A-Za-z]+")
# On short (< 3 page) documents where `recurring_blocks` has too few pages to
# fire, guard against re-cropping extreme-edge page numbers/folios into Engine B.
_EXTREME_MARGIN_FOLIO_RE = re.compile(
    r"\A[-—–─·•\s\[]*(?:第\s*[0-9０-９一二三四五六七八九十百千〇零]+\s*[頁页]|[0-9０-９]{1,4}|[ivxlcdmIVXLCDM]{1,8})[\s\]—–─·•-]*\Z"
)


def key_text(text: str) -> str:
    """TEXT as the string the two readings are compared on: its characters,
    letters and figures, markup and marks left out."""
    return "".join(c for c in _MARKUP.sub(" ", text) if c.isalnum()).casefold()


def pieces(key: str) -> list[str]:
    if len(key) >= PIECE:
        return [key[i : i + PIECE] for i in range(len(key) - PIECE + 1)]
    return [key] if len(key) >= 2 else []


def share_read(block_key: str, page_key: str) -> float | None:
    """The share of the block's pieces engine B's page text holds; None for a
    block too short to tell."""
    parts = pieces(block_key)
    if not parts:
        return None
    return sum(part in page_key for part in parts) / len(parts)


def overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """The share of box B ([x, y, width, height]) that box A covers."""
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    area = b[2] * b[3]
    return max(width, 0.0) * max(height, 0.0) / area if area > 0 else 0.0


def union(boxes: Sequence[Sequence[float]]) -> list[float]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


def is_short_doc_margin_furniture(
    block: Mapping[str, Any], box: Sequence[float], total_pages: int
) -> bool:
    """When total_pages < RECURRING_PAGES, exclude single-line folio/running-head
    strips sitting in the extreme outer top/bottom margin so they are not forced
    into Engine B as body text."""
    if total_pages >= RECURRING_PAGES:
        return False
    raw = str(block.get("text") or "").strip()
    if not raw or "\n" in raw:
        return False
    y0, h = float(box[1]), float(box[3])
    y1 = y0 + h
    if h > 0.045:
        return False
    if y1 <= 0.065 or y0 >= 0.935:
        if _EXTREME_MARGIN_FOLIO_RE.match(raw) or len(key_text(raw)) <= 16:
            return True
    return False


def unread_groups(
    blocks: Sequence[Mapping[str, Any]],
    page_text: str,
    recurring: frozenset[str] = frozenset(),
    *,
    total_pages: int = RECURRING_PAGES,
) -> list[dict[str, Any]]:
    """Engine A's blocks that engine B's PAGE_TEXT does not hold, as groups to
    read in one crop each."""
    page_key = key_text(page_text)
    state: list[bool | None] = []
    for block in blocks:
        box = block.get("boundingBox")
        key = key_text(str(block.get("text") or ""))
        if (
            block.get("blockLabel") in FURNITURE_LABELS
            or not isinstance(box, list)
            or len(box) != 4
            or key in recurring
            or is_short_doc_margin_furniture(block, box, total_pages)
        ):
            state.append(None)
            continue
        share = share_read(key, page_key)
        state.append(None if share is None else share < UNREAD_SHARE)
    read_boxes = [blocks[k]["boundingBox"] for k, unread in enumerate(state) if unread is False]
    groups: list[list[int]] = []
    for k, unread in enumerate(state):
        if not unread:
            continue
        previous = groups[-1] if groups else None
        joined = previous is not None and not any(
            state[j] is False for j in range(previous[-1] + 1, k)
        )
        if joined:
            box = union([blocks[j]["boundingBox"] for j in previous + [k]])
            joined = not any(overlap(box, other) > 0.1 for other in read_boxes)
        if joined:
            previous.append(k)
        else:
            groups.append([k])
    return [
        {
            "blocks": group,
            "box": [round(v, 6) for v in union([blocks[j]["boundingBox"] for j in group])],
            "text": "\n".join(str(blocks[j].get("text") or "") for j in group),
        }
        for group in groups
    ]


def recurring_blocks(directory: Path) -> tuple[frozenset[str], int]:
    """Return `(recurring_keys, total_page_files)` across Engine A's drafts."""
    seen: dict[str, int] = {}
    paths = sorted(directory.glob("page-*.json"))
    for path in paths:
        try:
            blocks = json.loads(path.read_bytes().decode("utf-8")).get("lines") or []
        except (OSError, UnicodeError, ValueError):
            continue
        for key in {key_text(str(block.get("text") or "")) for block in blocks}:
            if key:
                seen[key] = seen.get(key, 0) + 1
    return frozenset(key for key, pages in seen.items() if pages >= RECURRING_PAGES), len(paths)


def kept_lines(text: str, group_text: str, page_text: str) -> tuple[list[str], str | None]:
    if len(text) > LOOP_FACTOR * len(group_text) + LOOP_FLOOR:
        return [], f"a reading of {len(text)} characters for blocks of {len(group_text)}"
    alike = share_read(key_text(group_text), key_text(text))
    if alike is not None and alike < ALIKE_SHARE:
        return [], f"the reading holds {alike:.2f} of engine A's text of the blocks"
    page_key = key_text(page_text)
    lines = []
    for line in text.splitlines():
        held = share_read(key_text(line), page_key)
        if line.strip() and not (held is not None and held >= ALIKE_SHARE):
            lines.append(line.rstrip())
    return lines, None if lines else "engine B's page text holds the reading already"


def insertion_line(
    lines: Sequence[Mapping[str, Any]], blocks: Sequence[Mapping[str, Any]], first_block: int
) -> int:
    earlier = [
        set(pieces(key_text(str(block.get("text") or "")))) for block in blocks[:first_block]
    ]
    place = 0
    for n, line in enumerate(lines):
        parts = pieces(key_text(str(line.get("text") or "")))
        if parts and any(
            sum(part in block for part in parts) * 2 >= len(parts) for block in earlier
        ):
            place = n + 1
    return place


def own_lines(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(line)
        for line in record.get("lines") or []
        if line.get("blockLabel") != SECOND_LOOK_LABEL
    ]


def reseal(
    record: dict[str, Any], lines: list[dict[str, Any]], second_look: Mapping[str, Any]
) -> tuple[bytes, bytes]:
    """RECORD with LINES and SECOND_LOOK sealed identically to run_hunyuanocr.py
    (preserving schemaVersion: 2, rawText, rawResponse, toolSha256, ocrProfileSha256)."""
    text_bytes = hy.text_from_lines(lines)
    record = json.loads(json.dumps(record, ensure_ascii=False))
    record["lines"] = lines
    record["sentenceMarks"] = sum(
        1 for c in text_bytes.decode("utf-8") if c in hy.SENTENCE_MARKS
    )
    record["secondLook"] = dict(second_look)
    record["provenance"]["textSha256"] = hy.sha256_bytes(text_bytes)
    record["provenance"].pop("jsonPayloadSha256", None)
    record["provenance"]["jsonPayloadSha256"] = hy.sha256_bytes(
        hy.canonical_json_bytes(record, pretty=True)
    )
    return hy.canonical_json_bytes(record, pretty=True), text_bytes


def write_pair(json_path: Path, text_path: Path, json_bytes: bytes, text_bytes: bytes) -> None:
    with tempfile.TemporaryDirectory(dir=str(json_path.parent)) as staging:
        sj, st = Path(staging) / "p.json", Path(staging) / "p.txt"
        sj.write_bytes(json_bytes)
        st.write_bytes(text_bytes)
        os.replace(st, text_path)
        os.replace(sj, json_path)


def tool_sha256(model: Path | None, mmproj: Path | None) -> str:
    combined = hashlib.sha256()
    combined.update(
        b"run_hunyuanocr_second_look.py\0" + Path(__file__).resolve().read_bytes()
    )
    for label, path in (("model", model), ("mmproj", mmproj)):
        if path is not None and path.is_file():
            combined.update(f"\0{label}\0".encode() + hy.sha256_file(path).encode())
    return combined.hexdigest()


def extract_ocr_text(result: Any) -> str:
    """Unpack either a schemaVersion-2 OpenAI response dict or a raw string."""
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
            if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                return message["content"]
    raise hy.PageOCRFailure("unexpected OCR response payload shape")


def crop_png(render: Path, box: Sequence[float], directory: Path) -> Path:
    from PIL import Image

    with Image.open(render) as image:
        width, height = image.size
        pad = CROP_PAD * height
        x0, y0 = max(0, int(box[0] * width - pad)), max(0, int(box[1] * height - pad))
        x1 = min(width, int((box[0] + box[2]) * width + pad) + 1)
        y1 = min(height, int((box[1] + box[3]) * height + pad) + 1)
        out = directory / "crop.png"
        image.crop((x0, y0, x1, y1)).convert("RGB").save(out)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("render_directory", type=Path)
    p.add_argument(
        "engine_a_directory",
        type=Path,
        help="engine A's drafts (page-NNNN.json with its blocks)",
    )
    p.add_argument(
        "output_directory", type=Path, help="engine B's drafts, rewritten in place"
    )
    sel = p.add_mutually_exclusive_group(required=False)
    sel.add_argument("--pages", help="comma-separated pages/ranges, e.g. 1,3-5")
    sel.add_argument("--first-page", type=int)
    p.add_argument("--last-page", type=int)
    p.add_argument(
        "--model", type=Path, default=os.environ.get("HUNYUAN_MODEL") or None
    )
    p.add_argument(
        "--mmproj", type=Path, default=os.environ.get("HUNYUAN_MMPROJ") or None
    )
    p.add_argument(
        "--llama-server",
        type=Path,
        default=os.environ.get("HUNYUAN_LLAMA_SERVER") or None,
    )
    p.add_argument(
        "--endpoint",
        default=os.environ.get("HUNYUAN_ENDPOINT"),
        help="use an already-running server instead of starting one",
    )
    p.add_argument("--endpoint-model", help="exact model ID advertised by --endpoint")
    p.add_argument(
        "--endpoint-revision",
        help="caller-declared deployment revision; change it after endpoint updates",
    )
    p.add_argument("--port", type=int, default=8095)
    p.add_argument("--ctx-size", type=int, default=16384)
    p.add_argument(
        "--server-mode",
        choices=("shared", "dedicated"),
        default=os.environ.get("OCR_HUNYUAN_MODE", "shared"),
        help="shared cross-session backend (default), or one dedicated FIFO batch",
    )
    p.add_argument(
        "--server-slots",
        type=int,
        default=os.environ.get("OCR_HUNYUAN_SLOTS", "4"),
        help="total simultaneous requests; ctx-size is per slot (default 4)",
    )
    p.add_argument("--max-tokens", type=int, default=8000)
    p.add_argument("--timeout", type=float, default=1200.0)
    p.add_argument("--report", type=Path)
    add_queue_arguments(p)
    return p


def main(argv: Sequence[str] | None = None, server: Any | None = None) -> int:
    """SERVER, for offline tests: an object with ocr(path, max_tokens, timeout)
    and close(), used instead of starting llama-server or SharedHunyuanClient."""
    arguments = build_parser().parse_args(argv)
    exit_on_sigterm()
    started_at, started = hy.utc_now(), time.monotonic()
    try:
        renders = arguments.render_directory.expanduser().resolve()
        drafts_a = arguments.engine_a_directory.expanduser().resolve()
        output = arguments.output_directory.expanduser().resolve()
        for label, path in (
            ("render", renders),
            ("engine A draft", drafts_a),
            ("engine B draft", output),
        ):
            if not path.is_dir():
                raise hy.ConfigurationError(f"{label} directory does not exist: {path}")
        pages, selection_mode = hy.selected_pages(arguments, renders)
        report_path = (
            arguments.report.expanduser().resolve()
            if arguments.report
            else output.parent / "hunyuanocr-second-look.json"
        )
        if hy.path_is_within(report_path, output):
            raise hy.ConfigurationError("run report must be outside the OCR output directory")
        if hy.path_is_within(report_path, renders):
            raise hy.ConfigurationError("run report must be outside the render directory")
        if (
            not 1 <= arguments.port <= 65535
            or arguments.ctx_size <= 0
            or arguments.max_tokens <= 0
            or not math.isfinite(arguments.timeout)
            or arguments.timeout <= 0
        ):
            raise hy.ConfigurationError(
                "port, ctx-size, max-tokens and timeout must be positive and valid"
            )
        if arguments.server_mode not in ("shared", "dedicated") or not 1 <= arguments.server_slots <= 32:
            raise hy.ConfigurationError(
                "server-mode must be shared/dedicated; server-slots must be 1..32"
            )
        for name in ("model", "mmproj", "llama_server"):
            value = getattr(arguments, name)
            if value is not None:
                setattr(arguments, name, value.expanduser().resolve())
        if server is None:
            if arguments.endpoint:
                if not arguments.endpoint.startswith(("http://", "https://")):
                    raise hy.ConfigurationError("--endpoint must be an HTTP(S) server base URL")
            else:
                if (
                    arguments.model is None
                    or arguments.mmproj is None
                    or arguments.llama_server is None
                ):
                    raise hy.ConfigurationError(
                        "need --model, --mmproj and --llama-server (or HUNYUAN_* environment variables), "
                        "or --endpoint for an already-running server"
                    )
        tool = tool_sha256(arguments.model, arguments.mmproj)
    except (hy.ConfigurationError, OSError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    skipped = blocked = 0
    recurring, total_pages = recurring_blocks(drafts_a)
    for scan_page in pages:
        json_b, text_b = hy.output_paths(output, scan_page)
        json_a = drafts_a / f"page-{scan_page:04d}.json"
        render = hy.render_path(renders, scan_page)
        try:
            raw_a = json_a.read_bytes()
            blocks = json.loads(raw_a.decode("utf-8")).get("lines") or []
            record = json.loads(json_b.read_bytes().decode("utf-8"))
            if hy.text_from_lines(record.get("lines") or []) != text_b.read_bytes():
                raise ValueError("engine B's TXT does not match its JSON lines")
            if not render.is_file():
                raise ValueError("render file is missing")
        except (OSError, UnicodeError, ValueError) as error:
            blocked += 1
            results.append({"scanPage": scan_page, "status": "blocked", "error": str(error)[:200]})
            continue
        draft_a = hy.sha256_bytes(raw_a)
        earlier = record.get("secondLook") or {}
        if (
            earlier.get("schemaVersion") == SCHEMA_VERSION
            and earlier.get("toolSha256") == tool
            and earlier.get("engineADraftSha256") == draft_a
        ):
            skipped += 1
            results.append({"scanPage": scan_page, "status": "skipped-current"})
            continue
        lines = own_lines(record)
        groups = unread_groups(
            blocks,
            hy.text_from_lines(lines).decode("utf-8"),
            recurring,
            total_pages=max(total_pages, len(pages)),
        )
        pending.append(
            {
                "scanPage": scan_page,
                "record": record,
                "lines": lines,
                "blocks": blocks,
                "groups": groups,
                "render": render,
                "json": json_b,
                "text": text_b,
                "draftA": draft_a,
            }
        )

    completed = failed = deferred = reread = 0
    budget = Budget()
    own_server = server is None
    lease: GPUQueue | None = None
    queue_deferred = False
    try:
        if own_server and any(page["groups"] for page in pending):
            try:
                if not budget.allows_next_page():
                    raise GPUQueueTimeout("phase deadline reached before model loading")
                if not arguments.endpoint and arguments.server_mode == "shared":
                    from _hunyuan_shared import SharedHunyuanClient

                    hy_tool = hy.build_tool_identity(
                        arguments.model, arguments.mmproj, arguments.llama_server
                    )
                    shared_deployment = {
                        "mode": "shared-local-server",
                        "modelId": "hunyuanocr",
                        "ctxSize": arguments.ctx_size,
                        "modelSha256": hy_tool.model_sha256,
                        "mmprojSha256": hy_tool.mmproj_sha256,
                        "binarySha256": hy_tool.binary_sha256,
                        "serverSlots": arguments.server_slots,
                        "contextPerSlot": arguments.ctx_size,
                        "totalContext": arguments.ctx_size * arguments.server_slots,
                    }
                    shared_profile = dict(
                        hy.OCR_PROFILE,
                        maxTokens=arguments.max_tokens,
                        deployment=shared_deployment,
                    )
                    server = SharedHunyuanClient(
                        config={
                            "binary": str(arguments.llama_server),
                            "model": str(arguments.model),
                            "mmproj": str(arguments.mmproj),
                            "model_id": "hunyuanocr",
                            "ctx": arguments.ctx_size,
                            "slots": arguments.server_slots,
                            "max_tokens": arguments.max_tokens,
                            "identity": hy_tool.combined_sha256
                            + hy.profile_sha256(shared_profile),
                        },
                        queue_dir=arguments.gpu_queue_dir,
                        resource=arguments.gpu_resource,
                        queue_timeout=arguments.gpu_queue_timeout,
                        phase_deadline=budget.deadline,
                    )
                elif not arguments.endpoint:
                    lease = GPUQueue(arguments.gpu_queue_dir, arguments.gpu_resource)
                    lease.acquire(
                        timeout=arguments.gpu_queue_timeout, phase_deadline=budget.deadline
                    )
                    server = hy.HunyuanServer(
                        None,
                        arguments.llama_server,
                        arguments.model,
                        arguments.mmproj,
                        arguments.port,
                        arguments.ctx_size * arguments.server_slots,
                        "hunyuanocr",
                        parallel=arguments.server_slots,
                        lease_fds=lease.pass_fds,
                    )
                else:
                    server = hy.HunyuanServer(
                        arguments.endpoint,
                        arguments.llama_server,
                        arguments.model,
                        arguments.mmproj,
                        arguments.port,
                        arguments.ctx_size,
                        arguments.endpoint_model,
                    )
            except GPUQueueTimeout as error:
                queue_deferred = True
                deferred += len(pending)
                results.extend(
                    {"scanPage": page["scanPage"], "status": "deferred", "reason": str(error)}
                    for page in pending
                )
                print(f"[gpu-queue] {error}; {len(pending)} page(s) deferred", flush=True)
                pending = []
            except (hy.ConfigurationError, GPUQueueError, RuntimeError, OSError) as error:
                print(f"ERROR {error}", file=sys.stderr)
                for page in pending:
                    failed += 1
                    results.append(
                        {"scanPage": page["scanPage"], "status": "failed", "error": str(error)}
                    )
                pending = []

        for index, page in enumerate(pending):
            if page["groups"] and not budget.allows_next_page():
                for later in pending[index:]:
                    deferred += 1
                    results.append({"scanPage": later["scanPage"], "status": "deferred"})
                print(
                    f"  time budget reached: {deferred} page(s) left for the next call",
                    flush=True,
                )
                break
            t0 = time.monotonic()
            lines = list(page["lines"])
            done: list[dict[str, Any]] = []
            try:
                placed: list[tuple[int, int, list[dict[str, Any]]]] = []
                for order, group in enumerate(page["groups"]):
                    page_limit, _ = budget.page_timeout(arguments.timeout)
                    with tempfile.TemporaryDirectory() as staging:
                        crop = crop_png(page["render"], group["box"], Path(staging))
                        entry: dict[str, Any] = {
                            "blocks": group["blocks"],
                            "box": group["box"],
                        }
                        try:
                            raw_response = server.ocr(crop, arguments.max_tokens, page_limit)
                            text = extract_ocr_text(raw_response)
                        except (hy.PageOCRFailure, RuntimeError, OSError) as error:
                            left = budget.remaining()
                            if left is not None and left <= 1.0:
                                raise hy.PageOCRFailure(str(error)) from error
                            entry["error"] = str(error)[:200]
                            done.append(entry)
                            continue
                    kept, why = kept_lines(
                        text,
                        group["text"],
                        hy.text_from_lines(page["lines"]).decode("utf-8"),
                    )
                    entry["chars"] = len(text)
                    if not kept:
                        entry["notKept"] = why
                        done.append(entry)
                        continue
                    new = [
                        {
                            "text": line,
                            "confidence": 0.0,
                            "boundingBox": group["box"],
                            "blockLabel": SECOND_LOOK_LABEL,
                        }
                        for line in kept
                    ]
                    entry["lines"] = len(new)
                    done.append(entry)
                    placed.append(
                        (
                            insertion_line(page["lines"], page["blocks"], group["blocks"][0]),
                            order,
                            new,
                        )
                    )
                    reread += 1
                for place, _, new in sorted(
                    placed, key=lambda item: (item[0], item[1]), reverse=True
                ):
                    lines[place:place] = new
                second_look = {
                    "schemaVersion": SCHEMA_VERSION,
                    "toolSha256": tool,
                    "engineADraftSha256": page["draftA"],
                    "unreadShare": UNREAD_SHARE,
                    "groups": done,
                }
                json_bytes, text_bytes = reseal(page["record"], lines, second_look)
                write_pair(page["json"], page["text"], json_bytes, text_bytes)
                completed += 1
                if page["groups"]:
                    budget.record(time.monotonic() - t0)
                budget.sealed(page["scanPage"])
                seconds = round(time.monotonic() - t0, 2)
                results.append(
                    {
                        "scanPage": page["scanPage"],
                        "status": "completed",
                        "groups": len(done),
                        "blocks": sum(len(g["blocks"]) for g in done),
                        "errors": sum(1 for g in done if g.get("error")),
                        "notKept": sum(1 for g in done if g.get("notKept")),
                        "seconds": seconds,
                    }
                )
                if done:
                    print(
                        f"  page {page['scanPage']:04d}: {sum(len(g['blocks']) for g in done)} block(s) in "
                        f"{len(done)} crop(s), {seconds}s",
                        flush=True,
                    )
            except hy.PageOCRFailure:
                for later in pending[index:]:
                    deferred += 1
                    results.append({"scanPage": later["scanPage"], "status": "deferred"})
                print(
                    f"  time budget reached during page {page['scanPage']:04d}: "
                    f"{deferred} page(s) left for the next call",
                    flush=True,
                )
                break
    finally:
        try:
            if own_server and server is not None:
                server.close()
        finally:
            if lease is not None:
                lease.close()

    results.sort(key=lambda r: r["scanPage"])
    gpu_report = (
        lease.report
        if lease is not None
        else server.report
        if own_server and server is not None and hasattr(server, "report")
        else {
            "mode": "external-owner" if arguments.endpoint else "not-needed",
            "acquired": False,
        }
    )
    report = {
        "reportSchemaVersion": SCHEMA_VERSION,
        "startedAt": started_at,
        "finishedAt": hy.utc_now(),
        "durationSeconds": round(time.monotonic() - started, 2),
        "selectionMode": selection_mode,
        "gpuQueue": gpu_report,
        "renderDirectory": str(renders),
        "engineADirectory": str(drafts_a),
        "outputDirectory": str(output),
        "toolSha256": tool,
        "unreadShare": UNREAD_SHARE,
        "counts": {
            "requested": len(pages),
            "completed": completed,
            "skippedCurrent": skipped,
            "blocked": blocked,
            "failed": failed,
            "deferred": deferred,
            "cropsRead": reread,
            "pagesWithUnreadBlocks": sum(1 for r in results if r.get("groups")),
        },
        "pages": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(hy.canonical_json_bytes(report, pretty=True))
    print(
        f"  requested {len(pages)} | completed {completed} | skipped {skipped} | blocked {blocked} | "
        f"failed {failed} | deferred {deferred} | crops read {reread}",
        flush=True,
    )
    print(f"  report: {report_path}", flush=True)
    if deferred and queue_deferred and not failed and not blocked:
        return EXIT_INCOMPLETE
    if deferred and completed:
        return EXIT_INCOMPLETE
    if deferred:
        print(
            "ERROR the time budget ran out before a single page was done. "
            "Raise the budget together with the tool call's time limit.",
            file=sys.stderr,
        )
        return 1
    return 1 if failed or blocked else 0


if __name__ == "__main__":
    sys.exit(main())
