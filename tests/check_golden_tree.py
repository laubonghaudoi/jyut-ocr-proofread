"""Checks for the golden_tree assertion: a printed chart drawn as a nested list, a
file tree, a sideways brace drawing or a Mermaid graph is the same tree; prose is not.

Usage: python3 tests/check_golden_tree.py   (exits non-zero on the first failure)
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


GOLDEN = """\
- 科學
  - 物的科學（一曰自然科學）
    - 數學
    - 物理學
    - 化學
  - 心的科學
    - 論理學
    - 言語學
    - 文學
"""
FILE_TREE = """\
科學
├── 物的科學（一曰自然科學）
│   ├── 數學
│   ├── 物理學
│   └── 化學
└── 心的科學
    ├── 論理學
    ├── 言語學
    └── 文學
"""
# Drawn as a printed brace chart: each parent in the middle of its children's bar,
# box-drawing characters two columns wide, and a note wrapped over two lines.
BRACE = """\
                      ┌─ 數學
      ┌─ 物的科學 ─┼─ 物理學
      │   （一曰     └─ 化學
科學─┤    自然科學）
      │              ┌─ 論理學
      └─ 心的科學 ─┼─ 言語學
                      └─ 文學
"""
MERMAID = """\
```mermaid
graph LR
  a[科學] --> b[物的科學（一曰自然科學）]
  a --> c[心的科學]
  b --> d[數學]
  b --> e[物理學]
  b --> f[化學]
  c --> g[論理學]
  c --> h[言語學]
  c --> i[文學]
```
"""
# Mermaid's labelled edges: the label is not part of the child's name.
MERMAID_LABELLED = MERMAID.replace("a --> c[心的科學]", "a -->|二| c[心的科學]").replace("b --> e[物理學]", "b ---|理| e[物理學]")
PROSE = "科學分爲物的科學與心的科學。物的科學一曰自然科學，有數學、物理學、化學。心的科學有論理學、言語學、文學。"
# 化學 also written under 心的科學: all eight golden links present, one wrong link more.
DOUBLED = FILE_TREE.replace("    ├── 論理學", "    ├── 化學\n    ├── 論理學")
# The note written as plain text below the tree, not as the node's note.
NOTE_ELSEWHERE = FILE_TREE.replace("物的科學（一曰自然科學）", "物的科學") + "\n一曰自然科學\n"
# 化學 moved from 物的科學 to 心的科學: one link wrong, seven right.
WRONG = FILE_TREE.replace("│   ├── 物理學\n│   └── 化學\n", "│   └── 物理學\n").replace("    ├── 論理學", "    ├── 化學\n    ├── 論理學")

with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / "_check_golden_tree.md"
    golden.write_text(GOLDEN, encoding="utf-8")
    try:
        for name, text, want_status, want_form in (
                ("nested list", GOLDEN, "pass", "list"),
                ("file tree", FILE_TREE, "pass", "indent"),
                ("brace drawing", BRACE, "pass", "brace"),
                ("mermaid", MERMAID, "pass", "mermaid"),
                ("mermaid with labelled edges", MERMAID_LABELLED, "pass", "mermaid"),
                ("a child also under a wrong parent fails", DOUBLED, "fail", None),
                ("a note written outside the tree does not count", NOTE_ELSEWHERE, "fail", None),
                ("prose is not a tree", PROSE, "fail", None)):
            (Path(tmp) / "page-0006.md").write_text(text, encoding="utf-8")
            status, detail, metrics = rr.check({"type": "golden_tree", "page": 6, "value": "_check_golden_tree.md"}, Path(tmp))
            check(f"{name}: {detail}", (status, metrics["form"] if status == "pass" else None), (want_status, want_form))
        (Path(tmp) / "page-0006.md").write_text(WRONG, encoding="utf-8")
        status, detail, metrics = rr.check({"type": "golden_tree", "page": 6, "value": "_check_golden_tree.md"}, Path(tmp))
        check(f"a child moved to the wrong parent fails: {detail}", (status, metrics["edgesRight"]), ("fail", 7))
        # Decision D10: a node the golden marks uncertain (uncertainParents) counts
        # under any parent the assertion lists, and the metrics say which was taken.
        uncertain = {"type": "golden_tree", "page": 6, "value": "_check_golden_tree.md",
                     "uncertainParents": {"化學": ["物的科學", "心的科學"]}}
        status, detail, metrics = rr.check(uncertain, Path(tmp))
        check(f"an uncertain node under another listed parent passes: {detail}",
              (status, metrics["edgesRight"], metrics["uncertainParentsTaken"]),
              ("pass", 8, [{"child": "化學", "golden": "物的科學", "written": "心的科學"}]))
        status, detail, metrics = rr.check(dict(uncertain, uncertainParents={"化學": ["物的科學", "科學"]}), Path(tmp))
        check(f"a parent the assertion does not list still fails: {detail}", (status, metrics["edgesRight"]), ("fail", 7))
        rr.UNCERTAIN_PARENTS = False
        try:
            status, detail, metrics = rr.check(uncertain, Path(tmp))
        finally:
            rr.UNCERTAIN_PARENTS = True
        check(f"--no-uncertain-parents: the golden's parent only: {detail}",
              (status, metrics["edgesRight"], metrics["uncertainParentsTaken"]), ("fail", 7, []))
        (Path(tmp) / "page-0006.md").write_text(DOUBLED, encoding="utf-8")
        status, detail, metrics = rr.check(uncertain, Path(tmp))
        check(f"an uncertain node under two listed parents keeps one wrong: {detail}",
              (status, metrics["edgesWrong"], metrics["uncertainParentsTaken"]), ("fail", 1, []))
    finally:
        golden.unlink()

# The case files' uncertainParents name a node of their golden, and list the
# golden's own parent among the parents allowed.
for path in sorted(rr.CASES.glob("*.json")):
    for assertion in json.loads(path.read_text(encoding="utf-8"))["assertions"]:
        if assertion.get("type") != "golden_tree" or not assertion.get("uncertainParents"):
            continue
        edges, _ = rr.tree_parses((rr.GOLDEN / assertion["value"]).read_text(encoding="utf-8"))["list"]
        for child, parents in assertion["uncertainParents"].items():
            own = sorted(p for p, c in edges if c == child)
            check(f"{path.stem} {assertion['id']}: {child} is under {own} in the golden, allowed {parents}",
                  bool(own) and set(own) <= set(parents) and len(parents) > 1, True)

# A page with prose around the chart: the page's golden is the prose alone, and
# skipBetween takes the chart's lines out before scoring, whatever form they are in.
with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / "_check_skip_between.md"
    golden.write_text("法學爲科學之一。圖而解之如左。\n\n右圖所列。非舉凡百科學而分類之也。。", encoding="utf-8")
    try:
        for form, chart in (("file tree", FILE_TREE), ("brace drawing", BRACE), ("mermaid", MERMAID)):
            page = "法學爲科學之一。圖而解之如左。\n\n" + chart + "\n右圖所列。非舉凡百科學而分類之也。。"
            (Path(tmp) / "page-0006.md").write_text(page, encoding="utf-8")
            a = {"type": "golden_page", "page": 6, "value": "_check_skip_between.md"}
            _, _, whole = rr.check(a, Path(tmp))
            _, detail, cut = rr.check(dict(a, skipBetween=["圖而解之如左", "右圖所列"]), Path(tmp))
            check(f"skipBetween takes out a {form} chart: {detail}", (whole["errors"] > 0, cut["errors"]), (True, 0))
        # An output that loops the anchor sentence keeps the copies in the score.
        loop = "法學爲科學之一。圖而解之如左。\n" + "圖而解之如左。\n" * 3 + "\n" + FILE_TREE + "\n右圖所列。非舉凡百科學而分類之也。。"
        (Path(tmp) / "page-0006.md").write_text(loop, encoding="utf-8")
        _, detail, m = rr.check(dict(a, skipBetween=["圖而解之如左", "右圖所列"]), Path(tmp))
        check(f"skipBetween cuts after the last anchor copy: {detail}", m["errors"] > 0, True)
        # A chart that runs over a page break is cut across the pages.
        (Path(tmp) / "page-0006.md").write_text("法學爲科學之一。圖而解之如左。\n\n" + FILE_TREE[:40], encoding="utf-8")
        (Path(tmp) / "page-0007.md").write_text(FILE_TREE[40:] + "\n右圖所列。非舉凡百科學而分類之也。。", encoding="utf-8")
        _, detail, m = rr.check({"type": "golden_page", "page": 6, "pages": [6, 7], "value": "_check_skip_between.md",
                                 "skipBetween": ["圖而解之如左", "右圖所列"]}, Path(tmp))
        check(f"skipBetween cuts a chart across a page break: {detail}", m["errors"], 0)
    finally:
        golden.unlink()

sys.exit(1 if failures else 0)
