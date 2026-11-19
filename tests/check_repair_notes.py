#!/usr/bin/env python3
"""Offline checks of N9 (scripts/_repair_notes.py, the notes stage of
repair_book.py): the numbers of a page's notes and their calls where the
book numbers its notes on from page to page.

- a page whose calls and notes 64 and 65 the page lacks, engine A reading
  ⑥④ and ⑥5 (circled glyphs, a circled glyph and a plain figure) and engine
  B the calls as superscripts: written ⑥④ ⑥⑤ at both, and finalize pairs
  them (the whole finish);
- the bench's page 77: engine B reads the notes 72-74 as \\textcircled{7},
  {8}, {9} (its \\t a tab) and the calls as {2}, {3}, {4}, engine A one call
  as ⑦⑧: 72-74 written at the notes and the calls;
- a note numbered 240 that engine A reads ⑳ and engine B
  $\\textcircled {240}$, its call engine A alone reads (⑳): ②④⓪ at both;
- left: calls engine A reads ⑫ and ⑬ where engine B reads 19 at both (a
  run neither engine backs the other on), and notes the page already holds;
  --no-notes switches the stage off;
- a page's one note the engines read 82 and 85 takes the number after the
  previous page's last note (issue #8, item 3);
- issue #8, items 2 and 5: a note at the page's foot with no number in engine
  A's footnote block is marked unpaired (B4) and numbered by its call, which
  engine A alone reads, and finalize pairs them; a note no call numbers stays
  marked, and finalize writes it a blockquote;
- issue #8, item 4: a call no engine read, given by the answerer's
  note-numbers reading by the characters before it, is written with its
  note and paired; without the answer the page is left;
- the bench's page 315: notes 193 and 194 at the foot, calls 194 and 195
  that both engines read 19 (a run that fits 193-194 as well), the answer
  pairing the calls with the notes in order (193, 194): left;
- the bench's page 327: the page holds ㉔ at a call where 240 is printed,
  engine A reads ⑳, the answer 240, the note ⑳ and \textcircled{240}: the
  held ㉔ written over, ②④⓪ at both.

No model, no GPU, no corpus: made-up books (tests/repair_fixture.py), each
page changed only where a case is set up.

Usage: python3 tests/check_repair_notes.py   (exits non-zero if any check fails)
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
import _footnotes  # noqa: E402

FAILURES: list[str] = []
TAB_CIRCLED = "\textcircled"   # engine B's \textcircled whose \t arrived as a tab


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


def spec_of() -> dict:
    spec = fx.make_spec()
    # Scan 14: notes 64 and 65 and their calls.
    spec[14]["items"][1:1] = [("line", "春山居士云⑥④甲乙丙丁"), ("line", "秋夜居士曰⑥⑤戊己庚辛"),
                              ("line", "⑥④〔明〕春風吹過"), ("line", "⑥⑤〔清〕小橋流水")]
    spec[14]["aEdits"] = [("⑥⑤〔清〕", "⑥5〔清〕")]
    spec[14]["bEdits"] = [("云⑥④", "云$ ^{64} $"), ("曰⑥⑤", "曰$ ^{65} $")]
    spec[14]["sealedEdits"] = [("云⑥④", "云"), ("曰⑥⑤", "曰"), ("⑥④〔明〕", "〔明〕"), ("⑥⑤〔清〕", "〔清〕")]
    # Scan 15: the bench's page 77.
    spec[15]["items"][1:1] = [("line", "能食諸虫毒⑦②其實被形容"), ("line", "今皆化為土著⑦③亦步入"),
                              ("line", "醞釀的過程⑦④所以明代"), ("line", "⑦②〔清〕鄭昌齡修"),
                              ("line", "⑦③〔明〕劉熙祚修"), ("line", "⑦④〔美〕梁肇庭著")]
    spec[15]["aEdits"] = [("⑦②〔清〕", "⑦2〔清〕"), ("⑦③〔明〕", "⑦3〔明〕"), ("過程⑦④", "過程⑦⑧")]
    spec[15]["bEdits"] = [("毒⑦②", f"毒$ {TAB_CIRCLED}{{2}} $"), ("著⑦③", f"著$ {TAB_CIRCLED}{{3}} $"),
                          ("程⑦④", f"程$ {TAB_CIRCLED}{{4}} $"), ("⑦②〔清〕", f"$ {TAB_CIRCLED}{{7}} $〔清〕"),
                          ("⑦③〔明〕", f"$ {TAB_CIRCLED}{{8}} $〔明〕"), ("⑦④〔美〕", f"$ {TAB_CIRCLED}{{9}} $〔美〕")]
    spec[15]["sealedEdits"] = [("毒⑦②", "毒"), ("著⑦③", "著"), ("程⑦④", "程"), ("⑦②〔清〕", "〔清〕"),
                               ("⑦③〔明〕", "〔明〕"), ("⑦④〔美〕", "〔美〕")]
    # Scan 16: a note numbered 240.
    spec[16]["items"][1:1] = [("line", "依據前人研究②④⓪依據前文"), ("line", "②④⓪《明清史料》甲編")]
    spec[16]["aEdits"] = [("究②④⓪", "究⑳"), ("②④⓪《", "⑳《")]
    spec[16]["bEdits"] = [("究②④⓪", "究"), ("②④⓪《", "$\\textcircled {240}$《")]
    spec[16]["sealedEdits"] = [("究②④⓪", "究"), ("②④⓪《", "《")]
    # Scan 17: calls 193 and 194 that engine A reads ⑫ and ⑬ and engine B 19.
    spec[17]["items"][1:1] = [("line", "分爭者也①⑨③崇禎帝"), ("line", "致垂政體①⑨④兩廣總督")]
    spec[17]["aEdits"] = [("也①⑨③", "也⑫"), ("體①⑨④", "體⑬")]
    spec[17]["bEdits"] = [("也①⑨③", "也$ ^{19} $"), ("體①⑨④", "體$ ^{19} $")]
    spec[17]["sealedEdits"] = [("也①⑨③", "也"), ("體①⑨④", "體")]
    # Scan 18: notes the page already holds.
    spec[18]["items"][1:1] = [("line", "甲乙丙丁⑧①戊己庚辛"), ("line", "⑧①〔明〕田園雜記")]
    # Scan 19: one note the engines read 82 and 85, its call 82 by both: the
    # number after scan 18's last note (81) settles it.
    spec[19]["items"][1:1] = [("line", "春山居士云⑧②甲乙丙丁"), ("line", "⑧②〔清〕秋夜雜記")]
    spec[19]["bEdits"] = [("云⑧②", "云$ ^{82} $"), ("⑧②〔清〕", "⑧⑤〔清〕")]
    spec[19]["sealedEdits"] = [("云⑧②", "云"), ("⑧②〔清〕", "〔清〕")]
    # Scan 20, issue #8: a note at the page's foot with no number in engine
    # A's footnote block, its call ① read by engine A alone; and on scan 13 a
    # note at the foot no call numbers.
    spec[20]["items"][1:1] = [("line", "甲乙丙丁①戊己庚辛")]
    spec[20]["bEdits"] = [("丁①戊", "丁戊")]
    spec[20]["sealedEdits"] = [("丁①戊", "丁戊")]
    # Scans 12 and 11, issue #8 item 4: a note 99 both engines read, whose call
    # neither engine read; on scan 12 the answerer's note-numbers reading
    # (the sealed record's noteNumbers) gives the call by the characters
    # before it, on scan 11 there is no answer.
    for scan in (12, 11):
        spec[scan]["items"][1:1] = [("line", "春山居士云⑨⑨甲乙丙丁"), ("line", "⑨⑨〔明〕田園雜記")]
        spec[scan]["aEdits"] = [("云⑨⑨", "云")]
        spec[scan]["bEdits"] = [("云⑨⑨", "云")]
        spec[scan]["sealedEdits"] = [("云⑨⑨", "云"), ("⑨⑨〔明〕", "〔明〕")]
    spec[12]["recordExtra"] = {"noteNumbers": {"reason": "made up", "parsed": True, "notes": [
        {"number": "99", "note": "〔明〕田園雜記", "before": "春山居士云"}]}}
    # Scan 9, the bench's page 315: the page's first note (193) is called on
    # the page before; its calls are 194 and 195, which both engines read 19.
    spec[9]["items"][1:1] = [("line", "分爭者也①⑨④崇禎帝更"), ("line", "致垂政體①⑨⑤兩廣總督"),
                             ("line", "①⑨③〔明〕陳玉輝鑑鬚集"), ("line", "①⑨④萬曆廣東通志")]
    spec[9]["aEdits"] = [("也①⑨④", "也⑲"), ("體①⑨⑤", "體⑲"), ("①⑨③〔明〕", "⑲〔明〕"), ("①⑨④萬曆", "⑲萬曆")]
    spec[9]["bEdits"] = [("也①⑨④", "也$ ^{19} $"), ("體①⑨⑤", "體$ ^{19} $"), ("①⑨③〔明〕", "〔明〕"),
                         ("①⑨④萬曆", "萬曆")]
    spec[9]["sealedEdits"] = [("也①⑨④", "也"), ("體①⑨⑤", "體"), ("①⑨③〔明〕", "〔明〕"), ("①⑨④萬曆", "萬曆")]
    spec[9]["recordExtra"] = {"noteNumbers": {"reason": "made up", "parsed": True, "notes": [
        {"number": "193", "note": "〔明〕陳玉輝鑑鬚集", "before": "分爭者也"},
        {"number": "194", "note": "萬曆廣東通志", "before": "致垂政體"}]}}
    # Scan 10, the bench's page 327: the page holds ㉔ where 240 is printed.
    spec[10]["items"][1:1] = [("line", "造營房一百間②④⓪依據前表"), ("line", "②④⓪《明清史料》辛編")]
    spec[10]["aEdits"] = [("間②④⓪", "間⑳"), ("②④⓪《", "⑳《")]
    spec[10]["bEdits"] = [("間②④⓪", "間"), ("②④⓪《", "$\\textcircled {240}$《")]
    spec[10]["sealedEdits"] = [("間②④⓪", "間㉔"), ("②④⓪《", "《")]
    spec[10]["recordExtra"] = {"noteNumbers": {"reason": "made up", "parsed": True, "notes": [
        {"number": "240", "note": "《明清史料》辛編", "before": "造營房一百間"}]}}
    for scan, text in ((20, "原文如此"), (13, "編者案此處原缺")):
        spec[scan].setdefault("extraSealed", []).append(text)
        spec[scan].setdefault("extraBlocks", []).append({"text": text, "boundingBox": fx.box_of(300, 900, 16, 200),
                                                         "blockLabel": "footnote", "blockOrder": -1})
    return spec


def main_checks(tmp: Path) -> None:
    status, pages, log = repair(tmp, spec_of(), "notes")
    check(status == 0, f"notes: the run exits 0 (exit {status})")
    p = pages[14]
    check("云⑥④甲" in p and "曰⑥⑤戊" in p and "⑥④〔明〕春風吹過" in p and "⑥⑤〔清〕小橋流水" in p
          and "N9" in rules(log, 14),
          f"N9: calls and notes 64 and 65 the page lacks are written as circled digits ({rules(log, 14)}, "
          f"{p[p.find('春山'):p.find('春山') + 60]!r})")
    text, report = _footnotes.convert(f"<!-- page_014 -->\n{p}")
    check("[^64]" in text and "[^64]: 〔明〕" in text and "[^65]: 〔清〕" in text,
          f"N9: finalize pairs the calls and notes N9 wrote ({text[text.find('春山'):text.find('春山') + 40]!r})")
    p = pages[15]
    check("毒⑦②其" in p and "著⑦③亦" in p and "程⑦④所" in p and "⑦②〔清〕" in p and "⑦③〔明〕" in p and "⑦④〔美〕" in p,
          f"N9 on the bench's page 77: 72-74 written at the calls and the notes, engine B's 7-9 and 2-4 and engine A's "
          f"⑦⑧ notwithstanding ({p[p.find('能食'):p.find('能食') + 50]!r})")
    p = pages[16]
    check("究②④⓪依" in p and "②④⓪《明清史料》" in p,
          f"N9: a note numbered 240 (engine B \\textcircled{{240}}, engine A ⑳) and its call written ②④⓪ "
          f"({p[p.find('依據'):p.find('依據') + 40]!r})")
    p = pages[17]
    check("N9" not in rules(log, 17) and "也崇" in p and "體兩" in p,
          f"N9: calls engine A reads ⑫ ⑬ and engine B 19 19 are left ({rules(log, 17)})")
    p = pages[18]
    check("N9" not in rules(log, 18) and p.count("⑧①") == 2,
          f"N9: notes and calls the page already holds are left ({rules(log, 18)})")
    p = pages[12]
    check("云⑨⑨甲" in p and "⑨⑨〔明〕田園雜記" in p and "N9" in rules(log, 12),
          f"issue #8 item 4: a call no engine read, given by the answerer's note-numbers reading, written with its "
          f"note ({rules(log, 12)}, {p[p.find('春山'):p.find('春山') + 40]!r})")
    text, _ = _footnotes.convert(f"<!-- page_012 -->\n{p}")
    check("云[^99]甲" in text and "[^99]: 〔明〕" in text, f"issue #8 item 4: finalize pairs them ({text[-50:]!r})")
    check("N9" not in rules(log, 11) and "云甲" in pages[11],
          f"without the answer, a note whose call no engine read is left: it would pair with nothing ({rules(log, 11)})")
    p = pages[9]
    check("N9" not in rules(log, 9) and "也崇" in p and "體兩" in p and "①⑨③" not in p,
          f"N9 on the bench's page 315: calls both engines read 19, which fit a run shifted by one as well, are left "
          f"though the answer pairs them with the notes in order ({rules(log, 9)}, {p[p.find('分爭'):p.find('分爭') + 40]!r})")
    p = pages[10]
    check("間②④⓪依" in p and "②④⓪《明清史料》" in p and "㉔" not in p and "N9" in rules(log, 10),
          f"N9 on the bench's page 327: the page's ㉔ (240 printed, the answer 240, engine A ⑳) is written over "
          f"({rules(log, 10)}, {p[p.find('造營'):p.find('造營') + 40]!r})")
    text, _ = _footnotes.convert(f"<!-- page_010 -->\n{p}")
    check("間[^240]依" in text and "[^240]: 《明清史料》" in text, f"page 327: finalize pairs them ({text[-50:]!r})")
    p = pages[19]
    check("云⑧②甲" in p and "⑧②〔清〕秋夜雜記" in p,
          f"N9: one note the engines read 82 and 85 takes the number after the previous page's last note (81) "
          f"({p[p.find('春山'):p.find('春山') + 40]!r})")
    p = pages[20]
    check("丁①戊" in p and "\n①原文如此" in p and "B4" in rules(log, 20) and "N9" in rules(log, 20),
          f"issue #8: a note with no number at the page's foot (engine A: footnote) is numbered by its call, read by "
          f"engine A alone ({rules(log, 20)}, {p[p.find('甲乙'):]!r})")
    text, _ = _footnotes.convert(f"<!-- page_020 -->\n{p}")
    check("丁[^1]戊" in text and "[^1]: 原文如此" in text, f"issue #8: finalize pairs them ({text[-60:]!r})")
    p = pages[13]
    check("[^unpaired-13-1]: 編者案此處原缺" in p and "B4" in rules(log, 13),
          f"B4: a note at the page's foot with no number that no call numbers stays marked unpaired ({p[-60:]!r})")
    import finish_book
    check(finish_book.UNPAIRED_NOTE_LINE.sub(r"\1> ", "正文。\n\n[^unpaired-13-1]: 編者案。\n") == "正文。\n\n> 編者案。\n",
          "finalize writes an unpaired note as a blockquote")
    status, pages, log = repair(tmp, spec_of(), "notes-off", "--no-notes")
    check(status == 0 and not any(r["rule"] == "N9" for r in log),
          f"--no-notes switches the stage off ({sorted({r['rule'] for r in log})})")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        main_checks(Path(directory))
    print("all note-number checks passed" if not FAILURES else f"FAIL: {len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
