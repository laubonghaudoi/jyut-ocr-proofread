#!/usr/bin/env python3
"""Offline checks of a punctuation pass whose answers are both refused.

No model, no GPU, no corpus.  The shape is the one measured on a magazine page:
engine B wrote printed commas as a look-alike character (丶) that the merge kept,
the punctuator read it as the comma it is and dropped it, and both its answers
were refused for changing the characters - while engine A had printed the comma
at that place.  The stretch now keeps the marks the refused answer and engine A
agree on, and is reported partial, instead of being left bare.  The curly
quotation marks are among the marks it keeps (FALLBACK_MARKS), though the step
neither counts nor asks for them.

Usage:
    python3 tests/check_partial_punctuation.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def merged(a_text: str, b_text: str, vocabulary: str = pp.FALLBACK_MARKS) -> tuple[str, list[str]]:
    """Engine A's marks (those of VOCABULARY: the fallback's, as the runner
    reads them) at the gaps of a merge that keeps every character either
    engine read (engine B's insertions included), as a prefer-A merge does."""
    segments = pp.align_drafts(a_text, b_text)
    pieces = [seg["a"] or seg["b"] for seg in segments]
    return "".join(pieces), pp.gaps_of(pp.engine_marks_merged(a_text, segments, pieces, vocabulary=vocabulary))


# Marks tied to the character they go with; an opening mark to the next one.
check("anchored marks", pp.anchored_marks("卽：「以剷除」。"),
      ("卽以剷除", [(0, False, "："), (1, True, "「"), (3, False, "」"), (3, False, "。")]))
check("vertical and ASCII brackets are brackets", pp.anchored_marks("甲︵乙)")[1],
      [(1, True, "（"), (1, False, "）")])
# A stretch's edge gaps hold only its own marks.
tied = pp.tied_marks(4, [(1, False, "。"), (2, True, "「")], {0: 0, 1: 1, 2: 2, 3: 3})
check("marks across a cut between stretches", (pp.gaps_of(tied, 0, 2), pp.gaps_of(tied, 2, 2)),
      (["", "", "。"], ["「", "", ""]))

# Engine B read the printed comma as 丶; the merge kept it after engine A's 旨.
span, printed = merged("宗旨，卽以剷除。", "宗旨丶卽以剷除")
check("merged text keeps engine B's look-alike", span, "宗旨丶卽以剷除")
check("engine A's marks on the merged text", printed, ["", "", "，", "", "", "", "", "。"])
# The refused answer dropped 丶 for the comma it is: characters changed.
refused = ["宗旨，卽以剷除。"]
check("marks both put at one place", pp.agreed_punctuation(span, refused, printed, False),
      ("宗旨，丶卽以剷除。", 2))
check("a mark only the answer wrote is not kept",
      pp.agreed_punctuation(span, ["宗旨，卽以、剷除。"], printed, False), ("宗旨，丶卽以剷除。", 2))
check("a mark only engine A printed is not kept",
      pp.agreed_punctuation(span, ["宗旨卽以剷除。"], printed, False), ("宗旨丶卽以剷除。", 1))
check("another mark at the same place is not agreement",
      pp.agreed_punctuation(span, ["宗旨、卽以剷除。"], printed, False), ("宗旨丶卽以剷除。", 1))
# Circles: the same run only; a doubled-circle book's single 。 from engine A agrees with nothing.
span2, printed2 = merged("天地玄黃。宇宙洪荒。。", "天地玄黃宇宙洪荒")
check("single circle against single", pp.agreed_punctuation(span2, ["天地玄黃。宇宙洪荒。。"], printed2, False),
      ("天地玄黃。宇宙洪荒。。", 3))
check("a doubled-circle book: engine A's single 。 is no evidence of its shape",
      pp.agreed_punctuation(span2, ["天地玄黃。宇宙洪荒。。"], printed2, True), ("天地玄黃宇宙洪荒。。", 2))
check("single against doubled: no agreement",
      pp.agreed_punctuation(span2, ["天地玄黃。。宇宙洪荒。"], printed2, False), ("天地玄黃宇宙洪荒", 0))
check("two answers are not added into a doubled circle",
      pp.agreed_punctuation(span2, ["天地玄黃宇宙洪荒。", "天地玄黃宇宙洪荒。"], printed2, False),
      ("天地玄黃宇宙洪荒", 0))
check("of two answers, the one that agrees more at each place",
      pp.agreed_punctuation(span2, ["天地玄黃。宇宙洪荒", "天地玄黃宇宙洪荒。。"], printed2, False),
      ("天地玄黃。宇宙洪荒。。", 3))

# Curly quotation marks, which the step neither counts nor asks for: kept
# where a refused answer and engine A put them at one place, the opening one
# before the character after it; the step's own vocabulary reads none.
span3, printed3 = merged("答曰：“我乃陳近南。”眾僧", "答曰我乃陳近南眾僧")
check("curly quotes both put at one place are kept",
      pp.agreed_punctuation(span3, ["答曰：“我乃陳近南。”眾增"], printed3, False), ("答曰：“我乃陳近南。”眾僧", 4))
segments3 = pp.align_drafts("甲乙。“丙丁。”", "甲乙丙丁")
pieces3 = [seg["a"] or seg["b"] for seg in segments3]
check("an opening curly quote goes with the character after it (the stretch it opens)",
      pp.gaps_of(pp.engine_marks_merged("甲乙。“丙丁。”", segments3, pieces3, vocabulary=pp.FALLBACK_MARKS), 2, 2),
      ["“", "", "。”"])
check("the step's own marks hold no curly quote",
      pp.anchored_marks("答曰：“我乃陳近南。”")[1], [(1, False, "："), (6, False, "。")])


class Arguments:
    max_tokens = 100
    profile_data = None


def run_part(printed_marks, profile=None):
    arguments = Arguments()
    arguments.profile_data = profile
    model = stand_in.StandIn(census=3, punctuate=lambda text: text.replace("丶", "，") + "。")
    return pp.punctuate_part(model, arguments, None, span, "", 60.0, printed=printed_marks)


out, record, _ = run_part(printed)
check("both answers refused, the agreed marks kept", (out, record["ok"], record["marks"]),
      ("宗旨，丶卽以剷除。", False, 2))
check("the pass records what it kept", record["partial"], {"kept": 2, "engineA": 2, "doubledCircles": False})
check("the refused answers are sealed", len(record["refusedOutputs"]), 2)
doubts = pp.punctuation_doubts([dict(record, block=1, range=[0, len(span)])])
check("a partial pass is a doubt of its own", [d["kind"] for d in doubts], ["punctuation-partial"])
check("the doubt says what was kept", "kept only the 2 mark(s)" in doubts[0]["detail"], True)
out, record, _ = run_part(None)
check("no engine A marks: left bare as before", (out, "partial" in record), (span, False))
check("left bare is still that doubt",
      [d["kind"] for d in pp.punctuation_doubts([dict(record, block=1)])], ["punctuation-left-bare"])
out, record, _ = run_part(printed, {"notation": {"doubled": True}})
check("doubled-circle book: the comma kept, not the single circle", (out, record["partial"]["kept"]),
      ("宗旨，丶卽以剷除", 1))


# A page: engine B's look-alike kept in the merge, the punctuator refused twice.
text_a, text_b = "宗旨，卽以剷除共匪爲主。", "宗旨丶卽以剷除共匪爲主"
with stand_in.book({1: ([stand_in.block(text_a, [0.3, 0.1, 0.4, 0.8])], text_b)}) as root:
    # The look-alike is kept by the merge here: one occurrence is no evidence
    # for the mark rule, and 裁決, asked about it (D3), reads it there.
    model = stand_in.StandIn(census=2, punctuate=lambda text: text.replace("丶", "，") + "。",
                             judge=lambda a, b: b or a or "無")
    record, markdown = stand_in.run(root, model)[1]
check("page: agreed marks on the page", markdown.strip(), "宗旨，丶卽以剷除共匪爲主。")
check("page: not clean, partial", [d["kind"] for d in record["doubts"]], ["punctuation-partial"])
check("page: the pass is refused and partial",
      [(p["ok"], (p.get("partial") or {}).get("kept")) for p in record["punctuationPasses"]], [(False, 2)])

# A page whose punctuator writes the curly quotes engine A reads, refused
# twice for a character it changed: the fallback keeps the quotes too.
text_a, text_b = "答曰：“我乃陳近南。”眾僧", "答曰我乃陳近南眾僧"
with stand_in.book({1: ([stand_in.block(text_a, [0.3, 0.1, 0.4, 0.8])], text_b)}) as root:
    model = stand_in.StandIn(census=4, punctuate=lambda text: text.replace("曰", "曰：“").replace("南", "南。”")
                             .replace("僧", "增"), judge=lambda a, b: a or b or "無")
    record, markdown = stand_in.run(root, model)[1]
check("page: the curly quotes engine A and the refused answers agree on are kept", markdown.strip(), text_a)
check("page: ... counted as kept marks",
      [((p.get("partial") or {}).get("kept"), (p.get("partial") or {}).get("engineA")) for p in record["punctuationPasses"]],
      [(4, 4)])


# A table block of an order-conflict page read from engine B's lines (window_merge):
# its segments hold each of the block's characters once, in engine B's order, so
# they spell engine A's text there only as a whole.  The run stands for the
# block's stretch and holds no mark; the text around it keeps engine A's marks.
# (Before, one window made the whole page's segments fail to spell engine A's
# text, and no block of it had engine A's marks.)
window = [{"tag": "equal", "a": "天地玄黃", "b": ""},
          {"tag": "equal", "a": "丙丁", "b": "丙丁", "window": 1},
          {"tag": "insert", "a": "", "b": "戊", "window": 1},
          {"tag": "equal", "a": "甲乙", "b": "甲乙", "window": 1},
          {"tag": "equal", "a": "宇宙", "b": ""}]
window_pieces = [seg["a"] or seg["b"] for seg in window]
window_blocks = [{"text": "天地，玄黃。"}, {"text": "<table><tr><td>甲（乙）</td><td>丙丁</td></tr></table>"},
                 {"text": "宇宙。"}]
a_window = "天地，玄黃。\n甲（乙）丙丁\n宇宙。"
want = ["", "", "，", "", "。", "", "", "", "", "", "", "。"]
check("a window's run stands for its block's stretch",
      pp.gaps_of(pp.engine_marks_merged(a_window, window, window_pieces, window_blocks)), want)
check("... without the blocks, as a stretch of the same characters",
      pp.gaps_of(pp.engine_marks_merged(a_window, window, window_pieces)), want)
check("a window that is not its block's text: no marks",
      pp.engine_marks_merged(a_window, window, window_pieces,
                             [window_blocks[0], {"text": "甲乙丙己"}, window_blocks[2]]), None)
check("a window whose characters are not the stretch's: no marks",
      pp.engine_marks_merged(a_window, [dict(seg, a="甲己") if seg.get("a") == "甲乙" else seg for seg in window],
                             window_pieces), None)

# The page (made up, the shape measured on a textbook table's continuation page):
# the table block engine B reads the other way round, then two paragraphs whose
# punctuation answers both drop a character.  The first paragraph keeps the mark
# its refused answer and engine A agree on.
x, y, z = "甲乙丙丁戊己庚辛", "壬癸子丑寅卯辰巳", "青赤黃白黑紫綠藍"
p1 = "天地玄黃，宇宙洪荒。日月盈昃，辰宿列張。寒來暑往秋收冬藏閏餘成歲"
p2 = "律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
grid = "<table><tr><td>" + x + y + "</td></tr><tr><td>" + z + "</td></tr></table>"
blocks = [stand_in.block(grid, [0.1, 0.1, 0.8, 0.3], "table"), stand_in.block(p1, [0.1, 0.45, 0.8, 0.2]),
          stand_in.block(p2, [0.1, 0.7, 0.8, 0.2])]
with stand_in.book({1: (blocks, "\n".join([pp.CJK(p2), z, y, x, pp.CJK(p1)]))}) as root:
    model = stand_in.StandIn(census=4, punctuate=lambda text: text[:3] + "，" + text[4:8] + "。" + text[8:])
    record, markdown = stand_in.run(root, model)[1]
check("window page: an order conflict with the table read from engine B's lines",
      (record["mode"], [r["from"] for r in record["tablesRebuilt"]]),
      ("merge-order-conflict", ["grid refused, text from engine B's lines"]))
check("window page: the paragraph keeps the agreed mark",
      [(p["block"], (p.get("partial") or {}).get("kept"), (p.get("partial") or {}).get("engineA"))
       for p in record["punctuationPasses"]], [(1, 1, 4), (2, None, None)])
check("window page: the paragraph as written", "天地玄黃宇宙洪荒。日月盈昃" in markdown, True)
check("window page: partial, not bare, for the paragraph",
      [d["kind"] for d in record["doubts"] if d["kind"].startswith("punctuation")],
      ["punctuation-partial", "punctuation-left-bare"])

sys.exit(1 if failures else 0)
