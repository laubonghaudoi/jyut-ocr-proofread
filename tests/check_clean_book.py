#!/usr/bin/env python3
"""Offline check of scripts/clean_book.py, finish_book.py's fifth step and
survey_pdf.py --renders-only.

No GPU, no corpus, no model server: tests/check_finish_book.py's made-up
book (tests/repair_fixture.py's, as phase 3 leaves it), finished and
delivered beside a made-up PDF in a temporary folder, then dressed with the
pictures the real pipeline leaves - phase 3's crops beside the sealed
pages, an earlier repair's question crops, the profile's crops, a stale
assembly, a partial one, temporary files - and with what is not the
pipeline's: a backup folder, a log, a copy moved aside beside the PDF, the
person's own pictures and .tmp files among the pipeline's.

- Refused, nothing removed (exit 3, the line says which): no finish yet,
  X.md gone from beside the PDF, final.md edited after the delivery, a
  finish that did not end with exit 0 - an exit-2 one too unless its line
  names cleaned.json and --renders-only -, the manifest the finish used
  gone, the PDF gone, a finish_book.py holding its lock, a delivery holding
  its lock, a process still working on the book (one that names the folder
  and has a render open or works in it; a pipeline command given the folder
  on its command line) - and not a wait_for_run.py reading along, nor a
  process merely in the folder, nor one tailing a log.
- --dry-run: exit 0, the right items with their bytes, nothing removed, no
  cleaned.json.
- The default: exactly the regenerable set goes (renders/, phase 3's crops
  by name, repaired/crops/, book-profile-crops/, the stale and partial
  assemblies, the pipeline's temporary files, draft.md, the unedited
  X-draft.md); the text state, backup-*/, the log, finish/, the person's
  Cover.PNG and .tmp files, and beside the PDF the PDF, X.md, X-doubts.md
  and the *.edited-*.md copy stay; cleaned.json records it with the bytes
  freed, the inputs, the pdftoppm and how to resume; the RESULT line says
  what was freed.
- A second run removes nothing (exit 0).
- The removal set is the finish's inputs: sealed pages read elsewhere leave
  BOOK/proofread/ alone; of two manifests the finish's one is planned from.
- X-draft.md beside the PDF goes only when it is a version the pipeline
  wrote; an edited X.md there lets the default cleanup run and refuses
  --all, which says why; --all removes the book folder and never the PDF,
  X.md or X-doubts.md.
- --all refused, nothing removed: the PDF (and so X.md) inside the book
  folder, in three layouts; *.edited-*.md, backup-*/ or .git in it; a page
  sealed again after the delivery.  The default cleanup still runs then.
- A renders/ that is a symlink is unlinked, not followed; remove() refuses
  a path outside the book folder.
- Stopped part way (a folder that cannot be written in): exit 1 naming
  what went, cleaned.json with `stopped`, finish_book.py's hint, and the
  same command again finishes the job; a Refused inside the removal is a
  stop too, never "nothing removed".
- finish_book.py on a delivered book: its fifth step cleans with
  the renders kept, the report, the log and the RESULT line say so, and a
  later finish needs no re-render; --clean-renders removes them and the
  next finish's exit-2 line holds the whole resume command; a stop part
  way is `cleanup stopped`; --keep-work keeps the pictures and says so; so
  does --no-deliver.
- survey_pdf.py --renders-only on a made-up three-page PDF (pdftoppm, no
  GPU): the missing render comes back, every render hashes to the
  manifest's, the manifest and the report keep their bytes; a render that
  differs is exit 2 naming both pdftoppm versions, --overwrite renders it
  again; another PDF is exit 2.
- A book made from a folder of images (survey_pdf.py --images-dir: the
  manifest names no PDF): finish_book.py copies nothing, its fifth step
  cleans nothing (`; work kept`), its stdout names BOOK/final.md and
  BOOK/doubts.md alone; clean_book.py, the default and --all, exits 3,
  nothing removed, naming the missing PDF - the folder holds the only copy
  of the book.  SKILL.md (三, 四.6, 九) and 〈三次交付同本書目錄〉 say so:
  no --all, the files in BOOK told to the person, BOOK kept.  Found in the
  phase-E review (E-R3): SKILL.md ran --all and named X.md beside the PDF
  for every book.
- The skill's text: SKILL.md (the run sheet) runs clean_book.py --all as the
  last step (四.6), names its exits and switches and what to do on a refusal
  (七); the reference (long-book-workflow.md 〈清走工作檔（clean_book.py）〉)
  holds the fifth step, what it keeps and why, the refusals, the resume
  command, the RESULT words and the measurement; 〈一個 command 做完
  （finish_book.py）〉 the five steps and their log lines; 〈跨頁表格〉 that
  the stitch comparison images go with the cleanup.

Usage:
    python3 tests/check_clean_book.py
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import fcntl
import io
import json
import multiprocessing
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
import offline  # noqa: E402,F401  (private stores, closed endpoint)
import check_finish_book as cfb  # noqa: E402
import proofread_pages as pp  # noqa: E402
import build_draft  # noqa: E402
import clean_book  # noqa: E402
import finish_book  # noqa: E402
import survey_pdf  # noqa: E402

FAILURES: list[str] = []
PDF_NAME = "海港 年報.pdf"
STEM = PDF_NAME[:-4]


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


# --- the made-up book, finished and delivered -----------------------------------------------------

def size(path: Path) -> int:
    """File sizes under PATH by lstat (what clean_book counts)."""
    if path.is_symlink() or not path.is_dir():
        return path.lstat().st_size
    return sum(size(child) for child in path.iterdir())


def snapshot(folder: Path) -> dict[str, str]:
    """Every file under FOLDER (symlinks as their targets' names) and its hash."""
    out = {}
    for path in sorted(folder.rglob("*")):
        key = str(path.relative_to(folder))
        if path.is_symlink():
            out[key] = "-> " + os.readlink(path)
        elif path.is_file():
            out[key] = pp.sha256_file(path)
    return out


def delivered_book(tmp: Path, name: str, temporaries: bool = True, pdf: Path | None = None,
                   source_pdf: str | None = None) -> tuple[Path, Path]:
    """A finished, delivered made-up book at tmp/NAME/work (the finish with
    --keep-work, so the pictures stay for the check), then dressed with the
    pictures and the odd files a real one carries; and its PDF, at PDF
    (default: a shelf beside the book folder), named in the manifest as
    SOURCE_PDF (default: its absolute path)."""
    book = cfb.phase3_book(tmp / name / "work")
    pdf = pdf or tmp / name / "shelf" / PDF_NAME
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 made up\n" + name.encode("utf-8"))
    manifest = json.loads((book / "page-manifest.json").read_text(encoding="utf-8"))
    manifest.update(source_pdf=source_pdf or str(pdf), source_sha256=pp.sha256_file(pdf))
    (book / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        assert build_draft.main([str(book)]) == 0
    code, lines = cfb.finish(book, deliver=True, keep_work=True)
    shelf = pdf.parent
    assert code == 0 and (shelf / f"{STEM}.md").is_file() and (shelf / f"{STEM}-draft.md").is_file(), lines
    dress(book, shelf, temporaries)
    return book, pdf


def dress(book: Path, shelf: Path, temporaries: bool = True) -> None:
    """The pictures and the odd files a real finished book carries.  Not the
    TEMPORARIES for a book finish_book.py runs on again: finalize unlinks
    its own finish/.final.md.tmp before writing, and the count would be off."""
    png = (book / "renders" / "page-0001.png").read_bytes()
    for name in ("page-0001-adjudication-01.png", "page-0009-stitch-01.png", "page-0003-chart-01.png"):
        (book / "proofread" / name).write_bytes(png)
    (book / "repaired" / "crops").mkdir(exist_ok=True)
    for name in ("Q1-main.png", "Q2-glyph.png"):
        (book / "repaired" / "crops" / name).write_bytes(png)
    (book / "book-profile-crops" / "20260101T000000").mkdir(parents=True)
    (book / "book-profile-crops" / "20260101T000000" / "page-0005-probe-000.png").write_bytes(png)
    shutil.copytree(book / "assembled", book / "assembled.stale-20260101T000000")
    (book / ".assembled.partial").mkdir()
    (book / ".assembled.partial" / "ordered-segments.json").write_text("{}", encoding="utf-8")
    for name in TEMPORARIES if temporaries else ():
        (book / name).write_bytes(b"half written")
    for name in PERSONAL:
        (book / name).write_bytes(b"the person's")
    (book / "backup-20260101-before").mkdir()
    (book / "backup-20260101-before" / "final.md").write_bytes(b"the person's backup")
    (book / "finish.log").write_text("[finish] RESULT exit=0 DONE finish_book.py: ...\n", encoding="utf-8")
    (shelf / f"{STEM}.edited-20260101T000000.md").write_bytes(b"the person's earlier copy, moved aside")


TEMPORARIES = (".doubts.md.tmp", "finish/.final.md.tmp", "repaired/.lint.json.tmp")
# The person's files among the pipeline's: pictures in the sealed pages' folder that are no
# crop of phase 3's, and .tmp files of the pipeline's shapes but not its outputs' names.
PERSONAL = ("proofread/Cover.PNG", "proofread/reference-scan.png", ".my-notes.tmp", "notes.json.tmp",
            "finish/.todo.tmp", "repaired/scratch.json.tmp", "keep.tmp")
REGENERABLE = ("renders", "proofread/page-0001-adjudication-01.png", "proofread/page-0009-stitch-01.png",
               "proofread/page-0003-chart-01.png", "repaired/crops", "book-profile-crops",
               "assembled.stale-20260101T000000", ".assembled.partial", *TEMPORARIES, "draft.md")
TEXT_STATE = ("proofread/page-0001.md", "proofread/page-0001.json", "ocr-paddle", "ocr-hunyuan", "repaired/combined-repaired.md",
              "repaired/repair-report.json", "repaired/boundaries.json", "assembled/combined-annotated.md", "finish/finish-report.json",
              "finish/doubts.json", "finish/written.json", "book-profile.json", "page-manifest.json", "final.md", "doubts.md",
              "delivered.json", "backup-20260101-before/final.md", "finish.log", *PERSONAL)
CROPS_LABEL = "proofread/ crops (3)"


def expected_bytes(book: Path, shelf: Path, renders: bool = True) -> int:
    names = REGENERABLE if renders else tuple(n for n in REGENERABLE if n != "renders")
    return sum(size(book / name) for name in names if (book / name).exists()) + size(shelf / f"{STEM}-draft.md")


def run(book: Path, *argv: str) -> tuple[int, list[str]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = clean_book.main([str(book), *argv])
    return code, out.getvalue().splitlines()


def result_ok(lines: list[str], code: int) -> bool:
    state = clean_book.STATE[code]
    return bool(lines) and lines[-1].startswith(f"[clean] RESULT exit={code} {state} clean_book.py: ")


def write_report(book: Path, report: dict) -> None:
    (book / "finish" / "finish-report.json").write_text(json.dumps(report), encoding="utf-8")


def holds_open(pid: int, path: Path, seconds: float = 10.0) -> bool:
    """Whether the process has PATH open within SECONDS (a child needs a
    moment to start)."""
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        with contextlib.suppress(OSError):
            for name in os.listdir(f"/proc/{pid}/fd"):
                with contextlib.suppress(OSError):
                    if os.readlink(f"/proc/{pid}/fd/{name}") == str(path):
                        return True
        time.sleep(0.05)
    return False


@contextlib.contextmanager
def child(*argv: str, cwd: Path, opens: Path | None = None):
    """A process with ARGV after `python -c sleep`, working in CWD, holding
    OPENS open; killed on the way out."""
    code = "import sys, time; h = open(sys.argv[1], 'rb') if sys.argv[1] else None; time.sleep(120)"
    process = subprocess.Popen([sys.executable, "-c", code, str(opens or ""), *argv], cwd=str(cwd))
    try:
        if opens is not None:
            assert holds_open(process.pid, opens), "the child did not open the file"
        else:
            time.sleep(0.3)
        yield process
    finally:
        process.kill()
        process.wait()


# --- the checks ---------------------------------------------------------------------------------------

def refusal_checks(tmp: Path) -> None:
    book, pdf = delivered_book(tmp, "refused")
    shelf = pdf.parent
    before = snapshot(book), snapshot(shelf)

    def refused(what: str, words: str, *argv: str) -> None:
        """A run on the book as it is now removes nothing: every byte as before it."""
        now = snapshot(book), snapshot(shelf)
        code, lines = run(book, *argv)
        check(code == 3 and result_ok(lines, 3) and words in lines[-1] and lines[-1].endswith(": nothing removed")
              and (snapshot(book), snapshot(shelf)) == now,
              f"refused, nothing removed: {what} ({lines[-1] if lines else lines})")

    unfinished = cfb.phase3_book(tmp / "unfinished")
    manifest = json.loads((unfinished / "page-manifest.json").read_text(encoding="utf-8"))
    manifest.update(source_pdf=str(pdf), source_sha256=pp.sha256_file(pdf))
    (unfinished / "page-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    kept = snapshot(unfinished)
    code, lines = run(unfinished)
    check(code == 3 and result_ok(lines, 3) and "has not finished" in lines[-1] and snapshot(unfinished) == kept,
          f"refused, nothing removed: a book not finished yet ({lines[-1]})")

    final = shelf / f"{STEM}.md"
    final.rename(shelf / "moved.md")
    refused("X.md gone from beside the PDF", "is not beside the PDF")
    (shelf / "moved.md").rename(final)

    own = (book / "final.md").read_bytes()
    (book / "final.md").write_bytes(own + "\n用戶改咗一句。\n".encode("utf-8"))
    refused("final.md edited after the delivery", "is not the version last copied beside the PDF")
    (book / "final.md").write_bytes(own)

    report_path = book / "finish" / "finish-report.json"
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    report["exitStatus"] = 1
    write_report(book, report)
    refused("the last finish did not end with exit 0", "did not end with exit 0")
    # An exit-2 report for anything but the renders this command removed does not pass, cleaned.json or not.
    report.update(exitStatus=2, steps={}, result="engine A's and engine B's drafts are the same folder")
    write_report(book, report)
    (book / "cleaned.json").write_text("{}", encoding="utf-8")
    refused("an exit-2 finish for another reason, cleaned.json present", "did not end with exit 0")
    (book / "cleaned.json").unlink()
    report_path.write_bytes(report_bytes)
    report = json.loads(report_bytes.decode("utf-8"))
    report["inputs"]["page_manifest"] = str(book / "gone-manifest.json")
    write_report(book, report)
    refused("the page manifest the finish used is gone", "the page manifest the finish used is gone")
    report_path.write_bytes(report_bytes)

    pdf.rename(shelf / "renamed.pdf")
    refused("the PDF gone", "the PDF is no longer at")
    (shelf / "renamed.pdf").rename(pdf)

    with open(book / "finish" / finish_book.LOCK_NAME, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        refused("a finish_book.py holding its lock", "another finish_book.py runs on this book")
    with open(book / ".delivered.lock", "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        refused("a delivery holding its lock", "a delivery is being written")

    # A process still working on the book: phase 3 and repair_book.py hold no lock.
    render = book / "renders" / "page-0001.png"
    with child(str(render), cwd=book, opens=render) as process:
        refused(f"a process naming the book folder with a render open (pid {process.pid})",
                f"a run is still working on this book (pid {process.pid}", "--dry-run")
        refused("the same, not a dry run", f"(pid {process.pid} ({os.path.basename(sys.executable)}): its working directory is {book})")
    with child("scripts/proofread_pages.py", str(book / "renders"), str(book / "proofread"), cwd=tmp) as process:
        refused("a pipeline command given the book on its command line, nothing open",
                f"(pid {process.pid} (proofread_pages.py): the book folder is on its command line)")
    # A process pool's helper is not another run: Python 3.14's forkserver outlives the pool and carries its parent's
    # command line, which refused finish_book.py's cleanup whenever it ran with more than one worker.
    argv, sys.argv = sys.argv, ["scripts/finish_book.py", str(book)]
    try:
        with concurrent.futures.ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("forkserver")) as pool:
            pool.submit(abs, 1).result()
    finally:
        sys.argv = argv
    check(clean_book.working(book) is None, "a process pool's forkserver with the book on its command line is not another run")
    log = book / "finish.log"
    with child("scripts/wait_for_run.py", str(book), str(log), cwd=tmp, opens=log), child(cwd=book), \
            child(str(log), cwd=tmp, opens=log), child(str(book / "finish" / "finish-book.log"), cwd=tmp,
                                                        opens=book / "finish" / "finish-book.log"):
        code, lines = run(book, "--dry-run")
        check(code == 0 and result_ok(lines, 0),
              f"a wait_for_run.py reading the log, a process merely in the folder and two tailing the logs do not hold "
              f"it back ({lines[-1][:100]})")

    code, lines = run(tmp / "no-such-book")
    check(code == 2 and result_ok(lines, 2) and len(lines) == 1, f"no book folder: exit 2, one line ({lines})")
    code, lines = run(book, "--all", "--keep-renders")
    check(code == 2 and result_ok(lines, 2), f"--all with --keep-renders: exit 2 ({lines[-1]})")
    check((snapshot(book), snapshot(shelf)) == before, "the refusals left every byte as it was")


def default_checks(tmp: Path) -> None:
    book, pdf = delivered_book(tmp, "default")
    shelf = pdf.parent
    before = snapshot(book), snapshot(shelf)
    wanted = expected_bytes(book, shelf)
    draft_copy = shelf / f"{STEM}-draft.md"

    code, lines = run(book, "--dry-run")
    listed = "\n".join(lines)
    check(code == 0 and result_ok(lines, 0) and "dry run - would free " + clean_book.mb(wanted) in lines[-1]
          and lines[-1].endswith("; nothing removed"),
          f"--dry-run: exit 0, the bytes it would free ({lines[-1]})")
    check((snapshot(book), snapshot(shelf)) == before and not (book / "cleaned.json").exists(),
          "--dry-run removes nothing and writes no cleaned.json")
    items = {
        "renders/ (20 page renders)": size(book / "renders"),
        CROPS_LABEL: 3 * size(book / "proofread" / "page-0001-adjudication-01.png"),
        "repaired/crops/ (2 crops)": size(book / "repaired" / "crops"),
        "book-profile-crops/ (1 crop)": size(book / "book-profile-crops"),
        "assembled.stale-20260101T000000/": size(book / "assembled.stale-20260101T000000"),
        ".assembled.partial/": size(book / ".assembled.partial"),
        ".doubts.md.tmp": 12, "finish/.final.md.tmp": 12, "repaired/.lint.json.tmp": 12,
        "draft.md": size(book / "draft.md"),
        f"{STEM}-draft.md beside the PDF": size(draft_copy),
    }
    removing = listed.split("[clean] would remove:", 1)[-1].split("[clean] left alone:", 1)[0]
    for label, count in items.items():
        check(f"  {label:<64} {clean_book.mb(count):>10}" in removing, f"--dry-run lists {label} with its bytes")
    check(not any(name.rsplit("/", 1)[-1] in removing for name in PERSONAL),
          "--dry-run plans none of the person's files: Cover.PNG, reference-scan.png, the .tmp files of other names")
    left = listed.split("[clean] left alone:", 1)[-1].split("[clean] RESULT", 1)[0]
    for label in ("backup-20260101-before/", "finish.log", "finish/", "ocr-paddle/", "proofread/", "repaired/",
                  "assembled/", "final.md", "doubts.md", "keep.tmp", f"{PDF_NAME} beside the PDF",
                  f"{STEM}.md beside the PDF", f"{STEM}-doubts.md beside the PDF",
                  f"{STEM}.edited-20260101T000000.md beside the PDF"):
        check(f"  {label:<64}" in left, f"--dry-run lists {label} as left alone")
    check(f"{STEM}-draft.md beside the PDF" not in left and "  renders/" not in left,
          "--dry-run: neither the unedited draft copy nor the emptied renders/ is left alone")
    check(lines[-1].endswith("(renders/, proofread/ crops, repaired/crops/, book-profile-crops/, .assembled.partial/, "
                             "draft.md, the draft copy beside the PDF, 1 stale assembly, 3 temporary files); 0.2 MB left "
                             "alone in the book folder; nothing removed"),
          f"the RESULT line names the items in a few words ({lines[-1]})")

    code, lines = run(book)
    check(code == 0 and result_ok(lines, 0) and f"freed {clean_book.mb(wanted)} (" in lines[-1]
          and lines[-1].endswith("; cleaned.json written"),
          f"the default: exit 0, the RESULT line says what was freed ({lines[-1]})")
    gone = [name for name in REGENERABLE if (book / name).exists() or (book / name).is_symlink()]
    check(not gone and not draft_copy.exists(), f"the regenerable set and the draft copy are gone ({gone})")
    stays = [name for name in TEXT_STATE if not (book / name).exists()]
    check(not stays, f"the text state, the backup folder, the log and the person's files stay ({stays})")
    check(len(list((book / "proofread").glob("page-*.md"))) == 20
          and sorted(p.name for p in (book / "proofread").glob("*.[pP][nN][gG]")) == ["Cover.PNG", "reference-scan.png"],
          "the sealed pages stay, phase 3's crops do not, the person's pictures do")
    check((book / "repaired" / "combined-repaired.md").is_file() and not (book / "repaired" / "crops").exists(),
          "the repair stays without its crops")
    check(snapshot(shelf) == {k: v for k, v in before[1].items() if k != draft_copy.name},
          "beside the PDF only the draft copy went: the PDF, X.md, X-doubts.md and the copy moved aside stay")
    after = snapshot(book)
    changed = [k for k in after if k in before[0] and after[k] != before[0][k]]
    check(not changed and set(after) - set(before[0]) == {"cleaned.json"},
          f"no kept file changed; cleaned.json is the one new file ({changed}, {set(after) - set(before[0])})")
    cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))
    removed = {entry["path"] for entry in cleaned["removed"]}
    check(cleaned["tool"] == "clean_book.py" and cleaned["mode"] == "default" and cleaned["bytesFreed"] == wanted
          and removed == {"renders", "proofread", "repaired/crops", "book-profile-crops",
                          "assembled.stale-20260101T000000", ".assembled.partial", ".doubts.md.tmp",
                          "finish/.final.md.tmp", "repaired/.lint.json.tmp", "draft.md", str(draft_copy)}
          and sum(e["bytes"] for e in cleaned["removed"]) == wanted,
          f"cleaned.json: the tool, the mode, the items and the bytes freed ({cleaned['bytesFreed']} vs {wanted})")
    left_alone = {entry["path"] for entry in cleaned["leftAlone"]}
    check({"backup-20260101-before", "finish.log", "finish", "ocr-paddle", "final.md", str(pdf),
           str(shelf / f"{STEM}.md"), str(shelf / f"{STEM}-doubts.md"),
           str(shelf / f"{STEM}.edited-20260101T000000.md")} <= left_alone,
          f"cleaned.json lists what was left alone ({sorted(left_alone)[:6]}...)")
    how = cleaned["howToResume"]
    check("survey_pdf.py" in how and "--renders-only" in how and str(book) in how and "render_sha256" in how,
          f"cleaned.json says how to resume: survey_pdf.py --renders-only and the manifest's hashes ({how[:120]})")
    check(cleaned.get("rendersKept") is False and str(cleaned.get("pdftoppm", "")).startswith("pdftoppm")
          and cleaned.get("inputs", {}).get("proofread") == str(book / "proofread") and "stopped" not in cleaned,
          f"cleaned.json records the renders gone, the pdftoppm installed and the finish's inputs ({cleaned.get('pdftoppm')})")

    code, lines = run(book)
    check(code == 0 and result_ok(lines, 0) and "freed 0.0 MB (nothing left to remove" in lines[-1]
          and snapshot(book) == after, f"a second run: exit 0, nothing to remove, nothing changed ({lines[-1]})")

    # finish_book.py after the cleanup: no renders, and the line is the command that puts them back.
    code, lines = cfb.finish(book, deliver=True)
    check(code == 2 and "cleaned.json" in lines[0]
          and f"python3 scripts/survey_pdf.py {shlex.quote(str(pdf))} {shlex.quote(str(book))} --renders-only" in lines[0],
          f"finish_book.py after the cleanup: exit 2 with the whole resume command, the manifest's PDF in it ({lines[0][:160]})")

    code, lines = run(book, "--all")
    check(code == 3 and result_ok(lines, 3) and "backup-20260101-before/" in lines[-1] and book.is_dir(),
          f"--all over the person's backup folder: refused, named ({lines[-1][:160]})")
    shutil.rmtree(book / "backup-20260101-before")
    code, lines = run(book, "--all")
    check(code == 0 and result_ok(lines, 0) and not book.exists() and pdf.is_file()
          and (shelf / f"{STEM}.md").is_file() and (shelf / f"{STEM}-doubts.md").is_file(),
          f"--all after the default (the exit-2 finish notwithstanding): the book folder gone, the PDF and the copies "
          f"beside it stay ({lines[-1]})")


def edited_checks(tmp: Path) -> None:
    """Copies beside the PDF the person edited."""
    book, pdf = delivered_book(tmp, "edited")
    shelf = pdf.parent
    shutil.rmtree(book / "backup-20260101-before")     # --all over a backup is refused (all_checks)
    final, draft_copy = shelf / f"{STEM}.md", shelf / f"{STEM}-draft.md"
    own = final.read_bytes()
    final.write_bytes(own + "\n用戶改咗。\n".encode("utf-8"))
    draft_copy.write_bytes(b"the person's notes on the draft")
    code, lines = run(book, "--dry-run")
    listed = "\n".join(lines).split("left alone:")[-1].split("[clean] RESULT", 1)[0]
    check(code == 0 and f"  {STEM + '-draft.md beside the PDF':<64}" in listed and "edited by the person: left" in listed,
          f"an edited X-draft.md beside the PDF is left alone, and said ({code})")
    code, lines = run(book, "--all")
    check(code == 3 and result_ok(lines, 3) and f"{STEM}.md beside the PDF is not a version the pipeline wrote" in lines[-1]
          and "--all refused" in lines[-1] and book.is_dir() and draft_copy.read_bytes() == b"the person's notes on the draft",
          f"--all with an edited X.md: refused, says why, nothing removed ({lines[-1][:160]})")
    code, lines = run(book)
    check(code == 0 and result_ok(lines, 0) and not (book / "renders").exists()
          and draft_copy.read_bytes() == b"the person's notes on the draft" and final.read_bytes() != own,
          f"the default cleanup still runs with an edited X.md; the edited copies stay ({lines[-1][:120]})")
    final.write_bytes(own)
    code, lines = run(book, "--all")
    check(code == 0 and not book.exists() and draft_copy.read_bytes() == b"the person's notes on the draft"
          and final.read_bytes() == own and (shelf / f"{STEM}-doubts.md").is_file() and pdf.is_file(),
          f"--all once X.md is the pipeline's again: the folder goes, the edited X-draft.md stays ({lines[-1][:120]})")


def all_checks(tmp: Path) -> None:
    book, pdf = delivered_book(tmp, "whole")
    shelf = pdf.parent
    (book / "final.edited-20260101T000000.md").write_bytes(b"the person's corrections, moved aside by finish_book.py")
    (book / ".git").mkdir()
    (book / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    before = snapshot(book), snapshot(shelf)
    for argv in (("--all", "--dry-run"), ("--all",)):
        code, lines = run(book, *argv)
        check(code == 3 and result_ok(lines, 3) and "move them out first" in lines[-1]
              and all(w in lines[-1] for w in ("final.edited-20260101T000000.md", "backup-20260101-before/", ".git/"))
              and (snapshot(book), snapshot(shelf)) == before,
              f"{' '.join(argv)} over the person's files in the book folder: refused, each named, nothing removed "
              f"({lines[-1][:200]})")
    aside = tmp / "whole" / "aside"
    aside.mkdir()
    for name in ("final.edited-20260101T000000.md", "backup-20260101-before", ".git"):
        shutil.move(str(book / name), str(aside / name))

    # A page sealed again after the delivery: X.md does not hold its text.
    page = book / "proofread" / "page-0003.md"
    kept = page.read_bytes(), page.with_suffix(".json").read_bytes()
    cfb.reseal(book, 3, text=page.read_text(encoding="utf-8") + "\n用戶重新封存嘅新字。\n")
    code, lines = run(book, "--all")
    check(code == 3 and result_ok(lines, 3) and "the text state has moved on since the delivery" in lines[-1]
          and "page(s) 3 in proofread/" in lines[-1] and book.is_dir(),
          f"--all with a page sealed again after the delivery: refused, the page named ({lines[-1][:200]})")
    code, lines = run(book, "--dry-run")
    check(code == 0 and result_ok(lines, 0) and "would free" in lines[-1],
          f"the default cleanup is not held back by the newer page ({lines[-1][:100]})")
    page.write_bytes(kept[0])
    page.with_suffix(".json").write_bytes(kept[1])

    whole = size(book) + size(shelf / f"{STEM}-draft.md")
    code, lines = run(book, "--all", "--dry-run")
    check(code == 0 and book.is_dir() and (shelf / f"{STEM}-draft.md").is_file()
          and f"would free {clean_book.mb(whole)}" in lines[-1],
          f"--all --dry-run: nothing removed, the whole folder's bytes ({lines[-1][:160]})")
    code, lines = run(book, "--all")
    check(code == 0 and result_ok(lines, 0) and not book.exists() and not (shelf / f"{STEM}-draft.md").exists()
          and sorted(p.name for p in shelf.iterdir()) == sorted([PDF_NAME, f"{STEM}.md", f"{STEM}-doubts.md",
                                                                 f"{STEM}.edited-20260101T000000.md"])
          and f"freed {clean_book.mb(whole)}" in lines[-1],
          f"--all: the folder and the unedited draft copy gone, nothing else beside the PDF touched ({lines[-1][:160]})")


def pdf_inside_checks(tmp: Path) -> None:
    """The PDF, and so X.md and X-doubts.md, inside the book folder: SKILL.md
    says not to open BOOK beside the PDF, but a survey run by hand can; --all
    would take all three with the folder."""
    layouts = {"direct": ("work", None), "relative": ("work/source", f"source/{PDF_NAME}"), "parent": ("work/shelf", None)}
    for name, (where, source) in layouts.items():
        book, pdf = delivered_book(tmp, f"inside-{name}", pdf=tmp / f"inside-{name}" / where / PDF_NAME, source_pdf=source)
        final, doubts = pdf.parent / f"{STEM}.md", pdf.parent / f"{STEM}-doubts.md"
        assert book == pdf.parent or book in pdf.parents
        before = snapshot(book)
        for argv in (("--all", "--dry-run"), ("--all",)):
            code, lines = run(book, *argv)
            check(code == 3 and result_ok(lines, 3) and "lie inside the book folder" in lines[-1]
                  and "--all would remove the book itself" in lines[-1] and snapshot(book) == before,
                  f"PDF in BOOK ({name}): {' '.join(argv)} refused, nothing removed ({lines[-1][:160]})")
        code, lines = run(book, "--dry-run")
        left = "\n".join(lines).split("[clean] left alone:", 1)[-1].split("[clean] RESULT", 1)[0]
        check(code == 0 and "beside the PDF" not in left
              and sum(PDF_NAME in line for line in left.splitlines()) == (1 if name == "direct" else 0),
              f"PDF in BOOK ({name}): the default lists the PDF and the copies once, as the book folder's")
        code, lines = run(book)
        check(code == 0 and pdf.is_file() and final.is_file() and doubts.is_file()
              and not (pdf.parent / f"{STEM}-draft.md").exists() and not (book / "renders").exists(),
              f"PDF in BOOK ({name}): the default cleanup leaves the PDF, X.md and X-doubts.md, removes the rest ({lines[-1][:100]})")


def fallback_checks(tmp: Path) -> None:
    """The removal set is the finish's inputs, never a folder of the right name."""
    book, pdf = delivered_book(tmp, "elsewhere")
    elsewhere = tmp / "elsewhere" / "sealed-elsewhere"
    shutil.copytree(book / "proofread", elsewhere)
    report = cfb.report(book)
    report["inputs"]["proofread"] = str(elsewhere)
    write_report(book, report)
    pictures = sorted(p.name for p in (book / "proofread").glob("*.[pP][nN][gG]"))
    kept = snapshot(elsewhere)
    code, lines = run(book, "--dry-run")
    check(code == 0 and "proofread/ crops" not in "\n".join(lines),
          f"sealed pages read elsewhere: no crops planned from BOOK/proofread/ ({[l for l in lines if 'proofread' in l][:2]})")
    code, lines = run(book)
    check(code == 0 and sorted(p.name for p in (book / "proofread").glob("*.[pP][nN][gG]")) == pictures
          and snapshot(elsewhere) == kept and not (book / "renders").exists(),
          "sealed pages read elsewhere: BOOK/proofread/'s pictures and the folder elsewhere untouched, the rest cleaned")

    book, pdf = delivered_book(tmp, "twomanifests")
    real = book / "renders" / "page-manifest.json"
    real.write_bytes((book / "page-manifest.json").read_bytes())
    stale = json.loads((book / "page-manifest.json").read_text(encoding="utf-8"))
    for page in stale["pages"]:
        page["render_file"] = f"old-renders/page-{page['scan_page']:04d}.png"
    (book / "page-manifest.json").write_text(json.dumps(stale, indent=1), encoding="utf-8")
    report = cfb.report(book)
    report["inputs"]["page_manifest"] = str(real)
    write_report(book, report)
    shutil.copytree(book / "renders", book / "old-renders", ignore=shutil.ignore_patterns("page-manifest.json"))
    code, lines = run(book, "--dry-run")
    listed = "\n".join(lines)
    check(code == 0 and "  renders/ (20 page renders)" in listed and "old-renders/ (" not in listed
          and "\n  old-renders/" in listed.split("[clean] left alone:", 1)[-1],
          "two manifests: the plan comes from the one the finish used, old-renders/ is left alone")
    code, lines = run(book)
    how = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))["howToResume"]
    check(code == 0 and not list((book / "renders").glob("page-*.png")) and real.is_file()
          and len(list((book / "old-renders").glob("page-*.png"))) == 20
          and f"{shlex.quote(str(book / 'renders'))} --renders-only" in how,
          f"two manifests: renders/ cleaned, old-renders/ kept, the resume command names the finish's manifest folder ({how[:200]})")


def symlink_checks(tmp: Path) -> None:
    book, pdf = delivered_book(tmp, "linked")
    shared = tmp / "linked" / "shared-renders"
    shutil.move(str(book / "renders"), str(shared))
    (book / "renders").symlink_to(shared, target_is_directory=True)
    kept = snapshot(shared)
    code, lines = run(book)
    check(code == 0 and not (book / "renders").exists() and not (book / "renders").is_symlink()
          and snapshot(shared) == kept and len(kept) == 20,
          f"a renders/ symlink is unlinked, the folder it pointed to untouched ({lines[-1][:120]})")
    listed = "\n".join(lines)
    check("renders/" in listed and "a symlink: unlinked, not followed" in listed, "the listing says it was a symlink")

    outside = tmp / "linked" / "outside.txt"
    outside.write_bytes(b"not the book's")
    try:
        clean_book.remove(outside, book)
        refused = False
    except clean_book.Refused:
        refused = True
    check(refused and outside.is_file(), "remove() refuses a path outside the book folder")
    link = book / "ocr-paddle-link"
    link.symlink_to(outside)
    clean_book.remove(link, book)
    check(not link.is_symlink() and outside.is_file(), "remove() unlinks a symlink in the book folder, not its target")


@contextlib.contextmanager
def unwritable(folder: Path):
    """FOLDER made (with a file in it) and closed to writing, so a removal
    inside it fails; opened again on the way out."""
    folder.mkdir()
    (folder / "Q3-held.png").write_bytes(b"cannot be unlinked")
    folder.chmod(0o555)
    try:
        yield
    finally:
        folder.chmod(0o755)


def stopped_checks(tmp: Path) -> None:
    """A removal that fails part way, and a Refused raised inside the removal."""
    book, pdf = delivered_book(tmp, "stopped")
    shelf = pdf.parent
    with unwritable(book / "repaired" / "crops" / "held"):
        code, lines = run(book)
        check(code == 1 and result_ok(lines, 1) and "stopped at [Errno 13]" in lines[-1]
              and f"after removing 2 item(s) (renders/ (20 page renders), {CROPS_LABEL})" in lines[-1]
              and lines[-1].endswith("- run the same command again"),
              f"a folder that cannot be written in: exit 1, what went and what to do ({lines[-1][:200]})")
        cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))
        check("[Errno 13]" in cleaned.get("stopped", "") and {e["path"] for e in cleaned["removed"]} == {"renders", "proofread"}
              and cleaned["bytesFreed"] == sum(e["bytes"] for e in cleaned["removed"]) and "--renders-only" in cleaned["howToResume"]
              and cleaned["inputs"]["page_manifest"] == str(book / "page-manifest.json"),
              f"cleaned.json after the stop: `stopped`, the items gone, their bytes, how to resume ({cleaned.get('stopped', '')[:80]})")
        check(not (book / "renders").exists() and not list((book / "proofread").glob("page-*-*.png"))
              and (book / "repaired" / "crops" / "held").is_dir() and (book / "draft.md").is_file()
              and (shelf / f"{STEM}-draft.md").is_file(),
              "the stop left the items after the failed one, the draft among them")
        code, lines = cfb.finish(book, deliver=True)
        check(code == 2 and "cleaned.json" in lines[0] and "--renders-only" in lines[0],
              f"finish_book.py after a stopped cleanup: exit 2 with the resume command ({lines[0][:120]})")
    code, lines = run(book)
    cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))
    check(code == 0 and result_ok(lines, 0) and not (book / "repaired" / "crops").exists() and not (book / "draft.md").exists()
          and not (shelf / f"{STEM}-draft.md").exists() and "stopped" not in cleaned
          and {e["path"] for e in cleaned["removed"]} >= {"repaired/crops", "draft.md"},
          f"the same command again finishes the job, the exit-2 finish notwithstanding ({lines[-1][:120]})")

    book, pdf = delivered_book(tmp, "midway")
    original = clean_book.remove
    calls = {"n": 0}

    def flaky(path: Path, book_: Path, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise clean_book.Refused(f"refusing to remove {path}: not under {book_}")
        return original(path, book_, **kw)

    clean_book.remove = flaky
    try:
        code, lines = run(book)
    finally:
        clean_book.remove = original
    gone = [p for p in ("renders/page-0001.png", "renders/page-0002.png") if not (book / p).exists()]
    check(code == 1 and result_ok(lines, 1) and "stopped at refusing to remove" in lines[-1]
          and "after removing 0 item(s) - run the same command again" in lines[-1] and "nothing removed" not in lines[-1]
          and len(gone) == 2 and (book / "renders" / "page-0003.png").is_file(),
          f"a Refused inside the removal: STOPPED, never 'nothing removed' ({lines[-1][:160]})")
    cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))
    check("refusing to remove" in cleaned.get("stopped", "") and cleaned["removed"] == [],
          "cleaned.json records the stop with no whole item gone")


def finish_checks(tmp: Path) -> None:
    """finish_book.py's fifth step."""
    book, pdf = delivered_book(tmp, "fifth", temporaries=False)
    shelf = pdf.parent
    wanted = expected_bytes(book, shelf, renders=False)
    code, lines = cfb.finish(book, deliver=True, keep_work=False)
    report = cfb.report(book)
    step = report["steps"].get("clean") or {}
    cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8")) if (book / "cleaned.json").is_file() else {}
    check(code == 0 and step.get("status") == "cleaned" and step.get("renders") == "kept"
          and step.get("bytesFreed") == wanted == cleaned.get("bytesFreed")
          and lines[0].endswith(f"; freed {clean_book.mb(wanted)}") and "WARNING" not in lines[0],
          f"finish_book.py: the fifth step cleans with the renders kept; the report and the RESULT line say so "
          f"({lines[0][-80:]}, {step}, {[(e['path'], e['bytes']) for e in cleaned.get('removed', [])]})")
    check(len(list((book / "renders").glob("page-*.png"))) == 20 and not list((book / "proofread").glob("page-*-*.png"))
          and not (book / "repaired" / "crops").exists() and not (book / "draft.md").exists()
          and not (shelf / f"{STEM}-draft.md").exists() and (book / "cleaned.json").is_file()
          and (book / "final.md").is_file() and (shelf / f"{STEM}.md").is_file(),
          "finish_book.py: the renders stay, the crops and the draft go, the text state and the copies beside the PDF there")
    log = (book / "finish" / finish_book.LOG_NAME).read_text(encoding="utf-8")
    check(f"step 5 of {finish_book.STEPS}: clean up the working files" in log and "[finish] [clean] removing:" in log
          and "renders/ kept (finish_book.py keeps the renders" in log and "--clean-renders removes them" in log
          and "[finish] RESULT exit=0 DONE clean_book.py" not in log,
          "the log holds step 5, the listing, why the renders stay, and no second RESULT line")
    check(cleaned.get("rendersKept") is True and "pdftoppm" not in cleaned and "were kept" in cleaned.get("howToResume", ""),
          f"cleaned.json: the renders kept, so no resume command ({cleaned.get('howToResume', '')[:80]})")
    code, lines = cfb.finish(book, deliver=True, keep_work=False)
    step = cfb.report(book)["steps"].get("clean") or {}
    check(code == 0 and step.get("status") == "cleaned" and len(list((book / "renders").glob("page-*.png"))) == 20
          and lines[0].endswith("; freed 0.0 MB"),
          f"a later finish (a repair cycle) needs no re-render, and the renders are still there after it ({lines[0][-60:]})")
    helptext = finish_book.build_parser().format_help()
    check(finish_book.STEPS == 5 and "--keep-work" in helptext and "--clean-renders" in helptext,
          "finish_book.py: five steps, the --keep-work and --clean-renders switches")

    book, pdf = delivered_book(tmp, "cleanrenders", temporaries=False)
    shelf = pdf.parent
    wanted = expected_bytes(book, shelf)
    code, lines = cfb.finish(book, "--clean-renders", deliver=True, keep_work=False)
    step = cfb.report(book)["steps"].get("clean") or {}
    cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8"))
    check(code == 0 and step.get("status") == "cleaned" and step.get("renders") == "removed" and step.get("bytesFreed") == wanted
          and not (book / "renders").exists() and cleaned.get("rendersKept") is False
          and str(cleaned.get("pdftoppm", "")).startswith("pdftoppm") and "--renders-only" in cleaned["howToResume"]
          and lines[0].endswith(f"; freed {clean_book.mb(wanted)}"),
          f"--clean-renders: the renders go too, the report and cleaned.json say so ({step})")
    code, lines = cfb.finish(book, deliver=True)
    check(code == 2 and f"python3 scripts/survey_pdf.py {shlex.quote(str(pdf))} {shlex.quote(str(book))} --renders-only" in lines[0],
          f"the finish after it: exit 2 with the whole resume command ({lines[0][:200]})")

    book, pdf = delivered_book(tmp, "kept", temporaries=False)
    shelf = pdf.parent
    before = snapshot(book), snapshot(shelf)
    code, lines = cfb.finish(book, deliver=True, keep_work=True)
    step = cfb.report(book)["steps"].get("clean") or {}
    check(code == 0 and step.get("status") == "kept" and step.get("why") == "--keep-work"
          and lines[0].endswith("; work kept") and (book / "renders").is_dir()
          and len(list((book / "proofread").glob("page-*-*.png"))) == 3 and (shelf / f"{STEM}-draft.md").is_file(),
          f"--keep-work: the pictures stay, the report and the RESULT line say so ({lines[0][-60:]}, {step})")
    code, lines = cfb.finish(book, deliver=False, keep_work=False)
    step = cfb.report(book)["steps"].get("clean") or {}
    check(code == 0 and step.get("status") == "kept" and "--no-deliver" in step.get("why", "")
          and lines[0].endswith("; work kept") and (book / "renders").is_dir(),
          f"--no-deliver: the work is kept, no WARNING for it ({lines[0][-60:]}, {step})")
    after = snapshot(book), snapshot(shelf)
    changed = {k for k in after[0] if k in before[0] and after[0][k] != before[0][k]}
    check(changed <= {"finish/finish-book.log", "finish/finish-report.json"} and after[1] == before[1]
          and set(after[0]) == set(before[0]),
          f"the two kept runs changed only the finish log and report ({sorted(changed)})")

    book, pdf = delivered_book(tmp, "fifthstop", temporaries=False)
    shelf = pdf.parent
    with unwritable(book / "repaired" / "crops" / "held"):
        code, lines = cfb.finish(book, deliver=True, keep_work=False)
        step = cfb.report(book)["steps"].get("clean") or {}
        log = (book / "finish" / finish_book.LOG_NAME).read_text(encoding="utf-8")
        cleaned = json.loads((book / "cleaned.json").read_text(encoding="utf-8")) if (book / "cleaned.json").is_file() else {}
        check(code == 0 and step.get("status") == "stopped" and step.get("items") == 1 and step.get("renders") == "kept"
              and "; cleanup stopped; 1 WARNING line(s): grep -n WARNING " in lines[0]
              and "WARNING the cleanup stopped part way: stopped at [Errno 13]" in log
              and f"after removing 1 item(s) ({CROPS_LABEL}); the rest goes with python3 scripts/clean_book.py "
                  f"{shlex.quote(str(book))} --keep-renders" in log
              and "[Errno 13]" in cleaned.get("stopped", ""),
              f"a stop in the fifth step: `cleanup stopped`, the WARNING names what went and the command, cleaned.json has "
              f"`stopped` ({lines[0][-60:]}, {step})")
        check(not list((book / "proofread").glob("page-*-*.png")) and (book / "repaired" / "crops" / "held").is_dir()
              and (book / "draft.md").is_file() and (book / "renders").is_dir(),
              "the stop in the fifth step left the items after the failed one")
    code, lines = run(book, "--keep-renders")
    check(code == 0 and result_ok(lines, 0) and not (book / "repaired" / "crops").exists() and not (book / "draft.md").exists()
          and (book / "renders").is_dir() and "stopped" not in json.loads((book / "cleaned.json").read_text(encoding="utf-8")),
          f"the command the WARNING names finishes the job ({lines[-1][:120]})")


def renders_only_checks(tmp: Path) -> None:
    """survey_pdf.py --renders-only puts the renders back and verifies them."""
    from PIL import Image, ImageDraw
    folder = tmp / "survey"
    folder.mkdir()
    pdf = folder / "made-up.pdf"
    pages = []
    for number in range(3):
        image = Image.new("L", (200, 300), 255)
        ImageDraw.Draw(image).rectangle([20 + number * 30, 40, 80 + number * 30, 200], fill=0)
        pages.append(image)
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    out = folder / "book"

    def survey(*argv: str) -> tuple[int, str]:
        text = io.StringIO()
        with contextlib.redirect_stdout(text), contextlib.redirect_stderr(text):
            code = survey_pdf.main([str(pdf), str(out), *argv])
        return code, text.getvalue()

    code, said = survey("--dpi", "72", "--jobs", "1")
    manifest, report = out / "page-manifest.json", out / "survey-report.json"
    renders = out / "renders"
    check(code == 0 and manifest.is_file() and len(list(renders.glob("page-*.png"))) == 3,
          f"a made-up PDF surveyed: three renders, the manifest and the report ({code}, {said[-200:]!r})")
    version = json.loads(report.read_text(encoding="utf-8"))["render"].get("tool_version", "")
    check(version.startswith("pdftoppm") and version == survey_pdf.pdftoppm_version(),
          f"the survey report records pdftoppm's version ({version})")
    kept = manifest.read_bytes(), report.read_bytes()
    hashes = {p.name: pp.sha256_file(p) for p in renders.glob("page-*.png")}
    (renders / "page-0002.png").unlink()
    code, said = survey("--renders-only")
    check(code == 0 and (renders / "page-0002.png").is_file() and pp.sha256_file(renders / "page-0002.png") == hashes["page-0002.png"]
          and "rendering  : 1 page(s) [2] at 72 dpi" in said and "verified   : 3 of 3 page(s)" in said,
          f"--renders-only: the missing page rendered again at the report's dpi, every render verified ({said[-300:]!r})")
    check((manifest.read_bytes(), report.read_bytes()) == kept, "--renders-only rewrites neither the manifest nor the report")
    code, said = survey("--renders-only")
    check(code == 0 and "all 3 selected page(s) already present" in said, f"--renders-only with nothing missing: exit 0 ({said[-120:]!r})")
    (renders / "page-0003.png").write_bytes(b"not a render")
    code, said = survey("--renders-only")
    check(code == 2 and "1 render(s) differ from the manifest [3]" in said
          and f"pdftoppm now: {version}; at the survey: {version}" in said and "cannot be repaired in place" in said,
          f"a render that differs from the manifest: exit 2, the page named, both pdftoppm versions named ({said[-300:]!r})")
    code, said = survey("--renders-only", "--overwrite")
    check(code == 0 and pp.sha256_file(renders / "page-0003.png") == hashes["page-0003.png"],
          f"--renders-only --overwrite renders it again ({said[-120:]!r})")
    other = folder / "other.pdf"
    pages[2].save(other)
    text = io.StringIO()
    with contextlib.redirect_stdout(text), contextlib.redirect_stderr(text):
        code = survey_pdf.main([str(other), str(out), "--renders-only"])
    check(code == 2 and "is not the PDF" in text.getvalue(), f"another PDF: exit 2 ({text.getvalue()[-160:]!r})")
    check((manifest.read_bytes(), report.read_bytes()) == kept, "the manifest and the report still keep their bytes")


def images_dir_checks(tmp: Path) -> None:
    """A book from a folder of images: no copy, no cleanup, every byte kept."""
    book = cfb.phase3_book(tmp / "scans" / "work")
    manifest = json.loads((book / "page-manifest.json").read_text(encoding="utf-8"))
    manifest.update(source_pdf="", source_kind="images-dir")
    (book / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    code, lines = cfb.finish(book, deliver=True, keep_work=False)
    log = (book / "finish" / "finish-book.log").read_text(encoding="utf-8")
    check(code == 0 and cfb.result_ok(lines, book) and lines[0].endswith("; work kept")
          and "source_kind is images-dir (no source PDF): skipping beside-PDF copy" in log
          and '"why": "no source PDF"' in log and (book / "renders").is_dir(),
          f"images-dir: finish_book.py copies nothing and cleans nothing, its stdout names BOOK's files ({lines})")
    for argv in ((), ("--all",)):
        before = snapshot(book)
        code, said = run(book, *argv)
        check(code == 3 and result_ok(said, 3) and said[-1].endswith(
                  f"the page manifest {book / 'page-manifest.json'} names no source PDF: nothing removed")
              and snapshot(book) == before and (book / "final.md").is_file(),
              f"images-dir: clean_book.py {' '.join(argv) or '(default)'} refuses, nothing removed "
              f"({said[-1] if said else said})")


def skill_checks() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    finish = workflow.split("\n## 一個 command 做完（finish_book.py）", 1)[-1].split("\n## ", 1)[0]
    clean = (workflow.split("\n## 清走工作檔（clean_book.py）", 1)[-1].split("\n## ", 1)[0]
             if "\n## 清走工作檔（clean_book.py）" in workflow else "")
    run = skill.split("\n## 四、", 1)[-1].split("\n## ", 1)[0]
    folder = skill.split("\n## 三、", 1)[-1].split("\n## ", 1)[0]
    exits = skill.split("\n## 七、", 1)[-1].split("\n## ", 1)[0]
    stitch = next((line for line in workflow.splitlines() if "stitch-01.png" in line and "清走" in line), "")
    deliveries = (workflow.split("\n## 三次交付同本書目錄", 1)[-1].split("\n## ", 1)[0]
                  if "\n## 三次交付同本書目錄" in workflow else "")
    wanted = {
        "--all as the last step of the run": '"$JYUT_OCR_PYTHON" scripts/clean_book.py BOOK --all' in run
                                             and "淨係留 PDF 隔籬嘅交付檔" in run
                                             and all(f"`{name}`" in folder for name in ("X.md", "X-doubts.md")),
        "its exits, and a refusal passed on, nothing deleted by hand": all(w in exits for w in (
            "`clean_book.py BOOK --all`", "3 拒絕", "唔好自己刪", "唔好為咗清走而改 `X.md`", "1 刪到一半停咗")),
        "the switches named": all(f"`{s}`" in skill for s in ("--keep-work", "--clean-renders", "--dry-run",
                                                              "--keep-renders", "--all"))
                              and "--renders-only" in clean,
        "the reference: the fifth step, what goes and what stays": "第 5 步" in clean and all(
            w in clean for w in ("`repaired/crops/`", "`book-profile-crops/`", "`BOOK/draft.md`", "`X-draft.md`",
                                 "`backup-*/`", "`page-NNNN-adjudication-NN.png`")),
        "the reference: the renders kept by the fifth step, and why": all(w in clean for w in (
            "同 render，方便原地修書", "pdftoppm", "`--clean-renders`", "`--keep-renders`")),
        "the reference: the measurement says what was measured": "實測（du）" in clean and "會刪走" in clean
                                                                  and "MB" in clean,
        "the reference: the resume command": "`survey_pdf.py PDF MANIFEST_DIR --renders-only`" in clean
                                              and "`MANIFEST_DIR`" in clean and "`render_sha256`" in clean
                                              and "寫好成條 command" in clean,
        "the reference: a run still working on the book": "其他程序仲用緊本書" in clean
                                                          and "`wait_for_run.py` 同 `tail -f` 唔計" in clean,
        "the reference: stopped part way": "`; cleanup stopped`" in clean and "`stopped`" in clean,
        "the reference: --all as the last step after a delivery, and its refusals": "`clean_book.py BOOK --all`"
                                                           in clean and "最後一步" in clean and "2026-10-03" in clean
                                                           and all(w in clean for w in (
                                                               "`backup-*/`", "`.git`", "`final.md` 之後有頁重新封過",
                                                               "`*.edited-*.md`")),
        "the reference: cleaned.json and the RESULT words": "`BOOK/cleaned.json`" in clean
                                                            and "`; work kept`" in clean and "`; freed M MB`" in clean,
        "the stitch comparison image goes with the cleanup": "stitch_tables.py" in stitch,
        "images-dir: no copy, the files in BOOK, BOOK kept (三, 四.6, 九)":
            "由掃描圖資料夾開嘅書冇 PDF" in folder and "`BOOK/final.md`、`BOOK/doubts.md`" in folder
            and "`BOOK` 唔清走" in folder and "掃描圖資料夾開嘅書唔跑佢" in run and "交付檔淨係喺 `BOOK`" in run
            and "掃描圖書係 `BOOK/final.md`、`BOOK/doubts.md`" in skill.split("\n## 九、", 1)[-1],
        "images-dir: the reference says what each script says and does": all(w in deliveries for w in (
            "`survey_pdf.py BOOK --images-dir DIR`", "`images-dir`", "names no source PDF", "`; work kept`",
            "skipping beside-PDF copy", "唔跑 `--all`", "唔好自己刪 `BOOK`")) and "掃描圖資料夾開嘅書冇 PDF" in clean,
        "the reference: five steps and the lists": "step K of 5" in finish and "`clean_book.py`" in finish
                                                   and "`--keep-work`" in finish and "--renders-only" in clean
                                                   and "`--clean-renders`" in clean and "MANIFEST_DIR" in clean
                                                   and "`stopped`" in clean and "實測（du）" in clean,
    }
    for name, ok in wanted.items():
        check(ok, f"the skill's text: {name}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="clean-book-") as name:
        tmp = Path(name)
        refusal_checks(tmp)
        default_checks(tmp)
        edited_checks(tmp)
        all_checks(tmp)
        pdf_inside_checks(tmp)
        fallback_checks(tmp)
        symlink_checks(tmp)
        stopped_checks(tmp)
        finish_checks(tmp)
        renders_only_checks(tmp)
        images_dir_checks(tmp)
    skill_checks()
    print(f"{'FAIL' if FAILURES else 'ok  '} clean_book.py: {len(FAILURES)} problem(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
