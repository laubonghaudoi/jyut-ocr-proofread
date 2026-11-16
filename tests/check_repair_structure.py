#!/usr/bin/env python3
"""Offline check of the book repair tool's structure rules
(scripts/_repair_structure.py) through scripts/repair_book.py: the
contents and their units (S9, S4, S7, S11), the title page (S13) and the
heading levels (S5); and the stand-in rule's dash in headings (K1,
scripts/_repair_rules.py).

No model, no GPU, no corpus: made-up books (tests/repair_fixture.py), each
page changed only where a rule's case is set up.  Every rule has a case it
must repair and a look-alike it must leave:

Contents (the made-up contents: two groups, each a run suffix and its
subtitle, entries `n title`):
- S11 rebuilds the contents: an entry whose bullet stands where its number
  was gets the number engines A and B read (engine A on a line of its own
  above the title); a number printed after the title
  goes before it; a mark before the title engine A did not read goes; the
  mark after a number the page holds stays where engine A read the figure
  without it; the mark engine A read after its figure, which the page holds
  where the figure was lost, is written once; the group line becomes a
  heading, the suffix apart from the rest; an entry whose number no source
  read is a `TOC-NUMBER-LOST` doubt, and an entry with no unit in the body a
  `unit-not-in-scan` doubt.
- S11 on a contents that prints each entry's page at the end of its line
  after leaders (the look-alike): the figure both engines read at the end of
  the line before is not the entry's number and is not written; the bracket
  that opens an entry stays, read by engine A at the line's head, or not
  read by it while the line holds its closing partner, or with engine A not
  placed at the title (no evidence); the emphasis markers that
  wrap an entry go together where engine A wrote none, and stay together
  with engine A not placed, and a run with no partner at the line's end
  stays (the forms stage off: with it, K12 deletes every one of them); a figure the page holds at a line's head stays where no number is
  written, and goes to the end of the line before where both engines read
  it there; an enumerator of two figures (1.1) stays whole; the bullets
  still go.
- S11 on a heading of a contents page that the map takes for an entry and
  engine A reads as a title block: it stays a heading, at the units' level.
- S14 joins a unit title printed over two lines - two headings, a heading
  and a plain line, a contents entry whose second line ends with its page
  number (its unit one heading; the number bracketed; the second half
  misread, joined on the first half's unit), and both at once, cut at
  other places, the body's second line a heading or a plain line (the
  book model reads such an entry as one, M-6) - into one heading at the
  units' level and one contents line, every entry with its unit; it
  leaves a date line with no page number over the next title, which the
  body prints straight before that title, and a group line over an entry,
  which a sentence of the body names with it (the model reads them
  apart), a title whose second half opens a paragraph (a `unit-not-in-scan`
  doubt), an entry with its page number over a date its unit's heading
  holds after the title, an entry that lost its page number over a
  one-character entry its unit's heading does not go on with, two
  headings with a mark line between, a title the engines read short
  over its date heading, a title over its date heading whose contents line
  holds the date after its page number (with no leaders, a space or one
  '．' before the figure: the entry holds the date, a doubt; after leaders:
  the entry is the title, with its unit), and a whole contents line over
  the next; the lint finds two rows on one line only where it holds both
  whole, and no space is written between kana.  Titles listed on a
  contents page whose printed lines the OCR ran together with their dates
  (the date before the next title, a bullet with a `<br>`, two entries on
  one line), or with a `<br>` where a title wraps, are joined too, the
  page read as contents; S15 cuts those lines where engine A read the
  line break (a date's 批 stays with its date, a `<br>` there goes; the
  `<br>` inside a title stays), and S11 keeps every character of them.
  S15 cuts a date's 批 run on into the next title (no `<br>`) after 批,
  and not at a title's own wrap where engine A misread the character
  after the real cut.  The lint finds the rows cut apart, each
  on the line holding its last character, and the last of them with the
  row S14 joins on; and a row printed over two lines on the line of its
  end.
- An engine's figure before an entry (entry_gap) is the entry's alone on its
  line, at the head of the entry's line or straight before the title; not
  where it ends a line after leaders or after the title before, on that
  line or on a line of its own with its leaders; what stands before the
  gap's first line break is not read "before the title".
- S4 writes engine A's number over a glyph it did not read before a unit's
  title; a unit whose number engine A did not read is a `HEAD-NUMBER-LOST`
  doubt, and so is one whose heading holds, before the number, characters
  engine A read.  A number the page already holds on a line of its own
  above the title is not written again.
- Units numbered `n.`: a unit number written with no mark (engine A read
  none, the page none) between units that carry '.' is a
  `punctuation-partial` doubt (S4); S7 moves a date with the comma before it
  that neither engine read, and a comma engine A or engine B read stays.
  An entry of contents numbered `n.` read without its mark (page and
  engines) between entries that carry it is a `punctuation-partial` doubt
  (S11).
- S9 makes a unit title that stands as text a heading - split from the
  paragraph run onto its line at engine A's next block, and split from the
  paragraph it was glued to at the start of its engine A block.
- S7 moves the date after a unit title to its own paragraph, whole where the
  body's title is two characters short of the contents' (the title's match
  ends at its last character, not in the date).
- The date parser takes the one start a run of numerals allows (之四四月),
  the run's own start where that parses, and none where two parse; an
  enumerator and a word that opens with a numeral (一，五湖…) is no date.

Headings:
- K1 writes engine A's dash between a title and its subtitle where the page
  holds none; a heading where engine A read no dash is left; where engine A
  read a stand-in glyph beside its dash (the page's stand-ins may be the
  dash) it is a `MARK-STANDIN` doubt; a hyphen engine A read between two figures (a
  caption's number, which the page lacks) is no dash, and the runs stage
  writes the number - beside a printed dash too, which K1 still writes.

Levels and the title page:
- S13 names a page printed right to left (engine A read its lines
  backwards, engine B its title forwards) in one `research-front-matter`
  doubt and leaves it as it is; no other page is named.
- S5 sets one level per contents depth: the book title `#`, the contents
  title and the dividers `##`, the groups and the units `###`, a heading of
  its own inside a unit `####`; a date written as a heading is plain text; a heading engine A read at the start of a longer block
  keeps its level with no doubt (layout alone), an enumerator and a word
  that opens with a numeral too; a heading no rule places keeps its level, a
  `heading-kind-unknown` doubt.

Gates: contents whose numbers are not their places fail the
`contents-numbering` gate (a `gate-failed` doubt) and no number is taken
from a place; a book with no contents (M-6) gets no contents or level edit.

Every run replays its log to its output, and the stages run again on their
own output change nothing.

Usage:
    python3 tests/check_repair_structure.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
import _book_lint as lint  # noqa: E402
import _repair_edits as re_  # noqa: E402
import _repair_inputs as ri  # noqa: E402
import _repair_rules as rr  # noqa: E402
import _repair_structure as rs  # noqa: E402
import repair_book  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


class Result:
    def __init__(self, status: int, out: Path, book: Path):
        self.status = status
        self.out = out
        self.book = book
        self.report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
        self.log = [json.loads(line) for line in (out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
        self.pages = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in (out / "pages").glob("*.md")}
        self.sealed = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8")
                       for p in (book / "proofread").glob("page-*.md")}

    def rules(self, page: int) -> list[str]:
        return [r["rule"] for r in self.log if r["page"] == page]

    def rules_on(self, rule: str) -> list[int]:
        return [r["page"] for r in self.log if r["rule"] == rule]

    def named(self, page: int) -> list[str]:
        """The rules whose doubts name a site on PAGE."""
        return [d["rule"] for d in self.doubts if d["page"] == page and d.get("rule")]

    def doubted(self, kind: str) -> list[int | None]:
        return [d["page"] for d in self.doubts if d["kind"] == kind]


def repair(root: Path, spec: dict[int, dict[str, Any]], name: str, *argv: str) -> Result:
    book = fx.write_book(root / name, spec)
    out = root / f"{name}-out"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        status = repair_book.main(["--workers", "1", str(book), "--output", str(out), *argv])
    return Result(status, out, book)


def heading_of(spec: dict[int, dict[str, Any]], scan: int) -> str:
    return next(item[1] for item in spec[scan]["items"] if item[0] == "heading")


def set_heading(spec: dict[int, dict[str, Any]], scan: int, text: str) -> None:
    """The unit heading of SCAN printed (and read, and sealed) as TEXT."""
    items = spec[scan]["items"]
    k = next(i for i, item in enumerate(items) if item[0] == "heading")
    items[k] = ("heading", text, items[k][2])


def seal(spec: dict[int, dict[str, Any]], scan: int, old: str, new: str) -> None:
    spec[scan].setdefault("sealedEdits", []).append((old, new))


def paragraphs(spec: dict[int, dict[str, Any]], scan: int) -> list[str]:
    return [item[1] for item in spec[scan]["items"] if item[0] == "para"]


def retitle(spec: dict[int, dict[str, Any]], contents: int, unit: int, old: str, new: str) -> None:
    """A unit's title OLD printed as NEW, in the contents (scan CONTENTS)
    and over the unit (scan UNIT)."""
    for scan in (contents, unit):
        items = spec[scan]["items"]
        for i, item in enumerate(items):
            if item[0] in ("line", "heading") and item[1].endswith(" " + old):
                items[i] = (item[0], item[1][:-len(old)] + new, *item[2:])


def run_on(seed: int, size: int) -> str:
    """SIZE made-up characters with a comma every seven and no full stop:
    prose that goes on past the page."""
    chars = fx.filler(seed, size)
    return "".join(c + ("，" if i % 7 == 0 and i < size else "") for i, c in enumerate(chars, 1))


def again(root: Path, name: str, result: Result) -> list[re_.Edit]:
    """K1's dash in headings and the structure stages run on RESULT's own
    pages (with the model of the book): what they would change."""
    book = ri.load_book(ri.InputPaths.resolve(root / name, {}))
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    order = list(book.assembly.order)
    ledger = re_.Ledger({scan: result.pages[scan] for scan in order})
    facts = rr.book_facts(book, model)
    proposed: list[re_.Edit] = []

    def apply(edits):
        proposed.extend(edits)
        ledger.apply("again", edits)
    for step in (lambda: rr.Plan([e for scan in order
                                  for e in rr.heading_dashes(book, model, scan, ledger.texts[scan], facts).edits]),
                 lambda: rs.contents_stage(book, model, ledger.texts, order, apply, facts),
                 lambda: rs.title_pages(book, model, ledger.texts, order, facts),
                 lambda: rs.levels(book, model, ledger.texts, order, facts)):
        apply(step().edits)
    return proposed


# --- the date parser --------------------------------------------------------------

def date_checks() -> None:
    text = "訓話之四四月二十三日"
    found = rs.find_date(text)
    check(found is not None and text[found[0]:found[1]] == "四月二十三日",
          f"a subtitle's ordinal glued to a date: the one start the run allows ({found})")
    text = "紀念演說十八，六，十七，"
    found = rs.find_date(text)
    check(found is not None and text[found[0]:found[1]].startswith("十八，六，十七"),
          f"a date that starts where its run of numerals starts is taken whole ({found})")
    check(rs.find_date("訓詞之一十九五，十二") is None,
          "two starts inside a run of numerals that both parse: no date is taken")
    check(rs.find_date("訓話之十二十月十五日") is not None
          and "訓話之十二十月十五日"[rs.find_date("訓話之十二十月十五日")[0]:].startswith("十月"),
          "a month over twelve is no date: 之十二 then 十月十五日")
    check(rs.find_date("一年來之近況") is not None and rs.find_date("三月之間") is None,
          "a year alone is a date; a month alone is not (too many words hold one)")
    check(rs.is_date_line("三年五月七日") and not rs.is_date_line("三年來的工作與今後的計劃之大要與其方法及步驟"),
          "a date line is a date and at most a short clause after it")
    check(rs.find_date("一，五湖四海之遊") is None and not rs.is_date_line("一，五湖四海之遊")
          and not rs.is_date_line("二、三民之說"),
          "an enumerator and a word that opens with a numeral is no date (一，五湖…)")
    check(all(rs.is_date_line(line) for line in ("廿，八月", "三，九，在山中作", "九，十二。")),
          "numeral groups that end at 月, a separator or a mark are a date")


# --- an engine's figure before a contents entry --------------------------------------

def gap_checks() -> None:
    cases = [
        # The entry's: alone on its line (above the title), after the end of
        # the line before, opening the engine's text, at the head of the
        # entry's line, straight before the title, with its mark, after an
        # engine's heading marker.
        ("\n12\n", False, (12, ""), ""),
        ("？\n12\n", False, (12, ""), ""),
        ("12\n", True, (12, ""), ""),
        ("\n12 ", False, (12, ""), ""),
        ("15", False, (15, ""), ""),
        ("\n3.\n", False, (3, "."), ""),
        ("\n## 12\n", False, (12, ""), "## "),
        # The line before's page number: after leaders, after the title with
        # no leaders, with its leaders on a line of their own.
        ("……133\n", False, None, ""),
        ("……133  \n\n", False, None, ""),
        (" 133\n", False, None, ""),
        ("\n……133\n", False, None, ""),
        # No figure of the entry's: the bracket that opens it, an enumerator
        # of two figures, a year.
        ("……133\n（", False, None, "（"),
        ("……5\n1.1 ", False, None, "1.1 "),
        ("……5\n1949", False, None, "1949"),
        # Markup before a figure alone on its line - a bullet, a run of
        # emphasis - is no print of the line; underscores written for the
        # leaders are, and the figure after them is the line before's.
        ("\n- 12\n", False, (12, ""), "- "),
        ("- 12\n", True, (12, ""), "- "),
        ("\n**12\n", False, (12, ""), "**"),
        ("\n____12\n", False, None, ""),
        ("____12\n", True, None, ""),
    ]
    wrong = [(gap, first, rs.entry_gap(gap, first)[:2]) for gap, first, figure, lead in cases
             if rs.entry_gap(gap, first)[:2] != (lead, figure)]
    check(not wrong, f"entry_gap: an engine's figure is the entry's alone on its line or on the entry's line, and "
                     f"the line before's where it ends that line after other print ({wrong})")
    before = [rs.entry_gap(gap, first).before for gap, first, _, _ in cases]
    check(before == [None] * 7 + ["133"] * 4 + [None] * 3 + [None] * 3 + ["12"] * 2,
          f"entry_gap: the page number that ends the line before is kept as that ({before})")


# --- contents ------------------------------------------------------------------------

# A unit title long enough to match with two of its characters lost.
LONG_TITLE = "山居雜詠松風竹影梅花明月溪橋煙雨漁舟唱晚歸來"


def contents_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    # S11 on the first contents page: a bullet where the number was (both
    # engines read it), a number printed after its title, a mark before the
    # title engine A did not read, the group line as plain text.
    seal(spec, 2, "1 春日遊記", "- 春日遊記")
    seal(spec, 2, "2 秋夜讀書", "秋夜讀書 2")
    seal(spec, 2, "3 山居雜詠", "。山居雜詠")
    seal(spec, 2, "### 甲編 詩文", "甲編詩文")
    # An entry whose number no source read; an entry with no unit.
    # Engine A read the first entry's number on a line of its own above its
    # title (a vertical contents: the number printed over the title).
    spec[2]["aEdits"] = [("4 江上聽雨", "江上聽雨"), ("1 春日遊記", "1\n春日遊記")]
    spec[2]["bEdits"] = [("4 江上聽雨", "江上聽雨")]
    seal(spec, 2, "4 江上聽雨", "江上聽雨")
    spec[3]["items"].append(("line", "4 竹林清話"))
    # S4: a glyph engine A did not read where it read the unit's number;
    # a unit whose number engine A did not read (a doubt).
    seal(spec, 7, "### 2 秋夜讀書", "### 丄秋夜讀書")
    spec[18]["aEdits"] = [("3 松下問答", "松下問答")]
    seal(spec, 18, "### 3 松下問答", "### 松下問答")
    # Two long unit titles (the second contents page's entries).
    retitle(spec, 3, 13, "晨鐘暮鼓", "晨鐘暮鼓山寺早課聽松")
    retitle(spec, 3, 15, "田園小記", "田園小記春耕秋收冬藏")
    # S7: a date printed at the foot of the title's column; one after a
    # subtitle's ordinal set as a figure.
    set_heading(spec, 5, "1 春日遊記三年五月七日")
    set_heading(spec, 7, "2 秋夜讀書之2三年六月八日")
    # S7: a long title whose body copy lost two characters (it still
    # matches, with a window one short), its date after it.
    retitle(spec, 2, 9, "山居雜詠", LONG_TITLE)
    set_heading(spec, 9, f"3 {LONG_TITLE}十年五月七日")
    seal(spec, 9, f"### 3 {LONG_TITLE}", f"### 3 {LONG_TITLE.replace('明月', '')}")
    # S11: the mark after a number the page holds, which engine A read
    # without it; the mark engine A read after its figure, which the page
    # holds where the figure was lost.
    items = spec[3]["items"]
    for i, item in enumerate(items):
        if item[0] == "line" and item[1][:2] in ("1 ", "2 "):
            items[i] = ("line", item[1][0] + "." + item[1][2:])
    spec[3]["aEdits"] = [("1.晨鐘", "1晨鐘")]
    seal(spec, 3, "2.田園", ".田園")
    # S4: a heading that holds, before the unit's number, characters engine
    # A read there.
    set_heading(spec, 11, "卷首 4 江上聽雨")
    return spec


def units_spec() -> dict[int, dict[str, Any]]:
    """S9's book: two more units in the second group (so that each contents
    page still maps, M-6, with one title off its line's start)."""
    spec = fx.make_spec()
    for n, scan, title in ((4, 19, "竹林清話"), (5, 20, "漁舟唱晚")):
        spec[3]["items"].append(("line", f"{n} {title}"))
        spec[scan]["items"].insert(1, ("heading", f"{n} {title}", 3))
    # A unit title run onto its paragraph's line, as text; a unit title
    # glued to the end of the paragraph before it.
    first = paragraphs(spec, 11)[0]
    seal(spec, 11, "### 4 江上聽雨\n\n" + first, "4 江上聽雨" + first)
    before = run_on(180, 30) + "。"
    spec[18]["items"].insert(0, ("para", before, True))
    seal(spec, 18, before + "\n\n### 3 松下問答", before + "3 松下問答")
    return spec


def units_checks(tmp: Path) -> None:
    spec = units_spec()
    result = repair(tmp, spec, "units")
    pages = result.pages
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"units: the run exits 0 and its log replays to its output (exit {result.status})")
    first = paragraphs(spec, 11)[0]
    check(pages[11].startswith("### 4 江上聽雨\n\n" + first) and "S9" in result.rules(11),
          f"S9: the title run onto its paragraph's line is split off at engine A's next block and made a heading "
          f"({pages[11][:30]!r})")
    check("。\n\n### 3 松下問答\n\n" in pages[18] and "S9" in result.rules(18),
          f"S9: the title glued to the paragraph before it is split off where its engine A block starts "
          f"({result.rules(18)})")
    rest = again(tmp, "units", result)
    check(not rest, f"units: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule) for e in rest]})")


def contents_checks(tmp: Path) -> None:
    spec = contents_spec()
    result = repair(tmp, spec, "contents")
    pages = result.pages
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"contents: the run exits 0 and its log replays to its output (exit {result.status})")
    lines = pages[2].split("\n")
    check("1 春日遊記" in lines and "- 春日遊記" not in pages[2],
          f"S11: the bullet goes, the number both engines read is written ({lines})")
    check("2 秋夜讀書" in lines and "秋夜讀書 2" not in pages[2],
          "S11: the number printed after the title goes before it")
    check(f"3 {LONG_TITLE}" in lines and "。山居雜詠" not in pages[2],
          "S11: a mark before the title engine A did not read goes")
    check("### 甲編 詩文" in lines, "S11: the group line is a heading, the run suffix apart from the rest")
    check("江上聽雨" in lines and any(d.get("rule") == "S11" and d["kind"] == "TOC-NUMBER-LOST"
                                    and "江上聽雨" in d.get("entry", "") for d in result.doubts),
          f"S11: an entry whose number no source read is a `TOC-NUMBER-LOST` doubt, not a guess "
          f"({result.named(2)})")
    check(3 in result.doubted("unit-not-in-scan") and any("竹林清話" in d["detail"] for d in result.doubts),
          "an entry with no unit in the body is a `unit-not-in-scan` doubt")
    check("### 2 秋夜讀書" in pages[7] and "丄" not in pages[7] and "S4" in result.rules(7),
          f"S4: engine A's number replaces the glyph it did not read ({result.rules(7)})")
    check("### 松下問答" in pages[18] and "S4" in result.named(18) and "S4" not in result.rules(18)
          and 18 in result.doubted("HEAD-NUMBER-LOST"),
          f"S4: a unit whose number engine A did not read is a `HEAD-NUMBER-LOST` doubt ({result.named(18)})")
    check("### 1 春日遊記\n\n三年五月七日" in pages[5] and "S7" in result.rules(5),
          f"S7: the date after the title is its own paragraph ({result.rules(5)})")
    check("### 2 秋夜讀書之2\n\n三年六月八日" in pages[7],
          f"S7: a figure just before the date stays with the title ({pages[7].split(chr(10))[:3]})")
    short = LONG_TITLE.replace("明月", "")
    check(f"### 3 {short}\n\n十年五月七日" in pages[9],
          f"S7: the date after a title two characters short of the contents' moves whole "
          f"({[line for line in pages[9].split(chr(10)) if line.strip()][:2]})")
    later = pages[3].split("\n")
    check("1. 晨鐘暮鼓山寺早課聽松" in later,
          f"S11: the mark after a number the page holds stays where engine A read the figure without it "
          f"({[line for line in pages[3].split(chr(10)) if line.strip()]})")
    check("2. 田園小記春耕秋收冬藏" in later and "2. ." not in pages[3],
          "S11: the mark engine A read after its figure, held by the page where the figure was lost, is written once")
    check("### 卷首 4 江上聽雨" in pages[11] and "S4" in result.named(11) and "S4" not in result.rules(11),
          f"S4: characters engine A read before the unit's number stay, and the site is a doubt "
          f"({result.rules(11)}, {result.named(11)})")
    rest = again(tmp, "contents", result)
    check(not rest, f"contents: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule) for e in rest]})")


def unit_marks_checks(tmp: Path) -> None:
    """Unit headings numbered `n.`: S7's comma before a date, S4's missing
    mark, S11's missing mark, and a unit number on its own line above its
    title."""
    spec = fx.make_spec()
    for scan in (5, 7, 9, 11):
        set_heading(spec, scan, re.sub(r"^(\d) ", r"\1. ", heading_of(spec, scan)))
    # Scan 7: the date printed after the title, no comma; the page holds one.
    set_heading(spec, 7, "2. 秋夜讀書三年五月七日")
    seal(spec, 7, "### 2. 秋夜讀書三年五月七日", "### 2. 秋夜讀書，三年五月七日")
    # Scan 11: the same, and engine A read the comma: it is printed.
    set_heading(spec, 11, "4. 江上聽雨，三年六月八日")
    # Scan 5: the comma printed; engine A missed it, engine B read it (its
    # stand-in glyph): it is printed.
    set_heading(spec, 5, "1. 春日遊記，三年四月六日")
    spec[5]["aEdits"] = [("春日遊記，三年", "春日遊記三年")]
    # Scan 9: unit 3's dot engine A and the page lost.
    spec[9]["aEdits"] = [("3. 山居雜詠", "3 山居雜詠")]
    seal(spec, 9, "### 3. 山居雜詠", "### 3 山居雜詠")
    result = repair(tmp, spec, "unit-marks")
    pages = result.pages
    found = [(d["page"], d.get("contentsNumber")) for d in result.doubts
             if d["kind"] == "punctuation-partial" and d.get("rule") == "S4"]
    check(result.status == 0 and found == [(9, 3)] and "### 3 山居雜詠" in pages[9],
          f"S4: a unit number with no mark between units that carry '.' is a `punctuation-partial` doubt, "
          f"nothing written ({found}, {pages[9][:12]!r})")
    check("### 2. 秋夜讀書\n\n三年五月七日" in pages[7] and "秋夜讀書，" not in pages[7],
          f"S7: the comma before the date that neither engine read goes with the date's move ({pages[7][:40]!r})")
    check("江上聽雨，\n\n三年六月八日" in pages[11],
          f"S7: a comma engine A read before the date stays ({pages[11][:40]!r})")
    check("春日遊記，\n\n三年四月六日" in pages[5],
          f"S7: a comma engine B read before the date (engine A did not) stays ({pages[5][:40]!r})")

    # S11: contents numbered `n.`; entry 2's dot the page and both engines
    # lost.
    spec = fx.make_spec()
    for scan in (2, 3):
        spec[scan]["items"] = [("line", re.sub(r"^(\d) ", r"\1. ", item[1])) if item[0] == "line" else item
                               for item in spec[scan]["items"]]
    spec[2]["aEdits"] = [("2. 秋夜讀書", "2 秋夜讀書")]
    spec[2]["bEdits"] = [("2. 秋夜讀書", "2 秋夜讀書")]
    seal(spec, 2, "2. 秋夜讀書", "2 秋夜讀書")
    result = repair(tmp, spec, "entry-marks")
    found = [(d["page"], d.get("contentsNumber")) for d in result.doubts
             if d["kind"] == "punctuation-partial" and d.get("rule") == "S11"]
    lines = result.pages[2].split("\n")
    check(result.status == 0 and found == [(2, 2)] and "2 秋夜讀書" in lines and "1. 春日遊記" in lines,
          f"S11: an entry read without the '.' the entries on either side carry is a `punctuation-partial` doubt "
          f"({found}, {lines})")

    # S4: the unit's number on a line of its own above its title, which
    # engine A read once before the title: it is not written again.
    spec = fx.make_spec()
    seal(spec, 9, "### 3 山居雜詠", "### 3\n\n### 山居雜詠")
    result = repair(tmp, spec, "number-above")
    page = result.pages[9]
    check(result.status == 0 and "### 山居雜詠" in page and re.findall(r"\d+", page) == ["3"]
          and "S4" not in result.rules(9),
          f"S4: a unit number the page holds on its own line above the title is not written again "
          f"({page[:24]!r}, {result.rules(9)})")


def bold_checks(tmp: Path) -> None:
    """S11 on numbered entries sealed in bold: the number alone, the number
    and its mark, the whole entry."""
    spec = fx.make_spec()
    seal(spec, 2, "1 春日遊記", "**1** 春日遊記")
    seal(spec, 2, "2 秋夜讀書", "**2.** 秋夜讀書")
    seal(spec, 2, "3 山居雜詠", "**3 山居雜詠**")
    result = repair(tmp, spec, "bold")
    lines = result.pages[2].split("\n")
    check(result.status == 0 and "1 春日遊記" in lines,
          f"S11: the emphasis around the number alone goes with the number's old form ({lines})")
    check("2. 秋夜讀書" in lines,
          "S11: the number's mark inside the emphasis is the page's mark, written after the number")
    check("3 山居雜詠" in lines and "*" not in result.pages[2],
          "S11: the emphasis that wraps a numbered entry, which engine A did not write, goes - both markers")
    rest = again(tmp, "bold", result)
    check(not rest, f"bold entries: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")


def contents_heading_checks(tmp: Path) -> None:
    """S11 on a heading of a contents page that the contents map takes for an
    entry, which engine A reads as a title block."""
    spec = fx.make_spec()
    spec[3]["items"].insert(0, ("heading", "續錄", 2))
    result = repair(tmp, spec, "contents-heading")
    lines = [line for line in result.pages[3].split("\n") if line.strip()]
    check(result.status == 0 and lines[0] == "### 續錄",
          f"S11: a heading engine A reads as a title block, taken for an entry, stays a heading at the units' "
          f"level ({lines[:2]})")
    rest = again(tmp, "contents-heading", result)
    check(not rest, f"contents heading: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")


# --- a contents with page numbers ----------------------------------------------------

def paged_spec() -> dict[int, dict[str, Any]]:
    """The made-up book with a contents of the other kind: no entry is
    numbered, each line ends with leaders and the page its unit starts on,
    and the engines read each line as printed - so the figure they read
    before a title is the page number that ends the line before.  Four more
    units (scans 6, 8, 10, 14) carry the look-alikes."""
    spec = fx.make_spec()
    for scan, title in ((19, "竹林清話"), (20, "漁舟唱晚")):
        spec[3]["items"].append(("line", f"0 {title}"))
        spec[scan]["items"].insert(1, ("heading", f"0 {title}", 3))
    for scan, title, after in ((6, "晨光初照", "春日遊記"), (8, "雲水禪心", "秋夜讀書"), (10, "竹影清風", "山居雜詠"),
                               (14, "雲外鐘聲", "晨鐘暮鼓")):
        items = spec[2 if scan < 13 else 3]["items"]
        at = next(i for i, item in enumerate(items) if item[0] == "line" and item[1].endswith(after))
        items.insert(at + 1, ("line", f"0 {title}"))
        spec[scan]["items"].insert(1, ("heading", f"0 {title}", 3))
    for scan in spec:
        items = spec[scan]["items"]
        for i, item in enumerate(items):
            if item[0] in ("line", "heading") and item[1][:1].isdigit():
                items[i] = (item[0], item[1].split(" ", 1)[1], *item[2:])
    page_of = {heading_of(spec, scan): scan for scan in spec if scan > 4 and any(
        item[0] == "heading" for item in spec[scan]["items"])}
    for scan in (2, 3):
        items = spec[scan]["items"]
        for i, item in enumerate(items):
            if item[0] == "line":
                items[i] = ("line", f"{item[1]}……{page_of[item[1]]}")

    def reprint(scans: tuple[int, int], title: str, printed: str) -> None:
        for scan in scans:
            items = spec[scan]["items"]
            for i, item in enumerate(items):
                if item[0] in ("line", "heading") and item[1].startswith(title):
                    items[i] = (item[0], printed + item[1][len(title):], *item[2:])
    # An entry that opens with a bracket, as a list item after a plain one;
    # an entry in a pair of emphasis markers whose first character engine A
    # misread (it is not placed at the title); emphasis that ends inside
    # the title.
    reprint((2, 7), "秋夜讀書", "（乙）秋夜讀書")
    reprint((2, 9), "山居雜詠", "（甲）山居雜詠")
    reprint((2, 11), "江上聽雨", "「江上」聽雨")
    reprint((3, 19), "竹林清話", "1.1 竹林清話")
    seal(spec, 2, "春日遊記……5", "**春日**遊記……5")
    seal(spec, 2, "（乙）秋夜讀書……7", "- （乙）秋夜讀書……7")
    seal(spec, 2, "（甲）山居雜詠……9", "- （甲）山居雜詠……9")
    seal(spec, 2, "「江上」聽雨……11", "**「江上」聽雨……11**")
    # Two bold spans on one entry (the title's start and the page number);
    # a head of a letter and its mark, and one of a bracket the old CLOSERS
    # lacked (〔), both printed and misread by engine A.
    seal(spec, 2, "晨光初照……6", "**晨光**初照……**6**")
    reprint((2, 8), "雲水禪心", "A. 雲水禪心")
    reprint((2, 10), "竹影清風", "〔一〕竹影清風")
    # Engine A read no bracket before 乙 (its closing partner it read), no
    # head before 雲水禪心, and 〔 as [.
    spec[2]["aEdits"] = [("「江上」聽雨……11", "「汪上」聽雨……11"), ("（乙）秋夜讀書……7", "乙）秋夜讀書……7"),
                        ("A. 雲水禪心……8", "雲水禪心……8"), ("〔一〕竹影清風……10", "[一〕竹影清風……10")]
    # A bold entry; the page number of an entry sealed at the head of the
    # next line; an entry printed with an enumerator of two figures; a
    # figure at a line's head that no engine read at the end of the line
    # before.
    seal(spec, 3, "晨鐘暮鼓……13", "**晨鐘暮鼓……13**")
    seal(spec, 3, "田園小記……15\n\n松下問答……18", "田園小記……\n\n15松下問答……18")
    seal(spec, 3, "漁舟唱晚……20", "3 漁舟唱晚……20")
    # Both engines read the first two entries on one line (the 13 ending
    # the first stands straight before the second title), and the second
    # entry's page number on a line of its own, its leaders lost (the 14
    # stands alone on its line before the third title).
    spec[3]["aEdits"] = [("晨鐘暮鼓……13", "晨鐘暮鼓……13 雲外鐘聲"), ("雲外鐘聲……14", "14")]
    spec[3]["bEdits"] = [("晨鐘暮鼓……13\n雲外鐘聲……14\n", "晨鐘暮鼓……13 雲外鐘聲\n14\n")]
    return spec


def paged_checks(tmp: Path) -> None:
    spec = paged_spec()
    # The forms stage, last, deletes the emphasis neither engine read (K12):
    # off here, so that what S11 leaves shows; on, every run of it goes.
    whole = repair(tmp, spec, "paged-forms")
    check(whole.status == 0 and "**" not in whole.pages[2] and "**" not in whole.pages[3]
          and whole.rules_on("K12") and set(whole.rules_on("K12")) <= {2, 3},
          f"K12: the forms stage deletes the emphasis S11 leaves on the contents ({whole.rules_on('K12')})")
    result = repair(tmp, spec, "paged", "--no-forms")
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"paged contents: the run exits 0 and its log replays to its output (exit {result.status})")
    check(result.report["gates"].get("M-6") is True and 2 in result.rules_on("S11"),
          f"paged contents: the contents are found and rebuilt ({result.report['gates'].get('M-6')})")
    first, second = result.pages[2].split("\n"), result.pages[3].split("\n")
    check("（甲）山居雜詠……9" in first,
          f"S11: the page number that ends the line before is not written as the entry's number, the bullet "
          f"goes, and the bracket that opens the entry, read by engine A at the line's head, stays "
          f"({[line for line in first if line.strip()]})")
    check("（乙）秋夜讀書……7" in first,
          "S11: an opening bracket engine A did not read stays where the line holds its closing partner")
    check("**「江上」聽雨……11**" in first,
          "S11: with engine A not placed at the title, the mark and the emphasis the page holds before it stay")
    check("**春日**遊記……5" in first, "S11: emphasis with no partner at the line's end stays")
    check("**晨光**初照……**6**" in first,
          f"S11: two bold spans on one entry are no wrapping pair: both stay whole ({[line for line in first if '晨光' in line]})")
    check("A. 雲水禪心……8" in first,
          "S11: a letter at the head is print, and the mark beside it stays where engine A missed the letter")
    check("〔一〕竹影清風……10" in first,
          "S11: an opening 〔 engine A read as [ stays where the line holds its 〕")
    check("晨鐘暮鼓……13" in second and "**" not in result.pages[3],
          f"S11: the emphasis that wraps an entry, which engine A did not write, goes - both markers "
          f"({[line for line in second if line.strip()]})")
    check("雲外鐘聲……14" in second and "13 雲外鐘聲" not in result.pages[3],
          "S11: the figure both engines read straight before a title with no line break, which ends the line "
          "above on the page, is that line's page number and is not written")
    received = next((r for r in result.log if r["rule"] == "S11" and r["after"] == "田園小記……15"), None)
    check("田園小記……15" in second and "松下問答……18" in second and "14 田園小記" not in result.pages[3]
          and received is not None and received["evidence"]["numbers"] == {},
          f"S11: a figure at a line's head that both engines read at the end of the line before goes there; "
          f"the 14 both engines read alone on its line above the title, which ends the line above on the page, "
          f"is no source ({received and received['evidence']})")
    check("3 漁舟唱晚……20" in second,
          "S11: a figure the page holds at the head of a line stays where no number is written")
    check("1.1 竹林清話……19" in second,
          "S11: an enumerator of two figures is no number: it stays whole")
    numbered = [r["after"] for r in result.log if r["rule"] == "S11" and r["evidence"].get("number") is not None]
    check(not numbered, f"S11: no entry of a contents that numbers none gets a number ({numbered})")
    written = [r["after"] for r in result.log if r["rule"] == "S11" and r["evidence"].get("kind") == "entry"]
    check(written == ["（乙）秋夜讀書……7", "（甲）山居雜詠……9", "晨鐘暮鼓……13", "田園小記……15", "松下問答……18"],
          f"S11: it writes nothing else on the entries ({written})")
    rest = again(tmp, "paged", result)
    check(not rest, f"paged contents: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")


# --- unit titles printed over two lines ------------------------------------------------

def split_spec(titles: dict[int, str]) -> dict[int, dict[str, Any]]:
    """The made-up book with a contents of the paged kind (no entry numbered,
    each line ending with leaders and its unit's page) and a unit on every
    body page, each contents page then listing eight (so that it still maps,
    M-6, with three of its lines matching no line of the body); TITLES gives
    some units a longer title, printed as one line in the contents and as
    one heading in the body until the check sets them over two lines."""
    spec = fx.make_spec()
    units = {5: "春日遊記", 6: "晨光初照", 7: "秋夜讀書", 8: "雲水禪心", 9: "山居雜詠", 10: "竹影清風",
             11: "江上聽雨", 12: "古寺鐘聲", 13: "晨鐘暮鼓", 14: "雲外鐘聲", 15: "田園小記", 16: "漁歌晚唱",
             17: "柴門犬吠", 18: "松下問答", 19: "竹林清話", 20: "漁舟唱晚"} | titles
    for scan, title in units.items():
        items = spec[scan]["items"]
        k = next((i for i, item in enumerate(items) if item[0] == "heading"), None)
        if k is None:
            items.insert(1, ("heading", title, 3))
        else:
            items[k] = ("heading", title, 3)
    spec[2]["items"] = [("heading", "青山文叢目次", 2), ("heading", "甲編 詩文", 3)] + \
        [("line", f"{units[scan]}……{scan}") for scan in range(5, 13)]
    spec[3]["items"] = [("heading", "乙編 詩文", 3)] + [("line", f"{units[scan]}……{scan}") for scan in range(13, 21)]
    return spec


def two_lines(spec: dict[int, dict[str, Any]], scan: int, title: str, cut: int, second: str) -> None:
    """Item TITLE of SCAN (a heading or a contents line) printed over two
    lines, cut after CUT characters: the second a SECOND item (`heading`,
    `line`)."""
    items = spec[scan]["items"]
    k = next(i for i, item in enumerate(items) if item[1].startswith(title))
    item = items[k]
    items[k:k + 1] = [(item[0], item[1][:cut], *item[2:]), (second, item[1][cut:], *item[2:])]


def date_run_on(spec: dict[int, dict[str, Any]], scan: int, lead: str, date: str) -> None:
    """SCAN's unit (on the first contents page) with its DATE line set as a
    heading after its title, and its contents line holding that date after
    its page number, LEAD (no leaders) before the figure."""
    title = heading_of(spec, scan)
    items = spec[scan]["items"]
    items.insert(next(i for i, item in enumerate(items) if item[0] == "heading") + 1, ("heading", f"{date} 軍錄", 2))
    items = spec[2]["items"]
    k = next(i for i, item in enumerate(items) if item[1].startswith(title))
    items[k] = ("line", f"{title}{lead}{scan}{date}")


def split_checks(tmp: Path) -> None:
    # A unit title printed as two headings (scan 13); a contents entry
    # printed over two lines, its unit one heading, a date inside the second
    # half (scan 18), and one whose second line is a character and its page
    # number (scan 20); both at once, cut at other places (scan 9).
    spec = split_spec({13: "晨鐘暮鼓山寺早課", 18: "松下問答五月七日夜話", 9: "山居雜詠溪橋煙雨", 20: "漁舟唱晚江村夜泊摺"})
    two_lines(spec, 13, "晨鐘暮鼓", 4, "heading")
    two_lines(spec, 3, "松下問答", 4, "line")
    two_lines(spec, 3, "漁舟唱晚", 8, "line")
    two_lines(spec, 9, "山居雜詠", 4, "heading")
    two_lines(spec, 2, "山居雜詠", 6, "line")
    result = repair(tmp, spec, "split")
    joins = [(r["page"], r["evidence"]["side"]) for r in result.log if r["rule"] == "S14"]
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"split titles: the run exits 0 and its log replays to its output (exit {result.status})")
    check("### 晨鐘暮鼓山寺早課" in result.pages[13].split("\n") and "### 山寺早課" not in result.pages[13],
          f"S14: a unit title printed as two headings is one heading "
          f"({[line for line in result.pages[13].split(chr(10)) if line.startswith('#')]})")
    check("松下問答五月七日夜話……18" in result.pages[3].split("\n"),
          f"S14: a contents entry printed over two lines, the second ending with its page number, is one line "
          f"({[line for line in result.pages[3].split(chr(10)) if line.strip()]})")
    check("### 松下問答五月七日夜話" in result.pages[18].split("\n"),
          f"S14: the joined contents line is read as one entry: S7 does not take the date inside its second half "
          f"for the date after the title ({[line for line in result.pages[18].split(chr(10)) if line.strip()][:2]})")
    check("### 山居雜詠溪橋煙雨" in result.pages[9].split("\n") and "山居雜詠溪橋煙雨……9" in result.pages[2].split("\n"),
          "S14: a title printed over two lines in the body and in the contents, cut at other places, is one "
          "heading and one contents line, the heading at the units' level")
    check(sorted(joins) == [(2, "contents"), (3, "contents"), (3, "contents"), (9, "body"), (13, "body")]
          and "漁舟唱晚江村夜泊摺……20" in result.pages[3].split("\n"), f"S14: it joins those five and nothing else ({joins})")
    unmatched = json.loads((result.out / "lint.json").read_text(encoding="utf-8"))["after"]["hits"]["TOC-UNMATCHED"]
    check(not unmatched, f"the lint counts the two rows of a joined contents line as one entry ({unmatched})")
    lost = [d["detail"] for d in result.doubts if d["kind"] == "unit-not-in-scan"]
    check(not lost, f"split titles: every entry has its unit ({lost})")
    rest = again(tmp, "split", result)
    check(not rest, f"split titles: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")
    # A contents entry printed over two lines whose unit's title the body
    # prints over two lines too, cut at another place, the second a plain
    # line and its page number bracketed (scan 15): the book model reads the
    # entry as one (M-6), so S14 joins both - read as two entries, the first
    # half has no unit and neither is joined.  An entry printed over two
    # lines whose second half the contents misread, its unit one heading
    # (scan 20): the model reads it as two (the body does not hold the two
    # halves as read), and S14 joins it on the first half's unit.  The
    # look-alikes: a date line with no page number over the next title, the
    # body printing that date straight before the title (scan 10): the body
    # breaks its line there, so they are two lines; and a group line over
    # an entry (scan 16), the body printing it on its own line over the
    # title and naming the two in one sentence (scan 12): a title opens its
    # line, the sentence is no evidence.
    spec = split_spec({15: "田園小記春耕秋收", 10: "竹影清風松間明月", 20: "漁舟唱晚江村夜泊"})
    two_lines(spec, 15, "田園小記", 4, "line")
    two_lines(spec, 3, "田園小記", 6, "line")
    two_lines(spec, 10, "竹影清風", 4, "line")
    two_lines(spec, 3, "漁舟唱晚", 4, "line")
    items = spec[3]["items"]
    items[items.index(("line", "秋收……15"))] = ("line", "秋收……（15）")
    items[items.index(("line", "江村夜泊……20"))] = ("line", "江村夜泣……20")
    items.insert(next(i for i, item in enumerate(items) if item[1].startswith("漁歌晚唱")), ("line", "丙輯雜錄"))
    items = spec[16]["items"]
    items.insert(next(i for i, item in enumerate(items) if item[0] == "heading"), ("line", "丙輯雜錄"))
    items = spec[12]["items"]
    items.insert(max(i for i, item in enumerate(items) if item[0] == "para"),
                 ("para", "本書所收各篇，丙輯雜錄，漁歌晚唱一篇最早寫成。", False))
    items = spec[10]["items"]
    items.insert(next(i for i, item in enumerate(items) if item[0] == "heading"), ("para", "三年五月九日", False))
    items = spec[2]["items"]
    items.insert(next(i for i, item in enumerate(items) if item[1].startswith("竹影清風")), ("line", "三年五月九日"))
    result = repair(tmp, spec, "split-wrapped")
    joins = sorted((r["page"], r["evidence"]["side"]) for r in result.log if r["rule"] == "S14")
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    rows = [row["text"] for run in model["contents"]["runs"] for row in run["entries"]]
    check(result.status == 0 and "田園小記春耕秋收" in rows and "秋收" not in rows and "三年五月九日" in rows
          and "丙輯雜錄" in rows and "漁歌晚唱" in rows,
          f"M-6: a title printed over two lines on a contents page, the second ending with its page number, is one "
          f"entry; a date line or a group line over the next title is not ({[r for r in rows if len(r) > 3]})")
    check("田園小記春耕秋收……（15）" in result.pages[3].split("\n")
          and "### 田園小記春耕秋收" in result.pages[15].split("\n"),
          f"S14: a contents entry printed over two lines whose unit's title the body prints over a heading and a "
          f"plain line, cut at another place, is one contents line and one heading "
          f"({[line for line in result.pages[15].split(chr(10)) if line.strip()][:2]})")
    check("漁舟唱晚江村夜泣……20" in result.pages[3].split("\n") and "### 漁舟唱晚江村夜泊" in result.pages[20].split("\n"),
          f"S14: an entry printed over two lines whose second half the contents misread is one contents line, "
          f"joined on its first half's unit ({joins})")
    check("三年五月九日" in result.pages[2].split("\n") and "竹影清風松間明月……10" in result.pages[2].split("\n")
          and "### 竹影清風松間明月" in result.pages[10].split("\n")
          and joins == [(3, "contents"), (3, "contents"), (10, "body"), (15, "body")],
          f"S14: a date line is not joined to the title after it where the body prints the date straight before "
          f"the title; the title's unit is joined ({joins})")
    check("丙輯雜錄" in result.pages[3].split("\n") and "漁歌晚唱……16" in result.pages[3].split("\n")
          and "### 漁歌晚唱" in result.pages[16].split("\n"),
          f"S14: a group line is not joined to the entry after it where a sentence of the body names the two "
          f"({[line for line in result.pages[3].split(chr(10)) if line.strip()]})")
    lost = [d["detail"] for d in result.doubts if d["kind"] == "unit-not-in-scan"]
    unmatched = json.loads((result.out / "lint.json").read_text(encoding="utf-8"))["after"]["hits"]["TOC-UNMATCHED"]
    check(not lost and not unmatched, f"wrapped entries: every entry has its unit, and the lint finds each ({lost}, "
                                      f"{unmatched})")
    rest = again(tmp, "split-wrapped", result)
    check(not rest, f"wrapped entries: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")
    # A title printed as a heading and a plain line (scan 15); the
    # look-alikes: a title whose second half opens the paragraph after it
    # (scan 11); an entry with its page number over a line with none (a
    # date), which the unit's heading holds after the title (scan 14); an
    # entry that lost its page number over a one-character entry with no
    # unit, which its unit's heading does not go on with (scan 10).
    spec = split_spec({15: "田園小記春耕秋收", 11: "江上聽雨夜泊楓橋", 14: "雲外鐘聲三年五月七日", 10: "竹影清風松間明月"})
    two_lines(spec, 15, "田園小記", 4, "line")
    items = spec[11]["items"]
    k = next(i for i, item in enumerate(items) if item[0] == "heading")
    items[k:k + 2] = [("heading", "江上聽雨", 3), ("para", "夜泊楓橋" + items[k + 1][1], False)]
    two_lines(spec, 3, "雲外鐘聲", 4, "line")
    items = spec[3]["items"]
    k = next(i for i, item in enumerate(items) if item[1].startswith("雲外鐘聲"))
    items[k:k + 2] = [("line", "雲外鐘聲……14"), ("line", "三年五月七日")]
    items = spec[2]["items"]
    items.insert(next(i for i, item in enumerate(items) if item[1].startswith("竹影清風")) + 1, ("line", "跋……11"))
    seal(spec, 2, "竹影清風松間明月……10", "竹影清風松間明月")
    result = repair(tmp, spec, "split-line")
    joins = [(r["page"], r["evidence"]["side"]) for r in result.log if r["rule"] == "S14"]
    check(result.status == 0 and "### 田園小記春耕秋收\n" in result.pages[15],
          f"S14: a unit title printed as a heading and the plain line after it is one heading "
          f"({[line for line in result.pages[15].split(chr(10)) if line.strip()][:2]})")
    check("### 江上聽雨\n\n夜泊楓橋" in result.pages[11] and joins == [(15, "body")]
          and any("江上聽雨夜泊楓橋" in d["detail"] for d in result.doubts if d["kind"] == "unit-not-in-scan"),
          f"S14: a title whose second half opens a paragraph is left (a heading would take the paragraph), its "
          f"entry a `unit-not-in-scan` doubt ({joins})")
    check("雲外鐘聲……14" in result.pages[3].split("\n") and "三年五月七日" in result.pages[3].split("\n"),
          f"S14: an entry that ends with its page number and the line with none after it (a date the unit's "
          f"heading holds after the title) stay two lines ({[l for l in result.pages[3].split(chr(10)) if l][:4]})")
    check("跋……11" in result.pages[2].split("\n") and "明月跋" not in result.pages[2],
          "S14: an entry that lost its page number and a one-character entry after it, which its unit's heading "
          "does not go on with, stay two lines")
    rest = again(tmp, "split-line", result)
    check(not rest, f"split titles (a plain line): the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule, e.after) for e in rest]})")
    # More look-alikes, each left as it is with its entry a doubt: two
    # headings of one title with a mark line between (scan 12); a title the
    # engines read two characters short, its date written as a heading after
    # it (scan 14); two whole contents lines, each with its page number, the
    # first unit's heading going on with the second's title (scan 7).  And
    # not joined either, but with its unit: a title and its date heading
    # whose contents line ran the date line on after the leaders and the
    # page number (scan 16: the book model reads the date apart).
    long = "福建水師提督柴大紀奏拿獲奸細及近日戰況摺"
    spec = split_spec({12: "古寺鐘聲山門晚眺", 14: long, 16: "漁歌晚唱山村夜話之記", 7: "秋夜讀書燈下閒話"})
    two_lines(spec, 12, "古寺鐘聲", 4, "heading")
    seal(spec, 12, "### 古寺鐘聲\n\n### 山門晚眺", "### 古寺鐘聲\n\n* * *\n\n### 山門晚眺")
    items = spec[14]["items"]
    k = next(i for i, item in enumerate(items) if item[0] == "heading")
    items[k:k + 1] = [("heading", long[:-2], 3), ("heading", "乾隆五十二年十一月初二日批 軍錄", 2)]
    items = spec[16]["items"]
    k = next(i for i, item in enumerate(items) if item[0] == "heading")
    items.insert(k + 1, ("heading", "乾隆五十二年五月二十日批 軍錄", 2))
    items = spec[3]["items"]
    k = next(i for i, item in enumerate(items) if item[1].startswith("漁歌晚唱"))
    items[k] = ("line", "漁歌晚唱山村夜話之記……16乾隆五十二年五月二十日批")
    items = spec[2]["items"]
    k = next(i for i, item in enumerate(items) if item[1].startswith("秋夜讀書"))
    items[k:k + 1] = [("line", "秋夜讀書……7"), ("line", "燈下閒話……7")]
    result = repair(tmp, spec, "split-guards")
    joins = [(r["page"], r["evidence"]["lines"]) for r in result.log if r["rule"] == "S14"]
    lost = " ".join(d["detail"] for d in result.doubts if d["kind"] == "unit-not-in-scan")
    check(result.status == 0 and not joins, f"S14 joins none of the look-alikes ({joins})")
    check(re.search(r"# 古寺鐘聲\n\n\* \* \*\n\n#+ 山門晚眺\n", result.pages[12]) and "古寺鐘聲山門晚眺" in lost,
          "S14: two headings with a mark line between stay apart, the mark line kept")
    check(not any("戰乾隆" in line for line in result.pages[14].split("\n")) and long in lost
          and any(line.endswith(" 乾隆五十二年十一月初二日批 軍錄") for line in result.pages[14].split("\n")),
          f"S14: a title the engines read short is not joined to the date heading after it "
          f"({[line for line in result.pages[14].split(chr(10)) if line.startswith('#')]})")
    check(re.search(r"# 漁歌晚唱山村夜話之記\n", result.pages[16]) and "漁歌晚唱山村夜話之記" not in lost,
          "S14: a title is not joined to its date heading where its contents line ran the date on after leaders "
          "and the page number: the book model reads the date apart, and the entry finds its unit")
    check("秋夜讀書……7" in result.pages[2].split("\n") and "燈下閒話……7" in result.pages[2].split("\n")
          and "燈下閒話" in lost, "S14: a contents line that ends with its page number is not joined to the next")
    # A contents page whose printed lines - a title with its leaders and
    # page, the date line under it - the OCR ran together, as engine A read
    # them apart: the date before the next title (scan 16's), a bullet with
    # a `<br>` between date and title (17's) or title and date (18's), and
    # one line holding two entries (19's, 20's), the second title printed
    # over two lines with a `<br>` at the wrap (no date beside it: one
    # title).  The book model reads its lines apart, so every title is an
    # entry and S14 joins the units' titles set over two headings; S15 cuts
    # those lines where engine A read them apart, and S11 keeps every
    # character of them.  The look-alike: a title and its date
    # heading whose contents line ran the date on after the page number with
    # no leaders (scan 11: the entry holds the date) are not joined, the
    # entry a doubt.
    titles = {16: "漁歌晚唱山村夜話", 17: "柴門犬吠籬外春風", 18: "松下問答江亭夜話", 20: "漁舟唱晚江村夜泊"}
    dates = {15: "三年五月十五日", 16: "三年五月十六日批", 18: "三年五月十八日", 19: "三年五月十九日"}
    spec = split_spec(titles)
    for scan, title in titles.items():
        two_lines(spec, scan, title, 4, "heading")
    date_run_on(spec, 11, " ", "三年五月十一日批")
    items = spec[3]["items"]
    for scan, date in dates.items():
        k = next(i for i, item in enumerate(items) if item[1].endswith(f"……{scan}"))
        items.insert(k + 1, ("line", date))
        body = spec[scan]["items"]
        k = max(i for i, item in enumerate(body) if item[0] == "heading")
        body.insert(k + 1, ("para", date, False))
    run_together = {"三年五月十五日\n\n漁歌晚唱山村夜話……16": "三年五月十五日漁歌晚唱山村夜話……16",
                    "三年五月十六日批\n\n柴門犬吠籬外春風……17": "- 三年五月十六日批<br>柴門犬吠籬外春風……17",
                    "松下問答江亭夜話……18\n\n三年五月十八日": "- 松下問答江亭夜話……18<br>三年五月十八日",
                    "竹林清話……19\n\n三年五月十九日\n\n漁舟唱晚江村夜泊……20":
                        "竹林清話……19三年五月十九日漁舟唱晚<br>江村夜泊……20"}
    for old, new in run_together.items():
        seal(spec, 3, old, new)
    result = repair(tmp, spec, "split-dated")
    joins = sorted(r["page"] for r in result.log if r["rule"] == "S14")
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    check(result.status == 0 and model["contentsPages"] == [2, 3] and joins == [16, 17, 18, 20]
          and all(f"### {title}" in result.pages[scan].split("\n") for scan, title in titles.items()),
          f"S14: the titles a contents page lists on lines the OCR ran together with their dates are joined, the "
          f"page read as contents ({model['contentsPages']}, {joins})")
    lines = [line for line in result.pages[3].split("\n") if line]
    cut = ["三年五月十五日", "漁歌晚唱山村夜話……16", "三年五月十六日批", "柴門犬吠籬外春風……17", "松下問答江亭夜話……18",
           "三年五月十八日", "竹林清話……19", "三年五月十九日", "漁舟唱晚<br>江村夜泊……20"]
    check(lines[-len(cut):] == cut and sum(r["rule"] == "S15" for r in result.log) == 5,
          f"S15: a contents line holding more than one printed line is cut where engine A read the line break "
          f"(the date's 批 stays with it, a `<br>` there goes), not inside a title; S11 keeps every character "
          f"({lines})")
    lost = [d["entry"] for d in result.doubts if d["kind"] == "unit-not-in-scan"]
    check(lost == ["江上聽雨三年五月十一日批"],
          f"split titles (contents run together): every entry has its unit, but the one that holds its date ({lost})")
    check(re.search(r"# 江上聽雨\n", result.pages[11]),
          "S14: a title is not joined to its date heading on the evidence of a contents line that holds the date "
          "after its page number with no leaders")
    rest = again(tmp, "split-dated", result)
    check(not rest, f"split titles (contents run together): the stages run again on their own output change "
                    f"nothing ({[(e.page, e.rule, e.after) for e in rest]})")
    # The same look-alike with one '．' before the figure (no leaders), on
    # its own: next to another entry that holds its date, the contents page
    # would map too few of its characters (M-6).
    spec = split_spec({})
    date_run_on(spec, 11, "．", "三年五月十一日批")
    result = repair(tmp, spec, "split-dot")
    check(result.status == 0 and not result.rules_on("S14") and re.search(r"# 江上聽雨\n", result.pages[11]),
          f"S14: nor where one '．' stands before the figure, the date run on after it ({result.rules_on('S14')})")
    # Two more contents lines the OCR ran together.  A date, its 批 and the
    # next title with no `<br>` (the book model cuts before 批; engine A
    # read the line break after it): cut after 批.  A title printed over two
    # lines, one character on the second, its date line run on after the
    # page number, engine A misreading the date's first character: engine A
    # read no line break between two characters it shares with the page at
    # or after the model's cut, so the line stays - not cut at the title's
    # own wrap before it.
    spec = split_spec({19: "竹林清話山居雅集"})
    items = spec[3]["items"]
    for scan, date in ((13, "三年五月十三日批"), (19, "三年五月十九日")):
        items.insert(next(i for i, item in enumerate(items) if item[1].endswith(f"……{scan}")) + 1, ("line", date))
        body = spec[scan]["items"]
        body.insert(max(i for i, item in enumerate(body) if item[0] == "heading") + 1, ("para", date, False))
    two_lines(spec, 3, "竹林清話山居雅集", 7, "line")
    seal(spec, 3, "三年五月十三日批\n\n雲外鐘聲……14", "三年五月十三日批雲外鐘聲……14")
    seal(spec, 3, "竹林清話山居雅\n\n集……19\n\n三年五月十九日", "竹林清話山居雅集……19三年五月十九日")
    spec[3]["aEdits"] = [("三年五月十九日", "王年五月十九日")]
    result = repair(tmp, spec, "split-misread")
    lines = [line for line in result.pages[3].split("\n") if line]
    check(result.status == 0 and "三年五月十三日批" in lines and "雲外鐘聲……14" in lines
          and "竹林清話山居雅集……19三年五月十九日" in lines and len(result.rules_on("S15")) == 1,
          f"S15: a date's 批 run on into the next title stays with its date; no cut at a title's own wrap where "
          f"engine A misread the character after the model's cut ({lines}, {result.rules_on('S15')})")
    # The lint reads a row and the row after it on one line as one entry
    # only where the line holds both whole: not a short row at the end of a
    # longer line (the contents title at the end of the book's title).
    found = lint.locate_rows(["# 青山文叢第一卷目錄第一章", "目錄", "第一章……1"], [{"text": "目錄"}, {"text": "第一章"}])
    joined = lint.locate_rows(["山居雜詠溪橋煙雨……9"], [{"text": "山居雜詠"}, {"text": "溪橋煙雨"}])
    check(found == [2, 3] and joined == [1, 1], f"locate_rows: two rows on one line only where it holds both "
                                                f"whole ({found}, {joined})")
    # Rows the book model read on one line, cut apart since (S15): each on
    # the line that holds its last character (the date's 奉朱批, which the
    # model's cut gave the title after it, moves no row); the last of them
    # and the row after, joined since (S14), on one line.
    cut = lint.locate_rows(["竹林清話……19", "", "三年五月十九日奉朱批", "", "漁舟……20"],
                           [{"text": t, "line": 1} for t in ("竹林清話", "三年五月十九日", "奉朱批漁舟")])
    joined = lint.locate_rows(["竹林清話……19", "", "三年五月十九日", "", "漁舟唱晚江村夜泊……20"],
                              [{"text": "竹林清話", "line": 1}, {"text": "三年五月十九日", "line": 1},
                               {"text": "漁舟唱晚", "line": 1}, {"text": "江村夜泊", "line": 3}])
    check(cut == [1, 3, 5] and joined == [1, 3, 5, 5],
          f"locate_rows: rows read on one line, cut apart, each on its own line ({cut}, {joined})")
    # A row the book model reads printed over two lines (M-6), before S14
    # joins them: on the line holding its last character, as a cut row.
    wrapped = lint.locate_rows(["田園小記春耕", "", "秋收……15", "", "三年五月九日"],
                               [{"text": "田園小記春耕秋收", "line": 1}, {"text": "三年五月九日", "line": 5}])
    check(wrapped == [3, 5], f"locate_rows: a row printed over two lines, on the line of its end ({wrapped})")
    # A space between the joined lines only between two letters of an
    # alphabet: not between kana.
    texts = ["### 日本語の\n\n### はなし\n", "### Spring\n\n### Rain\n"]
    spaces = [rs.joined(text, rs.line_spans(text)[0], rs.line_spans(text)[2])[2] for text in texts]
    check(spaces == ["", " "], f"S14: no space between kana, one between Latin words ({spaces})")


# --- headings --------------------------------------------------------------------------

def headings_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    # K1: the dash engine A read between title and subtitle, lost.
    set_heading(spec, 7, "2 秋夜讀書——在山中作")
    seal(spec, 7, "### 2 秋夜讀書——在山中作", "### 2 秋夜讀書在山中作")
    # Its look-alike: engine A read a stand-in glyph beside its dash, and
    # the page holds two of them (engine B's) where the dash is.
    set_heading(spec, 5, "1 春日遊記——在園中作")
    spec[5]["aEdits"] = [("春日遊記——在", "春日遊記——口在")]
    spec[5]["bEdits"] = [("春日遊記——在", "春日遊記口口在")]
    seal(spec, 5, "### 1 春日遊記——在園中作", "### 1 春日遊記口口在園中作")
    # And a caption whose number engine A read with a hyphen inside, the
    # page without the number: no dash there.
    spec[8]["items"].insert(1, ("heading", "表1-3 竹林人數", 4))
    seal(spec, 8, "#### 表1-3 竹林人數", "#### 表竹林人數")
    # The same beside a printed dash, the page without either.
    spec[6]["items"].insert(1, ("heading", "表1-3——竹林人數", 4))
    seal(spec, 6, "#### 表1-3——竹林人數", "#### 表竹林人數")
    return spec


def headings_checks(tmp: Path) -> None:
    spec = headings_spec()
    result = repair(tmp, spec, "headings", "--no-contents", "--no-levels", "--no-title-page", "--no-paragraphs")
    pages = result.pages
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"headings: the run exits 0 and its log replays to its output (exit {result.status})")
    check("### 2 秋夜讀書——在山中作" in pages[7] and "K1" in result.rules(7),
          f"K1: engine A's dash between the title and its subtitle is written ({result.rules(7)})")
    check("K1" not in [r for p in (9, 13, 15, 18) for r in result.rules(p)],
          "K1: a heading where engine A read no dash is left")
    caption = next(line for line in pages[8].split("\n") if line.startswith("#"))
    check("K1" not in result.rules(8) and "表1-3" in caption,
          f"K1: a hyphen engine A read inside a caption's figures is no dash; the runs stage writes the number "
          f"({caption!r}, {result.rules(8)})")
    caption = next(line for line in pages[6].split("\n") if line.startswith("#"))
    check(caption == "#### 表1-3——竹林人數",
          f"K1: beside a printed dash, that hyphen is still no dash: K1 writes the dash, the runs stage the "
          f"number ({caption!r}, {result.rules(6)})")
    check("口口在園中作" in pages[5] and "K1" not in result.rules(5) and "K1" in result.named(5)
          and 5 in result.doubted("MARK-STANDIN"),
          f"K1: engine A's dash beside a stand-in glyph it read is a `MARK-STANDIN` doubt, not written beside the "
          f"page's stand-ins ({[line for line in pages[5].split(chr(10)) if line.startswith('#')]}, "
          f"{result.named(5)})")
    rest = [e for e in again(tmp, "headings", result) if e.rule == "K1"]
    check(not rest, f"headings: the rules run again on their own output change nothing "
                    f"({[(e.page, e.rule) for e in rest]})")


# --- levels and the title page ---------------------------------------------------------

def title_page() -> dict[str, Any]:
    """The title page printed right to left, set across the page: engine A
    read both lines backwards, engine B the title forwards only; the page
    lost the title and holds the second line backwards.  Its title is the
    running head's stem alone."""
    return {"items": [("heading", fx.HEAD, 1), ("line", "林泉書屋藏版")], "aHorizontal": True,
            "aEdits": [(fx.HEAD, fx.HEAD[::-1]), ("林泉書屋藏版", "版藏屋書泉林")],
            "bEdits": [("林泉書屋藏版\n", "")],
            "sealedEdits": [("# " + fx.HEAD + "\n\n林泉書屋藏版", "版藏屋書泉林")]}


def levels_spec() -> dict[int, dict[str, Any]]:
    spec = fx.make_spec()
    # S13: the title page printed right to left (title_page).
    spec[1].update(title_page())
    # Levels set page by page, each off the scheme.
    seal(spec, 2, "## 青山文叢目次", "# 青山文叢目次")
    seal(spec, 3, "### 乙編 詩文", "## 乙編 詩文")
    seal(spec, 5, "## 甲編", "# 甲編")
    seal(spec, 7, "### 2 秋夜讀書", "## 2 秋夜讀書")
    # A heading of its own inside a unit; a date written as a heading; a
    # lead written as a heading over its paragraph; a heading no rule
    # places.
    spec[6]["items"].insert(2, ("heading", "一 總說", 2))
    spec[11]["items"].insert(2, ("heading", "四年六月八日", 3))
    lead = paragraphs(spec, 12)[1]
    spec[12]["items"][1] = ("para", "甲、春耕" + lead, False)
    seal(spec, 12, "甲、春耕" + lead, "## 甲、春耕\n" + lead)
    # The same, its enumerator's word opening with a numeral (no date).
    lead = paragraphs(spec, 14)[1]
    spec[14]["items"][1] = ("para", "一，五湖" + lead, False)
    seal(spec, 14, "一，五湖" + lead, "## 一，五湖\n" + lead)
    spec[4]["items"].insert(0, ("heading", "序", 2))
    # A numeral alone written as a heading inside a unit (it opens the
    # paragraph under it): no heading of its own.
    spec[8]["items"].insert(2, ("heading", "三", 2))
    return spec


def levels_checks(tmp: Path) -> None:
    spec = levels_spec()
    result = repair(tmp, spec, "levels")
    pages = result.pages
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"levels: the run exits 0 and its log replays to its output (exit {result.status})")
    named = [d["page"] for d in result.doubts if d.get("rule") == "S13" and d["kind"] == "research-front-matter"]
    check(pages[1] == result.sealed[1] and not result.rules(1) and named == [1],
          f"S13: the page printed right to left is left as it is and named in one `research-front-matter` doubt; "
          f"no other page is ({named})")
    check(pages[2].startswith("## 青山文叢目次") and "### 乙編 詩文" in pages[3] and pages[5].startswith("## 甲編")
          and "### 2 秋夜讀書" in pages[7],
          "S5: the contents title and the divider at ##, the group and the unit at ###")
    check("#### 一 總說" in pages[6], f"S5: a heading of its own inside a unit is one below the unit "
                                      f"({[l for l in pages[6].split(chr(10)) if l.startswith('#')]})")
    check("\n四年六月八日\n" in pages[11] and "#" not in pages[11].split("四年六月八日")[0].split("\n")[-1],
          "S5: a date written as a heading is text")
    check("## 甲、春耕" in pages[12] and "S5" not in result.rules(12) and 12 not in [d["page"] for d in result.doubts],
          f"a heading engine A read at the start of a longer block keeps its level, with no doubt (layout alone) "
          f"({result.named(12)})")
    check("## 一，五湖" in pages[14] and "S5" not in result.rules(14),
          f"an enumerator and a word that opens with a numeral is no date line: kept ({result.rules(14)})")
    check("## 三" in pages[8] and 8 in result.doubted("heading-kind-unknown"),
          "a numeral alone written as a heading inside a unit is no heading of its own: its level stays, a "
          "`heading-kind-unknown` doubt")
    check("## 序" in pages[4] and 4 in result.doubted("heading-kind-unknown"),
          f"a heading no rule places keeps its level, a `heading-kind-unknown` doubt "
          f"({result.doubted('heading-kind-unknown')})")
    rest = again(tmp, "levels", result)
    check(not rest, f"levels: the stages run again on their own output change nothing "
                    f"({[(e.page, e.rule) for e in rest]})")


# --- gates -----------------------------------------------------------------------------

def gate_checks(tmp: Path) -> None:
    # Contents numbered by tens: the numbers read are not the entries'
    # places.
    spec = fx.make_spec()
    for scan in spec:
        items = spec[scan]["items"]
        for i, item in enumerate(items):
            if item[0] in ("line", "heading") and item[1][:1].isdigit():
                n, title = item[1].split(" ", 1)
                items[i] = (item[0], f"{int(n) * 10} {title}", *item[2:])
    result = repair(tmp, spec, "tens")
    check(result.status == 0 and any(d.get("gate") == "contents-numbering" for d in result.doubts),
          "contents whose numbers are not their places fail the contents-numbering gate (a `gate-failed` doubt)")
    check(not [r for r in result.log if r["rule"] in ("S11", "S4")],
          f"with the gate failed, no number is written from a place "
          f"({[(r['page'], r['rule']) for r in result.log if r['rule'] in ('S11', 'S4')]})")
    # No contents: the contents pages set as prose, but for a title leaf
    # (scan 3) that prints the cover's three lines again.
    spec = fx.make_spec()
    spec[1]["items"] += [("line", "春山居士著"), ("line", "青山書局印行")]
    spec[2]["items"] = [("para", fx.paragraph(302, 90), False)]
    spec[3]["items"] = [("line", "青山文叢全編"), ("line", "春山居士著"), ("line", "青山書局印行")]
    seal(spec, 7, "### 2 秋夜讀書", "# 2 秋夜讀書")
    result = repair(tmp, spec, "no-contents")
    structural = [r for r in result.log if r["rule"] in ("S9", "S4", "S7", "S11", "S5")]
    check(not result.report["gates"].get("M-6") and not structural and not result.doubted("heading-kind-unknown"),
          f"a book with no contents (M-6 failed) gets no contents or level edit and no heading doubt "
          f"({[(r['page'], r['rule']) for r in structural]})")
    refused = [d for d in result.doubts if d["kind"] == "gate-failed" and d.get("gate") == "M-6" and d.get("page") == 3]
    check(len(refused) == 1 and "one other page" in refused[0]["detail"] and refused[0]["measured"]["pages"] == [3],
          f"a list-like run M-6 refused as contents (the title leaf, every line mapped onto the cover) is a "
          f"`gate-failed` doubt naming it ({[d['detail'] for d in result.doubts if d['kind'] == 'gate-failed']})")


def main() -> int:
    date_checks()
    gap_checks()
    with tempfile.TemporaryDirectory() as tmp:
        contents_checks(Path(tmp))
        bold_checks(Path(tmp))
        contents_heading_checks(Path(tmp))
        unit_marks_checks(Path(tmp))
        paged_checks(Path(tmp))
        split_checks(Path(tmp))
        units_checks(Path(tmp))
        headings_checks(Path(tmp))
        levels_checks(Path(tmp))
        gate_checks(Path(tmp))
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_structure: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
