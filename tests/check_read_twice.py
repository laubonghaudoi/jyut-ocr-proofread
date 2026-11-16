#!/usr/bin/env python3
"""Offline checks of a stretch engine A read twice, written once (read_twice, #34).

No model, no GPU, no corpus: made-up pages run through proofread_pages.main()
with a stand-in model (tests/stand_in.py), in the shapes measured on the
sealed books:

- two paragraphs engine A decoded twice inside one layout block (P1 P1 P2 P2
  where engine B read P1 P2), on a block the repeat puts up as a possible
  table (the block union settled it as read, unasked, and wrote both copies);
- a page's last line read again in a second layout box lying on the first
  block's last line (裁決, asked about it, answered a piece of it, written
  beside the copy);
- engine A's side of a substitution that is such a copy: engine B's side goes
  the usual way, on the side of the stretch away from its copy;
- what is not taken for a copy read twice: a title printed in the running
  head and as the heading (boxes that do not meet), a copy with a character
  read differently, a run of one unit repeated (a ditto mark's cells), a
  table block's row printed twice, and a stretch shorter than
  BESIDE_COPY_MIN.

Usage:
    python3 tests/check_read_twice.py
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


LEAD = "秋收冬藏閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
BODY = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往"
P1 = "果珍李柰菜重芥薑海鹹河淡"
P2 = "鱗潛羽翔龍師火帝鳥官人皇始制文字"
PAGE, LINE = [0.1, 0.1, 0.8, 0.6], [0.12, 0.65, 0.7, 0.04]   # a block, and a box on its last line
HEAD = [0.1, 0.02, 0.8, 0.03]                                  # a running head's band, apart from PAGE


def run(blocks, b_text, judge=None):
    """A one-page made-up book with no engine preference: (record, CJK of the page, the model)."""
    model = stand_in.StandIn(census=0, judge=judge)
    with stand_in.book({1: (blocks, b_text)}, profile={"preferEngine": "adjudicate"}) as root:
        record, markdown = stand_in.run(root, model)[1]
    return record, pp.CJK(markdown), model


def settled(record):
    return [(d["engineA"], d["engineB"], d["resolved"]) for d in record["engineDisagreements"]
            if d.get("resolved_by") == "engine-A-read-twice"]


# Two paragraphs decoded twice inside one block.  The repeat puts the block up as a
# possible table (nominate_tables), which the rebuild refuses; the copy is not the
# union's to write.
record, text, model = run([stand_in.block(LEAD + P1 + P1 + P2 + P2 + BODY, PAGE)], LEAD + P1 + P2 + BODY)
check("two paragraphs read twice in one block: each written once, nothing asked",
      (text, settled(record), "adjudicate" in model.kinds(), record["adjudications"]),
      (LEAD + P1 + P2 + BODY, [(P1 + P2, "", "")], False, []))
check("... though the block is put up as a possible table, the union settles none of it",
      [(n["block"], n["union"]) for n in record["tableNominations"]], [(0, {"settled": 0, "leftToUsualWay": 0})])
check("... and no reading is reported dropped", [d["kind"] for d in record["doubts"]], ["table-rebuild-refused"])

# The last line read again in a second box lying on the block's last line.  Asked,
# 裁決 would see the line once and answer a piece of it: it is not asked.
record, text, model = run([stand_in.block(LEAD + P2, PAGE), stand_in.block(P2, LINE)], LEAD + P2,
                          judge=lambda a, b: a[:2])
check("a line read again in an overlapping box: written once, nothing asked",
      (text, settled(record), "adjudicate" in model.kinds(), [d["kind"] for d in record["doubts"]]),
      (LEAD + P2, [(P2, "", "")], False, []))

# Engine A's side of a substitution is the copy; engine B's side is one character only
# it read, which goes the usual way (here 裁決 confirms it).
record, text, model = run([stand_in.block(LEAD + P2, PAGE), stand_in.block(P2, LINE)], LEAD + P2 + "丙",
                          judge=lambda a, b: a or b)
check("a substitution whose engine-A side is the copy: engine B's side goes on alone",
      (text, settled(record), [(a["draft_reading"], a["writer_reading"], a["resolved"]) for a in record["adjudications"]]),
      (LEAD + P2 + "丙", [(P2, "", "")], [("", "丙", "丙")]))
# The stretch a second box on the NEXT block's first line (the copy after it), and engine B's
# side the last character of the block before (a column's 也 before a heading): engine B's
# side stands before the stretch, in the block before - not glued to the heading.
TOP = [0.1, 0.36, 0.8, 0.03]                                   # a box on the next block's first line
NEXT = [0.1, 0.35, 0.8, 0.4]
record, text, model = run([stand_in.block(LEAD, [0.1, 0.1, 0.8, 0.2]), stand_in.block(P2, TOP),
                           stand_in.block(P2 + BODY, NEXT)], LEAD + "也" + P2 + BODY, judge=lambda a, b: a or b)
check("a substitution whose copy follows: engine B's side ends the block before",
      (text, settled(record), [(a["writer_reading"], (a.get("boundaryCrop") or {}).get("blocks"))
                               for a in record["adjudications"]]),
      (LEAD + "也" + P2 + BODY, [(P2, "", "")], [("也", [0, 1])]))

# Not a line read twice.  A title in the running head and again as the heading: two
# boxes that do not meet.  Engine B read the title once; 裁決 confirms the other copy.
record, text, _ = run([stand_in.block(P2, HEAD), stand_in.block(P2 + LEAD, PAGE)], P2 + LEAD,
                      judge=lambda a, b: a or b)
check("a title in the running head and as the heading (boxes apart): both copies go the usual way",
      (text, settled(record), [a["resolved_from"] for a in record["adjudications"]]),
      (P2 + P2 + LEAD, [], ["adjudicator"]))
# A run of one unit repeated, and a table block's row printed twice: print repeats itself
# beside itself, so engine B reading fewer copies is no evidence of a second read.
DITTO = "同上" * 6
record, text, _ = run([stand_in.block(LEAD + DITTO + BODY, PAGE)], LEAD + "同上" * 3 + BODY,
                      judge=lambda a, b: a or b)
check("a run of one unit repeated (ditto cells): not settled as read twice", settled(record), [])
ROW = "白米一百二十元"
record, text, _ = run([stand_in.block(LEAD, [0.1, 0.05, 0.8, 0.2]),
                       stand_in.block(f"<table><tr><td>{ROW}</td></tr><tr><td>{ROW}</td></tr></table>",
                                      [0.1, 0.3, 0.8, 0.4], label="table")], LEAD + ROW,
                      judge=lambda a, b: a or b)
check("a table block's row printed twice: not settled as read twice", settled(record), [])
# The second copy with a character read differently: not an exact copy, the usual way.
NEAR = P2[:5] + "丁" + P2[6:]
record, text, _ = run([stand_in.block(LEAD + P2, PAGE), stand_in.block(NEAR, LINE)], LEAD + P2,
                      judge=lambda a, b: a or b)
check("a near copy (one character read differently): not settled as read twice",
      (settled(record), [a["draft_reading"] for a in record["adjudications"]]), ([], [NEAR]))
# Shorter than BESIDE_COPY_MIN (the beside-copy floor, reused): not settled.  Measured, the
# shorter engine-A copies are printed reduplications of 2 (消遣消遣) and two double reads of 5
# that go the usual way.
SHORT = P1[:pp.BESIDE_COPY_MIN - 1]
record, text, _ = run([stand_in.block(LEAD + SHORT + SHORT + BODY, PAGE)], LEAD + SHORT + BODY,
                      judge=lambda a, b: a or b)
check(f"a stretch of {pp.BESIDE_COPY_MIN - 1} characters read twice: not settled as read twice",
      (settled(record), text), ([], LEAD + SHORT + SHORT + BODY))

print(f"{'FAIL' if failures else 'ok'}: {failures} failure(s)")
sys.exit(1 if failures else 0)
