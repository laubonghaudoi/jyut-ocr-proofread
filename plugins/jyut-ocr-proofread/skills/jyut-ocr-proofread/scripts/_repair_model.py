"""The book model of the book repair tool (scripts/repair_book.py): what the
repair rules need to know about one book, learned from its own drafts and
sealed pages, with no model call - written to book-model.json.

Each entry is measured here and carries a gate: whether the book shows it
clearly enough for a rule to act on it, with the numbers.  A rule whose gate
failed stays silent on that book (a `gate-failed` doubt); nothing here is a
constant of one book.

- M-1 head band, per page parity: the folio blocks (engine A's `number`
  blocks) give the band's place; the band holds the folio, the blocks phase
  3's furniture_band takes, and the short thin blocks in
  the band's column (or the folio's row) at the page's outer edge - the
  column the page's own band blocks mark, the parity's folio strip only on
  a page with none, and no other block beyond it.  Of furniture_band's
  picks, a folio must lie at the parity's folio place and a block taken by
  its text where the parity's running heads run (unplaced_heads); a stem
  set in display type is the page's own text (drop_display_heads).
- M-2 stems: the profile's running heads, or the most shared core of the
  band's text; M-2b the characters the engines read at a stem's places.
- M-3 run suffixes: what the band prints beside the stem, the same on three
  or more consecutive pages (a volume label, the contents word).
- M-4 folio sequence: per run, the offset scan - folio that most readings
  agree on, and the jumps where leaves are missing.
- M-5 dividers: the page that opens a run and prints its suffix in display
  type.
- M-6 contents pages: a run of list-like pages whose lines map, in order,
  onto the book's other lines - most of its lines, holding at least half
  of its characters, onto more than one page, and (a run of one page) no
  more than half of them onto any one.  A line holding printed
  lines the OCR ran together (a title and its date line) is read as those
  lines: cut after leaders and the page number after them, at a `<br>` or
  after leaders with no page number beside a date line, and after a date
  that opens it and runs on into more than a note.  A title printed over
  two lines is one entry: a line with no leaders and no page number, and
  the line straight after it ending with the page number, where the body
  prints the two together from a line's start with no line break between
  them (a title the body wraps at the same place stays two entries).
- M-7 engine B's stand-ins: its mark aliases (phase 3's mark_evidence /
  mark_aliases), and the glyphs it writes where engine A printed figures
  (not CJK numerals: those read the figure in the other script), dashes and
  dots.
- M-9 body glyph size; M-10 sentence-final marks and the date shape.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from itertools import accumulate
import difflib
import math
import re
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import proofread_pages as pp
import _repair_align as ra


SCHEMA_VERSION = 1

# M-1: a parity's band is measured from at least this many pages with a
# folio block (FURNITURE_MINIMUM_PAGES, phase 3's floor for anything
# recurring).
BAND_MINIMUM_PAGES = pp.FURNITURE_MINIMUM_PAGES
# M-1: a page's own band block marks the band's column when it is no wider
# than this many times the parity's folio (the band's column width).
BAND_COLUMN_SPAN = 2
# M-1: a folio lies within this many of the parity's folio widths (heights)
# of the parity's folio column (row).
FOLIO_PLACE_SLACK = 2
# M-2: a stem is shared by at least this share of the band's pages
# (FURNITURE_PAGE_SHARE), and a longer string replaces a shorter core only
# while it keeps this share of the core's pages.
STEM_PAGE_SHARE = pp.FURNITURE_PAGE_SHARE
STEM_EXTENSION_SHARE = 0.9
# M-2: a stem match - positions equal (a misread character in place), or
# phase 3's is_running_head similarity for one character more or less.
STEM_POSITION_SHARE = 0.7
STEM_SIMILARITY = 0.8
# M-3: a suffix repeats on at least this many consecutive band pages.
RUN_MINIMUM_PAGES = 3
# M-4: the readings that must agree with a run's offsets.
FOLIO_AGREEMENT = 0.6
# M-5: display type is at least this many times the body glyph.
DISPLAY_RATIO = 1.5
TITLE_LABELS = frozenset({"doc_title", "paragraph_title", "title", "figure_title"})
# M-6: a list-like page has at least this many lines and a median line of at
# most this many characters; a contents run maps at least this share of its
# lines, each at this similarity, and the lines it maps hold at least half
# of its characters (learn_contents).
CONTENTS_MINIMUM_LINES = 3
CONTENTS_LINE_LENGTH = 30
CONTENTS_SHARE = 0.6
CONTENTS_TEXT_SHARE = 0.5
CONTENTS_SIMILARITY = 0.9
CONTENTS_LEADING_DROP = 3
# M-6: a run of one page more than this share of whose mapped lines stand
# on one other page is that page printed again, not a contents
# (learn_contents).
CONTENTS_ONE_PAGE = 0.5
# M-6: where a line holding printed lines the OCR ran together may be cut
# (contents_pieces): a `<br>`, and the end of leaders (`page`: with the
# page number after them).
CONTENTS_CUT = re.compile(r"<br\s*/?>|(?:[…⋯]+|[.．·]{2,})(?P<page>[ \t]*[(（]?[ \t]*[0-9０-９]{1,4}[ \t]*[)）]?)?",
                          re.IGNORECASE)
# M-6: the page number that ends a contents line (bracketed or not, the
# emphasis that may close the line after it): the last line of a title
# printed over two lines (wrapped_entries).
CONTENTS_PAGE_END = re.compile(r"[0-9０-９][ \t]*[)）]?[ \t]*(?:\*{1,3}|_{1,3})?[ \t]*$")
# M-7: engine B's glyph stands in for print engine A read as no character
# when this share of its insertions (where A read no character) are at gaps
# where A printed a mark or a figure; a figure stand-in is seen at A's
# figure gaps FIGURE_STANDIN_MINIMUM times at least, a dash (dot) stand-in at
# gaps where A printed only a dash (a dot) MARK_ALIAS_MINIMUM times.
FIGURE_STANDIN_MINIMUM = 3
STANDIN_SHARE = 0.8
# An insertion longer than this is text engine A skipped (a line, a
# heading), not stand-ins: a bracketed figure is read as three glyphs (the
# brackets' two and the figure's), with one more for a mark before it.
STANDIN_RUN = 4
# A figure stand-in is not a mark alias that now and then meets a figure: at
# least this share of its occurrences at A's printed gaps are at figures.
FIGURE_SHARE_OF_MARKED = 1 / 3
# M-10: the marks a sentence can end with, and a date's shape.
SENTENCE_FINAL = "。！？」』）…—"
NUMERALS = "〇零一二三四五六七八九十廿卅百千"
DATE_SHAPE = re.compile(
    rf"[{NUMERALS}]+年(?:[{NUMERALS}]+月)?(?:[{NUMERALS}]+日)?|[{NUMERALS}]+月[{NUMERALS}]+日"
    rf"|[{NUMERALS}]{{1,4}}[，、．.,][{NUMERALS}]{{1,3}}(?:[，、．.,][{NUMERALS}]{{1,3}})?[，、．.,]?")
# M-6: a date that opens a contents line's piece (contents_pieces): a year,
# month and day after at most CONTENTS_LEADING_DROP other characters (an era
# name; 正 the first month, 闰 or 閏 a leap month, 初 a day of the first ten).
OPENING_DATE = re.compile(rf"[^{NUMERALS}]{{0,{CONTENTS_LEADING_DROP}}}[{NUMERALS}]+年[闰閏]?[{NUMERALS}正]+月初?[{NUMERALS}]+日")

DIGIT_VALUE = {c: i for i, c in enumerate("〇一二三四五六七八九")}
DIGIT_VALUE["零"] = 0
UNIT_VALUE = {"十": 10, "百": 100, "千": 1000}


def numeral_value(text: str) -> int | None:
    """A folio's value: Arabic figures; CJK digits read digit by digit (the
    folio form 一七七, 二〇); or a positional numeral (二十五, 一百零三).
    None for anything else."""
    text = text.strip().translate(ra.FULLWIDTH_DIGITS)
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    if all(c in DIGIT_VALUE for c in text):
        return int("".join(str(DIGIT_VALUE[c]) for c in text))
    if not all(c in DIGIT_VALUE or c in UNIT_VALUE for c in text):
        return None
    total, digit = 0, None
    for c in text:
        if c in DIGIT_VALUE:
            # A zero holds an empty place (一百零三): the digit after it
            # takes its place.
            if digit not in (None, 0):
                return None
            digit = DIGIT_VALUE[c]
        else:
            total += (1 if digit is None else digit) * UNIT_VALUE[c]
            digit = None
    return total + (digit or 0)


def is_numeral_char(c: str) -> bool:
    return c in DIGIT_VALUE or c in UNIT_VALUE or c.isdigit()


# --- stems -----------------------------------------------------------------

def stem_matches(chars: str, stem: str) -> list[tuple[int, int]]:
    """Where STEM occurs in CHARS as a running head's copy: a window of its
    length with at least STEM_POSITION_SHARE of its positions equal, or one
    a character longer or shorter at STEM_SIMILARITY (a shorter one no
    shorter than that share of the stem); left to right, not overlapping,
    the best window at each place."""
    size = len(stem)
    if size < 2:
        return []
    need = math.ceil(STEM_POSITION_SHARE * size)
    out = []
    at = 0
    while at <= len(chars) - size + 1 and at < len(chars):
        window = chars[at:at + size]
        if len(window) == size and sum(x == y for x, y in zip(window, stem)) >= need:
            out.append((at, at + size))
            at += size
            continue
        found = None
        # A window one shorter holds at least the characters a positional
        # match needs: for a three-character stem a two-character window
        # would take every body word the stem begins with for a head copy.
        for width in (size + 1, size - 1):
            other = chars[at:at + width]
            if len(other) == width and width >= max(2, need) and other[0] == stem[0] and \
                    difflib.SequenceMatcher(None, other, stem, autojunk=False).ratio() >= STEM_SIMILARITY:
                found = (at, at + width)
                break
        if found:
            out.append(found)
            at = found[1]
            continue
        at += 1
    return out


def shared_core(texts: Sequence[str], pages: int) -> str | None:
    """The string most of the band's pages share (STEM_PAGE_SHARE of PAGES
    at least), extended while the longer string keeps STEM_EXTENSION_SHARE of
    the core's pages.  TEXTS holds one band text per page."""
    if not texts or pages < BAND_MINIMUM_PAGES:
        return None
    floor = max(BAND_MINIMUM_PAGES, math.ceil(STEM_PAGE_SHARE * pages))
    counts: Counter = Counter()
    for text in texts:
        counts.update({text[i:j] for i in range(len(text)) for j in range(i + 2, min(len(text), i + 24) + 1)})
    shared = {s: n for s, n in counts.items() if n >= floor}
    if not shared:
        return None
    top = max(shared.values())
    core = max((s for s, n in shared.items() if n == top), key=len)
    while True:
        longer = [s for s, n in shared.items() if core in s and len(s) == len(core) + 1
                  and n >= STEM_EXTENSION_SHARE * shared[core]]
        if not longer:
            return core
        core = max(longer, key=lambda s: shared[s])


# --- per page evidence -----------------------------------------------------

def page_size(page: Any) -> tuple[int, int] | None:
    size = page.engine_a_size()
    if size:
        return size
    try:
        from PIL import Image
        with Image.open(page.render) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return None


def folio_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Engine A's folio blocks: labelled number, reading numerals, figures,
    or no character."""
    out = []
    for block in blocks:
        if block.get("label") != "number":
            continue
        chars = pp.CJK(str(block.get("text") or ""))
        if all(c in pp.NUMERAL_CHARS for c in chars):
            out.append(block)
    return out


def _share(lo: float, hi: float, alo: float, ahi: float) -> float:
    return 0.0 if hi <= lo else max(0.0, min(hi, ahi) - max(lo, alo)) / (hi - lo)


# M-1: what furniture_band took a block for by its text, not its place.
TEXT_PICKS = ("running head", "label header", "label footer", "head's size")


def unplaced_heads(picked: Mapping[int, Mapping[int, str]], blocks: Mapping[int, Sequence[Mapping[str, Any]]],
                   sides: Mapping[str, str | None]) -> set[tuple[int, int]]:
    """M-1: the blocks furniture_band took by their text (a running head's
    words, a header label) that do not lie where the parity prints its
    running head, with the blocks it took for lying in their column or row.
    A running head runs: the same place on at least BAND_MINIMUM_PAGES pages
    of the parity (the blocks' centres within FOLIO_PLACE_SLACK of their
    size); on a scan that sits off the others it lies in the column or row
    of the page's own folio, or outermost on the parity's side (no other
    block of the page beyond it).  A line that reads like the head elsewhere
    on a page - a title, a line of the body - is the page's text."""
    places: dict[str, list[tuple[int, Sequence[float]]]] = {"odd": [], "even": []}
    for scan, found in picked.items():
        for k, why in found.items():
            if why.startswith(TEXT_PICKS):
                places["odd" if scan % 2 else "even"].append((scan, blocks[scan][k]["box"]))

    def near(a: Sequence[float], b: Sequence[float]) -> bool:
        return abs((a[0] + a[2] / 2) - (b[0] + b[2] / 2)) <= FOLIO_PLACE_SLACK * max(abs(a[2]), abs(b[2])) \
            and abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) <= FOLIO_PLACE_SLACK * max(abs(a[3]), abs(b[3]))

    out: set[tuple[int, int]] = set()
    for scan, found in picked.items():
        parity = "odd" if scan % 2 else "even"
        folios = [blocks[scan][k]["box"] for k, why in found.items() if why == "folio"]
        for k, why in found.items():
            if not why.startswith(TEXT_PICKS):
                continue
            box = blocks[scan][k]["box"]
            pages = {other for other, b in places[parity] if other != scan and near(box, b)}
            own = any(_share(box[0], box[0] + box[2], f[0] - FOLIO_PLACE_SLACK * f[2],
                             f[0] + (1 + FOLIO_PLACE_SLACK) * f[2]) >= 0.5
                      or _share(box[1], box[1] + box[3], f[1] - FOLIO_PLACE_SLACK * f[3],
                                f[1] + (1 + FOLIO_PLACE_SLACK) * f[3]) >= 0.5 for f in folios)
            if len(pages) + 1 >= BAND_MINIMUM_PAGES or own or outermost(box, k, blocks[scan], sides.get(parity)):
                continue
            out.add((scan, k))
            index = blocks[scan][k].get("index", k)
            for j, other in found.items():
                if re.fullmatch(rf"(?:column|row) of block {index}", other):
                    out.add((scan, j))
    return out


def outermost(box: Sequence[float], k: int, page_blocks: Sequence[Mapping[str, Any]], side: str | None) -> bool:
    """Whether no other block of the page (its text, its folio, another
    head) lies beyond block K (BOX) on the band's SIDE of the page."""
    if side not in ("left", "right"):
        return False
    for j, other in enumerate(page_blocks):
        if j == k:
            continue
        centre = other["box"][0] + other["box"][2] / 2
        if (centre > box[0] + box[2]) if side == "right" else (centre < box[0]):
            return False
    return True


def at_folio_place(box: Sequence[float], strip: Sequence[float], side: str | None) -> bool:
    """Whether a block (BOX) lies where the parity prints its folio (STRIP,
    the median folio box): at least half its width in the strip's column, or
    half its height in the strip's row on the strip's SIDE of the page, the
    strip widened by FOLIO_PLACE_SLACK of its own size on each side (a scan
    sits a little off another)."""
    x, y, w, h = box
    sx, sy, sw, sh = strip
    if _share(x, x + w, sx - FOLIO_PLACE_SLACK * sw, sx + (1 + FOLIO_PLACE_SLACK) * sw) >= 0.5:
        return True
    same_side = side is None or ((x + w / 2 > 0.5) == (side == "right"))
    return same_side and _share(y, y + h, sy - FOLIO_PLACE_SLACK * sh, sy + (1 + FOLIO_PLACE_SLACK) * sh) >= 0.5


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def learn_bands(pages: Mapping[int, Any], blocks: Mapping[int, list[dict[str, Any]]],
                heads: Sequence[str], profile: Mapping[str, Any], grams: set[str]) -> dict[str, Any]:
    """M-1: per parity, where the folio blocks sit, and each page's band."""
    parities: dict[str, Any] = {}
    for parity in ("odd", "even"):
        folios = []
        for scan, page_blocks in blocks.items():
            if (scan % 2 == 1) != (parity == "odd"):
                continue
            for block in folio_blocks(page_blocks):
                folios.append((scan, block["box"]))
        pages_with = sorted({scan for scan, _ in folios})
        entry: dict[str, Any] = {"folioPages": len(pages_with), "needs": BAND_MINIMUM_PAGES,
                                 "passed": len(pages_with) >= BAND_MINIMUM_PAGES}
        if folios:
            xs = [b[0] for _, b in folios]
            ws = [b[2] for _, b in folios]
            ys = [b[1] for _, b in folios]
            hs = [b[3] for _, b in folios]
            entry["folioBox"] = [round(_median(xs), 4), round(_median(ys), 4), round(_median(ws), 4),
                                 round(_median(hs), 4)]
            centre = _median([x + w / 2 for x, w in zip(xs, ws)])
            entry["side"] = "right" if centre > 0.5 else "left"
        parities[parity] = entry

    arguments = SimpleNamespace(profile_data=profile)
    per_page: dict[int, list[dict[str, Any]]] = {}
    orientation_votes: Counter = Counter()
    picked: dict[int, dict[int, str]] = {}
    for scan, page_blocks in blocks.items():
        parity = "odd" if scan % 2 else "even"
        try:
            band = pp.furniture_band(page_blocks, heads, arguments, grams)
        except Exception:  # noqa: BLE001 - a malformed block is not the band's
            band = {}
        strip = parities[parity].get("folioBox") if parities[parity]["passed"] else None
        picked[scan] = {}
        for k, why in band.items():
            # furniture_band takes every number block reading numerals for a
            # folio, wherever it lies.  A numeral set in the body (a section
            # number at the far side of the page, figures across the foot of
            # a contents page) is not the band's: a folio is kept only in the
            # parity's folio column, or in its row on its side of the page.
            if why == "folio" and strip and not at_folio_place(page_blocks[k]["box"], strip,
                                                                parities[parity].get("side")):
                continue
            picked[scan][k] = why
    dropped_heads = unplaced_heads(picked, blocks, {name: entry.get("side") for name, entry in parities.items()})
    for scan, page_blocks in blocks.items():
        parity = "odd" if scan % 2 else "even"
        strip = parities[parity].get("folioBox") if parities[parity]["passed"] else None
        found: dict[int, str] = {k: f"furniture_band: {why}" for k, why in picked[scan].items()
                                 if (scan, k) not in dropped_heads}
        folio_boxes = [b["box"] for b in folio_blocks(page_blocks)
                       if not strip or at_folio_place(b["box"], strip, parities[parity].get("side"))]
        # The band's column on this page is taken from the page's own band
        # blocks (the folio and what furniture_band took by its text) that
        # are one column wide - no wider than BAND_COLUMN_SPAN times the
        # parity's folio - and from the parity's folio strip only on a page
        # with none: a scan sits a column's width off another now and then,
        # so the strip can cover the page's first body column while the
        # page's own head lies further out; and figures set across the foot
        # of several columns (read as one number block) are no column at all.
        column_width = strip[2] if strip else None
        own = [page_blocks[k]["box"] for k in sorted(found)
               if column_width and abs(page_blocks[k]["box"][2]) <= BAND_COLUMN_SPAN * column_width]
        columns = own or ([strip] if strip else [])
        rows = [box for box in folio_boxes] or ([strip] if strip else [])
        side = parities[parity].get("side")
        # A block is at the outer edge when no other block that is not the
        # band's lies beyond it - any block, whatever its size: on a page of
        # short blocks (a contents page, a list) every block is short.
        others = {k: b["box"][0] + b["box"][2] / 2 for k, b in enumerate(page_blocks) if k not in found}
        for k, block in enumerate(page_blocks):
            if k in found or block.get("label") == "table":
                continue
            x, y, w, h = block["box"]
            size = len(pp.CJK(block["text"]))
            if size > pp.MARGIN_FURNITURE_LIMIT or abs(w) * abs(h) > pp.MARGIN_FURNITURE_AREA:
                continue
            if any(_share(x, x + w, ax, ax + aw) >= 0.5 for ax, _, aw, _ in columns):
                beyond = [j for j, c in others.items() if j != k
                          and (c > x + w if side == "right" else c < x if side == "left" else True)]
                if not beyond:
                    found[k] = "folio's column, outer edge"
                    orientation_votes["column"] += 1
                continue
            if any(_share(y, y + h, ay, ay + ah) >= 0.5 for _, ay, _, ah in rows) and w > h:
                found[k] = "folio's row"
                orientation_votes["row"] += 1
        per_page[scan] = [{"block": page_blocks[k].get("index", k), "text": page_blocks[k]["text"],
                           "label": page_blocks[k].get("label"),
                           "box": [round(v, 4) for v in page_blocks[k]["box"]], "why": found[k]}
                          for k in sorted(found)]
    return {"parities": parities, "pages": per_page,
            "orientation": orientation_votes.most_common(1)[0][0] if orientation_votes else None}


def drop_display_heads(band_pages: dict[int, list[dict[str, Any]]], stems: Sequence[str],
                       sizes: Mapping[int, tuple[int, int] | None]) -> list[dict[str, Any]]:
    """M-1: take out of the band a block that holds a stem copy set in
    display type - its glyph (the block's thin side: a vertical line's
    width, a horizontal line's height) DISPLAY_RATIO times the stem's copies'
    median or more - and the blocks furniture_band took for lying in its
    column or row.  A book whose running head is its title prints the title
    large on the title page or a divider: phase 3's furniture_band takes that
    block by its text wherever it lies, and it is the page's own text.  The
    median needs BAND_MINIMUM_PAGES copies of the stem.  Returns what was
    taken out (page, block, text, ratio)."""
    glyphs: dict[str, list[float]] = defaultdict(list)
    found: list[tuple[int, dict[str, Any], str, float]] = []
    for scan, band in band_pages.items():
        size = sizes.get(scan)
        if not size:
            continue
        for entry in band:
            if entry.get("label") == "number":
                continue
            chars = pp.CJK(entry["text"])
            stem = next((stem for stem in stems if stem_matches(chars, stem)), None)
            if stem is None:
                continue
            x, y, w, h = entry["box"]
            glyph = min(abs(w) * size[0], abs(h) * size[1])
            glyphs[stem].append(glyph)
            found.append((scan, entry, stem, glyph))
    medians = {stem: _median(values) for stem, values in glyphs.items() if len(values) >= BAND_MINIMUM_PAGES}
    removed: list[dict[str, Any]] = []
    for scan, entry, stem, glyph in found:
        median = medians.get(stem)
        if not median or glyph < DISPLAY_RATIO * median:
            continue
        band = band_pages[scan]
        gone = {entry["block"]}
        tied = [e for e in band if re.fullmatch(rf"furniture_band: (?:column|row) of block {entry['block']}", e["why"])]
        gone.update(e["block"] for e in tied)
        band_pages[scan] = [e for e in band if e["block"] not in gone]
        removed.append({"page": scan, "block": entry["block"], "text": entry["text"],
                        "ratio": round(glyph / median, 2), "alsoRemoved": [e["block"] for e in tied]})
    return removed


def learn_stems(profile: Mapping[str, Any], band_pages: Mapping[int, list[dict[str, Any]]]) -> dict[str, Any]:
    """M-2: the stems, and how many band pages each is read on."""
    heads = [h for h in (profile.get("layout") or {}).get("runningHeads") or [] if isinstance(h, str) and pp.CJK(h)]
    texts = ["".join(pp.CJK(b["text"]) for b in band if b["label"] != "number") for band in band_pages.values()]
    texts = [t for t in texts if t]
    method = "profile"
    stems = [pp.CJK(h) for h in heads]
    if not stems:
        core = shared_core(texts, len(texts))
        stems = [core] if core else []
        method = "band core"
    counts = {stem: sum(1 for t in texts if stem_matches(t, stem)) for stem in stems}
    floor = max(BAND_MINIMUM_PAGES, math.ceil(STEM_PAGE_SHARE * len(texts))) if texts else BAND_MINIMUM_PAGES
    return {"stems": stems, "method": method, "bandPagesWithText": len(texts), "stemPages": counts,
            "needs": floor, "passed": bool(stems) and any(n >= floor for n in counts.values())}


def head_spans(chars: str, stems: Sequence[str]) -> list[dict[str, Any]]:
    """The stem copies in one engine's stream, with the numeral runs just
    before and just after them."""
    out = []
    for stem in stems:
        for start, end in stem_matches(chars, stem):
            before = start
            while before > 0 and (is_numeral_char(chars[before - 1]) or chars[before - 1] == "口"):
                before -= 1
            out.append({"stem": stem, "start": start, "end": end, "read": chars[start:end],
                        "numeralsBefore": chars[before:start]})
    out.sort(key=lambda s: s["start"])
    return out


def strip_folio(text: str) -> tuple[str, str, str]:
    """TEXT's leading numerals, its middle, and its trailing numerals."""
    lead = 0
    while lead < len(text) and (is_numeral_char(text[lead]) or text[lead] == "口"):
        lead += 1
    trail = len(text)
    while trail > lead and (is_numeral_char(text[trail - 1]) or text[trail - 1] == "口"):
        trail -= 1
    return text[:lead], text[lead:trail], text[trail:]


def folio_readings_of(text: str) -> list[int]:
    """The value of a numeral run read beside the head (口 read for 〇), or
    nothing.  The whole run only: a body numeral glued to the folio makes
    that page's reading wrong, and the offset's agreement share (M-4) is what
    bounds such pages; reading its tails as well would make every two-figure
    folio vote for a second offset."""
    value = numeral_value(text.replace("口", "〇")) if text and len(text) <= 4 else None
    return [value] if value else []


# --- runs, folios, dividers ------------------------------------------------

def learn_runs(observed: Mapping[int, str]) -> list[dict[str, Any]]:
    """M-3: runs of the same suffix on RUN_MINIMUM_PAGES or more consecutive
    observed pages; a shorter group between two runs of one suffix is noise
    and the two are one run."""
    groups: list[list[Any]] = []
    for scan in sorted(observed):
        suffix = observed[scan]
        if groups and groups[-1][0] == suffix:
            groups[-1][1].append(scan)
        else:
            groups.append([suffix, [scan]])
    kept = [g for g in groups if len(g[1]) >= RUN_MINIMUM_PAGES]
    merged: list[list[Any]] = []
    for suffix, scans in kept:
        if merged and merged[-1][0] == suffix:
            merged[-1][1].extend(scans)
        else:
            merged.append([suffix, list(scans)])
    return [{"suffix": suffix, "first": scans[0], "last": scans[-1], "pages": len(scans)}
            for suffix, scans in merged]


def fit_folios(readings: Mapping[int, Sequence[int]], first: int, last: int) -> dict[str, Any]:
    """M-4 for one run: the offsets scan - folio that readings agree on, in
    stretches of pages (a change of offset is a jump: leaves missing or
    repeated), and the share of readable pages that agree."""
    pages = {scan: sorted({scan - v for v in values}) for scan, values in readings.items()
             if first <= scan <= last and values}
    counts: Counter = Counter()
    support: dict[int, list[int]] = defaultdict(list)
    for scan, offsets in pages.items():
        for offset in offsets:
            counts[offset] += 1
            support[offset].append(scan)
    candidates = [o for o, n in counts.most_common() if n >= BAND_MINIMUM_PAGES]
    accepted: list[int] = []
    for offset in candidates:
        scans = sorted(support[offset])
        inside = 0
        for other in accepted:
            others = sorted(support[other])
            trim = len(others) // 20
            lo, hi = others[trim], others[-1 - trim]
            inside += sum(1 for s in scans if lo <= s <= hi)
        # A second offset is a stretch of its own (a jump), not readings
        # scattered inside an accepted stretch (misread folios).
        if inside < 0.2 * len(scans):
            accepted.append(offset)
    stretches = []
    for offset in sorted(accepted, key=lambda o: _median(support[o])):
        scans = sorted(support[offset])
        stretches.append({"offset": offset, "firstSeen": scans[0], "lastSeen": scans[-1], "pages": len(scans)})
    agree = 0
    for scan, offsets in pages.items():
        expected = expected_offsets(stretches, scan)
        if any(o in expected for o in offsets):
            agree += 1
    jumps = []
    for a, b in zip(stretches, stretches[1:]):
        last_value = a["lastSeen"] - a["offset"]
        first_value = b["firstSeen"] - b["offset"]
        jump = {"after": a["lastSeen"], "before": b["firstSeen"], "offsets": [a["offset"], b["offset"]]}
        if b["offset"] < a["offset"]:
            unprinted = (b["firstSeen"] - a["lastSeen"] - 1)
            jump["foliosMissing"] = [last_value + 1 + unprinted if unprinted < 0 else last_value + 1,
                                     first_value - 1]
            jump["missingFrom"] = last_value + 1
            jump["missingTo"] = first_value - 1
            jump["kind"] = "leaves-missing"
        else:
            jump["kind"] = "folios-repeated"
        jumps.append(jump)
    share = agree / len(pages) if pages else 0.0
    return {"stretches": stretches, "jumps": jumps, "readablePages": len(pages), "agreeing": agree,
            "share": round(share, 3), "needs": FOLIO_AGREEMENT,
            "passed": bool(stretches) and share >= FOLIO_AGREEMENT}


def expected_offsets(stretches: Sequence[Mapping[str, Any]], scan: int) -> list[int]:
    """The offsets a page may carry: its stretch's, or both neighbours'
    between two stretches (where a jump falls is not seen)."""
    if not stretches:
        return []
    for k, s in enumerate(stretches):
        if s["firstSeen"] <= scan <= s["lastSeen"]:
            return [s["offset"]]
        if scan < s["firstSeen"]:
            return [s["offset"]] if k == 0 else [stretches[k - 1]["offset"], s["offset"]]
    return [stretches[-1]["offset"]]


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


# --- contents --------------------------------------------------------------

LINE_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)?")


def line_chars(line: str) -> str:
    return pp.CJK(LINE_PREFIX.sub("", line))


def contents_pieces(line: str) -> list[str]:
    """The characters (line_chars) of each printed line LINE holds.  An OCR
    that runs a contents' lines together - a title and the date line set
    under or over it, written on one line, with or without a `<br>` -
    leaves a line holding several (CONTENTS_CUT): a printed line ends after
    leaders and the page number after them; at a `<br>`, or after leaders
    with no page number, only beside a date line - where a date opens what
    follows, or what comes before is a date and at most a note
    (CONTENTS_LEADING_DROP characters: 批, 奉朱批) - for elsewhere they are
    a title printed over two lines, an ellipsis inside a title; and a date
    that opens a piece and runs on into more than a note is a date line run
    on into a title, cut off.  A title that itself opens with a date (a
    chronicle's) is read as a date line and a title too."""
    # The parts between the cuts, each after the page number (or None) of
    # the cut before it.
    parts = CONTENTS_CUT.split(line)
    pieces: list[str] = []
    for k in range(0, len(parts), 2):
        chars = line_chars(parts[k])
        date = OPENING_DATE.match(pieces[-1]) if pieces else None
        if not k or parts[k - 1] or OPENING_DATE.match(chars) \
                or date and len(pieces[-1]) - date.end() <= CONTENTS_LEADING_DROP:
            pieces.append(chars)
        else:
            pieces[-1] += chars
    out = []
    for chars in pieces:
        date = OPENING_DATE.match(chars)
        cut = date.end() if date and len(chars) - date.end() > CONTENTS_LEADING_DROP else len(chars)
        out += [chars[:cut], chars[cut:]]
    return [chars for chars in out if chars]


def entry_match(entry: str, target: str) -> float:
    """How well ENTRY (a contents line) matches the start of TARGET (a line
    of the body): the best ratio with up to CONTENTS_LEADING_DROP leading
    characters dropped from one of them (a number's stand-ins on either
    side), at least three characters of the entry compared.  Its callers ask
    only whether it reaches CONTENTS_SIMILARITY: a comparison that cannot
    (too many of the target's characters are none of the entry's) is not
    made, and a pair that reaches it nowhere gets a ratio below it."""
    best = 0.0
    alphabet = set(entry)
    for de, dt in [(d, 0) for d in range(CONTENTS_LEADING_DROP + 1)] + \
            [(0, d) for d in range(1, CONTENTS_LEADING_DROP + 1)]:
        e = entry[de:]
        t = target[dt:dt + len(e)]
        if len(e) < min(3, len(entry)) or not t or e[0] != t[0]:
            continue
        # At most the target's characters that the entry holds can match.
        shared = len(t) - sum(c not in alphabet for c in t)
        if 2.0 * shared / (len(e) + len(t)) < CONTENTS_SIMILARITY:
            continue
        best = max(best, difflib.SequenceMatcher(None, e, t, autojunk=False).ratio())
    return best


def map_in_order(entries: Sequence[str], targets: Sequence[str],
                 known: dict[tuple[str, str], float] | None = None) -> list[tuple[int, int, float] | None]:
    """Each entry's matching target, the targets taken in order (the longest
    chain of matches whose targets increase).  A target is compared with an
    entry only where entry_match can find them alike: its first character
    is one of the entry's first ones, or the entry's first character one of
    its first ones (the indexes).  KNOWN keeps entry_match's answers for
    another call on the same lines."""
    known = {} if known is None else known
    first: dict[str, list[int]] = defaultdict(list)
    near: dict[str, list[int]] = defaultdict(list)
    for t, text in enumerate(targets):
        if text:
            first[text[0]].append(t)
        for d in range(1, min(CONTENTS_LEADING_DROP, len(text) - 1) + 1):
            near[text[d]].append(t)
    pairs: list[tuple[int, int, float]] = []
    for e, entry in enumerate(entries):
        if len(entry) < 2:
            continue
        found = {t for d in range(min(CONTENTS_LEADING_DROP + 1, len(entry))) for t in first.get(entry[d], ())}
        found.update(near.get(entry[0], ()))
        for t in found:
            key = (entry, targets[t])
            ratio = known.get(key)
            if ratio is None:
                ratio = known[key] = entry_match(entry, targets[t])
            if ratio >= CONTENTS_SIMILARITY:
                pairs.append((e, t, ratio))
    # Longest chain with both indices increasing: a pair follows the first
    # pair (in sorted order) of the longest chains ending at an earlier
    # entry and an earlier target - read from a Fenwick tree over the
    # targets of (length, -pair), which takes an entry's pairs once the
    # next entry's are read.
    pairs.sort()
    tree = [(0, 0)] * (len(targets) + 1)
    best: list[tuple[int, int]] = []  # (length, previous) per pair
    held = 0
    for i, (e, t, _) in enumerate(pairs):
        while pairs[held][0] < e:
            x = pairs[held][1] + 1
            while x < len(tree):
                tree[x] = max(tree[x], (best[held][0], -held))
                x += x & -x
            held += 1
        top, x = (0, 0), t
        while x:
            top = max(top, tree[x])
            x -= x & -x
        best.append((top[0] + 1, -top[1] if top[0] else -1))
    out: list[tuple[int, int, float] | None] = [None] * len(entries)
    if not pairs:
        return out
    k = max(range(len(pairs)), key=lambda i: best[i][0])
    while k >= 0:
        e, t, ratio = pairs[k]
        out[e] = (e, t, ratio)
        k = best[k][1]
    return out


def unbroken(first: str, second: str, body: str, starts: set[int]) -> bool:
    """Whether BODY (the body's lines run together, variant-folded; STARTS,
    where each starts) holds FIRST and SECOND together from the start of
    one of its lines, as a title opens its line (a sentence naming the two
    is no evidence), with no line starting between them (the title may wrap
    elsewhere)."""
    joined = pp.variant_fold(first + second)
    at = body.find(joined)
    while at >= 0 and (at not in starts or at + len(first) in starts):
        at = body.find(joined, at + 1)
    return at >= 0


def wrapped_entries(rows: Sequence[dict[str, Any]], texts: Mapping[int, str], body: str,
                    starts: set[int]) -> list[dict[str, Any]]:
    """ROWS (a contents run's, in order) with each title printed over two
    lines read as one entry: a row alone on its line, which holds no leaders
    and no page number, and the row alone on the line straight after it
    (blank lines between), which ends with its page number, where the body
    (BODY and STARTS) holds the two together from a line's start with no
    line break between them (unbroken).  The entry stands on the first
    line, with the first row's match.  A phase 3 that keeps engine A's line
    breaks on a contents page seals such a title as two lines; read as two
    entries, the first half matches no unit where the body prints the title
    over two lines too (wrapped elsewhere), and the title is not joined
    there (_repair_structure S14).  Where the body breaks its line between
    the two as well, they stay apart: two of the body's lines (a date line,
    a part's or a group's title, and the title after it) cannot be told
    from a title the body wraps at the same place, which stays two
    entries.  Measured 2026-10-11 on three archive books sealed so: 36, 29
    and 21 titles read as one entry; 8 lines the body prints straight
    before the next title stay apart by its line break (4 date lines, on
    a multi-volume archival compilation 2, 3 and 5; a part's title and 3 case groups' titles, on a multi-volume archival compilation
    5); two other books' contents unchanged."""
    alone = Counter((row["page"], row["line"]) for row in rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        one = out[-1] if out else None
        if one is not None and one["page"] == row["page"] \
                and alone[(one["page"], one["line"])] == alone[(row["page"], row["line"])] == 1:
            lines = texts[row["page"]].split("\n")
            first, second = lines[one["line"] - 1], lines[row["line"] - 1]
            if not any(line.strip() for line in lines[one["line"]:row["line"] - 1]) \
                    and not CONTENTS_CUT.search(first) and not CONTENTS_PAGE_END.search(first) \
                    and CONTENTS_PAGE_END.search(second) and unbroken(one["text"], row["text"], body, starts):
                one["text"] += row["text"]
                if "matchedPage" not in one:
                    one["inText"] = True
                continue
        out.append(row)
    return out


def learn_contents(texts: Mapping[int, str], runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """M-6: runs of list-like pages whose lines map, in order, onto the lines
    of the book's other pages.

    A contents is made of its entries: the lines it maps hold most of its
    text.  A page of the body with a title, a date line and a closing
    formula between long paragraphs maps most of its LINES (onto the
    contents that list it, onto other dates) and little of its text, and so
    does a chapter's first page; such a run is no contents, and such a page
    at the end of a run is dropped from it by its own character share, as a
    page mapping too few lines is, before the run is judged (joined to the
    contents, it would dilute the run's characters and lose the contents
    whole).  And the entries of a contents stand on more than one page: a
    run whose mapped lines all stand on one other page is that page printed
    again (a cover and its title page), and so is a run of one page more
    than half of whose mapped lines stand on one other page
    (CONTENTS_ONE_PAGE; not a longer run, where a part-title page listing
    its units could draw most of the mapping, the map taking the earlier
    of two equal targets): a title
    page whose title, subtitle and author the half-title prints again and
    whose series line a page at the back repeats (a historical monograph scan 1, mapped 3 of
    its 4 lines onto scan 2 once a re-proofread colophon held the title as a
    heading: its title was made an entry and its dash dropped), or a body
    page of unit titles the contents lists (a multi-volume archival compilation 5 scan 131, 5 of 7 onto the
    contents page 8).  Measured 2026-10-13 on the six delivered books: the
    contents runs put at most 4% of their mapped lines on one page (a historical monograph
    4 of 99).  A run of list-like pages refused
    on its characters or on that count after the trimming is returned under
    `rejected` (the repair tool records a doubt for it: a contents lost
    here would otherwise be lost in silence).  Measured 2026-10-02 on three
    books: the five contents runs map 0.82-0.96 of their characters, the 22
    runs of body pages taken for contents 0.05-0.39, and a title page
    mapped three of its five lines, all onto the cover."""
    # The characters of each printed line a line holds (contents_pieces)
    # and their variant-folded form, taken once.
    lines = {scan: [(n, chars, pp.variant_fold(chars)) for n, line in enumerate(text.split("\n"), 1)
                    for chars in contents_pieces(line)]
             for scan, text in texts.items()}
    listlike = [scan for scan, rows in sorted(lines.items())
                if len(rows) >= CONTENTS_MINIMUM_LINES
                and _median([len(chars) for _, chars, _ in rows]) <= CONTENTS_LINE_LENGTH]
    groups: list[list[int]] = []
    for scan in listlike:
        if groups and scan == groups[-1][-1] + 1:
            groups[-1].append(scan)
        else:
            groups.append([scan])
    found = []
    rejected = []
    for group in groups:
        pages = list(group)
        known: dict[tuple[str, str], float] = {}
        # A list-like page at either end of the group that maps too little
        # - of its lines, or of its characters (a divider, a short list or a
        # unit's first page next to the contents) - is dropped, the worse
        # end first (by how far it falls short of its share), and its lines
        # are the body's again for the pages that stay.
        while True:
            entries = [(scan, n, chars, fold) for scan in pages for n, chars, fold in lines[scan]]
            targets = [(scan, n, chars, fold) for scan in sorted(lines) if scan not in pages
                       for n, chars, fold in lines[scan]]
            # Matched on variant-folded text: a contents line and its title
            # printed in two forms of one character are one title.
            mapped = map_in_order([e[3] for e in entries], [t[3] for t in targets], known)
            per_page = Counter(entries[m[0]][0] for m in mapped if m)
            chars_of = Counter({scan: sum(len(e[2]) for e in entries if e[0] == scan) for scan in pages})
            chars_mapped = Counter()
            for e, m in zip(entries, mapped):
                if m:
                    chars_mapped[e[0]] += len(e[2])
            share_of = {scan: per_page[scan] / max(1, len(lines[scan])) for scan in pages}
            text_of = {scan: chars_mapped[scan] / max(1, chars_of[scan]) for scan in pages}
            short_of = {scan: min(share_of[scan] / CONTENTS_SHARE, text_of[scan] / CONTENTS_TEXT_SHARE)
                        for scan in pages}
            ends = [scan for scan in {pages[0], pages[-1]} if short_of[scan] < 1]
            if not ends or len(pages) == 1 and ends:
                if ends:
                    pages = []
                break
            pages.remove(min(ends, key=lambda scan: (short_of[scan], -scan)))
        if not pages:
            continue
        share = sum(1 for m in mapped if m) / len(entries) if entries else 0.0
        characters = sum(len(e[2]) for e, m in zip(entries, mapped) if m) / max(1, sum(len(e[2]) for e in entries))
        if share < CONTENTS_SHARE or not pages:
            continue
        on_pages = Counter(targets[m[1]][0] for m in mapped if m)
        body_pages = len(on_pages)
        one_page = max(on_pages.values(), default=0) / max(1, sum(on_pages.values()))
        if characters < CONTENTS_TEXT_SHARE or body_pages < 2 or (len(pages) == 1 and one_page > CONTENTS_ONE_PAGE):
            rejected.append({"pages": pages, "share": round(share, 3), "characters": round(characters, 3),
                             "bodyPages": body_pages, "onePage": round(one_page, 3),
                             "why": (f"the lines mapped hold {characters:.2f} of the run's characters, under "
                                     f"{CONTENTS_TEXT_SHARE}") if characters < CONTENTS_TEXT_SHARE
                             else f"every line mapped stands on one other page ({body_pages})" if body_pages < 2
                             else (f"{one_page:.2f} of the lines mapped stand on one other page, over "
                                   f"{CONTENTS_ONE_PAGE}: that page printed again")})
            continue
        body = pp.variant_fold("".join(t[2] for t in targets))
        starts = set(accumulate((len(t[2]) for t in targets), initial=0))
        rows = []
        for (scan, n, chars, _), m in zip(entries, mapped):
            row = {"page": scan, "line": n, "text": chars}
            if m:
                target = targets[m[1]]
                row.update({"matchedPage": target[0], "matchedLine": target[1], "ratio": round(m[2], 3)})
            else:
                # Not at a line's start: the title may run on inside a line
                # (a page whose structure pass failed).
                folded = pp.variant_fold(chars)
                for drop in range(0, min(CONTENTS_LEADING_DROP, max(0, len(folded) - 4)) + 1):
                    if len(folded) - drop >= 4 and folded[drop:] in body:
                        row["inText"] = True
                        break
            rows.append(row)
        rows = wrapped_entries(rows, texts, body, starts)
        word = None
        for run in runs:
            if any(run["first"] <= scan <= run["last"] for scan in pages):
                word = run["suffix"]
        found.append({"pages": pages, "share": round(share, 3), "characters": round(characters, 3), "entries": rows,
                      "contentsWord": word})
    return {"runs": found, "needs": CONTENTS_SHARE, "passed": bool(found), **({"rejected": rejected} if rejected else {})}


# --- stand-ins -------------------------------------------------------------

def standin_counts(a_text: str, b_text: str) -> dict[str, Counter]:
    """Engine B's glyphs read where engine A read no character (inserted in
    the alignment of the two CJK streams, STANDIN_RUN glyphs at most), by
    what engine A printed in that
    gap: figures (`figure`), only a dash (`dash`), only a dot (`dot`), or any
    printed mark or figure (`marked`); `inserted` counts them all."""
    a, b = ra.stream(a_text), ra.stream(b_text)
    counts = {kind: Counter() for kind in ("figure", "dash", "dot", "marked", "inserted")}
    for tag, i1, i2, j1, j2 in ra.opcodes(a.chars, b.chars):
        if tag != "insert" or j2 - j1 > STANDIN_RUN:
            continue
        glyphs = b.chars[j1:j2]
        counts["inserted"].update(glyphs)
        gap = a.gaps[i1] if i1 < len(a.gaps) else ""
        printed = "".join(c for c in gap if not c.isspace())
        if not printed or ra.gap_kind(gap) is None:
            continue
        counts["marked"].update(glyphs)
        if ra.DIGITS.search(gap):
            counts["figure"].update(glyphs)
        elif all(c in ra.DASH_CHARS for c in printed):
            counts["dash"].update(glyphs)
        elif all(c in ra.DOT_CHARS for c in printed):
            counts["dot"].update(glyphs)
    return counts


def body_streams(page: Any, band: Sequence[Mapping[str, Any]], spans: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Engine A's text without its band blocks, and engine B's without its
    head copies (a stem copy with the suffix and numerals read beside it):
    the stand-ins are measured on the body only, where no head is read."""
    band_blocks = {b["block"] for b in band}
    a_text = "\n".join(block["text"] for block in page.engine_a_blocks() if block.get("index") not in band_blocks)
    if not a_text and not page.engine_a_blocks():
        a_text = page.engine_a_text()
    b_text = page.engine_b_text()
    cuts = []
    stream = ra.stream(b_text)
    for span in spans:
        start = span.get("startAll", span["start"])
        end = span.get("endAll", span["end"])
        if 0 <= start < end <= len(stream.offsets):
            cuts.append((stream.offsets[start], stream.offsets[end - 1] + 1))
    for start, end in sorted(cuts, reverse=True):
        b_text = b_text[:start] + b_text[end:]
    return a_text, b_text


def _standin_page(args: tuple[str, str]) -> dict[str, Counter]:
    return standin_counts(*args)


# --- the model --------------------------------------------------------------

def learn(book: Any, workers: int = 1) -> dict[str, Any]:
    """The book model of BOOK (a _repair_inputs.Book), every page it holds."""
    # The whole book, whatever pages are under repair.
    scans = sorted(book.pages)
    profile = book.profile or {}
    blocks = {scan: book.pages[scan].engine_a_blocks() for scan in scans}
    heads = [pp.CJK(h) for h in (profile.get("layout") or {}).get("runningHeads") or [] if pp.CJK(h)]
    grams = pp.furniture_grams([book.pages[s].paths["a_txt"] for s in scans if book.pages[s].paths["a_txt"].is_file()])
    bands = learn_bands(book.pages, blocks, heads, profile, grams)
    stems = learn_stems(profile, bands["pages"])
    bands["display"] = drop_display_heads(bands["pages"], stems["stems"],
                                          {scan: page_size(book.pages[scan]) for scan in scans})
    gates: dict[str, Any] = {"M-1": {"passed": all(p["passed"] for p in bands["parities"].values()),
                                     "parities": bands["parities"]},
                             "M-2": {k: stems[k] for k in ("passed", "needs", "stemPages", "method")}}

    # Band text per page, engine B's head copies, and the misread alphabet.
    misreads: dict[str, Counter] = defaultdict(Counter)
    observed: dict[int, str] = {}
    folio_reads: dict[int, list[int]] = {}
    pages_out: dict[str, dict[str, Any]] = {}
    b_spans: dict[int, list[dict[str, Any]]] = {}
    a_remainders: dict[int, str] = {}
    for scan in scans:
        band = bands["pages"].get(scan, [])
        reads: list[int] = []
        remainder = ""
        for entry in band:
            chars = pp.CJK(entry["text"])
            if entry["label"] == "number" or (chars and all(is_numeral_char(c) for c in chars)):
                value = numeral_value(chars or entry["text"])
                if value:
                    reads.append(value)
                continue
            rest = chars
            for stem in stems["stems"]:
                for start, end in stem_matches(rest, stem):
                    for x, y in zip(rest[start:end], stem):
                        if x != y:
                            misreads[y][x] += 1
                    rest = rest[:start] + "\0" * (end - start) + rest[end:]
            lead, middle, trail = strip_folio(rest.replace("\0", ""))
            for part in (lead, trail):
                reads.extend(folio_readings_of(part))
            remainder += middle
        if remainder:
            a_remainders[scan] = remainder
        spans = head_spans(pp.CJK(book.pages[scan].engine_b_text()), stems["stems"])
        b_spans[scan] = spans
        folio_reads[scan] = reads
    suffix_candidates = Counter(a_remainders.values())
    known = {s for s, n in suffix_candidates.items() if n >= RUN_MINIMUM_PAGES}
    # Engine B writes the band's marks as glyphs (its mark aliases, M-7a):
    # between the stem, the suffix and the folio they are skipped.
    pairs = [(book.pages[s].paths["a_txt"], book.pages[s].paths["b_txt"]) for s in scans
             if book.pages[s].paths["a_txt"].is_file() and book.pages[s].paths["b_txt"].is_file()]
    aliases = pp.mark_aliases(pp.mark_evidence(pairs))
    skippable = set(aliases.get("B", {})) - set(DIGIT_VALUE) - {"口"}

    def skip(text: str, at: int) -> int:
        while at < len(text) and text[at] in skippable:
            at += 1
        return at

    for scan in scans:
        chars = pp.CJK(book.pages[scan].engine_b_text())
        for span in b_spans[scan]:
            for x, y in zip(span["read"], span["stem"]):
                if x != y and len(span["read"]) == len(span["stem"]):
                    misreads[y][x] += 1
            at = skip(chars, span["end"])
            suffix = next((s for s in sorted(known, key=len, reverse=True) if chars.startswith(s, at)), None)
            span["suffix"] = suffix
            if suffix:
                at = skip(chars, at + len(suffix))
            numerals = ""
            while at < len(chars) and (is_numeral_char(chars[at]) or chars[at] == "口"):
                numerals += chars[at]
                at += 1
            span["numeralsAfter"] = numerals
            span["endAll"] = at if (suffix or numerals) else span["end"]
            if suffix is None and at >= len(chars):
                _, middle, _ = strip_folio(chars[skip(chars, span["end"]):])
                if middle:
                    span["suffixAtEnd"] = middle
                    span["endAll"] = len(chars)
            before = span["start"]
            while before > 0 and chars[before - 1] in skippable:
                before -= 1
            lead = before
            while lead > 0 and (is_numeral_char(chars[lead - 1]) or chars[lead - 1] == "口"):
                lead -= 1
            if lead < before:
                span["numeralsBefore"] = chars[lead:before]
                span["startAll"] = lead
            else:
                span["startAll"] = span["start"]
            folio_reads[scan].extend(folio_readings_of(span["numeralsBefore"]))
            folio_reads[scan].extend(folio_readings_of(numerals))
        suffix = a_remainders.get(scan)
        if not suffix:
            b_suffixes = [s.get("suffix") or s.get("suffixAtEnd") for s in b_spans[scan]]
            b_suffixes = [s for s in b_suffixes if s]
            suffix = Counter(b_suffixes).most_common(1)[0][0] if b_suffixes else None
        if suffix and len(suffix) <= 8:
            observed[scan] = suffix
    runs = learn_runs(observed)
    gates["M-3"] = {"passed": bool(runs), "needs": RUN_MINIMUM_PAGES, "runs": len(runs)}

    # M-4: the folio sequence per run.
    folio_runs = []
    for k, run in enumerate(runs):
        next_first = runs[k + 1]["first"] if k + 1 < len(runs) else max(scans) + 1
        prev_last = runs[k - 1]["last"] if k else min(scans) - 1
        fit = fit_folios(folio_reads, prev_last + 1, next_first - 1)
        folio_runs.append({"suffix": run["suffix"], "first": run["first"], "last": run["last"],
                           "span": [prev_last + 1, next_first - 1], **fit})
    gates["M-4"] = {"passed": bool(folio_runs) and all(r["passed"] for r in folio_runs), "needs": FOLIO_AGREEMENT,
                    "runs": [{k: r[k] for k in ("suffix", "share", "readablePages", "agreeing", "passed")}
                             for r in folio_runs]}

    # M-9: the body glyph.
    glyph_sizes = []
    for scan in scans:
        size = page_size(book.pages[scan])
        if not size:
            continue
        width, height = size
        band_blocks = {b["block"] for b in bands["pages"].get(scan, [])}
        for block in blocks[scan]:
            if block.get("index") in band_blocks or block.get("label") in TITLE_LABELS | pp.FURNITURE_LABELS:
                continue
            chars = len(pp.CJK(block["text"]))
            if chars < 5:
                continue
            w, h = abs(block["box"][2]) * width, abs(block["box"][3]) * height
            per = max(w, h) / chars
            if min(w, h) <= 1.6 * per:
                glyph_sizes.append(per)
    glyph = round(_median(glyph_sizes), 2) if glyph_sizes else None

    # M-5: dividers.
    dividers = []
    for k, run in enumerate(runs):
        stretches = folio_runs[k]["stretches"]
        candidates = {run["first"], run["first"] - 1}
        if stretches:
            candidates.add(stretches[0]["offset"] + 1)
        for scan in sorted(c for c in candidates if c in book.pages):
            size = page_size(book.pages[scan])
            band_blocks = {b["block"] for b in bands["pages"].get(scan, [])}
            for block in blocks[scan]:
                chars = pp.CJK(block["text"])
                if block.get("index") in band_blocks or not chars or len(chars) > len(run["suffix"]) + 1:
                    continue
                if len(run["suffix"]) < 2 or levenshtein(chars, run["suffix"]) > 1:
                    continue
                ratio = None
                if size and glyph:
                    w, h = abs(block["box"][2]) * size[0], abs(block["box"][3]) * size[1]
                    ratio = round(max(w, h) / len(chars) / glyph, 2)
                display = (ratio is not None and ratio >= DISPLAY_RATIO) or block.get("label") in TITLE_LABELS
                if display:
                    dividers.append({"suffix": run["suffix"], "page": scan, "read": block["text"],
                                     "label": block.get("label"), "glyphRatio": ratio})
                    break
    divider_pages = {d["page"] for d in dividers}

    # M-6: contents.
    contents = learn_contents({scan: book.pages[scan].sealed_text for scan in scans}, runs)
    contents_pages = sorted({p for run in contents["runs"] for p in run["pages"]})
    gates["M-6"] = {"passed": contents["passed"], "needs": CONTENTS_SHARE,
                    "runs": [{"pages": r["pages"], "share": r["share"], "characters": r["characters"]}
                             for r in contents["runs"]],
                    **({"rejected": contents["rejected"]} if contents.get("rejected") else {})}

    # M-7: engine B's stand-ins.
    jobs = [body_streams(book.pages[s], bands["pages"].get(s, []), b_spans.get(s, [])) for s in scans]
    totals = {kind: Counter() for kind in ("figure", "dash", "dot", "marked", "inserted")}
    if workers > 1 and len(jobs) > 8:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_standin_page, jobs, chunksize=8))
    else:
        results = [_standin_page(job) for job in jobs]
    for result in results:
        for kind, counter in result.items():
            totals[kind].update(counter)

    def stands_in(glyph: str) -> bool:
        return totals["marked"][glyph] >= STANDIN_SHARE * totals["inserted"][glyph]

    at_figures = sorted(g for g, n in totals["figure"].items()
                        if n >= FIGURE_STANDIN_MINIMUM and n >= FIGURE_SHARE_OF_MARKED * totals["marked"][g]
                        and stands_in(g))
    # A CJK numeral where engine A read a figure is that figure read in the
    # other script, not a glyph standing in for print engine B could not
    # read: which of the two the page prints is a reading of that spot (the
    # figure rules decide it from engine A's figure and its place, or name it
    # in a doubt).
    # As a stand-in it would make every numeral engine A did not align - a
    # date, an amount, a number in a name - count as one.
    figure = [g for g in at_figures if g not in NUMERALS]
    dash = sorted(g for g, n in totals["dash"].items() if n >= pp.MARK_ALIAS_MINIMUM and stands_in(g))
    dot = sorted(g for g, n in totals["dot"].items() if n >= pp.MARK_ALIAS_MINIMUM and stands_in(g))
    standins = {
        "markAliases": {side: {g: dict(marks) for g, marks in found.items()} for side, found in aliases.items()},
        "figure": figure, "dash": dash, "dot": dot,
        "numeralsAtFigures": [g for g in at_figures if g in NUMERALS],
        "counts": {g: {kind: totals[kind][g] for kind in totals if totals[kind][g]}
                   for g in sorted(set(at_figures) | set(dash) | set(dot) | set(aliases.get("B", {})))},
    }

    # M-10: sentence-final marks and the date shape.
    notation = [m for m in (profile.get("notation") or {}).get("marks") or [] if isinstance(m, str)]
    sealed_all = "".join(book.pages[s].sealed_text for s in scans)
    finals = [m for m in SENTENCE_FINAL if m in notation or sealed_all.count(m) >= BAND_MINIMUM_PAGES]
    gates["M-10"] = {"passed": bool(notation), "notation": notation, "sentenceFinal": finals}

    # Per page: band, head copies, suffix, run, expected folio.
    for scan in scans:
        run_index = next((k for k, r in enumerate(folio_runs) if r["span"][0] <= scan <= r["span"][1]), None)
        expected = None
        candidates: list[int] = []
        if run_index is not None and folio_runs[run_index]["passed"]:
            offsets = expected_offsets(folio_runs[run_index]["stretches"], scan)
            candidates = sorted({scan - o for o in offsets if scan - o >= 1})
            expected = candidates[0] if len(candidates) == 1 else None
        pages_out[str(scan)] = {
            "band": bands["pages"].get(scan, []),
            "headSpansB": b_spans.get(scan, []),
            "suffixRead": observed.get(scan),
            "run": run_index,
            "suffix": folio_runs[run_index]["suffix"] if run_index is not None else None,
            "folioReadings": sorted(set(folio_reads.get(scan, []))),
            "folio": expected,
            "folioCandidates": candidates,
            "divider": scan in divider_pages,
            "contents": scan in contents_pages,
        }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "gates": gates,
        "band": {"parities": bands["parities"], "orientation": bands["orientation"],
                 "displayHeads": bands.get("display") or []},
        "stems": stems["stems"],
        "stemMethod": stems["method"],
        "stemMisreads": {y: dict(c.most_common()) for y, c in sorted(misreads.items())},
        "suffixRuns": [{"suffix": r["suffix"], "first": r["first"], "last": r["last"], "pages": r["pages"]}
                       for r in runs],
        "folio": {"runs": folio_runs,
                  "jumps": [dict(j, suffix=r["suffix"]) for r in folio_runs for j in r["jumps"]]},
        "dividers": dividers,
        "contents": contents,
        "contentsPages": contents_pages,
        "standIns": standins,
        "glyph": glyph,
        "sentenceFinal": finals,
        "dateShape": DATE_SHAPE.pattern,
        "pages": pages_out,
    }


def gate(model: Mapping[str, Any], name: str) -> bool:
    return bool((model.get("gates") or {}).get(name, {}).get("passed"))


def page_entry(model: Mapping[str, Any], scan: int) -> dict[str, Any]:
    return (model.get("pages") or {}).get(str(scan)) or {}


def standin_glyphs(model: Mapping[str, Any]) -> set[str]:
    """Every glyph M-7 learned as engine B's stand-in."""
    s = model.get("standIns") or {}
    return set((s.get("markAliases") or {}).get("B", {})) | set(s.get("figure") or []) \
        | set(s.get("dash") or []) | set(s.get("dot") or [])
