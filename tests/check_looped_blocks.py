#!/usr/bin/env python3
"""Offline checks of one looped engine-A block set aside instead of engine A's page.

No model, no GPU, no corpus: made-up pages run through proofread_pages.main()
with a stand-in model (tests/stand_in.py).  The shapes are the two measured on
the corpus: a block whose reading starts on the region's text and then repeats
itself for thousands of characters while engine B reads the region cleanly, and
a block that repeats a list over an empty area engine B reads nothing in.

Usage:
    python3 tests/check_looped_blocks.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

CLEAN = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽"
REGION = "雲騰致雨露結為霜金生麗水玉出崑岡劍號巨闕珠稱夜光果珍李柰菜重芥薑"
# Engine A's reading of the region: a start on its text, then a loop.
LOOP = REGION[:14] + "海鹹河淡鱗潛羽翔" * 40
# A loop over an empty area: nothing of any region in it.
LIST = "".join(f"卷{n}" for n in "一二三四五六七八九十") * 30
UPPER, LOWER = [0.3, 0.05, 0.6, 0.4], [0.3, 0.5, 0.6, 0.45]


def kinds(record: dict) -> list[str]:
    return [d["kind"] for d in record["doubts"]]


def check_looped_blocks() -> None:
    blocks = [{"text": CLEAN, "label": "text"}, {"text": LOOP, "label": "table"}]
    draft = CLEAN + "\n" + LOOP
    assert pp.looped_blocks(blocks, draft, CLEAN + REGION) == [1]
    # Engine B reads the repeated stretch as often: printed repetition, not a loop.
    assert pp.looped_blocks(blocks, draft, CLEAN + LOOP) == []
    # Engine B looped itself, or the blocks do not account for engine A's text.
    assert pp.looped_blocks(blocks, draft, "八" * 400) == []
    assert pp.looped_blocks(blocks, draft + "某", CLEAN + REGION) == []
    # Short blocks are never judged.
    assert pp.looped_blocks([{"text": "之乎者也" * 10, "label": "text"}], "之乎者也" * 10, CLEAN) == []
    # A printed repetition with a period of two or three characters (a column of
    # 〇〇 cells, a form's repeated field) that engine B reads the same is not a
    # loop: its most repeated 4-gram occurs overlapping in both readings, and is
    # counted that way in both.
    body = "".join(chr(0x4E00 + 3 * i) for i in range(300))   # engine B's page is no loop
    for period in ("之乎", "甲乙丙"):
        text = period * (240 // len(period))
        blocks = [{"text": body, "label": "text"}, {"text": text, "label": "table"}]
        assert pp.looped_blocks(blocks, body + "\n" + text, body + text) == [], period
        # Engine B reading half as many repeats is no witness to them.
        assert pp.looped_blocks(blocks, body + "\n" + text, body + text[:120]) == [1], period
    print("looped_blocks: long, repeating and not repeated by engine B")


def check_region_read_by_engine_b() -> None:
    blocks = [stand_in.block(CLEAN, UPPER), stand_in.block(LOOP, LOWER, label="table")]
    with stand_in.book({1: (blocks, CLEAN + "\n" + REGION)}) as root:
        model = stand_in.StandIn(census=0)
        record, markdown = stand_in.run(root, model)[1]
    assert record["mode"] == "merge", record["mode"]
    assert pp.CJK(markdown) == CLEAN + REGION, markdown
    [dropped] = record["engineALoopsDropped"]
    assert (dropped["block"], dropped["label"], dropped["characters"], dropped["engineBCharacters"]) == (
        1, "table", len(LOOP), len(REGION)), dropped
    assert dropped["text"] == LOOP[:2000]
    assert "engine-a-loop-dropped" in kinds(record) and "punctuation-whole-page" not in kinds(record)
    reading = [d for d in record["engineDisagreements"] if d["resolved_by"] == "engine-B-only-reading-A-looped"]
    assert [d["resolved"] for d in reading] == [REGION], record["engineDisagreements"]
    # The set-aside block is punctuated as text with engine B's reading, on its own crop.
    assert sorted(p["block"] for p in record["punctuationPasses"]) == [0, 1], record["punctuationPasses"]
    assert record["engineCoverage"]["engineA"] == len(CLEAN + LOOP), record["engineCoverage"]
    assert model.kinds().count("adjudicate") == 0, model.kinds()
    print("one looped block: set aside, engine B's reading of its region kept and punctuated")


def check_block_follows_engine_b() -> None:
    # Engine A lists the looped block after the clean one; engine B reads the
    # region first.  The block moves to where engine B read it.
    blocks = [stand_in.block(CLEAN, UPPER), stand_in.block(LOOP, LOWER, label="table")]
    with stand_in.book({1: (blocks, REGION + "\n" + CLEAN)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge", record["mode"]
    assert pp.CJK(markdown) == REGION + CLEAN, markdown
    print("one looped block: placed where engine B read its region")


def check_layout_index_kept() -> None:
    # The looped block comes first in engine A's layout and engine B reads its
    # region last, so it moves behind the other two.  Every sealed block number
    # is still the layout's: engineALoopsDropped, the punctuation passes and the
    # refused table all name the block engine A's layout gave it.
    table = "<table><tr><td>甲乙\n丙丁</td><td>戊己</td></tr><tr><td>庚辛</td><td>壬癸</td></tr></table>"
    clean = CLEAN + "寸陰是競資父事君"
    blocks = [stand_in.block(LOOP, LOWER), stand_in.block(clean, UPPER),
              stand_in.block(table, [0.05, 0.05, 0.2, 0.3], label="table")]
    with stand_in.book({1: (blocks, clean + "甲乙丙丁戊己庚辛壬癸" + REGION)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge", record["mode"]
    assert pp.CJK(markdown).endswith(REGION), markdown
    assert [d["block"] for d in record["engineALoopsDropped"]] == [0], record["engineALoopsDropped"]
    passes = sorted((p["block"], p["range"][1]) for p in record["punctuationPasses"])
    assert passes == [(0, len(REGION)), (1, len(clean))], passes
    assert [t["block"] for t in record["tablesRejected"]] == [2], record["tablesRejected"]
    print("a moved block keeps its layout index in every sealed field")


def check_layout_index_nominated_table() -> None:
    # The table work of the other stream seals block numbers too: a block
    # engine B reads in another order is nominated as a missed table and its
    # rebuild asked (the stand-in answers no table).  With the looped block
    # moved behind the others, the nomination, the rebuild and its doubt name
    # the block engine A's layout gave it - as its punctuation pass does - not
    # its position (on the merge commit: 1 against the pass's 2).
    first, second = "甲乙丙丁戊己庚辛壬癸", "青赤黃白黑紫綠藍灰褐"
    blocks = [stand_in.block(LOOP, LOWER, label="table"), stand_in.block(CLEAN, UPPER),
              stand_in.block(first + second, [0.05, 0.05, 0.2, 0.3], label="vertical_text")]
    with stand_in.book({1: (blocks, CLEAN + second + first + REGION)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge" and [d["block"] for d in record["engineALoopsDropped"]] == [0]
    assert [n["block"] for n in record["tableNominations"]] == [2], record["tableNominations"]
    assert [r["block"] for r in record["tablesRebuilt"]] == [2], record["tablesRebuilt"]
    assert (2, "vertical_text") in [(p["block"], p["label"]) for p in record["punctuationPasses"]]
    assert any(d["kind"] == "table-rebuild-refused" and "block 2 " in d["detail"] for d in record["doubts"])
    assert pp.CJK(markdown) == CLEAN + first + second + REGION, markdown
    print("a moved block keeps its layout index in the nominated-table fields too")


def check_empty_region() -> None:
    blocks = [stand_in.block(CLEAN, UPPER), stand_in.block(LIST, LOWER)]
    with stand_in.book({1: (blocks, CLEAN)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge", record["mode"]
    assert pp.CJK(markdown) == CLEAN, markdown
    [dropped] = record["engineALoopsDropped"]
    assert dropped["engineBCharacters"] == 0 and dropped["characters"] == len(LIST), dropped
    detail = next(d["detail"] for d in record["doubts"] if d["kind"] == "engine-a-loop-dropped")
    # Worded from what was found: nothing only engine B read is the region's,
    # which is not proof that engine B read nothing there.
    assert "no reading of the region was found" in detail, detail
    assert dropped["engineBReading"] == {"found": "none", "sharedWith": None}, dropped
    print("one looped block over an empty area: set aside, nothing put in its place")


def check_reading_shares_a_segment() -> None:
    # Engine B read a short column engine A skipped right before the region, and
    # engine A read a fold label there that engine B did not: the alignment joins
    # them into one replacement with engine B's reading of the region.  The
    # reading is split off whole, never left to a preference that keeps the label.
    body = "".join(chr(0x4E00 + 3 * i) for i in range(120))
    label, column = "法學通論", "某甲某乙某丙"
    blocks = [stand_in.block(body, UPPER), stand_in.block(label, [0.9, 0.05, 0.03, 0.2]),
              stand_in.block(LOOP, LOWER, label="table")]
    with stand_in.book({1: (blocks, body + "\n" + column + REGION)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge", record["mode"]
    assert REGION in pp.CJK(markdown) and label in pp.CJK(markdown), markdown
    [dropped] = record["engineALoopsDropped"]
    assert dropped["engineBCharacters"] == len(column + REGION), dropped
    assert dropped["engineBReading"] == {"found": "split", "sharedWith": label}, dropped
    detail = next(d["detail"] for d in record["doubts"] if d["kind"] == "engine-a-loop-dropped")
    assert f"engine A's 「{label}」" in detail, detail
    # Engine B read the region inside another block's text, where no block
    # boundary lets the region's block stand: the page is judged as before -
    # here, as on the measured page, the loop outweighs engine A's page and
    # engine B's text is taken whole - rather than leave that reading to 裁決 as
    # a one-engine insertion it may answer "no character" to (the stand-in does).
    long_loop = REGION[:14] + "海鹹河淡鱗潛羽翔" * 80
    blocks = [stand_in.block(body, UPPER), stand_in.block(long_loop, LOWER, label="table")]
    with stand_in.book({1: (blocks, body[:60] + REGION + body[60:])},
                       profile={"preferEngine": "adjudicate"}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["engineALoopsDropped"] == [], record["engineALoopsDropped"]
    assert record["mode"] == "merge-order-conflict-fell-back-degenerate", record["mode"]
    assert REGION in pp.CJK(markdown), markdown
    print("engine B's reading of the region: split off a shared segment, or the page judged as before")


def check_unchanged_otherwise() -> None:
    # Engine B's extra text shares nothing with the loop: it is not the loop's
    # region, the page would not merge, and it is judged as before.
    other = "".join(chr(0x4E00 + 7 * i) for i in range(60))
    blocks = [stand_in.block(CLEAN, UPPER), stand_in.block(LIST, LOWER)]
    with stand_in.book({1: (blocks, CLEAN + other)}) as root:
        record, _ = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge-order-conflict-fell-back-degenerate", record["mode"]
    assert record["engineALoopsDropped"] == [], record["engineALoopsDropped"]
    # Two looped blocks: the whole-page fallback, as before.
    blocks = [stand_in.block(CLEAN, UPPER), stand_in.block(LOOP, LOWER), stand_in.block(LIST, [0.0, 0.5, 0.25, 0.45])]
    with stand_in.book({1: (blocks, CLEAN + REGION)}) as root:
        record, _ = stand_in.run(root, stand_in.StandIn(census=0))[1]
    assert record["mode"] == "merge-order-conflict-fell-back-degenerate", record["mode"]
    assert record["engineALoopsDropped"] == [], record["engineALoopsDropped"]
    print("otherwise unchanged: engine B's text not the loop's region, or two looped blocks")


if __name__ == "__main__":
    check_looped_blocks()
    check_region_read_by_engine_b()
    check_block_follows_engine_b()
    check_layout_index_kept()
    check_layout_index_nominated_table()
    check_empty_region()
    check_reading_shares_a_segment()
    check_unchanged_otherwise()
    print("all looped-block checks passed")
