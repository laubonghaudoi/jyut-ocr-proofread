"""Brackets, arabic figures, footnote call restoration, and structure marker helpers."""

from __future__ import annotations

import bisect
from collections import Counter
import difflib
import functools
import itertools
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Collection, Mapping, Sequence
import unicodedata

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes,
)
from jyut_ocr.core import note_markers as _note_markers  # noqa: E402
from _proofread_core import (  # noqa: E402
    BRACKET_BESIDE, BRACKET_MISREADS, BRACKET_SLIP, CIRCLE_CHARS, CJK, CLOSE_BRACKETS,
    DEGENERATE_NGRAM_RATIO, FURNITURE_LABELS, LEADER, LEADER_READ, LOOPED_READING, NEARBY_WINDOW,
    NUMERAL_CHARS, OPENING_MARKS, OPEN_BRACKETS, OTHER_BRACKETS, UNCORROBORATED_MINIMUM,
    distinct_ngram_ratio, engine_gaps, layout_index, load_blocks, strip_grounding, verdict_answer,
)
from _proofread_variants import variant_fold  # noqa: E402
from _proofread_adjudicate import align_drafts  # noqa: E402
from _proofread_furniture import margin_blocks  # noqa: E402
from _proofread_punctuation import (  # noqa: E402
    engine_b_places, engine_places,
)


def bracket_kinds(gap: str) -> list[str]:
    """The round brackets in a gap, in order, each as （ or ）."""
    return ["（" if c in OPEN_BRACKETS else "）" for c in gap
            if c in OPEN_BRACKETS or c in CLOSE_BRACKETS]


# A pair of round brackets an engine opened and closed between two of its
# characters: around a figure, Latin or nothing, which the merge (characters
# only) does not write.  The bracket rule wrote such a pair back as 「）（」 -
# the opening bracket goes with the character after it, the closing one with
# the character before - and, where engine B's reading was a character off, a
# pair inside a name (「瑚）（图里」): measured on the sealed contents pages of
# a book listing names, each with its page number in brackets, 20 such pairs
# on one page of 18 names.  With --keep-arabic-numerals only, which writes the
# figures it keeps and reports those it does not (engine_brackets_merged).
GAP_PAIR = re.compile(r"[（(︵][^（(︵）)︶]*[）)︶]")


def unpaired_brackets(gap: str) -> list[str]:
    """The round brackets of an engine's gap (bracket_kinds) less each pair
    opened and closed in it (GAP_PAIR)."""
    return bracket_kinds(GAP_PAIR.sub("", gap))


def explain_by_brackets(mine: str, gaps: Sequence[str], other: str, others_own: int) -> str | None:
    """OTHER with the numerals that this engine's brackets account for taken
    out, or None when no bracket accounts for any of them.

    MINE is this engine's reading of a numeral disagreement and GAPS the raw
    text it wrote in the len(MINE) + 1 gaps around and between those
    characters.  An engine that printed a bracket where the other engine has a
    numeral saw the bracket the other misread: engine B reads a vertical ︵ as
    八 or 一 and ︶ as 一, so engine A's （七） comes back 八七一.  Each bracket
    accounts for at most one of the other's numerals, and only brackets the
    other engine did not print itself count (OTHERS_OWN of them it did).  The
    other's remaining characters are returned in order, one for each of MINE:
    equal to MINE when the brackets explain the whole disagreement, and a
    shorter question (三 against 二 instead of 三 against 一一二一) when they do
    not.  Only a stroke shaped like a bracket (BRACKET_MISREADS) is taken for
    one.  Measured on one page: 裁決 answered 二, 一, 一 and 五 at four
    brackets and changed a bracketed 三 to 二, where engine A had printed every
    bracket.  When the brackets explain everything the engines read the same
    characters, and two-engine agreement is kept, as it is everywhere else -
    even the one time it was wrong in the goldens (both engines read the list
    number （二） as 一; 裁決 recovered the 二 in two of five runs).
    """
    tokens: list[tuple[str, str]] = [("X", k) for k in bracket_kinds(gaps[0])]
    for index, char in enumerate(mine):
        tokens.append(("C", char))
        tokens.extend(("X", k) for k in bracket_kinds(gaps[index + 1]))
    cap = sum(1 for kind, _ in tokens if kind == "X") - others_own
    if cap <= 0:
        return None

    @functools.lru_cache(maxsize=None)
    def best(i: int, j: int, used: int) -> tuple[int, int, str] | None:
        # (substitutions, -absorbed, the other's characters under mine)
        if i == len(tokens):
            return (0, 0, "") if j == len(other) else None
        kind, value = tokens[i]
        options = []
        if kind == "X":
            skip = best(i + 1, j, used)
            if skip:
                options.append(skip)
            if j < len(other) and used < cap and other[j] in BRACKET_MISREADS:
                taken = best(i + 1, j + 1, used + 1)
                if taken:
                    options.append((taken[0], taken[1] - 1, taken[2]))
        elif j < len(other):
            rest = best(i + 1, j + 1, used)
            if rest:
                options.append((rest[0] + (other[j] != value), rest[1], other[j] + rest[2]))
        return min(options) if options else None

    found = best(0, 0, 0)
    if found is None or found[1] == 0:
        return None
    return found[2]


def bracket_explanation(seg: Mapping[str, Any], a_start: int, b_start: int,
                        stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                        ) -> tuple[str, Any] | None:
    """How the engines' own brackets settle a numeral disagreement.

    ("A", reading) or ("B", reading) when one engine's brackets account for
    every numeral the other has in excess, so both read the same characters
    and the brackets were misread as strokes; ("reduced", segment) when they
    account for some, leaving a shorter question for 裁決; None otherwise.
    The streams are engine_gaps() of each engine's page text; a segment whose
    characters are not found at its start in either stream is left alone.
    """
    a, b = seg["a"], seg["b"]
    (chars_a, gaps_a), (chars_b, gaps_b) = stream_a, stream_b
    if chars_a[a_start:a_start + len(a)] != a or chars_b[b_start:b_start + len(b)] != b:
        return None
    around_a = gaps_a[a_start:a_start + len(a) + 1]
    around_b = gaps_b[b_start:b_start + len(b) + 1]
    own_a = sum(len(bracket_kinds(g)) for g in around_a)
    own_b = sum(len(bracket_kinds(g)) for g in around_b)
    core = explain_by_brackets(a, around_a, b, own_b)
    if core == a:
        return "A", a
    core_b = explain_by_brackets(b, around_b, a, own_a)
    if core_b == b:
        return "B", b
    if core is not None and core:
        return "reduced", dict(seg, b=core, bracketReduced={"engineA": a, "engineB": b})
    return None


def engine_brackets_merged(stream: tuple[str, list[str]], side: str,
                           segments: Sequence[Mapping[str, Any]],
                           pieces: Sequence[str],
                           figures_at: Collection[int] | None = None,
                           beside: bool = False) -> list[tuple[int, str]] | None:
    """The round brackets one engine read that the bracket rule writes back,
    as (gap in the merged text, bracket).

    With FIGURES_AT (--keep-arabic-numerals) not a pair it opened and closed
    between two characters (unpaired_brackets), around a figure or Latin the
    merge does not write, save at a gap of FIGURES_AT, where the merge writes
    a figure (brackets_by_block takes the figure's own brackets out); a
    figure or mark so left out that the page holds no copy of is a doubt
    (bracketed_pairs).  With FIGURES_AT None (the switch off, or not run on
    the page) every bracket, pairs too, as before: the merge keeps no figure
    and reports none, and the pair puts the punctuator's brackets back
    beside engine B's stand-in for the figure (「：丄）（土地」, which repair
    K1 writes 「：（1）土地」).  Measured on a book sealed with the switch off
    (canzungsiling, 2026-10-10): with the pair left out, on 4 pages whose two
    punctuation answers were refused the brackets stood where the fallback
    put them, split round the stand-in (「：）丄（土地」), and on 2 of them K1
    wrote 「：）：（1）土地」.

    SIDE is the engine's key in the segments ("a" or "b").  A bracket before
    one of the engine's characters goes before that character's place in the
    merged text; inside a disagreement resolved to other characters, before the
    character at the same offset, or after the resolution's last.  None when
    the segments do not spell out this engine's text.  Segments that carry
    their place in engine B's text (bStart: text engine B read in another order
    was placed at engine A's site, place_moved_once) are read by it.

    BESIDE: only the round brackets of a gap that also holds a bracket of
    another shape facing the same way (OTHER_BRACKETS: the engine read both,
    （〔清〕), which place_brackets writes even beside the punctuator's.
    """
    chars, gaps = stream

    def kinds(gap: str, at: int) -> list[str]:
        found = bracket_kinds(gap) if figures_at is None or at in figures_at else unpaired_brackets(gap)
        return [kind for kind in found if not beside or kind in {OTHER_BRACKETS.get(c) for c in gap}]
    if side == "b" and segments and all("bStart" in x for x in segments):
        where: list[int | None] = [None] * (len(chars) + 1)
        m = 0
        for seg, piece in zip(segments, pieces):
            text, e = seg["b"], seg["bStart"]
            if chars[e:e + len(text)] != text or any(where[e + k] is not None for k in range(len(text))):
                return None
            for k in range(len(text)):
                where[e + k] = m + (k if piece == text else min(k, len(piece)))
            if text and e + len(text) == len(chars):
                where[len(chars)] = m + len(piece)
            m += len(piece)
        if where[len(chars)] is None:
            where[len(chars)] = m
        if any(w is None for w in where):
            return None
        return [(where[g], kind) for g, gap in enumerate(gaps) for kind in kinds(gap, where[g])]
    if chars != "".join(x[side] for x in segments):
        return None
    where = [0] * (len(chars) + 1)
    e = m = 0
    for seg, piece in zip(segments, pieces):
        text = seg[side]
        for k in range(len(text)):
            where[e + k] = m + (k if piece == text else min(k, len(piece)))
        e += len(text)
        m += len(piece)
    where[len(chars)] = m
    return [(where[g], kind) for g, gap in enumerate(gaps) for kind in kinds(gap, where[g])]


# Printed marks as an engine writes them, for the mark rule (mark_explanation):
# the full-width sentence marks, quotes and brackets, their vertical
# presentation forms, the item bullets and the leader dots.  ASCII is left out:
# engine B writes its grounding boxes as (x,y) pairs, and markup is not a mark.
PRINTED_MARKS = frozenset("。，、；：？！「」『』《》〈〉（）"
                          "︐︑︒︓︔︕︖︙︰︵︶﹁﹂﹃﹄▲△●◎◆■…⋯")
# The mark rule's search over one disagreement: tokens x characters x marks
# spent.  A bound on work, not on what counts as a mark: the disagreements it
# settles hold a few characters (measured: the longest a book's aliases
# accounted for was 3 characters against 2 marks), and a disagreement larger
# than this - two engines reading a stretch in different orders - is left to
# the ordinary path, unexplained.
MARK_SEARCH_LIMIT = 250_000


def engine_marks(text: str) -> tuple[str, list[str]]:
    """engine_gaps(), with only the printed marks (PRINTED_MARKS) kept in each gap."""
    chars, gaps = engine_gaps(text)
    return chars, ["".join(c for c in gap if c in PRINTED_MARKS) for gap in gaps]


def explain_by_marks(mine: str, gaps: Sequence[str], other: str, others_own: int,
                     aliases: Mapping[str, Mapping[str, int]] | frozenset | set,
                     ) -> tuple[str, list[tuple[str, str]]] | None:
    """OTHER's characters left once this engine's printed marks account for
    some of them, and the (character, mark) pairs accounted for; None when no
    mark accounts for any.

    explain_by_brackets for every printed mark.  MINE is this engine's reading
    of a disagreement and GAPS the marks it printed in the len(MINE) + 1 gaps
    around and between those characters (engine_marks); each of MINE is
    matched, in order, to one of OTHER's characters.  A mark accounts for at
    most one of OTHER's characters, and only marks OTHER did not print there
    itself count (OTHERS_OWN of them it did): an engine that printed the comma
    itself did not read it as 丶.  Only a character in ALIASES - the glyphs
    this book's drafts show the other engine writing for marks (mark_aliases)
    - is taken for one; with a mapping, its per-mark counts break ties between
    readings that account for as many characters (爹一一 against ；（: which
    glyph was which mark), toward the pairs the book showed more often.  A
    character no mark accounts for stays in the result, in order: a smaller
    question than the whole disagreement (査 against 查 instead of 査丶
    against 查).  The search minimises characters left over, then
    substitutions, then maximises the evidence.
    """
    tokens: list[tuple[str, str]] = [("X", m) for m in gaps[0]]
    for index, char in enumerate(mine):
        tokens.append(("C", char))
        tokens.extend(("X", m) for m in gaps[index + 1])
    cap = sum(1 for kind, _ in tokens if kind == "X") - others_own
    if cap <= 0 or not any(c in aliases for c in other):
        return None
    if len(tokens) * (len(other) + 1) * (cap + 1) > MARK_SEARCH_LIMIT:
        return None
    weighted = isinstance(aliases, Mapping)

    def weight(char: str, mark: str) -> int:
        return (aliases[char].get(mark, 0) if weighted else 0) if char in aliases else 0

    n_t, n_o = len(tokens), len(other)
    # best[(i, j, u)] = (key, choice) for tokens[i:], other[j:], u marks spent;
    # key = (left over, substitutions, -evidence, -absorbed), filled bottom-up.
    best: dict[tuple[int, int, int], tuple[tuple[int, int, int, int], str]] = {}
    for i in range(n_t, -1, -1):
        for j in range(n_o, -1, -1):
            for u in range(cap, -1, -1):
                if i == n_t and j == n_o:
                    best[(i, j, u)] = ((0, 0, 0, 0), "end")
                    continue
                options = []
                if j < n_o and (i, j + 1, u) in best:           # other[j] accounted for by nothing
                    k = best[(i, j + 1, u)][0]
                    options.append(((k[0] + 1, k[1], k[2], k[3]), "left"))
                if i < n_t:
                    kind, value = tokens[i]
                    if kind == "X":
                        if (i + 1, j, u) in best:                # the mark stands for nothing of other's
                            options.append((best[(i + 1, j, u)][0], "skip"))
                        if j < n_o and u < cap and other[j] in aliases and (i + 1, j + 1, u + 1) in best:
                            k = best[(i + 1, j + 1, u + 1)][0]
                            options.append(((k[0], k[1], k[2] - weight(other[j], value), k[3] - 1), "absorb"))
                    elif j < n_o and (i + 1, j + 1, u) in best:
                        k = best[(i + 1, j + 1, u)][0]
                        options.append(((k[0], k[1] + (other[j] != value), k[2], k[3]), "match"))
                if options:
                    best[(i, j, u)] = min(options)
    if (0, 0, 0) not in best:
        return None
    kept, pairs = [], []
    i = j = u = 0
    while (i, j) != (n_t, n_o):
        choice = best[(i, j, u)][1]
        if choice == "left":
            kept.append(other[j])
            j += 1
        elif choice == "skip":
            i += 1
        elif choice == "absorb":
            pairs.append((other[j], tokens[i][1]))
            i, j, u = i + 1, j + 1, u + 1
        else:
            kept.append(other[j])
            i, j = i + 1, j + 1
    return ("".join(kept), pairs) if pairs else None


def mark_explanation(seg: Mapping[str, Any], a_start: int, b_start: int,
                     stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                     aliases: Mapping[str, Any]) -> tuple[str, Any, list[tuple[str, str]], str] | None:
    """How one engine's printed marks settle the other's extra characters.

    ("A", reading, pairs, "B") when engine A's marks account for every
    character engine B has in excess - engine B read engine A's marks as
    characters (丶 for ，, 厶 for ▲) and both read the same characters
    otherwise; ("B", reading, pairs, "A") the other way round; ("reduced",
    segment, pairs, side) when they account for some, leaving a smaller
    question for the ordinary path; None otherwise.  The last item names the
    engine whose glyphs were taken for marks.  STREAMS are engine_marks() of
    each engine's page text, ALIASES the glyphs each engine is seen to write
    for marks on this book (mark_aliases: {"A": ..., "B": ...}); a segment
    whose characters are not found at its start in either stream is left
    alone.
    """
    a, b = seg["a"], seg["b"]
    (chars_a, gaps_a), (chars_b, gaps_b) = stream_a, stream_b
    if chars_a[a_start:a_start + len(a)] != a or chars_b[b_start:b_start + len(b)] != b:
        return None
    around_a = gaps_a[a_start:a_start + len(a) + 1]
    around_b = gaps_b[b_start:b_start + len(b) + 1]
    own_a, own_b = sum(map(len, around_a)), sum(map(len, around_b))
    for mine, around, other, own, side, key in ((a, around_a, b, own_b, "B", "b"),
                                                (b, around_b, a, own_a, "A", "a")):
        if len(other) <= len(mine):
            continue
        found = explain_by_marks(mine, around, other, own, aliases.get(side) or {})
        if not found:
            continue
        kept, pairs = found
        if kept == mine:
            return ("A" if side == "B" else "B"), mine, pairs, side
        return "reduced", dict(seg, **{key: kept}, markReduced={"engineA": a, "engineB": b,
                                                                "marks": [list(p) for p in pairs]}), pairs, side
    return None


def circle_withheld(seg: Mapping[str, Any], a_start: int, b_start: int, found: Sequence[Any],
                    stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                    block: Mapping[str, Any] | None) -> str | None:
    """Why the mark rule may not take a circle for a mark, or None when it may.

    FOUND is mark_explanation's answer.  A 〇 it would take for one of the
    other engine's marks is held to is_lone_circle's context: beside a
    numeral or another circle it may be a zero (二〇, 一九〇五) or part of a
    withheld name (張〇〇), and in a table block (or one that cannot be found)
    a figure; those go on to the crop, as the circle rule sends them.
    Measured on every draft pair of six books: of the 3,383 disagreements the
    rule settles, 13 take a circle, and 7 of those have a numeral beside it -
    six a circle next to a figure in prose, one the last digit of a
    five-figure number - which the circle rule and the numeral protection
    had kept for 裁決; the other 6 stand alone.  The neighbours are the
    engine's own characters either side of the circle, in its stream.
    """
    pairs, side = found[2], found[3]
    if not any(char in CIRCLE_CHARS for char, _ in pairs):
        return None
    if block is None or block.get("label") == "table":
        return "a circle in a table block, or in no block found"
    chars, start, text = (stream_a[0], a_start, seg["a"]) if side == "A" else (stream_b[0], b_start, seg["b"])
    for j, char in enumerate(text):
        if char in CIRCLE_CHARS:
            near = chars[max(0, start + j - 1):start + j] + chars[start + j + 1:start + j + 2]
            if any(c in NUMERAL_CHARS or c in CIRCLE_CHARS or c.isdigit() for c in near):
                return "a circle beside a numeral or another circle"
    return None


# The two gates on a glyph taken for a mark, from this book's own drafts
# (mark_aliases).  Measured over five books' drafts, excess occurrences of a
# glyph at a gap where the other engine printed a mark it did not, against its
# excess occurrences anywhere else: a mark reading concentrates at the marks
# (engine B's 丶 1,682:32 on a magazine printing full-cell commas, 口 274:13,
# 号 51:2; 厶 101:60 for ▲ on another book), a character engine A simply missed
# spreads over the page (1:107, 1:93, 1:244 for three that each met a mark gap
# once).  One site is a coincidence, so it takes two, and more explained than
# unexplained.  Nothing about which glyphs: a book that prints its marks
# plainly shows none (engine B's on one book: none at all).
MARK_ALIAS_MINIMUM = 2


def mark_evidence(pairs: Sequence[tuple[Path, Path]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per engine and glyph, how often its excess occurrences sit where the other
    engine printed a mark (explained, per mark) or not (unexplained), over
    every draft pair in the book, with no model call.

    Each disagreement where one engine read more characters is searched with
    no alias restriction (explain_by_marks over every glyph it holds): what the
    other engine's marks can account for counts as explained, the rest of its
    excess as unexplained.  A page where either engine looped is left out -
    one engine repeating a glyph thousands of times would outvote the book -
    and so is a disagreement too large to search (MARK_SEARCH_LIMIT), which
    says nothing either way.
    """
    explained = {"A": {}, "B": {}}
    unexplained = {"A": Counter(), "B": Counter()}
    for path_a, path_b in pairs:
        try:
            text_a = path_a.read_text(encoding="utf-8")
            text_b = strip_grounding(path_b.read_text(encoding="utf-8"))
        except OSError:
            continue
        if any(len(CJK(t)) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(CJK(t)) < DEGENERATE_NGRAM_RATIO
               for t in (text_a, text_b)):
            continue
        (_, gaps_a), (_, gaps_b) = engine_marks(text_a), engine_marks(text_b)
        a_at = b_at = 0
        for seg in align_drafts(text_a, text_b):
            a, b = seg["a"], seg["b"]
            if seg["tag"] != "equal" and len(a) != len(b):
                side, short, long_ = ("B", a, b) if len(b) > len(a) else ("A", b, a)
                s_gaps = (gaps_a[a_at:a_at + len(a) + 1] if side == "B" else gaps_b[b_at:b_at + len(b) + 1])
                l_gaps = (gaps_b[b_at:b_at + len(b) + 1] if side == "B" else gaps_a[a_at:a_at + len(a) + 1])
                tokens = len(short) + sum(map(len, s_gaps))
                if tokens * (len(long_) + 1) * (sum(map(len, s_gaps)) + 1) <= MARK_SEARCH_LIMIT:
                    found = explain_by_marks(short, s_gaps, long_, sum(map(len, l_gaps)), frozenset(long_))
                    taken = Counter(c for c, _ in found[1]) if found else Counter()
                    for char, mark in (found[1] if found else []):
                        explained[side].setdefault(char, Counter())[mark] += 1
                    unexplained[side].update(Counter(long_) - Counter(short) - taken)
            a_at += len(a)
            b_at += len(b)
    return {side: {char: {"explained": sum(explained[side].get(char, Counter()).values()),
                          "unexplained": unexplained[side][char],
                          "marks": dict(explained[side].get(char, Counter()))}
                   for char in set(explained[side]) | set(unexplained[side])}
            for side in ("A", "B")}


def mark_aliases(evidence: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, dict[str, int]]]:
    """The glyphs each engine is seen to write for the other's printed marks on
    this book (mark_evidence), each with its per-mark counts: explained at
    MARK_ALIAS_MINIMUM sites or more, and more often explained than not."""
    return {side: {char: dict(row["marks"]) for char, row in (evidence.get(side) or {}).items()
                   if row["explained"] >= MARK_ALIAS_MINIMUM and row["explained"] > row["unexplained"]}
            for side in ("A", "B")}


def place_brackets(text: str, span: str, wanted: Mapping[tuple[int, str], int]) -> tuple[str, int, int]:
    """TEXT, a punctuated block, with the round brackets the engines read.

    WANTED counts brackets by (gap, kind): gap k is before the block's
    character k.  A bracket the punctuator already wrote at that gap is kept;
    so is one of another shape facing the same way (OTHER_BRACKETS: 〔 for a
    wanted （), which stands for it.  Until 2026-10-26 the round one was
    written beside it; measured on the sealed pages of five simplified books
    (2026-10-25), at each such doubled bracket one engine read 〔 and the
    other （, and the punctuator's shape was the print's (repair's B2 takes
    those round brackets out of the pages sealed before).  Not for the round
    brackets WANTED keys (gap, 〔) or (gap, 〕) counts, which one engine read
    beside that shape (brackets_by_block): the print holds both there
    (（〔清〕林時益輯）, both engines reading （〔), so those are written
    beside the punctuator's 〔 as before.  One it wrote
    within BRACKET_SLIP gaps of a wanted bracket of the same kind that is
    not there is taken for that bracket misplaced and moved; any other
    bracket it wrote is kept.  Returns the text, how many brackets were
    written in and how many of the punctuator's were moved.  The characters
    are never touched; a text whose characters are not SPAN is returned as
    is.
    """
    raw_chars: list[str] = []
    gaps: list[list[str]] = [[]]
    for c in text:
        if CJK(c):
            raw_chars.append(c)
            gaps.append([])
        else:
            gaps[-1].append(c)
    if CJK("".join(raw_chars)) != CJK(span):
        return text, 0, 0
    need = Counter({key: n for key, n in wanted.items() if n > 0 and key[1] in "（）"})
    loose: list[tuple[int, int, str]] = []
    for g, gap in enumerate(gaps):
        for pos, c in enumerate(gap):
            if c in OPEN_BRACKETS or c in CLOSE_BRACKETS:
                kind = "（" if c in OPEN_BRACKETS else "）"
                if need[(g, kind)] > 0:
                    need[(g, kind)] -= 1
                else:
                    loose.append((g, pos, kind))
    for g, gap in enumerate(gaps):
        for c in gap:
            kind = OTHER_BRACKETS.get(c)
            if kind and need[(g, kind)] > wanted.get((g, BRACKET_BESIDE[kind]), 0):
                need[(g, kind)] -= 1
    claimed: Counter = Counter()
    drop: set[tuple[int, int]] = set()
    for g, pos, kind in loose:
        near = sorted((abs(g2 - g), g2) for (g2, k2), n in need.items()
                      if k2 == kind and n - claimed[(g2, k2)] > 0 and abs(g2 - g) <= BRACKET_SLIP)
        if near:
            claimed[(near[0][1], kind)] += 1
            drop.add((g, pos))
    out: list[str] = []
    for g, gap in enumerate(gaps):
        kept = [c for pos, c in enumerate(gap) if (g, pos) not in drop]
        first_open = next((i for i, c in enumerate(kept) if c in OPEN_BRACKETS), len(kept))
        kept = (kept[:first_open] + ["）"] * need[(g, "）")] + kept[first_open:]
                + ["（"] * need[(g, "（")])
        out.append("".join(kept))
        if g < len(raw_chars):
            out.append(raw_chars[g])
    return "".join(out), sum(need.values()), len(drop)


def brackets_by_block(stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]] | None,
                      segments: Sequence[Mapping[str, Any]], pieces: Sequence[str], spans: Sequence[str],
                      own: Mapping[tuple[int, str], int] | None = None) -> dict[int, Counter]:
    """The round brackets either engine read (engine_brackets_merged; both
    reading one bracket is one bracket), by block: block index -> Counter of
    (gap in the block's span, bracket), for place_brackets.  OWN
    (--keep-arabic-numerals) counts, by
    (gap in the merged text, bracket), the brackets of the figures the merge
    writes, which go with their figure (figure_in_gap) and are not written
    here: a figure starting a block would otherwise see its closing bracket
    written at the end of the block before, with the character before it.
    Nor is a pair an engine read around no character, elsewhere
    (unpaired_brackets).  OWN None (the switch off): every bracket either
    engine read is written (engine_brackets_merged).  Keyed (gap, 〔) and
    (gap, 〕): how many of the round brackets wanted at the gap one engine
    read beside a bracket of another shape facing the same way (BRACKET_BESIDE)."""
    ends = list(itertools.accumulate(len(span) for span in spans))
    total = ends[-1] if ends else 0
    wanted: Counter = Counter()
    beside: Counter = Counter()
    figures_at = None if own is None else {gap for gap, _ in own}
    for stream, side in ((stream_a, "a"), (stream_b, "b")):
        for key, n in Counter(engine_brackets_merged(stream, side, segments, pieces, figures_at) or []).items():
            wanted[key] = max(wanted[key], n)
        for (gap, kind), n in Counter(engine_brackets_merged(stream, side, segments, pieces, figures_at,
                                                             beside=True) or []).items():
            beside[(gap, BRACKET_BESIDE[kind])] = max(beside[(gap, BRACKET_BESIDE[kind])], n)
    if own:
        wanted -= Counter(own)
    per_block: dict[int, Counter] = {}
    for (gap, kind), n in (wanted + beside).items():
        if not total:
            break
        # An opening bracket belongs with the character after it, a closing
        # one with the character before it.
        anchor = min(max(gap if kind in "（〔" else gap - 1, 0), total - 1)
        k = bisect.bisect_right(ends, anchor)
        start = ends[k - 1] if k else 0
        per_block.setdefault(k, Counter())[(gap - start, kind)] += n
    return per_block


# Switch --keep-arabic-numerals (on by default; ROUND4_DEFAULTS).  The merge keeps characters
# only (CJK), so an Arabic figure engine A read - a list item's (1), a contents
# entry's 12, a document heading's 5. - stands in the page only where the
# punctuator or the structure pass writes it, and engine B's reading of it (a
# glyph such as 丄 or 工 for 1, 八…一 for its brackets) could stand instead.
# Measured on the sealed pages of a 488-page book printing Arabic item,
# contents and heading numbers: the survey of its print counted 95 contents
# numbers lost or misread, 51 bracketed item numbers written as engine B's
# glyphs with reversed brackets, 17 heading numbers misread and 9 written as a
# stray mark.  With the switch the merge carries engine A's figure where the
# figure is the only reading of that print (the merge's walk and
# arabic_figure_kept): engine B read nothing there, or marks, or glyphs this
# book's drafts show engine B writing for A's figures and marks, none a numeral
# (figure_stand_ins, figure_aliases, mark_aliases), or 裁決 read the figure (or
# no character) there (figure_verdict).  And only a figure that continues a numbering: the figure engine A
# read before it is one less, or the one after it one more (figures_confirmed)
# - measured on the same book, engine A read 1 of about 75 heading and item
# figures wrong (a 41 read 44), and that one breaks the numbering on both
# sides - or a contents entry's page number: a figure ending a line that holds a
# leader before it (arabic_figures' "leader").  A contents page's numbers rise by
# pages, not by one: measured on a contents page of 26 names, 8 of its
# numbers continued no numbering, and two pages whose engine A wrote its
# leaders as ASCII dots ("..... (141)") had no figure found at all - one left
# an empty 「（）」 after every entry, the other 「）（」.  No character both
# engines read is replaced.
ARABIC_FIGURE_DIGITS = 3
_MARK_CLASS = re.escape("".join(sorted(PRINTED_MARKS)))
# A gap of engine A's text holding one figure and nothing else but printed
# marks and white space: 1 to ARABIC_FIGURE_DIGITS digits, in brackets or with
# a trailing point as engine A read it.
ARABIC_FIGURE_GAP = re.compile(
    rf"^(?P<lead>[\s{_MARK_CLASS}]*?)(?P<figure>[（(︵]?[0-9]{{1,{ARABIC_FIGURE_DIGITS}}}[）)︶]?\.?)"
    rf"(?P<tail>[\s{_MARK_CLASS}]*)$")
FIGURE_BRACKETS = str.maketrans({"(": "（", "︵": "（", ")": "）", "︶": "）"})


def arabic_figures(blocks: Sequence[Mapping[str, Any]], draft: str,
                   dotted: bool = False) -> list[dict[str, Any]]:
    """Engine A's Arabic figures that may number something (--keep-arabic-numerals),
    in reading order: each alone in a gap of its block's text (ARABIC_FIGURE_GAP)
    in a block of engine A's layout that is no furniture or table
    (FURNITURE_LABELS) - a folio, a library stamp's number or a table's figures
    are not a list's numbers.  Each gives its gap in engine A's stream of
    characters (as CJK counts them), its block and its gap there, the figure as
    written (brackets as full-width brackets) and its value, the marks engine
    A read after it, the brackets it read in the gap besides the figure's
    (a parenthesis closed right before it), and whether it is a contents
    entry's page number ("leader": it ends its line or block, and the line
    holds a leader before it, LEADER_READ; 「东瀛纪事……杨廷理（169）」 too).
    With DOTTED, a run of ASCII dots before the figure is a leader too
    (engine A's "..... (141)"); without it, as before the rule for leaders,
    for the book's numbering sealed in provenance (figure_evidence: so pages
    sealed before stay current).  Empty when the blocks do not spell engine
    A's text, where no gap of a block is a gap of the stream."""
    lengths = [len(CJK(str(block.get("text") or ""))) for block in blocks]
    if not blocks or sum(lengths) != len(CJK(draft)):
        return []
    found, offset = [], 0
    for k, block in enumerate(blocks):
        chars, gaps = engine_gaps(str(block.get("text") or ""))
        if block.get("label") not in FURNITURE_LABELS:
            # Whether the line read so far holds a leader.
            on_line = False
            for j, gap in enumerate(gaps):
                match = ARABIC_FIGURE_GAP.match(LEADER_READ.sub(LEADER, gap) if dotted else gap)
                if match:
                    figure = match.group("figure").translate(FIGURE_BRACKETS)
                    digits = re.sub(r"[^0-9]", "", figure)
                    found.append({"gap": offset + j, "block": k, "at": j, "figure": figure,
                                  "digits": digits, "value": int(digits),
                                  "after": "".join(c for c in match.group("tail") if c in PRINTED_MARKS),
                                  "otherBrackets": dict(Counter(bracket_kinds(match.group("lead")
                                                                              + match.group("tail")))),
                                  "leader": bool((on_line or LEADER_READ.search(match.group("lead")))
                                                 and ("\n" in match.group("tail") or j == len(gaps) - 1))})
                line = gap.rsplit("\n", 1)
                on_line = bool(LEADER_READ.search(line[-1])) or (on_line and len(line) == 1)
        offset += len(chars)
    return found


def figures_confirmed(values: Sequence[int], before: int | None, after: int | None) -> list[bool]:
    """Whether each of a page's figures (arabic_figures), in reading order,
    continues a numbering: the figure engine A read before it - on the page,
    or the last on the pages before (BEFORE) - is one less, or the one after it
    (on the page, or the first on the pages after: AFTER) one more.  Evidence,
    not a rule of the print: a figure no neighbour confirms is not kept (as
    without the switch), and one engine A misread breaks the numbering on
    both sides of it."""
    sequence = [before, *values, after]
    return [(sequence[i] is not None and sequence[i] == value - 1)
            or (sequence[i + 2] is not None and sequence[i + 2] == value + 1)
            for i, value in enumerate(values)]


def figure_neighbours(numbering: Mapping[int, Sequence[int]], page: int) -> tuple[int | None, int | None]:
    """The value of the last figure engine A read on the pages before PAGE, and
    of the first on the pages after (NUMBERING: page -> its figures' values)."""
    before = [numbering[p][-1] for p in sorted(numbering) if p < page and numbering[p]]
    after = [numbering[p][0] for p in sorted(numbering) if p > page and numbering[p]]
    return (before[-1] if before else None), (after[0] if after else None)


def figure_evidence(pairs: Sequence[tuple[int, Path, Path]],
                    aliases: Mapping[str, Any]) -> dict[str, Any]:
    """What --keep-arabic-numerals reads off the whole book's drafts, with no
    model call: each page's figures (arabic_figures; numbering: page -> their
    values, for figures_confirmed), and per glyph of engine B how often it is
    what engine B read in excess where engine A read a confirmed figure and
    nothing else (atFigure) - after the mark rule took out what engine A's
    marks there account for (mark_explanation with the book's ALIASES), at
    most as many glyphs as the figure has signs - or anywhere else (elsewhere),
    as mark_evidence counts a mark's glyphs.  PAIRS are (page, engine A's
    text, engine B's text); engine A's layout is beside its text.  A page
    where either engine looped is left out, as mark_evidence leaves it."""
    numbering: dict[int, list[int]] = {}
    figures: dict[int, list[dict[str, Any]]] = {}
    texts: dict[int, tuple[str, str]] = {}
    for page, path_a, path_b in pairs:
        try:
            text_a = path_a.read_text(encoding="utf-8").strip()
            text_b = strip_grounding(path_b.read_text(encoding="utf-8")) if path_b.is_file() else None
        except OSError:
            continue
        found = arabic_figures(load_blocks(path_a.with_suffix(".json")), text_a)
        figures[page] = found
        numbering[page] = [f["value"] for f in found]
        if text_b is not None:
            texts[page] = (text_a, text_b)
    at_figure, elsewhere = Counter(), Counter()
    for page, (text_a, text_b) in texts.items():
        if any(len(CJK(t)) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(CJK(t)) < DEGENERATE_NGRAM_RATIO
               for t in (text_a, text_b)):
            continue
        before, after = figure_neighbours(numbering, page)
        confirmed = figures_confirmed(numbering[page], before, after)
        sites = {f["gap"]: f for f, ok in zip(figures[page], confirmed) if ok}
        marks_a, marks_b = engine_marks(text_a), engine_marks(text_b)
        a_at = b_at = 0
        for seg in align_drafts(text_a, text_b):
            a, b = seg["a"], seg["b"]
            if seg["tag"] != "equal" and len(b) > len(a):
                reduced = seg
                found = mark_explanation(seg, a_at, b_at, marks_a, marks_b, aliases)
                if found and found[0] in ("A", "B"):
                    reduced = dict(seg, b=seg["a"]) if found[3] == "B" else seg
                elif found:
                    reduced = found[1]
                excess = Counter(reduced["b"]) - Counter(reduced["a"])
                figure = sites.get(a_at) if not a else None
                if figure is not None and len(reduced["b"]) <= len(figure["figure"]):
                    at_figure.update(excess)
                else:
                    elsewhere.update(excess)
            a_at += len(a)
            b_at += len(b)
    return {"numbering": numbering,
            "glyphs": {char: {"atFigure": at_figure[char], "elsewhere": elsewhere[char]}
                       for char in at_figure}}


def figure_aliases(evidence: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """The glyphs engine B is seen to write for engine A's figures on this book
    (figure_evidence): at MARK_ALIAS_MINIMUM confirmed figures or more, and
    more often there than anywhere else - the gates of mark_aliases."""
    return {char: dict(row) for char, row in (evidence.get("glyphs") or {}).items()
            if row["atFigure"] >= MARK_ALIAS_MINIMUM and row["atFigure"] > row["elsewhere"]}


def figure_stand_ins(read: str, figure: Mapping[str, Any], glyphs: Collection[str], brackets_read: int) -> bool:
    """Whether READ, what engine B read where engine A read FIGURE and nothing
    else (once the bracket and mark rules took out what engine A's marks there
    account for), is engine B's reading of the figure (--keep-arabic-numerals):
    the figure's brackets read as strokes (BRACKET_MISREADS, at either end;
    only as many as the figure has brackets that neither the mark rule took
    for them nor engine B printed there itself: BRACKETS_READ), and at most one
    glyph for each of its digits and its point, each one of GLYPHS - the
    glyphs this book's drafts show engine B writing for engine A's figures or
    marks (figure_aliases, mark_aliases) - and none a numeral.  A numeral may
    be the print's own figure in another script, which 裁決 reads off the
    crop (figure_verdict), as without the switch."""
    rest = list(read)
    free = figure["figure"].count("（") + figure["figure"].count("）") - brackets_read
    while free > 0 and rest and rest[0] in BRACKET_MISREADS:
        rest.pop(0)
        free -= 1
    while free > 0 and rest and rest[-1] in BRACKET_MISREADS:
        rest.pop()
        free -= 1
    return (len(rest) <= len(figure["digits"]) + figure["figure"].count(".")
            and all(c in glyphs and c not in NUMERAL_CHARS for c in rest))


def figure_in_gap(gap: str, figure: str, after: str = "", keep: Mapping[str, int] | None = None) -> str:
    """GAP, the text between two characters, with FIGURE written in it: the
    figure's own brackets, where the gap holds them (a punctuator wrote them
    with nothing between them: （）, or ）（), taken out and written around
    the figure; the marks AFTER - those engine A read right after the figure (a heading's 3， or 5.，) -
    that the gap ends with go after the figure, as read; of the rest, the
    marks before the gap's first opening mark go before the figure (a comma,
    the close of a sentence, a heading's markup), the others after it (an
    opening quote).  KEEP counts, by bracket (（ or ）), the brackets of other
    print the gap holds - the bracket rule's (brackets_by_block with the
    figures' own left out), or engine A's own reading of the gap beside the
    figure: only brackets beyond those are the figure's.  A closing bracket
    written just before the figure is the close of the text before it (a
    parenthesis closed right before a list's (2)), not the figure's."""
    marks = list(gap)
    for bracket, forms in (("（", OPEN_BRACKETS), ("）", CLOSE_BRACKETS)):
        spare = sum(1 for c in marks if c in forms) - (keep or {}).get(bracket, 0)
        if bracket in figure and spare > 0:
            del marks[max(i for i, c in enumerate(marks) if c in forms)]
    tail: list[str] = []
    for mark in reversed(after):
        if marks and marks[-1] == mark:
            tail.insert(0, marks.pop())
        else:
            break
    cut = next((i for i, c in enumerate(marks) if c in OPENING_MARKS), len(marks))
    return "".join(marks[:cut]) + figure + "".join(tail) + "".join(marks[cut:])


def figure_written(gap: str, digits: str) -> bool:
    """Whether GAP holds the figure DIGITS (not as part of a longer number)."""
    return re.search(rf"(?<![0-9]){digits}(?![0-9])", gap) is not None


def bracketed_pairs(blocks: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, str]]:
    """Each pair of brackets engine A read between two characters of a block
    that is no furniture or table (FURNITURE_LABELS), a pair of its gap
    (GAP_PAIR), around 1 to ARABIC_FIGURE_DIGITS digits and nothing else, or
    around no letter or digit (a mark - 「以『義氣』(？)相投」, once in twelve
    books - or nothing), beside note markers or markup or not (「」。⑲（2）」).  Not a
    year, a longer number or Latin text (「（1513-95）」, 「（G. W. Skinner）」),
    which the repair's runs stage writes back.  As (its gap in engine A's
    stream of characters, what the page holds of it - a figure's digits, or
    the pair in full-width brackets - the characters around it).  The bracket
    rule does not write such a pair alone (unpaired_brackets): one the page
    does not hold is a doubt (bracketed_unwritten)."""
    found, offset = [], 0
    for block in blocks:
        chars, gaps = engine_gaps(str(block.get("text") or ""))
        if block.get("label") not in FURNITURE_LABELS:
            for j, gap in enumerate(gaps):
                for pair in GAP_PAIR.findall(gap):
                    inside = pair[1:-1].strip()
                    if re.fullmatch(rf"[0-9]{{1,{ARABIC_FIGURE_DIGITS}}}", inside) or not re.search(r"[^\W_]", inside):
                        written = f"（{inside}）"
                        found.append((offset + j, written if not inside.isdigit() else inside,
                                      f"…{chars[max(0, j - 4):j]}[{written}]{chars[j:j + 4]}…"))
        offset += len(chars)
    return found


def bracketed_unwritten(bracketed: Sequence[tuple[int, str, str]], kept: Sequence[Mapping[str, Any]],
                        text: str, why: Mapping[int, str]) -> list[str]:
    """The pairs of BRACKETED (bracketed_pairs) that the page TEXT does not
    hold, each as the characters around it and WHY the merge did not keep its
    figure (by gap).  A figure the merge kept (KEPT: its gap and figure) is
    written; another pair is held where TEXT holds a copy of it - a figure's
    digits, the punctuator's or the structure pass's, or the pair as written -
    counted page-wide once the figures the merge kept have taken theirs."""
    held = Counter(re.findall(r"[0-9]+", text)
                   + [f"（{inside.strip()}）" for inside in re.findall(r"（([^（）\w]*)）", text)])
    for figure in kept:
        digits = re.sub(r"[^0-9]", "", figure["figure"])
        held[digits] -= held[digits] > 0
    kept_at = {figure["gap"] for figure in kept}
    lost = []
    for gap, key, context in bracketed:
        if gap in kept_at:
            continue
        if held[key] > 0:
            held[key] -= 1
        else:
            lost.append(context + (f" ({why[gap]})" if gap in why else ""))
    return lost


# How place_arabic_figures left a figure: standing at its gap (the text the
# structure pass is given holds it there), in its block (at its gap or the
# punctuator's copy near it), and written by the switch (the figure, or its
# brackets around the punctuator's copy).
FIGURE_AT_GAP = ("written", "already", "already-bracketed")
FIGURE_IN_BLOCK = FIGURE_AT_GAP + ("elsewhere", "elsewhere-bracketed")
FIGURE_WRITTEN = ("written", "already-bracketed", "elsewhere-bracketed")


def place_arabic_figures(text: str, span: str, figures: Sequence[tuple[Any, ...]]) -> tuple[str, list[str]]:
    """TEXT, a punctuated block whose characters are SPAN, with each (gap,
    figure[, the marks engine A read after it[, the brackets of other print
    there]]) of FIGURES written at its gap (figure_in_gap), and for each how:
    "written"; "already", where the gap holds its digits; "elsewhere", where
    the block holds a copy of its digits within NEARBY_WINDOW characters of the
    gap (the punctuator wrote the figure a gap or a line away, and it is not
    written twice); "unplaced" where TEXT's characters are not SPAN.  The
    bracket rule leaves a figure's brackets to it (brackets_by_block own), so
    a copy of the punctuator's that lacks the brackets engine A read around
    the figure gets them, next to its digits ("already-bracketed",
    "elsewhere-bracketed"): else the page has the figure's digits and not
    its brackets, where without the switch the bracket rule wrote them."""
    chars, gaps = engine_gaps(text)
    if chars != CJK(span):
        return text, ["unplaced"] * len(figures)
    how = [""] * len(figures)
    digits_of = [re.sub(r"[^0-9]", "", figure[1]) for figure in figures]
    copy_at: dict[int, int] = {}
    for k, (gap, *_) in enumerate(figures):
        if not 0 <= gap < len(gaps):
            how[k] = "unplaced"
        elif figure_written(gaps[gap], digits_of[k]):
            how[k] = "already"
            copy_at[k] = gap
    # Copies of a figure's digits the block holds within NEARBY_WINDOW
    # characters of its gap, at no gap that holds them as a figure written
    # there: the punctuator's for that figure, a gap or a line away (a
    # contents entry's number written after its title).  Each stands for one
    # figure to write.
    taken = {figures[k][0] for k in range(len(figures)) if how[k] == "already"}
    spare = [[g, digits] for g, gap in enumerate(gaps) if g not in taken
             for digits in re.findall(r"[0-9]+", gap)]
    for k in sorted(range(len(figures)), key=lambda k: figures[k][0]):
        if how[k]:
            continue
        near = next((copy for copy in spare if copy[1] == digits_of[k]
                     and abs(copy[0] - figures[k][0]) <= NEARBY_WINDOW), None)
        if near is not None:
            spare.remove(near)
            how[k] = "elsewhere"
            copy_at[k] = near[0]
        else:
            how[k] = "written"
    # Which of each copied figure's brackets the punctuator wrote at the
    # figure's gap, beyond the brackets of other print there (KEEP): those
    # are the figure's, wherever in the gap they stand.
    held = {k: {bracket: sum(1 for c in gaps[figures[k][0]] if c in forms)
                > (figures[k][3] if len(figures[k]) > 3 else {}).get(bracket, 0)
                for bracket, forms in (("（", OPEN_BRACKETS), ("）", CLOSE_BRACKETS))}
            for k in copy_at}
    for k in sorted(range(len(figures)), key=lambda k: -figures[k][0]):
        if how[k] == "written":
            gaps[figures[k][0]] = figure_in_gap(gaps[figures[k][0]], *figures[k][1:])
    for k, g in copy_at.items():
        # The punctuator's copy of the figure: the figure's brackets it wrote
        # neither at the figure's gap nor beside the copy's digits, written
        # beside them (the point after them stays after the close).
        found = re.search(rf"(?<![0-9]){digits_of[k]}(?![0-9])", gaps[g])
        figure = figures[k][1]
        if found is None:
            continue
        s, e = found.span()
        opening = ("（" in figure and not held[k]["（"]
                   and not (s and gaps[g][s - 1] in OPEN_BRACKETS))
        closing = ("）" in figure and not held[k]["）"]
                   and not gaps[g][e:e + 1] in CLOSE_BRACKETS)
        if opening or closing:
            gaps[g] = (gaps[g][:s] + ("（" if opening else "") + gaps[g][s:e] + ("）" if closing else "")
                       + gaps[g][e:])
            how[k] += "-bracketed"
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out), how


def restore_arabic_figures(given: str, formatted: str, figures: Sequence[tuple[Any, ...]]) -> tuple[str, list[str]]:
    """FORMATTED (the structure pass's page) with each (gap, figure[, marks
    after it]) of FIGURES
    the text GIVEN to it held - gap k before GIVEN's character k as CJK counts
    them - that FORMATTED no longer holds, written back at the same place
    (figure_in_gap): "kept" where its gap still holds its digits, "moved"
    where the page holds as many copies of them as GIVEN, "restored", or
    "unplaced" where neither character beside the gap is in FORMATTED.  The
    copies are counted gap by gap: two figures one character apart are two
    numbers, not one (a 1 moved next to the 2 of 甲乙1丙2丁 is still there).
    Only brackets beyond those GIVEN held there besides the figure's own are
    taken for the figure's (figure_in_gap's KEEP)."""
    source, target = CJK(given), CJK(formatted)
    matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
    where: dict[int, int] = {}
    for i, j, n in matcher.get_matching_blocks():
        for d in range(n):
            where[i + d] = j + d
    chars, gaps = engine_gaps(formatted)
    given_gaps = engine_gaps(given)[1]
    how = [""] * len(figures)
    counts_given = Counter(re.findall(r"[0-9]+", given))
    for k in sorted(range(len(figures)), key=lambda k: -figures[k][0]):
        gap, figure = figures[k][:2]
        digits = re.sub(r"[^0-9]", "", figure)
        at = (where[gap] if gap in where else where[gap - 1] + 1 if gap - 1 in where else None)
        if at is None:
            how[k] = "unplaced"
        elif figure_written(gaps[at], digits):
            how[k] = "kept"
        elif sum((Counter(re.findall(r"[0-9]+", g)) for g in gaps), Counter())[digits] >= counts_given[digits]:
            how[k] = "moved"
        else:
            held = Counter(bracket_kinds(given_gaps[gap] if 0 <= gap < len(given_gaps) else ""))
            keep = {bracket: max(0, held[bracket] - figure.count(bracket)) for bracket in "（）"}
            gaps[at] = figure_in_gap(gaps[at], figure, *(figures[k][2:3] or [""]), keep=keep)
            how[k] = "restored"
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out), how


# A printed note marker as the punctuation pass writes it: a circled number.
NOTE_MARK = re.compile(f"[{_note_markers.NOTE_CIRCLED}]")


# The structure pass's input is sealed whole up to this many characters
# (structure_input_record); a page's text is seldom a fifth of it.
STRUCTURE_INPUT_SEALED = 20000


def structure_input_record(text: str) -> dict[str, Any]:
    """The seal's record of TEXT, the text the structure pass was given
    (its question's text between the --- lines: the punctuated blocks, a
    table as its ⟦Tn⟧ token): the text itself, cut at STRUCTURE_INPUT_SEALED
    characters ("truncated"), its length and the sha256 of the whole.  A page
    sealed without it showed what came out of the pass but not what went in:
    measured on the benchmark (2026-11-15), a note's label both engines read
    was missing from a sealed page, and whether the structure pass dropped it
    or was never given it could only be argued from its restore not firing
    (it had never reached the pass: place_note_markers)."""
    return {"sha256": sha256_bytes(text.encode("utf-8")), "characters": len(text),
            "text": text[:STRUCTURE_INPUT_SEALED],
            **({"truncated": True} if len(text) > STRUCTURE_INPUT_SEALED else {})}


def marker_cut(here: str, before: str, after: str, line_start: bool) -> int:
    """Where in HERE, the gap a note marker is written into, the marker goes:
    beside the mark that follows it where it was read (AFTER, up to its line
    break: 「②《」), else beside the one before it on its own line (BEFORE's
    part after its last line break), else after HERE's last line break where
    it begins a line, else next to the character it goes with (a line's
    first marker before the character after it, any other after the
    character before).  A line's first marker is never put after the mark
    that ends the line before: a note's label there stands on the wrong
    note's line (「頁。\\n②某書」 had come back 「頁。②\\n\\n某書」)."""
    after = after.split("\n", 1)[0]
    tail = next((c for c in after if not c.isspace()), None) if not CJK(after[:1]) else None
    head = next((c for c in reversed(before.rsplit("\n", 1)[-1]) if not c.isspace()), None)
    if tail is not None and tail in here:
        return here.index(tail)
    if head is not None and head in here:
        return here.rindex(head) + 1
    if "\n" in before and "\n" in here:
        return here.rindex("\n") + 1
    return len(here) if line_start else 0


# How many of a line's first characters, variant-folded, a note block's text
# must hold for the line to be that block's (note_labels).
NOTE_LINE_ANCHOR = 4
# Engine A's labels for a block of notes (note_block_texts).
NOTE_BLOCK_LABELS = frozenset({"footnote", "vision_footnote"})


# Issue #8, item 4: where the rules leave a page's note numbers unsettled, the
# answerer reads them off the page (note-numbers): each note's printed number,
# the first characters of its text, and the body's characters right before
# its call.  The repair's N9 (_repair_notes) takes the answer as one more
# reading beside the engines', and finds a call the engines missed by the
# characters before it.  Asked only where the page prints notes (engine A's
# note blocks, or a line an engine opens with a note number) and the engines
# read more note numbers than the page holds, or one wrote a run of circled
# figures (CIRCLED_RUN: a two- or three-figure number in one circle).
NOTE_NUMBERS_SYSTEM = """你係古籍校對員。畀你一張完整書頁。頁底（或者表格下面）印咗註，正文入面印咗對應嘅註號：圈號、上標數字，可能係兩三位數（例如圈入面印 72、240）。

逐條註答：
- number：條註前面印嘅編號，寫阿拉伯數字（圈入面兩三位數照寫，例如 72、240）。
- note：條註開頭嘅頭幾個字，照印，最多十二個字，唔使標點。
- before：正文入面呢個註號前面緊貼嘅幾個字，照印，最多十二個字，連標點。正文搵唔到佢個註號就寫空字串。

淨係答 JSON：{"notes": [{"number": "72", "note": "...", "before": "..."}]}。頁上冇註就答 {"notes": []}。睇唔清嘅編號唔好寫，唔好由前後編號推斷。"""
NOTE_NUMBERS_USER = "列出呢頁每一條註嘅編號、開頭幾個字，同正文入面佢個註號前面嘅字。"
NOTE_NUMBERS_MAX_TOKENS = 16000
NOTE_NUMBERS_LIMIT = 60
NOTE_NUMBERS_TEXT = 24


def note_question_reason(draft: str | None, draft2: str | None, text: str,
                         blocks: Sequence[Mapping[str, Any]]) -> str | None:
    """Why the page's note numbers go to the answerer (note-numbers), or None."""
    def count(t: str | None) -> int:
        return sum(1 for m in _note_markers.iter_note_markers(t or "") if _note_markers.marker_key(m.group()))

    def heads(t: str | None) -> bool:
        return any(_note_markers.NOTE_LINE_RE.match(line.lstrip()) for line in (t or "").splitlines())

    if not (note_block_texts(blocks) or heads(draft) or heads(draft2)):
        return None
    if CIRCLED_RUN.search(draft or "") or CIRCLED_RUN.search(draft2 or ""):
        return "an engine wrote a note number as a run of circled figures"
    read, held = max(count(draft), count(draft2)), count(text)
    return f"the engines read {read} note numbers, the page holds {held}" if read > held else None


def parse_note_numbers(raw: str | None) -> list[dict[str, str]] | None:
    """The note-numbers answer as [{"number", "note", "before"}], numbers of
    one to three figures; None where it is no such JSON."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
        return None
    out = []
    for item in data["notes"][:NOTE_NUMBERS_LIMIT]:
        if not isinstance(item, dict):
            continue
        number = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(item.get("number") or "")))
        if not number or len(number) > 3 or int(number) < 1:
            continue
        out.append({"number": str(int(number)),
                    "note": str(item.get("note") or "").strip()[:NOTE_NUMBERS_TEXT],
                    "before": str(item.get("before") or "").strip()[:NOTE_NUMBERS_TEXT]})
    return out


def ask_note_numbers(client: "Client", render_bytes: bytes, reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The note-numbers question on the whole page: the record N9 reads
    ({"reason", "notes", "parsed"}) and the call's usage."""
    raw, used = client.ask_answering(NOTE_NUMBERS_SYSTEM, NOTE_NUMBERS_USER, render_bytes, NOTE_NUMBERS_MAX_TOKENS,
                                     kind="note-numbers")
    notes = parse_note_numbers(raw)
    return {"reason": reason, "notes": notes or [], "parsed": notes is not None}, used


def note_block_texts(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """The text of the page's note blocks: those engine A labelled a note
    (NOTE_BLOCK_LABELS), and those in a folio's margin band (margin_blocks),
    where a page's footnotes sit (note_labels)."""
    margin = {id(block) for block in margin_blocks(blocks)}
    return [str(block.get("text") or "") for block in blocks
            if block.get("label") in NOTE_BLOCK_LABELS or id(block) in margin]


def note_labels(given: str, notes: Sequence[str] = ()) -> set[int]:
    """Where GIVEN holds a circled note marker (NOTE_MARK) that is a note's
    label by evidence: it begins a line of GIVEN (nothing but marks before it
    since the line break), and GIVEN holds a marker of the same number that
    begins no line - its call - or the line it begins is the text of one of
    NOTES, the page's note blocks (its first NOTE_LINE_ANCHOR characters,
    variant-folded, stand in one).  The offsets are the markers' starts in
    GIVEN.

    A marker at a line's head is not a label by its place alone: the
    punctuation pass, or engine A's line break, can put a call there
    (FX3 review, made-up page: a call the structure answer set after its
    character, at the end of the line before, was moved to the head of
    the next paragraph, which finishing then read as a note)."""
    found = [m for m in _note_markers.iter_note_markers(given) if NOTE_MARK.search(m.group())]
    heads = {m.start() for m in found if not CJK(given[given.rfind("\n", 0, m.start()) + 1:m.start()])}
    calls = {_note_markers.marker_key(m.group()) for m in found if m.start() not in heads}
    folded = [variant_fold(CJK(text)) for text in notes]
    labels = set()
    for m in found:
        if m.start() not in heads:
            continue
        line = given[m.end():].split("\n", 1)[0]
        lead = variant_fold(CJK(line))[:NOTE_LINE_ANCHOR]
        if _note_markers.marker_key(m.group()) in calls or (
                len(lead) >= 2 and any(lead in text for text in folded)):
            labels.add(m.start())
    return labels


def restore_note_markers(given: str, formatted: str,
                         notes: Sequence[str] = ()) -> tuple[str, list[dict[str, Any]]]:
    """FORMATTED (the structure pass's answer) with each note marker of GIVEN
    (the text it was given) that it no longer holds written back where GIVEN
    has it, and what was written back: one record per marker, with its gap
    (GIVEN's character k as CJK counts them, before it), the marker and the
    characters around it.

    A note marker is print the merge leaves out (it holds characters alone):
    the punctuation pass writes it back off the crop (agreed_marks: a note
    marker is other print), or the merge where that pass left out one both
    engines read (place_note_markers), and the structure pass, told to keep
    every mark, must keep it.  Nothing held it there: verify_characters counts a circled
    number as its figure, which a block in the folio's margin band may lose
    as furniture (removable_text) - a page's footnotes sit there - and
    furniture_unexplained counts CJK.  Measured on the benchmark (2026-11-15):
    a page whose two notes, both in the margin band, begin with the labels ①
    and ②, sealed with the first label alone, so only [^1] was paired and
    the two notes came out swapped (45 errors); the ② had not reached the
    text given (place_note_markers puts it there now).  Only circled numbers
    (NOTE_MARK), counted by their figures (marker_key: an answer that writes
    ② as [^2] or <sup>2</sup> keeps it, and so does one whose gap holds its
    figure written another way, 「(2)」); a marker is written back beside the
    mark GIVEN has after it in its gap (「②《」), else beside the one before
    it on its own line, else at a line's start where GIVEN has it there, else
    next to the character it goes with (marker_cut: a line's first marker
    before the character after it, in a table cell too: 「| 甲 | ①乙 |」);
    not where the character it goes with - the one after it at a line's
    start, else the one before - is not in FORMATTED (the line taken out as
    furniture).

    A marker that begins a line of GIVEN and is a note's label by evidence
    (note_labels: GIVEN also holds its call, or the line is the text of one
    of NOTES, the page's note blocks) is kept only at the head of its line:
    one the answer set before the line break that begins it - at the end of
    the line before, or on a line of its own, more copies of it there than
    GIVEN has - is moved back after that break (a record with "moved"),
    since finishing pairs a note by the label that begins its line
    (_footnotes).  Any other marker is written back only where the answer
    left it out, as before: a call the answer set beside its character is
    not moved to a paragraph's head."""
    wanted = [m for m in _note_markers.iter_note_markers(given) if NOTE_MARK.search(m.group())]
    if not wanted:
        return formatted, []
    keys = Counter(_note_markers.marker_key(m.group()) for m in _note_markers.iter_note_markers(formatted))
    missing = Counter(_note_markers.marker_key(m.group()) for m in wanted) - keys
    labels = note_labels(given, notes)
    if not missing and not labels:
        return formatted, []
    source, target = CJK(given), CJK(formatted)
    where: dict[int, int] = {}
    for i, j, n in difflib.SequenceMatcher(None, source, target, autojunk=False).get_matching_blocks():
        for d in range(n):
            where[i + d] = j + d
    chars, gaps = engine_gaps(formatted)
    starts = [0]
    for at, char in enumerate(given):
        if CJK(char):
            starts.append(at + 1)
    restored = []
    for m in wanted:
        key = _note_markers.marker_key(m.group())
        gap = len(CJK(given[:m.start()]))
        before, after = given[starts[gap]:m.start()], given[m.end():]
        after = after[:after.find("\n")] if "\n" in after else after
        # A marker that begins a line goes with the character after it, any
        # other with the character before it: it is written back only where
        # FORMATTED still holds that character, and next to it when nothing
        # else in the gap places it.
        line_start = gap == 0 or "\n" in before
        if line_start:
            at = where.get(gap)
        else:
            at = where[gap - 1] + 1 if gap - 1 in where else None
        if at is not None and m.start() in labels and "\n" in before and "\n" in gaps[at]:
            # A note's label: at the head of its line, after the gap's last
            # line break, or moved there from before it.
            head = gaps[at].rindex("\n") + 1
            pre, post = gaps[at][:head], gaps[at][head:]
            if key in re.sub(r"\D", "", unicodedata.normalize("NFKC", post)):
                continue
            set_before = [found for found in _note_markers.iter_note_markers(pre)
                          if _note_markers.marker_key(found.group()) == key]
            moved = len(set_before) > sum(1 for found in _note_markers.iter_note_markers(
                before[:before.rindex("\n")]) if _note_markers.marker_key(found.group()) == key)
            if moved:
                drop = set_before[-1]
                pre = re.sub(r"\n{3,}", "\n\n", pre[:drop.start()] + pre[drop.end():])
            elif missing[key] <= 0:
                continue
            else:
                # Left out: the copies before the break are the line
                # before's own, as GIVEN has them there.
                missing[key] -= 1
            cut = marker_cut(post, before, after, True)
            gaps[at] = pre + post[:cut] + m.group() + post[cut:]
            restored.append({"gap": gap, "marker": m.group(), "contextBefore": source[max(0, gap - 8):gap],
                             "contextAfter": source[gap:gap + 8], **({"moved": True} if moved else {})})
            continue
        if missing[key] <= 0:
            continue
        # Kept there in any form: as a marker, or its figure written another
        # way (「(2)」).
        if at is None or key in re.sub(r"\D", "", unicodedata.normalize("NFKC", gaps[at])):
            continue
        here = gaps[at]
        cut = marker_cut(here, before, after, line_start)
        gaps[at] = here[:cut] + m.group() + here[cut:]
        missing[key] -= 1
        restored.append({"gap": gap, "marker": m.group(), "contextBefore": source[max(0, gap - 8):gap],
                         "contextAfter": source[gap:gap + 8]})
    out = [gaps[0]]
    for char, gap_text in zip(chars, gaps[1:]):
        out += [char, gap_text]
    return "".join(out), restored


def engine_note_markers(text: str, place: Mapping[int, int] | None) -> list[dict[str, Any]] | None:
    """The circled note markers (NOTE_MARK, alone or in an engine's LaTeX or
    <sup> around them) an engine read, TEXT, each tied to the merged text by
    PLACE, its characters' exact places there (engine_places,
    engine_b_places): {"gap": the merged text's gap, "marker": its circled
    figures, "raw": as the engine wrote it, "lineStart", "before" and
    "after": the print of its gap on either side of it}.  A marker that
    begins a line (or the text) goes with the character after it, any other
    with the character before it, as restore_note_markers takes them; one
    whose character has no exact place is left out.  None when PLACE is."""
    if place is None:
        return None
    chars, gaps = engine_gaps(text)
    found = []
    for k, gap in enumerate(gaps):
        for m in _note_markers.iter_note_markers(gap):
            figures = "".join(NOTE_MARK.findall(m.group()))
            key = _note_markers.marker_key(figures) if figures else ""
            if not key or key != _note_markers.marker_key(m.group()) or not int(key):
                continue
            before, after = gap[:m.start()], gap[m.end():]
            line_start = k == 0 or "\n" in before
            if line_start and k < len(chars):
                at = place.get(k)
            else:
                at = place[k - 1] + 1 if k > 0 and k - 1 in place else None
            if at is not None:
                found.append({"gap": at, "marker": figures, "raw": m.group(), "lineStart": line_start,
                              "before": before, "after": after})
    return found


# Circled figures an engine wrote in a run (①⑨, ⑲⑳), or one with a digit
# after it (②0): the page prints note numbers past what one circled glyph
# holds (a three-figure number in one circle), which the engines read as such
# runs or cut short (recover_tables' table-note-marker-unverified).
CIRCLED_RUN = re.compile(f"[{_note_markers.NOTE_CIRCLED}]{{2,}}|[{_note_markers.NOTE_CIRCLED}][0-9０-９]")


def agreed_note_markers(draft: str, draft2: str | None, segments: Sequence[Mapping[str, Any]],
                        pieces: Sequence[str], blocks: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """The circled note markers both engines read at one place of the merged
    text ("".join(pieces)): engine A's (engine_note_markers, its "lineStart",
    "before" and "after"), each with engine B's form ("rawB"), where engine B
    read the same circled figures at the same gap; as many at a gap as the
    engine that read fewer there.  Nothing where either engine's characters
    do not spell the segments (engine_places, engine_b_places), nor on a page
    where either engine wrote a run of circled figures (CIRCLED_RUN): there a
    single circled glyph both read can be a longer number cut short (both
    engines read ⑲ at the head of a note numbered 197 on a page whose other
    labels came back as ⑲⑳ and ②0)."""
    if draft2 is None or CIRCLED_RUN.search(draft) or CIRCLED_RUN.search(draft2):
        return []
    found_a = engine_places(engine_gaps(draft)[0], segments, pieces, blocks)
    read_a = engine_note_markers(draft, found_a[0] if found_a else None)
    read_b = engine_note_markers(draft2, engine_b_places(engine_gaps(draft2)[0], segments, pieces))
    if not read_a or not read_b:
        return []
    by_place: dict[tuple[int, str], list[str]] = {}
    for found in read_b:
        by_place.setdefault((found["gap"], found["marker"]), []).append(found["raw"])
    agreed = []
    for found in read_a:
        other = by_place.get((found["gap"], found["marker"]))
        if other:
            agreed.append(dict(found, rawB=other.pop(0)))
    return agreed


# How many gaps from where both engines read a note marker one the
# punctuation pass wrote may stand and still be that marker
# (place_note_markers).
NOTE_MARKER_REACH = 2


def place_note_markers(text: str, span: str,
                       wanted: Sequence[tuple[int, Mapping[str, Any]]]) -> tuple[str, list[tuple[int, Mapping[str, Any]]]]:
    """TEXT, a punctuated block, with each circled note marker both engines
    read in it (WANTED: its gap in the block's characters SPAN, gap k before
    character k, and the marker as agreed_note_markers gives it) that TEXT
    does not hold, written at its gap (marker_cut, beside the marks the
    engine read around it), and the markers written.

    The merge holds characters alone, so a note marker stands in the page
    only where the block's punctuation pass writes it off the crop; nothing
    else wrote one, and a pass that left a printed label out lost it for
    good - the structure pass is given no label to keep, and its restore
    (restore_note_markers) takes them from that text.  Measured on the
    benchmark (2026-11-15): a page's two notes each begin with a circled
    label both engines read; the pass wrote the first and left the second
    out (the same answer, token for token, from two skills), so only one
    note was paired and the two came out swapped.  A marker is counted by
    its figures (marker_key), so one the pass wrote in any form is not
    written again; and each wanted marker is first paired with a marker of
    the same number the pass wrote within NOTE_MARKER_REACH gaps of it,
    nearest first, so one written a place or two off stands for it and the
    one left out is written at its own gap (FX3 review, made-up block: the
    same number at two gaps, one written a place off and the other left
    out, had a second copy written beside the first).  Nor is one written
    where the gap holds its figure written another way (「(2)」).  The
    characters are never touched; a TEXT whose characters are not SPAN's
    is returned as it is."""
    chars, gaps = engine_gaps(text)
    if not wanted or chars != CJK(span):
        return text, []
    found_in = [(len(CJK(text[:m.start()])), _note_markers.marker_key(m.group()))
                for m in _note_markers.iter_note_markers(text)]
    missing = (Counter(_note_markers.marker_key(found["marker"]) for _, found in wanted)
               - Counter(key for _, key in found_in))
    ordered = sorted(wanted, key=lambda item: item[0])
    paired: set[int] = set()
    taken: set[int] = set()
    for _, i, j in sorted((abs(g - at), i, j) for i, (g, found) in enumerate(ordered)
                          for j, (at, key) in enumerate(found_in)
                          if key == _note_markers.marker_key(found["marker"]) and abs(g - at) <= NOTE_MARKER_REACH):
        if i not in paired and j not in taken:
            paired.add(i)
            taken.add(j)
    written = []
    for i, (g, found) in enumerate(ordered):
        key = _note_markers.marker_key(found["marker"])
        if i in paired or missing[key] <= 0 or not 0 <= g < len(gaps):
            continue
        here = gaps[g]
        if key in re.sub(r"\D", "", unicodedata.normalize("NFKC", here)):
            continue
        cut = marker_cut(here, found["before"], found["after"], found["lineStart"])
        gaps[g] = here[:cut] + found["marker"] + here[cut:]
        missing[key] -= 1
        written.append((g, found))
    if not written:
        return text, []
    out = [gaps[0]]
    for char, gap_text in zip(chars, gaps[1:]):
        out += [char, gap_text]
    return "".join(out), written


def note_markers_by_block(agreed: Sequence[Mapping[str, Any]],
                          spans: Sequence[str]) -> dict[int, list[tuple[int, Mapping[str, Any]]]]:
    """AGREED (agreed_note_markers) by block: block index -> (gap in the
    block's span, marker).  A marker goes to the block of the character it
    goes with: a line's first marker to the block it begins, any other to
    the block it ends or stands in."""
    ends = list(itertools.accumulate(len(span) for span in spans))
    total = ends[-1] if ends else 0
    per_block: dict[int, list[tuple[int, Mapping[str, Any]]]] = {}
    for found in agreed:
        if not total:
            break
        anchor = min(max(found["gap"] if found["lineStart"] else found["gap"] - 1, 0), total - 1)
        k = bisect.bisect_right(ends, anchor)
        start = ends[k - 1] if k else 0
        if 0 <= found["gap"] - start <= len(spans[k]):
            per_block.setdefault(k, []).append((found["gap"] - start, found))
    return per_block


def arabic_figure_kept(page_figures: Mapping[int, Sequence[Mapping[str, Any]]],
                       segments: Sequence[Mapping[str, Any]], starts: Sequence[int], pieces: Sequence[str],
                       after_rules: Mapping[int, Mapping[str, Any]],
                       settled: Mapping[int, tuple[str, bool]],
                       blocks: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Which of engine A's figures on a page the merge writes
    (--keep-arabic-numerals), once every piece is settled: (the figures kept,
    each with its gap in the merged text; every figure, kept or not and why,
    for the page's record).  A figure is kept where it is confirmed
    (figures_confirmed, or a contents entry's page number: arabic_figures'
    "leader"), alone at its gap, and the only reading of its print:
    the gap lies in a stretch both engines read alike, or between two
    readings each engine read as many characters of once the bracket and mark
    rules took out what engine A's marks there account for (AFTER_RULES),
    and whatever engine B read at the gap itself the figure's rules settled
    as no text (SETTLED: its brackets or marks, its stand-in glyphs, or 裁決's
    answer that it is the figure or no character; and whether the merge
    would write characters there without the figure).  PAGE_FIGURES maps a gap
    of engine A's stream to the figures there (arabic_figures)."""
    chars = "".join(x["a"] for x in segments)
    kept, seen = [], []
    for g in sorted(page_figures):
        figures = page_figures[g]
        how, engine_b, replaced, why = "nothing", "", False, None
        if len(figures) > 1:
            why = "two figures at one place"
        elif not figures[0]["confirmed"]:
            why = "numbering not continued"
        for i, (x, s) in enumerate(zip(segments, starts)):
            la = len(x["a"])
            if why or s > g:
                break
            if s + la < g or (x["tag"] == "equal" and "window" not in x and not x.get(LOOPED_READING)):
                continue
            if "window" in x or x.get(LOOPED_READING):
                why = "an order-conflict window or a looped block"
            elif not la and s == g:
                if i not in settled:
                    why = "engine B's reading there stands"
                else:
                    (how, replaced), engine_b = settled[i], x["b"]
            elif la and s < g < s + la:
                why = "inside a replacement"
            elif la and (s == g or s + la == g):
                y = after_rules.get(i)
                if y is None or not y["a"] or len(y["a"]) != len(y["b"]):
                    why = "beside a replacement of unequal readings"
        figure = figures[0]
        entry = {"figure": figure["figure"], "value": figure["value"],
                 "block": layout_index(blocks, figure["block"]),
                 "contextBefore": chars[max(0, g - 8):g], "contextAfter": chars[g:g + 8],
                 "confirmed": figure["confirmed"], **({"leader": True} if figure.get("leader") else {})}
        if why:
            seen.append({**entry, "kept": False, "why": why})
            continue
        merged = 0
        for x, s, piece in zip(segments, starts, pieces):
            la = len(x["a"])
            if s + la < g or (la and s + la == g) or (not la and s < g):
                merged += len(CJK(piece))
                continue
            if la and s < g:
                merged += g - s
            break
        seen.append({**entry, "kept": True, "how": how, **({"engineB": engine_b} if engine_b else {})})
        kept.append({**figure, "merged": merged, "how": how, "engineB": engine_b, "replaced": replaced,
                     "record": seen[-1]})
    return kept, seen


def figure_evidence_seal(arguments: Any) -> str:
    """The seal of what --keep-arabic-numerals read off the book's drafts: each
    page's figures and the glyphs taken for engine B's readings of them."""
    return sha256_bytes(canonical_json_bytes({
        "numbering": {str(page): values for page, values in
                      ((getattr(arguments, "figure_evidence", None) or {}).get("numbering") or {}).items()},
        "aliases": sorted(getattr(arguments, "figure_aliases", None) or {})}))


def figure_verdict(record: Mapping[str, Any], figure: Mapping[str, Any]) -> str | None:
    """What 裁決's answer (its sealed RECORD) says of engine B's reading where
    engine A read FIGURE (--keep-arabic-numerals): "figure" where the answer
    is the figure's digits - in brackets or with a point, as adjudicate_segment
    parses an answer - and was not used (a figure is no character:
    engine-A-default); "nothing" where it answered that no character is
    printed there and that stood (adjudicator-empty); else None - an answer
    that reads a character there, or that was refused, leaves the merge as it
    is without the switch."""
    if record.get("resolved_from") == "adjudicator-empty":
        return "nothing"
    if record.get("resolved_from") != "engine-A-default":
        return None
    answer = verdict_answer(str(record.get("verdict") or "")) or ""
    found = re.fullmatch(r"([0-9]+)\.?", unicodedata.normalize("NFKC", answer))
    return "figure" if found and found.group(1) == figure["digits"] else None
