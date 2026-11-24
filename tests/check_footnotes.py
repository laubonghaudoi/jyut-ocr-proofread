#!/usr/bin/env python3
"""Offline check of the notes written as Markdown footnotes: scripts/_footnotes.py
(finish_book.py's finalize, on the annotated book), _ocr_markdown.place_footnotes
(the marker removal's opt-in --relocate-footnotes) and the final audit.

The footnote rules are the benchmark's (user decision 8, 2026-11-14:
jyut-ocr-bench INSTRUCTIONS.md 腳註, docs/scoring.md #7): each call `[^L]`
where its number is printed, its note `[^L]: body`; labels unique in the
document, the printed number kept and a number printed again given a
distinct ID (`[^1-2]`, `[^1-3]` ...); only the calls and notes a page prints
are written, a half without its partner stays as printed; where a definition
stands does not count, so definitions stay where the notes are printed
unless --relocate-footnotes, and labels are 1..n only with
--renumber-footnotes.

No GPU, no model, no corpus.

- convert, on a made-up annotated book: a page restarting at ① and the next
  restarting again (the second page's labels [^1-2], [^2-2]); consecutive
  note lines; a call in a heading; on a page, every number whose calls and
  notes pair is written, and what does not pair stays as printed and is
  listed with why - a call with no note, a note with no call, a key with
  more notes than calls, a number in an HTML table (which keeps its ①); on
  a page all of whose notes are written the rule above its foot notes goes,
  on one with a note left it stays; a [^N] the text already holds is no
  label of another footnote; a note numbered ¹ before a join is a foot
  note to the join as well (one definition of a note number).  Keys: ①,
  ❶, ❿, ⓫, ⑤², <sup>52</sup>, $^{52}$, $^{{①⑨}}$.  A <sup>-numbered note
  converts; a number in a <p> or a multi-line <div> block, inside $…$
  math, in a note's own text or of no value stays as printed, with why; a
  whole math run is one call, written outside the math.
- the contract: a join past foot notes onto a heading, a fence, a rule or
  HTML is refused; onto a list item, a quotation's line or prose it runs.
- place_footnotes, through apply_boundary_contract with relocation: each
  definition right after the block that holds its call - after a heading
  line on its own, after a tight list, after the paragraph a join runs on
  across the page break (not after the cut half), after the definitions
  already there in label order; a label with two calls stays where it is;
  no line lost or doubled; the gap a definition leaves closes up.
- finish_book.py on tests/repair_fixture.py's book with notes on six pages:
  final.md's labels unique, each definition referenced once, none without
  its call, each after its call and followed by a blank line or another
  definition; the paragraph cut across a page break with notes is joined
  past them; the call with no note on a page and the call in an HTML table
  keep their numbers as printed and are one note-unmatched doubt each,
  saying why, a third page that phase 3 doubts the note numbers of none
  (its doubt says it already); final.md holds the lines the marker removal
  gives without the placement, no more, no fewer; the final audit reads
  the copy with the footnotes and finds no low-confidence-bracket and no
  structured-final-stale.  With --relocate-footnotes each definition is
  right after its call's block; with --renumber-footnotes the labels are
  1..n.  Two notes of one number on one page: two footnotes with distinct
  labels, and doubts.md is written (it raised KeyError 'text' before).
- stitch_tables.py then finish_book.py on a table printed over three pages,
  each page's note at its foot beside a line engine A read as a footnote:
  the table merged on its first page, each note left where it is printed;
  the pages are not paired as one (reverted 2026-11-15 by the benchmark's
  primary metric, #2 CER): the first page's call and the one that runs on
  to the second page's note pair, the third page's call and note stay as
  printed, a note-unmatched doubt, and the report has no `stitched` list.

Usage:
    python3 tests/check_footnotes.py
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
import offline  # noqa: E402,F401  (private stores, closed endpoint)
import check_finish_book as cfb  # noqa: E402
import repair_fixture as fx  # noqa: E402
import _footnotes  # noqa: E402
from jyut_ocr.core import ocr_markdown as om  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import finish_book  # noqa: E402

FAILURES: list[str] = []
HEAD = re.compile(r" {0,3}\[\^([^\]\s]+)\]:")
CALL = re.compile(r"\[\^([^\]\s]+)\]")


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def footnote_problems(final: str, *, relocated: bool = False, numbered: bool = False) -> list[str]:
    """What is wrong with FINAL's footnotes: labels not unique (with
    NUMBERED, not 1..n), a definition referenced not exactly once outside
    definitions, a call with no definition, a definition before its call, a
    definition a non-blank line other than a definition follows (it would
    run on into the note); with RELOCATED, a definition not right after its
    call's block (a heading line on its own; only definitions and blank
    lines between) or not a block of its own."""
    lines = final.split("\n")
    heads = {n: m.group(1) for n, line in enumerate(lines) if (m := HEAD.match(line))}
    calls: dict[str, list[int]] = {}
    for n, line in enumerate(lines):
        if n not in heads:
            for m in CALL.finditer(line):
                calls.setdefault(m.group(1), []).append(n)
    labels = list(heads.values())
    problems = []
    if len(set(labels)) != len(labels):
        problems.append(f"labels not unique: {labels}")
    if numbered and sorted(labels, key=lambda label: (len(label), label)) != [str(k) for k in range(1, len(labels) + 1)]:
        problems.append(f"labels not 1..n: {labels}")
    for n, label in heads.items():
        where = calls.get(label, [])
        if len(where) != 1:
            problems.append(f"[^{label}] referenced {len(where)} times")
            continue
        if n <= where[0]:
            problems.append(f"[^{label}]: before its call (line {n + 1}, call on line {where[0] + 1})")
        if n + 1 < len(lines) and lines[n + 1].strip() and not HEAD.match(lines[n + 1]):
            problems.append(f"[^{label}]: a line of text follows it, which would run on into the note")
        if not relocated:
            continue
        end = where[0]
        if not om.HEADING_RE.match(lines[end]):
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1
        if n <= end or any(line.strip() and not HEAD.match(line) for line in lines[end + 1:n]):
            problems.append(f"[^{label}]: not after the block of its call (line {n + 1}, call on line {where[0] + 1})")
        if not (lines[n - 1] == "" and n + 1 < len(lines) and lines[n + 1] == ""):
            problems.append(f"[^{label}]: no blank line before and after it")
    problems += [f"[^{label}] has no definition" for label in calls if label not in labels]
    return problems


def kept(text: str) -> list[str]:
    return sorted(line for line in text.split("\n") if line.strip())


def unplaced(annotated: str, boundaries: dict[int, str]) -> str:
    """The marker removal without the footnotes' placement."""
    place = om.place_footnotes
    om.place_footnotes = lambda lines, breaks=frozenset(): list(lines)
    try:
        return om.apply_boundary_contract(annotated, boundaries)
    finally:
        om.place_footnotes = place


BOOK = """<!-- page_001 -->

# 書名

<!-- page_002 -->

## 一 題目①
首段有註號②，完。

次段③也有，④再一個。

- 清單一項⑤
- 清單二項

---

①題目嘅註。

②首段嘅註。
③次段嘅註。
④次段第二條註。
⑤清單嘅註。

<!-- page_003 -->

前段①完。

後段未完，跨頁②

---

①前段嘅註。

②後段嘅註。

<!-- page_004 -->

續完。

第四頁尾段。

<!-- page_005 -->

<table><tr><td>格①</td></tr></table>

①表格嘅註。

<!-- page_006 -->

甲①乙②丙。

①淨係一條註。

<!-- page_007 -->

丁①戊。

---

①兩條註之一。

①兩條註之二。

<!-- page_008 -->

己庚。

③冇註號嘅註。
"""
LABELS = {1: "start", 2: "structural", 3: "paragraph", 4: "join", 5: "paragraph", 6: "paragraph", 7: "paragraph",
          8: "paragraph"}
FINAL = """# 書名

## 一 題目[^1]
首段有註號[^2]，完。

次段[^3]也有，[^4]再一個。

- 清單一項[^5]
- 清單二項

[^1]: 題目嘅註。

[^2]: 首段嘅註。
[^3]: 次段嘅註。
[^4]: 次段第二條註。
[^5]: 清單嘅註。

前段[^1-2]完。

後段未完，跨頁[^2-2]續完。

[^1-2]: 前段嘅註。

[^2-2]: 後段嘅註。

第四頁尾段。

<table><tr><td>格①</td></tr></table>

①表格嘅註。

甲[^1-3]乙②丙。

[^1-3]: 淨係一條註。

丁①戊。

---

①兩條註之一。

①兩條註之二。

己庚。

③冇註號嘅註。
"""
# The same with --relocate-footnotes: each definition after its call's block.
RELOCATED = """# 書名

## 一 題目[^1]

[^1]: 題目嘅註。

首段有註號[^2]，完。

[^2]: 首段嘅註。

次段[^3]也有，[^4]再一個。

[^3]: 次段嘅註。

[^4]: 次段第二條註。

- 清單一項[^5]
- 清單二項

[^5]: 清單嘅註。

前段[^1-2]完。

[^1-2]: 前段嘅註。

後段未完，跨頁[^2-2]續完。

[^2-2]: 後段嘅註。

第四頁尾段。

<table><tr><td>格①</td></tr></table>

①表格嘅註。

甲[^1-3]乙②丙。

[^1-3]: 淨係一條註。

丁①戊。

---

①兩條註之一。

①兩條註之二。

己庚。

③冇註號嘅註。
"""


def convert_checks(tmp: Path) -> None:
    converted, report = _footnotes.convert(BOOK)
    final = om.apply_boundary_contract(converted, LABELS)
    check(final == FINAL, "every number a page pairs as a footnote, the printed number its label and a number "
                          "printed again a distinct ID ([^1-2]), definitions where the notes are printed, the join "
                          "past them; the rule above a page's notes gone when all are written; what does not pair "
                          "as printed:\n" + final)
    check(not footnote_problems(final), f"labels unique, each definition referenced once, after its call "
                                        f"({footnote_problems(final)})")
    why = {page["page"]: [w["kind"] for w in page["why"]] for page in report["left"]}
    check(report["footnotes"] == 8 and report["pagesWritten"] == 3 and report["rulesRemoved"] == 2
          and report["relabelled"] == 3 and report["crossPage"] == []
          and why == {5: ["note", "html"], 6: ["call"], 7: ["count"], 8: ["note"]},
          f"left as printed, with why: an HTML table's call and its note, a call with no note (its page's other "
          f"call and note written), two notes for one call, a note with no call; two rules removed ({report})")
    relocated = om.apply_boundary_contract(converted, LABELS, relocate_footnotes=True)
    check(relocated == RELOCATED and not footnote_problems(relocated, relocated=True),
          "with --relocate-footnotes each definition after its call's block (a heading line, a tight list, the "
          f"paragraph a join runs on) ({footnote_problems(relocated, relocated=True)}):\n" + relocated)
    check(kept(relocated) == kept(unplaced(converted, LABELS)) == kept(final),
          "the placement keeps every line the marker removal gives, no more, no fewer")
    issues = {issue.code for issue in audit_ocr_markdown.audit_text(final, mode="final").issues}
    check("low-confidence-bracket" not in issues, f"the final audit takes [^N] for no confidence bracket ({issues})")
    renumbered, report = _footnotes.convert(BOOK, renumber=True)
    renumbered = om.apply_boundary_contract(renumbered, LABELS)
    check(not footnote_problems(renumbered, numbered=True) and report["footnotes"] == 8
          and "跨頁[^7]續完" in renumbered,
          f"with --renumber-footnotes the labels are 1..n in the order the calls are read "
          f"({footnote_problems(renumbered, numbered=True)})")

    # Labels skip the [^N] the text holds and this step does not write.
    printed = "<!-- page_001 -->\n\n甲[^1]乙[^2]。\n\n[^1]: 印咗嘅註。\n\n<!-- page_002 -->\n\n丙①丁②。\n\n①註。\n②又註。\n"
    out, report = _footnotes.convert(printed)
    check("丙[^1-2]丁[^2-2]。\n\n[^1-2]: 註。\n[^2-2]: 又註。\n" in out
          and out.startswith("<!-- page_001 -->\n\n甲[^1]乙[^2]。\n\n[^1]: 印咗嘅註。")
          and [(page["page"], [w["kind"] for w in page["why"]]) for page in report["left"]] == [(1, ["call"])],
          f"a [^N] the text holds is no label of another page's footnote, its call with no note listed:\n{out}")

    # One definition of a note number for J, the join and convert.
    sup = "<!-- page_001 -->\n\n甲¹乙，\n\n¹ 上標嘅註。\n\n<!-- page_002 -->\n\n續完。\n"
    out, report = _footnotes.convert(sup)
    check(om.apply_boundary_contract(out, {1: "start", 2: "join"}) == "甲[^1]乙，續完。\n\n[^1]: 上標嘅註。\n"
          and not report["left"], f"a note numbered ¹ before a join is a foot note to the join as well ({report})")
    left = sup.replace("甲¹乙，", "甲¹乙²，")
    out, report = _footnotes.convert(left)
    check(out == left.replace("甲¹", "甲[^1]").replace("¹ 上標", "[^1]: 上標")
          and om.apply_boundary_contract(out, {1: "start", 2: "join"}) == "甲[^1]乙²，續完。\n\n[^1]: 上標嘅註。\n"
          and [w["kind"] for w in report["left"][0]["why"]] == ["call"],
          f"a page with a call left as printed too: the join runs past the definition, which follows the paragraph "
          f"({report})")
    check(om.NOTE_LINE_RE.match("[^3]: 註") and not om.NOTE_LINE_RE.match(" ① 註")
          and _footnotes.NOTE.match("[^3]: 註").end() == 6 and _footnotes.NOTE.match("①: 註").end() == 1,
          "a note's line opens with its number, no indentation; only the colon of [^N]: goes")

    # Keys, whatever the notation.
    check(_footnotes.key("⑤²") == _footnotes.key("⑤②") == _footnotes.key("<sup>52</sup>") == _footnotes.key("$^{52}$")
          == _footnotes.key("[^52]") == "52" and _footnotes.key("$^{{①⑨}}$") == "19"
          and [_footnotes.key(c) for c in "①❶❿⓫"] == ["1", "1", "10", "11"],
          "a note number's key is its glyphs' values, whatever the notation (⑤² as 盜區政區's engines write 52)")
    out, report = _footnotes.convert("<!-- page_001 -->\n\n甲❾乙❿丙⓫。\n\n❾註九。\n\n❿註十。\n\n⓫註十一。\n")
    check(out.endswith("甲[^9]乙[^10]丙[^11]。\n\n[^9]: 註九。\n\n[^10]: 註十。\n\n[^11]: 註十一。\n")
          and not report["left"], f"negative-circled figures past 9 are numbers: the page converts whole ({report})")
    out, report = _footnotes.convert("<!-- page_001 -->\n\n甲<sup>1</sup>。\n\n<sup>1</sup> 註。\n")
    check("甲[^1]。" in out and "[^1]: 註。" in out and not report["left"],
          f"a note numbered <sup>1</sup> is no HTML block ({report})")
    for block in ("<p>甲①</p>\n\n①註。\n", '<div align="center">\n告示①\n</div>\n\n①註。\n'):
        out, report = _footnotes.convert("<!-- page_001 -->\n\n" + block)
        check(out.endswith(block) and "html" in [w["kind"] for w in report["left"][0]["why"]],
              f"a number in a <p> block, or inside a <div> block's lines, stays as printed ({report})")
    for math in ("$^{①⑨}$", "$^{{⑲}}$"):
        out, report = _footnotes.convert(f"<!-- page_001 -->\n\n甲{math}乙。\n\n⑲ 注一。\n")
        check(out.endswith("甲[^19]乙。\n\n[^19]: 注一。\n") and not report["left"],
              f"a call written as math is its whole math run: the label stands outside the math ({out!r})")
    out, report = _footnotes.convert("<!-- page_001 -->\n\n甲$x^{①}$乙。\n\n①注一。\n")
    check(out.endswith("甲$x^{①}$乙。\n\n①注一。\n") and "math" in [w["kind"] for w in report["left"][0]["why"]],
          f"a number inside $…$ math stays as printed ({report})")
    out, report = _footnotes.convert("<!-- page_001 -->\n\n（克拉斯諾亞爾斯克）\\*①。\n\n① 注一。\n")
    check("（克拉斯諾亞爾斯克）\\*[^1]。" in out and "[^1]: 注一。" in out and not report["left"],
          f"an escaped literal asterisk before a call is no math operator ({report})")
    out, report = _footnotes.convert("<!-- page_001 -->\n\na * ①。\n\n① 注一。\n")
    check("a * ①。" in out and "math" in [w["kind"] for w in report["left"][0]["why"]],
          f"an unescaped multiplication asterisk before a call stays math ({report})")
    tbl_one = ("<!-- page_001 -->\n\n| 甲 | 乙① |\n| --- | --- |\n\n<!-- page_002 -->\n\n<!-- page_003 -->\n\n① 註一。\n")
    out, report = _footnotes.convert(tbl_one)
    check("[^1]" not in out and report["footnotes"] == 0,
          f"_table_run_over never pairs key 1 across pages ({report})")
    inside = "<!-- page_001 -->\n\n乙[^1]丙[^2]。\n\n[^1]: 註甲。\n\n[^2]: 同[^1]。\n"
    out, report = _footnotes.convert(inside)
    check(out == inside and [w["kind"] for w in report["left"][0]["why"]] == ["inside"],
          f"a number in a note's own text stays as printed and keeps the label it cites, no crash ({report})")
    key = _footnotes.key
    _footnotes.key = lambda number: "" if "⑨" in number else key(number)
    try:
        out, report = _footnotes.convert("<!-- page_001 -->\n\n甲①乙⑨。\n\n①註一。\n\n⑨註九。\n")
    finally:
        _footnotes.key = key
    check(out.endswith("甲[^1]乙⑨。\n\n[^1]: 註一。\n\n⑨註九。\n")
          and [w["kind"] for w in report["left"][0]["why"]] == ["unread", "unread"],
          f"a number of no value stays as printed, its call and its note, with why; the page's other pair is "
          f"written ({report})")


def contract_checks() -> None:
    # A join past foot notes runs on the line above them: not a heading or
    # other structure (a hand-written join after lines numbered like notes).
    for above, refused in (("## 論文目錄", True), ("```", True), ("***", True), ("<div>", True),
                           ("- 清單項，", False), ("> 引文，", False), ("正文未完，", False)):
        text = f"<!-- page_001 -->\n\n{above}\n\n---\n\n⑲ 論文乙。\n\n<!-- page_002 -->\n\n續文。\n"
        lines = om.split_lines(text)
        problems = om.boundary_contract_problems(lines, om.find_page_markers(lines)[0], {1: "start", 2: "join"})
        check(bool(problems) == refused, f"a join past foot notes onto {above!r}: refused {refused} ({problems})")
    text = "<!-- page_001 -->\n\n## 標題\n\n<!-- page_002 -->\n\n續文。\n"
    lines = om.split_lines(text)
    check(not om.boundary_contract_problems(lines, om.find_page_markers(lines)[0], {1: "start", 2: "join"}),
          "a join with no foot notes skipped is the contract's as before (a hand-written join onto a heading)")


def place_checks() -> None:
    # Two definitions placed after one paragraph, one already there: after
    # it, in label order; a label with two calls, or two definitions, stays.
    text = ("甲[^2]乙[^1]丙。\n\n[^9]: 已經喺度。\n\n丁[^3]。\n\n[^1]: 一。\n\n[^2]: 二。\n\n戊[^4]。\n\n己[^4]。\n\n"
            "[^3]: 三。\n\n[^4]: 四。\n\n[^5]: 五甲。\n\n庚[^5]。\n\n[^5]: 五乙。\n")
    out = "".join(om.place_footnotes(om.split_lines(text)))
    check(out == ("甲[^2]乙[^1]丙。\n\n[^9]: 已經喺度。\n\n[^1]: 一。\n\n[^2]: 二。\n\n丁[^3]。\n\n[^3]: 三。\n\n"
                  "戊[^4]。\n\n己[^4]。\n\n[^4]: 四。\n\n[^5]: 五甲。\n\n庚[^5]。\n\n[^5]: 五乙。\n"),
          "definitions after their call's paragraph, after one already there, in label order; a label with two calls "
          "or two definitions stays:\n" + out)
    out = "".join(om.place_footnotes(["甲[^1]。\n", "\n", "[^1]: 一。\n", "\n", "\0B\0\n", "乙。\n"], {"\0B\0"}))
    check(out == "甲[^1]。\n\n[^1]: 一。\n\0B\0\n乙。\n",
          f"the gap a definition leaves before a verified break closes up to the break, not a blank line ({out!r})")
    text = "[^1]: 喺頭。\n\n甲[^1]。\n\n乙[^2]。\n\n[^2]: 喺尾。\n"
    out = "".join(om.place_footnotes(om.split_lines(text)))
    check(out == "甲[^1]。\n\n[^1]: 喺頭。\n\n乙[^2]。\n\n[^2]: 喺尾。\n",
          "a definition taken from the book's head leaves no blank line, one at its end none either:\n" + out)


def note_spec() -> dict[int, dict[str, Any]]:
    """tests/repair_fixture.py's book with notes: scan 6 calls ① and ② and
    prints both notes on consecutive lines; scan 7 a call in its heading;
    scan 8 calls ① and ② with only note ①, a rule above it; scan 9 calls ①
    in the paragraph under its heading and ② at the end of its last, which
    runs on to scan 10, a rule above its notes; scan 11 a call in an HTML
    table and its note; scan 14 like scan 8, with phase 3's doubt on its note
    numbers."""
    spec = fx.make_spec()

    def call(scan: int, item: int, mark: str, at: int | None = None) -> None:
        kind, text, *rest = spec[scan]["items"][item]
        cut = text.index("，") + 1 if at is None else at
        spec[scan]["items"][item] = (kind, text[:cut] + mark + text[cut:], *rest)

    def notes(scan: int, *texts: str, joined: bool = False) -> None:
        spec[scan]["extraSealed"] += ["\n".join(texts)] if joined else list(texts)
        spec[scan].setdefault("extraBlocks", []).extend(
            {"text": text, "boundingBox": fx.box_of(60, 900 + 30 * k, 16, 200), "blockLabel": "footnote",
             "blockOrder": -1} for k, text in enumerate(texts))

    call(6, 0, "①")
    call(6, 1, "②")
    notes(6, "①" + fx.paragraph(601, 10), "②" + fx.paragraph(602, 10), joined=True)
    kind, text, level = spec[7]["items"][0]
    spec[7]["items"][0] = (kind, text + "①", level)
    notes(7, "①" + fx.paragraph(701, 10))
    call(8, 0, "①")
    call(8, 1, "②")
    spec[8]["extraSealed"].append("---")
    notes(8, "①" + fx.paragraph(801, 10))
    call(9, 1, "①")
    kind, text, flush = spec[9]["items"][-1]
    spec[9]["items"][-1] = (kind, text.rstrip("。") + "②", flush)
    spec[9]["extraSealed"].append("---")
    notes(9, "①" + fx.paragraph(901, 10), "②" + fx.paragraph(902, 10))
    spec[11]["extraSealed"].append("<table><tr><td>" + fx.filler(1101, 4) + "①</td></tr></table>")
    notes(11, "①" + fx.paragraph(1102, 10))
    call(14, 0, "①")
    call(14, 1, "②")
    notes(14, "①" + fx.paragraph(1401, 10))
    spec[14]["sealDoubts"] = [{"kind": "note-markers-unverified", "detail": "NOTE MARKERS: ② read by the engines"}]
    return spec


def finish_checks(tmp: Path) -> None:
    spec = note_spec()
    book = cfb.phase3_book(tmp / "notes", spec=spec)
    code, lines = cfb.finish(book)
    check(code == 0, f"finish_book.py on a book with notes: exit 0 ({lines[:1]})")
    final = (book / "final.md").read_text(encoding="utf-8")
    problems = footnote_problems(final)
    check(not problems and HEAD.findall(final) == ["1", "2", "1-2", "1-3", "1-4", "2-2", "1-5"],
          f"final.md: seven footnotes, the printed numbers their labels and a number printed again a distinct "
          f"ID, each referenced once, none without its call, each after its call, no text running on into one "
          f"({HEAD.findall(final)}, {problems})")
    report = json.loads((book / "finish" / finish_book.FOOTNOTES_JSON).read_text(encoding="utf-8"))
    why = {page["page"]: [w["kind"] for w in page["why"]] for page in report["left"]}
    check(report["footnotes"] == 7 and report["rulesRemoved"] == 2 and report["relabelled"] == 5
          and why == {8: ["call"], 11: ["note", "html"], 14: ["call"]},
          f"finish/footnotes.json: seven footnotes, two rules removed; the call with no note on scans 8 and 14 "
          f"and scan 11's note whose call is in an HTML table left as printed ({report})")
    heading = next(line for line in final.split("\n") if line.startswith("### 2 "))
    check(heading.endswith("[^1-2]") and final.count("\n[^1-2]: ") == 1
          and final.index(heading) < final.index("\n[^1-2]: "),
          f"a call in a heading: [^1-2], its definition after it ({heading})")
    cut = spec[9]["items"][-1][1].rstrip("②")
    run_on = spec[10]["items"][0][1]
    first = spec[9]["items"][1][1]
    check(cut + "[^2-2]" + run_on + "\n\n[^1-4]: " in final and final.index(first.replace("①", "[^1-4]")) <
          final.index(cut), "the paragraph cut by the page break with its notes is joined past them, the "
                            "page's definitions after it")
    check("①" + fx.paragraph(801, 10) not in final and "[^1-3]: " + fx.paragraph(801, 10) in final
          and spec[8]["items"][1][1] in final
          and "①</td>" in final and "①" + fx.paragraph(1102, 10) in final
          and final.count("\n---\n") == 0,
          "scan 8's call with no note stays as printed and its other call and note are written, the rule above "
          "them gone; the page with a call in an HTML table keeps its call and note as printed")
    doubts = json.loads((book / "finish" / finish_book.DOUBTS_JSON).read_text(encoding="utf-8"))
    unmatched = {d["page"]: d["detail"] for d in doubts if d["kind"] == "note-unmatched"}
    check(sorted(unmatched) == [8, 11] and "註號 ②" in unmatched[8] and "冇同號嘅註" in unmatched[8]
          and "HTML" in unmatched[11] and "搵唔到叫佢嘅註號" in unmatched[11]
          and [d["page"] for d in doubts if d["kind"] == "note-markers-unverified"] == [14]
          and "呢版嘅註號同註唔係一個對一個" in (book / "doubts.md").read_text(encoding="utf-8"),
          f"one note-unmatched doubt for each page with a number left as printed, saying why, none on scan 14, "
          f"whose note numbers phase 3 doubts already ({unmatched})")
    source = book / "finish" / finish_book.FIGURES_SOURCE
    boundaries = om.load_boundary_manifest(book / "repaired" / "boundaries.json")
    check(kept(final) == kept(unplaced(source.read_text(encoding="utf-8"), boundaries)),
          "final.md holds the lines the marker removal gives without the placement, no more, no fewer")
    audit = json.loads((book / "finish" / finish_book.AUDIT_NAME).read_text(encoding="utf-8"))
    codes = {error["code"] for error in audit.get("errors") or []}
    check(audit["input_sha256"]["annotated_source"] == cfb.pp.sha256_file(source)
          and not codes & {"low-confidence-bracket", "structured-final-stale"},
          f"the final audit reads the copy with the footnotes: no low-confidence-bracket, no structured-final-stale "
          f"({sorted(codes)})")

    # The opt-in switches.
    book = cfb.phase3_book(tmp / "relocated", spec=note_spec())
    code, lines = cfb.finish(book, "--relocate-footnotes")
    final = (book / "final.md").read_text(encoding="utf-8")
    problems = footnote_problems(final, relocated=True)
    heading = next(line for line in final.split("\n") if line.startswith("### 2 "))
    audit = json.loads((book / "finish" / finish_book.AUDIT_NAME).read_text(encoding="utf-8"))
    check(code == 0 and not problems and len(HEAD.findall(final)) == 7
          and final.split(heading + "\n\n", 1)[-1].startswith("[^1-2]: ")
          and cut + "[^2-2]" + run_on + "\n\n[^2-2]: " in final
          and not {error["code"] for error in audit.get("errors") or []} & {"structured-final-stale"},
          f"--relocate-footnotes: each definition right after its call's block (a heading line, the paragraph a "
          f"join runs on), a blank line before and after, the audit replaying it ({problems})")
    book = cfb.phase3_book(tmp / "renumbered", spec=note_spec())
    code, lines = cfb.finish(book, "--renumber-footnotes")
    final = (book / "final.md").read_text(encoding="utf-8")
    problems = footnote_problems(final, numbered=True)
    check(code == 0 and not problems and len(HEAD.findall(final)) == 7 and cut + "[^6]" + run_on in final,
          f"--renumber-footnotes: labels 1..n in the order the calls are read ({problems})")

    # Two notes of one number on one page: the KeyError 'text' in the doubts
    # step, which left no doubts.md.
    spec = fx.make_spec()
    for item in (0, 1):
        kind, text, *rest = spec[6]["items"][item]
        cut_at = text.index("，") + 1
        spec[6]["items"][item] = (kind, text[:cut_at] + "①" + text[cut_at:], *rest)
    spec[6]["extraSealed"] += ["①" + fx.paragraph(611, 10), "①" + fx.paragraph(612, 10)]
    spec[6].setdefault("extraBlocks", []).extend(
        {"text": text, "boundingBox": fx.box_of(60, 900 + 30 * k, 16, 200), "blockLabel": "footnote",
         "blockOrder": -1} for k, text in enumerate(["①" + fx.paragraph(611, 10), "①" + fx.paragraph(612, 10)]))
    book = cfb.phase3_book(tmp / "same-number", spec=spec)
    code, lines = cfb.finish(book)
    final = (book / "final.md").read_text(encoding="utf-8")
    check(code == 0 and (book / "doubts.md").is_file() and HEAD.findall(final) == ["1", "1-2"]
          and not footnote_problems(final)
          and "[^1]: " + fx.paragraph(611, 10) in final and "[^1-2]: " + fx.paragraph(612, 10) in final,
          f"two notes numbered ① on one page: two footnotes, [^1] and [^1-2] in reading order, and doubts.md is "
          f"written (exit {code}, {HEAD.findall(final)}, {footnote_problems(final)})")


def stitch_checks(tmp: Path) -> None:
    """A table printed over three pages, each page's note at its foot beside
    a line engine A read as a footnote: stitch_tables.py merges the table on
    its first page and leaves each note where it is printed; finish_book.py
    pairs continuation table notes past one page break ONLY when every later
    continuation note also pairs (all-or-nothing tail completeness, so a
    partial prefix conversion cannot reorder converted definitions past
    unconverted numbered notes and hurt #2 CER)."""
    import contextlib
    import io
    import stitch_tables

    def table(row: tuple[str, str]) -> str:
        return "\n".join(["| 年 | 稅 |", "| --- | --- |", f"| {row[0]} | {row[1]} |"])

    spec = fx.make_spec()
    rows = {8: ("一年", "十兩①"), 9: ("二年", "廿兩②"), 10: ("三年", "卅兩③")}
    notes = {8: "①甲書卷一頁二。", 9: "②乙書卷三頁四。", 10: "③丙書卷五頁六。"}
    apparatus = {8: "此數據據原檔推算。", 9: "本表據檔案編製。", 10: "表內數目以兩計。"}
    for scan in (9, 10):
        spec[scan]["items"] = []
    for scan in rows:
        spec[scan]["extraSealed"] += [table(rows[scan]), notes[scan], apparatus[scan]]
        spec[scan].setdefault("extraBlocks", []).extend(
            {"text": text, "boundingBox": fx.box_of(60, 900 + 30 * k, 16, 200), "blockLabel": "footnote",
             "blockOrder": -1} for k, text in enumerate((notes[scan], apparatus[scan])))
    book = cfb.phase3_book(tmp / "stitched", spec=spec)
    with contextlib.redirect_stdout(io.StringIO()):
        code = stitch_tables.main([str(book / "proofread"), "--renders", str(book / "renders"),
                                   "--drafts", str(book / "ocr-paddle"), "--drafts2", str(book / "ocr-hunyuan")])
    pages = {scan: (book / "proofread" / f"page-{scan:04d}.md").read_text(encoding="utf-8") for scan in rows}
    check(code == 0 and all(f"| {a} | {b} |" in pages[8] for a, b in rows.values())
          and pages[9].startswith(notes[9]) and pages[10].startswith(notes[10]),
          "stitch: the table merged on its first page, each continuation page's note left at its foot")
    code, lines = cfb.finish(book)
    final = (book / "final.md").read_text(encoding="utf-8")
    report = json.loads((book / "finish" / finish_book.FOOTNOTES_JSON).read_text(encoding="utf-8"))
    finished = json.loads((book / "finish" / "finish-report.json").read_text(encoding="utf-8"))
    cross = [(c["callPage"], c["notePage"], c["label"]) for c in report["crossPage"]]
    check(code == 0 and report["footnotes"] == 3 and "stitched" not in report
          and "| 三年 | 卅兩[^3] |" in final and "[^3]: 丙書卷五頁六。" in final
          and (8, 10, "3") in cross and not footnote_problems(final)
          and "footnotesStitched" not in json.dumps(finished),
          f"finish_book.py after complete stitch: all 3 stitched table footnotes pair across pages 8->10 "
          f"(exit {code}, {report['footnotes']} footnotes, crossPage {cross}, left {report['left']})")

    # Partial continuation tail guard: when page 10 holds an extra unpaired
    # numbered note ④ with no call in the table, page 10's ③ is NOT converted
    # ahead of ④ (locking in the 97494fd CER guard).
    partial_book = ("<!-- page_008 -->\n\n| 年 | 稅 |\n| --- | --- |\n| 一年 | 十兩① |\n| 二年 | 廿兩② |\n| 三年 | 卅兩③ |\n\n①甲。\n"
                    "\n<!-- page_009 -->\n\n②乙。\n\n<!-- page_010 -->\n\n③丙。\n\n④丁。\n")
    p_out, p_report = _footnotes.convert(partial_book)
    p_cross = [(c["callPage"], c["notePage"], c["label"]) for c in p_report["crossPage"]]
    check(p_report["footnotes"] == 2 and "| 三年 | 卅兩③ |" in p_out and "③丙。" in p_out and "[^3]" not in p_out
          and (8, 10, "3") not in p_cross,
          f"partial stitched table tail leaves ③ unpaired when ④ remains unpaired after it ({p_report})")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="footnotes-") as name:
        tmp = Path(name)
        convert_checks(tmp)
        contract_checks()
        place_checks()
        finish_checks(tmp)
        stitch_checks(tmp)
    print(f"{'FAIL' if FAILURES else 'ok  '} notes as footnotes: {len(FAILURES)} problem(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
