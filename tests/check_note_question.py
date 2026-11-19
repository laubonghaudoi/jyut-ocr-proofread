#!/usr/bin/env python3
"""Offline check of the note-numbers question (issue #8, item 4:
proofread_pages.note_question_reason, ask_note_numbers, parse_note_numbers):

- a page whose engines read note numbers past one circled glyph (engine A
  ⑦② ⑦2, engine B \\textcircled{2} \\textcircled{7}) and that prints a note
  (engine A's footnote block) asks it once, on the whole page, and seals the
  answer as the record's noteNumbers;
- a page that prints no note, or one whose note numbers both engines read
  and the page holds, does not;
- the answer is read as JSON of one- to three-figure numbers (full-width
  figures folded), anything else set aside, no JSON an unparsed answer.

No model, no GPU: the stand-in book and model (tests/stand_in.py).

Usage: python3 tests/check_note_question.py   (exits non-zero if any check fails)
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402,F401
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


BODY = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽"
ANSWER = [{"number": "72", "note": "〔清〕鄭昌齡修", "before": "秋收冬藏"}]


def run(blocks, text_b, notes=ANSWER):
    model = stand_in.StandIn(census=0, notes=notes)
    with stand_in.book({1: ([stand_in.block(t, box, *rest) for t, box, *rest in blocks], text_b)}) as root:
        record, markdown = stand_in.run(root, model)[1]
    return record, markdown, model


blocks = [(BODY[:20] + "⑦②" + BODY[20:], [0.3, 0.1, 0.4, 0.6]),
          ("⑦2〔清〕鄭昌齡修寧都縣志", [0.3, 0.8, 0.4, 0.05], "footnote")]
text_b = BODY[:20] + "$ \\textcircled{2} $" + BODY[20:] + "\n$ \\textcircled{7} $〔清〕鄭昌齡修寧都縣志"
record, markdown, model = run(blocks, text_b)
check("a page with note numbers past one circled glyph asks note-numbers once", model.kinds().count("note-numbers"), 1)
check("the answer is sealed as noteNumbers", (record.get("noteNumbers") or {}).get("notes"), ANSWER)
check("its reason names the circled run", "circled figures" in (record.get("noteNumbers") or {}).get("reason", ""),
      True)
record, markdown, model = run([(BODY, [0.3, 0.1, 0.4, 0.6])], BODY)
check("a page that prints no note does not ask", (model.kinds().count("note-numbers"), "noteNumbers" in record),
      (0, False))
check("parse: one- to three-figure numbers, full width folded, the rest set aside",
      pp.parse_note_numbers('……{"notes": [{"number": "２４０", "note": "明清史料", "before": "前人研究"},'
                            ' {"number": "x"}, {"number": "1000"}, {"number": "0"}]}'),
      [{"number": "240", "note": "明清史料", "before": "前人研究"}])
check("parse: no JSON is no answer", pp.parse_note_numbers("睇唔清"), None)
check("reason: both engines read the page's numbers and the page holds them: none",
      pp.note_question_reason("甲①乙\n①丙", "甲①乙\n①丙", "甲①乙\n\n①丙", [{"label": "footnote", "text": "①丙"}]),
      None)

if __name__ == "__main__":
    print("all note-question checks passed" if not FAILURES else f"FAIL: {len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
