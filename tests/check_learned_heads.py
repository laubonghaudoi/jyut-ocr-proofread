#!/usr/bin/env python3
"""Offline checks of switch --learned-heads (on by default since the round-4
benchmark, the user's decision of 2026-09-30; --no-learned-heads turns it
off, in proofread_pages.py and in book_profile.py).

No model, no GPU, no corpus.  The profile measures a running head by verbatim
recurrence, so it learns the title a head column prints on every page, and
not what is printed beside it - a volume label that changes every few pages,
the folio that changes every page.  With the switch the running head is
learned by where it is printed (learn_heads): the blocks holding the title at
its type size (anchors), what stands in their column (a label beside the
title on several pages, and the family of labels that differ in a numeral;
the folio sequence), and where the anchors stand on odd and on even pages.

A made-up book of fourteen vertical pages, its head column in the outer margin
(right on odd pages, left on even ones): the title, a volume label and the
folio, read by the layout as one block or several.  Engine B reads the head
column in its stream.  The shapes:

- the title and label in one block, and the label in a block of its own
  beside the title, and the folio: left out with the switch, kept without
  (a paragraph, a line);
- a label the book never printed beside the title but of the learned family
  (a later volume): left out;
- a volume divider printing the label as a large title in the body, beside
  the running head: the title kept, whatever the switch;
- a page whose anchor engine A did not read: a label block in the learned
  region at the head's size left out; a numeral block there kept (the body
  prints numbers too), and a short body line there kept;
- a label not learned (another word in the head column): kept;
- the title read wrongly twice over (not the stem): kept, the label beside
  it (in the learned region) left out;
- a folio of one numeral the body holds too: a stretch of the body with it
  that the structure pass drops is still refused by the deletion guard;
- engine B's reading of the head column (title, label, folio) in the middle
  of the body, engine A reading none: left out before 裁決 with the switch;
- engine B's label alone between the head and the folio: asked of 裁決;
- the body naming the book (the title, or the title one character short) in
  a sentence whose words only one engine read, inside a block of engine A's:
  kept and asked of 裁決, with the switch as without; engine A's second
  reading of the head inside a body block, only the folio between it and the
  block's end: left out;
- the head drifting over the book, so its learned region takes in the column
  beside it: on a page whose anchor is read, a column there reading a
  volume's name alone is kept.

And: the structure pass refused and its fallback, which keeps no head block
the switch left out; the profile's learnedHeads (book_profile.py
--learned-heads), inert without the switch, and learning from the drafts the
same way when the profile has none; the record (decisionChanges, provenance,
the run report); resume; the default, the switch on in both scripts.  And
the running heads the profile measures by recurrence: notes and source
lines that recur on a page (天地会 5's) are none, a head on both leaves is.

Usage:
    python3 tests/check_learned_heads.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stand_in  # noqa: E402 - first: the private store and closed endpoint
import book_profile as bp  # noqa: E402
import proofread_pages as pp  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


HEAD = "某甲先生言行錄"
GLYPH = 0.0186
BODY = ["天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽",
        "雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光果珍李柰菜重芥薑",
        "海鹹河淡鱗潛羽翔龍師火帝鳥官人皇始制文字乃服衣裳推位讓國有虞陶唐"]
Block = stand_in.block
# Thirteen characters of the body, two of them the numeral of page 12's folio.
LOST = "甲一乙丙丁戊己庚辛壬癸一子"


def column(page: int) -> float:
    """The head column's left edge: the outer margin, right on odd pages."""
    return 0.74 if page % 2 else 0.18


def head(page: int, label: str = "", folio: str = "", together: bool = False, head_text: str = HEAD):
    """Engine A's head column: the title (with the label, TOGETHER), the label,
    the folio, each at the head's type size."""
    x = column(page)
    if together:
        return [Block(head_text + label, [x, 0.17, 0.03, GLYPH * len(head_text + label)], "vertical_text")] + (
            [Block(folio, [x, 0.66, 0.03, 0.03], "number")] if folio else [])
    blocks = [Block(head_text, [x, 0.17, 0.03, GLYPH * len(head_text)], "header")]
    if label:
        blocks.append(Block(label, [x, 0.33, 0.03, GLYPH * len(label)], "vertical_text"))
    if folio:
        blocks.append(Block(folio, [x, 0.66, 0.03, 0.03], "number"))
    return blocks


def body(page: int, texts=BODY[:2]):
    """The body's columns, away from the head column."""
    xs = (0.45, 0.20) if page % 2 else (0.55, 0.25)
    return [Block(text, [x, 0.10, 0.22, 0.68], "vertical_text") for x, text in zip(xs, texts)]


PAGES = {
    # Volume one: the title and its label in two blocks, the folio.
    1: (head(1, "第一冊", "一") + body(1), HEAD + "第一冊一" + BODY[0] + BODY[1]),
    # The title and its label read as one block.
    2: (head(2, "第一冊", "二", together=True) + body(2), HEAD + "第一冊二" + BODY[0] + BODY[1]),
    3: (head(3, "第一冊", "三") + body(3), HEAD + "第一冊三" + BODY[0] + BODY[1]),
    4: (head(4, "第二冊", "四", together=True) + body(4), HEAD + "第二冊四" + BODY[0] + BODY[1]),
    # A volume divider: the label printed as a large title in the body's first
    # column (in the head's region on odd pages, three times its type size),
    # beside the running head.
    5: (head(5, "第二冊", "五") + [Block("第二冊", [0.735, 0.45, 0.06, 0.15], "doc_title")] + body(5),
        HEAD + "第二冊五" + "第二冊" + BODY[0] + BODY[1]),
    # Engine A did not read the title: the label alone in the head's region,
    # a numeral block there at the top, a short body line there.
    6: ([Block("第二冊", [0.18, 0.33, 0.03, GLYPH * 3], "footer"),
         Block("十二", [0.18, 0.05, 0.03, 0.04], "number"),
         Block("始制文字乃服衣裳", [0.18, 0.45, 0.03, GLYPH * 8], "vertical_text")] + body(6),
        "第二冊十二始制文字乃服衣裳" + BODY[0] + BODY[1]),
    # Engine A read no head at all; engine B read the head column between the
    # body's two columns.
    7: (body(7), BODY[0] + HEAD + "第二冊七" + BODY[1]),
    # Engine B read the label between the title and the folio, engine A not.
    8: (head(8, "", "八") + body(8), HEAD + "第二冊八" + BODY[0] + BODY[1]),
    9: (head(9, "第二冊", "九") + body(9), HEAD + "第二冊九" + BODY[0] + BODY[1]),
    # A label of the learned family the book never printed beside the title
    # before; a word in the head column that is no label; the title misread.
    10: (head(10, "第三冊", "十") + [Block("凡例", [0.18, 0.45, 0.03, GLYPH * 2], "vertical_text")] + body(10),
         HEAD + "第三冊十凡例" + BODY[0] + BODY[1]),
    11: (head(11, "第二冊", "十一", head_text="某乙先王言行錄") + body(11),
         "某乙先王言行錄第二冊十一" + BODY[0] + BODY[1]),
    # A folio of one numeral, which the body holds too (a stretch the
    # structure pass may drop, below).
    12: (head(12, "第二冊", "一") + body(12, [BODY[0], LOST + BODY[2]]),
         HEAD + "第二冊一" + BODY[0] + LOST + BODY[2]),
    # The label spaced out (its box half again the title's length a
    # character), and the title's box taking in the label it did not read:
    # the head's thickness, the head's type.
    13: ([Block(HEAD, [0.74, 0.17, 0.03, GLYPH * 10], "header"),
          Block("第二冊", [0.74, 0.33, 0.03, GLYPH * 4.5], "vertical_text"),
          Block("十三", [0.74, 0.66, 0.03, 0.03], "number")] + body(13),
         HEAD + "第二冊十三" + BODY[0] + BODY[1]),
    # A page printing nothing but its running head (a plate's back).
    14: (head(14, "第二冊", "十四"), HEAD + "第二冊十四"),
}
PROFILE = {"preferEngine": "adjudicate", "layout": {"runningHeads": [HEAD], "runningHeadGlyph": {HEAD: GLYPH}}}


OFF = ["--no-learned-heads"]


def run(only=None, extra=(), profile=PROFILE, formatter=None, pages=None):
    """The book's pages ONLY (all by default) with 裁決 answering that what one
    engine read is printed.  The whole book is in the draft directory: the
    running head is learned over it, whichever pages are proofread.  PAGES:
    another book (the made-up one by default)."""
    book = pages or PAGES
    model = stand_in.StandIn(census=0, judge=lambda a, b: a or b, formatter=formatter)
    with stand_in.book(book, profile=profile) as root:
        out = stand_in.run(root, model, pages=",".join(str(p) for p in sorted(only or book)), extra=extra)
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return out, report


def text(result, page):
    return pp.CJK(result[page][1])


def learned_changes(record, rule):
    return [(c["evidence"]["text"], c["evidence"].get("band")) if rule == "learned-head-block"
            else (c["engineA"], c["engineB"], c["evidence"]["readBy"])
            for c in record["decisionChanges"] if c.get("rule") == rule]


def main() -> int:
    # What is learned, from engine A's blocks alone.
    blocks = {page: [dict(b, box=b["boundingBox"], label=b["blockLabel"]) for b in PAGES[page][0]] for page in PAGES}
    learned = pp.learn_heads(blocks, [HEAD])
    check("learn: the stem, its type size, the labels printed beside it on three pages or more, their family",
          (learned["stems"], learned["glyph"], learned["thickness"], learned["labels"], learned["families"]),
          ([HEAD], {HEAD: GLYPH}, {HEAD: 0.03}, {"第一冊": 3, "第二冊": 6}, ["第N冊"]))
    check("learn: the folio sequence (the scan's page less 0) from the folios read in the band",
          learned["folio"]["runs"], [{"first": 1, "last": 14, "offset": 0, "pages": 10}])
    check("learn: where the anchors stand on odd and on even pages",
          [(r["parity"], r["axis"], r["span"]) for r in learned["regions"]],
          [("even", "column", [0.18, 0.21]), ("odd", "column", [0.74, 0.77])])
    check("learn: nothing without a stem", pp.learn_heads(blocks, [])["labels"], {})
    check("a band block the merge left part of: engine A's reading all the head, what is left a part of it in "
          "order - left out; not a merged text with anything else, nor engine A's reading of anything else",
          [pp.learned_head_block(span, "column of block 0", learned, 3, own) is not None
           for span, own in (("二冊", "第二冊"), ("二冊凡", "第二冊"), ("冊二", "第二冊"), ("二冊", "凡例二冊"))],
          [True, False, False, False])
    check("numerals: figure by figure, counted, with a one-character ten",
          [pp.numeral_value(t) for t in ("一七七", "一百七十七", "十二", "廿二", "二〇", "一百〇五", "天")],
          [177, 177, 12, 22, 20, 105, None])
    check("numerals: the forms a folio is printed in", sorted(pp.numeral_forms(22)), ["二二", "二十二", "廿二"])
    check("explained: title, label and folio; the title misread once, or a character of it not read; not a "
          "sentence or a title with the title in it", [pp.learned_leftover(t, learned)[0] for t in (
              HEAD + "第一冊一五", "某甲先生言衍錄第三冊", "某甲先生行錄第二冊", "第二冊", HEAD + "之言行可法",
              HEAD + "序", "某甲先生行錄序")], [0, 0, 0, 0, 5, 1, 1])

    off, report_off = run(extra=OFF)
    on, report_on = run()
    check("off: the label beside the title, the title with its label, the folio all in the text",
          [("第一冊" in text(off, 1), "一" + BODY[0][:2] in text(off, 1) or text(off, 1).startswith("第一冊一")),
           HEAD + "第一冊" in text(off, 2), "第三冊" in text(off, 10)],
          [(True, True), True, True])
    check("on: the body alone on every page of the book's usual shape, a label spaced out and a title's box "
          "longer than its text among them", [text(on, p) for p in (1, 2, 3, 4, 9, 13)], [BODY[0] + BODY[1]] * 6)
    check("on: each block left out is recorded, with its place in the band",
          learned_changes(on[1][0], "learned-head-block"),
          [("第一冊", "column of block 0"), ("一", "column of block 0")])
    check("on: the title with its label as one block, and its folio",
          learned_changes(on[2][0], "learned-head-block"),
          [(HEAD + "第一冊", f"anchor ({HEAD})"), ("二", "column of block 0")])
    check("on: a label of the learned family never printed before is left out; a word that is no label is kept",
          (learned_changes(on[10][0], "learned-head-block"), "凡例" in text(on, 10)),
          ([("第三冊", "column of block 0"), ("十", "column of block 0")], True))
    check("divider: the label printed as a large title stays, with the switch and without",
          (text(on, 5), text(off, 5).count("第二冊")), ("第二冊" + BODY[0] + BODY[1], 2))
    check("no anchor: the label in the learned region is left out; a numeral block and a body line there stay",
          (learned_changes(on[6][0], "learned-head-block"), text(on, 6)),
          ([("第二冊", "region (even pages' column 0.180-0.210)")], "十二始制文字乃服衣裳" + BODY[0] + BODY[1]))
    check("the title misread twice over is no stem: kept; its label in the learned region left out, the folio "
          "there kept", (learned_changes(on[11][0], "learned-head-block"), text(on, 11), text(off, 11)),
          ([("第二冊", "region (odd pages' column 0.740-0.770)")], "某乙先王言行錄十一" + BODY[0] + BODY[1],
           "某乙先王言行錄第二冊十一" + BODY[0] + BODY[1]))
    check("engine B's reading of the head column in the body, engine A's none: left out with the switch",
          (HEAD in text(off, 7), text(on, 7), learned_changes(on[7][0], "learned-head-reading")),
          (True, BODY[0] + BODY[1], [("", HEAD + "第二冊七", "B")]))
    check("... and not asked of 裁決",
          ([a["writer_reading"] for a in off[7][0]["adjudications"]],
           [a["writer_reading"] for a in on[7][0]["adjudications"]]),
          ([HEAD + "第二冊七"], []))
    check("engine B's label alone between the title and the folio: asked of 裁決 with the switch alone, its "
          "answer then the folio block's, which goes whole",
          ([(a["draft_reading"], a["writer_reading"]) for a in on[8][0]["adjudications"]],
           learned_changes(on[8][0], "learned-head-block"), text(on, 8), "第二冊" in text(off, 8)),
          ([("", "第二冊")], [("第二冊八", "column of block 0")], BODY[0] + BODY[1], True))
    # The body naming the book - the title one character short, or whole -
    # in a sentence whose words only one engine read: engine B (page 15), or
    # engine A (page 16).  Text alone is no evidence of the head: it is asked
    # of 裁決, as without the switch, and kept.
    naming = dict(PAGES)
    naming[15] = (head(15, "第二冊", "十五") + body(15, [BODY[0] + "嘗讀可法", BODY[1]]),
                  HEAD + "第二冊十五" + BODY[0] + "嘗讀" + HEAD[:-1] + "可法" + BODY[1])
    naming[16] = (head(16, "第二冊", "十六") + body(16, [BODY[0] + "嘗讀" + HEAD + "一書", BODY[1]]),
                  HEAD + "第二冊十六" + BODY[0] + "嘗讀一書" + BODY[1])
    # Page 17: engine A read the head twice - the body's first column, the
    # title with its label and the folio as one block, and the title with its
    # label as a block of their own; engine B read it once, the folio first.
    first, second = body(17, [BODY[0] + HEAD + "第二冊十七", BODY[1]])
    naming[17] = ([first] + head(17, "第二冊", together=True) + [second],
                  BODY[0] + "十七" + HEAD + "第二冊" + BODY[1])
    named = {label: run([15, 16, 17], extra, pages=naming)[0]
             for label, extra in (("off", OFF), ("on", []))}
    check("the body naming the book, read by one engine inside a block of engine A's: kept and asked of 裁決, "
          "with the switch as without",
          [(HEAD[:-1] + "可法" in text(named[label], 15), HEAD + "一書" in text(named[label], 16),
            [(a["draft_reading"], a["writer_reading"]) for p in (15, 16) for a in named[label][p][0]["adjudications"]],
            [c["evidence"] for p in (15, 16) for c in named[label][p][0]["decisionChanges"]
             if c.get("rule") == "learned-head-reading"]) for label in ("off", "on")],
          [(True, True, [("", HEAD[:-1]), (HEAD, "")], [])] * 2)
    check("... while engine B's head reading between the body's columns is left out where engine A passes from one "
          "block to the next (its place recorded)",
          [(c["evidence"]["readBy"], c["evidence"].get("blocks")) for c in on[7][0]["decisionChanges"]
           if c.get("rule") == "learned-head-reading"], [("B", [0, 1])])
    check("... and engine A's second reading inside a block, where only the folio both engines read stands "
          "between it and the block's end: left out, not asked; kept and asked without the switch",
          [(text(named[label], 17), [(a["draft_reading"], a["writer_reading"]) for a in named[label][17][0]["adjudications"]],
            [(c["evidence"]["readBy"], c["evidence"].get("blocks")) for c in named[label][17][0]["decisionChanges"]
             if c.get("rule") == "learned-head-reading"]) for label in ("off", "on")],
          [(BODY[0] + HEAD + "第二冊十七" + HEAD + "第二冊" + BODY[1], [(HEAD + "第二冊", "")], []),
           (BODY[0] + "十七" + BODY[1], [], [("A", [0])])])

    # The head drifting over the book: on three even pages its column stands
    # further in, so the even pages' region spans both places.  Page 22 prints
    # the head where it mostly stands and, beside it inside that span, a
    # column reading a volume's name alone at the head's size (a contents
    # line).  The page's anchor is read: the band is its column, and the
    # region is not used there.
    drift = dict(PAGES)
    for page, folio in ((16, "十六"), (18, "十八"), (20, "二十")):
        drift[page] = ([Block(HEAD, [0.22, 0.17, 0.03, GLYPH * 7], "header"),
                        Block("第二冊", [0.22, 0.33, 0.03, GLYPH * 3], "vertical_text"),
                        Block(folio, [0.22, 0.66, 0.03, 0.03], "number")] + body(page),
                       HEAD + "第二冊" + folio + BODY[0] + BODY[1])
    drift[22] = (head(22, "第二冊", "二二") + [Block("第三冊", [0.215, 0.40, 0.03, GLYPH * 3], "vertical_text")]
                 + body(22), HEAD + "第二冊二二" + "第三冊" + BODY[0] + BODY[1])
    drifted = pp.learn_heads({page: [dict(b, box=b["boundingBox"], label=b["blockLabel"]) for b in drift[page][0]]
                              for page in drift}, [HEAD])
    beside, _ = run([22], [], pages=drift)
    check("the head's drift widens its region; a page whose anchor is read has its band in the anchor's column: "
          "a column beside it in the region reading a volume's name is kept, the head's label and folio left out",
          ([r["span"] for r in drifted["regions"] if r["parity"] == "even"], text(beside, 22).startswith("第三冊"),
           learned_changes(beside[22][0], "learned-head-block")),
          ([[0.18, 0.25]], True, [("第二冊", "column of block 0"), ("二二", "column of block 0")]))

    check("a page of nothing but its running head: empty with the switch, and clean (the structure pass, which "
          "would answer nothing, not asked)",
          (text(off, 14), [d["kind"] for d in on[14][0]["doubts"]], on[14][1]), ("第二冊十四", [], ""))

    # The structure pass answering nothing twice: the fallback, a paragraph a
    # block, keeps no block the switch left out.
    refused_off, _ = run([2], OFF, formatter=lambda given, kind: "")
    refused_on, _ = run([2], [], formatter=lambda given, kind: "")
    check("structure pass refused: the fallback keeps the title with its label without the switch, not with it",
          ([d["kind"] for d in refused_off[2][0]["doubts"]], HEAD + "第一冊" in text(refused_off, 2),
           [d["kind"] for d in refused_on[2][0]["doubts"]], text(refused_on, 2)),
          (["structure-rejected"], True, ["structure-rejected"], BODY[0] + BODY[1]))

    # The structure pass dropping thirteen characters of the body, two of them
    # the folio's numeral: the deletion guard refuses it with the switch as
    # without - the folio left out is not furniture a deleted run may consist of.
    dropping = lambda given, kind: given.replace(LOST, "")
    guard = {label: run([12], extra, formatter=dropping)[0][12]
             for label, extra in (("off", OFF), ("on", []))}
    check("the deletion guard: a body run holding the folio's numeral dropped by the structure pass is refused, "
          "with the switch as without", [(sorted({d["kind"] for d in guard[label][0]["doubts"]}),
                                          LOST in pp.CJK(guard[label][1])) for label in ("off", "on")],
          [(["structure-rejected"], True), (["structure-rejected"], True)])

    # The record.
    check("off: no provenance key, no decision, no run report key",
          ({p: "learnedHeads" in off[p][0]["provenance"] for p in PAGES} == {p: False for p in PAGES},
           sum(1 for p in PAGES for c in off[p][0]["decisionChanges"] if c["decision"] == "learnedHeads"),
           "learnedHeads" in report_off, [page.get("learnedHeadBlocks") for page in report_off["pages"]][:1]),
          (True, 0, False, [None]))
    check("on: the learned head sealed in provenance (its hash), the same on every page",
          len({on[p][0]["provenance"].get("learnedHeads") for p in PAGES}), 1)
    check("on: the run report says where it was learned, what, and how much it left out",
          (report_on["learnedHeads"]["from"], report_on["learnedHeads"]["learned"]["labels"],
           report_on["learnedHeads"]["blocksLeftOut"], report_on["learnedHeads"]["readingsLeftOut"]),
          ("drafts", {"第一冊": 3, "第二冊": 6}, 24, 1))
    # The profile's learnedHeads, when it has one, is what is used.
    profiled = dict(PROFILE, layout=dict(PROFILE["layout"], learnedHeads=dict(learned, labels={"第一冊": 3},
                                                                              families=[])))
    from_profile, report_profile = run([4, 9], [], profile=profiled)
    check("the profile's learned head is used as it is (here: no label of volume two)",
          (report_profile["learnedHeads"]["from"], "第二冊" in text(from_profile, 9)), ("profile", True))
    profile_off, _ = run([4, 9], OFF, profile=profiled)
    check("... and without the switch a profile holding one is read as before",
          [profile_off[p][1] == off[p][1] for p in (4, 9)], [True, True])
    # book_profile.py writes it (unless --no-learned-heads); without, the layout is as before.
    with stand_in.book(PAGES) as root:
        with_it = bp.survey_layout(sorted(PAGES), root / "a", True)
        without = bp.survey_layout(sorted(PAGES), root / "a")
    check("book_profile: learnedHeads only with the switch, learned from the heads it measured; the rest of "
          "the layout as without", ("learnedHeads" in without,
                                    with_it.get("learnedHeads") == pp.learn_heads(blocks, with_it["runningHeads"]),
                                    {k: v for k, v in with_it.items() if k != "learnedHeads"} == without),
          (False, True, True))
    check("... and phase 3, the profile having none, learns the same from the same pages and the profile's heads",
          report_on["learnedHeads"]["learned"] == learned, True)
    parse_profile = lambda *extra: bp.build_parser().parse_args(["r", "a", "b", "p.json", *extra]).learned_heads
    parse_pages = lambda *extra: pp.build_parser().parse_args(["r", "a", "o", *extra]).learned_heads
    check("parsing: on by default, off with --no-learned-heads, in book_profile.py and proofread_pages.py",
          [(parse(), parse(*OFF)) for parse in (parse_profile, parse_pages)], [(True, False)] * 2)

    # Resume: a page sealed with the switch is not current for a run without
    # it, nor the reverse, nor for another learned head.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page-0001.json"
        markdown = BODY[0]
        base = dict(prefer_engine="A", fallback_engine="A")
        with_it = pp.decision_provenance(SimpleNamespace(**base, learned_heads=True, learned=learned))
        other = pp.decision_provenance(SimpleNamespace(**base, learned_heads=True, learned=dict(learned, labels={})))
        without = pp.decision_provenance(SimpleNamespace(**base))
        job = SimpleNamespace(out_json=out, out_md=out.with_suffix(".md"))
        results = []
        for sealed_with, run_with in ((with_it, without), (without, with_it), (with_it, other),
                                      (with_it, with_it), (without, without)):
            record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "scanPage": 1,
                      "provenance": {**sealed_with, "markdownSha256": pp.sha256_bytes(markdown.encode("utf-8"))}}
            record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
            out.with_suffix(".md").write_text(markdown, encoding="utf-8")
            out.write_bytes(pp.canonical_json_bytes(record))
            results.append(pp.validate_completion(job, run_with)[0])
    check("resume: current only for a run with the same setting and the same learned head",
          results, [False, False, False, True, True])

    # The profile's running heads (survey_layout): 天地会 5's notes under the
    # body of scans 137-146, two or three to a page on 5 of the 9 pages
    # that print them, read alike once CJK() drops their figures, and a
    # biography's source line printed twice on a page (scans 358, 360 and
    # 361), are no head (each holds a full stop); a facsimile's head
    # printed on both its leaves is.
    note = "{}福康安奏折全文见本书第{}页。"
    counts = {137: 2, 138: 2, 139: 1, 140: 3, 141: 1, 142: 0, 143: 2, 144: 3, 145: 1, 146: 1}
    notes = {page: ([Block(BODY[page % 3], [0.1, 0.1, 0.8, 0.5])]
                    + [Block(note.format("①②③"[k], 93 + page - 137 + k), [0.15, 0.7 + 0.02 * k, 0.4, 0.015],
                             "footnote") for k in range(n)]
                    + [Block("《彰化县志》，卷三。", [0.6, 0.62 + 0.05 * k, 0.3, 0.015]) for k in range(2)]
                    + [Block(HEAD, [0.1 + 0.5 * k, 0.03, 0.2, 0.02], "header") for k in range(2)], "")
             for page, n in counts.items()}
    with stand_in.book(notes) as root:
        heads = bp.survey_layout(sorted(notes), root / "a")["runningHeads"]
    check("book_profile: notes and source lines that recur on a page are no running head (they hold a full stop); "
          "a head printed on both leaves of a page is", heads, [HEAD])
    print(f"{'FAIL' if failures else 'ok  '} {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
