#!/usr/bin/env python3
"""Offline checks of rule W1 (scripts/_repair_words.py, the words stage of
repair_book.py): one reading written different ways across the book is a
doubt that names every spot; nothing else is.

- spots: from 裁決's records and the disagreements settled without it, one
  character each side and one written; a reading of two characters, the
  same reading on both sides, or nothing written is not a spot;
- a group (what either engine read, with the character on one side)
  written two ways is one doubt on its first page, naming each written character, its
  pages and a context; written one way, no doubt;
- written characters that are forms of one character by the lists (清/淸,
  後/后), or numbering (第二集, 第三集), are no doubt;
- groups that share a spot are one doubt; a group with no page under repair
  (--pages) is no doubt; W1 never edits;
- what was written is what the page holds at the record's spot once the
  other stages have written (蒇 where the script stage wrote it for the
  sealed 蕆), the sealed character where the record's context finds no
  place, or two.

No model, no GPU, no corpus.

Usage: python3 tests/check_repair_words.py   (exits non-zero if any check fails)
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _repair_words as rw  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def asked(a: str, b: str, written: str, before: str, after: str) -> dict:
    return {"draft_reading": a, "writer_reading": b, "resolved": written,
            "context_before": before, "context_after": after}


def settled(a: str, b: str, written: str, before: str, after: str) -> dict:
    return {"engineA": a, "engineB": b, "resolved": written, "context_before": before, "context_after": after}


def book(pages: dict[int, dict]) -> SimpleNamespace:
    return SimpleNamespace(pages={scan: SimpleNamespace(record=record) for scan, record in pages.items()})


def doubts(pages: dict[int, dict], order=None, texts=None) -> list:
    plan = rw.words(book(pages), texts or {}, sorted(pages) if order is None else order)
    assert not plan.edits
    return plan.doubts


# --- spots
check("spots: 裁決's records and the settled disagreements, one character each side and one written",
      rw.spots({"adjudications": [asked("彲", "彧", "影", "守备杨", "业经降旨"), asked("杨彲", "杨彧", "杨彲", "守备", "在"),
                                  asked("彲", "彲", "彲", "守备杨", "在"), asked("彲", "彧", "", "守备杨", "在")],
                "engineDisagreements": [settled("杙", "代", "代", "打大里", "乾隆"), settled("。", "ㄛ", "。", "里", "乾")]}),
      [{"A": "彲", "B": "彧", "written": "影", "before": "备杨", "after": "业经"},
       {"A": "杙", "B": "代", "written": "代", "before": "大里", "after": "乾隆"}])

# --- one group, two ways
NAME = {43: {"adjudications": [asked("彲", "彫", "彲", "再守备杨", "在西门外")]},
        62: {"adjudications": [asked("彲", "彪", "彪", "阵亡守备杨", "等人乾隆")]},
        70: {"adjudications": [asked("彲", "彪", "彪", "又守备杨", "在西门外")]}}
found = doubts(NAME)
check("engine A's 彲 after 杨 written 彲 and 彪: one doubt on its first page, each form with its pages",
      [(kind, page, detail) for kind, page, detail, _ in found],
      [("same-reading-written-differently", 43,
        "W1: one reading written 2 ways: 备杨［彪］等人 p.62, 70; 备杨［彲］在西 p.43")])
check("... the doubt's fields list every spot", [s["page"] for s in found[0][3]["spots"]], [43, 62, 70])
check("written one way: no doubt",
      doubts({43: {"adjudications": [asked("彲", "彫", "彲", "守备杨", "在西")]},
              62: {"adjudications": [asked("彲", "彪", "彲", "守备杨", "等人")]}}), [])

# --- one character, numbering
check("forms of one character by the lists (清/淸): no doubt",
      doubts({1: {"adjudications": [asked("淸", "渚", "淸", "大", "朝")]},
              2: {"adjudications": [asked("淸", "渚", "清", "大", "朝")]}}), [])
check("numbering (第二, 第三): no doubt",
      doubts({1: {"adjudications": [asked("二", "三", "二", "第", "集")]},
              2: {"adjudications": [asked("二", "三", "三", "第", "集")]}}), [])

# --- groups sharing a spot are one doubt; pages under repair
SHARED = {12: {"engineDisagreements": [settled("杙", "代", "代", "打大里", "乾隆")]},
          14: {"engineDisagreements": [settled("杙", "代", "杙", "克大里", "林爽")]},
          356: {"engineDisagreements": [settled("代", "杙", "代", "在大里", "看守")]},
          461: {"engineDisagreements": [settled("代", "杙", "杙", "在大里", "林爽")]}}
found = doubts(SHARED)
check("杙 after 里, read by engine A on two pages and by engine B on two others, is one doubt",
      [(page, sorted(s["page"] for s in fields["spots"])) for _, page, _, fields in found], [(12, [12, 14, 356, 461])])
check("a group with no page under repair is no doubt", doubts(SHARED, order=[100]), [])
check("a group with one page under repair is a doubt, still naming every spot",
      [sorted(s["page"] for s in fields["spots"]) for *_, fields in doubts(SHARED, order=[461])], [[12, 14, 356, 461]])

# --- what the page holds at the spot
# 天地会 1 scans 242 and 248: engine A 藏, engine B 葳; 裁決's 蕆 sealed on the first,
# which the script stage writes 蒇 (V1), 葳 on the second.
DONE = {242: {"adjudications": [asked("藏", "葳", "蕆", "不及一月即已收复", "功伊等岂竟未之闻")]},
        248: {"adjudications": [asked("藏", "葳", "葳", "官兵迅速收复", "功乃该督")]}}
TEXTS = {242: "不及一月，即已收复蒇功，伊等岂竟未之闻。", 248: "官兵迅速收复葳功乃该督"}
check("the character the page holds at the record's spot, not the sealed one",
      [detail for _, _, detail, _ in doubts(DONE, texts=TEXTS)],
      ["W1: one reading written 2 ways: 收复［蒇］功伊 p.242; 收复［葳］功乃 p.248"])
check("... the sealed one where the spot is found at no place, or at two",
      [[detail for _, _, detail, _ in doubts(DONE, texts={242: text, 248: TEXTS[248]})]
       for text in ("收复蒇功", TEXTS[242] + "\n" + TEXTS[242])],
      [["W1: one reading written 2 ways: 收复［蕆］功伊 p.242; 收复［葳］功乃 p.248"]] * 2)
check("... a page not among the texts, as sealed",
      [detail for _, _, detail, _ in doubts(DONE, texts={248: TEXTS[248]})],
      ["W1: one reading written 2 ways: 收复［蕆］功伊 p.242; 收复［葳］功乃 p.248"])
check("... grouped as before: 蒇 on one page and the sealed 蕆 on another are one character, no doubt",
      doubts({**DONE, 248: {"adjudications": [asked("藏", "葳", "蕆", "官兵迅速收复", "功乃该督")]}},
             texts={242: TEXTS[242]}), [])

print("all W1 checks passed" if not failures else f"FAIL: {failures} failure(s)")
sys.exit(1 if failures else 0)
