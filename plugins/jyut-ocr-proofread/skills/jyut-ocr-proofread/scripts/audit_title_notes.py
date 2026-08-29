#!/usr/bin/env python3
"""Audit starred title notes and note-shaped cross-page boundaries.

The input is the annotated Markdown release candidate, before canonical page
markers are removed.  A main item starts at ``--heading-level`` (``##`` by
default).  Terminal ``*`` and ``**`` markers on that item and its subordinate
headings must be balanced by title-note definitions beginning with ``* `` or
``** `` inside the same item.

The cross-page check is deliberately fail closed.  A title or numbered note
immediately before a page marker and running prose immediately after it is
actionable.  It can be accepted only by an exact, independently reviewed
transition record sealed to the current target and boundary-manifest bytes;
the prose's language or lexical shape never acts as evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Sequence

from _ocr_markdown import (
    BOUNDARY_LABELS,
    boundary_contract_problems,
    find_page_markers,
    line_body,
    mask_nonrendered_markdown,
    parse_boundary_manifest,
    parse_atx_heading as parse_heading,
    paths_refer_to_same_file,
    split_lines,
    terminal_title_note_stars as terminal_stars,
    title_note_definition_marker as title_note_marker,
)


CIRCLED_NOTE_RE = re.compile(
    r"^(?P<marker>[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf])[ \t]*\S"
)
LIST_ITEM_RE = re.compile(r"^(?:[-+] |\d+[.)][ \t]+)")
NON_BODY_PREFIXES = (">", "|", "```", "~~~", "<!--")
STAR_KEYS = ("*", "**")
TRANSITION_REVIEW_ARTIFACT_TYPE = "ocr-note-transition-reviews"
TRANSITION_REVIEW_DISPOSITION = "accepted-reviewed-note-transition"
TRANSITION_NOTE_KINDS = frozenset({"title-note", "numbered-note"})
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TOOL_PATH = Path(__file__).resolve()
SHARED_HELPER_PATH = TOOL_PATH.with_name("_ocr_markdown.py")
RELEASE_VALIDATOR_PATH = TOOL_PATH.with_name("validate_release_evidence.py")


class _JsonObject(list[tuple[str, object]]):
    """Retain JSON pairs so duplicate keys cannot hide review data."""


@dataclass(frozen=True)
class TransitionReview:
    scan_page: int
    note_kind: str
    previous_text: str
    following_text: str
    boundary_label: str
    disposition: str
    writer: str
    independent_reviewer: str
    visual_evidence: str

    @property
    def hazard_key(self) -> tuple[int, str, str, str, str]:
        return (
            self.scan_page,
            self.note_kind,
            self.previous_text,
            self.following_text,
            self.boundary_label,
        )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def strict_mapping(
    value: object, *, context: str, expected_keys: set[str]
) -> dict[str, object]:
    if not isinstance(value, _JsonObject):
        raise ValueError(f"{context} must be a JSON object")
    result: dict[str, object] = {}
    for key, item in value:
        if key in result:
            raise ValueError(f"duplicate {context} key: {key!r}")
        result[key] = item
    if set(result) != expected_keys:
        missing = sorted(expected_keys - set(result))
        unexpected = sorted(set(result) - expected_keys)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unsupported " + ", ".join(unexpected))
        raise ValueError(f"{context} fields invalid: {'; '.join(details)}")
    return result


def nonempty_string(value: object, *, context: str, exact: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    if exact and value != value.strip():
        raise ValueError(f"{context} must not have leading or trailing whitespace")
    return value


def load_transition_reviews(
    payload: bytes,
) -> tuple[str, str, tuple[TransitionReview, ...]]:
    """Load a duplicate-safe, exact transition-review manifest."""

    try:
        raw_root = json.loads(payload.decode("utf-8"), object_pairs_hook=_JsonObject)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid UTF-8 JSON: {error}") from error
    root = strict_mapping(
        raw_root,
        context="transition reviews root",
        expected_keys={
            "schema_version",
            "artifact_type",
            "target_sha256",
            "boundary_manifest_sha256",
            "reviews",
        },
    )
    schema_version = root["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("transition reviews schema_version must be integer 1")
    if root["artifact_type"] != TRANSITION_REVIEW_ARTIFACT_TYPE:
        raise ValueError(
            "transition reviews artifact_type must be "
            f"{TRANSITION_REVIEW_ARTIFACT_TYPE!r}"
        )

    target_sha256 = root["target_sha256"]
    if not isinstance(target_sha256, str) or SHA256_RE.fullmatch(target_sha256) is None:
        raise ValueError("transition reviews target_sha256 must be lowercase SHA-256")
    boundary_sha256 = root["boundary_manifest_sha256"]
    if not isinstance(boundary_sha256, str) or SHA256_RE.fullmatch(boundary_sha256) is None:
        raise ValueError(
            "transition reviews boundary_manifest_sha256 must be lowercase SHA-256"
        )

    raw_reviews = root["reviews"]
    if not isinstance(raw_reviews, list) or isinstance(raw_reviews, _JsonObject):
        raise ValueError("transition reviews reviews must be a JSON array")
    if not raw_reviews:
        raise ValueError("transition reviews reviews must not be empty")

    reviews: list[TransitionReview] = []
    seen: set[tuple[int, str, str, str, str]] = set()
    expected_review_keys = {
        "scan_page",
        "note_kind",
        "previous_text",
        "following_text",
        "boundary_label",
        "disposition",
        "writer",
        "independent_reviewer",
        "visual_evidence",
    }
    for index, raw_review in enumerate(raw_reviews, 1):
        record = strict_mapping(
            raw_review,
            context=f"transition review {index}",
            expected_keys=expected_review_keys,
        )
        scan_page = record["scan_page"]
        if (
            not isinstance(scan_page, int)
            or isinstance(scan_page, bool)
            or scan_page < 1
        ):
            raise ValueError(
                f"transition review {index} scan_page must be a positive integer"
            )
        note_kind = nonempty_string(
            record["note_kind"], context=f"transition review {index} note_kind"
        )
        if note_kind not in TRANSITION_NOTE_KINDS:
            allowed = ", ".join(sorted(TRANSITION_NOTE_KINDS))
            raise ValueError(
                f"transition review {index} note_kind must be one of: {allowed}"
            )
        previous_text = nonempty_string(
            record["previous_text"],
            context=f"transition review {index} previous_text",
            exact=True,
        )
        following_text = nonempty_string(
            record["following_text"],
            context=f"transition review {index} following_text",
            exact=True,
        )
        boundary_label = nonempty_string(
            record["boundary_label"],
            context=f"transition review {index} boundary_label",
        )
        if boundary_label not in BOUNDARY_LABELS:
            allowed = ", ".join(sorted(BOUNDARY_LABELS))
            raise ValueError(
                f"transition review {index} boundary_label must be one of: {allowed}"
            )
        disposition = nonempty_string(
            record["disposition"],
            context=f"transition review {index} disposition",
        )
        if disposition != TRANSITION_REVIEW_DISPOSITION:
            raise ValueError(
                f"transition review {index} disposition must be "
                f"{TRANSITION_REVIEW_DISPOSITION!r}"
            )
        writer = nonempty_string(
            record["writer"], context=f"transition review {index} writer"
        )
        independent_reviewer = nonempty_string(
            record["independent_reviewer"],
            context=f"transition review {index} independent_reviewer",
        )
        if (
            unicodedata.normalize("NFKC", writer).casefold()
            == unicodedata.normalize("NFKC", independent_reviewer).casefold()
        ):
            raise ValueError(
                f"transition review {index} independent_reviewer must differ "
                "from writer"
            )
        visual_evidence = nonempty_string(
            record["visual_evidence"],
            context=f"transition review {index} visual_evidence",
        )
        review = TransitionReview(
            scan_page=scan_page,
            note_kind=note_kind,
            previous_text=previous_text,
            following_text=following_text,
            boundary_label=boundary_label,
            disposition=disposition,
            writer=writer,
            independent_reviewer=independent_reviewer,
            visual_evidence=visual_evidence,
        )
        if review.hazard_key in seen:
            raise ValueError(
                f"duplicate transition review row for scan page {scan_page} "
                f"and note kind {note_kind}"
            )
        seen.add(review.hazard_key)
        reviews.append(review)
    return target_sha256, boundary_sha256, tuple(reviews)


def nearest_nonblank(lines: Sequence[str], start: int, step: int) -> int | None:
    index = start
    while 0 <= index < len(lines):
        if line_body(lines[index]).strip():
            return index
        index += step
    return None


def numbered_note_marker(body: str) -> str | None:
    match = CIRCLED_NOTE_RE.match(body)
    return match.group("marker") if match else None


def is_running_body(body: str) -> bool:
    """Return whether a nonblank line is prose rather than a new structure."""

    stripped = body.strip()
    if not stripped:
        return False
    if parse_heading(stripped) is not None:
        return False
    if title_note_marker(stripped) is not None:
        return False
    if numbered_note_marker(stripped) is not None:
        return False
    if stripped.startswith(NON_BODY_PREFIXES):
        return False
    if LIST_ITEM_RE.match(stripped):
        return False
    return True


def new_item(
    *, scan_page: int | None, line_number: int, heading: str
) -> dict[str, object]:
    return {
        "scan_page": scan_page,
        "line": line_number,
        "heading": heading,
        "heading_markers": {key: 0 for key in STAR_KEYS},
        "title_note_markers": {key: 0 for key in STAR_KEYS},
        "heading_marker_occurrences": [],
        "title_notes": [],
    }


def audit_item_notes(
    lines: Sequence[str], *, heading_level: int
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Return items, mismatches, missing notes, and orphan notes."""

    items: list[dict[str, object]] = []
    orphan_outside_items: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    scan_page: int | None = None

    for index, raw_line in enumerate(lines):
        body = line_body(raw_line)
        marker_match = re.fullmatch(
            r"[ \t]*<!-- page_(?P<page>\d{3,}) -->[ \t]*", body
        )
        if marker_match:
            scan_page = int(marker_match.group("page"))
            continue

        heading = parse_heading(body)
        if heading is not None:
            level, text = heading
            if level == heading_level:
                if current is not None:
                    items.append(current)
                current = new_item(
                    scan_page=scan_page,
                    line_number=index + 1,
                    heading=body,
                )
            elif level < heading_level:
                if current is not None:
                    items.append(current)
                    current = None

            stars = terminal_stars(text)
            if stars is not None:
                occurrence = {
                    "scan_page": scan_page,
                    "line": index + 1,
                    "marker": stars,
                    "heading": body,
                }
                if current is None:
                    orphan_outside_items.append(
                        {**occurrence, "reason": "starred-heading-outside-main-item"}
                    )
                else:
                    heading_markers = current["heading_markers"]
                    assert isinstance(heading_markers, dict)
                    heading_markers[stars] += 1
                    occurrences = current["heading_marker_occurrences"]
                    assert isinstance(occurrences, list)
                    occurrences.append(occurrence)
            continue

        stars = title_note_marker(body)
        if stars is None:
            continue
        note = {
            "scan_page": scan_page,
            "line": index + 1,
            "marker": stars,
            "text": body,
        }
        if current is None:
            orphan_outside_items.append(
                {**note, "reason": "title-note-outside-main-item"}
            )
            continue
        note_markers = current["title_note_markers"]
        assert isinstance(note_markers, dict)
        note_markers[stars] += 1
        notes = current["title_notes"]
        assert isinstance(notes, list)
        notes.append(note)

    if current is not None:
        items.append(current)

    mismatches: list[dict[str, object]] = []
    missing_notes: list[dict[str, object]] = []
    orphan_notes = list(orphan_outside_items)
    for item_index, item in enumerate(items, 1):
        heading_counts = item["heading_markers"]
        note_counts = item["title_note_markers"]
        notes = item["title_notes"]
        assert isinstance(heading_counts, dict)
        assert isinstance(note_counts, dict)
        assert isinstance(notes, list)

        differences: dict[str, int] = {}
        for stars in STAR_KEYS:
            expected = heading_counts[stars]
            actual = note_counts[stars]
            differences[stars] = actual - expected
            if expected > actual:
                missing_notes.append(
                    {
                        "item_index": item_index,
                        "scan_page": item["scan_page"],
                        "line": item["line"],
                        "heading": item["heading"],
                        "marker": stars,
                        "missing_count": expected - actual,
                    }
                )
            elif actual > expected:
                matching_notes = [
                    note for note in notes if note.get("marker") == stars
                ]
                for note in matching_notes[expected:]:
                    orphan_notes.append(
                        {
                            **note,
                            "item_index": item_index,
                            "heading": item["heading"],
                            "reason": "surplus-title-note-in-item",
                        }
                    )

        item["status"] = "pass" if not any(differences.values()) else "mismatch"
        if item["status"] != "pass":
            mismatches.append(
                {
                    "item_index": item_index,
                    "scan_page": item["scan_page"],
                    "line": item["line"],
                    "heading": item["heading"],
                    "heading_markers": dict(heading_counts),
                    "title_note_markers": dict(note_counts),
                    "difference_note_minus_heading": differences,
                }
            )

    # A starred subordinate heading before the first configured main item is
    # evidence of a scope/configuration error, so expose it through the same
    # orphan gate as an unattached title-note line.
    return items, mismatches, missing_notes, orphan_notes


def audit_cross_page_notes(
    lines: Sequence[str],
    markers: Sequence[object],
    *,
    boundaries: dict[int, str] | None,
    transition_reviews: dict[tuple[int, str, str, str, str], TransitionReview],
) -> tuple[list[dict[str, object]], set[tuple[int, str, str, str, str]]]:
    hazards: list[dict[str, object]] = []
    used_review_keys: set[tuple[int, str, str, str, str]] = set()
    for marker in markers:
        marker_index = marker.line_index
        previous = nearest_nonblank(lines, marker_index - 1, -1)
        following = nearest_nonblank(lines, marker_index + 1, 1)
        if previous is None or following is None:
            continue

        left = line_body(lines[previous]).strip()
        right = line_body(lines[following]).strip()
        title_marker = title_note_marker(left)
        circled_marker = numbered_note_marker(left)
        if title_marker is None and circled_marker is None:
            continue
        if not is_running_body(right):
            continue

        boundary_label = boundaries.get(marker.page) if boundaries else None
        if title_marker is not None:
            note_kind = "title-note"
            note_marker = title_marker
            actionable_status = "actionable-title-note-cross-page-interruption"
        else:
            note_kind = "numbered-note"
            note_marker = circled_marker
            actionable_status = (
                "actionable-unverified-numbered-note-hard-break"
                if boundaries is None
                else "actionable-numbered-note-cross-page-interruption"
            )

        review_key = (
            (
                marker.page,
                note_kind,
                left,
                right,
                boundary_label,
            )
            if boundary_label is not None
            else None
        )
        review = (
            transition_reviews.get(review_key) if review_key is not None else None
        )
        if review is None:
            status = actionable_status
            review_record = None
        else:
            status = TRANSITION_REVIEW_DISPOSITION
            review_record = {
                "writer": review.writer,
                "independent_reviewer": review.independent_reviewer,
                "visual_evidence": review.visual_evidence,
            }
            assert review_key is not None
            used_review_keys.add(review_key)

        hazard = {
            "scan_page": marker.page,
            "marker_line": marker.line_number,
            "previous_line": previous + 1,
            "previous_text": left,
            "following_line": following + 1,
            "following_text": right,
            "note_kind": note_kind,
            "note_marker": note_marker,
            "boundary_label": boundary_label,
            "status": status,
        }
        if review_record is not None:
            hazard["transition_review"] = review_record
        hazards.append(hazard)
    return hazards, used_review_keys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit title-star/title-note balance and note-shaped cross-page "
            "hazards in annotated OCR Markdown."
        )
    )
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--heading-level",
        type=int,
        choices=range(1, 7),
        default=2,
        metavar="N",
        help="ATX heading level that starts a main item (default: 2)",
    )
    parser.add_argument(
        "--boundary-manifest",
        type=Path,
        help="strict {'boundaries': {...}} JSON covering every page marker",
    )
    parser.add_argument(
        "--transition-reviews",
        type=Path,
        help=(
            "strict reviewed-transition JSON sealed to the current target and "
            "boundary manifest"
        ),
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="write the complete deterministic JSON report to this path",
    )
    return parser


def write_report(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fail(message: str) -> int:
    print(f"ERROR {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = args.target.resolve()
    report_path = args.json_report.resolve() if args.json_report else None
    boundary_path = (
        args.boundary_manifest.resolve() if args.boundary_manifest else None
    )
    transition_reviews_path = (
        args.transition_reviews.resolve() if args.transition_reviews else None
    )

    if report_path is not None and paths_refer_to_same_file(report_path, target):
        return fail("--json-report must not alias the annotated target")
    if (
        report_path is not None
        and boundary_path is not None
        and paths_refer_to_same_file(report_path, boundary_path)
    ):
        return fail("--json-report must not alias --boundary-manifest")
    if (
        report_path is not None
        and transition_reviews_path is not None
        and paths_refer_to_same_file(report_path, transition_reviews_path)
    ):
        return fail("--json-report must not alias --transition-reviews")
    if transition_reviews_path is not None and paths_refer_to_same_file(
        transition_reviews_path, target
    ):
        return fail("--transition-reviews must not alias the annotated target")
    if (
        transition_reviews_path is not None
        and boundary_path is not None
        and paths_refer_to_same_file(transition_reviews_path, boundary_path)
    ):
        return fail("--transition-reviews must not alias --boundary-manifest")
    if report_path is not None and any(
        paths_refer_to_same_file(report_path, protected)
        for protected in (TOOL_PATH, SHARED_HELPER_PATH, RELEASE_VALIDATOR_PATH)
    ):
        return fail("--json-report must not alias the audit toolchain")
    if transition_reviews_path is not None and any(
        paths_refer_to_same_file(transition_reviews_path, protected)
        for protected in (TOOL_PATH, SHARED_HELPER_PATH, RELEASE_VALIDATOR_PATH)
    ):
        return fail("--transition-reviews must not alias the audit toolchain")

    try:
        tool = {"name": TOOL_PATH.name, "sha256": sha256_bytes(TOOL_PATH.read_bytes())}
        tool_dependencies = [
            {
                "name": SHARED_HELPER_PATH.name,
                "sha256": sha256_bytes(SHARED_HELPER_PATH.read_bytes()),
            },
            {
                "name": RELEASE_VALIDATOR_PATH.name,
                "sha256": sha256_bytes(RELEASE_VALIDATOR_PATH.read_bytes()),
            },
        ]
    except OSError as error:
        return fail(f"could not seal audit toolchain: {error}")
    invocation_profile = {
        "heading_level": args.heading_level,
        "boundary_manifest_provided": boundary_path is not None,
        "transition_reviews_provided": transition_reviews_path is not None,
    }

    try:
        target_bytes = target.read_bytes()
        text = target_bytes.decode("utf-8")
    except (OSError, UnicodeError) as error:
        return fail(f"could not read UTF-8 target {target}: {error}")

    lines = split_lines(text)
    analysis_lines = mask_nonrendered_markdown(
        lines, preserve_page_markers=True
    )
    markers, malformed = find_page_markers(analysis_lines)
    input_errors: list[str] = []
    if malformed:
        input_errors.extend(
            f"noncanonical page marker at line {line}: {body!r}"
            for line, body in malformed
        )
    if not markers:
        input_errors.append("annotated target has no canonical page markers")
    marker_pages = [marker.page for marker in markers]
    if any(right <= left for left, right in zip(marker_pages, marker_pages[1:])):
        input_errors.append(
            "canonical page markers must be unique and strictly increasing"
        )

    boundaries: dict[int, str] | None = None
    boundary_sha256: str | None = None
    if boundary_path is not None:
        try:
            boundary_bytes = boundary_path.read_bytes()
            boundary_sha256 = sha256_bytes(boundary_bytes)
            # Hash and parse one immutable byte snapshot.  Re-reading the path
            # here would let a concurrent replacement separate the sealed hash
            # from the labels that actually govern transition acceptance.
            boundaries = parse_boundary_manifest(boundary_bytes)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            input_errors.append(f"invalid boundary manifest: {error}")
        if boundaries is not None:
            input_errors.extend(
                f"invalid or stale boundary manifest: {problem}"
                for problem in boundary_contract_problems(lines, markers, boundaries)
            )

    target_sha256 = sha256_bytes(target_bytes)
    transition_reviews_sha256: str | None = None
    transition_reviews: tuple[TransitionReview, ...] = ()
    if transition_reviews_path is not None:
        if boundary_path is None:
            input_errors.append(
                "--transition-reviews requires --boundary-manifest"
            )
        try:
            transition_review_bytes = transition_reviews_path.read_bytes()
            transition_reviews_sha256 = sha256_bytes(transition_review_bytes)
            (
                reviewed_target_sha256,
                reviewed_boundary_sha256,
                transition_reviews,
            ) = load_transition_reviews(transition_review_bytes)
            if reviewed_target_sha256 != target_sha256:
                input_errors.append(
                    "stale transition reviews: target_sha256 does not match "
                    "the current annotated target"
                )
            if boundary_path is not None and boundary_sha256 is None:
                input_errors.append(
                    "stale transition reviews: no readable boundary manifest "
                    "is available for boundary_manifest_sha256"
                )
            elif (
                boundary_sha256 is not None
                and reviewed_boundary_sha256 != boundary_sha256
            ):
                input_errors.append(
                    "stale transition reviews: boundary_manifest_sha256 does "
                    "not match the supplied boundary manifest"
                )
        except (OSError, ValueError) as error:
            input_errors.append(f"invalid transition reviews: {error}")

    if input_errors:
        payload: dict[str, object] = {
            "schema_version": 1,
            "artifact_type": "ocr-title-note-audit",
            "tool": tool,
            "tool_dependencies": tool_dependencies,
            "invocation_profile": invocation_profile,
            "target": str(target),
            "target_sha256": target_sha256,
            "heading_level": args.heading_level,
            "boundary_manifest": str(boundary_path) if boundary_path else None,
            "boundary_manifest_sha256": boundary_sha256,
            "transition_reviews": (
                str(transition_reviews_path) if transition_reviews_path else None
            ),
            "transition_reviews_sha256": transition_reviews_sha256,
            "status": "invalid-input",
            "errors": input_errors,
        }
        if report_path is not None:
            try:
                write_report(report_path, payload)
            except OSError as error:
                return fail(f"could not write JSON report {report_path}: {error}")
        for problem in input_errors:
            print(f"ERROR {problem}", file=sys.stderr)
        return 1

    items, mismatches, missing_notes, orphan_notes = audit_item_notes(
        analysis_lines, heading_level=args.heading_level
    )
    transition_review_map = {
        review.hazard_key: review for review in transition_reviews
    }
    hazards, used_review_keys = audit_cross_page_notes(
        analysis_lines,
        markers,
        boundaries=boundaries,
        transition_reviews=transition_review_map,
    )
    unused_reviews = [
        review
        for review in transition_reviews
        if review.hazard_key not in used_review_keys
    ]
    review_errors = [
        "unused transition review for scan page "
        f"{review.scan_page}, note kind {review.note_kind}: "
        f"{review.previous_text!r} -> {review.following_text!r}"
        for review in unused_reviews
    ]
    actionable_hazards = [
        hazard
        for hazard in hazards
        if str(hazard["status"]).startswith("actionable-")
    ]
    accepted_hazards = [
        hazard
        for hazard in hazards
        if str(hazard["status"]).startswith("accepted-")
    ]

    def total_marker(key: str, marker: str) -> int:
        return sum(int(item[key][marker]) for item in items)

    heading_marker_counts = {
        marker: total_marker("heading_markers", marker) for marker in STAR_KEYS
    }
    title_note_marker_counts = {
        marker: total_marker("title_note_markers", marker) for marker in STAR_KEYS
    }
    heading_star_count = sum(
        len(marker) * count for marker, count in heading_marker_counts.items()
    )
    note_star_count = sum(
        len(marker) * count for marker, count in title_note_marker_counts.items()
    )

    actionable_count = (
        len(mismatches) + len(orphan_notes) + len(actionable_hazards)
    )
    status = (
        "invalid-input"
        if review_errors
        else "pass"
        if actionable_count == 0
        else "requires-visual-review"
    )
    payload = {
        "schema_version": 1,
        "artifact_type": "ocr-title-note-audit",
        "tool": tool,
        "tool_dependencies": tool_dependencies,
        "invocation_profile": invocation_profile,
        "target": str(target),
        "target_sha256": target_sha256,
        "heading_level": args.heading_level,
        "boundary_manifest": str(boundary_path) if boundary_path else None,
        "boundary_manifest_sha256": boundary_sha256,
        "transition_reviews": (
            str(transition_reviews_path) if transition_reviews_path else None
        ),
        "transition_reviews_sha256": transition_reviews_sha256,
        "transition_review_count": len(transition_reviews),
        "unused_transition_reviews": [asdict(review) for review in unused_reviews],
        "errors": review_errors,
        "marker_count": len(markers),
        "main_item_count": len(items),
        "heading_marker_counts": heading_marker_counts,
        "title_note_marker_counts": title_note_marker_counts,
        # Weighted asterisk totals retain compatibility with the original
        # project-local auditor while the marker maps above prevent a false
        # pass between two ``*`` notes and one ``**`` note.
        "heading_star_count": heading_star_count,
        "note_star_count": note_star_count,
        "mismatch_count": len(mismatches),
        "missing_note_count": sum(
            int(record["missing_count"]) for record in missing_notes
        ),
        "orphan_count": len(orphan_notes),
        "cross_page_hazard_count": len(hazards),
        "cross_page_actionable_count": len(actionable_hazards),
        "cross_page_accepted_count": len(accepted_hazards),
        "actionable_count": actionable_count,
        "status": status,
        "mismatches": mismatches,
        "missing_notes": missing_notes,
        "orphan_notes": orphan_notes,
        "cross_page_hazards": hazards,
        "items": items,
    }

    if report_path is not None:
        try:
            write_report(report_path, payload)
        except OSError as error:
            return fail(f"could not write JSON report {report_path}: {error}")

    for problem in review_errors:
        print(f"ERROR {problem}", file=sys.stderr)

    print(
        f"status={status} items={len(items)} "
        f"heading-stars={heading_star_count} "
        f"note-stars={note_star_count} "
        f"mismatches={len(mismatches)} missing={payload['missing_note_count']} "
        f"orphans={len(orphan_notes)} cross-page-actionable="
        f"{len(actionable_hazards)} cross-page-accepted={len(accepted_hazards)}"
    )
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
