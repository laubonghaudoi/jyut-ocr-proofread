"""Optional deadline and child-process cleanup for sequential local OCR drafts.

OCR_PHASE_DEADLINE is an optional absolute time.time() deadline. With no value,
runners process all selected pages. OCR_PHASE_PROGRESS optionally names a file
to append sealed scan numbers to. Exit 75 means resumable pages remain, including
queue wait expiry before loading; repeat the same command to resume sealed pairs.

This helper never controls an existing model service. Only children started by
a runner are closed on exit; Linux parent-death signals prevent orphan workers.
"""
from __future__ import annotations

import os
import signal
import sys
import time

DEADLINE_ENV = "OCR_PHASE_DEADLINE"
PROGRESS_ENV = "OCR_PHASE_PROGRESS"
# EX_TEMPFAIL: not finished, nothing wrong - run the same command again.
EXIT_INCOMPLETE = 75


class Budget:
    def __init__(self) -> None:
        raw = os.environ.get(DEADLINE_ENV, "").strip()
        try:
            self.deadline: float | None = float(raw) if raw else None
        except ValueError:
            self.deadline = None
        self.progress_file = os.environ.get(PROGRESS_ENV) or None
        self.longest = 0.0

    def remaining(self) -> float | None:
        """Seconds left before the deadline (may be negative), or None without one."""
        return None if self.deadline is None else self.deadline - time.time()

    def allows_next_page(self) -> bool:
        left = self.remaining()
        return left is None or left > self.longest

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
