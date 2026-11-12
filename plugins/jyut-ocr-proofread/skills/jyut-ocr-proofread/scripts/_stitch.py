"""Tables the page break cut in two, written as one table: the cross-page stitch.

Every other step of phase 3 looks at one page.  A table printed across pages
therefore comes out as one table per page: the first page carries the field
labels, every later page only records, and nothing joins them.  This module
decides, from the sealed pages alone, where a table continues on the next
page, and writes the merged table on the page where it starts.  No model is
asked: what position says is checked against what structure says, and where
they do not agree the tables stay as they are and the pages carry a doubt.

Pure functions over page texts, seals and engine A's layout blocks; the
files are read and written by stitch_tables.py.  Measured on engine A's
drafts of the seven calibration books (796 pages), each page's tables turned
the way a vertical book's are: 20 page breaks have a table ending one page and
a table starting the next.  4 of them join distinct tables - position alone
would merge them - and none is merged here: one table has its own title, three
another number of fields (one of those a series of numbered timetables with
one header; one a form whose printed title, which engine A labels header, is
now read as the title it is, so position no longer holds there).  Of the 16 real continuations 11 are merged, 2 of them after the
ruled grid put their fields back in the head's rows, and 5 are left undecided
(no ruled grid found on a grey scan, or a grid the engine garbled); every
merge was checked on the renders.  A six-page year table built from engine
A's drafts alone comes out in the golden's shape, 290 of its 323 cells exact.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import difflib
import re
from typing import Any, Callable, Mapping, Sequence

import _tables
import proofread_pages as pp

# Every doubt the stitch writes starts with this, so a re-run can take the
# previous run's doubts off a page before it decides again.
DOUBT_PREFIX = "table-continuation-"
MERGED = DOUBT_PREFIX + "merged"
UNDECIDED = DOUBT_PREFIX + "undecided"
UNALIGNED = DOUBT_PREFIX + "unaligned"
BLOCKED = DOUBT_PREFIX + "blocked"
CELL_JOINED = DOUBT_PREFIX + "cell-joined"

# Engine A's labels for what is not the page's text flow, where engine A's
# block order is read (lost_table_block, tail_block).  A library stamp is
# printed on the page but interrupts nothing: a table continues past it.
FURNITURE_BLOCK_LABELS = frozenset({"header", "footer", "number", "seal", "header_image", "footer_image"})


@dataclass
class Item:
    """One piece of a page's text: a table, or a line outside every table."""
    kind: str                         # "table" or "line"
    start: int
    end: int
    text: str
    table: _tables.Table | None = None
    furniture: bool = False
    index: int = -1                   # a table's position among the page's tables


@dataclass
class PageInput:
    page: int
    text: str                          # the page as proofread (tableStitch.before when present)
    record: Mapping[str, Any]          # its seal
    blocks: Sequence[Mapping[str, Any]] = ()   # engine A's layout blocks (load_blocks)
    witness: str | None = None         # engine B's text of the page
    render: Any = None                 # Path of the render, for geometry and crops
    # furniture_test with no blocks: a line with nothing printed but marks.
    is_furniture: Callable[..., bool] = lambda text, edge=None: not printed(text)


def furniture_test(blocks: Sequence[Mapping[str, Any]], heads: Sequence[str] = (),
                   grams: frozenset[str] | set[str] = frozenset()) -> Callable[..., bool]:
    """Whether a line of a page is its furniture: nothing a table continues past
    would be interrupted by it.

    Only what the book or the page shows to be furniture: the book's measured
    running heads and recurring n-grams (is_furniture_text), engine A's folio
    and the short blocks that share its margin band (margin_furniture), a
    stamp.  A line made only of those texts, or a misread copy of one, is
    furniture; so is a line with nothing printed but marks (a rule).  Not a
    block engine A merely labelled header or footer: it puts those labels on
    body content, and a table's own title is what keeps it from continuing
    the table before - a form's printed title, labelled header, was taken for
    furniture and the pair was decided on as if the table started its page.
    A line in Latin letters or Arabic figures is text unless the folio's band
    reads it.

    The test takes the line and, for a line at the edge of the page's text
    (only furniture between it and the edge), which edge.  A line of numerals
    alone at the edge where engine A reads its folio is that folio, however
    engine A read it, unless another block of engine A reads that line as its
    own.  Measured on a table page whose folio engine A read as a string of
    Latin letters and engine B as two numerals: the merged page kept engine
    B's, after the table, and the table no longer ended its page.  Anywhere
    else - a numbered title before a table on a page with a folio - a line of
    numerals is text.
    """
    margin = pp.margin_blocks(blocks, labels=("number",)) + [b for b in blocks if b.get("label") == "seal"]
    texts = sorted({pp.CJK(str(b.get("text") or "")) for b in margin} - {""}, key=len, reverse=True)
    latin = {re.sub(r"\s+", "", str(b.get("text") or "")) for b in margin if not pp.CJK(str(b.get("text") or ""))}
    edges = folio_edges(blocks, margin)
    own = {printed(str(b.get("text") or "")) for b in blocks if not any(b is m for m in margin)} - {""}

    def test(line: str, edge: str | None = None) -> bool:
        characters, shown = pp.CJK(line), printed(line)
        if not shown:
            return True
        if edge in edges and shown not in own and all(ch in pp.NUMERAL_CHARS or ch.isdigit() for ch in shown):
            return True
        if not characters:
            return re.sub(r"\s+", "", line) in latin
        if pp.is_furniture_text(line, heads, grams):
            return True
        rest = characters
        for text in texts:
            rest = rest.replace(text, "")
        return not rest or pp.is_running_head(characters, texts)

    return test


def printed(text: str) -> str:
    """A line's characters and alphanumerics, marks and spaces left out."""
    return "".join(ch for ch in text.translate(pp.ZERO_FORMS) if ch.isalnum() or pp.CJK(ch))


def folio_edges(blocks: Sequence[Mapping[str, Any]], margin: Sequence[Mapping[str, Any]]) -> set[str]:
    """Which edge of the page's text engine A reads its folio at: "first" when
    a number block comes before every block of the page's text in engine A's
    order, "last" when after every one.  The margin band's blocks and stamps
    are not the page's text; with no other block the folio has no side."""
    body = [k for k, block in enumerate(blocks) if not any(block is m for m in margin)
            and (block.get("label") == "table" or pp.CJK(str(block.get("text") or "")))]
    edges: set[str] = set()
    for k, block in enumerate(blocks):
        if block.get("label") != "number" or not body:
            continue
        if k < body[0]:
            edges.add("first")
        elif k > body[-1]:
            edges.add("last")
    return edges


def page_items(text: str, is_furniture: Callable[..., bool]) -> list[Item]:
    """The page's tables and its other lines, in page order.  A line at either
    edge of the page, with only furniture between it and the edge, is tested
    as sitting there (furniture_test)."""
    items: list[Item] = []
    tables = _tables.find_tables(text)
    cursor = 0
    for index, table in enumerate(tables + [None]):
        stop = table.start if table is not None else len(text)
        offset = cursor
        for line in text[cursor:stop].split("\n"):
            if line.strip():
                items.append(Item("line", offset, offset + len(line), line,
                                  furniture=is_furniture(line)))
            offset += len(line) + 1
        if table is not None:
            items.append(Item("table", table.start, table.end, text[table.start:table.end],
                              table=table, index=index))
            cursor = table.end
    for edge, order in (("first", items), ("last", items[::-1])):
        for item in order:
            if item.kind != "line":
                break
            item.furniture = item.furniture or is_furniture(item.text, edge)
            if not item.furniture:
                break
    return items


def content(items: Sequence[Item]) -> list[Item]:
    return [item for item in items if not item.furniture]


# --- the seal's record of a table --------------------------------------------

def seal_entry(record: Mapping[str, Any], table_text: str) -> dict[str, Any] | None:
    """The seal's `tables` entry for a table written on the page, with the
    engine A block it came from and how it was oriented.

    A recovered table stands on the page as sealed, so the text matches; a
    table the formatter built from the image has no entry, no block and no
    orientation.
    """
    wanted = _cells(table_text)
    for entry in record.get("tables") or []:
        if _cells(entry.get("text") or "") == wanted:
            block = entry.get("block")
            return {"block": block, "orientation": orientation_of(record, block)}
    return None


def _cells(text: str) -> list[list[str]] | None:
    table = pp.first_table(text)
    return table.grid() if table is not None else None


def orientation_of(record: Mapping[str, Any], block: Any) -> str | None:
    """"rotated" when the page turned the engine's grid (records were printed
    columns), "kept" when it kept it, "line" for a single line of cells kept
    undecided; None when the formatter or the rebuild wrote the layout, which
    says nothing about the engine's grid."""
    for note in record.get("tableOrientation") or []:
        head, _, how = str(note).partition(": ")
        if head != f"block {block}":
            continue
        if how.startswith("rebuilt"):
            return None
        if how.startswith("rotated"):
            return "rotated"
        if how.startswith(pp.SINGLE_LINE_KEPT):
            return "line"
        if how.startswith("kept"):
            return "kept"
    return None


# --- rows and cells -----------------------------------------------------------

def cell_class(text: str) -> str:
    """"blank" (nothing printed), "mark" (marks alone: a ditto mark 〃, a
    leader …… or a dash printed for nil), "figure" (a positional figure,
    二、三一五 or 1,250), "numeral" (one digit, 四: a count, or a numbered
    label) or "text".

    A mark is printed ink in a ruled cell like any figure: a cell holding one
    is written, and whatever moves the cell moves the mark.  Counted blank, a
    record's leader cell (engine A reads …… for nil in a year table's cells)
    could never match the ink its ruled cells hold, and was placed without
    it."""
    squashed = re.sub(r"\s+", "", text.translate(pp.ZERO_FORMS))
    if not squashed:
        return "blank"
    if not pp.CJK(squashed) and not any(ch.isalnum() for ch in squashed):
        return "mark"
    if pp.chinese_figure(squashed) is not None or re.fullmatch(r"[0-9][0-9,.，、]*", squashed):
        return "figure"
    if len(squashed) == 1 and squashed in pp.CHINESE_DIGITS:
        return "numeral"
    return "text"


def keys(row: Sequence[str]) -> tuple[str, ...]:
    """A row's cells as sorted characters: a label read right to left is the
    same label (the page's direction question may have turned one copy and not
    the other)."""
    return tuple("".join(sorted(pp.CJK(cell))) for cell in row)


def is_label_row(row: Sequence[str], body: Sequence[Sequence[str]]) -> bool:
    """Whether a row labels the columns of the rows given as its body.

    From the cells, not from the markup: a Markdown table's first row is its
    header whatever it holds, and a continuation page's first record is
    written there.  A label row holds no figure and at least two labels, and
    some column it labels holds figures - the contrast that makes a label a
    label.  A table of text alone shows no contrast, so its first row is never
    called a label row here.  A lone digit keeps a row from being one too: a
    record of counts (a volume count of 四 beside a title) has them, and on a
    list of corrections printed over three pages, whose engine grid was
    garbled, a first record of a ditto mark, a lone digit and two words was
    otherwise read as a header of its own.
    """
    classes = [cell_class(cell) for cell in row]
    if "figure" in classes or "numeral" in classes or classes.count("text") < 2:
        return False
    for column, kind in enumerate(classes):
        if kind != "text":
            continue
        below = Counter(cell_class(r[column]) for r in body if column < len(r))
        if below["figure"] and below["figure"] >= below["text"]:
            return True
    return False


def own_title(table: _tables.Table) -> str | None:
    """A title the table carries itself: its caption, or a first row that is
    one cell across the whole width."""
    if table.caption.strip():
        return table.caption.strip()
    first = [cell for cell in table.cells if cell.row == 0]
    if len(first) == 1 and first[0].colspan == table.cols and table.cols > 1 and pp.CJK(first[0].text):
        return first[0].text.strip()
    return None


def turned(table: _tables.Table, turns: int) -> _tables.Table:
    """The grid turned counter-clockwise `turns` times (3 = once clockwise)."""
    for _ in range(turns % 4):
        table = _tables.rotate_counterclockwise(table)
    return table


def appended(head: _tables.Table, part: _tables.Table, skip: int) -> _tables.Table:
    """The head with the part's rows from `skip` on below it."""
    offset = head.rows - skip
    cells = list(head.cells) + [replace(cell, row=cell.row + offset, header=False)
                                for cell in part.cells if cell.row >= skip]
    cells.sort(key=lambda cell: (cell.row, cell.col))
    merged = replace(head, rows=head.rows + part.rows - skip, cells=cells, problems=[])
    return replace(merged, kind="html" if merged.has_spans() else head.kind)


def record_labels(table: _tables.Table, first: int = 0) -> list[str]:
    """What names each row: its first non-blank cell, short."""
    out = []
    for row in table.grid()[first:]:
        label = next((cell for cell in row if pp.CJK(cell) or cell.strip()), "")
        out.append(re.sub(r"\s+", "", label)[:12])
    return out


# --- decisions -----------------------------------------------------------------

@dataclass
class Chain:
    """A table and the pages its continuations were found on."""
    page: int
    item: Item
    entry: dict[str, Any] | None
    table: _tables.Table
    orientation: str | None
    label_rows: int                     # 1 when the table starts with a label row
    last: int
    parts: list[dict[str, Any]] = field(default_factory=list)
    turned_from_continuation: str | None = None
    last_block: int | None = None       # engine A block of the table ending the chain's last page


@dataclass
class Decision:
    verdict: str                        # merge | separate | undecided | blocked
    pages: tuple[int, int]
    evidence: list[str]
    part: _tables.Table | None = None   # the continuation, oriented and aligned to the head
    skip: int = 0                       # its rows dropped (a repeated label row)
    record: dict[str, Any] = field(default_factory=dict)
    head_table: _tables.Table | None = None   # the head, when the continuation settled its orientation
    joined_head: _tables.Table | None = None  # the head with the tail of its cut cell joined


def start_chain(page: PageInput, item: Item) -> Chain:
    entry = seal_entry(page.record, item.text)
    table = item.table
    grid = table.grid()
    labels = 1 if table.rows > 1 and is_label_row(grid[0], grid[1:]) else 0
    return Chain(page.page, item, entry, table, (entry or {}).get("orientation"), labels, page.page,
                 last_block=(entry or {}).get("block"))


def head_series(items: Sequence[Item], head: Item) -> int:
    """How many tables on the head's page print the head's first row and have
    a title line right before them.  Numbered timetables printed two or more
    to a page, each titled, all with one header, are distinct tables: the
    corpus has such a series continuing onto the next page."""
    content_items = content(items)
    wanted = keys(head.table.grid()[0])
    count = 0
    for position, item in enumerate(content_items):
        if item.kind != "table" or keys(item.table.grid()[0]) != wanted:
            continue
        if position and content_items[position - 1].kind == "line":
            count += 1
    return count


def decide(chain: Chain, head_items: Sequence[Item], page: PageInput, first: Item,
           realign: "Realigner | None" = None, witness: str | None = None) -> Decision:
    """Whether `first`, the first content of the page after the chain's last,
    continues the chain's table.

    Position is already given: the chain's table ends its page and `first`
    starts this one.  Against a merge, any one of: the part carries a title of
    its own; its first row is a label row that differs from the head's; the
    head's page prints a series of titled tables with the head's header and
    the part repeats that header.  For a merge, one structural fact: the part
    repeats the head's label row (dropped), or holds the head's number of
    fields (orientation taken from the head where the page's own verdict went
    the other way), or a realignment the geometry vouches for.  Position with
    no structural fact is left undecided: two tables with a different number
    of fields cannot be written as one without knowing which field is which.
    A first record that is the tail of the head's last cell is joined to that
    cell (tail_record, join_cells); `witness` is engine B's reading of the
    page before.
    """
    pages = (chain.last, page.page)
    table = first.table
    evidence = [f"p.{chain.last}: the table ends the page; p.{page.page}: table {first.index + 1} starts it"]
    title = own_title(table)
    if title:
        return Decision("separate", pages, evidence + [f"the table on p.{page.page} has a title of its own ({title[:16]})"])
    entry = seal_entry(page.record, first.text)
    head = chain.table
    record: dict[str, Any] = {"page": page.page, "table": first.index, "kind": "records"}
    own_orientation = (entry or {}).get("orientation")

    # Orientation.  A continuation prints no totals label, so the page's own
    # verdict could go the other way from the head's; the head's stands when
    # the part's other axis holds the head's field count.
    head_table = None
    if (chain.orientation in ("rotated", "kept") and own_orientation in ("rotated", "kept")
            and own_orientation != chain.orientation and table.cols != head.cols
            and table.rows == head.cols):
        table = turned(table, 1 if chain.orientation == "rotated" else 3)
        record["orientation"] = f"turned to the head's ({chain.orientation}; the page's own verdict was {own_orientation})"
        evidence.append(f"orientation taken from the head: its other axis holds the head's {head.cols} fields")
    elif (chain.orientation == "line" and head.rows > 1 and head.cols == 1 and table.cols == head.rows
          and table.rows > 1):
        # The head is a lone column of labels its page could not orient; the
        # part's fields match it one for one, so it is the part's label row.
        head_table = turned(head, 1)
        head = head_table
        record["orientation"] = "the head's line of labels turned to the continuation's fields"
        evidence.append(f"the head is a single line of {table.cols} labels, the continuation's field count")

    grid = table.grid()
    head_grid = head.grid()
    label_rows = chain.label_rows if head_table is None else 1
    skip = 0
    if (table.cols == head.cols and table.rows >= 1 and keys(grid[0]) == keys(head_grid[0])
            and sum(1 for cell in grid[0] if pp.CJK(cell)) >= 2
            and all(cell.rowspan == 1 for cell in table.cells if cell.row == 0)):
        skip = 1
        record["header"] = "repeated, dropped"
        record["droppedHeader"] = grid[0]
        evidence.append("its first row repeats the head's first row (cells compared as sorted characters)")
        if head_series(head_items, chain.item) >= 2:
            return Decision("separate", pages, evidence + [
                f"p.{chain.page} prints a series of titled tables with this header: the next one is a table of its own"])
    else:
        record["header"] = "none"
        body = grid[1:] + (head_grid[label_rows:] if table.cols == head.cols else [])
        labelled = is_label_row(grid[0], body)
        # Header cells (<th>) are markup, and a continuation's first record is
        # where a formatter puts them.  They say a row is labels only where its
        # cells do not say otherwise: a row of figures in <th> is a record
        # the markup calls a header, and the pair is flagged, not taken apart.
        marked = table.kind == "html" and table.header_rows() >= 1
        counted = any(cell_class(cell) in ("figure", "numeral") for cell in grid[0])
        if table.rows > 1 and (labelled or marked):
            shown = "｜".join(c[:6] for c in grid[0][:4])
            if not labelled and counted:
                return Decision("undecided", pages, evidence + [
                    f"its first row is written as header cells but holds figures ({shown}…): the markup "
                    "calls a record a label row"])
            if label_rows:
                return Decision("separate", pages, evidence + [
                    f"its first row is a label row of its own ({shown}…), not the head's"])
            # Labels on the part and none on the head: the two pages disagree
            # about what the rows are.  Measured on a garbled grid of a list
            # printed over three pages, where the head's first row was no
            # label row and the part's looked like one.
            return Decision("undecided", pages, evidence + [
                f"its first row reads as a label row ({shown}…) and the head starts with none"])
    if table.rows - skip < 1:
        return Decision("undecided", pages, evidence + ["the continuation holds nothing but the head's label row"])

    joined_head = None
    targets = tail_record(head, table, skip, label_rows) if table.cols == head.cols else None
    if targets:
        joined_head, joins, why = join_cells(head, [(f, grid[skip][f]) for f in targets], witness)
        if joined_head is None:
            return Decision("undecided", pages, evidence + [
                f"its first record leaves a key field blank, like the tail of a cell the page cut, but {why}"])
        record["cellJoins"] = joins
        evidence.append("its first record leaves a key field blank and fills only fields the head's last "
                        f"record fills: the tail of a cell the page cut ({why}), joined")
        skip += 1
        if table.rows - skip < 1:
            record.update(kind="cell", fields={"own": table.cols, "head": head.cols, "by": "same count"},
                          records=[])
            return Decision("merge", pages, evidence, table, skip, record, head_table, joined_head)

    fields = {"own": table.cols, "head": head.cols}
    if table.cols == head.cols:
        fields["by"] = "same count"
        evidence.append(f"{table.cols} fields, the head's count")
        record.update(fields=fields, records=record_labels(table, skip))
        return Decision("merge", pages, evidence, table, skip, record, head_table, joined_head)
    if realign is not None and head_table is None and skip == 0:
        aligned = realign(chain, page, entry, table)
        if aligned is not None:
            verdict, part, how, more = aligned
            evidence += more
            record.update(fields=dict(fields, **how),
                          records=record_labels(part) if part is not None else record_labels(table))
            return Decision(verdict, pages, evidence, part, skip, record, head_table)
    return Decision("undecided", pages, evidence + [
        f"{table.cols} fields against the head's {head.cols}, and nothing on the page says which field is which"])


def lost_table_block(page: PageInput, which: str) -> int | None:
    """The engine A table block that is the page's first (or last) content in
    engine A's order, when the page does not carry it as a table: its
    recovered table is missing, or its grid was refused and nothing was built
    in its place."""
    record = page.record
    lost = set(record.get("tablesMissing") or []) | {
        r.get("block") for r in record.get("tablesRejected") or []}
    order = [k for k, block in enumerate(page.blocks)
             if block.get("label") == "table" or (block.get("label") not in FURNITURE_BLOCK_LABELS
                                                  and not page.is_furniture(str(block.get("text") or "")))]
    if not order:
        return None
    k = order[0] if which == "first" else order[-1]
    return k if page.blocks[k].get("label") == "table" and k in lost else None


# --- a cell the page break cut --------------------------------------------------

def covering(table: _tables.Table, row: int, col: int) -> _tables.Cell | None:
    """The cell that covers a slot: its own, or a merged one from above or the left."""
    return next((cell for cell in table.cells if cell.row <= row < cell.row + cell.rowspan
                 and cell.col <= col < cell.col + cell.colspan), None)


def key_fields(table: _tables.Table, first: int) -> list[int]:
    """The fields every record from row `first` on fills in a cell of its own:
    what names a record.  A merged cell counts in its first row only: counted
    in every row it covers, a notes column three records share looked like a
    key."""
    out = []
    for col in range(table.cols):
        rows = range(first, table.rows)
        if rows and all(any(cell.row == row and cell.col <= col < cell.col + cell.colspan
                            and cell_class(cell.text) != "blank" for cell in table.cells) for row in rows):
            out.append(col)
    return out


def tail_record(head: _tables.Table, part: _tables.Table, skip: int, label_rows: int) -> list[int] | None:
    """The fields of the part's first record (after `skip`) when that record
    may be the tail of a cell the page cut: it leaves blank a field every
    record of the head fills (what names a record), and fills only fields the
    head's last record fills.  None otherwise.  Whether the page really cut
    those cells is engine B's to show (cut_by_page).  A field every record
    fills can be the cut one itself - a notes column no record leaves empty -
    so it is only the fields the record leaves blank that must name.

    A field holding only a mark is filled (cell_class): the fields returned
    are every cell of the record that holds anything, so a join moves the
    whole record and drops nothing.  A ditto mark in a key field is then one
    more piece whose cell engine B must show cut, which a name cell is not:
    such a record is left undecided, not joined without its mark."""
    names = key_fields(head, label_rows)
    if head.rows <= label_rows or part.rows <= skip:
        return None
    filled = [col for col, text in enumerate(part.grid()[skip]) if cell_class(text) != "blank"]
    if not filled or not [col for col in names if col not in filled]:
        return None
    last = head.rows - 1
    for col in filled:
        cell = covering(head, last, col)
        if cell is None or cell_class(cell.text) == "blank":
            return None
    return filled


def cut_by_page(cell_text: str, witness: str | None) -> tuple[bool, int | None, str]:
    """Whether the page cut the cell rather than the cell ending on it: its last
    printed line runs as long as its full lines.  Read off engine B's line
    breaks, which follow the print; returns (cut, the full lines' length,
    why).

    Measured on a textbook table printed over three pages: the two cells the
    page breaks cut end in lines of 20 characters, the length of their full
    lines, while the cells that end on their page end in lines of 5, 11 and
    15.  A cell engine B read as one line, or whose end it read joined to
    other text, shows no length to compare: not taken as cut.
    """
    if not witness:
        return False, None, "there is no engine B reading of that page to show where the cell's last line ends"
    cell = pp.variant_fold(pp.CJK(cell_text))
    lines = [line for line in (pp.variant_fold(pp.CJK(text)) for text in witness.splitlines()) if line]
    tail = cell[-pp.WITNESS_ANCHOR:]
    if not tail or not lines:
        return False, None, "engine B read nothing to compare"

    def ending(line: str) -> float:
        """How well the line's end reads as the cell's end (a misread in four
        characters allowed); 0 when it does not."""
        n = min(len(line), len(tail))
        matcher = difflib.SequenceMatcher(None, tail[-n:], line[-n:], autojunk=False)
        matched = sum(block.size for block in matcher.get_matching_blocks())
        return matched / n + n / 100 if matched >= n - n // 4 else 0.0

    scores = [ending(line) for line in lines]
    best = max(range(len(lines)), key=lambda i: scores[i])
    if not scores[best] or scores.count(scores[best]) > 1:
        return False, None, "no one line of engine B's reading ends as the cell ends"
    own = [best]
    while own[0] > 0 and pp._covers(cell, lines[own[0] - 1]):
        own.insert(0, own[0] - 1)
    if len(own) < 2:
        return False, None, "engine B read the cell's end as a line of its own, so its full length is unknown"
    full = sorted(len(lines[i]) for i in own[:-1])[(len(own) - 1) // 2]
    last = len(lines[best])
    if last >= full:
        return True, full, f"engine B's last line of the cell is {last} characters, as long as its full lines"
    return False, full, (f"engine B's last line of the cell is {last} characters against {full} for its "
                         "full lines: the cell ends on its page")


def join_cells(head: _tables.Table, pieces: Sequence[tuple[int, str]],
               witness: str | None) -> tuple[_tables.Table | None, list[dict[str, Any]], str]:
    """The head with each piece's text appended to its last record's cell in
    that field - no separator, marks as printed, nothing dropped at the join -
    when engine B shows each of those cells cut by the page."""
    last = head.rows - 1
    label = record_labels(head)[last] if head.rows else ""
    joins, reasons = [], []
    for col, text in pieces:
        cell = covering(head, last, col)
        cut, _, why = cut_by_page(cell.text, witness)
        if not cut:
            return None, [], why
        reasons.append(why)
        before, after = pp.CJK(cell.text), pp.CJK(text)
        joined = cell.text.rstrip() + text.strip()
        joins.append({"record": label, "field": col, "join": f"…{before[-6:]} | {after[:6]}…",
                      "repeated": bool(before and after and before[-1] == after[0])})
        head = replace(head, cells=[replace(c, text=joined) if c is cell else c for c in head.cells])
    return head, joins, "; ".join(reasons)


def tail_block(chain: Chain, page: PageInput, first: Item,
               witness: str | None) -> tuple[str, list[str], dict[str, Any]] | None:
    """The first text of the page as the tail of the chain's last cell, cut by
    the page and printed on in that cell's field on the next page.

    Vertical text only.  Taken when engine A read the text as a block of its
    own that starts below where the page's other text starts (by a glyph or
    more: a paragraph starts at the top of the frame) and whose first line is
    as long as the cut cell's full lines (the field's height on the page
    before), and engine B shows the cell cut by the page.  Measured on the
    tail of a notes cell: its block starts a third of the page below the text
    frame's top, its first line is 20 characters, as are the cell's full lines.
    The head's own field start is not measured: engine A gives a table's cells
    no boxes, and on that grey scan the ruled grid finds no rules.  With the
    cell not shown cut, the pair is undecided; otherwise nothing is decided.
    """
    line = pp.CJK(first.text)
    blocks = [(k, block) for k, block in enumerate(page.blocks)
              if block.get("label") not in FURNITURE_BLOCK_LABELS and block.get("label") != "table"
              and pp.CJK(str(block.get("text") or "")) and not page.is_furniture(str(block.get("text") or ""))]
    matched = [(k, block) for k, block in blocks if line and pp._covers(pp.CJK(str(block["text"])), line)
               and pp._covers(line, pp.CJK(str(block["text"])))]
    if len(matched) != 1 or len(blocks) < 2:
        return None
    k, block = matched[0]
    x, y, w, h = block["box"]
    printed = [pp.CJK(text) for text in str(block["text"]).split("\n") if pp.CJK(text)]
    if h < w or not printed:
        return None
    glyph = h / max(len(text) for text in printed)
    top = min(other["box"][1] for j, other in blocks if j != k)
    if y - top < glyph:
        return None
    head = chain.table
    last = head.rows - 1
    col = max((c for c in range(head.cols) if cell_class((covering(head, last, c) or _tables.Cell(0, 0, 1, 1, "")).text)
               != "blank"), default=None)
    if col is None:
        return None
    cell = covering(head, last, col)
    cut, full, why = cut_by_page(cell.text, witness)
    evidence = [f"p.{chain.last}: the table ends the page; p.{page.page} starts with text engine A read as "
                f"block {k}, which starts {y - top:.3f} of the page below the page's other text "
                f"(a glyph is {glyph:.3f})"]
    if full is None or len(printed[0]) != full:
        return None
    evidence.append(f"its first line is {len(printed[0])} characters, the full lines of the last cell on "
                    f"p.{chain.last}")
    if not cut:
        return "undecided", evidence + [why], {"block": k}
    joined, joins, why = join_cells(head, [(col, first.text)], witness)
    return "merge", evidence + [f"{why}: joined to that cell"], {"block": k, "table": joined, "joins": joins}


# --- the ruled grid: a continuation's fields put back in the head's rows -----

# A continuation page prints no field labels, and engine A drops or adds a
# blank printed row there, so its field count drifts from the head's.  Measured
# on a year table printed over six pages: every page prints the same 19 ruled
# rows and engine A's grids have 19, 17, 22, 19, 19 and 19; on the 17- and
# 22-row pages 1 of 9 and 0 of 9 figures per year sit in the right field.
# Arithmetic cannot see it (a blank row adds nothing to a sum), and engine B is
# no better witness (one of its grids had the right row count and half its
# figures one field off).  The rules printed on the page can: each ruled cell
# of a record either holds ink or not.
#
# Sizes in pixels per 1,000 pixels of the render's width (rules, strokes and a
# scan's skew scale with its resolution), from the measures that placed every
# record of that table in its right fields.  They decide how often the
# geometry answers; a wrong placement needs two independent counts to agree on
# it (Realigner), which is what keeps one out.
SKEW = 1.6            # a vertical rule may wander this far across the block
BAND = 32.0           # the narrowest band between two vertical rules (not a double rule)
MARGIN = 3.2          # a band, or a cell's height, read this far inside its rules
CELL_MARGIN = 5.4     # a cell's width read this far inside its rules
RULE_HEIGHT = 1.1     # a horizontal rule may wander this far across a band
HALO = 5.4            # rows this far from a rule are text or paper, lighter than the rule
RULE_GAP = 2.2        # rows of one rule
RULE_SPACING = 11.0   # two rules of a band are at least this far apart
TEXT_LINE = 8.0       # the shortest run of inked rows that is a line of text
TEXT_MARGIN = 6.5     # text lines read this far inside the band's rules
VERTICAL_RULE = 0.85  # a column inked over this share of a strip of the block is a rule
RULE_STRIPS = 8       # the block's height is searched for vertical rules in this many strips
RULE_STRIPS_MISSED = 2  # a vertical rule may go unseen in this many of them (the margin, a break, a stamp)
HORIZONTAL_RULE = 0.5  # a row inked over this share of a band, twice its neighbours, is a rule
CELL_INK = 0.005      # a cell with this share of ink is written in
LINE_INK = 0.01       # a row with this share of ink belongs to a line of text


def _profile(image: Any, axis: int) -> list[float]:
    """Mean ink per column (axis 0) or per row (axis 1), 0 to 1."""
    from PIL import Image
    width, height = image.size
    small = image.resize((width, 1) if axis == 0 else (1, height), Image.BOX)
    return [value / 255 for value in small.tobytes()]


def _dilate(image: Any, dx: int, dy: int) -> Any:
    """Ink spread by dx columns and dy rows each way, nothing wrapping round."""
    from PIL import Image
    out = image.copy()
    for shift in range(1, dx + 1):
        for x in (shift, -shift):
            out.paste(image, (x, 0), image)
    base = out.copy()
    for shift in range(1, dy + 1):
        for y in (shift, -shift):
            out.paste(base, (0, y), base)
    return out


def otsu(histogram: Sequence[int]) -> int:
    """The grey level that best splits the block into ink and paper (Otsu).

    From the block's own levels: a fixed distance below the paper, as
    measure_columns uses for glyphs, leaves out the rules of a grey scan,
    whose paper sits at about 200 and whose thin rules only a little below."""
    total = sum(histogram)
    weighted = sum(level * count for level, count in enumerate(histogram))
    below = below_sum = 0
    best, cut = -1.0, 127
    for level, count in enumerate(histogram):
        below += count
        below_sum += level * count
        above = total - below
        if not below or not above:
            continue
        spread = below * above * (below_sum / below - (weighted - below_sum) / above) ** 2
        if spread > best:
            best, cut = spread, level
    return cut


def _runs(values: Sequence[float], need: float, gap: int) -> list[int]:
    """Centres of stretches at or above `need`, stretches `gap` apart joined."""
    out: list[list[int]] = []
    for index, value in enumerate(values):
        if value >= need:
            if out and index - out[-1][1] <= gap:
                out[-1][1] = index
            else:
                out.append([index, index])
    return [(a + b) // 2 for a, b in out]


class RuledGrid:
    """The ruled grid of one table block: its bands between vertical rules
    (a record each, when records are printed columns), each band's horizontal
    rules, which cells hold ink, where its lines of text sit."""

    def __init__(self, render: Any, box: Sequence[float], pad: float = 0.012):
        from PIL import Image
        with Image.open(render) as opened:
            page = opened.convert("L")
        width, height = page.size
        self.unit = width / 1000.0
        x, y, w, h = box
        region = page.crop((max(0, int((x - pad) * width)), max(0, int((y - pad) * height)),
                            min(width, int((x + w + pad) * width)), min(height, int((y + h + pad) * height))))
        cut = otsu(region.histogram())
        self.ink = region.point(lambda level: 255 if level <= cut else 0)
        self.bands: list[tuple[int, int, list[int]]] = []
        if region.size[0] < 8 or region.size[1] < 8:
            return
        u = self.unit
        rules = self.vertical_rules()
        for (_, a), (b, _) in zip(rules, rules[1:]):
            if b - a < BAND * u:
                continue
            margin = max(1, round(MARGIN * u))
            band = self.ink.crop((a + margin, 0, b - margin, self.ink.size[1]))
            rows = _profile(_dilate(band, 0, max(1, round(RULE_HEIGHT * u))), 1)
            halo, n = max(2, round(HALO * u)), len(rows)
            thin = [1.0 if rows[y] >= HORIZONTAL_RULE
                    and max(rows[max(0, y - halo)], rows[min(n - 1, y + halo)]) < 0.5 * rows[y] else 0.0
                    for y in range(n)]
            found = _runs(thin, 1.0, max(1, round(RULE_GAP * u)))
            spaced: list[int] = []
            for rule in found:
                if not spaced or rule - spaced[-1] >= RULE_SPACING * u:
                    spaced.append(rule)
            self.bands.append((a, b, spaced))

    def vertical_rules(self) -> list[tuple[int, int]]:
        """The block's vertical rules, left to right, as the columns each
        spans (a scan's skew moves a rule across columns down the page).

        Found strip by strip down the block and linked where a strip's rule
        lies next to the one above it; a rule is one found in all strips but
        RULE_STRIPS_MISSED.  Measured on a grey scan tilted by about a quarter
        of a degree: every rule drifts 13 pixels from top to bottom, so no
        single column is inked down the block's height and no rule was found
        before; per strip each is."""
        u = self.unit
        width, height = self.ink.size
        slack = max(1, round(SKEW * u))
        found: list[list[tuple[int, int]]] = []       # per rule: (strip, column)
        for strip in range(RULE_STRIPS):
            piece = self.ink.crop((0, strip * height // RULE_STRIPS, width, (strip + 1) * height // RULE_STRIPS))
            columns = _profile(_dilate(piece, slack, 0), 0)
            for x in _runs(columns, VERTICAL_RULE, max(1, round(2 * RULE_GAP * u))):
                near = [rule for rule in found if abs(rule[-1][1] - x) <= 2 * slack * (strip - rule[-1][0])]
                if near:
                    min(near, key=lambda rule: abs(rule[-1][1] - x)).append((strip, x))
                else:
                    found.append([(strip, x)])
        kept = [rule for rule in found if len({s for s, _ in rule}) >= RULE_STRIPS - RULE_STRIPS_MISSED]
        return sorted((min(x for _, x in rule), max(x for _, x in rule)) for rule in kept)

    def inked(self, band: tuple[int, int, list[int]]) -> list[bool]:
        """Per ruled cell of the band, top to bottom: written in or blank."""
        a, b, rules = band
        u = self.unit
        out = []
        for top, bottom in zip(rules, rules[1:]):
            cell = self.ink.crop((a + round(CELL_MARGIN * u), top + round(MARGIN * u),
                                  b - round(CELL_MARGIN * u), bottom - round(MARGIN * u)))
            out.append(cell.size[0] > 0 and cell.size[1] > 0
                       and sum(_profile(cell, 1)) / cell.size[1] > CELL_INK)
        return out

    def text_lines(self, band: tuple[int, int, list[int]], top: int, bottom: int) -> list[tuple[int, int]]:
        """Runs of inked rows between top and bottom, the band's rules masked."""
        a, b, _ = band
        u = self.unit
        margin = round(TEXT_MARGIN * u)
        region = self.ink.crop((a + margin, top, b - margin, bottom))
        if region.size[0] < 1 or region.size[1] < 1:
            return []
        rules = [value > 0.9 * HORIZONTAL_RULE for value in
                 _profile(_dilate(region, 0, max(1, round(RULE_HEIGHT * u))), 1)]
        rows = _profile(region, 1)
        halo = max(1, round(SKEW * u))
        for y, rule in enumerate(rules):
            if rule:
                for z in range(max(0, y - halo), min(len(rows), y + halo + 1)):
                    rows[z] = 0.0
        out, start = [], None
        for y, value in enumerate(rows + [0.0]):
            if value > LINE_INK and start is None:
                start = y
            elif value <= LINE_INK and start is not None:
                if y - start >= TEXT_LINE * u:
                    out.append((top + start, top + y))
                start = None
        return out


class Realigner:
    """Puts a continuation's records into the head's fields by the ruled grid.

    Only where records are printed columns on both pages (the tables were
    turned from the engine's grid), the sealed continuation is the engine's
    grid turned, and the page's bands pair one to one with the grid's
    columns.  Per record (band), two measures, each against the number of
    cells engine A filled in that record:

    - the band's own ruled rows, when there are as many as the head has
      fields: which ruled cells hold ink;
    - the head's ruled rows, measured on the head's page and stretched to the
      band's top and bottom rules: which of them each line of text falls in.

    A measure counts only when its count of written fields equals the
    engine's; when both count they must agree.  A record placed this way maps
    engine rows to fields, and an engine row is one printed row across every
    record of the page, so the page's other records go through that map
    (between two mapped rows only where the gap holds as many engine rows as
    fields).  A cell nothing maps leaves the pages unaligned: the tables stay
    apart, and no row is written shifted.  So does a map that does not keep
    the engine's order (two records whose placements cross).  Only empty
    fields are ever inserted or dropped; the figures keep their order.

    Measured on the six-page year table, against its merged golden: the
    band's rules alone place 9 of the 14 continuation records and the
    stretched head rules alone 8; with the page map all 14, every figure in
    its field, none placed wrong.
    """

    def __init__(self, pages: Mapping[int, PageInput]):
        self.pages = pages
        self.grids: dict[tuple[int, int], RuledGrid | None] = {}

    def grid(self, page: PageInput, block: Any) -> RuledGrid | None:
        key = (page.page, block)
        if key not in self.grids:
            grid = None
            if isinstance(block, int) and 0 <= block < len(page.blocks) and page.render is not None:
                try:
                    grid = RuledGrid(page.render, page.blocks[block]["box"])
                except Exception:  # noqa: BLE001 - an unreadable render is no evidence
                    grid = None
            self.grids[key] = grid
        return self.grids[key]

    def __call__(self, chain: Chain, page: PageInput, entry: Mapping[str, Any] | None,
                 table: _tables.Table) -> tuple[str, _tables.Table | None, dict[str, Any], list[str]] | None:
        head_page = self.pages.get(chain.page)
        fields = chain.table.cols
        if (head_page is None or chain.orientation != "rotated" or (entry or {}).get("orientation") != "rotated"
                or chain.entry is None or table.has_spans()):
            return None
        block = entry.get("block")
        engine = None
        if isinstance(block, int) and 0 <= block < len(page.blocks):
            engine = _tables.parse_html_table(str(page.blocks[block].get("text") or ""), source_breaks=True)
        if engine is None or engine.problems:
            return None
        engine = _tables.simplify(engine)
        records, width = table.rows, table.cols
        if (engine.rows, engine.cols) != (width, records):
            return None
        head_grid = self.grid(head_page, chain.entry.get("block"))
        grid = self.grid(page, block)
        if head_grid is None or grid is None:
            return None
        head_rules = next((rules for _, _, rules in head_grid.bands if len(rules) - 1 == fields), None)
        how: dict[str, Any] = {"by": "ruled grid"}
        evidence = [f"{width} fields against the head's {fields}"
                    + (f"; the head's {fields} ruled rows measured on p.{chain.page}" if head_rules
                       else f"; p.{chain.page} shows no band of {fields} ruled rows")]
        if len(grid.bands) != records:
            evidence.append(f"{len(grid.bands)} ruled band(s) on p.{page.page} for {records} record(s): "
                            "the bands cannot be paired with the records")
            return "undecided", None, how, evidence
        rows = table.grid()
        placements: dict[int, tuple[dict[int, int], str]] = {}
        refused: list[str] = []
        for k, band in enumerate(grid.bands):
            record = records - 1 - k          # bands left to right; printed right to left
            written = [j for j in range(width) if cell_class(rows[record][j]) != "blank"]
            found = []
            if len(band[2]) - 1 == fields:
                inked = grid.inked(band)
                occupied = [f for f in range(fields) if inked[f]]
                if len(occupied) == len(written):
                    found.append(("its ruled rows", occupied))
            if head_rules and len(band[2]) >= 2:
                top, bottom = band[2][0], band[2][-1]
                scale = (bottom - top) / max(1, head_rules[-1] - head_rules[0])
                fit = [top + (y - head_rules[0]) * scale for y in head_rules]
                occupied = sorted({next((f for f in range(fields) if fit[f] <= (s + e) / 2 < fit[f + 1]), -1)
                                   for s, e in grid.text_lines(band, top, bottom)} - {-1})
                if len(occupied) == len(written):
                    found.append(("the head's rows", occupied))
            if found and all(f[1] == found[0][1] for f in found):
                placements[record] = (dict(zip(written, found[0][1])), " and ".join(f[0] for f in found))
            elif found:
                refused.append(f"{record_labels(table)[record]}: the two measures disagree")
        rowmap: dict[int, int] = {}
        for record, (mapping, _) in placements.items():
            for j, f in mapping.items():
                if rowmap.get(j, f) != f:
                    evidence.append("records placed on the same page disagree about an engine row")
                    return "unaligned", None, how, evidence + [
                        f"{', '.join(record_labels(table))} (conflicting placements)"]
                rowmap[j] = f
        if len(set(rowmap.values())) != len(rowmap):
            return "unaligned", None, how, evidence + [
                f"{', '.join(record_labels(table))} (two engine rows placed in one field)"]
        # An engine row further down is a printed row further down: the map
        # must keep the engine's order.  Each record's own placement does
        # (its written cells and its inked fields are both taken top to
        # bottom), but two records can cross - ink in a band that is no
        # figure (a smear, a stamp) is read by both measures alike, so they
        # agree on it - and a record placed through a crossed map would have
        # its figures written in swapped fields.
        keys = sorted(rowmap)
        crossed = [(j, k) for j, k in zip(keys, keys[1:]) if rowmap[j] > rowmap[k]]
        if crossed:
            j, k = crossed[0]
            names = [record_labels(table)[record] for record, (mapping, _) in sorted(placements.items())
                     if j in mapping or k in mapping]
            return "unaligned", None, how, evidence + [
                f"{', '.join(names)} (their placements cross: engine row {j + 1} in field {rowmap[j] + 1}, "
                f"engine row {k + 1} in field {rowmap[k] + 1})"]

        def field(j: int) -> int | None:
            if j in rowmap:
                return rowmap[j]
            below = max((k for k in keys if k < j), default=None)
            above = min((k for k in keys if k > j), default=None)
            if below is None and above is None:
                return None
            if below is None:
                return j if above == rowmap[above] else None
            if above is None:
                return rowmap[below] + (j - below) if width - 1 - below == fields - 1 - rowmap[below] else None
            return rowmap[below] + (j - below) if above - below == rowmap[above] - rowmap[below] else None

        cells: list[_tables.Cell] = []
        unplaced: list[str] = []
        labels = record_labels(table)
        placed_by: dict[str, str] = {}
        for record in range(records):
            written = [j for j in range(width) if cell_class(rows[record][j]) != "blank"]
            targets = [field(j) for j in written]
            if any(t is None for t in targets) or len(set(targets)) != len(targets):
                unplaced.append(labels[record])
                continue
            placed_by[labels[record]] = placements.get(record, (None, "the page's row map"))[1]
            texts = {t: rows[record][j] for j, t in zip(written, targets)}
            cells += [_tables.Cell(record, f, 1, 1, texts.get(f, "")) for f in range(fields)]
        how["placed"] = placed_by
        if unplaced:
            return "unaligned", None, how, evidence + [", ".join(unplaced) + (
                "; " + "; ".join(refused) if refused else "")]
        used = {field(j) for j in range(width) if field(j) is not None}
        how["inserted"] = [f for f in range(fields) if f not in used]
        how["dropped"] = [j for j in range(width) if field(j) is None]
        evidence.append("placed " + "; ".join(f"{label} by {by}" for label, by in placed_by.items())
                        + f"; {len(how['inserted'])} blank field(s) inserted, {len(how['dropped'])} blank "
                        "engine row(s) dropped")
        part = _tables.Table("markdown", records, fields, sorted(cells, key=lambda c: (c.row, c.col)))
        return "merge", part, how, evidence


# --- the plan ------------------------------------------------------------------

def crop_name(page: int) -> str:
    """The pair crop of the page break before `page`, saved beside its seal."""
    return f"page-{page:04d}-stitch-01.png"


class Planner:
    """Walks the sealed pages in scan order and collects what the stitch does."""

    def __init__(self, pages: Sequence[PageInput], geometry: bool = True):
        self.pages = sorted(pages, key=lambda p: p.page)
        self.by_page = {p.page: p for p in self.pages}
        self.realign = Realigner(self.by_page) if geometry else None
        self.items = {p.page: page_items(p.text, p.is_furniture) for p in self.pages}
        self.decisions: list[dict[str, Any]] = []
        self.finished: list[Chain] = []
        self.edits: dict[int, list[tuple[int, int, str]]] = {}
        self.notes: dict[int, dict[str, Any]] = {}
        self.doubts: dict[int, list[dict[str, str]]] = {}

    def record(self, decision: Decision, blocks: Sequence[int | None],
               kind: str | None = None, detail: str = "") -> dict[str, Any]:
        """A decision at a page break: in the run's list, and - when it leaves a
        doubt - in both pages' seals, with the engine A blocks the pair crop is
        cut from."""
        entry = {"pages": list(decision.pages), "verdict": decision.verdict,
                 "evidence": decision.evidence, "blocks": list(blocks),
                 "crop": crop_name(decision.pages[1])}
        self.decisions.append(entry)
        if kind:
            for page in decision.pages:
                self.notes.setdefault(page, {}).setdefault("decisions", []).append(entry)
                self.doubts.setdefault(page, []).append({"kind": kind, "detail": detail})
        return entry

    def run(self) -> dict[str, Any]:
        chain: Chain | None = None
        previous: PageInput | None = None
        for page in self.pages:
            own = content(self.items[page.page])
            first = own[0] if own else None
            last = own[-1] if own else None
            consumed = False
            if chain is not None and chain.last == page.page - 1 and first is not None:
                consumed, stays = self.continue_chain(chain, page, first, last)
                if stays:
                    previous = page
                    continue
            elif (previous is not None and previous.page == page.page - 1
                  and first is not None and first.kind == "table"):
                self.check_lost_head(previous, page, first)
            if chain is not None and chain.parts:
                self.finished.append(chain)
            chain = None
            if last is not None and last.kind == "table" and not (consumed and last is first):
                chain = start_chain(page, last)
            previous = page
        if chain is not None and chain.parts:
            self.finished.append(chain)
        return self.result()

    def continue_chain(self, chain: Chain, page: PageInput, first: Item, last: Item) -> tuple[bool, bool]:
        """The page after the chain's last: (its first content consumed, the chain
        stays open because nothing else is on the page)."""
        witness = self.by_page[chain.last].witness
        if first.kind != "table":
            block = lost_table_block(page, "first")
            if block is not None:
                decision = Decision("blocked", (chain.last, page.page), [
                    f"p.{chain.last}: the table ends the page; p.{page.page} starts with text where "
                    f"engine A read a table (block {block}) that the page does not carry as a table"])
                self.record(decision, [chain.last_block, block], BLOCKED, (
                    f"TABLE CONTINUATION BLOCKED: the table ending p.{chain.last} may continue on "
                    f"p.{page.page}, whose table (engine A block {block}) was not written as a table "
                    "there; nothing was merged"))
                return False, False
            found = tail_block(chain, page, first, witness)
            if found is None:
                return False, False
            verdict, evidence, info = found
            decision = Decision(verdict, (chain.last, page.page), evidence)
            if verdict != "merge":
                self.record(decision, [chain.last_block, info["block"]], UNDECIDED, (
                    f"TABLE CELL MAY CONTINUE ACROSS PAGES: p.{page.page} starts with text in the field of "
                    f"the last cell of the table ending p.{chain.last}, but {evidence[-1]}; left where it "
                    "is - check the pair on the image"))
                return False, False
            chain.table = info["table"]
            pair = self.record(decision, [chain.last_block, info["block"]])
            chain.parts.append({"page": page.page, "table": None, "kind": "cell", "header": "none",
                                "records": [], "cellJoins": info["joins"], "evidence": evidence,
                                "fields": {"own": None, "head": chain.table.cols,
                                           "by": "text in the cut cell's field"}, "crop": pair["crop"]})
            self.edits.setdefault(page.page, []).append((first.start, first.end, ""))
            self.notes.setdefault(page.page, {})["continuation"] = {
                "head": {"page": chain.page, "table": chain.item.index},
                "moved": first.text, "evidence": evidence, "crop": pair["crop"]}
            return True, False
        decision = decide(chain, self.items[chain.page], page, first, self.realign, witness)
        entry = seal_entry(page.record, first.text)
        blocks = [chain.last_block, (entry or {}).get("block")]
        if decision.verdict == "separate":
            self.record(decision, blocks)
            return False, False
        if decision.verdict == "unaligned":
            self.record(decision, blocks, UNALIGNED, (
                f"TABLE CONTINUATION UNALIGNED: the table starting p.{decision.pages[1]} continues the one "
                f"ending p.{decision.pages[0]} by position, with {decision.record['fields']['own']} fields "
                f"against {decision.record['fields']['head']}, and the ruled grid could not place "
                f"{decision.evidence[-1]}; left as two tables - no row is written shifted"))
            return False, False
        if decision.verdict != "merge":
            self.record(decision, blocks, UNDECIDED, (
                f"TABLE MAY CONTINUE ACROSS PAGES: the table ending p.{decision.pages[0]} and the "
                f"one starting p.{decision.pages[1]} sit at the page break, but "
                f"{decision.evidence[-1]}; left as two tables - check the pair on the image"))
            return False, False
        if decision.head_table is not None:
            chain.table, chain.label_rows = decision.head_table, 1
            chain.turned_from_continuation = decision.record.get("orientation")
        if decision.joined_head is not None:
            chain.table = decision.joined_head
        chain.table = appended(chain.table, decision.part, decision.skip)
        pair = self.record(decision, blocks)
        chain.parts.append(dict(decision.record, evidence=decision.evidence, crop=pair["crop"]))
        self.edits.setdefault(page.page, []).append((first.start, first.end, ""))
        self.notes.setdefault(page.page, {})["continuation"] = {
            "head": {"page": chain.page, "table": chain.item.index},
            "moved": first.text, "evidence": decision.evidence, "crop": pair["crop"]}
        if first is last:
            chain.last = page.page
            chain.last_block = blocks[1]
            return True, True
        return True, False

    def check_lost_head(self, previous: PageInput, page: PageInput, first: Item) -> None:
        """A page that starts with a table after a page whose table was lost."""
        prior = content(self.items[previous.page])
        if not prior or prior[-1].kind != "line":
            return
        block = lost_table_block(previous, "last")
        if block is None:
            return
        entry = seal_entry(page.record, first.text)
        decision = Decision("blocked", (previous.page, page.page), [
            f"p.{previous.page} ends with text where engine A read a table (block {block}) that the "
            f"page does not carry as a table; p.{page.page}: table {first.index + 1} starts the page"])
        self.record(decision, [block, (entry or {}).get("block")], BLOCKED, (
            f"TABLE CONTINUATION BLOCKED: the table starting p.{page.page} may continue one on "
            f"p.{previous.page} (engine A block {block}) that was not written as a table there; "
            "nothing was merged"))

    def result(self) -> dict[str, Any]:
        chains = []
        for chain in self.finished:
            # A caption is the table's printed title, which the head's page
            # wrote inside its HTML.  Markdown has no place for one, and the
            # merged table is Markdown when no cell is merged: written that
            # way, the title was gone from the page.  The head keeps its form.
            markup = (_tables.render_html(chain.table) if chain.table.caption.strip()
                      else _tables.render(chain.table))
            self.edits.setdefault(chain.page, []).append((chain.item.start, chain.item.end, markup))
            spanned = [chain.page] + [part["page"] for part in chain.parts]
            parts = chain.parts
            head = {"table": chain.item.index, "pages": spanned, "parts": parts}
            if chain.turned_from_continuation:
                head["orientation"] = chain.turned_from_continuation
            self.notes.setdefault(chain.page, {})["head"] = head
            records = sum(len(part.get("records") or []) for part in parts)
            joins = [(part["page"], join) for part in parts for join in part.get("cellJoins") or []]
            detail = "; ".join(
                f"p.{part['page']}: {len(part.get('records') or [])} record(s)"
                + (f", {len(part['cellJoins'])} cell(s) joined" if part.get("cellJoins") else "")
                + f", header {part.get('header')}, "
                f"{part['fields']['own'] or '-'} -> {part['fields']['head']} fields ({part['fields'].get('by')})"
                for part in parts)
            self.doubts.setdefault(chain.page, []).append({"kind": MERGED, "detail": (
                f"TABLE CONTINUED ACROSS PAGES, MERGED HERE: table {chain.item.index + 1} continues on "
                f"pp.{spanned[1]}-{spanned[-1]}; {records} record(s) appended ({detail}); "
                "check each join on the image")})
            for part in parts:
                self.doubts.setdefault(part["page"], []).append({"kind": MERGED, "detail": (
                    f"TABLE MOVED TO p.{chain.page}: this page's first "
                    f"{'text' if part.get('kind') == 'cell' else 'table'} continues table "
                    f"{chain.item.index + 1} of p.{chain.page} and was merged there "
                    f"({part['fields'].get('by')}); the rest of the page is as proofread")})
            for page, join in joins:
                # A cell's text joined across the break is read as one run: the
                # join is where a character could be doubled (a catchword the
                # print repeats, or a misread) or one missing.
                detail = (f"CELL CONTINUED ACROSS THE PAGE BREAK: the last cell of {join['record']} "
                          f"(field {join['field'] + 1}) in table {chain.item.index + 1} of p.{chain.page} "
                          f"runs on at the top of p.{page}, joined as {join['join']}"
                          + ("; the same character ends the one and starts the other - kept twice, "
                             "check whether the print repeats it" if join["repeated"] else "")
                          + " - check the join on the image")
                for where in dict.fromkeys((chain.page, page)):
                    self.doubts.setdefault(where, []).append({"kind": CELL_JOINED, "detail": detail})
            chains.append({"head": chain.page, "table": chain.item.index, "pages": spanned,
                           "records": records, "shape": f"{chain.table.rows}x{chain.table.cols}",
                           "cellsJoined": len(joins),
                           "realigned": [part["page"] for part in parts
                                         if part["fields"].get("by") == "ruled grid"],
                           "parts": [{k: v for k, v in part.items() if k not in ("evidence", "droppedHeader")}
                                     for part in parts]})
        out_pages: dict[int, dict[str, Any]] = {}
        for page in self.pages:
            text = apply_edits(page.text, self.edits.get(page.page, []))
            out_pages[page.page] = {"text": text, "stitch": self.notes.get(page.page),
                                    "doubts": self.doubts.get(page.page, [])}
        return {"pages": out_pages, "chains": chains, "decisions": self.decisions}


def plan(pages: Sequence[PageInput], geometry: bool = True) -> dict[str, Any]:
    """Decide every continuation over consecutive sealed pages, in scan order.

    Returns, per page, the text to write and what its seal records
    (`pages`: text, stitch notes, stitch doubts), the merged tables (`chains`)
    and every decision taken at a page break where position holds
    (`decisions`).  Only the next scan page can continue a table, and a
    missing page stops the chain.  A page can hold a continuation and the head
    of the next table: only its first content is consumed.  `geometry` lets
    the ruled grid realign a continuation with another field count (off: such
    a pair is left undecided).
    """
    return Planner(pages, geometry).run()

def apply_edits(text: str, edits: Sequence[tuple[int, int, str]]) -> str:
    """Replace spans of the page, right to left.  A span replaced by nothing is
    cut with the blank lines around it, so what stays keeps one blank line
    between its parts and a page whose only content was moved is empty."""
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        if replacement:
            text = text[:start] + replacement + text[end:]
        else:
            before, after = text[:start].rstrip(), text[end:].lstrip()
            text = before + ("\n\n" if before and after else "") + after
    return text
