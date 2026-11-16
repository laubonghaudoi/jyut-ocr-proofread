#!/usr/bin/env python3
"""Offline checks of B1 (scripts/_repair_brackets.py, the brackets stage of
repair_book.py), P8 (scripts/_repair_joins.py, the paragraphs stage) and
S18 (scripts/_repair_structure.py, the levels stage):

- B1: the editors' brackets 〔〕 both engines read, where the page holds the
  pair as 「」 or holds none, are written 〔〕 on the page; a page holding a
  bracket of another shape on one side and none on the other is left, and
  so is a quote both engines read beside the 〔, or a bracket turned the
  wrong way; note markers ([^1]) beside the place stay whole, the 〔〕
  written inside them; --no-brackets switches the stage off;
- B1 on one engine's 〔〕: where the other engine reads [] or nothing there,
  the page's [] or （） is written 〔〕, and so is the pair the page lacks;
  left where the other engine reads （） or is not aligned there;
- B2: a round bracket beside the editors' one, after it or before it,
  which an engine reads there (the other reading round), goes; both stay
  where one engine reads both (its round one in ASCII too), and where no
  engine reads the editors' one;
- the whole repair with the runs stage on: B1 and B2 edit alike, and a page
  where N8 writes a bracket both engines read beside one B1 left is named
  by MARK-UNBALANCED (its 〔〕 counted);
- P8: a list item that opens with a note number (①, [^N]) loses its bullet
  and stands a blank line after the line before it (one blank line after a
  heading, P5's); a list item with a note number inside its text keeps its
  bullet; P6 leaves the note lines as they are; and two such lines either
  side of a page break are not joined (J);
- S18: on a page whose structure pass phase 3 refused, a plain line that is
  an engine A title block is a heading again; on another page it is left.

No model, no GPU, no corpus: made-up books (tests/repair_fixture.py), each
page changed only where a case is set up.

Usage: python3 tests/check_repair_brackets.py   (exits non-zero if any check fails)
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402
import repair_book  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def repair(root: Path, spec: dict, name: str, *argv: str) -> tuple[int, dict[int, str], list[dict]]:
    book = fx.write_book(root / name, spec)
    out = root / f"{name}-out"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        status = repair_book.main(["--workers", "1", str(book), "--output", str(out), *argv])
    pages = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in (out / "pages").glob("*.md")}
    log = [json.loads(line) for line in (out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines()]
    return status, pages, log


def rules(log: list[dict], page: int) -> list[str]:
    return [r["rule"] for r in log if r["page"] == page]


def bracket_checks(tmp: Path) -> None:
    spec = fx.make_spec()
    # Printed 〔〕 that both engines read; the sealed page holds 「」 (scan 8),
    # none (scan 10), and 「 alone with nothing after (scan 12).
    for scan, sealed in ((8, "春日「遊」記"), (10, "春日遊記"), (12, "春日「遊記")):
        spec[scan]["items"].insert(1, ("line", "春日〔遊〕記"))
        spec[scan].setdefault("sealedEdits", []).append(("春日〔遊〕記", sealed))
    # The page's note markers in both gaps (scan 6), a quote both engines
    # read before the 〔 (scan 9), a 〔 turned into the closing gap (scan 11).
    for scan, printed, sealed in ((6, "秋月①〔明〕②山", "秋月[^1]明[^2]山"), (9, "秋月「〔明〕山」", "秋月「明〕山」"),
                                  (11, "秋月〔明〕山", "秋月明〔山")):
        spec[scan]["items"].insert(1, ("line", printed))
        spec[scan].setdefault("sealedEdits", []).append((printed, sealed))
    # One engine reads 〔〕: the other reads [] or nothing, and the page holds
    # [] (scans 13, 14, 15: engine A reading [], the page （）), or nothing
    # (scan 17); the other reads （） (scan 16) or is not aligned (scan 18).
    for scan, a, b, sealed in ((13, "春日〔遊〕記", "春日[遊]記", "春日[遊]記"), (14, "春日[遊]記", "春日〔遊〕記", "春日（遊）記"),
                               (15, "春日〔遊〕記", "春日遊記", "春日[遊]記"), (16, "春日〔遊〕記", "春日（遊）記", "春日[遊]記"),
                               (17, "春日〔遊〕記", "春日[遊]記", "春日遊記"), (18, "春日〔遊〕記", "春目遊記", "春日[遊]記")):
        spec[scan]["items"].insert(1, ("line", "春日〔遊〕記"))
        spec[scan].setdefault("aEdits", []).append(("春日〔遊〕記", a))
        spec[scan].setdefault("bEdits", []).append(("春日〔遊〕記", b))
        spec[scan].setdefault("sealedEdits", []).append(("春日〔遊〕記", sealed))
    # B2: the page's round bracket beside the editors' one, which engine B
    # reads where engine A reads round, after it (scan 19) or before it
    # (scan 4); left where engine A reads both shapes, in full width or
    # ASCII (scans 20, 5), or no engine reads the editors' one (scan 7).
    for scan, printed, a, sealed in ((19, "秋月〔明〕山", "秋月（明）山", "秋月〔（明〕）山"),
                                     (4, "秋月〔明〕山", "秋月（明）山", "秋月（〔明）〕山"),
                                     (20, "秋月（〔明〕）山", "秋月（〔明〕）山", "秋月（〔明〕）山"),
                                     (5, "秋月（〔明〕）山", "秋月(〔明〕)山", "秋月（〔明〕）山"),
                                     (7, "秋月（明）山", "秋月（明）山", "秋月〔（明〕）山")):
        spec[scan]["items"].insert(1, ("line", printed))
        spec[scan].setdefault("aEdits", []).append((printed, a))
        spec[scan].setdefault("sealedEdits", []).append((printed, sealed))
    spec[5].setdefault("bEdits", []).append(("秋月（〔明〕）山", "秋月〔明〕山"))
    # The runs stage (N8) writes marks both engines read that B1 leaves: off.
    status, pages, log = repair(tmp, spec, "brackets", "--no-runs")
    check(status == 0, f"brackets: the run exits 0 (exit {status})")
    check("春日〔遊〕記" in pages[8] and rules(log, 8).count("B1") == 2,
          f"B1: the page's 「」 where both engines read 〔〕 becomes 〔〕 ({rules(log, 8)})")
    check("春日〔遊〕記" in pages[10] and rules(log, 10).count("B1") == 2,
          f"B1: 〔〕 both engines read that the page lacks is written ({rules(log, 10)})")
    check("春日「遊記" in pages[12] and "B1" not in rules(log, 12),
          f"B1: a bracket of another shape on one side and none on the other is left ({rules(log, 12)})")
    check(("秋月[^1]〔明〕[^2]山" in pages[6], "秋月「明〕山」" in pages[9], "秋月明〔山" in pages[11]) == (True, True, True),
          "B1: note markers stay whole with 〔〕 written inside them; a quote both engines read and a bracket turned "
          f"the wrong way are left ({[line for scan in (6, 9, 11) for line in pages[scan].splitlines() if '秋月' in line]})")
    for scan, want, by in ((13, "春日〔遊〕記", "A"), (14, "春日〔遊〕記", "B"), (15, "春日〔遊〕記", "A"),
                           (17, "春日〔遊〕記", "A")):
        check(want in pages[scan] and [r["evidence"].get("readBy") for r in log if r["page"] == scan] == [by, by],
              f"B1 on engine {by}'s 〔〕, the other engine reading a square bracket or nothing: {want} "
              f"({[line for line in pages[scan].splitlines() if '遊' in line]})")
    check("春日[遊]記" in pages[16] and "春日[遊]記" in pages[18] and not any(r["page"] in (16, 18) for r in log),
          "B1 leaves one engine's 〔〕 where the other reads a round bracket, or is not aligned there")
    check("秋月〔明〕山" in pages[19] and rules(log, 19) == ["B2", "B2"]
          and "秋月〔明〕山" in pages[4] and rules(log, 4) == ["B2", "B2"],
          f"B2: the round bracket beside the editors' one, which an engine reads there, goes, after it or before "
          f"it ({rules(log, 19)}, {rules(log, 4)})")
    check("秋月（〔明〕）山" in pages[20] and "秋月（〔明〕）山" in pages[5] and "秋月〔（明〕）山" in pages[7]
          and not rules(log, 20) and not rules(log, 5) and not rules(log, 7),
          "B2 leaves both brackets where one engine reads both (in ASCII too), and where no engine reads the "
          "editors' one")
    others = sorted({r["page"] for r in log if r["rule"] == "B1"} - {6, 8, 10, 13, 14, 15, 17})
    check(not others, f"B1: no other page is changed ({others})")
    # The whole repair, the runs stage on: B1 and B2 edit as above, and where
    # N8 then writes a bracket both engines read beside one B1 left (scans 11
    # and 12), the page's unpaired 〔 or 「 is named by MARK-UNBALANCED.
    status, pages_on, log_on = repair(tmp, spec, "brackets-runs")
    unbalanced = json.loads((tmp / "brackets-runs-out" / "lint.json").read_text(encoding="utf-8"))["after"]["hits"]
    named = sorted({hit["page"] for hit in unbalanced.get("MARK-UNBALANCED", [])})
    same = [[(r["page"], r["rule"], r["before"], r["after"]) for r in records if r["rule"] in ("B1", "B2")]
            for records in (log, log_on)]
    check(status == 0 and same[0] == same[1] and {11, 12} <= set(named),
          f"brackets with the runs stage on: B1 and B2 edit alike, and the pages N8 leaves unpaired are named "
          f"({[line for scan in (11, 12) for line in pages_on[scan].splitlines() if line.startswith(('秋月', '春日'))]}, "
          f"MARK-UNBALANCED {named})")
    status, pages, log = repair(tmp, spec, "brackets-off", "--no-brackets")
    check(status == 0 and "春日「遊」記" in pages[8] and not any(r["rule"] == "B1" for r in log),
          "--no-brackets: the stage is off and the page keeps its 「」")


def note_checks(tmp: Path) -> None:
    spec = fx.make_spec()
    for n, words in ((1, "①春風吹過"), (2, "②小橋流水")):
        spec[20]["items"].insert(n, ("line", f"- {words}"))
    spec[19]["items"].insert(1, ("line", "- 甲乙③丙丁"))
    spec[18]["items"].insert(1, ("heading", "田園雜記", 3))
    spec[18]["items"].insert(2, ("line", "- ④春風吹過"))
    # Two numbered lines with no full stop either side of a page break.
    spec[11]["items"].append(("line", "- ⑳春山居士"))
    spec[12]["items"].insert(0, ("line", "- ㉑秋夜居士"))
    status, pages, log = repair(tmp, spec, "notes")
    labels = json.loads((tmp / "notes-out" / "boundaries.json").read_text(encoding="utf-8"))["boundaries"]
    check(labels.get("12") != "join" and "⑳春山居士\n" in pages[11] and pages[12].lstrip().startswith("㉑秋夜居士"),
          f"J: a line opening with a note number is not joined to the next page's ({labels.get('12')})")
    check("### 田園雜記\n\n④春風吹過" in pages[18],
          f"P8 after a heading: one blank line ({pages[18][:40]!r})")
    check(status == 0, f"notes: the run exits 0 (exit {status})")
    check("\n\n①春風吹過\n\n②小橋流水" in pages[20] and rules(log, 20).count("P8") == 2,
          f"P8: list items opening with a note number lose their bullets, a blank line before each ({rules(log, 20)})")
    check("- 甲乙③丙丁" in pages[19] and "P8" not in rules(log, 19),
          f"P8: a list item with a note number inside its text keeps its bullet ({rules(log, 19)})")


def title_checks(tmp: Path) -> None:
    spec = fx.make_spec()
    # A title engine A reads as a title block, which the sealed page holds
    # as a plain line: on scan 8 the structure pass was refused, on scan 10
    # not.
    for scan in (8, 10):
        spec[scan]["items"].insert(1, ("heading", "山中小記", 3))
        spec[scan].setdefault("sealedEdits", []).append(("### 山中小記", "山中小記"))
    spec[8]["sealDoubts"] = [{"kind": "structure-rejected", "detail": "STRUCTURE PASS REJECTED: made up"}]
    status, pages, log = repair(tmp, spec, "titles")
    check(status == 0, f"titles: the run exits 0 (exit {status})")
    check(any(line.lstrip("#").strip() == "山中小記" and line.startswith("#") for line in pages[8].splitlines())
          and "S18" in rules(log, 8), f"S18: a title block written plain on a refused page is a heading ({rules(log, 8)})")
    check("\n山中小記\n" in pages[10] and "S18" not in rules(log, 10),
          f"S18: on a page whose structure pass stood, the line is left ({rules(log, 10)})")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        bracket_checks(Path(directory))
        note_checks(Path(directory))
        title_checks(Path(directory))
    print("all bracket, note-line and title checks passed" if not FAILURES else f"FAIL: {len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
