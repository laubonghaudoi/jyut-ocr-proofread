#!/usr/bin/env python3
"""Run PaddleOCR-VL to produce provenance-sealed native or MLX OCR drafts.

It emits the
same per-page JSON/TXT pair, the same sealed provenance and the same resume
semantics, so the proofreading workflow downstream is unchanged.

The generated OCR is deliberately marked as a draft.  It is provenance-rich
input for visual proofreading, never an authority for the source text.

Unlike the Apple Vision runner there is no bounded pool: PaddleOCR-VL is a GPU
model, and on a single card extra workers contend for VRAM with each other and
with any local LLM server.  One persistent worker loads the pipeline once and
processes pages in sequence.

The device is explicit (--device / OCR_PADDLE_DEVICE: gpu:N or cpu; unset, gpu:0
when this Paddle build sees a CUDA device, else cpu), and a GPU run never falls
back to the CPU silently.  A CPU run takes the CPU queue resource, not the GPU
one, and the worker sees no GPU.  The weights come from local folders
(--vl-model-dir / PADDLE_VL_MODEL_DIR and --layout-model-dir /
PADDLE_LAYOUT_MODEL_DIR, or PaddleX's own cache); the runner never lets PaddleX
download them on first use.  The worker's stderr is kept in a log beside the
run report.

The optional ``mlx-vlm-server`` backend keeps Paddle's layout detector on CPU
and runs the VL recogniser in an owned local MLX service on Metal. It uses a
separate interpreter, the GPU lease, and backend-specific draft identities.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import select
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence

import _paddle_pool
from _paddle_pool import (CARD_RESERVE_MIB, MAX_AUTO_WORKERS, MIN_PAGES_A_WORKER, WORKERS_ENV,
                          WORKER_SHARE_MIB)
# An OpenAI-compatible server someone else runs (SGLang or vLLM, as PaddleX names them), serving the
# pinned PaddleOCR-VL weights: Paddle keeps the layout detector on --device and sends each block's
# recognition there.  Native Paddle generation stalls on H200 GPUs (CUDA 12.6 wheel, driver 570:
# a page never ends); a server also batches the blocks of many pages and books.
SERVER_BACKENDS = ("sglang-server", "vllm-server")
SERVER_URL_ENV, SERVER_MODEL_ENV = "OCR_PADDLE_VL_SERVER_URL", "OCR_PADDLE_VL_SERVER_MODEL"
SERVER_REVISION_ENV, SERVER_CONCURRENCY_ENV = "OCR_PADDLE_VL_SERVER_REVISION", "OCR_PADDLE_VL_CONCURRENCY"
DEFAULT_SERVER_CONCURRENCY = 16

from _mlx_vlm_server import (BACKEND as MLX_BACKEND, MLXDeadline, MLXError, MLXServer,
                             SupervisedProcess, probe as probe_mlx)


SCHEMA_VERSION = 2
REPORT_SCHEMA_VERSION = 1
ENGINE = "PaddleOCR-VL"
TEXT_AUTHORITY = "draft-only-not-text-authority"

# Any change here invalidates existing completion records (draft_identity).
# Keep it in sync with the worker's settings.
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

DEVICE_PATTERN = re.compile(r"(?:gpu(?::(?P<index>[0-9]+))?|cpu)\Z")
# The queue resource a CPU run takes, so it never holds the GPU's queue.
DEFAULT_CPU_RESOURCE = "local-cpu"
# What each pipeline version loads: (model name, the folder names PaddleX's own
# cache gives it under official_models/) for the VL recogniser, then for the
# layout detector.
PIPELINE_MODELS: dict[str, tuple[tuple[str, tuple[str, ...]], tuple[str, tuple[str, ...]]]] = {
    "v1": (("PaddleOCR-VL-0.9B", ("PaddleOCR-VL/PaddleOCR-VL-0.9B", "PaddleOCR-VL")),
           ("PP-DocLayoutV2", ("PP-DocLayoutV2",))),
    "v1.5": (("PaddleOCR-VL-1.5-0.9B", ("PaddleOCR-VL-1.5",)),
             ("PP-DocLayoutV3", ("PP-DocLayoutV3",))),
    "v1.6": (("PaddleOCR-VL-1.6-0.9B", ("PaddleOCR-VL-1.6",)),
             ("PP-DocLayoutV3", ("PP-DocLayoutV3",))),
}
WEIGHT_PATTERNS = ("*.safetensors", "*.pdiparams")
WORKER_LOG_TAIL_LINES = 20
INSTALLER_HINT = (
    "This runner never lets PaddleX download weights on first use: the download "
    "would run while the run holds the GPU queue and inside the worker's start-up "
    "time limit. Run the skill's installer (./install.sh), which downloads and "
    "checks the pinned PaddleOCR-VL and layout weights and writes "
    "PADDLE_VL_MODEL_DIR and PADDLE_LAYOUT_MODEL_DIR to env.sh; then load env.sh "
    "and run this command again.")


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
    legacy: str | None = None


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


def draft_identity(profile: Mapping[str, Any]) -> dict[str, Any]:
    """What decides whether a finished page is still current: the pipeline
    version and declared model revision, and the fixed OCR_PROFILE settings.

    Native identity stays compatible with existing drafts. MLX also compares
    backend/runtime versions, local model assets, precision and concurrency.
    Code hashes, interpreter and input paths remain informational.
    """
    settings = {key: profile.get(key) for key in OCR_PROFILE}
    identity = {"engine": ENGINE, "model": {"pipelineVersion": profile.get("pipelineVersion"),
                                        "modelRevision": profile.get("modelRevision")},
            "settings": settings}
    # Native drafts retain their old identity.  An accelerated backend is a
    # different reader, even when its raw text happens to match the CPU draft.
    if profile.get("vlBackend") == MLX_BACKEND or profile.get("vlBackend") in SERVER_BACKENDS:
        identity["backend"] = profile["backendIdentity"]
    return identity


def legacy_profile_hash(profile: Mapping[str, Any]) -> str:
    """The whole-profile hash an older runner wrote for PROFILE's settings: its
    profile was OCR_PROFILE alone, without package versions or model revision."""
    return profile_sha256({key: profile.get(key) for key in OCR_PROFILE})


def build_tool_identity(worker_file: Path, interpreter: str) -> ToolIdentity:
    """Code and interpreter hashes, recorded in each draft; not compared."""
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
    combined.update(Path(__file__).with_name("_mlx_vlm_server.py").read_bytes())
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
    record: Mapping[str, Any], page: PageInput, profile: Mapping[str, Any],
    *, require_raw: bool = True,
) -> str | None:
    # renderFile is recorded, not compared: a book folder can move.
    expected = {
        "scanPage": page.scan_page,
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
    if require_raw and not isinstance(record.get("rawResult"), dict):
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
    identity = draft_identity(profile)
    record.update(
        {
            "schemaVersion": SCHEMA_VERSION,
            "status": "complete",
            "textAuthority": TEXT_AUTHORITY,
            "draftIdentity": identity,
            # Recorded for audit; only draftIdentity decides currency.
            "ocrProfile": dict(profile),
            "provenance": {
                "renderSha256": page.render_sha256,
                "toolSha256": tool.combined_sha256,
                "ocrProfileSha256": profile_sha256(profile),
                "draftIdentitySha256": profile_sha256(identity),
                "textSha256": sha256_bytes(text_bytes),
            },
        }
    )
    digest = sha256_bytes(canonical_json_bytes(record, pretty=True))
    record["provenance"]["jsonPayloadSha256"] = digest
    return record


def validate_completion(
    page: PageInput, tool: ToolIdentity | None, profile: Mapping[str, Any]
) -> ResumeCheck:
    """Whether PAGE's existing pair is a sealed draft of its current render with
    PROFILE's pipeline, model revision and settings (draft_identity).  TOOL is
    recorded in each draft but no longer compared.

    Drafts written before draftIdentity was recorded are matched by their whole
    profile hash: the current profile exactly, or an older runner's profile for
    the same settings (legacy_profile_hash).  Those are reported as legacy and
    may lack rawResult.
    """
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
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return ResumeCheck(False, "provenance is missing")
    has_identity = "draftIdentitySha256" in provenance
    field_error = _validate_record_fields(record, page, profile, require_raw=has_identity)
    if field_error:
        return ResumeCheck(False, field_error)
    if text_from_lines(record["lines"]) != text_bytes:
        return ResumeCheck(False, "TXT bytes do not match JSON lines")
    expected_provenance = {
        "renderSha256": page.render_sha256,
        "textSha256": sha256_bytes(text_bytes),
    }
    for key, expected_value in expected_provenance.items():
        if provenance.get(key) != expected_value:
            return ResumeCheck(False, f"provenance {key} does not match")
    legacy = None
    if has_identity:
        if provenance["draftIdentitySha256"] != profile_sha256(draft_identity(profile)):
            return ResumeCheck(False, "draft identity (pipeline, model revision or settings) does not match")
    elif profile.get("vlBackend") == MLX_BACKEND:
        return ResumeCheck(False, "MLX requires a backend-specific draft identity; use --overwrite")
    elif profile.get("vlBackend") in SERVER_BACKENDS:
        return ResumeCheck(False, "a VL server backend requires a backend-specific draft identity; use --overwrite")
    elif provenance.get("ocrProfileSha256") == profile_sha256(profile):
        legacy = "written before draft identity was recorded; same profile"
    elif provenance.get("ocrProfileSha256") == legacy_profile_hash(profile):
        legacy = "older runner's draft; same pipeline and settings"
    else:
        return ResumeCheck(False, "provenance ocrProfileSha256 does not match (a draft "
                                  "without draftIdentity is matched by its whole profile)")
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
        legacy=legacy,
    )


def atomic_promote(
    staged_text: Path, staged_json: Path, final_text: Path, final_json: Path
) -> None:
    # JSON is the completion record, so it must become visible last.
    os.replace(staged_text, final_text)
    os.replace(staged_json, final_json)


def gpu_free_mib() -> int | None:
    """Free memory of the card the workers use (run_ocr_phase.nvidia_free_mib), or None."""
    try:
        from run_ocr_phase import nvidia_free_mib
        return nvidia_free_mib()
    except Exception:  # noqa: BLE001 - no answer is an answer: one worker
        return None


def worker_count(requested: str | None, *, gpu: bool, pages: int, free_mib: int | None) -> tuple[int, str]:
    """_paddle_pool.worker_count, a wrong --workers as this runner's configuration error."""
    try:
        return _paddle_pool.worker_count(requested, gpu=gpu, pages=pages, free_mib=free_mib)
    except ValueError as error:
        raise ConfigurationError(str(error)) from None


class BudgetExpired(Exception):
    """The phase's time budget ran out while a page was being read, or while
    the model was still loading."""


def worker_environment(device: str | None, weights: Mapping[str, Mapping[str, Any]] | None,
                       *, mlx_url: str | None = None,
                       server: Mapping[str, Any] | None = None) -> dict[str, str]:
    """The worker's device and weight settings (paddleocr_vl_worker.main).

    A GPU run turns PaddleX's silent CPU fallback into an error.  A CPU run hides
    every card, because it does not hold the GPU queue.  Local weight folders
    also skip PaddleX's model-host connectivity check.
    """
    environment: dict[str, str] = {}
    if device:
        environment["JYUT_PADDLE_DEVICE"] = device
        if device.startswith("gpu"):
            environment["PADDLE_PDX_DISABLE_DEVICE_FALLBACK"] = "True"
        else:
            environment["CUDA_VISIBLE_DEVICES"] = ""
    if weights:
        environment["JYUT_PADDLE_VL_MODEL_DIR"] = str(weights["vl"]["directory"])
        environment["JYUT_PADDLE_LAYOUT_MODEL_DIR"] = str(weights["layout"]["directory"])
        environment["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    if mlx_url and weights:
        environment.update(JYUT_PADDLE_VL_BACKEND=MLX_BACKEND,
                           JYUT_PADDLE_VL_SERVER_URL=mlx_url,
                           JYUT_PADDLE_VL_API_MODEL=str(weights["vl"]["directory"]))
    if server:
        environment.update(JYUT_PADDLE_VL_BACKEND=str(server["backend"]),
                           JYUT_PADDLE_VL_SERVER_URL=str(server["url"]),
                           JYUT_PADDLE_VL_API_MODEL=str(server["model"]),
                           JYUT_PADDLE_VL_CONCURRENCY=str(server["concurrency"]))
    return environment


class PaddleWorker:
    """One persistent PaddleOCR-VL process speaking the JSONL page protocol."""

    def __init__(self, interpreter: str, worker_file: Path, pipeline_version: str = "v1.6",
                 lease_fds: tuple[int, ...] = (), *, device: str | None = None,
                 weights: Mapping[str, Mapping[str, Any]] | None = None,
                 log_path: Path | None = None,
                 mlx_url: str | None = None, server: Mapping[str, Any] | None = None,
                 ready_timeout: float = WORKER_READY_TIMEOUT_SECONDS, ready_capped: bool = False,
                 memory_mib: int | None = None, defer_ready: bool = False):
        worker_env = dict(os.environ, JYUT_PADDLE_PIPELINE_VERSION=pipeline_version)
        if memory_mib:
            # One of several on the card: its allocator stops here instead of taking what is left.
            worker_env["FLAGS_gpu_memory_limit_mb"] = str(memory_mib)
        for name in ("JYUT_PADDLE_DEVICE", "JYUT_PADDLE_VL_MODEL_DIR", "JYUT_PADDLE_LAYOUT_MODEL_DIR",
                     "JYUT_PADDLE_VL_BACKEND", "JYUT_PADDLE_VL_SERVER_URL", "JYUT_PADDLE_VL_API_MODEL",
                     "JYUT_PADDLE_VL_CONCURRENCY"):
            worker_env.pop(name, None)
        worker_env.update(worker_environment(device, weights, mlx_url=mlx_url, server=server))
        # The worker's stderr (PaddleX's log, CUDA and out-of-memory messages)
        # goes to this run's log; only the JSON responses come back on stdout.
        log_handle = log_path.open("wb") if log_path is not None else None
        self.owned: SupervisedProcess | None = None
        try:
            # The worker holds the model on the GPU; it must not outlive this runner.
            options = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=log_handle if log_handle is not None else subprocess.DEVNULL,
                           text=True, encoding="utf-8", bufsize=1, env=worker_env)
            if mlx_url:
                self.owned = SupervisedProcess([interpreter, str(worker_file)], lease_fds=lease_fds,
                                               **options)
                self.process_handle = self.owned.process
            else:
                self.process_handle = subprocess.Popen([interpreter, str(worker_file)],
                                                       preexec_fn=die_with_parent(), pass_fds=lease_fds,
                                                       **options)
            assert self.process_handle is not None
        finally:
            if log_handle is not None:
                log_handle.close()
        self.page_in_flight = False
        self.loading = True
        self.response_buffer = b""
        self.execution: dict[str, Any] = {}
        # DEFER_READY: the caller waits (await_ready) once it has started the others, so
        # several load side by side.  The process is started here, by the caller's thread:
        # die_with_parent()'s signal follows the thread that started it, not the process.
        if not defer_ready:
            self.await_ready(ready_timeout, ready_capped)

    def await_ready(self, ready_timeout: float = WORKER_READY_TIMEOUT_SECONDS,
                    ready_capped: bool = False) -> None:
        """Wait until the models are loaded; a worker that does not come up is closed."""
        try:
            # READY_CAPPED: the phase's budget set READY_TIMEOUT; running past it
            # raises BudgetExpired, so a late start ends with the budget.
            ready = self._read_response(ready_timeout, ready_capped)
            if not ready.get("ok") or not ready.get("ready"):
                raise WorkerUnavailable(str(ready.get("error") or "worker did not report ready"))
            if isinstance(ready.get("execution"), dict):
                self.execution = ready["execution"]
            self.loading = False
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
        if self.owned is not None:
            self.owned.close()
            return
        handle = self.process_handle
        if handle.poll() is None:
            if self.page_in_flight or self.loading:
                # Stopped mid-page or mid-load (budget, signal): the page is lost
                # anyway, and waiting would retain GPU memory unnecessarily.
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


# packageVersions keeps the keys older drafts recorded (their whole profile
# matches a legacy draft); the Paddle package actually installed, CPU or GPU,
# is recorded apart in paddlePackages.  With "cuda" the probe also asks Paddle
# what PaddleX's default device test asks (utils/device.get_default_device).
PROBE = (
    "import json, sys, importlib.metadata as m, paddleocr\n"
    "def v(name):\n"
    "    try: return m.version(name)\n"
    "    except Exception: return None\n"
    "out = {'versions': {'paddleocr': getattr(paddleocr, '__version__', None),\n"
    "                    'paddlepaddle-gpu': v('paddlepaddle-gpu'),\n"
    "                    'paddlex': v('paddlex')},\n"
    "       'paddlePackages': {n: v(n) for n in ('paddlepaddle-gpu', 'paddlepaddle')}}\n"
    "if 'cuda' in sys.argv[1:]:\n"
    "    try:\n"
    "        import paddle\n"
    "        built = bool(paddle.device.is_compiled_with_cuda())\n"
    "        out['cuda'] = {'compiled': built,\n"
    "                       'deviceCount': int(paddle.device.cuda.device_count()) if built else 0}\n"
    "    except Exception as error:\n"
    "        out['cuda'] = {'error': type(error).__name__ + ': ' + str(error)}\n"
    "print(json.dumps(out))\n"
)


def verify_paddleocr_available(interpreter: str, *, probe_cuda: bool = False) -> dict[str, Any]:
    """{"versions": ..., "paddlePackages": ..., "cuda": ...} from INTERPRETER."""
    command = [interpreter, "-c", PROBE] + (["cuda"] if probe_cuda else [])
    result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ConfigurationError(
            "PaddleOCR is not importable with "
            f"{interpreter}: {detail[-1] if detail else 'unknown error'}"
        )
    try:
        probe = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        probe = {}
    return probe if isinstance(probe, dict) else {}


def paddle_package(probe: Mapping[str, Any]) -> dict[str, str] | None:
    """The Paddle distribution installed: paddlepaddle-gpu or paddlepaddle."""
    packages = probe.get("paddlePackages") or {}
    for name in ("paddlepaddle-gpu", "paddlepaddle"):
        if packages.get(name):
            return {"name": name, "version": str(packages[name])}
    return None


def parse_device(value: str | None) -> str | None:
    """``gpu:N`` or ``cpu`` (``gpu`` alone is gpu:0); None, '' or ``auto``: detect."""
    if value is None or value.strip().lower() in ("", "auto"):
        return None
    text = value.strip().lower()
    match = DEVICE_PATTERN.match(text)
    if match is None:
        raise ConfigurationError(f"device must be gpu:N or cpu, not {value!r}")
    return f"gpu:{int(match.group('index') or 0)}" if text.startswith("gpu") else "cpu"


def resolve_device(requested: str | None, probe: Mapping[str, Any]) -> tuple[str, str | None]:
    """The device to run on, and why a GPU request cannot be met (or None).

    Unset, PaddleX's own default: gpu:0 when this Paddle build has CUDA and sees
    a device, else cpu.  A GPU that was asked for is checked the same way, since
    PaddleX would otherwise move the run to the CPU with only a warning.
    """
    cuda = probe.get("cuda") or {}
    if requested is None:
        return ("gpu:0" if cuda.get("compiled") and cuda.get("deviceCount", 0) > 0 else "cpu"), None
    if requested == "cpu":
        return requested, None
    index = int(requested.partition(":")[2])
    package = paddle_package(probe)
    installed = f"{package['name']} {package['version']}" if package else "no Paddle package found"
    if "error" in cuda:
        problem = f"Paddle does not import ({cuda['error']})"
    elif not cuda.get("compiled"):
        problem = f"this Paddle build has no CUDA ({installed})"
    elif cuda.get("deviceCount", 0) <= index:
        problem = f"Paddle sees {cuda.get('deviceCount', 0)} CUDA device(s) ({installed})"
    else:
        return requested, None
    return requested, (
        f"{requested} is configured (--device / OCR_PADDLE_DEVICE), but {problem}; PaddleX "
        "would run on the CPU instead. Run the installer again to set up Paddle for this "
        "GPU, or set OCR_PADDLE_DEVICE=cpu to run on the CPU on purpose.")


def paddlex_cache_root() -> Path:
    """PaddleX's own model cache (paddlex/utils/cache.py, official_models)."""
    return Path(os.environ.get("PADDLE_PDX_CACHE_HOME") or Path.home() / ".paddlex") / "official_models"


def model_dir_problem(path: Path, model_name: str) -> str | None:
    """Why PATH is not a usable local copy of MODEL_NAME, or None.

    The checks PaddleX makes before it loads (inference.yml names the model),
    plus a nonempty weights file; the installer has checked the hashes.
    """
    if not path.is_dir():
        return "is not a folder"
    try:
        config = (path / "inference.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "has no readable inference.yml"
    names = re.findall(r"^\s*model_name:\s*['\"]?([^'\"\s#]+)", config, re.M)
    if model_name not in names:
        return f"holds {names[0] if names else 'an unnamed model'}, not {model_name}"
    if not any(weight.is_file() and weight.stat().st_size > 0
               for pattern in WEIGHT_PATTERNS for weight in path.glob(pattern)):
        return "has no weights file (*.safetensors or *.pdiparams)"
    return None


def resolve_weights(pipeline_version: str, vl_dir: Path | None,
                    layout_dir: Path | None) -> dict[str, dict[str, str]]:
    """The local folders of the two models PIPELINE_VERSION loads.

    A configured folder must hold its model; unconfigured, PaddleX's cache is
    used when it already holds it.  Never a download: a missing model is a
    configuration error that points to the installer.
    """
    models = PIPELINE_MODELS.get(pipeline_version)
    if models is None:
        raise ConfigurationError(f"unknown pipeline version {pipeline_version!r}; "
                                 f"known: {', '.join(PIPELINE_MODELS)}")
    weights: dict[str, dict[str, str]] = {}
    for role, configured, (model_name, cache_names), variable, option in (
            ("vl", vl_dir, models[0], "PADDLE_VL_MODEL_DIR", "--vl-model-dir"),
            ("layout", layout_dir, models[1], "PADDLE_LAYOUT_MODEL_DIR", "--layout-model-dir")):
        if configured is not None:
            path = configured.expanduser().resolve()
            problem = model_dir_problem(path, model_name)
            if problem:
                raise ConfigurationError(f"{variable} ({option}) {path} {problem}. {INSTALLER_HINT}")
            weights[role] = {"model": model_name, "directory": str(path), "source": "configured"}
            continue
        for name in cache_names:
            path = paddlex_cache_root() / name
            if model_dir_problem(path, model_name) is None:
                weights[role] = {"model": model_name, "directory": str(path), "source": "paddlex-cache"}
                break
        else:
            raise ConfigurationError(
                f"the {model_name} weights are not installed: {variable} ({option}) is not set "
                f"and PaddleX's cache {paddlex_cache_root()} has no copy. {INSTALLER_HINT}")
    return weights


def execution_problem(device: str, loaded: Mapping[str, Any]) -> str | None:
    """Whether the worker loaded a model somewhere other than DEVICE."""
    for key, label in (("vlDevice", "VL recogniser"), ("layoutDevice", "layout detector")):
        value = loaded.get(key)
        if not isinstance(value, str) or not value:
            continue  # this PaddleX does not say; the fallback switch still holds
        kind, _, index = value.partition(":")
        wanted_kind, _, wanted_index = device.partition(":")
        if kind != wanted_kind or (index and wanted_index and index != wanted_index):
            return (f"Paddle loaded the {label} on {value}, not {device}; refusing a "
                    "silent device fallback")
    return None


def server_identity(backend: str, weights: Mapping[str, Any], model: str, revision: str,
                    concurrency: int) -> dict[str, Any]:
    """A VL server backend's identity: the pinned local weights the server is declared to serve
    (their assets' SHA-256, as for MLX), the model name and the caller-declared deployment
    revision (change it after the server's weights or runtime change), the block concurrency."""
    assets: dict[str, str] = {}
    for role in ("vl", "layout"):
        directory = Path(weights[role]["directory"])
        files = [f for f in directory.iterdir() if f.is_file() and
                 (f.suffix in (".json", ".safetensors", ".pdiparams", ".yml", ".yaml",
                               ".jinja", ".model", ".txt", ".py") or f.name == "tokenizer")]
        assets[role] = profile_sha256({f.name: sha256_file(f) for f in sorted(files)})
    return {"name": backend, "modelAssetsSha256": assets, "apiModel": model,
            "serverRevision": revision, "vlRecMaxConcurrency": concurrency}


def server_execution_problem(loaded: Mapping[str, Any], server: Mapping[str, Any]) -> str | None:
    """Whether the worker's recogniser uses the declared server, model and concurrency."""
    expected = {"vlBackend": server["backend"], "vlApiModel": server["model"],
                "vlMaxConcurrency": server["concurrency"]}
    for key, value in expected.items():
        if loaded.get(key) != value:
            return f"VL server attestation failed: {key}={loaded.get(key)!r}, expected {value!r}"
    if str(loaded.get("vlServerUrl", "")).rstrip("/") != str(server["url"]).rstrip("/"):
        return "VL server attestation failed: Paddle did not use the declared server URL"
    layout = loaded.get("layoutDevice")
    if isinstance(layout, str) and layout and layout.partition(":")[0] != server["layoutDevice"].partition(":")[0]:
        return f"Paddle loaded the layout detector on {layout}, not {server['layoutDevice']}"
    return None


def mlx_identity(weights: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
    """Attest local assets, not an arbitrary cache folder's name or location."""
    model = Path(weights["vl"]["directory"])
    try:
        config = json.loads((model / "config.json").read_text())
    except (OSError, ValueError) as error:
        raise ConfigurationError(f"MLX weights need a readable config.json: {error}") from error
    if config.get("quantization") or config.get("quantization_config"):
        raise ConfigurationError("MLX OCR requires the pinned unquantized model weights")
    dtype = config.get("torch_dtype") or config.get("dtype")
    if dtype not in ("bfloat16", "float16", "float32"):
        raise ConfigurationError(f"MLX weights have an unsupported or missing dtype: {dtype!r}")
    assets: dict[str, str] = {}
    for role in ("vl", "layout"):
        directory = Path(weights[role]["directory"])
        files = [p for p in directory.iterdir() if p.is_file() and
                 (p.suffix in (".json", ".safetensors", ".pdiparams", ".yml", ".yaml",
                               ".jinja", ".model", ".txt", ".py") or p.name == "tokenizer")]
        assets[role] = profile_sha256({p.name: sha256_file(p) for p in sorted(files)})
    return {"name": MLX_BACKEND, "versions": runtime["versions"],
            "modelAssetsSha256": assets, "weightDtype": dtype, "quantization": None,
            "vlRecMaxConcurrency": 1, "layoutDevice": "cpu"}


def mlx_execution_problem(loaded: Mapping[str, Any], server: MLXServer) -> str | None:
    expected = {"layoutDevice": "cpu", "vlBackend": MLX_BACKEND,
                "vlApiModel": server.model, "vlMaxConcurrency": 1}
    for key, value in expected.items():
        if loaded.get(key) != value:
            return f"MLX execution attestation failed: {key}={loaded.get(key)!r}, expected {value!r}"
    # The official OpenAI-compatible client accepts a trailing slash.
    if str(loaded.get("vlServerUrl", "")).rstrip("/") != server.url:
        return "MLX execution attestation failed: Paddle did not use the owned server URL"
    if server.owned is None or server.owned.process.poll() is not None:
        return "the owned MLX server exited during Paddle startup"
    return None


def worker_log_path(report_path: Path) -> Path:
    """This run's worker log, beside its report (paddleocr-vl-run.worker.log)."""
    return report_path.with_name(report_path.stem + ".worker.log")


ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def log_tail(path: Path, lines: int = WORKER_LOG_TAIL_LINES) -> list[str]:
    """The last LINES nonblank lines of PATH, without PaddleX's colour codes."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 64 * 1024))
            data = handle.read()
    except OSError:
        return []
    text = ANSI_ESCAPE.sub("", data.decode("utf-8", "replace")).replace("\r", "\n")
    return [line for line in text.splitlines() if line.strip()][-lines:]


def cpu_resource_name(value: str) -> str:
    # The same rule as the GPU queue's resource names (_gpu_queue).
    if not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise argparse.ArgumentTypeError("CPU resource must be a nonempty short name")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provenance-sealed PaddleOCR-VL drafts (native or Apple MLX)."
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
    parser.add_argument("--vl-backend", choices=("native", MLX_BACKEND, *SERVER_BACKENDS),
                        default=os.environ.get("OCR_PADDLE_VL_BACKEND", "native"),
                        help="VL inference backend (or OCR_PADDLE_VL_BACKEND; default native); "
                             "sglang-server/vllm-server: an OpenAI-compatible server you run, at --vl-server-url")
    parser.add_argument("--vl-server-url", default=os.environ.get(SERVER_URL_ENV),
                        help=f"the VL server's OpenAI base URL, e.g. http://127.0.0.1:31500/v1 (or {SERVER_URL_ENV})")
    parser.add_argument("--vl-server-model", default=os.environ.get(SERVER_MODEL_ENV, "PaddleOCR-VL-1.6"),
                        help=f"the model name the VL server serves (or {SERVER_MODEL_ENV}; default PaddleOCR-VL-1.6)")
    parser.add_argument("--vl-server-revision", default=os.environ.get(SERVER_REVISION_ENV),
                        help=f"caller-declared deployment revision of the VL server (or {SERVER_REVISION_ENV}); "
                             "change it after the server's weights or runtime change")
    parser.add_argument("--vl-concurrency", type=int,
                        default=int(os.environ.get(SERVER_CONCURRENCY_ENV, DEFAULT_SERVER_CONCURRENCY)),
                        help=f"a page's blocks sent to the VL server at once (or {SERVER_CONCURRENCY_ENV}; "
                             f"default {DEFAULT_SERVER_CONCURRENCY})")
    parser.add_argument("--mlx-python", default=os.environ.get("MLX_VLM_PYTHON"),
                        help="isolated MLX-VLM Python interpreter (or MLX_VLM_PYTHON)")
    parser.add_argument("--model-revision", default=os.environ.get("PADDLEOCR_MODEL_REVISION", "unversioned-cache"),
                        help="caller-declared cache revision; change after replacing cached model files")
    parser.add_argument("--device",
                        help="gpu:N or cpu (default OCR_PADDLE_DEVICE; unset, gpu:0 when this "
                             "Paddle build sees a CUDA device, else cpu); a GPU run never "
                             "falls back to the CPU")
    parser.add_argument("--vl-model-dir", type=Path,
                        default=os.environ.get("PADDLE_VL_MODEL_DIR") or None,
                        help="local PaddleOCR-VL weights folder (or PADDLE_VL_MODEL_DIR; "
                             "unset, PaddleX's cache); never downloaded here")
    parser.add_argument("--layout-model-dir", type=Path,
                        default=os.environ.get("PADDLE_LAYOUT_MODEL_DIR") or None,
                        help="local layout detector weights folder (or PADDLE_LAYOUT_MODEL_DIR)")
    parser.add_argument("--cpu-resource", type=cpu_resource_name,
                        default=os.environ.get("OCR_CPU_RESOURCE", DEFAULT_CPU_RESOURCE),
                        help=f"queue resource of a CPU run, apart from the GPU's (default "
                             f"OCR_CPU_RESOURCE or {DEFAULT_CPU_RESOURCE})")
    parser.add_argument("--timeout", type=float, default=PAGE_TIMEOUT_SECONDS)
    parser.add_argument("--workers", default=os.environ.get(WORKERS_ENV, "auto"), metavar="N|auto",
                        help=f"workers reading pages at once on one GPU (or {WORKERS_ENV}; default auto: "
                             f"as many {WORKER_SHARE_MIB} MiB shares as the free memory holds beside "
                             f"{CARD_RESERVE_MIB} MiB, at most {MAX_AUTO_WORKERS}; 1 where it cannot be read). "
                             "The pages' drafts do not depend on it")
    parser.add_argument(
        "--worker-source",
        type=Path,
        default=Path(__file__).with_name("paddleocr_vl_worker.py"),
        help=argparse.SUPPRESS,
    )
    add_queue_arguments(parser)
    return parser


from _gpu_queue import (GPUQueue, GPUQueueError, GPUQueueTimeout, LOCAL_MODEL_SUMMARY, LocalModelOwnsGPU,
                        add_queue_arguments)

from _phase_budget import (  # noqa: E402  (scripts/ is sys.path[0])
    EXIT_INCOMPLETE, Budget, die_with_parent, exit_on_sigterm)


def main(argv: Sequence[str] | None = None) -> int:
    from _ocr_engines import require_supported_host
    try:
        require_supported_host()
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
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
        worker_log = worker_log_path(report_path)
        if worker_log in [Path(__file__).resolve(), arguments.worker_source.resolve(), report_path]:
            raise ConfigurationError("the worker log must not overwrite a tool or the report")
        if arguments.timeout <= 0 or not arguments.pipeline_version or not arguments.model_revision:
            raise ConfigurationError("timeout, pipeline-version and model-revision must be valid")
        if arguments.device is not None:
            requested_device, device_source = parse_device(arguments.device), "cli"
        else:
            requested_device, device_source = parse_device(os.environ.get("OCR_PADDLE_DEVICE")), "config"
        if requested_device is None:
            device_source = "detected"
        use_mlx = arguments.vl_backend == MLX_BACKEND
        use_server = arguments.vl_backend in SERVER_BACKENDS
        if arguments.vl_backend not in ("native", MLX_BACKEND, *SERVER_BACKENDS):
            raise ConfigurationError(f"unknown VL backend {arguments.vl_backend!r}")
        if use_server:
            if not arguments.vl_server_url or not arguments.vl_server_url.startswith(("http://", "https://")):
                raise ConfigurationError(f"{arguments.vl_backend} needs --vl-server-url (or {SERVER_URL_ENV}): "
                                         "the server's http(s) OpenAI base URL")
            if not arguments.vl_server_revision:
                raise ConfigurationError(f"{arguments.vl_backend} needs --vl-server-revision (or "
                                         f"{SERVER_REVISION_ENV}): the drafts record which deployment read them")
            if not arguments.vl_server_model or arguments.vl_concurrency < 1:
                raise ConfigurationError("--vl-server-model must be set and --vl-concurrency at least 1")
        if use_mlx:
            if requested_device and requested_device != "cpu":
                raise ConfigurationError("MLX uses CPU layout and Metal VL; --device must be cpu")
            requested_device = "cpu"
            device_source = "mlx-layout-cpu"
        interpreter = resolve_interpreter(arguments.interpreter)
        probe = verify_paddleocr_available(interpreter, probe_cuda=requested_device != "cpu")
        versions = probe.get("versions") if isinstance(probe.get("versions"), dict) else {}
        device, device_problem = resolve_device(requested_device, probe)
        tool = build_tool_identity(arguments.worker_source, interpreter)
        # Device, dtype, the Paddle package and the weight folders are recorded
        # in each draft (ocrProfile.execution) and the report, never compared;
        # this profile, which resume checks use, keeps the keys it always had.
        current_profile = dict(OCR_PROFILE, pipelineVersion=arguments.pipeline_version,
                               packageVersions=versions, modelRevision=arguments.model_revision,
                               modelIdentityAssurance="package-versions-and-caller-declared-cache-revision-not-weight-attestation")
        execution: dict[str, Any] = {
            "device": device, "deviceSource": device_source,
            "paddlePackage": paddle_package(probe),
            # What Paddle said about CUDA before the run (None: not asked, cpu).
            "cudaProbe": probe.get("cuda"),
            "deviceFallback": "refused" if device.startswith("gpu") else "no-gpu-visible",
            "queueResource": arguments.gpu_resource if device.startswith("gpu") else arguments.cpu_resource,
        }
        mlx_interpreter = None
        if use_mlx:
            if not arguments.mlx_python:
                raise ConfigurationError("MLX_VLM_PYTHON or --mlx-python is required; rerun the installer")
            mlx_interpreter = resolve_interpreter(arguments.mlx_python)
            mlx_runtime = probe_mlx(mlx_interpreter)
            weights = resolve_weights(arguments.pipeline_version, arguments.vl_model_dir,
                                      arguments.layout_model_dir)
            current_profile.update(vlBackend=MLX_BACKEND,
                                   backendIdentity=mlx_identity(weights, mlx_runtime),
                                   modelIdentityAssurance="local-model-assets-sha256")
            execution.update(vlBackend=MLX_BACKEND, mlxRuntime=mlx_runtime,
                             mlxInterpreter=mlx_interpreter, weights=weights,
                             queueResource=arguments.gpu_resource, deviceFallback="refused")
        server: dict[str, Any] | None = None
        if use_server:
            weights = resolve_weights(arguments.pipeline_version, arguments.vl_model_dir,
                                      arguments.layout_model_dir)
            server = {"backend": arguments.vl_backend, "url": arguments.vl_server_url.rstrip("/"),
                      "model": arguments.vl_server_model, "concurrency": arguments.vl_concurrency,
                      "layoutDevice": device}
            current_profile.update(vlBackend=arguments.vl_backend,
                                   backendIdentity=server_identity(arguments.vl_backend, weights,
                                                                   arguments.vl_server_model,
                                                                   arguments.vl_server_revision,
                                                                   arguments.vl_concurrency),
                                   modelIdentityAssurance="local-model-assets-sha256-and-caller-declared-server-revision")
            execution.update(vlBackend=arguments.vl_backend, vlServerUrl=server["url"],
                             vlServerModel=server["model"], vlServerRevision=arguments.vl_server_revision,
                             weights=weights)
    except (ConfigurationError, MLXError, OSError, subprocess.TimeoutExpired) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    page_results: list[dict[str, Any]] = []
    pending: list[PageInput] = []
    skipped_pages: list[int] = []
    legacy_pages: list[int] = []
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
            entry: dict[str, Any] = {
                "scanPage": scan_page,
                "status": "skipped-current",
                "renderFile": str(render_file),
                "renderSha256": page.render_sha256,
                "jsonSha256": resume.json_sha256,
                "textSha256": resume.text_sha256,
            }
            if resume.legacy:
                legacy_pages.append(scan_page)
                entry["legacy"] = resume.legacy
            page_results.append(entry)
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

    # Checked before the queue: a GPU that Paddle cannot use and weights that
    # are not on disk fail now, never inside the lease or the ready timeout.
    if pending:
        try:
            if device_problem:
                raise ConfigurationError(device_problem)
            if not use_mlx and not use_server:
                execution["weights"] = resolve_weights(
                    arguments.pipeline_version, arguments.vl_model_dir, arguments.layout_model_dir)
        except ConfigurationError as error:
            print(f"ERROR {error}", file=sys.stderr)
            return 2

    worker: PaddleWorker | None = None
    workers: list[PaddleWorker] = []
    spare: list[tuple[int, PaddleWorker]] = []       # the workers beside the first, as started
    mlx_server: MLXServer | None = None
    lease: GPUQueue | None = None
    queue_deferred = local_model_deferred = False
    worker_trouble = False
    log_used = False
    completed_pages: list[int] = []
    deferred_pages: list[int] = []
    budget = Budget()
    # When the worker said it was ready, its models loaded (time.time()): the
    # model-ready signal ocr_book.py measures the engine's load by.
    model_ready_at: float | None = None

    try:
        if pending:
            try:
                if not budget.allows_next_page():
                    raise GPUQueueTimeout("phase deadline reached before model loading")
                # A CPU run takes its own resource: it must not hold the GPU's
                # queue for hours and keep GPU work waiting behind it.
                lease = GPUQueue(arguments.gpu_queue_dir, execution["queueResource"])
                # A local model that owns the GPU (OCR_PHASE_RELEASE_CMD) defers
                # only runs that need its memory: not a CPU run on Linux, which
                # run_ocr_phase.py counts as needing none (macOS memory is shared).
                lease.acquire(timeout=arguments.gpu_queue_timeout, phase_deadline=budget.deadline,
                              local_model_guard=use_mlx or device.startswith("gpu") or sys.platform == "darwin")
                log_used = True
                # The load never runs past the phase's deadline (OCR_PHASE_DEADLINE).
                ready_limit, ready_capped = budget.page_timeout(WORKER_READY_TIMEOUT_SECONDS)
                if use_mlx:
                    assert mlx_interpreter is not None
                    server_log = report_path.with_name(report_path.stem + ".mlx-server.log")
                    execution["serverLog"] = str(server_log)
                    mlx_server = MLXServer(mlx_interpreter, Path(execution["weights"]["vl"]["directory"]),
                                           server_log, lease_fds=lease.pass_fds,
                                           ready_timeout=ready_limit, budget_capped=ready_capped)
                    execution["serverHealth"] = mlx_server.health
                    ready_limit, ready_capped = budget.page_timeout(WORKER_READY_TIMEOUT_SECONDS)
                wanted, wanted_why = worker_count(
                    arguments.workers, gpu=not use_mlx and device.startswith("gpu"), pages=len(pending),
                    free_mib=gpu_free_mib() if str(arguments.workers).strip().lower() == "auto" else None)
                share = WORKER_SHARE_MIB if wanted > 1 else None
                # The others are started first and waited for after the first, so all of them
                # load side by side.  One that does not come up (no memory left for it, a load
                # the budget cut short) is left out, and the rest read.
                for number in range(2, wanted + 1):
                    try:
                        spare.append((number, PaddleWorker(
                            interpreter, tool.worker_file, arguments.pipeline_version,
                            lease_fds=lease.pass_fds, device=device, weights=execution["weights"],
                            log_path=worker_log.with_name(f"{worker_log.stem}.{number}{worker_log.suffix}"),
                            server=server, memory_mib=share, defer_ready=True)))
                    except OSError as error:
                        print(f"  worker {number}: {error}; left out", file=sys.stderr, flush=True)
                worker = PaddleWorker(interpreter, tool.worker_file, arguments.pipeline_version,
                                      lease_fds=lease.pass_fds, device=device,
                                      weights=execution["weights"], log_path=worker_log,
                                      ready_timeout=ready_limit, ready_capped=ready_capped,
                                      mlx_url=mlx_server.url if mlx_server else None, server=server,
                                      memory_mib=share)
                model_ready_at = time.time()
                execution.update({key: worker.execution.get(key)
                                  for key in ("vlDevice", "layoutDevice", "dtype")})
                problem = (mlx_execution_problem(worker.execution, mlx_server) if mlx_server
                           else server_execution_problem(worker.execution, server) if server
                           else execution_problem(device, worker.execution))
                if problem:
                    raise WorkerUnavailable(problem)
                if use_mlx:
                    execution.update(vlDevice="metal:gpu", layoutDevice="cpu", dtype=None,
                                     weightDtype=current_profile["backendIdentity"]["weightDtype"],
                                     vlClient=worker.execution)
                description = (f"Metal GPU, CPU layout ({execution['weightDtype']} weights)" if use_mlx
                               else f"{device} layout, VL on {server['backend']} {server['url']}" if server
                               else f"{device} ({execution['dtype'] or 'dtype not reported'})")
                workers = [worker]
                for number, extra in spare:
                    limit, capped = budget.page_timeout(WORKER_READY_TIMEOUT_SECONDS)
                    try:
                        extra.await_ready(limit, capped)
                        problem = (server_execution_problem(extra.execution, server) if server
                                   else execution_problem(device, extra.execution))
                    except BudgetExpired:
                        problem = "the time budget ran out while it loaded"
                    except (WorkerUnavailable, OSError) as error:
                        problem = str(error)
                    if problem:
                        extra.close()
                        print(f"  worker {number}: {problem}; left out", file=sys.stderr, flush=True)
                    else:
                        workers.append(extra)
                pool = f", {len(workers)} workers" if len(workers) > 1 else ""
                print(f"  PaddleOCR-VL on {description}{pool}, "
                      f"queue {execution['queueResource']}", flush=True)
            except GPUQueueTimeout as error:
                queue_deferred = True
                local_model_deferred = isinstance(error, LocalModelOwnsGPU)
                deferred_pages.extend(page.scan_page for page in pending)
                page_results.extend({"scanPage": page.scan_page, "status": "deferred",
                                     "renderFile": str(page.render_file), "reason": str(error)}
                                    for page in pending)
                print(f"[gpu-queue] {error}; {len(pending)} page(s) deferred", flush=True)
                pending = []
            except (BudgetExpired, MLXDeadline):
                # Out of time while the model loaded: the pages are not done
                # yet, not failed (the summary's ERROR says the budget ran out).
                deferred_pages.extend(page.scan_page for page in pending)
                page_results.extend({"scanPage": page.scan_page, "status": "deferred",
                                     "renderFile": str(page.render_file),
                                     "reason": "time budget ran out while the model was loading"}
                                    for page in pending)
                print(f"  time budget reached while PaddleOCR-VL was loading: {len(pending)} page(s) "
                      "left for the next call", flush=True)
                pending = []
            except (WorkerUnavailable, MLXError, GPUQueueError, OSError) as error:
                worker_trouble = worker_trouble or not isinstance(error, GPUQueueError)
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

        def seal(reader: PaddleWorker, page: PageInput, page_limit: float, capped: bool,
                 page_started: float) -> dict[str, Any]:
            """Read PAGE on READER and write its draft pair; the page's entry in the report."""
            response = reader.ocr_page(page.render_file, page_limit, capped)
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
            if use_mlx:
                for order, line in enumerate(lines or []):
                    box = line.get("boundingBox") if isinstance(line, dict) else None
                    if (not isinstance(box, list) or len(box) != 4 or
                        any(not isinstance(v, (int, float)) or isinstance(v, bool) or
                            not math.isfinite(v) or v < 0 or v > 1 for v in box) or
                        box[2] <= 0 or box[3] <= 0 or box[0] + box[2] > 1.000001 or
                        box[1] + box[3] > 1.000001):
                        raise PageOCRFailure(f"invalid layout geometry for MLX line {order}")
                    # Array order is the final geometric/model merge order;
                    # rawResult retains the model's original optional order.
                    line["blockOrder"] = order
                raw_record["layoutContract"] = {"version": 1, "role": "layout",
                    "coordinateSystem": "normalized-top-left-xywh", "textJoin": "\n"}
            field_error = _validate_record_fields(
                raw_record, page, current_profile
            )
            if field_error:
                raise PageOCRFailure(f"invalid worker output: {field_error}")
            text_bytes = text_from_lines(lines)
            record = build_completion_record(
                raw_record, page, text_bytes, tool, dict(current_profile, execution=execution)
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
            return {
                "scanPage": page.scan_page,
                "status": "completed",
                "renderFile": str(page.render_file),
                "renderSha256": page.render_sha256,
                "jsonSha256": sha256_bytes(json_bytes),
                "textSha256": sha256_bytes(text_bytes),
                "lineCount": len(lines),
                "seconds": round(time.monotonic() - page_started, 2),
            }

        # The pages wait in one line; each reader takes the next when it is free.  One reader
        # (the default) reads them in order, as before.  `state` guards the line and every
        # list of the report; the reading itself runs outside it.
        todo = deque(pending)
        state = threading.Lock()
        halted: list[str] = []            # why no further page is taken: "budget" or "lost"
        expired_on: list[int] = []        # the pages the budget ran out on
        gone: list[str] = []              # the error of each reader that is gone
        attempts: dict[int, int] = {}
        alive = [len(workers)]
        took_over = False                 # one worker with no ceiling read what several could not

        def serve(reader: PaddleWorker) -> None:
            nonlocal worker_trouble
            while True:
                with state:
                    if halted or not todo:
                        return
                    if not budget.allows_next_page():
                        # Out of time for this call: the pages left are neither failed
                        # nor blocked, just not done yet; the next call resumes them.
                        halted.append("budget")
                        return
                    page = todo.popleft()
                page_started = time.monotonic()
                page_limit, capped = budget.page_timeout(arguments.timeout)
                try:
                    entry = seal(reader, page, page_limit, capped, page_started)
                    with state:
                        completed_pages.append(page.scan_page)
                        budget.record(time.monotonic() - page_started)
                        budget.sealed(page.scan_page)
                        page_results.append(entry)
                        print(
                            f"  page {page.scan_page:04d}: {entry['lineCount']} blocks in "
                            f"{time.monotonic() - page_started:.1f}s",
                            flush=True,
                        )
                except BudgetExpired:
                    # The budget ran out mid-page; this reader is gone with the page.
                    with state:
                        todo.appendleft(page)
                        halted.append("budget")
                        expired_on.append(page.scan_page)
                    return
                except (PageOCRFailure, TypeError, ValueError) as error:
                    with state:
                        worker_trouble = True
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
                    with state:
                        worker_trouble = True
                        alive[0] -= 1
                        gone.append(str(error))
                        attempts[page.scan_page] = attempts.get(page.scan_page, 0) + 1
                        if alive[0] and attempts[page.scan_page] < 2:
                            # A reader is gone (out of memory, say) and others go on:
                            # one of them reads its page.
                            todo.appendleft(page)
                            print(f"  page {page.scan_page:04d}: its worker is gone ({error}); "
                                  f"{alive[0]} worker(s) go on and one reads it again",
                                  file=sys.stderr, flush=True)
                        elif alive[0]:
                            # The page has taken two readers with it: it fails, the rest go on.
                            failed_pages.append(page.scan_page)
                            page_results.append({"scanPage": page.scan_page, "status": "failed",
                                                 "renderFile": str(page.render_file),
                                                 "error": f"worker unavailable: {error}"})
                            print(f"  page {page.scan_page:04d}: FAILED its second worker is gone too "
                                  f"({error})", file=sys.stderr, flush=True)
                        else:
                            # The pipeline is gone; every remaining page fails with it.
                            todo.appendleft(page)
                            halted.append("lost")
                    return

        def read_with(readers: Sequence[PaddleWorker]) -> None:
            """READERS take the pages of the line until it is empty or a reason to stop is set."""
            if len(readers) == 1:
                serve(readers[0])
                return
            crashed: list[BaseException] = []

            def guarded(reader: PaddleWorker) -> None:
                try:
                    serve(reader)
                except BaseException as error:  # noqa: BLE001 - raised again below, in the main thread
                    with state:
                        crashed.append(error)
                        halted.append("budget")

            threads = [threading.Thread(target=guarded, args=(reader,), daemon=True) for reader in readers]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            if crashed:
                raise crashed[0]

        if workers:
            read_with(workers)
        if "lost" in halted and share and todo and budget.allows_next_page():
            # Every worker of several is gone, each under its ceiling: out of memory is the likely
            # reason.  One worker with no ceiling - the way a single worker always runs - reads
            # what is left, so several workers never fail a page that one would have read.
            print(f"  all {len(workers)} workers are gone ({gone[-1]}); one worker with no memory "
                  "ceiling reads the pages left", file=sys.stderr, flush=True)
            limit, capped = budget.page_timeout(WORKER_READY_TIMEOUT_SECONDS)
            try:
                last = PaddleWorker(interpreter, tool.worker_file, arguments.pipeline_version,
                                    lease_fds=lease.pass_fds, device=device, weights=execution["weights"],
                                    log_path=worker_log.with_name(f"{worker_log.stem}.alone{worker_log.suffix}"),
                                    ready_timeout=limit, ready_capped=capped)
            except BudgetExpired:
                halted[:] = ["budget"]
            except (WorkerUnavailable, OSError) as error:
                gone.append(str(error))
            else:
                spare.append((0, last))
                took_over = True
                halted.clear()
                attempts.clear()
                alive[0] = 1
                read_with([last])
        left = sorted(todo, key=lambda page: page.scan_page)
        if "lost" in halted:
            for lost in left:
                failed_pages.append(lost.scan_page)
                page_results.append(
                    {
                        "scanPage": lost.scan_page,
                        "status": "failed",
                        "renderFile": str(lost.render_file),
                        "error": f"worker unavailable: {gone[-1]}",
                    }
                )
            print(f"ERROR worker unavailable: {gone[-1]}", file=sys.stderr)
        elif left:
            for later in left:
                deferred_pages.append(later.scan_page)
                page_results.append(
                    {
                        "scanPage": later.scan_page,
                        "status": "deferred",
                        "renderFile": str(later.render_file),
                    }
                )
            during = f" during page {min(expired_on):04d}" if expired_on else ""
            print(
                f"  time budget reached{during}: {len(deferred_pages)} page(s) left "
                "for the next call",
                flush=True,
            )
    finally:
        try:
            every = list(workers or ([worker] if worker is not None else []))
            every += [extra for _, extra in spare if extra not in every]
            for reader in every:
                try:
                    reader.close()
                except Exception:  # noqa: BLE001 - the others are still to be closed
                    pass
        finally:
            try:
                if mlx_server is not None:
                    mlx_server.close()
            finally:
                if lease is not None:
                    lease.close()
    if worker_trouble and log_used:
        tail = log_tail(worker_log)
        print(f"  worker log {worker_log}" + (f", last {len(tail)} line(s):" if tail else ": empty"),
              file=sys.stderr)
        for line in tail:
            # Quoted, so a log line is never read as this runner's own ERROR line.
            print(f"    | {line}", file=sys.stderr)

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
        "execution": execution,
        "workerLog": str(worker_log) if log_used else None,
        "workers": {"asked": str(arguments.workers), "started": len(workers), "lost": len(gone) if workers else 0,
                    "aloneAfter": took_over},
        "counts": {
            "requested": len(pages),
            "completed": len(completed_pages),
            "skippedCurrent": len(skipped_pages),
            "blocked": len(blocked_pages),
            "failed": len(failed_pages),
            "deferred": len(deferred_pages),
            "legacy": len(legacy_pages),
        },
        "draftIdentity": draft_identity(current_profile),
        "draftIdentitySha256": profile_sha256(draft_identity(current_profile)),
        "pages": page_results,
    }
    if model_ready_at is not None:
        report["modelReadyAt"] = round(model_ready_at, 3)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report, pretty=True))

    print(
        f"  requested {len(pages)} | completed {len(completed_pages)} | "
        f"skipped {len(skipped_pages)} | blocked {len(blocked_pages)} | "
        f"failed {len(failed_pages)} | deferred {len(deferred_pages)}",
        flush=True,
    )
    if legacy_pages:
        print(f"  {len(legacy_pages)} skipped page(s) are legacy drafts (no draftIdentity); "
              "see the report", flush=True)
    print(f"  report: {report_path}", flush=True)
    if local_model_deferred:
        print(LOCAL_MODEL_SUMMARY, flush=True)

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
