#!/usr/bin/env python3
"""Run the whole model-off test suite: the unit tests and the regression checks.

Two kinds of test live in tests/:

    test_*.py    unittest modules, run with unittest discovery;
    check_*.py   standalone regression checks, each a script that exits 0
                 when every check in it passes.

Each file runs in its own subprocess from the repository root, with at most
--timeout seconds (default 900), PROOFREAD_ENDPOINT pointed at a port nothing
listens on (so no request reaches a model server), PYTHONDONTWRITEBYTECODE=1,
none of the variables that say which model the calling agent is or
proofreads with (AGENT_MODEL_VARIABLES: under Qwen Code, say, its shell's
QWEN_CODE_MODEL would make the scripts take the agent for the local model),
none of the machine's GPU hand-over settings (OCR_PHASE_*), and no inherited
packet event sink from a live run.
No GPU and no model are needed.  The script prints PASS or FAIL per file, then a
summary, and exits 1 when any file fails.

    python3 tests/run_all.py                      # everything
    python3 tests/run_all.py --only unit          # only the test_*.py modules
    python3 tests/run_all.py --only checks        # only the check_*.py scripts
    python3 tests/run_all.py stitch footnotes     # files whose name holds a filter
    python3 tests/run_all.py 'check_repair_*'     # or matches a glob
    python3 tests/run_all.py --log-dir /tmp/logs  # keep each file's output

Without --log-dir, the last lines of a failing file's output are printed
under its FAIL line.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

# The discard port, which only root may open: nothing answers there.
CLOSED_ENDPOINT = "http://127.0.0.1:9/v1/chat/completions"
DEFAULT_TIMEOUT = 900
# Left out of each file's environment: the suite runs the same under any agent.
AGENT_MODEL_VARIABLES = ("QWEN_CODE_MODEL", "QWEN_CODE_MODEL_IDENTITY", "JYUT_OCR_AGENT_IS_LOCAL_MODEL",
                         "PROOFREAD_MODEL")
# Also left out: the machine's GPU hand-over (a shell that sourced env.sh has
# them).  With OCR_PHASE_RELEASE_CMD set the runners defer outside a hand-over
# window, and no test may run the machine's real release or restore command.
HANDOVER_PREFIX = "OCR_PHASE_"
PACKET_EVENT_VARIABLES = ("JYUT_OCR_PACKET_EVENTS", "JYUT_OCR_PACKET_CASE")
TAIL_LINES = 30

UNIT_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
UNIT_FAILED = re.compile(r"^FAILED \(([^)]*)\)", re.M)
UNIT_SKIPPED = re.compile(r"skipped=(\d+)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filters", nargs="*", metavar="FILTER",
                        help="run only files whose name contains FILTER or matches it as a glob")
    parser.add_argument("--only", choices=("unit", "checks"),
                        help="unit: only tests/test_*.py; checks: only tests/check_*.py")
    parser.add_argument("--log-dir", type=Path, metavar="DIR",
                        help="write each file's full output to DIR/<file>.log")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                        help=f"time limit per file (default {DEFAULT_TIMEOUT})")
    return parser


def selected(name: str, filters: list[str]) -> bool:
    if not filters:
        return True
    for pattern in filters:
        if any(c in pattern for c in "*?["):
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name, pattern + ".py"):
                return True
        elif pattern in name:
            return True
    return False


def collect(only: str | None, filters: list[str]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    if only in (None, "unit"):
        files += [("unit", path) for path in sorted(TESTS.glob("test_*.py"))]
    if only in (None, "checks"):
        files += [("check", path) for path in sorted(TESTS.glob("check_*.py"))]
    return [(kind, path) for kind, path in files if selected(path.name, filters)]


def command(kind: str, path: Path) -> list[str]:
    if kind == "unit":
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", path.name]
    return [sys.executable, str(path.relative_to(ROOT))]


def unit_count(output: str) -> int:
    ran = UNIT_RAN.findall(output)
    return int(ran[-1]) if ran else 0


def child_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """ENVIRON (default os.environ) as one test file runs with it."""
    env = {key: value for key, value in (os.environ if environ is None else environ).items()
           if key not in AGENT_MODEL_VARIABLES and key not in PACKET_EVENT_VARIABLES
           and not key.startswith(HANDOVER_PREFIX)}
    env.update(PROOFREAD_ENDPOINT=CLOSED_ENDPOINT, PYTHONDONTWRITEBYTECODE="1")
    return env


def run_one(kind: str, path: Path, timeout: int) -> tuple[bool, str, str, float]:
    """Run one file; return (passed, note, output, seconds)."""
    env = child_environment()
    started = time.monotonic()
    process = subprocess.Popen(command(kind, path), cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        raw, _ = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        # The whole process group, so a check's own children go too.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raw, _ = process.communicate()
        timed_out = True
    seconds = time.monotonic() - started
    output = raw.decode("utf-8", errors="replace")
    if timed_out:
        return False, f"timed out after {timeout} s", output, seconds
    passed = process.returncode == 0
    note = "" if passed else f"exit {process.returncode}"
    if kind == "unit":
        note = f"{unit_count(output)} tests" + (f", {note}" if note else "")
        failed = UNIT_FAILED.findall(output)
        if failed:
            note += f" ({failed[-1]})"
        else:
            skipped = UNIT_SKIPPED.findall(output)
            if skipped:
                note += f" (skipped={skipped[-1]})"
    return passed, note, output, seconds


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = collect(args.only, args.filters)
    if not files:
        print("no test file matches", file=sys.stderr)
        return 2
    if args.log_dir:
        args.log_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, Path, bool, str]] = []
    tests_ran = 0
    started = time.monotonic()
    for kind, path in files:
        passed, note, output, seconds = run_one(kind, path, args.timeout)
        if kind == "unit":
            tests_ran += unit_count(output)
        results.append((kind, path, passed, note))
        line = f"{'PASS' if passed else 'FAIL'} tests/{path.name} ({seconds:.1f} s{', ' + note if note else ''})"
        print(line, flush=True)
        if args.log_dir:
            (args.log_dir / f"{path.name}.log").write_text(output, encoding="utf-8")
        elif not passed:
            for tail in output.rstrip("\n").splitlines()[-TAIL_LINES:]:
                print(f"    {tail}")
    failed = [(kind, path, note) for kind, path, passed, note in results if not passed]
    print()
    for kind, label in (("unit", "unit test files"), ("check", "check scripts")):
        group = [passed for k, _, passed, _ in results if k == kind]
        if group:
            extra = f" ({tests_ran} tests)" if kind == "unit" else ""
            print(f"{label}: {sum(group)} passed, {len(group) - sum(group)} failed{extra}")
    if failed:
        print("failed: " + ", ".join(f"tests/{path.name}" for _, path, _ in failed))
    if args.log_dir:
        print(f"logs: {args.log_dir}")
    print(f"{'FAILED' if failed else 'OK'} in {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
