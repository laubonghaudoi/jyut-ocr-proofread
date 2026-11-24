"""Page/column image rendering, ink measurement, leader dots, and glyph cropping."""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


from jyut_ocr.proofread.core import (  # noqa: E402
    CJK, INK_MARGIN, MEASURE_WIDTH, RULE_COVERAGE, norm_for_diff, _large_image_guard,
)


def measure_columns(render: Path, box: Sequence[float], text_only: bool = False,
                    turned: bool = False) -> list[tuple[int, int, int, int]] | None:
    """Real column geometry of a vertical-text block from its ink.

    Returns, right to left, (x0, x1, top, bottom) in render pixels for every
    ink column: x from the horizontal ink projection, top/bottom from the
    column's own vertical ink extent (a paragraph that ends half-way down
    leaves the rest blank).  No assumption about how many characters a
    column holds - that comes from the extent.  None when nothing usable is
    measured.

    TEXT_ONLY: the columns of text among them (text_columns), what
    crop_glyph cuts a disputed spot from.  column_layout takes every ink
    column.  TURNED: the render turned a quarter right (clockwise), and the
    pixels are the turned render's: the lines of horizontal text are then
    columns, the first on the right, each read top to bottom (crop_glyph).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(render) as image:
            orig_w, orig_h = image.size
            x, y, w, h = box
            if turned:
                rx, ry, rw, rh = 1 - y - h, x, h, w
                left, top = int(rx * orig_h), int(ry * orig_w)
                right, bottom = int((rx + rw) * orig_h), int((ry + rh) * orig_w)
                crop_bounds = (top, orig_h - right, bottom, orig_h - left)
                with _large_image_guard(orig_w * orig_h):
                    sub = image.crop(crop_bounds)
                region = sub.transpose(Image.ROTATE_270).convert("L")
            else:
                left, top = int(x * orig_w), int(y * orig_h)
                crop_bounds = (left, top, int((x + w) * orig_w), int((y + h) * orig_h))
                with _large_image_guard(orig_w * orig_h):
                    region = image.crop(crop_bounds).convert("L")
    except Exception:  # noqa: BLE001
        return None
    rw, rh = region.size
    if rw < 20 or rh < 20:
        return None
    ink = ink_of(region)
    columns = ink_columns(ink)
    if text_only:
        columns = text_columns(ink, columns)
    return [(left + a, left + b, top + y0, top + y1) for a, b, y0, y1 in columns] or None


def text_columns(ink: "Image.Image", columns: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """The columns of text among COLUMNS, a block's ink columns (ink_columns
    of INK, in its own pixels), for measure_columns' TEXT_ONLY.

    Columns of text only: an ink column narrower than 0.4 of the block's
    text width is dropped, the fraction ink_columns drops slivers at.  Its
    own cut is taken against the median of all the block's ink runs, and
    where thin runs beside the columns (a dashed rule, a sideline, specks
    of the scan) outnumber the columns, the median is one of them.
    Measured on a page of a 488-page book: 10 of a block's 17 ink runs were
    1-13 px wide against 61-72 px of text, so six of them were counted as
    columns, the glyph pitch was fitted at 124 px instead of 65, and a
    disputed character's crop (crop_glyph) was a strip 4 px wide beside a
    column - no character on it, and too thin for the server, which lost
    the page.  On the vertical blocks of seven books 467 of 5,830 had such
    columns; on 256 of them the pitch was fitted 1.5 times too large or
    more.  The text width is the width-weighted median of the columns
    (half of the columns' width lies in columns at least that wide): thin
    runs carry little width however many there are.

    A short column is measured again before it is dropped.  ink_columns
    takes a column's width from its ink averaged over the whole block's
    height, so the last column of a paragraph that ends a few characters
    down is thin there although its characters are as wide as the others':
    measured on the same 488-page book, a last column of five characters,
    285 px of a block 2,400 px high, was 23 px wide against 59, and the
    crop of a spot in it was then the foot of the column before.  So a
    column narrower than the cut and shorter than half the block's longest
    (only such a column loses half its density or more to the average) is
    measured on its own rows, between its neighbours, at the block's own
    ink cut (own_rows_extent); where it is as wide as the text there it is
    kept, with that extent.  A rule, a sideline or a column of marks runs
    most of the block and is not measured again.  On the vertical blocks of
    seven books (5,378) 186 columns in 106 blocks are kept again; of 72
    looked at, 56 were the last characters of a paragraph, now measured
    their full width (before the cut they were kept as slivers 1-23 px
    wide), and 16 were strokes of figures, a form's dot leaders or a column
    of circles, which had been columns before the cut too.
    """
    if not columns:
        return columns
    from PIL import Image
    rw = ink.width
    widths = sorted((b - a for a, b, _, _ in columns), reverse=True)
    reached, text_width = 0, widths[0]
    for text_width in widths:
        reached += text_width
        if 2 * reached >= sum(widths):
            break
    peak = max(ink.resize((rw, 1), Image.BOX).getdata()) or 1
    longest = max(y1 - y0 for _, _, y0, y1 in columns)
    kept = []
    for i, (a, b, y0, y1) in enumerate(columns):
        if b - a < 0.4 * text_width:
            if 2 * (y1 - y0) >= longest:
                continue
            lo = columns[i + 1][1] if i + 1 < len(columns) else 0
            hi = columns[i - 1][0] if i > 0 else rw
            a, b = own_rows_extent(ink, (a, b, y0, y1), lo, hi, peak, 0.25 * text_width)
            if b - a < 0.4 * text_width:
                continue
        kept.append((a, b, y0, y1))
    return kept


def own_rows_extent(ink: "Image.Image", column: tuple[int, int, int, int], lo: int, hi: int,
                    peak: float, gap: float) -> tuple[int, int]:
    """An ink column's x extent measured on its own rows (top to bottom of its
    ink), between LO and HI, at the same ink density as the block's: more than
    a tenth of PEAK, the block's densest x, as ink_columns cuts; runs split by
    less than GAP are one."""
    from PIL import Image
    a, b, y0, y1 = column
    if hi - lo < 1 or y1 - y0 < 1:
        return a, b
    profile = list(ink.crop((lo, y0, hi, y1)).resize((hi - lo, 1), Image.BOX).getdata())
    runs: list[list[int]] = []
    for x, value in enumerate(profile):
        if value > 0.10 * peak:
            if runs and x - runs[-1][1] < gap:
                runs[-1][1] = x + 1
            else:
                runs.append([x, x + 1])
    through = [(lo + s, lo + e) for s, e in runs if lo + s < b and lo + e > a]
    if not through:
        return a, b
    return min(a, min(s for s, _ in through)), max(b, max(e for _, e in through))


def ink_of(region: "Image.Image") -> "Image.Image":
    """The ink of a grayscale region: 255 where darker than its own paper by
    more than 60 levels (the paper is the modal level), 0 elsewhere."""
    hist = region.histogram()
    background = max(range(256), key=lambda i: hist[i])
    threshold = background - 60
    return region.point(lambda v: 255 if v < threshold else 0)


def ink_columns(ink: "Image.Image") -> list[tuple[int, int, int, int]]:
    """measure_columns on an ink image: every ink column, right to left, as
    (x0, x1, top, bottom) in the image's own pixels."""
    from PIL import Image
    rw, rh = ink.size
    xprof = list(ink.resize((rw, 1), Image.BOX).getdata())
    peak = max(xprof) or 1
    on = [v > 0.10 * peak for v in xprof]
    runs: list[list[int]] = []
    start = None
    for i, flag in enumerate(on + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append([start, i]); start = None
    if not runs:
        return []
    widths = sorted(b - a for a, b in runs)
    typical = widths[len(widths) // 2]
    # Merge runs split by a thin gap inside a glyph column; drop slivers.
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] < 0.25 * typical:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    columns = [(a, b) for a, b in merged if b - a >= 0.4 * typical]
    out: list[tuple[int, int, int, int]] = []
    for a, b in columns:
        col = ink.crop((a, 0, b, rh))
        yprof = list(col.resize((1, rh), Image.BOX).getdata())
        ypeak = max(yprof) or 1
        rows = [i for i, v in enumerate(yprof) if v > 0.10 * ypeak]
        if not rows:
            continue
        out.append((a, b, rows[0], rows[-1] + 1))
    out.sort(key=lambda c: -c[0])   # right to left
    return out


# A dot leader (目錄、表格入面嘅導引點線) is a run of small dots down a column,
# evenly spaced, with no character between them; so is a printed ellipsis, and
# both are written 「……」.  Their count is taken from the crop's ink with no
# model (leader_count), as the census of dot runs: the model's census counted 0
# marks on a table of contents printing 7 leaders, and the pass was skipped.  A
# dot is a blob of ink rows narrower than LEADER_DOT_SIZE of the column's width
# and wider than LEADER_DOT_MINIMUM of it (characters span the column; specks
# are smaller), round or two or three dots touching; a run is its dots in a row
# with no character between, each step within half to LEADER_STEP times their
# median spacing, and it counts from LEADER_MINIMUM_DOTS.  Measured on every
# page of six books (1,066 pages, vertical blocks): the 7 leaders of the table
# of contents ran 63-84 dots and nothing else there did; 9 runs of 8-11 dots
# on other pages were printed ellipses in prose, 5 of which engine A had
# written (engine B none); 2 more were not dots (a faint ruled line's fragments, a column of
# marks in a table); every other run was 7 dots or fewer, most of them 2 (a
# circle and a comma).  A printed ellipsis of 6 dots falls under the cut and is
# left to the model's census, so the count is a floor, not a ceiling.
# Vertical text only: a horizontal block is not measured.
LEADER_DOT_SIZE = 0.35
LEADER_DOT_MINIMUM = 0.10
LEADER_STEP = 2.5
LEADER_MINIMUM_DOTS = 8


def leader_dot_runs(region: "Image.Image") -> list[tuple[int, int, list[int]]] | None:
    """Per ink column of a grayscale block image, (x0, x1, [the length of each
    run of evenly spaced dots down it]); None when there are no columns."""
    from PIL import Image
    ink = ink_of(region)
    rw, rh = ink.size
    if rw < 20 or rh < 20:
        return None
    columns = ink_columns(ink)
    if not columns:
        return None
    # Text columns only: a printed rule between columns is a column of ink
    # too, a few pixels wide, and on a grey scan it breaks into fragments
    # that line up like dots.  Measured against the wider half of the
    # block's columns, which are text whatever else the block holds (a
    # heading column is wider still, rules far narrower).
    widths = sorted((x1 - x0 for x0, x1, _, _ in columns), reverse=True)
    upper = widths[:max(1, (len(widths) + 1) // 2)]
    text_width = upper[len(upper) // 2]
    out = []
    for x0, x1, top, bottom in columns:
        width = x1 - x0
        if width < 0.5 * text_width:
            continue
        strip = ink.crop((x0, top, x1, bottom))
        yprof = list(strip.resize((1, bottom - top), Image.BOX).getdata())
        blobs, start = [], None
        for y, v in enumerate(yprof + [0]):
            if v and start is None:
                start = y
            elif not v and start is not None:
                band = strip.crop((0, start, width, y)).resize((width, 1), Image.BOX)
                xs = [i for i, value in enumerate(band.getdata()) if value]
                extent = (xs[-1] - xs[0] + 1) if xs else 0
                blobs.append((start, y, extent))
                start = None
        runs, current = [], []
        for y0, y1, extent in blobs:
            # Narrow is what tells a dot from a character, which spans the
            # column: two or three dots printed touching make one taller blob,
            # still narrow, and a run must not break there.  Specks of the scan
            # are smaller than any printed dot, and a fragment of a faint ruled
            # line is far taller than it is wide, where a dot is round.
            dot = (LEADER_DOT_MINIMUM * width <= extent <= LEADER_DOT_SIZE * width
                   and y1 - y0 <= 3 * min(extent, LEADER_DOT_SIZE * width))
            if dot and current:
                pitches = [b[0] - a[0] for a, b in zip(current, current[1:])]
                pitch = sorted(pitches)[len(pitches) // 2] if pitches else None
                step = y0 - current[-1][0]
                if pitch is None or 0.5 * pitch <= step <= LEADER_STEP * pitch:
                    current.append((y0, y1))
                    continue
            if len(current) >= 2:
                runs.append(len(current))
            current = [(y0, y1)] if dot else []
        if len(current) >= 2:
            runs.append(len(current))
        out.append((x0, x1, runs))
    return out


def leader_count(crop: bytes | None) -> int | None:
    """How many dot leaders a block's crop prints (leader_dot_runs), or None
    when it cannot be measured: no crop, no ink columns, or horizontal text."""
    if not crop:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(crop)) as image:
            region = image.convert("L")
            region.load()
    except Exception:  # noqa: BLE001 - an unreadable crop is not measured
        return None
    ink = ink_of(region)
    xprof = list(ink.resize((region.width, 1), Image.BOX).getdata())
    yprof = list(ink.resize((1, region.height), Image.BOX).getdata())
    if not max(xprof) or not max(yprof):
        return None
    # block_is_vertical's test: vertical text leaves gaps between columns.
    xgap = sum(1 for v in xprof if v < 0.05 * max(xprof)) / region.width
    ygap = sum(1 for v in yprof if v < 0.05 * max(yprof)) / region.height
    if xgap < ygap:
        return None
    runs = leader_dot_runs(region)
    if runs is None:
        return None
    return sum(1 for _, _, lengths in runs for n in lengths if n >= LEADER_MINIMUM_DOTS)


TILE_CHARACTERS = 160   # a block larger than this is punctuated in column groups

def column_layout(render: Path, block: Mapping[str, Any]) -> list[tuple[int, int, int, int, int]] | None:
    """Columns of a vertical block, right to left, with their character counts.

    Each column's share of the block's characters is its ink extent divided by
    the glyph pitch, fitted so the shares add up to the block's character count.
    None when the geometry cannot be trusted.

    The columns are every ink column measure_columns finds, a rule or a
    sideline beside the text too (crop_glyph takes the columns of text only,
    text_columns).  The layout decides the column groups a large block's
    census and punctuate calls are cut in (block_tiles, column_reach, and
    the book profile's notation survey) and the edges a boundary crop shows
    (edge_region).
    """
    n = len(CJK(str(block["text"])))
    columns = measure_columns(render, block["box"])
    if not columns or n == 0:
        return None
    extents = [max(bottom - top, 1) for _, _, top, bottom in columns]
    pitch = sum(extents) / n
    if pitch <= 0:
        return None
    counts = [max(1, round(e / pitch)) for e in extents]
    if abs(sum(counts) - n) > max(2, n // 10):
        return None
    return [(x0, x1, top, bottom, count) for (x0, x1, top, bottom), count in zip(columns, counts)]


def block_tiles(render: Path, block: Mapping[str, Any], span_length: int,
                limit: int = TILE_CHARACTERS) -> list[tuple[bytes, int, int]] | None:
    """Split a large vertical block into groups of whole columns.

    The model cannot see marks a few pixels wide on a crop of a whole page
    (measured: on 570-character blocks the notation survey answered "no marks"
    with "570 marks" - it counted characters - on a book printed with circles
    throughout).  Block size is the layout detector's choice, not the book's,
    so crops are bounded here.  Returns (crop, start, end) with start/end in the
    block's merged text, scaled from the columns' character counts; None when
    the block is small, horizontal or its columns cannot be measured.
    """
    if span_length <= limit or block.get("label") == "table":
        return None
    if block_is_vertical(render, block["box"]) is False:
        return None
    layout = column_layout(render, block)
    if not layout:
        return None
    total = sum(c[4] for c in layout)
    scale = span_length / max(total, 1)
    groups, current, size = [], [], 0
    for column in layout:
        if current and size + column[4] > limit:
            groups.append(current); current, size = [], 0
        current.append(column); size += column[4]
    if current:
        groups.append(current)
    if len(groups) < 2:
        return None
    out, consumed = [], 0
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
            for number, group in enumerate(groups):
                x0 = min(c[0] for c in group); x1 = max(c[1] for c in group)
                top = min(c[2] for c in group); bottom = max(c[3] for c in group)
                colw = (x1 - x0) / len(group)
                # Right margin is kept (marks sit right of or below a character
                # in vertical setting); the left edge stops at the group so the
                # next group's marks are not counted twice.
                crop = image.crop((max(0, int(x0 - 0.15 * colw)), max(0, int(top - 0.02 * height)),
                                   min(width, int(x1 + 0.4 * colw)), min(height, int(bottom + 0.02 * height))))
                buffer = io.BytesIO(); crop.save(buffer, format="PNG")
                characters = sum(c[4] for c in group)
                start = consumed
                end = span_length if number == len(groups) - 1 else min(span_length,
                                                                       round((consumed / scale + characters) * scale))
                out.append((upscale_crop(buffer.getvalue()) or buffer.getvalue(), start, end))
                consumed = end
    except Exception:  # noqa: BLE001 - fall back to the whole block
        return None
    return out if out and out[-1][2] == span_length else None


def column_reach(render: Path, block: Mapping[str, Any], span_length: int) -> int:
    """One column's worth of a block's characters (its longest column, scaled
    as block_tiles scales them): how far a column group's text may be off its
    crop at either end (realign_answer); 0 when the columns cannot be measured."""
    layout = column_layout(render, block)
    if not layout:
        return 0
    counts = [column[4] for column in layout]
    return math.ceil(max(counts) * span_length / max(sum(counts), 1))


# What 裁決 is told about a crop that follows the text over a column break
# (crop_glyph's ACROSS).
COLUMN_BREAK_NOTE = ("裁圖由幾段拼成，段同段之間隔住一條空白：每段係一欄字嘅尾或者頭，由右至左連住讀"
                     "（右邊一段最尾嘅字，下一個就係左邊一段最頭嘅字）。爭議位可能喺任何一段，照上文同下文搵。")


def crop_glyph(render: Path, block: Mapping[str, Any], index: int, run: int,
               context: int = 4, pad: float = 0.02, across: dict[str, Any] | None = None) -> bytes | None:
    """Crop the cells of characters index..index+run of a block.

    Columns come from measure_columns, the columns of text only (TEXT_ONLY:
    a rule or a sideline beside the text is not a column, text_columns),
    whatever column_layout takes; each column's share of the block's
    characters is its ink extent divided by the glyph pitch, with the pitch
    fitted so the shares add up to the block's character count.  Measured
    need: a uniform-column estimate on a block of short paragraphs put the
    crop one column off, and the adjudicator confidently read the next item's
    number.

    RUN is the longer of the two engines' readings, and the crop shows all of
    it, with 2 * CONTEXT characters of reach above and below: the estimate
    drifts either way (an ink column that holds no text of the block's - a
    fold strip, a column engine A never read - makes the fitted pitch too
    small, and the spot then sits lower than estimated; measured: a crop that
    reached four characters above started one character below the disputed
    one, and 裁決 wrote the crop's top character for it).  A run that runs past
    its column's foot goes on at the head of the next column, and one that
    runs past the block's end was read before the spot, not after it: the
    crop is the box of every column the run touches.  Measured: 26 characters
    engine B read where engine A read the block's last four were cropped from
    those four down, clamped at the column's foot - the crop showed the last 7
    of them, 裁決 answered those 7, and 19 printed characters were deleted.

    Horizontal text is cut the same way on the render turned a quarter right
    (measure_columns' TURNED), where its lines are columns read right to
    left, each top to bottom, and the crop is turned back.  Which way a
    block's lines run is block_is_vertical's call, except for one line or
    one column, which it cannot tell (there is no gap between lines, and
    across the line its characters stand apart like columns).  So a block is
    measured first along its box's length (turned when the box is wider than
    tall), and one column there is a line read along it; a block of more
    lines is measured block_is_vertical's way where that fits.  A single
    column two characters' pitch wide or wider does not fit: it is the lines
    of a paragraph measured across, one ink column over the whole of them.

    Measured: the book profile cut the variant crops of books printed
    across as if in columns, the spot counted from the line's right end, or
    a strip across the whole paragraph through the middle of a line; for
    諭/谕, the crop showed 決, the line's last character.  Looked at, the
    crops of three such books showed the asked character on 7 of 181 and
    show it turned on 188 of 195; the other 7 lie a line or a few characters off,
    where the full-width marks, which take a cell in such print but are not
    counted, moved the estimate.  On every third page of one of them, 182
    of 821 blocks are single lines block_is_vertical takes for vertical,
    which phase 3 cut here (it shows a block it takes for horizontal whole,
    segment_crop_kind); of the whole book's sealed adjudications, 10 sat in
    such a line and 6 of them wrote neither engine's reading (杨彲 sealed as
    杨閣, the heading's third character).  On every other page of a book printed in columns,
    385 of 1,171 blocks are single columns it takes for horizontal (turned
    on its word, the crop of one, 1對十一師教導營訓話, was two characters,
    not the one asked); measured along their length they are cut as before,
    and that book's 120 variant crops are byte for byte the same.

    Not done: a block of two or three lines that block_is_vertical takes for
    vertical, its characters standing apart across the lines, is still cut
    as columns where they fit.  The title pages of the three books set 合编
    beside two lines, which measured along the box are then one ink column
    and do not fit; cut as columns, 合编 was sealed as 大, 社, 无 or 一 (7
    adjudications on five title pages).  Neither rule tried tells such
    blocks from blocks printed in columns (every third page of twelve
    books): reading a wide box along its lines whenever they fit turned 7
    blocks of four books printed in columns, and refusing a fit with a
    column two pitches wide left blocks of columns without one.

    ACROSS, when a dict is given, lets the crop follow the text over a column
    break (not on a turned render): where the spot is estimated within CONTEXT
    characters of its column's foot, the reach the foot cuts off is shown from
    the head of the next column, and for a spot that near its column's head,
    from the foot of the column before.  The parts are set side by side in
    reading order, right to left, with a strip of paper between (as
    boundary_crop sets two blocks' edges), and ACROSS is given "parts", how
    many; the caller tells 裁決 so (COLUMN_BREAK_NOTE).  Measured (2026-11-17,
    a 548-character block of 15 columns): the fit put a spot second from its
    column's foot that is printed second from the head of the next column;
    the crop ended at the foot, and in two runs the proofreader reported that
    the disputed character was not on it.  Without ACROSS the crop is as
    before (the book profile's variant crops, the coverage reads).
    """
    n = len(CJK(str(block["text"])))
    if n == 0:
        return None

    def fit(turned: bool) -> tuple[list[tuple[int, int, int, int]], list[int]] | None:
        columns = measure_columns(render, block["box"], text_only=True, turned=turned)
        if not columns:
            return None
        extents = [max(bottom - top, 1) for _, _, top, bottom in columns]
        pitch = sum(extents) / n
        counts = [max(1, round(e / pitch)) for e in extents]
        if abs(sum(counts) - n) > max(2, n // 10):
            return None
        if len(columns) == 1 and columns[0][1] - columns[0][0] >= 2 * pitch:
            return None   # the lines of a paragraph measured across, not a column
        return columns, counts

    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001
        return None
    # Measured first along the box's length: one column there is one line.
    turned = block["box"][2] * width > block["box"][3] * height
    fitted = fit(turned)
    if not (fitted and len(fitted[0]) == 1) and (block_is_vertical(render, block["box"]) is False) != turned:
        # More lines than one: block_is_vertical's way, where they fit.
        other = fit(not turned)
        if other:
            turned, fitted = not turned, other
    if not fitted:
        return None
    columns, counts = fitted
    run = max(run, 1)
    total = sum(counts)
    # The run as the columns hold it, moved back to end at the block's end
    # when it would run past it.
    start = max(0, min(index, total - run))
    end = min(total, start + run)
    boxes = []
    touched: list[tuple[int, int, int]] = []     # (column, its first character, one past its last)
    first = 0
    for number, ((x0, x1, top, bottom), count) in enumerate(zip(columns, counts)):
        last = first + count
        local_pitch = (bottom - top) / count
        if first < end and last > start:
            # The reach stays inside the columns the run touches.
            lo, hi = max(first, start - 2 * context), min(last, end + 2 * context)
            boxes.append((x0, x1, top + (lo - first) * local_pitch, top + (hi - first) * local_pitch))
            touched.append((number, first, last))
        first = last
    if not boxes:
        return None
    x0, x1 = min(b[0] for b in boxes), max(b[1] for b in boxes)
    y0, y1 = min(b[2] for b in boxes), max(b[3] for b in boxes)
    colw = max(b[1] - b[0] for b in boxes)
    # With ACROSS: the reach a column's end cuts off, from the column before
    # (its foot) and the column after (its head), for a spot estimated within
    # CONTEXT characters of that end.
    before = after = None
    if across is not None and not turned:
        number, first, _ = touched[0]
        if number > 0 and start - first <= context:
            # A quarter of a character of paper past the column's own end:
            # its last (first) glyph whole.
            px0, px1, ptop, pbottom = columns[number - 1]
            shown, pitch = min(2 * context - (start - first), counts[number - 1]), (pbottom - ptop) / counts[number - 1]
            before = (px0, px1, pbottom - shown * pitch, pbottom + 0.25 * pitch)
        number, _, last = touched[-1]
        if number < len(columns) - 1 and last - end <= context:
            nx0, nx1, ntop, nbottom = columns[number + 1]
            shown, pitch = min(2 * context - (last - end), counts[number + 1]), (nbottom - ntop) / counts[number + 1]
            after = (nx0, nx1, ntop - 0.25 * pitch, ntop + shown * pitch)
    try:
        from PIL import Image
        with Image.open(render) as image:
            if turned:
                image = image.transpose(Image.ROTATE_270)
            width, height = image.size

            def cut(source: "Image.Image", box: tuple[float, float, float, float], margin: float) -> "Image.Image":
                return source.crop((max(0, int(box[0] - margin)), max(0, int(box[2])),
                                    min(width, int(box[1] + margin)), min(height, int(box[3]))))

            crop = cut(image, (x0, x1, y0, y1), 0.3 * colw)
            if before or after:
                # One scale, one mode, and the page's own paper between the parts.
                source = image if image.mode in ("L", "RGB") else image.convert("L")
                levels = source.convert("L").histogram()
                paper = max(range(256), key=lambda level: levels[level])
                parts = [cut(source, box, 0.3 * (box[1] - box[0])) if box else None
                         for box in (before, None, after)]
                parts[1] = cut(source, (x0, x1, y0, y1), 0.3 * colw)
                parts = [part for part in parts if part is not None and min(part.size) >= 2]
                if len(parts) > 1:
                    gap = max(8, min(part.width for part in parts) // 2)
                    joined = Image.new(source.mode, (sum(part.width for part in parts) + gap * (len(parts) - 1),
                                                     max(part.height for part in parts)),
                                       paper if source.mode == "L" else (paper, paper, paper))
                    right = joined.width
                    for part in parts:               # reading order: from the right
                        joined.paste(part, (right - part.width, 0))
                        right -= part.width + gap
                    crop = joined
                    across["parts"] = len(parts)
            if turned:
                crop = crop.transpose(Image.ROTATE_90)
            buffer = io.BytesIO(); crop.save(buffer, format="PNG")
            return upscale_crop(buffer.getvalue())
    except Exception:  # noqa: BLE001
        return None


def crop_band(render: Path, block: Mapping[str, Any], seg: Mapping[str, Any],
              reach: float = 0.10, pad: float = 0.02, index: int | None = None) -> bytes | None:
    """Crop a horizontal band of a block around a disagreement.

    A whole vertical block is a strip of thousands of pixels in which one
    character must be found; a faint stroke at the block's edge is lost to the
    box's own boundary (measured: a 二 whose lower stroke printed light at a
    column foot was cropped to 一).  The position is estimated from the
    character's index in the block text, with columns approximated from the
    block's shape, and the band is padded well beyond the box.
    """
    text = norm_for_diff(str(block["text"]))
    # Geometry in CJK units, the same units as an offset from the merge: a table
    # block's raw text carries HTML tags that would inflate the glyph count.
    n = max(len(CJK(str(block["text"]))), 1)
    if index is None:
        index = -1
        after_key = norm_for_diff(seg["after"][:6])
        if len(after_key) >= 2 and after_key in text:
            index = max(0, text.find(after_key) - max(len(norm_for_diff(seg["a"])), 1))
        else:
            for key in (norm_for_diff(seg["a"]), norm_for_diff(seg["b"]), norm_for_diff(seg["before"][-6:])):
                if len(key) >= 2 and key in text:
                    index = text.find(key); break
    x, y, w, h = block["box"]
    if index < 0 or w <= 0 or h <= 0:
        return crop_block(render, block["box"], pad)
    # Glyph pitch from area: n glyphs fill w*h, so pitch ~ sqrt(w*h/n) (page fractions,
    # corrected for the page's aspect ratio being applied per axis at crop time).
    columns = max(1, round(w / max((w * h / n) ** 0.5, 1e-6)))
    per_column = n / columns
    fraction = (index % per_column) / per_column if per_column else 0.0
    # Reach at least the disputed run plus three glyphs each side, in glyph
    # units - a fixed fraction of a short block of figures was one character
    # (measured: a three-digit conflict judged on its first digit alone).
    run = max(len(seg.get("a", "")), len(seg.get("b", "")))
    reach = max(reach, (run + 3) / per_column if per_column else reach)
    top = y + max(0.0, fraction - reach * 0.5) * h
    bottom = y + min(1.0, fraction + reach) * h
    # Narrow to the estimated column (right-to-left) with a half-column margin:
    # a neighbouring column's own marker in the crop was measured to be read as
    # the answer.
    column = min(columns - 1, int(index // per_column)) if per_column else 0
    column_width = w / columns
    right = x + w - column * column_width + 0.2 * column_width
    left = x + w - (column + 1) * column_width - 0.2 * column_width
    left, right = max(x - pad, left), min(x + w + pad, right)
    return upscale_crop(crop_block(render, [left, top, right - left, bottom - top], pad / 2))


def edge_region(render: Path, block: Mapping[str, Any], end: bool, run: int, size: tuple[int, int],
                context: int = 4, pad: float = 0.01) -> tuple[int, int, int, int]:
    """The render pixels (left, top, right, bottom) at one edge of a layout
    block: its first glyphs (END false) or its last (END true), for
    boundary_crop.

    A vertical block's end is the foot of its last (leftmost) column, from
    CONTEXT glyphs above the column's last ink to RUN + CONTEXT glyphs below
    it - past the box, in the reading direction, since a character engine A
    left out of its reading may be left out of its box too.  Its start is cut
    as crop_glyph cuts the block's first character.  Columns and pitch come
    from column_layout; when they cannot be measured, from the box's shape as
    crop_band estimates them, the whole column's height taken.  A horizontal
    block or a table is the whole box, as segment_crop_kind shows it.
    """
    width, height = size
    x, y, w, h = block["box"]
    whole = (max(0, int((x - pad) * width)), max(0, int((y - pad) * height)),
             min(width, int((x + w + pad) * width)), min(height, int((y + h + pad) * height)))
    if block.get("label") == "table" or block_is_vertical(render, block["box"]) is False:
        return whole
    layout = column_layout(render, block)
    if layout:
        x0, x1, top, bottom, count = layout[-1] if end else layout[0]
        pitch = (bottom - top) / count
        if end:
            y0, y1 = bottom - min(count, context) * pitch, bottom + (run + context) * pitch
        else:
            y0, y1 = top, top + min(count, run + 2 * context) * pitch
        margin = 0.3 * (x1 - x0)
        return (max(0, int(x0 - margin)), max(0, int(y0)), min(width, int(x1 + margin)), min(height, int(y1)))
    n = max(len(CJK(str(block["text"]))), 1)
    if w <= 0 or h <= 0:
        return whole
    columns = max(1, round(w / max((w * h / n) ** 0.5, 1e-6)))
    column_width, pitch = w / columns, h * columns / n
    left = x if end else x + w - column_width
    top, bottom = (y, y + h + (run + context) * pitch) if end else (y, y + min(h, (run + 2 * context) * pitch))
    return (max(0, int((left - 0.2 * column_width) * width)), max(0, int((top - pad) * height)),
            min(width, int((left + 1.2 * column_width) * width)), min(height, int(bottom * height)))


# What 裁決 is told about a boundary crop's two parts, by how they are set.
BOUNDARY_NOTES = {
    "columns": "裁圖由兩段拼成，中間隔住一條空白：右邊係上文所在嗰段字嘅尾，左邊係下文所在嗰段字嘅頭。"
               "爭議位喺兩段交界：右邊嗰段最尾或者左邊嗰段最頭。",
    "rows": "裁圖由兩段拼成，中間隔住一條空白：上面係上文所在嗰段字嘅尾，下面係下文所在嗰段字嘅頭。"
            "爭議位喺兩段交界：上面嗰段最尾或者下面嗰段最頭。",
    "end": "裁圖係上文所在嗰段字嘅尾，連埋佢後面嘅空位。爭議位喺呢段字最尾。",
}


def boundary_crop(render: Path, previous: Mapping[str, Any], following: Mapping[str, Any] | None,
                  run: int) -> tuple[bytes | None, str]:
    """The crop for text only engine B read where engine A's reading passes
    from one layout block to the next, and how its parts are set (a
    BOUNDARY_NOTES key).

    The alignment cannot tell whether such text ends PREVIOUS or begins
    FOLLOWING, and the two blocks may stand anywhere on the page (another
    tier, another column group).  The crop from the insertion's place in
    engine A's stream showed the head of FOLLOWING alone, which cannot show a
    character printed at the foot of PREVIOUS: measured on a page whose two
    blocks stand on different tiers, the character printed at the foot of
    the first (inside its box; engine A's reading had left it out) was not
    on the crop, and 裁決 would delete it.  On one book 7 of the 16 one- and
    two-character insertions only engine B read sit on such an edge; on the
    stand-in benchmark pages 17 questions on 7 pages.  So both edges are
    shown, cut from the render at one scale and set
    side by side in reading order (right to left, when both blocks are
    vertical; else one above the other), with a strip of paper between; at the
    page's end, the end of PREVIOUS alone.  (None, "") when nothing can be cut.
    """
    try:
        from PIL import Image
        with Image.open(render) as opened:
            with _large_image_guard(opened.width * opened.height):
                image = opened.convert("L") if opened.mode not in ("L", "RGB") else opened.copy()
        size = image.size
        foot = image.crop(edge_region(render, previous, True, run, size))
        if min(foot.size) < 2:
            return None, ""
        if following is None:
            buffer = io.BytesIO(); foot.save(buffer, format="PNG")
            return upscale_crop(buffer.getvalue()), "end"
        head = image.crop(edge_region(render, following, False, run, size))
        # The strip between the parts is the page's own paper (its modal level).
        levels = image.convert("L").histogram()
        paper = max(range(256), key=lambda i: levels[i])
    except Exception:  # noqa: BLE001 - the ordinary crop stands instead
        return None, ""
    if min(head.size) < 2:
        return None, ""
    vertical = all(block.get("label") != "table" and block_is_vertical(render, block["box"]) is not False
                   for block in (previous, following))
    fill = paper if image.mode == "L" else (paper, paper, paper)
    if vertical:
        gap = max(8, min(foot.width, head.width) // 2)
        joined = Image.new(foot.mode, (foot.width + gap + head.width, max(foot.height, head.height)), fill)
        joined.paste(head, (0, 0))
        joined.paste(foot, (head.width + gap, 0))
    else:
        gap = max(8, min(foot.height, head.height) // 4)
        joined = Image.new(foot.mode, (max(foot.width, head.width), foot.height + gap + head.height), fill)
        joined.paste(foot, (0, 0))
        joined.paste(head, (0, foot.height + gap))
    buffer = io.BytesIO(); joined.save(buffer, format="PNG")
    return upscale_crop(buffer.getvalue()), "columns" if vertical else "rows"


def upscale_crop(png: bytes | None, minimum_side: int = 700) -> bytes | None:
    """Enlarge a small crop so glyph strokes survive the vision encoder.

    Measured: a clear （二） at ~70 px glyph height was read as 一; stroke
    counting needs pixels.  Enlarging adds no information, but it stops the
    encoder's own downsampling from discarding what is there.
    """
    if not png:
        return png
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            short = min(image.size)
            if short >= minimum_side:
                return png
            factor = min(4.0, minimum_side / max(short, 1))
            enlarged = image.resize((int(image.width * factor), int(image.height * factor)), Image.LANCZOS)
            buffer = io.BytesIO(); enlarged.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - never lose the crop over a resize
        return png


def block_is_vertical(render: Path, box: Sequence[float]) -> bool | None:
    """Whether a block's text runs in vertical columns, from its ink alone.

    Vertical text leaves clear gaps between columns in the horizontal ink
    profile and an almost continuous vertical profile; horizontal text is the
    reverse.  None when the block is too small or blank to tell.
    """
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
            x, y, w, h = box
            with _large_image_guard(width * height):
                region = image.crop((int(x * width), int(y * height),
                                     int((x + w) * width), int((y + h) * height))).convert("L")
    except Exception:  # noqa: BLE001
        return None
    rw, rh = region.size
    if rw < 20 or rh < 20:
        return None
    hist = region.histogram()
    background = max(range(256), key=lambda i: hist[i])
    ink = region.point(lambda v: 255 if v < background - 60 else 0)
    xprof = list(ink.resize((rw, 1), Image.BOX).getdata())
    yprof = list(ink.resize((1, rh), Image.BOX).getdata())
    if not max(xprof) or not max(yprof):
        return None
    xgap = sum(1 for v in xprof if v < 0.05 * max(xprof)) / rw
    ygap = sum(1 for v in yprof if v < 0.05 * max(yprof)) / rh
    return xgap >= ygap


def image_size(render_bytes: bytes | None) -> tuple[int, int] | None:
    """The render's (width, height) in pixels, or None."""
    if not render_bytes:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(render_bytes)) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return None


def crop_block(render: Path, box: Sequence[float], pad: float = 0.01,
               pad_left: float | None = None) -> bytes | None:
    """Crop a normalised [x, y, w, h] top-left-origin box out of the render.

    `pad_left` overrides the padding on the left edge alone: in vertical
    setting a mark sits at the lower right of its character, so the marks of
    the column to the LEFT of a block lie in the gutter against the block's
    left edge, and ordinary padding pulls them into frame (measured: a
    four-character heading came back with a circle at each end).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(render) as image:
            width, height = image.size
            x, y, w, h = box
            left = max(0, int((x - (pad if pad_left is None else pad_left)) * width))
            top = max(0, int((y - pad) * height))
            right = min(width, int((x + w + pad) * width))
            bottom = min(height, int((y + h + pad) * height))
            if right - left < 8 or bottom - top < 8:
                return None
            buffer = io.BytesIO()
            image.crop((left, top, right, bottom)).save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a bad crop must not kill the page
        return None


def page_ink_fraction(render: Path) -> float:
    """Fraction of the page carrying TEXT ink, or -1.0 if it cannot be read.

    Two corrections over a plain darkness count, both forced by real pages:

    An absolute cutoff assumes white paper.  The corpus's 1910 government report is scanned
    grey - its paper sits well below level 200 - so counting pixels under 200
    made every blank page read as 99.9% ink and three genuine blanks went
    undetected.  Ink is contrast against the paper this page was scanned on.

    And ink is not the same as text.  That book's blank leaves still carry a
    printed border frame and column rules, so they measure MORE ink (6.4%) than
    a real table page in a fiscal report (4.0%).  Rows and columns that are
    almost entirely inked are rules; what is left is text.  A blank ruled leaf
    then measures near zero, which is the whole point.

    Pure PIL - the runtime has no numpy - using row-slice byte counts, so the
    per-page cost stays in the milliseconds.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - PIL is needed for crops anyway
        return -1.0
    try:
        with Image.open(render) as opened:
            with _large_image_guard(opened.width * opened.height):
                opened.draft("L", (MEASURE_WIDTH, max(1, round(opened.height * MEASURE_WIDTH / max(1, opened.width)))))
                image = opened.convert("L")
                if image.width > MEASURE_WIDTH:
                    height = max(1, round(image.height * MEASURE_WIDTH / image.width))
                    image = image.resize((MEASURE_WIDTH, height), Image.BOX)
                histogram = image.histogram()
            total = sum(histogram)
            if not total:
                return -1.0
            paper = max(range(len(histogram)), key=lambda level: histogram[level])
            cutoff = paper - INK_MARGIN
            if cutoff <= 0:
                # Modal level already black: no usable contrast to measure.
                return -1.0
            binary = image.point(lambda level: 255 if level < cutoff else 0)
            width, height = binary.size
            rows = binary.tobytes()
            columns = binary.transpose(Image.ROTATE_90).tobytes()
    except OSError:
        return -1.0

    row_ink = [rows[y * width:(y + 1) * width].count(255) for y in range(height)]
    column_ink = [columns[x * height:(x + 1) * height].count(255) for x in range(width)]
    rule_columns = sum(1 for ink in column_ink if ink >= height * RULE_COVERAGE)
    text_rows = [ink for ink in row_ink if ink < width * RULE_COVERAGE]
    if not text_rows:
        return 0.0
    # Every surviving row still crosses the rule columns; discount them.
    text_ink = sum(max(0, ink - rule_columns) for ink in text_rows)
    area = len(text_rows) * max(1, width - rule_columns)
    return text_ink / area
