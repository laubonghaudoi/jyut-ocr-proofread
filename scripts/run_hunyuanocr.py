#!/usr/bin/env python3
"""Produce HunyuanOCR drafts for agent-led visual proofreading on CPU/CUDA/Metal.

Local runners share one bounded llama.cpp server across sessions by default.
Dedicated FIFO batches and explicit externally managed endpoints are optional.
JSON/TXT completion pairs record the render, tools, settings and returned text;
raw model responses are retained in JSON. A finished page stays current while
its render, model, runtime and output-affecting request settings are
unchanged (see draft_identity). A reply cut off before its natural end (finish_reason other
than "stop", e.g. max tokens) is kept as the draft and marked truncated.
The model's whitespace/reading order, variant forms, punctuation and omitted
furniture still require full-page review.
Hunyuan emits whole-page text, not per-line coordinates or glyph confidence.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
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
import threading
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _common  # noqa: E402
from _common import sha256_bytes, sha256_file, utc_now  # noqa: E402
from _ocr_runner_common import (  # noqa: E402
    PAGE_TOKEN,
    ConfigurationError,
    PageInput,
    PageOCRFailure,
    output_paths,
    parse_pages_expression,
    path_is_within,
    profile_sha256,
    render_path,
    rendered_pages,
    selected_pages,
    text_from_lines,
)

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

# Drafts written before draftIdentity was recorded carry only a hash of their
# whole profile.  The older runner's profile had exactly these keys (and, for a
# while, "emitsSentencePunctuation": False); it sent none of repetition penalty,
# seed or cache_prompt, so the server defaults applied.  llama.cpp's default
# repetition penalty is 1.0 (off), although that profile said 1.08 for a time.
LEGACY_PROFILE_KEYS = (
    "engine", "prompt", "recognitionLevel", "usesLanguageCorrection", "temperature",
    "topP", "repetitionPenalty", "readingOrder", "boundingBoxConvention",
)
LEGACY_UNSENT_KEYS = frozenset({"seed", "cachePrompt"})
LEGACY_RECORDED_PENALTY = 1.08

READY_TIMEOUT = 600.0
# A server this runner started that dies is started again (HunyuanServer.recover): at most
# MAX_RESTARTS times within RESTART_WINDOW seconds; CRASH_WAIT: how long a failed request waits
# for a server that stopped answering to exit before it counts as gone.
MAX_RESTARTS = 3
RESTART_WINDOW = 600.0
CRASH_WAIT = 15.0
# Inflight stall watchdog: if llama-server makes zero completions for this many
# seconds while at least one request is inflight, assume a GPU/CUDA-graph
# livelock even if /v1/models still responds 200, and force a restart.
DEFAULT_STALL_TIMEOUT = 600.0
STALL_TIMEOUT_ENV = "OCR_HUNYUAN_STALL_TIMEOUT"
# llama.cpp's CUDA graphs: a speed-up that replays the recorded GPU steps of the last page.  On
# M1 (2026-11-20, llama.cpp 391fac1) one server reading pages of very different sizes
# (MH1408's 5306x7643 renders beside MH1442's 1410x2079) aborted with them on ("CUDA error: an
# illegal instruction was encountered") and read the same pages cleanly with them off, about
# 15-20% slower.  So a server starts with them on, and one that died starts again with them off.
NO_GRAPHS = "GGML_CUDA_DISABLE_GRAPHS"
DEVICE_CHOICES = ("auto", "cpu", "cuda", "metal")
DEVICE_ENVIRONMENT = (
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
    "GGML_METAL_DEVICES", "LLAMA_ARG_DEVICE", "MTMD_BACKEND_DEVICE",
)


# What the server keeps in system memory between requests, which OCR never uses: every page
# is its own request with its own image, and the runner asks for no prompt cache.  llama.cpp's
# server still keeps a prompt cache (--cache-ram, 8192 MiB unless told) and context checkpoints
# for each slot (--ctx-checkpoints, 32 a slot unless told).  Measured 2026-11-17 on one book:
# 4 slots, 40 pages - the server's resident memory peaked at 10.7 GB with them and 2.6 GB
# without, the 40 drafts the same and the time the same; 16 slots, 96 pages - 4.4 GB without.
# With them a 32-slot server reached 13.6 GB and the machine ran out of memory.
SERVER_MEMORY_ARGUMENTS = (("--cache-ram", "0"), ("--ctx-checkpoints", "0"))
_SERVER_HELP: dict[tuple[str, float], str] = {}


def server_memory_arguments(binary: Path) -> list[str]:
    """SERVER_MEMORY_ARGUMENTS, each only where BINARY's own --help names the option: a
    llama-server older than the option would refuse to start on it."""
    try:
        key = (str(binary), binary.stat().st_mtime)
    except OSError:
        return []
    if key not in _SERVER_HELP:
        try:
            shown = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=20,
                                   stdin=subprocess.DEVNULL)
            _SERVER_HELP[key] = shown.stdout + shown.stderr
        except (OSError, subprocess.SubprocessError):
            _SERVER_HELP[key] = ""
    listed = _SERVER_HELP[key]
    return [word for option, value in SERVER_MEMORY_ARGUMENTS if option in listed for word in (option, value)]


@dataclass(frozen=True)
class ToolIdentity:
    runner_file: Path
    model_sha256: str | None
    mmproj_sha256: str | None
    binary_sha256: str | None
    combined_sha256: str


@dataclass(frozen=True)
class ResumeCheck:
    current: bool
    reason: str
    legacy: str | None = None
    truncated: bool = False
    finish_reason: str | None = None


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    return _common.canonical_json_bytes(value, pretty=pretty, trailing_newline=False)


def draft_identity(profile: Mapping[str, Any]) -> dict[str, Any]:
    """What decides whether a finished page is still current.

    The model (weights and projector, or an external endpoint's model ID and
    declared revision), the runtime that ran it (the llama-server binary; an
    external endpoint's is its owner's) and the request settings that shape
    the reply: the fixed OCR_PROFILE (prompt, sampling) and max tokens.  The
    deployment (server mode, slots, context, client workers, device),
    helper-code and runner hashes and the render's absolute path are recorded
    in the draft but not part of this identity.
    """
    deployment = profile.get("deployment") or {}
    runtime = None
    if deployment.get("mode") == "external-endpoint":
        model = {"endpointModel": deployment.get("modelId"),
                 "endpointRevision": deployment.get("revision")}
    else:
        model = {"modelSha256": deployment.get("modelSha256"),
                 "mmprojSha256": deployment.get("mmprojSha256")}
        # The user's decision of 2026-11-14: the draft identity covers the
        # runtime, so drafts of two llama.cpp builds are not mixed in a book.
        runtime = {"binarySha256": deployment.get("binarySha256")}
    settings = {key: profile.get(key) for key in OCR_PROFILE}
    settings["maxTokens"] = profile.get("maxTokens")
    return {"engine": ENGINE, "model": model, **({"runtime": runtime} if runtime else {}),
            "settings": settings}


def legacy_profile_hashes(profile: Mapping[str, Any]) -> frozenset[str]:
    """Whole-profile hashes an older runner wrote for the same prompt and
    sampling as PROFILE; empty when PROFILE asks for something that runner
    could not have sent."""
    extra = set(OCR_PROFILE) - set(LEGACY_PROFILE_KEYS) - LEGACY_UNSENT_KEYS
    # Seed and cache_prompt were left to the server; that is equivalent only
    # for greedy decoding.
    if extra or profile.get("temperature") != 0.0:
        return frozenset()
    base = {key: profile.get(key) for key in LEGACY_PROFILE_KEYS}
    variants = [base]
    if base["repetitionPenalty"] == 1.0:
        variants.append(dict(base, repetitionPenalty=LEGACY_RECORDED_PENALTY))
    variants += [dict(variant, emitsSentencePunctuation=False) for variant in variants]
    return frozenset(profile_sha256(variant) for variant in variants)


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
                 cancel_event=None, device: str = "auto", ready_deadline: float | None = None,
                 stall_timeout: float | None = None):
        """READY_DEADLINE: the phase deadline (time.time()); a server still
        loading then raises StartupExpired instead of waiting out READY_TIMEOUT."""
        self.process: subprocess.Popen[bytes] | None = None
        # A server this object started and that died (llama.cpp aborting on a CUDA error) is
        # started again, under the same GPU lease, and the pages it lost are sent again: see
        # recover().  generation counts the starts; restarts keeps when each one was.
        self.generation = 0
        self.restarts: list[float] = []
        self._restart_lock = threading.Lock()
        self._extra_env: dict[str, str] = {}   # set for the starts after a crash (NO_GRAPHS)
        if stall_timeout is None:
            raw_stall = (os.environ.get(STALL_TIMEOUT_ENV) or "").strip()
            if raw_stall:
                try:
                    stall_timeout = float(raw_stall)
                except ValueError as error:
                    raise ConfigurationError(f"{STALL_TIMEOUT_ENV} must be numeric (got {raw_stall!r})") from error
            else:
                stall_timeout = DEFAULT_STALL_TIMEOUT
        self.stall_timeout = max(0.0, float(stall_timeout))
        self._inflight_lock = threading.Lock()
        self._inflight_count = 0
        self._last_progress = time.monotonic()
        self._stop_watch = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self.port = port
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
        self._command = [str(binary), "--model", str(model), "--mmproj", str(mmproj),
                         "--host", "127.0.0.1", "--port", str(port), "--alias", model_id,
                         "--ctx-size", str(ctx), "--parallel", str(parallel),
                         "--no-kv-unified", "--cont-batching", "--no-cache-prompt",
                         *server_memory_arguments(binary), *device_arguments(device)]
        self._lease_fds, self._log_path, self._cancel_event = lease_fds, log_path, cancel_event
        self._ready_deadline = ready_deadline
        self._start()
        if self.stall_timeout > 0:
            self._watch_thread = threading.Thread(target=self._watch_stall, daemon=True)
            self._watch_thread.start()

    def _enter_inflight(self) -> None:
        lock = getattr(self, "_inflight_lock", None)
        if lock is None:
            return
        with lock:
            if self._inflight_count == 0:
                self._last_progress = time.monotonic()
            self._inflight_count += 1

    def _leave_inflight(self) -> None:
        lock = getattr(self, "_inflight_lock", None)
        if lock is None:
            return
        with lock:
            self._inflight_count = max(0, self._inflight_count - 1)
            self._last_progress = time.monotonic()

    def is_stalled(self, now: float | None = None) -> bool:
        """Whether at least one request is inflight and no request has finished for stall_timeout."""
        if self.stall_timeout <= 0 or self.process is None or self.process.poll() is not None:
            return False
        current = time.monotonic() if now is None else now
        with self._inflight_lock:
            return self._inflight_count > 0 and (current - self._last_progress) >= self.stall_timeout

    def _watch_stall(self) -> None:
        interval = min(1.0, max(0.05, self.stall_timeout / 4.0))
        while not self._stop_watch.wait(interval):
            generation = self.generation
            if self.is_stalled():
                if not self.recover(generation, force=True) and self.generation == generation and self.is_stalled():
                    break

    def _start(self) -> None:
        try:
            log_handle = self._log_path.open("ab") if self._log_path else None
            try:
                self.process = subprocess.Popen(
                    parent_bound_command(self._command), stdout=log_handle or subprocess.DEVNULL,
                    stderr=log_handle or subprocess.DEVNULL,
                    pass_fds=self._lease_fds,
                    env={**os.environ, **self._extra_env} if self._extra_env else None)
            finally:
                if log_handle is not None:
                    log_handle.close()
            began = time.monotonic()
            deadline = began + READY_TIMEOUT
            ready_deadline, cancel_event = self._ready_deadline, self._cancel_event
            capped = ready_deadline is not None and began + max(0.0, ready_deadline - time.time()) < deadline
            if capped:
                deadline = began + max(0.0, ready_deadline - time.time())
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise ConfigurationError("server startup cancelled")
                if self.process.poll() is not None:
                    raise ConfigurationError(
                        f"llama-server exited with code {self.process.returncode}; "
                        "check the configured runtime and available GPU memory")
                if self._healthy() and self.process.poll() is None:
                    with self._inflight_lock:
                        self._last_progress = time.monotonic()
                    return
                time.sleep(max(0.0, min(0.2, deadline - time.monotonic())))
            waited = time.monotonic() - began
            if capped:
                raise StartupExpired(f"llama-server was still loading at the phase deadline, "
                                     f"{waited:.0f}s after it started", waited)
            raise ConfigurationError(f"llama-server did not become ready in {READY_TIMEOUT:.0f}s")
        except BaseException:
            self._stop_process()
            raise

    def recover(self, generation: int, *, force: bool = False) -> bool:
        """After a request to the server of start GENERATION failed: True when the server runs
        again and the page is worth sending once more, False when the failure was the page's
        own (the server still answers) or the server is not started again.

        Only a server this object started is started again: llama.cpp b10969 on M1's H200s
        sometimes aborted in its image encoder under load ("CUDA error: an illegal instruction
        was encountered", 2026-11-20, two nodes; the same pages read cleanly alone), and every
        page left on that server failed with it.  The first start after a crash turns llama.cpp's
        CUDA graphs off (NO_GRAPHS), which that crash needed, for the rest of this server's life.
        MAX_RESTARTS starts within RESTART_WINDOW seconds and it stays down: a server that cannot
        stay up is not restarted for ever.  With force=True (inflight stall watchdog), restart a
        stalled process even when /v1/models still responds 200."""
        if self.process is None:
            return False
        with self._restart_lock:
            if self.generation != generation:
                return self.process.poll() is None   # another request started it again
            if not force and self.process.poll() is None:
                if self._healthy():
                    return False
                # An aborting llama.cpp drops its connections before the process is gone.
                end = time.monotonic() + CRASH_WAIT
                while self.process.poll() is None and time.monotonic() < end:
                    time.sleep(0.1)
                if self.process.poll() is None and self._healthy():
                    return False
            elif force and self.process.poll() is None and not self.is_stalled():
                return False
            now = time.monotonic()
            recent = [t for t in self.restarts if now - t < RESTART_WINDOW]
            code = self.process.poll()
            why = ("exited with code " + str(code) if code is not None
                   else f"stalled with inflight request(s) for >= {self.stall_timeout:.1f}s" if force
                   else "stopped answering")
            if len(recent) >= MAX_RESTARTS or (self._cancel_event is not None and self._cancel_event.is_set()):
                print(f"[hunyuan] llama-server {why}; "
                      f"{len(recent)} restarts in the last {RESTART_WINDOW:.0f}s, not starting it again", flush=True)
                return False
            graphs_off = "" if NO_GRAPHS in os.environ or NO_GRAPHS in self._extra_env else \
                f", with CUDA graphs off ({NO_GRAPHS}=1)"
            self._extra_env[NO_GRAPHS] = "1"   # llama.cpp reads any value as off
            print(f"[hunyuan] llama-server {why}; "
                  f"starting it again{graphs_off} (restart {len(recent) + 1} of {MAX_RESTARTS} allowed in "
                  f"{RESTART_WINDOW:.0f}s)", flush=True)
            self._stop_process()
            self.restarts.append(now)
            try:
                with socket.socket() as probe:   # never start on a port someone else took meanwhile
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(("127.0.0.1", self.port))
            except OSError as error:
                print(f"[hunyuan] llama-server's port {self.port} is taken ({error}); not starting it again", flush=True)
                return False
            try:
                self._start()
            except (ConfigurationError, StartupExpired) as error:
                print(f"[hunyuan] llama-server did not start again: {error}", flush=True)
                return False
            self.generation += 1
            return True

    def _healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/v1/models", timeout=5) as response:
                payload = json.load(response)
            return any(isinstance(row, dict) and row.get("id") == self.model_id
                       for row in payload.get("data", []))
        except (urllib.error.URLError, OSError, ValueError, TypeError, AttributeError):
            return False

    def ocr(self, render: Path, max_tokens: int, timeout: float) -> dict[str, Any]:
        """The server's reading of RENDER; a server that died on the way is started again
        and the page sent once more (recover), within TIMEOUT."""
        deadline = time.monotonic() + timeout
        while True:
            generation = getattr(self, "generation", 0)
            try:
                return self._ocr_once(render, max_tokens, max(0.001, deadline - time.monotonic()))
            except PageOCRFailure:
                if time.monotonic() >= deadline or not self.recover(generation):
                    raise

    def _ocr_once(self, render: Path, max_tokens: int, timeout: float) -> dict[str, Any]:
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
        self._enter_inflight()
        try:
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode())
            except urllib.error.HTTPError as error:
                raise PageOCRFailure(f"HTTP {error.code}: {error.read().decode(errors='replace')[:200]}") from error
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
                raise PageOCRFailure(f"invalid or unavailable endpoint response: {error}") from error
        finally:
            self._leave_inflight()
        try:
            if payload.get("model") != self.model_id:
                raise PageOCRFailure("response model ID differs from requested model")
            choice = payload["choices"][0]
            # A reply that did not end with "stop" (max tokens, for example) is
            # still the engine's reading so far; the caller keeps it as the
            # draft and marks it truncated.
            text = choice["message"].get("content")
            if not isinstance(text, str) or not text.strip():
                raise PageOCRFailure("empty or non-text output")
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise PageOCRFailure(f"malformed endpoint response: {error}") from error
        return payload

    def _stop_process(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)

    def close(self) -> None:
        self._stop_watch.set()
        thread = self._watch_thread
        if thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._watch_thread = None
        self._stop_process()


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


def finish_reason(raw_response: Mapping[str, Any]) -> str | None:
    reason = raw_response["choices"][0].get("finish_reason")
    return reason if isinstance(reason, str) else None


def build_record(page: PageInput, lines: list[dict[str, Any]], text_bytes: bytes,
                 tool: ToolIdentity, profile: Mapping[str, Any],
                 raw_response: Mapping[str, Any]) -> dict[str, Any]:
    identity = draft_identity(profile)
    finish = finish_reason(raw_response)
    record = {
        "rawResponse": dict(raw_response),
        "rawText": raw_response["choices"][0]["message"]["content"],
        "finishReason": finish,
        "truncated": finish != "stop",
        "draftIdentity": identity,
        # Recorded for audit; only draftIdentity decides currency.
        "ocrProfile": dict(profile),
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
            "draftIdentitySha256": profile_sha256(identity),
            "textSha256": sha256_bytes(text_bytes),
        },
    }
    record["provenance"]["jsonPayloadSha256"] = sha256_bytes(
        canonical_json_bytes(record, pretty=True))
    return record


def completion_is_current(page: PageInput, tool: ToolIdentity | None,
                          profile: Mapping[str, Any]) -> tuple[bool, str]:
    """(current, reason) for PAGE's existing pair; see check_completion.
    TOOL is recorded in each draft but no longer compared."""
    check = check_completion(page, profile)
    return check.current, check.reason


def check_completion(page: PageInput, profile: Mapping[str, Any]) -> ResumeCheck:
    """Whether PAGE's existing pair is a sealed draft of its current render made
    with PROFILE's model, runtime and request settings (draft_identity).

    Drafts written before draftIdentity was recorded are matched by their whole
    profile hash: the current profile exactly, or an older runner's profile with
    the same prompt and sampling (legacy_profile_hashes).  Those are reported
    as legacy.  They may lack the raw response, and their recorded render path
    may point to a folder that has since moved.
    """
    if not (page.json_output.is_file() and page.text_output.is_file()):
        return ResumeCheck(False, "no existing output")
    try:
        raw = page.json_output.read_bytes()
        text_bytes = page.text_output.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return ResumeCheck(False, f"unreadable: {error}")
    if not isinstance(record, dict):
        return ResumeCheck(False, "completion JSON is not an object")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return ResumeCheck(False, "legacy schema")
    if raw != canonical_json_bytes(record, pretty=True):
        return ResumeCheck(False, "not canonical or edited")
    for key, expected in {"status": "complete", "textAuthority": TEXT_AUTHORITY,
                          "scanPage": page.scan_page, "engine": ENGINE}.items():
        if record.get(key) != expected:
            return ResumeCheck(False, f"invalid {key}")
    lines = record.get("lines")
    if not isinstance(lines, list) or any(not isinstance(line, dict) or
            not isinstance(line.get("text"), str) for line in lines):
        return ResumeCheck(False, "invalid line records")
    if text_from_lines(lines) != text_bytes:
        return ResumeCheck(False, "TXT does not match JSON lines")
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return ResumeCheck(False, "provenance is missing")
    for key, value in {"renderSha256": page.render_sha256,
                       "textSha256": sha256_bytes(text_bytes)}.items():
        if provenance.get(key) != value:
            return ResumeCheck(False, f"provenance {key} differs")
    sealed = provenance.get("jsonPayloadSha256")
    if not isinstance(sealed, str):
        return ResumeCheck(False, "payload seal is missing")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed, pretty=True)) != sealed:
        return ResumeCheck(False, "payload seal does not match")
    finish = record.get("finishReason") if isinstance(record.get("finishReason"), str) else None
    truncated = record.get("truncated") is True
    legacy = None
    if "draftIdentitySha256" in provenance:
        if not isinstance(record.get("rawText"), str) or not isinstance(record.get("rawResponse"), dict):
            return ResumeCheck(False, "raw response is missing")
        if provenance["draftIdentitySha256"] != profile_sha256(draft_identity(profile)):
            return ResumeCheck(False, "draft identity (model, runtime or request settings) differs")
    elif provenance.get("ocrProfileSha256") == profile_sha256(profile):
        legacy = "written before draft identity was recorded; same profile"
    elif provenance.get("ocrProfileSha256") in legacy_profile_hashes(profile):
        legacy = "older runner's draft; same prompt and sampling"
    else:
        return ResumeCheck(False, "provenance ocrProfileSha256 differs (a draft without "
                                  "draftIdentity is matched by its whole profile)")
    return ResumeCheck(True, "current", legacy, truncated, finish)


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


from _gpu_queue import (GPUQueue, GPUQueueError, GPUQueueTimeout, LOCAL_MODEL_SUMMARY, LocalModelOwnsGPU,
                        add_queue_arguments)

from _phase_budget import (  # noqa: E402  (scripts/ is sys.path[0])
    EXIT_INCOMPLETE, Budget, StartupExpired, exit_on_sigterm, parent_bound_command)


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
    from _ocr_engines import require_supported_host
    try:
        require_supported_host()
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
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
    skipped = blocked = missing_failed = legacy = truncated = 0

    for scan_page in pages:
        render = render_path(renders, scan_page).resolve()
        json_out, text_out = output_paths(output, scan_page)
        if not render.is_file():
            results.append({"scanPage": scan_page, "status": "failed",
                            "error": "render file is missing"})
            missing_failed += 1
            continue
        page = PageInput(scan_page, render, sha256_file(render), json_out, text_out)
        resume = check_completion(page, profile)
        exists = os.path.lexists(json_out) or os.path.lexists(text_out)
        if resume.current and not arguments.overwrite:
            skipped += 1
            entry: dict[str, Any] = {"scanPage": scan_page, "status": "skipped-current"}
            if resume.legacy:
                legacy += 1
                entry["legacy"] = resume.legacy
            if resume.truncated:
                truncated += 1
                entry.update(truncated=True, finishReason=resume.finish_reason)
            results.append(entry)
            continue
        if exists and not arguments.overwrite:
            blocked += 1
            results.append({"scanPage": scan_page, "status": "blocked",
                            "error": f"existing output rejected: {resume.reason}; use --overwrite"})
            continue
        pending.append(page)

    completed = deferred = 0
    failed = missing_failed
    budget = Budget()
    server: HunyuanServer | None = None
    lease: GPUQueue | None = None
    queue_deferred = local_model_deferred = False
    # The phase deadline came while the model loaded: (seconds it loaded,
    # seconds of the budget left when this run began to load it).
    load_expired: tuple[float, float] | None = None
    # When the server answered with the model loaded (time.time()): the
    # model-ready signal ocr_book.py measures the engine's load by.
    model_ready_at: float | None = None
    try:
        if pending:
            left_at_load = budget.remaining()
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
                                           lease_fds=lease.pass_fds if lease is not None else (),
                                           ready_deadline=budget.deadline)
                model_ready_at = time.time()
            except StartupExpired as error:
                # Out of time while the model loaded (dedicated, or the shared
                # broker's server): not done yet, not failed; and no queue wait,
                # so the summary's ERROR says the budget is too small.
                load_expired = (error.waited, left_at_load or 0.0)
                deferred += len(pending)
                results.extend({"scanPage": page.scan_page, "status": "deferred",
                                "reason": "time budget ran out while the model was loading"} for page in pending)
                print(f"  time budget reached while HunyuanOCR was loading ({error}): {len(pending)} page(s) "
                      "left for the next call", flush=True)
                pending = []
            except GPUQueueTimeout as error:
                queue_deferred = True
                local_model_deferred = isinstance(error, LocalModelOwnsGPU)
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
            entry = {"scanPage": page.scan_page, "status": "completed",
                     "renderSha256": page.render_sha256,
                     "chars": len(text), "seconds": round(seconds, 2),
                     "finishReason": record["finishReason"]}
            note = ""
            if record["truncated"]:
                truncated += 1
                entry["truncated"] = True
                note = f" (TRUNCATED: finish_reason={record['finishReason']}; kept as the draft)"
            results.append(entry)
            print(f"  page {page.scan_page:04d}: {len(text)} chars in {seconds:.2f}s{note}", flush=True)
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
                   "deferred": deferred, "legacy": legacy, "truncated": truncated},
        "draftIdentity": draft_identity(profile),
        "draftIdentitySha256": profile_sha256(draft_identity(profile)),
        "pages": results,
    }
    if model_ready_at is not None:
        report["modelReadyAt"] = round(model_ready_at, 3)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report, pretty=True))
    print(f"  requested {len(pages)} | completed {completed} | skipped {skipped} | "
          f"blocked {blocked} | failed {failed} | deferred {deferred}", flush=True)
    if legacy:
        print(f"  {legacy} skipped page(s) are legacy drafts (no draftIdentity); see the report", flush=True)
    if truncated:
        print(f"  {truncated} page(s) hold a truncated reply (truncated: true in the report)", flush=True)
    print(f"  report: {report_path}", flush=True)
    if local_model_deferred:
        print(LOCAL_MODEL_SUMMARY, flush=True)
    # Pages left over after progress: call again (failed pages are retried then
    # too).  Pages left over with no progress: calling again would do exactly
    # the same, so stop the loop with an error the agent has to read.
    if deferred and queue_deferred and not failed and not blocked:
        return EXIT_INCOMPLETE
    if deferred and completed:
        return EXIT_INCOMPLETE
    if deferred and load_expired is not None:
        waited, left = load_expired
        print(f"ERROR the time budget is too small for HunyuanOCR's model load: {left:.0f}s of it were left "
              f"when the load began, and the server was still loading after {waited:.0f}s, so no page was "
              "sealed. Raise the budget (OCR_PHASE_MAX_SECONDS, below the tool call's time limit; raise both "
              "together) well above the load; the same call with the same budget ends the same way.",
              file=sys.stderr)
        return 1
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
