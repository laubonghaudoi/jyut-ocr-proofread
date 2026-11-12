#!/usr/bin/env python3
"""Clean a finished book's working files: the pictures the pipeline cuts
again from the renders, the stale assemblies, its temporary files and the
draft.  The text state stays; the renders go unless --keep-renders.

    python3 scripts/clean_book.py BOOK [--dry-run] [--keep-renders] [--all]

BOOK is finish_book.py's book folder.  finish_book.py runs this cleanup
itself as its fifth step, with the renders kept, once both copies are
beside the PDF (--keep-work: not at all; --clean-renders: the renders too);
this command is for a book finished before that step existed, for the
renders once the person wants them gone, for --dry-run, and for --all.

Why.  Measured by du on two finished books (2026-10-02): the pictures are
about nine tenths of what the pipeline writes.  A 609-page book's folder
held 635 MB - renders/ 132 MB, phase 3's adjudication and stitch crops
under proofread/ 189 MB, repaired/crops/ 119 MB, book-profile-crops/ 13
MB, a stale assembly 4 MB, and a backup folder the person's session made
132 MB - against some 45 MB of text state (that book was measured, not
cleaned); of a 396-page book's 411 MB (136 MB of it the same kind of
backup) this command freed 250 MB on a scratch copy, renders included.
The text state is what a later repair in place reads (the user's rule:
repair the output in place, no full re-run): the sealed pages, both
engines' drafts, repaired/ without its crops, assembled/, the profile, the
page manifest, finish/, final.md and doubts.md.  The crops are cut from
the renders as needed.  The renders come back from the PDF -
`survey_pdf.py PDF MANIFEST_DIR --renders-only` renders the pages missing
from renders/ and verifies each against the page manifest's
render_sha256, rewriting nothing else - but as the same bytes only while
pdftoppm renders them the same: the repair in place verifies every render
against the manifest, so with another poppler the book could not be
repaired in place at all (3 pages of the 396-page book rendered the same
under poppler 26.01 after the OS upgrade of 2026-10-02; today's
measurement, not a guarantee).  That is why finish_book.py keeps them, and
why cleaned.json records the pdftoppm installed when they went.

What goes, by default - each item printed with its bytes:
- the renders folder the page manifest names (--keep-renders keeps it);
- phase 3's crops directly in the sealed pages' folder, by their names
  (page-NNNN-adjudication-NN.png, -chart-NN.png, -table-unread-NN.png,
  -stitch-NN.png); any other picture there is the person's and stays;
- repaired/crops/ (an earlier repair's question crops) and book-profile-crops/;
- assembled.stale-*/, .assembled.partial/, and the temporary files the
  pipeline writes beside its outputs (left only by a crash): only the
  shapes it writes, for the outputs it writes in that folder;
- BOOK/draft.md, and X-draft.md beside the PDF when it hashes to a version
  the pipeline wrote there (one the person edited stays).
The sealed pages' folder, the profile and the page manifest are the ones
finish_book.py's report (or finish_book.py itself, in its fifth step)
names, when they lie in BOOK; one outside BOOK contributes nothing, and no
folder of the same name in BOOK stands in for it.  Everything else stays
and is listed as left alone: the text state, the logs, finish/, backup-*/
or anything the pipeline did not write (BOOK/figures/ too, the figures'
pictures), and beside the PDF the PDF itself, X.md, X-doubts.md, X-圖/ and
the *.edited-*.md copies.

Before anything is removed, every one of these must hold, else exit 3
with one line saying which, and nothing is removed:
- finish/finish-report.json says the last finish_book.py ended with exit 0
  (or stopped at its inputs for want of the renders this command removed:
  its line names cleaned.json and --renders-only);
- the page manifest names a PDF that exists and hashes to the recorded
  SHA-256;
- no finish_book.py runs on the book (finish/.lock) and no delivery is
  being written (.delivered.lock); both locks are held while cleaning;
- no other process still works on the book: one of the pipeline's commands
  that names BOOK, works in it or has a file of one of its folders open
  (phase 3 and repair_book.py hold no lock a cleanup could check), or any
  process that names BOOK and works in it or has such a file open;
  wait_for_run.py, which only reads along, and a `tail -f` on a log at the
  book's root or in finish/ do not count;
- delivered.json records X.md and X-doubts.md beside the PDF, both are
  there, BOOK/final.md is the version last copied there and each picture
  of BOOK/figures/ the version last copied into X-圖/.  A copy there that
  hashes to none of its recorded versions (or a picture gone from X-圖/) is
  the person's now: the default cleanup still runs; --all is refused and
  says why.

--all removes the whole book folder (with everything in it) and the
unedited X-draft.md, nothing else (X-圖/ stays with X.md): the last step
after a delivery (SKILL.md; the user's decision of 2026-10-03: the work
folder goes, X.md and X-doubts.md beside the PDF are the book, a repair in
place is given up for the space).  It is refused, nothing removed, when
X.md, X-doubts.md or a picture in X-圖/ beside the PDF was edited or
removed (the folder then holds the only copy of the pipeline's); when the
PDF, X.md, X-doubts.md or X-圖/ lies inside BOOK (they would go with it);
when BOOK holds *.edited-*.md, backup-*/ or .git (the person's, not to be
made again: they move them out first); or when a sealed page in BOOK is
not the one final.md was built from (assembled/page-sources.json's
hashes: the text state moved on since the delivery - finish_book.py
first).
--dry-run prints the same list and removes nothing.  Running it twice is
harmless: the second run finds nothing to remove (exit 0).

Every deletion goes through remove(), which refuses a path not under BOOK
(the draft copy beside the PDF apart) and unlinks a symlink instead of
following it (the offline checks' books link renders/ and the drafts to
shared folders).  After a default cleanup BOOK/cleaned.json records what
went, what stayed, the bytes freed, the inputs, how to resume and - when
the renders went - the pdftoppm installed; a cleanup stopped part way
writes it too, with `stopped`.  stdout ends with
`[clean] RESULT exit=N DONE|STOPPED|REFUSED clean_book.py: ...`.

Exit codes:
    0  done - also a dry run, and a run that finds nothing left to remove
    1  stopped part way: a removal failed (the line says which; what was
       removed before it is listed, and cleaned.json records it with
       `stopped`) - run the same command again
    2  wrong arguments, or BOOK is not a folder
    3  refused: a condition above does not hold; nothing was removed
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _deliver  # noqa: E402
import _figures  # noqa: E402
import finish_book  # noqa: E402

TOOL = "clean_book.py"
RESULT_PREFIX = "[clean] RESULT"
EXIT_DONE, EXIT_STOPPED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
STATE = {EXIT_DONE: "DONE", EXIT_STOPPED: "STOPPED", EXIT_USAGE: "REFUSED", EXIT_REFUSED: "REFUSED"}
# book_profile.py cuts its probe and variant crops into <profile stem>-crops/<run stamp>/
# beside the profile (book-profile-crops/ for book-profile.json).
PROFILE_CROPS_SUFFIX = "-crops"
# The question crops an earlier version of repair_book.py cut into repaired/crops/.
REPAIR_CROPS = "crops"
PAGE_RENDER = re.compile(r"page-\d{4,}\.png")
PAGE_MARKDOWN = re.compile(r"page-\d{4,}\.md")
# Phase 3's crops beside the sealed pages: page-NNNN-adjudication-NN.png, -chart-NN.png and
# -table-unread-NN.png (proofread_pages.py) and page-NNNN-stitch-NN.png (stitch_tables.py).
# Nothing else there is the pipeline's: a Cover.PNG or reference-scan.png the person put there
# stays (an earlier *.png match took both, on a made-up book in review).
CROP = re.compile(r"page-\d{4,}-[a-z-]+-\d+\.png")
# survey_pdf.py's staging folders under renders/ (tempfile.mkdtemp's names), left only by a kill.
STAGING = re.compile(r"tmp[a-z0-9_]{6,}")
# finish_book.py's move-aside of the person's corrections: final.edited-<time>.md,
# doubts.edited-<time>.md (keep_edited), and _deliver.py's X.edited-<time>.md beside the PDF.
EDITED = re.compile(r".+\.edited-[^/]+\.md")
RENDERS_ONLY = "--renders-only"            # survey_pdf.py's switch that puts the renders back
WIDTH = 64                                 # the listing's label column
# The commands that work on a book: every script of this folder but the two the agent runs
# alongside a run, which only read (wait_for_run.py; build_progress.py, which it calls, writes
# proofreading.md and nothing the cleanup touches).
READERS = frozenset({"wait_for_run.py", "build_progress.py"})
COMMANDS = frozenset(path.name for path in HERE.glob("*.py")) - READERS


class Refused(Exception):
    """Nothing is removed: the message says which condition does not hold."""


@dataclass
class Item:
    """One thing removed or left alone: PATH, how the listing names it, its
    size (file sizes by lstat - a symlink counts as itself - not disk
    blocks), a note; FILES when only files under PATH go (renders/'s page
    renders, the crops in the sealed pages' folder), and whether PATH then
    stays even when empty."""
    path: Path
    label: str
    size: int
    note: str = ""
    files: list[Path] = field(default_factory=list)
    keep_folder: bool = False
    short: str = ""                            # the RESULT line's word for it (the label without its count)

    def __post_init__(self) -> None:
        self.short = self.short or self.label

    def as_dict(self, book: Path) -> dict[str, Any]:
        where = str(self.path.relative_to(book)) if book in self.path.parents else str(self.path)
        return {"path": where, "bytes": self.size, **({"note": self.note} if self.note else {})}


@dataclass
class Copies:
    """The copies beside the PDF as delivered.json records them: by kind,
    their paths and recorded SHA-256s; EDITED names the X.md / X-doubts.md
    and the pictures in X-圖/ that hash to no recorded version (the
    person's now) or are gone; FIGURES is the X-圖/ folder."""
    paths: dict[str, Path]
    versions: dict[str, list[str]]
    edited: list[str]
    figures: Path


@dataclass
class Result:
    code: int
    why: str                                   # the RESULT line after the tool's name
    lines: list[str] = field(default_factory=list)   # the listing, for stdout or finish_book.py's log
    removed: list[Item] = field(default_factory=list)
    left: list[Item] = field(default_factory=list)
    bytes_freed: int = 0

    @property
    def result(self) -> str:
        return f"{RESULT_PREFIX} exit={self.code} {STATE[self.code]} {TOOL}: {self.why}"


def mb(count: int) -> str:
    """Binary megabytes, as du -h shows them, so the figure matches what the
    person sees."""
    return f"{count / 2 ** 20:.1f} MB"


def _bytes(path: Path) -> int:
    """The file sizes under PATH by lstat: a symlink counts as itself and is
    not followed."""
    try:
        status = path.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(status.st_mode):
        return status.st_size
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                total += _bytes(Path(entry.path))
    except OSError:
        pass
    return total


def _inside(book: Path, path: Path) -> bool:
    """Whether PATH lies under BOOK once its parent is resolved - PATH
    itself is not, so a symlink under BOOK counts as the book's (and is
    unlinked, never followed)."""
    parent = Path(os.path.realpath(path.parent))
    return parent == book or book in parent.parents


def _under(book: Path, path: Path) -> bool:
    """Whether PATH, fully resolved, is BOOK or lies under it."""
    real = Path(os.path.realpath(path))
    return real == book or book in real.parents


def _allowed(path: Path, book: Path, beside: Path | None, whole: bool) -> bool:
    if path == book:
        return whole
    return _inside(book, path) or (beside is not None and path == beside)


def remove(path: Path, book: Path, *, beside: Path | None = None, whole: bool = False) -> None:
    """The one place anything is deleted.  PATH must lie under BOOK (its
    parent resolved, itself not: a symlink is unlinked, never followed) or
    be the draft copy BESIDE the PDF; BOOK itself goes only with WHOLE
    (--all).  Anything else is refused untouched."""
    if not _allowed(path, book, beside, whole):
        raise Refused(f"refusing to remove {path}: not under {book}")
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


# --- the conditions ----------------------------------------------------------------------------

def source(book: Path, given: Any) -> tuple[Path, Path]:
    """The page manifest - the one finish_book.py used (GIVEN, its report's
    inputs.page_manifest), else the book's by name - and the PDF it names,
    there and hashing to the recorded SHA-256 (scripts/_deliver.py's check,
    plus: a SHA-256 must be recorded, or renders cut again could not be
    verified).  Two manifests in a book (a stale one at the root, the
    finish's under renders/) were planned from the wrong one by name, on a
    made-up book in review."""
    if isinstance(given, str) and given:
        manifest = Path(given)
        if not manifest.is_file():
            raise Refused(f"the page manifest the finish used is gone ({manifest})")
    else:
        manifest = _deliver.find_manifest(book)
        if manifest is None:
            raise Refused(f"no page manifest in {book}")
    pdf, why = _deliver.source_pdf(book, manifest)
    if pdf is None:
        raise Refused(why)
    recorded = json.loads(manifest.read_text(encoding="utf-8")).get("source_sha256")
    if not (isinstance(recorded, str) and re.fullmatch(r"[0-9a-f]{64}", recorded)):
        raise Refused(f"{manifest.name} records no SHA-256 of the PDF, so renders cut again could not be verified")
    return manifest, pdf


def finished(book: Path) -> Mapping[str, Any]:
    """finish/finish-report.json of a finish_book.py that ended with exit 0."""
    path = book / finish_book.FINISH_DIR / finish_book.REPORT_NAME
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"finish_book.py has not finished this book (no readable {path.relative_to(book)})") from error
    if not isinstance(report, dict):
        report = {}
    status = report.get("exitStatus")
    # A finish_book.py run after this command removed the renders stops at
    # its inputs, before any step, and writes an exit-2 report whose line
    # names cleaned.json and --renders-only: the finished state stands
    # (check_clean_book runs it after the cleanup, then --all).  An exit-2
    # report saying anything else does not: in review, on a made-up book,
    # any exit 2 with no steps passed once cleaned.json existed, and --all
    # then took a page sealed again after the delivery.
    said = str(report.get("result") or "")
    stopped_at_renders = (status == finish_book.EXIT_USAGE and not report.get("steps")
                          and (book / finish_book.CLEANED_NAME).is_file()
                          and finish_book.CLEANED_NAME in said and RENDERS_ONLY in said)
    if status != 0 and not stopped_at_renders:
        raise Refused(f"the last finish_book.py did not end with exit 0 ({path.relative_to(book)} says exit {status})")
    return report


def _cleaned(book: Path) -> dict[str, Any]:
    """cleaned.json as the last cleanup wrote it, or {}."""
    try:
        data = json.loads((book / finish_book.CLEANED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def delivered(book: Path, pdf: Path) -> Copies:
    """X.md and X-doubts.md beside the PDF, recorded in delivered.json and
    there; final.md the version last copied as X.md (its image lines read
    from X-圖/); each picture of BOOK/figures/ the version last copied into
    X-圖/."""
    record = _deliver._record(book)
    paths = {kind: pdf.parent / _deliver.copy_name(pdf, kind) for kind in _deliver.KINDS}
    versions = {kind: record.get(str(path), []) for kind, path in paths.items()}
    edited = []
    for kind in ("final", "doubts"):
        path = paths[kind]
        if not versions[kind]:
            raise Refused(f"{_deliver.RECORD_NAME} records no {path.name} beside the PDF: the book has not been "
                          "delivered there")
        if path.is_symlink() or not path.is_file():
            raise Refused(f"{path.name} is not beside the PDF ({path.parent})")
        if _deliver._sha256_file(path) not in versions[kind]:
            edited.append(path.name)
    final = book / finish_book.FINAL
    try:
        text = final.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise Refused(f"no readable {finish_book.FINAL} in the book folder") from error
    if _deliver._sha256(_deliver.figures_for_copy(text, pdf).encode("utf-8")) != versions["final"][-1]:
        raise Refused(f"{finish_book.FINAL} is not the version last copied beside the PDF as {paths['final'].name} "
                      "(edited since?)")
    folder = _deliver.figures_folder(pdf)
    for picture in _figures.pictures(book / _figures.FOLDER):
        copy = folder / picture.name
        shas = record.get(str(copy), [])
        if not shas or shas[-1] != _deliver._sha256_file(picture):
            raise Refused(f"{_figures.FOLDER}/{picture.name} is not the version last copied beside the PDF into "
                          f"{folder.name}/ (not delivered yet?)")
        if copy.is_symlink() or not copy.is_file() or _deliver._sha256_file(copy) not in shas:
            edited.append(f"{folder.name}/{picture.name}")
    return Copies(paths, versions, edited, folder)


def held_inside(book: Path, pdf: Path, copies: Copies) -> list[Path]:
    """The PDF, X.md, X-doubts.md and X-圖/ when they lie inside BOOK (the
    PDF's folder under it, or the PDF itself or X-圖/ through a link): --all
    would remove them with the folder, and the PDF is gitignored in the person's repo, so
    there would be no other copy.  SKILL.md tells the agent not to open BOOK
    beside the PDF; in review, three such layouts of a made-up book lost
    all three files to --all while the RESULT line called them left alone."""
    return [path for path in (pdf, copies.paths["final"], copies.paths["doubts"], copies.figures)
            if (path.exists() or path.is_symlink()) and (_inside(book, path) or _under(book, path))]


def personal(book: Path) -> list[str]:
    """What BOOK holds that the pipeline did not make and --all could not
    bring back: the person's corrections finish_book.py moved aside
    (*.edited-*.md), a backup-*/ of theirs, a .git.  In review --all took
    all three with a made-up book's folder, named only inside the long
    label of the whole folder."""
    names = []
    for entry in sorted(book.iterdir()):
        if EDITED.fullmatch(entry.name) or entry.name == ".git" or entry.name.startswith("backup-"):
            names.append(entry.name + ("/" if entry.is_dir() and not entry.is_symlink() else ""))
    return names


def text_moved_on(book: Path, sealed: Path | None) -> None:
    """--all is refused when a sealed page in BOOK is not the one final.md
    was built from (assembled/page-sources.json's hashes): X.md beside the
    PDF would not hold the newer text.  SEALED is the finish's folder of
    sealed pages; one outside BOOK does not go with the folder.  In review
    a page of a made-up book sealed again after the delivery went with the
    folder under --all."""
    if sealed is None or not _under(book, sealed):
        return
    listed = book / finish_book.ASSEMBLED / "page-sources.json"
    try:
        rows = {row.get("scanPage"): row for row in json.loads(listed.read_text(encoding="utf-8")).get("pages")
                if isinstance(row, dict)}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise Refused(f"no readable {_label(book, listed)}, so whether the sealed pages are still the ones "
                      f"{finish_book.FINAL} was built from cannot be told: --all refused") from error
    moved = []
    for markdown in _files(sealed, PAGE_MARKDOWN):
        scan = int(markdown.stem.split("-")[1])
        row = rows.get(scan) or {}
        record = markdown.with_suffix(".json")
        try:
            same = (row.get("markdownSha256") == _deliver._sha256_file(markdown)
                    and (not record.is_file() or row.get("jsonSha256") == _deliver._sha256_file(record)))
        except OSError:
            same = False
        if not same:
            moved.append(scan)
    if moved:
        raise Refused(f"the text state has moved on since the delivery: page(s) {finish_book._compress(moved)} in "
                      f"{_label(book, sealed)}/ are not the ones {finish_book.FINAL} was built from "
                      f"({_label(book, listed)}) - run finish_book.py again first; --all refused")


def _ancestors(pid: int) -> set[int]:
    """PID and its parents up to init: finish_book.py calls clean() in its
    own process, and the shell that started either command may hold the log
    it redirected into the book folder."""
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            pid = int(raw[raw.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return seen


def _uses(pid: int, book: Path) -> str:
    """Why the process works in BOOK - its working directory, or a file it
    has open in one of the book's folders (not a log or report at the
    book's root or in finish/: a `tail -f BOOK/finish.log` beside the run
    is in nobody's way) - or '' when it does not (or cannot be read:
    another user's)."""
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = ""
    if cwd and _under(book, Path(cwd)):
        return f"its working directory is {cwd}"
    try:
        names = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return ""
    for name in names:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{name}")
        except OSError:
            continue
        real = Path(os.path.realpath(target))
        if target.startswith("/") and book in real.parents:
            parts = real.relative_to(book).parts
            if len(parts) > 1 and parts[0] != finish_book.FINISH_DIR:
                return f"it has {target} open"
    return ""


def working(book: Path) -> str | None:
    """A live process, other than this one and its ancestors, still working
    on BOOK: one of the pipeline's COMMANDS that names BOOK, works in it or
    has a file of one of its folders open, or any process that names BOOK
    and works in it or has such a file open; a few words naming it, or
    None.  Phase 3 and
    repair_book.py take no lock a cleanup could check (phase 3's
    proofread/.table-stitch.lock is held only while tables are stitched),
    and a run between two pages may hold no file of the book open, so a
    pipeline command given the book on its command line counts by that
    alone.  wait_for_run.py reads the log and the sealed pages along a run
    and names BOOK: it is left out, else the fifth step would be refused
    whenever the agent waits as SKILL.md tells it.  In review the cleanup
    removed a render from under a process that held it open."""
    skip = _ancestors(os.getpid())
    named = re.compile(r"(?<![\w./-])" + re.escape(str(book)) + r"(?![\w.-])")
    try:
        pids = sorted(int(entry.name) for entry in os.scandir("/proc") if entry.name.isdigit())
    except OSError:
        return None
    for pid in pids:
        if pid in skip:
            continue
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            status = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            continue
        try:
            state = status[status.rindex(")") + 2:].split()[0]
        except (ValueError, IndexError):
            state = "?"
        if not raw or state in "ZX":
            continue                # a kernel thread; gone, or exited and not yet reaped
        argv = [word.decode("utf-8", "surrogateescape") for word in raw.rstrip(b"\0").split(b"\0")]
        code = argv[argv.index("-c") + 1] if "-c" in argv[1:-1] else ""
        if "from multiprocessing." in code:
            continue                # a process pool's helper: Python 3.14's forkserver carries its parent's command line
        script = next((os.path.basename(word) for word in argv
                       if os.path.basename(word) in COMMANDS or os.path.basename(word) in READERS), None)
        if script in READERS:
            continue
        names = any(named.search(word) for word in argv)
        if not (script or names):
            continue
        uses = _uses(pid, book)
        if (script and (names or uses)) or (names and uses):
            return f"pid {pid} ({script or os.path.basename(argv[0])}): {uses or 'the book folder is on its command line'}"
    return None


# --- what goes and what stays --------------------------------------------------------------------

def _input_folder(book: Path, inputs: Mapping[str, Any], key: str, default: str) -> Path | None:
    """The folder finish_book.py used for KEY (its report's inputs: resolved
    paths), exactly that one when it lies in the book folder, and nothing
    when it lies outside: a BOOK/DEFAULT then is not the one the finish
    read (in review, on a made-up book finished from sealed pages
    elsewhere, the fallback took a picture of the person's from
    BOOK/proofread/).  Only inputs that name nothing at all fall back to
    BOOK/DEFAULT (a finish_book.py stopped at its inputs writes none; the
    last cleanup's cleaned.json then supplies the finish's)."""
    given = inputs.get(key)
    if isinstance(given, str) and given:
        path = Path(given)
        return path if _inside(book, path) and (path.is_symlink() or path.exists()) else None
    if inputs:
        return None
    path = book / default
    return path if path.is_symlink() or path.exists() else None


def renders_folder(book: Path, manifest: Path) -> Path | None:
    """The folder of page renders: the one the page manifest's render_file
    paths name (relative to the manifest, as survey_pdf.py writes them),
    else BOOK/renders - a folder below the book's (lexically: a symlinked
    folder is still the book's, and is unlinked, not followed)."""
    candidates = []
    try:
        pages = json.loads(manifest.read_text(encoding="utf-8")).get("pages") or []
        first = next(p["render_file"] for p in pages if isinstance(p, dict) and isinstance(p.get("render_file"), str))
        candidates.append(Path(os.path.normpath(manifest.parent / first)).parent)
    except (OSError, ValueError, StopIteration, TypeError, AttributeError):
        pass
    candidates.append(book / "renders")
    for path in candidates:
        if book in path.parents and (path.is_symlink() or path.is_dir()):
            return path
    return None


def _files(folder: Path, pattern: re.Pattern[str]) -> list[Path]:
    """The files (and symlinks to files, unlinked not followed) directly in
    FOLDER whose whole name matches PATTERN (case matters: Cover.PNG is not
    a crop)."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        if not pattern.fullmatch(name):
            continue
        path = folder / name
        if path.is_symlink() or path.is_file():
            out.append(path)
    return out


def _label(book: Path, path: Path) -> str:
    try:
        return str(path.relative_to(book))
    except ValueError:
        return str(path)


def temporaries(book: Path, repaired: Path, profile: Path | None) -> list[tuple[Path, re.Pattern[str]]]:
    """Where the pipeline's temporary files are and what they are called, by
    the output each stands for: .<output>.tmp (finish_book.py,
    repair_book.py), .<output>.<random>.tmp (_deliver.py's and
    build_draft.py's tempfile.mkstemp) and <output>.json.tmp (book_profile.py),
    each in the folder that output is written to.  One is left only by a
    crash.  A .tmp of any other name is not the pipeline's and stays: an
    earlier pattern on the shapes alone took .my-notes.tmp, notes.json.tmp
    and finish/.todo.tmp of a made-up book in review."""
    import build_draft
    import _repair_inputs as ri

    def dotted(names: tuple[str, ...], random: bool = False) -> re.Pattern[str]:
        tail = r"(?:\.[a-z0-9_]+)?\.tmp" if random else r"\.tmp"
        return re.compile(r"\.(?:" + "|".join(re.escape(name) for name in names) + ")" + tail)

    spots = [
        (book, dotted((finish_book.DOUBTS, finish_book.CLEANED_NAME, _deliver.RECORD_NAME, build_draft.DRAFT_NAME),
                      random=True)),
        (book / finish_book.FINISH_DIR, dotted((finish_book.FINAL, finish_book.WRITTEN_NAME, finish_book.DOUBTS_JSON,
                                                finish_book.REPORT_NAME, finish_book.FIGURES_JSON,
                                                finish_book.FIGURES_SOURCE, finish_book.FOOTNOTES_JSON,
                                                finish_book.BOUNDARIES_NAME))),
        (repaired, dotted((ri.REPORT_NAME, *ri.RUN_FILES))),
        (repaired / "pages", re.compile(r"\.page-\d{4,}\.md\.tmp")),
    ]
    if profile is not None:
        spots.append((profile.parent, re.compile(re.escape(profile.name) + r"\.tmp")))
    return spots


def plan(book: Path, manifest: Path, pdf: Path, copies: Copies, inputs: Mapping[str, Any],
         keep_renders: bool, renders_why: str) -> tuple[list[Item], list[Item]]:
    """The default cleanup: what goes and what stays, nothing touched.
    RENDERS_WHY says in the listing why the renders stay when KEEP_RENDERS."""
    import build_draft

    removed: list[Item] = []
    left: list[Item] = []
    notes: dict[Path, str] = {}

    def folder_item(path: Path, what: str | None = None) -> None:
        """A whole folder of the pipeline's (or the symlink standing for it),
        its files counted as WHAT (plural) when given."""
        if not (path.is_symlink() or path.is_dir()):
            return
        if not _inside(book, path):
            notes[top(path)] = f"{_label(book, path)}/ is not under the book folder: not followed"
            return
        if path.is_symlink():
            removed.append(Item(path, f"{_label(book, path)}/", _bytes(path), "a symlink: unlinked, not followed"))
            return
        count = sum(1 for p in path.rglob("*") if p.is_file()) if what else 0
        counted = f" ({count} {what if count != 1 else what[:-1]})" if what else ""
        removed.append(Item(path, f"{_label(book, path)}/{counted}", _bytes(path), short=f"{_label(book, path)}/"))

    def top(path: Path) -> Path:
        return book / path.relative_to(book).parts[0] if book in path.parents else path

    renders = renders_folder(book, manifest)
    if renders is not None:
        if keep_renders:
            notes[top(renders)] = f"{_label(book, renders)}/ kept ({renders_why})"
        elif renders.is_symlink():
            removed.append(Item(renders, f"{_label(book, renders)}/", _bytes(renders), "a symlink: unlinked, not followed"))
        elif not _inside(book, renders):
            notes[top(renders)] = f"{_label(book, renders)}/ is not under the book folder: not followed"
        else:
            pngs = _files(renders, PAGE_RENDER)
            staging = [renders / name for name in sorted(os.listdir(renders))
                       if STAGING.fullmatch(name) and (renders / name).is_dir() and not (renders / name).is_symlink()]
            if pngs or staging:
                removed.append(Item(renders, f"{_label(book, renders)}/ ({len(pngs)} page renders"
                                    + (f", {len(staging)} staging folder(s))" if staging else ")"),
                                    sum(_bytes(p) for p in pngs + staging), files=pngs + staging,
                                    short=f"{_label(book, renders)}/"))

    proofread = _input_folder(book, inputs, "proofread", "proofread")
    if proofread is not None:
        if proofread.is_symlink() or not _inside(book, proofread):
            notes[top(proofread)] = f"{_label(book, proofread)}/ is a symlink or not under the book folder: its pictures are not followed"
        else:
            pngs = _files(proofread, CROP)
            if pngs:
                removed.append(Item(proofread, f"{_label(book, proofread)}/ crops ({len(pngs)})",
                                    sum(_bytes(p) for p in pngs), files=pngs, keep_folder=True,
                                    short=f"{_label(book, proofread)}/ crops"))

    repaired = book / finish_book.REPAIRED
    folder_item(repaired / REPAIR_CROPS, "crops")
    profile = inputs.get("profile")
    if isinstance(profile, str) and profile:
        profile_path = Path(profile) if _inside(book, Path(profile)) else None
    else:
        profile_path = book / "book-profile.json"
    if profile_path is not None:
        folder_item(profile_path.with_name(profile_path.stem + PROFILE_CROPS_SUFFIX), "crops")
    for path in sorted(book.glob(f"{finish_book.ASSEMBLED}.stale-*")) + [book / finish_book.PARTIAL]:
        folder_item(path)
    for folder, pattern in temporaries(book, repaired, profile_path):
        if folder.is_symlink() or not folder.is_dir() or (folder != book and not _inside(book, folder)):
            continue
        for path in _files(folder, pattern):
            if not path.is_symlink():
                removed.append(Item(path, _label(book, path), _bytes(path), "a temporary file"))

    draft = book / build_draft.DRAFT_NAME
    if draft.is_symlink() or draft.is_file():
        removed.append(Item(draft, draft.name, _bytes(draft)))
    copy = copies.paths["draft"]
    if copy.is_symlink():
        left.append(Item(copy, f"{copy.name} beside the PDF", _bytes(copy), "a symlink: left"))
    elif copy.is_file():
        if copies.versions["draft"] and _deliver._sha256_file(copy) in copies.versions["draft"]:
            removed.append(Item(copy, f"{copy.name} beside the PDF", _bytes(copy), short="the draft copy beside the PDF"))
        else:
            left.append(Item(copy, f"{copy.name} beside the PDF", _bytes(copy),
                             "edited by the person: left" if copies.versions["draft"]
                             else "not written by this book's pipeline: left"))

    # Left alone: every entry of the book folder that does not go whole (a
    # folder emptied of its files goes too), with what remains of it, then
    # the person's files beside the PDF - those not already listed as the
    # book folder's, when the PDF lies inside it.
    going = {item.path for item in removed if not item.files}
    going |= {item.path for item in removed if item.files and not item.keep_folder
              and not set(item.path.iterdir()) - set(item.files)}
    for entry in sorted(book.iterdir()):
        if entry in going:
            continue
        taken = sum(item.size for item in removed if book in item.path.parents and top(item.path) == entry)
        name = entry.name + ("/" if entry.is_dir() and not entry.is_symlink() else "")
        left.append(Item(entry, name, _bytes(entry) - taken, notes.get(entry, "")))
    left.extend(item for item in beside_pdf(pdf, copies) if not (_inside(book, item.path) or _under(book, item.path)))
    return removed, left


def beside_pdf(pdf: Path, copies: Copies) -> list[Item]:
    """The person's files beside the PDF, never touched: the PDF, X.md,
    X-doubts.md, X-圖/ and the copies moved aside as *.edited-*.md."""
    stem = _deliver.copy_name(pdf, "final")[:-3]
    paths = [pdf, copies.paths["final"], copies.paths["doubts"]]
    paths += sorted(p for p in pdf.parent.glob(f"{glob_escape(stem)}*.edited-*.md") if p not in paths)
    paths.append(copies.figures)
    return [Item(path, f"{path.name}{'/' if path.is_dir() else ''} beside the PDF", _bytes(path),
                 "edited by the person" if any(name.split("/")[0] == path.name for name in copies.edited) else "")
            for path in paths if path.exists()]


def glob_escape(text: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", text)


def plan_all(book: Path, pdf: Path, copies: Copies) -> tuple[list[Item], list[Item]]:
    """--all: the whole book folder and the unedited X-draft.md, nothing else."""
    if copies.edited:
        names = " and ".join(copies.edited)
        raise Refused(f"{names} beside the PDF {'is' if len(copies.edited) == 1 else 'are'} not a version the "
                      f"pipeline wrote there (changed or removed by the person), so the book folder holds the only "
                      f"copy of the pipeline's version: --all refused; the default cleanup (without --all) still runs")
    holds = sorted(p.name + ("/" if p.is_dir() and not p.is_symlink() else "") for p in book.iterdir())
    removed = [Item(book, f"{book}/ (the whole book folder: {', '.join(holds)})", _bytes(book),
                    short="the whole book folder")]
    left: list[Item] = []
    copy = copies.paths["draft"]
    if copy.is_file() and not copy.is_symlink():
        if copies.versions["draft"] and _deliver._sha256_file(copy) in copies.versions["draft"]:
            removed.append(Item(copy, f"{copy.name} beside the PDF", _bytes(copy), short="the draft copy beside the PDF"))
        else:
            left.append(Item(copy, f"{copy.name} beside the PDF", _bytes(copy),
                             "edited by the person: left" if copies.versions["draft"]
                             else "not written by this book's pipeline: left"))
    return removed, left + beside_pdf(pdf, copies)


def listing(book: Path, pdf: Path, removed: list[Item], left: list[Item], verb: str) -> list[str]:
    lines = [f"[clean] book folder: {book}", f"[clean] PDF: {pdf} (its SHA-256 is the page manifest's)"]
    lines.append(f"[clean] {verb}:" if removed else f"[clean] {verb}: nothing - the book is clean")
    for item in removed:
        lines.append(f"  {item.label:<{WIDTH}} {mb(item.size):>10}" + (f"  ({item.note})" if item.note else ""))
    lines.append("[clean] left alone:")
    for item in left:
        lines.append(f"  {item.label:<{WIDTH}} {mb(item.size):>10}" + (f"  ({item.note})" if item.note else ""))
    return lines


def summary(removed: list[Item]) -> str:
    """The removed items in a few words for the RESULT line."""
    parts, stale, temporary = [], 0, 0
    for item in removed:
        if item.label.startswith(f"{finish_book.ASSEMBLED}.stale-"):
            stale += 1
        elif item.note == "a temporary file":
            temporary += 1
        else:
            parts.append(item.short)
    if stale:
        parts.append(f"{stale} stale assembl{'y' if stale == 1 else 'ies'}")
    if temporary:
        parts.append(f"{temporary} temporary file{'s' if temporary > 1 else ''}")
    return ", ".join(parts)


def resume_command(pdf: Path, manifest: Path) -> str:
    """survey_pdf.py --renders-only on the folder the manifest is in (its
    OUTPUT_DIR: the renders go to renders/ under it)."""
    return f"python3 scripts/survey_pdf.py {shlex.quote(str(pdf))} {shlex.quote(str(manifest.parent))} {RENDERS_ONLY}"


def write_cleaned(book: Path, pdf: Path, manifest: Path, removed: list[Item], left: list[Item], freed: int,
                  keep_renders: bool, inputs: Mapping[str, Any], stopped: str | None = None) -> None:
    """BOOK/cleaned.json: what went (REMOVED - with STOPPED, the items gone
    before the removal that failed), what stayed, the bytes freed, the
    finish's INPUTS (for a later --all, whose report may be an exit-2 one
    without them), how to resume and, when the renders went, the pdftoppm
    installed (what --renders-only must match)."""
    import survey_pdf

    renders = renders_folder(book, manifest)
    where = _label(book, renders) if renders is not None else "renders"
    how = (f"The text state stays, so the book can be repaired in place once the renders are back: "
           f"{resume_command(pdf, manifest)} renders the pages missing from {where}/ again from the PDF and verifies "
           "each against the page manifest's render_sha256, rewriting nothing else (they match only under a "
           "pdftoppm that renders the same bytes as the survey's); then finish_book.py or repair_book.py can run "
           "again (the crops are cut from the renders as needed).")
    if keep_renders:
        how = "The renders were kept and the text state stays: finish_book.py or repair_book.py can run again as " \
              "they are (the crops are cut from the renders as needed)."
    data = {"tool": TOOL, "when": finish_book._now(), "mode": "default", "book": str(book), "pdf": str(pdf),
            "inputs": {key: value for key, value in inputs.items() if isinstance(value, (str, type(None)))},
            "rendersKept": keep_renders,
            **({} if keep_renders else {"pdftoppm": survey_pdf.pdftoppm_version()}),
            **({"stopped": stopped} if stopped else {}),
            "removed": [item.as_dict(book) for item in removed], "leftAlone": [item.as_dict(book) for item in left],
            "bytesFreed": freed, "howToResume": how}
    finish_book._write_if_changed(book / finish_book.CLEANED_NAME, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def execute(removed: list[Item], book: Path, beside: Path | None, whole: bool, done: list[Item]) -> int:
    """Remove the planned items, each through remove(), appending each to
    DONE as it goes (what a stop part way has to report); the bytes freed."""
    freed = 0
    for item in removed:
        if item.files:
            for path in item.files:
                remove(path, book)
            if not item.keep_folder and item.path.is_dir() and not item.path.is_symlink() and not any(item.path.iterdir()):
                remove(item.path, book)
        else:
            remove(item.path, book, beside=beside, whole=whole)
        done.append(item)
        freed += item.size
    return freed


def clean(book: Path, *, mode: str = "default", dry_run: bool = False, keep_renders: bool = False,
          after_finish: bool = False, inputs: Mapping[str, Any] | None = None) -> Result:
    """The command on BOOK (a folder, resolved).  AFTER_FINISH: called by
    finish_book.py right after its delivery, which holds the finish lock
    and has not written its report yet, so neither is checked, and INPUTS
    are its resolved inputs (else the report's, else the last cleanup's)."""
    lines: list[str] = []
    try:
        report = None if after_finish else finished(book)
        if inputs is None:
            inputs = (report or {}).get("inputs") or _cleaned(book).get("inputs") or {}
        manifest, pdf = source(book, inputs.get("page_manifest"))
        with contextlib.ExitStack() as locks:
            if not after_finish:
                try:
                    locks.enter_context(finish_book.book_lock(book / finish_book.FINISH_DIR))
                except (finish_book.FinishError, OSError) as error:
                    raise Refused(f"another finish_book.py runs on this book ({finish_book.FINISH_DIR}/"
                                  f"{finish_book.LOCK_NAME}): wait for it") from error
            try:
                locks.enter_context(_deliver._locked(book, blocking=False))
            except BlockingIOError as error:
                raise Refused(f"a delivery is being written beside the PDF ({_deliver.LOCK_NAME}): run again when "
                              "it is done") from error
            busy = working(book)
            if busy:
                raise Refused(f"a run is still working on this book ({busy}): wait for it with wait_for_run.py, "
                              "or stop it")
            copies = delivered(book, pdf)
            whole = mode == "all"
            if whole:
                held = held_inside(book, pdf, copies)
                if held:
                    raise Refused(f"the PDF / {copies.paths['final'].name} lie inside the book folder "
                                  f"({', '.join(path.name for path in held)}): --all would remove the book itself")
                mine = personal(book)
                if mine:
                    raise Refused(f"the book folder holds what the pipeline did not write and cannot make again "
                                  f"({', '.join(mine)}): have the person move them out first; --all refused")
                sealed = _input_folder(book, inputs, "proofread", "proofread")
                if sealed is None and not inputs.get("proofread"):
                    found = [p for p in finish_book._subfolders(book) if finish_book._is_sealed_folder(p)]
                    sealed = found[0] if len(found) == 1 else None
                text_moved_on(book, sealed)
            renders_why = ("finish_book.py keeps the renders - a repair in place needs the same bytes, and "
                           "renders cut again match only under the same pdftoppm; --clean-renders removes them"
                           if after_finish else "--keep-renders")
            removed, left = plan_all(book, pdf, copies) if whole else plan(book, manifest, pdf, copies, inputs,
                                                                           keep_renders, renders_why)
            beside = copies.paths["draft"]
            for item in removed:
                for path in item.files or [item.path]:
                    if not _allowed(path, book, beside, whole):
                        raise Refused(f"{path} is not under the book folder")
            lines = listing(book, pdf, removed, left, "would remove" if dry_run else "removing")
            freed = sum(item.size for item in removed)
            if not dry_run and removed:
                done: list[Item] = []
                try:
                    freed = execute(removed, book, beside, whole, done)
                except (OSError, Refused) as error:
                    # remove() itself refuses when a path's resolved parent moved out of the
                    # book folder between the plan and now; in review that refusal fell
                    # through to the handler below, whose line said nothing was removed.
                    freed = sum(item.size for item in done)
                    if not whole:
                        with contextlib.suppress(OSError):
                            write_cleaned(book, pdf, manifest, done, left, freed, keep_renders, inputs,
                                          stopped=str(error))
                    names = f" ({', '.join(item.label for item in done)[:200]})" if done else ""
                    return Result(EXIT_STOPPED, f"stopped at {error} after removing {len(done)} item(s){names} - run "
                                  "the same command again", lines, done, left, freed)
            if whole:
                kept = ", ".join(i.label for i in left if "beside the PDF" in i.label and "draft" not in i.label)
                why = (f"{'dry run - would remove' if dry_run else 'removed'} the whole book folder {book} and "
                       f"{summary(removed[1:]) or 'no draft copy'}: {'would free' if dry_run else 'freed'} "
                       f"{mb(freed)}; left alone: {kept}")
            elif not removed:
                when = _cleaned(book).get("when") or ""
                why = f"freed 0.0 MB (nothing left to remove" + (f"; cleaned on {when}" if when else "") + \
                    f"; {mb(sum(i.size for i in left if book in i.path.parents))} left alone in the book folder)"
            else:
                if not dry_run:
                    write_cleaned(book, pdf, manifest, removed, left, freed, keep_renders, inputs)
                stays = mb(sum(i.size for i in left if book in i.path.parents))
                why = (f"{'dry run - would free' if dry_run else 'freed'} {mb(freed)} ({summary(removed)}); "
                       f"{stays} left alone in the book folder"
                       + ("; nothing removed" if dry_run else f"; {finish_book.CLEANED_NAME} written"))
            return Result(EXIT_DONE, why, lines, removed, left, freed)
    except Refused as error:
        return Result(EXIT_REFUSED, f"{error}: nothing removed", lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=TOOL, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path, help="the finished book's folder (finish_book.py's BOOK)")
    parser.add_argument("--dry-run", action="store_true", help="print what would go and what stays; remove nothing")
    parser.add_argument("--keep-renders", action="store_true",
                        help="keep the renders folder (what finish_book.py's fifth step does); the rest goes")
    parser.add_argument("--all", action="store_true",
                        help="remove the whole book folder and the unedited X-draft.md beside the PDF, nothing else "
                             "(the last step after a delivery, SKILL.md; refused when X.md or "
                             "X-doubts.md beside the PDF was edited or lies inside the folder, when the folder holds "
                             "*.edited-*.md, backup-*/ or .git, or when a sealed page changed since final.md)")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    book = Path(os.path.realpath(os.path.expanduser(str(arguments.book))))
    if not book.is_dir():
        print(f"{RESULT_PREFIX} exit={EXIT_USAGE} REFUSED {TOOL}: {book} is not a folder: nothing removed", flush=True)
        return EXIT_USAGE
    if arguments.all and arguments.keep_renders:
        print(f"{RESULT_PREFIX} exit={EXIT_USAGE} REFUSED {TOOL}: --all removes the renders with the folder; drop "
              "--keep-renders: nothing removed", flush=True)
        return EXIT_USAGE
    result = clean(book, mode="all" if arguments.all else "default", dry_run=arguments.dry_run,
                   keep_renders=arguments.keep_renders)
    for line in result.lines:
        print(line)
    print(result.result, flush=True)
    return result.code


if __name__ == "__main__":
    sys.exit(main())
