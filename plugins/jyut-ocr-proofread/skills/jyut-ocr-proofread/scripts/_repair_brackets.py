"""Rules B1 and B2 of repair_book.py (the brackets stage): the editors'
brackets 〔〕 the engines read, in the page's text in that shape, and no
round bracket doubled beside one.

Phase 3 merges the engines on CJK characters and the punctuator writes the
marks from the crop; its bracket rule writes back only round brackets
(proofread_pages.OPEN_BRACKETS).  So the brackets an editor prints around
a dynasty (〔明〕), a nationality (〔法〕) or a character he supplies
(〔迅速〕) stand in the page only as the punctuator wrote them, in whatever
shape: measured on the six delivered books (2026-10-13), both engines read
〔〕 at 1,788 places on a historical monograph and the delivered text wrote 313 of them
[], 273 （） and 22 not at all; on a multi-volume archival compilation 4, 10 of 56 were 「」 and 3 were
dropped.

B1 reads each page's gaps as the runs stage does (_repair_runs.PageRuns:
the gap between two page characters, and what each engine holds between
the characters it aligns to them).  Where both engines read 〔 in one gap
and the first bracket either reads after it is 〕, both in one later gap
of the same page line, the page's two gaps get 〔 and 〕.  Also where one
engine alone reads them so, if the other is aligned at both gaps and holds
in each no bracket, or only a square one facing the editors' one's way ([
or ［, its reading of the same print): measured on four simplified books
(2026-10-25, model-off), the punctuation pass (its census shapes say
"[]"), engine A and engine B each write [ ] for a printed 〔〕, and no
golden prints a square bracket; B1 on one engine then wrote 14 brackets on
one book, each as the second reading of it (the user's copy) has it, 12,
6 and 2 on three books with goldens, all right by them (two a pair the
page lacked, engine A reading [], engine B 〔〕), and 12 on an
archive-citing book (〔明〕 for its （明）).  Not where the other engine
reads a round bracket: engine B read 〔 for a printed （ on one page.  Each
gap's brackets facing the editors' one's way are read (a closing bracket in
the opening gap ends the text before it; a note marker [^1] is no bracket).  A
bracket of another shape, the gap's only one that way, is replaced where
neither engine reads that shape in the gap (a 「 both read before the 〔 is
the print's): the page's pair of another shape there (「」, [], （）, 【】
...), or one facing a 〔 or 〕 the page already holds.  Where a gap holds no
such bracket, 〔 goes at the end of its gap and 〕 at the start of its.
Nothing is written where a gap holds two brackets one way, or the editors'
bracket turned the other way (a 〔 in the closing gap), where the page
holds a bracket of another shape on one side and none on the other (its
partner may be elsewhere), where the two shapes are not a pair, nor where
〔 and 〕 stand in one gap (a figure or a mark between them, no character).

B2 is phase 3's bracket rule as it stands since 2026-10-26 applied to the
pages sealed before: a bracket of another shape the punctuator wrote at a
gap, facing the same way, stands for the round one an engine read there
(proofread_pages.place_brackets), where until then the rule wrote the
round bracket beside it for the same print.  On a sealed page, which does
not say which bracket the rule wrote, a gap holding a round bracket, in
the full-width form the rule writes, beside a bracket of another shape
facing the same way (〔（, （〔, 【（, [（ ...), where an engine reads that
other shape in the gap, loses the round one.  Not where one engine reads
both there (a round bracket printed beside an editors' one, （〔清〕…）).
Measured on the sealed pages of five simplified books (2026-10-25): each
doubled bracket had one engine reading 〔 and the other （, and the
punctuator's shape right; B2 wrote 2 on one book (where the user's copy
prints no bracket), 2 on each of two books with goldens, right by them,
and 10 and 20 on two more.

B1 and B2 ask no model and name no doubt: the edits are in the log.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import _repair_rules as rr
import _repair_runs as rn
import _repair_edits as re_

OPEN, CLOSE = "〔", "〕"
# The shapes the page may hold where the engines read the editors' brackets:
# each opening bracket with its closing one.
PAIRS = {"「": "」", "[": "]", "［": "］", "（": "）", "(": ")", "【": "】", "〈": "〉", "『": "』"}
CLOSERS = {close: open_ for open_, close in PAIRS.items()}


def _read_once(raw: str | None, mark: str) -> bool:
    return raw is not None and raw.count(mark) == 1 and raw.count(OPEN if mark == CLOSE else CLOSE) == 0


# The brackets an engine writes (B1, the other engine's gap): round, square,
# lenticular and the editors' own, each way; and the square ones that are its
# reading of the editors' one.
BRACKETS = frozenset("〔〕［］[]（）()【】")
SQUARE = {OPEN: "［[", CLOSE: "］]"}


def _beside(raw: str | None, mark: str) -> str | None:
    """B1 with one engine reading MARK (〔 or 〕) in a gap: the other
    engine's gap RAW with a square bracket facing MARK's way set aside (its
    reading of the same print), where that is the only bracket it holds
    there or it holds none; None where it holds another bracket (a round one
    reads another print) or is not aligned there."""
    if raw is None:
        return None
    held = [c for c in raw if c in BRACKETS]
    if not held:
        return raw
    if len(held) == 1 and held[0] in SQUARE[mark]:
        return raw.replace(held[0], "")
    return None


# B2: the brackets of another shape than round, each way, and the round one
# phase 3's bracket rule writes (proofread_pages.OPEN_BRACKETS, full width).
OTHER_SHAPES = {"open": "〔［[【", "close": "〕］]】"}
ROUND = {"open": "（", "close": "）"}


# A note marker in the page's form: its brackets are no bracket.
NOTE = re.compile(r"\[\^\d+\]")


def _side(text: str, mark: str, shapes: Mapping[str, str], read: str) -> tuple[str, int | None]:
    """What a gap TEXT holds where the engines read MARK (〔 or 〕), its
    brackets facing MARK's way (SHAPES and MARK) counted, note markers set
    aside: ("ok", None) the mark itself; ("shape", i) one bracket of SHAPES
    at offset i that neither engine reads (READ: both engines' gap text);
    ("none", None) no such bracket; ("unsure", None) anything else, or the
    editors' bracket turned the other way."""
    if (CLOSE if mark == OPEN else OPEN) in text:
        return "unsure", None
    bare = NOTE.sub(lambda m: " " * len(m.group()), text)
    held = [i for i, c in enumerate(bare) if c in shapes or c == mark]
    if mark in text:
        return ("ok", None) if len(held) == 1 else ("unsure", None)
    if not held:
        return "none", None
    if len(held) == 1 and text[held[0]] not in read:
        return "shape", held[0]
    return "unsure", None


def brackets(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
             facts: rr.BookFacts | None = None) -> rr.Plan:
    """B2 and B1 over the pages ORDER."""
    facts = facts or rr.book_facts(book, model)
    setting = rn.Setting(book, model, facts, list(order), texts)
    plan = rr.Plan()
    for scan in order:
        page = setting.page(scan)
        if not page.s.chars:
            continue
        gaps = list(page.gaps())
        for gap in gaps:
            edit = doubled(scan, page.text, gap)
            if edit is not None:
                plan.edits.append(edit)
        for k, gap in enumerate(gaps):
            if not (_read_once(gap.a_raw, OPEN) or _read_once(gap.b_raw, OPEN)):
                continue
            later = next((j for j in range(k + 1, len(gaps))
                          if any(m in (gaps[j].a_raw or "") + (gaps[j].b_raw or "") for m in (OPEN, CLOSE))), None)
            if later is None:
                continue
            shut = gaps[later]
            # The engines that read the pair: both, or one with the other
            # beside it (_beside); and what the two gaps hold that they read.
            sides = {"A": (gap.a_raw, shut.a_raw), "B": (gap.b_raw, shut.b_raw)}
            readers = [name for name, (o, c) in sides.items() if _read_once(o, OPEN) and _read_once(c, CLOSE)]
            if len(readers) == 2:
                by, read_open, read_close = "both", gap.a_raw + gap.b_raw, shut.a_raw + shut.b_raw
            elif readers:
                by = readers[0]
                (o, c), (other_o, other_c) = sides[by], sides["B" if by == "A" else "A"]
                beside_o, beside_c = _beside(other_o, OPEN), _beside(other_c, CLOSE)
                if beside_o is None or beside_c is None:
                    continue
                read_open, read_close = o + beside_o, c + beside_c
            else:
                continue
            if "\n" in page.text[gap.end:shut.start]:
                continue
            left, right = page.text[gap.start:gap.end], page.text[shut.start:shut.end]
            lkind, li = _side(left, OPEN, PAIRS, read_open)
            rkind, ri = _side(right, CLOSE, CLOSERS, read_close)
            kinds = {lkind, rkind}
            if "unsure" in kinds or kinds == {"ok"} or kinds == {"shape", "none"}:
                continue
            if lkind == rkind == "shape" and PAIRS[left[li]] != right[ri]:
                continue
            new_left = (left if lkind == "ok" else left[:li] + OPEN + left[li + 1:] if lkind == "shape"
                        else left + OPEN)
            new_right = (right if rkind == "ok" else right[:ri] + CLOSE + right[ri + 1:] if rkind == "shape"
                         else CLOSE + right)
            for g, old, new in ((gap, left, new_left), (shut, right, new_right)):
                if new != old:
                    plan.edits.append(re_.Edit(scan, g.start, g.end, new, rule="B1", evidence={
                        "before": old, "engineA": rn.show(g.a_raw or "", 80), "engineB": rn.show(g.b_raw or "", 80),
                        "inside": page.s.chars[k:later], "readBy": by}))
    return plan


def doubled(scan: int, text: str, gap: Any) -> re_.Edit | None:
    """B2 on one gap of the page: the gap with each round bracket that
    stands beside a bracket of another shape facing the same way, which an
    engine reads there, taken out (phase 3's bracket rule wrote it there
    until 2026-10-26) - unless one engine reads both shapes that way in the
    gap."""
    page = text[gap.start:gap.end]
    engines = [(gap.a_raw or "").translate(rr.NORM), (gap.b_raw or "").translate(rr.NORM)]
    new = page
    for way in ("open", "close"):
        round_ = ROUND[way]
        if any(round_ in e and any(o in e for o in OTHER_SHAPES[way]) for e in engines):
            continue
        for other in OTHER_SHAPES[way]:
            if not any(other in e for e in engines):
                continue
            for pair in (other + round_, round_ + other):
                if pair in new:
                    new = new.replace(pair, other, 1)
    if new == page:
        return None
    return re_.Edit(scan, gap.start, gap.end, new, rule="B2", evidence={
        "before": page, "engineA": rn.show(gap.a_raw or "", 80), "engineB": rn.show(gap.b_raw or "", 80)})
