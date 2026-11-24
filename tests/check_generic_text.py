#!/usr/bin/env python3
"""Offline check that the skill's own text stays generic: SKILL.md, references/
and the bundled scripts name no test case and quote no golden.

The skill runs on books it has never seen; the calibration books are examples,
and their facts live in tests/cases/ and tests/golden/ only
(references/developer-notes.md, 〈經驗泛化同技能更新門檻〉).  A comment that cites a measurement describes the failure's shape,
not the page's text.  Two things are checked, both mechanical:

- no case id of tests/cases/ (the file names and their bookId) appears;
- no run of QUOTE_LENGTH or more CJK characters of a golden (its CJK read in
  order, cells and lines run together, so a figure glued across two cells
  counts too) appears in a line of the text.

Book titles and page numbers written in prose are not checked: older comments
still carry some, and the check names only what it can find without a list.

Usage:
    python3 tests/check_generic_text.py
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U0003ffff〇]+")
# Six characters: at five, a line of the rules' own wording (據某館藏本影印, how a
# reprint's added title page is described) matched a golden's colophon; at six
# nothing did once the quotes of the corpus were rewritten.
QUOTE_LENGTH = 6


def skill_text() -> list[Path]:
    return ([ROOT / "SKILL.md"] + sorted((ROOT / "references").glob("*.md"))
            + sorted((ROOT / "scripts").rglob("*.py")))


def case_ids() -> set[str]:
    ids = set()
    for path in (ROOT / "tests" / "cases").glob("*.json"):
        ids.add(path.stem)
        book_id = json.loads(path.read_text(encoding="utf-8")).get("bookId")
        if book_id:
            ids.add(book_id)
    return ids


def golden_quotes(line: str, goldens: list[str]) -> list[str]:
    """The longest pieces, QUOTE_LENGTH or more CJK characters, of LINE's CJK
    runs that a golden holds."""
    found = []
    for run in HAN.findall(line):
        start = 0
        while start + QUOTE_LENGTH <= len(run):
            size = QUOTE_LENGTH
            if not any(run[start:start + size] in golden for golden in goldens):
                start += 1
                continue
            while start + size < len(run) and any(run[start:start + size + 1] in golden for golden in goldens):
                size += 1
            found.append(run[start:start + size])
            start += size
    return found


def main() -> int:
    ids = case_ids()
    assert len(ids) >= 4, f"tests/cases/ not found: {ids}"
    goldens = ["".join(HAN.findall(path.read_text(encoding="utf-8")))
               for path in (ROOT / "tests" / "golden").rglob("*") if path.suffix in (".md", ".html")]
    assert sum(map(len, goldens)) > 1000, "tests/golden/ not found"
    problems = []
    for path in skill_text():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{path.relative_to(ROOT)}:{number}"
            problems += [f"{where}: case id {case!r}" for case in sorted(ids)
                         if re.search(rf"(?<![\w-]){re.escape(case)}(?![\w-])", line)]
            problems += [f"{where}: quotes a golden: {quote}" for quote in golden_quotes(line, goldens)]
    for problem in problems:
        print("FAIL", problem)
    print(f"{'FAIL' if problems else 'ok  '} {len(skill_text())} files of the skill's text: "
          f"{len(problems)} case id(s) or golden quote(s) of {QUOTE_LENGTH}+ characters")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
