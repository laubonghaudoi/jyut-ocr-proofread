#!/usr/bin/env python3
"""Offline check of scripts/wait_for_run.py on made-up output folders.

No GPU, no model, no server: each case writes a made-up book folder in a
temporary directory and starts a stand-in process under the name of the real
script (proofread_pages.py, book_profile.py, run_ocr_phase.py,
run_paddleocr_vl.py, run_hunyuanocr_second_look.py, run_apple_vision_ocr.py,
stitch_tables.py, repair_book.py and apply_packet_answers.py), which seals
pages and prints the lines the real script prints - proofread_pages.py's
summary line with its `pending` field, the `[packets] PENDING` lines and its
exits 10 and 11 included - then ends the way the case says: with its report,
killed, crashed, with failed, blocked or pending pages, without a summary
line (the run report's counts then decide), or not at all.  wait_for_run.py
reads the stand-in's arguments with the real script's parser, as it would a
real run's.  One launcher runs the real book_profile.main() with its model
steps stubbed, so its log lines and the profile it writes are the real
script's; the real scripts' parsers refuse a wrong option before any file or
model call.

The real proofread_pages.py, book_profile.py and apply_packet_answers.py run
too, with the packet transport, on a made-up book with real renders (no
model: a packet run stops for answers, and a page with no draft fails): a
wave pending (exit 10), answered and applied, then the run done; pending
beside a failed page (exit 11); every page failed (exit 1); the profile
pending.  So the wait reads what P's scripts print now, and a pending or
failed run cannot be reported DONE again (it was, before 2026-11-14: the
summary line's `pending` field did not match, and the report branch said
DONE).

Some cases run again with the wait reading the process table with ps and
lsof instead of /proc, as on macOS (wait_for_run.PROC pointed at a folder
that does not exist).

Each case checks the exit code, that exactly one line is printed, what the
line says, and that the wait returns promptly when the process ends instead
of sitting out its timeout.  A run with failed or blocked pages has them on
the line with the reason its report's pages[] gives each (the stand-ins'
reports and the real proofread_pages.py's): rule set B reads no run report,
and before 2026-11-15 its agent had only the counts to tell the person.

Also checked: wait_for_run.py's exit codes 10 and 11 are the packet
transport's, it knows P's long steps, its docstring documents each exit code,
the script opens no connection, and SKILL.md's section on
long steps (五) tells the agent to wait with it instead of reading logs and
sealed pages, with every exit code the script has and no other (a step that
did not start is exit 1, exit 2 only this script's own arguments), and the
rules that were there before (the handover in the foreground, no
run_ocr_phase.py while a step runs, Qwen Code killing background shells on
exit), and the run sheet's steps (四) wait with it on each long step:
the OCR phase (ocr_book.py), the book profile, phase 3 with --progress
BOOK and finish_book.py; the measured cost that says why is
long-book-workflow.md's.

Usage:
    python3 tests/check_wait_for_run.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = Path(__file__).resolve().parent.parent
WAIT = ROOT / "scripts" / "wait_for_run.py"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
# Every process this check starts inherits an endpoint nothing listens on
# (tests/offline.py).
import offline  # noqa: E402,F401

# The stand-ins take what to do from STAND_IN_PLAN (JSON); their command-line
# arguments are only there for wait_for_run.py to read.
COMMON = '''
import json, os, signal, subprocess, sys, time
from pathlib import Path
plan = json.loads(os.environ["STAND_IN_PLAN"])

def end(kind):
    if kind == "kill":
        os.kill(os.getpid(), signal.SIGKILL)
    if kind == "hang":
        time.sleep(120)
    if kind == "traceback":
        raise RuntimeError("stand-in crash")
    if kind == "interrupt":
        raise KeyboardInterrupt
'''

# proofread_pages.py's lines as P prints them: the summary line with its
# pending field, and with packet answers pending the wave's two lines (and a
# third beside failed or blocked pages), exit 10 or 11.
PROOFREAD = COMMON + '''
out = Path(plan["output"])
if plan.get("end") == "error":
    print("ERROR render directory does not exist: " + plan["output"], file=sys.stderr)
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
if plan.get("warnings"):        # proofread_pages.py's own WARNING line, and a made-up one at the end
    print("  WARNING 3 pair(s) are classified differently by the variant lists than by this profile (e.g. "
          "甲|乙, 丙|丁, 戊|己); merging follows the lists, but the profile's engine calibration and variant "
          "list do not: rebuild the profile (book_profile.py)", flush=True)
print("  engine preference A (from profile book-profile.json)", flush=True)
print(f"  proofreading {len(plan['pages'])} page(s), 2 in flight, backend=packet, effort=xhigh", flush=True)
started = time.monotonic()
for page in plan["pages"]:
    time.sleep(plan.get("delay", 0.2))
    (out / f"page-{page:04d}.md").write_text("text\\n")
    (out / f"page-{page:04d}.json").write_text("{}\\n")
    print(f"  page {page:04d}: 1.0s  clean=True  adjudicated=0  calls=3  reasoning=100", flush=True)
for page in plan.get("failed", []):
    print(f"  page {page:04d}: FAILED stand-in page failure", file=sys.stderr, flush=True)
end(plan.get("end"))
failed, blocked, pending = len(plan.get("failed", [])), plan.get("blocked", 0), plan.get("pending", 0)
status = (11 if failed or blocked else 10) if pending else (1 if failed or blocked else 0)
wave = Path(plan.get("packets", "packets")).resolve() / "wave-01"
apply = f"{sys.executable} {Path('bin/apply_packet_answers.py').resolve()} {wave.parent} {wave}"
report = Path(plan["report"])
report.write_text(plan.get("report_text") or json.dumps({
    "exitStatus": status,
    "counts": {"requested": plan["requested"], "completed": len(plan["pages"]), "pending": pending,
               "skippedCurrent": 0, "legacy": 0, "blocked": blocked, "failed": failed},
    "pages": [{"scanPage": page, "status": "failed", "error": "stand-in page failure"} for page in plan.get("failed", [])]
             + [{"scanPage": 90 + n, "status": "blocked", "error": "existing output rejected: stand-in; use --overwrite"}
                for n in range(blocked)],
    "wallSeconds": round(time.monotonic() - started, 1),
    **({"packetWave": {"pending": pending, "waveDir": str(wave), "bundles": [{"bundle": str(wave / "bundle-01.json")}],
                       "applyCommand": apply}} if pending else {})}))
if plan.get("summary", True):
    print(f"  requested {plan['requested']} | completed {len(plan['pages'])} | pending {pending} | skipped 0 | "
          f"blocked {blocked} | failed {failed}", flush=True)
if pending and plan.get("wave_lines", True):
    print(f"  [packets] PENDING: {pending} request(s) across 1 bundle(s) in {wave}", flush=True)
    print(f"  [packets] after subagents write answers-*.json, run: {apply}", flush=True)
    if failed or blocked:
        print(f"  [packets] exit {status}: answers are pending AND {failed} page(s) failed and {blocked} were "
              "blocked (see the report's pages): answer this wave and run the same command again; the failed "
              "and blocked pages need their own fix", flush=True)
print(f"  wall 1.0s -> 1.0s/page effective", flush=True)
if plan.get("warnings"):
    print("  WARNING a made-up warning at the run's end - read it", flush=True)
print(f"  report: {report}", flush=True)
sys.exit(status)
'''

PROFILE = COMMON + '''
print("  probing 3 page(s) for engines: [1, 2, 3]; notation on [1, 2, 3]", flush=True)
print("  layout: 1 leaf/leaves per page, 0 running head(s) []", flush=True)
time.sleep(plan.get("classify", 0.2))
if plan.get("end") == "during-classify":
    end("kill")
print("  variants: 12 variant-form disagreement(s) in 2 pair(s); top []", flush=True)
if plan.get("end") == "packets":        # book_profile.py's lines when questions wait for answers: exit 10
    wave = Path(plan["packets"]).resolve() / "wave-01"
    print(f"[packets] PENDING 3 question(s) in {wave} (1 bundle(s)); answer and run:\\n"
          f"  {sys.executable} {Path('bin/apply_packet_answers.py').resolve()} {wave.parent} {wave}", flush=True)
    sys.exit(10)
print("  engines: distinct substitution pairs A 1 / B 0 -> adjudicate (too few pairs; ; excluded {})", flush=True)
print("  notation: 3 of 5 blocks, density bound from 0.2 marks/char", flush=True)
Path(plan["profile"]).write_text("{}\\n")
print(f"  profile: {plan['profile']} (1.5s)")        # no flush, as book_profile.py
'''

# An OCR runner as P's print: its summary line with deferred (the second
# look's with crops read too), or run_apple_vision_ocr.py's REPORT line.
RUNNER = COMMON + '''
out = Path(plan["output"])
out.mkdir(parents=True, exist_ok=True)
for page in plan["pages"]:
    time.sleep(plan.get("delay", 0.2))
    (out / f"page-{page:04d}.txt").write_text("text\\n")
    (out / f"page-{page:04d}.json").write_text("{}\\n")
    print(f"  page {page:04d}: 4 chars in 0.2s", flush=True)
for page in plan.get("failed", []):
    print(f"  page {page:04d}: FAILED stand-in page failure", file=sys.stderr, flush=True)
end(plan.get("end"))
skipped, failed = plan.get("skipped", 0), len(plan.get("failed", []))
deferred = plan["requested"] - len(plan["pages"]) - skipped - failed
report = Path(plan["report"])
report.write_text(json.dumps({"counts": {"requested": plan["requested"], "completed": len(plan["pages"]),
                                         "skippedCurrent": skipped, "blocked": 0, "failed": failed,
                                         "deferred": deferred}, "durationSeconds": 1.0,
                              "pages": [{"scanPage": page, "status": "failed", "error": "render file is missing"}
                                        for page in plan.get("failed", [])],
                              "tool": {"runner": str(Path(sys.argv[0]).resolve())}}))
if plan.get("vision"):
    print(f"REPORT {report} requested={plan['requested']} completed={len(plan['pages'])} skipped={skipped} "
          f"failed={failed} workers=2")
    sys.exit(1 if failed else 0)
crops = f" | crops read {len(plan['pages'])}" if plan.get("second_look") else ""
print(f"  requested {plan['requested']} | completed {len(plan['pages'])} | skipped {skipped} | blocked 0 | "
      f"failed {failed} | deferred {deferred}{crops}", flush=True)
print(f"  report: {report}", flush=True)
sys.exit(75 if deferred else 1 if failed else 0)
'''

PHASE = COMMON + '''
runner = sys.argv[sys.argv.index("--") + 1:]
code = plan["phase_code"] if "phase_code" in plan else subprocess.call(runner)
meaning = {0: "DONE", 75: "INCOMPLETE - time budget spent, finished pages are sealed, the server is up: "
                          "run the SAME command again",
           76: "GPU BUSY - another job holds the GPU queue; nothing was stopped"}.get(code, "FAILED")
print(f"[ocr-phase] RESULT exit={code} {meaning}", flush=True)
sys.exit(code)
'''

# stitch_tables.py's, repair_book.py's and apply_packet_answers.py's last
# lines, as they print them.
STITCH = COMMON + '''
end(plan.get("end"))
print("  1 table(s) stitched, 2 page(s) rewritten, 0 page(s) skipped", flush=True)
'''

REPAIR = COMMON + '''
if plan.get("end") == "refused":
    print(f"output refused: {plan['output']} exists (pass --resume to reuse it)", file=sys.stderr)
    sys.exit(3)
Path(plan["output"]).mkdir(parents=True, exist_ok=True)
print("repair_book: verifying the inputs", flush=True)
time.sleep(plan.get("delay", 0))
end(plan.get("end"))
code = plan.get("code", 0)
print(f"repair_book: 4 marker pages, 2 edits, 1 doubts; stages: furniture done, marks done; replay "
      f"{code != 5}; inputs unchanged True; exit {code}", flush=True)
sys.exit(code)
'''

APPLY = COMMON + '''
if plan.get("end") == "error":
    print("ERROR answers-01.json: request_id r1 occurs twice", file=sys.stderr)
    sys.exit(2)
print(f"[packets] applied 2 new answer(s) (2 submitted, 2 total in {Path(plan['packets']).resolve()}/answers.jsonl)"
      "; answered by unrecorded 2", flush=True)
'''

# The REAL book_profile.main() with its model steps stubbed (no client, no
# server): its log lines and the profile it writes are the real script's.
REAL_PROFILE = '''
import os, sys
sys.path.insert(0, os.environ["STAND_IN_SCRIPTS"])
import book_profile as bp
class NoClient:
    def __init__(self, *a, **k):
        self.model, self.effort = "stand-in", None
bp.pp.Client = NoClient
bp.page_candidates = lambda *a: [{"page": 1, "agreement": 1.0}]
bp.select_probe_pages = lambda *a: []
bp.survey_layout = lambda *a: {"leavesPerPage": 1, "runningHeads": []}
bp.collect_variants = lambda *a: ({"pairs": [], "variantOccurrences": 0, "pairSources": {}}, {})
bp.pp.draft_digest = lambda *a: "stand-in"
bp.survey_notation = lambda *a, **k: {"markBlocks": 0, "blocksSurveyed": 0, "doubled": False,
                                      "emphasis": None, "maxMarksPerCharacter": 0.1}
sys.exit(bp.main())
'''

# wait_for_run.py reading the process table as it does where there is no
# /proc (macOS): with ps and lsof.  It stops if the table is not ps's.
PS_LAUNCH = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import wait_for_run as w; "
             "w.PROC = Path(sys.argv[2]); assert isinstance(w.process_table(), w.PsTable); "
             "sys.exit(w.main(sys.argv[3:]))")
# The variables that say which model the calling agent is: a real packet
# run under a local-model agent would refuse the transport (tests/run_all.py
# leaves them out too).
AGENT_VARIABLES = ("QWEN_CODE_MODEL", "QWEN_CODE_MODEL_IDENTITY", "JYUT_OCR_AGENT_IS_LOCAL_MODEL", "PROOFREAD_MODEL")
# Real drafts for the real runs: at least 12 CJK characters a block, so the
# profile's notation survey asks a question.
REAL_TEXTS = ("甲乙丙丁戊己庚辛壬癸子丑寅卯辰。", "子丑寅卯辰巳午未申酉戌亥甲乙丙。", "東南西北春夏秋冬金木水火土日月。")


class Book:
    """A made-up book folder with stand-in scripts in bin/."""

    def __init__(self, root: Path):
        self.root = root
        (root / "bin").mkdir()
        for name, text in (("proofread_pages.py", PROOFREAD), ("book_profile.py", PROFILE),
                           ("run_paddleocr_vl.py", RUNNER), ("run_hunyuanocr_second_look.py", RUNNER),
                           ("run_apple_vision_ocr.py", RUNNER), ("run_ocr_phase.py", PHASE),
                           ("stitch_tables.py", STITCH), ("repair_book.py", REPAIR),
                           ("apply_packet_answers.py", APPLY)):
            (root / "bin" / name).write_text(textwrap.dedent(text))
        (root / "bin" / "real").mkdir()
        (root / "bin" / "real" / "book_profile.py").write_text(textwrap.dedent(REAL_PROFILE))
        (root / "renders").mkdir()
        for page in range(1, 5):
            (root / "renders" / f"page-{page:04d}.png").write_bytes(b"")
        (root / "ocr-a").mkdir()
        (root / "ocr-b").mkdir()
        # Engine B's drafts, sealed an hour ago: what the second look rewrites.
        for page in range(1, 5):
            for suffix in (".txt", ".json"):
                (root / "ocr-b" / f"page-{page:04d}{suffix}").write_text("text\n")
                os.utime(root / "ocr-b" / f"page-{page:04d}{suffix}", (time.time() - 3600,) * 2)
        self.processes: list[subprocess.Popen] = []

    def environment(self, plan: dict) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in AGENT_VARIABLES}
        return dict(env, STAND_IN_PLAN=json.dumps(plan), STAND_IN_SCRIPTS=str(ROOT / "scripts"))

    def start(self, argv: list[str], log: str, plan: dict) -> subprocess.Popen:
        """Start a stand-in the way the skill launches a background step:
        relative paths, output to LOG."""
        handle = open(self.root / log, "wb")
        process = subprocess.Popen([sys.executable] + argv, cwd=self.root, env=self.environment(plan),
                                   stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        handle.close()
        self.processes.append(process)
        return process

    def wait_for_line(self, log: str, text: str, limit: float = 10.0) -> None:
        stop = time.monotonic() + limit
        while not (self.root / log).is_file() or text not in (self.root / log).read_text(errors="replace"):
            assert time.monotonic() < stop, f"the stand-in never printed {text!r}"
            time.sleep(0.05)

    def watch(self, output: str, log: str, timeout: float = 30.0, cwd: Path | None = None,
              table: str = "proc") -> tuple[int, str, float]:
        return self.watched(self.start_watch(output, log, timeout, cwd, table), timeout)

    def start_watch(self, output: str, log: str, timeout: float = 30.0, cwd: Path | None = None,
                    table: str = "proc") -> tuple[subprocess.Popen, float]:
        """Start wait_for_run.py (from CWD, the book folder by default),
        reading /proc, or (TABLE "ps") ps and lsof as where there is none."""
        began = time.monotonic()
        launch = ([str(WAIT)] if table == "proc"
                  else ["-c", PS_LAUNCH, str(ROOT / "scripts"), str(self.root / "no-proc-here")])
        return subprocess.Popen([sys.executable, *launch, output, log, "--timeout", str(timeout),
                                 "--interval", "0.2"], cwd=cwd or self.root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True), began

    def watched(self, started: tuple[subprocess.Popen, float], timeout: float = 30.0) -> tuple[int, str, float]:
        """The exit code and line of a wait started with start_watch."""
        process, began = started
        stdout, stderr = process.communicate(timeout=timeout + 30)
        lines = stdout.splitlines()
        assert len(lines) == 1 and not stderr, \
            f"wait_for_run.py must print one line and nothing else: {stdout!r} {stderr!r}"
        assert lines[0].startswith(f"[wait] RESULT exit={process.returncode} "), lines[0]
        return process.returncode, lines[0], time.monotonic() - began

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()


def proofread_argv(extra: list[str] | None = None, output: str = "proofread") -> list[str]:
    return ["bin/proofread_pages.py", "renders", "ocr-a", output, "--draft2-directory", "ocr-b",
            "--workers", "2"] + (extra or [])


def expect(problems: list[str], name: str, got: tuple[int, str, float], code: int, *patterns: str,
           within: float | None = None) -> None:
    returned, line, seconds = got
    wrong = [p for p in patterns if not re.search(p, line)]
    if returned != code or wrong or (within is not None and seconds > within):
        problems.append(f"{name}: exit {returned} (want {code}) in {seconds:.1f}s"
                        f"{f' (want under {within}s)' if within else ''}; missing {wrong}: {line}")
    print(f"{'FAIL' if problems and problems[-1].startswith(name + ':') else 'ok  '} {name} "
          f"({seconds:.1f}s): {line}")


def cases(problems: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        book = Book(Path(tmp))
        try:
            run_cases(book, problems)
            packet_cases(book, problems)
            step_cases(book, problems)
        finally:
            book.close()


def run_cases(book: Book, problems: list[str]) -> None:
    root = book.root
    report = str(root / "proofread-run.json")

    # Finishes: the report line ends the wait, with its counts and path.  The
    # selection is every render (4 pages), as proofread_pages.py reads it.
    book.start(proofread_argv(), "p1.log", {"output": "proofread", "pages": [1, 2, 3, 4], "requested": 4,
                                            "report": report, "delay": 0.3})
    book.wait_for_line("p1.log", "proofreading")
    expect(problems, "finishes", book.watch("proofread", "p1.log"), 0, r"DONE proofread_pages\.py",
           r"4/4 pages sealed \(4 this run, [\d.]+ pages/h\)", r"0 failed, 0 blocked",
           re.escape(f"report {report}"), r"^(?!.*WARNING)", within=10)

    # Times out: still running at --timeout, with what is sealed out of how
    # many (--pages), the pages per hour and the pages that failed so far.
    long_run = book.start(proofread_argv(["--pages", "1-4", "--overwrite"]), "p2.log",
                          {"output": "proofread", "pages": [1, 2], "failed": [4], "requested": 4,
                           "report": report, "delay": 0.1, "end": "hang"})
    book.wait_for_line("p2.log", "FAILED")
    expect(problems, "times out", book.watch("proofread", "p2.log", timeout=1.5), 75,
           r"RUNNING proofread_pages\.py: 2/4 pages sealed \(2 this run, [\d.]+ pages/h\), 1 failed",
           r"last seal \d+ s ago", r"call wait_for_run\.py again", within=8)
    long_run.kill()
    long_run.wait()

    # Killed while the wait runs: said at once, not after the timeout, and an
    # earlier run's report beside the folder is not taken for this run's end.
    Path(report).write_text(json.dumps({"counts": {"requested": 4, "completed": 4}}))
    book.start(proofread_argv(["--pages", "1-3", "--overwrite"]), "p3.log",
               {"output": "proofread", "pages": [1], "requested": 3, "report": report, "delay": 1.0,
                "end": "kill"})
    book.wait_for_line("p3.log", "proofreading")
    expect(problems, "killed", book.watch("proofread", "p3.log"), 4,
           r"STOPPED proofread_pages\.py: no process runs it", r"killed or died", r"1/3 pages sealed",
           r"last log line: page 0001", r"run the SAME command again", within=10)

    # Gone before the wait starts, with no last line: stopped too.
    expect(problems, "gone before the wait", book.watch("proofread", "p3.log", timeout=1), 4,
           r"STOPPED proofread_pages\.py", within=8)

    # Crashed: the traceback's last line.
    book.start(proofread_argv(), "p4.log", {"output": "proofread", "pages": [1], "requested": 4,
                                            "report": report, "delay": 0.5, "end": "traceback"})
    book.wait_for_line("p4.log", "proofreading")
    expect(problems, "crashed", book.watch("proofread", "p4.log"), 1,
           r"FAILED proofread_pages\.py: it crashed: RuntimeError: stand-in crash", within=10)

    # Interrupted (Ctrl-C): stopped, run it again.
    book.start(proofread_argv(), "p5.log", {"output": "proofread", "pages": [1], "requested": 4,
                                            "report": report, "delay": 0.5, "end": "interrupt"})
    book.wait_for_line("p5.log", "proofreading")
    expect(problems, "interrupted", book.watch("proofread", "p5.log"), 4,
           r"STOPPED proofread_pages\.py: interrupted \(KeyboardInterrupt\)", within=10)

    # Finished with failed pages: exit 1, with the count and the report.
    book.start(proofread_argv(["--pages", "1-3"]), "p6.log",
               {"output": "proofread", "pages": [1, 2], "failed": [3], "requested": 3, "report": report,
                "delay": 0.3})
    book.wait_for_line("p6.log", "proofreading")
    # The pages and their reasons are on the line (the run report's pages[]):
    # rule set B reads no run report (E-R5).
    expect(problems, "failed pages", book.watch("proofread", "p6.log"), 1,
           r"FAILED proofread_pages\.py: finished with failed or blocked pages: 2/3 pages sealed",
           r"1 failed, 0 blocked",
           re.escape(f"report {report}") + r"; pages failed or blocked: \[failed 3: stand-in page failure\] - "
           r"the pages and why are on this line: tell the user; do not just run it again", within=10)

    # Finished with warnings (the step's stale pair classes at the start, a
    # made-up one at the end): done, and the line names both and how to read
    # them all.
    book.start(proofread_argv(["--pages", "1-2", "--overwrite"]), "p11.log",
               {"output": "proofread", "pages": [1, 2], "requested": 2, "report": report, "delay": 0.2,
                "warnings": True})
    book.wait_for_line("p11.log", "proofreading")
    expect(problems, "finished with warnings", book.watch("proofread", "p11.log"), 0,
           r"DONE proofread_pages\.py: 2/2 pages sealed",
           r"; 2 WARNING line\(s\) - read them before the next step \(grep -n WARNING .*p11\.log\)",
           r"'3 pair\(s\) are classified differently by the variant lists .*rebuild the profile "
           r"\(book_profile\.py\)'",
           r"'a made-up warning at the run's end - read it'",
           within=10)

    # A configuration error before any page: exit 1 with the ERROR.
    book.start(proofread_argv(), "p7.log", {"output": "proofread-missing", "end": "error"})
    expect(problems, "configuration error", book.watch("proofread-missing", "p7.log", timeout=3), 1,
           r"FAILED proofread_pages\.py: ERROR render directory does not exist", within=8)

    # A step that did not start - its own parser refused the arguments (the
    # REAL scripts' parsers, which exit before any file or model call), python
    # found no such script, the shell no such command - failed, since the same
    # command fails the same way; not "stopped, run the SAME command again".
    scripts = ROOT / "scripts"
    for name, command, output, patterns in (
            ("proofread with a wrong option", f"{sys.executable} {scripts}/proofread_pages.py renders ocr-a "
             "proofread --draft2-directory ocr-b --effort xhigh", "proofread",
             (r"FAILED proofread_pages\.py: it did not start: it refused its arguments: "
              r"unrecognized arguments: --effort xhigh",)),
            ("profile with a wrong option", f"{sys.executable} {scripts}/book_profile.py renders ocr-a ocr-b "
             "book-profile.json --max-inflight 8", "book-profile.json",
             (r"FAILED book_profile\.py: it did not start: it refused its arguments: "
              r"unrecognized arguments: --max-inflight 8",)),
            ("no such script", f"{sys.executable} scripts/proofread_pages.py renders ocr-a proofread",
             "proofread", (r"FAILED proofread_pages\.py: it did not start: python cannot open "
                           r".*scripts/proofread_pages\.py",)),
            ("no such command (bash)", "bash -c 'no-such-python3 bin/proofread_pages.py renders ocr-a proofread'",
             "proofread", (r"FAILED proofread_pages\.py: it did not start: the shell says no-such-python3: "
                           r"command not found",)),
            ("no such command (sh)", "sh -c 'no-such-python3 bin/proofread_pages.py renders ocr-a proofread'",
             "proofread", (r"FAILED proofread_pages\.py: it did not start: the shell says no-such-python3: "
                           r"(command )?not found",))):
        log = re.sub(r"\W+", "-", name) + ".log"
        subprocess.run(["bash", "-c", f"{command} > {log} 2>&1"], cwd=root, env=dict(os.environ),
                       stdin=subprocess.DEVNULL, timeout=60)
        expect(problems, name, book.watch(output, log, timeout=3), 1, *patterns,
               r"the same command fails the same way", within=8)

    # Profile: its step while running, then done with its profile line.
    profile_argv = ["bin/book_profile.py", "renders", "ocr-a", "ocr-b", "book-profile.json", "--workers", "8"]
    book.start(profile_argv, "b1.log", {"profile": str(root / "book-profile.json"), "classify": 3.0})
    book.wait_for_line("b1.log", "layout:")
    expect(problems, "profile running", book.watch("book-profile.json", "b1.log", timeout=1), 75,
           r"RUNNING book_profile\.py: step 3 of 5 \(classifying variant pairs\)", within=8)
    expect(problems, "profile done", book.watch("book-profile.json", "b1.log"), 0,
           r"DONE book_profile\.py: profile .*book-profile\.json written in 2 s$", within=15)

    # Profile killed before its profile line: the step did not finish.
    book.start(profile_argv[:4] + ["other-profile.json"] + profile_argv[5:], "b2.log",
               {"profile": str(root / "other-profile.json"), "classify": 1.0, "end": "during-classify"})
    book.wait_for_line("b2.log", "layout:")
    expect(problems, "profile killed", book.watch("other-profile.json", "b2.log"), 4,
           r"STOPPED book_profile\.py", r"run the SAME command again", within=10)

    # The real book_profile.py with its output unbuffered (python3 -u), as an
    # agent may launch it: its last line is the profile's, and the step is done.
    real = ["-u", "bin/real/book_profile.py", "renders", "ocr-a", "ocr-b", "real-profile.json"]
    book.start(real, "b4.log", {}).wait()
    expect(problems, "real profile", book.watch("real-profile.json", "b4.log", timeout=3), 0,
           r"DONE book_profile\.py: profile .*real-profile\.json written", within=8)

    # A proofread run that only READS the profile (--profile) is not the
    # profile being built: the finished profile is reported done at once.
    reader = book.start(proofread_argv(["--profile", "book-profile.json"]), "p8.log",
                        {"output": "proofread", "pages": [], "requested": 4, "report": report,
                         "end": "hang"})
    book.wait_for_line("p8.log", "proofreading")
    expect(problems, "reader of the profile", book.watch("book-profile.json", "b1.log", timeout=20), 0,
           r"DONE book_profile\.py", within=5)
    reader.kill()
    reader.wait()

    # OCR under run_ocr_phase.py: the runner's pages, then the phase's RESULT.
    ocr_report = str(root / "paddleocr-vl-run.json")
    runner = ["bin/run_paddleocr_vl.py", "renders", "ocr-a"]
    book.start(["bin/run_ocr_phase.py", "--", sys.executable] + runner + ["--pages", "1-2"], "o1.log",
               {"output": "ocr-a", "pages": [1, 2], "requested": 2, "report": ocr_report, "delay": 0.3})
    book.wait_for_line("o1.log", "page 0001")
    expect(problems, "OCR done", book.watch("ocr-a", "o1.log"), 0,
           r"DONE run_paddleocr_vl\.py: 2/2 pages sealed \(2 this run, [\d.]+ pages/h\)",
           re.escape(f"report {ocr_report}"), within=10)
    book.start(["bin/run_ocr_phase.py", "--", sys.executable] + runner, "o2.log",
               {"output": "ocr-a", "pages": [3], "skipped": 2, "requested": 4, "report": ocr_report,
                "delay": 0.3})
    book.wait_for_line("o2.log", "page 0003")
    expect(problems, "OCR budget spent", book.watch("ocr-a", "o2.log"), 4,
           r"STOPPED run_paddleocr_vl\.py: the OCR call spent its time budget", r"3/4 pages sealed",
           r"run the SAME command again", within=10)
    book.start(runner + ["--pages", "1-4"], "o3.log",
               {"output": "ocr-a", "pages": [4], "requested": 4, "report": ocr_report, "delay": 0.2,
                "end": "hang"})
    book.wait_for_line("o3.log", "page 0004")
    expect(problems, "OCR running", book.watch("ocr-a", "o3.log", timeout=1), 75,
           r"RUNNING run_paddleocr_vl\.py: 4/4 pages sealed \(1 this run", within=8)

    # A process whose arguments name no known script but that writes the log
    # is still running, and a shell that only carries the command in one
    # string is not the step.
    with open(root / "x1.log", "wb") as handle:
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=root,
                                 stdout=handle, stderr=subprocess.STDOUT)
    book.processes.append(other)
    expect(problems, "writer of the log", book.watch("elsewhere", "x1.log", timeout=1), 75, r"RUNNING",
           within=8)
    other.kill()
    other.wait()
    shell = subprocess.Popen(["sh", "-c", f"{sys.executable} bin/proofread_pages.py renders ocr-a proofread "
                              "> p9.log 2>&1; sleep 30"], cwd=root, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             env=dict(os.environ, STAND_IN_PLAN=json.dumps(
                                 {"output": "proofread", "pages": [1], "requested": 4, "report": report})))
    book.processes.append(shell)
    try:
        book.wait_for_line("p9.log", "report:")
        expect(problems, "shell wrapper", book.watch("proofread", "p9.log"), 0, r"DONE proofread_pages\.py",
               within=5)
    finally:
        os.killpg(shell.pid, signal.SIGKILL)     # the shell and its sleep
        shell.wait()

    # The wrong log given: the file the run writes is read (and named while
    # it runs), so a finished run is done, not stopped.
    book.start(proofread_argv(["--pages", "1-2"]), "p10.log",
               {"output": "proofread", "pages": [1, 2], "requested": 2, "report": report, "delay": 1.2})
    book.wait_for_line("p10.log", "proofreading")
    expect(problems, "wrong log, running", book.watch("proofread", "wrong.log", timeout=0.5), 75,
           r"RUNNING proofread_pages\.py", r"its output goes to .*p10\.log, not .*wrong\.log", within=8)
    expect(problems, "wrong log, finished", book.watch("proofread", "wrong.log"), 0,
           r"DONE proofread_pages\.py: 2/2 pages sealed", within=10)

    # No process and no log: nothing is guessed (a finished run would look
    # killed), whether the output exists or not.
    expect(problems, "no log", book.watch("proofread", "no-such.log", timeout=1), 2,
           r"CANNOT TELL: .*no-such\.log does not exist", within=8)
    expect(problems, "nothing to watch", book.watch("no-such-dir", "no-such.log", timeout=1), 2,
           r"CANNOT TELL", within=8)


def packet_cases(book: Book, problems: list[str]) -> None:
    """P's packet transport, the failures behind it and the report the line
    falls back on: never DONE while a page failed or waits on answers."""
    root = book.root
    report = str(root / "packet-run.json")
    wave = re.escape(str((root / "packets").resolve() / "wave-01"))
    apply = r"run \S+ \S*bin/apply_packet_answers\.py \S*packets " + wave + r", then run the SAME command again"

    # Answers pending (exit 10): PENDING with the wave and the command, the
    # pages sealed so far, not DONE.
    book.start(proofread_argv(["--pages", "1-4", "--overwrite"]), "k1.log",
               {"output": "proofread", "pages": [1, 2], "pending": 2, "requested": 4, "report": report})
    book.wait_for_line("k1.log", "proofreading")
    expect(problems, "packets pending (exit 10)", book.watch("proofread", "k1.log"), 10,
           r"PENDING proofread_pages\.py: packet answers wait \(2 question\(s\) in 1 bundle\(s\) in " + wave + r"\)",
           r"2/4 pages sealed \(2 this run", r"0 failed, 0 blocked, 2 pending", apply, r"sealed pages are kept",
           r"^(?!.*DONE)", within=10)

    # Pending beside a blocked page, and beside a failed one (exit 11): the
    # wave first, and the pages that running it again does not fix.
    for name, plan in (("packets pending and a blocked page (exit 11)", {"blocked": 1, "pages": [1, 2]}),
                       ("packets pending and a failed page (exit 11)", {"failed": [3], "pages": [1]})):
        log = re.sub(r"\W+", "-", name) + ".log"
        book.start(proofread_argv(["--pages", "1-4"]), log,
                   {"output": "proofread", "pending": 1, "requested": 4, "report": report, **plan})
        book.wait_for_line(log, "proofreading")
        expect(problems, name, book.watch("proofread", log), 11,
               r"PENDING proofread_pages\.py: packet answers wait \(1 question\(s\)",
               r"AND pages failed or were blocked",
               rf"{len(plan.get('failed', []))} failed, {plan.get('blocked', 0)} blocked, 1 pending", apply,
               r"fix the failed and blocked pages from the report's pages\[\], running it again does not fix them",
               (r"\[blocked 90: existing output rejected: stand-in; use --overwrite\]" if plan.get("blocked")
                else r"\[failed 3: stand-in page failure\]"), within=10)

    # No summary line (a format the wait does not read): the report's
    # counts decide - failed pages are FAILED, pending ones PENDING with the
    # report's packetWave, never DONE.
    book.start(proofread_argv(["--pages", "1-3"]), "k4.log",
               {"output": "proofread", "pages": [1, 2], "failed": [3], "requested": 3, "report": report,
                "summary": False})
    book.wait_for_line("k4.log", "proofreading")
    expect(problems, "no summary line: the report's failed pages", book.watch("proofread", "k4.log"), 1,
           r"FAILED proofread_pages\.py: finished with failed or blocked pages: 2/3 pages sealed",
           r"1 failed, 0 blocked", within=10)
    book.start(proofread_argv(["--pages", "1-4"]), "k5.log",
               {"output": "proofread", "pages": [1, 2], "pending": 2, "requested": 4, "report": report,
                "summary": False, "wave_lines": False})
    book.wait_for_line("k5.log", "proofreading")
    expect(problems, "no summary line: the report's pending pages", book.watch("proofread", "k5.log"), 10,
           r"PENDING proofread_pages\.py: packet answers wait \(2 question\(s\) in 1 bundle\(s\) in " + wave + r"\)",
           r"2 pending", apply, within=10)
    # Neither a summary line nor a report it can read: not taken for done.
    book.start(proofread_argv(["--pages", "1-2"]), "k6.log",
               {"output": "proofread", "pages": [1, 2], "requested": 2, "report": str(root / "garbled.json"),
                "summary": False, "report_text": "not json"})
    book.wait_for_line("k6.log", "proofreading")
    expect(problems, "no summary line and no report", book.watch("proofread", "k6.log"), 2,
           r"CANNOT TELL proofread_pages\.py: its log names the report .*garbled\.json, which cannot be read",
           r"do not take the step for done", within=10)

    # The profile's questions pending (book_profile.py exit 10): PENDING,
    # not "stopped, run the SAME command again".
    book.start(["bin/book_profile.py", "renders", "ocr-a", "ocr-b", "pending-profile.json"], "k7.log",
               {"profile": str(root / "pending-profile.json"), "end": "packets", "packets": "profile-packets"})
    book.wait_for_line("k7.log", "variants:")
    wave = re.escape(str((root / "profile-packets").resolve() / "wave-01"))
    expect(problems, "profile pending (exit 10)", book.watch("pending-profile.json", "k7.log"), 10,
           r"PENDING book_profile\.py: packet answers wait \(3 question\(s\) in 1 bundle\(s\) in " + wave + r"\)",
           r"no profile written yet", r"run \S+ \S*bin/apply_packet_answers\.py \S*profile-packets " + wave,
           r"then run the SAME command again$", within=10)

    # The answers applied: done, and the step that wrote the wave runs again.
    (root / "packets" / "wave-01").mkdir(parents=True, exist_ok=True)
    book.start(["bin/apply_packet_answers.py", "packets", "packets/wave-01"], "k8.log", {"packets": "packets"})
    expect(problems, "answers applied", book.watch("packets", "k8.log"), 0,
           r"DONE apply_packet_answers\.py: applied 2 new answer\(s\)",
           r"now run the step that wrote the wave again, the SAME command", within=10)
    book.start(["bin/apply_packet_answers.py", "packets", "packets/wave-01"], "k9.log",
               {"packets": "packets", "end": "error"})
    expect(problems, "answers refused", book.watch("packets", "k9.log"), 1,
           r"FAILED apply_packet_answers\.py: ERROR answers-01\.json: request_id r1 occurs twice", within=10)


def step_cases(book: Book, problems: list[str]) -> None:
    """P's other long steps: an OCR runner's failed page, the second look,
    Apple Vision, a busy GPU, the table stitch and the book repair.  Their
    own OCR folders: run_cases leaves a runner running on ocr-a."""
    root = book.root
    ocr_report = str(root / "ocr-run.json")

    # An OCR runner that finished with a failed page (exit 1), run directly.
    book.start(["bin/run_paddleocr_vl.py", "renders", "ocr-a2", "--pages", "1-2"], "s1.log",
               {"output": "ocr-a2", "pages": [1], "failed": [2], "requested": 2, "report": ocr_report})
    book.wait_for_line("s1.log", "page 0001")
    expect(problems, "OCR failed page", book.watch("ocr-a2", "s1.log"), 1,
           r"FAILED run_paddleocr_vl\.py: finished with failed or blocked pages: 1/2 pages sealed",
           r"1 failed, 0 blocked", r"; pages failed or blocked: \[failed 2: render file is missing\]", within=10)

    # The GPU busy with another job (run_ocr_phase.py exit 76): stopped,
    # nothing run, the same command again once that job is done.
    book.start(["bin/run_ocr_phase.py", "--", sys.executable, "bin/run_paddleocr_vl.py", "renders", "ocr-a3"],
               "s2.log", {"phase_code": 76})
    expect(problems, "GPU busy (OCR phase exit 76)", book.watch("ocr-a3", "s2.log"), 4,
           r"STOPPED .*: the GPU is busy with another job \(run_ocr_phase\.py exit 76 GPU BUSY",
           r"nothing was stopped and no page was run", r"run the SAME command again", within=10)

    # The second look rewrites engine B's sealed drafts: only the pages it
    # rewrote in this run count, not the four sealed before it.
    second = ["bin/run_hunyuanocr_second_look.py", "renders", "ocr-a", "ocr-b"]
    look = book.start(second, "s3.log", {"output": "ocr-b", "pages": [2], "requested": 4, "skipped": 3,
                                         "report": ocr_report, "end": "hang"})
    book.wait_for_line("s3.log", "page 0002")
    expect(problems, "second look running", book.watch("ocr-b", "s3.log", timeout=1), 75,
           r"RUNNING run_hunyuanocr_second_look\.py: 1 pages rewritten \(1 this run", r"^(?!.*4/4)", within=8)
    look.kill()
    look.wait()
    book.start(second, "s4.log", {"output": "ocr-b", "pages": [3], "requested": 4, "skipped": 3,
                                  "report": ocr_report, "second_look": True})
    book.wait_for_line("s4.log", "page 0003")
    expect(problems, "second look done", book.watch("ocr-b", "s4.log"), 0,
           r"DONE run_hunyuanocr_second_look\.py: 4/4 pages sealed \(1 this run", within=10)

    # Apple Vision's REPORT line ends its run.
    book.start(["bin/run_apple_vision_ocr.py", "renders", "ocr-v", "--pages", "1-2"], "s5.log",
               {"output": "ocr-v", "pages": [1, 2], "requested": 2, "report": ocr_report, "vision": True})
    book.wait_for_line("s5.log", "page 0002")
    expect(problems, "Apple Vision done", book.watch("ocr-v", "s5.log"), 0,
           r"DONE run_apple_vision_ocr\.py: 2/2 pages sealed \(2 this run", re.escape(f"report {ocr_report}"),
           within=10)

    # The table stitch (its parser is built inside its main()): running,
    # then done with its line; its folder is --output-dir.
    stitch = ["bin/stitch_tables.py", "proofread", "--renders", "renders", "--drafts", "ocr-a", "--output-dir",
              "stitched"]
    held = book.start(stitch, "s6.log", {"end": "hang"})
    expect(problems, "stitch running", book.watch("stitched", "s6.log", timeout=1.5), 75,
           r"RUNNING stitch_tables\.py: nothing printed yet", within=8)
    held.kill()
    held.wait()
    book.start(stitch, "s7.log", {})
    expect(problems, "stitch done", book.watch("stitched", "s7.log"), 0,
           r"DONE stitch_tables\.py: 1 table\(s\) stitched, 2 page\(s\) rewritten", within=10)

    # The book repair: done; refused before it repaired anything; a replay
    # that differs (exit 5); killed, which needs --resume.
    repair = ["bin/repair_book.py", ".", "--output", "repaired"]
    for name, plan, code, patterns in (
            ("repair done", {}, 0, (r"DONE repair_book\.py: 4 marker pages, 2 edits, 1 doubts",
                                    r"output .*repaired$")),
            ("repair refused", {"end": "refused"}, 1,
             (r"FAILED repair_book\.py: it repaired nothing: output refused: .*repaired exists",)),
            ("repair replay differs", {"code": 5}, 1,
             (r"FAILED repair_book\.py: exit 5: .*replay False", r"repair-report\.json")),
            ("repair killed", {"end": "kill", "delay": 1.0}, 4,
             (r"STOPPED repair_book\.py: no process runs it", r"run the same command again with --resume"))):
        log = re.sub(r"\W+", "-", name) + ".log"
        book.start(repair, log, {"output": str(root / "repaired"), **plan})
        if plan.get("delay"):
            book.wait_for_line(log, "verifying")
        expect(problems, name, book.watch("repaired", log), code, *patterns, within=10)


def real_book(root: Path) -> Path:
    """A made-up book with real renders and engine A's drafts, for the real
    scripts: renders/, ocr-paddle/ (page 3's draft only in ocr-paddle, not
    in ocr-missing/), ocr-hunyuan/ and ocr-none/ (no drafts)."""
    from PIL import Image

    for name in ("renders", "ocr-paddle", "ocr-hunyuan", "ocr-missing", "ocr-none"):
        (root / name).mkdir(parents=True)
    for scan, text in enumerate(REAL_TEXTS, 1):
        stem = f"page-{scan:04d}"
        Image.new("L", (1000, 1400), 255).save(root / "renders" / f"{stem}.png")
        block = {"engine": "paddleocr-vl", "renderWidth": 1000, "renderHeight": 1400,
                 "lines": [{"text": text, "boundingBox": [0.1, 0.1, 0.8, 0.1], "blockLabel": "text"}]}
        for folder in ("ocr-paddle",) + (("ocr-missing",) if scan != 3 else ()):
            (root / folder / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
            (root / folder / f"{stem}.json").write_text(json.dumps(block, ensure_ascii=False), encoding="utf-8")
        (root / "ocr-hunyuan" / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
    return root


def real_cases(problems: list[str]) -> None:
    """The real proofread_pages.py, book_profile.py and apply_packet_answers.py
    with the packet transport, no model: what P's scripts print now."""
    with tempfile.TemporaryDirectory() as tmp:
        book = Book(Path(tmp))
        try:
            real_runs(book, real_book(book.root / "real"), problems)
        finally:
            book.close()


def fenced(text: str) -> str:
    """The text a question fences with --- lines: what it hands over (the
    page's draft, for a transcription), taken as its answer."""
    match = re.search(r"---\n(.*?)\n---", text, re.S)
    return match.group(1) if match else ""


def real_runs(book: Book, real: Path, problems: list[str]) -> None:
    scripts = ROOT / "scripts"
    proofread = [str(scripts / "proofread_pages.py"), "real/renders", "real/ocr-paddle", "real/proofread",
                 "--single-engine", "--backend", "packet", "--workers", "1", "--packets-dir", "real/packets"]
    wave = re.escape(str(real / "packets" / "wave-01"))

    # Questions pending: PENDING, with the command the real run printed.
    book.start(proofread, "r1.log", {}).wait()
    expect(problems, "real proofread: answers pending", book.watch("real/proofread", "r1.log", timeout=5), 10,
           r"PENDING proofread_pages\.py: packet answers wait \(3 question\(s\) in 3 bundle\(s\) in " + wave + r"\)",
           r"0/3 pages sealed", r"3 pending", r"run \S+ \S*scripts/apply_packet_answers\.py \S*real/packets " + wave,
           within=8)

    # Answered (each question with the text it hands over) and applied,
    # with the real apply_packet_answers.py: done.
    for bundle in sorted((real / "packets" / "wave-01").glob("bundle-*.json")):
        data = json.loads(bundle.read_text(encoding="utf-8"))
        answers = [{"request_id": item["request_id"], "answer": fenced(item["user_text"])} for item in data["items"]]
        Path(data["expected_answers_file"]).write_text(json.dumps({"answers": answers}, ensure_ascii=False),
                                                       encoding="utf-8")
    book.start([str(scripts / "apply_packet_answers.py"), "real/packets", "real/packets/wave-01"], "r2.log", {}).wait()
    expect(problems, "real answers applied", book.watch("real/packets", "r2.log", timeout=5), 0,
           r"DONE apply_packet_answers\.py: applied 3 new answer\(s\)", within=8)

    # The same command again: every page sealed, done.
    book.start(proofread, "r3.log", {}).wait()
    expect(problems, "real proofread: done", book.watch("real/proofread", "r3.log", timeout=5), 0,
           r"DONE proofread_pages\.py: 3/3 pages sealed \(3 this run", r"0 failed, 0 blocked;", within=8)

    # A page with no draft fails beside the pending ones (exit 11).
    missing = [str(scripts / "proofread_pages.py"), "real/renders", "real/ocr-missing", "real/proofread-missing",
               "--single-engine", "--backend", "packet", "--workers", "1", "--packets-dir", "real/packets-missing"]
    book.start(missing, "r4.log", {}).wait()
    expect(problems, "real proofread: pending and a failed page", book.watch("real/proofread-missing", "r4.log",
                                                                             timeout=5), 11,
           r"PENDING proofread_pages\.py: packet answers wait \(2 question\(s\)", r"AND pages failed or were blocked",
           r"1 failed, 0 blocked, 2 pending", within=8)

    # No page has a draft: every page failed, nothing pending (exit 1).
    none = [str(scripts / "proofread_pages.py"), "real/renders", "real/ocr-none", "real/proofread-none",
            "--single-engine", "--backend", "packet", "--workers", "1", "--packets-dir", "real/packets-none"]
    book.start(none, "r5.log", {}).wait()
    expect(problems, "real proofread: every page failed", book.watch("real/proofread-none", "r5.log", timeout=5), 1,
           r"FAILED proofread_pages\.py: finished with failed or blocked pages: 0/3 pages sealed",
           r"3 failed, 0 blocked", r"; pages failed or blocked: \[failed 1, 2, 3: missing engine A's draft\]",
           r"tell the user; do not just run it again", within=8)

    # The real profile's questions pending.
    profile = [str(scripts / "book_profile.py"), "real/renders", "real/ocr-paddle", "real/ocr-hunyuan",
               "real/book-profile.json", "--backend", "packet", "--packets-dir", "real/profile-packets"]
    book.start(profile, "r6.log", {}).wait()
    expect(problems, "real profile: answers pending", book.watch("real/book-profile.json", "r6.log", timeout=5), 10,
           r"PENDING book_profile\.py: packet answers wait \(\d+ question\(s\) in \d+ bundle\(s\) in "
           + re.escape(str(real / "profile-packets" / "wave-01")) + r"\)",
           r"run \S+ \S*scripts/apply_packet_answers\.py", within=8)


def ps_cases(problems: list[str]) -> None:
    """The wait with no /proc (macOS): ps for the processes, lsof for where
    each works and writes.  Fresh output folders: ps gives start times to
    the second."""
    if shutil.which("ps") is None or shutil.which("lsof") is None:
        print("ok   the process table without /proc: not checked here (no ps or no lsof on PATH)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        book = Book(Path(tmp))
        try:
            ps_runs(book, problems)
        finally:
            book.close()


def ps_runs(book: Book, problems: list[str]) -> None:
    root = book.root
    report = str(root / "ps-run.json")
    book.start(proofread_argv(["--pages", "1-4"]), "q1.log",
               {"output": "proofread", "pages": [1, 2, 3, 4], "requested": 4, "report": report, "delay": 0.3})
    book.wait_for_line("q1.log", "proofreading")
    expect(problems, "ps: finishes", book.watch("proofread", "q1.log", table="ps"), 0,
           r"DONE proofread_pages\.py: 4/4 pages sealed \(4 this run", within=15)

    # Running, with its own selection read (relative paths against its
    # working directory, by lsof) and its failed page.
    long_run = book.start(proofread_argv(["--pages", "1-4"], "proofread-ps"), "q2.log",
                          {"output": "proofread-ps", "pages": [1, 2], "failed": [4], "requested": 4,
                           "report": report, "delay": 0.1, "end": "hang"})
    book.wait_for_line("q2.log", "FAILED")
    expect(problems, "ps: times out", book.watch("proofread-ps", "q2.log", timeout=1.5, table="ps"), 75,
           r"RUNNING proofread_pages\.py: 2/4 pages sealed \(2 this run, [\d.]+ pages/h\), 1 failed", within=12)
    # The wrong log given: the file its output goes to (by lsof) is named.
    expect(problems, "ps: wrong log, running", book.watch("proofread-ps", "wrong.log", timeout=1, table="ps"), 75,
           r"RUNNING proofread_pages\.py", r"its output goes to .*q2\.log, not .*wrong\.log", within=12)
    long_run.kill()
    long_run.wait()

    # Killed: said at once.
    book.start(proofread_argv(["--pages", "1-3"], "proofread-ps2"),
               "q3.log", {"output": "proofread-ps2", "pages": [1], "requested": 3, "report": report, "delay": 1.0,
                          "end": "kill"})
    book.wait_for_line("q3.log", "proofreading")
    expect(problems, "ps: killed", book.watch("proofread-ps2", "q3.log", table="ps"), 4,
           r"STOPPED proofread_pages\.py: no process runs it", r"1/3 pages sealed", within=15)

    # A process that names no known script but writes the log.
    with open(root / "q4.log", "wb") as handle:
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=root,
                                 stdout=handle, stderr=subprocess.STDOUT)
    book.processes.append(other)
    expect(problems, "ps: writer of the log", book.watch("elsewhere", "q4.log", timeout=1, table="ps"), 75,
           r"RUNNING", within=12)
    other.kill()
    other.wait()

    # A packet run pending: the same line as with /proc.
    book.start(proofread_argv(["--pages", "1-4"]), "q5.log",
               {"output": "proofread", "pages": [1], "pending": 3, "requested": 4, "report": report})
    book.wait_for_line("q5.log", "proofreading")
    expect(problems, "ps: packets pending", book.watch("proofread", "q5.log", table="ps"), 10,
           r"PENDING proofread_pages\.py: packet answers wait \(3 question\(s\)", within=15)


def code_checks(problems: list[str], wait_for_run) -> None:
    """The wait's codes and steps: the packet transport's exit codes, P's
    long steps, and each exit code in its docstring."""
    import _packet_client
    wanted = {
        "exit 10 and 11 are the packet transport's":
            (wait_for_run.EXIT_PENDING, wait_for_run.EXIT_PENDING_FAILED)
            == (_packet_client.EXIT_PACKETS_PENDING, _packet_client.EXIT_PACKETS_PENDING_WITH_FAILURES),
        "it knows P's long steps": all(name in wait_for_run.SCRIPTS for name in (
            "proofread_pages.py", "book_profile.py", "run_hunyuanocr.py", "run_paddleocr_vl.py",
            "run_hunyuanocr_second_look.py", "run_apple_vision_ocr.py", "ocr_book.py", "stitch_tables.py",
            "repair_book.py", "apply_packet_answers.py", "finish_book.py")),
        "every step it names is a script here":
            all((ROOT / "scripts" / name).is_file() for name in wait_for_run.SCRIPTS),
        "ocr_book.py's folder is the argument its parser names":
            "book" in vars(__import__("ocr_book").build_parser().parse_args(["BOOK"]))
            and "book" in wait_for_run.SCRIPTS["ocr_book.py"][2],
        "a script without build_parser() has its arguments declared in the wait":
            all(hasattr(__import__(module), "build_parser") or module in wait_for_run.OWN_PARSERS
                for name, (_, module, _) in wait_for_run.SCRIPTS.items() if (ROOT / "scripts" / name).is_file()),
        "its docstring documents each exit code":
            all(re.search(rf"^\s+{code}\s", wait_for_run.__doc__ or "", re.M) for code in (
                wait_for_run.EXIT_DONE, wait_for_run.EXIT_FAILED, wait_for_run.EXIT_USAGE, wait_for_run.EXIT_STOPPED,
                wait_for_run.EXIT_PENDING, wait_for_run.EXIT_PENDING_FAILED, wait_for_run.EXIT_RUNNING)),
    }
    for name, ok in wanted.items():
        if not ok:
            problems.append(f"wait_for_run.py: {name}")
        print(f"{'ok  ' if ok else 'FAIL'} wait_for_run.py: {name}")


def asks_no_model(problems: list[str]) -> None:
    """The wait must cost the server nothing: no HTTP, no socket, no client."""
    source = WAIT.read_text(encoding="utf-8")
    found = [word for word in ("urllib", "http", "socket", "Client(", "ask(", "requests") if word in source]
    if found:
        problems.append(f"wait_for_run.py mentions {found}: it must ask no model and open no connection")
    print(f"{'FAIL' if found else 'ok  '} wait_for_run.py opens no connection: {found or 'none of'} "
          "urllib/http/socket/Client/ask/requests")


def skill_text(problems: list[str], wait_for_run) -> None:
    """What SKILL.md tells the agent about a long step in the background:
    wait with wait_for_run.py instead of reading logs and pages, with the
    measured cost that says why, every exit code the script has and no other,
    and the rules that were there before still there."""
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    section = skill.split("\n## 四、", 1)[-1].split("\n## 六、", 1)[0]
    bullet = skill.split("\n## 五、", 1)[-1].split("\n## ", 1)[0] if "\n## 五、" in skill else ""
    codes = {int(code) for code in re.findall(r"\*\*exit (\d+)\*\*", bullet)}

    def item(code: int) -> str:
        """The bullet's line on exit CODE."""
        return next((line for line in bullet.splitlines() if line.strip().startswith(f"- **exit {code}**")), "")
    script_codes = {wait_for_run.EXIT_DONE, wait_for_run.EXIT_FAILED, wait_for_run.EXIT_USAGE,
                    wait_for_run.EXIT_STOPPED, wait_for_run.EXIT_PENDING, wait_for_run.EXIT_PENDING_FAILED,
                    wait_for_run.EXIT_RUNNING}
    wanted = {
        # How to wait, and not to watch.
        "the wait command in the section on long steps":
            '"$JYUT_OCR_PYTHON" scripts/wait_for_run.py OUTPUT LOG' in bullet,
        "the tool call's timeout 600000": "600000" in bullet,
        f"the default wait of {wait_for_run.DEFAULT_TIMEOUT:.0f} s":
            f"{wait_for_run.DEFAULT_TIMEOUT:.0f} 秒" in bullet,
        "not reading the log": "唔好 tail log" in bullet,
        "not reading sealed pages": "唔好讀已封存嘅頁" in bullet,
        "calling it again at once on exit 75": "即刻再叫同一個 command" in bullet,
        "a short conversation before a long step": "對話保持短" in bullet and "唔好將 log" in bullet,
        # The measured cost, plainly, in the reference.
        "the turns and tokens measured": all(n in workflow for n in ("84 輪", "11 萬", "930 萬")),
        "the server share measured": "三成" in workflow,
        "the pages per hour measured": "7 頁" in workflow and "17–21 頁" in workflow,
        "every exit code of the script and no other": codes == script_codes,
        # A step that did not start is exit 1: fix the command, not run it
        # again; exit 2 is only wait_for_run.py's own arguments and paths.
        "exit 1 covers a step that did not start":
            all(word in item(wait_for_run.EXIT_FAILED) for word in ("did not start", "開唔到工", "改好 command")),
        # The failed pages and their reasons are on the line: rule set B
        # reads it alone and tells the person; only rule set A reads the log.
        "exit 1: the line names the pages and why, rule set B reads only it":
            all(word in item(wait_for_run.EXIT_FAILED) for word in (
                "嗰行列出 failed／blocked 頁同原因", "A 套再睇 `tail -20 LOG`", "B 套淨係讀嗰行", "頁碼同原因話畀用戶知")),
        "exit 2 is wait_for_run.py's own arguments":
            "`wait_for_run.py` 自己嘅參數" in item(wait_for_run.EXIT_USAGE),
        # The step's own warnings, which the agent no longer sees in the log,
        # are read once it is done.
        "on exit 0 the WARNING lines are read first":
            all(word in item(wait_for_run.EXIT_DONE) for word in ("`WARNING`", "grep -n WARNING LOG")),
        # The rules that were there stay.
        "the handover in the foreground": "GPU 交接一定要喺前景行" in section,
        "no run_ocr_phase.py while a step runs": "背景跑緊嘅時候唔好開任何 `run_ocr_phase.py`" in section,
        "Qwen Code kills background shells on exit": "Qwen Code 退出會殺晒背景 shell" in section,
        "the old polling gone": "定時睇 log" not in skill,
        # The run sheet's steps (四) name the wait by the script alone; 五
        # gives its interpreter.  The OCR phase is one ocr_book.py run.
        "the long steps waited on with it": all(f"`wait_for_run.py {args}` 等" in section for args in (
            "BOOK BOOK/ocr.log", "BOOK/book-profile.json BOOK/profile.log",
            "BOOK/proofread BOOK/proofread.log --progress BOOK", "BOOK BOOK/finish.log")),
    }
    missing = [name for name, ok in wanted.items() if not ok]
    for name in missing:
        problems.append(f"SKILL.md: {name}"
                        + (f" (SKILL.md names {sorted(codes)}, the script {sorted(script_codes)})"
                           if name.startswith("every exit code") else ""))
    print(f"{'FAIL' if missing else 'ok  '} SKILL.md on waiting for a long step: "
          f"{len(wanted) - len(missing)} of {len(wanted)} rules")


def main() -> int:
    problems: list[str] = []
    if not WAIT.is_file():
        print(f"FAIL {WAIT.relative_to(ROOT)} does not exist")
        return 1
    import wait_for_run
    if wait_for_run.DEFAULT_TIMEOUT >= 600:
        problems.append("the default wait must stay under Qwen Code's 600 s shell cap")
    skill_text(problems, wait_for_run)
    code_checks(problems, wait_for_run)
    asks_no_model(problems)
    cases(problems)
    real_cases(problems)
    ps_cases(problems)
    for problem in problems:
        print("FAIL", problem)
    print(f"{'FAIL' if problems else 'ok  '} wait_for_run.py: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
