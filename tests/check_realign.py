#!/usr/bin/env python3
"""Checks for a punctuation answer that reads a stretch of its block other than its tile's
(realign_answer): a large vertical block is punctuated in column groups whose text is
scaled from the columns' lengths, so a tile's text drifts off its crop by a few
characters at either end; the punctuator writes the text its crop shows, and the answer
was refused for "characters changed" and the stretch left bare.

No model, no GPU, no corpus.  The shape is the one measured on a page printed with
bracketed notes (a bracket takes a whole cell): tile 0's answer left out the four
characters printed at the head of tile 1's crop, tile 1's answer began with them, and
both were refused in every run; one answer also wrote 吿 where the merge holds 告.

Usage: python3 tests/check_realign.py   (exits non-zero on the first failure)
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


BLOCK = "".join(chr(0x4E00 + 7 * k) for k in range(40))       # forty distinct characters


def marked(text: str, after: set[int], start: int = 0) -> str:
    """TEXT with 。 after each character whose place in the block (START + index) is in AFTER."""
    return "".join(c + ("。" if start + k in after else "") for k, c in enumerate(text))


# The tile [0, 20) whose answer leaves out the two characters printed on the next crop.
check("an answer short of its tile's end reads the stretch before it",
      pp.realign_answer(BLOCK, 0, 20, marked(BLOCK[:18], {5}), 10), (0, 18, 0))
check("the next tile's answer that begins with them reads from there",
      pp.realign_answer(BLOCK, 20, 40, marked(BLOCK[18:], {25}, 18), 10), (18, 40, 0))
check("an answer that adds a character inside is no reading of any stretch",
      pp.realign_answer(BLOCK, 0, 20, BLOCK[:9] + "某" + BLOCK[9:18], 10), None)
check("an answer that leaves one out inside is none either",
      pp.realign_answer(BLOCK, 0, 20, BLOCK[:9] + BLOCK[10:20], 10), None)
check("nor one past the reach", pp.realign_answer(BLOCK, 0, 20, BLOCK[:12], 4), None)
VARIANT = BLOCK[:10] + "告" + BLOCK[11:20]
check("reach 0: a variant form of the merge's character is the same reading",
      pp.realign_answer(VARIANT, 0, 20, VARIANT[:10] + "吿" + VARIANT[11:], 0), (0, 20, 1))
check("the marks are seated on the merge's characters, never the answer's",
      pp.seat_marks(marked(VARIANT[:10] + "吿" + VARIANT[11:], {10}), VARIANT), marked(VARIANT, {10}))

# The block's text from its passes: marks by position; a character two passes read is
# the one's that read it outside its own stretch; one no pass read keeps no mark.
parts = [(marked(BLOCK[:18], {5, 17}), 0, 18, 0, 20), (marked(BLOCK[18:], {18, 30}, 18), 18, 40, 20, 40)]
check("passes realigned: the block's characters, every mark at its place",
      pp.assemble_passes(BLOCK, parts), (marked(BLOCK, {5, 17, 18, 30}), []))
check("passes that read their own stretches: joined, as before",
      pp.assemble_passes(BLOCK, [(marked(BLOCK[:20], {5}), 0, 20, 0, 20), (BLOCK[20:], 20, 40, 20, 40)]),
      (marked(BLOCK, {5}), []))
gap = [(marked(BLOCK[:18], {5}), 0, 18, 0, 20), (BLOCK[20:], 20, 40, 20, 40)]
check("characters no pass read are reported", pp.assemble_passes(BLOCK, gap), (marked(BLOCK, {5}), [(18, 20)]))
both = [(BLOCK[:20], 0, 20, 0, 20), (marked(BLOCK[18:], {18}, 18), 18, 40, 20, 40)]
check("read by both: the pass that read it outside its own stretch", pp.assemble_passes(BLOCK, both)[0],
      marked(BLOCK, {18}))

# Two answers that each read a stretch: the marks they put at one place.
check("two answers agree where they put the same marks",
      pp.agreed_readings(BLOCK, (marked(BLOCK[:18], {3, 9}), [0, 18]), (marked(BLOCK[:20], {9, 12}), [0, 20])),
      (marked(BLOCK[:18], {9}), 1, [0, 18]))
check("a doubled circle against a single one is not agreed",
      pp.agreed_readings(BLOCK, (BLOCK[:4] + "。。" + BLOCK[4:10], [0, 10]), (BLOCK[:4] + "。" + BLOCK[4:10], [0, 10])),
      (BLOCK[:10], 0, [0, 10]))


class Arguments:
    max_tokens = 100
    profile_data = None


class Answers:
    """Stands in for the model: a census, then the answers in turn."""

    def __init__(self, census: int, answers: list[str]) -> None:
        self.census, self.answers, self.kinds = census, list(answers), []

    def ask_answering(self, system, prompt, crop, max_tokens, kind=None):
        self.kinds.append(kind)
        if kind == "census":
            return f"符號：{self.census}\n雙圈：0\n形狀：。", {}
        return self.answers.pop(0), {}


def part(census: int, answers: list[str], lo: int = 0, hi: int = 20, reach: int = 10):
    client = Answers(census, answers)
    out, record, _ = pp.punctuate_part(client, Arguments(), None, BLOCK[lo:hi], "", 60.0,
                                       block=(BLOCK, lo, hi, reach))
    return out, record, client.kinds


out, record, kinds = part(2, [marked(BLOCK[:18], {5, 12})])
check("a drifted answer that meets the census is kept, on the stretch it read",
      (out, record["ok"], record["realigned"], kinds),
      (marked(BLOCK[:18], {5, 12}), True, {"read": [0, 18], "range": [0, 20], "variantsFolded": 0},
       ["census", "punctuate"]))
out, record, kinds = part(8, [marked(BLOCK[:18], {5, 12}), marked(BLOCK[:18], {1, 3, 5, 7, 9, 11, 12, 15})])
check("a realigned retry is held to the first answer: only what both put at one place",
      (out, record["ok"], record["partial"], record["realigned"]["read"], kinds),
      (marked(BLOCK[:18], {5, 12}), False, {"kept": 2, "agreedWith": "both answers"}, [0, 18],
       ["census", "punctuate", "punctuate-retry"]))
check("... sealed as a partial pass of the two answers",
      [(d["kind"], "the two answers" in d["detail"]) for d in pp.punctuation_doubts([dict(record, block=0)])],
      [("punctuation-partial", True)])
out, record, kinds = part(8, [marked(BLOCK[:18], {5, 12}), marked(BLOCK[:20], {1, 3, 5, 7, 9, 11, 12, 15})])
check("a retry on the tile's own characters is kept as before", (out, record["ok"], "realigned" in record),
      (marked(BLOCK[:20], {1, 3, 5, 7, 9, 11, 12, 15}), True, False))
check("... and the first answer is sealed beside it", record["firstAnswer"], marked(BLOCK[:18], {5, 12}))
out, record, kinds = part(2, [BLOCK[:9] + "某" + BLOCK[9:18] + "。"] * 2)
check("an answer that changes a character inside is refused as before", (out, record["ok"]), (BLOCK[:20], False))

# A page: one vertical block of ten columns of twenty characters, punctuated in two
# column groups ([0, 160) and [160, 200)).  The first group's answer leaves out the
# three characters printed at the head of the second group's crop, and the second's
# begins with them.
TEXT = "".join(chr(0x4E00 + 13 * k) for k in range(200))
AFTER = {9, 29, 49, 69, 89, 109, 158, 169, 179, 189, 194, 199}


def columns_page() -> Image.Image:
    image = Image.new("L", (1000, 1400), 255)
    draw = ImageDraw.Draw(image)
    for k in range(200):
        x, top = 900 - 80 * (k // 20), 100 + (k % 20) * 50
        for bar in (0, 18, 36):
            draw.rectangle((x, top + bar, x + 40, top + bar + 3), fill=0)
        draw.rectangle((x + 18, top, x + 21, top + 39), fill=0)
    return image


def drifted(span: str) -> str:
    """What the crop of each column group shows, with its marks."""
    if span.startswith(TEXT[:10]):
        return marked(TEXT[:157], AFTER)
    return marked(TEXT[157:], AFTER, 157)


box = [0.17, 0.06, 0.78, 0.74]
with stand_in.book({1: ([stand_in.block(TEXT, box)], TEXT)}) as root:
    columns_page().save(root / "renders" / "page-0001.png")
    record, markdown = stand_in.run(root, stand_in.StandIn(census=6, punctuate=drifted))[1]
check("page: two column groups", [p.get("tile") for p in record["punctuationPasses"]], [0, 1])
check("page: each read the stretch its crop shows",
      [(p["ok"], (p.get("realigned") or {}).get("read"), p["range"]) for p in record["punctuationPasses"]],
      [(True, [0, 157], [0, 157]), (True, [157, 200], [157, 200])])
check("page: the block's characters with every mark in place", markdown.strip(), marked(TEXT, AFTER))
check("page: clean", record["doubts"], [])

sys.exit(1 if failures else 0)
