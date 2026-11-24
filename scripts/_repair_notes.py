"""Rule N9 of repair_book.py (the notes stage): the numbers of a page's
notes and of their calls, where the book numbers its notes on from page to
page, written back so that finalize (_footnotes.convert) can pair them.

Why: phase 3 merges the engines on CJK characters, so a note number stands
on the page only where a rule writes it back, and the runs stage writes a
note call only in its place in a numbering that runs 1, 2, 3 ... from the
page's first call (_repair_runs.numbered).  A book that numbers its notes on
prints circled figures with two or three digits (64, 72, 240), for which
Unicode has no glyph past 50, and the engines read each its own way:
measured on the bench copies of the modern book (jyut-ocr-bench, 2026-11-18,
pages 75, 77 and 327), engine A writes ⑥④ or ⑥5 or ⑦3 (the digits as
circled glyphs, or a circled glyph and plain figures), ^{66} or a lone ⑧
for a call, ⑳ for 240; engine B writes ^{64} ... ^{69} for the calls,
⑥④ ... ⑥⑧ for the notes, $ \\textcircled{240} $, and on page 77 \\textcircled{2},
{3}, {4} for the calls 72-74 and {7}, {8}, {9} for their notes.  No rule
wrote any of them, so none of the 21 notes on those pages became a
footnote, for every model proofreading them (Gemini, Claude Haiku and two
Qwen models alike: note F1 21-28 on the bench).

N9 reads, on each page as the runs stage leaves it, every note marker the
engines read where they are aligned to the page: in a line (a call), or
opening a line (a note: the line of the next page character, or a whole
line of the gap, a Western note, matched to the page's line by its text).
Each reading is a number: circled glyphs by their values one after another
(⑥④ 64), with plain or superscript figures right after them (⑦3, ⑦⁴), a
superscript or LaTeX marker by marker_key (^{66}, \\textcircled{240}; engine
B's \\t that arrived as a tab too); at most three digits.  A reading is a
truncation of a number when it is the number with one digit left out
(engine B's 4 for 74, engine A's 20 for 240).

The page's notes are settled first, as one run of consecutive numbers: the
start most slots read exactly, ties broken by how many readings of either
engine give the number exactly or truncated (page 77: engine B's notes 7, 8,
9 run as exactly as engine A's 72-74, and only 72 accounts for both), at
least two exact slots and both engines behind the run at one slot at least
(the bench's table pages: engine A ⑫ ⑬ where 193-205 are printed, engine
B 19 at both: left); one note alone where both engines read it, each its
number or a truncation of it.  An exact tie between two runs is left.  The
page's calls take the notes' run where every call reads it at least
truncated, else are settled as the notes are.  Nothing is written on a
page whose notes do not settle, whose calls and notes settle on numbers
that share none, where a note of the run would stay without its number
(finalize would make the others footnotes and leave it a paragraph among
them, which the bench scores worse than none converted), or where no note
would find its call.

A slot is written where an engine reads its number exactly or truncated:
as one circled glyph up to 50 (the print's), and past 50 as circled digits
(⑦②, ②④⓪), which marker_key reads as the number; a call where the
engines read it among the gap's marks (the page's marks and runs there the
engine's, else nothing is written), a note at the start of its line.  A
marker the page holds there stays, but one N9 reads as the number and
marker_key does not (⑦3, ⑦⁴, as N1 or the model wrote it) is written over.
A slot written from a truncation alone is a `note-markers-unverified` doubt
naming both readings.  Pages whose notes run 1, 2, 3 keep N1's calls; N9
then only writes what N1 left.  The edits pass the page-edge check
(_repair_runs.edge_safe) as the runs stage's do.

Issue #8 adds three sources of evidence.  (2) The page's structure: a note
at the page's foot that B4 marked unpaired and no engine read a number for
is a note slot with no reading; where the readings do not settle the notes
(or such a slot is among them), the page's calls number them in order -
as many calls as notes, every call read exactly by one engine at least (the
note it calls stands at the page's foot), one run of consecutive numbers,
no note read otherwise - and N9 writes the number over B4's mark.  (3) The
pages around: pages are read in order, and the number after the previous
page's last note is taken first wherever every slot reads it at least
truncated (one note the engines read 82 and 85 after a page ending at 81).
(4) The answerer: where phase 3 asked note-numbers (the sealed record's
noteNumbers), its numbers are one more reading ("m") on the note whose
line opens with the note's first characters and on the call after the
characters it gives before it (read once on the page), and a call no engine
read gets a slot there.

Replayed on the sealed pages of five Qwen runs of the bench (2026-11-18):
only pages 75, 77 and 327 change, none scores worse; on one run every note
of the three pages is paired (note F1 27.6 -> 68.4) and its errors there
fall from 50 to 11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import bisect
import re
import unicodedata
from typing import Any, Mapping, Sequence

from jyut_ocr.core import ocr_markdown as om
import _repair_edits as re_
import _repair_rules as rr
import _repair_runs as rn
from jyut_ocr.core.note_markers import NOTE_CIRCLED, NUMBER_RE, SUPERSCRIPTS, marker_key, math_context
from _repair_brackets import UNPAIRED_HEAD

CIRCLED = re.compile(rf"[{NOTE_CIRCLED}]")
# Plain or superscript figures right after circled glyphs (⑦3, ⑦⁴: one number).
FIGURES = re.compile(r"[0-9０-９⁰¹²³⁴-⁹]+")
_SUP = re.compile(rf"[{SUPERSCRIPTS}]+")
_BRACKET_SUP = re.compile(
    r"^\$\s*(?:\{\s*\}\s*)?\^\s*\{\s*(?:[\[(〔（]\s*)?([0-9０-９]{1,3})\s*(?:[\])〕）])?\s*\}\s*\$"
)
_HTML_BRACKET_SUP = re.compile(r"^<sup>\s*[\[(〔（]?\s*([0-9０-９]{1,3})\s*[\])〕）]?\s*</sup>", re.I)
_CARET_BRACKET = re.compile(r"^\^\{\s*[\[(〔（]\s*([0-9０-９]{1,3})\s*(?:[\])〕）])\s*\}")
_NOTE_HEAD_BRACKET = re.compile(r"^[\[(〔（]\s*([0-9０-９]{1,3})\s*[\])〕）]")
_EMPTY_HEAD_BRACKET = re.compile(r"^\s*(?:\[\s*\]|\(\s*\)|〔\s*〕|（\s*）)[ \t]*")
MAX_DIGITS = 3
# The circled digit glyphs N9 writes a number past 50 with.
DIGIT_GLYPHS = "⓪①②③④⑤⑥⑦⑧⑨"
# The largest number one circled glyph prints (㊿).
ONE_GLYPH = 50


def _digits_of(raw: str) -> str:
    values = [unicodedata.numeric(c, None) for c in raw]
    if not values or any(v is None or v != int(v) for v in values):
        return ""
    return "".join(str(int(v)) for v in values)


def _norm(raw: str) -> str:
    """An engine's gap with engine B's \\textcircled whose \\t arrived as a tab
    written back (rn.SUPERSCRIPT_RE allows the same)."""
    return raw.replace("\textcircled", r"\textcircled")


def clusters(seg: str, *, note_head: bool = False) -> list[tuple[int, int, str]]:
    """The note markers of SEG as (start, end, digits): circled glyphs one
    after another by their values, plain or superscript figures, or a
    wrapper marker_key / bracket-superscript pattern reads; digits "" where
    the reading is no number of at most MAX_DIGITS digits."""
    out: list[tuple[int, int, str]] = []
    at = 0
    while at < len(seg):
        c = seg[at]
        if seg.startswith("[^", at):
            # Footnote syntax (B4's unpaired mark, a definition, a call): no
            # marker of the print.
            close = seg.find("]", at)
            at = close + 1 if close > at else at + 2
            continue
        if CIRCLED.match(c):
            end, digits = at, ""
            while end < len(seg) and CIRCLED.match(seg[end]):
                value = unicodedata.numeric(seg[end], None)
                digits += str(int(value)) if value is not None else "?"
                end += 1
            figures = FIGURES.match(seg, end)
            if figures and len(digits) + len(figures.group()) <= MAX_DIGITS:
                digits += _digits_of(figures.group())
                end = figures.end()
            digits = digits if digits.isdigit() and len(digits) <= MAX_DIGITS else ""
            out.append((at, end, digits))
            at = end
            continue
        if _SUP.match(seg, at):
            sup = _SUP.match(seg, at)
            assert sup is not None
            digits = _digits_of(sup.group())
            out.append((at, sup.end(), digits if digits and len(digits) <= MAX_DIGITS else ""))
            at = sup.end()
            continue
        if note_head and not seg[:at].strip() and c in "[(〔（":
            bm = _NOTE_HEAD_BRACKET.match(seg[at:])
            if bm:
                digits = _digits_of(bm.group(1))
                out.append((at, at + bm.end(), digits if digits and len(digits) <= MAX_DIGITS else ""))
                at += bm.end()
                continue
        if c == "$":
            close = seg.find("$", at + 1)
            if close > at:
                raw_tex = seg[at:close + 1]
                value = marker_key(raw_tex)
                if value:
                    out.append((at, close + 1, value if len(value) <= MAX_DIGITS else ""))
                    at = close + 1
                    continue
                bm = _BRACKET_SUP.match(raw_tex)
                if bm:
                    digits = _digits_of(bm.group(1))
                    out.append((at, close + 1, digits if digits and len(digits) <= MAX_DIGITS else ""))
                    at = close + 1
                    continue
        if c in "<^\\{":
            match = NUMBER_RE.match(seg, at)
            if match is not None and match.end() > at:
                value = marker_key(match.group())
                out.append((at, match.end(), value if value and len(value) <= MAX_DIGITS else ""))
                at = match.end()
                continue
            bm = (_HTML_BRACKET_SUP if c == "<" else _CARET_BRACKET).match(seg[at:])
            if bm:
                digits = _digits_of(bm.group(1))
                out.append((at, at + bm.end(), digits if digits and len(digits) <= MAX_DIGITS else ""))
                at += bm.end()
                continue
        at += 1
    return out


def written(number: int) -> str:
    """NUMBER as N9 writes it: its circled glyph up to ONE_GLYPH (①-⑳,
    ㉑-㉟, ㊱-㊿), else its digits as circled glyphs."""
    if 1 <= number <= 20:
        return chr(0x2460 + number - 1)
    if 21 <= number <= 35:
        return chr(0x3251 + number - 21)
    if 36 <= number <= ONE_GLYPH:
        return chr(0x32B1 + number - 36)
    return "".join(DIGIT_GLYPHS[int(d)] for d in str(number))


def _truncation(reading: str, number: str) -> bool:
    """READING is NUMBER with one of its digits left out (engine B's 4 for 74,
    engine A's 20 for 240)."""
    if not reading or len(reading) != len(number) - 1:
        return False
    return any(number[:i] + number[i + 1:] == reading for i in range(len(number)))


@dataclass
class Slot:
    kind: str                  # "call" or "note"
    at: int                    # offset in the page text, for reading order
    gap: Any
    insert: int | None         # offset in the page's gap text to write at; None: the page holds a marker
    readings: dict[str, str] = field(default_factory=dict)   # engine -> digits
    shown: dict[str, str] = field(default_factory=dict)      # engine -> as read
    # The marker the page holds there: its span in the gap's text and as written.
    held: tuple[int, int, str] | None = None
    # B4's unpaired mark at the note line's start, which the number writes over.
    mark: int = 0


def _marks(seg: str, side: str) -> list[tuple[str, str]]:
    return [t.key for t in rn.tokens(seg, side) if t.kind in (rn.MARK, rn.RUN) and t.key is not None]


def _without(seg: str, spans: Sequence[tuple[int, int, str]]) -> str:
    out, at = [], 0
    for s, e, _ in spans:
        out.append(seg[at:s])
        at = e
    out.append(seg[at:])
    return "".join(out)


def _call_insert(page_seg: str, engine_seg: str, cluster: tuple[int, int, str], spans: Sequence[tuple[int, int, str]],
                 side: str) -> int | None:
    """Where in PAGE_SEG a call goes that the engine reads at CLUSTER of
    ENGINE_SEG: after as many of the page's marks and runs as the engine
    reads before it, where the page holds the engine's marks and runs there
    (its other markers set aside); else marker_cut on non-markup gaps when
    marks differ slightly."""
    clean_before = _without(engine_seg[:cluster[0]], [s for s in spans if s[1] <= cluster[0]])
    clean_after = _without(engine_seg[cluster[1]:], [(s - cluster[1], e - cluster[1], d) for s, e, d in spans
                                                      if s >= cluster[1]])
    before = _marks(clean_before, side)
    after = _marks(clean_after, side)
    page = [t for t in rn.tokens(page_seg, "page") if t.kind in (rn.MARK, rn.RUN) and t.key is not None]
    if [rn.coarse(t.key) for t in page] == [rn.coarse(k) for k in before + after]:
        return 0 if not before else page[len(before) - 1].end
    if not clusters(page_seg) and not any(ch in page_seg for ch in "|<>"):
        import proofread_pages as pp
        return pp.marker_cut(page_seg, clean_before, clean_after, False)
    return None


def _key_text(text: str) -> str:
    cleaned = UNPAIRED_HEAD.sub("", text, count=1)
    cleaned = _EMPTY_HEAD_BRACKET.sub("", cleaned, count=1)
    return re.sub(r"[\s*_]", "", cleaned)[:8]


def _mark(line: str) -> int:
    """The length of B4's unpaired mark opening LINE (its leading spaces and
    any empty bracket pair left behind after it), 0 where it holds none."""
    lead = len(line) - len(line.lstrip(" \t"))
    m = UNPAIRED_HEAD.match(line[lead:])
    if not m:
        return 0
    end = lead + m.end()
    empty = _EMPTY_HEAD_BRACKET.match(line[end:])
    return end + (empty.end() if empty else 0)


def _slots(page: rn.PageRuns) -> list[Slot]:
    """The note slots of PAGE where an engine is aligned to it."""
    found: dict[tuple, Slot] = {}
    for gap in page.gaps():
        page_raw = page.text[gap.start:gap.end]
        page_lines = page_raw.split("\n")
        for side, raw in (("a", gap.a_raw), ("b", gap.b_raw)):
            if raw is None:
                continue
            raw = _norm(raw)
            lines = raw.split("\n")
            # Calls: the gap's part in the line of the character before it.
            # Skip only when the page itself broke before any marks in line 0 while
            # the engine stayed single-line (avoid placing on the preceding paragraph).
            if gap.edge != "start" and not (len(page_lines) > 1 and not page_lines[0] and len(lines) == 1):
                spans = clusters(lines[0])
                for n, cluster in enumerate(spans):
                    if not cluster[2]:
                        continue
                    raw_tok = lines[0][cluster[0]:cluster[1]]
                    if _SUP.fullmatch(raw_tok) and math_context(lines[0], cluster[0], cluster[1]):
                        continue
                    held = clusters(page_lines[0])
                    slot = found.setdefault(("call", gap.k, n), Slot("call", gap.start, gap, None))
                    if held:
                        h = held[min(n, len(held) - 1)]
                        slot.readings.setdefault("page", h[2])
                        slot.held = slot.held or (h[0], h[1], page_lines[0][h[0]:h[1]])
                    else:
                        where = _call_insert(page_lines[0], lines[0], cluster, spans, side)
                        if where is not None and slot.insert is None:
                            slot.insert = where
                    slot.readings[side], slot.shown[side] = cluster[2], raw_tok
            offsets, at = ([0] if len(page_lines) == 1 else []), len(page_lines[0]) + 1
            for line in page_lines[1:]:
                offsets.append(at)
                at += len(line) + 1
            if len(lines) >= 2 and len(page_lines) >= 2:
                middle = lines[1:] if gap.edge == "end" else lines[1:-1]
                page_middle = page_lines[1:] if gap.edge == "end" else page_lines[1:-1]
                for line in middle:
                    spans = clusters(line, note_head=True)
                    if not spans or line[:spans[0][0]].strip() or not spans[0][2]:
                        continue
                    text = _key_text(line[spans[0][1]:])
                    if len(text) < 4:
                        continue
                    hits = [i for i, p in enumerate(page_middle) if _key_text(p).startswith(text) or
                            (clusters(p, note_head=True)
                             and _key_text(p[clusters(p, note_head=True)[0][1]:]).startswith(text))]
                    if len(hits) != 1:
                        continue
                    p = page_middle[hits[0]]
                    held = clusters(p, note_head=True)
                    lead = len(p) - len(p.lstrip(" \t"))
                    slot = found.setdefault(("note", gap.k, "line", hits[0]),
                                            Slot("note", gap.start + offsets[hits[0]], gap, None))
                    if held and not p[:held[0][0]].strip():
                        slot.readings.setdefault("page", held[0][2])
                        o = offsets[hits[0]]
                        slot.held = slot.held or (o + held[0][0], o + held[0][1], p[held[0][0]:held[0][1]])
                    elif slot.insert is None:
                        slot.insert, slot.mark = offsets[hits[0]] + (0 if _mark(p) else lead), _mark(p)
                    slot.readings[side], slot.shown[side] = spans[0][2], line[spans[0][0]:spans[0][1]]
            last, page_last = lines[-1], page_lines[-1]
            if gap.edge != "end" and (
                    (len(lines) >= 2 and len(page_lines) >= 2)
                    or (gap.edge == "start" and (not page_last.strip() or _mark(page_last) > 0
                                                 or bool(clusters(page_last, note_head=True))))):
                spans = clusters(last, note_head=True)
                if spans and not last[:spans[0][0]].strip() and spans[0][2]:
                    held = clusters(page_last, note_head=True)
                    lead = len(page_last) - len(page_last.lstrip(" \t"))
                    slot = found.setdefault(("note", gap.k, "next"), Slot("note", gap.end, gap, None))
                    if held and not page_last[:held[0][0]].strip():
                        slot.readings.setdefault("page", held[0][2])
                        o = offsets[-1]
                        slot.held = slot.held or (o + held[0][0], o + held[0][1],
                                                  page_last[held[0][0]:held[0][1]])
                    elif slot.insert is None:
                        slot.insert, slot.mark = offsets[-1] + (0 if _mark(page_last) else lead), _mark(page_last)
                    slot.readings[side], slot.shown[side] = spans[0][2], last[spans[0][0]:spans[0][1]]
    return sorted(found.values(), key=lambda s: (s.at, s.kind != "call"))


def _how(slots: Sequence[Slot], start: int) -> list[str | None]:
    """Per slot, how the readings give START + its index: "exact" (an
    engine reads it), "truncated" (an engine reads it with digits left out),
    None (neither)."""
    out: list[str | None] = []
    for i, slot in enumerate(slots):
        want = str(start + i)
        values = [v for v in slot.readings.values() if v]
        out.append("exact" if want in values else "truncated" if any(_truncation(v, want) for v in values) else None)
    return out


def settle(slots: Sequence[Slot], prefer: int | None = None) -> tuple[int | None, list[str | None]]:
    """The start of the run of consecutive numbers SLOTS (in reading order)
    take, and per slot how its number is read (_how); (None, ...) where no
    start has the readings' support.  PREFER (the start the page's notes
    settled, for its calls) is taken where every slot reads it at least
    truncated.  Otherwise the start most readings give exactly, at least
    two of them; one slot alone only where both engines read it and each
    reading is its number or a truncation of it."""
    if prefer is not None:
        how = _how(slots, prefer)
        if all(how):
            return prefer, how
    scored: list[tuple[tuple[int, int, int], int, list[str | None]]] = []
    starts = {int(v) - i for i, s in enumerate(slots) for v in s.readings.values() if v and v.isdigit()}
    for start in sorted(starts):
        if start < 1:
            continue
        how = _how(slots, start)
        # The slots read exactly first; then every engine's reading that gives
        # the number, exactly or truncated: on page 77 engine B reads the notes
        # 72-74 as 7, 8, 9, a run read exactly as often as engine A's 72-74,
        # and only 72 accounts for the other engine's readings too.  (Support
        # first would let a misreading win: engine A's ⑥⑭ for 64 starts a run
        # 614-618 of which every other two-digit reading is a truncation.)
        support = sum(1 for i, slot in enumerate(slots) for v in slot.readings.values()
                      if v and (v == str(start + i) or _truncation(v, str(start + i))))
        scored.append(((how.count("exact"), support, how.count("truncated")), start, how))
    if not scored:
        return None, [None] * len(slots)
    scored.sort(key=lambda x: x[0], reverse=True)
    (exact, support, _), start, how = scored[0]
    if len(scored) > 1 and scored[1][0][:2] == (exact, support):
        # Two runs the readings support alike: which is printed is not given.
        return None, [None] * len(slots)
    # Both engines behind it at one slot at least (each reading the number or
    # a truncation of it): on the bench's table pages engine A reads ⑫ and ⑬
    # where 193-205 are printed and engine B 19 at both, a run of two exact
    # readings the other engine gives nowhere.
    both = any(sum(1 for v in slot.readings.values() if v and (v == str(start + i) or _truncation(v, str(start + i))))
               >= 2 for i, slot in enumerate(slots))
    if exact >= 2 and both:
        return start, how
    if len(slots) == 1 and exact == 1:
        values = [v for v in slots[0].readings.values() if v]
        want = str(start)
        if len(values) >= 2 and all(v == want or _truncation(v, want) for v in values):
            return start, how
    return None, [None] * len(slots)


def _engine_fit(slots: Sequence[Slot], start: int) -> tuple[int, int] | None:
    """How the readings other than the answerer's (the page's own marker and
    the engines') give the run from START: (slots read exactly, readings that
    give the number exactly or truncated); None where a slot so read has no
    reading that gives its number."""
    exact = support = 0
    for i, slot in enumerate(slots):
        want = str(start + i)
        values = [v for k, v in slot.readings.items() if v and k != "m"]
        good = [v for v in values if v == want or _truncation(v, want)]
        if values and not good:
            return None
        exact += want in values
        support += len(good)
    return exact, support


def _shifted(calls: Sequence[Slot], start: int) -> bool:
    """Whether the page's and the engines' readings of CALLS give the run
    from START - 1 or START + 1 as well as the run from START: then which
    the page prints is not given.  A page's first note may be called on the
    page before and its last call's note stand on the next: the bench's page
    315 holds notes 193-196 and calls 194-198, each call read 19 by an
    engine, which fits 193-197 as well.  The answerer's reading does not
    tell them apart: it pairs the page's calls with its notes in order (it
    gave the calls 193-196 there).  Calls no engine read are not judged
    here: the answer is all there is of them (issue #8, item 4)."""
    if not any(k != "m" and v for s in calls for k, v in s.readings.items()):
        return False
    here = _engine_fit(calls, start)
    if here is None:
        return False
    return any(alt is not None and alt >= here
               for alt in (_engine_fit(calls, s) for s in (start - 1, start + 1) if s >= 1))


def _assign(notes: Sequence[Slot], calls: Sequence[Slot], n0: int, c0: int | None,
            nhow: Sequence[str | None], chow: Sequence[str | None]) -> dict[str, list] | None:
    """What each slot will hold, as (start, end, text, slot, how) in its gap's
    text (an insertion where start == end, B4's mark written over), for notes
    numbered from N0 and calls from C0 (None: no calls written); None where a
    note would stay without its number, or no note would find its call.  A
    marker the page holds that N9 reads as the number but marker_key does
    not (⑦3, ⑦⁴) is written over, and so is one that marker_key reads as
    the number with a digit left out where another reading at the slot gives
    the number (the bench's page 327: the page holds ㉔ where 240 is printed,
    the answerer reads 240 there and engine A ⑳)."""
    writes: dict[str, list] = {"note": [], "call": []}
    note_numbers_set = {str(n0 + j) for j in range(len(notes))}
    call_numbers_set = ({str(c0 + j) for j in range(len(calls))} if c0 is not None
                        else {marker_key(s.held[2]) for s in calls if s.held and marker_key(s.held[2])})
    for kind, group, start, how in (("note", notes, n0, nhow), ("call", calls, c0, chow)):
        if start is None:
            continue
        other_numbers = note_numbers_set if kind == "call" else call_numbers_set
        for i, (slot, read) in enumerate(zip(group, how)):
            number = str(start + i)
            if slot.held is not None:
                held_key = marker_key(slot.held[2])
                if held_key == number:
                    continue
                if (slot.readings.get("page") == number
                        or (_truncation(held_key or slot.readings.get("page", ""), number)
                            and any(v == number for k, v in slot.readings.items() if k != "page"))
                        or (any(v == number for k, v in slot.readings.items() if k != "page")
                            and held_key not in other_numbers)):
                    writes[kind].append((slot.held[0], slot.held[1], written(start + i), slot, "held"))
                    continue
                if kind == "note":
                    # A note of the run that would stay unpaired: finalize
                    # would make the others footnotes and leave it a paragraph
                    # among them (the bench scores that worse than none).
                    return None
                continue
            if read is None or slot.insert is None:
                if kind == "note":
                    return None
                continue
            writes[kind].append((slot.insert, slot.insert + slot.mark, written(start + i), slot, read))
    noted = {str(n0 + i) for i in range(len(notes))}
    called = {marker_key(s.held[2]) for s in calls if s.held} | {marker_key(text) for _, _, text, _, _ in writes["call"]}
    if not noted & called:
        # No note of the page would find its call: the numbers written would
        # stand as printed glyphs and pair with nothing.
        return None
    return writes


def _by_readings(notes: Sequence[Slot], calls: Sequence[Slot], prior: int | None) -> dict[str, list] | None:
    """The page's notes settled by their readings (PRIOR, the number after
    the previous page's last note, preferred where every note reads it at
    least truncated), and its calls on the notes' run, else on their own;
    None where they do not settle, the calls and notes share no number, or
    _assign refuses."""
    if any(not n.readings for n in notes):
        return None
    n0, nhow = settle(notes, prior)
    if n0 is None:
        return None
    c0, chow = settle(calls, n0) if calls else (None, [])
    if c0 is not None and _shifted(calls, c0):
        c0, chow = None, [None] * len(calls)
    if c0 is not None and (n0 + len(notes) <= c0 or c0 + len(calls) <= n0):
        # The calls and the notes settled on numbers that share none: one of
        # them is misread, and which is not given.
        return None
    return _assign(notes, calls, n0, c0, nhow, chow)


def _model_slots(page: rn.PageRuns, slots: list[Slot]) -> list[Slot]:
    """Issue #8, item 4: the answerer's reading of the page's note numbers
    (the sealed record's noteNumbers, proofread_pages.ask_note_numbers) as one
    more reading ("m"): a note's number on the note slot whose line opens
    with the note's first characters, a call's on the call slot after the
    characters the answer gives before it, read once on the page - a new call
    slot where no engine read a call there (written after the marks the
    answer gives after those characters, where the page holds them).
    Returns the new call slots."""
    import proofread_pages as pp
    record = getattr(page.pd, "record", None) or {}
    answer = (record.get("noteNumbers") or {}).get("notes") or []
    if not answer:
        return []
    fold = lambda s: pp.variant_fold(pp.CJK(s or ""))
    chars = pp.variant_fold(page.s.chars)
    text = page.text
    new: list[Slot] = []
    for item in answer:
        number = str(item.get("number") or "")
        if not number.isdigit():
            continue
        head = fold(item.get("note"))[:4] or re.sub(r"[^A-Za-z]", "", item.get("note") or "")[:6].lower()
        if len(head) >= 2:
            for slot in slots:
                if slot.kind != "note":
                    continue
                start = text.rfind("\n", 0, slot.gap.start + (slot.insert or 0)) + 1
                line = text[start:text.find("\n", start) if "\n" in text[start:] else len(text)]
                line = UNPAIRED_HEAD.sub("", line.lstrip(" \t"), count=1)
                lead = fold(line)[:len(head)] if pp.CJK(head) else re.sub(r"[^A-Za-z]", "", line)[:len(head)].lower()
                if lead == head:
                    slot.readings["m"], slot.shown["m"] = number, number
                    break
        before = item.get("before") or ""
        tail_chars = fold(before)[-6:]
        if len(tail_chars) < 2:
            continue
        hits = [i for i in range(len(chars)) if chars.startswith(tail_chars, i)]
        if len(hits) != 1:
            continue
        k = hits[0] + len(tail_chars)
        found = [s for s in slots + new if s.kind == "call" and s.gap.k == k]
        if found:
            found[0].readings["m"], found[0].shown["m"] = number, number
            continue
        if k >= len(page.s.chars):
            continue
        gap = page.gap(k)
        seg = text[gap.start:gap.end].split("\n")[0]
        if clusters(seg) or "\n" in text[gap.start:gap.end]:
            continue
        last = max((i for i, c in enumerate(before) if pp.CJK(c)), default=-1)
        marks = before[last + 1:].strip()
        if not seg.startswith(marks):
            continue
        new.append(Slot("call", gap.start, gap, len(marks), readings={"m": number}, shown={"m": number}))
    return new


def _layout_slots(page: rn.PageRuns, slots: list[Slot]) -> list[Slot]:
    """The notes at PAGE's foot that B4 marked unpaired and no engine read a
    number for: note slots with no reading, written over B4's mark (issue
    #8: the page's structure pairs them with its calls)."""
    taken = {s.gap.start + s.insert for s in slots if s.kind == "note" and s.insert is not None}
    taken |= {s.gap.start + s.held[0] for s in slots if s.kind == "note" and s.held is not None}
    out: list[Slot] = []
    at = 0
    gaps = list(page.gaps())
    for line in page.text.split("\n"):
        mark = _mark(line)
        if mark and at not in taken:
            gap = next((g for g in gaps if g.start <= at <= g.end), None)
            if gap is not None:
                out.append(Slot("note", at, gap, at - gap.start, mark=mark))
        at += len(line) + 1
    return out


def _layout(notes: Sequence[Slot], calls: Sequence[Slot], prior: int | None) -> int | None:
    """The page's notes numbered by its calls (issue #8, item 2): as many calls
    as notes, every call read exactly by an engine as one run of consecutive
    numbers (one engine is enough: the note it calls stands at the page's
    foot; the answerer's reading is not one, as it pairs calls with notes in
    order: _shifted), no note read otherwise than its number (exactly or truncated);
    PRIOR, the number after the previous page's last note, first."""
    if not notes or len(calls) != len(notes):
        return None
    read = [[v for k, v in c.readings.items() if v and k != "m"] for c in calls]
    starts = ([prior] if prior else []) + sorted({int(v) - i for i, values in enumerate(read)
                                                 for v in values if v.isdigit()})
    for start in starts:
        if start < 1:
            continue
        if all(str(start + i) in values for i, values in enumerate(read)) and all(
                not n.readings or any(v == str(start + i) or _truncation(v, str(start + i))
                                      for v in n.readings.values() if v) for i, n in enumerate(notes)):
            return start
    return None


def _foot_notes_only(page: rn.PageRuns, notes: Sequence[Slot]) -> list[Slot]:
    """Subset of NOTES sitting inside PAGE's contiguous trailing foot-note block."""
    if not notes:
        return []
    lines = page.text.split("\n")
    starts: list[int] = []
    off = 0
    for line in lines:
        starts.append(off)
        off += len(line) + 1
    note_lines = {max(0, bisect.bisect_right(starts, s.at) - 1) for s in notes}
    in_foot = False
    top = len(lines)
    for idx in range(len(lines) - 1, -1, -1):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped or stripped in ("---", "***", "___"):
            continue
        head_cl = clusters(raw.lstrip(" \t"), note_head=True)
        is_note = (idx in note_lines
                   or _mark(raw) > 0
                   or bool(head_cl and head_cl[0][0] == 0)
                   or bool(re.search(r"[A-Za-z]{3}", raw) and not re.search(r"[\u4e00-\u9fff]", raw)))
        if is_note:
            in_foot = True
            top = idx
            continue
        break
    if not in_foot or top >= len(starts):
        return []
    cutoff = starts[top]
    return [s for s in notes if s.at >= cutoff]


def _table_call_slots(text: str) -> tuple[int | None, int | None, list[tuple[int, int, str, str]]]:
    """Return (first_table_offset, last_table_end, [(abs_s, abs_e, raw, digits), ...])
    for Markdown pipe-table data rows in TEXT."""
    first_at: int | None = None
    last_end: int | None = None
    calls: list[tuple[int, int, str, str]] = []
    off = 0
    for line in text.split("\n"):
        if om.is_table_row(line):
            if first_at is None:
                first_at = off
            last_end = off + len(line)
            if not all(c in "-:| \t" for c in line.strip()):
                for cs, ce, digits in clusters(line):
                    if not digits:
                        continue
                    raw_tok = line[cs:ce]
                    if not CIRCLED.search(raw_tok) and math_context(line, cs, ce):
                        continue
                    calls.append((off + cs, off + ce, raw_tok, digits))
        off += len(line) + 1
    return first_at, last_end, calls


def _folded_circled(raw_tok: str) -> str | None:
    """When RAW_TOK consists solely of fewer circled glyphs than a 3-digit
    number and holds a >=10 glyph (e.g. ②⑩ read for 20), fold >=10 glyphs to
    their ones digit (②⑩ -> '20')."""
    circled = CIRCLED.findall(raw_tok)
    if len(circled) != len(raw_tok) or len(circled) < 2:
        return None
    vals = [int(unicodedata.numeric(c)) for c in circled]
    if not any(v >= 10 for v in vals):
        return None
    return "".join(str(v % 10) for v in vals)


def _settle_table_calls(table_calls: Sequence[tuple[int, int, str, str]]) -> int | None:
    """Infer the starting number of sequential table calls when consecutive exact
    readings pin a unique non-contradictory run."""
    if len(table_calls) < 2:
        return None
    starts = {int(d) - j for j, (_, _, _, d) in enumerate(table_calls) if d.isdigit() and int(d) - j >= 1}
    scored: list[tuple[tuple[int, int], int]] = []
    for start in sorted(starts):
        exact: list[int] = []
        fit = 0
        contradict = False
        for j, (_, _, raw_tok, d) in enumerate(table_calls):
            want = str(start + j)
            if d == want:
                exact.append(j)
                fit += 1
            elif len(d) >= len(want):
                folded = _folded_circled(raw_tok)
                if folded and len(raw_tok) < len(want):
                    if _truncation(folded, want):
                        fit += 1
                else:
                    contradict = True
                    break
            elif _truncation(d, want):
                fit += 1
        if contradict or len(exact) < 2 or fit * 2 < len(table_calls):
            continue
        has_pair = any(b == a + 1 for a, b in zip(exact, exact[1:]))
        if not has_pair and fit < len(table_calls):
            continue
        scored.append(((len(exact), fit), start))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _apply_writes(plan: rr.Plan, page: rn.PageRuns, scan: int, writes: Mapping[str, list]) -> None:
    inserts: dict[int, list[tuple[int, int, str, Slot, str]]] = {}
    for start_end in writes.values():
        for s, e, text, slot, read in start_end:
            inserts.setdefault(slot.gap.k, []).append((s, e, text, slot, read))
    for items in inserts.values():
        gap = items[0][3].gap
        raw = page.text[gap.start:gap.end]
        new = raw
        for s, e, text, slot, read in sorted(items, key=lambda x: x[0], reverse=True):
            new = new[:s] + text + new[e:]
        items = sorted(items, key=lambda x: x[0])
        numbers = [text for _, _, text, _, _ in items]
        evidence = {"before": raw, "engineA": rn.show(gap.a_raw or "", 80), "engineB": rn.show(gap.b_raw or "", 80),
                    "numbers": numbers, "read": [{"kind": s.kind, "as": r, **s.shown} for _, _, _, s, r in items]}
        plan.edits.append(re_.Edit(scan, gap.start, gap.end, new, rule="N9", evidence=evidence))
        for _, _, text, slot, read in items:
            if read == "truncated":
                plan.doubts.append(("note-markers-unverified", scan, rn._gap_detail(page, gap,
                                    f"{slot.kind} {marker_key(text)} written from the page's numbering; the engines "
                                    f"read {slot.shown.get('a', '-')} and {slot.shown.get('b', '-')}"),
                                    {"number": marker_key(text), **slot.shown}))


def _stitched_table_span(order: Sequence[int], pages: Mapping[int, rn.PageRuns],
                         slots_by_scan: Mapping[int, list[Slot]], plan: rr.Plan) -> dict[int, int | None]:
    """Handle stitched multi-page tables whose calls sit in page i0's pipe table
    while their footnotes sit across pages i0..i1.  Returns handled scan ->
    last note number on that page (for the span's final page) or None."""
    handled: dict[int, int | None] = {}
    i0 = 0
    while i0 < len(order):
        s0 = order[i0]
        page0 = pages.get(s0)
        if page0 is None or s0 in handled:
            i0 += 1
            continue
        first_at, _, table_calls = _table_call_slots(page0.text)
        t_start = _settle_table_calls(table_calls) if first_at is not None else None
        if first_at is None or t_start is None:
            i0 += 1
            continue
        t_end = t_start + len(table_calls) - 1
        i1 = i0
        for k in range(i0 + 1, len(order)):
            sk = order[k]
            pk = pages.get(sk)
            if pk is None or any(om.is_table_row(l) for l in pk.text.split("\n")):
                break
            calls_k = [s for s in slots_by_scan[sk] if s.kind == "call"]
            raw_notes_k = [s for s in slots_by_scan[sk] if s.kind == "note"]
            notes_k = _foot_notes_only(pk, raw_notes_k) or raw_notes_k
            if calls_k or not notes_k:
                break
            i1 = k
        if i1 == i0:
            i0 += 1
            continue
        span_notes: list[tuple[int, rn.PageRuns, Slot]] = []
        for idx_p in range(i0, i1 + 1):
            sp = order[idx_p]
            ppage = pages[sp]
            raw_ns = [s for s in slots_by_scan[sp] if s.kind == "note"]
            for slot in (_foot_notes_only(ppage, raw_ns) or raw_ns):
                span_notes.append((sp, ppage, slot))
        total = len(span_notes)
        n_start = t_end - total + 1
        if total < len(table_calls) or n_start < 1 or n_start > t_start:
            i0 += 1
            continue
        exact_notes = 0
        fit_notes = 0
        with_readings = 0
        contradict = False
        for idx_n, (_, _, slot) in enumerate(span_notes):
            want = str(n_start + idx_n)
            vals = [v for v in slot.readings.values() if v]
            if not vals:
                continue
            with_readings += 1
            if any(len(v) >= len(want) and v != want for v in vals):
                contradict = True
                break
            if any(v == want for v in vals):
                exact_notes += 1
                fit_notes += 1
            elif any(_truncation(v, want) for v in vals):
                fit_notes += 1
        if contradict or exact_notes < 1 or (with_readings and fit_notes * 2 < with_readings):
            i0 += 1
            continue
        for j, (abs_s, abs_e, raw_tok, _) in enumerate(table_calls):
            want = str(t_start + j)
            if marker_key(raw_tok) != want:
                plan.edits.append(re_.Edit(s0, abs_s, abs_e, written(t_start + j), rule="N9",
                                           evidence={"kind": "table-call", "before": raw_tok, "number": want}))
        body_calls = [s for s in slots_by_scan[s0]
                      if s.kind == "call" and s.at < first_at and s.gap.end <= first_at]
        notes_s0 = [entry for entry in span_notes if entry[0] == s0]
        m = len(body_calls)
        if m > 0:
            b_start = t_start - m
            if b_start >= n_start and (m >= len(notes_s0) or not _shifted(body_calls, b_start)):
                ok = True
                for j, s in enumerate(body_calls):
                    want = str(b_start + j)
                    vals = [v for v in s.readings.values() if v]
                    if ((s.held is None and s.insert is None)
                            or not any(v == want or _truncation(v, want) for v in vals)
                            or any(len(v) >= len(want) and v != want for k, v in s.readings.items()
                                   if v and k != "m")):
                        ok = False
                        break
                if ok:
                    body_writes: dict[str, list] = {"call": []}
                    for j, s in enumerate(body_calls):
                        want = str(b_start + j)
                        if s.held is not None:
                            if marker_key(s.held[2]) != want:
                                body_writes["call"].append((s.held[0], s.held[1], written(b_start + j), s, "held"))
                        elif s.insert is not None:
                            body_writes["call"].append((s.insert, s.insert + s.mark, written(b_start + j), s, "table-span"))
                    _apply_writes(plan, page0, s0, body_writes)
        for idx_n, (scan_n, page_n, slot) in enumerate(span_notes):
            want = str(n_start + idx_n)
            target = written(n_start + idx_n)
            if slot.held is not None:
                if marker_key(slot.held[2]) != want:
                    start_off = slot.gap.start + slot.held[0]
                    end_off = slot.gap.start + slot.held[1]
                    plan.edits.append(re_.Edit(scan_n, start_off, end_off, target, rule="N9",
                                               evidence={"kind": "table-span", "before": slot.held[2],
                                                         "number": want}))
            elif slot.insert is not None:
                start_off = slot.gap.start + slot.insert
                end_off = start_off + slot.mark
                plan.edits.append(re_.Edit(scan_n, start_off, end_off, target, rule="N9",
                                           evidence={"kind": "table-span",
                                                     "before": page_n.text[start_off:end_off], "number": want}))
        for idx_p in range(i0, i1 + 1):
            handled[order[idx_p]] = t_end if idx_p == i1 else None
        i0 = i1 + 1
    return handled


def note_numbers(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
                 facts: rr.BookFacts | None = None) -> rr.Plan:
    """N9 over the pages ORDER, in order: a page's notes continue from the
    previous page's last note where the readings allow (issue #8, item 3)."""
    facts = facts or rr.book_facts(book, model)
    setting = rn.Setting(book, model, facts, list(order), texts)
    plan = rr.Plan()
    pages: dict[int, rn.PageRuns] = {}
    slots_by_scan: dict[int, list[Slot]] = {}
    for scan in order:
        page = setting.page(scan)
        if not page.s.chars:
            continue
        pages[scan] = page
        slots = _slots(page)
        slots = slots + _layout_slots(page, slots)
        slots_by_scan[scan] = sorted(slots + _model_slots(page, slots), key=lambda s: (s.at, s.kind != "call"))

    handled = _stitched_table_span(order, pages, slots_by_scan, plan)
    last: int | None = None
    for scan in order:
        if scan not in pages:
            continue
        if scan in handled:
            if handled[scan] is not None:
                last = handled[scan]
            continue
        page = pages[scan]
        slots = slots_by_scan[scan]
        notes = [s for s in slots if s.kind == "note"]
        calls = [s for s in slots if s.kind == "call"]
        if not notes:
            # Calls alone pair with nothing on the page.
            continue
        prior = last + 1 if last else None
        writes = _by_readings(notes, calls, prior)
        if writes is None:
            start = _layout(notes, calls, prior)
            writes = None if start is None else _assign(notes, calls, start, start,
                                                        ["layout"] * len(notes), ["exact"] * len(calls))
        if writes is None:
            foot = _foot_notes_only(page, notes)
            if 0 < len(foot) < len(notes):
                notes = foot
                writes = _by_readings(notes, calls, prior)
                if writes is None:
                    start = _layout(notes, calls, prior)
                    writes = None if start is None else _assign(notes, calls, start, start,
                                                                ["layout"] * len(notes), ["exact"] * len(calls))
        if writes is None:
            continue
        numbers = [int(marker_key(text)) for _, _, text, slot, _ in writes["note"] if marker_key(text).isdigit()]
        numbers += [int(marker_key(s.held[2])) for s in notes if s.held and marker_key(s.held[2]).isdigit()]
        if numbers:
            last = max(numbers)
        _apply_writes(plan, page, scan, writes)
    return plan
