#!/usr/bin/env python3
"""Offline check of the book repair tool's paragraph and page-boundary rules
(scripts/_repair_joins.py) through scripts/repair_book.py: the paragraphs
inside a page (P6) and each page marker's label in boundaries.json (S,
T, H, L, J, P), which must pass finalize_page_markers.py's contract.

No model, no GPU, no corpus: made-up books (tests/repair_fixture.py), each
page changed only where a rule's case is set up.

Paragraphs:
- P6 two list items a blank line apart become a tight list.

Page boundaries, read on the text:
- S the first marker; T inside the contents, and never a join out of a
  contents page; H onto a unit's heading and onto a divider; J after a page
  whose body text does not end a sentence, also when the page ends with its
  foot notes (set aside: finalize puts them after the joined paragraph, a
  paragraph running on over three pages gets both pages' notes after it in
  printed order); P after a finished sentence, after a page that ends with
  a heading, after a sentence ended before a note number, after an
  unfinished body that a line set aside follows that opens with no note
  number (a join would splice it onto the next page's text), and after a
  body ending with a table row above its foot notes (J reads the line above
  the notes); never a join into a page that opens with a note number of
  any notation the finish writes as a footnote; L a list
  running on across a page; a list item after unfinished text is
  `paragraph`; after a join onto a list item, the blank line before the
  list's next item goes (P6).
- --pages with a page left out between two is `paragraph`.
- boundaries.json covers every marker and passes finalize's contract; the
  evidence file says each label's rule; no boundary is a doubt; a label the
  contract refuses is exit 4.

Every run replays its log to its output, and the paragraph rules run again
on their own output change nothing.

Usage:
    python3 tests/check_repair_boundaries.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
import _repair_edits as re_  # noqa: E402
import _repair_inputs as ri  # noqa: E402
import _repair_joins as rj  # noqa: E402
import repair_book  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


class Result:
    def __init__(self, status: int, out: Path, book: Path):
        self.status = status
        self.out = out
        self.report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
        self.log = [json.loads(line) for line in (out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
        self.pages = {int(p.stem.split("-")[1]): p.read_text(encoding="utf-8") for p in (out / "pages").glob("*.md")}
        self.labels = {int(k): v for k, v in json.loads((out / "boundaries.json").read_text(encoding="utf-8"))
                       ["boundaries"].items()} if (out / "boundaries.json").is_file() else {}
        self.evidence = json.loads((out / "boundary-evidence.json").read_text(encoding="utf-8"))["boundaries"] \
            if (out / "boundary-evidence.json").is_file() else {}

    def rules(self, page: int) -> list[str]:
        return [r["rule"] for r in self.log if r["page"] == page]

    def rule_of(self, page: int) -> str | None:
        return (self.evidence.get(str(page)) or {}).get("rule")


def repair(root: Path, spec: dict[int, dict[str, Any]], name: str, *argv: str) -> Result:
    book = fx.write_book(root / name, spec)
    out = root / f"{name}-out"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        status = repair_book.main(["--workers", "1", str(book), "--output", str(out), *argv])
    return Result(status, out, book)


def again(root: Path, name: str, result: Result) -> list[Any]:
    """The paragraph rules run on RESULT's own pages (with the model of the
    book): what they would change."""
    book = ri.load_book(ri.InputPaths.resolve(root / name, {}))
    model = json.loads((result.out / "book-model.json").read_text(encoding="utf-8"))
    order = list(book.assembly.order)
    ledger = re_.Ledger({scan: result.pages[scan] for scan in order})
    return list(rj.paragraphs(book, model, ledger.texts, order).edits)


def seal(spec: dict[int, dict[str, Any]], scan: int, old: str, new: str) -> None:
    spec[scan].setdefault("sealedEdits", []).append((old, new))


def prose(seed: int, size: int, final: bool) -> str:
    """SIZE made-up characters with a comma every seven; a full stop after
    the last when FINAL."""
    chars = fx.filler(seed, size)
    out = "".join(c + ("，" if i % 7 == 0 and i < size else "") for i, c in enumerate(chars, 1))
    return out + ("。" if final else "")


def end_page(spec: dict[int, dict[str, Any]], scan: int, final: bool) -> str:
    """SCAN's last paragraph, its text finished (FINAL) or running on."""
    items = spec[scan]["items"]
    k = max(i for i, item in enumerate(items) if item[0] == "para")
    text = prose(scan * 13, 60, final)
    items[k] = ("para", text, items[k][2])
    return text


# --- paragraphs -------------------------------------------------------------------

def paragraph_checks(tmp: Path) -> None:
    spec = fx.make_spec()
    # P6: two items of one list a blank line apart.
    for n, words in ((1, "春風吹過"), (2, "小橋流水")):
        spec[20]["items"].insert(n, ("line", f"{n}. {words}"))
    result = repair(tmp, spec, "paragraphs")
    pages = result.pages
    check(result.status == 0 and result.report["checks"]["logReplay"]["passed"] is True,
          f"paragraphs: the run exits 0 and its log replays to its output (exit {result.status})")
    check("1. 春風吹過\n2. 小橋流水" in pages[20] and "P6" in result.rules(20),
          f"P6: two items of one list a blank line apart become a tight list ({result.rules(20)})")
    others = sorted({r["page"] for r in result.log} - {20})
    check(not others, f"paragraphs: no other page is changed ({others})")
    rest = again(tmp, "paragraphs", result)
    check(not rest, f"paragraphs: the rules run again on their own output change nothing "
                    f"({[(e.page, e.rule) for e in rest]})")


# --- boundaries -------------------------------------------------------------------

def boundary_spec() -> dict[int, dict[str, Any]]:
    """Page ends laid out for each rule: J (15 -> 16, 16 -> 17), P after a
    finished sentence (9 -> 10), after a heading (7 -> 8), after a note
    number (19 -> 20) and with a note set aside after unfinished text
    (11 -> 12).  Scans 7, 9, 11, 13, 15 and 18 open with a unit's heading
    (H), 5 and 13 with a divider."""
    spec = fx.make_spec()
    spec[7]["items"].append(("heading", "一 總說", 2))
    end_page(spec, 15, final=False)
    end_page(spec, 16, final=False)
    end_page(spec, 9, final=True)
    last = end_page(spec, 19, final=True)
    seal(spec, 19, last, last + "①")
    # A note engine A read outside its reading order (a footnote block),
    # after an unfinished body.
    end_page(spec, 11, final=False)
    note = "①" + prose(1102, 12, True)
    spec[11]["extraSealed"] = [note]
    spec[11]["extraBlocks"] = [{"text": note, "boundingBox": fx.box_of(60, 900, 16, 200),
                                "blockLabel": "footnote", "blockOrder": -1}]
    return spec


def foot_note(spec: dict[int, dict[str, Any]], scan: int, text: str) -> None:
    """TEXT on SCAN, a block engine A read outside its reading order (a
    footnote's), sealed after the page's body."""
    spec[scan]["extraSealed"].append(text)
    spec[scan].setdefault("extraBlocks", []).append({"text": text, "boundingBox": fx.box_of(60, 900, 16, 200),
                                                     "blockLabel": "footnote", "blockOrder": -1})


def notes_spec() -> dict[int, dict[str, Any]]:
    """A paragraph running on over scans 15, 16 and 17, each of the first two
    ending with its foot notes; scan 9's unfinished body followed by a line
    set aside that opens with no note number; scan 11's body ending with a
    pipe-table row, a foot note under it."""
    spec = fx.make_spec()
    end_page(spec, 15, final=False)
    spec[16]["items"] = spec[16]["items"][-1:]          # one paragraph, running on
    end_page(spec, 16, final=False)
    foot_note(spec, 15, "①" + prose(1501, 12, True))
    foot_note(spec, 15, "②" + prose(1502, 12, True))
    foot_note(spec, 16, "①" + prose(1601, 12, True))
    end_page(spec, 9, final=False)
    foot_note(spec, 9, prose(901, 12, True))
    end_page(spec, 11, final=False)
    cells = fx.filler(1101, 2), fx.filler(1102, 2)
    spec[11]["extraSealed"].append(f"| {cells[0]} | {cells[1]} |")
    spec[11].setdefault("extraBlocks", []).append({"text": " ".join(cells), "boundingBox": fx.box_of(60, 800, 16, 200),
                                                   "blockLabel": "table"})
    foot_note(spec, 11, "①" + prose(1103, 12, True))
    return spec


def aside_spec() -> dict[int, dict[str, Any]]:
    """Scan 9's unfinished body followed by a line set aside that opens with
    no note number, in a block engine A labels text (no footnote)."""
    spec = fx.make_spec()
    end_page(spec, 9, final=False)
    text = prose(901, 12, True)
    spec[9]["extraSealed"].append(text)
    spec[9].setdefault("extraBlocks", []).append({"text": text, "boundingBox": fx.box_of(60, 900, 16, 200),
                                                  "blockLabel": "text", "blockOrder": -1})
    return spec


def list_spec() -> dict[int, dict[str, Any]]:
    """A list running on across scans 16 and 17."""
    spec = fx.make_spec()
    for n, words in ((1, "春風吹過"), (2, "小橋流水")):
        spec[16]["items"].append(("line", f"{n}. {words}"))
    spec[17]["items"].insert(0, ("line", "3. 人家門前"))
    return spec


def list_join_spec() -> dict[int, dict[str, Any]]:
    """A list item whose text runs on, unfinished, onto scan 17, where the
    list's next item follows it after a blank line (P6 across the join)."""
    spec = fx.make_spec()
    spec[16]["items"].append(("line", "1. 春風吹過"))
    spec[16]["items"].append(("line", "2. " + prose(1601, 79, False)))
    spec[17]["items"][0] = ("para", prose(1701, 20, True), True)
    spec[17]["items"].insert(1, ("line", "3. 人家門前"))
    return spec


def list_after_spec() -> dict[int, dict[str, Any]]:
    """A list opening scan 17 after unfinished text."""
    spec = fx.make_spec()
    end_page(spec, 16, final=False)
    spec[17]["items"].insert(0, ("line", "1. 春風吹過"))
    spec[17]["items"].insert(1, ("line", "2. 小橋流水"))
    return spec


def boundary_checks(tmp: Path) -> None:
    spec = boundary_spec()
    result = repair(tmp, spec, "boundaries")
    check(result.status == 0 and result.report["checks"]["boundaryContract"]["passed"] is True,
          f"boundaries.json passes finalize's contract (exit {result.status}, "
          f"{result.report['checks'].get('boundaryContract')})")
    markers = sorted(result.pages)
    check(sorted(result.labels) == markers, "boundaries.json has a label for every marker page")
    check(result.labels[1] == "start" and result.rule_of(1) == "S", "S: the first marker is `start`")
    check(result.rule_of(3) == "T" and result.labels[3] in ("paragraph", "list-new"),
          f"T: inside the contents ({result.labels[3]}, {result.rule_of(3)})")
    check(result.labels[4] == "paragraph" and result.evidence["4"]["previousFinished"] is False,
          f"T: a contents page whose last entry ends no sentence is not joined onto the next page "
          f"({result.labels[4]}, {result.rule_of(4)})")
    check(result.labels[7] == "structural" and result.rule_of(7) == "H" and result.labels[13] == "structural",
          f"H: onto a unit's heading, and onto a divider, is `structural` ({result.rule_of(7)}, {result.rule_of(13)})")
    check(result.labels[16] == "join" and result.rule_of(16) == "J" and result.labels[17] == "join",
          f"J: after a page whose text does not end a sentence, `join` ({result.labels[16]}, {result.rule_of(16)})")
    check(result.labels[10] == "paragraph" and result.rule_of(10) == "P",
          f"P: after a finished sentence, `paragraph` ({result.rule_of(10)})")
    check(result.labels[8] == "paragraph" and result.evidence["8"]["previousFinished"] is True,
          f"P: after a page that ends with a heading, `paragraph` ({result.labels[8]}, {result.rule_of(8)})")
    check(result.labels[20] == "paragraph" and result.evidence["20"]["previousFinished"] is True,
          f"P: a sentence ended before a note number is finished ({result.labels[20]}, "
          f"{result.evidence['20'].get('previousTail')})")
    check(result.labels[12] == "join" and result.rule_of(12) == "J" and result.evidence["12"]["setAside"] == 1
          and result.evidence["12"]["footNotes"] == 1,
          f"J: an unfinished body that a foot note engine A read outside its reading order follows is joined "
          f"({result.labels[12]}, {result.evidence['12']})")
    check(not any(d.get("stage") == "joins" for d in result.doubts),
          f"no boundary is a doubt ({[d['kind'] for d in result.doubts if d.get('stage') == 'joins']})")
    check(set(result.report["boundaries"]["rules"]) <= {"S", "T", "H", "L", "J", "P"},
          f"the report counts each label's rule ({result.report['boundaries']['rules']})")
    import _ocr_markdown as om
    combined = (result.out / "combined-repaired.md").read_text(encoding="utf-8")
    final = om.apply_boundary_contract(combined, result.labels)
    first = next(item[1] for item in spec[16]["items"] if item[0] == "para")
    check("<!-- page_" not in final and prose(15 * 13, 60, False) + first in final,
          "finalize applies the labels: the J join runs the sentence on across the page")
    note = spec[11]["extraSealed"][0]
    first = next(item[1] for item in spec[12]["items"] if item[0] == "para")
    check(prose(11 * 13, 60, False) + first + "\n\n" + note + "\n\n" in final,
          "finalize: the join runs the sentence on past the page's foot note, which follows the joined paragraph")

    check(all(rj.NOTE_HEAD.match(head) for head in ("¹ 上標嘅註。", "$^{1}$ 註。", "<sup>1</sup> 註。", "❶註。", "①註。")),
          "J: a next page that opens with a note number of any notation the finish writes as a footnote is never "
          "joined into (its first line would be a definition)")
    spec = notes_spec()
    result = repair(tmp, spec, "notes")
    check(result.status == 0 and [result.labels[k] for k in (16, 17)] == ["join", "join"]
          and [result.evidence[k].get("footNotes") for k in ("16", "17")] == [2, 1],
          f"J: a paragraph running on over three pages, each of the first two ending with its foot notes, is joined "
          f"at both breaks ({[result.labels[k] for k in (16, 17)]}, exit {result.status})")
    # Issue #8: the line set aside is a note engine A reads in a footnote
    # block that lost its number; B4 marks it an unpaired note, so the join
    # runs on the body's line above it and the note follows the paragraph.
    combined9 = (result.out / "combined-repaired.md").read_text(encoding="utf-8")
    check(result.labels[10] == "join" and result.rule_of(10) == "J" and result.evidence["10"].get("footNotes") == 1
          and "[^unpaired-9-1]: " in combined9,
          f"J: an unfinished body that a note at the page's foot with no number follows (engine A: a footnote block) "
          f"is joined past the note, which B4 marks unpaired ({result.labels[10]}, {result.evidence['10']})")
    final9 = om.apply_boundary_contract(combined9, result.labels)
    body9 = prose(9 * 13, 60, False)
    first10 = next(item[1] for item in spec[10]["items"] if item[0] == "para")
    check(body9 + first10 in final9 and final9.index("[^unpaired-9-1]: ") > final9.index(body9 + first10),
          "finalize: the sentence runs on across the page and the unpaired note follows the joined paragraph")
    aside = repair(tmp, aside_spec(), "aside")
    check(aside.labels[10] == "paragraph" and aside.rule_of(10) == "P" and aside.evidence["10"]["setAside"] == 1
          and "footNotes" not in aside.evidence["10"],
          f"P: an unfinished body that a line set aside follows, which opens with no note number and that engine A "
          f"reads as no footnote, is not joined ({aside.labels[10]}, {aside.evidence['10']})")
    check(result.status == 0 and result.labels[12] == "paragraph" and result.rule_of(12) == "P"
          and result.evidence["12"].get("footNotes") == 1 and result.evidence["12"]["kinds"][0] == "table",
          f"P: a body ending with a table row above its foot notes is read by that row, not the note: no join into "
          f"the table ({result.labels[12]}, {result.evidence['12']}, exit {result.status})")
    final = om.apply_boundary_contract((result.out / "combined-repaired.md").read_text(encoding="utf-8"),
                                       result.labels)
    notes = spec[15]["extraSealed"] + spec[16]["extraSealed"]
    first = next(item[1] for item in spec[17]["items"] if item[0] == "para")
    joined = prose(15 * 13, 60, False) + prose(16 * 13, 60, False) + first
    check(joined + "\n\n" + "\n\n".join(notes) + "\n\n" in final and all(final.count(n) == 1 for n in notes),
          "finalize: both pages' foot notes follow the paragraph the joins run on over three pages, once each, in "
          "printed order")

    result = repair(tmp, list_spec(), "list")
    check(result.labels[17] == "list-new" and result.rule_of(17) == "L" and result.status == 0,
          f"L: a list running on across the page is `list-new` ({result.labels.get(17)}, {result.rule_of(17)})")

    result = repair(tmp, list_join_spec(), "list-join")
    check(result.labels[17] == "join" and "3. 人家門前" in result.pages[17]
          and "。\n3. 人家門前" in result.pages[17] and "P6" in result.rules(17),
          f"P6 across a join: the list's next item follows the item the join finishes without a blank line "
          f"({result.labels[17]}, {result.rule_of(17)}, {result.rules(17)})")

    result = repair(tmp, list_after_spec(), "list-after")
    check(result.labels[17] == "paragraph" and result.rule_of(17) == "P" and result.status == 0,
          f"P: a list item after unfinished text is `paragraph` ({result.labels[17]}, {result.rule_of(17)})")

    result = repair(tmp, fx.make_spec(), "subset", "--pages", "5-7,9-10")
    check(result.labels.get(5) == "start" and result.labels.get(9) == "paragraph" and result.rule_of(9) == "gap",
          f"--pages: the first page is `start`, a page after a left-out one `paragraph` ({result.labels})")

    # A label the contract refuses (a join into a list item): exit 4.
    real = rj.joins

    def refused(*args, **kwargs):
        plan, labels, evidence = real(*args, **kwargs)
        labels[17] = "list-cont"
        return plan, labels, evidence
    rj.joins = refused
    try:
        result = repair(tmp, list_spec(), "refused")
    finally:
        rj.joins = real
    check(result.status == 4 and result.report["checks"]["boundaryContract"]["passed"] is False,
          f"a label finalize's contract refuses is exit 4 ({result.status})")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        paragraph_checks(Path(tmp))
        boundary_checks(Path(tmp))
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_boundaries: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
