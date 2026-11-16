#!/usr/bin/env python3
"""Wait quietly for a long step running in the background, then print one line.

Why this exists
---------------
The agent harness and the skill's long steps can share one local model server,
and every agent turn sends the whole conversation to that server again.
Measured on one book (the Qwen Code transcript and the run's own files, read
after the run): during a 99-minute book profile the agent took 84 turns of
about 110K prompt tokens each, most with no prefix-cache hit - 9.3M prompt
tokens, an estimated 30% of the server's time; phase 3 at 2 server lanes sealed
7 pages an hour while the agent took 73 turns beside it, and 17-21 pages an
hour with no agent (the server's queue was not recorded, so that the agent
caused all of the gap is not proven).

So the agent does not read logs and pages turn after turn while a step runs.
It calls this script again and again.  One call blocks for up to --timeout
seconds (default 570, under Qwen Code's 600 s cap on a shell call), looks at
files and the process table between sleeps, asks no model and opens no
connection, and prints ONE line: done or not, pages sealed out of how many,
pages per hour, failures, and the run report when done, with the WARNING lines
the step printed (they say what to do once it is done, and the agent no longer
reads the log).

    python3 scripts/wait_for_run.py OUTPUT LOG [--progress BOOK]

OUTPUT is what the step writes:

- proofread_pages.py: its output directory;
- book_profile.py: the profile;
- an OCR runner (run_hunyuanocr.py, run_paddleocr_vl.py,
  run_apple_vision_ocr.py), directly or under run_ocr_phase.py: its output
  directory; run_hunyuanocr_second_look.py: engine B's directory, which it
  rewrites (only the pages it rewrote in this run count); ocr_book.py, the
  whole OCR phase in one command: the folder its parser names (the book);
- stitch_tables.py: its --output-dir, or the directory it rewrites;
- repair_book.py: its --output folder;
- apply_packet_answers.py: the packets directory;
- finish_book.py: the book folder.

LOG is the file the step's output goes to (`> LOG 2>&1`), a new one for each
launch.

ocr_book.py prints `[ocr-book] DRAFT PATH` once it has written the early
draft (PATH: the copy the person reads, X-draft.md beside the PDF, or
BOOK/draft.md and why it is not there): from then on the line, running or
not, ends with `; DRAFT PATH`, so the agent can tell the person where the
draft is while engine A still runs (SKILL.md, step 2).

With --progress BOOK, while it waits on phase 3 (proofread_pages.py) it
also rebuilds BOOK/proofreading.md (scripts/build_progress.py: the pages
sealed in OUTPUT as proofread, every other page as the draft) every
--progress-every seconds (default 300) and once more when it returns, and
its line ends with the file and the pages proofread.  The user reads that
file instead of waiting for the end of the book.  A progress file that cannot
be built is said on the line and changes nothing else: the exit code is the
step's.

The step is running while a process runs one of those scripts with OUTPUT as
the thing it writes - its arguments read with the script's own parser,
relative paths against the process's working directory, so a proofread run
that only reads a profile (--profile) is not the profile being built - or
while any process still writes LOG.  finish_book.py sends its steps' output to
its own log (BOOK/finish/finish-book.log), whose `[finish] step K of 5` lines
the running line shows; its RESULT line (in LOG, and in its own log) says how
it ended.  When none is left, the log's last lines
say how the step ended: its report line, the book profile's last step, a
`[packets] PENDING` line, the OCR phase's RESULT line, an ERROR or a
traceback.  With none of them the process was killed or died, and the line
says so; nothing here waits for a process that is gone.  When a process of
the step was seen writing another file than LOG, that file is read instead;
with no log at all nothing is guessed (exit 2), since a finished step and a
killed one then look alike.

When pages failed or were blocked, the line names them with the one-line
reason the run report's pages[] gives each (`[failed 3, 7: REASON]`, the
pages of one reason together), so an agent that reads no run report (rule
set B, SKILL.md 一) can tell the person which pages and why.

A run is DONE only when what it printed says no page failed, was blocked or
waits on packet answers: its summary line (`requested N | completed N |
pending N | ...`, every such line the run printed), and for a count the line
lacks, or with no summary line, its run report's counts and exitStatus.  A
`[packets] PENDING` line, a pending count or a report's exitStatus 10 or 11
is PENDING whatever else the run printed.  A report line with neither a
summary line nor a readable report is not taken for done (exit 2).

The packet transport (the calling agent answers phase 3's and the profile's
questions): proofread_pages.py and book_profile.py stop when questions wait
for answers, print `[packets] PENDING` with the wave's folder and the
apply_packet_answers.py command, and exit 10 (proofread_pages.py 11 when
pages failed or were blocked too).  The wait then says PENDING with the wave
and the command: answer every bundle of the wave, run the command, then run
the SAME step again.

The process table is /proc on Linux.  Where there is none (macOS), it is ps
(command lines, states and start times, to the second) and lsof (each
process's working directory and where its output goes); ps gives a command
line as one string, so an argument with a space in it reads as two words, and
such a run is found by the log it writes.  Without lsof only the command line
is read: a run started with relative paths is then found by its log alone.

Exit codes:
    0   done: the step finished and no page failed, was blocked or waits on
        packet answers
    1   finished with failures: failed or blocked pages, an ERROR, a traceback,
        a server not restored; or the step did not start (its parser refused
        the arguments, no such script or command).  Read the line: it names
        the failed and blocked pages and why (rule set B reads nothing else
        and tells the person; rule set A may read the report and the end of
        the log); do not just run it again
    2   cannot tell: this script's own arguments are wrong, no process runs
        the step and LOG does not exist (a wrong path), or the step's report
        line names a report that cannot be read and it printed no summary
        line.  Check both paths and read the end of LOG; do not run the step
        again for this
    4   stopped before it finished: no process runs the step and the log has
        no last line of it (killed, interrupted or died), an OCR call spent
        its time budget, or the GPU was busy with another job.  Run the SAME
        command again; sealed pages are kept
    10  waiting on packet answers (the step's exit 10): answer every
        bundle-*.json of the wave the line names, run the
        apply_packet_answers.py command it names, then run the SAME command
        again; sealed pages are kept
    11  waiting on packet answers AND pages failed or were blocked
        (proofread_pages.py exit 11): answer the wave as for exit 10, and fix
        the failed and blocked pages from the run report's pages[] - running
        it again does not fix those
    75  still running when the wait ran out: call this script again
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXIT_DONE, EXIT_FAILED, EXIT_USAGE, EXIT_STOPPED, EXIT_RUNNING = 0, 1, 2, 4, 75
# The packet transport's own codes (scripts/_packet_client.py), kept as the
# wait's: questions wait on answers the agent writes; 11 when pages failed or
# were blocked too.
EXIT_PENDING, EXIT_PENDING_FAILED = 10, 11
# run_ocr_phase.py's exit for a GPU queue busy with another job: nothing was
# released and no page was run.
PHASE_GPU_BUSY = 76
# What an OCR runner says when it deferred because a configured local model
# owns the GPU and no hand-over window was open (scripts/_gpu_queue.py's
# LOCAL_MODEL_MESSAGE): its exit 75 then means "run it through
# run_ocr_phase.py", not "run the same command again".
LOCAL_MODEL_MARK = "GPU belongs to the local model"
DEFAULT_TIMEOUT = 570.0
DEFAULT_INTERVAL = 15.0
# A process launched a moment ago may not be in the process table yet; nothing
# is called stopped within this many seconds of the wait's start.
START_GRACE = 10.0
# The end of the log that is read (a whole book's proofread log is well under this).
LOG_TAIL_BYTES = 4 << 20

# How often a wait on phase 3 rebuilds the progress file (--progress), in
# seconds; it is rebuilt once more when the wait returns.  A rebuild reads the
# book's sealed pages and engine B's (measured: 1.1 s for a 488-page book, all
# sealed), so the file is never more than a few minutes behind.
PROGRESS_EVERY = 300.0

# Script -> (kind of step, module, the argument naming what it writes).  A
# tuple of arguments: the first of them the run was given.
SCRIPTS = {
    "proofread_pages.py": ("proofread", "proofread_pages", "output_directory"),
    "book_profile.py": ("profile", "book_profile", "profile"),
    "run_paddleocr_vl.py": ("ocr", "run_paddleocr_vl", "output_directory"),
    "run_hunyuanocr.py": ("ocr", "run_hunyuanocr", "output_directory"),
    "run_apple_vision_ocr.py": ("ocr", "run_apple_vision_ocr", "output_directory"),
    "run_hunyuanocr_second_look.py": ("second-look", "run_hunyuanocr_second_look", "output_directory"),
    "ocr_book.py": ("ocr-book", "ocr_book", ("book", "book_directory", "output_directory", "output")),
    "stitch_tables.py": ("stitch", "stitch_tables", ("output_dir", "output_directory")),
    "repair_book.py": ("repair", "repair_book", "output"),
    "apply_packet_answers.py": ("packets", "apply_packet_answers", "packets_dir"),
    "finish_book.py": ("finish", "finish_book", "book"),
}
LABEL = {"proofread": "proofread_pages.py", "ocr": "the OCR runner", "profile": "book_profile.py",
         "finish": "finish_book.py", "second-look": "run_hunyuanocr_second_look.py", "ocr-book": "ocr_book.py",
         "stitch": "stitch_tables.py", "repair": "repair_book.py", "packets": "apply_packet_answers.py"}
# The file each runner seals beside page-NNNN.json, which it writes last.
SEAL_PARTNER = {"proofread": ".md", "ocr": ".txt", "second-look": ".txt"}
# Steps that rewrite pages sealed before them: only this run's count.
REWRITES = frozenset({"second-look"})

PAGE_SEAL = re.compile(r"page-(\d{4,})\.json")
PAGE_LINE = re.compile(r"^\s*page \d{4,}: ")
FAILED_LINE = re.compile(r"^\s*page \d{4,}: FAILED")
# A run's summary line, whatever its fields: proofread_pages.py's
# `requested N | completed N | pending N | skipped N | blocked N | failed N`,
# an OCR runner's (`... | deferred N`, the second look's `... | crops read N`).
SUMMARY_LINE = re.compile(r"\brequested (\d+)((?: \| [a-z][a-z ]*? \d+)+)")
SUMMARY_FIELD = re.compile(r" \| ([a-z][a-z ]*?) (\d+)")
# run_apple_vision_ocr.py's summary and report line.
VISION_LINE = re.compile(r"^REPORT (.+?) requested=(\d+) completed=(\d+) skipped=(\d+) failed=(\d+)")
# The counts a run fails or waits by: summed over every summary line it printed.
TROUBLE_KEYS = ("blocked", "failed", "pending", "deferred")
COUNT_KEYS = ("requested", "completed", "skipped") + TROUBLE_KEYS
# The run report's names (counts) where they differ from the summary's.
REPORT_KEYS = {"skipped": "skippedCurrent"}
# Packet waves: proofread_pages.py's `[packets] PENDING: N request(s) across K
# bundle(s) in WAVE`, book_profile.py's `[packets] PENDING N question(s) in
# WAVE (K bundle(s)); answer and run:` with the command on the next line.
PACKETS_PENDING = re.compile(r"^\s*\[packets\] PENDING:? (\d+) (request|question)\(s\)"
                             r"(?: across (\d+) bundle\(s\))? in (.+?)"
                             r"(?: \((\d+) bundle\(s\)\); answer and run:)?\s*$")
APPLY_AFTER = re.compile(r"^\s*\[packets\] after .*?, run: (.+?)\s*$")
ENDINGS = (
    ("finish", re.compile(r"^\[finish\] RESULT exit=(-?\d+)\s*(.*)$")),
    # The OCR phase's RESULT line (run_ocr_phase.py's `[ocr-phase]`), or any
    # other step's `[TAG] RESULT exit=N` but this script's own.
    ("phase", re.compile(r"^\[(?!finish\]|wait\])([\w-]+)\] RESULT exit=(-?\d+)\s*(.*)$")),
    ("report", re.compile(r"^\s*report: (.+?)\s*$")),
    ("vision", VISION_LINE),
    ("packets", PACKETS_PENDING),
    ("applied", re.compile(r"^\s*\[packets\] applied (.*)$")),
    ("stitched", re.compile(r"^\s*\d+ table\(s\) stitched, \d+ page\(s\) (?:would be )?rewritten, "
                            r"\d+ page\(s\) skipped")),
    ("repair", re.compile(r"^repair_book: (.*); exit (-?\d+)\s*$")),
    ("error", re.compile(r"^\s*ERROR (.*)$")),
    ("profile", re.compile(r"^\s*profile: (.+?) \(([\d.]+)s\)\s*$")),
    # repair_book.py's refusals before it repairs anything (only it prints
    # these lines).
    ("repair-refused", re.compile(r"^(output refused|--pages|inputs failed verification): ?(.*)$")),
    # The step did not start: its parser refused the arguments (argparse's
    # "PROG: error: ..."), Python found no such script, or the shell found no
    # such command.  The same command fails the same way.
    ("refused", re.compile(r"^(\S+\.py): error: (.*)$")),
    ("no-script", re.compile(r"^\S+: can't open file '(.+)': (\[Errno \d+\] .*)$")),
    ("no-command", re.compile(r"^\S*sh: (?:line \d+: |\d+: )?(.+?): (command not found|not found|"
                              r"No such file or directory|Permission denied)$")),
)
PHASE_TOOLS = {"ocr-phase": "run_ocr_phase.py", "ocr-book": "ocr_book.py"}
TRACEBACK_LINE = "Traceback (most recent call last):"
# ocr_book.py's line once the early draft is written: where the person reads it.
DRAFT_LINE = re.compile(r"^\[ocr-book\] DRAFT (.+?)\s*$")
# A step's own warnings (proofread_pages.py: the profile's pair classes are
# stale; the OCR phase's GPU warnings): each says what to do once the step is
# done.
WARNING_LINE = re.compile(r"^\s*WARNING (.*)$")
WARNINGS_SHOWN = 3
# finish_book.py: the line that starts a run in its own log (which it
# appends to), a step's line, and its exit code that means "run it again".
FINISH_START = re.compile(r"^\[finish\] \d{4}-\d{2}-\d{2}T")
FINISH_STEP = re.compile(r"^\[finish\] (step \d+ of \d+)(?: (done))?: ?(.*)$")
FINISH_AGAIN = (4,)
# The book profile's steps, each with the line book_profile.py prints when it
# is done.
PROFILE_STEPS = (
    ("probing", "aligning the two drafts"),
    ("layout:", "surveying the layout"),
    ("variants:", "classifying variant pairs"),
    ("engines:", "reading engine disagreements on crops"),
    ("notation:", "surveying the notation"),
)

# The process table: /proc where there is one, else ps and lsof.
PROC = Path("/proc")
PS_COMMAND = ("ps", "-A", "-ww", "-o", "pid=,stat=,lstart=,args=")
LSTART = "%a %b %d %H:%M:%S %Y"
TOOL_SECONDS = 20.0


@dataclass
class Step:
    """One process running the watched step."""
    pid: int
    script: str
    kind: str | None               # None: known only as a process writing LOG
    started: float                 # epoch seconds
    pages: list[int] | None        # the pages it was asked for; None when unknown
    overwrite: bool
    stdout: str | None = None      # the regular file its output goes to


@dataclass
class Counts:
    """What a finished run says it did: its summary lines, and its report
    for what they lack."""
    requested: int
    completed: int
    skipped: int
    blocked: int
    failed: int
    pending: int
    deferred: int
    summaries: int                 # how many summary lines the run printed
    exit_status: int | None        # the report's exitStatus, if it has one
    seconds: float | None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path,
                        help="what the step writes: proofread_pages.py's output directory, book_profile.py's "
                             "profile, an OCR runner's output directory, stitch_tables.py's or repair_book.py's "
                             "output, apply_packet_answers.py's packets directory, or finish_book.py's book folder")
    parser.add_argument("log", type=Path, help="the file the step's output goes to (> LOG 2>&1)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"seconds to wait at most (default {DEFAULT_TIMEOUT:.0f}); keep it under the "
                             "harness's time limit for one shell call")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"seconds between checks (default {DEFAULT_INTERVAL:.0f})")
    parser.add_argument("--progress", type=Path, metavar="BOOK",
                        help="waiting on phase 3 (OUTPUT is proofread_pages.py's output folder): rebuild "
                             "BOOK/proofreading.md (scripts/build_progress.py) every --progress-every "
                             "seconds and when the wait returns, and end the line with it and the pages "
                             "proofread")
    parser.add_argument("--progress-every", type=float, default=PROGRESS_EVERY,
                        help=f"seconds between rebuilds of the progress file (default {PROGRESS_EVERY:.0f})")
    arguments = parser.parse_args(argv)
    if arguments.timeout < 0 or arguments.interval <= 0 or arguments.progress_every <= 0:
        parser.error("--timeout must be at least 0, --interval and --progress-every above 0")
    output = real(arguments.output)
    progress = (ProgressFile(real(arguments.progress), output, arguments.progress_every)
                if arguments.progress is not None else None)
    code, line = wait(output, real(arguments.log), arguments.timeout, arguments.interval,
                      progress.tick if progress else None)
    if progress is not None and code != EXIT_USAGE:
        line += progress.last_line()
    print(f"[wait] RESULT exit={code} {line}", flush=True)
    return code


class ProgressFile:
    """The progress file of a book whose phase 3 the wait watches
    (--progress): rebuilt every EVERY seconds while the step runs, and once
    more when the wait returns."""

    def __init__(self, book: Path, output: Path, every: float):
        self.book, self.output, self.every = book, output, every
        self.kind: str | None = None
        self.built: float = time.monotonic()      # the last call built it on return
        self.said = ""

    def tick(self, kind: str, running: bool) -> None:
        self.kind = kind
        if running and kind == "proofread" and time.monotonic() - self.built >= self.every:
            self.build()

    def build(self) -> None:
        self.built = time.monotonic()
        try:
            import build_progress   # noqa: PLC0415 - only a wait with --progress reads the pages
            made = build_progress.build(self.book, self.output)
            self.said = f"; progress file {made.path}: {made.proofread}/{made.total} pages proofread"
        except Exception as error:  # noqa: BLE001 - the progress file never changes the wait's answer
            self.said = f"; progress file NOT rebuilt ({type(error).__name__}: {clip(str(error))})"

    def last_line(self) -> str:
        """What the wait's line ends with: the file rebuilt now, for phase 3."""
        if self.kind != "proofread":
            return ""
        self.build()
        return self.said


def wait(output: Path, log: Path, timeout: float, interval: float,
         tick: Callable[[str, bool], None] | None = None) -> tuple[int, str]:
    """Wait for the step; TICK(kind, running) is called at each check."""
    began = time.monotonic()
    deadline = began + timeout
    seen: dict[int, Step] = {}
    while True:
        steps = find_steps(output, log)
        for step in steps:
            seen.setdefault(step.pid, step)
        known = list(seen.values())
        written = log_of(log, known)
        kind = kind_of(output, written, known)
        if tick is not None:
            tick(kind, bool(steps))
        if steps:
            if time.monotonic() >= deadline:
                line = running_line(output, written, kind, steps)
                if written != log and kind != "finish":
                    line += f" (its output goes to {written}, not {log})"
                return EXIT_RUNNING, line + draft_said(written)
        else:
            ending = step_ending(output, written, kind, known)
            if ending is not None:
                return ending[0], ending[1] + draft_said(written)
            if not known and time.monotonic() - began < min(START_GRACE, timeout):
                time.sleep(min(1.0, interval))
                continue
            if not written.is_file():
                # Without the log, a finished step and a killed one look alike;
                # saying "stopped" would have a finished step run again.
                return EXIT_USAGE, (f"CANNOT TELL: no process runs the step that writes {output}, and its log "
                                    f"{written} does not exist - pass the file its output went to")
            return EXIT_STOPPED, stopped_line(output, written, kind, known) + draft_said(written)
        time.sleep(max(0.0, min(interval, deadline - time.monotonic())))


def draft_said(log: Path) -> str:
    """`; DRAFT PATH` when LOG holds ocr_book.py's draft line (the last one),
    else ""."""
    found = [match.group(1) for match in map(DRAFT_LINE.match, read_log(log) or []) if match]
    return f"; DRAFT {found[-1]}" if found else ""


# --- processes -------------------------------------------------------------

def process_table() -> ProcTable | PsTable:
    """The live processes, read now: /proc where there is one (Linux), else
    ps and lsof (macOS)."""
    return ProcTable() if (PROC / "self" / "stat").is_file() else PsTable()


class ProcTable:
    """The process table read from /proc."""

    def __init__(self) -> None:
        self.boot = boot_time()

    def pids(self) -> list[int]:
        try:
            return [int(entry.name) for entry in os.scandir(PROC) if entry.name.isdigit()]
        except OSError:
            return []

    def argv(self, pid: int) -> list[str] | None:
        try:
            raw = (PROC / str(pid) / "cmdline").read_bytes()
        except OSError:
            return None
        return [part.decode("utf-8", "surrogateescape") for part in raw.rstrip(b"\0").split(b"\0")] if raw else None

    def status(self, pid: int) -> tuple[str, float] | None:
        """The process's state letter and start time (epoch seconds)."""
        try:
            raw = (PROC / str(pid) / "stat").read_text()
            fields = raw[raw.rindex(")") + 2:].split()
            return fields[0], self.boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None

    def cwd(self, pid: int) -> str | None:
        try:
            return os.readlink(PROC / str(pid) / "cwd")
        except OSError:
            return None

    def target(self, pid: int, fd: int) -> str | None:
        """What the process's file descriptor FD is open on."""
        try:
            return os.readlink(PROC / str(pid) / "fd" / str(fd))
        except OSError:
            return None

    def writes_to(self, pid: int, log: Path) -> bool:
        """Whether the process's standard output or error is LOG."""
        return any(is_file_named(self.target(pid, fd), log) for fd in (1, 2))


class PsTable:
    """The process table read with ps, and with lsof for where a process
    works and writes, where there is no /proc (macOS).  ps gives each
    command line as one string: it is split at white space."""

    def __init__(self) -> None:
        self.rows: dict[int, tuple[str, float, list[str]]] = {}
        for line in tool_output(PS_COMMAND).splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            try:
                pid = int(parts[0])
                started = time.mktime(time.strptime(" ".join(parts[2:7]), LSTART))
            except ValueError:
                continue
            self.rows[pid] = (parts[1], started, parts[7].split())
        self.files: dict[int, dict[str, str]] = {}
        self.writers: dict[Path, set[int]] = {}

    def pids(self) -> list[int]:
        return list(self.rows)

    def argv(self, pid: int) -> list[str] | None:
        row = self.rows.get(pid)
        return row[2] if row else None

    def status(self, pid: int) -> tuple[str, float] | None:
        row = self.rows.get(pid)
        return (row[0][:1], row[1]) if row and row[0] else None

    def open_files(self, pid: int) -> dict[str, str]:
        """The process's working directory ("cwd") and what its standard
        output and error ("1", "2") are open on, by lsof."""
        if pid not in self.files:
            self.files[pid] = {}
            fd = None
            for line in tool_output(("lsof", "-n", "-P", "-w", "-a", "-p", str(pid), "-d", "cwd,1,2",
                                     "-Fpfn")).splitlines():
                if line.startswith("f"):
                    fd = line[1:]
                elif line.startswith("n") and fd is not None:
                    self.files[pid][fd] = line[1:]
        return self.files[pid]

    def cwd(self, pid: int) -> str | None:
        return self.open_files(pid).get("cwd")

    def target(self, pid: int, fd: int) -> str | None:
        return self.open_files(pid).get(str(fd))

    def writes_to(self, pid: int, log: Path) -> bool:
        """Whether the process's standard output or error is LOG: one lsof
        call for the whole table."""
        if log not in self.writers:
            self.writers[log] = set()
            if log.exists():
                for line in tool_output(("lsof", "-n", "-P", "-w", "-a", "-d", "1,2", "-Fp", "--",
                                         str(log))).splitlines():
                    if line.startswith("p") and line[1:].isdigit():
                        self.writers[log].add(int(line[1:]))
        return pid in self.writers[log]


def tool_output(command: Sequence[str]) -> str:
    """COMMAND's standard output in the C locale; "" when it cannot run."""
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=TOOL_SECONDS,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C", LANG="C"),
                              errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def is_file_named(target: str | None, log: Path) -> bool:
    return bool(target) and target.startswith("/") and os.path.realpath(target) == str(log)


def find_steps(output: Path, log: Path) -> list[Step]:
    """Every live process that runs one of SCRIPTS writing OUTPUT, or whose
    output goes to LOG (a run whose arguments cannot be read is still not
    called stopped while it writes its log)."""
    steps = []
    me = os.getpid()
    table = process_table()
    for pid in table.pids():
        if pid == me:
            continue
        argv = table.argv(pid)
        if not argv:
            continue
        joined = "\0".join(argv)
        named = any(name in joined for name in SCRIPTS)
        writes_log = not named and table.writes_to(pid, log)
        if not (named or writes_log):
            continue
        status = table.status(pid)
        if status is None or status[0][:1] in ("Z", "X"):
            continue                # gone, or exited and not yet reaped
        step = read_step(table, pid, argv, status[1], output) if named else None
        if step is None and (writes_log or table.writes_to(pid, log)):
            script = next((os.path.basename(word) for word in argv if word.endswith(".py")),
                          os.path.basename(argv[0]) if argv else "?")
            step = Step(pid, script, None, status[1], None, False)
        if step is not None:
            steps.append(step)
    return steps


def boot_time() -> float:
    """Epoch seconds at boot, to the hundredth (/proc/stat's btime is whole
    seconds, too coarse to tell a page sealed just before a run from one it
    sealed)."""
    try:
        return time.time() - float((PROC / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def parser_of(module: Any) -> argparse.ArgumentParser:
    """The script's own parser, or, for a script that builds it inside its
    main(), the same arguments declared here."""
    build = getattr(module, "build_parser", None)
    if callable(build):
        return build()
    return OWN_PARSERS[module.__name__]()


def stitch_parser() -> argparse.ArgumentParser:
    """stitch_tables.py's arguments (it builds its parser inside main())."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("output_directory")
    for option in ("--output-dir", "--renders", "--drafts", "--drafts2", "--profile", "--report"):
        parser.add_argument(option)
    parser.add_argument("--dry-run", action="store_true")
    return parser


OWN_PARSERS: dict[str, Callable[[], argparse.ArgumentParser]] = {"stitch_tables": stitch_parser}


def read_step(table: ProcTable | PsTable, pid: int, argv: list[str], started: float, output: Path) -> Step | None:
    """ARGV read as the step that writes OUTPUT, or None.  The first argument
    that names one of SCRIPTS is the script (so run_ocr_phase.py's own command
    line counts as the runner it wraps); the rest is read by that script's own
    parser."""
    cwd = table.cwd(pid)
    for index, word in enumerate(argv):
        script = os.path.basename(word)
        if script not in SCRIPTS:
            continue
        kind, module_name, destination = SCRIPTS[script]
        try:
            module = importlib.import_module(module_name)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                namespace, _ = parser_of(module).parse_known_args(argv[index + 1:])
        except (Exception, SystemExit):  # noqa: BLE001 - not a run of this script (an editor, a grep)
            return None
        names = (destination,) if isinstance(destination, str) else destination
        given = next((getattr(namespace, name) for name in names if getattr(namespace, name, None) is not None),
                     None)
        if absolute(given, cwd) != output:
            return None
        return Step(pid, script, kind, started, requested_pages(module, script, namespace, cwd),
                    bool(getattr(namespace, "overwrite", False)), stdout_file(table, pid))
    return None


def requested_pages(module: Any, script: str, namespace: argparse.Namespace, cwd: str | None) -> list[int] | None:
    """The pages a proofread or OCR run was asked for, by its own selection."""
    kind = SCRIPTS[script][0]
    if kind not in SEAL_PARTNER or kind in REWRITES:
        return None
    renders = absolute(getattr(namespace, "render_directory", None), cwd)
    if renders is None:
        return None
    namespace.render_directory = renders
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            if script == "proofread_pages.py":
                return list(module.parse_pages(namespace, renders))
            select = module.selected_pages
            # run_apple_vision_ocr.py's takes the arguments alone.
            chosen = select(namespace, renders) if len(inspect.signature(select).parameters) > 1 else select(namespace)
            return list(chosen[0])
    except Exception:  # noqa: BLE001 - the run itself reports a bad selection
        return None


def stdout_file(table: ProcTable | PsTable, pid: int) -> str | None:
    target = table.target(pid, 1)
    return os.path.realpath(target) if target and target.startswith("/") and os.path.isfile(target) else None


def absolute(value: Any, cwd: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        if cwd is None:
            return None
        path = Path(cwd) / path
    return Path(os.path.realpath(path))


def real(path: Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(path))))


# --- files -----------------------------------------------------------------

def kind_of(output: Path, log: Path, known: list[Step]) -> str:
    kinds = [step.kind for step in known if step.kind]
    if kinds:
        return kinds[0]
    if output.is_file() or output.suffix == ".json":
        return "profile"
    lines = read_log(log) or []
    if finish_log(output).is_file() or any(pattern.match(line) for line in lines
                                           for name, pattern in ENDINGS if name == "finish"):
        return "finish"
    told = kind_told(lines)
    if told:
        return told
    if (output / "answers.jsonl").is_file() or any(output.glob("wave-*")):
        return "packets"
    if seals(output, "ocr"):
        return "ocr"
    if any(pattern.match(line) for line in lines for name, pattern in ENDINGS if name == "phase"):
        return "ocr"
    return "proofread"


def kind_told(lines: list[str]) -> str | None:
    """The kind of step the log's last lines are of, by what only that step
    prints; None when they do not say."""
    endings = dict(ENDINGS)
    for line in reversed(lines):
        for name, kind in (("repair", "repair"), ("repair-refused", "repair"), ("stitched", "stitch"),
                           ("applied", "packets"), ("vision", "ocr")):
            if endings[name].match(line):
                return kind
        # ocr_book.py's own RESULT line: the whole OCR phase, whose engines'
        # summary lines come before it.
        phase = endings["phase"].match(line)
        if phase and phase.group(1) == "ocr-book":
            return "ocr-book"
        match = SUMMARY_LINE.search(line)
        if match:
            fields = summary_fields(match)
            return ("proofread" if "pending" in fields else "second-look" if "crops read" in fields
                    else "ocr" if "deferred" in fields else None)
    return None


def log_of(log: Path, known: list[Step]) -> Path:
    """LOG, unless a process of the step was seen writing another file: then
    that file, whose last lines are the step's."""
    files = [step.stdout for step in known if step.stdout]
    return log if not files or str(log) in files else Path(files[0])


def seals(directory: Path, kind: str) -> dict[int, float]:
    """Sealed pages in DIRECTORY and their seal times: page-NNNN.json beside
    its partner file."""
    partner = SEAL_PARTNER.get(kind)
    if partner is None:
        return {}
    try:
        names = set(os.listdir(directory))
    except OSError:
        return {}
    out = {}
    for name in names:
        match = PAGE_SEAL.fullmatch(name)
        if match and f"page-{match.group(1)}{partner}" in names:
            try:
                out[int(match.group(1))] = os.stat(directory / name).st_mtime
            except OSError:
                pass
    return out


def read_log(log: Path) -> list[str] | None:
    try:
        with open(log, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            data = handle.read()
    except OSError:
        return None
    return data.decode("utf-8", "replace").splitlines()


def read_report(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return report if isinstance(report, dict) else {}


def wanted_pages(steps: list[Step]) -> set[int] | None:
    """The pages the runs were asked for together; None when any is unknown."""
    if not steps or any(step.pages is None for step in steps):
        return None
    return set().union(*(step.pages for step in steps))


# --- counts ----------------------------------------------------------------

def summary_fields(match: re.Match) -> dict[str, int]:
    """A summary line's fields by name: requested, completed, pending, ..."""
    fields = {"requested": int(match.group(1))}
    fields.update((name, int(value)) for name, value in SUMMARY_FIELD.findall(match.group(2)))
    return fields


def summaries(lines: list[str]) -> list[dict[str, int]]:
    """Every summary line in LINES, as fields."""
    out = []
    for line in lines:
        vision = VISION_LINE.match(line)
        if vision:
            out.append(dict(zip(("requested", "completed", "skipped", "failed"), map(int, vision.groups()[1:]))))
            continue
        match = SUMMARY_LINE.search(line)
        if match:
            out.append(summary_fields(match))
    return out


def report_counts(report: dict[str, Any]) -> dict[str, int]:
    """The run report's counts by the summary's names; run_apple_vision_ocr.py's
    are its page sets."""
    out: dict[str, int] = {}
    counts = report.get("counts")
    sets = report.get("pageSets")
    if isinstance(counts, dict):
        for key in COUNT_KEYS:
            value = counts.get(REPORT_KEYS.get(key, key))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = int(value)
    elif isinstance(sets, dict):
        for key, name in (("requested", "requested"), ("completed", "completed"), ("skipped", "skippedCurrent"),
                          ("blocked", "blockedBeforeWorkers"), ("failed", "failed")):
            if isinstance(sets.get(name), list):
                out[key] = len(sets[name])
    return out


def run_counts(lines: list[str], report: str | None) -> Counts | None:
    """What the run says it did: each count from its summary lines (the
    failures and pending pages summed over all of them, the rest from the
    last), else from its report; None when it has neither."""
    found = summaries(lines)
    data = read_report(report)
    filed = report_counts(data)
    status = data.get("exitStatus")
    if not found and not filed:
        return None
    last = found[-1] if found else {}

    def count(key: str) -> int:
        given = [fields[key] for fields in found if key in fields]
        if given:
            return sum(given) if key in TROUBLE_KEYS else last.get(key, given[-1])
        return filed.get(key, 0)

    seconds = data.get("wallSeconds") or data.get("durationSeconds")
    # The report's exit status only stands in for a summary line: a report
    # at a shared default path may be another run's by now.
    filed_status = not found and isinstance(status, int) and not isinstance(status, bool)
    return Counts(*(count(key) for key in COUNT_KEYS), summaries=len(found),
                  exit_status=status if filed_status else None,
                  seconds=float(seconds) if isinstance(seconds, (int, float)) and seconds else None)


def counts_said(counts: Counts | None, report: str | None, output: Path, kind: str) -> str:
    """Pages sealed out of how many, pages per hour, failures and the report;
    with failed or blocked pages, which and why (trouble_said)."""
    if counts is None:
        unit = "pages rewritten" if kind in REWRITES else "pages sealed"
        return f"{len(seals(output, kind))} {unit}; no run report"
    rate = (f", {counts.completed / (counts.seconds / 3600):.1f} pages/h"
            if counts.seconds and counts.completed else "")
    where = f"report {report}" if report else "no run report"
    more = "".join(f", {value} {name}" for name, value in (("pending", counts.pending),
                                                           ("deferred", counts.deferred)) if value)
    over = f" (failures counted over {counts.summaries} summary lines)" if counts.summaries > 1 else ""
    trouble = trouble_said(read_report(report)) if counts.failed or counts.blocked else ""
    return (f"{counts.completed + counts.skipped}/{counts.requested} pages sealed ({counts.completed} this run"
            f"{rate}), {counts.failed} failed, {counts.blocked} blocked{more}{over}; {where}{trouble}")


def what_to_do(said: str) -> str:
    """What a line on failed or blocked pages tells the agent: the pages and
    their reasons are on it when the report names them."""
    if "; pages failed or blocked: " in said:
        return "the pages and why are on this line: tell the user; do not just run it again"
    return "read the report's pages[] errors, do not just run it again"


TROUBLE_STATUSES = ("failed", "blocked")
TROUBLE_GROUPS = 4                 # reasons named; the rest counted
TROUBLE_PAGES = 8                  # pages named a reason; the rest counted
TROUBLE_REASON = 140               # characters of a reason


def trouble_said(report: dict[str, Any]) -> str:
    """The failed and blocked pages of a run report's pages[], each reason
    once with its pages: `; pages failed or blocked: [failed 3, 7: REASON]
    ...`; "" when the report names none."""
    groups: dict[tuple[str, str], list[int]] = {}
    for entry in report.get("pages") or []:
        if not isinstance(entry, dict) or entry.get("status") not in TROUBLE_STATUSES:
            continue
        page = entry.get("scanPage")
        reason = clip(str(entry.get("error") or entry.get("reason") or "no reason given"), TROUBLE_REASON)
        groups.setdefault((str(entry["status"]), reason), []).append(page if isinstance(page, int) else -1)
    if not groups:
        return ""
    said = []
    for (status, reason), pages in list(groups.items())[:TROUBLE_GROUPS]:
        named = ", ".join(str(page) if page >= 0 else "?" for page in sorted(pages)[:TROUBLE_PAGES])
        more = f" and {len(pages) - TROUBLE_PAGES} more" if len(pages) > TROUBLE_PAGES else ""
        said.append(f"[{status} {named}{more}: {reason}]")
    rest = sum(len(pages) for pages in list(groups.values())[TROUBLE_GROUPS:])
    return "; pages failed or blocked: " + " ".join(said) + (f" and {rest} more page(s)" if rest else "")


# --- packet waves ----------------------------------------------------------

@dataclass
class Wave:
    """A packet wave the step waits on."""
    questions: int | None
    bundles: int | None
    folder: str | None
    command: str | None


def wave_of(lines: list[str], report: dict[str, Any]) -> Wave | None:
    """The wave the run's `[packets] PENDING` line names, with the
    apply_packet_answers.py command it printed; else its report's packetWave."""
    wave = None
    for index, line in enumerate(lines):
        match = PACKETS_PENDING.match(line)
        if match:
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            wave = Wave(int(match.group(1)), int(match.group(3) or match.group(5) or 0) or None, match.group(4),
                        following if match.group(5) and "apply_packet_answers.py" in following else None)
            continue
        after = APPLY_AFTER.match(line)
        if after and wave is not None:
            wave.command = after.group(1)
    filed = report.get("packetWave")
    if wave is None and isinstance(filed, dict) and filed.get("pending"):
        bundles, pending = filed.get("bundles"), filed.get("pending")
        wave = Wave(pending if isinstance(pending, int) else None,
                    len(bundles) if isinstance(bundles, list) else None,
                    str(filed["waveDir"]) if filed.get("waveDir") else None,
                    str(filed["applyCommand"]) if filed.get("applyCommand") else None)
    return wave


def pending_ending(script: str, kind: str, said: str, wave: Wave | None, failing: bool,
                   warned: str) -> tuple[int, str]:
    """The wait's line for a step that stopped for packet answers."""
    if wave is not None and wave.folder:
        where = (f"{wave.questions} question(s)" if wave.questions else "questions") + (
            f" in {wave.bundles} bundle(s)" if wave.bundles else "") + f" in {wave.folder}"
        command = wave.command or (f"python3 scripts/apply_packet_answers.py {Path(wave.folder).parent} "
                                   f"{wave.folder}")
    else:
        where = "questions; the wave is in the run report's packetWave"
        command = "python3 scripts/apply_packet_answers.py PACKETS_DIR WAVE_DIR"
    then = (f"answer every bundle-*.json there into the answers file it names, run {command}, then run the "
            f"SAME command again{'; sealed pages are kept' if kind in SEAL_PARTNER else ''}")
    if failing:
        return EXIT_PENDING_FAILED, (f"PENDING {script}: packet answers wait ({where}) AND pages failed or were "
                                     f"blocked: {said} - {then}; fix the failed and blocked pages from the "
                                     f"report's pages[], running it again does not fix them{warned}")
    return EXIT_PENDING, f"PENDING {script}: packet answers wait ({where}); {said} - {then}{warned}"


# --- lines -----------------------------------------------------------------

def running_line(output: Path, log: Path, kind: str, steps: list[Step]) -> str:
    now = time.time()
    started = min(step.started for step in steps)
    script = next((step.script for step in steps if step.kind), steps[0].script)
    tail = f"running {duration(now - started)} - call wait_for_run.py again"
    lines = read_log(log) or []
    if kind == "profile":
        return f"RUNNING {script}: {profile_progress(lines)}; {tail}"
    if kind == "finish":
        return f"RUNNING {script}: {finish_progress(output)}; {tail}"
    if kind not in SEAL_PARTNER:
        last = next((line.strip() for line in reversed(lines) if line.strip()), None)
        return f"RUNNING {script}: {f'last line: {clip(last)}' if last else 'nothing printed yet'}; {tail}"
    sealed = seals(output, kind)
    this_run = {page: when for page, when in sealed.items() if when >= started}
    counted = this_run if kind in REWRITES or any(step.overwrite for step in steps) else sealed
    wanted = wanted_pages([step for step in steps if step.kind])
    unit = "pages rewritten" if kind in REWRITES else "pages sealed"
    progress = (f"{sum(1 for page in counted if page in wanted)}/{len(wanted)} {unit}" if wanted is not None
                else f"{len(counted)} {unit}")
    rate = len(this_run) / max((now - started) / 3600, 1e-9)
    failed = sum(1 for line in lines if FAILED_LINE.match(line))
    last = (f"last seal {duration(now - max(this_run.values()))} ago" if this_run
            else "no page sealed yet in this run")
    return (f"RUNNING {script}: {progress} ({len(this_run)} this run, {rate:.1f} pages/h), "
            f"{failed} failed, {last}; {tail}")


def profile_progress(lines: list[str]) -> str:
    done = [marker for marker, _ in PROFILE_STEPS if any(line.strip().startswith(marker) for line in lines)]
    missing = [(number, what) for number, (marker, what) in enumerate(PROFILE_STEPS, 1) if marker not in done]
    number, what = missing[0] if missing else (len(PROFILE_STEPS), "writing the profile")
    return f"step {number} of {len(PROFILE_STEPS)} ({what})"


def finish_log(output: Path) -> Path:
    """finish_book.py's own log in the book folder OUTPUT."""
    return output / "finish" / "finish-book.log"


def finish_run_lines(output: Path) -> list[str]:
    """The lines of finish_book.py's last run in its own log (it appends)."""
    lines = read_log(finish_log(output)) or []
    start = max((index for index, line in enumerate(lines) if FINISH_START.match(line)), default=0)
    return lines[start:]


def finish_progress(output: Path) -> str:
    """Which step finish_book.py is on, from its own log."""
    steps = [match for match in map(FINISH_STEP.match, finish_run_lines(output)) if match]
    if not steps:
        return "starting"
    last = steps[-1]
    return f"{last.group(1)} done" if last.group(2) else f"{last.group(1)} ({clip(last.group(3), 90)})"


def beside_pdf(output: Path) -> str:
    """The copies finish_book.py wrote beside the book's PDF (its report's
    delivered list, scripts/_deliver.py), for the DONE line: the files the
    person reads."""
    try:
        report = json.loads((finish_log(output).parent / "finish-report.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return ""
    written = [entry.get("path") for entry in report.get("delivered") or []
               if isinstance(entry, dict) and entry.get("written") and entry.get("path")]
    return f"; beside the PDF: {', '.join(written)}" if written else ""


def finish_ending(output: Path, match: re.Match, warned: str) -> tuple[int, str]:
    """The wait's line for finish_book.py's RESULT line MATCH."""
    code = int(match.group(1))
    said = re.sub(r"^(?:DONE|FAILED|STOPPED|NOT READY) finish_book\.py: ", "", match.group(2).strip())
    if code == 0:
        return EXIT_DONE, (f"DONE finish_book.py: {clip(said, 300)}; final text {output / 'final.md'}, doubt list "
                           f"{output / 'doubts.md'}{beside_pdf(output)}{warned}")
    if code in FINISH_AGAIN:
        return EXIT_STOPPED, f"STOPPED finish_book.py: exit {code}: {clip(said, 300)}{warned}"
    if code == 3:
        return EXIT_FAILED, f"FAILED finish_book.py: not ready: {clip(said, 300)}{warned}"
    return EXIT_FAILED, f"FAILED finish_book.py: exit {code}: {clip(said, 300)}{warned}"


def step_ending(output: Path, log: Path, kind: str, known: list[Step]) -> tuple[int, str] | None:
    """How the step ended, from the last lines of its log; None when the log
    has no last line of it."""
    lines = read_log(log)
    if kind == "finish" and lines:
        # finish_book.py's own log holds its earlier runs too: only the last.
        start = max((index for index, line in enumerate(lines) if FINISH_START.match(line)), default=0)
        lines = lines[start:]
    if not lines:
        return None
    last_page = max((index for index, line in enumerate(lines) if PAGE_LINE.match(line)), default=-1)
    tail = lines[last_page + 1:]
    script = next((step.script for step in known if step.kind), LABEL[kind])
    events: list[tuple[int, str, Any]] = []
    for index, line in enumerate(tail):
        for name, pattern in ENDINGS:
            match = pattern.match(line)
            if match:
                events.append((index, name, match))
                break
        if line.strip() == TRACEBACK_LINE:
            events.append((index, "traceback", None))
    if not events:
        return None
    report = next((match.group(1) for _, name, match in reversed(events) if name in ("report", "vision")), None)
    data = read_report(report)
    if not any(step.kind for step in known):
        # Ended before the wait saw it: an OCR runner's report names it.
        tool = data.get("tool") or {}
        runner = tool.get("runner") or tool.get("runnerFile") if isinstance(tool, dict) else None
        script = os.path.basename(runner) if isinstance(runner, str) and runner else script
    index, name, match = events[-1]
    if name == "traceback":
        exception = next((line.strip() for line in reversed(tail[index:]) if line.strip()), "")
        if exception.startswith("KeyboardInterrupt"):
            return EXIT_STOPPED, (f"STOPPED {script}: interrupted ({clip(exception)}); "
                                  f"{progress_on_disk(output, kind, known)} - {run_again(kind)}")
        return EXIT_FAILED, f"FAILED {script}: it crashed: {clip(exception)} - read the end of {log}"
    if name == "error":
        return EXIT_FAILED, f"FAILED {script}: ERROR {clip(match.group(1))} - read {log}"
    if name in ("refused", "no-script", "no-command"):
        if name == "refused":
            script = match.group(1)
            why = f"it refused its arguments: {clip(match.group(2))} - fix the command (see its --help)"
        elif name == "no-script":
            script = os.path.basename(match.group(1))
            why = (f"python cannot open {clip(match.group(1))} ({clip(match.group(2))}) - fix the script's "
                   "path or the folder the command runs in")
        else:
            why = f"the shell says {clip(match.group(1))}: {match.group(2)} - fix the command"
        return EXIT_FAILED, (f"FAILED {script}: it did not start: {why}; the same command fails the same way, "
                             "do not just run it again")
    if name == "repair-refused":
        said = " ".join(line.strip() for line in tail[index:] if line.strip())
        return EXIT_FAILED, (f"FAILED {script}: it repaired nothing: {clip(said, 300)} - fix what it names, do "
                             "not just run it again")
    # The step ran to its end (or to its time budget): what it warned of too.
    if name == "finish":
        own = finish_run_lines(output)
        return finish_ending(output, match, warnings_said(own, finish_log(output)) if own else "")
    warned = warnings_said(lines, log)
    counts = run_counts(lines, report)
    said = counts_said(counts, report, output, kind)
    wave = wave_of(lines, data)
    if name == "phase":
        wrapper, code = PHASE_TOOLS.get(match.group(1), f"its [{match.group(1)}] RESULT line:"), int(match.group(2))
        if code in (EXIT_PENDING, EXIT_PENDING_FAILED) or wave is not None or (counts and counts.pending):
            return pending_ending(script, kind, said, wave, code == EXIT_PENDING_FAILED or bool(
                counts and (counts.failed or counts.blocked)), warned)
        if code == 0 and counts and (counts.failed or counts.blocked):
            return EXIT_FAILED, (f"FAILED {script}: {wrapper} exit 0, but pages failed or were blocked: {said} - "
                                 f"{what_to_do(said)}{warned}")
        if code == 0:
            return EXIT_DONE, f"DONE {script}: {said}{warned}"
        if code == 75 and any(LOCAL_MODEL_MARK in line for line in tail):
            return local_model_ending(script, said, warned)
        if code == 75:
            return EXIT_STOPPED, (f"STOPPED {script}: the OCR call spent its time budget ({said}) - run the "
                                  f"SAME command again; sealed pages are kept{warned}")
        if code == PHASE_GPU_BUSY and "stopped for it" in match.group(3):
            # The window opened and its runner sealed nothing: the server was
            # stopped and restored for it (run_ocr_phase.no_progress_is_busy).
            return EXIT_STOPPED, (f"STOPPED {script}: no page was sealed in the OCR window ({wrapper} exit {code} "
                                  f"{clip(match.group(3))}); the local model was stopped for it and is back up - "
                                  f"wait, then run the SAME command again{warned}")
        if code == PHASE_GPU_BUSY:
            return EXIT_STOPPED, (f"STOPPED {script}: the GPU is busy with another job ({wrapper} exit {code} "
                                  f"{clip(match.group(3))}); nothing was stopped and no page was run - wait for "
                                  f"that job, then run the SAME command again{warned}")
        return EXIT_FAILED, f"FAILED {script}: {wrapper} exit {code} {clip(match.group(3))}; {said}{warned}"
    if name in ("report", "vision"):
        if counts and counts.deferred and not counts.completed and any(LOCAL_MODEL_MARK in line for line in tail):
            return local_model_ending(script, said, warned)
        return finished_ending(script, kind, log, report, counts, said, wave, warned)
    if name == "packets":
        unwritten = ("no profile written yet" if kind == "profile"
                     else said if counts is not None else "nothing sealed by it yet")
        return pending_ending(script, kind, unwritten, wave, bool(counts and (counts.failed or counts.blocked)),
                              warned)
    if name == "profile":
        return EXIT_DONE, (f"DONE {script}: profile {match.group(1)} written in "
                           f"{duration(float(match.group(2)))}{warned}")
    if name == "stitched":
        return EXIT_DONE, f"DONE {script}: {clip(tail[index].strip())}{warned}"
    if name == "repair":
        code = int(match.group(2))
        if code == 0:
            return EXIT_DONE, f"DONE {script}: {clip(match.group(1), 300)}; output {output}{warned}"
        return EXIT_FAILED, (f"FAILED {script}: exit {code}: {clip(match.group(1), 300)} - read "
                             f"{output / 'repair-report.json'}, do not just run it again{warned}")
    if name == "applied":
        return EXIT_DONE, (f"DONE {script}: applied {clip(match.group(1), 300)} - now run the step that wrote "
                           f"the wave again, the SAME command{warned}")
    return None


def local_model_ending(script: str, said: str, warned: str) -> tuple[int, str]:
    """The wait's line for a run whose GPU engines deferred to the local model."""
    return EXIT_STOPPED, (f"STOPPED {script}: the GPU belongs to the local model (OCR_PHASE_RELEASE_CMD is set) "
                          f"and no hand-over window was open, so nothing was loaded ({said}) - run it through "
                          f"run_ocr_phase.py, not the SAME command again{warned}")


def finished_ending(script: str, kind: str, log: Path, report: str | None, counts: Counts | None, said: str,
                    wave: Wave | None, warned: str) -> tuple[int, str]:
    """The wait's line for a run that printed its report line: done only when
    nothing failed, was blocked or waits on packet answers."""
    status = counts.exit_status if counts else None
    failing = bool(counts and (counts.failed or counts.blocked))
    if wave is not None or (counts and counts.pending) or status in (EXIT_PENDING, EXIT_PENDING_FAILED):
        return pending_ending(script, kind, said, wave, failing or status == EXIT_PENDING_FAILED, warned)
    if counts is None:
        return EXIT_USAGE, (f"CANNOT TELL {script}: its log names the report {report}, which cannot be read, "
                            f"and has no summary line - read the end of {log}; do not take the step for done")
    if failing:
        return EXIT_FAILED, (f"FAILED {script}: finished with failed or blocked pages: {said} - "
                             f"{what_to_do(said)}{warned}")
    if counts.deferred:
        return EXIT_STOPPED, (f"STOPPED {script}: {counts.deferred} page(s) left by the time budget; "
                              f"{said} - run the SAME command again; sealed pages are kept{warned}")
    if status not in (None, 0):
        return EXIT_FAILED, (f"FAILED {script}: its report says exitStatus {status}: {said} - read the "
                             f"report and the end of {log}, do not just run it again{warned}")
    return EXIT_DONE, f"DONE {script}: {said}{warned}"


def warnings_said(lines: list[str], log: Path) -> str:
    """The step's WARNING lines, which the agent no longer reads in the log:
    how many, the first few (the head and the tail, where the advice is) and
    how to read them all; "" when there are none."""
    found = [match.group(1).strip() for match in map(WARNING_LINE.match, lines) if match]
    if not found:
        return ""
    distinct = list(dict.fromkeys(found))
    shown = "; ".join(f"'{clip_middle(text)}'" for text in distinct[:WARNINGS_SHOWN])
    more = f"; and {len(distinct) - WARNINGS_SHOWN} more" if len(distinct) > WARNINGS_SHOWN else ""
    return (f"; {len(found)} WARNING line(s) - read them before the next step "
            f"(grep -n WARNING {log}): {shown}{more}")


def progress_on_disk(output: Path, kind: str, known: list[Step]) -> str:
    if kind == "finish":
        return f"its last step: {finish_progress(output)}"
    if kind == "profile":
        if not output.is_file():
            return "no profile written"
        if known and output.stat().st_mtime < min(step.started for step in known):
            return f"{output.name} is from an earlier run"
        return f"{output.name} exists"
    if kind not in SEAL_PARTNER:
        return f"{output} {'exists' if output.exists() else 'not written'}"
    sealed = seals(output, kind)
    if known and (kind in REWRITES or any(step.overwrite for step in known)):
        started = min(step.started for step in known)
        sealed = {page: when for page, when in sealed.items() if when >= started}
    unit = "pages rewritten" if kind in REWRITES else "pages sealed"
    wanted = wanted_pages([step for step in known if step.kind])
    if wanted is not None:
        return f"{sum(1 for page in sealed if page in wanted)}/{len(wanted)} {unit}"
    return f"{len(sealed)} {unit}"


def stopped_line(output: Path, log: Path, kind: str, known: list[Step]) -> str:
    script = next((step.script for step in known if step.kind), LABEL[kind])
    last = next((line.strip() for line in reversed(read_log(log) or []) if line.strip()), None)
    said = f"; last log line: {clip(last)}" if last else f"; {log} is empty or missing"
    return (f"STOPPED {script}: no process runs it and its log has no last line of it (killed or died); "
            f"{progress_on_disk(output, kind, known)}{said} - {run_again(kind)}")


def run_again(kind: str) -> str:
    """What to do about a step of KIND that stopped before its end."""
    if kind == "finish":
        return "run the SAME command again; what it finished is kept"
    if kind == "repair":
        # repair_book.py refuses an output folder that exists (exit 3).
        return "run the same command again with --resume, which reuses the output folder it started"
    if kind in SEAL_PARTNER:
        return "run the SAME command again; sealed pages are kept"
    return "run the SAME command again"


def duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def clip(text: str, size: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[:size - 1] + "…"


def clip_middle(text: str, size: int = 160) -> str:
    """TEXT cut in the middle, keeping its start and its end."""
    text = " ".join(text.split())
    if len(text) <= size:
        return text
    head = (size - 3) // 2
    start, end = text[:head], text[len(text) - (size - 3 - head):]
    # Cut at spaces where there are any, not inside a word.
    start = start.rsplit(" ", 1)[0] if " " in start else start
    end = end.split(" ", 1)[-1] if " " in end else end
    return start + " … " + end


if __name__ == "__main__":
    sys.exit(main())
