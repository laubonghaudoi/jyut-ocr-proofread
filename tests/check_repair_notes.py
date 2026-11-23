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
    # Resolved B4 note-unpaired doubt is pruned from doubts.json while unresolved stays.
    main_doubts = json.loads((tmp / "notes-out" / "doubts.json").read_text(encoding="utf-8"))
    unpaired_pages = {d["page"] for d in main_doubts if d["kind"] == "note-unpaired"}
    check(unpaired_pages == {13}, f"resolved B4 note-unpaired doubt pruned for scan 20, kept for scan 13 ({unpaired_pages})")


def more_checks(tmp: Path) -> None:
    """Issue #8 fixes: mangled held markers, bracketed superscripts/note heads,
    punctuation/line-break call insert fallbacks, and spurious mid-page note heads."""
    spec = fx.make_spec()
    # Scan 14: mangled held markers ㊱/㊷ where 66/67 belong (s0075).
    spec[14]["items"][1:1] = [
        ("line", "春山居士云⑥④甲乙丙丁"), ("line", "秋夜居士曰⑥⑤戊己庚辛"),
        ("line", "民的蹤跡⑥⑥春風吹過"), ("line", "民的說法⑥⑦小橋流水"),
        ("line", "⑥④〔明〕春風吹過"), ("line", "⑥⑤〔清〕小橋流水"),
        ("line", "⑥⑥萬曆湖廣總志"), ("line", "⑥⑦羅香林源流考"),
    ]
    spec[14]["sealedEdits"] = [("蹤跡⑥⑥", "蹤跡㊱"), ("說法⑥⑦", "說法㊷")]
    # Scan 15: mangled held markers ㉗/㉘ where 72/73 belong (s0077).
    spec[15]["items"][1:1] = [
        ("line", "諸虫毒⑦②其實被形容"), ("line", "化為土著⑦③亦步入"), ("line", "醞釀過程⑦④所以明代"),
        ("line", "⑦②〔清〕鄭昌齡修"), ("line", "⑦③〔明〕劉熙祚修"), ("line", "⑦④〔美〕梁肇庭著"),
    ]
    spec[15]["bEdits"] = [("毒⑦②", "毒$ ^{72} $"), ("著⑦③", "著$ ^{73} $"), ("程⑦④", "程$ ^{74} $")]
    spec[15]["sealedEdits"] = [("毒⑦②", "毒㉗"), ("著⑦③", "著㉘")]
    # Scan 16: bracketed superscripts & note heads + B4 empty 〔〕 cleanup (MH1582).
    spec[16]["items"][1:1] = [
        ("line", "根本計劃①甲乙丙丁"), ("line", "主持大計②戊己庚辛"), ("line", "爭取支持③壬癸子丑"),
        ("line", "①〔1〕胡漢民自傳"), ("line", "②〔2〕張難先知錄"),
    ]
    spec[16]["aEdits"] = [("劃①甲", "劃$ ^{[1]} $甲"), ("計②戊", "計$ ^{(2)} $戊"), ("持③壬", "持<sup>[3]</sup>壬"),
                          ("①〔1〕胡漢民", "〔1〕胡漢民"), ("②〔2〕張難先", "〔2〕張難先")]
    spec[16]["bEdits"] = [("劃①甲", "劃$ ^{(1)} $甲"), ("計②戊", "計$ ^{(2)} $戊"), ("持③壬", "持$ ^{(3)} $壬"),
                          ("①〔1〕胡漢民", "〔1〕胡漢民"), ("②〔2〕張難先", "〔2〕張難先")]
    spec[16]["sealedEdits"] = [("劃①甲", "劃甲"), ("計②戊", "計戊"), ("持③壬", "持壬"),
                               ("①〔1〕胡漢民", "〔1〕胡漢民"), ("②〔2〕張難先", "〔2〕張難先")]
    spec[16]["extraSealed"].append("〔〕孫中山全集")
    spec[16]["extraBlocks"] = [{"text": "〔3〕孫中山全集", "boundingBox": fx.box_of(300, 900, 16, 200),
                                "blockLabel": "footnote", "blockOrder": -1}]
    # Scan 17: punctuation mismatch (`）。` vs `。`) and line-break mismatch on calls (MH1656).
    spec[17]["items"][1:1] = [
        ("line", "居「住過」①。本書編者"), ("line", "可觀的利益②。正因如此"),
        ("line", "①〔蘇〕哈巴羅夫呈文"), ("line", "②〔俄〕別斯克羅内夫"),
    ]
    spec[17]["bEdits"] = [("過」①口", "過」①\n口"), ("益②口", "益$ ^{2} $口\n")]
    spec[17]["aEdits"] = [("益②。", "益$ ^{2} $。\n")]
    spec[17]["sealedEdits"] = [("過」①。", "過」）。"), ("益②。", "益。」"),
                               ("①〔蘇〕", "〔蘇〕"), ("②〔俄〕", "〔俄〕")]
    # Scan 18: spurious mid-page circled header filtered by _foot_notes_only (MH0651 p58).
    spec[18]["items"][1:1] = [
        ("line", "裹足不前①。市場情況"),
        ("line", "生計路絀②。因此傳統"),
        ("line", "第一章戰略分析"),
    ]
    spec[18]["aEdits"] = [("第一章戰略分析", "①第一章戰略分析"), ("前①。", "前$ ^{1} $。"), ("絀②。", "絀$ ^{2} $。")]
    spec[18]["bEdits"] = [("前①口", "前$ ^{1} $口"), ("絀②口", "絀$ ^{2} $口")]
    spec[18]["sealedEdits"] = [("前①。", "前。"), ("絀②。", "絀。")]
    spec[18]["extraSealed"] = ["彭澤益財政經濟", "馮桂芬顯志堂稿"]
    spec[18]["extraBlocks"] = [
        {"text": "①彭澤益財政經濟", "boundingBox": fx.box_of(300, 880, 16, 200), "blockLabel": "footnote", "blockOrder": -1},
        {"text": "②馮桂芬顯志堂稿", "boundingBox": fx.box_of(300, 910, 16, 200), "blockLabel": "footnote", "blockOrder": -1},
    ]

    status, pages, log = repair(tmp, spec, "more")
    check(status == 0, f"more checks exit 0 (exit {status})")
    p14 = pages[14]
    check("蹤跡⑥⑥春" in p14 and "說法⑥⑦小" in p14 and "㊱" not in p14 and "㊷" not in p14,
          f"mangled ㊱/㊷ overwritten with ⑥⑥/⑥⑦ ({p14[p14.find('蹤跡'):p14.find('蹤跡')+30]!r})")
    t14, r14 = _footnotes.convert(f"<!-- page_014 -->\n{p14}")
    check(r14["footnotes"] == 4, f"s0075 converts all 4 footnotes ({r14})")
    p15 = pages[15]
    check("毒⑦②其" in p15 and "著⑦③亦" in p15 and "㉗" not in p15 and "㉘" not in p15,
          f"mangled ㉗/㉘ overwritten with ⑦②/⑦③ ({p15[:80]!r})")
    t15, r15 = _footnotes.convert(f"<!-- page_015 -->\n{p15}")
    check(r15["footnotes"] == 3, f"s0077 converts all 3 footnotes ({r15})")
    p16 = pages[16]
    check("計劃①甲" in p16 and "大計②戊" in p16 and "支持③壬" in p16
          and "\n①胡漢民自傳" in p16 and "\n②張難先知錄" in p16 and "\n③孫中山全集" in p16
          and "〔〕" not in p16,
          f"MH1582 bracketed superscripts and note heads restored cleanly ({p16[-120:]!r})")
    t16, r16 = _footnotes.convert(f"<!-- page_016 -->\n{p16}")
    check(r16["footnotes"] == 3, f"MH1582 converts all 3 footnotes ({r16})")
    more_doubts = json.loads((tmp / "more-out" / "doubts.json").read_text(encoding="utf-8"))
    check(16 not in {d["page"] for d in more_doubts if d["kind"] == "note-unpaired"},
          "MH1582 scan 16 resolved note-unpaired doubt pruned")
    p17 = pages[17]
    check("居「住過」）①。" in p17 and "益②。」" in p17,
          f"MH1656 call_insert fallback places calls beside mismatched punctuation ({p17[:100]!r})")
    t17, r17 = _footnotes.convert(f"<!-- page_017 -->\n{p17}")
    check(r17["footnotes"] == 2, f"MH1656 converts both footnotes ({r17})")
    p18 = pages[18]
    check("①彭澤益財政經濟" in p18 and "②馮桂芬顯志堂稿" in p18 and "第一章戰略分析" in p18,
          f"MH0651 _foot_notes_only ignores spurious mid-page circled line ({p18[-80:]!r})")
    t18, r18 = _footnotes.convert(f"<!-- page_018 -->\n{p18}")
    check(r18["footnotes"] == 2, f"MH0651 converts both foot notes ({r18})")


def stitched_checks(tmp: Path) -> None:
    """Stitched multi-page table span (doukeoi-zingkeoi/s0315-0318-table geometry):
    scans 6..9 hold body calls 194..197 + 8 stitched table calls 198..205 on
    scan 6 and 13 notes 193..205 across scans 6..9 (with orphan 193)."""
    spec = fx.make_spec()
    for scan in (7, 8, 9):
        spec[scan]["items"] = []
    # Scan 6: 4 body prose calls (194..197) + 4 notes (193..196) + 8 stitched table calls (198..205).
    spec[6]["items"][1:1] = [
        ("line", "非兩藩之利也①⑨④兩廣總督"),
        ("line", "支持吉安行廣鹽①⑨⑤天啟五年"),
        ("line", "以免分爭者也①⑨⑥崇禎帝"),
        ("line", "致垂政體①⑨⑦兩淮鹽課"),
    ]
    spec[6]["aEdits"] = [("也①⑨④兩", "也⑲兩"), ("鹽①⑨⑤天", "鹽⑲天"), ("也①⑨⑥崇", "也⑲崇"), ("體①⑨⑦兩", "體⑲兩")]
    spec[6]["bEdits"] = [("也①⑨④兩", "也$ ^{19} $兩"), ("鹽①⑨⑤天", "鹽$ ^{19} $天"),
                         ("也①⑨⑥崇", "也$ ^{19} $崇"), ("體①⑨⑦兩", "體$ ^{19} $兩")]
    spec[6]["sealedEdits"] = [("也①⑨④兩", "也兩"), ("鹽①⑨⑤天", "鹽天"), ("也①⑨⑥崇", "也崇"), ("體①⑨⑦兩", "體兩")]
    table_lines = [
        "| 年份 | 歲收 | 出處 |",
        "| --- | --- | --- |",
        "| 正德六年 | 10671.66兩⑲ | 王陽明集 |",
        "| 正德十二年 | 6395.65兩①⑨ | 王陽明集 |",
        "| 嘉靖五年 | 24000兩②⑩ | 明世宗錄 |",
        "| 嘉靖十五年 | 26958.08兩②⑩ | 明世宗錄 |",
        "| 嘉靖三十六年 | 15000兩⑩ | 重修虔臺 |",
        "| 萬曆十四年 | 20000兩²⁰³ | 明神宗錄 |",
        "| 萬曆二十一年 | 26666.66兩²⁰⁴ | 重修虔臺 |",
        "| 天啟五年 | 12500兩²⁰⁵ | 明熹宗錄 |",
    ]
    s6_notes = [
        ("陳玉輝上報鹽議", "⑲陳玉輝上報鹽議"),
        ("萬曆廣東通志鹽課條", "萬曆廣東通志鹽課條"),
        ("吳文華與畢松坡疏", "吳文華與畢松坡疏"),
        ("明熹宗實錄卷五十六", "⑩明熹宗實錄卷五十六"),
    ]
    spec[6]["extraSealed"] = table_lines + [s for s, _ in s6_notes]
    spec[6]["extraBlocks"] = [
        {"text": a, "boundingBox": fx.box_of(300, 880 + 25 * k, 16, 200), "blockLabel": "footnote", "blockOrder": -1}
        for k, (_, a) in enumerate(s6_notes)
    ]

    # Scans 7..9: continuation note items (note #205 has engine B $ ^{20)} $ reading, #204 has ②04 exact).
    # Scan 7: 2 notes (197..198); Scan 8: 1 note (199); Scan 9: 6 notes (200..205).
    spec[7]["items"] = [("line", "畢自嚴題覆吉安疏。"), ("line", "正德六年贛關抽分廠。")]
    spec[7]["aEdits"] = [("畢自嚴題覆吉安疏。", "⑲畢自嚴題覆吉安疏。"), ("正德六年贛關抽分廠。", "⑲正德六年贛關抽分廠。")]
    spec[7]["bEdits"] = [("畢自嚴題覆吉安疏口", "$ ^{19} $畢自嚴題覆吉安疏。"), ("正德六年贛關抽分廠口", "$ ^{20)} $正德六年贛關抽分廠。")]
    spec[8]["items"] = [("line", "正德十二年抽過商稅。")]
    spec[8]["aEdits"] = [("正德十二年抽過商稅。", "⑲正德十二年抽過商稅。")]
    spec[8]["bEdits"] = [("正德十二年抽過商稅口", "$ ^{20)} $正德十二年抽過商稅。")]
    s9_notes = [
        ("萬曆江西省大志賦書。", "②萬曆江西省大志賦書。", "萬曆江西省大志賦書口", "$ ^{20)} $萬曆江西省大志賦書。"),
        ("嘉靖十六年共收鹽稅。", "②0嘉靖十六年共收鹽稅。", "嘉靖十六年共收鹽稅口", "$ ^{20)} $嘉靖十六年共收鹽稅。"),
        ("嘉靖三十六年不及數。", "②2021 嘉靖三十六年不及數。", "嘉靖三十六年不及數口", "$ ^{20)} $嘉靖三十六年不及數。"),
        ("天啟重修虔臺志事紀。", "②3天啟重修虔臺志事紀。", "天啟重修虔臺志事紀口", "$ ^{20)} $天啟重修虔臺志事紀。"),
        ("贛關商稅達至四萬兩。", "②04 贛關商稅達至四萬兩。", "贛關商稅達至四萬兩口", "$ ^{20)} $贛關商稅達至四萬兩。"),
        ("當時江西南贛巡撫稅。", "當時江西南贛巡撫稅。", "當時江西南贛巡撫稅口", "$ ^{20)} $當時江西南贛巡撫稅。"),
    ]
    spec[9]["items"] = [("line", s) for s, _, _, _ in s9_notes]
    spec[9]["aEdits"] = [(s, a) for s, a, _, _ in s9_notes if a != s]
    spec[9]["bEdits"] = [(bt, b) for _, _, bt, b in s9_notes]

    # Scan 10: continuation after stitched span (`span_last = 205` -> `prior = 206`).
    spec[10]["items"][1:1] = [("line", "續表後論②⓪⑥甲乙丙丁"), ("line", "②⓪⑥清初史料輯錄")]
    spec[10]["aEdits"] = [("論②⓪⑥甲", "論②⓪⑥甲"), ("②⓪⑥清初", "②⓪⑥清初")]
    spec[10]["bEdits"] = [("論②⓪⑥甲", "論②⓪⑥甲"), ("②⓪⑥清初", "②⓪⑨清初")]
    spec[10]["sealedEdits"] = [("論②⓪⑥甲", "論甲"), ("②⓪⑥清初", "清初")]

    status, pages, log = repair(tmp, spec, "stitched")
    check(status == 0, f"stitched table repair exits 0 (exit {status})")
    p6 = pages[6]
    check("非兩藩之利也①⑨④兩" in p6 and "10671.66兩①⑨⑧" in p6 and "15000兩②⓪②" in p6 and "20000兩²⁰³" in p6,
          f"s0315-0318-table body calls 194..197 and table calls 198..205 written cleanly ({p6[:300]!r})")
    joined = "".join(f"<!-- page_{sc:04d} -->\n{pages[sc]}\n" for sc in (6, 7, 8, 9))
    converted, report = _footnotes.convert(joined)
    check(report["footnotes"] == 12 and "①⑨③陳玉輝上報鹽議" in converted
          and "10671.66兩[^198]" in converted and "[^205]: 當時江西南贛巡撫稅" in converted,
          f"s0315-0318-table converts all 12/12 footnotes while leaving orphan 193 ({report})")
    check("論②⓪⑥甲" in pages[10] and "②⓪⑥清初史料輯錄" in pages[10],
          f"prior=206 after stitched span settles scan 10 to 206 ({pages[10][:80]!r})")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        main_checks(root)
        more_checks(root)
        stitched_checks(root)
    print("all note-number checks passed" if not FAILURES else f"FAIL: {len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
