"""Same-user Hunyuan page broker, shared by independent OCR runner processes.

Only the broker owns the GPU lease and llama child. Each runner keeps a lifetime
membership socket, with separate bounded page sockets for its worker threads.
EOF removes a membership, so killing the first runner does not kill another
runner's work. The final disconnect drains the owned child before releasing the
GPU. Different configurations use different brokers and the same FIFO lease.

The private Unix socket is the discovery boundary: we never discover or attach
to an arbitrary HTTP model service. IPC carries local filenames, not image
bodies; images are read only when a scheduler slot is available.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from _gpu_queue import GPUQueue, GPUQueueError, GPUQueueTimeout

PROTOCOL = 1
MAX_MESSAGE = 32 * 1024 * 1024
MAX_COMMAND = 32 * 1024
MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 256
MAX_CONNECTIONS = 1024
IDLE_GRACE = 0.5
FIRST_JOIN_GRACE = 10.0
DISCOVERY_TIMEOUT = 30.0


class SharedHunyuanError(RuntimeError):
    """One shared OCR page or connection failed; no draft may be sealed."""


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise GPUQueueError(f"shared OCR directory must be private to this user: {path}")


def _private_file(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        os.close(fd)
        raise GPUQueueError(f"shared OCR file must be private to this user: {path}")
    return fd


def _positive(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise GPUQueueError(f"shared OCR {name} must be an integer in 1..{maximum}")
    return value


def _seconds(value: Any, name: str, *, optional: bool = False) -> float | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise GPUQueueError(f"shared OCR {name} must be a finite number >= 0")
    return float(value)


def _config(value: dict) -> dict:
    if not isinstance(value, dict):
        raise GPUQueueError("shared OCR configuration must be an object")
    allowed = {"binary", "model", "mmproj", "model_id", "ctx", "slots", "identity", "max_tokens", "device"}
    unknown = set(value) - allowed
    if unknown:
        raise GPUQueueError(f"unknown shared OCR settings: {sorted(unknown)}")
    out = {}
    for key in ("binary", "model", "mmproj"):
        try:
            path = Path(value[key]).expanduser().resolve(strict=True)
        except (KeyError, TypeError, OSError, ValueError) as error:
            raise GPUQueueError(f"invalid shared OCR {key} path") from error
        if not path.is_file() or (key == "binary" and not os.access(path, os.X_OK)):
            raise GPUQueueError(f"shared OCR {key} is not a usable file: {path}")
        out[key] = str(path)
    for key, maximum in (("ctx", 1048576), ("slots", 64)):
        out[key] = _positive(value.get(key), key, maximum)
    if "max_tokens" in value:
        out["max_tokens"] = _positive(value["max_tokens"], "max_tokens", 1048576)
    device = value.get("device", "auto")
    if device not in ("auto", "cpu", "cuda", "metal"):
        raise GPUQueueError("shared OCR device must be auto, cpu, cuda or metal")
    out["device"] = device
    model_id = value.get("model_id", "hunyuanocr")
    identity = value.get("identity")
    if (not isinstance(model_id, str) or not model_id or len(model_id) > 256
            or any(ord(char) < 32 for char in model_id)):
        raise GPUQueueError("invalid shared OCR model ID")
    if not isinstance(identity, str) or not identity or len(identity) > 1024 or not identity.isascii():
        raise GPUQueueError("shared OCR requires an explicit tool/profile identity")
    out.update(model_id=model_id, identity=identity)
    return out


def _identity(config: dict, root: Path, resource: str) -> dict:
    # These runtime selectors can change the effective GPU without changing the
    # executable. Do not serialize arbitrary environment variables or secrets.
    runtime = {key: os.environ.get(key) for key in
               ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
                "GGML_METAL_DEVICES", "LLAMA_ARG_DEVICE", "MTMD_BACKEND_DEVICE")}
    code = hashlib.sha256()
    for filename in ("_hunyuan_shared.py", "_gpu_queue.py", "_phase_budget.py", "run_hunyuanocr.py"):
        code.update(Path(__file__).with_name(filename).read_bytes())
    body = {"protocol": PROTOCOL, "config": config, "queue_root": str(root),
            "resource": resource, "runtime": runtime, "code": code.hexdigest()}
    body["digest"] = hashlib.sha256(_json(body)).hexdigest()
    return body


def _paths(body: dict) -> tuple[Path, Path]:
    # AF_UNIX sockaddr paths are short on Linux; long home/project paths must
    # not silently disable sharing. This directory and every child are 0700.
    runtime_root = Path("/tmp") / f"jyut-ocr-{os.getuid()}"
    _private_directory(runtime_root)
    partition = hashlib.sha256(_json([body["queue_root"], body["resource"]])).hexdigest()[:20]
    directory = runtime_root / partition
    _private_directory(directory)
    return directory, directory / (body["digest"][:32] + ".sock")


def _send(connection: socket.socket, payload: dict) -> None:
    encoded = _json(payload)
    if len(encoded) > MAX_MESSAGE:
        raise GPUQueueError("shared OCR response exceeds IPC limit")
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def _recv_exact(connection: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        part = connection.recv(count - len(data))
        if not part:
            raise EOFError("shared OCR connection closed")
        data.extend(part)
    return bytes(data)


def _recv(connection: socket.socket, maximum: int = MAX_MESSAGE) -> dict:
    length, = struct.unpack("!I", _recv_exact(connection, 4))
    if not 0 < length <= maximum:
        raise GPUQueueError("invalid shared OCR message size")
    payload = json.loads(_recv_exact(connection, length))
    if not isinstance(payload, dict):
        raise GPUQueueError("shared OCR message is not an object")
    return payload


def _disconnect(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    connection.close()


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


class SharedHunyuanClient:
    """One document membership; ``ocr`` is safe for bounded worker threads."""

    def __init__(self, config: dict, queue_dir: Path | str | None = None,
                 resource: str = "local-gpu", queue_timeout: float | None = None,
                 phase_deadline: float | None = None):
        self.config = _config(config)
        queue_timeout = _seconds(queue_timeout, "queue timeout", optional=True)
        phase_deadline = _seconds(phase_deadline, "phase deadline", optional=True)
        queue = GPUQueue(queue_dir, resource)
        # Canonicalize lexical aliases to avoid two discovery sockets for one
        # physical queue. GPUQueue itself retains its normal lease validation.
        self.body = _identity(self.config, queue.root.resolve(), resource)
        self.directory, self.socket_path = _paths(self.body)
        self._lock = threading.Lock()
        self._pages: set[socket.socket] = set()
        self._membership: socket.socket | None = None
        self._launched: subprocess.Popen | None = None
        self._closed = False
        self.session: str | None = None
        self._report = {"mode": "shared-local-fifo", "resource": resource,
                        "configurationSha256": self.body["digest"],
                        "device": self.config["device"],
                        "slots": self.config["slots"], "status": "waiting"}
        started = time.monotonic()
        deadlines = []
        if queue_timeout is not None:
            # Try-once still permits one short local IPC handshake, but never
            # creates or waits for a model server.
            deadlines.append(started + (0.25 if queue_timeout == 0 else queue_timeout))
        if phase_deadline is not None:
            deadlines.append(started + max(0.0, phase_deadline - time.time()))
        deadline = min(deadlines) if deadlines else None
        try:
            self._join(deadline, try_once=queue_timeout == 0)
        except BaseException:
            self.close()
            raise
        finally:
            self._report["waitSeconds"] = round(time.monotonic() - started, 3)

    @property
    def report(self) -> dict:
        with self._lock:
            return dict(self._report)

    def _check(self, deadline: float | None) -> None:
        if _expired(deadline):
            raise GPUQueueTimeout("shared Hunyuan queue/startup wait deadline reached")

    def _connect(self) -> socket.socket:
        info = self.socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GPUQueueError("shared OCR socket is not a private same-user socket")
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(0.25)
        try:
            connection.connect(str(self.socket_path))
            if hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise GPUQueueError("shared OCR broker belongs to another user")
            return connection
        except BaseException:
            connection.close()
            raise

    def _launch_if_needed(self, deadline: float | None) -> None:
        # A failing broker can release its owner fd before the interpreter
        # exits. Keep the first child so _join can report that failure instead
        # of replacing it with another process during that shutdown window.
        if self._launched is not None:
            return
        prefix = self.body["digest"][:32]
        guard = _private_file(self.directory / (prefix + ".launch"), os.O_RDWR | os.O_CREAT)
        owner = None
        try:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self._check(deadline)
            owner = _private_file(self.directory / (prefix + ".owner"), os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            # The inherited owner fd makes spawn-before-bind a single-owner
            # state. A launcher killed after Popen cannot spawn a second daemon.
            config_path = self.directory / (prefix + ".json")
            fd = _private_file(config_path, os.O_RDWR | os.O_CREAT)
            try:
                os.ftruncate(fd, 0)
                os.write(fd, _json(self.body))
                os.fsync(fd)
            finally:
                os.close(fd)
            log = _private_file(self.directory / (prefix + ".log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                child = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--broker", str(config_path),
                     "--owner-fd", str(owner)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, close_fds=True, pass_fds=(owner,))
                self._launched = child
                # Popen is intentionally not retained as a runner-owned child.
                # Reap it in a daemon thread if this runner happens to outlive it.
                threading.Thread(target=child.wait, daemon=True).start()
            finally:
                os.close(log)
        finally:
            if owner is not None:
                os.close(owner)  # Never LOCK_UN an fd inherited by the broker.
            os.close(guard)

    def _join(self, deadline: float | None, *, try_once: bool) -> None:
        last_notice = 0.0
        discovery_started = time.monotonic()
        eof_retries = 0
        while True:
            self._check(deadline)
            if self._launched is not None and self._launched.poll() is not None:
                log = self.directory / (self.body["digest"][:32] + ".log")
                raise GPUQueueError(f"shared Hunyuan broker exited before admission (code {self._launched.returncode}); inspect {log}")
            if time.monotonic() - discovery_started >= DISCOVERY_TIMEOUT:
                log = self.directory / (self.body["digest"][:32] + ".log")
                raise GPUQueueError(f"shared Hunyuan broker did not accept a membership within {DISCOVERY_TIMEOUT:.0f}s; inspect {log}")
            try:
                connection = self._connect()
            except (FileNotFoundError, ConnectionRefusedError):
                if try_once:
                    raise GPUQueueTimeout("no ready shared Hunyuan broker")
                self._launch_if_needed(deadline)
                time.sleep(0.05)
                continue
            try:
                remaining = None if deadline is None else max(0.001, deadline - time.monotonic())
                # Admission must answer promptly. Only the post-admission GPU
                # wait may be unlimited; a wedged pre-bind daemon cannot hang a
                # session forever when queue_timeout was omitted.
                connection.settimeout(min(5.0, remaining) if remaining is not None else 5.0)
                _send(connection, {"op": "join", "digest": self.body["digest"], "ready_only": try_once})
                response = _recv(connection)
                if response.get("status") == "retry":
                    _disconnect(connection)
                    if try_once:
                        raise GPUQueueTimeout(response.get("error", "shared broker is draining"))
                    if time.monotonic() - last_notice >= 30:
                        print("[hunyuan-shared] waiting for current GPU batch to drain", flush=True)
                        last_notice = time.monotonic()
                    discovery_started = time.monotonic()
                    time.sleep(0.1)
                    continue
                if response.get("status") != "joined":
                    raise GPUQueueError(response.get("error", "invalid shared OCR admission"))
                self._membership = connection
                self.session = response["session"]
                self._report.update(brokerPid=response["brokerPid"], session=self.session,
                                    logFile=str(self.directory / (self.body["digest"][:32] + ".log")))
                remaining = None if deadline is None else max(0.001, deadline - time.monotonic())
                connection.settimeout(remaining)
                response = _recv(connection)
                if response.get("status") != "ready":
                    raise GPUQueueError(response.get("error", "shared OCR startup failed"))
                if response.get("digest") != self.body["digest"]:
                    raise GPUQueueError("shared OCR configuration identity mismatch")
                self._report.update(status="ready", serverPid=response["serverPid"],
                                    queue=response["queue"])
                connection.settimeout(None)
                return
            except (socket.timeout, TimeoutError) as error:
                _disconnect(connection)
                raise GPUQueueTimeout("shared Hunyuan queue/startup wait deadline reached") from error
            except (EOFError, ConnectionResetError, BrokenPipeError) as error:
                _disconnect(connection)
                self._membership = None
                self.session = None
                if not try_once and eof_retries < 3:
                    # Last-owner shutdown can race a new join. No page has
                    # been submitted yet, so retrying this admission is safe.
                    eof_retries += 1
                    time.sleep(0.05)
                    continue
                raise GPUQueueError(f"shared Hunyuan broker connection failed: {error}") from error
            except (OSError, ValueError) as error:
                _disconnect(connection)
                raise GPUQueueError(f"shared Hunyuan broker connection failed: {error}") from error
            except BaseException:
                _disconnect(connection)
                raise

    def ocr(self, render: Path, max_tokens: int, timeout: float) -> dict[str, Any]:
        _positive(max_tokens, "max_tokens", 1048576)
        timeout = _seconds(timeout, "page timeout")
        started = time.monotonic()
        connection = None
        try:
            with self._lock:
                if self._closed or not self.session:
                    raise SharedHunyuanError("shared OCR membership is closed")
                connection = self._connect()
                self._pages.add(connection)
            connection.settimeout(max(0.001, timeout - (time.monotonic() - started)))
            _send(connection, {"op": "ocr", "digest": self.body["digest"], "session": self.session,
                               "render": str(Path(render).resolve()), "max_tokens": max_tokens,
                               "deadline": time.time() + max(0.0, timeout - (time.monotonic() - started))})
            response = _recv(connection)
            if response.get("status") != "complete" or not isinstance(response.get("payload"), dict):
                raise SharedHunyuanError(response.get("error", "shared OCR page failed"))
            return response["payload"]
        except (OSError, EOFError, ValueError, GPUQueueError) as error:
            raise SharedHunyuanError(f"shared OCR request failed: {error}") from error
        finally:
            if connection is not None:
                with self._lock:
                    self._pages.discard(connection)
                _disconnect(connection)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connections = list(self._pages)
            self._pages.clear()
            if self._membership is not None:
                connections.append(self._membership)
                self._membership = None
            self._report["status"] = "membership-closed"
        for connection in connections:
            _disconnect(connection)


@dataclass
class _Request:
    session: str
    render: Path
    max_tokens: int
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    response: dict | None = None


class _Broker:
    def __init__(self, body: dict, directory: Path, socket_path: Path):
        self.body = body
        self.config = body["config"]
        self.directory = directory
        self.socket_path = socket_path
        self.queue = GPUQueue(body["queue_root"], body["resource"])
        self.condition = threading.Condition()
        self.cancel = threading.Event()
        self.server = None
        self.members: dict[str, socket.socket] = {}
        self.pending: dict[str, deque[_Request]] = {}
        self.request_counts: dict[str, int] = {}
        self.turns: deque[str] = deque()
        self.ready = False
        self.draining = False
        self.error: str | None = None
        self.last_empty = time.monotonic()
        self.ever_joined = False
        self.connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.listener: socket.socket | None = None
        self.stopped = threading.Event()

    def _admit(self, connection: socket.socket, request: dict) -> str | None:
        with self.condition:
            if self.draining or self.cancel.is_set() or self.error or (
                    self.queue.acquired and self.queue.has_waiters()):
                # Latch drain admission shut when another backend is waiting.
                # New arrivals cannot keep an old model resident indefinitely.
                self.draining = True
                _send(connection, {"status": "retry", "error": "GPU batch is draining for another queued backend"})
                return None
            if request.get("ready_only") and not self.ready:
                _send(connection, {"status": "retry", "error": "shared OCR server is not ready"})
                return None
            if len(self.members) >= MAX_MEMBERS:
                _send(connection, {"status": "error", "error": "shared OCR membership limit reached"})
                return None
            session = uuid.uuid4().hex
            self.members[session] = connection
            self.request_counts[session] = 0
            self.ever_joined = True
            self.condition.notify_all()
            return session

    def _membership(self, connection: socket.socket, request: dict) -> None:
        session = self._admit(connection, request)
        if session is None:
            return
        ready_sent = False
        try:
            _send(connection, {"status": "joined", "session": session, "brokerPid": os.getpid()})
            while not self.stopped.is_set():
                with self.condition:
                    if self.error:
                        _send(connection, {"status": "error", "error": self.error})
                        return
                    if self.ready and not ready_sent:
                        _send(connection, {"status": "ready", "digest": self.body["digest"],
                                           "serverPid": self.server.process.pid,
                                           "queue": self.queue.report})
                        ready_sent = True
                readable, _, _ = select.select([connection], [], [], 0.1)
                if readable:
                    # Membership is a lifetime channel, not a command stream.
                    connection.recv(1)
                    return
        finally:
            with self.condition:
                self.members.pop(session, None)
                for pending in self.pending.pop(session, ()):
                    pending.cancelled = True
                    pending.event.set()
                self.turns = deque(item for item in self.turns if item != session)
                if not self.members:
                    self.last_empty = time.monotonic()
                self.condition.notify_all()

    def _page(self, connection: socket.socket, command: dict) -> None:
        session = command.get("session")
        deadline = _seconds(command.get("deadline"), "request deadline")
        max_tokens = _positive(command.get("max_tokens"), "max_tokens", 1048576)
        if "max_tokens" in self.config and max_tokens != self.config["max_tokens"]:
            raise GPUQueueError("page token limit differs from shared configuration")
        path = Path(command.get("render", ""))
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            raise GPUQueueError("OCR render must be an absolute local file within the image size limit")
        request = _Request(session, path, max_tokens, deadline)
        with self.condition:
            if session not in self.members or not self.ready or self.cancel.is_set():
                raise GPUQueueError("shared OCR membership is not active")
            if self.request_counts[session] >= self.config["slots"]:
                raise GPUQueueError("too many outstanding pages for one shared OCR membership")
            self.request_counts[session] += 1
            if session not in self.pending:
                self.pending[session] = deque()
                self.turns.append(session)
            self.pending[session].append(request)
            self.condition.notify_all()
        try:
            while not request.event.wait(0.05):
                if time.time() >= deadline:
                    request.cancelled = True
                    _send(connection, {"status": "error", "error": "shared OCR page deadline reached"})
                    return
                readable, _, _ = select.select([connection], [], [], 0)
                if readable:
                    # Peer EOF (or unexpected extra bytes) cancels queued work.
                    connection.recv(1)
                    request.cancelled = True
                    return
            if request.response is not None:
                _send(connection, request.response)
            elif not request.cancelled:
                _send(connection, {"status": "error", "error": "shared OCR stopped before page completed"})
        finally:
            with self.condition:
                queued = self.pending.get(session)
                if queued is not None and request in queued:
                    queued.remove(request)
                    request.cancelled = True
                    request.event.set()
                    if not queued:
                        del self.pending[session]
                        self.turns = deque(item for item in self.turns if item != session)
                self.request_counts[session] = max(0, self.request_counts.get(session, 1) - 1)

    def _connection(self, connection: socket.socket) -> None:
        try:
            if hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise GPUQueueError("cross-user shared OCR connection rejected")
            connection.settimeout(10)
            command = _recv(connection, MAX_COMMAND)
            if command.get("digest") != self.body["digest"]:
                raise GPUQueueError("shared OCR configuration identity mismatch")
            if command.get("op") == "join":
                self._membership(connection, command)
            elif command.get("op") == "ocr":
                self._page(connection, command)
            else:
                raise GPUQueueError("unknown shared OCR operation")
        except (OSError, EOFError, ValueError, TypeError, GPUQueueError) as error:
            try:
                _send(connection, {"status": "error", "error": str(error)})
            except (OSError, GPUQueueError):
                pass
        finally:
            _disconnect(connection)
            self.connections.release()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self.connections.acquire(blocking=False):
                _disconnect(connection)
                continue
            threading.Thread(target=self._connection, args=(connection,), daemon=True).start()

    def _watch_members(self) -> None:
        while not self.stopped.wait(0.05):
            with self.condition:
                grace = IDLE_GRACE if self.ever_joined else FIRST_JOIN_GRACE
                if not self.members and time.monotonic() - self.last_empty >= grace:
                    self.draining = True
                    self.cancel.set()
                    self.condition.notify_all()
                    return

    def _worker(self) -> None:
        while True:
            with self.condition:
                while not self.turns and not self.cancel.is_set():
                    self.condition.wait(0.1)
                if self.cancel.is_set():
                    return
                session = self.turns.popleft()
                requests = self.pending[session]
                request = requests.popleft()
                if requests:
                    self.turns.append(session)
                else:
                    del self.pending[session]
                active_member = session in self.members
            try:
                if request.cancelled or not active_member:
                    continue
                left = request.deadline - time.time()
                if left <= 0:
                    raise TimeoutError("shared OCR page deadline reached before its GPU slot")
                payload = self.server.ocr(request.render, request.max_tokens, left)
                request.response = {"status": "complete", "payload": payload}
            except Exception as error:
                request.response = {"status": "error", "error": str(error)}
            finally:
                request.event.set()

    def run(self) -> None:
        from run_hunyuanocr import HunyuanServer
        from _phase_budget import exit_on_sigterm
        exit_on_sigterm()
        workers = []
        try:
            if self.socket_path.exists() or self.socket_path.is_symlink():
                info = self.socket_path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise GPUQueueError("refusing to replace unexpected shared OCR socket path")
                self.socket_path.unlink()
            self.listener = socket.socket(socket.AF_UNIX)
            self.listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            self.listener.listen(256)
            self.listener.settimeout(0.2)
            threading.Thread(target=self._accept, daemon=True).start()
            threading.Thread(target=self._watch_members, daemon=True).start()
            with self.condition:
                while not self.members and not self.cancel.is_set():
                    self.condition.wait(0.1)
            if self.cancel.is_set():
                return
            self.queue.acquire(cancel_event=self.cancel)
            if self.cancel.is_set():
                return
            with self.condition:
                # A queue lease can become available between a client's EOF
                # and its membership thread's next poll. Check sockets here,
                # synchronously, before spending GPU memory on a new child.
                for session, connection in list(self.members.items()):
                    try:
                        readable, _, _ = select.select([connection], [], [], 0)
                        if readable:
                            connection.recv(1, socket.MSG_PEEK)
                            self.members.pop(session, None)
                    except (OSError, ValueError):
                        self.members.pop(session, None)
                if not self.members:
                    self.draining = True
                    self.cancel.set()
                    return
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = self.config
            print(f"[hunyuan-shared] starting broker={os.getpid()} slots={config['slots']} config={self.body['digest']}", flush=True)
            self.server = HunyuanServer(
                None, Path(config["binary"]), Path(config["model"]), Path(config["mmproj"]),
                port, config["ctx"] * config["slots"], config["model_id"],
                lease_fds=self.queue.pass_fds, parallel=config["slots"],
                device=config["device"],
                log_path=self.directory / (self.body["digest"][:32] + ".llama.log"),
                cancel_event=self.cancel)
            with self.condition:
                self.ready = True
                self.condition.notify_all()
            for _ in range(config["slots"]):
                worker = threading.Thread(target=self._worker, daemon=True)
                worker.start()
                workers.append(worker)
            while not self.cancel.wait(0.1):
                if self.server.process.poll() is not None:
                    raise GPUQueueError(f"shared llama-server exited with code {self.server.process.returncode}")
        except BaseException as error:
            self.error = f"shared Hunyuan broker failed: {error}; inspect {self.directory / (self.body['digest'][:32] + '.log')}"
            print(self.error, flush=True)
            # Give joined waiters a chance to receive the concrete failure.
            with self.condition:
                self.condition.notify_all()
            time.sleep(0.15)
        finally:
            self.cancel.set()
            # Owned child must exit before this process closes its lease fd.
            if self.server is not None:
                self.server.close()
            self.queue.close()
            self.stopped.set()
            with self.condition:
                for requests in self.pending.values():
                    for request in requests:
                        request.event.set()
                self.condition.notify_all()
                memberships = list(self.members.values())
            for connection in memberships:
                _disconnect(connection)
            if self.listener is not None:
                self.listener.close()
            try:
                self.socket_path.unlink(missing_ok=True)
            except OSError:
                pass
            for worker in workers:
                worker.join(timeout=0.2)


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--owner-fd", type=int, required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    fd = _private_file(arguments.broker, os.O_RDONLY)
    try:
        with os.fdopen(fd) as handle:
            body = json.load(handle)
        supplied_digest = body.pop("digest")
        if hashlib.sha256(_json(body)).hexdigest() != supplied_digest:
            raise GPUQueueError("shared OCR launch configuration digest mismatch")
        body["digest"] = supplied_digest
        config = _config(body["config"])
        if body != _identity(config, Path(body["queue_root"]), body["resource"]):
            raise GPUQueueError("shared OCR runtime changed before broker startup")
        directory, socket_path = _paths(body)
        if arguments.broker.resolve() != directory / (body["digest"][:32] + ".json"):
            raise GPUQueueError("invalid shared OCR launch configuration location")
        # Verify the inherited fd still names our private owner inode. It must
        # remain held until socket removal and GPU child cleanup are complete.
        expected = (directory / (body["digest"][:32] + ".owner")).stat()
        actual = os.fstat(arguments.owner_fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise GPUQueueError("invalid shared OCR owner descriptor")
        _Broker(body, directory, socket_path).run()
        return 0
    finally:
        os.close(arguments.owner_fd)


if __name__ == "__main__":
    raise SystemExit(_main())
