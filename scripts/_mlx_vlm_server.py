"""Owned MLX-VLM service and portable parent-death supervision (stdlib only).

The runner owns the listening port, model and process group.  A private pipe
ties each supervised child to the runner even on macOS, where prctl is absent.
Neither health probes nor cleanup ever adopt another process's service.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

VERSION = "0.8.0"
BACKEND = "mlx-vlm-server"


class MLXError(RuntimeError):
    pass


class MLXDeadline(MLXError):
    pass


PROBE = """import importlib.metadata as m, json
import mlx.core as mx
names = ('mlx-vlm', 'mlx', 'mlx-metal', 'transformers')
def version(name):
    try: return m.version(name)
    except m.PackageNotFoundError: return None
print(json.dumps({'versions': {name: version(name) for name in names},
                  'metalAvailable': bool(mx.metal.is_available()),
                  'defaultDevice': str(mx.default_device())}))
"""


def probe(interpreter: str) -> dict[str, Any]:
    result = subprocess.run([interpreter, "-c", PROBE], capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = {}
    if result.returncode or not isinstance(data, dict) or not data:
        tail = (result.stderr or result.stdout).strip().splitlines()
        raise MLXError("MLX runtime probe failed: " + (tail[-1] if tail else "no valid response"))
    if data.get("versions", {}).get("mlx-vlm") != VERSION:
        raise MLXError(f"MLX runtime requires mlx-vlm=={VERSION}; rerun the installer")
    if not data.get("metalAvailable") or "gpu" not in str(data.get("defaultDevice", "")).lower():
        raise MLXError("MLX requires an available Metal GPU and the GPU default device")
    return data


class SupervisedProcess:
    """A child whose supervisor terminates its entire group on pipe EOF.

    Only the runner holds the writing end.  SIGKILL or a crash closes it in
    the kernel, so no process polling or PID-only ownership inference is used.
    """

    def __init__(self, command: list[str], *, lease_fds: tuple[int, ...] = (), **kwargs: Any):
        read_fd, self.control_fd = os.pipe()
        self.process: subprocess.Popen | None = None
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--supervise", str(read_fd),
                 ",".join(str(fd) for fd in lease_fds), "--", *command],
                pass_fds=(read_fd, *lease_fds), start_new_session=True, **kwargs)
        except BaseException:
            os.close(self.control_fd)
            self.control_fd = -1
            raise
        finally:
            os.close(read_fd)
        from _phase_budget import record_child
        record_child("mlx-owned", self.process.pid)

    def close(self) -> None:
        if self.control_fd >= 0:
            os.close(self.control_fd)
            self.control_fd = -1
        if self.process is not None:
            try:
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                # The supervisor is the process-group leader; this group was
                # created by us and was never attached to a pre-existing server.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MLXServer:
    def __init__(self, interpreter: str, model: Path, log_path: Path, *,
                 lease_fds: tuple[int, ...] = (), ready_timeout: float = 600,
                 budget_capped: bool = False):
        self.owned: SupervisedProcess | None = None
        self.url = ""
        self.model = str(model.resolve())
        self.health: dict[str, Any] = {}
        deadline = time.monotonic() + ready_timeout
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # No environment can make this service fetch another model at runtime.
        environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           TOKENIZERS_PARALLELISM="false")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for attempt in range(3):
                port = free_port()
                self.url = f"http://127.0.0.1:{port}"
                with log_path.open("wb" if attempt == 0 else "ab") as log:
                    attempt_offset = log.tell()
                    self.owned = SupervisedProcess(
                        [interpreter, "-m", "mlx_vlm.server", "--host", "127.0.0.1",
                         "--port", str(port), "--model", self.model], lease_fds=lease_fds,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        env=environment)
                handle = self.owned.process
                assert handle is not None
                while True:
                    if handle.poll() is not None:
                        # Retry only a failed bind, not invalid weights or imports.
                        with log_path.open("rb") as log:
                            log.seek(attempt_offset)
                            detail = log.read().decode("utf-8", "replace")[-8000:]
                        if attempt < 2 and ("address already in use" in detail.lower()
                                            or "errno 48" in detail.lower()
                                            or "errno 98" in detail.lower()):
                            self.owned.close()
                            break
                        raise MLXError(f"MLX server exited {handle.returncode}; see {log_path}")
                    left = deadline - time.monotonic()
                    if left <= 0:
                        error = MLXDeadline if budget_capped else MLXError
                        raise error(f"MLX server not ready after {ready_timeout:.0f}s; see {log_path}")
                    # A free-port reservation has a bind race.  Probe only after
                    # *our* Uvicorn reports its successful bind; otherwise an
                    # unrelated service with the same model could appear ready.
                    with log_path.open("rb") as log:
                        log.seek(attempt_offset)
                        own_log = log.read().decode("utf-8", "replace")
                    bound = f"Uvicorn running on {self.url}" in own_log
                    try:
                        if not bound:
                            raise OSError("owned server has not bound its port")
                        with opener.open(self.url + "/health", timeout=min(1.0, left)) as response:
                            status = json.load(response)
                    except (OSError, ValueError, urllib.error.URLError):
                        status = {}
                    if isinstance(status, dict):
                        if status.get("preload_failures"):
                            raise MLXError(f"MLX preload failed: {status['preload_failures']}; see {log_path}")
                        loaded = status.get("loaded_model")
                        if status.get("model_ready") and loaded:
                            if str(Path(str(loaded)).resolve()) != self.model:
                                raise MLXError(f"MLX server loaded {loaded!r}, not the configured model")
                            if handle.poll() is not None:
                                raise MLXError("MLX server exited during its readiness probe")
                            self.health = status
                            return
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            raise MLXError("MLX server could not bind a private loopback port")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.owned is not None:
            self.owned.close()


def _supervise(control_fd: int, lease_fds: tuple[int, ...], command: list[str]) -> int:
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)
    # The supervised command inherits our stdio, not the liveness pipe.
    child = subprocess.Popen(command, pass_fds=lease_fds)
    try:
        while child.poll() is None and not stopping:
            if select.select([control_fd], [], [], 0.1)[0] and not os.read(control_fd, 1):
                stopping = True
        # Also terminate descendants if the direct child exited first.
        os.killpg(os.getpgrp(), signal.SIGTERM)
        if child.poll() is None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        # A library may have spawned a descendant that ignores SIGTERM even
        # after its direct parent exits.  This group contains only our owned
        # processes.  Include the supervisor in the final kill; the runner
        # reaps it and uses the worker protocol, not this exit code, as success.
        os.killpg(os.getpgrp(), signal.SIGKILL)
        return child.wait()  # unreachable unless a platform rejected SIGKILL
    finally:
        os.close(control_fd)


if __name__ == "__main__":
    if len(sys.argv) < 6 or sys.argv[1] != "--supervise" or sys.argv[4] != "--":
        raise SystemExit("internal owned-process supervisor requires a pipe and command")
    raise SystemExit(_supervise(int(sys.argv[2]), tuple(int(fd) for fd in sys.argv[3].split(",") if fd),
                               sys.argv[5:]))
