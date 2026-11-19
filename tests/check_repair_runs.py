#!/usr/bin/env python3
"""Offline check of the book repair tool's text-run stage
(scripts/_repair_runs.py, stage `runs`) through scripts/repair_book.py.

No model, no GPU, no corpus: a made-up book (tests/repair_fixture.py), each
page changed only where a case is set up.  Every rule has a case it must
repair and a look-alike it must leave:

- N1 writes a figure both engines read between two characters the page
  holds without it (T1); a bracketed Latin name both engines read where
  the page holds the empty bracket pair; where the page has one mark of
  the engines' and lacks the others (T2), the figure with the marks beside
  it - but not a bracket that would find no partner on its line; a table
  cell's figure where the page's row has the empty cell; a figure in a
  table row that phase 3 moved to the page before (the seal's tableStitch),
  written there.
- N5 writes the marks both engines read in a table's cells where the page
  holds none, the cells' characters in an engine A block labelled a table
  (one the punctuator never read): a comma between two characters, a full
  stop before a cell boundary, the two title marks of a pair written in
  two gaps of one row, a dash in an empty cell, the marks either side of a
  cell's line break, each line's marks on its side of a space the page
  holds where engine A breaks the line; in the rows phase 3 moved to the
  page before, also where the rule under the continuation's first row
  stands between the two rows an edit touches.  Left to N8: the same
  marks in running text.  Left: on a line that merely holds a pipe, and in a page table
  whose characters lie in a punctuated text block; a gap where the engines
  differ (no doubt); a bracket whose partner is not written, two brackets
  with a cell boundary between them, and the two about a gap between them
  that is not written where engine A reads brackets there (two titles
  would read as one); a star, a character a renderer reads as markup, one
  that prints nothing; a gap beside a note marker; a gap where the page
  holds a mark; cell boundaries that are not the
  engines' one to one; a page punctuated whole, and the one table of a
  page rebuilt from its crop (the seal's record).  The marks the rule found
  and could not write - on the page, or in moved rows whose host row has
  changed - are one `run-no-place` doubt a page, with their count; an edit
  whose cell marks were all withheld is N1's.
- N2 writes a whole line of Western text both engines read between two
  paragraphs, one engine with emphasis markers in it (bibliography_checks:
  an entry that goes on with a Chinese note on the next character's line
  woven there, apart from the heading's colon before the break; an entry
  engine A cuts in two and engine B reads as one line written as one
  paragraph, beside an entry the engines read a mark apart, left out as a
  doubt; nothing where they read a figure on the next character's line
  apart, or where a figure opens engine A's line there; an entry engine A
  cuts at a hyphen joined with no space, beside one engine B cuts in two;
  an entry engine A cuts in two that the engines read an apostrophe apart,
  written as engine A reads it, and one they read a figure apart, left out,
  the entries after either paired and written).
- N8 (agreed_checks, made-up pages) writes the marks both engines read alike
  in a gap of running text where the page holds none of them, or some in
  their order (all of them then; the page's ASCII mark read full width
  too), an engine's ASCII mark read full width and its emphasis markers,
  tags and line-opening heading markers set aside, the page's space
  replaced, its ideographic space kept where the engines read one too; the
  comma of scan 9's running text in the made-up book.  Left: a gap at the
  page's leading or trailing edge, engines whose marks differ, engine B not
  aligned, a line break of engine A's or of the page's, print on the page
  besides marks, a mark that is not CJK print, a page that holds every
  mark or one the engines do not (an ideographic space too), engines that
  read an ideographic space alone; and the rule is off on a book whose
  profile saw doubled circles or emphasis marks.  The table look-alikes
  of N5 below are N8's look-alikes too (a table's cells are N5's).
- Forms after runs (scan 16 of the made-up book): a figure's comma the page
  kept without the figure is written in the figure, ASCII (N1), and the
  forms stage's K9, after the runs, leaves it and writes the page's ASCII
  comma beside characters full width.
- N6 rewrites a stretch of a footnote that is all runs as engine A's text
  where every mark of the page there is one of engine A's: `）（` as the
  engines' `（1522-66）` (their marks in another order), a Western
  citation whose brackets the page holds out of order, a journal's volume,
  issue and year (the page's title mark in its own shape, a colon a run
  carries counted).  Left, as doubts: a gap across a table's cell
  boundary, and one where a bracket of engine A's text would find no
  partner on its line.
- N6 where the page holds a mark neither engine reads there
  (dropped_checks, and scan 11 of the made-up book): an imprint page's
  size and price (a comma, a middle dot for the engines' colon: the doubt
  says engine A's mark took its place), a closing bracket after a page
  reference, a doubled full stop, a dash for engine A's hyphen (the doubt
  says none did) - the runs written, the mark dropped and named in a
  `run-mark-dropped` doubt.  A connector a run carries stays the run's,
  with no doubt.  Across a line break of engine A's that the page or
  engine B holds too the runs go on its sides, the page's own break
  between them (the imprint's copies; a single newline stays single) or a
  blank line where the page holds none (天地会 5's insert count and first
  year, a colon dropped); at the page's trailing edge a break of engine
  A's with nothing after it goes, whatever engine B holds there.  Left,
  as doubts with no mark dropped: a line break of engine A's alone (the
  imprint's second year, printed on one line with the first; body text
  engine B reads as one line), a bracket whose partner is on the page
  (opening its line, past a title mark's end, in the next paragraph), a
  page's dash that engine B reads where engine A reads a hyphen, two line
  breaks of engine A in the gap, a line break of the page's alone or the
  page's breaks apart, an edit the pairing guard undoes (a bracket after
  engine A's break with no partner on its line) or the page-edge check
  withholds.
- N2 beside note markers (footnote_checks): a Western footnote that opens
  with its number, read by both engines, goes in as a paragraph without
  the number - at the page's trailing edge after the page range woven
  before it, mid-page with the page's brackets of it dropped and the next
  note's number left as the page has it; where the page holds the page
  range (or the runs of the lines either side) already, beside it as the
  page has it.  Left: the same note read
  differently by the engines (the page range before it still woven), a gap
  engine B is not aligned at.
- Fail loud: runs both engines read that no rule writes are a
  `run-no-place` doubt naming them - in a gap of a note marker whose part
  on the line before is not given, where the page holds another run and
  the engines differ - and the runs of a page that no gap aligns are named
  by its net (unaligned).
- Look-alikes of the footnote rules (footnote_guard_checks), each left as
  the page has it with a doubt naming the runs: a citation engine B cuts
  with emphasis markers; a note marker the page and engine A put on either
  side of a mark; a gap of a note marker at the page's leading edge (a
  running head there is furniture: no doubt); a bracket the pairing guard
  withholds from an N6 edit kept on its trial only by another one, from two
  N6 gaps on one line, or between two runs of an N1 edit; N6 inside a table
  cell; a Western note whose own number the page holds after its break; a
  line beside note markers that opens with a figure of its own (one that
  opens with a year is written) or reads as a list item.  N6 writes the
  page's own form of a comma (with the space after it) and of a connector
  a run carries but a comma (N1 too: a figure's 6,500, 3,000 as engine A
  spells it where the page holds ，), and engine A's curly quotation
  marks of a pair inside the gap with their spaces; with engines a hyphen
  apart, as engine A reads it.  N1 writes the figures the engines read a
  dash and a hyphen apart between them (天地会 5's imprint page, 1—5,000)
  as engine A reads them, with the doubt; a dash beside a letter is not a
  hyphen's other form.
- N7 writes a Western citation the engines read a letter of one word apart
  as engine A reads it, on N6's marks and on N1's, and one they read an
  apostrophe inside a word or a word's case apart, with the doubt naming
  both readings of the word; it leaves, as the doubt it was, a word two
  letters apart, a figure apart, two words apart, a word of three letters,
  a quotation mark pair one engine alone reads, a word that holds a figure
  a letter apart, readings with no Latin letter, a word a letter apart in a
  table's cell, and an edit whose bracket the pairing guard withholds
  between two runs.  The page-edge check names the runs and
  lines of an edit it withholds; at a join across a page's foot notes it
  withholds an edit on the line above them that the join would glue, a
  table row put there (the contract refuses it), an edit that changes that
  line's kind, and a line put back among the notes (盜區政區's scan 157, a
  note's Latin text with no number: the join ran on it), which stands before
  a paragraph break.  The page net reads a page with no CJK
  character, names a figure equal to the folio inside the text, and pools
  the pages of a stitched table through text_runs.
- R1 puts back a refused first answer's mark where it equals engine A's and
  the page's kept retry differs; a first answer that differs from engine A
  changes nothing.
- An imprint page (imprint_checks, 天地会 2's scan 3 cut short): N1 writes
  the book's size, which engine A writes as LaTeX (`$ 850 \\times 1168 $`)
  and engine B as 850×1168, as one run, and the years that open engine A's
  lines (four digits are not a line's own number), with the figure before
  one on the line above; where the page keeps the size's × alone (天地会
  5's scan 3, also after a comma, N6), the × is the run's own.  A × alone
  elsewhere is print, as □ is: written where both engines read it (in a
  table's cell on a page punctuated whole too), a doubt where engine A
  alone reads it.  Left: a formula with `=` (unknown markup), figures
  joined by another command, a figure alone in LaTeX (a note marker), a
  figure of three digits opening a line.
- The □ a book prints for a character it lacks (missing_glyph_checks, lines
  of 天地会 2's scans 79 and 260): N1 writes it where both engines read it,
  beside the page's marks, and where engine B writes it as LaTeX of \\Box
  alone (天地会 5's scans 358 and 361, 天地会 3's scan 116); a run of them
  the engines count apart and one engine A alone reads are left, as
  doubts.
- left alone, as doubts: a figure the engines read differently
  (`run-engines-differ`); one engine A alone read (`run-one-engine`); one
  beside a note marker (`note-markers-unverified`, no run written); a
  figure that opens a line in engine A (`run-no-place`); a figure the page
  already holds (nothing).
- Note calls (note_call_checks, the shapes of 天地会 4's scans 37, 70 and
  79, 天地会 2's 43 and 69 - engine B's bracket in another shape - and
  天地会 3's 394 - engine A's mark written too; a call at a line's end, one
  after a call engine B alone reads and one beside a run of figures are
  made up): a circled figure both engines read in a line (engine A's
  `$ ^{①} $`, engine B's ①) that the page lacks is written as N1 writes a
  run - between two characters, before or after the page's mark, with
  engine A's mark where the page holds none, at a line's end, close beside
  a run - where the page's calls run 1, 2 ... up to it.  Left, as the
  page's `note-markers-unverified` doubt: a call out of the page's
  numbering (a book whose 81 both engines read as ⑧) or after one, two
  circled figures side by side, a figure after the call, a superscript
  figure, a note's own number opening a line or in the call's gap, a call
  the engines put on either side of a mark, one beside a page mark the
  engines do not read (no N6), one where the page holds a marker, one on
  a page holding a call a gap off, one where the page breaks a line
  engine A reads on, one at the page's leading edge, and one the engines
  read as different figures (both values named).
- `--no-runs` switches the stage off; every run replays its log to its
  output; the made-up book with nothing to repair gets no edit from the
  stage (tests/check_repair_readonly.py).

Usage:
    python3 tests/check_repair_runs.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
import _repair_edits as re_  # noqa: E402
import _repair_rules as rr  # noqa: E402
import _repair_runs as rn  # noqa: E402
import _ocr_markdown as om  # noqa: E402
import proofread_pages as pp  # noqa: E402
import repair_book  # noqa: E402


FAILURES: list[str] = []
OTHER_STAGES_OFF = ("--no-furniture", "--no-marks", "--no-contents", "--no-title-page",
                    "--no-levels", "--no-paragraphs", "--no-joins", "--no-words", "--no-brackets")


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def run(*argv: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return repair_book.main(["--workers", "1", *argv])


def paragraphs(page: dict[str, Any]) -> list[str]:
    return [item[1] for item in page["items"] if item[0] == "para"]


def b_form(text: str) -> str:
    return text.replace("，", "丶").replace("。", "口")


def insert(spec: dict[int, dict[str, Any]], scan: int, para: int, a_text: str, b_text: str | None,
           sealed_text: str | None, at: int = 1) -> tuple[str, str]:
    """Put A_TEXT (engine A), B_TEXT (engine B) and SEALED_TEXT (the page)
    between characters AT+1 and AT+2 of paragraph PARA of SCAN (a stretch
    with no mark in it); returns the two characters either side."""
    text = paragraphs(spec[scan])[para]
    old = text[at:at + 4]
    assert "，" not in old and text.count(old) == 1, (scan, para, old)
    left, right = old[:2], old[2:]
    page = spec[scan]
    page.setdefault("aEdits", []).append((old, left + a_text + right))
    if b_text is not None:
        page.setdefault("bEdits", []).append((b_form(old), b_form(left) + b_text + b_form(right)))
    if sealed_text is not None:
        page.setdefault("sealedEdits", []).append((old, left + sealed_text + right))
    return left, right


def make_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    # N1 T1: a year both engines read; a bracketed Latin name where the page
    # holds the empty pair; T2: the page holds the opening bracket only.
    insert(spec, 6, 0, "1989", "1989", None)
    insert(spec, 6, 1, "（G. W. Skinner）", "(G. W. Skinner)", "（）")
    insert(spec, 6, 2, "（1522-66）", "(1522-66)", "（")
    # T2: the comma before a year, written with it beside the bracket the
    # page has; an opening bracket with no partner on its line, withheld.
    insert(spec, 6, 0, "，1996）", "，1996）", "）", at=9)
    insert(spec, 6, 1, "7〈", "7〈", None, at=9)
    # Left alone: the engines differ; engine A alone; a note marker beside it.
    insert(spec, 8, 0, "1989", "1980", None)
    insert(spec, 8, 1, "1989", None, None)
    insert(spec, 8, 2, "⑫1989", "⑫1989", None)
    # The page already holds the figure; a figure that opens a line of engine
    # A (the second column of the paragraph: its 39th character).
    insert(spec, 10, 0, "1989", "1989", "1989")
    insert(spec, 10, 2, "1989", "1989", "1980")
    text = paragraphs(spec[10])[1]
    old = text[43:47]
    assert "，" not in old and text.count(old) == 1 and len(pp.CJK(text[:43])) == 38
    spec[10].setdefault("aEdits", []).append((old, "18" + old))
    spec[10].setdefault("bEdits", []).append((b_form(old), "18" + b_form(old)))
    # N6: the bracket pair reversed and emptied on the page; a closing
    # bracket after a page reference that neither engine reads, dropped.
    insert(spec, 12, 0, "（1522-66）", "(1522-66)", "）（")
    insert(spec, 11, 0, "336。", "336。", "。）")
    # A table: the page's row has an empty cell and a cell short of its
    # figure; engine A the HTML table, engine B the row as a line.
    spec[12]["extraSealed"] = ["| 贛州 | | 弘治 |"]
    spec[12]["extraBlocks"] = [{"text": "<table><tr><td>贛州</td><td>1496</td><td>弘治9</td></tr></table>",
                                "boundingBox": [0.1, 0.9, 0.5, 0.03], "blockLabel": "table"}]
    last = paragraphs(spec[12])[2]
    tail = b_form(last[-4:])
    spec[12].setdefault("bEdits", []).append((tail, tail + "\n贛州 1496 弘治9"))
    # N2: a line of Western text between scan 8's last two paragraphs (no
    # full stop there: engine B writes one as a character), engine B with
    # emphasis markers around the title.
    end = paragraphs(spec[8])[1][-2:]
    spec[8].setdefault("sealedEdits", []).append((end, end[:-1]))
    spec[8].setdefault("aEdits", []).append((end, end[:-1] + "\nHobsbawm, E. J. Bandits. London: Weidenfeld, 1969."))
    spec[8].setdefault("bEdits", []).append((b_form(end), end[:-1] + "\nHobsbawm, E. J. *Bandits*. London: Weidenfeld, 1969."))
    # A table row of scan 18 that phase 3 moved into scan 17's table: the
    # seal of scan 18 holds its text before the move and the row moved;
    # scan 18's engines read the row with its figure.
    spec[17]["extraSealed"] = ["| 贛州 | 弘治 |\n| --- | --- |\n| 南安 | 嘉靖 |"]
    before = fx.sealed_text(spec[18]) + "\n| 南安 | 嘉靖 |\n"
    spec[18]["tableStitch"] = {"before": before, "continuation": {"head": {"page": 17, "table": 0},
                                                                   "moved": "| 南安 | 嘉靖 |"}}
    spec[18]["extraBlocks"] = [{"text": "<table><tr><td>南安</td><td>嘉靖3</td></tr></table>",
                                "boundingBox": [0.1, 0.9, 0.5, 0.03], "blockLabel": "table"}]
    tail = b_form(paragraphs(spec[18])[-1][-4:])
    spec[18].setdefault("bEdits", []).append((tail, tail + "\n南安 嘉靖3"))
    # N5: a table of scan 9 whose cells the page holds without the marks both
    # engines read (engine A the HTML table, engine B its rows as lines); in
    # its last row the engines differ on a mark, and read the title's opening
    # bracket alike but not its closing one.  The same comma in running text.
    spec[9]["extraSealed"] = ["| 府 | 事由 | 出處 |\n| --- | --- | --- |\n| 贛州 | 城砌以石高尺 | 康熙上猶縣志 |\n"
                              "| 汀州 | 平亂建鎮 | 明史 |"]
    spec[9]["extraBlocks"] = [{"text": "<table><tr><td>府</td><td>事由</td><td>出處</td></tr><tr><td>贛州</td>"
                                       "<td>城砌以石，高18尺。</td><td>康熙《上猶縣志》3/2a-b。</td></tr><tr><td>汀州</td>"
                                       "<td>平亂，建鎮</td><td>《明史》</td></tr></table>",
                               "boundingBox": [0.1, 0.9, 0.5, 0.05], "blockLabel": "table"}]
    tail = b_form(paragraphs(spec[9])[-1][-4:])
    spec[9].setdefault("bEdits", []).append(
        (tail, tail + "\n府 事由 出處\n贛州 城砌以石，高18尺。 康熙《上猶縣志》3/2a-b。\n汀州 平亂；建鎮 《明史"))
    insert(spec, 9, 0, "，", "，", None)
    # A figure's comma the page kept without the figure, and an ASCII comma
    # the punctuator wrote where both engines read a full-width one.
    insert(spec, 16, 0, "7,920", "7,920", ",")
    insert(spec, 16, 0, "，", "，", ",", at=9)
    # N5 on rows moved: the last two rows of scan 14's table are scan 15's,
    # whose text before the move holds them as a table of its own, the rule
    # under its first row.
    spec[14]["extraSealed"] = ["| 府 | 事由 |\n| --- | --- |\n| 贛州 | 城周三里 |\n| 南安 | 城圮官修議出公帑 |\n| 汀州 | 動支河稅 |"]
    part = "| 南安 | 城圮官修議出公帑 |\n| --- | --- |\n| 汀州 | 動支河稅 |"
    spec[15]["tableStitch"] = {"before": fx.sealed_text(spec[15]) + "\n" + part + "\n",
                               "continuation": {"head": {"page": 14, "table": 0}, "moved": part}}
    spec[15]["extraBlocks"] = [{"text": "<table><tr><td>南安</td><td>城圮，官修。議出公帑。</td></tr><tr><td>《汀州》</td>"
                                        "<td>動支河稅。</td></tr></table>",
                                "boundingBox": [0.1, 0.9, 0.5, 0.05], "blockLabel": "table"}]
    tail = b_form(paragraphs(spec[15])[-1][-4:])
    spec[15].setdefault("bEdits", []).append((tail, tail + "\n南安 城圮，官修。議出公帑。\n《汀州》 動支河稅。"))
    # R1: a kept retry wrote 。 where the first answer and engine A have ，
    # (scan 14, the first paragraph's first column); a first answer that
    # differs from engine A (、 for ，) in the second paragraph.
    first = paragraphs(spec[14])[0]
    block = first[:45]
    assert len(pp.CJK(block)) == 40 and block[15] == "，"
    spec[14].setdefault("sealedEdits", []).append((first[8:16], first[8:15] + "。"))
    second = paragraphs(spec[14])[1]
    block2 = second[:45]
    spec[14]["punctuationPasses"] = [
        {"block": 3, "range": [0, 40], "ok": True, "label": "vertical_text", "census": 15, "places": 5,
         "marks": 5, "firstAnswerMarks": 5, "reason": "5 places marked but 15 counted on the page (after retry)",
         "firstAnswer": block},
        {"block": 5, "range": [0, 40], "ok": True, "label": "vertical_text", "census": 15, "places": 5,
         "marks": 5, "firstAnswerMarks": 5, "reason": "5 places marked but 15 counted on the page (after retry)",
         "firstAnswer": block2.replace("，", "、", 1)},
    ]
    return spec


def gap_of(page: str, a: str, b: str | None = None, edge: str = "") -> rn.Gap:
    """A made-up gap: the page's text, engine A's and engine B's (engine A's
    when None)."""
    b = a if b is None else b
    return rn.Gap(1, 0, len(page), rn.tokens(page, "page"), rn.tokens(a, "a"), rn.tokens(b, "b"), a, b, 0, edge=edge)


def run_page(text: str, a: str, b: str | None = None, record: dict[str, Any] | None = None,
             labels: list[str] | None = None) -> tuple[str, list[dict[str, Any]], list[Any]]:
    """One made-up page through the stage's page step (N1, N5, N6, N2): the
    page's TEXT, engine A's body A - one layout block per line, labelled
    `table` where the line holds a table's markup (a tag, a pipe) and
    `text` otherwise, or by LABELS, one per line - and engine B's (A's when
    None), the seal's RECORD (one that recorded doubts, none, unless given);
    returns the text it leaves, the records and the doubts."""
    plan = rr.Plan()
    ledger = re_.Ledger({1: text})
    records = ledger.apply("runs", rn._page_runs(page_of(text, a, b, record, labels), plan))
    return ledger.texts[1], records, plan.doubts


def page_of(text: str, a: str, b: str | None = None, record: dict[str, Any] | None = None,
            labels: list[str] | None = None, scan: int = 1, folio: set[str] | None = None) -> rn.PageRuns:
    """The made-up page of run_page, as the stage reads it (scan SCAN, the
    folio's forms FOLIO)."""
    b = a if b is None else b
    lines = a.split("\n")
    labels = labels or ["table" if re.search(r"<t[adr]|\|", line) else "text" for line in lines]
    blocks = [{"text": line, "label": label, "index": k} for k, (line, label) in enumerate(zip(lines, labels))]
    pd = rr.PageData(scan=scan, text=text, entry={}, record={"doubts": [], **(record or {})}, a_body=a, a_blocks=blocks,
                     b_text=b, b_body=b, band_text="", band_numbers=[])
    return rn.PageRuns(pd, folio or set())


def run_pages(pages: dict[int, rn.PageRuns], records: dict[int, dict[str, Any]] | None = None) -> rr.Plan:
    """Made-up pages (scan -> page_of) through the stage's text_runs, the
    seals' RECORDS (a tableStitch) per scan: the edits proposed and the
    doubts."""
    book = SimpleNamespace(pages={n: SimpleNamespace(record=(records or {}).get(n, {})) for n in pages})
    setting = rn.Setting(book, {}, None, list(pages), {n: page.text for n, page in pages.items()})
    setting.page = lambda scan, text=None: pages[scan]  # type: ignore[method-assign]
    plan = rr.Plan()
    rn.text_runs(setting, plan)
    return plan


TABLE = ("表一\n\n| 年代 | 規模 | 出處 |\n| --- | --- | --- |\n| 正德年間 | 城砌以石高尺馬道實以土 | 康熙上猶縣志 |\n"
         "| 嘉靖五年 | 周圍三里門三 |  | 明世宗實錄 |\n")
TABLE_A = ("表一\n<table><tr><td>年代</td><td>規模</td><td>出處</td></tr><tr><td>正德年間</td>"
           "<td>城砌以石，高18尺，馬道實以土。</td><td>康熙《上猶縣志》3/2a-b。</td></tr><tr><td>嘉靖五年</td>"
           "<td>周圍三里，門三。</td><td>—</td><td>《明世宗實錄》</td></tr></table>")
TABLE_DONE = ("表一\n\n| 年代 | 規模 | 出處 |\n| --- | --- | --- |\n"
              "| 正德年間 | 城砌以石，高18尺，馬道實以土。 | 康熙《上猶縣志》3/2a-b。 |\n"
              "| 嘉靖五年 | 周圍三里，門三。 |  — | 《明世宗實錄》 |\n")


def cell_checks() -> None:
    """N5 on made-up pages: what it must write, and each look-alike it must
    leave."""
    text, records, doubts = run_page(TABLE, TABLE_A)
    rules = [(r["rule"], r["after"]) for r in records]
    check(text == TABLE_DONE, f"N5: the marks both engines read in a table's cells are written, the cell boundaries kept ({text!r})")
    check([r["evidence"]["tier"] for r in records if r["rule"] == "N5"] == ["cell marks"] * 7
          and ("N1", "18") in rules and ("N1", "》3/2a-b。 |\n| ") in rules and not doubts,
          f"N5: a gap of marks alone is N5's, a gap whose marks N1 writes with its run stays N1's, no doubt ({rules})")
    check(not any(r["evidence"].get("withheld") for r in records),
          "N5: the two title marks written in two gaps of one row pair up (none withheld, N1's closing one too)")
    check(run_page(text, TABLE_A)[1] == [], "N5: run again on its own output, nothing changes")
    check(pp.CJK(text) == pp.CJK(TABLE), "N5: no CJK character changes")
    # The look-alikes in running text and on a line that is no table (N8
    # writes running text's marks: agreed_checks).
    check([r["rule"] for r in run_page("城砌以石高尺\n", "城砌以石，高尺。")[1]] == ["N8"],
          "N5: an empty gap of running text where both engines read a mark is not N5's (N8's)")
    check(not any(r["rule"] == "N5" for r in run_page("甲乙丙丁 | 戊己庚辛\n", "甲乙，丙丁。 | 《戊己》庚辛")[1]),
          "N5: a line that merely holds a pipe is no table")
    row = "| 甲 | 乙 |\n| --- | --- |\n| 城砌以石高尺 | 馬道實以土 |\n"

    def engines(cell: str, other: str = "馬道實以土") -> str:
        return f"<table><tr><td>甲</td><td>乙</td></tr><tr><td>{cell}</td><td>{other}</td></tr></table>"

    # The block's label decides, not the page's shape: the structure pass
    # builds a page table from a punctuated text block too, and its empty
    # gaps are the punctuator's.
    passes = {"punctuationPasses": [{"block": 0, "label": "text", "ok": True, "range": [0, 12], "marks": 1}]}
    text, records, doubts = run_page(row, engines("城砌以石，高尺"), record=passes, labels=["text"])
    check(text == row and not records and not doubts,
          "N5: a page table whose characters lie in an engine A text block (one the punctuator read) gets no edit")
    check(run_page(row, engines("城砌以石，高尺"), record=passes)[0] == row.replace("以石高", "以石，高"),
          "N5: the same page and engines, the block labelled a table: the comma is written")
    # The seal's record: a model read the table's marks from the image.
    text, records, _ = run_page(TABLE, TABLE_A, record={"doubts": [{"kind": "punctuation-whole-page", "detail": "x"}]})
    check(not [r for r in records if r["rule"] == "N5"] and "城砌以石高18尺馬道實以土 |" in text,
          "N5: a page punctuated whole keeps its cells' empty gaps (the runs are still N1's)")
    text, records, _ = run_page(TABLE, TABLE_A, record={"tablesRebuilt": [{"block": 1, "outcome": "accepted"}]})
    check(not [r for r in records if r["rule"] == "N5"] and "城砌以石高18尺馬道實以土 |" in text,
          "N5: a table rebuilt from its crop (the seal names its block) keeps its cells' empty gaps")
    two = "| 甲 | 乙 |\n| --- | --- |\n| 丙丁 | 戊己 |\n\n| 庚 | 辛 |\n| --- | --- |\n| 壬癸 | 子丑 |\n"
    two_a = ("<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙，丁</td><td>戊己</td></tr></table>\n"
             "<table><tr><td>庚</td><td>辛</td></tr><tr><td>壬，癸</td><td>子丑</td></tr></table>")
    text, records, _ = run_page(two, two_a, record={"tablesRebuilt": [{"block": 0, "outcome": "accepted"}]})
    check(text == two.replace("壬癸", "壬，癸") and len(records) == 1,
          f"N5: a rebuilt table is set aside per table; the page's other table gets its marks ({text!r})")
    check(rn.unread_marks({"doubts": []}) and rn.unread_marks({"tablesRebuilt": [{"outcome": "refused"}], "doubts": [{"kind": "table-rebuild"}]})
          and not rn.unread_marks({}) and not rn.unread_marks({"tablesRebuilt": []})
          and rn.unread_marks({"punctuationPasses": [{"block": 0, "ok": True}]}),
          "unread_marks: a seal with no doubts key is an older one, taken as read unless it records block passes")
    refused = {"tablesRebuilt": [{"block": 0, "from": "nominated", "outcome": "refused"}], **passes}
    check([r["rule"] for r in run_page(TABLE, TABLE_A, record={"tablesRebuilt": [{"block": 1, "outcome": "refused"}]})[1]].count("N5") == 7
          and run_page(row, engines("城砌以石，高尺"), record=refused, labels=["text"])[0] == row,
          "N5: a refused rebuild does not switch the rule off for a table block; a nominated text block kept its pass")
    # Engines that differ, a star, a note marker, a mark of the page, an answer.
    text, records, doubts = run_page(row, engines("城砌以石，高尺"), engines("城砌以石；高尺"))
    check(text == row and not doubts, "N5: a gap where the engines read different marks is left, with no doubt")
    text, records, doubts = run_page(row, engines("城砌以石*高尺"))
    check(text == row and not doubts, "N5: a star both engines read (a table's note sign) is not written")
    for cell in ("城砌以石~高尺", "城砌以石[高]尺", "城砌以石<高尺", "城砌以石\\高尺", "城砌以石 高尺", "城砌以石​高尺"):
        text, records, doubts = run_page(row, engines(cell))
        check(text == row and not doubts, f"N5: a character a renderer reads as markup, or one that prints nothing, is not written ({cell!r})")
    text, records, doubts = run_page(row, engines("城砌以石①，高尺"))
    check(text == row, "N5: a gap that holds a note marker is not written")
    marked = row.replace("以石高", "以石、高")
    check(run_page(marked, engines("城砌以石，高尺"))[0] == marked, "N5: a gap where the page holds a mark of its own is left")
    text, records, doubts = run_page(row, engines("城砌以石，高尺", "《馬道實以土"))
    check(text == row.replace("以石高", "以石，高") and len(records) == 1
          and [(d[0], d[3].get("marks"), d[3].get("withheld")) for d in doubts] == [("run-no-place", 1, 1)],
          f"N5: a bracket with no partner on its row is withheld, the other marks written, the page's one doubt counts it ({text!r})")
    # Cell boundaries that are not the engines' one to one.
    text, records, doubts = run_page(row, "<table><tr><td>甲</td><td>乙</td></tr><tr><td>城砌以石，高尺。</td><td>—</td>"
                                          "<td>《馬道實以土》</td></tr></table>")
    check(text == row.replace("以石高", "以石，高")
          and [(d[0], d[3].get("marks"), d[3].get("gaps"), d[3].get("withheld")) for d in doubts] == [("run-no-place", 4, 1, 1)],
          f"N5: where the page's cell boundaries are not the engines' one to one, the gap across them is left, its three marks "
          f"counted with the closing bracket withheld after it ({text!r})")
    # An HTML table, a cell's line break.
    html = "<table>\n<tr>\n<td>治世餘聞<br>繼事紀聞</td>\n<td>城漸圮巡撫酌議</td>\n</tr>\n</table>\n"
    text, records, _ = run_page(html, "<table><tr><td>《治世餘聞》\\n《繼事紀聞》</td><td>城漸圮，巡撫酌議。</td></tr></table>",
                                "<table><tr><td>《治世餘聞》<br>《繼事紀聞》</td><td>城漸圮，巡撫酌議。</td></tr></table>")
    check(text == "<table>\n<tr>\n<td>《治世餘聞》<br>《繼事紀聞》</td>\n<td>城漸圮，巡撫酌議。</td>\n</tr>\n</table>\n",
          f"N5: an HTML table's cells get their marks, each side of a cell's line break ({text!r})")
    text, records, _ = run_page(html, "<table><tr><td>《治世餘聞\\n繼事紀聞》</td><td>城漸圮巡撫酌議</td></tr></table>")
    check("<td>《治世餘聞<br>繼事紀聞》</td>" in text, f"N5: a pair written about a cell's line break stands ({text!r})")
    text, records, doubts = run_page(html, "<table><tr><td>《治世餘聞》《繼事紀聞》</td><td>城漸圮巡撫酌議</td></tr></table>")
    check(text == html and [(d[0], d[3].get("marks")) for d in doubts] == [("run-no-place", 4)],
          f"N5: marks whose side of the page's line break engine A does not give are not written, nor the brackets "
          f"about them (two titles would read as one); the four are the page's one doubt ({text!r})")
    # The bracket guard: two titles of one cell with a gap between them the
    # engines differ in; a cell boundary between an opening and a closing one.
    text, records, doubts = run_page("| 甲 | 乙 |\n| --- | --- |\n| 治世餘聞繼事紀聞 | 庚辛 |\n",
                                     engines("《治世餘聞》《繼事紀聞》", "庚辛"), engines("《治世餘聞》、《繼事紀聞》", "庚辛"))
    check("| 治世餘聞繼事紀聞 |" in text and not records and [(d[0], d[3].get("withheld")) for d in doubts] == [("run-no-place", 2)],
          f"N5: the outer brackets of two titles are no pair where the gap between them is not written ({text!r})")
    text, records, doubts = run_page("| 甲 | 乙 |\n| --- | --- |\n| 見明史卷三 | 及實錄載 |\n", engines("見《明史卷三", "及實錄》載"))
    check("| 見明史卷三 | 及實錄載 |" in text and not records and [d[3].get("withheld") for d in doubts] == [2],
          f"N5: an opening bracket in one cell and a closing one in the next are no pair ({text!r})")
    html2 = "<table><tr><td>甲乙</td><td>丙丁</td></tr></table>\n"
    check(run_page(html2, html2.replace("甲乙", "甲《乙").replace("丙丁", "丙》丁").strip())[0] == html2,
          "N5: an HTML cell boundary on one line parts two brackets too")
    # The page's space where engine A breaks the line in the cell.
    text, records, _ = run_page("| 甲 | 乙 |\n| --- | --- |\n| 治世餘聞 繼事紀聞 | 庚辛 |\n",
                                engines("《治世餘聞》\\n《繼事紀聞》", "庚辛"), engines("《治世餘聞》<br>《繼事紀聞》", "庚辛"))
    check("| 《治世餘聞》 《繼事紀聞》 |" in text,
          f"N5: where the page's cell holds a space and engine A breaks the line, each line's marks go on its side ({text!r})")
    text, records, doubts = run_page("| 甲 | 乙 |\n| --- | --- |\n| 治世餘聞 繼事紀聞 | 庚辛 |\n",
                                     engines("《治世餘聞》\\n——\\n《繼事紀聞》", "庚辛"), engines("《治世餘聞》<br>——<br>《繼事紀聞》", "庚辛"))
    check("| 治世餘聞 繼事紀聞 |" in text and [d[3].get("marks") for d in doubts] == [5],
          f"N5: a line of marks between them has no side of the page's space: the stretch is left, its marks counted ({text!r})")
    # N5 names the edit for the marks N1 leaves; with every one withheld it is N1's.
    text, records, _ = run_page("<table>\n<tr>\n<td>丙丁<br>戊己</td>\n</tr>\n</table>\n", "<table><tr><td>丙丁》\\nAD戊己</td></tr></table>",
                                "<table><tr><td>丙丁》<br>AD戊己</td></tr></table>")
    check("<td>丙丁<br>AD戊己</td>" in text and [(r["rule"], r["evidence"]["tier"], r["evidence"].get("withheld")) for r in records]
          == [("N1", "T2", ["》"])],
          f"N5: an edit whose cell marks were all withheld is N1's, its run written ({text!r})")
    # The table's edge: what stands before the table is running text.
    text, records, _ = run_page("表一城池\n\n| 甲乙 | 丙丁 |\n| --- | --- |\n| 戊己 | 庚辛 |\n",
                                "表一城池。\n<table><tr><td>《甲乙》</td><td>丙丁</td></tr><tr><td>戊己</td><td>庚辛</td></tr></table>")
    check(text.startswith("表一城池\n\n| 《甲乙》 | 丙丁 |"),
          f"N5: in the gap that enters a table, the cell's mark is written and the line before keeps its end ({text!r})")
    # stretches() on a made-up gap: the offsets of N5's marks, the brackets listed.
    gap = gap_of(" | ", "。</td><td>《")
    gap.tables, gap.bare = [(-1, 99)], (True, True)
    check(rn.weave(gap)[0] == "。 | 《" and gap.cells == [0, 4] and gap.wrote == [4],
          "N5: a stretch of marks alone in a table is written, its marks listed, its bracket listed for the pairing guard")
    gap = gap_of(" | ", "。</td><td>《")
    gap.tables = [(-1, 99)]
    check(rn.weave(gap)[0] == " | " and gap.cells == [] and not gap.cell_lost,
          "N5: a stretch beside no character of a block the punctuator never read is N1's alone, which leaves marks with no run")
    check(rn.table_spans("甲 | 乙\n\n| 甲 | 乙 |\n| --- | --- |\n| 丙 | 丁 |\n") == [(7, 40)],
          "table_spans: a Markdown table is its label row, rule and rows; a line with a pipe is none")


def unit_checks() -> None:
    """The stage's functions on made-up gaps: what each must write, and the
    look-alike it must leave."""
    gap = gap_of("-。", "119-140。")
    check(rn.weave(gap)[0] == "119-140。" and gap.dropped == 1,
          "N1: the hyphen a dropped run left on the page goes with the run written")
    gap = gap_of("》：（），", "》5:3（1994），")
    check(rn.weave(gap)[0] == "》5：3（1994），", "N1: a run takes the page's own form of the connector it left behind")
    gap = gap_of("：，", "：14,625")
    check(rn.weave(gap)[0] == "：14,625" and gap.dropped == 1,
          "N1: but a figure's comma the page holds full width: the run as engine A spells it (14,625)")
    check(rn.engine_marks(gap_of("</u>）（", "（1987）"), "</u>）（") is None, "N6: a gap that holds the page's markup is left")
    gap = gap_of("，）。", "，1999）", "，1999)。")
    check(rn.weave(gap)[0] is None, "N1: a full stop of the page that engine A lacks and no run carries is not dropped")
    check(rn.weave(gap_of("「", "（1582）「"))[0] == "（1582）「",
          "N1: the page's mark is matched to the engines' mark of its own class (the year stays outside the quotation)")
    check(rn.weave(gap_of("（？）", "（？-1604）"))[0] == "（？-1604）",
          "N1 T2: the hyphen both engines read beside the year is written with it")
    check(rn.weave(gap_of("，", "，，1989"))[0] is None,
          "N1 T2: a run whose place among the page's marks differs by the matching is not written")
    check(rn.weave(gap_of("\n\n", "1989"))[0] is None,
          "N1: a run with no mark beside it is not put on a side of the page's line break engine A does not give")
    check(rn.weave(gap_of("。\n\n", "。\nIbid.\n"))[0] is None,
          "N1: a run on a line of its own in engine A is not joined to the next line")
    check((rn.engine_marks(gap_of("』，）（", "》45：4（1987），"), "』，）（") or [None])[0] == "』45：4（1987），",
          "N6: the page's marks in another order: the gap in the engines' order keeps the page's own bracket shape")
    for text, wanted in (("。\n18 Fernand Braudel", True), ("。\n〔1〕", True), ("，1989", False)):
        toks = rn.tokens(text, "a")
        check(rn.line_initial_figure(toks, rn.runs_of(toks)[0]) is wanted,
              f"a figure opening a line of engine A is a line's own number: {text!r} -> {wanted}")
    check(rn.note_like(rn.tokens("。12", "a")) and rn.note_like(rn.tokens("。」3", "a"))
          and not rn.note_like(rn.tokens("》12", "a")) and not rn.note_like(rn.tokens("pp. 12", "a")),
          "a short figure right after a sentence's end is taken for a note number, one after a title mark is not")
    check([t.kind for t in rn.tokens(" - ", "page")] == [rn.SPACE, rn.MARK, rn.SPACE]
          and rn.tokens("\n- ", "page")[1].kind == rn.STRUCT and rn.tokens("\n---\n", "page")[1].kind == rn.STRUCT,
          "tokens: a hyphen inside a line is a mark; a list marker and a rule at a line's start are structure")
    check(rn.tokens("。<sup>3</sup>", "a")[1].kind == rn.MARKER and rn.tokens("。❶", "a")[1].kind == rn.MARKER,
          "tokens: an HTML superscript and a dingbat figure are note markers")
    check(rn.agree(rn.tokens("Chu, T. Local Government. Harvard, 1962.", "a"),
                   rn.tokens("Chu, T. *Local Government*. Harvard, 1962.", "b"))
          and not rn.agree(rn.tokens("30</td><td>29", "a"), rn.tokens("3029", "b")),
          "two readings that cut the same characters into runs differently agree; two figures and one figure do not")
    import _repair_inputs as ri
    import _repair_joins as rj
    frame = ri.parse_assembly("<!-- page_001 -->\n\n甲\n\n<!-- page_002 -->\n<!-- page_003 -->\n\n乙\n")
    composed = frame.compose({2: "Tang Li-chung\n\n2002\n"})
    check(composed == "<!-- page_001 -->\n\n甲\n\n<!-- page_002 -->\n\nTang Li-chung\n\n2002\n\n<!-- page_003 -->\n\n乙\n"
          and frame.compose({}) == "<!-- page_001 -->\n\n甲\n\n<!-- page_002 -->\n<!-- page_003 -->\n\n乙\n"
          and not rj.contract_problems(composed, {1: "start", 2: "paragraph", 3: "paragraph"}),
          "compose: a text written into a page sealed empty gets a blank line each side; unchanged, the frame is byte for byte")
    hyphenated = rn.tokens("eds., Challenging Authority: The Historical Study of Conten-tious Politics (London, 1998), pp. 91-104.", "b")
    joined = rn.tokens("eds., Challenging Authority: The Historical Study of Contentious Politics (London, 1998), pp. 91-104.", "a")
    check(not rn.agree(joined, hyphenated) and rn.nearly(joined, hyphenated)
          and rn.nearly(rn.tokens("'Brigand Region' and 'District':", "a"), rn.tokens("\u2018Brigand Region\u2019 and \u2018District\u2019:", "b"))
          and not rn.nearly(joined, rn.tokens("eds., Challenging Authority: The Historical Study of Contentious Politic (London, 1998), pp. 91-104.", "b"))
          and not rn.nearly(joined, rn.tokens("eds., Challenging Authority: The Historical Study of Contentious Politics (London, 1998), pp. 91104.", "b"))
          and not rn.nearly(rn.tokens("1997", "a"), rn.tokens("", "b")),
          "nearly: a word hyphenated by one engine, curly and straight apostrophes; not a letter less, not a hyphen between figures, not a reading of nothing")
    check(rn.nearly(rn.tokens("：1—5,000", "a"), rn.tokens(":1-5,000", "b"))
          and rn.nearly(rn.tokens("第95—96页", "a"), rn.tokens("第95-96页", "b"))
          and not rn.nearly(rn.tokens("Smith—Jones", "a"), rn.tokens("Smith-Jones", "b"))
          and not rn.nearly(rn.tokens("1—5,000", "a"), rn.tokens("15,000", "b"))
          and not rn.nearly(rn.tokens("3—A", "a"), rn.tokens("3-A", "b"))
          and not rn.nearly(rn.tokens("A—3", "a"), rn.tokens("A-3", "b")),
          "nearly: a dash and a hyphen between figures; not beside a letter, not a dash for nothing")
    text, brackets = rn.written(rn.tokens('Skinner, "Introduction," in Leong, Migration (Stanford: Press, 1997), pp. 1-18.', "a"))
    check(text == 'Skinner, "Introduction," in Leong, Migration (Stanford: Press, 1997), pp. 1-18.',
          f"written: Western text keeps its spaces and its ASCII marks ({text!r})")
    check(rn.written(rn.tokens("(1996),", "a"))[0] == "（1996），" and rn.written(rn.tokens("12 34", "a"))[0] == "12 34",
          "written: an ASCII mark beside figures takes its full-width form; two figures stay apart")
    text = "卷7天文志，頁8a。"
    edit = re_.Edit(1, 2, 2, "〈", rule="N1")
    wrote = {id(edit): [0]}
    rn.unpaired(text, [edit], wrote)
    check(edit.after == "" and edit.evidence.get("withheld") == ["〈"],
          "a bracket written from the engines that finds no partner on its line is withheld")


IMPRINT = "开本：毫米开印张插页\n\n年月第版年月第次印刷\n\n字数：册数："
IMPRINT_A = ("开本： $ 850 \\times 1168 $毫米32开印张：12.375 插页2\n1980年11月第1版\n1980年11月第1次印刷\n"
             "字数：269,000\n册数：5,400")
IMPRINT_B = "开本:850×1168毫米32开 印张:12.375插页2\n1980年11月第1版 1980年11月第1次印刷\n字数:269,000 册数:5,400"


def imprint_checks() -> None:
    """An imprint page on a made-up page: the figures engine A reads in
    LaTeX and at its lines' start are written; the look-alikes are left."""
    text, records, doubts = run_page(IMPRINT, IMPRINT_A, IMPRINT_B)
    check(text == "开本：850×1168毫米32开印张：12.375插页2\n\n1980年11月第1版1980年11月第1次印刷\n\n字数：269,000册数：5,400"
          and not doubts and {r["rule"] for r in records} == {"N1"},
          f"N1: an imprint page gets its size from engine A's LaTeX and the years that open engine A's lines ({text!r})")
    check([t.kind for t in rn.tokens("$ 4 \\times 0.1 \\times 360 = 144 $", "a")] == [rn.OTHER],
          "tokens: an engine's formula with more than a product of figures stays unknown markup")
    check(rn.RUN not in [t.kind for t in rn.tokens("$ 2 \\div 3 $", "a")],
          "tokens: figures joined by a command other than \\times are not a run")
    check([t.kind for t in rn.tokens("$ 12 $", "a")] == [rn.MARKER],
          "tokens: a figure alone in LaTeX is a note marker, not a product")
    # 天地会 5's scan 3: the page keeps the size's × and lacks its figures;
    # the × is the run's own connector, and the run is written as printed.
    text, records, doubts = run_page("开本：×毫米32开", "开本： $ 850 \\times 1168 $毫米32开", "开本:850×1168毫米32开")
    check(text == "开本：850×1168毫米32开" and [r["rule"] for r in records] == ["N1"] and not doubts,
          f"N1: a × the page holds alone where the engines read 850×1168 is the run's own ({text!r}, {doubts})")
    text, records, doubts = run_page("开本，×毫米32开", "开本： $ 850 \\times 1168 $毫米32开", "开本:850×1168毫米32开")
    check(text == "开本：850×1168毫米32开" and [r["rule"] for r in records] == ["N6"]
          and [d[0] for d in doubts] == ["run-mark-dropped"],
          f"N6: the page's ，× beside the dropped size, the × the run's own ({text!r}, {doubts})")
    # Elsewhere a × alone is print, as □ is (CJK() drops it too): written
    # where both engines read it and the page lacks it, in running text and
    # in a table's cell on a page punctuated whole; a doubt where engine A
    # alone reads it; nothing where the page holds it too.
    text, records, doubts = run_page("甲乙于×月×日丙丁", "甲乙于×月×日丙丁")
    check(text == "甲乙于×月×日丙丁" and not records and not doubts,
          f"a × alone that the page and both engines hold: no edit, no doubt ({text!r}, {doubts})")
    text, records, doubts = run_page("甲乙于月日丙丁", "甲乙于×月×日丙丁")
    check(text == "甲乙于×月×日丙丁" and [r["rule"] for r in records] == ["N1", "N1"] and not doubts,
          f"N1: a × alone that both engines read and the page lacks is written ({text!r}, {doubts})")
    text, records, doubts = run_page("甲乙于月日丙丁", "甲乙于×月×日丙丁", "甲乙于月日丙丁")
    check(text == "甲乙于月日丙丁" and not records and [d[0] for d in doubts] == ["run-one-engine"] * 2,
          f"a × alone that engine A alone reads is not written, and is a doubt ({text!r}, {doubts})")
    text, records, doubts = run_page(
        "| 甲 | 乙 | 丙 |\n| --- | --- | --- |\n| 丁 |  | 戊 |\n",
        "<table><tr><td>甲</td><td>乙</td><td>丙</td></tr><tr><td>丁</td><td>×</td><td>戊</td></tr></table>",
        record={"doubts": [{"kind": "punctuation-whole-page", "detail": ""}]})
    check(text.splitlines()[-1] == "| 丁 |  × | 戊 |" and [r["rule"] for r in records] == ["N1"] and not doubts,
          f"N1: a × alone in a table's cell, on a page punctuated whole, is written ({text!r}, {doubts})")
    check([t.kind for t in rn.tokens("：×", "page")] == [rn.MARK, rn.RUN] == [t.kind for t in rn.tokens("：×", "b")],
          "tokens: a × alone is a run on every side")
    # 天地会 5's scan 3: engine A reads the copies 1—5,000, engine B 1-5,000
    # (and the colon ASCII): written as engine A reads it, with the doubt.
    text, records, doubts = run_page("字数：350,000册数：\n\n统一书号", "字数：350,000 册数：1—5,000\n统一书号",
                                     "字数:350,000\n册数:1-5,000\n统一书号")
    check(text == "字数：350,000册数：1—5,000\n\n统一书号" and [r["rule"] for r in records] == ["N1"]
          and [(d[0], d[2].split(";")[0], d[3].get("written")) for d in doubts]
          == [("run-engines-differ", "hyphens, dashes or apostrophes apart", "A")],
          f"N1: figures the engines read a dash and a hyphen apart between them, written as engine A reads them "
          f"({text!r}, {doubts})")
    for a, wanted in (("。\n999", True), ("。\n1980", False)):
        toks = rn.tokens(a, "a")
        check(rn.line_initial_figure(toks, rn.runs_of(toks)[0]) is wanted,
              f"a figure of three digits opening a line is the line's own number, one of four a year: {a!r} -> {wanted}")


def missing_glyph_checks() -> None:
    """The □ a book prints for a character it lacks, on lines of 天地会 2's
    scans 79 and 260: written where both engines read it, a doubt where they
    read it apart or one alone."""
    def kinds(doubts: list[Any]) -> list[str]:
        return [kind for kind, _, _, _ in doubts]

    text, records, doubts = run_page("以慰悬。所有黄仕简复奏折", "以慰悬□。所有黄仕简复奏折")
    check(text == "以慰悬□。所有黄仕简复奏折" and [r["rule"] for r in records] == ["N1"] and not doubts,
          f"N1: a □ both engines read before the page's full stop is written before it ({text!r})")
    text, records, doubts = run_page("恭请王命，次贼匪被官兵杀败", "恭请王命□□，□次贼匪被官兵杀败")
    check(text == "恭请王命□□，□次贼匪被官兵杀败" and [r["rule"] for r in records] == ["N1"] and not doubts,
          f"N1: □□ and □ either side of the page's comma, each in its place ({text!r})")
    text, records, doubts = run_page("奴才连文实在踪迹", "奴才连" + "□" * 7 + "文实在踪迹", "奴才连" + "□" * 8 + "文实在踪迹")
    check(text == "奴才连文实在踪迹" and not records and kinds(doubts) == ["run-engines-differ"],
          f"a run of □ the engines count apart is not written, and is a doubt ({text!r}, {kinds(doubts)})")
    text, records, doubts = run_page("即不能进兵攻岂见贼匪", "即不能进兵□攻岂见贼匪", "即不能进兵攻岂见贼匪")
    check(text == "即不能进兵攻岂见贼匪" and not records and kinds(doubts) == ["run-one-engine"],
          f"a □ engine A alone reads is not written, and is a doubt ({text!r}, {kinds(doubts)})")
    # Engine B writes a printed □ as LaTeX (天地会 5's scans 358 and 361,
    # 天地会 3's scan 116): that many □, one run.
    for page, a, b in (("孙景燧，号，浙江海盐人", "孙景燧，号□□，浙江海盐人", "孙景燧，号$ \\Box\\Box $，浙江海盐人"),
                       ("渠永湜，县人，任斗六", "渠永湜，□□县人，任斗六", "渠永湜，$ \\Box\\Box $县人，任斗六"),
                       ("即不能进兵攻岂见贼匪", "即不能进兵□攻岂见贼匪", "即不能进兵$ \\Box $攻岂见贼匪")):
        text, records, doubts = run_page(page, a, b)
        check(text == a and [r["rule"] for r in records] == ["N1"] and not doubts,
              f"N1: the □ engine B writes as LaTeX is a □, written where engine A reads it alike ({text!r}, {kinds(doubts)})")
    check([t.kind for t in rn.tokens("$ \\Box x $", "b")] == [rn.OTHER]
          and [t.kind for t in rn.tokens("$ \\Box\\Box $", "page")] == [rn.MARKUP],
          "tokens: an engine's LaTeX with more than \\Box stays unknown markup; the page's LaTeX is not read as □")


def agreed_checks() -> None:
    """N8 on made-up pages of running text: the marks both engines read alike
    where the page holds none of them, or some, are written; each look-alike
    is left."""
    for text, a, b, want, why in (
            ("城砌以石高尺\n", "城砌以石，高尺。", None, "城砌以石，高尺\n",
             "the comma both engines read is written; the full stop at the page's trailing edge is not"),
            ("甲乙丙，丁戊己\n", "甲乙丙，「丁戊己", None, "甲乙丙，「丁戊己\n",
             "where the page holds some of the engines' marks, in their order, all of them"),
            ("城砌以石高尺\n", "城砌以石,高尺", "城砌以石，高尺", "城砌以石，高尺\n",
             "an ASCII mark is read in its full-width form"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以石**，**高尺", "城砌以石，高尺\n",
             "an engine's emphasis markers are set aside"),
            ("城砌以石 高尺\n", "城砌以石，高尺", None, "城砌以石，高尺\n", "the page's space goes"),
            ("五祖灯\u3000一盏明灯\n", "五祖灯，\u3000一盏明灯", None, "五祖灯，\u3000一盏明灯\n",
             "the page's ideographic space, which both engines read too, stays"),
            ("城砌以石,高尺\n", "城砌以石，「高尺", None, "城砌以石，「高尺\n",
             "the page's ASCII mark is one of the engines' in its full-width form"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以石，<br>高尺", "城砌以石，高尺\n", "an engine's tag is set aside"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以石，\n# 高尺", "城砌以石，高尺\n",
             "engine B's heading marker after its line break is set aside"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以石__，__高尺", "城砌以石，高尺\n", "and its underscores"),
            ("城砌以石高尺\n", "，城砌以石高尺", None, None, "nothing at the page's leading edge"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以石。高尺", None, "nothing where the engines' marks differ"),
            ("城砌以石高尺\n", "城砌以石，高尺", "城砌以右，高尺", None, "nothing where engine B is not aligned"),
            ("城砌以石高尺\n", "城砌以石，\n高尺", "城砌以石，高尺", None, "nothing where engine A breaks its line"),
            ("城砌以石\n\n高尺\n", "城砌以石，高尺", None, None, "nothing where the page breaks its line"),
            ("城砌以石**高尺\n", "城砌以石，高尺", None, None, "nothing where the page holds print but marks"),
            ("城砌以石高尺\n", "城砌以石/高尺", None, None, "nothing where the engines' mark is not CJK print"),
            ("城砌以石， 高尺\n", "城砌以石，高尺", None, None, "nothing where the page holds every mark (its space stays)"),
            ("城砌以石。高尺\n", "城砌以石，高尺", None, None, "nothing where the page holds a mark the engines do not"),
            ("五祖灯\u3000一盏明灯\n", "五祖灯，一盏明灯", None, None,
             "nothing where the page holds an ideographic space the engines do not (it stays)"),
            ("城砌以石高尺\n", "城砌以石\u3000高尺", None, None, "nothing where the engines read an ideographic space alone")):
        out, records, doubts = run_page(text, a, b)
        check(out == (want or text) and [r["rule"] for r in records] == (["N8"] if want else []) and not doubts,
              f"N8: {why}: {text!r} -> {out!r} ({[r['rule'] for r in records]}, doubts {doubts})")
    # Off on a book whose profile saw doubled circles or emphasis marks.
    page = page_of("城砌以石高尺\n", "城砌以石，高尺")
    page.beside_marks = True
    check(not rn._page_runs(page, rr.Plan()), "N8: off where the profile saw marks beside the characters")
    blocks = [{"text": "城砌以石，高尺", "label": "text", "index": 0}]
    engines = SimpleNamespace(record={"doubts": []}, engine_a_blocks=lambda: blocks, engine_a_text=lambda: "城砌以石，高尺",
                              engine_b_text=lambda: "城砌以石，高尺")
    for notation, on in (({}, True), ({"doubled": True}, False), ({"emphasis": True}, False)):
        setting = rn.Setting(SimpleNamespace(pages={1: engines}, profile={"notation": notation}), {}, None, [1],
                             {1: "城砌以石高尺\n"})
        check(bool(rn._page_runs(setting.page(1), rr.Plan())) == on,
              f"N8: {'on' if on else 'off'} with the profile's notation {notation}")


def one_engine_checks() -> None:
    """A run one engine alone read (#12, judged on the page image at 312
    places): written where the other engine is not aligned, but not the
    page's folio, engine B's box for a figure, or a run its table row holds
    already; a doubt where the other engine is aligned and reads none."""
    def kinds(doubts: list[Any]) -> list[str]:
        return [kind for kind, _, _, _ in doubts]

    # 天地会 5 scan 144: engine A is not aligned (it lacks 第); engine B's page number is written.
    text, records, doubts = run_page("见本书第页福康安", "见本书页福康安", "见本书第100页福康安")
    check(text == "见本书第100页福康安" and [r["evidence"]["tier"] for r in records] == ["T1 (engine B alone)"]
          and not doubts, f"N1: a run engine B alone reads where engine A is not aligned is written ({text!r}, {kinds(doubts)})")
    # Engine B is not aligned (it lacks 路): engine A's run is written.
    text, records, doubts = run_page("北京车公庄西路号", "北京车公庄西路19号", "北京车公庄西号")
    check(text == "北京车公庄西路19号" and [r["evidence"]["tier"] for r in records] == ["T1 (engine B not aligned)"]
          and not doubts, f"N1: a run engine A reads where engine B is not aligned is written ({text!r}, {kinds(doubts)})")
    # Engine B's box for a figure is markup: nothing written, no doubt.
    check([t.kind for t in rn.tokens("(108,319),(925,712)", "b")] == [rn.MARKUP]
          and rn.RUN in [t.kind for t in rn.tokens("(108,319),(925,712)", "a")],
          "tokens: engine B's box for a figure is markup; the same text from engine A is not")
    text, records, doubts = run_page("圖明代九邊總圖資料來源", "圖明代九邊總資料來源", "圖明代九邊總圖\n(108,319),(925,712)\n資料來源")
    check(text == "圖明代九邊總圖資料來源" and not records and "run-one-engine" not in kinds(doubts),
          f"engine B's box for a figure is not written nor named ({text!r}, {kinds(doubts)})")
    # 盜區政區 scan 395: 頁382 in a note, where 382 is the page's folio: not written, a doubt.
    plan = rr.Plan()
    ledger = re_.Ledger({1: "縣治疏頁。明王守仁"})
    records = ledger.apply("runs", rn._page_runs(page_of("縣治疏頁。明王守仁", "縣治疏。明王守仁", "縣治疏頁382。明王守仁",
                                                         folio={"382"}), plan))
    check(ledger.texts[1] == "縣治疏頁。明王守仁" and not records and kinds(plan.doubts) == ["run-one-engine"],
          f"a run engine B alone reads that is the page's folio is not written, and is a doubt ({kinds(plan.doubts)})")
    # 盜區政區 scan 541: a row the page holds out of order, where engine B's run stands already.
    row = "| 南雄 | 成化尺 | 3480尺 | 15尺 |\n"
    at = row.index("化") + 1
    gap = rn.Gap(1, at, at, [], None, None, None, None, None)
    check(rn._in_row(row, gap, ["11", "3480"]) and not rn._in_row(row, gap, ["11"])
          and not rn._in_row(row.replace("|", "，"), gap, ["11", "3480"]),
          "a run a table row of the page holds already is not written into it again; prose is not a row")


def footnote_checks() -> None:
    """The footnotes of a modern book on made-up pages: a Western footnote
    that opens with its note number (N2 beside note markers), a stretch of a
    footnote that is all runs whose marks the punctuator wrote in an order
    of its own (N6), what each must leave, and the runs both engines read
    that no rule writes, which are a doubt (left_out, unaligned)."""
    def kinds(doubts: list[Any]) -> list[str]:
        return [f"{kind}: {detail.split(':')[0]}" for kind, _, detail, _ in doubts if kind != "note-markers-unverified"]

    # At the page's trailing edge: the page ends on the full stop of a
    # Chinese note and lacks its page range and the Western note after it;
    # engine B writes the note's number as LaTeX whose backslash-t is a tab
    # and hyphenates a word.
    line = "Lin Wen-hao, Rivers and Markets in the Southern Hills: Traders, Boatmen, and their kin, pp. 12-19."
    a = "前文甲乙卷七雜紀頁13b-14a。\n⑦④ " + line
    b = "前文甲乙卷七雜紀頁13b-14a。\n$ \textcircled{9} $" + line.replace("Boatmen", "Boat-men") + "\n"
    text, records, doubts = run_page("前文甲乙卷七雜紀頁。", a, b)
    check(text == "前文甲乙卷七雜紀頁13b-14a。\n\n" + line and [r["rule"] for r in records] == ["N2"]
          and kinds(doubts) == ["run-engines-differ: 1 whole line(s) hyphens or apostrophes apart; written as engine A reads them"],
          f"N2 beside note markers: at the page's end the page range goes after its character and the Western note "
          f"after it as a paragraph, without its number, as engine A reads it ({text!r}, {kinds(doubts)})")
    # The same page where phase 3 kept the page range (bench case
    # doukeoi-zingkeoi/s0077): the run the page holds is engine A's first
    # line's, so it is not "another run"; it stays as the page has it and the
    # Western note goes after it.  A page range of the page's own that the
    # engines read otherwise still keeps the gap a doubt.
    text, records, doubts = run_page("前文甲乙卷七雜紀頁13b-14a。", a, b)
    check(text == "前文甲乙卷七雜紀頁13b-14a。\n\n" + line and [r["rule"] for r in records] == ["N2"]
          and kinds(doubts) == ["run-engines-differ: 1 whole line(s) hyphens or apostrophes apart; written as engine A reads them"],
          f"N2 beside note markers: where the page holds the page range already, the Western note still goes after "
          f"it ({text!r}, {kinds(doubts)})")
    text, records, doubts = run_page("前文甲乙卷七雜紀頁13b。", a, b)
    check(text == "前文甲乙卷七雜紀頁13b。" and not records
          and "run-no-place: the page holds another run" in kinds(doubts),
          f"N2 beside note markers: a run of the page's that the engines read otherwise is another run, a doubt "
          f"({text!r}, {kinds(doubts)})")
    mid = "甲乙丙頁13b-14a。\n" + line + "\n2019丁戊己庚"
    text, records, doubts = run_page("甲乙丙頁13b-14a。\n\n2019丁戊己庚", mid)
    check(text == "甲乙丙頁13b-14a。\n\n" + line + "\n\n2019丁戊己庚" and [r["rule"] for r in records] == ["N2"]
          and not kinds(doubts),
          f"N2: whole lines go in at the page's break where the page holds the runs of the lines either side "
          f"already ({text!r}, {kinds(doubts)})")
    text, records, doubts = run_page("前文甲乙卷七雜紀頁。", a, b.replace("Markets", "Market"))
    check(text == "前文甲乙卷七雜紀頁13b-14a。" and [r["rule"] for r in records] == ["N1"]
          and kinds(doubts) == ["run-engines-differ: whole lines"],
          f"N2 beside note markers: a Western note the engines read differently (a letter apart) is not written, and "
          f"is a doubt; the page range before it is still woven ({text!r}, {kinds(doubts)})")
    # Mid-page: the page holds the brackets of the lost note's parentheses
    # about the next note's number.
    page = "甲乙丙丁。）\n\n⑬〔（明〕戊己庚辛"
    note = "Wang Li, A Study of Local Magistrates (New York: Columbia Press, 1972), p. 120."
    text, records, doubts = run_page(page, "甲乙丙丁。\n $ ^{234} $ " + note + "\n⑳〔明〕戊己庚辛",
                                     "甲乙丙丁。\n$ ^{㉟} $" + note + "\n$ ^{㉟} $〔明〕戊己庚辛")
    check(text == "甲乙丙丁。\n\n" + note + "\n\n⑬〔明〕戊己庚辛" and [r["rule"] for r in records] == ["N2"]
          and not kinds(doubts),
          f"N2 beside note markers: between two notes, the Western note goes in without its number, the page's "
          f"brackets of it go, the next note keeps the page's number ({text!r})")
    page = "甲乙丙頁。）\n㉓ （戊己庚辛"
    note = "Scott, Forms of Resistance (New Haven: Yale Press, 1985)."
    text, records, doubts = run_page(page, "甲乙丙頁60b-61a。\n⑲ " + note + "\n⑲戊己庚辛",
                                     "甲乙丙頁60b-61a。\n$ ^{⑼} $" + note + "\n$ ^{⑼} $戊己庚辛")
    check(text == "甲乙丙頁60b-61a。\n\n" + note + "\n\n㉓ 戊己庚辛" and not kinds(doubts),
          f"N2 beside note markers: the page range on the line before is woven in the same edit ({text!r})")
    # Engine B is not aligned at the gap (it lacks the character before):
    # nothing is written; the run before is the page's doubt, and the
    # Western note, which both engines read, the page's net (unaligned).
    b = "甲乙丙60b-61a。\n$ ^{⑼} $" + note + "\n$ ^{⑼} $戊己庚辛"
    text, records, doubts = run_page(page, "甲乙丙頁60b-61a。\n⑲ " + note + "\n⑲戊己庚辛", b)
    left = rn.unaligned([(page_of(page, "甲乙丙頁60b-61a。\n⑲ " + note + "\n⑲戊己庚辛", b), doubts, [])])
    check(text == page and not records and [d[0] for d in doubts if d[0] != "note-markers-unverified"] == ["run-one-engine"]
          and left == ["Scott, Forms of Resistance", "New Haven", "Yale Press, 1985"],
          f"a gap engine B is not aligned at is left; the runs both engines read that the page lacks are named ({left})")
    # A table stitched across two pages: the second page's row stands on the
    # first now (in another rendering); a row an earlier stage took out of
    # the first is named, on the pages pooled.
    head_a = "<table><tr><td>韶州</td><td>府城</td><td>1523</td><td>嘉靖2</td></tr></table>"
    cont_a = "<table><tr><td>韶州</td><td>縣城</td><td>1537</td><td>嘉靖16</td></tr></table>"
    head = page_of("| 韶州 | 府城 | 1523 | 嘉靖2 |\n| --- | --- | --- | --- |\n<table><tr><td>韶州</td><td>縣城</td><td>1537</td>"
                   "<td>嘉靖16</td></tr></table>\n", head_a, head_a.replace("<", " <"))
    cont = page_of("", cont_a)
    left_whole = rn.unaligned([(head, [], []), (cont, [], [])])
    head = page_of("| 韶州 | 府城 | 1523 | 嘉靖2 |\n| --- | --- | --- | --- |\n", head_a)
    check(left_whole == [] and rn.unaligned([(head, [], []), (cont, [], [])]) == ["1537", "16"],
          "the page net pools the pages of a stitched table: a row moved to the first page stands there; one taken out "
          "of it is named")
    # The page has no break between the two notes (mid-page): the part on
    # the line before is not given; the runs are a doubt, not dropped.
    text, records, doubts = run_page("甲乙丙頁。〔丙丁戊己", "甲乙丙頁13b。\n⑲〔丙丁戊己")
    check(text == "甲乙丙頁。〔丙丁戊己" and not records
          and kinds(doubts) == ["run-no-place: runs both engines read left out, beside a note marker"]
          and doubts[0][3]["runs"] == ["13b"],
          f"fail loud: a gap of a note marker whose part on the line before is not given is a doubt naming its runs ({kinds(doubts)})")
    text, records, doubts = run_page("甲乙丙丁。㉘戊己庚辛", "甲乙丙丁96%。 $ ^{38} $戊己庚辛", "甲乙丙丁96%。③戊己庚辛")
    check(text == "甲乙丙丁96%。㉘戊己庚辛" and [r["rule"] for r in records] == ["N1"],
          f"N1: the part of a gap before a note marker on its line is woven, the marker left as the page has it ({text!r})")
    text, records, doubts = run_page("甲乙丙丁1993，戊己", "甲乙丙丁35，1993戊己", "甲乙丙丁35，1994戊己")
    check(text == "甲乙丙丁1993，戊己" and not records
          and [(d[0], d[3].get("runs")) for d in doubts] == [("run-no-place", ["35"])],
          f"fail loud: where the page holds another run and the engines differ, the run both read is a doubt ({doubts})")
    # N6: a Western citation inside a Chinese note, the page holding the
    # brackets the punctuator saw about it.
    cite = 'Blok, "Bandits and Borders (1730-1778)," in Hanagan eds., Challenging Order (London: Minnesota Press, 1998), pp. 91-104.'
    text, records, doubts = run_page("參見甲乙〔荷〕））（（丙丁戊己", "參見甲乙〔荷〕" + cite + "丙丁戊己")
    check(text == "參見甲乙〔荷〕" + cite + "丙丁戊己" and [r["rule"] for r in records] == ["N6"]
          and records[0]["evidence"]["replaced"] == ["〕", "）", "）", "（", "（"] and not doubts,
          f"N6: a Western citation whose brackets the page holds out of order becomes engine A's text, the page's "
          f"marks named in the evidence ({text!r})")
    text, records, doubts = run_page("甲乙《社會學報》，）（丙丁", "甲乙《社會學報》3:3、4（1947），丙丁",
                                     "甲乙《社會學報》3：3、4（1947），丙丁")
    check(text == "甲乙《社會學報》3:3、4（1947），丙丁" and [r["rule"] for r in records] == ["N6"] and not doubts,
          f"N6: a journal's volume, issue and year, every mark of the page one of engine A's ({text!r})")
    text, records, doubts = run_page("甲乙〈社會學報〉）（丙丁", "甲乙《社會學報》97（1999），丙丁")
    check(text == "甲乙〈社會學報〉97（1999），丙丁",
          f"N6: the page's title mark keeps its shape, its partner outside the gap being the page's ({text!r})")
    text, records, doubts = run_page("甲乙社會學報），（丙丁", "甲乙社會學報》12（1998），丙丁")
    check(text == "甲乙社會學報），（丙丁" and not records and kinds(doubts) == ["run-no-place: the page's marks are not the engines'"],
          f"N6: not where a bracket of engine A's text would find no partner on its line; the gap stays a doubt ({text!r})")
    table = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 甲乙《學報》，）（ | 丙丁 |\n"
    text, records, doubts = run_page(table, "<table><tr><td>甲乙</td><td>丙丁</td></tr><tr><td>甲乙《學報》3:3、4（1947），"
                                            "</td><td>丙丁</td></tr></table>")
    check(text == table and not records and kinds(doubts) == ["run-no-place: the page's marks are not the engines'"],
          f"N6 leaves a gap across a table's cell boundary ({text!r})")
    check(rn.engine_marks(gap_of("》：），（", "》65：3（1999），", "》65:3(1999)，"), "》：），（")
          == ("》65：3（1999），", [0, 5, 10], ["》", "：", "）", "，", "（"], []),
          "N6: a colon the page holds that a run of engine A carries is one of engine A's marks, and none is dropped")
    check(rn._tab_marker(rn.tokens("$ \textcircled{9} $", "b")[0]) and not rn._tab_marker(rn.tokens("$ \times 3 $", "b")[0]),
          "a circled figure in LaTeX whose backslash-t is a tab is a note marker; other such LaTeX is not")


IMPRINT3 = "开本，毫米开\n\n年月第版。年月第次印刷\n\n字数：册数，\n\n统一书号：定价·元"
IMPRINT3_A = ("开本： $ 850 \\times 1168 $毫米32开\n1982年12月第1版\n1982年12月第1次印刷\n字数：343,000\n册数：6,500\n"
              "统一书号：11011·97\n定价：2.10元")
IMPRINT3_B = "开本:850×1168毫米32开\n1982年12月第1版 1982年12月第1次印刷\n字数：343,000 册数：6,500\n统一书号：11011·97 定价：2.10元"


def dropped_checks() -> None:
    """N6 where the page holds a mark that neither engine reads there (the
    user's ruling of 2026-10-06): the runs are written as engine A reads
    them, the page's mark goes, and a `run-mark-dropped` doubt says whether
    engine A's mark took its place or none did - and the look-alikes it
    must leave as they were."""
    def dropped(doubts: list[Any]) -> list[str]:
        return [d[2].split(":")[0] for d in doubts if d[0] == "run-mark-dropped"]

    # An imprint page (天地会 3's scan 3 cut short and, for the size, 天地会
    # 4's): a comma where both read a colon, a middle dot for the colon
    # before the price; across engine A's line break where the page breaks
    # too, the page's break kept (the copies).  The second year, printed on
    # one line with the first: engine A breaks the line there, engine B
    # reads a space, the page holds a full stop - the gap stays the doubt.
    text, records, doubts = run_page(IMPRINT3, IMPRINT3_A, IMPRINT3_B)
    check(text == ("开本：850×1168毫米32开\n\n1982年12月第1版。年12月第1次印刷\n\n字数：343,000册数：6,500\n\n"
                   "统一书号：11011·97定价：2.10元")
          and [r["before"] for r in records if r["rule"] == "N6"] == ["，", "，\n\n", "·"]
          and dropped(doubts) == ["頁面嘅「，」換做引擎嘅「：」", "頁面嘅「·」換做引擎嘅「：」"]
          and [d[3].get("runs") for d in doubts if d[0] == "run-no-place"] == [["1982"]],
          f"N6: an imprint page's figures are written where the page holds a mark of another class there, across "
          f"a line break of the page's too, and each doubt names the mark replaced; not across engine A's break "
          f"alone ({text!r}, {dropped(doubts)})")
    # 天地会 5's scan 3: both engines break the line between the insert
    # count and the year, the page holds a colon there; a page's single
    # newline stays single; at the page's trailing edge a break of engine
    # A's with nothing after it goes, whatever engine B reads there.
    for page, a, b, want, gone in (("印张：15，625插页：年5月第1版", "印张：15,625插页2\n1986年5月第1版",
                                    "印张:15,625插页2\n1986年5月第1版", "印张：15，625插页2\n\n1986年5月第1版",
                                    ["刪咗頁面嘅「：」，兩個引擎都冇讀到"]),
                                   ("册数，\n统一书号", "册数：6,500\n统一书号", None, "册数：6,500\n统一书号", []),
                                   ("统一书号定价·", "统一书号定价：2.10\n", "统一书号定价：2.10", "统一书号定价：2.10",
                                    ["頁面嘅「·」換做引擎嘅「：」"])):
        text, records, doubts = run_page(page, a, b)
        check(text == want and [r["rule"] for r in records] == ["N6"] and dropped(doubts) == gone,
              f"N6 across engine A's line break: the lines stay apart, the page's mark named ({text!r}, {dropped(doubts)})")
    # Footnotes: a closing bracket after a page reference that opens nothing
    # on the page, a mark doubled, a dash for engine A's hyphen.
    for page, a, want, gone in (("甲乙事紀頁。）丙丁戊己", "甲乙事紀頁336。丙丁戊己", "甲乙事紀頁336。丙丁戊己", "）"),
                                ("甲乙政紀頁。。丙丁戊己", "甲乙政紀頁34a-36a。丙丁戊己", "甲乙政紀頁34a-36a。丙丁戊己", "。"),
                                ("甲乙見圖—丙丁戊己", "甲乙見圖4-2丙丁戊己", "甲乙見圖4-2丙丁戊己", "—")):
        text, records, doubts = run_page(page, a)
        check(text == want and [r["rule"] for r in records] == ["N6"] and records[0]["evidence"]["dropped"] == [gone]
              and dropped(doubts) == [f"刪咗頁面嘅「{gone}」，兩個引擎都冇讀到"] and len(doubts) == 1,
              f"N6: the page's {gone} that neither engine reads there goes, the runs written; a doubt names it ({text!r})")
    own = rn.engine_marks(gap_of("））", "(Press, 1962), p. 175."), "））")
    check(own is not None and own[3] == [("）", "")],
          f"N6: a bracket dropped stands for no other mark of engine A's left over: neither engine reads it ({own})")
    # A connector the page holds that a run carries is the run's, as before:
    # no mark dropped, no doubt (a comma as engine A spells the run, 3,000).
    for page, a, want in (("甲乙共計，人丙丁", "甲乙共計3,000。人丙丁", "甲乙共計3,000。人丙丁"),
                          ("甲乙頁-丙丁", "甲乙頁12-14，丙丁", "甲乙頁12-14，丙丁")):
        text, records, doubts = run_page(page, a)
        check(text == want and [r["rule"] for r in records] == ["N6"] and not dropped(doubts),
              f"N6: a connector of the page that a run carries is not said to be dropped ({text!r}, {dropped(doubts)})")
    # Look-alikes, left as the doubt they were: a bracket with its partner on
    # the page (on its line, past a title mark's end, in the next paragraph);
    # a page's dash engine B reads where engine A reads a hyphen; a line
    # break of engine A's alone (body text engine B reads as one line), of
    # the page where engine A has none, two of engine A, the page's apart; a
    # bracket after engine A's break with no partner on its line; engine B's
    # break at another place than engine A's; Western text across a break.
    for page, a, b, what in (("（甲乙事紀頁。）丙丁戊己", "甲乙事紀頁336。丙丁戊己", None,
                              "a closing bracket whose partner opens the line"),
                             ("甲乙卷〈（弭盜疏〉，）頁丙丁", "甲乙卷4〈弭盜疏〉，頁丙丁", None,
                              "an opening bracket whose partner stands past a title mark's end"),
                             ("他說：「年我們回鄉。\n\n下一段話完。」", "他說：1949年我們回鄉。\n下一段話完。」", None,
                              "an opening quotation mark whose partner closes the next paragraph"),
                             ("於—年間大亂。", "於1840-1842年間大亂。", "於1840—1842年間大亂。",
                              "a page's dash that engine B reads where engine A reads a hyphen"),
                             ("事遂平息。年春再起。", "事遂平息\n1950年春再起。", "事遂平息1950年春再起。",
                              "a line break of engine A's alone"),
                             ("册数，\n\n统一书号", "册数：6,500 统一书号", None, "a line break of the page's alone"),
                             ("册数，统一书号", "册数：6,500\n\n统一书号", None, "two line breaks of engine A"),
                             ("册数\n\n，\n\n统一书号", "册数：6,500\n统一书号", None, "the page's line breaks apart"),
                             ("甲，\n\n乙丙", "甲：6,500\n（1979乙丙", None,
                              "an opening bracket after engine A's break with no partner on its line"),
                             ("甲，乙丙", "甲：1980\n1981乙丙", "甲：1980 1981\n乙丙",
                              "a line break of engine A's that engine B makes at another place"),
                             ("甲，乙丙", "甲：AB12\n1979乙丙", "甲：AB12\n1979乙丙",
                              "Western text across engine A's line break")):
        text, records, doubts = run_page(page, a, b)
        check(text == page and not [r for r in records if r["rule"] == "N6"] and not dropped(doubts)
              and any(d[0] == "run-no-place" for d in doubts),
              f"N6 leaves {what}: the gap is a doubt, no mark dropped ({text!r}, {[r['rule'] for r in records]})")
    # Where the pairing guard undoes the edit, no mark is said to be dropped.
    text, records, doubts = run_page("甲乙社會學報：，丙丁", "甲乙社會學報》12，丙丁")
    check(text == "甲乙社會學報：，丙丁" and not records and not dropped(doubts)
          and [d[0] for d in doubts] == ["run-no-place"],
          f"N6: an edit whose bracket finds no partner is not made, and names no dropped mark ({text!r}, {doubts})")
    # Nor where the page-edge check withholds it (the page's last line would
    # change kind with the Western note N2 writes after it); where it keeps
    # the edit, the doubt is raised with it.
    page = "甲乙丙丁。\n\n- 戊己事紀頁。）庚辛前文甲乙卷七雜紀頁。"
    for a, kept in (("甲乙丙丁。\n- 戊己事紀頁336。庚辛前文甲乙卷七雜紀頁13b-14a。\n⑦ Lin Wen, Rivers and Markets, pp. 12-19.", False),
                    ("甲乙丙丁。\n- 戊己事紀頁336。庚辛前文甲乙卷七雜紀頁13b-14a。", True)):
        plan, along = rr.Plan(), {}
        edits = rn._page_runs(page_of(page, a, a.replace("⑦ ", "$ ^{⑦} $")), plan, along=along)
        edits = rn.edge_safe(None, {1: page}, [1], None, edits, plan, along)
        check(("N6" in [e.rule for e in edits]) is kept and (dropped(plan.doubts) == ["刪咗頁面嘅「）」，兩個引擎都冇讀到"]) is kept,
              f"N6: the dropped mark's doubt stands with the edit the page-edge check {'keeps' if kept else 'withholds'} "
              f"({[e.rule for e in edits]}, {dropped(plan.doubts)})")


def bibliography_checks() -> None:
    """N2 on a made-up bibliography of Western entries between Chinese ones:
    an entry that goes on with a Chinese note on the next character's line,
    and an entry engine A cuts in two where the print wraps it while engine
    B reads it as one line, beside an entry the engines read apart; an
    entry engine A cuts at a hyphen."""
    def kinds(doubts: list[Any]) -> list[str]:
        return [f"{kind}: {detail.split(':')[0]}" for kind, _, detail, _ in doubts]

    # The page holds the heading's colon and a break; engine A's last entry
    # goes on with the Chinese note the page holds after the break, and holds
    # a colon of its own (London:) that the heading's could be matched to.
    lin = "Lin, W. Rivers and Markets. Taipei: Sun Press, 1962."
    hu = "Hu, E. J. Boats. London: Wade & Nichols, 1969."
    a = f"外文專書：\n{lin}\n{hu} 另有中譯本甲乙"
    b = a.replace("Rivers and Markets", "*Rivers and Markets*").replace("Boats", "*Boats*")
    text, records, doubts = run_page("外文專書：\n\n另有中譯本甲乙", a, b)
    check(text == f"外文專書：\n\n{lin}\n\n{hu}另有中譯本甲乙" and [r["rule"] for r in records] == ["N2"] and not doubts,
          f"N2: the entry on the next character's line is woven with the page's text after its break, apart from "
          f"the heading's colon before it ({text!r}, {kinds(doubts)})")
    text, records, doubts = run_page("外文專書：\n\n另有中譯本甲乙", a, b.replace("1969", "1968"))
    check(text == "外文專書：\n\n另有中譯本甲乙" and not records
          and kinds(doubts) == ["run-engines-differ: whole lines", "run-no-place: runs both engines read left out, no rule writes them"],
          f"N2: where the engines read a figure on the next character's line apart, nothing is written; the runs "
          f"both read are named ({kinds(doubts)})")
    a = f"外文專書：\n{lin}\n12 {hu} 另有中譯本甲乙"
    text, records, doubts = run_page("外文專書：\n\n另有中譯本甲乙", a, a.replace("Boats", "*Boats*"))
    check(text == "外文專書：\n\n另有中譯本甲乙" and not records and kinds(doubts) == ["run-no-place: runs beside the whole lines"],
          f"N2: where a figure opens engine A's line of the next character (a note number), nothing is written "
          f"({text!r}, {kinds(doubts)})")
    # Engine A cuts an entry at the print's hyphen (Pre-/ss) where engine B
    # reads Press, and engine B cuts another entry in two that engine A
    # reads as one line: each written as engine A reads it, the cut at the
    # hyphen joined with no space.
    chu = "Chu, T. Local Government. Harvard University Pre-ss, Cambridge, 1962."
    a = f"前文甲乙。\n{lin}\n{chu.replace('Pre-', 'Pre-' + chr(10))}\n{hu}\n後文丙丁"
    b = f"前文甲乙。\n{lin.replace(' Taipei', chr(10) + 'Taipei')}\n{chu.replace('Pre-ss', 'Press')}\n{hu}\n後文丙丁"
    text, records, doubts = run_page("前文甲乙。\n\n後文丙丁", a, b)
    check(text == f"前文甲乙。\n\n{lin}\n\n{chu}\n\n{hu}\n\n後文丙丁" and [r["rule"] for r in records] == ["N2"]
          and kinds(doubts) == ["run-engines-differ: 1 whole line(s) hyphens or apostrophes apart; written as engine A reads them"]
          and doubts[0][3]["engineA"] == [chu],
          f"N2: an entry engine A cuts at a hyphen written as one paragraph with no space at the cut, beside one "
          f"engine B cuts in two ({text!r}, {kinds(doubts)})")
    # After a Chinese entry whose year the page lacks: engine A cuts the
    # second entry where the print wraps it (two runs meet at the cut),
    # engine B reads it as one line; the engines read the third entry a mark
    # apart (named with each engine's own line of it).
    ho = "Ho, P. The Ladder of Success, 1368-1911. New York: Columbia Press{} 1967."
    hsiao = "Hsiao, K. Rural China: Imperial Control. Seattle: Washington Press, 1960."
    dardess = ("Dardess, J. W. A Ming Society: Fourteenth to Seventeenth", "Centuries. Berkeley: California Press, 1996.")
    a = f"麥田公司，1998）。\n{hsiao}\n{dardess[0]}\n{dardess[1]}\n{ho.format('.')}\n《岩波講座》"
    b = (f"麥田公司，1998）。\n{hsiao}\nDardess, J. W. *A Ming Society: Fourteenth to Seventeenth "
         f"Centuries*. Berkeley: California Press, 1996.\n{ho.format(',')}\n《岩波講座》")
    text, records, doubts = run_page("麥田公司，）。\n\n《岩波講座》", a, b)
    check(text == f"麥田公司，1998）。\n\n{hsiao}\n\n{' '.join(dardess)}\n\n《岩波講座》"
          and [r["rule"] for r in records] == ["N2"] and kinds(doubts) == ["run-engines-differ: 1 of 4 whole lines, left out"]
          and doubts[0][3]["engineB"] == [ho.format(",")],
          f"N2: the engines' lines paired where engine A cuts an entry in two, written as one paragraph; the entry "
          f"they read apart left out as a doubt; the year on the line before woven ({text!r}, {kinds(doubts)})")
    # The same, engine A reading T'ai-ho in the entry it cuts in two where
    # engine B reads Tai-ho on one line, and an entry after the one read a
    # mark apart (the bench copy of the modern book's bibliography page,
    # 2026-11-18): the lines still pair to the end (line_pairs, close
    # prints), the entry an apostrophe apart is written as engine A reads it
    # (N7's slightly), the one a mark apart stays out and the entry after it
    # goes in.  Before, the cut entry paired one with one and every line after
    # it with the wrong one, and none of the lines was written.
    watt = "Watt, J. R. The District Magistrate. New York: Columbia Press, 1972."
    cut = (dardess[0].replace("Society:", "Society: T'ai-ho County,"), dardess[1])
    a = f"麥田公司，1998）。\n{hsiao}\n{cut[0]}\n{cut[1]}\n{ho.format('.')}\n{watt}\n《岩波講座》"
    b = (f"麥田公司，1998）。\n{hsiao}\nDardess, J. W. *A Ming Society: Tai-ho County, Fourteenth to Seventeenth "
         f"Centuries*. Berkeley: California Press, 1996.\n{ho.format(',')}\n{watt}\n《岩波講座》")
    text, records, doubts = run_page("麥田公司，）。\n\n《岩波講座》", a, b)
    check(text == f"麥田公司，1998）。\n\n{hsiao}\n\n{' '.join(cut)}\n\n{watt}\n\n《岩波講座》"
          and [r["rule"] for r in records] == ["N2"]
          and kinds(doubts) == ["run-engines-differ: 1 whole line(s) apart in apostrophes, hyphens or case; written as engine A reads them",
                                "run-engines-differ: 1 of 5 whole lines, left out"]
          and doubts[0][3]["engineA"] == [" ".join(cut)] and "T'ai-ho" in doubts[0][3]["apart"][0],
          f"N2: an entry engine A cuts in two and the engines read an apostrophe apart still pairs, written as engine "
          f"A reads it; the entries after it are paired and written ({text!r}, {kinds(doubts)})")
    # A figure apart is not slight: the entry stays out, the others go in.
    b2 = b.replace("1996.", "1995.")
    text, records, doubts = run_page("麥田公司，）。\n\n《岩波講座》", a, b2)
    check(text == f"麥田公司，1998）。\n\n{hsiao}\n\n{watt}\n\n《岩波講座》"
          and kinds(doubts) == ["run-engines-differ: 3 of 5 whole lines, left out"],
          f"N2: an entry engine A cuts in two that the engines read a figure apart still pairs and stays out; the "
          f"entries after it are written ({text!r}, {kinds(doubts)})")


def note_call_checks() -> None:
    """N1 on note calls (the module's docstring)."""
    def edits(page: str, a: str, b: str) -> tuple[str, list[tuple[str, str, str]], list[str]]:
        text, records, doubts = run_page(page, a, b)
        return text, [(r["rule"], r["before"], r["after"]) for r in records], [d[0] for d in doubts]

    note = "\n①同上。"
    for name, page, a, b, want, after in (
            ("between two characters", "甲乙丙丁官兵義民。\n", "甲乙丙丁 $ ^{①} $官兵義民。", "甲乙丙丁①官兵義民。",
             "甲乙丙丁①官兵義民。\n", "①"),
            ("before the page's mark", "甲乙丙丁，戊己庚。\n", "甲乙丙丁 $ ^{①} $，戊己庚。", "甲乙丙丁①，戊己庚。",
             "甲乙丙丁①，戊己庚。\n", "①，"),
            ("after the page's mark", "甲乙〔丙〕丁戊己。\n", "甲乙〔丙〕 $ ^{①} $丁戊己。", "甲乙〔丙〕①丁戊己。",
             "甲乙〔丙〕①丁戊己。\n", "〕①"),
            ("with engine A's mark where the page holds none", "甲乙丙丁戊己庚。\n", "甲乙丙丁 $ ^{①} $，戊己庚。",
             "甲乙丙丁$ ^{①} $，戊己庚。", "甲乙丙丁①，戊己庚。\n", "①，"),
            ("at a line's end", "甲乙丙丁\n戊己庚。\n", "甲乙丙丁 $ ^{①} $\n戊己庚。", "甲乙丙丁①\n戊己庚。",
             "甲乙丙丁①\n戊己庚。\n", "①\n"),
            ("beside a bracket engine B reads in another shape", "甲乙丙丁。（至）于戊。\n", "甲乙丙丁 $ ^{①} $。（至）于戊。",
             "甲乙丙丁①。〔至〕于戊。", "甲乙丙丁①。（至）于戊。\n", "①。（"),
            ("after a call engine B alone reads", "甲①乙丙丁戊己庚。\n", "甲乙丙丁 $ ^{②} $戊己庚。", "甲①乙丙丁②戊己庚。",
             "甲①乙丙丁②戊己庚。\n", "②")):
        text, records, doubts = edits(page + note, a + note, b + note)
        check(text == want + note and [r[0] for r in records] == ["N1"] and records[0][2] == after and not doubts,
              f"N1 writes a note call both engines read, {name} ({text!r}, {records}, {doubts})")
    text, records, doubts = edits("甲乙丙丁戊己庚。\n①同上。", "甲乙1990 $ ^{①} $丙丁戊己庚。\n①同上。",
                                  "甲乙1990①丙丁戊己庚。\n①同上。")
    check(text == "甲乙1990①丙丁戊己庚。\n①同上。" and len(records) == 1,
          f"N1: a call set close beside a run of figures, no space between ({text!r})")
    text, records, doubts = edits("甲①乙丙丁戊己庚。", "甲①乙丙丁 $ ^{②} $戊己庚。", "甲①乙丙丁②戊己庚。")
    check(text == "甲①乙丙丁②戊己庚。" and len(records) == 1,
          f"N1: the page's second call, after the first it holds ({text!r})")
    for name, page, a, b in (
            ("out of the page's numbering", "甲乙丙丁官兵義民。", "甲乙丙丁⑧官兵義民。", "甲乙丙丁$ ^{⑧} $官兵義民。"),
            ("with a figure after it", "甲乙丙丁官兵義民。", "甲乙丙丁①2官兵義民。", "甲乙丙丁①2官兵義民。"),
            ("a superscript figure", "甲乙丙丁官兵義民。", "甲乙丙丁 $ ^{1} $官兵義民。", "甲乙丙丁¹官兵義民。"),
            ("a note's own number", "甲乙丙丁。\n\n同上書。", "甲乙丙丁。\n①同上書。", "甲乙丙丁。\n①同上書。"),
            ("on either side of a mark", "甲乙丙丁。戊己庚。", "甲乙丙丁。①戊己庚。", "甲乙丙丁①。戊己庚。"),
            ("beside a page mark the engines do not read", "甲乙丙丁，戊己庚。", "甲乙丙丁①。戊己庚。", "甲乙丙丁①。戊己庚。"),
            ("where the page holds a marker", "甲乙丙丁②官兵義民。", "甲乙丙丁①官兵義民。", "甲乙丙丁①官兵義民。"),
            ("read as different figures", "甲乙丙丁官兵義民。", "甲乙丙丁 $ ^{⑧} $官兵義民。", "甲乙丙丁③官兵義民。"),
            ("after one out of the page's numbering", "甲乙丙丁戊己庚辛。", "甲⑧乙丙丁①戊己庚辛。", "甲⑧乙丙丁①戊己庚辛。"),
            ("at the page's leading edge", "甲乙丙丁戊己庚。", "$ ^{①} $甲乙丙丁戊己庚。", "①甲乙丙丁戊己庚。"),
            ("in the gap of the next note's own number", "甲乙丙丁。\n\n同上。", "甲乙丙丁 $ ^{①} $。\n①同上。",
             "甲乙丙丁①。\n①同上。"),
            ("beside another (a number of two figures read figure by figure, 12 as ①②)", "甲乙丙丁戊己庚。",
             "甲乙丙丁①②戊己庚。", "甲乙丙丁 $ ^{①} $②戊己庚。"),
            ("on a page that holds a call a gap off (written, it would stand twice)", "甲乙丙①丁戊己。",
             "甲乙丙丁 $ ^{①} $戊己。", "甲乙丙丁①戊己。"),
            ("where the page breaks a line engine A reads on (two notes on one line)",
             "甲①乙丙丁戊己庚。\n①同上。\n同上書。", "甲 $ ^{①} $乙丙丁戊己庚。\n①同上。 $ ^{②} $同上書。",
             "甲①乙丙丁戊己庚。\n①同上。②同上書。")):
        text, records, doubts = edits(page, a, b)
        check(text == page and not records and doubts == ["note-markers-unverified"],
              f"a note call {name} is not written, and the page's doubt names it ({text!r}, {doubts})")
    _, _, doubts = run_page("甲乙丙丁官兵義民。", "甲乙丙丁 $ ^{⑧} $官兵義民。", "甲乙丙丁③官兵義民。")
    check(len(doubts) == 1 and "\u20388\u22603" in doubts[0][2],
          f"the page's doubt names both engines' values where they differ ({[d[2] for d in doubts]})")


def footnote_guard_checks() -> None:
    """What the footnote rules must leave, and what the stage must name, on
    made-up pages of the shapes a review of them found: each look-alike is
    left as the page has it, with a doubt naming the runs both engines
    read."""
    def kinds(doubts: list[Any]) -> list[tuple[str, str, Any, Any]]:
        return [(kind, detail.split(":")[0], fields.get("runs"), fields.get("lines"))
                for kind, _, detail, fields in doubts if kind != "note-markers-unverified"]

    # Engine B wraps the title of a Western citation in emphasis markers,
    # which cuts the citation into three runs where engine A reads one.
    cite = "Li Ming, Order Under Tension, p. 47."
    page = "甲乙丙丁。\n⑫ 參照其中圖表。"
    text, records, doubts = run_page(page, "甲乙丙丁。\n⑫ " + cite + " 參照其中圖表。",
                                     "甲乙丙丁。\n$ ^{12} $ Li Ming, *Order Under Tension*, p. 47. 參照其中圖表。")
    check(text == page and not records
          and kinds(doubts) == [("run-no-place", "runs both engines read left out, beside a note marker",
                                 ["Li Ming, Order Under Tension, p. 47"], None)],
          f"fail loud: a citation engine B cuts with emphasis markers is still named where it is left out ({kinds(doubts)})")
    left = rn.unaligned([(page_of("甲乙丙丁戊己。參照其中圖表。", "甲乙丙丁戊己。" + cite + "參照其中圖表。",
                                  "甲乙丙丁戊己。參照Li Ming, *Order Under Tension*, p. 47.其中圖表。"), [], [])])
    check(left == ["Li Ming, Order Under Tension, p. 47"],
          f"the page net compares the engines' runs with engine B's emphasis markers set aside ({left})")
    # The page puts a note marker before the full stop, the engines after it:
    # woven, the part before the markers would write the stop twice.
    for page, a in (("甲乙頁⑳。丙丁", "甲乙頁12b-13a。⑳丙丁"), ("甲乙頁⑳。\n丙丁", "甲乙頁12b-13a。⑳\n丙丁")):
        text, records, doubts = run_page(page, a, a.replace("⑳", "$ ^{20} $"))
        check(text == page and not records
              and kinds(doubts) == [("run-no-place", "runs both engines read left out, beside a note marker", ["12b-13a"], None)],
              f"a gap whose note marker the page and engine A put on either side of a mark is a doubt, not woven ({text!r})")
    text, records, doubts = run_page("甲乙頁。⑳丙丁", "甲乙頁12b-13a。⑳丙丁")
    check(text == "甲乙頁12b-13a。⑳丙丁" and [r["rule"] for r in records] == ["N1"],
          f"the part before a note marker both put after the mark is woven ({text!r})")
    # At the page's leading edge no character stands before the gap: a line
    # engine A reads there (the end of a note carried over) is not glued to
    # the page's first line; a running head in a header block is furniture.
    page = "⑧〔明〕甲乙丙丁。"
    text, records, doubts = run_page(page, "pp. 31-36.\n⑧〔明〕甲乙丙丁。", "pp. 31-36.\n$ ^{⑧} $〔明〕甲乙丙丁。")
    check(text == page and not records
          and kinds(doubts) == [("run-no-place", "runs both engines read left out, beside a note marker", ["pp. 31-36"], None)],
          f"a gap of a note marker at the page's leading edge is not woven; its runs are a doubt ({text!r})")
    text, records, doubts = run_page(page, "Outlaws in the Hills\n⑧〔明〕甲乙丙丁。",
                                     "Outlaws in the Hills\n$ ^{⑧} $〔明〕甲乙丙丁。", labels=["header", "text"])
    check(text == page and not records and not kinds(doubts),
          f"a running head before a note marker at the page's leading edge is not written into the text ({text!r})")
    # The pairing guard until nothing changes: an N6 edit kept on the trial
    # only by a bracket of another one that is then taken out; two N6 gaps
    # on one line, one undone; an N1 edit whose withheld bracket stood
    # between two runs.
    for page, a, b, what in (
            ("甲乙，（丙丁，》戊己", "甲乙（1998，5、丙丁》12），戊己", None, "an N6 edit whose partner bracket is taken out"),
            ("甲乙《學報（，丙丁，》戊己", "甲乙《學報》，3（1947丙丁1948）》，戊己", None, "two N6 gaps on one line"),
            ("甲乙《學報》，丙丁，戊己", "甲乙《學報》，3（1947丙丁1948），戊己", "甲乙《學報》，3（1947丙丁1949），戊己",
             "an N1 edit whose withheld bracket would join two runs")):
        text, records, doubts = run_page(page, a, b)
        check(text == page and not records and all(d[0] in ("run-no-place", "run-engines-differ") for d in doubts)
              and sorted(run for d in doubts for run in d[3].get("runs") or []) == sorted(
                  t.raw for t in rn.runs_of(rn.tokens(a, "a")) if (b or a).count(t.raw)),
              f"the pairing guard: {what} is not written, and its runs are doubts ({text!r}, {kinds(doubts)})")
    # N6 writes the page's own form of the marks it holds: an ASCII comma
    # the page holds (and the space after it) where engine A writes it
    # full-width; a colon a run carries, as the page holds it.
    text, records, doubts = run_page("參見甲乙〔英〕. . , , . .丙丁", "參見甲乙〔英〕K. L. Morrow，Outlaws，p. 71.丙丁",
                                     "參見甲乙〔英〕K.L.Morrow, Outlaws, p.71.丙丁")
    check(text == "參見甲乙〔英〕K. L. Morrow, Outlaws, p. 71.丙丁" and [r["rule"] for r in records] == ["N6"],
          f"N6: a comma the page holds keeps the page's form and the space after it ({text!r})")
    text, records, doubts = run_page("甲乙《學報》：），（丙丁", "甲乙《學報》65:3（1999），丙丁", "甲乙《學報》65:3(1999)，丙丁")
    check(text == "甲乙《學報》65：3（1999），丙丁" and [r["rule"] for r in records] == ["N6"],
          f"N6: a connector the page holds for a run is written in the page's form ({text!r})")
    # Curly quotation marks: a pair inside the gap is engine A's, and its
    # spaces beside the Latin runs stay.
    cite = "Blok, \u201cBandits and Borders (1730-1778),\u201d in Hanagan eds., Order (London: Minnesota Press, 1998), pp. 91-104."
    for page in ("參見甲乙〔荷〕「」））（（丙丁戊己", "參見甲乙〔荷〕））（（丙丁戊己"):
        text, records, doubts = run_page(page, "參見甲乙〔荷〕" + cite + "丙丁戊己")
        check(text == "參見甲乙〔荷〕" + cite + "丙丁戊己" and [r["rule"] for r in records] == ["N6"],
              f"N6: curly quotation marks of a pair inside the gap are engine A's, the spaces beside them kept ({text!r})")
    # N6 nearly: a hyphen inside a word apart is written as engine A reads
    # it, with the doubt; N7: a letter of a word apart too, on N6's marks
    # and on N1's (the page holding the empty bracket pair), the doubt
    # naming both readings of the word.
    cite = 'Blok, "Bandits and Borders (1730-1778)," in Hanagan eds., Order (London: Minnesota Press, 1998), pp. 91-104.'
    page = "參見甲乙〔荷〕））（（丙丁戊己"
    for other, rule, apart in ((cite.replace("Borders", "Bor-ders"), ["N6"], "hyphens, dashes or apostrophes apart"),
                               (cite.replace("Borders", "Border"), ["N7"], "a letter apart (engine A Borders, engine B Border)")):
        text, records, doubts = run_page(page, "參見甲乙〔荷〕" + cite + "丙丁戊己", "參見甲乙〔荷〕" + other + "丙丁戊己")
        check(text == "參見甲乙〔荷〕" + cite + "丙丁戊己" and [r["rule"] for r in records] == rule
              and [(d[0], d[2].split(";")[0], d[3].get("written")) for d in doubts] == [("run-engines-differ", apart, "A")],
              f"{rule}: the engines {apart.split(' (')[0]} written as engine A reads it, the doubt says so "
              f"({[r['rule'] for r in records]}, {kinds(doubts)})")
    text, records, doubts = run_page("甲乙（）丙丁", "甲乙（Lin Wenhao, Rivers and Markets）丙丁",
                                     "甲乙(Lin Wenhao, Rivers and Market)丙丁")
    check(text == "甲乙（Lin Wenhao, Rivers and Markets）丙丁" and [r["rule"] for r in records] == ["N7"]
          and [(d[0], d[3].get("written"), d[3].get("engineB")) for d in doubts]
          == [("run-engines-differ", "A", ["Lin Wenhao, Rivers and Market"])],
          f"N7: a letter apart on N1's marks, written as engine A reads it, engine B's reading in the doubt ({text!r}, {kinds(doubts)})")
    # N7: an apostrophe inside a word apart, or a word's case: written as
    # engine A reads it, the doubt naming the word as each engine spells it.
    a = "甲乙（K'ang-ming Ch'iu, Rivers and Markets）丙丁"
    for b, apart in ((a.replace("K'ang", "Kang"), "apostrophes, hyphens or case apart (engine A K'ang-ming, engine B Kang-ming)"),
                     (a.replace("Rivers", "RIVERS"), "apostrophes, hyphens or case apart (engine A Rivers, engine B RIVERS)")):
        text, records, doubts = run_page("甲乙（）丙丁", a, b.replace("（", "(").replace("）", ")"))
        check(text == a and [r["rule"] for r in records] == ["N7"]
              and [(d[0], d[2].split(";")[0], d[3].get("written")) for d in doubts] == [("run-engines-differ", apart, "A")],
              f"N7: {apart.split(' (')[0]} written as engine A reads it, the doubt names both words ({text!r}, {kinds(doubts)})")
    # N7 leaves, as the doubt it was: a word two letters apart (one engine
    # reads a word printed hyphenated across a line two letters short), a
    # figure apart, two words a letter apart each, a word of three letters
    # a letter apart, a pair of single quotation marks one engine alone
    # reads (not an apostrophe), and a word that holds a figure a letter
    # apart.
    comma = cite.replace("Minnesota Press", "Minnesota, Press")
    for mine, other, what in (
            (cite, cite.replace("Bandits", "Bands"), "a word two letters apart"),
            (cite, cite.replace("1778", "1779"), "a figure apart"),
            (cite, cite.replace("Borders", "Border").replace("Order", "Ordar"), "two words a letter apart each"),
            (cite, cite.replace("eds.", "ed."), "a word of three letters a letter apart"),
            (comma, comma.replace(", Press", ", \u2018Press\u2019"), "a quotation mark pair engine B alone reads"),
            (comma.replace(", Press", ", \u2018Press\u2019"), comma, "a quotation mark pair engine A alone reads"),
            (cite.replace("Order", "Order2"), cite.replace("Order", "Ordar2"), "a word that holds a figure a letter apart")):
        text, records, doubts = run_page(page, "參見甲乙〔荷〕" + mine + "丙丁戊己", "參見甲乙〔荷〕" + other + "丙丁戊己")
        check(text == page and not records and [(d[0], d[3].get("written")) for d in doubts] == [("run-engines-differ", None)],
              f"N7 leaves {what}: not written, the doubt as before ({text!r}, {kinds(doubts)})")
    # N7 leaves readings with no Latin letter (kana a space apart), and an
    # edit placed on N1's marks whose bracket the pairing guard withholds
    # between two runs (its partner is in a gap of figures apart): each
    # gap the plain doubt.
    for page_, a, b, what in (
            ("甲乙（）丙丁", "甲乙（アイウエ）丙丁", "甲乙(アイ ウエ)丙丁", "readings with no Latin letter"),
            ("甲乙《學報》，丙丁，戊己", "甲乙《學報》，Vol. 3（Borders丙丁1948），戊己", "甲乙《學報》，Vol. 3（Border丙丁1949），戊己",
             "an edit whose withheld bracket would join two runs")):
        text, records, doubts = run_page(page_, a, b)
        check(text == page_ and not records and doubts and all((d[0], d[3].get("written")) == ("run-engines-differ", None)
                                                                for d in doubts),
              f"N7 leaves {what}: not written, the doubt as before ({text!r}, {kinds(doubts)})")
    table = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 甲乙（）丙丁 | 戊己 |\n"
    a = "<table><tr><td>甲乙</td><td>丙丁</td></tr><tr><td>甲乙（Lin Wenhao, Rivers）丙丁</td><td>戊己</td></tr></table>"
    text, records, doubts = run_page(table, a, a.replace("Rivers", "Rivera"))
    check(text == table and not records and [(d[0], d[3].get("written")) for d in doubts] == [("run-engines-differ", None)],
          f"N7 leaves a word a letter apart in a table's cell: not written, the doubt as before ({text!r}, {kinds(doubts)})")
    # N6 not inside a table of the page, whether engine A labels the block
    # a table or text.
    table = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 甲乙《學報》，）（丙丁 | 戊己 |\n"
    a = "<table><tr><td>甲乙</td><td>丙丁</td></tr><tr><td>甲乙《學報》3:3、4（1947），丙丁</td><td>戊己</td></tr></table>"
    for labels in (None, ["text"]):
        text, records, doubts = run_page(table, a, labels=labels)
        check(text == table and not records and kinds(doubts)[:1] == [("run-no-place", "the page's marks are not the engines'",
                                                                       ["3:3", "4", "1947"], None)],
              f"N6 leaves a gap inside one table cell (block {labels}): a doubt ({text!r})")
    # N2 beside note markers leaves a note whose own number the page holds
    # (the number would stand after its note), at the page's trailing edge
    # and mid-page; the page range before it is still woven.
    line = "Lin Wen-hao, Rivers and Markets in the Southern Hills, pp. 12-19."
    for page, a, b, want in (
            ("前文甲乙卷七雜紀頁。\n⑦", "前文甲乙卷七雜紀頁13b-14a。\n⑦ " + line, "前文甲乙卷七雜紀頁13b-14a。\n$ ^{⑦} $" + line,
             "前文甲乙卷七雜紀頁13b-14a。\n⑦"),
            ("甲乙丙丁。\n⑦ ⑧〔明〕戊己", "甲乙丙丁。\n⑦ " + line + "\n⑧〔明〕戊己", "甲乙丙丁。\n$ ^{⑦} $" + line + "\n$ ^{⑧} $〔明〕戊己",
             "甲乙丙丁。\n⑦ ⑧〔明〕戊己")):
        text, records, doubts = run_page(page, a, b)
        check(text == want and kinds(doubts) == [("run-no-place", "the page holds a note number of the lines' own after its break",
                                                  None, [line])],
              f"N2 beside note markers leaves a note whose number the page holds after its break ({text!r})")
    # N2 beside note markers keeps N2's guards: a line that opens with a
    # figure of its own, or would read as a list item, is a doubt; one that
    # opens with a year is written.
    line = "Lin Wen-hao, Rivers and Markets in the Southern Hills: Traders, Boatmen, and their kin, pp. 12-19."
    text, records, doubts = run_page("前文甲乙卷七雜紀頁。", "前文甲乙卷七雜紀頁13b-14a。\n⑦ 1990 census of the hills, " + line,
                                     "前文甲乙卷七雜紀頁13b-14a。\n⑦1990 census of the hills, " + line)
    check(text == "前文甲乙卷七雜紀頁13b-14a。\n\n1990 census of the hills, " + line and [r["rule"] for r in records] == ["N2"],
          f"N2 beside note markers: a line that opens with a year is written ({text!r})")
    for opening in ("12 ", "- "):
        text, records, doubts = run_page("前文甲乙卷七雜紀頁。", "前文甲乙卷七雜紀頁13b-14a。\n⑦ " + opening + line,
                                         "前文甲乙卷七雜紀頁13b-14a。\n⑦" + opening + line)
        check(text == "前文甲乙卷七雜紀頁13b-14a。" and [r["rule"] for r in records] == ["N1"]
              and kinds(doubts) == [("run-no-place", "a line would read as Markdown structure, or opens with a figure of its own",
                                     None, [opening + line])],
              f"N2 beside note markers: a line opening {opening!r} is not written; the page range is ({text!r})")
    # The page-edge check withholds an N2 edit beside note markers (the
    # page's last line, a list item, would change kind): its doubt names
    # the page range woven in it as well as the lines.
    page = "甲乙丙丁。\n\n- 前文甲乙卷七雜紀頁。"
    a = "甲乙丙丁。\n- 前文甲乙卷七雜紀頁13b-14a。\n⑦ Lin Wen, Rivers and Markets, pp. 12-19."
    plan = rr.Plan()
    edits = rn._page_runs(page_of(page, a, a.replace("⑦ ", "$ ^{⑦} $")), plan)
    kept = rn.edge_safe(None, {1: page}, [1], None, edits, plan)
    withheld = [d for d in plan.doubts if d[2].startswith("withheld")]
    check([e.rule for e in edits] == ["N2"] and not kept and len(withheld) == 1
          and withheld[0][3] == {"runs": ["13b-14a"], "lines": ["Lin Wen, Rivers and Markets, pp. 12-19."]},
          f"the page-edge check names the runs and lines of an N2 edit it withholds ({withheld})")
    # A join runs on the line before the page's foot notes: the glue check
    # reads that line, not the last note's.
    check(rn._glued(["甲乙頁12", "①註文。"], ["34丙丁"]) and not rn._glued(["甲乙頁。", "①註文12"], ["34丙丁"]),
          "the page-edge check: a join across a page's foot notes glues the runs of the body's last line and the next "
          "page's first")
    # At a join across foot notes the page's edge is the line above them
    # (the one J judged, the one the join runs on): an edit there that
    # would glue two runs, or break the contract, is withheld; so is one
    # that changes the line's kind, and one that puts a line back among the
    # notes (盜區政區's scan 157: N2's Latin text of a note with no number,
    # after which the join ran on it), which stands before a paragraph
    # break.
    def held(texts: dict[int, str], label: str, at: str, line: str) -> tuple[list[Any], list[Any], str]:
        """LINE inserted after AT on page 1 of TEXTS, through the page-edge
        check with LABEL at page 2: the edits kept, the doubts, and what
        finalize would make of the page with the edit."""
        book = SimpleNamespace(assembly=SimpleNamespace(compose=lambda pages: "".join(
            f"<!-- page_{scan:03d} -->\n\n{pages[scan].strip()}\n\n" for scan in sorted(pages))))
        start = texts[1].index(at) + len(at)
        plan = rr.Plan()
        edit = re_.Edit(1, start, start, line, rule="N2", evidence={"lines": [line.strip()]})
        kept = rn.edge_safe(book, texts, [1, 2], {1: "start", 2: label}, [edit], plan)
        try:
            edited = {**texts, 1: texts[1][:start] + line + texts[1][start:]}
            final = om.apply_boundary_contract(book.assembly.compose(edited), {1: "start", 2: label})
        except ValueError as error:
            final = f"refused: {error}"
        return kept, plan.doubts, final
    kept, doubts, final = held({1: "甲乙丙頁\n\n①註文。\n", 2: "34丙丁。\n"}, "join", "頁", "12")
    check(not kept and [d[2].split(": ", 1)[1].split(";")[0] for d in doubts] == ["a join would glue two text runs"]
          and final.startswith("甲乙丙頁1234丙丁。"),
          f"the page-edge check: an edit on the body's last line above foot notes that a join would glue is withheld "
          f"({doubts})")
    kept, doubts, final = held({1: "甲乙丙，\n\n①註文。\n", 2: "丁戊。\n"}, "join", "，", "\n| 甲 | 乙 |")
    check(not kept and len(doubts) == 1 and doubts[0][2].startswith("withheld") and final.startswith("refused"),
          f"the page-edge check: a table row put between the body and its foot notes, which the join's contract "
          f"refuses, is withheld ({doubts})")
    page = "首行。\n\n甲乙丙，\n\n①註文。\n"
    plan = rr.Plan()
    kept = rn.edge_safe(None, {1: page}, [1], None, [re_.Edit(1, page.index("甲"), page.index("，") + 1, "| 甲乙丙， |",
                                                              rule="N2")], plan)
    check(not kept and "last line would change kind" in plan.doubts[0][2],
          f"the page-edge check: an edit that changes the kind of the line above the foot notes is withheld "
          f"({plan.doubts})")
    note = "\n\nJames Scott, Weapons of the Weak (New Haven: Yale University Press, 1985)."
    texts = {1: "田佃戶，「名為守備，實則\n\n---\n\n⑲ 註一。\n\n⑳ 註二。\n", 2: "串通」，他們皆與盜賊交結。\n"}
    kept, doubts, final = held(texts, "join", "⑲ 註一。", note)
    check(not kept and "line a join runs on past the page's foot notes would move" in doubts[0][2]
          and doubts[0][3] == {"lines": [note.strip()]} and "1985).串通」" in final,
          f"the page-edge check: a line put back among the foot notes a join runs past (it would run on that line, "
          f"1985).串通」) is withheld, its doubt naming it ({doubts})")
    kept, doubts, final = held(texts, "paragraph", "⑲ 註一。", note)
    check(len(kept) == 1 and not doubts,
          f"the same line among the notes before a paragraph break is written ({doubts})")
    # As the stage runs it (runs(), WITHDRAWN given): the line stands and the
    # join gives way - printed text before layout - and the caller writes the
    # boundary `paragraph`.
    book = SimpleNamespace(assembly=SimpleNamespace(compose=lambda pages: "".join(
        f"<!-- page_{scan:03d} -->\n\n{pages[scan].strip()}\n\n" for scan in sorted(pages))))
    start = texts[1].index("⑲ 註一。") + len("⑲ 註一。")
    plan, gone = rr.Plan(), {}
    kept = rn.edge_safe(book, texts, [1, 2], {1: "start", 2: "join"},
                        [re_.Edit(1, start, start, note, rule="N2", evidence={"lines": [note.strip()]})], plan,
                        withdrawn=gone)
    edited = {**texts, 1: texts[1][:start] + note + texts[1][start:]}
    final = om.apply_boundary_contract(book.assembly.compose(edited), {1: "start", 2: "paragraph"})
    check(len(kept) == 1 and not plan.doubts and list(gone) == [2] and "1985).\n" in final + "\n"
          and "1985).串通」" not in final,
          f"the page-edge check in the stage: the line put back among the foot notes stands and the join past them "
          f"gives way ({gone}, {plan.doubts})")
    plan = rr.Plan()
    at = texts[1].index("⑲ 註一。") + len("⑲ 註一。")
    figure = texts[1].index("註二") + 2
    edits = [re_.Edit(1, at, at, note, rule="N2"), re_.Edit(1, figure, figure, "12", rule="N1")]
    kept = rn.edge_safe(SimpleNamespace(assembly=SimpleNamespace(compose=lambda pages: "".join(
        f"<!-- page_{scan:03d} -->\n\n{pages[scan].strip()}\n\n" for scan in sorted(pages)))),
        texts, [1, 2], {1: "start", 2: "join"}, edits, plan)
    check([e.rule for e in kept] == ["N1"] and len(plan.doubts) == 1,
          f"the page-edge check withholds the edit that moves the joined line alone, not a figure written inside a "
          f"note beside it ({plan.doubts})")
    texts = {1: "首段甲乙。\n\n甲乙丙頁\n\n①註文。\n", 2: "34丙丁。\n"}
    edits = [re_.Edit(1, texts[1].index("頁") + 1, texts[1].index("頁") + 1, "12", rule="N2"),
             re_.Edit(1, texts[1].index("文") + 1, texts[1].index("文") + 1, "56", rule="N1"),
             re_.Edit(1, texts[1].index("。") + 1, texts[1].index("。") + 1, "\n\nSmith, A Book (1990).", rule="N2")]
    plan = rr.Plan()
    kept = rn.edge_safe(SimpleNamespace(assembly=SimpleNamespace(compose=lambda pages: "".join(
        f"<!-- page_{scan:03d} -->\n\n{pages[scan].strip()}\n\n" for scan in sorted(pages)))),
        texts, [1, 2], {1: "start", 2: "join"}, edits, plan)
    check([e.after for e in kept] == ["56", "\n\nSmith, A Book (1990)."] and len(plan.doubts) == 1,
          f"the page-edge check: of the edits on a page a join runs on past its foot notes, only the one on the "
          f"joined line is withheld - not one inside a note, nor a line written above the joined line "
          f"({[e.after for e in kept]}, {plan.doubts})")
    # A page with text and no CJK character, which lacks lines both engines
    # read: the page net names them.
    a = "Bibliography\nSmith, John. A Book of Things. London, 1990.\nJones, Kate. Another Book. Paris, 1991."
    plan = run_pages({1: page_of("Bibliography\n", a)})
    check(not plan.edits and [(d[0], d[3]["runs"]) for d in plan.doubts] == [
              ("run-no-place", ["Smith, John. A Book of Things. London, 1990", "Jones, Kate. Another Book. Paris, 1991"])],
          f"the page net reads a page with no CJK character that lacks lines both engines read ({plan.doubts})")
    plan = run_pages({1: page_of("", a)})
    check([e.rule for e in plan.edits] == ["N2"] and not plan.doubts,
          f"a page sealed empty: its lines written, nothing left for the net ({plan.doubts})")
    # The page net leaves out the folio only where it stands alone on engine
    # A's first or last line, not a figure of the text equal to it.
    for a, want in (("甲乙丙丁戊己。見卷18之說，庚辛壬癸。", ["18"]), ("甲乙丙丁戊己。見卷之說，庚辛壬癸。\n18", [])):
        left = rn.unaligned([(page_of("甲乙丙丁戊己。見卷之說，庚辛壬癸。", a, folio={"18", "1", "8"}), [], [])])
        check(left == want, f"the page net and a figure equal to the folio: {a!r} -> {left}")
    # The pages of a table stitched across two pages (the seals'
    # tableStitch records: the head names the pages, the continuation its
    # head), through text_runs: a row moved to the first page stands there;
    # one taken out of it is named on the first page.
    head_a = "<table><tr><td>韶州</td><td>府城</td><td>1523</td><td>嘉靖2</td></tr></table>"
    cont_a = "<table><tr><td>韶州</td><td>縣城</td><td>1537</td><td>嘉靖16</td></tr></table>"
    records = {1: {"tableStitch": {"head": {"pages": [1, 2]}}}, 2: {"tableStitch": {"continuation": {"head": {"page": 1}}}}}
    whole = ("| 韶州 | 府城 | 1523 | 嘉靖2 |\n| --- | --- | --- | --- |\n<table><tr><td>韶州</td><td>縣城</td><td>1537</td>"
             "<td>嘉靖16</td></tr></table>\n")
    plan = run_pages({1: page_of(whole, head_a, head_a.replace("<", " <")), 2: page_of("", cont_a, scan=2)}, records)
    check(not [d for d in plan.doubts if d[0] == "run-no-place"],
          f"text_runs pools a stitched table's pages: the moved row stands on the first ({plan.doubts})")
    plan = run_pages({1: page_of("| 韶州 | 府城 | 1523 | 嘉靖2 |\n| --- | --- | --- | --- |\n", head_a),
                      2: page_of("", cont_a, scan=2)}, records)
    check([(d[0], d[1], d[3].get("runs")) for d in plan.doubts if d[0] == "run-no-place"] == [("run-no-place", 1, ["1537", "16"])]
          and "stitched over pages 1-2" in next(d[2] for d in plan.doubts if d[0] == "run-no-place"),
          f"text_runs names on the first page the runs of a row the stitched pages lack ({plan.doubts})")


def page_text(out: Path, scan: int) -> str:
    return (out / "pages" / f"page-{scan:04d}.md").read_text(encoding="utf-8")


def main() -> int:
    unit_checks()
    cell_checks()
    imprint_checks()
    missing_glyph_checks()
    one_engine_checks()
    agreed_checks()
    footnote_checks()
    dropped_checks()
    bibliography_checks()
    footnote_guard_checks()
    note_call_checks()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = make_spec()
        book = fx.write_book(tmp / "book", spec)
        out = tmp / "out"
        status = run(str(book), "--output", str(out), *OTHER_STAGES_OFF)
        report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
        check(status == 0 and report["checks"]["logReplay"]["passed"],
              f"the run exits 0 and its log replays to its output (exit {status})")
        check(report["stages"]["runs"]["status"] == "ran"
              and report["stages"]["runs"]["rules"] == ["R1", "N1", "N5", "N8", "N6", "N7", "N2"],
              "the runs stage ran and names its rules")
        records = re_.read_log((out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines())
        by_rule = {}
        for r in records:
            by_rule.setdefault(r["rule"], []).append(r)
        doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
        kinds = {(d["kind"], d["page"]) for d in doubts}

        p6 = page_text(out, 6)
        texts6 = paragraphs(spec[6])
        check(texts6[0][1:3] + "1989" + texts6[0][3:5] in p6, "N1 T1: a year both engines read is written between its characters")
        check(texts6[1][1:3] + "（G. W. Skinner）" + texts6[1][3:5] in p6,
              "N1 T1: a bracketed Latin name goes into the page's empty bracket pair, spaces kept")
        check(texts6[2][1:3] + "（1522-66）" + texts6[2][3:5] in p6,
              "N1 T2: a figure goes in beside the one bracket the page has, with the bracket it lacks")
        check(texts6[0][9:11] + "，1996）" + texts6[0][11:13] in p6,
              "N1 T2: the comma both engines read before a year is written with it")
        n1 = [r for r in by_rule.get("N1", []) if r["page"] == 6]
        check(texts6[1][9:11] + "7" + texts6[1][11:13] in p6 and "〈" not in p6
              and any(r["evidence"].get("withheld") == ["〈"] for r in n1),
              "N1 T2: a bracket with no partner on its line is withheld, its figure written")
        check(len(n1) == 5 and sorted(r["evidence"]["tier"] for r in n1) == ["T1", "T1", "T2", "T2", "T2"],
              f"N1: five edits on scan 6, two T1 and three T2 ({[r['evidence']['tier'] for r in n1]})")
        check(all(pp.CJK(page_text(out, s)) == pp.CJK(book.joinpath('proofread', f'page-{s:04d}.md').read_text(encoding='utf-8'))
                  for s in (6, 8, 9, 10, 12, 14, 15, 17, 18)), "no CJK character changes on any page")

        p8 = page_text(out, 8)
        check("1989" not in p8 and "1980" not in p8, "left alone: a figure the engines read differently")
        check(("run-engines-differ", 8) in kinds, "`run-engines-differ` is recorded for it")
        check(("run-one-engine", 8) in kinds, "`run-one-engine` is recorded for the figure engine A alone read")
        check(("note-markers-unverified", 8) in kinds and "⑫" not in p8,
              "a figure beside a note marker is not written; the page's markers are one doubt")
        check("\n\nHobsbawm, E. J. Bandits. London: Weidenfeld, 1969.\n\n" in p8
              and any(r["page"] == 8 for r in by_rule.get("N2", [])),
              "N2: a whole line of Western text goes in as a paragraph, one engine's emphasis markers aside")
        p17 = page_text(out, 17)
        moved = [r for r in by_rule.get("N1", []) if r["page"] == 17]
        check("| 南安 | 嘉靖3 |" in p17 and len(moved) == 1 and moved[0]["evidence"].get("movedFrom") == 18
              and "嘉靖3" not in page_text(out, 18),
              f"N1: a figure of a row moved to the page before is written there ({p17.splitlines()[-1]!r})")
        p10 = page_text(out, 10)
        check(p10.count("1989") == 1 and not [r for r in by_rule.get("N1", []) if r["page"] == 10],
              "a figure the page already holds is left as it is")
        check("18" not in p10 and ("run-no-place", 10) in kinds,
              "a figure that opens a line in engine A is not written (`run-no-place`)")
        check("1980" in p10 and any(d["kind"] == "run-no-place" and d["page"] == 10 and d.get("pageRuns") == ["1980"]
                                   for d in doubts),
              "a figure the page holds in another form is left, with a `run-no-place` doubt naming both")

        texts11 = paragraphs(spec[11])
        check(texts11[0][1:3] + "336。" + texts11[0][3:5] in page_text(out, 11) and ("run-mark-dropped", 11) in kinds,
              "N6: the page's closing bracket after a page reference goes, and the stage's doubt names it")
        p12 = page_text(out, 12)
        texts12 = paragraphs(spec[12])
        check(texts12[0][1:3] + "（1522-66）" + texts12[0][3:5] in p12 and any(r["page"] == 12 for r in by_rule.get("N6", [])),
              "N6: `）（` becomes the engines' `（1522-66）`")
        check("| 贛州 | 1496 | 弘治9 |" in p12, f"N1: a table row's empty cell and short cell get their figures ({p12.splitlines()[-1]!r})")

        p9 = page_text(out, 9)
        n5 = [r for r in by_rule.get("N5", []) if r["page"] == 9]
        check("| 贛州 | 城砌以石，高18尺。 | 康熙《上猶縣志》3/2a-b。 |" in p9 and sorted(r["after"] for r in n5) == sorted(["，", "。 | ", "《"]),
              f"N5: a table row's cells get the marks both engines read, the title marks as a pair ({p9.splitlines()[-2]!r})")
        check(not any(r["evidence"].get("withheld") for r in records if r["page"] == 9)
              and sum(1 for r in by_rule.get("N1", []) if r["page"] == 9) == 2,
              "N5: the closing title mark N1 writes with its figure finds the opening one N5 wrote")
        check("| 汀州 | 平亂建鎮 | 明史 |" in p9
              and [(d["kind"], d.get("marks"), d.get("withheld")) for d in doubts if d["page"] == 9] == [("run-no-place", 1, 1)],
              "N5: marks the engines read differently are not written, with no doubt; the bracket with no partner is "
              "withheld and is the page's one doubt")
        text9 = paragraphs(spec[9])[0]
        check(text9[1:3] + "，" + text9[3:5] in p9 and [r["after"] for r in by_rule.get("N8", []) if r["page"] == 9] == ["，"],
              "N8: the same comma both engines read in running text is written (not N5's)")
        # The page keeps a figure's comma without the figure: N1 writes the
        # figure with it, and the forms stage, after the runs, leaves the
        # comma between its figures ASCII; the ASCII comma the punctuator
        # wrote beside a character becomes full width.
        text16 = paragraphs(spec[16])[0]
        check(text16[1:3] + "7,920" + text16[3:5] in page_text(out, 16)
              and text16[9:11] + "，" + text16[11:13] in page_text(out, 16)
              and [r["rule"] for r in records if r["page"] == 16] == ["N1", "K9"],
              f"forms after runs: a figure's comma the page kept is written in the figure ASCII (N1), a comma beside "
              f"characters full width (K9) ({[(r['rule'], r['after']) for r in records if r['page'] == 16]})")
        p14 = page_text(out, 14)
        moved = [r for r in by_rule.get("N5", []) if r["page"] == 14]
        check("| 南安 | 城圮，官修。議出公帑。 |\n| 《汀州》 | 動支河稅。 |" in p14
              and len(moved) == 6 and all(r["evidence"].get("movedFrom") == 15 for r in moved)
              and "城圮" not in page_text(out, 15),
              f"N5: the marks of rows moved to the page before are written there, either side of the rule the move dropped "
              f"({p14.splitlines()[-2:]!r})")
        first = paragraphs(spec[14])[0]
        check(first[8:16] in p14 and first[8:15] + "。" not in p14 and any(r["page"] == 14 and r["rule"] == "R1" for r in records),
              "R1: the kept retry's 。 becomes the first answer's ， where engine A has it")
        second = paragraphs(spec[14])[1]
        check(second[:45] in p14 and len([r for r in records if r["page"] == 14 and r["rule"] == "R1"]) == 1,
              "R1: a first answer that differs from engine A changes nothing")

        status = run(str(book), "--output", str(tmp / "off"), "--no-runs", "--no-forms", *OTHER_STAGES_OFF)
        report = json.loads((tmp / "off" / "repair-report.json").read_text(encoding="utf-8"))
        check(status == 0 and report["stages"]["runs"]["status"] == "off (--no-runs)"
              and report["stages"]["forms"]["status"] == "off (--no-forms)" and report["counts"]["edits"] == 0,
              "--no-runs switches the stage off (and --no-forms the forms stage)")

        # The rows moved to scan 14, one of them changed there: the marks of
        # that row find no place, and are the page's one doubt.
        spec_moved = make_spec()
        spec_moved[14]["extraSealed"] = [spec_moved[14]["extraSealed"][0].replace("| 汀州 | 動支河稅 |", "| 汀州 | 動支河稅銀 |")]
        book_moved = fx.write_book(tmp / "book-moved", spec_moved)
        status = run(str(book_moved), "--output", str(tmp / "out-moved"), *OTHER_STAGES_OFF)
        doubts_moved = json.loads((tmp / "out-moved" / "doubts.json").read_text(encoding="utf-8"))
        p14 = page_text(tmp / "out-moved", 14)
        moved = [(d["kind"], d.get("movedTo"), d.get("marks"), d.get("gaps")) for d in doubts_moved if d["page"] == 15]
        check(status == 0 and "| 南安 | 城圮，官修。議出公帑 |\n| 汀州 | 動支河稅銀 |" in p14 and moved == [("run-no-place", 14, 4, 3)],
              f"N5: marks of a moved row that no longer stands on the host unchanged are not written (the full stop before "
              f"the row goes with the bracket after it: one edit, all or none), and are one doubt of the page they were "
              f"read on, with their count ({moved}, {p14.splitlines()[-2:]!r})")
    print("\nFAILURES:" if FAILURES else "\nall checks passed")
    for failure in FAILURES:
        print(" -", failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
