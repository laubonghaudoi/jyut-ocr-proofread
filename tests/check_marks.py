#!/usr/bin/env python3
"""Checks for the mark rule (decision D1): a character one engine has in excess where
the other engine printed a mark is that mark misread, for the glyphs this book's
drafts show the engine writing for marks.

No model, no GPU, no corpus.  The shapes are the ones measured on a magazine page:
engine B wrote full-cell commas as 丶, a colon and an opening quote as 号一, a
printed ▲ as 厶, and the merge kept them as characters while engine A had printed
every mark.

Usage: python3 tests/check_marks.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
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


# engine_marks: printed marks only; ASCII (engine B's grounding boxes) never counts.
check("engine_marks keeps printed marks", pp.engine_marks("宗旨，卽：「以"), ("宗旨卽以", ["", "", "，", "：「", ""]))
check("ASCII brackets and digits are not marks", pp.engine_marks("甲(346,309)乙")[1], ["", "", ""])
check("bullets and leaders are marks", pp.engine_marks("▲本刊……顚公")[1], ["▲", "", "……", "", ""])

# explain_by_marks, on readings measured on the magazine page.
check("B's 丶 is A's comma", pp.explain_by_marks("", ["，"], "丶", 0, {"丶"}), ("", [("丶", "，")]))
check("B's 号一 is A's ：「", pp.explain_by_marks("", ["：「"], "号一", 0, {"号", "一"}),
      ("", [("号", "："), ("一", "「")]))
check("査丶 against 查: 査 is left for the variant path",
      pp.explain_by_marks("查", ["", "，"], "査丶", 0, {"丶"}), ("査", [("丶", "，")]))
check("a glyph the book never showed for a mark is not taken", pp.explain_by_marks("", ["，"], "盡", 0, {"丶"}), None)
check("engine B printed its own comma there: its 丶 is not the comma",
      pp.explain_by_marks("", ["，"], "丶", 1, {"丶"}), None)
check("no mark where the excess is: nothing explained", pp.explain_by_marks("", [""], "丶", 0, {"丶"}), None)
check("one mark accounts for one glyph", pp.explain_by_marks("", ["，"], "丶丶", 0, {"丶"}), ("丶", [("丶", "，")]))
# Which glyph was which mark, when the marks account for two of three: the pairs
# the book showed more often (爹 for ；, 一 for （).
evidence = {"爹": {"；": 30}, "一": {"（": 40, "：": 1}}
check("ambiguous leftover goes to the book's evidence",
      pp.explain_by_marks("", ["；（"], "爹一一", 0, evidence), ("一", [("爹", "；"), ("一", "（")]))
check("the same glyphs on a book where 一 stands for both marks and 爹 for none",
      pp.explain_by_marks("", ["；（"], "爹一一", 0, {"爹": {}, "一": {"；": 30, "（": 40}}),
      ("爹", [("一", "；"), ("一", "（")]))


def settle(a_text: str, b_text: str, aliases: dict) -> list:
    """mark_explanation on every disagreement of two readings."""
    segs = pp.align_drafts(a_text, b_text)
    out, a_at, b_at = [], 0, 0
    stream_a, stream_b = pp.engine_marks(a_text), pp.engine_marks(b_text)
    for seg in segs:
        if seg["tag"] != "equal":
            found = pp.mark_explanation(seg, a_at, b_at, stream_a, stream_b, aliases)
            out.append((seg["a"], seg["b"], found and (found[0], found[1] if found[0] != "reduced"
                                                       else (found[1]["a"], found[1]["b"]))))
        a_at += len(seg["a"])
        b_at += len(seg["b"])
    return out


A = "一、本刊立言以反共救民會之宗旨爲宗旨，卽：「以剷除共匪」。"
B = "一丶本刋立言以反共救民會之宗旨爲宗旨丶卽号一以剷除共匪一口"
aliases = {"A": {}, "B": {"丶": {"，": 9, "、": 3}, "号": {"：": 2}, "一": {"「": 4}, "口": {"。": 5}}}
check("a line of the magazine page", settle(A, B, aliases), [
    ("", "丶", ("A", "")), ("刊", "刋", None), ("", "丶", ("A", "")), ("", "号一", ("A", "")),
    ("", "一口", ("A", ""))])
check("engine A's excess the other way round (A's 〇 for B's 。)",
      settle("天地〇玄黃", "天地。玄黃", {"A": {"〇": {"。": 3}}, "B": {}}), [("〇", "", ("B", ""))])
check("glyphs not seen for marks on this book: nothing settled",
      settle(A, B, {"A": {}, "B": {}}), [("", "丶", None), ("刊", "刋", None), ("", "丶", None),
                                         ("", "号一", None), ("", "一口", None)])


def write_pages(root: Path, pages: list[tuple[str, str]]) -> list[tuple[Path, Path]]:
    (root / "a").mkdir()
    (root / "b").mkdir()
    out = []
    for n, (a_text, b_text) in enumerate(pages, 1):
        pa, pb = root / "a" / f"page-{n:04d}.txt", root / "b" / f"page-{n:04d}.txt"
        pa.write_text(a_text, encoding="utf-8")
        pb.write_text(b_text, encoding="utf-8")
        out.append((pa, pb))
    return out


# mark_evidence / mark_aliases: from the book's own drafts.
with tempfile.TemporaryDirectory() as tmp:
    pairs = write_pages(Path(tmp), [
        ("甲乙，丙丁。戊己", "甲乙丶丙丁口戊己"),          # 丶 and 口 at marks
        ("庚辛，壬癸，子丑", "庚辛丶壬癸丶子丑"),          # 丶 twice more
        ("寅卯辰巳", "寅卯口辰巳"),                          # 口 where nothing is printed
        ("午未，申酉", "午未盡申酉"),                        # 盡 once at a mark: a coincidence
        ("八" * 300, "八" * 300 + "丶" * 50),               # a loop: left out
    ])
    evidence = pp.mark_evidence(pairs)
    found = pp.mark_aliases(evidence)
check("evidence counts explained and unexplained excess", {c: (r["explained"], r["unexplained"])
                                                            for c, r in evidence["B"].items()},
      {"丶": (3, 0), "口": (1, 1), "盡": (1, 0)})
check("aliases: at two sites or more, explained more often than not", found, {"A": {}, "B": {"丶": {"，": 3}}})
check("each alias carries its marks", found["B"]["丶"], {"，": 3})


# A page: engine B wrote the commas as 丶 at two places; the merge keeps none of them.
text_a, text_b = "宗旨，卽以剷除，共匪爲主。", "宗旨丶卽以剷除丶共匪爲主"
with stand_in.book({1: ([stand_in.block(text_a, [0.3, 0.1, 0.4, 0.8])], text_b)}) as root:
    record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
check("page: the look-alike glyph is not in the text", pp.CJK(markdown), "宗旨卽以剷除共匪爲主")
check("page: settled as engine A's marks misread",
      [(d["engineB"], d["resolved"], d["resolved_by"], d["marks"]) for d in record["engineDisagreements"]],
      [("丶", "", "mark-not-character-A", [["丶", "，"]])] * 2)
check("page: each change sealed for decision D1",
      [(c["decision"], c["rule"], c["after"], c["mergedOffset"], c["block"]) for c in record["decisionChanges"]],
      [("D1", "mark-not-character", "", 2, 0), ("D1", "mark-not-character", "", 6, 0)])
check("page: the evidence names the glyph, its mark and the book's counts",
      record["decisionChanges"][0]["evidence"], {"marks": [["丶", "，"]], "glyphsOf": "B",
                                                 "aliases": {"丶": {"，": 2}}})
check("page: the rule is sealed as on", record["provenance"]["markRule"], "on")

# A numeral conflict the rule settles: engine B's 一 for engine A's comma after a
# figure (the book shows 一 for the comma twice more).
box = [0.3, 0.1, 0.4, 0.8]
rest = "本會同人議決以後凡屬本省之事務均歸本會辦理"
pages = {1: ([stand_in.block("法第七〇三，條文" + rest, box)], "法第七〇三一條文" + rest),
         2: ([stand_in.block("甲乙，丙丁", box)], "甲乙一丙丁"),
         3: ([stand_in.block("戊己，庚辛", box)], "戊己一庚辛")}
with stand_in.book(pages) as root:
    asked, _ = stand_in.run(root, stand_in.StandIn(census=0), pages="1")[1]
check("a numeral: the comma taken for a mark, sealed for D1",
      [(c["engineA"], c["engineB"], c["after"]) for c in asked["decisionChanges"]], [("七〇三", "七〇三一", "七〇三")])

# A circle the rule would take for a mark, held to the circle rule's context: a book
# whose engine A writes 〇 for printed 。 (the alias), and a page where engine B
# printed circles at a withheld name 張〇〇 and a year 二〇 engine A read as 〇.
# Beside a numeral or another circle it may be a zero or a placeholder: 裁決 asks.
check("circle_withheld: beside a numeral", pp.circle_withheld(
    {"a": "二〇", "b": "二"}, 4, 4, ("B", "二", [("〇", "。")], "A"), ("甲乙丙丁二〇年", []), ("甲乙丙丁二年", []),
    {"label": "text"}), "a circle beside a numeral or another circle")
check("circle_withheld: alone in prose, the rule may take it", pp.circle_withheld(
    {"a": "〇旂", "b": "旒"}, 4, 4, ("reduced", {}, [("〇", "。")], "A"), ("甲乙丙丁〇旂年", []),
    ("甲乙丙丁旒年", []), {"label": "text"}), None)
check("circle_withheld: in a table", pp.circle_withheld(
    {"a": "〇旂", "b": "旒"}, 4, 4, ("reduced", {}, [("〇", "。")], "A"), ("甲乙丙丁〇旂年", []),
    ("甲乙丙丁旒年", []), {"label": "table"}), "a circle in a table block, or in no block found")
check("circle_withheld: no circle taken, nothing to hold", pp.circle_withheld(
    {"a": "", "b": "丶"}, 2, 2, ("A", "", [("丶", "，")], "B"), ("宗旨卽以", []), ("宗旨丶卽以", []), None), None)
rest = "本會同人議決以後凡屬本省之事務均歸本會辦理"
pages = {1: ([stand_in.block(rest + "張〇〇之事民國二〇年" + rest, box)], rest + "張。。之事民國二。年" + rest),
         2: ([stand_in.block("天地玄黃〇宇宙洪荒〇日月盈昃〇" + rest, box)], "天地玄黃。宇宙洪荒。日月盈昃。" + rest),
         3: ([stand_in.block("寒來暑往〇秋收冬藏〇" + rest, box)], "寒來暑往。秋收冬藏。" + rest)}
with stand_in.book(pages) as root:
    held, held_markdown = stand_in.run(root, stand_in.StandIn(census=0), pages="1")[1]
check("page: the placeholder and the zero go to 裁決, not the rule",
      [(a["draft_reading"], a["writer_reading"], a["trigger"], a["markWithheld"]["why"]) for a in held["adjudications"]],
      [("〇〇", "", "numeral-disagreement", "a circle beside a numeral or another circle"),
       ("二〇", "二", "numeral-disagreement", "a circle beside a numeral or another circle")])
check("page: kept by 裁決 (the stand-in answers engine A), no D1 change",
      ("張〇〇之事民國二〇年" in pp.CJK(held_markdown), held["decisionChanges"]), (True, []))

sys.exit(1 if failures else 0)
