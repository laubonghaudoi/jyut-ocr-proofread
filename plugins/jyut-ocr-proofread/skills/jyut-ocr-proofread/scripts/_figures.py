"""Figures kept as pictures: each map, chart, family tree, photograph or
plate cut from its page's render into BOOK/figures/, linked from final.md
and named in doubts.md (references/long-book-workflow.md 〈圖：淨係留圖片〉),
with no model and no GPU, in finish_book.py's finalize step.

Phase 3 reads text only, so every figure was lost without a word: measured
on the corpus (2026-10-05), a 609-page monograph holds 29 regions engine A
labels chart or image, on 28 pages (17 maps, 5 charts, 4 family trees, 3
pictures), and its doubts.md named none.  A map, a photograph or a chart
without printed values cannot be transcribed faithfully: the picture is
kept, the text inside it is not transcribed.

Which: engine A's layout regions that hold no text (the `regions` of its
page JSON, scripts/paddleocr_vl_worker.py) labelled chart or image - not
header_image, footer_image or seal -, merged where their crops would
overlap, of at least MIN_AREA of the page.  Engine-A pages written before
the worker kept its regions (no `regions` key) give none; so does a figure
engine A read as text, and a page the manifest leaves out.  A block phase 3
set aside because engine A looped on it (engineALoopsDropped) is not one:
its sealed record names the block, not its box, and the corpus holds one
such table (a benchmark page).  Where its image line goes: after the line
engine A reads just before it when that line is a title, else after the
page's nearest figure_title, else at the page's end (page_figures, place).

With engine B's drafts, two more (page_figures): a plate - a reproduced
original engine A reads as text, in a book printed in simplified script -
and a picture engine B boxes and engine A reads no text in.  Their text
goes from the delivered book (a caption engine B read stays), the image
line in its place, every line taken out listed (take_out, finish_book.py's
doubts): measured on a multi-volume archival compilation 1 (2026-10-25), engine A read most of its
plates as text blocks, the one it gave an image region for was cut and
transcribed too, and engine B's boxes round them were dropped as markup,
so 2,767 characters the user's copy keeps as pictures were transcribed,
mostly garbled.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass, field
import difflib
import itertools
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import unicodedata

from _ocr_markdown import (PAGE_MARKER_RE, find_page_markers, foot_notes, is_blank, line_body, preferred_newline,
                           split_lines)

FOLDER = "figures"                 # in BOOK; copied as X-圖/ beside the PDF (_deliver.py)
LABELS = {"chart": "圖表", "image": "圖片"}       # what doubts.md calls each
# Below 2 % of the page the corpus's chart/image regions are a barcode (1.7 %)
# and ornaments; the smallest above it is a title page's calligraphy (3.5 %).
MIN_AREA = 0.02
PAD = 0.01                         # of the page's width and height, around each crop
# How much of the anchor's letters (in runs of two or more) a paragraph must
# hold to be the anchor's.  Measured on a monograph's 29 anchored figures:
# the anchor's own paragraph holds 0.67-1.0 (engine A's line and the
# repaired text differ by a space, a variant, a misread character or a lost
# number), so it always matched; another paragraph of its page up to 0.86
# (a second copy of the caption, a heading's words in the text), which is
# why the best one wins.  The same share of a line of engine A's that
# engine B's page matches is engine B's reading of it (_readings).
MIN_COVER = 0.5
BREAKS = ("paragraph", "structural")
PICTURE = re.compile(r"page-\d{4,}-\d+\.png")
# Engine B's grounding box (proofread_pages.GROUNDING_LINE): a line of its
# draft holding only (x,y),(x,y), the corners of a picture or of a region it
# did not transcribe, on its 0-999 grid.
GROUNDING_BOX = re.compile(r"^[ \t]*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*,\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)[ \t]*$", re.M)
GRID = 1000
LATIN_WORD = re.compile(r"[A-Za-z]{2}")   # print in an engine B box (_drawn), not a lone letter


@dataclass
class Figure:
    page: int
    # 1, 2, ... on its page: engine A's regions in reading order (a plate that took one in, in its
    # place), then the other plates and engine B's boxes, top to bottom
    number: int
    label: str
    box: list[float]               # x, y, width, height: fractions of the page, top left
    crop: list[float]              # left, top, right, bottom: what is cut (_crop)
    anchor: str = ""               # the text its image line goes after (page_figures)
    picture: bool = False          # cut from the render
    after: str = ""                # where its image line went: in place of its text, after the anchor, the page's end
    source: str = "regions"        # what found it: engine A's regions, a plate, engine B's grounding box
    drop: list[int] = field(default_factory=list)       # engine A's lines inside it not transcribed (take_out)
    lines: list[str] = field(default_factory=list, repr=False)   # engine A's lines of its page, in reading order
    removed: list[str] = field(default_factory=list)    # the text's lines take_out took out for it
    text_b: str = field(default="", repr=False)         # engine B's draft of its page, its boxes left out

    @property
    def name(self) -> str:
        return f"page-{self.page:04d}-{self.number}.png"

    @property
    def link(self) -> str:
        """Its image line: no alt text, so no character is added to the text."""
        return f"![]({FOLDER}/{self.name})"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("lines")
        data.pop("text_b")
        return {**data, "drop": [self.lines[n] for n in self.drop if n < len(self.lines)], "name": self.name}


def _box(value: Any) -> list[float] | None:
    if not (isinstance(value, list) and len(value) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        return None
    x, y, w, h = (float(v) for v in value)
    return [x, y, w, h] if 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 else None


def _meet(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether the crops of boxes A and B (each padded by PAD) overlap.
    Measured over the corpus's 2,474 engine-A pages that keep their regions:
    no two chart or image regions of a page come closer than 4.9 % of the
    page (two family trees under their own headings), so this merges none
    there; a figure the layout model splits with no gap is joined."""
    return (max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]) <= 2 * PAD
            and max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]) <= 2 * PAD)


def _gap(a: Sequence[float], b: Sequence[float]) -> float:
    dx = max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2])
    dy = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])
    return math.hypot(max(0.0, dx), max(0.0, dy))


def _crop(box: Sequence[float], lines: Sequence[Mapping[str, Any]]) -> list[float]:
    """BOX with PAD around it, each side stopped at a text line of the page
    beyond it.  Measured on a monograph's 29 figures: engine A's box is
    tight on a map's frame (with no PAD, 26 crops end on ink: the frame
    cut), but 1 % took the lower half of the caption or heading above into
    2 pictures and a column of the inscription beside it into one."""
    x, y, w, h = box
    edges = [x - PAD, y - PAD, x + w + PAD, y + h + PAD]
    for lx, ly, lw, lh in filter(None, (_box(line.get("boundingBox")) for line in lines)):
        if lx < x + w and lx + lw > x:                # above or below it
            if ly + lh / 2 < y:
                edges[1] = max(edges[1], min(ly + lh, y))
            elif ly + lh / 2 > y + h:
                edges[3] = min(edges[3], max(ly, y + h))
        if ly < y + h and ly + lh > y:                # left or right of it
            if lx + lw / 2 < x:
                edges[0] = max(edges[0], min(lx + lw, x))
            elif lx + lw / 2 > x + w:
                edges[2] = min(edges[2], max(lx, x + w))
    return [round(max(0.0, edges[0]), 6), round(max(0.0, edges[1]), 6), round(min(1.0, edges[2]), 6),
            round(min(1.0, edges[3]), 6)]


def _union(a: Sequence[float], b: Sequence[float]) -> list[float]:
    x, y = min(a[0], b[0]), min(a[1], b[1])
    return [x, y, max(a[0] + a[2], b[0] + b[2]) - x, max(a[1] + a[3], b[1] + b[3]) - y]


def _inside(line: Mapping[str, Any], box: Sequence[float]) -> bool:
    """Whether the middle of LINE's box lies in BOX."""
    own = _box(line.get("boundingBox"))
    return own is not None and box[0] <= own[0] + own[2] / 2 <= box[0] + box[2] \
        and box[1] <= own[1] + own[3] / 2 <= box[1] + box[3]


def _overlap(line: Mapping[str, Any], box: Sequence[float]) -> bool:
    """Whether LINE's box and BOX share any of the page."""
    own = _box(line.get("boundingBox"))
    return own is not None and min(own[0] + own[2], box[0] + box[2]) > max(own[0], box[0]) \
        and min(own[1] + own[3], box[1] + box[3]) > max(own[1], box[1])


def grounding_boxes(text_b: str) -> list[list[float]]:
    """Engine B's grounding boxes in its draft TEXT_B (GROUNDING_BOX), as x,
    y, width, height fractions of the page; not one from corner to corner of
    its grid, which locates nothing: measured on the corpus (2026-10-25),
    engine B writes (0,0),(999,999) for four blank pages and a cover."""
    out = []
    for match in GROUNDING_BOX.finditer(text_b):
        x0, y0, x1, y1 = (int(v) for v in match.groups())
        if x1 > x0 and y1 > y0 and not (x0 == y0 == 0 and min(x1, y1) >= GRID - 1):
            out.append([x0 / GRID, y0 / GRID, (x1 - x0) / GRID, (y1 - y0) / GRID])
    return out


def _traditional(text: str) -> bool:
    """Whether TEXT is written in traditional script: the characters only
    traditional script has outnumber the simplified forms of such
    characters (proofread_pages.simplified_script's count, on one text)."""
    import proofread_pages as pp

    table = pp.traditional_forms()
    simplified = {form for forms in table.values() for form in forms}
    return sum(c in table for c in text) > sum(c in simplified and c not in table for c in text)


def fold(text: str) -> str:
    """TEXT's letters (_letters, engine A's circle for zero read as 〇:
    proofread_pages.ZERO_FORMS), each as one form of its character in
    either script (proofread_pages.simplified_form, then variant_fold),
    letter for letter."""
    import proofread_pages as pp

    letters = _letters(text.translate(pp.ZERO_FORMS))
    return "".join(f if len(f) == 1 else c for c in letters for f in [pp.variant_fold(pp.simplified_form(c))])


def _shares(texts: Sequence[str], others: Sequence[str]) -> list[list[int]]:
    """For each of TEXTS, how many of its letters one alignment (both sides
    in order, fold) matches with each of OTHERS."""
    mine, theirs = [fold(t) for t in texts], [fold(t) for t in others]
    owner = [n for n, part in enumerate(theirs) for _ in part]
    at = [n for n, part in enumerate(mine) for _ in part]
    out = [[0] * len(others) for _ in texts]
    for match in difflib.SequenceMatcher(None, "".join(mine), "".join(theirs), autojunk=False).get_matching_blocks():
        for k in range(match.size):
            out[at[match.a + k]][owner[match.b + k]] += 1
    return out


def _readings(texts: Sequence[str], text_b: str) -> list[str | None]:
    """For each of engine A's TEXTS (a page's lines, in its reading order),
    engine B's reading of it: the letters of engine B's draft TEXT_B that
    one alignment of the page (fold) matches with it, as engine B wrote
    them; None where they are under MIN_COVER of its letters (engine B left
    it unread).  Measured on the lines engine A reads in traditional script
    in a multi-volume archival compilation 1-6 (2026-10-25): engine B matched 0.00-0.12 of the letters of
    the plates' lines it left unread (a box, a loop or nothing in their
    place) and 0.75-1.00 of those it read, but a line of two letters, one
    matched (0.50); of the 44 printed headings, 0.83-1.00 but a two-letter
    name, one matched (0.50)."""
    import proofread_pages as pp

    letters = _letters(text_b.translate(pp.ZERO_FORMS))
    found: list[list[int]] = [[] for _ in texts]
    mine = [fold(t) for t in texts]
    at = [n for n, part in enumerate(mine) for _ in part]
    for match in difflib.SequenceMatcher(None, "".join(mine), fold(text_b), autojunk=False).get_matching_blocks():
        for k in range(match.size):
            found[at[match.a + k]].append(match.b + k)
    return ["".join(letters[j] for j in hits) if len(hits) >= MIN_COVER * len(mine[n]) else None
            for n, hits in enumerate(found)]


Group = tuple[list[float], str, int, int]     # engine A's regions merged: box, label, reading order, afterLine


def _region(one: Group | None, other: Group | None) -> Group | None:
    """Two groups of engine A's regions a plate took in, as one: chart over
    image, the earlier place."""
    if one is None or other is None:
        return one or other
    return (_union(one[0], other[0]), "chart" if "chart" in (one[1], other[1]) else one[1], min(one[2], other[2]),
            min(one[3], other[3]))


def _parted(lines: Sequence[Mapping[str, Any]], readings: Sequence[str | None], one: Sequence[float],
            other: Sequence[float]) -> bool:
    """Whether print parts the plates ONE and OTHER: a line engine B reads in
    simplified script, the book's, that lies between them - in the box
    joining them, wholly or partly, and in neither - such as a caption."""
    import proofread_pages as pp

    joined = _union(one, other)
    return any(_overlap(line, joined) and not _overlap(line, one) and not _overlap(line, other)
               and reading is not None and pp.simplified_script([reading]) for line, reading in zip(lines, readings))


def _plates(lines: Sequence[Mapping[str, Any]], texts: Sequence[str], readings: Sequence[str | None],
            groups: list[Group], boxes: list[list[float]]) -> list[tuple[list[float], set[int], Group | None]]:
    """A page's plates, in a book printed in simplified script: each block
    engine A reads in traditional script (_traditional; not a folio, not a
    title) that engine B leaves unread or reads in traditional script too,
    with every engine A region group that meets it and every engine B box
    that meets one of its blocks or the regions it took in (taken out of
    GROUPS and BOXES), and every other such block of the page no print
    parts it from (_parted): (box, its blocks, the region group it took
    in, if any).

    A title (paragraph_title, doc_title, figure_title) starts none: of a
    printed two-letter heading engine A misreads one letter in traditional
    script, and one misread of engine B's leaves it under MIN_COVER read
    (the measured headings go down to 0.50, _readings), a picture of
    itself; inside a plate other blocks or a box found, it still goes when
    engine B did not read it (page_figures).  Measured on a multi-volume archival compilation 1-6
    (2026-10-26) it changes one plate: a cover's date column engine B
    reads in traditional script (a multi-volume archival compilation 6 scan 6) stays text.  A box that
    meets only another box joins none: a chain of specks would stretch the
    plate across the page, and with it the lines it drops.  Text the print
    typesets in traditional script, which both engines read alike (a
    footnote citing a work printed so), is a plate too: no area, region or
    box tells it from a plate engine B transcribes (a multi-volume archival compilation 1 scans 14 and
    15 have none)."""
    taken: list[list[Any]] = [[box, {n}, None] for n, line in enumerate(lines)
                              if line.get("blockLabel") != "number" and not str(line.get("blockLabel")).endswith("_title")
                              and (box := _box(line.get("boundingBox"))) and _traditional(texts[n])
                              and (readings[n] is None or _traditional(readings[n]))]
    grown = True
    while grown:
        grown = False
        for plate in taken:
            for group in [g for g in groups if _meet(g[0], plate[0])]:
                groups.remove(group)
                plate[0], plate[2], grown = _union(plate[0], group[0]), _region(plate[2], group), True
            own = [_box(lines[n].get("boundingBox")) for n in plate[1]] + ([plate[2][0]] if plate[2] else [])
            for box in [b for b in boxes if any(_meet(b, o) for o in own)]:
                boxes.remove(box)
                plate[0], grown = _union(plate[0], box), True
        for one, other in itertools.combinations(taken, 2):
            if _meet(one[0], other[0]) or not _parted(lines, readings, one[0], other[0]):
                taken.remove(other)
                one[:], grown = [_union(one[0], other[0]), one[1] | other[1], _region(one[2], other[2])], True
                break
    return [(box, members, region) for box, members, region in taken]


def _drawn(lines: Sequence[Mapping[str, Any]], texts: Sequence[str], regions: Sequence[Sequence[float]],
           boxes: Sequence[list[float]]) -> list[list[float]]:
    """Engine B's BOXES of at least MIN_AREA that meet none of engine A's
    REGIONS, merged where they meet, but those a line of engine A's that is
    print (not a folio) lies in, wholly or partly: one with Han characters
    or kana, or with a Latin word of two letters or more (a caption, a
    credit), but in a formula block (display_formula: what engine A reads
    off a drawing's dots, a multi-volume archival compilation 1 scan 76).  Measured on the corpus's 15
    books (2026-10-26): the Latin words change no figure."""
    drawn: list[list[float]] = []
    for box in (b for b in boxes if b[2] * b[3] >= MIN_AREA and not any(_meet(b, r) for r in regions)):
        while (hit := next((d for d in drawn if _meet(d, box)), None)) is not None:
            drawn.remove(hit)
            box = _union(box, hit)
        drawn.append(box)
    return [box for box in drawn if not any(
        _overlap(line, box) and line.get("blockLabel") != "number"
        and (_han(text) or (LATIN_WORD.search(text) and line.get("blockLabel") != "display_formula"))
        for line, text in zip(lines, texts))]


def page_figures(page: int, data: Mapping[str, Any], text_b: str | None = None,
                 simplified: bool = False) -> list[Figure]:
    """The figures of engine A's page DATA (its JSON): its regions, in its
    reading order (a plate that took one in, in its place), then, given
    engine B's draft TEXT_B, its other plates and engine B's boxes (below),
    top to bottom.  Each region's anchor is the line engine A reads just
    before it (afterLine) when that line is a title, else the page's
    figure_title nearest it.  Measured on a monograph's 29 figures: the
    line before is the caption in 23; in 4 a heading the print sets right
    above the figure (family trees each under its own heading, a caption
    engine A labels a paragraph title), which the nearest figure_title put
    the picture before or, with none, at the page's end; in 2 it is text
    set beside the figure or inside it, and the nearest figure_title is the
    caption.

    A plate, in a book printed in SIMPLIFIED script (_plates): a reproduced
    original (a handwritten report, a proclamation, a register page, a
    seal-script title) that engine A reads as text, in traditional script,
    and engine B leaves unread or reads in traditional script too - and so
    text the print typesets in traditional script, which both engines read
    alike.  Engine A's lines inside it go from the text (`drop`,
    take_out): its blocks, and every other line engine B did not read, as
    one alignment of the page in reading order finds it (_readings: a line
    engine B reads in another order counts as unread).  A caption engine B
    read stays when it is a line of its own: take_out keeps a line in the
    book's script that engine B's draft holds, but a line phase 3 ran
    together with plate text goes whole.  Measured (2026-10-25): on a multi-volume archival compilation
    1 it pictures the plates of scans 11-19 and 77 and the seal-script
    series title on scan 1, which our book transcribed as 2,804
    characters, mostly garbled; on a multi-volume archival compilation 2-5 only that title; on a multi-volume archival compilation 6
    the plates of scans 4-7, the scans the user's copy keeps as pictures -
    5, one page holding two with a caption between (scan 6's date column,
    a title, stays text since 2026-10-26); the 44 printed headings engine
    A reads in traditional script in a multi-volume archival compilation 1-6 engine B reads otherwise,
    and none is one.  It misses a plate engine B reads in mixed script
    (a multi-volume archival compilation 1 scan 20).

    An engine B grounding box (grounding_boxes, _drawn) of at least
    MIN_AREA that meets no engine A region and no line of engine A's print
    (Han characters, kana, a Latin word) is a picture too - a drawing, a
    diagram, a photograph engine A found no region for - and engine A's
    lines inside it that engine B did not read go (circles, digits, a
    formula read off a drawing).  Measured on the corpus (2026-10-25):
    a multi-volume archival compilation 1's diagrams of scans 73, 74 and 76 and 14 photographs of a
    memoir are such boxes; engine B also boxes printed text - the upper
    page of a two-page scan, 769 characters engine A read and engine B did
    not; the foot notes of three pages, read anyway (a monograph) - and
    drawings with captions it shifts onto the caption (a multi-volume archival compilation 1 scan 68):
    the Han characters there keep it from a picture.  Specks under MIN_AREA
    (one page of a multi-volume archival compilation 5 has 212) only join a plate whose own block or
    region they meet."""
    lines = [line for line in data.get("lines") or [] if isinstance(line, dict)]
    found = [(box, region["blockLabel"], after if isinstance(after := region.get("afterLine"), int) else 0)
             for region in data.get("regions") or [] if isinstance(region, dict)
             and region.get("blockLabel") in LABELS and (box := _box(region.get("boundingBox")))]
    groups: list[Group] = []
    for order, (box, label, after) in enumerate(found):
        while (hit := next((g for g in groups if _meet(g[0], box)), None)) is not None:
            groups.remove(hit)
            box = _union(box, hit[0])
            label, order = ("chart" if "chart" in (label, hit[1]) else label), min(order, hit[2])
            after = min(after, hit[3])
        groups.append((box, label, order, after))
    texts = [str(line.get("text") or "") for line in lines]
    # A box's corners are no reading: aligned, their digits would make a
    # line of figures inside it read or unread by where the box ends.
    read_b = GROUNDING_BOX.sub("", text_b) if text_b is not None else ""
    readings = _readings(texts, read_b) if text_b is not None else [None] * len(lines)
    boxes = grounding_boxes(text_b) if text_b is not None else []
    plates = _plates(lines, texts, readings, groups, boxes) if simplified and text_b is not None else []
    found_figures = sorted([(group, "regions", set()) for group in groups if group[0][2] * group[0][3] >= MIN_AREA]
                           + [((box, *region[1:]), "plate", members) for box, members, region in plates if region],
                           key=lambda f: f[0][2])
    found_figures += sorted([((box, "image", 0, 0), "plate", members) for box, members, region in plates if not region]
                            + [((box, "image", 0, 0), "grounding", set())
                               for box in _drawn(lines, texts, [box for box, _, _ in found], boxes)],
                            key=lambda f: (f[0][0][1], f[0][0][0]))
    titles = [(box, str(line["text"])) for line in lines
              if line.get("blockLabel") == "figure_title" and str(line.get("text") or "").strip()
              and (box := _box(line.get("boundingBox")))]
    out = []
    for number, ((box, label, _, after), source, members) in enumerate(found_figures, 1):
        before = lines[after - 1] if 0 < after <= len(lines) else {}
        anchor = (str(before.get("text") or "") if str(before.get("blockLabel")).endswith("_title")
                  else min(titles, key=lambda t: _gap(t[0], box))[1] if titles else "")
        drop = [n for n, line in enumerate(lines) if n in members or (source != "regions" and readings[n] is None
                and line.get("blockLabel") != "number" and _inside(line, box))]
        out.append(Figure(page, number, label, [round(v, 6) for v in box], _crop(box, lines), anchor,
                          source=source, drop=drop, lines=texts if drop else [], text_b=read_b if drop else ""))
    return out


def _han(text: str) -> bool:
    """Whether TEXT holds a letter of a script without case (Han, kana):
    not a circle, a digit or Latin."""
    return any(unicodedata.category(c) == "Lo" for c in text)


def simplified_book(ocr_a: Path, ocr_b: Path) -> bool:
    """Whether the book is printed in simplified script, as phase 3 tells it:
    proofread_pages.simplified_script on every draft of both engines."""
    import proofread_pages as pp

    return pp.simplified_script(path.read_text(encoding="utf-8") for folder in (ocr_a, ocr_b)
                                for path in sorted(folder.glob("page-*.txt")))


def find(ocr_a: Path, pages: Sequence[int], ocr_b: Path | None = None) -> list[Figure]:
    """The figures of PAGES, from engine A's page JSONs in OCR_A and engine
    B's drafts in OCR_B (page_figures)."""
    simplified = ocr_b is not None and simplified_book(ocr_a, ocr_b)
    out: list[Figure] = []
    for page in pages:
        try:
            data = json.loads((ocr_a / f"page-{page:04d}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            text_b = (ocr_b / f"page-{page:04d}.txt").read_text(encoding="utf-8") if ocr_b is not None else None
        except OSError:
            text_b = None
        if isinstance(data, dict):
            out += page_figures(page, data, text_b, simplified)
    return out


def pictures(folder: Path) -> list[Path]:
    """The pictures in FOLDER (BOOK/figures/), by name."""
    return sorted(p for p in folder.iterdir() if PICTURE.fullmatch(p.name) and p.is_file()
                  and not p.is_symlink()) if folder.is_dir() else []


def cut(figures: Sequence[Figure], renders: Path, folder: Path) -> None:
    """Each figure's picture cut from its page's render (its crop) into
    FOLDER as a lossless PNG - grey when the render's three bands are one,
    the same pixels in about two thirds of the bytes (measured on a
    monograph's scans).  A figure whose render cannot be read gets none
    (picture stays False), and a picture of FOLDER no figure has goes, so
    an earlier run's is never delivered for it."""
    from PIL import Image, ImageChops

    for page in sorted({figure.page for figure in figures}):
        try:
            image = Image.open(renders / f"page-{page:04d}.png")
            image.load()
        except OSError:
            continue
        width, height = image.size
        for figure in (f for f in figures if f.page == page):
            left, top, right, bottom = figure.crop
            crop = image.crop((round(left * width), round(top * height), round(right * width), round(bottom * height)))
            if crop.mode == "RGB" and not any(ImageChops.difference(crop.getchannel(0), crop.getchannel(band)).getbbox()
                                              for band in (1, 2)):
                crop = crop.convert("L")
            folder.mkdir(exist_ok=True)
            crop.save(folder / figure.name, "PNG")
            figure.picture = True
    kept = {figure.name for figure in figures if figure.picture}
    for path in pictures(folder):
        if path.name not in kept:
            path.unlink()
    with contextlib.suppress(OSError):
        folder.rmdir()                 # only when no picture is left in it


def _letters(text: str) -> str:
    return "".join(c for c in text if c.isalnum())


def _anchor_block(lines: Sequence[str], blocks: Sequence[tuple[int, int]], anchor: str) -> tuple[int, int] | None:
    """The paragraph (first, last line) of BLOCKS holding ANCHOR: the one
    whose letters cover the most of the anchor's, in runs of two or more,
    and at least MIN_COVER of them; of equals, the one the anchor is most
    of (a caption's own short paragraph, not body text that quotes it
    before it), then the first."""
    want = _letters(anchor)
    best, rank = None, (MIN_COVER, 0.0)
    for block in blocks if len(want) >= 2 else ():
        have = _letters("".join(lines[block[0]:block[1] + 1]))
        matcher = difflib.SequenceMatcher(None, want, have, autojunk=False)
        cover = sum(m.size for m in matcher.get_matching_blocks() if m.size >= 2) / len(want)
        if (cover, len(want) / max(len(have), 1)) > rank:
            best, rank = block, (cover, len(want) / max(len(have), 1))
    return best


def _next_text(lines: Sequence[str], index: int) -> int | None:
    index += 1
    while index < len(lines) and is_blank(lines[index]):
        index += 1
    return index if index < len(lines) else None


def _paragraph_end(lines: Sequence[str], last: int, boundaries: Mapping[int, str]) -> int:
    """The last line of the paragraph that ends at line LAST, or runs on from
    it: while the next text is a page marker that does not break the
    paragraph (join, list-cont, list-new), the paragraph goes on to the end of
    the next page's first lines - an image line before such a marker would
    split a sentence, or be glued to the next page's first line."""
    while True:
        following = _next_text(lines, last)
        # A join runs the paragraph on past the page's foot notes.
        at = next((i for i in range(last + 1, len(lines)) if PAGE_MARKER_RE.fullmatch(line_body(lines[i]))), None)
        if following is not None and at is not None and foot_notes(lines, at) <= following:
            following = at
        marker = PAGE_MARKER_RE.fullmatch(line_body(lines[following])) if following is not None else None
        if marker is None or boundaries.get(int(marker.group("page"))) in BREAKS:
            return last
        last = _next_text(lines, following) or following
        while (last + 1 < len(lines) and not is_blank(lines[last + 1])
               and not any(PAGE_MARKER_RE.fullmatch(line_body(lines[i])) for i in (last, last + 1))):
            last += 1


def _blocks(lines: Sequence[str], start: int, end: int) -> list[tuple[int, int]]:
    """The paragraphs (first, last line) of LINES between the page marker on
    line START and line END."""
    blocks, first = [], None
    for index in range(start + 1, end + 1):
        if index < end and not is_blank(lines[index]):
            first = index if first is None else first
        elif first is not None:
            blocks.append((first, index - 1))
            first = None
    return blocks


def _printed(line: str, text_b: str) -> bool:
    """Whether LINE is in simplified script (proofread_pages.simplified_script)
    and engine B's draft TEXT_B holds MIN_COVER of its letters (fold, in
    order): print engine B read."""
    import proofread_pages as pp

    mine = fold(line)
    hits = sum(m.size for m in difflib.SequenceMatcher(None, mine, fold(text_b), autojunk=False).get_matching_blocks())
    return bool(mine) and hits >= MIN_COVER * len(mine) and pp.simplified_script([line])


def take_out(text: str, figures: Sequence[Figure], boundaries: Mapping[int, str]) -> tuple[str, dict[int, str]]:
    """TEXT, the repaired book with its page markers, without the lines that
    transcribe what FIGURES picture, each one's image line in place of the
    first line taken out for it (its `after` is "text", its `removed` the
    lines taken out), and BOUNDARIES with each page break that runs a line
    taken out on to the next page, or the next page's first line on to it,
    made a paragraph break: nothing runs on there any more.

    A line of a figure's page goes when one alignment of the page (_shares:
    its lines' letters against those of engine A's lines) puts more of its
    letters on engine A's lines the figure drops (`drop`) than on the other
    lines: the page's text is engine A's or engine B's reading merged, in
    reading order, so a caption stays on its own line of engine A's and a
    line phase 3 rewrote stays the plate's.  Measured on a multi-volume archival compilation 1
    (2026-10-25): the seal-script title, of which 裁決 rewrote four of six
    characters, keeps two on its line of engine A's and none on another.
    But a line in the book's simplified script whose letters engine B's
    draft of the page holds, MIN_COVER of them (_printed), stays: a
    caption engine B read, which page_figures drops when engine B wrote it
    out of engine A's order and it lies inside the plate, or when engine A
    read it into the plate's block.  Measured (2026-10-26): it keeps none
    of the 28 lines taken out on a multi-volume archival compilation 1-5."""
    lines = split_lines(text)
    markers, _ = find_page_markers(lines)
    position = {marker.page: n for n, marker in enumerate(markers)}
    joins = dict(boundaries)
    gone: dict[int, Figure] = {}
    for page in sorted({figure.page for figure in figures if figure.drop and figure.page in position}):
        mine = [figure for figure in figures if figure.page == page and figure.drop]
        n = position[page]
        end = markers[n + 1].line_index if n + 1 < len(markers) else len(lines)
        here = [index for index in range(markers[n].line_index + 1, end) if not is_blank(lines[index])]
        dropped = {k for figure in mine for k in figure.drop}
        for index, share in zip(here, _shares([lines[index] for index in here], mine[0].lines)):
            taken = [sum(share[k] for k in figure.drop) for figure in mine]
            if max(taken) > sum(share) - sum(share[k] for k in dropped) and not _printed(lines[index], mine[0].text_b):
                gone[index] = mine[taken.index(max(taken))]
    first: dict[str, int] = {}
    for index in sorted(gone):
        first.setdefault(gone[index].name, index)
        gone[index].removed.append(lines[index].rstrip("\r\n"))
    for marker in markers:
        if joins.get(marker.page) in (*BREAKS, "start", None):
            continue
        before = next((i for i in range(foot_notes(lines, marker.line_index) - 1, -1, -1) if not is_blank(lines[i])), None)
        after = _next_text(lines, marker.line_index)
        if before in gone or after in gone:
            joins[marker.page] = "paragraph"
    touched = {position[figure.page] for figure in gone.values()}
    for first_line, last_line in sorted((block for n in touched for block in _blocks(
            lines, markers[n].line_index, markers[n + 1].line_index if n + 1 < len(markers) else len(lines))
            if any(i in gone for i in range(block[0], block[1] + 1))), reverse=True):
        newline = preferred_newline(lines, first_line)
        pieces: list[list[str]] = []
        kept: list[str] = []
        for index in range(first_line, last_line + 1):
            if index not in gone:
                kept.append(lines[index])
                continue
            figure = gone[index]
            if kept:
                pieces.append(kept)
                kept = []
            if first[figure.name] == index:
                pieces.append([figure.link])
                figure.after = "text"
        if kept:
            pieces.append(kept)
        new = [line if line.endswith(("\n", "\r")) else line + newline
               for k, piece in enumerate(pieces) for line in ([newline] if k else []) + piece]
        if new:
            lines[first_line:last_line + 1] = new
        else:                          # with the blank line before it
            del lines[first_line - (first_line > 0 and is_blank(lines[first_line - 1])):last_line + 1]
    return "".join(lines), joins


def place(text: str, figures: Sequence[Figure], boundaries: Mapping[int, str]) -> str:
    """TEXT, the repaired book with its page markers, with each figure's image
    line - `![](figures/NAME)`, no alt text, so no character is added to the
    book's text - on its own line between blank lines, after the paragraph of
    its page that holds its anchor, else after the page's last paragraph
    (each figure's `after` says which); never inside a paragraph that runs
    on to the next page.  A figure take_out put in place of its text stays
    there."""
    lines = split_lines(text)
    markers, _ = find_page_markers(lines)
    position = {marker.page: n for n, marker in enumerate(markers)}
    links: dict[int, list[str]] = {}
    for figure in figures:
        n = position.get(figure.page)
        if n is None or figure.after == "text":
            continue
        start = markers[n].line_index
        blocks = _blocks(lines, start, markers[n + 1].line_index if n + 1 < len(markers) else len(lines))
        block = _anchor_block(lines, blocks, figure.anchor)
        figure.after = "anchor" if block else "page"
        last = block[1] if block else (blocks[-1][1] if blocks else start)
        links.setdefault(_paragraph_end(lines, last, boundaries), []).append(figure.link)
    for last in sorted(links, reverse=True):
        newline = preferred_newline(lines, last)
        if not lines[last].endswith(("\n", "\r")):
            lines[last] += newline
        added = [piece for link in links[last] for piece in (newline, link + newline)]
        if last + 1 < len(lines) and not is_blank(lines[last + 1]):
            added.append(newline)
        lines[last + 1:last + 1] = added
    return "".join(lines)
