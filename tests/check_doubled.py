#!/usr/bin/env python3
"""Checks for doubled circles 。。 in the punctuation step: the census reports the places
a mark is printed and, apart, how many are doubled circles; the prompts and the gates
use both; the notation probe reads a doubled-circle-only answer; the whole-page
formatter is told the rule and its first refused answer's reason is sealed.

No model, no GPU, no corpus.  The shapes are the ones measured on two books printing
doubled circles: a census that counted each pair once held answers to half their
marks and refused the right ones; a block of doubled circles alone was read as
unpunctuated by the probe.

Usage: python3 tests/check_doubled.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_profile as bp  # noqa: E402
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


# The census answer, both lines.
check("places and doubled circles apart",
      (pp.parse_census("符號：12\n雙圈：10\n形狀：。"), pp.parse_census("符號：12\n雙圈：10", pp.DOUBLED_LINE)),
      (12, 10))
check("an answer with no doubled-circle line", pp.parse_census("符號：5\n形狀：。", pp.DOUBLED_LINE), None)
check("the census asks for places and doubled circles", "雙圈：<" in pp.CENSUS_SYSTEM, True)
check("places: a doubled circle is one", pp.mark_places("甲乙。。丙丁。戊，"), 3)

# What the punctuator is told.
check("hint with doubled circles", pp.census_hint(12, 10, True),
      "你先前數過呢個 block 有 12 處印咗符號，其中 10 處係雙圈（兩個圈連住，寫「。。」）；輸出應該接近呢個數，位置逐個由圖讀。\n")
check("hint with none", "冇雙圈" in pp.census_hint(5, 0, True), True)
check("hint without a doubled line: a pair is one place", "雙圈算一處" in pp.census_hint(5, None, True), True)
check("hint on a book whose survey found no doubled circles: the count is not told",
      "雙圈" in pp.census_hint(5, 3, False), False)


class Arguments:
    max_tokens = 100

    def __init__(self, profile=None):
        self.profile_data = profile


class Client:
    """A census answer and punctuation answers in order; records every prompt."""

    model, effort = "fake", None

    def __init__(self, census: str, answers: list[str]):
        self.census, self.answers, self.prompts, self.systems = census, list(answers), [], []

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        self.prompts.append((kind, user_text))
        self.systems.append(system)
        metrics = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        if system == pp.CENSUS_SYSTEM:
            return self.census, metrics
        return self.answers.pop(0), metrics


SPAN = "".join(chr(0x4E00 + 5 * i) for i in range(64))   # 22 marks on 64 characters, as printed books do
DOUBLED = "".join(c + ("。。" if i % 5 == 4 and i < 50 else "。" if i in (55, 60) else "")
                  for i, c in enumerate(SPAN))          # 10 doubled circles, 2 single: 12 places
SINGLES = DOUBLED.replace("。。", "。")                     # the same places, every circle single
census = "符號：12\n雙圈：10\n形狀：。"

client = Client(census, [DOUBLED])
out, record, _ = pp.punctuate_part(client, Arguments(), None, SPAN, "", 60.0)
check("a pair is never refused as an extra mark: first answer kept", (out, record["ok"], record["reason"]),
      (DOUBLED, True, ""))
check("the pass records both counts", (record["census"], record["doubledCensus"], record["censusConvention"],
                                       record["marks"], record["doubled"]), (12, 10, "places", 22, 10))
check("the punctuator was told both numbers", "其中 10 處係雙圈" in client.prompts[1][1], True)
check("no doubt for it", pp.punctuation_doubts([dict(record, block=0)]), [])

client = Client(census, [SINGLES, DOUBLED])
out, record, _ = pp.punctuate_part(client, Arguments(), None, SPAN, "", 60.0)
check("every pair written single: one shape retry, kept", (out, record["shapeRetry"]["kept"]), (DOUBLED, True))
check("the shape retry names both numbers", ("寫咗 0 處雙圈" in client.prompts[2][1],
                                              "數過嘅係 10 處" in client.prompts[2][1]), (True, True))
check("the first answer is recorded", (record["firstAnswerMarks"], record["firstAnswerDoubled"]), (12, 0))
client = Client(census, [SINGLES, SINGLES.replace("。", "", 3)])
out, record, _ = pp.punctuate_part(client, Arguments(), None, SPAN, "", 60.0)
check("a shape retry no nearer the count is not kept", (out, record["shapeRetry"]["kept"]), (SINGLES, False))
check("and the stretch is a doubt of its own", [d["kind"] for d in pp.punctuation_doubts([dict(record, block=0)])],
      ["doubled-count-mismatch"])

FIVE = "".join(c + ("。" if i in (3, 10, 20, 30, 40) else "") for i, c in enumerate(SPAN))
client = Client("符號：5\n形狀：。", [FIVE])
out, record, _ = pp.punctuate_part(client, Arguments(), None, SPAN, "", 60.0)
check("a census answer without the doubled line still works",
      (out, record["ok"], record["reason"], record["doubledCensus"]), (FIVE, True, "", None))

client = Client(census, [SPAN[:4], DOUBLED])
out, record, _ = pp.punctuate_part(client, Arguments(), None, SPAN, "", 60.0)
check("the retry after a refusal is told the census too", "其中 10 處係雙圈" in client.prompts[2][1], True)

# A book whose notation survey looked and found no doubled circles: the crop's
# count is sealed and doubted, not written.
no_doubled = {"notation": {"blocksSurveyed": 16, "doubled": False}}
client = Client(census, [SINGLES])
out, record, _ = pp.punctuate_part(client, Arguments(no_doubled), None, SPAN, "", 60.0)
check("survey found none: not told, no shape retry", ("雙圈" in client.prompts[1][1], "shapeRetry" in record,
                                                      len(client.prompts)), (False, False, 2))
check("survey found none: the disagreement is a doubt", [d["kind"] for d in pp.punctuation_doubts([dict(record, block=0)])],
      ["doubled-count-mismatch"])


# The notation probe: a block of doubled circles alone.
answer = "符號：無\n雙圈：有\n圈點：冇\n點數：22"
check("doubled-circle-only answer: a doubled-circle block", {k: v for k, v in bp.parse_notation(answer).items()},
      {"marks": ["。"], "none": False, "doubled": True, "emphasis": None, "count": 22})
check("with a count of 0 it still contradicts itself", bp.parse_notation(answer.replace("22", "0"))["none"], True)
check("--no-doubled-notation: read as unpunctuated", bp.parse_notation(answer, doubled_only=False)["none"], True)
check("the probe lists 。。 as a form", ("。 。。 ，" in bp.NOTATION_SYSTEM, "。。" in bp.NOTATION_SYSTEM_SINGLE),
      (True, False))

# The whole-page formatter: the rule, and why a first answer was refused.
DOUBLED_RULE = "兩個圈連住印（雙圈）就寫兩個：「。。」"
check("the formatter is told to write doubled circles", DOUBLED_RULE in pp.FORMAT_SYSTEM, True)
first, second = "天地玄黃宇宙洪荒日月盈昃辰宿列張", "寒來暑往秋收冬藏閏餘成歲律呂調陽"
# Engine B read a fragment: its text is taken whole, so the formatter punctuates it.
invent = lambda text, kind: text + "某" if kind == "format" else text
with stand_in.book({1: ([stand_in.block(first[:6], [0.3, 0.1, 0.4, 0.2])], first + second)}) as root:
    model = stand_in.StandIn(census=0, formatter=invent)
    record, _ = stand_in.run(root, model)[1]
check("a whole page is formatted with the rule", DOUBLED_RULE in model.systems[-1], True)
check("the first answer's refusal is sealed", (record["formatterRetryReason"] or "")[:40],
      "formatter introduced 1 character(s) not ")
check("provenance keeps the census's key", record["provenance"]["doubledCensus"], "on")

sys.exit(1 if failures else 0)
