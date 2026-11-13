#!/usr/bin/env python3
"""The first of three deliveries: the book as engine B read it, unproofread.

The user no longer waits for the end of phase 3 to see the book.  They get it
three times (references/long-book-workflow.md, 三次交付同本書目錄):

1. draft.md, this script, as soon as engine B is done and before engine A runs
   (SKILL.md, step 3 of its run sheet): engine B's text, page after page, no
   model asked.  Seconds, not hours.
2. proofreading.md, scripts/build_progress.py, rebuilt while phase 3 runs:
   the pages sealed so far as proofread, every other page as in the draft.
3. final.md and doubts.md, scripts/finish_book.py (SKILL.md, step 8), once
   phase 3 has sealed the book: assembled, repaired and finalized.

Why engine B's text and not engine A's: engine A reads the columns of a
vertical page out of order.  Measured on the golden pages: 75.8% CER for
engine A's text against 5.8% for engine B's.

What the draft is:

- the pages in scan order, as the page manifest lists them; a page the
  manifest marks with no marker expected (a blank or duplicate scan) or as
  blank is left out (without a manifest, the pages found in the OCR and
  render folders; none found anywhere: not a book folder, nothing written);
- each page's text as engine B wrote it (ocr-hunyuan/page-NNNN.txt), without
  its grounding boxes (proofread_pages.strip_grounding, as phase 3 reads it);
- running heads and folios kept as engine B read them: the book profile
  does not exist yet, and a stretch that recurs at the page edges can be
  printed text (the opening of a document's title); phase 3 and the repair
  take the furniture off the final text;
- no page markers; a page break is a paragraph break;
- a page with no engine B file is a visible gap line naming its scan page,
  and so is a page engine B read nothing on (a leaf the manifest does not
  call blank: a plate, or a page the engine failed on);
- where engine B looped - wrote one stretch back to back until its tokens ran
  out, a page of thousands of one character - the stretch is written once and
  its repeats are a visible gap line naming the scan page (cut_loops);
- a short header, in Cantonese, saying it is unproofread OCR and when it was
  built.

It never stops the pipeline: a page it cannot read is a gap, a manifest it
cannot read means the pages found on disk, and the file is written atomically
(temp file, then rename), so a reader never sees half of it.  Exit 0 when the
draft is written, 1 only when nothing could be written; the next step of the
pipeline runs either way.

Usage:
    python3 scripts/build_draft.py BOOK [--ocr-dir ocr-hunyuan] [--manifest page-manifest.json]
        [--output draft.md]

Relative paths are taken inside BOOK.  One line is printed: the file and
the page counts.  A second line names the copy beside
the book's PDF, X-draft.md for X.pdf (scripts/_deliver.py; --no-deliver: none),
or why none was written.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proofread_pages as pp  # noqa: E402
import _deliver  # noqa: E402

DRAFT_NAME = "draft.md"
OCR_DIRNAME = "ocr-hunyuan"
MANIFEST_NAME = "page-manifest.json"
RENDERS_DIRNAME = "renders"
PAGE_FILE = re.compile(r"page-(\d{4,})\.(?:txt|png)")

# --- engine B's loops ----------------------------------------------------------
#
# Engine B sometimes does not stop: from some point on a page it writes one
# stretch back to back until its tokens run out.  Measured on engine B's
# drafts of the calibration corpus (1,279 distinct page files, eight books):
# 18 pages hold a stretch of LOOP_MIN_COPIES or more copies of a unit of at
# most LOOP_PERIOD_MAX characters that holds text, LOOP_MIN_CHARS characters
# or more in all - each 3,958 to 12,517 characters, the unit one character (a
# page of 口) up to a table row of 150, every one running to the file's end,
# its last copy cut short - and the longest such repetition under
# LOOP_MIN_CHARS is 120 characters: one table cell printed ten times over,
# which is text.  Copied, a loop is thousands of characters of noise the user
# scrolls through; cut, the unit stays once (the engine read it) and a gap
# line says what was not copied.  A unit of markup alone (empty table cells:
# 90 characters at most in engine B's drafts) holds no text to lose and may
# be a table's printed shape: kept.
LOOP_PERIOD_MAX = 200
LOOP_MIN_COPIES = 5
LOOP_MIN_CHARS = 1000
LOOP = re.compile(r"(.{1,%d}?)\1{%d,}" % (LOOP_PERIOD_MAX, LOOP_MIN_COPIES - 1), re.DOTALL)
MARKUP = re.compile(r"<[^<>]*>")
# How much of the unit the gap line quotes.
LOOP_SHOWN = 12

HEADER_DRAFT = ("> **未校對草稿**：呢份係 OCR 引擎直接讀出嚟嘅文字，未經校對——錯字、漏字、字序錯、符號錯都會有；"
                "書眉同頁碼照留（定稿先刪），頁同頁之間淨係分段。校對完嘅定稿係 `final.md`。")
GAP_LINE = "〔掃描第 {page} 頁：冇 OCR 文字〕"
EMPTY_LINE = "〔掃描第 {page} 頁：OCR 冇讀到字〕"
LOOP_LINE = "〔掃描第 {page} 頁：OCR 喺呢度將「{unit}」再重複咗 {repeats} 次，冇抄{rest}〕"
# Said when nothing follows the loop: the engine never read past it.
LOOP_TO_END = "；之後 OCR 冇再讀落去，呢頁其餘嘅字要睇原書"


@dataclass
class Loop:
    """A stretch engine B wrote back to back (find_loops)."""
    unit: str
    copies: int                      # whole copies; the draft keeps the first
    to_end: bool                     # nothing follows it on the page


@dataclass
class DraftPage:
    scan_page: int
    text: str | None                 # None: no engine B text for the page
    read_nothing: bool = False       # engine B's file holds no text
    loops: list[Loop] = field(default_factory=list)  # its loops, each cut to a gap line


@dataclass
class DraftBook:
    book: Path
    pages: list[DraftPage]
    skipped: list[int]
    note: str | None                 # why the pages are not the manifest's


# --- pages -----------------------------------------------------------------

def inside(book: Path, path: Path | str) -> Path:
    """PATH, relative paths taken inside BOOK."""
    path = Path(os.path.expanduser(str(path)))
    return path if path.is_absolute() else book / path


def found_pages(*directories: Path) -> list[int]:
    pages: set[int] = set()
    for directory in directories:
        with contextlib.suppress(OSError):
            for name in os.listdir(directory):
                match = PAGE_FILE.fullmatch(name)
                if match:
                    pages.add(int(match.group(1)))
    return sorted(pages)


def skipped_by_manifest(record: Mapping) -> bool:
    """A page the manifest says carries no text of its own: no marker expected
    (a scan checked on its image as blank, a duplicate or the like), or
    blank.  A scan marked `relation: exact_duplicate` is not
    left out by that alone: the label can stand on both scans of a pair, and
    the finished book keeps every scan whose marker is expected - the draft
    would lose a page the book has."""
    return record.get("marker_expected") is False or record.get("content") == "blank"


def book_pages(book: Path, manifest: Path, ocr: Path) -> tuple[list[int], list[int], str | None]:
    """(the pages in scan order, the pages the manifest leaves out, a note when
    the manifest could not be read and the pages are those found on disk)."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        records = data["pages"]
        if not isinstance(records, list):
            raise TypeError("its pages are not a list")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        why = "missing" if isinstance(error, FileNotFoundError) else f"unreadable: {error}"
        return (found_pages(ocr, book / RENDERS_DIRNAME), [],
                f"no page manifest ({manifest.name} {why}); the pages found in {ocr.name}/ and renders/")
    pages: dict[int, bool] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        number = record.get("scan_page")
        if isinstance(number, int) and not isinstance(number, bool) and number > 0 and number not in pages:
            pages[number] = skipped_by_manifest(record)
    if not pages:
        return (found_pages(ocr, book / RENDERS_DIRNAME), [],
                f"no page manifest ({manifest.name} lists no scan page); the pages found in {ocr.name}/ and renders/")
    return ([page for page in sorted(pages) if not pages[page]],
            [page for page in sorted(pages) if pages[page]], None)


def engine_b_lines(ocr: Path, page: int) -> list[str] | None:
    """Engine B's text of PAGE as lines, without its grounding boxes, blank
    lines at the ends dropped; None when there is no file to read."""
    try:
        raw = (ocr / f"page-{page:04d}.txt").read_bytes()
    except OSError:
        return None
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in pp.strip_grounding(text).split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def find_loops(text: str) -> list[tuple[int, int, str]]:
    """(start, end, unit) of each loop in TEXT: a unit of at most
    LOOP_PERIOD_MAX characters, holding text outside markup, back to back
    LOOP_MIN_COPIES times or more and LOOP_MIN_CHARS characters or more in
    all.  A last copy cut short is part of the loop only where nothing but
    blank follows it (the engine's tokens ran out in the middle of it); text
    after it is the page's."""
    if len(text) < LOOP_MIN_CHARS:
        return []
    found = []
    at = 0
    while True:
        match = LOOP.search(text, at)
        if match is None:
            return found
        start, end, unit = match.start(), match.end(), match.group(1)
        if not holds_text(unit):
            at = end                  # markup alone: no stretch of it holds text
            continue
        if end - start < LOOP_MIN_CHARS:
            # The shortest unit repeating here may not be the loop's: a loop
            # can start with a run of one character.  Look again one on.
            at = start + 1
            continue
        tail = 0
        while tail < len(unit) - 1 and end + tail < len(text) and text[end + tail] == unit[tail]:
            tail += 1
        if tail and not text[end + tail:].strip():
            end += tail
        found.append((start, end, unit))
        at = end


def from_tag(unit: str) -> str:
    """A loop's UNIT read from its first < on, where a tag starts.  A unit
    is any rotation of the stretch that repeats - it starts where the loop
    does, taking in a tag's closing > of the text before it - and read from
    a tag's start its tags are whole."""
    at = unit.find("<")
    return unit[at:] + unit[:at] if at > 0 else unit


def holds_text(unit: str) -> bool:
    """Whether a loop's UNIT holds anything but markup."""
    return bool(MARKUP.sub("", from_tag(unit)).strip())


def loop_line(page: int, loop: Loop) -> str:
    shown_unit = " ".join(MARKUP.sub(" ", from_tag(loop.unit)).split())
    if len(shown_unit) > LOOP_SHOWN:
        shown_unit = shown_unit[:LOOP_SHOWN] + "…"
    return LOOP_LINE.format(page=page, unit=shown_unit, repeats=loop.copies - 1,
                            rest=LOOP_TO_END if loop.to_end else "")


def cut_loops(page: int, lines: Sequence[str]) -> tuple[list[str], list[Loop]]:
    """PAGE's lines with each loop (find_loops) cut: its unit kept once where
    the loop starts, then a gap line (LOOP_LINE) as a paragraph of its own,
    then the text after the loop, if any.  Lines with no loop come back as
    they are."""
    text = "\n".join(lines)
    found = find_loops(text)
    if not found:
        return list(lines), []
    pieces: list[str] = []
    loops: list[Loop] = []
    at = 0
    for start, end, unit in found:
        loop = Loop(unit, (end - start) // len(unit), not text[end:].strip())
        pieces += [text[at:start + len(unit)].strip("\n").rstrip(), loop_line(page, loop)]
        loops.append(loop)
        at = end
    pieces.append(text[at:].strip("\n").rstrip())
    return "\n\n".join(piece for piece in pieces if piece.strip()).split("\n"), loops


# --- the draft -----------------------------------------------------------------

def draft_book(book: Path, ocr: Path | str = OCR_DIRNAME, manifest: Path | str = MANIFEST_NAME) -> DraftBook:
    """Every page of BOOK as the draft writes it."""
    book = Path(book)
    ocr, manifest = inside(book, ocr), inside(book, manifest)
    pages, skipped, note = book_pages(book, manifest, ocr)
    if not pages and not skipped:
        # Not a book folder (the proofread folder passed for it, a typo): a
        # file of no page there would be a delivery of nothing, in the
        # wrong place.
        raise FileNotFoundError(f"no page found in {book}: no page manifest listing one, no page in "
                                f"{ocr.name}/ or {RENDERS_DIRNAME}/ - is it the book folder?")
    out = []
    for page in pages:
        lines = engine_b_lines(ocr, page)
        if lines is None:
            out.append(DraftPage(page, None))
            continue
        lines, loops = cut_loops(page, lines)
        out.append(DraftPage(page, "\n".join(lines), read_nothing=not lines, loops=loops))
    return DraftBook(book, out, skipped, note)


def text_block(text: str) -> str | None:
    """A page's text as it goes into a delivery: None when it has none."""
    text = text.strip("\n")
    return text if text.strip() else None


def draft_block(page: DraftPage) -> str | None:
    """A page as the draft writes it: its text, or the line saying why there
    is none (no engine B file, or nothing read)."""
    if page.text is None:
        return GAP_LINE.format(page=page.scan_page)
    if page.read_nothing:
        return EMPTY_LINE.format(page=page.scan_page)
    return text_block(page.text)


def built_at(now: datetime | None = None) -> str:
    """The local time a delivery is built, to the minute, with its offset."""
    now = (now or datetime.now()).astimezone()
    return f"{now:%Y-%m-%d %H:%M} UTC{now.isoformat(timespec='minutes')[-6:]}"


def join_pages(blocks: Iterable[str | None]) -> str:
    return "\n\n".join(block for block in blocks if block is not None)


def render_draft(draft: DraftBook, when: str) -> str:
    missing = sum(1 for page in draft.pages if page.text is None)
    counts = f"共 {len(draft.pages)} 頁"
    if draft.skipped:
        counts += f"（略過 {len(draft.skipped)} 頁空白或者重複嘅掃描）"
    if missing:
        counts += f"，{missing} 頁冇 OCR 文字"
    looped = sum(1 for page in draft.pages if page.loops)
    if looped:
        counts += f"，{looped} 頁 OCR 不停重複同一段字，重複嘅部分冇抄"
    header = f"{HEADER_DRAFT}\n>\n> 建立時間：{when}；{counts}。\n"
    body = join_pages(draft_block(page) for page in draft.pages)
    return header + ("\n" + body + "\n" if body else "")


def atomic_write(path: Path, text: str) -> None:
    """Write TEXT to PATH through a temporary file beside it and a rename, so a
    reader sees the old file or the new one, never half of one."""
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path, help="the book folder (renders/, page-manifest.json, ocr-hunyuan/)")
    parser.add_argument("--ocr-dir", default=OCR_DIRNAME, help=f"engine B's pages (default {OCR_DIRNAME})")
    parser.add_argument("--manifest", default=MANIFEST_NAME, help=f"the page manifest (default {MANIFEST_NAME})")
    parser.add_argument("--output", default=DRAFT_NAME, help=f"the draft (default {DRAFT_NAME})")
    parser.add_argument("--no-deliver", action="store_true",
                        help="do not copy the draft beside the book's PDF as X-draft.md (scripts/_deliver.py: the "
                             "PDF the page manifest names; a file there the pipeline did not write is left alone)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    began = time.monotonic()
    book = Path(os.path.abspath(os.path.expanduser(str(arguments.book))))
    output = inside(book, arguments.output)
    try:
        draft = draft_book(book, arguments.ocr_dir, arguments.manifest)
        text = render_draft(draft, built_at())
        atomic_write(output, text)
    except Exception as error:  # noqa: BLE001 - the draft never stops the pipeline
        print(f"[draft] NOT WRITTEN {output}: {type(error).__name__}: {error} - go on with the next step",
              flush=True)
        return 1
    missing = [page.scan_page for page in draft.pages if page.text is None]
    nothing = [page.scan_page for page in draft.pages if page.read_nothing]
    looped = [page.scan_page for page in draft.pages if page.loops]
    print(f"[draft] wrote {output}: {len(draft.pages)} pages, {len(draft.skipped)} left out by the manifest, "
          f"{len(missing)} with no OCR text{f' {missing[:10]}' if missing else ''}, "
          f"{len(nothing)} read as nothing{f' {nothing[:10]}' if nothing else ''}, "
          f"{len(looped)} with an OCR loop cut to a gap line{f' {looped[:10]}' if looped else ''}"
          + (f"; {draft.note}" if draft.note else "")
          + f" ({time.monotonic() - began:.1f}s)", flush=True)
    if not arguments.no_deliver:
        # The person's copy beside the PDF; it never stops the pipeline either.
        try:
            copy = _deliver.deliver(book, "draft", text.encode("utf-8"), _deliver.find_manifest(
                book, inside(book, arguments.manifest)))
            print(f"[draft] beside the PDF: {copy.path}" + (f" (the edited copy kept as {copy.kept_as})"
                                                            if copy.kept_as else "")
                  if copy.written else f"[draft] not copied beside the PDF: {copy.reason}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"[draft] not copied beside the PDF: {type(error).__name__}: {error}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
