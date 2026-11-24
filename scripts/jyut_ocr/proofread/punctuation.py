"""Punctuation passes, census parsing, leader rewrites, gap alignment, and doubt collection."""

from __future__ import annotations

from collections import Counter
import difflib
import itertools
import os
import re
import sys
from typing import Any, Collection, Mapping, Sequence
import unicodedata


from jyut_ocr.proofread import punctuation_fusion as _punctuation_fusion  # noqa: E402
from jyut_ocr.proofread.core import (  # noqa: E402
    BULLET_MARKS, BULLET_SHAPES, CENSUS_SYSTEM, CHINESE_DIGITS, CJK, DELETED_RUN_LIMIT,
    FALLBACK_MARKS, INSERTION_LIMIT, LEADER, LEADER_MARKS, LEADER_READ, LOOPED_READING,
    NUMERAL_CHARS, OPENING_MARKS, PUNCT_MARKS, PUNCT_SYSTEM, TEXT_SEAMS, engine_gaps, facade_attr,
    gaps_of, profile_section, punctuation_counts, punctuation_mode, strip_fence, table_spans,
)
from jyut_ocr.proofread.crops import leader_count  # noqa: E402
from jyut_ocr.proofread.variants import variant_fold  # noqa: E402
from jyut_ocr.proofread.furniture import (  # noqa: E402
    DEGENERATE_PUNCTUATION_DENSITY, FURNITURE_EDGE, furniture_leftover, _is_furniture,
)


SENTENCE_PUNCT = "。，、；：？！"


def punctuation_density(text: str) -> float:
    """Sentence-punctuation marks per 100 CJK characters.

    Recorded as an observation, never judged against a threshold. Punctuation
    practice varies within a single book - a punctuated 序 in front of an
    unpunctuated 正文, unpunctuated quotations inside punctuated commentary -
    so there is no book-wide density a page can be "too far" from.
    """
    cjk = sum(1 for c in text if "\u3400" <= c <= "\u9fff")
    if cjk < 40:
        return -1.0
    return 100.0 * sum(text.count(p) for p in SENTENCE_PUNCT) / cjk


def punctuation_delta(before: str, after: str) -> str:
    gained = punctuation_counts(after) - punctuation_counts(before)
    lost = punctuation_counts(before) - punctuation_counts(after)
    parts = []
    if gained:
        parts.append("added " + "".join(f"{k}×{v}" for k, v in gained.items()))
    if lost:
        parts.append("removed " + "".join(f"{k}×{v}" for k, v in lost.items()))
    return "; ".join(parts) or "same counts, different placement"


CENSUS_LINE = re.compile(r"符號[：:]\s*([0-9０-９〇零一二三四五六七八九十百]+)")
DOUBLED_LINE = re.compile(r"雙圈[：:]\s*([0-9０-９〇零一二三四五六七八九十百]+)")


def parse_census(text: str, line: re.Pattern = CENSUS_LINE) -> int | None:
    """The printed-mark count the model reported, or None when it gave none.
    With DOUBLED_LINE, the number of those places that are doubled circles."""
    match = line.search(text)
    if not match:
        return None
    raw = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    if raw in CHINESE_DIGITS:
        return CHINESE_DIGITS[raw]
    if raw == "十":
        return 10
    if "十" in raw and all(c in CHINESE_DIGITS or c == "十" for c in raw):
        tens, _, ones = raw.partition("十")
        return (CHINESE_DIGITS.get(tens, 1) if tens else 1) * 10 + (CHINESE_DIGITS.get(ones, 0) if ones else 0)
    return None


def density_limit(arguments: Any) -> float:
    """Degenerate-output bound, scaled to this book's densest printed block.

    A fixed bound encoded one book's habits: a page printing a circle after
    every character (圈點, marks per character ~1) would be rejected as
    garbage.  With a profile the bound is a quarter above the densest block
    the probe actually measured; without one it stays at the old constant.
    """
    measured = profile_section(arguments, "notation").get("maxMarksPerCharacter")
    if not measured:
        return DEGENERATE_PUNCTUATION_DENSITY
    return max(DEGENERATE_PUNCTUATION_DENSITY, min(250.0, 100.0 * measured * 1.25))


def census_ceiling(arguments: Any, characters: int) -> int:
    """Largest believable census for a block of this many characters."""
    measured = profile_section(arguments, "notation").get("maxMarksPerCharacter")
    ratio = 0.5 if not measured else max(0.5, min(2.2, measured * 1.5))
    return int(characters * ratio) + 2


def notation_hint(arguments: Any) -> str:
    """What the probe saw this book print, as evidence for the punctuator."""
    notation = profile_section(arguments, "notation")
    lines = []
    counts = notation.get("markBlocks") or {}
    total = notation.get("blocksSurveyed") or 0
    if counts and total:
        seen = "、".join(f"「{mark}」{n}" for mark, n in counts.items())
        lines.append(f"本書抽樣 {total} 個 block，見到各符號嘅 block 數：{seen}。"
                     "少數 block 先見到嘅符號可能係誤認（例如雙圈細看似「，」），寫之前要喺圖上確認形狀。")
    elif notation.get("marks"):
        lines.append(f"本書抽樣頁見過嘅符號：{''.join(notation['marks'])}。")
    if notation.get("doubled"):
        lines.append("本書有雙圈：圖上兩個圈連住印，就寫「。。」，唔好合併做一個。")
    emphasis = notation.get("emphasis")
    if emphasis:
        shown = notation.get("emphasisBlocks") or {}
        lines.append(f"抽樣 {total or '若干'} 個 block 之中有 {shown.get(emphasis, '幾')} 個報告有圈點"
                     f"（一串字逐個字旁邊印{emphasis}）：只有裁圖上真係逐個字旁邊都印咗先照寫，"
                     f"{'圈寫「。」' if emphasis == '圈' else '點寫「、」'}，逐個跟圖，唔好推廣去冇印嘅字。")
    return ("\n".join(lines) + "\n") if lines else ""


def census_tolerance(census: int) -> int:
    """How far the marks written may stray from the census and still match it.

    One definition for the pass that is refused on it and for the seal that
    reports a kept stretch outside it (punctuation_doubts).
    """
    return max(2, census // 4)


def mark_places(text: str) -> int:
    """How many marks TEXT carries as the census counts them: places, where a
    doubled circle 。。 is one (the census reports 雙圈 apart), and so is a
    bullet printed twice (⦿⦿, bullet_runs)."""
    marks = sum(punctuation_counts(text).values())
    return marks - text.count("。。") - sum(len(run) - 1 for _, run in bullet_runs(text))


def bullet_runs(text: str) -> list[tuple[int, str]]:
    """Each bullet TEXT writes, as (where in TEXT, the run): one bullet, or the
    same one printed twice or more in a row (⦿⦿), which marks one item.  ●, ⦿
    and ◉ are one bullet (BULLET_SHAPES, the user's ruling), so a run mixing
    them (●⦿) is one item too."""
    out: list[tuple[int, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in BULLET_MARKS:
            end = index
            shape = char.translate(BULLET_SHAPES)
            while end < len(text) and text[end] in PUNCT_MARKS and text[end].translate(BULLET_SHAPES) == shape:
                end += 1
            out.append((index, text[index:end]))
            index = end
            continue
        index += 1
    return out


SHAPE_LINE = re.compile(r"形狀[：:][ \t]*([^\n]*)")


def bullet_contradicted(bullet: str, shapes: str | None) -> bool:
    """Whether the census's shape line (what it saw on the crop) names a
    bullet, but not BULLET: two readings of one crop that disagree on the
    shape.  Sealed as evidence beside the bullet's D5 entry
    (shapeContradicted), never asked about: the user does not want bullet
    shapes put to them, and the bullet stays as written.  A line naming no
    bullet says nothing about one, and ●, ⦿ and ◉, one or two of them, are
    one bullet (BULLET_SHAPES, the user's ruling)."""
    named = [c.translate(BULLET_SHAPES) for c in (shapes or "") if c in BULLET_MARKS]
    return bool(named) and bullet[:1].translate(BULLET_SHAPES) not in named


def doubled_shown(arguments: Any) -> bool:
    """Whether the census's doubled-circle count is put to the punctuator as a
    shape to write, or kept for the seal alone.

    The count is one reading of one crop; the profile's notation survey is the
    book's.  Where the survey looked and found no doubled circles, a crop count
    above zero is recorded and reported (doubled-count-mismatch) but not
    written: a false count would put 。。 into a book that prints none, and
    nothing on the page could correct it.  With no survey there is no book
    evidence against it and the crop's count stands.
    """
    notation = profile_section(arguments, "notation")
    return bool(notation.get("doubled")) or not notation.get("blocksSurveyed")


def census_hint(census: int | None, doubled: int | None, show: bool) -> str:
    """The census, told to the punctuator: places and, where they are to be
    written (doubled_shown), how many are doubled circles."""
    if census is None:
        return ""
    if show and doubled:
        shape = f"，其中 {doubled} 處係雙圈（兩個圈連住，寫「。。」）"
    elif show and doubled == 0:
        shape = "，冇雙圈"
    elif doubled is None:
        shape = "（兩個圈連住嘅雙圈算一處）"
    else:
        shape = ""
    return f"你先前數過呢個 block 有 {census} 處印咗符號{shape}；輸出應該接近呢個數，位置逐個由圖讀。\n"


# The forms engines and models write for the marks PUNCT_MARKS names: vertical
# presentation forms and ASCII brackets.
MARK_FORMS = str.maketrans({"︵": "（", "︶": "）", "(": "（", ")": "）",
                            "﹁": "「", "﹂": "」", "﹃": "『", "﹄": "』"})
# The marks a pass whose answers are both refused keeps where a refused
# answer and engine A put them at one place (agreed_punctuation): the marks
# the step counts, and the curly quotation marks, which it neither counts nor
# asks for but which a book in simplified script prints and both the
# punctuator and engine A write.  Measured on such a book (2026-10-25): on a
# page whose two answers were refused (12 quotation marks in each, 12 in
# engine A), the fallback kept 116 marks and no quotation mark (11 lost
# against the user's copy of the book); replayed offline on the sealed
# answers with these marks, 127, 11 quotation marks among them.  Only this
# fallback reads them (anchored_marks' VOCABULARY), so nothing else the step
# counts or places changes; the opening ones anchor to the character after.
OPENING_QUOTES = frozenset("“‘")
# Markup in an engine's gap (HTML tags and entities): not print.
GAP_MARKUP = re.compile(r"<[^>]*>|&[A-Za-z0-9#]+;")


def gap_sides(gap: str, by_print: bool = True) -> tuple[str, str]:
    """The marks of one gap of an engine's text (engine_gaps), in the order
    written, parted by where they go - (with the character before, with the
    character after) - for the text beside a table and a block written with
    the engines' marks (seam_sides, marks_as_written).  The marks are
    PUNCT_MARKS.

    Print in the gap that is no mark (digits or letters, which the merge
    does not align) parts it: the marks written before it go with the
    character before, the marks after it with the character after.  Engine A
    read a numbered item's head 。(2) between two characters; anchored_marks
    ties 。 and ) after the character before and ( before the one after, so
    the pair comes out reversed, 。）…（ (the replay of one book's sealed pages
    wrote 。） at the three such heads of a page, where the print is 。（2）).
    With no such print, the gap parts at its first opening mark, as
    anchored_marks ties them, but a mark written after an opening one stays
    after it: （） is not written ）（.  With BY_PRINT false the gap parts as
    though it held no such print (marks_as_written: nothing in the merged
    text stands for it)."""
    written = GAP_MARKUP.sub("", gap).translate(MARK_FORMS)
    parted = by_print and any(unicodedata.category(c)[0] in "LN" for c in written)
    lead, tail = [], []
    after = False
    for c in written:
        if c in PUNCT_MARKS:
            # With print in the gap, the marks after it; without, the marks
            # from the first opening one on.
            after = after or (c in OPENING_MARKS and not parted)
            (tail if after else lead).append(c)
        elif parted and unicodedata.category(c)[0] in "LN":
            after = True
    return "".join(lead), "".join(tail)


def seam_sides(gap: str, by_print: bool = True) -> tuple[str, str]:
    """The marks (PUNCT_MARKS) of one gap of an engine's text, in the order
    written, parted by where they go - (with the character before, with the
    character after) - for the text beside a table (marks_as_written).

    As gap_sides parts them (BY_PRINT as there), but a seam (TEXT_SEAMS: a
    line break, a table cell's markup) parts the gap first: a mark stays with
    the line it is written on - before the gap's first seam with the
    character before, after its last seam with the character after, between
    two seams (a line of marks alone) as gap_sides parts them - except that
    the opening marks and bullets that end the line before (after its last
    other mark or print) open the line after, and the closing and sentence
    marks that start the line after (before its first opening mark, bullet
    or print) close the line before: wrapped there, as a comma an engine
    writes at the head of the next line.  Engine B read a title ending in
    （） on its own line above a table: with no seam the pair goes with the
    table's first cell, as a mark after an opening one does, and the edge
    text lacks it.  A numbered head (2) or a bullet ▲ starting a line goes
    with that line, where the old order split the pair or gave the bullet to
    the line before.  The order written is kept: once a mark goes with the
    character after, so do the marks written after it.  Dashes are left
    out, as anchored_marks leaves them: nothing counts or keeps them here."""
    parts = TEXT_SEAMS.split(gap)
    if len(parts) == 1:
        lead, tail = gap_sides(gap, by_print)
        return "".join(c for c in lead if c in PUNCT_MARKS), "".join(c for c in tail if c in PUNCT_MARKS)
    opens = OPENING_MARKS | frozenset(BULLET_MARKS)

    def line(part: str) -> list[str]:
        """A line's marks and print (letters, digits), in the order written."""
        written = GAP_MARKUP.sub("", part).translate(MARK_FORMS)
        return [c for c in written if c in PUNCT_MARKS or unicodedata.category(c)[0] in "LN"]

    # (mark, whether it goes with the character before), in the order written.
    sides: list[tuple[str, bool]] = []
    first = line(parts[0])
    k = len(first)
    while k and first[k - 1] in opens:
        k -= 1
    sides += [(c, j < k) for j, c in enumerate(first) if c in PUNCT_MARKS]
    lead, tail = gap_sides("".join(parts[1:-1]), by_print)
    sides += [(c, True) for c in lead if c in PUNCT_MARKS] + [(c, False) for c in tail if c in PUNCT_MARKS]
    last = line(parts[-1])
    k = 0
    while k < len(last) and last[k] in PUNCT_MARKS and last[k] not in opens:
        k += 1
    sides += [(c, j < k) for j, c in enumerate(last) if c in PUNCT_MARKS]
    before, after, parted = [], [], False
    for c, with_before in sides:
        parted = parted or not with_before
        (after if parted else before).append(c)
    return "".join(before), "".join(after)


def anchored_marks(text: str, vocabulary: str = PUNCT_MARKS) -> tuple[str, list[tuple[int, bool, str]]]:
    """TEXT's characters (as CJK counts them) and its marks (those of
    VOCABULARY), each tied to a character: (index, before, mark) - before
    that character for an opening mark, after it for any other.  A mark with
    no character on its side (a closing mark before the first character, an
    opening one after the last) is tied to the nearest one."""
    chars, gaps = engine_gaps(text)
    out: list[tuple[int, bool, str]] = []
    for g, gap in enumerate(gaps):
        for mark in gap.translate(MARK_FORMS):
            if mark not in vocabulary:
                continue
            opening = mark in OPENING_MARKS or mark in OPENING_QUOTES
            if chars and (opening and g < len(chars) or g == 0):
                out.append((g, True, mark))
            elif chars:
                out.append((g - 1, False, mark))
    return chars, out


def tied_marks(length: int, marks: Sequence[tuple[int, bool, str]],
               place: Mapping[int, int]) -> tuple[list[str], list[str]]:
    """The marks tied after and before each of a text's LENGTH characters:
    PLACE maps a marked character (anchored_marks) to its index there; a mark
    whose character has no place is dropped."""
    after, before = [""] * length, [""] * length
    for index, is_before, mark in marks:
        at = place.get(index)
        if at is None:
            continue
        if is_before:
            before[at] += mark
        else:
            after[at] += mark
    return after, before


def marks_as_written(text: str, place: Mapping[int, int], length: int) -> tuple[list[str], list[str]]:
    """TEXT's marks tied after and before each of LENGTH characters (as
    tied_marks ties them, for gaps_of), in the order TEXT writes them, for
    the text beside a table (engine_marks_merged, engine_b_marks_merged with
    WRITTEN): each gap's marks parted by seam_sides, those that go with the
    character before tied after it and the rest before the character after
    (all of them to the nearest character past TEXT's ends).  PLACE maps
    TEXT's characters (engine_gaps) to their index there; a mark whose
    character has no place is dropped.

    Print in a gap that is no character (a bracketed Arabic numeral) parts
    it (gap_sides) only where the gap's two characters are not neighbours in
    the merged text - there the merge holds another engine's reading of that
    print, as engine A's 第(2)表 against the merge's 第二表 is 第（二）表.
    Where they are neighbours, nothing stands for the print, and the marks
    part as though it were not there: a bracket pair stays together, with
    the line it is written on where a seam parts the gap, else with the
    character after - at the edge of a stretch (gaps_of), both brackets or
    neither.

    anchored_marks ties every closing mark of a gap after the character
    before and every opening one before the character after, so a gap reads
    closing marks first: a bracket pair with no character between its
    brackets, 第(2)表 or 表（）, came out 第）（表, and at the edge of a stretch
    with one bracket, 表）.  A block written with the engines' marks
    (agreed_marks) has its marks placed the same way."""
    chars, gaps = engine_gaps(text)
    after, before = [""] * length, [""] * length
    for g, gap in enumerate(gaps):
        left = place.get(g - 1) if g > 0 else None
        right = place.get(g) if g < len(chars) else None
        lead, tail = seam_sides(gap, left is not None and right is not None and right != left + 1)
        if g == 0:
            lead, tail = "", lead + tail
        if g == len(chars):
            lead, tail = lead + tail, ""
        if lead and left is not None:
            after[left] += lead
        if tail and right is not None:
            before[right] += tail
    return after, before


def engine_marks_merged(text: str, segments: Sequence[Mapping[str, Any]],
                        pieces: Sequence[str], blocks: Sequence[Mapping[str, Any]] = (),
                        placed: dict[int, str] | None = None,
                        written: bool = False, vocabulary: str = PUNCT_MARKS) -> tuple[list[str], list[str]] | None:
    """The marks engine A printed, tied to the characters of the merged text
    ("".join(pieces)) - tied_marks, for gaps_of; with WRITTEN, in the order
    engine A wrote them (marks_as_written: the text beside a table).
    VOCABULARY: the marks read (anchored_marks; tied_marks only).

    A mark is tied to the character it goes with (anchored_marks) and placed
    only where that character's place in the merged text is exact: agreed, a
    disagreement settled on engine A's own reading, or a one-for-one
    substitution.  None when the segments do not spell engine A's text.

    A table block of an order-conflict page read from engine B's lines
    (window_merge) is a run of segments carrying "window": k.  Together they
    hold each of the block's characters once, but in engine B's order, so
    they spell the block's stretch of engine A's text only as a whole: the
    run stands for that stretch (block k's text, where BLOCKS is given), and
    no mark is placed inside it.  The text around it keeps its exact places.
    Before, one such block made the segments fail to spell engine A's text
    and every block of the page lost engine A's marks (agreed_punctuation).

    PLACED, when given, gets the merged text's characters whose place in
    engine A's reading is exact - those whose marks were placed - each with
    engine A's own character there.
    """
    chars, marks = anchored_marks(text, vocabulary)
    found = engine_places(chars, segments, pieces, blocks)
    if found is None:
        return None
    place, m = found
    if placed is not None:
        placed.update((at, chars[k]) for k, at in place.items())
    if written:
        return marks_as_written(text, place, m)
    return tied_marks(m, marks, place)


def engine_places(chars: str, segments: Sequence[Mapping[str, Any]], pieces: Sequence[str],
                  blocks: Sequence[Mapping[str, Any]] = ()) -> tuple[dict[int, int], int] | None:
    """Where each of engine A's characters CHARS stands in the merged text
    ("".join(pieces)) when its place there is exact (engine_marks_merged), and
    the merged text's length; None when the segments do not spell CHARS."""
    place: dict[int, int] = {}
    e = m = i = 0
    while i < len(segments):
        window = segments[i].get("window")
        if window is not None:
            j = i
            while j < len(segments) and segments[j].get("window") == window:
                j += 1
            own = "".join(seg["a"] for seg in segments[i:j])
            stretch = chars[e:e + len(own)]
            if Counter(stretch) != Counter(own) or (
                    blocks and (not isinstance(window, int) or not 0 <= window < len(blocks)
                                or CJK(str(blocks[window].get("text") or "")) != stretch)):
                return None
            e += len(own)
            m += sum(len(piece) for piece in pieces[i:j])
            i = j
            continue
        own, piece = segments[i]["a"], pieces[i]
        if chars[e:e + len(own)] != own:
            return None
        if piece == own or len(piece) == len(own):
            place.update((e + k, m + k) for k in range(len(own)))
        e += len(own)
        m += len(piece)
        i += 1
    if e != len(chars):
        return None
    return place, m


def gap_marks(gap: str) -> str:
    """One gap's marks as two readings are compared (agreed_marks): ●, ⦿ and
    ◉ one bullet (BULLET_SHAPES, the user's ruling) and a run of leader dots
    「……」 whatever its number of dots (leaders_written, D4)."""
    return leaders_written(gap.translate(BULLET_SHAPES))


# The rule agreedMarks (C1): a block whose marks both engines read alike, at
# least this many places, is written with them and neither counted nor
# punctuated (agreed_marks).
AGREED_MARKS_MINIMUM = 2


def engine_reading(text: str, place: Mapping[int, int] | None, length: int
                   ) -> tuple[list[int | None], str, list[str], tuple[list[str], list[str]]] | None:
    """An engine's reading TEXT against the merged text's LENGTH characters,
    PLACE mapping its characters to theirs where the place is exact
    (engine_places, engine_b_places): for each merged character the index of
    the engine's character there (None where none is), the engine's
    characters and the text in each gap between them (engine_gaps), and its
    marks tied to the merged characters in the order it wrote them
    (marks_as_written).  None when PLACE is."""
    if place is None:
        return None
    own: list[int | None] = [None] * length
    for k, at in place.items():
        own[at] = k
    chars, gaps = engine_gaps(text)
    return own, chars, gaps, marks_as_written(text, place, length)


def other_print(gap: str) -> bool:
    """Whether an engine's gap holds print the mark vocabulary does not:
    figures, Latin text, a note marker (an engine's LaTeX around one too), a
    dash, 〔〕, a middle dot (HTML markup and spaces aside; vertical forms and
    ASCII brackets are marks, MARK_FORMS)."""
    gap = GAP_MARKUP.sub("", gap).translate(MARK_FORMS)
    return any(not c.isspace() and c not in PUNCT_MARKS for c in gap)


# The circled numerals a note marker (註號) is written with.
CIRCLED_NUMERALS = ("①-⑳⓪-⓿㉑-㉟㊱-㊿❶-❿")
# Note markers are set aside in the comparison (gap_print): the two engines
# write and misread them differently, and they are restored by another
# mechanism.  Engine A's LaTeX around one, or a run of digits and circled
# numerals with a circled numeral in it; a run of plain digits (a year) stays.
NOTE_MARKER_PRINT = re.compile(
    rf"\$[^$]*\$|(?=[0-9{CIRCLED_NUMERALS}]*[{CIRCLED_NUMERALS}])[0-9{CIRCLED_NUMERALS}]+")


def gap_print(gap: str) -> str:
    """One gap's text as the two engines' print is compared (agreed_marks,
    same_print): its markup out, its vertical and ASCII forms folded
    (MARK_FORMS), its whitespace deleted, its note markers (NOTE_MARKER_PRINT)
    out."""
    return NOTE_MARKER_PRINT.sub("", "".join(GAP_MARKUP.sub("", gap).translate(MARK_FORMS).split()))


# Switch --same-marks-any-form: the forms in which the two engines write one
# mark or one piece of print, made one before their readings are compared
# (agreed_marks; the page is written in engine A's own forms).  ASCII marks
# for the full-width ones, straight quotation marks for the curly ones, and
# the square bracket in its three shapes.  Measured on 160 pages of eight
# modern books set in horizontal lines (2026-11-19): of 921 body blocks whose
# characters both engines read alike, 393 had the same marks as written, and
# 472 more once these forms were made one - engine B wrote "[" for 870 of 881
# brackets engine A wrote 〔 or 【, and ASCII commas, colons and quotation
# marks where engine A wrote the full-width ones.  All 472 were asked about;
# the answers of 491 of 558 such questions were the engines' own marks, and
# the rest mostly left out what both engines had read (97 square brackets).
# Angle brackets are not folded: "<" and ">" are an engine's markup too, and
# where one engine wrote 《》 and the other 〈〉 the page decides.  "#" goes:
# engine B writes a heading's line as Markdown ("## （一）"), which is no
# print (on one book's 20 pages it kept 20 blocks out).
FORM_FOLD = str.maketrans({",": "，", ";": "；", ":": "：", "?": "？", "!": "！",
                           "“": '"', "”": '"', "＂": '"', "‘": "'", "’": "'", "＇": "'",
                           "[": "〔", "]": "〕", "［": "〔", "］": "〕", "【": "〔", "】": "〕",
                           "☐": "□", "#": None})


# What --same-marks-any-form writes of engine A's gaps besides the marks the
# step counts (PUNCT_MARKS): the print a punctuator copies off the crop when
# it is asked - quotation marks, square brackets, a dash, a middle dot.
# Without them a block the switch takes would lose them: measured on 15 pages
# of one of those books, the sealed pages held 96 square brackets and 102
# curly quotation marks, and the marks alone left 14 and 18.  Figures, Latin
# text, 「□」 and note markers stay with the rules that write them later, as
# for a block that is asked about.
KEPT_PRINT = frozenset("“”‘’\"'〔〕【】［］[]—·")


# --same-marks-any-form takes a block with a single place holding a mark (the
# rule's own minimum is AGREED_MARKS_MINIMUM) on a page where the blocks it
# took hold this many such places.  Measured on those 160 pages: of 55 such
# blocks the two engines read alike, every answer wrote the engines' mark.
# A block with no mark in either engine stays asked: on older print the
# proofreader wrote marks in 15 of 62 such blocks (unmarked_candidates).
ANY_FORM_PAGE_PLACES = 20


def gap_as_written(gap: str) -> str:
    """One of engine A's gaps as --same-marks-any-form writes it: its marks
    (MARK_FORMS) and its KEPT_PRINT, in the order it wrote them; markup, note
    markers, figures, other print and white space left out."""
    gap = NOTE_MARKER_PRINT.sub("", GAP_MARKUP.sub("", gap)).translate(MARK_FORMS)
    return "".join(c for c in gap if c in PUNCT_MARKS or c in KEPT_PRINT)


def agreed_marks(span: str, start: int, readings: Sequence[tuple[list[int | None], str, list[str],
                                                                 tuple[list[str], list[str]]] | None],
                 *, same_print: bool = False,
                 write: tuple[list[int | None], str, list[str], tuple[list[str], list[str]]] | None = None,
                 minimum: int = AGREED_MARKS_MINIMUM) -> str | None:
    """SPAN, a block's stretch of the merged text from START, with the marks
    both engines printed there, when the rule agreedMarks takes it: in each
    of the two READINGS (engine_reading) SPAN is an exact stretch - every
    character the engine's own, the same character, in consecutive places -
    with no print beyond the marks in its gaps (other_print; at its two ends
    only the part on the stretch's side of a line break), and at each of
    SPAN's gaps the two engines wrote the same marks (gap_marks: the three
    bullets one shape, a run of leader dots one), at least
    AGREED_MARKS_MINIMUM places (mark_places).  Engine A's marks are written.
    WRITE (--same-marks-any-form): READINGS are the two engines' texts with
    their forms made one (FORM_FOLD), and what is written is WRITE's, engine
    A's reading as it wrote it: its marks and, with them, its quotation
    marks, square brackets, dashes and middle dots (gap_as_written), which
    both engines read - but only where that text, its marks alone, is the
    text the marks tied to the characters give; None where it is not (a mark
    engine A wrote in a form the step does not count).
    None otherwise: the block is counted and punctuated, and the punctuator
    writes off the crop the print the merged text leaves out (it holds
    characters alone) - a year, a page number, a Western name, a note marker.

    The census, punctuate and count-retry calls were 81% of the GPU time of a
    modern book's phase 3.  Replayed offline on three finished books (this
    code, a made-up model; the cost from their seals): the rule takes 280 of
    the modern book's 4,401 passes (3.9% of its completion tokens: most of
    its blocks hold figures, note markers or Western text), 949 of 2,047 and
    1,256 of 2,613 passes of two older horizontally set books (30% and 32%);
    on the modern book's golden pages its 4 blocks are right at all 363
    gaps.  Without the condition on other print it took 3,139 of the modern
    book's blocks, and the sealed pages hold 5,854 characters of print in
    1,712 of them (figures, note markers, 〔〕, middle dots, Latin) that the
    engines' marks would have dropped.  A vertical page seldom qualifies:
    engine B read 66 marks in a whole vertically set book."""
    if len(readings) != 2 or not span:
        return None
    gaps = []
    prints = []
    for reading in readings:
        if reading is None:
            return None
        own, chars, written, tied = reading
        first = own[start] if start < len(own) else None
        if first is None or start + len(span) > len(own) or any(
                own[start + k] != first + k or chars[first + k] != char for k, char in enumerate(span)):
            return None
        ends = [TEXT_SEAMS.split(written[first])[-1], TEXT_SEAMS.split(written[first + len(span)])[0]]
        if not same_print and any(other_print(gap) for gap in written[first + 1:first + len(span)] + ends):
            return None
        prints.append([ends[0]] + written[first + 1:first + len(span)] + [ends[1]])
        gaps.append(gaps_of(tied, start, len(span)))
    if same_print and any((other_print(x) or other_print(y)) and gap_print(x) != gap_print(y)
                          for x, y in zip(*prints)):
        return None
    if any(gap_marks(x) != gap_marks(y) for x, y in zip(*gaps)):
        return None
    out = gaps[0] if write is None else gaps_of(write[3], start, len(span))
    text = "".join(gap + char for gap, char in zip(out, span)) + out[-1]
    if mark_places(text) < minimum:
        return None
    if write is None:
        return text
    own, _, written, _ = write
    first = own[start]
    inner = [gap_as_written(gap) for gap in written[first + 1:first + len(span)]]
    full = (gap_as_written(TEXT_SEAMS.split(written[first])[-1]) + span[0]
            + "".join(gap + char for gap, char in zip(inner, span[1:]))
            + gap_as_written(TEXT_SEAMS.split(written[first + len(span)])[0]))
    marks_only = lambda value: gap_marks("".join(c for c in value if c in PUNCT_MARKS or CJK(c)))
    return full if marks_only(full) == marks_only(text) else None


# Switch --unmarked-page-sample: a page neither engine read more than
# UNMARKED_PAGE_MARKS marks on is asked about UNMARKED_SAMPLE of its blocks
# first, the longest, and the others are taken as printed without marks when
# every one of those is answered so (unmarked_candidates).
UNMARKED_PAGE_MARKS = 2
UNMARKED_SAMPLE = 3


def unmarked_candidates(blocks: Sequence[Mapping[str, Any]], spans: Sequence[str], indices: Sequence[int],
                        draft2: str | None) -> list[int]:
    """The blocks of INDICES (the page's blocks to punctuate) that may be
    taken as printed without marks on the word of a sample, switch
    --unmarked-page-sample: none unless the page is one neither engine read
    marks on - at most UNMARKED_PAGE_MARKS in all of engine A's BLOCKS, and
    as few in engine B's reading DRAFT2 - and then each block engine A read
    as characters alone: no mark, and no other print (a figure, Latin text, a
    note marker), which the punctuator writes off the crop and a block not
    asked would lose.

    The page is the unit, not the block: engine B's reading is not tied to
    blocks where the engines read different characters, and a block both
    engines read without marks on a page they read marks on is no evidence -
    measured on the benchmark's 28 samples (2026-11-17, a small model
    answering): of 62 such blocks outside the one unpunctuated book the
    proofreader wrote marks in 15, 80 marks in all.  On that book's 11 pages
    neither engine read more than two marks a page, 244 blocks were asked, and
    241 were answered as printed without marks: a third of the benchmark's
    questions.
    """
    if draft2 is None:
        return []
    read = lambda text: sum(punctuation_counts(text).values())
    if read("\n".join(str(block.get("text") or "") for block in blocks)) > UNMARKED_PAGE_MARKS \
            or read(draft2) > UNMARKED_PAGE_MARKS:
        return []
    return [index for index in indices
            if CJK(spans[index]) and all(c.isspace() or CJK(c) for c in str(blocks[index].get("text") or ""))]


def engine_b_places(chars: str, segments: Sequence[Mapping[str, Any]],
                    pieces: Sequence[str]) -> dict[int, int] | None:
    """Where each of engine B's characters (CHARS, as engine_gaps counts
    them) stands in the merged text ("".join(pieces)), where its place is
    exact: the merge wrote engine B's reading there, or one character for
    one.  Segments that carry their place in engine B's text (bStart: text
    engine B read in another order was placed at engine A's site,
    place_moved_once) are read by it.  None when the segments do not spell
    engine B's text."""
    if segments and all("bStart" in x for x in segments):
        b_at = [x["bStart"] for x in segments]
    else:
        b_at = list(itertools.accumulate((len(x["b"]) for x in segments), initial=0))
        if b_at[-1] != len(chars):
            return None
    place: dict[int, int] = {}
    m = 0
    for seg, piece, e in zip(segments, pieces, b_at):
        own = seg["b"]
        if chars[e:e + len(own)] != own:
            return None
        if own and (piece == own or len(piece) == len(own)):
            place.update((e + k, m + k) for k in range(len(own)))
        m += len(piece)
    return place


def engine_b_marks_merged(text: str, segments: Sequence[Mapping[str, Any]], pieces: Sequence[str],
                          placed: dict[int, str] | None = None,
                          written: bool = False) -> tuple[list[str], list[str]] | None:
    """The marks engine B printed, tied to the characters of the merged text
    as engine_marks_merged ties engine A's (WRITTEN as there): placed only
    where a character's place is exact (engine_b_places).  None when the
    segments do not spell engine B's text.  PLACED gets the merged characters
    placed, each with engine B's own character there.  For the text beside a
    table that only engine B read (table_edges): no punctuation pass reads a
    table block's text."""
    chars, marks = anchored_marks(text)
    place = engine_b_places(chars, segments, pieces)
    if place is None:
        return None
    if placed is not None:
        placed.update((at, chars[k]) for k, at in place.items())
    if written:
        return marks_as_written(text, place, sum(len(piece) for piece in pieces))
    return tied_marks(sum(len(piece) for piece in pieces), marks, place)


def engine_b_lines_merged(text: str, segments: Sequence[Mapping[str, Any]],
                          pieces: Sequence[str]) -> list[int] | None:
    """For each character of the merged text ("".join(pieces)), the line of
    engine B's reading TEXT it was read in - its lines and its table cells,
    split where TEXT_SEAMS splits (a line break, a cell's markup), numbered
    in engine B's order - where its place is exact (engine_b_places), and
    -1 elsewhere.  None when the segments do not spell engine B's text.
    For the text beside a table (edge_verdicts): what engine B read on one
    line is judged as one."""
    chars, gaps = engine_gaps(text)
    place = engine_b_places("".join(chars), segments, pieces)
    if place is None:
        return None
    line, number = [], 0
    for k in range(len(chars)):
        if k and TEXT_SEAMS.search(gaps[k]):
            number += 1
        line.append(number)
    out = [-1] * sum(len(piece) for piece in pieces)
    for k, at in place.items():
        out[at] = line[k]
    return out


def marked_stretch(merged: str, lo: int, hi: int,
                   engines: Sequence[tuple[tuple[list[str], list[str]] | None, Mapping[int, str]]]) -> str:
    """MERGED[LO:HI] with its marks: in each gap, the marks of the first of
    ENGINES (each an engine's marks tied to the merged text, and the
    characters it placed; engine_marks_merged, engine_b_marks_merged) that
    read a character beside the gap exactly, as read.  A mark tied to a
    character outside the stretch is not the stretch's (gaps_of)."""
    length = hi - lo
    shown = [(gaps_of(tied, lo, length), placed) for tied, placed in engines if tied is not None]
    out = []
    for g in range(length + 1):
        for gaps, placed in shown:
            if (g > 0 and lo + g - 1 in placed) or (g < length and lo + g in placed):
                out.append(gaps[g])
                break
        if g < length:
            out.append(merged[lo + g])
    return "".join(out)


def agreed_punctuation(span: str, refused: Sequence[str], printed: Sequence[str],
                       doubled: bool) -> tuple[str, int]:
    """SPAN with the marks a refused punctuation answer and engine A both put
    at the same gap, and how many.

    A refused answer is refused for its characters (a character dropped or
    changed), its count or its density, not for its marks, and engine A's
    own marks are an independent reading of the same print: measured on one
    magazine page, engine A wrote 44 of the 45 printed marks and none wrong,
    while both punctuation answers on six of its blocks were refused (they
    dropped engine B's look-alike characters for the marks they are) and the
    page kept 11.  Offline, keeping the marks the refused answers and engine
    A agree on took it to 39 of 45 with 40 written.  PRINTED holds engine A's
    marks at each of SPAN's gaps (engine_marks_merged, gaps_of).  Each
    answer's marks are placed on SPAN by aligning its characters, and at each
    gap a mark is kept as often as both wrote it; the answer that agrees more
    is taken gap by gap, never the two added together.  A circle is kept only
    as the same run: 。 against 。 or 。。 against 。。.  Where the book prints
    doubled circles (DOUBLED) engine A's single 。 says a circle is there but
    not whether one or two - engine A wrote 17 。。 in two such books, and on
    one book's golden pages 55 of its 470 marks were right - so it agrees
    with nothing.
    """
    best: list[str] = [""] * (len(span) + 1)
    for answer in refused:
        chars, marks = anchored_marks(answer, FALLBACK_MARKS)
        matcher = difflib.SequenceMatcher(None, chars, span, autojunk=False)
        place = {block.a + k: block.b + k for block in matcher.get_matching_blocks()
                 for k in range(block.size)}
        for g, (ours, theirs) in enumerate(zip(gaps_of(tied_marks(len(span), marks, place)), printed)):
            wanted = Counter(ours) & Counter(theirs)
            circles = theirs.count("。")
            wanted["。"] = circles if (ours.count("。") == circles and not (doubled and circles == 1)) else 0
            kept, left = [], +wanted
            for mark in theirs:
                if left[mark] > 0:
                    kept.append(mark)
                    left[mark] -= 1
            if len(kept) > len(best[g]):
                best[g] = "".join(kept)
    text = "".join(best[g] + span[g] for g in range(len(span))) + best[len(span)]
    return text, sum(map(len, best))


def realign_answer(block_span: str, lo: int, hi: int, answer: str, reach: int) -> tuple[int, int, int] | None:
    """Where in its block a punctuation answer for the stretch [LO, HI) of
    BLOCK_SPAN reads, when its characters are not that stretch's: (a, b, how
    many of its characters are another form of the block's), for the stretch
    [a, b) whose characters, variant-folded (variant_fold), are exactly the
    answer's, with a and b each within REACH of LO and HI - the nearest such.
    None when there is none: an answer that drops, adds or changes a
    character inside the stretch is no reading of any stretch.

    A column group's text is scaled from its columns' measured lengths
    (block_tiles), and a bracket takes a whole cell in vertical print, so a
    tile's text drifts off its crop by a few characters at either end.  The
    punctuator writes the text its crop shows.  Measured on one page, in six
    runs of two code versions: tile 0's answer left out the four characters
    printed at the head of tile 1's crop, tile 1's added them and left out
    its last one, printed on tile 2's crop; both were refused as "characters
    changed" and the stretches kept no circle (recall 41-51%).  Realigned and
    their marks seated on the block's characters, offline: 77-85%.  REACH 0
    folds variants alone (an answer that wrote another form of a character
    the variant lists rule one with the merge's)."""
    got = variant_fold(CJK(answer))
    if not got:
        return None
    folded = variant_fold(block_span)
    found = [(abs(a - lo) + abs(a + len(got) - hi), a) for a in range(max(0, lo - reach), lo + reach + 1)
             if a + len(got) <= len(block_span) and abs(a + len(got) - hi) <= reach
             and folded[a:a + len(got)] == got]
    if not found:
        return None
    a = min(found)[1]
    b = a + len(got)
    return a, b, sum(1 for x, y in zip(CJK(answer), block_span[a:b]) if x != y)


def seat_marks(answer: str, stretch: str) -> str:
    """ANSWER's marks on STRETCH's characters, by position: the answer's k-th
    character stands for STRETCH's k-th (realign_answer found them the same
    but for variant forms).  The characters written are always STRETCH's."""
    chars, marks = anchored_marks(answer)
    after, before = tied_marks(len(stretch), marks, {k: k for k in range(min(len(chars), len(stretch)))})
    return "".join(before[k] + stretch[k] + after[k] for k in range(len(stretch)))


def assemble_passes(span: str, parts: Sequence[tuple[str, int, int, int, int]]) -> tuple[str, list[tuple[int, int]]]:
    """A block's punctuated text from its passes, each (text, a, b, lo, hi): the
    text of SPAN's characters [a, b) it read - its own stretch [lo, hi), or
    another where its answer was realigned (realign_answer).  The passes'
    texts joined when every pass read its own stretch; otherwise every mark on
    SPAN's characters by position, from the pass that read the character - a
    character two passes read is the one's that read it outside its own
    stretch, which it can only have seen on its crop.  Also the stretches of
    SPAN no pass read, which keep no mark."""
    if all((a, b) == (lo, hi) for _, a, b, lo, hi in parts):
        return "".join(text for text, *_ in parts), []
    after, before = [""] * len(span), [""] * len(span)
    reader: list[bool | None] = [None] * len(span)
    for text, a, b, lo, hi in parts:
        chars, marks = anchored_marks(text)
        own_after, own_before = tied_marks(b - a, marks, {k: k for k in range(min(len(chars), b - a))})
        for k in range(b - a):
            outside = not (lo <= a + k < hi)
            if reader[a + k] is None or (outside and not reader[a + k]):
                reader[a + k] = outside
                after[a + k], before[a + k] = own_after[k], own_before[k]
    unread = [(k, k + 1) for k, who in enumerate(reader) if who is None]
    merged: list[tuple[int, int]] = []
    for lo, hi in unread:
        if merged and merged[-1][1] == lo:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return "".join(before[k] + span[k] + after[k] for k in range(len(span))), merged


def agreed_readings(block_span: str, first: tuple[str, Sequence[int]], second: tuple[str, Sequence[int]]
                    ) -> tuple[str, int, list[int]] | None:
    """The marks two punctuation answers put at the same place, each (text on
    BLOCK_SPAN's characters, the stretch [a, b) it read): the text of the
    stretch both read, with those marks, how many, and the stretch.  A circle
    only as the same run (。 against 。, 。。 against 。。), as
    agreed_punctuation holds them.  None when the stretches do not meet."""
    (text_1, (a_1, b_1)), (text_2, (a_2, b_2)) = first, second
    a, b = max(a_1, a_2), min(b_1, b_2)
    if a >= b:
        return None

    def tied(text: str, length: int) -> tuple[list[str], list[str]]:
        chars, marks = anchored_marks(text)
        return tied_marks(length, marks, {k: k for k in range(min(len(chars), length))})

    def both(x: str, y: str) -> str:
        wanted = Counter(x) & Counter(y)
        if x.count("。") != y.count("。"):
            wanted["。"] = 0
        kept = []
        for mark in x:
            if wanted[mark] > 0:
                kept.append(mark)
                wanted[mark] -= 1
        return "".join(kept)

    after_1, before_1 = tied(text_1, b_1 - a_1)
    after_2, before_2 = tied(text_2, b_2 - a_2)
    out, kept = [], 0
    for k in range(a, b):
        ahead, behind = both(before_1[k - a_1], before_2[k - a_2]), both(after_1[k - a_1], after_2[k - a_2])
        out.append(ahead + block_span[k] + behind)
        kept += len(ahead) + len(behind)
    return "".join(out), kept, [a, b]


def punctuate_part(client: "Client", arguments: Any, crop: bytes | None, span: str, hint: str,
                   limit: float, printed: Sequence[str] | None = None,
                   block: tuple[str, int, int, int] | None = None
                   ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Census then punctuation for one block or column group, against its crop.

    The census reports the places a mark is printed and, apart, how many of
    them are doubled circles (CENSUS_SYSTEM); the punctuator is told both
    (census_hint), and an answer is held to the places, so a pair written 。。
    is never refused as one mark too many.  Measured on one doubled-circle
    book's golden stretches over eight runs: where the census had counted the
    pairs as two marks the first answers wrote about all of them as 。。 (787
    of 700 golden), where it counted a pair as one they wrote 67 of 2,202, and
    the count gate refused right answers (44 marks written, 22 counted - 21
    doubled circles and a circle).  When the answer's doubled circles stray
    from the count by more than the tolerance, one retry names both numbers,
    and whichever answer is nearer the count is kept.

    Runs of printed dots - leaders, long ellipses - are counted off the crop
    with no model (leader_count, decision D4): the punctuator is told how many
    to write as 「……」, and the places an answer is held to are never fewer
    (a census of 0 on a table of contents printing 7 leaders skipped the pass).

    BLOCK is (the block's text, where SPAN is in it [lo, hi), a column's
    worth of characters): an answer refused for its characters that reads a
    stretch of the block within that reach of SPAN's ends, but for variant
    forms (realign_answer), is that stretch's reading - the tile's text
    drifted off its crop - and is kept, its marks seated on the block's own
    characters (seat_marks; the merged characters are written, never the
    answer's), and the pass records it (realigned: read, range, variants
    folded) and returns the stretch's text.  Reach 0: variant forms alone.
    A retry is not trusted so far: a retry is told the census, and measured
    on the drifted tiles of one page (four runs) the retries that read their
    stretch padded their marks toward the count (22 places to 31, with 7 of
    them wrong), where the first answers had 1-3 wrong.  A retry that needs
    realigning keeps only the marks it and the first answer (realigned, or
    on the tile's own characters) put at one place (agreed_readings): the
    pass stays refused and partial.  Offline on those tiles: 13-21 marks
    kept per tile with 0-1 wrong, against 16-24 with 2-10 wrong for the
    retry alone.

    Notes are counted too.  Measured on 30 scans of a horizontally set book
    with many footnotes, run twice each way: with no census on its footnotes
    and bibliography entries, the differing marks there were right on the
    image at 10 and 9 places against 21 and 24 with it - the count refusal's
    retry is what writes back a Western citation, a dash or a note marker
    the first answer left out.
    """
    used: list[dict[str, Any]] = []

    def judge(text: str) -> tuple[bool, str, str, dict[str, Any] | None]:
        """punctuation_pass_ok on TEXT, or on its realigned reading (BLOCK):
        (ok, reason, the text kept, the realignment)."""
        ok, reason = punctuation_pass_ok(span, text, limit, census)
        if ok or block is None or not reason.startswith("characters"):
            return ok, reason, text, None
        whole, lo, hi, reach = block
        found = realign_answer(whole, lo, hi, text, reach)
        if not found:
            return ok, reason, text, None
        a, b, folded = found
        seated = seat_marks(text, whole[a:b])
        if not punctuation_pass_ok(whole[a:b], seated, limit, census)[0]:
            return ok, reason, text, None
        return True, "", seated, {"read": [a, b], "range": [lo, hi], "variantsFolded": folded}

    punct_system = PUNCT_SYSTEM
    leaders = facade_attr("leader_count", leader_count)(crop)
    show = doubled_shown(arguments)
    # Census first: how many marks are PRINTED here. A count read from the
    # image gates the pass (zero printed -> nothing to add) and checks its
    # output; measured without it, an unpunctuated 1910 report came back with
    # 。and ，throughout.
    ceiling = census_ceiling(arguments, len(CJK(span)))
    fused = punctuation_mode(arguments, client) == "fused"
    fusion = {"attempted": fused, "success": False, "fallbackReason": None}
    fused_out = ""
    if fused:
        census_text, fused_out, m = _punctuation_fusion.ask_fused(client,
            crop_bytes=crop,
            span=span,
            hint=hint,
            leaders=leaders,
            show_doubled=show,
            max_tokens=arguments.max_tokens,
            census_system=CENSUS_SYSTEM,
            punct_system=punct_system,
        )
        used.append(m)
        n = parse_census(census_text)
        doubled_n = parse_census(census_text, DOUBLED_LINE)
        problem = m.get("fusionFormatProblem") or ("invalid-census" if n is None else "")
        if not problem and n + (doubled_n or 0) > ceiling:
            problem = "census-over-ceiling"
        if problem:
            # A malformed fusion never weakens the census gate to unknown:
            # ask the independent count and punctuation questions as before.
            fusion["fallbackReason"] = problem
            fused = False
    if not fused:
        census_text, m = client.ask_answering(
            CENSUS_SYSTEM, "數清楚呢個 block 印咗幾多個標點符號。", crop, arguments.max_tokens, kind="census")
        used.append(m)
    census = parse_census(census_text)
    doubled_census = parse_census(census_text, DOUBLED_LINE)
    # The shapes the census saw, as it wrote them: evidence for a bullet's
    # shape (bullet_contradicted).
    shape_line = SHAPE_LINE.search(census_text or "")
    if census is not None and census + (doubled_census or 0) > ceiling:
        # More marks than this book ever prints per character is not a count of
        # marks (measured: 178 for a 430-character block - it counted
        # characters). Unknown, not zero.
        census = doubled_census = None
    counted = {"censusConvention": "places", "doubledCensus": doubled_census,
               "punctuationMode": getattr(arguments, "punctuation_mode", "auto"),
               "punctuationModeEffective": punctuation_mode(arguments, client), "fusion": fusion}
    if shape_line and any(c in PUNCT_MARKS or c in BULLET_MARKS for c in shape_line.group(1)):
        counted["censusShapes"] = shape_line.group(1).strip()[:40]
    if leaders is not None:
        counted["leaderCensus"] = leaders
    if leaders and census is not None and census < leaders:
        # The dot runs on the crop are printed marks the model's count left
        # out: the census held to is at least that many.
        counted["modelCensus"], census = census, leaders
    if census == 0:
        fusion["success"] = fused
        return span, {"ok": True, "marks": 0, "places": 0, "census": 0, "reason": "no printed marks", **counted}, used
    told = census_hint(census, doubled_census, show) + (
        f"裁圖上量到 {leaders} 串點（導引點線或者刪節號），每串寫「……」。\n" if leaders else "")
    if fused:
        out = fused_out
    else:
        ask = (f"已定嘅字（唔准改）：\n---\n{span}\n---\n" + told + hint +
               "睇裁圖，逐欄加返頁上印咗嘅標點；唔准加、刪、改任何漢字。")
        out, m = client.ask_answering(punct_system, ask, crop, arguments.max_tokens, kind="punctuate")
        used.append(m)
    out = strip_fence(out).strip()
    first = {"firstAnswerMarks": sum(punctuation_counts(out).values()), "firstAnswerDoubled": out.count("。。")}
    answered = out
    ok_p, reason_p, out, realigned = judge(out)
    # The first answer on the block's characters and the stretch it read,
    # whatever its count: what a realigned retry is held to.
    first_read = ((out, realigned["read"] if realigned else [block[1], block[2]])
                  if ok_p and block is not None else None)
    agreement = None
    if ok_p and census is not None:
        places = mark_places(out)
        if abs(places - census) > census_tolerance(census):
            reason_p = f"{places} places marked but {census} counted on the page"
            ok_p = False
    # Kept when refused: a refused pass leaves its text bare, and without the
    # output nobody can tell a wrong refusal from a right one afterwards.
    refused = [] if ok_p else [answered]
    fusion["success"] = fused and ok_p
    if not ok_p:
        if fused:
            fusion["fallbackReason"] = "punctuation-validation"
        realigned = None
        # The retry is told the census too (it was not, and a retry after a
        # count refusal then wrote to no number at all).
        retry, m = client.ask_answering(
            punct_system,
            f"已定嘅字（唔准改）：\n---\n{span}\n---\n"
            f"你上一次嘅輸出被拒絕，原因：{reason_p}。" + told + hint +
            "淨係加圖上印咗嘅標點，字要同上面一模一樣、次序都唔准變。重新輸出。",
            crop, arguments.max_tokens, kind="punctuate-retry")
        used.append(m)
        retry = strip_fence(retry).strip()
        ok_r, _, kept_retry, realigned_retry = judge(retry)
        if ok_r and realigned_retry is None:
            out, ok_p, reason_p, realigned = kept_retry, True, reason_p + " (after retry)", realigned_retry
        else:
            refused.append(retry)
            if ok_r and first_read is not None:
                # A retry that reads another stretch than its tile's: only
                # what it and the first answer agree on (agreed_readings).
                agreement = agreed_readings(block[0], first_read, (kept_retry, realigned_retry["read"]))
    record = {"ok": ok_p, "census": census, "reason": reason_p, **counted, **first}
    if ok_p and refused:
        # The first answer, refused, when the retry was kept: sealed too, so
        # a retry far off the census can be audited against it.
        record["firstAnswer"] = refused[0][:2000]
    if ok_p and show and doubled_census is not None and (
            abs(out.count("。。") - doubled_census) > census_tolerance(doubled_census)):
        # The shape: as many doubled circles as the census found, within the
        # tolerance the places are held to.  One retry that names both
        # numbers; the answer nearer the count stands, and only one that keeps
        # the characters and the places.
        if fused:
            fusion["success"] = False
            fusion["fallbackReason"] = "doubled-circle-validation"
        again, m = client.ask_answering(
            punct_system,
            f"已定嘅字（唔准改）：\n---\n{span}\n---\n" + told +
            f"你上一次寫咗 {out.count('。。')} 處雙圈「。。」，但數過嘅係 {doubled_census} 處"
            f"（{census} 處符號入面）。返去裁圖逐個符號睇清楚：係一個圈，定係兩個圈連住。" + hint +
            "淨係加圖上印咗嘅標點，字要同上面一模一樣、次序都唔准變。重新輸出。",
            crop, arguments.max_tokens, kind="punctuate-shape")
        used.append(m)
        again = strip_fence(again).strip()
        good, _, kept_again, realigned_again = judge(again)
        good = good and (census is None or abs(mark_places(kept_again) - census)
                         <= census_tolerance(census))
        nearer = abs(again.count("。。") - doubled_census) < abs(out.count("。。") - doubled_census)
        record["shapeRetry"] = {"doubled": again.count("。。"), "marks": sum(punctuation_counts(again).values()),
                                "kept": bool(good and nearer)}
        if good and nearer:
            out, realigned = kept_again, realigned_again
    if not ok_p:
        out = span
        record["refusedOutputs"] = [text[:2000] for text in refused]
        if agreement is not None and agreement[1]:
            # Both answers read their stretches (realign_answer): the marks
            # they agree on, on the block's characters.  Still a refused pass.
            out, kept, read = agreement
            record["partial"] = {"kept": kept, "agreedWith": "both answers"}
            record["realigned"] = {"read": read, "range": [block[1], block[2]], "variantsFolded": None}
        elif printed is not None and len(printed) == len(span) + 1 and CJK(span) == span:
            # Both answers refused: keep what a refused answer and engine A's
            # own marks agree on (agreed_punctuation), never the bare text
            # alone when two readings put a mark there.  Still a refused pass:
            # the seal says partial and the page is not clean.  Doubled
            # circles: this book's profile, the census on this crop, or the
            # answers themselves.
            doubled = bool(profile_section(arguments, "notation").get("doubled")) or any(
                "。。" in text for text in refused) or bool(doubled_census)
            partial, kept = agreed_punctuation(span, refused, printed, doubled)
            if kept and punctuation_pass_ok(span, partial, limit)[0]:
                out = partial
                record["partial"] = {"kept": kept, "engineA": sum(map(len, printed)),
                                     "doubledCircles": doubled}
    if ok_p and realigned:
        record["realigned"] = realigned
    record["marks"] = sum(punctuation_counts(out).values())
    # How many of the marks are doubled circles: a census that counts each as
    # one mark sees half of them (measured on a sealed page: census 23 where
    # 38 marks were kept, 15 doubled circles among them - 23 counting each as
    # one), so the reader of a count mismatch needs the number.
    record["doubled"] = out.count("。。")
    # And the places they hold as the places census counts them (mark_places:
    # a doubled circle one, a bullet printed twice one), which the pass was
    # held to: what the seal holds the kept stretch to (punctuation_doubts).
    record["places"] = mark_places(out)
    # The leaders and bullets kept (decisions D4, D5), for decisionChanges.
    record["leaders"] = punctuation_counts(out)[LEADER]
    record["bullets"] = sum(out.count(bullet) for bullet in BULLET_MARKS)
    return out, record, used


def leader_runs(text: str) -> list[tuple[int, int]]:
    """Where TEXT holds runs of leader dots, as (start, end) in TEXT (D4)."""
    return [(m.start(), m.end()) for m in re.finditer(f"[{LEADER_MARKS}]+", text)]


def leaders_written(text: str) -> str:
    """TEXT with each run of leader dots written 「……」 (D4: whatever the number
    of dots printed or written)."""
    runs = leader_runs(text)
    for start, end in reversed(runs):
        text = text[:start] + LEADER + text[end:]
    return text


def leader_rewrites(text: str) -> tuple[str, list[dict[str, Any]]]:
    """A page's text with each run of leader dots written 「……」 (D4), and a
    decisionChanges entry for each run that was not: one a structure pass
    wrote with another number of dots than its block pass (the lock counts a
    run as one mark) or a recovered table's cell holds.  Page offsets, since
    the formatter drops furniture.  The run as written is what the page held."""
    chars = CJK(text)
    out: list[dict[str, Any]] = []
    for start, end in leader_runs(text):
        if text[start:end] == LEADER:
            continue
        at = len(CJK(text[:start]))
        out.append({
            "decision": "D4", "rule": "dot-leader-rewritten", "block": None,
            "contextBefore": chars[max(0, at - 4):at], "contextAfter": chars[at:at + 4],
            "mergedOffset": None, "pageOffset": at,
            "after": LEADER, "evidence": {"written": text[start:end]},
        })
    return leaders_written(text), out


# A block of engine A's layout that lists entries, each with a leader and
# its page number (a contents page): two or more of its lines hold a leader
# (LEADER_READ).  Its block pass reads the block's characters as one run, so
# the structure pass was given it as one line, and where that pass was
# refused (one paragraph per block) a whole contents page was sealed as one
# line: measured on a contents page of 36 lines and on one of 18 names, the
# second sealed by an accepted structure pass that kept the line it was given.
LIST_LEADER_LINES = 2


def leader_list_breaks(text: str) -> list[int]:
    """Where engine A broke the lines of TEXT, its reading of one layout
    block, when the block lists entries with leaders (two or more of its
    lines hold one, LIST_LEADER_LINES): each gap of its characters
    (engine_gaps) a line break is in.  Empty for any other block: a
    paragraph's lines are where the print wrapped, not entries."""
    if sum(1 for line in text.split("\n") if LEADER_READ.search(line)) < LIST_LEADER_LINES:
        return []
    chars, gaps = engine_gaps(text)
    return [g for g in range(1, len(chars)) if "\n" in gaps[g]]


def break_lines(text: str, span: str, at: Collection[int]) -> str:
    """TEXT, a punctuated block whose characters are SPAN, with a line break
    in each gap of AT (gap k is before character k): after the marks there
    that end the line (a leader, a page number and its brackets), before
    those that open the next (OPENING_MARKS: the bracket of a list's
    「（一）」).  TEXT as it is where its characters are not SPAN."""
    chars, gaps = engine_gaps(text)
    if chars != CJK(span):
        return text
    for g in at:
        if 0 < g < len(chars) and "\n" not in gaps[g]:
            gap = gaps[g]
            cut = len(gap)
            while cut and gap[cut - 1] in OPENING_MARKS:
                cut -= 1
            gaps[g] = gap[:cut].rstrip() + "\n" + gap[cut:].lstrip()
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out)


def mark_changes(text: str, base: int, block: int | None, passes: Sequence[Mapping[str, Any]],
                 arguments: Any) -> tuple[str, list[dict[str, Any]]]:
    """TEXT with each run of leader dots written 「……」 (leaders_written), and
    decisionChanges entries for each bullet (D5) and each run of dots (D4) it
    keeps: a block's punctuated text (BASE its offset in the merged text,
    BLOCK its layout index, PASSES its punctuation passes, for the counts
    behind it) or a page the formatter punctuated whole (BASE 0, no block:
    pageOffset, not mergedOffset, since the formatter drops furniture).

    A punctuator that writes a leader as three dots, or one, had its answer
    kept as written while the entry said 「……」; now the page is what the
    entry says, and the run as written is in the entry's evidence.
    """
    chars = CJK(text)
    runs = dict(leader_runs(text))
    # A bullet printed twice (⦿⦿) marks one item: one entry for the run.
    bullets = dict(bullet_runs(text))
    out: list[dict[str, Any]] = []
    written: list[str] = []
    at, index = 0, 0
    while index < len(text):
        c = text[index]
        if CJK(c):
            at += 1
        leader = index in runs
        if leader or index in bullets:
            own = next((p for p in passes if p.get("range") and p["range"][0] <= at <= p["range"][1]), None)
            shapes = own.get("censusShapes") if own else None
            out.append({
                "decision": "D4" if leader else "D5", "rule": "dot-leader" if leader else "bullet",
                "block": block, "contextBefore": chars[max(0, at - 4):at], "contextAfter": chars[at:at + 4],
                **({"mergedOffset": base + at} if block is not None else {"mergedOffset": None, "pageOffset": at}),
                "after": LEADER if leader else bullets[index],
                "evidence": {"census": own.get("census") if own else None,
                             "leaderCensus": own.get("leaderCensus") if own else None,
                             **({"written": text[index:runs[index]]} if leader else {}),
                             # What the census saw there, and whether it names
                             # another bullet (bullet_contradicted).
                             **({"censusShapes": shapes,
                                 "shapeContradicted": bullet_contradicted(bullets[index], shapes)}
                                if not leader and shapes else {})},
            })
        if leader:
            written.append(LEADER)
            index = runs[index]
            continue
        if index in bullets:
            written.append(bullets[index])
            index += len(bullets[index])
            continue
        written.append(c)
        index += 1
    return "".join(written), out


def punctuation_pass_ok(span: str, out: str, limit: float | None = None,
                        census: int | None = None) -> tuple[bool, str]:
    """Exact character sequence, marks within the degenerate bound.

    A count read off the image outranks the density bound: when the marks
    written match the census - as places, a doubled circle one
    (mark_places) - a dense block is the page, not a runaway.
    """
    if CJK(out) != CJK(span):
        if Counter(CJK(out)) == Counter(CJK(span)):
            return False, "characters reordered"
        return False, "characters changed"
    if census is not None and abs(mark_places(out) - census) <= census_tolerance(census):
        return True, ""
    density = punctuation_density(out)
    if density > (DEGENERATE_PUNCTUATION_DENSITY if limit is None else limit):
        return False, f"over-punctuated ({density:.1f} marks per 100 characters)"
    return True, ""


def deleted_runs(resolved: str, formatted: str,
                 minimum: int = DELETED_RUN_LIMIT,
                 furniture: frozenset[str] | set[str] = frozenset(),
                 margin: Sequence[str] = (),
                 known: Sequence[str] = (),
                 whole: str | None = None,
                 prose_only: bool = False) -> list[str]:
    """Long stretches of the merged text the formatter dropped.

    A run built mostly from strings that recur across the book is furniture the
    formatter was asked to remove, so it is not reported.

    Nor is a run the formatter moved.  The sequence diff reports text written
    elsewhere on the page as a deletion (and an insertion), and moving text is
    the formatter's job - verify_characters counts characters for that very
    reason.  So a run counts as deleted only when at least MINIMUM of its
    characters are missing from the output altogether: counted against WHOLE
    (the whole merged text, table blocks included, when RESOLVED is only its
    prose - the output holds the tables too, and their characters must not
    stand in for prose that is gone).  Measured: a colophon engine A read in
    another column order, written right by the formatter in four runs out of
    four, was refused each time for "deleting" a 21-character run - the
    answers lacked no character at all - and the page fell back to engine A's
    order (31 errors).  The nine other such refusals sealed in the corpus
    (pages of a year table formatted whole) lacked 17-18 characters each,
    every one of them the running head, which is furniture; the runs they were
    refused for were table rows of figures written in another order.  A run the formatter
    really dropped lacks every one of its characters, and is reported.

    With PROSE_ONLY (RESOLVED is the prose blocks' text, the tables' kept
    apart) the sequence diff is taken against the output with its tables
    left out (table_spans): the prose is compared with the prose.  A table holds
    characters the prose around it holds too, and the diff matched them one
    by one, cutting a dropped run into pieces shorter than MINIMUM.  Measured
    on a textbook page: the structure pass left out the table's 20-character
    header column, a block of prose beside the rebuilt body, and the diff
    against the page with the table matched five of its characters in the
    table's cells - the longest piece left was 4 characters, and the page
    was sealed with the header gone and nothing saying so.  The
    multiset test still counts the whole output, tables included, so text
    the formatter moved into a table lacks nothing and is not reported.
    """
    src, out = CJK(resolved), CJK(formatted)
    # What the output lacks, as a multiset: a moved run lacks nothing.
    lacking = Counter(CJK(whole) if whole is not None else src) - Counter(out)
    if sum(lacking.values()) < minimum:
        return []
    beside = formatted
    for start, end in (reversed(table_spans(formatted)) if prose_only else ()):
        beside = beside[:start] + "\n" + beside[end:]
    matcher = difflib.SequenceMatcher(None, src, CJK(beside), autojunk=False)
    runs = []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "delete" or (i2 - i1) < minimum:
            continue
        run = src[i1:i2]
        if sum((Counter(run) & lacking).values()) < minimum:
            # Its characters stand elsewhere in the output: moved, not dropped.
            continue
        if known:
            # Furniture the book's profile measured (running heads repeated on
            # the page or across the book): a run made of them, with nothing
            # sentence-sized left over, was removed on purpose.
            rest = run
            for text in sorted(known, key=len, reverse=True):
                if text:
                    rest = rest.replace(text, "")
            if len(rest) < minimum:
                continue
        at_edge = i1 <= FURNITURE_EDGE or i2 >= len(src) - FURNITURE_EDGE
        if furniture and _is_furniture(run, furniture, minimum, at_edge):
            continue
        # No book to learn recurrence from, or a head this book prints only
        # once: fall back to the page's own margin blocks.
        if margin and any(
                difflib.SequenceMatcher(None, run, text, autojunk=False).ratio() >= 0.85
                for text in margin):
            continue
        runs.append(run)
    return runs


def furniture_unexplained(given: str, formatted: str, removable: str,
                          furniture: frozenset[str] | set[str] = frozenset(),
                          heads: Sequence[str] = ()) -> str:
    """The CJK characters of GIVEN, the text the structure pass was given,
    that FORMATTED lacks and the page's furniture does not explain, in GIVEN's
    order: "" when what it deleted, if anything, is furniture.

    Without a content review the formatter deletes the page's furniture
    itself (verify_characters' REMOVABLE).  deleted_runs reports a deletion
    only from DELETED_RUN_LIMIT characters on, so body text in shorter runs
    went with no refusal and no doubt: two clauses of 7 and 9 characters, or
    the repeated 一 of a 凡例 renumbered 1. 2. 3.  Here every character the
    output lacks (a multiset: text moved is not lost) must be furniture by
    evidence: REMOVABLE's (removable_text: the blocks labelled furniture and
    those in a folio's margin band, the book's known heads; on the whole-page
    path the page's margin blocks, margin_furniture), or a deleted stretch the
    book's recurring n-grams (FURNITURE, furniture_grams) and its running
    heads (HEADS) explain - furniture_leftover at most INSERTION_LIMIT, one
    character at least explained, or a run deleted_runs takes for furniture
    (_is_furniture)."""
    src, out = CJK(given), CJK(formatted)
    lacking = Counter(src) - Counter(out) - Counter(CJK(removable))
    if not lacking:
        return ""

    def explained(i1: int, i2: int) -> bool:
        if not (furniture or heads):
            return False
        left = furniture_leftover(src, furniture, heads, (i1, i2))
        at_edge = i1 <= FURNITURE_EDGE or i2 >= len(src) - FURNITURE_EDGE
        return (left <= INSERTION_LIMIT and left < i2 - i1) or bool(
            furniture and _is_furniture(src[i1:i2], furniture, DELETED_RUN_LIMIT, at_edge))

    # The stretches the output lacks, with those INSERTION_LIMIT characters
    # apart or less as one: the diff cuts a deleted head in two where a
    # character of it matches one the output holds elsewhere (a head, its
    # volume and its folio, 某書第五集六九, with the 五 matched), and each
    # piece alone is too short for its n-grams.
    groups: list[list[tuple[int, int]]] = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, src, out, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            if groups and i1 - groups[-1][-1][1] <= INSERTION_LIMIT:
                groups[-1].append((i1, i2))
            else:
                groups.append([(i1, i2)])
    lost: list[int] = []
    for group in groups:
        whole = len(group) > 1 and explained(group[0][0], group[-1][1])
        for i1, i2 in group:
            if whole or explained(i1, i2):
                lacking -= Counter(src[i1:i2])
            else:
                lost.extend(range(i1, i2))
    named = []
    for at in lost:
        if lacking[src[at]] > 0:
            lacking[src[at]] -= 1
            named.append(src[at])
    return "".join(named)


def punctuation_doubts(passes: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Stretches whose kept marks disagree with what the page prints.

    Every pass records the census (the marks the model counted on the crop)
    and the marks the page kept there, and the page used to be sealed clean
    whatever they said.  Measured over the 546 sealed passes on 74 pages:

    - A refused pass keeps its characters and drops every mark: 22 passes on
      15 pages, every one after a census had counted marks there, 14 of the
      pages sealed clean.  A census of 0 never reaches a refusal - the pass
      returns early - so a refusal is a stretch left bare, unless the census
      is unknown and nothing suggests marks: no other pass on the page wrote
      or counted any, and the refused output itself had none.  A refused pass
      that kept the marks a refused answer and engine A agree on
      (agreed_punctuation, `partial`) is reported partial, whatever the census:
      the marks only one of them saw are missing.
    - A retry is accepted on the character check alone; the census that
      refused the first answer is not asked again, so the retry may keep any
      number of marks, none included.  12 of the 53 passes accepted after a
      retry kept a count outside the census tolerance, 4 under and 8 over.
      Neither reading is the print by default.  Three of the twelve lie
      where a golden covers the stretch: in the under-count the kept text was
      wrong (census 117, golden 118, kept 63), in both over-counts the census
      was (it counted a doubled circle as one then: kept 26 and 14, golden 25
      and 14).  A third over-count matches its census exactly with each
      doubled circle as one, and its crop prints doubled circles.  So the
      doubt says the two readings disagree, with the doubled circles kept,
      and a person looks at the crop.
    - The shape re-check re-reads a whole block and is accepted on the
      character check alone too.  Its marks are counted per pass (range), so
      a refused tile it left bare stays bare here.  Seals older than ranges
      carry the block's total on every pass; those are judged once per block,
      against the block's summed census.

    The tolerance is the one the first answer is refused on (census_tolerance),
    so the seal asks of the kept text exactly what the pass asked of itself.
    It is applied in the census's own count (censusConvention): places, a
    doubled circle one, for the census that reports doubled circles apart;
    glyphs for the older one; either, for a seal that does not say.  Where the
    census counted doubled circles, kept doubled circles outside the tolerance
    of that count are a doubt of their own (doubled-count-mismatch).
    """
    def offered(entry: Mapping[str, Any]) -> bool:
        return any(punctuation_counts(text) for text in entry.get("refusedOutputs") or [])

    units = []
    legacy: dict[Any, list[Mapping[str, Any]]] = {}
    for entry in passes:
        if entry.get("shapeRecheck") and "range" not in entry:
            if entry.get("block") not in legacy:
                units.append(("block", entry.get("block")))
            legacy.setdefault(entry.get("block"), []).append(entry)
        else:
            units.append(("pass", entry))
    doubts = []
    for shape, unit in units:
        group = legacy[unit] if shape == "block" else [unit]
        first = group[0]
        censuses = [entry.get("census") for entry in group]
        census = None if any(c is None for c in censuses) else sum(censuses)
        marks = first.get("marks") or 0
        refused = [entry for entry in group if not entry.get("ok")]
        rechecked = bool(first.get("shapeRecheck"))
        reason = "; ".join(sorted({entry.get("reason") or "" for entry in group} - {""}))
        where = f"block {first.get('block')}" + (
            f" tile {first['tile']}" if shape == "pass" and first.get("tile") is not None else "")
        others = [entry for entry in passes if not any(entry is mine for mine in group)]
        elsewhere = any((other.get("marks") or 0) > 0 or (other.get("census") or 0) > 0
                        for other in others)
        evidence = ("no census, but marks elsewhere on the page" if elsewhere
                    else "no census, but the refused output wrote marks"
                    if any(offered(entry) for entry in refused) else "")
        if refused and not rechecked:
            partial = sum((entry.get("partial") or {}).get("kept", 0) for entry in group)
            if partial:
                # Both answers refused; the stretch keeps only the marks a
                # refused answer and engine A agree on (agreed_punctuation).
                # Always a doubt: whatever else is printed there is missing.
                counted = f"census counted {census} marks" if census is not None else "no census"
                # Kept by agreed_punctuation (a refused answer and engine A) or
                # by agreed_readings (the two answers, each realigned).
                between = ("the two answers" if any((entry.get("partial") or {}).get("agreedWith")
                                                    for entry in group)
                           else "a refused answer and engine A")
                doubts.append({"kind": "punctuation-partial", "detail": (
                    f"PUNCTUATION PARTIAL: {where}: {counted}; both answers refused "
                    f"({reason or 'no reason recorded'}), kept only the {partial} mark(s) {between} "
                    "printed at the same place")})
                continue
            if census is None:
                if not evidence:
                    continue
                counted = evidence
            else:
                counted = f"census counted {census} marks"
            doubts.append({"kind": "punctuation-left-bare",
                           "detail": f"PUNCTUATION LEFT BARE: {where}: {counted}, none kept "
                                     f"(pass refused: {reason or 'no reason recorded'})"})
            continue
        kept_by = ("the shape re-check" if rechecked
                   else "a retry" if "(after retry)" in reason else "the pass")
        if census is None:
            # No count to hold the kept marks to; only a refused stretch the
            # re-check also left bare is known to be missing its marks.
            if refused and marks == 0 and evidence:
                doubts.append({"kind": "punctuation-left-bare", "detail": (
                    f"PUNCTUATION LEFT BARE: {where}: {evidence}; the pass was refused "
                    f"({reason}) and the shape re-check kept none here")})
            continue
        if census == 0:
            continue
        why = (f" (first answer refused: {reason.replace(' (after retry)', '')})"
               if kept_by == "a retry" else "")
        doubled = (first.get("doubled") or 0) if shape == "pass" else 0
        convention = first.get("censusConvention") if shape == "pass" else "glyphs"
        # The census counts places (a doubled circle is one, and so is a
        # bullet printed twice) since it reports doubled circles apart, glyphs
        # before.  The places kept are sealed as
        # the pass counted them (mark_places); a seal from before they were
        # sealed counts the doubled circles off.  A seal older than either
        # convention does not say which count its model made, and in 21-27 of
        # 41 doubled-circle passes per run it was places although asked for
        # glyphs: either within the tolerance is a match.
        places = first.get("places", marks - doubled) if shape == "pass" else marks - doubled
        off = {"places": [places], "glyphs": [marks]}.get(convention, [marks, places])
        if marks == 0:
            doubts.append({"kind": "punctuation-left-bare", "detail": (
                f"PUNCTUATION LEFT BARE: {where}: census counted {census} marks, "
                f"{kept_by} kept none{why}")})
        elif all(abs(count - census) > census_tolerance(census) for count in off):
            shown = (f"{marks} ({doubled} of them in doubled circles 。。)" if doubled else str(marks)) + (
                f", {places} places" if convention == "places" and places != marks else "")
            doubts.append({"kind": "punctuation-count-mismatch", "detail": (
                f"PUNCTUATION COUNT MISMATCH: {where}: census counted {census} "
                f"{'places' if convention == 'places' else 'marks'}, {kept_by} kept {shown}{why}")})
        counted_doubled = first.get("doubledCensus") if shape == "pass" else None
        if marks and counted_doubled is not None and (
                abs(doubled - counted_doubled) > census_tolerance(counted_doubled)):
            # The shape: 。 where 。。 is printed, or the reverse.  Kept as a
            # doubt whether or not the count was put to the punctuator
            # (doubled_shown): the two readings disagree and the crop decides.
            doubts.append({"kind": "doubled-count-mismatch", "detail": (
                f"DOUBLED CIRCLES MISMATCH: {where}: census counted {counted_doubled} doubled "
                f"circle(s) 。。 among {census} places, {kept_by} kept {doubled}{why}")})
    return doubts


def extra_copies(text: str, draft: str, draft2: str) -> list[dict[str, Any]]:
    """Text the page holds at more sites than either engine read it.

    The merge keeps what either engine read, so text the two engines place in
    different spots - a heading, a leaf label, a column read before its
    neighbour by one and after it by the other - aligns as a deletion plus an
    insertion, and both copies are kept.  The worst case measured is a
    186-character passage written twice while each engine read it once.

    Counting against the drafts is what separates this from a phrase the book
    really prints twice: both engines read that one twice.  The unit counted is
    a character pair, the shortest string that has an order; one character
    over-counted is an ordinary union of two readings.  A count alone still
    fires where readings are joined up - a character, clause or running head
    dropped between two words makes a pair the page holds twice - so each
    engine must also have left a copy of the run unread where it sits: more
    than half of the copy's characters unmatched when the page is aligned with
    that engine's reading.

    What text cannot tell is WHY each engine left a site unread.  One reading
    placed twice and two engines each missing a different printed copy (a
    running head on each leaf, a word printed ten times) leave identical
    drafts, so the result is a question for the image, never a reason to
    delete: each run is returned with the sites each engine left unread.  A
    site both engines left unread is the one unambiguous shape - no engine
    put the text there (one run on the corpus, a word written twice side by
    side).

    Measured over the 511 sealed merge pages of five books: 98 runs are held
    more often than either engine read them inside a line or cell; 82 runs on
    21 pages have a copy each engine left unread.  The page set is the same
    for any unread share from a half up to the whole copy; counting a copy
    with fewer than half its characters unmatched adds two pages that hold no
    duplication.  Of the 21, one is two engines missing different copies of
    a common word; the rest hold a passage, running heads, leaf labels,
    headings, roster entries, verse lines or table labels written twice.  An
    earlier test asked the unread stretch around each copy to be mostly text
    the engine read elsewhere (moved, not missed); at its cut of a half it
    dropped five real duplications (0.29 to 0.33) for the one union of misses
    (0.20), and no cut is evidence where the drafts are identical by
    construction, so it was removed.  Returns the runs longest first.
    """
    lines = [piece for piece in (CJK(part) for part in TEXT_SEAMS.split(text)) if piece]
    page, first, second = "".join(lines), CJK(draft), CJK(draft2)

    def pairs(value: str) -> Counter:
        return Counter(value[i:i + 2] for i in range(len(value) - 1))

    held = Counter()
    for line in lines:
        held.update(pairs(line))
    read = (pairs(first), pairs(second))
    # A pair of numerals says nothing about order: a handful of digits make so
    # few pairs that figures set in another order over-count them by chance.
    # In an offline mock a figure table written as one paragraph was confirmed
    # on numeral pairs alone; no duplication measured on the corpus consists of
    # figures only.
    surplus = {pair for pair, count in held.items()
               if count >= 2 and count > max(read[0][pair], read[1][pair]) >= 1
               and not set(pair) <= NUMERAL_CHARS}
    if not surplus:
        return []
    runs = set()
    for line in lines:
        start = end = -1
        for i in range(len(line) - 1):
            if line[i:i + 2] not in surplus:
                continue
            if i < end:
                end = i + 2
            else:
                if end > start >= 0:
                    runs.add(line[start:end])
                start, end = i, i + 2
        if end > start >= 0:
            runs.add(line[start:end])
    runs = {run for run in runs if page.count(run) >= 2
            and page.count(run) > max(first.count(run), second.count(run))}
    if not runs:
        return []

    def unread_mask(reading: str) -> bytearray:
        mask = bytearray([1]) * len(page)
        matcher = difflib.SequenceMatcher(None, page, reading, autojunk=False)
        for tag, i1, i2, _, _ in matcher.get_opcodes():
            if tag == "equal":
                mask[i1:i2] = bytes(i2 - i1)
        return mask

    unread = (unread_mask(first), unread_mask(second))
    found: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        sites = []
        for match in re.finditer(re.escape(run), page):
            o, e = match.start(), match.end()
            by = "".join(name for name, mask in zip("AB", unread) if sum(mask[o:e]) * 2 > len(run))
            if by:
                shown = run if len(run) <= 8 else run[:6] + "…"
                sites.append({"at": o, "unreadBy": by,
                              "context": f"{page[max(0, o - 4):o]}[{shown}]{page[e:e + 4]}"})
        if any("A" in site["unreadBy"] for site in sites) and any("B" in site["unreadBy"] for site in sites):
            found[run] = sites
    kept: list[str] = []
    for run in sorted(found, key=lambda run: (-len(run), page.find(run))):
        if not any(run in longer for longer in kept):
            kept.append(run)
    return [{"text": run, "page": page.count(run), "engineA": first.count(run),
             "engineB": second.count(run), "sites": found[run]} for run in kept]


def extra_copy_doubts(text: str, draft: str, draft2: str) -> list[dict[str, str]]:
    """extra_copies as a doubt: what was counted, and where to look."""
    runs = extra_copies(text, draft, draft2)
    if not runs:
        return []

    def sites(run: Mapping[str, Any]) -> str:
        shown = []
        for key, label in (("AB", "both engines"), ("A", "engine A"), ("B", "engine B")):
            at = [site["context"] for site in run["sites"] if site["unreadBy"] == key][:2]
            if at:
                shown.append(f"{label} left unread …{'…, …'.join(at)}…")
        return "; ".join(shown)

    both_unread = any(site["unreadBy"] == "AB" for run in runs for site in run["sites"])
    return [{"kind": "extra-copy", "detail": (
        "TEXT AT MORE SITES THAN EITHER ENGINE READ IT: "
        + " | ".join(f"{run['text'][:20]}{'…' if len(run['text']) > 20 else ''} "
                     f"({len(run['text'])} characters, {run['page']} times here, engine A "
                     f"{run['engineA']}, engine B {run['engineB']}; {sites(run)})"
                     for run in runs[:3])
        + (" - at a site both engines left unread, the text was put there from a reading elsewhere"
           if both_unread else "")
        + " - where each engine left a different site unread, one reading placed twice and "
          "two different misses look the same in text: check the image at those sites")}]


# Characters matched in all before a one-engine run counts as engine B's reading
# of text engine A read elsewhere (place_moved_once).  A pair is the unit of the
# evidence, as in extra_copies, but a pair alone also coincides - the one page
# of 21 where extra_copies found two engines each missing a different printed
# copy was a two-character word.  Three is the shortest real move measured: a
# colophon's three-character labels, read label by label by one engine and all
# labels first by the other.
MOVE_MINIMUM = 3


def place_moved_once(segments: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Text engine B read in another order, placed once, at engine A's site
    (decision D2).

    The merge keeps what either engine read, so text the two engines read in
    different places - a colophon engine A read labels first and engine B
    label by value, a heading, a running head - aligns as engine A's
    one-engine text in one place and engine B's in another, and both copies
    were kept (extra_copies found them afterwards, as a doubt).  Here each of
    engine B's one-engine insertions is matched against engine A's text that
    engine B did not read where it stands (delete and replace segments),
    pairs first: a stretch counts only through pairs the old merge would hold
    more often than either engine read them (extra_copies' test), at least
    MOVE_MINIMUM characters in all.  Then:

    - what matches becomes text both engines read, at engine A's site - the
      page follows engine A's layout blocks, so its place is the one kept;
    - what differs between the two copies there becomes an ordinary
      disagreement at that site (a price one engine read with 一 and the other
      with 二 is a numeral question for the crop), and engine B's characters
      between two matched stretches
      that face nothing of engine A's are engine B's alone, there;
    - what engine A never read (engine B's characters before the first or
      after the last match) stays at engine B's site, as before.

    A replacement whose engine-A side is taken this way gives up its engine-B
    side, which is tried the same way and otherwise kept as engine B's
    one-engine text where it was; one whose engine-A side would be left in two
    pieces is not touched, since its engine-B side could then belong to
    either.  Pairs of numerals prove nothing about order (extra_copies), so a
    run of figures alone never moves.  Nothing is dropped: every character
    either engine read is on the page once, agreed or in a disagreement.

    Returns the segments, each with its place in engine B's text (bStart),
    and one record per move; the input unchanged, with no bStart, when nothing
    moved.
    """
    a_text = "".join(seg["a"] for seg in segments)
    b_text = "".join(seg["b"] for seg in segments)
    starts, a_at, b_at = [], 0, 0
    for seg in segments:
        starts.append((a_at, b_at))
        a_at += len(seg["a"])
        b_at += len(seg["b"])
    # Engine A's characters engine B did not read where they stand: the
    # engine-A side of a delete or replace (a fused numeral run keeps its
    # own treatment).
    owner: list[int | None] = [None] * len(a_text)
    for k, seg in enumerate(segments):
        if seg["tag"] in ("delete", "replace") and not seg.get("fused") and not seg.get(LOOPED_READING):
            for p in range(starts[k][0], starts[k][0] + len(seg["a"])):
                owner[p] = k

    def pairs(text: str) -> Counter:
        return Counter(text[i:i + 2] for i in range(len(text) - 1))

    read_a, read_b = pairs(a_text), pairs(b_text)
    page = read_a + sum((pairs(seg["b"]) for seg in segments if seg["tag"] == "insert"), Counter())

    def surplus(pair: str) -> bool:
        return page[pair] >= 2 and page[pair] > max(read_a[pair], read_b[pair]) and not set(pair) <= NUMERAL_CHARS

    claims: dict[int, tuple[int, str, int, int]] = {}      # A position -> (run, kind, r_lo, r_hi)
    runs = [{"site": k, "text": seg["b"], "bStart": starts[k][1], "released": False}
            for k, seg in enumerate(segments)
            if seg["tag"] == "insert" and len(seg["b"]) >= 2 and not seg.get(LOOPED_READING)]

    def leftovers(k: int, taken: Mapping[int, Any]) -> int:
        a0 = starts[k][0]
        free = [p not in taken for p in range(a0, a0 + len(segments[k]["a"]))]
        return sum(1 for i, f in enumerate(free) if f and (i == 0 or not free[i - 1]))

    def attempt(number: int) -> bool:
        text = runs[number]["text"]
        masked = "".join(c if owner[p] is not None and p not in claims else "\0" for p, c in enumerate(a_text))
        blocks = [m for m in difflib.SequenceMatcher(None, text, masked, autojunk=False).get_matching_blocks()
                  if m.size]
        anchors = [m for m in blocks if m.size >= 2 and all(surplus(text[m.a + i:m.a + i + 2])
                                                             for i in range(m.size - 1))]
        if not anchors:
            return False
        # One site only: the longest matching stretch stands once among engine
        # A's text engine B did not read, or the run could belong to either
        # copy (a word a chart prints in two cells, both engines garbled).
        longest = max(anchors, key=lambda m: m.size)
        if masked.count(text[longest.a:longest.a + longest.size]) != 1:
            return False
        inside = [m for m in blocks if anchors[0].a <= m.a and m.a + m.size <= anchors[-1].a + anchors[-1].size]
        # A matching stretch one character away on either side, with at most
        # one of engine A's facing it, is the same copy read with one
        # character different (a label, then a figure's first digit read 一
        # by one engine and 二 by the other): it joins, and the difference
        # becomes the site's disagreement.
        outside = [m for m in blocks if m not in inside]
        for m in reversed([m for m in outside if m.a < inside[0].a]):
            gap_r = inside[0].a - (m.a + m.size)
            gap_a = sum(1 for p in range(m.b + m.size, inside[0].b) if masked[p] != "\0")
            if gap_r > 1 or gap_a > 1:
                break
            inside.insert(0, m)
        for m in [m for m in outside if m.a > inside[-1].a]:
            gap_r = m.a - (inside[-1].a + inside[-1].size)
            gap_a = sum(1 for p in range(inside[-1].b + inside[-1].size, m.b) if masked[p] != "\0")
            if gap_r > 1 or gap_a > 1:
                break
            inside.append(m)
        # The run is the text read elsewhere only if most of it matches, as a
        # copy counts as unread in extra_copies when most of it is: measured,
        # a heading engine A missed shared three of its ten characters with
        # the running head engine A read, and moving those three would have
        # cut them out of the heading.  On six books' merged pages 10 runs
        # matched half their characters or fewer: two headings of this kind,
        # garbled running heads, a scrambled list, a row of figures.
        matched = sum(m.size for m in inside)
        if matched < MOVE_MINIMUM or 2 * matched <= len(text):
            return False
        new: dict[int, tuple[int, str, int, int]] = {}
        inserts: list[tuple[int, int, int]] = []
        for m in inside:
            for i in range(m.size):
                new[m.b + i] = (number, "match", m.a + i, m.a + i + 1)
        for m1, m2 in zip(inside, inside[1:]):
            r_lo, r_hi = m1.a + m1.size, m2.a
            free = [p for p in range(m1.b + m1.size, m2.b) if masked[p] != "\0"]
            if free and r_hi > r_lo:
                if free[-1] - free[0] + 1 != len(free) or len({owner[p] for p in free}) != 1:
                    return False        # engine A's facing text is in two places
                for p in free:
                    new[p] = (number, "face", r_lo, r_hi)
            elif r_hi > r_lo:
                inserts.append((m1.b + m1.size, r_lo, r_hi))
        trial = {**claims, **new}
        if any(segments[k]["b"] and leftovers(k, trial) > 1 for k in {owner[p] for p in new}):
            return False
        claims.update(new)
        runs[number]["placement"] = {"inserts": inserts, "head": (0, inside[0].a),
                                     "tail": (inside[-1].a + inside[-1].size, len(text)),
                                     "matched": sum(m.size for m in inside),
                                     "at": (min(new), max(new) + 1)}
        return True

    released: set[int] = set()
    progress = True
    while progress:
        progress = False
        for number in range(len(runs)):
            if "placement" not in runs[number] and attempt(number):
                progress = True
        for k, seg in enumerate(segments):
            if (seg["tag"] == "replace" and seg["a"] and k not in released and owner[starts[k][0]] == k
                    and leftovers(k, claims) == 0):
                # Its engine-B side is now text only engine B read, as an
                # insertion is: counted so, and tried the same way.
                released.add(k)
                page.update(pairs(seg["b"]))
                runs.append({"site": k, "text": seg["b"], "bStart": starts[k][1], "released": True})
                progress = True
    if not any("placement" in run for run in runs):
        return list(segments), []

    def piece(tag: str, a: str, b: str, p: int, q: int, b_start: int, **extra: Any) -> dict[str, Any]:
        return {"tag": tag, "a": a, "b": b, "before": a_text[max(0, p - 8):p], "after": a_text[q:q + 8],
                "bStart": b_start, **extra}

    def left_at_site(run: Mapping[str, Any], p: int) -> list[dict[str, Any]]:
        """What of a run stays where engine B read it: all of it if unplaced."""
        text, spans = run["text"], ([run["placement"]["head"], run["placement"]["tail"]]
                                    if "placement" in run else [(0, len(run["text"]))])
        mark = {"movedRemainderOf": run["bStart"]} if "placement" in run else {}
        return [piece("insert", "", text[lo:hi], p, p, run["bStart"] + lo, **mark)
                for lo, hi in spans if hi > lo]

    inserts_at: dict[int, list[tuple[int, int, int]]] = {}
    for number, run in enumerate(runs):
        for gap, lo, hi in (run.get("placement") or {}).get("inserts", []):
            inserts_at.setdefault(gap, []).append((number, lo, hi))
    moved_sites = {run["site"]: run for run in runs if not run["released"] and "placement" in run}
    released_sites = {run["site"]: run for run in runs if run["released"]}
    out: list[dict[str, Any]] = []
    for k, seg in enumerate(segments):
        a0, b0 = starts[k]
        end = a0 + len(seg["a"])
        if k in moved_sites:
            out.extend(left_at_site(moved_sites[k], a0))
            continue
        if not any(p in claims for p in range(a0, end)):
            out.append(dict(seg, bStart=b0))
            continue
        own_b = "" if k in released_sites else seg["b"]
        if k in released_sites:
            out.extend(left_at_site(released_sites[k], a0))
        p = a0
        while p < end:
            if p not in claims:
                q = p
                while q < end and q not in claims:
                    q += 1
                out.append(piece("replace" if own_b else "delete", a_text[p:q], own_b, p, q, b0))
                own_b = ""
            else:
                number, kind, r_lo, r_hi = claims[p]
                run = runs[number]
                q = p + 1
                if kind == "match":
                    while q < end and claims.get(q, (None, None, None))[:2] == (number, "match") and \
                            claims[q][2] == r_lo + (q - p):
                        q += 1
                    out.append(piece("equal", a_text[p:q], a_text[p:q], p, q, run["bStart"] + r_lo,
                                     movedFrom=run["bStart"]))
                else:
                    while q < end and claims.get(q) == claims[p]:
                        q += 1
                    out.append(piece("replace", a_text[p:q], run["text"][r_lo:r_hi], p, q, run["bStart"] + r_lo,
                                     movedFrom=run["bStart"]))
            for number, lo, hi in inserts_at.get(q, []):
                run = runs[number]
                out.append(piece("insert", "", run["text"][lo:hi], q, q, run["bStart"] + lo,
                                 movedFrom=run["bStart"]))
            p = q
    moves = []
    for run in runs:
        if "placement" not in run:
            continue
        where = run["placement"]["at"]
        site = starts[run["site"]][0]
        moves.append({"text": run["text"], "engineBOffset": run["bStart"],
                      "readAt": {"engineAGap": site, "context": a_text[max(0, site - 8):site] + "|" + a_text[site:site + 8]},
                      "placedAt": {"engineA": list(where),
                                   "context": a_text[max(0, where[0] - 4):where[0]] + "["
                                   + a_text[where[0]:where[1]] + "]" + a_text[where[1]:where[1] + 4]},
                      "matched": run["placement"]["matched"],
                      "leftAtSite": "".join(run["text"][lo:hi] for lo, hi in (run["placement"]["head"],
                                                                              run["placement"]["tail"])),
                      "released": run["released"]})
    return out, moves


def whole_page_reason(blocks: Sequence[Mapping[str, Any]], draft: str, mode: str,
                      order_text: str | None = None) -> str:
    """Why a merged page could not be punctuated block by block (block_spans).

    ORDER_TEXT is the engine whose reading an order-conflict page takes whole:
    engine B's has no layout blocks at all, engine A's goes block by block
    unless its blocks fail it as a merged page's would.
    """
    if mode != "merge" and order_text != "A":
        return (f"the engines read the page in different orders and engine B's text was "
                f"taken whole, with no layout ({mode})")
    if not blocks:
        return "engine A gave no layout blocks"
    if sum(len(CJK(str(b["text"]))) for b in blocks) != len(CJK(draft)):
        return "engine A's layout blocks do not account for its text"
    return "a disagreement read across several layout blocks shares no character with engine A's reading"
