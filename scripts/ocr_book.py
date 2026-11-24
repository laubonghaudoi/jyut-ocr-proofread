#!/usr/bin/env python3
"""The whole OCR phase of one book folder, as one resumable command.

    python3 scripts/ocr_book.py BOOK
    python3 scripts/run_ocr_phase.py -- python3 scripts/ocr_book.py BOOK   # GPU hand-over

In this order, under one deadline (OCR_PHASE_DEADLINE, which run_ocr_phase.py
sets, or --max-seconds):

With --engine apple-vision, only Apple Vision runs, with no downloaded model,
second look or model hand-over. Use --single-engine in proofread_pages.py and
finish_book.py afterwards. The hunyuan-paddle route runs these steps:

1. engine B, HunyuanOCR: scripts/run_hunyuanocr.py BOOK/renders BOOK/ocr-hunyuan;
2. the early draft for the user, scripts/build_draft.py BOOK, as soon as engine
   B has read every page (again only when engine B sealed pages in this call or
   there is no draft yet); a draft that fails does not stop the phase.  Once
   it is written, one line says where the person reads it:
   "[ocr-book] DRAFT X-draft.md's path" (the copy beside the PDF), or
   "[ocr-book] DRAFT BOOK/draft.md's path (not copied beside the PDF: why)";
   scripts/wait_for_run.py's line ends with it too.  The agent tells the
   person that path at once (SKILL.md, step 2), without waiting for engine A;
3. engine A, PaddleOCR-VL: scripts/run_paddleocr_vl.py BOOK/renders BOOK/ocr-paddle;
4. engine B's second look: scripts/run_hunyuanocr_second_look.py BOOK/renders
   BOOK/ocr-paddle BOOK/ocr-hunyuan.

Each runner skips the pages it sealed before, so the same command, run again,
carries on where the last one stopped.  Under run_ocr_phase.py one window
covers all of them: the agent's model is stopped and started once per call,
not once per engine. Linux/WSL2 also supports --engine hunyuan (or the
legacy --single-engine flag), which leaves out steps 3 and 4.

Starting an engine late in a call would spend the rest of it loading a model
and seal nothing (the runner then fails: "time budget ran out before a single
page was sealed").  So once an earlier step of this call has sealed pages, an
engine is started only when the time left covers its model load and its
longest page, as measured on this book (BOOK/reports/ocr-times.json, rewritten
after each engine that seals a page; the load up to the engine's own
model-ready signal, the run report's modelReadyAt, else to its first seal
less that page's time: measure(); DEFAULT_STARTUP before the first
measurement).  The runner is told (OCR_PHASE_STARTUP_SECONDS) and defers
before loading when there is not that much time; the call then ends with 75.
run_ocr_phase.py does not stop the model for a window shorter than the first
engine's estimate.  An engine that runs out of time before its first page
leaves its run time as the least its load takes (loadAtLeast); when it had
at least its estimate, the call ends with 75 (the estimate was short), and
when it had less (the budget is too small for it), with 1 and a RESULT line
that names both times and says to raise the budget.  That holds for shared
Hunyuan too: a broker still loading its server at the deadline is a load the
budget cannot hold (StartupExpired, exit 1), not a queue wait (75).

Run directly on a machine whose env.sh names a local model that owns the GPU
(OCR_PHASE_RELEASE_CMD), outside a hand-over window, the GPU engines load
nothing and defer (scripts/_gpu_queue.py): the call ends with 75 and a RESULT
line that says to run it through run_ocr_phase.py, since the same command run
again directly would only defer again.

Exit codes: 0 every engine done; 75 not finished, run the SAME command again
(finished pages are kept; run directly, the RESULT line names it with
--round N+1, which changes nothing but keeps repeated calls from being
identical), or, as its RESULT line then says, run it through
run_ocr_phase.py; 1 a runner failed or refused its settings (its own summary
says why); 2 this command's own usage (no render folder).  Every step's output
is the runner's own; the last line is "[ocr-book] RESULT exit=N ...".
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jyut_ocr.ocr.phase_budget import (  # noqa: E402
    DEADLINE_ENV, EXIT_INCOMPLETE, PROGRESS_ENV, STARTUP_ENV)
from jyut_ocr.ocr.engines import resolve_engines, engine_a, BOOK_ENGINE_RECORD
from jyut_ocr.ocr.gpu_queue import LOCAL_MODEL_MESSAGE, TENANT_ENV, WINDOW_ENV  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
TIMES_NAME = "ocr-times.json"
# Model load plus the longest page, before this book measured it (seconds).
DEFAULT_STARTUP = {"hunyuan": 60.0, "paddle": 90.0, "second-look": 60.0, "vision": 15.0}
# Kept on top of the measured startup: a load is never exactly as fast twice.
STARTUP_MARGIN = 1.2
POLL = 0.25

ENGINES = {
    "vision": ("run_apple_vision_ocr.py", "apple-vision-run.json"),
    "hunyuan": ("run_hunyuanocr.py", "hunyuanocr-run.json"),
    "paddle": ("run_paddleocr_vl.py", "paddleocr-run.json"),
    "second-look": ("run_hunyuanocr_second_look.py", "second-look-run.json"),
}


def say(message: str) -> None:
    with contextlib.suppress(OSError, ValueError):
        print(f"[ocr-book] {message}", flush=True)


# build_draft.py's lines: the draft written, and its copy beside the PDF (or why none).
DRAFT_WROTE = re.compile(r"^\[draft\] wrote (.+?): \d+ pages, ", re.M)
DRAFT_BESIDE = re.compile(r"^\[draft\] beside the PDF: (.+?)(?: \(the edited copy kept as (.+)\))?$", re.M)
DRAFT_NOT_COPIED = re.compile(r"^\[draft\] not copied beside the PDF: (.+)$", re.M)


def draft_line(output: str, book: Path) -> str:
    """Where the person reads the draft build_draft.py wrote (its OUTPUT):
    the copy beside the PDF, else BOOK/draft.md and why it is not there."""
    beside = DRAFT_BESIDE.search(output)
    if beside:
        kept = f" (the person's edited copy kept as {beside.group(2)})" if beside.group(2) else ""
        return f"{beside.group(1)}{kept}"
    wrote = DRAFT_WROTE.search(output)
    reason = DRAFT_NOT_COPIED.search(output)
    return (f"{wrote.group(1) if wrote else book / 'draft.md'}"
            f" (not copied beside the PDF: {reason.group(1) if reason else 'build_draft.py did not say why'})")


def count_lines(path: str | None) -> int:
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", delete=False,
                                         encoding="utf-8")
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(handle.name, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)


def startup_seconds(times: dict, engine: str) -> float:
    """Model load plus the longest page for ENGINE, with a margin."""
    measured = times.get("engines", {}).get(engine) if isinstance(times.get("engines"), dict) else None
    if isinstance(measured, dict):
        load, page = measured.get("loadSeconds"), measured.get("longestPageSeconds")
        if isinstance(load, (int, float)) and isinstance(page, (int, float)):
            return (load + page) * STARTUP_MARGIN
    return DEFAULT_STARTUP[engine]


def measure(times_path: Path, engine: str, started: float, first_seal: float | None,
            report: dict, first_page: int | None = None) -> None:
    """Keep ENGINE's load time and longest page from this run, when it sealed one.

    The load is from the runner's start (STARTED) to the engine's own signal
    that its model is loaded, the report's modelReadyAt (the llama-server
    answering with the model, through the shared broker or not; the Paddle
    worker's ready line), when this run wrote one.  Else it is the time to
    the first seal less that page's own seconds (FIRST_PAGE, the first scan
    page the progress file names; the fastest page's when that is not
    known).  Until 2026-11-15 it always took the fastest page off: with
    parallel Hunyuan slots the first seal is the first page's latency after
    the load, and Paddle's first page is often its slowest, so both loads
    came out about three times too long (24.8 s and 27.5 s against about
    2 s and 8 s measured from the engines' own logs)."""
    if first_seal is None:
        return
    pages = [page for page in report.get("pages", [])
             if isinstance(page, dict) and page.get("status") == "completed"
             and isinstance(page.get("seconds"), (int, float))]
    seconds = [page["seconds"] for page in pages]
    longest = max(seconds) if seconds else 0.0
    ready = report.get("modelReadyAt")
    if isinstance(ready, (int, float)) and not isinstance(ready, bool) and started - 1 <= ready <= first_seal:
        load, source = max(0.0, ready - started), "model-ready"
    else:
        own = next((page["seconds"] for page in pages if page.get("scanPage") == first_page), None)
        if own is None:
            own, source = (min(seconds) if seconds else 0.0), "fastest-page"
        else:
            source = "first-page"
        load = max(0.0, first_seal - started - own)
    times = read_json(times_path)
    engines = times.get("engines") if isinstance(times.get("engines"), dict) else {}
    engines[engine] = {"loadSeconds": round(load, 2), "longestPageSeconds": round(longest, 2),
                       "pagesTimed": len(seconds), "loadFrom": source,
                       "measuredAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    times["engines"] = engines
    write_json(times_path, times)


def slowest_start(times_path: Path, engine: str, seconds: float) -> None:
    """ENGINE ran SECONDS without sealing a page: its load takes at least that."""
    times = read_json(times_path)
    engines = times.get("engines") if isinstance(times.get("engines"), dict) else {}
    known = engines.get(engine) if isinstance(engines.get(engine), dict) else {}
    load = known.get("loadSeconds") if isinstance(known.get("loadSeconds"), (int, float)) else 0.0
    page = known.get("longestPageSeconds") if isinstance(known.get("longestPageSeconds"), (int, float)) else 0.0
    engines[engine] = dict(known, loadSeconds=round(max(load, seconds), 2), longestPageSeconds=page,
                           loadAtLeast=True, measuredAt=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    times["engines"] = engines
    write_json(times_path, times)


def first_line(path: str | None, skip: int) -> int | None:
    """The scan page on line SKIP+1 of the progress file (the runners append
    one sealed scan page a line), or None."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return None
    try:
        return int(lines[skip]) if len(lines) > skip else None
    except ValueError:
        return None


def run_step(command: list[str], env: dict[str, str], progress: str) -> tuple[int, float, float | None, int | None]:
    """Run one runner; (exit code, start time, time of its first sealed page,
    that page's scan number)."""
    before = count_lines(progress)
    started = time.time()
    first_seal = None
    process = subprocess.Popen(command, env=env)
    while True:
        try:
            code = process.wait(timeout=POLL)
            break
        except subprocess.TimeoutExpired:
            pass
        if first_seal is None and count_lines(progress) > before:
            first_seal = time.time()
    if first_seal is None and count_lines(progress) > before:
        first_seal = time.time()
    return code, started, first_seal, first_line(progress, before) if first_seal is not None else None


def ran_out_of_time(report: dict) -> bool:
    """Whether a failed run left pages deferred and none failed or blocked: the
    budget ended before its first page, which the next call starts afresh."""
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    return (counts.get("deferred", 0) > 0 and not counts.get("failed")
            and not counts.get("blocked") and not counts.get("completed"))


def deferred_by_local_model(report: dict) -> bool:
    """Whether a run's pages were deferred because a configured local model
    owns the GPU and no hand-over window is open (scripts/_gpu_queue.py),
    not for want of time."""
    queue = report.get("gpuQueue") if isinstance(report.get("gpuQueue"), dict) else {}
    if queue.get("status") == "deferred-local-model":
        return True
    pages = report.get("pages") if isinstance(report.get("pages"), list) else []
    return any(isinstance(page, dict) and page.get("status") == "deferred"
               and page.get("reason") == LOCAL_MODEL_MESSAGE for page in pages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path, help="the book folder (renders/ inside)")
    parser.add_argument("--renders", default="renders", help="render folder in BOOK (default renders)")
    parser.add_argument("--hunyuan-dir", default="ocr-hunyuan", help="engine B's drafts (default ocr-hunyuan)")
    parser.add_argument("--paddle-dir", default="ocr-paddle", help="engine A's drafts (default ocr-paddle)")
    parser.add_argument("--engine", choices=("apple-vision", "hunyuan-paddle", "hunyuan"),
                        help="OCR route; defaults to saved route or hunyuan-paddle. hunyuan is Linux/WSL2 only")
    parser.add_argument("--vision-dir", default="ocr-vision", help="Apple Vision layout drafts")
    parser.add_argument("--reports", default="reports", help="run reports and ocr-times.json (default reports)")
    parser.add_argument("--single-engine", action="store_true",
                        help="Linux/WSL2 only: equivalent to --engine hunyuan; proofreading/finishing have their own --single-engine flag")
    parser.add_argument("--max-seconds", type=float,
                        help="time budget when no OCR_PHASE_DEADLINE is set (run_ocr_phase.py sets one)")
    parser.add_argument("--max-missing-engine-b", type=int, default=0,
                        help="allow up to N failed engine-B pages to fall back to single-engine proofreading "
                             "when all remaining pages complete (default 0)")
    parser.add_argument("--round", type=int, default=1,
                        help="which call of a run of repeated calls this is; it changes nothing. Run "
                             "directly, an exit 75 names the next command with --round N+1, so repeated "
                             "calls are not identical (an agent harness's loop detection)")
    return parser


RESULT_MEANING = {
    0: "DONE - the selected OCR route has read the book",
    EXIT_INCOMPLETE: "INCOMPLETE - time ran out; finished pages are sealed: run the SAME command again",
    1: "FAILED - read the runner's summary and ERROR lines above; do not just repeat",
    2: "CONFIGURATION ERROR",
}


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if any(arg == "--engines" or arg.startswith("--engines=") for arg in raw):
        parser.error("--engines was removed; use --engine apple-vision or --engine hunyuan-paddle")
    arguments = parser.parse_args(raw)
    arguments.argv = raw
    code = run(arguments)
    meaning = getattr(arguments, "result_meaning", None) or RESULT_MEANING.get(code, "see the output above")
    # Run directly and to be repeated as it is, the next call differs from
    # this one by its round only (GH-13).  Under run_ocr_phase.py (which sets
    # the progress file, and the window when it hands over) the wrapper's
    # RESULT line names its own next call.
    after = (f"; next call: {next_round(arguments)}"
             if code == EXIT_INCOMPLETE and not getattr(arguments, "result_meaning", None)
             and not (os.environ.get(WINDOW_ENV) or os.environ.get(PROGRESS_ENV)) else "")
    say(f"RESULT exit={code} {meaning}{after}")
    return code


def own_options(arguments: argparse.Namespace) -> list[str]:
    """This call's arguments without --round."""
    kept: list[str] = []
    skip = False
    for item in getattr(arguments, "argv", [str(arguments.book)]):
        if skip:
            skip = False
        elif item == "--round":
            skip = True
        elif not item.startswith("--round="):
            kept.append(item)
    return kept


def next_round(arguments: argparse.Namespace) -> str:
    """This call's command line with --round N+1."""
    return shlex.join([sys.executable, str(SCRIPTS / "ocr_book.py"), *own_options(arguments),
                       "--round", str(arguments.round + 1)])


def hand_over_command(arguments: argparse.Namespace) -> str:
    """This call's command line under run_ocr_phase.py."""
    python = sys.executable
    return shlex.join([python, str(SCRIPTS / "run_ocr_phase.py"), "--", python, str(SCRIPTS / "ocr_book.py"),
                       *own_options(arguments)])


def run(arguments: argparse.Namespace) -> int:
    if arguments.max_missing_engine_b < 0:
        say("ERROR --max-missing-engine-b must be >= 0")
        return 2
    book = arguments.book.expanduser().resolve()
    renders = book / arguments.renders
    if not renders.is_dir():
        say(f"ERROR no render folder: {renders}")
        return 2
    try:
        engines = resolve_engines(arguments.engine, single_engine=arguments.single_engine, book=book)
    except ValueError as error:
        say(f"ERROR {error}")
        return 2
    reports = book / arguments.reports
    reports.mkdir(parents=True, exist_ok=True)
    times_path = reports / TIMES_NAME
    hunyuan_dir, paddle_dir = book / arguments.hunyuan_dir, book / arguments.paddle_dir
    env = dict(os.environ)
    env.pop(STARTUP_ENV, None)
    if not env.get(DEADLINE_ENV) and arguments.max_seconds:
        env[DEADLINE_ENV] = f"{time.time() + arguments.max_seconds:.3f}"
    own_progress = None
    if not env.get(PROGRESS_ENV):
        handle = tempfile.NamedTemporaryFile(prefix="ocr-book-progress-", delete=False)
        handle.close()
        own_progress = env[PROGRESS_ENV] = handle.name
    progress = env[PROGRESS_ENV]
    deadline = float(env[DEADLINE_ENV]) if env.get(DEADLINE_ENV) else None
    call_start = count_lines(progress)
    python = sys.executable

    a_engine = engine_a(engines)
    a_dir = paddle_dir
    steps = []
    if "hunyuan" in engines:
        steps.extend([("hunyuan", [str(renders), str(hunyuan_dir)]), ("draft", [])])
    if a_engine:
        steps.append((a_engine, [str(renders), str(a_dir)]))
        steps.append(("second-look", [str(renders), str(a_dir), str(hunyuan_dir)]))
    elif engines == ("vision",):
        a_dir = book / arguments.vision_dir
        steps.append(("vision", [str(renders), str(a_dir)]))
    def stored_path(path):
        try:
            return str(path.relative_to(book))
        except ValueError:
            return str(path)
    write_json(book / "reports" / BOOK_ENGINE_RECORD, {
        "schemaVersion": 1, "engines": list(engines),
        "draftA": stored_path(a_dir if a_engine or engines == ("vision",) else hunyuan_dir),
        "draftB": stored_path(hunyuan_dir) if a_engine else None,
    })
    render_pages = sorted(int(path.stem.split("-")[-1]) for path in renders.glob("page-*.png")
                          if re.fullmatch(r"page-\d{4,}\.png", path.name))

    try:
        hunyuan_sealed = 0
        tolerated_missing_b: list[int] = []
        for name, positional in steps:
            if name == "draft":
                if hunyuan_sealed or not (book / "draft.md").exists():
                    say("engine B has read every page: the early draft")
                    draft = subprocess.run([python, str(SCRIPTS / "build_draft.py"), str(book)], env=env,
                                           check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, errors="replace")
                    with contextlib.suppress(OSError, ValueError):
                        sys.stdout.write(draft.stdout)
                        sys.stdout.flush()
                    if draft.returncode != 0:
                        say(f"WARNING build_draft.py exit {draft.returncode}; going on with engine A")
                    else:
                        say(f"DRAFT {draft_line(draft.stdout, book)}")
                continue
            script, report_name = ENGINES[name]
            report_path = reports / report_name
            step_env = dict(env)
            sealed_in_call = count_lines(progress) - call_start
            startup = startup_seconds(read_json(times_path), name)
            left_at_start = None if deadline is None else deadline - time.time()
            if sealed_in_call and deadline is not None:
                step_env[STARTUP_ENV] = f"{startup:.1f}"
                left = deadline - time.time()
                if left <= startup:
                    say(f"{name}: {left:.0f}s left, less than its model load and longest page "
                        f"(about {startup:.0f}s): it only skips what is done, and loads nothing")
            say(f"{name}: {script}")
            command = [python, str(SCRIPTS / script), *positional, "--report", str(report_path)]
            if name == "second-look" and (arguments.max_missing_engine_b > 0 or tolerated_missing_b):
                command.append("--allow-missing-engine-b")
            if name == "vision":
                if not render_pages:
                    say("ERROR no rendered pages selected")
                    return 2
                command.extend(["--pages", ",".join(map(str, render_pages))])
            before = count_lines(progress)
            code, started, first_seal, first_page = run_step(command, step_env, progress)
            sealed = count_lines(progress) - before
            report = read_json(report_path)
            # A report from an earlier run must not speak for this one.
            fresh = report_path.is_file() and report_path.stat().st_mtime >= started - 1
            measure(times_path, name, started, first_seal, report, first_page)
            if name == "hunyuan":
                hunyuan_sealed = sealed
            if code == 0:
                continue
            if code == EXIT_INCOMPLETE:
                if fresh and deferred_by_local_model(report):
                    # Not for want of time: run again as it is, it defers again.
                    say(f"{name}: {LOCAL_MODEL_MESSAGE}: {TENANT_ENV} is set and no hand-over window "
                        f"is open, so it loaded nothing. Run: {hand_over_command(arguments)}")
                    arguments.result_meaning = (
                        f"INCOMPLETE - the GPU belongs to the local model ({TENANT_ENV} is set) and no "
                        "hand-over window is open, so nothing was loaded: run it through run_ocr_phase.py "
                        f"({hand_over_command(arguments)}), not the same command again")
                    return EXIT_INCOMPLETE
                say(f"{name}: not finished ({sealed} page(s) sealed in this step); run the same command again")
                return EXIT_INCOMPLETE
            if code == 1 and fresh and first_seal is None and ran_out_of_time(report):
                # Its load and first page took longer than this step had: keep
                # that as the least they take, so the next call's estimate (and
                # run_ocr_phase.py's window) allows for it.
                slowest_start(times_path, name, time.time() - started)
            if code == 1 and sealed_in_call and ran_out_of_time(report):
                # Started with time to spare by the measurement, but the time
                # still ran out before its first page: not a failure of the engine.
                say(f"{name}: the time ran out before its first page; run the same command again")
                return EXIT_INCOMPLETE
            if (code == 1 and fresh and ran_out_of_time(report) and left_at_start is not None
                    and left_at_start >= startup):
                # The first engine of the call had the time its estimate asks
                # for and still sealed nothing: the estimate was short, which
                # the measurement above corrects.  A budget too small for the
                # engine starts it with less than the estimate and stays a failure.
                say(f"{name}: the time ran out before its first page although {left_at_start:.0f}s were "
                    f"left, its estimated load and longest page being {startup:.0f}s; the estimate is "
                    f"now {startup_seconds(read_json(times_path), name):.0f}s; run the same command again")
                return EXIT_INCOMPLETE
            if name == "hunyuan" and code == 1 and fresh and arguments.max_missing_engine_b > 0:
                counts = (report or {}).get("counts") or {}
                failed_n = int(counts.get("failed") or 0)
                comp_n = int(counts.get("completed") or 0)
                skip_n = int(counts.get("skippedCurrent") or 0)
                def_n = int(counts.get("deferred") or 0)
                req_n = int(counts.get("requested") or 0)
                if (
                    0 < failed_n <= arguments.max_missing_engine_b
                    and def_n == 0
                    and (comp_n + skip_n + failed_n) == req_n
                ):
                    failed_pages = [
                        p.get("scanPage")
                        for p in (report.get("pages") or [])
                        if isinstance(p, dict) and p.get("status") == "failed"
                    ]
                    tolerated_missing_b = (
                        [p for p in failed_pages if isinstance(p, int)] or list(range(failed_n))
                    )
                    say(
                        f"hunyuan: tolerating {failed_n} failed page(s) ({failed_pages}) within "
                        f"--max-missing-engine-b={arguments.max_missing_engine_b}; continuing for single-engine fallback"
                    )
                    continue
            say(f"{name}: {script} exit {code}")
            if code == 1 and fresh and ran_out_of_time(report) and left_at_start is not None:
                # The budget is smaller than the engine's load: the same call
                # would fail the same way (a 75 here looped 75 -> 76 for ever).
                estimate = startup_seconds(read_json(times_path), name)
                arguments.result_meaning = (
                    f"FAILED - the time budget is too small for {name}: {left_at_start:.0f}s were left when it "
                    f"started, and its model load and longest page take at least {estimate:.0f}s "
                    f"({times_path}); raise the budget (OCR_PHASE_MAX_SECONDS, below the tool call's time "
                    "limit; raise both together) above that. The same call with the same budget fails the same way")
            return 1
        if tolerated_missing_b:
            say(
                f"completed with {len(tolerated_missing_b)} missing engine-B page(s) "
                f"({tolerated_missing_b}) tolerated for single-engine fallback"
            )
        return 0
    finally:
        if own_progress:
            with contextlib.suppress(OSError):
                os.unlink(own_progress)


if __name__ == "__main__":
    raise SystemExit(main())
