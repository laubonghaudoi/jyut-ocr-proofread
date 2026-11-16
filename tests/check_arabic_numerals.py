#!/usr/bin/env python3
"""Offline checks of --keep-arabic-numerals (on by default since the round-4
benchmark, the user's decision of 2026-09-30; --no-keep-arabic-numerals
turns it off).

No model, no GPU, no corpus.  The merge keeps characters only (CJK), so an
Arabic figure engine A read - a list item's (1), a contents entry's 12, a
heading's 5. - stands in the page only where the punctuator or the structure
pass writes it, and engine B's reading of the figure (a glyph such as 丄 or 工
for 1, 八…一 for its brackets) could stand instead.  With the switch the merge
writes engine A's figure where it is the only reading of its print, and only
a figure that continues a numbering.  The pages are made up, in the shapes
measured on the sealed pages of a book printing Arabic item, contents and
heading numbers:

- a list whose figures engine B read as glyphs it writes for figures and
  marks on this book (stand-ins), and the same list with the switch off;
- contents lines where engine B read no figure at all;
- a figure engine A misread, which breaks the numbering on both sides;
- a folio in a number block (furniture), never a list's number;
- a figure engine B read as a numeral, which 裁決 is asked about as without
  the switch: its answer that the figure is printed writes it, an answer
  that reads the numeral keeps engine B's reading;
- a character only engine B read at a figure's place that is no stand-in,
  and a figure beside a replacement of unequal readings: neither is written,
  and no character both engines read is replaced;
- a structure pass that leaves the figures out: they are written back;
- a punctuator that already wrote a figure, at its place or after the entry;
- the page's budget of questions (--max-adjudications) as with it off;
- resume: a page sealed with it is not current for a run without it.

Usage:
    python3 tests/check_arabic_numerals.py
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stand_in  # noqa: E402  (first: the closed endpoint)
import proofread_pages as pp  # noqa: E402

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


SWITCH: list[str] = []   # the default: on
OFF = ["--no-keep-arabic-numerals"]
BOX = [0.3, 0.1, 0.4, 0.8]
# Prose both engines read alike, so that the pages merge (READING_ORDER_AGREEMENT).
LEAD = "秋收冬藏閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
# And parentheses engine B read as strokes (一), so that its drafts show it writing
# 一 for them on this book (mark_aliases).
PAREN_A, PAREN_B = "律呂（調陽）雲騰（致雨）露結（爲霜）", "律呂一調陽一雲騰一致雨一露結一爲霜一"


def run(pages, extra=(), profile=None, **model):
    """Every page of a made-up book: page -> (record, Markdown), and the run report."""
    with stand_in.book(pages, profile) as root:
        out = stand_in.run(root, stand_in.StandIn(**{"census": 0, **model}),
                           pages=",".join(str(p) for p in sorted(pages)), extra=list(extra))
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return out, report


def figures(record):
    return [(f["figure"], f["kept"], f.get("how") or f.get("why")) for f in record.get("arabicFigures") or []]


def changes(record):
    return [(c["engineA"], c["engineB"], c["after"], c["evidence"]["how"])
            for c in record["decisionChanges"] if c["decision"] == "keepArabicNumerals"]


def asked(record):
    return [(a["draft_reading"], a["writer_reading"]) for a in record["adjudications"]]


# --- Engine A's figures, and which continue a numbering.
blocks = [{"text": "天地玄黃：（1）宇宙洪荒，（2）日月盈昃", "label": "vertical_text"},
          {"text": "12", "label": "number"},
          {"text": "3.辰宿列張", "label": "vertical_text"},
          {"text": "寒來暑往 a1 秋收(4)冬藏", "label": "text"},
          {"text": "1934年閏餘", "label": "text"}]
draft = "\n".join(b["text"] for b in blocks)
found = pp.arabic_figures(blocks, draft)
check("arabic_figures: alone in a gap with marks, in brackets or with a point, as read; not in a number "
      "block, not beside letters, not four digits",
      [(f["figure"], f["gap"], f["block"], f["at"], f["value"]) for f in found],
      [("（1）", 4, 0, 4, 1), ("（2）", 8, 0, 8, 2), ("3.", 12, 2, 0, 3), ("（4）", 22, 3, 6, 4)])
check("arabic_figures: none where the blocks do not spell engine A's text",
      pp.arabic_figures(blocks, draft + "天"), [])
check("figures_confirmed: one less before or one more after",
      pp.figures_confirmed([1, 2, 3], None, None), [True, True, True])
check("figures_confirmed: a misread is not confirmed, nor a figure whose one neighbour it is",
      pp.figures_confirmed([1, 2, 8, 4], None, None), [True, True, False, False])
check("figures_confirmed: a lone figure, by the figures of the pages before and after",
      [pp.figures_confirmed([7], 6, None), pp.figures_confirmed([7], None, 8), pp.figures_confirmed([7], 3, 1)],
      [[True], [True], [False]])
check("figure_neighbours: the last figure before the page and the first after it",
      pp.figure_neighbours({1: [1, 2], 2: [], 4: [3], 5: [4, 5]}, 3), (2, 3))

# --- Writing a figure in a gap, among the marks there.
check("figure_in_gap: the bracket rule's reversed pair around the figure, the colon before",
      pp.figure_in_gap("：）（", "（1）"), "：（1）")
check("figure_in_gap: a pair written in order", pp.figure_in_gap("，（）", "（2）"), "，（2）")
check("figure_in_gap: no marks there", pp.figure_in_gap("", "（3）"), "（3）")
check("figure_in_gap: a closed parenthesis stays before the figure's own pair",
      pp.figure_in_gap("）（）", "（4）"), "）（4）")
check("figure_in_gap: an opening quote after the figure", pp.figure_in_gap("：（「", "（1）"), "：（1）「")
check("figure_in_gap: a figure with a point after a sentence's close", pp.figure_in_gap("。", "10."), "。10.")
check("figure_in_gap: a mark engine A read after the figure stays after it",
      (pp.figure_in_gap("，", "3", "，"), pp.figure_in_gap("！，", "3", "，"), pp.figure_in_gap("：（），", "（1）", "，")),
      ("3，", "！3，", "：（1），"))
check("place_arabic_figures: written, already there, written after the entry, and text not the span",
      [pp.place_arabic_figures("天地：）（玄黃", "天地玄黃", [(2, "（1）")]),
       pp.place_arabic_figures("1.天地玄黃", "天地玄黃", [(0, "1.")]),
       pp.place_arabic_figures("天地玄黃 12", "天地玄黃", [(0, "12")]),
       pp.place_arabic_figures("天地", "天地玄黃", [(0, "1")])],
      [("天地：（1）玄黃", ["written"]), ("1.天地玄黃", ["already"]), ("天地玄黃 12", ["elsewhere"]),
       ("天地", ["unplaced"])])
far = "天地玄黃" + "宇宙洪荒" * 12 + "（6）日月"
check("place_arabic_figures: the same figure farther than NEARBY_WINDOW is another's",
      pp.place_arabic_figures(far, pp.CJK(far), [(0, "（6）")])[1], ["written"])
check("restore_arabic_figures: a figure the structure pass left out is written back at its place",
      pp.restore_arabic_figures("天地\n1.玄黃\n2.宇宙", "## 天地\n\n- 玄黃\n- 2.宇宙", [(2, "1."), (4, "2.")]),
      ("## 天地\n\n- 1.玄黃\n- 2.宇宙", ["restored", "kept"]))
check("restore_arabic_figures: one it moved stays where it is",
      pp.restore_arabic_figures("天地\n1.玄黃", "天地\n玄黃 1.", [(2, "1.")]), ("天地\n玄黃 1.", ["moved"]))
check("figure_verdict: the figure (unused, a figure is no character), nothing printed, a character",
      [pp.figure_verdict({"resolved_from": "engine-A-default", "verdict": "定案：2"}, {"digits": "2"}),
       pp.figure_verdict({"resolved_from": "engine-A-default", "verdict": "定案：（2）（兩個候選皆非）"},
                         {"digits": "2"}),
       pp.figure_verdict({"resolved_from": "adjudicator-empty", "verdict": "定案：無"}, {"digits": "2"}),
       pp.figure_verdict({"resolved_from": "adjudicator", "verdict": "定案：二"}, {"digits": "2"}),
       pp.figure_verdict({"resolved_from": "engine-A-default", "verdict": "定案：3"}, {"digits": "2"}),
       pp.figure_verdict({"resolved_from": "adjudicator-empty-unconfirmed", "verdict": "定案：無"},
                         {"digits": "2"})],
      ["figure", "figure", "nothing", None, None, None])
check("figure_verdict: the answer on the last 定案 line, not 定案： inside the reasoning",
      pp.figure_verdict({"resolved_from": "engine-A-default",
                         "verdict": "構件：略\n由兩橫可以定案：二\n\n定案：2"}, {"digits": "2"}), "figure")
check("figure_stand_ins: the brackets as strokes and a glyph for the figure, never a numeral",
      [pp.figure_stand_ins("一丄一", {"figure": "（1）", "digits": "1"}, {"丄"}, 0),
       pp.figure_stand_ins("丄", {"figure": "（1）", "digits": "1"}, {"丄"}, 2),
       pp.figure_stand_ins("一", {"figure": "（1）", "digits": "1"}, {"一"}, 2),
       pp.figure_stand_ins("盡", {"figure": "（1）", "digits": "1"}, {"丄"}, 2),
       pp.figure_stand_ins("丄丄", {"figure": "1", "digits": "1"}, {"丄"}, 0)],
      [True, True, False, False, False])


# --- A list engine B read as stand-ins, on every page of a made-up book (the
# glyphs are this book's: its drafts show engine B writing them where engine A
# read a mark or a figure).
def listed(n):
    return (LEAD + PAREN_A + f"果珍李柰，菜重芥薑：（{n}）海鹹河淡，（{n + 1}）鱗潛羽翔，（{n + 2}）龍師火帝。",
            LEAD + PAREN_B + "果珍李柰丶菜重芥薑号一丄一海鹹河淡丶八工一鱗潛羽翔丶一了一龍師火帝口")


LIST = {page: ([stand_in.block(listed(1)[0], BOX)], listed(1)[1]) for page in (1, 2, 3)}
off, off_report = run(LIST, OFF)
on, on_report = run(LIST, SWITCH)
check("off: engine B's stand-ins asked of 裁決, no figure written, engine A's brackets written as the "
      "bracket rule has always written them (a pair round no character: 「）（」), nothing of the switch sealed",
      (asked(off[2][0]), off[2][1], "arabicFigures" in off[2][0], changes(off[2][0]),
       "keepArabicNumerals" in off[2][0]["provenance"], "keepArabicNumerals" in off_report),
      ([("", "丄"), ("", "工"), ("", "了")], LEAD + "律呂（調陽）雲騰（致雨）露結（爲霜）果珍李柰菜重芥薑）（海鹹河淡）（鱗潛羽翔）（龍師火帝",
       False, [], False, False))
check("on: each figure written at its place, engine B's stand-ins written as nothing, not asked",
      (asked(on[2][0]), on[2][1]),
      ([], LEAD + "律呂（調陽）雲騰（致雨）露結（爲霜）果珍李柰菜重芥薑（1）海鹹河淡（2）鱗潛羽翔（3）龍師火帝"))
check("on: the figures sealed, with how engine B's reading was settled",
      figures(on[2][0]), [("（1）", True, "stand-in"), ("（2）", True, "stand-in"), ("（3）", True, "stand-in")])
check("on: a decisionChanges entry each",
      changes(on[2][0]), [("（1）", "号一丄一", "（1）", "stand-in"), ("（2）", "丶八工一", "（2）", "stand-in"),
                          ("（3）", "丶一了一", "（3）", "stand-in")])
check("on: engine B's stand-ins settled as the figure's (arabic-figure-A)",
      [(d["engineB"], d["figure"]) for d in on[2][0]["engineDisagreements"] if d["resolved_by"] == "arabic-figure-A"],
      [("丄", "（1）"), ("工", "（2）"), ("了", "（3）")])
check("on: the run report counts them and names the glyphs engine B writes for figures on this book",
      (on_report["keepArabicNumerals"]["kept"], sorted(on_report["keepArabicNumerals"]["figureAliases"]),
       on_report["decisionChanges"].get("keepArabicNumerals")),
      ({"stand-in": 9}, ["丄", "了", "工"], 9))
check("on: sealed as the figures and glyphs it read off the drafts",
      on[2][0]["provenance"]["keepArabicNumerals"] == on[1][0]["provenance"]["keepArabicNumerals"]
      and len(on[2][0]["provenance"]["keepArabicNumerals"]) == 64, True)


# --- Contents lines engine B read with no figure; a figure engine A misread; a folio.
def contents(first, misread=None):
    figures_ = [first, first + 1, first + 2, first + 3, first + 4]
    if misread is not None:
        figures_[2] = misread
    titles = ["天地玄黃宇宙洪荒", "日月盈昃辰宿列張", "寒來暑往秋收冬藏", "閏餘成歲律呂調陽", "雲騰致雨露結爲霜"]
    return ([stand_in.block(LEAD, BOX)] + [stand_in.block(f"{n}.{t}", BOX) for n, t in zip(figures_, titles)]
            + [stand_in.block("12", BOX, label="number")],
            LEAD + "\n" + "\n".join(titles))


TOC = {1: contents(1), 2: contents(6, misread=3)}
toc, _ = run(TOC, SWITCH)
check("contents engine B read with no figure: each figure written at its line's head",
      toc[1][1].split("\n")[1:],
      ["1.天地玄黃宇宙洪荒", "2.日月盈昃辰宿列張", "3.寒來暑往秋收冬藏", "4.閏餘成歲律呂調陽", "5.雲騰致雨露結爲霜"])
check("a figure engine A misread (3 for 8) is not written, nor the folio in its number block",
      (figures(toc[2][0]), toc[2][1].split("\n")[1:]),
      ([("6.", True, "nothing"), ("7.", True, "nothing"), ("3.", False, "numbering not continued"),
        ("9.", True, "nothing"), ("10.", True, "nothing")],
       ["6.天地玄黃宇宙洪荒", "7.日月盈昃辰宿列張", "寒來暑往秋收冬藏", "9.閏餘成歲律呂調陽", "10.雲騰致雨露結爲霜"]))
check("a page without figures is the same with the switch as without",
      run({1: ([stand_in.block(LEAD, BOX)], LEAD)}, SWITCH)[0][1][1], run({1: ([stand_in.block(LEAD, BOX)], LEAD)}, OFF)[0][1][1])


# --- A numeral engine B read where engine A read a figure: 裁決 is asked as
# without the switch, and its answer decides.
def numeral_page():
    a = LEAD + PAREN_A + "果珍李柰，菜重芥薑：（1）海鹹河淡，（2）鱗潛羽翔，（3）龍師火帝。"
    b = LEAD + PAREN_B + "果珍李柰丶菜重芥薑号一丄一海鹹河淡丶八二一鱗潛羽翔丶一了一龍師火帝口"
    return {page: ([stand_in.block(a, BOX)], b) for page in (1, 2, 3)}


def judge_figure(a, b):
    return "2" if b == "二" else (a or "無")


def judge_numeral(a, b):
    return "二" if b == "二" else (a or "無")


read_figure, _ = run(numeral_page(), SWITCH, judge=judge_figure)
read_numeral, _ = run(numeral_page(), SWITCH, judge=judge_numeral)
without, _ = run(numeral_page(), OFF, judge=judge_figure)
without_numeral, _ = run(numeral_page(), OFF, judge=judge_numeral)
check("a numeral engine B read is asked of 裁決, as without the switch",
      (asked(read_figure[2][0]), asked(without[2][0])), ([("", "二")], [("", "丄"), ("", "二"), ("", "了")]))
check("裁決 reads the figure: it is written, engine B's numeral written as nothing",
      (read_figure[2][1], figures(read_figure[2][0])[1], changes(read_figure[2][0])[1]),
      (LEAD + "律呂（調陽）雲騰（致雨）露結（爲霜）果珍李柰菜重芥薑（1）海鹹河淡（2）鱗潛羽翔（3）龍師火帝",
       ("（2）", True, "verdict"), ("（2）", "丶八二一", "（2）", "verdict")))
check("裁決 reads the numeral: engine B's reading stands, written there as without the switch, and the "
      "figure is not written, nor its brackets alone: a doubt says so (without the switch they are, as before)",
      (read_numeral[2][1].split("海鹹河淡")[1], without_numeral[2][1].split("海鹹河淡")[1],
       figures(read_numeral[2][0])[1],
       [d["detail"] for d in read_numeral[2][0]["doubts"] if d["kind"] == "arabic-figure-unwritten"]),
      ("二鱗潛羽翔（3）龍師火帝", "二）（鱗潛羽翔）（龍師火帝", ("（2）", False, "engine B's reading there stands"),
       ["ARABIC FIGURE NOT WRITTEN: 1 figure(s) or mark(s) engine A read in brackets, which the merge did not keep, "
        "are not on the page (the bracket rule writes no pair alone): …海鹹河淡[（2）]鱗潛羽翔… (engine B's reading "
        "there stands) - check the "
        "numbers against the image"]))


# --- Never a character both engines read, nor one only engine B read that is no stand-in.
def edges():
    # Page 2 only: a character engine B read at one figure's place is no glyph
    # the book's drafts show it writing for figures.
    a1 = LEAD + PAREN_A + "果珍李柰：（1）海鹹河淡：（2）潛羽翔：（3）龍師火帝。"
    b1 = LEAD + PAREN_B + "果珍李柰号一丄一海鹹河淡号一鱗一潛羽翔号一了一龍師火帝口"
    return {1: LIST[1], 2: ([stand_in.block(a1, BOX)], b1), 3: LIST[3]}


edge, _ = run(edges(), SWITCH, judge=lambda a, b: b or a or "無")
check("a character only engine B read at a figure's place, no stand-in, that 裁決 reads, stands "
      "(the figure is not written)",
      (asked(edge[2][0]), edge[2][1], figures(edge[2][0])[1]),
      ([("", "鱗")], LEAD + "律呂（調陽）雲騰（致雨）露結（爲霜）果珍李柰（1）海鹹河淡鱗潛羽翔（3）龍師火帝",
       ("（2）", False, "engine B's reading there stands")))
check("no character both engines read is replaced",
      pp.CJK(edge[2][1]).replace("鱗", "") == pp.CJK(LEAD + PAREN_A + "果珍李柰海鹹河淡潛羽翔龍師火帝"), True)
unequal = [{"tag": "equal", "a": "天地", "b": "天地"}, {"tag": "replace", "a": "玄", "b": "丄元"},
           {"tag": "equal", "a": "黃", "b": "黃"}]
check("arabic_figure_kept: a figure beside a replacement of unequal readings is not written",
      [(f["figure"], f["kept"], f.get("why")) for f in pp.arabic_figure_kept(
          {2: [{"gap": 2, "block": 0, "at": 2, "figure": "1", "digits": "1", "value": 1, "confirmed": True}]},
          unequal, [0, 2, 3], ["天地", "玄", "黃"], {1: unequal[1]}, {}, [{"text": "天地1玄黃"}])[1]],
      [("1", False, "beside a replacement of unequal readings")])


# --- A figure starting its block: its brackets go with it, not the closing one
# with the last character of the block before (brackets_by_block, own).
def heads():
    a = [LEAD + PAREN_A + "果珍李柰。", "（1）海鹹河淡鱗潛羽翔。", "（2）龍師火帝鳥官人皇。", "（3）始制文字乃服衣裳。"]
    b = LEAD + PAREN_B + "果珍李柰口一丄一海鹹河淡鱗潛羽翔口八工一龍師火帝鳥官人皇口一了一始制文字乃服衣裳口"
    return {page: ([stand_in.block(text, BOX) for text in a], b) for page in (1, 2, 3)}


headed, _ = run(heads(), SWITCH)
off_heads, _ = run(heads(), OFF)
check("a figure starting its block is written there with both its brackets, none left at the block before "
      "(without the switch the closing one goes to the end of the block before)",
      (headed[2][1].split("\n"), off_heads[2][1].split("\n")[1:]),
      ([LEAD + "律呂（調陽）雲騰（致雨）露結（爲霜）果珍李柰", "（1）海鹹河淡鱗潛羽翔", "（2）龍師火帝鳥官人皇", "（3）始制文字乃服衣裳"],
       ["（海鹹河淡鱗潛羽翔）", "（龍師火帝鳥官人皇）", "（始制文字乃服衣裳"]))
check("gap_sides: the pair goes with the character after, the marks read after the figure too",
      (pp.gap_sides("。\n(2)"), pp.gap_sides("。\n2()"), pp.gap_sides("！\n4，")),
      (("。（", "）"), ("。", "（）"), ("！", "，")))


# --- The structure pass leaves the figures out: they are written back.
dropped, _ = run(TOC, SWITCH, formatter=lambda text, kind: re.sub(r"[0-9]+\.", "", text))
check("figures the structure pass left out are written back at their places",
      (dropped[1][1].split("\n")[1:], [f.get("structure") for f in dropped[1][0]["arabicFigures"]]),
      (["1.天地玄黃宇宙洪荒", "2.日月盈昃辰宿列張", "3.寒來暑往秋收冬藏", "4.閏餘成歲律呂調陽", "5.雲騰致雨露結爲霜"],
       ["restored", "restored", "restored", "restored", "restored"]))


# --- A punctuator that wrote the figure: at its place, or after the entry.
def punctuate(span):
    return {"天地玄黃宇宙洪荒": "1.天地玄黃宇宙洪荒", "日月盈昃辰宿列張": "日月盈昃辰宿列張 2."}.get(span, span)


written, _ = run({1: contents(1)}, SWITCH, census=1, punctuate=punctuate)
check("a figure the punctuator wrote at its place or after its entry is not written twice",
      (written[1][1].split("\n")[1:3], [f.get("placed") for f in written[1][0]["arabicFigures"]][:2]),
      (["1.天地玄黃宇宙洪荒", "日月盈昃辰宿列張 2."], ["already", "elsewhere"]))


# --- The budget: a question the switch does not ask takes the budget it would
# have taken, so a later disagreement is decided as with it off.
def budget_page():
    a = LEAD + PAREN_A + "果珍李柰：（1）海鹹河淡：（2）鱗潛羽翔：（3）龍師火帝。菜重芥薑"
    b = LEAD + PAREN_B + "果珍李柰号一丄一海鹹河淡号八工一鱗潛羽翔号一了一龍師火帝口菜重芥美"
    return {page: ([stand_in.block(a, BOX)], b) for page in (1, 2, 3)}


tight = ["--max-adjudications", "2"]
b_off, _ = run(budget_page(), OFF + tight, {"preferEngine": "adjudicate"}, judge=lambda a, b: b or "無")
b_on, _ = run(budget_page(), SWITCH + tight, {"preferEngine": "adjudicate"}, judge=lambda a, b: b or "無")
check("with the budget spent before the last question, it is spent before it with the switch too",
      ([d["resolved_by"] for d in b_off[2][0]["engineDisagreements"] if d["engineA"] == "薑"],
       [d["resolved_by"] for d in b_on[2][0]["engineDisagreements"] if d["engineA"] == "薑"]),
      (["prefer-engine-A-adjudication-budget-spent"], ["prefer-engine-A-adjudication-budget-spent"]))


# --- A parenthesis closed right before a figure: its closing bracket is the
# text's, not the figure's, and stays (figure_in_gap's keep).
check("figure_in_gap: a bracket of other print there stays; only brackets beyond it are the figure's",
      (pp.figure_in_gap("）", "（1）", keep={"）": 1}), pp.figure_in_gap("）（）", "（1）", keep={"）": 1}),
       pp.figure_in_gap("））（", "（1）", keep={"）": 1}), pp.figure_in_gap("）（", "（1）", keep={"）": 1})),
      ("）（1）", "）（1）", "）（1）", "）（1）"))
check("place_arabic_figures: the bracket rule's close of the text before the figure stays",
      pp.place_arabic_figures("天地）玄黃", "天地玄黃", [(2, "（1）", "", {"（": 0, "）": 1})]),
      ("天地）（1）玄黃", ["written"]))
check("place_arabic_figures: the punctuator's copy of a bracketed figure without its brackets gets them "
      "(the bracket rule leaves them to the figure)",
      [pp.place_arabic_figures("天地1玄黃", "天地玄黃", [(2, "（1）")]),
       pp.place_arabic_figures("天地（1）玄黃", "天地玄黃", [(2, "（1）")]),
       pp.place_arabic_figures("天地玄黃 1", "天地玄黃", [(2, "（1）")]),
       pp.place_arabic_figures("天地1.玄黃", "天地玄黃", [(2, "（1）.")]),
       pp.place_arabic_figures("天地）1玄黃", "天地玄黃", [(2, "（1）", "", {"）": 1})])],
      [("天地（1）玄黃", ["already-bracketed"]), ("天地（1）玄黃", ["already"]),
       ("天地玄黃 （1）", ["elsewhere-bracketed"]), ("天地（1）.玄黃", ["already-bracketed"]),
       ("天地）（1）玄黃", ["already-bracketed"])])
check("place_arabic_figures: brackets the punctuator wrote at the figure's gap are not written twice",
      [pp.place_arabic_figures("天地（）玄黃 1", "天地玄黃", [(2, "（1）")]),
       pp.place_arabic_figures("天地（）1玄黃", "天地玄黃", [(2, "（1）")])],
      [("天地（）玄黃 1", ["elsewhere"]), ("天地（）1玄黃", ["already"])])
check("restore_arabic_figures: the close before the figure stays, with or without the figure's own pair",
      (pp.restore_arabic_figures("天地）（1）玄黃", "天地）玄黃", [(2, "（1）")])[0],
       pp.restore_arabic_figures("天地）（1）玄黃", "天地）（）玄黃", [(2, "（1）")])[0]),
      ("天地）（1）玄黃", "天地）（1）玄黃"))
check("restore_arabic_figures: a figure moved beside another's is there (copies counted gap by gap), "
      "not written twice",
      pp.restore_arabic_figures("甲1乙丙2丁", "甲乙1丙2丁", [(1, "1"), (3, "2")]), ("甲乙1丙2丁", ["moved", "kept"]))


def closed_before(n):
    a = (LEAD + PAREN_A + f"果珍李柰（菜重）（{n}）海鹹河淡，（{n + 1}）鱗潛羽翔，（{n + 2}）龍師火帝。")
    b = LEAD + PAREN_B + "果珍李柰（菜重）海鹹河淡丶八工一鱗潛羽翔丶一了一龍師火帝口"
    return {1: LIST[1], 2: ([stand_in.block(a, BOX)], b), 3: LIST[3]}


closed, _ = run(closed_before(1), SWITCH)
closed_off, _ = run(closed_before(1), OFF)
check("a figure right after a closed parenthesis: the close stays before the figure "
      "(without the switch both brackets are there, the pair reversed)",
      (closed[2][1].split("果珍李柰")[1].split("海鹹")[0], closed_off[2][1].split("果珍李柰")[1].split("海鹹")[0]),
      ("（菜重）（1）", "（菜重））（"))
bare, _ = run(LIST, SWITCH, census=1, punctuate=lambda span: span.replace("芥薑海", "芥薑1海"))
bare_off, _ = run(LIST, OFF, census=1, punctuate=lambda span: span.replace("芥薑海", "芥薑1海"))
check("the punctuator wrote a figure's digits without its brackets: the figure's brackets written round them "
      "(without the switch the bracket rule writes them, the pair reversed), and sealed",
      (bare[2][1].split("菜重芥薑")[1].split("海鹹")[0], bare_off[2][1].split("菜重芥薑")[1].split("海鹹")[0],
       [f.get("placed") for f in bare[2][0]["arabicFigures"]][0], changes(bare[2][0])[0][2]),
      ("（1）", "1）（", "already-bracketed", "（1）"))


# --- The page falls back to text without the figures: they are not written,
# and a doubt says so.
def whole_page():
    # A disagreement across two blocks that 裁決 settles with engine B's
    # reading: no block pass (block_spans), the formatter punctuates the page whole.
    a = [LEAD + PAREN_A + "果珍李柰二", "三菜重芥薑（1）海鹹河淡（2）鱗潛羽翔（3）龍師火帝。"]
    b = LEAD + PAREN_B + "果珍李柰四五菜重芥薑一丄一海鹹河淡八工一鱗潛羽翔一了一龍師火帝口"
    return {1: LIST[1], 2: ([stand_in.block(text, BOX) for text in a], b), 3: LIST[3]}


def invents(text, kind):
    return "爨" + text


whole, _ = run(whole_page(), SWITCH, judge=lambda a, b: b or a or "無")
rejected, _ = run(whole_page(), SWITCH, judge=lambda a, b: b or a or "無", formatter=invents)
check("a page punctuated whole: the figures in the text the formatter is given",
      (whole[2][1].split("菜重芥薑")[1], [d["kind"] for d in whole[2][0]["doubts"]]),
      ("（1）海鹹河淡（2）鱗潛羽翔（3）龍師火帝", ["punctuation-whole-page"]))
check("the formatter rejected: the page is the merged characters, the figures not written, and said so",
      (rejected[2][1].split("菜重芥薑")[1], [f.get("structure") for f in rejected[2][0]["arabicFigures"]],
       [c[2] for c in changes(rejected[2][0])], [d["kind"] for d in rejected[2][0]["doubts"]]),
      ("海鹹河淡鱗潛羽翔龍師火帝", ["formatter rejected"] * 3, ["", "", ""],
       ["formatter-rejected", "arabic-figure-unwritten"]))


def own_lines():
    # Contents entries whose numbers engine A read as blocks of their own.
    titles = ["天地玄黃", "宇宙洪荒", "日月盈昃", "辰宿列張"]
    a = [stand_in.block(LEAD, BOX)]
    for n, title in zip((3, 4, 5, None), titles):
        a += [stand_in.block(title, BOX)] + ([stand_in.block(str(n), BOX)] if n else [])
    return {1: (a, LEAD + "".join(titles))}


lines_ok, _ = run(own_lines(), SWITCH)
lines_rejected, _ = run(own_lines(), SWITCH, formatter=invents)
check("a number on a line of its own is written there",
      [line for line in lines_ok[1][1].split("\n") if line.strip()][2:5:2], ["3", "4"])
check("the structure pass rejected: a block with no character is left out, its figure with it, and said so",
      ([f.get("structure") for f in lines_rejected[1][0]["arabicFigures"]], "3" in lines_rejected[1][1],
       "arabic-figure-unwritten" in [d["kind"] for d in lines_rejected[1][0]["doubts"]]),
      (["block left out"] * 3, False, True))


# --- Sealed only when on, and a key a resume compares when absent.
on_seal = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A", keep_arabic_numerals=True,
                                                 figure_evidence={"numbering": {1: [1, 2]}},
                                                 figure_aliases={"丄": {}}))
off_seal = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A"))
check("--keep-arabic-numerals: sealed as its evidence when on, and not at all when off",
      (len(on_seal.get("keepArabicNumerals", "")), "keepArabicNumerals" in off_seal), (64, False))
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "page-0001.json"
    job = SimpleNamespace(out_json=out, out_md=out.with_suffix(".md"))
    current = []
    for sealed_with, run_with in ((on_seal, off_seal), (off_seal, on_seal), (on_seal, on_seal), (off_seal, off_seal)):
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "scanPage": 1,
                  "provenance": {**sealed_with, "markdownSha256": pp.sha256_bytes("天地玄黃".encode("utf-8"))}}
        record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
        out.with_suffix(".md").write_text("天地玄黃", encoding="utf-8")
        out.write_bytes(pp.canonical_json_bytes(record))
        current.append(pp.validate_completion(job, run_with)[0])
check("--keep-arabic-numerals: a resume takes a sealed page as current only under the same setting",
      current, [False, False, True, True])
parse = lambda *extra: pp.build_parser().parse_args(["r", "a", "o", *extra]).keep_arabic_numerals
check("parsing: on by default, off with --no-keep-arabic-numerals", (parse(), parse(*OFF)), (True, False))

print(f"{'FAIL' if failures else 'ok'}: {failures} failure(s)")
sys.exit(1 if failures else 0)
