#!/usr/bin/env python3
"""Checks for text engine B read in another order, placed once (decision D2).

No model, no GPU, no corpus.  The shape is the one measured on a magazine's colophon:
engine A read the labels first and the values after, engine B label by value, and
the merge kept both copies of every label; one price engine A read as 二元 and
engine B as 一元.

Usage: python3 tests/check_moves.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys

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


def moved(a_text: str, b_text: str):
    segments = pp.fuse_numeral_runs(pp.align_drafts(a_text, b_text))
    out, moves = pp.place_moved_once(segments)
    return segments, out, moves


def union(segments) -> str:
    """What the old merge under a preference for engine A wrote: engine A's text
    and every one-engine insertion of engine B's."""
    return "".join(seg["a"] or seg["b"] for seg in segments)


BODY = "本刊立言以反共救民會之宗旨爲宗旨絕對不涉及軍事政治與黨派問題"
A = BODY + "編輯者出版者全年念四期" + "二元二角六角" + "上海總郵局信箱八七九號"
B = BODY + "全年念四期" + "上海總郵局信箱八七九號" + "編輯者出版者一元二角六角價"
segments, out, moves = moved(A, B)
check("the old merge kept both copies", union(segments).count("編輯者"), 2)
check("each run placed once", [(m["text"], m["leftAtSite"]) for m in moves], [("編輯者出版者一元二角六角價", "價")])
check("engine A's order is kept", "".join(s["a"] for s in out), pp.CJK(A))
check("every character engine B read is in the segments once",
      "".join(s["b"] for s in sorted(out, key=lambda s: s["bStart"])), pp.CJK(B))
check("what matches is agreed at engine A's site",
      [s["a"] for s in out if s["tag"] == "equal" and "movedFrom" in s], ["編輯者出版者", "元二角六角"])
check("what differs there is a disagreement: a numeral question",
      [(s["a"], s["b"]) for s in out if s["tag"] != "equal" and "movedFrom" in s], [("二", "一")])
check("what engine A never read stays where engine B read it",
      [(s["tag"], s["b"]) for s in out if "movedRemainderOf" in s], [("insert", "價")])

# Printed twice and read twice by both engines: nothing moves.
twice = BODY + "編輯者出版者" + BODY[:6] + "編輯者出版者"
check("a phrase both engines read twice is never moved", moved(twice, twice)[2], [])
check("nor when engine B misses its second reading of one copy",
      [m["text"] for m in moved(twice, twice.replace("出版者", "", 1))[2]], [])
# Figures alone say nothing about order.
check("a run of figures never moves", moved(BODY + "一二三四五六", "一二三四五六" + BODY)[2], [])
# Two characters in common is not enough.
check("a coincidence of two characters does not move",
      moved(BODY + "甲乙丙丁", "某某甲乙" + BODY)[2], [])
# A replacement whose engine-A side would be left in two pieces is not touched.
tail = "天地玄黃宇宙洪荒"
seg_two = moved(BODY + "子丑寅卯辰巳午" + tail, BODY + "某某" + tail + "寅卯辰")
check("the shape: engine A's side of a replacement holds the run in its middle",
      [(s["tag"], s["a"], s["b"]) for s in seg_two[0] if s["tag"] != "equal"],
      [("replace", "子丑寅卯辰巳午", "某某"), ("insert", "", "寅卯辰")])
check("engine A's text would be left in two pieces around it: not touched", seg_two[2], [])
seg_one = moved(BODY + "子丑寅卯辰" + tail, BODY + "某某" + tail + "寅卯辰")
check("in one piece: the engine-B side goes with it", [(s["tag"], s["a"], s["b"]) for s in seg_one[1]
                                                       if s["tag"] != "equal"], [("replace", "子丑", "某某")])


def page(text_a_blocks, text_b, extra=()):
    # 裁決 keeps engine A's reading, and where engine A read none engine B's: the
    # one-character remainder (價) is a short insertion D3 asks about.
    with stand_in.book({1: ([stand_in.block(t, box) for t, box in text_a_blocks], text_b)}) as root:
        return stand_in.run(root, stand_in.StandIn(census=0, judge=lambda a, b: a or b or "無"), extra=list(extra))[1]


blocks = [(BODY, [0.5, 0.1, 0.4, 0.5]), ("編輯者出版者", [0.3, 0.1, 0.05, 0.3]),
          ("全年念四期二元二角六角", [0.2, 0.1, 0.05, 0.4])]
text_b = BODY + "全年念四期" + "編輯者出版者一元二角六角價"
record, markdown = page(blocks, text_b)
# Here the alignment pairs the labels and leaves the periods read in two places.
check("page: every run once", [pp.CJK(markdown).count(t) for t in ("編輯者", "全年念四期", "二角六角")], [1, 1, 1])
check("page: nothing engine B read is lost", "價" in pp.CJK(markdown), True)
change = [c for c in record["decisionChanges"] if c["decision"] == "D2"]
check("page: sealed for D2, both sites", [(c["moveSite"], c["engineA"], c["engineB"], c["after"], c["mergedOffset"])
                                         for c in change],
      [("read", "", "全年念四期", "", 30), ("placed", "全年念四期二", "全年念四期一", "全年念四期二", 36)])
check("page: where it was placed", change[0]["evidence"]["placedAt"]["context"], "者出版者[全年念四期]二元二角")
check("page: the difference at engine A's site went to the crop",
      [(a["draft_reading"], a["writer_reading"]) for a in record["adjudications"] if a.get("askedFor") != "D3"],
      [("二", "一")])
check("page: a doubt names both sites", [d["kind"] for d in record["doubts"]], ["moved-placed-once"])
# Engine B read the colophon at the page's end, with a character engine A never
# read between two matched stretches: that character is written at engine A's site,
# and the placed site's entry says so; the read site is at the merged text's end.
TAIL = "上海總郵局信箱八七九號凡屬同志均可投稿"
end_blocks = [(BODY, [0.5, 0.1, 0.4, 0.5]), ("編輯者出版者", [0.3, 0.1, 0.05, 0.3]), (TAIL, [0.2, 0.1, 0.05, 0.4])]
end_record, end_markdown = page(end_blocks, BODY + TAIL + "編輯者甲出版者")
check("page end: the run once, at engine A's site", pp.CJK(end_markdown), BODY + "編輯者甲出版者" + TAIL)
check("page end: both sites sealed, each with its offset",
      [(c["moveSite"], c["after"], c["mergedOffset"]) for c in end_record["decisionChanges"] if c["decision"] == "D2"],
      [("read", "", len(BODY) + 7 + len(TAIL)), ("placed", "編輯者甲出版者", len(BODY))])

# A block engine B reads in another order is nominated as a missed table, and
# its moved text is settled by the union (settle_by_union) - after D2, which
# places the half engine B read first at engine A's site.
first, second = "甲乙丙丁戊己庚辛壬癸", "青赤黃白黑紫綠藍灰褐"
with stand_in.book({1: ([stand_in.block(BODY, [0.5, 0.1, 0.4, 0.5]),
                         stand_in.block(first + second, [0.05, 0.05, 0.2, 0.3], label="vertical_text")],
                        BODY + second + first)}) as root:
    record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
check("nominated block: nominated, the moved half once",
      (len(record["tableNominations"]), pp.CJK(markdown)), (1, BODY + first + second))
check("nominated block: D2 sealed at both sites",
      [(c["moveSite"], c["after"]) for c in record["decisionChanges"] if c["decision"] == "D2"],
      [("read", ""), ("placed", second)])

# The structure pass may move text (the colophon above, written in its reading order):
# the deletion guard reports a run only when its characters are gone from the answer,
# not when the sequence diff shows it moved.  Measured: an answer lacking no character
# at all was refused for "deleting" 21 characters, in four runs out of four.
ONE, TWO, THREE = "天地玄黃宇宙洪荒日月盈昃辰宿列張", "寒來暑往秋收冬藏閏餘成歲律呂調陽", "雲騰致雨露結為霜金生麗水玉出崑岡"
check("a run written elsewhere is not deleted", pp.deleted_runs(ONE + TWO + THREE, ONE + THREE + TWO), [])
check("a run left out is", pp.deleted_runs(ONE + TWO + THREE, ONE + THREE), [TWO])
check("a run left out while another moved is still reported",
      [TWO in run for run in pp.deleted_runs(ONE + TWO + THREE, THREE + ONE)], [True])
check("a short slip beside a move is no deletion",
      pp.deleted_runs(ONE + TWO + THREE, ONE + THREE + TWO[:-3]), [])
# The guard compares the prose; the answer holds the tables too.  A table holding the
# same characters (here in another order) must not stand in for prose that is gone:
# counted against the whole merged text.
TABLE = "| " + " | ".join(TWO[::-1]) + " |"
check("prose dropped, its characters in a table: reported against the whole merged text",
      [run in TWO and len(run) >= len(TWO) - 1
       for run in pp.deleted_runs(ONE + TWO, ONE + "\n" + TABLE, whole=ONE + TWO + TWO[::-1])], [True])
check("the prose alone would take the table's characters for the prose's",
      pp.deleted_runs(ONE + TWO, ONE + "\n" + TABLE), [])
# The prose compared with the prose: a table whose cells hold the dropped run's characters
# among its own cut the run into pieces too short to report (x2 p.61: a 20-character
# header column beside a rebuilt table, left out, matched character by character in the
# table's cells).  With the tables left out of the diff (prose_only) the run is whole.
CELLS = "".join(ch + "某" for ch in TWO)
SPREAD = "| " + " | ".join(ch + "某" for ch in TWO) + " |\n| " + " | ".join("---" for _ in TWO) + " |"
check("a run dropped beside a table holding its characters: cut to pieces against the table",
      pp.deleted_runs(ONE + TWO, ONE + "\n\n" + SPREAD, whole=ONE + TWO + CELLS), [])
check("... and whole against the prose (prose_only)",
      pp.deleted_runs(ONE + TWO, ONE + "\n\n" + SPREAD, whole=ONE + TWO + CELLS, prose_only=True), [TWO])
check("prose written into a table is moved, not dropped, with the tables left out of the diff",
      pp.deleted_runs(ONE + TWO, ONE + "\n\n| " + " | ".join(TWO) + " |\n| " + " | ".join("---" for _ in TWO) + " |",
                      whole=ONE + TWO, prose_only=True), [])


def structure_page(answers):
    """A page of three blocks both engines read alike, and a structure pass that
    answers ANSWERS[kind] (the text it was given, as lines) for each kind of call."""
    blocks = [stand_in.block(text, [0.7 - 0.3 * k, 0.1, 0.2, 0.8]) for k, text in enumerate((ONE, TWO, THREE))]
    model = stand_in.StandIn(census=0, formatter=lambda text, kind: answers[kind](text.split("\n")))
    with stand_in.book({1: (blocks, ONE + TWO + THREE)}) as root:
        return stand_in.run(root, model)[1]


record, markdown = structure_page({"format": lambda lines: "\n\n".join([lines[0], lines[2], lines[1]])})
check("page: a structure answer that moves a block is kept",
      (pp.CJK(markdown), record["formatterRetryReason"], [d["kind"] for d in record["doubts"]]),
      (ONE + THREE + TWO, None, []))
record, markdown = structure_page({"format": lambda lines: "\n\n".join([lines[0], lines[2]]),
                                   "format-retry": lambda lines: "\n\n".join([lines[2], lines[0]])})
check("page: an answer that drops a block is refused, and so is a retry that drops it too",
      (pp.CJK(markdown), (record["formatterRetryReason"] or "")[:9], (record["formatterRetryRefusal"] or "")[:19]),
      (ONE + TWO + THREE, "deleted 1", "retry still deleted"))
check("page: the retry's answer and reason are sealed with the first's",
      (pp.CJK(record["formatterRejectedRetry"]), pp.CJK(record["formatterRejectedOutput"]),
       ["retry refused: retry still deleted" in d["detail"] for d in record["doubts"]
        if d["kind"] == "structure-rejected"]), (THREE + ONE, ONE + THREE, [True]))

sys.exit(1 if failures else 0)
