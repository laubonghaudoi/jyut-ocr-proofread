"""Rule W1 of repair_book.py (the words stage): one reading written different
ways across the book.

Where the two engines read one character each and differ, phase 3 settles
the spot on its own - 裁決 reads it from the crop, or a rule or the engine
preference writes one reading - and nothing compares the spot with the same
words elsewhere.  So one printed name comes out several ways in one book:
a multi-volume archival compilation 4 wrote 杨彲 as 彲, 彪, 彫, 彧 and 影 at fifteen spots where engine A
read 彲 at most of them, and 大里杙 as 代 at six; a historical monograph wrote 利瑪竇 as
利瑪寶 at seven spots.

W1 groups the spots by what either engine read there and the character
beside it on one side (彲 after 杨; 杙 after 里, which engine A read on some
pages and engine B on others).  A group of
two or more spots whose written characters differ, other than as forms of
one character by the lists (proofread_pages.classify_pair) or as numbering
(第二集, 第三集), is a doubt; groups that share a spot are one doubt, which
names every spot of them, its page and what was written there (what the
page holds there once the other stages have written, spots).  W1 changes
no text and asks no model: it cannot tell which spelling the print has, only
that the book writes one reading more than one way.

Measured on the six delivered books (2026-10-13, their sealed pages, the
repair model-off): 14 to 82 doubts a book (a historical monograph 14, a multi-volume archival compilation 2 20,
a multi-volume archival compilation 3 22, a multi-volume archival compilation 4 31, a multi-volume archival compilation 5 36, a vertical speech collection 82); of the 68 sites the
consistency checks of those books name (tests/cases, `consistent-*`), the
doubts name 47.  What W1 cannot see: a word written wrong the same way at
every spot (a multi-volume archival compilation 3's 圣监 four times and a historical monograph's 祛除 twice, pairs no
list holds, which 裁決 now reads from the image), and a spot where both
engines read the same character.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

import proofread_pages as pp
import _repair_rules as rr

# Characters numbering differs in: a group whose written characters are all
# of them (第二集 against 第三集) is numbering, not one reading.
NUMERALS = frozenset("〇零一二三四五六七八九十百千廿卅0123456789")
# Pages named per written character in a doubt; the rest are counted.
PAGES_SHOWN = 8


def spots(record: Mapping[str, Any], text: str | None = None) -> list[dict[str, str]]:
    """The spots of one sealed page where the engines read one character
    each, different, and one character was written: 裁決's records
    (adjudications: engine A's reading is draft_reading, engine B's
    writer_reading) and the disagreements settled without asking it
    (engineDisagreements).  What was written is the character TEXT, the
    page as the other stages left it, holds at the record's spot where the
    record's context puts it at one place (_repair_rules.at_record_spot, as
    V1 finds it), else the sealed one: the script stage writes 裁決's sealed
    蕆 as 蒇, and until 2026-10-26 W1 still named 蕆 there.  Measured on the
    repaired a multi-volume archival compilation 1-5 (2026-10-26, model-off): 10, 8, 12, 12 and 6 spots
    now name what the page holds, 9, 5, 9, 7 and 4 of them 蒇 for 蕆, the
    rest V1's other characters (塵 as 尘); every group as before."""
    out = []
    for item in record.get("adjudications") or []:
        out.append((item.get("draft_reading"), item.get("writer_reading"), item.get("resolved"),
                    item.get("context_before"), item.get("context_after")))
    for item in record.get("engineDisagreements") or []:
        out.append((item.get("engineA"), item.get("engineB"), item.get("resolved"),
                    item.get("context_before"), item.get("context_after")))
    chars = pp.CJK(text or "")
    kept = []
    for a, b, written, before, after in out:
        a, b, written = pp.CJK(str(a or "")), pp.CJK(str(b or "")), pp.CJK(str(written or ""))
        before, after = pp.CJK(str(before or "")), pp.CJK(str(after or ""))
        if len(a) == len(b) == len(written) == 1 and a != b:
            at = [k for k in range(len(chars)) if rr.at_record_spot(chars, k, k + 1, before, after)]
            kept.append({"A": a, "B": b, "written": chars[at[0]] if len(at) == 1 else written,
                         "before": before[-2:], "after": after[:2]})
    return kept


def one_character(forms: Iterable[str]) -> bool:
    forms = sorted(set(forms))
    return all(pp.classify_pair(forms[0], other) == "variant" for other in forms[1:])


def words(book: Any, texts: Mapping[int, str], order: Sequence[int]) -> rr.Plan:
    """W1 over every sealed page of BOOK, TEXTS the pages as the other
    stages left them (a page not in it as sealed); a doubt for each group of
    spots that holds a page of ORDER, on its first page."""
    found = [(scan, spot) for scan in sorted(book.pages)
             for spot in spots(book.pages[scan].record, texts.get(scan))]
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, (_, spot) in enumerate(found):
        for reading in (spot["A"], spot["B"]):
            if spot["before"]:
                groups[(spot["before"][-1] + "_", reading)].append(index)
            if spot["after"]:
                groups[("_" + spot["after"][0], reading)].append(index)
    parent: dict[int, int] = {}

    def root(index: int) -> int:
        while parent.setdefault(index, index) != index:
            index = parent[index]
        return index

    for members in groups.values():
        written = {found[index][1]["written"] for index in members}
        if len(members) < 2 or one_character(written) or written <= NUMERALS:
            continue
        for index in members[1:]:
            parent[root(index)] = root(members[0])
        root(members[0])
    joined: dict[int, list[int]] = defaultdict(list)
    for index in parent:
        joined[root(index)].append(index)
    selected = set(order)
    plan = rr.Plan()
    for members in sorted(joined.values(), key=lambda m: min(found[i][0] for i in m)):
        members.sort(key=lambda i: found[i][0])
        if not any(found[i][0] in selected for i in members):
            continue
        by_form: dict[str, list[int]] = defaultdict(list)
        for index in members:
            by_form[found[index][1]["written"]].append(found[index][0])
        parts = []
        for form, pages in sorted(by_form.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(str(p) for p in pages[:PAGES_SHOWN]) + (f" … ({len(pages)})" if len(pages) > PAGES_SHOWN
                                                                       else "")
            spot = next(found[i][1] for i in members if found[i][1]["written"] == form)
            parts.append(f"{spot['before']}［{form}］{spot['after']} p.{shown}")
        plan.doubts.append(("same-reading-written-differently", found[members[0]][0],
                            f"W1: one reading written {len(by_form)} ways: " + "; ".join(parts),
                            {"rule": "W1",
                             "spots": [{"page": found[i][0], **found[i][1]} for i in members]}))
    return plan
