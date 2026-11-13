#!/usr/bin/env python3
"""Run PaddleOCR-VL to produce provenance-sealed OCR drafts on Linux/NVIDIA.

This is the non-macOS counterpart to ``run_apple_vision_ocr.py``.  It emits the
same per-page JSON/TXT pair, the same sealed provenance and the same resume
semantics, so the proofreading workflow downstream is unchanged.

The generated OCR is deliberately marked as a draft.  It is provenance-rich
input for visual proofreading, never an authority for the source text.

Unlike the Apple Vision runner there is no bounded pool: PaddleOCR-VL is a GPU
model, and on a single card extra workers contend for VRAM with each other and
with any local LLM server.  One persistent worker loads the pipeline once and
processes pages in sequence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import select
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
REPORT_SCHEMA_VERSION = 1
ENGINE = "PaddleOCR-VL"
TEXT_AUTHORITY = "draft-only-not-text-authority"

# Any change here invalidates existing completion records, exactly like the
# Apple Vision profile hash does.  Keep it in sync with the worker's settings.
OCR_PROFILE: dict[str, Any] = {
    "engine": ENGINE,
    "pipelineVersion": "v1.6",
    "recognitionLanguages": ["zh-Hant", "zh-Hans", "en"],
    "recognitionLevel": "vlm-end-to-end",
    "usesLanguageCorrection": False,
    "readingOrder": "layout-model-block-order",
    "boundingBoxConvention": "normalised-xywh-top-left-origin",
    "confidenceMeaning": "layout-detector-block-score-not-glyph-confidence",
}
PAGE_TOKEN = re.compile(r"(?P<first>[1-9][0-9]*)(?:-(?P<last>[1-9][0-9]*))?\Z")

WORKER_READY_TIMEOUT_SECONDS = 600.0
PAGE_TIMEOUT_SECONDS = 900.0


class ConfigurationError(ValueError):
    """A command-line or filesystem configuration is unsafe or invalid."""


class PageOCRFailure(RuntimeError):
    """PaddleOCR rejected one page while the worker remains usable."""


class WorkerUnavailable(RuntimeError):
    """The worker exited or stopped speaking the worker protocol."""


@dataclass(frozen=True)
class ToolIdentity:
    runner_file: Path
    worker_file: Path
    runner_sha256: str
    worker_sha256: str
    combined_sha256: str
    interpreter: str


@dataclass(frozen=True)
class PageInput:
    scan_page: int
    render_file: Path
    render_sha256: str
    json_output: Path
    text_output: Path


@dataclass(frozen=True)
class ResumeCheck:
    valid: bool
    reason: str
    json_sha256: str | None = None
    text_sha256: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
        )
    else:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return text.encode("utf-8")


def profile_sha256(profile: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(profile))


def build_tool_identity(worker_file: Path, interpreter: str) -> ToolIdentity:
    runner_file = Path(__file__).resolve()
    worker_file = worker_file.resolve()
    if not worker_file.is_file():
        raise ConfigurationError(f"missing PaddleOCR worker source: {worker_file}")
    runner_bytes = runner_file.read_bytes()
    worker_bytes = worker_file.read_bytes()
    combined = hashlib.sha256()
    combined.update(b"run_paddleocr_vl.py\0")
    combined.update(runner_bytes)
    combined.update(b"\0paddleocr_vl_worker.py\0")
    combined.update(worker_bytes)
    # The interpreter selects the PaddleOCR build, so it is part of tool identity.
    combined.update(b"\0interpreter\0")
    combined.update(interpreter.encode("utf-8"))
    combined.update(Path(__file__).with_name("_phase_budget.py").read_bytes())
    combined.update(Path(__file__).with_name("_gpu_queue.py").read_bytes())
    return ToolIdentity(
        runner_file=runner_file,
        worker_file=worker_file,
        runner_sha256=sha256_bytes(runner_bytes),
        worker_sha256=sha256_bytes(worker_bytes),
        combined_sha256=combined.hexdigest(),
        interpreter=interpreter,
    )


def parse_pages_expression(expression: str) -> list[int]:
    pages: set[int] = set()
    for token in expression.split(","):
        token = token.strip()
        if not token:
            continue
        match = PAGE_TOKEN.match(token)
        if match is None:
            raise ConfigurationError(f"invalid page selector: {token!r}")
        first = int(match.group("first"))
        last = int(match.group("last") or first)
        if last < first:
            raise ConfigurationError(f"descending page range: {token!r}")
        pages.update(range(first, last + 1))
    if not pages:
        raise ConfigurationError("no pages were selected")
    return sorted(pages)


def rendered_pages(render_directory: Path) -> list[int]:
    """Every scan page that has a page-NNNN.png render; the default selection."""
    pages: list[int] = []
    for path in render_directory.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d{4})\.png", path.name)
        if match:
            pages.append(int(match.group(1)))
    return sorted(pages)


def selected_pages(arguments: argparse.Namespace, render_directory: Path) -> tuple[list[int], str]:
    if arguments.pages is not None:
        if arguments.last_page is not None:
            raise ConfigurationError("--last-page cannot be combined with --pages")
        return parse_pages_expression(arguments.pages), "explicit-pages"
    if arguments.first_page is None and arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ConfigurationError(f"no page-NNNN.png renders in {render_directory}")
        return pages, "all-renders"
    if arguments.first_page is None:
        raise ConfigurationError("--last-page needs --first-page")
    if arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ConfigurationError(f"no page-NNNN.png renders in {render_directory}")
        arguments.last_page = pages[-1]
    if arguments.first_page <= 0 or arguments.last_page <= 0:
        raise ConfigurationError("page numbers must be positive")
    if arguments.last_page < arguments.first_page:
        raise ConfigurationError("--last-page must be at least --first-page")
    return list(range(arguments.first_page, arguments.last_page + 1)), "page-range"


def render_path(render_directory: Path, scan_page: int) -> Path:
    return render_directory / f"page-{scan_page:04d}.png"


def output_paths(output_directory: Path, scan_page: int) -> tuple[Path, Path]:
    stem = f"page-{scan_page:04d}"
    return output_directory / f"{stem}.json", output_directory / f"{stem}.txt"


def default_report_path(output_directory: Path) -> Path:
    return output_directory.parent / "paddleocr-vl-run.json"


def path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        candidate.relative_to(directory)
    except ValueError:
        return False
    return True


def text_from_lines(lines: Sequence[Mapping[str, Any]]) -> bytes:
    return ("\n".join(str(line["text"]) for line in lines) + "\n").encode("utf-8")


def _validate_line_fields(record: Mapping[str, Any]) -> str | None:
    lines = record.get("lines")
    if not isinstance(lines, list):
        return "lines is not an array"
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            return f"line {index} is not an object"
        if not isinstance(line.get("text"), str):
            return f"line {index} has no text"
        confidence = line.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            return f"line {index} has invalid confidence"
        box = line.get("boundingBox")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in box
            )
        ):
            return f"line {index} has invalid boundingBox"
    # The layout regions that hold no text (paddleocr_vl_worker.normalise_page).
    # Absent in a record the worker wrote before it kept them.
    regions = record.get("regions", [])
    if not isinstance(regions, list):
        return "regions is not an array"
    for index, region in enumerate(regions):
        box = region.get("boundingBox") if isinstance(region, dict) else None
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in box
            )
            or not isinstance(region.get("blockLabel"), str)
        ):
            return f"region {index} has no label or an invalid boundingBox"
    return None


def _validate_record_fields(
    record: Mapping[str, Any], page: PageInput, profile: Mapping[str, Any]
) -> str | None:
    expected = {
        "scanPage": page.scan_page,
        "renderFile": str(page.render_file),
        "engine": profile["engine"],
        "pipelineVersion": profile["pipelineVersion"],
        "recognitionLanguages": profile["recognitionLanguages"],
        "recognitionLevel": profile["recognitionLevel"],
        "usesLanguageCorrection": profile["usesLanguageCorrection"],
    }
    for key, expected_value in expected.items():
        if record.get(key) != expected_value:
            return f"{key} does not match the current input/profile"
    if not isinstance(record.get("generatedAt"), str) or not record["generatedAt"]:
        return "generatedAt is missing"
    if not isinstance(record.get("rawResult"), dict):
        return "rawResult is missing"
    return _validate_line_fields(record)


def build_completion_record(
    raw_record: Mapping[str, Any],
    page: PageInput,
    text_bytes: bytes,
    tool: ToolIdentity,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    record = dict(raw_record)
    record.update(
        {
            "schemaVersion": SCHEMA_VERSION,
            "status": "complete",
            "textAuthority": TEXT_AUTHORITY,
            "provenance": {
                "renderSha256": page.render_sha256,
                "toolSha256": tool.combined_sha256,
                "ocrProfileSha256": profile_sha256(profile),
                "textSha256": sha256_bytes(text_bytes),
            },
        }
    )
    digest = sha256_bytes(canonical_json_bytes(record, pretty=True))
    record["provenance"]["jsonPayloadSha256"] = digest
    return record


def validate_completion(
    page: PageInput, tool: ToolIdentity, profile: Mapping[str, Any]
) -> ResumeCheck:
    json_exists = page.json_output.is_file()
    text_exists = page.text_output.is_file()
    if not json_exists and not text_exists:
        return ResumeCheck(False, "no existing output")
    if json_exists != text_exists:
        return ResumeCheck(False, "only one member of the JSON/TXT pair exists")
    try:
        json_bytes = page.json_output.read_bytes()
        text_bytes = page.text_output.read_bytes()
        record = json.loads(json_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return ResumeCheck(False, f"existing output cannot be read: {error}")
    if not isinstance(record, dict):
        return ResumeCheck(False, "completion JSON is not an object")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return ResumeCheck(False, "completion JSON uses a legacy/unknown schema")
    if json_bytes != canonical_json_bytes(record, pretty=True):
        return ResumeCheck(False, "completion JSON is not canonical or was edited")
    if record.get("status") != "complete":
        return ResumeCheck(False, "completion status is not complete")
    if record.get("textAuthority") != TEXT_AUTHORITY:
        return ResumeCheck(False, "text authority marker does not match")
    field_error = _validate_record_fields(record, page, profile)
    if field_error:
        return ResumeCheck(False, field_error)
    if text_from_lines(record["lines"]) != text_bytes:
        return ResumeCheck(False, "TXT bytes do not match JSON lines")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return ResumeCheck(False, "provenance is missing")
    expected_provenance = {
        "renderSha256": page.render_sha256,
        "toolSha256": tool.combined_sha256,
        "ocrProfileSha256": profile_sha256(profile),
        "textSha256": sha256_bytes(text_bytes),
    }
    for key, expected_value in expected_provenance.items():
        if provenance.get(key) != expected_value:
            return ResumeCheck(False, f"provenance {key} does not match")
    sealed_digest = provenance.get("jsonPayloadSha256")
    if not isinstance(sealed_digest, str):
        return ResumeCheck(False, "JSON payload seal is missing")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed, pretty=True)) != sealed_digest:
        return ResumeCheck(False, "JSON payload seal does not match")
    return ResumeCheck(
        True,
        "current completion record",
        json_sha256=sha256_bytes(json_bytes),
        text_sha256=sha256_bytes(text_bytes),
    )


def atomic_promote(
    staged_text: Path, staged_json: Path, final_text: Path, final_json: Path
) -> None:
    # JSON is the completion record, so it must become visible last.
    os.replace(staged_text, final_text)
    os.replace(staged_json, final_json)


class BudgetExpired(Exception):
    """The phase's time budget ran out while a page was being read."""


class PaddleWorker:
    """One persistent PaddleOCR-VL process speaking the JSONL page protocol."""

    def __init__(self, interpreter: str, worker_file: Path, pipeline_version: str = "v1.6",
                 lease_fds: tuple[int, ...] = ()):
        worker_env = dict(os.environ, JYUT_PADDLE_PIPELINE_VERSION=pipeline_version)
        # The worker holds the model on the GPU; it must not outlive this runner.
        self.process_handle = subprocess.Popen(
            [interpreter, str(worker_file)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            preexec_fn=die_with_parent(),
            pass_fds=lease_fds,
            env=worker_env,
        )
        self.page_in_flight = False
        self.response_buffer = b""
        try:
            ready = self._read_response(WORKER_READY_TIMEOUT_SECONDS)
            if not ready.get("ok") or not ready.get("ready"):
                raise WorkerUnavailable(str(ready.get("error") or "worker did not report ready"))
        except BaseException:
            self.close()
            raise

    def _read_response(self, timeout_seconds: float, budget_capped: bool = False) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        stdout = self.process_handle.stdout
        assert stdout is not None
        while True:
            if b"\n" in self.response_buffer:
                line, self.response_buffer = self.response_buffer.split(b"\n", 1)
                if not line.strip():
                    continue
            else:
                left = deadline - time.monotonic()
                if left <= 0:
                    self.close()
                    if budget_capped:
                        raise BudgetExpired()
                    raise WorkerUnavailable(f"worker timed out after {timeout_seconds:.0f}s")
                if not select.select([stdout], [], [], min(left, 1.0))[0]:
                    continue
                chunk = os.read(stdout.fileno(), 65536)
                if not chunk:
                    raise WorkerUnavailable("worker closed its output stream")
                self.response_buffer += chunk
                if len(self.response_buffer) > 64 * 1024 * 1024:
                    raise WorkerUnavailable("worker response exceeds 64 MiB")
                continue
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, UnicodeError) as error:
                raise WorkerUnavailable(f"worker emitted non-JSON: {error}") from error
            if not isinstance(payload, dict):
                raise WorkerUnavailable("worker response is not an object")
            return payload

    def ocr_page(self, render_file: Path, timeout_seconds: float = PAGE_TIMEOUT_SECONDS,
                 budget_capped: bool = False) -> dict[str, Any]:
        stdin = self.process_handle.stdin
        if stdin is None or self.process_handle.poll() is not None:
            raise WorkerUnavailable("worker is not accepting requests")
        try:
            stdin.write(json.dumps({"renderFile": str(render_file)}) + "\n")
            stdin.flush()
        except (OSError, ValueError) as error:
            raise WorkerUnavailable(f"worker input unavailable: {error}") from error
        self.page_in_flight = True
        try:
            response = self._read_response(timeout_seconds, budget_capped)
        finally:
            self.page_in_flight = False
        if not response.get("ok"):
            raise PageOCRFailure(str(response.get("error") or "PaddleOCR failed"))
        return response

    def close(self) -> None:
        handle = self.process_handle
        if handle.poll() is None:
            if self.page_in_flight:
                # Stopped mid-page (budget, signal): the page is lost anyway, and
                # waiting for it would retain GPU memory unnecessarily.
                handle.kill()
                handle.wait(timeout=30)
                return
            try:
                if handle.stdin is not None:
                    handle.stdin.close()
                handle.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                handle.kill()
                handle.wait(timeout=30)


def resolve_interpreter(explicit: str | None) -> str:
    """Pick the Python that has PaddleOCR installed.

    PaddleOCR usually lives in a dedicated venv, not the interpreter running this
    script, so the caller can point at it explicitly or via PADDLEOCR_PYTHON.
    """
    candidate = explicit or os.environ.get("PADDLEOCR_PYTHON")
    if candidate:
        resolved = shutil.which(candidate) or candidate
        if not Path(resolved).is_file():
            raise ConfigurationError(f"interpreter does not exist: {candidate}")
        # Deliberately abspath, never resolve(): a venv's bin/python is a symlink
        # to the base interpreter, and following it discards the venv entirely.
        return os.path.abspath(resolved)
    return sys.executable


def verify_paddleocr_available(interpreter: str) -> dict[str, Any]:
    probe = (
        "import json, importlib.metadata as m, paddleocr\n"
        "def v(name):\n"
        "    try: return m.version(name)\n"
        "    except Exception: return None\n"
        "print(json.dumps({'paddleocr': getattr(paddleocr, '__version__', None),\n"
        "                  'paddlepaddle-gpu': v('paddlepaddle-gpu'),\n"
        "                  'paddlex': v('paddlex')}))\n"
    )
    result = subprocess.run(
        [interpreter, "-c", probe], text=True, capture_output=True, check=False, timeout=60
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ConfigurationError(
            "PaddleOCR is not importable with "
            f"{interpreter}: {detail[-1] if detail else 'unknown error'}"
        )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provenance-sealed PaddleOCR-VL drafts (Linux/NVIDIA)."
    )
    parser.add_argument("render_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    selector = parser.add_mutually_exclusive_group(required=False)
    selector.add_argument(
        "--pages", help="comma-separated pages/ranges, for example 1,3-5,9"
    )
    selector.add_argument("--first-page", type=int)
    parser.add_argument("--last-page", type=int)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace legacy, partial, stale, or otherwise mismatched output pairs",
    )
    parser.add_argument(
        "--report", type=Path, help="run report path; must be outside OUTPUT_DIRECTORY"
    )
    parser.add_argument(
        "--python",
        dest="interpreter",
        help="interpreter that has PaddleOCR installed (or set PADDLEOCR_PYTHON)",
    )
    parser.add_argument("--pipeline-version", default="v1.6")
    parser.add_argument("--model-revision", default=os.environ.get("PADDLEOCR_MODEL_REVISION", "unversioned-cache"),
                        help="caller-declared cache revision; change after replacing cached model files")
    parser.add_argument("--timeout", type=float, default=PAGE_TIMEOUT_SECONDS)
    parser.add_argument(
        "--worker-source",
        type=Path,
        default=Path(__file__).with_name("paddleocr_vl_worker.py"),
        help=argparse.SUPPRESS,
    )
    add_queue_arguments(parser)
    return parser


from _gpu_queue import GPUQueue, GPUQueueError, GPUQueueTimeout, add_queue_arguments

from _phase_budget import (  # noqa: E402  (scripts/ is sys.path[0])
    EXIT_INCOMPLETE, Budget, die_with_parent, exit_on_sigterm)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    exit_on_sigterm()
    started_wall = time.monotonic()
    started_at = utc_now()

    try:
        render_directory = arguments.render_directory.expanduser().resolve()
        pages, selection_mode = selected_pages(arguments, render_directory)
        output_directory = arguments.output_directory.expanduser().resolve()
        if not render_directory.is_dir():
            raise ConfigurationError(
                f"render directory does not exist: {render_directory}"
            )
        output_directory.mkdir(parents=True, exist_ok=True)
        report_path = (
            arguments.report.expanduser().resolve()
            if arguments.report is not None
            else default_report_path(output_directory).resolve()
        )
        if path_is_within(report_path, output_directory):
            raise ConfigurationError(
                "run report must be outside the OCR output directory"
            )
        if path_is_within(report_path, render_directory):
            raise ConfigurationError("run report must be outside the render directory")
        if report_path in [Path(__file__).resolve(), arguments.worker_source.resolve()]:
            raise ConfigurationError("run report must not overwrite a tool")
        if arguments.timeout <= 0 or not arguments.pipeline_version or not arguments.model_revision:
            raise ConfigurationError("timeout, pipeline-version and model-revision must be valid")
        interpreter = resolve_interpreter(arguments.interpreter)
        versions = verify_paddleocr_available(interpreter)
        tool = build_tool_identity(arguments.worker_source, interpreter)
        current_profile = dict(OCR_PROFILE, pipelineVersion=arguments.pipeline_version,
                               packageVersions=versions, modelRevision=arguments.model_revision,
                               modelIdentityAssurance="package-versions-and-caller-declared-cache-revision-not-weight-attestation")
    except (ConfigurationError, OSError, subprocess.TimeoutExpired) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    page_results: list[dict[str, Any]] = []
    pending: list[PageInput] = []
    skipped_pages: list[int] = []
    blocked_pages: list[int] = []
    failed_pages: list[int] = []

    for scan_page in pages:
        render_file = render_path(render_directory, scan_page).resolve()
        json_output, text_output = output_paths(output_directory, scan_page)
        if not render_file.is_file():
            page_results.append(
                {
                    "scanPage": scan_page,
                    "status": "failed",
                    "renderFile": str(render_file),
                    "error": "render file is missing",
                }
            )
            failed_pages.append(scan_page)
            continue
        page = PageInput(
            scan_page=scan_page,
            render_file=render_file,
            render_sha256=sha256_file(render_file),
            json_output=json_output,
            text_output=text_output,
        )
        resume = validate_completion(page, tool, current_profile)
        # ``Path.exists`` follows symlinks, so a dangling output link would look
        # absent and could otherwise be replaced without the explicit overwrite
        # gate.
        output_exists = os.path.lexists(json_output) or os.path.lexists(text_output)
        if resume.valid and not arguments.overwrite:
            skipped_pages.append(scan_page)
            page_results.append(
                {
                    "scanPage": scan_page,
                    "status": "skipped-current",
                    "renderFile": str(render_file),
                    "renderSha256": page.render_sha256,
                    "jsonSha256": resume.json_sha256,
                    "textSha256": resume.text_sha256,
                }
            )
            continue
        if output_exists and not arguments.overwrite:
            blocked_pages.append(scan_page)
            page_results.append(
                {
                    "scanPage": scan_page,
                    "status": "blocked",
                    "renderFile": str(render_file),
                    "error": f"existing output rejected: {resume.reason}; "
                    "re-run with --overwrite to replace it",
                }
            )
            continue
        pending.append(page)

    worker: PaddleWorker | None = None
    lease: GPUQueue | None = None
    queue_deferred = False
    completed_pages: list[int] = []
    deferred_pages: list[int] = []
    budget = Budget()

    try:
        if pending:
            try:
                if not budget.allows_next_page():
                    raise GPUQueueTimeout("phase deadline reached before model loading")
                lease = GPUQueue(arguments.gpu_queue_dir, arguments.gpu_resource)
                lease.acquire(timeout=arguments.gpu_queue_timeout, phase_deadline=budget.deadline)
                worker = PaddleWorker(interpreter, tool.worker_file, arguments.pipeline_version,
                                      lease_fds=lease.pass_fds)
            except GPUQueueTimeout as error:
                queue_deferred = True
                deferred_pages.extend(page.scan_page for page in pending)
                page_results.extend({"scanPage": page.scan_page, "status": "deferred",
                                     "renderFile": str(page.render_file), "reason": str(error)}
                                    for page in pending)
                print(f"[gpu-queue] {error}; {len(pending)} page(s) deferred", flush=True)
                pending = []
            except (WorkerUnavailable, GPUQueueError, OSError) as error:
                print(f"ERROR worker unavailable: {error}", file=sys.stderr)
                for page in pending:
                    failed_pages.append(page.scan_page)
                    page_results.append(
                        {
                            "scanPage": page.scan_page,
                            "status": "failed",
                            "renderFile": str(page.render_file),
                            "error": f"worker unavailable: {error}",
                        }
                    )
                pending = []

        for index, page in enumerate(pending):
            assert worker is not None
            if not budget.allows_next_page():
                # Out of time for this call: the pages left are neither failed
                # nor blocked, just not done yet; the next call resumes them.
                for later in pending[index:]:
                    deferred_pages.append(later.scan_page)
                    page_results.append(
                        {
                            "scanPage": later.scan_page,
                            "status": "deferred",
                            "renderFile": str(later.render_file),
                        }
                    )
                print(
                    f"  time budget reached: {len(deferred_pages)} page(s) left "
                    "for the next call",
                    flush=True,
                )
                break
            page_started = time.monotonic()
            page_limit, capped = budget.page_timeout(arguments.timeout)
            try:
                response = worker.ocr_page(page.render_file, page_limit, capped)
                lines = response.get("lines")
                raw_record = {
                    "scanPage": page.scan_page,
                    "renderFile": str(page.render_file),
                    "generatedAt": utc_now(),
                    "engine": current_profile["engine"],
                    "pipelineVersion": current_profile["pipelineVersion"],
                    "recognitionLanguages": current_profile["recognitionLanguages"],
                    "recognitionLevel": current_profile["recognitionLevel"],
                    "usesLanguageCorrection": current_profile["usesLanguageCorrection"],
                    "renderWidth": response.get("width"),
                    "renderHeight": response.get("height"),
                    "lines": lines,
                    "rawResult": response.get("rawResult"),
                    # Layout regions that hold no text (a chart, an image):
                    # apart from the lines, so the text is the lines' alone.
                    "regions": response.get("regions") or [],
                }
                field_error = _validate_record_fields(
                    raw_record, page, current_profile
                )
                if field_error:
                    raise PageOCRFailure(f"invalid worker output: {field_error}")
                text_bytes = text_from_lines(lines)
                record = build_completion_record(
                    raw_record, page, text_bytes, tool, current_profile
                )
                json_bytes = canonical_json_bytes(record, pretty=True)

                with tempfile.TemporaryDirectory(
                    dir=str(page.json_output.parent)
                ) as staging:
                    staged_json = Path(staging) / "page.json"
                    staged_text = Path(staging) / "page.txt"
                    staged_json.write_bytes(json_bytes)
                    staged_text.write_bytes(text_bytes)
                    atomic_promote(
                        staged_text, staged_json, page.text_output, page.json_output
                    )

                completed_pages.append(page.scan_page)
                budget.record(time.monotonic() - page_started)
                budget.sealed(page.scan_page)
                page_results.append(
                    {
                        "scanPage": page.scan_page,
                        "status": "completed",
                        "renderFile": str(page.render_file),
                        "renderSha256": page.render_sha256,
                        "jsonSha256": sha256_bytes(json_bytes),
                        "textSha256": sha256_bytes(text_bytes),
                        "lineCount": len(lines),
                        "seconds": round(time.monotonic() - page_started, 2),
                    }
                )
                print(
                    f"  page {page.scan_page:04d}: {len(lines)} blocks in "
                    f"{time.monotonic() - page_started:.1f}s",
                    flush=True,
                )
            except BudgetExpired:
                # The budget ran out mid-page; the worker is gone with the page.
                for later in pending[index:]:
                    deferred_pages.append(later.scan_page)
                    page_results.append(
                        {
                            "scanPage": later.scan_page,
                            "status": "deferred",
                            "renderFile": str(later.render_file),
                        }
                    )
                print(
                    f"  time budget reached during page {page.scan_page:04d}: "
                    f"{len(deferred_pages)} page(s) left for the next call",
                    flush=True,
                )
                break
            except (PageOCRFailure, TypeError, ValueError) as error:
                failed_pages.append(page.scan_page)
                page_results.append(
                    {
                        "scanPage": page.scan_page,
                        "status": "failed",
                        "renderFile": str(page.render_file),
                        "error": str(error),
                    }
                )
                print(f"  page {page.scan_page:04d}: FAILED {error}", file=sys.stderr)
            except (WorkerUnavailable, OSError) as error:
                # The pipeline is gone; every remaining page fails with it.
                remaining = pending[pending.index(page) :]
                for lost in remaining:
                    failed_pages.append(lost.scan_page)
                    page_results.append(
                        {
                            "scanPage": lost.scan_page,
                            "status": "failed",
                            "renderFile": str(lost.render_file),
                            "error": f"worker unavailable: {error}",
                        }
                    )
                print(f"ERROR worker unavailable: {error}", file=sys.stderr)
                break
    finally:
        try:
            if worker is not None:
                worker.close()
        finally:
            if lease is not None:
                lease.close()

    page_results.sort(key=lambda entry: entry["scanPage"])
    report = {
        "reportSchemaVersion": REPORT_SCHEMA_VERSION,
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "durationSeconds": round(time.monotonic() - started_wall, 2),
        "selectionMode": selection_mode,
        "gpuQueue": lease.report if lease is not None else {"mode": "not-needed", "acquired": False},
        "renderDirectory": str(render_directory),
        "outputDirectory": str(output_directory),
        "textAuthority": TEXT_AUTHORITY,
        "tool": {
            "runner": str(tool.runner_file),
            "runnerSha256": tool.runner_sha256,
            "worker": str(tool.worker_file),
            "workerSha256": tool.worker_sha256,
            "toolSha256": tool.combined_sha256,
            "interpreter": tool.interpreter,
            "versions": versions,
        },
        "ocrProfile": current_profile,
        "ocrProfileSha256": profile_sha256(current_profile),
        "counts": {
            "requested": len(pages),
            "completed": len(completed_pages),
            "skippedCurrent": len(skipped_pages),
            "blocked": len(blocked_pages),
            "failed": len(failed_pages),
            "deferred": len(deferred_pages),
        },
        "pages": page_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report, pretty=True))

    print(
        f"  requested {len(pages)} | completed {len(completed_pages)} | "
        f"skipped {len(skipped_pages)} | blocked {len(blocked_pages)} | "
        f"failed {len(failed_pages)} | deferred {len(deferred_pages)}",
        flush=True,
    )
    print(f"  report: {report_path}", flush=True)

    # Pages left over after progress: call again (failed pages are retried then
    # too).  Pages left over with no progress: calling again would do exactly
    # the same, so stop the loop with an error the agent has to read.
    if deferred_pages and queue_deferred and not failed_pages and not blocked_pages:
        return EXIT_INCOMPLETE
    if deferred_pages and completed_pages:
        return EXIT_INCOMPLETE
    if deferred_pages:
        print("ERROR the time budget ran out before a single page was sealed "
              "(model loading used it up, or this page is stuck). Keep the budget "
              "below the tool call's time limit; raise both together. If one page "
              "never finishes, it is stuck: run it alone to see why.",
              file=sys.stderr)
        return 1
    if failed_pages or blocked_pages:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
