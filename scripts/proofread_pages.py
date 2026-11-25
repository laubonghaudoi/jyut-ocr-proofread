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
from jyut_ocr.core.common import canonical_json_bytes, sha256_bytes, sha256_file, utc_now  # noqa: E402
from jyut_ocr.core import draft_contract as _draft_contract
from jyut_ocr.core import tables as _tables  # noqa: E402
from jyut_ocr.proofread import loop_watch as _loop_watch  # noqa: E402
from jyut_ocr.core import content_fidelity as _content_fidelity  # noqa: E402
from jyut_ocr.core import note_markers as _note_markers  # noqa: E402
from jyut_ocr.proofread import packet_events as _packet_events  # noqa: E402
from jyut_ocr.proofread import punctuation_fusion as _punctuation_fusion  # noqa: E402
from jyut_ocr.proofread.packet_client import (  # noqa: E402
    DEFAULT_MAX_ITEMS_PER_BUNDLE,
    EXIT_PACKETS_PENDING,
    EXIT_PACKETS_PENDING_WITH_FAILURES,
    PACKET_USAGE_SOURCES,
    PacketClient,
    PacketPending,
    packet_page_context,
)
from jyut_ocr.proofread.core import (  # noqa: E402
    ADJUDICATE_SYSTEM, ANSWER_DELETION_LIMIT, ANSWER_LINE, AUDITOR_SYSTEM, BLIND_SYSTEM, BLIND_USER,
    BRACKET_BESIDE, BRACKET_MISREADS, BRACKET_SLIP, BULLET_MARKS, BULLET_SHAPES, CENSUS_SYSTEM,
    CENSUS_SYSTEM_SPACE_CLAUSE, CHINESE_DIGITS, CIRCLE_CHARS, CJK, CJK_EXTRA, CLOSE_BRACKETS,
    DEFAULT_EFFORT, DEFAULT_ENDPOINT, DEFAULT_MODEL, DEFAULT_PAGE_CONCURRENCY, DEFAULT_WORKERS,
    DEGENERATE_NGRAM_RATIO, DELETED_RUN_LIMIT, DROP_GRAM, EMPTY_VERDICTS, ENGINE_A_NAME,
    ENGINE_B_FOLDER, ENGINE_B_LABELS, ENGINE_B_NAME, FALLBACK_MARKS, FOLIO_MAX_FIGURES,
    FORMAT_SYSTEM, FORMAT_SYSTEM_REVIEWED, FORMAT_SYSTEM_REVIEWED_SPACE_CLAUSE,
    FORMAT_SYSTEM_SPACE_CLAUSE, FRONT_MATTER_LABELS, FRONT_MATTER_LIMIT, FURNITURE_LABELS,
    GROUNDING_LINE, HEAD_SIZE_RATIO, IDEOGRAPHIC_SPACE, INK_MARGIN, INSERTION_LIMIT,
    LARGE_IMAGE_PIXEL_THRESHOLD, LEADER, LEADER_MARKS, LEADER_READ, LOCAL_AGENT_ENV, LONE_FOLIO,
    LOOPED_READING, MEASURE_WIDTH, MEDIA_PIXEL_LIMIT, MISSING_ENGINE_DOUBT,
    MULTIMODAL_PLACEHOLDER_RE, NEARBY_WINDOW, NO_ANSWER_RETRY_EFFORT, NUMERAL_CHARS,
    NUMERAL_CONFLICT_LIMIT, OPENING_MARKS, OPEN_BRACKETS, OTHER_BRACKETS, PADDLEX_IMG_LINE_RE,
    PADDLEX_IMG_TAG_RE, PUNCT, PUNCT_MARKS, PUNCT_SYSTEM, PUNCT_SYSTEM_SPACE_CLAUSE, PageJob,
    ProofreadError, QWEN_CODE_MODEL_ENV, RARE_CODEPOINT, RESEARCH_LINE, ROLE_SAMPLE_PAGES,
    RULE_COVERAGE, SCHEMA_VERSION, SENTENCE_MARKS, SHORT_DOC_MAX_PAGES, SITE_PLACE_KEYS,
    SPACE_CLAUSE_MARKS, STRUCTURE_SYSTEM, STRUCTURE_SYSTEM_REVIEWED,
    STRUCTURE_SYSTEM_REVIEWED_SPACE_CLAUSE, STRUCTURE_SYSTEM_SPACE_CLAUSE, TEXT_AUTHORITY,
    TEXT_SEAMS, UNCORROBORATED_MINIMUM, UNSEEN_VERDICTS, WHOLE_PAGE_FURNITURE_LABELS,
    WRITER_SYSTEM, WRITER_SYSTEM_SPACE_CLAUSE, ZERO_FORMS, aggregate_usage, call_site,
    distinct_ngram_ratio, engine_gaps, facade_attr, front_matter_research, gaps_of, in_context,
    is_lone_circle, is_numeral_conflict, is_rare_codepoint_conflict, layout_index, load_blocks,
    neutralize_multimodal_placeholders, norm_for_diff, profile_section, punctuation_counts,
    punctuation_mode, set_facade, space_clause_enabled, strip_fence, strip_grounding,
    strip_paddlex_img_tags, summarize_calls, table_spans, verdict_answer, _CALL_LEDGER, _CALL_SITE,
    _LARGE_IMAGE_SEM, _LOOP_CUTS, _PADDLEX_IMG_TAG_PATTERN, _large_image_guard, _reviewed_prompt,
)
from jyut_ocr.proofread.client import (  # noqa: E402
    Client, EFFORT_KINDS, EFFORT_POLICY_ENV, EFFORT_RUNGS, MEDIA_ASPECT_LIMIT, agent_is_local_model,
    ask_lower, effort_policy_record, effort_policy_text, endpoint_models_url, fit_media_aspect,
    fit_media_budget, loop_cut_changes, loopback_endpoint, no_answer, no_answer_cut,
    parse_effort_policy, resolve_http_model, _MEDIA_BUDGET_CACHE, _MEDIA_BUDGET_CACHE_LOCK,
    _MEDIA_BUDGET_CACHE_MAX,
)
from jyut_ocr.proofread.crops import (  # noqa: E402
    BOUNDARY_NOTES, COLUMN_BREAK_NOTE, LEADER_DOT_MINIMUM, LEADER_DOT_SIZE, LEADER_MINIMUM_DOTS,
    LEADER_STEP, TILE_CHARACTERS, block_is_vertical, block_tiles, boundary_crop, column_layout,
    column_reach, crop_band, crop_block, crop_glyph, edge_region, image_size, ink_columns, ink_of,
    leader_count, leader_dot_runs, measure_columns, own_rows_extent, page_ink_fraction,
    text_columns, upscale_crop,
)
from jyut_ocr.proofread.variants import (  # noqa: E402
    ALLOGRAPHS, TS_CHARACTERS, VARIANT_LIST, classify_pair, converted_against_b, opencc_allographs,
    resolve_variant, simplified_as_read, simplified_form, simplified_script, traditional_forms,
    user_variant_rulings, variant_fold, variant_model_sites, variant_person_unruled,
    variant_ruling_by, variant_rulings_report, variant_rulings_seal, variant_segment,
    variant_source, _ALLOGRAPH_PAIRS, _FOLD, _LIST_SHA256, _TRADITIONAL_FORMS, _VARIANT_RULINGS,
)
from jyut_ocr.proofread.adjudicate import (  # noqa: E402
    BESIDE_COPY_MIN, MOVED_SHARE, NEIGHBOUR_WINDOW, SPOT_ANCHOR, WHOLE_CROP_NOTES, WIDER_CROP_NOTE,
    adjudicate_prompt, adjudicate_segment, align_drafts, block_spans, confirm_nothing,
    crop_shows_spot, fuse_numeral_runs, gram_counts, judged_misreads, left_out_of, locate_block,
    locate_segment, looped_reading, moved_share, neighbour_copy, non_cjk_glyphs, place_by_alignment,
    propagation_note, propagation_spots, read_elsewhere, read_twice, reading_nearby,
    restore_merged_characters, segment_crop, segment_crop_kind, segment_winner, split_edge_circle,
    split_lopsided, split_lopsided_segments, verify_characters, widened_answer,
)
from jyut_ocr.proofread.furniture import (  # noqa: E402
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
from jyut_ocr.proofread.punctuation import (  # noqa: E402
    AGREED_MARKS_MINIMUM, ANY_FORM_PAGE_PLACES, CENSUS_LINE, CIRCLED_NUMERALS, DOUBLED_LINE,
    FORM_FOLD, GAP_MARKUP, KEPT_PRINT, LIST_LEADER_LINES, MARK_FORMS, MOVE_MINIMUM,
    NOTE_MARKER_PRINT, OPENING_QUOTES, SENTENCE_PUNCT, SHAPE_LINE, UNMARKED_PAGE_MARKS,
    UNMARKED_SAMPLE, agreed_marks, agreed_punctuation, agreed_readings, anchored_marks,
    assemble_passes, break_lines, bullet_contradicted, bullet_runs, census_ceiling, census_hint,
    census_tolerance, deleted_runs, density_limit, doubled_shown, engine_b_lines_merged,
    engine_b_marks_merged, engine_b_places, engine_marks_merged, engine_places, engine_reading,
    extra_copies, extra_copy_doubts, furniture_unexplained, gap_as_written, gap_marks, gap_print,
    gap_sides, leader_list_breaks, leader_rewrites, leader_runs, leaders_written, mark_changes,
    mark_places, marked_stretch, marks_as_written, normalize_cjk_clause_spaces, notation_hint,
    other_print, parse_census, place_moved_once, punctuate_part, punctuation_delta,
    punctuation_density, punctuation_doubts, punctuation_pass_ok, realign_answer,
    restore_clause_spaces, seam_sides, seat_marks, tied_marks, unmarked_candidates,
    whole_page_reason,
)
from jyut_ocr.proofread.markers import (  # noqa: E402
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
from jyut_ocr.proofread.tables import (  # noqa: E402
    CELL_LATEX, CELL_NOTE_MARKER, DIRECTION_ANSWERS, DIRECTION_SYSTEM, FIGURE_DIGITS, FIGURE_TOKEN,
    FIRST_LINE, FIRST_RECORD_SYSTEM, GROUP_MARKS, MIRRORED, NOTE_FIGURES, ORIENT_EVIDENCE_LINE,
    ORIENT_LINE, ORIENT_SYSTEM, PRINT_DIRECTION_ASPECT, PRINT_DIRECTION_LEAD,
    PRINT_DIRECTION_MINIMUM, PRINT_DIRECTION_SKIP, READING_ORDER_PAIRS, READING_ORDER_SHARE,
    SINGLE_LINE_KEPT, SUPERSCRIPT_FIGURES, TABLE_BRACKET_REACH, TABLE_MINIMUM_ROWS,
    TABLE_NOT_BUILT_RETRY, TABLE_TOKEN, TABLE_TURNS, TEXTCIRCLED, TOTAL_LABELS, TURN_AXIS,
    VOUCH_CELLS_DIFFER, VOUCH_CELL_DISTANCE, WITNESS_ANCHOR, WRITTEN_TABLE_TURNS,
    WRITTEN_TURN_UNCHECKED, agreed_block_text, ask_first_record, bare_cell_note_calls,
    bare_note_call, cells_as_read, cells_turned_as_recovered, chinese_figure, circled_figure,
    direction_candidates, enforce_recovered_tables, engine_table, figure_groups, first_table,
    fix_cell_direction, furniture_row, grouping_fits, orient_table, orient_written_tables,
    orientation_undecided, page_print_direction, parse_direction_answers,
    parse_table_visual_verdict, parse_visual_direction_answers, place_recovered_tables,
    punctuation_kept, reading_order_turn, reading_order_votes, recover_tables, recovered_runs,
    refused_grid, restate_table, rotate_table, single_line_grid, split_table_losses,
    table_block_marks, table_brackets, table_characters_lost, table_edges, table_marks_lock,
    table_orientation_record, table_places, table_signature, table_tokens, totals_axis,
    turn_records, turned_round, vouched_grid, witness_anchors, witness_figures, witness_line_axis,
    witness_refusal, written_cells, _cell_key, _covers, _turned_place,
)
from jyut_ocr.proofread.table_rebuild import (  # noqa: E402
    DRAFT2_KEY, NOT_A_TABLE, REBUILD_AS_PRINTED_SYSTEM, REBUILD_SYSTEM, REBUILT_ANCHOR,
    REFUSED_ANSWER_LIMIT, REGION_WITNESS_SHARE, UNREAD_CROP_REACH, UNREAD_NOTE, UNREAD_TABLE_LIMIT,
    block_copy, cells_copy, check_rebuilt_table, confirm_unread, edge_copies, edge_judged,
    edge_verdicts, engine_b_as_settled, engine_b_inside, engine_b_outside, engine_b_table_lines,
    fold_named, marks_left_out, merged_origins, merged_other_readings, nominate_tables,
    rebuild_lock, rebuild_region, rebuild_table, rebuilt_edges, region_witness, settle_by_union,
    unread_crop, unread_sites, unread_spot, unwritten_readings, window_merge,
)
from jyut_ocr.proofread.seal import (  # noqa: E402
    BUILD_REFUSED_TABLES_KEY, CONTENT_FIDELITY_KEY, INFORMATIONAL_PROVENANCE, LEGACY_ABSENT_KEYS,
    LEGACY_REASON, OPTIONAL_PROVENANCE, PROVENANCE_ABSENT_MEANS, ROUND4_DEFAULTS, SEALED_ON,
    content_fidelity_on, decision_provenance, draft_digest, missing_engine_doubt, output_lock,
    packet_request_config, page_spans, profile_digest_matches, profile_fingerprint,
    punctuation_fusion_summary, round4_split, seal_page, sealed_inputs, sum_fusion_summaries,
    validate_completion,
)
from jyut_ocr.proofread.pipeline import (  # noqa: E402
    proofread_page, _proofread_page_inner,
)
set_facade(sys.modules[__name__])


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
    parser.add_argument("--build-refused-tables", action="store_true", dest="build_refused_tables", default=False,
                        help="where the engine wrote a table whose grid was refused and the structure answer "
                             "holds no table for it, ask the structure question once more, saying so; a page "
                             "left without the table gets the doubt table-not-built; off by default")
    parser.add_argument("--unmarked-page-sample", action="store_true", dest="unmarked_page_sample", default=False,
                        help=f"on a page neither engine read more than {UNMARKED_PAGE_MARKS} punctuation marks on, "
                             f"ask the punctuation question for the {UNMARKED_SAMPLE} longest blocks first and, "
                             "when every one is answered as printed without marks, take the page's other "
                             "blocks without marks, unasked; off by default")
    parser.add_argument("--space-clause", action="store_true", dest="space_clause", default=False,
                        help="count and insert printed one-cell Chinese clause-pause spaces as full-width "
                             "ideographic space U+3000 (also turned on when book-profile.json reports "
                             "notation.spaceClause); off by default")
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
        from jyut_ocr.ocr.engines import read_book_engine_record
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
