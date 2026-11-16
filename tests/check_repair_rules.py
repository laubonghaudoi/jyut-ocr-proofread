#!/usr/bin/env python3
"""Offline check of the book repair tool's mechanical rules
(scripts/_repair_rules.py) through scripts/repair_book.py: band text and
answers at band spots (F1, F6, F8), engine B's stand-ins and vertical
brackets (K1, K5, K9, K-seq), the marks of headings (K11), the blank line
around a heading (P5), and the script of 裁決's answers (V1).

No model, no GPU, no corpus: made-up books (tests/repair_fixture.py), each
page changed only where a rule's case is set up.  Every rule has a case it
must repair and a look-alike it must leave:

Furniture (a book whose running head is 青山文叢 with its run's suffix, the
folio under it):
- F1 (band text): a head line (with a stray numeral, a misread character
  and the folio) is deleted; a line of the body that reads like the head,
  which engine A read in the body, stays; so do the title page's title (the
  stem inside a longer title, on a page that prints no running head) and the
  first contents page's title.
- F1: the page's own suffix alone is deleted; the divider's suffix, printed
  large, stays.  The contents word alone on the second contents page is
  deleted.
- F1: a numeral line at the page's foot that is the folio engine A read in
  the band is deleted; a numeral line engine A read in the body, and one
  that is no piece of the folio, stay.
- F1: the head engine B read glued to the end of a paragraph is deleted
  there and the paragraph kept.
- F6: an adjudicator's answer at the band where engine A read nothing and
  engine B the folio is deleted; the same answer where engine B read a
  numeral that is not the folio stays.  F1: a leading line of such an answer
  and band characters is deleted.
- F1 also where engine A glued the head, suffix and folio to a body block.
- F8: a character both engines read at a page's head (engine A as another
  glyph) or foot, which the page lost, is one `TEXT-LOST` doubt at the
  page's edge naming both engines' readings; nothing is written.
- a book whose band gate fails (no folio anywhere) changes nothing.
- look-alikes on pages that print a running head, each kept: a title
  holding the stem and suffix, a paragraph opening with the book's name, the
  book named in the prose (a doubt where engine B alone read it), a line
  ending with the suffix before a head line F1 deletes, a head-like heading and a
  folio-like section numeral engine A read in one block with their
  paragraph, a numeral line that is the folio by the sequence but that no
  engine read in the band, a printed 一 that is a piece of the folio (a
  doubt), a head character engine B read, and a character engine A read
  and the adjudicator answered; on the title page (no running head) the
  title, the title with more after it and an answer below it; on the first
  contents page its title (the contents word) and an answer below it.

Marks (a book where engine B writes 丁 for engine A's bracketed figures,
乛 for its corner quotes, 丨 for its dashes and 丶 for its dots, besides 丶
and 口 for its commas and full stops):
- K1 writes （1） over 丁）（; a figure the page already holds, and a
  printed ）（ engine A read, stay; a figure that does not go on from the
  one before it (K-seq) is a doubt, not an edit; so is a character before
  a figure that stands for no mark engine A read there.
- K1 also writes a heading's figure over its stand-in, a dash over 丨, a
  dot over 丶, and 上 over a stand-in engine A never reads; K5 deletes 丶
  beside a dot already written, keeps one 口 of 口口 with engine A's mark,
  and names the site where engine B read a doubled 口 besides its full
  stop; K5 deletes a quote glyph beside a written quote when the seal shows
  engine B alone put it there, and keeps it without that record; engine
  A's closing quote where the page opens none is a doubt (K1); K9 writes
  ︵︶ as （）.
- guards: a glyph twice where engine A read the dash and one copy (K1), a
  figure read without the dot its neighbours carry (K-seq), a 一 of the
  text engine A read as the figure 1 (K-seq: a 1 the page holds as a CJK
  numeral needs the 2 after it) are left; a heading figure that goes on
  from nothing but is the contents' number is written (K-seq).
- K1 writes （4） over the numeral 四 engine B read for it, on a book where
  engine B was seen to write numerals for figures (M-7b).
- a book whose engine B stand-ins are not learned (none, and no numerals
  read for figures) changes no mark.
- on made-up pages, K1 writes the dash where its stand-ins stand, before a
  closing quote and a paragraph break; a page mark engine A did not read,
  or a figure engine A read, beside the dash's stand-in is a doubt, but in
  a heading the dash goes in with engine A's marks; a dot's stand-in where
  engine A read a figure with the dot is left; so is a bracketed figure the
  page holds, in any digits, before a bracket or a stand-in (its closing
  bracket and the next one, on the next line or the same, are no stand-in
  pair).  A bracketed figure engine A ends its line with goes ahead of the
  page's line break, with engine A's marks before its own break; one that
  opens engine A's line goes after it.  Where the page holds nothing there
  (phase 3 writes no bracket pair alone since #23) K1 writes the figure,
  unless the page holds a figure there; engine A's ASCII-dot leader is
  written 「……」 (not after the page's own).
- K11, on headings as four books sealed them, with both engines' readings
  (天地会 2 scans 43 and 262, 天地会 3 scans 108 and 66, 盜區政區 scan
  398, 陳總司令言論集 scan 133, checked against the print): the comma the
  punctuation pass put where a title's two printed lines meet (engine A's
  line break) and the comma it put at the title's end, where neither
  engine read a mark, are deleted, and so is the `<br>` the formatter
  wrote where the lines meet; where engine A read a space on its line, the
  comma becomes that space; a mark a heading opens with after a line that
  ends with its own full stop (made up from scan 108's title) goes, that
  full stop stays; a full stop and a comma neither engine read inside a
  title engine A read on one line go too (scans 262 and 108, specks in
  the print; two marks and a `<br>` in one title, and a comma on scan 43's
  second line, made up).  Left with one `heading-mark-unread` doubt a
  heading, naming its marks: the full stop printed in a title engine A read
  as a column, a character a line (scan 133), and a mark a heading opens
  or ends with beside a column, on either side (made up).  Left without one: a colon engine A read at a heading's end, a comma only
  engine A or only engine B read, a `<br>` where engine B read a glyph
  between the two lines, a comma beside a figure (made up from 盜區政區
  scan 268's table title), a comma neither engine read in a paragraph (not
  a heading), and a date's marks after its numerals, the last one at the
  heading's end too (陳總司令言論集 scan 430, printed down a column that
  engine A read a character a line); and the page's 、 or ， where both
  engines read another mark of CJK print there (· “” 〔, made up).

Spacing: P5 puts a blank line after a heading that has none, and leaves a
heading engine A read in one block with the line after it - unless a date
under the heading ends that block.

Forms (made-up pages through the forms stage's rules): K9 writes an ASCII
comma and brackets beside CJK characters full width, both brackets of a
pair on one line with a CJK character inside or right outside (round
figures, opening or closing with a figure, holding Western text), a
bracket after a full-width mark (and the bracket of its pair with it), a
bracket with no partner on its line beside CJK print, a comma before an
ideographic space, brackets inside a table cell's tags, and a bracket at
the page's leading edge; it leaves a pair with no CJK character inside or
right outside, a Markdown link's target, math, a comma and a colon between
two figures, a comma between a figure and a space and a gap of Western
text.  K12 deletes bold and underscores neither engine read, at the page's
edges too (also where engine A's first or last character is not the
page's), on a page of no CJK character, and where engine A holds such a
run on another line; between two characters where engine A read a space,
it writes one; it leaves a line of the run alone (a rule) and underscores
the engines read full width (＿), and it escapes the stars of a run either
engine holds there (at an edge too) or elsewhere on the line, one the
engines read full width (＊), stars the engines read in a table's rows in
another order and a single star: a printed * is written \* (decision 8 of
2026-11-14).

Script (made-up pages through the script stage's rule, in a book whose
drafts are in simplified script): V1 writes 裁決's sealed 蕆 (天地会 1 scan
242: engine A 藏, engine B 葳) as its one simplified form 蒇, with an
`answer-script-converted` doubt; a 懸 sealed before phase 3 wrote the form
an engine read as engine B's 悬, with none; a 塵 so sealed as engine A's 尘
where engine B read 廑 (天地会 3 scan 181), with one; 蒇 where engine B read
nothing, with one too; with marks between the characters, at the page's
either edge, and once for two records of one spot; of an answer phase 3
wrote in an engine's form in part (懸蕆 sealed 悬蕆), the rest, doubted
alone.  It leaves a book printed in traditional script, an answer phase 3
did not write, a page phase 3 sealed as it now writes (蒇, and 薴 sealed
as its one form 苧, which is itself a key of the table: not 苎; with a
digit both engines read spliced round the answer, from the seal), a
character engine A read there, the character elsewhere on the page, a
place sharing three characters of the record's context, one whose
character on either side, inside the page, is not the record's, and two
places holding it.  Through repair_book.py on the made-up book in simplified
script, the stage writes and doubts one spot (which W1 then names as
written), an answer at the band (叢 of
the head) goes with the band (F6) before the stage runs, and --no-script
turns the stage off.

Every run replays its log to its output, and the rules run again on their
own output change nothing.

Usage:
    python3 tests/check_repair_rules.py
"""

from __future__ import annotations

from collections import Counter
import contextlib
import copy
import io
import json
from pathlib import Path
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
import _repair_inputs as ri  # noqa: E402
import _repair_model as rm  # noqa: E402
import _repair_rules as rr  # noqa: E402
import proofread_pages as pp  # noqa: E402
import repair_book  # noqa: E402


FAILURES: list[str] = []


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
    """Engine B's reading of printed text in the made-up book."""
    return text.replace("，", "丶").replace("。", "口")


def column(page: dict[str, Any]) -> tuple[int, str]:
    """The page's first body column: its index and its text."""
    columns = fx.layout(page)["columns"]
    k = next(i for i, c in enumerate(columns) if c["label"] == "vertical_text")
    return k, columns[k]["text"]


class Result:
    """What a run wrote: the exit status, the report, the log, the
    doubts, and the pages."""

    def __init__(self, status: int, out: Path, book: Path):
        self.status = status
        self.out = out
        self.report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
        self.log = [json.loads(line) for line in (out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
        self.pages = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in (out / "pages").glob("*.md")}
        self.sealed = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8")
                       for p in (book / "proofread").glob("page-*.md")}

    def rules(self, page: int) -> list[str]:
        return [r["rule"] for r in self.log if r["page"] == page]

    def named(self, page: int) -> list[str]:
        """The rules whose doubts name a site on PAGE."""
        return [d["rule"] for d in self.doubts if d["page"] == page and d.get("rule")]


# The structure stages (tests/check_repair_structure.py) are off here: these
# cases change only what the furniture, mark and spacing rules act on.
STRUCTURE_OFF = ("--no-contents", "--no-title-page", "--no-levels", "--no-joins")


def repair(root: Path, spec: dict[int, dict[str, Any]], name: str, *argv: str) -> Result:
    book = fx.write_book(root / name, spec)
    out = root / f"{name}-out"
    status = run(str(book), "--output", str(out), *STRUCTURE_OFF, *argv)
    return Result(status, out, book)


def again(root: Path, name: str, result: Result) -> list[re_.Edit]:
    """The furniture, mark and spacing rules run on RESULT's own pages (with
    the model of the book): what they would change."""
    book = ri.load_book(ri.InputPaths.resolve(root / name, {}))
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    order = list(book.assembly.order)
    ledger = re_.Ledger({scan: result.pages[scan] for scan in order})
    facts = rr.book_facts(book, model)
    edits = rr.furniture(book, model, ledger.texts, order, facts).edits
    ledger.apply("furniture", edits)
    marks = rr.marks(book, model, ledger.texts, order, facts).edits
    ledger.apply("marks", marks)
    return edits + marks + rr.spacing(book, model, ledger.texts, order).edits


# --- furniture -----------------------------------------------------------------

def furniture_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    head, digits = fx.HEAD, fx.numeral
    # F1: a head line at the foot of scan 6; one with a stray numeral, a
    # misread character and the folio on scan 8 (folio 4).
    spec[6]["extraSealed"] = [head + "甲編"]
    spec[8]["extraSealed"] = ["一" + head[0] + "丈" + head[2:] + "甲編" + digits(4)]
    # A line of the body of scan 10 that reads like the head: engine A reads
    # it in the body.
    spec[10]["items"].insert(1, ("line", head))
    # F1: the suffix alone on scan 12; the folio at the foot of scan 16
    # (folio 4); a numeral line that is no piece of the folio (九) at the
    # foot of scan 14 (folio 2), and one engine A read in its body.
    spec[12]["extraSealed"] = ["# 甲編"]
    spec[16]["extraSealed"] = [digits(4)]
    spec[14]["extraSealed"] = ["九"]
    spec[14]["items"].insert(1, ("line", "五"))
    # F1: the contents word alone on the second contents page.
    first = spec[3]["items"][0][1]
    spec[3]["sealedEdits"] = [(first, "# 目次\n\n" + first)]
    # F1 inline: engine B read the head at the end of scan 11 (headInB end); the
    # page holds it glued to its last paragraph, the folio before it.
    last = paragraphs(spec[11])[-1]
    spec[11]["sealedEdits"] = [(last, last + digits(7) + head + "甲編")]
    # F6: an answer at the band at the start of scan 15 (engine B reads the
    # head first: folio 3); the same answer where engine B read a numeral
    # that is no folio, on scan 17 (folio 7).
    for scan, engine_b in ((15, digits(3)), (17, "三一")):
        spec[scan]["headInB"] = "start"
        body = paragraphs(spec[scan])[0]
        spec[scan]["sealedEdits"] = [(body, "一" + body)]
        spec[scan]["adjudications"] = [{
            "context_before": head + "乙編", "context_after": fx.pp.CJK(body)[:8], "draft_reading": "",
            "writer_reading": engine_b, "resolved": "一", "resolved_from": "adjudicator",
            "trigger": "numeral-disagreement"}]
    # F1: a leading line of an answer at the band and band characters
    # (scan 19, folio 9: engine B reads the folio there).
    spec[19]["headInB"] = "start"
    body = paragraphs(spec[19])[0]
    spec[19]["sealedEdits"] = [(body, "丙乙編\n\n" + body)]
    spec[19]["adjudications"] = [{
        "context_before": head + "乙編", "context_after": fx.pp.CJK(body)[:8], "draft_reading": "",
        "writer_reading": digits(9), "resolved": "丙", "resolved_from": "adjudicator",
        "trigger": "engine-disagreement"}]
    # F1 inline where engine A glued the band - head, suffix and folio - to the end
    # of scan 18's last body column (folio 8), and the seal holds the head
    # and suffix glued to the last paragraph.
    last = paragraphs(spec[18])[-1]
    spec[18]["headInB"] = "end"
    spec[18]["aHeadless"] = True
    spec[18]["aEdits"] = [(last[-5:], last[-5:] + head + "乙編" + digits(8))]
    spec[18]["sealedEdits"] = [(last, last + head + "乙編")]
    return spec


def furniture_checks(tmp: Path) -> None:
    spec = furniture_spec()
    result = repair(tmp, spec, "furniture", "--no-marks", "--no-paragraphs")
    pages, sealed = result.pages, result.sealed
    head = fx.HEAD
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"the run exits 0 and its log replays to its output (exit {result.status})")
    check(result.rules(6) == ["F1"] and head + "甲編" not in pages[6] and pages[6].endswith("。\n"),
          f"F1: a head line at the page's foot is deleted, one separator left ({result.rules(6)})")
    check(result.rules(8) == ["F1"] and "丈" not in pages[8],
          f"F1: a head line with a stray numeral, a misread character and the folio is deleted ({result.rules(8)})")
    check(not result.rules(10) and "\n" + head + "\n" in pages[10],
          f"F1: a line of the body that reads like the head, which engine A read in the body, stays "
          f"({result.rules(10)})")
    check(not result.rules(1) and pages[1] == sealed[1] and not result.rules(2),
          "F1: the title page's title (the stem inside it, on a page with no running head) and the first "
          "contents page's title stay")
    check(result.rules(12) == ["F1"] and "甲編" not in pages[12], f"F1: the suffix alone is deleted ({result.rules(12)})")
    check(not result.rules(5) and pages[5] == sealed[5] and not result.rules(13),
          "F1: the dividers' suffix, printed large, stays")
    check(result.rules(3) == ["F1"] and "# 目次" not in pages[3],
          f"F1: the contents word alone on the second contents page is deleted ({result.rules(3)})")
    check(result.rules(16) == ["F1"] and not pages[16].rstrip().endswith(fx.numeral(4)),
          f"F1: the folio at the page's foot is deleted ({result.rules(16)})")
    check(not result.rules(14) and pages[14].rstrip().endswith("九") and "\n五\n" in pages[14],
          "F1: a numeral line that is no piece of the folio, and one engine A read in the body, stay")
    check(result.rules(11) == ["F1"] and head not in pages[11] and pages[11].rstrip().endswith("。"),
          f"F1: the head engine B read glued to a paragraph's end is deleted there, the paragraph kept "
          f"({result.rules(11)})")
    check(result.rules(18) == ["F1"] and head not in pages[18] and pages[18].rstrip().endswith("。"),
          f"F1: the head engine A glued (with its suffix and folio) to the end of a body block is deleted "
          f"({result.rules(18)})")
    first15 = paragraphs(spec[15])[0]
    check(result.rules(15) == ["F6"] and first15 in pages[15] and "一" + first15 not in pages[15],
          f"F6: the answer at the band is deleted ({result.rules(15)})")
    check(not result.rules(17) and "一" + paragraphs(spec[17])[0] in pages[17],
          "F6: the same answer where engine B read a numeral that is not the folio stays")
    check(result.rules(19) == ["F1"] and "丙乙編" not in pages[19],
          f"F1: a leading line of an answer and band characters is deleted ({result.rules(19)})")
    extra = sorted({r["page"] for r in result.log} - {3, 6, 8, 11, 12, 15, 16, 18, 19})
    check(not extra, f"no page but the cases' changes ({extra})")
    check(not again(tmp, "furniture", result), "the rules run on their own output change nothing")

    spec = furniture_spec()
    for page in spec.values():
        page["folio"] = None
    result = repair(tmp, spec, "no-band", "--no-marks", "--no-paragraphs")
    check(result.status == 0 and not result.log and result.report["gates"]["M-1"] is False,
          f"with the band's gate failed (no folio anywhere) the furniture rules change nothing "
          f"({[r['rule'] for r in result.log]})")
    lookalike_checks(tmp)


def set_paragraph(page: dict[str, Any], old: str, new: str) -> None:
    """The page prints NEW where it printed the paragraph OLD."""
    k = next(i for i, item in enumerate(page["items"]) if item[0] == "para" and item[1] == old)
    page["items"][k] = ("para", new, page["items"][k][2])


def lookalike_spec() -> dict[int, dict[str, Any]]:
    """Printed text that looks like the band, on pages that print a running
    head (a folio in the band): each must stay."""
    spec = fx.make_spec()
    head, digits = fx.HEAD, fx.numeral
    # F1 inline: a title holding the stem and the run's suffix at the top of scan 7
    # (engine B reads it before its head, at the start of its reading); a
    # paragraph opening scan 17 with the book's name (engine B reads its head
    # last); the book named in the prose of scan 8.
    spec[7]["items"].insert(0, ("heading", head + "甲編序", 3))
    body = paragraphs(spec[17])[0]
    set_paragraph(spec[17], body, head + "所收諸篇，" + body)
    body = paragraphs(spec[8])[1]
    set_paragraph(spec[8], body, body[:20] + "讀" + head + "，" + body[20:])
    # The same on scan 12, where engine A dropped the name: engine B alone
    # read it, inside its reading of the body.
    body = paragraphs(spec[12])[1]
    set_paragraph(spec[12], body, body[:20] + "讀" + head + "，" + body[20:])
    spec[12]["aEdits"] = [("讀" + head + "，", "讀，")]
    # A line of scan 9 that ends with the run's suffix (both engines read
    # it), just before a head line the seal holds.
    last = paragraphs(spec[9])[-1]
    set_paragraph(spec[9], last, last[:-1] + "，見甲編。")
    spec[9]["extraSealed"] = [head + "甲編"]
    spec[9]["headInB"] = "start"
    # F1: a heading that reads like the head (scan 10), and a section's
    # numeral that is the folio (scan 16, folio 4), each printed in its own
    # line, which engine A read in one block with the paragraph after it.
    for scan, lead in ((10, head + "甲編"), (16, digits(4))):
        body = paragraphs(spec[scan])[0]
        set_paragraph(spec[scan], body, lead + body)
        spec[scan]["sealedEdits"] = [(lead + body, lead + "\n\n" + body)]
    # F6: the first paragraph of scan 20 (folio 一〇) opens with a printed 一
    # and that of scan 19 (folio 9) with a printed 山, a character of the
    # head: engine A dropped them, engine B read them, and the adjudicator
    # answered them from the image.  On scan 15 engine A read a printed 山
    # where engine B read the folio, and the adjudicator answered 山.
    for scan, char, a_reads in ((20, "一", False), (19, "山", False), (15, "山", True)):
        spec[scan]["headInB"] = "start"
        body = paragraphs(spec[scan])[0]
        set_paragraph(spec[scan], body, char + body)
        if a_reads:
            spec[scan]["bEdits"] = [(char + b_form(body[:5]), digits(spec[scan]["folio"]) + b_form(body[:5]))]
        else:
            spec[scan]["aEdits"] = [(char + body[:6], body[:6])]
        spec[scan]["adjudications"] = [{
            "context_before": head + "乙編", "context_after": fx.pp.CJK(body)[:8],
            "draft_reading": char if a_reads else "", "writer_reading": digits(spec[scan]["folio"]) if a_reads else char,
            "resolved": char, "resolved_from": "adjudicator", "trigger": "engine-disagreement"}]
    # The title page prints no running head: its lines are the book's title
    # with more after it (F1 inline), the title alone (F1), and an author's line
    # whose first character neither engine read and the adjudicator answered
    # below the title (F6).  Engine A read none of the title's lines in its
    # body.
    spec[1]["items"] = [("line", head + "全集"), ("heading", head, 1), ("line", "林泉撰")]
    spec[1]["aEdits"] = [(head + "全集", ""), (head, ""), ("林泉撰", "泉撰")]
    spec[1]["bEdits"] = [("林泉撰", "泉撰")]
    spec[1]["adjudications"] = [{
        "context_before": head, "context_after": "泉撰", "draft_reading": "", "writer_reading": "",
        "resolved": "林", "resolved_from": "adjudicator", "trigger": "engine-disagreement"}]
    # The first contents page prints its title as the contents word alone
    # (engine A did not read it in its body: F1), and an adjudicator's answer
    # below it that neither engine read (F6).
    spec[2]["sealedEdits"] = [("# " + head + fx.CONTENTS_WORD, "# " + fx.CONTENTS_WORD)]
    spec[2]["aEdits"] = [(head + fx.CONTENTS_WORD, ""), ("甲編 詩文", "編 詩文")]
    spec[2]["bEdits"] = [("甲編 詩文", "編 詩文")]
    spec[2]["adjudications"] = [{
        "context_before": fx.CONTENTS_WORD, "context_after": "編詩文", "draft_reading": "", "writer_reading": "",
        "resolved": "甲", "resolved_from": "adjudicator", "trigger": "engine-disagreement"}]
    # F1: scan 14 (folio 2) opens with a printed 二 engine A did not read;
    # no engine read the folio in the band (engine A misread its folio block,
    # engine B read no head).
    spec[14]["items"].insert(0, ("line", "二"))
    spec[14]["aEdits"] = [("二", "")]
    spec[14]["aFolio"] = "工"
    spec[14]["bHeadless"] = True
    return spec


def lookalike_checks(tmp: Path) -> None:
    spec = lookalike_spec()
    result = repair(tmp, spec, "lookalikes", "--no-marks", "--no-paragraphs")
    pages, sealed = result.pages, result.sealed
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"look-alikes: the run exits 0 and its log replays to its output (exit {result.status})")
    check(not result.rules(7) and pages[7] == sealed[7],
          f"F1: a title holding the stem and the suffix, which both engines read, stays ({result.rules(7)})")
    check(not result.rules(17) and pages[17] == sealed[17],
          f"F1: a paragraph opening with the book's name, which both engines read, stays ({result.rules(17)})")
    check(not result.rules(8) and pages[8] == sealed[8],
          f"F1: the book named in the prose, which both engines read, stays ({result.rules(8)})")
    check(not result.rules(12) and pages[12] == sealed[12] and "F1" in result.named(12),
          f"F1: the book named in the prose, which engine B alone read inside the body, stays and is a doubt "
          f"({result.rules(12)}, named {result.named(12)})")
    check(result.rules(9) == ["F1"] and "見甲編。" in pages[9],
          f"F1: a line that ends with the suffix both engines read stays before the head line F1 deletes "
          f"({result.rules(9)})")
    check(not result.rules(10) and pages[10] == sealed[10],
          f"F1: a heading that reads like the head, which engine A read in one block with its paragraph, stays "
          f"({result.rules(10)})")
    check(not result.rules(16) and pages[16] == sealed[16],
          f"F1: a section numeral that is the folio, which engine A read in one block with its paragraph, stays "
          f"({result.rules(16)})")
    check(not result.rules(20) and pages[20] == sealed[20] and "F6" in result.named(20),
          f"F6: a printed 一 that engine B read, a piece of the folio 一〇, stays and is a doubt "
          f"({result.rules(20)}, named {result.named(20)})")
    check(not result.rules(19) and pages[19] == sealed[19],
          f"F6: a printed character of the head that engine B read stays ({result.rules(19)})")
    check(not result.rules(15) and pages[15] == sealed[15],
          f"F6: a character engine A read and the adjudicator answered stays ({result.rules(15)})")
    check(not result.rules(1) and pages[1] == sealed[1],
          f"F1/F6: the title page's title, the title with more after it, and an answer below it stay on a page "
          f"that prints no running head ({result.rules(1)})")
    check(not result.rules(2) and pages[2] == sealed[2],
          f"F1/F6: the first contents page's title (the contents word) and an answer below it stay "
          f"({result.rules(2)})")
    check(not result.rules(14) and pages[14] == sealed[14],
          f"F1: a numeral line that is the folio by the sequence, which no engine read in the band, stays "
          f"({result.rules(14)})")
    extra = sorted({r["page"] for r in result.log} - {9})
    check(not extra, f"look-alikes: no page but the head line's changes ({extra})")


def edge_checks(tmp: Path) -> None:
    """F8: the first character of a page's body, which both engines read
    (engine A as another glyph), and the last before its full stop, lost
    from the page: each a `TEXT-LOST` doubt at the edge; the page is left."""
    spec = fx.make_spec()
    skip = set(fx.HEAD) | set("甲編乙編目次") | set(fx.DIGITS)
    head_scan = next(scan for scan in (8, 10, 12, 14) if paragraphs(spec[scan])[0][0] not in skip)
    first = paragraphs(spec[head_scan])[0]
    lost = first[0]
    spec[head_scan]["sealedEdits"] = [(first[:4], first[1:4])]
    spec[head_scan]["aEdits"] = [(first[:4], "龍" + first[1:4])]
    foot_scan = 16
    last = paragraphs(spec[foot_scan])[-1]
    spec[foot_scan]["sealedEdits"] = [(last[-4:], last[-4:-2] + "。")]
    result = repair(tmp, spec, "edges", "--no-marks", "--no-paragraphs")
    found = [(d["page"], d.get("edge"), d.get("engineA"), d.get("engineB")) for d in result.doubts
             if d["kind"] == "TEXT-LOST" and d.get("rule") == "F8"]
    check(result.status == 0 and found == [(head_scan, "head", "龍", lost), (foot_scan, "foot", last[-2], last[-2])],
          f"F8: a character both engines read at a page's head or foot, lost from the page, is one `TEXT-LOST` "
          f"doubt at that edge with both engines' readings ({found})")
    check(result.pages[head_scan] == result.sealed[head_scan] and result.pages[foot_scan] == result.sealed[foot_scan],
          "F8: nothing is written at the edges (no guess)")


# --- marks and spacing ---------------------------------------------------------

def edit_column(spec: dict[int, dict[str, Any]], scan: int, cut: int, a_text: str, sealed: str | None = None,
                b_text: str | None = None, end: int | None = None) -> None:
    """At character CUT of scan SCAN's first body column (to END, replaced):
    engine A reads A_TEXT there, the page holds SEALED (engine A's reading
    when None: the figure or mark is printed and written), engine B reads
    B_TEXT (nothing when None)."""
    page = spec[scan]
    k, text = column(page)
    end = cut if end is None else end
    marks = dict(page.get("aMarks") or {})
    current = marks.get(k, text)
    offset = len(current) - len(text)
    marks[k] = current[:cut + offset] + a_text + current[end + offset:]
    page["aMarks"] = marks
    # The page's own text is found by the four characters before the place.
    lead = text[max(0, cut - 4):cut]
    if sealed is not None:
        page.setdefault("sealedEdits", []).append((lead + text[cut:end], lead + sealed))
    if b_text is not None:
        page.setdefault("bEdits", []).append((b_form(lead + text[cut:end]), b_form(lead) + b_text))


def marks_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    # What engine B writes on this book, on enough pages for M-7 to learn:
    # 丁 for engine A's bracketed figures (between two 一), 丨 for a dash,
    # 丶 for a dot, 乛 for corner quotes.
    for scan, number, sealed in ((6, 1, "丁）（"), (8, 2, "（2）"), (10, 3, "丁）（"), (14, 9, "丁）（")):
        edit_column(spec, scan, 3, f"（{number}）", sealed, "一丁一")
    for scan, sealed in ((7, "丨"), (9, None), (11, None)):
        edit_column(spec, scan, 5, "——", sealed, "丨")
    for scan, sealed in ((15, "丶"), (18, None)):
        edit_column(spec, scan, 4, "·", sealed, "丶")
    for scan in (16, 17, 19):
        k, text = column(spec[scan])
        edit_column(spec, scan, 6, "「" + text[6:9] + "」", "「乛" + text[6:9] + "」" if scan in (16, 17) else None,
                    "乛" + b_form(text[6:9]) + "乛", end=9)
    # K5's record: engine B alone put 乛 there (a question engine A read
    # nothing for) on scan 16; scan 17 has no such record.
    k, text = column(spec[16])
    spec[16]["adjudications"] = [{"context_before": fx.pp.CJK(text[:6])[-8:], "context_after": text[6:9],
                                  "draft_reading": "", "writer_reading": "乛", "resolved": "乛",
                                  "resolved_from": "engine-A-default", "trigger": "engine-disagreement"}]
    # K1 refused: on scan 8 engine A read 「…」 but the page holds only the
    # glyph engine B wrote for the closing quote, with the seal's record;
    # engine A's 」 there would close nothing the page opens.
    k, text = column(spec[8])
    edit_column(spec, 8, 10, "「" + text[10:13] + "」", text[10:13] + "乛", "乛" + b_form(text[10:13]) + "乛", end=13)
    spec[8]["adjudications"] = [{"context_before": fx.pp.CJK(text[:13])[-8:], "context_after": fx.pp.CJK(text[13:])[:8],
                                 "draft_reading": "", "writer_reading": "乛", "resolved": "乛",
                                 "resolved_from": "engine-A-default", "trigger": "engine-disagreement"}]
    # K1: a heading whose figure the page holds as a stand-in.
    heading = next(item[1] for item in spec[9]["items"] if item[0] == "heading")
    spec[9]["sealedEdits"] = [("### " + heading, "### 丁" + heading.split(" ", 1)[1])]
    # K5: 口 written twice where engine A read it once, before its full stop.
    edit_column(spec, 20, 6, "口。", "口口。", "口口")
    # A printed doubled 口 after a full stop (scan 4): engine A read one,
    # engine B both and a third for the full stop.
    edit_column(spec, 4, 10, "。口", "。口口", "口口口")
    # A dot the page already holds beside engine B's glyph for it (scan 4).
    edit_column(spec, 4, 30, "•", "·丶", "丶")
    # A printed 口 (engine B's glyph for a full stop too) just before a
    # bracketed figure (scan 13): engine A dropped it, engine B read it.
    edit_column(spec, 13, 8, "（1）", "口（1）", "口一丁一")
    # K1: a stand-in engine A never reads, where engine A read 上.
    edit_column(spec, 12, 8, "上", "丁", "丁")
    # A printed ）（ engine A read, before a bracketed figure.
    edit_column(spec, 12, 3, "）（5）", "）（5）")
    # The page holds the figure and engine B's stand-in before it; and a
    # note marker before a stray bracket and the figure, which engine A
    # wrote in a LaTeX wrapper.
    edit_column(spec, 12, 20, "（6）", "丁（6）", "一丁一")
    edit_column(spec, 12, 28, "$ ^{㉑} $（7）", "㉑）（7）")
    # K9.
    k, text = column(spec[17])
    spec[17]["sealedEdits"] = spec[17].get("sealedEdits", []) + [(text[10:14], "︵" + text[10:13] + "︶" + text[13])]
    # P5: a heading with no blank line after it; a heading printed over two
    # lines, engine A's one block.
    heading = next(item[1] for item in spec[5]["items"] if item[0] == "heading")
    spec[5]["sealedEdits"] = [("### " + heading + "\n\n", "### " + heading + "\n")]
    heading = next(item[1] for item in spec[11]["items"] if item[0] == "heading")
    spec[11]["sealedEdits"] = [("### " + heading, "### " + heading[:-2] + "\n" + heading[-2:])]
    # A heading engine A read in one block with the date printed under it
    # (scan 7): the date ends the heading's block, so the blank line goes in.
    k = next(i for i, item in enumerate(spec[7]["items"]) if item[0] == "heading")
    heading = spec[7]["items"][k][1]
    spec[7]["items"][k] = ("heading", heading + "十七年五月", spec[7]["items"][k][2])
    spec[7]["sealedEdits"] = spec[7].get("sealedEdits", []) + [
        ("### " + heading + "十七年五月", "### " + heading + "\n十七年五月")]
    # K1: engine A read the dash, then the glyph as a character (engine B
    # the glyph once); the page holds it twice (scan 4): which copy is the
    # dash is a reading.
    edit_column(spec, 4, 36, "——丨", "丨丨", "丨")
    # K-seq: a list whose figures carry a dot (scan 19), the middle one read
    # by engine A without it where the page holds a stand-in.
    for n, words in ((1, "春風吹"), (2, "小橋流"), (3, "水人家")):
        spec[19]["items"].insert(n, ("line", f"{n}.{words}"))
    spec[19]["aEdits"] = [("2.小橋流", "2小橋流")]
    spec[19]["sealedEdits"] = spec[19].get("sealedEdits", []) + [("2.小橋流", "丁小橋流")]
    # Engine B reads engine A's （5） as the numeral 五 on three pages, the
    # page holding （5）: M-7b sees engine B write numerals for figures.
    for scan in (15, 16, 20):
        edit_column(spec, scan, 30, "（5）", "（5）", "五")
    # K1 over a numeral: the page holds 四）（ where engine A read （4）
    # (scan 11), engine B the numeral between its bracket strokes.
    edit_column(spec, 11, 20, "（4）", "四）（", "一四一")
    # K-seq for a figure the page holds in CJK numerals: a line printed
    # 一般原則 that engine A read 1般原則 (scan 16), and a list on scan 17
    # whose first figure the page holds as 一, with 2 after it.
    spec[16]["items"].insert(1, ("line", "一般原則"))
    spec[16]["aEdits"] = [("一般原則", "1般原則")]
    spec[17]["items"][1:1] = [("line", "1.漁舟唱"), ("line", "2.竹籬茅")]
    spec[17]["sealedEdits"] = spec[17].get("sealedEdits", []) + [("1.漁舟唱", "一漁舟唱")]
    # K-seq by the contents: engine A misread scan 15's heading figure (7 for
    # 2), so scan 18's figure 3 goes on from nothing; the contents give 3.
    spec[15]["aEdits"] = [("2 田園小記", "7 田園小記")]
    spec[18]["sealedEdits"] = spec[18].get("sealedEdits", []) + [("### 3 松下問答", "### 丁松下問答")]
    return spec


def marks_checks(tmp: Path) -> None:
    spec = marks_spec()
    result = repair(tmp, spec, "marks", "--no-furniture")
    pages = result.pages
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    standins = model["standIns"]
    check("丁" in standins["figure"] and "丨" in standins["dash"] and "丶" in standins["dot"]
          and "乛" in standins["markAliases"]["B"],
          f"M-7 learns the made-up book's stand-ins ({standins['figure']}, {standins['dash']}, {standins['dot']}, "
          f"{sorted(standins['markAliases']['B'])})")
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"the run exits 0 and its log replays to its output (exit {result.status})")
    k, text = column(spec[6])
    check("K1" in result.rules(6) and text[:3] + "（1）" + text[3:6] in pages[6] and "丁" not in pages[6],
          f"K1: （1） over the stand-ins ({result.rules(6)})")
    check(not result.rules(8) and "（2）" in pages[8], "K1: a figure the page already holds stays")
    check("K1" not in result.rules(13) and "口（1）" in pages[13] and "K1" in result.named(13),
          f"K1: a character before the figure that stands for no mark engine A read there stays, and is a "
          f"doubt ({result.rules(13)}, named {result.named(13)})")
    check("K1" in result.named(8) and "乛" in pages[8],
          f"K1: engine A's closing quote where the page opens none is a doubt, not an edit "
          f"(named {result.named(8)})")
    check("K1" in result.rules(10), f"K1: （3） follows （2） ({result.rules(10)})")
    k, text = column(spec[12])
    check(text[16:20] + "（6）" in pages[12] and "丁（6）" not in pages[12],
          f"K1: the stand-in before a figure the page already holds goes ({result.rules(12)})")
    check(text[24:28] + "㉑（7）" in pages[12] and "$" not in pages[12],
          "K1: a stray bracket after the page's note marker goes; engine A's wrapper and its marks are not written again")
    check("K1" in result.rules(11) and "（4）" in pages[11] and "四）（" not in pages[11],
          f"K1: （4） over the numeral engine B read for it, on a book where engine B writes numerals for "
          f"figures ({result.rules(11)})")
    check("K1" not in result.rules(14) and "丁）（" in pages[14] and "K-seq" in result.named(14),
          f"K-seq: （9） after （3） is a doubt, not an edit ({result.rules(14)}, named {result.named(14)})")
    check("K1" in result.rules(7) and "——" in pages[7] and "丨" not in pages[7], f"K1: —— over 丨 ({result.rules(7)})")
    check("K1" in result.rules(15) and "·" in pages[15], f"K1: the dot over 丶 ({result.rules(15)})")
    check("K5" in result.rules(4) and "··" not in pages[4] and "·" in pages[4] and "丶" not in pages[4],
          f"K5: the glyph beside a dot the page already holds goes, and no second dot is written "
          f"({result.rules(4)})")
    check("K5" in result.rules(16) and "乛" not in pages[16], f"K5: the quote glyph beside a written quote, which "
          f"the seal shows engine B alone put there, is deleted ({result.rules(16)})")
    check("K5" not in result.rules(17) and "乛" in pages[17],
          "K5: the same glyph without that record stays")
    heading = next(item[1] for item in spec[9]["items"] if item[0] == "heading")
    check("K1" in result.rules(9) and "### " + heading in pages[9], f"K1: the heading's figure ({result.rules(9)})")
    check("K5" in result.rules(20) and "口口" not in pages[20], f"K5: one 口 of two ({result.rules(20)})")
    touched = [r["before"] for r in result.log if r["page"] == 4 and "口" in r["before"]]
    check(not touched and "。口口" in pages[4] and "K5" in result.named(4),
          f"K5: a doubled 口 engine B read twice besides the full stop stays, and is a doubt "
          f"({touched}, named {result.named(4)})")
    check("K1" in result.rules(12) and "丁" not in pages[12] and "上" in pages[12] and "）（5）" in pages[12],
          f"K1: 上 over a stand-in engine A never reads; a printed ）（ engine A read stays ({result.rules(12)})")
    check("K9" in result.rules(17) and "︵" not in pages[17], f"K9: ︵︶ become （） ({result.rules(17)})")
    heading = next(item[1] for item in spec[5]["items"] if item[0] == "heading")
    check("P5" in result.rules(5) and "### " + heading + "\n\n" in pages[5],
          f"P5: a blank line after a heading that had none ({result.rules(5)})")
    check("P5" not in result.rules(11), f"P5: a heading engine A read in one block with the next line gets none "
                                         f"({result.rules(11)})")
    check("P5" in result.rules(7) and "秋夜讀書\n\n十七年五月" in pages[7],
          f"P5: a heading engine A read in one block with the date under it gets its blank line ({result.rules(7)})")
    check("K1" not in result.rules(4) and "丨丨" in pages[4] and "K1" in result.named(4),
          f"K1: a glyph twice where engine A read the dash and one copy is a doubt ({result.rules(4)}, "
          f"named {result.named(4)})")
    check("丁小橋流" in pages[19] and "K-seq" in result.named(19),
          f"K-seq: a figure engine A read without the dot the figures beside it carry is a doubt "
          f"({result.rules(19)}, named {result.named(19)})")
    check("一般原則" in pages[16] and "K-seq" in result.named(16),
          f"K-seq: a line's 一 engine A read as the figure 1, with no 2 after it, is a doubt "
          f"({result.rules(16)}, named {result.named(16)})")
    check("K1" in result.rules(17) and "1.漁舟唱" in pages[17],
          f"K-seq: a list's first figure the page holds as 一, with 2 after it, is written ({result.rules(17)})")
    check("K1" in result.rules(18) and "### 3 松下問答" in pages[18],
          f"K-seq: a heading's figure the contents give its title is written ({result.rules(18)}, "
          f"named {result.named(18)})")
    check(not again(tmp, "marks", result), "the rules run on their own output change nothing")

    book = ri.load_book(ri.InputPaths.resolve(tmp / "marks", {}))
    bare = copy.deepcopy(model)
    bare["standIns"] = {"markAliases": {"A": {}, "B": {}}, "figure": [], "dash": [], "dot": []}
    order = list(book.assembly.order)
    ledger = re_.Ledger({scan: book.pages[scan].sealed_text for scan in order})
    plan = rr.marks(book, bare, ledger.texts, order, rr.book_facts(book, bare))
    # An edit of K1 that names no stand-in (a stray bracket beside a figure
    # the page holds) acts on none.
    rules = sorted({e.rule for e in plan.edits if e.evidence.get("standIns") != ""} - {"K9"})
    check(not rules, f"with no stand-ins learned (nor numerals read for figures) no rule that acts on a stand-in "
                     f"(K1, K5) fires ({rules})")


def shape_checks() -> None:
    facts = rr.BookFacts(gates={}, stems=[], suffixes=[], contents_word=None, contents_first=set(), dividers=set(),
                         misread_chars=set(), aliases={}, figure=set(), dash={"丨"}, dot={"丶"},
                         numeral_figures=False, a_counts=Counter("甲乙丙丁"))
    for page, a, want in (("「甲乙丨丨」\n\n丙丁", "「甲乙——」\n丙丁", "「甲乙——」\n\n丙丁"),
                          ("甲乙，丨丙丁", "甲乙——丙丁", None), ("### 甲乙，丨丙丁", "甲乙——丙丁", "### 甲乙——丙丁"),
                          ("甲乙丨丨丙丁", "甲乙1——丙丁", None),
                          ("甲乙丶丙丁", "甲乙3·丙丁", "甲乙丶丙丁"),
                          ("甲乙……（203）\n\n（丙丁）", "甲乙……（203）\n（丙丁）", "甲乙……（203）\n\n（丙丁）"),
                          ("甲乙（3）（丙丁）", "甲乙（3）（丙丁）", "甲乙（3）（丙丁）"),
                          ("甲乙（３）（丙丁）", "甲乙（3）（丙丁）", "甲乙（３）（丙丁）"),
                          ("甲乙（3）丨丙丁", "甲乙（3）——丙丁", None),
                          ("甲乙……）（\n\n## 丙丁", "甲乙……（360）\n丙丁", "甲乙……（360）\n\n## 丙丁"),
                          ("甲乙）（\n\n丙丁", "甲乙（3）。\n丙丁", "甲乙（3）。\n\n丙丁"),
                          ("甲乙）（\n\n丙丁", "甲乙。\n（1）丙丁", "甲乙。\n\n（1）丙丁"),
                          # Phase 3 writes no bracket pair alone (#23): where it
                          # once wrote 「）（」 the page holds nothing (canzungsiling
                          # scan 45, doukeoi-zingkeoi scan 404, as sealed less
                          # the pair), and K1 writes engine A's figure there; not
                          # where the page holds a figure.
                          ("人民，主權。國家", "人民，（3）主權。國家", "人民，（3）主權。國家"),
                          ("上列要素。\n\n在拯里甲制", "上列要素。\n（1）在拯里甲制", "上列要素。\n\n（1）在拯里甲制"),
                          ("人民，3主權", "人民，（3）主權", "人民，3主權"),
                          # Engine A's leader as ASCII dots is written 「……」, not
                          # after the page's own (天地会 5 scan 14 as sealed).
                          ("俞峻……）（冯启宗", "俞峻..... (341)\n冯启宗", "俞峻……（341）冯启宗")):
        plan = rr.Marks(rr.PageData(1, page, {}, {}, a, [], page, page, "", []), facts).plan()
        out = page
        for e in sorted(plan.edits, key=lambda e: -e.start):
            out = out[:e.start] + e.after + out[e.end:]
        doubts = [(d[0], d[3].get("rule")) for d in plan.doubts]
        check(out == want if want else (out == page and doubts == [("MARK-STANDIN", "K1")]),
              f"K1 on a made-up page {page!r}, engine A {a!r}: {out!r}, doubts {doubts}")


def k11(page: str, a: str | list[tuple[str, str]], b: str) -> tuple[str, list[str], list[tuple[str, str, str]]]:
    """The mark rules on a made-up page whose engine A reading is A (its text,
    or its blocks: label and text): the page they write, their rules, and
    their doubts (kind, rule, marks)."""
    facts = rr.BookFacts(gates={}, stems=[], suffixes=[], contents_word=None, contents_first=set(), dividers=set(),
                         misread_chars=set(), aliases={}, figure=set(), dash=set(), dot=set(),
                         numeral_figures=False, a_counts=Counter())
    blocks = [{"label": label, "text": text} for label, text in a] if isinstance(a, list) else []
    body = "\n".join(block["text"] for block in blocks) if blocks else a
    plan = rr.Marks(rr.PageData(1, page, {}, {}, body, blocks, b, b, "", []), facts).plan()
    out = page
    for e in sorted(plan.edits, key=lambda e: -e.start):
        out = out[:e.start] + e.after + out[e.end:]
    return (out, sorted({e.rule for e in plan.edits}),
            [(d.kind, d.fields.get("rule"), d.fields.get("marks")) for d in plan.doubts])


def heading_mark_checks() -> None:
    date = "乾隆五十二年三月二十一日批 军录"
    for page, a, b, want in (
            # 天地会 2 scan 43: a comma where the title's two printed lines
            # meet (engine A's line break), and one at its end.
            (f"## 闽浙总督李侍尧奏常青因风汛不顺，尚在料罗暂泊片，\n\n{date}",
             f"闽浙总督李侍尧奏常青因风汛不顺\n尚在料罗暂泊片\n{date}",
             f"# 闽浙总督李侍尧奏常青因风汛不顺尚在料罗暂泊片\n{date}",
             f"## 闽浙总督李侍尧奏常青因风汛不顺尚在料罗暂泊片\n\n{date}"),
            # 天地会 3 scan 66: the title's two printed lines joined by <br>.
            ("# 闽浙总督李侍尧奏续派兵船护饷及<br>预行料理战船折\n\n乾隆五十二年七月十三日批 军录",
             "闽浙总督李侍尧奏续派兵船护饷及\n预行料理战船折\n乾隆五十二年七月十三日批 军录",
             "# 闽浙总督李侍尧奏续派兵船护饷及 预行料理战船折\n乾隆五十二年七月十三日批 军录",
             "# 闽浙总督李侍尧奏续派兵船护饷及预行料理战船折\n\n乾隆五十二年七月十三日批 军录"),
            # 盜區政區 scan 398: a comma where both engines read the
            # printed gap as a space.
            ("### 第三節，自保與教化\n\n明代", "第三節 自保與教化\n明代", "## 第三節 自保與教化\n明代",
             "### 第三節 自保與教化\n\n明代"),
            # A mark a heading opens with, after the line before it ends
            # with its own full stop.
            ("钦此遵旨寄信前来。\n\n## ，谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             "钦此遵旨寄信前来。\n谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             "钦此遵旨寄信前来。\n# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             "钦此遵旨寄信前来。\n\n## 谕闽浙总督李侍尧等巡缉毋得滋扰商贩"),
            # Marks neither engine read inside a title engine A read on one
            # line (天地会 2 scan 262 and 天地会 3 scan 108, specks in the
            # print), two marks and a <br> in one title, and scan 43's title
            # with a comma on its second line too (made up).
            ("## 谕钦差湖广总督常青等。查明打仗出力者奖擢议叙\n\n乾隆五十二年五月二十七日 台湾档",
             "谕钦差湖广总督常青等查明打仗出力者奖擢议叙\n乾隆五十二年五月二十七日 台湾档",
             "# 谕钦差湖广总督常青等查明打仗出力者奖擢议叙\n乾隆五十二年五月二十七日 台湾档",
             "## 谕钦差湖广总督常青等查明打仗出力者奖擢议叙\n\n乾隆五十二年五月二十七日 台湾档"),
            ("# 谕闽浙总督李侍尧等巡缉，毋得滋扰商贩\n\n乾隆五十二年七月二十三日 台湾档",
             "谕闽浙总督李侍尧等巡缉毋得滋扰商贩\n乾隆五十二年七月二十三日 台湾档",
             "# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩\n乾隆五十二年七月二十三日 台湾档",
             "# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩\n\n乾隆五十二年七月二十三日 台湾档"),
            ("## 谕钦差湖广总督常青等。<br>查明打仗出力者，奖擢议叙", "谕钦差湖广总督常青等查明打仗出力者奖擢议叙",
             "# 谕钦差湖广总督常青等查明打仗出力者奖擢议叙", "## 谕钦差湖广总督常青等查明打仗出力者奖擢议叙"),
            (f"## 闽浙总督李侍尧奏常青因风汛不顺，尚在料罗，暂泊片，\n\n{date}",
             f"闽浙总督李侍尧奏常青因风汛不顺\n尚在料罗暂泊片\n{date}",
             f"# 闽浙总督李侍尧奏常青因风汛不顺尚在料罗暂泊片\n{date}",
             f"## 闽浙总督李侍尧奏常青因风汛不顺尚在料罗暂泊片\n\n{date}"),
            # Left: a colon engine A read, a comma only one engine read, a
            # <br> where engine B read a glyph between the lines, a comma
            # beside a figure, a comma outside a heading, and a date's
            # separator in a heading engine A read as a column; a mark of
            # the page where both engines read another mark of CJK print
            # there (· “” 〔: a mark read, of another shape).
            ("### 折内批示：\n\n钦此", "折内批示：\n钦此", "折内批示：\n钦此", None),
            ("## 生活、讀書、新知三聯書店的歷史\n\n正文甲乙丙丁。", "生活·讀書·新知三聯書店的歷史\n正文甲乙丙丁。",
             "# 生活·讀書·新知三聯書店的歷史\n正文甲乙丙丁。", None),
            ("## 論，三民主義，的起源\n\n正文甲乙丙丁。", "論“三民主義”的起源\n正文甲乙丙丁。",
             "# 論“三民主義”的起源\n正文甲乙丙丁。", None),
            ("## 第一章，總論\n\n正文甲乙丙丁。", "第一章〔總論\n正文甲乙丙丁。", "# 第一章〔總論\n正文甲乙丙丁。", None),
            ("# 谕闽浙总督李侍尧等巡缉，毋得滋扰商贩", "谕闽浙总督李侍尧等巡缉，毋得滋扰商贩",
             "# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩", None),
            ("# 谕闽浙总督李侍尧等巡缉，毋得滋扰商贩", "谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             "# 谕闽浙总督李侍尧等巡缉，毋得滋扰商贩", None),
            ("# 闽浙总督李侍尧奏续派兵船护饷及<br>预行料理战船折", "闽浙总督李侍尧奏续派兵船护饷及\n预行料理战船折",
             "# 闽浙总督李侍尧奏续派兵船护饷及口预行料理战船折", None),
            ("#### 表，4-1 明清南贛巡撫年表", "表 4-1 明清南贛巡撫年表", "表 4-1 明清南贛巡撫年表", None),
            ("谕闽浙总督李侍尧等巡缉，毋得滋扰商贩", "谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             "谕闽浙总督李侍尧等巡缉毋得滋扰商贩", None),
            ("# 禦侮與剿共廿一、三、廿一、\n\n## 在聯合紀念週報吿",
             "12\n禦\n侮\n與\n剿\n共\n廿一\n三\n廿一\n在聯合紀念週報告",
             "口禦侮與剿共卝一三丶廿一在聯合紀念週報吿", None)):
        out, rules, doubts = k11(page, a, b)
        check(out == (want or page) and rules == (["K11"] if want else []) and doubts == [],
              f"K11 on {page.splitlines()[0][:24]!r}…: {out!r} ({rules}, doubts {doubts})")
    # Left with one doubt a heading naming its marks: where engine A read
    # either character in a block it labelled vertical_text (a column: it
    # breaks after every character).
    column = "\n".join("就國民革命軍第四軍軍長職演說詞")
    for page, a, b, want, marks in (
            # 陳總司令言論集 scan 133: a full stop printed inside a title
            # engine A read as a column, a character a line.
            ("### 28 就國民革命軍。第四軍軍長職演說詞\n\n十七年三月",
             [("vertical_text", "28\n" + column), ("vertical_text", "十七年三月")],
             "十七年三月28就國民革命軍第四軍軍長職演說詞兄弟", None, "。"),
            # The mark a heading opens or ends with, beside a column.
            ("钦此遵旨寄信前来。\n\n## ，谕闽浙总督李侍尧等巡缉毋得滋扰商贩",
             [("text", "钦此遵旨寄信前来。"), ("vertical_text", "\n".join("谕闽浙总督李侍尧等巡缉毋得滋扰商贩"))],
             "钦此遵旨寄信前来。\n# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩", None, "，"),
            ("## 谕闽浙总督李侍尧等巡缉毋得滋扰商贩，\n\n钦此遵旨寄信前来。",
             [("vertical_text", "\n".join("谕闽浙总督李侍尧等巡缉毋得滋扰商贩")), ("text", "钦此遵旨寄信前来。")],
             "# 谕闽浙总督李侍尧等巡缉毋得滋扰商贩\n钦此遵旨寄信前来。", None, "，")):
        out, rules, doubts = k11(page, a, b)
        heading = next(line for line in page.splitlines() if line.startswith("#"))
        check(out == (want or page) and rules == (["K11"] if want else [])
              and doubts == [("heading-mark-unread", "K11", marks)],
              f"K11 leaves {marks!r} in {heading[:24]!r}… with a doubt: {out!r} ({rules}, doubts {doubts})")


def forms_on(page: str, a: str, b: str | None = None) -> tuple[str, list[str]]:
    """The forms stage (K9, K12) on a made-up page whose engine A reading is
    A (one block) and engine B's B (A's when None): the page it writes and
    the rules of its edits."""
    blocks = [{"text": a, "label": "text", "index": 0}]
    engines = SimpleNamespace(record={}, engine_a_blocks=lambda: blocks, engine_a_text=lambda: a,
                              engine_b_text=lambda: a if b is None else b)
    plan = rr.forms(SimpleNamespace(pages={1: engines}), {}, {1: page}, [1])
    out = page
    for e in sorted(plan.edits, key=lambda e: -e.start):
        out = out[:e.start] + e.after + out[e.end:]
    return out, sorted({e.rule for e in plan.edits})


def forms_checks() -> None:
    for page, want, why in (
            ("甲乙,丙丁(戊己)庚", "甲乙，丙丁（戊己）庚", "a comma and brackets beside CJK characters"),
            ("雍正甲寅(1734)年", "雍正甲寅（1734）年", "brackets round figures in Chinese text, both"),
            ("乾隆(1736—1795年)间", "乾隆（1736—1795年）间", "a pair that opens with a figure, both brackets"),
            ("参见(图2)所示", "参见（图2）所示", "a pair that closes with a figure, both brackets"),
            ("甲(英文名 Hong Kong)港", "甲（英文名 Hong Kong）港", "a pair that holds Western text, both brackets"),
            ("见 Smith (1990)，书", "见 Smith （1990），书", "a pair with a full-width mark right outside, both"),
            ("亦未為晚。」(49)首領", "亦未為晚。」（49）首領", "a note number after a mark, both brackets"),
            ("see Smith (1990) and 书", None, "a pair with nothing CJK inside or right outside stays"),
            ("见[乙](#丙)。", None, "a Markdown link's target stays"),
            ("见$f(甲)$。", None, "math stays"),
            ("甲(1\n乙)", "甲（1\n乙）", "a bracket with no partner on its line, beside CJK print"),
            ("共14,625两，11:30时", None, "a comma and a colon between two figures stay"),
            ("第1, 2條", None, "a comma between a figure and a space stays"),
            ("年(1。)月", "年（1。）月", "a bracket after a full stop, and the bracket of its pair with it"),
            ("甲乙。)丙", "甲乙。）丙", "a bracket after a full-width mark"),
            ("甲1,\u3000乙", "甲1，\u3000乙", "a comma before an ideographic space"),
            ("见 Smith, J. (1990),书", None, "a gap of Western text keeps its ASCII marks, beside a character too"),
            ("<td>城周(尺)</td><td>甲</td>", "<td>城周（尺）</td><td>甲</td>",
             "a table cell's tag is no Western text"),
            ("(甲)乙", "（甲）乙", "at the page's leading edge, the bracket before its first character")):
        out, rules = forms_on(page, pp.CJK(page))
        check(out == (want or page) and rules == (["K9"] if want else []),
              f"K9: {why}: {page!r} -> {out!r} ({rules})")
    for page, a, b, want, why in (
            ("**附：**甲乙\n\n丙丁", "附：甲乙\n丙丁", None, "附：甲乙\n\n丙丁",
             "bold neither engine read goes, both markers"),
            ("甲__乙__丙", "甲乙丙", None, "甲乙丙", "underscores go as stars do"),
            ("**甲乙丙**", "甲乙丙", None, "甲乙丙", "at the page's edges"),
            ("**乙丙", "**\n甲乙丙", None, "乙丙", "at the page's leading edge, engine A's first character not the page's"),
            ("乙丙**", "乙丙丁\n**", None, "乙丙", "at the page's trailing edge, engine A's last character not the page's"),
            ("**Note**", "Note", None, "Note", "on a page of no CJK character"),
            ("甲**乙丙\n\n丁戊", "甲乙丙\n丁**戊", None, "甲乙丙\n\n丁戊", "where engine A holds the run on another line"),
            ("- **五祖灯**一盏明灯在佛前", "五祖灯 一盏明灯在佛前", "五祖灯\n一盏明灯在佛前", "- 五祖灯 一盏明灯在佛前",
             "between two characters where engine A read a space: one space"),
            # A printed star is written \* (decision 8 of 2026-11-14): a run
            # or a star the engines read stays, escaped.
            ("甲**乙**丙", "甲**乙**丙", None, r"甲\*\*乙\*\*丙", "a run engine A holds there stays, escaped"),
            ("甲**乙**丙", "甲乙丙", "甲**乙**丙", r"甲\*\*乙\*\*丙", "a run engine B holds there stays, escaped"),
            ("**甲乙丙**", "**甲乙丙**", None, r"\*\*甲乙丙\*\*",
             "a run an engine holds at the page's edges stays, escaped"),
            ("甲乙\n\n***\n\n丙丁", "甲乙\n丙丁", None, None, "a line of the run alone is a rule and stays"),
            ("甲*乙", "甲乙", None, r"甲\*乙", "a single star stays, escaped"),
            ("甲**乙丙", "甲乙丙丁**戊", None, r"甲\*\*乙丙", "a run engine A holds elsewhere on the line stays, escaped"),
            ("某某***某人", "某某＊＊＊某人", None, r"某某\*\*\*某人", "a run the engines read full width stays, escaped"),
            ("姓名______年齡", "姓名＿＿＿＿＿＿年齡", None, None, "underscores the engines read full width stay"),
            ("| 收入 | 0.45*** |\n| 支出 | 0.32** |",
             "<table><tr><td>支出</td><td>0.32**</td></tr><tr><td>收入</td><td>0.45***</td></tr></table>",
             "支出 0.32** 收入 0.45***", "| 收入 | 0.45\\*\\*\\* |\n| 支出 | 0.32\\*\\* |",
             "stars the engines read in a table's rows in another order stay, escaped")):
        out, rules = forms_on(page, a, b)
        check(out == (want or page) and rules == (["K12"] if want else []),
              f"K12: {why}: {page!r} -> {out!r} ({rules})")


# --- script ------------------------------------------------------------------

# A book printed in simplified script, as its drafts read (天地会 3 scan 75's date
# line), and the same line in traditional script.
SIMPLIFIED_DRAFT = "乾隆五十二年七月十三日内阁奉上谕台湾现在剿捕贼匪"
TRADITIONAL_DRAFT = "乾隆五十二年七月十三日內閣奉上諭臺灣現在剿捕賊匪"
# 天地会 1 scan 242: engine A 藏, engine B 葳, 裁決 蕆; 蒇 is printed.
LINE242 = "不及一月即已收复{}功伊等岂竟未之闻"
RECORD242 = {"resolved": "蕆", "draft_reading": "藏", "writer_reading": "葳", "context_before": "不及一月即已收复",
             "context_after": "功伊等岂竟未之闻", "resolved_from": "adjudicator", "crop": "page-0001-adjudication-01.png"}


def script_on(page: str, records: list[dict[str, Any]], drafts: str = SIMPLIFIED_DRAFT) -> tuple[str, list, list]:
    """The script stage (V1) on a made-up page holding PAGE, with RECORDS its
    sealed adjudications, in a book whose other page both engines read as
    DRAFTS: the page it writes, its edits (rule, before, after, form) and its
    doubts (kind, answer as given, how its detail says the answer is written)."""
    sealed = SimpleNamespace(record={"adjudications": records}, engine_a_text=lambda: "", engine_b_text=lambda: "")
    other = SimpleNamespace(record={}, engine_a_text=lambda: drafts, engine_b_text=lambda: drafts)
    plan = rr.answer_scripts(SimpleNamespace(pages={1: sealed, 2: other}), {1: page}, [1])
    out = page
    for e in sorted(plan.edits, key=lambda e: -e.start):
        out = out[:e.start] + e.after + out[e.end:]
    return (out, [(e.rule, page[e.start:e.end], e.after, e.evidence["form"]) for e in plan.edits],
            [(d[0], d[3].get("answered"), d[2][d[2].index(" is written ") + 12:d[2].index(" where ")])
             for d in plan.doubts])


def script_checks(root: Path) -> None:
    page = LINE242.format("蕆") + "\n\n" + SIMPLIFIED_DRAFT
    converted = ("answer-script-converted", "蕆", "蒇 (its one simplified form)")
    line40 = "朕连日盼望捷音甚为{}切今阅常青等奏到情形"
    record40 = {"resolved": "懸", "draft_reading": "恳", "writer_reading": "悬", "context_before": "朕连日盼望捷音甚为",
                "context_after": "切今阅常青等奏到情", "resolved_from": "adjudicator"}
    # 天地会 3 scan 181: engine A 尘, engine B 廑 as printed, 裁決 塵.
    line181 = "著福康安等速行驰奏以慰{}念至李侍尧所奏"
    record181 = {"resolved": "塵", "draft_reading": "尘", "writer_reading": "廑", "context_before": "安等速行驰奏以慰",
                 "context_after": "念至李侍尧所奏", "resolved_from": "adjudicator"}
    for page_text, records, drafts, want, edits, doubts, why in (
            (page, [RECORD242], SIMPLIFIED_DRAFT, LINE242.format("蒇") + "\n\n" + SIMPLIFIED_DRAFT,
             [("V1", "蕆", "蒇", "one")], [converted],
             "an answer's character neither engine read, nor a form of it: its one simplified form, and a doubt "
             "(engine B read another character)"),
            (line40.format("懸"), [record40], SIMPLIFIED_DRAFT, line40.format("悬"), [("V1", "懸", "悬", "read")], [],
             "a page sealed before phase 3 wrote an answer in the form an engine read: that form, no doubt"),
            (line181.format("塵"), [record181], SIMPLIFIED_DRAFT, line181.format("尘"), [("V1", "塵", "尘", "read")],
             [("answer-script-converted", "塵", "尘 (as engine A read it)")],
             "so sealed, engine A's form where engine B read another character: a doubt"),
            (page, [dict(RECORD242, writer_reading="")], SIMPLIFIED_DRAFT, LINE242.format("蒇") + "\n\n" + SIMPLIFIED_DRAFT,
             [("V1", "蕆", "蒇", "one")], [converted], "where engine B read nothing, the one form is a doubt too"),
            (LINE242.format("悬蕆"), [dict(RECORD242, resolved="悬蕆", answeredTraditional="懸蕆", draft_reading="悬藏",
                                         writer_reading="县葳")], SIMPLIFIED_DRAFT, LINE242.format("悬蒇"),
             [("V1", "蕆", "蒇", "one")], [("answer-script-converted", "懸蕆", "蒇 (its one simplified form)")],
             "an answer phase 3 wrote in an engine's form in part: the rest, and the doubt names only it"),
            (LINE242.format("蒇"), [dict(RECORD242, resolved="蒇", answeredTraditional="蕆")], SIMPLIFIED_DRAFT, None,
             [], [], "a page phase 3 sealed as it now writes is left"),
            ("其地多产苧麻以织布为业", [{"resolved": "苧", "answeredTraditional": "薴", "draft_reading": "寧",
                                    "writer_reading": "檸", "context_before": "其地多产", "context_after": "麻以织布为业",
                                    "resolved_from": "adjudicator"}], SIMPLIFIED_DRAFT, None, [], [],
             "薴 sealed as its one form 苧, itself a key of the table, is not written 苎"),
            (LINE242.format("三蒇"), [dict(RECORD242, resolved="三蒇", answeredTraditional="蕆", draft_reading="三藏",
                                         writer_reading="三葳")], SIMPLIFIED_DRAFT, None, [], [],
             "an answer as given shorter than the seal (a digit both engines read spliced round it): the seal's"),
            ("不及一月，即已收复「蕆」功，伊等岂竟未之闻。", [RECORD242], SIMPLIFIED_DRAFT,
             "不及一月，即已收复「蒇」功，伊等岂竟未之闻。", [("V1", "蕆", "蒇", "one")], [converted],
             "marks between the characters"),
            ("蕆功伊等岂竟未之闻", [dict(RECORD242, context_before="")], SIMPLIFIED_DRAFT, "蒇功伊等岂竟未之闻",
             [("V1", "蕆", "蒇", "one")], [converted], "at the page's leading edge"),
            ("不及一月即已收复蕆", [dict(RECORD242, context_after="")], SIMPLIFIED_DRAFT, "不及一月即已收复蒇",
             [("V1", "蕆", "蒇", "one")], [converted], "at the page's trailing edge"),
            (page, [RECORD242, RECORD242], SIMPLIFIED_DRAFT, LINE242.format("蒇") + "\n\n" + SIMPLIFIED_DRAFT,
             [("V1", "蕆", "蒇", "one")], [converted], "two records of one spot: one edit, one doubt"),
            (page, [RECORD242], TRADITIONAL_DRAFT, None, [], [], "a book printed in traditional script is left"),
            (page, [dict(RECORD242, resolved_from="adjudicator-deletion-refused")], SIMPLIFIED_DRAFT, None, [], [],
             "an answer phase 3 did not write is left"),
            (page, [dict(RECORD242, draft_reading="蕆")], SIMPLIFIED_DRAFT, None, [], [],
             "a character engine A read there is left"),
            (LINE242.format("藏") + "又蕆事", [RECORD242], SIMPLIFIED_DRAFT, None, [], [],
             "the character elsewhere on the page, not at the record's spot, is left"),
            ("收复蕆功", [RECORD242], SIMPLIFIED_DRAFT, None, [], [],
             "a place that shares only three characters of the record's context is left"),
            ("又蕆功伊等岂竟未之闻", [RECORD242], SIMPLIFIED_DRAFT, None, [], [],
             "a place whose character before is not the record's, inside the page, is left"),
            ("不及一月即已收复蕆事", [RECORD242], SIMPLIFIED_DRAFT, None, [], [],
             "a place whose character after is not the record's, inside the page, is left"),
            (LINE242.format("蕆") + "\n\n" + LINE242.format("蕆"), [RECORD242], SIMPLIFIED_DRAFT, None, [], [],
             "two places that both hold the record's context are left")):
        out, got, raised = script_on(page_text, records, drafts)
        check((out, got, raised) == (want or page_text, edits, doubts),
              f"V1: {why}: {page_text[:20]!r}… -> {out[:20]!r} ({got}, {raised})")

    # Through repair_book.py, on the made-up book printed in simplified script:
    # scan 6 holds 蕆 where engine A read 藏 and engine B 葳.
    spec = fx.make_spec()
    for page in spec.values():
        page["items"] = [(item[0], pp.simplified_form(item[1]), *item[2:]) if item[0] == "para" else item
                         for item in page["items"]]
    spec[6]["items"].append(("para", LINE242.format("藏") + "。", False))
    spec[6]["bEdits"] = [("收复藏功", "收复葳功")]
    spec[6]["sealedEdits"] = [("收复藏功", "收复蕆功")]
    spec[6]["adjudications"] = [dict(RECORD242, crop=None)]
    # Scan 7 holds the same reading written 葳 (天地会 1 scan 248): W1, which runs
    # after the stage, names what scan 6 now holds.
    spec[7]["items"].append(("para", "官兵迅速收复藏功乃该督。", False))
    spec[7]["bEdits"] = [("收复藏功", "收复葳功")]
    spec[7]["sealedEdits"] = [("收复藏功", "收复葳功")]
    spec[7]["adjudications"] = [{"resolved": "葳", "draft_reading": "藏", "writer_reading": "葳",
                                 "context_before": "官兵迅速收复", "context_after": "功乃该督",
                                 "resolved_from": "adjudicator"}]
    # An answer at the band, opening scan 16 (engine B reading the head first,
    # folio 4): 叢 of the head, which neither engine read there.  F6 deletes it
    # first, and the script stage, which runs after it, finds nothing to write;
    # run before it, the stage would write 丛 there and F6 would find no answer.
    spec[16]["headInB"] = "start"
    body = paragraphs(spec[16])[0]
    spec[16]["sealedEdits"] = [(body, "叢" + body)]
    spec[16]["adjudications"] = [{
        "context_before": fx.HEAD + "乙編", "context_after": pp.CJK(body)[:8], "draft_reading": "",
        "writer_reading": fx.numeral(4), "resolved": "叢", "resolved_from": "adjudicator",
        "trigger": "engine-disagreement"}]
    result = repair(root, spec, "script")
    v1 = [r for r in result.log if r["rule"] == "V1"]
    raised = [d for d in result.doubts if d["kind"] == "answer-script-converted"]
    check(result.status == 0 and [(r["stage"], r["page"], r["before"], r["after"]) for r in v1] == [("script", 6, "蕆", "蒇")]
          and "收复蒇功" in result.pages[6] and [(d["page"], d["stage"], d["rule"]) for d in raised] == [(6, "script", "V1")]
          and result.report["stages"]["script"]["rules"] == ["V1"],
          f"V1 through repair_book: the stage writes 蒇 on scan 6 and names it in a doubt "
          f"({result.status}, {v1}, {raised})")
    named = [d["detail"] for d in result.doubts if d.get("rule") == "W1"]
    check(named == ["W1: one reading written 2 ways: 收复［蒇］功伊 p.6; 收复［葳］功乃 p.7"],
          f"V1 then W1: the words stage names the 蒇 scan 6 now holds, not the sealed 蕆 ({named})")
    check(result.rules(16) == ["F6"] and result.pages[16].startswith(body),
          f"V1 after F6: an answer at the band goes with the band, not written in simplified script first "
          f"({result.rules(16)}, {result.pages[16][:12]!r})")
    off = repair(root, spec, "script-off", "--no-script")
    check(off.status == 0 and not [r for r in off.log if r["rule"] == "V1"] and "收复蕆功" in off.pages[6]
          and off.report["stages"]["script"]["status"] == "off (--no-script)",
          "--no-script switches the stage off")


def main() -> int:
    forms_checks()
    shape_checks()
    heading_mark_checks()
    with tempfile.TemporaryDirectory() as tmp:
        furniture_checks(Path(tmp))
        edge_checks(Path(tmp))
        marks_checks(Path(tmp))
        script_checks(Path(tmp))
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_rules: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
