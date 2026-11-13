"""The book repair tool's structure rules (scripts/repair_book.py): heading
content per page, the contents and the units they list, the title page, and
one heading hierarchy for the whole book.

Each rule acts only on what the book model (_repair_model.py) measured on
this book and on what the engines read; a rule whose gate failed stays
silent, and a site it finds but cannot settle where printed text is at
stake becomes a doubt, never a guess.  Nothing here adds a character no engine read: the
rules move text already on the page, write a figure engine A read (and,
on the unit titles the contents stage makes headings, the dash of the
stand-in rule K1, _repair_rules), delete a stand-in engine A did not read,
and change Markdown structure (`#`, blank lines, the space after a number).

The contents map (contents_map) is the book's own second printing of every
unit title: the contents pages (M-6) parsed into their title, their groups
(a line that opens with a run suffix of the body, M-3) and their entries,
each entry's number as the page and the engines read it, and each entry
mapped, in contents order, onto the line of the body that prints it.  An
entry's number is its place in its group only where the book numbers its
entries that way (the `contents-numbering` gate: most numbers read are their
places, and enough of them); elsewhere it is what two sources read alike.

Contents (stage `contents`), in passes on the text the pass before leaves:
- S15 a contents line holding printed lines the OCR ran together (the rows
  the book model reads apart: M-6) -> cut where engine A read a line break
  between two of them: at the model's cut, or at engine A's line break up
  to LEADING_DROP characters after it (a note after a date, such as 批, is
  the date line's; the model's cut gives it to the title after it).  A
  `<br>` at the cut goes; the figures and marks before it stay on the line
  before, an opening mark goes with the next.  Where engine A read no line
  break there, or not beside both characters (a `<br>` where a title
  wraps; a character engine A misread), the line stays; so does a line
  that no longer spells its rows (a stand-in written over since the model
  read it).  Only line breaks change; the contents map then finds the rows
  the model read on one line on the lines after it
  (_book_lint.locate_rows).
- S14 a unit title printed over two lines -> one line.  In the body: a
  heading and the line straight after it on its page (only blank lines
  between; a heading, or a plain line the title ends with), which together
  match an entry that neither matches alone, the second line going on with
  the title -> one heading (the map reads such a pair as one line:
  body_lines).  Not where the entry's contents line holds a figure between
  two characters (its page number with no leaders before it, one '.' at
  most, and the date line run on after it: the entry then holds the date;
  after leaders the book model reads the date as a line of its own, M-6).
  In the contents: an entry the book model reads printed over two lines
  (M-6: a line with no leaders and no page number, the line straight after
  it ending with its page number, the body printing the two from a line's
  start with no line break between them; the map reads it as one entry, so
  its unit is found where the body prints the title over two lines too) ->
  one line; and an entry's line with no page number at its end and the
  line straight after it, which ends with one (a line with none after an
  entry is its date), where the two together match the first one's unit
  further than it alone -> one line, read as one entry (two rows the model
  read on two lines, found on one line, are one: contents_map).  The
  second line's heading marker or bullet and the white space between go;
  no character is added or dropped (a space stays only between two
  letters of an alphabet that spaces its words: Latin, Greek, Cyrillic).
- S9 an entry whose title stands in the body as a plain line, or at the
  start of an engine A block inside a line, where engine A read the entry's
  number before it -> split off (before it, at the start of the block; after
  it, at engine A's next block, or after a date that ends it) and made a
  heading; a title whose end the engines do not show, or whose number
  engine A did not read as the entry's, stays as it is.
- S4 a unit heading whose number engine A read as the entry's number ->
  `n[mark] title` with engine A's mark; the prefix it replaces holds only
  what engine A did not read there (stand-ins, the number in the other
  script, marks).  Where the page already holds that figure above the
  heading's line (after the character before the title), nothing is
  written.  No number, another number, or a prefix engine A read ->
  a `HEAD-NUMBER-LOST` doubt.  A number written with no mark (engine A read
  none, the page holds none) where the units on either side in its group
  carry one -> a `punctuation-partial` doubt.
- S7 a date after a unit title -> its own paragraph with what follows it on
  the line (a place clause).  The date's start is certain only where its run
  of numerals starts it, or where one start in the run parses (之四四月…);
  the title ends at its last character the contents' title matches
  (title_end), and a figure just before the date stays with it.  Marks
  between the title and the date that neither engine read between them go
  with the split (left, they would end the heading).
- S11 the contents rebuilt: bullets dropped; each entry `N[mark] title` with
  the number the page, engine A or engine B read where it is the entry's
  number and the mark after it (engine A's, else the page's), a number
  printed after the title moved before it, marks before the title that
  engine A did not read (or read as its figure's mark) dropped; the group
  lines as headings, the run suffix apart from the rest; the contents title
  as a heading; entries that make a Markdown list tight, the others a blank
  line apart.  An engine's figure is the entry's only where it stands alone
  on its line or on the entry's own line: one that ends a line after other
  print (leaders, the title before) is that line's page number, and what an
  engine read on the line before is not read "before the title"
  (entry_gap); and the one figure an engine read between the two titles
  that is the very figure ending the line above on the page (its page
  number) is that line's, read on a line of its own or with no line break
  (read_numbers).  An engine not placed at the title is no evidence: the
  marks the page holds there stay.  Where a bracket engine A read opens the
  line, or figures engine A read follow the first, the head is the entry's
  own enumerator and no number is written; a head with no number written
  keeps its figures and letters (print, whatever engine A read) - but a
  figure at a line's head that both engines read as the page number ending
  the line before goes to that line's end, where the entry above ends with
  its leaders and no figure.  Emphasis markers that wrap a whole entry go
  together where engine A wrote none; emphasis around the number alone goes
  with the number's old form; any other stay.  An entry line written as a
  heading that engine A reads as a title block (the contents' own heading,
  which the map took for an entry) stays a heading, at the units' level.
  A line still holding more than one row (printed lines the OCR ran
  together, which the book model reads apart, M-6, and S15 did not cut)
  is rebuilt once, as its first row's.  An entry whose number no source
  read is a `TOC-NUMBER-LOST` doubt, and one read without the mark the
  entries on either side carry a `punctuation-partial` doubt.  An entry
  with no unit in the body is a `unit-not-in-scan` doubt.

Title page (stage `title-page`): S13 a page where a line one engine read
(four or more characters) is the exact reverse of a run the other engine
read, and the other never read it that way round - a page printed right to
left, which one engine read backwards - is a `research-front-matter` doubt
over the page, naming the lines; the page is left as it is.

Levels (stage `levels`): every heading gets a kind - book title (the first
heading before the contents, a title block of engine A), contents title,
contents group, division (a divider's suffix, M-5), unit (a contents
entry), heading inside a unit (a block of its own in engine A, a title
block, or display type), date line - and one level per contents depth: the
book title `#`, the contents title and the divisions one below it, the
groups and units one below those (at the divisions' level in a book without
divisions), a heading inside a unit one below its unit.  Date lines become
plain text; a heading engine A read as the start of a longer block (a run-in
lead, or a heading) keeps its level; any other heading keeps its level and
is a `heading-kind-unknown` doubt.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
import difflib
import re
from typing import Any, Callable, Mapping, NamedTuple, Sequence

import proofread_pages as pp
import _book_lint as lint
import _ocr_markdown as om
import _repair_align as ra
import _repair_edits as re_
import _repair_model as rm
import _repair_rules as rr


HEADING = re.compile(r"^(?P<indent> {0,3})(?P<marks>#{1,6})[ \t]+")
LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
# A contents line's markup and leading number: a bullet, a heading marker,
# the emphasis run that may open the number (`**1**`), a stray bracket
# before the figure, the figure and the mark after it.  A figure that runs
# on into another - a year, or a second figure joined to it by one mark
# (1.1, 2-3) - is part of a longer enumerator, no number.
ENTRY_LEAD = re.compile(r"^(?P<markup>[ \t]*(?:[-*+][ \t]+)?(?:#{1,6}[ \t]+)?)(?:\*{1,3}|_{1,3})?(?P<pre>[\[［(（]?[ \t]*)"
                        r"(?:(?P<digits>[0-9０-９]{1,3})(?![0-9０-９]|[^\s0-9０-９㐀-鿿][0-9０-９])"
                        r"[ \t]*(?P<mark>[.．,，。、])?)?")
ENTRY_TRAIL = re.compile(r"[ \t]+(?P<digits>[0-9０-９]{1,3})[ \t]*$")
# A figure at the end of an engine's gap, the mark after it and white space;
# not the last figure of a longer enumerator (the 1 that ends 1.1).
GAP_FIGURE = re.compile(r"(?<![0-9０-９])(?<![0-9０-９][^\s0-9０-９])(?P<digits>[0-9０-９]{1,3})[ \t]*"
                        r"(?P<mark>[.．,，、。])?\s*$")
# What an engine writes on a line before a figure that is no print of the
# line: white space, heading and list markers, one run of emphasis markers
# (one to three, as Markdown has them: a longer run of underscores is an
# engine's leaders), and the stray bracket ENTRY_LEAD allows before a figure.
GAP_MARKUP = re.compile(r"(?:[ \t]|#{1,6}[ \t]|[-*+>][ \t])*(?:\*{1,3}|_{1,3})?[ \t]*[\[［(（]?[ \t]*")
# The figure that ends a contents line on the page (its page number after
# its leaders or its title), with the emphasis that may close the line.
LINE_END_FIGURE = re.compile(r"(?<![0-9０-９])(?P<digits>[0-9０-９]{1,3})[ \t]*(?:\*{1,3}|_{1,3})?[ \t]*$")
FIGURES = re.compile(r"[0-9０-９]+")
# S14: what opens a line joined onto the line before it (a heading marker,
# a bullet); the page number that ends a contents line (M-6's: bracketed or
# not, the emphasis that may close the line after it); a figure inside a
# contents line, between two characters, after one '.', '．' or '·' at most
# (not after leaders: the book model reads what follows them as a line of
# its own, M-6).
JOIN_LEAD = re.compile(r"[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+)?")
PAGE_END = rm.CONTENTS_PAGE_END
INNER_FIGURE = re.compile(r"[㐀-鿿][.．·]?[ \t]*[(（]?[0-9０-９]+[)）]?[ \t]*[㐀-鿿]")
# Emphasis markers, and a run of them.  S11 takes them off in pairs or not
# at all: the run before the title with the same run ending the line and no
# such marker between (one taken alone leaves its partner).
EMPHASIS = frozenset("*_")
EMPHASIS_RUN = re.compile(r"\*+|_+")
# An opening mark and the mark that closes it; of these, the quotes that
# may wrap a whole title (S11 takes such a pair off together).
CLOSERS = {"“": "”", "「": "」", "『": "』", "‘": "’", "（": "）", "(": ")", "［": "］", "[": "]", "《": "》",
           "〈": "〉", "【": "】", "〔": "〕", "〖": "〗", "｛": "｝", "«": "»", "‹": "›", "〝": "〞"}
QUOTES = frozenset("“「『‘")
# Marks that open what follows them: a split point goes before them.
OPENERS = frozenset("「『（(《〈【“‘[［")
# S15: the line break an OCR writes as markup inside a line.
LINE_BREAK_TAG = re.compile(r"[ \t]*<br\s*/?>[ \t]*", re.IGNORECASE)
# An enumerator opening a heading (一 導言, （二）, 3.).
ENUMERATOR = re.compile(r"^[（(]?(?:[0-9０-９]{1,3}|[一二三四五六七八九十]{1,3})[）)]?"
                        r"(?:[、，,．.·：:\s]|(?=[㐀-鿿])|$)")
# How close a contents entry and a body title must be (the book model's
# contents similarity), and how many leading characters either may carry
# that the other lacks (a number's stand-ins).
TITLE_SIMILARITY = rm.CONTENTS_SIMILARITY
LEADING_DROP = rm.CONTENTS_LEADING_DROP
# The weaker pass: between two entries mapped in order, a line whose engine A
# number is the entry's and whose start is at least this close to the title.
WEAK_SIMILARITY = 0.6
# A title of fewer characters than this matches only a heading, or a line
# whose engine A number is the entry's: two characters open many lines.
SHORT_TITLE = 4
# The contents-numbering gate: of the entries whose number a source read, at
# least this share read their place in their group, and at least this many.
NUMBERING_SHARE = 0.8
NUMBERING_MINIMUM = 3
# A heading's prefix before its title that S4 may replace holds at most this
# many characters engine A did not read (a figure read as glyphs: the
# model's STANDIN_RUN).
PREFIX_CHARACTERS = rm.STANDIN_RUN
# What moves with a date into its own paragraph: a place or occasion clause
# of at most this many characters.  With more after it the date is no date
# line's, and nothing is moved.
PLACE_CLAUSE = 16
# S9: without an engine A block break after a title, what may follow the
# title on its line and still be the title's column (a subtitle, a date):
# at most this many characters.  With more, where the title ends is not shown: no split.
TITLE_TAIL = 40
# The title page: an exact reverse of at least this many characters.
REVERSE_MINIMUM = 4

DATE_NUMERALS = rm.NUMERALS + "卄"
DATE_SEPARATORS = "，、．.,"
DIGIT = {c: i for i, c in enumerate("〇一二三四五六七八九")}
DIGIT["零"] = 0
TENS = {"廿": 20, "卄": 20, "卅": 30}


# --- common ------------------------------------------------------------------

def heading_of(line: str) -> re.Match | None:
    return HEADING.match(line)


def heading_body(line: str) -> str:
    match = HEADING.match(line)
    return line[match.end():] if match else line


def folded(text: str) -> str:
    return pp.variant_fold(text)


def date_value(text: str) -> int | None:
    """A date's numeral, strictly: digits read one by one (九五, 一九二八);
    or tens and units (十, 十二, 二十, 二十三, 廿三, 卅一); None otherwise."""
    if not text:
        return None
    if all(c in DIGIT for c in text):
        return int("".join(str(DIGIT[c]) for c in text))
    if text[0] in TENS:
        rest = text[1:]
        if not rest:
            return TENS[text[0]]
        return TENS[text[0]] + DIGIT[rest] if len(rest) == 1 and rest in DIGIT and DIGIT[rest] else None
    match = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", text)
    if match:
        return (DIGIT[match.group(1)] if match.group(1) else 1) * 10 + (DIGIT[match.group(2)] if match.group(2) else 0)
    if any(c in "百千" for c in text):
        return rm.numeral_value(text)
    return None


DATE_FORMS = (
    re.compile(rf"(?P<y>[{DATE_NUMERALS}]+)年(?:(?P<m>[{DATE_NUMERALS}]+)月(?:(?P<d>[{DATE_NUMERALS}]+)日)?)?"),
    re.compile(rf"(?P<m>[{DATE_NUMERALS}]+)月(?:(?P<d>[{DATE_NUMERALS}]+)日)?"),
    re.compile(rf"(?P<a>[{DATE_NUMERALS}]{{1,4}})[{DATE_SEPARATORS}](?P<b>[{DATE_NUMERALS}]{{1,3}})"
               rf"(?:[{DATE_SEPARATORS}](?P<c>[{DATE_NUMERALS}]{{1,3}}))?[{DATE_SEPARATORS}]?"),
)


def date_at(text: str, at: int) -> int | None:
    """The end of a date starting at AT of TEXT - a year with or without its
    month and day, a month with or without its day, or two or three numeral
    groups between separators - when its numerals parse and the month is
    1-12 and the day 1-31; the longest such date; None when none parses.
    Numeral groups end at a separator, a mark, the text's end or 年/月/日:
    a group that runs on into a word is an enumerator and the word's first
    numeral (一，五湖…), no date."""
    best = None
    for form in DATE_FORMS:
        match = form.match(text, at)
        if not match:
            continue
        g = match.groupdict()
        if g.get("a") is not None:
            a, b = date_value(g["a"]), date_value(g["b"])
            c = date_value(g["c"]) if g.get("c") is not None else None
            end = match.end()
            if a is None or b is None or (g.get("c") is not None and c is None):
                ok = False
            elif text[end - 1] not in DATE_SEPARATORS and end < len(text) and pp.CJK(text[end]) \
                    and text[end] not in "年月日":
                ok = False
            elif c is not None:
                ok = 1 <= b <= 12 and 1 <= c <= 31
            else:
                ok = 1 <= b <= 12 or (1 <= a <= 12 and 1 <= b <= 31)
        else:
            ok = True
            if "y" in g and g.get("y") is not None:
                ok = date_value(g["y"]) is not None
            if g.get("m") is not None:
                ok = ok and 1 <= (date_value(g["m"]) or 0) <= 12
            if g.get("d") is not None:
                ok = ok and 1 <= (date_value(g["d"]) or 0) <= 31
            if "y" not in g and g.get("d") is None:
                # A month alone (三月) is no date line's: too many words hold it.
                ok = False
        if ok and (best is None or match.end() > best):
            best = match.end()
    return best


def find_date(text: str, start: int = 0) -> tuple[int, int] | None:
    """The first date in TEXT[start:] whose start is certain: the date that
    starts where its run of numerals starts, when it parses; else the one
    start inside that run that parses (a subtitle's ordinal glued to the
    date leaves one); None when two starts parse, or none in any run."""
    at = start
    while at < len(text):
        if text[at] not in DATE_NUMERALS:
            at += 1
            continue
        run_end = at
        while run_end < len(text) and text[run_end] in DATE_NUMERALS:
            run_end += 1
        end = date_at(text, at)
        if end is not None:
            return at, end
        found = [(s, e) for s in range(at + 1, run_end) for e in [date_at(text, s)] if e is not None]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
        at = run_end
    return None


def is_date_line(text: str) -> bool:
    """A line that is a date and at most a place clause after it."""
    body = lint.LINE_PREFIX.sub("", heading_body(text)).strip()
    if not body or body[0] not in DATE_NUMERALS:
        return False
    found = find_date(body)
    return bool(found) and found[0] == 0 and len(pp.CJK(body[found[1]:])) <= PLACE_CLAUSE


def split_back(text: str, at: int, floor: int, figures: bool = True) -> int:
    """Where to split TEXT before offset AT: back over white space and the
    opening marks just before it (they open what follows), and with FIGURES
    over the figures there too (a block's enumerator), no further back than
    FLOOR."""
    while at > floor and (text[at - 1] in OPENERS or text[at - 1] in " \t"
                          or figures and (text[at - 1].isdigit() or text[at - 1] in "０１２３４５６７８９")):
        at -= 1
    return at


def title_match(entry: str, target: str) -> tuple[float, int, int]:
    """How well ENTRY (a contents title, folded CJK) matches the start of
    TARGET (folded CJK): the best ratio with up to LEADING_DROP leading
    characters dropped from either (those dropped from the entry count
    against it) and the target window a character longer or shorter than
    the entry; and where the title lies in TARGET (start, end), its end
    before what the window holds past the entry's last character
    (title_end).  A title of fewer than three characters is matched whole.
    Its caller takes only a match of TITLE_SIMILARITY or more: a window
    that cannot reach it (too many of its characters are none of the
    entry's) is not compared."""
    best = (0.0, 0, 0)
    if not entry or not target:
        return best
    alphabet = set(entry)
    for de in range(0, min(LEADING_DROP, max(0, len(entry) - 3)) + 1):
        e = entry[de:]
        for dt in range(0, LEADING_DROP + 1):
            if dt >= len(target) or target[dt] != e[0]:
                continue
            for width in (len(e), len(e) - 1, len(e) + 1):
                if width < 1 or dt + width > len(target) or (len(entry) < 3 and width != len(e)):
                    continue
                window = target[dt:dt + width]
                shared = width - sum(c not in alphabet for c in window)
                if 2.0 * shared / (len(e) + width) * (len(e) / len(entry)) < TITLE_SIMILARITY:
                    continue
                ratio = 1.0 if window == e else difflib.SequenceMatcher(None, e, window, autojunk=False).ratio()
                ratio *= len(e) / len(entry)
                if ratio > best[0] + 1e-9:
                    best = (ratio, dt, dt + title_end(e, window))
    return best


def title_end(entry: str, window: str) -> int:
    """How much of WINDOW (the body's characters where the title ENTRY is
    matched) the title takes: all of it but the characters past the entry's
    last one that match nothing of the entry.  A body title two characters
    short of the contents' still matches with a window one short, and that
    window then holds the first character of what follows the title (the
    first numeral of its date)."""
    ops = difflib.SequenceMatcher(None, entry, window, autojunk=False).get_opcodes()
    if not ops:
        return len(window)
    tag, i1, i2, j1, j2 = ops[-1]
    if tag == "insert":
        return j1
    if tag == "replace" and j2 - j1 > i2 - i1:
        return j1 + (i2 - i1)
    return len(window)


# --- page readings ------------------------------------------------------------

class PageReading:
    """One page as the structure rules read it: the page data
    (_repair_rules.PageData), the alignment with both engines, and engine
    A's block of each of its characters."""

    def __init__(self, pd: rr.PageData):
        self.pd = pd
        self.text = pd.text
        self.align = ra.align_page(pd.text, pd.a_body, pd.b_body)
        self.block_of = rr.a_blocks_of(pd) if pd.a_blocks and pp.CJK(pd.a_body) else []
        self._folds: dict[str, str] = {}

    @property
    def s(self) -> ra.Stream:
        return self.align.sealed

    def chars_in(self, start: int, end: int) -> list[int]:
        """The sealed stream indices of the CJK characters in TEXT[start:end]."""
        offsets = self.s.offsets
        return list(range(bisect.bisect_left(offsets, start), bisect.bisect_left(offsets, end)))

    def a_index(self, k: int) -> int | None:
        return self.align.sealed_to_a[k] if 0 <= k < len(self.align.sealed_to_a) else None

    def b_index(self, k: int) -> int | None:
        return self.align.sealed_to_b[k] if 0 <= k < len(self.align.sealed_to_b) else None

    def block(self, k: int) -> int | None:
        """Engine A's block of sealed character K (None: not read by A)."""
        j = self.a_index(k)
        if j is None or not self.block_of or j >= len(self.block_of):
            return None
        return self.block_of[j]

    def block_start(self, k: int) -> bool:
        """Whether sealed character K is the first character of an engine A
        block."""
        j = self.a_index(k)
        if j is None or not self.block_of or j >= len(self.block_of):
            return False
        return j == 0 or self.block_of[j - 1] != self.block_of[j]

    def _fold(self, engine: str) -> str:
        if engine not in self._folds:
            stream = self.align.a if engine == "a" else self.align.b
            self._folds[engine] = folded(stream.chars)
        return self._folds[engine]

    def figure_at(self, k: int, chars: str = "", engine: str = "a") -> tuple[int, str] | None:
        """The figure an engine read just before sealed character K, by the
        alignment; where K is not aligned to it, just before CHARS (folded
        CJK) found in its page stream.  Its value and the mark after it."""
        found = self.gap_before(k, chars, engine)
        return gap_figure(found[0]) if found else None

    def gap_before(self, k: int, chars: str = "", engine: str = "a") -> tuple[str, bool] | None:
        """What an engine read between sealed character K and the character
        before it (its gap there), found as figure_at finds its figure, and
        whether the gap opens the engine's text; None where the engine is
        not aligned at K and CHARS does not place it: no evidence, which is
        not the same as an engine that read nothing there."""
        j = (self.a_index(k) if engine == "a" else self.b_index(k)) if k >= 0 else None
        if j is None:
            j = self.start_of(chars, engine) if chars else None
        if j is None:
            return None
        stream = self.align.a if engine == "a" else self.align.b
        return stream.gaps[j], j == 0

    def start_of(self, chars: str, engine: str = "a") -> int | None:
        """Where an engine's page stream holds CHARS (folded CJK) once: the
        whole of CHARS, else its longest start (three characters at least)
        that the stream holds only once."""
        hay = self._fold(engine)
        for size in range(len(chars), 2, -1):
            j = hay.find(chars[:size])
            if j >= 0:
                return None if hay.find(chars[:size], j + 1) >= 0 else j
        return None


def gap_figure(gap: str) -> tuple[int, str] | None:
    match = GAP_FIGURE.search(gap)
    if not match:
        return None
    return int(match.group("digits").translate(ra.FULLWIDTH_DIGITS)), (match.group("mark") or "")


def markup_only(on_line: str) -> bool:
    """Whether ON_LINE - what an engine wrote on a line before a figure - is
    markup and white space alone (GAP_MARKUP), so that the figure stands
    alone on its line; anything else there is print of that line (leaders,
    a title) and the figure its page number."""
    return GAP_MARKUP.fullmatch(on_line) is not None


def line_end_figure(line: str) -> int | None:
    """The figure that ends LINE of a contents page after print of its own
    (its page number after its leaders or its title); None where no figure
    ends it or only markup stands before the figure (a number alone on its
    line is an entry's, not a page number)."""
    match = LINE_END_FIGURE.search(line)
    if not match or markup_only(line[:match.start()]):
        return None
    return int(match.group("digits").translate(ra.FULLWIDTH_DIGITS))


class EntryGap(NamedTuple):
    """An engine's gap before a contents entry's first character, read as
    the entry's (entry_gap)."""
    lead: str                       # what it read before the title besides the entry's figure and its mark
    figure: tuple[int, str] | None  # the entry's figure and the mark after it
    side: str                       # all it read on the entry's side of the gap
    before: str | None              # the page number that ends the line before (its digits)


def entry_gap(gap: str, first: bool) -> EntryGap:
    """An engine's GAP before a contents entry's first character (FIRST: it
    opens the engine's text), read as the entry's.

    The gap runs from the last character of the line before, so it holds
    that line's end too.  What stands before its first line break is the
    line before's; and a figure that ends a line holding other print before
    it - leaders, the title - is that line's own page number, never the
    next entry's number (`……133⏎（一）…`: a contents that prints each
    entry's page at the end of its line).  A figure alone on its line
    (`12⏎title`: an entry's number printed above or before its title), or
    on the entry's own line, is the entry's.  Measured 2026-10-02: the first
    reading wrote 248 page numbers of the line before at entries' heads on
    two horizontal books (223 and 25); the second is every number of a
    vertical book's contents (its 158 edits unchanged)."""
    own = 0 if first or "\n" not in gap else gap.index("\n") + 1
    match = GAP_FIGURE.search(gap)
    if match and "\n" in gap[match.end("digits"):]:
        on_line = gap[:match.start()].rsplit("\n", 1)[-1]
        if match.start() < own or not markup_only(on_line):
            return EntryGap("", None, "", match.group("digits"))
    if not match:
        return EntryGap(gap[own:], None, gap[own:], None)
    figure = int(match.group("digits").translate(ra.FULLWIDTH_DIGITS)), (match.group("mark") or "")
    return EntryGap(gap[own:match.start()], figure, gap[own:], None)


class Readings:
    """PageReading per page text, and the contents map per state of the
    book's texts (Setting.contents), each computed once."""

    def __init__(self, book: Any, model: Mapping[str, Any]):
        self.book = book
        self.model = model
        self._cache: dict[tuple[int, str], PageReading] = {}
        self.held_contents: tuple[dict[int, str], Contents | None] | None = None

    def get(self, scan: int, text: str) -> PageReading:
        key = (scan, text)
        if key not in self._cache:
            if len(self._cache) > 4096:
                self._cache.clear()
            self._cache[key] = PageReading(rr.page_data(self.book, self.model, scan, text))
        return self._cache[key]


def all_texts(book: Any, texts: Mapping[int, str]) -> dict[int, str]:
    """Every marker page's text: under repair (TEXTS) or, for a page outside
    a subset, as sealed."""
    return {scan: texts[scan] if scan in texts else book.pages[scan].sealed_text
            for scan in sorted(book.pages) if book.pages[scan].marker}


def body_suffixes(model: Mapping[str, Any]) -> list[str]:
    """The run suffixes of the body (M-3), not the contents word; longest
    first."""
    if not rm.gate(model, "M-3"):
        return []
    words = {r.get("contentsWord") for r in (model.get("contents") or {}).get("runs") or []}
    return sorted({r["suffix"] for r in model.get("suffixRuns") or [] if r["suffix"] not in words},
                  key=len, reverse=True)


def line_spans(text: str) -> list[rr.Line]:
    return rr.lines_of(text)


# --- the contents map -----------------------------------------------------------

@dataclass
class Unit:
    """Where a contents entry's title stands in the body: the page, the line
    (1-based, in the text the map was made from), the title's span in that
    line's CJK characters, whether the line is a heading, whether the title
    starts inside the line, and the figure engine A read before it; the
    line after it that holds the title's end (S14's pair, 0 for none), and
    the body's characters from the title's line or block start on."""
    page: int
    line: int
    start: int
    end: int
    heading: bool
    mid_line: bool
    a_number: tuple[int, str] | None
    score: float
    anchor: int = 0
    joins: int = 0
    chars: str = ""


@dataclass
class Row:
    """One contents line: where it is now, its characters as the book model
    read them, its kind (`title`, `group`, `entry`), its group and place, the
    numbers the sources read, the entry's number and its unit; and for a
    title printed over two lines (M-6), the line holding its end (S14 joins
    it on; 0 for none)."""
    page: int
    line: int
    chars: str
    kind: str
    matched: bool = False
    joins: int = 0
    group: int = -1
    place: int = 0
    suffix: str | None = None
    numbers: dict[str, tuple[int, str]] = field(default_factory=dict)
    number: int | None = None
    unit: Unit | None = None


@dataclass
class Contents:
    rows: list[Row]
    pages: list[int]
    word: str | None
    numbering: dict[str, Any]
    unit_level: int
    has_divisions: bool

    def entries(self) -> list[Row]:
        return [r for r in self.rows if r.kind == "entry"]

    def groups(self) -> list[Row]:
        return [r for r in self.rows if r.kind == "group"]

    def units_on(self, page: int) -> dict[int, Row]:
        """Line -> entry, for the units standing on PAGE."""
        return {r.unit.line: r for r in self.entries() if r.unit and r.unit.page == page}


@dataclass
class BodyLine:
    page: int
    line: int
    ks: list[int]       # the sealed stream indices of the line's CJK characters
    chars: str          # folded CJK of the line
    heading: bool
    starts: list[int]   # indices into ks where an engine A block starts inside the line
    joins: int = 0      # a pair (S14): the line after it, read with it
    split: int = 0      # where that line's characters start in chars


def contents_map(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], readings: Readings,
                 facts: rr.BookFacts) -> Contents | None:
    """The contents parsed and mapped onto the body (the module's docstring);
    None where the book shows no contents (M-6)."""
    if not rm.gate(model, "M-6"):
        return None
    texts = all_texts(book, texts)
    runs = (model.get("contents") or {}).get("runs") or []
    pages = sorted({p for run in runs for p in run.get("pages") or [] if p in texts})
    if not pages:
        return None
    word = next((run.get("contentsWord") for run in runs if run.get("contentsWord")), None)
    suffixes = body_suffixes(model)
    rows: list[Row] = []
    for run in runs:
        by_page: dict[int, list[dict[str, Any]]] = {}
        for entry in run.get("entries") or []:
            by_page.setdefault(entry["page"], []).append(entry)
        for page in run.get("pages") or []:
            if page not in texts:
                continue
            lines = texts[page].split("\n")
            located = lint.locate_rows(lines, by_page.get(page, []))
            read_on = None
            for entry, number in zip(by_page.get(page, []), located):
                if number is None:
                    continue
                # The second half of an entry printed over two lines, on the
                # first half's line (S14 joined them): one entry.  Not a
                # printed line the model read on the same line as the row
                # before (M-6): a row of its own.
                piece = entry.get("line") == read_on
                read_on = entry.get("line")
                if rows and rows[-1].page == page and rows[-1].line == number and not piece:
                    rows[-1].chars += entry["text"]
                    continue
                # A title the book model reads printed over two lines,
                # found on the line holding its end: it starts on the line
                # before (blank lines between), which S14 joins it onto.
                joins = 0
                start = next((n for n in range(number - 1, 0, -1) if lines[n - 1].strip()), 0)
                if pp.CJK(lines[number - 1]) != entry["text"] and start \
                        and pp.CJK(lines[start - 1]) + pp.CJK(lines[number - 1]) == entry["text"]:
                    number, joins = start, number
                line = lines[number - 1]
                core = lint.line_core(line)
                # A line of the running head's shape left in the text (the
                # furniture stage off) is no contents line - but the first
                # contents page's heading is its title, whatever it holds.
                matched = entry.get("matchedPage") is not None
                # The title: the first line of the first contents page, no
                # unit's, a heading or holding the contents word.
                title = page == pages[0] and entry is by_page[page][0] and not matched \
                    and bool(heading_of(line) or (word and word in entry["text"]))
                if not title and facts.stems and lint.head_line(core, facts.stems,
                                                                facts.suffixes + ([word] if word else [])):
                    continue
                if not title and word and pp.CJK(line) == word:
                    continue
                rows.append(Row(page, number, entry["text"], "title" if title else "entry", matched=matched,
                                joins=joins))
    rows.sort(key=lambda r: (r.page, r.line))
    group = -1
    place = 0
    for row in rows:
        if row.kind == "title":
            continue
        suffix = next((s for s in suffixes if row.chars.startswith(s)), None)
        if suffix and not row.matched:
            row.kind, row.suffix = "group", suffix
            group += 1
            place = 0
            row.group = group
            continue
        place += 1
        row.group, row.place = max(group, 0), place
    has_divisions = any(r.kind == "group" for r in rows) or bool(model.get("dividers"))
    contents = Contents(rows, pages, word, {}, 3 if has_divisions else 2, has_divisions)
    read_numbers(contents, texts, readings)
    map_units(contents, model, texts, readings)
    return contents


def read_numbers(contents: Contents, texts: Mapping[int, str], readings: Readings) -> None:
    """Each entry's number as the page, engine A and engine B read it (an
    engine's figure only where it is the entry's: entry_gap, and not the
    page number that ends the line above); the contents-numbering gate;
    each entry's number."""
    agree = total = 0
    for row in contents.entries():
        lines = texts[row.page].split("\n")
        line = lines[row.line - 1]
        match = ENTRY_LEAD.match(line)
        if match and match.group("digits"):
            row.numbers["page"] = (int(match.group("digits").translate(ra.FULLWIDTH_DIGITS)), match.group("mark") or "")
        else:
            trail = ENTRY_TRAIL.search(line)
            if trail and pp.CJK(line[:trail.start()]):
                row.numbers["pageAfter"] = (int(trail.group("digits").translate(ra.FULLWIDTH_DIGITS)), "")
        reading = readings.get(row.page, texts[row.page])
        spans = line_spans(texts[row.page])[row.line - 1]
        ks = reading.chars_in(spans.start, spans.end)
        first = ks[0] if ks else -1
        key = folded(row.chars)
        # The figure the page prints at the end of the line above (that
        # entry's page number, after its leaders or its title).  An engine
        # that read one figure between the two titles, and that one, read
        # that page number - on a line of its own with the leaders lost, or
        # with no line break before this title - not this entry's number.
        # Two figures there (`……2⏎2 title`) are the page number and then
        # the entry's own.
        above = next((lines[n] for n in range(row.line - 2, -1, -1) if lines[n].strip()), "")
        held = line_end_figure(above)
        for engine, name in (("a", "engineA"), ("b", "engineB")):
            found = reading.gap_before(first, key, engine)
            figure = entry_gap(*found).figure if found else None
            if figure and not (figure[0] == held and len(FIGURES.findall(found[0])) == 1):
                row.numbers[name] = figure
        values = {v for v, _ in row.numbers.values()}
        if values:
            total += 1
            agree += row.place in values
    passed = total >= NUMBERING_MINIMUM and agree >= NUMBERING_SHARE * total
    contents.numbering = {"passed": passed, "read": total, "atTheirPlace": agree, "needs": NUMBERING_SHARE,
                          "minimum": NUMBERING_MINIMUM}
    for row in contents.entries():
        values = {v for v, _ in row.numbers.values()}
        if passed:
            row.number = row.place
        elif len(values) == 1 and len(row.numbers) >= 2:
            row.number = next(iter(values))


def body_lines(texts: Mapping[int, str], readings: Readings, contents_pages: Sequence[int]) -> list[BodyLine]:
    """The body's lines after the first contents page, each with its folded
    characters and where engine A starts a block inside it; after each
    heading, the pair of it and the line straight after it on its page (only
    blank lines between), read as one line (S14)."""
    out = []
    first = min(contents_pages)
    skip = set(contents_pages)
    for scan in sorted(texts):
        if scan <= first or scan in skip:
            continue
        text = texts[scan]
        reading = readings.get(scan, text)
        before = None
        for line in line_spans(text):
            ks = reading.chars_in(line.start, line.end)
            if not ks:
                continue
            chars = folded("".join(reading.s.chars[k] for k in ks))
            starts = [i for i, k in enumerate(ks) if i > 0 and reading.block_start(k)]
            if before and before[0].heading and not text[before[1]:line.start].strip():
                head = before[0]
                out.append(BodyLine(scan, head.line, head.ks + ks, head.chars + chars, True, [], line.number,
                                    len(head.chars)))
            out.append(BodyLine(scan, line.number, ks, chars, bool(heading_of(line.text)), starts))
            before = (out[-1], line.end)
    return out


def map_units(contents: Contents, model: Mapping[str, Any], texts: Mapping[int, str], readings: Readings) -> None:
    """Each entry's unit: the longest chain, in contents order and in page
    order, of body lines (or engine A block starts inside a line) matching
    the entries, each in its group's run of pages where the group names a
    run; then, between two entries so mapped, the one line that engine A
    numbers as the entry and that starts like its title."""
    entries = contents.entries()
    if not entries:
        return
    lines = body_lines(texts, readings, contents.pages)
    spans: dict[int, tuple[int, int]] = {}
    for row in contents.groups():
        run = next((r for r in (model.get("folio") or {}).get("runs") or [] if r.get("suffix") == row.suffix), None)
        if run and run.get("span"):
            spans[row.group] = (run["span"][0], run["span"][1])
    index: dict[str, list[tuple[int, int]]] = {}
    for n, body in enumerate(lines):
        for c in [0] + body.starts:
            for d in range(LEADING_DROP + 1):
                if c + d < len(body.chars):
                    index.setdefault(body.chars[c + d], []).append((n, c))

    def number_at(body: BodyLine, c: int) -> tuple[int, str] | None:
        reading = readings.get(body.page, texts[body.page])
        return reading.figure_at(body.ks[c], body.chars[c:c + 6]) if c < len(body.ks) else None

    candidates: list[tuple[int, int, int, float, int, int]] = []
    for e, row in enumerate(entries):
        key = folded(row.chars)
        span = spans.get(row.group)
        run_on = bool(INNER_FIGURE.search(texts[row.page].split("\n")[row.line - 1]))
        seen = set()
        for d in range(min(LEADING_DROP, max(0, len(key) - 1)) + 1):
            for n, c in index.get(key[d], ()):
                if (n, c) in seen:
                    continue
                seen.add((n, c))
                body = lines[n]
                if span and not span[0] <= body.page <= span[1]:
                    continue
                ratio, start, end = title_match(key, body.chars[c:])
                if ratio < TITLE_SIMILARITY:
                    continue
                # A pair (S14) holds the title where neither of its lines
                # does alone; its second line goes on with the title (not a
                # line after a title the engines read short), and a plain
                # one (lines[n + 1]: body_lines puts it after the pair) ends
                # with it.  Not for an entry whose contents line holds a
                # figure between two characters (its page number with no
                # leaders before it, its date run on after: the entry holds
                # the date).
                if body.joins:
                    rest = key[max(0, body.split - start):]
                    if run_on or any(title_match(key, half)[0] >= TITLE_SIMILARITY
                                     for half in (body.chars[:body.split], body.chars[body.split:])) \
                            or difflib.SequenceMatcher(None, rest, body.chars[body.split:body.split + len(rest)],
                                                       autojunk=False).ratio() < WEAK_SIMILARITY \
                            or not lines[n + 1].heading and end < len(body.chars):
                        continue
                number = number_at(body, c + start)
                agrees = bool(number and row.number is not None and number[0] == row.number)
                whole_heading = body.heading and c == 0
                if len(key) < SHORT_TITLE and not whole_heading and not agrees:
                    continue
                score = ratio + (0.5 if whole_heading else 0.0) + (0.5 if agrees else 0.0)
                candidates.append((e, n, c, score, start, end))
    placed = {cand[0]: cand for cand in longest_chain(candidates)}
    for e, row in enumerate(entries):
        if e in placed or row.number is None:
            continue
        before = max((placed[x][1] for x in placed if x < e), default=-1)
        after = min((placed[x][1] for x in placed if x > e), default=len(lines))
        key = folded(row.chars)
        span = spans.get(row.group)
        found = []
        for n in range(before + 1, after):
            body = lines[n]
            if body.joins or span and not span[0] <= body.page <= span[1]:
                continue
            for c in [0] + body.starts:
                rest = body.chars[c:]
                number = number_at(body, c)
                if not number or number[0] != row.number:
                    continue
                window = rest[:len(key)]
                ratio = difflib.SequenceMatcher(None, key, window, autojunk=False).ratio()
                if ratio >= WEAK_SIMILARITY:
                    found.append((e, n, c, ratio, 0, title_end(key, window)))
        if len(found) == 1:
            placed[e] = found[0]
    for e, (_, n, c, score, start, end) in placed.items():
        body = lines[n]
        entries[e].unit = Unit(body.page, body.line, c + start, c + end, body.heading and c == 0, c > 0,
                               number_at(body, c + start), round(score, 3), c, body.joins, body.chars[c:])


def longest_chain(candidates: Sequence[tuple]) -> list[tuple]:
    """The chain of candidates (entry, line, start, score, ...) with the entry
    and the place in the body both increasing: the longest, then the one
    with the highest score."""
    ordered = sorted(candidates, key=lambda c: (c[0], c[1], c[2]))
    best: list[tuple[int, float, int]] = []
    for i, cand in enumerate(ordered):
        top = (1, cand[3], -1)
        for j in range(i):
            other = ordered[j]
            if other[0] < cand[0] and (other[1], other[2]) < (cand[1], cand[2]):
                length, score, _ = best[j]
                if (length + 1, score + cand[3]) > top[:2]:
                    top = (length + 1, score + cand[3], j)
        best.append(top)
    if not ordered:
        return []
    k = max(range(len(ordered)), key=lambda i: best[i][:2])
    out = []
    while k >= 0:
        out.append(ordered[k])
        k = best[k][2]
    return list(reversed(out))


# --- shared by the stages ----------------------------------------------------------

@dataclass
class Setting:
    """What a structure stage reads: the book, its model, the facts the
    rules share, the page readings, the pages under repair and the texts
    as they stand (updated by the ledger as passes are applied)."""
    book: Any
    model: Mapping[str, Any]
    facts: rr.BookFacts
    readings: Readings
    order: list[int]
    texts: Mapping[int, str]

    def reading(self, scan: int) -> PageReading:
        return self.readings.get(scan, self.texts[scan])

    def contents(self) -> Contents | None:
        """The contents map of the texts as they stand, made again only
        when a text changed since the last one."""
        texts = all_texts(self.book, self.texts)
        held = self.readings.held_contents
        if held is None or held[0] != texts:
            held = (texts, contents_map(self.book, self.model, texts, self.readings, self.facts))
            self.readings.held_contents = held
        return held[1]

    def finals(self) -> str:
        return "".join(self.model.get("sentenceFinal") or "。！？")


# --- contents (book level) ------------------------------------------------------------

def break_span(text: str, at: int, floor: int, figures: bool = True) -> tuple[int, int]:
    """The span to replace with a paragraph break before offset AT: from the
    split point (split_back, FIGURES as there) over the white space after
    it, and back over the white space before it."""
    cut = split_back(text, at, floor, figures)
    start = cut
    while start > floor and text[start - 1] in " \t":
        start -= 1
    end = cut
    while end < len(text) and text[end] in " \t":
        end += 1
    return start, end


def unit_line(setting: Setting, row: Row) -> tuple[PageReading, rr.Line, list[int]]:
    reading = setting.reading(row.unit.page)
    line = line_spans(reading.text)[row.unit.line - 1]
    return reading, line, reading.chars_in(line.start, line.end)


def joined(text: str, first: rr.Line, second: rr.Line) -> tuple[int, int, str]:
    """S14's edit joining line SECOND onto line FIRST of TEXT: the span from
    FIRST's last character to SECOND's first past its heading marker or
    bullet, and what takes its place - nothing, or a space between two
    letters of an alphabet (before U+3000: kana and CJK space no words)."""
    start = first.start + len(first.text.rstrip())
    end = second.start + JOIN_LEAD.match(second.text).end()
    letters = all(c.isalpha() and c < "\u3000" for c in (text[start - 1], text[end]))
    return start, end, " " if letters else ""


def s15(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S15: a contents line holding printed lines the OCR ran together (the
    rows the book model reads apart: M-6) cut into them where engine A read
    a line break between two of them (the module's docstring).  Measured
    2026-10-07 on two archive books: 18 and 30 such lines, holding 58 and
    60 rows (a whole page on one line, whose structure pass phase 3
    refused, and title lines that lost their page number, the date line run
    on after them), all cut; none on two other books."""
    for scan in contents.pages:
        if scan not in setting.order:
            continue
        held: dict[int, list[str]] = {}
        for row in contents.rows:
            if row.page == scan:
                held.setdefault(row.line, []).append(row.chars)
        reading = setting.reading(scan)
        text, offsets, a_gaps = reading.text, reading.s.offsets, reading.align.a.gaps
        for line in line_spans(text):
            rows = held.get(line.number, [])
            # Only a line that spells the rows it holds (no stand-in written
            # over or taken away since the model read them): the contents
            # map finds them again on the lines cut (_book_lint.locate_rows).
            if "".join(rows) != pp.CJK(line.text):
                continue
            ks = reading.chars_in(line.start, line.end)
            cuts = set()
            end = 0
            for row in rows[:-1]:
                end += len(row)
                # The book model's cut, or engine A's line break up to
                # LEADING_DROP characters after it: a note after a date (批)
                # is the date line's, which the model's cut leaves with the
                # title after it.
                for i in range(end, end + LEADING_DROP + 1):
                    j = reading.a_index(ks[i - 1]) if 0 < i < len(ks) else None
                    if j is not None and reading.a_index(ks[i]) == j + 1 and "\n" in a_gaps[j + 1]:
                        cuts.add(i)
                        break
            for i in sorted(cuts):
                floor, at = offsets[ks[i - 1]] + 1, offsets[ks[i]]
                tag = LINE_BREAK_TAG.search(text, floor, at)
                start, stop = (tag.span() if tag else break_span(text, at, floor, figures=False))
                plan.edits.append(re_.Edit(scan, start, stop, "\n\n", rule="S15", evidence={
                    "line": line.text, "before": text[floor:at], "engineA": a_gaps[reading.a_index(ks[i])]}))


def s14(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S14: unit titles printed over two lines, joined (the module's
    docstring).  Measured 2026-10-06 on two archive books (each entry a
    memorial, its long title set over two lines): 19 and 75 titles joined in
    the body, 1 entry in the contents; their `unit-not-in-scan` doubts 26 ->
    7 and 91 -> 15 (left: wraps across a page, a contents line that holds a
    date and the next title, titles the engines misread); 1 and 13 titles
    still on two lines, 11 of them listed on a contents page the book model
    leaves out (no entry to match, no doubt); two other books unchanged.
    With those pages read (M-6 reads contents lines the OCR ran together,
    2026-10-06): 2 and 9 more titles joined, 0 and 4 still on two lines
    (three misread in the body, one with its date line between the
    halves).  Measured 2026-10-11 on three archive books whose contents
    phase 3 seals with engine A's line breaks (a wrapped title on two
    lines), the book model reading 36, 29 and 21 wrapped titles as one
    entry (M-6): contents lines joined 39 -> 39, 27 -> 29 and 18 -> 22, and
    body titles 20 -> 20, 83 -> 85 and 21 -> 24 (the titles whose first
    contents line, read alone, matched no unit); the pair rule on the first
    entry's unit still joins 3, 0 and 1 that the model reads apart (a
    character misread on one side); two other books unchanged."""
    for row in contents.entries():
        unit = row.unit
        if unit and unit.joins and unit.page in setting.order:
            lines = line_spans(setting.texts[unit.page])
            first, second = lines[unit.line - 1], lines[unit.joins - 1]
            start, end, between = joined(setting.texts[unit.page], first, second)
            plan.edits.append(re_.Edit(unit.page, start, end, between, rule="S14", evidence={
                "entry": row.chars, "side": "body", "lines": [first.text, second.text]}))
    for row in contents.rows:
        if row.joins and row.page in setting.order:
            text = setting.texts[row.page]
            first, second = line_spans(text)[row.line - 1], line_spans(text)[row.joins - 1]
            start, end, between = joined(text, first, second)
            plan.edits.append(re_.Edit(row.page, start, end, between, rule="S14", evidence={
                "entry": row.chars, "side": "contents", "lines": [first.text, second.text]}))
    for one, two in zip(contents.rows, contents.rows[1:]):
        if one.kind != "entry" or two.kind != "entry" or one.unit is None or one.page != two.page \
                or one.page not in setting.order:
            continue
        text = setting.texts[one.page]
        first, second = line_spans(text)[one.line - 1], line_spans(text)[two.line - 1]
        # The second line, not the first, ends with the entry's page number
        # (a line with none after an entry is its date), and the body
        # prints the title further than the first line alone.
        if text[first.end:second.start].strip() or PAGE_END.search(first.text) or not PAGE_END.search(second.text):
            continue
        ratio, _, end = title_match(folded(one.chars + two.chars), one.unit.chars)
        if ratio >= TITLE_SIMILARITY and end > one.unit.end - one.unit.anchor:
            start, end, between = joined(text, first, second)
            plan.edits.append(re_.Edit(one.page, start, end, between, rule="S14", evidence={
                "entry": one.chars + two.chars, "side": "contents", "lines": [first.text, second.text]}))


def s9(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S9: each entry whose unit title is not yet a heading of its own."""
    level = contents.unit_level
    for row in contents.entries():
        unit = row.unit
        if unit is None or unit.heading or unit.page not in setting.order:
            continue
        reading, line, ks = unit_line(setting, row)
        text = reading.text
        if unit.end > len(ks):
            continue
        site = (reading.s.offsets[ks[unit.start]], reading.s.offsets[ks[unit.end - 1]] + 1)
        if unit.a_number is None or row.number is None or unit.a_number[0] != row.number:
            continue
        # After the title: engine A's next block, or the end of a date.
        first_block = next((reading.block(k) for k in ks[unit.start:unit.end] if reading.block(k) is not None), None)
        tail = None
        for k in ks[unit.end:]:
            if reading.block_start(k) and reading.block(k) != first_block:
                tail = reading.s.offsets[k]
                break
        rest = len(ks) - unit.end
        if tail is None and rest > TITLE_TAIL:
            found = find_date(text[:line.end], reading.s.offsets[ks[unit.end - 1]] + 1)
            if found and len(pp.CJK(text[found[1]:line.end])) > PLACE_CLAUSE:
                end = found[1]
                while end < line.end and not pp.CJK(text[end]) and not text[end].isspace():
                    end += 1
                tail = end
            else:
                continue
        if unit.mid_line:
            anchor = reading.s.offsets[ks[unit.anchor]]
            start, end = break_span(text, anchor, line.start)
            plan.edits.append(re_.Edit(unit.page, start, end, "\n\n" + "#" * level + " ", rule="S9", evidence={
                "entry": row.chars, "number": row.number, "engineA": unit.a_number[0], "split": "engine A block"}))
        else:
            lead = len(line.text) - len(line.text.lstrip())
            plan.edits.append(re_.Edit(unit.page, line.start + lead, line.start + lead, "#" * level + " ",
                                       rule="S9", evidence={"entry": row.chars, "number": row.number,
                                                            "engineA": unit.a_number[0]}))
        if tail is not None:
            start, end = break_span(text, tail, site[1])
            plan.edits.append(re_.Edit(unit.page, start, end, "\n\n", rule="S9", evidence={
                "entry": row.chars, "after": "the title's end: engine A's next block or a date"}))


def s4(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S4 on each unit heading; then its mark doubt: a unit
    number written with no mark (engine A read none, the page holds none)
    where the units on either side of it in its group carry one - engine A
    misses a small full stop - is a `punctuation-partial` doubt over the
    number as written."""
    numbered: list[tuple[Row, str, str, int, int]] = []
    for row in contents.entries():
        unit = row.unit
        if unit is None or not unit.heading or unit.page not in setting.order:
            continue
        reading, line, ks = unit_line(setting, row)
        text = reading.text
        match = heading_of(line.text)
        if not match or unit.start >= len(ks):
            continue
        prefix_start = line.start + match.end()
        prefix_end = reading.s.offsets[ks[unit.start]]
        prefix = text[prefix_start:prefix_end]
        n = row.number
        if n is None:
            continue
        number = unit.a_number
        if number is None or number[0] != n:
            if re.fullmatch(rf"\s*{n}\s*[.．,，、]?\s*", prefix):
                continue
            why = (f"the contents give this unit the number {n}; engine A read "
                   + (f"{number[0]}" if number else "no number") + " before its title")
            plan.doubts.append(rr.unsettled("HEAD-NUMBER-LOST", unit.page, "S4", text, prefix_start, prefix_end,
                                            why, entry=row.chars, contentsNumber=n,
                                            engineA=number[0] if number else None))
            continue
        # The page holds the figure engine A read before the title already,
        # above the heading's line (a line of its own): written again it
        # would be printed twice.
        k = ks[unit.start]
        above = text[reading.s.offsets[k - 1] + 1 if k else 0:line.start]
        if re.search(rf"(?<!\d){n}(?!\d)", above):
            continue
        mark = number[1]
        held = re.match(rf"\s*{n}\s*(?P<mark>[.．,，、。])", prefix)
        if not mark and held:
            # Engine A read the figure and no mark after it; the page's mark
            # there stays (a mark is no stand-in).
            mark = held.group("mark")
        want = f"{n}{mark} "
        numbered.append((row, mark.replace("．", "."), text[:prefix_start] + want + text[prefix_end:], prefix_start,
                         prefix_start + len(want)))
        if prefix == want:
            continue
        pks = reading.chars_in(prefix_start, prefix_end)
        read = [reading.s.chars[k] for k in pks if reading.a_index(k) is not None]
        if read or len(pks) > PREFIX_CHARACTERS:
            plan.doubts.append(rr.unsettled(
                "HEAD-NUMBER-LOST", unit.page, "S4", text, prefix_start, prefix_end,
                f"engine A read the number {n} before this unit's title, and the page holds {prefix!r} there"
                + (f", of which engine A read {''.join(read)}" if read else ""), entry=row.chars,
                contentsNumber=n))
            continue
        plan.edits.append(re_.Edit(unit.page, prefix_start, prefix_end, want, rule="S4", evidence={
            "entry": row.chars, "contentsNumber": n, "engineA": f"{number[0]}{number[1]}",
            "replaced": prefix, "numberSources": sorted(row.numbers)}))
    for i, (row, mark, page, start, end) in enumerate(numbered):
        if mark:
            continue
        before = next((m for other, m, *_ in reversed(numbered[:i]) if other.group == row.group), None)
        after = next((m for other, m, *_ in numbered[i + 1:] if other.group == row.group), None)
        if not before or not after:
            continue
        plan.doubts.append(rr.unsettled(
            "punctuation-partial", row.unit.page, "S4", page, start, end,
            f"unit {row.number} is written without a mark after its number, where the units on either side of "
            f"it carry {before!r} and {after!r}", entry=row.chars, contentsNumber=row.number))


def s7(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S7 on each unit heading."""
    for row in contents.entries():
        unit = row.unit
        if unit is None or not unit.heading or unit.page not in setting.order:
            continue
        reading, line, ks = unit_line(setting, row)
        if unit.end > len(ks) or unit.end < 1:
            continue
        split_date(setting, reading, line, reading.s.offsets[ks[unit.end - 1]] + 1, "S7", plan,
                   {"entry": row.chars})


def split_date(setting: Setting, reading: PageReading, line: rr.Line, after: int, rule: str, plan: rr.Plan,
               evidence: Mapping[str, Any]) -> bool:
    """A date after offset AFTER on LINE, with at most a place clause after
    it, to its own paragraph (S7).  Whether an edit was proposed."""
    text = reading.text
    found = find_date(text[:line.end], after)
    if not found or len(pp.CJK(text[found[1]:line.end])) > PLACE_CLAUSE:
        return False
    # A figure just before the date's numerals ends what the date follows
    # (an ordinal 之2): no date opens with one.
    start, end = break_span(text, found[0], after, figures=False)
    if start == end and start == after and text[after - 1:after].isspace():
        return False
    # Marks between the title and the date that neither engine read there
    # (the punctuator's comma before a date set apart in smaller type) go
    # with the split: left, they would end the heading.
    dropped = unread_marks_before(reading, after, start)
    if dropped:
        start = after
    plan.edits.append(re_.Edit(reading.pd.scan, start, end, "\n\n", rule=rule, evidence={
        **evidence, "date": text[found[0]:found[1]], "moved": text[found[0]:line.end],
        **({"dropped": dropped} if dropped else {})}))
    return True


def unread_marks_before(reading: PageReading, lo: int, hi: int) -> str:
    """The marks the page holds in TEXT[lo:hi] (between a title's last
    character and a date after it), when that is only marks and white space
    and neither engine read any of them between the two characters: they
    are adjacent in engine A, with none of those marks between, and in
    engine B, with no mark between (engine B's stand-in glyph for a mark is
    a character of its own: adjacent, it read none); else ''."""
    text = reading.text
    held = text[lo:hi]
    if not held.strip() or pp.CJK(held) or any(c.isalnum() for c in held):
        return ""
    ks = reading.chars_in(0, lo)
    k_after = bisect.bisect_left(reading.s.offsets, hi)
    if not ks or k_after >= len(reading.s.chars):
        return ""
    before, following = reading.a_index(ks[-1]), reading.a_index(k_after)
    if before is None or following is None or following != before + 1:
        return ""
    read = rr.marks_of(reading.align.a.gaps[following])
    marks = rr.marks_of(held)
    b_before, b_following = reading.b_index(ks[-1]), reading.b_index(k_after)
    if b_before is None or b_following is None or b_following != b_before + 1 \
            or rr.marks_of(reading.align.b.gaps[b_following]):
        return ""
    return marks if not any(c in read for c in marks) else ""


def entry_line(reading: PageReading, row: Row, line: rr.Line) -> tuple[str, list[dict[str, Any]], tuple[str, str] | None]:
    """S11: a contents entry's line as rebuilt, what it leaves open, and -
    where the figure at its head is the line before's page number - that
    figure and the line without it (s11 moves it where the line before can
    take it).

    Engine A's evidence is its gap before the title read as the entry's
    (entry_gap); an engine not placed at the title is no evidence, and what
    the page holds there then stays.  The number is written before the
    title only where the line's head is a number's place: not where a
    bracket engine A read opens it (`（1）`, `（一）`: the entry's own
    enumerator), nor where it holds further figures engine A read (`3
    1949年…`).  There, and where no number is written, the head stays as
    the page holds it - its figures and letters too, print whatever engine
    A read, and every mark where engine A missed one of those - less the
    marks engine A did not read; an opening bracket or quote engine A did
    not read stays too where the line holds its partner (dropped, it would
    leave the partner alone), but for the quotes that wrap the whole title.
    Emphasis markers that wrap the whole entry go where engine A wrote
    none, both of them; a run with no partner at the line's end, or with
    one inside the line (two spans), stays; emphasis closed inside the head
    around the number alone goes with the number's old form."""
    text = line.text
    match = ENTRY_LEAD.match(text)
    ks = reading.chars_in(line.start, line.end)
    if not ks:
        return text, [], None
    title_at = reading.s.offsets[ks[0]] - line.start
    markup_end = min(match.end("markup"), title_at)
    lead_size = min(match.end(), title_at) - markup_end
    head = text[markup_end:title_at]
    body = text[title_at:]
    trail = ENTRY_TRAIL.search(body)
    if trail and "pageAfter" in row.numbers and row.number == row.numbers["pageAfter"][0]:
        body = body[:trail.start()]
    body = body.rstrip()
    # What engine A read before the title besides its figure and the mark
    # after it (those are the number, written apart), and all it read there.
    found = reading.gap_before(ks[0], folded(row.chars))
    a_gap = entry_gap(*found) if found else None
    a_lead, a_side = (a_gap.lead, a_gap.side) if a_gap else (None, None)

    def stays(c: str, read: str | None) -> bool:
        if c in EMPHASIS or read is None or c in read:
            return True
        # An opening mark whose partner the line holds stays with it, but
        # for the quotes that wrap the whole title (drop_closers).
        closer = CLOSERS.get(c)
        return bool(closer) and closer in body and not (c in QUOTES and body.endswith(closer))

    # The first run of emphasis markers in the head, where the same run ends
    # the line and no such marker stands between: the pair that wraps the
    # whole entry.  A run with a partner inside the line (two spans, the
    # number set in bold alone) is no wrap: both stay.
    wrap = EMPHASIS_RUN.search(head)
    if wrap and a_side is not None and wrap.group()[0] not in a_side and len(body) > len(wrap.group()) \
            and body.endswith(wrap.group()) and wrap.group()[0] not in head[wrap.end():] \
            and wrap.group()[0] not in body[:-len(wrap.group())]:
        head = head[:wrap.start()] + head[wrap.end():]
        body = body[:-len(wrap.group())].rstrip()
        if wrap.start() < lead_size:
            lead_size -= len(wrap.group())
    stray = head[lead_size:]
    number = row.number
    sources = [name for name, (value, _) in row.numbers.items() if value == number] if number is not None else []
    bracket = match.group("pre").strip()
    opened = bool(bracket) and stays(bracket, a_lead)
    further = any(c.isdigit() and stays(c, a_lead) for c in stray)
    open_items = []
    if number is None or not sources or opened or further:
        if number is not None and not sources:
            open_items.append({"why": f"no source read the number of entry {number} ({row.chars})"})
        # No number is written: what the page holds before the title that
        # an engine read there stays, and so do its letters and figures,
        # which are print whatever engine A read; where it missed one of
        # those, its silence on the marks beside it is no evidence either.
        missed = a_side is not None and any(c.isalnum() and c not in a_side for c in head)
        dropped = "" if missed else "".join(c for c in head if not c.isspace() and not c.isalnum()
                                            and not stays(c, a_side))
        kept = "".join(c for c in head if c not in dropped).lstrip() if dropped else head
        body = drop_closers(reading, ks, body, dropped)
        # The figure at the head that engine A - and engine B, where it is
        # placed here - read as the page number ending the line before.
        digits = match.group("digits")
        carried = None
        if digits and not bracket and not match.group("mark") and not further and a_gap and a_gap.before \
                and a_gap.before.translate(ra.FULLWIDTH_DIGITS) == digits.translate(ra.FULLWIDTH_DIGITS):
            b_found = reading.gap_before(ks[0], folded(row.chars), "b")
            b_gap = entry_gap(*b_found) if b_found else None
            if b_gap is None or (b_gap.before or "").translate(ra.FULLWIDTH_DIGITS) == \
                    digits.translate(ra.FULLWIDTH_DIGITS):
                carried = (digits, (kept[:kept.index(digits)] + kept[kept.index(digits) + len(digits):]).lstrip() + body)
        return kept + body, open_items, carried
    kept = "".join(c for c in stray if not c.isspace() and not c.isdigit() and stays(c, a_lead))
    dropped = "".join(c for c in stray if not c.isspace() and not c.isdigit() and not stays(c, a_lead))
    # Emphasis whose runs pair up inside the head (`**1**`: the number set
    # in bold alone) goes with the number's old form; a run whose partner
    # is in the title, or has none, stays.
    kept = "".join(c for c in kept if c not in EMPHASIS or not paired_runs(head, c))
    body = drop_closers(reading, ks, body, dropped)
    # The mark after the number: engine A's, else the page's own, else
    # engine B's - a figure read without its mark does not take the page's
    # mark away.
    mark = next((row.numbers[name][1] for name in ("engineA", "page", "engineB", "pageAfter")
                 if name in sources and row.numbers[name][1]), "")
    mark = mark.replace("．", ".")
    return f"{number}{mark} {kept}{body}", open_items, None


def paired_runs(head: str, marker: str) -> bool:
    """Whether the runs of the emphasis MARKER in HEAD close one another
    (`**1** `: two runs of the same length), so the emphasis wrapped what
    stands between them alone."""
    runs = re.findall(re.escape(marker) + "+", head)
    return bool(runs) and len(runs) % 2 == 0 and all(len(a) == len(b) for a, b in zip(runs[::2], runs[1::2]))


def drop_closers(reading: PageReading, ks: Sequence[int], body: str, dropped: str) -> str:
    """BODY (an entry's title to its line's end) without the closing quote
    that ends it, where its opening partner is among the marks DROPPED from
    before the title and engine A read no such closer after the title."""
    for opener in dropped:
        closer = CLOSERS.get(opener) if opener in QUOTES else None
        if closer and body.endswith(closer):
            jl = reading.a_index(ks[-1])
            after_gap = reading.align.a.gaps[jl + 1] if jl is not None and jl + 1 < len(reading.align.a.gaps) else ""
            if closer not in after_gap:
                body = body[:-1]
    return body


def s11(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
    """S11: the contents pages rebuilt (the module's docstring), and its
    doubts on the rebuilt texts."""
    level = contents.unit_level
    rebuilt: dict[int, str] = {}
    marks_by_group: dict[int, list[tuple[Row, str]]] = {}
    for scan in contents.pages:
        if scan not in setting.order:
            continue
        reading = setting.reading(scan)
        text = reading.text
        lines = line_spans(text)
        rows = sorted((r for r in contents.rows if r.page == scan), key=lambda r: r.line)
        # A line still holding more than one row (printed lines the OCR ran
        # together, M-6, that S15 did not cut) is rebuilt once, as its
        # first row's.
        rows = [row for k, row in enumerate(rows) if not k or row.line != rows[k - 1].line]
        new_text = {}
        pending: list[dict[str, Any]] = []
        moved: dict[int, dict[str, str]] = {}
        for k, row in enumerate(rows):
            line = lines[row.line - 1]
            body = ENTRY_LEAD.match(line.text)
            core = line.text[body.end("markup"):].strip() if body else line.text.strip()
            if row.kind == "title":
                new = "#" * 2 + " " + core
            elif row.kind == "group":
                ks = reading.chars_in(line.start, line.end)
                cut = reading.s.offsets[ks[len(row.suffix) - 1]] + 1 - line.start if len(ks) >= len(row.suffix) else 0
                head = line.text[body.end("markup") if body else 0:cut].strip()
                rest = line.text[cut:].strip()
                new = "#" * level + " " + head + (" " + rest if rest else "")
            else:
                new, open_items, carried = entry_line(reading, row, line)
                # A page number the page holds at the head of this line,
                # which the engines read at the end of the line before, goes
                # there: where that line is the entry just above (nothing but
                # white space between them), ends with no figure and with no
                # markup, and a mark of its own - its leaders - ends it.
                above = rows[k - 1] if k else None
                if carried and above is not None and above.kind == "entry" \
                        and not text[lines[above.line - 1].end:line.start].strip():
                    held = new_text[above.line]
                    if held and not held[-1].isalnum() and not pp.CJK(held[-1]) and held[-1] not in "*_`>|" \
                            and not held[-1].isspace():
                        new_text[above.line] = held + carried[0]
                        moved.setdefault(above.line, {})["fromTheLineAfter"] = carried[0]
                        moved.setdefault(row.line, {})["toTheLineBefore"] = carried[0]
                        new = carried[1]
                for item in open_items:
                    pending.append({"row": row, **item})
                marks_by_group.setdefault(row.group, []).append((row, new))
                # A heading engine A reads as a title block (the contents'
                # own heading, which the map took for an entry) stays one.
                ks = reading.chars_in(line.start, line.end)
                block = reading.block(ks[0]) if ks else None
                if heading_of(line.text) and block is not None \
                        and reading.pd.a_blocks[block].get("label") in rm.TITLE_LABELS:
                    new = "#" * level + " " + new
            new_text[row.line] = new
        for row in rows:
            line = lines[row.line - 1]
            if new_text[row.line] != line.text:
                plan.edits.append(re_.Edit(scan, line.start, line.end, new_text[row.line], rule="S11", evidence={
                    "kind": row.kind, "numbers": {k: f"{v}{m}" for k, (v, m) in row.numbers.items()},
                    "number": row.number, "place": row.place, **moved.get(row.line, {})}))
        for one, two in zip(rows, rows[1:]):
            a, b = lines[one.line - 1], lines[two.line - 1]
            between = text[a.end:b.start]
            if between.strip():
                continue
            tight = bool(LIST_ITEM.match(new_text[one.line]) and LIST_ITEM.match(new_text[two.line]))
            want = "\n" if tight else "\n\n"
            if between != want:
                plan.edits.append(re_.Edit(scan, a.end, b.start, want, rule="S11", evidence={
                    "between": "list items" if tight else "entries"}))
        # The page as rebuilt, for the doubts' sites.
        page = "\n".join(new_text.get(line.number, line.text) for line in lines)
        rebuilt[scan] = page
        for item in pending:
            row = item["row"]
            where = page.split("\n")
            offset = sum(len(x) + 1 for x in where[:row.line - 1])
            plan.doubts.append(rr.unsettled("TOC-NUMBER-LOST", scan, "S11", page, offset, offset, item["why"],
                                            entry=row.chars, contentsNumber=row.number, group=row.group))
    # A number written without the mark the entries on either side carry.
    for group, items in marks_by_group.items():
        written = [(row, re.match(r"(\d{1,3})([^\s\d]?) ", new)) for row, new in items]
        for i, (row, match) in enumerate(written):
            if not match or match.group(2):
                continue
            before = next((m.group(2) for _, m in reversed(written[:i]) if m), None)
            after = next((m.group(2) for _, m in written[i + 1:] if m), None)
            around = [x for x in (before, after) if x is not None]
            if len(around) == 2 and around[0] and around[0] == around[1] and row.page in rebuilt:
                page = rebuilt[row.page]
                offset = sum(len(x) + 1 for x in page.split("\n")[:row.line - 1])
                plan.doubts.append(rr.unsettled(
                    "punctuation-partial", row.page, "S11", page, offset, offset + len(match.group(0)),
                    f"entry {row.number} is read without the {around[0]!r} its neighbours carry",
                    entry=row.chars, contentsNumber=row.number, group=row.group))


def contents_stage(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
                   apply: Callable[[Sequence[re_.Edit]], None], facts: rr.BookFacts | None = None,
                   readings: Readings | None = None) -> rr.Plan:
    """The contents stage over the pages ORDER, in passes applied through
    APPLY: S15; S14; S9; K1's dash on the units S9 makes headings; S4; S7; S11.
    Returns the last pass's edits and the doubts: the rules', the gate's and
    the `unit-not-in-scan` ones."""
    facts = facts or rr.book_facts(book, model)
    setting = Setting(book, model, facts, readings or Readings(book, model), list(order), texts)
    plan = rr.Plan()
    contents = setting.contents()
    if contents is None:
        return plan
    # S15 and S14 first: the map, the gate and the passes after them read
    # the printed lines S15 cuts apart one to a line, and the titles S14
    # joins as one.
    for passes in (s15, s14):
        step = rr.Plan()
        passes(setting, contents, step)
        apply(step.edits)
        contents = setting.contents()
    if not contents.numbering.get("passed"):
        plan.doubts.append(("gate-failed", None, "contents-numbering: the contents' numbers read are not their "
                            "places in their groups, so no entry's number is taken from its place",
                            {"gate": "contents-numbering", "measured": contents.numbering}))
    # The unit titles S9 is to make headings: the marks stage saw the
    # others, and named what it could not settle there.
    promoted = {(row.page, row.line) for row in contents.entries() if row.unit and not row.unit.heading}

    def dashes(setting: Setting, contents: Contents, plan: rr.Plan) -> None:
        new: dict[int, set[int]] = {}
        for row in contents.entries():
            if row.unit and row.unit.page in order and (row.page, row.line) in promoted:
                new.setdefault(row.unit.page, set()).add(row.unit.line)
        for scan in sorted(new):
            step = rr.heading_dashes(setting.book, setting.model, scan, setting.texts[scan], setting.facts)
            plan.edits.extend(step.edits)
            plan.doubts.extend(d for d in step.doubts if d[3]["line"] in new[scan])

    for passes in (s9, dashes, s4, s7):
        step = rr.Plan()
        passes(setting, contents, step)
        plan.doubts.extend(step.doubts)
        apply(step.edits)
        contents = setting.contents()
    s11(setting, contents, plan)
    for row in contents.entries():
        if row.unit is None and row.page in order:
            plan.doubts.append(("unit-not-in-scan", row.page,
                                f"contents entry {row.number or row.place} of group {row.group + 1} ({row.chars}) has "
                                "no unit in the body", {"entry": row.chars, "line": row.line,
                                                        "number": row.number, "group": row.group + 1}))
    return plan


# --- the title page ----------------------------------------------------------------

def reversed_lines(reading: PageReading) -> tuple[str, list[dict[str, Any]]] | None:
    """S13's test: which engine read the page's lines backwards - one of its
    lines (REVERSE_MINIMUM characters or more) is the exact reverse of a run
    the other engine read, and the other engine never read it that way round
    - and the pairs found.  None when neither did."""
    a_lines = [line for block in reading.pd.a_blocks for line in block["text"].split("\n")]
    # Engine B's whole reading, its head copies too: a title page prints the
    # book's title, which is the running head's stem.
    b_lines = reading.pd.b_text.split("\n")
    a_all, b_all = folded(pp.CJK(reading.pd.a_body)), folded(pp.CJK(reading.pd.b_text))
    for engine, lines, other in (("a", a_lines, b_all), ("b", b_lines, a_all)):
        pairs = []
        for line in lines:
            chars = folded(pp.CJK(line))
            if len(chars) >= REVERSE_MINIMUM and chars[::-1] in other and chars not in other:
                pairs.append({"read": pp.CJK(line), "otherEngine": pp.CJK(line)[::-1]})
        if pairs:
            return engine, pairs
    return None


def title_page(setting: Setting, scan: int, plan: rr.Plan) -> None:
    """S13 on one page (the module's docstring)."""
    reading = setting.reading(scan)
    found = reversed_lines(reading)
    if not found:
        return
    engine, pairs = found
    text = reading.text
    plan.doubts.append(rr.unsettled(
        "research-front-matter", scan, "S13", text, 0, len(text.rstrip()),
        f"engine {engine.upper()} read {len(pairs)} line(s) of this page backwards ({pairs[0]['read']!r}, the "
        f"other engine {pairs[0]['otherEngine']!r}): the page may be printed right to left; it is left as it is",
        pairs=pairs))


def title_pages(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
                facts: rr.BookFacts | None = None, readings: Readings | None = None) -> rr.Plan:
    """The title-page stage over the pages ORDER."""
    facts = facts or rr.book_facts(book, model)
    setting = Setting(book, model, facts, readings or Readings(book, model), list(order), texts)
    plan = rr.Plan()
    for scan in order:
        title_page(setting, scan, plan)
    return plan


# --- titles on a page whose structure pass was refused --------------------------------

# Engine A's block labels of a title.
TITLE_LABELS = frozenset({"paragraph_title", "doc_title"})
# The seal doubts of a page whose structure pass phase 3 refused: its text is
# the fallback's, one engine A block a paragraph, no heading.
REFUSED = frozenset({"structure-rejected", "formatter-rejected"})


def refused_titles(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
                   facts: rr.BookFacts | None = None) -> rr.Plan:
    """S18 over the pages ORDER: on a page whose structure pass phase 3
    refused (REFUSED), a plain line whose characters are all the characters
    of an engine A block labelled a title (TITLE_LABELS), two or more, and
    not the running head, becomes a heading (###; the levels stage then
    gives it its kind and level).  The fallback writes one block a
    paragraph, so every title on such a page came out as body text.
    Measured on the six delivered books (2026-10-13): a historical monograph lost 7 such
    titles on 5 of its 13 refused pages (二、江氏家族, 明清檔案：),
    a multi-volume archival compilation 5 2 on its 2; on a multi-volume archival compilation 4 scan 448
    engine A labels the title plain text, and nothing is written."""
    facts = facts or rr.book_facts(book, model)
    plan = rr.Plan()
    for scan in order:
        if not REFUSED & {str(d.get("kind")) for d in book.pages[scan].record.get("doubts") or []
                          if isinstance(d, Mapping)}:
            continue
        pd = rr.page_data(book, model, scan, texts[scan])
        titles = {pp.CJK(str(b.get("text") or "")) for b in pd.a_blocks if b.get("label") in TITLE_LABELS}
        titles = {t for t in titles if len(t) >= 2}
        if not titles:
            continue
        for line in line_spans(texts[scan]):
            body = line.text.strip()
            if not body or heading_of(line.text) or om.LIST_ITEM_RE.match(body) or om.is_table_row(body):
                continue
            if pp.CJK(body) not in titles or (facts.stems and lint.head_line(lint.line_core(body), facts.stems,
                                                                             facts.suffixes)):
                continue
            plan.edits.append(re_.Edit(scan, line.start, line.start, "### ", rule="S18", evidence={
                "title": body[:40], "label": "engine A title block"}))
    return plan


# --- levels ---------------------------------------------------------------------------

def own_block(reading: PageReading, line: rr.Line) -> tuple[str, dict[str, Any]]:
    """What engine A shows of a heading line: `own` (a block of its own, a
    title block or display type), `run-in` (the start of a longer block), or
    `none` (engine A did not read it)."""
    ks = reading.chars_in(line.start, line.end)
    blocks = [reading.block(k) for k in ks if reading.block(k) is not None]
    core = folded(pp.CJK(heading_body(line.text)))
    if not blocks and core:
        # Nothing aligned (a line the page holds the other way round from
        # engine A's reading, a page S13 names): the block holding its
        # characters either way.
        blocks = [b for b, block in enumerate(reading.pd.a_blocks)
                  if folded(pp.CJK(block["text"])) in (core, core[::-1])][:1]
    if not blocks:
        return "none", {}
    b = blocks[0]
    block = reading.pd.a_blocks[b] if b < len(reading.pd.a_blocks) else {}
    block_core = folded(pp.CJK(block.get("text") or ""))
    evidence = {"label": block.get("label"), "block": block_core[:24]}
    if block.get("label") in rm.TITLE_LABELS:
        return "own", evidence
    if len(block_core) <= len(core) + 2 and difflib.SequenceMatcher(None, core, block_core).ratio() >= TITLE_SIMILARITY:
        return "own", evidence
    return "run-in", evidence


def heading_kinds(setting: Setting, contents: Contents) -> list[dict[str, Any]]:
    """Every heading of the book (marker pages in order) with its kind and
    the level the scheme gives it (None: no heading)."""
    texts = all_texts(setting.book, setting.texts)
    dividers = {d["page"]: d.get("suffix") for d in setting.model.get("dividers") or []}
    first_contents = min(contents.pages)
    unit_level = contents.unit_level
    rows_at = {(r.page, r.line): r for r in contents.rows}
    out = []
    in_unit = False
    seen_heading = False
    for scan in sorted(texts):
        text = texts[scan]
        reading = setting.readings.get(scan, text)
        units = contents.units_on(scan)
        for line in line_spans(text):
            body = line.text.strip()
            if not body:
                continue
            match = heading_of(line.text)
            if not match:
                continue
            core = pp.CJK(heading_body(line.text))
            kind, level, why = "unknown", None, {}
            row = rows_at.get((scan, line.number))
            if scan in contents.pages:
                if row is not None and row.kind == "title":
                    kind, level = "contents-title", 2
                elif row is not None and row.kind == "group":
                    kind, level = "contents-group", unit_level
            elif scan < first_contents:
                show, why = own_block(reading, line)
                if not seen_heading and (why.get("label") in rm.TITLE_LABELS):
                    kind, level = "book-title", 1
            elif scan in dividers and dividers[scan] and rm.levenshtein(core, dividers[scan]) <= 1:
                kind, level = "division", 2
                in_unit = False
            elif line.number in units:
                kind, level = "unit", unit_level
                in_unit = True
                why = {"entry": units[line.number].chars}
            elif is_date_line(line.text):
                kind = "date"
            elif in_unit:
                show, why = own_block(reading, line)
                lead = ENUMERATOR.match(heading_body(line.text).strip())
                words = pp.CJK(heading_body(line.text).strip()[lead.end():] if lead else heading_body(line.text))
                # A heading of its own holds words beyond its enumerator: a
                # numeral alone (in a number block) opens what follows it.
                if show == "own" and words and why.get("label") != "number":
                    kind, level = "in-unit", unit_level + 1
                elif show == "run-in":
                    kind = "run-in"
            seen_heading = True
            out.append({"page": scan, "line": line.number, "text": line.text, "kind": kind, "level": level,
                        "current": len(match.group("marks")), "why": why})
    return out


def levels(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
           facts: rr.BookFacts | None = None, readings: Readings | None = None) -> rr.Plan:
    """The levels stage over the pages ORDER (the module's docstring)."""
    facts = facts or rr.book_facts(book, model)
    setting = Setting(book, model, facts, readings or Readings(book, model), list(order), texts)
    plan = rr.Plan()
    contents = setting.contents()
    if contents is None:
        return plan
    for item in heading_kinds(setting, contents):
        scan = item["page"]
        if scan not in setting.order:
            continue
        reading = setting.reading(scan)
        line = line_spans(reading.text)[item["line"] - 1]
        match = heading_of(line.text)
        kind = item["kind"]
        evidence = {"kind": kind, **item["why"]}
        if kind == "date":
            plan.edits.append(re_.Edit(scan, line.start, line.start + match.end(), "", rule="S5",
                                       evidence=evidence))
            continue
        if item["level"] is None:
            if kind != "run-in":
                plan.doubts.append(("heading-kind-unknown", scan, f"no rule places the heading {line.text[:40]!r}; "
                                    "its level is left as it is", {"line": item["line"], "heading": line.text}))
            continue
        if item["current"] != item["level"]:
            plan.edits.append(re_.Edit(scan, line.start + len(match.group("indent")),
                                       line.start + len(match.group("indent")) + item["current"],
                                       "#" * item["level"], rule="S5", evidence={**evidence,
                                                                                "from": item["current"]}))
    return plan
