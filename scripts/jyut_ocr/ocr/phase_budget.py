"""Optional deadline and child-process cleanup for sequential local OCR drafts.

OCR_PHASE_DEADLINE is an optional absolute time.time() deadline. With no value,
runners process all selected pages. OCR_PHASE_PROGRESS optionally names a file
to append sealed scan numbers to. Exit 75 means resumable pages remain, including
queue wait expiry before loading; repeat the same command to resume sealed pairs.

OCR_PHASE_STARTUP_SECONDS optionally gives the time a runner needs to load its
model and seal one page (ocr_book.py measures it).  A fresh Budget's first
question - every runner asks it once, just before it loads the model - then
answers no unless that much time is left, so an engine started late in a
shared slice defers (exit 75) instead of loading for nothing and failing.
OCR_PHASE_CHILDREN optionally names a file, beside the progress file, where
detached OCR processes (the shared Hunyuan broker and its server) are written
as "KIND PID START" lines, so the hand-over can wait for them and stop them.

This helper never controls a model service: scripts/run_ocr_phase.py does the
GPU hand-over.  Only children started by a runner are closed on exit; Linux
parent-death signals prevent orphan workers.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

DEADLINE_ENV = "OCR_PHASE_DEADLINE"
PROGRESS_ENV = "OCR_PHASE_PROGRESS"
STARTUP_ENV = "OCR_PHASE_STARTUP_SECONDS"
CHILDREN_ENV = "OCR_PHASE_CHILDREN"
# EX_TEMPFAIL: not finished, nothing wrong - run the same command again.
EXIT_INCOMPLETE = 75


class StartupExpired(Exception):
    """The phase deadline came while the OCR model was still loading: no
    queue wait, so running the same call again with the same budget would
    end the same way.  The runner defers its pages and exits 1, naming the
    times, rather than 75."""

    def __init__(self, message: str, waited: float):
        super().__init__(message)
        self.waited = waited


class Budget:
    def __init__(self) -> None:
        raw = os.environ.get(DEADLINE_ENV, "").strip()
        try:
            self.deadline: float | None = float(raw) if raw else None
        except ValueError:
            self.deadline = None
        self.progress_file = os.environ.get(PROGRESS_ENV) or None
        self.longest = 0.0
        try:
            startup = float(os.environ.get(STARTUP_ENV, "").strip() or 0.0)
        except ValueError:
            startup = 0.0
        self.startup = startup if startup > 0 and startup != float("inf") else 0.0
        self._started = False

    def remaining(self) -> float | None:
        """Seconds left before the deadline (may be negative), or None without one."""
        return None if self.deadline is None else self.deadline - time.time()

    def allows_next_page(self) -> bool:
        """Whether there is time for one more page.

        The first answer of a fresh budget is the one asked before the model
        loads (each runner asks it there, once, before taking the GPU), so it
        also needs the startup time OCR_PHASE_STARTUP_SECONDS names."""
        left = self.remaining()
        need = self.longest
        if not self._started:
            self._started = True
            need = max(need, self.startup)
        return left is None or left > need

    def page_timeout(self, own_limit: float) -> tuple[float, bool]:
        """The limit for the next page, and whether the budget (not the runner) set it."""
        left = self.remaining()
        if left is not None and left < own_limit:
            return max(left, 0.0), True
        return own_limit, False

    def record(self, seconds: float) -> None:
        self.longest = max(self.longest, seconds)

    def sealed(self, scan_page: int) -> None:
        if not self.progress_file:
            return
        try:
            with open(self.progress_file, "a", encoding="utf-8") as handle:
                handle.write(f"{scan_page}\n")
        except OSError:
            pass


def process_start_time(pid: int) -> str | None:
    """When PID started, as a token that tells it from a later process given
    the same number (Linux: the kernel's start tick; elsewhere ps's lstart),
    or None when no such process exists."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                return handle.read().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    started = " ".join(result.stdout.split())
    return started if result.returncode == 0 and started else None


def record_child(kind: str, pid: int) -> None:
    """Name a detached OCR process in OCR_PHASE_CHILDREN, when it is set."""
    path = os.environ.get(CHILDREN_ENV)
    started = process_start_time(pid) if path else None
    if not path or started is None:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{kind} {pid} {started.replace(' ', '_')}\n")
    except OSError:
        pass


def read_children(path: str | os.PathLike | None) -> list[tuple[str, int, str]]:
    """The (kind, pid, start) lines record_child wrote to PATH."""
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    children = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and parts[1].isdigit():
            children.append((parts[0], int(parts[1]), parts[2].replace("_", " ")))
    return children


def exit_on_sigterm() -> None:
    """Make SIGTERM/SIGHUP raise SystemExit so the runner's finally closes its GPU child."""
    def handler(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def die_with_parent():
    """On Linux, fail closed unless the owned child is tied to this parent."""
    if not sys.platform.startswith("linux"):
        return None
    expected_parent = os.getpid()

    def preexec() -> None:
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
                os._exit(127)
            # The parent could have died before prctl installed the signal.
            if os.getppid() != expected_parent:
                os._exit(127)
        except Exception:
            os._exit(127)
    return preexec


def parent_bound_command(command: list[str]) -> list[str]:
    """Set parent-death handling after exec, safe for a multithreaded broker.

    preexec_fn runs between fork and exec and can inherit locked Python state.
    This small fresh interpreter installs the signal, checks the parent race,
    then execs the backend, preserving the explicitly inherited lease FDs.
    """
    if not sys.platform.startswith("linux"):
        return command
    return [sys.executable, os.path.abspath(__file__), "--exec-parent-bound",
            str(os.getpid()), *command]


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "--exec-parent-bound":
        raise SystemExit("internal parent-bound launcher requires a parent PID and command")
    import ctypes
    expected_parent = int(sys.argv[2])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != expected_parent:
        os._exit(127)
    os.execv(sys.argv[3], sys.argv[3:])
