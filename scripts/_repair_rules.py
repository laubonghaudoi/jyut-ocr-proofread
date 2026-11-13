"""The book repair tool's mechanical rules (scripts/repair_book.py): per page,
band text and answers at band spots (furniture: F1, F6, F8), engine B's
stand-ins, vertical brackets and the marks of headings (marks: K1, K5, K9,
K11, K-seq), the blank line around a heading (P5), the marks' forms
(forms: K9, K12), and last the script of 裁決's answers (script: V1).

Each rule is a detector over one page that proposes offset edits
(_repair_edits.Edit) with the rule's name and its evidence, and each acts
only on what the book model (_repair_model.py) measured on this book: a rule
whose gate failed stays silent.  A site a rule finds but cannot settle - its
evidence falls short, or two sources disagree - becomes a doubt naming what
is at stake there (unsettled: the kind of the defect counter that counts
such a site), never a guess.

What each rule may delete (the conservation the design asks for): band
text (F1) and an adjudicator's answer at a band spot where neither engine
read body text (F6); a glyph engine B writes for a mark or a figure that
engine A did not read (K1, K5); a duplicate engine A read once (K5); in a
heading, a mark engine A did not read where it read a dash (K1), and a mark
or `<br>` neither engine read, outside a column (K11); emphasis markers
neither engine read (K12).  What each may write: what engine A read at that
place (its figure, dash, dot, quote or character, a heading's space), the
ordinary form of a vertical bracket or of an ASCII mark of Chinese text, a
space where a deleted emphasis run stood in engine A's gap (K12), a blank
line (P5), or 裁決's sealed answer as phase 3 now writes it (V1).

Furniture (stage `furniture`), in the order evaluated:
- F6 answer at a band spot: a phase-3 adjudication (resolved by the
  adjudicator, engine A's default or moved-kept-once) with a context in the
  band, where engine A read nothing, band text or folio figures and engine
  B read nothing, folio figures, its own head span or a run suffix: neither
  engine read body text there, so the answer is not the page's.  Located by
  the body context on its other side.  An answer an engine read too (the
  adjudicator confirmed it) goes only when that reading is the band as a
  whole - a suffix, the whole folio, a whole head - not a piece of the folio
  or one character of the head.  An answer that is a folio figure where
  engine A read a body character, or where an engine read it as only a piece
  of the folio, is a `FURN-ANSWER` doubt, not an edit.
- F1 band text, deleted where engine A did not read it in its body (as a
  line of its own, or most of its characters inside a block with the text
  around it: then it is printed and is kept):
  - a whole line: a running head (at most one stray numeral, a stem copy,
    optionally a run suffix, optionally folio figures), on a page that
    prints one; or the page's own words alone - its run suffix (not on a
    contents page), the contents word on a contents page;
  - at the page's leading and trailing edge (after the lines above are
    gone): a numeral-only line that is the folio or a piece of it - by the
    folio sequence or engine A's folio block - and that an engine read in
    the band; or a line made only of F6 answers and characters of engine
    A's band;
  - inline: what the page holds where engine B read its running head,
    anchored by the body characters B read on either side, when it is only
    head, suffix, folio, misread and F6 characters and holds a stem's core
    (or is the page's own suffix) - where engine B read the head at an end
    of its reading (a stem copy with body it read on both sides is a
    mention or a title; a `FURN-HEAD` doubt when engine A did not read it
    in its body), and, where engine A read the run in its body, as the band
    glued to a text block (with the suffix or folio beside the stem, not in
    a title).
The first contents page and the dividers are exempt from the page's own
words, F1 inline and F6.  Marks left beside a deletion that engine A did
not read are a `FURN-HEAD` doubt.
- F8, on the page as the edits above leave it: one or two characters both
  engines read at the page's head or foot, beyond the first (last)
  character they share with it, that are not the band's, numerals or
  stand-ins, where the page holds none (the two may read them as different
  glyphs) - a body character that went out with an answer at the band - is
  a `TEXT-LOST` doubt at the page's edge, naming both readings.

Marks (stage `marks`), between two characters the page and engine A share
and that are adjacent in engine A, on characters engine A did not read
(stand-ins: engine B's glyphs, M-7):
- K1 the page holds stand-ins, or nothing, where engine A read a figure, a
  dash, a dot, a quote or a character -> engine A's reading:
  - a bracketed figure (d): the page holds at most four stand-ins, a
    `）（`/`（）` pair or nothing there -> `（d）` with engine A's marks (its
    leader as `……`).  Each stand-in
    must stand for what engine A read there (the figure, or a mark, dash or
    dot of its gap); one that does not is a `MARK-FIGURE-LOST` doubt.  Not
    where the page holds a figure before them (anywhere there, where it
    holds neither stand-ins nor a pair): they stand for none (the
    closing bracket of a （d） and a bracket opening what follows, on the
    next line or the same, are no pair).  Where engine A breaks its line
    after the figure and not before it, the figure and engine A's marks
    before that break end the page's line, ahead of its line break;
  - a figure opening one of engine A's lines: the page's heading, item or
    line opens with one to three stand-ins or CJK numerals (or only a stray
    mark) -> the figure (`d`, `d.`, or `（d）` where B read a bracket's
    glyphs round it);
  - a dash: where the page holds stand-ins there -> engine A's dash in
    their place, the page's other marks kept; in a heading, where the page
    holds stand-ins, nothing, marks or a shorter dash -> engine A's dash
    with the page's marks that engine A read too (the contents stage runs
    this again on the unit titles it makes headings).  A `MARK-STANDIN`
    doubt instead where engine A read a stand-in beside the dash or the
    page holds characters that are no stand-ins there; outside a heading
    also where the stand-ins are apart, the page holds marks engine A did
    not read there, or engine A read a figure or a letter there.  Not a
    dash with a figure or a Latin letter on both sides in engine A's text
    (a caption's number such as 1-3, a page range with letters): it
    belongs to a run, the runs stage's to write;
  - one stand-in, with no line break or heading marker beside it, where
    engine A read only marks (no figure or letter) - a dot's
    stand-in where engine A read a dot or the page holds all its marks; a
    quote's stand-in beside a quote or bracket the page holds, or where
    engine A read one the page lacks, when the seal shows engine B alone put
    it there (it may be a character of the text elsewhere) -> the page's
    marks with engine A's (K5 where they already hold all engine A read: the
    stand-in is only deleted); a `MARK-STANDIN` doubt where they would leave
    a quote or bracket unpaired;
  - a stand-in engine A never reads anywhere in the book, where engine A
    read a character at the same aligned place -> engine A's character.
  A stand-in counts as a CJK numeral for engine A's figure only on a book
  where engine B was seen to write numerals for engine A's figures (M-7b).
- K5 a stand-in beside marks engine A's reading already holds -> deleted:
  the one stand-in above where the page's marks hold all engine A read
  there; a mark-alias glyph engine A did not read beside the same glyph
  engine A read, only marks between -> one glyph with engine A's marks (a
  `MARK-STANDIN` doubt when no mark it stands for is beside it, or engine B
  read the glyph more often than once plus those marks: a doubled
  character).
- K9 `︵︶` -> `（）` (an ASCII mark of Chinese text: the forms stage).
  A stand-in glyph no rule settles is left to the MARK-STANDIN counter
  (_book_lint.py), whose hits are doubts too.
- K11 in a heading, between two characters both engines read side by side,
  the marks and `<br>` neither engine read there -> deleted: the
  punctuation pass put a mark where a title's two printed lines meet, or
  inside a title (a speck, or its own reading of the title's sense), and
  the formatter wrote the two lines into one heading with `<br>`.  Where
  engine A read a space on its line there and the page keeps none, one
  space (第三節 自保與教化: the gap is printed, the mark a speck); where it
  read a line break, or the two characters side by side on one line,
  nothing.  Also a mark the heading opens or ends with, where the engines
  read only the line's own marks across the line break.  Where engine A
  read either character in a block it labelled vertical_text (a column,
  which it breaks after every character), the marks stay and the heading
  gets one `heading-mark-unread` doubt naming them: an old vertical book
  prints 。 inside titles, which both engines missed (a vertical speech collection scans
  133, 255 and 440).  Not where either engine read a mark there that the
  page does not keep - any mark of CJK print (printed_marks: 生活·讀書,
  where the page's 、 is the engines' ·, a mark read, of another shape;
  until 2026-10-26 only phase 3's marks counted, and K11 deleted the page's
  、 there, a mark lost where N8 is off) - where the gap holds a figure or a
  letter, nor after a numeral (a date's or an item number's mark:
  廿一、三、廿一、, 一、).  Until
  2026-10-25 a heading engine A read on one line kept its marks with that
  doubt too; measured then (model-off), every such mark on five
  horizontally set books was a punctuation pass's comma or full stop that
  the print does not hold: 10 on one book, each as the second reading of
  it (the user's copy) has it; 13 on three others, 8 confirmed by their
  goldens and 5 on the page images, where the goldens hold a 。 the print
  lacks (a speck, or nothing); 6 on a fifth.  The vertical book's printed
  。 are all in vertical_text blocks (no edit there).
- K-seq: a figure K1 writes must continue its list (the previous figure of
  its kind is one less, or it is 1); one that does not is a
  `MARK-FIGURE-LOST` doubt, not an edit.  Where the page holds the
  figure's value in CJK numerals and nothing else (the print read in the
  other script), 1 and that agreement settle nothing: the figures around
  it or the contents do.

Forms (stage `forms`, after the runs stage: whether a mark stands between
two figures is known only once the runs stage has written the figures
phase 3 dropped; the page keeps a figure's comma without the figure, and a
full-width one there was written into the figure, 7，920, when this ran in
the marks stage: three figures of one page of an archive-citing book,
2026-10-25):
- K9 an ASCII mark of Chinese text the punctuation pass wrote -> its
  full-width form; an HTML tag, $…$ math, a URL and a Markdown link's
  target are not text (NOT_TEXT).  A bracket that pairs on its page line
  goes with its partner: both where a CJK character stands right outside
  either one or anywhere between them (乾隆(1736—1795年)间, 参见(图2)所示,
  甲(1734)乙, 圖像(picture)), neither in Western text (no CJK character
  inside, none right outside: (London: Press, 1962), a list's (1) opening
  a Western line).  Any other mark - `, : ; ? !`, a bracket with no
  partner on its line - where CJK print (a CJK character, a full-width
  mark or an ideographic space) stands beside it, in a gap between two page
  characters that holds no letter outside an HTML tag (Western text keeps
  its ASCII marks); so not one between two figures (14,625; 11:30).
  Until 2026-10-26 the brackets were paired inside one gap alone, so a pair
  whose content opens or closes with a figure or a letter had one bracket
  converted (乾隆(1736—1795年）间), and a bracket round figures alone,
  (1734), stayed ASCII; no rule reads that form alone (the lint, the
  structure and the footnote readers take either width).  The pairing over
  the line made 2 more edits on one book (its (1734), which the user's copy
  writes （1734）) and 22 on an archive-citing book (11 pairs: 同治三年(1864)
  and 米里拜爾 (Miribel, Jean de), which its print sets half width and the
  user's rule writes full width; 圖像(picture); note numbers such as (49)
  where engine A read a superscript ⑨), none on five more (2026-10-26,
  model-off).  Measured (2026-10-25, model-off): the sealed pages of a
  simplified book printed in full-width marks held 126 ASCII marks beside
  CJK print, all written by the punctuation pass (the runs stage writes
  ASCII only inside a figure, 312,000); the engines read the full-width
  form at most of them.  The rule made 80 edits there; the second reading
  of the book (the user's copy) has the full-width form at 74 of the 75
  it was located at, and none at the other (an extra mark of ours).  On
  the three books with goldens, 11, 20 and 2 edits, all right by them (one
  changed only the width of a mark the golden prints as 〕); 18, 3 and 0
  on three more.  The user ruled marks in Chinese text full width
  (2026-10-25).
- K12 an emphasis run (`**`, `__`, or longer) the page holds where neither
  engine holds that character (its ＊ and ＿ as * and _) in the gap (an
  engine not aligned there holds nothing) nor on the lines that hold the
  page's line (engine_lines: those of the characters it aligns to the
  line's, and one that holds the line's characters in a row, a table's
  row read in another order) -> deleted; not a line that is the run alone
  (a Markdown rule).  Where the runs were all the gap held between two
  characters and engine A read white space on its line there, one space,
  as K11 writes (a bold label and its verse, 五祖灯 一盏明灯: five such on
  one page of one book, which the user's copy writes with a space and
  which had been joined, 2026-10-26).  Until 2026-10-26 a printed run the
  engines read in full width (某＊＊＊某, ＿＿＿) or elsewhere on its line
  was deleted too; none on the seven measured books.  The structure pass
  writes Markdown bold round a label or a date head the print sets in bold,
  but the same kind of line elsewhere became a heading or plain text, and
  the user ruled no bold in a delivered book (2026-10-25).  Measured
  (2026-10-25, model-off): the engines wrote no `**` in any of seven books;
  the rule deleted 19 spans (38 runs) on four pages of one book, the
  user's copy plain at each one located, and 2, 1, 1 and 5 spans on four
  others.  A single `*` (a printed star, a note's) stays.

Script (stage `script`, after the forms stage, on the text every stage but
the words has written: an answer at a band spot is gone with the band, F6):
- V1 in a book printed in simplified script (as phase 3 tells it, from
  both engines' drafts), 裁決's answer written as given (resolved_from
  `adjudicator`) that phase 3 now writes otherwise
  (proofread_pages.simplified_as_read: a character only traditional script
  has, which engine B did not read there, in the simplified form an engine
  read there, or in its one simplified form where neither engine read it
  or a form of it) -> so written, where the page still holds the sealed
  answer at the one place the record's context puts it (at_record_spot,
  as K1 and K5 find the seal's record of a stand-in engine B alone read); an
  `answer-script-converted` doubt where engine A's form is written and
  engine B read another character there, or where the form written is one
  neither engine read (proofread_pages.converted_against_b), as phase 3's.
  The form is worked out from the answer as given (answeredTraditional), so
  a page phase 3 sealed so is left as it is (薴, sealed as its one form 苧,
  is not written 苎, 苧's own one form).  Pages sealed before phase 3 wrote
  an answer in the form an engine read get that form too.  Measured
  (2026-10-25, model-off) on the repaired a multi-volume archival compilation 1-5: 25, 17, 15, 15 and 19
  edits, every other edit unchanged; against the goldens of a multi-volume archival compilation 2-4 the
  Han errors fall from 212, 289 and 323 to 202, 279 and 316, 25 edits now
  as the golden has it and none of them broken (16 wrong either way: an
  answer of another character, 塵 where 廑 is printed); on a multi-volume archival compilation 1 the
  user's copy has 9 as written (蒇), 4 are on two plates printed in
  traditional script in that simplified book (no longer as printed), and
  the other 12 are wrong either way or title-page noise, by the user's copy
  and the images.  TEXT-TRADITIONAL (the lint) lists 15 to 18 fewer lines a
  book; the new doubts name 12 to 23 spots (24 on a multi-volume archival compilation 1 since the one
  form neither engine read is doubted where engine B read nothing too,
  2026-10-26).

Spacing (stage `paragraphs`): P5 a blank line before and after an ATX
heading - unless the heading and the next line are one block of engine A's
(a heading printed over two lines).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import bisect
import math
import re
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

import proofread_pages as pp
import _book_lint as lint
import _repair_align as ra
import _repair_edits as re_
import _repair_model as rm


# --- common ----------------------------------------------------------------

@dataclass
class Plan:
    """What a rule family proposes: edits and doubts (kind, page, detail,
    fields)."""
    edits: list[re_.Edit] = field(default_factory=list)
    doubts: list[tuple[str, int | None, str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class Line:
    number: int  # 1-based
    start: int
    end: int     # the line's end, its newline excluded
    text: str


def lines_of(text: str) -> list[Line]:
    out = []
    at = 0
    for number, line in enumerate(text.split("\n"), 1):
        out.append(Line(number, at, at + len(line), line))
        at += len(line) + 1
    return out


NUMERALS = rm.NUMERALS
ZERO_READS = "口"  # a folio's zero, read as a square
QUOTES = frozenset("「」『』")
BRACKET_MARKS = frozenset("（）")
PAIRED = QUOTES | BRACKET_MARKS
NORM = str.maketrans({"(": "（", ")": "）", "︵": "（", "︶": "）", ",": "，", ";": "；", "?": "？", "!": "！",
                      ":": "："})
DOT = "·"
DOT_READS = frozenset("·•・")
DASH_RUN = re.compile("[" + re.escape("".join(sorted(ra.DASH_CHARS))) + "]+")
# A dash with a figure or a Latin letter on both sides (1-3, or a page range
# with letters) is part of a run, the runs stage's to write.
FIGURE_DASH = re.compile(r"(?<=[\dA-Za-z])" + DASH_RUN.pattern + r"(?=[\dA-Za-z])")
# K11: the marks a heading may hold that neither engine read, and a line
# break written inside a heading.
HEADING_MARKS = frozenset("，、。；：！？")
BREAK = re.compile(r"<br\s*/?>")
HEADING_TOKEN = re.compile(BREAK.pattern + "|.", re.S)
# K9: the ASCII marks NORM writes in their full-width form, a letter of
# Western text (Latin, Greek, Cyrillic, full-width Latin: a gap that holds
# one is Western text), and the marks of CJK print in their full-width forms
# (a mark beside one is in CJK print; N8 writes these too).
ASCII_MARKS = frozenset("(),;?!:")
TAG = re.compile(r"</?[A-Za-z][^<>\n]{0,60}>")
LETTER = re.compile("[A-Za-z\u00c0-\u02af\u0370-\u03ff\u0400-\u04ff\u1e00-\u1eff\uff21-\uff3a\uff41-\uff5a]")
CJK_MARKS = frozenset("。，、；：？！（）「」『』《》〈〉“”‘’〔〕［］【】…—·")
# K9: what a page holds that is not its text, whose marks stay as they are:
# an HTML tag, $…$ math, a URL (its brackets aside) and a Markdown link's
# target (the brackets after `]`).
NOT_TEXT = re.compile(r"</?[A-Za-z][^<>\n]{0,60}>|\$[^$\n]{1,200}\$|(?:https?://|www\.)[!-'*-~]+|\]\([^()\n]*\)")
# K12: an emphasis run, Markdown's bold (or longer); an engine's full-width
# star and low line as the ASCII ones.
EMPHASIS = re.compile(r"\*{2,}|_{2,}")
EMPHASIS_FOLD = str.maketrans({"\uff0a": "*", "\uff3f": "_"})


def dash_form(gap: str) -> str:
    """Engine A's marks in a gap as the page writes them: white space out,
    brackets full width, each dash run `——` (two cells or more) or `—`."""
    return DASH_RUN.sub(lambda m: "——" if len(m.group()) >= 2 else "—", marks_of(gap))


def marks_of(text: str) -> str:
    """TEXT's marks: every character but white space, brackets in their
    ordinary full-width form."""
    return "".join(c for c in text.translate(NORM) if not c.isspace())


def printed_marks(text: str) -> list[str]:
    """TEXT's printed marks, in order (brackets in their ordinary form): the
    marks phase 3 counts (pp.PRINTED_MARKS) and those of CJK print
    (CJK_MARKS: · “” 〔〕 too)."""
    return [c for c in text.translate(NORM) if c in pp.PRINTED_MARKS or c in CJK_MARKS]


def subsequence(small: str, big: str) -> bool:
    it = iter(big)
    return all(c in it for c in small)


def common_run(a: str, b: str) -> int:
    """The longest run of characters A and B share."""
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def lcs(a: str, b: str) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


class Doubt(NamedTuple):
    """A doubt a rule proposes (the stage writes it): its kind, page,
    detail and fields."""
    kind: str
    page: int | None
    detail: str
    fields: dict[str, Any]


def unsettled(kind: str, page: int, rule: str, text: str, start: int, end: int, why: str,
              **fields: Any) -> Doubt:
    """A site RULE found on PAGE and cannot settle, TEXT[start:end] (the text
    as the rule saw it), where printed text is at stake: a doubt of KIND
    that says why and shows the site between the characters on either
    side."""
    before = pp.CJK(text[:start])[-8:]
    after = pp.CJK(text[end:])[:8]
    site = text[start:end] if end - start <= 30 else text[start:start + 30] + "…"
    return Doubt(kind, page, f"{rule}: {why} (「…{before}［{site}］{after}…」)",
                 {"rule": rule, "line": text.count("\n", 0, start) + 1, **fields})


@dataclass
class BookFacts:
    """What the rules read of the book model, and the book-wide counts."""
    gates: dict[str, bool]
    stems: list[str]
    suffixes: list[str]
    contents_word: str | None
    contents_first: set[int]
    dividers: set[int]
    misread_chars: set[str]
    aliases: dict[str, dict[str, int]]
    figure: set[str]
    dash: set[str]
    dot: set[str]
    numeral_figures: bool
    a_counts: Counter

    def standins(self) -> set[str]:
        return set(self.aliases) | self.figure | self.dash | self.dot

    def aliases_of(self, marks: Iterable[str]) -> set[str]:
        """The glyphs engine B writes for any of MARKS."""
        wanted = set(marks)
        return {g for g, found in self.aliases.items() if wanted & set(found)}


def book_facts(book: Any, model: Mapping[str, Any]) -> BookFacts:
    gates = {name: bool(g.get("passed")) for name, g in (model.get("gates") or {}).items()}
    s = model.get("standIns") or {}
    contents_first = set()
    word = None
    for run in (model.get("contents") or {}).get("runs") or []:
        if run.get("pages"):
            contents_first.add(min(run["pages"]))
        word = word or run.get("contentsWord")
    misreads = set()
    for found in (model.get("stemMisreads") or {}).values():
        misreads.update(found)
    a_counts: Counter = Counter()
    for scan in sorted(book.pages):
        page = book.pages[scan]
        entry = rm.page_entry(model, scan)
        a_body, _ = rm.body_streams(page, entry.get("band") or [], [])
        a_counts.update(pp.CJK(a_body))
    return BookFacts(
        gates=gates,
        stems=list(model.get("stems") or []) if gates.get("M-2") else [],
        suffixes=sorted({r["suffix"] for r in model.get("suffixRuns") or []}) if gates.get("M-3") else [],
        contents_word=word if gates.get("M-3") and gates.get("M-6") else None,
        contents_first=contents_first,
        dividers={d["page"] for d in model.get("dividers") or []},
        misread_chars=misreads,
        aliases={g: dict(m) for g, m in ((s.get("markAliases") or {}).get("B") or {}).items()},
        figure=set(s.get("figure") or []),
        dash=set(s.get("dash") or []),
        dot=set(s.get("dot") or []),
        numeral_figures=bool(s.get("numeralsAtFigures")),
        a_counts=a_counts,
    )


@dataclass
class PageData:
    """One page as a rule reads it: the text under repair, the model's entry,
    the seal's records and both engines' readings (engine A without its band
    blocks, engine B whole and without its head copies)."""
    scan: int
    text: str
    entry: dict[str, Any]
    record: dict[str, Any]
    a_body: str
    a_blocks: list[dict[str, Any]]
    b_text: str
    b_body: str
    band_text: str
    band_numbers: list[str]


def page_data(book: Any, model: Mapping[str, Any], scan: int, text: str) -> PageData:
    page = book.pages[scan]
    entry = rm.page_entry(model, scan)
    band = entry.get("band") or []
    band_blocks = {b["block"] for b in band}
    a_blocks = [b for b in page.engine_a_blocks() if b.get("index") not in band_blocks]
    a_body, b_body = rm.body_streams(page, band, entry.get("headSpansB") or [])
    numbers = []
    for b in band:
        if b.get("label") == "number":
            value = normal_numerals(b["text"])
            if value:
                numbers.append(value)
    return PageData(scan=scan, text=text, entry=entry, record=page.record, a_body=a_body, a_blocks=a_blocks,
                    b_text=page.engine_b_text(), b_body=b_body,
                    band_text="".join(pp.CJK(b["text"]) for b in band), band_numbers=numbers)


ARABIC_TO_CJK = str.maketrans("0123456789０１２３４５６７８９", "〇一二三四五六七八九〇一二三四五六七八九")


def normal_numerals(text: str) -> str:
    """A folio as read: CJK digits (a zero read as a square or a Latin O is
    〇), Arabic figures in CJK form; empty if anything else is in it."""
    out = []
    for c in text:
        if c.isspace():
            continue
        c = c.replace("O", "〇").replace("o", "〇").replace(ZERO_READS, "〇").translate(ARABIC_TO_CJK)
        c = pp.CJK(c) or c
        if c not in rm.DIGIT_VALUE and c not in rm.UNIT_VALUE:
            return ""
        out.append(c)
    return "".join(out)


def pieces(text: str) -> set[str]:
    return {text[i:j] for i in range(len(text)) for j in range(i + 1, len(text) + 1)}


# --- furniture ---------------------------------------------------------------

@dataclass
class Answer:
    """An adjudicator's answer at a band spot (F6): its text, where it is on
    the page (sealed CJK indices, or None when it is not found once), and
    the record."""
    text: str
    at: int | None
    record: dict[str, Any]
    source: str


class Furniture:
    """The furniture rules on one page."""

    def __init__(self, pd: PageData, facts: BookFacts):
        self.pd = pd
        self.facts = facts
        self.text = pd.text
        self.lines = [line for line in lines_of(pd.text) if line.text.strip()]
        self.sealed = ra.stream(pd.text)
        self.suffix = pd.entry.get("suffix")
        folio = pd.entry.get("folio")
        self.folio_forms = {f.translate(ARABIC_TO_CJK) for f in lint.folio_forms(folio)}
        self.band_numeral_pieces = set().union(*(pieces(n) for n in pd.band_numbers)) if pd.band_numbers else set()
        self.exempt = pd.scan in facts.dividers or pd.scan in facts.contents_first
        # A page that prints a running head shows it: a folio the sequence
        # gives it or an engine read in the band, or its run's suffix read
        # there.  A page with none of them (a title page, a plate) prints no
        # running head, and a stem copy on it is the page's own text.
        self.running = bool(pd.entry.get("folio") or pd.entry.get("folioReadings") or pd.entry.get("suffixRead")
                            or pd.band_numbers)
        self.printed = Counter(lint.line_core(line) for line in pd.a_body.split("\n"))
        self.printed_lines = set(self.printed)
        self.b = ra.stream(pd.b_text)
        self.a = ra.stream(pd.a_body)
        self.to_a = ra.mapping(self.a.chars, self.sealed.chars)
        self.block_of = a_blocks_of(pd) if pd.a_blocks and pp.CJK(pd.a_body) else []
        b_to_a = ra.mapping(self.a.chars, self.b.chars)
        self.head_cores, self.head_numerals = self._b_heads(b_to_a)

    def read_in_body(self, start: int, end: int) -> bool:
        """Engine A read most of the page's characters in START:END in its
        body (outside the band): they are printed there, whether engine A
        read them as a line of their own or inside a block with the text
        around them (a heading engine A joined to its paragraph)."""
        offsets = self.sealed.offsets
        ks = range(bisect.bisect_left(offsets, start), bisect.bisect_left(offsets, end))
        return len(ks) > 0 and 2 * sum(self.to_a[k] is not None for k in ks) > len(ks)

    def glued_head(self, aligned: Sequence[int], allowed: set[str]) -> bool:
        """Whether engine A's reading of a held run (the indices ALIGNED of
        its body stream) is the band glued to one of its body blocks: a block
        of text, not a title, where the run with the band's characters engine
        A read next to it holds the page's suffix or its folio besides the
        stem.  A stem copy engine A read in a title, or bare inside a block's
        text (a mention of the book), is the page's own."""
        if not self.block_of or not aligned:
            return False
        lo, hi = min(aligned), max(aligned)
        block = self.block_of[lo]
        if self.block_of[hi] != block or self.pd.a_blocks[block].get("label") in rm.TITLE_LABELS:
            return False
        chars = self.a.chars
        while lo > 0 and self.block_of[lo - 1] == block and chars[lo - 1] in allowed:
            lo -= 1
        while hi + 1 < len(chars) and self.block_of[hi + 1] == block and chars[hi + 1] in allowed:
            hi += 1
        run = chars[lo:hi + 1]
        numerals = re.findall("[" + NUMERALS + ZERO_READS + "]+", run)
        return any(s in run for s in self.facts.suffixes) or any(self.folio_whole(n) for n in numerals)

    # engine B's head: each stem copy with the suffix and numerals the model
    # read beside it - and, where no suffix follows the stem copy, the
    # page's own suffix read with one character wrong, when engine A's body
    # does not hold those characters there.
    def _b_heads(self, b_to_a: Sequence[int | None]) -> tuple[list[str], set[str]]:
        chars = self.b.chars
        cores, numerals = [], set()
        for span in self.pd.entry.get("headSpansB") or []:
            start, end = span.get("startAll", span["start"]), span.get("endAll", span["end"])
            if not (0 <= start < end <= len(chars)):
                continue
            stop = end
            suffix = self.suffix
            if suffix and len(suffix) >= 2 and not span.get("suffix") and not span.get("suffixAtEnd"):
                window = chars[end:end + len(suffix)]
                if len(window) == len(suffix) and window != suffix \
                        and sum(x == y for x, y in zip(window, suffix)) >= len(suffix) - 1 \
                        and all(b_to_a[j] is None for j in range(end, end + len(suffix))):
                    stop = end + len(suffix)
            lead, middle, trail = rm.strip_folio(chars[start:stop])
            cores.append(middle)
            for run in (span.get("numeralsBefore") or "", span.get("numeralsAfter") or "", lead, trail):
                if run:
                    numerals.update(pieces(normal_numerals(run)))
        return cores, numerals - {""}

    def folio_piece(self, text: str) -> bool:
        value = normal_numerals(text)
        return bool(value) and (value in self.folio_forms or value in self.band_numeral_pieces)

    def folio_whole(self, text: str) -> bool:
        """TEXT is the page's folio as a whole - the value the sequence gives
        the page, or what engine A read in its folio block - not a piece of
        it (a piece, 一 of 一〇, is as likely a numeral of the body)."""
        value = normal_numerals(text)
        if not value:
            return False
        folio = self.pd.entry.get("folio")
        return (folio is not None and rm.numeral_value(value) == folio) or value in self.pd.band_numbers

    def without_suffixes(self, text: str) -> str:
        for suffix in sorted(self.facts.suffixes, key=len, reverse=True):
            text = text.replace(suffix, "")
        return text

    def band_context(self, context: str) -> bool:
        chars = pp.CJK(context)
        if not chars:
            return False
        for stem in self.facts.stems:
            if rm.stem_matches(chars, stem) or common_run(chars, stem) >= math.ceil(rm.STEM_POSITION_SHARE * len(stem)):
                return True
        return bool(self.suffix and self.suffix in chars)

    def a_ok(self, reading: str) -> bool:
        """Engine A read no body text at the spot: nothing, a run suffix,
        text of its band blocks, or folio figures."""
        rest = self.without_suffixes(pp.CJK(reading))
        return not rest or rest in self.pd.band_text or self.folio_piece(rest)

    def b_ok(self, reading: str) -> bool:
        """Engine B read no body text at the spot: nothing, a run suffix,
        folio figures, or text of its running head."""
        rest = self.without_suffixes(pp.CJK(reading))
        return not rest or self.folio_piece(rest) or any(rest in core for core in self.head_cores)

    def band_whole(self, reading: str) -> bool:
        """READING is the band as a whole: nothing but a run suffix, the
        page's folio (whole), or a running head as an engine read it (a band
        block of engine A, or engine B's head with the folio beside it).  A
        character of the head alone, or a piece of the folio (一 of 一〇), is
        as likely a character of the body."""
        rest = self.without_suffixes(pp.CJK(reading))
        if not rest or self.folio_whole(rest):
            return True
        lead, middle, trail = rm.strip_folio(rest)
        heads = {self.without_suffixes(core) for core in self.head_cores}
        heads |= {self.without_suffixes(pp.CJK(b["text"])) for b in self.pd.entry.get("band") or []}
        return bool(middle) and middle in heads and all(not x or self.folio_whole(x) for x in (lead, trail))

    def answers(self) -> tuple[list[Answer], list[Doubt]]:
        """F6's answers on this page, and the doubts for a folio figure
        written where engine A read a body character, or where an engine
        read that figure as only a piece of the folio."""
        records = []
        for a in self.pd.record.get("adjudications") or []:
            if isinstance(a, dict):
                records.append(("adjudication", a.get("context_before"), a.get("draft_reading"),
                                a.get("writer_reading"), a.get("resolved"), a.get("context_after"),
                                a.get("resolved_from"), a))
        for e in self.pd.record.get("engineDisagreements") or []:
            if isinstance(e, dict) and e.get("resolved_by") == "moved-kept-once":
                records.append(("moved-kept-once", e.get("context_before"), e.get("engineA"), e.get("engineB"),
                                e.get("resolved"), e.get("context_after"), "moved-kept-once", e))
        found: list[Answer] = []
        doubts: list[Doubt] = []
        if self.exempt or not self.facts.stems or not self.running:
            return found, doubts
        for source, before, a_read, b_read, resolved, after, how, record in records:
            answer = pp.CJK(str(resolved or ""))
            if not answer or how not in ("adjudicator", "engine-A-default", "moved-kept-once"):
                continue
            band_before, band_after = self.band_context(str(before or "")), self.band_context(str(after or ""))
            if not (band_before or band_after):
                continue
            a_chars, b_chars = pp.CJK(str(a_read or "")), pp.CJK(str(b_read or ""))
            at = self.locate(answer, str(before or ""), str(after or ""), band_before, band_after)
            # An engine that read the answer itself, which the adjudicator
            # confirmed from the image, read band text there only when its
            # reading is the band as a whole; an answer of the adjudicator's
            # own, where the engines read the band's characters or nothing,
            # is not the page's.
            a_fine = self.band_whole(a_chars) if a_chars == answer else self.a_ok(a_chars)
            b_fine = self.band_whole(b_chars) if b_chars == answer else self.b_ok(b_chars)
            if a_fine and b_fine:
                found.append(Answer(answer, at, record, source))
                continue
            if at is None or not self.folio_piece(answer):
                continue
            if a_chars and not self.a_ok(a_chars):
                why = "a folio figure written where engine A read a body character"
            elif answer in (a_chars, b_chars):
                why = "a piece of the folio written where an engine read it, not the folio as a whole"
            else:
                continue
            start = self.sealed.offsets[at]
            end = self.sealed.offsets[at + len(answer) - 1] + 1
            doubts.append(unsettled("FURN-ANSWER", self.pd.scan, "F6", self.text, start, end, why,
                                  engineA=a_chars, engineB=b_chars))
        # One answer per place: an adjudication and a move of the same answer.
        unique: dict[Any, Answer] = {}
        for answer in found:
            key = (answer.text, answer.at) if answer.at is not None else (answer.text, id(answer))
            unique.setdefault(key, answer)
        return list(unique.values()), [d for k, d in enumerate(doubts) if d not in doubts[:k]]

    def locate(self, answer: str, before: str, after: str, band_before: bool, band_after: bool) -> int | None:
        chars = self.sealed.chars
        before, after = pp.CJK(before), pp.CJK(after)
        candidates = []
        if band_before and band_after:
            if chars.startswith(answer):
                candidates.append(0)
            if chars.endswith(answer):
                candidates.append(len(chars) - len(answer))
        elif band_before:
            body = after[:3]
            if body:
                candidates = [m.start() for m in re.finditer(re.escape(answer + body), chars)]
            elif chars.endswith(answer):
                candidates = [len(chars) - len(answer)]
        else:
            body = before[-3:]
            if body:
                candidates = [m.start() + len(body) for m in re.finditer(re.escape(body + answer), chars)]
            elif chars.startswith(answer):
                candidates = [0]
        candidates = sorted(set(candidates))
        return candidates[0] if len(candidates) == 1 else None

    def band_line(self, line: Line, answers: Sequence[Answer], edge: bool) -> str | None:
        """F1 on a whole line: why it is band text, or None.  Anywhere: a
        running head, or the page's own words alone (its run suffix; the
        contents word on a contents page).  At the page's leading or trailing
        EDGE: the folio (or a piece of it) an engine read in the band, or
        answers at band spots with characters of engine A's band.  A line
        engine A read in its body is printed and is kept."""
        core = lint.line_core(line.text)
        if not core:
            return None
        facts = self.facts
        contents = self.pd.entry.get("contents")
        own = {self.suffix if self.suffix in facts.suffixes and not contents else None,
               facts.contents_word if contents else None} - {None}
        if edge:
            value = normal_numerals(core) if lint.numeral_only(core) else ""
            rest = pp.CJK(line.text)
            for text in sorted({a.text for a in answers}, key=len, reverse=True):
                rest = rest.replace(text, "")
            if value and value in (self.folio_forms | self.band_numeral_pieces) \
                    and value in (self.band_numeral_pieces | self.head_numerals):
                why = "the folio (or a piece of it), which an engine read in the band, at the page's edge"
            elif rest != pp.CJK(line.text) and all(c in self.pd.band_text for c in rest):
                why = "answers at band spots and characters of engine A's band, at the page's edge"
            else:
                return None
        elif facts.stems and self.running and lint.head_line(core, facts.stems, facts.suffixes):
            why = "a running head: a stem copy with at most a stray numeral, a run suffix and folio figures"
        elif not self.exempt and core in own:
            why = "the page's own run suffix, or the contents word on a contents page, alone"
        else:
            return None
        if self.printed[core] > 0:
            # Engine A read this line in its body: it is printed there.
            self.printed[core] -= 1
            return None
        # So it is when engine A read it in a block with the text around it.
        return None if self.read_in_body(line.start, line.end) else why

    def plan(self) -> Plan:
        plan = Plan()
        pd = self.pd
        answers, doubts = self.answers()
        plan.doubts.extend(doubts)
        deleted: dict[int, str] = {}
        for line in self.lines:
            found = self.band_line(line, answers, False)
            if found:
                deleted[line.number] = found
        for ordered in (self.lines, list(reversed(self.lines))):
            for line in ordered:
                if line.number in deleted:
                    continue
                found = self.band_line(line, answers, True)
                if not found:
                    break
                deleted[line.number] = found
        spans = self.deletion_spans(set(deleted))
        for line in self.lines:
            if line.number not in deleted:
                continue
            why = deleted[line.number]
            start, end = spans[line.number]
            plan.edits.append(re_.Edit(pd.scan, start, end, "", rule="F1", evidence={
                "deletes": "furniture", "line": line.number, "text": line.text, "why": why,
                "band": [b["text"] for b in pd.entry.get("band") or []], "folio": pd.entry.get("folio"),
                "suffix": self.suffix}))

        def inside_deleted(start: int, end: int) -> bool:
            return any(line.number in deleted and line.start <= start and end <= line.end for line in self.lines)

        inline: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for answer in answers:
            if answer.at is None:
                continue
            start = self.sealed.offsets[answer.at]
            end = self.sealed.offsets[answer.at + len(answer.text) - 1] + 1
            record = answer.record
            inline.append((start, end, "F6", "an answer at a band spot where neither engine read body text", {
                "answer": answer.text, "record": answer.source,
                "engineA": record.get("draft_reading", record.get("engineA")),
                "engineB": record.get("writer_reading", record.get("engineB")),
                "contextBefore": record.get("context_before"), "contextAfter": record.get("context_after")}))
        if not self.exempt:
            inline.extend(self.inline_heads(answers, deleted, plan.doubts))
        kept: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for item in sorted(inline, key=lambda x: (x[0], -(x[1] - x[0]))):
            start, end = item[0], item[1]
            if inside_deleted(start, end):
                continue
            container = next((k for k in kept if k[0] <= start and end <= k[1]), None)
            if container is not None:
                container[4].setdefault("alsoRules", []).append(item[2])
                continue
            kept.append(item)
        for start, end, rule, why, evidence in kept:
            plan.edits.append(re_.Edit(pd.scan, start, end, "", rule=rule, evidence={
                "deletes": "furniture", "text": self.text[start:end], "why": why, **evidence}))
            left = self.marks_beside(start, end, rule)
            if left:
                plan.doubts.append(left)
        return plan

    def deletion_spans(self, deleted: set[int]) -> dict[int, tuple[int, int]]:
        """The span each deleted line takes: the line and one separator
        beside it, so the page keeps one separator between the lines left -
        the stronger (more newlines) of the two around a deleted group."""
        lines = self.lines
        spans: dict[int, tuple[int, int]] = {}
        i = 0
        while i < len(lines):
            if lines[i].number not in deleted:
                i += 1
                continue
            j = i
            while j + 1 < len(lines) and lines[j + 1].number in deleted:
                j += 1
            group = lines[i:j + 1]
            prev = lines[i - 1] if i > 0 else None
            nxt = lines[j + 1] if j + 1 < len(lines) else None
            keep_before = True
            if prev is not None and nxt is not None:
                sep_before = self.text[prev.end:group[0].start].count("\n")
                sep_after = self.text[group[-1].end:nxt.start].count("\n")
                keep_before = sep_before >= sep_after
            elif prev is not None:
                keep_before = False
            for k, line in enumerate(group):
                if keep_before:
                    stop = group[k + 1].start if k + 1 < len(group) else (nxt.start if nxt else line.end)
                    spans[line.number] = (line.start, stop)
                else:
                    begin = group[k - 1].end if k else prev.end
                    spans[line.number] = (begin, line.end)
            i = j + 1
        return spans

    def in_deleted(self, offset: int, deleted: Mapping[int, Any]) -> bool:
        return any(line.number in deleted and line.start <= offset < line.end for line in self.lines)

    def inline_heads(self, answers: Sequence[Answer], deleted: Mapping[int, Any],
                     doubts: list[Doubt]) -> list[tuple[int, int, str, str, dict[str, Any]]]:
        """F1 inline: what the page holds where engine B read its running
        head (the sites it cannot settle go to DOUBTS)."""
        out = []
        if not self.facts.stems or not self.running:
            return out
        chars = self.sealed.chars
        to_b = ra.mapping(self.b.chars, chars)
        b_to_s: dict[int, int] = {j: k for k, j in enumerate(to_b) if j is not None}
        allowed = set("".join(self.facts.stems)) | set("".join(self.facts.suffixes)) | set(NUMERALS) \
            | set(ZERO_READS) | self.facts.misread_chars | set("".join(a.text for a in answers))
        for span in self.pd.entry.get("headSpansB") or []:
            start, end = span.get("startAll", span["start"]), span.get("endAll", span["end"])
            # The body characters engine B read on either side, where the
            # page holds them.
            lo = next((b_to_s[j] for j in range(start - 1, -1, -1) if j in b_to_s), -1)
            hi = next((b_to_s[j] for j in range(end, len(self.b.chars)) if j in b_to_s), len(chars))
            if hi <= lo + 1:
                continue
            # The held run's part in the lines the page keeps: a run that
            # goes on into a line deleted whole is cut at that line; one
            # that crosses between two kept lines is not taken.
            ks = [k for k in range(lo + 1, hi) if not self.in_deleted(self.sealed.offsets[k], deleted)]
            if not ks or ks != list(range(ks[0], ks[-1] + 1)):
                continue
            s = self.sealed.offsets[ks[0]]
            e = self.sealed.offsets[ks[-1]] + 1
            held = chars[ks[0]:ks[-1] + 1]
            if "\n" in self.text[s:e] or not all(c in allowed for c in held):
                continue
            core = any(lcs(held, stem) >= math.ceil(rm.STEM_POSITION_SHARE * len(stem)) for stem in self.facts.stems)
            if not (core or held == self.suffix):
                continue
            # A whole line is band_line's to judge.  Engine A read the run as a line
            # of its body (outside the band): it is printed there, a title
            # that holds the stem.  (Engine A now and then glues the head to
            # the end of the body column beside it; that is no line of its
            # own.)
            line = next(l for l in self.lines if l.start <= s < l.end)
            if pp.CJK(line.text) == held or held in self.printed_lines:
                continue
            aligned = [self.to_a[k] for k in ks if self.to_a[k] is not None]
            in_body = 2 * len(aligned) > len(ks)
            # Engine B reads the band first or last: a stem copy with text of
            # the page it read on both sides is one it read inside the body
            # (a mention of the book, a title that holds it).
            inside = lo >= 0 and hi < len(chars)
            # Engine A read the run in its body: the band glued to a text
            # block carries its suffix or folio beside the stem; a stem copy
            # in a title, or bare in a block's text, is the page's own.
            if inside or (in_body and not self.glued_head(aligned, allowed)):
                if inside and not in_body:
                    doubts.append(unsettled(
                        "FURN-HEAD", self.pd.scan, "F1", self.text, s, e,
                        "engine B read its running head here, inside its reading of the body, and engine A did "
                        "not read these characters in its body: are they printed?", engineB=span.get("read")))
                continue
            out.append((s, e, "F1", "what the page holds where engine B read its running head: only head, "
                                    "suffix, folio and misread characters", {"engineB": span.get("read"),
                                                                              "held": held}))
        return out

    def a_between(self, start: int, end: int) -> str | None:
        """What engine A read between the page's nearest characters it shares
        before START and at or after END (the page's edges where there are
        none); None when the two are not in order in engine A."""
        offsets = self.sealed.offsets
        lo = next((k for k in range(bisect.bisect_left(offsets, start) - 1, -1, -1) if self.to_a[k] is not None), None)
        hi = next((k for k in range(bisect.bisect_left(offsets, end), len(offsets)) if self.to_a[k] is not None), None)
        a_lo = self.a.offsets[self.to_a[lo]] + 1 if lo is not None else 0
        a_hi = self.a.offsets[self.to_a[hi]] if hi is not None else len(self.a.text)
        return self.a.text[a_lo:a_hi] if a_lo <= a_hi else None

    def marks_beside(self, start: int, end: int, rule: str) -> Doubt | None:
        """A doubt about the marks a deletion leaves beside the band: the
        marks the page holds right before or after the span, in its line,
        when engine A read none of them between the characters on either
        side."""
        text = self.text
        s = start
        while s > 0 and text[s - 1] not in "\n" and not pp.CJK(text[s - 1]) and not text[s - 1].isspace():
            s -= 1
        e = end
        while e < len(text) and text[e] not in "\n" and not pp.CJK(text[e]) and not text[e].isspace():
            e += 1
        marks = marks_of(text[s:start] + text[end:e])
        marks = "".join(c for c in marks if not c.isalnum() and c not in "#-*>")
        if not marks:
            return None
        read = self.a_between(start, end)
        if read is not None and subsequence(marks, marks_of(read)):
            return None
        return unsettled("FURN-HEAD", self.pd.scan, rule, text, s, e,
                         f"marks {marks!r} left beside the furniture deleted here ({text[start:end]!r}), which "
                         "engine A did not read: they may be the band's", marks=marks, engineA=read)


def furniture(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
              facts: BookFacts | None = None) -> Plan:
    """The furniture rules over the pages ORDER (their texts TEXTS)."""
    facts = facts or book_facts(book, model)
    plan = Plan()
    if not facts.gates.get("M-1"):
        return plan
    for scan in order:
        page_plan = Furniture(page_data(book, model, scan, texts[scan]), facts).plan()
        plan.edits.extend(page_plan.edits)
        plan.doubts.extend(page_plan.doubts)
        # F8 on the page as its furniture edits leave it (where the stage
        # applies them): its first and last characters are the body's.
        local = re_.Ledger({scan: texts[scan]})
        local.apply("furniture", page_plan.edits)
        plan.doubts.extend(lost_at_edges(page_data(book, model, scan, local.texts[scan]), facts))
    return plan


# F8: at most this many characters both engines read at a page's edge that
# the page lacks (a character, or two, that went out with the band).
LOST_EDGE = 2


def edge_run(chars: str, outward: Sequence[int], skip: set[str]) -> tuple[str, list[int]]:
    """The characters of CHARS at the indices OUTWARD (from the page's
    shared text out to its edge) that are not in SKIP: the first run of
    them, past any SKIP characters beside the shared text (engine B's
    stand-ins for the marks there), up to the next SKIP character (the
    band's, a numeral); and their indices."""
    out: list[int] = []
    for i in outward:
        if chars[i] in skip:
            if out:
                break
            continue
        out.append(i)
    return "".join(chars[i] for i in out), out


def lost_at_edges(pd: PageData, facts: BookFacts) -> list[Doubt]:
    """F8: the body's first or last character(s) that both engines read, one
    or two, where the page holds none - beyond the page's first (last)
    character, which both engines share with it, each engine read a run of
    as many characters that are not the band's (its blocks, the stems, the
    run suffixes), numerals or the book's stand-ins; the two may read them
    differently (a damaged glyph).  On a real book the adjudication at a
    folio spot answered the folio and dropped the body's first character
    with it, and one at the foot answered that no glyph could settle it:
    the character was lost and nothing said of it.  A `TEXT-LOST` doubt at
    the page's edge, naming both engines' readings of the run, at the place
    it would go: before the first character (after the last line's marks at
    the foot)."""
    text = pd.text
    s, a, b = ra.stream(text), ra.stream(pd.a_body), ra.stream(pd.b_body)
    if not s.chars or not a.chars or not b.chars:
        return []
    to_a, to_b = ra.mapping(a.chars, s.chars), ra.mapping(b.chars, s.chars)
    skip = (set(pd.band_text) | set("".join(facts.stems)) | set("".join(facts.suffixes)) | facts.standins()
            | set(NUMERALS) | set(ZERO_READS) | set(rm.UNIT_VALUE))
    out = []
    for edge in ("head", "foot"):
        k = 0 if edge == "head" else len(s.chars) - 1
        ja, jb = to_a[k], to_b[k]
        if ja is None or jb is None:
            continue
        if edge == "head":
            a_run, a_at = edge_run(a.chars, range(ja - 1, -1, -1), skip)
            b_run, _ = edge_run(b.chars, range(jb - 1, -1, -1), skip)
            a_run, b_run = a_run[::-1], b_run[::-1]
        else:
            a_run, a_at = edge_run(a.chars, range(ja + 1, len(a.chars)), skip)
            b_run, _ = edge_run(b.chars, range(jb + 1, len(b.chars)), skip)
        if not (1 <= len(a_run) <= LOST_EDGE and len(a_run) == len(b_run)):
            continue
        # Where the run goes among the page's marks beside its edge
        # character: as engine A reads the marks between the two.
        line_start = text.rfind("\n", 0, s.offsets[0]) + 1
        if edge == "head":
            between = marks_of(a.text[a.offsets[max(a_at)] + 1:a.offsets[ja]])
            held = text[line_start:s.offsets[0]]
            lead = re.match(r"[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+)?", held).end()
            at = s.offsets[0]
            while at > line_start + lead and between and marks_of(text[at - 1]) == between[-1:]:
                at -= 1
                between = between[:-1]
        else:
            between = marks_of(a.text[a.offsets[ja] + 1:a.offsets[min(a_at)]])
            end = len(text.rstrip())
            at = s.offsets[-1] + 1
            while at < end and between and marks_of(text[at]) == between[:1]:
                at += 1
                between = between[1:]
        out.append(unsettled("TEXT-LOST", pd.scan, "F8", text, at, at,
                             f"both engines read {a_run!r} / {b_run!r} at the page's {edge}, beyond the first "
                             f"character they share with it, where the page holds none", engineA=a_run,
                             engineB=b_run, edge=edge))
    return out


# --- marks -------------------------------------------------------------------

# A structure token of a span: line breaks, and a heading or list marker
# opening a line.
STRUCTURE = re.compile(r"(?:\n+|^)(?:[ \t]*(?:#{1,6}|[-*+])[ \t]+)|\n+")
FIGURE_GAP = re.compile(r"(?P<pre>[^（0-9０-９]*)(?P<fig>（[0-9０-９]{1,3}）)(?P<post>[^0-9０-９]*)")
# What an engine writes about a figure that is not print: LaTeX wrappers and
# commands, and note markers (circled or superscript figures).  Measured on a
# modern book (2026-10-01): engine A wrote `」。 $ ^{㉑}(2) $` for a footnote's
# number and an item's, and K1 copied the wrapper into the page.
SUPERSCRIPT_FIGURE = re.compile(r"\^\{[^{}]*[（(][0-9０-９]{1,3}[）)][^{}]*\}")
ENGINE_MARKUP = re.compile(r"\$|\\[A-Za-z]+|[\^{}]|[\u2460-\u24ff\u3251-\u325f\u32b1-\u32bf\u2070-\u2079\u00b9\u00b2\u00b3]")
LINE_FIGURE_GAP = re.compile(r"(?P<pre>[^0-9０-９]*?)(?P<d>[0-9０-９]{1,3})(?P<dot>[.．])?")
LINE_FIGURE_END = re.compile(r"\n\s*[0-9０-９]{1,3}\s*[.．]?\s*$")


def split_structure(span: str) -> list[tuple[str, str]]:
    """SPAN as tokens: ("c", a character) or ("s", a structure run)."""
    out: list[tuple[str, str]] = []
    at = 0
    for match in STRUCTURE.finditer(span):
        if match.end() == match.start():
            continue
        out.extend(("c", c) for c in span[at:match.start()])
        out.append(("s", match.group()))
        at = match.end()
    out.extend(("c", c) for c in span[at:])
    return out


def with_structure(tokens: Sequence[tuple[str, str]], before: str, after: str) -> str:
    """BEFORE ahead of the span's structure, AFTER behind its last heading or
    list marker (the line's own text starts there)."""
    structures = [t for kind, t in tokens if kind == "s"]
    if not structures:
        return before + after
    last = max((i for i, t in enumerate(structures) if re.search(r"[#*+-]", t)), default=len(structures) - 1)
    return before + "".join(structures[:last + 1]) + after + "".join(structures[last + 1:])


def figure_value(text: str) -> int:
    return int(text.translate(ra.FULLWIDTH_DIGITS))


class Marks:
    """The mark rules on one page (the text the stage starts from)."""

    def __init__(self, pd: PageData, facts: BookFacts):
        self.pd = pd
        self.facts = facts
        self.text = pd.text
        self.s = ra.stream(pd.text)
        self.a = ra.stream(pd.a_body)
        self.b = ra.stream(pd.b_body)
        self.to_a = ra.mapping(self.a.chars, self.s.chars)
        self.to_b = ra.mapping(self.b.chars, self.s.chars)
        self.block_of = a_blocks_of(pd) if pd.a_blocks and pp.CJK(pd.a_body) else []
        # K11's marks it leaves, by the heading's line: (start, end, marks).
        self.unread: dict[int, list[tuple[int, int, str]]] = {}
        # A CJK numeral stands for engine A's figure only on a book where
        # engine B was seen to write numerals for engine A's figures (M-7b's
        # numeralsAtFigures): elsewhere which script the page prints is a
        # reading.
        self.standins = facts.standins() | (set(NUMERALS) if facts.numeral_figures else set())
        self.open_aliases = facts.aliases_of("（")
        self.close_aliases = facts.aliases_of("）")
        self.quote_aliases = facts.aliases_of(PAIRED)

    def span(self, k1: int, k2: int) -> tuple[int, int]:
        start = 0 if k1 < 0 else self.s.offsets[k1] + 1
        end = self.s.offsets[k2] if k2 < len(self.s.offsets) else len(self.text)
        return start, end

    def b_between(self, k1: int, k2: int) -> str | None:
        if k1 < 0:
            return None
        return ra.engine_between(self.b, self.to_b, k1, k2)

    def figure_glyphs(self, k1: int, k2: int) -> set[str]:
        """The glyphs engine B read for a figure between two sealed
        characters: those between a bracket's glyphs (an opening one before,
        and a closing one after or nothing), after the mark glyphs before."""
        read = self.b_between(k1, k2)
        if not read:
            return set()
        glyphs = pp.CJK(read)
        at = 0
        while at < len(glyphs) and glyphs[at] in self.facts.aliases and glyphs[at] not in self.open_aliases:
            at += 1
        if at >= len(glyphs) or glyphs[at] not in self.open_aliases:
            return set()
        inner = glyphs[at + 1:]
        if inner and inner[-1] in self.close_aliases:
            inner = inner[:-1]
        return set(inner) if 1 <= len(inner) <= 2 else set()

    def accounted(self, glyph: str, gap: str, figure_glyphs: set[str]) -> bool:
        """Whether GLYPH, a character engine A did not read where it read
        GAP, is engine B's stand-in for something engine A read there: a
        glyph B read for the figure, a figure stand-in or a numeral, or a
        stand-in for one of the marks, dashes or dots in GAP - a mark engine
        B was seen to write it for at as many sites as phase 3 needs to call
        it an alias (once is chance, such as this very site).  A stand-in of
        the book for a mark engine A did not read there (often a character
        of the text too) is not."""
        if glyph in NUMERALS:
            return self.facts.numeral_figures
        if glyph in figure_glyphs or glyph in self.facts.figure:
            return True
        if any(n >= pp.MARK_ALIAS_MINIMUM and mark in gap for mark, n in self.facts.aliases.get(glyph, {}).items()):
            return True
        if glyph in self.facts.dash and any(c in ra.DASH_CHARS for c in gap):
            return True
        return glyph in self.facts.dot and any(c in DOT_READS for c in gap)

    def gaps(self) -> Iterable[tuple[int, int, int, int, str, list[str], str]]:
        """Each place between two characters the page shares with engine A
        that are adjacent in engine A (and the page's start): the two, the
        span between them, the characters there engine A did not read, and
        what engine A read there."""
        shared = [k for k, j in enumerate(self.to_a) if j is not None]
        for k1, k2 in ([(-1, shared[0])] if shared else []) + list(zip(shared, shared[1:])):
            ia = self.to_a[k2]
            start, end = self.span(k1, k2)
            span, gap_raw = self.text[start:end], self.a.gaps[ia]
            if (k1 < 0 or ia == self.to_a[k1] + 1) and (span.strip() or marks_of(gap_raw)):
                yield k1, k2, start, end, span, list(self.s.chars[k1 + 1:k2]), gap_raw

    def line_of(self, k: int) -> str:
        """The line of the page that holds its character K."""
        at = self.s.offsets[k]
        end = self.text.find("\n", at)
        return self.text[self.text.rfind("\n", 0, at) + 1:end if end >= 0 else len(self.text)]

    def plan(self) -> Plan:
        plan = Plan()
        text = self.text
        proposals: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for k1, k2, start, end, span, non, gap_raw in self.gaps():
            gap = marks_of(gap_raw)
            found = (self.bracket_figure(k1, k2, span, non, gap, gap_raw)
                     or self.line_figure(k1, k2, span, non, gap, gap_raw)
                     or self.dash(k1, k2, span, non, gap_raw) or self.lone(k1, k2, span, non, gap)
                     or self.heading_marks(k1, k2, span, non, gap_raw))
            if isinstance(found, Doubt):
                plan.doubts.append(found)
                continue
            if found and found[0] != span:
                new, rule, evidence = found
                proposals.append((start, end, new, rule, {"engineA": gap_raw, **evidence}))
        for sites in self.unread.values():
            plan.doubts.append(unsettled(
                "heading-mark-unread", self.pd.scan, "K11", text, sites[0][0], sites[-1][1],
                "marks in a heading neither engine read, left: engine A read a column there, so the print may "
                "hold them", marks="".join(m for _, _, m in sites)))
        twins, doubts = self.twins()
        proposals.extend(twins)
        plan.doubts.extend(doubts)
        proposals.extend(self.characters())
        proposals.extend((m.start(), m.end(), m.group().translate(NORM), "K9", {})
                         for m in re.finditer(r"[︵︶]", text))
        taken: list[tuple[int, int]] = []
        for start, end, new, rule, evidence in sorted(proposals, key=lambda p: (p[0], p[1])):
            if any(re_.overlaps((start, end), t) for t in taken):
                continue
            taken.append((start, end))
            plan.edits.append(re_.Edit(self.pd.scan, start, end, new, rule=rule, evidence={
                "before": text[start:end], **evidence}))
        return plan

    # K1: a bracketed figure engine A read.
    def bracket_figure(self, k1: int, k2: int, span: str, non: list[str], gap: str, gap_raw: str) -> Any:
        # Engine A's leader before the figure as the page writes it (「……」,
        # pp.LEADER): its ASCII dots ("..... (141)") were written after the
        # page's own leader (「…….....（141）」, a contents page of an
        # archive volume, 2026-10-10).
        gap = pp.LEADER_READ.sub(pp.LEADER, gap)
        match = FIGURE_GAP.fullmatch(gap)
        tokens = split_structure(span)
        content = "".join(t for kind, t in tokens if kind == "c")
        # The page may hold nothing there: phase 3 with --keep-arabic-numerals
        # writes no bracket pair alone (pp.unpaired_brackets), where it once
        # wrote 「）（」 for a bracketed figure the merge did not keep (and
        # still does without the switch).
        if not match or len(non) > 4:
            return None
        if SUPERSCRIPT_FIGURE.search(gap):
            # A figure engine A wrote inside a superscript: a note's number,
            # not an item's.
            return None
        first = min([content.index(c) for c in non if c in content]
                    + [content.index(x) for x in ("）（", "（）") if x in content] + [len(content)])
        if re.search(r"[0-9０-９]", content[:first]):
            # The page holds a figure before its stand-ins or its pair: they
            # stand for no figure engine A read, and writing engine A's would
            # print it twice.  Measured on an archive volume (2026-10-07): a
            # contents line ending （203） over a line opening with （, the two
            # brackets taken for engine B's ）（ once the line break between
            # them was set aside.
            return None
        figure_glyphs = self.figure_glyphs(k1, k2)
        strange = [c for c in non if not self.accounted(c, gap, figure_glyphs)]
        if strange:
            start, end = self.span(k1, k2)
            return unsettled("MARK-FIGURE-LOST", self.pd.scan, "K1", self.text, start, end,
                            "engine A read a bracketed figure here, and the page holds characters that are not "
                            "engine B's stand-ins for what engine A read there", engineA=gap,
                            characters="".join(strange))
        figure = match["fig"].translate(ra.FULLWIDTH_DIGITS)
        lead = content[:first].replace("（", "")
        pre = ENGINE_MARKUP.sub("", match["pre"])
        post = ENGINE_MARKUP.sub("", match["post"])
        if re.search(r"[^\W\d_]", pre + post):
            # Letters beside the figure (Latin, kana, any script): text one
            # engine read, which this rule does not write.
            return None
        # The page's marks before the figure are engine A's already when they
        # end the lead once a note marker the page holds after them is set
        # aside.  Measured on a modern book (2026-10-02): comparing with the
        # marker in place wrote engine A's marks a second time.
        add = pre if not ENGINE_MARKUP.sub("", lead).endswith(pre) else ""
        # Where engine A breaks its line after the figure and not before it,
        # the figure ends the page's line: it goes ahead of the page's line
        # break, with engine A's marks before that break.  Seen on an archive
        # volume's contents (2026-10-07): page numbers that end their lines
        # in print opened the next line, a heading's among them.
        digits = re.search(r"[0-9０-９]+", gap_raw)
        brk = gap_raw.find("\n", digits.end())
        if brk < 0 or "\n" in gap_raw[:digits.start()]:
            new = with_structure(tokens, lead + add, figure + post)
        else:
            same = pp.LEADER_READ.sub(pp.LEADER, marks_of(gap_raw[:brk]))[match.start("post"):]
            new = with_structure(tokens, lead + add + figure + ENGINE_MARKUP.sub("", same),
                                 ENGINE_MARKUP.sub("", match["post"][len(same):]))
        return new, "K1", {"figure": figure_value(figure[1:-1]), "style": "bracket", "standIns": "".join(non),
                           "aIndex": self.to_a[k2]}

    # K1: a figure opening one of engine A's lines.
    def line_figure(self, k1: int, k2: int, span: str, non: list[str], gap: str, gap_raw: str) -> Any:
        match = LINE_FIGURE_GAP.fullmatch(gap)
        # The figure opens one of engine A's lines: after a line break in its
        # gap, or at the start of its text.
        if not match or not (LINE_FIGURE_END.search(gap_raw) or self.to_a[k2] == 0):
            return None
        tokens = split_structure(span)
        marker = max((i for i, (kind, t) in enumerate(tokens) if kind == "s" and re.search(r"[#*+-]", t)),
                     default=None)
        plain = False
        if k1 < 0 and marker is None and tokens and tokens[0][0] == "c":
            marker = -1
        if marker is None:
            marker = max((i for i, (kind, _) in enumerate(tokens) if kind == "s"), default=None)
            plain = True
        if marker is None:
            return None
        head = "".join(t for kind, t in tokens[:max(marker, 0)] if kind == "c")
        tail = "".join(t for kind, t in tokens[marker + 1:] if kind == "c").strip()
        tail_cjk = [c for c in tail if pp.CJK(c)]
        digits = re.findall(r"[0-9０-９]+", tail)
        d = match["d"].translate(ra.FULLWIDTH_DIGITS)
        if any(pp.CJK(c) for c in head) or (digits and [x.translate(ra.FULLWIDTH_DIGITS) for x in digits] != [d]):
            return None
        stand = bool(tail_cjk) and len(tail_cjk) <= 3 and all(c in self.standins for c in tail_cjk)
        stray = not plain and not tail_cjk and not digits and bool(re.fullmatch(r"[（，。、]+", tail or "x"))
        if not (stand or stray):
            return None
        bracketed = len(tail_cjk) >= 3 and tail_cjk[0] in self.open_aliases and tail_cjk[-1] in self.close_aliases
        structures = [t for kind, t in tokens if kind == "s"]
        last = max((i for i, t in enumerate(structures) if re.search(r"[#*+-]", t)), default=len(structures) - 1)
        if bracketed:
            figure = f"（{d}）"
        elif match["dot"]:
            figure = d + "."
        else:
            # A figure without a dot, run into the title after it on its
            # line: a space between them (the number and its title).
            follows = self.text[self.span(k1, k2)[1]:self.span(k1, k2)[1] + 1]
            runs_on = not structures[last + 1:] and follows and not follows.isspace()
            figure = d + (" " if runs_on else "")
        # The page's marks before the line break are kept (engine A's that
        # are missing are added), as K1 keeps them before its figure.
        lead = "".join(c for c in head if not c.isspace())
        pre = match["pre"].strip()
        new = with_structure(tokens, lead + (pre if not lead.endswith(pre) else ""), figure)
        heading = bool(structures[last:last + 1]) and "#" in structures[last] if structures else False
        return new, "K1", {
            "figure": int(d), "style": "bracket" if bracketed else ("heading" if heading else "line"),
            "dot": bool(match["dot"]), "standIns": "".join(tail_cjk), "aIndex": self.to_a[k2]}

    # K1: a dash engine A read where the page holds stand-ins, or (in a
    # heading) nothing, marks or a shorter dash.  A dash with a figure or a
    # letter on both sides in engine A's text (a caption's number, a page
    # range) is no dash: it belongs to a run, the runs stage's to write.
    def dash(self, k1: int, k2: int, span: str, non: list[str], gap_raw: str) -> Any:
        a_gap = FIGURE_DASH.sub("", gap_raw)
        if not DASH_RUN.search(a_gap):
            return None
        heading = k1 >= 0 and "\n" not in span and lint.HEADING.match(self.line_of(k2))
        if not non and (not heading or max((len(m.group()) for m in DASH_RUN.finditer(span)), default=0)
                        >= max(len(m.group()) for m in DASH_RUN.finditer(a_gap))):
            return None
        chars, standins, glyphs, read = self.s.chars, self.facts.standins(), "".join(non), marks_of(a_gap)
        beside = "".join(chars[k] for k in (k1, k2) if k >= 0 and chars[k] in standins)
        if beside or any(c not in standins for c in non) or not heading and (
                glyphs not in span or any(c.isalnum() for c in read)
                or not subsequence(marks_of(span.replace(glyphs, "", 1)), DASH_RUN.sub("", read))):
            # A stand-in engine A read beside the dash (the page's may be the
            # dash itself, and writing it would print it twice), characters
            # that are no stand-ins; outside a heading also stand-ins apart,
            # marks engine A did not read, or a figure or letter engine A
            # read there: a reading.
            start, end = self.span(k1, k2)
            return unsettled("MARK-STANDIN", self.pd.scan, "K1", self.text, start, end,
                             "engine A read a dash here, and the page holds what engine A did not read, or a "
                             "stand-in beside it", engineA=gap_raw.strip(), engineB=self.b_between(k1, k2),
                             characters=glyphs + beside)
        if heading:
            # Engine A's dash with the page's marks there that engine A read
            # too.
            page = marks_of(span)
            return "".join(c for c in dash_form(a_gap) if c == "—" or c in page), "K1", {"standIns": glyphs}
        # Engine A's dash where the stand-ins stand; the page's other marks
        # stay where they are.
        return span.replace(glyphs, DASH_RUN.search(dash_form(a_gap)).group(), 1), "K1", {"standIns": glyphs}

    # One stand-in where engine A read only marks, on one line: a dot's
    # stand-in where engine A read a dot or the page holds all engine A's
    # marks; a quote's stand-in beside a quote or bracket the page holds, or
    # where engine A read one the page lacks, when the seal shows engine B
    # alone put it there (it may be a character of the text elsewhere).
    # Where the page's other marks hold all engine A read it is deleted
    # (K5); else the page's marks with engine A's are written (K1).
    def lone(self, k1: int, k2: int, span: str, non: list[str], gap: str) -> Any:
        if len(non) != 1 or any(kind == "s" for kind, _ in split_structure(span)) or any(c.isalnum() for c in gap):
            return None
        glyph, chars = non[0], self.s.chars
        if (k1 >= 0 and chars[k1] == glyph) or chars[k2] == glyph:
            return None
        dots = str.maketrans({c: DOT for c in DOT_READS})
        rest, read = marks_of(span.replace(glyph, "", 1)).translate(dots), gap.translate(dots)
        new = read if read and subsequence(rest, read) else rest + "".join(c for c in read if c not in rest)
        drop = bool(read) and subsequence(read, rest)
        beside = re.search(f"{re.escape(glyph)}[「」『』（）]|[「」『』（）]{re.escape(glyph)}", marks_of(span))
        lacks = subsequence(rest, read) and any(c in PAIRED and c not in rest for c in read)
        record = None
        if glyph in self.facts.dot:
            if not ((drop and rest) or DOT in read):
                return None
        elif glyph not in self.quote_aliases or not (beside or lacks):
            return None
        else:
            record = self.b_only_record(k1 + 1, glyph)
            if record is None:
                return None
        start, end = self.span(k1, k2)
        if self.leaves_unpaired(start, end, new):
            # Engine A's quote there leaves a quote or bracket of the page
            # without its partner: the other one is not where engine A has
            # it, which is a reading of the page.
            return unsettled("MARK-STANDIN", self.pd.scan, "K1", self.text, start, end,
                             "engine A's marks here would leave a quote or bracket unpaired", engineA=gap)
        return new, "K5" if drop else "K1", {"standIns": glyph, "seal": record}

    # K11: in a heading, marks and <br> neither engine read: deleted, but
    # left with a doubt in a column.
    def heading_marks(self, k1: int, k2: int, span: str, non: list[str], gap_raw: str) -> Any:
        if k1 < 0 or non or self.to_b[k1] is None or self.to_b[k2] != self.to_b[k1] + 1:
            return None
        if self.s.chars[k1] in NUMERALS:
            # A mark after a numeral is a date's or an item number's
            # (廿一、三、廿一、 printed down a column, which engine A read a
            # character a line, without its marks; 一、).
            return None
        start, _ = self.span(k1, k2)
        kept, dropped, sites = [], [], []
        for token in HEADING_TOKEN.finditer(span):
            at = start + token.start()
            line = self.text[self.text.rfind("\n", 0, at) + 1:at + 1]
            if (token.group() in HEADING_MARKS or BREAK.fullmatch(token.group())) and lint.HEADING.match(line):
                dropped.append(token.group())
                sites.append((at, at + len(token.group())))
            else:
                kept.append(token.group())
        rest = "".join(kept)
        b_read = self.b_between(k1, k2) or ""
        if not dropped or any(c.isalnum() for c in BREAK.sub("", rest)):
            return None
        # The engines read the marks the page keeps there (the line's own
        # before or after a heading's edge) and no other mark of CJK print
        # (生活·讀書: the page's 、 is the engines' ·, a mark read).
        if printed_marks(gap_raw) != printed_marks(rest) or printed_marks(b_read) != printed_marks(rest):
            return None
        if any(self.block_of and self.pd.a_blocks[self.block_of[self.to_a[k]]].get("label") == "vertical_text"
               for k in (k1, k2)):
            # Not in a column (engine A breaks after every character there,
            # so its reading does not say where the title's lines meet): the
            # print may hold the marks (a vertical speech collection prints 。 inside titles,
            # which both engines missed); they stay, and one doubt per heading
            # names them.  Elsewhere both engines reading the two characters
            # side by side with no mark is the print's (a title engine A read
            # on one line: every such mark measured was the punctuation
            # pass's, 2026-10-25).
            line = self.text.count("\n", 0, sites[0][0])
            self.unread.setdefault(line, []).append((sites[0][0], sites[-1][1], "".join(dropped)))
            return None
        if not rest and gap_raw.isspace() and "\n" not in gap_raw:
            # Engine A read a space on its line: the print's gap (第三節
            # 自保與教化), which the mark stood in.
            rest = " "
        return rest, "K11", {"engineB": b_read, "dropped": "".join(dropped)}

    def leaves_unpaired(self, start: int, end: int, new: str) -> bool:
        """Whether writing NEW over the page's START:END leaves a closing
        quote or bracket with nothing open before it, or an opening one
        that nothing closes."""
        after = self.text[:start] + new + self.text[end:]
        for opener, closer in lint.BRACKETS.items():
            before_state, after_state = pair_state(self.text, opener, closer), pair_state(after, opener, closer)
            if after_state[1] > before_state[1] or after_state[0] > before_state[0]:
                return True
        return False

    def b_only_record(self, k: int, glyph: str) -> dict[str, Any] | None:
        """The seal's record of the decision that wrote GLYPH at sealed
        character K from engine B alone: a question engine A read nothing
        for (a numeral question, or engine A's default with A empty)."""
        chars = self.s.chars
        for record in self.pd.record.get("adjudications") or []:
            if not isinstance(record, dict) or pp.CJK(str(record.get("resolved") or "")) != glyph:
                continue
            if pp.CJK(str(record.get("draft_reading") or "")):
                continue
            if record.get("trigger") != "numeral-disagreement" and record.get("resolved_from") != "engine-A-default":
                continue
            before = pp.CJK(str(record.get("context_before") or ""))
            after = pp.CJK(str(record.get("context_after") or ""))
            if at_record_spot(chars, k, k + 1, before, after):
                return {"crop": record.get("crop"), "engineB": record.get("writer_reading"),
                        "trigger": record.get("trigger"), "resolvedFrom": record.get("resolved_from")}
        return None

    # K5: a glyph twice where engine A read it once.
    def twins(self) -> tuple[list[tuple[int, int, str, str, dict[str, Any]]], list[Doubt]]:
        out = []
        doubts: list[Doubt] = []
        s, text = self.s, self.text
        done = set()
        for k in range(len(s.chars)):
            if self.to_a[k] is not None or s.chars[k] not in self.facts.aliases or k in done:
                continue
            for t in (k - 1, k + 1):
                if not (0 <= t < len(s.chars)) or self.to_a[t] is None or s.chars[t] != s.chars[k]:
                    continue
                lo, hi = min(k, t), max(k, t)
                if lo == 0 or hi + 1 >= len(s.chars):
                    break
                start, end = s.offsets[lo - 1] + 1, s.offsets[hi + 1]
                segment = text[start:end]
                ia = self.to_a[t]
                raw_before, after = self.a.gaps[ia], marks_of(self.a.gaps[ia + 1])
                sealed_marks = marks_of(segment.replace(s.chars[k], ""))
                # The second copy is engine B's glyph for a mark engine A
                # read beside the first: without such a mark there, which
                # copy is printed is a reading.
                if not set(self.facts.aliases[s.chars[k]]) & set(marks_of(raw_before) + after):
                    doubts.append(unsettled("MARK-STANDIN", self.pd.scan, "K5", text, start, end,
                                        "a glyph twice where engine A read it once, and none of the marks it "
                                        "stands for beside it", engineA=raw_before + s.chars[t]))
                    done.add(k)
                    break
                # Engine B wrote the glyph once for each mark it stands for
                # and once for the character: more copies than that are
                # characters it read (a doubled character engine A read
                # once), which copy is printed is a reading.
                read = self.b_between(lo - 1, hi + 1)
                stands = sum(c in self.facts.aliases[s.chars[k]] for c in marks_of(raw_before) + after)
                if read is None or pp.CJK(read).count(s.chars[k]) > 1 + stands:
                    doubts.append(unsettled("MARK-STANDIN", self.pd.scan, "K5", text, start, end,
                                        "a glyph twice where engine A read it once, and engine B read it more "
                                        "often than the marks beside it account for", engineA=raw_before + s.chars[t],
                                        engineB=read))
                    done.add(k)
                    break
                if subsequence(sealed_marks, marks_of(raw_before) + after):
                    breaks = "".join(re.findall(r"\n+", segment))
                    glyph = s.chars[k]
                    if breaks and "\n" in raw_before:
                        first, second = raw_before.split("\n", 1)
                        new = marks_of(first) + breaks + marks_of(second) + glyph + after
                    elif breaks:
                        new = marks_of(segment[:segment.index("\n")]) + breaks + glyph + after
                    else:
                        new = marks_of(raw_before) + glyph + after
                    if new != segment:
                        out.append((start, end, new, "K5", {"standIns": glyph, "engineA": raw_before + glyph}))
                    done.add(k)
                break
        return out, doubts

    # K1: a stand-in engine A never reads, where engine A read a character.
    def characters(self) -> list[tuple[int, int, str, str, dict[str, Any]]]:
        out = []
        for tag, i1, i2, j1, j2 in ra.opcodes(self.a.chars, self.s.chars):
            if tag != "replace" or i2 - i1 != j2 - j1:
                continue
            for x in range(j2 - j1):
                glyph, read = self.s.chars[j1 + x], self.a.chars[i1 + x]
                if glyph in self.facts.standins() and self.facts.a_counts[glyph] == 0 and read != glyph:
                    at = self.s.offsets[j1 + x]
                    out.append((at, at + 1, read, "K1", {"standIns": glyph, "engineA": read}))
        return out


def pair_state(text: str, opener: str, closer: str) -> tuple[int, int]:
    """TEXT's OPENERs left open and CLOSERs with nothing open before them."""
    depth = unopened = 0
    for c in text.translate(NORM):
        if c == opener:
            depth += 1
        elif c == closer:
            if depth:
                depth -= 1
            else:
                unopened += 1
    return depth, unopened


def common_suffix(a: str, b: str) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def common_prefix(a: str, b: str) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def at_record_spot(chars: str, start: int, end: int, before: str, after: str) -> bool:
    """Whether a page's CJK CHARS[start:end] stand where a phase 3 record
    puts its spot: the characters on either side are the record's context
    BEFORE and AFTER (engine A's, CJK), four of them at least, one on each
    side but at the page's edge."""
    left = common_suffix(before, chars[:start])
    right = common_prefix(after, chars[end:])
    return left + right >= 4 and (left > 0 or start == 0) and (right > 0 or end == len(chars))


# --- K-seq -------------------------------------------------------------------

@dataclass
class Figure:
    """A figure engine A read: its page, the index of engine A's character
    after it, its kind (`bracket`, `heading` - opening a heading of the page -
    or `line`), its value and whether a dot follows it."""
    page: int
    index: int
    style: str
    value: int
    dot: bool


BRACKETED = re.compile(r"[（(]\s*([0-9０-９]{1,3})\s*[)）]")
TRAILING_FIGURE = re.compile(r"(?:^|\n)[ \t]*([0-9０-９]{1,3})[ \t]*([.．])?[ \t]*$")
LINE_BREAK_FIGURE = re.compile(r"\n[ \t]*([0-9０-９]{1,3})[ \t]*([.．])?[ \t]*$")


def engine_a_figures(pd: PageData) -> list[Figure]:
    """Engine A's figures on one page, in its reading order."""
    a, s = ra.stream(pd.a_body), ra.stream(pd.text)
    to_a = ra.mapping(a.chars, s.chars)
    a_to_s = {j: k for k, j in enumerate(to_a) if j is not None}
    lines = lines_of(pd.text)
    out = []
    for k, gap in enumerate(a.gaps):
        for match in BRACKETED.finditer(gap):
            out.append(Figure(pd.scan, k, "bracket", figure_value(match.group(1)), False))
        if BRACKETED.search(gap):
            continue
        # A line's figure: after a line break in the gap, or opening the page.
        match = LINE_BREAK_FIGURE.search(gap) or (TRAILING_FIGURE.fullmatch(gap.lstrip("\n")) if k == 0 else None)
        if not match:
            continue
        at = next((a_to_s[j] for j in range(k, len(a.chars)) if j in a_to_s), None)
        style = "line"
        if at is not None:
            line = lines[pd.text.count("\n", 0, s.offsets[at])]
            if lint.HEADING.match(line.text):
                style = "heading"
        out.append(Figure(pd.scan, k, style, figure_value(match.group(1)), bool(match.group(2))))
    return out


def contents_numbers(book: Any, model: Mapping[str, Any], texts: Mapping[int, str]) -> dict[int, list[tuple[str, int]]]:
    """For each body page the contents map a line of theirs to: the contents
    line's title and the figure engine A read opening that contents line."""
    out: dict[int, list[tuple[str, int]]] = {}
    for run in (model.get("contents") or {}).get("runs") or []:
        for entry in run.get("entries") or []:
            target, page = entry.get("matchedPage"), entry.get("page")
            if target is None or page not in texts:
                continue
            pd = page_data(book, model, page, texts[page])
            s, a = ra.stream(pd.text), ra.stream(pd.a_body)
            to_a = ra.mapping(a.chars, s.chars)
            line = next((l for l in lines_of(pd.text) if pp.CJK(l.text) == entry["text"]), None)
            if line is None:
                continue
            ks = [k for k, o in enumerate(s.offsets) if line.start <= o < line.end and to_a[k] is not None]
            if not ks:
                continue
            match = TRAILING_FIGURE.search(a.gaps[to_a[ks[0]]])
            if match:
                out.setdefault(target, []).append((entry["text"], figure_value(match.group(1))))
    return out


def dot_missing(figure: Mapping[str, Any], page: int, sequence: Sequence[Figure]) -> bool:
    """Whether FIGURE (a figure a rule writes opening a line or a heading)
    is read without the dot that every figure of its kind beside it on its
    page carries."""
    index = next((i for i, f in enumerate(sequence) if f.page == page and f.index == figure.get("aIndex")), None)
    if index is None:
        return False
    own = sequence[index]
    if own.style not in ("line", "heading") or own.dot:
        return False
    before = next((f for f in reversed(sequence[:index]) if f.style == own.style), None)
    after = next((f for f in sequence[index + 1:] if f.style == own.style), None)
    same_page = [f for f in (before, after) if f is not None and f.page == page]
    return bool(same_page) and all(f.dot for f in same_page)


def sequence_problem(figure: Mapping[str, Any], page: int, title: str, sequence: Sequence[Figure],
                     contents: Mapping[int, list[tuple[str, int]]]) -> str | None:
    """K-seq for one figure a rule writes (its evidence FIGURE: value,
    style, the engine A character after it, the stand-ins it replaces): why
    it is not settled, or None.  Settled: 1; the number engine B read as CJK
    numerals there; one more than engine A's figure of its kind before it,
    or one less than the one after it; a heading's number that the contents
    give its title."""
    value = figure["figure"]
    numerals = "".join(c for c in figure.get("standIns") or "" if c in NUMERALS)
    index = next((i for i, f in enumerate(sequence) if f.page == page and f.index == figure.get("aIndex")), None)
    own = sequence[index] if index is not None else None
    style = own.style if own else figure.get("style")
    before = next((f for f in reversed(sequence[:index]) if f.style == style), None) if index is not None else None
    after = next((f for f in sequence[index + 1:] if f.style == style), None) if index is not None else None
    if dot_missing(figure, page, sequence):
        return f"figure {value} is read without the dot the figures beside it carry"
    # The page holding the figure's own value in CJK numerals, and nothing
    # else, is a reading of the print in the other script (一般 read 1般 is
    # one too): 1, and the numerals' agreement, hold for either script, so
    # only the figures around it or the contents settle it - a 1 by the 2
    # after it (a list that starts there).
    script_only = bool(numerals) and numerals == (figure.get("standIns") or "") \
        and rm.numeral_value(numerals) == value
    if not script_only and (value == 1 or (numerals and rm.numeral_value(numerals) == value)):
        return None
    if script_only and value == 1 and after is not None and after.value == 2:
        return None
    if before is not None and before.value == value - 1:
        return None
    if after is not None and after.value == value + 1 and (before is None or before.value < value):
        return None
    if style == "heading":
        for text, number in contents.get(page, []):
            if number == value and rm.entry_match(pp.variant_fold(text), pp.variant_fold(title)) >= rm.CONTENTS_SIMILARITY:
                return None
    return (f"figure {value} does not go on from engine A's figure of its kind before it "
            f"({before.value if before else 'none'}) or lead to the one after it "
            f"({after.value if after else 'none'})" + (", and the contents give its title no such number"
                                                      if style == "heading" else ""))


def marks(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
          facts: BookFacts | None = None) -> Plan:
    """The mark rules over the pages ORDER, proposed for the whole book and
    the figures they write checked (K-seq): what passes, and every doubt."""
    facts = facts or book_facts(book, model)
    plan = Plan()
    first: dict[int, list[re_.Edit]] = {}
    sequence: list[Figure] = []
    for scan in order:
        pd = page_data(book, model, scan, texts[scan])
        page_plan = Marks(pd, facts).plan()
        first[scan] = page_plan.edits
        plan.doubts.extend(page_plan.doubts)
        sequence.extend(engine_a_figures(pd))
    # A figure a rule writes in brackets that engine A read bare (engine B's
    # bracket glyphs round it) belongs to the bracketed figures' list.
    by_place = {(f.page, f.index): f for f in sequence}
    for scan in order:
        for edit in first[scan]:
            found = by_place.get((scan, edit.evidence.get("aIndex")))
            if found is not None and edit.evidence.get("style") == "bracket":
                found.style = "bracket"
    contents = contents_numbers(book, model, texts)
    refused: set[int] = set()
    for scan in order:
        for edit in first[scan]:
            if edit.evidence.get("figure") is None:
                continue
            line_end = texts[scan].find("\n", edit.end)
            title = pp.CJK(texts[scan][edit.end:line_end if line_end >= 0 else len(texts[scan])])
            why = sequence_problem(edit.evidence, scan, title, sequence, contents)
            if why:
                refused.add(id(edit))
                plan.doubts.append(unsettled("MARK-FIGURE-LOST", scan, "K-seq", texts[scan], edit.start, edit.end,
                                             why, engineA=edit.evidence.get("engineA"), proposed=edit.after,
                                             proposedRule=edit.rule))
    plan.edits = [edit for scan in order for edit in first[scan] if id(edit) not in refused]
    return plan


def cjk_print(c: str) -> bool:
    """Whether C, a mark's neighbour, is CJK print: a CJK character, a
    full-width mark or an ideographic space ("" at the page's edge is not)."""
    return bool(pp.CJK(c)) or c in CJK_MARKS or c == "\u3000"


def ascii_forms(text: str, s: ra.Stream) -> dict[int, str]:
    """K9 on a page's TEXT (S its stream): the offsets of the ASCII marks of
    its Chinese text, each with the gap it stands in (the text between two
    page characters), an HTML tag, $…$ math, a URL and a Markdown link's
    target set aside (NOT_TEXT).  A bracket that pairs on its line (a `(`
    with the `)` that closes it) goes with its partner: both where CJK print
    (cjk_print: a CJK character, a full-width mark) stands right outside
    either one, or a CJK character anywhere between them (乾隆(1736—1795年)间,
    参见(图2)所示, 甲(1734)乙, a note number after a mark: 。」(50)), neither
    else (Western text: (London: Press, 1962)).  Until 2026-10-26 a mark
    right outside did not count: one line then held （49） after a
    character and (50) after 」 (a historical monograph, 17 such pairs left half width).  Any other - `, : ; ? !`, a bracket with
    no partner on its line - where CJK print stands beside it (cjk_print),
    in a gap that holds no letter outside its HTML tags (Western text keeps
    its marks; a table cell's tag is markup); so not one between two
    figures (14,625; 11:30)."""
    plain = NOT_TEXT.sub(lambda m: "\x00" * len(m.group()), text)
    paired: set[int] = set()
    single: list[int] = []
    opened: list[int] = []
    for i, c in enumerate(plain):
        if c == "\n":
            single += opened
            opened = []
        elif c == "(":
            opened.append(i)
        elif c == ")" and opened:
            j = opened.pop()
            if cjk_print(plain[j - 1] if j else "") or cjk_print(plain[i + 1:i + 2]) or pp.CJK(plain[j + 1:i]):
                paired |= {i, j}
        elif c in ASCII_MARKS:
            single.append(i)
    found: dict[int, str] = {}
    for i in sorted(paired | set(single + opened)):
        gap = s.gaps[bisect.bisect_left(s.offsets, i)]
        if i in paired or (not LETTER.search(TAG.sub("", gap))
                           and (cjk_print(text[i - 1] if i else "") or cjk_print(text[i + 1:i + 2]))):
            found[i] = gap
    return found


def engine_gap(stream: ra.Stream, to: Sequence[int | None], k: int) -> str | None:
    """What an engine (STREAM, with TO mapping the page's characters onto
    it) holds in the page's gap K, before the page's character K (K =
    len(TO): after the last one), where the page's characters on either side
    are aligned to adjacent characters of it (at an edge: to its first or
    last); None where it is not aligned so."""
    n = len(to)
    if not n:
        return None
    if k == 0:
        return stream.gaps[0] if to[0] == 0 else None
    if k == n:
        return stream.gaps[-1] if to[n - 1] == len(stream.chars) - 1 else None
    i, j = to[k - 1], to[k]
    return stream.gaps[j] if i is not None and j == i + 1 else None


def engine_lines(stream: ra.Stream, to: Sequence[int | None], s: ra.Stream, at: int) -> str:
    """K12: an engine's lines (STREAM, with TO mapping the page's characters
    onto it) that hold the page's line at offset AT (S: the page's stream):
    those of the characters it aligns to the line's, and any that holds the
    line's characters in a row (an engine that reads a table's rows in
    another order)."""
    start, end = s.text.rfind("\n", 0, at) + 1, s.text.find("\n", at)
    ks = range(bisect.bisect_left(s.offsets, start), bisect.bisect_left(s.offsets, len(s.text) if end < 0 else end))
    js = [to[k] for k in ks if to[k] is not None]
    found = ""
    if js:
        lo, hi = stream.offsets[min(js)], stream.offsets[max(js)]
        end = stream.text.find("\n", hi)
        found = stream.text[stream.text.rfind("\n", 0, lo) + 1:len(stream.text) if end < 0 else end]
    chars = "".join(s.chars[k] for k in ks)
    return "\n".join([found] + [line for line in stream.text.split("\n") if chars and chars in pp.CJK(line)])


def _unescaped_at(text: str, pos: int) -> bool:
    bs = 0
    idx = pos - 1
    while idx >= 0 and text[idx] == "\\":
        bs += 1
        idx -= 1
    return bs % 2 == 0


def forms(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int]) -> Plan:
    """The forms stage over the pages ORDER, on the text every other rule
    has written: each ASCII mark of Chinese text in its full-width form and
    U+16FE6 canonicalized to 。。 (K9), emphasis runs neither engine holds
    deleted, and literal printed asterisks escaped as \\* where Markdown
    would otherwise parse emphasis or list bullets (K12)."""
    import _ocr_markdown as om
    import _tables

    plan = Plan()
    single_star_re = re.compile(r"(?<!\*)\*(?!\*)")
    bullet_star_re = re.compile(r"^[ \t]*\*[ \t]+\S")
    for scan in order:
        text = texts[scan]
        masked_text = "".join(om.mask_nonrendered_markdown(om.split_lines(text)))
        html_tables = _tables.html_table_spans(text)
        not_text_spans = [m.span() for m in NOT_TEXT.finditer(text)]
        has_title_star = any(
            om.terminal_title_note_heading_marker(om.line_body(raw_line)) is not None
            for raw_line in om.split_lines(text)
        )

        def in_code_or_comment(pos: int) -> bool:
            return masked_text[pos] == " " and text[pos] != " "

        def in_protected_star(pos: int) -> bool:
            return (
                in_code_or_comment(pos)
                or any(lo <= pos < hi for lo, hi in html_tables)
                or any(lo <= pos < hi for lo, hi in not_text_spans)
            )

        pd = page_data(book, model, scan, text)
        s, a, b = ra.stream(text), ra.stream(pd.a_body), ra.stream(pd.b_body)
        to_a, to_b = ra.mapping(a.chars, s.chars), ra.mapping(b.chars, s.chars)
        for i, gap in ascii_forms(text, s).items():
            plan.edits.append(re_.Edit(scan, i, i + 1, text[i].translate(NORM), rule="K9",
                                       evidence={"before": text[i], "gap": gap}))
        for m in re.finditer("\U00016fe6", text):
            if not in_code_or_comment(m.start()):
                plan.edits.append(re_.Edit(scan, m.start(), m.end(), "。。", rule="K9",
                                           evidence={"before": "\U00016fe6"}))
        for k, gap in enumerate(s.gaps):
            emphasis = list(EMPHASIS.finditer(gap))
            single_stars = list(single_star_re.finditer(gap))
            if not emphasis and not single_stars:
                continue
            start = s.offsets[k - 1] + 1 if k else 0
            read = [engine_gap(a, to_a, k) or "", engine_gap(b, to_b, k) or ""]
            if emphasis:
                gone, rest = [], gap
                for m in reversed(emphasis):
                    at = start + m.start()
                    if in_protected_star(at):
                        continue
                    line_end = text.find("\n", at)
                    line = text[text.rfind("\n", 0, at) + 1:len(text) if line_end < 0 else line_end]
                    if line.strip() == m.group() or om.THEMATIC_BREAK_RE.fullmatch(line):
                        continue
                    held = read + [engine_lines(a, to_a, s, at), engine_lines(b, to_b, s, at)]
                    if any(m.group()[0] in r.translate(EMPHASIS_FOLD) for r in held):
                        if m.group()[0] == "*":
                            escaped = "".join(
                                "\\*" if _unescaped_at(text, at + idx) else "*"
                                for idx in range(len(m.group()))
                            )
                            if escaped != m.group():
                                plan.edits.append(
                                    re_.Edit(scan, at, start + m.end(), escaped, rule="K12",
                                             evidence={"before": m.group(), "escaped": True})
                                )
                        continue
                    gone.insert(0, m)
                    rest = rest[:m.start()] + rest[m.end():]
                space = not rest and 0 < k < len(s.chars) and read[0].isspace() and "\n" not in read[0]
                for n, m in enumerate(gone):
                    plan.edits.append(re_.Edit(scan, start + m.start(), start + m.end(), " " if space and not n else "",
                                               rule="K12", evidence={"before": m.group(), "engineA": read[0],
                                                                     "engineB": read[1]}))
            for m in single_stars:
                at = start + m.start()
                if not _unescaped_at(text, at) or in_protected_star(at):
                    continue
                line_start = text.rfind("\n", 0, at) + 1
                line_end = text.find("\n", at)
                line = text[line_start:len(text) if line_end < 0 else line_end]
                if om.THEMATIC_BREAK_RE.fullmatch(line):
                    continue
                if bullet_star_re.match(line) and at == line_start + line.index("*"):
                    prev_part = text[: max(0, line_start - 1)].rstrip("\r\n")
                    prev_line = prev_part[prev_part.rfind("\n") + 1 :] if prev_part else ""
                    next_part = (text[line_end + 1 :].lstrip("\r\n") if line_end >= 0 else "")
                    next_nl = next_part.find("\n")
                    next_line = next_part[: len(next_part) if next_nl < 0 else next_nl]
                    adjacent_bullet = bool(
                        bullet_star_re.match(prev_line) or bullet_star_re.match(next_line)
                    )
                    if not has_title_star and adjacent_bullet:
                        continue
                    held = read + [engine_lines(a, to_a, s, at), engine_lines(b, to_b, s, at)]
                    if not has_title_star and not any("*" in r.translate(EMPHASIS_FOLD) for r in held):
                        continue
                plan.edits.append(re_.Edit(scan, at, at + 1, "\\*", rule="K12",
                                           evidence={"before": "*", "escaped": True}))
    return plan


def answer_scripts(book: Any, texts: Mapping[int, str], order: Sequence[int]) -> Plan:
    """V1 (stage `script`) over the pages ORDER, in a book printed in
    simplified script (proofread_pages.simplified_script on both engines'
    drafts, as phase 3 tells it): each answer 裁決's sealed records say was
    written as given (resolved_from `adjudicator`) that phase 3 now writes
    otherwise (proofread_pages.simplified_as_read of the answer as given and
    the two readings) is written so where the page still holds it at the
    record's spot (at_record_spot, at one place on the page), one edit a
    character.  Where a character so written is engine A's form and engine
    B read another character there, or a form neither engine read
    (proofread_pages.converted_against_b), an `answer-script-converted`
    doubt names the spot, as phase 3's doubt of that kind does."""
    plan = Plan()
    drafts = (text for scan in sorted(book.pages)
              for text in (book.pages[scan].engine_a_text(), book.pages[scan].engine_b_text()))
    if not pp.simplified_script(drafts):
        return plan
    for scan in order:
        text = texts[scan]
        s = ra.stream(text)
        taken: set[int] = set()
        for record in book.pages[scan].record.get("adjudications") or []:
            if not isinstance(record, dict) or record.get("resolved_from") != "adjudicator":
                continue
            sealed = pp.CJK(str(record.get("resolved") or ""))
            a, b = pp.CJK(str(record.get("draft_reading") or "")), pp.CJK(str(record.get("writer_reading") or ""))
            # From the answer as given, so that phase 3's own writing is never
            # converted again (薴 is sealed 苧, itself a key of the table: 苎);
            # from the sealed answer where the seal keeps none, or where digits
            # both engines read were spliced round it.
            answered = pp.CJK(str(record.get("answeredTraditional") or sealed))
            written = pp.simplified_as_read(answered if len(answered) == len(sealed) else sealed, a, b)
            before = pp.CJK(str(record.get("context_before") or ""))
            after = pp.CJK(str(record.get("context_after") or ""))
            spots = [k for k in range(len(s.chars) - len(sealed) + 1) if s.chars.startswith(sealed, k)
                     and at_record_spot(s.chars, k, k + len(sealed), before, after)]
            if len(spots) != 1:
                continue
            at = spots[0]
            changed = [k for k in range(len(sealed)) if sealed[k] != written[k] and at + k not in taken]
            taken.update(at + k for k in changed)
            for k in changed:
                offset = s.offsets[at + k]
                plan.edits.append(re_.Edit(scan, offset, offset + 1, written[k], rule="V1", evidence={
                    "answered": answered, "sealed": sealed, "written": written, "engineA": a, "engineB": b,
                    "form": "read" if written[k] in a + b else "one",
                    "contextBefore": record.get("context_before"), "contextAfter": record.get("context_after"),
                    "crop": record.get("crop")}))
            # The characters this rule writes (phase 3 doubted its own when it
            # sealed them).
            against = pp.converted_against_b("".join(sealed[k] for k in changed), a, b)
            if against:
                page = list(text)
                for k in changed:
                    page[s.offsets[at + k]] = written[k]
                forms = "、".join(f"{w} ({'as engine A read it' if w in a else 'its one simplified form'})"
                                  for w in dict.fromkeys(against))
                read_a, read_b = (f"「{x}」" if x else "nothing" for x in (a, b))
                plan.doubts.append(unsettled(
                    "answer-script-converted", scan, "V1", "".join(page), s.offsets[at],
                    s.offsets[at + len(sealed) - 1] + 1,
                    f"裁決's answer 「{answered}」, in traditional script, is written {forms} where engine A read "
                    f"{read_a} and engine B {read_b}; check the character against the image",
                    engineA=a, engineB=b, answered=answered))
    return plan


def heading_dashes(book: Any, model: Mapping[str, Any], scan: int, text: str, facts: BookFacts) -> Plan:
    """K1's dash on one page's headings alone (the contents stage runs it
    on the unit titles it makes headings)."""
    marks = Marks(page_data(book, model, scan, text), facts)
    plan = Plan()
    for k1, k2, start, end, span, non, gap_raw in marks.gaps():
        if k1 < 0 or "\n" in span or not lint.HEADING.match(marks.line_of(k2)):
            continue
        found = marks.dash(k1, k2, span, non, gap_raw)
        if isinstance(found, Doubt):
            plan.doubts.append(found)
        elif found and found[0] != span:
            plan.edits.append(re_.Edit(scan, start, end, found[0], rule="K1", evidence={
                "before": span, "engineA": gap_raw, **found[2]}))
    return plan


# --- spacing -------------------------------------------------------------------

DATE = re.compile(rm.DATE_SHAPE.pattern)


def starts_with_date(line: str) -> bool:
    return bool(DATE.match(lint.LINE_PREFIX.sub("", line).strip()))


def has_date(line: str) -> bool:
    return bool(DATE.search(lint.LINE_PREFIX.sub("", line)))


def a_blocks_of(pd: PageData) -> list[int]:
    """For each character of engine A's body stream, the index of the block
    it is in (the body is the blocks joined by line breaks)."""
    starts, at = [], 0
    for block in pd.a_blocks:
        starts.append(at)
        at += len(block["text"]) + 1
    a = ra.stream(pd.a_body)
    return [bisect.bisect_right(starts, offset) - 1 for offset in a.offsets]


class Spacing:
    """P5 on one page."""

    def __init__(self, pd: PageData):
        self.pd = pd
        self.text = pd.text
        self.s = ra.stream(pd.text)
        self.a = ra.stream(pd.a_body)
        self.to_a = ra.mapping(self.a.chars, self.s.chars)
        self.block_of = a_blocks_of(pd) if pd.a_blocks and pp.CJK(pd.a_body) else []

    def aligned(self, line: Line) -> list[int]:
        """Engine A's indices of the line's characters it shares."""
        out = []
        for k in range(bisect.bisect_left(self.s.offsets, line.start), bisect.bisect_left(self.s.offsets, line.end)):
            if self.to_a[k] is not None:
                out.append(self.to_a[k])
        return out

    def one_block(self, heading: Line, line: Line) -> bool:
        """Whether engine A read the heading and the line after it as one
        block: the line's first character engine A shares is in a block that
        also holds the heading's (or, for a heading engine A shares no
        character of, holds the heading's figures and characters before it)."""
        if not self.block_of:
            return False
        after = self.aligned(line)
        if not after:
            return False
        block = self.block_of[after[0]]
        mine = self.aligned(heading)
        if mine:
            return any(self.block_of[j] == block for j in mine)
        text = self.pd.a_blocks[block]["text"] if 0 <= block < len(self.pd.a_blocks) else ""
        lead_at = self.a.offsets[after[0]] - sum(len(b["text"]) + 1 for b in self.pd.a_blocks[:block])
        return bool(lint.line_core(text[:max(0, lead_at)]))

    def plan(self) -> Plan:
        plan = Plan()
        lines = lines_of(self.text)
        for first, second in zip(lines, lines[1:]):
            if not first.text.strip() or not second.text.strip():
                continue
            one, two = lint.HEADING.match(first.text), lint.HEADING.match(second.text)
            if not (one or two):
                continue
            if one and not two and self.one_block(first, second) and not has_date(first.text) \
                    and not starts_with_date(second.text):
                # A heading engine A read in one block with the line after
                # it, no date between them: the heading printed over two
                # lines, or a run-in lead (the heading stage settles which),
                # not a heading without its blank line.  A date line ends a
                # heading's block.
                continue
            plan.edits.append(re_.Edit(self.pd.scan, first.end, first.end, "\n", rule="P5", evidence={
                "heading": first.text if one else second.text, "where": "after" if one else "before"}))
        return plan


def spacing(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int]) -> Plan:
    """P5 over the pages ORDER."""
    plan = Plan()
    for scan in order:
        plan.edits.extend(Spacing(page_data(book, model, scan, texts[scan])).plan().edits)
    return plan
