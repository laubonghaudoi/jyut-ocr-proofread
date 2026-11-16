"""Checks for the golden_page score: the kind of each line (heading, list item,
footnote, table row, plain) compared through the character alignment; note
markers as one token in either convention ([^N] or ①); ratchets per stage
(--stage); and text assertions on a repaired-pages directory (no page JSON).

Usage: python3 tests/check_golden_scoring.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_regression as rr  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def kinds(golden: str, output: str) -> list[str]:
    metrics = rr.score_golden_page(golden, [(1, output)])
    return [f"{e['golden']} as {e['output']}" for e in metrics["structureMismatches"]]


# --- structure ---------------------------------------------------------------
BODY = "在日常生活中，我們經常看到各行政單位機構避重就輕。"
check("same structure: no error", kinds("## 緒論\n\n" + BODY, "## 緒論\n\n" + BODY), [])
check("a heading written as a plain line", kinds("## 緒論\n\n" + BODY, "緒論\n\n" + BODY), ["heading as plain"])
check("a heading run into the paragraph", kinds("## 緒論\n\n" + BODY, "緒論" + BODY), ["heading as plain"])
check("the first words of a paragraph made a heading",
      kinds(BODY, "## 在日常生活中\n\n我們經常看到各行政單位機構避重就輕。"), ["plain as heading"])
check("a date line made a heading", kinds("十七年二月十一日\n\n" + BODY, "### 十七年二月十一日\n\n" + BODY),
      ["plain as heading"])
TOC = "1. 在廣州各界慶祝元旦大會演說\n2. 出巡東江抵蕉嶺對獨立第一師官長訓話\n"
check("printed numbers are text, not a list", kinds(TOC, TOC), [])
check("numbered lines written as bullets", kinds(TOC, "- 在廣州各界慶祝元旦大會演說\n- 出巡東江抵蕉嶺對獨立第一師官長訓話\n"),
      ["plain as list", "plain as list"])
check("（一） is text too", kinds("（一）準備\n", "- 準備\n"), ["plain as list"])
NOTE = "之中。[^1]荷蘭史家。\n\n[^1]: 〔法〕布勞岱爾著，唐家龍譯。"
check("a note head as ① is a footnote line", kinds(NOTE, "之中。①荷蘭史家。\n\n① 〔法〕布勞岱爾著，唐家龍譯。"), [])
check("a note written as a list item", kinds(NOTE, "之中。①荷蘭史家。\n\n- ① 〔法〕布勞岱爾著，唐家龍譯。"),
      ["footnote as list"])
check("a note whose marker is lost", kinds(NOTE, "之中。①荷蘭史家。\n\n〔法〕布勞岱爾著，唐家龍譯。"),
      ["footnote as plain"])
check("a note joined to the body", kinds(NOTE, "之中。①荷蘭史家。〔法〕布勞岱爾著，唐家龍譯。"),
      ["footnote as plain"])
metrics = rr.score_golden_page("## 緒論\n\n" + BODY, [(1, "### 緒論\n\n" + BODY)])
check("a heading level is information, not an error",
      (metrics["structureErrors"], [(e["golden"], e["output"]) for e in metrics["headingLevels"]]), (0, [(2, 3)]))
TABLE = "| 年 | 事 |\n| --- | --- |\n| 正德九年 | 禁止行廣鹽 |\n"
check("table rows are left to the table score", kinds(TABLE, "年 事\n\n正德九年 禁止行廣鹽"), [])
check("an HTML table row is a table row", rr.line_kind("<tr><td>贛營</td>"), "table")
check("[^1] opening a line of text is a marker, not a note's head", rr.line_kind("[^1]荷蘭史家。"), "plain")
check("a seal or a tag over two lines keeps the lines after it",
      kinds("前言\n\n## 緒論\n\n" + BODY, "前言〔印：甲\n乙〕<br\n/>\n\n## 緒論\n\n" + BODY), [])
check("a line with no Han character is not compared",
      kinds("Sow-Theng Leong, pp. 31-36.\n\n" + BODY, "## Sow-Theng Leong, pp. 31-36.\n\n" + BODY), [])
metrics = rr.score_golden_page("## 第一集\n\n" + BODY, [(1, "第一集"), (2, BODY)])
check("an output line is keyed by its page", [(e["output"], e["page"]) for e in metrics["structureMismatches"]],
      [("plain", 1)])

# --- note markers ------------------------------------------------------------
for name, golden, output in (
        ("[^1] and ①", "之中。[^1]荷", "之中。①荷"),
        ("[^21] and ㉑", "之中。[^21]荷", "之中。㉑荷"),
        ("[^72] and ⑦²", "毒。[^72]其", "毒。⑦²其"),
        ("[^276] and ②⁷⁶", "毒。[^276]其", "毒。②⁷⁶其"),
        ("[^78] and ⁷⁸", "合。[^78]但", "合。⁷⁸但"),
        ("a note's head [^1]: and ①", "明\n\n[^1]: 法著", "明\n\n① 法著")):
    metrics = rr.score_golden_page(golden, [(1, output)])
    check(f"{name} are one kept marker", (metrics["marksGolden"], metrics["marksOutput"], metrics["marksRight"]),
          (metrics["marksGolden"], metrics["marksGolden"], metrics["marksGolden"]))
check("the marker token carries its number", rr.golden_units("毒。⑦²其[^72]")[1], {1: ["。", "[^72]"], 2: ["[^72]"]})
metrics = rr.score_golden_page("之中。[^1]荷蘭\n\n[^1]: 法著", [(1, "之中。荷蘭\n\n法著")])
check("a lost marker is missed, in the text and at the note",
      [m["mark"] for m in metrics["missedMarks"]], ["[^1]", "[^1]"])
metrics = rr.score_golden_page("鹽\n\n[^197]: 明", [(1, "鹽\n\n⑲明")])
check("a marker of another number is missed and extra",
      ([m["mark"] for m in metrics["missedMarks"]], [m["mark"] for m in metrics["extraMarks"]]), (["[^197]"], ["[^19]"]))
check("other marks are tokens by the character as before: （1） (1990)",
      rr.golden_units("移民（1）年(1990)")[1], {2: ["（", "1", "）"], 3: ["(", "1", "9", "9", "0", ")"]})
check("a circled figure with ASCII figures after it keeps them apart", rr.golden_units("①1996年")[1][0],
      ["[^1]", "1", "9", "9", "6"])

# --- ratchets per stage ------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / "_check_golden_scoring.md"
    golden.write_text("## 緒論\n\n" + BODY, encoding="utf-8")
    (Path(tmp) / "page-0001.md").write_text("緒論\n\n在日常生活中，我們經常看到各行政單位機構避重。", encoding="utf-8")
    base = {"type": "golden_page", "page": 1, "value": golden.name}
    try:
        for name, assertion, stage, want in (
                ("no ratchet: a measurement", base, None, "info"),
                ("top-level maxErrors 0 is broken", dict(base, maxErrors=0), None, "fail"),
                ("the stage's block replaces the top level",
                 dict(base, maxErrors=0, ratchets={"repaired": {"maxErrors": 2}}), "repaired", "pass"),
                ("no block for the stage: the top level",
                 dict(base, maxErrors=0, ratchets={"repaired": {"maxErrors": 2}}), "phase3", "fail"),
                ("without --stage the top level",
                 dict(base, maxErrors=2, ratchets={"repaired": {"maxErrors": 0}}), None, "pass"),
                ("the stage's block, not the top level, is the ratchet",
                 dict(base, maxErrors=2, ratchets={"repaired": {"maxErrors": 0}}), "repaired", "fail"),
                ("the stage's block replaces the top level whole: a field it leaves out does not apply",
                 dict(base, maxStructureErrors=0, ratchets={"repaired": {"maxErrors": 2}}), "repaired", "pass"),
                ("maxStructureErrors 0 is broken by a heading lost", dict(base, maxStructureErrors=0), None, "fail"),
                ("maxStructureErrors 1 holds", dict(base, maxStructureErrors=1), None, "pass")):
            rr.STAGE = stage
            try:
                status, detail, _ = rr.check(assertion, Path(tmp))
            finally:
                rr.STAGE = None
            check(f"{name}: {detail.split(' | ', 1)[-1] if ' | ' in detail else status}", status, want)
    finally:
        golden.unlink()

# The case files' ratchets blocks name a stage --stage takes and ratchet fields.
fields = {field for field, _, _ in rr.RATCHETS + rr.TABLE_RATCHETS}
for path in sorted(rr.CASES.glob("*.json")):
    for assertion in json.loads(path.read_text(encoding="utf-8"))["assertions"]:
        for stage, block in (assertion.get("ratchets") or {}).items():
            check(f"{path.stem} {assertion['id']}: ratchets.{stage} is a stage with ratchet fields",
                  stage in rr.STAGES and set(block) - {"ratchetFrom"} <= fields, True)

# --- text assertions on repaired pages (page-NNNN.md alone, no page JSON) -----
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "page-0003.md").write_text("### 緒論\n\n吾民卽起。羣衆。", encoding="utf-8")
    (Path(tmp) / "page-0004.md").write_text("吾民即起。", encoding="utf-8")
    for name, assertion, want in (
            ("contains reads a repaired page", {"type": "contains", "page": 3, "value": "緒論"}, "pass"),
            ("a form the user's list accepts passes (卽 for 即)", {"type": "contains", "page": 3, "value": "即起"}, "pass"),
            ("a form only OpenCC accepts fails (羣 for 群)", {"type": "contains", "page": 3, "value": "群衆"}, "fail"),
            ("absent reads a repaired page", {"type": "absent", "page": 4, "value": "羣"}, "pass"),
            ("a range passes when one page has it", {"type": "contains", "page": 3, "pages": [3, 4], "value": "羣衆"}, "pass"),
            ("a page JSON assertion skips", {"type": "json_field", "page": 3, "field": "mode", "value": "x"}, "skip")):
        status, detail = rr.check_text(assertion, Path(tmp), False)
        check(f"{name}: {detail or status}", status, want)

sys.exit(1 if failures else 0)
