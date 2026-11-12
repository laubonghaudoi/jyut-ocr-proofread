#!/usr/bin/env python3
"""The second of three deliveries: the book while phase 3 proofreads it.

proofreading.md holds every page of the book in scan order (the page
manifest's pages, as scripts/build_draft.py lists them):

- a page whose seal is complete and intact - page-NNNN.json and
  page-NNNN.md in the proofread folder, the JSON canonical, of this schema,
  status complete, the Markdown's hash and the payload's hash what the seal
  says (stitch_tables.sealed_page, the part of proofread_pages.py's resume
  check that does not depend on the run's arguments) - as proofread;
- every other page as the draft writes it (engine B's text as read, running
  heads and folios too): a page not reached yet, a page being sealed while this
  runs (its Markdown written, its seal not yet, or half of either), a page
  whose seal was edited;
- no page markers (a proofread page carrying one loses it); a page break is a
  paragraph break - joining a sentence across pages is the repair step's work,
  for the final delivery;
- a header line: pages proofread out of how many, and when it was built.

A proofread page whose text is empty stays empty: the table stitch moved its
text to the page where the table starts (its seal's tableStitch).

Cheap enough to rebuild every few minutes (a 488-page book: about a second),
and written through a temporary file and a rename, so a reader never sees half
of it.  scripts/wait_for_run.py --progress BOOK rebuilds it while it waits
on phase 3.  Nothing in the proofread folder is written.

Usage:
    python3 scripts/build_progress.py BOOK [--output-dir proofread] [--ocr-dir ocr-hunyuan]
        [--manifest page-manifest.json] [--output proofreading.md]

Relative paths are taken inside BOOK.  One line is printed; exit 0 when
the file is written, 1 when it could not be.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import time
from typing import Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_draft  # noqa: E402
import stitch_tables  # noqa: E402

PROGRESS_NAME = "proofreading.md"
OUTPUT_DIRNAME = "proofread"
# A page marker line (_ocr_markdown.PAGE_MARKER_CANDIDATE_RE's shape).
PAGE_MARKER_LINE = re.compile(r"^[ \t]*<!--\s*page_[^>]*-->[ \t]*\n?", re.IGNORECASE | re.MULTILINE)
HEADER_PROGRESS = ("> **校對進行中**：已校對 {done}／{total} 頁（{when} 更新）。{rest}"
                   "頁同頁之間淨係分段，跨頁駁句同最後修補喺定稿 `final.md` 先做。")
# Said while pages are left that are not proofread.
HEADER_REST = "其餘嘅頁暫時用未校對嘅 OCR 草稿；"


@dataclass
class Progress:
    path: Path
    proofread: int
    total: int
    when: str


def sealed_markdown(directory: Path, page: int) -> str | None:
    """PAGE's proofread Markdown when its seal is complete and intact, else
    None - also for a seal that is JSON but not of a seal's shape (a list, a
    provenance that is not an object): one odd file is that page's draft,
    not a progress file that cannot be built."""
    try:
        record, text, _ = stitch_tables.sealed_page(directory / f"page-{page:04d}.json")
    except (AttributeError, TypeError, KeyError, ValueError):
        return None
    return text if record is not None else None


def without_markers(text: str) -> str:
    return PAGE_MARKER_LINE.sub("", text)


def render_progress(book: Path, proofread: Path, when: str, ocr: Path | str = build_draft.OCR_DIRNAME,
                    manifest: Path | str = build_draft.MANIFEST_NAME) -> tuple[str, int, int]:
    """The progress file's text, the pages proofread and the pages in all."""
    draft = build_draft.draft_book(book, ocr, manifest)
    blocks = []
    done = 0
    for page in draft.pages:
        text = sealed_markdown(proofread, page.scan_page)
        if text is None:
            blocks.append(build_draft.draft_block(page))
            continue
        done += 1
        blocks.append(build_draft.text_block(without_markers(text)))
    header = HEADER_PROGRESS.format(done=done, total=len(draft.pages), when=when,
                                    rest=HEADER_REST if done < len(draft.pages) else "") + "\n"
    body = build_draft.join_pages(blocks)
    return header + ("\n" + body + "\n" if body else ""), done, len(draft.pages)


def build(book: Path, proofread: Path | str = OUTPUT_DIRNAME, output: Path | str = PROGRESS_NAME,
          ocr: Path | str = build_draft.OCR_DIRNAME, manifest: Path | str = build_draft.MANIFEST_NAME) -> Progress:
    """Write the progress file of BOOK from the pages sealed in PROOFREAD;
    relative paths are taken inside BOOK."""
    book = Path(os.path.abspath(os.path.expanduser(str(book))))
    path = build_draft.inside(book, output)
    when = build_draft.built_at()
    text, done, total = render_progress(book, build_draft.inside(book, proofread), when, ocr, manifest)
    build_draft.atomic_write(path, text)
    return Progress(path, done, total, when)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path, help="the book folder (page-manifest.json, ocr-hunyuan/, proofread/)")
    parser.add_argument("--output-dir", default=OUTPUT_DIRNAME,
                        help=f"phase 3's output folder, the sealed pages (default {OUTPUT_DIRNAME})")
    parser.add_argument("--ocr-dir", default=build_draft.OCR_DIRNAME,
                        help=f"engine B's pages, for the pages not proofread yet (default {build_draft.OCR_DIRNAME})")
    parser.add_argument("--manifest", default=build_draft.MANIFEST_NAME,
                        help=f"the page manifest (default {build_draft.MANIFEST_NAME})")
    parser.add_argument("--output", default=PROGRESS_NAME, help=f"the progress file (default {PROGRESS_NAME})")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    began = time.monotonic()
    try:
        progress = build(arguments.book, arguments.output_dir, arguments.output, arguments.ocr_dir,
                         arguments.manifest)
    except Exception as error:  # noqa: BLE001 - a progress file never stops the run
        print(f"[progress] NOT WRITTEN: {type(error).__name__}: {error}", flush=True)
        return 1
    print(f"[progress] wrote {progress.path}: {progress.proofread}/{progress.total} pages proofread "
          f"({time.monotonic() - began:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
