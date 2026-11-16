#!/usr/bin/env python3
"""Offline checks of a table of contents through phase 3 (#23): its page
numbers, their brackets and its lines.

No model, no GPU, no corpus.  The lines are real: copied from the drafts of
天地会 5 (scans 10, 12, 13, 14) and 天地会 2 (scan 20), where the sealed pages
lost most page numbers, wrote 「）（」 for them (20 on scan 14, one inside a
name: 「瑚）（图里」) and were sealed with a whole contents page as one line
(the structure pass refused, one paragraph per block; or accepted with the
one line it was given).  What is checked:

- arabic_figures: a contents entry's page number - a figure ending a line
  that holds a leader - is found where engine A wrote its leader as ASCII
  dots (dotted), and is marked; without dotted, as before (the book's
  numbering sealed in provenance stays as it was), and a figure inside a
  line is no page number;
- the bracket rule does not write a pair an engine read around no character
  (unpaired_brackets), save where the merge writes the figure; without
  --keep-arabic-numerals it does, as before (canzungsiling scan 45, sealed
  without the switch: the pair puts the fallback's brackets back after engine
  B's stand-in, 「：丄）（土地」, which the repair's K1 writes 「：（1）土地」);
- of those, a figure (or a mark) the page holds no copy of is a doubt
  (bracketed_pairs, bracketed_unwritten): counted page-wide, a figure the
  merge kept taking its own copy; not a year or a table's cell (天地会 5
  scan 14, doukeoi-zingkeoi scans 105 and 223, caangung-4 scan 20);
- a line's leader makes no page number of the next line's figure, and two
  lines with a leader make a list (doukeoi-zingkeoi scan 11: engine A read
  two of its leaders);
- a contents page whose numbers rise by pages, not by one, keeps them all,
  with their brackets, one entry a line;
- a page number engine B's reading displaces is not written, nor its
  brackets alone, and a doubt says so (an empty pair the punctuator wrote
  there stays: the repair's K1 writes engine A's figure in it);
- the structure pass refused: one entry a paragraph, not the page as one;
- a page of prose is the same as before.

Usage:
    python3 tests/check_contents_pages.py
"""

from __future__ import annotations

from pathlib import Path
import sys

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


def attempt(name, thunk, want):
    """check() of THUNK's value; an exception is a failure, not the end of the run."""
    try:
        got = thunk()
    except Exception as error:  # noqa: BLE001 - a missing function is a failure of this check
        got = f"{type(error).__name__}: {error}"
    check(name, got, want)


BOX = [0.1, 0.1, 0.9, 0.9]

# 天地会 5 scan 10, engine A (leaders as ASCII dots) and engine B, its first lines.
TOC_A = ("思过..... (141)\n乾隆五十三年七月二十七日\n"
         "广东巡抚图萨布奏追查天地会赵明德等情折..... (142)\n乾隆五十三年七月二十七日\n"
         "四川总督李世杰奏密查严烟所供天地会起会人朱鼎元等情折..... (143)\n乾隆五十三年八月初三日批")
TOC_B = ("思过·····(141)\n乾隆五十三年七月二十七日\n"
         "广东巡抚图萨布奏追查天地会赵明德等情折·····(142)\n乾隆五十三年七月二十七日\n"
         "四川总督李世杰奏密查严烟所供天地会起会人朱鼎元等情折·····(143)\n乾隆五十三年八月初三日批")
TOC_TITLES = ["思过", "广东巡抚图萨布奏追查天地会赵明德等情折", "四川总督李世杰奏密查严烟所供天地会起会人朱鼎元等情折"]
# 天地会 5 scan 13: names, each page number rising by pages; engine B read an extra 韬
# where the fourth line's number is printed (its reading of the next line's name).
NAMES_A = "柴大纪……(302)\n蓝元枚……(310)\n普吉保……(316)\n格绷额……(323)\n潘韬……(324)\n魏大斌……(325)"
NAMES_B = "柴大纪·····(302)\n蓝元枚·····(310)\n普吉保·····(316)\n格绷额韬·····(323)\n潘韬·····(324)\n魏大斌·····(325)"
NAMES = ["柴大纪", "蓝元枚", "普吉保", "格绷额", "潘韬", "魏大斌"]
# 天地会 2 scan 20: page numbers with no brackets.
ENTRIES_A = ("谕钦差湖广总督常青等酌筹调度进剿事宜并速报捷音……271\n乾隆五十二年六月初十日\n\n"
             "钦差湖广总督常青复奏查察逃兵等由折……275\n乾隆五十二年六月十五日批\n\n"
             "钦差湖广总督常青奏麻豆庄战况片……276\n乾隆五十二年六月十五日批")
ENTRIES = ["谕钦差湖广总督常青等酌筹调度进剿事宜并速报捷音", "钦差湖广总督常青复奏查察逃兵等由折",
           "钦差湖广总督常青奏麻豆庄战况片"]


def leaders_after(titles, pair=()):
    """A punctuator that writes a leader after each of TITLES (the retry of
    the sealed scan 14: leaders only), and an empty pair after the leader of
    each of PAIR (the sealed scans 10 and 12)."""
    def punctuate(span):
        for title in titles:
            span = span.replace(title, title + "……" + ("（）" if title in pair else ""), 1)
        return span
    return punctuate


def refused(text, kind):
    """A structure pass that writes a character not in the text: refused twice."""
    return text + "批"


# --- Engine A's page numbers.
blocks = [{"text": TOC_A, "label": "content"}]
attempt("arabic_figures: page numbers after ASCII-dot leaders, each ending its line",
        lambda: [(f["figure"], f["leader"]) for f in pp.arabic_figures(blocks, TOC_A, dotted=True)],
        [("（141）", True), ("（142）", True), ("（143）", True)])
attempt("arabic_figures without dotted: as before (the book's numbering sealed in provenance)",
        lambda: pp.arabic_figures(blocks, TOC_A), [])
line12 = "东瀛纪事……杨廷理（169）\n平台纪事本末……佚名（191）"
attempt("arabic_figures: a page number after an author's name, the leader before the name (天地会 5 scan 12)",
        lambda: [(f["figure"], f["leader"])
                 for f in pp.arabic_figures([{"text": line12, "label": "content"}], line12, dotted=True)],
        [("（169）", True), ("（191）", True)])
prose = "据记载……共有3处寨堡，又有（4）处\n关隘"
attempt("arabic_figures: figures inside a line of prose are no page numbers, an ellipsis before them or not",
        lambda: [(f["figure"], f["leader"])
                 for f in pp.arabic_figures([{"text": prose, "label": "text"}], prose, dotted=True)],
        [("3", False), ("（4）", False)])

# --- Brackets around no character.
attempt("unpaired_brackets: a pair opened and closed between two characters is not written back alone",
        lambda: [pp.unpaired_brackets("……(302)\n"), pp.unpaired_brackets("）（3）"), pp.unpaired_brackets("。（")],
        [[], ["）"], ["（"]])
segs = [{"tag": "equal", "a": "柴大纪蓝元枚", "b": "柴大纪蓝元枚"}]
attempt("engine_brackets_merged: the pair around a figure is left out, save where the merge writes the figure; "
        "without the switch (no figures: None) it is written, as before",
        lambda: [pp.engine_brackets_merged(pp.engine_gaps("柴大纪……(302)\n蓝元枚"), "a", segs, ["柴大纪蓝元枚"], ()),
                 pp.engine_brackets_merged(pp.engine_gaps("柴大纪……(302)\n蓝元枚"), "a", segs, ["柴大纪蓝元枚"], {3}),
                 pp.engine_brackets_merged(pp.engine_gaps("柴大纪……(302)\n蓝元枚"), "a", segs, ["柴大纪蓝元枚"]),
                 # engine B's side, its text placed by segment (bStart)
                 pp.engine_brackets_merged(pp.engine_gaps("柴大纪·····(302)\n蓝元枚"), "b",
                                           [dict(segs[0], bStart=0)], ["柴大纪蓝元枚"], ()),
                 pp.engine_brackets_merged(pp.engine_gaps("柴大纪·····(302)\n蓝元枚"), "b",
                                           [dict(segs[0], bStart=0)], ["柴大纪蓝元枚"])],
        [[], [(3, "（"), (3, "）")], [(3, "（"), (3, "）")], [], [(3, "（"), (3, "）")]])
# canzungsiling scan 45 (sealed without the switch), engine A and B as read, the merge's segments and
# pieces there (engine B's stand-ins 丄 and 已 written, 了 answered 無), and the text of the block's
# punctuation fallback (both answers refused: they wrote the figures), engine A's pairs split round the
# stand-ins.  The bracket rule moves them back after each stand-in, where K1 writes engine A's figure.
A45 = "國家成立的要素有三：（1）土地，（2）人民，（3）主權。國家必具有這三要素"
B45 = "國家成立的要素有三丶一丄一土地丶八已人民丶一了一主權口國家必具有這三要素"
SEGS45 = [("國家成立的要素有三", "國家成立的要素有三"), ("", "丶一丄一"), ("土地", "土地"), ("", "丶八已"),
          ("人民", "人民"), ("", "丶一了一"), ("主權", "主權"), ("", "口"), ("國家必具有這三要素", "國家必具有這三要素")]
PIECES45 = ["國家成立的要素有三", "丄", "土地", "已", "人民", "", "主權", "", "國家必具有這三要素"]
SPAN45 = "".join(PIECES45)
FALLBACK45 = "國家成立的要素有三：）丄（土地，）已（人民，）（主權。國家必具有這三要素"


def brackets45(own):
    segments = [{"tag": "equal" if a == b else "insert", "a": a, "b": b} for a, b in SEGS45]
    wanted = pp.brackets_by_block(pp.engine_gaps(A45), pp.engine_gaps(B45), segments, PIECES45, [SPAN45], own=own)
    return pp.place_brackets(FALLBACK45, SPAN45, wanted.get(0, {}))[0]


attempt("without the switch, engine A's pairs round engine B's stand-ins are written, as before: the fallback's "
        "brackets go back after each stand-in (canzungsiling scan 45)",
        lambda: brackets45(None), "國家成立的要素有三：丄）（土地，已）（人民，）（主權。國家必具有這三要素")

# --- Pairs engine A read in brackets that the page may lose (bracketed_pairs, bracketed_unwritten).
# 天地会 5 scan 14, engine A: page numbers repeating (341 twice, 342 three times).
NAMES14 = "耿世文..... (339)\n王隽..... (340)\n俞峻..... (341)\n冯启宗..... (341)\n渠永湜..... (342)\n程峻..... (342)\n宋学浩..... (342)"


def pairs14():
    return pp.bracketed_pairs([{"text": NAMES14, "label": "content"}])


attempt("bracketed_pairs: each figure engine A read in brackets, by its gap and digits",
        lambda: [(gap, key) for gap, key, _ in pairs14()],
        [(3, "339"), (5, "340"), (7, "341"), (10, "341"), (13, "342"), (15, "342"), (18, "342")])
# doukeoi-zingkeoi: scan 105's year (and its notes' superscripts), scan 223's table cell; caangung-4
# scan 20, a vertical page printing 「以『義氣』(？)相投」.
YEAR105 = "甲於天下」。 $ ^{206} $張瀚（1513-95）則指出其中「山寇居十之七八"
CELL223 = "<td>2</td><td>15姓各1人（15）</td><td>共75人</td></tr></table>"
MARK20 = "共黨便是中國小人的大集團，以『義氣』(？)相投，利害相同"
attempt("bracketed_pairs: not a year (the runs stage's), nor a table's cell; a mark in brackets is",
        lambda: ([key for _, key, _ in pp.bracketed_pairs([{"text": YEAR105, "label": "text"}])],
                 [key for _, key, _ in pp.bracketed_pairs([{"text": CELL223, "label": "table"}])],
                 [key for _, key, _ in pp.bracketed_pairs([{"text": CELL223, "label": "text"}])],
                 [context for _, _, context in pp.bracketed_pairs([{"text": MARK20, "label": "vertical_text"}])]),
        ([], [], ["15"], ["…團以義氣[（？）]相投利害…"]))
# The merge kept the first (342) and wrote it; the punctuator wrote the others but the last two 342.
page14 = "耿世文……（339）\n王隽……（340）\n俞峻……（341）\n冯启宗……（341）\n渠永湜……（342）\n程峻……\n宋学浩……"
attempt("bracketed_unwritten: a figure the page holds no copy of is lost - one the merge kept takes its own copy",
        lambda: pp.bracketed_unwritten(pairs14(), [{"gap": 13, "figure": "（342）"}], page14,
                                       {15: "engine B's reading there stands"}),
        ["…永湜程峻[（342）]宋学浩… (engine B's reading there stands)", "…峻宋学浩[（342）]…"])
attempt("bracketed_unwritten: none lost where the punctuator wrote every number",
        lambda: pp.bracketed_unwritten(pairs14(), [], page14.replace("程峻……", "程峻……（342）").replace(
            "宋学浩……", "宋学浩……（342）"), {}), [])
attempt("bracketed_unwritten: a mark in brackets the page lost, and one it holds",
        lambda: [pp.bracketed_unwritten(pp.bracketed_pairs([{"text": MARK20, "label": "text"}]), [], page, {})
                 for page in ("共黨便是中國小人的大集團，以『義氣』？相投，利害相同",
                              "共黨便是中國小人的大集團，以『義氣』（？）相投，利害相同")],
        [["…團以義氣[（？）]相投利害…"], []])

# --- A list of figures (doukeoi-zingkeoi scan 11, 圖次目錄): engine A read the leaders of two lines
# only, though every line prints one.
FIGS11 = "圖一 G. W. Skinner 的中國巨區（macro-region）劃分 …36\n圖二 虔鎮圖 …38\n圖1-1 大庾嶺路圖 44\n圖1-2 華南諸省主要水陸交通路線圖 47"
attempt("arabic_figures: a line's leader makes no page number of the next line's figure",
        lambda: [(f["figure"], f["leader"]) for f in pp.arabic_figures([{"text": FIGS11, "label": "content"}], FIGS11,
                                                                       dotted=True)],
        [("36", True), ("38", True), ("44", False), ("47", False)])
attempt("leader_list_breaks: two lines with a leader make a list",
        lambda: pp.leader_list_breaks(FIGS11), [9, 14, 20])

# --- Line breaks of a list with leaders.
attempt("leader_list_breaks: engine A's line breaks in a block listing entries with leaders, none in prose",
        lambda: (pp.leader_list_breaks(TOC_A), pp.leader_list_breaks("天地玄黃\n宇宙洪荒……3\n日月盈昃")),
        ([2, 14, 33, 45, 71], []))
attempt("break_lines: after the marks ending the line, before those opening the next",
        lambda: pp.break_lines("甲……（1）乙。（一）丙", "甲乙一丙", [1, 2]), "甲……（1）\n乙。\n（一）丙")


# --- Whole pages: the made-up book's three contents pages.
def book(formatter=None, judge=None):
    pages = {1: ([stand_in.block(TOC_A, BOX, label="content")], TOC_B),
             2: ([stand_in.block(NAMES_A, BOX, label="content")], NAMES_B),
             3: ([stand_in.block(ENTRIES_A, BOX, label="content")], ENTRIES_A.replace("……", "·····"))}
    model = stand_in.StandIn(census="不詳", punctuate=leaders_after(TOC_TITLES + NAMES + ENTRIES, ("格绷额",)),
                             **({"formatter": formatter} if formatter else {}), **({"judge": judge} if judge else {}))
    with stand_in.book(pages) as root:
        return stand_in.run(root, model, pages="1,2,3")


# 裁決 reads the 韬 engine B alone read where (323) is printed (its reading stands).
echo = book(judge=lambda a, b: b or a or "無")
check("ASCII-dot leaders: every page number written with its brackets, one entry a line",
      echo[1][1].strip(),
      "思过……（141）\n乾隆五十三年七月二十七日\n广东巡抚图萨布奏追查天地会赵明德等情折……（142）\n乾隆五十三年七月二十七日\n"
      "四川总督李世杰奏密查严烟所供天地会起会人朱鼎元等情折……（143）\n乾隆五十三年八月初三日批")
check("numbers rising by pages are written; the one engine B's reading displaces is not, nor its brackets alone "
      "(no 「）（」): the punctuator's own pair stays, for the repair's K1 to write engine A's figure in",
      echo[2][1].strip(), "柴大纪……（302）\n蓝元枚……（310）\n普吉保……（316）\n格绷额……（）韬\n潘韬……（324）\n魏大斌……（325）")
check("the page number not written is a doubt",
      ([d["detail"] for d in echo[2][0]["doubts"] if d["kind"] == "arabic-figure-unwritten"],
       [(f["figure"], f["kept"], f.get("leader")) for f in echo[2][0]["arabicFigures"]][3]),
      (["ARABIC FIGURE NOT WRITTEN: 1 figure(s) or mark(s) engine A read in brackets, which the merge did not keep, "
        "are not on the page (the bracket rule writes no pair alone): …保格绷额[（323）]潘韬魏大… (engine B's reading "
        "there stands) - check the numbers against the image"], ("（323）", False, True)))
check("page numbers with no brackets, rising by pages (271, 275, 276): every one written",
      echo[3][1].strip(),
      "谕钦差湖广总督常青等酌筹调度进剿事宜并速报捷音……271\n乾隆五十二年六月初十日\n"
      "钦差湖广总督常青复奏查察逃兵等由折……275\n乾隆五十二年六月十五日批\n"
      "钦差湖广总督常青奏麻豆庄战况片……276\n乾隆五十二年六月十五日批")
fallback = book(formatter=refused, judge=lambda a, b: b or a or "無")
check("the structure pass refused: one entry a paragraph (engine A's lines), not the page as one",
      ([d["kind"] for d in fallback[3][0]["doubts"]], fallback[3][1].strip().split("\n\n")),
      (["structure-rejected"],
       ["谕钦差湖广总督常青等酌筹调度进剿事宜并速报捷音……271", "乾隆五十二年六月初十日",
        "钦差湖广总督常青复奏查察逃兵等由折……275", "乾隆五十二年六月十五日批",
        "钦差湖广总督常青奏麻豆庄战况片……276", "乾隆五十二年六月十五日批"]))

# --- A page of prose: as before.
PROSE = "天地玄黃宇宙洪荒日月盈昃辰宿列張\n寒來暑往秋收冬藏閏餘成歲律呂調陽"
with stand_in.book({1: ([stand_in.block(PROSE, BOX, label="text")], PROSE)}) as root:
    plain = stand_in.run(root, stand_in.StandIn(census="不詳", formatter=refused))
check("prose: engine A's line breaks are where the print wrapped, not entries - one paragraph, as before",
      plain[1][1].strip(), "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽")

if failures:
    print(f"FAIL: {failures} failure(s)")
    sys.exit(1)
print("all ok")
