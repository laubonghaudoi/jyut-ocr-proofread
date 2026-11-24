"""Windowed table merge, edge nomination/verdicts, region rebuild, and unread cell confirmation."""

from __future__ import annotations

from collections import Counter
import difflib
import itertools
import os
from pathlib import Path
import sys
from typing import Any, Callable, Collection, Mapping, Sequence


from jyut_ocr.core import tables as _tables  # noqa: E402
from jyut_ocr.proofread.core import (  # noqa: E402
    CIRCLE_CHARS, CJK, DROP_GRAM, FURNITURE_LABELS, LOOPED_READING, NUMERAL_CHARS, TEXT_SEAMS,
    facade_attr, is_numeral_conflict, strip_fence,
)
from jyut_ocr.proofread.crops import (  # noqa: E402
    crop_block, upscale_crop,
)
from jyut_ocr.proofread.variants import variant_fold  # noqa: E402
from jyut_ocr.proofread.adjudicate import (  # noqa: E402
    MOVED_SHARE, fuse_numeral_runs, restore_merged_characters, segment_crop,
)
from jyut_ocr.proofread.furniture import READING_ORDER_AGREEMENT  # noqa: E402
from jyut_ocr.proofread.markers import (  # noqa: E402
    bracket_explanation, mark_explanation,
)
from jyut_ocr.proofread.tables import (  # noqa: E402
    TABLE_MINIMUM_ROWS, WITNESS_ANCHOR, furniture_row, witness_anchors, witness_refusal,
)


def edge_copies(merged: str, lo: int, hi: int, draft: str, draft2: str | None,
                origin: Sequence[str] | None = None) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], the edge text of a table (table_edges), written
    beside the table, would be an extra copy: text the page already holds
    where the other engine read it.  The stretches of DROP_GRAM characters
    (the text itself when it is shorter) that MERGED, the merged text with
    the edge text in it, holds more often than either engine's reading does
    - the count extra_copies takes of a page, variant-folded - and that
    stand elsewhere on the page (outside LO:HI) as engine A's reading alone,
    cover more than MOVED_SHARE of its characters.  Returns that evidence,
    or None.

    Engine A's reading alone: more than half the characters of a copy
    elsewhere are ones the merge wrote from engine A's reading where engine
    B read nothing or something else (ORIGIN, merged_origins; without it
    the count alone decides).  The count alone also fires where engine B
    misread the copy elsewhere: its reading then holds the stretch once too
    few.  On a made-up page (check_tables,
    check_table_edge_text_misread_elsewhere) a title only engine B read
    restated two of the table's column labels, engine B read one of those
    labels with one character wrong in its cells, and the title was held
    back as an extra copy of the labels (57%), under 裁決 too, which had
    answered it printed: engine B read the labels where they stand, so the
    title is no reading of them.

    A one-engine reading at a block boundary goes to the next block
    (block_spans), and where that is a table, what an engine read of the page
    in another order lands at its edge.  Measured on the corpus's 48 pages
    with a table block in engine A's layout, replayed offline (both engine B
    versions, 裁決 answered from the sealed runs): on two pages of one book
    (its second scan, engine B v1.0) the edge text was engine B's reading of
    what engine A read elsewhere on the page - the running foot and its
    folio, which engine A read as its footer and number blocks (a share of
    0.67), and the folio with the table's own column labels, which engine A
    read in its cells (0.75).  The merge held both because 裁決 answered 無
    there and D16 refused the answer (engine A's copy is beyond its reach);
    before the edge text was written, the table's token dropped them.  Text
    read in another order is placed once
    by D2 (place_moved_once), but not a run shorter than MOVE_MINIMUM: on a
    made-up page (check_tables, check_table_edge_text_read_by_a) a
    two-character title engine A read below the table and engine B above it
    came out twice.  A title only engine B read, the made-up page the rule is
    for (check_table_edge_text), has none of its stretches held so: 0.  A
    stretch of figures alone is no such evidence (extra_copies), nor is a
    single character."""
    text = merged[lo:hi]
    if len(text) < 2:
        return None
    folded, page = variant_fold(text), variant_fold(merged)
    readings = (variant_fold(CJK(draft)), variant_fold(CJK(draft2 or "")))
    size = min(DROP_GRAM, len(text))

    def read_by_a_alone(gram: str) -> bool:
        """Whether GRAM stands outside LO:HI where engine A's reading alone
        wrote most of it."""
        if origin is None:
            return True
        at = page.find(gram)
        while at >= 0:
            if (at + size <= lo or at >= hi) and 2 * sum(origin[at + j] == "a" for j in range(size)) > size:
                return True
            at = page.find(gram, at + 1)
        return False

    held = bytearray(len(text))
    stretches: list[str] = []
    for i in range(len(text) - size + 1):
        gram = folded[i:i + size]
        if set(gram) <= NUMERAL_CHARS:
            continue
        counts = (page.count(gram), *(reading.count(gram) for reading in readings))
        if counts[0] > max(counts[1:]) and read_by_a_alone(gram):
            held[i:i + size] = b"\1" * size
            stretches.append(f"{text[i:i + size]} {counts[0]}/{counts[1]}/{counts[2]}")
    share = sum(held) / len(text)
    return ({"share": round(share, 2), "stretches": stretches[:12]} if share > MOVED_SHARE else None)


def engine_b_table_lines(text: str) -> set[int]:
    """The lines of engine B's reading TEXT, numbered as
    engine_b_lines_merged numbers them, that are cells of a table in it
    (Markdown or HTML markup).  For the table-edge-text doubt: engine B may
    read the text beside a table in its own table's cells - its reading of
    the table's first cells, which engine A read differently - and the doubt
    says so.  On a made-up page (check_tables,
    check_table_edge_text_read_in_cells) engine A misread the table's first
    three characters, engine B read a title above the table and those
    cells, the merge kept both readings (a replacement too uneven to pair),
    and engine B's reading of the cells was written above the table with
    the title, the doubt saying only "written"."""
    spans = [(table.start, table.end) for table in _tables.find_tables(text)]
    out: set[int] = set()
    if not spans:
        return out
    number, count, gap = 0, 0, ""
    for offset, char in enumerate(text):
        if not CJK(char):
            gap += char
            continue
        if count and TEXT_SEAMS.search(gap):
            number += 1
        if any(lo <= offset < hi for lo, hi in spans):
            out.add(number)
        count, gap = count + 1, ""
    return out


def edge_judged(text: str) -> bool:
    """Whether edge_copies can judge TEXT: two characters or more, and a
    stretch of them not all figures."""
    size = min(DROP_GRAM, len(text))
    return len(text) >= 2 and any(not set(text[i:i + size]) <= NUMERAL_CHARS
                                  for i in range(len(text) - size + 1))


def block_copy(merged: str, lo: int, hi: int, draft: str, draft2: str | None, origin: Sequence[str] | None,
               blocks_at: Sequence[tuple[int, int, int]]) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], a piece of a table's edge text, is the whole
    of a block of engine A's elsewhere on the page - BLOCKS_AT, (layout
    number, start, end) in MERGED - that the merge wrote as engine A's
    reading alone (ORIGIN, merged_origins: more than half its characters),
    and the page holds it more often than either engine read it: engine B's
    reading of that block, read in another place.  Returns that evidence,
    or None.

    Figures alone and two characters, which edge_copies' stretches cannot
    weigh (a pair of numerals says nothing of order): on a made-up page
    (check_tables, check_table_edge_text_whole_block) engine A read the folio
    十二 as its number block, engine B read it after a table that ends the
    page, and the folio was written twice; a prose block there keeps it
    twice too (D2 never places a run of figures).  The whole block, standing
    where engine B read nothing, is the evidence a stretch of figures
    lacks."""
    text = variant_fold(merged[lo:hi])
    if len(text) < 2 or origin is None:
        return None
    for layout, start, end in blocks_at:
        if (end - start != len(text) or not (end <= lo or start >= hi)
                or variant_fold(merged[start:end]) != text
                or 2 * sum(origin[p] == "a" for p in range(start, end)) <= end - start):
            continue
        counts = (variant_fold(merged).count(text), variant_fold(CJK(draft)).count(text),
                  variant_fold(CJK(draft2 or "")).count(text))
        if counts[0] > max(counts[1:]):
            return {"share": 1.0, "stretches": [f"{merged[lo:hi]} {counts[0]}/{counts[1]}/{counts[2]}"],
                    "wholeBlock": layout}
    return None


def cells_copy(merged: str, lo: int, hi: int, draft: str, draft2: str | None, cells: str) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], a piece of a table's edge text, is engine B's
    second reading of the table's own cells: it stands whole in CELLS (the
    table's characters in reading order, across cells and rows), it is not
    figures alone, it is DROP_GRAM characters or more, and engine B read it
    more often than engine A, who read it (and the page holds it more often
    than engine A read it).  Returns that evidence, or None.

    edge_copies does not see it: engine B read it twice, so the page holds
    it no more often than engine B read it, and the copy in the cells is
    both engines' reading.  On a made-up page (check_tables,
    check_table_edge_text_cells_again) engine B read the table's last two
    rows again after a table that ends the page; the merge keeps a run only
    one engine read under every preference, and the rows were written again
    after the table.  Whether a page prints a table's cells twice the text
    cannot tell - so it is a doubt, never a silent drop; a table's title
    that is nothing but its column labels in order would be held back too."""
    text = variant_fold(merged[lo:hi])
    if len(text) < DROP_GRAM or set(text) <= NUMERAL_CHARS or text not in variant_fold(cells):
        return None
    counts = (variant_fold(merged).count(text), variant_fold(CJK(draft)).count(text),
              variant_fold(CJK(draft2 or "")).count(text))
    if counts[2] > counts[1] >= 1 and counts[0] > counts[1]:
        return {"share": 1.0, "stretches": [f"{merged[lo:hi]} {counts[0]}/{counts[1]}/{counts[2]}"],
                "tableCells": True}
    return None


def edge_verdicts(merged: str, lo: int, hi: int, draft: str, draft2: str | None,
                  origin: Sequence[str] | None = None, lines: Sequence[int] | None = None,
                  blocks_at: Sequence[tuple[int, int, int]] = (), cells: str = "") -> list[dict[str, Any]]:
    """The edge text MERGED[LO:HI] of a table, piece by piece, each piece
    with edge_copies' verdict: {"lo", "hi", "judged", "held"} - "held" the
    evidence that it is an extra copy (not written), or None (written).

    A piece is what engine B read on one line or in one table cell (LINES,
    engine_b_lines_merged: each merged character's line of engine B's; the
    characters of no line of engine B's make pieces of their own), or the
    whole stretch without LINES.  Judged whole, a stretch of engine B's
    reading of text engine A read elsewhere and a line only engine B read
    beside it went together: on a made-up page (check_tables,
    check_table_edge_text_pieces) engine B read, after a table that ends
    the page, a unit line only it read and then the running foot, which
    engine A read as its footer block (D2 does not place a run whose pairs
    include two figures); the foot was 60% of the stretch, and the unit line
    was not written with it, the doubt blaming it on a reading elsewhere.

    A piece that is the whole of a block engine A alone read elsewhere
    (BLOCKS_AT, block_copy) is held back too, figures and all, and so is one
    that is engine B's second reading of the table's own cells (CELLS, the
    table's characters: cells_copy).  A piece edge_copies cannot judge
    (edge_judged: one character, or figures alone) and neither of those
    holds goes with the nearest piece before it on the same side that was
    judged, else the nearest after it, and is written where there is none.
    Measured on the corpus's 48 pages with a table block in engine A's
    layout, replayed offline: the two pages whose edge text was held back
    (edge_copies) each hold one - a folio's first digits on a line after
    the running foot, in one cell of engine B's (the foot held back, 0.80),
    and the end of engine B's line of the running head and folio, before
    the column labels it read again (each label 1.00).  The digits alone
    say nothing, and written they were furniture."""
    bounds = [lo]
    if lines is not None:
        bounds += [p for p in range(lo + 1, hi) if lines[p] != lines[p - 1]]
    bounds.append(hi)
    parts = []
    for start, end in zip(bounds, bounds[1:]):
        judged = edge_judged(merged[start:end])
        held = edge_copies(merged, start, end, draft, draft2, origin) if judged else None
        # The whole of a block engine A alone read elsewhere (block_copy):
        # figures too, which the stretches cannot weigh.  Or engine B's
        # second reading of the table's own cells (cells_copy).
        held = (held or block_copy(merged, start, end, draft, draft2, origin, blocks_at)
                or cells_copy(merged, start, end, draft, draft2, cells))
        parts.append({"lo": start, "hi": end, "judged": judged or held is not None, "held": held})
    for k, part in enumerate(parts):
        if part["judged"]:
            continue
        near = (next((q for q in reversed(parts[:k]) if q["judged"]), None)
                or next((q for q in parts[k + 1:] if q["judged"]), None))
        if near is not None and near["held"] is not None:
            part["held"] = {"share": near["held"]["share"], "stretches": [],
                            "with": merged[near["lo"]:near["hi"]]}
    return parts


def merged_other_readings(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str]) -> list[str]:
    """For each character of the merged text ("".join(pieces)), the other
    engine's character there where the engines read one character for one
    and the merge wrote one of them (the D20 lock lets a rebuilt table write
    the other), and "" elsewhere or where the two are one character."""
    out: list[str] = []
    for seg, piece in zip(segments, pieces):
        a, b = seg["a"], seg["b"]
        if seg["tag"] != "equal" and len(a) == len(b) == len(piece) and piece in (a, b):
            out += [y if y != x else "" for x, y in zip(piece, b if piece == a else a)]
        else:
            out += [""] * len(piece)
    return out


# The shortest run of a rebuilt table's characters matched in order with its
# region's merged text that places the table there (rebuilt_edges): twice
# DROP_GRAM.  A title shares words with its table's rows - on the made-up
# page of check_table_edge_text_rebuilt three characters - and a run that
# short, met by chance where the rebuild wrote the rows in another order,
# would cut the title there.
REBUILT_ANCHOR = 6


def rebuilt_edges(table: str, region: str, other: Sequence[str], origin: Sequence[str],
                  first: int, last: int) -> tuple[int, int]:
    """How many characters at the start and at the end of REGION - a rebuilt
    table's region's merged text, its blocks' spans joined - the table's
    characters TABLE (as the rebuild answered, in its order) do not hold:
    its edge text, from the region's first character on within its first
    block's span (FIRST characters), and back from its last within its last
    block's (LAST).

    A character of REGION is the table's where the table holds it: matched
    in order in a run of REBUILT_ANCHOR characters or more; or where the
    table wrote, one for one in its place, the other engine's reading of it
    (OTHER, merged_other_readings - the D20 lock lets a rebuild write it);
    or, else, counted: while REGION holds the character more often than the
    table does, less the characters so written, it is not the table's.  All
    variant-folded.  And a character of engine A's reading alone (ORIGIN,
    merged_origins) is the table's too: a rebuilt table stands for engine
    A's block, and what engine A alone read in it and the rebuild left out
    is its reading of the table, which the rebuilt-unvouched doubt names -
    the text a table's edge holds is text engine A did not read in its
    block (block_spans hands engine B's reading at a block's edge to the
    block after it).  Measured offline on the benchmark (the runs whose
    order-conflict page has a grid refused for copies and rebuilt from
    engine B's lines, with a stand-in rebuild that writes only the
    characters it must, the first of each): the window merge wrote engine
    A's second copy of a character after the block's last window, and it
    was written after the table on a line of its own though the page
    prints it once, inside the table.

    Counted alone, as the rule first counted, from the first character on
    while the count held, the walk went by characters, not by their places.  On
    made-up pages (check_tables, check_table_edge_text_rebuilt_places): a
    title only engine B read above a nominated block, 各種讀本年用法一覽表,
    and a rebuild that wrote engine A's 年 where the merge wrote engine B's
    季 - the table's 年 used up the title's, the walk stopped there, and
    年用法一覽表 was lost while the doubt said 各種讀本 was written; the
    region's first character, 弟 as the merge wrote it, which the rebuild
    wrote as engine B's 第, was written again on a line of its own; and a
    character engine B read once more in the middle of the first row, one
    the rebuild left out, was taken for the region's first character, which
    the table's first cell holds, and written twice."""
    t, r = variant_fold(table), variant_fold(region)
    anchored, written = bytearray(len(r)), bytearray(len(r))
    held, count = Counter(t), Counter(r)
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, t, r, autojunk=False).get_opcodes():
        if tag == "equal" and a2 - a1 >= REBUILT_ANCHOR:
            anchored[b1:b2] = b"\1" * (b2 - b1)
        elif tag == "replace" and a2 - a1 == b2 - b1:
            for k in range(a2 - a1):
                if other[b1 + k] and variant_fold(other[b1 + k]) == t[a1 + k] != r[b1 + k]:
                    written[b1 + k] = 1
                    held[t[a1 + k]] -= 1
                    count[r[b1 + k]] -= 1
    left = count - held

    def beside(p: int) -> bool:
        """Whether REGION's character P is none of the table's."""
        return not anchored[p] and not written[p] and origin[p] != "a" and left[r[p]] > 0

    x = 0
    while x < first and beside(x):
        left[r[x]] -= 1
        x += 1
    y = len(r)
    while y > max(x, len(r) - last) and beside(y - 1):
        left[r[y - 1]] -= 1
        y -= 1
    return x, len(r) - y


def merged_origins(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str]) -> list[str]:
    """Whose reading each character of the merged text ("".join(pieces))
    is, where it stands: "both" where the two engines read that character
    there (agreed, or one for one in another variant form), "a" where the
    merge wrote engine A's reading and engine B read nothing or something
    else there, "b" the same the other way round, and "" where the merge
    wrote neither reading as it stands (裁決's third reading).  For
    edge_copies: a copy elsewhere is engine A's reading alone; and for
    rebuilt_edges.  A segment of an order-conflict table block read from
    engine B's lines (window_merge) may stand "equal" with one side empty:
    it is the reading of the side that has it."""
    out: list[str] = []
    for seg, piece in zip(segments, pieces):
        a, b = seg["a"], seg["b"]
        if seg["tag"] == "equal" and a and b:
            out += ["both"] * len(piece)
        elif len(a) == len(b) == len(piece) and piece in (a, b):
            side = "a" if piece == a else "b"
            out += ["both" if variant_fold(x) == variant_fold(y) else side for x, y in zip(a, b)]
        else:
            out += [("a" if piece == a else "b" if piece == b else "")] * len(piece)
    return out


def nominate_tables(blocks: Sequence[Mapping[str, Any]], witness: str | None) -> list[dict[str, Any]]:
    """Blocks the layout model did not call tables that engine B reads as one.

    A table read as running text comes out of each engine in its own order -
    one reads the records row by row, the other column by column - while
    prose is read in one order by both.  So a block of text (not furniture)
    long enough to hold two anchors, which witness_refusal - the block taken
    as one cell - finds engine B reading in another order, is nominated.
    Measured on the merge-mode pages of the corpus (3,305 blocks of 16 or
    more characters): 11 are nominated; 4 are tables (a ruled textbook
    table, two unruled rosters, a list of coin weights) and 7 are prose,
    several of them facsimile blocks that cross the fold between two
    leaves.  (An earlier count, before two books were added, had 0 prose
    blocks in 1,107.)  So a nomination is a question, not a verdict: the
    rebuild asks the page (rebuild_table), and the model may answer that it
    is not a table.  Ruled lines do not settle it either: measured on these
    crops, text columns and grey scans give interior rules as often as the
    tables do.  Order-conflict pages are not nominated at all: there
    the engines read the whole page in different orders, so one block read
    in another order says nothing about that block.
    """
    nominated = []
    for index, block in enumerate(blocks):
        text = CJK(str(block.get("text") or ""))
        if block.get("label") in FURNITURE_LABELS or len(text) < 2 * WITNESS_ANCHOR:
            continue
        why = witness_refusal(_tables.Table("markdown", 1, 1, [_tables.Cell(0, 0, 1, 1, text)]), witness)
        if why and "another order" in why:
            nominated.append({"block": index, "label": block.get("label"), "characters": len(text),
                              "signal": why.replace("row 1 column 1", "the block")})
    return nominated


def engine_b_as_settled(x: Mapping[str, Any], a_at: int, b_at: int,
                        streams: tuple[tuple[str, list[str]], tuple[str, list[str]]] | None,
                        marks: tuple[tuple[str, list[str]], tuple[str, list[str]], Mapping[str, Any]] | None,
                        ) -> str:
    """Engine B's side of one disagreement without the characters the page's
    rules take for engine A's printed marks: the bracket rule's numerals
    (bracket_explanation, when STREAMS - engine_gaps() of each page - are
    given) and the mark rule's glyphs (decision D1, mark_explanation, when
    MARKS - engine_marks() of each page and the book's mark_aliases - are).
    What is left is a subsequence of engine B's reading.  A circle taken for
    a mark is kept: circle_withheld may keep it as a zero."""
    b = x["b"]
    if x["tag"] == "equal" or not b or x.get(LOOPED_READING) or "window" in x:
        return b
    seg = x
    if streams is not None and is_numeral_conflict(dict(seg)):
        found = bracket_explanation(seg, a_at, b_at, *streams)
        if found and found[0] == "A":
            return found[1]
        if found and found[0] == "reduced":
            # The walk asks about what the brackets leave, and the mark rule
            # finds nothing on a reduced reading (its characters are no longer
            # engine B's at B_AT), as here.
            return found[1]["b"]
    if marks is not None:
        found = mark_explanation(seg, a_at, b_at, *marks)
        if found and found[3] == "B" and not any(char in CIRCLE_CHARS for char, _ in found[2]):
            return found[1] if found[0] in ("A", "B") else found[1]["b"]
    return b


def marks_left_out(segments: Sequence[Mapping[str, Any]], starts: Sequence[int], b_starts: Sequence[int],
                   chars_b: str, settled: Sequence[str]) -> tuple[str, int]:
    """Engine B's reading (CHARS_B, its characters) with the characters the
    page's rules take for engine A's printed marks left out (SETTLED, each
    segment's engine-B side as engine_b_as_settled leaves it), and how many
    were left out: the witness nominate_tables reads a block against
    (decision D21).

    A witness piece is WITNESS_ANCHOR characters engine B read without a
    break, and a glyph engine B writes for a printed mark (丶 for a full-cell
    ，) breaks every piece it falls in.  Measured on a magazine's price table
    printed in a ruled box, which the layout model called text: engine B
    wrote the comma in its nine-character note as 丶, so the note held no
    piece and the block one anchor where two in the other order make a
    nomination, and the table was not nominated - on a page where the mark
    rule settled 丶 as a mark 33 times.  Only this page's settled sites are left out,
    never a list of glyphs."""
    drop = [False] * len(chars_b)
    for x, b_at, kept in zip(segments, b_starts, settled):
        if kept == x["b"] or chars_b[b_at:b_at + len(x["b"])] != x["b"]:
            continue
        # What the rules keep of engine B's reading is a subsequence of it;
        # the rest is what they took for marks.
        j = 0
        for k, char in enumerate(x["b"]):
            if j < len(kept) and kept[j] == char:
                j += 1
            else:
                drop[b_at + k] = True
    return "".join(char for char, gone in zip(chars_b, drop) if not gone), sum(drop)


def settle_by_union(segments: Sequence[Mapping[str, Any]], indices: Sequence[int],
                    elsewhere: str = "") -> dict[int, str]:
    """The pieces for a block's order disagreements: moved text once.

    Where engine B reads a block in another order, what the alignment calls
    a disagreement there is mostly text the two engines put in different
    places: a run deleted here is the run inserted there.  Settled one by one
    (a preference or 裁決 per disagreement) that text is written twice, or
    dropped where 裁決 answers "nothing here" - measured on a missed table,
    裁決 deleted runs both engines read (a book title with its figures, and
    part of a header).

    Text counts as moved only by runs.  A run engine B read in the block (an
    insertion, or its side of a replacement) is engine A's text when it
    stands whole in engine A's side of these disagreements, or by pieces of
    WITNESS_ANCHOR or more (variants folded; each stretch of engine A's is
    matched once, engine B's longest runs first).  And engine A's run only
    stands for text engine B read in the block when engine B did not read it
    `elsewhere` - engine B's reading of the page outside the block - whole
    or by such a piece.  Characters are not matched one by one: pooled that
    way, a genuine insertion lost every character that also occurred in an
    unrelated run of engine A whose own engine-B copy the alignment had put
    outside the block (measured on a roster: a seven-character post engine
    A missed kept four characters, the other three taken by a name and post
    of engine A's that engine B read at the running head), and 裁決 was
    never asked.

    Every disagreement settled here keeps engine A's reading in place, and
    the part of engine B's run not matched is added after it.  A run of
    engine B's with nothing matched is not shown to be moved text: it is
    left to the usual way (a preference or 裁決) when engine A read nothing
    there, or when none of engine A's text there was matched either.
    Returns the piece of each disagreement settled here; the ones left to
    the usual way are not in it.
    """
    far = variant_fold(elsewhere)
    far_matcher = difflib.SequenceMatcher(None, "", far, autojunk=False)
    owner: list[int | None] = []
    pool: list[str] = []
    for i in indices:
        text = variant_fold(segments[i]["a"])
        if not text or (far and (text in far or witness_anchors(text, far, far_matcher, unique=False))):
            continue
        pool += list(text) + ["\0"]
        owner += [i] * len(text) + [None]
    matched_a: set[int | None] = set()
    held: dict[int, list[bool]] = {}
    matcher = difflib.SequenceMatcher(None, "", "", autojunk=False)

    def take(b_at: int, a_at: int, size: int, flags: list[bool]) -> None:
        flags[b_at:b_at + size] = [True] * size
        pool[a_at:a_at + size] = ["\0"] * size
        matched_a.update(owner[a_at:a_at + size])

    for i in sorted((i for i in indices if segments[i]["b"]), key=lambda i: (-len(segments[i]["b"]), i)):
        run = variant_fold(segments[i]["b"])
        flags = held[i] = [False] * len(run)
        at = "".join(pool).find(run)
        if at >= 0:
            take(0, at, len(run), flags)
            continue
        probe = list(run)
        while True:
            matcher.set_seqs("".join(probe), "".join(pool))
            match = matcher.find_longest_match(0, len(probe), 0, len(pool))
            if match.size < WITNESS_ANCHOR:
                break
            probe[match.a:match.a + match.size] = ["\1"] * match.size
            take(match.a, match.b, match.size, flags)
    pieces = {}
    for i in indices:
        a, b = segments[i]["a"], segments[i]["b"]
        flags = held.get(i, [])
        if b and not any(flags) and (not a or i not in matched_a):
            continue
        pieces[i] = a + "".join(char for char, flag in zip(b, flags) if not flag)
    return pieces


def engine_b_outside(segments: Sequence[Mapping[str, Any]], starts: Sequence[int],
                     b_starts: Sequence[int], lo: int, hi: int) -> str:
    """Engine B's reading of the page outside the block at [LO, HI) of engine
    A's stream, its stretches apart by "\0": settle_by_union's `elsewhere`.

    The block's own stretch of engine B is what its segments hold there - the
    part of an agreed segment inside the block, and each disagreement inside
    it - and engine B's text is laid out in engine B's order, each segment at
    its B_STARTS.  Without text placed at engine A's site (place_moved_once)
    that is the segments' order, and the block's stretch one piece of it.  With
    it, a run engine B read elsewhere and D2 placed in the block is where
    engine B read it: marked as the block's there, and the text between left
    outside; and a run D2 took out of the block's stretch to another site
    leaves nothing in the stretch.  (Joining the segments' engine-B text in
    their order, as this was first done, cut engine B's reading at engine B
    positions of a text in another order.)  Engine B's reading of a set-aside
    looped block (quarantine_looped_block) is that block's, outside this one.
    """
    total = max((b + len(x["b"]) for x, b in zip(segments, b_starts)), default=0)
    text = [""] * total
    for x, b in zip(segments, b_starts):
        text[b:b + len(x["b"])] = list(x["b"])
    taken = [False] * total
    cuts: set[int] = set()
    for x, a_at, b_at in zip(segments, starts, b_starts):
        if x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if x["tag"] == "equal":
            if not (a_at < hi and a_end > lo):
                continue
            first, last = b_at + max(lo, a_at) - a_at, b_at + min(hi, a_end) - a_at
        elif (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi):
            first, last = b_at, b_at + len(x["b"])
        else:
            continue
        taken[first:last] = [True] * (last - first)
        cuts.add(first)
    out: list[str] = []
    for p in range(total + 1):
        if (p in cuts or (p < total and taken[p])) and (not out or out[-1] != "\0"):
            out.append("\0")
        if p < total and not taken[p]:
            out.append(text[p])
    return "".join(out)


def window_merge(block: Mapping[str, Any], witness: str, heads: Sequence[str] = (),
                 furniture: frozenset[str] | set[str] = frozenset(),
                 elsewhere: str = "") -> list[dict[str, Any]] | None:
    """Engine A's refused table block, read against engine B's lines, as segments.

    On an order-conflict page the whole page is engine A's reading, and a
    table block of it that engine B contradicts (witness_refusal: a cell
    spliced or copied; furniture_row: the running head as a row) went into
    the page as the spliced text the refusal had rejected.  Measured on a
    textbook table's continuation page: engine A's grid carried the running
    head as a row and one twenty-character run three times, while engine B
    read the notes once each, in print order, and read the page's rare
    form of one character as another rare codepoint where A has a common
    character.

    So the block is rebuilt from engine B's lines: every line of B that
    shares a piece of WITNESS_ANCHOR or more with the block (or lies between
    two that do) is aligned with the stretch of engine A its anchors fall in
    - a window - and the window's disagreements become segments like any in
    merge mode, so a substitution there (a numeral, a rare codepoint) reaches
    裁決 or the preference.  Engine A's text outside every window is kept
    when engine B read no piece of it (labels and figures B never read) and
    dropped when B read it in another window (a copy); the furniture row is
    dropped.  Returns the segments, each character of engine A's block in
    exactly one of them (so the page's positions in A's stream hold), the
    windows in engine B's order and the rest after them in A's; or None
    when the block is not a grid whose cells account for its text, or B
    has no line in it.  `elsewhere` is engine A's reading of the rest of
    the page (CJK, the parts apart by "\\0"), which says where a line of
    engine B's runs on out of the block.
    """
    grid = _tables.parse_html_table(str(block.get("text") or ""), source_breaks=True)
    if grid is None:
        return None
    grid = _tables.simplify(grid)
    a_text = CJK(str(block.get("text") or ""))
    if "".join(CJK(cell.text) for cell in grid.cells) != a_text:
        return None
    head = furniture_row(grid, heads, furniture)
    in_head = [cell.row == head for cell in grid.cells for _ in CJK(cell.text)]
    body_at = [i for i, flag in enumerate(in_head) if not flag]
    owner = [n for n, cell in enumerate(grid.cells) for _ in CJK(cell.text)]
    body_cell = [owner[i] for i in body_at]
    cell_positions: dict[int, list[int]] = {}
    for position, cell in enumerate(body_cell):
        cell_positions.setdefault(cell, []).append(position)
    body = "".join(a_text[i] for i in body_at)
    lines = [CJK(line) for line in (witness or "").splitlines()]
    lines = [line for line in lines if line]
    # Anchors are found on the variant-folded texts (same lengths, so the
    # offsets hold for the texts as read).
    folded_body = variant_fold(body)
    anchors = [witness_anchors(variant_fold(line), folded_body, unique=False)
               if len(line) >= WITNESS_ANCHOR else [] for line in lines]
    anchored = [i for i, found in enumerate(anchors) if found]
    if not anchored:
        return None
    first, last = anchored[0], anchored[-1]
    claimed = [False] * len(body)
    windows: list[dict[str, Any]] = []
    far = variant_fold(elsewhere)
    far_matcher = difflib.SequenceMatcher(None, "", far, autojunk=False)
    for i in range(first, last + 1):
        line = lines[i]
        runs: list[list[tuple[int, int, int]]] = []
        for anchor in anchors[i]:
            if runs and anchor[1] > runs[-1][-1][1]:
                runs[-1].append(anchor)
            else:
                runs.append([anchor])
        if not runs:
            windows.append({"b": line, "lo": None, "hi": None})
            continue
        # Engine B writes a whole table as one line, with whatever it read
        # next to it (a caption, the next table, the prose after it).  Past
        # the line's outermost anchors, a piece engine A read in another
        # block of the page is that block's text: the line is cut after the
        # last such piece before its first anchor and before the first one
        # after its last anchor.  Measured on a year table whose slices ran
        # to the line's ends: the sentence after the table and the whole of
        # the next table were written into it, each a second time.  What
        # engine B read there that engine A read nowhere stays, as before.
        # (Tried on made-up year tables: cutting at the outermost anchors'
        # own cells dropped the first or last cell of a line wherever engine
        # A had read that cell as another.)
        head, tail = 0, len(line)
        if far:
            start_at, end_at = runs[0][0][0], runs[-1][-1][0] + runs[-1][-1][2]
            before = witness_anchors(variant_fold(line[:start_at]), far, far_matcher, unique=False)
            after = witness_anchors(variant_fold(line[end_at:]), far, far_matcher, unique=False)
            head = max((offset + size for offset, _, size in before), default=0)
            tail = end_at + min((offset for offset, _, _ in after), default=len(line) - end_at)
        # A line whose anchors go backwards in engine A crosses a splice:
        # each forward run is its own window, cut halfway between runs.
        cuts = [head] + [(r[-1][0] + r[-1][2] + n[0][0]) // 2 for r, n in zip(runs, runs[1:])] + [tail]
        for run, lo_b, hi_b in zip(runs, cuts, cuts[1:]):
            lo = max(0, run[0][1] - (run[0][0] - lo_b))
            hi = min(len(body), run[-1][1] + run[-1][2] + (hi_b - run[-1][0] - run[-1][2]))
            while lo < hi and claimed[lo]:
                lo += 1
            while hi > lo and claimed[hi - 1]:
                hi -= 1
            if lo >= hi or any(claimed[lo:hi]):
                # Engine A's stretch for it is another window's already:
                # engine B read that text again, or anchored on text engine
                # A has in another window.  Tested as a copy below.
                windows.append({"b": line[lo_b:hi_b], "lo": None, "hi": None, "claimed": True})
                continue
            for k in range(lo, hi):
                claimed[k] = True
            windows.append({"b": line[lo_b:hi_b], "lo": lo, "hi": hi})
        for piece in (line[:head], line[tail:]):
            if piece:
                windows.append({"b": piece, "lo": None, "hi": None, "outside": True})
    # Engine B's reading of the block: the lines' text the windows take.
    stretch = "\0".join(window["b"] for window in windows if not window.get("outside"))

    def seg(tag: str, a: str, b: str, at: int, settled: tuple[str, str] | None = None) -> dict[str, Any]:
        out = {"tag": tag, "a": a, "b": b, "before": body[max(0, at - 8):at],
               "after": body[at + len(a):at + len(a) + 8]}
        if settled is not None:
            out["settled"] = settled
        return out

    segments: list[dict[str, Any]] = []
    # The windows' engine-A text, and where each of its characters is in the
    # block, for the slices tested as copies.
    held_text, held_at = "", []
    for w in windows:
        if w["lo"] is not None:
            held_text += variant_fold(body[w["lo"]:w["hi"]]) + "\0"
            held_at += list(range(w["lo"], w["hi"])) + [-1]
    folded_stretch = variant_fold(stretch)
    written_again: Counter = Counter()
    for window in windows:
        if window.get("outside"):
            segments.append(seg("insert", "", window["b"], 0, ("", "engine-B-beyond-the-block-windows")))
            continue
        if window.get("claimed"):
            # A slice whose stretch of engine A is another window's.  A
            # piece of it (WITNESS_ANCHOR or more) found in the windows'
            # engine-A text is a copy as far as it covers whole cells there
            # - a cell is one printed box, and a year table repeats its
            # words from cell to cell, so a piece that joins the end of one
            # figure, a year and the start of another is no copy - and only
            # while the windows hold it at least as often as engine B read
            # it (each as often as the engine that read it more often).  A
            # stretch shorter than an anchor between two copied pieces is
            # part of the copy (a character one reading has as another);
            # the rest stands as engine B read it.  Measured: a year table's
            # slice anchored on another window's stretch was written whole,
            # and a figure engine A holds in that window came out twice.
            b = window["b"]
            folded = variant_fold(b)
            copied = [False] * len(b)
            for offset, at, size in witness_anchors(folded, held_text, unique=False):
                where = held_at[at:at + size]
                inside = set(where)
                whole = [p >= 0 and all(q in inside for q in cell_positions[body_cell[p]]) for p in where]
                t = 0
                while t < size:
                    u = t
                    while u < size and whole[u] == whole[t]:
                        u += 1
                    piece = folded[offset + t:offset + u]
                    if whole[t] and held_text.count(piece) + written_again[piece] >= folded_stretch.count(piece):
                        copied[offset + t:offset + u] = [True] * (u - t)
                    elif whole[t]:
                        written_again[piece] += 1
                    t = u
            j = 0
            while j < len(b):
                stop = j
                while stop < len(b) and copied[stop] == copied[j]:
                    stop += 1
                if not copied[j] and 0 < j and stop < len(b) and stop - j < WITNESS_ANCHOR:
                    copied[j:stop] = [True] * (stop - j)
                j = stop
            start = 0
            for j in range(1, len(b) + 1):
                if j == len(b) or copied[j] != copied[start]:
                    piece = b[start:j]
                    segments.append(seg("insert", "", piece, 0, (
                        ("", "engine-B-copy-of-a-window") if copied[start]
                        else (piece, "engine-B-line-in-table-block"))))
                    start = j
            continue
        if window["lo"] is None:
            # A line of engine B inside the block's stretch that anchors
            # nowhere in engine A: B's reading stands (engine A's own text
            # for it, if any, is tested as a copy below).
            segments.append(seg("insert", "", window["b"], 0,
                                (window["b"], "engine-B-line-in-table-block")))
            continue
        lo, hi = window["lo"], window["hi"]
        a = body[lo:hi]
        # Numerals fused per window, as align_drafts' output is for the page.
        segments.extend(fuse_numeral_runs([
            seg(tag, a[i1:i2], window["b"][j1:j2], lo + i1)
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, window["b"], autojunk=False).get_opcodes()]))
    # A window's edge is projected from its anchors, so where the two
    # readings differ in length there one character can fall to the wrong
    # side: the same text deleted at the end of one window and inserted at
    # the start of the next is one character both read.
    merged: list[dict[str, Any]] = []
    for item in segments:
        if (merged and item["tag"] == "insert" and "settled" not in item
                and merged[-1]["tag"] == "delete" and merged[-1]["a"] == item["b"]):
            merged[-1] = dict(merged[-1], tag="equal", b=item["b"])
            continue
        merged.append(item)
    segments = merged
    # Engine A outside every window: a piece engine B read elsewhere is a
    # copy; what B read no piece of is engine A's alone and stays.
    matcher = difflib.SequenceMatcher(None, "", variant_fold(stretch), autojunk=False)
    k = 0
    while k < len(body):
        if claimed[k]:
            k += 1
            continue
        end = k
        while end < len(body) and not claimed[end]:
            end += 1
        run = body[k:end]
        copied = [False] * len(run)
        for offset, _, size in witness_anchors(variant_fold(run), variant_fold(stretch), matcher,
                                               unique=False):
            copied[offset:offset + size] = [True] * size
        # A stretch too short to anchor, inside a cell engine B read elsewhere,
        # is part of that copy: a character the copy misread between two
        # copied pieces (a character engine B read as another), or a piece the windows
        # already hold as often as engine B read it.  A whole cell is never
        # taken this way - a label engine B did not read stays.
        j = 0
        while j < len(run):
            if copied[j]:
                j += 1
                continue
            stop = j
            while stop < len(run) and not copied[stop]:
                stop += 1
            cells = {body_cell[k + x] for x in range(j, stop)}
            others = [q for q in cell_positions[next(iter(cells))] if not k + j <= q < k + stop] \
                if len(cells) == 1 else []
            if any(claimed[q] or (k <= q < end and copied[q - k]) for q in others):
                piece = variant_fold(run[j:stop])
                read = variant_fold(stretch).count(piece)
                between = 0 < j and stop < len(run) and stop - j < WITNESS_ANCHOR
                held = sum(variant_fold(body[w["lo"]:w["hi"]]).count(piece)
                           for w in windows if w["lo"] is not None)
                if between or (read and held >= read):
                    copied[j:stop] = [True] * (stop - j)
            j = stop
        start = 0
        for j in range(1, len(run) + 1):
            if j == len(run) or copied[j] != copied[start]:
                piece = run[start:j]
                segments.append(seg("delete" if copied[start] else "equal", piece, "", k + start,
                                    ("", "copy-engine-B-read-once") if copied[start] else None))
                start = j
        k = end
    head_text = "".join(a_text[i] for i, flag in enumerate(in_head) if flag)
    if head_text:
        segments.append({"tag": "delete", "a": head_text, "b": "", "before": "", "after": "",
                         "settled": ("", "table-furniture-row")})
    return segments


REBUILD_SYSTEM = """你係 jyut-ocr-table-builder。畀你一個版面區域嘅裁圖，同埋兩個 OCR 引擎喺呢個區域讀到嘅字。
兩個引擎讀表格會跳格、搬格，所以啲字嘅**次序可能亂咗**，但字本身已經核對過。

先睇裁圖：呢個區域係咪一個表格（有欄有行、一格格嘅資料，有冇格線都算）？
- 如果唔係表格（係一段段嘅文字、一條條直行嘅正文），淨係答：唔係表格
- 如果係表格，對住裁圖將佢寫出嚟：
  1. 由裁圖重建物理格網：上至下係行、左至右係欄，記清楚邊啲格合併咗。
     本步唔轉置、唔倒序：下一步按頁面印刷方向機械轉換（直排表由右至左嘅直欄變成由上至下嘅行）。
     唔好按內容或者合計方向自行重排。
  2. 冇合併格寫 Markdown 表；有合併格（一格跨幾行或者幾欄）寫 HTML `<table>`，用 rowspan／colspan，
     唔好拆格、補空格或者將同一段字抄入幾格。
  3. **字淨係可以用畀你嗰啲**：每個字最多用佢喺下面出現嘅次數，可以按裁圖重新排次序，
     但唔准加任何其他漢字，唔准改字、唔准正規化異體字。下面嘅字應該差唔多全部用上，唔好漏。
  4. 圖上印咗嘅括號、頓號（「、」）等照圖加。

輸出：淨係輸出個表（或者「唔係表格」），唔好解釋，唔好 code fence。"""

REBUILD_AS_PRINTED_SYSTEM = """你係 jyut-ocr-table-builder。畀你一個版面區域嘅裁圖，同埋兩個 OCR 引擎喺呢個區域讀到嘅字。
兩個引擎讀表格會跳格、搬格，所以啲字嘅**次序可能亂咗**，但字本身已經核對過。

先睇裁圖：呢個區域係咪一個表格（有欄有行、一格格嘅資料，有冇格線都算）？
- 如果唔係表格（係一段段嘅文字、一條條直行嘅正文），淨係答：唔係表格
- 如果係表格，對住裁圖將佢寫出嚟：
  1. 由裁圖判斷結構：幾多行、幾多欄、邊啲格合併咗。**照版面印出嚟嘅橫行同直欄原樣排格網**（由上至下係行、由左至右係欄），唔好將直欄轉置成橫行。
  2. 冇合併格寫 Markdown 表；有合併格（一格跨幾行或者幾欄）寫 HTML `<table>`，用 rowspan／colspan，
     唔好拆格、補空格或者將同一段字抄入幾格。
  3. **字淨係可以用畀你嗰啲**：每個字最多用佢喺下面出現嘅次數，可以按裁圖重新排次序，
     但唔准加任何其他漢字，唔准改字、唔准正規化異體字。下面嘅字應該差唔多全部用上，唔好漏。
  4. 圖上印咗嘅括號、頓號（「、」）等照圖加。

輸出：淨係輸出個表（或者「唔係表格」），唔好解釋，唔好 code fence。"""

NOT_A_TABLE = "唔係表格"
# What 裁決 is told when asked about a character a rebuilt table wrote that
# no engine read (confirm_unread): 甲 is engine A's reading there (nothing).
UNREAD_NOTE = ("呢個位係一個表格：乙係重建表格嗰陣寫低嘅字，兩個 OCR 引擎都冇讀到佢（所以甲冇字）。"
               "睇清楚嗰個位有冇印字、印嘅係乜字；冇印字就寫 `定案：無`。")


def check_rebuilt_table(answer: str, allowed: Counter, required: Counter,
                        source: str, unread: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """A rebuilt table, or why it cannot stand.

    `allowed` is what may appear (every character either engine read in the
    region, each as often as the engine that read it more often); `required`
    what must (the characters both engines read there).  One table, at
    least TABLE_MINIMUM_ROWS rows holding text, no character beyond
    `allowed` - after the mechanical repair of a variant swap against
    `source`, the region's text - and none of `required` left out.

    UNREAD, when given, is filled when characters beyond `allowed` are all
    that refuse the table (nothing both engines read left out): the table
    ("markup"), those characters as the answer wrote them ("characters"),
    and what its reason would be without them ("accepted") - for
    confirm_unread, which may ask 裁決 about them.
    """
    answer = strip_fence(answer or "").strip()
    tables = _tables.find_tables(answer)
    if not tables:
        return None, ("not a table (the model, on the crop)" if NOT_A_TABLE in answer
                      else "no table in the answer")
    if len(tables) > 1:
        return None, f"{len(tables)} tables in the answer, one region"
    table = _tables.simplify(tables[0])
    rows = len({cell.row for cell in table.cells if CJK(cell.text)})
    if rows < TABLE_MINIMUM_ROWS:
        return None, f"{rows} row(s) holding text"
    markup = _tables.render(table)
    invented = Counter(CJK(markup)) - allowed
    if invented:
        repaired = restore_merged_characters(source, markup)
        if repaired is not None and not Counter(CJK(repaired)) - allowed:
            markup, invented = repaired, Counter()
    # An acceptable variant of a character an engine read is that character
    # (variant_fold: the user's list, then OpenCC's).  Measured on a textbook
    # table: the rebuild's retry was refused, and the table lost, for writing
    # 算 where engine B read the acceptable 𮅕 (engine A 筍); a region rebuild
    # for 冊 where the engines read 册.  The answer's forms are kept: which
    # form each site prints is no count's to say.
    folded = False
    if invented:
        invented = Counter(variant_fold(CJK(markup))) - Counter(variant_fold("".join(allowed.elements())))
        folded = not invented
    lost = Counter(variant_fold("".join(required.elements()))) - Counter(variant_fold(CJK(markup)))
    if invented:
        named = fold_named(invented, CJK(markup), "".join(allowed.elements()))
        if unread is not None and not lost:
            unread.update({"markup": markup, "characters": named,
                           "accepted": f"{table.rows} rows x {table.cols} columns"})
        return None, "characters neither engine read here: " + "".join(sorted(named))[:30]
    if lost:
        return None, ("characters both engines read here left out: "
                      + "".join(sorted(fold_named(lost, "".join(required.elements()), CJK(markup))))[:30])
    return markup, f"{table.rows} rows x {table.cols} columns" + ("; acceptable variants folded" if folded else "")


def fold_named(excess: Counter, written: str, held: str) -> str:
    """The characters of WRITTEN that EXCESS - counts by variant_fold
    representative, what WRITTEN has beyond HELD - stands for, in WRITTEN's
    own forms, in its order: of each class, the forms WRITTEN has more often
    than HELD has them as written.

    A refusal names what the answer wrote (or, for characters left out, what
    the engines read), never the class's representative.  Measured: a
    rebuild refused for copying a note into three rows was told the lock's
    representatives - five characters every engine had read in their printed
    forms, named in the other forms the fold picks for their classes - and
    its retry wrote those forms into the table."""
    beyond = Counter(written) - Counter(held)
    need = Counter(excess)
    out = []
    for char in written:
        key = variant_fold(char)
        if need[key] > 0 and beyond[char] > 0:
            out.append(char)
            need[key] -= 1
            beyond[char] -= 1
    return "".join(out)


# How much of a neighbour's text engine B must have read inside a nominated
# block's region before the rebuild takes it in (rebuild_region,
# region_witness): most of it.  Measured on the neighbours of the corpus's 12
# nominations that engine B does not read in engine A's order: the blocks of
# a table the layout split off hold 59%, 76% and 95% of their text in engine
# B's reading of the region (a coin table's block, a textbook table's header
# column, a price table's other half); the fourth, two characters engine A
# read a second time as a block of their own - engine B read them once, with
# the block beside it - 0%.  A block engine B skipped has none.
REGION_WITNESS_SHARE = 0.5


def region_witness(blocks: Sequence[Mapping[str, Any]], segments: Sequence[Mapping[str, Any]],
                   starts: Sequence[int], k: int, j: int) -> str:
    """The characters of block J's text (engine A's reading) that engine B
    read inside the union of blocks K and J beyond block K's own text - its
    reading of the two blocks' stretches (engine_b_inside, on SEGMENTS as the
    merge placed them: text engine B read in another order and D2 placed at
    engine A's site counts), less engine A's text of K.  In J's order.

    Engine B reading the union of a table's two blocks reads both; engine B
    that skipped J, or read J somewhere outside the union, leaves nothing
    of J beyond K's own text.  Two stretches that meet are one, so a
    disagreement across the blocks' boundary is inside the union."""
    ends = list(itertools.accumulate(len(CJK(str(b.get("text") or ""))) for b in blocks))
    stretches: list[list[int]] = []
    for lo, hi in sorted(((ends[i - 1] if i else 0), ends[i]) for i in (k, j)):
        if stretches and stretches[-1][1] == lo:
            stretches[-1][1] = hi
        else:
            stretches.append([lo, hi])
    read = Counter(CJK("".join(engine_b_inside(segments, starts, lo, hi) for lo, hi in stretches)))
    beyond = read - Counter(CJK(str(blocks[k].get("text") or "")))
    out = []
    for ch in CJK(str(blocks[j].get("text") or "")):
        if beyond[ch] > 0:
            out.append(ch)
            beyond[ch] -= 1
    return "".join(out)


def rebuild_region(blocks: Sequence[Mapping[str, Any]], k: int, size: tuple[int, int],
                   agreed: Sequence[str], witness: Callable[[int], str],
                   nominated: Collection[int] = ()) -> list[int]:
    """The other blocks a nominated block's rebuild takes in: each block of
    text (not furniture, not NOMINATED itself) whose
    box overlaps block K's or lies within one glyph pitch of it, whose top or
    bottom edge is within one glyph pitch of K's, whose own text engine B
    does not read in engine A's order - AGREED (agreed_block_text, on the
    alignment before D2 places text read in another order once) holds less
    of it than the share the page's order test takes as reading order
    (READING_ORDER_AGREEMENT) - and most of whose text engine B did read
    inside the region: WITNESS(j) (region_witness) holds more of it than
    REGION_WITNESS_SHARE.  The pitch is block K's: its box over its
    characters, in the render's pixels (SIZE).  Returns positions in BLOCKS,
    in order.

    Not read in engine A's order is no evidence on its own: a title or
    caption beside the table that engine B skipped agrees on nothing either,
    and was taken in and left the page with the table.  A block nominated
    itself is a table of its own: two adjacent tables asked as one region
    answered two tables, refused.

    A table the layout model called text can come back as several blocks:
    its header column as a block of its own, or its halves either side of a
    ruled line.  Rebuilt from the nominated block alone, the table has no
    header row, and the rest of its text stays beside it as prose.
    Measured on every nomination of the corpus's 692 merge-mode pages (12
    blocks; 16 neighbours overlap one or lie within a pitch): the rule takes
    a textbook table's header column (overlapping, the same top edge, 47% of
    it agreed), the other half of a ruled price table (0.12 pitch apart, its
    edges within 0.12 pitch, 67% agreed before D2, 95% after, D2 having
    placed its runs read in another order once) and two blocks of a coin
    table (0% and 47% agreed).  The neighbours of the prose nominations
    share an edge too - columns of one leaf - but both engines read them in
    one order (98-100% agreed), so no prose nomination takes a block; a
    section heading engine B read elsewhere (0%) shares no edge.  Two more
    blocks of the coin table (79%, 80%) are not taken: the region is grown
    only where the evidence is plain.  Of the coin table's two, the 0% block
    is engine A's second reading of two characters engine B read once, with
    the block beside it: it has no witness and is no longer taken.  No
    neighbour of the twelve is nominated itself."""
    if not blocks or not (0 <= k < len(blocks)):
        return []
    width, height = size
    x, y, w, h = blocks[k]["box"]
    count = len(CJK(str(blocks[k].get("text") or "")))
    if not count or w <= 0 or h <= 0:
        return []
    pitch = ((w * width) * (h * height) / count) ** 0.5
    taken = []
    for j, other in enumerate(blocks):
        text = CJK(str(other.get("text") or ""))
        if j == k or not text or other.get("label") in FURNITURE_LABELS or other.get("quarantined"):
            continue
        ox, oy, ow, oh = other["box"]
        apart_x = max(ox - (x + w), x - (ox + ow), 0.0) * width
        apart_y = max(oy - (y + h), y - (oy + oh), 0.0) * height
        if apart_x >= pitch or apart_y >= pitch:
            continue
        if min(abs(oy - y), abs((oy + oh) - (y + h))) * height >= pitch:
            continue
        if j < len(agreed) and len(agreed[j]) >= READING_ORDER_AGREEMENT * len(text):
            continue
        if j in nominated:
            continue
        if len(witness(j)) <= REGION_WITNESS_SHARE * len(text):
            continue
        taken.append(j)
    return taken


def engine_b_inside(segments: Sequence[Mapping[str, Any]], starts: Sequence[int], lo: int, hi: int) -> str:
    """Engine B's reading of engine A's stretch [LO, HI) - a block: the part of
    each agreed segment inside it and the engine-B side of each disagreement
    inside it (block_union's test), in the segments' order.  Engine B's
    reading of a set-aside looped block is that block's (engine_b_outside)."""
    out: list[str] = []
    for x, a_at in zip(segments, starts):
        if x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if x["tag"] == "equal":
            if a_at < hi and a_end > lo and len(x["b"]) == len(x["a"]):
                out.append(x["b"][max(lo, a_at) - a_at:min(hi, a_end) - a_at])
        elif (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi):
            out.append(x["b"])
    return "".join(out)


def unwritten_readings(segments: Sequence[Mapping[str, Any]], starts: Sequence[int],
                       pieces: Sequence[str], lo: int, hi: int) -> list[tuple[str, str]]:
    """The engines' readings of each disagreement inside engine A's stretch
    [LO, HI) that the merge did not write as read, as (engine, reading):
    engine B's side, then engine A's, in the segments' order."""
    out: list[tuple[str, str]] = []
    for x, a_at, piece in zip(segments, starts, pieces):
        if x["tag"] == "equal" or x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if not ((lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi)):
            continue
        out += [(engine, text) for engine, text in (("B", x["b"]), ("A", x["a"])) if text and text != piece]
    return out


def rebuild_lock(merged: str, engine_a: str, engine_b: str,
                 unwritten: Sequence[tuple[str, str]] = ()) -> tuple[str, list[tuple[str, str]]]:
    """The characters a rebuilt table of a region may hold, as the text the
    rebuild is shown: MERGED (the merge's text of the region) and, each on a
    line of its own, runs of the characters engine A (ENGINE_A, its reading
    of the region) or engine B (ENGINE_B, its reading inside the region,
    engine_b_inside) read there more often than the merge wrote them - so
    each character stands at the highest of its counts in the three
    (check_rebuilt_table's "every character either engine read in the
    region, each as often as the engine that read it more often"; the
    merge's own count too, for a character 裁決 read that neither engine
    did).  The runs are cut from the disagreements' unwritten readings
    (UNWRITTEN, unwritten_readings) first, where the characters stand in the
    order an engine read them, then from the engines' readings.  Returns the
    text and the runs added, each with the engine it came from.

    The merge's text alone was the lock.  Measured on a ruled textbook
    table nominated as a missed table: engine B read a two-digit figure cell
    that the alignment paired with another cell of engine A's, a one-digit
    figure; 裁決, shown engine A's site, kept engine A's, and the merge
    dropped engine B's two digits.  The one
    rebuild (of four runs) that wrote the figure the page prints was refused
    as inventing it, and its retry too; a person's transcription of the
    table's records is refused the same way under the merge's text, and
    passes under this lock."""
    deficit = (Counter(CJK(merged)) | Counter(CJK(engine_a)) | Counter(CJK(engine_b))) - Counter(CJK(merged))
    runs: list[tuple[str, str]] = []
    for engine, source in [*unwritten, ("A", engine_a), ("B", engine_b)]:
        run = ""
        for char in CJK(source) + "\0":
            if char != "\0" and deficit[char] > 0:
                deficit[char] -= 1
                run += char
            elif run:
                runs.append((engine, run))
                run = ""
    return merged + "".join("\n" + run for _, run in runs), runs


# How much of a refused rebuild answer the seal keeps (a table of a few hundred cells fits).
REFUSED_ANSWER_LIMIT = 20000


# How many characters no engine read a rebuilt table may write, each
# confirmed by 裁決 on a crop of its own spot (confirm_unread): the user's
# ruling of 2026-09-27 (round-2 register, headerCharacters) - "a table rebuild
# may add at most 2 characters no engine read, each confirmed by 裁決 on its
# own crop and recorded as a doubt".
UNREAD_TABLE_LIMIT = 2


def unread_sites(markup: str, characters: str) -> list[tuple[str, str, int]]:
    """Every place in a rebuilt table's cells holding a character of the
    variant classes of CHARACTERS (characters no engine read): (the
    character as written, its cell's text, its offset in the cell's CJK), in
    reading order.  Every place of such a class, not only the ones beyond
    the lock: which of several is the one no engine read no count says."""
    table = _tables.find_tables(markup)
    classes = {variant_fold(char) for char in CJK(characters)}
    out = []
    for cell in (table[0].cells if table else []):
        text = CJK(cell.text)
        out += [(char, text, at) for at, char in enumerate(text) if variant_fold(char) in classes]
    return out


def unread_spot(texts: Sequence[str], cell: str, at: int) -> tuple[int, int, str, str] | None:
    """Where the character a rebuilt table wrote at offset AT of CELL (one no
    engine read) stands in engine A's reading of the region's blocks (TEXTS,
    each a block's CJK): (the block, the position in its text the character
    would take, the cell's text before it and after it, as far as engine A
    read it there).  The spot is fixed by the longest stretch of the cell
    just before the character, or just after it, that engine A read exactly
    once in the region - one character will do, a stretch read twice or not
    at all says nothing.  None when no stretch of the cell around it does."""
    cell = CJK(cell)
    before, after = cell[:at], cell[at + 1:]
    found: list[tuple[int, int, int, str, str]] = []
    for size in range(len(before), 0, -1):
        piece = before[-size:]
        hits = [(j, text.find(piece)) for j, text in enumerate(texts) if piece in text]
        if sum(text.count(piece) for text in texts) == 1:
            j, p = hits[0]
            found.append((size, j, p + size, piece, texts[j][p + size:p + size + 4]))
            break
    for size in range(len(after), 0, -1):
        piece = after[:size]
        hits = [(j, text.find(piece)) for j, text in enumerate(texts) if piece in text]
        if sum(text.count(piece) for text in texts) == 1:
            j, p = hits[0]
            found.append((size, j, p, texts[j][max(0, p - 4):p], piece))
            break
    if not found:
        return None
    _, j, position, left, right = max(found, key=lambda spot: spot[0])
    return j, position, left, right


# How far past engine A's box, in glyph pitches, the crop of a character no
# engine read reaches when its spot is at the box's edge (unread_crop).
UNREAD_CROP_REACH = 2


def unread_crop(render: Path, render_bytes: bytes | None, block: Mapping[str, Any], position: int,
                seg: Mapping[str, Any]) -> bytes | None:
    """The crop 裁決 is asked about a character no engine read on: the
    disagreement crop at POSITION of BLOCK's text (segment_crop) where the
    spot lies inside engine A's reading of the block, and the block's box
    grown by UNREAD_CROP_REACH glyph pitches every way (the pitch from the
    box's area over its characters) where it lies at either end - a
    character engine A did not read is often outside the box engine A drew
    round what it did.  Measured on a textbook table's header column: the
    box ends in the upper part of the header's last character, the one no
    engine read, and the block's own crop showed only the tops of its
    strokes."""
    count = len(CJK(str(block.get("text") or "")))
    if 0 < position < count:
        return facade_attr("segment_crop", segment_crop)(render, render_bytes, block, position, seg)
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001 - the ordinary crop stands instead
        return facade_attr("segment_crop", segment_crop)(render, render_bytes, block, position, seg)
    x, y, w, h = block["box"]
    pitch = ((w * width) * (h * height) / max(count, 1)) ** 0.5
    grow_x, grow_y = UNREAD_CROP_REACH * pitch / width, UNREAD_CROP_REACH * pitch / height
    return (upscale_crop(crop_block(render, [x - grow_x, y - grow_y, w + 2 * grow_x, h + 2 * grow_y], pad=0.0))
            or facade_attr("segment_crop", segment_crop)(render, render_bytes, block, position, seg))


def confirm_unread(answer: str, allowed: Counter, required: Counter, source: str,
                   confirm: Callable[[str, str, int], dict[str, Any]], asked: list[dict[str, Any]],
                   limit: int = UNREAD_TABLE_LIMIT) -> tuple[str | None, str]:
    """A rebuilt table refused only for characters no engine read, accepted
    when there are at most LIMIT places of them (unread_sites) and 裁決
    confirms every one on a crop of its own spot (CONFIRM: character, cell,
    offset -> the question's record, "confirmed" true or false).  The
    questions go into ASKED.  Returns the table and its reason, or None and
    why not ("" when the answer is not such a table).

    Measured on a textbook table's header: engine A read the fifth header
    cell but for its last character, engine B (v1.0) none of the cell, and
    the region rebuild (D22) was refused in all six benchmark runs for that
    character, the one printed header character no engine read.  The
    rebuild misreads too - in one run it wrote another character there, in
    another it misread the cell's first - so no such character stands on the
    rebuild's word: each is asked on its own crop, and a person is told
    (table-unread-confirmed)."""
    found: dict[str, Any] = {}
    check_rebuilt_table(answer, allowed, required, source, unread=found)
    if not found:
        return None, ""
    sites = unread_sites(found["markup"], found["characters"])
    if not sites or len(sites) > limit:
        return None, (f"{len(sites)} place(s) of characters no engine read ({found['characters'][:10]}), "
                      f"more than the {limit} 裁決 may confirm")
    results = [confirm(char, cell, at) for char, cell, at in sites]
    asked.extend(results)
    unconfirmed = [r for r in results if not r.get("confirmed")]
    if unconfirmed:
        return None, "裁決 did not confirm " + ", ".join(
            f"{r['char']} ({'read ' + r['answered'] if r.get('answered') else r.get('why') or 'nothing printed'})"
            for r in unconfirmed)
    return found["markup"], (f"{found['accepted']}; {len(results)} character(s) no engine read, "
                             f"confirmed by 裁決 ({''.join(r['char'] for r in results)})")


def rebuild_table(client: "Client", crop: bytes | None, text: str, required: Counter,
                  max_tokens: int, confirm: Callable[[str, str, int], dict[str, Any]] | None = None,
                  table_orientation: str = "auto"
                  ) -> tuple[str | None, dict[str, Any]]:
    """Ask the formatter to write one region as a table, from its crop, with its
    characters locked to `text` (what may appear) and `required` (what must).

    Not an engine re-read: the characters are the ones the engines read there
    (rebuild_lock; for a region read from engine B's lines, those lines).
    One retry names what was wrong; an answer that the region is not a table
    is taken as it stands.  With CONFIRM, an answer refused only for up to
    UNREAD_TABLE_LIMIT characters no engine read stands when 裁決 confirms
    each on its own crop (confirm_unread); the retry is told the lock's
    reason, not 裁決's reading.  Returns the table or None, and the record
    sealed in tablesRebuilt.
    """
    rebuild_system = REBUILD_AS_PRINTED_SYSTEM if table_orientation == "as-printed" else REBUILD_SYSTEM
    allowed = Counter(CJK(text))
    record: dict[str, Any] = {"locked": sum(allowed.values()), "required": sum(required.values())}
    ask = (f"呢個區域兩個引擎讀到嘅字（次序可能亂咗）：\n---\n{text}\n---\n"
           "睇裁圖：係表格就用上面啲字寫出個表，唔係就答「唔係表格」。")
    asked: list[dict[str, Any]] = []
    notes: list[str] = []

    def judged(answer: str, table: str | None, why: str) -> tuple[str | None, str]:
        if table is not None or confirm is None:
            return table, why
        confirmed, how = confirm_unread(answer, allowed, required, text, confirm, asked)
        if confirmed is not None:
            return confirmed, how
        if how:
            notes.append(how)
        return None, why

    answer, _ = client.ask_answering(rebuild_system, ask, crop, max_tokens, kind="table-rebuild")
    table, why = judged(answer, *check_rebuilt_table(answer, allowed, required, text))
    refused = [] if table is not None else [answer or ""]
    if table is None and not why.startswith(("not a table", "no table")):
        answer, _ = client.ask_answering(
            rebuild_system, ask + f"\n你上一次嘅答案唔收得，原因：{why}。淨係用上面啲字，一個都唔好漏，重新寫。",
            crop, max_tokens, kind="table-rebuild-retry")
        table, again = judged(answer, *check_rebuilt_table(answer, allowed, required, text))
        why = again if table is not None else f"{why}; retry: {again}"
        if table is None:
            refused.append(answer or "")
    record["outcome"] = "accepted" if table is not None else "refused"
    record["reason"] = why + (f" ({'; '.join(notes)})" if notes and table is None else "")
    if asked:
        # The characters no engine read that 裁決 was asked about (confirm_unread).
        record["unreadAsked"] = asked
    if refused:
        # A refused answer is sealed, as the formatter's is (formatterRejectedOutput):
        # a table lost to the lock is diagnosed from what the model wrote, not guessed.
        record["refusedAnswers"] = [a[:REFUSED_ANSWER_LIMIT] for a in refused]
    return table, record

# Engine B's draft a page was sealed with (sha256; "" when it had none).
DRAFT2_KEY = "draft2Sha256"
