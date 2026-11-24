"""Tables in a finished page: Markdown pipe tables and HTML tables.

A Markdown table cannot say that one cell spans several rows or columns, and
printed tables do that - a notes column shared by three rows of a textbook
list, a year heading over three month columns.  So a finished page may carry
an HTML <table> where cells are merged, and a Markdown table everywhere else.
Both are read here into one grid model, so a checker, the auditor and the
regression suite agree on what a table is.  Both are also written here, so the
pipeline emits one markup for each kind.

Standard library only: the regression runner and the auditor import this.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from html import escape
from html.parser import HTMLParser
import re

SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
HTML_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
# An opening tag with no close after it: the auditor reports it, because the
# regex above never sees it and the rows would otherwise pass as prose.
HTML_TABLE_OPEN_RE = re.compile(r"<table\b", re.IGNORECASE)
# A line that is part of an HTML table's markup.  Page-boundary logic treats it
# as structure, never as prose to be joined.
HTML_TABLE_LINE_RE = re.compile(
    r"^\s*</?(?:table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col)\b", re.IGNORECASE)
# The skill's one fence test (_ocr_markdown re-exports it to the auditor).
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
MAX_SPAN = 500


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    header: bool = False


@dataclass
class Table:
    kind: str                      # "markdown" or "html"
    rows: int
    cols: int
    cells: list[Cell] = field(default_factory=list)   # anchored at top-left, reading order
    start: int = 0                 # character offsets of the table in the page text
    end: int = 0
    # What the layout algorithm had to repair: a rowspan running off the table
    # or out of its row group, a colspan running into a cell from above, a row
    # too short for the grid, text outside any cell.  A browser renders all of
    # these somehow, so they are not parse failures - but a grid that needed
    # repairing is not one the markup actually describes.
    problems: list[str] = field(default_factory=list)
    # A <caption> is printed text - usually the table's title - that sits inside
    # the markup but in no cell; it is carried so that nothing rewriting the
    # grid can drop it.
    caption: str = ""

    def has_spans(self) -> bool:
        return any(c.rowspan > 1 or c.colspan > 1 for c in self.cells)

    def layout(self) -> set[tuple[int, int, int, int]]:
        return {(c.row, c.col, c.rowspan, c.colspan) for c in self.cells}

    def header_rows(self) -> int:
        rows = 0
        for r in range(self.rows):
            anchored = [c for c in self.cells if c.row == r]
            if anchored and all(c.header for c in anchored):
                rows += 1
            else:
                break
        return rows

    def grid(self, fill_spans: bool = False) -> list[list[str]]:
        """Rows x columns of text.  A merged cell's text sits in its top-left
        slot; with fill_spans it is repeated into every slot it covers."""
        out = [[""] * self.cols for _ in range(self.rows)]
        for c in self.cells:
            for r in range(c.row, c.row + c.rowspan):
                for k in range(c.col, c.col + c.colspan):
                    if fill_spans or (r, k) == (c.row, c.col):
                        out[r][k] = c.text
        return out


def _place(raw_rows: list[list[tuple[str, int, int, bool]]],
           groups: list[int] | None = None) -> tuple[int, int, list[Cell], list[str]]:
    """The HTML table layout algorithm: cells fill the first free slot.

    `groups` gives each row's row group (<thead>, <tbody>, <tfoot>, or the body
    a bare <tr> opens).  A rowspan ends with its group: a browser stops it
    there, so a header cell spanning down into the body looks merged in the
    markup and is drawn cut.
    """
    n = len(raw_rows)
    groups = groups if groups is not None and len(groups) == n else [0] * n
    taken: set[tuple[int, int]] = set()
    cells: list[Cell] = []
    problems: list[str] = []
    width = 0
    for r, row in enumerate(raw_rows):
        c = 0
        group_end = r
        while group_end + 1 < n and groups[group_end + 1] == groups[r]:
            group_end += 1
        for text, rowspan, colspan, header in row:
            while (r, c) in taken:
                c += 1
            if rowspan > n - r:
                problems.append(f"row {r + 1}: rowspan {rowspan} runs past the last row")
            elif rowspan > group_end + 1 - r:
                problems.append(f"row {r + 1}: rowspan {rowspan} runs out of its row group "
                                "(<thead>/<tbody>/<tfoot>), where a browser cuts it")
            rowspan = max(1, min(rowspan, group_end + 1 - r))
            # A colspan running into a slot a cell above already holds stops there.
            span = 0
            while span < colspan and (r, c + span) not in taken:
                span += 1
            span = max(1, span)
            if span < colspan:
                problems.append(f"row {r + 1}: colspan {colspan} runs into a cell from a row above")
            for dr in range(rowspan):
                for dc in range(span):
                    taken.add((r + dr, c + dc))
            cells.append(Cell(r, c, rowspan, span, text, header))
            c += span
            width = max(width, c)
    for r in range(n):
        width = max(width, max((c + 1 for rr, c in taken if rr == r), default=0))
    # Ragged rows: pad with empty cells so every row covers the full width.
    for r in range(n):
        covered = sum(1 for c in range(width) if (r, c) in taken)
        if covered < width:
            problems.append(f"row {r + 1} covers {covered} of {width} column(s)")
        for c in range(width):
            if (r, c) not in taken:
                taken.add((r, c))
                cells.append(Cell(r, c, 1, 1, "", False))
    cells.sort(key=lambda cell: (cell.row, cell.col))
    return n, width, cells, problems


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v　]*\n[ \t\r\f\v　]*", "\n", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


class _HtmlGrid(HTMLParser):
    def __init__(self, source_breaks: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int, bool]]] = []
        self.groups: list[int] = []
        self.caption: list[str] = []
        self.problems: list[str] = []
        self._source_breaks = source_breaks
        self._row: list[tuple[str, int, int, bool]] | None = None
        self._cell: list[str] | None = None
        self._spans = (1, 1)
        self._header = False
        self._in_thead = False
        self._in_caption = False
        self._depth = 0
        self._group = 0

    @staticmethod
    def _span(value: str | None) -> int:
        try:
            return max(1, min(MAX_SPAN, int(str(value).strip())))
        except (TypeError, ValueError):
            return 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth > 1:
                self.problems.append("a <table> nested inside a table")
        elif tag == "caption":
            self._in_caption = True
        elif tag in ("thead", "tbody", "tfoot"):
            self._close_cell()
            self._close_row()
            self._group += 1
            self._in_thead = tag == "thead"
        elif tag == "tr":
            self._close_cell()
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self.problems.append("a cell outside any <tr>")
                self._row = []
            named = dict(attrs)
            self._cell = []
            self._spans = (self._span(named.get("rowspan")), self._span(named.get("colspan")))
            self._header = tag == "th" or self._in_thead
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_cell()
            self._close_row()
        elif tag in ("thead", "tbody", "tfoot"):
            self._close_cell()
            self._close_row()
            # A bare <tr> after the group closes opens an implicit body.
            self._group += 1
            self._in_thead = False
        elif tag == "caption":
            self._in_caption = False
        elif tag == "table":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            # In written markup a source line break inside a cell is indentation,
            # not content; only <br> is a break.  An engine's markup is not laid
            # out by hand, so there a newline between two characters is the
            # engine's own line break and is kept (_clean drops the ones at the
            # ends, which are indentation either way).
            if self._source_breaks:
                self._cell.append(re.sub(r"\s+", lambda m: "\n" if "\n" in m.group(0) else " ", data))
            else:
                self._cell.append(re.sub(r"\s+", " ", data))
        elif self._in_caption:
            self.caption.append(data)
        elif data.strip():
            self.problems.append(f"text outside any cell: {data.strip()[:20]!r}")

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append((_clean("".join(self._cell)), *self._spans, self._header))
        self._cell = None

    def _close_row(self) -> None:
        if self._row is not None:
            self.rows.append(self._row)
            self.groups.append(self._group)
        self._row = None

    def close(self) -> None:
        super().close()
        self._close_cell()
        self._close_row()


def parse_html_table(markup: str, source_breaks: bool = False) -> Table | None:
    """One HTML table.  `source_breaks` keeps a newline between two characters
    of a cell as a line break (an engine's markup); otherwise it is layout."""
    grid = _HtmlGrid(source_breaks)
    try:
        grid.feed(markup)
        grid.close()
    except Exception:
        return None
    if not grid.rows:
        return None
    n, width, cells, problems = _place(grid.rows, grid.groups)
    return Table("html", n, width, cells, problems=grid.problems + problems,
                 caption=_clean("".join(grid.caption)))


def table_cells(value: str) -> list[str] | None:
    """Split a possible Markdown table row, respecting escaped pipes.

    The one table-row splitter for the whole skill (the auditor, page-boundary
    classification and the regression suite all reach it through here).  Outer
    pipes are optional; a pipe preceded by an odd number of backslashes is
    content.  Returns None when the line has no unescaped pipe at all.
    """
    value = value.strip()
    cells = [""]
    unescaped_pipes = 0
    backslashes = 0
    for character in value:
        if character == "|" and backslashes % 2 == 0:
            cells.append("")
            unescaped_pipes += 1
        else:
            cells[-1] += character
        if character == "\\":
            backslashes += 1
        else:
            backslashes = 0
    if unescaped_pipes == 0:
        return None
    if value.startswith("|"):
        cells = cells[1:]
    trailing_backslashes = len(value[:-1]) - len(value[:-1].rstrip("\\"))
    if value.endswith("|") and trailing_backslashes % 2 == 0:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def split_row(line: str) -> list[str]:
    """A Markdown row's cell texts: escaped pipes and backslashes undone, <br> a break."""
    cells = table_cells(line) or [line.strip()]
    return [_clean(re.sub(r"<br\s*/?>", "\n", re.sub(r"\\([\\|])", r"\1", c), flags=re.I))
            for c in cells]


def fenced_lines(text: str) -> list[bool]:
    """For each line of the text, whether it belongs to a code fence (the fence
    lines included).  Recognised as the auditor does; an unclosed fence runs to
    the end."""
    out: list[bool] = []
    fence: str | None = None
    for line in text.split("\n"):
        match = FENCE_RE.match(line)
        if fence is not None:
            stripped = line.strip()
            if match and stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
            out.append(True)
        elif match:
            fence = match.group(1)
            out.append(True)
        else:
            out.append(False)
    return out


def mask_fences(text: str) -> str:
    """The text with fenced code blanked to spaces, offsets and newlines kept.

    Markup inside a code fence renders as code, not as a table, so no reader of
    tables may count it - the auditor always masked fences and the regression
    suite and the pipeline did not, so a fenced table passed their checks while
    rendering as a block of tags.
    """
    lines = text.split("\n")
    return "\n".join(" " * len(line) if fenced else line
                     for line, fenced in zip(lines, fenced_lines(text)))


def html_table_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every closed <table>…</table> outside code fences."""
    return [(m.start(), m.end()) for m in HTML_TABLE_RE.finditer(mask_fences(text))]


def find_tables(text: str) -> list[Table]:
    """Every Markdown and HTML table in the text, in page order (not in fences)."""
    tables: list[Table] = []
    html_spans = []
    text = mask_fences(text)
    for match in HTML_TABLE_RE.finditer(text):
        table = parse_html_table(match.group(0))
        if table:
            table.start, table.end = match.start(), match.end()
            tables.append(table)
            html_spans.append((match.start(), match.end()))
    lines = text.split("\n")
    offset, i = 0, 0
    starts = []
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    while i < len(lines):
        inside_html = any(a <= starts[i] < b for a, b in html_spans)
        if (not inside_html and table_cells(lines[i]) is not None and i + 1 < len(lines)
                and SEPARATOR_RE.match(lines[i + 1]) and not SEPARATOR_RE.match(lines[i])):
            j = i + 2
            while j < len(lines) and lines[j].strip() and table_cells(lines[j]) is not None:
                j += 1
            raw = [[(t, 1, 1, position == 0) for t in split_row(lines[k])]
                   for position, k in enumerate([i, *range(i + 2, j)])]
            n, width, cells, problems = _place(raw)
            tables.append(Table("markdown", n, width, cells, starts[i],
                                starts[j - 1] + len(lines[j - 1]), problems))
            i = j
            continue
        i += 1
    tables.sort(key=lambda t: t.start)
    return tables


def with_texts(table: Table, texts: list[str]) -> Table:
    """The same layout with new cell texts, given in reading order."""
    cells = [replace(cell, text=text) for cell, text in zip(table.cells, texts)]
    return replace(table, cells=cells)


def simplify(table: Table) -> Table:
    """Drop grid lines that no cell uses.

    A column boundary that no cell starts at separates nothing: an engine that
    gives every row one cell with colspan="2" has drawn a one-column table, and
    calling it merged would put a span-free table into HTML.  The same holds for
    a row that every cell covering it entered from above.
    """
    col_starts = sorted({0} | {c.col for c in table.cells})
    row_starts = sorted({0} | {c.row for c in table.cells})
    if len(col_starts) == table.cols and len(row_starts) == table.rows:
        return table
    # A grid line's new index is the number of used lines before it.
    cells = [replace(c, row=bisect_left(row_starts, c.row), col=bisect_left(col_starts, c.col),
                     rowspan=bisect_left(row_starts, c.row + c.rowspan) - bisect_left(row_starts, c.row),
                     colspan=bisect_left(col_starts, c.col + c.colspan) - bisect_left(col_starts, c.col))
             for c in table.cells]
    return replace(table, rows=len(row_starts), cols=len(col_starts), cells=cells)


def rotate_counterclockwise(table: Table) -> Table:
    """The grid turned 90 degrees counter-clockwise: the last column becomes the
    first row.  A cell's rowspan and colspan trade places; header flags do not
    survive the turn, because the old header row is now a column."""
    cells = [Cell(table.cols - c.col - c.colspan, c.row, c.colspan, c.rowspan, c.text, False)
             for c in table.cells]
    cells.sort(key=lambda cell: (cell.row, cell.col))
    return replace(table, rows=table.cols, cols=table.rows, cells=cells, problems=[])


def transpose(table: Table) -> Table:
    """The grid mirrored on its diagonal: the first column becomes the first
    row.  Spans trade places and header flags go, as in
    rotate_counterclockwise."""
    cells = [Cell(c.col, c.row, c.colspan, c.rowspan, c.text, False) for c in table.cells]
    cells.sort(key=lambda cell: (cell.row, cell.col))
    return replace(table, rows=table.cols, cols=table.rows, cells=cells, problems=[])


# The name of a table compared as it is written (orientations).
AS_WRITTEN = "as written"


def band_rows(table: Table) -> tuple[int, int]:
    """How many rows at the top and at the bottom of the grid are a band: one
    cell across the whole width (a title over the table, a note under it).
    None in a one-column grid, whose every row is one cell."""
    def band(r: int) -> bool:
        cells = [c for c in table.cells if c.row <= r < c.row + c.rowspan]
        return (len(cells) == 1 and cells[0].row == r and cells[0].rowspan == 1
                and cells[0].colspan == table.cols)

    if table.cols < 2:
        return 0, 0
    top = 0
    while top < table.rows and band(top):
        top += 1
    bottom = 0
    while bottom < table.rows - top and band(table.rows - 1 - bottom):
        bottom += 1
    return top, bottom


def orientations(table: Table) -> list[tuple[str, Table]]:
    """The ways a table may be written that the user's ruling (2026-09-27)
    accepts as one table: as written, and transposed - a table printed in
    vertical columns is written a record per row, and scored either way, a
    record per row or a column per row - and, where the grid has a title row
    across its top or a note row across its foot (band_rows), the grid below
    the title and/or above the note transposed, the bands kept where they
    are across the transposed grid's width.  A price table written with a
    column per row, its title row and its note row across the top and the
    foot, is the same table as its golden written a record per row, the note
    a column beside the records: the grid under the title transposed.

    Nothing else.  Records in reverse order, each record's fields reversed,
    a grid upside down or turned a quarter are not in the ruling: a scorer
    that took them for the golden would pass a table the pipeline turned the
    wrong way, the fault the orientation and reading-direction steps are
    scored for.  They wait for the user's ruling.

    In order: as written, then the transposes that keep the most title and
    note rows where they are, the whole grid transposed last - a title stays
    a title, so where two score alike the scorer reads the table with its
    bands kept."""
    out = [(AS_WRITTEN, table)]
    top, bottom = band_rows(table)
    kept = sorted({(t, b) for t in (0, top) for b in (0, bottom)} - {(0, 0)}, key=lambda tb: (-sum(tb), -tb[0]))
    for t, b in kept:
        if table.rows - t - b < 1:
            continue
        head = [c for c in table.cells if c.row < t]
        foot = [c for c in table.cells if c.row >= table.rows - b]
        body = replace(table, rows=table.rows - t - b, problems=[],
                       cells=[replace(c, row=c.row - t) for c in table.cells if t <= c.row < table.rows - b])
        where = ("under its title row" if t and not b else "above its note row" if b and not t
                 else "between its title and note rows")
        turned = transpose(body)
        width = turned.cols
        cells = ([replace(c, colspan=width, header=False) for c in head]
                 + [replace(c, row=c.row + t) for c in turned.cells]
                 + [replace(c, row=c.row - (table.rows - b) + t + turned.rows, colspan=width, header=False)
                    for c in foot])
        cells.sort(key=lambda cell: (cell.row, cell.col))
        out.append((f"transposed {where}", replace(table, rows=t + turned.rows + b, cols=width,
                                                   cells=cells, problems=[])))
    return out + [("transposed", transpose(table))]


def render_markdown(table: Table) -> str:
    """A pipe table; the first row is the header row, as Markdown requires."""
    if table.has_spans():
        raise ValueError("a table with merged cells cannot be written as Markdown")
    rows = table.grid()

    def cell(text: str) -> str:
        # Backslashes first: a backslash in front of a pipe would otherwise
        # cancel the pipe's escape and split the cell (split_row undoes both).
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>").strip()

    lines = ["| " + " | ".join(cell(c) for c in rows[0]) + " |",
             "| " + " | ".join(["---"] * table.cols) + " |"]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def render_html(table: Table) -> str:
    """Clean HTML: one <tr> per line, rowspan/colspan only where above 1.

    Header rows go into <thead> only when no header cell spans down past them:
    a rowspan cannot cross from <thead> into <tbody>, so such a table keeps its
    <th> cells in one body instead of having its layout cut.
    """
    head = table.header_rows()
    while head and any(c.row < head < c.row + c.rowspan for c in table.cells):
        head -= 1

    def row_markup(r: int) -> str:
        parts = []
        for c in table.cells:
            if c.row != r:
                continue
            tag = "th" if c.header else "td"
            attributes = ""
            if c.rowspan > 1:
                attributes += f' rowspan="{c.rowspan}"'
            if c.colspan > 1:
                attributes += f' colspan="{c.colspan}"'
            text = escape(c.text, quote=False).replace("\n", "<br>")
            parts.append(f"<{tag}{attributes}>{text}</{tag}>")
        return "<tr>" + "".join(parts) + "</tr>"

    lines = ["<table>"]
    if table.caption:
        lines.append(f"<caption>{escape(table.caption, quote=False)}</caption>")
    if head:
        lines += ["<thead>", *(row_markup(r) for r in range(head)), "</thead>", "<tbody>"]
    lines += [row_markup(r) for r in range(head, table.rows)]
    if head:
        lines.append("</tbody>")
    lines.append("</table>")
    return "\n".join(lines)


def render(table: Table) -> str:
    """HTML when a cell is merged, Markdown otherwise - the skill's one rule."""
    return render_html(table) if table.has_spans() else render_markdown(table)


def _squash(text: str) -> str:
    """Cell text for comparison: whitespace is layout (stacked sub-lines), not content."""
    return re.sub(r"\s+", "", text)


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def compare(golden: Table, got: Table) -> dict:
    """How far a produced table is from a golden one.

    shapeMatch  same rows x columns and the same merged-cell layout;
    cellsExact  golden cells whose text the produced table has in the same
                anchored position (whitespace ignored);
    cer         character error rate of the cell texts read row by row, so a
                table with the right words in the wrong cells still shows as
                structurally wrong through shapeMatch/cellsExact, not through cer.
    """
    produced = {(c.row, c.col): _squash(c.text) for c in got.cells}
    exact = sum(1 for c in golden.cells if produced.get((c.row, c.col)) == _squash(c.text))
    reference = "".join(_squash(c.text) for c in golden.cells)
    hypothesis = "".join(_squash(c.text) for c in got.cells)
    distance = _edit_distance(reference, hypothesis)
    return {
        "shapeMatch": (golden.rows, golden.cols) == (got.rows, got.cols) and golden.layout() == got.layout(),
        "shape": f"{got.rows}x{got.cols}{' merged' if got.has_spans() else ''}",
        "goldenShape": f"{golden.rows}x{golden.cols}{' merged' if golden.has_spans() else ''}",
        "cellsExact": exact,
        "cells": len(golden.cells),
        "cer": distance / max(1, len(reference)),
    }
