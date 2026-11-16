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


def page(blocks, text_b, model, profile=None):
    """Page 1 proofread: (sealed record, Markdown, the kinds of call made,
    the run report)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = stand_in.write_book(Path(tmp), {1: (blocks, text_b)}, profile)
        record, markdown = stand_in.run(root, model)[1]
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



def agreed(text_a: str, text_b: str, start: int = 0, length: int | None = None) -> str | None:
    """agreed_marks on two readings of one text (aligned, no disagreement),
    for the stretch [START, START + LENGTH) of the merged text."""
    segments = pp.align_drafts(text_a, text_b)
    pieces = [segment["a"] for segment in segments]
    merged = "".join(pieces)
    found = pp.engine_places(pp.engine_gaps(text_a)[0], segments, pieces)
    readings = [pp.engine_reading(text_a, found[0] if found else None, len(merged)),
                pp.engine_reading(text_b, pp.engine_b_places(pp.engine_gaps(text_b)[0], segments, pieces), len(merged))]
    length = len(merged) - start if length is None else length
    return pp.agreed_marks(merged[start:start + length], start, readings)


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

print(f"\n{'FAIL' if failures else 'ok'}: {failures} failure(s)")
sys.exit(1 if failures else 0)
