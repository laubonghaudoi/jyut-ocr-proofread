#!/usr/bin/env python3
"""Offline checks of the rule agreedMarks (always on, sealed, no switch).

No model, no GPU, no corpus: made-up pages (tests/stand_in.py), run through
proofread_pages.main() with the stand-in model.

- C1: a block whose every character is an exact stretch of both engines'
  readings, where the two wrote the same marks at every gap, at least two
  places, is written with those marks: no census and no punctuate call, and
  its pass says so (censusNotAsked, rule engines-agree).  The normal path
  (census, then punctuation) where one character differs, where fewer than
  two marks agree, where engine B reads no marks (a vertically set block),
  on a book whose profile saw doubled circles, and where either engine read
  print the mark vocabulary does not hold (a year, a note marker, a Western
  name): the punctuator writes it off the crop, the engines' marks would
  lose it.
- Notes are counted: a footnote or bibliography entry of a horizontal page
  (no block engine A labels vertical_text) gets its census like body text.
  The rule that skipped it (C2) left more marks wrong on the image and was
  dropped.
- gap_sides, which parts an engine gap's marks for C1 and the text beside a
  table.
- The seal: every page carries agreedMarks "on"; a page sealed before the
  rule (without the key) is current as a legacy seal (user decision 5 of
  2026-11-14: the old skill's seals are accepted, no proofreading again);
  one sealed with a removed witness switch's key on is not current; the run
  report counts the passes by rule; the key is in every page sealed with
  it and in none sealed before.  SKILL.md's resume (八) takes the old
  skill's sealed pages as they are, marked legacy, and redoes none, and
  long-book-workflow.md's 〈封條同續跑〉 names agreedMarks among the rule
  keys a legacy seal may lack.

Usage:
    python3 tests/check_agreed_marks.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stand_in  # noqa: E402
from stand_in import pp  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


BODY = [0.1, 0.1, 0.8, 0.3]
NOTE = [0.1, 0.7, 0.8, 0.1]


def page(blocks, text_b, model, profile=None, extra=()):
    """Page 1 proofread: (sealed record, Markdown, the kinds of call made,
    the run report)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = stand_in.write_book(Path(tmp), {1: (blocks, text_b)}, profile)
        record, markdown = stand_in.run(root, model, extra=extra)[1]
        report = __import__("json").loads((root / "report.json").read_text(encoding="utf-8"))
    return record, markdown, model.kinds(), report


def rules(record) -> list:
    return [(p["block"], (p.get("censusNotAsked") or {}).get("rule")) for p in record["punctuationPasses"]]


TEXT = "天地玄黃，宇宙洪荒。日月盈昃，辰宿列張。"

# --- C1: written from the engines -------------------------------------------------------------
record, markdown, kinds, report = page([stand_in.block(TEXT, BODY)], TEXT, stand_in.StandIn(census=4))
check("C1: a block both engines read alike is written with their marks", markdown.strip(), TEXT)
check("C1: no census, no punctuate call", ("census" in kinds, "punctuate" in kinds), (False, False))
check("C1: its pass says so", (rules(record), record["punctuationPasses"][0]["places"],
                               record["punctuationPasses"][0]["census"]), ([(0, "engines-agree")], 4, None))
check("C1: no doubt", record["doubts"], [])
check("C1: sealed", record["provenance"].get("agreedMarks"), "on")
check("C1: the run report counts it", report["censusNotAsked"], {"engines-agree": 1})

# One character differs: the normal path.
record, markdown, kinds, _ = page([stand_in.block(TEXT, BODY)], TEXT.replace("宿", "夜"),
                                  stand_in.StandIn(census=4, punctuate=lambda span: TEXT))
check("one character differing: census and punctuate", ("census" in kinds, "punctuate" in kinds), (True, True))
check("one character differing: no rule on the pass", rules(record), [(0, None)])
check("one character differing: the punctuator's marks", markdown.strip(), TEXT)

# Fewer than two marks: the normal path.
ONE = "天地玄黃宇宙洪荒日月盈昃。"
record, markdown, kinds, _ = page([stand_in.block(ONE, BODY)], ONE, stand_in.StandIn(census=1, punctuate=lambda s: s + "。"))
check("one mark: census and punctuate", ("census" in kinds, "punctuate" in kinds, rules(record)),
      (True, True, [(0, None)]))

# A vertically set block where engine B reads no marks: the normal path.
record, markdown, kinds, _ = page([stand_in.block(TEXT, BODY, "vertical_text")], TEXT.replace("，", "").replace("。", ""),
                                  stand_in.StandIn(census=4, punctuate=lambda span: TEXT))
check("vertical, engine B no marks: census and punctuate", ("census" in kinds, "punctuate" in kinds, rules(record)),
      (True, True, [(0, None)]))
check("vertical, engine B no marks: the punctuator's marks", markdown.strip(), TEXT)

# A book whose profile saw doubled circles: the normal path.
record, _, kinds, _ = page([stand_in.block(TEXT, BODY)], TEXT, stand_in.StandIn(census=4, punctuate=lambda s: TEXT),
                           profile={"preferEngine": "A", "notation": {"doubled": True, "blocksSurveyed": 3}})
check("doubled circles in the profile: census and punctuate", ("census" in kinds, "punctuate" in kinds, rules(record)),
      (True, True, [(0, None)]))

# Print beyond the marks (a year): the normal path, and the punctuator's
# answer (which writes the year off the crop) stands.
YEAR_A, YEAR_B = "見某書（1990）頁，又見某處。", "見某書(1990)頁，又見某處。"
record, markdown, kinds, _ = page([stand_in.block(YEAR_A, BODY)], YEAR_B,
                                  stand_in.StandIn(census=4, punctuate=lambda span: YEAR_A))
check("a year both engines read: census and punctuate, the year written",
      ("census" in kinds, "punctuate" in kinds, rules(record), markdown.strip()), (True, True, [(0, None)], YEAR_A))
# The same page with --same-print-marks: taken with the engines' marks, no call, its pass named for the rule.
record, markdown, kinds, report = page([stand_in.block(YEAR_A, BODY)], YEAR_B, stand_in.StandIn(census=4),
                                       extra=["--same-print-marks"])
check("same_print page: no census, no punctuate call, the pass named engines-agree-print",
      ("census" in kinds, "punctuate" in kinds, rules(record), report["censusNotAsked"],
       record["provenance"].get("samePrintMarks")),
      (False, False, [(0, "engines-agree-print")], {"engines-agree-print": 1}, "on"))
check("same_print page: the engines' marks, no digits", any(c.isdigit() for c in markdown), False)



def agreed(text_a: str, text_b: str, start: int = 0, length: int | None = None,
           same_print: bool = False) -> str | None:
    """agreed_marks on two readings of one text (aligned, no disagreement),
    for the stretch [START, START + LENGTH) of the merged text."""
    segments = pp.align_drafts(text_a, text_b)
    pieces = [segment["a"] for segment in segments]
    merged = "".join(pieces)
    found = pp.engine_places(pp.engine_gaps(text_a)[0], segments, pieces)
    readings = [pp.engine_reading(text_a, found[0] if found else None, len(merged)),
                pp.engine_reading(text_b, pp.engine_b_places(pp.engine_gaps(text_b)[0], segments, pieces), len(merged))]
    length = len(merged) - start if length is None else length
    return pp.agreed_marks(merged[start:start + length], start, readings, same_print=same_print)


# A note marker (in LaTeX or not), a Western name, a middle dot, 〔〕: print
# beyond the marks, not taken; HTML markup is no print; a line break in one
# engine's reading parts nothing; print on the other side of a line break at
# the stretch's end is the next or last block's.
check("a note marker, in LaTeX in engine A: not taken",
      (agreed("天地\\(^{①}\\)，玄黃。宇宙。", "天地①，玄黃。宇宙。"), agreed("天地①，玄黃。宇宙。", "天地①，玄黃。宇宙。")),
      (None, None))
check("a Western name, a middle dot, 〔〕: not taken",
      (agreed("費爾南（Braudel）指出，盜賊。", "費爾南(Braudel)指出，盜賊。"),
       agreed("費爾南·布勞岱爾指出，盜賊。", "費爾南·布勞岱爾指出，盜賊。"),
       agreed("〔日〕某某編，《某書》。", "〔日〕某某編，《某書》。")), (None, None, None))
check("print past a line break at the stretch's edge: the other block's",
      (agreed("天地，玄黃。12\n宇宙，洪荒。", "天地，玄黃。12\n宇宙，洪荒。", 4, 4),
       agreed("天地，玄黃。12\n宇宙，洪荒。", "天地，玄黃。12\n宇宙，洪荒。", 0, 4)), ("宇宙，洪荒。", None))
check("markup and a line break: the same marks", agreed("天地，<b>玄黃</b>。宇宙。", "天地，\n玄黃。宇宙。"), "天地，玄黃。宇宙。")
check("bullets one shape, a leader run one mark: agreed, engine A's written",
      agreed("●天地……玄黃。", "⦿天地…玄黃。"), "●天地……玄黃。")
check("another mark at one gap: not agreed", agreed("天地，玄黃。宇宙。", "天地、玄黃。宇宙。"), None)
check("a mark of the stretch's edge: the stretch's own only",
      (agreed("天地，玄黃。宇宙「洪荒」。", "天地，玄黃。宇宙「洪荒」。", 4, 2),
       agreed("天地，玄黃。宇宙「洪荒」。", "天地，玄黃。宇宙「洪荒」。", 0, 4)), (None, "天地，玄黃。"))

# --same-print-marks (same_print): print both engines read alike is no bar.
check("same_print off: a year both engines read is refused", agreed(YEAR_A, YEAR_B), None)
taken = agreed(YEAR_A, YEAR_B, same_print=True)
check("same_print on: a year both engines read is taken, no digits written",
      (taken is not None, taken is not None and any(c.isdigit() for c in taken)), (True, False))
check("same_print on: figures the engines read differently: not taken",
      agreed("見某書（1990）頁，又見某處。", "見某書（1991）頁，又見某處。", same_print=True), None)
check("same_print on: print in one engine's gap only: not taken",
      agreed("見某書（1990）頁，又見某處。", "見某書（）頁，又見某處。", same_print=True), None)
check("same_print on: a Latin name both read alike: taken",
      agreed("費爾南（Braudel）指出，盜賊。", "費爾南(Braudel)指出，盜賊。", same_print=True) is not None, True)
check("same_print on: markup and spaces differ only between the engines: taken",
      agreed("天地，$ ^{66} $玄黃。宇宙。", "天地，$^{66}$玄黃。宇宙。", same_print=True) is not None, True)
check("same_print on: marks differ at one gap: not taken",
      agreed("天地，玄黃。宇宙1990洪荒。", "天地、玄黃。宇宙1990洪荒。", same_print=True), None)
# Note markers are set aside in the comparison (gap_print): the marks the
# engines write for one differ, or one engine alone writes one, and the block
# is taken; a year that differs or that one engine alone reads is not.
check("same_print on: note markers written differently, same marks: taken",
      (agreed("天地。⑥⑤玄黃宇宙。", "天地。$ ^{65} $玄黃宇宙。", same_print=True) is not None,
       agreed("天地。$ ^{①} $玄黃宇宙。", "天地。①玄黃宇宙。", same_print=True) is not None,
       agreed("天地。⑥8〔玄黃〕宇宙。", "天地。$ ^{68} $〔玄黃〕宇宙。", same_print=True) is not None),
      (True, True, True))
check("same_print on: a note marker on one side only, same marks: taken",
      agreed("天地。⑥玄黃宇宙。", "天地。玄黃宇宙。", same_print=True) is not None, True)
check("same_print on: a year that differs between the engines: not taken",
      agreed("天地。1992玄黃宇宙。", "天地。1997玄黃宇宙。", same_print=True), None)
check("same_print on: a year only one engine reads: not taken",
      agreed("天地。1992玄黃宇宙。", "天地。玄黃宇宙。", same_print=True), None)
check("gap_print: a year kept, a note marker set aside",
      [pp.gap_print(gap) for gap in ("1992", "⑥5", "$ ^{65} $", "。⑥8〔")],
      ["1992", "", "", "。〔"])

# The marks of one engine gap, parted by where they go (gap_sides, which
# marks_as_written and so C1 use): print parts the gap; an opening mark and
# what follows it go after; closing marks go before; a dash is no mark here;
# markup and entities are no print; with BY_PRINT false print parts nothing.
check("gap_sides", [pp.gap_sides(gap) for gap in ("。(2)", "（）", "。「", "」。", "，——", "<td>。</td>", "&nbsp;。「")]
      + [pp.gap_sides("。(2)", False)],
      [("。（", "）"), ("", "（）"), ("。", "「"), ("」。", ""), ("，", ""), ("。", ""), ("。", "「"), ("。", "（）")])

# --- Notes are counted like body text -----------------------------------------------------------
NOTE_A, NOTE_B = "註一見某書，頁三。", "註一見某書頁三"
BODY_A, BODY_B = "天地玄黃，宇宙洪荒。", "天地玄黃宇宙洪荒"
for label in ("footnote", "reference_content"):
    record, markdown, kinds, report = page(
        [stand_in.block(BODY_A, BODY), stand_in.block(NOTE_A, NOTE, label)], BODY_B + "\n" + NOTE_B,
        stand_in.StandIn(census=2, punctuate=lambda span: {"天地玄黃宇宙洪荒": BODY_A}.get(span, NOTE_A)))
    check(f"notes ({label}): the body and the note both counted and punctuated",
          (rules(record), kinds.count("census"), kinds.count("punctuate")), ([(0, None), (1, None)], 2, 2))
    check(f"notes ({label}): the page", markdown.strip().split("\n"), [BODY_A, NOTE_A])
check("notes: the run report counts no pass", report["censusNotAsked"], {})

# --- The seal --------------------------------------------------------------------------------
decided = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A"))
check("agreedMarks is always sealed, never optional",
      (decided.get("agreedMarks"), "agreedMarks" in pp.OPTIONAL_PROVENANCE), ("on", False))
check("--same-print-marks: sealed only when on",
      (decided.get("samePrintMarks"), "samePrintMarks" in decided,
       pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A",
                                              same_print_marks=True)).get("samePrintMarks")),
      (None, False, "on"))
check("--same-print-marks: a page sealed with it is not current without it",
      "samePrintMarks" in pp.OPTIONAL_PROVENANCE, True)
check("no switch turns it off", any("agreed" in option for action in pp.build_parser()._actions
                                    for option in action.option_strings), False)
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "page-0001.json"
    job = SimpleNamespace(out_json=out, out_md=out.with_suffix(".md"))
    before = {key: value for key, value in decided.items() if key != "agreedMarks"}
    results, keyed = [], []
    for sealed_with in (before, dict(decided, engineAConfirmsFirstAnswer="on"),
                        dict(decided, censusSkipOverCeiling="on"), decided):
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "scanPage": 1,
                  "provenance": {**sealed_with, "markdownSha256": pp.sha256_bytes(TEXT.encode("utf-8"))}}
        record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
        out.with_suffix(".md").write_text(TEXT, encoding="utf-8")
        out.write_bytes(pp.canonical_json_bytes(record))
        results.append(pp.validate_completion(job, decided))
        keyed.append(b"agreedMarks" in out.read_bytes())
# Changed 2026-11-14 (user decision 5: the old skill's seals are accepted as
# they are, no proofreading again): a page sealed before the rule is current,
# as a legacy seal that names the key; a removed switch sealed on still is not.
check("resume: a page sealed before the rule is a legacy seal, one with a removed witness switch on is not current",
      results, [(True, pp.LEGACY_REASON + "agreedMarks"), (False, "provenance engineAConfirmsFirstAnswer differs"),
                (False, "provenance censusSkipOverCeiling differs"), (True, "current")])
check("resume: the key's name is in a sealed page's bytes exactly when it was sealed with the rule", keyed,
      [False, True, True, True])
# The docs say the same (decision 5).  Q's run sheet had the pages sealed
# before the rule found by grep -L and done again with --overwrite --pages;
# that rule is gone with the decision, and so are its two checks (the
# commands in SKILL.md, and the page list they printed, run in a shell).
root = Path(__file__).resolve().parent.parent
skill = (root / "SKILL.md").read_text(encoding="utf-8")
resume = skill.split("\n## 八、", 1)[-1].split("\n## ", 1)[0] if "\n## 八、" in skill else ""
workflow = (root / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
seals = (workflow.split("\n## 封條同續跑", 1)[-1].split("\n## ", 1)[0]
         if "\n## 封條同續跑" in workflow else "")
legacy = next((line for line in seals.splitlines() if "**舊版技能封好嘅頁照收**" in line), "")
check("SKILL.md: a resumed book's old seals are taken as they are, marked legacy, no page done again",
      ("舊版技能封好嘅頁同 OCR 底稿照收，唔重做" in resume, "`legacy`" in resume,
       "grep -L agreedMarks" in skill, "唔好 `--overwrite` 成本" in resume), (True, True, False, True))
check("long-book-workflow.md: a legacy seal may lack the rule's key, counted, a key recorded otherwise still blocks",
      ("`agreedMarks`" in legacy, "`legacy`" in legacy, "`counts.legacy`" in legacy, "仲係 blocked" in legacy),
      (True, True, True, True))


# --- --unmarked-page-sample: a page neither engine read marks on -------------------------------
# Measured (the benchmark's 28 samples, 2026-11-17): one unpunctuated book's 11 pages asked
# 244 punctuation questions, a third of all the questions, and 241 were answered "no marks".
# With the switch the three longest blocks are asked first; answered without a mark, the
# page's other blocks are written as they are, unasked; a mark in one, and all are asked.
LINES = ["天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往", "秋收冬藏閏餘成歲律呂調陽", "雲騰致雨露結爲霜",
         "金生麗水玉出崑岡", "劍號巨闕珠稱夜光", "果珍李柰"]


def unmarked_page(model, extra=("--unmarked-page-sample",), profile=None, lines=LINES, text_b=None):
    blocks = [stand_in.block(line, [0.1, 0.05 + 0.12 * k, 0.8, 0.1]) for k, line in enumerate(lines)]
    return page(blocks, "\n".join(lines) if text_b is None else text_b, model, profile=profile, extra=list(extra))


record, markdown, kinds, report = unmarked_page(stand_in.StandIn(census=0))
check("unmarked page: the three longest blocks are asked, the other three are not",
      (kinds.count("census"), rules(record)),
      (3, [(0, None), (1, None), (2, None), (3, "page-unmarked-sample"), (4, "page-unmarked-sample"),
           (5, "page-unmarked-sample")]))
check("unmarked page: every block's text is written, without marks",
      [line for line in markdown.splitlines() if line.strip()], LINES)
check("unmarked page: sealed with the switch, counted in the run report, the sample's passes say so",
      (record["provenance"].get("unmarkedPageSample"), report["censusNotAsked"],
       [p.get("unmarkedSample") for p in record["punctuationPasses"][:4]]),
      ("on", {"page-unmarked-sample": 3}, [{"waiting": 3, "taken": True}] * 3 + [None]))
record, markdown, kinds, _ = unmarked_page(stand_in.StandIn(census=0), extra=())
check("unmarked page, switch off: every block is asked, as before",
      (kinds.count("census"), rules(record), "unmarkedPageSample" in record["provenance"]),
      (6, [(k, None) for k in range(6)], False))
record, markdown, kinds, _ = unmarked_page(stand_in.StandIn(census=1, punctuate=lambda span: span + "。"))
check("a mark in the sample: the other blocks are asked too, and every block has its mark",
      (kinds.count("census"), rules(record), [line for line in markdown.splitlines() if line.strip()],
       record["punctuationPasses"][0].get("unmarkedSample")),
      (6, [(k, None) for k in range(6)], [line + "。" for line in LINES], {"waiting": 3, "taken": False}))
record, _, kinds, _ = unmarked_page(stand_in.StandIn(census=0), lines=LINES[:3])
check("three blocks or fewer: all asked, no sample", (kinds.count("census"), rules(record)), (3, [(k, None) for k in range(3)]))
record, _, kinds, _ = unmarked_page(stand_in.StandIn(census=0),
                                    profile={"preferEngine": "A", "notation": {"doubled": True, "blocksSurveyed": 3}})
check("doubled circles in the profile: no sample, every block asked", kinds.count("census"), 6)
# The candidates (unmarked_candidates): the page, then the block.
BLOCKS = [{"text": line} for line in LINES]
check("candidates: every block engine A read as characters alone, on a page with no marks",
      pp.unmarked_candidates(BLOCKS, LINES, range(6), "".join(LINES)), [0, 1, 2, 3, 4, 5])
check("candidates: not a block with a figure or Latin text, which the punctuator writes",
      pp.unmarked_candidates([{"text": "秋收冬藏 1990"}, {"text": "天地 A 玄黃"}] + BLOCKS[2:], LINES, range(6),
                             "".join(LINES)), [2, 3, 4, 5])
check("candidates: two marks on the page are an engine's noise, three are a page with marks",
      [len(pp.unmarked_candidates([{"text": LINES[0] + marks}] + BLOCKS[1:], LINES, range(6), "".join(LINES)))
       for marks in ("。、", "。、，")], [5, 0])
check("candidates: none where engine B read marks, or there is no second engine",
      (pp.unmarked_candidates(BLOCKS, LINES, range(6), "，。、".join(LINES)),
       pp.unmarked_candidates(BLOCKS, LINES, range(6), None)), ([], []))
check("the switch is sealed only when on, and a page sealed before it is current",
      ("unmarkedPageSample" in decided,
       pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A",
                                              unmarked_page_sample=True)).get("unmarkedPageSample"),
       "unmarkedPageSample" in pp.OPTIONAL_PROVENANCE), (False, "on", True))

print(f"\n{'FAIL' if failures else 'ok'}: {failures} failure(s)")
sys.exit(1 if failures else 0)
