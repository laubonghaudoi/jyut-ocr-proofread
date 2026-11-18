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
none of the settings the machine's env.sh holds (the names env.sh.example
gives, PROOFREAD_* and the GPU hand-over's OCR_PHASE_*), and no inherited
packet event sink from a live run: the suite runs the same in any shell.
No GPU and no model are needed.  The script prints PASS or FAIL per file as
each one ends, then a summary, and exits 1 when any file fails.

Files run side by side, --jobs at a time (default: the machine's cores, at
most 8; --jobs 1 runs them one after the other, as before 2026-11-17).  A
unit file named in SPLIT_BY_CLASS runs each of its test classes as a job of
its own, since one such file alone took a third of the suite.  Measured on
16 cores, 96 files: 380 s one after the other; see CHANGELOG.md for the
figure side by side.

    python3 tests/run_all.py                      # everything
    python3 tests/run_all.py --only unit          # only the test_*.py modules
    python3 tests/run_all.py --only checks        # only the check_*.py scripts
    python3 tests/run_all.py stitch footnotes     # files whose name holds a filter
    python3 tests/run_all.py 'check_repair_*'     # or matches a glob
    python3 tests/run_all.py --log-dir /tmp/logs  # keep each file's output
    python3 tests/run_all.py --jobs 1             # one file at a time

Without --log-dir, the last lines of a failing file's output are printed
under its FAIL line.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
DEFAULT_JOBS = min(8, os.cpu_count() or 1)
# Unit files whose test classes each run as a job of their own (the file's name, as in tests/).
# test_handover.py waits real seconds in many tests: 130 s as one job, 40 s as its classes.
SPLIT_BY_CLASS = ("test_handover.py", "test_ocr_batch.py")
# Left out of each file's environment: the suite runs the same under any agent.
AGENT_MODEL_VARIABLES = ("QWEN_CODE_MODEL", "QWEN_CODE_MODEL_IDENTITY", "JYUT_OCR_AGENT_IS_LOCAL_MODEL",
                         "PROOFREAD_MODEL")
# Also left out: the machine's GPU hand-over (a shell that sourced env.sh has
# them).  With OCR_PHASE_RELEASE_CMD set the runners defer outside a hand-over
# window, and no test may run the machine's real release or restore command.
HANDOVER_PREFIX = "OCR_PHASE_"
# Also left out: every setting the machine's env.sh can hold - the names env.sh.example gives,
# set or commented out, and the proofreading ones (PROOFREAD_*).  In a shell that had sourced
# env.sh, a retired engine name failed four files and the machine's device one more (2026-11-17).
SETTINGS_PREFIX = "PROOFREAD_"
SETTING_LINE = re.compile(r"^\s*#?\s*export\s+([A-Z][A-Z0-9_]*)=", re.M)
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
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS, metavar="N",
                        help=f"files run side by side (default {DEFAULT_JOBS}: the cores, at most 8; 1: one at a time)")
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


def command(kind: str, path: Path, test_class: str | None = None) -> list[str]:
    if kind == "unit":
        # -k without a wildcard is a substring of the test's full name, module.Class.method.
        only = ["-k", f"{path.stem}.{test_class}."] if test_class else []
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", path.name, *only]
    return [sys.executable, str(path.relative_to(ROOT))]


CLASSES_OF = """
import sys, unittest
def each(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from each(item)
        else:
            yield item
names = sorted({type(test).__name__ for test in each(unittest.defaultTestLoader.discover("tests", pattern=sys.argv[1]))})
print("\\n".join(names))
"""


def test_classes(path: Path) -> list[str]:
    """The test classes unittest finds in PATH, as the loader names them - so a class that
    only inherits its tests is one of them, and a module that does not load gives none (the
    file then runs whole, and reports its own failure)."""
    found = subprocess.run([sys.executable, "-c", CLASSES_OF, path.name], cwd=ROOT, env=child_environment(),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    names = found.stdout.split()
    return [] if found.returncode or "_FailedTest" in names or "ModuleImportFailure" in names else names


def jobs_of(files: list[tuple[str, Path]]) -> list[tuple[str, Path, str | None]]:
    """FILES as jobs, the largest first (a long file started last would be the whole tail):
    one job a file, or one a test class for a unit file in SPLIT_BY_CLASS."""
    jobs: list[tuple[str, Path, str | None]] = []
    for kind, path in sorted(files, key=lambda item: -item[1].stat().st_size):
        classes = test_classes(path) if kind == "unit" and path.name in SPLIT_BY_CLASS else []
        jobs += [(kind, path, name) for name in classes] or [(kind, path, None)]
    return jobs


def unit_count(output: str) -> int:
    ran = UNIT_RAN.findall(output)
    return int(ran[-1]) if ran else 0


def machine_settings() -> set[str]:
    """The names env.sh.example gives a setting, set or commented out."""
    try:
        return set(SETTING_LINE.findall((ROOT / "env.sh.example").read_text(encoding="utf-8")))
    except OSError:
        return set()


def child_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """ENVIRON (default os.environ) as one test file runs with it."""
    settings = machine_settings()
    env = {key: value for key, value in (os.environ if environ is None else environ).items()
           if key not in AGENT_MODEL_VARIABLES and key not in PACKET_EVENT_VARIABLES
           and key not in settings and not key.startswith((HANDOVER_PREFIX, SETTINGS_PREFIX))}
    env.update(PROOFREAD_ENDPOINT=CLOSED_ENDPOINT, PYTHONDONTWRITEBYTECODE="1")
    return env


def run_one(kind: str, path: Path, timeout: int, test_class: str | None = None) -> tuple[bool, str, str, float]:
    """Run one file, or one test class of it; return (passed, note, output, seconds)."""
    env = child_environment()
    started = time.monotonic()
    process = subprocess.Popen(command(kind, path, test_class), cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
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
    jobs = jobs_of(files)
    parts: dict[Path, list[tuple[str | None, bool, str, str, float]]] = {path: [] for _, path in files}
    wanted = {path: sum(1 for _, other, _ in jobs if other == path) for _, path in files}
    kinds = dict((path, kind) for kind, path in files)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        running = {pool.submit(run_one, kind, path, args.timeout, name): (path, name) for kind, path, name in jobs}
        for future in as_completed(running):
            path, name = running[future]
            parts[path].append((name, *future.result()))
            if len(parts[path]) < wanted[path]:
                continue
            # The file is done: one line for it, as when it ran as one job.
            done = sorted(parts[path], key=lambda part: part[0] or "")
            passed = all(part[1] for part in done)
            seconds = sum(part[4] for part in done)
            kind = kinds[path]
            if kind == "unit":
                count = sum(unit_count(part[3]) for part in done)
                tests_ran += count
                bad = [f"{part[0]}: {part[2]}" if part[0] else part[2] for part in done if not part[1]]
                note = (done[0][2] if len(done) == 1 else
                        f"{count} tests in {len(done)} classes" + (f", {'; '.join(bad)}" if bad else ""))
            else:
                note = done[0][2]
            results.append((kind, path, passed, note))
            print(f"{'PASS' if passed else 'FAIL'} tests/{path.name} ({seconds:.1f} s{', ' + note if note else ''})",
                  flush=True)
            output = "".join((f"=== {part[0]} ===\n" if part[0] else "") + part[3] for part in done)
            if args.log_dir:
                (args.log_dir / f"{path.name}.log").write_text(output, encoding="utf-8")
            elif not passed:
                for part in done:
                    if not part[1]:
                        for tail in part[3].rstrip("\n").splitlines()[-TAIL_LINES:]:
                            print(f"    {tail}")
    results.sort(key=lambda row: (row[0] != "unit", row[1].name))
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
