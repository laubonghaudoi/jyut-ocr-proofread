"""Draft alignment, segment crop selection, loop/uncorroborated guards, and adjudication."""

from __future__ import annotations

import bisect
from collections import Counter
import difflib
import itertools
import os
from pathlib import Path
import re
import sys
from typing import Any, Collection, Mapping, Sequence
import unicodedata

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from jyut_ocr.core import content_fidelity as _content_fidelity  # noqa: E402
from _proofread_core import (  # noqa: E402
    ADJUDICATE_SYSTEM, ANSWER_DELETION_LIMIT, BLIND_SYSTEM, BLIND_USER, CIRCLE_CHARS, CJK,
    DEGENERATE_NGRAM_RATIO, DROP_GRAM, EMPTY_VERDICTS, INSERTION_LIMIT, LOOPED_READING,
    NEARBY_WINDOW, NUMERAL_CHARS, NUMERAL_CONFLICT_LIMIT, RESEARCH_LINE, UNCORROBORATED_MINIMUM,
    UNSEEN_VERDICTS, aggregate_usage, distinct_ngram_ratio, facade_attr, is_numeral_conflict,
    is_rare_codepoint_conflict, norm_for_diff, strip_grounding, verdict_answer,
)
from _proofread_crops import (  # noqa: E402
    block_is_vertical, crop_band, crop_block, crop_glyph, upscale_crop,
)
from _proofread_variants import (  # noqa: E402
    simplified_as_read, simplified_form, variant_fold,
)


def split_edge_circle(seg: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int] | None:
    """A single circle at either end of a disagreement, split off from the rest.

    A printed circle next to a character the engines also read differently
    comes out of the alignment fused with that substitution - engine A 〇旂,
    engine B 旒 - so it is neither a lone circle nor a numeral conflict, and the
    whole segment went to the engine preference, circle and all (measured: 3
    of the 20 reading errors left on five pages after the lone-circle rule).
    Returns the two segments in reading order and the index of the circle's,
    or None when neither side has a single circle at an end the other lacks.
    The caller keeps the split only when the circle's segment is a lone circle
    (is_lone_circle), so nothing else changes.  Contexts are cut from engine
    A's stream, as align_drafts cuts them.
    """
    if seg["tag"] == "equal":
        return None
    for side, other in (("a", "b"), ("b", "a")):
        text, facing = seg[side], seg[other]
        if len(text) < 2:
            continue
        if text[0] in CIRCLE_CHARS and text[1] not in CIRCLE_CHARS and facing[:1] not in CIRCLE_CHARS:
            lead, circle, rest = True, text[0], text[1:]
            beside = rest[:1] + facing[:1]
        elif text[-1] in CIRCLE_CHARS and text[-2] not in CIRCLE_CHARS and facing[-1:] not in CIRCLE_CHARS:
            lead, circle, rest = False, text[-1], text[:-1]
            beside = rest[-1:] + facing[-1:]
        else:
            continue
        # The character next to the circle in the engine that read it, and the
        # other engine's character at that end: a numeral in either makes the
        # circle a likely zero (engine A 一九工, engine B 一九二〇), which the
        # contexts - cut from engine A's stream - would not show.
        if any(c in NUMERAL_CHARS or c.isdigit() for c in beside):
            continue
        on_a = side == "a"
        remainder = {side: rest, other: facing}
        remainder["tag"] = "replace" if remainder["a"] and remainder["b"] else ("delete" if remainder["a"] else "insert")
        mark = {"tag": "delete" if on_a else "insert", "a": circle if on_a else "", "b": "" if on_a else circle}
        if "lopsided" in seg:
            # A piece of a split lopsided replacement stays one (split_lopsided).
            remainder["lopsided"] = mark["lopsided"] = seg["lopsided"]
        before, after = seg["before"], seg["after"]
        if lead:
            mark.update(before=before, after=(remainder["a"] + after)[:8])
            remainder.update(before=(before + mark["a"])[-8:], after=after)
            return [mark, remainder], 0
        mark.update(before=(before + remainder["a"])[-8:], after=after)
        remainder.update(before=before, after=(mark["a"] + after)[:8])
        return [remainder, mark], 1
    return None


def split_lopsided(seg: Mapping[str, Any], a_edges: Collection[int] = (),
                   b_edges: Collection[int] = ()) -> tuple[list[dict[str, Any]], str] | None:
    """A lopsided replacement - one engine read INSERTION_LIMIT characters or
    more beyond the other there - split into a like-for-like part and the
    surplus, which is text only one engine read (the user's fix of 2026-09-27).

    The alignment fuses a line one engine skipped with a character or two the
    engines read differently next to it, and the preference then wrote the
    preferred engine's short side, deleting the rest with no doubt: measured,
    engine A skipped a printed line and a different form of the next heading's
    first character (決/决) fused them, 17 characters lost on two runs; with
    engine B v1.0, engine A's bullet read as 〇 lost 8 characters of
    signatures on one page, and engine A's two-figure folio a 7-character
    list item on another - neither trigger a variant, so the folded
    alignment alone does not stop them.  An insertion was always kept (the
    preference falls back to the side that has text); a lopsided replacement
    was not.

    The like-for-like part is the character or two read differently beside
    the skipped line: a short side of more than INSERTION_LIMIT characters is
    taken for text of its own, not a misreading of part of the other side
    (measured on the benchmark replay, with every page split: engine A's
    four-character fold title, set against a sentence only engine B read,
    was paired with the sentence's first four characters where a line of
    engine B's happened to break after them).  Where it lies is read off
    each engine's own structure, as positions inside the sides where a
    layout block of engine A's begins or ends (A_EDGES) or a line of engine
    B's does (B_EDGES):

    - either side is whole blocks or lines of its engine (it begins and ends
      at one), or the short side is longer than INSERTION_LIMIT: the other
      side is no reading of any part of it - both are text only one engine
      read ("none"; the short side first when it ends a block or line of its
      own engine, else after: an insertion at a block's end goes to the next
      block, block_spans);
    - else the surplus ends where a block or line of the long side's engine
      begins, with the short side read against what follows ("end"), or
      begins where one ends ("start") - when only one of the two holds;
    - else the short side begins a block or line of its engine, so the text
      its engine skipped came before it ("end"), or ends one ("start");
    - else nothing says, and both are text only one engine read ("none").

    Returns the segments in reading order and how the sides were set, or None
    when SEG is not lopsided (or is a numeral run fused for the crop, engine
    B's reading of a looped block, a window of an order-conflict table
    block).  Contexts are cut from engine A's stream, as align_drafts cuts
    them.
    """
    a, b = seg["a"], seg["b"]
    if (seg["tag"] != "replace" or not a or not b or abs(len(a) - len(b)) <= INSERTION_LIMIT
            or seg.get("fused") or seg.get(LOOPED_READING) or "window" in seg or "settled" in seg
            or is_numeral_conflict(dict(seg)) or is_rare_codepoint_conflict(dict(seg))):
        # A number (or a rare codepoint) is read off the crop whole, never
        # settled by a preference.
        return None
    long_side, short_side = ("a", "b") if len(a) > len(b) else ("b", "a")
    n, m = len(seg[long_side]), len(seg[short_side])
    edges = {"a": set(a_edges), "b": set(b_edges)}
    own, other = edges[long_side], edges[short_side]
    if m > INSERTION_LIMIT or (0 in own and n in own) or (0 in other and m in other):
        how = "none"
    elif (n - m in own) != (m in own):
        how = "end" if n - m in own else "start"
    elif (0 in other) != (m in other):
        how = "end" if 0 in other else "start"
    else:
        how = "none"
    if how == "none":
        alone = {long_side: seg[long_side], short_side: ""}
        short = {short_side: seg[short_side], long_side: ""}
        parts = [short, alone] if m in other else [alone, short]
    else:
        cut = n - m if how == "end" else m
        head = {long_side: seg[long_side][:cut], short_side: seg[short_side] if how == "start" else ""}
        tail = {long_side: seg[long_side][cut:], short_side: seg[short_side] if how == "end" else ""}
        parts = [head, tail]
    out, a_seen = [], ""
    for part in parts:
        a_rest = "".join(p["a"] for p in parts[len(out) + 1:])
        tag = "replace" if part["a"] and part["b"] else "delete" if part["a"] else "insert"
        out.append({"tag": tag, "a": part["a"], "b": part["b"],
                    "before": (seg["before"] + a_seen)[-8:], "after": (a_rest + seg["after"])[:8],
                    "lopsided": {"engineA": a, "engineB": b, "how": how,
                                 "surplus": tag != "replace"}})
        a_seen += part["a"]
    return out, how


def gram_counts(text: str) -> Counter:
    """How often each DROP_GRAM-character stretch stands in TEXT."""
    return Counter(text[i:i + DROP_GRAM] for i in range(len(text) - DROP_GRAM + 1))


def moved_share(text: str, reading: str) -> float:
    """How much of TEXT the other engine's READING holds as well: the share of
    its characters inside a DROP_GRAM-character stretch that stands in the
    reading too (both variant-folded).

    Anywhere in the reading, not as often as in TEXT's engine's reading
    (readings_dropped counts): a line one engine read twice has each stretch
    twice in its reading and once in the other's, and counted so its second
    copy was held 0.00 and split into a second written copy (measured: a line
    of signatures, 1.00 by this share).  Counted against TEXT's own
    stretches instead, the parallel list item readings_dropped now reports
    stays 0.78, and 9 of the 448 lopsided replacements of the corpus's page
    pairs (folded) cross MOVED_SHARE, three of them a run of one character
    one engine repeated (餘餘…, 〇〇〇…) set against the other's text, which
    this share leaves to the preference."""
    text, reading = variant_fold(CJK(text)), variant_fold(CJK(reading))
    if not text:
        return 0.0
    grams = {reading[i:i + DROP_GRAM] for i in range(len(reading) - DROP_GRAM + 1)}
    held = bytearray(len(text))
    for i in range(len(text) - DROP_GRAM + 1):
        if text[i:i + DROP_GRAM] in grams:
            held[i:i + DROP_GRAM] = b"\1" * DROP_GRAM
    return sum(held) / len(text)


# Above this share of a lopsided replacement's longer reading held by the other
# engine's reading of the page, the replacement is text read in another place,
# not a line one engine skipped (split_lopsided_segments).
MOVED_SHARE = 0.5


def looped_reading(text: str) -> bool:
    """Whether an engine's reading (of a page, or of one stretch) repeats
    itself: the order conflict's test."""
    chars = CJK(text)
    return len(chars) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars) < DEGENERATE_NGRAM_RATIO


def read_elsewhere(long_text: str, other_reading: str, page_looped: bool) -> bool:
    """Whether a lopsided replacement's longer reading LONG_TEXT is no line its
    engine read and the other skipped, and so stays one replacement for the
    preference or 裁決 to settle: it loops, or its engine's reading of the
    page does (PAGE_LOOPED), or more than MOVED_SHARE of it stands in the
    other engine's reading of the page, OTHER_READING (moved_share) - text
    read in another order, or read twice by its engine.  The test
    split_lopsided_segments and align_drafts' fold both apply."""
    return page_looped or looped_reading(long_text) or moved_share(long_text, other_reading) > MOVED_SHARE


def split_lopsided_segments(segments: Sequence[dict[str, Any]], block_ends: Sequence[int] | None,
                            draft: str, draft2: str, prefer: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every lopsided replacement of SEGMENTS the engine preference PREFER ("A"
    or "B") would settle with its engine's shorter reading split
    (split_lopsided), with engine A's block boundaries at BLOCK_ENDS (None
    when the blocks do not split engine A's stream) and engine B's lines from
    DRAFT2.  Returns the segments and one record per split - the replacement
    as aligned, where it stood in each engine's stream, how it was split -
    whose number each of its pieces carries (lopsided["id"]).

    Only where the preferred engine's reading is the shorter: where it is the
    longer, the preference writes it and loses nothing (measured over the
    corpus's 1,898 pages with both engines' drafts, aligned folded: 91 such
    replacements on the three books with a preference, against 37 with the
    preferred reading the shorter - a split of the 91 would only have made
    questions of the other engine's character or two).

    Not where the longer reading repeats itself, or its engine's reading of
    the page does (the order conflict's test): measured on the same pages,
    engine B ran into a loop at the foot of four pages (up to 7,704
    characters, 0.00 to 0.07 distinct 4-grams over its page), which the
    preference had set against engine A's last character or two - split, the
    loop would have been written into the page.

    Not a replacement whose longer reading the other engine read somewhere
    on the page (more than MOVED_SHARE of it, moved_share against the other
    engine's whole reading, DRAFT or DRAFT2): that is text read in another
    order, not a line skipped, and a split would write it twice.  Measured
    on the 26 replacements the round-2 scan found settled by the preference
    with three or more characters fewer than the longer reading: engine A
    read a line of signatures twice, and on another page two tiers of text
    in another order than engine B, and split, both came out with a second
    copy (their longer readings 1.00 and 0.94 held by the other engine); the
    three lines an engine skipped were held 0.00.  A running head the other
    engine read elsewhere on the page (0.40 to 1.00 held) is left to the
    preference as well when more than half of it is held, which wrote one
    copy of it before; split off otherwise, its surplus is left out as the
    book's furniture (lopsided_furniture)."""
    readings = {"a": CJK(draft), "b": CJK(draft2 or "")}
    looped = {side: looped_reading(text) for side, text in readings.items()}
    a_bounds = set([0, *block_ends]) if block_ends else set()
    b_bounds, at = set(), 0
    for line in strip_grounding(draft2 or "").splitlines():
        b_bounds.add(at)
        at += len(CJK(line))
    b_bounds.add(at)
    out: list[dict[str, Any]] = []
    splits: list[dict[str, Any]] = []
    a_at = b_at = 0
    for seg in segments:
        la, lb = len(seg["a"]), len(seg["b"])
        # Where engine B read it: its own place once D2 has placed a run
        # (bStart), else in segment order.
        b_at = seg.get("bStart", b_at)
        # D2's pieces of a run placed once (place_moved_once) are placed already.
        longer = "a" if len(seg["a"]) > len(seg["b"]) else "b"
        kept_whole = (longer == prefer.lower()
                      or read_elsewhere(seg[longer], readings["b" if longer == "a" else "a"], looped[longer]))
        found = (None if "movedFrom" in seg or "movedRemainderOf" in seg or kept_whole else
                 split_lopsided(seg, {p - a_at for p in a_bounds if a_at <= p <= a_at + la},
                                {p - b_at for p in b_bounds if b_at <= p <= b_at + lb}))
        if found:
            number = len(splits)
            splits.append({"id": number, "segment": dict(seg), "aStart": a_at, "bStart": b_at, "how": found[1]})
            b_piece = b_at
            for part in found[0]:
                piece = dict(part, lopsided=dict(part["lopsided"], id=number))
                if "bStart" in seg:
                    piece["bStart"] = b_piece
                b_piece += len(part["b"])
                out.append(piece)
        else:
            out.append(seg)
        a_at += la
        b_at += lb
    return out, splits


def fuse_numeral_runs(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent all-numeral segments into one conflict.

    When engine A reads （一） for a （二） and engine B reads 一一一, the
    alignment yields an *equal* 一 next to an *insert* 一一.  Adjudicating the
    insert alone can only add or drop strokes around a number that both engines
    got wrong, and the equal 一 is never questioned - measured result: （一）一本書
    and （二四四）.  So every maximal run of numeral-only segments that contains
    a disagreement is put to the crop as a single 甲/乙 pair covering the whole
    number; the verdict replaces the run.
    """
    def numeral_only(seg: dict[str, Any]) -> bool:
        return bool(seg["a"] or seg["b"]) and all(c in NUMERAL_CHARS for c in seg["a"] + seg["b"])

    # Peel numerals off the ends of equal segments so a shared 一 next to a
    # disputed 一一 is part of the same run.
    peeled: list[dict[str, Any]] = []
    for seg in segments:
        if seg["tag"] != "equal":
            peeled.append(seg); continue
        text = seg["a"]
        lead = 0
        while lead < len(text) and lead < NUMERAL_CONFLICT_LIMIT and text[lead] in NUMERAL_CHARS:
            lead += 1
        trail = 0
        while trail < len(text) - lead and trail < NUMERAL_CONFLICT_LIMIT and text[-1 - trail] in NUMERAL_CHARS:
            trail += 1
        parts = [text[:lead], text[lead:len(text) - trail], text[len(text) - trail:]]
        stream = seg["before"] + text + seg["after"]
        cursor = len(seg["before"])
        for part in parts:
            if part:
                peeled.append({"tag": "equal", "a": part, "b": part,
                               "before": stream[max(0, cursor - 8):cursor],
                               "after": stream[cursor + len(part):cursor + len(part) + 8]})
                cursor += len(part)
    segments = peeled

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(segments):
        if not numeral_only(segments[i]):
            out.append(segments[i]); i += 1
            continue
        j = i
        while j < len(segments) and numeral_only(segments[j]):
            j += 1
        run = segments[i:j]
        a = "".join(x["a"] for x in run); b = "".join(x["b"] for x in run)
        if all(x["tag"] == "equal" for x in run) or len(a) > NUMERAL_CONFLICT_LIMIT or len(b) > NUMERAL_CONFLICT_LIMIT:
            out.extend(run)
        else:
            out.append({"tag": "replace", "a": a, "b": b,
                        "before": run[0]["before"], "after": run[-1]["after"], "fused": len(run),
                        # Digits both engines agree on inside the run, in order.
                        # Two-engine agreement outranks a verdict: a marker 二
                        # fused with the figure 十七 that followed it was
                        # answered "二", and 十七 - read by both engines - was lost.
                        "agreed": [x["a"] for x in run if x["tag"] == "equal" and x["a"]]})
        i = j
    return out


def locate_segment(blocks: Sequence[Mapping[str, Any]], block_ends: Sequence[int],
                   block_lengths: Sequence[int], offsets_valid: bool, a_start: int,
                   seg: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, int | None]:
    """The engine-A layout block a disagreement sits in, and its offset there.

    Taken straight from the disagreement's position in engine A's character
    stream; text search at a block boundary was measured to crop the running
    foot instead of a table's last figure and delete it.
    """
    block, offset = None, None
    window = seg.get("window")
    if offsets_valid and isinstance(window, int) and 0 <= window < len(blocks):
        # A segment of an order-conflict table block read from engine B's
        # lines (window_merge) is that block's: its segments run in engine B's
        # order, so its place in engine A's stream only says which block it
        # is in, and at the block's end it would name the next block.
        start = block_ends[window - 1] if window else 0
        return blocks[window], min(max(a_start - start, 0), max(block_lengths[window] - 1, 0))
    if offsets_valid and blocks:
        k = next((i for i, end in enumerate(block_ends) if a_start < end), len(blocks) - 1)
        if not seg["a"] and k > 0 and a_start == block_ends[k - 1] and block_lengths[k] == 0:
            k -= 1
        block = blocks[k]
        offset = a_start - (block_ends[k - 1] if k else 0)
    if block is None:
        block = locate_block(list(blocks), dict(seg))
    return block, offset


def segment_crop(render: Path, render_bytes: bytes | None, block: Mapping[str, Any] | None,
                 offset: int | None, seg: Mapping[str, Any]) -> bytes | None:
    """The image an adjudicator is shown for one disagreement."""
    return facade_attr("segment_crop_kind", segment_crop_kind)(render, render_bytes, block, offset, seg)[0]


def segment_crop_kind(render: Path, render_bytes: bytes | None, block: Mapping[str, Any] | None,
                      offset: int | None, seg: Mapping[str, Any],
                      across: dict[str, Any] | None = None) -> tuple[bytes | None, str]:
    """segment_crop, and which kind of crop it is: "glyph" or "band" (cut to the
    disputed characters' column), "block" (a whole horizontal block), "table"
    (a whole table) or "page" (the whole render).  ACROSS is crop_glyph's: a
    glyph crop may then follow the text over a column break, in parts."""
    if block is not None and block.get("label") != "table" and \
            block_is_vertical(render, block["box"]) is False:
        # Column geometry is measured for vertical text only; for a horizontal
        # block show the whole block - larger, but it always contains the
        # disputed character, and the prompt's context locates it.
        crop = upscale_crop(crop_block(render, block["box"], pad=0.01))
        return (crop, "block") if crop else (render_bytes, "page")
    if block is not None and block.get("label") == "table":
        # A table block's text is HTML in row-major order; a position estimated
        # as if it were columns of running text lands on the wrong cell
        # (measured: three figures replaced by digits from elsewhere in the
        # grid). Show the whole table.
        crop = upscale_crop(crop_block(render, block["box"], pad=0.01))
        return (crop, "table") if crop else (render_bytes, "page")
    measured = (facade_attr("crop_glyph", crop_glyph)(render, block, offset, max(len(seg["a"]), len(seg["b"]), 1), across=across)
                if block is not None and offset is not None else None)
    if measured:
        return measured, "glyph"
    band = crop_band(render, block, seg, index=offset) if block else None
    return (band, "band") if band else (render_bytes, "page")


def segment_winner(seg: Mapping[str, Any], resolved: str, resolved_from: str) -> str:
    """Which engine the adjudicator sided with: A, B, neither, or undecided.

    This is the measurement behind a book's engine preference - counted by
    the profiler on probe pages and by the drift audit during a run.
    """
    if resolved_from in ("adjudicator-invalid", "adjudicator-partial", "adjudicator-deletion-refused",
                         "adjudicator-empty-unconfirmed", "adjudicator-neighbour-copy", "engine-A-default"):
        # The verdict was not used as given: no engine won it.
        return "undecided"
    a, b, got = CJK(seg["a"]), CJK(seg["b"]), CJK(resolved)
    if a == b:
        return "undecided"
    if got == a:
        return "A"
    if got == b:
        return "B"
    return "neither"


def adjudicate_prompt(seg: Mapping[str, Any], swap: bool = False, note: str = "") -> str:
    """The 裁決 question about one disagreement SEG (its before and after
    context, engine A's reading a and engine B's b), as adjudicate_segment
    asks it; SWAP shows engine B's reading first, NOTE is said before the
    context."""
    numeral = is_numeral_conflict(dict(seg))
    rare = is_rare_codepoint_conflict(dict(seg))
    rare_hint = (
        "\n呢個爭議有一方用咗罕見碼位（Unicode 擴展區）。佢可能係頁上真正嘅異體字形，"
        "亦可能係引擎亂配嘅碼位。請照圖上部件判定；如果圖上係常見字形，定案寫常見字。\n"
    ) if rare else ""
    numeral_hint = (
        "\n呢個爭議淨係關於數字／筆畫。甲乙兩邊寫嘅係**成個數字**（唔係其中一劃）："
        "一方多咗「一」之類，好多時係括號、格線或者筆畫被讀成咗字。"
        "請寫出圖上呢個位置**整個**數字（例如「二」「四」「十二」）；"
        "如果圖上根本冇數字，寫 `定案：無`。"
        "直排文字入面，括號係上下兩半（「︵」喺數字上面、「︶」喺下面），"
        "夾喺中間疊起嘅橫畫係**一個**數字：一橫係「一」、兩橫係「二」、三橫係「三」，"
        "唔好逐劃寫成「一一」。數橫畫之前，先由上至下逐項列出你見到嘅每一樣嘢"
        "（上弧、第一橫、第二橫……、下弧），上弧唔係橫畫，唔好將佢同第一橫合併計。\n") if numeral else ""
    prompt = (
        "兩個獨立 OCR 引擎喺呢個位置讀法唔同。兩個都可能錯，"
        "尤其係一方或雙方將異體字正規化咗。" + numeral_hint + rare_hint + (f"\n{note}\n" if note else "") + "\n"
        f"  上文：…{seg['before']}\n"
        # Which engine is shown first is varied by the caller: a judge that
        # leans toward the first option would otherwise be counted as one
        # engine being more accurate.
        f"  甲) {(seg['b'] if swap else seg['a']) or '（冇字）'}\n"
        f"  乙) {(seg['a'] if swap else seg['b']) or '（冇字）'}\n"
        f"  下文：{seg['after']}…\n\n"
        "先淨係描述圖上構件（唔准提甲乙），再由構件推出係邊個字，最後先同甲乙對照。"
        "若兩者皆非，寫出你見到嘅字。講完之後另起一行淨係寫：`定案：<字>`")
    return prompt


def adjudicate_segment(client: "Client", crop: bytes | None, seg: Mapping[str, Any],
                       max_tokens: int, fallback_engine: str | None = "A", swap: bool = False,
                       note: str = "", simplified: bool = False) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Ask the model to read one disagreement off its crop.

    NOTE says how the crop is made when it is not one stretch of the page
    (boundary_crop, BOUNDARY_NOTES).  SIMPLIFIED says the book is printed in
    simplified script (simplified_script): a character of the answer only
    traditional script has, which engine B did not read there, is written in
    the simplified form an engine read there, or in its one simplified form
    where neither engine read it or a form of it (simplified_as_read), and the
    answer as given is kept in the record (answeredTraditional).  Measured on
    a multi-volume archival compilation 2, 3 and 5 (2026-10-10): 91 answers so written (懸 where engine B
    read 悬), all looked at against the images: 76 now as printed, 14
    misreadings either way, 1 unclear.  The misreadings lost the
    TEXT-TRADITIONAL doubt that flagged 12 of them by chance (the lint takes
    恆 and 恒, 脫 and 脱 for one character); the 11 written in engine A's
    form where engine B read another character are a doubt now (塵 where 廑
    is printed: now 尘; answer-script-converted, converted_against_b), and
    so is each character written in the one form neither engine read.
    Returns the text to put in the merge, the sealed adjudication record
    (without its crop file name) and the call's usage metrics.
    """
    numeral = is_numeral_conflict(dict(seg))
    rare = is_rare_codepoint_conflict(dict(seg))
    prompt = adjudicate_prompt(seg, swap, note)
    verdict, metrics = client.ask_answering(ADJUDICATE_SYSTEM, prompt, crop, max_tokens, kind="adjudicate")
    verdict = (verdict or "").strip()
    match = RESEARCH_LINE.search(verdict)
    answer = verdict_answer(verdict)
    chosen, answer = answer is not None, answer or ""
    # 無 (and 冇, 空) is also a character.  When an engine read exactly that
    # character here, the answer is the character, not "no character":
    # measured, 裁決 read 無論 right, agreeing with engine A, and the page lost
    # its 無.
    explicit_empty = (bool(chosen) and answer in EMPTY_VERDICTS
                      and answer not in (CJK(seg["a"]), CJK(seg["b"])))
    # "Nothing printed here" answers a character one engine read out of a
    # bracket, a rule or a stroke.  Where both engines read ordinary text it
    # is no verdict - it would delete a printed character - and the fallback
    # engine's reading stands.
    empty_not_allowed = explicit_empty and bool(CJK(seg["a"]) and CJK(seg["b"])) and not numeral
    if empty_not_allowed:
        explicit_empty = False
    # 睇唔到: the crop does not show the spot (UNSEEN_VERDICTS).  The words are
    # no reading of it, whatever the readings' length: no verdict.
    unseen = chosen and answer in UNSEEN_VERDICTS and answer not in (CJK(seg["a"]), CJK(seg["b"]))
    picked = "" if explicit_empty or unseen else (CJK(answer) if chosen and not empty_not_allowed else "")
    as_given, picked = picked, (simplified_as_read(picked, CJK(seg["a"]), CJK(seg["b"])) if simplified else picked)
    converted = picked != as_given
    # An answer longer than both readings by more than ANSWER_DELETION_LIMIT
    # characters is words of the reasoning, not a reading of the spot (the
    # measurement at ANSWER_DELETION_LIMIT): no verdict either.  A numeral
    # question's answer is tested as given, so the script it is written in
    # cannot make it invalid: 萬 neither engine read is written 万, which
    # NUMERAL_CHARS does not hold, and the fallback would write engine A's
    # misread with no doubt.
    longer = max(len(CJK(seg["a"])), len(CJK(seg["b"])))
    invalid = (unseen or empty_not_allowed
               or (bool(as_given) and numeral and not all(c in NUMERAL_CHARS for c in as_given))
               or len(picked) > longer + ANSWER_DELETION_LIMIT)
    agreed = seg.get("agreed") or []
    if picked and agreed and not all(part in picked for part in agreed):
        # The verdict drops digits both engines read: keep them. With a
        # single agreed run at one end, splice it back around the verdict;
        # otherwise the verdict is not usable and the fallback stands.
        lead = seg["a"].startswith(agreed[0]) and len(agreed) == 1
        tail = seg["a"].endswith(agreed[-1]) and len(agreed) == 1
        if lead and not picked.startswith(agreed[0]):
            picked = agreed[0] + picked
        elif tail and not picked.endswith(agreed[-1]):
            picked = picked + agreed[-1]
        elif not all(part in picked for part in agreed):
            picked, invalid = "", True
    # Decision D3 asks whether anything is printed where one engine read a
    # short run the other did not (insertionQuestion).  An answer that writes
    # some text there but less than that engine read says text is printed and
    # disputes only how much: a count of glyphs, which is what failed -
    # measured on one ink-blotted spot, identical crop in four runs, 裁決 kept
    # one of the two characters engine B read in three of them, each time
    # wrong (the render shows both half-covered glyphs), and none of the 20
    # D3 questions of the benchmark had a right shorter answer.  The engine
    # that read the text counted it off the image; its reading is written and
    # the count is left to a person (insertion-partial).  An empty answer
    # (nothing printed) and one as long as the engine's reading stand.
    inserted = CJK(seg["a"] or seg["b"]) if seg.get("insertionQuestion") else ""
    partial = (not invalid and not explicit_empty and bool(inserted) and bool(picked)
               and len(picked) < len(inserted))
    # Any question: an answer that is neither engine's reading, or says nothing
    # is printed, and leaves out more than ANSWER_DELETION_LIMIT characters of
    # the reading it is a view of (the one it shares most characters with, the
    # longer on a tie) deletes printed text on a partial view - a crop that
    # showed part of a column, a table header read in part.  That reading is
    # kept and a person looks (deletion-refused).  Not a numeral question, which
    # asks for 無 where a stroke or a bracket was read as a figure, nor a
    # reading the book's own furniture explains (furnitureSides: its running
    # head, which 裁決 rightly says is not printed there).  Decision D16
    # (the user's ruling of 2026-09-27).  A question with deletionGuard False
    # (one character no engine read, confirm_unread) has 裁決's answer written
    # as it is.
    #
    # Nor the reading of an engine whose reply for the page was cut off
    # (cutOffSides: engine B's reply ended for want of tokens, and what it
    # read before its loop is merged, _proofread_page_inner): where the
    # reading the answer is a view of (by overlap, over both sides, as
    # above) is the cut-off engine's, the guard measures the answer against
    # a whole engine's reading instead - one that shares a character with
    # the answer (nothing printed is no verdict where both engines read
    # text, above, so a whole reading never meets it here) - so text both
    # engines read stays kept, and characters only the cut-off
    # engine read may be answered away (cutOffReadingAnswered; a reading the
    # merged text holds nowhere is still a reading-dropped doubt).  A whole
    # reading that shares no character with an answer that is a view of the
    # cut-off one is no reading of what 裁決 answered, and is not written
    # over it (FX3 review, made-up spot: engine A 甲乙丙丁戊, engine B, cut
    # off, 子丑寅卯辰巳, 裁決 子丑 - the guard wrote engine A's reading, which
    # 裁決 had not chosen; its loss is a reading-dropped doubt where the page
    # holds it nowhere else, and the answer's record names it,
    # unsharedWholeReadings).  An engine whose
    # reply ran out of tokens on a page was failing on that page, and what it
    # read there - its reply up to the loop, and its second look at the
    # blocks the reply left unread - is weaker evidence that text is printed
    # than a whole reading: measured on the cut-off pages of a fiscal table
    # printed over six pages, engine B's second look at one page's table read
    # its column of row labels as a grid of doubled cells, and the heading
    # after the table a second time, before the figures both engines read;
    # 裁決 answered that nothing is printed there, the guard kept those 65
    # characters over it, and the text they made between the two pages'
    # tables kept the table from being stitched to the page before (written
    # as a second table, under the next table's heading).
    kept_side = None
    waived_side = None
    unshared: list[str] = []
    written = "" if explicit_empty else picked
    if seg.get("deletionGuard", True) and not (partial or invalid or numeral) and (explicit_empty or (
            picked and picked not in (CJK(seg["a"]), CJK(seg["b"])))):
        cut_off = set(seg.get("cutOffSides") or ())

        def guarded(sides: Sequence[str]) -> str | None:
            """The side of SIDES whose reading the answer is a view of, when it
            leaves out more than ANSWER_DELETION_LIMIT of its characters."""
            side, reading = max(((side, CJK(seg[side])) for side in sides if CJK(seg[side])),
                                key=lambda pair: (sum((Counter(pair[1]) & Counter(written)).values()),
                                                  len(pair[1])),
                                default=(None, ""))
            if (side is not None and len(reading) - len(written) > ANSWER_DELETION_LIMIT
                    and side not in (seg.get("furnitureSides") or ())):
                return side
            return None

        viewed = guarded(("a", "b"))
        if viewed is not None and viewed in cut_off:
            whole = [side for side in ("a", "b") if side not in cut_off and CJK(seg[side])]
            sharing = [side for side in whole if sum((Counter(CJK(seg[side])) & Counter(written)).values())]
            unshared = [side for side in whole if side not in sharing]
            kept_side = guarded(sharing)
            # What the guard would have kept with the engine's reply whole.
            waived_side = viewed if kept_side is None else None
        else:
            kept_side = viewed
    if partial:
        piece = seg["a"] or seg["b"]
    elif kept_side is not None:
        piece = seg[kept_side]
    elif invalid:
        # A numeral question answered with prose (measured: 廣東 from the
        # running foot written into a figure), or any answer far longer than
        # both readings. Not a verdict: fall back.
        picked = ""
        piece = (seg["b"] if fallback_engine == "B" else seg["a"]) or seg["a"] or seg["b"]
    elif explicit_empty:
        # Digits both engines read stay: the verdict only says the disputed
        # one is not printed.
        piece = "".join(agreed)
    else:
        piece = picked or seg["a"] or seg["b"]
    resolved_from = ("adjudicator-partial" if partial
                     else "adjudicator-deletion-refused" if kept_side is not None
                     else "adjudicator-invalid" if invalid
                     else "adjudicator-empty" if explicit_empty
                     else "adjudicator" if picked else "engine-A-default")
    record = {
        "trigger": ("numeral-disagreement" if numeral
                    else "rare-codepoint-disagreement" if rare else "engine-disagreement"),
        "context_before": seg["before"], "context_after": seg["after"],
        "draft_reading": seg["a"], "writer_reading": seg["b"],
        "shownFirst": "B" if swap else "A",
        "resolved": piece,
        "resolved_from": resolved_from,
        "winner": segment_winner(seg, piece, resolved_from),
        "needs_research": (match.group(1).lower() == "yes") if match else True,
        "research_classified": match is not None,
        "verdict": RESEARCH_LINE.sub("", verdict).strip(),
        # What 裁決 wrote where its answer was not used as written: in a book
        # printed in simplified script, in the form an engine read (the text
        # D16 measures, and writes where the reading stands nearby), with the
        # answer as given in answeredTraditional, which the doubts quote.
        **({"answered": written} if partial or kept_side is not None else {}),
        # 裁決 said the crop does not show the spot (UNSEEN_VERDICTS).
        **({"unseen": True} if unseen else {}),
        **({"answeredTraditional": as_given} if converted else {}),
        # The cut-off engine whose reading the deletion guard would have kept
        # over the answer, had its reply been whole (cutOffSides).
        **({"cutOffReadingAnswered": waived_side.upper()} if waived_side is not None else {}),
        # A whole engine's reading here that shares no character with the
        # answer, so the guard did not keep it over the answer.
        **({"unsharedWholeReadings": [side.upper() for side in unshared]}
           if waived_side is not None and unshared else {}),
    }
    return piece, record, metrics


def left_out_of(reading: str, answer: str) -> str:
    """The characters of READING an answer leaves out, in order (variant-folded
    alignment; the characters are READING's)."""
    out = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, variant_fold(reading), variant_fold(answer),
                                                     autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            out.append(reading[i1:i2])
    return "".join(out)


def reading_nearby(left_out: str, before: str, after: str, window: int = NEARBY_WINDOW) -> dict[str, Any] | None:
    """Where the characters LEFT_OUT already stand in the merged text around a
    spot (BEFORE and AFTER: the merged text either side of it), within WINDOW
    characters of it: all of them, in their order, close together - spread
    over at most twice their number of characters, others between.  The
    nearest such stretch, {side, distance, span, text}, or None - always None
    for a reading of numerals only.

    D16 keeps an engine's reading 裁決 answers away; measured on four runs, 10
    of its 26 refusals kept a second copy of text the page already had beside
    the spot: a table header of two-character cells engine A read row by row,
    interleaving the cells, where engine B read three of their characters by
    columns first (4 refusals), and figures engine B read a second time
    under the next row (6).  In order and close together, not merely present:
    a six-character price cell only engine B read must not be answered away
    because two of its characters stand in the next cell.

    Not figures.  Which of two copies of a number is the second one the page
    does not say: on the benchmark replay the one figure refusal the test let
    go (in two runs) took away the copy in its right cell and left the other,
    run onto the cell before it - one more table cell wrong in each run.  And
    figures stand around any place in a table by chance: every stretch of the
    replayed benchmark pages' merged text, looked for around its own place
    with no copy there, is found 27% of the time for three numerals and 7%
    for five in order within twice their span (1.1% and 0.4% for other
    text), and even the same figures in a row stand there 15% and 5% of the
    time (a table repeats its zeros).  A figure refusal stays refused, and a
    person looks (deletion-refused)."""
    want = variant_fold(CJK(left_out))
    if not want or all(c in NUMERAL_CHARS for c in want):
        return None
    best: dict[str, Any] | None = None
    for side, text in (("before", CJK(before)[-window:] if window else ""), ("after", CJK(after)[:window])):
        folded = variant_fold(text)
        for start in range(len(folded)):
            if folded[start] != want[0]:
                continue
            k, at = 0, start
            while at < len(folded) and k < len(want) and at - start < 2 * len(want):
                if folded[at] == want[k]:
                    k += 1
                at += 1
            if k < len(want):
                continue
            distance = len(folded) - at if side == "before" else start
            if best is None or distance < best["distance"]:
                best = {"side": side, "distance": distance, "span": at - start, "text": text[start:at]}
    return best


# How near a spot, in characters of the merged text either side, the text a
# 裁決 answer repeats may stand for --neighbour-guard to take the answer for a
# copy of it (neighbour_copy): 1 is the character right beside the spot.
NEIGHBOUR_WINDOW = 2
# The shortest one-engine reading that, written right beside its own copy,
# is doubted as a line read twice (beside_copies): shorter stretches repeat
# in print (個個, 年年, a table's zeros).  Also the shortest stretch read_twice
# settles as read twice: two measured double reads of 5 characters stay
# written twice.
BESIDE_COPY_MIN = 6


def read_twice(segments: Sequence[Mapping[str, Any]], blocks: Sequence[Mapping[str, Any]],
               block_ends: Sequence[int]) -> list[tuple[int, int]]:
    """The disagreements whose engine-A side is engine A reading again the
    text right beside it, each written once (engine-A-read-twice), with K,
    how many of its characters the copy has before it (the rest after): a
    stretch of BESIDE_COPY_MIN characters or more that engine A's own text
    holds again right before it, right after it, or split across both (A
    read P1 P1 P2 P2 where B read P1 P2: the stretch P1 P2), variant-folded,
    where engine A's layout (BLOCKS, BLOCK_ENDS: where each block's text
    ends in engine A's stream) says the same print was read twice: the
    stretch and its copy in one block, or in blocks whose boxes intersect.
    Not a stretch that is one unit repeated (哈哈…, 同上同上…, a run of 〇),
    nor one in a table block, where print repeats itself beside itself.

    Measured over the 4,241 pages of 13 books that have both engines'
    drafts, aligned as the merge aligns them, model off (2026-10-24; the 90
    order-conflict pages are not merged): 13 such copies of 7 to 116
    characters, each looked at on the page image, all a line or two printed
    once and read twice - a second layout box lying on the first or last
    line of another block (boxes overlapping by 34-70%), and once two
    paragraphs decoded twice inside one block.  9 of them stand on pages
    sealed earlier (six books): 3 were written whole (one by the block
    union, #34), 4 with a scrap of 裁決's answer beside the copy - shown the
    line printed once, it answers a piece of it - and 2 once (裁決 answered
    無).  The 11 exact copies printed twice (a chapter title in the running
    head and as the heading, a title printed on both leaves of a scan) all
    stand in blocks whose boxes do not meet.  Two more double reads, of 5
    characters, are shorter than BESIDE_COPY_MIN and go the usual way.
    Exact copies only: a near copy inside one block is parallel text (two
    entries of a list of offices that differ only in the office's name).
    Engine B's own repeats are not looked at: it has no layout to tell a
    line read twice from one printed twice."""
    fa = variant_fold("".join(x["a"] for x in segments))
    if not blocks or not block_ends or block_ends[-1] != len(fa):
        return []

    def spanned(lo: int, hi: int) -> range:
        """The blocks engine A's characters LO..HI-1 stand in."""
        return range(bisect.bisect_right(block_ends, lo), bisect.bisect_right(block_ends, hi - 1) + 1)

    def meet(j: int, k: int) -> bool:
        p, q = blocks[j].get("box"), blocks[k].get("box")
        return j == k or (bool(p) and bool(q) and min(p[0] + p[2], q[0] + q[2]) > max(p[0], q[0])
                          and min(p[1] + p[3], q[1] + q[3]) > max(p[1], q[1]))

    found: list[tuple[int, int]] = []
    at = 0
    for i, x in enumerate(segments):
        copy, n = variant_fold(x["a"]), len(x["a"])
        if (x["tag"] != "equal" and n >= BESIDE_COPY_MIN and (copy + copy).find(copy, 1) == n
                and not x.get(LOOPED_READING) and "settled" not in x and "window" not in x):
            # K characters of the copy before the stretch, the rest after it.
            k = next((k for k in range(n, -1, -1)
                      if fa[:at].endswith(copy[:k]) and fa.startswith(copy[k:], at + n)), None)
            if k is not None:
                here = [*spanned(at, at + n)]
                there = ([*spanned(at - k, at)] if k else []) + ([*spanned(at + n, at + 2 * n - k)] if k < n else [])
                if (all(blocks[j].get("label") != "table" for j in here + there)
                        and all(meet(j, t) for j in here for t in there)):
                    found.append((i, k))
        at += n
    return found


def neighbour_copy(answer: str, reading_a: str, reading_b: str, before: str, after: str,
                   shown_before: str | None = None, shown_after: str | None = None,
                   window: int = NEIGHBOUR_WINDOW, simplified: bool = False) -> dict[str, Any] | None:
    """Switch --neighbour-guard: whether 裁決's ANSWER at a spot, which is
    neither engine's reading there (READING_A, READING_B), is the text the
    merged text already has beside the spot (BEFORE and AFTER: the merged
    text either side of it) - the whole answer, standing WINDOW characters or
    fewer from the spot (distance 1: right beside it) - while neither
    engine's reading at the spot holds it.  Given SHOWN_BEFORE and
    SHOWN_AFTER (the context 裁決 was shown: engine A's text either side),
    the text must stand at the same place there too: a character of the
    merged text beside the spot that the context does not have there is
    another question's answer, which 裁決 never saw - measured on a folio,
    two answers that each read 二, two places apart, where the print has
    no 二; taken for a copy, the second would have given way to engine A's
    two characters of furniture.  Returns {side, distance, text} (the
    nearest such copy, "before" first on a tie), or None.  Variant-folded:
    an answer that is an engine's reading in another form is that reading;
    in a book printed in simplified script (SIMPLIFIED), in another script
    too (simplified_form): 裁決 often answers in traditional script there.
    Measured on four simplified volumes' sealed answers (2026-10-24): 241
    written as given hold a character only traditional script has; 5 of
    them are a copy of the text beside the spot in its simplified form and
    no copy as given, all 5 wrong, 3 where the fallback engine read right.

    What it assumes: an answer that repeats the text beside its spot, where
    neither engine read that text a second time, is 裁決 reading the
    neighbour - a crop that does not show the spot, or shows the neighbour
    beside a mark, a bracket, a folio or a rule an engine read as a
    character - not a character printed twice.  Evidence, not a law: where
    either engine read the answer at the spot too (the doubling read by an
    engine), or the answer stands farther away, it is not taken for a copy;
    a doubled character that both engines misread at its second place is
    the case this gets wrong (the engines' reading is then kept, with a
    doubt).  Measured on the sealed runs: of the 'neither engine' answers of
    one character, the share that repeats a character d places from the spot
    is several times the chance rate at d = 1 and 2 and near it from 3 on,
    and every answer this takes for a copy there, checked against a golden
    or opened on its crop and page, was wrong (the numbers are in the
    reference, long-book-workflow.md)."""
    fold = (lambda text: variant_fold(simplified_form(text))) if simplified else variant_fold
    got = fold(CJK(answer))
    if not got:
        return None
    for reading in (reading_a, reading_b):
        if got in fold(CJK(reading)):
            return None
    size = len(got)

    def ending(text: str, distance: int) -> str:
        """The SIZE characters of TEXT that end DISTANCE - 1 characters before its end."""
        end = len(text) - (distance - 1)
        return text[end - size:end] if end - size >= 0 else ""

    def starting(text: str, distance: int) -> str:
        return text[distance - 1:distance - 1 + size]

    left, right = CJK(before), CJK(after)
    for distance in range(1, window + 1):
        for side, text, shown, cut in (("before", left, shown_before, ending), ("after", right, shown_after, starting)):
            if fold(cut(text, distance)) != got:
                continue
            if shown is not None and fold(cut(CJK(shown), distance)) != got:
                continue
            return {"side": side, "distance": distance, "text": cut(text, distance)}
    return None


def confirm_nothing(client: "Client", crop: bytes | None, seg: Mapping[str, Any],
                    answer: tuple[str, dict[str, Any], dict[str, Any]], max_tokens: int,
                    fallback_engine: str = "A") -> tuple[str, dict[str, Any], dict[str, Any]]:
    """裁決's "nothing is printed here" (無), on a crop cut where the spot was
    estimated to be (SEG["coverage"]: a glyph or band crop), deletes what an
    engine read only when the crop is shown to hold the spot: the user's fix
    of 2026-09-27 (measured: two crops that did not show the spot, 無 both
    times, a character engine A read right deleted in two runs of two
    pages).

    The crop is transcribed blind (BLIND_SYSTEM: no candidates, no
    context) and the spot is looked for in the
    transcription between the characters engine A read around it
    (crop_shows_spot).  Shown, with what the answer writes read there -
    nothing, or on a numeral run the digits both engines read, which a 無
    keeps (adjudicate_segment) - the answer stands.  Otherwise - the context
    is not on the crop, or the transcription reads something else at the
    spot - the reading is kept (the engine's the
    transcription has there, else the fallback engine's where both engines
    read one) and the page gets a doubt
    (adjudicator-empty-unconfirmed; coverageRead holds what was read).  Not
    for a reading the book's furniture explains (furnitureSides: its running
    head, which 裁決 rightly says is not printed there, may go on any crop),
    nor for a crop of a whole block, table or page, or of both edges of a
    block boundary, which hold the spot by how they are cut.
    """
    piece, record, metrics = answer
    coverage = seg.get("coverage")
    if not coverage or record.get("resolved_from") != "adjudicator-empty":
        return answer
    furniture = set(seg.get("furnitureSides") or ())
    deleted = [side for side in ("a", "b")
               if CJK(seg[side]) and side not in furniture and CJK(seg[side]) != CJK(piece)]
    if not deleted:
        return answer
    transcript, used = client.ask_answering(BLIND_SYSTEM, BLIND_USER, crop, max_tokens, kind="coverage-read")
    metrics = dict(metrics)
    combined_usage = aggregate_usage([metrics, used])
    metrics = {**metrics, **combined_usage}
    shown, reading, how = crop_shows_spot(transcript or "", seg, coverage["beforeRoom"], coverage["afterRoom"])
    # What the answer writes, not nothing: a 無 on a numeral run keeps the
    # digits both engines read, and a crop that shows them there shows the
    # answer right (on the round-2 scan's pages, emulated: a numeral both
    # engines read, engine A with the circle mark after it as 〇, 無 answered
    # on a crop showing the numeral alone - taken as unconfirmed, it wrote the
    # same numeral with a doubt and a textLoss entry that changed nothing).
    # A transcription that writes the circle there as 〇 (the prompt says to
    # leave marks out) is left unconfirmed: a withheld name (張〇〇) and a
    # figure (五〇) print the same glyph, which the blind read cannot tell
    # from a mark.
    confirmed = shown and reading == variant_fold(CJK(piece))
    record = dict(record, coverageRead={"crop": coverage["kind"], "found": how, "reading": reading,
                                        "confirmed": confirmed, "transcript": (transcript or "").strip()[-600:]})
    if confirmed:
        return piece, record, metrics
    # The engine whose reading the blind read has at the spot, else the
    # fallback engine's where both engines read one.
    matched = [seg[side] for side in ("a", "b") if CJK(seg[side]) and variant_fold(CJK(seg[side])) == reading]
    kept = (matched[0] if matched else
            (seg["b"] if fallback_engine == "B" else seg["a"]) or seg["a"] or seg["b"])
    record.update(resolved=kept, resolved_from="adjudicator-empty-unconfirmed", winner="undecided", answered=piece)
    return kept, record, metrics


# What 裁決 is told about a crop that is the whole of what holds the spot (a
# horizontal block, a table, the page: segment_crop_kind), by its kind.  Such a
# crop shows the spot by how it is cut, and 睇唔到 there leaves an engine's
# reading unjudged.  Measured (2026-11-17, a small model on the benchmark's 28
# samples): 15 of 26 睇唔到 answers were on such crops, 9 of them on one
# book's tables, and of the 8 the reference settles the reading kept was right
# on 3 where the same model, with no such answer to give, had been right on 6.
WHOLE_CROP_NOTES = {
    "block": "呢張裁圖係爭議位所在嘅成段字，冇裁走任何部份，個位喺入面：照上文同下文喺段入面搵返佢先答。",
    "table": "呢張裁圖係爭議位所在嘅成個表格，冇裁走任何部份，個位喺入面：照上文同下文喺表入面搵返嗰一格先答。",
    "page": "呢張圖係成頁，爭議位喺入面：照上文同下文喺頁上搵返佢先答。",
}

# What 裁決 is told when it is asked a second time, on the whole block
# (widened_answer).
WIDER_CROP_NOTE = ("頭先嗰張裁圖影唔到爭議嗰個位。呢張係嗰個位所在成段字嘅裁圖，個位應該喺入面："
                   "先喺圖上搵返上文同下文，再答夾喺中間嗰個位。成段睇晒都搵唔到，先至寫 `定案：睇唔到`。")


def widened_answer(client: "Client", seg: Mapping[str, Any], answer: tuple[str, dict[str, Any], dict[str, Any]],
                   render: Path, block: Mapping[str, Any] | None, max_tokens: int, fallback_engine: str = "A",
                   simplified: bool = False
                   ) -> tuple[tuple[str, dict[str, Any], dict[str, Any]], bytes | None]:
    """裁決's second look at a spot it said its crop does not show (睇唔到,
    UNSEEN_VERDICTS): the question once more on the whole of BLOCK, the layout
    block the spot is in, and the crop it was shown (None when it was not
    asked again: another answer, or no block to show).

    BLOCK is given for a crop cut where the spot was estimated to be (a glyph
    or band crop).  The estimate comes from the columns' ink and the block's
    character count, and on some pages it is off by more than the crop
    reaches - type of two sizes, a form's blanks.  Measured (2026-11-17, a
    small model answering the first wave of nine benchmark samples, 66
    verdicts): 9 said 睇唔到, 8 of them on crops that were not at a column's
    end; of three looked at, one showed the column from two characters below
    the spot, one another part of the page.  The whole block holds the spot by
    how it is cut, as a horizontal block's crop does (segment_crop_kind), so
    a 無 there is not confirmed by a blind read (confirm_nothing).  The second
    answer is the answer - 睇唔到 again is no verdict, and the fallback
    engine's reading stands - and its record holds the first under "widened".
    """
    piece, record, metrics = answer
    if not record.get("unseen") or block is None:
        return answer, None
    wider = upscale_crop(crop_block(render, block["box"], pad=0.01))
    if not wider:
        return answer, None
    second, again, used = facade_attr("adjudicate_segment", adjudicate_segment)(client, wider, seg, max_tokens, fallback_engine=fallback_engine,
                                             note=WIDER_CROP_NOTE, simplified=simplified)
    again = dict(again, widened={"shown": "block", "firstVerdict": str(record.get("verdict") or "")[-300:]})
    return (second, again, {**used, **aggregate_usage([metrics, used])}), wider


# How many of the characters around a spot, in a row, a transcription must
# have on each side for the spot to be on the crop (crop_shows_spot).
SPOT_ANCHOR = 2


def crop_shows_spot(transcript: str, seg: Mapping[str, Any], before_room: int = 8,
                    after_room: int = 8) -> tuple[bool, str, str]:
    """Whether a transcription of a crop shows a disagreement's spot, what it
    reads there, and how it was found: (shown, reading, how).

    Shown means the characters engine A read around the spot are in the
    transcription on each side: BEFORE_ROOM is how many of its context
    characters before the spot are printed in the spot's own layout block,
    AFTER_ROOM the same after it.  A side with room is anchored by SPOT_ANCHOR
    of those characters (fewer where the room is less) in a row, in a row in
    the transcription too (variant-folded): one common character (之, 國) is
    found on any crop, two in a row were not on the crops measured.  A side
    with no room - the spot begins or ends its block - is anchored by
    SPOT_ANCHOR in a row of the neighbouring block's text there, when the
    crop shows it, and is not shown otherwise: a transcription that begins
    with the characters after the spot is the same whether the crop shows
    the spot empty or begins a character below it (the round-3 review; the
    measured miss was of this kind, mid-block), and the spot at the page's
    first or last character has no neighbour to show.  The context
    characters between the nearest anchor and the spot count as read there,
    misread or not.  How: "placed", "before-unshown", "after-unshown",
    "unanchored" or "misplaced" (anchored on both sides, the readings do not
    fit between).

    Measured on the round-2 runs: 裁決 answered 無 for a character one engine
    read on crops that did not show it - one began a character below the
    spot, at the head of the next column (the column fit put the spot one
    character late); the other was a column two columns away, where the
    block held a fold strip and a column engine A never read - and a
    character engine A had read right was deleted, in both runs of both
    pages.
    """
    t = variant_fold(CJK(transcript))
    before, after = variant_fold(CJK(seg.get("before") or "")), variant_fold(CJK(seg.get("after") or ""))
    # A side with room: its own block's characters; with none, the
    # neighbouring block's, SPOT_ANCHOR of them in a row.
    if before_room > 0:
        before = before[len(before) - min(len(before), before_room):]
    if after_room > 0:
        after = after[:after_room]
    need_before = min(SPOT_ANCHOR, len(before)) if before_room > 0 else SPOT_ANCHOR
    need_after = min(SPOT_ANCHOR, len(after)) if after_room > 0 else SPOT_ANCHOR
    readings = [variant_fold(CJK(seg["a"])), variant_fold(CJK(seg["b"]))]
    best: tuple[bool, int, str, str] | None = None
    for middle in dict.fromkeys(readings):
        ref = before + middle + after
        lo, hi = len(before), len(before) + len(middle)
        start = end = None
        anchored = 0
        for m in difflib.SequenceMatcher(None, ref, t, autojunk=False).get_matching_blocks():
            if not m.size:
                continue
            # The part of a matching run inside each side's context.
            b_lo, b_hi = m.a, min(m.a + m.size, lo)
            if b_hi > b_lo and b_hi - b_lo >= need_before:
                start = m.b + (b_hi - m.a) + (lo - b_hi)
                anchored += b_hi - b_lo
            a_lo, a_hi = max(m.a, hi), m.a + m.size
            if end is None and a_hi > a_lo and a_hi - a_lo >= need_after:
                end = m.b + (a_lo - m.a) - (a_lo - hi)
                anchored += a_hi - a_lo
        left, right = start is not None, end is not None
        if not (left and right):
            how = ("unanchored" if not (left or right) else "before-unshown" if not left else "after-unshown")
            found = (False, anchored, "", how)
        else:
            span = max(map(len, readings))
            fits = 0 <= start <= end <= len(t) and end - start <= span + 3
            found = (fits, anchored, t[start:end] if fits else "", "placed" if fits else "misplaced")
        if best is None or found[:2] > best[:2]:
            best = found
    assert best is not None
    return best[0], best[2], best[3]


def judged_misreads(asked: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, tuple[str, int]]:
    """Engine A's characters the page's verdicts judged misread: for each
    (segment asked, sealed verdict) of the page, the disputed characters of a
    like-for-like substitution (both engines read as many characters, the
    verdict as many) that 裁決 replaced; returned as
    {engine A's character: (the character written instead, sites)} for a
    character replaced at every disputed site of the page, always by the same
    one.  A numeral is no glyph question (a stroke or a bracket read as 一),
    and a verdict that kept engine A's character anywhere on the page says it
    is read right there."""
    replaced: dict[str, set[str]] = {}
    count: Counter = Counter()
    kept: Counter = Counter()
    for seg, record in asked:
        a, b, got = CJK(seg["a"]), CJK(seg["b"]), CJK(str(record.get("resolved") or ""))
        if (not a or len(a) != len(b) or len(got) != len(a)
                or record.get("resolved_from") != "adjudicator"):
            continue
        for x, y, z in zip(a, b, got):
            if x == y or x in NUMERAL_CHARS:
                continue
            if z == x:
                kept[x] += 1
            else:
                replaced.setdefault(x, set()).add(z)
                count[x] += 1
    return {x: (next(iter(zs)), count[x]) for x, zs in replaced.items() if len(zs) == 1 and not kept[x]}


def propagation_spots(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str], union_of: Collection[int],
                      asked_slots: Collection[int], misread: Mapping[str, tuple[str, int]]
                      ) -> list[tuple[int, int, dict[str, Any]]]:
    """The characters decision D24 asks about: (segment, position,
    the question) for each character of a reading only engine A made, written
    as engine A read it, that judged_misreads found misread on the page.

    A reading already put to 裁決 (ASKED_SLOTS: an insertion question, or an
    answer the deletion guard or D3's partial rule refused, which keeps the
    engine's reading) is left alone: 裁決 has answered for that text, and a
    character written into it would make a mix no verdict wrote, while the
    seal says the engine's reading was kept."""
    spots: list[tuple[int, int, dict[str, Any]]] = []
    for i, x in enumerate(segments):
        only_a = (x["a"] and not x["b"] and pieces[i] == x["a"] and not x.get(LOOPED_READING)
                  and i not in asked_slots
                  and ((x["tag"] == "delete" and "settled" not in x and i not in union_of)
                       or (x["tag"] == "equal" and "window" in x)))
        if not only_a:
            continue
        for p, char in enumerate(x["a"]):
            if char in misread:
                sub = {"tag": "replace", "a": char, "b": misread[char][0],
                       "before": (x["before"] + x["a"][:p])[-8:], "after": (x["a"][p + 1:] + x["after"])[:8],
                       **({"window": x["window"]} if "window" in x else {})}
                spots.append((i, p, sub))
    return spots


def propagation_note(x: str, z: str) -> str:
    """What 裁決 is told when asked about a character only engine A read
    because the page's verdicts judged engine A's same character a misread
    (decision D24): the second reading is not another engine's."""
    return (f"呢個位淨係一個引擎讀到。乙唔係第二個引擎嘅讀法：同一頁其他位置，對住裁圖判定過呢個引擎嘅「{x}」"
            f"係「{z}」讀錯。呢度係咪一樣，照圖定。")


def locate_block(blocks: list[dict[str, Any]], seg: dict[str, Any]) -> dict[str, Any] | None:
    """Find the layout block a disagreement sits in, by the text around it.

    Side A alone cannot locate an insertion (A is empty there), so the search
    uses the six characters before and after as well.
    """
    # After-context first: a list marker or heading number opens its block, so
    # the text before it belongs to the previous column's block (measured: three
    # of four markers cropped from the wrong block when before-context led).
    probes = [seg["after"][:6], seg["a"], seg["b"], seg["before"][-6:]]
    for probe in probes:
        key = norm_for_diff(probe)[:6] if probe else ""
        if len(key) < 2:
            continue
        for block in blocks:
            if key in norm_for_diff(str(block["text"])):
                return block
    return None


def place_by_alignment(a_text: str, piece: str, a_start: int,
                       ends: Sequence[int]) -> list[tuple[int, str]] | None:
    """PIECE split into (block index, text) runs by aligning it to engine A's
    A_TEXT, which starts at A_START in A's stream; ENDS are the blocks' ends.

    A character of the piece that matches one of A's, or replaces one, goes to
    the block A read that character in (a replacement of a different length is
    spread over the characters it replaces); a character with nothing of A's
    under it goes to the block of the A character that follows it, as
    block_spans places an insertion at a boundary, or the last one at the end.
    An empty piece (the disagreement resolved to nothing) places nothing; None
    when the piece shares no character with A.
    """
    if not piece:
        return []
    def block_of(position: int) -> int:
        return next((i for i, end in enumerate(ends) if position < end), len(ends) - 1)
    matcher = difflib.SequenceMatcher(None, a_text, piece, autojunk=False)
    if not any(n for _, _, n in matcher.get_matching_blocks()):
        return None
    owner: list[int] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            continue
        if tag == "insert":
            at = i1 if i1 < len(a_text) else len(a_text) - 1
            owner += [block_of(a_start + at)] * (j2 - j1)
            continue
        span = i2 - i1
        owner += [block_of(a_start + i1 + (t * span) // (j2 - j1)) for t in range(j2 - j1)]
    runs: list[list] = []
    for char, block in zip(piece, owner):
        if runs and runs[-1][0] == block:
            runs[-1][1] += char
        else:
            runs.append([block, char])
    return [(block, text) for block, text in runs]


def block_spans(blocks: Sequence[Mapping[str, Any]], segments: Sequence[Mapping[str, Any]],
                pieces: Sequence[str], draft: str) -> list[str] | None:
    """Split the merged text into the engine-A layout blocks it came from.

    The merge walks engine A's character stream; block boundaries are known
    in that stream, so every piece of the merged text can be handed to the
    block it belongs to.  Insertions (engine B only) at a boundary go to the
    following block, where a list marker's stroke debris belongs.  Returns
    None when the blocks do not account for the whole draft, and the page
    falls back to whole-page formatting.
    """
    lengths = [len(CJK(b["text"])) for b in blocks]
    if not blocks or sum(lengths) != len(CJK(draft)):
        return None
    ends = list(itertools.accumulate(lengths))
    spans = [[] for _ in blocks]
    a_cursor, k = 0, 0
    for seg, piece in zip(segments, pieces):
        a_len = len(seg["a"])
        if seg.get(LOOPED_READING):
            # Engine B's reading of a block whose own reading was a loop
            # (quarantine_looped_block) belongs to that block, wherever it sits.
            quarantined = [i for i, block in enumerate(blocks) if block.get("quarantined")]
            if len(quarantined) != 1:
                return None
            spans[quarantined[0]].append(piece)
            a_cursor += a_len
            continue
        if "window" in seg:
            # A segment of an order-conflict table block read from engine B's
            # lines (window_merge) belongs to that block.  Its segments hold
            # each of the block's characters once, but in engine B's order, so
            # text only engine B read at the end of its last window stands
            # where engine A's stream has already passed the block, and would
            # otherwise go to the next block.
            spans[seg["window"]].append(piece)
            a_cursor += a_len
            continue
        if a_len and piece == seg["a"]:
            # Engine A's own text (agreed, or chosen where the engines
            # disagree) may straddle blocks: split at the boundaries it
            # crosses.  A disagreement is not assigned whole to the block it
            # starts in: when the other engine reads a multi-leaf page in a
            # different order, one disagreement can run across several blocks,
            # and a running-head block was measured absorbing 115 characters of
            # body text that were then dropped with it.
            offset = 0
            while offset < a_len:
                while k < len(blocks) and a_cursor + offset >= ends[k]:
                    k += 1
                idx = min(k, len(blocks) - 1)
                take = min(a_len - offset, ends[idx] - (a_cursor + offset)) if k < len(blocks) else a_len - offset
                spans[idx].append(piece[offset:offset + take])
                offset += max(take, 1)
        else:
            while k < len(blocks) and a_cursor >= ends[k] and (a_len or k < len(blocks) - 1):
                k += 1
            first = min(k, len(blocks) - 1)
            last = next((i for i, end in enumerate(ends) if a_cursor + a_len <= end), len(blocks) - 1)
            if a_len and last > first:
                # A resolved disagreement over several of engine A's blocks.
                # Where the resolution keeps engine A's characters (裁決 kept a
                # running head and dropped the folio A read after it), each
                # kept character goes back to the block A read it in.  Text
                # sharing nothing with A cannot be placed block by block, so the
                # page is punctuated whole rather than risk text landing in the
                # wrong block (a furniture block, a table).  Measured: one such
                # head-and-folio 裁決 sent a page to whole-page punctuation, which
                # invented ，：「」 on a book printed with 。 only.
                placed = place_by_alignment(seg["a"], piece, a_cursor, ends)
                if placed is None:
                    return None
                for idx, text in placed:
                    spans[idx].append(text)
                a_cursor += a_len
                continue
            spans[first].append(piece)
        a_cursor += a_len
    joined = ["".join(x) for x in spans]
    if "".join(joined) != "".join(pieces):
        return None
    return joined


def align_drafts(a: str, b: str, folded: bool | None = None, prefer: str | None = None) -> list[dict[str, Any]]:
    """Character-level alignment of two independent OCR readings.

    Agreement between two engines from different vendors is the strongest
    character evidence available - measured 11/11 on this project's adjudicated
    items, against 8/11 and 10/11 for the engines alone and 4-7/11 for a model
    re-transcribing the page. So agreement is frozen and only differences are
    put to a model.

    The readings are matched on variant-folded text (variant_fold; FOLDED
    False matches them as written, as the book profile does), so two forms of one character
    never move an anchor: measured, engine B wrote a heading's second
    character as 决 where engine A wrote 決, difflib anchored the heading's
    first character on an earlier copy of it instead, and a line only engine
    B read (engine A skipped it) came out fused with that one character as a
    replacement of 1 against 18, which the preference then settled with
    engine A's single character - 17 printed characters lost with no doubt
    (split_lopsided is the other half of the fix).  The characters in the
    segments stay the engines' own: where the folded match pairs two
    different forms, those positions are a replacement of their own (a
    variant question for variant_segment), the rest of the match is
    agreement.

    Not, on a page the engine preference PREFER ("A" or "B") settles, where
    the fold takes apart a lopsided replacement - one reading more than
    INSERTION_LIMIT characters longer than the other - whose longer reading
    is the other engine's, into text only that engine read (more than
    INSERTION_LIMIT characters) which is no line the preferred engine
    skipped: the preferred engine read it elsewhere on the page, or it loops
    (read_elsewhere, the test split_lopsided_segments applies).  That
    stretch keeps the replacement the characters as written make, and the
    preference settles it as before.  Only there: where the fold matches
    such a stretch with the preferred engine's reading instead, the
    alignment as written had anchored on another copy of a sentence, and
    the fold is what puts it right - measured on the corpus, a page whose
    engines read in order 0.88 folded and 0.53 as written (a sentence
    written with 真 and its later copy with 眞); kept as written wherever
    the fold took a lopsided replacement apart, a made-up page like it
    (check_text_loss), preferring engine A, read as an order conflict
    again.  Measured: engine A read a line of signatures twice, its
    second copy fused with a variant form beside it (暨 against engine B's
    曁); aligned folded, the copy became text only engine A read, which a
    preference for engine B keeps (the preference falls back to the side
    that has text), and 11 characters were written twice with no doubt -
    the copy all held by engine B's reading (moved_share 1.00).  Where the
    longer reading is the preferred engine's, the preference writes it
    either way; on a page 裁決 reads every disagreement off its crop, it
    sees both questions the fold makes (D16 keeps a longer reading the
    answer leaves out, unless the merged text holds it nearby).
    """
    ca, cb = CJK(a), CJK(b)
    fold = True if folded is None else folded
    fa, fb = (variant_fold(ca), variant_fold(cb)) if fold else (ca, cb)
    out = []

    def emit(tag: str, i1: int, i2: int, j1: int, j2: int) -> None:
        out.append({"tag": tag, "a": ca[i1:i2], "b": cb[j1:j2],
                    "before": ca[max(0, i1 - 8):i1], "after": ca[i2:i2 + 8]})

    def opcodes(x: str, y: str, i0: int = 0, j0: int = 0) -> list[tuple[str, int, int, int, int]]:
        return [(tag, i1 + i0, i2 + i0, j1 + j0, j2 + j0) for tag, i1, i2, j1, j2
                in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes()]

    def lopsided(op: tuple[str, int, int, int, int]) -> bool:
        tag, i1, i2, j1, j2 = op
        return tag == "replace" and i2 > i1 and j2 > j1 and abs((i2 - i1) - (j2 - j1)) > INSERTION_LIMIT

    ops = opcodes(fa, fb)
    if prefer in ("A", "B") and (fa != ca or fb != cb):
        # Text only the other engine read, as the fold aligns it, that it read
        # elsewhere (or that loops): where it lies inside a lopsided
        # replacement of the characters as written that the fold takes apart,
        # with that engine's reading the longer, the replacement stands.
        still = {(ca[i1:i2], cb[j1:j2]) for _, i1, i2, j1, j2 in filter(lopsided, ops)}
        other_side = "b" if prefer == "A" else "a"
        mine, theirs = (cb, ca) if other_side == "b" else (ca, cb)
        page_looped = looped_reading(mine)
        alone = [(i1, i2) if other_side == "a" else (j1, j2) for tag, i1, i2, j1, j2 in ops
                 if tag == ("delete" if other_side == "a" else "insert")
                 and (i2 - i1 if other_side == "a" else j2 - j1) > INSERTION_LIMIT]
        alone = [(lo, hi) for lo, hi in alone if read_elsewhere(mine[lo:hi], theirs, page_looped)]
        keep = []
        for op in (filter(lopsided, opcodes(ca, cb)) if alone else ()):
            _, i1, i2, j1, j2 = op
            lo, hi = (i1, i2) if other_side == "a" else (j1, j2)
            if ((ca[i1:i2], cb[j1:j2]) not in still and (i2 - i1 > j2 - j1) == (other_side == "a")
                    and any(a_lo < hi and lo < a_hi for a_lo, a_hi in alone)):
                keep.append(op)
        if keep:
            # Folded between them, as written across them.
            hybrid, i0, j0 = [], 0, 0
            for op in keep:
                _, i1, i2, j1, j2 = op
                hybrid.extend(opcodes(fa[i0:i1], fb[j0:j1], i0, j0))
                hybrid.append(op)
                i0, j0 = i2, j2
            hybrid.extend(opcodes(fa[i0:], fb[j0:], i0, j0))
            ops = [op for op in hybrid if op[2] > op[1] or op[4] > op[3]]
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" or ca[i1:i2] == cb[j1:j2]:
            emit(tag, i1, i2, j1, j2)
            continue
        k = 0
        while k < i2 - i1:
            same = ca[i1 + k] == cb[j1 + k]
            end = k
            while end < i2 - i1 and (ca[i1 + end] == cb[j1 + end]) == same:
                end += 1
            emit("equal" if same else "replace", i1 + k, i1 + end, j1 + k, j1 + end)
            k = end
    return out


def restore_merged_characters(resolved: str, formatted: str) -> str | None:
    """Keep the formatter's STRUCTURE but the merge's CHARACTERS.

    The formatter's job is layout; the characters were already settled by two
    engines and must not drift.  But a model reversing a right-to-left cell
    naturally writes the variant it knows (内 becomes 內, 烟 becomes 煙), and
    one such slip used to cost a page its whole table.

    Only glyph-for-glyph substitutions are repaired, and each invented glyph is
    paired with a dropped one by EVIDENCE: the characters adjacent to it in the
    output must also be adjacent to the dropped glyph in the merge.  Adjacency
    is unordered because the formatter legitimately reverses cells and
    transposes tables.  An invented glyph whose neighbours match no dropped
    glyph, or more than one, is left alone and the page goes to the retry.
    """
    source, output = Counter(CJK(resolved)), Counter(CJK(formatted))
    surplus = output - source          # glyphs the formatter brought in
    deficit = source - output          # glyphs it dropped in exchange
    if not surplus or surplus == deficit or sum(surplus.values()) > 6:
        return None

    def neighbour_sets(text: str, glyph: str) -> list[set[str]]:
        seq = CJK(text)
        return [{seq[i - 1] if i > 0 else "", seq[i + 1] if i + 1 < len(seq) else ""} - {""}
                for i, c in enumerate(seq) if c == glyph]

    pairs: list[tuple[str, str]] = []
    for wrong in surplus:
        contexts = neighbour_sets(formatted, wrong)
        matches = set()
        for right in deficit:
            if right == wrong:
                continue
            right_contexts = neighbour_sets(resolved, right)
            if all(any(ctx & rc for rc in right_contexts) for ctx in contexts if ctx):
                matches.add(right)
        if len(matches) != 1:
            return None
        pairs.append((wrong, matches.pop()))
    repaired = formatted
    for wrong, right in pairs:
        repaired = repaired.replace(wrong, right)
    return repaired


def non_cjk_glyphs(text: str) -> Counter:
    """The letters and figures of TEXT that are not CJK, as a multiset:
    Latin, Greek, kana and the like, and figures folded by NFKC (a full-width,
    circled or superscript figure counts as its digits, so a formatter that
    writes ① as 1 loses nothing).  Markup tags, LaTeX command names and the
    layout markup visible_text drops are not text."""
    text = re.sub(r"</?[A-Za-z][^<>\n]*>", " ", re.sub(r"\\[A-Za-z]+", " ", text))
    text = unicodedata.normalize("NFKC", _content_fidelity.visible_text(text))
    return Counter(c for c in text if unicodedata.category(c)[0] in "LN" and not CJK(c))


def verify_characters(resolved: str, formatted: str, removable: str | None = None) -> tuple[bool, str]:
    """Formatting may rearrange text, but cannot invent or delete glyphs.

    REMOVABLE None (switch --content-fidelity, after a content-recovery
    review): image-confirmed furniture removal happened in content-recovery
    before this lock, so nothing may go.  Include Latin, numerals and formula
    signs; a CJK-only subsequence check used to accept both a missing digit
    and a completely empty output (no_answer refuses the empty one first).
    Position-sensitive source features are checked separately against their
    image-confirmed anchors and in the final full-page coverage review.

    REMOVABLE a string (no such review): the formatter removes the page's
    furniture itself, as the old skill's did.  A CJK character it invented is
    refused, and a CJK one it deleted is left to deleted_runs and
    furniture_unexplained, which know the furniture (the page checks them
    after this, so a long run keeps deleted_runs' reason); a letter or figure
    that is not CJK may go only as far as REMOVABLE holds it (the furniture
    blocks, the page's known heads, lone folio lines, the figures
    restore_arabic_figures writes back).  The old
    rule counted CJK alone, so '見Skinner所著，共1513人。' formatted as
    '見所著，共人。' passed every check and the name and the number were gone.
    """
    if removable is not None:
        invented = Counter(CJK(formatted)) - Counter(CJK(resolved))
        if invented:
            detail = "".join(char * count for char, count in invented.items())
            return False, (f"formatter introduced {sum(invented.values())} character(s) "
                           f"not in the merged text: {detail[:40]}")
        lost = non_cjk_glyphs(resolved) - non_cjk_glyphs(formatted) - non_cjk_glyphs(removable)
        if lost:
            detail = "".join(char * count for char, count in lost.items())
            return False, (f"formatter deleted {sum(lost.values())} letter(s) or figure(s) "
                           f"outside the page's furniture: {detail[:40]}")
        return True, "characters preserved"
    have, output = (_content_fidelity.character_counts(t) for t in (resolved, formatted))
    invented = output - have
    if invented:
        detail = "".join(char * count for char, count in invented.items())
        return False, (f"formatter introduced {sum(invented.values())} character(s) "
                       f"not in the merged text: {detail[:40]}")
    lost = have - output
    if lost:
        detail = "".join(char * count for char, count in lost.items())
        return False, f"formatter deleted {sum(lost.values())} retained character(s): {detail[:40]}"
    return True, "characters preserved"
