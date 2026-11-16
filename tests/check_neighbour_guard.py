#!/usr/bin/env python3
"""Offline checks of --neighbour-guard (on by default since the round-4
benchmark, the user's decision of 2026-09-30; --no-neighbour-guard turns it
off).

No model, no GPU, no corpus.  裁決 sometimes answers a disagreement with the
text right beside the spot, which neither engine read there: a crop that
does not show the spot (it begins at the next character, or ends at the one
before), or shows a bracketed numeral, a folio or a mark next to a spot
where one engine read a bracket or a rule as a character.  Written, the page
has that text twice.  With the switch, such an answer is not written: the
reading of the engine the page falls back on stands, as it read it (on a D3
question, the reading of the engine that read the text), with a doubt.  The
pages are made up, in the shapes measured on the sealed runs:

- the answer is a numeral two places after the spot, where one engine read a
  bracket as a numeral and the other nothing (a numeral question);
- a substitution answered with the character two places after the spot, on
  a book whose fallback engine is A and on one whose fallback engine is B;
- a D3 question (one engine read a short run the other did not) answered
  with the next character: the run the engine read stands;
- what is not a copy: an engine read the answer at the spot too (a doubled
  character one engine read twice), the text three places away, an answer
  that is an engine's reading in another form (a variant pair), a character
  beside the spot that another question's answer wrote (the context 裁決 was
  shown does not have it there);
- D16's refusal let go (#34): a line only engine A read, in a layout block of
  its own, that 裁決 answered with the text right before the spot - engine B's
  nothing is written, with doubts; kept where 裁決 answered 無 or a misread of
  the line, where the reading runs across two blocks, where engine B read
  something there, and where engine B alone read it (it has no layout);
- the switch off: the answer written, as before, and nothing sealed;
- resume: a page sealed with it is not current for a run without it.

Usage:
    python3 tests/check_neighbour_guard.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

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


SWITCH: list[str] = []   # the default: on
OFF = ["--no-neighbour-guard"]
BOX = [0.3, 0.1, 0.4, 0.8]
# Prose both engines read alike, so that the pages merge (READING_ORDER_AGREEMENT).
LEAD = "秋收冬藏閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
ADJUDICATE = {"preferEngine": "adjudicate"}


# --- The rule itself (neighbour_copy).
BEFORE, AFTER = "天地玄黃宇宙", "洪荒日月盈昃"
for name, args, want in (
        ("the character right after the spot", ("洪", "甲", "乙"), {"side": "after", "distance": 1, "text": "洪"}),
        ("the character two places after it", ("荒", "甲", "乙"), {"side": "after", "distance": 2, "text": "荒"}),
        ("the character right before it", ("宙", "", "一"), {"side": "before", "distance": 1, "text": "宙"}),
        ("the character two places before it", ("宇", "甲", ""), {"side": "before", "distance": 2, "text": "宇"}),
        ("a run right after it", ("洪荒日", "", "乙丙丁"), {"side": "after", "distance": 1, "text": "洪荒日"}),
        ("a run one character after it", ("荒日", "", "乙"), {"side": "after", "distance": 2, "text": "荒日"})):
    check(f"neighbour_copy: {name}", pp.neighbour_copy(*args, BEFORE, AFTER), want)
for name, args in (
        ("three places after the spot", ("日", "甲", "乙")),
        ("three places before it", ("玄", "甲", "乙")),
        ("a run that begins three places after it", ("日月", "甲", "乙")),
        ("text found nowhere beside it", ("丙", "甲", "乙")),
        ("engine B read the answer at the spot too (the character read twice)", ("洪", "", "丙洪")),
        ("engine A read it there too", ("宙", "宙丙", "")),
        ("no answer", ("", "甲", "乙"))):
    check(f"neighbour_copy: not a copy - {name}", pp.neighbour_copy(*args, BEFORE, AFTER), None)
# Variant-folded: 擧 and 舉 are one character (the user's list).
check("neighbour_copy: an answer that is an engine's reading in another form is that reading",
      pp.neighbour_copy("舉", "擧", "乙", "天地舉", AFTER), None)
check("neighbour_copy: the neighbour in another form is the neighbour",
      pp.neighbour_copy("舉", "甲", "乙", "天地擧", AFTER), {"side": "before", "distance": 1, "text": "擧"})
check("neighbour_copy: the nearer copy, before first on a tie",
      (pp.neighbour_copy("洪", "甲", "乙", "天地洪", "荒洪"), pp.neighbour_copy("洪", "甲", "乙", "洪天", "洪荒")),
      ({"side": "before", "distance": 1, "text": "洪"}, {"side": "after", "distance": 1, "text": "洪"}))
# The context 裁決 was shown (engine A's text either side): the copy must stand at the
# same place there too.  A character of the merged text beside the spot that the context
# does not have there is another question's answer, which 裁決 never saw.
check("neighbour_copy: in the context shown at the same place, a copy",
      pp.neighbour_copy("洪", "甲", "乙", BEFORE, AFTER, BEFORE, AFTER), {"side": "after", "distance": 1, "text": "洪"})
check("neighbour_copy: not in the context shown (another answer wrote it), not a copy",
      pp.neighbour_copy("丁", "甲", "乙", "天地丁玄", AFTER, "天地甲玄", AFTER), None)
check("neighbour_copy: in the context shown elsewhere than beside the spot, not a copy",
      pp.neighbour_copy("洪", "甲", "乙", BEFORE, AFTER, BEFORE, "荒洪日月"), None)
# In a book printed in simplified script 裁決 often answers in traditional script: there the
# answer in its simplified form is compared (simplified_form), and only there.
check("neighbour_copy: simplified script, an answer in traditional script that is the neighbour is a copy",
      pp.neighbour_copy("前來", "甲", "", "寄信前来", "", simplified=True),
      {"side": "before", "distance": 1, "text": "前来"})
check("neighbour_copy: ... not in a book printed in traditional script",
      pp.neighbour_copy("前來", "甲", "", "寄信前来", ""), None)
check("neighbour_copy: simplified script, an engine's reading in the other script is that reading",
      pp.neighbour_copy("顢", "颟", "预", "稍涉颟", "", simplified=True), None)
check("neighbour_copy: the window is a parameter",
      pp.neighbour_copy("日", "甲", "乙", BEFORE, AFTER, window=3), {"side": "after", "distance": 3, "text": "日"})


# --- Pages.
def run(a_text, b_text, judge, profile=ADJUDICATE, extra=()):
    """A one-page made-up book, engine A's text one block (or A_TEXT the blocks):
    (record, CJK of the page, report)."""
    model = stand_in.StandIn(census=0, judge=judge)
    blocks = a_text if isinstance(a_text, list) else [stand_in.block(a_text, BOX)]
    with stand_in.book({1: (blocks, b_text)}, profile=profile) as root:
        record, markdown = stand_in.run(root, model, extra=list(extra))[1]
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return record, pp.CJK(markdown), report


def adjudicated(record):
    return [(a["draft_reading"], a["writer_reading"], a["resolved"], a["resolved_from"], a["winner"])
            for a in record["adjudications"]]


# A numeral question: engine B read a closing bracket as 一, engine A nothing, and
# 裁決 answers 二, the numeral two places after the spot.
NUM_A = LEAD[:10] + "日二" + LEAD[10:]
NUM_B = LEAD[:10] + "一日二" + LEAD[10:]
NUMERAL = lambda a, b: "二"  # noqa: E731
record, text, report = run(NUM_A, NUM_B, NUMERAL, extra=OFF)
check("off: the answer copying the numeral two places on is written, as before",
      (text, adjudicated(record), "neighbourGuard" in record["provenance"], "neighbourGuard" in report,
       [d["kind"] for d in record["doubts"]], record["decisionChanges"]),
      (LEAD[:10] + "二日二" + LEAD[10:], [("", "一", "二", "adjudicator", "neither")], False, False, [], []))
record, text, report = run(NUM_A, NUM_B, NUMERAL, extra=SWITCH)
check("on: not written; engine A's reading (nothing) stands, with a doubt",
      (text, adjudicated(record), [d["kind"] for d in record["doubts"]]),
      (NUM_A, [("", "一", "", "adjudicator-neighbour-copy", "undecided")], ["neighbour-copy"]))
check("on: what 裁決 wrote and the copy are sealed on the adjudication",
      (record["adjudications"][0]["answered"], record["adjudications"][0]["neighbourCopy"]),
      ("二", {"side": "after", "distance": 2, "text": "二", "keptEngine": "A", "keptBy": "fallback-engine"}))
check("on: sealed as a neighbourGuard change",
      [(c["decision"], c["rule"], c["after"], c["mergedOffset"], c["engineA"], c["engineB"], c["evidence"])
       for c in record["decisionChanges"]],
      [("neighbourGuard", "neighbour-copy", "", 10, "", "一",
        {"side": "after", "distance": 2, "text": "二", "keptEngine": "A", "keptBy": "fallback-engine",
         "window": 2})])
check("on: the provenance and the run report say so",
      (record["provenance"]["neighbourGuard"], report["neighbourGuard"], report["pages"][0]["neighbourCopies"]),
      ("window=2", {"window": 2, "notWritten": {"A": 1}}, {"A": 1}))
check("on: the doubt names the spot, the answer and the reading kept",
      "「二」" in record["doubts"][0]["detail"] and "kept engine A's" in record["doubts"][0]["detail"], True)

# A substitution answered with the character two places after the spot.
SUB_A = LEAD[:10] + "甲" + LEAD[11:]
SUB_B = LEAD[:10] + "乙" + LEAD[11:]
SECOND = lambda a, b: LEAD[12]  # noqa: E731
record, text, _ = run(SUB_A, SUB_B, SECOND, extra=OFF)
check("off: a substitution answered with the character two places on writes it",
      (text, adjudicated(record)), (LEAD[:10] + LEAD[12] + LEAD[11:], [("甲", "乙", LEAD[12], "adjudicator", "neither")]))
record, text, _ = run(SUB_A, SUB_B, SECOND, extra=SWITCH)
check("on: the fallback engine's reading (A) stands",
      (text, adjudicated(record), record["decisionChanges"][0]["evidence"]["distance"]),
      (SUB_A, [("甲", "乙", "甲", "adjudicator-neighbour-copy", "undecided")], 2))
LEANS_B = {"preferEngine": "adjudicate", "engines": {"decision": {"pairVotes": {"A": 1, "B": 3}}}}
record, text, _ = run(SUB_A, SUB_B, SECOND, profile=LEANS_B, extra=SWITCH)
check("on, a book whose probe leans to engine B: engine B's reading stands",
      (text, adjudicated(record), record["adjudications"][0]["neighbourCopy"]["keptEngine"]),
      (SUB_B, [("甲", "乙", "乙", "adjudicator-neighbour-copy", "undecided")], "B"))
# An engine's reading at the spot, answered: not a question for the guard.
record, text, _ = run(SUB_A, SUB_B, lambda a, b: b, extra=SWITCH)
check("on: an answer that is an engine's reading stands", (text, [d["kind"] for d in record["doubts"]]), (SUB_B, []))

# A D3 question: on a book preferring engine A, engine B read one character engine A
# did not; 裁決 answers with the character after it.
D3_B = LEAD[:10] + "丙" + LEAD[10:]
NEXT = lambda a, b: LEAD[10]  # noqa: E731
record, text, _ = run(LEAD, D3_B, NEXT, profile={"preferEngine": "A"}, extra=OFF)
check("off: the D3 question answered with the next character writes it twice",
      (text, [a.get("askedFor") for a in record["adjudications"]]), (LEAD[:10] + LEAD[10] + LEAD[10:], ["D3"]))
record, text, _ = run(LEAD, D3_B, NEXT, profile={"preferEngine": "A"}, extra=SWITCH)
check("on: the character engine B read stands (as D3 keeps it), not the preference's nothing",
      (text, adjudicated(record), record["adjudications"][0]["neighbourCopy"]["keptBy"],
       [d["kind"] for d in record["doubts"]]),
      (D3_B, [("", "丙", "丙", "adjudicator-neighbour-copy", "undecided")], "D3-reading", ["neighbour-copy"]))

# Not copies.  Engine B read the next character at the spot too - a doubled character
# one engine read twice: the answer stands.
TWICE_B = LEAD[:10] + "丙" + LEAD[10] + LEAD[10:]
record, text, _ = run(LEAD, TWICE_B, NEXT, extra=SWITCH)
check("on: engine B read the answer at the spot too: it stands, no doubt",
      (text, [a["resolved_from"] for a in record["adjudications"]], record["doubts"], record["decisionChanges"]),
      (LEAD[:10] + LEAD[10] + LEAD[10:], ["adjudicator"], [], []))
# Two questions two places apart, each answered with the same third reading: the page
# writes it at both, and neither is a copy of the other - the context 裁決 was shown
# holds engine A's reading there, not the other answer.
TWO_A = LEAD[:10] + "甲" + LEAD[10] + "乙" + LEAD[12:]
TWO_B = LEAD[:10] + "丙" + LEAD[10] + "戊" + LEAD[12:]
record, text, _ = run(TWO_A, TWO_B, lambda a, b: "丁", extra=SWITCH)
check("on: two answers of the same character two places apart both stand",
      (text, [a["resolved_from"] for a in record["adjudications"]], record["doubts"]),
      (LEAD[:10] + "丁" + LEAD[10] + "丁" + LEAD[12:], ["adjudicator", "adjudicator"], []))
# The same character three places away is not taken for a copy.
record, text, _ = run(SUB_A, SUB_B, lambda a, b: LEAD[13], extra=SWITCH)
check("on: the text three places away is not a copy",
      (text, [a["resolved_from"] for a in record["adjudications"]]), (LEAD[:10] + LEAD[13] + LEAD[11:], ["adjudicator"]))

# A line engine B read twice (the modern book's scan 33: the body line after a
# footnote marker came back once more with a stray marker), engine A once;
# 裁決, shown the spot between the two copies, confirms the line.  It is an
# engine's reading, so it is written, and the page says so (neighbour-copy):
# the line may be printed once.
LINE = "目前對於閩粵贛邊界地區的研究仍方興未艾"
TWICE_A = LEAD + LINE
TWICE_B = LEAD + LINE + LINE
record, text, _ = run(TWICE_A, TWICE_B, lambda a, b: LINE)
check("a one-engine reading confirmed beside its own copy: written, with the doubt",
      (text, [a.get("copyBeside") for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]], "ONE-ENGINE READING WRITTEN BESIDE ITS OWN COPY" in (record["doubts"] or [{}])[0].get("detail", "")),
      (LEAD + LINE + LINE, ["before"], ["neighbour-copy"], True))
record, text, _ = run(TWICE_A, LEAD + LINE + "山居雜記卷三頁十二至十五", lambda a, b: "山居雜記卷三頁十二至十五")
check("... not when the reading is other text", ([a.get("copyBeside") for a in record["adjudications"]], [d["kind"] for d in record["doubts"]]),
      ([None], []))


# D16 (#34): a line only engine A read, in a narrow block of its own at the margin (a
# strip of bleed-through), engine B nothing; 裁決, shown that block, answers with the
# text right before the spot - it read the neighbour.  The deletion guard would keep
# engine A's 24 characters; with the switch, engine B's nothing is written, and the
# page says so twice (neighbour-copy, and the reading dropped).
TEXT = LEAD + "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往果珍李柰菜重芥薑海鹹河淡鱗潛羽翔龍師火帝鳥官人皇"
STRAY = "一葉系法包括葉面設計及葉面生長控制系統當葉面生長"
MARGIN = [0.96, 0.5, 0.02, 0.25]
COPY = lambda a, b: TEXT[-8:]  # noqa: E731
record, text, _ = run([stand_in.block(TEXT, BOX), stand_in.block(STRAY, MARGIN)], TEXT, COPY)
check("D16, on: a line only engine A read, answered with the text before it, is not written",
      (text, adjudicated(record), record["adjudications"][0]["answered"],
       record["adjudications"][0]["neighbourCopy"]),
      (TEXT, [(STRAY, "", "", "adjudicator-neighbour-copy", "undecided")], TEXT[-8:],
       {"side": "before", "distance": 1, "text": TEXT[-8:], "keptEngine": "B", "keptBy": "engine-B-read-nothing"}))
check("D16, on: sealed as a D16 change, with the doubts",
      ([(c["decision"], c["rule"], c["after"]) for c in record["decisionChanges"]],
       [d["kind"] for d in record["doubts"]]),
      ([("D16", "answer-copies-neighbour", "")], ["neighbour-copy", "reading-dropped"]))
record, text, _ = run([stand_in.block(TEXT, BOX), stand_in.block(STRAY, MARGIN)], TEXT, COPY, extra=OFF)
check("D16, off: the deletion guard keeps engine A's line, as before",
      (text, adjudicated(record), [d["kind"] for d in record["doubts"]]),
      (TEXT + STRAY, [(STRAY, "", STRAY, "adjudicator-deletion-refused", "undecided")], ["deletion-refused"]))
for name, judge in (("answered 無", lambda a, b: "無"), ("answered a misread of it", lambda a, b: "岡")):
    record, text, _ = run([stand_in.block(TEXT, BOX), stand_in.block(STRAY, MARGIN)], TEXT, judge)
    check(f"D16, on: kept where 裁決 {name}", (text, [a["resolved_from"] for a in record["adjudications"]]),
          (TEXT + STRAY, ["adjudicator-deletion-refused"]))
record, text, _ = run([stand_in.block(TEXT + STRAY[:12], BOX), stand_in.block(STRAY[12:], MARGIN)], TEXT, COPY)
check("D16, on: kept where engine A's reading runs across two blocks (the crop shows one)",
      (text, [a["resolved_from"] for a in record["adjudications"]]), (TEXT + STRAY, ["adjudicator-deletion-refused"]))
record, text, _ = run([stand_in.block(TEXT, BOX)], TEXT + STRAY, COPY)
check("D16, on: kept where engine B alone read the line",
      (text, [a["resolved_from"] for a in record["adjudications"]]), (TEXT + STRAY, ["adjudicator-deletion-refused"]))
record, text, _ = run([stand_in.block(TEXT, BOX), stand_in.block(STRAY, MARGIN)], TEXT + "曰", COPY)
check("D16, on: kept where engine B read something there",
      [a["resolved_from"] for a in record["adjudications"]], ["adjudicator-deletion-refused"])


# --- Sealed only when on, and a key a resume compares when absent.
on_seal = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A", neighbour_guard=True))
off_seal = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A"))
check("--neighbour-guard: sealed with its window when on, and not at all when off",
      (on_seal.get("neighbourGuard"), "neighbourGuard" in off_seal), ("window=2", False))
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "page-0001.json"
    job = SimpleNamespace(out_json=out, out_md=out.with_suffix(".md"))
    current = []
    for sealed_with, run_with in ((on_seal, off_seal), (off_seal, on_seal), (on_seal, on_seal), (off_seal, off_seal)):
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "scanPage": 1,
                  "provenance": {**sealed_with, "markdownSha256": pp.sha256_bytes("天地玄黃".encode("utf-8"))}}
        record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
        out.with_suffix(".md").write_text("天地玄黃", encoding="utf-8")
        out.write_bytes(pp.canonical_json_bytes(record))
        current.append(pp.validate_completion(job, run_with)[0])
check("--neighbour-guard: a resume takes a sealed page as current only under the same setting",
      current, [False, False, True, True])
parse = lambda *extra: pp.build_parser().parse_args(["r", "a", "o", *extra]).neighbour_guard  # noqa: E731
check("parsing: on by default, off with --no-neighbour-guard", (parse(), parse(*OFF)), (True, False))

print(f"{'FAIL' if failures else 'ok'}: {failures} failure(s)")
sys.exit(1 if failures else 0)
