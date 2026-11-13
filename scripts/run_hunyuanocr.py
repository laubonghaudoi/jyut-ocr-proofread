#!/usr/bin/env python3
"""Produce HunyuanOCR drafts for agent-led visual proofreading on CPU/CUDA/Metal.

Local runners share one bounded llama.cpp server across sessions by default.
Dedicated FIFO batches and explicit externally managed endpoints are optional.
JSON/TXT completion pairs bind the render, tools, settings and returned text;
raw model responses are retained in JSON. The model's whitespace/reading order,
variant forms, punctuation and omitted furniture still require full-page review.
Hunyuan emits whole-page text, not per-line coordinates or glyph confidence.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request

SCHEMA_VERSION = 2
REPORT_SCHEMA_VERSION = 1
ENGINE = "HunyuanOCR"
TEXT_AUTHORITY = "draft-only-not-text-authority"

# The model card's own document-parsing prompt, kept verbatim: it is what the
# card's reported accuracy was measured with.  Of the card's sampling settings
# the requests send temperature and top_p only (see OCR_PROFILE).
DOC_PARSE_PROMPT = (
    "提取文档图片中正文的所有信息用markdown格式表示，其中页眉、页脚部分忽略，"
    "表格用html格式表达，文档中公式用latex格式表示，按照阅读顺序组织进行解析。"
)

OCR_PROFILE: dict[str, Any] = {
    "engine": ENGINE,
    "prompt": DOC_PARSE_PROMPT,
    "recognitionLevel": "vlm-whole-page",
    "usesLanguageCorrection": False,
    "temperature": 0.0,
    "topP": 1.0,
    # Send this explicitly; server defaults must not silently change a draft.
    "repetitionPenalty": 1.0,
    "seed": 1234,
    "cachePrompt": False,
    "readingOrder": "model-resolved-whole-page",
    "boundingBoxConvention": "none-whole-page-only",
}
SENTENCE_MARKS = frozenset("。，、；：？！")

PAGE_TOKEN = re.compile(r"(?P<first>[1-9][0-9]*)(?:-(?P<last>[1-9][0-9]*))?\Z")
READY_TIMEOUT = 600.0
DEVICE_CHOICES = ("auto", "cpu", "cuda", "metal")
DEVICE_ENVIRONMENT = (
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
    "GGML_METAL_DEVICES", "LLAMA_ARG_DEVICE", "MTMD_BACKEND_DEVICE",
)


class ConfigurationError(ValueError):
    """A command-line or filesystem configuration is unsafe or invalid."""


class PageOCRFailure(RuntimeError):
    """The engine rejected one page while the server remains usable."""


@dataclass(frozen=True)
class ToolIdentity:
    runner_file: Path
    model_sha256: str | None
    mmproj_sha256: str | None
    binary_sha256: str | None
    combined_sha256: str


@dataclass(frozen=True)
class PageInput:
    scan_page: int
    render_file: Path
    render_sha256: str
    json_output: Path
    text_output: Path


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
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                          separators=(",", ": "))
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return text.encode("utf-8")


def profile_sha256(profile: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(profile))


def device_profile(device: str, *, endpoint: bool = False) -> dict[str, Any]:
    """Describe the enforced launch policy, without claiming GPU utilization.

    ``auto`` retains llama.cpp's existing device selection. Explicit CUDA uses
    its first visible device (CUDA_VISIBLE_DEVICES can select the physical GPU).
    Hardware/build suitability is checked by the installer's actual OCR probe;
    a runner with an unsupported explicit backend fails at server startup.
    """
    if device not in DEVICE_CHOICES:
        raise ConfigurationError("device must be auto, cpu, cuda or metal")
    if endpoint and device != "auto":
        raise ConfigurationError("--device controls an owned local server; use auto with --endpoint")
    return {
        "requestedDevice": device,
        "effectiveDevice": "external-uncontrolled" if endpoint else "runtime-auto" if device == "auto" else device,
        "decoderGpuLayers": None if endpoint else 0 if device == "cpu" else 99,
        "projectorOffload": None if endpoint else device != "cpu",
        "kvOffload": None if endpoint else device != "cpu",
        "llamaDevices": None if endpoint or device == "auto" else {"cpu": "none", "cuda": "CUDA0", "metal": "MTL0"}[device],
        "runtimeSelectors": {} if endpoint else {key: os.environ.get(key) for key in DEVICE_ENVIRONMENT},
    }


def device_arguments(device: str) -> list[str]:
    profile = device_profile(device)
    arguments = ["--n-gpu-layers", str(profile["decoderGpuLayers"]),
                 "--mmproj-offload" if profile["projectorOffload"] else "--no-mmproj-offload",
                 "--kv-offload" if profile["kvOffload"] else "--no-kv-offload"]
    if profile["llamaDevices"] is not None:
        arguments += ["--device", profile["llamaDevices"],
                      "--mmproj-device", profile["llamaDevices"]]
    return arguments


def render_path(directory: Path, scan_page: int) -> Path:
    return directory / f"page-{scan_page:04d}.png"


def output_paths(directory: Path, scan_page: int) -> tuple[Path, Path]:
    stem = f"page-{scan_page:04d}"
    return directory / f"{stem}.json", directory / f"{stem}.txt"


def path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        candidate.relative_to(directory)
    except ValueError:
        return False
    return True


def text_from_lines(lines: Sequence[Mapping[str, Any]]) -> bytes:
    return ("\n".join(str(line["text"]) for line in lines) + "\n").encode("utf-8")


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


def build_tool_identity(model: Path | None, mmproj: Path | None,
                        binary: Path | None) -> ToolIdentity:
    runner = Path(__file__).resolve()
    hashes = []
    for label, path in (("model", model), ("mmproj", mmproj), ("binary", binary)):
        if path is not None and not path.is_file():
            raise ConfigurationError(f"missing {label} file: {path}")
        hashes.append(sha256_file(path) if path is not None else None)
    combined = hashlib.sha256(runner.read_bytes())
    combined.update(Path(__file__).with_name("_phase_budget.py").read_bytes())
    combined.update(Path(__file__).with_name("_gpu_queue.py").read_bytes())
    combined.update(Path(__file__).with_name("_hunyuan_shared.py").read_bytes())
    combined.update(canonical_json_bytes(hashes))
    return ToolIdentity(runner, *hashes, combined.hexdigest())


class HunyuanServer:
    """An OpenAI-compatible llama.cpp server, started here or already running."""

    def __init__(self, endpoint: str | None, binary: Path | None,
                 model: Path | None, mmproj: Path | None, port: int, ctx: int,
                 model_id: str = "hunyuanocr", lease_fds: tuple[int, ...] = (),
                 parallel: int = 1, log_path: Path | None = None,
                 cancel_event=None, device: str = "auto"):
        self.process: subprocess.Popen[bytes] | None = None
        self.execution_profile = device_profile(device, endpoint=bool(endpoint))
        self.model_id = model_id
        self.base = endpoint.rstrip("/") if endpoint else f"http://127.0.0.1:{port}"
        if endpoint:
            if not self._healthy():
                raise ConfigurationError("endpoint does not advertise the requested model ID")
            return
        if binary is None or not binary.is_file():
            raise ConfigurationError("need --llama-server or HUNYUAN_LLAMA_SERVER")
        if cancel_event is not None and cancel_event.is_set():
            raise ConfigurationError("server startup cancelled")
        # Never attach to, stop or replace another process already on this port.
        try:
            with socket.socket() as probe:
                # A completed batch can leave TIME_WAIT sockets. Match the
                # server's address reuse without taking over a live listener.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise ConfigurationError(f"owned server port {port} is unavailable: {error}") from error
        try:
            command = [str(binary), "--model", str(model), "--mmproj", str(mmproj),
                 "--host", "127.0.0.1", "--port", str(port), "--alias", model_id,
                 "--ctx-size", str(ctx), "--parallel", str(parallel),
                 "--no-kv-unified", "--cont-batching", "--no-cache-prompt",
                 *device_arguments(device)]
            log_handle = log_path.open("ab") if log_path else None
            try:
                self.process = subprocess.Popen(
                    parent_bound_command(command), stdout=log_handle or subprocess.DEVNULL,
                    stderr=log_handle or subprocess.DEVNULL,
                    pass_fds=lease_fds)
            finally:
                if log_handle is not None:
                    log_handle.close()
            deadline = time.monotonic() + READY_TIMEOUT
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise ConfigurationError("server startup cancelled")
                if self.process.poll() is not None:
                    raise ConfigurationError(
                        f"llama-server exited with code {self.process.returncode}; "
                        "check the configured runtime and available GPU memory")
                if self._healthy() and self.process.poll() is None:
                    return
                time.sleep(0.2)
            raise ConfigurationError(f"llama-server did not become ready in {READY_TIMEOUT:.0f}s")
        except BaseException:
            self.close()
            raise

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/v1/models", timeout=5) as response:
                payload = json.load(response)
            return any(isinstance(row, dict) and row.get("id") == self.model_id
                       for row in payload.get("data", []))
        except (urllib.error.URLError, OSError, ValueError, TypeError, AttributeError):
            return False

    def ocr(self, render: Path, max_tokens: int, timeout: float) -> dict[str, Any]:
        b64 = base64.b64encode(render.read_bytes()).decode()
        body = {
            "model": self.model_id, "max_tokens": max_tokens,
            "temperature": OCR_PROFILE["temperature"],
            "top_p": OCR_PROFILE["topP"],
            "repeat_penalty": OCR_PROFILE["repetitionPenalty"],
            "seed": OCR_PROFILE["seed"], "cache_prompt": OCR_PROFILE["cachePrompt"],
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                {"type": "text", "text": DOC_PARSE_PROMPT}]}],
        }
        request = urllib.request.Request(
            self.base + "/v1/chat/completions", json.dumps(body).encode(),
            {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise PageOCRFailure(f"HTTP {error.code}: {error.read().decode(errors='replace')[:200]}") from error
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
            raise PageOCRFailure(f"invalid or unavailable endpoint response: {error}") from error
        try:
            if payload.get("model") != self.model_id:
                raise PageOCRFailure("response model ID differs from requested model")
            choice = payload["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise PageOCRFailure(f"incomplete response: finish_reason={choice.get('finish_reason')}")
            text = choice["message"].get("content")
            if not isinstance(text, str) or not text.strip():
                raise PageOCRFailure("empty or non-text output")
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise PageOCRFailure(f"malformed endpoint response: {error}") from error
        return payload

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)


def lines_from_text(text: str) -> list[dict[str, Any]]:
    """One record per non-empty line. No boxes: this engine reads whole pages."""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        out.append({
            "text": line,
            "confidence": 0.0,          # the engine reports none; do not invent one
            "boundingBox": [0.0, 0.0, 1.0, 1.0],
            "blockLabel": "whole-page",
        })
    return out or [{"text": text.strip(), "confidence": 0.0,
                    "boundingBox": [0.0, 0.0, 1.0, 1.0], "blockLabel": "whole-page"}]


def build_record(page: PageInput, lines: list[dict[str, Any]], text_bytes: bytes,
                 tool: ToolIdentity, profile: Mapping[str, Any],
                 raw_response: Mapping[str, Any]) -> dict[str, Any]:
    record = {
        "rawResponse": dict(raw_response),
        "rawText": raw_response["choices"][0]["message"]["content"],
        "schemaVersion": SCHEMA_VERSION,
        "scanPage": page.scan_page,
        "status": "complete",
        "renderFile": str(page.render_file),
        "generatedAt": utc_now(),
        "engine": profile["engine"],
        "recognitionLevel": profile["recognitionLevel"],
        "usesLanguageCorrection": profile["usesLanguageCorrection"],
        # Measured on this page, not assumed for the engine.
        "sentenceMarks": sum(1 for c in text_bytes.decode("utf-8") if c in SENTENCE_MARKS),
        "textAuthority": TEXT_AUTHORITY,
        "lines": lines,
        "provenance": {
            "renderSha256": page.render_sha256,
            "toolSha256": tool.combined_sha256,
            "ocrProfileSha256": profile_sha256(profile),
            "textSha256": sha256_bytes(text_bytes),
        },
    }
    record["provenance"]["jsonPayloadSha256"] = sha256_bytes(
        canonical_json_bytes(record, pretty=True))
    return record


def completion_is_current(page: PageInput, tool: ToolIdentity,
                          profile: Mapping[str, Any]) -> tuple[bool, str]:
    if not (page.json_output.is_file() and page.text_output.is_file()):
        return False, "no existing output"
    try:
        raw = page.json_output.read_bytes()
        text_bytes = page.text_output.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return False, f"unreadable: {error}"
    if not isinstance(record, dict):
        return False, "completion JSON is not an object"
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return False, "legacy schema"
    if raw != canonical_json_bytes(record, pretty=True):
        return False, "not canonical or edited"
    for key, expected in {"status": "complete", "textAuthority": TEXT_AUTHORITY,
                          "scanPage": page.scan_page, "renderFile": str(page.render_file),
                          "engine": ENGINE}.items():
        if record.get(key) != expected:
            return False, f"invalid {key}"
    lines = record.get("lines")
    if not isinstance(lines, list) or any(not isinstance(line, dict) or
            not isinstance(line.get("text"), str) for line in lines):
        return False, "invalid line records"
    if not isinstance(record.get("rawText"), str) or not isinstance(record.get("rawResponse"), dict):
        return False, "raw response is missing"
    if text_from_lines(lines) != text_bytes:
        return False, "TXT does not match JSON lines"
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return False, "provenance is missing"
    expected = {
        "renderSha256": page.render_sha256,
        "toolSha256": tool.combined_sha256,
        "ocrProfileSha256": profile_sha256(profile),
        "textSha256": sha256_bytes(text_bytes),
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            return False, f"provenance {key} differs"
    sealed = provenance.get("jsonPayloadSha256")
    if not isinstance(sealed, str):
        return False, "payload seal is missing"
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed, pretty=True)) != sealed:
        return False, "payload seal does not match"
    return True, "current"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("render_directory", type=Path)
    p.add_argument("output_directory", type=Path)
    sel = p.add_mutually_exclusive_group(required=False)
    sel.add_argument("--pages", help="comma-separated pages/ranges, e.g. 1,3-5")
    sel.add_argument("--first-page", type=int)
    p.add_argument("--last-page", type=int)
    p.add_argument("--model", type=Path,
                   default=os.environ.get("HUNYUAN_MODEL") or None)
    p.add_argument("--mmproj", type=Path,
                   default=os.environ.get("HUNYUAN_MMPROJ") or None)
    p.add_argument("--llama-server", type=Path,
                   default=os.environ.get("HUNYUAN_LLAMA_SERVER") or None)
    p.add_argument("--endpoint", default=os.environ.get("HUNYUAN_ENDPOINT"),
                   help="use an already-running server instead of starting one")
    p.add_argument("--endpoint-model", help="exact model ID advertised by --endpoint")
    p.add_argument("--endpoint-revision", help="caller-declared deployment revision; change it after endpoint updates")
    p.add_argument("--port", type=int, default=8095)
    p.add_argument("--ctx-size", type=int, default=16384)
    p.add_argument("--device", choices=DEVICE_CHOICES,
                   default=os.environ.get("OCR_HUNYUAN_DEVICE", "auto"),
                   help="local backend: auto keeps runtime selection; cpu disables all GPU offload")
    p.add_argument("--server-mode", choices=("shared", "dedicated"),
                   default=os.environ.get("OCR_HUNYUAN_MODE", "shared"),
                   help="shared cross-session backend (default), or one dedicated FIFO batch")
    p.add_argument("--server-slots", type=int, default=os.environ.get("OCR_HUNYUAN_SLOTS", "1"),
                   help="total simultaneous requests; ctx-size is per slot (default 1)")
    p.add_argument("--workers", type=int,
                   help="this document's outstanding pages; default server-slots, external endpoints default 1")
    p.add_argument("--max-tokens", type=int, default=8000)
    p.add_argument("--timeout", type=float, default=1200.0)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--report", type=Path)
    add_queue_arguments(p)
    return p


from _gpu_queue import GPUQueue, GPUQueueError, GPUQueueTimeout, add_queue_arguments

from _phase_budget import (  # noqa: E402  (scripts/ is sys.path[0])
    EXIT_INCOMPLETE, Budget, exit_on_sigterm, parent_bound_command)


def recognize_pages(pages, server, budget, max_tokens, timeout, workers):
    """Bound the in-flight window; only the caller seals completed outputs."""
    def recognize(page):
        start = time.monotonic()
        limit, capped = budget.page_timeout(timeout)
        if limit <= 0:
            return page, None, "deferred", 0.0, True
        try:
            if sha256_file(page.render_file) != page.render_sha256:
                raise PageOCRFailure("render changed before recognition")
            response = server.ocr(page.render_file, max_tokens, limit)
            return page, response, None, time.monotonic() - start, capped
        except (RuntimeError, OSError) as error:
            return page, None, str(error), time.monotonic() - start, capped

    if workers == 1:
        for page in pages:
            if not budget.allows_next_page():
                yield page, None, "deferred", 0.0, True
            else:
                yield recognize(page)
        return
    pool = ThreadPoolExecutor(max_workers=workers)
    active = set()
    cursor = 0
    try:
        while cursor < len(pages) or active:
            while cursor < len(pages) and len(active) < workers and budget.allows_next_page():
                active.add(pool.submit(recognize, pages[cursor]))
                cursor += 1
            if not active:
                break
            done, active = wait(active, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
        for page in pages[cursor:]:
            yield page, None, "deferred", 0.0, True
    finally:
        # Closing a shared client's sockets cancels only this membership.
        # Do this before joining workers so SIGTERM cannot wait an OCR timeout.
        if active:
            server.close()
        pool.shutdown(wait=True, cancel_futures=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    exit_on_sigterm()
    started_at, started = utc_now(), time.monotonic()
    try:
        renders = arguments.render_directory.expanduser().resolve()
        pages, selection_mode = selected_pages(arguments, renders)
        output = arguments.output_directory.expanduser().resolve()
        if not renders.is_dir():
            raise ConfigurationError(f"render directory does not exist: {renders}")
        output.mkdir(parents=True, exist_ok=True)
        report_path = (arguments.report.expanduser().resolve() if arguments.report
                       else output.parent / "hunyuanocr-run.json")
        if path_is_within(report_path, output):
            raise ConfigurationError("run report must be outside the OCR output directory")
        if path_is_within(report_path, renders):
            raise ConfigurationError("run report must be outside the render directory")
        if not 1 <= arguments.port <= 65535 or arguments.ctx_size <= 0 or arguments.max_tokens <= 0 or not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
            raise ConfigurationError("port, ctx-size, max-tokens and timeout must be positive and valid")
        if arguments.server_mode not in ("shared", "dedicated") or not 1 <= arguments.server_slots <= 32:
            raise ConfigurationError("server-mode must be shared/dedicated; server-slots must be 1..32")
        if arguments.workers is None:
            arguments.workers = 1 if arguments.endpoint else arguments.server_slots
        if not 1 <= arguments.workers <= arguments.server_slots:
            raise ConfigurationError("workers must be 1..server-slots")
        if arguments.endpoint and arguments.workers != 1:
            raise ConfigurationError("external endpoints use one page worker per runner")
        execution = device_profile(arguments.device, endpoint=bool(arguments.endpoint))
        for name in ("model", "mmproj", "llama_server"):
            value = getattr(arguments, name)
            if value is not None:
                setattr(arguments, name, value.expanduser().resolve())
        if arguments.endpoint:
            if not arguments.endpoint_model or not arguments.endpoint_revision:
                raise ConfigurationError("--endpoint needs --endpoint-model and --endpoint-revision")
            if not arguments.endpoint.startswith(("http://", "https://")):
                raise ConfigurationError("--endpoint must be an HTTP(S) server base URL")
            tool = build_tool_identity(None, None, None)
            deployment = {"mode": "external-endpoint", "endpoint": arguments.endpoint.rstrip("/"),
                          "modelId": arguments.endpoint_model, "revision": arguments.endpoint_revision,
                          "identityAssurance": "caller-declared-revision-and-server-reported-id-not-weight-attestation"}
        else:
            if arguments.model is None or arguments.mmproj is None or arguments.llama_server is None:
                raise ConfigurationError("need --model, --mmproj and --llama-server (or HUNYUAN_* environment variables)")
            tool = build_tool_identity(arguments.model, arguments.mmproj, arguments.llama_server)
            deployment = {"mode": "shared-local-server" if arguments.server_mode == "shared" else "owned-local-server", "modelId": "hunyuanocr",
                          "ctxSize": arguments.ctx_size, "modelSha256": tool.model_sha256,
                          "mmprojSha256": tool.mmproj_sha256, "binarySha256": tool.binary_sha256,
                          "serverSlots": arguments.server_slots, "contextPerSlot": arguments.ctx_size,
                          "totalContext": arguments.ctx_size * arguments.server_slots,
                          "clientWorkers": arguments.workers}
        deployment["executionProfile"] = execution
        if report_path in [Path(__file__).resolve(), arguments.model, arguments.mmproj, arguments.llama_server]:
            raise ConfigurationError("report must not overwrite a tool or model file")
        profile = dict(OCR_PROFILE, maxTokens=arguments.max_tokens, deployment=deployment)
    except (ConfigurationError, OSError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    pending: list[PageInput] = []
    skipped = blocked = missing_failed = 0

    for scan_page in pages:
        render = render_path(renders, scan_page).resolve()
        json_out, text_out = output_paths(output, scan_page)
        if not render.is_file():
            results.append({"scanPage": scan_page, "status": "failed",
                            "error": "render file is missing"})
            missing_failed += 1
            continue
        page = PageInput(scan_page, render, sha256_file(render), json_out, text_out)
        current, reason = completion_is_current(page, tool, profile)
        exists = os.path.lexists(json_out) or os.path.lexists(text_out)
        if current and not arguments.overwrite:
            skipped += 1
            results.append({"scanPage": scan_page, "status": "skipped-current"})
            continue
        if exists and not arguments.overwrite:
            blocked += 1
            results.append({"scanPage": scan_page, "status": "blocked",
                            "error": f"existing output rejected: {reason}; use --overwrite"})
            continue
        pending.append(page)

    completed = deferred = 0
    failed = missing_failed
    budget = Budget()
    server: HunyuanServer | None = None
    lease: GPUQueue | None = None
    queue_deferred = False
    try:
        if pending:
            try:
                if not budget.allows_next_page():
                    raise GPUQueueTimeout("phase deadline reached before model loading")
                if not arguments.endpoint and arguments.server_mode == "shared":
                    from _hunyuan_shared import SharedHunyuanClient
                    # workers vary per client; weights, runtime, slot capacity and
                    # actual request profile must agree for a shared backend.
                    shared_profile = dict(profile, deployment={
                        k: v for k, v in deployment.items() if k != "clientWorkers"})
                    server = SharedHunyuanClient(
                        config={"binary": str(arguments.llama_server), "model": str(arguments.model),
                                "mmproj": str(arguments.mmproj), "model_id": deployment["modelId"],
                                "ctx": arguments.ctx_size, "slots": arguments.server_slots,
                                "device": arguments.device,
                                "max_tokens": arguments.max_tokens,
                                "identity": tool.combined_sha256 + profile_sha256(shared_profile)},
                        queue_dir=arguments.gpu_queue_dir, resource=arguments.gpu_resource,
                        queue_timeout=arguments.gpu_queue_timeout, phase_deadline=budget.deadline)
                elif not arguments.endpoint:
                    lease = GPUQueue(arguments.gpu_queue_dir, arguments.gpu_resource)
                    lease.acquire(timeout=arguments.gpu_queue_timeout, phase_deadline=budget.deadline)
                if server is None:
                    server = HunyuanServer(arguments.endpoint, arguments.llama_server,
                                           arguments.model, arguments.mmproj,
                                           arguments.port, arguments.ctx_size * arguments.server_slots,
                                           deployment["modelId"], parallel=arguments.server_slots,
                                           device=arguments.device,
                                           lease_fds=lease.pass_fds if lease is not None else ())
            except GPUQueueTimeout as error:
                queue_deferred = True
                deferred += len(pending)
                results.extend({"scanPage": page.scan_page, "status": "deferred",
                                "reason": str(error)} for page in pending)
                print(f"[gpu-queue] {error}; {len(pending)} page(s) deferred", flush=True)
                pending = []
            except (ConfigurationError, GPUQueueError, RuntimeError, OSError) as error:
                print(f"ERROR {error}", file=sys.stderr)
                for page in pending:
                    failed += 1
                    results.append({"scanPage": page.scan_page, "status": "failed",
                                    "error": str(error)})
                pending = []

        for page, response, page_error, seconds, capped in recognize_pages(
                pending, server, budget, arguments.max_tokens, arguments.timeout, arguments.workers):
            if response is None:
                if page_error == "deferred" or (capped and (budget.remaining() or 0.0) <= 1.0):
                    deferred += 1
                    results.append({"scanPage": page.scan_page, "status": "deferred"})
                else:
                    failed += 1
                    results.append({"scanPage": page.scan_page, "status": "failed", "error": page_error})
                    print(f"  page {page.scan_page:04d}: FAILED {page_error}", file=sys.stderr)
                continue
            # No worker writes outputs: parent serializes atomic completion pairs.
            # Refuse to seal changed render bytes under their preflight identity.
            if sha256_file(page.render_file) != page.render_sha256:
                failed += 1
                results.append({"scanPage": page.scan_page, "status": "failed",
                                "error": "render changed during recognition"})
                continue
            text = response["choices"][0]["message"]["content"]
            lines = lines_from_text(text)
            text_bytes = text_from_lines(lines)
            record = build_record(page, lines, text_bytes, tool, profile, response)
            json_bytes = canonical_json_bytes(record, pretty=True)
            with tempfile.TemporaryDirectory(dir=str(page.json_output.parent)) as staging:
                sj, st = Path(staging) / "p.json", Path(staging) / "p.txt"
                sj.write_bytes(json_bytes)
                st.write_bytes(text_bytes)
                os.replace(st, page.text_output)
                os.replace(sj, page.json_output)
            completed += 1
            budget.record(seconds)
            budget.sealed(page.scan_page)
            results.append({"scanPage": page.scan_page, "status": "completed",
                            "renderSha256": page.render_sha256,
                            "chars": len(text), "seconds": round(seconds, 2)})
            print(f"  page {page.scan_page:04d}: {len(text)} chars in {seconds:.2f}s", flush=True)
    finally:
        try:
            if server is not None:
                server.close()
        finally:
            if lease is not None:
                lease.close()

    results.sort(key=lambda r: r["scanPage"])
    report = {
        "reportSchemaVersion": REPORT_SCHEMA_VERSION,
        "startedAt": started_at, "finishedAt": utc_now(),
        "durationSeconds": round(time.monotonic() - started, 2),
        "selectionMode": selection_mode,
        "gpuQueue": lease.report if lease is not None else server.report if server is not None and hasattr(server, "report") else {
            "mode": "external-owner" if arguments.endpoint else "not-needed", "acquired": False},
        "renderDirectory": str(renders), "outputDirectory": str(output),
        "textAuthority": TEXT_AUTHORITY,
        "tool": {"runner": str(tool.runner_file), "toolSha256": tool.combined_sha256,
                 "modelSha256": tool.model_sha256, "mmprojSha256": tool.mmproj_sha256, "binarySha256": tool.binary_sha256},
        "ocrProfile": profile, "ocrProfileSha256": profile_sha256(profile),
        "counts": {"requested": len(pages), "completed": completed,
                   "skippedCurrent": skipped, "blocked": blocked, "failed": failed,
                   "deferred": deferred},
        "pages": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report, pretty=True))
    print(f"  requested {len(pages)} | completed {completed} | skipped {skipped} | "
          f"blocked {blocked} | failed {failed} | deferred {deferred}", flush=True)
    print(f"  report: {report_path}", flush=True)
    # Pages left over after progress: call again (failed pages are retried then
    # too).  Pages left over with no progress: calling again would do exactly
    # the same, so stop the loop with an error the agent has to read.
    if deferred and queue_deferred and not failed and not blocked:
        return EXIT_INCOMPLETE
    if deferred and completed:
        return EXIT_INCOMPLETE
    if deferred:
        print("ERROR the time budget ran out before a single page was sealed "
              "(starting the server used it up, or this page is stuck). Keep the "
              "budget below the tool call's time limit; raise both together. If one "
              "page never finishes, it is stuck: run it alone to see why.",
              file=sys.stderr)
        return 1
    if failed or blocked:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
