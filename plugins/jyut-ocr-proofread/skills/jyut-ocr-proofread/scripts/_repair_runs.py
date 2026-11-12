"""The book repair tool's text-run rules (scripts/repair_book.py, stage
`runs`): the digits, Latin letters and kana both engines read between two
characters that the page lacks, the note calls (①) both read in a line, and
the □ a book prints for a character it lacks; the marks a refused first
answer and engine A agree on, and the marks both engines read alike that
the page lacks, in a table's cells and in running text.

Why the stage exists (measured on a 609-page modern academic book,
2026-10-01): phase 3 merges the engines on CJK characters only
(proofread_pages.CJK), so a year, a page range, a volume number, a Latin
name or a kana title that both engines read stands in the page only where
the punctuator wrote it from the crop.  Of the text runs both engines read
identically, 5,455 gaps on 476 pages lacked them in the sealed pages; 122
of 122 such runs drawn at random matched the print on the page image, and
every error found under agreement was a note number (a circled figure),
which this stage writes only as a note call in its place in the page's
numbering (N1, below).  The □ (MISSING) is a letter of a run the
same way: CJK() drops it as it drops a figure, and the punctuator never
wrote one back - measured on the four kept books (2026-10-07), the engines
read 19 (engine A) and 18 (engine B), the sealed pages held one.  The
stage writes the 9 both engines read alike (a multi-volume archival compilation 2 scans 79 and 260,
a historical monograph scan 204), each as printed, and the one engine B writes as LaTeX
of \\Box alone (a multi-volume archival compilation 3 scan 116; BOX_RE reads `$ \\Box\\Box $` as □□,
2026-10-08: six more on a multi-volume archival compilation 5, scans 358, 360 and 361, each matching
the print on the page image, where the stage said "engine B reads none");
a run of 7 that engine B reads as 8 and the one only engine A reads (the
character 口, which engine B reads) are doubts.  A run of □ that engine A
breaks over a line the page joins is written with a space in it, as two
figures there are (neither engine breaks one in the four books).

The unit is the gap between two consecutive CJK characters of the page
(_repair_align: gaps[k] is the raw text before character k), and the
evidence is two independent readings of that gap: engine A's between the
two characters it aligns to, adjacent in its text, and engine B's likewise.
Each rule compares the gaps as tokens (text runs, marks by class, cell and
row boundaries of a table; line breaks, spaces and engine markup set aside;
a gap's text does not open a line, so a hyphen inside a line is a mark and
not a list marker).  An engine's LaTeX that holds whole figures joined
by \\times and nothing else is the print it stands for, one run with ×
for the command (PRODUCT_RE; engine A writes an imprint page's size
`$ 850 \\times 1168 $` where engine B writes 850×1168; LaTeX of \\Box
alone is that many □ the same way, BOX_RE).  Measured on the eleven
books of the corpus with engine A readings (2026-10-06): a product in
LaTeX stands on the imprint pages of a multi-volume archival compilation 2 and 3 alone (and of a multi-volume archival compilation 4, by
its doubt list), whose size the stage left out with a `run-one-engine`
doubt ("engine A reads none"); engine A's other LaTeX that holds a figure
is a note marker (360 on the 609-page modern book, five more with the
item's number after it) or a formula (six with `=` in one table of that
book, engine B writing them with `*`; four with `\\frac` or a subscript
in two other books), which stays unknown markup.  A × alone, with no
letter or figure joined to it, is print, a run as □ is (×月×日: CJK()
drops it too); but where the page holds no other run and engine A reads
a run with × in it, the page's × is that run's connector, a mark (N1,
_carried): the page keeps the sign of a product whose figures phase 3
dropped (a multi-volume archival compilation 5 scan 3: 开本：×毫米 for 850×1168).  Two readings agree
when their tokens are the same, or when the characters they hold are
(fused: one engine wraps a title in emphasis markers, and the point
before it then ends the run); they nearly agree when they differ only in
a hyphen inside a word, a dash or a hyphen between two figures (a multi-volume archival compilation
5's imprint page: engine A 1—5,000, engine B 1-5,000) or an apostrophe's
form (nearly): N1 and N6 write such a gap as engine A reads it, with a
`run-engines-differ` doubt that says so (the dash, 2026-10-11, model-off:
5 gaps on the five kept books, all on a multi-volume archival compilation 5 - its 1—5,000 and four
page ranges of its notes, 95—96 and the like - each a dash on the page
image).

What is written: a run as engine A spells it, but a connector of it that
the page held alone in the page's form (a citation's 5：3, printed full
width where engine A reads 5:3) - not a comma, which a run holds as ASCII
alone (a figure's 14,625, printed half width where the page held 14，625:
three imprint pages, on the page images, 2026-10-26); a mark of the page
stays as it is and where it is; a mark the page lacks is written only
beside a run it shares engine A's line with, in a table's cell (N5), or
where both engines read the gap's marks alike (N8), in engine A's glyph -
an ASCII mark in its ordinary full-width form, unless it stands in Western
text (written()).

Rules, in the order they run (the first applied before the rest; both
steps pass the page-edge check, edge_safe; N6 where N1 refuses a gap; N7
where the engines read a gap slightly apart):
- R1 a punctuation pass whose retry was kept over a first answer refused on
  the count: its first answer stands on the page exactly once (its
  characters), and in a gap of that stretch the first answer and engine A
  write the same marks and runs (by class) while the page differs (and
  engine B, where it is aligned there, agrees with them): the page's gap
  becomes engine A's.  A refused first answer is the model's reading of the
  crop; engine A is an independent witness that never saw it, and its
  glyphs are the print's where the two differ in shape.  Not at an outer
  gap of the stretch where engine A ends its line and the page does not
  (the page's gap then holds the next block's marks too), nor over the
  page's markup, nor a figure opening a line.
- N1 both engines hold the same text runs in the gap and the page holds
  none.  The page's marks are matched to engine A's, each to a mark of its
  own class (of its coarse class only where engine A holds none of that
  class).  T1, every mark of engine A stands on the page: each run goes in
  at its place among them; a connector the page holds beyond them goes only
  when a run of the gap itself holds that connector and engine B reads no
  such mark (the hyphen left of 119-140).  T2, engine A holds marks the page
  lacks: the page's marks stay, and each stretch between two of them that
  holds a run on engine A's line becomes engine A's, runs and marks
  (stretches); a stretch of marks alone is left; the result must be the
  same whether the page's marks are matched from the left or from the
  right.  A bracket written this way that finds no partner on its line once
  the page's edits stand is withheld (unpaired); where it stood between two
  runs, which would then read as one (5（1990 as 51990), the edit is not
  made and the gap is a `run-no-place` doubt.  A run on the far side of
  a line break in engine A goes on that side on the page; a run with no mark
  beside it is not put on a side of a page break engine A does not give,
  and a run on a line of its own in engine A is not joined to another line.
  The rows of a table that phase 3 moved into an earlier page's table (the
  seal's tableStitch) are read on the page whose engines read them, in its
  text before the move, and written where the rows stand now (_moved_rows).
  In a gap that holds a note marker, the part on the line of the character
  before, before the marker, is read on its own (head_gap); the marker
  stays as the page has it.  Not at the page's leading edge (no character
  stands before), nor where the page and engine A put the marker on either
  side of a mark (the mark would be written twice).
  A note call - a circled figure both engines read in a line, between two
  characters or after the last one of a line, where the page holds no note
  marker - is such a run (called): the same glyph at the same place among
  the gap's marks in both engines (by coarse class: beside one call of the
  archive volumes the engines' brackets differ in shape; engine A's
  `$ ^{①} $` read as its figure), written close beside the page's marks
  (T1) or with engine A's marks about it (T2), never by N6 (measured on
  runs the punctuator never saw; no call reaches it on the five books
  measured); only where the page's calls in reading order run 1, 2, 3 ...
  up to it (numbered), the count stopping at the first that does not, and
  on no page that holds a call where neither engine reads one (written at
  the engines' place it would stand twice).  A book that numbers its notes
  on through a chapter prints figures past the circled glyphs, which both
  engines read alike as the glyph of some of their digits; on such a book
  a page whose calls do not start at ① keeps them as its doubt, right or
  wrong.  Not two circled figures side by side (where measured, one number
  read figure by figure), a call in a gap that also holds a note's own
  number opening a line, a call with a figure after it (a piece of its
  number), a parenthesised or superscript figure, a call the engines put
  on either side of a mark, one where the page breaks a line engine A
  reads on (two notes set on one line), nor in a table of the page.
  Measured (#35, 2026-10-24, model-off): on four archive volumes that
  number their notes per page, 28 calls the sealed pages lacked (after a
  word, after a closing bracket, before a comma or a full stop; two with
  the comma or full stop engine A reads beside them, which the page lacked
  too), the 26 of three volumes each in the golden, the two of the fourth
  on the page image, and no other edit; on a modern book whose notes run
  past 200 a chapter, the engines agree on 102 calls the page lacks, many
  wrong (81 and 82 both read ⑧).  A neighbour one apart is no proof there:
  it passes 50 of the 102, and 15 of the 19 of those checked on the image
  are wrong (77-79 read ⑦⑧⑨).  The numbering admits 3 (①②③ opening a
  chapter, right); without it the stage writes 86 more there.  Met on
  neither: a page whose first call ends in 1 read by that figure alone
  (11 as ①, 12 as ②), which the numbering would pass.
- N5 a gap inside a table of the page (table_spans: a Markdown table from
  its label row and rule, an HTML table from its opening tag to its closing
  one) beside a character that lies in an engine A block the punctuator
  never read (its label in proofread_pages.FURNITURE_LABELS: a table
  block; cell_gap, per side of the gap), where the page holds neither a
  mark nor a run and both engines hold the same tokens, marks alone or
  marks with runs: the gap becomes the engines' - marks and runs in engine
  A's order and glyphs (written), the page's cell boundaries, spaces and
  breaks where they are, the cell boundaries matched one to one
  (stretches; a gap that N1 weaves is written once, whole, and named N5
  when it holds a mark N1 leaves - N1 again where the bracket guard
  withheld every such mark).  Phase 3 runs no punctuation pass on a table
  block: its cells hold engine A's marks, none, or the （） the structure
  pass's lock lets it add, so an empty gap of such a cell where both
  engines read marks is lost print (running text: N8).  Measured on the
  609-page book (2026-10-02): where engine A's grid was refused, the
  structure pass rebuilt the table from the bare
  merged characters with punctuation locked, and 30 pages' cells were
  sealed with 29 of the 1,040 marks engine A read there; the rule wrote
  723 marks in 645 gaps on 18 pages, and 30 of 30 of its edits drawn at
  random matched the print on the page image; the one page table built
  from a punctuated text block (scan 606) gets no edit.  Not on a page
  punctuated whole (unread_marks), nor in a table rebuilt from its crop
  (unread_tables): there an empty gap may be the model's reading.  Not
  where engine A holds a star, a character a renderer reads as markup
  (RENDERER_MARKS) or one that prints nothing, nor a note marker; the
  bracket guard (unpaired) holds for these marks too, over all the edits
  of the page together, so the two brackets of a pair written in two gaps
  of one row stand - not across a cell boundary, nor around a gap between
  them where engine A read brackets the page will not hold.  The marks
  the rule found and could not write are one doubt a page
  (`run-no-place`, with their count).
- N8 a gap of running text (not in a table of the page, whose cells are
  N5's; not at a page's edge) between two characters that both engines
  read side by side, engine A on one line - no line break in its gap nor
  in the page's, no run in engine A's - where both engines hold the same
  marks (markup and ASCII white space set aside, an ASCII mark read in its
  full-width form, every one a mark of CJK print, rr.CJK_MARKS, or an
  ideographic space beside one) and the page holds none of them, or some
  of them in their order, and nothing but white space besides: the gap
  becomes engine A's marks in their full-width form.  An ideographic space
  is print, as K9 reads it: one the page holds stays, written where engine
  A reads it, and where the engines read none there the gap is left (until
  2026-10-26 the gap became the marks alone; no repaired page of the seven
  measured books holds one).  The punctuation pass read a crop: text
  outside it (engine A joined two printed paragraphs into one block, and
  the crop showed the first), a pass refused twice whose fallback kept only
  proofread_pages.PUNCT_MARKS (no “” until 2026-10-25), and a mark it
  missed leave a gap both engines read.  No bracket guard (unpaired): a
  bracket or a quote both engines read is the print's even where its
  partner is lost elsewhere (the page then holds an unbalanced pair, which
  the MARK-UNBALANCED counter names: 《》〈〉“”〔〕 counted since
  2026-10-26, which named 23 more pages of the seven books, among them four
  where both halves were read and N8 wrote the one it could reach, 卷11藝文〉
  where the 〈 stands in a gap of a run); left out, it would be lost with
  nothing said.  Off on a book whose profile saw doubled circles or
  emphasis marks beside the characters, as the rule agreedMarks of phase
  3 is: an engine's 。 there does not say whether one circle or two is
  printed, nor does it read the marks beside each character.  Measured
  (2026-10-25, model-off), on five simplified books and two others: 43
  gaps on one book, each of the 41 located in the second reading of it
  (the user's copy) as it has them; 40 on three books with goldens, all
  right by them; 69 on an archive-citing book (《》〈〉, dashes, commas; its
  golden pages score the same), 7 and 0 on two more; none in a table.
  Until then a gap of marks alone in running text was left, as an empty
  gap may be the punctuator's reading of the crop; none measured was.
- N6 N1 finds no place for the runs among the page's marks in the gap,
  the page holds no run there and both engines read the gap alike (or
  nearly: written as engine A reads it, with the doubt that says so).
  No condition on the block (footnotes, bibliography and body text on
  the 609-page modern book): a stretch that is all runs (a Western
  citation inside a Chinese note; a journal's volume, issue and year; a
  span of years; an imprint page's figures), where the punctuator, who
  read the crop without the runs, wrote the marks it saw about them in an
  order of its own, or a mark where neither engine reads one.  The gap
  becomes engine A's text of it (engine_marks): a mark the page holds
  that is one of engine A's (a multiset of them by class, a connector one
  of its runs carries counted) in the page's form (but an ASCII one of
  engine A in Western text, a bracket whose partner lies inside the gap,
  and a comma a run carries, 6,500: engine A's); one that is none of
  engine A's goes (the user's ruling, 2026-10-06), and a
  `run-mark-dropped` doubt, raised only for an edit the pairing guard and
  the page-edge check keep, names it - with the mark of engine A that took
  its place, or none - to be checked on the image.  A line break of engine A's in the gap that the page or
  engine B breaks too stays a break: the page's own there, or a
  paragraph's where the page holds none, each of engine A's lines written
  on its side of it (a multi-volume archival compilation 5's imprint page `插页：年5月`, both engines
  `插页2⏎1986年5月`, printed on two lines: `插页2⏎⏎1986年5月`, the colon
  dropped).  A break of engine A's alone is none of the print's: engine A
  cuts a line where the print wraps it or sets a wide space (paragraphs;
  a multi-volume archival compilation 3's imprint page prints `1982年12月第1版` and `1982年12月第1次印刷`
  on one line, which engine A cuts and engine B reads with a space
  between), and the gap stays a doubt (a first build of 2026-10-06 joined
  engine A's lines there, `第1版1982年12月`, and was removed on review).
  Not where a mark to drop is a bracket with a partner of its kind on the
  page (the other half would stand alone), or a mark engine B reads more
  of there than engine A (engine B's reading backs the page's: the page's
  dash where engine A reads 1840-1842 and engine B 1840—1842), nor where
  engine A breaks the line twice or the page breaks it where engine A
  does not, nor over a table's boundary, a note marker or markup, nor in
  a table of the page, nor where a bracket written would find no partner
  on its line (the gap stays a doubt).  Measured on the 609-page modern
  book (2026-10-04, model-off): 87 gaps on 59 pages, 15 of them a
  `run-no-place` doubt before, 72 a bracket pair emptied of its runs and
  reversed (`）（`, `），（`).  The marks dropped, on four books
  (2026-10-06, model-off): 58 gaps more, 57 on the 609-page book (46 of
  them a closing bracket after a page reference) and a multi-volume archival compilation 3's price;
  checked on the page images, every run written matches the print, and
  every mark dropped but one, a full stop the print doubles (`。。`), is
  not printed.  Eleven brackets to drop stay doubts, each with a partner
  of its kind on the page that the print lacks too (an opening bracket
  the punctuator put before a note, one of a reversed pair, one a title
  mark crosses).  Across engine A's line break, on the five kept books
  (2026-10-11, model-off): 3 gaps - a multi-volume archival compilation 3's 6,500 and a multi-volume archival compilation 5's 插页2
  and first 1986 (imprint pages), and a journal's issue and year on the
  609-page book, whose page held a reversed `）⏎⏎（` over the entry's
  end; each matches the print on the page image, and the colon dropped is
  not printed.  a multi-volume archival compilation 3's second 1982, engine A's break alone, stays a
  doubt.  Body text whose printed line-end full stop both engines miss
  before a figure that opens the next line comes up nowhere.
- N7 the engines read the gap's Western text slightly apart (slightly:
  every figure, word that holds a figure, mark - a quotation mark too -
  and kana the same, the spaces and line breaks between words set aside;
  words of Latin letters may be apart in case or in an apostrophe or a
  hyphen inside the word, and at most one of them, four or more letters
  in each reading, one letter - one more, one less or one other) and the
  page holds no run there: the gap is written as engine A reads it, its
  marks placed as N1 or N6 would place them for an agreed gap (where
  neither does, the gap stays the doubt it was), and the
  `run-engines-differ` doubt says it was written (`written`: "A") and
  names the word apart as each engine spells it, to be checked on the
  image.  An apostrophe is a word's only between two of its letters or
  figures; a single quotation mark one engine alone reads is a mark apart.
  Not where a figure is apart, nor a word that holds one: a wrong number
  is worse than a missing one, and where the engines' figures differ one
  of them is often a note number read as a figure.  Not in a table of the
  page (a cell's marks are N5's).  Measured on the 609-page modern book
  (2026-10-05, model-off, its 30 `run-engines-differ` gaps, the 20 with a
  Latin letter checked on the page image): 7 were written already
  (nearly); of the 23 no rule wrote, 3 held Western words apart - a name
  one letter apart (engine A right), a name one apostrophe apart (engine A
  right; the page holds a closing bracket engine A does not read there, so
  neither N1 nor N6 places it) and a word printed hyphenated across a line
  that engine A read two letters short (engine A wrong): one letter is
  where they split.  The other 20 are a note number one engine adds beside
  agreed runs (9), figures apart (4; the one with a letter, a leaf number,
  engine A wrong), kana or hangul (3), a Roman numeral in two forms (2), a
  table cell, and whole lines apart in a mark (N2's).  The rule writes one
  gap on that book.
- N2 one or more whole lines of no CJK character (a Western-language
  bibliography entry, an English footnote) that both engines read alike
  between the same two characters: written as paragraphs of their own at
  the page's one line break there, lines engine B reads as one line joined
  (by a space, or by nothing after a line that ends in a hyphen: the print
  wraps a word there); where the engines differ in some of the lines, the
  others are written - the lines paired in order, one with one, or
  consecutive lines of one engine with the one line of the other that
  holds their print (line_pairs: engine A cuts an entry where the print
  wraps it, engine B most often not).  What engine A reads on the lines of
  the characters either side of the gap (the year closing the Chinese
  entry before, a Western entry that goes on with a Chinese note on the
  next character's line) is woven with the page's text on that side of its
  break, each side on its own.  A bracket the page holds there that is one
  of the lines' own goes.  A page sealed empty where both engines read
  such text alike: every line of it.  In a gap that holds note markers (a
  Western footnote between two others, each line opening with its number)
  the lines are read with the markers that open the engines' lines set
  aside (marker_lines) and written without them; the part on the line of
  the character before is woven in the same edit; the next note's line
  keeps its number and its other text as the page has it, but a bracket of
  the lines' own the punctuator put there goes; at the page's trailing
  edge, where the page has no break, the lines go after its text.  Not
  where the page holds a note number of the lines' own (the number would
  stand after its note).  Before this the stage read such a gap only up to
  the page's first line break, or skipped it where the page had none
  there, and no doubt named the lines after the marker: on the 609-page
  book three Western notes were lost that way, and the page range before
  one of them with no doubt either.  On the same book (2026-10-06) the one
  page of Western entries in its bibliography (scan 583) lost all 10 of
  its entries and a year, 174 runs, in two gaps: in one the lines were
  paired only where both engines cut them alike (engine A cuts an entry in
  two, and another entry differs in a mark), in the other the page's colon
  after the heading could be matched to an entry's own colon on the next
  line as well (the place was not unique).  The rule now writes 9 of them
  and the year; the entry the engines read a mark apart stays a doubt.  No
  other page of that book or of three others holds such a gap.
Nothing both engines read is left out with nothing said: the runs both
engines read in a gap that the page lacks there, that no edit of the gap
writes and no doubt raised on it names, are a `run-no-place` doubt naming
them, with the way out that left them (left_out); and last on each page,
a page with no character included, the runs both engines read in its
body that it lacks and that no edit writes nor doubt names - where
neither engine aligns about them, the page holding the text in another
order or a table rebuilt - are one more (unaligned; the pages of a table
stitched across pages pooled, on the first of them: an earlier stage that
takes a moved row out of the table leaves its runs named there).  Both
compare the engines' runs with their markup set aside (_plain: an engine
that wraps a title in emphasis markers cuts one run into several), and
neither counts a note number's piece, the folio whole alone on a line at
the page's edge, nor a line of engine A's furniture blocks, which the
furniture stage deletes by design.  An edit the page-edge check withholds
names its runs and lines in its doubt.  On the 609-page book (2026-10-03),
of the runs both engines read that the delivered text lacked, 122 had no
run doubt on their page; 69 of them were folios or running heads.
Never written: a note number in any form but N1's note calls (circled
where the page's numbering does not place it, superscript in LaTeX or
HTML, a plain figure beside one, a figure of its own of one to three
digits opening a line, a short figure right after the mark that ends a
sentence - the engines agree on the wrong value too often), the page's
whole folio on a line of its own, an edit that would change the kind of a
page's first or last line, glue two runs across a `join` or break the
finalize contract there, a line that would become a list item or table
row.  A figure of four digits that opens a line is a year, not the line's
own number (LINE_FIGURE_DIGITS); alone on its line it stays out as any
run does there (N1).  A run only one engine read (one doubt a page for the gaps
engine B is not aligned at), a gap where the engines differ, an agreed run
with no clean place and a stretch whose marks could not be restored are
doubts (`run-one-engine`, `run-engines-differ`, `run-no-place`,
`retry-marks-unverified`; none for a gap of marks alone in running text
that N8 does not write); the note numbers of a page that the engines read
and the page lacks or writes with another value are one doubt per page
(`note-markers-unverified`), and so are the marks of a page's table cells
that N5 found and could not write (`run-no-place`, with their count); a
mark of the page that N6 drops is one doubt (`run-mark-dropped`).

A run only one engine read is written (N1, its tier naming the engine)
where the other engine is not aligned there: engine A's where engine B is
not, engine B's where engine A is not, but never a run that is the page's
folio whole, nor one that a table row of the page already holds elsewhere
(_in_row); those stay doubts.  Where the other engine is aligned and
reads no run there, the run stays out with a doubt as before.  Measured on
the 312 such places of a multi-volume archival compilation 2-5 and a historical monograph, each judged on the page
image (#12, 2026-10-20): engine A's runs where engine B is not aligned
matched the print at 122 of 125 (19 read as 10, the character 口 read as □,
one run where nothing is printed); engine B's where engine A is not, at
134 of 162 - the other 28 were 24 folios and 4 figure boxes (below) -
while a run where the other engine reads none matched at 5 of 25 (the
rest folios, printer's marks in a margin and blank line ends).  Engine
B's box for a figure, two corners on its 0-1000 grid ((108,319),(925,712),
FIGURE_BOX_RE), is markup, not print.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

import proofread_pages as pp
import _ocr_markdown as om
import _tables
import _repair_align as ra
import _repair_edits as re_
import _repair_joins as rj
import _repair_rules as rr


# --- tokens ---------------------------------------------------------------------

RUN, MARK, BREAK, CELL, ROW, MARKER, MARKUP, SPACE, STRUCT, OTHER = (
    "run", "mark", "break", "cell", "row", "marker", "markup", "space", "struct", "other")

# Letters and figures of a text run: ASCII and full-width digits and
# letters, Latin with diacritics, Greek, Cyrillic, kana, hangul, Roman
# numerals; and the square a book prints for a character it lacks (□,
# MISSING), which CJK() drops as it drops a figure.
MISSING = "\u25a1"
ALNUM = ("0-9A-Za-z\u00c0-\u02af\u0370-\u03ff\u0400-\u04ff\u1e00-\u1eff"
         "\u3041-\u3096\u3099-\u309f\u30a0-\u30fa\u30fc-\u30ff\u31f0-\u31ff\uff66-\uff9f"
         "\uac00-\ud7af\u2160-\u217f\uff10-\uff19\uff21-\uff3a\uff41-\uff5a" + MISSING)
LATIN = "A-Za-z\u00c0-\u02af\u0370-\u03ff\u0400-\u04ff\u1e00-\u1eff\uff21-\uff3a\uff41-\uff5a"
# A run: letters and figures, joined by a connector (a hyphen, slash, wave,
# colon, apostrophe, or a point or comma that a letter or figure follows,
# with one space allowed after the point or comma), or by one space before
# a Latin word; a percent sign may end it.
WORD = rf"[{ALNUM}]+"
RUN_RE = re.compile(
    rf"{WORD}(?:"
    rf"[-\u2010/~\uff5e\u301c'\u2019]{WORD}"              # a hyphen, slash, wave or apostrophe joins anything
    rf"|[:\uff1a](?=[0-9\uff10-\uff19]){WORD}"              # a colon between figures (17:2)
    rf"|\.(?=[0-9])[0-9]+"                                 # a decimal point
    rf"|,(?=[0-9]{{3}}(?![0-9]))[0-9]{{3}}"                 # a thousands comma (2,100)
    rf"|(?<=[{LATIN}])[.,] ?(?=[{ALNUM}]){WORD}"            # a point or comma after a letter (E. J., Press, 1962)
    rf"| (?=[{LATIN}]){WORD}"                               # a space before a Latin word
    rf")*[%\uff05]?")
# Note markers: circled and parenthesised figures, superscript and subscript
# digits.
MARKER_RE = re.compile(r"[\u2460-\u24ff\u2776-\u2793\u3220-\u3229\u3251-\u325f\u3280-\u3289\u32b1-\u32bf"
                       r"\u2070-\u2079\u00b9\u00b2\u00b3\u2080-\u2089\u2020\u2021]")
# A superscript or subscript in HTML form: a note marker.
SUP_RE = re.compile(r"<(sup|sub)>([^<\n]{0,40})</\1>", re.I)
# A Markdown rule: a line of three or more of one of - * _.
RULE_RE = re.compile(r"[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*(?=\n|$)")
LATEX_RE = re.compile(r"\$[^$\n]{0,200}\$")
# An engine's LaTeX that is a product of whole figures (engine A's `$ 850
# \times 1168 $`, an imprint page's size): one run, spelled as printed
# (850×1168).  × falls in the Latin range of ALNUM and LATIN, so the run
# is one with engine B's plain 850×1168, and is Western text to written():
# an ASCII mark engine A writes beside it stays ASCII, as beside a plain
# reading of the size.
PRODUCT_RE = re.compile(r"\$\s*[0-9]+(?:\s*\\times\s*[0-9]+)+\s*\$")
# An engine's LaTeX that holds the command \Box alone, once or more (engine
# B's `$ \Box\Box $` where engine A reads □□, the squares a book prints for
# characters it lacks): one run of that many □ (MISSING).
BOX_RE = re.compile(r"\$\s*(?:\\Box\s*)+\$")
# Engine B's box for a figure it does not transcribe: two corners on a
# 0-1000 grid, on a line of its own or after a cell's <br> - markup.
FIGURE_BOX_RE = re.compile(r"\(\d{1,4},\s?\d{1,4}\),\s?\(\d{1,4},\s?\d{1,4}\)")
SUPERSCRIPT_RE = re.compile(r"\^\{[^}\n]{0,40}\}|\t?extcircled\{[^}\n]{0,40}\}|\\textcircled\{[^}\n]{0,40}\}")
COMMAND_RE = re.compile(r"\\[A-Za-z]+|[{}]")
# A command whose backslash-t an engine wrote as a tab (engine B's circled
# figure in LaTeX, on 21 pages of the 609-page modern book): tokens() reads
# such LaTeX as unknown markup, _unmarked as the note marker it is.
TAB_COMMAND_RE = re.compile(r"\t[A-Za-z]+(?=\{)")
HTML_ROW_RE = re.compile(r"</t[dh]>\s*</tr>\s*(?:</?t(?:body|head)>\s*)*(?:<tr[^>]*>\s*)?<t[dh][^>]*>", re.I)
HTML_CELL_RE = re.compile(r"</t[dh]>\s*<t[dh][^>]*>", re.I)
HTML_CELL_END_RE = re.compile(r"</t[dh]>(?:\s*</tr>)?(?:\s*</t(?:body|head)>)?(?:\s*</table>)?", re.I)
HTML_CELL_START_RE = re.compile(r"(?:<table[^>]*>\s*)?(?:<t(?:body|head)>\s*)?(?:</caption>\s*)?(?:<tr[^>]*>\s*)?<t[dh][^>]*>",
                                re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"</?[A-Za-z][^<>\n]{0,60}>")
PAGE_ROW_RE = re.compile(r"[ \t]*\|[ \t]*\n(?:[ \t]*\|?[ \t:|-]*\|[ \t]*\n)?[ \t]*\|[ \t]*")
PAGE_CELL_RE = re.compile(r"[ \t]*\|[ \t]*")
STRUCT_RE = re.compile(r"[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|>[ \t]*)+")
LEADER_RE = re.compile(r"\u2026+|\u22ef+|\.{2,}|\u00b7{2,}|\u30fb{2,}|\u2022{2,}")
DASH_RE = re.compile(r"[\u2014\u2015\u2500\u2013\u2012]+")
SPACE_RE = re.compile(r"[ \t\u3000]+")

# Mark classes: the forms of one printed mark that the engines and the
# punctuator write (width, vertical presentation, the shape of a title or
# square bracket).
_CLASSES = {
    "open-round": "\uff08(\ufe35", "close-round": "\uff09)\ufe36",
    "open-square": "\u3014\uff3b[\u3010\u3016", "close-square": "\u3015\uff3d]\u3011\u3017",
    "open-title": "\u300a\u3008<", "close-title": "\u300b\u3009>",
    "open-quote": "\u300c\u300e\ufe41\ufe43\u201c\u2018", "close-quote": "\u300d\u300f\ufe42\ufe44\u201d\u2019",
    "comma": "\uff0c,", "stop": "\u3002\uff0e.", "enum": "\u3001", "semi": "\uff1b;", "colon": "\uff1a:\ufe30",
    "question": "\uff1f?", "bang": "\uff01!", "dot": "\u00b7\u2022\u30fb", "wave": "~\uff5e\u301c",
    "star": "*\uff0a\u203b", "amp": "&\uff06", "hyphen": "-\u2010", "slash": "/\uff0f", "times": "\u00d7",
}
MARK_CLASS = {c: name for name, chars in _CLASSES.items() for c in chars}
# A connector a run carries, which the page may hold alone when the run
# was dropped ('-。' for '119-140。', '：×' for '：850×1168').
CONNECTOR_CLASSES = frozenset({"hyphen", "colon", "comma", "stop", "slash", "wave", "dot", "times"})
OPEN_CLASSES = frozenset({"open-round", "open-square", "open-title", "open-quote"})
CLOSE_CLASSES = frozenset({"close-round", "close-square", "close-title", "close-quote"})
RUN_FOLD = str.maketrans({**{chr(0xff10 + k): str(k) for k in range(10)},
                          **{chr(0xff21 + k): chr(0x41 + k) for k in range(26)},
                          **{chr(0xff41 + k): chr(0x61 + k) for k in range(26)},
                          "\uff5e": "~", "\u301c": "~", "\uff0e": ".", "\uff0c": ",", "\uff1a": ":", "\u2019": "'",
                          "\u2010": "-", "\uff05": "%", " ": None})
# The largest gap (in tokens) a rule weaves; a longer one is a table or a
# block of its own, and a doubt.
TOKEN_LIMIT = 80
# Engine A's labels for a block of a page's furniture.
FURNITURE_LABELS = frozenset({"header", "footer", "number", "aside_text", "seal"})
# A run of this many digits or fewer that opens a line in engine A is a
# footnote's own number or an item's, not body print, as the marks stage
# reads one (rr.LINE_FIGURE_GAP).  Four digits are a year: measured on the
# eleven books of the corpus with engine A readings (2026-10-06), the four
# lines of engine A that open with four digits and go on with a character
# are all an imprint page's dates of edition and printing (1980年11月 on
# a multi-volume archival compilation 2, 1982年12月 on a multi-volume archival compilation 3), whose years a limit of four left out;
# the other lines that open with four digits hold them alone (77: a folio
# in a block engine A labels a number, which engine B does not read, and
# a title page's year) but one, a year ending a bibliography entry at a
# page's head (1987。), which stays out as a run on a line of its own.
LINE_FIGURE_DIGITS = 3


@dataclass
class Token:
    kind: str
    raw: str
    start: int
    end: int
    cls: str = ""

    @property
    def key(self) -> tuple[str, str] | None:
        """What the token compares as; None for a token set aside."""
        if self.kind == RUN:
            return (RUN, self.raw.translate(RUN_FOLD))
        if self.kind in (MARK, MARKER, OTHER):
            return (self.kind, self.cls)
        if self.kind in (CELL, ROW):
            return (self.kind, "")
        return None


def _marker_value(text: str) -> str:
    """A note marker's figures: circled or superscript digits folded to
    ASCII (NFKC), other marks left out."""
    import unicodedata
    folded = unicodedata.normalize("NFKC", text)
    return "".join(c for c in folded if c.isdigit()) or folded.strip()


def tokens(text: str, side: str, line_start: bool = False) -> list[Token]:
    """TEXT as tokens; SIDE is "page", "a" or "b" (the engine's markup is
    read as such, the page's Markdown as structure).  LINE_START: TEXT opens
    a line (a gap's text follows a character, and does not, unless it is the
    page's or the engine's first)."""
    out: list[Token] = []
    at = 0
    n = len(text)
    while at < n:
        c = text[at]
        # Structure at a line's start: the page's heading, bullet and quote
        # markers and its rules; engine B's heading markers.
        if line_start and side != "a":
            m = RULE_RE.match(text, at)
            if m:
                out.append(Token(STRUCT if side == "page" else MARKUP, m.group(), at, m.end()))
                at = m.end()
                line_start = False
                continue
            m = STRUCT_RE.match(text, at)
            if m and m.end() > at:
                out.append(Token(STRUCT if side == "page" else MARKUP, m.group(), at, m.end()))
                at = m.end()
                line_start = False
                continue
        line_start = False
        if c == "\n":
            out.append(Token(BREAK, c, at, at + 1))
            at += 1
            line_start = True
            continue
        if side == "a" and text.startswith("\\n", at):
            out.append(Token(BREAK, "\\n", at, at + 2))
            at += 2
            line_start = True
            continue
        m = SPACE_RE.match(text, at)
        if m:
            out.append(Token(SPACE, m.group(), at, m.end()))
            at = m.end()
            continue
        if side == "b" and c == "(":
            m = FIGURE_BOX_RE.match(text, at)
            if m:
                out.append(Token(MARKUP, m.group(), at, m.end()))
                at = m.end()
                continue
        if side != "page" and c == "$":
            m = PRODUCT_RE.match(text, at)
            if m:
                out.append(Token(RUN, "\u00d7".join(re.findall(r"[0-9]+", m.group())), at, m.end()))
                at = m.end()
                continue
            m = BOX_RE.match(text, at)
            if m:
                out.append(Token(RUN, MISSING * m.group().count("\\Box"), at, m.end()))
                at = m.end()
                continue
        if side != "page" or c == "$":
            m = LATEX_RE.match(text, at)
            if m:
                inner = COMMAND_RE.sub("", m.group()[1:-1]).replace("^", "").strip()
                if not inner:
                    out.append(Token(MARKUP, m.group(), at, m.end()))
                elif MARKER_RE.search(inner) or re.fullmatch(r"[(\uff08]?[0-9]{1,3}[)\uff09]?", inner) \
                        or re.fullmatch(r"[*\u2217]", inner):
                    out.append(Token(MARKER, m.group(), at, m.end(), _marker_value(inner)))
                else:
                    out.append(Token(OTHER, m.group(), at, m.end(), "latex"))
                at = m.end()
                continue
        m = SUPERSCRIPT_RE.match(text, at)
        if m:
            out.append(Token(MARKER, m.group(), at, m.end(), _marker_value(m.group())))
            at = m.end()
            continue
        if c == "\\" or c in "{}":
            m = COMMAND_RE.match(text, at)
            if m:
                out.append(Token(OTHER if m.group() not in ("{", "}") and side != "page" else MARKUP,
                                 m.group(), at, m.end(), "latex"))
                at = m.end()
                continue
        if c == "<":
            m = SUP_RE.match(text, at)
            if m:
                out.append(Token(MARKER, m.group(), at, m.end(), _marker_value(m.group(2))))
                at = m.end()
                continue
            m = HTML_ROW_RE.match(text, at)
            if m:
                out.append(Token(ROW, m.group(), at, m.end()))
                at = m.end()
                continue
            m = HTML_CELL_RE.match(text, at)
            if m:
                out.append(Token(CELL, m.group(), at, m.end()))
                at = m.end()
                continue
            m = HTML_CELL_END_RE.match(text, at) or HTML_CELL_START_RE.match(text, at)
            if m:
                out.append(Token(CELL, m.group(), at, m.end()))
                at = m.end()
                continue
            m = BR_RE.match(text, at)
            if m:
                out.append(Token(BREAK, m.group(), at, m.end()))
                at = m.end()
                line_start = True
                continue
            m = TAG_RE.match(text, at)
            if m:
                out.append(Token(MARKUP, m.group(), at, m.end()))
                at = m.end()
                continue
        if c == "|" or (c in " \t" and "|" in text[at:at + 3]):
            m = PAGE_ROW_RE.match(text, at)
            if m:
                out.append(Token(ROW, m.group(), at, m.end()))
                at = m.end()
                line_start = True
                continue
            m = PAGE_CELL_RE.match(text, at)
            if m:
                out.append(Token(CELL, m.group(), at, m.end()))
                at = m.end()
                continue
        m = MARKER_RE.match(text, at)
        if m:
            out.append(Token(MARKER, m.group(), at, m.end(), _marker_value(m.group())))
            at = m.end()
            continue
        m = RUN_RE.match(text, at)
        if m:
            out.append(Token(RUN, m.group(), at, m.end()))
            at = m.end()
            continue
        m = LEADER_RE.match(text, at)
        if m:
            out.append(Token(MARK, m.group(), at, m.end(), "leader"))
            at = m.end()
            continue
        m = DASH_RE.match(text, at)
        if m:
            out.append(Token(MARK, m.group(), at, m.end(), "dash"))
            at = m.end()
            continue
        if side == "b" and c in "*_":
            out.append(Token(MARKUP, c, at, at + 1))
            at += 1
            continue
        if c == "#":
            out.append(Token(MARKUP if side == "b" else MARK, c, at, at + 1, "hash"))
            at += 1
            continue
        out.append(Token(MARK, c, at, at + 1, MARK_CLASS.get(c, c)))
        at += 1
    return out


def keys(toks: Sequence[Token], cells: bool = True) -> list[tuple[str, str]]:
    """The comparable tokens of TOKS: runs, marks by class, markers and
    unknown markup; cell and row boundaries when CELLS."""
    return [t.key for t in toks if t.key is not None and (cells or t.kind not in (CELL, ROW))]


def coarse(key: tuple[str, str]) -> tuple[str, str]:
    """A mark's key with the opening and closing shapes as one class each
    (the page writes 〈 where the engines have 《)."""
    kind, cls = key
    if kind == MARK:
        if cls in OPEN_CLASSES:
            return (MARK, "open")
        if cls in CLOSE_CLASSES:
            return (MARK, "close")
    return key


# A connector as a mark, in the form a run holds it in (RUN_FOLD).
_FUSED_MARK = {"comma": ",", "stop": ".", "colon": ":", "hyphen": "-", "slash": "/", "wave": "~"}


def fused(toks: Sequence[Token]) -> str:
    """The print of TOKS as one string: each run folded, each mark by its
    class - a connector as the character a run would hold it as - and two
    runs with no mark between them kept apart; spaces, breaks, markup and
    table boundaries left out.  Two readings that hold the same characters
    and cut them into runs differently are equal: one engine wraps a title
    in emphasis markers, and the point before it then ends the run."""
    out: list[str] = []
    last_run = False
    for t in toks:
        if t.kind == RUN:
            out.append(("\x03" if last_run else "") + t.raw.translate(RUN_FOLD))
            last_run = True
        elif t.kind == MARK:
            out.append("'" if t.raw in ("'", "\u2018", "\u2019") else _FUSED_MARK.get(t.cls) or f"\x01{t.cls}\x01")
            last_run = False
        elif t.kind in (MARKER, OTHER):
            out.append(f"\x02{t.kind}:{t.cls}\x02")
            last_run = False
    return "".join(out)


def agree(a: Sequence[Token], b: Sequence[Token]) -> bool:
    """Whether two readings of a gap hold the same print (table boundaries
    aside)."""
    return keys(a, cells=False) == keys(b, cells=False) or fused(a) == fused(b)


# What two readings of Western text may differ in and still be one reading
# of the print: a hyphen between two letters (a word one engine keeps
# hyphenated at the printed line's end and the other joins - Conten-tious /
# Contentious, Pre-ss / Press on the modern book's notes and bibliography) and
# an apostrophe's form (fused folds the curly ones).  Not a hyphen between
# figures: 91-104 is a range and 91104 a number.  But a dash between two
# figures is a hyphen's other form, the range one (a multi-volume archival compilation 5's imprint page:
# engine A 1—5,000, engine B 1-5,000).
_LOOSE_HYPHEN = re.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])")
_FIGURE_DASH = re.compile(r"(?<=[0-9])\x01dash\x01(?=[0-9])")


def loose(toks: Sequence[Token]) -> str:
    """The print of TOKS as fused() gives it, with the hyphens inside words
    out and a dash between figures a hyphen."""
    return _LOOSE_HYPHEN.sub("", _FIGURE_DASH.sub("-", fused(toks)))


def nearly(a: Sequence[Token], b: Sequence[Token]) -> bool:
    """Whether two readings that do not agree() differ in hyphens inside words,
    in a dash or a hyphen between figures, or in the form of an apostrophe
    only (loose).  Such a gap is written as engine A reads it, with a doubt
    naming both readings: on the modern book one such hyphen kept the whole
    of a 44-run footnote and a 176-run bibliography page out, while every
    other run of them agreed."""
    return runs_of(a) != [] and runs_of(b) != [] and loose(a) == loose(b)


# N7: a reading cut into units - a word (letters and figures, with the
# apostrophes and hyphens inside it), a mark's class (a quotation mark as
# fused() writes it, "'"), a note marker or unknown markup, and any other
# character alone (a kana, a point) - with the spaces, breaks and run
# boundaries between them set aside.  A word of Latin letters alone, as
# _key folds it; the shortest word whose letter apart N7 writes.
_UNIT = re.compile(rf"\x01[^\x01]*\x01|\x02[^\x02]*\x02|[{LATIN}0-9]+(?:['-][{LATIN}0-9]+)*|.", re.S)
_LETTERS = re.compile(rf"[{LATIN}]+(?:['-][{LATIN}]+)*")
SLIGHT_LETTERS = 4


def _units(toks: Sequence[Token]) -> list[str]:
    """TOKS as fused() reads them cut into _UNIT's units; a space, inside a
    run or between tokens, kept as a boundary between words."""
    spaced = [Token(RUN, "\x04", t.start, t.end) if t.kind == SPACE
              else Token(t.kind, t.raw.replace(" ", "\x04"), t.start, t.end, t.cls) if t.kind == RUN else t for t in toks]
    return [u for u in _UNIT.findall(fused(spaced)) if u not in ("\x03", "\x04")]


def _key(word: str) -> str:
    """A word as N7 compares it: its case, apostrophes and hyphens set aside."""
    return re.sub("['-]", "", word.lower())


def _one_letter(x: str, y: str) -> bool:
    """Whether X and Y are one letter apart: one more, one less or one other."""
    if len(x) < len(y):
        x, y = y, x
    if len(x) == len(y):
        return sum(p != q for p, q in zip(x, y)) == 1
    return len(x) == len(y) + 1 and any(x[:i] + x[i + 1:] == y for i in range(len(x)))


def slightly(a: Sequence[Token], b: Sequence[Token]) -> str | None:
    """N7: how two readings of Western text are apart, where they are only
    slightly apart - both hold Latin letters and, cut into _UNIT's units,
    they are the same unit for unit but for words of Latin letters alone,
    apart in case or in an apostrophe or hyphen inside the word, and at
    most one of them, SLIGHT_LETTERS or more letters in each, one letter
    apart (_key).  The words as each engine spells them are named.  None
    otherwise: a figure or a word that holds one, a mark (a quotation mark
    too), a kana, or a unit more or less."""
    if not all(any(re.search(rf"[{LATIN}]", t.raw) for t in runs_of(toks)) for toks in (a, b)):
        return None
    ua, ub = _units(a), _units(b)
    apart = [(x, y) for x, y in zip(ua, ub) if x != y]
    if len(ua) != len(ub) or not all(_LETTERS.fullmatch(x) and _LETTERS.fullmatch(y) for x, y in apart):
        return None
    letters = [(x, y) for x, y in apart if _key(x) != _key(y)]
    if len(letters) > 1 or letters and not (min(len(_key(w)) for w in letters[0]) >= SLIGHT_LETTERS
                                            and _one_letter(*map(_key, letters[0]))):
        return None
    if not apart:
        return "spaces or line breaks apart"
    x, y = (letters or apart)[0]
    more = f", {len(apart) - 1} more word(s) apart" if len(apart) > 1 else ""
    return f"{'a letter' if letters else 'apostrophes, hyphens or case'} apart (engine A {x}, engine B {y}{more})"


def runs_of(toks: Sequence[Token]) -> list[Token]:
    return [t for t in toks if t.kind == RUN]


def show(text: str, limit: int = 60) -> str:
    text = text.replace("\n", "\u23ce").replace("\t", " ")
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


# --- the page as the stage reads it ---------------------------------------------------

@dataclass
class Gap:
    """One gap of the page: between page characters K-1 and K (K = 0 is the
    page's leading edge, K = len(chars) its trailing edge), its span in the
    page text, and what each side holds there (None: the engine is not
    aligned to adjacent characters there)."""
    k: int
    start: int
    end: int
    page: list[Token]
    a: list[Token] | None
    b: list[Token] | None
    a_raw: str | None
    b_raw: str | None
    a_index: int | None
    # Set by weave(): the offsets, in the text it returns, of the brackets
    # it wrote from the engines (the page's own are not listed).
    wrote: list[int] = field(default_factory=list)
    # "start" or "end" when the gap is the page's leading or trailing edge.
    edge: str = ""
    # Set by weave(): how many marks of the page it left out as a run's own.
    dropped: int = 0
    # N5: the spans of the page's tables whose marks no model read
    # (unread_tables), and whether the character before the gap and the one
    # after it lie in an engine A block the punctuator never read (`bare`),
    # both set by cell_gap() where the gap is one whose marks the rule
    # writes; stretches() then writes a stretch of marks alone that lies in
    # one of them beside such a character, lists in `cells` the offsets (in
    # the text written) of the marks it wrote that N1 leaves, and counts in
    # `cell_lost` the marks of such stretches it could not write.
    tables: Sequence[tuple[int, int]] = ()
    bare: tuple[bool, bool] = (False, False)
    cells: list[int] = field(default_factory=list)
    cell_lost: int = 0


class PageRuns:
    """A page's text, the engines' bodies and their alignment, read once."""

    def __init__(self, pd: rr.PageData, folio_forms: set[str], beside_marks: bool = False):
        self.pd = pd
        self.scan = pd.scan
        # N8 is off on a book whose profile saw marks beside the characters.
        self.beside_marks = beside_marks
        self.text = pd.text
        self.align = ra.align_page(pd.text, pd.a_body, pd.b_body)
        self.s = self.align.sealed
        self.a = self.align.a
        self.b = self.align.b
        self.to_a = self.align.sealed_to_a
        self.to_b = self.align.sealed_to_b
        self.folio_forms = folio_forms
        # The folio whole, in each form (folio_forms holds every piece too).
        longest = max((len(form) for form in folio_forms), default=0)
        self.folio_whole = {form for form in folio_forms if len(form) == longest}
        self._block_of = rr.a_blocks_of(pd) if pd.a_blocks and pp.CJK(pd.a_body) else []
        # N5: the page's tables whose marks no model read from the image.
        self.tables = unread_tables(self)

    def span(self, k: int) -> tuple[int, int]:
        start = 0 if k == 0 else self.s.offsets[k - 1] + 1
        end = self.s.offsets[k] if k < len(self.s.offsets) else len(self.text)
        return start, end

    def _engine_gap(self, stream: ra.Stream, to: Sequence[int | None], k: int) -> tuple[str | None, int | None]:
        """What the engine holds in the page's gap K, when the page
        characters on both sides are aligned to adjacent characters of the
        engine (an edge: to the engine's first or last character)."""
        n = len(self.s.chars)
        if not stream.chars:
            return None, None
        if k == 0:
            j = to[0] if n else None
            if j != 0:
                return None, None
            return stream.text[:stream.offsets[0]], 0
        if k == n:
            j = to[n - 1]
            if j is None or j != len(stream.chars) - 1:
                return None, None
            return stream.text[stream.offsets[j] + 1:], j + 1
        i, j = to[k - 1], to[k]
        if i is None or j is None or j != i + 1:
            return None, None
        return stream.text[stream.offsets[i] + 1:stream.offsets[j]], j

    def gap(self, k: int) -> Gap:
        start, end = self.span(k)
        a_raw, ia = self._engine_gap(self.a, self.to_a, k)
        b_raw, _ = self._engine_gap(self.b, self.to_b, k)
        return Gap(k, start, end, tokens(self.text[start:end], "page", k == 0),
                   tokens(a_raw, "a", k == 0) if a_raw is not None else None,
                   tokens(b_raw, "b", k == 0) if b_raw is not None else None, a_raw, b_raw, ia,
                   edge="start" if k == 0 else "end" if k == len(self.s.chars) else "")

    def gaps(self) -> Iterable[Gap]:
        for k in range(len(self.s.chars) + 1):
            yield self.gap(k)

    def context(self, k: int, width: int = 4) -> tuple[str, str]:
        return self.s.chars[max(0, k - width):k], self.s.chars[k:k + width]

    def a_block(self, k: int) -> Mapping[str, Any] | None:
        """Engine A's block holding the engine character the page character
        K aligns to; None where K aligns to none or the blocks are not known."""
        j = self.to_a[k] if 0 <= k < len(self.to_a) else None
        if j is None or not self._block_of or j >= len(self._block_of):
            return None
        block = self._block_of[j]
        return self.pd.a_blocks[block] if 0 <= block < len(self.pd.a_blocks) else None

    def a_label(self, k: int) -> str | None:
        """Engine A's block label of the block holding the engine character
        the page character K aligns to."""
        block = self.a_block(k)
        return block.get("label") if block is not None else None

    def line_span(self, offset: int) -> tuple[int, int]:
        start = self.text.rfind("\n", 0, offset) + 1
        end = self.text.find("\n", offset)
        return start, (len(self.text) if end < 0 else end)


def table_spans(text: str) -> list[tuple[int, int]]:
    """The spans of TEXT's tables as the skill reads them
    (_tables.find_tables): a Markdown table from its label row and rule to
    its last row, an HTML table from its opening tag to its closing one.  A
    line that merely holds a pipe is no table."""
    return [(table.start, table.end) for table in _tables.find_tables(text)]


def unread_marks(record: Mapping[str, Any]) -> bool:
    """Whether phase 3 punctuated the page block by block (the seal's
    record), so that a block it gave no pass (an engine A table block,
    proofread_pages.FURNITURE_LABELS) holds engine A's marks, none, or the
    （） the structure pass's lock lets it add.  Not on a page punctuated
    whole (the seal's doubt `punctuation-whole-page`): there the formatter
    wrote the tables' marks from the page image, and an empty gap may be its
    reading, as in running text.  A seal with no `doubts` key is an older
    one that recorded none: the page is taken as read unless its
    punctuationPasses show the block passes (a page with passes was not
    punctuated whole).  A table rebuilt from its crop is set aside per table
    (unread_tables)."""
    doubts = record.get("doubts")
    if doubts is None:
        return bool(record.get("punctuationPasses"))
    return not any(isinstance(d, Mapping) and d.get("kind") == "punctuation-whole-page" for d in doubts)


def unread_tables(page: PageRuns) -> list[tuple[int, int]]:
    """N5: the spans of PAGE's tables whose marks no model read from the
    image - none on a page not punctuated block by block (unread_marks), and
    not a table that holds a character of a block the structure pass
    rebuilt from its crop (the seal's tablesRebuilt, outcome accepted: its
    block and the blocks of its region, by engine A's layout index): the
    rebuilt table holds the brackets and commas the model saw there, so an
    empty gap of it may be that reading.  The other tables of such a page
    are read as any (one rebuilt table used to switch the rule off for the
    whole page).  A rebuilt block that no table character aligns to names
    no table (a page character aligned to no block has no label either, and
    cell_gap keeps the rule off it)."""
    if not unread_marks(page.pd.record):
        return []
    spans = table_spans(page.text)
    rebuilt = {index for r in page.pd.record.get("tablesRebuilt") or [] if isinstance(r, Mapping)
               and r.get("outcome") == "accepted"
               for index in (r.get("block"), *(r.get("region") or [])) if isinstance(index, int)}
    if not rebuilt:
        return spans

    def of_rebuilt(k: int) -> bool:
        block = page.a_block(k)
        return block is not None and block.get("index") in rebuilt

    return [(start, end) for start, end in spans
            if not any(of_rebuilt(k) for k in range(bisect_left(page.s.offsets, start), bisect_left(page.s.offsets, end)))]


def line_figure(text: str) -> bool:
    """Whether TEXT begins with a figure of its own: one to three digits
    (LINE_FIGURE_DIGITS) that end it or stand before a space (`18`,
    `18 Fernand Braudel`)."""
    return re.match(rf"[0-9\uff10-\uff19]{{1,{LINE_FIGURE_DIGITS}}}(?: |$)", text) is not None


def line_initial_figure(a_toks: Sequence[Token], run: Token, edge_start: bool = False) -> bool:
    """Whether RUN opens a line of engine A's text with a figure of its own
    (line_figure), after a break - or at the engine's text start when the
    gap is the page's leading edge - with nothing but opening brackets
    before it on the line: a footnote's or item's own number, which this
    stage never writes."""
    if not line_figure(run.raw):
        return False
    before = [t for t in a_toks if t.end <= run.start and t.kind not in (SPACE, MARKUP)]
    while before and before[-1].kind == MARK and before[-1].cls in OPEN_CLASSES:
        before.pop()
    if not before:
        return edge_start
    return before[-1].kind == BREAK


def note_like(a_toks: Sequence[Token]) -> bool:
    """Whether engine A's gap ends with a figure of one to three digits right
    after the mark that ends a sentence (and the bracket or quote closing
    it): the place of a note number, which the engines may have read as
    plain figures both."""
    items = [t for t in a_toks if t.kind not in (MARKUP,)]
    while items and items[-1].kind in (SPACE, BREAK):
        items.pop()
    if len(items) < 2 or items[-1].kind != RUN or not re.fullmatch(r"[0-9\uff10-\uff19]{1,3}", items[-1].raw):
        return False
    before = items[:-1]
    while before and before[-1].kind == MARK and before[-1].cls in CLOSE_CLASSES:
        before.pop()
    return bool(before) and before[-1].kind == MARK and before[-1].cls in ("stop", "question", "bang") \
        and before[-1].raw not in ".\uff0e"


def _carried(mark: Token, a_runs: Sequence[Token], a_marks: Sequence[Token], b_toks: Sequence[Token] | None) -> bool:
    """Whether MARK, a mark of the page with no counterpart among engine A's,
    is what is left of a run: a connector that one of the gap's runs itself
    holds (the hyphen of 119-140, the colon of 1989：2, the × of 850×1168),
    and that engine B does not read as a mark of the gap either."""
    if mark.kind != MARK or mark.cls not in CONNECTOR_CLASSES:
        return False
    forms = _CLASSES.get(mark.cls, "")
    if not any(c in run.raw for run in a_runs for c in forms):
        return False
    count = sum(1 for t in a_marks if t.kind == MARK and t.cls == mark.cls)
    return b_toks is None or sum(1 for t in b_toks if t.kind == MARK and t.cls == mark.cls) <= count


def _match(a_marks: Sequence[Token], p_marks: Sequence[Token], a_runs: Sequence[Token], b_toks: Sequence[Token] | None,
           leftmost: bool = True) -> tuple[dict[int, int], set[int]] | None:
    """The page's marks matched to engine A's, in order: each to the first
    (LEFTMOST) or last engine mark still free of its own class - of its
    coarse class (any opening, any closing bracket) only where engine A
    holds none of the page's class in the gap.  Returns engine mark ->
    page mark and the page marks set aside as a run's own (_carried); None
    when another page mark has no counterpart."""
    matched: dict[int, int] = {}
    remnants: set[int] = set()
    free = list(range(len(a_marks))) if leftmost else list(range(len(a_marks) - 1, -1, -1))
    for pi in (range(len(p_marks)) if leftmost else range(len(p_marks) - 1, -1, -1)):
        pt = p_marks[pi]
        exact = any(t.key == pt.key for t in a_marks)
        j = next((j for j in free if (a_marks[j].key == pt.key if exact else coarse(a_marks[j].key) == coarse(pt.key))),
                 None)
        if j is not None:
            matched[j] = pi
            free = free[free.index(j) + 1:]
        elif _carried(pt, a_runs, a_marks, b_toks):
            remnants.add(pi)
        else:
            return None
    return matched, remnants


# The reason weave() gives where a mark of the page has no counterpart among
# engine A's (N6 reads such a gap again).
MARKS_NOT_ENGINES = "the page's marks are not the engines'"


def weave(gap: Gap) -> tuple[str, str, list[str]] | tuple[None, str, list[str]]:
    """The gap's new text with the engines' agreed runs woven in: (text,
    tier, runs), or (None, why, runs) when the gap has no clean place.  The
    caller has checked that engine A and engine B agree on the gap's runs
    and marks and that the page holds no run there.  Sets gap.wrote (the
    brackets written from the engines) and gap.dropped (the page's marks
    left out as a run's own)."""
    a_toks, p_toks = gap.a or [], gap.page
    a_runs = runs_of(a_toks)
    a_marks = [t for t in a_toks if t.kind in (MARK, CELL, ROW)]
    p_marks = [t for t in p_toks if t.kind in (MARK, CELL, ROW)]
    names = [t.raw for t in a_runs]
    gap.wrote, gap.dropped, gap.cells, gap.cell_lost = [], 0, [], 0
    if any(t.kind in (MARKER, OTHER) for t in a_toks) or any(t.kind in (MARKER, OTHER) for t in p_toks):
        return None, "a note marker or unknown markup stands in the gap", names
    if any(line_initial_figure(a_toks, run, gap.k == 0) for run in a_runs):
        return None, "a figure opens a line in engine A", names
    if note_like(a_toks):
        return None, "a short figure right after a sentence's end may be a note number", names
    a_lines = _lines_of(a_toks)
    if len(a_lines) > 1 and (any(runs_of(line) for line in a_lines[1:-1])
                             or (gap.edge == "end" and any(runs_of(line) for line in a_lines[1:]))
                             or (gap.edge == "start" and any(runs_of(line) for line in a_lines[:-1]))):
        return None, "a run stands on a line of its own in engine A", names
    found = _match(a_marks, p_marks, a_runs, gap.b)
    if found is None:
        return None, MARKS_NOT_ENGINES, names
    matched, remnants = found
    tier = "T1" if len(matched) == len(a_marks) else "T2"
    if tier == "T2":
        if remnants:
            return None, MARKS_NOT_ENGINES, names
        new, why, names = stretches(gap, a_marks, p_marks, matched, names)
        # The place is unique when the page's marks matched from the other
        # end give the same text.
        other = _match(a_marks, p_marks, a_runs, gap.b, leftmost=False)
        if new is not None and other is not None and other[0] != matched:
            wrote, cells, lost = list(gap.wrote), list(gap.cells), gap.cell_lost
            again = stretches(gap, a_marks, p_marks, other[0], names)[0]
            gap.wrote, gap.cells, gap.cell_lost = wrote, cells, lost
            if again != new:
                return None, "the run's place among the page's marks is not unique", names
        return new, why, names
    # T1: every mark of the engines stands on the page.  Each run goes after
    # the engine mark before it (its page match) or before the one after it;
    # on the page side of a break where engine A breaks the line beside it.
    if not a_marks and any(t.kind in (BREAK, STRUCT) for t in p_toks) and not any(t.kind == BREAK for t in a_toks):
        return None, "the run's place among the page's marks is not unique", names
    # A connector the page holds as a run's own stays in the page's form: the
    # run is written with it in place of engine A's glyph for it (a
    # citation's 5：3, printed full width where engine A reads 5:3).  But a
    # comma, which a run holds as ASCII alone (a figure's thousands comma,
    # 14,625; a point or comma after a letter): the run as engine A spells
    # it (measured 2026-10-26 on the page images: 14,625, 15,625 and 6,500
    # of three imprint pages printed half width, where the page's 14，625
    # had been written; 3,224 in an archive-citing book's body text too).
    spelled = [run.raw for run in a_runs]
    done = [0] * len(spelled)
    for pi in sorted(remnants):
        forms = _CLASSES.get(p_marks[pi].cls, "")
        for n, text in enumerate(spelled):
            hit = next((i for i in range(done[n], len(text)) if text[i] in forms), None)
            if hit is not None:
                if p_marks[pi].cls != "comma":
                    spelled[n] = text[:hit] + p_marks[pi].raw + text[hit + 1:]
                done[n] = hit + 1
                break
    positions: list[tuple[int, str]] = []   # (offset in the gap text, run text)
    for run, run_text in zip(a_runs, spelled):
        prev = max((idx for idx, t in enumerate(a_marks) if t.end <= run.start), default=None)
        nxt = min((idx for idx, t in enumerate(a_marks) if t.start >= run.end), default=None)
        between_prev = [t for t in a_toks if (prev is None or t.start >= a_marks[prev].end) and t.end <= run.start]
        break_before = any(t.kind == BREAK for t in between_prev)
        if prev is not None:
            anchor = p_marks[matched[prev]]
            at = anchor.end
            if break_before:
                # Past the page's break and the structure that opens the next line.
                for t in p_toks:
                    if t.start >= anchor.end and t.kind in (SPACE, BREAK, STRUCT):
                        at = t.end
                    elif t.start >= anchor.end:
                        break
        elif nxt is not None:
            anchor = p_marks[matched[nxt]]
            between_next = [t for t in a_toks if t.start >= run.end and t.end <= a_marks[nxt].start]
            # Before the anchor: before the page's break too where engine A
            # breaks the line after the run, else after it, before its spaces.
            apart = (SPACE, BREAK, STRUCT) if any(t.kind == BREAK for t in between_next) else (SPACE,)
            at = anchor.start
            for t in reversed(p_toks):
                if t.end <= anchor.start and t.kind in apart:
                    at = t.start
                elif t.end <= anchor.start:
                    break
        else:
            at = 0
            if break_before:
                for t in p_toks:
                    if t.kind in (SPACE, BREAK, STRUCT):
                        at = t.end
                    else:
                        break
        positions.append((at, run_text))
    if len({at for at, _ in positions}) != len(positions):
        if remnants:
            return None, "two runs share one place on the page", names
        # Two runs with no mark between them: written as engine A has them.
        new, why, names = stretches(gap, a_marks, p_marks, matched, names)
        return new, ("T1" if new is not None else why), names
    # Build the text: the page's tokens, the runs' own marks dropped, runs
    # inserted.
    drop = {p_marks[pi].start for pi in remnants}
    inserts = dict(positions)
    gap_text = "".join(t.raw for t in p_toks)
    out: list[str] = []
    for offset in range(len(gap_text) + 1):
        if offset in inserts:
            run_text = inserts[offset]
            # A space between a run and a table cell's pipe.
            before_c = out[-1] if out else ""
            after_c = gap_text[offset:offset + 1]
            if before_c == "|" and not run_text.startswith(" "):
                out.append(" ")
            out.append(run_text)
            if after_c == "|" and not run_text.endswith(" "):
                out.append(" ")
        if offset < len(gap_text):
            if offset in drop:
                continue
            out.append(gap_text[offset])
    gap.dropped = len(remnants)
    return "".join(out), tier, names


def written(part: Sequence[Token], full_round: bool = False,
            spaced_after: Iterable[int] = ()) -> tuple[str, list[int]]:
    """A stretch of engine A's gap as this stage writes it, and the offsets
    of the brackets in it: each run as spelled, each mark as engine A wrote
    it - an ASCII mark in its ordinary full-width form (rr.NORM) unless it
    stands in Western text: beside a run of Latin letters, or, a bracket,
    inside a stretch that holds a run of several Latin words (and not where
    the page writes the gap's round brackets full-width, FULL_ROUND: a pair
    keeps one form).  A space stands between two runs, and where engine A
    has one between two tokens of Western text - a curly quotation mark
    beside a run of Latin letters is Western text too - or a mark of PART
    whose id is in SPACED_AFTER (N6: the page's mark, with the space the
    page holds after it) stands before one."""
    spaced_after = set(spaced_after)
    items = [t for t in part if t.kind in (RUN, MARK)]
    latin = [t.kind == RUN and re.search(rf"[{LATIN}]", t.raw) is not None for t in items]
    prose = any(t.kind == RUN and re.search(rf"[{LATIN}] +[{LATIN}]", t.raw) for t in items)
    forms: list[str] = []
    western: list[bool] = []
    for i, t in enumerate(items):
        if t.kind == RUN:
            forms.append(t.raw)
            western.append(latin[i])
            continue
        raw = t.raw
        ascii_mark = len(raw) == 1 and ord(raw) < 128
        inner = 0 < i < len(items) - 1
        beside = (i > 0 and latin[i - 1]) or (i + 1 < len(items) and latin[i + 1])
        if ascii_mark and raw in "()[]":
            keep = prose and inner and not (full_round and raw in "()")
        else:
            keep = ascii_mark and (beside or (prose and inner))
        if ascii_mark and not keep:
            raw = raw.translate(rr.NORM)
        forms.append(raw)
        western.append((ascii_mark and keep) or (raw in "\u2018\u2019\u201c\u201d" and beside))
    out: list[str] = []
    brackets: list[int] = []
    at = 0
    for i, t in enumerate(items):
        if i:
            previous = items[i - 1]
            spaced = any(x.kind == SPACE for x in part if previous.end <= x.start and x.end <= t.start) \
                or id(previous) in spaced_after
            # Two runs side by side are two words; a note call is set close (1990①).
            if (previous.kind == RUN and t.kind == RUN and "call" not in (previous.cls, t.cls)) \
                    or (spaced and western[i - 1] and western[i]):
                out.append(" ")
                at += 1
        if t.kind == MARK and (MARK_CLASS.get(forms[i]) in OPEN_CLASSES or MARK_CLASS.get(forms[i]) in CLOSE_CLASSES):
            brackets.append(at)
        out.append(forms[i])
        at += len(forms[i])
    return "".join(out), brackets


def _stretch(a_part: Sequence[Token], p_part: Sequence[Token], edge: str = "",
             full_round: bool = False, whole: bool = False) -> tuple[str, list[int], list[int]] | None:
    """One stretch of a gap between two marks the page shares with the
    engines (or the gap's edge): the page's spaces, breaks and structure,
    with what engine A holds on each of its lines there that has a run -
    the run and the marks beside it on that line; a line of marks alone is
    left as the page has it, unless the stretch lies in a table cell (N5,
    WHOLE): its marks are written too, and the offsets of those marks (the
    ones N1 leaves) are the third item returned, after the text and the
    offsets of its brackets.  Before the page's line break goes engine A's
    line before its own, after it the line that follows; so too about the
    page's spaces where a table cell holds nothing else there and engine A
    breaks the line (the print wraps in the cell: the second title's
    opening bracket goes after the space, not before it - a made-up page,
    check_repair_runs cell_checks), and where engine A has a line between
    them with marks of its own the stretch is left as the page has it (none
    of the page's spaces is that line's).  None when a run's side of the
    page's break is not given (in a table cell, a mark's), and at a page's
    EDGE ("start", "end") when engine A has the run on a line of its own
    beyond the page's first or last line."""
    p_raw = "".join(t.raw for t in p_part)
    lines = _lines_of(a_part)

    def wanted(line: Sequence[Token]) -> bool:
        return bool(runs_of(line)) or (whole and any(t.kind == MARK for t in line))

    def cell_marks(line: Sequence[Token], text: str) -> list[int]:
        # The marks N1 leaves: those of a line with no run (written() spells
        # such a line as its marks alone).
        if runs_of(line) or not whole:
            return []
        return [t.start for t in tokens(text, "page") if t.kind == MARK]

    if not any(wanted(line) for line in lines):
        return p_raw, [], []
    if len(lines) > 1 and edge == "end" and any(runs_of(line) for line in lines[1:]):
        return None
    if len(lines) > 1 and edge == "start" and any(runs_of(line) for line in lines[:-1]):
        return None
    spaces_only = whole and bool(p_part) and all(t.kind == SPACE for t in p_part)
    if not any(t.kind in (BREAK, STRUCT) for t in p_part) and not (spaces_only and len(lines) > 1):
        out = ""
        brackets: list[int] = []
        cells: list[int] = []
        for line in lines:
            if not wanted(line):
                continue
            text, at = written(line, full_round)
            if out and text and re.match(rf"[{ALNUM}]", text) and re.search(rf"[{ALNUM}]$", out):
                out += " "
            brackets.extend(len(out) + offset for offset in at)
            cells.extend(len(out) + offset for offset in cell_marks(line, text))
            out += text
        return out + p_raw, brackets, cells
    if any(wanted(line) for line in lines[1:-1]):
        return (p_raw, [], []) if spaces_only else None
    if len(lines) < 2:
        return None
    head, head_brackets = written(lines[0], full_round) if wanted(lines[0]) else ("", [])
    tail, tail_brackets = written(lines[-1], full_round) if wanted(lines[-1]) else ("", [])
    shift = len(head) + len(p_raw)
    return (head + p_raw + tail, head_brackets + [shift + at for at in tail_brackets],
            cell_marks(lines[0], head) + [shift + at for at in cell_marks(lines[-1], tail)])


def stretches(gap: Gap, a_marks: Sequence[Token], p_marks: Sequence[Token], matched: Mapping[int, int],
              names: list[str]) -> tuple[str | None, str, list[str]]:
    """T2: every mark of the page is one of the engines', and the engines
    hold more (the punctuator read the page without its runs, and left out
    marks that stand beside them).  The page's marks stay; a stretch between
    two of them that holds a run becomes the engines' - runs and marks in
    engine A's order (_stretch, written) - and a stretch of marks alone is
    left as the page has it - but written where the stretch lies in a table
    whose marks N5 writes (gap.tables, gap.bare, _in_table; gap.cells lists
    the offsets of those marks, gap.cell_lost counts the ones of such a
    stretch that could not be written).  The brackets written are listed in
    gap.wrote (text_runs withholds one that finds no partner on its line)."""
    a_toks, p_toks = gap.a or [], gap.page
    if any(t.kind in (CELL, ROW) and j not in matched for j, t in enumerate(a_marks)):
        return None, MARKS_NOT_ENGINES, names
    pairs = sorted(matched.items())
    full_round = any(t.kind == MARK and t.raw in "\uff08\uff09" for t in p_marks)
    out: list[str] = []
    wrote: list[int] = []
    cells: list[int] = []
    a_at = p_at = 0
    size = lost = 0
    for n in range(len(pairs) + 1):
        a_mark = a_marks[pairs[n][0]] if n < len(pairs) else None
        p_mark = p_marks[pairs[n][1]] if n < len(pairs) else None
        a_part = [t for t in a_toks if t.start >= a_at and (a_mark is None or t.end <= a_mark.start)]
        p_part = [t for t in p_toks if t.start >= p_at and (p_mark is None or t.end <= p_mark.start)]
        edge = gap.edge if (gap.edge == "start" and n == 0) or (gap.edge == "end" and n == len(pairs)) else ""
        whole = _in_table(gap, n == 0, n == len(pairs), p_at)
        # The marks of the stretch that N5 is to write: those of engine A's
        # lines there with no run.
        found = sum(1 for line in _lines_of(a_part) if not runs_of(line) for t in line if t.kind == MARK) if whole else 0
        piece = _stretch(a_part, p_part, edge, full_round, whole)
        if piece is None and whole:
            # The marks' side of the page's line break is not given: the
            # stretch as N1 alone writes it.
            piece = _stretch(a_part, p_part, edge, full_round)
        if piece is None:
            return None, "the run's place among the page's marks is not unique", names
        text, brackets, marks = piece
        lost += found - len(marks)
        if text.strip() and text != "".join(t.raw for t in p_part):
            # A space between what is written and a table cell's pipe.
            if out and out[-1].endswith("|") and not text.startswith(" "):
                text, brackets, marks = " " + text, [at + 1 for at in brackets], [at + 1 for at in marks]
            if p_mark is not None and p_mark.raw.startswith("|") and not text.endswith(" "):
                text += " "
        wrote.extend(size + at for at in brackets)
        cells.extend(size + at for at in marks)
        out.append(text)
        size += len(text)
        if a_mark is not None and p_mark is not None:
            out.append(p_mark.raw)
            size += len(p_mark.raw)
            a_at, p_at = a_mark.end, p_mark.end
    gap.wrote, gap.cells, gap.cell_lost = wrote, cells, lost
    return "".join(out), "T2", names


def _in_table(gap: Gap, first: bool, last: bool, at: int) -> bool:
    """N5: whether a stretch of GAP lies in one of the page's tables
    (gap.tables; none where the rule does not hold for the gap) beside a
    character of a block the punctuator never read (gap.bare): the gap's
    FIRST stretch with the character before the gap, its LAST with the
    character after it - at a page's edge there is none, and what stands
    beyond the page's first or last character is not a cell's - and a
    stretch between two cell boundaries where it stands (AT, its offset in
    the gap's text), when both characters are such."""
    if not gap.tables:
        return False

    def inside(offset: int) -> bool:
        return any(start <= offset < end for start, end in gap.tables)

    before, after = gap.bare
    if first and (gap.edge == "start" or not inside(gap.start - 1) or not before):
        return False
    if last and (gap.edge == "end" or not inside(gap.end) or not after):
        return False
    return first or last or (inside(gap.start + at) and before and after)


# Marks a renderer reads as markup, not print: a table's note sign or
# emphasis (`*`, `_`, a backtick, `#`), a link or a strike (`[`, `]`, `~`),
# a tag or an escape (`<`, `>`, a backslash).
RENDERER_MARKS = frozenset("*_`#~[]\\<>")


def unprintable(text: str) -> bool:
    """Whether TEXT holds a character that prints nothing (Unicode category
    Z: a space of some width; C: a control, a format character such as a
    zero-width space)."""
    import unicodedata
    return any(unicodedata.category(c)[0] in "ZC" for c in text)


def cell_gap(page: PageRuns, gap: Gap) -> bool:
    """N5: whether GAP is one whose marks the rule writes - the page has
    tables whose marks no model read (page.tables) and holds in the gap
    nothing but cell boundaries, spaces and breaks; a character beside the
    gap lies in an engine A block the punctuator never read (its label in
    proofread_pages.FURNITURE_LABELS: a table block; gap.bare, per side -
    the structure pass builds a page table from a punctuated text block too,
    and an empty gap of that table may be the punctuator's reading: scan 606
    of the 609-page book, whose 366 table characters are in a `text` block
    with a pass of 11 marks); both engines hold the same tokens there,
    engine A a mark among them, and neither a note marker or unknown markup
    nor a star or a character a renderer reads as markup (RENDERER_MARKS)
    nor one that prints nothing (unprintable).  Sets gap.tables and
    gap.bare, by which stretches() writes the marks of the stretches that
    lie in a table."""
    if not page.tables or gap.a is None or gap.b is None:
        return False
    if any(t.kind not in (CELL, ROW, SPACE, BREAK, STRUCT) for t in gap.page):
        return False
    marks = [t for t in gap.a if t.kind == MARK]
    if not marks or any(_markup(t) or t.cls == "star" or set(t.raw) & RENDERER_MARKS or unprintable(t.raw) for t in marks) \
            or any(t.kind in (MARKER, OTHER) for t in gap.a + gap.b) or not agree(gap.a, gap.b):
        return False

    def bare(k: int) -> bool:
        return 0 <= k < len(page.s.chars) and page.a_label(k) in pp.FURNITURE_LABELS

    gap.bare = (bare(gap.k - 1), bare(gap.k))
    if not any(gap.bare):
        return False
    gap.tables = page.tables
    return True


# N8: an engine's line-opening structure in a gap (a heading's hashes, a
# list's bullet or number), which is not print.
LINE_STRUCTURE_RE = re.compile(r"(^|\n)\s*(#+|-|\d+\.)\s")


def gap_marks(raw: str) -> str:
    """N8: the marks of a gap's text RAW as two readings are compared -
    tags, line-opening structure, emphasis stars and underscores and ASCII
    white space set aside, an ASCII mark in its full-width form (rr.NORM);
    an ideographic space is print, as K9 reads it."""
    raw = LINE_STRUCTURE_RE.sub(r"\1", TAG_RE.sub("", raw)).replace("*", "").replace("_", "")
    return "".join(c for c in raw if c == "\u3000" or not c.isspace()).translate(rr.NORM)


def agreed_marks(page: PageRuns, gap: Gap) -> str | None:
    """N8 on a GAP of running text where engine A reads no run: engine A's
    marks in their full-width form, where both engines hold the same marks
    there (gap_marks: every one CJK print, rr.CJK_MARKS, an ideographic
    space among them), on one line of engine A, and the page holds none of
    them or only some in their order (an ideographic space of the page's
    among them, so it stays), with white space and nothing else (no line
    break); None otherwise, at the page's edges, where an engine is not
    aligned, and on a book whose profile saw marks beside the characters
    (the module's docstring)."""
    if page.beside_marks or gap.edge or gap.a_raw is None or gap.b_raw is None:
        return None
    held = page.text[gap.start:gap.end]
    if "\n" in gap.a_raw or "\n" in held or not all(
            c.isspace() or c in rr.CJK_MARKS or c in rr.ASCII_MARKS for c in held):
        return None
    marks = gap_marks(gap.a_raw)
    if marks != gap_marks(gap.b_raw) or not marks.strip("\u3000") or not set(marks) <= rr.CJK_MARKS | {"\u3000"}:
        return None
    page_marks = gap_marks(held)
    if page_marks == marks or not rr.subsequence(page_marks, marks):
        return None
    return marks


CELL_BOUNDARY_RE = re.compile(r"\||</t[dh]>", re.I)


def brackets_of(toks: Sequence[Token]) -> int:
    """How many brackets (marks of an opening or closing class) TOKS hold."""
    return sum(1 for t in toks if t.kind == MARK and (t.cls in OPEN_CLASSES or t.cls in CLOSE_CLASSES))


def unpaired(text: str, edits: Sequence[re_.Edit], wrote: dict[int, list[int]],
             lacking: Sequence[tuple[int, int, int]] = (), cells: dict[int, list[int]] | None = None) -> None:
    """Withhold each bracket this stage would write that has no partner on
    its line once all of the page's EDITS stand (WROTE: per edit, by id, the
    offsets in its text of the brackets written from the engines): a
    bracket whose partner the page lacks would open or close nothing.  Two
    brackets with a cell boundary between them (a pipe, `</td>`) are no
    pair, nor are two whose span encloses a gap of LACKING (per gap of the
    page: its span in TEXT and how many brackets engine A holds there) in
    which the page with its edits holds fewer brackets than engine A: where
    the engines differ between two titles of a cell, or the page breaks the
    line where engine A does not, the gap between the titles is not written,
    and the outer brackets would show one title where the print has two
    (made-up pages, check_repair_runs cell_checks; on the 609-page book no
    pair of the stage's output enclosed such a gap, measured 2026-10-02).
    Both brackets of such a pair are withheld where they are this stage's.
    The edit keeps the rest and names what was withheld (and, `glued`,
    where a bracket withheld stood between two letters or figures, which
    then read as one run: 5（1990 as 51990); CELLS (per edit, the offsets of
    the marks N5 writes) follows its text."""
    if not any(wrote.get(id(e)) for e in edits):
        return
    ordered = sorted(edits, key=lambda e: (e.start, e.end))

    def shifted(offset: int, within: bool) -> int:
        # The offset in the trial of a page offset: past the edits before it
        # (WITHIN: an edit that ends there is inside the span measured).
        return offset + sum(len(e.after) - (e.end - e.start) for e in ordered
                            if (e.end <= offset if within else e.start < offset))

    while True:
        pieces: list[str] = []
        ours: dict[int, tuple[re_.Edit, int]] = {}
        at = size = 0
        for edit in ordered:
            pieces.append(text[at:edit.start])
            size += edit.start - at
            for offset in wrote.get(id(edit), ()):
                ours[size + offset] = (edit, offset)
            pieces.append(edit.after)
            size += len(edit.after)
            at = edit.end
        pieces.append(text[at:])
        trial = "".join(pieces)
        gaps = []
        for start, end, held in lacking:
            span = (shifted(start, False), shifted(end, True))
            if brackets_of(tokens(trial[span[0]:span[1]], "page")) < held:
                gaps.append(span)
        alone: list[int] = []
        start = 0
        for line in trial.split("\n"):
            stack: list[tuple[str, int]] = []
            i = 0
            while i < len(line):
                boundary = CELL_BOUNDARY_RE.match(line, i)
                if boundary:
                    alone.extend(at_ for _, at_ in stack)
                    stack.clear()
                    i = boundary.end()
                    continue
                c = line[i]
                cls = MARK_CLASS.get(c)
                if cls in OPEN_CLASSES:
                    stack.append((cls, start + i))
                elif cls in CLOSE_CLASSES:
                    want = "open-" + cls[len("close-"):]
                    j = next((n for n in range(len(stack) - 1, -1, -1) if stack[n][0] == want), None)
                    if j is None:
                        alone.append(start + i)
                    else:
                        alone.extend(at_ for _, at_ in stack[j + 1:])
                        opened = stack[j][1]
                        if any(opened < gap_start and gap_end <= start + i for gap_start, gap_end in gaps):
                            alone.extend((opened, start + i))
                        del stack[j:]
                i += 1
            alone.extend(at_ for _, at_ in stack)
            start += len(line) + 1
        drop: dict[int, list[int]] = {}
        for position in alone:
            if position in ours:
                edit, offset = ours[position]
                drop.setdefault(id(edit), []).append(offset)
        if not drop:
            return
        for edit in edits:
            offsets = sorted(set(drop.get(id(edit), ())))
            if not offsets:
                continue
            withheld = [edit.after[o] for o in offsets]
            after = "".join(c for o, c in enumerate(edit.after) if o not in offsets)
            # Where each bracket stood in the text left: the characters either
            # side of it, the page's beyond the edit's ends.
            spots = [o - sum(1 for x in offsets if x < o) for o in offsets]
            left_of = text[:edit.start] + after
            glued = any(re.search(rf"[{ALNUM}]$", left_of[:edit.start + at]) is not None
                        and re.match(rf"[{ALNUM}]", (after + text[edit.end:])[at:]) is not None for at in spots)
            for listed in (wrote, cells or {}):
                if id(edit) in listed:
                    kept = [o for o in listed[id(edit)] if o not in offsets]
                    listed[id(edit)] = [o - sum(1 for x in offsets if x < o) for o in kept]
            edit.after = after
            edit.evidence = {**edit.evidence, "withheld": list(edit.evidence.get("withheld") or []) + withheld}
            if glued:
                edit.evidence["glued"] = True


def _paired_inside(marks: Sequence[Token]) -> set[int]:
    """The ids of the brackets of MARKS whose partner (a bracket of the same
    kind: round, square, title, quotation) is among MARKS too."""
    inside: set[int] = set()
    stack: list[Token] = []
    for t in marks:
        if t.cls in OPEN_CLASSES:
            stack.append(t)
        elif t.cls in CLOSE_CLASSES:
            want = "open-" + t.cls[len("close-"):]
            j = next((n for n in range(len(stack) - 1, -1, -1) if stack[n].cls == want), None)
            if j is not None:
                inside.update((id(stack[j]), id(t)))
                del stack[j:]
    return inside


def _ascii(raw: str) -> bool:
    return all(ord(c) < 128 for c in raw)


def _partnered(text: str, at: int) -> bool:
    """Whether the bracket at offset AT of TEXT (a page's) has a partner on
    the page among the brackets of its kind alone (_paired_inside), as the
    lint's MARK-UNBALANCED counts them: one of another kind between them
    does not part them (卷〈（弭盜疏〉，）頁 pairs the round ones), nor does
    a paragraph break (a quotation that runs on into the next paragraph)."""
    kind = MARK_CLASS.get(text[at], "-").split("-", 1)[1]
    marks = [t for t in tokens(text, "page") if t.kind == MARK and t.cls in ("open-" + kind, "close-" + kind)]
    paired = _paired_inside(marks)
    return any(id(t) in paired for t in marks if t.start == at)


def engine_marks(gap: Gap, page_text: str) -> tuple[str, list[int], list[str], list[tuple[str, str]]] | None:
    """N6: weave refused the gap because a mark of the page has no
    counterpart in engine A's order (MARKS_NOT_ENGINES), and the page holds
    no run there.  The page's marks there are matched to engine A's as a
    multiset: each to one of its own class, of its coarse class only where
    engine A holds none of its class there (as _match pairs them), or as a
    connector one of engine A's runs holds where engine B reads no more
    such marks than engine A (_carried: the colon of 5:3); one that is none
    of these goes (the user's ruling of 2026-10-06; the caller names it in
    a doubt), standing for a mark of engine A left over, in another class
    (the page's ， for the engines' ： before 850×1168), or for none.  In a
    stretch that is all runs (a Western citation inside a Chinese note; a
    journal's volume, issue and year; an imprint page's figures), the
    punctuator read the crop without the runs and wrote the marks it saw
    about them in an order of its own, and marks where it saw none.  The
    gap becomes engine A's text of it (written), the page's spaces at
    either end kept, each mark of the page in its own form: a mark engine A
    holds too takes the page's form (an ASCII comma of the page stays ASCII
    where engine A writes it full-width, with the space the page holds
    after it), but not where engine A's is ASCII and the page's full-width
    (written then keeps the ASCII one in Western text), and a bracket only
    where its partner lies outside the gap (the page's 〉 for engine A's 》:
    the other half is the page's, and a pair keeps one form; a pair inside
    the gap is engine A's, as written gives it); a connector a run carries
    takes the page's form inside the run, but a comma (a figure's 6,500) and,
    in a run of Latin letters, an ASCII one stay as engine A spells them.
    Returns that text, the offsets of the brackets in it, the page's marks
    it replaces, and those it drops, each with the mark of engine A it
    stands for ("" for none; a bracket stands for none), in order.  A line
    break of engine A's that the page or engine B breaks too stays a break:
    the page's own there, or a paragraph's (a blank line) where the page
    holds none, engine A's text either side of it written on its own; at
    the page's edge, where engine A writes nothing beyond it and the page
    holds no break, it is the edge's and goes.  None where
    engine A breaks the line twice, or alone (it cuts a line where the
    print wraps it: paragraphs; engine B's one break must have the same
    runs before it), where a run holds a letter (Western text: written()
    sets its marks' forms over a whole line), where the page breaks it where engine A
    does not or holds breaks apart, where the page or engine A holds
    structure, a table's boundary, a note marker or markup there (the
    page's own too; engine B's emphasis markers aside), where engine B
    holds a table's boundary, a note marker or unknown markup, or where a
    mark to drop is a bracket with a partner of its kind on the page
    (PAGE_TEXT; _partnered), whose other half would stand alone, or one
    engine B reads more of there than engine A (as fused reads them), whose
    reading backs the page's: the gap stays a doubt."""
    a_toks, p_toks = gap.a or [], gap.page
    if gap.b is None or not runs_of(a_toks) or runs_of(p_toks):
        return None
    apart = (STRUCT, CELL, ROW, MARKER, OTHER, MARKUP)
    cut = [n for n, t in enumerate(a_toks) if t.kind == BREAK]
    p_cut = [n for n, t in enumerate(p_toks) if t.kind == BREAK]
    # At the page's edge, a break of engine A's alone with nothing beyond it
    # is the edge's.
    if len(cut) == 1 and not p_cut and any(edge == gap.edge and not any(t.kind in (RUN, MARK) for t in part) for edge, part
                                           in (("start", a_toks[:cut[0]]), ("end", a_toks[cut[0] + 1:]))):
        a_toks, cut = a_toks[:cut[0]] + a_toks[cut[0] + 1:], []
    # Engine B breaks at engine A's place when the same runs stand before
    # its one break.
    b_cut = [n for n, t in enumerate(gap.b) if t.kind == BREAK]
    b_same = len(b_cut) == 1 and len(runs_of(gap.b[:b_cut[0]])) == len(runs_of(a_toks[:cut[0]])) if cut else False
    if any(t.kind in apart or _markup(t) for t in p_toks) or any(t.kind in apart for t in a_toks) \
            or any(t.kind in (CELL, ROW, MARKER, OTHER) for t in gap.b) or len(cut) > 1 \
            or cut and not p_cut and not b_same \
            or cut and any(re.search("[A-Za-z]", t.raw) for t in runs_of(a_toks)) \
            or p_cut and (not cut or p_cut[-1] - p_cut[0] + 1 != len(p_cut)):
        return None
    a_marks = [t for t in a_toks if t.kind == MARK]
    p_marks = [t for t in p_toks if t.kind == MARK]
    free = list(a_marks)
    carried = {cls: sum(run.raw.count(c) for run in runs_of(a_toks) for c in _CLASSES[cls]) for cls in CONNECTOR_CLASSES}
    page_of: dict[int, Token] = {}
    connectors: list[Token] = []
    dropped: list[Token] = []
    for pt in p_marks:
        exact = any(t.key == pt.key for t in a_marks)
        j = next((n for n, t in enumerate(free) if (t.key == pt.key if exact else coarse(t.key) == coarse(pt.key))), None)
        form = _FUSED_MARK.get(pt.cls) or f"\x01{pt.cls}\x01"
        if j is not None:
            page_of[id(free.pop(j))] = pt
        elif carried.get(pt.cls) and sum(1 for t in gap.b if t.kind == MARK and t.cls == pt.cls) \
                <= sum(1 for t in a_marks if t.cls == pt.cls):
            carried[pt.cls] -= 1
            connectors.append(pt)
        elif fused(gap.b).count(form) > fused(a_toks).count(form) \
                or (pt.cls in OPEN_CLASSES or pt.cls in CLOSE_CLASSES) and _partnered(page_text, gap.start + pt.start):
            return None
        else:
            dropped.append(pt)
    inside = _paired_inside(a_marks)
    spaced = {id(t) for n, t in enumerate(p_toks)
              if t.kind == MARK and n + 1 < len(p_toks) and p_toks[n + 1].kind == SPACE}
    glyph: dict[int, str] = {}
    for t in a_marks:
        pt = page_of.get(id(t))
        if pt is None or pt.raw == t.raw:
            continue
        if t.cls in OPEN_CLASSES or t.cls in CLOSE_CLASSES:
            if id(t) not in inside:
                glyph[id(t)] = pt.raw
        elif not (_ascii(t.raw) and not _ascii(pt.raw)):
            glyph[id(t)] = pt.raw
    # The page's connectors, in their order, into the runs that carry one of
    # their class (as weave writes a run's own connector in the page's form,
    # a comma as engine A spells it).
    spelled = {id(run): run.raw for run in runs_of(a_toks)}
    done = {id(run): 0 for run in runs_of(a_toks)}
    for pt in connectors:
        forms = _CLASSES.get(pt.cls, "")
        for run in runs_of(a_toks):
            text = spelled[id(run)]
            hit = next((i for i in range(done[id(run)], len(text)) if text[i] in forms), None)
            if hit is None:
                continue
            if pt.cls != "comma" and not (re.search(rf"[{LATIN}]", run.raw) and _ascii(text[hit])
                                          and not _ascii(pt.raw)):
                text = text[:hit] + pt.raw + text[hit + 1:]
                spelled[id(run)] = text
            done[id(run)] = hit + 1
            break
    copies = [Token(t.kind, spelled.get(id(t), glyph.get(id(t), t.raw)), t.start, t.end, t.cls) for t in a_toks]
    after = {id(copy) for t, copy in zip(a_toks, copies)
             if id(t) in glyph and id(page_of[id(t)]) in spaced}
    # Engine A's line break stays one: the page's break there, or a
    # paragraph's where the page holds none; each line written on its own.
    parts = [copies[:cut[0]], copies[cut[0] + 1:]] if cut else [copies]
    brk = "".join(t.raw for t in p_toks[p_cut[0]:p_cut[-1] + 1]) if p_cut else "\n\n"
    text, brackets = "", []
    for n, part in enumerate(parts):
        line, at = written(part, spaced_after=after)
        text += brk if n else ""
        brackets += [len(text) + offset for offset in at]
        text += line
    lead = "".join(t.raw for t in p_toks[:next((n for n, t in enumerate(p_toks) if t.kind != SPACE), len(p_toks))])
    tail = "".join(t.raw for t in p_toks[next((n for n in range(len(p_toks), 0, -1) if p_toks[n - 1].kind != SPACE), 0):])
    # Each mark dropped with the mark of engine A left over it stands for,
    # in order ("" for none: neither engine reads one there); a bracket, on
    # either side, stands for no other mark.
    paired = OPEN_CLASSES | CLOSE_CLASSES
    others = iter(t.raw for t in free if t.cls not in paired)
    return (lead + text + tail, [len(lead) + at for at in brackets], [t.raw for t in p_marks],
            [(t.raw, "" if t.cls in paired else next(others, "")) for t in dropped])


# --- whole lines of no CJK character (N2) ------------------------------------------------

def _lines_of(toks: Sequence[Token]) -> list[list[Token]]:
    """TOKS cut at the line breaks."""
    segments: list[list[Token]] = [[]]
    for t in toks:
        if t.kind == BREAK:
            segments.append([])
        else:
            segments[-1].append(t)
    return segments


def text_lines(a_toks: Sequence[Token], last: bool = False) -> list[list[Token]]:
    """The segments of engine A's gap between breaks that hold a text run of
    letters and at least six letters or figures in all: whole lines of
    Western text (an entry of a bibliography, an English footnote).  LAST:
    the gap is the page's trailing edge, and its last segment is a line of
    its own too."""
    segments = _lines_of(a_toks)
    out = []
    # The first and last segments share a line with the characters on either
    # side of the gap: they are woven, not inserted whole.
    for seg in segments[1:] if last else segments[1:-1]:
        runs = runs_of(seg)
        letters = sum(len(re.findall(rf"[{LATIN}]", t.raw)) for t in runs)
        figures = sum(len(re.findall(rf"[{ALNUM}]", t.raw)) for t in runs)
        if runs and letters >= 3 and figures >= 6 and not any(t.kind in (CELL, ROW, MARKER, OTHER) for t in seg):
            out.append(seg)
    return out


def _joined(lines: Sequence[Sequence[Token]], numbers: Sequence[int]) -> str:
    """The print of the LINES numbered NUMBERS as one string (fused), the
    cuts between them set aside."""
    return "".join(fused(lines[n]) for n in numbers)


def line_pairs(a_lines: Sequence[Sequence[Token]],
               b_lines: Sequence[Sequence[Token]]) -> list[tuple[list[int], list[int]]] | None:
    """The engines' lines of a gap (_lines_of) paired in order: a line of
    engine A with one of engine B, or consecutive lines of one engine with
    the one line of the other that holds their print (loose, the cut
    between the lines set aside as a space between two words is) - each
    engine cuts a long entry where it reads the print wrap, or not at all.
    Lines whose print differs pair one with one.  Each pair as the numbers
    of its lines in either engine; None where the lines do not pair to the
    end of both."""
    a_print, b_print = [fused(line) for line in a_lines], [fused(line) for line in b_lines]

    def same(x: str, y: str) -> bool:
        return _LOOSE_HYPHEN.sub("", x) == _LOOSE_HYPHEN.sub("", y)

    pairs: list[tuple[list[int], list[int]]] = []
    i = j = 0
    while i < len(a_lines) and j < len(b_lines):
        # The shorter line takes the lines after it while it is shorter.
        di, dj, a, b = 1, 1, a_print[i], b_print[j]
        while not same(a, b) and len(a) < len(b) and i + di < len(a_lines):
            a += a_print[i + di]
            di += 1
        if not same(a, b):
            di, a = 1, a_print[i]
            while not same(a, b) and len(b) < len(a) and j + dj < len(b_lines):
                b += b_print[j + dj]
                dj += 1
            if not same(a, b):
                dj = 1
        pairs.append((list(range(i, i + di)), list(range(j, j + dj))))
        i, j = i + di, j + dj
    return pairs if i == len(a_lines) and j == len(b_lines) else None


def paragraphs(lines: Sequence[Sequence[Token]], a_toks: Sequence[Token], b_toks: Sequence[Token]) -> list[str]:
    """The whole LINES of engine A's gap A_TOKS as the paragraphs written:
    engine A cuts a line where the print wraps it, so consecutive lines
    that engine B (B_TOKS) reads as one line (line_pairs) are one
    paragraph (run_on); every other line is its own."""
    a_lines = _lines_of(a_toks)
    at = {id(seg[0]): n for n, seg in enumerate(a_lines) if seg}
    joined = {n: k for k, (numbers, _) in enumerate(line_pairs(a_lines, _lines_of(b_toks)) or []) for n in numbers}
    out: list[list[str]] = []
    last = None
    for seg in lines:
        pair = joined.get(at.get(id(seg[0]), -1), ("own", id(seg)))
        if out and pair == last:
            out[-1].append(line_text(seg))
        else:
            out.append([line_text(seg)])
        last = pair
    return [run_on(texts) for texts in out]


def run_on(texts: Sequence[str]) -> str:
    """Consecutive lines of one entry as one string: joined by a space, or by
    nothing after a line that ends in a hyphen after a letter or figure (the
    print wraps a word there, engine A's Pre-/ss written Pre-ss)."""
    out = ""
    for text in texts:
        out += text if not out or re.search(r"[^\W_]-$", out) else " " + text
    return out


def line_text(seg: Sequence[Token]) -> str:
    text = "".join(" " if t.kind == SPACE else "" if t.kind == MARKUP else t.raw for t in seg)
    return re.sub(r"[ \t]+", " ", text).strip()


# --- the first answers of retried punctuation passes (R1) --------------------------------------

COUNT_REFUSAL = re.compile(r"\d+ (?:places marked|marks written) but \d+ counted")


def retry_passes(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The sealed punctuation passes whose kept retry followed a first answer
    refused on the count, with the first answer whole."""
    out = []
    for n, p in enumerate(record.get("punctuationPasses") or []):
        if not isinstance(p, dict) or not p.get("ok"):
            continue
        answer = p.get("firstAnswer")
        if not isinstance(answer, str) or not answer or len(answer) >= 2000:
            continue
        if not COUNT_REFUSAL.search(str(p.get("reason") or "")):
            continue
        out.append({"index": n, **p})
    return out


def _lead_part(toks: Sequence[Token]) -> list[Token]:
    """The tokens after the last break or structure (a line's own lead)."""
    last = max((i for i, t in enumerate(toks) if t.kind in (BREAK, STRUCT)), default=-1)
    return list(toks[last + 1:])


def _trail_part(toks: Sequence[Token]) -> list[Token]:
    """The tokens before the first break or structure (a line's own tail)."""
    first = next((i for i, t in enumerate(toks) if t.kind in (BREAK, STRUCT, CELL, ROW)), len(toks))
    return list(toks[:first])


# --- the stage -----------------------------------------------------------------------------

@dataclass
class Setting:
    book: Any
    model: Mapping[str, Any]
    facts: rr.BookFacts
    order: list[int]
    texts: Mapping[int, str]
    folio_forms: dict[int, set[str]] = field(default_factory=dict)

    def page(self, scan: int, text: str | None = None) -> PageRuns:
        """SCAN as the stage reads it: its text as it stands, or TEXT."""
        pd = rr.page_data(self.book, self.model, scan, self.texts[scan] if text is None else text)
        if scan not in self.folio_forms:
            import _book_lint as lint
            self.folio_forms[scan] = lint.folio_forms(pd.entry.get("folio"))
        notation = (getattr(self.book, "profile", None) or {}).get("notation") or {}
        return PageRuns(pd, self.folio_forms[scan], bool(notation.get("doubled") or notation.get("emphasis")))


def bisect_left(values: Sequence[int], x: int) -> int:
    import bisect
    return bisect.bisect_left(values, x)


def _markup(t: Token) -> bool:
    """A token of the page that is its own markup, not print (emphasis
    markers, a tag)."""
    return t.kind == MARKUP or (t.kind == MARK and t.raw in "*_`#")


def retry_marks(setting: Setting, plan: rr.Plan) -> None:
    """R1 over the pages (the module's docstring)."""
    for scan in setting.order:
        record = setting.book.pages[scan].record
        passes = retry_passes(record)
        if not passes:
            continue
        page = setting.page(scan)
        text, s = page.text, page.s
        taken: list[tuple[int, int]] = []
        for p in passes:
            fa = ra.stream(p["firstAnswer"])
            if len(fa.chars) < 4 or s.chars.count(fa.chars) != 1:
                continue
            i0 = s.chars.index(fa.chars)
            n = len(fa.chars)
            left = 0   # gaps of the stretch that differ from engine A's and stay
            for j in range(n + 1):
                k = i0 + j
                fa_toks = tokens(fa.gaps[j], "page", j == 0)
                uneven = False
                if j == 0 or j == n:
                    # The stretch's outer gaps: the part on the stretch's own
                    # line, on each side cut at its line break.
                    if (j == 0 and k == 0) or (j == n and k >= len(s.chars)):
                        continue
                    start, end = page.span(k)
                    p_all = tokens(text[start:end], "page")
                    ia = page.to_a[k] if j == 0 else page.to_a[k - 1]
                    ib = page.to_b[k] if j == 0 else page.to_b[k - 1]
                    if j == n:
                        ia = ia + 1 if ia is not None and ia + 1 < len(page.a.gaps) else None
                        ib = ib + 1 if ib is not None and ib + 1 < len(page.b.gaps) else None
                    a_all = tokens(page.a.gaps[ia], "a") if ia is not None else None
                    b_all = tokens(page.b.gaps[ib], "b") if ib is not None else None
                    cut = _lead_part if j == 0 else _trail_part
                    p_part = cut(p_all)
                    a_part = cut(a_all) if a_all is not None else None
                    b_part = cut(b_all) if b_all is not None else None
                    if j == 0:
                        sub_start, sub_end = start + (p_part[0].start if p_part else end - start), end
                    else:
                        sub_start, sub_end = start, start + (p_part[-1].end if p_part else 0)
                    # Engine A ends its line in the gap and the page does not
                    # (or the reverse): what stands beyond the break on one
                    # side stands in the part on the other.
                    uneven = a_all is not None and any(t.kind == BREAK for t in a_all) \
                        != any(t.kind in (BREAK, STRUCT, CELL, ROW) for t in p_all)
                else:
                    gap = page.gap(k)
                    sub_start, sub_end = gap.start, gap.end
                    a_part, b_part, p_part = gap.a, gap.b, gap.page
                if a_part is None:
                    continue
                fa_keys, a_keys, p_keys = keys(fa_toks), keys(a_part), keys(p_part)
                if fa_keys != a_keys or fa_keys == p_keys:
                    # Marks the kept retry wrote that neither the first
                    # answer nor engine A holds stay, and are counted.
                    marks = [[key for key in side if key[0] == MARK] for side in (p_keys, fa_keys, a_keys)]
                    left += marks[0] != marks[1] and marks[0] != marks[2] and not uneven
                    continue
                if any(kind in (MARKER, OTHER) for kind, _ in fa_keys + p_keys):
                    continue
                if any(t.kind in (BREAK, STRUCT, CELL, ROW) for t in p_part):
                    continue
                p_runs = [key for key in p_keys if key[0] == RUN]
                why = None
                if uneven:
                    why = "engine A's line ends here and the page's does not"
                elif any(_markup(t) for t in p_part):
                    why = "the page holds markup here"
                elif p_runs and p_runs != [key for key in fa_keys if key[0] == RUN]:
                    why = "the page holds print the first answer lacks"
                elif b_part is not None and keys(b_part) != fa_keys:
                    why = "engine B reads the gap otherwise"
                elif j == 0 and any(line_figure(t.raw) for t in runs_of(a_part)[:1]):
                    why = "a figure opens the line"
                if why is not None:
                    plan.doubts.append(("retry-marks-unverified", scan,
                                        _retry_detail(page, k, fa, j, a_part, b_part, p_part, why),
                                        {"pass": p["index"], "block": p.get("block")}))
                    continue
                if any(re_.overlaps((sub_start, sub_end), t) for t in taken):
                    continue
                # The gap as engine A wrote it: the first answer holds the
                # same runs and marks, and the engine's glyphs are the print's.
                new = written(a_part)[0]
                if new == text[sub_start:sub_end]:
                    continue
                taken.append((sub_start, sub_end))
                plan.edits.append(re_.Edit(scan, sub_start, sub_end, new, rule="R1", evidence={
                    "before": text[sub_start:sub_end], "firstAnswer": show(fa.gaps[j], 120),
                    "engineA": show("".join(t.raw for t in a_part), 120),
                    "engineB": show("".join(t.raw for t in b_part), 120) if b_part is not None else None,
                    "pass": p["index"], "block": p.get("block"), "range": p.get("range"),
                    "reason": p.get("reason")}))
            if left:
                before, after = s.chars[max(0, i0 - 0):i0 + 4], s.chars[max(i0, i0 + n - 4):i0 + n]
                plan.doubts.append(("retry-marks-unverified", scan,
                                    f"{left} gap(s) of a retried stretch ({before}\u2026{after}) hold marks that are neither "
                                    f"the first answer's nor engine A's", {"pass": p["index"], "block": p.get("block"),
                                                                           "gaps": left}))


def _retry_detail(page: PageRuns, k: int, fa: ra.Stream, j: int, a_part: Sequence[Token], b_part: Sequence[Token] | None,
                  p_part: Sequence[Token], why: str) -> str:
    before, after = page.context(k)
    return (f"{why}: {before}\u2038{after}\u3000first answer\u300c{show(fa.gaps[j], 30)}\u300dA\u300c"
            f"{show(''.join(t.raw for t in a_part), 30)}\u300dB\u300c"
            f"{show(''.join(t.raw for t in b_part), 30) if b_part is not None else '-'}\u300d"
            f"page\u300c{show(''.join(t.raw for t in p_part), 30)}\u300d")[:220]


def _gap_detail(page: PageRuns, gap: Gap, why: str | None = None) -> str:
    """A doubt's text: the reason first (it is not to be cut off), then the
    place and each side's reading of the gap."""
    before, after = page.context(gap.k)
    text = (f"{before}\u2038{after}\u3000A\u300c{show(gap.a_raw, 45) if gap.a_raw is not None else '-'}\u300d"
            f"B\u300c{show(gap.b_raw, 45) if gap.b_raw is not None else '-'}\u300d"
            f"page\u300c{show(page.text[gap.start:gap.end], 45)}\u300d")
    return (f"{why}: {text}" if why else text)[:220]


@dataclass
class Tally:
    """A page's note markers the engines read: lacking on the page, or of
    another value."""
    lacking: list[str] = field(default_factory=list)
    differing: list[str] = field(default_factory=list)
    # The runs engine A alone read where engine B is not aligned: (place, runs).
    alone: list[tuple[str, list[str]]] = field(default_factory=list)
    # N5: the gaps of a table whose marks both engines read and the page
    # lacks, where the marks could not be written (place, engine A's text),
    # how many marks those gaps hold, and how many of the rule's marks the
    # bracket guard withheld.
    cells: list[str] = field(default_factory=list)
    cell_marks: int = 0
    withheld: int = 0


def _markers(toks: Sequence[Token] | None) -> list[str]:
    return [t.cls for t in (toks or []) if t.kind == MARKER]


def _head(toks: Sequence[Token] | None) -> list[Token] | None:
    """The tokens before the first break or note marker, when there is one."""
    if toks is None:
        return None
    first = next((i for i, t in enumerate(toks) if t.kind in (BREAK, MARKER)), None)
    return list(toks[:first]) if first is not None else None


def _past_marker(toks: Sequence[Token], head: Sequence[Token]) -> list[Token]:
    """The marks of TOKS on the line of the character before the gap after
    the note marker that ends HEAD (_head); none where a break ends it."""
    if len(head) >= len(toks) or toks[len(head)].kind != MARKER:
        return []
    rest = toks[len(head) + 1:]
    end = next((i for i, t in enumerate(rest) if t.kind == BREAK), len(rest))
    return [t for t in rest[:end] if t.kind == MARK]


def _astray(past: Sequence[Token], other_head: Sequence[Token], other_past: Sequence[Token]) -> bool:
    """Whether a mark one side holds after its note marker (PAST) is one the
    other side holds before its own (OTHER_HEAD) more often than after it
    (OTHER_PAST), by coarse class: the two sides put the marker on either
    side of that mark (the page `頁⑳。`, engine A `頁12b-13a。⑳`), and the
    parts before the markers are not one stretch of print."""
    for key in {coarse(t.key) for t in past}:
        if sum(1 for t in other_past if coarse(t.key) == key) < sum(1 for t in past if coarse(t.key) == key) \
                and any(t.kind == MARK and coarse(t.key) == key for t in other_head):
            return True
    return False


def head_gap(gap: Gap) -> Gap | None:
    """The part of GAP on the line of the character before it, before a
    note marker: each side's tokens up to its first line break or note
    marker.  A note marker that ends the line (the number of a note the
    sentence refers to) or opens the next one (the next footnote's own
    number) then stands outside it.  A side with neither is read whole:
    engine A's and engine B's (they hold no marker then, and no break); the
    page's where engine A holds no mark past its own part - else the page's
    marks could be those of a line engine A reads after the marker - or
    where the gap is the page's trailing edge, which nothing of the page
    follows (a mark of the page that is not one of engine A's part is then
    no match, and weave refuses the gap).  None where the page's part is
    not given: at the page's leading edge (no character stands before the
    gap; what engine A reads there may be a line of its own, a running
    head), and where the page and engine A put the marker on either side of
    a mark (_astray: woven, the mark would be written twice)."""
    if gap.a is None or gap.edge == "start":
        return None
    a_head = _head(gap.a)
    if a_head is None:
        a_head = list(gap.a)
    p_head = _head(gap.page)
    if p_head is None:
        if gap.edge != "end" and any(t.kind == MARK for t in gap.a[len(a_head):]):
            return None
        p_head = list(gap.page)
    p_past, a_past = _past_marker(gap.page, p_head), _past_marker(gap.a, a_head)
    if _astray(p_past, a_head, a_past) or _astray(a_past, p_head, p_past):
        return None
    b_head = _head(gap.b) if gap.b is not None else None
    if gap.b is not None and b_head is None:
        b_head = list(gap.b)
    end = gap.start + (p_head[-1].end if p_head else 0)
    a_raw = "".join(t.raw for t in a_head)
    b_raw = "".join(t.raw for t in b_head) if b_head is not None else None
    return Gap(gap.k, gap.start, end, p_head, a_head, b_head, a_raw, b_raw, gap.a_index)


# A note call as printed: one circled figure whose value folds to digits
# (_marker_value: ① to ⑳, ㉑ to ㊿), and nothing else of the marker.  The
# negative, double and sans-serif forms (❶, ⓵, ➀) fold to none, so
# numbered() could never place one.
CALL_RE = re.compile(r"[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf]")


def _call(t: Token) -> str | None:
    """The circled figure of note marker T when it holds nothing else
    (engine A's LaTeX `$ ^{①} $`, engine B's ①): the call as printed."""
    if t.kind != MARKER:
        return None
    inner = re.sub(r"[\s$^{}]", "", t.raw)
    return inner if CALL_RE.fullmatch(inner) else None


def _line_markers(toks: Sequence[Token] | None) -> list[str]:
    """The values of the note markers of TOKS that do not open a line."""
    out: list[str] = []
    opening = False
    for t in toks or []:
        if t.kind == BREAK:
            opening = True
        elif t.kind == MARKER:
            if not opening:
                out.append(t.cls)
        elif t.kind not in (SPACE, MARKUP):
            opening = False
    return out


def numbered(gaps: Sequence[Gap]) -> set[int]:
    """The gaps (by K) whose note calls stand in their place in the page's
    numbering: the page's calls in reading order - in each gap the values
    of the note markers in a line that engine A reads (engine B's where
    engine A reads none), the page's leading edge set aside - run 1, 2,
    3 ... from its first call up to the gap's last.  A book that numbers
    its notes on from page to page prints figures past the circled glyphs,
    which both engines read alike as the glyph of some of their digits."""
    if any(_line_markers(gap.page) and not (_line_markers(gap.a) or _line_markers(gap.b)) for gap in gaps):
        # The page holds a call where neither engine reads one (set a gap
        # off by the punctuator): one written at the engines' place would
        # stand twice.
        return set()
    out: set[int] = set()
    count = 0
    for gap in gaps:
        if gap.edge == "start":
            continue
        values = _line_markers(gap.a) or _line_markers(gap.b)
        if not values:
            continue
        if values != [str(n) for n in range(count + 1, count + 1 + len(values))]:
            break
        count += len(values)
        out.add(gap.k)
    return out


def _as_calls(toks: Sequence[Token]) -> list[Token] | None:
    """TOKS with its note call (_call) a run of its glyph; None where the
    gap holds another note marker, a marker that is no call, one that opens
    a line (a note's own number), or a figure after it (a piece of the
    note's number, _note_piece).  Two circled figures side by side are one
    number misread figure by figure: both engines read 14 such pairs on the
    modern book (79 as ⑦⑨, 108 as ⑩⑧), none on the four archive volumes."""
    markers = [i for i, t in enumerate(toks) if t.kind == MARKER]
    if len(markers) != 1 or any(t.kind == RUN and _note_piece(toks, t) for t in toks):
        return None
    i = markers[0]
    glyph = _call(toks[i])
    before = [t for t in toks[:i] if t.kind not in (SPACE, MARKUP)]
    if glyph is None or (before and before[-1].kind == BREAK):
        return None
    return [*toks[:i], Token(RUN, glyph, toks[i].start, toks[i].end, "call"), *toks[i + 1:]]


def called(gap: Gap) -> Gap | None:
    """N1 on note calls: GAP with each engine's note call read as a run
    (_as_calls), where both engines read the same circled figure at the
    same place among the gap's marks (by coarse class) and the page holds no
    note marker there.  None where an engine is not aligned, where a marker
    of either engine is not a call in a line, or where the page breaks a
    line engine A does not (numbered(), its only way in, sets the page's
    leading edge aside)."""
    if gap.a is None or gap.b is None or _markers(gap.page):
        return None
    if any(t.kind == BREAK for t in gap.page) and not any(t.kind == BREAK for t in gap.a):
        # The page breaks a line engine A reads on (two notes set on one
        # line): which side of the break the call goes is not given.
        return None
    a, b = _as_calls(gap.a), _as_calls(gap.b)
    if a is None or b is None:
        return None

    def place(toks: Sequence[Token]) -> list[Any]:
        return [t.raw if t.cls == "call" else coarse(t.key) for t in toks if t.cls == "call" or t.kind == MARK]

    if not any(t.cls == "call" for t in a) or place(a) != place(b):
        return None
    return Gap(gap.k, gap.start, gap.end, gap.page, a, b, gap.a_raw, gap.b_raw, gap.a_index, edge=gap.edge)


def _tab_marker(t: Token) -> bool:
    """Whether T is LaTeX that tokens() left as unknown markup and that is a
    note marker once a command whose backslash-t was written as a tab is
    read as that command (TAB_COMMAND_RE)."""
    if t.kind != OTHER or not t.raw.startswith("$") or "\t" not in t.raw:
        return False
    marker = tokens(TAB_COMMAND_RE.sub(lambda m: "\\t" + m.group()[1:], t.raw), "b")
    return len(marker) == 1 and marker[0].kind == MARKER


def _unmarked(toks: Sequence[Token], trailing: bool) -> list[Token]:
    """TOKS without the note markers that open a line after a break (a
    footnote's own number, never written; _tab_marker's too), with the
    spaces and markup about them; TRAILING (the gap is the page's trailing
    edge): without the breaks, spaces and markup that end it too."""
    out: list[Token] = []
    opening = False
    for t in toks:
        if t.kind == BREAK:
            opening = True
        elif opening and (t.kind in (MARKER, SPACE, MARKUP) or _tab_marker(t)):
            continue
        else:
            opening = False
        out.append(t)
    while trailing and out and out[-1].kind in (BREAK, SPACE, MARKUP):
        out.pop()
    return out


def marker_lines(gap: Gap) -> tuple[Gap, list[list[Token]]] | None:
    """N2 on a gap that holds note markers - a footnote of Western text
    between two others, each line opening with its number: the gap with
    engine A's and engine B's tokens _unmarked, and engine A's whole lines
    in it (text_lines; at the page's trailing edge its last line too, which
    no character of the page follows).  None where it holds none, where an
    engine is not aligned there, or where a note marker is left in an
    engine's tokens (one inside a line, or on the line of a character
    beside the gap)."""
    if gap.a is None or gap.b is None:
        return None
    trailing = gap.edge == "end"
    a, b = _unmarked(gap.a, trailing), _unmarked(gap.b, trailing)
    if any(t.kind == MARKER for t in a + b):
        return None
    lines = text_lines(a, trailing) if len(a) <= 5 * TOKEN_LIMIT else []
    if not lines:
        return None
    return Gap(gap.k, gap.start, gap.end, gap.page, a, b, gap.a_raw, gap.b_raw, gap.a_index, edge=gap.edge), lines


def stitched(setting: Setting, scan: int) -> list[int]:
    """The pages of the table phase 3 stitched across pages that SCAN
    holds part of (the seals' tableStitch: the head page names them all, a
    continuation its head), those of this run, head first; [SCAN] where
    there is none."""
    record = setting.book.pages[scan].record
    stitch = record.get("tableStitch") if isinstance(record.get("tableStitch"), Mapping) else {}
    head = (stitch.get("continuation") or {}).get("head", {}).get("page") if stitch.get("continuation") else scan
    if head != scan and head in setting.texts:
        record = setting.book.pages[head].record
        stitch = record.get("tableStitch") if isinstance(record.get("tableStitch"), Mapping) else {}
    pages = (stitch.get("head") or {}).get("pages") or [scan]
    return [n for n in pages if n in setting.texts] or [scan]


def text_runs(setting: Setting, plan: rr.Plan,
              along: dict[int, list[tuple[str, int, str, dict[str, Any]]]] | None = None) -> None:
    """N1, N5, N8, N6, N7 and N2 over the pages: the edits proposed (not yet
    applied) and the doubts, added to PLAN (those that stand with an edit
    to ALONG, where given: _page_runs).  Last, what no gap aligns: the
    runs both engines read on a page that it lacks, where neither engine is
    aligned to the characters about them (the page holds the text in
    another order, a table was rebuilt), and that no edit writes and no
    doubt names, are one doubt for the page (unaligned) - for the pages of
    a table stitched across pages together, on the first of them, as the
    stitch moved rows from one page's text to another's.  A page with no
    character is read too: _blank_page writes the engines' lines only where
    the page is empty, and its edits and doubts count as on any page."""
    held: dict[int, list[tuple[PageRuns, list[Any], list[re_.Edit]]]] = {}
    for scan in setting.order:
        page = setting.page(scan)
        doubted, proposed = len(plan.doubts), len(plan.edits)
        if not page.s.chars:
            _blank_page(setting, page, plan)
        else:
            plan.edits.extend(_page_runs(page, plan, along=along))
        _moved_rows(setting, scan, plan)
        unit = stitched(setting, scan)
        parts = held.setdefault(unit[0], [])
        parts.append((page, plan.doubts[doubted:], plan.edits[proposed:]))
        if len(parts) < len(unit):
            continue
        left = unaligned(held.pop(unit[0]))
        if left:
            where = f" (the table stitched over pages {unit[0]}-{unit[-1]})" if len(unit) > 1 else ""
            plan.doubts.append(("run-no-place", unit[0], f"runs both engines read that the page lacks and no gap "
                                                         f"places{where}: {', '.join(left)}"[:220], {"runs": left}))


def _run_keys(toks: Sequence[Token]) -> Counter[tuple[str, str]]:
    return Counter(t.key for t in runs_of(toks))


def _plain(toks: Sequence[Token], side: str, line_start: bool) -> list[Token]:
    """TOKS read again without their markup: an engine that wraps a Western
    title in emphasis markers (engine B's `*Title of a Book*`) cuts the
    citation into several runs where the other engine reads one, and the
    runs of the two are then compared as the same print (as fused() does
    for agree)."""
    if not any(t.kind == MARKUP for t in toks):
        return list(toks)
    return tokens("".join(t.raw for t in toks if t.kind != MARKUP), side, line_start)


# A comma, point or colon of either width between two figures (3，224 for
# 3,224): the page's run, cut by the punctuator's full-width mark.
FIGURE_JOIN_RE = re.compile(r"(?<=[0-9\uff10-\uff19])[\uff0c\uff0e](?=[0-9\uff10-\uff19])")


def _held(text: str, line_start: bool = False) -> Counter[tuple[str, str]]:
    """The runs TEXT (the page's) holds, by key: a figure the punctuator cut
    with a full-width comma or point (FIGURE_JOIN_RE) counted whole."""
    return _run_keys(tokens(FIGURE_JOIN_RE.sub(lambda m: m.group().translate(RUN_FOLD), text), "page", line_start))


def left_out(page: PageRuns, gap: Gap, doubts: Sequence[tuple[str, int, str, Mapping[str, Any]]],
             edits: Sequence[re_.Edit]) -> list[str]:
    """The runs both engines read in GAP (engine A's that engine B reads
    there too, compared by key as a multiset, each engine's markup set
    aside: _plain) that the page lacks there, that no edit of the gap
    (EDITS) writes and that no doubt raised on it (DOUBTS: their `runs`,
    `lines` and `engineA`) names, as engine A spells them: what the stage
    would otherwise leave out with nothing said.  None
    of a folio (_is_folio) or of a line engine A read in a block of the
    page's furniture: the furniture stage deletes those by design."""
    if gap.a is None or gap.b is None or _is_folio(page, gap, gap.a) or _is_folio(page, gap, gap.b):
        return []
    furniture = _furniture(page)
    a_toks, b_toks = _plain(gap.a, "a", gap.k == 0), _plain(gap.b, "b", gap.k == 0)
    a_runs = [t for line in _lines_of(a_toks) if not (furniture and _in_furniture(line, furniture))
              for t in runs_of(line) if not _note_piece(a_toks, t)]
    left = Counter(t.key for t in a_runs) & _run_keys(b_toks)
    left -= _held(page.text[gap.start:gap.end], gap.k == 0)
    for edit in edits:
        left -= _held(edit.after) - _held(page.text[edit.start:edit.end])
    left -= _named(doubts)
    out = []
    for t in a_runs:
        if left[t.key] > 0:
            left[t.key] -= 1
            out.append(t.raw)
    return out


def _named(doubts: Sequence[tuple[str, int, str, Mapping[str, Any]]]) -> Counter[tuple[str, str]]:
    """The runs DOUBTS name (their `runs`, `lines` and `engineA`), by key."""
    out: Counter[tuple[str, str]] = Counter()
    for _kind, _scan, _detail, fields in doubts:
        for key in ("runs", "lines", "engineA"):
            value = fields.get(key)
            for item in [value] if isinstance(value, str) else value if isinstance(value, list) else []:
                out += _run_keys(tokens(str(item), "a"))
    return out


def _note_piece(toks: Sequence[Token], run: Token) -> bool:
    """Whether RUN is a figure of one to three digits right after a note
    marker of TOKS (spaces and markup between): a piece of the note's
    number, which the engines read as a marker and a figure."""
    if not re.fullmatch(r"[0-9\uff10-\uff19]{1,3}", run.raw):
        return False
    before = [t for t in toks if t.end <= run.start and t.kind not in (SPACE, MARKUP)]
    return bool(before) and (before[-1].kind == MARKER or _tab_marker(before[-1]))


def unaligned(items: Sequence[tuple[PageRuns, Sequence[tuple[str, int, str, Mapping[str, Any]]], Sequence[re_.Edit]]]
              ) -> list[str]:
    """The runs both engines read in the bodies of ITEMS' pages (per page,
    engine A's that engine B reads too; compared by key as a multiset, each
    engine's markup set aside: _plain) that the pages' texts lack, as each
    page's edits leave them, and that the pages' doubts do not name: text
    no gap could place, because neither engine aligns there.  ITEMS: (page, its doubts, its edits) for one page,
    or for the pages of a table stitched across pages, whose rows phase 3
    moved from one page's text to another's.  Not a figure that is a note
    number's piece (_note_piece), the folio whole alone on engine A's first
    or last line of print, nor a line engine A read in a block of the page's
    furniture."""
    a_runs: list[Token] = []
    agreed: Counter[tuple[str, str]] = Counter()
    held: Counter[tuple[str, str]] = Counter()
    texts = {page.scan: page.text for page, _, _ in items}
    for page, _, _ in items:
        furniture = _furniture(page)
        a_toks, b_toks = _plain(tokens(page.pd.a_body, "a", True), "a", True), \
            _plain(tokens(page.pd.b_body, "b", True), "b", True)
        lines = _lines_of(a_toks)
        printed = [[t for t in line if t.kind in (RUN, MARK)] for line in lines]
        edges = [n for n, items in enumerate(printed) if items]
        # The folio: the folio whole alone on engine A's first or last line
        # of print (as _is_folio reads a gap at the page's edge).
        folio = {n for n in edges[:1] + edges[-1:] if len(printed[n]) == 1 and printed[n][0].kind == RUN
                 and printed[n][0].key[1] in page.folio_whole}
        runs = [t for n, line in enumerate(lines) if n not in folio and not (furniture and _in_furniture(line, furniture))
                for t in runs_of(line) if not _note_piece(a_toks, t)]
        a_runs.extend(runs)
        agreed += Counter(t.key for t in runs) & Counter(t.key for t in runs_of(b_toks) if not _note_piece(b_toks, t))
        held += _held(page.text, True)
    left = agreed - held
    for _page, doubts, edits in items:
        for edit in edits:
            before = texts[edit.page][edit.start:edit.end] if edit.page in texts else str(edit.evidence.get("before") or "")
            left -= _held(edit.after) - _held(before)
        left -= _named(doubts)
    out = []
    for t in a_runs:
        if left[t.key] > 0:
            left[t.key] -= 1
            out.append(t.raw)
    return out


def _page_runs(page: PageRuns, plan: rr.Plan, inside: tuple[int, int] | None = None,
               along: dict[int, list[tuple[str, int, str, dict[str, Any]]]] | None = None) -> list[re_.Edit]:
    """One page's gaps: the edits proposed, the doubts added to PLAN - but
    those that stand with an edit (N7's reading, the marks N6 drops), with
    ALONG, to ALONG by the edit's id, for edge_safe to raise if it keeps the
    edit.  With INSIDE (a span of the page's text), only the gaps that
    touch it are read.  The runs both engines read in
    a gap that the page lacks there, that no edit of the gap writes and no
    doubt raised on it names, are a doubt of their own (left_out): no rule's
    early way out leaves them out with nothing said."""
    scan = page.scan
    text = page.text
    tally = Tally()
    found: list[re_.Edit] = []
    wrote: dict[int, list[int]] = {}
    cells: dict[int, list[int]] = {}
    # The gaps where engine A holds brackets, for the pairing guard (unpaired).
    lacking: list[tuple[int, int, int]] = []
    # The page's tables (N6 writes in none).
    spans = table_spans(text)
    # Per edit, the doubt it stands for, raised where the pairing guard would
    # withhold a bracket of it that N6 needs, or one between two runs (the
    # edit is then not made).
    refused: dict[int, tuple[str, int, str, dict[str, Any]]] = {}
    # N7: per edit, the doubt that says it is engine A's reading (raised, and
    # the edit named N7, once the pairing guard has kept it).
    letters: dict[int, tuple[str, int, str, dict[str, Any]]] = {}
    # N6: per edit, the doubt that names the page's marks it drops (raised,
    # as N7's, once the pairing guard has kept the edit).
    gone: dict[int, tuple[str, int, str, dict[str, Any]]] = {}

    def tabled(gap: Gap) -> bool:
        return any(start < max(gap.end, gap.start + 1) and gap.start < end for start, end in spans)

    def woven(gap: Gap, new: str, tier: str, names: Sequence[str]) -> None:
        # N5 where the text written holds a mark of a table cell that N1 leaves.
        if gap.cells:
            edit = _edit(scan, gap, new, "N5", "cell marks, runs" if names else "cell marks", names)
            cells[id(edit)] = list(gap.cells)
        else:
            edit = _edit(scan, gap, new, "N1", tier, names)
        wrote[id(edit)] = list(gap.wrote)
        if names:
            refused[id(edit)] = ("run-no-place", scan, _gap_detail(page, gap, "a bracket withheld would join two runs"),
                                 {"runs": list(names)})
        found.append(edit)

    def unwritten(gap: Gap, new: str | None) -> None:
        # N5: the marks of a table gap the rule holds for (cell_gap) that it
        # could not write - the whole gap (its cell boundaries are not the
        # engines' one to one), or a stretch of it (stretches, _stretch).
        count = sum(1 for t in gap.a or [] if t.kind == MARK) if new is None else gap.cell_lost
        if count:
            tally.cells.append(f"{page.context(gap.k)[0]}\u2038{show(gap.a_raw or '', 16)}")
            tally.cell_marks += count

    def read(gap: Gap) -> str | None:
        """The rules on one GAP: its edits go to FOUND, its doubts to PLAN.
        Returns why the runs both engines read there may stay out of the
        page with no doubt naming them, where a way out says (left_out)."""
        if gap.a is None:
            # Engine B alone: a run the page lacks, with no engine A witness.
            # Written as engine B reads it, unless it is the page's folio.
            if gap.b is not None and runs_of(gap.b) and not runs_of(gap.page) and not _markers(gap.b) \
                    and not _is_folio(page, gap, gap.b):
                if not any(t.raw.translate(RUN_FOLD) in page.folio_whole for t in runs_of(gap.b)):
                    on_b = Gap(gap.k, gap.start, gap.end, gap.page, gap.b, None, gap.b_raw, None, None,
                               edge=gap.edge)
                    new, tier, names = weave(on_b)
                    if new is not None and new != text[gap.start:gap.end] and not _in_row(text, gap, names):
                        woven(on_b, new, tier + " (engine B alone)", names)
                        return None
                plan.doubts.append(("run-one-engine", scan, _gap_detail(page, gap, "engine B alone"),
                                    {"engine": "B", "runs": [t.raw for t in runs_of(gap.b)]}))
            return None
        a_runs = runs_of(gap.a)
        p_runs = runs_of(gap.page)
        a_mk, b_mk, p_mk = _markers(gap.a), _markers(gap.b), _markers(gap.page)
        if a_mk or b_mk or p_mk:
            # A note call both engines read alike in a line, in its place in
            # the page's numbering, that the page lacks: read as a run (N1).
            call = called(gap) if gap.k in calls and not tabled(gap) else None
            if call is not None:
                doubted, proposed = len(plan.doubts), len(found)
                why = read(call)
                if len(found) > proposed:
                    return why
                del plan.doubts[doubted:]
            # Note markers not written: tallied for the page, both engines'
            # values where they differ (such a gap was in no doubt before
            # #35: one page of the archive volumes, 95 of the modern book).
            if (a_mk or b_mk) and not p_mk:
                value = "/".join(a_mk or b_mk) + ("\u2260" + "/".join(b_mk) if a_mk and b_mk and a_mk != b_mk else "")
                tally.lacking.append(f"{page.context(gap.k)[0]}\u2038{value}")
            elif a_mk and b_mk and a_mk == b_mk and p_mk != a_mk:
                tally.differing.append(f"{page.context(gap.k)[0]}\u2038{'/'.join(a_mk)}\u2260{'/'.join(p_mk)}")
            # A footnote of Western text between two others, each line
            # opening with its number: N2 on the gap, the markers that open
            # the engines' lines set aside.
            unmarked = marker_lines(gap)
            if unmarked is not None:
                proposed = len(found)
                why = _whole_lines(page, unmarked[0], unmarked[1], plan, found, marker=gap)
                if len(found) > proposed:
                    return why
            # Else (or where N2 writes nothing) the part of the gap on the line
            # of the character before, before the marker, is read on its own.
            head = head_gap(gap)
            if head is None or _markers(head.a) or _markers(head.b) or _markers(head.page) \
                    or not runs_of(head.a):
                return None
            gap = head
            a_runs = runs_of(gap.a)
            p_runs = runs_of(gap.page)
        lines = text_lines(gap.a) if len(gap.a) <= 5 * TOKEN_LIMIT else []
        if lines:
            return _whole_lines(page, gap, lines, plan, found)
        if len(gap.a) > TOKEN_LIMIT:
            if a_runs and not p_runs:
                plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, "the gap is too long to weave"),
                                    {"runs": [t.raw for t in a_runs]}))
            return "the gap is too long to weave"
        if not a_runs:
            if gap.b is not None and runs_of(gap.b) and not p_runs and not _is_folio(page, gap, gap.b):
                plan.doubts.append(("run-one-engine", scan, _gap_detail(page, gap, "engine A reads none"),
                                    {"engine": "B", "runs": [t.raw for t in runs_of(gap.b)]}))
            elif cell_gap(page, gap):
                # N5, marks alone: written where their place is given, and
                # counted for the page's one doubt where it is not.
                new, tier, names = weave(gap)
                if new is not None and gap.cells and new != text[gap.start:gap.end]:
                    woven(gap, new, tier, names)
                unwritten(gap, new)
            elif not tabled(gap):
                new = agreed_marks(page, gap)
                if new is not None:
                    found.append(_edit(scan, gap, new, "N8", "marks both engines read", []))
            return None
        if p_runs:
            if [t.key for t in p_runs] == [t.key for t in a_runs] or fused(gap.page) == fused(gap.a):
                return None
            if not (all(t.raw == "\u00d7" for t in p_runs) and any("\u00d7" in t.raw for t in a_runs)):
                if gap.b is not None and agree(gap.a, gap.b):
                    plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, "the page holds another run"),
                                        {"runs": [t.raw for t in a_runs], "pageRuns": [t.raw for t in p_runs]}))
                return "the page holds another run"
            # The page holds × alone where engine A reads a run with × in
            # it: the run's sign, left when phase 3 dropped its figures
            # (开本：×毫米 for 850×1168), read as a mark the run carries.
            gap.page = [Token(MARK, t.raw, t.start, t.end, "times") if t.kind == RUN else t for t in gap.page]
        if _is_folio(page, gap, gap.a):
            return None
        if gap.b is None:
            # Written as engine A reads it; else one doubt for the page
            # (below): engine B may hold none of a page's notes, and each of
            # their runs is such a gap.
            new, tier, names = weave(gap)
            if new is not None and new != text[gap.start:gap.end] and not _in_row(text, gap, names):
                woven(gap, new, tier + " (engine B not aligned)", names)
                return None
            tally.alone.append((f"{page.context(gap.k)[0]}\u2038{show(gap.a_raw or '', 24)}", [t.raw for t in a_runs]))
            return None
        near = not agree(gap.a, gap.b) and nearly(gap.a, gap.b)
        slight: tuple[tuple[str, int, str, dict[str, Any]], tuple[str, int, str, dict[str, Any]]] | None = None
        if near:
            # Written as engine A reads it (below); the two readings on record.
            plan.doubts.append(("run-engines-differ", scan,
                                _gap_detail(page, gap, "hyphens, dashes or apostrophes apart; written as engine A reads it"),
                                {"engineA": [t.raw for t in a_runs], "engineB": [t.raw for t in runs_of(gap.b)],
                                 "written": "A"}))
        elif not agree(gap.a, gap.b):
            b_runs = runs_of(gap.b)
            if not b_runs:
                plan.doubts.append(("run-one-engine", scan, _gap_detail(page, gap, "engine B reads none"),
                                    {"engine": "A", "runs": [t.raw for t in a_runs]}))
                return None
            if [t.key for t in b_runs] == [t.key for t in a_runs] and not any(
                    t.kind == OTHER for t in gap.a + gap.b):
                # The runs agree, the marks about them do not: the runs
                # may still be woven where the page's marks are all of one
                # engine's (T1, no mark of the page left out), not otherwise.
                names = [t.raw for t in a_runs]
                why = "the engines' marks about the run differ"
                if any(line_initial_figure(gap.a, run, gap.k == 0) for run in a_runs):
                    why = "a figure opens a line in engine A"
                else:
                    new, tier, names = weave(gap)
                    if new is not None and tier == "T1" and not gap.dropped and new != text[gap.start:gap.end]:
                        woven(gap, new, tier + " (engine B's marks differ)", names)
                        return None
                    on_b = Gap(gap.k, gap.start, gap.end, gap.page, gap.b, gap.a, gap.b_raw, gap.a_raw, gap.a_index,
                               edge=gap.edge)
                    new, tier, names = weave(on_b)
                    if new is not None and tier == "T1" and not on_b.dropped and new != text[gap.start:gap.end]:
                        woven(on_b, new, tier + " (on engine B's marks)", names)
                        return None
                plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, why), {"runs": names}))
                return None
            differ = ("run-engines-differ", scan, _gap_detail(page, gap),
                      {"engineA": [t.raw for t in a_runs], "engineB": [t.raw for t in b_runs]})
            apart = slightly(gap.a, gap.b)
            if apart is None or tabled(gap) or any(t.kind in (CELL, ROW) for t in gap.a + gap.b + gap.page):
                plan.doubts.append(differ)
                return None
            # N7: written as engine A reads it where N1 or N6 places it (below),
            # else the doubt as before.
            slight = (differ, ("run-engines-differ", scan,
                               _gap_detail(page, gap, f"{apart}; written as engine A reads it, check it on the image"),
                               {**differ[3], "written": "A"}))
        # N5: in a table, the marks of the gap are written with its runs.
        in_cell = cell_gap(page, gap)
        new, tier, names = weave(gap)
        if in_cell:
            unwritten(gap, new)
        if new is None:
            doubt = ("run-no-place", scan, _gap_detail(page, gap, tier), {"runs": names})
            # N6 not in a table of the page: a cell's marks are N5's to write.
            # Nor beside a note call: N6 was measured on runs the punctuator
            # never saw, and a call it left out does not explain a page mark
            # both engines lack (no call reaches N6 on the five books, #35).
            own = engine_marks(gap, text) if tier == MARKS_NOT_ENGINES and not in_cell and not tabled(gap) \
                and not any(t.cls == "call" for t in gap.a or []) else None
            if own is not None:
                rewritten, brackets, replaced, dropped = own
                edit = _edit(scan, gap, rewritten, "N6", "the page's marks among engine A's"
                             + (" (written as engine A reads it)" if near else ""), names)
                edit.evidence["replaced"] = replaced
                if dropped:
                    edit.evidence["dropped"] = [mark for mark, _ in dropped]
                    gone[id(edit)] = ("run-mark-dropped", scan, _gap_detail(page, gap, "；".join(
                        f"頁面嘅「{mark}」換做引擎嘅「{engine}」" if engine else f"刪咗頁面嘅「{mark}」，兩個引擎都冇讀到"
                        for mark, engine in dropped)), {"dropped": edit.evidence["dropped"]})
                wrote[id(edit)] = brackets
                refused[id(edit)] = slight[0] if slight else doubt
                if slight:
                    letters[id(edit)] = slight[1]
                found.append(edit)
                return None
            plan.doubts.append(slight[0] if slight else doubt)
            return None
        if new != text[gap.start:gap.end]:
            woven(gap, new, tier, names)
            if slight:
                refused[id(found[-1])], letters[id(found[-1])] = slight
        return None

    gaps = list(page.gaps())
    calls = numbered(gaps)
    for gap in gaps:
        if inside is not None and not (gap.end > inside[0] and gap.start < inside[1]):
            continue
        if gap.a is not None and brackets_of(gap.a):
            lacking.append((gap.start, gap.end, brackets_of(gap.a)))
        doubted, proposed_before = len(plan.doubts), len(found)
        why = read(gap)
        left = left_out(page, gap, plan.doubts[doubted:], found[proposed_before:])
        if left:
            if why is None:
                why = "beside a note marker" if _markers(gap.a) or _markers(gap.b) or _markers(gap.page) \
                    else "no rule writes them"
            plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, f"runs both engines read left out, {why}"),
                                {"runs": left}))
    # The pairing guard on a trial, until it takes nothing more: N6 is made
    # only where it keeps every bracket of the edit (a bracket of engine A's
    # text withheld would leave the gap without a mark the page held), and
    # no edit where a bracket withheld would join two runs into one.  Such
    # an edit is the doubt it stands for; with it gone, a bracket of another
    # edit may lose its partner, so the trial is run again.
    while any(wrote.get(id(edit)) for edit in found):
        saved = [(edit, edit.after, dict(edit.evidence)) for edit in found]
        saved_wrote = {key: list(offsets) for key, offsets in wrote.items()}
        saved_cells = {key: list(offsets) for key, offsets in cells.items()}
        unpaired(text, found, wrote, lacking, cells)
        undone = [edit for edit in found if id(edit) in refused and edit.evidence.get("withheld")
                  and (edit.rule == "N6" or edit.evidence.get("glued"))]
        for edit, after, evidence in saved:
            edit.after, edit.evidence = after, evidence
        wrote.clear()
        wrote.update(saved_wrote)
        cells.clear()
        cells.update(saved_cells)
        if not undone:
            break
        for edit in undone:
            plan.doubts.append(refused[id(edit)])
        found = [edit for edit in found if not any(edit is u for u in undone)]
    proposed = {key: len(offsets) for key, offsets in cells.items()}
    unpaired(text, found, wrote, lacking, cells)
    for edit in found:
        if id(edit) in letters:
            edit.rule = "N7"
        # The doubts that stand with the edit: raised here, or, with ALONG,
        # by edge_safe once it keeps the edit.
        withit = [doubts[id(edit)] for doubts in (letters, gone) if id(edit) in doubts]
        if along is None:
            plan.doubts.extend(withit)
        elif withit:
            along[id(edit)] = withit
    for edit in found:
        if edit.rule != "N5":
            continue
        # N5 names the edit for the marks of a cell that N1 leaves; where the
        # bracket guard withheld every one of them, what is left is N1's (its
        # runs with the marks beside them), or nothing of the print (a space
        # put beside a pipe for a mark withheld): the gap stays as it is.
        tally.withheld += proposed[id(edit)] - len(cells[id(edit)])
        if not cells[id(edit)]:
            if edit.evidence.get("runs"):
                edit.rule, edit.evidence = "N1", {**edit.evidence, "tier": "T2"}
            else:
                edit.after = text[edit.start:edit.end]
    if tally.alone:
        places = "; ".join(place for place, _ in tally.alone)
        plan.doubts.append(("run-one-engine", scan,
                            f"engine B is not aligned at {len(tally.alone)} place(s) where engine A reads a run: {places}"[:220],
                            {"engine": "A", "runs": [run for _, names in tally.alone for run in names],
                             "places": len(tally.alone)}))
    if tally.lacking or tally.differing:
        parts = []
        if tally.lacking:
            parts.append(f"{len(tally.lacking)} the page lacks ({'; '.join(tally.lacking[:3])})")
        if tally.differing:
            parts.append(f"{len(tally.differing)} of another value ({'; '.join(tally.differing[:3])})")
        plan.doubts.append(("note-markers-unverified", scan, ("note markers the engines read: " + ", ".join(parts))[:220],
                            {"lacking": len(tally.lacking), "differing": len(tally.differing)}))
    if tally.cells or tally.withheld:
        # N5: one doubt a page for the marks it found and could not write.
        parts = []
        if tally.cells:
            parts.append(f"{tally.cell_marks} in {len(tally.cells)} gap(s) with no place for them "
                         f"({'; '.join(tally.cells[:3])})")
        if tally.withheld:
            parts.append(f"{tally.withheld} bracket(s) withheld, no partner on the line or in the cell")
        plan.doubts.append(("run-no-place", scan,
                            ("table marks both engines read that the page lacks, not written: " + ", ".join(parts))[:220],
                            {"marks": tally.cell_marks + tally.withheld, "gaps": len(tally.cells), "withheld": tally.withheld}))
    return found


def _moved_rows(setting: Setting, scan: int, plan: rr.Plan) -> None:
    """The rows of SCAN's table that phase 3 moved into an earlier page's
    table (the seal's tableStitch: the page's text before the move, the rows
    moved and the page that holds them now).  The engines read them on SCAN,
    so they are read there: the page's text as it stood before the move
    against SCAN's engines, the gaps that touch the rows.  Each edit is made
    on the page that holds the rows now, where the lines it touches stand
    there once and unchanged; one that cannot be is a doubt - the edits of
    marks alone (N5) one doubt a page, with the count of their marks (the
    stitch re-rendered the rows of scans 527, 528 and 536 of the 609-page
    book into scan 522's HTML table, and the 44 marks their 44 edits held
    found no place there and left no trace, measured 2026-10-02)."""
    stitch = setting.book.pages[scan].record.get("tableStitch")
    if not isinstance(stitch, dict):
        return
    before, part = stitch.get("before"), stitch.get("continuation")
    if not isinstance(before, str) or not isinstance(part, dict):
        return
    moved, host = part.get("moved"), (part.get("head") or {}).get("page")
    if not isinstance(moved, str) or not moved.strip() or host == scan or host not in setting.texts:
        return
    at = before.find(moved)
    if at < 0 or before.find(moved, at + 1) >= 0:
        return
    block = (at, at + len(moved))
    page = setting.page(scan, before)
    if not page.s.chars:
        return
    trial = rr.Plan()
    edits = _page_runs(page, trial, inside=block)
    for kind, _, detail, fields in trial.doubts:
        plan.doubts.append((kind, scan, detail, {**fields, "movedTo": host}))
    host_text = setting.texts[host]
    unplaced: list[str] = []   # why, per edit of marks alone that found no place
    marks = 0
    for edit in edits:
        placed = _on_host(scan, host, before, block, host_text, edit)
        if isinstance(placed, str):
            names = list(edit.evidence.get("runs") or edit.evidence.get("lines") or [])
            if names:
                plan.doubts.append(("run-no-place", scan,
                                    f"rows moved into page {host}'s table: {placed}; runs {names}"[:220], {"movedTo": host}))
            else:
                unplaced.append(placed)
                marks += len([t for t in tokens(edit.after, "page") if t.kind == MARK]) \
                    - len([t for t in tokens(before[edit.start:edit.end], "page") if t.kind == MARK])
        else:
            plan.edits.extend(placed)
    if unplaced:
        plan.doubts.append(("run-no-place", scan,
                            f"rows moved into page {host}'s table: {marks} table mark(s) both engines read that the rows lack, "
                            f"in {len(unplaced)} gap(s), not written: {'; '.join(sorted(set(unplaced)))}"[:220],
                            {"movedTo": host, "marks": marks, "gaps": len(unplaced)}))


def _on_host(scan: int, host: int, before: str, block: tuple[int, int], host_text: str,
             edit: re_.Edit) -> list[re_.Edit] | str:
    """EDIT, proposed on SCAN's text BEFORE its rows moved, as edits of the
    page HOST that holds the rows now; else why not.  An edit that changes
    two lines which do not stand together on the host (the move drops the
    rule under the continuation's first row, which the gap between its
    first two rows holds) is made line by line, all of it or none."""
    old, new = before[edit.start:edit.end], edit.after
    placed = _host_edit(scan, host, before, block, host_text, edit, edit.start, old, new)
    if not isinstance(placed, str):
        return [placed]
    if "\n" not in old or old.count("\n") != new.count("\n"):
        return placed
    parts: list[re_.Edit] = []
    at = edit.start
    for old_line, new_line in zip(old.split("\n"), new.split("\n")):
        if old_line != new_line:
            part = _host_edit(scan, host, before, block, host_text, edit, at, old_line, new_line)
            if isinstance(part, str):
                return part
            parts.append(part)
        at += len(old_line) + 1
    return parts


def _host_edit(scan: int, host: int, before: str, block: tuple[int, int], host_text: str, edit: re_.Edit,
               at: int, old: str, new: str) -> re_.Edit | str:
    """BEFORE's text OLD at AT becoming NEW (EDIT, or a line of it), as an
    edit of the page HOST; else why not."""
    lead = 0
    while lead < min(len(old), len(new)) and old[lead] == new[lead]:
        lead += 1
    trail = 0
    while trail < min(len(old), len(new)) - lead and old[len(old) - 1 - trail] == new[len(new) - 1 - trail]:
        trail += 1
    start, end, after = at + lead, at + len(old) - trail, new[lead:len(new) - trail]
    line_start = before.rfind("\n", 0, start) + 1
    line_end = before.find("\n", end)
    line_end = len(before) if line_end < 0 else line_end
    if line_start < block[0] or line_end > block[1]:
        return "the place is not in the rows moved"
    lines = before[line_start:line_end]
    here = host_text.find(lines)
    if here < 0 or host_text.find(lines, here + 1) >= 0:
        return "the rows do not stand there once and unchanged"
    if (here and host_text[here - 1] != "\n") \
            or (here + len(lines) < len(host_text) and host_text[here + len(lines)] != "\n"):
        return "the rows do not stand there once and unchanged"
    host_start = here + start - line_start
    host_end = host_start + end - start
    return re_.Edit(host, host_start, host_end, after, rule=edit.rule, evidence={
        **edit.evidence, "before": host_text[host_start:host_end], "movedFrom": scan})


def _is_folio(page: PageRuns, gap: Gap, toks: Sequence[Token] | None) -> bool:
    """Whether the runs of TOKS (an engine's reading of GAP, a page's edge)
    are the page's folio: one run, the folio whole, alone on a line beyond
    the page's first or last line of text.  A figure joined to text is
    text."""
    runs = runs_of(toks or [])
    if not gap.edge or len(runs) != 1 or runs[0].raw.translate(RUN_FOLD) not in page.folio_whole:
        return False
    lines = _lines_of(toks or [])
    at = next(n for n, line in enumerate(lines) if any(t is runs[0] for t in line))
    alone = not any(t.kind in (RUN, MARK) and t is not runs[0] for t in lines[at])
    return alone and (at > 0 if gap.edge == "end" else at < len(lines) - 1)


def _in_row(text: str, gap: Gap, names: Sequence[str]) -> bool:
    """Whether a run of NAMES (two characters or more) already stands
    elsewhere in the table row of the page that holds GAP: a row the page
    holds out of order, where one engine's run would be written twice
    (a historical monograph scans 541-552, 2026-10-20: 成化11 3480尺 | 3480尺)."""
    start = text.rfind("\n", 0, gap.start) + 1
    end = text.find("\n", gap.end)
    row = text[start:gap.start] + "\n" + text[gap.end:len(text) if end < 0 else end]
    if not (row.lstrip().startswith("|") or "<td" in row):
        return False
    return any(len(name) > 1 and name in row for name in names)


def _edit(scan: int, gap: Gap, new: str, rule: str, tier: str, names: Sequence[str]) -> re_.Edit:
    return re_.Edit(scan, gap.start, gap.end, new, rule=rule, evidence={
        "before": "".join(t.raw for t in gap.page), "tier": tier, "runs": list(names),
        "engineA": show(gap.a_raw or "", 160), "engineB": show(gap.b_raw or "", 160), "aIndex": gap.a_index})


def _furniture(page: PageRuns) -> list[str]:
    """The text of each engine A block of the page's furniture (a running
    head, a folio): a line engine A read in one is not text."""
    return [str(b.get("text") or "") for b in page.pd.a_blocks if b.get("label") in FURNITURE_LABELS]


def _in_furniture(seg: Sequence[Token], furniture: Sequence[str]) -> bool:
    return any("".join(t.raw for t in seg).strip() in text for text in furniture)


def _lines_own(p_toks: Sequence[Token], rest: Sequence[Token], lines: Sequence[Sequence[Token]]) -> list[Token]:
    """The marks of the page (P_TOKS) that are the whole LINES' own: a mark
    the engines do not hold outside the lines (REST, matched in order by
    coarse class), of a class the lines hold - the punctuator saw it in the
    crop and had no line to put it on."""
    outside = [coarse(t.key) for t in rest if t.kind == MARK]
    theirs: dict[tuple[str, str], int] = {}
    for seg in lines:
        for t in seg:
            if t.kind == MARK:
                theirs[coarse(t.key)] = theirs.get(coarse(t.key), 0) + 1
    gone: list[Token] = []
    i = 0
    for t in p_toks:
        if t.kind != MARK:
            continue
        key = coarse(t.key)
        j = next((n for n in range(i, len(outside)) if outside[n] == key), None)
        if j is not None:
            i = j + 1
        elif theirs.get(key):
            theirs[key] -= 1
            gone.append(t)
    return gone


def _whole_lines(page: PageRuns, gap: Gap, lines: Sequence[Sequence[Token]], plan: rr.Plan, found: list[re_.Edit],
                 marker: Gap | None = None) -> str | None:
    """N2 on one gap: the whole lines of engine A's gap, when engine B
    reads them alike and the page holds no run in the gap; the edit goes to
    FOUND, the runs woven beside the lines named in its evidence with them.
    MARKER (the gap as read, holding note markers): GAP is marker_lines'
    (the engines' tokens without the markers that open their lines, which
    are never written), and _beside_markers writes the lines.  Returns why
    the runs both engines read may stay out where no doubt says so
    (left_out names them)."""
    scan = page.scan
    # A line engine A read in a block of the page's furniture is not text.
    furniture = _furniture(page)
    lines = [seg for seg in lines if not _in_furniture(seg, furniture)]
    if not lines:
        return None
    texts = [line_text(seg) for seg in lines]
    if runs_of(gap.page):
        if fused(gap.page) != fused(gap.a) and gap.b is not None and agree(gap.a, gap.b):
            plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, "the page holds another run"), {"lines": texts}))
        return "the page holds another run"
    if gap.b is None or not runs_of(gap.b):
        plan.doubts.append(("run-one-engine", scan, _gap_detail(page, gap, "a whole line engine B did not read"),
                            {"engine": "A", "lines": texts}))
        return None
    whole = list(lines)
    if not agree(gap.a, gap.b):
        # Line by line, the engines' lines paired (line_pairs: one engine may
        # cut an entry in two that the other reads as one line): where they
        # differ in some of the whole lines only, the others are written.
        a_lines, b_lines = _lines_of(gap.a), _lines_of(gap.b)
        at = {id(seg[0]): n for n, seg in enumerate(a_lines) if seg}
        pairs = line_pairs(a_lines, b_lines)
        numbers = [at[id(seg[0])] for seg in whole]
        apart = None

        def readings(pairs: Sequence[tuple[list[int], list[int]]]) -> dict[str, list[str]]:
            return {"engineA": [run_on([line_text(a_lines[n]) for n in ns]) for ns, _ in pairs],
                    "engineB": [run_on([line_text(b_lines[m]) for m in ms]) for _, ms in pairs]}

        if pairs is not None:
            differ = [(ns, ms) for ns, ms in pairs
                      if _LOOSE_HYPHEN.sub("", _joined(a_lines, ns)) != _LOOSE_HYPHEN.sub("", _joined(b_lines, ms))]
            apart = [n for ns, _ in differ for n in ns]
            # Lines that differ in hyphens or apostrophes only are written as
            # engine A reads them (nearly), the two readings on record.
            near = [(ns, ms) for ns, ms in pairs if ns[0] not in apart and _joined(a_lines, ns) != _joined(b_lines, ms)]
            if near:
                plan.doubts.append(("run-engines-differ", scan,
                                    _gap_detail(page, gap, f"{len(near)} whole line(s) hyphens or apostrophes apart; "
                                                           "written as engine A reads them"),
                                    {**readings(near), "written": "A"}))
        if apart is None or len(apart) >= len(whole) or any(n not in numbers for n in apart):
            plan.doubts.append(("run-engines-differ", scan, _gap_detail(page, gap, "whole lines"),
                                {"engineA": texts, "engineB": show("".join(t.raw for t in gap.b), 200)}))
            return None
        if apart:
            plan.doubts.append(("run-engines-differ", scan,
                                _gap_detail(page, gap, f"{len(apart)} of {len(whole)} whole lines, left out"),
                                readings(differ)))
            lines = [seg for seg, n in zip(whole, numbers) if n not in apart]
            texts = [line_text(seg) for seg in lines]
    p_toks = gap.page
    breaks = [n for n, t in enumerate(p_toks) if t.kind == BREAK]
    # At the page's trailing edge a gap of note markers may end the page's
    # text with no break: the lines go after it.
    trailing = marker is not None and gap.edge == "end"
    why = None
    if (not breaks and not trailing) or any(t.kind in (CELL, ROW) for t in p_toks) \
            or (breaks and any(t.kind not in (BREAK, SPACE) for t in p_toks[breaks[0]:breaks[-1]])):
        # One run of line breaks on the page is the lines' place; with more,
        # which one is not given.
        why = "no single line break on the page to put the lines at"
    elif len(whole) != len(_lines_of(gap.a)) - (1 if trailing else 2):
        # Engine A holds other lines between: the lines' order among what the
        # page holds there is not given.
        why = "other lines stand among them in engine A"
    elif any(om.LIST_ITEM_RE.match(t) or t.startswith(("#", "<", "|", ">")) or line_figure(t) for t in texts):
        why = "a line would read as Markdown structure, or opens with a figure of its own"
    elif breaks and any(t.kind == STRUCT and re.search(r"[-*+]", t.raw) for t in p_toks[breaks[-1]:]) \
            and om.LIST_ITEM_RE.match(page.text[slice(*page.line_span(max(0, gap.start - 1)))]):
        why = "the lines would stand between two items of a list"
    if why is not None:
        plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, why), {"lines": texts}))
        return None
    gap_text = page.text[gap.start:gap.end]
    inside = {id(t) for seg in whole for t in seg}
    rest = [t for t in gap.a if id(t) not in inside]
    if marker is not None:
        new, tier, names = _beside_markers(page, gap, marker, lines, texts, breaks, trailing, plan)
        if new is None:
            return None
        found.append(re_.Edit(scan, gap.start, gap.end, new, rule="N2", evidence={
            "before": gap_text, "tier": tier, "lines": paragraphs(lines, gap.a, gap.b), "runs": names,
            "engineA": show(gap.a_raw or "", 160), "engineB": show(gap.b_raw or "", 160), "aIndex": gap.a_index}))
        return None
    # The rest of the gap (the marks and runs on the lines either side of the
    # whole lines) is woven first; the whole lines go in at the page's break.
    tier = "lines"
    names: list[str] = []
    if not runs_of(rest):
        base = gap_text
        # A mark of the page that the engines do not hold outside the whole
        # lines, of a class the lines written hold, is one of theirs: the
        # punctuator saw it in the crop and had no line to put it on.  It
        # goes, now that the line stands with its own.
        gone = _lines_own(gap.page, rest, lines)
        if gone:
            base = "".join(t.raw for t in gap.page if not any(t is g for g in gone))
            tier = "lines, the page's marks of theirs dropped"
        cut = base.find("\n")
        if cut < 0:
            plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, "no line break on the page to put the lines at"),
                                {"lines": texts}))
            return None
        head, tail = base[:cut], base[cut:]
    else:
        # The page's text before its break stands on the line of the
        # character before the gap, and its text after it on the line of the
        # character after: each is woven on its own with engine A's text on
        # that line (after its last break, with the break: a figure opening
        # the line is not written), so that a mark of one line is not matched
        # to the other's.
        a_cut = [n for n, t in enumerate(gap.a) if t.kind == BREAK]
        b_cut = [n for n, t in enumerate(gap.b) if t.kind == BREAK]
        woven: list[str] = []
        for p_part, a_part, b_part, line_start, edge in (
                (p_toks[:breaks[0]], gap.a[:a_cut[0]], gap.b[:b_cut[0]] if b_cut else gap.b, gap.k == 0, "start"),
                (p_toks[breaks[-1] + 1:], gap.a[a_cut[-1]:], gap.b[b_cut[-1]:] if b_cut else gap.b, True, "end")):
            p_text = "".join(t.raw for t in p_part)
            side, how, _names = weave(Gap(gap.k, gap.start, gap.start + len(p_text), tokens(p_text, "page", line_start),
                                          a_part, b_part, "".join(t.raw for t in a_part), "".join(t.raw for t in b_part),
                                          gap.a_index, edge=gap.edge if gap.edge == edge else ""))
            if side is None:
                plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, f"runs beside the whole lines: {how}"),
                                    {"lines": texts, "runs": [t.raw for t in runs_of(rest)]}))
                return None
            if runs_of(a_part):
                tier = f"{tier}, {how}"
                names.extend(_names)
            woven.append(side)
        head, tail = woven
    head, tail = head.rstrip(" \t"), tail.lstrip("\n")
    texts = paragraphs(lines, gap.a, gap.b)
    new = head + "\n\n" + "\n\n".join(texts) + "\n\n" + tail
    found.append(re_.Edit(scan, gap.start, gap.end, new, rule="N2", evidence={
        "before": gap_text, "tier": tier, "lines": texts, "runs": names, "engineA": show(gap.a_raw or "", 160),
        "engineB": show("".join(t.raw for t in gap.b), 160), "aIndex": gap.a_index}))
    return None


def _opening_markers(toks: Sequence[Token]) -> int:
    """How many note markers open the last line of TOKS (after its last
    break; _tab_marker's too)."""
    count = 0
    for t in _lines_of(toks)[-1]:
        if t.kind == MARKER or _tab_marker(t):
            count += 1
        elif t.kind not in (SPACE, MARKUP):
            break
    return count


def _beside_markers(page: PageRuns, gap: Gap, marked: Gap, lines: Sequence[Sequence[Token]], texts: Sequence[str],
                    breaks: Sequence[int], trailing: bool, plan: rr.Plan) -> tuple[str | None, str, list[str]]:
    """N2's text for a gap of note markers (_whole_lines; MARKED the gap
    with its markers, GAP marker_lines' of it): the page's part before its
    first break (all of it at the page's trailing edge when it has no break)
    with engine A's first line woven in, the whole lines as paragraphs of
    their own, then the page's part from its break on - the next note's
    line, its number and its other text as the page has them - with the
    page's marks that are the lines' own dropped from both parts (a bracket
    the punctuator saw about the lines, on the next note's line too); the
    tier, and the runs woven.  (None, why, []) with a doubt where engine A's first
    line cannot be woven, its last line - the next note's, after its number
    - holds a run, or the page holds a note number of the lines' own: more
    markers after its break than open the next note's line in either
    engine, or anything at the page's trailing edge after its break (the
    number would stand after its note)."""
    scan = page.scan
    gap_text = page.text[gap.start:gap.end]
    p_toks = gap.page
    a_lines = _lines_of(gap.a)
    a_head, a_tail = a_lines[0], ([] if trailing else a_lines[-1])
    if runs_of(a_tail):
        why = "runs beside the whole lines: on the next note's line"
        plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, why),
                            {"lines": list(texts), "runs": [t.raw for t in runs_of(a_tail)]}))
        return None, why, []
    gone = _lines_own(p_toks, a_head + a_tail, lines)
    cut = breaks[0] if breaks else len(p_toks)
    head = "".join(t.raw for t in p_toks[:cut] if not any(t is g for g in gone))
    tail = "".join(t.raw for t in p_toks[cut:] if not any(t is g for g in gone)).lstrip("\n")
    numbers = sum(1 for t in p_toks[cut:] if t.kind == MARKER)
    opening = 0 if trailing else max(_opening_markers(marked.a or []), _opening_markers(marked.b or []))
    if numbers > opening or (trailing and tail.strip()):
        why = "the page holds a note number of the lines' own after its break"
        plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, why), {"lines": list(texts)}))
        return None, why, []
    tier = "lines beside note markers" + (", the page's marks of theirs dropped" if gone else "")
    names: list[str] = []
    if runs_of(a_head):
        b_head = _lines_of(gap.b or [])[0]
        woven, how, names = weave(Gap(gap.k, gap.start, gap.start + len(head), tokens(head, "page", gap.k == 0), a_head,
                                      b_head, "".join(t.raw for t in a_head), "".join(t.raw for t in b_head),
                                      gap.a_index))
        if woven is None:
            why = f"runs beside the whole lines: {how}"
            plan.doubts.append(("run-no-place", scan, _gap_detail(page, gap, why), {"lines": list(texts), "runs": names}))
            return None, why, []
        head, tier = woven, f"{tier}, {how}"
    if trailing and not tail.strip():
        end = "\n" if gap_text.endswith("\n") else ""
    else:
        end = "\n\n" + tail
    return head.rstrip(" \t") + "\n\n" + "\n\n".join(paragraphs(lines, gap.a or [], gap.b or [])) + end, tier, list(names)


def _blank_page(setting: Setting, page: PageRuns, plan: rr.Plan) -> None:
    """A marker page sealed with no character: the engines' text of no CJK
    character - every line of it that holds a run, but the folio on a line
    of its own - written when they agree, else a doubt."""
    scan = page.scan
    a_text, b_text = page.pd.a_body, page.pd.b_body
    if pp.CJK(a_text) or pp.CJK(b_text) or page.text.strip():
        return
    a_toks, b_toks = tokens(a_text, "a", True), tokens(b_text, "b", True)
    lines = [line for line in _lines_of(a_toks) if runs_of(line)
             and not (len(runs_of(line)) == 1 and not any(t.kind == MARK for t in line)
                      and runs_of(line)[0].raw.translate(RUN_FOLD) in page.folio_whole)]
    if not lines:
        return
    texts = [line_text(seg) for seg in lines]
    if not runs_of(b_toks):
        plan.doubts.append(("run-one-engine", scan, f"a page sealed empty where engine A reads: {show(' / '.join(texts), 160)}",
                            {"engine": "A", "lines": texts}))
        return
    if not agree(a_toks, b_toks) or any(t.kind in (MARKER, OTHER, CELL, ROW) for t in a_toks + b_toks):
        plan.doubts.append(("run-engines-differ", scan,
                            f"a page sealed empty; A: {show(' / '.join(texts), 90)}; B: {show(line_text(b_toks), 90)}",
                            {"engineA": texts, "engineB": show(b_text, 200)}))
        return
    if any(om.LIST_ITEM_RE.match(t) or t.startswith(("#", "<", "|", ">")) for t in texts):
        plan.doubts.append(("run-no-place", scan, f"a page sealed empty: a line would read as Markdown structure: "
                                                   f"{show(' / '.join(texts), 150)}"[:220], {"lines": texts}))
        return
    new = "\n\n".join(texts) + "\n"
    plan.edits.append(re_.Edit(scan, 0, len(page.text), new, rule="N2", evidence={
        "before": page.text, "lines": texts, "engineB": show(b_text, 200), "aIndex": 0}))


# --- the contract at the page edges --------------------------------------------------------

def _nonblank(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def _body_end(lines: Sequence[str]) -> int:
    """How many of a page's nonblank LINES come before its foot notes
    (_ocr_markdown.foot_notes): the last of them is the line J judged the
    page by and a join runs on (a page of notes alone: 0, its last line)."""
    return om.foot_notes(lines, len(lines))


def _glued(last: Sequence[str], first: Sequence[str]) -> bool:
    """Whether a `join` of a page ending LAST with one opening FIRST would
    run two text runs together (the join runs on the line before the
    page's foot notes)."""
    last = last[:_body_end(last)]
    return bool(last and first) and re.search(rf"[{ALNUM}%\uff05]$", last[-1].rstrip()) is not None \
        and re.match(rf"[{ALNUM}]", first[0].lstrip()) is not None


def edge_safe(book: Any, texts: Mapping[int, str], order: Sequence[int], labels: Mapping[int, str] | None,
              edits: Sequence[re_.Edit], plan: rr.Plan,
              along: Mapping[int, Sequence[tuple[str, int, str, dict[str, Any]]]] | None = None,
              withdrawn: dict[int, str] | None = None) -> list[re_.Edit]:
    """EDITS without those that would change the kind of a page's first or
    last line (the joins stage labelled each boundary by it), break the
    finalize contract at a boundary, or glue two text runs across a `join`
    - the edits on the line concerned are withheld - and without an edit
    that moves the line a `join` runs on past the page's foot notes: each
    as a doubt naming the runs and lines it would have written (its
    evidence).  A page's last line is the one above its foot notes, which J
    judged and a join runs on (_body_end).  Only what the edits themselves
    bring about counts.  The doubts that stand with an edit (ALONG, by its
    id: _page_runs) are raised for those kept.  Measured on a historical monograph
    finished after #36 (2026-10-24, model-off): N2 put a note's Latin text
    back without its number among the foot notes of scan 157, whose join J
    had judged past six lines of notes; the join then ran on that line,
    gluing the body's end onto the note.  Given WITHDRAWN, such an edit
    stands and the join gives way instead (the boundary is written
    `paragraph`, as before #36; WITHDRAWN names it, with why): the line put
    back is printed text, a paragraph cut by its page's notes only layout."""
    kept = list(edits)
    if not kept:
        return kept
    labels = dict(labels) if labels else labels
    known = set(rj.contract_problems(book.assembly.compose(dict(texts)), labels)) if labels else set()
    for _ in range(8):
        trial = re_.Ledger({scan: texts[scan] for scan in order})
        trial.apply("runs", kept)
        after = trial.texts
        bad: dict[tuple[int, str], str] = {}
        moves: dict[int, str] = {}                  # an edit's id -> why
        for scan in order:
            was, now = _nonblank(texts[scan]), _nonblank(after[scan])
            if was and now:
                for which, one, two in (("first", was[0], now[0]),
                                        ("last", was[_body_end(was) - 1], now[_body_end(now) - 1])):
                    if rj.kind_of(one) != rj.kind_of(two):
                        bad[(scan, which)] = f"the page's {which} line would change kind"
        if labels:
            for problem in rj.contract_problems(book.assembly.compose(after), labels):
                m = re.search(r"boundary (\d+)", problem)
                if m and problem not in known and int(m.group(1)) in order:
                    scan = int(m.group(1))
                    bad[(scan, "first")] = problem
                    if order.index(scan):
                        bad[(order[order.index(scan) - 1], "last")] = problem
            for n, scan in enumerate(order):
                if n and labels.get(scan) == "join":
                    prev = order[n - 1]
                    if _glued(_nonblank(after[prev]), _nonblank(after[scan])) \
                            and not _glued(_nonblank(texts[prev]), _nonblank(texts[scan])):
                        bad[(scan, "first")] = bad[(prev, "last")] = "a join would glue two text runs"
                    # An edit from the joined line on that adds or takes a
                    # line there, or a note's number, moves it.
                    end = _body_end(_nonblank(texts[prev]))
                    for edit in kept:
                        if edit.page == prev and "tail" in _edges(texts[prev], edit):
                            alone = re_.Ledger({prev: texts[prev]})
                            alone.apply("runs", [edit])
                            if _body_end(_nonblank(alone.texts[prev])) != end:
                                why = "the line a join runs on past the page's foot notes would move"
                                if withdrawn is None:
                                    moves[id(edit)] = why
                                else:
                                    withdrawn[scan], labels[scan] = why, "paragraph"
                                    break

        still = []
        for edit in kept:
            why = moves.get(id(edit)) or next((bad[(edit.page, which)] for which in _edges(texts[edit.page], edit)
                                               if (edit.page, which) in bad), None)
            if why is None:
                still.append(edit)
                continue
            kind = "retry-marks-unverified" if edit.rule == "R1" else "run-no-place"
            named = {key: list(edit.evidence.get(key) or []) for key in ("runs", "lines") if edit.evidence.get(key)}
            what = [item for items in named.values() for item in items] or show(edit.after, 40)
            plan.doubts.append((kind, edit.page, f"withheld at the page's edge ({edit.rule}): {why}; {what}"[:220], named))
        if len(still) == len(kept):
            # Nothing of ours to withhold on a line concerned.
            break
        kept = still
    plan.doubts.extend(doubt for edit in kept for doubt in (along or {}).get(id(edit), ()))
    return kept


def _edges(text: str, edit: re_.Edit) -> list[str]:
    """Which of the page's edges EDIT touches: "first" its first line, "last"
    the line above its foot notes (_body_end) or what follows it up to them,
    "tail" that line or anything after it."""
    lines = [(m.start(), m.end()) for m in re.finditer(r"[^\n]*", text) if text[m.start():m.end()].strip()]
    if not lines:
        return ["first", "last", "tail"]
    body = _body_end([text[s:e] for s, e in lines])
    last = lines[body - 1][0]
    notes = lines[body][0] if 0 < body < len(lines) else len(text) + 1
    hits = (("first", edit.start <= lines[0][1]), ("last", last <= edit.end and edit.start < notes),
            ("tail", last <= edit.end))
    return [which for which, hit in hits if hit]


def runs(book: Any, model: Mapping[str, Any], ledger: re_.Ledger, order: Sequence[int],
         apply: Callable[[Sequence[re_.Edit]], None], facts: rr.BookFacts | None = None,
         labels: Mapping[int, str] | None = None, withdrawn: dict[int, str] | None = None) -> rr.Plan:
    """The stage: R1 applied through APPLY, then N1, N5, N8, N6, N7 and N2 proposed
    on the text it leaves (and checked against the finalize contract at
    the page edges).  WITHDRAWN collects the joins past foot notes that an
    edit's line gives way to (edge_safe): the caller writes them
    `paragraph`."""
    facts = facts or rr.book_facts(book, model)
    setting = Setting(book, model, facts, list(order), ledger.texts)
    plan = rr.Plan()
    part = rr.Plan()
    retry_marks(setting, part)
    plan.doubts.extend(part.doubts)
    gone: dict[int, str] = {} if withdrawn is None else withdrawn
    apply(edge_safe(book, ledger.texts, list(order), labels, part.edits, plan, withdrawn=gone))
    labels = {**labels, **{scan: "paragraph" for scan in gone}} if labels else labels
    along: dict[int, list[tuple[str, int, str, dict[str, Any]]]] = {}
    text_runs(setting, plan, along)
    plan.edits = edge_safe(book, ledger.texts, list(order), labels, plan.edits, plan, along, withdrawn=gone)
    return plan
