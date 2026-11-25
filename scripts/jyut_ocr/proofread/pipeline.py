"""Per-page proofreading orchestration (proofread_page and staged helpers)."""

from __future__ import annotations

import argparse
import bisect
from collections import Counter
import concurrent.futures as cf
from dataclasses import dataclass, field
import difflib
import hashlib
import itertools
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence


from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes, sha256_file, utc_now,
)
from jyut_ocr.core import content_fidelity as _content_fidelity  # noqa: E402
from jyut_ocr.proofread.packet_client import (  # noqa: E402
    PacketPending, packet_page_context,
)
from jyut_ocr.proofread.core import (  # noqa: E402
    ANSWER_DELETION_LIMIT, BULLET_MARKS, CJK, DEFAULT_PAGE_CONCURRENCY, DEGENERATE_NGRAM_RATIO,
    DROP_GRAM, FALLBACK_MARKS, FORMAT_SYSTEM, FORMAT_SYSTEM_REVIEWED,
    FORMAT_SYSTEM_REVIEWED_SPACE_CLAUSE, FORMAT_SYSTEM_SPACE_CLAUSE, FURNITURE_LABELS,
    IDEOGRAPHIC_SPACE, INSERTION_LIMIT, LEADER, LOOPED_READING, NO_ANSWER_RETRY_EFFORT, PageJob,
    ProofreadError, SCHEMA_VERSION, SITE_PLACE_KEYS, STRUCTURE_SYSTEM, STRUCTURE_SYSTEM_REVIEWED,
    STRUCTURE_SYSTEM_REVIEWED_SPACE_CLAUSE, STRUCTURE_SYSTEM_SPACE_CLAUSE, TEXT_AUTHORITY,
    UNCORROBORATED_MINIMUM, WRITER_SYSTEM, WRITER_SYSTEM_SPACE_CLAUSE, aggregate_usage, call_site,
    distinct_ngram_ratio, engine_gaps, facade_attr, front_matter_research, gaps_of, in_context,
    is_lone_circle, is_numeral_conflict, is_rare_codepoint_conflict, layout_index, load_blocks,
    profile_section, punctuation_counts, punctuation_mode, space_clause_enabled, strip_fence,
    strip_grounding, strip_paddlex_img_tags, summarize_calls, table_spans, _CALL_LEDGER,
    _LOOP_CUTS,
)
from jyut_ocr.proofread.client import (  # noqa: E402
    Client, ask_lower, effort_policy_record, loop_cut_changes, no_answer, no_answer_cut,
)
from jyut_ocr.proofread.crops import (  # noqa: E402
    BOUNDARY_NOTES, COLUMN_BREAK_NOTE, block_tiles, boundary_crop, column_reach, crop_block,
    image_size, page_ink_fraction, upscale_crop,
)
from jyut_ocr.proofread.variants import (  # noqa: E402
    converted_against_b, resolve_variant, variant_fold, variant_model_sites, variant_segment,
)
from jyut_ocr.proofread.adjudicate import (  # noqa: E402
    BESIDE_COPY_MIN, NEIGHBOUR_WINDOW, WHOLE_CROP_NOTES, adjudicate_segment, block_spans,
    confirm_nothing, judged_misreads, left_out_of, locate_segment, neighbour_copy, propagation_note,
    propagation_spots, read_twice, reading_nearby, restore_merged_characters, segment_crop,
    segment_crop_kind, segment_winner, split_edge_circle, split_lopsided_segments,
    verify_characters, widened_answer,
)
from jyut_ocr.proofread.furniture import (  # noqa: E402
    FURNITURE_GRAM, LOOP_MARKUP, ORDER_CONFLICT_FLOOR, READING_ORDER_AGREEMENT,
    UNCORROBORATED_WITNESS, cut_engine_b_loops, engine_b_cut_off, furniture_leftover,
    head_size_matches, is_running_head, learned_band, learned_head_block, learned_leftover,
    learned_record, loop_unit_from_tag, margin_furniture, needs_placed_stem, non_cjk_text,
    order_judgement, readings_dropped, removable_text, unsupported_reading,
)
from jyut_ocr.proofread.punctuation import (  # noqa: E402
    ANY_FORM_PAGE_PLACES, FORM_FOLD, MARK_FORMS, UNMARKED_SAMPLE, agreed_marks, assemble_passes,
    break_lines, deleted_runs, density_limit, engine_b_lines_merged, engine_b_marks_merged,
    engine_b_places, engine_marks_merged, engine_places, engine_reading, extra_copy_doubts,
    furniture_unexplained, leader_list_breaks, leader_rewrites, mark_changes, mark_places,
    marked_stretch, notation_hint, place_moved_once, punctuate_part, punctuation_delta,
    punctuation_density, punctuation_doubts, restore_clause_spaces, unmarked_candidates,
    whole_page_reason,
)
from jyut_ocr.proofread.markers import (  # noqa: E402
    FIGURE_AT_GAP, FIGURE_IN_BLOCK, FIGURE_WRITTEN, agreed_note_markers, arabic_figure_kept,
    arabic_figures, ask_note_numbers, bracket_explanation, bracket_kinds, bracketed_pairs,
    bracketed_unwritten, brackets_by_block, circle_withheld, engine_marks, figure_neighbours,
    figure_stand_ins, figure_verdict, figures_confirmed, mark_explanation, note_block_texts,
    note_markers_by_block, note_question_reason, place_arabic_figures, place_brackets,
    place_note_markers, restore_arabic_figures, restore_note_markers, structure_input_record,
)
from jyut_ocr.proofread.tables import (  # noqa: E402
    agreed_block_text, ask_first_record, cells_as_read, enforce_recovered_tables, engine_table,
    fix_cell_direction, orient_table, orient_written_tables, orientation_undecided,
    page_print_direction, place_recovered_tables, punctuation_kept, recover_tables,
    split_table_losses, table_block_marks, table_brackets, table_characters_lost, table_marks_lock,
    table_orientation_record, table_tokens, turn_records, _covers,
)
from jyut_ocr.proofread.table_rebuild import (  # noqa: E402
    UNREAD_NOTE, edge_verdicts, engine_b_as_settled, engine_b_inside, engine_b_outside,
    engine_b_table_lines, marks_left_out, merged_origins, merged_other_readings, nominate_tables,
    rebuild_lock, rebuild_region, rebuild_table, rebuilt_edges, region_witness, settle_by_union,
    unread_crop, unread_spot, unwritten_readings, window_merge,
)
from jyut_ocr.proofread.seal import (  # noqa: E402
    content_fidelity_on, decision_provenance, missing_engine_doubt, punctuation_fusion_summary,
    seal_page, sealed_inputs,
)

@dataclass
class _PageState:
    """Mutable per-page state shared across proofreading pipeline stages."""

    job: PageJob
    client: Client
    arguments: argparse.Namespace
    started: float
    ledger: list[dict[str, Any]]
    loop_cuts: list[dict[str, Any]]
    render_bytes: bytes
    draft: str
    draft2: str | None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=lambda: aggregate_usage([]))
    metric_calls: list[Mapping[str, Any]] = field(default_factory=list)
    page_concurrency: int = 1
    table_orientation_mode: str = "auto"
    recovery_packet_pending: bool = False
    rebuild_packet_pending: bool = False
    adjudications: list[dict[str, Any]] = field(default_factory=list)
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    punctuation_passes: list[dict[str, Any]] = field(default_factory=list)
    engine_brackets: dict[str, int] = field(default_factory=lambda: {"inserted": 0, "moved": 0})
    profile_furniture: list[str] = field(default_factory=list)
    learned_dropped: set[int] = field(default_factory=set)
    variant_unruled: list[str] = field(default_factory=list)
    rejected_output: str | None = None
    structure_input: dict[str, Any] | None = None
    format_retry_reason: str | None = None
    retry_refusal: str | None = None
    rejected_retry: str | None = None
    doubts: list[dict[str, Any]] = field(default_factory=list)
    cut_reading: str = ""
    b_cut_off: dict[str, Any] | None = None
    read_b: str | None = None
    tables: list[str] = field(default_factory=list)
    table_sources: list[int] = field(default_factory=list)
    table_notes: list[str] = field(default_factory=list)
    tables_missing: list[int] = field(default_factory=list)
    tables_disputed: list[int] = field(default_factory=list)
    table_characters: str = ""
    structure_table_lost: str = ""
    structure_table_marks: str = ""
    prose: str | None = None
    guarded: str | None = None
    known_heads: list[str] = field(default_factory=list)
    tables_rejected: list[dict[str, Any]] = field(default_factory=list)
    table_orientation: list[str] = field(default_factory=list)
    table_orientation_evidence: list[dict[str, Any]] = field(default_factory=list)
    source_table_mapping_current: bool = True
    recovery_table_correspondence: list[dict[str, Any]] = field(default_factory=list)
    table_grouping: list[str] = field(default_factory=list)
    cell_direction: list[dict[str, Any]] = field(default_factory=list)
    table_nominations: list[dict[str, Any]] = field(default_factory=list)
    table_rebuilds: list[dict[str, Any]] = field(default_factory=list)
    figures_kept: list[dict[str, Any]] = field(default_factory=list)
    arabic_figures_seen: list[dict[str, Any]] = field(default_factory=list)
    page_bracketed: list[tuple[int, str, str]] = field(default_factory=list)
    figure_why: dict[int, str] = field(default_factory=dict)
    loops_dropped: list[dict[str, Any]] = field(default_factory=list)
    decision_changes: list[dict[str, Any]] = field(default_factory=list)
    lopsided: list[dict[str, Any]] = field(default_factory=list)
    readings_lost: list[dict[str, Any]] = field(default_factory=list)
    explained: dict[str, list[tuple[int, int]]] = field(default_factory=lambda: {"A": [], "B": []})
    engine_a_characters: int = 0
    engine_b_characters: int = 0
    inventory: dict[str, Any] = field(default_factory=dict)
    fidelity_on: bool = False
    reviewing: bool = False
    fidelity_status: str = "complete"
    content_recovery: dict[str, Any] | None = None
    coverage_review: dict[str, Any] | None = None
    unsupported: str | None = None
    engines_silent: bool = False
    non_cjk: str = ""
    transcribing: bool = False
    mode: str = "merge"
    resolved: str = ""
    writer_text: str = ""
    spans: list[str] | None = None
    last_message: dict[str, Any] = field(default_factory=dict)

    def add(self, m: Mapping[str, Any]) -> None:
        self.metric_calls.append(m)
        self.metrics.update(aggregate_usage(self.metric_calls))

    def fail_fidelity(self, candidate: str, detail: Mapping[str, Any]) -> None:
        evidence = {
            "schema_version": 2,
            "contract_version": _content_fidelity.CONTRACT_VERSION,
            "status": "failed",
            "source_image_sha256": hashlib.sha256(self.render_bytes).hexdigest(),
            "candidate": candidate,
            "candidate_sha256": _content_fidelity.text_sha256(candidate),
            "inventory": self.inventory,
            "detail": dict(detail),
            "formatter_rejected_output": self.rejected_output,
            "formatter_retry_reason": self.format_retry_reason,
            "formatter_rejected_retry": self.rejected_retry,
            "formatter_retry_refusal": self.retry_refusal,
        }
        path = self.job.out_json.with_name(self.job.out_json.stem + ".fidelity-failure.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_json_bytes(evidence)
        # Immutable content-addressed failures survive retry and later success.
        path = path.with_name(path.stem + "-" + hashlib.sha256(encoded).hexdigest()[:16] + path.suffix)
        if not path.exists():
            path.write_bytes(encoded)

    def fidelity_doubt(self, stage: str, detail: str) -> None:
        """A fidelity stage that could not be settled: a doubt, and no further
        stage on this page."""
        self.reviewing, self.fidelity_status = False, "doubt"
        self.doubts.append({"kind": _content_fidelity.DOUBT_KIND, "detail": (
            f"CONTENT FIDELITY UNRESOLVED ({stage}): {detail} - check the page against the image")})

    def review_changes(self, stage: str, evidence: Mapping[str, Any], adopted: bool) -> None:
        """Each edit a fidelity review made (or proposed, when set aside), for
        decisionChanges: what it replaced, with what, and its evidence."""
        for edit in evidence.get("edits") or []:
            self.decision_changes.append({
                "decision": "contentFidelity", "rule": stage, "region": edit.get("region_id"),
                "contextBefore": edit.get("before", ""), "contextAfter": edit.get("after", ""),
                "before": edit.get("old", ""), "after": edit.get("new", ""),
                "evidence": {"source": edit.get("evidence"), "adopted": adopted,
                             "reviewedResultSha256": evidence.get("reviewed_result_sha256")}})

    def image_review(self, candidate: str, stage: str) -> tuple[str, dict[str, Any] | None]:
        """CANDIDATE after the STAGE review, and the review's evidence."""
        try:
            result, evidence, used = _content_fidelity.review_page(
                self.client, self.render_bytes, candidate, self.inventory,
                self.arguments.max_tokens, stage=stage,
                table_orientation=self.table_orientation_mode)
        except _content_fidelity.FidelityError as error:
            self.fail_fidelity(candidate, {**error.diagnostic(), "stage": stage,
                                             "rejected_answer": getattr(error, "rejected_answer", None),
                                             "correction": getattr(error, "correction", None)})
            self.fidelity_doubt(stage, f"no valid review answer ({error}); the text is kept as it was before the review")
            return candidate, None
        self.add(used)
        adopted = True
        if stage == "coverage-review" and not evidence["complete"]:
            adopted = not evidence["feature_losses"] and not evidence["empty_retained_content"]
            self.fail_fidelity(result, {"code": "visual-uncertain", "review": evidence})
            open_items = [*(f"issue {i}" for i in evidence.get("unresolved_issue_ids") or []),
                          *(f"region {r['id']} {r['status']}" for r in evidence.get("regions") or []
                            if r.get("status") == "uncertain"),
                          *(f"table region {t} unmapped" for t in evidence.get("unmapped_table_regions") or []),
                          *(f"feature {f} lost" for f in evidence.get("feature_losses") or [])]
            self.fidelity_doubt(stage, f"coverage {evidence.get('verdict')}: "
                                + ("; ".join(open_items[:8]) or "not confirmed complete")
                                + ("; its edits are kept" if adopted else "; its edits are set aside"))
        self.review_changes(stage, evidence, adopted)
        return (result if adopted else candidate), evidence


@dataclass
class _MergeState:
    """Intermediate dual-engine merge state passed between stage helpers."""

    mode: str = "merge"
    order_text: str | None = None
    page_prefer: Any = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    agreement: float = 1.0
    moves: list[dict[str, Any]] = field(default_factory=list)
    aligned: list[dict[str, Any]] = field(default_factory=list)
    pieces: list[str] = field(default_factory=list)
    block_lengths: list[int] = field(default_factory=list)
    block_starts: list[int] = field(default_factory=list)
    block_ends: list[int] = field(default_factory=list)
    offsets_valid: bool = False
    book_heads: list[str] = field(default_factory=list)
    book_furniture: Any = frozenset()
    table_work: bool = False
    windowed: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    prefer: Any = None
    fallback: str = "A"
    starts: list[int] = field(default_factory=list)
    b_starts: list[int] = field(default_factory=list)
    bracket_rule: bool = False
    stream_a: Any = None
    stream_b: Any = None
    mark_rule: bool = False
    marks_a: Any = None
    marks_b: Any = None
    aliases: dict[str, Any] = field(default_factory=lambda: {"A": {}, "B": {}})
    figure_rule: bool = False
    page_figures: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    figure_glyphs: set[str] = field(default_factory=set)
    page_heads: list[str] = field(default_factory=list)
    page_furniture: Any = frozenset()
    agreed_segments: list[dict[str, Any]] = field(default_factory=list)
    b_settled: list[str] = field(default_factory=list)
    nominations: list[dict[str, Any]] = field(default_factory=list)
    span_at: list[int] | None = None
    b_lines: list[int] | None = None
    table_edge: dict[int, dict[str, Any]] = field(default_factory=dict)
    edge_written: dict[int, tuple[str, str]] = field(default_factory=dict)
    edge_placed: tuple[dict[int, str], dict[int, str]] = field(default_factory=lambda: ({}, {}))
    absorbed_into: dict[int, int] = field(default_factory=dict)
    absorbed_held: dict[int, str] = field(default_factory=dict)
    beside_table: dict[int, str] = field(default_factory=dict)
    format_input: str = ""
    format_system: str = FORMAT_SYSTEM
    punct_locked: bool = False
    punctuated: list[str] = field(default_factory=list)
    listed: set[int] = field(default_factory=set)
    whole_page_hint: str = ""
    placeholders: bool = False
    to_punctuate: list[int] = field(default_factory=list)
    table_added: Counter = field(default_factory=Counter)

def _prepare_engine_drafts(page: _PageState) -> None:
    """Handle single/missing engine flags, strip grounding, and cut truncated Engine B loops."""
    if page.job.missing_engine:
        page.doubts.append(missing_engine_doubt(page.job))
    if page.job.missing_engine == "A":
        # Engine B's text in engine A's place: without its grounding markup.
        page.draft = strip_grounding(page.draft).strip()
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
    page.cut_reading = ""
    page.b_cut_off = engine_b_cut_off(page.job.draft2_text) if page.draft2 is not None else None
    if page.b_cut_off is not None:
        whole_b = page.draft2 or ""
        kept_b, b_loops = cut_engine_b_loops(whole_b)
        cut = (f" and from character {len(CJK(whole_b[:b_loops[0][0]]))} on it wrote "
               f"「{' '.join(LOOP_MARKUP.sub(' ', loop_unit_from_tag(b_loops[0][2])).split())[:12]}」 "
               f"{(b_loops[0][1] - b_loops[0][0]) // len(b_loops[0][2])} times over" if b_loops else "")
        if len(CJK(kept_b)) >= UNCORROBORATED_WITNESS:
            page.draft2 = kept_b
            page.doubts.append({"kind": "engine-b-truncated", "detail": (
                f"ENGINE B CUT OFF: its reply for this page did not end (finish reason {page.b_cut_off}){cut}; "
                + (f"its reading is merged up to there ({len(CJK(kept_b))} of its {len(CJK(whole_b))} "
                   "characters; the loop is not merged)" if b_loops else
                   f"its reading is merged as far as it goes ({len(CJK(kept_b))} characters)")
                + ", so what the page holds past that is engine A's reading alone - check it against the "
                "image, or OCR the page again with engine B")})
        else:
            page.cut_reading, page.draft2 = whole_b, None
            page.doubts.append({"kind": "engine-b-truncated", "detail": (
                f"ENGINE B CUT OFF: its reply for this page did not end (finish reason {page.b_cut_off}){cut}, "
                f"leaving {len(CJK(kept_b))} of its {len(CJK(whole_b))} characters, so it is not merged as a "
                "second reading; the page is transcribed from the image with engine A's draft as its hint - "
                "check it against the image, or OCR the page again with engine B")})
    page.read_b = page.draft2 if page.draft2 is not None else (page.cut_reading or None)
    page.engine_a_characters = len(CJK(page.draft))
    page.engine_b_characters = len(CJK(page.draft2 or ""))

def _run_fidelity_inventory(page: _PageState) -> None:
    """Run pre-merge full-image page inventory when content fidelity is active."""
    # With the switch on, every page first receives a full-image inventory,
    # including pages for which both CJK streams are empty.  Packet replay
    # binds image and prompt; no standalone old sidecar can authorize a blank
    # or a content repair.
    if page.fidelity_on:
        try:
            page.inventory, inventory_metrics = _content_fidelity.inventory_page(
                page.client, page.render_bytes, [page.draft, page.draft2 or ""], page.arguments.max_tokens)
            page.add(inventory_metrics)
        except _content_fidelity.FidelityError as error:
            page.fail_fidelity(page.draft, {**error.diagnostic(), "stage": "page-inventory",
                                 "rejected_answer": getattr(error, "rejected_answer", None),
                                 "correction": getattr(error, "correction", None)})
            page.inventory = {}
            page.fidelity_doubt("page-inventory", f"no valid inventory answer ({error}); the page is not image-reviewed")

def _seal_blank_or_uncorroborated(page: _PageState) -> dict[str, Any] | None:
    """Detect blank or uncorroborated pages and seal them without merging."""
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
    page.unsupported = unsupported_reading(page.draft, page.read_b)
    page.engines_silent = not CJK(page.draft) and not CJK(page.read_b or "")
    page.non_cjk = non_cjk_text(page.draft, page.read_b, load_blocks(page.job.draft_json)) if page.engines_silent else ""
    if (page.reviewing and page.inventory["page_state"] == "blank" and (not page.engines_silent or page.non_cjk)
            and not page.unsupported):
        # The engines read text the inventory says is not there: their text is
        # merged (or transcribed) as usual, and a person looks (a blank answer
        # never empties a page the engines read).
        page.fidelity_doubt("page-inventory", (
            f"the inventory calls the page blank, but the engines read {len(CJK(page.draft))} and "
            f"{len(CJK(page.draft2 or ''))} characters; their text is kept, not image-reviewed"
            if not page.engines_silent else
            f"the inventory calls the page blank, but the engines read {len(page.non_cjk)} characters "
            "of text with no CJK in it; the page is transcribed from the image, not image-reviewed"))
    if ((page.reviewing and page.inventory["page_state"] == "blank") or (page.engines_silent and (page.reviewing or not page.non_cjk))
            or page.unsupported):
        if page.unsupported:
            page.doubts.append({"kind": "unsupported-reading", "detail": (
                f"one engine returned {len(CJK(page.unsupported))} characters the other "
                f"never saw, and the text repeats itself "
                f"({distinct_ngram_ratio(CJK(page.unsupported)):.3f} distinct 4-grams); "
                + ("requires a full-image transcription rather than a blank shortcut"
                   if page.reviewing and page.inventory["page_state"] != "blank" else "discarded as unsupported"))})
        page.writer_text = ""
        page.mode = "blank-page"
        if page.reviewing and page.inventory["page_state"] != "blank":
            page.mode = "image-recovery"
            page.writer_text, page.content_recovery = page.image_review(page.draft if not page.unsupported else "", "content-recovery")
            if page.reviewing:
                page.writer_text, page.coverage_review = page.image_review(page.writer_text, "coverage-review")
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "scanPage": page.job.scan_page,
            "status": "complete",
            "textAuthority": TEXT_AUTHORITY,
            "generatedAt": utc_now(),
            "model": page.client.model,
            "reasoningEffort": page.client.effort,
            **effort_policy_record(page.client),
            "punctuationMode": getattr(page.arguments, "punctuation_mode", "auto"),
            "punctuationModeEffective": punctuation_mode(page.arguments, page.client),
            "punctuationFusion": punctuation_fusion_summary([]),
            "renderFile": str(page.job.render),
            # An unsupported reading is not a clean page: a human should look.
            "auditorClean": not page.doubts,
            "mode": page.mode,
            **({"contentFidelityContract": _content_fidelity.CONTRACT_VERSION,
                "contentFidelityStatus": page.fidelity_status,
                "pageInventory": page.inventory or None,
                "contentRecovery": page.content_recovery,
                "coverageReview": page.coverage_review} if page.fidelity_on else {}),
            "inkFraction": round(page_ink_fraction(page.job.render), 6),
            "engineDisagreements": [],
            "auditorReport": "; ".join(d["detail"] for d in page.doubts),
            "doubts": page.doubts,
            # Kept, not dropped: if this judgement is ever wrong, the text is
            # still here to recover.
            "discardedOnBlankPage": page.unsupported[:2000] if page.unsupported else "",
            "adjudications": [],
            # Each edit a fidelity review made (switch --content-fidelity).
            **({"decisionChanges": page.decision_changes} if page.decision_changes else {}),
            "usage": page.metrics,
            "punctuationDensity": 0.0,
            "degraded": False,
            "seconds": round(time.monotonic() - page.started, 1),
            "calls": facade_attr("summarize_calls", summarize_calls)(page.ledger),
            "provenance": {
                **sealed_inputs(page.job),
                **decision_provenance(page.arguments),
                "markdownSha256": sha256_bytes(page.writer_text.encode("utf-8")),
            },
        }
        record["provenance"]["jsonPayloadSha256"] = sha256_bytes(
            canonical_json_bytes(record))
        seal_page(page.job, page.writer_text.encode("utf-8"), record, events=getattr(page.arguments, "packet_events", None))
        return record
    return None

def _transcribe_page(page: _PageState) -> None:
    """Fallback whole-page transcription when only one draft or non-CJK text exists."""
    # Single-engine fallback: no second reading to merge against, so the
    # model must transcribe. Less accurate - measured 4/11 vs 11/11 for two
    # agreeing engines - but it is all that is available.  The same for a
    # page of text with no CJK (non_cjk), which the merge cannot align:
    # both drafts are its hint.
    hint = (f"OCR 底稿（提示，非權威）：\n---\n{page.draft}\n---\n\n" if page.draft2 is None else
            f"OCR 底稿 A（提示，非權威）：\n---\n{page.draft}\n---\n\n"
            f"OCR 底稿 B（提示，非權威）：\n---\n{page.draft2}\n---\n\n")
    space_clause = space_clause_enabled(page.arguments)
    writer_system = WRITER_SYSTEM_SPACE_CLAUSE if space_clause else WRITER_SYSTEM
    page.writer_text, page.last_message = page.client.ask_answering(
        writer_system, hint + "睇頁圖，輸出呢一頁嘅忠實 Markdown 轉錄。",
        page.render_bytes, page.arguments.max_tokens, kind="writer")
    page.add(page.last_message)
    page.writer_text = strip_fence(page.writer_text)
    if space_clause and page.writer_text:
        page.writer_text = restore_clause_spaces("", page.writer_text, whole_page=True)
    if not page.writer_text and not page.non_cjk:
        raise ProofreadError("writer returned empty output")
    if not page.writer_text:
        # Sealed, not failed (a blank page is), but not clean: the engines
        # read text the transcription does not hold.
        page.doubts.append({"kind": "non-cjk-text-not-transcribed", "detail": (
            f"NO TEXT TRANSCRIBED: the engines read {len(page.non_cjk)} characters of text with no CJK "
            f"in it (「{page.non_cjk[:60]}」), and the transcription from the image is empty - "
            "check the page against the image")})
    page.resolved, page.mode = page.writer_text, "transcribe"
    if page.reviewing:
        page.writer_text, page.content_recovery = page.image_review(page.writer_text, "content-recovery")

def _prepare_merge_segments(page: _PageState) -> _MergeState:
    """Align both OCR drafts, quarantine Engine A loops, resolve order conflicts, and split segments."""
    merge = _MergeState()
    # MERGE. Where two independent engines agree, that reading is frozen and
    # no model is allowed to revise it. Only differences go to adjudication.
    page.mode = "merge"
    # On an order conflict, the engine whose reading the page takes whole.
    merge.order_text = None
    page.blocks = load_blocks(page.job.draft_json)
    # One looped engine-A block is set aside rather than the whole of engine
    # A's page - only when what is left then merges; otherwise the page is
    # judged as it always was (order_judgement).
    # The engine preference that settles this page's disagreements.
    merge.page_prefer = page.arguments.prefer_engine
    judged = order_judgement(page.blocks, page.draft, page.draft2, prefer=merge.page_prefer)
    merge.segments, merge.agreement, quarantine = judged["segments"], judged["agreement"], judged["quarantine"]
    if quarantine is not None:
        page.blocks, page.draft = quarantine["blocks"], quarantine["draft"]
        dropped = quarantine["dropped"]
        page.loops_dropped.append(dropped)
        found = dropped["engineBReading"]
        page.doubts.append({"kind": "engine-a-loop-dropped", "detail": (
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
    if merge.agreement < READING_ORDER_AGREEMENT:
        # The engines disagree about READING ORDER, not just characters -
        # observed on a vertical title page where one engine scrambled the
        # columns. Character alignment is meaningless then and produces
        # spliced nonsense, so take the preferred engine's page whole.
        page.mode = "merge-order-conflict"
        # Take the preferred engine's page whole - but NEVER at the cost of
        # most of the page.  Measured on a fiscal report (NLC) p.24: HunyuanOCR
        # read 36 characters (the title and running head, missing the table
        # entirely) while PaddleOCR read 151 including a complete 19-row
        # table.  Preferring B discarded the table, and because the loss
        # happened at merge time rather than format time, droppedRuns saw
        # nothing and the page was sealed auditorClean.  A preference is for
        # choosing between two readings of the same page, not for choosing
        # a fragment over a page.
        order_engine = (page.arguments.prefer_engine if page.arguments.prefer_engine in ("A", "B")
                        else getattr(page.arguments, "fallback_engine", "A"))
        preferred = CJK(page.draft2 if order_engine == "B" else page.draft)
        other = CJK(page.draft if order_engine == "B" else page.draft2)
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
        table_text = CJK("".join(str(b.get("text") or "") for b in page.blocks
                                 if b.get("label") == "table"))
        structured_only_in_other = bool(table_text) and (
            order_engine == "B") and (
            _covers(other, table_text) and not _covers(preferred, table_text))

        other_engine = "A" if order_engine == "B" else "B"
        if preferred_looped and not other_looped:
            chosen, merge.order_text, page.mode = other, other_engine, "merge-order-conflict-fell-back-degenerate"
        elif len(preferred) < len(other) * ORDER_CONFLICT_FLOOR and not other_looped:
            chosen, merge.order_text, page.mode = other, other_engine, "merge-order-conflict-fell-back"
        elif structured_only_in_other and not other_looped:
            chosen, merge.order_text, page.mode = other, other_engine, "merge-order-conflict-fell-back-structured"
        else:
            chosen, merge.order_text = preferred, order_engine
        merge.segments = [{"tag": "equal", "a": chosen,
                     "b": "", "before": "", "after": ""}]
    # Text engine B read in another order is placed once, at engine A's
    # site (decision D2, place_moved_once).
    merge.moves = []
    # The segments as aligned, before any run moved: what both engines read
    # in each block, for D22's region (rebuild_region), and the stretch a
    # moved run's site entry covers (site_span).
    merge.aligned = merge.segments
    if page.mode == "merge":
        merge.segments, merge.moves = place_moved_once(merge.segments)
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
    if page.mode == "merge" and merge.page_prefer in ("A", "B"):
        split_lengths = [len(CJK(b["text"])) for b in page.blocks]
        merge.segments, page.lopsided = split_lopsided_segments(
            merge.segments, list(itertools.accumulate(split_lengths))
            if page.blocks and sum(split_lengths) == len(CJK(page.draft)) else None, page.draft, page.draft2 or "", merge.page_prefer)
    merge.pieces = []
    # Block index and offset of each disagreement, straight from its position
    # in engine A's stream; text search at a block boundary was measured to
    # crop the running foot instead of a table's last figure and delete it.
    merge.block_lengths = [len(CJK(b["text"])) for b in page.blocks]
    merge.block_ends = list(itertools.accumulate(merge.block_lengths))
    merge.offsets_valid = bool(page.blocks) and sum(merge.block_lengths) == len(CJK(page.draft))
    merge.book_heads = sorted(set(profile_section(page.arguments, "layout").get("runningHeads") or []))
    merge.book_furniture = getattr(page.arguments, "furniture", frozenset())
    merge.table_work = merge.offsets_valid
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
    merge.windowed = {}
    witness_text = CJK(page.draft2)
    witness_looped = (len(witness_text) > UNCORROBORATED_MINIMUM
                      and distinct_ngram_ratio(witness_text) < DEGENERATE_NGRAM_RATIO)
    if (merge.table_work and page.mode == "merge-order-conflict" and merge.segments[0]["a"] == CJK(page.draft)
            and not witness_looped):
        for k, block in enumerate(page.blocks):
            if block.get("label") != "table":
                continue
            grid, why = engine_table(str(block.get("text") or ""), page.draft2, merge.book_heads, merge.book_furniture)
            if grid is None and any(sign in why for sign in
                                    ("in another order", "both hold", "page furniture")):
                # Engine A's reading of the rest of the page: where a line
                # of engine B's runs on into another block (window_merge).
                a_text, a_lo = CJK(page.draft), (merge.block_ends[k - 1] if k else 0)
                found = window_merge(block, page.draft2 or "", merge.book_heads, merge.book_furniture,
                                     elsewhere=a_text[:a_lo] + "\0" + a_text[merge.block_ends[k]:])
                if found:
                    merge.windowed[k] = [dict(x, window=k) for x in found]
    if merge.windowed:
        chosen_text, rebuilt, cursor = merge.segments[0]["a"], [], 0
        for k, (lo, hi) in enumerate(zip([0] + merge.block_ends[:-1], merge.block_ends)):
            if k not in merge.windowed:
                continue
            if lo > cursor:
                rebuilt.append({"tag": "equal", "a": chosen_text[cursor:lo], "b": "",
                                "before": "", "after": ""})
            rebuilt.extend(merge.windowed[k])
            cursor = hi
        if cursor < len(chosen_text):
            rebuilt.append({"tag": "equal", "a": chosen_text[cursor:], "b": "", "before": "", "after": ""})
        merge.segments = rebuilt
    merge.prefer = page.arguments.prefer_engine
    merge.fallback = getattr(page.arguments, "fallback_engine", "A")
    # A printed circle one engine read as the numeral 〇 and the other not
    # at all is a mark, not a character (is_lone_circle); it is dropped
    # here, before any budget is set aside for numerals.
    # A circle fused with a neighbouring substitution (A 〇旂, B 旒) is first
    # split off (split_edge_circle), where it would be dropped.
    split: list[dict[str, Any]] = []
    a_at = 0
    for x in merge.segments:
        # Engine B's reading of a set-aside block is kept whole, and a
        # window of an order-conflict table block (window_merge) keeps
        # its own segments.
        parts = (split_edge_circle(x) if not is_numeral_conflict(x) and not x.get(LOOPED_READING)
                 and "window" not in x else None)
        if parts:
            pieces_of, at = parts
            offset = a_at + sum(len(p["a"]) for p in pieces_of[:at])
            mark = pieces_of[at]
            if is_lone_circle(mark, locate_segment(page.blocks, merge.block_ends, merge.block_lengths,
                                                   merge.offsets_valid, offset, mark)[0]):
                if "bStart" in x:
                    pieces_of[0]["bStart"] = x["bStart"]
                    pieces_of[1]["bStart"] = x["bStart"] + len(pieces_of[0]["b"])
                split.extend(pieces_of)
                a_at += len(x["a"])
                continue
        split.append(x)
        a_at += len(x["a"])
    merge.segments = split
    if page.mode == "merge" and merge.offsets_valid:
        # A stretch engine A read twice, the copy beside it standing for
        # it (read_twice), is not written, and nothing is asked about it:
        # engine B's side of a substitution goes the usual way.
        for i, k in reversed(read_twice(merge.segments, page.blocks, merge.block_ends)):
            x = merge.segments[i]
            rest = ([{"tag": "insert", "a": "", "b": x["b"], "before": x["before"], "after": x["after"],
                      **({"bStart": x["bStart"]} if "bStart" in x else {})}] if x["b"] else [])
            copy = [dict(x, tag="delete", b="", settled=("", "engine-A-read-twice"))]
            # Engine B's side stands on the side of the stretch away from
            # the copy: where the copy follows (K == 0), before it - the
            # stretch is a second box on the next block's first line, and
            # what engine B read there ends the block before.
            merge.segments[i:i + 1] = rest + copy if k == 0 else copy + rest
    merge.starts = [0] + list(itertools.accumulate(len(x["a"]) for x in merge.segments))[:-1]
    # Where each segment's engine-B text is in engine B's own: in segment
    # order, unless text read in another order was placed at engine A's
    # site, when each segment says (bStart).
    merge.b_starts = ([x["bStart"] for x in merge.segments] if merge.segments and all("bStart" in x for x in merge.segments)
                else [0] + list(itertools.accumulate(len(x["b"]) for x in merge.segments))[:-1])
    # Each engine's own marks, for the bracket rule: a numeral one engine
    # read where the other printed a bracket is that bracket, and the
    # brackets the engines read are written back after punctuation.
    merge.bracket_rule = page.mode == "merge"
    merge.stream_a, merge.stream_b = engine_gaps(page.draft), engine_gaps(page.draft2)
    # And every printed mark, for the mark rule (decision D1): a character
    # one engine has in excess where the other printed a mark is that mark
    # misread, when the glyph is one this book's drafts show that engine
    # writing for marks (mark_explanation, mark_aliases).
    merge.mark_rule = page.mode == "merge"
    merge.marks_a, merge.marks_b = engine_marks(page.draft), engine_marks(page.draft2)
    merge.aliases = getattr(page.arguments, "mark_aliases", None) or {"A": {}, "B": {}}
    # Switch --keep-arabic-numerals: engine A's figures on this page
    # (arabic_figures, its ASCII dots before a figure a leader too) by
    # their gap in engine A's stream, each confirmed or not by the
    # numbering (figures_confirmed, with the book's figures before and
    # after the page) - a contents entry's page number (a figure ending a
    # line that holds a leader) needs none - and the glyphs engine B
    # writes for them or for marks on this book (figure_aliases,
    # mark_aliases).
    merge.figure_rule = getattr(page.arguments, "keep_arabic_numerals", False) and page.mode == "merge" and merge.offsets_valid
    merge.page_figures = {}
    return merge

def _adjudicate_and_resolve(page: _PageState, merge: _MergeState) -> None:
    """Apply rule filters, plan and run parallel adjudication, and assemble page.resolved."""
    merge.figure_glyphs = set()
    if merge.figure_rule:
        found_figures = arabic_figures(page.blocks, page.draft, dotted=True)
        for figure, confirmed in zip(found_figures, figures_confirmed(
                [f["value"] for f in found_figures],
                *figure_neighbours((getattr(page.arguments, "figure_evidence", None) or {}).get("numbering") or {},
                                   page.job.scan_page))):
            merge.page_figures.setdefault(figure["gap"], []).append(
                dict(figure, confirmed=confirmed or figure["leader"]))
        merge.figure_glyphs = set(merge.aliases.get("B") or {}) | set(getattr(page.arguments, "figure_aliases", None) or {})
        page.page_bracketed = bracketed_pairs(page.blocks)

    def figure_at(gap: int) -> dict[str, Any] | None:
        """The confirmed figure engine A read at GAP of its stream, alone there."""
        here = merge.page_figures.get(gap) or []
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
        return not (merge.prefer in ("A", "B") and not numeral_or_rare and not short_insertion(seg, a_start, b_start))

    # Decision D3: a short insertion only one engine read is asked of 裁決.
    insertion_rule = page.mode == "merge"
    merge.page_heads = sorted(set(profile_section(page.arguments, "layout").get("runningHeads") or []))
    merge.page_furniture = getattr(page.arguments, "furniture", frozenset())

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
        chars, at = (merge.stream_a[0], a_start) if seg["a"] else (merge.stream_b[0], b_start)
        if chars[at:at + len(own)] != own:
            return True
        lo, hi = at, at + len(own)
        for start in range(max(0, hi - FURNITURE_GRAM), min(lo, len(chars) - FURNITURE_GRAM) + 1):
            if chars[start:start + FURNITURE_GRAM] in merge.page_furniture:
                return False
        for head in merge.page_heads:
            for start in range(max(0, hi - len(head)), min(lo, len(chars) - len(head)) + 1):
                if is_running_head(chars[start:start + len(head)], [head]):
                    return False
        return True

    # Decision D15 (the user's ruling of 2026-09-27): the book's furniture
    # one engine read and the other did not is left out before anything
    # is asked.
    furniture_rule = page.mode == "merge"
    merge.block_starts = [0] + merge.block_ends[:-1]

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
        if len(own) < FURNITURE_GRAM or furniture_leftover(own, merge.page_furniture, ()):
            return None
        side = "A" if seg["a"] else "B"
        chars, at = (merge.stream_a[0], a_start) if side == "A" else (merge.stream_b[0], b_start)
        if chars[at:at + len(own)] != own:
            return None
        edge = at == 0 or at + len(own) == len(chars)
        whole = side == "A" and merge.offsets_valid and any(
            lo == a_start and hi == a_start + len(own) for lo, hi in zip(merge.block_starts, merge.block_ends))
        if not (edge or whole):
            return None
        return {"readBy": side, "characters": len(own), "wholeBlock": whole, "pageEdge": edge}

    # Switch --learned-heads: the learned running head, in the merge.
    learned_merge = learned_record(page.arguments) if page.mode == "merge" else None
    learned_merge_bands: list[dict[int, str]] = []

    def learned_merge_band() -> dict[int, str]:
        """Engine A's blocks in this page's learned band (learned_band),
        worked out once, when a reading needs it."""
        if not learned_merge_bands:
            learned_merge_bands.append(learned_band(page.blocks, learned_merge, page.job.scan_page))
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
        if not learned_merge or bool(seg["a"]) == bool(seg["b"]) or "window" in seg or not merge.offsets_valid:
            return None
        own = CJK(seg["a"] or seg["b"])
        left, found = learned_leftover(own, learned_merge, page.job.scan_page)
        if not own or left or not found["stems"]:
            return None
        a_end = a_start + len(seg["a"])
        if any(lo < a_start and a_end < hi
               and learned_leftover(merge.stream_a[0][lo:a_start], learned_merge, page.job.scan_page)[0]
               and learned_leftover(merge.stream_a[0][a_end:hi], learned_merge, page.job.scan_page)[0]
               for lo, hi in zip(merge.block_starts, merge.block_ends)):
            return None
        # The blocks it is (engine A's) or stands between (engine B's).
        where = [k for k, (lo, hi) in enumerate(zip(merge.block_starts, merge.block_ends))
                 if lo < hi and (lo < a_end and a_start < hi if seg["a"] else a_start in (lo, hi))]
        # A head learned by place alone (placed_heads) is the head only
        # in its band: engine A's reading, every block of it in the
        # page's learned band (learned_band, an anchor there only in the
        # band).  Engine B's reading has no place on the page, and a
        # chapter's title only engine B read on the chapter's first page
        # is those words too: asked of 裁決, as before.
        if needs_placed_stem(own, learned_merge, page.job.scan_page) and not (
                seg["a"] and where and all(k in learned_merge_band() for k in where)):
            return None
        return {"readBy": "A" if seg["a"] else "B", "characters": len(own),
                "blocks": [layout_index(page.blocks, k) for k in where],
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
        chars, at = (merge.stream_a[0], a_start) if side == "a" else (merge.stream_b[0], b_start)
        left = (furniture_leftover(chars, merge.page_furniture, merge.page_heads, (at, at + len(own)))
                if chars[at:at + len(own)] == own else furniture_leftover(own, merge.page_furniture, merge.page_heads))
        return side.upper() if len(own) > INSERTION_LIMIT and left <= INSERTION_LIMIT else None

    def boundary_of(seg: Mapping[str, Any], a_start: int) -> tuple[int, int | None] | None:
        """The layout blocks either side of text only engine B read where
        engine A's reading passes from one block to the next: (the block
        that ends there, the next block holding text, or None at the
        page's end); None inside a block, at the page's start, or when
        the blocks do not split engine A's stream.  boundary_crop shows
        both edges."""
        if seg["a"] or not seg["b"] or not merge.offsets_valid:
            return None
        if "window" in seg:
            # Engine B's reading of a window of an order-conflict table
            # block (window_merge): its place in engine A's stream is not
            # a place on the page - the block is the window's, and its
            # crop the block's (locate_segment).
            return None
        ending = next((j for j, (end, n) in enumerate(zip(merge.block_ends, merge.block_lengths))
                       if n and end == a_start), None)
        if ending is None:
            return None
        return ending, next((j for j in range(ending + 1, len(page.blocks)) if merge.block_lengths[j]), None)

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
        block, _ = locate_segment(page.blocks, merge.block_ends, merge.block_lengths, merge.offsets_valid, a_start, seg)
        position = next((i for i, b in enumerate(page.blocks) if b is block), None)
        return {"block": layout_index(page.blocks, position) if position is not None else None,
                "contextBefore": seg["before"], "contextAfter": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"]}

    own_reading = {"a": CJK(page.draft), "b": CJK(page.draft2 or "")}

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
                    page.explained[side.upper()].append((at + i1, at + i2))
    circles = set()
    for i, (x, a_at) in enumerate(zip(merge.segments, merge.starts)):
        if x["tag"] != "equal" and is_numeral_conflict(x) and is_lone_circle(
                x, locate_segment(page.blocks, merge.block_ends, merge.block_lengths, merge.offsets_valid, a_at, x)[0]):
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
    merge.b_settled = [engine_b_as_settled(x, a_at, b_at, (merge.stream_a, merge.stream_b) if merge.bracket_rule else None,
                                     (merge.marks_a, merge.marks_b, merge.aliases) if merge.mark_rule else None)
                 for x, a_at, b_at in zip(merge.segments, merge.starts, merge.b_starts)] if merge.table_work else []
    merge.nominations = nominate_tables(page.blocks, page.draft2) if merge.table_work and page.mode == "merge" else []
    if merge.table_work and page.mode == "merge":
        # Decision D21: the witness is engine B's reading with the glyphs
        # this page's rules settle as marks left out (marks_left_out).
        # A block nominated on engine B's reading as read stays nominated.
        witness, left_out = marks_left_out(merge.segments, merge.starts, merge.b_starts, merge.stream_b[0], merge.b_settled)
        if left_out:
            plain = {n["block"] for n in merge.nominations}
            merge.nominations = sorted(merge.nominations + [
                dict(n, witness={"marksLeftOut": left_out})
                for n in nominate_tables(page.blocks, witness) if n["block"] not in plain],
                key=lambda n: n["block"])
    union_pieces: dict[int, str] = {}

    def block_union(segs: Sequence[Mapping[str, Any]], seg_starts: Sequence[int],
                    seg_b_starts: Sequence[int], k: int,
                    skip: set[int] | frozenset[int] = frozenset()) -> tuple[list[int], dict[int, str]]:
        """Block K's disagreements the union may settle, and the pieces it
        settles (settle_by_union), for the segments SEGS."""
        lo, hi = (merge.block_ends[k - 1] if k else 0), merge.block_ends[k]
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

    for nomination in merge.nominations:
        in_block, settled = block_union(merge.segments, merge.starts, merge.b_starts, nomination["block"], circles)
        union_pieces.update(settled)
        nomination["union"] = {"settled": len(settled), "leftToUsualWay": len(in_block) - len(settled)}
    union_of = set(union_pieces)
    # Numeral and rare-codepoint conflicts are read off the crop on every
    # page; keep budget for them so the page's prose cannot use it up.
    protected_left = sum(1 for i, x in enumerate(merge.segments) if x["tag"] != "equal" and i not in circles
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
    for index, seg in enumerate(merge.segments):
        a_start = merge.starts[index]
        if seg["tag"] == "equal":
            merge.pieces.append(seg["a"])
            continue
        if seg.get(LOOPED_READING):
            # Engine A's reading of this region was a loop and is set
            # aside: engine B's is the only reading, not a disagreement.
            merge.pieces.append(seg["b"])
            page.disagreements.append({
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
                explain(seg, a_start, merge.b_starts[index])
            merge.pieces.append(piece)
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": piece, "resolved_by": how,
            })
            continue
        if index in circles:
            explain(seg, a_start, merge.b_starts[index])
            merge.pieces.append("")
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": "", "resolved_by": "circle-not-numeral",
            })
            continue
        surplus = lopsided_furniture(seg, a_start, merge.b_starts[index])
        if surplus:
            # The surplus of a split lopsided replacement that the book's
            # furniture explains (its running head with the folio beside
            # it, its recurring n-grams): left out, as the preference left
            # it out before the split - a running head is not revived as
            # body text.
            explain(seg, a_start, merge.b_starts[index])
            merge.pieces.append("")
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": "", "resolved_by": f"lopsided-surplus-furniture-{surplus}",
                "lopsided": seg["lopsided"],
            })
            continue
        furniture_found = furniture_insertion(seg, a_start, merge.b_starts[index])
        if furniture_found:
            # Decision D15: the book's furniture only one engine read, left out.
            changes.append((len(merge.pieces), {"decision": "D15", "rule": "furniture-insertion",
                                          **site(seg, a_start), "evidence": furniture_found}))
            explain(seg, a_start, merge.b_starts[index])
            merge.pieces.append("")
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": "", "resolved_by": f"furniture-one-engine-{furniture_found['readBy']}",
            })
            continue
        learned_found = learned_insertion(seg, a_start)
        if learned_found:
            # Switch --learned-heads: the learned running head only one
            # engine read, left out.
            changes.append((len(merge.pieces), {"decision": "learnedHeads", "rule": "learned-head-reading",
                                          **site(seg, a_start), "evidence": learned_found}))
            explain(seg, a_start, merge.b_starts[index])
            merge.pieces.append("")
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": "", "resolved_by": f"learned-head-one-engine-{learned_found['readBy']}",
            })
            continue
        if merge.bracket_rule and is_numeral_conflict(seg):
            settled = bracket_explanation(seg, a_start, merge.b_starts[index], merge.stream_a, merge.stream_b)
            if settled and settled[0] in ("A", "B"):
                protected_left -= 1
                explain(seg, a_start, merge.b_starts[index], {"a": settled[1], "b": settled[1]})
                if merge.figure_rule:
                    after_rules[index] = dict(seg, a=settled[1], b=settled[1])
                    if not seg["a"]:
                        figure_settled[index] = ("marks", bool(CJK(settled[1])))
                merge.pieces.append(settled[1])
                page.disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": settled[1], "resolved_by": f"bracket-not-numeral-{settled[0]}",
                })
                continue
            if settled:
                # Only the numerals left after the brackets go to 裁決.
                explain(seg, a_start, merge.b_starts[index], settled[1])
                seg = settled[1]
        if merge.mark_rule:
            marked = mark_explanation(seg, a_start, merge.b_starts[index], merge.marks_a, merge.marks_b, merge.aliases)
            withheld = marked and circle_withheld(
                seg, a_start, merge.b_starts[index], marked, merge.marks_a, merge.marks_b,
                locate_segment(page.blocks, merge.block_ends, merge.block_lengths, merge.offsets_valid, a_start, seg)[0])
            if withheld:
                # A circle that may be a zero or a placeholder: the segment
                # goes on whole, as with the rule off, and says why.
                seg = dict(seg, markWithheld={"marks": [list(pair) for pair in marked[2]], "why": withheld})
                marked = None
            if marked:
                changes.append((len(merge.pieces), {
                    "decision": "D1", "rule": "mark-not-character", **site(seg, a_start),
                    # The glyphs taken for marks and how often this book's
                    # drafts showed each at a mark the other engine printed.
                    "evidence": {"marks": [list(pair) for pair in marked[2]], "glyphsOf": marked[3],
                                 "aliases": {char: merge.aliases[marked[3]].get(char, {})
                                             for char, _ in marked[2]}}}))
            if marked and marked[0] in ("A", "B"):
                # Engine B read engine A's marks as characters (or the other
                # way round): both read the same characters otherwise.
                if is_numeral_conflict(seg) or is_rare_codepoint_conflict(seg):
                    protected_left -= 1
                explain(seg, a_start, merge.b_starts[index], {"a": marked[1], "b": marked[1]})
                if merge.figure_rule:
                    after_rules[index] = dict(seg, a=marked[1], b=marked[1])
                    if not seg["a"]:
                        figure_settled[index] = ("marks", bool(CJK(marked[1])))
                merge.pieces.append(marked[1])
                page.disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": marked[1], "resolved_by": f"mark-not-character-{marked[0]}",
                    "marks": [list(pair) for pair in marked[2]],
                })
                continue
            if marked:
                # Only the characters no mark accounts for go on (査丶 against
                # 查 becomes the variant question 査 against 查).
                explain(seg, a_start, merge.b_starts[index], marked[1])
                seg = marked[1]
        if merge.figure_rule:
            after_rules[index] = seg
            standing = figure_at(a_start) if not seg["a"] and seg["b"] else None
            if standing is not None and not figure_stand_ins(
                    seg["b"], standing, merge.figure_glyphs,
                    sum(1 for _, mark in (seg.get("markReduced") or {}).get("marks") or [] if mark in "（）")
                    + sum(len(bracket_kinds(gap)) for gap in
                          merge.stream_b[1][merge.b_starts[index]:merge.b_starts[index] + len(merge.segments[index]["b"]) + 1])):
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
                if to_judge(seg, a_start, merge.b_starts[index]):
                    budget = page.arguments.max_adjudications - (0 if numeral_or_rare else max(0, protected_left))
                    if len(planned) + figure_taken < budget:
                        figure_taken += 1
                explain(seg, a_start, merge.b_starts[index])
                figure_settled[index] = ("stand-in", True)
                merge.pieces.append("")
                page.disagreements.append({
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
            piece, how, unruled = resolve_variant(seg, page.arguments)
            merge.pieces.append(piece)
            page.variant_unruled.extend(unruled)
            page.disagreements.append({
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
        insertion = merge.prefer in ("A", "B") and not (numeral or rare) and short_insertion(seg, a_start, merge.b_starts[index])
        if merge.prefer in ("A", "B") and not (numeral or rare) and not insertion:
            # Deterministic: the better engine wins. No model call, so this
            # cannot introduce the variant-normalisation errors that every
            # model-chooses design produced (4-7/11 vs the engine's 10/11).
            # Numeral-only disagreements are the exception: see
            # is_numeral_conflict.
            pick = seg["b"] if merge.prefer == "B" else seg["a"]
            merge.pieces.append(pick or seg["a"] or seg["b"])
            page.disagreements.append({
                "context_before": seg["before"], "context_after": seg["after"],
                "engineA": seg["a"], "engineB": seg["b"],
                "resolved": pick or (seg["a"] or seg["b"]),
                "resolved_by": f"prefer-engine-{merge.prefer}",
                **reduced_by(seg),
            })
            continue
        budget = page.arguments.max_adjudications - (0 if (numeral or rare) else max(0, protected_left))
        if len(planned) + figure_taken >= budget:
            # Out of budget: the engine this book's profile measured as the
            # better reader (the stated preference, or the probe's leader
            # when the probe was not confident enough to state one).
            engine = merge.prefer if merge.prefer in ("A", "B") else merge.fallback
            pick = seg["b"] if engine == "B" else seg["a"]
            merge.pieces.append(pick or seg["a"] or seg["b"])
            page.disagreements.append({
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
            changes.append((len(merge.pieces), {
                "decision": "D3", "rule": "insertion-to-crop", **site(seg, a_start),
                "evidence": {"characters": len(CJK(seg["a"] or seg["b"])),
                             "readBy": "A" if seg["a"] else "B"}}))
            seg = dict(seg, insertionQuestion=True)
        block, offset = locate_segment(page.blocks, merge.block_ends, merge.block_lengths,
                                       merge.offsets_valid, a_start, seg)
        crop, edge = None, boundary_of(seg, a_start)
        crop_kind = "boundary"
        if edge is not None:
            # Between two layout blocks (or at the page's end): both
            # edges, not the next block's head alone (boundary_crop).
            crop, setting = boundary_crop(page.job.render, page.blocks[edge[0]],
                                          page.blocks[edge[1]] if edge[1] is not None else None,
                                          len(CJK(seg["b"])) or 1)
            if crop:
                shown = {"blocks": [layout_index(page.blocks, j) if j is not None else None for j in edge],
                         "set": setting}
                seg = dict(seg, boundaryCrop=shown)
                if insertion:
                    changes[-1][1]["evidence"]["boundaryCrop"] = shown
        if not crop:
            # A spot estimated at a column's end is shown with the
            # neighbouring column's end too (crop_glyph's ACROSS), and
            # 裁決 is told the crop has parts (COLUMN_BREAK_NOTE).
            across: dict[str, Any] = {}
            crop, crop_kind = facade_attr("segment_crop_kind", segment_crop_kind)(page.job.render, page.render_bytes, block, offset, seg, across=across)
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
        crop_path = page.job.out_json.with_name(
            f"{page.job.out_json.stem}-adjudication-{len(planned) + 1:02d}.png")
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
        read_at = {"a": (merge.stream_a[0], a_start), "b": (merge.stream_b[0], merge.b_starts[index])}

        def leftover(side: str) -> int:
            chars, at = read_at[side]
            own = CJK(seg[side])
            if chars[at:at + len(own)] == own:
                return furniture_leftover(chars, merge.page_furniture, merge.page_heads, (at, at + len(own)))
            return furniture_leftover(own, merge.page_furniture, merge.page_heads)

        seg = dict(seg, furnitureSides=[side for side in ("a", "b")
                                        if len(CJK(seg[side])) > ANSWER_DELETION_LIMIT
                                        and leftover(side) <= INSERTION_LIMIT],
                   # Engine B's reply was cut off and its reading merged up
                   # to the cut: the guard keeps none of its reading alone
                   # over 裁決's answer (adjudicate_segment, cutOffSides).
                   cutOffSides=["b"] if page.b_cut_off is not None else [],
                   # Where the spot is, for a D16 change if the answer is refused,
                   # and where each engine read it (explain).
                   answerSite=site(seg, a_start), streamAt=(a_start, merge.b_starts[index]))
        merge.pieces.append("")          # filled in below, in this order
        if crop_kind in ("glyph", "band") and block is not None:
            estimated[len(merge.pieces) - 1] = block
        planned.append((len(merge.pieces) - 1, seg, crop, crop_path, merge.prefer if merge.prefer in ("A", "B") else merge.fallback))

    # Each question's place (call_site: its spot, its crop, and where it
    # stands in the merged text), for the loop detector's entry of a call
    # on it; filled in once known.
    spot_sites: dict[int, dict[str, Any]] = {slot: {} for slot, *_ in planned}

    def adjudicate(item: tuple[int, dict[str, Any], bytes | None, Path, str]):
        slot, seg, crop, crop_path, engine = item
        with call_site(spot_sites[slot]):
            simplified = getattr(page.arguments, "simplified_script", False)
            answer = facade_attr("adjudicate_segment", adjudicate_segment)(page.client, crop, seg, page.arguments.max_tokens, fallback_engine=engine,
                                        note=(BOUNDARY_NOTES.get((seg.get("boundaryCrop") or {}).get("set"), "")
                                              or (COLUMN_BREAK_NOTE if seg.get("columnBreakCrop") else "")
                                              or WHOLE_CROP_NOTES.get(seg.get("wholeCrop"), "")),
                                        simplified=simplified)
            # 睇唔到 on a crop cut where the spot was estimated: once more,
            # on the whole block, which needs no blind read to hold it.
            answer, wider = widened_answer(page.client, seg, answer, page.job.render, estimated.get(slot),
                                           page.arguments.max_tokens, engine, simplified)
            if wider:
                # The pixels of the second look, beside the first's.
                wider_path = crop_path.with_name(crop_path.name.replace("-adjudication-", "-adjudication-wider-"))
                wider_path.write_bytes(wider)
                answer[1]["widened"]["crop"] = wider_path.name
                return answer
            return confirm_nothing(page.client, crop, seg, answer, page.arguments.max_tokens, engine)

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
        with cf.ThreadPoolExecutor(page.page_concurrency) as pool:
            answers = in_context(pool, adjudicate, planned)
        for (slot, asked, crop, crop_path, engine), (piece, record, m) in zip(planned, answers):
            page.add(m)
            merge.pieces[slot] = piece
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
                merge.pieces[slot] = ""
                record["figureKept"] = {"figure": figure_at(asked["figureAt"])["figure"], "read": read_figure,
                                        "resolvedBefore": record["resolved"]}
                record["resolved"] = ""
            page.adjudications.append(record)
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
                                     CJK("".join(merge.pieces[:slot])), CJK("".join(merge.pieces[slot + 1:])),
                                     asked["before"], asked["after"],
                                     simplified=getattr(page.arguments, "simplified_script", False))
                      if getattr(page.arguments, "neighbour_guard", False) and not CJK(asked["b"])
                      and asked.get("cropHoldsA") else None)
            if copied:
                answered = record["answered"]
                merge.pieces[slot] = asked["b"]
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
            near = reading_nearby(left_out, CJK("".join(merge.pieces[:slot])), CJK("".join(merge.pieces[slot + 1:])))
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
                        page.client, crop, asked,
                        (record["answered"], dict(record, resolved_from="adjudicator-empty"), {}),
                        page.arguments.max_tokens, engine)
                page.add(m)
                if "coverageRead" in checked:
                    record["coverageRead"] = checked["coverageRead"]
                    evidence["coverageRead"] = {key: checked["coverageRead"][key]
                                                for key in ("crop", "found", "reading", "confirmed")}
                    if not checked["coverageRead"]["confirmed"]:
                        near = None
            if near:
                # The answer stands: the reading is written nearby already.
                answered = record.pop("answered")
                merge.pieces[slot] = answered
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
        if getattr(page.arguments, "neighbour_guard", False):
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
                copied = neighbour_copy(merge.pieces[slot], asked["a"], asked["b"],
                                        CJK("".join(merge.pieces[:slot])), CJK("".join(merge.pieces[slot + 1:])),
                                        asked["before"], asked["after"],
                                        simplified=getattr(page.arguments, "simplified_script", False))
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
                answered = merge.pieces[slot]
                merge.pieces[slot] = asked[side]
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
            written = CJK(merge.pieces[slot])
            if sum(1 for r in readings if r) != 1 or len(written) < BESIDE_COPY_MIN or written not in readings:
                continue
            before, after = CJK("".join(merge.pieces[:slot])), CJK("".join(merge.pieces[slot + 1:]))
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
                    f"{asked['after'][:4]} (裁決 wrote 「{given}」; written {merge.pieces[slot][:8]})")
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
    merge.agreed_segments = merge.segments
    if planned:
        misread = judged_misreads([(asked, record) for (_, asked, _, _, _), (_, record, _)
                                   in zip(planned, answers)])
        spots = propagation_spots(merge.segments, merge.pieces, union_of, {slot for slot, *_ in planned}, misread)
        spots = spots[:max(0, page.arguments.max_adjudications - len(planned) - figure_taken)]

        # Each question's place (call_site), as its D24 entry gives it, for
        # the loop detector's entry of a call on it.
        propagated_sites: dict[tuple[int, int], dict[str, Any]] = {(i, p): {} for i, p, _ in spots}

        def propagate(spot: tuple[int, int, dict[str, Any]]):
            i, p, sub = spot
            block, offset = locate_segment(page.blocks, merge.block_ends, merge.block_lengths, merge.offsets_valid, merge.starts[i] + p, sub)
            crop = facade_attr("segment_crop", segment_crop)(page.job.render, page.render_bytes, block, offset, sub)
            with call_site(propagated_sites[(i, p)]):
                return crop, facade_attr("adjudicate_segment", adjudicate_segment)(page.client, crop, sub, page.arguments.max_tokens, fallback_engine="A",
                                                note=propagation_note(sub["a"], sub["b"]),
                                                simplified=getattr(page.arguments, "simplified_script", False))

        with cf.ThreadPoolExecutor(page.page_concurrency) as pool:
            propagated = in_context(pool, propagate, spots)
        for number, ((i, p, sub), (crop, (piece, record, m))) in enumerate(zip(spots, propagated), 1):
            page.add(m)
            # One character for one: never a deletion or an insertion of
            # text engine A read (an answer that is neither is not written).
            written = piece if len(CJK(piece)) == 1 else sub["a"]
            merge.pieces[i] = merge.pieces[i][:p] + written + merge.pieces[i][p + 1:]
            if written != sub["a"]:
                # Engine A's reading there is not required in a table
                # (engine B read none of it): what is written there is.
                if merge.agreed_segments is merge.segments:
                    merge.agreed_segments = list(merge.segments)
                merge.agreed_segments[i] = dict(merge.agreed_segments[i], a=merge.pieces[i])
            crop_path = page.job.out_json.with_name(
                f"{page.job.out_json.stem}-adjudication-{len(planned) + number:02d}.png")
            if crop:
                crop_path.write_bytes(crop)
            # Not an engine disagreement: 乙 is no engine's reading, so the
            # verdict says nothing about which engine reads better
            # (engineTally, the drift audit).
            record.update({"crop": crop_path.name if crop else None, "askedFor": "D24",
                           "trigger": "verdict-propagation",
                           "propagatedFrom": {"pair": [sub["a"], sub["b"]], "sites": misread[sub["a"]][1]}})
            page.adjudications.append(record)
            block, _ = locate_segment(page.blocks, merge.block_ends, merge.block_lengths, merge.offsets_valid, merge.starts[i] + p, sub)
            position = next((j for j, b in enumerate(page.blocks) if b is block), None)
            page.decision_changes.append({
                "decision": "D24", "rule": "verdict-propagated",
                "block": layout_index(page.blocks, position) if position is not None else None,
                "contextBefore": sub["before"], "contextAfter": sub["after"],
                "engineA": sub["a"], "engineB": "", "after": written,
                "mergedOffset": len(CJK("".join(merge.pieces[:i]))) + p,
                "evidence": {"pair": [sub["a"], sub["b"]], "judgedSites": misread[sub["a"]][1],
                             "resolved": piece}})
            propagated_sites[(i, p)].update({key: page.decision_changes[-1][key] for key in SITE_PLACE_KEYS},
                                            crop=record["crop"])
    if unjudged:
        page.doubts.append({"kind": "insertion-unjudged", "detail": (
            f"ONE-ENGINE INSERTION KEPT UNJUDGED: 裁決 budget spent; {len(unjudged)} short insertion(s) "
            "only one engine read were kept by the preference as before: " + "; ".join(unjudged[:6])
            + " - check them against the image")})
    if refused_deletions:
        page.doubts.append({"kind": "deletion-refused", "detail": (
            f"READING KEPT OVER A SHORTER ANSWER: at {len(refused_deletions)} spot(s) 裁決's answer (neither "
            f"engine's reading, or nothing printed) would have left out more than {ANSWER_DELETION_LIMIT} "
            "characters one engine read; that reading is kept: " + "; ".join(refused_deletions[:6])
            + " - check the text against the image")})
    if unconfirmed:
        page.doubts.append({"kind": "nothing-unconfirmed", "detail": (
            f"READING KEPT OVER AN UNCONFIRMED 無: at {len(unconfirmed)} spot(s) 裁決 answered that nothing is "
            "printed where one engine read text, on a crop cut where the spot was estimated to be, and a "
            "blind reading of that crop did not show the spot empty between the characters around it; "
            "the reading is kept: " + "; ".join(unconfirmed[:6]) + " - check it against the image")})
    if partial_insertions:
        page.doubts.append({"kind": "insertion-partial", "detail": (
            f"ONE-ENGINE INSERTION KEPT WHOLE: 裁決 said text is printed at {len(partial_insertions)} "
            "spot(s) only one engine read, but wrote fewer characters than that engine read; the "
            "engine's reading is kept: " + "; ".join(partial_insertions[:6])
            + " - count the characters against the image")})
    if beside_copies:
        page.doubts.append({"kind": "neighbour-copy", "detail": (
            f"ONE-ENGINE READING WRITTEN BESIDE ITS OWN COPY: at {len(beside_copies)} spot(s) 裁決 confirmed a "
            "reading only one engine made, and the same characters stand right beside the spot already; the "
            "engine may have read the line twice: " + "; ".join(beside_copies[:6])
            + " - check against the image whether the line is printed once or twice")})
    if script_converted:
        page.doubts.append({"kind": "answer-script-converted", "detail": (
            f"ANSWER IN TRADITIONAL SCRIPT WRITTEN IN SIMPLIFIED SCRIPT: at {len(script_converted)} spot(s) "
            "裁決's answer, in traditional script in a book printed in simplified script, is written in the "
            "form engine A read where engine B read another character, or in OpenCC's one simplified form, "
            "which neither engine read: " + "; ".join(script_converted[:6])
            + " - check the character against the image")})
    if neighbour_copies:
        page.doubts.append({"kind": "neighbour-copy", "detail": (
            f"ANSWER COPIED FROM BESIDE THE SPOT: at {len(neighbour_copies)} spot(s) 裁決's answer (neither "
            f"engine's reading) is the text the page has within {NEIGHBOUR_WINDOW} character(s) of the spot, "
            "which neither engine read at the spot; it is not written (--neighbour-guard) and an engine's "
            "reading is kept: " + "; ".join(neighbour_copies[:6])
            + " - check the spot against the image (a character printed twice there is written once)")})
    # Each decision's text change, now that every piece is settled: what it
    # wrote there and where that is in the merged text.
    for slot, change in changes:
        change["after"] = merge.pieces[slot]
        change["mergedOffset"] = len(CJK("".join(merge.pieces[:slot])))
        page.decision_changes.append(change)
    if page.loop_cuts:
        # And each question's, for the loop detector's entries (spot_sites).
        for slot, place in spot_sites.items():
            place["mergedOffset"] = len(CJK("".join(merge.pieces[:slot])))
    # Each lopsided replacement split (split_lopsided): what the merge wrote
    # for its pieces.
    for split in page.lopsided:
        slots = [i for i, x in enumerate(merge.segments) if (x.get("lopsided") or {}).get("id") == split["id"]]
        written = "".join(merge.pieces[i] for i in slots)
        split.update(written=written, pieces=len(slots))
        if slots:
            page.decision_changes.append({
                "decision": "textLoss", "rule": "lopsided-split", **site(split["segment"], split["aStart"]),
                "after": written, "mergedOffset": len(CJK("".join(merge.pieces[:slots[0]]))),
                "evidence": {"how": split["how"],
                             "pieces": [{"engineA": merge.segments[i]["a"], "engineB": merge.segments[i]["b"],
                                         "written": merge.pieces[i]} for i in slots]}})
    # Each run placed once (D2): at engine B's site the merge now writes only
    # what engine A never read, and the rest stands at engine A's site.
    # Both sites are sealed: engine A's site holds what the two copies
    # share and engine B's characters between them that face nothing of
    # engine A's, which the read site's entry alone did not show.
    draft_chars = "".join(x["a"] for x in merge.segments)

    def merged_offset(gap: int) -> int:
        """Where engine A's position GAP falls in the merged text: the start of
        the first segment at or after it, or the text's end (a run engine B
        read at the page's end, after all of engine A's text)."""
        return len(CJK("".join(merge.pieces[:bisect.bisect_left(merge.starts, gap)])))

    def within(tag_a: str, at: int, lo: int, hi: int) -> bool:
        """Whether a segment of engine-A text TAG_A starting at AT belongs to
        engine A's stretch [LO, HI): its text overlaps it, or it is text
        only engine B read strictly inside it."""
        return (at < hi and at + len(tag_a) > lo) if tag_a else lo < at < hi

    aligned_at = [0] + list(itertools.accumulate(len(x["a"]) for x in merge.aligned))[:-1]

    def site_span(move: Mapping[str, Any]) -> tuple[int, int]:
        """Engine A's stretch at a moved run's site, widened to the whole
        aligned segments the placement touched: what engine B read there
        beside the run, read otherwise than engine A, is in the entry."""
        lo, hi = move["placedAt"]["engineA"]
        touched = [k for k, x in enumerate(merge.aligned) if x["a"] and within(x["a"], aligned_at[k], lo, hi)]
        return (min([lo] + [aligned_at[k] for k in touched]),
                max([hi] + [aligned_at[k] + len(merge.aligned[k]["a"]) for k in touched]))

    def placed_site(lo: int, hi: int, placed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """The decisionChanges entry for engine A's stretch [LO, HI) where the
        runs PLACED were put: what is written there now, engine B's
        characters there that face nothing of engine A's included."""
        now = [i for i, x in enumerate(merge.segments) if within(x["a"], merge.starts[i], lo, hi)]
        there = {"a": draft_chars[lo:hi], "b": "".join(merge.segments[i]["b"] for i in now),
                 "before": draft_chars[max(0, lo - 8):lo], "after": draft_chars[hi:hi + 8]}
        return {
            "decision": "D2", "rule": "moved-placed-once", "moveSite": "placed",
            **site(there, lo), "mergedOffset": merged_offset(lo),
            "after": "".join(merge.pieces[i] for i in now),
            "evidence": {"moves": [{"engineBOffset": move["engineBOffset"], "readAt": move["readAt"],
                                    "matched": move["matched"]} for move in placed]}}

    for move in merge.moves:
        gap = move["readAt"]["engineAGap"]
        left = "".join(merge.pieces[i] for i, x in enumerate(merge.segments)
                       if x.get("movedRemainderOf") == move["engineBOffset"])
        read = {"tag": "insert", "a": "", "b": move["text"], "before": draft_chars[max(0, gap - 8):gap],
                "after": draft_chars[gap:gap + 8]}
        page.disagreements.append({
            "context_before": read["before"], "context_after": read["after"],
            "engineA": "", "engineB": move["text"], "resolved": left, "resolved_by": "moved-kept-once",
            "placedAt": move["placedAt"]["context"],
        })
        page.decision_changes.append({
            "decision": "D2", "rule": "moved-placed-once", "moveSite": "read",
            **site(read, gap), "mergedOffset": merged_offset(gap), "after": left, "evidence": {"placedAt": move["placedAt"], "matched": move["matched"],
                                        "engineBOffset": move["engineBOffset"]}})
    # Engine A's sites, one entry each: runs placed in one stretch (a label
    # and the value engine B read after it, both in one of engine A's
    # segments) share it, so no character is counted twice.
    placements = sorted((site_span(move), number) for number, move in enumerate(merge.moves))
    groups: list[list[Any]] = []
    for (lo, hi), number in placements:
        if groups and lo < groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], hi)
            groups[-1][2].append(merge.moves[number])
        else:
            groups.append([lo, hi, [merge.moves[number]]])
    page.decision_changes.extend(placed_site(lo, hi, placed) for lo, hi, placed in groups)
    if merge.figure_rule:
        page.figures_kept, page.arabic_figures_seen = arabic_figure_kept(
            merge.page_figures, merge.segments, merge.starts, merge.pieces, after_rules, figure_settled, page.blocks)
        page.figure_why = {g: entry["why"] for g, entry in zip(sorted(merge.page_figures), page.arabic_figures_seen)
                      if not entry["kept"]}
    if merge.moves:
        page.doubts.append({"kind": "moved-placed-once", "detail": (
            "TEXT READ IN ANOTHER ORDER PLACED ONCE: " + " | ".join(
                f"{move['text'][:12]}{'…' if len(move['text']) > 12 else ''} (engine B read it at "
                f"…{move['readAt']['context']}…, engine A at …{move['placedAt']['context']}…; kept at "
                f"engine A's{', what engine A never read left where engine B read it: ' + move['leftAtSite'] if move['leftAtSite'] else ''})"
                for move in merge.moves[:4])
            + " - check the order against the image")})
    page.resolved = "".join(merge.pieces)
    if not page.resolved.strip():
        raise ProofreadError("merge produced no text")

def _compute_beside_lost(page: _PageState, merge: _MergeState, text: str) -> str:
    """What TEXT lacks of the blocks beside a rebuilt table
    (beside_table), beyond what it lacks of the table blocks."""
    if not merge.beside_table:
        return ""
    agreed_now = [merge.absorbed_held.get(index, held_text) for index, held_text
                  in enumerate(agreed_block_text(page.blocks, merge.agreed_segments))]
    both = table_characters_lost(page.blocks, agreed_now, text, merge.book_heads, merge.book_furniture, merge.beside_table)
    tables_only = table_characters_lost(page.blocks, agreed_now, text, merge.book_heads, merge.book_furniture)
    short = Counter(both) - Counter(tables_only)
    out = []
    for char in both:
        if short[char] > 0:
            out.append(char)
            short[char] -= 1
    return "".join(out)

def _merge_tables(page: _PageState, merge: _MergeState) -> None:
    """Compute block spans, recover structured grids, rebuild nominated/windowed tables, and seed format input."""
    # Whatever rule dropped it - the preference, 裁決's answer, a
    # furniture test, the order conflict's choice of one reading - more
    # than ANSWER_DELETION_LIMIT characters one engine read that the
    # merged text holds nowhere leave the page unclean
    # (readings_dropped; the user's fix of 2026-09-27).
    page.readings_lost = readings_dropped({"A": page.draft, "B": page.draft2 or ""}, page.resolved, merge.page_furniture,
                                     merge.page_heads, {engine: set(merge.aliases.get(engine) or {})
                                                  for engine in ("A", "B")}, page.explained)
    for drop in page.readings_lost:
        key, asked_key = (("engineA", "draft_reading") if drop["engine"] == "A"
                          else ("engineB", "writer_reading"))
        grams = {drop["text"][i:i + DROP_GRAM] for i in range(len(drop["text"]) - DROP_GRAM + 1)}
        how = ([d.get("resolved_by") for d in page.disagreements
                if any(g in variant_fold(CJK(d.get(key) or "")) for g in map(variant_fold, grams))]
               + [a.get("resolved_from") for a in page.adjudications
                  if any(g in variant_fold(CJK(a.get(asked_key) or "")) for g in map(variant_fold, grams))])
        drop["by"] = sorted(set(filter(None, how))) or [page.mode]
    if page.readings_lost:
        page.doubts.append({"kind": "reading-dropped", "detail": (
            f"READING DROPPED AT MERGE: at {len(page.readings_lost)} spot(s) more than {ANSWER_DELETION_LIMIT} "
            "characters one engine read are nowhere in the merged text: " + "; ".join(
                f"engine {drop['engine']} 「{drop['text'][:20]}{'…' if len(drop['text']) > 20 else ''}」 "
                f"({drop['characters']}) at …{drop['contextBefore'][-4:]}|{drop['contextAfter'][:4]}… "
                f"({', '.join(drop['by'])})" for drop in page.readings_lost[:6])
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
    page.spans = (block_spans(page.blocks, merge.segments, merge.pieces, page.draft)
             if page.mode == "merge" or merge.order_text == "A" else None)

    # The table's edge text: what a table block's span holds before or
    # after the cells of the table that stands for it is written beside
    # the table, not dropped with the block's text (restate_table,
    # table_edges).  By block position, the stretches of the merged text
    # before and after each such table (recover_tables' `edges`; for a
    # rebuilt table, what of its region's span it does not hold at either
    # end).
    merge.table_edge = {}
    # And, by block position, the edge text as written before and after
    # the table, marks and all (set with the structure pass's input).
    merge.edge_written = {}
    # The merged characters whose place in each engine's reading is exact,
    # with that engine's own character there (engine_marks_merged,
    # engine_b_marks_merged): what each engine read of the edge text.
    merge.edge_placed = ({}, {})
    merge.span_at = (list(itertools.accumulate((len(CJK(span)) for span in page.spans), initial=0))
               if page.spans is not None else None)
    # The line or table cell of engine B's each merged character was read
    # in (engine_b_lines_merged): what engine B read with a table's first
    # or last cell on one line is that cell's (table_places), and the
    # edge text is judged line by line (edge_verdicts).
    merge.b_lines = engine_b_lines_merged(page.draft2, merge.segments, merge.pieces) if page.draft2 is not None else None
    if merge.b_lines is not None and len(merge.b_lines) != len(CJK(page.resolved)):
        merge.b_lines = None
    # Engine B's reading is the witness for the grid engine A built, and
    # the book's running heads tell a head row from a table row
    # (engine_table).
    page.tables, page.tables_rejected, page.table_orientation, page.table_sources, recovery = recover_tables(
        page.blocks, page.resolved, page.render_bytes, page.client, page.arguments.max_tokens,
        witness=page.draft2, heads=merge.book_heads, furniture=merge.book_furniture, spans=page.spans, lines=merge.b_lines,
        table_orientation=page.table_orientation_mode)
    page.recovery_packet_pending = bool(recovery.get("packetPending"))
    page.doubts.extend(recovery["doubts"])
    page.table_grouping = recovery["figureGrouping"]
    page.cell_direction = recovery["cellDirection"]
    page.table_orientation_evidence.extend(recovery.get("tableOrientationEvidence", []))
    merge.table_edge.update((index, dict(found, source="recovered"))
                      for index, found in recovery["edges"].items() if index in page.table_sources)

    # Tables rebuilt from the image, with their characters locked: a
    # block nominated as a missed table (its text is the union of both
    # readings), and a grid engine B contradicted on an order-conflict
    # page (its text from engine B's lines, window_merge).  What both
    # engines read there must be in the table, nothing else either read
    # may be added (rebuild_table).  A nominated block is only rebuilt
    # where the page has block spans: the table takes the block's place.
    page.table_nominations = [dict(n) for n in merge.nominations]
    candidates = ([(n["block"], "nominated") for n in page.table_nominations if page.spans is not None]
                  + [(k, "grid refused, text from engine B's lines") for k in sorted(merge.windowed)])
    if page.spans is None:
        for nomination in page.table_nominations:
            nomination["outcome"] = "not rebuilt: the page has no block spans"
    agreed_text = agreed_block_text(page.blocks, merge.agreed_segments) if candidates else []
    # Characters the rebuilt tables hold beyond the merged text (D20): the
    # structure pass writes the tables as they stand, so its character
    # check counts them as sanctioned too.
    merge.table_added = Counter()
    # Decision D22: a nominated block's rebuild also takes in the blocks
    # of its table the layout model split off (rebuild_region); each
    # block a table took in, by position, with the position of the block
    # whose table took it in.  Their text is the table's: it is not
    # written again beside it.
    merge.absorbed_into = {}
    # What of each block taken in its table must hold (both engines'
    # reading of it): the fallback and tableCharactersLost count it as
    # they count a table block's agreed text.
    merge.absorbed_held = {}
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
    merge.beside_table = {}
    # Characters no engine read that a rebuilt table may write, each
    # confirmed by 裁決 (the user's headerCharacters ruling, confirm_unread),
    # and the crops they were asked on.
    no_unread = getattr(page.arguments, "no_unread_table_characters", False)
    unread_crops: list[Path] = []
    # 裁決's answer on each spot asked (a retry that writes the same
    # character there is not asked again).
    unread_verdicts: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
    agreed_aligned = (agreed_block_text(page.blocks, merge.aligned) if page.mode == "merge"
                      and any(source == "nominated" for _, source in candidates) else [])
    render_size: tuple[int, int] | None = None
    # Engine B's reading as the page settled it: its glyphs the page's
    # rules take for printed marks (丶 for ，, 一 for a bracket) are no
    # reading of a character (the D20 lock, D22's witness).
    as_settled = [dict(x, b=b) for x, b in zip(merge.segments, merge.b_settled)] if candidates else []
    # The other engine's character at each merged character the engines
    # read one for one (merged_other_readings), for a rebuilt table's
    # edge text: set when first needed.
    other_readings: list[str] | None = None
    region_origin: list[str] = []
    for k, source in candidates:
        # Sealed as the layout's block number, as every block number is
        # (layout_index); k stays the block's position here.
        layout = layout_index(page.blocks, k)
        if k in merge.absorbed_into:
            for nomination in page.table_nominations:
                if nomination["block"] == k:
                    nomination["outcome"] = (f"taken into block {layout_index(page.blocks, merge.absorbed_into[k])}'s "
                                             "rebuilt table")
            continue
        region = [k]
        # What of each block taken in engine B read inside the region
        # (region_witness): the evidence that took it in.
        witnessed: dict[int, str] = {}
        if source == "nominated" and agreed_aligned:
            if render_size is None:
                from PIL import Image
                with Image.open(page.job.render) as image:
                    render_size = image.size
            region = sorted([k] + [j for j in rebuild_region(
                page.blocks, k, render_size, agreed_aligned,
                witness=lambda j, k=k: witnessed.setdefault(
                    j, region_witness(page.blocks, as_settled, merge.starts, k, j)),
                nominated={n["block"] for n in page.table_nominations}) if j not in merge.absorbed_into])

        def ask_region(region: list[int]) -> tuple[str, str, list, str | None,
                                                  dict[str, Any], dict[int, str]]:
            """The rebuild of REGION: its lock, its merged text, the runs
            the lock added, the table or None, the record, and what of
            each block taken in the table must hold."""
            text = ("\n".join(page.spans[j] for j in region) if page.spans is not None
                    else "".join(piece for x, piece in zip(merge.segments, merge.pieces) if x.get("window") == k))
            merged_text, lock_runs = text, []
            if source == "nominated":
                # Decision D20: a nominated block's table may hold what either
                # engine read there, each character at the higher engine's
                # count, not only what the merge wrote (rebuild_lock).  A
                # block read from engine B's lines keeps its lines' text:
                # engine A's reading of it is the grid refused for copies.
                bounds = [((merge.block_ends[j - 1] if j else 0), merge.block_ends[j]) for j in region]
                text, lock_runs = rebuild_lock(
                    merged_text, "\n".join(CJK(str(page.blocks[j]["text"])) for j in region),
                    "\n".join(engine_b_inside(as_settled, merge.starts, lo, hi) for lo, hi in bounds),
                    [run for lo, hi in bounds for run in unwritten_readings(as_settled, merge.starts, merge.pieces, lo, hi)])
            boxes = [page.blocks[j]["box"] for j in region]
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            union_box = [x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0]
            crop = upscale_crop(crop_block(page.job.render, union_box, pad=0.01)) or page.render_bytes
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
            table, rebuild = rebuild_table(page.client, crop, text, required, page.arguments.max_tokens,
                                           confirm=None if no_unread else lambda char, cell, at: confirm_spot(
                                               region, char, cell, at),
                                           table_orientation=page.table_orientation_mode)
            held = {}
            for j, counts in need.items():
                chars = []
                for ch in CJK(str(page.blocks[j].get("text") or "")):
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
            spot = unread_spot([CJK(str(page.blocks[j].get("text") or "")) for j in region], cell, at)
            if spot is None:
                return dict(asked, confirmed=False,
                            why="no stretch of its cell around it stands once in engine A's reading")
            j, position, left, right = spot
            key = (region[j], position, left, right, char)
            if key in unread_verdicts:
                return dict(asked, **unread_verdicts[key])
            seg = {"tag": "insert", "a": "", "b": char, "before": left, "after": right, "deletionGuard": False}
            spot_crop = unread_crop(page.job.render, page.render_bytes, page.blocks[region[j]], position, seg)
            unread_crops.append(page.job.out_json.with_name(
                f"{page.job.out_json.stem}-table-unread-{len(unread_crops) + 1:02d}.png"))
            if spot_crop:
                unread_crops[-1].write_bytes(spot_crop)
            # Its place (call_site): the spot's block and context, as its
            # adjudication record gives them, and where the region's text
            # begins in the merged text, as the table's entries give it.
            with call_site({"block": layout_index(page.blocks, region[j]), "contextBefore": left, "contextAfter": right,
                            "engineA": "", "engineB": "",
                            "mergedOffset": len(CJK("".join(page.spans[:region[0]]))) if page.spans is not None else None,
                            "crop": unread_crops[-1].name if spot_crop else None}):
                _, verdict, used = facade_attr("adjudicate_segment", adjudicate_segment)(page.client, spot_crop, seg, page.arguments.max_tokens,
                                                      note=UNREAD_NOTE,
                                                      simplified=getattr(page.arguments, "simplified_script", False))
            page.add(used)
            read = CJK(verdict["resolved"]) if verdict["resolved_from"] in ("adjudicator", "adjudicator-empty") else None
            verdict.update({"crop": unread_crops[-1].name if spot_crop else None, "askedFor": "headerCharacters",
                            "block": layout_index(page.blocks, region[j])})
            page.adjudications.append(verdict)
            unread_verdicts[key] = dict(block=layout_index(page.blocks, region[j]), crop=verdict["crop"],
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
                region_refused = {"region": [layout_index(page.blocks, j) for j in region if j != k],
                                  "reason": rebuild["reason"],
                                  **({"unreadAsked": rebuild["unreadAsked"]} if rebuild.get("unreadAsked") else {})}
                refused_held = {j: held[j] for j in region if j != k}
                region = [k]
                text, merged_text, lock_runs, table, rebuild, held = ask_region(region)
                if table is not None:
                    # The blocks the refused region named stay text beside
                    # the table rebuilt without them, and what both engines
                    # read of them must stay on the page (beside_table).
                    merge.beside_table.update(refused_held)
        except PacketPending:
            page.rebuild_packet_pending = True
            continue
        # The table as the rebuild answered it, in its order: before its
        # orientation, what of the region's text it holds in order
        # (rebuilt_edges).
        answered = table or ""
        rebuild = {"block": layout, "from": source, **rebuild,
                   **({"region": [layout_index(page.blocks, j) for j in region if j != k]} if len(region) > 1 else {}),
                   **({"regionRefused": region_refused} if region_refused else {}),
                   **({"lockAdded": [run for _, run in lock_runs]} if lock_runs else {})}
        page.table_rebuilds.append(rebuild)
        beyond = Counter(CJK(table)) - Counter(CJK(merged_text)) if table is not None else Counter()
        merge.table_added.update(beyond)
        # The characters no engine read that 裁決 confirmed (the user's
        # headerCharacters ruling, confirm_unread): theirs, not D20's.
        confirmed_unread = ([r for r in rebuild.get("unreadAsked") or [] if r.get("confirmed")]
                            if table is not None else [])
        beyond = beyond - Counter(r["char"] for r in confirmed_unread)
        at = len(CJK("".join(page.spans[:region[0]]))) if page.spans is not None else None
        merged_cjk = CJK(page.resolved)
        where = {"block": layout,
                 "contextBefore": merged_cjk[max(0, at - 8):at] if at is not None else "",
                 "contextAfter": merged_cjk[at + len(CJK(merged_text)):][:8] if at is not None else ""}
        if confirmed_unread:
            page.doubts.append({"kind": "table-unread-confirmed", "detail": (
                f"TABLE CHARACTER NO ENGINE READ: block {layout} ({source}): the rebuilt table writes "
                + "; ".join(f"「{r['char']}」 in 「{r['cell'][:12]}」 (crop {r['crop']})" for r in confirmed_unread)
                + ", which neither engine read; 裁決 read the same on its spot - check it against the image")})
            page.decision_changes.append({
                "decision": "headerCharacters", "rule": "table-unread-confirmed",
                **where, "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                "evidence": {"confirmed": [{key: r.get(key) for key in ("char", "cell", "at", "block", "crop",
                                                                         "answered")}
                                           for r in confirmed_unread]}})
        if table is not None and len(region) > 1:
            # Decision D22: the blocks taken in are the table's text.
            for j in region:
                if j != k:
                    merge.absorbed_into[j] = k
                    merge.absorbed_held[j] = held[j]
                    page.blocks[j] = dict(page.blocks[j], label="table", nominated=True, absorbedInto=layout)
            page.decision_changes.append({
                "decision": "D22", "rule": "rebuild-region", **where,
                "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                "evidence": {"region": [layout_index(page.blocks, j) for j in region if j != k],
                             "held": [held[j] for j in region if j != k]}})
        if beyond:
            # Characters the table holds that the merge did not write there
            # (decision D20).
            page.decision_changes.append({
                "decision": "D20", "rule": "rebuild-lock-both-readings", **where,
                "engineA": "".join(run for engine, run in lock_runs if engine == "A"),
                "engineB": "".join(run for engine, run in lock_runs if engine == "B"),
                "after": "".join(beyond.elements()), "mergedOffset": at,
                "evidence": {"lockAdded": [run for _, run in lock_runs],
                             "locked": rebuild["locked"], "merged": len(CJK(merged_text))}})
        nominated_by = next((n for n in page.table_nominations if n["block"] == k and source == "nominated"), None)
        if table is not None and nominated_by is not None and nominated_by.get("witness"):
            # Decision D21: nominated only on the witness with this page's
            # mark glyphs left out (marks_left_out).
            page.decision_changes.append({
                "decision": "D21", "rule": "nomination-witness-marks-left-out", **where,
                "engineA": CJK(str(page.blocks[k]["text"])), "engineB": "",
                "after": table, "mergedOffset": at,
                "evidence": {"signal": nominated_by["signal"], **nominated_by["witness"]}})
        for nomination in page.table_nominations:
            if nomination["block"] == k:
                nomination["outcome"] = rebuild["outcome"] + ": " + rebuild["reason"]
                if len(region) > 1:
                    nomination["region"] = [layout_index(page.blocks, j) for j in region if j != k]
                if region_refused:
                    nomination["regionRefused"] = region_refused
        if table is None:
            if not rebuild["reason"].startswith("not a table"):
                page.doubts.append({"kind": "table-rebuild-refused", "detail": (
                    f"TABLE REBUILD REFUSED: block {layout} ({source}): {rebuild['reason']}")})
            if page.spans is not None and CJK(text) and page.blocks[k].get("label") in FURNITURE_LABELS:
                # A table block gets no punctuation pass (FURNITURE_LABELS),
                # so its text goes to the structure pass bare, and that pass
                # may not add a mark: nothing reads the marks printed in it.
                # Such a page went whole before it had block spans, and the
                # formatter read them (sealed punctuation-whole-page).
                # Measured: a textbook table whose four printed marks are
                # all brackets, window-read, its rebuild refused - the page
                # was sealed with nothing saying its marks were never read.
                # A nominated block keeps its own label, and its pass.
                a_marks = punctuation_counts(str(page.blocks[k].get("text") or "").translate(MARK_FORMS))
                page.doubts.append({"kind": "table-marks-unread", "detail": (
                    f"TABLE MARKS NOT READ: block {layout} ({source}): no table was rebuilt "
                    f"({rebuild['reason'][:80]}), and a table block's text goes to the structure "
                    "pass with no punctuation pass and its marks locked"
                    + (f"; engine A read {sum(a_marks.values())} mark(s) in it ("
                       + "".join(f"{mark}×{n}" for mark, n in a_marks.items()) + ")" if a_marks else "")
                    + " - check the marks against the image")})
            continue
        as_written = table
        orientation_context = (f"rebuilt block {layout}, full-page boxes "
                               f"{[page.blocks[j].get('box') for j in region]}; source grid:\n{table}")
        # The rebuild was asked for the physical grid; the benchmark's rule
        # turns it by the page's print direction (orient_table) - unless
        # engine B's reading order shows the rows are the printed columns
        # already - and engine B's order or the image where that is unknown.
        try:
            oriented, how = orient_table(
                table, page.render_bytes, page.client, page.arguments.max_tokens,
                turn=lambda written: turn_records(written, page.draft2, ask=lambda grid: ask_first_record(
                    grid, page.render_bytes, page.client, page.arguments.max_tokens, context=orientation_context)),
                table_orientation=page.table_orientation_mode, context=orientation_context,
                witness=page.draft2, print_direction=page_print_direction(page.blocks, image_size(page.render_bytes)),
                physical=True)
            page.table_orientation_evidence.append(table_orientation_record(
                as_written, oriented, how, page.render_bytes, page.table_orientation_mode, layout))
            if orientation_undecided(how):
                page.doubts.append({"kind": "table-orientation-undecided", "detail": (
                    f"TABLE ORIENTATION UNDECIDED: block {layout}; kept as rebuilt: {how}")})
            if oriented != as_written:
                page.decision_changes.append({
                    "decision": "D23", "rule": "rebuilt-table-oriented",
                    **where, "engineA": "", "engineB": "", "after": oriented, "mergedOffset": at,
                    "evidence": {"orientation": how[:300]}})
            table = oriented
            table, directions = fix_cell_direction(
                table, page.client, page.arguments.max_tokens, block=layout, render_bytes=page.render_bytes,
                context=orientation_context + "\norientation: " + how,
                table_orientation=page.table_orientation_mode)
        except PacketPending:
            page.rebuild_packet_pending = True
            continue
        page.cell_direction = page.cell_direction + directions
        undecided = [record["read"] for record in directions if record["outcome"] == "undecided"]
        if undecided:
            page.doubts.append({"kind": "cell-direction-undecided", "detail": (
                f"CELL DIRECTION UNDECIDED: block {layout}: {len(undecided)} label cell(s) kept as read "
                f"({'、'.join(undecided[:6])}{'…' if len(undecided) > 6 else ''})")})
        page.table_orientation.append(f"block {layout}: rebuilt from the crop, {how}")
        # The brackets engine A printed in the region, written back
        # cell by cell (table_brackets): the bracket rule's write-back,
        # for a table no punctuation pass reads.
        table, brackets = table_brackets(table, [str(page.blocks[j].get("text") or "") for j in region])
        if brackets["inserted"] or brackets["moved"] or brackets["unplaced"]:
            rebuild["brackets"] = brackets
        if brackets["unplaced"]:
            page.doubts.append({"kind": "table-marks-unread", "detail": (
                f"TABLE MARKS NOT READ: block {layout} ({source}): the table was rebuilt, but "
                f"{len(brackets['unplaced'])} bracket(s) engine A read there stand in no cell of it "
                f"({'; '.join(brackets['unplaced'][:4])}) - check the marks against the image")})
        unused = Counter(CJK(text)) - Counter(CJK(table))
        rebuild["unused"] = "".join(unused.elements())[:200]
        page.doubts.append({"kind": "table-rebuilt-unvouched", "detail": (
            f"TABLE REBUILT FROM THE IMAGE: block {layout} ({source}); no engine grid vouches for "
            f"its layout" + (f"; {sum(unused.values())} character(s) one engine read there are "
                             f"not in it ({''.join(unused.elements())[:20]})" if unused else ""))})
        if page.blocks[k].get("label") != "table":
            page.blocks[k] = dict(page.blocks[k], label="table", nominated=True)
        if merge.span_at is not None:
            # The table's edge text: what of its region's span the rebuilt
            # table does not hold, from the region's first character on and
            # back from its last, stays text beside it (rebuilt_edges,
            # table_edges).  The rest it does not hold is the unvouched
            # doubt's, as before.
            merged_cjk = CJK(page.resolved)
            at_region = [p for j in region for p in range(merge.span_at[j], merge.span_at[j + 1])]
            if other_readings is None:
                other_readings = merged_other_readings(merge.segments, merge.pieces)
                region_origin = merged_origins(merge.segments, merge.pieces)
            lead, trail = rebuilt_edges(
                CJK(answered), "".join(merged_cjk[p] for p in at_region),
                [other_readings[p] if p < len(other_readings) else "" for p in at_region],
                [region_origin[p] if p < len(region_origin) else "" for p in at_region],
                merge.span_at[region[0] + 1] - merge.span_at[region[0]], merge.span_at[region[-1] + 1] - merge.span_at[region[-1]])
            lo, hi = merge.span_at[region[0]], merge.span_at[region[-1] + 1]
            if lead or trail:
                merge.table_edge[k] = {"lead": (lo, lo + lead), "trail": (hi - trail, hi),
                                 "restated": table, "outside": False, "source": "rebuilt"}
        # In page order with the recovered tables (token numbers, joins).
        in_order = sorted(zip(page.table_sources + [k], page.tables + [table]), key=lambda pair: pair[0])
        page.table_sources, page.tables = [s for s, _ in in_order], [t for _, t in in_order]
    # The whole-page formatter is told to write a doubled circle as 。。, as
    # the punctuator is.
    merge.format_input, merge.punct_locked = page.resolved, False
    merge.format_system = (
        FORMAT_SYSTEM_SPACE_CLAUSE if space_clause_enabled(page.arguments) else FORMAT_SYSTEM
    )
    merge.punctuated = []
    # The blocks listing entries with leaders, given engine A's line breaks
    # (break_lines).
    merge.listed = set()
    merge.whole_page_hint = ""
    if page.spans is None and CJK(page.resolved):
        # No block-by-block pass: the formatter punctuates the whole page
        # with no census to check it against.  Not clean - measured: such a
        # page came out with ，：「」 throughout a book printed with 。 only,
        # and was sealed clean - and the formatter is at least given what
        # the probe saw this book print.
        page.doubts.append({"kind": "punctuation-whole-page",
                       "detail": "PUNCTUATED AS A WHOLE PAGE: "
                                 + whole_page_reason(page.blocks, page.draft, page.mode, merge.order_text)
                                 + "; no mark count was taken, so check the marks against the image"})
        merge.whole_page_hint = notation_hint(page.arguments)
        # Switch --keep-arabic-numerals: the figures the merge keeps go
        # into the characters the formatter is given, at their gaps (the
        # merged text holds characters only, so a gap is its index).
        for kept in sorted(page.figures_kept, key=lambda f: -f["merged"]):
            kept["placed"], kept["given"] = "written", kept["merged"]
            merge.format_input = merge.format_input[:kept["merged"]] + kept["figure"] + merge.format_input[kept["merged"]:]

def _merge_punctuation(page: _PageState, merge: _MergeState) -> None:
    """Run per-block punctuation passes, rule mark/bracket/figure placement, and edge text."""
    space_clause = space_clause_enabled(page.arguments)
    if page.spans is not None:
        heads = set(profile_section(page.arguments, "layout").get("runningHeads") or [])
        hint = notation_hint(page.arguments)
        limit = density_limit(page.arguments)
        # Which blocks are punctuated at all is settled first; then every
        # (block, tile) pass runs at once.  The passes are independent - each
        # sees only its own crop and its own characters - so running them
        # together changes no answer, only how long the page takes.
        merge.punctuated = [""] * len(page.blocks)
        merge.to_punctuate = []
        # Switch --learned-heads: the page's blocks in the learned running
        # head's band (learned_band).
        learned = learned_record(page.arguments)
        learned_here = learned_band(page.blocks, learned, page.job.scan_page) if learned else {}

        for index, (block, span) in enumerate(zip(page.blocks, page.spans)):
            if heads and is_running_head(str(block["text"]), sorted(heads)) and \
                    is_running_head(span, sorted(heads)) and \
                    head_size_matches(block, str(block["text"]), page.arguments):
                # A running head the probe measured for this book (repeated
                # on the page, or on most pages): furniture by evidence.
                page.profile_furniture.append(CJK(span))
                continue
            found = (learned_head_block(span, learned_here.get(index), learned, page.job.scan_page, str(block["text"]))
                     if learned else None)
            if found:
                # Switch --learned-heads: a block of the band whose merged
                # text is all the learned running head - the title with the
                # label beside it, the label alone, the folio - is left out
                # before punctuation, so neither the structure pass nor its
                # fallback has it, whatever its layout label.  Off, it is
                # punctuated (a folio labelled number handed over as it is)
                # and the structure pass is asked to take the furniture out.
                page.learned_dropped.add(index)
                page.decision_changes.append({
                    "decision": "learnedHeads", "rule": "learned-head-block",
                    "block": layout_index(page.blocks, index),
                    "contextBefore": CJK("".join(page.spans[:index]))[-8:],
                    "contextAfter": CJK("".join(page.spans[index + 1:]))[:8],
                    "mergedOffset": sum(len(other) for other in page.spans[:index]),
                    "after": "", "evidence": {"label": block.get("label"), "text": span, **found}})
                continue
            if not CJK(span) or block["label"] in FURNITURE_LABELS:
                merge.punctuated[index] = span
                continue
            merge.to_punctuate.append(index)

        # Engine A's own marks, tied to the merged text's characters: what a
        # pass whose answers are both refused is held to (agreed_punctuation,
        # its marks FALLBACK_MARKS).
        a_marks_merged = engine_marks_merged(page.draft, merge.segments, merge.pieces, page.blocks, vocabulary=FALLBACK_MARKS)
        span_starts = [0] + list(itertools.accumulate(len(span) for span in page.spans))
        # The rule agreedMarks (C1): a block whose marks both engines read
        # alike (agreed_marks) is written with them, its census and
        # punctuate calls not made.  Not on a book whose profile saw
        # doubled circles, emphasis marks or space clauses: an engine's 。
        # there does not say whether one circle or two is printed, and
        # neither engine reads emphasis marks or clause spaces reliably.
        notation = profile_section(page.arguments, "notation")
        agreed: dict[int, str] = {}
        # The blocks taken only with --same-print-marks (agreed_marks, same_print).
        agreed_print: set[int] = set()
        # The blocks taken only with --same-marks-any-form (agreed_marks, FORM_FOLD).
        agreed_form: set[int] = set()
        if page.draft2 is not None and not (notation.get("doubled") or notation.get("emphasis") or space_clause):
            found_a = engine_places(engine_gaps(page.draft)[0], merge.segments, merge.pieces, page.blocks)
            readings = [engine_reading(page.draft, found_a[0] if found_a else None, span_starts[-1]),
                        engine_reading(page.draft2, engine_b_places(engine_gaps(page.draft2)[0], merge.segments, merge.pieces),
                                       span_starts[-1])]
            # Switch --same-marks-any-form: the two readings again, their forms made one.
            # The fold maps one character to one, so the characters' places are the same.
            folded = None
            if getattr(page.arguments, "same_marks_any_form", False):
                folded = [engine_reading(page.draft.translate(FORM_FOLD), found_a[0] if found_a else None, span_starts[-1]),
                          engine_reading(page.draft2.translate(FORM_FOLD),
                                         engine_b_places(engine_gaps(page.draft2)[0], merge.segments, merge.pieces), span_starts[-1])]
            for index in merge.to_punctuate:
                text = agreed_marks(page.spans[index], span_starts[index], readings)
                if text is None and getattr(page.arguments, "same_print_marks", False):
                    text = agreed_marks(page.spans[index], span_starts[index], readings, same_print=True)
                    if text is not None:
                        agreed_print.add(index)
                if text is None and folded is not None:
                    text = agreed_marks(page.spans[index], span_starts[index], folded, same_print=True, write=readings[0])
                    if text is not None:
                        agreed_form.add(index)
                if text is not None:
                    agreed[index] = text
            # A block with one place holding a mark, both engines' the same: taken too on a
            # page whose other blocks show the two engines read its marks alike
            # (ANY_FORM_PAGE_PLACES places in the blocks taken).  A block neither engine read
            # a mark in is still asked about.
            if folded is not None and sum(mark_places(text) for text in agreed.values()) >= ANY_FORM_PAGE_PLACES:
                for index in merge.to_punctuate:
                    if index not in agreed:
                        text = agreed_marks(page.spans[index], span_starts[index], folded, same_print=True,
                                            write=readings[0], minimum=1)
                        if text is not None:
                            agreed[index] = text
                            agreed_form.add(index)
        # Switch --unmarked-page-sample: on a page neither engine read
        # marks on, the longest UNMARKED_SAMPLE of the blocks engine A read
        # as characters alone are asked with the page's other blocks, and
        # the rest wait for their answers (unmarked_candidates).  Not on a
        # book whose profile saw doubled circles, emphasis marks or space
        # clauses, which no engine reads: the sample would find them, a
        # wave later.
        waiting: list[int] = []
        sampled: set[int] = set()
        unmarked: set[int] = set()
        if getattr(page.arguments, "unmarked_page_sample", False) and not (
                notation.get("doubled") or notation.get("emphasis") or space_clause):
            candidates = unmarked_candidates(page.blocks, page.spans, [index for index in merge.to_punctuate
                                                             if index not in agreed], page.draft2)
            if len(candidates) > UNMARKED_SAMPLE:
                sampled = set(sorted(candidates, key=lambda index: (-len(CJK(page.spans[index])), index))
                              [:UNMARKED_SAMPLE])
                waiting = [index for index in candidates if index not in sampled]

        def cut(index: int) -> tuple[bool, list[tuple[bytes | None, int, int]], int]:
            """Whether block INDEX is cut in column groups, its (crop, start,
            end) parts, and how far a part's text may be off its crop at
            either end (a column's worth; 0 for a block punctuated whole,
            where only variant forms are folded - realign_answer).  A block
            the rule agreedMarks writes is one part, with no crop."""
            block, span = page.blocks[index], page.spans[index]
            if index in agreed:
                return False, [(None, 0, len(span))], 0
            tiles = facade_attr("block_tiles", block_tiles)(page.job.render, block, len(span))
            return bool(tiles), tiles or [
                (upscale_crop(crop_block(page.job.render, block["box"], pad=0.012, pad_left=0.0))
                 or page.render_bytes, 0, len(span))], facade_attr("column_reach", column_reach)(page.job.render, block, len(span)) if tiles else 0

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
            with call_site({"block": layout_index(page.blocks, index), "mergedOffset": span_starts[index] + lo,
                            "range": [lo, hi], **({"tile": tile_number} if tiled else {})}):
                return punctuate_part(page.client, page.arguments, crop, page.spans[index][lo:hi], hint, limit,
                                      printed=printed, block=(page.spans[index], lo, hi, reach))

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

        with cf.ThreadPoolExecutor(page.page_concurrency) as pool:
            passes, answers = asked([index for index in merge.to_punctuate if index not in waiting])
            if waiting:
                # The sample is in.  Every pass of it answered and without
                # a mark: the blocks that waited are written as they are
                # (agreed, rule page-unmarked-sample).  A mark in one, or
                # an answer refused: they are asked as any block is.
                if all(record.get("ok") and not sum(punctuation_counts(out).values())
                       for (index, *_), (out, record, _) in zip(passes, answers) if index in sampled):
                    unmarked = set(waiting)
                    agreed.update((index, page.spans[index]) for index in waiting)
                more, more_answers = asked(waiting)
                for (index, *_), (_, record, _) in zip(passes, answers):
                    if index in sampled:
                        record["unmarkedSample"] = {"waiting": len(waiting), "taken": bool(unmarked)}
                # In the page's block order, as with no sample.
                place = {index: number for number, index in enumerate(merge.to_punctuate)}
                both = sorted(zip(passes + more, answers + more_answers),
                              key=lambda pair: (place[pair[0][0]], pair[0][1]))
                passes, answers = [pair[0] for pair in both], [pair[1] for pair in both]
        # Each pass's text with the stretch of its block it read: its own
        # [lo, hi), or where its answer was realigned (realign_answer).
        pieces_out: dict[int, list[tuple[str, int, int, int, int]]] = {index: [] for index in merge.to_punctuate}
        records_out: dict[int, list[dict[str, Any]]] = {index: [] for index in merge.to_punctuate}
        for (index, tile_number, _, lo, hi, tiled, _), (out, record, used) in zip(passes, answers):
            # Not `for metrics in used`: that rebinds the page's running
            # total, which add() then sums into itself (every seal before
            # this held the last punctuation call's usage, doubled).
            for used_metrics in used:
                page.add(used_metrics)
            read = (record.get("realigned") or {}).get("read") or [lo, hi]
            pieces_out[index].append((out, read[0], read[1], lo, hi))
            records_out[index].append(record)
            # range: where this pass sits in the block's text (the stretch
            # it read), so a later re-read of the whole block can still be
            # judged stretch by stretch against this pass's census.
            record.update({"block": layout_index(page.blocks, index), "label": page.blocks[index]["label"],
                           "range": list(read)})
            if tiled:
                record["tile"] = tile_number
            page.punctuation_passes.append(record)
        for index in merge.to_punctuate:
            merge.punctuated[index], unread = assemble_passes(
                page.spans[index], pieces_out[index], space_clause=space_clause
            )
            if space_clause:
                merge.punctuated[index] = merge.punctuated[index].strip(IDEOGRAPHIC_SPACE)
            if unread:
                # Realigned passes left characters between them that no
                # pass read: they stand, with no mark.
                page.doubts.append({"kind": "punctuation-unread", "detail": (
                    f"PUNCTUATION UNREAD: block {layout_index(page.blocks, index)}: "
                    + ", ".join(f"「{page.spans[index][lo:hi][:12]}」" for lo, hi in unread[:4])
                    + " lie between column groups whose answers read the text their crops show "
                    "(realigned), and no answer read them; any marks there are missing - "
                    "check them against the image")})
        # Switch --keep-arabic-numerals: the brackets the bracket rule
        # writes, by block (brackets_by_block, the figures' own left out) -
        # at a figure's gap, the brackets of other print (figure_in_gap).
        rule_brackets: dict[int, Counter] = {}
        if merge.bracket_rule:
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
            for kept in page.figures_kept:
                figure_brackets.update({(kept["merged"], "（"): kept["figure"].count("（"),
                                        (kept["merged"], "）"): kept["figure"].count("）")})
            per_block = brackets_by_block(merge.stream_a, merge.stream_b, merge.segments, merge.pieces, page.spans,
                                          own=figure_brackets if merge.figure_rule else None)
            rule_brackets = per_block
            for index in merge.to_punctuate:
                if index in per_block:
                    text, inserted, moved = place_brackets(merge.punctuated[index], page.spans[index], per_block[index])
                    merge.punctuated[index] = text
                    page.engine_brackets["inserted"] += inserted
                    page.engine_brackets["moved"] += moved
        for index in merge.to_punctuate:
            own = [entry for entry in page.punctuation_passes if entry["block"] == layout_index(page.blocks, index)]
            merge.punctuated[index], found = mark_changes(merge.punctuated[index], span_starts[index],
                                                    layout_index(page.blocks, index), own, page.arguments)
            page.decision_changes.extend(found)
        # A circled note marker both engines read at one place that the
        # block's pass left out is written in (place_note_markers): the
        # structure pass is given it, and restore_note_markers keeps it.
        markers_at = note_markers_by_block(agreed_note_markers(page.draft, page.draft2, merge.segments, merge.pieces, page.blocks),
                                           page.spans) if page.draft2 is not None else {}
        merged_chars = "".join(page.spans)
        for index in merge.to_punctuate:
            if index not in markers_at:
                continue
            merge.punctuated[index], written = place_note_markers(merge.punctuated[index], page.spans[index], markers_at[index])
            for g, found in written:
                at = span_starts[index] + g
                page.decision_changes.append({
                    "decision": "textLoss", "rule": "note-marker-both-engines",
                    "block": layout_index(page.blocks, index),
                    "contextBefore": merged_chars[max(0, at - 8):at], "contextAfter": merged_chars[at:at + 8],
                    "engineA": found["raw"], "engineB": found["rawB"], "after": found["marker"],
                    "mergedOffset": at, "evidence": {"blockGap": g}})
        if page.figures_kept:
            # Switch --keep-arabic-numerals: each figure the merge keeps is
            # written into its block at its gap, among the marks the block
            # pass and the bracket rule wrote there (place_arabic_figures).
            by_block: dict[int, list[tuple[dict[str, Any], int]]] = {}
            for kept in page.figures_kept:
                k = kept["block"]
                at = kept["merged"] - span_starts[k]
                if k in merge.absorbed_into or k in page.table_sources or page.blocks[k].get("label") == "table":
                    kept["placed"] = "in a table"
                elif k not in merge.to_punctuate and (CJK(page.spans[k]) or page.blocks[k]["label"] in FURNITURE_LABELS):
                    kept["placed"] = "in a block not punctuated"
                elif not 0 <= at <= len(page.spans[k]) or (kept["at"] == 0 and at != 0):
                    kept["placed"] = "unplaced"
                else:
                    by_block.setdefault(k, []).append((kept, at))
            for k, here in by_block.items():
                # The brackets of other print at each figure's gap: those
                # the bracket rule wrote there, or with the rule off those
                # engine A read there besides the figure's.
                merge.punctuated[k], placed_how = place_arabic_figures(
                    merge.punctuated[k], page.spans[k],
                    [(at, kept["figure"], kept["after"],
                      {bracket: rule_brackets.get(k, Counter())[(at, bracket)] for bracket in "（）"}
                      if merge.bracket_rule else kept.get("otherBrackets") or {})
                     for kept, at in here])
                for (kept, at), placed_as in zip(here, placed_how):
                    kept["placed"], kept["blockGap"] = placed_as, at
        places_a = engine_places(engine_gaps(page.draft)[0], merge.segments, merge.pieces, page.blocks)
        # A block listing entries with leaders (a contents page) keeps
        # engine A's line breaks: before the place in the merged text of
        # the first character of each of its printed lines, where that
        # place is exact (engine_places).  Before, not after the last
        # character of the line before: what engine B alone read between
        # them (a name's character read twice) stays with the line before,
        # with its leader and page number.  The structure pass is given
        # one printed line a line, and its fallback writes one a paragraph
        # (leader_list_breaks, break_lines).
        for index in merge.to_punctuate:
            at = set()
            for g in leader_list_breaks(str(page.blocks[index]["text"])) if places_a else []:
                if merge.block_starts[index] + g in places_a[0]:
                    at.add(places_a[0][merge.block_starts[index] + g] - span_starts[index])
            if at:
                merge.punctuated[index] = break_lines(merge.punctuated[index], page.spans[index], at)
                merge.listed.add(index)
        # A recovered table never goes through the formatter: its block
        # stands in the text as one token line (place_recovered_tables).
        tokens = dict(zip(page.table_sources, table_tokens(len(page.tables))))
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
        for index in list(merge.table_edge):
            if index not in tokens or index in merge.absorbed_into:
                del merge.table_edge[index]
        if any(found["lead"][1] > found["lead"][0] or found["trail"][1] > found["trail"][0]
               for found in merge.table_edge.values()):
            a_placed, b_placed = merge.edge_placed
            engines_marks = [(engine_marks_merged(page.draft, merge.segments, merge.pieces, page.blocks, placed=a_placed,
                                                  written=True), a_placed),
                             (engine_b_marks_merged(page.draft2, merge.segments, merge.pieces, placed=b_placed, written=True)
                              if page.draft2 is not None else None, b_placed)]
            merged_cjk = CJK(page.resolved)
            # Whose reading each merged character is: a copy elsewhere
            # counts only where engine A's reading alone wrote it.
            origin = merged_origins(merge.segments, merge.pieces)
            # Each of engine A's blocks' stretch of the merged text: a
            # piece of edge text that is the whole of one is engine B's
            # reading of it (block_copy).
            blocks_at = ([(layout_index(page.blocks, j), merge.span_at[j], merge.span_at[j + 1]) for j in range(len(page.blocks))]
                         if merge.span_at is not None else [])
            for index, found in merge.table_edge.items():
                written = []
                for side, (lo, hi) in (("before", found["lead"]), ("after", found["trail"])):
                    parts = edge_verdicts(merged_cjk, lo, hi, page.draft, page.draft2,
                                          origin if len(origin) == len(merged_cjk) else None, merge.b_lines,
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
                merge.edge_written[index] = (written[0], written[1])
        # A block a rebuilt table took in (D22) is the table's text.
        merge.format_input = "\n".join(
            "\n".join(part for part in (merge.edge_written.get(index, ("", ""))[0], tokens[index],
                                        merge.edge_written.get(index, ("", ""))[1]) if part)
            if index in tokens else text
            for index, text in enumerate(merge.punctuated) if index not in merge.absorbed_into)
        if page.figures_kept:
            # Where each figure written into a block stands in the text the
            # structure pass is given, as CJK counts its characters: the
            # gap the structure pass must keep it at (restore_arabic_figures).
            running, first_of = 0, {}
            for index, text in enumerate(merge.punctuated):
                if index in merge.absorbed_into:
                    continue
                first_of[index] = running
                running += len(CJK("".join(merge.edge_written.get(index, ("", ""))) if index in tokens else text))
            for kept in page.figures_kept:
                if (kept.get("placed") in FIGURE_AT_GAP and kept["block"] in first_of
                        and kept["block"] not in tokens):
                    kept["given"] = first_of[kept["block"]] + kept["blockGap"]
        merge.format_system, merge.punct_locked = (
            STRUCTURE_SYSTEM_SPACE_CLAUSE if space_clause else STRUCTURE_SYSTEM
        ), True
    merge.placeholders = merge.punct_locked and bool(page.tables)
    if page.recovery_packet_pending or page.rebuild_packet_pending:
        raise PacketPending("table-wave")

def _merge_format_and_enforce(page: _PageState, merge: _MergeState) -> None:
    """Run pre-format recovery, Stage B structure/format pass, retry validation, table enforcement, and doubts."""
    def expanded(text: str) -> str:
        """TEXT with each table token (⟦Tn⟧) written out as its table."""
        if not merge.placeholders:
            return text
        out, misplaced_tokens, _ = place_recovered_tables(text, page.tables)
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
    if page.reviewing:
        candidate = expanded(merge.format_input)
        reviewed, page.content_recovery = page.image_review(candidate, "content-recovery")
        if reviewed != candidate:
            if table_spans(candidate):
                # Keep a recovered table's confirmed repairs when the
                # formatter copies it.  A changed table count cannot be
                # mapped to old block IDs safely; final image coverage
                # will verify those regions without such mapping.  (On
                # the whole-page path the tables are not in the
                # candidate but in the formatter's hint: nothing to map.)
                grids = [reviewed[a:b] for a, b in table_spans(reviewed)]
                page.source_table_mapping_current, page.recovery_table_correspondence = (
                    _content_fidelity.refresh_table_mapping(page.tables, grids))
                if page.source_table_mapping_current:
                    page.tables = grids
            if merge.placeholders and page.source_table_mapping_current:
                # The reviewed text with its tables as tokens again.
                retokened = reviewed
                for (start, stop), token in reversed(list(zip(table_spans(reviewed),
                                                              table_tokens(len(page.tables))))):
                    retokened = retokened[:start] + token + retokened[stop:]
                merge.format_input = retokened
            else:
                merge.format_input, merge.placeholders = reviewed, False
    # What the formatter is given, its tables written out: what its
    # answer is checked against once the tokens are replaced.
    expanded_input = expanded(merge.format_input)

    # Stage B - structure.  With punctuation locked the model may only add
    # headings, lists, tables and remove furniture; never touch a mark.
    table_hint = ""
    if merge.placeholders:
        table_hint = (
            f"\n文本入面有 {len(page.tables)} 行表格記號（{'、'.join(table_tokens(len(page.tables)))}）："
            "每一行代表一個已經由引擎格仔定咗嘅表格，內容同方向都唔使你理。"
            "每行記號原封不動照抄一次，自己一行，擺喺個表喺頁上嘅位置（通常就係佢喺文本入面嘅位置）；"
            "唔好自己寫呢啲表，唔好刪走、改寫或者重複記號。\n")
    elif page.tables and page.source_table_mapping_current:
        # The whole-page path has no block spans to put a token in: the
        # formatter copies the recovered tables, with their label cells
        # as the engines read them (cells_as_read: the deletion guard
        # below compares runs of the merged text, table included), and
        # enforce_recovered_tables puts back what it changes, turning
        # the cells fix_cell_direction decided.
        joined = "\n\n".join(
            cells_as_read(table, [record for record in page.cell_direction
                                 if record.get("block") == layout_index(page.blocks, source)])
            for source, table in zip(page.table_sources, page.tables))
        orient_note = (
            "呢啲表保持**版面原印方向**，你唔使轉置，原樣照抄結構。\n"
            if page.table_orientation_mode == "as-printed"
            else "呢啲表已經**擺返做閱讀次序**（該轉置嘅已經轉咗），你唔使再轉，照抄結構。\n"
        )
        table_hint = (
            f"\n呢一頁有 {len(page.tables)} 個表格，下面係已經由引擎格仔定咗嘅表：\n---\n"
            f"{joined}\n---\n"
            f"{orient_note}"
            "冇合併格嘅表係 Markdown 表，照抄做 Markdown 表；有合併格（一格跨幾行或幾欄）嘅表係 "
            "HTML `<table>`，照抄做 HTML，rowspan／colspan 一個都唔准改，"
            "唔准拆返做 Markdown 表（Markdown 表表達唔到合併格），亦唔准寫成散文；"
            "兩個表就寫兩個表，唔准併埋一個；格入面嘅字一個都唔准改、唔准倒轉。\n"
            "表格入面嘅字已經包含喺上面『已定嘅字』入面，唔好喺表外重複寫一次。\n")
    if any(block.get("label") == "table" and index not in page.table_sources and index not in merge.absorbed_into
           for index, block in enumerate(page.blocks)):
        # Tables the formatter must build from the image: the engine's grid
        # was refused (tablesRejected) or not used, so which way a record
        # runs is the formatter's to read off the page (unless as-printed).
        if page.table_orientation_mode == "as-printed":
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
    space_clause = space_clause_enabled(page.arguments)
    furniture_reviewed = page.content_recovery is not None
    if furniture_reviewed:
        merge.format_system = {
            FORMAT_SYSTEM: FORMAT_SYSTEM_REVIEWED,
            STRUCTURE_SYSTEM: STRUCTURE_SYSTEM_REVIEWED,
            FORMAT_SYSTEM_SPACE_CLAUSE: FORMAT_SYSTEM_REVIEWED_SPACE_CLAUSE,
            STRUCTURE_SYSTEM_SPACE_CLAUSE: STRUCTURE_SYSTEM_REVIEWED_SPACE_CLAUSE,
        }[merge.format_system]
    keep_or_delete = ("保留全部來源覆核後嘅字、數字、算式同符號" if furniture_reviewed
                      else "刪走欄外書眉／頁碼")
    layout = profile_section(page.arguments, "layout")
    layout_hint = ""
    if (layout.get("leavesPerPage") or 1) > 1:
        layout_hint += (f"本書係影印本：量度過，一頁通常印咗 {layout['leavesPerPage']} 葉原書，"
                        + ("每葉有自己嘅書眉同葉碼；正文按葉嘅次序接落去。\n" if furniture_reviewed else
                           "每葉有自己嘅書眉同葉碼——全部係家具，刪走；正文按葉嘅次序接落去。\n"))
    if layout.get("runningHeads") and not furniture_reviewed:
        layout_hint += ("以下字串喺本書量度到係書眉（同一頁重複或者差唔多每頁都有）："
                        + "、".join(layout["runningHeads"][:8]) + "；出現喺正文以外就刪走。\n")
    if space_clause and merge.punct_locked:
        layout_hint += ("文本入面嘅全形空格「　」（U+3000）係句讀停頓位，同段內原樣保留；"
                        "如該處實為新段落／標題分界，改用換行分段，行首同行尾唔留「　」。\n")
    # Switch --learned-heads: a page whose every block with text was the
    # learned running head has nothing left for the structure pass, which
    # given nothing answers nothing, a failed pass (measured on the
    # offline replay of a book's sealed pages: its two pages printing the
    # running head alone were sealed structure-rejected).
    head_only = bool(page.learned_dropped) and merge.punct_locked and not CJK(merge.format_input) and not page.tables
    if head_only:
        # No format call, so none cut off (the seal's "degraded").
        formatted, page.last_message = "", {}
    else:
        page.structure_input = structure_input_record(merge.format_input)
        formatted, page.last_message = page.client.ask_answering(
            merge.format_system,
            (f"字同標點都已定（唔准改）：\n---\n{merge.format_input}\n---\n{table_hint}{layout_hint}\n"
             f"睇頁圖，加版面結構（heading／段落／list／表格），{keep_or_delete}。標點原封不動。")
            if merge.punct_locked else
            (f"已定嘅字（唔准改）：\n---\n{merge.format_input}\n---\n{table_hint}\n{merge.whole_page_hint}"
             f"睇頁圖，加返句讀同版面結構，{keep_or_delete}。"),
            page.render_bytes, page.arguments.max_tokens, kind="format")
        page.add(page.last_message)
    formatted = strip_fence(formatted)
    # An answer that is none - empty, or cut off at every effort - is a
    # failed pass, whatever the checks below would say of it (no_answer).
    silent = None if head_only else no_answer(formatted, page.last_message)
    # The tables go in where their tokens are before anything is checked,
    # so every check below sees the page as it will be written.
    placement_notes: list[str] = []
    misplaced = None
    if merge.placeholders:
        formatted, misplaced, placement_notes = place_recovered_tables(formatted, page.tables)
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
    note_blocks = note_block_texts(page.blocks)
    if not silent and not misplaced:
        formatted, markers_restored = restore_note_markers(sanctioned, formatted, note_blocks)
        if space_clause:
            formatted = restore_clause_spaces(sanctioned, formatted, whole_page=not merge.punct_locked)
    # The book's recurring n-grams are no text the answer may lack as
    # many times as they hold a character: furniture_unexplained tests a
    # deleted stretch against them instead.
    removable = None if furniture_reviewed else removable_text(
        page.blocks, merge.punctuated if page.spans is not None else [], merge.format_input,
        [*page.profile_furniture, *merge.book_heads],
        [kept["figure"] for kept in page.figures_kept if "given" in kept])
    ok, reason = ((False, silent) if silent else (False, misplaced) if misplaced
                  else verify_characters(sanctioned, formatted, removable))
    def formatter_losses(candidate: str) -> list[str]:
        # The inventory's features, with --content-fidelity on only.
        if not page.reviewing:
            return []
        reviewed_maps = (page.content_recovery or {}).get("table_mappings", [])
        mapped = _content_fidelity.rebind_unchanged_tables(expanded_input, candidate, reviewed_maps)
        mapped_regions = {m["region_id"] for m in reviewed_maps}
        # Newly reconstructed grids have no earlier confirmed cell mapping.
        # Their ownership must be established in mandatory final review;
        # do not reject a new valid table for lacking a pre-format mapping.
        protected = {**page.inventory, "features": [f for f in page.inventory["features"]
            if f.get("cell", {}).get("kind") != "grid" or f["region_id"] in mapped_regions]}
        missing = _content_fidelity.feature_losses(candidate, protected, mapped)
        if any(r["kind"] == "table" for r in page.inventory["regions"]) and not table_spans(candidate):
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
    if merge.punct_locked and page.spans is not None:
        refused_blocks = [index for index, block in enumerate(page.blocks)
                          if block.get("label") == "table" and index not in page.table_sources
                          and index not in merge.absorbed_into and index not in page.learned_dropped
                          and index not in merge.to_punctuate]
        if refused_blocks and page.draft2 is not None:
            refused_marks = table_block_marks(
                page.spans, refused_blocks, merge.punctuated,
                (engine_marks_merged(page.draft, merge.segments, merge.pieces, page.blocks),
                 engine_b_marks_merged(page.draft2, merge.segments, merge.pieces)))
    # The marks of REFUSED_MARKS the accepted answer's tables carry, sealed.
    table_marks_allowed: dict[str, Counter] = {}

    def locked_input(candidate: str) -> str:
        held, table_marks_allowed[candidate] = table_marks_lock(lock_input, candidate, refused_marks)
        return held

    if ok and merge.punct_locked and not punctuation_kept(locked_input(formatted), formatted):
        ok, reason = False, "changed punctuation: " + punctuation_delta(locked_input(formatted), formatted)
    if ok and punctuation_density(formatted) > density_limit(page.arguments):
        ok = False
        reason = (f"punctuation density {punctuation_density(formatted):.1f} per 100 "
                  "characters - a mark after almost every character")
    furniture = getattr(page.arguments, 'furniture', frozenset())
    margin_texts = margin_furniture(page.blocks)
    if not ok and "introduced" in reason:
        missing = Counter(CJK(page.resolved)) - Counter(CJK(formatted))
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
    page.prose = ("".join(span if block.get("label") != "table" else "".join(merge.edge_written.get(index, ("", "")))
                     for index, (block, span) in enumerate(zip(page.blocks, page.spans)) if index not in page.learned_dropped)
             if page.spans is not None else page.resolved)
    page.known_heads = sorted(set(page.profile_furniture))
    dropped = (deleted_runs(page.prose, formatted, furniture=furniture, margin=margin_texts,
                            known=page.known_heads, whole=page.resolved, prose_only=page.spans is not None) if ok else [])
    if dropped:
        ok = False
        reason = ("deleted " + " / ".join(f"{len(d)} characters ({d[:18]}…)"
                                          for d in dropped[:3]))

    # The text of the table blocks the structure pass was given, on the
    # block path: each recovered or rebuilt table, and the text of each
    # table block whose grid was refused (split_table_losses).
    given_tables = ("\n".join([*page.tables, *(text for index, (block, text) in enumerate(zip(page.blocks, merge.punctuated))
                                         if block.get("label") == "table" and index not in page.table_sources
                                         and index not in merge.absorbed_into)])
                    if page.spans is not None and merge.punct_locked else "")
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
                                     furniture, [*page.profile_furniture, *merge.book_heads])
        prose_lost, table_lost[candidate] = split_table_losses(lost, sanctioned, candidate, given_tables)
        return prose_lost

    gone_beside = _compute_beside_lost(page, merge, formatted) if ok else ""
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
        page.format_retry_reason = reason
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
            retry, page.last_message = ask_lower(
                page.client, NO_ANSWER_RETRY_EFFORT if cut_off else "", merge.format_system,
                (f"字同標點都已定（唔准改）：\n---\n{merge.format_input}\n---\n{table_hint}{layout_hint}\n"
                 if merge.punct_locked else
                 f"已定嘅字（唔准改）：\n---\n{merge.format_input}\n---\n{table_hint}\n{merge.whole_page_hint}")
                + ("你上一次諗得太耐，喺 token 上限之前冇寫出答案。唔使逐個字再諗，直接對住頁圖輸出。\n"
                   if cut_off else
                   "你上一次交咗個空白答案，一個字都冇寫。直接對住頁圖輸出。\n")
                + (f"睇頁圖，加版面結構（heading／段落／list／表格），{keep_or_delete}。標點原封不動。"
                   if merge.punct_locked else f"睇頁圖，加返句讀同版面結構，{keep_or_delete}。"),
                page.render_bytes, page.arguments.max_tokens, kind="format-retry")
        else:
            retry, page.last_message = page.client.ask_answering(
                merge.format_system,
                f"{'字同標點都已定' if merge.punct_locked else '已定嘅字'}（唔准改）：\n---\n{merge.format_input}\n---\n{table_hint}\n{merge.whole_page_hint}"
                f"你上一次嘅輸出被拒絕，原因：{reason}\n"
                + ("如果係改咗標點：每一個符號都要原封不動，唔准加、刪、搬。\n" if merge.punct_locked else "")
                + ("如果係表格記號唔見咗或者重複咗：每一行記號都要原封不動照抄一次，唔好自己寫嗰啲表。\n"
                   if merge.placeholders else "")
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
                page.render_bytes, page.arguments.max_tokens, kind="format-retry")
        page.add(page.last_message)
        retry = strip_fence(retry)
        silent_retry = no_answer(retry, page.last_message)
        retry_notes: list[str] = []
        misplaced = None
        if merge.placeholders:
            retry, misplaced, retry_notes = place_recovered_tables(retry, page.tables)
        retry_markers: list[dict[str, Any]] = []
        if not silent_retry and not misplaced:
            retry, retry_markers = restore_note_markers(sanctioned, retry, note_blocks)
            if space_clause:
                retry = restore_clause_spaces(sanctioned, retry, whole_page=not merge.punct_locked)
        ok2, reason2 = ((False, "retry: " + silent_retry) if silent_retry
                        else (False, "retry: " + misplaced) if misplaced
                        else verify_characters(sanctioned, retry, removable))
        retry_feature_losses = formatter_losses(retry) if ok2 else []
        if retry_feature_losses:
            ok2, reason2 = False, "retry lost or displaced source features: " + ", ".join(retry_feature_losses)
        if ok2 and merge.punct_locked and not punctuation_kept(locked_input(retry), retry):
            ok2, reason2 = False, "retry still changed punctuation"
        if ok2 and punctuation_density(retry) > density_limit(page.arguments):
            ok2, reason2 = False, "retry still over-punctuated"
        still = (deleted_runs(page.prose, retry, furniture=furniture, margin=margin_texts,
                              known=page.known_heads, whole=page.resolved, prose_only=page.spans is not None) if ok2 else [])
        if still:
            # Keep the better of the two rather than blocking the page, and
            # record what went missing so a later pass can find it.
            dropped = still
            ok2, reason2 = False, "retry still deleted " + " / ".join(still[:2])
        gone_beside = _compute_beside_lost(page, merge, retry) if ok2 else ""
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
            page.retry_refusal = reason2 if not ok2 else "the retry answered nothing"
            page.rejected_retry = retry
    page.rejected_output = None if ok else formatted
    # What the accepted answer's tables lack of the table blocks it was
    # given (unexplained): a doubt, below, not a refusal.
    page.structure_table_lost = table_lost.get(formatted, "") if ok else ""
    # The marks both engines read in a refused grid's table block that the
    # accepted answer's tables carry beyond what it was given (table_marks_lock).
    page.structure_table_marks = ("".join(table_marks_allowed.get(formatted, Counter()).elements())
                             if ok and merge.punct_locked else "")
    if ok and page.tables and page.source_table_mapping_current:
        # droppedRuns reports what the formatter dropped, so it is taken
        # on the text the deletion guard passed: enforcement only puts a
        # written table's cells in the recovered order and direction, which
        # breaks the merged text's runs without dropping anything.
        page.guarded = formatted
        formatted, page.table_notes, page.tables_missing, page.tables_disputed = enforce_recovered_tables(
            formatted, page.tables)
        page.table_notes = placement_notes + page.table_notes
    if ok and (formatted.strip() or head_only):
        if not merge.punct_locked:
            # The whole-page formatter wrote the marks (no block pass).
            formatted, found = mark_changes(formatted, 0, None, [], page.arguments)
            page.decision_changes.extend(found)
        # Switch --keep-arabic-numerals: a figure the text given held at its
        # gap that the structure pass left out is written back there (the
        # character checks count characters, and a figure is none).
        given = [kept for kept in page.figures_kept if "given" in kept]
        if given:
            formatted, restored = restore_arabic_figures(
                merge.format_input, formatted, [(kept["given"], kept["figure"], kept["after"]) for kept in given])
            for kept, how in zip(given, restored):
                kept["structure"] = how
        # Each note marker the structure pass left out and the page has
        # again (restore_note_markers), sealed (decision 8: the printed
        # note labels are kept).
        page.decision_changes.extend({
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
            formatted, page.tables, page.draft2, page_print_direction(page.blocks, image_size(page.render_bytes)),
            page.table_orientation_mode)
        for number, (as_written, now, how) in enumerate(written_turns, 1):
            page.table_orientation.append(f"structure pass table {number}: {how}")
            page.table_orientation_evidence.append(table_orientation_record(
                as_written, now, how, page.render_bytes, page.table_orientation_mode))
            if now != as_written:
                at = len(CJK(formatted[:formatted.find(as_written)]))
                chars = CJK(formatted)
                page.decision_changes.append({
                    "decision": "D23", "rule": "structure-table-oriented", "block": None,
                    "contextBefore": chars[max(0, at - 8):at],
                    "contextAfter": chars[at + len(CJK(as_written)):at + len(CJK(as_written)) + 8],
                    "engineA": "", "engineB": "", "after": now, "mergedOffset": None, "pageOffset": at,
                    "evidence": {"orientation": how[:300], "asWritten": as_written}})
                page.doubts.append({"kind": "table-orientation-turned", "detail": (
                    f"STRUCTURE TABLE TURNED: structure pass table {number} ({how.split(' ')[0]}) was turned on "
                    "engine B's reading (its order and its lines), which reads the same for a table written "
                    "right that engine B read band by band, several cells to a line - check the table "
                    f"against the image: {how}")})
        if oriented != formatted:
            page.guarded = formatted if page.guarded is None else page.guarded
            formatted = oriented
        page.writer_text = formatted
        if page.tables_missing:
            # A table the engine structured and the checks vouched for is not
            # in the page as a table: the formatter wrote it as prose or left
            # it out.  deleted_runs cannot see this - table blocks are kept out
            # of its comparison - so the page is flagged here.
            page.doubts.append({"kind": "table-missing", "detail": (
                "RECOVERED TABLE NOT IN OUTPUT: block(s) "
                + ", ".join(str(layout_index(page.blocks, page.table_sources[i])) for i in page.tables_missing))})
        if page.tables_disputed:
            # The formatter's table and the recovered one disagree about the
            # layout (merged cells), or the formatter's holds text outside
            # its cells; the formatter's stands, and a person decides.
            page.doubts.append({"kind": "table-disputed", "detail": (
                "RECOVERED TABLE LAYOUT DISPUTED: block(s) "
                + ", ".join(str(layout_index(page.blocks, page.table_sources[i])) for i in page.tables_disputed))})
    elif page.content_recovery is not None:
        # Switch --content-fidelity: keep the latest image-reviewed
        # checkpoint after either formatter fails.  Reconstructing from
        # punctuated blocks or the older CJK merge would lose confirmed
        # additions and could reapply stale source-table mappings.  Final
        # coverage still checks this fallback.
        page.writer_text = expanded_input
        if (page.reviewing and any(r["kind"] == "table" for r in page.inventory["regions"])
                and not table_spans(page.writer_text)):
            detail = {"code": "coverage-structure-mismatch", "path": "$.formatter",
                      "message": "both formatter candidates rejected for table ownership; fallback has no table"}
            page.fail_fidelity(page.writer_text, detail)
            page.fidelity_doubt("formatter", "both formatter answers were rejected and the image-reviewed text "
                                        "kept in their place has no table where the inventory found one")
        for kept in page.figures_kept:
            if "given" in kept:
                kept["structure"] = "source-recovery fallback"
        page.doubts.append({"kind": "structure-rejected" if merge.punct_locked else "formatter-rejected",
                       "detail": f"FORMATTER REJECTED: {reason}; image-reviewed input preserved"
                       + (f"; retry refused: {page.retry_refusal}" if page.retry_refusal else "")})
    elif merge.punct_locked:
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
        agreed = [merge.absorbed_held.get(index, text)
                  for index, text in enumerate(agreed_block_text(page.blocks, merge.agreed_segments))]
        left_out = []
        for position, (index, table) in enumerate(zip(page.table_sources, page.tables)):
            # A rebuilt table's region is its block and the blocks it took in (D22).
            region = [index] + [j for j, owner in merge.absorbed_into.items() if owner == index]
            # With the text beside it, as the structure pass was given it.
            beside = merge.edge_written.get(index, ("", ""))
            if Counter("".join(agreed[j] for j in region)) - Counter(CJK(table + "".join(beside))):
                page.tables_missing.append(position)
                continue
            recovered_from[index] = "\n\n".join(part for part in (beside[0], table, beside[1]) if part)
            extra = (Counter(CJK("".join(merge.punctuated[j] for j in region)))
                     - Counter(CJK(recovered_from[index])))
            if extra:
                left_out.append(f"block {layout_index(page.blocks, index)}: {''.join(extra.elements())[:20]}")
        # A block listing entries with leaders, one entry a paragraph
        # (listed: engine A's lines).
        page.writer_text = "\n\n".join(
            recovered_from.get(index, text.replace("\n", "\n\n") if index in merge.listed else text)
            for index, (block, text) in enumerate(zip(page.blocks, merge.punctuated))
            if merge.absorbed_into.get(index) not in recovered_from and (index in recovered_from or (CJK(text) and (
                block["label"] == "table" or block["label"] not in FURNITURE_LABELS))))
        # Switch --keep-arabic-numerals: a figure written into a block
        # this leaves out (one with no character: a contents entry's
        # number on a line of its own) is not in the page.
        for kept in page.figures_kept:
            if kept.get("placed") in FIGURE_IN_BLOCK and not CJK(merge.punctuated[kept["block"]]):
                kept["structure"] = "block left out"
        page.doubts.append({"kind": "structure-rejected",
                       "detail": f"STRUCTURE PASS REJECTED: {reason}"
                                 + (f"; retry refused: {page.retry_refusal}" if page.retry_refusal else "")})
        if page.tables_missing:
            page.doubts.append({"kind": "table-missing", "detail": (
                "block text kept instead of the recovered table (the table lacks "
                "characters both engines read there): block(s) "
                + ", ".join(str(layout_index(page.blocks, page.table_sources[i])) for i in page.tables_missing))})
        if left_out:
            page.doubts.append({"kind": "table-text-left-out", "detail": (
                "characters only engine B read in a table block, not in its "
                "table, left out: " + "; ".join(left_out))})
    else:
        # Refuse invented characters; keep the merged text unformatted -
        # with no block spans in this mode, there is nowhere to put the
        # recovered tables back, so they are all recorded as missing.
        page.writer_text = page.resolved
        # Switch --keep-arabic-numerals: the figures the formatter was
        # given are not in the merged text (characters only).
        for kept in page.figures_kept:
            if "given" in kept:
                kept["structure"] = "formatter rejected"
        page.doubts.append({"kind": "formatter-rejected", "detail": f"FORMATTER REJECTED: {reason}"
                       + (f"; retry refused: {page.retry_refusal}" if page.retry_refusal else "")})
        page.tables_missing = list(range(len(page.tables)))
        # The page is the merged text with no marks at all, not a page the
        # formatter punctuated whole: that doubt no longer describes it.
        page.doubts[:] = [d for d in page.doubts if d["kind"] != "punctuation-whole-page"]
    # Every run of dots the page keeps is 「……」 (D4).  The block passes'
    # and the whole-page formatter's are written so and sealed where they
    # are punctuated; one a structure pass wrote with another number of
    # dots (its lock counts a run as one mark) or a recovered table holds
    # is written so here, and sealed as rewritten.
    page.writer_text, found = leader_rewrites(page.writer_text)
    page.decision_changes.extend(found)
    if page.spans is not None:
        # A block a rebuilt table took in (D22) counts what its table had
        # to hold of it: both engines read it, only not in one order.
        # And the blocks beside a table rebuilt without them (beside_table).
        page.table_characters = table_characters_lost(
            page.blocks, [merge.absorbed_held.get(index, text)
                     for index, text in enumerate(agreed_block_text(page.blocks, merge.agreed_segments))],
            page.writer_text, merge.book_heads, merge.book_furniture, merge.beside_table)
        # And what the accepted structure pass's tables lack of the table
        # blocks it was given, beyond those (split_table_losses): the
        # page's prose can hide a table's loss from the count above (a 兩
        # of a merged cell left out, the prose holding 兩 too).
        more = Counter(page.structure_table_lost) - Counter(page.table_characters)
        unnamed = ""
        for char in page.structure_table_lost:
            if more[char] > 0:
                more[char] -= 1
                unnamed += char
        read_by = ("engine A read (the page's only reading)" if merge.order_text == "A"
                   else "both engines read")
        if page.table_characters:
            page.doubts.append({"kind": "table-characters-lost", "detail": (
                f"TABLE CHARACTERS NOT IN OUTPUT: {len(page.table_characters)} "
                f"character(s) {read_by} in the table blocks"
                + (" and the blocks beside a table rebuilt without them ("
                   + ", ".join(str(layout_index(page.blocks, j)) for j in sorted(merge.beside_table)) + ")"
                   if merge.beside_table else "")
                + f" ({page.table_characters[:30]})"
                + (f"; and {len(unnamed)} more character(s) of the table blocks the structure pass was "
                   f"given, which its tables lack ({unnamed[:30]})" if unnamed else ""))})
        elif unnamed:
            page.doubts.append({"kind": "table-characters-lost", "detail": (
                f"TABLE CHARACTERS NOT IN OUTPUT: {len(unnamed)} character(s) of the table blocks the "
                f"structure pass was given, which its tables lack ({unnamed[:30]}) - check the table "
                "against the image")})
        page.table_characters += unnamed
    if merge.table_edge:
        # The table's edge text (table_edges): each table whose block's span
        # held text beside its cells, or whose restatement the rule
        # changed, sealed in decisionChanges; the text written beside a
        # table is a doubt, whether the page still holds it or not.
        merged_cjk, page_cjk = CJK(page.resolved), CJK(page.writer_text)

        def read_there(lo: int, hi: int) -> dict[str, str]:
            """What each engine read of the merged text's [LO, HI): its own
            characters where its reading's place there is exact."""
            return {name: "".join(placed[at] for at in range(lo, hi) if at in placed)
                    for name, placed in zip(("engineA", "engineB"), merge.edge_placed)}

        # The lines of engine B's that are cells of its own tables.
        b_cells = engine_b_table_lines(page.draft2) if page.draft2 is not None else set()

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
        for index, found in sorted(merge.table_edge.items()):
            layout = layout_index(page.blocks, index)
            written = merge.edge_written.get(index, ("", ""))
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
                in_cells = ("".join(merged_cjk[at] for at in range(lo, hi) if merge.b_lines[at] in b_cells)
                            if merge.b_lines is not None and b_cells else "")
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
            page.decision_changes.append({
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
            page.doubts.append({"kind": "table-edge-text", "detail": (
                "TEXT AT A TABLE'S EDGE: the table block's merged text holds it before the table's first "
                "cell or after its last, and no cell of the table does: " + "; ".join(beside_notes[:6])
                + " - check it against the image")})
    # The steps below each passed or failed silently before: a refused or
    # miscounted punctuation pass left its stretch bare or short, and a
    # merge that placed one reading twice kept both, and the page was
    # sealed clean either way (punctuation_doubts, extra_copies).
    page.doubts.extend(punctuation_doubts(page.punctuation_passes))
    page.doubts.extend(extra_copy_doubts(page.writer_text, page.draft, page.draft2))

def _finalize_and_seal(page: _PageState) -> dict[str, Any]:
    """Run post-format checks, coverage review, note-number prompt, and seal page JSON/Markdown."""
    # Switch --loop-detector: each call with a rung given up in a loop.
    page.decision_changes.extend(loop_cut_changes(page.loop_cuts))
    # Switch --keep-arabic-numerals: each figure the merge kept, where it wrote
    # the figure or left out engine B's reading of it; and a doubt where it
    # could write neither the figure nor what was there before.
    unwritten = []
    for kept in page.figures_kept:
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
            page.decision_changes.append({
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
    unkept = bracketed_unwritten(page.page_bracketed, page.figures_kept, page.writer_text or "", page.figure_why)
    if unwritten or unkept:
        page.doubts.append({"kind": "arabic-figure-unwritten", "detail": (
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
    page.writer_text = strip_paddlex_img_tags(page.writer_text)

    # Switch --content-fidelity: final full-image coverage is after table
    # enforcement and every mechanical rewrite.  A rejected formatter fallback
    # is still a candidate, never evidence of full-page coverage.  What it
    # cannot settle is a doubt on the page (image_review), sealed as such.
    if page.reviewing:
        page.writer_text, page.coverage_review = page.image_review(page.writer_text, "coverage-review")
    final_tables = [page.writer_text[a:b] for a, b in table_spans(page.writer_text)]
    table_mapping_current, table_correspondence = _content_fidelity.refresh_table_mapping(page.tables, final_tables)
    table_mapping_current = page.source_table_mapping_current and table_mapping_current
    if table_mapping_current:
        page.tables = final_tables
    for evidence in page.table_orientation_evidence:
        evidence["status"] = ("active" if evidence.get("outputSha256") in
                              {sha256_bytes(t.encode("utf-8")) for t in final_tables} else "historical")
        if page.coverage_review is not None:
            evidence["finalCoverageSha256"] = page.coverage_review["result_sha256"]
    model_sites = variant_model_sites(page.disagreements, page.arguments)
    # Issue #8, item 4: note numbers the rules leave unsettled, read off the
    # page by the answerer for the repair's N9 (note_question_reason).
    note_numbers = None
    note_question = note_question_reason(page.draft, page.draft2, page.writer_text, page.blocks)
    if note_question and page.client is not None and page.render_bytes:
        note_numbers, used = ask_note_numbers(page.client, page.render_bytes, note_question)
        page.add(used)
    record: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "scanPage": page.job.scan_page,
        "status": "complete",
        "textAuthority": TEXT_AUTHORITY,
        "generatedAt": utc_now(),
        "model": page.client.model,
        "reasoningEffort": page.client.effort,
        **effort_policy_record(page.client),
        "punctuationMode": getattr(page.arguments, "punctuation_mode", "auto"),
        "punctuationModeEffective": punctuation_mode(page.arguments, page.client),
        "punctuationFusion": punctuation_fusion_summary(page.punctuation_passes),
        "renderFile": str(page.job.render),
        "auditorClean": not page.doubts,
        "mode": page.mode,
        # Switch --content-fidelity: its evidence, and whether it was settled
        # ("complete") or left a doubt ("doubt").  Absent with it off.
        **({"contentFidelityContract": _content_fidelity.CONTRACT_VERSION,
            "contentFidelityStatus": page.fidelity_status,
            "pageInventory": page.inventory or None,
            "contentRecovery": page.content_recovery,
            "coverageReview": page.coverage_review} if page.fidelity_on else {}),
        "engineDisagreements": page.disagreements,
        "auditorReport": "; ".join(d["detail"] for d in page.doubts),
        "doubts": page.doubts,
        "adjudications": page.adjudications,
        "punctuationPasses": page.punctuation_passes,
        "engineBrackets": page.engine_brackets,
        "profileFurnitureDropped": page.profile_furniture,
        # Genuine substitutions only - both engines produced text - the same
        # population the profile measured, so the two can be compared.
        "engineTally": dict(Counter(a.get("winner", "undecided") for a in page.adjudications
                                    if a.get("trigger") == "engine-disagreement"
                                    and CJK(a.get("draft_reading", "")) and CJK(a.get("writer_reading", "")))),
        "variantUnruled": page.variant_unruled,
        # The sites the model's vote decided (variant_model_sites), on books it ruled.
        **({"variantRuledByModel": model_sites} if model_sites is not None else {}),
        # An engine-A block whose reading was a repetition loop engine B does not
        # share, set aside (quarantine_looped_block): kept here, as a page's
        # discarded reading is, so the judgement can be undone.
        "engineALoopsDropped": page.loops_dropped,
        # Each text change a decision-dependent rule made (see decision_changes).
        "decisionChanges": page.decision_changes,
        # The note-numbers answer (ask_note_numbers), where it was asked.
        **({"noteNumbers": note_numbers} if note_numbers is not None else {}),
        # Switch --keep-arabic-numerals: every figure engine A read that may
        # number something (arabic_figures), kept or not and why.  Only when on.
        **({"arabicFigures": page.arabic_figures_seen} if getattr(page.arguments, "keep_arabic_numerals", False) else {}),
        # Each lopsided replacement split (split_lopsided), and what was written for it.
        "lopsidedSplits": [{"engineA": split["segment"]["a"], "engineB": split["segment"]["b"],
                            "contextBefore": split["segment"]["before"], "contextAfter": split["segment"]["after"],
                            "how": split["how"], "written": split.get("written")} for split in page.lopsided],
        # Places where the merge dropped more than ANSWER_DELETION_LIMIT characters
        # one engine read that the merged text holds nowhere, and the rules that
        # settled them (readings_dropped): each a reading-dropped doubt.
        "readingsDropped": page.readings_lost,
        "formatterRejectedOutput": page.rejected_output,
        "formatterRetryReason": page.format_retry_reason,
        # The text the structure pass and its retry were given
        # (structure_input_record); None where no structure pass ran.
        "structureInput": page.structure_input,
        # The retry's own refusal and answer, when it was refused as well.
        "formatterRetryRefusal": page.retry_refusal,
        "formatterRejectedRetry": page.rejected_retry,
        "needsResearch": front_matter_research(page.blocks, page.resolved) if page.mode.startswith("merge") else [],
        "usage": page.metrics,
        "punctuationDensity": round(punctuation_density(page.writer_text), 2),
        "droppedRuns": (deleted_runs(page.prose if page.prose is not None else page.resolved,
                                     page.guarded if page.guarded is not None else page.writer_text,
                                     furniture=getattr(page.arguments, "furniture", frozenset()),
                                     margin=margin_furniture(page.blocks),
                                     known=sorted(set(page.profile_furniture)), whole=page.resolved,
                                     prose_only=page.prose is not None and page.spans is not None)
                        if not page.transcribing else []),
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
            "engineA": page.engine_a_characters,
            "engineB": page.engine_b_characters,
            "merged": len(CJK(page.resolved)) if not page.transcribing else None,
        },
        "tablesRecovered": len(page.tables),
        # Each recovered table with the block it came from (its layout index,
        # as the stitch reads it against engine A's layout file), as it is to
        # stand on the page (a whole-page pass was offered it with its label
        # cells as read, cells_as_read); tablesEnforced, tablesMissing and
        # tablesDisputed say what became of it on the page.
        "tables": [{"block": layout_index(page.blocks, block),
                    "format": "html" if "<table" in table else "markdown",
                    "text": table} for block, table in zip(page.table_sources, page.tables)],
        "tablesEnforced": page.table_notes,
        "tablesMissing": [layout_index(page.blocks, page.table_sources[i]) for i in page.tables_missing],
        "tablesDisputed": [layout_index(page.blocks, page.table_sources[i]) for i in page.tables_disputed],
        # Characters both engines read in the table blocks that the page lacks -
        # also for a refused table, which the formatter rebuilds
        # (table_characters_lost).
        "tableCharactersLost": page.table_characters,
        # The marks the structure pass wrote in the tables it built from the
        # image that its punctuation lock let through because both engines
        # read them in a table block whose grid was refused (table_marks_lock).
        "tableMarksAllowed": page.structure_table_marks,
        "tableOrientation": page.table_orientation,
        "tableOrientationEvidence": page.table_orientation_evidence,
        "tableMappingCurrent": table_mapping_current,
        "tableCorrespondence": table_correspondence,
        "recoveryTableCorrespondence": page.recovery_table_correspondence,
        "finalTables": [{"text": table, "sha256": sha256_bytes(table.encode("utf-8")),
                         "format": "html" if "<table" in table else "markdown"} for table in final_tables],
        # Figures whose group marks were taken from engine B (restate_table).
        "tableFigureGrouping": page.table_grouping,
        # The label cells asked which way they read, with both answers
        # (fix_cell_direction).
        "cellDirection": page.cell_direction,
        # Blocks engine B reads in another order, nominated as tables the
        # layout model missed (nominate_tables), with how many of their
        # disagreements settle_by_union settled and how many it left to the
        # usual way; and every table rebuilt from the image with its
        # characters locked (rebuild_table): where from, how many characters
        # were allowed and required, the outcome.
        "tableNominations": [dict(nomination, block=layout_index(page.blocks, nomination["block"]))
                             for nomination in page.table_nominations],
        "tablesRebuilt": page.table_rebuilds,
        "tablesRejected": page.tables_rejected,
        "degraded": bool(page.last_message.get("degraded")),
        "seconds": round(time.monotonic() - page.started, 1),
        # Where the page's time went, per kind of model call.  Seconds are the
        # caller's wall time per call (they overlap when calls run in parallel),
        # waitSeconds the time spent queued for a free lane.
        "calls": facade_attr("summarize_calls", summarize_calls)(page.ledger),
    }
    record["provenance"] = {
        **sealed_inputs(page.job),
        **decision_provenance(page.arguments),
        "markdownSha256": sha256_bytes(page.writer_text.encode("utf-8")),
    }
    record["provenance"]["jsonPayloadSha256"] = sha256_bytes(canonical_json_bytes(record))
    seal_page(page.job, page.writer_text.encode("utf-8"), record, events=getattr(page.arguments, "packet_events", None))
    return record

def proofread_page(
    job: PageJob, client: Client, arguments: argparse.Namespace
) -> dict[str, Any]:
    with packet_page_context(job.scan_page, job.render, facade_attr("sha256_file", sha256_file)(job.render)):
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
    draft2 = (strip_paddlex_img_tags(strip_grounding(job.draft2_text.read_text(encoding="utf-8"))).strip()
              if job.draft2_text is not None and job.draft2_text.is_file() else None)
    fidelity_on = content_fidelity_on(arguments)
    page = _PageState(
        job=job,
        client=client,
        arguments=arguments,
        started=started,
        ledger=ledger,
        loop_cuts=loop_cuts,
        render_bytes=render_bytes,
        draft=draft,
        draft2=draft2,
        page_concurrency=max(1, getattr(arguments, "page_concurrency", DEFAULT_PAGE_CONCURRENCY) or 1),
        table_orientation_mode=getattr(arguments, "table_orientation", "auto") or "auto",
        fidelity_on=fidelity_on,
        reviewing=fidelity_on,
    )
    _prepare_engine_drafts(page)
    _run_fidelity_inventory(page)
    early_record = _seal_blank_or_uncorroborated(page)
    if early_record is not None:
        return early_record

    page.transcribing = page.draft2 is None or bool(page.non_cjk)
    if page.transcribing:
        _transcribe_page(page)
    else:
        merge = _prepare_merge_segments(page)
        _adjudicate_and_resolve(page, merge)
        _merge_tables(page, merge)
        _merge_punctuation(page, merge)
        _merge_format_and_enforce(page, merge)

    return _finalize_and_seal(page)
