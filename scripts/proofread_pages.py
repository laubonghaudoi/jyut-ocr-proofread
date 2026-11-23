#!/usr/bin/env python3
"""Proofread one or two OCR drafts against complete page images.

The default packet transport writes resumable question bundles and exits 10
until the invoking agent has inspected the images and supplied answers. The
same agent can answer sequentially; parallel workers are optional. An HTTP
model is used with --backend http (--endpoint, else PROOFREAD_ENDPOINT; the
model --model, else PROOFREAD_MODEL, else the server's first listed model).
When the invoking agent is itself the local proofreading model, HTTP is the
default and packets are refused (agent_is_local_model).

Two OCR engines are the default: engine A (PaddleOCR-VL, with layout labels)
in DRAFT_DIR and engine B (HunyuanOCR) in --draft2-directory, else the folder
the book profile's sources name, else ocr-hunyuan beside DRAFT_DIR.  The
drafts' JSON must say so (engine_roles_problem): swapped folders are refused.
--single-engine reads DRAFT_DIR alone and transcribes each page from the
image with it as the hint.  A page missing engine B's draft is not proofread
(engine B's OCR may still be running, or was interrupted): it counts as failed
with a warning naming the pages, unless --allow-missing-engine-b, which
transcribes it from engine A's draft alone.  A page missing engine A's draft
is transcribed from engine B's.  Either one-engine page carries an
engine-draft-missing doubt (missing_engine_doubt).  A document of at most SHORT_DOC_MAX_PAGES
rendered pages needs no book profile (book_profile.py's short-document fast
path, short_document_engines); a longer one does.

A second OCR draft enables alignment and targeted disagreement checks;
agreement between engines never proves that either engine captured all
printed content.

Exit codes:
    0   every selected page is sealed (proofread now or current already)
    1   some pages failed or were blocked, and no packet answers are pending
    2   wrong arguments or inputs (no engine B drafts and no --single-engine,
        swapped engines, a profile for other drafts, ...): nothing was run
    10  packet answers are pending and nothing failed: answer the wave named
        on stdout, apply it, run the same command again
    11  packet answers are pending AND some pages failed or were blocked:
        answer the wave as for 10, and see the report for the failed pages

With --content-fidelity on (off by default) every page first receives a
whole-image content inventory, including apparently blank pages; content pages
then receive image-based recovery, guarded formatting, and a final coverage
check, and what those cannot settle is sealed as a doubt on the page. Numbers,
formulas, note markers, table structure, and uncertain readings remain part of
those checks rather than being discarded as formatting noise. A second pass by
the same agent is self-review, not an independent audit; an independent audit
requires a distinct reviewer.

Output is a sealed per-page JSON/Markdown candidate with source and decision
provenance for validated resume; the transport, model, endpoint and helper
hashes are recorded beside it but do not decide whether a page is current. A
completed candidate does not by itself establish independent visual approval
or a measured accuracy rate.
"""

from __future__ import annotations

import argparse
import base64
import bisect
from collections import Counter, OrderedDict
import concurrent.futures as cf
import contextlib
import contextvars
from dataclasses import dataclass
import difflib
import fcntl
import functools
import hashlib
import inspect
import io
import ipaddress
import itertools
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import threading
import time
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Sequence
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import canonical_json_bytes, sha256_bytes, sha256_file, utc_now  # noqa: E402
import _draft_contract
import _tables  # noqa: E402
import _loop_watch  # noqa: E402
import _content_fidelity  # noqa: E402
import _note_markers  # noqa: E402
import _packet_events  # noqa: E402
import _punctuation_fusion  # noqa: E402
from _packet_client import (  # noqa: E402
    DEFAULT_MAX_ITEMS_PER_BUNDLE,
    EXIT_PACKETS_PENDING,
    EXIT_PACKETS_PENDING_WITH_FAILURES,
    PACKET_USAGE_SOURCES,
    PacketClient,
    PacketPending,
    packet_page_context,
)
from _proofread_core import (  # noqa: E402
    ADJUDICATE_SYSTEM, ANSWER_DELETION_LIMIT, ANSWER_LINE, AUDITOR_SYSTEM, BLIND_SYSTEM, BLIND_USER,
    BRACKET_BESIDE, BRACKET_MISREADS, BRACKET_SLIP, BULLET_MARKS, BULLET_SHAPES, CENSUS_SYSTEM,
    CHINESE_DIGITS, CIRCLE_CHARS, CJK, CJK_EXTRA, CLOSE_BRACKETS, DEFAULT_EFFORT, DEFAULT_ENDPOINT,
    DEFAULT_MODEL, DEFAULT_PAGE_CONCURRENCY, DEFAULT_WORKERS, DEGENERATE_NGRAM_RATIO,
    DELETED_RUN_LIMIT, DROP_GRAM, EMPTY_VERDICTS, ENGINE_A_NAME, ENGINE_B_FOLDER, ENGINE_B_LABELS,
    ENGINE_B_NAME, FALLBACK_MARKS, FOLIO_MAX_FIGURES, FORMAT_SYSTEM, FORMAT_SYSTEM_REVIEWED,
    FRONT_MATTER_LABELS, FRONT_MATTER_LIMIT, FURNITURE_LABELS, GROUNDING_LINE, HEAD_SIZE_RATIO,
    INK_MARGIN, INSERTION_LIMIT, LARGE_IMAGE_PIXEL_THRESHOLD, LEADER, LEADER_MARKS, LEADER_READ,
    LOCAL_AGENT_ENV, LONE_FOLIO, LOOPED_READING, MEASURE_WIDTH, MEDIA_PIXEL_LIMIT,
    MISSING_ENGINE_DOUBT, MULTIMODAL_PLACEHOLDER_RE, NEARBY_WINDOW, NO_ANSWER_RETRY_EFFORT,
    NUMERAL_CHARS, NUMERAL_CONFLICT_LIMIT, OPENING_MARKS, OPEN_BRACKETS, OTHER_BRACKETS,
    PADDLEX_IMG_LINE_RE, PADDLEX_IMG_TAG_RE, PUNCT, PUNCT_MARKS, PUNCT_SYSTEM, PageJob,
    ProofreadError, QWEN_CODE_MODEL_ENV, RARE_CODEPOINT, RESEARCH_LINE, ROLE_SAMPLE_PAGES,
    RULE_COVERAGE, SCHEMA_VERSION, SENTENCE_MARKS, SHORT_DOC_MAX_PAGES, SITE_PLACE_KEYS,
    STRUCTURE_SYSTEM, STRUCTURE_SYSTEM_REVIEWED, TEXT_AUTHORITY, TEXT_SEAMS, UNCORROBORATED_MINIMUM,
    UNSEEN_VERDICTS, WHOLE_PAGE_FURNITURE_LABELS, WRITER_SYSTEM, ZERO_FORMS, aggregate_usage,
    call_site, distinct_ngram_ratio, engine_gaps, facade_attr, front_matter_research, gaps_of,
    in_context, is_lone_circle, is_numeral_conflict, is_rare_codepoint_conflict, layout_index,
    load_blocks, neutralize_multimodal_placeholders, norm_for_diff, profile_section,
    punctuation_counts, punctuation_mode, set_facade, strip_fence, strip_grounding,
    strip_paddlex_img_tags, summarize_calls, table_spans, verdict_answer, _CALL_LEDGER, _CALL_SITE,
    _LARGE_IMAGE_SEM, _LOOP_CUTS, _PADDLEX_IMG_TAG_PATTERN, _large_image_guard, _reviewed_prompt,
)
from _proofread_client import (  # noqa: E402
    Client, EFFORT_KINDS, EFFORT_POLICY_ENV, EFFORT_RUNGS, MEDIA_ASPECT_LIMIT, agent_is_local_model,
    ask_lower, effort_policy_record, effort_policy_text, endpoint_models_url, fit_media_aspect,
    fit_media_budget, loop_cut_changes, loopback_endpoint, no_answer, no_answer_cut,
    parse_effort_policy, resolve_http_model, _MEDIA_BUDGET_CACHE, _MEDIA_BUDGET_CACHE_LOCK,
    _MEDIA_BUDGET_CACHE_MAX,
)
from _proofread_crops import (  # noqa: E402
    BOUNDARY_NOTES, COLUMN_BREAK_NOTE, LEADER_DOT_MINIMUM, LEADER_DOT_SIZE, LEADER_MINIMUM_DOTS,
    LEADER_STEP, TILE_CHARACTERS, block_is_vertical, block_tiles, boundary_crop, column_layout,
    column_reach, crop_band, crop_block, crop_glyph, edge_region, image_size, ink_columns, ink_of,
    leader_count, leader_dot_runs, measure_columns, own_rows_extent, page_ink_fraction,
    text_columns, upscale_crop,
)
from _proofread_variants import (  # noqa: E402
    ALLOGRAPHS, TS_CHARACTERS, VARIANT_LIST, classify_pair, converted_against_b, opencc_allographs,
    resolve_variant, simplified_as_read, simplified_form, simplified_script, traditional_forms,
    user_variant_rulings, variant_fold, variant_model_sites, variant_person_unruled,
    variant_ruling_by, variant_rulings_report, variant_rulings_seal, variant_segment,
    variant_source, _ALLOGRAPH_PAIRS, _FOLD, _LIST_SHA256, _TRADITIONAL_FORMS, _VARIANT_RULINGS,
)
from _proofread_adjudicate import (  # noqa: E402
    BESIDE_COPY_MIN, MOVED_SHARE, NEIGHBOUR_WINDOW, SPOT_ANCHOR, WHOLE_CROP_NOTES, WIDER_CROP_NOTE,
    adjudicate_prompt, adjudicate_segment, align_drafts, block_spans, confirm_nothing,
    crop_shows_spot, fuse_numeral_runs, gram_counts, judged_misreads, left_out_of, locate_block,
    locate_segment, looped_reading, moved_share, neighbour_copy, non_cjk_glyphs, place_by_alignment,
    propagation_note, propagation_spots, read_elsewhere, read_twice, reading_nearby,
    restore_merged_characters, segment_crop, segment_crop_kind, segment_winner, split_edge_circle,
    split_lopsided, split_lopsided_segments, verify_characters, widened_answer,
)
from _proofread_furniture import (  # noqa: E402
    BLANK_INK_FRACTION, DEGENERATE_PUNCTUATION_DENSITY, FOLIO_CHARS, FURNITURE_EDGE, FURNITURE_GRAM,
    FURNITURE_MINIMUM_PAGES, FURNITURE_PAGE_SHARE, HEAD_THICKNESS_RATIO,
    LEARNED_FOLIO_MINIMUM_PAGES, LEARNED_LABEL_MINIMUM_CHARACTERS, LEARNED_LABEL_MINIMUM_PAGES,
    LEARNED_REGION_GAP, LEARNED_REGION_MINIMUM_PAGES, LOOP_MARKUP, LOOP_MIN_CHARS, LOOP_MIN_COPIES,
    LOOP_PATTERN, LOOP_PERIOD_MAX, MARGIN_FURNITURE_AREA, MARGIN_FURNITURE_LIMIT, NUMERAL_DIGITS,
    NUMERAL_RUN, NUMERAL_TENS, NUMERAL_UNITS, NUMERAL_VALUES, ORDER_CONFLICT_FLOOR,
    PAGE_FURNITURE_LABELS, PLACED_BAND_PAGE_SHARE, PLACED_HEAD_CHARACTERS, PLACED_HEAD_LABEL,
    READING_ORDER_AGREEMENT, UNCORROBORATED_WITNESS, at_head_size, cut_engine_b_loops,
    engine_b_cut_off, expected_folios, find_loops, folio_figures, folio_runs, furniture_band,
    furniture_grams, furniture_leftover, furniture_text, head_band_blocks, head_size_fits,
    head_size_matches, in_placed_band, is_furniture_text, is_running_head, label_skeleton,
    learn_heads, learned_band, learned_head_block, learned_heads_seal, learned_leftover,
    learned_record, loop_holds_text, loop_unit_from_tag, looped_blocks, margin_blocks,
    margin_furniture, needs_placed_stem, non_cjk_text, numeral_forms, numeral_value,
    order_judgement, placed_heads, quarantine_looped_block, readings_dropped, removable_text,
    short_thin, split_folio, stem_windows, top_line, unsupported_reading, _is_furniture,
)
from _proofread_punctuation import (  # noqa: E402
    AGREED_MARKS_MINIMUM, ANY_FORM_PAGE_PLACES, CENSUS_LINE, CIRCLED_NUMERALS, DOUBLED_LINE,
    FORM_FOLD, GAP_MARKUP, KEPT_PRINT, LIST_LEADER_LINES, MARK_FORMS, MOVE_MINIMUM,
    NOTE_MARKER_PRINT, OPENING_QUOTES, SENTENCE_PUNCT, SHAPE_LINE, UNMARKED_PAGE_MARKS,
    UNMARKED_SAMPLE, agreed_marks, agreed_punctuation, agreed_readings, anchored_marks,
    assemble_passes, break_lines, bullet_contradicted, bullet_runs, census_ceiling, census_hint,
    census_tolerance, deleted_runs, density_limit, doubled_shown, engine_b_lines_merged,
    engine_b_marks_merged, engine_b_places, engine_marks_merged, engine_places, engine_reading,
    extra_copies, extra_copy_doubts, furniture_unexplained, gap_as_written, gap_marks, gap_print,
    gap_sides, leader_list_breaks, leader_rewrites, leader_runs, leaders_written, mark_changes,
    mark_places, marked_stretch, marks_as_written, notation_hint, other_print, parse_census,
    place_moved_once, punctuate_part, punctuation_delta, punctuation_density, punctuation_doubts,
    punctuation_pass_ok, realign_answer, seam_sides, seat_marks, tied_marks, unmarked_candidates,
    whole_page_reason,
)
from _proofread_markers import (  # noqa: E402
    ARABIC_FIGURE_DIGITS, ARABIC_FIGURE_GAP, CIRCLED_RUN, FIGURE_AT_GAP, FIGURE_BRACKETS,
    FIGURE_IN_BLOCK, FIGURE_WRITTEN, GAP_PAIR, MARK_ALIAS_MINIMUM, MARK_SEARCH_LIMIT,
    NOTE_BLOCK_LABELS, NOTE_LINE_ANCHOR, NOTE_MARK, NOTE_MARKER_REACH, NOTE_NUMBERS_LIMIT,
    NOTE_NUMBERS_MAX_TOKENS, NOTE_NUMBERS_SYSTEM, NOTE_NUMBERS_TEXT, NOTE_NUMBERS_USER,
    PRINTED_MARKS, STRUCTURE_INPUT_SEALED, agreed_note_markers, arabic_figure_kept, arabic_figures,
    ask_note_numbers, bracket_explanation, bracket_kinds, bracketed_pairs, bracketed_unwritten,
    brackets_by_block, circle_withheld, engine_brackets_merged, engine_marks, engine_note_markers,
    explain_by_brackets, explain_by_marks, figure_aliases, figure_evidence, figure_evidence_seal,
    figure_in_gap, figure_neighbours, figure_stand_ins, figure_verdict, figure_written,
    figures_confirmed, mark_aliases, mark_evidence, mark_explanation, marker_cut, note_block_texts,
    note_labels, note_markers_by_block, note_question_reason, parse_note_numbers,
    place_arabic_figures, place_brackets, place_note_markers, restore_arabic_figures,
    restore_note_markers, structure_input_record, unpaired_brackets, _MARK_CLASS,
)
set_facade(sys.modules[__name__])


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
        markers = CELL_NOTE_MARKER.findall(restated)
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
        return segment_crop(render, render_bytes, block, position, seg)
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001 - the ordinary crop stands instead
        return segment_crop(render, render_bytes, block, position, seg)
    x, y, w, h = block["box"]
    pitch = ((w * width) * (h * height) / max(count, 1)) ** 0.5
    grow_x, grow_y = UNREAD_CROP_REACH * pitch / width, UNREAD_CROP_REACH * pitch / height
    return (upscale_crop(crop_block(render, [x - grow_x, y - grow_y, w + 2 * grow_x, h + 2 * grow_y], pad=0.0))
            or segment_crop(render, render_bytes, block, position, seg))


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


def sealed_inputs(job: PageJob) -> dict[str, str]:
    """The input hashes a page's seal carries: its render and both engines'
    drafts."""
    return {"renderSha256": sha256_file(job.render), "draftSha256": sha256_file(job.draft_text),
            DRAFT2_KEY: sha256_file(job.draft2_text) if job.draft2_text is not None else "",
            "draftSemanticsSha256": _draft_contract.semantic_digest(job.draft_json),
            "draft2SemanticsSha256": _draft_contract.semantic_digest(
                job.draft2_text.with_suffix(".json") if job.draft2_text else None)}


def validate_completion(job: PageJob, expect: Mapping[str, str]) -> tuple[bool, str]:
    if not job.out_json.is_file() or not job.out_md.is_file():
        return False, "no existing output"
    try:
        raw = job.out_json.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return False, f"unreadable: {error}"
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return False, "legacy schema"
    if raw != canonical_json_bytes(record):
        return False, "not canonical or edited"
    provenance = record.get("provenance") or {}
    # Rule keys the seal was made before (LEGACY_ABSENT_KEYS): taken as sealed.
    legacy: list[str] = []
    for key, value in expect.items():
        # The switches' keys (last in EXPECT) are compared below, both ways;
        # the informational keys not at all; engine B's draft and the
        # fidelity switch below.
        if (key in OPTIONAL_PROVENANCE or key in INFORMATIONAL_PROVENANCE
                or key in (DRAFT2_KEY, CONTENT_FIDELITY_KEY, "punctuationMode")):
            continue
        if key not in provenance and key in _draft_contract.SEMANTIC_KEYS:
            source = (job.draft_json if key == "draftSemanticsSha256" else
                      job.draft2_text.with_suffix(".json") if job.draft2_text else None)
            if not _draft_contract.requires_semantics(_draft_contract.read_record(source)):
                legacy.append(key)
                continue
        if key not in provenance and key in LEGACY_ABSENT_KEYS:
            legacy.append(key)
            continue
        if provenance.get(key, PROVENANCE_ABSENT_MEANS.get(key)) != value:
            return False, f"provenance {key} differs"
    # Before mode was explicit, packet always fused and HTTP always split.
    # An old seal with no transport evidence may resume only under auto;
    # explicit experiments must never silently reuse an unknown mode.
    if "punctuationMode" in expect:
        sealed_mode = provenance.get("punctuationMode")
        if sealed_mode is None:
            transport = provenance.get("proofreadTransport")
            sealed_mode = {"packet": "fused", "http": "split"}.get(transport)
        if sealed_mode is None:
            if expect.get("punctuationModeRequested", "auto") != "auto":
                return False, "provenance punctuationMode is unknown in legacy seal"
        elif sealed_mode != expect["punctuationMode"]:
            return False, "provenance punctuationMode differs"
    # Engine B's draft: a page sealed with another (or none, where there now
    # is one) was merged from other readings.  A seal without the key was
    # made before it was recorded and is taken as it is.
    if DRAFT2_KEY in provenance and provenance[DRAFT2_KEY] != expect.get(DRAFT2_KEY, ""):
        return False, ("engine B's draft differs from the one the page was sealed with"
                       if provenance[DRAFT2_KEY] else "engine B's draft was added after the page was sealed")
    # A run with --content-fidelity on asks for its evidence, not for the
    # key: a page sealed by a skill that always ran the v2 questions carries
    # the current contract with no switch key, and validate_completed_record
    # below decides whether that evidence holds.
    if (expect.get(CONTENT_FIDELITY_KEY) and provenance.get(CONTENT_FIDELITY_KEY) != expect[CONTENT_FIDELITY_KEY]
            and record.get("contentFidelityContract") != _content_fidelity.CONTRACT_VERSION):
        return False, (f"provenance {CONTENT_FIDELITY_KEY} differs: sealed without --content-fidelity on, "
                       "which this run asks for")
    for key in OPTIONAL_PROVENANCE:
        # Absent means off: a page sealed with the switch on is not current
        # for a run with it off.
        if key not in ROUND4_DEFAULTS.values() and provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    # The round-4 fixes: a page sealed with one off and asked on, or the other
    # way round (a book begun before they were on by default), is told which
    # switches resume it as it was sealed.
    turned = [switch for switch, key in ROUND4_DEFAULTS.items() if (key in provenance) != (key in expect)]
    if turned:
        sealed_off = [switch for switch in turned if ROUND4_DEFAULTS[switch] not in provenance]
        sealed_on = [switch for switch in turned if switch not in sealed_off]
        return False, ("provenance " + ", ".join(ROUND4_DEFAULTS[switch] for switch in turned)
                       + (" differs: " if len(turned) == 1 else " differ: ")
                       + "; ".join(part for part in (
                           f"sealed with {', '.join(sealed_off)} off, this run has "
                           f"{'it' if len(sealed_off) == 1 else 'them'} on (resume with "
                           + " ".join("--no-" + switch[2:] for switch in sealed_off) + ")" if sealed_off else "",
                           f"sealed with {', '.join(sealed_on)} on, this run has "
                           f"{'it' if len(sealed_on) == 1 else 'them'} off" if sealed_on else "") if part))
    for key in ROUND4_DEFAULTS.values():
        if provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    body = job.out_md.read_bytes()
    if sha256_bytes(body) != provenance.get("markdownSha256"):
        return False, "markdown does not match its seal"
    sealed = provenance.get("jsonPayloadSha256")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed)) != sealed:
        return False, "payload seal does not match"
    # Content-fidelity evidence is checked wherever a page carries it (it
    # must still be bound to this render and text); a page with none - the
    # switch off, or sealed before it existed - needs none.
    if _content_fidelity.carries_record(record) or expect.get(CONTENT_FIDELITY_KEY):
        try:
            _content_fidelity.validate_completed_record(record, body.decode("utf-8"), provenance["renderSha256"])
        except (ValueError, KeyError, TypeError) as error:
            return False, f"content fidelity: {error}"
    if legacy:
        return True, LEGACY_REASON + ", ".join(legacy)
    return True, "current"


def page_spans(pages: Collection[int]) -> str:
    """PAGES as runs: 1-3, 7, 9-10."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(f"{lo}-{hi}" if hi > lo else str(lo) for lo, hi in runs)


def round4_split(output: Path, rewritten: Collection[int], expect: Mapping[str, Any]) -> str | None:
    """Why a run with EXPECT's provenance would leave the book in OUTPUT decided
    two ways, else None: a page sealed there that this run does not write again
    (REWRITTEN: the pages it proofreads with --overwrite) with one of the
    round-4 fixes (ROUND4_DEFAULTS) on where this run has it off, or off where
    it has it on.  validate_completion blocks such a page, but the run would
    go on to decide the book's other pages with this run's switches: a book
    begun before the fixes were on by default and resumed without the four
    --no-* switches would be sealed half with them off and half on, and no
    later run finds every page current without --overwrite.  So the run does
    not start (main); the reason says which switches resume the book as its
    sealed pages were decided."""
    wanted = {key for key in ROUND4_DEFAULTS.values() if key in expect}
    skip = set(rewritten)
    states: dict[frozenset, list[int]] = {}
    for path in sorted(output.glob("page-*.json")):
        match = re.fullmatch(r"page-(\d{4})\.json", path.name)
        if not match or int(match.group(1)) in skip:
            continue
        try:
            provenance = json.loads(path.read_text(encoding="utf-8")).get("provenance")
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
        if not isinstance(provenance, dict) or "renderSha256" not in provenance:
            continue
        states.setdefault(frozenset(key for key in ROUND4_DEFAULTS.values() if key in provenance),
                          []).append(int(match.group(1)))
    other = {state: pages for state, pages in states.items() if state != wanted}
    if not other:
        return None
    switch = {key: name for name, key in ROUND4_DEFAULTS.items()}
    order = list(ROUND4_DEFAULTS.values())

    def names(keys: Collection[str]) -> list[str]:
        return [switch[key] for key in order if key in set(keys)]

    if len(states) > 1:
        # The sealed pages disagree among themselves already: no run resumes
        # them all.
        return ("the sealed pages in " + str(output) + " were decided with the on-by-default switches set in "
                + str(len(states)) + " ways: "
                + "; ".join(f"pages {page_spans(pages)} with "
                            + (", ".join(names(state)) or "none of " + ", ".join(ROUND4_DEFAULTS)) + " on"
                            for state, pages in sorted(states.items(), key=lambda item: min(item[1])))
                + " - no run finds them all current; decide one part again with --overwrite and that part's "
                  "--pages, with the switches of the other; nothing was proofread")
    (state, pages), = other.items()
    sealed_off, sealed_on = names(wanted - state), names(state - wanted)
    return ("resume "
            + " and ".join(part for part in (
                "with " + " ".join("--no-" + name[2:] for name in sealed_off) if sealed_off else "",
                "without " + " ".join("--no-" + name[2:] for name in sealed_on) if sealed_on else "") if part)
            + f": {len(pages)} sealed page(s) in {output} ({page_spans(pages)}) were decided with "
            + "; ".join(part for part in (
                f"{', '.join(sealed_off)} off" if sealed_off else "",
                f"{', '.join(sealed_on)} on" if sealed_on else "") if part)
            + ", this run the other way; a book is decided one way throughout, so nothing was proofread "
              "(to decide every page again with this run's switches: --overwrite, every page)")


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


def draft_digest(pages: Sequence[int], drafts: Path, drafts2: Path | None, *, legacy: bool = False) -> str:
    """Identity of the drafts a profile was measured on (its probe pages only)."""
    digest = hashlib.sha256()
    for page in pages:
        for directory in (drafts, drafts2):
            path = (directory / f"page-{page:04d}.txt") if directory else None
            digest.update(f"page-{page:04d}.txt".encode())
            digest.update(sha256_file(path).encode() if path and path.is_file() else b"-")
            if not legacy:
                digest.update(b"\0semantics\0")
                digest.update(_draft_contract.semantic_digest(path.with_suffix(".json") if path else None).encode())
    return digest.hexdigest()


def profile_digest_matches(measured: str | None, pages: Sequence[int], drafts: Path, drafts2: Path | None) -> bool:
    if measured == draft_digest(pages, drafts, drafts2):
        return True
    requires = any(_draft_contract.requires_semantics(_draft_contract.read_record(
        directory / f"page-{page:04d}.json")) for page in pages for directory in (drafts, drafts2) if directory)
    return not requires and measured == draft_digest(pages, drafts, drafts2, legacy=True)


def profile_fingerprint(profile: Mapping[str, Any] | None) -> str:
    """Hash of what proofreading consumes from a profile, not of the file.

    Timestamps, usage and evidence change on every re-profile; sealing the
    file bytes would mark every page stale whenever the evidence was
    refreshed but the decisions were not.
    """
    if not profile:
        return ""
    engines = profile.get("engines") or {}
    notation = {k: v for k, v in (profile.get("notation") or {}).items() if k != "blocks"}
    return sha256_bytes(canonical_json_bytes({
        "schemaVersion": profile.get("schemaVersion"),
        "preferEngine": profile.get("preferEngine"),
        "decision": engines.get("decision"),
        "notation": notation,
        "layout": profile.get("layout"),
        "pairClasses": profile.get("pairClasses"),
    }))


# decision_provenance keys written only when a switch is on (absent = off).
# tieBreak, censusConfirmsEngineA, folioFurniture, topRungCaps,
# textColumnLayout, engineAConfirmsFirstAnswer and censusSkipOverCeiling are
# the keys of trials and switches that are gone (the last three replaced by
# the rule agreedMarks): no run writes them now, and a page sealed with one
# on is not current.
OPTIONAL_PROVENANCE = ("tieBreak", "censusConfirmsEngineA", "folioFurniture", "topRungCaps", "textColumnLayout",
                       "keepArabicNumerals", "loopDetector", "neighbourGuard")
# Switch --learned-heads (learned_heads_seal).
OPTIONAL_PROVENANCE += ("learnedHeads",)
OPTIONAL_PROVENANCE += ("engineAConfirmsFirstAnswer", "censusSkipOverCeiling")
# Switch --same-print-marks (agreed_marks, same_print).
OPTIONAL_PROVENANCE += ("samePrintMarks",)
# Switch --same-marks-any-form (agreed_marks, FORM_FOLD).
OPTIONAL_PROVENANCE += ("sameMarksAnyForm",)
OPTIONAL_PROVENANCE += ("unmarkedPageSample",)
# The round-4 fixes, on by default since the user's decision of 2026-09-30
# (after the round-4 benchmark), each with the provenance key it seals when
# on.  --no-<switch> turns one off.  Provenance records each one's state
# (present when on, absent when off), so a page sealed under the old
# defaults (all four off) is not current for a run with the new ones
# (validate_completion says which switches resume it), and a run that would
# decide the rest of such a book under the new ones does not start
# (round4_split): a book begun under the old defaults resumes with the four
# --no-* switches (its sealed pages stay current and the rest are decided
# the same way), or is proofread again, every page, with --overwrite; its
# sealed pages are never mixed with pages decided under the new defaults.
ROUND4_DEFAULTS = {"--keep-arabic-numerals": "keepArabicNumerals", "--loop-detector": "loopDetector",
                   "--neighbour-guard": "neighbourGuard", "--learned-heads": "learnedHeads"}
# Rules that are always on now, with the value a page sealed with each on
# carries: their switches went (2026-10-04) - the user's decided rules
# (D1-D5, D9, D15, D16, D20-D25, D27), the switches kept only for measuring,
# the chart step (its keys charts, chartVariants, chartReview, foldCut) and
# the proposed grid follow (gridFollow, never fired) - and their keys stay so
# that the pages sealed before are still current (validate_completion).
SEALED_ON = {
    "circleRule": "on", "bracketRule": "on", "doubledCensus": "on", "insertionCrop": "on",
    "furnitureInsertion": "on", "boundaryCrop": "on", "moveOnce": "on", "answerDeletionGuard": "on",
    "wholeRunCrop": "on", "foldedAlignment": "on", "lopsidedSplit": "on", "mergeLossGuard": "on",
    "cropCoverageCheck": "on", "answerNearby": "on", "leaderMarks": "on", "bulletMarks": "on", "markRule": "on",
    "charts": "on", "chartVariants": "on", "chartReview": "doubt", "rebuildLock": "both",
    "markAwareWitness": "on", "rebuildRegion": "on", "gridFollow": "on", "tableEdgeText": "on",
    "rebuiltOrientation": "on", "verdictPropagation": "on", "foldCut": "on",
}


# Recorded in every seal but never compared (validate_completion) and kept
# out of packet request IDs (packet_request_config): which transport, model
# and endpoint proofread a page, and the bytes of the helper code.  The
# user's decision of 2026-11-14: editing the scripts, switching between the
# agent's packets and a local model, or renaming the model or the endpoint
# does not itself make a sealed page stale or orphan a stored answer. The
# explicit punctuation mode is compared separately: transport auto defaults
# differ, so changing transport can change the decision mode.
INFORMATIONAL_PROVENANCE = ("proofreadTransport", "proofreadModel", "proofreadEndpoint",
                            "proofreadScriptSha256", "contentFidelitySha256", "contentNoteMarkersSha256",
                            "punctuationModeRequested", "punctuationFusionSha256")
# Keys decision_provenance gained after pages were first sealed without them,
# with the value their absence stands for: a page sealed before the key
# existed was decided that way.
PROVENANCE_ABSENT_MEANS = {"tableOrientation": "auto"}
# Rule keys the old skill added to its seals as its rules came on: the
# variant pairs from the lists alone, agreed marks, unread table characters
# and the rules always on now (SEALED_ON).  A page it sealed before one of
# them carries no such key and was decided without that rule.  The user's
# decision of 2026-11-14 accepts the old skill's seals as they are, with no
# proofreading again, so such a page is current; validate_completion says
# which keys it lacks (LEGACY_REASON) and the run report marks it legacy.
# A key that is present is compared as always.
LEGACY_ABSENT_KEYS = frozenset({"variantPairs", "agreedMarks", "unreadTableCharacters", *SEALED_ON})
LEGACY_REASON = "legacy seal, accepted as sealed; made before the rules "
# Switch --content-fidelity on (absent: off).  Compared one way only
# (validate_completion): a run with it on does not take a page sealed
# without its evidence (a page with the current v2 contract and no key, as
# the skill sealed every page before the switch, is judged by its evidence),
# a run with it off takes either.
CONTENT_FIDELITY_KEY = "contentFidelity"


def content_fidelity_on(arguments: Any) -> bool:
    """Switch --content-fidelity: the three whole-page image questions
    (inventory, content recovery, coverage review).  Off unless asked for."""
    return getattr(arguments, "content_fidelity", "off") == "on"


def punctuation_fusion_summary(passes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Completed passes only; success means no independent fallback/retry."""
    rows = [p["fusion"] for p in passes if p.get("fusion", {}).get("attempted")]
    return {"attempts": len(rows), "successes": sum(bool(r.get("success")) for r in rows),
            "fallbacks": sum(bool(r.get("fallbackReason")) for r in rows),
            "fallbackReasons": dict(Counter(r["fallbackReason"] for r in rows if r.get("fallbackReason")))}


def sum_fusion_summaries(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    summaries = [row.get("punctuationFusion") or {} for row in rows]
    return {**{key: sum(row.get(key, 0) for row in summaries)
               for key in ("attempts", "successes", "fallbacks")},
            "fallbackReasons": dict(sum((Counter(row.get("fallbackReasons") or {})
                                          for row in summaries), Counter()))}


def packet_request_config(arguments: Any) -> dict[str, str]:
    """The part of decision_provenance a packet request ID is bound to: what
    decides an answer, not the informational keys nor the fidelity switch
    (the question asked is the same either way)."""
    config = {key: value for key, value in decision_provenance(arguments).items()
              if key not in INFORMATIONAL_PROVENANCE and key != CONTENT_FIDELITY_KEY}
    # Packet's historical default was fusion. The exact same question keeps
    # its stored answer ID, whether requested as auto or explicitly fused.
    if punctuation_mode(arguments) == "fused":
        config.pop("punctuationMode", None)
    return config


def decision_provenance(arguments: Any) -> dict[str, str]:
    """What decided a page's characters, sealed so a resume under different
    decisions (another profile, new rulings, a switch set otherwise) is not
    mixed in.  INFORMATIONAL_PROVENANCE is recorded with them, not compared."""
    return {
        "proofreadTransport": getattr(arguments, "backend", "packet"),
        "punctuationMode": punctuation_mode(arguments),
        "punctuationModeRequested": getattr(arguments, "punctuation_mode", "auto"),
        "punctuationFusionSha256": sha256_file(Path(_punctuation_fusion.__file__)),
        # The packet label, or the HTTP model the client resolved (main).
        "proofreadModel": getattr(arguments, "model", None)
                          or (DEFAULT_MODEL if getattr(arguments, "backend", "packet") == "packet" else ""),
        "proofreadEndpoint": (getattr(arguments, "endpoint", None) or "")
                             if getattr(arguments, "backend", "packet") == "http" else "",
        "proofreadScriptSha256": sha256_file(Path(__file__)),
        "contentFidelitySha256": sha256_file(Path(_content_fidelity.__file__)),
        "contentNoteMarkersSha256": sha256_file(Path(_content_fidelity.__file__).with_name("_note_markers.py")),
        "profileSha256": getattr(arguments, "profile_sha", "") or "",
        "enginePreference": getattr(arguments, "prefer_engine", "") or "",
        "fallbackEngine": getattr(arguments, "fallback_engine", "") or "",
        "variantRulingsSha256": getattr(arguments, "variant_rulings_sha", "") or "",
        # The book-independent lists that also decide characters: which pairs
        # are two forms of one character (variant_classes).
        "variantListSha256": (user_variant_rulings(), _LIST_SHA256.get("variantList", ""))[1],
        "allographsSha256": (opencc_allographs(), _LIST_SHA256.get("allographs", ""))[1],
        # Since 2026-10-13 only the lists say which pairs are forms of one
        # character, never the model (classify_pair), so a page sealed when
        # the model's answer could decide is not current.
        "variantPairs": "lists",
        **SEALED_ON,
        # Whether a rebuilt table may write up to UNREAD_TABLE_LIMIT characters
        # no engine read, each confirmed by 裁決 (headerCharacters, confirm_unread).
        "unreadTableCharacters": "off" if getattr(arguments, "no_unread_table_characters", False) else "on",
        # The glyphs the mark rule takes for marks come from the book's drafts
        # (mark_aliases), so a page sealed under other evidence is not current.
        "markAliasesSha256": (lambda found: sha256_bytes(canonical_json_bytes(found))
                              if any(found.values()) else "")(getattr(arguments, "mark_aliases", None) or {}),
        # The rule agreedMarks, always on (the user's ruling of 2026-10-04, no
        # switch): a block whose marks both engines read alike is written with
        # them, unasked (agreed_marks).  A page sealed before it is not
        # current.
        "agreedMarks": "on",
        # Switch --same-print-marks: the engines' marks also for a block whose
        # print both engines read alike (agreed_marks, same_print).  Only when on.
        **({"samePrintMarks": "on"} if getattr(arguments, "same_print_marks", False) else {}),
        # Switch --same-marks-any-form: the engines' marks also where the two
        # wrote one mark in two forms (agreed_marks with FORM_FOLD).  Only when on.
        **({"sameMarksAnyForm": "on"} if getattr(arguments, "same_marks_any_form", False) else {}),
        # Switch --unmarked-page-sample: on a page neither engine read marks
        # on, a sample of the blocks is asked and the rest are taken as
        # unmarked when the sample is (unmarked_candidates).  Only when on.
        **({"unmarkedPageSample": "on"} if getattr(arguments, "unmarked_page_sample", False) else {}),
        # Engine A's figures written where they are the only reading of their
        # print (--keep-arabic-numerals): the book's figures and the glyphs
        # engine B writes for them, from the drafts (figure_evidence), so a
        # page sealed under other evidence is not current.  Only when on.
        **({"keepArabicNumerals": figure_evidence_seal(arguments)}
           if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Switch --loop-detector: requests streamed and a rung given up in a
        # word-for-word loop of its reasoning, by this rule (_loop_watch).
        # Only when on.
        **({"loopDetector": _loop_watch.RULE.text()} if getattr(arguments, "loop_detector", False) else {}),
        # Switch --neighbour-guard: a 裁決 answer that copies the text beside
        # its spot is not written (neighbour_copy), within this window.  Only
        # when on.
        **({"neighbourGuard": f"window={NEIGHBOUR_WINDOW}"} if getattr(arguments, "neighbour_guard", False)
           else {}),
        # Switch --learned-heads: the running head learned by place, sealed by
        # what was learned (learned_heads_seal), so a page sealed under another
        # book's evidence or another profile's is not current.  Only when on.
        **({"learnedHeads": learned_heads_seal(arguments)} if getattr(arguments, "learned_heads", False) else {}),
        "tableOrientation": getattr(arguments, "table_orientation", "auto") or "auto",
        **({CONTENT_FIDELITY_KEY: "on"} if content_fidelity_on(arguments) else {}),
    }


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


def proofread_page(
    job: PageJob, client: Client, arguments: argparse.Namespace
) -> dict[str, Any]:
    with packet_page_context(job.scan_page, job.render, sha256_file(job.render)):
        return _proofread_page_inner(job, client, arguments)


def _proofread_page_inner(
    job: PageJob, client: Client, arguments: argparse.Namespace
) -> dict[str, Any]:
    started = time.monotonic()
    ledger: list[dict[str, Any]] = []
    _CALL_LEDGER.set(ledger)   # this page's thread; its worker threads get it via in_context
    # The calls with a rung the loop detector gave up, for decisionChanges.
    loop_cuts: list[dict[str, Any]] = []
    _LOOP_CUTS.set(loop_cuts)
    render_bytes = job.render.read_bytes()
    draft = strip_paddlex_img_tags(job.draft_text.read_text(encoding="utf-8")).strip()
    # Engine A's layout blocks: the merge loads them; empty on a page
    # transcribed whole (note_question_reason reads them at the end).
    blocks: list[dict[str, Any]] = []
    metrics = aggregate_usage([])
    metric_calls: list[Mapping[str, Any]] = []
    page_concurrency = max(1, getattr(arguments, "page_concurrency", DEFAULT_PAGE_CONCURRENCY) or 1)
    table_orientation_mode = getattr(arguments, "table_orientation", "auto") or "auto"
    recovery_packet_pending = False
    rebuild_packet_pending = False

    def add(m: Mapping[str, Any]) -> None:
        metric_calls.append(m)
        metrics.update(aggregate_usage(metric_calls))

    draft2 = (strip_paddlex_img_tags(strip_grounding(job.draft2_text.read_text(encoding="utf-8"))).strip()
              if job.draft2_text is not None and job.draft2_text.is_file() else None)
    adjudications: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    punctuation_passes: list[dict[str, Any]] = []
    # Brackets the engines read that the punctuator left out (written in) or
    # put a character or two away (moved): see place_brackets.
    engine_brackets = {"inserted": 0, "moved": 0}
    profile_furniture: list[str] = []
    # Switch --learned-heads: the blocks left out as the learned running head
    # (learned_head_block), by position.
    learned_dropped: set[int] = set()
    variant_unruled: list[str] = []
    rejected_output: str | None = None
    # The text the structure pass was given (structure_input_record).
    structure_input: dict[str, Any] | None = None
    # Why the formatter's first answer was refused, when it was (a retry followed).
    format_retry_reason: str | None = None
    # Why the retry was refused too, and what it wrote: before, only the first
    # answer and its reason were sealed, so a page that fell back could not be
    # audited for what the retry did (formatterRetryRefusal).
    retry_refusal: str | None = None
    rejected_retry: str | None = None
    # Every reason the page is not clean, {kind, detail}: auditorClean is "no
    # doubts" and auditorReport their details, so a run report can count kinds.
    doubts: list[dict[str, Any]] = []
    if job.missing_engine:
        doubts.append(missing_engine_doubt(job))
    if job.missing_engine == "A":
        # Engine B's text in engine A's place: without its grounding markup.
        draft = strip_grounding(draft).strip()
    # Engine B's reply for this page did not end (its runner keeps such a reply
    # as the draft and marks it truncated): its reading stops short of the
    # page, in a loop more often than not (find_loops).  What it read before
    # the loop is a reading of the page like any other: it is cut at the loop
    # (cut_engine_b_loops, as the draft cuts it) and merged as the second
    # reading, the text past the cut read by engine A alone, and a doubt names
    # the cut.  Measured on a fiscal table printed over six pages (three
    # replies cut off in a loop of 〇 or 八、 after the page's whole table):
    # taken whole the loop was read against the page, and dropped the three
    # pages went the one-engine way - no table path, no stitch, 717 more
    # errors on the benchmark than the old skill, which merged the same
    # readings.  Only where the cut leaves fewer than UNCORROBORATED_WITNESS
    # characters (the reply looped from its start) is there no reading to
    # merge: the page goes the one-engine way (transcribed from the image,
    # engine A's draft its hint).  Whether the page holds text at all still
    # counts what engine B read (read_b): a page is not sealed blank for want
    # of engine A's text when engine B's cut-off reading holds some.
    cut_reading = ""
    b_cut_off = engine_b_cut_off(job.draft2_text) if draft2 is not None else None
    if b_cut_off is not None:
        whole_b = draft2 or ""
        kept_b, b_loops = cut_engine_b_loops(whole_b)
        cut = (f" and from character {len(CJK(whole_b[:b_loops[0][0]]))} on it wrote "
               f"「{' '.join(LOOP_MARKUP.sub(' ', loop_unit_from_tag(b_loops[0][2])).split())[:12]}」 "
               f"{(b_loops[0][1] - b_loops[0][0]) // len(b_loops[0][2])} times over" if b_loops else "")
        if len(CJK(kept_b)) >= UNCORROBORATED_WITNESS:
            draft2 = kept_b
            doubts.append({"kind": "engine-b-truncated", "detail": (
                f"ENGINE B CUT OFF: its reply for this page did not end (finish reason {b_cut_off}){cut}; "
                + (f"its reading is merged up to there ({len(CJK(kept_b))} of its {len(CJK(whole_b))} "
                   "characters; the loop is not merged)" if b_loops else
                   f"its reading is merged as far as it goes ({len(CJK(kept_b))} characters)")
                + ", so what the page holds past that is engine A's reading alone - check it against the "
                "image, or OCR the page again with engine B")})
        else:
            cut_reading, draft2 = whole_b, None
            doubts.append({"kind": "engine-b-truncated", "detail": (
                f"ENGINE B CUT OFF: its reply for this page did not end (finish reason {b_cut_off}){cut}, "
                f"leaving {len(CJK(kept_b))} of its {len(CJK(whole_b))} characters, so it is not merged as a "
                "second reading; the page is transcribed from the image with engine A's draft as its hint - "
                "check it against the image, or OCR the page again with engine B")})
    read_b = draft2 if draft2 is not None else (cut_reading or None)
    # Defined here because the single-engine path never reaches the merge branch
    # but the sealed record always reports them.
    tables: list[str] = []
    table_sources: list[int] = []
    table_notes: list[str] = []
    tables_missing: list[int] = []
    tables_disputed: list[int] = []
    table_characters: str = ""
    # Characters the accepted structure pass left out of the table blocks it
    # was given (split_table_losses), for the table-characters-lost doubt.
    structure_table_lost: str = ""
    # The marks its punctuation lock let through in the tables it built from
    # the image (table_marks_lock), for the seal.
    structure_table_marks: str = ""
    prose: str | None = None
    guarded: str | None = None
    known_heads: list[str] = []
    tables_rejected: list[dict[str, Any]] = []
    table_orientation: list[str] = []
    table_orientation_evidence: list[dict[str, Any]] = []
    source_table_mapping_current = True
    recovery_table_correspondence: list[dict[str, Any]] = []
    table_grouping: list[str] = []
    cell_direction: list[dict[str, Any]] = []
    table_nominations: list[dict[str, Any]] = []
    table_rebuilds: list[dict[str, Any]] = []
    # Switch --keep-arabic-numerals: engine A's figures the merge writes, with
    # their gaps in the merged text, and every figure of the page, kept or not
    # (arabic_figure_kept).
    figures_kept: list[dict[str, Any]] = []
    arabic_figures_seen: list[dict[str, Any]] = []
    # And the figures and marks engine A read in brackets (bracketed_pairs),
    # for the doubt where the page does not hold one.
    page_bracketed: list[tuple[int, str, str]] = []
    # Why the merge did not keep the figure at each gap of engine A's stream.
    figure_why: dict[int, str] = {}
    # An engine-A block whose reading was a loop, set aside (quarantine_looped_block).
    loops_dropped: list[dict[str, Any]] = []
    # Every text change made by a rule that depends on one of the user's
    # decisions: which decision and rule, where (layout block, engine A's
    # context, offset in the merged text), what it wrote there ("after") and
    # the evidence.  The run report counts them by decision.
    decision_changes: list[dict[str, Any]] = []
    # Each lopsided replacement split into its like-for-like part and text only
    # one engine read (split_lopsided), with what the merge wrote for it.
    lopsided: list[dict[str, Any]] = []
    # Places where the merge dropped text one engine read, which the merged text
    # holds nowhere (readings_dropped); and what rules that read it as no text of
    # the page left out (brackets and marks read as characters), which is not.
    readings_lost: list[dict[str, Any]] = []
    explained: dict[str, list[tuple[int, int]]] = {"A": [], "B": []}
    engine_a_characters = len(CJK(draft))
    engine_b_characters = len(CJK(draft2 or ""))

    inventory: dict[str, Any] = {}
    # Switch --content-fidelity (off unless asked): the page's full-image
    # inventory, its content recovery before formatting and its coverage
    # review of the final text.  `reviewing` stays true while the stages
    # are settled; a stage that cannot be (fidelity_doubt) ends them for the
    # page, which is sealed with the text it had and a doubt, not failed.
    fidelity_on = content_fidelity_on(arguments)
    reviewing = fidelity_on
    fidelity_status = "complete"
    content_recovery: dict[str, Any] | None = None
    coverage_review: dict[str, Any] | None = None

    def fail_fidelity(candidate: str, detail: Mapping[str, Any]) -> None:
        evidence = {"schema_version": 2, "contract_version": _content_fidelity.CONTRACT_VERSION,
                    "status": "failed", "source_image_sha256": hashlib.sha256(render_bytes).hexdigest(),
                    "candidate": candidate, "candidate_sha256": _content_fidelity.text_sha256(candidate),
                    "inventory": inventory,
                    "detail": dict(detail), "formatter_rejected_output": rejected_output,
                    "formatter_retry_reason": format_retry_reason, "formatter_rejected_retry": rejected_retry,
                    "formatter_retry_refusal": retry_refusal}
        path = job.out_json.with_name(job.out_json.stem + ".fidelity-failure.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_json_bytes(evidence)
        # Immutable content-addressed failures survive retry and later success.
        path = path.with_name(path.stem + "-" + hashlib.sha256(encoded).hexdigest()[:16] + path.suffix)
        if not path.exists():
            path.write_bytes(encoded)

    def fidelity_doubt(stage: str, detail: str) -> None:
        """A fidelity stage that could not be settled: a doubt, and no further
        stage on this page."""
        nonlocal reviewing, fidelity_status
        reviewing, fidelity_status = False, "doubt"
        doubts.append({"kind": _content_fidelity.DOUBT_KIND, "detail": (
            f"CONTENT FIDELITY UNRESOLVED ({stage}): {detail} - check the page against the image")})

    def review_changes(stage: str, evidence: Mapping[str, Any], adopted: bool) -> None:
        """Each edit a fidelity review made (or proposed, when set aside), for
        decisionChanges: what it replaced, with what, and its evidence."""
        for edit in evidence.get("edits") or []:
            decision_changes.append({
                "decision": "contentFidelity", "rule": stage, "region": edit.get("region_id"),
                "contextBefore": edit.get("before", ""), "contextAfter": edit.get("after", ""),
                "before": edit.get("old", ""), "after": edit.get("new", ""),
                "evidence": {"source": edit.get("evidence"), "adopted": adopted,
                             "reviewedResultSha256": evidence.get("reviewed_result_sha256")}})

    # With the switch on, every page first receives a full-image inventory,
    # including pages for which both CJK streams are empty.  Packet replay
    # binds image and prompt; no standalone old sidecar can authorize a blank
    # or a content repair.
    if fidelity_on:
        try:
            inventory, inventory_metrics = _content_fidelity.inventory_page(
                client, render_bytes, [draft, draft2 or ""], arguments.max_tokens)
            add(inventory_metrics)
        except _content_fidelity.FidelityError as error:
            fail_fidelity(draft, {**error.diagnostic(), "stage": "page-inventory",
                                 "rejected_answer": getattr(error, "rejected_answer", None),
                                 "correction": getattr(error, "correction", None)})
            inventory = {}
            fidelity_doubt("page-inventory", f"no valid inventory answer ({error}); the page is not image-reviewed")

    def image_review(candidate: str, stage: str) -> tuple[str, dict[str, Any] | None]:
        """CANDIDATE after the STAGE review, and the review's evidence.  An
        answer that fails its contract (after its one correction) is a doubt
        and leaves CANDIDATE as it was; so is a coverage review that does not
        come back complete, whose edits stand only when they lose no source
        feature and leave retained content."""
        try:
            result, evidence, used = _content_fidelity.review_page(
                client, render_bytes, candidate, inventory, arguments.max_tokens, stage=stage,
                table_orientation=table_orientation_mode)
        except _content_fidelity.FidelityError as error:
            fail_fidelity(candidate, {**error.diagnostic(), "stage": stage,
                                     "rejected_answer": getattr(error, "rejected_answer", None),
                                     "correction": getattr(error, "correction", None)})
            fidelity_doubt(stage, f"no valid review answer ({error}); the text is kept as it was before the review")
            return candidate, None
        add(used)
        adopted = True
        if stage == "coverage-review" and not evidence["complete"]:
            adopted = not evidence["feature_losses"] and not evidence["empty_retained_content"]
            fail_fidelity(result, {"code": "visual-uncertain", "review": evidence})
            open_items = [*(f"issue {i}" for i in evidence.get("unresolved_issue_ids") or []),
                          *(f"region {r['id']} {r['status']}" for r in evidence.get("regions") or []
                            if r.get("status") == "uncertain"),
                          *(f"table region {t} unmapped" for t in evidence.get("unmapped_table_regions") or []),
                          *(f"feature {f} lost" for f in evidence.get("feature_losses") or [])]
            fidelity_doubt(stage, f"coverage {evidence.get('verdict')}: "
                           + ("; ".join(open_items[:8]) or "not confirmed complete")
                           + ("; its edits are kept" if adopted else "; its edits are set aside"))
        review_changes(stage, evidence, adopted)
        return (result if adopted else candidate), evidence

    # A page carrying no text either engine can corroborate.  Two shapes:
    # genuinely blank (both engines silent) and unsupported (one engine emitted
    # a page of degenerate text the other never saw).  Both are sealed rather
    # than failed, so the assembled deliverable has no hole where a page should
    # be - three such pages failed the whole phase before this existed.  With
    # content fidelity on, the inventory may also find the page blank, and a
    # page the engines found nothing on is transcribed from the image unless
    # the inventory finds it blank.  A page whose engines read text with no
    # CJK in it (non_cjk_text) is not blank: with nothing to merge, it is
    # transcribed from the image, both drafts its hint.
    unsupported = unsupported_reading(draft, read_b)
    engines_silent = not CJK(draft) and not CJK(read_b or "")
    non_cjk = non_cjk_text(draft, read_b, load_blocks(job.draft_json)) if engines_silent else ""
    if (reviewing and inventory["page_state"] == "blank" and (not engines_silent or non_cjk)
            and not unsupported):
        # The engines read text the inventory says is not there: their text is
        # merged (or transcribed) as usual, and a person looks (a blank answer
        # never empties a page the engines read).
        fidelity_doubt("page-inventory", (
            f"the inventory calls the page blank, but the engines read {len(CJK(draft))} and "
            f"{len(CJK(draft2 or ''))} characters; their text is kept, not image-reviewed"
            if not engines_silent else
            f"the inventory calls the page blank, but the engines read {len(non_cjk)} characters "
            "of text with no CJK in it; the page is transcribed from the image, not image-reviewed"))
    if ((reviewing and inventory["page_state"] == "blank") or (engines_silent and (reviewing or not non_cjk))
            or unsupported):
        if unsupported:
            doubts.append({"kind": "unsupported-reading", "detail": (
                f"one engine returned {len(CJK(unsupported))} characters the other "
                f"never saw, and the text repeats itself "
                f"({distinct_ngram_ratio(CJK(unsupported)):.3f} distinct 4-grams); "
                + ("requires a full-image transcription rather than a blank shortcut"
                   if reviewing and inventory["page_state"] != "blank" else "discarded as unsupported"))})
        writer_text = ""
        mode = "blank-page"
        if reviewing and inventory["page_state"] != "blank":
            mode = "image-recovery"
            writer_text, content_recovery = image_review(draft if not unsupported else "", "content-recovery")
            if reviewing:
                writer_text, coverage_review = image_review(writer_text, "coverage-review")
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "scanPage": job.scan_page,
            "status": "complete",
            "textAuthority": TEXT_AUTHORITY,
            "generatedAt": utc_now(),
            "model": client.model,
            "reasoningEffort": client.effort,
            **effort_policy_record(client),
            "punctuationMode": getattr(arguments, "punctuation_mode", "auto"),
            "punctuationModeEffective": punctuation_mode(arguments, client),
            "punctuationFusion": punctuation_fusion_summary([]),
            "renderFile": str(job.render),
            # An unsupported reading is not a clean page: a human should look.
            "auditorClean": not doubts,
            "mode": mode,
            **({"contentFidelityContract": _content_fidelity.CONTRACT_VERSION,
                "contentFidelityStatus": fidelity_status,
                "pageInventory": inventory or None,
                "contentRecovery": content_recovery,
                "coverageReview": coverage_review} if fidelity_on else {}),
            "inkFraction": round(page_ink_fraction(job.render), 6),
            "engineDisagreements": [],
            "auditorReport": "; ".join(d["detail"] for d in doubts),
            "doubts": doubts,
            # Kept, not dropped: if this judgement is ever wrong, the text is
            # still here to recover.
            "discardedOnBlankPage": unsupported[:2000] if unsupported else "",
            "adjudications": [],
            # Each edit a fidelity review made (switch --content-fidelity).
            **({"decisionChanges": decision_changes} if decision_changes else {}),
            "usage": metrics,
            "punctuationDensity": 0.0,
            "degraded": False,
            "seconds": round(time.monotonic() - started, 1),
            "calls": summarize_calls(ledger),
            "provenance": {
                **sealed_inputs(job),
                **decision_provenance(arguments),
                "markdownSha256": sha256_bytes(writer_text.encode("utf-8")),
            },
        }
        record["provenance"]["jsonPayloadSha256"] = sha256_bytes(
            canonical_json_bytes(record))
        seal_page(job, writer_text.encode("utf-8"), record, events=getattr(arguments, "packet_events", None))
        return record

    # Transcribed, not merged: one engine only, or text with no CJK to merge.
    transcribing = draft2 is None or bool(non_cjk)
    if transcribing:
        # Single-engine fallback: no second reading to merge against, so the
        # model must transcribe. Less accurate - measured 4/11 vs 11/11 for two
        # agreeing engines - but it is all that is available.  The same for a
        # page of text with no CJK (non_cjk), which the merge cannot align:
        # both drafts are its hint.
        hint = (f"OCR 底稿（提示，非權威）：\n---\n{draft}\n---\n\n" if draft2 is None else
                f"OCR 底稿 A（提示，非權威）：\n---\n{draft}\n---\n\n"
                f"OCR 底稿 B（提示，非權威）：\n---\n{draft2}\n---\n\n")
        writer_text, m = client.ask_answering(
            WRITER_SYSTEM, hint + "睇頁圖，輸出呢一頁嘅忠實 Markdown 轉錄。",
            render_bytes, arguments.max_tokens, kind="writer")
        add(m)
        writer_text = strip_fence(writer_text)
        if not writer_text and not non_cjk:
            raise ProofreadError("writer returned empty output")
        if not writer_text:
            # Sealed, not failed (a blank page is), but not clean: the engines
            # read text the transcription does not hold.
            doubts.append({"kind": "non-cjk-text-not-transcribed", "detail": (
                f"NO TEXT TRANSCRIBED: the engines read {len(non_cjk)} characters of text with no CJK "
                f"in it (「{non_cjk[:60]}」), and the transcription from the image is empty - "
                "check the page against the image")})
        resolved, mode = writer_text, "transcribe"
        if reviewing:
            writer_text, content_recovery = image_review(writer_text, "content-recovery")
    else:
        # MERGE. Where two independent engines agree, that reading is frozen and
        # no model is allowed to revise it. Only differences go to adjudication.
        mode = "merge"
        # On an order conflict, the engine whose reading the page takes whole.
        order_text: str | None = None
        blocks = load_blocks(job.draft_json)
        # One looped engine-A block is set aside rather than the whole of engine
        # A's page - only when what is left then merges; otherwise the page is
        # judged as it always was (order_judgement).
        # The engine preference that settles this page's disagreements.
        page_prefer = arguments.prefer_engine
        judged = order_judgement(blocks, draft, draft2, prefer=page_prefer)
        segments, agreement, quarantine = judged["segments"], judged["agreement"], judged["quarantine"]
        if quarantine is not None:
            blocks, draft = quarantine["blocks"], quarantine["draft"]
            dropped = quarantine["dropped"]
            loops_dropped.append(dropped)
            found = dropped["engineBReading"]
            doubts.append({"kind": "engine-a-loop-dropped", "detail": (
                f"ENGINE A LOOPED IN ONE BLOCK: block {dropped['block']} ({dropped['label']}, "
                f"{dropped['characters']} characters, {dropped['distinct4gram']:.3f} distinct 4-grams) "
                "repeats itself where engine B does not; its reading is set aside (engineALoopsDropped) "
                "and the region is "
                + (f"engine B's reading alone ({dropped['engineBCharacters']} characters)"
                   if found["found"] == "insertion" else
                   f"engine B's reading alone ({dropped['engineBCharacters']} characters), split off "
                   f"engine A's 「{found['sharedWith']}」, which the alignment had set against it and "
                   "which stays as engine A's own reading"
                   if found["found"] == "split" else
                   "left empty: nothing only engine B read shares the loop's 4-grams, so no reading "
                   "of the region was found")
                + " - check it against the image")})
        if agreement < READING_ORDER_AGREEMENT:
            # The engines disagree about READING ORDER, not just characters -
            # observed on a vertical title page where one engine scrambled the
            # columns. Character alignment is meaningless then and produces
            # spliced nonsense, so take the preferred engine's page whole.
            mode = "merge-order-conflict"
            # Take the preferred engine's page whole - but NEVER at the cost of
            # most of the page.  Measured on a fiscal report (NLC) p.24: HunyuanOCR
            # read 36 characters (the title and running head, missing the table
            # entirely) while PaddleOCR read 151 including a complete 19-row
            # table.  Preferring B discarded the table, and because the loss
            # happened at merge time rather than format time, droppedRuns saw
            # nothing and the page was sealed auditorClean.  A preference is for
            # choosing between two readings of the same page, not for choosing
            # a fragment over a page.
            order_engine = (arguments.prefer_engine if arguments.prefer_engine in ("A", "B")
                            else getattr(arguments, "fallback_engine", "A"))
            preferred = CJK(draft2 if order_engine == "B" else draft)
            other = CJK(draft if order_engine == "B" else draft2)
            # Length alone is not a quality signal in EITHER direction.  Too
            # short and the preferred engine read a fragment, not the page; too
            # long and it looped.  Measured on a multi-page fiscal report: p.24 HunyuanOCR
            # gave 36 characters against PaddleOCR's 157, while p.28 and p.29 it
            # gave 3,996 and 7,990 - 八八八八… and 〇〇〇〇… - against 339 and 308.
            # An earlier version guarded only the short side and then happily
            # took the flood.  Repetition is what tells them apart: genuine
            # readings here score 0.91-0.99 distinct 4-grams, the loops 0.007.
            preferred_looped = (len(preferred) > UNCORROBORATED_MINIMUM
                                and distinct_ngram_ratio(preferred) < DEGENERATE_NGRAM_RATIO)
            other_looped = (len(other) > UNCORROBORATED_MINIMUM
                            and distinct_ngram_ratio(other) < DEGENERATE_NGRAM_RATIO)
            # In an order conflict the whole page comes from one engine, so the
            # choice should favour the reading we can still structure.  Engine A
            # supplies the layout blocks, so when it holds a table that engine B
            # never saw, taking B throws that table away and the recovery then
            # cannot find its text in the merged page - which is what happened on
            # a multi-page fiscal report p.25, where the two readings were within five
            # characters of each other and only one carried the grid.
            table_text = CJK("".join(str(b.get("text") or "") for b in blocks
                                     if b.get("label") == "table"))
            structured_only_in_other = bool(table_text) and (
                order_engine == "B") and (
                _covers(other, table_text) and not _covers(preferred, table_text))

            other_engine = "A" if order_engine == "B" else "B"
            if preferred_looped and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back-degenerate"
            elif len(preferred) < len(other) * ORDER_CONFLICT_FLOOR and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back"
            elif structured_only_in_other and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back-structured"
            else:
                chosen, order_text = preferred, order_engine
            segments = [{"tag": "equal", "a": chosen,
                         "b": "", "before": "", "after": ""}]
        # Text engine B read in another order is placed once, at engine A's
        # site (decision D2, place_moved_once).
        moves: list[dict[str, Any]] = []
        # The segments as aligned, before any run moved: what both engines read
        # in each block, for D22's region (rebuild_region), and the stretch a
        # moved run's site entry covers (site_span).
        aligned = segments
        if mode == "merge":
            segments, moves = place_moved_once(segments)
        # A lopsided replacement - a line one engine skipped, fused with a
        # character or two read differently beside it - is split into its
        # like-for-like part and text only one engine read, before anything is
        # decided (split_lopsided; the user's fix of 2026-09-27): the surplus
        # goes the way an insertion goes.  After D2, which reads a moved run
        # against engine A's side of a replacement whole (a price's label read
        # in two places and its first figure read 一 and 二 - split first, the
        # figures became two questions instead of one).  Only where the
        # engine preference settles a replacement: on a page 裁決 reads every
        # disagreement off its crop (no preference) it sees
        # the replacement whole, and D16 keeps the longer reading when the
        # answer leaves most of it out.  Measured on the benchmark replay, the
        # split there made two or three questions of one and took a fold
        # title engine A read against a sentence only engine B read (answered
        # with one character, D16 kept the sentence) for text of its own - a
        # question 裁決 can only answer by keeping it, or by 無 that D16 then
        # refuses: the title would stand in the body either way.
        if mode == "merge" and page_prefer in ("A", "B"):
            split_lengths = [len(CJK(b["text"])) for b in blocks]
            segments, lopsided = split_lopsided_segments(
                segments, list(itertools.accumulate(split_lengths))
                if blocks and sum(split_lengths) == len(CJK(draft)) else None, draft, draft2 or "", page_prefer)
        pieces: list[str] = []
        # Block index and offset of each disagreement, straight from its position
        # in engine A's stream; text search at a block boundary was measured to
        # crop the running foot instead of a table's last figure and delete it.
        block_lengths = [len(CJK(b["text"])) for b in blocks]
        block_ends = list(itertools.accumulate(block_lengths))
        offsets_valid = bool(blocks) and sum(block_lengths) == len(CJK(draft))
        book_heads = sorted(set(profile_section(arguments, "layout").get("runningHeads") or []))
        book_furniture = getattr(arguments, "furniture", frozenset())
        table_work = offsets_valid
        # An order-conflict page that took engine A's reading takes a table
        # block of it that engine B contradicts from engine B's lines instead
        # (window_merge): engine A's copies and splices do not stand, and a
        # substitution inside still reaches 裁決.  Each such block's segments
        # carry "window" (its index), and ones decided without a question
        # "settled".  Not when engine B's reading looped, the same test the
        # order conflict applies above: its lines are no reading of the
        # block (measured: a year table read against a looped line came
        # out with a company name 85 times, 3,837 characters for engine A's
        # 357).
        windowed: dict[int, list[dict[str, Any]]] = {}
        witness_text = CJK(draft2)
        witness_looped = (len(witness_text) > UNCORROBORATED_MINIMUM
                          and distinct_ngram_ratio(witness_text) < DEGENERATE_NGRAM_RATIO)
        if (table_work and mode == "merge-order-conflict" and segments[0]["a"] == CJK(draft)
                and not witness_looped):
            for k, block in enumerate(blocks):
                if block.get("label") != "table":
                    continue
                grid, why = engine_table(str(block.get("text") or ""), draft2, book_heads, book_furniture)
                if grid is None and any(sign in why for sign in
                                        ("in another order", "both hold", "page furniture")):
                    # Engine A's reading of the rest of the page: where a line
                    # of engine B's runs on into another block (window_merge).
                    a_text, a_lo = CJK(draft), (block_ends[k - 1] if k else 0)
                    found = window_merge(block, draft2 or "", book_heads, book_furniture,
                                         elsewhere=a_text[:a_lo] + "\0" + a_text[block_ends[k]:])
                    if found:
                        windowed[k] = [dict(x, window=k) for x in found]
        if windowed:
            chosen_text, rebuilt, cursor = segments[0]["a"], [], 0
            for k, (lo, hi) in enumerate(zip([0] + block_ends[:-1], block_ends)):
                if k not in windowed:
                    continue
                if lo > cursor:
                    rebuilt.append({"tag": "equal", "a": chosen_text[cursor:lo], "b": "",
                                    "before": "", "after": ""})
                rebuilt.extend(windowed[k])
                cursor = hi
            if cursor < len(chosen_text):
                rebuilt.append({"tag": "equal", "a": chosen_text[cursor:], "b": "", "before": "", "after": ""})
            segments = rebuilt
        prefer = arguments.prefer_engine
        fallback = getattr(arguments, "fallback_engine", "A")
        # A printed circle one engine read as the numeral 〇 and the other not
        # at all is a mark, not a character (is_lone_circle); it is dropped
        # here, before any budget is set aside for numerals.
        # A circle fused with a neighbouring substitution (A 〇旂, B 旒) is first
        # split off (split_edge_circle), where it would be dropped.
        split: list[dict[str, Any]] = []
        a_at = 0
        for x in segments:
            # Engine B's reading of a set-aside block is kept whole, and a
            # window of an order-conflict table block (window_merge) keeps
            # its own segments.
            parts = (split_edge_circle(x) if not is_numeral_conflict(x) and not x.get(LOOPED_READING)
                     and "window" not in x else None)
            if parts:
                pieces_of, at = parts
                offset = a_at + sum(len(p["a"]) for p in pieces_of[:at])
                mark = pieces_of[at]
                if is_lone_circle(mark, locate_segment(blocks, block_ends, block_lengths,
                                                       offsets_valid, offset, mark)[0]):
                    if "bStart" in x:
                        pieces_of[0]["bStart"] = x["bStart"]
                        pieces_of[1]["bStart"] = x["bStart"] + len(pieces_of[0]["b"])
                    split.extend(pieces_of)
                    a_at += len(x["a"])
                    continue
            split.append(x)
            a_at += len(x["a"])
        segments = split
        if mode == "merge" and offsets_valid:
            # A stretch engine A read twice, the copy beside it standing for
            # it (read_twice), is not written, and nothing is asked about it:
            # engine B's side of a substitution goes the usual way.
            for i, k in reversed(read_twice(segments, blocks, block_ends)):
                x = segments[i]
                rest = ([{"tag": "insert", "a": "", "b": x["b"], "before": x["before"], "after": x["after"],
                          **({"bStart": x["bStart"]} if "bStart" in x else {})}] if x["b"] else [])
                copy = [dict(x, tag="delete", b="", settled=("", "engine-A-read-twice"))]
                # Engine B's side stands on the side of the stretch away from
                # the copy: where the copy follows (K == 0), before it - the
                # stretch is a second box on the next block's first line, and
                # what engine B read there ends the block before.
                segments[i:i + 1] = rest + copy if k == 0 else copy + rest
        starts = [0] + list(itertools.accumulate(len(x["a"]) for x in segments))[:-1]
        # Where each segment's engine-B text is in engine B's own: in segment
        # order, unless text read in another order was placed at engine A's
        # site, when each segment says (bStart).
        b_starts = ([x["bStart"] for x in segments] if segments and all("bStart" in x for x in segments)
                    else [0] + list(itertools.accumulate(len(x["b"]) for x in segments))[:-1])
        # Each engine's own marks, for the bracket rule: a numeral one engine
        # read where the other printed a bracket is that bracket, and the
        # brackets the engines read are written back after punctuation.
        bracket_rule = mode == "merge"
        stream_a, stream_b = engine_gaps(draft), engine_gaps(draft2)
        # And every printed mark, for the mark rule (decision D1): a character
        # one engine has in excess where the other printed a mark is that mark
        # misread, when the glyph is one this book's drafts show that engine
        # writing for marks (mark_explanation, mark_aliases).
        mark_rule = mode == "merge"
        marks_a, marks_b = engine_marks(draft), engine_marks(draft2)
        aliases = getattr(arguments, "mark_aliases", None) or {"A": {}, "B": {}}
        # Switch --keep-arabic-numerals: engine A's figures on this page
        # (arabic_figures, its ASCII dots before a figure a leader too) by
        # their gap in engine A's stream, each confirmed or not by the
        # numbering (figures_confirmed, with the book's figures before and
        # after the page) - a contents entry's page number (a figure ending a
        # line that holds a leader) needs none - and the glyphs engine B
        # writes for them or for marks on this book (figure_aliases,
        # mark_aliases).
        figure_rule = getattr(arguments, "keep_arabic_numerals", False) and mode == "merge" and offsets_valid
        page_figures: dict[int, list[dict[str, Any]]] = {}
        figure_glyphs: set[str] = set()
        if figure_rule:
            found_figures = arabic_figures(blocks, draft, dotted=True)
            for figure, confirmed in zip(found_figures, figures_confirmed(
                    [f["value"] for f in found_figures],
                    *figure_neighbours((getattr(arguments, "figure_evidence", None) or {}).get("numbering") or {},
                                       job.scan_page))):
                page_figures.setdefault(figure["gap"], []).append(
                    dict(figure, confirmed=confirmed or figure["leader"]))
            figure_glyphs = set(aliases.get("B") or {}) | set(getattr(arguments, "figure_aliases", None) or {})
            page_bracketed = bracketed_pairs(blocks)

        def figure_at(gap: int) -> dict[str, Any] | None:
            """The confirmed figure engine A read at GAP of its stream, alone there."""
            here = page_figures.get(gap) or []
            return here[0] if len(here) == 1 and here[0]["confirmed"] else None

        # Each segment's reading once the bracket and mark rules took out what
        # engine A's brackets and marks account for (a figure's neighbours are
        # like for like, arabic_figure_kept); each insertion at a figure's gap
        # the figure settles: how (marks, stand-in, verdict), and whether the
        # merge would write characters there without the figure (a stand-in's
        # question not asked: yes).
        after_rules: dict[int, Mapping[str, Any]] = {}
        figure_settled: dict[int, tuple[str, bool]] = {}
        # Questions the switch did not ask (the stand-ins of a figure): each
        # takes the budget it would have taken asked.
        figure_taken = 0

        def to_judge(seg: Mapping[str, Any], a_start: int, b_start: int) -> bool:
            """Whether the walk below would ask 裁決 about SEG, its budget aside:
            not where a variant ruling or the preference settles it (a numeral
            or rare-codepoint conflict and a short insertion D3 asks about go
            to 裁決 under a preference too)."""
            numeral_or_rare = is_numeral_conflict(dict(seg)) or is_rare_codepoint_conflict(dict(seg))
            if not numeral_or_rare and variant_segment(seg) is not None:
                return False
            return not (prefer in ("A", "B") and not numeral_or_rare and not short_insertion(seg, a_start, b_start))

        # Decision D3: a short insertion only one engine read is asked of 裁決.
        insertion_rule = mode == "merge"
        page_heads = sorted(set(profile_section(arguments, "layout").get("runningHeads") or []))
        page_furniture = getattr(arguments, "furniture", frozenset())

        def short_insertion(seg: Mapping[str, Any], a_start: int, b_start: int) -> bool:
            """Decision D3: text of INSERTION_LIMIT characters or fewer that one
            engine read and the other did not, not explained by the mark or the
            bracket rule (those settled it before this is asked) nor by the
            book's furniture - a piece of a running head one engine read and
            the other left out, which the formatter removes as furniture."""
            if not insertion_rule or bool(seg["a"]) == bool(seg["b"]):
                return False
            own = CJK(seg["a"] or seg["b"])
            if not own or len(own) > INSERTION_LIMIT:
                return False
            chars, at = (stream_a[0], a_start) if seg["a"] else (stream_b[0], b_start)
            if chars[at:at + len(own)] != own:
                return True
            lo, hi = at, at + len(own)
            for start in range(max(0, hi - FURNITURE_GRAM), min(lo, len(chars) - FURNITURE_GRAM) + 1):
                if chars[start:start + FURNITURE_GRAM] in page_furniture:
                    return False
            for head in page_heads:
                for start in range(max(0, hi - len(head)), min(lo, len(chars) - len(head)) + 1):
                    if is_running_head(chars[start:start + len(head)], [head]):
                        return False
            return True

        # Decision D15 (the user's ruling of 2026-09-27): the book's furniture
        # one engine read and the other did not is left out before anything
        # is asked.
        furniture_rule = mode == "merge"
        block_starts = [0] + block_ends[:-1]

        def furniture_insertion(seg: Mapping[str, Any], a_start: int, b_start: int) -> dict[str, Any] | None:
            """Decision D15: text one engine read and the other did not, every
            character of it inside a stretch that recurs across the book's pages
            (furniture_grams: a reprint's margin title, a running head), which is
            a whole layout block of engine A's or reaches the edge of the page as
            its engine read it.  Measured on a lecture page of a reprint: engine A
            read the reprint's margin title as a block of its own, engine B did
            not read it, and with no engine preference the insertion went to
            裁決, which rightly said it is printed - and the page kept 14 (in two
            runs 19, the verdict running on into the volume number) characters of
            margin furniture as a heading.  The recurrence the formatter's
            deletion guard already accepts as furniture covered all seven of its
            8-grams.  Recurrence, not the margin: the layout's margin blocks on
            that page included its real chapter headings.  Returns the evidence,
            or None."""
            if not furniture_rule or bool(seg["a"]) == bool(seg["b"]):
                return None
            own = CJK(seg["a"] or seg["b"])
            if len(own) < FURNITURE_GRAM or furniture_leftover(own, page_furniture, ()):
                return None
            side = "A" if seg["a"] else "B"
            chars, at = (stream_a[0], a_start) if side == "A" else (stream_b[0], b_start)
            if chars[at:at + len(own)] != own:
                return None
            edge = at == 0 or at + len(own) == len(chars)
            whole = side == "A" and offsets_valid and any(
                lo == a_start and hi == a_start + len(own) for lo, hi in zip(block_starts, block_ends))
            if not (edge or whole):
                return None
            return {"readBy": side, "characters": len(own), "wholeBlock": whole, "pageEdge": edge}

        # Switch --learned-heads: the learned running head, in the merge.
        learned_merge = learned_record(arguments) if mode == "merge" else None
        learned_merge_bands: list[dict[int, str]] = []

        def learned_merge_band() -> dict[int, str]:
            """Engine A's blocks in this page's learned band (learned_band),
            worked out once, when a reading needs it."""
            if not learned_merge_bands:
                learned_merge_bands.append(learned_band(blocks, learned_merge, job.scan_page))
            return learned_merge_bands[0]

        def learned_insertion(seg: Mapping[str, Any], a_start: int) -> dict[str, Any] | None:
            """Switch --learned-heads: text one engine read and the other did
            not that is all the learned running head with a stem in it
            (learned_leftover: the stem, the labels, the folio beside them),
            to be left out before anything is asked.  Engine B reads the head
            column in its stream wherever it falls - before the body, between
            two of its columns - where engine A read it as a block of its own
            or not at all: measured on a 488-page book's sealed pages, six
            such readings stood in the body, the title with the volume label
            and the folio, one letter of the title misread in two of them,
            which the recurring n-grams (D15) did not cover.  A stem is
            required: a label or numerals alone are what the body prints too.
            And a place apart from the body's words, in engine A's stream (its
            layout blocks, offsets_valid): engine A's text at an edge of its
            blocks - a block of its own, or the start or end of one the layout
            read with a body column - and engine B's where engine A passes
            from one block to the next or at the page's start or end; inside
            a block only where what engine A read between it and the block's
            start or end is the learned head too (the folio both engines read
            beside the title: measured on the replay of a book's sealed pages,
            engine A read a body line, the title with its label, and the
            folio as one block, and engine B the folio before the title).
            Never with the body's words of one block on both sides.  The text
            alone is no evidence: a stem is the title's words, a window of
            them one short or one misread (stem_windows), and a book's body
            names its own subject - measured on a history of a rebellion, the
            title's first five characters (the rebel's name and the word for
            the rebellion; one short of the title, a stem window) stand in
            the body of nine of its 344 pages; a made-up page
            (tests/check_learned_heads.py) naming the book in a sentence whose
            words engine A did not read.  Returns the evidence, or None."""
            if not learned_merge or bool(seg["a"]) == bool(seg["b"]) or "window" in seg or not offsets_valid:
                return None
            own = CJK(seg["a"] or seg["b"])
            left, found = learned_leftover(own, learned_merge, job.scan_page)
            if not own or left or not found["stems"]:
                return None
            a_end = a_start + len(seg["a"])
            if any(lo < a_start and a_end < hi
                   and learned_leftover(stream_a[0][lo:a_start], learned_merge, job.scan_page)[0]
                   and learned_leftover(stream_a[0][a_end:hi], learned_merge, job.scan_page)[0]
                   for lo, hi in zip(block_starts, block_ends)):
                return None
            # The blocks it is (engine A's) or stands between (engine B's).
            where = [k for k, (lo, hi) in enumerate(zip(block_starts, block_ends))
                     if lo < hi and (lo < a_end and a_start < hi if seg["a"] else a_start in (lo, hi))]
            # A head learned by place alone (placed_heads) is the head only
            # in its band: engine A's reading, every block of it in the
            # page's learned band (learned_band, an anchor there only in the
            # band).  Engine B's reading has no place on the page, and a
            # chapter's title only engine B read on the chapter's first page
            # is those words too: asked of 裁決, as before.
            if needs_placed_stem(own, learned_merge, job.scan_page) and not (
                    seg["a"] and where and all(k in learned_merge_band() for k in where)):
                return None
            return {"readBy": "A" if seg["a"] else "B", "characters": len(own),
                    "blocks": [layout_index(blocks, k) for k in where],
                    **{key: value for key, value in found.items() if value}}

        def lopsided_furniture(seg: Mapping[str, Any], a_start: int, b_start: int) -> str | None:
            """The engine ("A" or "B") whose text only it read, the surplus of a
            split lopsided replacement (split_lopsided), the book's furniture
            explains: all but INSERTION_LIMIT of its characters are its
            running head, the folio beside it or its recurring n-grams
            (furniture_leftover, where its engine read it) - the test, and the
            allowance, an answer of 裁決's has beside the furniture
            (adjudicate_segment, furnitureSides).  Else None."""
            if not (seg.get("lopsided") or {}).get("surplus") or bool(seg["a"]) == bool(seg["b"]):
                return None
            side = "a" if seg["a"] else "b"
            own = CJK(seg[side])
            chars, at = (stream_a[0], a_start) if side == "a" else (stream_b[0], b_start)
            left = (furniture_leftover(chars, page_furniture, page_heads, (at, at + len(own)))
                    if chars[at:at + len(own)] == own else furniture_leftover(own, page_furniture, page_heads))
            return side.upper() if len(own) > INSERTION_LIMIT and left <= INSERTION_LIMIT else None

        def boundary_of(seg: Mapping[str, Any], a_start: int) -> tuple[int, int | None] | None:
            """The layout blocks either side of text only engine B read where
            engine A's reading passes from one block to the next: (the block
            that ends there, the next block holding text, or None at the
            page's end); None inside a block, at the page's start, or when
            the blocks do not split engine A's stream.  boundary_crop shows
            both edges."""
            if seg["a"] or not seg["b"] or not offsets_valid:
                return None
            if "window" in seg:
                # Engine B's reading of a window of an order-conflict table
                # block (window_merge): its place in engine A's stream is not
                # a place on the page - the block is the window's, and its
                # crop the block's (locate_segment).
                return None
            ending = next((j for j, (end, n) in enumerate(zip(block_ends, block_lengths))
                           if n and end == a_start), None)
            if ending is None:
                return None
            return ending, next((j for j in range(ending + 1, len(blocks)) if block_lengths[j]), None)

        def reduced_by(seg: Mapping[str, Any]) -> dict[str, Any]:
            """The engines' readings before the mark rule took out the characters
            the other engine's marks account for (or the circles it was held
            back from taking, circle_withheld), and where engine B read a
            reading placed at engine A's site (place_moved_once), for the record
            of whatever settles the rest."""
            return {**({"markReduced": seg["markReduced"]} if seg.get("markReduced") else {}),
                    **({"markWithheld": seg["markWithheld"]} if seg.get("markWithheld") else {}),
                    **({"movedFrom": seg["movedFrom"]} if "movedFrom" in seg else {})}

        def site(seg: Mapping[str, Any], a_start: int) -> dict[str, Any]:
            """Where a disagreement is, for decisionChanges: its layout block and the
            engines' readings with engine A's context (mergedOffset is filled in
            once the page's text is settled)."""
            block, _ = locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_start, seg)
            position = next((i for i, b in enumerate(blocks) if b is block), None)
            return {"block": layout_index(blocks, position) if position is not None else None,
                    "contextBefore": seg["before"], "contextAfter": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"]}

        own_reading = {"a": CJK(draft), "b": CJK(draft2 or "")}

        def explain(seg: Mapping[str, Any], a_start: int, b_start: int,
                    kept: Mapping[str, str] | None = None, sides: str = "ab") -> None:
            """Mark what SEG's SIDES leave out - all of it, or what KEPT (the
            segment as a rule reduced it, or the piece written) does not hold -
            as read as no text of the page, in each engine's reading
            (explained, for readings_dropped).  Where the segment's place in
            the stream is not its text in the engine's reading (a window of an
            order-conflict table block runs in engine B's order), its nearest
            copy there; none, nothing is marked."""
            for side, at in (("a", a_start), ("b", b_start)):
                own, reading = CJK(seg[side]), own_reading[side]
                if side not in sides or not own:
                    continue
                if reading[at:at + len(own)] != own:
                    found = [i for i in range(len(reading) - len(own) + 1) if reading.startswith(own, i)]
                    if not found:
                        continue
                    at = min(found, key=lambda i: abs(i - at))
                rest = "" if kept is None else kept[side]
                for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, variant_fold(own), variant_fold(CJK(rest)),
                                                                 autojunk=False).get_opcodes():
                    if tag in ("delete", "replace"):
                        explained[side.upper()].append((at + i1, at + i2))
        circles = set()
        for i, (x, a_at) in enumerate(zip(segments, starts)):
            if x["tag"] != "equal" and is_numeral_conflict(x) and is_lone_circle(
                    x, locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_at, x)[0]):
                circles.add(i)
        # A block engine B reads in another order (nominate_tables) is a
        # table the layout model missed, or a question for the rebuild: the
        # text its disagreements move is settled once, by runs, not one
        # disagreement at a time (settle_by_union) - except a like-for-like
        # substitution, and a run of engine B's not shown to be moved, which
        # go the usual way.
        # Each segment's engine-B side without the characters the bracket rule
        # and the mark rule (D1) take for engine A's printed marks: the
        # witness a nomination is read against (D21) and engine B's reading
        # a rebuilt table may hold (D20).
        b_settled = [engine_b_as_settled(x, a_at, b_at, (stream_a, stream_b) if bracket_rule else None,
                                         (marks_a, marks_b, aliases) if mark_rule else None)
                     for x, a_at, b_at in zip(segments, starts, b_starts)] if table_work else []
        nominations = nominate_tables(blocks, draft2) if table_work and mode == "merge" else []
        if table_work and mode == "merge":
            # Decision D21: the witness is engine B's reading with the glyphs
            # this page's rules settle as marks left out (marks_left_out).
            # A block nominated on engine B's reading as read stays nominated.
            witness, left_out = marks_left_out(segments, starts, b_starts, stream_b[0], b_settled)
            if left_out:
                plain = {n["block"] for n in nominations}
                nominations = sorted(nominations + [
                    dict(n, witness={"marksLeftOut": left_out})
                    for n in nominate_tables(blocks, witness) if n["block"] not in plain],
                    key=lambda n: n["block"])
        union_pieces: dict[int, str] = {}

        def block_union(segs: Sequence[Mapping[str, Any]], seg_starts: Sequence[int],
                        seg_b_starts: Sequence[int], k: int,
                        skip: set[int] | frozenset[int] = frozenset()) -> tuple[list[int], dict[int, str]]:
            """Block K's disagreements the union may settle, and the pieces it
            settles (settle_by_union), for the segments SEGS."""
            lo, hi = (block_ends[k - 1] if k else 0), block_ends[k]
            in_block: list[int] = []
            for i, (x, a_at) in enumerate(zip(segs, seg_starts)):
                if x["tag"] == "equal" or x.get(LOOPED_READING) or "settled" in x:
                    # Engine B's reading of a set-aside looped block is that
                    # block's (quarantine_looped_block), wherever it sits; a
                    # stretch engine A read twice is settled (read_twice).
                    continue
                a_end = a_at + len(x["a"])
                inside = (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi)
                # Numerals and rare codepoints keep their own rules, which hold
                # whatever the order: a rule or a bracket read as 一 is no
                # character anywhere.
                if inside and not (i in skip or (x["tag"] == "replace" and len(CJK(x["a"])) == len(CJK(x["b"])))
                                   or is_numeral_conflict(x) or is_rare_codepoint_conflict(x)):
                    in_block.append(i)
            # Engine B's reading outside the block: an engine-A run it holds
            # is not the text B read in the block (settle_by_union).
            return in_block, settle_by_union(segs, in_block, engine_b_outside(segs, seg_starts, seg_b_starts, lo, hi))

        for nomination in nominations:
            in_block, settled = block_union(segments, starts, b_starts, nomination["block"], circles)
            union_pieces.update(settled)
            nomination["union"] = {"settled": len(settled), "leftToUsualWay": len(in_block) - len(settled)}
        union_of = set(union_pieces)
        # Numeral and rare-codepoint conflicts are read off the crop on every
        # page; keep budget for them so the page's prose cannot use it up.
        protected_left = sum(1 for i, x in enumerate(segments) if x["tag"] != "equal" and i not in circles
                             and i not in union_of and "settled" not in x
                             and (is_numeral_conflict(x) or is_rare_codepoint_conflict(x)))
        # The segments are walked in order to decide each one - the budget
        # depends only on how many were sent to 裁決 before, never on what they
        # answered - and the calls themselves then run together.
        planned: list[tuple[int, dict[str, Any], bytes | None, Path, str]] = []
        # The layout block of each question whose crop was cut where the spot
        # was estimated to be, by slot: shown whole when 裁決 says the crop
        # does not show the spot (widened_answer).
        estimated: dict[int, Mapping[str, Any]] = {}
        # (slot in pieces, decisionChanges entry) for each text change a
        # decision-dependent rule makes; its outcome is filled in once known.
        changes: list[tuple[int, dict[str, Any]]] = []
        # Short one-engine insertions D3 would have asked about with the
        # budget spent (insertion-unjudged).
        unjudged: list[str] = []
        for index, seg in enumerate(segments):
            a_start = starts[index]
            if seg["tag"] == "equal":
                pieces.append(seg["a"])
                continue
            if seg.get(LOOPED_READING):
                # Engine A's reading of this region was a loop and is set
                # aside: engine B's is the only reading, not a disagreement.
                pieces.append(seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": seg["b"], "resolved_by": "engine-B-only-reading-A-looped",
                })
                continue
            if "settled" in seg or index in union_of:
                # window_merge's copies and furniture row, a stretch engine A
                # read twice (read_twice), and a nominated block's order
                # disagreements settled by the union.
                piece, how = seg.get("settled") or (union_pieces[index], "order-disputed-block-union")
                if how == "table-furniture-row":
                    explain(seg, a_start, b_starts[index])
                pieces.append(piece)
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": piece, "resolved_by": how,
                })
                continue
            if index in circles:
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": "circle-not-numeral",
                })
                continue
            surplus = lopsided_furniture(seg, a_start, b_starts[index])
            if surplus:
                # The surplus of a split lopsided replacement that the book's
                # furniture explains (its running head with the folio beside
                # it, its recurring n-grams): left out, as the preference left
                # it out before the split - a running head is not revived as
                # body text.
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"lopsided-surplus-furniture-{surplus}",
                    "lopsided": seg["lopsided"],
                })
                continue
            furniture_found = furniture_insertion(seg, a_start, b_starts[index])
            if furniture_found:
                # Decision D15: the book's furniture only one engine read, left out.
                changes.append((len(pieces), {"decision": "D15", "rule": "furniture-insertion",
                                              **site(seg, a_start), "evidence": furniture_found}))
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"furniture-one-engine-{furniture_found['readBy']}",
                })
                continue
            learned_found = learned_insertion(seg, a_start)
            if learned_found:
                # Switch --learned-heads: the learned running head only one
                # engine read, left out.
                changes.append((len(pieces), {"decision": "learnedHeads", "rule": "learned-head-reading",
                                              **site(seg, a_start), "evidence": learned_found}))
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"learned-head-one-engine-{learned_found['readBy']}",
                })
                continue
            if bracket_rule and is_numeral_conflict(seg):
                settled = bracket_explanation(seg, a_start, b_starts[index], stream_a, stream_b)
                if settled and settled[0] in ("A", "B"):
                    protected_left -= 1
                    explain(seg, a_start, b_starts[index], {"a": settled[1], "b": settled[1]})
                    if figure_rule:
                        after_rules[index] = dict(seg, a=settled[1], b=settled[1])
                        if not seg["a"]:
                            figure_settled[index] = ("marks", bool(CJK(settled[1])))
                    pieces.append(settled[1])
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": settled[1], "resolved_by": f"bracket-not-numeral-{settled[0]}",
                    })
                    continue
                if settled:
                    # Only the numerals left after the brackets go to 裁決.
                    explain(seg, a_start, b_starts[index], settled[1])
                    seg = settled[1]
            if mark_rule:
                marked = mark_explanation(seg, a_start, b_starts[index], marks_a, marks_b, aliases)
                withheld = marked and circle_withheld(
                    seg, a_start, b_starts[index], marked, marks_a, marks_b,
                    locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_start, seg)[0])
                if withheld:
                    # A circle that may be a zero or a placeholder: the segment
                    # goes on whole, as with the rule off, and says why.
                    seg = dict(seg, markWithheld={"marks": [list(pair) for pair in marked[2]], "why": withheld})
                    marked = None
                if marked:
                    changes.append((len(pieces), {
                        "decision": "D1", "rule": "mark-not-character", **site(seg, a_start),
                        # The glyphs taken for marks and how often this book's
                        # drafts showed each at a mark the other engine printed.
                        "evidence": {"marks": [list(pair) for pair in marked[2]], "glyphsOf": marked[3],
                                     "aliases": {char: aliases[marked[3]].get(char, {})
                                                 for char, _ in marked[2]}}}))
                if marked and marked[0] in ("A", "B"):
                    # Engine B read engine A's marks as characters (or the other
                    # way round): both read the same characters otherwise.
                    if is_numeral_conflict(seg) or is_rare_codepoint_conflict(seg):
                        protected_left -= 1
                    explain(seg, a_start, b_starts[index], {"a": marked[1], "b": marked[1]})
                    if figure_rule:
                        after_rules[index] = dict(seg, a=marked[1], b=marked[1])
                        if not seg["a"]:
                            figure_settled[index] = ("marks", bool(CJK(marked[1])))
                    pieces.append(marked[1])
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": marked[1], "resolved_by": f"mark-not-character-{marked[0]}",
                        "marks": [list(pair) for pair in marked[2]],
                    })
                    continue
                if marked:
                    # Only the characters no mark accounts for go on (査丶 against
                    # 查 becomes the variant question 査 against 查).
                    explain(seg, a_start, b_starts[index], marked[1])
                    seg = marked[1]
            if figure_rule:
                after_rules[index] = seg
                standing = figure_at(a_start) if not seg["a"] and seg["b"] else None
                if standing is not None and not figure_stand_ins(
                        seg["b"], standing, figure_glyphs,
                        sum(1 for _, mark in (seg.get("markReduced") or {}).get("marks") or [] if mark in "（）")
                        + sum(len(bracket_kinds(gap)) for gap in
                              stream_b[1][b_starts[index]:b_starts[index] + len(segments[index]["b"]) + 1])):
                    # Something engine B read there is no stand-in: 裁決 is
                    # asked as without the switch, and told nothing new; its
                    # answer may still read the figure (figureAt).
                    seg = dict(seg, figureAt=a_start)
                    standing = None
                if standing is not None:
                    # Switch --keep-arabic-numerals: all engine B read at the
                    # gap where engine A read a confirmed figure (the marks
                    # taken out) is the figure's brackets read as strokes, or
                    # glyphs this book's drafts show engine B writing for
                    # figures or marks, none a numeral (figure_stand_ins):
                    # engine B's reading of the figure, written as nothing; the
                    # figure is written there (arabic_figure_kept).  A question
                    # it would have asked takes the budget it would have taken
                    # (figure_taken), so every other disagreement is decided
                    # as with it off.
                    numeral_or_rare = is_numeral_conflict(seg) or is_rare_codepoint_conflict(seg)
                    if numeral_or_rare:
                        protected_left -= 1
                    if to_judge(seg, a_start, b_starts[index]):
                        budget = arguments.max_adjudications - (0 if numeral_or_rare else max(0, protected_left))
                        if len(planned) + figure_taken < budget:
                            figure_taken += 1
                    explain(seg, a_start, b_starts[index])
                    figure_settled[index] = ("stand-in", True)
                    pieces.append("")
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": "", "resolved_by": "arabic-figure-A", "figure": standing["figure"],
                        **reduced_by(seg),
                    })
                    continue
            numeral = is_numeral_conflict(seg)
            rare = is_rare_codepoint_conflict(seg)
            if numeral or rare:
                protected_left -= 1
            elif variant_segment(seg) is not None:
                # Two forms of one character: which form is printed is ruled per
                # pair for the whole book, never asked of the judge (measured
                # unable to tell them apart).
                piece, how, unruled = resolve_variant(seg, arguments)
                pieces.append(piece)
                variant_unruled.extend(unruled)
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": piece, "resolved_by": how,
                    **reduced_by(seg),
                })
                continue
            # Decision D3: a short one-engine insertion is a question for the
            # crop, not for the preference, which was measured on substitutions
            # only and says nothing about text one engine read and the other
            # did not (short_insertion).
            insertion = prefer in ("A", "B") and not (numeral or rare) and short_insertion(seg, a_start, b_starts[index])
            if prefer in ("A", "B") and not (numeral or rare) and not insertion:
                # Deterministic: the better engine wins. No model call, so this
                # cannot introduce the variant-normalisation errors that every
                # model-chooses design produced (4-7/11 vs the engine's 10/11).
                # Numeral-only disagreements are the exception: see
                # is_numeral_conflict.
                pick = seg["b"] if prefer == "B" else seg["a"]
                pieces.append(pick or seg["a"] or seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": pick or (seg["a"] or seg["b"]),
                    "resolved_by": f"prefer-engine-{prefer}",
                    **reduced_by(seg),
                })
                continue
            budget = arguments.max_adjudications - (0 if (numeral or rare) else max(0, protected_left))
            if len(planned) + figure_taken >= budget:
                # Out of budget: the engine this book's profile measured as the
                # better reader (the stated preference, or the probe's leader
                # when the probe was not confident enough to state one).
                engine = prefer if prefer in ("A", "B") else fallback
                pick = seg["b"] if engine == "B" else seg["a"]
                pieces.append(pick or seg["a"] or seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": pick or (seg["a"] or seg["b"]),
                    "resolved_by": f"prefer-engine-{engine}-adjudication-budget-spent",
                    **reduced_by(seg),
                })
                if insertion:
                    # Kept as it always was, but not settled: a question the
                    # crop was not asked.
                    unjudged.append(f"{seg['before'][-4:]}[{seg['a'] or seg['b']}]{seg['after'][:4]} "
                                    f"(engine {'A' if seg['a'] else 'B'} only)")
                continue
            if insertion:
                changes.append((len(pieces), {
                    "decision": "D3", "rule": "insertion-to-crop", **site(seg, a_start),
                    "evidence": {"characters": len(CJK(seg["a"] or seg["b"])),
                                 "readBy": "A" if seg["a"] else "B"}}))
                seg = dict(seg, insertionQuestion=True)
            block, offset = locate_segment(blocks, block_ends, block_lengths,
                                           offsets_valid, a_start, seg)
            crop, edge = None, boundary_of(seg, a_start)
            crop_kind = "boundary"
            if edge is not None:
                # Between two layout blocks (or at the page's end): both
                # edges, not the next block's head alone (boundary_crop).
                crop, setting = boundary_crop(job.render, blocks[edge[0]],
                                              blocks[edge[1]] if edge[1] is not None else None,
                                              len(CJK(seg["b"])) or 1)
                if crop:
                    shown = {"blocks": [layout_index(blocks, j) if j is not None else None for j in edge],
                             "set": setting}
                    seg = dict(seg, boundaryCrop=shown)
                    if insertion:
                        changes[-1][1]["evidence"]["boundaryCrop"] = shown
            if not crop:
                # A spot estimated at a column's end is shown with the
                # neighbouring column's end too (crop_glyph's ACROSS), and
                # 裁決 is told the crop has parts (COLUMN_BREAK_NOTE).
                across: dict[str, Any] = {}
                crop, crop_kind = segment_crop_kind(job.render, render_bytes, block, offset, seg, across=across)
                if across.get("parts", 1) > 1:
                    seg = dict(seg, columnBreakCrop=dict(across))
                elif crop and crop_kind in WHOLE_CROP_NOTES:
                    seg = dict(seg, wholeCrop=crop_kind)
            if crop_kind in ("glyph", "band") and block is not None and offset is not None:
                # A crop cut where the spot is estimated to be (crop_glyph,
                # crop_band) must be shown to hold it before 裁決's 無 deletes
                # what an engine read there (confirm_nothing): how much of the
                # context the spot's own block prints either side of it.
                in_block = len(CJK(str(block.get("text") or "")))
                seg = dict(seg, coverage={"kind": crop_kind, "beforeRoom": max(0, offset),
                                          "afterRoom": max(0, in_block - offset - len(seg["a"]))})
            if (crop and crop_kind != "page" and block is not None and offset is not None and seg["a"]
                    and offset + len(seg["a"]) <= len(CJK(str(block.get("text") or "")))):
                # The crop was cut from the one layout block that holds all of
                # engine A's reading here (D16's answer that copies the neighbour).
                seg = dict(seg, cropHoldsA=True)
            # Keep the exact pixels the adjudicator saw next to the sealed page,
            # so a verdict can be audited against its evidence.
            crop_path = job.out_json.with_name(
                f"{job.out_json.stem}-adjudication-{len(planned) + 1:02d}.png")
            if crop:
                crop_path.write_bytes(crop)
            # A reading the book's furniture explains - its running head with
            # the folio's figures beside it, however many, its recurring
            # n-grams, as its engine read them around the reading, and at most
            # INSERTION_LIMIT characters more (a figure misread) - may be
            # answered away; any other, read by one engine at more characters
            # than 裁決 writes, is kept (adjudicate_segment, deletion-refused).
            # Each reading is taken where its engine read it, when it still
            # stands there (the mark and bracket rules may have reduced it).
            read_at = {"a": (stream_a[0], a_start), "b": (stream_b[0], b_starts[index])}

            def leftover(side: str) -> int:
                chars, at = read_at[side]
                own = CJK(seg[side])
                if chars[at:at + len(own)] == own:
                    return furniture_leftover(chars, page_furniture, page_heads, (at, at + len(own)))
                return furniture_leftover(own, page_furniture, page_heads)

            seg = dict(seg, furnitureSides=[side for side in ("a", "b")
                                            if len(CJK(seg[side])) > ANSWER_DELETION_LIMIT
                                            and leftover(side) <= INSERTION_LIMIT],
                       # Engine B's reply was cut off and its reading merged up
                       # to the cut: the guard keeps none of its reading alone
                       # over 裁決's answer (adjudicate_segment, cutOffSides).
                       cutOffSides=["b"] if b_cut_off is not None else [],
                       # Where the spot is, for a D16 change if the answer is refused,
                       # and where each engine read it (explain).
                       answerSite=site(seg, a_start), streamAt=(a_start, b_starts[index]))
            pieces.append("")          # filled in below, in this order
            if crop_kind in ("glyph", "band") and block is not None:
                estimated[len(pieces) - 1] = block
            planned.append((len(pieces) - 1, seg, crop, crop_path, prefer if prefer in ("A", "B") else fallback))

        # Each question's place (call_site: its spot, its crop, and where it
        # stands in the merged text), for the loop detector's entry of a call
        # on it; filled in once known.
        spot_sites: dict[int, dict[str, Any]] = {slot: {} for slot, *_ in planned}

        def adjudicate(item: tuple[int, dict[str, Any], bytes | None, Path, str]):
            slot, seg, crop, crop_path, engine = item
            with call_site(spot_sites[slot]):
                simplified = getattr(arguments, "simplified_script", False)
                answer = adjudicate_segment(client, crop, seg, arguments.max_tokens, fallback_engine=engine,
                                            note=(BOUNDARY_NOTES.get((seg.get("boundaryCrop") or {}).get("set"), "")
                                                  or (COLUMN_BREAK_NOTE if seg.get("columnBreakCrop") else "")
                                                  or WHOLE_CROP_NOTES.get(seg.get("wholeCrop"), "")),
                                            simplified=simplified)
                # 睇唔到 on a crop cut where the spot was estimated: once more,
                # on the whole block, which needs no blind read to hold it.
                answer, wider = widened_answer(client, seg, answer, job.render, estimated.get(slot),
                                               arguments.max_tokens, engine, simplified)
                if wider:
                    # The pixels of the second look, beside the first's.
                    wider_path = crop_path.with_name(crop_path.name.replace("-adjudication-", "-adjudication-wider-"))
                    wider_path.write_bytes(wider)
                    answer[1]["widened"]["crop"] = wider_path.name
                    return answer
                return confirm_nothing(client, crop, seg, answer, arguments.max_tokens, engine)

        # D3 questions whose answer wrote less than the engine read (insertion-partial),
        # and answers that would have left out a longer reading (deletion-refused).
        partial_insertions: list[str] = []
        refused_deletions: list[str] = []
        # D16's refusals, (slot, question, sealed verdict, crop, fallback
        # engine), settled once every answer is in (reading_nearby); and 無
        # answers not shown to hold the spot (confirm_nothing).
        refusals: list[tuple[int, dict[str, Any], dict[str, Any], bytes | None, str]] = []
        unconfirmed: list[str] = []
        # Answers --neighbour-guard did not write: copies of the text beside their spot.
        neighbour_copies: list[str] = []
        beside_copies: list[str] = []
        # Answers in traditional script written in engine A's form against engine B's
        # other character, or in the one form neither engine read (converted_against_b).
        script_converted: list[str] = []
        if planned:
            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                answers = in_context(pool, adjudicate, planned)
            for (slot, asked, crop, crop_path, engine), (piece, record, m) in zip(planned, answers):
                add(m)
                pieces[slot] = piece
                record["crop"] = crop_path.name if crop else None
                spot_sites[slot].update(asked["answerSite"], crop=record["crop"])
                if asked.get("bracketReduced"):
                    # The engines' readings before the bracket rule took out
                    # the numerals engine A's brackets account for.
                    record["bracketReduced"] = asked["bracketReduced"]
                record.update(reduced_by(asked))
                if asked.get("insertionQuestion"):
                    # Asked because of decision D3 (short_insertion).
                    record["askedFor"] = "D3"
                if record.get("resolved_from") == "adjudicator-partial":
                    # 裁決 wrote less than the engine read: the engine's
                    # reading stands (adjudicate_segment), the count is a
                    # person's to check against the image.
                    partial_insertions.append(
                        f"{asked['before'][-4:]}[{asked['a'] or asked['b']}]{asked['after'][:4]} "
                        f"(engine {'A' if asked['a'] else 'B'} only; 裁決 wrote "
                        f"「{record.get('answeredTraditional', record['answered'])}」)")
                if record.get("resolved_from") == "adjudicator-deletion-refused":
                    # 裁決's answer would have left out more than a couple of
                    # characters one engine read: that reading stands (decision
                    # D16; with the switch off, the answer as written)
                    # - unless the merged text holds it nearby, which is looked
                    # at once every answer is in (below).
                    refusals.append((slot, asked, record, crop, engine))
                if record.get("cutOffReadingAnswered") and record.get("resolved_from") in (
                        "adjudicator", "adjudicator-empty"):
                    # D16 kept no reading here only because the engine that
                    # read more than the answer was cut off (adjudicate_segment):
                    # the answer is written, and the change is sealed.
                    cut_side = record["cutOffReadingAnswered"].lower()
                    changes.append((slot, {
                        "decision": "D16", "rule": "answer-accepted-cut-off-reading", **asked["answerSite"],
                        "evidence": {"cutOffEngine": cut_side.upper(),
                                     "readingCharacters": len(CJK(asked[cut_side])),
                                     "answeredCharacters": len(CJK(record["resolved"])),
                                     "limit": ANSWER_DELETION_LIMIT,
                                     # A whole engine's reading here that shares
                                     # no character with the answer.
                                     **({"unsharedWholeReadings": record["unsharedWholeReadings"]}
                                        if record.get("unsharedWholeReadings") else {})}}))
                if record.get("resolved_from") == "adjudicator-empty-unconfirmed":
                    # 裁決 answered 無 on a crop not shown to hold the spot
                    # (confirm_nothing): the reading stands, and a person looks.
                    cover = record["coverageRead"]
                    changes.append((slot, {
                        "decision": "textLoss", "rule": "nothing-answer-unconfirmed", **asked["answerSite"],
                        "evidence": {key: cover[key] for key in ("crop", "found", "reading")}}))
                    shown = ("the crop does not show the spot" if cover["found"] != "placed"
                             else "read there blind: " + cover["reading"])
                    unconfirmed.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} ({shown}; kept {record['resolved'][:8]})")
                if asked.get("boundaryCrop"):
                    # Shown both blocks' edges (boundary_crop): which, and how set.
                    record["boundaryCrop"] = asked["boundaryCrop"]
                read_figure = (figure_verdict(record, figure_at(asked["figureAt"]))
                               if asked.get("figureAt") is not None and figure_at(asked["figureAt"]) else None)
                if read_figure:
                    # Switch --keep-arabic-numerals: 裁決 read the figure engine
                    # A read there, or that no character is printed there -
                    # engine B's glyphs are its reading of the figure, written
                    # as nothing, and the figure is written (arabic_figure_kept).
                    figure_settled[slot] = ("verdict", bool(CJK(piece)))
                    explain(asked, *asked["streamAt"])
                    pieces[slot] = ""
                    record["figureKept"] = {"figure": figure_at(asked["figureAt"])["figure"], "read": read_figure,
                                            "resolvedBefore": record["resolved"]}
                    record["resolved"] = ""
                adjudications.append(record)
            # D16 as the user ruled on 2026-09-27: 裁決's answer is refused only
            # where what it leaves out of the reading is not already in the
            # merged text nearby (reading_nearby) - measured, 10 of the 26
            # refusals of four runs kept a second copy of text read beside the
            # spot in another order (a table header read row by row; figures
            # read again under the next row, which stay refused).
            for slot, asked, record, crop, engine in refusals:
                kept = "A" if record["resolved"] == asked["a"] else "B"
                # Nor where engine B read nothing there, and 裁決, shown the
                # block engine A's reading stands in, answered the text beside
                # the spot (neighbour_copy, the neighbour guard's test): two
                # readings of three find no such text, and 裁決 read the
                # neighbour, so engine B's nothing is written (a person checks
                # it: neighbour-copy, and reading-dropped unless the page holds
                # the reading as a second copy).  Measured over the 220
                # refusals of six sealed books (2026-10-24): 4 so, and none
                # printed - a line engine A read in a margin strip of
                # bleed-through (#34), and three lines engine A read twice,
                # the second copy misread.  Every refusal of a reading only
                # engine A made that kept printed text answered 無 or something
                # not beside the spot (most often a piece of the reading; once
                # a misread of it, 岡 for a note's 同), or was shown another
                # block than the reading's (a heading read across two blocks).
                copied = (neighbour_copy(record["answered"], asked["a"], asked["b"],
                                         CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])),
                                         asked["before"], asked["after"],
                                         simplified=getattr(arguments, "simplified_script", False))
                          if getattr(arguments, "neighbour_guard", False) and not CJK(asked["b"])
                          and asked.get("cropHoldsA") else None)
                if copied:
                    answered = record["answered"]
                    pieces[slot] = asked["b"]
                    copied.update(keptEngine="B", keptBy="engine-B-read-nothing")
                    record.update(resolved=asked["b"], resolved_from="adjudicator-neighbour-copy",
                                  winner="undecided", neighbourCopy=copied)
                    changes.append((slot, {
                        "decision": "D16", "rule": "answer-copies-neighbour", **asked["answerSite"],
                        "evidence": {**copied, "readingCharacters": len(CJK(asked["a"])),
                                     "answeredCharacters": len(CJK(answered)), "window": NEIGHBOUR_WINDOW}}))
                    neighbour_copies.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B -]{asked['after'][:4]} "
                        f"(裁決 wrote 「{answered}」, the text {copied['distance']} character(s) {copied['side']} "
                        f"the spot; engine B read nothing there: engine A's 「{asked['a'][:8]}」 not written)")
                    continue
                reading = CJK(asked["a"] if kept == "A" else asked["b"])
                left_out = left_out_of(reading, CJK(record["answered"]))
                near = reading_nearby(left_out, CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])))
                if near:
                    near["leftOut"] = left_out
                evidence = {"keptEngine": kept, "readingCharacters": len(reading),
                            "answeredCharacters": len(CJK(record["answered"])), "limit": ANSWER_DELETION_LIMIT,
                            "furnitureSides": [side.upper() for side in asked.get("furnitureSides") or []]}
                if near and not CJK(record["answered"]) and asked.get("coverage"):
                    # A 無 deletes only once its crop is shown to hold the spot
                    # (confirm_nothing), here as anywhere: a reading nearby says
                    # the page has the text once more, not that the crop shows
                    # the spot empty.  Not shown, the refusal stands.
                    with call_site(spot_sites[slot]):
                        _, checked, m = confirm_nothing(
                            client, crop, asked,
                            (record["answered"], dict(record, resolved_from="adjudicator-empty"), {}),
                            arguments.max_tokens, engine)
                    add(m)
                    if "coverageRead" in checked:
                        record["coverageRead"] = checked["coverageRead"]
                        evidence["coverageRead"] = {key: checked["coverageRead"][key]
                                                    for key in ("crop", "found", "reading", "confirmed")}
                        if not checked["coverageRead"]["confirmed"]:
                            near = None
                if near:
                    # The answer stands: the reading is written nearby already.
                    answered = record.pop("answered")
                    pieces[slot] = answered
                    record.update(resolved=answered, resolved_from="adjudicator" if CJK(answered)
                                  else "adjudicator-empty", readingNearby=near)
                    record["winner"] = segment_winner(asked, answered, record["resolved_from"])
                    # What it leaves out of the kept reading is written nearby: no
                    # dropped reading (readings_dropped).
                    explain(asked, *asked["streamAt"], {"a": answered, "b": answered}, kept.lower())
                    changes.append((slot, {
                        "decision": "D16", "rule": "answer-accepted-reading-nearby", **asked["answerSite"],
                        "evidence": {**evidence, "nearby": near}}))
                    continue
                changes.append((slot, {
                    "decision": "D16", "rule": "answer-deletion-guard", **asked["answerSite"],
                    "evidence": evidence}))
                refused_deletions.append(
                    f"{asked['before'][-4:]}[A {asked['a'][:16] or '-'} / B {asked['b'][:16] or '-'}]"
                    f"{asked['after'][:4]} (裁決 wrote "
                    + (f"「{record.get('answeredTraditional', record['answered'])}」" if record["answered"]
                       else "that nothing is printed")
                    + ("; a blind read of the crop did not show the spot empty" if "coverageRead" in record else "")
                    + f"; kept {record['resolved'][:16]})")
            if getattr(arguments, "neighbour_guard", False):
                # Switch --neighbour-guard: an answer, neither engine's reading,
                # that is the text the merged text has right beside its spot, as
                # the context 裁決 was shown has it there, and neither engine
                # read at the spot (neighbour_copy), is not written: 裁決 read
                # the neighbour.  Once every answer is in, so the text beside a
                # spot is what the page writes there; in the page's order, each
                # seeing the ones before it settled.  Not an answer D16 let go
                # because its left-out reading stands nearby (readingNearby),
                # which that rule settled.
                for (slot, asked, _, _, engine), (_, record, _) in zip(planned, answers):
                    if record.get("resolved_from") != "adjudicator" or "readingNearby" in record:
                        continue
                    copied = neighbour_copy(pieces[slot], asked["a"], asked["b"],
                                            CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])),
                                            asked["before"], asked["after"],
                                            simplified=getattr(arguments, "simplified_script", False))
                    if copied is None:
                        continue
                    # What stands instead: on a D3 question (one engine read a
                    # short run the other did not) the reading of the engine
                    # that read it, as D3 keeps it where 裁決 wrote less of it
                    # (insertion-partial); otherwise the reading of the engine
                    # the page falls back on (its preference, else the probe's
                    # leader), as that engine read it - nothing where it read
                    # nothing.  A person checks it (neighbour-copy).
                    if asked.get("insertionQuestion"):
                        side, kept_by = ("a" if asked["a"] else "b"), "D3-reading"
                    else:
                        side, kept_by = ("b" if engine == "B" else "a"), "fallback-engine"
                    answered = pieces[slot]
                    pieces[slot] = asked[side]
                    copied.update(keptEngine=side.upper(), keptBy=kept_by)
                    record.update(resolved=asked[side], resolved_from="adjudicator-neighbour-copy",
                                  winner="undecided", answered=answered, neighbourCopy=copied)
                    changes.append((slot, {
                        "decision": "neighbourGuard", "rule": "neighbour-copy", **asked["answerSite"],
                        "evidence": {**copied, "window": NEIGHBOUR_WINDOW}}))
                    neighbour_copies.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} (裁決 wrote 「{answered}」, the text {copied['distance']} "
                        f"character(s) {copied['side']} the spot; kept engine {side.upper()}'s "
                        f"「{asked[side][:8]}」)")
        if planned:
            # A reading only one engine made, of some length, that 裁決 confirmed
            # whole and that is exactly the text standing right beside the spot
            # in the merged text (the engine read a line twice; 裁決, shown the
            # spot between two blocks, answered the next line): written, since
            # the answer is an engine's reading, but a doubt - the page may hold
            # the line twice.  Not where a side stands in a furniture band: a
            # chapter title printed in the running head and as the heading is
            # two copies in print, and the furniture one goes later (measured on
            # the modern book: 3 such titles, none a copy; one body line read
            # twice by engine B on scan 33, written twice in one run of two).
            for (slot, asked, _, _, engine), (_, record, _) in zip(planned, answers):
                if record.get("resolved_from") != "adjudicator" or asked.get("furnitureSides"):
                    continue
                readings = [CJK(asked["a"]), CJK(asked["b"])]
                written = CJK(pieces[slot])
                if sum(1 for r in readings if r) != 1 or len(written) < BESIDE_COPY_MIN or written not in readings:
                    continue
                before, after = CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:]))
                side = ("after" if after.startswith(written) else "before" if before.endswith(written) else None)
                if side is None:
                    continue
                record["copyBeside"] = side
                beside_copies.append(f"{asked['before'][-4:]}[{'A' if readings[0] else 'B'} {written[:10]}…]{asked['after'][:4]} "
                                     f"(the same {len(written)} characters stand right {side} the spot)")
            # In a book printed in simplified script, an answer in traditional
            # script written in engine A's form where engine B read another
            # character, or in the table's one form, which neither engine read
            # (converted_against_b): a person looks.
            for (slot, asked, _, _, _), (_, record, _) in zip(planned, answers):
                given = record.get("answeredTraditional")
                if (given and record.get("resolved_from") == "adjudicator"
                        and converted_against_b(given, CJK(asked["a"]), CJK(asked["b"]))):
                    script_converted.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} (裁決 wrote 「{given}」; written {pieces[slot][:8]})")
        # Decision D24: a character only engine A read, which the page's
        # verdicts judged a misread of engine A's at every site they were
        # asked about (judged_misreads), is asked about too, on its own crop,
        # with the verdicts' reading as the second.  Measured on a textbook
        # table's continuation page: 裁決 read one character of engine A's as
        # another at all three sites engine B had read (as a rare codepoint),
        # and a label cell holding it, in a band engine B never read, kept
        # engine A's misread - nothing was asked.
        # The segments as the table checks count what engine A read there
        # (agreed_block_text): a character D24 asked about stands as written.
        agreed_segments = segments
        if planned:
            misread = judged_misreads([(asked, record) for (_, asked, _, _, _), (_, record, _)
                                       in zip(planned, answers)])
            spots = propagation_spots(segments, pieces, union_of, {slot for slot, *_ in planned}, misread)
            spots = spots[:max(0, arguments.max_adjudications - len(planned) - figure_taken)]

            # Each question's place (call_site), as its D24 entry gives it, for
            # the loop detector's entry of a call on it.
            propagated_sites: dict[tuple[int, int], dict[str, Any]] = {(i, p): {} for i, p, _ in spots}

            def propagate(spot: tuple[int, int, dict[str, Any]]):
                i, p, sub = spot
                block, offset = locate_segment(blocks, block_ends, block_lengths, offsets_valid, starts[i] + p, sub)
                crop = segment_crop(job.render, render_bytes, block, offset, sub)
                with call_site(propagated_sites[(i, p)]):
                    return crop, adjudicate_segment(client, crop, sub, arguments.max_tokens, fallback_engine="A",
                                                    note=propagation_note(sub["a"], sub["b"]),
                                                    simplified=getattr(arguments, "simplified_script", False))

            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                propagated = in_context(pool, propagate, spots)
            for number, ((i, p, sub), (crop, (piece, record, m))) in enumerate(zip(spots, propagated), 1):
                add(m)
                # One character for one: never a deletion or an insertion of
                # text engine A read (an answer that is neither is not written).
                written = piece if len(CJK(piece)) == 1 else sub["a"]
                pieces[i] = pieces[i][:p] + written + pieces[i][p + 1:]
                if written != sub["a"]:
                    # Engine A's reading there is not required in a table
                    # (engine B read none of it): what is written there is.
                    if agreed_segments is segments:
                        agreed_segments = list(segments)
                    agreed_segments[i] = dict(agreed_segments[i], a=pieces[i])
                crop_path = job.out_json.with_name(
                    f"{job.out_json.stem}-adjudication-{len(planned) + number:02d}.png")
                if crop:
                    crop_path.write_bytes(crop)
                # Not an engine disagreement: 乙 is no engine's reading, so the
                # verdict says nothing about which engine reads better
                # (engineTally, the drift audit).
                record.update({"crop": crop_path.name if crop else None, "askedFor": "D24",
                               "trigger": "verdict-propagation",
                               "propagatedFrom": {"pair": [sub["a"], sub["b"]], "sites": misread[sub["a"]][1]}})
                adjudications.append(record)
                block, _ = locate_segment(blocks, block_ends, block_lengths, offsets_valid, starts[i] + p, sub)
                position = next((j for j, b in enumerate(blocks) if b is block), None)
                decision_changes.append({
                    "decision": "D24", "rule": "verdict-propagated",
                    "block": layout_index(blocks, position) if position is not None else None,
                    "contextBefore": sub["before"], "contextAfter": sub["after"],
                    "engineA": sub["a"], "engineB": "", "after": written,
                    "mergedOffset": len(CJK("".join(pieces[:i]))) + p,
                    "evidence": {"pair": [sub["a"], sub["b"]], "judgedSites": misread[sub["a"]][1],
                                 "resolved": piece}})
                propagated_sites[(i, p)].update({key: decision_changes[-1][key] for key in SITE_PLACE_KEYS},
                                                crop=record["crop"])
        if unjudged:
            doubts.append({"kind": "insertion-unjudged", "detail": (
                f"ONE-ENGINE INSERTION KEPT UNJUDGED: 裁決 budget spent; {len(unjudged)} short insertion(s) "
                "only one engine read were kept by the preference as before: " + "; ".join(unjudged[:6])
                + " - check them against the image")})
        if refused_deletions:
            doubts.append({"kind": "deletion-refused", "detail": (
                f"READING KEPT OVER A SHORTER ANSWER: at {len(refused_deletions)} spot(s) 裁決's answer (neither "
                f"engine's reading, or nothing printed) would have left out more than {ANSWER_DELETION_LIMIT} "
                "characters one engine read; that reading is kept: " + "; ".join(refused_deletions[:6])
                + " - check the text against the image")})
        if unconfirmed:
            doubts.append({"kind": "nothing-unconfirmed", "detail": (
                f"READING KEPT OVER AN UNCONFIRMED 無: at {len(unconfirmed)} spot(s) 裁決 answered that nothing is "
                "printed where one engine read text, on a crop cut where the spot was estimated to be, and a "
                "blind reading of that crop did not show the spot empty between the characters around it; "
                "the reading is kept: " + "; ".join(unconfirmed[:6]) + " - check it against the image")})
        if partial_insertions:
            doubts.append({"kind": "insertion-partial", "detail": (
                f"ONE-ENGINE INSERTION KEPT WHOLE: 裁決 said text is printed at {len(partial_insertions)} "
                "spot(s) only one engine read, but wrote fewer characters than that engine read; the "
                "engine's reading is kept: " + "; ".join(partial_insertions[:6])
                + " - count the characters against the image")})
        if beside_copies:
            doubts.append({"kind": "neighbour-copy", "detail": (
                f"ONE-ENGINE READING WRITTEN BESIDE ITS OWN COPY: at {len(beside_copies)} spot(s) 裁決 confirmed a "
                "reading only one engine made, and the same characters stand right beside the spot already; the "
                "engine may have read the line twice: " + "; ".join(beside_copies[:6])
                + " - check against the image whether the line is printed once or twice")})
        if script_converted:
            doubts.append({"kind": "answer-script-converted", "detail": (
                f"ANSWER IN TRADITIONAL SCRIPT WRITTEN IN SIMPLIFIED SCRIPT: at {len(script_converted)} spot(s) "
                "裁決's answer, in traditional script in a book printed in simplified script, is written in the "
                "form engine A read where engine B read another character, or in OpenCC's one simplified form, "
                "which neither engine read: " + "; ".join(script_converted[:6])
                + " - check the character against the image")})
        if neighbour_copies:
            doubts.append({"kind": "neighbour-copy", "detail": (
                f"ANSWER COPIED FROM BESIDE THE SPOT: at {len(neighbour_copies)} spot(s) 裁決's answer (neither "
                f"engine's reading) is the text the page has within {NEIGHBOUR_WINDOW} character(s) of the spot, "
                "which neither engine read at the spot; it is not written (--neighbour-guard) and an engine's "
                "reading is kept: " + "; ".join(neighbour_copies[:6])
                + " - check the spot against the image (a character printed twice there is written once)")})
        # Each decision's text change, now that every piece is settled: what it
        # wrote there and where that is in the merged text.
        for slot, change in changes:
            change["after"] = pieces[slot]
            change["mergedOffset"] = len(CJK("".join(pieces[:slot])))
            decision_changes.append(change)
        if loop_cuts:
            # And each question's, for the loop detector's entries (spot_sites).
            for slot, place in spot_sites.items():
                place["mergedOffset"] = len(CJK("".join(pieces[:slot])))
        # Each lopsided replacement split (split_lopsided): what the merge wrote
        # for its pieces.
        for split in lopsided:
            slots = [i for i, x in enumerate(segments) if (x.get("lopsided") or {}).get("id") == split["id"]]
            written = "".join(pieces[i] for i in slots)
            split.update(written=written, pieces=len(slots))
            if slots:
                decision_changes.append({
                    "decision": "textLoss", "rule": "lopsided-split", **site(split["segment"], split["aStart"]),
                    "after": written, "mergedOffset": len(CJK("".join(pieces[:slots[0]]))),
                    "evidence": {"how": split["how"],
                                 "pieces": [{"engineA": segments[i]["a"], "engineB": segments[i]["b"],
                                             "written": pieces[i]} for i in slots]}})
        # Each run placed once (D2): at engine B's site the merge now writes only
        # what engine A never read, and the rest stands at engine A's site.
        # Both sites are sealed: engine A's site holds what the two copies
        # share and engine B's characters between them that face nothing of
        # engine A's, which the read site's entry alone did not show.
        draft_chars = "".join(x["a"] for x in segments)

        def merged_offset(gap: int) -> int:
            """Where engine A's position GAP falls in the merged text: the start of
            the first segment at or after it, or the text's end (a run engine B
            read at the page's end, after all of engine A's text)."""
            return len(CJK("".join(pieces[:bisect.bisect_left(starts, gap)])))

        def within(tag_a: str, at: int, lo: int, hi: int) -> bool:
            """Whether a segment of engine-A text TAG_A starting at AT belongs to
            engine A's stretch [LO, HI): its text overlaps it, or it is text
            only engine B read strictly inside it."""
            return (at < hi and at + len(tag_a) > lo) if tag_a else lo < at < hi

        aligned_at = [0] + list(itertools.accumulate(len(x["a"]) for x in aligned))[:-1]

        def site_span(move: Mapping[str, Any]) -> tuple[int, int]:
            """Engine A's stretch at a moved run's site, widened to the whole
            aligned segments the placement touched: what engine B read there
            beside the run, read otherwise than engine A, is in the entry."""
            lo, hi = move["placedAt"]["engineA"]
            touched = [k for k, x in enumerate(aligned) if x["a"] and within(x["a"], aligned_at[k], lo, hi)]
            return (min([lo] + [aligned_at[k] for k in touched]),
                    max([hi] + [aligned_at[k] + len(aligned[k]["a"]) for k in touched]))

        def placed_site(lo: int, hi: int, placed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
            """The decisionChanges entry for engine A's stretch [LO, HI) where the
            runs PLACED were put: what is written there now, engine B's
            characters there that face nothing of engine A's included."""
            now = [i for i, x in enumerate(segments) if within(x["a"], starts[i], lo, hi)]
            there = {"a": draft_chars[lo:hi], "b": "".join(segments[i]["b"] for i in now),
                     "before": draft_chars[max(0, lo - 8):lo], "after": draft_chars[hi:hi + 8]}
            return {
                "decision": "D2", "rule": "moved-placed-once", "moveSite": "placed",
                **site(there, lo), "mergedOffset": merged_offset(lo),
                "after": "".join(pieces[i] for i in now),
                "evidence": {"moves": [{"engineBOffset": move["engineBOffset"], "readAt": move["readAt"],
                                        "matched": move["matched"]} for move in placed]}}

        for move in moves:
            gap = move["readAt"]["engineAGap"]
            left = "".join(pieces[i] for i, x in enumerate(segments)
                           if x.get("movedRemainderOf") == move["engineBOffset"])
            read = {"tag": "insert", "a": "", "b": move["text"], "before": draft_chars[max(0, gap - 8):gap],
                    "after": draft_chars[gap:gap + 8]}
            disagreements.append({
                "context_before": read["before"], "context_after": read["after"],
                "engineA": "", "engineB": move["text"], "resolved": left, "resolved_by": "moved-kept-once",
                "placedAt": move["placedAt"]["context"],
            })
            decision_changes.append({
                "decision": "D2", "rule": "moved-placed-once", "moveSite": "read",
                **site(read, gap), "mergedOffset": merged_offset(gap), "after": left, "evidence": {"placedAt": move["placedAt"], "matched": move["matched"],
                                            "engineBOffset": move["engineBOffset"]}})
        # Engine A's sites, one entry each: runs placed in one stretch (a label
        # and the value engine B read after it, both in one of engine A's
        # segments) share it, so no character is counted twice.
        placements = sorted((site_span(move), number) for number, move in enumerate(moves))
        groups: list[list[Any]] = []
        for (lo, hi), number in placements:
            if groups and lo < groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], hi)
                groups[-1][2].append(moves[number])
            else:
                groups.append([lo, hi, [moves[number]]])
        decision_changes.extend(placed_site(lo, hi, placed) for lo, hi, placed in groups)
        if figure_rule:
            figures_kept, arabic_figures_seen = arabic_figure_kept(
                page_figures, segments, starts, pieces, after_rules, figure_settled, blocks)
            figure_why = {g: entry["why"] for g, entry in zip(sorted(page_figures), arabic_figures_seen)
                          if not entry["kept"]}
        if moves:
            doubts.append({"kind": "moved-placed-once", "detail": (
                "TEXT READ IN ANOTHER ORDER PLACED ONCE: " + " | ".join(
                    f"{move['text'][:12]}{'…' if len(move['text']) > 12 else ''} (engine B read it at "
                    f"…{move['readAt']['context']}…, engine A at …{move['placedAt']['context']}…; kept at "
                    f"engine A's{', what engine A never read left where engine B read it: ' + move['leftAtSite'] if move['leftAtSite'] else ''})"
                    for move in moves[:4])
                + " - check the order against the image")})
        resolved = "".join(pieces)
        if not resolved.strip():
            raise ProofreadError("merge produced no text")
        # Whatever rule dropped it - the preference, 裁決's answer, a
        # furniture test, the order conflict's choice of one reading - more
        # than ANSWER_DELETION_LIMIT characters one engine read that the
        # merged text holds nowhere leave the page unclean
        # (readings_dropped; the user's fix of 2026-09-27).
        readings_lost = readings_dropped({"A": draft, "B": draft2 or ""}, resolved, page_furniture,
                                         page_heads, {engine: set(aliases.get(engine) or {})
                                                      for engine in ("A", "B")}, explained)
        for drop in readings_lost:
            key, asked_key = (("engineA", "draft_reading") if drop["engine"] == "A"
                              else ("engineB", "writer_reading"))
            grams = {drop["text"][i:i + DROP_GRAM] for i in range(len(drop["text"]) - DROP_GRAM + 1)}
            how = ([d.get("resolved_by") for d in disagreements
                    if any(g in variant_fold(CJK(d.get(key) or "")) for g in map(variant_fold, grams))]
                   + [a.get("resolved_from") for a in adjudications
                      if any(g in variant_fold(CJK(a.get(asked_key) or "")) for g in map(variant_fold, grams))])
            drop["by"] = sorted(set(filter(None, how))) or [mode]
        if readings_lost:
            doubts.append({"kind": "reading-dropped", "detail": (
                f"READING DROPPED AT MERGE: at {len(readings_lost)} spot(s) more than {ANSWER_DELETION_LIMIT} "
                "characters one engine read are nowhere in the merged text: " + "; ".join(
                    f"engine {drop['engine']} 「{drop['text'][:20]}{'…' if len(drop['text']) > 20 else ''}」 "
                    f"({drop['characters']}) at …{drop['contextBefore'][-4:]}|{drop['contextAfter'][:4]}… "
                    f"({', '.join(drop['by'])})" for drop in readings_lost[:6])
                + " - check them against the image")})
        # Stage A - punctuation per layout block, against the block's own crop.
        # A whole-page pass on a long page was measured to land anywhere between
        # every printed circle and none (or outright rejection) across runs of
        # the same input; a block is short enough to be read, and its crop shows
        # exactly the text being punctuated.  Characters are checked as an exact
        # sequence here - a punctuator has no business moving anything.
        # An order conflict that keeps engine A's own reading is engine A's
        # stream whole, so its blocks split it exactly: such a page went whole
        # only because this was asked of merge mode alone.  Measured on the
        # corpus: 77 of 94 order-conflict pages keep engine A's reading and
        # block_spans splits every one; four of them replayed offline went from
        # no block pass to 4-20 each.  A page that takes engine B's reading
        # has no layout for it and still goes whole.  Its one segment is engine
        # A's text marked equal, so the table checks that count the characters
        # both engines read (agreed_block_text) count engine A's there: the page
        # has no other reading to lose.  Table recovery takes these spans too
        # (a table's edge text, table_edges).
        spans = (block_spans(blocks, segments, pieces, draft)
                 if mode == "merge" or order_text == "A" else None)

        # The table's edge text: what a table block's span holds before or
        # after the cells of the table that stands for it is written beside
        # the table, not dropped with the block's text (restate_table,
        # table_edges).  By block position, the stretches of the merged text
        # before and after each such table (recover_tables' `edges`; for a
        # rebuilt table, what of its region's span it does not hold at either
        # end).
        table_edge: dict[int, dict[str, Any]] = {}
        # And, by block position, the edge text as written before and after
        # the table, marks and all (set with the structure pass's input).
        edge_written: dict[int, tuple[str, str]] = {}
        # The merged characters whose place in each engine's reading is exact,
        # with that engine's own character there (engine_marks_merged,
        # engine_b_marks_merged): what each engine read of the edge text.
        edge_placed: tuple[dict[int, str], dict[int, str]] = ({}, {})
        span_at = (list(itertools.accumulate((len(CJK(span)) for span in spans), initial=0))
                   if spans is not None else None)
        # The line or table cell of engine B's each merged character was read
        # in (engine_b_lines_merged): what engine B read with a table's first
        # or last cell on one line is that cell's (table_places), and the
        # edge text is judged line by line (edge_verdicts).
        b_lines = engine_b_lines_merged(draft2, segments, pieces) if draft2 is not None else None
        if b_lines is not None and len(b_lines) != len(CJK(resolved)):
            b_lines = None
        # Engine B's reading is the witness for the grid engine A built, and
        # the book's running heads tell a head row from a table row
        # (engine_table).
        tables, tables_rejected, table_orientation, table_sources, recovery = recover_tables(
            blocks, resolved, render_bytes, client, arguments.max_tokens,
            witness=draft2, heads=book_heads, furniture=book_furniture, spans=spans, lines=b_lines,
            table_orientation=table_orientation_mode)
        recovery_packet_pending = bool(recovery.get("packetPending"))
        doubts.extend(recovery["doubts"])
        table_grouping = recovery["figureGrouping"]
        cell_direction = recovery["cellDirection"]
        table_orientation_evidence.extend(recovery.get("tableOrientationEvidence", []))
        table_edge.update((index, dict(found, source="recovered"))
                          for index, found in recovery["edges"].items() if index in table_sources)

        # Tables rebuilt from the image, with their characters locked: a
        # block nominated as a missed table (its text is the union of both
        # readings), and a grid engine B contradicted on an order-conflict
        # page (its text from engine B's lines, window_merge).  What both
        # engines read there must be in the table, nothing else either read
        # may be added (rebuild_table).  A nominated block is only rebuilt
        # where the page has block spans: the table takes the block's place.
        table_nominations = [dict(n) for n in nominations]
        candidates = ([(n["block"], "nominated") for n in table_nominations if spans is not None]
                      + [(k, "grid refused, text from engine B's lines") for k in sorted(windowed)])
        if spans is None:
            for nomination in table_nominations:
                nomination["outcome"] = "not rebuilt: the page has no block spans"
        agreed_text = agreed_block_text(blocks, agreed_segments) if candidates else []
        # Characters the rebuilt tables hold beyond the merged text (D20): the
        # structure pass writes the tables as they stand, so its character
        # check counts them as sanctioned too.
        table_added: Counter = Counter()
        # Decision D22: a nominated block's rebuild also takes in the blocks
        # of its table the layout model split off (rebuild_region); each
        # block a table took in, by position, with the position of the block
        # whose table took it in.  Their text is the table's: it is not
        # written again beside it.
        absorbed_into: dict[int, int] = {}
        # What of each block taken in its table must hold (both engines'
        # reading of it): the fallback and tableCharactersLost count it as
        # they count a table block's agreed text.
        absorbed_held: dict[int, str] = {}
        # The blocks a refused region named, beside a table rebuilt from the
        # nominated block alone, with what both engines read of each (the
        # region's table would have had to hold it): they stay text, and the
        # structure pass may not leave them out (beside_lost); a page that
        # lacks them seals it in tableCharactersLost.  Measured on a textbook
        # page: the region of a table's body and its header column was refused
        # for one character no engine read, the body alone was rebuilt, and
        # the structure pass left the 20-character header out - the deletion
        # guard cut it into short pieces against the table's cells, and
        # tableCharactersLost counted table blocks only, so nothing said so.
        beside_table: dict[int, str] = {}
        # Characters no engine read that a rebuilt table may write, each
        # confirmed by 裁決 (the user's headerCharacters ruling, confirm_unread),
        # and the crops they were asked on.
        no_unread = getattr(arguments, "no_unread_table_characters", False)
        unread_crops: list[Path] = []
        # 裁決's answer on each spot asked (a retry that writes the same
        # character there is not asked again).
        unread_verdicts: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
        agreed_aligned = (agreed_block_text(blocks, aligned) if mode == "merge"
                          and any(source == "nominated" for _, source in candidates) else [])
        render_size: tuple[int, int] | None = None
        # Engine B's reading as the page settled it: its glyphs the page's
        # rules take for printed marks (丶 for ，, 一 for a bracket) are no
        # reading of a character (the D20 lock, D22's witness).
        as_settled = [dict(x, b=b) for x, b in zip(segments, b_settled)] if candidates else []
        # The other engine's character at each merged character the engines
        # read one for one (merged_other_readings), for a rebuilt table's
        # edge text: set when first needed.
        other_readings: list[str] | None = None
        region_origin: list[str] = []
        for k, source in candidates:
            # Sealed as the layout's block number, as every block number is
            # (layout_index); k stays the block's position here.
            layout = layout_index(blocks, k)
            if k in absorbed_into:
                for nomination in table_nominations:
                    if nomination["block"] == k:
                        nomination["outcome"] = (f"taken into block {layout_index(blocks, absorbed_into[k])}'s "
                                                 "rebuilt table")
                continue
            region = [k]
            # What of each block taken in engine B read inside the region
            # (region_witness): the evidence that took it in.
            witnessed: dict[int, str] = {}
            if source == "nominated" and agreed_aligned:
                if render_size is None:
                    from PIL import Image
                    with Image.open(job.render) as image:
                        render_size = image.size
                region = sorted([k] + [j for j in rebuild_region(
                    blocks, k, render_size, agreed_aligned,
                    witness=lambda j, k=k: witnessed.setdefault(
                        j, region_witness(blocks, as_settled, starts, k, j)),
                    nominated={n["block"] for n in table_nominations}) if j not in absorbed_into])

            def ask_region(region: list[int]) -> tuple[str, str, list, str | None,
                                                      dict[str, Any], dict[int, str]]:
                """The rebuild of REGION: its lock, its merged text, the runs
                the lock added, the table or None, the record, and what of
                each block taken in the table must hold."""
                text = ("\n".join(spans[j] for j in region) if spans is not None
                        else "".join(piece for x, piece in zip(segments, pieces) if x.get("window") == k))
                merged_text, lock_runs = text, []
                if source == "nominated":
                    # Decision D20: a nominated block's table may hold what either
                    # engine read there, each character at the higher engine's
                    # count, not only what the merge wrote (rebuild_lock).  A
                    # block read from engine B's lines keeps its lines' text:
                    # engine A's reading of it is the grid refused for copies.
                    bounds = [((block_ends[j - 1] if j else 0), block_ends[j]) for j in region]
                    text, lock_runs = rebuild_lock(
                        merged_text, "\n".join(CJK(str(blocks[j]["text"])) for j in region),
                        "\n".join(engine_b_inside(as_settled, starts, lo, hi) for lo, hi in bounds),
                        [run for lo, hi in bounds for run in unwritten_readings(as_settled, starts, pieces, lo, hi)])
                boxes = [blocks[j]["box"] for j in region]
                x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
                union_box = [x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0]
                crop = upscale_crop(crop_block(job.render, union_box, pad=0.01)) or render_bytes
                # What the table must hold: what both engines read in each
                # block of the region, and of a block taken in (D22) also
                # what of it engine B read inside the region - both engines'
                # reading of it too, only not in one order.  An accepted
                # table stands for every block of its region, so a block
                # whose text is not in it is not the table's: a title engine
                # B read after the table was aligned against one of its rows,
                # agreed on nothing, and left the page with the table.
                allowed = Counter(CJK(text))
                need = {j: Counter(agreed_text[j]) | (Counter(witnessed.get(j, "")) & allowed)
                        for j in region if j != k}
                required = sum(need.values(), Counter(agreed_text[k]))
                table, rebuild = rebuild_table(client, crop, text, required, arguments.max_tokens,
                                               confirm=None if no_unread else lambda char, cell, at: confirm_spot(
                                                   region, char, cell, at),
                                               table_orientation=table_orientation_mode)
                held = {}
                for j, counts in need.items():
                    chars = []
                    for ch in CJK(str(blocks[j].get("text") or "")):
                        if counts[ch] > 0:
                            chars.append(ch)
                            counts[ch] -= 1
                    held[j] = "".join(chars)
                return text, merged_text, lock_runs, table, rebuild, held

            def confirm_spot(region: list[int], char: str, cell: str, at: int) -> dict[str, Any]:
                """裁決 asked about a character no engine read that a rebuilt
                table wrote (confirm_unread), on a crop of its spot: found by
                the cell's characters around it in engine A's reading of the
                region (unread_spot), cut as a disagreement's crop there is,
                or past the box where the spot is at its edge (unread_crop),
                and sealed next to the page."""
                asked: dict[str, Any] = {"char": char, "cell": cell[:40], "at": at}
                spot = unread_spot([CJK(str(blocks[j].get("text") or "")) for j in region], cell, at)
                if spot is None:
                    return dict(asked, confirmed=False,
                                why="no stretch of its cell around it stands once in engine A's reading")
                j, position, left, right = spot
                key = (region[j], position, left, right, char)
                if key in unread_verdicts:
                    return dict(asked, **unread_verdicts[key])
                seg = {"tag": "insert", "a": "", "b": char, "before": left, "after": right, "deletionGuard": False}
                spot_crop = unread_crop(job.render, render_bytes, blocks[region[j]], position, seg)
                unread_crops.append(job.out_json.with_name(
                    f"{job.out_json.stem}-table-unread-{len(unread_crops) + 1:02d}.png"))
                if spot_crop:
                    unread_crops[-1].write_bytes(spot_crop)
                # Its place (call_site): the spot's block and context, as its
                # adjudication record gives them, and where the region's text
                # begins in the merged text, as the table's entries give it.
                with call_site({"block": layout_index(blocks, region[j]), "contextBefore": left, "contextAfter": right,
                                "engineA": "", "engineB": "",
                                "mergedOffset": len(CJK("".join(spans[:region[0]]))) if spans is not None else None,
                                "crop": unread_crops[-1].name if spot_crop else None}):
                    _, verdict, used = adjudicate_segment(client, spot_crop, seg, arguments.max_tokens,
                                                          note=UNREAD_NOTE,
                                                          simplified=getattr(arguments, "simplified_script", False))
                add(used)
                read = CJK(verdict["resolved"]) if verdict["resolved_from"] in ("adjudicator", "adjudicator-empty") else None
                verdict.update({"crop": unread_crops[-1].name if spot_crop else None, "askedFor": "headerCharacters",
                                "block": layout_index(blocks, region[j])})
                adjudications.append(verdict)
                unread_verdicts[key] = dict(block=layout_index(blocks, region[j]), crop=verdict["crop"],
                                            answered=read or "",
                                            confirmed=bool(read) and variant_fold(read) == variant_fold(char),
                                            **({} if read is not None else {"why": "no verdict in 裁決's answer"}))
                return dict(asked, **unread_verdicts[key])

            try:
                text, merged_text, lock_runs, table, rebuild, held = ask_region(region)
                region_refused = None
                if table is None and len(region) > 1:
                    # The region refused (not a table, a block taken in not in
                    # the table, two tables in the answer): the nominated block
                    # alone, the rebuild D22 off asks, before the block is given
                    # up as text - a refused region must not cost the page the
                    # table D22 off keeps.  A made-up page of two adjacent tables
                    # the layout called text, asked as one region: the answer
                    # held two tables, refused, and the page kept neither; with
                    # D22 off each was accepted.
                    region_refused = {"region": [layout_index(blocks, j) for j in region if j != k],
                                      "reason": rebuild["reason"],
                                      **({"unreadAsked": rebuild["unreadAsked"]} if rebuild.get("unreadAsked") else {})}
                    refused_held = {j: held[j] for j in region if j != k}
                    region = [k]
                    text, merged_text, lock_runs, table, rebuild, held = ask_region(region)
                    if table is not None:
                        # The blocks the refused region named stay text beside
                        # the table rebuilt without them, and what both engines
                        # read of them must stay on the page (beside_table).
                        beside_table.update(refused_held)
            except PacketPending:
                rebuild_packet_pending = True
                continue
            # The table as the rebuild answered it, in its order: before its
            # orientation, what of the region's text it holds in order
            # (rebuilt_edges).
            answered = table or ""
            rebuild = {"block": layout, "from": source, **rebuild,
                       **({"region": [layout_index(blocks, j) for j in region if j != k]} if len(region) > 1 else {}),
                       **({"regionRefused": region_refused} if region_refused else {}),
                       **({"lockAdded": [run for _, run in lock_runs]} if lock_runs else {})}
            table_rebuilds.append(rebuild)
            beyond = Counter(CJK(table)) - Counter(CJK(merged_text)) if table is not None else Counter()
            table_added.update(beyond)
            # The characters no engine read that 裁決 confirmed (the user's
            # headerCharacters ruling, confirm_unread): theirs, not D20's.
            confirmed_unread = ([r for r in rebuild.get("unreadAsked") or [] if r.get("confirmed")]
                                if table is not None else [])
            beyond = beyond - Counter(r["char"] for r in confirmed_unread)
            at = len(CJK("".join(spans[:region[0]]))) if spans is not None else None
            merged_cjk = CJK(resolved)
            where = {"block": layout,
                     "contextBefore": merged_cjk[max(0, at - 8):at] if at is not None else "",
                     "contextAfter": merged_cjk[at + len(CJK(merged_text)):][:8] if at is not None else ""}
            if confirmed_unread:
                doubts.append({"kind": "table-unread-confirmed", "detail": (
                    f"TABLE CHARACTER NO ENGINE READ: block {layout} ({source}): the rebuilt table writes "
                    + "; ".join(f"「{r['char']}」 in 「{r['cell'][:12]}」 (crop {r['crop']})" for r in confirmed_unread)
                    + ", which neither engine read; 裁決 read the same on its spot - check it against the image")})
                decision_changes.append({
                    "decision": "headerCharacters", "rule": "table-unread-confirmed",
                    **where, "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                    "evidence": {"confirmed": [{key: r.get(key) for key in ("char", "cell", "at", "block", "crop",
                                                                             "answered")}
                                               for r in confirmed_unread]}})
            if table is not None and len(region) > 1:
                # Decision D22: the blocks taken in are the table's text.
                for j in region:
                    if j != k:
                        absorbed_into[j] = k
                        absorbed_held[j] = held[j]
                        blocks[j] = dict(blocks[j], label="table", nominated=True, absorbedInto=layout)
                decision_changes.append({
                    "decision": "D22", "rule": "rebuild-region", **where,
                    "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                    "evidence": {"region": [layout_index(blocks, j) for j in region if j != k],
                                 "held": [held[j] for j in region if j != k]}})
            if beyond:
                # Characters the table holds that the merge did not write there
                # (decision D20).
                decision_changes.append({
                    "decision": "D20", "rule": "rebuild-lock-both-readings", **where,
                    "engineA": "".join(run for engine, run in lock_runs if engine == "A"),
                    "engineB": "".join(run for engine, run in lock_runs if engine == "B"),
                    "after": "".join(beyond.elements()), "mergedOffset": at,
                    "evidence": {"lockAdded": [run for _, run in lock_runs],
                                 "locked": rebuild["locked"], "merged": len(CJK(merged_text))}})
            nominated_by = next((n for n in table_nominations if n["block"] == k and source == "nominated"), None)
            if table is not None and nominated_by is not None and nominated_by.get("witness"):
                # Decision D21: nominated only on the witness with this page's
                # mark glyphs left out (marks_left_out).
                decision_changes.append({
                    "decision": "D21", "rule": "nomination-witness-marks-left-out", **where,
                    "engineA": CJK(str(blocks[k]["text"])), "engineB": "",
                    "after": table, "mergedOffset": at,
                    "evidence": {"signal": nominated_by["signal"], **nominated_by["witness"]}})
            for nomination in table_nominations:
                if nomination["block"] == k:
                    nomination["outcome"] = rebuild["outcome"] + ": " + rebuild["reason"]
                    if len(region) > 1:
                        nomination["region"] = [layout_index(blocks, j) for j in region if j != k]
                    if region_refused:
                        nomination["regionRefused"] = region_refused
            if table is None:
                if not rebuild["reason"].startswith("not a table"):
                    doubts.append({"kind": "table-rebuild-refused", "detail": (
                        f"TABLE REBUILD REFUSED: block {layout} ({source}): {rebuild['reason']}")})
                if spans is not None and CJK(text) and blocks[k].get("label") in FURNITURE_LABELS:
                    # A table block gets no punctuation pass (FURNITURE_LABELS),
                    # so its text goes to the structure pass bare, and that pass
                    # may not add a mark: nothing reads the marks printed in it.
                    # Such a page went whole before it had block spans, and the
                    # formatter read them (sealed punctuation-whole-page).
                    # Measured: a textbook table whose four printed marks are
                    # all brackets, window-read, its rebuild refused - the page
                    # was sealed with nothing saying its marks were never read.
                    # A nominated block keeps its own label, and its pass.
                    a_marks = punctuation_counts(str(blocks[k].get("text") or "").translate(MARK_FORMS))
                    doubts.append({"kind": "table-marks-unread", "detail": (
                        f"TABLE MARKS NOT READ: block {layout} ({source}): no table was rebuilt "
                        f"({rebuild['reason'][:80]}), and a table block's text goes to the structure "
                        "pass with no punctuation pass and its marks locked"
                        + (f"; engine A read {sum(a_marks.values())} mark(s) in it ("
                           + "".join(f"{mark}×{n}" for mark, n in a_marks.items()) + ")" if a_marks else "")
                        + " - check the marks against the image")})
                continue
            as_written = table
            orientation_context = (f"rebuilt block {layout}, full-page boxes "
                                   f"{[blocks[j].get('box') for j in region]}; source grid:\n{table}")
            # The rebuild was asked for the physical grid; the benchmark's rule
            # turns it by the page's print direction (orient_table) - unless
            # engine B's reading order shows the rows are the printed columns
            # already - and engine B's order or the image where that is unknown.
            try:
                oriented, how = orient_table(
                    table, render_bytes, client, arguments.max_tokens,
                    turn=lambda written: turn_records(written, draft2, ask=lambda grid: ask_first_record(
                        grid, render_bytes, client, arguments.max_tokens, context=orientation_context)),
                    table_orientation=table_orientation_mode, context=orientation_context,
                    witness=draft2, print_direction=page_print_direction(blocks, image_size(render_bytes)),
                    physical=True)
                table_orientation_evidence.append(table_orientation_record(
                    as_written, oriented, how, render_bytes, table_orientation_mode, layout))
                if orientation_undecided(how):
                    doubts.append({"kind": "table-orientation-undecided", "detail": (
                        f"TABLE ORIENTATION UNDECIDED: block {layout}; kept as rebuilt: {how}")})
                if oriented != as_written:
                    decision_changes.append({
                        "decision": "D23", "rule": "rebuilt-table-oriented",
                        **where, "engineA": "", "engineB": "", "after": oriented, "mergedOffset": at,
                        "evidence": {"orientation": how[:300]}})
                table = oriented
                table, directions = fix_cell_direction(
                    table, client, arguments.max_tokens, block=layout, render_bytes=render_bytes,
                    context=orientation_context + "\norientation: " + how,
                    table_orientation=table_orientation_mode)
            except PacketPending:
                rebuild_packet_pending = True
                continue
            cell_direction = cell_direction + directions
            undecided = [record["read"] for record in directions if record["outcome"] == "undecided"]
            if undecided:
                doubts.append({"kind": "cell-direction-undecided", "detail": (
                    f"CELL DIRECTION UNDECIDED: block {layout}: {len(undecided)} label cell(s) kept as read "
                    f"({'、'.join(undecided[:6])}{'…' if len(undecided) > 6 else ''})")})
            table_orientation.append(f"block {layout}: rebuilt from the crop, {how}")
            # The brackets engine A printed in the region, written back
            # cell by cell (table_brackets): the bracket rule's write-back,
            # for a table no punctuation pass reads.
            table, brackets = table_brackets(table, [str(blocks[j].get("text") or "") for j in region])
            if brackets["inserted"] or brackets["moved"] or brackets["unplaced"]:
                rebuild["brackets"] = brackets
            if brackets["unplaced"]:
                doubts.append({"kind": "table-marks-unread", "detail": (
                    f"TABLE MARKS NOT READ: block {layout} ({source}): the table was rebuilt, but "
                    f"{len(brackets['unplaced'])} bracket(s) engine A read there stand in no cell of it "
                    f"({'; '.join(brackets['unplaced'][:4])}) - check the marks against the image")})
            unused = Counter(CJK(text)) - Counter(CJK(table))
            rebuild["unused"] = "".join(unused.elements())[:200]
            doubts.append({"kind": "table-rebuilt-unvouched", "detail": (
                f"TABLE REBUILT FROM THE IMAGE: block {layout} ({source}); no engine grid vouches for "
                f"its layout" + (f"; {sum(unused.values())} character(s) one engine read there are "
                                 f"not in it ({''.join(unused.elements())[:20]})" if unused else ""))})
            if blocks[k].get("label") != "table":
                blocks[k] = dict(blocks[k], label="table", nominated=True)
            if span_at is not None:
                # The table's edge text: what of its region's span the rebuilt
                # table does not hold, from the region's first character on and
                # back from its last, stays text beside it (rebuilt_edges,
                # table_edges).  The rest it does not hold is the unvouched
                # doubt's, as before.
                merged_cjk = CJK(resolved)
                at_region = [p for j in region for p in range(span_at[j], span_at[j + 1])]
                if other_readings is None:
                    other_readings = merged_other_readings(segments, pieces)
                    region_origin = merged_origins(segments, pieces)
                lead, trail = rebuilt_edges(
                    CJK(answered), "".join(merged_cjk[p] for p in at_region),
                    [other_readings[p] if p < len(other_readings) else "" for p in at_region],
                    [region_origin[p] if p < len(region_origin) else "" for p in at_region],
                    span_at[region[0] + 1] - span_at[region[0]], span_at[region[-1] + 1] - span_at[region[-1]])
                lo, hi = span_at[region[0]], span_at[region[-1] + 1]
                if lead or trail:
                    table_edge[k] = {"lead": (lo, lo + lead), "trail": (hi - trail, hi),
                                     "restated": table, "outside": False, "source": "rebuilt"}
            # In page order with the recovered tables (token numbers, joins).
            in_order = sorted(zip(table_sources + [k], tables + [table]), key=lambda pair: pair[0])
            table_sources, tables = [s for s, _ in in_order], [t for _, t in in_order]
        def beside_lost(text: str) -> str:
            """What TEXT lacks of the blocks beside a rebuilt table
            (beside_table), beyond what it lacks of the table blocks."""
            if not beside_table:
                return ""
            agreed_now = [absorbed_held.get(index, held_text) for index, held_text
                          in enumerate(agreed_block_text(blocks, agreed_segments))]
            both = table_characters_lost(blocks, agreed_now, text, book_heads, book_furniture, beside_table)
            tables_only = table_characters_lost(blocks, agreed_now, text, book_heads, book_furniture)
            short = Counter(both) - Counter(tables_only)
            out = []
            for char in both:
                if short[char] > 0:
                    out.append(char)
                    short[char] -= 1
            return "".join(out)

        # The whole-page formatter is told to write a doubled circle as 。。, as
        # the punctuator is.
        format_input, punct_locked = resolved, False
        format_system = FORMAT_SYSTEM
        punctuated: list[str] = []
        # The blocks listing entries with leaders, given engine A's line breaks
        # (break_lines).
        listed: set[int] = set()
        whole_page_hint = ""
        if spans is None and CJK(resolved):
            # No block-by-block pass: the formatter punctuates the whole page
            # with no census to check it against.  Not clean - measured: such a
            # page came out with ，：「」 throughout a book printed with 。 only,
            # and was sealed clean - and the formatter is at least given what
            # the probe saw this book print.
            doubts.append({"kind": "punctuation-whole-page",
                           "detail": "PUNCTUATED AS A WHOLE PAGE: "
                                     + whole_page_reason(blocks, draft, mode, order_text)
                                     + "; no mark count was taken, so check the marks against the image"})
            whole_page_hint = notation_hint(arguments)
            # Switch --keep-arabic-numerals: the figures the merge keeps go
            # into the characters the formatter is given, at their gaps (the
            # merged text holds characters only, so a gap is its index).
            for kept in sorted(figures_kept, key=lambda f: -f["merged"]):
                kept["placed"], kept["given"] = "written", kept["merged"]
                format_input = format_input[:kept["merged"]] + kept["figure"] + format_input[kept["merged"]:]
        if spans is not None:
            heads = set(profile_section(arguments, "layout").get("runningHeads") or [])
            hint = notation_hint(arguments)
            limit = density_limit(arguments)
            # Which blocks are punctuated at all is settled first; then every
            # (block, tile) pass runs at once.  The passes are independent - each
            # sees only its own crop and its own characters - so running them
            # together changes no answer, only how long the page takes.
            punctuated = [""] * len(blocks)
            to_punctuate: list[int] = []
            # Switch --learned-heads: the page's blocks in the learned running
            # head's band (learned_band).
            learned = learned_record(arguments)
            learned_here = learned_band(blocks, learned, job.scan_page) if learned else {}

            for index, (block, span) in enumerate(zip(blocks, spans)):
                if heads and is_running_head(str(block["text"]), sorted(heads)) and \
                        is_running_head(span, sorted(heads)) and \
                        head_size_matches(block, str(block["text"]), arguments):
                    # A running head the probe measured for this book (repeated
                    # on the page, or on most pages): furniture by evidence.
                    profile_furniture.append(CJK(span))
                    continue
                found = (learned_head_block(span, learned_here.get(index), learned, job.scan_page, str(block["text"]))
                         if learned else None)
                if found:
                    # Switch --learned-heads: a block of the band whose merged
                    # text is all the learned running head - the title with the
                    # label beside it, the label alone, the folio - is left out
                    # before punctuation, so neither the structure pass nor its
                    # fallback has it, whatever its layout label.  Off, it is
                    # punctuated (a folio labelled number handed over as it is)
                    # and the structure pass is asked to take the furniture out.
                    learned_dropped.add(index)
                    decision_changes.append({
                        "decision": "learnedHeads", "rule": "learned-head-block",
                        "block": layout_index(blocks, index),
                        "contextBefore": CJK("".join(spans[:index]))[-8:],
                        "contextAfter": CJK("".join(spans[index + 1:]))[:8],
                        "mergedOffset": sum(len(other) for other in spans[:index]),
                        "after": "", "evidence": {"label": block.get("label"), "text": span, **found}})
                    continue
                if not CJK(span) or block["label"] in FURNITURE_LABELS:
                    punctuated[index] = span
                    continue
                to_punctuate.append(index)

            # Engine A's own marks, tied to the merged text's characters: what a
            # pass whose answers are both refused is held to (agreed_punctuation,
            # its marks FALLBACK_MARKS).
            a_marks_merged = engine_marks_merged(draft, segments, pieces, blocks, vocabulary=FALLBACK_MARKS)
            span_starts = [0] + list(itertools.accumulate(len(span) for span in spans))
            # The rule agreedMarks (C1): a block whose marks both engines read
            # alike (agreed_marks) is written with them, its census and
            # punctuate calls not made.  Not on a book whose profile saw
            # doubled circles or emphasis marks: an engine's 。 there does not
            # say whether one circle or two is printed, and neither engine reads
            # the marks beside each character.
            notation = profile_section(arguments, "notation")
            agreed: dict[int, str] = {}
            # The blocks taken only with --same-print-marks (agreed_marks, same_print).
            agreed_print: set[int] = set()
            # The blocks taken only with --same-marks-any-form (agreed_marks, FORM_FOLD).
            agreed_form: set[int] = set()
            if draft2 is not None and not (notation.get("doubled") or notation.get("emphasis")):
                found_a = engine_places(engine_gaps(draft)[0], segments, pieces, blocks)
                readings = [engine_reading(draft, found_a[0] if found_a else None, span_starts[-1]),
                            engine_reading(draft2, engine_b_places(engine_gaps(draft2)[0], segments, pieces),
                                           span_starts[-1])]
                # Switch --same-marks-any-form: the two readings again, their forms made one.
                # The fold maps one character to one, so the characters' places are the same.
                folded = None
                if getattr(arguments, "same_marks_any_form", False):
                    folded = [engine_reading(draft.translate(FORM_FOLD), found_a[0] if found_a else None, span_starts[-1]),
                              engine_reading(draft2.translate(FORM_FOLD),
                                             engine_b_places(engine_gaps(draft2)[0], segments, pieces), span_starts[-1])]
                for index in to_punctuate:
                    text = agreed_marks(spans[index], span_starts[index], readings)
                    if text is None and getattr(arguments, "same_print_marks", False):
                        text = agreed_marks(spans[index], span_starts[index], readings, same_print=True)
                        if text is not None:
                            agreed_print.add(index)
                    if text is None and folded is not None:
                        text = agreed_marks(spans[index], span_starts[index], folded, same_print=True, write=readings[0])
                        if text is not None:
                            agreed_form.add(index)
                    if text is not None:
                        agreed[index] = text
                # A block with one place holding a mark, both engines' the same: taken too on a
                # page whose other blocks show the two engines read its marks alike
                # (ANY_FORM_PAGE_PLACES places in the blocks taken).  A block neither engine read
                # a mark in is still asked about.
                if folded is not None and sum(mark_places(text) for text in agreed.values()) >= ANY_FORM_PAGE_PLACES:
                    for index in to_punctuate:
                        if index not in agreed:
                            text = agreed_marks(spans[index], span_starts[index], folded, same_print=True,
                                                write=readings[0], minimum=1)
                            if text is not None:
                                agreed[index] = text
                                agreed_form.add(index)
            # Switch --unmarked-page-sample: on a page neither engine read
            # marks on, the longest UNMARKED_SAMPLE of the blocks engine A read
            # as characters alone are asked with the page's other blocks, and
            # the rest wait for their answers (unmarked_candidates).  Not on a
            # book whose profile saw doubled circles or emphasis marks, which
            # no engine reads: the sample would find them, a wave later.
            waiting: list[int] = []
            sampled: set[int] = set()
            unmarked: set[int] = set()
            if getattr(arguments, "unmarked_page_sample", False) and not (
                    notation.get("doubled") or notation.get("emphasis")):
                candidates = unmarked_candidates(blocks, spans, [index for index in to_punctuate
                                                                 if index not in agreed], draft2)
                if len(candidates) > UNMARKED_SAMPLE:
                    sampled = set(sorted(candidates, key=lambda index: (-len(CJK(spans[index])), index))
                                  [:UNMARKED_SAMPLE])
                    waiting = [index for index in candidates if index not in sampled]

            def cut(index: int) -> tuple[bool, list[tuple[bytes | None, int, int]], int]:
                """Whether block INDEX is cut in column groups, its (crop, start,
                end) parts, and how far a part's text may be off its crop at
                either end (a column's worth; 0 for a block punctuated whole,
                where only variant forms are folded - realign_answer).  A block
                the rule agreedMarks writes is one part, with no crop."""
                block, span = blocks[index], spans[index]
                if index in agreed:
                    return False, [(None, 0, len(span))], 0
                tiles = block_tiles(job.render, block, len(span))
                return bool(tiles), tiles or [
                    (upscale_crop(crop_block(job.render, block["box"], pad=0.012, pad_left=0.0))
                     or render_bytes, 0, len(span))], column_reach(job.render, block, len(span)) if tiles else 0

            def punctuate(item: tuple[int, int, bytes | None, int, int, bool, int]):
                index, tile_number, crop, lo, hi, tiled, reach = item
                if index in agreed:
                    # The rule agreedMarks (C1): the engines' marks, no call;
                    # the pass records it (censusNotAsked, rule engines-agree;
                    # engines-agree-print where --same-print-marks took it).
                    out = agreed[index]
                    return out, {"ok": True, "census": None, "reason": "",
                                 "censusNotAsked": {"rule": "engines-agree-print" if index in agreed_print
                                                    else "engines-agree-form" if index in agreed_form
                                                    else "page-unmarked-sample" if index in unmarked
                                                    else "engines-agree"},
                                 "marks": sum(punctuation_counts(out).values()), "doubled": out.count("。。"),
                                 "places": mark_places(out), "leaders": punctuation_counts(out)[LEADER],
                                 "bullets": sum(out.count(bullet) for bullet in BULLET_MARKS)}, []
                printed = (gaps_of(a_marks_merged, span_starts[index] + lo, hi - lo)
                           if a_marks_merged is not None else None)
                # The pass's place (call_site): its block, its tile and the
                # stretch it is asked - its punctuationPasses record's range,
                # or realigned.range where the answer read another stretch.
                with call_site({"block": layout_index(blocks, index), "mergedOffset": span_starts[index] + lo,
                                "range": [lo, hi], **({"tile": tile_number} if tiled else {})}):
                    return punctuate_part(client, arguments, crop, spans[index][lo:hi], hint, limit,
                                          printed=printed, block=(spans[index], lo, hi, reach))

            def asked(indices: Sequence[int]) -> tuple[list[tuple[int, int, bytes | None, int, int, bool, int]], list]:
                """The passes of the blocks INDICES and their answers."""
                cuts = in_context(pool, cut, indices)
                some = [(index, tile_number, crop, lo, hi, tiled, reach)
                        for index, (tiled, parts, reach) in zip(indices, cuts)
                        for tile_number, (crop, lo, hi) in enumerate(parts)]
                del cuts
                answers_out = in_context(pool, punctuate, some)
                some = [(index, tile_number, None, lo, hi, tiled, reach)
                        for index, tile_number, _, lo, hi, tiled, reach in some]
                return some, answers_out

            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                passes, answers = asked([index for index in to_punctuate if index not in waiting])
                if waiting:
                    # The sample is in.  Every pass of it answered and without
                    # a mark: the blocks that waited are written as they are
                    # (agreed, rule page-unmarked-sample).  A mark in one, or
                    # an answer refused: they are asked as any block is.
                    if all(record.get("ok") and not sum(punctuation_counts(out).values())
                           for (index, *_), (out, record, _) in zip(passes, answers) if index in sampled):
                        unmarked = set(waiting)
                        agreed.update((index, spans[index]) for index in waiting)
                    more, more_answers = asked(waiting)
                    for (index, *_), (_, record, _) in zip(passes, answers):
                        if index in sampled:
                            record["unmarkedSample"] = {"waiting": len(waiting), "taken": bool(unmarked)}
                    # In the page's block order, as with no sample.
                    place = {index: number for number, index in enumerate(to_punctuate)}
                    both = sorted(zip(passes + more, answers + more_answers),
                                  key=lambda pair: (place[pair[0][0]], pair[0][1]))
                    passes, answers = [pair[0] for pair in both], [pair[1] for pair in both]
            # Each pass's text with the stretch of its block it read: its own
            # [lo, hi), or where its answer was realigned (realign_answer).
            pieces_out: dict[int, list[tuple[str, int, int, int, int]]] = {index: [] for index in to_punctuate}
            records_out: dict[int, list[dict[str, Any]]] = {index: [] for index in to_punctuate}
            for (index, tile_number, _, lo, hi, tiled, _), (out, record, used) in zip(passes, answers):
                # Not `for metrics in used`: that rebinds the page's running
                # total, which add() then sums into itself (every seal before
                # this held the last punctuation call's usage, doubled).
                for used_metrics in used:
                    add(used_metrics)
                read = (record.get("realigned") or {}).get("read") or [lo, hi]
                pieces_out[index].append((out, read[0], read[1], lo, hi))
                records_out[index].append(record)
                # range: where this pass sits in the block's text (the stretch
                # it read), so a later re-read of the whole block can still be
                # judged stretch by stretch against this pass's census.
                record.update({"block": layout_index(blocks, index), "label": blocks[index]["label"],
                               "range": list(read)})
                if tiled:
                    record["tile"] = tile_number
                punctuation_passes.append(record)
            for index in to_punctuate:
                punctuated[index], unread = assemble_passes(spans[index], pieces_out[index])
                if unread:
                    # Realigned passes left characters between them that no
                    # pass read: they stand, with no mark.
                    doubts.append({"kind": "punctuation-unread", "detail": (
                        f"PUNCTUATION UNREAD: block {layout_index(blocks, index)}: "
                        + ", ".join(f"「{spans[index][lo:hi][:12]}」" for lo, hi in unread[:4])
                        + " lie between column groups whose answers read the text their crops show "
                        "(realigned), and no answer read them; any marks there are missing - "
                        "check them against the image")})
            # Switch --keep-arabic-numerals: the brackets the bracket rule
            # writes, by block (brackets_by_block, the figures' own left out) -
            # at a figure's gap, the brackets of other print (figure_in_gap).
            rule_brackets: dict[int, Counter] = {}
            if bracket_rule:
                # The punctuator reads marks off the crop alone and was measured
                # keeping 3 of 14 bracket pairs that engine A had read, every one
                # right.  The brackets either engine read are written back into
                # the block they fall in; a bracket the punctuator wrote a
                # character or two away is taken for the same one, misplaced.
                # Switch --keep-arabic-numerals: the brackets of each figure
                # the merge writes go with the figure (figure_in_gap), and a
                # pair around a figure it does not keep is not written alone;
                # without the switch (or where it did not run on the page)
                # every pair is (engine_brackets_merged).
                figure_brackets = Counter()
                for kept in figures_kept:
                    figure_brackets.update({(kept["merged"], "（"): kept["figure"].count("（"),
                                            (kept["merged"], "）"): kept["figure"].count("）")})
                per_block = brackets_by_block(stream_a, stream_b, segments, pieces, spans,
                                              own=figure_brackets if figure_rule else None)
                rule_brackets = per_block
                for index in to_punctuate:
                    if index in per_block:
                        text, inserted, moved = place_brackets(punctuated[index], spans[index], per_block[index])
                        punctuated[index] = text
                        engine_brackets["inserted"] += inserted
                        engine_brackets["moved"] += moved
            for index in to_punctuate:
                own = [entry for entry in punctuation_passes if entry["block"] == layout_index(blocks, index)]
                punctuated[index], found = mark_changes(punctuated[index], span_starts[index],
                                                        layout_index(blocks, index), own, arguments)
                decision_changes.extend(found)
            # A circled note marker both engines read at one place that the
            # block's pass left out is written in (place_note_markers): the
            # structure pass is given it, and restore_note_markers keeps it.
            markers_at = note_markers_by_block(agreed_note_markers(draft, draft2, segments, pieces, blocks),
                                               spans) if draft2 is not None else {}
            merged_chars = "".join(spans)
            for index in to_punctuate:
                if index not in markers_at:
                    continue
                punctuated[index], written = place_note_markers(punctuated[index], spans[index], markers_at[index])
                for g, found in written:
                    at = span_starts[index] + g
                    decision_changes.append({
                        "decision": "textLoss", "rule": "note-marker-both-engines",
                        "block": layout_index(blocks, index),
                        "contextBefore": merged_chars[max(0, at - 8):at], "contextAfter": merged_chars[at:at + 8],
                        "engineA": found["raw"], "engineB": found["rawB"], "after": found["marker"],
                        "mergedOffset": at, "evidence": {"blockGap": g}})
            if figures_kept:
                # Switch --keep-arabic-numerals: each figure the merge keeps is
                # written into its block at its gap, among the marks the block
                # pass and the bracket rule wrote there (place_arabic_figures).
                by_block: dict[int, list[tuple[dict[str, Any], int]]] = {}
                for kept in figures_kept:
                    k = kept["block"]
                    at = kept["merged"] - span_starts[k]
                    if k in absorbed_into or k in table_sources or blocks[k].get("label") == "table":
                        kept["placed"] = "in a table"
                    elif k not in to_punctuate and (CJK(spans[k]) or blocks[k]["label"] in FURNITURE_LABELS):
                        kept["placed"] = "in a block not punctuated"
                    elif not 0 <= at <= len(spans[k]) or (kept["at"] == 0 and at != 0):
                        kept["placed"] = "unplaced"
                    else:
                        by_block.setdefault(k, []).append((kept, at))
                for k, here in by_block.items():
                    # The brackets of other print at each figure's gap: those
                    # the bracket rule wrote there, or with the rule off those
                    # engine A read there besides the figure's.
                    punctuated[k], placed_how = place_arabic_figures(
                        punctuated[k], spans[k],
                        [(at, kept["figure"], kept["after"],
                          {bracket: rule_brackets.get(k, Counter())[(at, bracket)] for bracket in "（）"}
                          if bracket_rule else kept.get("otherBrackets") or {})
                         for kept, at in here])
                    for (kept, at), placed_as in zip(here, placed_how):
                        kept["placed"], kept["blockGap"] = placed_as, at
            places_a = engine_places(engine_gaps(draft)[0], segments, pieces, blocks)
            # A block listing entries with leaders (a contents page) keeps
            # engine A's line breaks: before the place in the merged text of
            # the first character of each of its printed lines, where that
            # place is exact (engine_places).  Before, not after the last
            # character of the line before: what engine B alone read between
            # them (a name's character read twice) stays with the line before,
            # with its leader and page number.  The structure pass is given
            # one printed line a line, and its fallback writes one a paragraph
            # (leader_list_breaks, break_lines).
            for index in to_punctuate:
                at = set()
                for g in leader_list_breaks(str(blocks[index]["text"])) if places_a else []:
                    if block_starts[index] + g in places_a[0]:
                        at.add(places_a[0][block_starts[index] + g] - span_starts[index])
                if at:
                    punctuated[index] = break_lines(punctuated[index], spans[index], at)
                    listed.add(index)
            # A recovered table never goes through the formatter: its block
            # stands in the text as one token line (place_recovered_tables).
            tokens = dict(zip(table_sources, table_tokens(len(tables))))
            # The table's edge text goes on its own lines around the token, with
            # the marks the engines read in it: a table block has no
            # punctuation pass, so what either engine read there is all there
            # is (marked_stretch; engine A's first, where it read the
            # characters beside a gap, else engine B's), in the order the
            # engine wrote them (marks_as_written), as a block written with the
            # engines' marks (agreed_marks) has them.  Nor a census, and the
            # bracket rule writes only the punctuated blocks
            # (tests/check_compositions.py).  A block a rebuilt table took in
            # (D22) is the table's text, and has none of its own.
            for index in list(table_edge):
                if index not in tokens or index in absorbed_into:
                    del table_edge[index]
            if any(found["lead"][1] > found["lead"][0] or found["trail"][1] > found["trail"][0]
                   for found in table_edge.values()):
                a_placed, b_placed = edge_placed
                engines_marks = [(engine_marks_merged(draft, segments, pieces, blocks, placed=a_placed,
                                                      written=True), a_placed),
                                 (engine_b_marks_merged(draft2, segments, pieces, placed=b_placed, written=True)
                                  if draft2 is not None else None, b_placed)]
                merged_cjk = CJK(resolved)
                # Whose reading each merged character is: a copy elsewhere
                # counts only where engine A's reading alone wrote it.
                origin = merged_origins(segments, pieces)
                # Each of engine A's blocks' stretch of the merged text: a
                # piece of edge text that is the whole of one is engine B's
                # reading of it (block_copy).
                blocks_at = ([(layout_index(blocks, j), span_at[j], span_at[j + 1]) for j in range(len(blocks))]
                             if span_at is not None else [])
                for index, found in table_edge.items():
                    written = []
                    for side, (lo, hi) in (("before", found["lead"]), ("after", found["trail"])):
                        parts = edge_verdicts(merged_cjk, lo, hi, draft, draft2,
                                              origin if len(origin) == len(merged_cjk) else None, b_lines,
                                              blocks_at, CJK(found["restated"]))
                        held = [dict(part["held"], text=merged_cjk[part["lo"]:part["hi"]], mergedOffset=part["lo"])
                                for part in parts if part["held"] is not None]
                        if held:
                            # Extra copies: read elsewhere on the page, which
                            # holds them there (edge_copies).
                            found.setdefault("heldElsewhere", {})[side] = held
                        # What is written: each run of pieces not held back,
                        # a line of its own.
                        runs: list[list[int]] = []
                        for part in parts:
                            if part["held"] is None and part["hi"] > part["lo"]:
                                if runs and runs[-1][1] == part["lo"]:
                                    runs[-1][1] = part["hi"]
                                else:
                                    runs.append([part["lo"], part["hi"]])
                        written.append("\n".join(marked_stretch(merged_cjk, start, end, engines_marks)
                                                  for start, end in runs))
                    edge_written[index] = (written[0], written[1])
            # A block a rebuilt table took in (D22) is the table's text.
            format_input = "\n".join(
                "\n".join(part for part in (edge_written.get(index, ("", ""))[0], tokens[index],
                                            edge_written.get(index, ("", ""))[1]) if part)
                if index in tokens else text
                for index, text in enumerate(punctuated) if index not in absorbed_into)
            if figures_kept:
                # Where each figure written into a block stands in the text the
                # structure pass is given, as CJK counts its characters: the
                # gap the structure pass must keep it at (restore_arabic_figures).
                running, first_of = 0, {}
                for index, text in enumerate(punctuated):
                    if index in absorbed_into:
                        continue
                    first_of[index] = running
                    running += len(CJK("".join(edge_written.get(index, ("", ""))) if index in tokens else text))
                for kept in figures_kept:
                    if (kept.get("placed") in FIGURE_AT_GAP and kept["block"] in first_of
                            and kept["block"] not in tokens):
                        kept["given"] = first_of[kept["block"]] + kept["blockGap"]
            format_system, punct_locked = STRUCTURE_SYSTEM, True
        placeholders = punct_locked and bool(tables)
        if recovery_packet_pending or rebuild_packet_pending:
            raise PacketPending("table-wave")

        def expanded(text: str) -> str:
            """TEXT with each table token (⟦Tn⟧) written out as its table."""
            if not placeholders:
                return text
            out, misplaced_tokens, _ = place_recovered_tables(text, tables)
            if misplaced_tokens:
                raise ProofreadError("table expansion failed: " + misplaced_tokens)
            return out

        # Switch --content-fidelity: before formatting, review the actual
        # candidate with its tables written out.  This is the one bounded
        # opportunity to recover regions or non-CJK content dropped by the CJK
        # merge, and to remove image-confirmed furniture.  Never restore raw
        # OCR digits by agreement alone.  The formatter is then given the
        # tables as tokens again, as without the review, unless the review
        # changed them past mapping (refresh_table_mapping).
        if reviewing:
            candidate = expanded(format_input)
            reviewed, content_recovery = image_review(candidate, "content-recovery")
            if reviewed != candidate:
                if table_spans(candidate):
                    # Keep a recovered table's confirmed repairs when the
                    # formatter copies it.  A changed table count cannot be
                    # mapped to old block IDs safely; final image coverage
                    # will verify those regions without such mapping.  (On
                    # the whole-page path the tables are not in the
                    # candidate but in the formatter's hint: nothing to map.)
                    grids = [reviewed[a:b] for a, b in table_spans(reviewed)]
                    source_table_mapping_current, recovery_table_correspondence = (
                        _content_fidelity.refresh_table_mapping(tables, grids))
                    if source_table_mapping_current:
                        tables = grids
                if placeholders and source_table_mapping_current:
                    # The reviewed text with its tables as tokens again.
                    retokened = reviewed
                    for (start, stop), token in reversed(list(zip(table_spans(reviewed),
                                                                  table_tokens(len(tables))))):
                        retokened = retokened[:start] + token + retokened[stop:]
                    format_input = retokened
                else:
                    format_input, placeholders = reviewed, False
        # What the formatter is given, its tables written out: what its
        # answer is checked against once the tokens are replaced.
        expanded_input = expanded(format_input)

        # Stage B - structure.  With punctuation locked the model may only add
        # headings, lists, tables and remove furniture; never touch a mark.
        table_hint = ""
        if placeholders:
            table_hint = (
                f"\n文本入面有 {len(tables)} 行表格記號（{'、'.join(table_tokens(len(tables)))}）："
                "每一行代表一個已經由引擎格仔定咗嘅表格，內容同方向都唔使你理。"
                "每行記號原封不動照抄一次，自己一行，擺喺個表喺頁上嘅位置（通常就係佢喺文本入面嘅位置）；"
                "唔好自己寫呢啲表，唔好刪走、改寫或者重複記號。\n")
        elif tables and source_table_mapping_current:
            # The whole-page path has no block spans to put a token in: the
            # formatter copies the recovered tables, with their label cells
            # as the engines read them (cells_as_read: the deletion guard
            # below compares runs of the merged text, table included), and
            # enforce_recovered_tables puts back what it changes, turning
            # the cells fix_cell_direction decided.
            joined = "\n\n".join(
                cells_as_read(table, [record for record in cell_direction
                                     if record.get("block") == layout_index(blocks, source)])
                for source, table in zip(table_sources, tables))
            orient_note = (
                "呢啲表保持**版面原印方向**，你唔使轉置，原樣照抄結構。\n"
                if table_orientation_mode == "as-printed"
                else "呢啲表已經**擺返做閱讀次序**（該轉置嘅已經轉咗），你唔使再轉，照抄結構。\n"
            )
            table_hint = (
                f"\n呢一頁有 {len(tables)} 個表格，下面係已經由引擎格仔定咗嘅表：\n---\n"
                f"{joined}\n---\n"
                f"{orient_note}"
                "冇合併格嘅表係 Markdown 表，照抄做 Markdown 表；有合併格（一格跨幾行或幾欄）嘅表係 "
                "HTML `<table>`，照抄做 HTML，rowspan／colspan 一個都唔准改，"
                "唔准拆返做 Markdown 表（Markdown 表表達唔到合併格），亦唔准寫成散文；"
                "兩個表就寫兩個表，唔准併埋一個；格入面嘅字一個都唔准改、唔准倒轉。\n"
                "表格入面嘅字已經包含喺上面『已定嘅字』入面，唔好喺表外重複寫一次。\n")
        if any(block.get("label") == "table" and index not in table_sources and index not in absorbed_into
               for index, block in enumerate(blocks)):
            # Tables the formatter must build from the image: the engine's grid
            # was refused (tablesRejected) or not used, so which way a record
            # runs is the formatter's to read off the page (unless as-printed).
            if table_orientation_mode == "as-printed":
                table_hint += (
                    "\n頁上有表格要你對住頁圖重建（引擎嘅格仔信唔過，冇畀你）。"
                    "**照頁圖印出嚟嘅橫行同直欄原樣排格網**（由上至下係行、由左至右係欄），"
                    "唔好將直欄轉置成橫行。\n"
                    "重建淨係搬字，一個字都唔准加、唔准刪、唔准換。\n")
            else:
                table_hint += (
                    "\n頁上有表格要你對住頁圖重建（引擎嘅格仔信唔過，冇畀你）。"
                    "橫排表保持原圖嘅行同欄；直排表將原圖由右至左嘅直欄，依次寫成由上至下嘅行，"
                    "原欄內由上至下嘅格，依次寫成由左至右嘅欄，合併格嘅 rowspan／colspan 對調。"
                    "唔好按內容自行重排行列，亦唔好虛構表頭；冇表頭或者有合併格就寫 HTML `<table>`。\n"
                    "重建淨係搬字，一個字都唔准加、唔准刪、唔准換。\n")
        # Switch --content-fidelity: whether a content-recovery review ran on
        # this page.  It removed the furniture it confirmed, so the formatter
        # deletes nothing (FORMAT_SYSTEM_REVIEWED, verify_characters' strict
        # rule).  Otherwise the formatter takes the furniture out, as the old
        # skill's did: told to here, and checked by what it may delete
        # (removable_text) - the hint asking it to delete running heads was
        # left beside a prompt and a check that forbade any deletion, so a
        # formatter that did as the hint said was refused.
        furniture_reviewed = content_recovery is not None
        if furniture_reviewed:
            format_system = {FORMAT_SYSTEM: FORMAT_SYSTEM_REVIEWED,
                             STRUCTURE_SYSTEM: STRUCTURE_SYSTEM_REVIEWED}[format_system]
        keep_or_delete = ("保留全部來源覆核後嘅字、數字、算式同符號" if furniture_reviewed
                          else "刪走欄外書眉／頁碼")
        layout = profile_section(arguments, "layout")
        layout_hint = ""
        if (layout.get("leavesPerPage") or 1) > 1:
            layout_hint += (f"本書係影印本：量度過，一頁通常印咗 {layout['leavesPerPage']} 葉原書，"
                            + ("每葉有自己嘅書眉同葉碼；正文按葉嘅次序接落去。\n" if furniture_reviewed else
                               "每葉有自己嘅書眉同葉碼——全部係家具，刪走；正文按葉嘅次序接落去。\n"))
        if layout.get("runningHeads") and not furniture_reviewed:
            layout_hint += ("以下字串喺本書量度到係書眉（同一頁重複或者差唔多每頁都有）："
                            + "、".join(layout["runningHeads"][:8]) + "；出現喺正文以外就刪走。\n")
        # Switch --learned-heads: a page whose every block with text was the
        # learned running head has nothing left for the structure pass, which
        # given nothing answers nothing, a failed pass (measured on the
        # offline replay of a book's sealed pages: its two pages printing the
        # running head alone were sealed structure-rejected).
        head_only = bool(learned_dropped) and punct_locked and not CJK(format_input) and not tables
        if head_only:
            # No format call, so none cut off (the seal's "degraded").
            formatted, m = "", {}
        else:
            structure_input = structure_input_record(format_input)
            formatted, m = client.ask_answering(
                format_system,
                (f"字同標點都已定（唔准改）：\n---\n{format_input}\n---\n{table_hint}{layout_hint}\n"
                 f"睇頁圖，加版面結構（heading／段落／list／表格），{keep_or_delete}。標點原封不動。")
                if punct_locked else
                (f"已定嘅字（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}"
                 f"睇頁圖，加返句讀同版面結構，{keep_or_delete}。"),
                render_bytes, arguments.max_tokens, kind="format")
            add(m)
        formatted = strip_fence(formatted)
        # An answer that is none - empty, or cut off at every effort - is a
        # failed pass, whatever the checks below would say of it (no_answer).
        silent = None if head_only else no_answer(formatted, m)
        # The tables go in where their tokens are before anything is checked,
        # so every check below sees the page as it will be written.
        placement_notes: list[str] = []
        misplaced = None
        if placeholders:
            formatted, misplaced, placement_notes = place_recovered_tables(formatted, tables)
        # After a content-recovery review, which removed only image-confirmed
        # furniture, formatting has no remaining deletion exemption, for any
        # script.  Without one the formatter removes the furniture, and the
        # letters and figures it may take with it are those removable_text
        # holds.
        sanctioned = expanded_input
        # A note marker of the text given that the answer left out is written
        # back before anything is checked (restore_note_markers), as the
        # tables are put in: its checks count a marker as a figure, which the
        # page's footnotes may lose as furniture, and a marker is no mark of
        # the punctuation lock.
        markers_restored: list[dict[str, Any]] = []
        note_blocks = note_block_texts(blocks)
        if not silent and not misplaced:
            formatted, markers_restored = restore_note_markers(sanctioned, formatted, note_blocks)
        # The book's recurring n-grams are no text the answer may lack as
        # many times as they hold a character: furniture_unexplained tests a
        # deleted stretch against them instead.
        removable = None if furniture_reviewed else removable_text(
            blocks, punctuated if spans is not None else [], format_input,
            [*profile_furniture, *book_heads],
            [kept["figure"] for kept in figures_kept if "given" in kept])
        ok, reason = ((False, silent) if silent else (False, misplaced) if misplaced
                      else verify_characters(sanctioned, formatted, removable))
        def formatter_losses(candidate: str) -> list[str]:
            # The inventory's features, with --content-fidelity on only.
            if not reviewing:
                return []
            reviewed_maps = (content_recovery or {}).get("table_mappings", [])
            mapped = _content_fidelity.rebind_unchanged_tables(expanded_input, candidate, reviewed_maps)
            mapped_regions = {m["region_id"] for m in reviewed_maps}
            # Newly reconstructed grids have no earlier confirmed cell mapping.
            # Their ownership must be established in mandatory final review;
            # do not reject a new valid table for lacking a pre-format mapping.
            protected = {**inventory, "features": [f for f in inventory["features"]
                if f.get("cell", {}).get("kind") != "grid" or f["region_id"] in mapped_regions]}
            missing = _content_fidelity.feature_losses(candidate, protected, mapped)
            if any(r["kind"] == "table" for r in inventory["regions"]) and not table_spans(candidate):
                missing.append("table-structure")
            return missing

        format_feature_losses = formatter_losses(formatted) if ok else []
        if format_feature_losses:
            ok, reason = False, "formatter lost or displaced source features: " + ", ".join(format_feature_losses)
        # The recovered tables carry their own marks (、 separators, brackets)
        # that the block text does not, so they count as expected input.
        lock_input = expanded_input
        # So do the marks both engines read in a table block whose grid was
        # refused - its text is given bare, with no punctuation pass - where
        # the answer writes them inside its tables (table_marks_lock): the
        # structure pass builds that table from the image, marks and all.
        refused_marks: Counter = Counter()
        if punct_locked and spans is not None:
            refused_blocks = [index for index, block in enumerate(blocks)
                              if block.get("label") == "table" and index not in table_sources
                              and index not in absorbed_into and index not in learned_dropped
                              and index not in to_punctuate]
            if refused_blocks and draft2 is not None:
                refused_marks = table_block_marks(
                    spans, refused_blocks, punctuated,
                    (engine_marks_merged(draft, segments, pieces, blocks),
                     engine_b_marks_merged(draft2, segments, pieces)))
        # The marks of REFUSED_MARKS the accepted answer's tables carry, sealed.
        table_marks_allowed: dict[str, Counter] = {}

        def locked_input(candidate: str) -> str:
            held, table_marks_allowed[candidate] = table_marks_lock(lock_input, candidate, refused_marks)
            return held

        if ok and punct_locked and not punctuation_kept(locked_input(formatted), formatted):
            ok, reason = False, "changed punctuation: " + punctuation_delta(locked_input(formatted), formatted)
        if ok and punctuation_density(formatted) > density_limit(arguments):
            ok = False
            reason = (f"punctuation density {punctuation_density(formatted):.1f} per 100 "
                      "characters - a mark after almost every character")
        furniture = getattr(arguments, 'furniture', frozenset())
        margin_texts = margin_furniture(blocks)
        if not ok and "introduced" in reason:
            missing = Counter(CJK(resolved)) - Counter(CJK(formatted))
            hint = "".join(sorted(missing))[:20]
            if hint:
                reason += f"；合併文本入面對應嘅字係：{hint}"
        if not ok and not misplaced and not silent:
            # Before a retry, try the mechanical repair: if the formatter only
            # swapped glyphs, put the merge's characters back and keep its layout.
            # Never on no answer: a cut-off answer that also swapped a glyph
            # was "repaired" into an accepted page, and what came after the
            # cut was lost with no retry and no doubt (a tail shorter than
            # DELETED_RUN_LIMIT is not a deleted run).
            repaired = restore_merged_characters(sanctioned, formatted)
            if repaired is not None:
                ok2r, reason2r = verify_characters(sanctioned, repaired, removable)
                if ok2r and formatter_losses(repaired):
                    ok2r = False
                if ok2r:
                    formatted, ok = repaired, True
                    reason = "characters restored from the merge after a variant swap"
        # The deletion guard looks for contiguous runs of the merged text; a
        # table rotated into reading order breaks the engine's row-major runs
        # by design, and its characters are already checked cell by cell in
        # the restatement, so the guard runs on the prose blocks only - and on
        # the text written beside a table (table_edges), which is prose too.
        # A block --learned-heads left out is not the prose the structure pass
        # was given: its text is not looked for in the answer (and is not
        # furniture the guard lets a deleted run consist of - a folio of one
        # numeral would let any run with that character in it go).
        prose = ("".join(span if block.get("label") != "table" else "".join(edge_written.get(index, ("", "")))
                         for index, (block, span) in enumerate(zip(blocks, spans)) if index not in learned_dropped)
                 if spans is not None else resolved)
        known_heads = sorted(set(profile_furniture))
        dropped = (deleted_runs(prose, formatted, furniture=furniture, margin=margin_texts,
                                known=known_heads, whole=resolved, prose_only=spans is not None) if ok else [])
        if dropped:
            ok = False
            reason = ("deleted " + " / ".join(f"{len(d)} characters ({d[:18]}…)"
                                              for d in dropped[:3]))

        # The text of the table blocks the structure pass was given, on the
        # block path: each recovered or rebuilt table, and the text of each
        # table block whose grid was refused (split_table_losses).
        given_tables = ("\n".join([*tables, *(text for index, (block, text) in enumerate(zip(blocks, punctuated))
                                             if block.get("label") == "table" and index not in table_sources
                                             and index not in absorbed_into)])
                        if spans is not None and punct_locked else "")
        # What each answer's tables lack of the tables it was given, by answer.
        table_lost: dict[str, str] = {}

        def unexplained(candidate: str) -> str:
            # Without a content review, what the answer deleted of the CJK it
            # was given must be furniture by evidence, however short its runs
            # (furniture_unexplained); deleted_runs took runs from
            # DELETED_RUN_LIMIT characters only.  On the whole-page path
            # REMOVABLE holds the margin blocks' own text (margin_furniture,
            # deleted_runs' MARGIN), which is that evidence.  Body text only,
            # as deleted_runs': what the answer's tables lack of the table
            # blocks is kept for the table-characters-lost doubt
            # (split_table_losses), and does not refuse the answer.
            if removable is None:
                return ""
            lost = furniture_unexplained(sanctioned, candidate, removable,
                                         furniture, [*profile_furniture, *book_heads])
            prose_lost, table_lost[candidate] = split_table_losses(lost, sanctioned, candidate, given_tables)
            return prose_lost

        gone_beside = beside_lost(formatted) if ok else ""
        if gone_beside:
            # The text beside a table rebuilt without it (beside_table) may not
            # be left out, however short: the deletion guard's runs are no
            # measure of a header column of a few characters.
            ok = False
            reason = (f"deleted {len(gone_beside)} characters of the text beside a rebuilt table "
                      f"({gone_beside[:18]}…)")
        gone_furniture = unexplained(formatted) if ok else ""
        if gone_furniture:
            ok = False
            reason = (f"deleted {len(gone_furniture)} character(s) outside the page's furniture "
                      f"({gone_furniture[:18]}…)")
        if not ok:
            # Why the first answer was refused, sealed: the page's text may be
            # the retry's, and without this nobody can tell afterwards what the
            # first answer did wrong (measured: a whole-page answer whose retry
            # was sealed, with nothing saying why the first was not).
            format_retry_reason = reason
            # Tell it exactly what it invented. A blind retry repeats the error;
            # naming the characters lets it drop them and keep the formatting.
            # The recovered tables go with it: a retry that never saw them
            # cannot copy them, and the page then loses its tables for good.
            # A pass that answered nothing is asked its own question again:
            # one cut off at the token budget from a lower effort
            # (NO_ANSWER_RETRY_EFFORT), told it thought too long; one whose
            # reply was empty at its own effort, told the reply was empty
            # (the budget was not what failed: the same question answered at
            # the top effort in other runs, no_answer).
            if silent:
                cut_off = no_answer_cut(silent)
                retry, m = ask_lower(
                    client, NO_ANSWER_RETRY_EFFORT if cut_off else "", format_system,
                    (f"字同標點都已定（唔准改）：\n---\n{format_input}\n---\n{table_hint}{layout_hint}\n"
                     if punct_locked else
                     f"已定嘅字（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}")
                    + ("你上一次諗得太耐，喺 token 上限之前冇寫出答案。唔使逐個字再諗，直接對住頁圖輸出。\n"
                       if cut_off else
                       "你上一次交咗個空白答案，一個字都冇寫。直接對住頁圖輸出。\n")
                    + (f"睇頁圖，加版面結構（heading／段落／list／表格），{keep_or_delete}。標點原封不動。"
                       if punct_locked else f"睇頁圖，加返句讀同版面結構，{keep_or_delete}。"),
                    render_bytes, arguments.max_tokens, kind="format-retry")
            else:
                retry, m = client.ask_answering(
                    format_system,
                    f"{'字同標點都已定' if punct_locked else '已定嘅字'}（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}"
                    f"你上一次嘅輸出被拒絕，原因：{reason}\n"
                    + ("如果係改咗標點：每一個符號都要原封不動，唔准加、刪、搬。\n" if punct_locked else "")
                    + ("如果係表格記號唔見咗或者重複咗：每一行記號都要原封不動照抄一次，唔好自己寫嗰啲表。\n"
                       if placeholders else "")
                    +
                    "如果係多咗漢字：嗰啲字唔喺原文入面，好可能你自己幫個 list "
                    "重新編號咗——唔好咁做，重複嘅編號照抄。\n"
                    "如果係標點太密：你差唔多逐個字都加咗符號。返去頁圖數清楚"
                    "真係印咗幾多個，冇印就唔好加。\n"
                    + ("如果係刪走咗字：本階段收到嘅文字必須保留，唔可以用格式化決定刪字。"
                       "家具已喺來源覆核處理；表格入面嘅格"
                       if furniture_reviewed else
                       "如果係刪走咗字：嗰段字兩個 OCR 引擎都讀到，即係頁上真係有。"
                       "你只准刪欄外書眉同頁碼，正文一個字（包括外文同數字）都唔准刪。表格入面嘅格")
                    + "(人名、職銜、籍貫嗰啲)係正文，唔係書眉，照留。\n\n"
                    "重新輸出：加句讀同結構，但一個漢字都唔准加，亦唔准刪正文。",
                    render_bytes, arguments.max_tokens, kind="format-retry")
            add(m)
            retry = strip_fence(retry)
            silent_retry = no_answer(retry, m)
            retry_notes: list[str] = []
            misplaced = None
            if placeholders:
                retry, misplaced, retry_notes = place_recovered_tables(retry, tables)
            retry_markers: list[dict[str, Any]] = []
            if not silent_retry and not misplaced:
                retry, retry_markers = restore_note_markers(sanctioned, retry, note_blocks)
            ok2, reason2 = ((False, "retry: " + silent_retry) if silent_retry
                            else (False, "retry: " + misplaced) if misplaced
                            else verify_characters(sanctioned, retry, removable))
            retry_feature_losses = formatter_losses(retry) if ok2 else []
            if retry_feature_losses:
                ok2, reason2 = False, "retry lost or displaced source features: " + ", ".join(retry_feature_losses)
            if ok2 and punct_locked and not punctuation_kept(locked_input(retry), retry):
                ok2, reason2 = False, "retry still changed punctuation"
            if ok2 and punctuation_density(retry) > density_limit(arguments):
                ok2, reason2 = False, "retry still over-punctuated"
            still = (deleted_runs(prose, retry, furniture=furniture, margin=margin_texts,
                                  known=known_heads, whole=resolved, prose_only=spans is not None) if ok2 else [])
            if still:
                # Keep the better of the two rather than blocking the page, and
                # record what went missing so a later pass can find it.
                dropped = still
                ok2, reason2 = False, "retry still deleted " + " / ".join(still[:2])
            gone_beside = beside_lost(retry) if ok2 else ""
            if gone_beside:
                ok2, reason2 = False, ("retry still deleted the text beside a rebuilt table "
                                       f"({gone_beside[:18]}…)")
            still_furniture = unexplained(retry) if ok2 else ""
            if still_furniture:
                ok2, reason2 = False, (f"retry still deleted {len(still_furniture)} character(s) outside "
                                       f"the page's furniture ({still_furniture[:18]}…)")
            if ok2 and retry.strip():
                formatted, ok, reason = retry, True, reason2 + " (after retry)"
                placement_notes, markers_restored = retry_notes, retry_markers
            else:
                retry_refusal = reason2 if not ok2 else "the retry answered nothing"
                rejected_retry = retry
        rejected_output = None if ok else formatted
        # What the accepted answer's tables lack of the table blocks it was
        # given (unexplained): a doubt, below, not a refusal.
        structure_table_lost = table_lost.get(formatted, "") if ok else ""
        # The marks both engines read in a refused grid's table block that the
        # accepted answer's tables carry beyond what it was given (table_marks_lock).
        structure_table_marks = ("".join(table_marks_allowed.get(formatted, Counter()).elements())
                                 if ok and punct_locked else "")
        if ok and tables and source_table_mapping_current:
            # droppedRuns reports what the formatter dropped, so it is taken
            # on the text the deletion guard passed: enforcement only puts a
            # written table's cells in the recovered order and direction, which
            # breaks the merged text's runs without dropping anything.
            guarded = formatted
            formatted, table_notes, tables_missing, tables_disputed = enforce_recovered_tables(
                formatted, tables)
            table_notes = placement_notes + table_notes
        if ok and (formatted.strip() or head_only):
            if not punct_locked:
                # The whole-page formatter wrote the marks (no block pass).
                formatted, found = mark_changes(formatted, 0, None, [], arguments)
                decision_changes.extend(found)
            # Switch --keep-arabic-numerals: a figure the text given held at its
            # gap that the structure pass left out is written back there (the
            # character checks count characters, and a figure is none).
            given = [kept for kept in figures_kept if "given" in kept]
            if given:
                formatted, restored = restore_arabic_figures(
                    format_input, formatted, [(kept["given"], kept["figure"], kept["after"]) for kept in given])
                for kept, how in zip(given, restored):
                    kept["structure"] = how
            # Each note marker the structure pass left out and the page has
            # again (restore_note_markers), sealed (decision 8: the printed
            # note labels are kept).
            decision_changes.extend({
                "decision": "textLoss", "rule": "note-marker-restored",
                "contextBefore": restored["contextBefore"], "contextAfter": restored["contextAfter"],
                "after": restored["marker"],
                "evidence": {"givenGap": restored["gap"], **({"moved": True} if restored.get("moved") else {})}}
                for restored in markers_restored)
            # Each table the structure pass wrote itself, turned the
            # benchmark's way where engine B's reading settles it, its order
            # and its lines (orient_written_tables); the recovered tables
            # were turned before the pass.  Sealed as the rebuilds are (D23),
            # and droppedRuns is taken on the answer as the guard passed it:
            # a turn breaks the answer's runs without dropping anything.
            # Each turn is a doubt: engine B reading the table band by band
            # gives the same counts for a table written right, and nothing
            # else on the page checks the turn.
            oriented, written_turns = orient_written_tables(
                formatted, tables, draft2, page_print_direction(blocks, image_size(render_bytes)),
                table_orientation_mode)
            for number, (as_written, now, how) in enumerate(written_turns, 1):
                table_orientation.append(f"structure pass table {number}: {how}")
                table_orientation_evidence.append(table_orientation_record(
                    as_written, now, how, render_bytes, table_orientation_mode))
                if now != as_written:
                    at = len(CJK(formatted[:formatted.find(as_written)]))
                    chars = CJK(formatted)
                    decision_changes.append({
                        "decision": "D23", "rule": "structure-table-oriented", "block": None,
                        "contextBefore": chars[max(0, at - 8):at],
                        "contextAfter": chars[at + len(CJK(as_written)):at + len(CJK(as_written)) + 8],
                        "engineA": "", "engineB": "", "after": now, "mergedOffset": None, "pageOffset": at,
                        "evidence": {"orientation": how[:300], "asWritten": as_written}})
                    doubts.append({"kind": "table-orientation-turned", "detail": (
                        f"STRUCTURE TABLE TURNED: structure pass table {number} ({how.split(' ')[0]}) was turned on "
                        "engine B's reading (its order and its lines), which reads the same for a table written "
                        "right that engine B read band by band, several cells to a line - check the table "
                        f"against the image: {how}")})
            if oriented != formatted:
                guarded = formatted if guarded is None else guarded
                formatted = oriented
            writer_text = formatted
            if tables_missing:
                # A table the engine structured and the checks vouched for is not
                # in the page as a table: the formatter wrote it as prose or left
                # it out.  deleted_runs cannot see this - table blocks are kept out
                # of its comparison - so the page is flagged here.
                doubts.append({"kind": "table-missing", "detail": (
                    "RECOVERED TABLE NOT IN OUTPUT: block(s) "
                    + ", ".join(str(layout_index(blocks, table_sources[i])) for i in tables_missing))})
            if tables_disputed:
                # The formatter's table and the recovered one disagree about the
                # layout (merged cells), or the formatter's holds text outside
                # its cells; the formatter's stands, and a person decides.
                doubts.append({"kind": "table-disputed", "detail": (
                    "RECOVERED TABLE LAYOUT DISPUTED: block(s) "
                    + ", ".join(str(layout_index(blocks, table_sources[i])) for i in tables_disputed))})
        elif content_recovery is not None:
            # Switch --content-fidelity: keep the latest image-reviewed
            # checkpoint after either formatter fails.  Reconstructing from
            # punctuated blocks or the older CJK merge would lose confirmed
            # additions and could reapply stale source-table mappings.  Final
            # coverage still checks this fallback.
            writer_text = expanded_input
            if (reviewing and any(r["kind"] == "table" for r in inventory["regions"])
                    and not table_spans(writer_text)):
                detail = {"code": "coverage-structure-mismatch", "path": "$.formatter",
                          "message": "both formatter candidates rejected for table ownership; fallback has no table"}
                fail_fidelity(writer_text, detail)
                fidelity_doubt("formatter", "both formatter answers were rejected and the image-reviewed text "
                                            "kept in their place has no table where the inventory found one")
            for kept in figures_kept:
                if "given" in kept:
                    kept["structure"] = "source-recovery fallback"
            doubts.append({"kind": "structure-rejected" if punct_locked else "formatter-rejected",
                           "detail": f"FORMATTER REJECTED: {reason}; image-reviewed input preserved"
                           + (f"; retry refused: {retry_refusal}" if retry_refusal else "")})
        elif punct_locked:
            # The structure pass failed but the per-block punctuation held: keep
            # it, one paragraph per block, furniture blocks left out.  A table
            # block is not furniture here: it becomes the table recovered from it,
            # or its own text when the table was refused - leaving it out, as this
            # once did, deleted every table on the page where no check could see
            # it (droppedRuns leaves table blocks out of its comparison).  The
            # table stands in only when it holds every character both engines
            # read in the block; otherwise the block's own text is kept and the
            # table is recorded missing.  What only engine B read there - its
            # reading of the folio or of a heading, which restatement places in
            # no cell (twelve characters on one page, all misreadings) - goes
            # with the block, and is recorded rather than dropped unseen.
            recovered_from = {}
            # A block a rebuilt table took in (D22) counts what its table had
            # to hold of it (absorbed_held).
            agreed = [absorbed_held.get(index, text)
                      for index, text in enumerate(agreed_block_text(blocks, agreed_segments))]
            left_out = []
            for position, (index, table) in enumerate(zip(table_sources, tables)):
                # A rebuilt table's region is its block and the blocks it took in (D22).
                region = [index] + [j for j, owner in absorbed_into.items() if owner == index]
                # With the text beside it, as the structure pass was given it.
                beside = edge_written.get(index, ("", ""))
                if Counter("".join(agreed[j] for j in region)) - Counter(CJK(table + "".join(beside))):
                    tables_missing.append(position)
                    continue
                recovered_from[index] = "\n\n".join(part for part in (beside[0], table, beside[1]) if part)
                extra = (Counter(CJK("".join(punctuated[j] for j in region)))
                         - Counter(CJK(recovered_from[index])))
                if extra:
                    left_out.append(f"block {layout_index(blocks, index)}: {''.join(extra.elements())[:20]}")
            # A block listing entries with leaders, one entry a paragraph
            # (listed: engine A's lines).
            writer_text = "\n\n".join(
                recovered_from.get(index, text.replace("\n", "\n\n") if index in listed else text)
                for index, (block, text) in enumerate(zip(blocks, punctuated))
                if absorbed_into.get(index) not in recovered_from and (index in recovered_from or (CJK(text) and (
                    block["label"] == "table" or block["label"] not in FURNITURE_LABELS))))
            # Switch --keep-arabic-numerals: a figure written into a block
            # this leaves out (one with no character: a contents entry's
            # number on a line of its own) is not in the page.
            for kept in figures_kept:
                if kept.get("placed") in FIGURE_IN_BLOCK and not CJK(punctuated[kept["block"]]):
                    kept["structure"] = "block left out"
            doubts.append({"kind": "structure-rejected",
                           "detail": f"STRUCTURE PASS REJECTED: {reason}"
                                     + (f"; retry refused: {retry_refusal}" if retry_refusal else "")})
            if tables_missing:
                doubts.append({"kind": "table-missing", "detail": (
                    "block text kept instead of the recovered table (the table lacks "
                    "characters both engines read there): block(s) "
                    + ", ".join(str(layout_index(blocks, table_sources[i])) for i in tables_missing))})
            if left_out:
                doubts.append({"kind": "table-text-left-out", "detail": (
                    "characters only engine B read in a table block, not in its "
                    "table, left out: " + "; ".join(left_out))})
        else:
            # Refuse invented characters; keep the merged text unformatted -
            # with no block spans in this mode, there is nowhere to put the
            # recovered tables back, so they are all recorded as missing.
            writer_text = resolved
            # Switch --keep-arabic-numerals: the figures the formatter was
            # given are not in the merged text (characters only).
            for kept in figures_kept:
                if "given" in kept:
                    kept["structure"] = "formatter rejected"
            doubts.append({"kind": "formatter-rejected", "detail": f"FORMATTER REJECTED: {reason}"
                           + (f"; retry refused: {retry_refusal}" if retry_refusal else "")})
            tables_missing = list(range(len(tables)))
            # The page is the merged text with no marks at all, not a page the
            # formatter punctuated whole: that doubt no longer describes it.
            doubts[:] = [d for d in doubts if d["kind"] != "punctuation-whole-page"]
        # Every run of dots the page keeps is 「……」 (D4).  The block passes'
        # and the whole-page formatter's are written so and sealed where they
        # are punctuated; one a structure pass wrote with another number of
        # dots (its lock counts a run as one mark) or a recovered table holds
        # is written so here, and sealed as rewritten.
        writer_text, found = leader_rewrites(writer_text)
        decision_changes.extend(found)
        if spans is not None:
            # A block a rebuilt table took in (D22) counts what its table had
            # to hold of it: both engines read it, only not in one order.
            # And the blocks beside a table rebuilt without them (beside_table).
            table_characters = table_characters_lost(
                blocks, [absorbed_held.get(index, text)
                         for index, text in enumerate(agreed_block_text(blocks, agreed_segments))],
                writer_text, book_heads, book_furniture, beside_table)
            # And what the accepted structure pass's tables lack of the table
            # blocks it was given, beyond those (split_table_losses): the
            # page's prose can hide a table's loss from the count above (a 兩
            # of a merged cell left out, the prose holding 兩 too).
            more = Counter(structure_table_lost) - Counter(table_characters)
            unnamed = ""
            for char in structure_table_lost:
                if more[char] > 0:
                    more[char] -= 1
                    unnamed += char
            read_by = ("engine A read (the page's only reading)" if order_text == "A"
                       else "both engines read")
            if table_characters:
                doubts.append({"kind": "table-characters-lost", "detail": (
                    f"TABLE CHARACTERS NOT IN OUTPUT: {len(table_characters)} "
                    f"character(s) {read_by} in the table blocks"
                    + (" and the blocks beside a table rebuilt without them ("
                       + ", ".join(str(layout_index(blocks, j)) for j in sorted(beside_table)) + ")"
                       if beside_table else "")
                    + f" ({table_characters[:30]})"
                    + (f"; and {len(unnamed)} more character(s) of the table blocks the structure pass was "
                       f"given, which its tables lack ({unnamed[:30]})" if unnamed else ""))})
            elif unnamed:
                doubts.append({"kind": "table-characters-lost", "detail": (
                    f"TABLE CHARACTERS NOT IN OUTPUT: {len(unnamed)} character(s) of the table blocks the "
                    f"structure pass was given, which its tables lack ({unnamed[:30]}) - check the table "
                    "against the image")})
            table_characters += unnamed
        if table_edge:
            # The table's edge text (table_edges): each table whose block's span
            # held text beside its cells, or whose restatement the rule
            # changed, sealed in decisionChanges; the text written beside a
            # table is a doubt, whether the page still holds it or not.
            merged_cjk, page_cjk = CJK(resolved), CJK(writer_text)

            def read_there(lo: int, hi: int) -> dict[str, str]:
                """What each engine read of the merged text's [LO, HI): its own
                characters where its reading's place there is exact."""
                return {name: "".join(placed[at] for at in range(lo, hi) if at in placed)
                        for name, placed in zip(("engineA", "engineB"), edge_placed)}

            # The lines of engine B's that are cells of its own tables.
            b_cells = engine_b_table_lines(draft2) if draft2 is not None else set()

            def not_written(held: Mapping[str, Any]) -> str:
                """Why a piece of the edge text was held back (edge_verdicts)."""
                piece = held["text"]
                head = f"「{piece[:24]}{'…' if len(piece) > 24 else ''}」 not written - "
                if held.get("with"):
                    return (head + "one character or figures alone, which say nothing by themselves, "
                            f"go with 「{held['with'][:12]}」 beside them")
                if held.get("tableCells"):
                    return (head + "it stands whole in the table's own cells, and engine B read it more often "
                            "than engine A, who read it there (page/engine A/engine B: "
                            + ", ".join(held["stretches"][:1]) + "): engine B's second reading of the cells, "
                            "it may be")
                if held.get("wholeBlock") is not None:
                    return (head + f"it is the whole of block {held['wholeBlock']}, which engine A alone read "
                            "there (page/engine A/engine B: " + ", ".join(held["stretches"][:1]) + ")")
                return (head + f"{held['share']:.0%} of it is in stretches the page would hold more often "
                        "than either engine read them (page/engine A/engine B: "
                        + ", ".join(held["stretches"][:4]) + "), each standing elsewhere on the page as "
                        "engine A's reading alone")

            beside_notes: list[str] = []
            for index, found in sorted(table_edge.items()):
                layout = layout_index(blocks, index)
                written = edge_written.get(index, ("", ""))
                sides = []
                elsewhere = found.get("heldElsewhere") or {}
                for side, (lo, hi), text in (("before", found["lead"], written[0]),
                                             ("after", found["trail"], written[1])):
                    if not text and side not in elsewhere:
                        continue
                    read = read_there(lo, hi)
                    kept = bool(text) and CJK(text) in page_cjk
                    # What of it engine B read in its own table's cells: its
                    # reading of the table's first (last) cells, it may be,
                    # set beside engine A's in the table.
                    in_cells = ("".join(merged_cjk[at] for at in range(lo, hi) if b_lines[at] in b_cells)
                                if b_lines is not None and b_cells else "")
                    sides.append({"side": side, "written": text, "mergedOffset": lo, **read, "onPage": kept,
                                  **({"heldElsewhere": elsewhere[side]} if side in elsewhere else {}),
                                  **({"inEngineBTable": in_cells} if in_cells else {})})
                    shown = merged_cjk[lo:hi]
                    state = ("written" if kept else "written, and not on the page: the structure pass left it out")
                    beside_notes.append(
                        f"block {layout}: 「{shown[:24]}{'…' if len(shown) > 24 else ''}」 {side} the table "
                        f"({found['source']}; engine A read {len(read['engineA'])} of its {hi - lo} character(s) "
                        f"there, engine B {len(read['engineB'])}): "
                        + ("; ".join(not_written(held) for held in elsewhere[side][:4])
                           + (f"; the rest {state} (「{CJK(text)[:24]}」)" if text else "")
                           if side in elsewhere else state)
                        + (f"; engine B read 「{in_cells[:24]}{'…' if len(in_cells) > 24 else ''}」 of it in its own "
                           "table's cells - its reading of the table's own cells, it may be, where the table holds "
                           "engine A's" if in_cells else ""))
                if not any(entry["written"] for entry in sides):
                    # Nothing written beside the table: the page's text is the table's.
                    continue
                at = sides[0]["mergedOffset"]
                decision_changes.append({
                    "decision": "textLoss", "rule": "table-edge-text",
                    "block": layout, "contextBefore": merged_cjk[max(0, at - 8):at],
                    "contextAfter": merged_cjk[at:at + 8],
                    "engineA": "".join(entry["engineA"] for entry in sides),
                    "engineB": "".join(entry["engineB"] for entry in sides),
                    "after": "\n\n".join(part for part in (written[0], found["restated"], written[1]) if part),
                    "mergedOffset": at,
                    "evidence": {"source": found["source"], "beside": sides,
                                 "foundOutsideBlock": found["outside"]}})
            if beside_notes:
                doubts.append({"kind": "table-edge-text", "detail": (
                    "TEXT AT A TABLE'S EDGE: the table block's merged text holds it before the table's first "
                    "cell or after its last, and no cell of the table does: " + "; ".join(beside_notes[:6])
                    + " - check it against the image")})
        # The steps below each passed or failed silently before: a refused or
        # miscounted punctuation pass left its stretch bare or short, and a
        # merge that placed one reading twice kept both, and the page was
        # sealed clean either way (punctuation_doubts, extra_copies).
        doubts.extend(punctuation_doubts(punctuation_passes))
        doubts.extend(extra_copy_doubts(writer_text, draft, draft2))
    # Switch --loop-detector: each call with a rung given up in a loop.
    decision_changes.extend(loop_cut_changes(loop_cuts))
    # Switch --keep-arabic-numerals: each figure the merge kept, where it wrote
    # the figure or left out engine B's reading of it; and a doubt where it
    # could write neither the figure nor what was there before.
    unwritten = []
    for kept in figures_kept:
        # Not in the page after all: the structure pass left it out where
        # its place was not found, or the page fell back to text without it
        # (the formatter rejected, or its block left out).
        lost = kept.get("structure") in ("unplaced", "formatter rejected", "block left out")
        written = kept.get("placed") in FIGURE_WRITTEN and not lost
        kept["record"].update(placed=kept.get("placed"), **({"structure": kept["structure"]}
                                                           if "structure" in kept else {}))
        if written or kept["replaced"]:
            # A change: the figure written, or characters the merge would
            # write at its gap without it written as nothing.
            decision_changes.append({
                "decision": "keepArabicNumerals", "rule": "arabic-figure-kept",
                "block": kept["record"]["block"], "contextBefore": kept["record"]["contextBefore"],
                "contextAfter": kept["record"]["contextAfter"], "engineA": kept["figure"],
                "engineB": kept["engineB"],
                "after": kept["figure"] if written else "", "mergedOffset": kept["merged"],
                "evidence": {"how": kept["how"], "placed": kept.get("placed"),
                             **({"structure": kept["structure"]} if "structure" in kept else {})}})
        if kept.get("placed") not in FIGURE_IN_BLOCK or lost:
            unwritten.append(f"…{kept['record']['contextBefore'][-4:]}[{kept['figure']}]"
                             f"{kept['record']['contextAfter'][:4]}… ({kept.get('structure') or kept.get('placed')})")
    # A figure or mark engine A read in brackets that the merge did not keep
    # (bracketed_pairs): the bracket rule does not write its brackets alone
    # (unpaired_brackets), so where the page holds no copy of it it is lost,
    # and said so (bracketed_unwritten).
    unkept = bracketed_unwritten(page_bracketed, figures_kept, writer_text or "", figure_why)
    if unwritten or unkept:
        doubts.append({"kind": "arabic-figure-unwritten", "detail": (
            "ARABIC FIGURE NOT WRITTEN: "
            + "; ".join(part for part in (
                f"{len(unwritten)} figure(s) engine A read, which the merge kept "
                "(--keep-arabic-numerals), could not be written at their place: " + "; ".join(unwritten[:6])
                if unwritten else "",
                f"{len(unkept)} figure(s) or mark(s) engine A read in brackets, which the merge did not keep, "
                "are not on the page (the bracket rule writes no pair alone): " + "; ".join(unkept[:6])
                if unkept else "") if part)
            + " - check the numbers against the image")})

    # Strip any PaddleX synthetic img_in_* tags before coverage review and sealing.
    writer_text = strip_paddlex_img_tags(writer_text)

    # Switch --content-fidelity: final full-image coverage is after table
    # enforcement and every mechanical rewrite.  A rejected formatter fallback
    # is still a candidate, never evidence of full-page coverage.  What it
    # cannot settle is a doubt on the page (image_review), sealed as such.
    if reviewing:
        writer_text, coverage_review = image_review(writer_text, "coverage-review")
    final_tables = [writer_text[a:b] for a, b in table_spans(writer_text)]
    table_mapping_current, table_correspondence = _content_fidelity.refresh_table_mapping(tables, final_tables)
    table_mapping_current = source_table_mapping_current and table_mapping_current
    if table_mapping_current:
        tables = final_tables
    for evidence in table_orientation_evidence:
        evidence["status"] = ("active" if evidence.get("outputSha256") in
                              {sha256_bytes(t.encode("utf-8")) for t in final_tables} else "historical")
        if coverage_review is not None:
            evidence["finalCoverageSha256"] = coverage_review["result_sha256"]
    model_sites = variant_model_sites(disagreements, arguments)
    # Issue #8, item 4: note numbers the rules leave unsettled, read off the
    # page by the answerer for the repair's N9 (note_question_reason).
    note_numbers = None
    note_question = note_question_reason(draft, draft2, writer_text, blocks)
    if note_question and client is not None and render_bytes:
        note_numbers, used = ask_note_numbers(client, render_bytes, note_question)
        add(used)
    record: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "scanPage": job.scan_page,
        "status": "complete",
        "textAuthority": TEXT_AUTHORITY,
        "generatedAt": utc_now(),
        "model": client.model,
        "reasoningEffort": client.effort,
        **effort_policy_record(client),
        "punctuationMode": getattr(arguments, "punctuation_mode", "auto"),
        "punctuationModeEffective": punctuation_mode(arguments, client),
        "punctuationFusion": punctuation_fusion_summary(punctuation_passes),
        "renderFile": str(job.render),
        "auditorClean": not doubts,
        "mode": mode,
        # Switch --content-fidelity: its evidence, and whether it was settled
        # ("complete") or left a doubt ("doubt").  Absent with it off.
        **({"contentFidelityContract": _content_fidelity.CONTRACT_VERSION,
            "contentFidelityStatus": fidelity_status,
            "pageInventory": inventory or None,
            "contentRecovery": content_recovery,
            "coverageReview": coverage_review} if fidelity_on else {}),
        "engineDisagreements": disagreements,
        "auditorReport": "; ".join(d["detail"] for d in doubts),
        "doubts": doubts,
        "adjudications": adjudications,
        "punctuationPasses": punctuation_passes,
        "engineBrackets": engine_brackets,
        "profileFurnitureDropped": profile_furniture,
        # Genuine substitutions only - both engines produced text - the same
        # population the profile measured, so the two can be compared.
        "engineTally": dict(Counter(a.get("winner", "undecided") for a in adjudications
                                    if a.get("trigger") == "engine-disagreement"
                                    and CJK(a.get("draft_reading", "")) and CJK(a.get("writer_reading", "")))),
        "variantUnruled": variant_unruled,
        # The sites the model's vote decided (variant_model_sites), on books it ruled.
        **({"variantRuledByModel": model_sites} if model_sites is not None else {}),
        # An engine-A block whose reading was a repetition loop engine B does not
        # share, set aside (quarantine_looped_block): kept here, as a page's
        # discarded reading is, so the judgement can be undone.
        "engineALoopsDropped": loops_dropped,
        # Each text change a decision-dependent rule made (see decision_changes).
        "decisionChanges": decision_changes,
        # The note-numbers answer (ask_note_numbers), where it was asked.
        **({"noteNumbers": note_numbers} if note_numbers is not None else {}),
        # Switch --keep-arabic-numerals: every figure engine A read that may
        # number something (arabic_figures), kept or not and why.  Only when on.
        **({"arabicFigures": arabic_figures_seen} if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Each lopsided replacement split (split_lopsided), and what was written for it.
        "lopsidedSplits": [{"engineA": split["segment"]["a"], "engineB": split["segment"]["b"],
                            "contextBefore": split["segment"]["before"], "contextAfter": split["segment"]["after"],
                            "how": split["how"], "written": split.get("written")} for split in lopsided],
        # Places where the merge dropped more than ANSWER_DELETION_LIMIT characters
        # one engine read that the merged text holds nowhere, and the rules that
        # settled them (readings_dropped): each a reading-dropped doubt.
        "readingsDropped": readings_lost,
        "formatterRejectedOutput": rejected_output,
        "formatterRetryReason": format_retry_reason,
        # The text the structure pass and its retry were given
        # (structure_input_record); None where no structure pass ran.
        "structureInput": structure_input,
        # The retry's own refusal and answer, when it was refused as well.
        "formatterRetryRefusal": retry_refusal,
        "formatterRejectedRetry": rejected_retry,
        "needsResearch": front_matter_research(blocks, resolved) if mode.startswith("merge") else [],
        "usage": metrics,
        "punctuationDensity": round(punctuation_density(writer_text), 2),
        "droppedRuns": (deleted_runs(prose if prose is not None else resolved,
                                     guarded if guarded is not None else writer_text,
                                     furniture=getattr(arguments, "furniture", frozenset()),
                                     margin=margin_furniture(blocks),
                                     known=sorted(set(profile_furniture)), whole=resolved,
                                     prose_only=prose is not None and spans is not None)
                        if not transcribing else []),
        # Evidence, not decoration: a refused table is a page where the engine
        # found a grid we would not vouch for, and someone may want to look.
        # A merge-time loss is invisible to droppedRuns, which only compares the
        # merged text against the formatted text.  This compares the merged text
        # against what each engine actually read.  A merge longer than both
        # engines is NOT a doubt by itself: the merge keeps text either engine
        # read alone, so it is longer on many pages (measured: 31 of the 77
        # sealed pages that record it, by 1 to 186 characters).  Every page of
        # the 77 with an extra copy is among the 31, but only 9 of the 31 hold
        # one, and a page 28 characters over held none while one 3 over did.
        # Text placed twice is looked for directly (extra_copies).
        "engineCoverage": {
            "engineA": engine_a_characters,
            "engineB": engine_b_characters,
            "merged": len(CJK(resolved)) if not transcribing else None,
        },
        "tablesRecovered": len(tables),
        # Each recovered table with the block it came from (its layout index,
        # as the stitch reads it against engine A's layout file), as it is to
        # stand on the page (a whole-page pass was offered it with its label
        # cells as read, cells_as_read); tablesEnforced, tablesMissing and
        # tablesDisputed say what became of it on the page.
        "tables": [{"block": layout_index(blocks, block),
                    "format": "html" if "<table" in table else "markdown",
                    "text": table} for block, table in zip(table_sources, tables)],
        "tablesEnforced": table_notes,
        "tablesMissing": [layout_index(blocks, table_sources[i]) for i in tables_missing],
        "tablesDisputed": [layout_index(blocks, table_sources[i]) for i in tables_disputed],
        # Characters both engines read in the table blocks that the page lacks -
        # also for a refused table, which the formatter rebuilds
        # (table_characters_lost).
        "tableCharactersLost": table_characters,
        # The marks the structure pass wrote in the tables it built from the
        # image that its punctuation lock let through because both engines
        # read them in a table block whose grid was refused (table_marks_lock).
        "tableMarksAllowed": structure_table_marks,
        "tableOrientation": table_orientation,
        "tableOrientationEvidence": table_orientation_evidence,
        "tableMappingCurrent": table_mapping_current,
        "tableCorrespondence": table_correspondence,
        "recoveryTableCorrespondence": recovery_table_correspondence,
        "finalTables": [{"text": table, "sha256": sha256_bytes(table.encode("utf-8")),
                         "format": "html" if "<table" in table else "markdown"} for table in final_tables],
        # Figures whose group marks were taken from engine B (restate_table).
        "tableFigureGrouping": table_grouping,
        # The label cells asked which way they read, with both answers
        # (fix_cell_direction).
        "cellDirection": cell_direction,
        # Blocks engine B reads in another order, nominated as tables the
        # layout model missed (nominate_tables), with how many of their
        # disagreements settle_by_union settled and how many it left to the
        # usual way; and every table rebuilt from the image with its
        # characters locked (rebuild_table): where from, how many characters
        # were allowed and required, the outcome.
        "tableNominations": [dict(nomination, block=layout_index(blocks, nomination["block"]))
                             for nomination in table_nominations],
        "tablesRebuilt": table_rebuilds,
        "tablesRejected": tables_rejected,
        "degraded": bool(m.get("degraded")),
        "seconds": round(time.monotonic() - started, 1),
        # Where the page's time went, per kind of model call.  Seconds are the
        # caller's wall time per call (they overlap when calls run in parallel),
        # waitSeconds the time spent queued for a free lane.
        "calls": summarize_calls(ledger),
    }
    record["provenance"] = {
        **sealed_inputs(job),
        **decision_provenance(arguments),
        "markdownSha256": sha256_bytes(writer_text.encode("utf-8")),
    }
    record["provenance"]["jsonPayloadSha256"] = sha256_bytes(canonical_json_bytes(record))
    seal_page(job, writer_text.encode("utf-8"), record, events=getattr(arguments, "packet_events", None))
    return record


@contextlib.contextmanager
def output_lock(directory: Path) -> Iterator[None]:
    """An exclusive lock on OUTPUT_DIR/.table-stitch.lock.

    The table stitch (stitch_tables.py) holds it from reading the sealed pages
    to writing its plan; a page's seal is written under it too.  Otherwise a
    page proofread again by another run sharing the directory could be sealed
    inside the stitch's window, and the stitch would write a plan built from
    the page before it over the new one: the new proofreading lost, and the
    merged table carrying the old page's cells.
    """
    with open(directory / ".table-stitch.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def seal_page(job: PageJob, markdown: bytes, record: Mapping[str, Any],
              *, events: _packet_events.PacketEvents | None = None) -> None:
    """Write a page's Markdown and its seal, under the output directory's lock."""
    with output_lock(job.out_json.parent):
        job.out_md.write_bytes(markdown)
        job.out_json.write_bytes(canonical_json_bytes(record))
    if events is not None:
        events.record("seal", artifact="page", scan_page=job.scan_page)


class AgentTransportParser(argparse.ArgumentParser):
    """Which transport answers the model questions.

    Packets, answered by the calling agent, are the default.  HTTP to a local
    (or other) OpenAI-compatible server with --backend http: the endpoint is
    --endpoint, else PROOFREAD_ENDPOINT; the model --model, else
    PROOFREAD_MODEL, else the server's first listed model (resolve_http_model,
    when the client is made).  When the calling agent is itself the local
    proofreading model (agent_is_local_model), HTTP is the default and
    packets are refused: the page images must not enter its context.
    """

    def parse_args(self, args=None, namespace=None):
        arguments = super().parse_args(args, namespace)
        try:
            local, why = agent_is_local_model(arguments.model, arguments.endpoint)
        except ProofreadError as error:
            self.error(str(error))
        if arguments.backend is None:
            arguments.backend = "http" if local else "packet"
        if arguments.backend == "http":
            endpoint = arguments.endpoint if arguments.endpoint is not None else os.environ.get("PROOFREAD_ENDPOINT")
            if not endpoint or not endpoint.strip():
                self.error("--backend http needs an endpoint: pass --endpoint URL or set PROOFREAD_ENDPOINT"
                           + (f" (the calling agent is the local proofreading model: {why})" if local else ""))
            arguments.endpoint = endpoint.strip()
            # None: the server's first listed model (resolve_http_model).
            arguments.model = arguments.model or os.environ.get("PROOFREAD_MODEL") or None
        else:
            if local:
                self.error(f"the calling agent is the local proofreading model ({why}): --backend packet would "
                           "put the page images into the agent's own context. Run with --backend http (the "
                           f"default for this agent); set {LOCAL_AGENT_ENV}=0 if this agent is not that model")
            if arguments.endpoint is not None:
                self.error("--endpoint requires --backend http")
            arguments.model = arguments.model or DEFAULT_MODEL
        arguments.agent_is_local_model = local
        return arguments


def build_parser() -> argparse.ArgumentParser:
    parser = AgentTransportParser(
        description=__doc__, epilog=_packet_events.HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("render_directory", type=Path)
    parser.add_argument("draft_directory", type=Path, help="OCR runner output (page-NNNN.txt/.json)")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--profile", type=Path,
                        help="book-profile.json from scripts/book_profile.py (default: "
                             "book-profile.json next to the draft directory, if present)")
    parser.add_argument("--variant-rulings", type=Path,
                        help="per-pair variant-form rulings (default: variant-rulings.json next "
                             "to the profile, if present)")
    parser.add_argument("--draft2-directory", type=Path,
                        help=f"engine B's drafts ({ENGINE_B_NAME}) for alignment and disagreement checks (default: "
                             "the folder the book profile's sources name, else "
                             f"{ENGINE_B_FOLDER}/ beside the draft directory; none found is an error unless "
                             "--single-engine)")
    parser.add_argument("--single-engine", action="store_true",
                        help="proofread from the draft directory alone: each page transcribed from its image, "
                             "the one draft its hint (two engines are the default)")
    parser.add_argument("--allow-missing-engine-b", action="store_true",
                        help="on a two-engine run, transcribe a page whose engine B draft is missing from "
                             "engine A's draft alone, with an engine-draft-missing doubt (default: such a page "
                             "is not proofread and counts as failed, since engine B's OCR may still be running "
                             "or was interrupted)")
    selector = parser.add_mutually_exclusive_group(required=False)
    selector.add_argument("--pages", help="comma-separated pages/ranges, e.g. 1,3-5")
    selector.add_argument("--first-page", type=int)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"pages processed concurrently (default {DEFAULT_WORKERS}); "
                             "packet answers may be supplied sequentially or by optional workers")
    parser.add_argument("--backend", choices=("packet", "http"),
                        default=None,
                        help="model transport: 'packet' (default) writes checkpoint-replay wave bundles "
                             "for the invoking agent or optional workers and exits 10 until answered; "
                             "'http' asks an OpenAI-compatible server (--endpoint, else PROOFREAD_ENDPOINT). "
                             "When the calling agent is the local proofreading model "
                             f"({LOCAL_AGENT_ENV}=1, or Qwen Code's {QWEN_CODE_MODEL_ENV} equal to "
                             "PROOFREAD_MODEL served at a loopback PROOFREAD_ENDPOINT), 'http' is the default "
                             "and 'packet' is refused")
    parser.add_argument("--packets-dir", type=Path, default=None,
                        help="packet checkpoint directory for --backend packet "
                             "(default: <output_directory.parent>/packets)")
    parser.add_argument("--max-bundles", type=int,
                        default=int(os.environ.get("PROOFREAD_MAX_BUNDLES") or 8),
                        help="question bundle files per wave in --backend packet (default 8; more when "
                             "--max-items-per-bundle needs them)")
    parser.add_argument("--max-items-per-bundle", type=int,
                        default=int(os.environ.get("PROOFREAD_MAX_ITEMS_PER_BUNDLE") or DEFAULT_MAX_ITEMS_PER_BUNDLE),
                        help="questions one packet bundle holds at most (env PROOFREAD_MAX_ITEMS_PER_BUNDLE; "
                             f"default {DEFAULT_MAX_ITEMS_PER_BUNDLE}): a wave with more than --max-bundles times "
                             "this many is written in more bundles, so no bundle hands one answerer hundreds "
                             "of page images")
    parser.add_argument("--punctuation-mode", choices=("auto", "split", "fused"), default="auto",
                        help="census and punctuation questions: auto preserves packet fusion and HTTP split; "
                             "split asks independently; fused asks together with guarded split fallback. "
                             "The effective mode is sealed and a different mode cannot reuse the page")
    parser.add_argument("--table-orientation", choices=("auto", "as-printed"),
                        default=os.environ.get("PROOFREAD_TABLE_ORIENTATION", "auto"),
                        help="table orientation policy: 'auto' (default) puts vertical-column records into "
                             "logical rows; 'as-printed' keeps the printed row/column grid without transposing")
    parser.add_argument("--endpoint", default=None,
                        help="HTTP endpoint for --backend http (default: PROOFREAD_ENDPOINT)")
    parser.add_argument("--model", default=None,
                        help="HTTP model (default: PROOFREAD_MODEL, else the server's first model at "
                             "{endpoint base}/v1/models) or packet provenance label")
    parser.add_argument("--content-fidelity", choices=("off", "on"), default="off",
                        help="content-fidelity v2: 'on' asks three more whole-page image questions per page "
                             "(an inventory of the page, a content-recovery review before formatting, a "
                             "coverage review of the final text); whatever they cannot settle becomes a "
                             f"{_content_fidelity.DOUBT_KIND} doubt on the page.  Default 'off': none of the "
                             "three is asked and pages are sealed without their record")
    parser.add_argument("--reasoning-effort", default=os.environ.get("PROOFREAD_EFFORT", DEFAULT_EFFORT),
                        help=f"low|medium|xhigh, or 'none' to omit (default {DEFAULT_EFFORT})")
    parser.add_argument("--effort-policy", default=os.environ.get(EFFORT_POLICY_ENV, ""), metavar="KIND=RUNG,...",
                        help="with --backend http, the rung of the effort ladder each kind of question named "
                             "is first asked on, in place of --reasoning-effort, which stays the highest rung "
                             "of any call: KIND=RUNG pairs with commas between, e.g. census=low,format=low; "
                             f"KIND one of {', '.join(EFFORT_KINDS)}; RUNG xhigh, medium, low or none.  A kind "
                             "not named, a retry among them, keeps --reasoning-effort.  For measuring which "
                             "kinds may begin lower; recorded with each page (effortPolicy), never compared on "
                             f"a resume.  Default: none (env: {EFFORT_POLICY_ENV}); with packets it is not used")
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--max-adjudications", type=int, default=24,
                        help="per-page cap on escalated forced-choice calls")
    parser.add_argument("--no-learned-heads", dest="learned_heads", action="store_false", default=True,
                        help="switch the learned running head off (on by default since the round-4 benchmark, "
                             "the user's decision of 2026-09-30; ROUND4_DEFAULTS): the running head learned by "
                             "where it is printed "
                             "(learn_heads): the profile's (book_profile.py, unless --no-learned-heads there) or, "
                             "for a profile measured without it, learned from engine A's layout of every page in "
                             "the draft directory - "
                             "the stems the profile measured, the labels printed beside them on several pages "
                             "and their families (a volume's number), the folio sequence, where the band stands "
                             "on odd and even pages.  A block in the page's band whose merged text is all the "
                             "learned head (a stem, a label, the folio beside them; the folio alone in an "
                             "anchor's column or row) is left out before punctuation and so out of the structure "
                             "pass and its fallback, whatever its layout label; text only one engine read that "
                             "is the learned head with a stem, at an edge of engine A's blocks or between two, is "
                             "left out before 裁決")
    parser.add_argument("--no-keep-arabic-numerals", dest="keep_arabic_numerals", action="store_false",
                        default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) writing "
                             "an Arabic figure engine A read - a list item's (1), "
                             "a contents entry's 12, a heading's 5. - into the merged text at its place where "
                             "it is the only reading of its print (arabic_figure_kept): engine B read nothing "
                             "there, or marks, or glyphs this book's drafts show engine B writing for figures "
                             "or marks, which are written as nothing (figure_aliases, mark_aliases), or 裁決 "
                             "read the figure or no character there; and only a figure that continues a "
                             "numbering, the figure engine A read before it one less or the one after it one "
                             "more (figures_confirmed), or a contents entry's page number (ending a line "
                             "that holds a leader).  No character both engines read is replaced")
    parser.add_argument("--no-loop-detector", dest="loop_detector", action="store_false", default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) the loop "
                             "detector: stream every model request and watch its reasoning as it "
                             "comes: when the reasoning ends in one passage written three times back to back, "
                             "word for word, at least 800 characters repeating (_loop_watch.RULE), the "
                             "request is given up there and the effort ladder treats it exactly as a cut at "
                             "max_tokens - the next rung, never the same rung again (Client.ask_answering); "
                             "the answer after the reasoning is never watched.  Measured on 68 top-effort "
                             "replays: every loop found, none of the 49 that ended with an answer fired on")
    parser.add_argument("--no-neighbour-guard", dest="neighbour_guard", action="store_false", default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) the "
                             "neighbour guard: do not write a 裁決 answer that is neither engine's reading "
                             "and is the text the merged text has within NEIGHBOUR_WINDOW characters of the "
                             "spot, which neither engine read at the spot (neighbour_copy): 裁決 read the "
                             "neighbour, not the spot.  The reading of the engine the page falls back on stands, "
                             "as that engine read it (on a D3 question, the reading of the engine that read the "
                             "text), with a neighbour-copy doubt")
    parser.add_argument("--page-concurrency", type=int, default=DEFAULT_PAGE_CONCURRENCY,
                        help=f"model calls one page runs at once: its 裁決, its "
                             f"punctuation passes, its shape re-checks "
                             f"(default {DEFAULT_PAGE_CONCURRENCY}; 1 = one after another)")
    parser.add_argument("--max-inflight", type=int,
                        default=int(os.environ.get("PROOFREAD_MAX_INFLIGHT") or 0) or None,
                        help="requests on the wire at once across all pages (env "
                             "PROOFREAD_MAX_INFLIGHT; default --workers). Set it to the "
                             "server's --max-concurrency: a request the server cannot "
                             "admit waits in its queue and fails after its pending timeout"),
    parser.add_argument("--same-print-marks", action="store_true", dest="same_print_marks", default=False,
                        help="also take the engines' marks, with no punctuation question, for a block whose "
                             "gaps hold print (figures, Latin text, note markers) that both engines read alike; "
                             "off by default")
    parser.add_argument("--same-marks-any-form", action="store_true", dest="same_marks_any_form", default=False,
                        help="also take the engines' marks, with no punctuation question, for a block where the two "
                             "engines differ only in the form of a mark or of print: ASCII for full-width marks, "
                             "straight for curly quotation marks, the square bracket's three shapes; off by default")
    parser.add_argument("--unmarked-page-sample", action="store_true", dest="unmarked_page_sample", default=False,
                        help=f"on a page neither engine read more than {UNMARKED_PAGE_MARKS} punctuation marks on, "
                             f"ask the punctuation question for the {UNMARKED_SAMPLE} longest blocks first and, "
                             "when every one is answered as printed without marks, take the page's other "
                             "blocks without marks, unasked; off by default")
    parser.add_argument("--no-unread-table-characters", action="store_true",
                        help="the user's headerCharacters ruling (2026-09-27) off: a rebuilt table that "
                             "writes a character no engine read in its region is refused, as before, "
                             f"instead of standing when there are at most {UNREAD_TABLE_LIMIT} such places "
                             "and 裁決 reads the same character on a crop of each (confirm_unread)")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="sampling temperature (default 0: greedy, and therefore "
                             "reproducible). Raise it only to explore alternatives; "
                             "any measurement taken above 0 is not comparable.")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="seconds a read of the server's answer may wait: the whole answer of a request "
                             "that is not streamed, the next event of one that is (the loop detector, on unless "
                             "--no-loop-detector), so a streamed request may take longer in all (default 1800)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def rendered_pages(render_directory: Path) -> list[int]:
    """Every scan page that has a page-NNNN.png render; the default selection."""
    pages: list[int] = []
    for path in render_directory.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d{4})\.png", path.name)
        if match:
            pages.append(int(match.group(1)))
    return sorted(pages)


def parse_pages(arguments: argparse.Namespace, render_directory: Path) -> list[int]:
    if arguments.pages is not None:
        if arguments.last_page is not None:
            raise ProofreadError("--last-page cannot be combined with --pages")
        pages: set[int] = set()
        for token in arguments.pages.split(","):
            token = token.strip()
            if not token:
                continue
            match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", token)
            if not match:
                raise ProofreadError(f"invalid page selector: {token!r}")
            first = int(match.group(1))
            last = int(match.group(2) or first)
            if last < first:
                raise ProofreadError(f"descending range: {token!r}")
            pages.update(range(first, last + 1))
        if not pages:
            raise ProofreadError("no pages selected")
        return sorted(pages)
    if arguments.first_page is None and arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ProofreadError(f"no page-NNNN.png renders in {render_directory}")
        return pages
    if arguments.first_page is None:
        raise ProofreadError("--last-page needs --first-page")
    if arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ProofreadError(f"no page-NNNN.png renders in {render_directory}")
        arguments.last_page = pages[-1]
    if arguments.last_page < arguments.first_page:
        raise ProofreadError("--last-page must be at least --first-page")
    return list(range(arguments.first_page, arguments.last_page + 1))


def short_document_engines(pages: int) -> dict[str, Any]:
    """The engine section of a short document (at most SHORT_DOC_MAX_PAGES
    pages): no calibration, every disagreement adjudicated where it is.
    book_profile.py writes it for such a document, and a two-engine run of
    one with no profile takes it as its engine preference."""
    return {
        "preferEngine": "adjudicate",
        "reason": (f"short document ({pages} page(s) <= {SHORT_DOC_MAX_PAGES}): skipping calibration; "
                   "per-spot adjudication resolves actual disagreements directly"),
        "stopped": "short-document fast path",
        "decision": {"pairVotes": {}, "preferEngine": "adjudicate"},
        "verdicts": [],
    }


def _engine_key(name: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(name or "").lower())


def draft_engine_evidence(folder: Path, sample: int = ROLE_SAMPLE_PAGES) -> dict[str, Any]:
    """What the drafts' JSON in FOLDER says about the engine that wrote them,
    from its first SAMPLE pages: the engine names they give (`engine`, else
    `ocrProfile.engine`), how many pages carry layout labels (engine A's
    blocks: a label other than ENGINE_B_LABELS) and how many only engine B's
    whole-page labels.  Lines with no label say nothing."""
    names: set[str] = set()
    layout = whole = 0
    has_contract = False
    contract_errors: list[str] = []
    paths = sorted(path for path in folder.glob("page-*.json") if re.fullmatch(r"page-\d{4,}\.json", path.name))
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if _draft_contract.requires_semantics(record):
            try:
                draft_text = path.with_suffix(".txt").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                draft_text = None
                contract_errors.append(f"{path.name}: paired TXT missing")
            problem = _draft_contract.layout_problem(record, draft_text)
            if problem:
                contract_errors.append(f"{path.name}: {problem}")
            else:
                has_contract = True
        name = record.get("engine")
        if not (isinstance(name, str) and name) and isinstance(record.get("ocrProfile"), dict):
            name = record["ocrProfile"].get("engine")
        if isinstance(name, str) and name:
            names.add(name)
        labels = {str(line.get("blockLabel") or "") for line in record.get("lines") or []
                  if isinstance(line, dict)} - {"", "unknown"}
        if labels:
            if labels - ENGINE_B_LABELS:
                layout += 1
            else:
                whole += 1
    if has_contract or contract_errors:
        # A new layout backend cannot silently turn a missing completion JSON
        # into the legacy TXT-only fallback on another page of the same run.
        for text_path in sorted(folder.glob("page-*.txt")):
            if re.fullmatch(r"page-\d{4,}\.txt", text_path.name) and not text_path.with_suffix(".json").is_file():
                contract_errors.append(f"{text_path.name}: paired layout JSON missing")
    return {"names": sorted(names), "layoutPages": layout, "wholePagePages": whole,
            "contractErrors": contract_errors}


def draft_role(evidence: Mapping[str, Any]) -> str | None:
    """'A' for PaddleOCR-VL's drafts (or unnamed ones with layout labels),
    'B' for HunyuanOCR's (or unnamed whole-page ones), the names of another
    engine (or of several), or None when the drafts do not say."""
    if evidence.get("contractErrors"):
        return "invalid layout contract: " + "; ".join(evidence["contractErrors"])
    names = list(evidence.get("names") or [])
    keys = {_engine_key(name) for name in names}
    if keys:
        if keys == {_engine_key(ENGINE_A_NAME)}:
            return "A"
        if keys == {_engine_key(ENGINE_B_NAME)}:
            return "B"
        return " and ".join(names)
    if evidence.get("layoutPages") and not evidence.get("wholePagePages"):
        return "A"
    if evidence.get("wholePagePages") and not evidence.get("layoutPages"):
        return "B"
    return None


def engine_roles_problem(drafts_a: Path, drafts_b: Path, a_name: str = "DRAFT_DIR",
                         b_name: str = "--draft2-directory", one_engine: str = "") -> str | None:
    """Why DRAFTS_A and DRAFTS_B cannot be engine A and engine B, or None.
    Engine A must be PaddleOCR-VL: its layout labels drive the merge, the
    crops, the learned running heads and the furniture; engine B HunyuanOCR.
    Only what the drafts' JSON says counts (draft_engine_evidence): drafts
    that do not say are taken as given.  A_NAME and B_NAME are how the
    command names the two folders; ONE_ENGINE, how it runs one engine."""
    role_a, role_b = draft_role(draft_engine_evidence(drafts_a)), draft_role(draft_engine_evidence(drafts_b))
    if role_a in (None, "A") and role_b in (None, "B"):
        return None
    rule = (f"engine A must be {ENGINE_A_NAME} (its layout labels drive the merge, the crops and the running "
            f"heads) and engine B {ENGINE_B_NAME}")
    if role_a == "B" and role_b == "A":
        return (f"engine roles swapped: {a_name} {drafts_a} holds {ENGINE_B_NAME}'s drafts and {b_name} "
                f"{drafts_b} holds {ENGINE_A_NAME}'s; {rule} - pass {drafts_b} as {a_name} and {drafts_a} "
                f"as {b_name}")
    said = {"A": ENGINE_A_NAME, "B": ENGINE_B_NAME}
    problems = []
    if role_a not in (None, "A"):
        problems.append(f"{a_name} {drafts_a} holds {said.get(role_a, role_a)}'s drafts, not {ENGINE_A_NAME}'s")
    if role_b not in (None, "B"):
        problems.append(f"{b_name} {drafts_b} holds {said.get(role_b, role_b)}'s drafts, not {ENGINE_B_NAME}'s")
    return ("wrong engine roles: " + "; ".join(problems) + f"; {rule}"
            + (f" (to proofread from one engine's drafts, {one_engine})" if one_engine else ""))


def default_draft2(drafts: Path, sources: Mapping[str, Any]) -> tuple[Path | None, list[Path]]:
    """Engine B's drafts when --draft2-directory is not given, and the folders
    looked at, in order: the one the book profile's sources name when the
    profile was measured on DRAFTS, then ENGINE_B_FOLDER beside DRAFTS, then
    the profile's folder however it was measured.  A folder counts when it
    holds page-NNNN.txt drafts and is not DRAFTS."""
    named = sources.get("draftB")
    named = Path(named).expanduser() if isinstance(named, str) and named else None
    measured_a = sources.get("draftA")
    here = isinstance(measured_a, str) and bool(measured_a) and Path(measured_a).expanduser().resolve() == drafts
    candidates = ([named] if named and here else []) + [drafts.parent / ENGINE_B_FOLDER] + (
        [named] if named and not here else [])
    looked: list[Path] = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in looked:
            continue
        looked.append(candidate)
        if (candidate != drafts and candidate.is_dir()
                and any(re.fullmatch(r"page-\d{4}\.txt", path.name) for path in candidate.glob("page-*.txt"))):
            return candidate, looked
    return None, looked


def page_ranges(pages: Sequence[int]) -> str:
    """PAGES as ranges: 3-5,9."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def missing_engine_b_warning(drafts2: Path, waiting: Sequence[int], selected: int) -> str:
    """The warning for the pages of a two-engine run not proofread because
    engine B's draft is missing (--allow-missing-engine-b)."""
    return (f"engine B's drafts ({drafts2}) lack {len(waiting)} of the {selected} selected page(s): pages "
            f"{page_ranges(waiting)}. Engine B's OCR may still be running, or was interrupted; two engines are "
            "the default, so these pages are not proofread and count as failed. Finish engine B's OCR and run "
            "the same command again, or pass --allow-missing-engine-b to transcribe them from engine A's "
            f"draft alone (an {MISSING_ENGINE_DOUBT} doubt each), or --single-engine for one engine throughout")


def missing_engine_doubt(job: PageJob) -> dict[str, str]:
    """The doubt on a page of a two-engine run sealed from one engine's draft
    because the other's is missing (job.missing_engine)."""
    if job.missing_engine == "B":
        detail = (f"ENGINE B DRAFT MISSING: this page has no {ENGINE_B_NAME} draft; it is transcribed from "
                  f"engine A's ({ENGINE_A_NAME}) draft alone, with no second reading to check it against - "
                  "check it against the image, or OCR it with engine B and proofread it again (--overwrite)")
    else:
        detail = (f"ENGINE A DRAFT MISSING: this page has no {ENGINE_A_NAME} draft; it is transcribed from "
                  f"engine B's ({ENGINE_B_NAME}) draft alone, without engine A's layout and with no second "
                  "reading to check it against - check it against the image, or OCR it with engine A and "
                  "proofread it again (--overwrite)")
    return {"kind": MISSING_ENGINE_DOUBT, "detail": detail}


def _run_windowed(
    pool: cf.ThreadPoolExecutor,
    fn: Callable[[Any], Any],
    jobs: Sequence[Any],
    window: int,
) -> Iterator[tuple[Any, cf.Future[Any]]]:
    """Yield (job, completed_future) while keeping at most WINDOW futures
    submitted or uncollected in memory at once."""
    job_iter = iter(jobs)
    pending: dict[cf.Future[Any], Any] = {}
    cap = max(1, window)

    def fill() -> None:
        while len(pending) < cap:
            try:
                nxt = next(job_iter)
            except StopIteration:
                return
            pending[pool.submit(fn, nxt)] = nxt

    fill()
    while pending:
        done, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
        for future in done:
            job = pending.pop(future)
            fill()
            yield job, future


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    started_at = utc_now()
    started = time.monotonic()
    try:
        renders = arguments.render_directory.expanduser().resolve()
        from _ocr_engines import read_book_engine_record
        try:
            read_book_engine_record(renders.parent)
        except ValueError as exc:
            raise ProofreadError(str(exc)) from exc
        pages = parse_pages(arguments, renders)
        drafts = arguments.draft_directory.expanduser().resolve()
        output = arguments.output_directory.expanduser().resolve()
        for label, path in (("render", renders), ("draft", drafts)):
            if not path.is_dir():
                raise ProofreadError(f"{label} directory does not exist: {path}")
        if arguments.workers < 1:
            raise ProofreadError("--workers must be at least 1")
        if arguments.max_items_per_bundle < 1:
            raise ProofreadError("--max-items-per-bundle must be at least 1")
        effort = None if arguments.reasoning_effort.lower() == "none" else arguments.reasoning_effort
        # Switch --effort-policy: the HTTP client's alone (a packet is answered
        # by the agent, at whatever effort it works at).
        arguments.effort_policy_rungs = (parse_effort_policy(arguments.effort_policy)
                                         if arguments.backend == "http" else {})
        # The book profile: which engine reads THIS book better, what it
        # prints, how its pages are laid out.  Nothing about a book is assumed.
        profile_path = (arguments.profile.expanduser().resolve() if arguments.profile
                        else drafts.parent / "book-profile.json")
        arguments.profile_data, arguments.profile_sha = None, ""
        if profile_path.is_file():
            arguments.profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            arguments.profile_sha = profile_fingerprint(arguments.profile_data)
        elif arguments.profile:
            raise ProofreadError(f"profile does not exist: {profile_path}")
        sources = (arguments.profile_data or {}).get("sources") or {}
        # Engine B's drafts: two engines are the default, one only when asked.
        if arguments.single_engine:
            if arguments.draft2_directory is not None:
                raise ProofreadError("--single-engine cannot be combined with --draft2-directory")
            drafts2 = None
        elif arguments.draft2_directory is not None:
            drafts2 = arguments.draft2_directory.expanduser().resolve()
            if not drafts2.is_dir():
                raise ProofreadError(f"engine B's draft directory (--draft2-directory) does not exist: {drafts2}")
        else:
            drafts2, looked = default_draft2(drafts, sources)
            if drafts2 is None:
                raise ProofreadError(
                    f"no engine B drafts: two OCR engines are the default (engine A {ENGINE_A_NAME} in "
                    f"{drafts}, engine B {ENGINE_B_NAME}), and no folder of engine B's drafts was found at "
                    + ", ".join(str(path) for path in looked)
                    + " - pass --draft2-directory DIR, or --single-engine to proofread from "
                      f"{drafts} alone")
            print(f"  engine B's drafts: {drafts2} (no --draft2-directory given)", flush=True)
        if arguments.single_engine:
            contract_errors = draft_engine_evidence(drafts).get("contractErrors") or []
            if contract_errors:
                raise ProofreadError("invalid layout contract: " + "; ".join(contract_errors))
        if drafts2 is not None:
            problem = engine_roles_problem(drafts, drafts2, one_engine="pass --single-engine")
            if problem:
                raise ProofreadError(problem)
        # A profile describes one book's drafts; applied to other drafts
        # (another book, re-OCR'd pages, engines swapped) it is wrong.
        probe = (arguments.profile_data or {}).get("probePages") or []
        measured = sources.get("probeDraftsSha256")
        if probe and not profile_digest_matches(measured, probe, drafts, drafts2):
            measured_b = sources.get("draftB")
            measured_b = Path(measured_b).expanduser().resolve() if isinstance(measured_b, str) and measured_b else None
            if drafts2 is None and measured_b is not None:
                raise ProofreadError(
                    f"{profile_path} was measured on two engines' drafts (B={measured_b}), and --single-engine "
                    "reads one: drop --single-engine to proofread with engine B's drafts, or name a profile "
                    "for these drafts with --profile")
            if drafts2 is not None and profile_digest_matches(measured, probe, drafts2, drafts):
                raise ProofreadError(
                    f"engine roles swapped: {profile_path} was measured with {drafts2} as engine A and "
                    f"{drafts} as engine B - pass {drafts2} as the draft directory and {drafts} as "
                    "--draft2-directory")
            if (drafts2 is not None and measured_b is not None and measured_b != drafts2
                    and measured_b.is_dir() and profile_digest_matches(measured, probe, drafts, measured_b)):
                raise ProofreadError(
                    f"{profile_path} was measured with engine B's drafts in {measured_b}, not {drafts2}: "
                    f"pass --draft2-directory {measured_b}")
            raise ProofreadError(
                f"{profile_path} was measured on drafts A={sources.get('draftA')} "
                f"B={sources.get('draftB')}; the probe pages' drafts under A={drafts} "
                f"B={drafts2} differ (another book, or re-OCR'd pages) - "
                "re-run scripts/book_profile.py for these drafts")
        output.mkdir(parents=True, exist_ok=True)
        # Variant-form rulings, one per pair for the whole book: a person's,
        # or the model's in a file the variant vote wrote (variant_ruling_by).
        # A person's ruling stands over the model's for the same pair wherever
        # it is in the file.
        rulings_path = (arguments.variant_rulings.expanduser().resolve() if arguments.variant_rulings
                        else profile_path.with_name("variant-rulings.json"))
        arguments.variant_rulings_data, arguments.variant_rulings_sha = {}, ""
        arguments.variant_ruled_by, arguments.variant_ruling_forms = {}, {}
        arguments.variant_rulings_file, arguments.variant_rulings_file_sha = None, ""
        if rulings_path.is_file():
            raw_rulings = json.loads(rulings_path.read_text(encoding="utf-8"))
            arguments.variant_rulings_file = str(rulings_path)
            arguments.variant_rulings_file_sha = sha256_file(rulings_path)
            # A person may rule a pair the model left unruled on its own
            # entry there (variant_person_unruled): theirs too.
            entries = [(ruling, variant_ruling_by(ruling)) for ruling in raw_rulings.get("rulings") or []]
            entries += [(ruling, "person") for ruling in variant_person_unruled(raw_rulings)]
            for ruling, by in entries:
                forms = [CJK(f) for f in ruling.get("forms") or []]
                printed = CJK(ruling.get("printed") or "")
                if len(forms) == 2 and printed in forms:
                    if by == "model" and arguments.variant_ruled_by.get(frozenset(forms)) == "person":
                        continue
                    arguments.variant_rulings_data[frozenset(forms)] = printed
                    arguments.variant_ruled_by[frozenset(forms)] = by
                    arguments.variant_ruling_forms[frozenset(forms)] = "|".join(forms)
            arguments.variant_rulings_sha = variant_rulings_seal(
                raw_rulings, rulings_path, arguments.variant_rulings_data, arguments.variant_ruled_by)
        elif arguments.variant_rulings:
            raise ProofreadError(f"variant rulings do not exist: {rulings_path}")
        arguments.variant_rulings = arguments.variant_rulings_data
        # A short document needs no profile for two engines: the engine
        # section book_profile.py would write for it (short_document_engines).
        short_engines = None
        if arguments.profile_data is None and drafts2 is not None:
            document_pages = len(rendered_pages(renders))
            if document_pages > SHORT_DOC_MAX_PAGES:
                raise ProofreadError(
                    "no engine preference: which OCR engine reads a book better differs from "
                    f"book to book, so measure it first (a document of more than {SHORT_DOC_MAX_PAGES} "
                    f"pages; this one has {document_pages}) - run scripts/book_profile.py "
                    f"RENDER_DIR DRAFT_DIR DRAFT2_DIR {drafts.parent / 'book-profile.json'}, or name "
                    "one with --profile")
            short_engines = short_document_engines(document_pages)
        engines_section = ((arguments.profile_data or {}).get("engines") or short_engines or {})
        arguments.prefer_engine = ((arguments.profile_data or {}).get("preferEngine")
                                   or (short_engines or {}).get("preferEngine") or "adjudicate")
        preference_source = (f"profile {profile_path.name}" if arguments.profile_data is not None else
                             f"no profile: {short_engines['reason']}" if short_engines is not None else
                             "single-engine image transcription; no engine calibration")
        # When the profile could not state a preference, budget overflow and
        # unusable verdicts still need an engine: the probe's leader.
        votes = (engines_section.get("decision") or {}).get("pairVotes") or {}
        arguments.fallback_engine = "B" if votes.get("B", 0) > votes.get("A", 0) else "A"
        if arguments.prefer_engine in ("A", "B"):
            arguments.fallback_engine = arguments.prefer_engine
        # Which engine's forms the rulings (the model's and a person's) found
        # printed more often: the default for variant pairs not ruled.
        arguments.variant_engine = None
        listed = ((arguments.profile_data or {}).get("variants") or {}).get("pairs") or []
        side = Counter()
        for pair in listed:
            printed = arguments.variant_rulings.get(frozenset((pair.get("engineA"), pair.get("engineB"))))
            if printed == pair.get("engineA"):
                side["A"] += 1
            elif printed == pair.get("engineB"):
                side["B"] += 1
        if side and side["A"] != side["B"]:
            arguments.variant_engine = "A" if side["A"] > side["B"] else "B"
        # Pairs the variant lists now decide differently from this profile's
        # classification (a profile made before 2026-10-13 holds the model's
        # answers too).  Merging follows the lists, but the profile's engine
        # calibration left out the pairs it called variants, and its list of
        # variant pairs, with their crops, holds no pair it called different.
        arguments.stale_pair_classes = sorted(
            key for key, verdict in ((arguments.profile_data or {}).get("pairClasses") or {}).items()
            if len(key.split("|")) == 2 and classify_pair(*key.split("|")) != verdict)
        if arguments.stale_pair_classes:
            print(f"  WARNING {len(arguments.stale_pair_classes)} pair(s) are classified differently by "
                  "the variant lists than by this profile (e.g. "
                  + ", ".join(arguments.stale_pair_classes[:5]) + "); merging follows the lists, but the "
                  "profile's engine calibration and variant list do not: rebuild the profile (book_profile.py)",
                  flush=True)
        # The glyphs each engine writes for the other's printed marks on this
        # book, from every draft pair in the directories (not only the pages
        # selected), with no model call: measured about a second for a
        # 344-page book.  Before any page is judged current, since they decide
        # characters and the seal carries them.
        arguments.mark_evidence, arguments.mark_aliases = {}, {"A": {}, "B": {}}
        if drafts2 is not None:
            arguments.mark_evidence = mark_evidence(
                [(path, drafts2 / path.name) for path in sorted(drafts.glob("page-*.txt"))
                 if (drafts2 / path.name).is_file()])
            arguments.mark_aliases = mark_aliases(arguments.mark_evidence)
        # The script the book is printed in, from every draft of both engines
        # in the directories (not only the pages selected), with no model call:
        # in simplified script, a variant pair the rulings leave open whose
        # forms differ in script alone is written as engine B read it
        # (resolve_variant), and an answer in traditional script in the
        # simplified form an engine read (adjudicate_segment).
        arguments.simplified_script = simplified_script(
            path.read_text(encoding="utf-8") for folder in (drafts, drafts2) if folder is not None
            for path in sorted(folder.glob("page-*.txt")))
        # Switch --keep-arabic-numerals: engine A's figures on every page and the
        # glyphs engine B writes for them, from every draft in the directories
        # (not only the pages selected: a page's figure is confirmed by the
        # figures before and after it), with no model call.
        arguments.figure_evidence, arguments.figure_aliases = {}, {}
        if arguments.keep_arabic_numerals and drafts2 is not None:
            arguments.figure_evidence = figure_evidence(
                [(int(path.stem.split("-")[-1]), path, drafts2 / path.name)
                 for path in sorted(drafts.glob("page-*.txt")) if path.stem.split("-")[-1].isdigit()],
                arguments.mark_aliases)
            arguments.figure_aliases = figure_aliases(arguments.figure_evidence)
        # Switch --learned-heads: the running head learned by where it is
        # printed - the profile's (book_profile.py --learned-heads) or, for a
        # profile measured without the switch, learned here the same way from
        # engine A's layout of the pages the profile learns from (every page
        # with both drafts, a render and text; not only the pages selected: a
        # label is learned over the book).  No model call.
        arguments.learned, arguments.learned_from = None, None
        if arguments.learned_heads:
            layout = profile_section(arguments, "layout")
            if layout.get("learnedHeads") is not None:
                arguments.learned, arguments.learned_from = layout["learnedHeads"], "profile"
            else:
                pool = {}
                for path in sorted(drafts.glob("page-*.txt")):
                    match = re.fullmatch(r"page-(\d{4})\.txt", path.name)
                    if (match and (renders / f"page-{int(match.group(1)):04d}.png").is_file()
                            and (drafts2 is None or (drafts2 / path.name).is_file())
                            and CJK(path.read_text(encoding="utf-8"))):
                        pool[int(match.group(1))] = load_blocks(path.with_suffix(".json"))
                arguments.learned = learn_heads(pool, layout.get("runningHeads") or [])
                arguments.learned_from = "drafts"
    except ProofreadError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    # The round-4 fixes are on or off for the whole book: a run that would
    # leave pages sealed with them set the other way (a book begun before they
    # were on by default, resumed without the four --no-* switches) does not
    # start, before a client is made or any page decided (round4_split).
    two_ways = round4_split(output, [page for page in pages if arguments.overwrite
                                     and (renders / f"page-{page:04d}.png").is_file()
                                     and (drafts / f"page-{page:04d}.txt").is_file()],
                            decision_provenance(arguments))
    if two_ways:
        print(f"ERROR {two_ways}", file=sys.stderr)
        return 2

    packets_dir = (arguments.packets_dir.expanduser().resolve()
                   if arguments.packets_dir is not None else output.parent / "packets")
    events = _packet_events.PacketEvents(packets_dir, "proofread",
                                        enabled=arguments.backend == "packet" and drafts2 is not None)
    arguments.packet_events = events
    # Made once the pages are sorted, and only when one is to be proofread: a
    # run that finds every page current asks no model, and so needs none.
    client: Any = None
    jobs: list[PageJob] = []
    results: list[dict[str, Any]] = []
    skipped = blocked = legacy = 0
    run_tally: Counter = Counter()
    variant_queue: Counter = Counter()
    variant_by_model: Counter = Counter()
    doubt_pages: Counter = Counter()

    def add_tally(record: Mapping[str, Any], doubts: Sequence[Mapping[str, Any]]) -> None:
        run_tally.update(Counter(record.get("engineTally") or {}))
        variant_queue.update(record.get("variantUnruled") or [])
        variant_by_model.update(record.get("variantRuledByModel") or [])
        doubt_pages.update({doubt.get("kind") for doubt in doubts})

    unreadable = one_engine = 0
    # Pages of a two-engine run with no engine B draft, not proofread without
    # --allow-missing-engine-b.
    waiting_b: list[int] = []
    for scan_page in pages:
        render = renders / f"page-{scan_page:04d}.png"
        draft_text = drafts / f"page-{scan_page:04d}.txt"
        draft_json = drafts / f"page-{scan_page:04d}.json"
        d2 = (drafts2 / f"page-{scan_page:04d}.txt") if drafts2 else None
        has_a, has_b = draft_text.is_file(), bool(d2 and d2.is_file())
        if not render.is_file() or not (has_a or has_b):
            missing = [what for what, there in (("render", render.is_file()), ("engine A's draft", has_a),
                                                 *((("engine B's draft", has_b),) if drafts2 else ()))
                       if not there]
            results.append({"scanPage": scan_page, "status": "failed",
                            "error": "missing " + " and ".join(missing)})
            unreadable += 1
            continue
        out_json, out_md = output / f"page-{scan_page:04d}.json", output / f"page-{scan_page:04d}.md"
        if has_a:
            # Engine B's draft missing on a two-engine run: engine A's alone.
            job = PageJob(scan_page, render, draft_text, draft_json if draft_json.is_file() else None,
                          d2 if has_b else None, out_json, out_md,
                          missing_engine="B" if drafts2 is not None and not has_b else None)
        else:
            # Engine A's draft missing: engine B's alone, without a layout.
            job = PageJob(scan_page, render, d2, None, None, out_json, out_md, missing_engine="A")
        expect = {**sealed_inputs(job), **decision_provenance(arguments)}
        valid, reason = validate_completion(job, expect)
        if job.missing_engine == "B" and not arguments.allow_missing_engine_b and not (
                valid and not arguments.overwrite):
            # A page sealed from engine A alone stays current; one not sealed
            # yet waits for engine B's draft.
            waiting_b.append(scan_page)
            results.append({"scanPage": scan_page, "status": "failed",
                            "error": "missing engine B's draft (engine B's OCR still running or interrupted?); "
                                     "--allow-missing-engine-b transcribes it from engine A's draft alone"})
            continue
        one_engine += job.missing_engine is not None
        if valid and not arguments.overwrite:
            skipped += 1
            record = json.loads(job.out_json.read_text(encoding="utf-8"))
            entry = {"scanPage": scan_page, "status": "skipped-current",
                     "auditorClean": record.get("auditorClean"),
                     # Packet waves complete pages at different times. Keep
                     # their sealed fusion evidence in the selected-range
                     # total even when this replay asks no further question.
                     "punctuationFusion": record.get("punctuationFusion") or
                         punctuation_fusion_summary(record.get("punctuationPasses") or [])}
            if reason != "current":
                # Sealed by the old skill before some of its rules (LEGACY_ABSENT_KEYS).
                legacy += 1
                entry["legacy"] = reason
            doubts = record.get("doubts") or []
            entry["doubts"] = sorted({doubt["kind"] for doubt in doubts})
            results.append(entry)
            # Pages sealed under the same decisions still count toward the
            # audit, so a resumed or chunked run judges the whole range.
            add_tally(record, doubts)
            continue
        if (os.path.lexists(job.out_json) or os.path.lexists(job.out_md)) and not arguments.overwrite:
            blocked += 1
            results.append({"scanPage": scan_page, "status": "blocked",
                            "error": f"existing output rejected: {reason}; use --overwrite"})
            continue
        jobs.append(job)

    if waiting_b:
        print("WARNING " + missing_engine_b_warning(drafts2, waiting_b, len(pages)), file=sys.stderr, flush=True)

    if jobs and arguments.backend == "packet":
        try:
            client = PacketClient(
                packets_dir,
                max_bundles=arguments.max_bundles,
                max_items_per_bundle=arguments.max_items_per_bundle,
                model=arguments.model,
                effort=effort,
                call_ledger_var=_CALL_LEDGER,
                call_site_var=_CALL_SITE,
                request_config=packet_request_config(arguments),
            )
        except ValueError as error:
            print(f"ERROR {error}", file=sys.stderr)
            return 2
    elif jobs:
        try:
            client = Client(arguments.endpoint, arguments.model, effort, arguments.timeout, arguments.temperature,
                            max_inflight=arguments.max_inflight or arguments.workers,
                            loop_detector=_loop_watch.RULE if arguments.loop_detector else None,
                            effort_policy=arguments.effort_policy_rungs)
        except ProofreadError as error:
            print(f"ERROR {error}", file=sys.stderr)
            return 2
        # The model the seals record (proofreadModel) and the report names.
        arguments.model = getattr(client, "model", None) or arguments.model
    events.record("resume", checkpoint_answers=len(client.answers) if isinstance(client, PacketClient) else None,
                  queued_pages=len(jobs), current_pages=skipped)

    completed = pending_pages = 0
    # A page with no render, or with no draft of either engine, cannot be
    # read; one with no engine B draft waits for it (waiting_b).
    failed = unreadable + len(waiting_b)
    research_queue: list[dict[str, Any]] = []
    call_totals: dict[str, Counter] = {}
    if jobs:
        # Learn this book's running heads and folios once, before any page is
        # proofread.  A single page cannot tell a long running head from a
        # sentence; the book can.  Read EVERY draft in the directory, not just
        # the selected pages: a six-page run learns almost nothing, and the
        # guard then rejects the formatter for removing a running head it could
        # not recognise.
        corpus = sorted(drafts.glob("page-*.txt")) or [j.draft_text for j in jobs]
        arguments.furniture = furniture_grams(corpus)
        print(f"  engine preference {arguments.prefer_engine} (from {preference_source}); printed in "
              f"{'simplified' if arguments.simplified_script else 'traditional'} script (the drafts)", flush=True)
        if drafts2 is not None:
            print("  glyphs taken for marks (mark rule): engine A "
                  f"{''.join(sorted(arguments.mark_aliases['A'])) or '-'}, engine B "
                  f"{''.join(sorted(arguments.mark_aliases['B'])) or '-'}", flush=True)
        print(f"  proofreading {len(jobs)} page(s), {arguments.workers} in flight, "
              f"backend={arguments.backend}, effort={effort or 'default'}, "
              f"{len(arguments.furniture)} recurring furniture n-gram(s)", flush=True)
        if arguments.learned is not None:
            learned = arguments.learned
            by_place = len((learned.get("placed") or {}).get("stems") or {})
            print(f"  learned running head (--learned-heads, from the {arguments.learned_from}): stems "
                  f"{'、'.join((learned.get('stems') or [])[:12]) or '-'}"
                  f"{' (%d in all, %d learned by place)' % (len(learned['stems']), by_place) if by_place else ''}; labels "
                  f"{'、'.join(list(learned.get('labels') or {})[:12]) or '-'}; families "
                  f"{'、'.join(learned.get('families') or []) or '-'}; "
                  f"{len((learned.get('folio') or {}).get('runs') or [])} folio run(s); "
                  f"{len(learned.get('regions') or [])} region(s); a band on "
                  f"{learned.get('pagesWithBand', 0)} of {learned.get('pagesExamined', 0)} pages", flush=True)
        with cf.ThreadPoolExecutor(arguments.workers) as pool:
            window = max(arguments.workers * 2, 16)
            for job, future in _run_windowed(
                pool, lambda j: proofread_page(j, client, arguments), jobs, window
            ):
                try:
                    record = future.result()
                except PacketPending:
                    pending_pages += 1
                    results.append({"scanPage": job.scan_page, "status": "pending-packets"})
                    continue
                except Exception as error:  # noqa: BLE001 - one page must not kill the batch
                    failed += 1
                    results.append({"scanPage": job.scan_page, "status": "failed",
                                    "error": str(error)[:300]})
                    print(f"  page {job.scan_page:04d}: FAILED {str(error)[:120]}", file=sys.stderr)
                    continue
                completed += 1
                add_tally(record, record.get("doubts") or [])
                for kind, row in (record.get("calls") or {}).items():
                    total = call_totals.setdefault(kind, Counter())
                    for key, value in row.items():
                        if key == "efforts":
                            # This distribution is nested, unlike the numeric
                            # call totals. Missing legacy/packet metadata stays
                            # absent; never infer it from the run's policy.
                            if value:
                                counts = Counter(total.get(key) or {})
                                counts.update(value)
                                total[key] = dict(sorted(counts.items()))
                        elif key == "maxCompletion":
                            total[key] = (None if total.get(key, 0) is None or value is None
                                          else max(total.get(key, 0), value))
                        elif value is None:
                            total[key] = None
                        elif isinstance(value, (int, float)) and total.get(key, 0) is not None:
                            total[key] += value
                research = [a for a in record["adjudications"] if a.get("needs_research")]
                research_queue.extend(
                    {
                        "scanPage": job.scan_page,
                        "trigger": flag["kind"],
                        "context": None,
                        "candidates": [flag["text"][:120]],
                        "evidence": flag["why"],
                    }
                    for flag in record.get("needsResearch", [])
                )
                research_queue.extend(
                    {
                        "scanPage": job.scan_page,
                        "blockIndex": a.get("block_index"),
                        "context": (a.get("context_before","") + "|" +
                                    a.get("context_after","")) or None,
                        "trigger": a.get("trigger"),
                        "candidates": [a["draft_reading"][:120], a["writer_reading"][:120]],
                        "evidence": a["verdict"][:600],
                    }
                    for a in research
                )
                results.append({
                    "scanPage": job.scan_page, "status": "completed",
                    "seconds": record["seconds"], "auditorClean": record["auditorClean"],
                    "doubts": sorted({doubt["kind"] for doubt in record.get("doubts") or []}),
                    "adjudications": len(record["adjudications"]),
                    "needsResearch": len(research) + len(record.get("needsResearch", [])),
                    "reasoningTokens": record["usage"]["reasoning_tokens"],
                    "calls": sum(row.get("n", 0) for row in (record.get("calls") or {}).values()),
                    "punctuationFusion": record.get("punctuationFusion") or punctuation_fusion_summary([]),
                    "circlesDropped": sum(1 for d in record.get("engineDisagreements") or []
                                          if d.get("resolved_by") == "circle-not-numeral"),
                    "bracketNumerals": sum(1 for d in record.get("engineDisagreements") or []
                                           if str(d.get("resolved_by", "")).startswith("bracket-not-numeral")),
                    "bracketsWritten": (record.get("engineBrackets") or {}).get("inserted", 0),
                    "bracketsMoved": (record.get("engineBrackets") or {}).get("moved", 0),
                    "engineALoopsDropped": len(record.get("engineALoopsDropped") or []),
                    "marksNotCharacters": sum(1 for d in record.get("engineDisagreements") or []
                                              if str(d.get("resolved_by", "")).startswith("mark-not-character")),
                    "movedPlacedOnce": sum(1 for d in record.get("engineDisagreements") or []
                                           if d.get("resolved_by") == "moved-kept-once"),
                    "boundaryCrops": sum(1 for a in record.get("adjudications") or [] if a.get("boundaryCrop")),
                    "furnitureInsertionsDropped": sum(
                        1 for d in record.get("engineDisagreements") or []
                        if str(d.get("resolved_by", "")).startswith("furniture-one-engine")),
                    "decisionChanges": dict(Counter(c.get("decision") for c in record.get("decisionChanges") or [])),
                    # Passes whose census was not asked, by the rule
                    # agreedMarks's reason (engines-agree: no punctuate call
                    # either).
                    "censusNotAsked": dict(Counter((entry.get("censusNotAsked") or {}).get("rule")
                                                   for entry in record.get("punctuationPasses") or []
                                                   if entry.get("censusNotAsked"))),
                    # Requests given up in a word-for-word loop (--loop-detector; only when on).
                    **({"loopCuts": sum(row.get("loopCut", 0) for row in (record.get("calls") or {}).values())}
                       if getattr(arguments, "loop_detector", False) else {}),
                    # Blocks left out and one engine's readings left out as the
                    # learned running head (--learned-heads; only when on).
                    **({"learnedHeadBlocks": sum(1 for c in record.get("decisionChanges") or []
                                                 if c.get("rule") == "learned-head-block"),
                        "learnedHeadReadings": sum(1 for c in record.get("decisionChanges") or []
                                                   if c.get("rule") == "learned-head-reading")}
                       if getattr(arguments, "learned_heads", False) else {}),
                    # Engine A's figures written, by how, and not written, by why
                    # (--keep-arabic-numerals; only when on).
                    **({"arabicFiguresKept": dict(Counter(f.get("how") for f in record.get("arabicFigures") or []
                                                          if f.get("kept"))),
                        "arabicFiguresNotKept": dict(Counter(f.get("why") for f in record.get("arabicFigures") or []
                                                             if not f.get("kept")))}
                       if getattr(arguments, "keep_arabic_numerals", False) else {}),
                    # 裁決 answers not written as copies of the text beside
                    # their spot, by the engine kept (--neighbour-guard; only when on).
                    **({"neighbourCopies": dict(Counter(
                        (a.get("neighbourCopy") or {}).get("keptEngine") for a in record.get("adjudications") or []
                        if a.get("resolved_from") == "adjudicator-neighbour-copy"))}
                       if getattr(arguments, "neighbour_guard", False) else {}),
                })
                print(f"  page {job.scan_page:04d}: {record['seconds']}s  "
                      f"clean={record['auditorClean']}  "
                      f"adjudicated={len(record['adjudications'])}  "
                      f"calls={results[-1]['calls']}  "
                      f"reasoning={record['usage']['reasoning_tokens'] if record['usage']['reasoning_tokens'] is not None else 'unknown'}", flush=True)
                del record, future

    # A table printed across pages: the pages above are sealed one by one, and
    # nothing that sees one page can join a table the page break cut.  The
    # stitch runs once they are all sealed, over every sealed page in the
    # directory (a table can cross the edge of this run's pages), rewrites the
    # pages it joins and reseals them; see stitch_tables.py.  Model-free.
    stitched: dict[str, Any] = {}
    wave_summary: dict[str, Any] | None = None
    if pending_pages > 0 and isinstance(client, PacketClient):
        wave_summary = client.write_wave_bundles(stage="proofread")
        events.emit(wave_summary)
    elif pending_pages == 0:
        # When this file runs as a script, the stitch's own import of it must
        # find this module, not load a second copy.
        sys.modules.setdefault("proofread_pages", sys.modules[__name__])
        import stitch_tables
        furniture = getattr(arguments, "furniture", None)
        if furniture is None:
            furniture = furniture_grams(sorted(drafts.glob("page-*.txt")))
        stitched = stitch_tables.stitch_directory(
            output, renders, drafts, drafts2,
            sorted(set(profile_section(arguments, "layout").get("runningHeads") or [])), furniture)
        if stitched.get("pagesRewritten"):
            events.record("seal", artifact="stitched-pages", scan_pages=stitched["pagesRewritten"])
        # The pages it rewrote carry other doubts now; the report says so.
        for entry in results:
            if entry["scanPage"] not in stitched.get("pagesRewritten", []):
                continue
            record = json.loads((output / f"page-{entry['scanPage']:04d}.json").read_text(encoding="utf-8"))
            old, new = set(entry.get("doubts") or []), {d["kind"] for d in record.get("doubts") or []}
            doubt_pages.subtract(old - new)
            doubt_pages.update(new - old)
            entry["doubts"], entry["auditorClean"] = sorted(new), record.get("auditorClean")
        for kind in [kind for kind, n in doubt_pages.items() if n <= 0]:
            del doubt_pages[kind]

    results.sort(key=lambda r: r["scanPage"])
    wall = time.monotonic() - started
    # Exit 1 with nothing pending, 10 with answers pending and nothing failed,
    # 11 with both: pending waves are never hidden behind a failed page.
    if pending_pages > 0:
        exit_status = EXIT_PACKETS_PENDING_WITH_FAILURES if (failed or blocked) else EXIT_PACKETS_PENDING
    else:
        exit_status = 1 if (failed or blocked) else 0
    answered_by = client.answered_by_summary() if isinstance(client, PacketClient) else {}
    report = {
        "reportSchemaVersion": SCHEMA_VERSION,
        "startedAt": started_at, "finishedAt": utc_now(),
        "wallSeconds": round(wall, 1),
        "secondsPerPageEffective": round(wall / max(completed, 1), 1),
        "backend": arguments.backend,
        "endpoint": arguments.endpoint if arguments.backend == "http" else None,
        "packetsDir": str(packets_dir) if arguments.backend == "packet" else None,
        "model": arguments.model,
        "reasoningEffort": effort, "workers": arguments.workers,
        # Switch --effort-policy, only when given (HTTP).
        **({"effortPolicy": effort_policy_text(arguments.effort_policy_rungs)}
           if getattr(arguments, "effort_policy_rungs", None) else {}),
        "tableOrientation": arguments.table_orientation,
        "punctuationMode": arguments.punctuation_mode,
        "punctuationModeEffective": punctuation_mode(arguments),
        # Unlike calls (this process's completed pages), the fusion summary
        # covers every completed/current selected page across packet waves.
        "punctuationFusion": sum_fusion_summaries(results),
        "exitStatus": exit_status,
        "counts": {"requested": len(pages), "completed": completed,
                   "pending": pending_pages,
                   "skippedCurrent": skipped, "legacy": legacy, "blocked": blocked, "failed": failed,
                   # Pages of a two-engine run with one engine's draft only
                   # (an engine-draft-missing doubt on each).
                   "oneEngine": one_engine,
                   # Of the failed: pages not proofread for want of engine
                   # B's draft (--allow-missing-engine-b).
                   "engineBMissing": len(waiting_b)},
        "singleEngine": drafts2 is None,
        "draftDirectories": {"A": str(drafts), "B": str(drafts2) if drafts2 is not None else None},
        "pages": results,
        # Items the model judged unresolvable from glyph shape alone -
        # proper nouns needing external lookup. The runner has no tools, so
        # these are handed UP to an agent that does.
        "researchQueue": research_queue,
        "enginePreference": {"engine": arguments.prefer_engine, "source": preference_source,
                             "profileSha256": getattr(arguments, "profile_sha", "")},
        # Verdicts counted during the run.
        "engineTallyAdjudicated": dict(run_tally),
        # Variant pairs decided without a ruling, most frequent first.
        # Nothing waits for them.
        "variantQueue": [{"pair": pair, "occurrences": n} for pair, n in variant_queue.most_common()],
        # Which pairs a person ruled, and which the model (variant_ruling_by).
        "variantRulings": variant_rulings_report(arguments, variant_by_model),
        # Pages per kind of doubt (a page counts once per kind): what the
        # not-clean pages need a person for.  Resumed pages count too, with
        # the doubts they were sealed with.
        "doubtPages": dict(doubt_pages.most_common()),
        # Model calls of the pages completed in this run, per kind (the sum of
        # each page's sealed `calls`), with how the calls were run.  Call
        # seconds overlap when calls run together; wallSeconds is the run's.
        "calls": {kind: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(row.items())}
                  for kind, row in sorted(call_totals.items())},
        "pageConcurrency": getattr(arguments, "page_concurrency", DEFAULT_PAGE_CONCURRENCY),
        "maxInflight": arguments.max_inflight or arguments.workers,
        # Single circles one engine read as 〇, dropped as printed marks
        # (is_lone_circle); each is in its page's engineDisagreements.
        "circlesDropped": sum(r.get("circlesDropped", 0) for r in results),
        # Numeral disagreements settled by the other engine's brackets, and
        # brackets the engines read written into (or moved in) the punctuated
        # text (bracket_explanation, place_brackets).
        "bracketNumerals": sum(r.get("bracketNumerals", 0) for r in results),
        "bracketsWritten": sum(r.get("bracketsWritten", 0) for r in results),
        "bracketsMoved": sum(r.get("bracketsMoved", 0) for r in results),
        # Engine-A blocks set aside as repetition loops (quarantine_looped_block).
        "engineALoopsDropped": sum(r.get("engineALoopsDropped", 0) for r in results),
        # Disagreements settled as one engine's printed marks read as characters
        # by the other (mark_explanation), and the glyphs taken for marks on
        # this book with their evidence (mark_aliases, mark_evidence).
        "marksNotCharacters": sum(r.get("marksNotCharacters", 0) for r in results),
        # Runs engine B read in another order, placed once at engine A's site
        # (place_moved_once); each is in its page's decisionChanges.
        "movedPlacedOnce": sum(r.get("movedPlacedOnce", 0) for r in results),
        # 裁決 questions between two layout blocks shown both blocks' edges
        # (boundary_crop); each adjudication records which.
        "boundaryCrops": sum(r.get("boundaryCrops", 0) for r in results),
        # One-engine text left out as the book's furniture (decision D15,
        # furniture_insertion); each is in its page's decisionChanges.
        "furnitureInsertionsDropped": sum(r.get("furnitureInsertionsDropped", 0) for r in results),
        "markAliases": getattr(arguments, "mark_aliases", {}),
        "markEvidence": {side: {char: row for char, row in rows.items() if row["explained"]}
                         for side, rows in (getattr(arguments, "mark_evidence", None) or {}).items()},
        # Text changes per decision, the user's or a proposed one (each page's
        # decisionChanges).
        "decisionChanges": dict(sum((Counter(r.get("decisionChanges") or {}) for r in results), Counter())),
        # The rule agreedMarks: the passes of the pages completed in this run
        # whose census was not asked, by reason - engines-agree (the block
        # written with the marks both engines read: a census and a punctuate
        # call not made).
        "censusNotAsked": dict(sum((Counter(r.get("censusNotAsked") or {}) for r in results), Counter())),
        # Switch --learned-heads, only when on: where the learned running head
        # came from and what it is (learn_heads), and how many blocks and one
        # engine's readings the pages completed in this run left out as it.
        **({"learnedHeads": {
            "from": getattr(arguments, "learned_from", None), "learned": getattr(arguments, "learned", None),
            "blocksLeftOut": sum(r.get("learnedHeadBlocks", 0) for r in results),
            "readingsLeftOut": sum(r.get("learnedHeadReadings", 0) for r in results)}}
           if getattr(arguments, "learned_heads", False) else {}),
        # Switch --keep-arabic-numerals, only when on: engine A's figures on the
        # pages completed in this run, written (by how engine B's reading
        # there was settled) or not and why (arabic_figure_kept), and what it
        # read off the book's drafts: the glyphs engine B writes for figures
        # with their counts (figure_evidence, figure_aliases).
        **({"keepArabicNumerals": {
            "kept": dict(sum((Counter(r.get("arabicFiguresKept") or {}) for r in results), Counter())),
            "notKept": dict(sum((Counter(r.get("arabicFiguresNotKept") or {}) for r in results), Counter())),
            "figureAliases": getattr(arguments, "figure_aliases", {}),
            "figureGlyphs": (getattr(arguments, "figure_evidence", None) or {}).get("glyphs") or {}}}
           if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Switch --loop-detector, only when on: the rule, and for each kind of
        # call how many requests of the pages completed in this run it gave
        # up in a word-for-word loop (loopCut) and what they left of their
        # max_tokens (loopCutTokensSaved): the saving where the loop would
        # have run on to max_tokens.  Each is in its page's calls and
        # decisionChanges.
        **({"loopDetector": {"rule": _loop_watch.RULE.text(),
                             "loopCuts": {kind: {"loopCut": row.get("loopCut", 0),
                                                 "loopCutTokensSaved": row.get("loopCutTokensSaved", 0)}
                                          for kind, row in sorted(call_totals.items())}}}
           if getattr(arguments, "loop_detector", False) else {}),
        # Switch --neighbour-guard, only when on: the window, and how many 裁決
        # answers of the pages completed in this run were not written as
        # copies of the text beside their spot, by the engine whose reading
        # stands; each is in its page's adjudications, decisionChanges and a
        # neighbour-copy doubt.
        **({"neighbourGuard": {
            "window": NEIGHBOUR_WINDOW,
            "notWritten": dict(sum((Counter(r.get("neighbourCopies") or {}) for r in results), Counter()))}}
           if getattr(arguments, "neighbour_guard", False) else {}),
        # Profile pair classes the variant lists now overrule; the variant
        # sheet needs a rebuilt profile to show them.
        "profilePairClassesStale": getattr(arguments, "stale_pair_classes", []),
        # Tables printed across pages and written as one (stitch_tables.py),
        # every page break the stitch decided on, and the pages it rewrote.
        "tablesStitched": stitched.get("tablesStitched", []),
        "tableStitchDecisions": stitched.get("decisions", []),
        "tableStitchPagesRewritten": stitched.get("pagesRewritten", []),
        **({"packetWave": wave_summary} if wave_summary is not None else {}),
        # Packet answers replayed in this run, by the model apply_packet_answers.py
        # recorded as answering them (--answered-by; "unrecorded" when none was).
        **({"packetAnsweredBy": answered_by} if isinstance(client, PacketClient) else {}),
    }
    report_path = (arguments.report.expanduser().resolve() if arguments.report
                   else output.parent / "proofread-run.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report))

    print(f"  requested {len(pages)} | completed {completed} | pending {pending_pages} | "
          f"skipped {skipped} | blocked {blocked} | failed {failed}", flush=True)
    if legacy:
        print(f"  {legacy} skipped page(s) are legacy seals, accepted as sealed (made before some "
              "of the rules they would be decided by now); see the report", flush=True)
    if one_engine:
        print(f"  {one_engine} page(s) had one engine's draft only: transcribed from the other's, with an "
              f"{MISSING_ENGINE_DOUBT} doubt each (counts.oneEngine)", flush=True)
    if waiting_b:
        print("  NOT PROOFREAD (counts.engineBMissing): " + missing_engine_b_warning(drafts2, waiting_b, len(pages)),
              flush=True)
    if answered_by:
        print("  [packets] answers replayed, by answering model: "
              + ", ".join(f"{model} {n}" for model, n in sorted(answered_by.items())), flush=True)
    if wave_summary is not None:
        print(f"  [packets] PENDING: {wave_summary['pending']} request(s) across "
              f"{len(wave_summary['bundles'])} bundle(s) in {wave_summary['waveDir']}", flush=True)
        print(f"  [packets] after subagents write answers-*.json, run: {wave_summary['applyCommand']}",
              flush=True)
        # The plan that shares the bundles among answerers (packet_dispatch.py).
        print("  [packets] to share the bundles among subagents: "
              + shlex.join([sys.executable, str(Path(__file__).with_name("packet_dispatch.py")),
                            str(Path(wave_summary["waveDir"]).parent)]), flush=True)
        if failed or blocked:
            print(f"  [packets] exit {exit_status}: answers are pending AND {failed} page(s) failed and "
                  f"{blocked} were blocked (see the report's pages): answer this wave and run the same "
                  "command again; the failed and blocked pages need their own fix", flush=True)
    if doubt_pages:
        print("  pages with doubts, by kind: "
              + ", ".join(f"{kind} {n}" for kind, n in doubt_pages.most_common()), flush=True)
    padded = sum(row.get("padded", 0) for row in call_totals.values())
    if padded:
        print(f"  {padded} image(s) padded to the server's aspect ratio (fit_media_aspect; "
              "calls.<kind>.padded)", flush=True)
    print(f"  wall {wall:.1f}s -> {wall/max(completed,1):.1f}s/page effective", flush=True)
    if research_queue:
        print(f"  {len(research_queue)} item(s) need external research "
              f"(proper nouns unresolvable from glyphs) - see researchQueue", flush=True)
    print(f"  report: {report_path}", flush=True)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
