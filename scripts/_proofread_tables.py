"""Table witness checks, orientation detection/turning, recovery, and enforcement."""

from __future__ import annotations

import bisect
from collections import Counter
import difflib
import itertools
import json
import os
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence
import unicodedata

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from jyut_ocr.core.common import sha256_bytes  # noqa: E402
from jyut_ocr.core import tables as _tables  # noqa: E402
from jyut_ocr.core import note_markers as _note_markers  # noqa: E402
from _packet_client import PacketPending  # noqa: E402
from _proofread_core import (  # noqa: E402
    CHINESE_DIGITS, CJK, INSERTION_LIMIT, ZERO_FORMS, engine_gaps, facade_attr, gaps_of,
    is_numeral_conflict, layout_index, punctuation_counts, table_spans,
)
from _proofread_crops import image_size  # noqa: E402
from _proofread_variants import variant_fold  # noqa: E402
from _proofread_furniture import is_furniture_text  # noqa: E402
from _proofread_markers import (  # noqa: E402
    NOTE_MARK, bracket_kinds, place_brackets,
)


TABLE_MINIMUM_ROWS = 2
# How long a stretch of a table cell must be before engine B's reading of the
# page is allowed to testify about it.  Shorter matches land by coincidence.
# Measured by treating each of the 1,482 non-table blocks of the five
# calibration books as one long cell: 6 come out "read in another order" with
# 4-character pieces, 3 with 8.  The ones lost on the way are two heads that
# each engine read at a different place on the page and a 4-character
# coincidence in a name list; the three left are real order disagreements (two
# rosters printed as unruled grids, and the one table the detector missed,
# which it read column-interleaved).  On the 45 engine tables that have an
# engine-B page the whole of witness_refusal refuses the same two garbled
# tables at 8 and at 10; shorter, the testimony check starts refusing real
# tables of figures - cells of 12 or 8 characters that engine B read with a
# digit different here and there hold no piece that long - and 6 tables are
# refused in all at 6, 15 at 4.  8 is the shortest length that refuses no
# real table.
WITNESS_ANCHOR = 8


def witness_anchors(cell: str, witness: str,
                    matcher: difflib.SequenceMatcher | None = None,
                    unique: bool = True) -> list[tuple[int, int, int]]:
    """Where the pieces of one cell sit in another engine's reading of the page.

    Each piece is the longest stretch of the cell that the other reading also
    has, at least WITNESS_ANCHOR long.  With `unique` it is kept only when it
    occurs there exactly once - a stretch read twice cannot say where the cell
    is; without, every piece counts, which is the question "did the other
    engine read this text at all".  Returns (cell offset, witness offset,
    length) in cell order.
    """
    if matcher is None:
        matcher = difflib.SequenceMatcher(None, "", witness, autojunk=False)
    found: list[tuple[int, int, int]] = []
    pending = [(0, len(cell))]
    while pending:
        lo, hi = pending.pop()
        if hi - lo < WITNESS_ANCHOR:
            continue
        matcher.set_seq1(cell[lo:hi])
        match = matcher.find_longest_match(0, hi - lo, 0, len(witness))
        if match.size < WITNESS_ANCHOR:
            continue
        piece = cell[lo + match.a:lo + match.a + match.size]
        if not unique or witness.count(piece) == 1:
            found.append((lo + match.a, match.b, match.size))
        pending += [(lo, lo + match.a), (lo + match.a + match.size, hi)]
    return sorted(found)


def witness_refusal(table: "_tables.Table", witness: str | None) -> str | None:
    """Why the other engine's reading of the page will not vouch for this grid.

    A cell is one printed box, so its text is one stretch of the page, and an
    independent reading of the page meets that stretch once, in one order.
    Three ways the grid fails that.  The first two are checked on every cell
    long enough to hold two anchors, the third on every cell holding one; a
    shorter cell gives no testimony either way.

    - the other engine read none of the cell.  Without this the other two
      checks are silent exactly when the witness is worthless - a reading that
      looped, stopped early, or is missing - and a garbled long-cell table went
      through on a looped reading.  Measured on the 1,107 non-table blocks of
      16 or more characters on pages whose engine-B reading did not loop: 21
      have no piece of 8 in it at all (it skipped them, or engine A garbled
      them), none has a share between 0 and 0.2, the rest 0.2 or more; every
      long cell of the corpus's engine tables has 0.4 or more.  So the test is
      "some piece", not a share: any share up to 0.2 draws the same line.
    - two pieces of a cell come in the other reading the other way round, or
      overlap there by a whole anchor (the same stretch used twice): engine A
      spliced text from elsewhere into the cell, or repeated it.  An overlap
      shorter than an anchor is a reduplicated character (糾糾, 個個) that one
      engine read once, not a repeat - counting any overlap flagged three such
      blocks of prose.
    - two cells claim one stretch the other engine read once: a note copied
      into every row it spans, or a figure written twice.  Measured: fires on
      two engine tables, both garbled against the page (the continuation of
      the 教授要言 table in the corpus's 1910 government report p.62, where a twenty-
      character run also repeats back to back inside one cell; a list of eight
      years and eight figures in a fiscal report p.681 that came back with
      eleven years and ten figures, two of them twice).  With the prose blocks
      of each page taken as the cells of one table, it fires on one page of
      the corpus, where engine A read the end of one block again at the start
      of the next.

    This replaces a 60-character cap on cell length, which called any long cell
    "prose the layout detector swallowed".  It was a false invariant: a notes
    column holds real cells of 75, 80 and 105 characters (the corpus's 1910 government report
    p.61, transcribed by a person), and the page it was written for, p.62, is
    not prose either - it is the same table continued, which the engine garbled.
    Other candidates were measured on every engine table in the corpus and
    dropped: the longest run repeated inside a table (p.62 has 40, real tables
    up to 8, but real pages print runs of up to 34 twice, so length says
    nothing on its own); runs the table repeats more often than engine B does
    (fires on 15 real tables for runs of 2-3 characters engine B simply did not
    read); how much of the table aligns in order with engine B (0.05-1.00 on
    real tables - it reads them in another order - against 0.50 on p.62).
    """
    reading = CJK(witness or "")
    matcher = difflib.SequenceMatcher(None, "", reading, autojunk=False)
    claimed: list[tuple["_tables.Cell", int, int]] = []
    for cell in table.cells:
        text = CJK(cell.text)
        if len(text) < WITNESS_ANCHOR:
            continue
        where = f"row {cell.row + 1} column {cell.col + 1}"
        anchors = witness_anchors(text, reading, matcher)
        if len(text) >= 2 * WITNESS_ANCHOR:
            if not anchors and not witness_anchors(text, reading, matcher, unique=False):
                return (f"unwitnessed: engine B's reading holds none of the {len(text)} characters "
                        f"at {where} ({text[:10]}…) - it skipped them, looped or is missing, "
                        "so nothing vouches for the cell")
            for (a1, b1, n1), (a2, b2, n2) in zip(anchors, anchors[1:]):
                if b2 < b1 or b1 + n1 - b2 >= WITNESS_ANCHOR:
                    return (f"engine B reads {where} in another order - {text[a1:a1 + n1][:10]}… "
                            f"before {text[a2:a2 + n2][:10]}…, which it reads the other way round "
                            "or once: the cell was spliced or repeated")
        for a, b, n in anchors:
            for other, b2, n2 in claimed:
                if other is not cell and min(b + n, b2 + n2) - max(b, b2) >= WITNESS_ANCHOR:
                    return (f"{where} and row {other.row + 1} column {other.col + 1} both hold "
                            f"{text[a:a + n][:10]}…, which engine B reads once: a cell was copied")
            claimed.append((cell, b, n))
    return None


def furniture_row(table: "_tables.Table", heads: Sequence[str] = (),
                  furniture: frozenset[str] | set[str] = frozenset()) -> int | None:
    """The first row of the grid that is page furniture, or None.

    The corpus's 1910 government report p.62 came back with its running head as the table's
    first row.  Measured on the 410 rows holding text of all 47 engine tables:
    the book's recurring n-grams mark that row and no other; the profile heads
    mark none (three of the five books have measured heads, p.62's is not one).
    """
    for r in range(table.rows):
        if is_furniture_text("".join(cell.text for cell in table.cells if cell.row == r),
                             heads, furniture):
            return r
    return None


# A second reading of a grid vouches for it when the two agree cell for cell:
# at most this share of the cells holding CJK may differ, and a differing pair
# must be the same cell read with a character or two apart (vouched_grid).
VOUCH_CELLS_DIFFER = 0.1
VOUCH_CELL_DISTANCE = 2


def vouched_grid(table: "_tables.Table", witness: str | None) -> tuple["_tables.Table | None", str]:
    """Engine B's own grid of TABLE, when it agrees with engine A's, else None
    and why not.

    HunyuanOCR writes a table it reads as HTML too, so a page often holds two
    independent readings of one grid.  They agree when the two grids have the
    same rows and columns, lay their merged cells the same way, and read the
    same characters at every position (a merged cell's text repeated into every
    slot it covers; a cell with no CJK by its letters and digits): the layout -
    a rowspan, a colspan, a cell wrapped onto two lines - is then not one
    engine's word but both.  Up to VOUCH_CELLS_DIFFER of the cells holding
    text may differ by a character or two (a variant sort, a
    character one engine dropped - 張翀/張狒, 正德十一年至十三年/正德十一至十三年
    on the modern book): those are the same cell read apart, and the merge
    settles the characters; anything further - a cell shifted into its
    neighbour, a column the other engine never cut - is a different grid, and
    so is any difference in a slot a merged cell covers (an empty slot where
    engine A spans: engine B saw no merge there).  Engine B writing the
    merged cell's text in every slot it covers agrees with the merge: that is
    how a reader without rowspans writes one.
    Measured on the one modern book (609 pages, 30 engine A grids refused,
    1,450 figures in them that the rebuild from bare characters lost): engine
    B's grid vouches for 9 - 6 refused for a cell line break, 3 for merged
    cells, 326 figures; on 13 engine B wrote no grid of the same shape and on 7
    its grid differs further (a row shifted into its neighbours' cells, the
    same cell wrapped), and those 20 stay refused.
    """
    if not witness or "<table" not in witness:
        return None, "engine B wrote no grid"
    def key(text: str) -> str:
        # A cell's characters; one with no CJK (a year, a page reference, a
        # name in Latin letters) by its letters and digits, so that a figure
        # standing in another slot is a difference and a dash's width is not.
        return CJK(text) or "".join(ch for ch in text if ch.isalnum())

    keyed = [[key(text) for text in row] for row in table.grid(fill_spans=True)]
    # The slots a merged cell covers: there the other reading must agree to
    # the character, since the merge is what is being vouched for.
    covered = {(r, k) for cell in table.cells if cell.rowspan > 1 or cell.colspan > 1
               for r in range(cell.row, cell.row + cell.rowspan) for k in range(cell.col, cell.col + cell.colspan)}
    nearest: tuple[int, str] | None = None
    for match in re.finditer(r"<table\b.*?</table>", witness, flags=re.S | re.I):
        other = _tables.parse_html_table(match.group(0), source_breaks=True)
        if other is None:
            continue
        other = _tables.simplify(other)
        if (other.rows, other.cols) != (table.rows, table.cols):
            note = f"engine B's grid is {other.rows} rows x {other.cols} columns"
            nearest = nearest or (10 ** 6, note)
            continue
        got = other.grid(fill_spans=True)
        holding = differing = 0
        apart: list[str] = []
        for r in range(table.rows):
            for k in range(table.cols):
                mine, theirs = keyed[r][k], key(got[r][k])
                if not mine and not theirs:
                    continue
                holding += 1
                if mine != theirs:
                    differing += 1
                    if (r, k) in covered or _tables._edit_distance(mine, theirs) > VOUCH_CELL_DISTANCE:
                        apart.append(f"row {r + 1} column {k + 1} ({mine[:8] or 'empty'} / {theirs[:8] or 'empty'})")
        if differing > max(1, int(holding * VOUCH_CELLS_DIFFER)) or apart:
            note = (f"engine B's grid of the same shape reads {differing} of {holding} cells otherwise"
                    + (f", e.g. {apart[0]}" if apart else ""))
            if nearest is None or differing < nearest[0]:
                nearest = (differing, note)
            continue
        return other, f"vouched by engine B's grid ({holding} cells, {differing} read a character or two apart)"
    return None, nearest[1] if nearest else "engine B wrote no grid"


def engine_table(html_text: str, witness: str | None = None, heads: Sequence[str] = (),
                 furniture: frozenset[str] | set[str] = frozenset()) -> tuple[str | None, str]:
    """Convert one engine <table> to page markup, or explain the refusal.

    Detected is not the same as correct, so this refuses more than it accepts,
    and only what some evidence vouches for comes back.  A grid with a cell
    wrapped onto two lines, or with a merged cell, is refused on engine A's
    word alone and accepted when engine B's own grid agrees with it
    (vouched_grid) - as HTML when a cell is merged, Markdown otherwise.
    `witness` is the other engine's text for the same page (vouched_grid,
    witness_refusal); `heads` and `furniture` are the running heads and
    recurring n-grams of the book (furniture_row).
    """
    if "<table" not in html_text:
        return None, "not table markup"
    table = _tables.parse_html_table(html_text, source_breaks=True)
    if table is None:
        return None, "unparseable table markup"
    # A grid that only renders after repair (a rowspan off the end, a colspan
    # into a cell from above, a short row) is not one the engine described.
    if table.problems:
        return None, "inconsistent grid: " + "; ".join(table.problems[:3])
    table = _tables.simplify(table)
    # Blank rows stay in the grid; only rows holding text count toward the
    # minimum.  The old harvester dropped blank rows, but printed tables rule
    # empty rows (a tax line with no figure that year) and a rowspan counts
    # them: on a multi-page fiscal report p.29 the figures lost three of their nineteen
    # rows and no longer lined up with the nineteen labels printed beside them.
    rows = len({cell.row for cell in table.cells if cell.text.strip()})
    if rows < TABLE_MINIMUM_ROWS:
        return None, f"{rows} row(s); a table needs at least {TABLE_MINIMUM_ROWS}"
    # A line break inside a cell - <br>, a newline between two characters, or
    # the literal backslash-n this engine writes - is either the cell's own
    # text wrapping or several columns collapsed into one cell, and the grid
    # cannot say which.  The corpus has both: its 1910 government report p.71
    # collapsed every row's last four columns into one cell (價值\\n購置處\\n…),
    # while a fiscal report p.261 prints one vertical character per line inside
    # single cells and p.30 has a diagonal corner cell.  Matching a cell's
    # fragments against the width of the neighbouring rows does not separate
    # them either - every row of p.71 is collapsed the same way, so the match
    # holds for 2 of its 12 collapsed cells, and also for 3 of p.261's 31
    # wrapped cells and for p.30's corner.  What does separate them is engine
    # B's own grid: a collapsed cell is a column the other engine cut, and the
    # grids differ; a wrapped cell is one both read as one cell, with engine B
    # writing it on one line.  So the table is refused and rebuilt from the
    # page image unless engine B's grid vouches for it (vouched_grid), and
    # then the break is the cell's wrapping, written <br> (_tables.render).
    # On the modern book, 13 grids refused here held 1,450 figures the rebuild
    # from bare characters lost.
    broken = [cell for cell in table.cells if "\n" in cell.text or "\\n" in cell.text]
    vouched: "_tables.Table | None" = None
    vouch_note = ""
    if broken:
        vouched, vouch_note = vouched_grid(table, witness)
        if vouched is None or any(
                "\n" in other.text or "\\n" in other.text for other in vouched.cells
                if any((other.row, other.col) == (cell.row, cell.col) for cell in broken)):
            why = ("engine B's grid wraps the same cell" if vouched is not None else vouch_note)
            return None, (f"{len(broken)} cell(s) hold line breaks - the cell's own text wrapping, or "
                          f"several columns collapsed into one cell, which the grid cannot tell apart; {why}")
        # The engine's literal backslash-n is the cell's line break from here on.
        table = _tables.with_texts(table, [cell.text.replace("\\n", "\n") for cell in table.cells])
    # The engine's merged cells are not evidence of the page's.  Of the six
    # engine tables in the corpus whose grid keeps a merged cell after
    # simplify, every one places a merge wrong against the page (checked on the
    # renders): the sentence above a table as a full-width row, a form's title
    # printed outside its frame as a full-height column, row labels as
    # full-height columns, a year heading split one column off, sub-headings
    # put under the next column.  The one right merge, a diagonal corner cell,
    # sits in a grid garbled elsewhere.  On the engine's word alone nothing
    # vouches for a rowspan or colspan, and enforcing one would overwrite the
    # formatter's own reading of the image, so the table goes back to the
    # formatter to be rebuilt from the image as HTML - unless the second
    # reading of the grid, engine B's own, lays the merged cells the same way
    # and reads the same characters in every slot (vouched_grid): then the
    # merge is two engines' word, and the grid stands as HTML.
    merged = [cell for cell in table.cells if cell.rowspan > 1 or cell.colspan > 1]
    if merged:
        if vouched is None:
            vouched, vouch_note = vouched_grid(table, witness)
        if vouched is None:
            first = merged[0]
            return None, (f"merged layout unverified: {len(merged)} merged cell(s), e.g. row "
                          f"{first.row + 1} column {first.col + 1} ({CJK(first.text)[:10] or 'empty'}, "
                          f"{first.rowspan} row(s) x {first.colspan} column(s)); nothing but the "
                          f"engine vouches for the merge ({vouch_note})")
    # The evidence checks: every one that fails is reported, so the sealed
    # record shows the whole case against the grid, not the first symptom.
    evidence: list[str] = []
    head = furniture_row(table, heads, furniture)
    if head is not None:
        text = CJK("".join(cell.text for cell in table.cells if cell.row == head))
        evidence.append(f"row {head + 1} ({text[:16]}) is page furniture - the running head - "
                        "which the detector pulled into the grid")
    spliced = witness_refusal(table, witness)
    if spliced:
        evidence.append(spliced)
    # A cell with no CJK is outside everything that checks characters: the
    # merge aligns CJK only.  Marks alone (a printed leader ……, a dash) are
    # kept - there is nothing to misread into them and dropping them deletes
    # print.  Letters or digits must be in engine B's reading too: a fiscal
    # report p.681 came back with ASCII 00 where the page prints 〇〇, which
    # engine B read as 〇.
    squashed = re.sub(r"\s+", "", witness or "")
    unread = next((cell for cell in table.cells if cell.text and not CJK(cell.text) and any(
        unicodedata.category(ch)[0] in "LN" for ch in cell.text) and (
        re.sub(r"\s+", "", cell.text) not in squashed)), None)
    if unread is not None:
        evidence.append(f"row {unread.row + 1} column {unread.col + 1} holds {unread.text[:12]!r}, "
                        "letters or digits that neither the character merge nor engine B's "
                        "reading holds")
    if evidence:
        return None, "; ".join(evidence)
    note = f"{table.rows} rows x {table.cols} columns"
    if vouched is not None:
        note += f"; {len(broken)} wrapped cell(s), {len(merged)} merged cell(s), {vouch_note}"
    return _tables.render(table), note


ORIENT_SYSTEM = """你係古籍表格判讀員。畀你原掃描頁圖同一個現有格仔
（Markdown 或 HTML；rowspan／colspan 係跨格）。逐格對返圖，分清原圖物理位置同現有格仔位置。
今次政策係 auto：有充分圖像依據先將格仔整理成每行一筆紀錄、欄名一行嘅邏輯表。
原圖文字可以橫排或直排、由左至右或由右至左；唔可以用全書慣例、語意通順、表格高闊或
合計數目推斷方向。合計只係核對線索，唔證明邊條軸係一筆紀錄。

問題係：喺畀你嘅格仔入面，完整紀錄已經逐行排（row），定係逐列排（column）？
欄名條帶亦要對圖：如果 Nx1 係欄名而同表其餘部分清楚證明應該成為邏輯欄名一行，可以答
column；只有孤立 Nx1／1xN 條帶，睇唔到足夠表格關係，就答 unknown，唔好借用另一個表。
兩種方向都可能、格位對唔返、圖模糊或相關續頁未提供，同樣答 unknown。

只輸出兩行：
RECORD: row
或 RECORD: column
或 RECORD: unknown
EVIDENCE: 指出原圖可見嘅欄名／格線／紀錄對應及現有格仔位置；唔清楚就講缺少乜證據。
row = 現有格仔已經係邏輯行；column = 要交換軸，首筆紀錄次序另行對圖確認。"""

ORIENT_LINE = re.compile(r"^\s*RECORD:\s*(column|row|unknown)\s*$", re.I | re.M)
ORIENT_EVIDENCE_LINE = re.compile(r"^\s*EVIDENCE:[ \t]*(\S[^\n]*)$", re.I | re.M)


def first_table(text: str) -> "_tables.Table | None":
    """The table a recovered-table string holds, Markdown or HTML.

    Parsed by _tables, the parser the auditor and the regression suite use, so
    a blank row stays a row: an earlier splitter here called a blank row a
    separator (the empty set is a subset of everything) and dropped it, which
    collapsed an 18-row table to 10 and shifted every figure onto the wrong
    tax line when the grid was rotated.
    """
    tables = _tables.find_tables(text)
    return tables[0] if tables else None


def rotate_table(table_text: str) -> str:
    """Rotate a table 90 degrees counter-clockwise; merged cells turn with it.

    Not a bare transpose: printed columns run RIGHT TO LEFT, so the engine's
    last column is the first logical row.  A bare transpose would put the header
    row last - which is exactly what the corner label 年度 landing at the end of
    the engine's header row tells you has happened.
    """
    table = first_table(table_text)
    if table is None:
        return table_text
    return _tables.render(_tables.rotate_counterclockwise(table))


FIGURE_DIGITS = "〇零一二三四五六七八九"
GROUP_MARKS = "、,，"
FIGURE_TOKEN = re.compile(f"[{FIGURE_DIGITS}]+(?:[{GROUP_MARKS}][{FIGURE_DIGITS}]+)*")


def figure_groups(text: str) -> tuple[int, ...] | None:
    """How a printed figure's digits are grouped: 七、〇五八、二一九 -> (1, 3, 3).

    None when the text is anything but positional digits and group marks.
    """
    squashed = re.sub(r"\s+", "", text.translate(ZERO_FORMS))
    if not FIGURE_TOKEN.fullmatch(squashed):
        return None
    return tuple(len(group) for group in re.split(f"[{GROUP_MARKS}]", squashed))


def witness_figures(witness: str | None) -> dict[str, set[str]]:
    """Every figure of two or more digits engine B read, by its digits: the
    ways (group marks included) it wrote them."""
    found: dict[str, set[str]] = {}
    for match in FIGURE_TOKEN.finditer((witness or "").translate(ZERO_FORMS)):
        digits = re.sub(f"[{GROUP_MARKS}]", "", match.group(0))
        if len(digits) >= 2:
            found.setdefault(digits, set()).add(match.group(0))
    return found


def grouping_fits(groups: Sequence[int], size: int) -> bool:
    """Whether a figure is grouped in `size`s, counted from its last digit."""
    return 1 <= groups[0] <= size and all(n == size for n in groups[1:])


def table_places(sequence: str, target: str, cells: tuple[int, int] | None = None,
                 lines: Sequence[int] | None = None, base: int = 0) -> tuple[list[int], list[bool], int]:
    """Where each character of a table's SEQUENCE (its cells' characters in
    reading order) falls in TARGET, the merged text it is restated from:
    at[k] for each character and at[len(SEQUENCE)] where the last one ends;
    found[k], whether it is matched as written; and how many are.

    A character the merge replaced falls in the text that replaced it, one it
    deleted where it would have stood.  The end is the end of the table's
    LAST matched character, never of TARGET: an insertion after the table
    (text after it, such as the running foot) is not the last cell's.

    For the table's edge text (table_edges), a
    lopsided replacement at the table's first character - more than
    INSERTION_LIMIT characters longer than the table's characters it
    replaces, which are INSERTION_LIMIT or fewer, and not a numeral conflict
    - ends with them: what comes before is text before the table.  One at its
    last character begins with them, and what comes after is text after the
    table.  split_lopsided's rule: the table's characters begin (end) engine
    A's block, so what engine A did not read there came before (after) them.
    The alignment joins an insertion beside a table and a character the
    merge changed next to it into one replacement, and without this the
    first cell took everything from the replacement's start: on a made-up
    page (check_tables, check_table_edge_text_substitution) the title above
    the table only engine B read, and with the whole page as the target the
    prose before it too, written twice.  A replacement a character or two
    longer is a reading of the cell (a figure one engine read a digit longer)
    and stays whole in it, as does one over the whole table.

    Then what engine B read in one line or table cell with the
    table's first cell goes into it, and so for its last: LINES gives the
    line of engine B's each character of the page's merged text was read
    in (engine_b_lines_merged; TARGET starts at BASE there), CELLS the
    number of characters of the table's first and last cells.  When the
    first cell's characters that engine B read where they stand are all of
    one line of engine B's, the text before it on that line is the cell's,
    unless the line runs on before TARGET - engine B then read the block
    before on it too, and its lines say nothing of where the table begins.
    The split above cannot tell a cell engine A read short from a title:
    on made-up pages (check_tables, check_table_edge_text_cell_read_short)
    a first cell engine A read 目 and engine B 項目 left 項 on a line of its
    own above the table, a last figure engine A read a digit short left
    that digit below it, and a last cell engine A read 另如 where engine B
    read 另加五成照舊 was cut to 另加, 五成照舊 on a line below - where the
    table ends the page, the table restated against the whole page had
    kept the cell whole.
    """
    at = [0] * (len(sequence) + 1)
    found = [False] * len(sequence)
    mapped = [False] * len(sequence)
    end = 0
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, sequence, target, autojunk=False).get_opcodes():
        n, m = a2 - a1, b2 - b1
        split = (tag == "replace" and m - n > INSERTION_LIMIT >= n and (a1 == 0) != (a2 == len(sequence))
                 and not is_numeral_conflict({"a": sequence[a1:a2], "b": target[b1:b2]}))
        shift = m - n if split and a1 == 0 else 0
        for k in range(n):
            at[a1 + k] = b1 + shift + min(k, max(m - shift - 1, 0)) if tag != "delete" else b1
            found[a1 + k] = tag == "equal"
            mapped[a1 + k] = tag != "delete"
        if a2 == len(sequence) and a1 < a2:
            end = (b1 + n if split else b2) if tag != "delete" else b1
    at[len(sequence)] = end
    if cells is not None and lines is not None and sequence:

        def line_of(ks: range) -> int | None:
            """The one line of engine B's the characters KS of SEQUENCE were
            read in, where engine B read them where they stand."""
            own = {lines[base + at[k]] for k in ks if mapped[k] and at[k] < len(target)} - {-1}
            return own.pop() if len(own) == 1 else None

        q = line_of(range(cells[0]))
        start = at[0]
        while q is not None and start > 0 and lines[base + start - 1] == q:
            start -= 1
        if not (start == 0 and base > 0 and lines[base - 1] == q):
            at[0] = start
        q = line_of(range(len(sequence) - cells[1], len(sequence)))
        stop = at[len(sequence)]
        while q is not None and stop < len(target) and lines[base + stop] == q:
            stop += 1
        if not (stop == len(target) and base + stop < len(lines) and lines[base + stop] == q):
            at[len(sequence)] = stop
    return at, found, sum(found)


def restate_table(table_text: str, resolved: str, witness: str | None = None,
                  regrouped: list[str] | None = None, span: tuple[int, int] | None = None,
                  held: list[tuple[int, int]] | None = None,
                  lines: Sequence[int] | None = None) -> str | None:
    """Rewrite a recovered table using the MERGED characters.

    Structure comes from the engine; characters must come from the merge, or
    the two disagree wherever they chose different variant sorts (a table
    that said 價值 and 教員 where the merge had 價値 and 敎員 cost a page its
    table when verify_characters rejected the copy).

    Each cell is mapped into the merged text through the character alignment,
    never by counting widths: a single adjudication that drops a grid line
    read as 一, or adds a zero one engine missed, used to shift every later
    cell and scramble a page of figures.  When a cell keeps its character
    count, its own punctuation - thousands separators 、, brackets - is kept in
    place; when the count changed, the merged characters stand alone.  A cell
    with no CJK at all keeps its own text as read, spaces included (Hong Kong
    is two words): the merge has nothing to say about it, dropping it would
    delete what the engine read, and engine_table has already refused a table
    whose letters or digits engine B does not also read.

    A figure's group marks (、) are the engine's reading too, and engine A
    misplaces them: measured on a page of 21 figures, 7 came back with marks
    inside a group - a seven-digit figure grouped 1-1-2-3 where the print
    (and engine B, `witness`) groups it 1-3-3.  Where the merge changed a figure's
    digits, engine A's marks no longer fit it at all.  So a figure takes engine
    B's grouping when B read exactly the figure's merged digits, in one way,
    and either the merge changed the digit count or engine A's grouping does
    not fit the page's.  The page's grouping is what the figures both engines
    group alike show: when all their groups after the first have one size,
    a figure fits it when its own do and its first group is no longer; when
    they show several sizes, or there are none, no grouping is judged wrong.
    Otherwise engine A's marks stay, and a changed figure stands bare as
    before - no grouping is ever made up.  Each figure regrouped is noted in
    `regrouped`.

    What the table holds is the merged text from its first cell's first
    character to its last cell's last; HELD, when given, gets that stretch as
    (start, end) in CJK(RESOLVED).  The rest of the table block's stretch is
    the table's edge text: recover_tables finds it, and the page writes it
    beside the table (table_edges).  SPAN - the block's stretch, (start, end) - is where the table's
    characters are looked for, a replacement at the table's first or last
    character longer than the table's characters there is split at the table's
    edge, and what engine B read in one line or cell with the first (last)
    cell goes into it (table_places; LINES, engine B's line of each character
    of CJK(RESOLVED), engine_b_lines_merged).

    Markdown and HTML tables alike: cells are taken in reading order (row by
    row, a merged cell where its top-left slot is), and the table comes back in
    the markup it came in, spans untouched.

    Returns None when the table's text cannot be located in the merged text
    (or in SPAN), which means the two are not describing the same region.
    """
    table = first_table(table_text)
    if table is None:
        return None
    texts = [cell.text for cell in table.cells]
    sequence = "".join(CJK(text) for text in texts)
    if not sequence:
        return None
    merged_all = CJK(resolved)
    # With SPAN (the table's edge text), the table's characters are looked for
    # in its own block's stretch of the merged text only: they are engine A's
    # reading of that block, and block_spans hands each of them to it.
    base = span[0] if span is not None else 0
    if span is not None:
        merged_all = merged_all[span[0]:span[1]]
    # Position in the merged text of every character of the table sequence; the
    # end of the table is the end of its LAST matched character (table_places).
    # Engine B's lines place the first and last cells' edges only inside the
    # block's own span: against the whole page a line of engine B's that ran
    # on from the prose before would take the prose into the first cell.
    lines = lines if span is not None else None
    widths = [len(CJK(text)) for text in texts if CJK(text)]
    a2b, _, covered = table_places(sequence, merged_all, (widths[0], widths[-1]), lines, base)
    if covered < len(sequence) * 0.9:
        return None
    merged_all_end = a2b[len(sequence)]

    merged_cells: list[str | None] = []
    cursor = 0
    for text in texts:
        width = len(CJK(text))
        if not width:
            merged_cells.append(None)
            continue
        lo = a2b[cursor]
        hi = a2b[cursor + width] if cursor + width < len(sequence) else merged_all_end
        merged_cells.append(merged_all[lo:hi])
        cursor += width
    if held is not None:
        held.append((base + a2b[0], base + merged_all_end))

    read_by_b = witness_figures(witness)

    def b_reading(digits: str) -> str | None:
        ways = read_by_b.get(digits) or set()
        return next(iter(ways)) if len(ways) == 1 else None

    # The page's grouping: group sizes after the first, in figures both
    # engines read with the same digits and the same marks.
    sizes: set[int] = set()
    for text, merged_cell in zip(texts, merged_cells):
        groups = figure_groups(text)
        if groups is None or merged_cell is None or CJK(text) != merged_cell:
            continue
        other = b_reading(merged_cell)
        if other is not None and figure_groups(other) == groups:
            sizes.update(groups[1:])
    size = next(iter(sizes)) if len(sizes) == 1 else None

    rebuilt: list[str] = []
    for index, (text, merged_cell) in enumerate(zip(texts, merged_cells)):
        if merged_cell is None:
            rebuilt.append(text)
            continue
        own = figure_groups(text)
        other = b_reading(merged_cell) if own is not None and figure_groups(merged_cell) else None
        if other is not None and figure_groups(other) != own and (
                len(merged_cell) != len(CJK(text))
                or (size is not None and not grouping_fits(own, size)
                    and grouping_fits(figure_groups(other), size))):
            if regrouped is not None:
                regrouped.append(f"{text.strip()} -> {other} (engine B's grouping)")
            rebuilt.append(other)
        elif len(merged_cell) == len(CJK(text)):
            chars = iter(merged_cell)
            rebuilt.append("".join(next(chars) if CJK(ch) else ch
                                   for ch in text if ch == "\n" or not ch.isspace()))
        else:
            rebuilt.append(merged_cell)
    restated = _tables.with_texts(table, rebuilt)
    return (_tables.render_html(restated) if table.kind == "html"
            else _tables.render_markdown(restated))
TOTAL_LABELS = ("合計", "總計", "共計", "計合", "計總", "計共")


def chinese_figure(cell: str) -> int | None:
    """Parse a printed figure like 二、三一五、六六三 into 2315663.

    These are positional digit strings with 、 as the group separator, not the
    多位 form (二千三百…), so each character is one digit.  Returns None for
    anything that is not purely digits and separators.
    """
    digits = [c for c in cell if c not in "、,， \t"]
    if len(digits) < 2 or not all(c in CHINESE_DIGITS for c in digits):
        return None
    value = 0
    for c in digits:
        value = value * 10 + CHINESE_DIGITS[c]
    return value


def totals_axis(table_text: str) -> str | None:
    """Which axis holds an exact arithmetic total, as a review clue only.

    Both a total across fields and a total across records can reconcile, so
    arithmetic does not establish which axis is a record or authorize a
    transform.  A merged cell counts once, in its top-left slot.
    """
    table = first_table(table_text)
    if table is None or table.rows < 3:
        return None
    rows = table.grid()
    width = table.cols

    def labelled(cells):
        return next((i for i, c in enumerate(cells)
                     if any(t in c.replace(" ", "") for t in TOTAL_LABELS)), None)

    # A total sitting at the end of a COLUMN of labels means the figures beside
    # it accumulate down that column.
    for index, row in enumerate(rows):
        if labelled(row) is None:
            continue
        for column in range(width):
            total = chinese_figure(row[column])
            if total is None:
                continue
            series = [chinese_figure(rows[r][column]) for r in range(len(rows))
                      if r != index]
            values = [v for v in series if v is not None]
            if len(values) >= 2 and sum(values) == total:
                return "column"
    # The mirror image: a total at the end of a row of figures.
    for column in range(width):
        column_cells = [rows[r][column] for r in range(len(rows))]
        if labelled(column_cells) is None:
            continue
        for index, row in enumerate(rows):
            total = chinese_figure(row[column])
            if total is None:
                continue
            values = [v for v in (chinese_figure(c) for i, c in enumerate(row)
                                  if i != column) if v is not None]
            if len(values) >= 2 and sum(values) == total:
                return "row"

    # No 合計 LABEL, but the total is usually still there as the last figure of
    # the series.  A continuation page carries the figures without the stub -
    # a fiscal report p.25 to p.29 - so the label lives on the first page of the
    # span and only the arithmetic is left here.  Three or more large figures
    # summing exactly to a fourth is not a coincidence worth worrying about.
    def series_ends_in_total(cells: Sequence[str]) -> bool:
        figures = [v for v in (chinese_figure(c) for c in cells) if v is not None]
        return len(figures) >= 4 and sum(figures[:-1]) == figures[-1]

    columns_total = sum(1 for c in range(width)
                        if series_ends_in_total([rows[r][c] for r in range(len(rows))]))
    rows_total = sum(1 for r in rows if series_ends_in_total(r))
    if columns_total and columns_total > rows_total:
        return "column"
    if rows_total and rows_total > columns_total:
        return "row"
    return None


DIRECTION_SYSTEM = """你係古籍表格逐格核圖員。依據附上嘅原掃描圖，同現有表格、block 定位及
每格嘅零起算 row／col／rowspan／colspan，先確定圖上對應嗰一格，再判斷字嘅閱讀次序。
甲乙係同一格兩種相反讀法，必須由圖上字形位置、該格局部排字方向同相鄰格關係定案。
唔可以因為成詞、通順、常見姓名／地名、全書直排慣例、其他格嘅方向，或者另一引擎同意就倒轉。
原書可能真係印咗睇似反序或唔通順嘅字；兩個字嘅短格亦冇例外。
格仔本身可能已經轉軸；row／col 指下面提供嘅現有表格位置，唔直接代表原圖座標。
如果定位唔唯一、格內有混合方向、圖像模糊、兩個讀法都可能或都唔符，就答「兩個都唔係」。
淨係輸出 JSON array，每個候選一個 object，次序相同：
{"row": 0, "col": 1, "choice": "甲", "evidence": "圖上該格可見字序與局部方向"}
choice 只可係「甲」、「乙」、「兩個都得」或「兩個都唔係」。evidence 必須講圖像依據；唔肯定就講缺少乜。
唔好改字、補字或者推算答案。"""

# Paired marks swap when a cell is turned round: reversed as a string,
# （甲項　乙項） would read ）項乙　項甲（.
MIRRORED = dict(zip("（）()「」『』《》〈〉︵︶", "）（)(」「』『》《〉〈︶︵"))
DIRECTION_ANSWERS = {"甲": "first", "a": "first", "乙": "second", "b": "second",
                     "兩個都得": "both", "both": "both", "兩個都唔係": "neither", "neither": "neither"}


def turned_round(text: str) -> str:
    """A cell read the other way: characters reversed, paired marks mirrored."""
    return "".join(MIRRORED.get(char, char) for char in reversed(text))


def direction_candidates(table: "_tables.Table") -> list["_tables.Cell"]:
    """Header cells and both outside label axes, each physical cell once.

    A right-to-left grid can put labels on its right edge.  A spanned cell
    reaches that edge when its covered columns do, not only when its anchor
    does.  Candidate selection only schedules visual review; it never
    authorizes a reversal, including for a short label.
    """
    return [cell for cell in table.cells
            if (cell.header or cell.row == 0 or cell.col == 0
                or cell.col + cell.colspan == table.cols)
            and len(CJK(cell.text)) >= 2 and CJK(cell.text) != CJK(cell.text)[::-1]
            and chinese_figure(cell.text) is None]


def parse_direction_answers(reply: str, count: int) -> list[str | None]:
    """Parse candidate choices.  Visual decisions also require exact locators
    and evidence through parse_visual_direction_answers below."""
    try:
        match = re.search(r"\[.*\]", reply or "", re.S)
        answers = json.loads(match.group(0)) if match else []
    except (json.JSONDecodeError, AttributeError):
        answers = []
    if not isinstance(answers, list) or len(answers) != count:
        return [None] * count
    return [DIRECTION_ANSWERS.get(str(answer).strip().strip("\"'「」").lower()) for answer in answers]


def parse_visual_direction_answers(reply: str, cells: Sequence["_tables.Cell"]
                                   ) -> list[tuple[str | None, str]]:
    """Reject absent, shifted or duplicated locators and evidence-free answers.

    Legacy text-only arrays and fragments embedded in contradictory prose
    do not authorize image-backed changes.
    """
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate answer field")
            result[key] = value
        return result

    try:
        answers = json.loads(reply or "", object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        answers = []
    if not isinstance(answers, list) or len(answers) != len(cells):
        return [(None, "invalid visual answer list")] * len(cells)
    locators = [(answer.get("row"), answer.get("col")) for answer in answers
                if isinstance(answer, dict) and type(answer.get("row")) is int
                and type(answer.get("col")) is int]
    if len(set(locators)) != len(locators):
        return [(None, "duplicated cell locator")] * len(cells)
    out: list[tuple[str | None, str]] = []
    for cell, answer in zip(cells, answers):
        if (not isinstance(answer, dict) or type(answer.get("row")) is not int
                or type(answer.get("col")) is not int
                or (answer["row"], answer["col"]) != (cell.row, cell.col)):
            out.append((None, "missing or mismatched cell locator"))
            continue
        evidence = answer.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            out.append((None, "missing visual evidence"))
            continue
        choice = DIRECTION_ANSWERS.get(str(answer.get("choice", "")).strip().lower())
        out.append((choice, evidence.strip()))
    return out


def fix_cell_direction(table_text: str, client: "Client | None", max_tokens: int,
                       block: int | None = None, *, render_bytes: bytes | None = None,
                       context: str = "", table_orientation: str = "auto"
                       ) -> tuple[str, list[dict[str, Any]]]:
    """Reverse a label only after two image-backed, located visual verdicts.

    The option order is exchanged between the two questions.  Both must
    identify the same current-grid cell and support its reversed reading
    from the image.  Missing images, ambiguous correspondence and malformed
    answers preserve the reading and emit an explicit undecided record.
    The transformation changes cell text only, preserving dimensions/spans
    under either output policy.  Records bind decisions to input bytes.

    A split vote stays undecided: no third witness breaks it without a model
    call.  Neither engine is one: both read a horizontal label left to right
    whichever way it is printed, so their order inside a cell is the reading
    asked about, not evidence on it.  Measured (2026-11-15) on a vertically
    set fiscal report whose two tables print their row labels horizontally
    right to left: engine A's grids held every label reversed; engine B's own
    grid held each label of the first table in the same left-to-right order
    (19 of 19 rows, none the other way) and split those of the second over
    two cells.  One run's verdicts split on 38 of the tables' 42 candidates,
    every label among them, each answer taking the option offered first;
    engine B's order as a third vote would have kept every label reversed,
    as the split did.  Nor does the page's print direction settle it: the
    first table prints its column headings left to right beside those labels,
    two of the 38, and turning every split cell of a vertical page would have
    reversed them.
    """
    table = first_table(table_text)
    cells = direction_candidates(table) if table is not None else []
    if not cells:
        return table_text, []
    readings = [(cell.text, turned_round(cell.text)) for cell in cells]
    common = {"tableOrientation": table_orientation,
              "tableSha256": sha256_bytes(table_text.encode("utf-8")),
              "sourceImageSha256": sha256_bytes(render_bytes) if render_bytes else None,
              "context": context}

    def ask(turned_first: bool) -> list[tuple[str | None, str]]:
        listing = "\n".join(
            f"row={cell.row}, col={cell.col}, rowspan={cell.rowspan}, colspan={cell.colspan}: "
            f"甲「{turned if turned_first else read}」 乙「{read if turned_first else turned}」"
            for cell, (read, turned) in zip(cells, readings))
        reply, _ = client.ask_answering(
            DIRECTION_SYSTEM,
            f"block={block}; policy={table_orientation}; grid={table.rows} x {table.cols}\n"
            f"定位／轉軸紀錄：{context or '以表格全部格位對照原圖；不能唯一定位就未定'}\n"
            f"現有表格：\n---\n{table_text}\n---\n"
            f"要核圖嘅格（row/col 由零開始）：\n{listing}\n"
            "按圖上實際字序回答，輸出含 row、col、choice、evidence 嘅 JSON array。",
            render_bytes, max_tokens, kind="cell-direction")
        return parse_visual_direction_answers(reply, cells)

    pending_err: PacketPending | None = None
    as_read_first: list[tuple[str | None, str]] = []
    turned_first: list[tuple[str | None, str]] = []
    if not render_bytes or client is None or table.problems:
        reason = ("no source image" if not render_bytes else "no visual reviewer" if client is None
                  else "source grid requires repair: " + "; ".join(table.problems))
        as_read_first = turned_first = [(None, reason)] * len(cells)
    else:
        for is_turned, sink in ((False, as_read_first), (True, turned_first)):
            try:
                sink.extend(ask(is_turned))
            except PacketPending as err:
                if pending_err is None:
                    pending_err = err
        if pending_err is not None:
            raise pending_err
    first_order = {"first": "as read", "second": "turned"}
    second_order = {"first": "turned", "second": "as read"}
    records: list[dict[str, Any]] = []
    texts = {(cell.row, cell.col): cell.text for cell in table.cells}
    for cell, (read, turned), one, two in zip(cells, readings, as_read_first, turned_first):
        verdicts = [first_order.get(one[0], one[0]), second_order.get(two[0], two[0])]
        outcome = ("turned" if verdicts == ["turned", "turned"]
                   else "kept" if verdicts == ["as read", "as read"] else "undecided")
        if outcome == "turned":
            texts[(cell.row, cell.col)] = turned
        records.append({**common, **({"block": block} if block is not None else {}),
                        "row": cell.row, "col": cell.col,
                        "rowspan": cell.rowspan, "colspan": cell.colspan,
                        "read": read, "turned": turned, "verdicts": verdicts,
                        "visualEvidence": [one[1], two[1]], "outcome": outcome})
    if not any(record["outcome"] == "turned" for record in records):
        return table_text, records
    fixed = _tables.with_texts(table, [texts[(cell.row, cell.col)] for cell in table.cells])
    return (_tables.render_html(fixed) if table.kind == "html" else _tables.render_markdown(fixed)), records


def cells_as_read(table_text: str, records: Sequence[Mapping[str, Any]]) -> str:
    """The table with every cell fix_cell_direction turned round put back as read.

    For the whole-page pass, which has no block spans to stand a token in and
    hands the formatter the recovered tables to copy.  Its deletion guard
    compares runs of the merged text, where a label printed right to left
    stands as the engines read it, so a table copied with its labels turned
    breaks those runs and the pass is refused as having deleted them.
    Measured with a stand-in formatter that copies the tables as the prompt
    tells it: a page whose eighteen labels were turned lost the pass and its
    retry, and was written as the bare merged text with the table missing.
    So the formatter copies the cells as read, and enforce_recovered_tables
    then writes the recovered table, turned cells and all, in their place
    (it matches a written table to a recovered one whichever way its cells
    read).  `records` are fix_cell_direction's for this table.
    """
    turned = {(record["row"], record["col"]): record["read"]
              for record in records if record.get("outcome") == "turned"}
    table = first_table(table_text) if turned else None
    if table is None:
        return table_text
    read = _tables.with_texts(table, [turned.get((cell.row, cell.col), cell.text) for cell in table.cells])
    return _tables.render_html(read) if table.kind == "html" else _tables.render_markdown(read)


def _covers(text: str, wanted: str, share: float = 0.8) -> bool:
    """Whether `text` contains most of `wanted`, allowing for reading-order shuffle."""
    if not wanted:
        return False
    matcher = difflib.SequenceMatcher(None, wanted, text, autojunk=False)
    matched = sum(b.size for b in matcher.get_matching_blocks())
    return matched >= len(wanted) * share


SINGLE_LINE_KEPT = "kept — a single line of cells"


def single_line_grid(table_text: str) -> bool:
    """Whether a recovered table is one row or one column of cells."""
    table = first_table(table_text)
    return table is not None and (table.rows == 1 or table.cols == 1)


def orientation_undecided(how: str) -> bool:
    """Whether an orientation note preserves a grid pending source review."""
    return "UNDECIDED" in how or how.startswith(SINGLE_LINE_KEPT) or "NO VERDICT PARSED" in how


def parse_table_visual_verdict(reply: str, directive: str) -> str | None:
    """Accept only the two-line visual contract, never a usable fragment.

    Searching for one well-formed line would overlook a second, malformed
    or qualified directive that contradicts it.  Extra prose/directives and
    empty evidence therefore leave the decision unresolved.
    """
    lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    pattern = ORIENT_LINE if directive == "RECORD" else FIRST_LINE
    verdict = pattern.fullmatch(lines[0])
    evidence = ORIENT_EVIDENCE_LINE.fullmatch(lines[1])
    return verdict.group(1).lower() if verdict is not None and evidence is not None else None


def table_orientation_record(before: str, after: str, how: str,
                             render_bytes: bytes | None, policy: str,
                             block: int | None = None) -> dict[str, Any]:
    """Keep the source grid and bind the selected representation to its image."""
    original, output = first_table(before), first_table(after)
    return {**({"block": block} if block is not None else {}),
            "policy": policy, "decision": how,
            "outcome": "undecided" if orientation_undecided(how) else "resolved",
            "inputText": before, "outputText": after,
            "inputShape": [original.rows, original.cols] if original else None,
            "outputShape": [output.rows, output.cols] if output else None,
            "inputSha256": sha256_bytes(before.encode("utf-8")),
            "outputSha256": sha256_bytes(after.encode("utf-8")),
            "sourceImageSha256": sha256_bytes(render_bytes) if render_bytes else None}


# How the stored grid turns into the benchmark's table.  "kept": its rows are
# already the printed lines in reading order.  "rotated" (counter-clockwise):
# its last column is the first printed line - a physical grid of a vertical
# table, whose printed columns run right to left.  "flipped": its rows are the
# printed lines in the reverse order - engine A's grid of a vertical table
# read column by column from the left.  Spans turn with the cells.
TABLE_TURNS: dict[str, Callable[["_tables.Table"], "_tables.Table"]] = {
    "kept": lambda table: table,
    "rotated": lambda table: _tables.rotate_counterclockwise(table),
    "flipped": lambda table: _tables.rotate_counterclockwise(_tables.transpose(table)),
}
# Engine B's reading order settles a turn when the best turn puts at least
# READING_ORDER_PAIRS pairs of cells in engine B's order and at least
# READING_ORDER_SHARE of all the pairs it places, and no other turn ties it.
# Measured on the corpus's engine A table grids (126 grids, 11 books; 87 have
# an engine B page that is itself a table or none, 2 are single lines): of
# the 37 left, a horizontal book's 15 came out "kept" 14 times and turned
# none; on vertical books engine A's grid was the physical grid (rotated:
# 115 of 119 pairs, 10 of 10, 154 of 161), the reading-order grid (kept: 990
# of 990, 171 of 171, 120 of 120) and the columns read from the left
# (flipped: 323 of 324) - so no fixed turn of engine A's grid is right.  The
# three shapes were confirmed on the renders of four table pages.
READING_ORDER_PAIRS = 3
READING_ORDER_SHARE = 0.8
# The turns a table the structure pass wrote itself may need
# (orient_written_tables): TABLE_TURNS' three, and "transposed" - its rows
# the printed lines across the page, its columns the printed columns in
# reading order, right to left, which a turn of engine A's left-to-right
# grid never is.  Measured on the benchmark (a vertical textbook table, its
# engine grid refused): the structure answer wrote each field as a row and
# each item as a column, items right to left; engine B's reading order put
# 325 of 325 pairs of its cells in order transposed, 227 kept, 25 rotated
# and 160 flipped, and the 25 pairs of its cells engine B read on one line
# all shared a column (witness_line_axis).
WRITTEN_TABLE_TURNS: dict[str, Callable[["_tables.Table"], "_tables.Table"]] = {
    **TABLE_TURNS,
    "transposed": lambda table: _tables.transpose(table),
}
# A block this many times taller than wide (in pixels) is vertical text, this
# many times wider than tall horizontal; a page's print direction is the one
# with at least PRINT_DIRECTION_LEAD times the other's characters, and at
# least PRINT_DIRECTION_MINIMUM of them.
PRINT_DIRECTION_ASPECT = 2.0
PRINT_DIRECTION_LEAD = 2
PRINT_DIRECTION_MINIMUM = 6
# Furniture does not count: a vertical book's running head is often printed
# across the top, right to left (measured: the one block beside a vertical
# table on a pamphlet page, which would have called the page horizontal).
PRINT_DIRECTION_SKIP = frozenset({"table", "image", "figure", "seal", "number", "header", "footer",
                                  "header_image", "footer_image", "chart"})


def page_print_direction(blocks: Sequence[Mapping[str, Any]],
                         size: tuple[int, int] | None) -> str | None:
    """"vertical" or "horizontal": which way the page's own text is printed,
    from engine A's layout - a block it labelled vertical_text, or a block
    whose box is PRINT_DIRECTION_ASPECT times taller than wide (a running
    title in the margin, a heading labelled text), counts its characters as
    vertical; a box that many times wider than tall as horizontal.  Tables,
    figures, stamps, folios and running heads do not count.  None when the
    page shows too little, or both: a page holding only a table says nothing
    here.

    The benchmark's table rule turns on this: a horizontally printed table
    keeps its rows and columns; a vertically printed table's printed columns,
    right to left, become its rows top to bottom."""
    vertical = horizontal = 0
    for block in blocks:
        label = str(block.get("label") or "")
        chars = len(CJK(str(block.get("text") or "")))
        if label in PRINT_DIRECTION_SKIP or chars < 3:
            continue
        box = block.get("box") or []
        aspect = None
        if size and len(box) == 4 and box[2] > 0 and box[3] > 0:
            aspect = (float(box[3]) * size[1]) / (float(box[2]) * size[0])
        if label == "vertical_text" or (aspect is not None and aspect >= PRINT_DIRECTION_ASPECT):
            vertical += chars
        elif aspect is not None and aspect <= 1 / PRINT_DIRECTION_ASPECT:
            horizontal += chars
    if vertical >= PRINT_DIRECTION_MINIMUM and vertical >= PRINT_DIRECTION_LEAD * horizontal:
        return "vertical"
    if horizontal >= PRINT_DIRECTION_MINIMUM and horizontal >= PRINT_DIRECTION_LEAD * vertical:
        return "horizontal"
    return None


def reading_order_votes(table: "_tables.Table", witness: str | None,
                        turns: Sequence[str] = tuple(TABLE_TURNS)) -> dict[str, tuple[int, int]]:
    """For each turn, how many pairs of the turned grid's cells engine B read
    in the grid's row-major order, and how many in the other order.

    The benchmark's table, read row by row, is the page's reading order: a
    horizontal table's rows top to bottom, a vertical one's printed columns
    right to left, each cell from the top.  Engine B reads a page in that
    order.  A cell is placed by its first piece of WITNESS_ANCHOR characters
    engine B read once (witness_anchors), a shorter one of two or more when
    engine B read it whole once, variants folded (turn_records' measure in
    the old skill).  Engine B's own <table> is a grid, not a reading: no
    votes."""
    if not witness or "<table" in witness.lower():
        return {name: (0, 0) for name in turns}
    reading = variant_fold(CJK(witness))
    matcher = difflib.SequenceMatcher(None, "", reading, autojunk=False)
    at: dict[tuple[int, int, int, int], int] = {}
    for cell in table.cells:
        text = variant_fold(CJK(cell.text))
        if len(text) >= WITNESS_ANCHOR:
            anchors = witness_anchors(text, reading, matcher)
            if anchors:
                at[(cell.row, cell.col, cell.rowspan, cell.colspan)] = anchors[0][1] - anchors[0][0]
        elif len(text) >= 2 and reading.count(text) == 1:
            at[(cell.row, cell.col, cell.rowspan, cell.colspan)] = reading.index(text)
    votes: dict[str, tuple[int, int]] = {}
    for name in turns:
        placed = sorted((_turned_place(table, cell, name), at[key]) for cell in table.cells
                        if (key := (cell.row, cell.col, cell.rowspan, cell.colspan)) in at)
        agree = disagree = 0
        for (_, a1), (_, a2) in itertools.combinations(placed, 2):
            if a1 < a2:
                agree += 1
            elif a1 > a2:
                disagree += 1
        votes[name] = (agree, disagree)
    return votes


def _turned_place(table: "_tables.Table", cell: "_tables.Cell", name: str) -> tuple[int, int]:
    """Where CELL's top-left slot lands when TABLE takes the turn NAME
    (TABLE_TURNS; the arithmetic of _tables.rotate_counterclockwise)."""
    if name == "rotated":
        return table.cols - cell.col - cell.colspan, cell.row
    if name == "flipped":
        return table.rows - cell.row - cell.rowspan, cell.col
    if name == "transposed":
        return cell.col, cell.row
    return cell.row, cell.col


def reading_order_turn(table: "_tables.Table", witness: str | None,
                       turns: Sequence[str] = tuple(TABLE_TURNS)) -> tuple[str | None, str]:
    """The turn engine B's reading order settles (reading_order_votes), or
    None, and what the votes were."""
    votes = reading_order_votes(table, witness, turns)
    shown = ", ".join(f"{name} {a}/{a + d}" for name, (a, d) in votes.items())
    margins = sorted((votes[name][0] - votes[name][1], name) for name in turns)
    best = margins[-1][1]
    agree, disagree = votes[best]
    tied = len(margins) > 1 and margins[-2][0] == margins[-1][0]
    if (not tied and agree >= READING_ORDER_PAIRS
            and agree >= READING_ORDER_SHARE * (agree + disagree)):
        return best, f"engine B's reading order: {shown} pair(s) in order"
    return None, f"engine B's reading order settles nothing ({shown} pair(s) in order)"


def orient_table(table_text: str, render_bytes: bytes | None, client: "Client | None",
                 max_tokens: int, turn: Callable[[str], tuple[str, str]] | None = None,
                 table_orientation: str = "auto", *, context: str = "",
                 witness: str | None = None, print_direction: str | None = None,
                 physical: bool = False) -> tuple[str, str]:
    """The table turned the benchmark's way, and how.

    The rule is mechanical: a horizontally printed table keeps its rows and
    columns; a vertically printed one's printed columns, right to left,
    become its rows top to bottom, each column's cells, top down, its cells
    left to right, merged cells turned with them.  Nothing is reordered by
    what the cells say.  Equivalently, the table read row by row is the page's
    reading order.

    A PHYSICAL grid (a table rebuilt from the crop, whose prompt asks for the
    printed rows and columns as they stand) follows `print_direction`
    (page_print_direction) directly: vertical -> rotated, horizontal -> kept;
    but on a vertical page, where engine B's reading order settles that the
    grid's rows are its printed columns already (kept over rotated; not a
    single line of cells), it is kept: a model may answer the rebuild with
    the reading-order grid, and rotated again its rows come out in reverse
    (measured on a bench page, 2026-11-15: a price table's three rows in
    reverse, engine B's order 15 of 15 pairs kept).  Engine A's grid of a vertical table does not say how it lies - the
    physical grid, the reading-order grid, or the columns read from the left
    (TABLE_TURNS) - so for it, in order: on a vertical page an exact 合計
    settles which axis holds the printed columns (totals_axis; the old
    skill's measured rule, without a model call); a single line of cells is
    kept and sealed undecided, with no question (the old skill's measured
    guard: a nineteen-cell label column once cost a 132-second question,
    truncated, answered from another table); engine B's reading order
    (reading_order_turn); a horizontal page keeps the grid.  For either kind,
    when nothing settles it, engine B's order is asked first and then the
    source image (ORIENT_SYSTEM, ask_first_record) - the only model question
    left.  As-printed keeps the physical grid.
    """
    if table_orientation not in {"auto", "as-printed"}:
        raise ValueError(f"unknown table orientation policy: {table_orientation}")
    if table_orientation == "as-printed":
        return table_text, "kept — policy=as-printed; physical grid and spans preserved"
    table = first_table(table_text)
    if table is None:
        return table_text, "kept, UNDECIDED — policy=auto; table could not be parsed"

    def undecided(reason: str) -> tuple[str, str]:
        prefix = SINGLE_LINE_KEPT if single_line_grid(table_text) else "kept"
        return table_text, f"{prefix}, UNDECIDED — policy=auto; {reason}"

    def turned(name: str, reason: str) -> tuple[str, str]:
        out = table_text if name == "kept" else _tables.render(TABLE_TURNS[name](table))
        return out, f"{name} — {reason}"

    if table.problems:
        return undecided("source grid requires repair: " + "; ".join(table.problems))
    single = single_line_grid(table_text)
    if physical and print_direction == "vertical":
        # The rebuild prompt asks for the physical grid, but a model may write
        # the printed columns as rows already (the reading-order grid);
        # turned again, its rows come out in reverse.  Engine B's reading
        # order tells the two apart where it settles (kept or rotated); not
        # on a single line of cells, whose order says nothing of its shape.
        name, votes = (None, "") if single else reading_order_turn(table, witness, ("kept", "rotated"))
        if name == "kept":
            return turned("kept", f"vertical print, the rebuilt grid already in reading order: {votes}")
        return turned("rotated", "vertical print: the printed columns, right to left, are the rows"
                      + (f" ({votes})" if name == "rotated" else ""))
    if physical and print_direction == "horizontal":
        return turned("kept", "horizontal print keeps its rows and columns")
    if not physical and print_direction == "vertical":
        axis = totals_axis(table_text)
        if axis == "column":
            return turned("rotated", "合計 reconciles down the column")
        if axis == "row":
            return turned("kept", "合計 reconciles along the row")
    if single:
        return table_text, (f"{SINGLE_LINE_KEPT} ({table.rows} x {table.cols}); a single line of cells "
                            "does not show which way it lies, so no question was asked")
    name, votes = reading_order_turn(table, witness, ("kept", "rotated") if physical else tuple(TABLE_TURNS))
    if name is not None:
        return turned(name, votes)
    if print_direction == "horizontal":
        return turned("kept", f"horizontal print keeps its rows and columns ({votes})")
    if not render_bytes or client is None:
        return undecided(("no source image" if not render_bytes else "no visual reviewer") + f"; {votes}")
    axis = totals_axis(table_text)
    hint = f"數目核對線索：現有格仔 {axis} 軸加總吻合；呢個唔證明紀錄方向。" if axis else ""
    verdict, _ = client.ask_answering(
        ORIENT_SYSTEM,
        f"定位／相關表格上下文：{context or '只可用附圖中同一個表嘅證據'}\n"
        f"現有格仔 ({table.rows} x {table.cols})：\n---\n{table_text}\n---\n{hint}\n"
        "逐格對照原圖，再答 RECORD: row / column / unknown 同 EVIDENCE: 圖上依據。",
        render_bytes, max_tokens, kind="orient")
    axis = parse_table_visual_verdict(verdict, "RECORD")
    said = (verdict or "").strip().replace("\n", " ")[:300] or "(empty reply)"
    if axis not in {"row", "column"}:
        return undecided(said)
    if axis == "row":
        return table_text, f"kept — policy=auto; {said}"
    if turn is None:
        oriented, how = turn_records(table_text, None, ask=lambda grid: ask_first_record(
            grid, render_bytes, client, max_tokens, context=context))
    else:
        oriented, how = turn(table_text)
    if orientation_undecided(how):
        return undecided(f"axis=column; {how}; {said}")
    return oriented, f"{how} — policy=auto; {said}"


FIRST_RECORD_SYSTEM = """你係古籍表格判讀員。畀你原掃描頁圖，同埋現有格仔
（Markdown 或 HTML）。圖像覆核已確認要交換軸，令每列內容變成邏輯上嘅一行。
再逐格對圖確認：第一筆紀錄（或第一組欄名）喺現有格仔最左列，定最右列？
唔可以假定直排書一定由右至左、OCR 一定由左至右，亦唔可以單憑文字排列／語意決定。
要指出圖上原格位同現有格仔嘅對應；位置唔唯一、看唔清或欠相關續頁就答 unknown。
只輸出兩行：
FIRST: left
或 FIRST: right
或 FIRST: unknown
EVIDENCE: 指出原圖可見嘅閱讀次序同現有格仔位置；唔清楚就講缺少乜證據。"""

FIRST_LINE = re.compile(r"^\s*FIRST:\s*(left|right|unknown)\s*$", re.I | re.M)


def ask_first_record(table_text: str, render_bytes: bytes | None, client: "Client | None",
                     max_tokens: int, *, context: str = "") -> tuple[str | None, str]:
    """Read record order from the source image; missing evidence stays unknown."""
    if not render_bytes or client is None:
        return None, "no source image" if not render_bytes else "no visual reviewer"
    reply, _ = client.ask_answering(
        FIRST_RECORD_SYSTEM,
        f"定位／相關表格上下文：{context}\n格仔：\n---\n{table_text}\n---\n"
        "對住原圖答 FIRST: left / right / unknown 同 EVIDENCE: 圖上依據。",
        render_bytes, max_tokens, kind="record-order")
    first = parse_table_visual_verdict(reply, "FIRST")
    said = (reply or "").strip().replace("\n", " ")[:300] or "(empty reply)"
    return (first if first in {"left", "right"} else None), said


def turn_records(table_text: str, witness: str | None,
                 ask: Callable[[str], tuple[str | None, str]] | None = None) -> tuple[str, str]:
    """A table whose records are its columns, turned so that each record is a
    row, in reading order; and how it was turned.

    Two turns make records of columns: a transpose (the first column becomes
    the first row) and a counter-clockwise rotation (the last column becomes
    the first row).  A table the formatter or the rebuild wrote does not say
    which way its columns run (measured on three rebuilds of one vertical
    textbook table: two listed the page's columns in reading order, one in
    page order).  Engine B's reading of the page is in reading order - the
    order the benchmark's table is read in - so it decides first: each cell
    found there places its column (a cell of WITNESS_ANCHOR characters or
    more by its first piece engine B read once, a shorter one of two or more
    when engine B read it whole, once, variants folded), and every pair of
    such cells in different columns votes for the column order it shows.
    With no vote, or a tie, the page is asked (ASK, ask_first_record: is the
    first record the grid's leftmost column or its rightmost).  With no answer
    the grid is kept and the decision left undecided: no default turn.  A
    single column needs no left/right choice."""
    table = first_table(table_text)
    if table is None:
        return table_text, "kept, UNDECIDED — unparsed table"
    if table.cols == 1:
        return _tables.render(_tables.transpose(table)), "transposed (one column; no record-order ambiguity)"
    reading = variant_fold(CJK(witness or "")) if "<table" not in (witness or "").lower() else ""
    matcher = difflib.SequenceMatcher(None, "", reading, autojunk=False)
    placed: list[tuple[int, int, int]] = []
    for cell in table.cells:
        text = variant_fold(CJK(cell.text))
        if len(text) >= WITNESS_ANCHOR:
            anchors = witness_anchors(text, reading, matcher)
            if anchors:
                placed.append((cell.col, cell.col + cell.colspan - 1, anchors[0][1] - anchors[0][0]))
        elif len(text) >= 2 and reading.count(text) == 1:
            placed.append((cell.col, cell.col + cell.colspan - 1, reading.index(text)))
    forward = backward = 0
    for (lo1, hi1, at1), (lo2, hi2, at2) in itertools.combinations(placed, 2):
        if hi1 < lo2 or hi2 < lo1:
            left_first = (at1 < at2) == (hi1 < lo2)
            forward, backward = forward + left_first, backward + (not left_first)
    votes = f"engine B's reading order: {forward} pair(s) left to right, {backward} right to left"
    first = "left" if forward > backward else "right" if backward > forward else None
    if first is None and ask is not None:
        first, said = ask(table_text)
        votes += f"; asked, the first record is the {first or 'unread'} column - {said}"
    if first == "left":
        return _tables.render(_tables.transpose(table)), f"transposed ({votes})"
    if first == "right":
        return rotate_table(table_text), f"rotated ({votes})"
    return table_text, f"kept, UNDECIDED — record order unknown ({votes})"


def witness_line_axis(table: "_tables.Table", witness: str | None) -> tuple[str | None, str]:
    """Which axis of TABLE holds the cells engine B read on one line of its
    reading: "row", "column", or None; and what the pairs were.

    Engine B writes a page one printed line at a time - on a vertical page a
    printed column to a line - and the benchmark makes each printed line of
    a table one row.  So two cells engine B read on one line belong in one
    row: where they share a row of TABLE its rows are the printed lines, and
    where they share a column its columns are.  A cell is placed as in
    reading_order_votes (its first piece of WITNESS_ANCHOR characters engine
    B read once, or a shorter one of two or more read whole once), on the
    line that piece starts on.  An axis settles with at least
    READING_ORDER_PAIRS pairs and READING_ORDER_SHARE of every pair that
    shares a line (one sharing neither a row nor a column counts against
    both).  A reading that gives each cell a line of its own - a ruled table
    read band by band, one cell to a line - says nothing here, though its
    order runs field by field (measured on the benchmark: on such a page
    engine B's order put 102 of 105 pairs of the old skill's structure
    answer's table, written in the reading order, in order transposed, and
    no line held two of its cells).  Engine B's own <table> is a grid, not
    a reading."""
    if not witness or "<table" in witness.lower():
        return None, "engine B's lines: no reading"
    line_of: list[int] = []
    for number, line in enumerate(witness.split("\n")):
        line_of += [number] * len(CJK(line))
    reading = variant_fold(CJK(witness))
    if len(line_of) != len(reading):
        return None, "engine B's lines: not aligned"
    matcher = difflib.SequenceMatcher(None, "", reading, autojunk=False)
    placed: list[tuple["_tables.Cell", int]] = []
    for cell in table.cells:
        text = variant_fold(CJK(cell.text))
        at = None
        if len(text) >= WITNESS_ANCHOR:
            anchors = witness_anchors(text, reading, matcher)
            if anchors:
                at = anchors[0][1]
        elif len(text) >= 2 and reading.count(text) == 1:
            at = reading.index(text)
        if at is not None:
            placed.append((cell, line_of[at]))
    rows = columns = neither = 0
    for (one, line1), (two, line2) in itertools.combinations(placed, 2):
        if line1 != line2:
            continue
        row = one.row < two.row + two.rowspan and two.row < one.row + one.rowspan
        column = one.col < two.col + two.colspan and two.col < one.col + one.colspan
        rows += row and not column
        columns += column and not row
        neither += row == column
    shown = f"engine B's lines: {rows} pair(s) of cells on one line share a row, {columns} a column, {neither} neither"
    for axis, count in (("row", rows), ("column", columns)):
        if count >= READING_ORDER_PAIRS and count >= READING_ORDER_SHARE * (rows + columns + neither):
            return axis, shown
    return None, shown


# The axis of the stored grid that holds a printed line, for each turn
# (TABLE_TURNS, WRITTEN_TABLE_TURNS): what witness_line_axis must find for
# that turn to be taken.
TURN_AXIS = {"kept": "row", "flipped": "row", "transposed": "column", "rotated": "column"}
# What orient_written_tables says of every turn it takes: nothing but engine
# B's one reading settled it.
WRITTEN_TURN_UNCHECKED = "turned on engine B's reading alone"


def orient_written_tables(text: str, recovered: Sequence[str], witness: str | None,
                          print_direction: str | None, policy: str = "auto"
                          ) -> tuple[str, list[tuple[str, str, str]]]:
    """TEXT, an accepted structure answer, with each table it wrote itself
    turned the benchmark's way where engine B's reading settles how it lies;
    and, for each table looked at, (as written, as it now stands, how).

    The structure pass builds a table from the page where no engine grid or
    rebuild stands for it (a grid refused, or none).  Its prompt asks for the
    reading order, but nothing held the answer to it, and orient_table, which
    turns the engine grids and the rebuilds, never saw it: a model may write
    a vertical table as printed - each printed line across the page a row,
    the printed columns its columns, right to left or left to right - and it
    was delivered so (measured on the benchmark: a vertical textbook table,
    each field written as a row and each item as a column, its whole text
    stream out of order; WRITTEN_TABLE_TURNS).  The rule is orient_table's:
    the table read row by row is the page's reading order, which is engine
    B's.  So each table of TEXT that is none of RECOVERED (those were turned
    before the pass, and the pass's copies of them are put back) and holds
    more than one line of cells is turned the way engine B's reading settles
    on two counts that must agree: its order (reading_order_turn over
    WRITTEN_TABLE_TURNS) names the turn, and its lines (witness_line_axis)
    the axis that turn takes a printed line from (TURN_AXIS) - the order
    alone is the bands' on a ruled table engine B read band by band, and
    would turn a table written right.  Merged cells turn with the grid.
    When the two do not settle one turn the answer's table stands,
    undecided, and no question is asked: the pass was told the rule, and
    nothing else on the page says how its table lies.  A page printed
    horizontally keeps its rows and columns (orient_table's rule, the one
    the pass was given), and the as-printed policy keeps the grid the pass
    was asked for there, so neither is looked at.  A single line of cells
    is not either: its order says nothing of its shape.

    Both counts are one reading's, engine B's, and they can agree on a wrong
    turn: engine B may read a vertical table one printed band across the
    page to a line, several cells a line, and that reading of a table
    written right has the order and the lines of one printed column a line
    of the same table written as printed - the axes swapped (made-up page:
    a 4 x 3 table written each item a row, read band by band, came out
    transposed; on the benchmark a correctly written vertical textbook
    table's order already says transposed, 124 of 135 pairs, with one pair
    of its cells on one of engine B's lines).  Nothing on the page checks a
    turn deterministically.  A 合計 says which axis a table's records lie
    on, not which axis was printed as a line: a total printed at the foot
    of each printed column and a total printed as a column of its own
    reconcile along different axes of the same table, so a 合計 check
    would refuse a right turn on one table and vouch for a wrong one on
    the other, and the benchmark's rule is mechanical (the printed columns,
    right to left, become the rows), not read from the content.  Engine
    A's grid of the block is no check either: it lies three ways on
    vertical pages (TABLE_TURNS, measured: the physical grid, the
    reading-order grid, the columns read from the left), so its rows are
    printed lines on some pages and bands on others - the same two
    readings engine B's lines have.  So every turn is taken on engine B's
    reading alone (WRITTEN_TURN_UNCHECKED), says so, and the page raises a
    doubt for it (table-orientation-turned) for a person to check against
    the image."""
    if policy == "as-printed" or print_direction == "horizontal":
        return text, []
    recovered_at = {(start, end) for start, end, _, _ in recovered_runs(text, recovered)} if recovered else set()
    notes: list[tuple[str, str, str]] = []
    for start, end in reversed(table_spans(text)):
        if (start, end) in recovered_at:
            continue
        written = text[start:end]
        table = first_table(written)
        if table is None or table.problems or table.rows < 2 or table.cols < 2:
            continue
        name, votes = reading_order_turn(table, witness, tuple(WRITTEN_TABLE_TURNS))
        axis, lines = witness_line_axis(table, witness)
        if name is None or axis != TURN_AXIS[name]:
            notes.append((written, written, f"kept, UNDECIDED — the structure pass's table stands; {votes}; {lines}"))
            continue
        if name == "kept":
            notes.append((written, written, f"{name} — {votes}; {lines}"))
            continue
        turned = _tables.render(WRITTEN_TABLE_TURNS[name](table))
        text = text[:start] + turned + text[end:]
        notes.append((written, turned, f"{name} — {votes}; {lines}; {WRITTEN_TURN_UNCHECKED}"))
    notes.reverse()
    return text, notes


TABLE_BRACKET_REACH = 4   # characters of engine A's reading either side that place a bracket in a cell


def table_brackets(table_text: str, sources: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """TABLE_TEXT, a table the formatter rebuilt from a region, with the round
    brackets engine A printed in the region (SOURCES, its readings of the
    region's blocks, marks and markup included) written back into the cells
    they stand in; and what was done: brackets written in, the model's own
    moved (place_brackets), and the ones no cell could be found for.

    A table block gets no punctuation pass, the structure pass may add no
    mark, and the rebuild was measured leaving out every bracket: a textbook
    table printing four brackets, all read by engine A, came back from all
    three accepted rebuilds with none.  Each
    bracket is placed by engine A's characters either side of it,
    TABLE_BRACKET_REACH each way, variants folded: where the two together
    stand once in the table's cells, the bracket goes between them; where
    they do not (the bracket at a cell's edge, or a character of the context
    read otherwise), an opening bracket by the characters after it and a
    closing one by those before it, when they stand once in one cell.  A
    bracket placed twice (engine A's reading repeats a run) is written once;
    one that no cell holds is reported, never guessed.  The characters are
    never touched."""
    table = first_table(table_text)
    done: dict[str, Any] = {"inserted": 0, "moved": 0, "unplaced": []}
    if table is None:
        return table_text, done
    cells = [variant_fold(CJK(cell.text)) for cell in table.cells]
    joined = "\0".join(cells)
    starts = list(itertools.accumulate([0] + [len(cell) + 1 for cell in cells]))[:-1]

    def spot(at: int) -> tuple[int, int] | None:
        index = bisect.bisect_right(starts, at) - 1
        offset = at - starts[index]
        return (index, offset) if 0 <= offset <= len(cells[index]) else None

    wanted: dict[int, Counter] = {}
    for source in sources:
        chars, gaps = engine_gaps(source)
        folded = variant_fold(chars)
        for g, gap in enumerate(gaps):
            for kind in bracket_kinds(gap):
                before = folded[max(0, g - TABLE_BRACKET_REACH):g]
                after = folded[g:g + TABLE_BRACKET_REACH]
                found = None
                if before and after and joined.count(before + after) == 1:
                    found = spot(joined.index(before + after) + len(before))
                elif kind == "（" and len(after) >= 2 and joined.count(after) == 1:
                    found = spot(joined.index(after))
                elif kind == "）" and len(before) >= 2 and joined.count(before) == 1:
                    found = spot(joined.index(before) + len(before))
                if found is None:
                    done["unplaced"].append(f"{chars[max(0, g - TABLE_BRACKET_REACH):g]}{kind}"
                                            f"{chars[g:g + TABLE_BRACKET_REACH]}")
                    continue
                wanted.setdefault(found[0], Counter())[(found[1], kind)] = 1
    if not wanted:
        return table_text, done
    texts = []
    for index, cell in enumerate(table.cells):
        if index in wanted:
            text, inserted, moved = place_brackets(cell.text, CJK(cell.text), wanted[index])
            done["inserted"] += inserted
            done["moved"] += moved
            texts.append(text)
        else:
            texts.append(cell.text)
    if not done["inserted"] and not done["moved"]:
        return table_text, done
    fixed = _tables.with_texts(table, texts)
    return (_tables.render_html(fixed) if table.kind == "html" else _tables.render_markdown(fixed)), done


# A note marker as an engine writes it inside a table cell: a LaTeX or HTML
# superscript, a LaTeX \textcircled, or a run of circled figures
# (recover_tables).
CELL_NOTE_MARKER = re.compile(r"\$\s*\^\{[^$]{1,12}\}\s*\$|\$\s*\\textcircled\s*\{\s*[0-9]{1,2}\s*\}\s*\$"
                              r"|<sup>[^<]{1,8}</sup>|[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf\u24ea-\u24ff]+")
# An engine's LaTeX in a cell, and the \textcircled command inside one.
CELL_LATEX = re.compile(r"\$[^$\n]{1,60}\$")
TEXTCIRCLED = re.compile(r"\\textcircled\s*\{\s*([0-9]{1,2})\s*\}")
# A note number's glyphs: figures, circled figures, superscript figures.
NOTE_FIGURES = re.compile(f"[0-9\uff10-\uff19{_note_markers.NOTE_CIRCLED}{_note_markers.SUPERSCRIPTS}]+")
SUPERSCRIPT_FIGURES = str.maketrans("0123456789\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079" * 2)


def circled_figure(number: int) -> str | None:
    """The circled glyph of NUMBER (0 to 50), as \\textcircled{NUMBER} prints."""
    if number == 0:
        return "\u24ea"
    if 1 <= number <= 20:
        return chr(0x2460 + number - 1)
    if 21 <= number <= 35:
        return chr(0x3251 + number - 21)
    if 36 <= number <= 50:
        return chr(0x32B1 + number - 36)
    return None


def bare_note_call(cell: str, start: int, end: int) -> str | None:
    """The note call the LaTeX wrapper CELL[START:END] holds, written bare, or
    None when it is no note call.

    A note call: a superscript or \\textcircled wrapper around a note number
    alone (_note_markers.marker_key: figures, circled or superscript figures,
    balanced braces), not beside a formula's base or operator
    (_note_markers.math_context).  Circled figures are never an exponent, so
    a figure before them is no base (a year with its call, 1990①); an Arabic
    figure after a figure, a Latin letter or a bracket may be one (10$^{6}$,
    m$^{2}$) and stays.  Written bare: the circled glyphs as the engine read
    them, Arabic figures as superscript figures - the plain-text form of a
    printed superscript call, which stays the same note number to the footnote
    step and the repair (_note_markers reads bare superscript figures as one).
    Bare Arabic figures would join the figure or word before them (二〇〇〇兩203)
    and be no note number to either: replayed on the table measured below, the
    repair then doubted three calls as note numbers the engines read and the
    page lacks.
    """
    raw = cell[start:end]
    if not _note_markers.marker_key(raw):
        return None
    inner = TEXTCIRCLED.sub(lambda m: circled_figure(int(m.group(1))) or m.group(), raw[1:-1])
    glyphs = re.sub(r"[\s^{}]", "", inner)
    if not NOTE_FIGURES.fullmatch(glyphs):
        return None
    circled = all(NOTE_MARK.fullmatch(c) for c in glyphs)
    if _note_markers.math_context(cell, start, end, figure_base=not circled):
        return None
    return glyphs if circled else glyphs.translate(SUPERSCRIPT_FIGURES)


def bare_cell_note_calls(table_text: str) -> tuple[str, list[tuple[str, str]]]:
    """TABLE_TEXT with each note call an engine wrapped in LaTeX inside a cell
    written bare (bare_note_call), and each one so written as (wrapper, bare).
    A table with none comes back unchanged, byte for byte.

    The engines write a superscript note call as LaTeX: engine A `$ ^{⑲} $`,
    `$ ^{{①⑨}} $` or `$ ^{203} $` in a modern book's table cells, engine B
    `$ ^{19} $` in its running text.  In running text the merge keeps the
    characters alone and the run stage writes a call as its glyphs, but a
    recovered table's cells are engine A's as read (restate_table), so the
    wrapper reached the delivered book as text no page prints: measured on a
    four-page table of that book: with the eight calls in its figures' cells
    written bare, the benchmark's CER on those pages fell from 115 errors to 85.
    The footnote step pairs a bare call as it paired the wrapper: the same
    note number (_note_markers.marker_key).
    """
    table = first_table(table_text)
    if table is None or "$" not in table_text:
        return table_text, []
    written: list[tuple[str, str]] = []
    texts: list[str] = []
    for cell in table.cells:
        out, at = [], 0
        for m in CELL_LATEX.finditer(cell.text):
            bare = bare_note_call(cell.text, m.start(), m.end())
            if bare is None:
                continue
            out += [cell.text[at:m.start()], bare]
            at = m.end()
            written.append((m.group(), bare))
        texts.append("".join(out) + cell.text[at:] if out else cell.text)
    if not written:
        return table_text, []
    fixed = _tables.with_texts(table, texts)
    return (_tables.render_html(fixed) if table.kind == "html" else _tables.render_markdown(fixed)), written


def recover_tables(
    blocks: Sequence[Mapping[str, Any]], resolved: str,
    render_bytes: bytes | None = None, client: "Client | None" = None,
    max_tokens: int = 16000, witness: str | None = None,
    heads: Sequence[str] = (), furniture: frozenset[str] | set[str] = frozenset(),
    spans: Sequence[str] | None = None, lines: Sequence[int] | None = None,
    table_orientation: str = "auto",
) -> tuple[list[str], list[dict[str, Any]], list[str], list[int], dict[str, Any]]:
    """Every block the engine structured as a table, as page markup, plus refusals.

    The engine solves tables that the character merge then destroys: align_drafts
    reduces both drafts to bare CJK, so <table><tr><td>物名</td>… arrives at the
    model as 物名件數約計價值黑板一二元… with the grid gone.  Recovering it here
    costs no model call, because the work was already done upstream.

    What engine_table vouches for comes back as a Markdown table; a table with
    merged cells is refused there and left to the formatter.  `witness` is the
    other engine's text for the page, `heads` and `furniture` the book's
    running heads and recurring n-grams (engine_table).  Returns the tables,
    the refusals, the orientation notes, the position in BLOCKS each table
    came from, and what the page's seal needs besides: `doubts` ({kind,
    detail}) and `figureGrouping` (figures that took engine B's group marks,
    restate_table), and `cellDirection` (the label cells asked which way they
    read, fix_cell_direction).  The refusals, notes, doubts and records name
    blocks by their layout index (layout_index), as everything sealed does.

    `spans` are the merged text's block spans (block_spans; None where the
    page has none).  A table is looked for in its own block's span, and
    `edges` in what is returned holds, by block position, each table whose
    block's span holds text before or after its cells: the stretches of the
    merged text (CJK offsets) before it (`lead`) and after it (`trail`), the
    table as restated (`restated`, before its orientation), and whether its
    characters were found only outside its block's span (`outside`).  The page
    writes the stretches beside the table (table_edges).  Found on made-up pages
    (check_tables, check_table_edge_text): a title only engine B read above
    a table - which block_spans hands to the block after it, the table - was
    in no cell, and the table's token stood for the block's whole span, so the
    title was on neither the table nor the page, with no doubt.  `lines` is
    engine B's line of each character of the merged text
    (engine_b_lines_merged): what engine B read with a table's first or last
    cell on one line or in one cell is that cell's (table_places).
    """
    recovered: list[str] = []
    rejected: list[dict[str, Any]] = []
    orientations: list[str] = []
    sources: list[int] = []
    sealed: dict[str, Any] = {"doubts": [], "figureGrouping": [], "cellDirection": [],
                              "tableOrientationEvidence": [], "edges": {}}
    span_at = (list(itertools.accumulate((len(CJK(text)) for text in spans), initial=0))
               if spans is not None else None)
    direction = page_print_direction(blocks, image_size(render_bytes))
    for index, block in enumerate(blocks):
        if block.get("label") != "table":
            continue
        # Sealed as the layout's block number; `sources` stays positional.
        layout = layout_index(blocks, index)
        table, why = engine_table(str(block.get("text") or ""), witness, heads, furniture)
        if not table:
            rejected.append({"block": layout, "reason": why})
            continue
        regrouped: list[str] = []
        held: list[tuple[int, int]] = []
        span = (span_at[index], span_at[index + 1]) if span_at is not None else None
        restated = restate_table(table, resolved, witness, regrouped, span=span, held=held, lines=lines)
        outside = False
        if restated is None and span is not None:
            # Not in its own block's span: looked for on the whole page, as it
            # was before, so that no table is refused for it.
            regrouped, held = [], []
            restated = restate_table(table, resolved, witness, regrouped, held=held, lines=lines)
            outside = restated is not None
        if restated is not None:
            lead, trail = table_edges(span, held[-1]) if span is not None else ((0, 0), (0, 0))
            if lead[1] > lead[0] or trail[1] > trail[0]:
                sealed["edges"][index] = {"lead": lead, "trail": trail, "restated": restated,
                                          "outside": outside}
        sealed["figureGrouping"] += [f"block {layout}: {note}" for note in regrouped]
        if restated is None:
            rejected.append({"block": layout,
                             "reason": "table text not found in the merged text"})
            continue
        markers = facade_attr("CELL_NOTE_MARKER", CELL_NOTE_MARKER).findall(restated)
        note_doubt: dict[str, Any] | None = None
        if markers:
            # A note marker in a cell is engine A's reading and nothing checks
            # it: the modern book prints circled three-figure note numbers
            # (199, 200) that engine A wrote as ①⑨ and ②⑩ and engine B as ⑲
            # and ⑳ (checked against the page images, 2 of 8 vouched rows).
            note_doubt = {"kind": "table-note-marker-unverified", "detail": (
                f"TABLE NOTE MARKERS UNVERIFIED: block {layout}: {len(markers)} note marker(s) in its cells as "
                f"engine A read them ({'、'.join(m.strip() for m in markers[:6])}{'…' if len(markers) > 6 else ''}); "
                "a circled number above 20 is often misread")}
            sealed["doubts"].append(note_doubt)
        before_orientation = restated
        context = (f"block={layout}; page box (x,y,width,height)={block.get('box')}; "
                   f"source grid before orientation:\n{before_orientation}")
        try:
            restated, how = orient_table(
                restated, render_bytes, client, max_tokens,
                table_orientation=table_orientation, context=context,
                witness=witness, print_direction=direction)
            orientations.append(f"block {layout}: {how}")
            sealed["tableOrientationEvidence"].append(table_orientation_record(
                before_orientation, restated, how, render_bytes, table_orientation, block=layout))
            if orientation_undecided(how):
                sealed["doubts"].append({"kind": "table-orientation-undecided", "detail": (
                    f"TABLE ORIENTATION UNDECIDED: block {layout}: {how}")})
            restated, directions = fix_cell_direction(
                restated, client, max_tokens, block=layout, render_bytes=render_bytes,
                context=f"{context}\norientation decision: {how}", table_orientation=table_orientation)
            sealed["cellDirection"] += directions
            undecided = [record["read"] for record in directions if record["outcome"] == "undecided"]
            if undecided:
                sealed["doubts"].append({"kind": "cell-direction-undecided", "detail": (
                    f"CELL DIRECTION UNDECIDED: block {layout}: {len(undecided)} label cell(s) kept "
                    f"as read ({'、'.join(undecided[:6])}{'…' if len(undecided) > 6 else ''}); "
                    "source image evidence was absent, ambiguous or not agreed")})
        except PacketPending:
            sealed["packetPending"] = True
        # After the questions above, which are asked on the grid as read: an
        # engine's LaTeX around a cell's note call is no print of the page.
        restated, bare = bare_cell_note_calls(restated)
        records = sealed["tableOrientationEvidence"]
        if bare and records and records[-1].get("block") == layout:
            # The orientation record binds the table as it now stands: its
            # output written bare too, so it still matches the page's table
            # (status active), and what was written bare.
            output, written = bare_cell_note_calls(records[-1]["outputText"])
            records[-1].update(outputText=output, outputSha256=sha256_bytes(output.encode("utf-8")),
                               noteCallsBare=[[wrapper, call] for wrapper, call in written])
        if bare and note_doubt is None:
            # A call no form above matched, written bare all the same: still
            # engine A's reading, and unchecked.
            note_doubt = {"kind": "table-note-marker-unverified", "detail": (
                f"TABLE NOTE MARKERS UNVERIFIED: block {layout}: {len(bare)} note marker(s) in its cells as "
                f"engine A read them ({'、'.join(w.strip() for w, _ in bare[:6])}{'…' if len(bare) > 6 else ''}); "
                "a circled number above 20 is often misread")}
            sealed["doubts"].append(note_doubt)
        if bare:
            note_doubt["detail"] += ("; written bare on the page: "
                                     + "、".join(b for _, b in bare[:6]) + ("…" if len(bare) > 6 else ""))
        recovered.append(restated)
        sources.append(index)
    return recovered, rejected, orientations, sources, sealed


def table_edges(span: tuple[int, int], held: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """The stretches of a table block's SPAN, (start, end) in the merged text,
    before and after HELD, the stretch its table's cells hold: the text the
    block's span holds and no cell of the table does, which the page writes
    as text before and after the table (restate_table's edges).  A table held
    wholly outside SPAN leaves the whole span on the side it is not on."""
    s_lo, s_hi = span
    t_lo, t_hi = held
    lead = (s_lo, min(max(t_lo, s_lo), s_hi))
    return lead, (max(min(t_hi, s_hi), lead[1]), s_hi)


def _cell_key(text: str) -> str:
    """A cell's characters in sorted order: the same cell read either way round."""
    return "".join(sorted(CJK(text)))


def written_cells(text: str) -> list[str]:
    """The cell texts of a written table in reading order, including pipe rows
    written without the | --- | line, which no parser calls a table."""
    tables = _tables.find_tables(text)
    if tables:
        return [cell.text for table in tables for cell in table.cells]
    return [cell for line in text.splitlines() for cell in (_tables.table_cells(line) or [])]


def table_signature(text: str) -> list[str]:
    """The non-empty cells of a table as sorted characters, sorted: the same
    table whatever its orientation, its markup, or the direction a cell was
    read in.  A formatter that turns a cell round (a right-to-left label it
    reads the other way) still wrote the recovered table, and a signature of
    exact cell strings called such a table missing while it stood on the
    page."""
    return sorted(_cell_key(cell) for cell in written_cells(text) if CJK(cell))


def recovered_runs(formatted: str, tables: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """Written tables made of exactly the cells of recovered tables first..last,
    consecutive, as (start, end, first, last) in page order.

    Cells are compared as table_signature compares them, so a table counts
    in any orientation or markup.  first == last is one recovered table
    written out; last > first is several joined into one.
    """
    signatures = [Counter(table_signature(table)) for table in tables]
    found = []
    for start, end in table_spans(formatted):
        written = Counter(table_signature(formatted[start:end]))
        if not written:
            continue
        match = next(((first, last) for first in range(len(tables))
                      for last in range(first, len(tables))
                      if sum(signatures[first:last + 1], Counter()) == written), None)
        if match:
            found.append((start, end, *match))
    return found


# In the structure pass a recovered table stands in the text as one line of
# this token, so the formatter never writes the table itself.  Not CJK and no
# mark: the character and mark checks do not see it.
TABLE_TOKEN = "⟦T{}⟧"


def table_tokens(count: int) -> list[str]:
    return [TABLE_TOKEN.format(number + 1) for number in range(count)]


def place_recovered_tables(formatted: str, tables: Sequence[str]
                           ) -> tuple[str, str | None, list[str]]:
    """The structure pass's output with each token line replaced by its table.

    The formatter was measured rewriting recovered tables it was told to copy:
    turning them back into layout order, joining two into one, turning cells
    round, running out of tokens on a page of figures (30k tokens and a
    truncation for one page), and a recovered table that was sealed never
    reached the page.  With a token in its place it only has to keep one line.

    Each token must be there exactly once; otherwise the output is returned
    unchanged with the reason, and the caller treats it as a refused pass
    (retry, then the per-block assembly).  A table the formatter wrote anyway
    whose cells are a recovered table's (or several joined) is its own copy
    and is dropped, since the token puts the table there.  Returns the text,
    the problem or None, and a note per copy dropped.
    """
    tokens = table_tokens(len(tables))
    lost = [token for token in tokens if token not in formatted]
    repeated = [token for token in tokens if formatted.count(token) > 1]
    if lost or repeated:
        problem = "; ".join(part for part in (
            ("lost " + " ".join(lost)) if lost else "",
            ("repeated " + " ".join(repeated)) if repeated else "") if part)
        return formatted, f"table placeholder line(s) {problem} - each must be kept exactly once", []
    notes = []
    for start, end, first, last in reversed(recovered_runs(formatted, tables)):
        formatted = formatted[:start] + formatted[end:]
        notes.append(f"the formatter's own copy of table(s) {first + 1}"
                     + (f"-{last + 1}" if last > first else "") + " dropped; the recovered table stands")
    for token, table in zip(tokens, tables):
        # The token's whole line, with any list or quote marker put in front
        # of it; a token inside a line of text still becomes its own block.
        line = re.compile(rf"^[ \t]*(?:[-*+>][ \t]*|\d+[.)][ \t]*)?{re.escape(token)}[ \t]*$", re.M)
        block = f"\n\n{table}\n\n"
        formatted = (line.sub(lambda _: block, formatted, count=1) if line.search(formatted)
                     else formatted.replace(token, block, 1))
    formatted = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", formatted).strip("\n")
    return formatted, None, notes


def cells_turned_as_recovered(written: str, recovered: "_tables.Table") -> str | None:
    """A written table that stands as written (tablesDisputed) with each cell
    that reads the other way round from a recovered cell turned to match it.

    The formatter's layout stands there, but which way a label reads was
    settled by fix_cell_direction, and on the whole-page pass the formatter
    was given the cells as read (cells_as_read).  A cell that is a recovered
    cell as written is matched first, so a label that reads either way is
    never turned.  Returns None when no cell is turned, or when the table
    cannot be written again without losing text outside its cells.
    """
    got = first_table(written)
    if got is None:
        return None
    wanted = Counter(CJK(cell.text) for cell in recovered.cells if CJK(cell.text))
    texts = [cell.text for cell in got.cells]
    matched = [False] * len(texts)
    for n, text in enumerate(texts):
        if wanted[CJK(text)] > 0:
            wanted[CJK(text)] -= 1
            matched[n] = True
    turned = False
    for n, text in enumerate(texts):
        if not matched[n] and CJK(text) and wanted[CJK(text)[::-1]] > 0:
            wanted[CJK(text)[::-1]] -= 1
            texts[n], turned = turned_round(text), True
    if not turned:
        return None
    rewritten = _tables.with_texts(got, texts)
    markup = (_tables.render_html(rewritten) if got.kind == "html" or rewritten.has_spans()
              else _tables.render_markdown(rewritten))
    return markup if Counter(CJK(markup)) == Counter(CJK(written)) else None


def enforce_recovered_tables(formatted: str, tables: Sequence[str]
                             ) -> tuple[str, list[str], list[int], list[int]]:
    """Put each recovered table back where the formatter rendered it.

    The formatter is told the tables are already in reading order and to copy
    them, and it usually does; but a model looking at a page of vertical
    columns will sometimes transpose the copy back into layout order (measured
    on the same page, same decision upstream, two runs apart).  The recovered
    table's orientation was settled by the totals arithmetic or an explicit
    verdict, so an output table holding the same cells is put back in the
    recovered layout, with the recovered cell texts: which way a label cell
    reads was settled by fix_cell_direction's checked question, and a cell
    the formatter turned round anyway is put back.  On the whole-page pass
    the formatter copies the cells as the engines read them (cells_as_read),
    and this is where the turned ones are written.  A <caption> it gave the
    table stays with it.

    The recovered layout is not forced over everything.  An output table that
    merges cells the recovered grid keeps apart is the formatter's reading of
    the image, and the engine's plain grid is no evidence against it - engines
    flatten merges too - so it is kept and reported as disputed.  So is a table
    holding text outside its cells, which a replacement would delete.

    Returns the text, a note per table touched, the index of every recovered
    table that is not in the output as a table at all (written as prose, left
    out, or fenced as code), and the index of every disputed table; the caller
    flags the page for both.
    """
    notes: list[str] = []
    missing: list[int] = []
    disputed: list[int] = []
    # A written table holding exactly the cells of several recovered tables
    # is those tables joined, not a table of the page: the engine read them
    # as separate blocks.  Measured: the formatter joined a year table and
    # the label column of the next table into one three-column table, which
    # put one table's figures under the other's categories; its cells were
    # the two recovered tables' cells exactly (50 = 50).  It is put back as
    # the recovered tables, each then enforced below.
    for start, end, first, last in reversed(recovered_runs(formatted, tables)):
        if last > first:
            formatted = (formatted[:start] + "\n\n".join(tables[first:last + 1])
                         + formatted[end:])
            notes.append(f"tables {first + 1}-{last + 1} were written as one table; put back "
                         "as the separate recovered tables")
    for number, table in enumerate(tables):
        want = table_signature(table)
        recovered = first_table(table)
        if not want or recovered is None:
            continue
        kind = "HTML" if recovered.kind == "html" else "Markdown"
        for start, end in reversed(table_spans(formatted)):
            written = formatted[start:end]
            if table_signature(written) != want:
                continue
            if written.strip() == table.strip():
                break
            got = first_table(written)
            got_kind = "HTML" if got is not None and got.kind == "html" else "Markdown"
            if got is not None and _tables.simplify(got).has_spans() and (
                    _tables.simplify(got).layout() != recovered.layout()):
                disputed.append(number)
                turned = cells_turned_as_recovered(written, recovered)
                if turned is not None:
                    formatted = formatted[:start] + turned + formatted[end:]
                notes.append(f"table {number + 1}: the formatter's {got_kind} table merges cells the "
                             f"recovered {kind} table does not; kept as written - nothing vouches "
                             "for either layout" + (", its cells turned as the recovered table's"
                                                    if turned is not None else ""))
                break
            restored = _tables.with_texts(recovered, [cell.text for cell in recovered.cells])
            caption = got.caption if got is not None else ""
            if recovered.kind == "html":
                restored.caption = caption or restored.caption
                markup = _tables.render_html(restored)
            else:
                markup = (caption + "\n\n" if caption else "") + _tables.render_markdown(restored)
            if Counter(CJK(markup)) != Counter(CJK(written)):
                disputed.append(number)
                turned = cells_turned_as_recovered(written, recovered)
                if turned is not None:
                    formatted = formatted[:start] + turned + formatted[end:]
                notes.append(f"table {number + 1}: the formatter's {got_kind} table holds text "
                             "outside its cells; kept as written rather than delete it"
                             + (", its cells turned as the recovered table's" if turned is not None else ""))
            elif markup.strip() != written.strip():
                formatted = formatted[:start] + markup + formatted[end:]
                notes.append(f"table {number + 1} restored as the recovered {kind} table "
                             f"(the formatter had written a different {got_kind} table)")
            break
        else:
            missing.append(number)
            notes.append(f"table {number + 1} ({kind}) is not in the formatter output as a table")
    return formatted, notes, missing, disputed


def agreed_block_text(blocks: Sequence[Mapping[str, Any]],
                      segments: Sequence[Mapping[str, Any]]) -> list[str]:
    """Per engine-A block, the characters both engines read there: the merge's
    agreed segments, placed by their offset in engine A's stream."""
    ends = list(itertools.accumulate(len(CJK(str(b.get("text") or ""))) for b in blocks))
    out: list[list[str]] = [[] for _ in blocks]
    cursor, k = 0, 0
    for seg in segments:
        if seg["tag"] == "equal":
            for offset, ch in enumerate(seg["a"]):
                while k < len(ends) - 1 and cursor + offset >= ends[k]:
                    k += 1
                out[k].append(ch)
        cursor += len(seg["a"])
    return ["".join(chars) for chars in out]


def table_characters_lost(blocks: Sequence[Mapping[str, Any]], agreed: Sequence[str],
                          written: str, heads: Sequence[str] = (),
                          furniture: frozenset[str] | set[str] = frozenset(),
                          beside: Mapping[int, str] | None = None) -> str:
    """Characters both engines read inside the table blocks that the page lacks.

    The deletion guard (deleted_runs) compares runs of prose and leaves table
    blocks out, because a table rotated into reading order breaks the engine's
    runs by design; and tablesMissing only asks whether a recovered table is
    there.  So a refused table - whose text the formatter must rebuild - could
    lose characters without anything noticing.  Counts, not runs: the page
    must hold each character at least as often as the table blocks alone do
    (prose only adds to the page's count, so it cannot cause a report, only
    hide one).  `agreed` is agreed_block_text: characters only one engine has
    at a table's place are left out, because engine B's reading of something
    else lands in that gap - measured on the 14 accepted tables of merge-mode
    pages, the merged span of 5 holds characters their table does not (the
    book's running head in 3), which the formatter rightly removes; counting
    agreed characters, a page holding exactly the recovered tables reports
    nothing.  A table row that is the running head (furniture_row) is not
    counted either.  Measured on the existing phase-3 outputs (22 merge-mode
    pages with a table block): this reports 8, six of them sealed clean.
    Seven are losses against the page - zeros the old character class
    dropped, figures dropped from tables the old restatement scrambled, every
    table on a page the old fallback discarded; the eighth is two characters
    both engines misread and the formatter deleted, a deletion it is not
    allowed either.  Returns the lost characters in page order, or "".

    BESIDE (block position -> characters) adds blocks that are not tables
    but must be counted as if they were: the blocks a rebuilt table's
    refused region named, which stay text beside it (beside_table), each
    with what both engines read of it.
    """
    beside = beside or {}
    needed: Counter = Counter()
    for index, (block, text) in enumerate(zip(blocks, agreed)):
        if index in beside and block.get("label") != "table":
            needed.update(CJK(beside[index]))
        if block.get("label") != "table":
            continue
        needed.update(text)
        grid = _tables.parse_html_table(str(block.get("text") or ""), source_breaks=True)
        for r in range(grid.rows if grid is not None else 0):
            row = "".join(cell.text for cell in grid.cells if cell.row == r)
            if is_furniture_text(row, heads, furniture):
                needed.subtract(CJK(row))
    lost = +(needed - Counter(CJK(written)))
    out = []
    for index, (block, text) in enumerate(zip(blocks, agreed)):
        if block.get("label") == "table" or index in beside:
            for ch in (text if block.get("label") == "table" else CJK(beside[index])):
                if lost[ch] > 0:
                    out.append(ch)
                    lost[ch] -= 1
    return "".join(out)


def punctuation_kept(before: str, after: str) -> bool:
    """Every mark survives; the only additions allowed are list-number brackets."""
    gained = punctuation_counts(after) - punctuation_counts(before)
    lost = punctuation_counts(before) - punctuation_counts(after)
    return not lost and set(gained) <= {"（", "）"}


def table_block_marks(spans: Sequence[str], indices: Iterable[int], given: Sequence[str],
                      readings: Sequence[tuple[list[str], list[str]] | None]) -> Counter:
    """The marks both engines read in the table blocks INDICES that the text
    the structure pass is given there (GIVEN, by block) lacks.

    A table block gets no punctuation pass (FURNITURE_LABELS): where its grid
    is recovered, the table stands in the text with the marks engine A read
    in its cells, but where the grid is refused (tablesRejected) the block
    goes to the structure pass as its characters alone, and the pass builds
    the table from the image.  The pass is told to keep the text as given,
    so it writes the cells with the marks it sees printed in them (an
    enumeration's 、, a bracketed formula in a header), and the punctuation
    lock, counting those against a text that holds none, refused the table:
    the page was sealed with the table as one paragraph.  READINGS: each
    engine's marks tied to the merged text (engine_marks_merged,
    engine_b_marks_merged); at each gap of a block's stretch of the merged
    text (SPANS, the blocks' stretches in order) a mark counts as often as
    both engines wrote it there - one engine's reading of a mark is not
    enough, and none where either reading is None.
    """
    found: Counter = Counter()
    if len(readings) != 2 or any(reading is None for reading in readings):
        return found
    starts = list(itertools.accumulate((len(span) for span in spans), initial=0))
    for index in indices:
        both: Counter = Counter()
        for x, y in zip(*(gaps_of(reading, starts[index], len(spans[index])) for reading in readings)):
            both += punctuation_counts(x) & punctuation_counts(y)
        found += both - punctuation_counts(given[index])
    return found


def table_marks_lock(given: str, answer: str, allowed: Counter) -> tuple[str, Counter]:
    """The text the structure pass's punctuation lock holds ANSWER to, and
    the marks it adds: GIVEN, the text the pass was given with its tables
    written out, plus each mark of ALLOWED (table_block_marks) that ANSWER
    writes inside its tables beyond what GIVEN's tables hold.

    Only inside tables: the lock's purpose - the pass may not invent a mark
    in prose - is kept, and a table built from the image may carry only the
    marks both engines read in the table blocks, each as often as they read
    it.  Each mark goes on a line of its own, so a leader counts once."""
    if not allowed:
        return given, Counter()

    def in_tables(text: str) -> Counter:
        counts: Counter = Counter()
        for start, end in table_spans(text):
            counts += punctuation_counts(text[start:end])
        return counts

    extra = (in_tables(answer) - in_tables(given)) & allowed
    return (given + "\n" + "\n".join(extra.elements()) if extra else given), extra


def split_table_losses(lost: str, given: str, formatted: str, tables: str) -> tuple[str, str]:
    """LOST, characters of GIVEN (the text the structure pass was given) that
    FORMATTED (its answer) lacks, split into the prose's and the tables':
    (prose, tables), each in LOST's order.  TABLES is the text of GIVEN's
    table blocks - a recovered or rebuilt table written out, or the text of a
    table block whose grid was refused.

    furniture_unexplained is a guard on body text, whose short runs
    deleted_runs does not see; deleted_runs leaves the table blocks out, and
    so does this.  A table's text is rebuilt by the structure pass, and what
    a table lacks is table_characters_lost's to report, a doubt: refused
    instead, the table was lost as well (measured on the benchmark: a page's
    table whose retry left out one 兩 of a merged cell was sealed as a
    paragraph, every count gone, #4 0 against 100; another's first answer,
    four characters short, was refused and its retry split the table's rows,
    #4 73 against 100).  A character is counted where it went: the tables'
    share is what GIVEN's tables hold that the answer's tables
    (table_spans) lack and its prose does not hold beyond GIVEN's prose;
    the rest is the prose's.  So prose moved into a table (a lead-in, a
    header line turned into a header row) lacks nothing, and a table cell
    written as prose lacks nothing; a character both lost, the tables' share
    first."""
    if not lost or not tables:
        return lost, ""
    in_tables = Counter(CJK(tables))
    in_prose = Counter(CJK(given)) - in_tables
    out_tables: Counter = Counter()
    for start, end in table_spans(formatted):
        out_tables.update(CJK(formatted[start:end]))
    out_prose = Counter(CJK(formatted)) - out_tables
    table_share = in_tables - out_tables - (out_prose - in_prose)
    prose, table = [], []
    for char in lost:
        if table_share[char] > 0:
            table_share[char] -= 1
            table.append(char)
        else:
            prose.append(char)
    return "".join(prose), "".join(table)
