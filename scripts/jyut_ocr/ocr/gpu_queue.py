"""Same-user, cross-session FIFO leases for owned local OCR GPU children.

No daemon, model loading, GPU probing or external-service control. Every owned
backend for the same GPU must use the same directory/resource. The default
resource deliberately serializes all local GPU drafts, including across CUDA
visibility aliases. Explicit external endpoints have their own scheduler.

Tickets are ordered under a stable guard flock and stay locked while live.
Owned children inherit the ticket fd: a killed parent cannot admit another job
while its child still holds the lease. Never explicitly LOCK_UN that shared fd.

A local model can own the GPU from outside the queue (OCR_PHASE_RELEASE_CMD
names how to stop it, in the machine config).  Then a lease is only granted
inside an open hand-over window of scripts/run_ocr_phase.py: the runner's
OCR_PHASE_WINDOW names the window, whose owner holds the window lock in the
hand-over state folder.  Anywhere else acquire() defers (LocalModelOwnsGPU, a
GPUQueueTimeout, so the runners exit 75 with nothing loaded) instead of loading
a model into a full GPU.  OCR_PHASE_SWAP=never turns this off.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from typing import Iterator


class GPUQueueError(Exception):
    """Queue setup or lease state is invalid; do not start a GPU child."""


class GPUQueueTimeout(Exception):
    """No GPU child was started; pending pages can be resumed later."""


class GPUQueueCancelled(GPUQueueError):
    """The shared owner lost its clients before obtaining the GPU."""


class LocalModelOwnsGPU(GPUQueueTimeout):
    """A configured local model holds the GPU and no hand-over window is open."""


TENANT_ENV = "OCR_PHASE_RELEASE_CMD"
WINDOW_ENV = "OCR_PHASE_WINDOW"
SWAP_ENV = "OCR_PHASE_SWAP"
LOCAL_MODEL_MESSAGE = "GPU belongs to the local model; run through run_ocr_phase.py"
# A runner's last word when it deferred for that reason: its exit 75 does not
# mean "run the same command again" here, which would only defer again.
LOCAL_MODEL_SUMMARY = (f"  NOT RUN: {LOCAL_MODEL_MESSAGE}, not the same command again "
                       f"({TENANT_ENV} is set and no hand-over window is open; exit 75)")


def handover_directory() -> Path:
    """The hand-over's private state folder (window lock, status, logs)."""
    explicit = os.environ.get("OCR_PHASE_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser().absolute()
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return (state / "jyut-ocr" / "handover").expanduser().absolute()


def open_window_id(directory: Path | None = None) -> str | None:
    """The ID of the hand-over window that is open now, or None.

    A window is open while its owner holds window.lock; window.json names it.
    A lock nobody holds means the owner is gone, whatever window.json says."""
    directory = handover_directory() if directory is None else directory
    try:
        fd = os.open(directory / "window.lock", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            return None
        try:
            with open(directory / "window.json", encoding="utf-8") as handle:
                window = json.load(handle)
        except (OSError, ValueError):
            return None
        return window.get("id") if isinstance(window, dict) and isinstance(window.get("id"), str) else None
    finally:
        os.close(fd)


def check_local_model() -> None:
    """Raise LocalModelOwnsGPU when a configured local model owns the GPU and
    this process is not inside the open hand-over window."""
    if not os.environ.get(TENANT_ENV, "").strip():
        return
    if os.environ.get(SWAP_ENV, "").strip().lower() == "never":
        return
    window = os.environ.get(WINDOW_ENV, "").strip()
    if window and window == open_window_id():
        return
    raise LocalModelOwnsGPU(LOCAL_MODEL_MESSAGE)


def live_ticket_count(root: Path | str) -> int:
    """Live tickets under every resource of the queue ROOT: jobs that hold or
    wait for a lease there, including a lease a GPU child inherited.  Unlocked
    tickets are reaped on the way.  A guard that stays busy counts as one."""
    root = Path(root)
    try:
        directories = [path for path in root.iterdir()
                       if path.is_dir() and len(path.name) == 64]
    except FileNotFoundError:
        return 0
    total = 0
    for directory in directories:
        probe = GPUQueue._at(directory)
        try:
            with probe._guard(wait_until=time.monotonic() + 1.0):
                total += len(probe._live_tickets())
        except (GPUQueueTimeout, GPUQueueError, OSError):
            total += 1
    return total


def _nonnegative_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return number


def _resource_name(value: str) -> str:
    if not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise argparse.ArgumentTypeError("GPU resource must be a nonempty short name")
    return value


def add_queue_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gpu-queue-dir", type=Path,
                        default=os.environ.get("OCR_GPU_QUEUE_DIR") or None,
                        help="shared same-user queue root (or OCR_GPU_QUEUE_DIR)")
    parser.add_argument("--gpu-resource", type=_resource_name,
                        default=os.environ.get("OCR_GPU_RESOURCE", "local-gpu"),
                        help="shared GPU resource name; do not use a per-book name")
    parser.add_argument("--gpu-queue-timeout", type=_nonnegative_seconds,
                        help="maximum wait seconds; default unlimited, 0 = try once")


def _directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise GPUQueueError(f"queue directory must be owned by this user and not writable by others: {path}")


def _open_file(path: Path, *, exclusive: bool = False) -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    flags |= os.O_CREAT | os.O_EXCL if exclusive else 0
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        raise GPUQueueError(f"queue file must be a private regular file: {path}")
    return fd


class GPUQueue:
    def __init__(self, directory: Path | str | None = None, resource: str = "local-gpu"):
        try:
            self.resource = _resource_name(resource)
        except argparse.ArgumentTypeError as error:
            raise GPUQueueError(str(error)) from error
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        root = Path(directory) if directory is not None else Path(
            os.environ.get("OCR_GPU_QUEUE_DIR") or state / "jyut-ocr/gpu-queue")
        self.root = root.expanduser().absolute()
        self.directory = self.root / hashlib.sha256(resource.encode()).hexdigest()
        self.fd: int | None = None
        self.ticket: Path | None = None
        self.wait_seconds = 0.0
        self.acquired = False
        self.status = "not-requested"

    @classmethod
    def _at(cls, directory: Path) -> GPUQueue:
        """A queue object for an existing resource folder, for inspection only."""
        probe = cls.__new__(cls)
        probe.directory = directory
        return probe

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return (self.fd,) if self.fd is not None and self.acquired else ()

    @property
    def report(self) -> dict:
        return {"mode": "owned-local-fifo", "resource": self.resource,
                "directory": str(self.directory), "status": self.status,
                "acquired": self.acquired, "waitSeconds": round(self.wait_seconds, 3)}

    @contextmanager
    def _guard(self, *, wait_until: float | None = None,
               phase_deadline: float | None = None, try_once: bool = False,
               cancel_event=None) -> Iterator[None]:
        # This inode must never be replaced or removed, even for an empty queue.
        path = self.directory / "guard.lock"
        try:
            fd = _open_file(path, exclusive=True)
        except FileExistsError:
            fd = _open_file(path)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise GPUQueueCancelled("GPU queue acquisition cancelled")
                self._check_deadlines(wait_until, phase_deadline)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if try_once:
                        raise GPUQueueTimeout("GPU queue guard is busy")
                    delay = 0.05
                    if wait_until is not None:
                        delay = min(delay, max(0.0, wait_until - time.monotonic()))
                    if phase_deadline is not None:
                        delay = min(delay, max(0.0, phase_deadline - time.time()))
                    time.sleep(delay)
            self._check_deadlines(wait_until, phase_deadline)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _check_deadlines(wait_until: float | None, phase_deadline: float | None) -> None:
        if phase_deadline is not None and time.time() >= phase_deadline:
            raise GPUQueueTimeout("phase deadline reached while waiting for GPU")
        if wait_until is not None and time.monotonic() >= wait_until:
            raise GPUQueueTimeout("GPU queue wait timeout reached")

    def _live_tickets(self) -> list[Path]:
        """Under the guard, reap unlocked tickets; PID metadata is not authority."""
        live = []
        for path in sorted(self.directory.glob("*.ticket")):
            if len(path.stem) != 20 or not path.stem.isdigit():
                raise GPUQueueError(f"invalid queue ticket: {path}")
            fd = _open_file(path)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    live.append(path)
                else:
                    path.unlink()
            finally:
                os.close(fd)
        return live

    def acquire(self, timeout: float | None = None,
                phase_deadline: float | None = None, cancel_event=None,
                local_model_guard: bool = True) -> GPUQueue:
        """Wait for the lease.  LOCAL_MODEL_GUARD False is for the hand-over
        wrapper itself, which takes the lease before it opens its window."""
        if self.fd is not None or self.status != "not-requested":
            raise GPUQueueError("lease objects cannot be acquired twice")
        if local_model_guard:
            try:
                check_local_model()
            except LocalModelOwnsGPU:
                self.status = "deferred-local-model"
                raise
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise GPUQueueError("queue timeout must be finite and >= 0")
        if phase_deadline is not None and not math.isfinite(phase_deadline):
            raise GPUQueueError("phase deadline must be finite")
        began = time.monotonic()
        wait_until = began + timeout if timeout is not None and timeout > 0 else None
        guard_options = {"wait_until": wait_until, "phase_deadline": phase_deadline,
                         "try_once": timeout == 0, "cancel_event": cancel_event}
        self.status = "waiting"
        try:
            _directory(self.root)
            _directory(self.directory)
            with self._guard(**guard_options):
                live = self._live_tickets()
                sequence = int(live[-1].stem) + 1 if live else 1
                self.ticket = self.directory / f"{sequence:020d}.ticket"
                self.fd = _open_file(self.ticket, exclusive=True)
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.write(self.fd, json.dumps({"pid": os.getpid(), "resource": self.resource,
                                             "createdAt": time.time()}).encode())
            next_notice = 0.0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise GPUQueueCancelled("GPU queue acquisition cancelled")
                self.wait_seconds = time.monotonic() - began
                self._check_deadlines(wait_until, phase_deadline)
                with self._guard(**guard_options):
                    live = self._live_tickets()
                    if live and live[0] == self.ticket:
                        self._check_deadlines(wait_until, phase_deadline)
                        self.acquired = True
                        self.status = "acquired"
                        print(f"[gpu-queue] acquired {self.resource} after {self.wait_seconds:.1f}s", flush=True)
                        return self
                    ahead = live.index(self.ticket) if self.ticket in live else -1
                if ahead < 0:
                    raise GPUQueueError("own queue ticket disappeared")
                if timeout is not None and self.wait_seconds >= timeout:
                    raise GPUQueueTimeout("GPU queue wait timeout reached")
                if self.wait_seconds >= next_notice:
                    print(f"[gpu-queue] waiting for {self.resource}: {ahead} job(s) ahead", flush=True)
                    next_notice = self.wait_seconds + 30.0
                delay = 0.1
                if timeout is not None:
                    delay = min(delay, max(0.0, timeout - self.wait_seconds))
                if phase_deadline is not None:
                    delay = min(delay, max(0.0, phase_deadline - time.time()))
                time.sleep(delay)
        except BaseException as error:
            self.status = "timed-out" if isinstance(error, GPUQueueTimeout) else "cancelled-or-error"
            self.wait_seconds = time.monotonic() - began
            self.close()
            raise

    def has_waiters(self) -> bool:
        """Whether another live backend needs this GPU; used to close admission.

        A shared Hunyuan owner drains its existing clients before yielding to
        Paddle or a different configuration. A busy guard closes admission
        conservatively rather than blocking request handling.
        """
        if self.fd is None or not self.acquired:
            return False
        try:
            with self._guard(try_once=True):
                return any(ticket != self.ticket for ticket in self._live_tickets())
        except GPUQueueTimeout:
            return True

    def close(self) -> None:
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        # No LOCK_UN: an inherited copy must hold the ticket until the GPU child
        # exits. A fresh open below detects that copy and leaves the ticket live.
        os.close(fd)
        live = None
        try:
            with self._guard(try_once=True):
                live = self._live_tickets()
        except GPUQueueTimeout:
            # Cleanup must not wait behind a paused guard owner. The next
            # queue operation will reap this now-unlocked ticket.
            pass
        if self.acquired:
            self.status = ("lease-fd-closed" if live is None else
                           "child-retained" if self.ticket in live else "released")
            print(f"[gpu-queue] {self.status} {self.resource}", flush=True)
