#!/usr/bin/env python3
"""Several books in one run: one OCR window for all of them, and one round of answerers for all.

    python3 scripts/ocr_batch.py A.pdf B.pdf ...      1. the batch: one book folder for every PDF
    python3 scripts/ocr_batch.py --ocr BATCH          2. the OCR phase of every book, one after another
    python3 scripts/ocr_batch.py --round BATCH        3. one round for every book, until all are delivered
    python3 scripts/ocr_batch.py --status BATCH       where every book stands, and nothing else

BATCH is the folder call 1 prints.  Every call can be run again: it carries on where the books are.

Why
---
Run one after another, each book takes its whole run alone: the local model that holds the GPU is
stopped and started for every book (and at every chunk boundary of a budgeted hand-over), and the
agent sits through one book's OCR before it may answer another's questions.  Measured on one
639-page book (2026-11-17): the OCR took 66 minutes and the proofreading 39, and nothing of the
second needs the GPU.  So here:

- call 2 is ONE command for every book's OCR.  Under run_ocr_phase.py it is one hand-over window:
  the local model is stopped once and started once for the whole batch.  The engines' models load
  in a few seconds (3 to 5 measured), so the books are read one after another with the plain
  one-book command, ocr_book.py, and a book that fails does not stop the next;
- call 3 does not wait for call 2.  A book is proofread as soon as its own OCR is done, while the
  GPU reads the next one: start call 2 in the background, then run call 3 again and again;
- with the packet backend (the agent answers) one round's questions come from every book that
  has any, so one round of answerers serves all the books.

Each book is an ordinary book folder (the one-book commands of SKILL.md work on it at any time);
the batch only keeps a list of them, its own marks and logs.  A book that fails is marked failed
with the reason, and the others go on.

Call 1: the batch
-----------------
Each PDF gets a folder under --work-root (JYUT_OCR_WORK_ROOT, else ~/jyut-ocr-books).  A book
folder there whose page-manifest.json names the same PDF is taken as it is; otherwise the folder
is named after the PDF's file name, or the folder that holds it when the file name has no Latin
letter.  BATCH is --batch, else a folder under WORK_ROOT/batches named after the PDFs, so the same
command names the same batch again.  The call returns at once: the pages are rendered in the
background (survey_pdf.py, one book after another; 0.12 s a page measured on 16 cores), and it
prints the two commands that follow.

Call 2: --ocr BATCH
-------------------
For every book whose OCR is not done: waits for its render (or renders it), runs ocr_book.py
(output appended to BOOK/ocr.log), then build_draft.py.  Under a time budget (OCR_PHASE_DEADLINE,
set by run_ocr_phase.py) it stops with the book that ran out of time and exits 75: run the same
command again.  Exit 0 every book read; 1 a book failed (the others were still read; the round
says which); 75 not finished.

Call 3: --round BATCH
---------------------
One call waits at most --wait seconds (default 540, under the 600 s cap of a shell call) and
prints a few lines; every step it starts (the book profile, the proofreading, finish_book.py -
the same commands SKILL.md gives, one book's steps in order) runs detached and outlives the call.
For every book whose OCR is done it hands in the answers of a wave that is answered in full
(--answered-by MODEL, the answering model's exact id), starts the step the book is at, and once
finish_book.py has delivered it removes the book folder (clean_book.py --all; --keep-work: not).

- exit 10: questions wait.  BATCH/plans/round-NN/ holds one worker-KK.txt per answerer.  Start
  them all at once, each told only to read its file and do what it says; when all are back, run
  the round again.  A round hands out at most --max-workers files of about --items-per-worker
  questions; what is left over is handed out by the next round;
- exit 75: nothing to answer yet, books are still being read or worked on: run it again.  Also
  when the round is called before every answerer of the last one has written its file: it waits
  for them (for --answer-grace seconds after that round, default 600) instead of handing the same
  bundles to a second answerer;
- exit 4: books wait for the OCR and call 2 is not running: the line says how it ended.  Start
  call 2 again (SKILL.md 五 for a hand-over's exit code);
- exit 0: every book is delivered; 1: every book is delivered or failed, and at least one failed
  (--retry-failed tries those again); 2: wrong arguments, or another round is running.

With --backend http (the local model proofreads) there are no questions: the round starts each
book's steps one book at a time on the model, once every book's OCR is over (the OCR window has
the model stopped), and exits 75 until all are done.

How many questions an answerer is given
---------------------------------------
Measured with a small vision model as the answerer, input tokens (almost all cache reads) for one
question: 85 thousand when an answerer was given about 38 questions (one 639-page book, 1,682
questions, 44 answerers, 2026-11-17), and 64 thousand when it was given 10 or fewer (two short
books, 86 questions, 10 answerers, 2026-11-18: about 50 thousand for those given exactly 10).
Fewer is not always cheaper: an answerer reads its own instructions again on every turn, about
300 thousand tokens before any question counts, so one given 3 questions cost 112 thousand each.
So the default is 10, and the proofreading call is given --max-items-per-bundle with the same
number, since a bundle is never split between two answerers.  Twenty has not been measured.
Fewer questions an answerer mean more rounds; with the GPU reading the next book meanwhile, the
rounds are not what the batch waits for.

The last line is always `[batch] RESULT exit=N ...`.  BATCH/batch-status.json holds the same
state for a program to read - per book its stage, pages read by each engine, pages sealed,
questions waiting, and the error of a failed one - and BATCH/timeline.jsonl one line for every
step that started or ended, with its seconds.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Iterator

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import _ocr_engines  # noqa: E402
import packet_dispatch  # noqa: E402
from _phase_budget import DEADLINE_ENV, EXIT_INCOMPLETE  # noqa: E402

PY = sys.executable
SCHEMA = 1
EXIT_QUESTIONS = 10
EXIT_OCR_STOPPED = 4
PENDING = (10, 11)
DEFAULT_WAIT = 540
DEFAULT_MAX_WORKERS = 20
DEFAULT_ITEMS_PER_WORKER = 10       # see "How many questions an answerer is given" in the docstring
DEFAULT_JOBS = 4                    # steps of different books that run at once (packet backend)
GATHER_SECONDS = 90                 # with questions waiting: how long to wait for a step that is about to ask more
STARTING_SECONDS = 20               # a detached step counts as running for this long before it holds its lock
MAX_HANDOUTS = 4                    # a bundle handed out this often and never answered fails its book
MAX_LOST = 3                        # a step killed this often before it ended fails its book
DEFAULT_ANSWER_GRACE = 600          # how long a round's answerers are given before a bundle is handed out again
PROFILE_MIN_PAGES = 5               # book_profile.py is left out for a file of four pages or fewer (SKILL.md, step 3)
SINGLE_DRAFTS = {"apple-vision": "ocr-vision", "hunyuan": "ocr-hunyuan"}
OVER = ("delivered", "failed")
# What proofread_pages.py is given for a book with no sealed page under the packet backend (SKILL.md, step 4).
NEW_BOOK_SWITCHES = ["--same-print-marks", "--unmarked-page-sample"]
RESULT_LINE = re.compile(r"RESULT exit=(\d+)")


def say(text: str) -> None:
    print(text, flush=True)


def script(name: str) -> str:
    return str(SCRIPTS / name)


def this(*arguments: str) -> list[str]:
    return [PY, script("ocr_batch.py"), *arguments]


# --- the batch record, its marks and its timeline ---------------------------------------------

def load(batch: Path) -> dict[str, Any]:
    try:
        record = json.loads((batch / "batch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"ERROR {batch} is not a batch folder (no readable batch.json: {error}); "
                         "it is the folder the first call printed as BATCH")
    if not isinstance(record.get("books"), list) or record.get("route") not in _ocr_engines.ROUTES:
        raise SystemExit(f"ERROR {batch / 'batch.json'} lists no books or no OCR route")
    return record


def write_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temp, path)


def mark(batch: Path, name: str) -> Path:
    folder = batch / "marks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def event(batch: Path, **fields: Any) -> None:
    """One line of BATCH/timeline.jsonl (appended whole: several processes write it)."""
    line = json.dumps({"t": round(time.time(), 1), **fields}, ensure_ascii=False) + "\n"
    with contextlib.suppress(OSError):
        with open(batch / "timeline.jsonl", "a", encoding="utf-8") as handle:
            handle.write(line)


@contextlib.contextmanager
def held(path: Path) -> Iterator[bool]:
    """Hold the lock file PATH for the block; yields False, at once, when another process has it.
    The lock goes with the process, so one that was killed holds nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_held(path: Path) -> bool:
    with held(path) as mine:
        return not mine


def work_root(value: str | None) -> Path:
    return Path(value or os.environ.get("JYUT_OCR_WORK_ROOT") or Path.home() / "jyut-ocr-books").expanduser().resolve()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")


def book_name(pdf: Path, taken: set[str]) -> str:
    """A folder name for PDF's book, in plain Latin letters: the file's own name, else the name of
    the folder that holds it (a file named in Chinese in a folder named by a shelf mark), else
    book-<hash>; with -2, -3 ... when TAKEN has it."""
    name = next((candidate for candidate in (slug(pdf.stem), slug(pdf.parent.name))
                 if len(candidate) >= 3 and re.search(r"[a-z]", candidate)), "")
    name = name or "book-" + hashlib.sha256(str(pdf).encode()).hexdigest()[:8]
    chosen, number = name, 2
    while chosen in taken:
        chosen, number = f"{name}-{number}", number + 1
    return chosen


def existing_book(root: Path, pdf: Path) -> Path | None:
    """The book folder under ROOT whose page-manifest.json names PDF as its source, if any."""
    for manifest in sorted(root.glob("*/page-manifest.json")):
        try:
            source = json.loads(manifest.read_text(encoding="utf-8")).get("source_pdf")
        except (OSError, ValueError, AttributeError):
            continue
        if source and Path(source).expanduser().resolve() == pdf:
            return manifest.parent
    return None


def rendered(book: Path) -> bool:
    return (book / "page-manifest.json").is_file()


def page_count(book: Path) -> int:
    try:
        pages = json.loads((book / "page-manifest.json").read_text(encoding="utf-8")).get("pages")
    except (OSError, ValueError, AttributeError):
        return 0
    return len(pages) if isinstance(pages, list) else 0


def count(folder: Path) -> int:
    return len(list(folder.glob("page-*.json"))) if folder.is_dir() else 0


def tail(path: Path, lines: int = 3) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " | ".join(line.strip() for line in text[-lines:] if line.strip())[-500:]


def last_result(path: Path) -> tuple[int, str] | None:
    """The last `RESULT exit=N` line of the log at PATH: (N, the line)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        found = RESULT_LINE.search(line)
        if found:
            return int(found.group(1)), line.strip()[:400]
    return None


# --- the commands of one book (the ones SKILL.md gives) -----------------------------------------

def step_command(step: str, entry: dict[str, Any], record: dict[str, Any], backend: str, bundles: int) -> list[str]:
    book, route = Path(entry["book"]), record["route"]
    single = route != _ocr_engines.DEFAULT_ROUTE
    asking = ["--backend", "http"] if backend == "http" else ["--max-bundles", str(bundles)]
    # A bundle is never split between answerers, so none may hold more than one answerer is given.
    sized = [] if backend == "http" else ["--max-items-per-bundle", str(entry.get("bundleItems") or DEFAULT_ITEMS_PER_WORKER)]
    if step == "render":
        return [PY, script("survey_pdf.py"), entry["pdf"], str(book)]
    if step == "ocr":
        return [PY, script("ocr_book.py"), str(book), *(["--engine", route] if single else [])]
    if step == "profile":
        return [PY, script("book_profile.py"), str(book / "renders"), str(book / "ocr-paddle"),
                str(book / "ocr-hunyuan"), str(book / "book-profile.json"), *asking]
    if step == "proofread":
        if single:
            return [PY, script("proofread_pages.py"), str(book / "renders"), str(book / SINGLE_DRAFTS[route]),
                    str(book / "proofread"), "--single-engine", *asking, *sized]
        return [PY, script("proofread_pages.py"), str(book / "renders"), str(book / "ocr-paddle"),
                str(book / "proofread"), "--draft2-directory", str(book / "ocr-hunyuan"), *asking, *sized,
                *(entry.get("switches") or [] if backend != "http" else [])]
    if step == "finish":
        return [PY, script("finish_book.py"), str(book),
                *(["--single-engine", "--ocr-a", str(book / SINGLE_DRAFTS[route])] if single else [])]
    raise ValueError(step)


def needs_profile(book: Path, route: str) -> bool:
    return (route == _ocr_engines.DEFAULT_ROUTE and page_count(book) >= PROFILE_MIN_PAGES
            and not (book / "book-profile.json").is_file())


# --- a step of one book: run under its lock, by this call or detached -----------------------------

def run_step(batch: Path, name: str, step: str, command: list[str]) -> int | None:
    """Run COMMAND as STEP of the book NAME, its output in BATCH/logs/NAME-STEP-NN.log and its
    exit code in BATCH/marks/NAME.STEP.exit.  None, and nothing run, when the step runs already."""
    with held(mark(batch, f"{name}.{step}.lock")) as mine:
        if not mine:
            return None
        done = mark(batch, f"{name}.{step}.exit")
        done.unlink(missing_ok=True)
        logs = batch / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / f"{name}-{step}-{len(list(logs.glob(f'{name}-{step}-*.log'))) + 1:02d}.log"
        mark(batch, f"{name}.{step}.log").write_text(str(log), encoding="utf-8")
        began = time.time()
        event(batch, book=name, step=step, event="start")
        with log.open("w", encoding="utf-8") as handle:
            code = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                  cwd=str(SCRIPTS.parent)).returncode
        temp = done.with_name(done.name + ".tmp")
        temp.write_text(str(code), encoding="utf-8")
        os.replace(temp, done)
        event(batch, book=name, step=step, event="end", exit=code, seconds=round(time.time() - began, 1))
        return code


def detach(batch: Path, *arguments: str) -> None:
    subprocess.Popen(this(*arguments), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(SCRIPTS.parent))


def start_step(batch: Path, name: str, step: str, command: list[str]) -> None:
    """Start COMMAND detached as STEP of the book NAME (run_step in a process of its own)."""
    mark(batch, f"{name}.{step}.exit").unlink(missing_ok=True)
    write_json(mark(batch, f"{name}.{step}.command"), command)
    mark(batch, f"{name}.{step}.started").write_text(f"{time.time():.0f}", encoding="utf-8")
    detach(batch, "--run-step", str(batch), name, step)


def step_state(batch: Path, name: str, step: str) -> tuple[str, int | None]:
    """("none" | "running" | "ended" | "lost", exit code).  Lost: started, no longer running, and
    no exit code - killed with the session that started it."""
    if is_held(mark(batch, f"{name}.{step}.lock")):
        return "running", None
    with contextlib.suppress(OSError, ValueError):
        return "ended", int(mark(batch, f"{name}.{step}.exit").read_text().strip())
    try:
        began = float(mark(batch, f"{name}.{step}.started").read_text().strip())
    except (OSError, ValueError):
        return "none", None
    return ("running" if time.time() - began < STARTING_SECONDS else "lost"), None


def step_log(batch: Path, name: str, step: str) -> Path:
    try:
        return Path(mark(batch, f"{name}.{step}.log").read_text(encoding="utf-8").strip())
    except OSError:
        return batch / "logs" / f"{name}-{step}-01.log"


def run_detached_step(batch: Path, name: str, step: str) -> int:
    """--run-step BATCH NAME STEP: what start_step starts."""
    try:
        command = json.loads(mark(batch, f"{name}.{step}.command").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        say(f"ERROR no command for {name} {step}: {error}")
        return 2
    code = run_step(batch, name, step, command)
    return 0 if code is None else code


# --- call 1: the batch ------------------------------------------------------------------------------

def render_all(batch: Path) -> int:
    """--render BATCH: render every book that is not rendered, one after another."""
    record = load(batch)
    with held(mark(batch, "render-all.lock")) as mine:
        if not mine:
            return 0
        for entry in record["books"]:
            if not rendered(Path(entry["book"])) and step_state(batch, entry["name"], "render")[0] != "ended":
                run_step(batch, entry["name"], "render", step_command("render", entry, record, "packet", 1))
    return 0


def start_render(batch: Path) -> None:
    mark(batch, "render-all.started").write_text(f"{time.time():.0f}", encoding="utf-8")
    detach(batch, "--render", str(batch))


def renderer_running(batch: Path) -> bool:
    if is_held(mark(batch, "render-all.lock")):
        return True
    with contextlib.suppress(OSError, ValueError):
        return time.time() - float(mark(batch, "render-all.started").read_text().strip()) < STARTING_SECONDS
    return False


def start(arguments: argparse.Namespace) -> int:
    root = work_root(arguments.work_root)
    pdfs: list[Path] = []
    for item in arguments.inputs:
        path = Path(item).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            say(f"ERROR {item} is not a PDF file (a batch takes PDFs; a folder of scans is one book: SKILL.md 四)")
            return 2
        if path not in pdfs:
            pdfs.append(path)
    try:
        engines = _ocr_engines.resolve_engines(arguments.engine)
    except ValueError as error:
        say(f"ERROR {error}")
        return 2
    route = next(name for name, names in _ocr_engines.ROUTES.items() if names == engines)
    root.mkdir(parents=True, exist_ok=True)
    taken = {path.name for path in root.iterdir() if path.is_dir()}
    books = []
    for pdf in pdfs:
        folder = existing_book(root, pdf) or root / book_name(pdf, taken)
        taken.add(folder.name)
        books.append({"name": folder.name, "pdf": str(pdf), "book": str(folder), "state": "new"})
    key = hashlib.sha256("\n".join(sorted(map(str, pdfs))).encode()).hexdigest()[:6]
    batch = (Path(arguments.batch).expanduser().resolve() if arguments.batch
             else root / "batches" / f"{books[0]['name']}-{len(books)}-books-{key}")
    batch.mkdir(parents=True, exist_ok=True)
    if (batch / "batch.json").is_file():
        # The same command again: the batch as it stands, with what its books have reached.
        old = load(batch)
        if old["route"] != route:
            say(f"ERROR {batch} was started with the route {old['route']}, not {route}: give --batch another folder")
            return 2
        known = {entry["pdf"]: entry for entry in old["books"]}
        books = [known.get(entry["pdf"], entry) for entry in books]
    else:
        event(batch, event="batch", books=len(books), route=route)
    record = {"schema": SCHEMA, "route": route, "workRoot": str(root), "books": books}
    write_json(batch / "batch.json", record)
    if any(not rendered(Path(entry["book"])) for entry in books) and not renderer_running(batch):
        start_render(batch)
    for entry in books:
        say(f"[batch] {entry['name']}: {entry['pdf']}")
    say(f"[batch] BATCH {batch}")
    say(f"[batch] {len(books)} book(s), route {route}, work folders under {root}; the pages are being rendered "
        "in the background")
    ocr, log = shlex.join(this("--ocr", str(batch))), shlex.quote(str(batch / "ocr.log"))
    if route == "apple-vision" or not os.environ.get("OCR_PHASE_RELEASE_CMD"):
        say(f"[batch] NEXT 1, in the background: {ocr} > {log} 2>&1")
    else:
        phase = shlex.join([PY, script("run_ocr_phase.py")])
        say("[batch] NEXT 1, the OCR of every book in one hand-over window (SKILL.md 五):")
        say(f"[batch]   you do not think on the local model - in the background: {phase} --background -- {ocr} > {log} 2>&1")
        say(f"[batch]   you think on the local model - in the foreground, again while it ends with 75: "
            f"{phase} -- {ocr} > {log} 2>&1; echo \"exit=$?\"; tail -3 {log}")
    say(f"[batch] NEXT 2, again and again until its RESULT says exit=0 (or 1): "
        f"{shlex.join(this('--round', str(batch)))} --answered-by YOUR-MODEL-ID")
    say(f"[batch] RESULT exit=0 STARTED - {len(books)} book(s) in {batch}")
    return 0


# --- call 2: every book's OCR, one after another -----------------------------------------------

def out_of_time() -> bool:
    try:
        return bool(os.environ.get(DEADLINE_ENV)) and float(os.environ[DEADLINE_ENV]) <= time.time()
    except ValueError:
        return False


def read_books(batch: Path) -> int:
    record = load(batch)
    with held(mark(batch, "ocr.lock")) as mine:
        if not mine:
            say("[ocr-batch] RESULT exit=2 another --ocr call is reading this batch: wait for it (the round says "
                "where the books are)")
            return 2
        return read_books_locked(batch, record)


def read_books_locked(batch: Path, record: dict[str, Any]) -> int:
    total = len(record["books"])
    failed: list[str] = []
    read = 0
    for entry in record["books"]:
        name, book = entry["name"], Path(entry["book"])
        if mark(batch, f"{name}.ocr.done").is_file():
            read += 1
            continue
        if mark(batch, f"{name}.ocr.failed").is_file():
            failed.append(name)
            continue
        while not rendered(book):
            state, code = step_state(batch, name, "render")
            if state == "ended":
                break
            if state != "running":
                run_step(batch, name, "render", step_command("render", entry, record, "packet", 1))
            else:
                time.sleep(2)
        if not rendered(book):
            mark(batch, f"{name}.ocr.failed").write_text("not rendered: " + tail(step_log(batch, name, "render")),
                                                         encoding="utf-8")
            failed.append(name)
            say(f"[ocr-batch] {name}: not rendered, left out")
            continue
        say(f"[ocr-batch] {name}: reading {page_count(book)} page(s)")
        began = time.time()
        event(batch, book=name, step="ocr", event="start")
        with (book / "ocr.log").open("a", encoding="utf-8") as handle:
            code = subprocess.run(step_command("ocr", entry, record, "packet", 1), stdin=subprocess.DEVNULL,
                                  stdout=handle, stderr=subprocess.STDOUT, cwd=str(SCRIPTS.parent)).returncode
        event(batch, book=name, step="ocr", event="end", exit=code, seconds=round(time.time() - began, 1))
        if code == EXIT_INCOMPLETE:
            why = (last_result(book / "ocr.log") or (0, "run the SAME command again"))[1]
            say(f"[ocr-batch] RESULT exit={EXIT_INCOMPLETE} INCOMPLETE - {read} of {total} book(s) read; {name}: {why}")
            return EXIT_INCOMPLETE
        if code != 0:
            mark(batch, f"{name}.ocr.failed").write_text(f"ocr_book.py exit {code}: {tail(book / 'ocr.log')}",
                                                         encoding="utf-8")
            failed.append(name)
            say(f"[ocr-batch] {name}: FAILED (ocr_book.py exit {code}, see {book / 'ocr.log'}); the next book is read")
            continue
        if record["route"] != "apple-vision":
            # The second look rewrote some of engine B's pages: the draft the person reads, once more.
            subprocess.run([PY, script("build_draft.py"), str(book)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mark(batch, f"{name}.ocr.done").write_text(f"{time.time():.0f}", encoding="utf-8")
        read += 1
        say(f"[ocr-batch] {name}: read in {time.time() - began:.0f} s")
        if out_of_time() and read + len(failed) < total:
            say(f"[ocr-batch] RESULT exit={EXIT_INCOMPLETE} INCOMPLETE - {read} of {total} book(s) read; the time "
                "budget is spent: run the SAME command again")
            return EXIT_INCOMPLETE
    code = 1 if failed else 0
    say(f"[ocr-batch] RESULT exit={code} {'DONE' if not failed else 'FAILED for ' + ', '.join(failed)} - "
        f"{read} of {total} book(s) read")
    return code


# --- call 3: one round for every book ----------------------------------------------------------

class Round:
    """What one --round call knows while it advances the books."""

    def __init__(self, batch: Path, record: dict[str, Any], arguments: argparse.Namespace) -> None:
        self.batch, self.record, self.arguments = batch, record, arguments
        self.backend = arguments.backend
        self.bundles = max(1, arguments.max_workers)
        self.jobs = 1 if self.backend == "http" else max(1, arguments.jobs)
        self.running = 0

    def fail(self, entry: dict[str, Any], why: str) -> None:
        entry.update(state="failed", error=why[:600])
        event(self.batch, book=entry["name"], event="failed", why=why[:300])

    def launch(self, entry: dict[str, Any], step: str) -> None:
        """Start STEP of the book detached, or leave it queued when the round's steps are all taken."""
        entry["step"] = step
        if self.running >= self.jobs or (self.backend == "http" and self.ocr_pending):
            mark(self.batch, f"{entry['name']}.{step}.started").unlink(missing_ok=True)
            entry["state"] = "queued"
            return
        if step == "proofread" and "switches" not in entry:
            # Settled once, at the first proofreading call, and the same command from then on: a
            # book with sealed pages keeps the switches it was sealed with.
            fresh = self.backend != "http" and not count(Path(entry["book"]) / "proofread")
            entry["switches"] = list(NEW_BOOK_SWITCHES) if fresh else []
            entry["bundleItems"] = max(1, self.arguments.items_per_worker)
        start_step(self.batch, entry["name"], step, step_command(step, entry, self.record, self.backend, self.bundles))
        self.running += 1
        entry["state"] = "finishing" if step == "finish" else step

    def advance(self, entry: dict[str, Any]) -> None:
        """Carry one book as far as it goes now; ENTRY is updated."""
        batch, name, book, route = self.batch, entry["name"], Path(entry["book"]), self.record["route"]
        if entry.get("state") in OVER:
            return
        entry.pop("questions", None)
        if not mark(batch, f"{name}.ocr.done").is_file():
            failure = mark(batch, f"{name}.ocr.failed")
            if failure.is_file():
                return self.fail(entry, "OCR: " + failure.read_text(encoding="utf-8"))
            if rendered(book):
                entry["state"] = "ocr"
                return
            state, code = step_state(batch, name, "render")
            if state == "ended":
                return self.fail(entry, f"render: survey_pdf.py exit {code}: {tail(step_log(batch, name, 'render'))}")
            entry["state"] = "render"
            return

        for _ in range(6):                       # an ended step, then the next one: a few moves at most
            step = entry.get("step")
            if not step:
                done = entry.setdefault("done", [])
                if "profile" not in done and needs_profile(book, route):
                    return self.launch(entry, "profile")
                return self.launch(entry, "proofread" if "proofread" not in done else "finish")
            state, code = step_state(batch, name, step)
            if state == "running":
                self.running += 1
                entry["state"] = "finishing" if step == "finish" else step
                return
            log = step_log(batch, name, step)
            if state == "lost":
                entry["lost"] = entry.get("lost", 0) + 1
                if entry["lost"] > MAX_LOST:
                    return self.fail(entry, f"{step} was started {entry['lost']} times and never ended: {tail(log)}")
            if state in ("none", "lost"):
                return self.launch(entry, step)
            if step == "finish":
                if code == 0:
                    return self.delivered(entry, log)
                if code == 4:                    # stopped: the same command again (SKILL.md 七)
                    return self.launch(entry, "finish")
                if code == 3 and not entry.get("resealed"):
                    # Pages are not sealed after all: the proofreading once more, then the finish.
                    entry["resealed"] = True
                    entry["done"] = [item for item in entry.get("done", []) if item != "proofread"]
                    entry["step"] = None
                    continue
                return self.fail(entry, f"finish_book.py exit {code}: {(last_result(log) or (0, tail(log)))[1]}")
            if code in PENDING:
                if self.backend == "http":
                    return self.fail(entry, f"{step} asked packet questions under --backend http: {tail(log, 2)}")
                try:
                    found = packet_dispatch.plan([book / "packets"], 10 ** 6, self.arguments.items_per_worker)
                except packet_dispatch.DispatchError as error:
                    return self.fail(entry, f"{step} stopped for answers, but {error}")
                if found["assignments"]:
                    entry.update(state="waiting", questions=found["waiting"]["questions"])
                    return
                if found["apply"] and not packet_dispatch.run_apply(found, self.arguments.answered_by):
                    return self.fail(entry, "apply_packet_answers.py refused the answers: "
                                     + found["apply"][0].get("output", "")[-400:])
                if found["apply"]:
                    event(batch, book=name, event="applied", wave=found["apply"][0]["wave"])
                return self.launch(entry, step)
            if code != 0:
                return self.fail(entry, f"{step} exit {code}: {tail(log)}")
            entry.setdefault("done", []).append(step)
            entry["step"] = None
        self.fail(entry, "the steps did not settle: see BATCH/logs")

    def delivered(self, entry: dict[str, Any], log: Path) -> None:
        book = Path(entry["book"])
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        first = next((index for index, line in enumerate(lines) if "[finish] RESULT" in line), max(0, len(lines) - 3))
        entry.update(state="delivered", delivered=[line.strip() for line in lines[first:first + 8] if line.strip()])
        entry.pop("step", None)
        event(self.batch, book=entry["name"], event="delivered")
        if self.arguments.keep_work or self.record["route"] != _ocr_engines.DEFAULT_ROUTE:
            return
        cleaned = subprocess.run([PY, script("clean_book.py"), str(book), "--all"], capture_output=True, text=True)
        entry["cleaned"] = (last_result_text(cleaned.stdout + cleaned.stderr)
                            or f"clean_book.py exit {cleaned.returncode}")

    @property
    def ocr_pending(self) -> bool:
        return any(entry.get("state") in ("new", "render", "ocr") for entry in self.record["books"])

    def once(self) -> list[dict[str, Any]]:
        self.running = 0
        for entry in self.record["books"]:
            entry["pages"] = page_count(Path(entry["book"])) or entry.get("pages", 0)
        # The books already under way first, so a queued one never takes the place of a running one.
        for entry in sorted(self.record["books"], key=lambda item: item.get("state") in ("queued", "new", "render", "ocr")):
            self.advance(entry)
        write_json(self.batch / "batch.json", self.record)
        return write_status(self.batch, self.record)


def last_result_text(text: str) -> str:
    return next((line.strip()[:300] for line in reversed(text.splitlines()) if "RESULT" in line), "")


def describe(entry: dict[str, Any], route: str) -> dict[str, Any]:
    book = Path(entry["book"])
    row = {"name": entry["name"], "pdf": entry.get("pdf"), "book": entry["book"], "state": entry.get("state", "new"),
           "pages": page_count(book) or entry.get("pages", 0), "sealed": count(book / "proofread")}
    if route == _ocr_engines.DEFAULT_ROUTE:
        row["read"] = {"hunyuan": count(book / "ocr-hunyuan"), "paddle": count(book / "ocr-paddle")}
    else:
        row["read"] = {route: count(book / SINGLE_DRAFTS[route])}
    for key in ("questions", "error", "delivered", "cleaned"):
        if key in entry:
            row[key] = entry[key]
    return row


def status_line(rows: list[dict[str, Any]]) -> str:
    def named(*states: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["state"] in states]
    parts = [f"{len(named('delivered'))}/{len(rows)} delivered"]
    if named("new", "render"):
        parts.append(f"rendering {len(named('new', 'render'))}")
    reading = named("ocr")
    if reading:
        first = reading[0]
        pages = ", ".join(f"{engine} {done}/{first['pages']}" for engine, done in first["read"].items())
        parts.append(f"OCR {len(reading)} to read ({first['name']}: {pages})")
    waiting = named("waiting")
    if waiting:
        parts.append(f"{sum(row.get('questions', 0) for row in waiting)} question(s) wait in {len(waiting)} book(s)")
    for state in ("profile", "proofread", "queued", "finishing"):
        if named(state):
            parts.append(f"{state} {len(named(state))}")
    if named("failed"):
        parts.append("FAILED " + ", ".join(row["name"] for row in named("failed")))
    return "; ".join(parts)


def write_status(batch: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [describe(entry, record["route"]) for entry in record["books"]]
    write_json(batch / "batch-status.json", {
        "schema": SCHEMA, "route": record["route"], "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": status_line(rows), "books": rows})
    return rows


def plan_round(batch: Path, record: dict[str, Any], arguments: argparse.Namespace) -> tuple[Path, int, int, int]:
    """Write this round's worker files: (their folder, answerers, questions handed out, questions
    left for a later round).  A bundle handed out MAX_HANDOUTS times and still unanswered fails
    its book: (folder, 0, 0, 0) then, and the caller advances the books again."""
    waiting = [entry for entry in record["books"] if entry.get("state") == "waiting"]
    found = packet_dispatch.plan([Path(entry["book"]) / "packets" for entry in waiting], 10 ** 6,
                                 arguments.items_per_worker)
    chosen = found["assignments"][:max(1, arguments.max_workers)]
    if not chosen:
        return batch / "plans", 0, 0, 0
    handouts = record.setdefault("handouts", {})
    stuck = {bundle["bundle"] for assignment in chosen for bundle in assignment["bundles"]
             if handouts.get(bundle["bundle"], 0) >= MAX_HANDOUTS}
    plans = batch / "plans"
    if stuck:
        for entry in waiting:
            mine = sorted(path for path in stuck if Path(entry["book"]) in Path(path).parents)
            if mine:
                entry.update(state="failed", error=f"a bundle was handed out {MAX_HANDOUTS} times and never answered "
                                                   f"in full: {mine[0]}")
        write_json(batch / "batch.json", record)
        return plans, 0, 0, 0
    out = plans / f"round-{len(list(plans.glob('round-*'))) + 1 if plans.is_dir() else 1:02d}"
    out.mkdir(parents=True)
    for number, assignment in enumerate(chosen, start=1):
        (out / f"worker-{number:02d}.txt").write_text(assignment["prompt"] + "\n", encoding="utf-8")
        for bundle in assignment["bundles"]:
            handouts[bundle["bundle"]] = handouts.get(bundle["bundle"], 0) + 1
    handed = sum(assignment["questions"] for assignment in chosen)
    write_json(out / "plan.json", {"schema": SCHEMA, "answerers": len(chosen), "questions": handed,
                                   "leftForLater": found["waiting"]["questions"] - handed, "assignments": chosen})
    record["lastRound"] = {"round": out.name, "t": time.time(),
                           "bundles": [[bundle["bundle"], bundle["answers"]] for assignment in chosen
                                       for bundle in assignment["bundles"]]}
    write_json(batch / "batch.json", record)
    event(batch, event="round", round=out.name, answerers=len(chosen), questions=handed,
          left=found["waiting"]["questions"] - handed, books=len(waiting))
    return out, len(chosen), handed, found["waiting"]["questions"] - handed


def still_out(record: dict[str, Any], grace: float) -> tuple[str, int]:
    """(the last round's name, how many of its answers files are not written yet) while its
    answerers still have time: a round called again too early waits for them instead of handing
    the same bundles to a second answerer.  A file that is written but incomplete is not waited
    for, nor is a bundle that is gone (its book was delivered and its folder removed since)."""
    last = record.get("lastRound") or {}
    if time.time() - float(last.get("t") or 0) >= grace:
        return "", 0
    return str(last.get("round")), sum(1 for bundle, answers in last.get("bundles") or []
                                       if Path(bundle).is_file() and not Path(answers).is_file())


def ocr_stopped(batch: Path) -> str | None:
    """Why books wait for an OCR that is not running, when call 2's log says it has ended."""
    if is_held(mark(batch, "ocr.lock")):
        return None
    log = batch / "ocr.log"
    try:
        idle = time.time() - log.stat().st_mtime
    except OSError:
        return None
    ended = last_result(log)
    return ended[1] if ended and idle > 5 else None


def round_(batch: Path, arguments: argparse.Namespace) -> int:
    record = load(batch)
    if arguments.backend == "packet" and not arguments.answered_by:
        say("[batch] RESULT exit=2 --round needs --answered-by MODEL: the exact id of the model that answers "
            "(what the platform shows when it does not tell you the id)")
        return 2
    if arguments.retry_failed:
        for entry in record["books"]:
            if entry.get("state") == "failed":
                name = entry["name"]
                for stale in (f"{name}.ocr.failed", f"{name}.render.exit", f"{name}.{entry.get('step')}.exit"):
                    mark(batch, stale).unlink(missing_ok=True)
                entry.pop("error", None)
                entry.pop("lost", None)
                entry["state"] = "new"
    deadline = time.monotonic() + max(0, arguments.wait)
    asked_since = None
    again = shlex.join(this(*(item for item in sys.argv[1:] if item != "--retry-failed")))
    with held(mark(batch, "round.lock")) as mine:
        if not mine:
            say("[batch] RESULT exit=2 another --round call is running for this batch: wait for it")
            return 2
        turn = Round(batch, record, arguments)
        while True:
            rows = turn.once()
            states = [row["state"] for row in rows]
            if any(not rendered(Path(entry["book"])) and entry.get("state") in ("new", "render")
                   for entry in record["books"]) and not renderer_running(batch):
                start_render(batch)
            out_round, out_count = still_out(record, arguments.answer_grace) if "waiting" in states else ("", 0)
            if out_count:
                if time.monotonic() >= deadline:
                    say(f"[batch] RESULT exit={EXIT_INCOMPLETE} RUNNING - {status_line(rows)} - {out_count} answers file(s) "
                        f"of {out_round} are not written yet: wait for every answerer of that round, then run this "
                        f"command again: {again}")
                    return EXIT_INCOMPLETE
            elif "waiting" in states:
                asked_since = asked_since or time.monotonic()
                about_to_ask = any(state in ("profile", "proofread") for state in states)
                if not about_to_ask or time.monotonic() - asked_since >= GATHER_SECONDS or time.monotonic() >= deadline:
                    out, answerers, handed, left = plan_round(batch, record, arguments)
                    if not answerers:
                        time.sleep(1)
                        continue
                    say(f"[batch] {status_line(rows)}")
                    say(f"[batch] ANSWERERS {answerers}: start them all at once, one per file "
                        f"{out}/worker-01.txt .. worker-{answerers:02d}.txt, each with only this prompt (its own "
                        "file's path in it): Read FILE and do exactly what it says.")
                    say(f"[batch] when every answerer is back (not before, and without stopping any), run this "
                        f"command again: {again}")
                    say(f"[batch] RESULT exit={EXIT_QUESTIONS} QUESTIONS - {handed} question(s) for {answerers} "
                        f"answerer(s) in {out}" + (f"; {left} more wait for the next round" if left else ""))
                    return EXIT_QUESTIONS
            elif all(state in OVER for state in states):
                failed = [row for row in rows if row["state"] == "failed"]
                for row in rows:
                    for line in row.get("delivered") or [f"{row['state']}: {row.get('error', '')}"]:
                        say(f"[batch] {row['name']}: {line}")
                    if row.get("cleaned"):
                        say(f"[batch] {row['name']}: {row['cleaned']}")
                code = 1 if failed else 0
                say(f"[batch] RESULT exit={code} {'DONE' if not failed else 'DONE, ' + str(len(failed)) + ' FAILED'} - "
                    f"{status_line(rows)}")
                return code
            elif "ocr" in states and not any(state in ("profile", "proofread", "finishing", "queued") for state in states):
                why = ocr_stopped(batch)
                if why:
                    say(f"[batch] RESULT exit={EXIT_OCR_STOPPED} OCR STOPPED - {status_line(rows)} - call 2 is not "
                        f"running and ended with: {why} - start it again: {shlex.join(this('--ocr', str(batch)))} "
                        "(under run_ocr_phase.py as before)")
                    return EXIT_OCR_STOPPED
            if time.monotonic() >= deadline:
                idle = "ocr" in states and not is_held(mark(batch, "ocr.lock"))
                say(f"[batch] RESULT exit={EXIT_INCOMPLETE} RUNNING - {status_line(rows)} - nothing to answer yet"
                    + ("; no --ocr call is reading right now (call 2)" if idle else "")
                    + f" - run this command again: {again}")
                return EXIT_INCOMPLETE
            time.sleep(max(0.05, min(arguments.poll, deadline - time.monotonic())))


def status(batch: Path) -> int:
    record = load(batch)
    rows = [describe(entry, record["route"]) for entry in record["books"]]
    for row in rows:
        say(f"[batch] {row['name']}: {row['state']}; {row['pages']} page(s), read {row['read']}, sealed {row['sealed']}"
            + (f", {row['questions']} question(s) waiting" if row.get("questions") else "")
            + (f" - {row['error']}" if row.get("error") else ""))
    say(f"[batch] RESULT exit=0 STATUS - {status_line(rows)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", metavar="PDF_OR_BATCH",
                        help="the PDFs (first call), or the batch folder with --ocr, --round or --status")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ocr", action="store_true", help="read every book of BATCH (the OCR phase), one after another")
    mode.add_argument("--round", action="store_true", help="one round for every book of BATCH")
    mode.add_argument("--status", action="store_true", help="print where every book of BATCH stands")
    mode.add_argument("--render", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--run-step", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--batch", help="first call: the batch folder (default: under WORK_ROOT/batches, named after the PDFs)")
    parser.add_argument("--work-root", help="first call: where the book folders go (default JYUT_OCR_WORK_ROOT, "
                                            "else ~/jyut-ocr-books)")
    parser.add_argument("--engine", choices=tuple(_ocr_engines.ROUTES),
                        help="first call: the OCR route (default: the saved one, as for ocr_book.py)")
    parser.add_argument("--backend", choices=("packet", "http"), default="packet",
                        help="--round: who proofreads - the agent by packets (default) or the local model over HTTP")
    parser.add_argument("--answered-by", metavar="MODEL",
                        help="--round, packet backend: the exact id of the model that answers, recorded with the answers")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"--round: the answerers the platform runs at once; a round hands out that many "
                             f"files at most (default {DEFAULT_MAX_WORKERS})")
    parser.add_argument("--items-per-worker", type=int, default=DEFAULT_ITEMS_PER_WORKER,
                        help=f"--round: about how many questions one answerer is given (default {DEFAULT_ITEMS_PER_WORKER})")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"--round: steps of different books that run at once (default {DEFAULT_JOBS}; "
                             "--backend http: always 1)")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT, metavar="SECONDS",
                        help=f"--round: the longest one call waits for something to do (default {DEFAULT_WAIT})")
    parser.add_argument("--poll", type=float, default=5.0, metavar="SECONDS",
                        help="--round: how often the call looks at the books again while it waits (default 5)")
    parser.add_argument("--answer-grace", type=float, default=DEFAULT_ANSWER_GRACE, metavar="SECONDS",
                        help="--round: how long after a round was handed out its unwritten answers are waited for "
                             f"before their bundles are handed out again (default {DEFAULT_ANSWER_GRACE})")
    parser.add_argument("--keep-work", action="store_true",
                        help="--round: keep each book's folder after delivery (default: clean_book.py --all)")
    parser.add_argument("--retry-failed", action="store_true", help="--round: try the failed books again")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.run_step:
        if len(arguments.inputs) != 3:
            return 2
        return run_detached_step(Path(arguments.inputs[0]), arguments.inputs[1], arguments.inputs[2])
    if not (arguments.ocr or arguments.round or arguments.status or arguments.render):
        return start(arguments)
    if len(arguments.inputs) != 1:
        say("ERROR --ocr, --round and --status take one argument: the batch folder")
        return 2
    batch = Path(arguments.inputs[0]).expanduser().resolve()
    if arguments.render:
        return render_all(batch)
    return read_books(batch) if arguments.ocr else round_(batch, arguments) if arguments.round else status(batch)


if __name__ == "__main__":
    sys.exit(main())
