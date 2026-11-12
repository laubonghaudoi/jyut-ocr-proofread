"""Alignment of the sealed text with the two engine drafts, for the book
repair tool (scripts/repair_book.py) and its lint (_book_lint.py).

Every alignment is on CJK streams (phase 3's CJK(): ideographs and 〇, with
the zero forms folded), matched on variant-folded text (variant_fold) so two
forms of one character never move an anchor.  What is not CJK - marks,
figures, Latin letters, Markdown - sits in the gaps between characters:
gaps[k] is the raw text before character k.  Engine A's digits are in its
gaps, since CJK() drops them.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
import re
from typing import Sequence

import proofread_pages as pp


@dataclass
class Stream:
    """A text's CJK characters, the raw text in each gap, and each
    character's offset in the text."""
    text: str
    chars: str
    gaps: list[str]
    offsets: list[int]


def stream(text: str) -> Stream:
    chars: list[str] = []
    gaps = [""]
    offsets: list[int] = []
    for at, c in enumerate(text):
        kept = pp.CJK(c)
        if kept:
            chars.append(kept)
            offsets.append(at)
            gaps.append("")
        else:
            gaps[-1] += c
    return Stream(text, "".join(chars), gaps, offsets)


def mapping(source: str, target: str) -> list[int | None]:
    """For each character of TARGET, the index of the SOURCE character it is
    aligned equal to (variant-folded), else None."""
    matcher = difflib.SequenceMatcher(None, pp.variant_fold(source), pp.variant_fold(target), autojunk=False)
    out: list[int | None] = [None] * len(target)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            out[b + k] = a + k
    return out


def opcodes(source: str, target: str) -> list[tuple[str, int, int, int, int]]:
    matcher = difflib.SequenceMatcher(None, pp.variant_fold(source), pp.variant_fold(target), autojunk=False)
    return matcher.get_opcodes()


@dataclass
class PageAlignment:
    """The sealed text aligned with engine A and engine B."""
    sealed: Stream
    a: Stream
    b: Stream
    sealed_to_a: list[int | None]
    sealed_to_b: list[int | None]
    a_to_sealed: list[int | None]
    b_to_sealed: list[int | None]


def align_page(sealed_text: str, a_text: str, b_text: str) -> PageAlignment:
    sealed, a, b = stream(sealed_text), stream(a_text), stream(b_text)
    to_a = mapping(a.chars, sealed.chars)
    to_b = mapping(b.chars, sealed.chars)
    a_to = [None] * len(a.chars)
    for k, j in enumerate(to_a):
        if j is not None:
            a_to[j] = k
    b_to = [None] * len(b.chars)
    for k, j in enumerate(to_b):
        if j is not None:
            b_to[j] = k
    return PageAlignment(sealed, a, b, to_a, to_b, a_to, b_to)


DIGITS = re.compile(r"[0-9０-９]+")
DASH_CHARS = frozenset("—―─‒–-|｜")
DOT_CHARS = frozenset("·•・")
MARK_CHARS = pp.PRINTED_MARKS | DASH_CHARS | DOT_CHARS


def gap_kind(gap: str) -> str | None:
    """What an engine printed in a gap: figures, a dash, a dot, another mark,
    or nothing (None)."""
    if DIGITS.search(gap):
        return "figure"
    if any(c in DASH_CHARS for c in gap):
        return "dash"
    if any(c in DOT_CHARS for c in gap):
        return "dot"
    if any(c in MARK_CHARS for c in gap):
        return "mark"
    return None


def engine_between(target: Stream, to_target: Sequence[int | None], lo: int, hi: int) -> str | None:
    """What an engine (TARGET, with TO_TARGET mapping sealed characters onto
    it) read between the sealed characters LO and HI - both aligned to it,
    LO before HI - as raw text; None when either is not aligned."""
    if not (0 <= lo < len(to_target)) or not (0 <= hi < len(to_target)):
        return None
    a, b = to_target[lo], to_target[hi]
    if a is None or b is None or b <= a:
        return None
    start = target.offsets[a] + 1
    end = target.offsets[b]
    return target.text[start:end]


def adjacent_in(to_target: Sequence[int | None], lo: int, hi: int) -> bool:
    """Whether sealed characters LO and HI are aligned to adjacent characters
    of the engine."""
    if not (0 <= lo < len(to_target)) or not (0 <= hi < len(to_target)):
        return False
    a, b = to_target[lo], to_target[hi]
    return a is not None and b is not None and b == a + 1


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
