"""Running head learning, margin furniture filtering, folio detection, and loop quarantining."""

from __future__ import annotations

from collections import Counter
import difflib
import itertools
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence


from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes,
)
from jyut_ocr.proofread.core import (  # noqa: E402
    ANSWER_DELETION_LIMIT, CJK, DEGENERATE_NGRAM_RATIO, DELETED_RUN_LIMIT, DROP_GRAM,
    FOLIO_MAX_FIGURES, FURNITURE_LABELS, HEAD_SIZE_RATIO, INSERTION_LIMIT, LONE_FOLIO,
    LOOPED_READING, NUMERAL_CHARS, UNCORROBORATED_MINIMUM, WHOLE_PAGE_FURNITURE_LABELS,
    distinct_ngram_ratio, layout_index, profile_section, strip_grounding,
)
from jyut_ocr.proofread.variants import variant_fold  # noqa: E402
from jyut_ocr.proofread.adjudicate import (  # noqa: E402
    align_drafts, fuse_numeral_runs, gram_counts,
)


def is_furniture_text(text: str, heads: Sequence[str] = (),
                      furniture: frozenset[str] | set[str] = frozenset()) -> bool:
    """Whether a table row's text is page furniture - the running head.

    Evidence the page run already has: the book profile's measured running
    heads, and the n-grams that recur across the book's pages (the deletion
    guard's furniture, same test and same leftover limit).
    """
    text = CJK(text)
    return bool(text) and bool((heads and is_running_head(text, heads))
                               or (furniture and _is_furniture(text, furniture, DELETED_RUN_LIMIT)))


def is_running_head(text: str, heads: Sequence[str], similarity: float = 0.8) -> bool:
    """Whether a block's text is one of the book's measured running heads.

    Similarity rather than equality: the same head is OCR'd a little
    differently from copy to copy, and an exact test left misread copies in
    the body.  The length check stops a sentence that merely contains the
    head from being taken for it.
    """
    text = CJK(text)
    for head in heads:
        if not head or not text:
            continue
        if abs(len(text) - len(head)) > max(1, len(head) // 4):
            continue
        if text == head or difflib.SequenceMatcher(None, text, head, autojunk=False).ratio() >= similarity:
            return True
    return False


def head_size_matches(block: Mapping[str, Any], text: str, arguments: Any) -> bool:
    """Whether a block matching a running head is set in the head's own type size."""
    glyph = profile_section(arguments, "layout").get("runningHeadGlyph") or {}
    heads = [h for h in glyph if is_running_head(text, [h])]
    box = block.get("box")
    if not heads or not (isinstance(box, list) and len(box) == 4):
        return not glyph  # profiles without sizes keep the text-only test
    size = max(abs(box[2]), abs(box[3])) / max(len(CJK(text)), 1)
    return any(HEAD_SIZE_RATIO[0] <= size / glyph[h] <= HEAD_SIZE_RATIO[1] for h in heads if glyph[h])


def removable_text(blocks: Sequence[Mapping[str, Any]], punctuated: Sequence[str], format_input: str,
                   heads: Iterable[str], figures: Iterable[str]) -> str:
    """What the structure pass may delete of the letters and figures it is
    given (verify_characters' REMOVABLE): the text of the blocks labelled
    furniture (FURNITURE_LABELS, a table's excepted, as the fallback leaves
    them out) and of the blocks in a folio's margin band (margin_blocks), the
    book's known heads (HEADS), each line of FORMAT_INPUT that is a lone folio
    (LONE_FOLIO), and FIGURES, which restore_arabic_figures writes back where
    the pass left them out.  BLOCKS and PUNCTUATED go together (the block
    path).  On the whole-page path PUNCTUATED is empty and the merged text
    holds the furniture as the engines read it: the blocks' own text is the
    evidence there, those labelled WHOLE_PAGE_FURNITURE_LABELS and those in a
    folio's margin band.  A figure's or an image's text is not removable
    there, since the whole-page fallback keeps it."""
    margin = {id(block) for block in margin_blocks(blocks)}
    if punctuated:
        parts = [text for block, text in zip(blocks, punctuated)
                 if (block.get("label") in FURNITURE_LABELS and block.get("label") != "table")
                 or id(block) in margin]
    else:
        parts = [str(block.get("text") or "") for block in blocks
                 if block.get("label") in WHOLE_PAGE_FURNITURE_LABELS or id(block) in margin]
    parts += [line for line in format_input.split("\n")
              if line.strip() and re.fullmatch(LONE_FOLIO % FOLIO_MAX_FIGURES, line.strip(), flags=re.IGNORECASE)]
    return "\n".join([*parts, *heads, *figures])


# A blank leaf is far below the sparsest real page once rules are discounted.
# Used only as a recorded measurement and a tiebreaker - never as the sole
# gate, because it does not separate cleanly across scans: an empty ruled leaf
# of the corpus's 1910 government report measures 0.024 against 0.0065 for a genuinely sparse
# text page of a historical monograph.  The reliable signal is two engines, not pixels.
BLANK_INK_FRACTION = 0.004
UNCORROBORATED_WITNESS = 10


def engine_b_cut_off(draft_text: Path | None) -> str | None:
    """Why engine B's draft at DRAFT_TEXT is cut off: its record (the .json
    beside it, run_hunyuanocr's) marks it truncated - the reply ended for a
    reason other than "stop" - and this is that finish reason ("truncated"
    when the record names none).  None for a draft whose reply ended, and for
    one with no such record (the old skill's drafts, a Paddle draft)."""
    if draft_text is None:
        return None
    try:
        record = json.loads(draft_text.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("truncated") is not True:
        return None
    reason = record.get("finishReason")
    return reason if isinstance(reason, str) and reason else "truncated"


# Engine B's loops (build_draft.py cuts the draft at them; phase 3 cuts engine
# B's cut-off reading at them before merging it).  Engine B sometimes does not
# stop: from some point on a page it writes one stretch back to back until its
# tokens run out.  Measured on engine B's drafts of the calibration corpus
# (1,279 distinct page files, eight books): 18 pages hold a stretch of
# LOOP_MIN_COPIES or more copies of a unit of at most LOOP_PERIOD_MAX
# characters that holds text, LOOP_MIN_CHARS characters or more in all - each
# 3,958 to 12,517 characters, the unit one character (a page of 口) up to a
# table row of 150, every one running to the file's end, its last copy cut
# short - and the longest such repetition under LOOP_MIN_CHARS is 120
# characters: one table cell printed ten times over, which is text.  A unit of
# markup alone (empty table cells: 90 characters at most in engine B's drafts)
# holds no text to lose and may be a table's printed shape: kept.
LOOP_PERIOD_MAX = 200
LOOP_MIN_COPIES = 5
LOOP_MIN_CHARS = 1000
LOOP_PATTERN = re.compile(r"(.{1,%d}?)\1{%d,}" % (LOOP_PERIOD_MAX, LOOP_MIN_COPIES - 1), re.DOTALL)
LOOP_MARKUP = re.compile(r"<[^<>]*>")


def find_loops(text: str) -> list[tuple[int, int, str]]:
    """(start, end, unit) of each loop in TEXT: a unit of at most
    LOOP_PERIOD_MAX characters, holding text outside markup, back to back
    LOOP_MIN_COPIES times or more and LOOP_MIN_CHARS characters or more in
    all.  A last copy cut short is part of the loop only where nothing but
    blank follows it (the engine's tokens ran out in the middle of it); text
    after it is the page's."""
    if len(text) < LOOP_MIN_CHARS:
        return []
    found = []
    at = 0
    while True:
        match = LOOP_PATTERN.search(text, at)
        if match is None:
            return found
        start, end, unit = match.start(), match.end(), match.group(1)
        if not loop_holds_text(unit):
            at = end                  # markup alone: no stretch of it holds text
            continue
        if end - start < LOOP_MIN_CHARS:
            # The shortest unit repeating here may not be the loop's: a loop
            # can start with a run of one character.  Look again one on.
            at = start + 1
            continue
        tail = 0
        while tail < len(unit) - 1 and end + tail < len(text) and text[end + tail] == unit[tail]:
            tail += 1
        if tail and not text[end + tail:].strip():
            end += tail
        found.append((start, end, unit))
        at = end


def loop_unit_from_tag(unit: str) -> str:
    """A loop's UNIT read from its first < on, where a tag starts.  A unit
    is any rotation of the stretch that repeats - it starts where the loop
    does, taking in a tag's closing > of the text before it - and read from
    a tag's start its tags are whole."""
    at = unit.find("<")
    return unit[at:] + unit[:at] if at > 0 else unit


def loop_holds_text(unit: str) -> bool:
    """Whether a loop's UNIT holds anything but markup."""
    return bool(LOOP_MARKUP.sub("", loop_unit_from_tag(unit)).strip())


def cut_engine_b_loops(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Engine B's reading TEXT with each loop (find_loops) cut, and the loops
    cut, as (start, end, unit) in TEXT.  Cut as the draft cuts it
    (build_draft.cut_loops): the unit kept once where the loop starts, then
    the text after the loop, if any, on a line of its own.

    Except inside a table: a loop in a table engine B opened on the loop's
    own line (it writes a table a line) keeps the rows it closed before the
    loop, the table closed after them, and nothing of the row the loop is in.
    That row is the loop's run-up, not a reading: measured on the three
    cut-off pages of a fiscal table printed over six pages, a second table
    opened after the page's whole table, its first cell 「十 九」「十 九 年 度
    十八、八、七、九、二、三…」 or 「九〇〇四九〇〇八七〇〇〇…」 running into the
    loop; cut at the loop, 「十九〇二〇」 stood after the page's table as text
    engine B alone read, and the table no longer ended the page, so its stitch
    to the next page was not tried."""
    found = find_loops(text)
    if not found:
        return text, []
    pieces, at = [], 0
    for start, end, unit in found:
        line = max(at, text.rfind("\n", 0, start) + 1)
        opened = text.rfind("<table", line, start)
        if opened >= 0 and text.rfind("</table>", opened, start) < 0:
            closed = text.rfind("</tr>", opened, start)
            head = (text[at:closed + len("</tr>")] + "</table>" if closed >= 0 else text[at:opened])
        else:
            head = text[at:start + len(unit)]
        pieces.append(head.rstrip())
        at = end
    pieces.append(text[at:].strip())
    return "\n".join(piece for piece in pieces if piece.strip()), found


def unsupported_reading(draft: str, draft2: str | None) -> str:
    """The text one engine returned for a page the other did not see.

    Returns the unsupported text, or "" when the reading is corroborated.

    On the corpus's 1910 government report p.73 - an empty ruled leaf - PaddleOCR emitted
    4,244 characters of 欽定四庫全書卷一卷二卷三… while HunyuanOCR read nothing.
    Neither test catches that safely on its own: p.48 of the same book is a
    genuine page whose repetition score is LOWER still, and a page legitimately
    read by one engine alone must not be thrown away.  Both together are safe.
    """
    first, second = CJK(draft), CJK(draft2 or "")
    longer, shorter = (first, second) if len(first) >= len(second) else (second, first)
    if len(longer) < UNCORROBORATED_MINIMUM or len(shorter) >= UNCORROBORATED_WITNESS:
        return ""
    if distinct_ngram_ratio(longer) > DEGENERATE_NGRAM_RATIO:
        return ""
    return longer


# The layout labels of page furniture (running head, folio, seal): text in such
# a block of engine A's layout does not make a page hold text.
PAGE_FURNITURE_LABELS = frozenset({"header", "footer", "number", "seal", "header_image", "footer_image"})


def non_cjk_text(draft: str, draft2: str | None, blocks: Sequence[Mapping[str, Any]]) -> str:
    """The text a page's drafts read past its furniture, on a page where
    neither holds CJK: a title page in Latin letters, a page of figures, a
    formula.  "" when there is none, and the page is blank.

    Engine A's blocks labelled as furniture are left out (when its layout
    accounts for its draft), and so is a draft that is one lone folio (up to
    FOLIO_MAX_FIGURES figures, or a roman numeral, dashes or brackets round
    it).  Markup tags and engine B's grounding boxes are not text.  What is
    left is text when it holds a letter or a figure.

    The blank-page rule asked only for CJK, so such a page was sealed empty
    and clean: every word of it lost, and nothing said so."""
    if blocks and "\n".join(str(b["text"]) for b in blocks).strip() == draft.strip():
        draft = "\n".join(str(b["text"]) for b in blocks if b.get("label") not in PAGE_FURNITURE_LABELS)
    kept = []
    for text in (draft, draft2 or ""):
        text = re.sub(r"<[^<>]*>", " ", strip_grounding(text)).strip()
        if re.fullmatch(LONE_FOLIO % FOLIO_MAX_FIGURES, text, flags=re.IGNORECASE):
            continue
        if any(c.isalnum() for c in text):
            kept.append(text)
    return "\n".join(kept)


def looped_blocks(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str | None) -> list[int]:
    """Engine-A blocks whose reading is a repetition loop engine B does not share.

    unsupported_reading's two tests, block by block: long and repeating itself
    (more than UNCORROBORATED_MINIMUM characters, fewer than DEGENERATE_NGRAM_RATIO
    distinct 4-grams), and not corroborated - engine B reads the block's most
    repeated 4-gram fewer times than the block does, so a printed table of
    repeating boilerplate that engine B reads too is not taken for a loop.  Only
    when the blocks account for engine A's text and engine B's own page is not
    a loop.  Measured over the 794 pages of six books with both drafts: three
    blocks are this long and repetitive, every one a loop whose most repeated
    4-gram engine B never read - a chart's cells repeating (one 4-gram 415
    times) beside a clean leaf (3,677 characters, 0.072) and a title list
    over an empty ruled area (4,244, 0.032), both on pages sent whole to
    engine B's text, and the same list on a page engine B left blank (an
    unsupported_reading).
    """
    if not blocks or draft2 is None or sum(len(CJK(str(b["text"]))) for b in blocks) != len(CJK(draft)):
        return []
    other = CJK(draft2)
    if len(other) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(other) < DEGENERATE_NGRAM_RATIO:
        return []
    out = []
    for index, block in enumerate(blocks):
        text = CJK(str(block["text"]))
        if len(text) <= UNCORROBORATED_MINIMUM or distinct_ngram_ratio(text) >= DEGENERATE_NGRAM_RATIO:
            continue
        top, count = Counter(text[i:i + 4] for i in range(len(text) - 3)).most_common(1)[0]
        # Counted the same way on both sides, overlapping: str.count skips
        # overlaps, so a printed repetition of period two or three (〇〇 cells,
        # a repeated field) that engine B reads just as often counted half or a
        # third as many there and was taken for a loop.
        if sum(other.startswith(top, i) for i in range(len(other) - 3)) < count:
            out.append(index)
    return out


def quarantine_looped_block(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str,
                            prefer: str | None = None, folded: bool | None = None) -> dict[str, Any] | None:
    """Engine A's page with its one looped block set aside, or None.

    A loop in one layout block made the whole page an order conflict and sent it
    to engine B's bare text (the loop outweighs the page, and the page's reading
    counts as looped): measured on one page, a 3,677-character loop in the lower
    leaf's block threw away engine A's clean upper leaf, whose 475 of 495
    characters align with engine B in order.  Here the block's text is emptied
    - its box kept, so its crop still frames the region, and its label kept in
    `quarantined` while it is handled as text, since whatever it holds now is
    engine B's plain reading - engine A's stream is rebuilt from the blocks and
    aligned with engine B again.  Engine
    B's reading of the region is the stretch only engine B read that shares
    more 4-grams with the loop's own text (a loop usually starts as a reading
    of the region before it repeats: 146 of the 336 there) than with the rest
    of engine A's page; it is kept as the block's text, the block moved to the
    block boundary where engine B read it so the page follows engine B's order,
    and left out of the agreement's denominator.  With none, the block stays
    empty where it was: nothing engine B read alone is the region's (the empty
    ruled area).

    The reading is used only when it is found whole and at a block boundary.
    Where the alignment joined it with a neighbouring disagreement - engine A
    read a fold label there, engine B a column engine A skipped and then the
    region - the replacement is split: engine A's side becomes its own
    one-engine reading and engine B's side the region's text, on the boundary
    side of it.  Otherwise the reading could not stand as the block's text and
    would go down the ordinary path, where a preference for engine A keeps the
    label and drops everything engine B read there, and 裁決 may answer "no
    character" to a one-engine insertion: a reading in several pieces, or
    inside another block's text, keeps the page's old judgement (None).

    Returns the new blocks, draft and segments, the agreement for the order
    test, and what was dropped; None when there is not exactly one looped
    block (two or more keep the whole-page fallback) or when engine B's
    reading of the region cannot be placed.
    """
    looped = looped_blocks(blocks, draft, draft2)
    if len(looped) != 1:
        return None
    index = looped[0]
    loop = CJK(str(blocks[index]["text"]))
    emptied = {**blocks[index], "text": "", "label": "text",
               "quarantined": {"label": blocks[index]["label"]}}
    # Each block keeps the index engine A's layout gave it (layout_index): the
    # emptied block may move, and what is sealed about a block must still name
    # the one engineALoopsDropped names and the layout file lists.
    kept = [dict(emptied if i == index else b, layoutIndex=layout_index(blocks, i)) for i, b in enumerate(blocks)]
    reduced = "\n".join(str(b["text"]) for b in kept)
    segments = fuse_numeral_runs(align_drafts(reduced, draft2, folded, prefer))
    rest = CJK(reduced)

    def grams(text: str) -> set[str]:
        return {text[i:i + 4] for i in range(len(text) - 3)}

    loop_grams, rest_grams = grams(loop), grams(rest)
    starts = set(itertools.accumulate([0] + [len(CJK(str(b["text"]))) for b in kept]))
    # Every stretch only engine B read that reads like the loop's region.
    candidates, at = [], 0
    for position, seg in enumerate(segments):
        if seg["tag"] != "equal" and seg["b"]:
            own = [seg["b"][i:i + 4] for i in range(len(seg["b"]) - 3)]
            shared = sum(g in loop_grams for g in own)
            if shared and shared > sum(g in rest_grams for g in own):
                candidates.append((position, at))
        at += len(seg["a"])
    if len(candidates) > 1:
        return None
    read, reading_at, found, shared_with = 0, 0, "none", None
    if candidates:
        position, at = candidates[0]
        seg = segments[position]
        end = at + len(seg["a"])
        if seg["tag"] == "insert" and at in starts:
            found, reading_at = "insertion", at
        elif seg["tag"] == "replace" and (end in starts or at in starts):
            # Engine B's side on the boundary side of engine A's: the reading
            # after the label when the label's end is a block boundary.
            first = end not in starts
            reading = {"tag": "insert", "a": "", "b": seg["b"],
                       "before": seg["before"] if first else (seg["before"] + seg["a"])[-8:],
                       "after": (seg["a"] + seg["after"])[:8] if first else seg["after"]}
            own = {"tag": "delete", "a": seg["a"], "b": "", "before": seg["before"], "after": seg["after"]}
            segments[position:position + 1] = [reading, own] if first else [own, reading]
            found, shared_with, reading_at, seg = "split", seg["a"], (at if first else end), reading
        else:
            return None
    if found != "none":
        seg[LOOPED_READING] = True
        read = len(seg["b"])
        # The block goes before the first block holding text that starts where
        # engine B read the region, or last.
        moved = kept.pop(index)
        offset, slot = 0, len(kept)
        for i, block in enumerate(kept):
            length = len(CJK(str(block["text"])))
            if offset == reading_at and length:
                slot = i
                break
            offset += length
        kept.insert(slot, moved)
    agreed = sum(len(x["a"]) for x in segments if x["tag"] == "equal")
    total = max(len(rest), len(CJK(draft2)) - read, 1)
    return {"blocks": kept, "draft": reduced, "segments": segments, "agreement": agreed / total,
            "dropped": {"block": layout_index(blocks, index), "label": blocks[index]["label"],
                        "characters": len(loop),
                        "distinct4gram": round(distinct_ngram_ratio(loop), 3),
                        "engineBCharacters": read, "text": loop[:2000],
                        # How engine B's reading of the region was found: a
                        # one-engine insertion, split off a replacement it
                        # shared with engine A's text (sharedWith), or none.
                        "engineBReading": {"found": found, "sharedWith": shared_with}}}


def order_judgement(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str,
                    prefer: str | None = None, folded: bool | None = None) -> dict[str, Any]:
    """The page's order test on these readings, before anything is merged:
    the segments, the agreement - what the engines read in the same order,
    over the longer reading, engine B's reading of a looped block
    (quarantine_looped_block) left out - and the quarantine when it applies
    (it sets its one looped block aside and what is left merges), else None.
    PREFER is the page's engine preference and FOLDED how the drafts are
    matched (align_drafts)."""
    segments = fuse_numeral_runs(align_drafts(draft, draft2, folded, prefer))
    agreed = sum(len(x["a"]) for x in segments if x["tag"] == "equal")
    agreement = agreed / max(len(CJK(draft)), len(CJK(draft2)), 1)
    quarantine = quarantine_looped_block(blocks, draft, draft2, prefer, folded)
    if quarantine is not None and quarantine["agreement"] >= READING_ORDER_AGREEMENT:
        return {"segments": quarantine["segments"], "agreement": quarantine["agreement"],
                "quarantine": quarantine}
    return {"segments": segments, "agreement": agreement, "quarantine": None}


# A page where nearly every character is followed by a mark is a degenerate
# formatter output, not a style of book: measured 96.6 marks per 100 characters
# on one page against 4-25 for normal ones.  It is a retry signal, not a fact
# about the original.
DEGENERATE_PUNCTUATION_DENSITY = 60.0

# Below this share of the page's characters aligned in order, the two engines
# read the page in different orders and it is not merged (merge-order-conflict).
READING_ORDER_AGREEMENT = 0.70

# In an order conflict the whole page comes from one engine.  If the preferred
# engine read less than this share of what the other read, it did not read the
# page - it read a fragment of it - and the preference is overridden.
ORDER_CONFLICT_FLOOR = 0.6

# A running head is short and thin.  Anything longer or larger that happens to
# share the folio's margin is content, not furniture.
MARGIN_FURNITURE_LIMIT = 40
MARGIN_FURNITURE_AREA = 0.20


FURNITURE_GRAM = 8
# A string on this share of the book's pages is a running head or a folio, not
# a sentence.  Measured: a fiscal report's running head appears on 19 of 28
# sampled pages, while the real clause a historical monograph lost off p.3 appears on 1
# of 344.  Length cannot separate those two - the running head is 33 characters
# and the lost clause 38 - but recurrence separates them cleanly.
FURNITURE_PAGE_SHARE = 0.15
FURNITURE_MINIMUM_PAGES = 3


def furniture_grams(draft_paths: Sequence[Path]) -> set[str]:
    """Character n-grams that recur across the book's pages.

    Computed once per run from the OCR drafts, with no model call.  Without it
    the deletion guard blocks the formatter from removing a long running head,
    which is exactly what it is supposed to remove.
    """
    seen: dict[str, int] = {}
    pages = 0
    for path in draft_paths:
        try:
            text = CJK(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        pages += 1
        for gram in {text[i:i + FURNITURE_GRAM]
                     for i in range(len(text) - FURNITURE_GRAM + 1)}:
            seen[gram] = seen.get(gram, 0) + 1
    if pages < FURNITURE_MINIMUM_PAGES:
        return set()
    floor = max(FURNITURE_MINIMUM_PAGES, int(pages * FURNITURE_PAGE_SHARE))
    return {gram for gram, count in seen.items() if count >= floor}


# A running head or folio sits at the page boundary; body text does not.  This
# is what separates a provincial finance report第三編省稅第十二章稅捐第二十三節花筵搨及花搨附加九三
# (33 characters, page start, all furniture) from the 38-character 凡例 clause
# a historical monograph lost off the middle of p.3.
FURNITURE_EDGE = 8


def margin_furniture(blocks: Sequence[Mapping[str, Any]],
                     labels: Sequence[str] = ("header", "footer", "number")) -> list[str]:
    """Text of blocks that sit in the page's furniture margin.

    Recurrence needs a book; a single-page run has none, and the bare length
    limit then rejects the formatter for removing a 16-character running head -
    which is the realistic case when someone OCRs a handful of pages.

    The anchor is the folio.  PaddleOCR's `number` label is the one furniture
    label that has been reliable across all four calibration books, while
    `header`, `footer`, `text` and `figure_title` have each been observed on
    real body content.  Whatever shares the folio's margin band is furniture:
    on a multi-page fiscal report p.24 the running head and the folio both sit at
    x≈0.05 while the page's real title sits at x≈0.85.  `labels`: the blocks
    taken by their label alone (the table stitch takes only the folio's).
    """
    return [text for text in (CJK(str(b.get("text") or "")) for b in margin_blocks(blocks, labels)) if text]


def margin_blocks(blocks: Sequence[Mapping[str, Any]],
                  labels: Sequence[str] = ("header", "footer", "number")) -> list[Mapping[str, Any]]:
    """The blocks margin_furniture reads: those labelled `labels`, then, per
    folio, the short thin blocks in its margin band."""
    folios = [b for b in blocks if b.get("label") == "number"
              and isinstance(b.get("box"), list) and len(b["box"]) == 4]
    found = [b for b in blocks if b.get("label") in labels]
    for folio in folios:
        fx, fy = folio["box"][0], folio["box"][1]
        for block in blocks:
            box = block.get("box")
            if not (isinstance(box, list) and len(box) == 4) or block is folio:
                continue
            # Same vertical band (a horizontal running head) or the same
            # horizontal band (a vertical one, as in these books).
            if not (abs(box[0] - fx) <= 0.06 or abs(box[1] - fy) <= 0.06):
                continue
            # Size is what stops this from waving through real content: on this
            # very page the TABLE block also sits in the folio's margin band,
            # and treating it as furniture would let a deleted table pass
            # unreported.  A running head is short and thin; a table is not.
            text = CJK(str(block.get("text") or ""))
            area = abs(box[2]) * abs(box[3]) if len(box) == 4 else 1.0
            if len(text) <= MARGIN_FURNITURE_LIMIT and area <= MARGIN_FURNITURE_AREA:
                found.append(block)
    return found


def furniture_text(text: str, heads: Sequence[str], furniture: frozenset[str] | set[str] | None) -> bool:
    """Whether TEXT, a block's own reading, is the book's furniture (furniture_band):
    one of the running heads its profile measured (is_running_head), or text
    that those heads - with the folio's figures beside them - and the n-grams
    recurring across the book's pages (furniture_grams) explain but for at
    most INSERTION_LIMIT characters, one at least explained
    (furniture_leftover).  A block's layout label is not this evidence: the
    layout labels section headings, table cells and body lines header, and a
    signature number (the review's scan of the whole corpus)."""
    chars = CJK(text)
    if not chars:
        return False
    if heads and is_running_head(chars, heads):
        return True
    left = furniture_leftover(chars, furniture or frozenset(), heads)
    return left <= INSERTION_LIMIT and left < len(chars)


def at_head_size(block: Mapping[str, Any], text: str, arguments: Any) -> bool:
    """Whether BLOCK, reading TEXT, is set in the type size of one
    of the running heads the book's profile measured (runningHeadGlyph); False
    where it measured none.  Unlike head_size_matches, for any text: a head
    with its volume or chapter beside it is the head's size too."""
    glyph = [size for size in (profile_section(arguments, "layout").get("runningHeadGlyph") or {}).values() if size]
    box = block.get("box")
    chars = len(CJK(text))
    if not (glyph and chars and isinstance(box, list) and len(box) == 4):
        return False
    size = max(abs(box[2]), abs(box[3])) / chars
    return any(HEAD_SIZE_RATIO[0] <= size / g <= HEAD_SIZE_RATIO[1] for g in glyph)


def furniture_band(blocks: Sequence[Mapping[str, Any]], heads: Sequence[str], arguments: Any,
                   furniture: frozenset[str] | set[str] | None = None) -> dict[int, str]:
    """The positions of engine A's layout blocks in the page's running-head
    band, each with why it is there.  The repair's book model takes it
    (_repair_model); phase 3 wrote one-sided readings in it as nothing in a
    trial since removed.

    The folios are the blocks the layout labelled number that read numerals,
    or no character (figures, a mark).  The anchors are the blocks whose own
    text is the book's furniture with a second piece of evidence: read as one
    of the profile's running heads at the head's own type size
    (is_running_head, head_size_matches), or its text the book's furniture
    (furniture_text) and labelled header or footer, set in a head's type size
    (at_head_size), or with half its width in a folio's column or half its
    height in a folio's row.  A label alone is not enough: a section heading
    at the start of the body labelled header would otherwise be the band's.

    Any other block is in the band when it is short and thin (margin_blocks'
    limits), holds no more characters than the anchor, and at least half its
    width lies in the column of a tall anchor, or half its height in the row
    of a wide one.  A folio extends the band nowhere: its box says nothing of
    which way the band runs, and its column and row cross the body - a body
    column printed under a folio in the top margin, a heading above the body
    in the column of a folio centred at the foot (made-up pages in the same
    check; the review's corpus scan found the first on real pages).  The band
    is where the page prints its furniture - a side column, the fold between
    two leaves, a margin - not a fixed strip at the top or the bottom.

    A table block (labelled table) is never the band's, whatever its size and
    place: its text is the table's cells."""
    def box_of(block: Mapping[str, Any]) -> list[float] | None:
        box = block.get("box")
        return box if isinstance(box, list) and len(box) == 4 else None

    def share(lo: float, hi: float, anchor_lo: float, anchor_hi: float) -> float:
        return 0.0 if hi <= lo else max(0.0, min(hi, anchor_hi) - max(lo, anchor_lo)) / (hi - lo)

    def in_line(box: Sequence[float], anchor: Sequence[float], column: bool, row: bool) -> str | None:
        x, y, w, h = box
        ax, ay, aw, ah = anchor
        if column and share(x, x + w, ax, ax + aw) >= 0.5:
            return "column"
        if row and share(y, y + h, ay, ay + ah) >= 0.5:
            return "row"
        return None

    band: dict[int, str] = {}
    folios = [k for k, block in enumerate(blocks) if block.get("label") == "number"
              and all(c in NUMERAL_CHARS for c in CJK(str(block.get("text") or "")))]
    for k in folios:
        band[k] = "folio"
    anchors: list[int] = []
    for k, block in enumerate(blocks):
        text = str(block.get("text") or "")
        label = block.get("label")
        box = box_of(block)
        if k in band or label == "table":
            continue
        if heads and is_running_head(text, heads) and head_size_matches(block, text, arguments):
            why = "running head"
        elif not furniture_text(text, heads, furniture):
            continue
        elif label in ("header", "footer"):
            why = f"label {label}, furniture text"
        elif at_head_size(block, text, arguments):
            why = "head's size, furniture text"
        else:
            line = next(((line, f) for f in folios if box and box_of(blocks[f])
                         for line in [in_line(box, blocks[f]["box"], True, True)] if line), None)
            if line is None:
                continue
            why = f"{line[0]} of block {layout_index(blocks, line[1])}, furniture text"
        band[k] = why
        if box:
            anchors.append(k)
    for k, block in enumerate(blocks):
        box = box_of(block)
        if k in band or box is None or block.get("label") == "table":
            continue
        size = len(CJK(str(block.get("text") or "")))
        if size > MARGIN_FURNITURE_LIMIT or abs(box[2]) * abs(box[3]) > MARGIN_FURNITURE_AREA:
            continue
        for a in anchors:
            ax, ay, aw, ah = blocks[a]["box"]
            if size > len(CJK(str(blocks[a].get("text") or ""))):
                continue
            line = in_line(box, blocks[a]["box"], ah >= aw, aw > ah)
            if line:
                band[k] = f"{line} of block {layout_index(blocks, a)}"
                break
    return band


# Switch --learned-heads (on by default; ROUND4_DEFAULTS): the running head learned by where it
# is printed, not by what recurs verbatim alone.
#
# The profile keeps a block as a running head only when its text recurs
# verbatim on half the pages (survey_layout): the title every head prints.
# What is printed beside the title changes: a volume or part label every few
# hundred pages, a contents page's own word, the folio every page.  None of it
# recurs on half the pages, so none of it was furniture evidence, and the
# profile drop needs a block reading the title alone (is_running_head).
# Measured on a 488-page book whose head column prints title, volume label and
# folio (a survey of its sealed pages, every instance checked on the render):
# the head, its volume label or its folio was left in the body on 63 pages -
# the title with its label as one paragraph (ten characters, not the
# seven-character title), the label in a block of its own written as a
# heading once the title was dropped, the folio written as a line - and on
# the pages whose structure pass was refused the fallback kept every head
# block the layout had not labelled header (184 of 400).
#
# learn_heads learns, from engine A's blocks of every page: the band - the
# blocks holding a measured running head (a stem) at its type size (anchors)
# and the short thin blocks in an anchor's column or row; the labels printed
# in the band beside the stem on LEARNED_LABEL_MINIMUM_PAGES pages or more,
# and the families of labels that differ only in a numeral (a volume label's
# number); the folio sequence (the scan's page less a constant, over runs of
# pages, from the folios read in the band); and where the anchors stand on
# odd and on even pages (regions).  What it assumes, each gated on the book's
# own evidence: the running head is printed in one band with the stem the
# profile measured (a book whose title never stands verbatim on half its
# pages has no such stem: its heads are learned by place, placed_heads below,
# or not at all); a label is a label when the book
# prints it beside the stem on several pages; the band's place on a page is
# its anchors' column or row, and a learned region stands in for a page whose
# anchor engine A misread only for text holding a stem or a label.
LEARNED_LABEL_MINIMUM_PAGES = 3
# A single character beside the stem is not taken for a label: measured on a
# facsimile's pages, the stem's own first character misread (the same wrong
# character each time) stood beside the stem on three pages.
LEARNED_LABEL_MINIMUM_CHARACTERS = 2
# A folio offset (the printed page number against the scan's) is learned when
# this many pages read it, and a region when this many pages' anchors stand
# there.
LEARNED_FOLIO_MINIMUM_PAGES = 3
LEARNED_REGION_MINIMUM_PAGES = 3
# Anchor centres further apart than this (page fraction) are two bands: the
# outer margin of odd pages and of even pages, the leaves of a facsimile.
LEARNED_REGION_GAP = 0.05
# How far a band block's thickness - its width across a column, its height
# across a row - may be from the stem's and still be the head's type size
# (head_size_fits).
HEAD_THICKNESS_RATIO = (0.6, 1.5)
NUMERAL_DIGITS = "〇一二三四五六七八九"
NUMERAL_VALUES = {**{c: i for i, c in enumerate(NUMERAL_DIGITS)}, "零": 0}
NUMERAL_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}
# The tens a folio may print as one character (廿二, 卅五): a facsimile's leaf
# folios, measured on a book of 26 pages, write 21-39 so.
NUMERAL_TENS = {"廿": 20, "卅": 30, "卌": 40}
FOLIO_CHARS = NUMERAL_CHARS | frozenset(NUMERAL_TENS)
NUMERAL_RUN = "[" + "".join(sorted(FOLIO_CHARS)) + "]"

# The running head of a book set in lines, learned by its place alone
# (placed_heads): a stem needs no recurrence on half the pages.
#
# A book set in lines prints its head as the top line of the page, and not one
# head: the book's title on one side of the opening and the chapter's on the
# other, each chapter's for as long as the chapter runs.  None stands verbatim
# on half the pages, so the profile measured none, nothing was a stem, and the
# merge asked 裁決 about the head on every page one engine read it.  Measured
# on a 639-page book (2026-11-17): the book's title heads 304 of its 638 pages
# with text, one short of half, and each of its chapters' titles 6 to 28; of
# the 830 questions 裁決 was asked in the book's first wave, 606 - one on each
# of 606 pages - were that line, read by engine A and not by engine B.
#
# What is learned, from engine A's blocks alone: the top line of each page
# (top_line) where the layout labelled it header; the band - the place where
# such lines stand, their centres within LEARNED_REGION_GAP of each other, on
# PLACED_BAND_PAGE_SHARE of the pages of a parity or more; and the stems - each
# text standing in the band, one line thick, on LEARNED_LABEL_MINIMUM_PAGES
# pages or more.  Three things are asked together because none is evidence
# alone: the layout labels a chapter's number header on the chapter's first
# page (three of that book's chapter openings), a title or a table's label
# may repeat at the top of several pages, and a short line stands at the top
# of a page wherever a section begins there.
#
# A stem learned so is the head only where it was learned, whatever the
# block's label: a block holding it is an anchor only with half its height in
# the band of its page's parity (head_band_blocks, in_placed_band), and a
# reading of it one engine alone made is left out only where engine A read it
# in a block of that band (the merge's learned_insertion).  A chapter's title
# printed as the heading of its first page stands under the band and is
# neither, at whatever type size; the same words only engine B read have no
# place on the page and are asked of 裁決 as before.
#
# Replayed on that book's drafts with a stand-in for the model (no answer
# real): 22 stems, a band on 603 pages; 600 of the 606 readings are left out
# and the first wave asks 230 questions for 830.  Every page that changed -
# 605 - lost its top line in the band and nothing else; the 17 pages that open
# a chapter or the afterword kept their titles.  The six still asked: four
# heads standing on one or two pages, and two readings that run on into a
# character only engine A read at the start of the body (the head is then
# left out as a block, the character is 裁決's to decide).
PLACED_HEAD_CHARACTERS = (2, 30)
PLACED_BAND_PAGE_SHARE = 0.5
PLACED_HEAD_LABEL = "header"


def numeral_value(text: str) -> int | None:
    """The value TEXT's numerals write, figure by figure (一七七, 二〇) or
    counted (一百七十七, 十二, 廿二); None when TEXT holds anything else."""
    chars = CJK(text)
    if not chars or any(c not in FOLIO_CHARS for c in chars):
        return None
    if not any(c in NUMERAL_UNITS or c in NUMERAL_TENS for c in chars):
        return int("".join(str(NUMERAL_VALUES[c]) for c in chars))
    total = section = 0
    digit = None
    for c in chars:
        if c in NUMERAL_VALUES:
            digit = NUMERAL_VALUES[c]
            continue
        if c in NUMERAL_TENS:
            section, digit = section + NUMERAL_TENS[c], None
            continue
        unit = NUMERAL_UNITS[c]
        if unit == 10000:
            total += (section + (digit or 0)) * unit
            section = 0
        else:
            section += (1 if digit is None else digit) * unit
        digit = None
    return total + section + (digit or 0)


def numeral_forms(value: int) -> set[str]:
    """VALUE written as a folio is printed: figure by figure (一七七, 二〇) and
    counted (一百七十七, 一百〇五 or 一百零五, 十二 or 一十二, 二十二 or 廿二)."""
    figures = "".join(NUMERAL_DIGITS[int(d)] for d in str(value))
    if value <= 0 or value >= 10000:
        return {figures}
    counted, zero = "", False
    for unit, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit = value // unit % 10
        if not digit:
            zero = zero or bool(counted)
            continue
        if zero:
            counted, zero = counted + "〇", False
        counted += NUMERAL_DIGITS[digit] + name
    forms = {figures, counted, counted.replace("〇", "零")}
    if counted.startswith("一十"):
        forms.add(counted[1:])
    for tens, value_of in NUMERAL_TENS.items():
        written = NUMERAL_DIGITS[value_of // 10] + "十"
        if written in counted:
            forms.add(counted.replace(written, tens))
    return forms


def head_size_fits(block: Mapping[str, Any], characters: int, glyph: float | None, thickness: float | None,
                   axis: str | None = None) -> bool:
    """Whether BLOCK, reading CHARACTERS characters, is set in a running head's
    type size: its thickness across the band - its width in a column (AXIS
    "column"), its height in a row ("row"), its shorter side when AXIS is
    None - within HEAD_THICKNESS_RATIO of the stem's THICKNESS, and its long
    side no shorter than its characters take at the stem's size GLYPH (page
    fraction per character; HEAD_SIZE_RATIO's lower bound).  Not the long
    side's length against the characters: measured on a book's pages, the
    layout's box for the head took in the label printed after it that it did
    not read (a box of ten characters' length reading the seven-character
    title), and a contents page's two-character label was spaced out to half
    again the title's length per character.  A title set large is thicker:
    the same book's volume titles on its divider pages are twice the head's
    width."""
    box = block.get("box")
    if not (glyph and thickness and characters and isinstance(box, list) and len(box) == 4):
        return False
    w, h = abs(box[2]), abs(box[3])
    across, along = (w, h) if axis == "column" else (h, w) if axis == "row" else (min(w, h), max(w, h))
    return (HEAD_THICKNESS_RATIO[0] * thickness <= across <= HEAD_THICKNESS_RATIO[1] * thickness
            and along >= HEAD_SIZE_RATIO[0] * glyph * characters)


def short_thin(block: Mapping[str, Any]) -> bool:
    """A block that may be a margin's furniture: short and thin (the limits of
    margin_blocks), with a box, not a table."""
    box = block.get("box")
    return (block.get("label") != "table" and isinstance(box, list) and len(box) == 4
            and len(CJK(str(block.get("text") or ""))) <= MARGIN_FURNITURE_LIMIT
            and abs(box[2]) * abs(box[3]) <= MARGIN_FURNITURE_AREA)


def top_line(blocks: Sequence[Mapping[str, Any]]) -> list[int]:
    """The positions of engine A's blocks in a page's top line: the block that
    starts highest on the page and every block starting above its middle,
    when each is short, thin (short_thin) and wider than tall - a line set
    across the page above everything else on it.  None where the highest
    block is the body's: a paragraph, a column of a page set in columns, a
    table.  A block reading numerals alone or no character (the folio, in
    figures or in numerals) is neither the line nor in its way."""
    texts = [k for k, block in enumerate(blocks)
             if isinstance(block.get("box"), list) and len(block["box"]) == 4
             and any(c not in FOLIO_CHARS for c in CJK(str(block.get("text") or "")))]
    if not texts:
        return []
    first = min(texts, key=lambda k: float(blocks[k]["box"][1]))
    middle = float(blocks[first]["box"][1]) + abs(float(blocks[first]["box"][3])) / 2
    row = [k for k in texts if float(blocks[k]["box"][1]) < middle]
    if all(short_thin(blocks[k]) and abs(blocks[k]["box"][2]) >= abs(blocks[k]["box"][3]) for k in row):
        return row
    return []


def placed_heads(page_blocks: Mapping[int, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """The running heads of a book set in lines, learned by place (see
    PLACED_BAND_PAGE_SHARE), from PAGE_BLOCKS (scan page -> engine A's
    blocks): "stems", each text with the pages it heads, and "bands", where
    they stand on odd and on even pages (the span of the page's height their
    lines take).  Both empty for a book that prints no such line."""
    lines: dict[int, list[tuple[float, float, float, int, str]]] = {}
    for page, blocks in page_blocks.items():
        for k in top_line(blocks):
            raw = str(blocks[k].get("text") or "")
            chars = CJK(raw)
            if (blocks[k].get("label") == PLACED_HEAD_LABEL and "。" not in raw
                    and PLACED_HEAD_CHARACTERS[0] <= len(chars) <= PLACED_HEAD_CHARACTERS[1]):
                y, h = float(blocks[k]["box"][1]), abs(float(blocks[k]["box"][3]))
                lines.setdefault(page % 2, []).append((y + h / 2, y, y + h, page, chars))
    bands: list[tuple[int, list[tuple[float, float, float, int, str]]]] = []
    pages_of: dict[str, set[int]] = {}
    for parity, items in sorted(lines.items()):
        items.sort()
        clusters = [[items[0]]]
        for item in items[1:]:
            if item[0] - clusters[-1][-1][0] > LEARNED_REGION_GAP:
                clusters.append([])
            clusters[-1].append(item)
        of_parity = sum(1 for page in page_blocks if page % 2 == parity)
        for cluster in clusters:
            # One line thick: a chapter's number set large at the top of its
            # first page, labelled header, starts within the gap of the band.
            thick = sorted(hi - lo for _, lo, hi, _, _ in cluster)[len(cluster) // 2]
            cluster = [item for item in cluster
                       if HEAD_THICKNESS_RATIO[0] * thick <= item[2] - item[1] <= HEAD_THICKNESS_RATIO[1] * thick]
            if len({item[3] for item in cluster}) < max(LEARNED_REGION_MINIMUM_PAGES,
                                                         PLACED_BAND_PAGE_SHARE * of_parity):
                continue
            bands.append((parity, cluster))
            for item in cluster:
                pages_of.setdefault(item[4], set()).add(item[3])
    stems = {text: len(pages) for text, pages in pages_of.items() if len(pages) >= LEARNED_LABEL_MINIMUM_PAGES}
    found: dict[str, Any] = {"stems": dict(sorted(stems.items(), key=lambda kv: (-kv[1], kv[0]))), "bands": []}
    for parity, cluster in bands:
        own = [item for item in cluster if item[4] in stems]
        if own:
            found["bands"].append({"parity": "odd" if parity else "even",
                                   "span": [round(min(item[1] for item in own), 4),
                                            round(max(item[2] for item in own), 4)],
                                   "pages": len({item[3] for item in own})})
    return found


def in_placed_band(block: Mapping[str, Any], placed: Mapping[str, Any] | None, page: int | None) -> bool:
    """Whether BLOCK stands where the heads learned by place (PLACED, the
    learned record's "placed") stand on pages of scan PAGE's parity: half its
    height or more inside that band."""
    box = block.get("box")
    if not (placed and page is not None and isinstance(box, list) and len(box) == 4):
        return False
    y, h = float(box[1]), abs(float(box[3]))
    parity = "odd" if page % 2 else "even"
    return h > 0 and any(band["parity"] == parity
                         and (min(y + h, band["span"][1]) - max(y, band["span"][0])) / h >= 0.5
                         for band in placed.get("bands") or [])


def needs_placed_stem(text: str, learned: Mapping[str, Any], page: int | None = None) -> bool:
    """Whether the learned head explains TEXT only by a stem learned by place
    alone (the learned record's "placed"): without those stems some of it is
    left unexplained (learned_leftover), or no stem is in it."""
    placed = (learned.get("placed") or {}).get("stems") or {}
    if not placed:
        return False
    measured = dict(learned, stems=[stem for stem in learned.get("stems") or [] if stem not in placed])
    left, found = learned_leftover(text, measured, page)
    return bool(left) or not found["stems"]


def stem_windows(chars: str, stem: str) -> list[tuple[float, int, int]]:
    """Every stretch of CHARS is_running_head takes for STEM, as (how like it,
    start, length): the stem's own length with characters misread in place
    (a substitution, nothing gained or lost), or one character shorter (a
    character not read), the stem's own length first, then the likest.  Not
    one character longer, nor the stem's length with a character lost and
    one gained: either takes in the character beside the stem - a label's,
    or the body's, a title that is the stem with a word after it (a preface
    to the book) read as the head."""
    found = []
    for length in (len(stem), len(stem) - 1):
        for start in range(0, len(chars) - length + 1) if length >= 2 else ():
            window = chars[start:start + length]
            if not is_running_head(window, [stem]):
                continue
            matcher = difflib.SequenceMatcher(None, window, stem, autojunk=False)
            if length == len(stem) and any(tag in ("insert", "delete") for tag, *_ in matcher.get_opcodes()):
                continue
            found.append((length == len(stem), 1.0 if window == stem else matcher.ratio(), -start, length))
    return [(ratio, -start, length) for _, ratio, start, length in sorted(found, reverse=True)]


def head_band_blocks(blocks: Sequence[Mapping[str, Any]], stems: Sequence[str], glyph: Mapping[str, float],
                     thickness: Mapping[str, float],
                     is_head: Callable[[str], bool] | None = None,
                     placed: Mapping[str, Any] | None = None, page: int | None = None) -> dict[int, dict[str, Any]]:
    """Engine A's blocks in a page's running-head band, by position: the
    anchors - short thin blocks holding one of STEMS (stem_windows) set in its
    type size (head_size_fits, GLYPH and THICKNESS) - and the short thin
    blocks with half their width in a tall anchor's column or half their
    height in a wide one's row, each reading numerals or nothing (a folio,
    figures) or set in the anchor's type size across the band.  A title set
    large, the body, a table are not.
    IS_HEAD, when given, must also take an anchor's own text for the running
    head: a body line the layout read in one block with the head beside it
    holds the stem too, and its column is the body's.
    A stem learned by place alone (PLACED, the learned record's "placed")
    makes an anchor only of a block in its band on scan PAGE
    (in_placed_band): the same words lower on the page are a heading."""
    by_place = (placed or {}).get("stems") or {}
    anchors: dict[int, dict[str, Any]] = {}
    for k, block in enumerate(blocks):
        if not short_thin(block):
            continue
        chars = CJK(str(block.get("text") or ""))
        for stem in stems:
            windows = stem_windows(chars, stem)
            if (windows and head_size_fits(block, len(chars), glyph.get(stem), thickness.get(stem))
                    and (is_head is None or is_head(chars))
                    and (stem not in by_place or in_placed_band(block, placed, page))):
                anchors[k] = {"role": "anchor", "stem": stem, "at": windows[0][1], "length": windows[0][2]}
                break
    band = dict(anchors)
    for k, block in enumerate(blocks):
        if k in band or not short_thin(block):
            continue
        x, y, w, h = (float(v) for v in block["box"])
        chars = CJK(str(block.get("text") or ""))
        for a, anchor in anchors.items():
            ax, ay, aw, ah = (float(v) for v in blocks[a]["box"])
            tall = abs(ah) >= abs(aw)
            lo, hi, alo, ahi = (x, x + w, ax, ax + aw) if tall else (y, y + h, ay, ay + ah)
            if hi <= lo or max(0.0, min(hi, ahi) - max(lo, alo)) / (hi - lo) < 0.5:
                continue
            if (not chars or all(c in FOLIO_CHARS for c in chars)
                    or head_size_fits(block, len(chars), glyph.get(anchor["stem"]), thickness.get(anchor["stem"]),
                                      "column" if tall else "row")):
                band[k] = {"role": "member", "anchor": a}
                break
    return band


def split_folio(piece: str) -> tuple[str, list[str]]:
    """PIECE, text printed in the running-head band beside the stem or apart
    from it, as its label and the folio figures at either end of it: numerals
    at a piece's start or end are the folio's, what is between them a label
    (a volume label's own numeral is inside it)."""
    start, end = 0, len(piece)
    while start < end and piece[start] in FOLIO_CHARS:
        start += 1
    while end > start and piece[end - 1] in FOLIO_CHARS:
        end -= 1
    return piece[start:end], [f for f in (piece[:start], piece[end:]) if f]


def label_skeleton(label: str) -> str:
    """LABEL with each run of numerals written N: the family it belongs to."""
    return re.sub(NUMERAL_RUN + "+", "N", label)


def folio_runs(observed: Sequence[tuple[int, int]], minimum: int = LEARNED_FOLIO_MINIMUM_PAGES
               ) -> list[dict[str, int]]:
    """The folio sequence from OBSERVED (scan page, folio read): runs of pages
    whose folio is the scan's page less one offset, each offset read on
    MINIMUM pages or more.  A folio misread is an offset read on fewer, and
    neither starts nor breaks a run."""
    counts = Counter(page - value for page, value in observed)
    runs: list[dict[str, int]] = []
    for page, value in sorted(observed):
        offset = page - value
        if counts[offset] < minimum:
            continue
        if runs and runs[-1]["offset"] == offset:
            runs[-1]["last"], runs[-1]["pages"] = page, runs[-1]["pages"] + 1
        else:
            runs.append({"first": page, "last": page, "offset": offset, "pages": 1})
    merged: list[dict[str, int]] = []
    for run in runs:
        if run["pages"] < minimum:
            continue
        if merged and merged[-1]["offset"] == run["offset"]:
            merged[-1]["last"], merged[-1]["pages"] = run["last"], merged[-1]["pages"] + run["pages"]
        else:
            merged.append(dict(run))
    return merged


def expected_folios(learned: Mapping[str, Any], page: int) -> set[int]:
    """The folio the learned sequence prints on scan PAGE: its run's; between
    two runs, either's; before the first or after the last, the nearest's."""
    runs = (learned.get("folio") or {}).get("runs") or []
    if not runs:
        return set()
    inside = [run for run in runs if run["first"] <= page <= run["last"]]
    if inside:
        return {page - run["offset"] for run in inside}
    before = [run for run in runs if run["last"] < page]
    after = [run for run in runs if run["first"] > page]
    near = ([before[-1]] if before else []) + ([after[0]] if after else [])
    return {page - run["offset"] for run in near if page - run["offset"] > 0}


def learn_heads(page_blocks: Mapping[int, Sequence[Mapping[str, Any]]], heads: Sequence[str]) -> dict[str, Any]:
    """Switch --learned-heads: the book's running head learned by where it is
    printed (see LEARNED_LABEL_MINIMUM_PAGES), from PAGE_BLOCKS (scan page ->
    engine A's blocks) and HEADS, the running heads the profile measured by
    recurrence.  The stems are those heads and the heads of a book set in
    lines learned by place (placed_heads; see PLACED_BAND_PAGE_SHARE): the
    ones the profile did not measure are recorded under "placed" with their
    bands, a key a book with none does not have.  No model call."""
    measured = {CJK(h) for h in heads if len(CJK(h)) >= 2}
    by_place = placed_heads(page_blocks)
    placed = {"stems": {stem: pages for stem, pages in by_place["stems"].items() if stem not in measured},
              "bands": by_place["bands"]}
    stems = sorted(measured | set(placed["stems"]), key=lambda h: (-len(h), h))
    record: dict[str, Any] = {"stems": stems, "glyph": {}, "thickness": {}, "labels": {}, "families": [],
                              "folio": {"observed": 0, "runs": []}, "regions": [],
                              "pagesExamined": len(page_blocks), "pagesWithBand": 0,
                              **({"placed": placed} if placed["stems"] else {})}
    # The stems' type size, from the blocks reading one alone: the length of a
    # character along the band, and the band's thickness across it.
    sizes: dict[str, list[tuple[float, float]]] = {stem: [] for stem in stems}
    for blocks in page_blocks.values():
        for block in blocks:
            chars, box = CJK(str(block.get("text") or "")), block.get("box")
            if chars in sizes and isinstance(box, list) and len(box) == 4:
                sizes[chars].append((max(abs(box[2]), abs(box[3])) / len(chars), min(abs(box[2]), abs(box[3]))))
    glyph = {stem: sorted(v[0] for v in values)[len(values) // 2] for stem, values in sizes.items() if values}
    thickness = {stem: sorted(v[1] for v in values)[len(values) // 2] for stem, values in sizes.items() if values}
    record["glyph"] = {stem: round(size, 5) for stem, size in glyph.items()}
    record["thickness"] = {stem: round(size, 5) for stem, size in thickness.items()}
    labels: dict[str, set[int]] = {}
    observed: list[tuple[int, int]] = []
    anchored: list[tuple[int, str, float, float]] = []
    for page in sorted(page_blocks):
        blocks = page_blocks[page]
        band = head_band_blocks(blocks, stems, glyph, thickness, placed=record.get("placed"), page=page)
        if not band:
            continue
        record["pagesWithBand"] += 1
        for k, role in sorted(band.items()):
            chars = CJK(str(blocks[k].get("text") or ""))
            if role["role"] == "anchor":
                pieces = [chars[:role["at"]], chars[role["at"] + role["length"]:]]
                x, y, w, h = (float(v) for v in blocks[k]["box"])
                anchored.append((page, "column", x, x + w) if abs(h) >= abs(w) else (page, "row", y, y + h))
            else:
                pieces = [chars]
            # A folio read partly in figures (二〇 as 20, or 一〇四 as 10四)
            # has lost them from its characters: not a reading of the folio.
            figures = bool(re.search(r"[0-9A-Za-z]", str(blocks[k].get("text") or "")))
            for piece in pieces:
                label, folios = split_folio(piece)
                observed.extend((page, value) for value in (numeral_value(f) for f in folios
                                                            if len(f) <= FOLIO_MAX_FIGURES and not figures)
                                if value is not None)
                if len(label) >= LEARNED_LABEL_MINIMUM_CHARACTERS:
                    labels.setdefault(label, set()).add(page)
    learned = {label: len(pages) for label, pages in labels.items() if len(pages) >= LEARNED_LABEL_MINIMUM_PAGES}
    record["labels"] = dict(sorted(learned.items(), key=lambda kv: (-kv[1], kv[0])))
    families: dict[str, set[str]] = {}
    for label in learned:
        if label_skeleton(label) != label:
            families.setdefault(label_skeleton(label), set()).add(label)
    record["families"] = sorted(skeleton for skeleton, members in families.items() if len(members) >= 2)
    record["folio"] = {"observed": len(observed), "runs": folio_runs(observed)}
    by_side: dict[tuple[int, str], list[tuple[float, float, float, int]]] = {}
    for page, axis, lo, hi in anchored:
        by_side.setdefault((page % 2, axis), []).append(((lo + hi) / 2, lo, hi, page))
    for (parity, axis), items in sorted(by_side.items()):
        items.sort()
        clusters = [[items[0]]]
        for item in items[1:]:
            if item[0] - clusters[-1][-1][0] > LEARNED_REGION_GAP:
                clusters.append([])
            clusters[-1].append(item)
        for cluster in clusters:
            pages = {item[3] for item in cluster}
            if len(pages) >= LEARNED_REGION_MINIMUM_PAGES:
                record["regions"].append({"parity": "odd" if parity else "even", "axis": axis,
                                          "span": [round(min(i[1] for i in cluster), 4),
                                                   round(max(i[2] for i in cluster), 4)],
                                          "pages": len(pages)})
    return record


def learned_band(blocks: Sequence[Mapping[str, Any]], learned: Mapping[str, Any] | None,
                 page: int) -> dict[int, str]:
    """Switch --learned-heads: engine A's blocks of scan PAGE in the running
    head's band, each with why: an anchor - its own text the learned head but
    for INSERTION_LIMIT characters (learned_leftover) - or a block in an
    anchor's column or row (head_band_blocks), or a short thin block set in
    the head's type size in a region where the book's anchors stand on pages
    of this parity ("region", which learned_head_block takes only for text
    holding a stem or a label: a page's own folio is known by its own
    anchor's column).  A region stands in only for the anchor engine A did
    not read: where one of the page's anchors stands in it, the band there is
    that anchor's column or row, and the region is not used.  Its span is the
    head's drift over the book - measured on a 488-page book, the odd pages'
    anchors over 18% of the page's width, where the head itself is 3% wide -
    and on a page whose head is read it takes in the body's columns beside
    the head at the head's type size (on that book's contents pages, the
    entries; made-up page in tests/check_learned_heads.py: a column beside
    the head reading a volume's name alone)."""
    if not learned or not learned.get("stems"):
        return {}
    glyph = learned.get("glyph") or {}
    thickness = learned.get("thickness") or {}
    band: dict[int, str] = {}

    def is_head(chars: str) -> bool:
        return learned_leftover(chars, learned, page)[0] <= INSERTION_LIMIT

    def share(block: Mapping[str, Any], region: Mapping[str, Any]) -> float:
        """How much of BLOCK's width (a column region) or height (a row
        region) is inside REGION's span."""
        x, y, w, h = (float(v) for v in block["box"])
        lo, hi = (x, x + w) if region["axis"] == "column" else (y, y + h)
        rlo, rhi = region["span"]
        return max(0.0, min(hi, rhi) - max(lo, rlo)) / (hi - lo) if hi > lo else 0.0

    anchors = []
    for k, role in head_band_blocks(blocks, learned["stems"], glyph, thickness, is_head,
                                    learned.get("placed"), page).items():
        if role["role"] == "anchor":
            band[k] = f"anchor ({role['stem']})"
            anchors.append(k)
        else:
            _, _, aw, ah = blocks[role["anchor"]]["box"]
            band[k] = f"{'column' if abs(ah) >= abs(aw) else 'row'} of block {layout_index(blocks, role['anchor'])}"
    parity = "odd" if page % 2 else "even"
    regions = [region for region in learned.get("regions") or [] if region["parity"] == parity
               and not any(share(blocks[a], region) >= 0.5 for a in anchors)]
    for k, block in enumerate(blocks):
        if k in band or not short_thin(block):
            continue
        chars = CJK(str(block.get("text") or ""))
        if not chars:
            continue
        for region in regions:
            if not any(head_size_fits(block, len(chars), glyph.get(stem), thickness.get(stem), region["axis"])
                       for stem in learned["stems"]):
                continue
            if share(block, region) >= 0.5:
                rlo, rhi = region["span"]
                band[k] = f"region ({parity} pages' {region['axis']} {rlo:.3f}-{rhi:.3f})"
                break
    return band


def learned_leftover(text: str, learned: Mapping[str, Any] | None, page: int | None = None
                     ) -> tuple[int, dict[str, list[str]]]:
    """Switch --learned-heads: how many of TEXT's characters the learned
    running head leaves unexplained, and what explains the rest: each
    stretch a stem matches (stem_windows, the likest first), each learned
    label and each label of a learned family (a numeral of one to
    FOLIO_MAX_FIGURES figures where the family has one), and each run of up to
    FOLIO_MAX_FIGURES numerals touching one of those, or making up the whole
    text, or read in the folio the learned sequence prints on scan PAGE
    (numeral_forms, expected_folios)."""
    chars = CJK(text)
    if not learned or not chars:
        return len(chars), {"stems": [], "labels": [], "folio": []}
    windows = sorted(((ratio, -start, length) for stem in learned.get("stems") or []
                      for ratio, start, length in stem_windows(chars, stem)), reverse=True)
    patterns = [re.escape(label) for label in sorted(learned.get("labels") or {}, key=len, reverse=True)]
    patterns += [re.escape(skeleton).replace("N", NUMERAL_RUN + "{1,%d}" % FOLIO_MAX_FIGURES)
                 for skeleton in learned.get("families") or []]
    forms = {form for value in (expected_folios(learned, page) if page is not None else ())
             for form in numeral_forms(value)}

    def explain(first: int | None) -> tuple[int, float, dict[str, list[str]]]:
        """The stretches explained taking stem window FIRST before the others
        (greedy, the likest first), then the labels, then the folio."""
        covered = bytearray(len(chars))
        found: dict[str, list[str]] = {"stems": [], "labels": [], "folio": []}
        likeness = 0.0
        for ratio, start, length in ([windows[first]] if first is not None else []) + windows:
            start = -start
            if not any(covered[start:start + length]):
                covered[start:start + length] = b"\1" * length
                found["stems"].append(chars[start:start + length])
                likeness += ratio
        for pattern in patterns:
            for match in re.finditer(pattern, chars):
                if not any(covered[match.start():match.end()]):
                    covered[match.start():match.end()] = b"\1" * (match.end() - match.start())
                    found["labels"].append(match.group())
        for match in re.finditer(NUMERAL_RUN + "+", chars):
            lo, hi = match.span()
            if any(covered[lo:hi]) or hi - lo > FOLIO_MAX_FIGURES:
                continue
            touches = (lo > 0 and covered[lo - 1]) or (hi < len(chars) and covered[hi])
            if touches or (lo == 0 and hi == len(chars)) or any(match.group() in form for form in forms):
                covered[lo:hi] = b"\1" * (hi - lo)
                found["folio"].append(match.group())
        return len(chars) - sum(covered), likeness, found

    # A stem window one character longer or shorter than the stem can take a
    # character of the label beside it, or leave out a misread one of its
    # own: each window is tried first, and the reading that leaves the fewest
    # characters unexplained stands (the likest, between equals).
    left, _, found = min((explain(first) for first in [None, *range(len(windows))]),
                         key=lambda result: (result[0], -result[1]))
    return left, found


def learned_record(arguments: Any) -> dict[str, Any] | None:
    """Switch --learned-heads: the learned running head (learn_heads), when
    the switch is on and the book has one."""
    learned = getattr(arguments, "learned", None) if getattr(arguments, "learned_heads", False) else None
    return learned if learned and learned.get("stems") else None


def learned_heads_seal(arguments: Any) -> str:
    """What provenance seals of switch --learned-heads: the hash of the
    learned running head the page was decided with."""
    return sha256_bytes(canonical_json_bytes(getattr(arguments, "learned", None) or {}))


def learned_head_block(span: str, why: str | None, learned: Mapping[str, Any] | None,
                       page: int, own: str | None = None) -> dict[str, Any] | None:
    """Switch --learned-heads: the evidence that a block in scan PAGE's
    learned band (WHY, learned_band) whose merged text is SPAN is the running
    head, else None: every character of SPAN the learned head's
    (learned_leftover) - a stem, a label, the folio beside them - or SPAN
    numerals alone (the folio) in an anchor's column or row.  Or OWN, engine
    A's reading of the block, is all the learned head and SPAN is what is
    left of it, character for character in order (a subsequence): the merge
    left some of the head out - measured on the offline replay of a book's
    sealed pages, with a trial since removed, the first character of a
    volume label was written as nothing and the two left of it were no
    label, and stayed.  A block in the band only by a learned region must hold a stem or
    a label: numerals there may be the body's (a list number, a date) on a
    page whose anchor engine A did not read."""
    chars = CJK(span)
    if not chars or why is None:
        return None
    left, found = learned_leftover(chars, learned, page)
    read = CJK(own or "")
    if left and read and read != chars:
        rest = iter(read)
        if all(c in rest for c in chars):
            left, found = learned_leftover(read, learned, page)
            found = dict(found, engineA=[read]) if not left else found
    if left:
        return None
    if why.startswith("region") and not (found["stems"] or found["labels"]):
        return None
    return {"band": why, **{key: value for key, value in found.items() if value}}


def _is_furniture(run: str, furniture: frozenset[str] | set[str],
                  minimum: int, at_edge: bool = False) -> bool:
    """Whether a deleted run is a running head or folio rather than text.

    The test is a CONTIGUOUS recurring stretch, not a fraction of the whole,
    because a running head is a book title plus a chapter title: a provincial finance report第三
    編省稅 recurs across the whole book while 第八章保險稅 recurs only inside one
    chapter, so only half its n-grams are book-wide and a fraction test misses
    it.  What is left over must itself be too short to be a lost sentence,
    otherwise a deletion of real text WITH a running head attached would be
    waved through.
    """
    if len(run) < FURNITURE_GRAM:
        return False
    # Union coverage, not a contiguous stretch: the running head is itself OCR'd
    # differently from page to page (a provincial finance report / 記實, 第三編 / 第三輯), and a
    # mutation in the MIDDLE splits a contiguous match into two halves that are
    # each too short.  Measured on a fiscal report: the 記 variant survived the
    # contiguous test because the damage was near the start, the 輯 variant did
    # not because it was in the middle.  What is left uncovered must still be
    # too short to be a lost sentence.
    covered = bytearray(len(run))
    for index in range(len(run) - FURNITURE_GRAM + 1):
        if run[index:index + FURNITURE_GRAM] in furniture:
            for offset in range(index, index + FURNITURE_GRAM):
                covered[offset] = 1
    hits = sum(covered)
    if hits < FURNITURE_GRAM:
        return False
    if (len(run) - hits) < minimum:
        return True
    # A run at the page edge that is anchored by the book's own recurring head
    # is the running head, even when it trails a section title unique to this
    # page: a fiscal report prints [book][chapter][section][folio] up there, and
    # only the first half recurs.  Away from the edge the same leftover would be
    # a lost sentence, so the position is doing real work here.
    return at_edge


def folio_figures(chars: str, at: int, step: int) -> list[int]:
    """Where the figures of a folio printed beside a running head stand in
    CHARS, reading from AT away from the head (STEP -1 before it, 1 after
    it): the numerals in a row, however many, passing over one character
    that is not a numeral - a figure misread, not returned, so it is still
    counted as unexplained - when figures stand beyond it and the folio has
    two or more.  Measured on a book's drafts: engine B wrote 口 (once 囗)
    for the folio's 〇 beside the head on 39 of its 344 pages, as the last
    figure next to the head or the middle one (engine A: on none); a
    character with one figure or none beyond it is not taken for a figure
    misread."""
    figures: list[int] = []
    misread = None
    while 0 <= at < len(chars):
        if chars[at] in NUMERAL_CHARS:
            figures.append(at)
        elif misread is None:
            misread = at
        else:
            break
        at += step
    if misread is not None and (len(figures) < 2 or not any((at - misread) * step > 0 for at in figures)):
        figures = [at for at in figures if (at - misread) * step < 0]
    return figures


def furniture_leftover(text: str, furniture: frozenset[str] | set[str], heads: Sequence[str],
                       span: tuple[int, int] | None = None) -> int:
    """How many of TEXT's characters the book's furniture leaves unexplained:
    every stretch one of its measured running heads matches (is_running_head),
    with the folio printed beside it - the numerals touching that stretch on
    either side, however many - and every recurring n-gram (furniture_grams)
    is taken out, and what is left is counted: a figure misread, or text.

    SPAN (start, end) counts only those of TEXT's characters: a reading where
    its engine read it, TEXT being that engine's reading of the page, so a
    head or a recurring stretch the reading only touches explains it as well.
    The alignment, or a run placed once (D2), leaves a head in another
    disagreement than its folio.

    The folio is not counted against a length: past its 99th page a book
    prints three figures, and counting them as leftover (two were allowed)
    kept every such head and folio one engine read, written into the body
    with a deletion-refused doubt.  Measured on a book whose engine B read
    its head and folio where engine A read nothing there (every sealed 裁決
    answer at such a spot was 無, 9 runs of 9): in a stand-in run of the
    whole book, 裁決 answering 無 there, 45 of the 55 pages with that doubt
    lose it: the head and a three-figure folio, or the folio alone beside
    the head in engine B's reading (one, a piece of the head alone).  A
    figure misread (engine B's 口 for 〇) is left over, and the allowance for
    leftover takes it - as it takes any character or two beside the
    furniture, which an answer may leave out anywhere (ANSWER_DELETION_LIMIT;
    measured once in that run: a character engine A did not read before the
    folio, at the foot of the page's last column)."""
    chars = CJK(text)
    lo, hi = span if span is not None else (0, len(chars))
    if span is not None:
        # Only what can reach the span: a head its folio's figures lead to, a
        # head or an n-gram overlapping it.
        reach = max([FURNITURE_GRAM, *(len(CJK(head)) for head in heads)])
        left = min([lo, *folio_figures(chars, lo - 1, -1)])
        right = max([hi - 1, *folio_figures(chars, hi, 1)]) + 1
        cut = max(0, left - 1 - reach)
        chars, lo, hi = chars[cut:right + 1 + reach], lo - cut, hi - cut
    covered = bytearray(len(chars))
    for head in heads:
        head = CJK(head)
        # The stretches most like the head, when is_running_head takes them for
        # it: an exact copy, or else the misread one - the best of the
        # stretches that overlap, so a copy one character off, which takes in
        # a character of the text beside the head, is not the head.
        found = [(difflib.SequenceMatcher(None, chars[start:start + len(head)], head, autojunk=False).ratio(),
                  start) for start in (range(len(chars) - len(head) + 1) if head else ())
                 if is_running_head(chars[start:start + len(head)], [head])]
        for ratio, start in found:
            if any(other > ratio and abs(where - start) < len(head) for other, where in found):
                continue
            covered[start:start + len(head)] = b"\1" * len(head)
            for step in (-1, 1):
                for at in folio_figures(chars, start - 1 if step < 0 else start + len(head), step):
                    covered[at] = 1
    for start in range(len(chars) - FURNITURE_GRAM + 1):
        if chars[start:start + FURNITURE_GRAM] in furniture:
            covered[start:start + FURNITURE_GRAM] = b"\1" * FURNITURE_GRAM
    return (hi - lo) - sum(covered[lo:hi])


def readings_dropped(readings: Mapping[str, str], merged: str,
                     furniture: frozenset[str] | set[str] = frozenset(), heads: Sequence[str] = (),
                     marks: Mapping[str, Collection[str]] | None = None,
                     explained: Mapping[str, Collection[tuple[int, int]]] | None = None) -> list[dict[str, Any]]:
    """Each place where the merge dropped more than ANSWER_DELETION_LIMIT
    characters one engine read (READINGS: engine -> its reading as merged)
    that the merged text holds nowhere: the user's fix of 2026-09-27, a doubt
    whenever any rule drops three characters or more.

    Measured: a line engine A skipped was fused with the heading beside it and
    the preference wrote engine A's one character for the 18 engine B read -
    17 printed characters gone, with nothing to say so (engineCoverage recorded
    merged 1392 < engine A 1395 < engine B 1411, and nothing read it;
    droppedRuns compares the merged text with the formatted page only).

    Net: each engine's reading is aligned with the merged text (both
    variant-folded), and where it has more than ANSWER_DELETION_LIMIT
    characters more than the merged text there, a character of it is dropped
    only when the merged text holds fewer of that character than the reading
    does, all over the page, and no DROP_GRAM characters around it stand in
    the merged text as often as in the reading.  So text written once in
    another place (D2, an order conflict's other reading), a table header one
    engine read by rows and the other by columns are not dropped; nor is a
    substitution (as many characters written as read).  Measured on the
    round-2 benchmark replay: with the n-gram test alone, a header read by
    rows (6 characters) and an inscription both engines misread, each
    differently (6 characters with the title beside it, two of them
    misread), were reported dropped.  As often, not merely anywhere (the
    round-3 review): a list item only engine B read, fused with engine A's
    folio and settled by the preference for engine A, worded like the item
    beside it (the same nine characters but two) had every stretch but
    those two characters' somewhere in the merged text - in the other item -
    and 9 printed characters went with no doubt.  But where every character
    dropped so stands in the merged text inside a DROP_GRAM-character
    stretch, only fewer times, the stretch is a second copy of text written
    there - the engine read it once more - and is not reported: measured on
    the benchmark replay, engine A read the notes of a textbook table twice
    (in another order, and part of them a third time), engine B once, the
    page wrote them once, and counting alone reported engine A's other copy
    (38 to 77 characters, every run); and a line of signatures engine A read
    twice would be reported where the preference for engine B wrote it once,
    as printed.  The parallel list item has characters in no stretch of the
    merged text (the two it words differently), and is reported whole.
    Which of two copies the print has, where the page prints a line twice
    and one engine read it once, the texts cannot say either way.

    Not dropped either: the book's furniture (a place its running head, the
    folio beside it and its recurring n-grams explain but for
    INSERTION_LIMIT, furniture_leftover, where the engine read it); the glyphs
    this book's drafts show that engine writing for printed marks (MARKS,
    mark_aliases); an engine's reading of the page that loops (the order
    conflict's test, unsupported_reading - measured: engine B read a table
    header some 30 times over, 1,661 characters at 0.05 distinct 4-grams, and
    every copy was reported); and EXPLAINED (engine -> spans of its reading), what a
    rule that reads it as no text of the page left out - furniture only one
    engine read (D15, a split's surplus), a table's furniture row, a bracket, a
    mark or a lone circle read as a character - or what D16 let 裁決 answer
    away because the merged text holds it nearby (reading_nearby).  Measured on
    the same replay: brackets engine B read as numerals around a figure,
    which the bracket rule took for brackets, were reported dropped."""
    folded = variant_fold(CJK(merged))
    grams = gram_counts(folded)
    held_counts = Counter(folded)
    out: list[dict[str, Any]] = []
    for engine, text in readings.items():
        chars = CJK(text or "")
        if len(chars) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars) < DEGENERATE_NGRAM_RATIO:
            continue
        mine = variant_fold(chars)
        own = gram_counts(mine)
        glyphs = set((marks or {}).get(engine) or ())
        # How many of each character the reading has beyond the merged text.
        excess = Counter(mine)
        excess.subtract(held_counts)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mine, folded, autojunk=False).get_opcodes():
            if tag == "equal" or (i2 - i1) - (j2 - j1) <= ANSWER_DELETION_LIMIT:
                continue
            held = bytearray(i2 - i1)
            for k in range(max(0, i1 - DROP_GRAM + 1), min(i2, len(mine) - DROP_GRAM + 1)):
                if grams[mine[k:k + DROP_GRAM]] >= own[mine[k:k + DROP_GRAM]]:
                    for at in range(max(k, i1), min(k + DROP_GRAM, i2)):
                        held[at - i1] = 1
            for lo, hi in (explained or {}).get(engine) or ():
                for at in range(max(lo, i1), min(hi, i2)):
                    held[at - i1] = 1
            lost = []
            for k in range(i1, i2):
                if not held[k - i1] and excess[mine[k]] > 0:
                    excess[mine[k]] -= 1
                    lost.append(k)
            if len(lost) <= ANSWER_DELETION_LIMIT or {chars[k] for k in lost} <= glyphs:
                continue
            lo, hi = lost[0], lost[-1] + 1
            if furniture_leftover(chars, furniture, heads, (lo, hi)) <= INSERTION_LIMIT:
                continue
            if all(any(grams[mine[j:j + DROP_GRAM]] for j in range(max(0, k - DROP_GRAM + 1),
                                                                  min(k, len(mine) - DROP_GRAM) + 1))
                   for k in lost):
                # Each character of it stands in the merged text with the
                # characters around it, only fewer times: a second copy of text
                # written there, which the engine read once more.
                continue
            if hi - lo > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars[lo:hi]) < DEGENERATE_NGRAM_RATIO:
                continue
            out.append({"engine": engine, "text": chars[lo:hi], "characters": len(lost), "at": lo,
                        "contextBefore": chars[max(0, lo - 8):lo], "contextAfter": chars[hi:hi + 8]})
    return out
