#!/usr/bin/env python3
"""Run Apple Vision OCR with a bounded pool of persistent local workers.

The generated OCR is deliberately marked as a draft.  It is provenance-rich
input for visual proofreading, never an authority for the source text.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import signal
import threading
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
import uuid


_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from jyut_ocr.core import common as _common  # noqa: E402
from jyut_ocr.core.common import sha256_bytes, sha256_file  # noqa: E402
from _ocr_runner_common import (  # noqa: E402
    PAGE_TOKEN,
    ConfigurationError,
    PageInput,
    PageOCRFailure,
    output_paths,
    path_is_within as _path_is_within,
    profile_sha256 as _profile_sha256,
    render_path,
    text_from_lines,
)
from _phase_budget import Budget, EXIT_INCOMPLETE, record_child
from _mlx_vlm_server import SupervisedProcess
from _vision_layout import adapt_observations
from jyut_ocr.core.draft_contract import layout_problem

_STOP = threading.Event()

SCHEMA_VERSION = 3
REPORT_SCHEMA_VERSION = 1
MAX_WORKERS = 8
ENGINE = "Apple Vision VNRecognizeTextRequest"
TEXT_AUTHORITY = "draft-only-not-text-authority"
VISION_PROFILE: dict[str, Any] = {
    "engine": ENGINE,
    "recognitionLanguages": ["zh-Hans", "zh-Hant", "en-US"],
    "recognitionLevel": "accurate",
    "usesLanguageCorrection": True,
    "minimumTextHeight": 0.0025,
    "readingOrder": "top-to-bottom-then-left-to-right",
}


class BudgetExpired(RuntimeError):
    """The shared phase ended before a page could be sealed."""


class WorkerUnavailable(RuntimeError):
    """A persistent worker exited or stopped speaking the worker protocol."""


@dataclass(frozen=True)
class ToolIdentity:
    runner_file: Path
    swift_file: Path
    runner_sha256: str
    swift_sha256: str
    combined_sha256: str


@dataclass(frozen=True)
class ResumeCheck:
    valid: bool
    reason: str
    json_sha256: str | None = None
    text_sha256: str | None = None


WorkerFactory = Callable[[Path, int], Any]
Compiler = Callable[[Path, Path], tuple[Path, dict[str, Any]]]


def utc_now() -> str:
    return _common.utc_now(milliseconds=True)


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    return _common.canonical_json_bytes(value, pretty=pretty, trailing_newline=True)


def profile_sha256(profile: Mapping[str, Any]) -> str:
    return _profile_sha256(profile, trailing_newline=True)


def build_tool_identity(swift_file: Path) -> ToolIdentity:
    runner_file = Path(__file__).resolve()
    swift_file = swift_file.resolve()
    if not swift_file.is_file():
        raise ConfigurationError(f"missing Swift worker source: {swift_file}")
    runner_bytes = runner_file.read_bytes()
    swift_bytes = swift_file.read_bytes()
    combined = hashlib.sha256()
    combined.update(b"run_apple_vision_ocr.py\0")
    combined.update(runner_bytes)
    combined.update(b"\0apple_vision_ocr.swift\0")
    combined.update(swift_bytes)
    for helper in ("_vision_layout.py", "_mlx_vlm_server.py"):
        combined.update(b"\0" + helper.encode() + b"\0")
        combined.update(Path(__file__).with_name(helper).read_bytes())
    combined.update(b"\0_draft_contract.py\0")
    combined.update((Path(__file__).resolve().parent / "jyut_ocr" / "core" / "draft_contract.py").read_bytes())
    return ToolIdentity(
        runner_file=runner_file,
        swift_file=swift_file,
        runner_sha256=sha256_bytes(runner_bytes),
        swift_sha256=sha256_bytes(swift_bytes),
        combined_sha256=combined.hexdigest(),
    )


def parse_pages_expression(expression: str) -> list[int]:
    if not expression.strip():
        raise ConfigurationError("--pages must not be empty")
    pages: set[int] = set()
    for raw_token in expression.split(","):
        token = raw_token.strip()
        match = PAGE_TOKEN.fullmatch(token)
        if match is None:
            raise ConfigurationError(
                f"invalid --pages token {token!r}; use comma-separated pages/ranges"
            )
        first = int(match.group("first"))
        last_text = match.group("last")
        last = int(last_text) if last_text is not None else first
        if last < first:
            raise ConfigurationError(f"descending --pages range is not allowed: {token}")
        pages.update(range(first, last + 1))
    return sorted(pages)


def selected_pages(arguments: argparse.Namespace) -> tuple[list[int], str]:
    if arguments.pages is not None:
        if arguments.last_page is not None:
            raise ConfigurationError("--last-page cannot be combined with --pages")
        return parse_pages_expression(arguments.pages), "explicit-pages"
    if arguments.first_page is None or arguments.last_page is None:
        raise ConfigurationError(
            "the range selector requires both --first-page and --last-page"
        )
    if arguments.first_page <= 0 or arguments.last_page <= 0:
        raise ConfigurationError("page numbers must be positive")
    if arguments.last_page < arguments.first_page:
        raise ConfigurationError("--last-page must be at least --first-page")
    return list(range(arguments.first_page, arguments.last_page + 1)), "range"


def physical_core_count() -> int:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.physicalcpu"],
                check=True,
                text=True,
                capture_output=True,
            )
            value = int(result.stdout.strip())
            if value > 0:
                return value
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return max(1, os.cpu_count() or 1)


def resolve_worker_count(
    specification: str, pending_count: int, *, physical_cores: int | None = None
) -> int:
    if pending_count < 0:
        raise ConfigurationError("pending page count cannot be negative")
    if specification == "auto":
        cores = physical_core_count() if physical_cores is None else physical_cores
        if cores <= 0:
            raise ConfigurationError("physical core count must be positive")
        return min(pending_count, cores, MAX_WORKERS)
    try:
        requested = int(specification)
    except ValueError as error:
        raise ConfigurationError("--workers must be auto or an integer from 1 to 8") from error
    if not 1 <= requested <= MAX_WORKERS:
        raise ConfigurationError("--workers must be auto or an integer from 1 to 8")
    return min(pending_count, requested)


def _validate_legacy_fields(
    record: Mapping[str, Any], page: PageInput, profile: Mapping[str, Any]
) -> str | None:
    expected = {
        "scanPage": page.scan_page,
        "renderFile": str(page.render_file),
        "engine": profile["engine"],
        "recognitionLanguages": profile["recognitionLanguages"],
        "recognitionLevel": profile["recognitionLevel"],
        "usesLanguageCorrection": profile["usesLanguageCorrection"],
    }
    for key, expected_value in expected.items():
        if record.get(key) != expected_value:
            return f"{key} does not match the current input/profile"
    if not isinstance(record.get("generatedAt"), str) or not record["generatedAt"]:
        return "generatedAt is missing"
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
    return None


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
            "minimumTextHeight": profile["minimumTextHeight"],
            "textAuthority": TEXT_AUTHORITY,
            "provenance": {
                "renderSha256": page.render_sha256,
                "toolSha256": tool.combined_sha256,
                "visionProfileSha256": profile_sha256(profile),
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
    if record.get("minimumTextHeight") != profile["minimumTextHeight"]:
        return ResumeCheck(False, "minimumTextHeight does not match the profile")
    legacy_error = _validate_legacy_fields(record, page, profile)
    if legacy_error:
        return ResumeCheck(False, legacy_error)
    problem = layout_problem(record, text_bytes.decode("utf-8"))
    if problem:
        return ResumeCheck(False, problem)
    lines = record["lines"]
    if text_from_lines(lines) != text_bytes:
        return ResumeCheck(False, "TXT bytes do not match JSON lines")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return ResumeCheck(False, "provenance is missing")
    expected_provenance = {
        "renderSha256": page.render_sha256,
        "toolSha256": tool.combined_sha256,
        "visionProfileSha256": profile_sha256(profile),
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


def validate_raw_worker_output(
    raw_json: Path,
    raw_text: Path,
    page: PageInput,
    profile: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes]:
    if not raw_json.is_file() or not raw_text.is_file():
        raise PageOCRFailure("worker reported success without both staged outputs")
    try:
        record = json.loads(raw_json.read_text(encoding="utf-8"))
        text_bytes = raw_text.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PageOCRFailure(f"cannot read staged worker output: {error}") from error
    if not isinstance(record, dict):
        raise PageOCRFailure("staged JSON is not an object")
    legacy_error = _validate_legacy_fields(record, page, profile)
    if legacy_error:
        raise PageOCRFailure(f"invalid staged JSON: {legacy_error}")
    if text_from_lines(record["lines"]) != text_bytes:
        raise PageOCRFailure("staged TXT bytes do not match staged JSON lines")
    return record, text_bytes


def atomic_promote(
    staged_text: Path,
    staged_json: Path,
    final_text: Path,
    final_json: Path,
) -> None:
    # JSON is the completion record, so it must become visible last.
    os.replace(staged_text, final_text)
    os.replace(staged_json, final_json)


class SwiftPersistentWorker:
    def __init__(self, binary: Path, worker_id: int):
        self.budget = Budget()
        self.response_buffer = b""
        self.worker_id = worker_id
        self.supervisor = SupervisedProcess(
            [str(binary), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.process_handle = self.supervisor.process
        record_child("vision-worker", self.process_handle.pid)

    def process(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        handle = self.process_handle
        if handle.poll() is not None or handle.stdin is None or handle.stdout is None:
            raise WorkerUnavailable(f"worker {self.worker_id} is not running")
        try:
            handle.stdin.write(
                json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            handle.stdin.flush()
            limit, capped = self.budget.page_timeout(300.0)
            until = time.monotonic() + limit
            while b"\n" not in self.response_buffer:
                if _STOP.is_set() or time.monotonic() >= until:
                    if capped or _STOP.is_set():
                        raise BudgetExpired("phase deadline reached during Vision recognition")
                    raise WorkerUnavailable("Vision page exceeded its 300 second deadline")
                if not select.select([handle.stdout], [], [], min(0.2, max(0, until - time.monotonic())))[0]:
                    continue
                chunk = os.read(handle.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.response_buffer += chunk
                if len(self.response_buffer) > 1024 * 1024:
                    raise WorkerUnavailable("Vision worker response exceeds protocol limit")
            response, separator, self.response_buffer = self.response_buffer.partition(b"\n")
            response_line = response.decode("utf-8") if separator else ""
        except (BrokenPipeError, OSError, UnicodeError) as error:
            raise WorkerUnavailable(f"worker {self.worker_id} pipe failed: {error}") from error
        if not response_line:
            detail = ""
            if handle.stderr is not None and handle.poll() is not None:
                detail = handle.stderr.read().strip()
            raise WorkerUnavailable(
                f"worker {self.worker_id} exited before responding"
                + (f": {detail}" if detail else "")
            )
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as error:
            raise WorkerUnavailable(
                f"worker {self.worker_id} returned invalid protocol JSON"
            ) from error
        if not isinstance(response, dict) or response.get("scanPage") != job["scanPage"]:
            raise WorkerUnavailable(f"worker {self.worker_id} returned the wrong page")
        if response.get("status") != "ok":
            raise PageOCRFailure(str(response.get("error") or "Apple Vision failed"))
        return response

    def close(self) -> None:
        handle = self.process_handle
        if handle.stdin is not None:
            try:
                handle.stdin.close()
            except OSError:
                pass
        self.supervisor.close()
        for stream in (handle.stdout, handle.stderr):
            if stream is not None:
                stream.close()


def locate_swiftc() -> Path:
    direct = shutil.which("swiftc")
    if direct:
        return Path(direct).resolve()
    xcrun = shutil.which("xcrun")
    if xcrun:
        result = subprocess.run(
            [xcrun, "--find", "swiftc"], text=True, capture_output=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    raise ConfigurationError("swiftc is required to build the Apple Vision worker")


def compile_swift_worker(
    swift_source: Path, build_directory: Path
) -> tuple[Path, dict[str, Any]]:
    swiftc = locate_swiftc()
    binary = build_directory / "apple-vision-ocr-worker"
    result = subprocess.run(
        [str(swiftc), "-O", str(swift_source), "-o", str(binary)],
        text=True,
        capture_output=True,
        check=False,
        timeout=Budget().page_timeout(120.0)[0],
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown compiler error"
        raise ConfigurationError(f"Swift worker compilation failed: {detail}")
    version = subprocess.run(
        [str(swiftc), "--version"], text=True, capture_output=True, check=False
    )
    return binary, {
        "compiler": str(swiftc),
        "compilerVersion": (version.stdout or version.stderr).strip(),
        "workerBinarySha256": sha256_file(binary),
    }


def make_worker(binary: Path, worker_id: int) -> SwiftPersistentWorker:
    return SwiftPersistentWorker(binary, worker_id)


def _process_page(
    worker: Any,
    worker_id: int,
    page: PageInput,
    staging_root: Path,
    tool: ToolIdentity,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    job_directory = staging_root / (
        f"worker-{worker_id:02d}-page-{page.scan_page:04d}-{uuid.uuid4().hex}"
    )
    job_directory.mkdir()
    raw_json = job_directory / "raw.json"
    raw_text = job_directory / "raw.txt"
    job = {
        "scanPage": page.scan_page,
        "renderFile": str(page.render_file),
        "jsonOutput": str(raw_json),
        "textOutput": str(raw_text),
        "profile": dict(profile),
    }
    worker.process(job)
    raw_record, text_bytes = validate_raw_worker_output(
        raw_json, raw_text, page, profile
    )
    raw_record = adapt_observations(raw_record)
    text_bytes = text_from_lines(raw_record["lines"])
    completion = build_completion_record(raw_record, page, text_bytes, tool, profile)
    staged_text = job_directory / page.text_output.name
    staged_json = job_directory / page.json_output.name
    staged_text.write_bytes(text_bytes)
    staged_json.write_bytes(canonical_json_bytes(completion, pretty=True))
    atomic_promote(staged_text, staged_json, page.text_output, page.json_output)
    return {
        "scanPage": page.scan_page,
        "status": "completed",
        "workerId": worker_id,
        "renderFile": str(page.render_file),
        "renderSha256": page.render_sha256,
        "jsonFile": str(page.json_output),
        "jsonSha256": sha256_file(page.json_output),
        "textFile": str(page.text_output),
        "textSha256": sha256_file(page.text_output),
        "lineCount": len(raw_record["lines"]),
    }


def _process_assignment(
    worker_id: int,
    pages: Sequence[PageInput],
    binary: Path,
    staging_root: Path,
    tool: ToolIdentity,
    profile: Mapping[str, Any],
    worker_factory: WorkerFactory,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    worker: Any | None = None
    budget = Budget()

    def close_worker() -> None:
        nonlocal worker
        if worker is None:
            return
        try:
            worker.close()
        except Exception:
            # A close failure cannot invalidate pages already atomically sealed.
            pass
        worker = None

    try:
        for page in pages:
            if _STOP.is_set() or not budget.allows_next_page():
                results.append({"scanPage": page.scan_page, "status": "deferred", "reason": "phase deadline reached"})
                continue
            began = time.monotonic()
            try:
                if worker is None:
                    worker = worker_factory(binary, worker_id)
                results.append(
                    _process_page(
                        worker, worker_id, page, staging_root, tool, profile
                    )
                )
                seconds = time.monotonic() - began
                results[-1]["seconds"] = seconds
                budget.record(seconds)
                budget.sealed(page.scan_page)
            except BudgetExpired as error:
                close_worker()
                results.append({"scanPage": page.scan_page, "status": "deferred", "reason": str(error)})
            except WorkerUnavailable as error:
                close_worker()
                results.append(
                    {
                        "scanPage": page.scan_page,
                        "status": "failed",
                        "workerId": worker_id,
                        "renderFile": str(page.render_file),
                        "renderSha256": page.render_sha256,
                        "error": str(error),
                    }
                )
            except Exception as error:  # Per-page failure must not cancel the batch.
                results.append(
                    {
                        "scanPage": page.scan_page,
                        "status": "failed",
                        "workerId": worker_id,
                        "renderFile": str(page.render_file),
                        "renderSha256": page.render_sha256,
                        "error": str(error),
                    }
                )
    finally:
        close_worker()
    return results


def run_worker_pool(
    assignments: Sequence[Sequence[PageInput]],
    binary: Path,
    staging_root: Path,
    tool: ToolIdentity,
    profile: Mapping[str, Any],
    worker_factory: WorkerFactory,
) -> list[dict[str, Any]]:
    if not assignments:
        return []
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(assignments)) as executor:
        futures = {
            executor.submit(
                _process_assignment,
                worker_id,
                pages,
                binary,
                staging_root,
                tool,
                profile,
                worker_factory,
            ): worker_id
            for worker_id, pages in enumerate(assignments, 1)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                results.extend(future.result())
            except Exception as error:
                assigned = assignments[worker_id - 1]
                results.extend(
                    {
                        "scanPage": page.scan_page,
                        "status": "failed",
                        "workerId": worker_id,
                        "renderFile": str(page.render_file),
                        "renderSha256": page.render_sha256,
                        "error": f"worker group failed: {error}",
                    }
                    for page in assigned
                )
    return sorted(results, key=lambda item: item["scanPage"])


def path_is_within(path: Path, directory: Path) -> bool:
    return _path_is_within(path, directory, resolve=True)


def default_report_path(output_directory: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return output_directory.parent / (
        f"{output_directory.name}-apple-vision-run-{stamp}.json"
    )


def write_report(report_path: Path, report: Mapping[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.parent / f".{report_path.name}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(canonical_json_bytes(report, pretty=True))
    os.replace(temporary, report_path)


def environment_report(physical_cores: int) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "macOSVersion": platform.mac_ver()[0],
        "machine": platform.machine(),
        "python": sys.version.splitlines()[0],
        "pythonExecutable": sys.executable,
        "physicalCores": physical_cores,
        "logicalCores": os.cpu_count(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provenance-sealed Apple Vision OCR with up to 8 workers."
    )
    parser.add_argument("render_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--pages", help="comma-separated pages/ranges, for example 1,3-5,9"
    )
    selector.add_argument("--first-page", type=int)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--workers", default="auto", help="auto or 1..8")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace legacy, partial, stale, or otherwise mismatched output pairs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="run report path; must be outside OUTPUT_DIRECTORY",
    )
    parser.add_argument(
        "--swift-source",
        type=Path,
        default=Path(__file__).with_name("apple_vision_ocr.swift"),
        help=argparse.SUPPRESS,
    )
    return parser


def _main(
    argv: Sequence[str] | None = None,
    *,
    worker_factory: WorkerFactory | None = None,
    compiler: Compiler | None = None,
    physical_cores_override: int | None = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    started_wall = time.monotonic()
    started_at = utc_now()
    try:
        pages, selection_mode = selected_pages(arguments)
        render_directory = arguments.render_directory.expanduser().resolve()
        output_directory = arguments.output_directory.expanduser().resolve()
        if not render_directory.is_dir():
            raise ConfigurationError(f"render directory does not exist: {render_directory}")
        output_directory.mkdir(parents=True, exist_ok=True)
        report_path = (
            arguments.report.expanduser().resolve()
            if arguments.report is not None
            else default_report_path(output_directory).resolve()
        )
        if path_is_within(report_path, output_directory):
            raise ConfigurationError("run report must be outside the OCR output directory")
        tool = build_tool_identity(arguments.swift_source)
        current_profile = dict(VISION_PROFILE)
        current_profile_hash = profile_sha256(current_profile)
        physical_cores = (
            physical_core_count()
            if physical_cores_override is None
            else physical_cores_override
        )
        if physical_cores <= 0:
            raise ConfigurationError("physical core count must be positive")
        # Validate an explicit worker value even when every requested page resumes.
        resolve_worker_count(arguments.workers, 0, physical_cores=physical_cores)
    except ConfigurationError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    page_results: list[dict[str, Any]] = []
    pending: list[PageInput] = []
    skipped_pages: list[int] = []
    blocked_pages: list[int] = []
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
            blocked_pages.append(scan_page)
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
        # gate.  Every filesystem object at either output path is an existing,
        # inconsistent pair until the user authorizes replacement.
        output_exists = os.path.lexists(json_output) or os.path.lexists(text_output)
        if resume.valid:
            skipped_pages.append(scan_page)
            page_results.append(
                {
                    "scanPage": scan_page,
                    "status": "skipped-current",
                    "renderFile": str(render_file),
                    "renderSha256": page.render_sha256,
                    "jsonFile": str(json_output),
                    "jsonSha256": resume.json_sha256,
                    "textFile": str(text_output),
                    "textSha256": resume.text_sha256,
                }
            )
        elif output_exists and not arguments.overwrite:
            blocked_pages.append(scan_page)
            page_results.append(
                {
                    "scanPage": scan_page,
                    "status": "failed",
                    "renderFile": str(render_file),
                    "renderSha256": page.render_sha256,
                    "error": (
                        f"existing output is not a current completion record ({resume.reason}); "
                        "rerun with --overwrite"
                    ),
                }
            )
        else:
            pending.append(page)

    worker_count = resolve_worker_count(
        arguments.workers, len(pending), physical_cores=physical_cores
    )
    assignments: list[list[PageInput]] = []
    if worker_count:
        assignments = [pending[index::worker_count] for index in range(worker_count)]

    budget = Budget()
    model_ready_at = None
    compiler_metadata: dict[str, Any] = {}
    staging_root: Path | None = None
    if assignments:
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=".apple-vision-ocr-stage-", dir=output_directory.parent
            )
        )
        build_directory = staging_root / "build"
        build_directory.mkdir()
        selected_compiler = compiler or compile_swift_worker
        selected_factory = worker_factory
        try:
            if _STOP.is_set() or not budget.allows_next_page():
                raise BudgetExpired("phase deadline reached before Vision startup")
            if selected_factory is None:
                selected_factory = SwiftPersistentWorker
            binary, compiler_metadata = selected_compiler(
                tool.swift_file, build_directory
            )
            model_ready_at = time.time()
            pool_results = run_worker_pool(
                assignments,
                binary,
                staging_root,
                tool,
                current_profile,
                selected_factory,
            )
            page_results.extend(pool_results)
        except (BudgetExpired, subprocess.TimeoutExpired) as error:
            page_results.extend({"scanPage": page.scan_page, "status": "deferred", "reason": str(error)}
                                for page in pending)
        except Exception as error:
            page_results.extend(
                {
                    "scanPage": page.scan_page,
                    "status": "failed",
                    "renderFile": str(page.render_file),
                    "renderSha256": page.render_sha256,
                    "error": f"worker pool could not start: {error}",
                }
                for page in pending
            )
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    page_results.sort(key=lambda item: item["scanPage"])
    completed_pages = [
        item["scanPage"] for item in page_results if item["status"] == "completed"
    ]
    failed_pages = [
        item["scanPage"] for item in page_results if item["status"] == "failed"
    ]
    scheduled_pages = sorted(page.scan_page for group in assignments for page in group)
    attempted_pages = sorted(
        item["scanPage"] for item in page_results if "workerId" in item
    )
    deferred_pages = [item["scanPage"] for item in page_results if item["status"] == "deferred"]
    finished_at = utc_now()
    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "kind": "apple-vision-ocr-run",
        "status": "partial-failure" if failed_pages else "incomplete" if deferred_pages else "pass",
        "counts": {"completed": len(completed_pages), "skipped": len(skipped_pages), "failed": len(failed_pages), "deferred": len(deferred_pages)},
        "modelReadyAt": model_ready_at,
        "textAuthority": TEXT_AUTHORITY,
        "fallbackPolicy": {
            "used": [],
            "tesseractTessdata": "forbidden-never-invoked",
        },
        "startedAt": started_at,
        "finishedAt": finished_at,
        "durationSeconds": round(time.monotonic() - started_wall, 6),
        "renderDirectory": str(render_directory),
        "outputDirectory": str(output_directory),
        "reportFile": str(report_path),
        "selection": {
            "mode": selection_mode,
            "requestedPages": pages,
        },
        "pageSets": {
            "requested": pages,
            "skippedCurrent": skipped_pages,
            "blockedBeforeWorkers": blocked_pages,
            "pendingAtStart": [page.scan_page for page in pending],
            "scheduled": scheduled_pages,
            "attempted": attempted_pages,
            "completed": completed_pages,
            "failed": failed_pages,
            "deferred": deferred_pages,
        },
        "workers": {
            "requested": arguments.workers,
            "resolved": worker_count,
            "maximum": MAX_WORKERS,
            "physicalCores": physical_cores,
            "pageSets": [
                {
                    "workerId": worker_id,
                    "pages": [page.scan_page for page in assignment],
                }
                for worker_id, assignment in enumerate(assignments, 1)
            ],
        },
        "visionProfile": current_profile,
        "visionProfileSha256": current_profile_hash,
        "tool": {
            "runnerFile": str(tool.runner_file),
            "runnerSha256": tool.runner_sha256,
            "swiftFile": str(tool.swift_file),
            "swiftSha256": tool.swift_sha256,
            "combinedSha256": tool.combined_sha256,
            **compiler_metadata,
        },
        "environment": environment_report(physical_cores),
        "pages": page_results,
        "errors": [
            {"scanPage": item["scanPage"], "error": item["error"]}
            for item in page_results
            if item["status"] == "failed"
        ],
    }
    try:
        write_report(report_path, report)
    except OSError as error:
        print(f"ERROR could not write run report: {error}", file=sys.stderr)
        return 2
    print(
        f"REPORT {report_path} requested={len(pages)} "
        f"completed={len(completed_pages)} skipped={len(skipped_pages)} "
        f"failed={len(failed_pages)} workers={worker_count}"
    )
    return 1 if failed_pages else EXIT_INCOMPLETE if deferred_pages else 0


def main(argv: Sequence[str] | None = None, **kwargs: Any) -> int:
    from _ocr_engines import require_supported_host
    try:
        require_supported_host()
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    _STOP.clear()
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous_signals[sig] = signal.signal(sig, lambda *_: _STOP.set())
    try:
        return _main(argv, **kwargs)
    finally:
        for sig, old_handler in previous_signals.items():
            signal.signal(sig, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
