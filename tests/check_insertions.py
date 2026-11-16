#!/usr/bin/env python3
"""Checks for short one-engine insertions under a preference (decision D3): one or two
characters only one engine read go to 裁決 on the crop - the existing 有冇字 question -
unless the mark rule, the bracket rule or the book's furniture explains them; with the
裁決 budget spent they are kept as before, with a doubt.

No model, no GPU, no corpus.  The shape is the one measured on a magazine page: a ▲
engine B read as 厶, kept by a preference for engine A that was measured on
substitutions only and says nothing about text one engine read and the other did not.

Usage: python3 tests/check_insertions.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


BODY = "本刊徵文簡約本刊立言以反共救民會之宗旨爲宗旨"
HEAD = "剷共半月刊第四期"


def run(text_a: str, text_b: str, judge=None, profile=None, extra=()):
    blocks = [stand_in.block(text_a, [0.3, 0.1, 0.4, 0.8])]
    model = stand_in.StandIn(census=0, judge=judge)
    with stand_in.book({1: (blocks, text_b)}, profile=profile) as root:
        record, markdown = stand_in.run(root, model, extra=list(extra))[1]
    return record, markdown, model


# Engine B read a ▲ as 厶; engine A read nothing there.  After a line of the same
# block, so the crop can be shown to hold the spot (confirm_nothing).
LEAD = "啓者同人發起"
record, markdown, model = run(LEAD + BODY, LEAD + "厶" + BODY, judge=lambda a, b: "無")
check("asked of 裁決 on the crop", [(a["draft_reading"], a["writer_reading"], a["askedFor"])
                                    for a in record["adjudications"]], [("", "厶", "D3")])
check("its answer 'nothing printed' stands", pp.CJK(markdown), LEAD + BODY)
check("sealed for D3", [(c["decision"], c["rule"], c["engineB"], c["after"], c["mergedOffset"])
                        for c in record["decisionChanges"]],
      [("D3", "insertion-to-crop", "厶", "", len(LEAD))])
# At the page's first character nothing before the spot can show the crop holds it: the
# 無 is not confirmed, and the reading is kept with a doubt.
record, markdown, model = run(BODY, "厶" + BODY, judge=lambda a, b: "無")
check("at the page's head: 'nothing printed' unconfirmed, kept with a doubt",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]], record["adjudications"][0]["coverageRead"]["found"]),
      ("厶" + BODY, ["adjudicator-empty-unconfirmed"], ["nothing-unconfirmed"], "before-unshown"))
record, markdown, _ = run(BODY, "厶" + BODY, judge=lambda a, b: b)
check("an answer that the character is printed keeps it", pp.CJK(markdown), "厶" + BODY)
# Engine A read one character engine B did not: the same question.
record, markdown, _ = run(BODY[:10] + "某" + BODY[10:], BODY, judge=lambda a, b: "無")
check("engine A's one-engine character too", [(a["draft_reading"], a["writer_reading"])
                                              for a in record["adjudications"]], [("某", "")])
# An answer that writes part of what the engine read: text is printed there, and only
# the count is in dispute - the engine's reading is kept, and a person counts.  The
# shape measured: engine B read 者廣 under an ink blot engine A skipped, and 裁決
# answered 者 in three of four runs on the same crop.
TWO = BODY[:8] + "甲乙" + BODY[8:]
record, markdown, model = run(BODY, TWO, judge=lambda a, b: b[:1])
check("a shorter answer: engine B's reading is kept", pp.CJK(markdown), TWO)
check("a shorter answer: refused as partial, with what 裁決 wrote",
      [(a["resolved_from"], a["resolved"], a["answered"], a["winner"]) for a in record["adjudications"]],
      [("adjudicator-partial", "甲乙", "甲", "undecided")])
check("a shorter answer: D3 sealed, engine B's reading written",
      [(c["decision"], c["after"]) for c in record["decisionChanges"]], [("D3", "甲乙")])
check("a shorter answer: an insertion-partial doubt naming 裁決's reading",
      [(d["kind"], "「甲」" in d["detail"]) for d in record["doubts"]], [("insertion-partial", True)])
record, markdown, model = run(TWO, BODY, judge=lambda a, b: a[1:])
check("mirrored: engine A's two characters, 裁決 wrote one: kept",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]]), (TWO, ["adjudicator-partial"], ["insertion-partial"]))
record, markdown, model = run(BODY, TWO, judge=lambda a, b: "無")
check("the whole run answered 'nothing printed': dropped, as before",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]], record["doubts"]),
      (BODY, ["adjudicator-empty"], []))
record, markdown, model = run(BODY, TWO, judge=lambda a, b: "丙丁")
check("an answer as long as the reading stands", (pp.CJK(markdown), record["doubts"]),
      (BODY[:8] + "丙丁" + BODY[8:], []))
record, markdown, model = run(BODY[:10] + "某" + BODY[10:], BODY, judge=lambda a, b: "日")
check("same length, one character: 裁決's reading is written",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]]),
      (BODY[:10] + "日" + BODY[10:], ["adjudicator"]))
# Not a D3 question (no preference): a shorter answer is 裁決's, as before.
record, markdown, model = run(BODY, TWO, profile={"preferEngine": "adjudicate"}, judge=lambda a, b: b[:1])
check("no preference: a shorter answer stands, as before", (pp.CJK(markdown), record["doubts"]),
      (BODY[:8] + "甲" + BODY[8:], []))
# Longer runs stay with the preference.
record, markdown, model = run(BODY, "甲乙丙" + BODY)
check("three characters: the preference, as before", (model.kinds().count("adjudicate"), pp.CJK(markdown)),
      (0, "甲乙丙" + BODY))
# A piece of the book's running head only engine B read: furniture, not asked.
profile = {"preferEngine": "A", "layout": {"runningHeads": [HEAD]}}
record, markdown, model = run(BODY + HEAD[:4] + HEAD[5:], BODY + HEAD, profile=profile)
check("inside a measured running head: not asked", model.kinds().count("adjudicate"), 0)
record, markdown, model = run(BODY + HEAD[:4] + HEAD[5:], BODY + HEAD, profile={"preferEngine": "A"})
check("the same character where the book measured no running head: asked", model.kinds().count("adjudicate"), 1)
# The budget spent: kept as before, and doubted.
record, markdown, model = run(BODY, "厶" + BODY, extra=["--max-adjudications", "0"])
check("budget spent: kept as before", (model.kinds().count("adjudicate"), pp.CJK(markdown)), (0, "厶" + BODY))
check("budget spent: a doubt", [d["kind"] for d in record["doubts"]], ["insertion-unjudged"])
check("budget spent: no text change sealed", record["decisionChanges"], [])
# A book with no preference already asks 裁決 about everything: nothing changes.
record, markdown, model = run(LEAD + BODY, LEAD + "厶" + BODY, profile={"preferEngine": "adjudicate"},
                              judge=lambda a, b: "無")
check("no preference: asked as before, not as D3", ([a.get("askedFor") for a in record["adjudications"]],
                                                     record["decisionChanges"]), ([None], []))


# Text only engine B read where engine A's reading passes from one layout block to
# the next: the crop shows the end of the first block (past its box) beside the
# start of the next, since the alignment cannot tell which the text belongs to.  The
# shape measured: engine A read 初七 at the end of one block and 又不開門 at the
# start of the next, and the 日 printed under 初七 was not on the next block's crop.
FIRST, SECOND = "甲乙丙丁戊己庚辛壬癸子丑", "寅卯辰巳午未申酉戌亥天地"
FOOT_INK = (255, 0, 0)     # the character engine A left out, drawn in red: scaling black on
                           # white makes greys, never red


def two_columns() -> "Image.Image":
    """A page with two one-column vertical blocks: FIRST at x 600-640, its 13th
    character (engine A's box stops above it) in FOOT_INK; SECOND at x 400-440."""
    image = Image.new("RGB", (1000, 1400), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x, count, extra in ((600, 12, True), (400, 12, False)):
        for i in range(count + extra):
            top, fill = 140 + i * 50, (FOOT_INK if i == count else (0, 0, 0))
            for bar in (0, 18, 36):
                draw.rectangle((x, top + bar, x + 40, top + bar + 3), fill=fill)
            draw.rectangle((x + 18, top, x + 21, top + 39), fill=fill)
    return image


def boundary_run(text_b: str, extra=(), last_first: bool = False):
    """A page run with engine B's TEXT_B; LAST_FIRST puts FIRST's block last in
    engine A's reading, so the page ends at its foot."""
    blocks = [stand_in.block(FIRST, [0.59, 130 / 1400, 0.06, 610 / 1400]),
              stand_in.block(SECOND, [0.39, 130 / 1400, 0.06, 620 / 1400])]
    blocks = blocks[::-1] if last_first else blocks
    model = stand_in.StandIn(census=0, judge=lambda a, b: "無")
    with stand_in.book({1: (blocks, text_b)}) as root:
        two_columns().save(root / "renders" / "page-0001.png")
        record, markdown = stand_in.run(root, model, extra=list(extra))[1]
        crop = root / "out" / "page-0001-adjudication-01.png"
        foot = 0
        if crop.is_file():
            # Pixels of the red character: red well above green.
            red, green, _ = (band.tobytes() for band in Image.open(crop).convert("RGB").split())
            foot = sum(1 for r, g in zip(red, green) if r - g > 100)
    return record, markdown, model, foot


record, markdown, model, foot = boundary_run(FIRST + "某" + SECOND)
check("between two blocks: asked on both blocks' edges",
      [(a["writer_reading"], a["askedFor"], a.get("boundaryCrop")) for a in record["adjudications"]],
      [("某", "D3", {"blocks": [0, 1], "set": "columns"})])
check("the crop holds the character printed past the first block's box", foot > 0, True)
check("裁決 is told how the crop is set",
      [pp.BOUNDARY_NOTES["columns"] in text for kind, text in model.calls if kind == "adjudicate"], [True])
check("sealed with the D3 change", record["decisionChanges"][0]["evidence"].get("boundaryCrop"),
      {"blocks": [0, 1], "set": "columns"})
record, markdown, model, foot = boundary_run(SECOND + FIRST + "某", last_first=True)
check("at the page's end: the last block's end", ([a["boundaryCrop"] for a in record["adjudications"]],
                                                  foot > 0),
      ([{"blocks": [1, None], "set": "end"}], True))
record, markdown, model, foot = boundary_run(FIRST[:6] + "某" + FIRST[6:] + SECOND)
check("inside a block: the ordinary crop", [a.get("boundaryCrop") for a in record["adjudications"]], [None])

# Decision D15: the book's furniture one engine read and the other did not - here a
# reprint's margin title engine A read as a block of its own on every page, and engine
# B never read - is left out before anything is asked.  The shape measured: with no
# engine preference 裁決 was asked, rightly said the title is printed, and the page
# kept it as a heading.
TITLE = "某某大典第九輯史部政書類"
VERSE = ("天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽雲騰致雨露結為霜金生麗水玉出崑岡"
         "劍號巨闕珠稱夜光果珍李柰菜重芥薑海鹹河淡鱗潛羽翔龍師火帝鳥官人皇始制文字乃服衣裳推位讓國有虞陶唐"
         "弔民伐罪周發殷湯坐朝問道垂拱平章愛育黎首臣伏戎羌遐邇一體率賓歸王鳴鳳在竹白駒食場化被草木賴及萬方")
# Three pages of prose long enough that the title is a small share of the page (the
# engines agree on the reading order).
BODIES = [VERSE[0:48], VERSE[48:96], VERSE[96:144]]


def reprint(extra=(), title_in_body: bool = False, pages: int = 3):
    book_pages = {}
    for page in range(1, pages + 1):
        body = BODIES[page - 1]
        if title_in_body:
            # The title inside a body block, neither a block of its own nor at the edge.
            blocks = [stand_in.block(body[:8] + TITLE + body[8:], [0.3, 0.1, 0.4, 0.8])]
        else:
            blocks = [stand_in.block(TITLE, [0.93, 0.05, 0.03, 0.5], label="doc_title"),
                      stand_in.block(body, [0.3, 0.1, 0.4, 0.8])]
        book_pages[page] = (blocks, body)
    model = stand_in.StandIn(census=0, judge=lambda a, b: a or "無")
    with stand_in.book(book_pages, profile={"preferEngine": "adjudicate"}) as root:
        return stand_in.run(root, model, pages=f"1-{pages}", extra=list(extra)), model


pages_out, model = reprint()
record, markdown = pages_out[2]
check("D15: the margin title only engine A read is left out, not asked",
      (pp.CJK(markdown), model.kinds().count("adjudicate")), (BODIES[1], 0))
check("D15: sealed, with its evidence",
      [(c["decision"], c["rule"], c["engineA"], c["after"], c["evidence"]) for c in record["decisionChanges"]],
      [("D15", "furniture-insertion", TITLE, "",
        {"readBy": "A", "characters": len(TITLE), "wholeBlock": True, "pageEdge": True})])
check("D15: on the page's disagreements", [d["resolved_by"] for d in record["engineDisagreements"]],
      ["furniture-one-engine-A"])
pages_out, model = reprint(title_in_body=True)
check("the same text inside a body block is not furniture: asked",
      (pp.CJK(pages_out[2][1]), model.kinds().count("adjudicate")), (BODIES[1][:8] + TITLE + BODIES[1][8:], 3))
pages_out, model = reprint(pages=2)
check("a title on two pages is no recurrence: asked", model.kinds().count("adjudicate"), 2)

sys.exit(1 if failures else 0)
