#!/usr/bin/env python3
"""Offline check of the book repair tool's offset edits, repair log, doubts
and log replay (scripts/_repair_edits.py, scripts/repair_book.py).

No model, no GPU, no corpus: made-up page texts and a made-up book
(tests/repair_fixture.py), with made-up stages put in the tool's stage list.

- Edits of one stage apply against the text the stage saw: each record's
  offset is into the page as it stands just before that record, so replaying
  the log on the sealed pages gives the repaired pages; an insertion at a
  span's edge goes before it; an edit that changes nothing is not logged.
- Two edits over the same text: the first proposed is applied, the other is
  a `conflict` doubt naming both; two insertions at one place conflict; an
  insertion strictly inside another span conflicts; touching spans do not.
- An edit names its rule (ValueError otherwise), and record_problems()
  names a record that lacks a field or its rule.
- A replay whose record does not hold at its offset raises ReplayError.
- Through repair_book.py: a made-up furniture stage's edits reach
  pages/*.md and combined-repaired.md, the log replays to them byte for
  byte, and the report counts the rule; a doubt the stage proposes is
  written with its stage; --resume removes an earlier run's pages, log and
  repaired book first, and the questions and answers an earlier version
  wrote, so a resumed subset never sits beside them; --no-furniture turns
  the stage off; a log written wrong (a shifted offset) fails the run with
  exit 5; a failed gate of the book model is a `gate-failed` doubt and a
  folio jump a `leaves-missing` one.

Usage:
    python3 tests/check_repair_log_replay.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
from jyut_ocr.repair import edits as re_  # noqa: E402
import repair_book  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def raises(kind: type, call) -> bool:
    try:
        call()
    except kind:
        return True
    return False


def ledger_checks() -> None:
    pages = {1: "甲乙丙丁戊\n己庚辛\n", 2: "子丑寅卯"}
    ledger = re_.Ledger(pages)
    records = ledger.apply("made-up", [
        re_.Edit(1, 6, 10, "", rule="R-line"),            # delete 己庚辛\n
        re_.Edit(1, 1, 2, "乙乙", rule="R-grow"),          # 乙 -> 乙乙
        re_.Edit(1, 0, 0, "《", rule="R-open"),            # insert at the start
        re_.Edit(1, 1, 1, "·", rule="R-edge"),             # insertion at 乙's edge, before it
        re_.Edit(2, 1, 3, "丑寅", rule="R-noop"),          # changes nothing
        re_.Edit(2, 3, 4, "", rule="R-tail"),
    ])
    check(ledger.texts[1] == "《甲·乙乙丙丁戊\n" and ledger.texts[2] == "子丑寅",
          f"a stage's edits apply against the text it saw (got {ledger.texts[1]!r}, {ledger.texts[2]!r})")
    check([r["rule"] for r in records] == ["R-open", "R-edge", "R-grow", "R-line", "R-tail"],
          "records are in page order, left to right; an insertion at a span's edge goes first; "
          "an edit that changes nothing is not logged")
    check(re_.replay(pages, ledger.records) == ledger.texts,
          "replaying the log on the original pages gives the repaired pages")
    check(records[3]["line"] == 2 and records[3]["before"] == "己庚辛\n",
          "a record names the line its offset falls on and the text it replaced")

    ledger = re_.Ledger({1: "甲乙丙丁戊己"})
    ledger.apply("made-up", [
        re_.Edit(1, 1, 4, "", rule="R-first"),
        re_.Edit(1, 3, 5, "X", rule="R-overlap"),          # shares 丁
        re_.Edit(1, 2, 2, "Y", rule="R-inside"),            # insertion strictly inside 1:4
        re_.Edit(1, 5, 5, "Z", rule="R-insert-a"),
        re_.Edit(1, 5, 5, "W", rule="R-insert-b"),          # a second insertion at one place
        re_.Edit(1, 4, 4, "V", rule="R-touch"),             # at the first span's edge: no conflict
    ])
    kinds = [d["kind"] for d in ledger.doubts]
    refused = sorted(d["refused"]["rule"] for d in ledger.doubts)
    check(ledger.texts[1] == "甲V戊Z己" and kinds == ["conflict"] * 3
          and refused == ["R-insert-b", "R-inside", "R-overlap"],
          f"overlapping edits: the first proposed wins, each other is a `conflict` doubt (got "
          f"{ledger.texts[1]!r}, {refused})")
    check(all(d["applied"]["rule"] in ("R-first", "R-insert-a") for d in ledger.doubts),
          "a conflict doubt names the edit that was applied")

    check(raises(ValueError, lambda: re_.Edit(1, 0, 1, "")), "an edit with no rule is refused")
    ledger = re_.Ledger({1: "甲乙"})
    ledger.apply("made-up", [re_.Edit(1, 0, 1, "", rule="R-cut")])
    check(not re_.record_problems(ledger.records[0]), "a complete record has no problems")
    check(any("rule" in p for p in re_.record_problems(dict(ledger.records[0], rule=None))),
          "record_problems names a record without its rule")
    check(any("evidence" in p for p in re_.record_problems({k: v for k, v in ledger.records[0].items()
                                                            if k != "evidence"})),
          "record_problems names a record without a field")
    check(raises(ValueError, lambda: ledger.doubt("made-up-kind", 1, "x")), "an unknown doubt kind is refused")
    check(raises(re_.ReplayError, lambda: re_.replay({1: "甲乙"}, [dict(ledger.records[0], offset=1)])),
          "a record that does not hold at its offset fails the replay")


def run(*argv: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return repair_book.main(["--workers", "1", *argv])


def made_up_furniture(context: repair_book.Context) -> repair_book.StageResult:
    """Delete an extra line 'HEAD LINE' wherever a page holds one, and
    propose one doubt."""
    result = repair_book.StageResult()
    for page, text in context.ledger.texts.items():
        at = text.find(fx.HEAD + "\n")
        if at >= 0:
            result.edits.append(re_.Edit(page, at, at + len(fx.HEAD) + 1, "", rule="F-made-up",
                                         evidence={"line": fx.HEAD}))
    result.doubts.append(("MARK-STANDIN", 7, "made-up", {"rule": "F-made-up"}))
    return result


def with_stage(run_stage):
    stages = tuple(repair_book.Stage(s.name, s.switch, run_stage if s.name == "furniture" else s.run)
                   for s in repair_book.STAGES)
    old = repair_book.STAGES
    repair_book.STAGES = stages
    return old


def tool_checks() -> None:
    spec = fx.make_spec()
    for scan in (6, 7, 9):
        spec[scan]["extraSealed"] = [fx.HEAD]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        book = fx.write_book(tmp / "book", spec)
        old = with_stage(made_up_furniture)
        try:
            out = tmp / "out"
            status = run(str(book), "--output", str(out))
            report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
            log = [json.loads(line) for line in (out / "repair-log.jsonl").read_text(encoding="utf-8").splitlines()]
            page6 = (out / "pages" / "page-0006.md").read_text(encoding="utf-8")
            combined = (out / "combined-repaired.md").read_text(encoding="utf-8")
            check(status == 0 and len(log) == 3 and fx.HEAD + "\n" not in page6
                  and combined.count(fx.HEAD + "\n") == 0,
                  f"a stage's edits reach pages/ and the repaired book (exit {status}, {len(log)} records)")
            check(report["checks"]["logReplay"]["passed"] is True and report["counts"]["rules"] == {"F-made-up": 3},
                  "the log replays to the written pages and book byte for byte; the report counts the rule")
            check(all(not re_.record_problems(r) for r in log) and all(r["evidence"] for r in log),
                  "every record names page, before, after, rule and evidence")
            doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
            check(any(d["kind"] == "MARK-STANDIN" and d["page"] == 7 and d.get("stage") == "furniture"
                      and d.get("rule") == "F-made-up" for d in doubts),
                  "a doubt the stage proposes is written with its stage")
            check(any(d["kind"] == "leaves-missing" and d["page"] == 17 for d in doubts),
                  "the book's folio jump is a `leaves-missing` doubt")

            # --resume on that folder, holding an earlier version's questions
            # and answers too: a subset run with the stage off; the folder
            # holds only the last run's outputs.
            for name in ("questions.json", "answers.jsonl"):
                (out / name).write_text("[]\n", encoding="utf-8")
            status = run(str(book), "--output", str(out), "--resume", "--pages", "6-8", "--no-furniture")
            pages = sorted(p.name for p in (out / "pages").glob("page-*.md"))
            log = (out / "repair-log.jsonl").read_text(encoding="utf-8")
            same = all((out / "pages" / name).read_bytes() == (book / "proofread" / name).read_bytes()
                       for name in pages)
            report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
            names = sorted(p.name for p in out.iterdir())
            check(status == 0 and pages == ["page-0006.md", "page-0007.md", "page-0008.md"] and not log and same
                  and report["status"] == "finished" and report["resume"]["earlierOutputsRemoved"] >= 20
                  and "questions.json" not in names and "answers.jsonl" not in names,
                  f"--resume removes the earlier run's pages and log, and an earlier version's questions and "
                  f"answers: the folder holds the subset's pages, each what its (empty) log replays to "
                  f"({pages}, {names}, exit {status})")

            off = tmp / "off"
            status = run(str(book), "--output", str(off), "--no-furniture")
            report = json.loads((off / "repair-report.json").read_text(encoding="utf-8"))
            check(status == 0 and report["stages"]["furniture"]["status"] == "off (--no-furniture)"
                  and report["counts"]["edits"] == 0, "--no-furniture turns the stage off")

            real = re_.log_bytes

            def shifted(records):
                return real([dict(r, offset=r["offset"] + 1) for r in records])

            repair_book.re_.log_bytes = shifted
            try:
                wrong = tmp / "wrong"
                status = run(str(book), "--output", str(wrong))
            finally:
                repair_book.re_.log_bytes = real
            report = json.loads((wrong / "repair-report.json").read_text(encoding="utf-8"))
            check(status == 5 and report["checks"]["logReplay"]["passed"] is False,
                  f"a log that does not replay to the written text fails the run with exit 5 (got {status})")
        finally:
            repair_book.STAGES = old

        book = fx.write_book(tmp / "no-notation", fx.make_spec(), profile={"layout": {"runningHeads": [fx.HEAD]}})
        out = tmp / "no-notation-out"
        status = run(str(book), "--output", str(out))
        doubts = json.loads((out / "doubts.json").read_text(encoding="utf-8"))
        check(status == 0 and any(d["kind"] == "gate-failed" and d.get("gate") == "M-10" for d in doubts),
              "a book whose profile names no marks fails gate M-10, a `gate-failed` doubt")


def main() -> int:
    ledger_checks()
    tool_checks()
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_log_replay: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
