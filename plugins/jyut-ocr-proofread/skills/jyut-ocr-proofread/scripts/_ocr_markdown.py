"""Shared page-marker helpers for the Jyut OCR proofreading scripts."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

from validate_release_evidence import (
    _artifact_path as release_artifact_path,
    _load_json as load_release_plan_json,
    validate_plan,
)


PAGE_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t]*)<!-- page_(?P<page>\d{3,}) -->(?P<trailing>[ \t]*)$"
)
PAGE_MARKER_CANDIDATE_RE = re.compile(r"<!--\s*page_", re.IGNORECASE)
HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
ATX_HEADING_CAPTURE_RE = re.compile(
    r"^ {0,3}(?P<marks>#{1,6})[ \t]+(?P<text>.*?)(?:[ \t]+#+)?[ \t]*$"
)
TITLE_NOTE_TERMINAL_STARS_RE = re.compile(
    r"(?<!\*)(?P<stars>\*{1,2})(?=(?:[ \t]*(?:（[^）]*）|\([^)]*\)))?[ \t]*$)"
)
TITLE_NOTE_DEFINITION_RE = re.compile(r"^(?P<stars>\*{1,2})[ \t]+(?P<text>\S.*)$")
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])[ \t]+\S")
TABLE_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
BOUNDARY_LABELS = frozenset(
    {"start", "join", "paragraph", "structural", "list-cont", "list-new", "table-cont"}
)


class _JsonObject(list[tuple[str, object]]):
    """Preserve JSON object pairs so duplicate keys remain detectable."""


@dataclass(frozen=True)
class PageMarker:
    """A canonical page marker found on its own line."""

    line_index: int
    line_number: int
    page: int
    indent: str


@dataclass(frozen=True)
class MarkerBoundary:
    """The whitespace meaning carried by a page marker."""

    marker: PageMarker
    kind: str
    previous_line: int | None
    next_line: int | None


@dataclass(frozen=True)
class ReleasePlanInventory:
    """Validated release artifacts captured from one stable plan snapshot."""

    plan_path: Path
    plan_sha256: str
    active_paths: tuple[Path, ...]
    active_sha256: tuple[str, ...]
    classified_paths: tuple[Path, ...]
    classified_sha256: tuple[str, ...]
    discovery_globs: tuple[str, ...]


def split_lines(text: str) -> list[str]:
    """Split text while retaining its original newline bytes."""

    return text.splitlines(keepends=True)


def line_body(line: str) -> str:
    """Remove only the line ending, not meaningful horizontal whitespace."""

    return line.rstrip("\r\n")


def is_blank(line: str) -> bool:
    return not line_body(line).strip()


def line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def mask_nonrendered_markdown(
    lines: Sequence[str], *, preserve_page_markers: bool = False
) -> list[str]:
    """Mask frontmatter, fences, HTML comments, and code spans by column.

    The returned lines retain their exact lengths and line endings.  Canonical
    page-marker comments may be preserved for page-aware consumers, while
    marker-looking text inside a fence or an already-open comment remains
    masked.  Unclosed code-span delimiters are left visible (fail closed).
    """

    masked = list(lines)
    protected_lines: set[int] = set()

    if lines:
        first = line_body(lines[0])
        if first.startswith("\ufeff"):
            first = first[1:]
        if first == "---":
            for index in range(1, len(lines)):
                if line_body(lines[index]) in {"---", "..."}:
                    protected_lines.update(range(index + 1))
                    break

    fence_character: str | None = None
    fence_length = 0
    for index, raw_line in enumerate(lines):
        if index in protected_lines:
            continue
        body = line_body(raw_line)
        if fence_character is not None:
            protected_lines.add(index)
            if re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                body,
            ):
                fence_character = None
                fence_length = 0
            continue
        match = re.match(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$", body)
        if match is not None:
            fence = match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            protected_lines.add(index)

    for index in protected_lines:
        masked[index] = " " * len(line_body(lines[index])) + line_ending(lines[index])

    in_comment = False
    for index, raw_line in enumerate(masked):
        if index in protected_lines:
            continue
        body = line_body(raw_line)
        if (
            preserve_page_markers
            and not in_comment
            and PAGE_MARKER_CANDIDATE_RE.search(body) is not None
        ):
            continue
        characters = list(body)
        cursor = 0
        while cursor < len(body):
            if in_comment:
                end = body.find("-->", cursor)
                stop = len(body) if end < 0 else end + 3
                for position in range(cursor, stop):
                    characters[position] = " "
                if end < 0:
                    cursor = len(body)
                else:
                    in_comment = False
                    cursor = stop
                continue
            start = body.find("<!--", cursor)
            if start < 0:
                break
            end = body.find("-->", start + 4)
            stop = len(body) if end < 0 else end + 3
            for position in range(start, stop):
                characters[position] = " "
            if end < 0:
                in_comment = True
                cursor = len(body)
            else:
                cursor = stop
        masked[index] = "".join(characters) + line_ending(raw_line)

    barrier_lines = {
        index
        for index, raw_line in enumerate(lines)
        if index in protected_lines
        or not line_body(raw_line).strip()
        or (
            preserve_page_markers
            and PAGE_MARKER_CANDIDATE_RE.search(line_body(raw_line)) is not None
        )
    }

    def mask_code_spans(start: int, end: int) -> None:
        bodies = [line_body(masked[index]) for index in range(start, end)]
        flattened = "\n".join(bodies)
        characters = list(flattened)
        cursor = 0
        while cursor < len(flattened):
            if flattened[cursor] != "`":
                cursor += 1
                continue
            opening_end = cursor + 1
            while opening_end < len(flattened) and flattened[opening_end] == "`":
                opening_end += 1
            opening_length = opening_end - cursor
            search = opening_end
            closing_end: int | None = None
            while search < len(flattened):
                candidate = flattened.find("`", search)
                if candidate < 0:
                    break
                candidate_end = candidate + 1
                while (
                    candidate_end < len(flattened)
                    and flattened[candidate_end] == "`"
                ):
                    candidate_end += 1
                if candidate_end - candidate == opening_length:
                    closing_end = candidate_end
                    break
                search = candidate_end
            if closing_end is None:
                cursor = opening_end
                continue
            for position in range(cursor, closing_end):
                if characters[position] != "\n":
                    characters[position] = " "
            cursor = closing_end
        rebuilt = "".join(characters).split("\n")
        assert len(rebuilt) == end - start
        for offset, body in enumerate(rebuilt):
            source_index = start + offset
            assert len(body) == len(line_body(masked[source_index]))
            masked[source_index] = body + line_ending(masked[source_index])

    region_start: int | None = None
    for index in range(len(lines) + 1):
        is_barrier = index == len(lines) or index in barrier_lines
        if not is_barrier and region_start is None:
            region_start = index
        elif is_barrier and region_start is not None:
            mask_code_spans(region_start, index)
            region_start = None

    return masked


def parse_atx_heading(body: str) -> tuple[int, str] | None:
    """Return an ATX heading's level and text, excluding optional closing marks."""

    match = ATX_HEADING_CAPTURE_RE.fullmatch(body)
    if match is None:
        return None
    return len(match.group("marks")), match.group("text").rstrip()


def _terminal_title_note_match(heading_text: str) -> re.Match[str] | None:
    """Return a title-note marker match, excluding full Markdown emphasis."""

    match = TITLE_NOTE_TERMINAL_STARS_RE.search(heading_text)
    if match is None:
        return None
    stars = match.group("stars")
    stripped = heading_text.strip()
    # ``## *italic title*`` and ``## **bold title**`` use the same terminal
    # bytes as this source-apparatus adapter.  A fully wrapped heading is
    # ordinary Markdown unless project-level visual review says otherwise.
    if (
        not heading_text[match.end() :].strip()
        and stripped.startswith(stars)
        and len(stripped) > len(stars) * 2
    ):
        return None
    return match


def terminal_title_note_stars(heading_text: str) -> str | None:
    """Return a terminal one- or two-star editorial title-note marker."""

    match = _terminal_title_note_match(heading_text)
    return match.group("stars") if match else None


def terminal_title_note_heading_marker(body: str) -> tuple[str, int] | None:
    """Return a heading's title-note marker and zero-based source column."""

    heading_match = ATX_HEADING_CAPTURE_RE.fullmatch(body)
    if heading_match is None:
        return None
    heading_text = heading_match.group("text").rstrip()
    marker_match = _terminal_title_note_match(heading_text)
    if marker_match is None:
        return None
    return (
        marker_match.group("stars"),
        heading_match.start("text") + marker_match.start("stars"),
    )


def title_note_definition_marker(body: str) -> str | None:
    """Return the one- or two-star marker starting a title-note definition."""

    match = TITLE_NOTE_DEFINITION_RE.match(body)
    return match.group("stars") if match else None


def preferred_newline(lines: Sequence[str], index: int) -> str:
    """Choose a nearby newline style, defaulting to LF."""

    for candidate in (index, index - 1, index + 1):
        if 0 <= candidate < len(lines):
            ending = line_ending(lines[candidate])
            if ending:
                return ending
    return "\n"


def find_page_markers(
    lines: Sequence[str],
) -> tuple[list[PageMarker], list[tuple[int, str]]]:
    """Return canonical markers and marker-like lines with invalid syntax."""

    markers: list[PageMarker] = []
    malformed: list[tuple[int, str]] = []
    for index, raw_line in enumerate(lines):
        body = line_body(raw_line)
        match = PAGE_MARKER_RE.fullmatch(body)
        if match:
            page = int(match.group("page"))
            if page < 1:
                malformed.append((index + 1, body))
                continue
            markers.append(
                PageMarker(
                    line_index=index,
                    line_number=index + 1,
                    page=page,
                    indent=match.group("indent"),
                )
            )
        elif PAGE_MARKER_CANDIDATE_RE.search(body):
            malformed.append((index + 1, body))
    return markers, malformed


def is_structural_line(line: str) -> bool:
    """Return whether a line must retain a Markdown line boundary."""

    body = line_body(line)
    stripped = body.strip()
    if not stripped:
        return True
    if body.startswith(("  ", "\t")):
        return True
    if HEADING_RE.match(body) or LIST_ITEM_RE.match(body) or TABLE_RE.match(body):
        return True
    if FENCE_RE.match(body):
        return True
    if stripped.startswith((">", "<!--", "```", "~~~")):
        return True
    if re.fullmatch(r" {0,3}(?:[-*_][ \t]*){3,}", body):
        return True
    return False


def _nearest_nonblank(
    lines: Sequence[str], start: int, step: int
) -> tuple[int | None, bool]:
    """Return nearest nonblank index and whether blank lines were crossed."""

    index = start
    crossed_blank = False
    while 0 <= index < len(lines):
        if not is_blank(lines[index]):
            return index, crossed_blank
        crossed_blank = True
        index += step
    return None, crossed_blank


def _previous_line_is_in_list_item(lines: Sequence[str], index: int) -> bool:
    """Return whether ``index`` belongs to an open Markdown list item.

    This deliberately inspects the raw leading whitespace.  A manifest builder
    that calls ``strip()`` before classifying the next page can otherwise turn
    an indented list continuation into ``join`` and silently emit a two-space
    Markdown hard break.
    """

    cursor = index
    while cursor >= 0:
        body = line_body(lines[cursor])
        if not body.strip():
            cursor -= 1
            continue
        if LIST_ITEM_RE.match(body):
            return True
        if body.startswith(("  ", "\t")):
            cursor -= 1
            continue
        return False
    return False


def classify_marker_boundary(
    lines: Sequence[str], marker: PageMarker
) -> MarkerBoundary:
    """Classify a marker as join, break, line, or edge.

    ``join`` removes the physical newline between ordinary prose lines.
    ``break`` preserves a paragraph break represented by surrounding blanks.
    ``line`` preserves one newline for Markdown structures such as lists.
    ``edge`` is a marker without content on one side.
    """

    previous, blank_before = _nearest_nonblank(lines, marker.line_index - 1, -1)
    following, blank_after = _nearest_nonblank(lines, marker.line_index + 1, 1)
    if previous is None or following is None:
        kind = "edge"
    elif blank_before or blank_after:
        kind = "break"
    elif marker.indent or is_structural_line(lines[previous]) or is_structural_line(
        lines[following]
    ):
        kind = "line"
    else:
        kind = "join"
    return MarkerBoundary(
        marker=marker,
        kind=kind,
        previous_line=None if previous is None else previous + 1,
        next_line=None if following is None else following + 1,
    )


def classify_all_boundaries(
    lines: Sequence[str], markers: Iterable[PageMarker]
) -> list[MarkerBoundary]:
    return [classify_marker_boundary(lines, marker) for marker in markers]


def _remove_line_ending(line: str) -> str:
    ending = line_ending(line)
    return line[: -len(ending)] if ending else line


def marker_context(
    lines: Sequence[str], marker: PageMarker, limit: int = 60
) -> tuple[str, str]:
    """Return compact nonblank context without assigning boundary semantics."""

    previous, _ = _nearest_nonblank(lines, marker.line_index - 1, -1)
    following, _ = _nearest_nonblank(lines, marker.line_index + 1, 1)

    def compact(index: int | None) -> str:
        if index is None:
            return "<EDGE>"
        value = re.sub(r"\s+", " ", line_body(lines[index]).strip())
        if len(value) > limit:
            return value[: limit - 1] + "…"
        return value

    return compact(previous), compact(following)


def parse_boundary_manifest(payload: bytes) -> dict[int, str]:
    """Parse duplicate-safe boundary-manifest bytes.

    Byte-oriented callers can hash and interpret the exact same snapshot,
    avoiding a time-of-check/time-of-use gap if another writer replaces the
    manifest while an audit is running.
    """

    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_JsonObject)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid UTF-8 JSON: {error}") from error
    if not isinstance(raw, _JsonObject):
        raise ValueError("boundary manifest root must be a JSON object")

    top_level: dict[str, object] = {}
    for key, value in raw:
        if key in top_level:
            raise ValueError(f"duplicate top-level manifest key: {key!r}")
        top_level[key] = value
    if set(top_level) != {"boundaries"}:
        raise ValueError("boundary manifest must contain only a 'boundaries' object")

    raw_boundaries = top_level["boundaries"]
    if not isinstance(raw_boundaries, _JsonObject):
        raise ValueError("manifest 'boundaries' must be a JSON object")

    boundaries: dict[int, str] = {}
    original_keys: dict[int, str] = {}
    for raw_page, raw_label in raw_boundaries:
        if not isinstance(raw_page, str) or not raw_page.isdigit() or int(raw_page) < 1:
            raise ValueError(f"invalid boundary page key: {raw_page!r}")
        page = int(raw_page)
        if page in boundaries:
            raise ValueError(
                f"duplicate boundary record for page {page}: "
                f"{original_keys[page]!r} and {raw_page!r}"
            )
        if not isinstance(raw_label, str) or raw_label not in BOUNDARY_LABELS:
            allowed = ", ".join(sorted(BOUNDARY_LABELS))
            raise ValueError(
                f"invalid boundary label for page {page}: {raw_label!r}; "
                f"expected one of {allowed}"
            )
        boundaries[page] = raw_label
        original_keys[page] = raw_page
    return boundaries


def load_boundary_manifest(path: Path) -> dict[int, str]:
    """Load a duplicate-safe ``{"boundaries": {page: label}}`` manifest."""

    return parse_boundary_manifest(path.read_bytes())


def boundary_contract_problems(
    lines: Sequence[str], markers: Sequence[PageMarker], boundaries: dict[int, str]
) -> list[str]:
    """Check that an explicit contract covers exactly the actual markers."""

    problems: list[str] = []
    actual_pages = [marker.page for marker in markers]
    unique_actual = set(actual_pages)
    duplicate_actual = sorted(
        page for page in unique_actual if actual_pages.count(page) > 1
    )
    if duplicate_actual:
        problems.append(
            "duplicate marker pages cannot share one contract: "
            + ", ".join(map(str, duplicate_actual))
        )
    missing = sorted(unique_actual - set(boundaries))
    extra = sorted(set(boundaries) - unique_actual)
    if missing:
        problems.append("missing boundary records: " + ", ".join(map(str, missing)))
    if extra:
        problems.append("extra boundary records: " + ", ".join(map(str, extra)))

    marker_by_page = {marker.page: marker for marker in markers}
    start_pages = [page for page, label in boundaries.items() if label == "start"]
    if len(start_pages) > 1:
        problems.append("only one boundary may use the 'start' label")
    for page in start_pages:
        marker = marker_by_page.get(page)
        if marker is None:
            continue
        if not markers or marker != markers[0]:
            problems.append("'start' is allowed only on the first actual marker")
            continue
        previous, _ = _nearest_nonblank(lines, marker.line_index - 1, -1)
        if previous is not None:
            problems.append("'start' marker has nonblank content before it")

    for page, label in boundaries.items():
        marker = marker_by_page.get(page)
        if marker is None or label == "start":
            continue
        previous, _ = _nearest_nonblank(lines, marker.line_index - 1, -1)
        following, _ = _nearest_nonblank(lines, marker.line_index + 1, 1)
        if previous is None or following is None:
            problems.append(
                f"boundary {page} ({label}) needs nonblank content on both sides"
            )
            continue
        if label == "list-cont" and not line_body(lines[following]).startswith(
            ("  ", "\t")
        ):
            problems.append(
                f"boundary {page} uses list-cont but following line is not indented"
            )
        if label == "list-new" and not LIST_ITEM_RE.match(line_body(lines[following])):
            problems.append(
                f"boundary {page} uses list-new but following line is not a list item"
            )
        if label == "table-cont" and not all(
            TABLE_RE.fullmatch(line_body(lines[index]))
            for index in (previous, following)
        ):
            problems.append(
                f"boundary {page} uses table-cont but both sides are not table rows"
            )
        following_body = line_body(lines[following])
        previous_in_list = _previous_line_is_in_list_item(lines, previous)
        following_is_list_item = LIST_ITEM_RE.match(following_body) is not None
        if previous_in_list:
            if following_is_list_item and label in {"join", "list-cont"}:
                problems.append(
                    f"boundary {page} starts a list item and cannot use {label}; "
                    "use list-new or a visually verified break"
                )
            elif (
                not following_is_list_item
                and following_body.startswith(("  ", "\t"))
                and label == "join"
            ):
                problems.append(
                    f"boundary {page} cannot join an indented continuation of an "
                    "open list item; use list-cont or a visually verified break"
                )
        elif following_is_list_item and label == "join":
            problems.append(
                f"boundary {page} cannot join directly into a list item; use "
                "list-new or a visually verified break"
            )
    return problems


def apply_boundary_contract_with_break_lines(
    text: str, boundaries: dict[int, str]
) -> tuple[str, frozenset[int]]:
    """Apply a contract and retain final line numbers of verified hard breaks.

    Call :func:`boundary_contract_problems` before this function. Surrounding blank
    lines are discarded and replaced by the separator mandated by the contract,
    so whitespace cannot silently change the result.
    """

    lines = split_lines(text)
    markers, malformed = find_page_markers(lines)
    if malformed:
        raise ValueError("cannot apply a boundary contract to malformed page markers")
    problems = boundary_contract_problems(lines, markers, boundaries)
    if problems:
        raise ValueError("; ".join(problems))

    break_sentinels: set[str] = set()
    for marker in reversed(markers):
        index = marker.line_index
        label = boundaries[marker.page]
        previous, _ = _nearest_nonblank(lines, index - 1, -1)
        following, _ = _nearest_nonblank(lines, index + 1, 1)
        if label == "start":
            if following is None:
                del lines[:]
            else:
                del lines[:following]
            continue

        # Validation guarantees content exists on both sides.
        assert previous is not None and following is not None
        newline = preferred_newline(lines, index)
        if label == "join":
            lines[previous] = _remove_line_ending(lines[previous])
            lines[previous + 1 : following] = []
        elif label in {"paragraph", "structural"}:
            sentinel = f"\0JYUT_OCR_VERIFIED_BREAK_{marker.page}\0"
            if sentinel in text:
                raise ValueError("annotated input contains reserved boundary sentinel")
            break_sentinels.add(sentinel)
            lines[previous + 1 : following] = [sentinel + newline]
        else:  # List and table continuations retain one physical newline.
            lines[previous + 1 : following] = []

    final_lines = split_lines("".join(lines))
    verified_break_lines: set[int] = set()
    for index, line in enumerate(final_lines):
        if line_body(line) not in break_sentinels:
            continue
        verified_break_lines.add(index + 1)
        final_lines[index] = line_ending(line) or "\n"
    return "".join(final_lines), frozenset(verified_break_lines)


def apply_boundary_contract(text: str, boundaries: dict[int, str]) -> str:
    """Remove page markers using only explicit manifest labels."""

    result, _ = apply_boundary_contract_with_break_lines(text, boundaries)
    return result


def parse_page_spec(values: Sequence[str] | None) -> set[int]:
    """Parse comma-separated pages and inclusive ranges such as ``2,5-7``."""

    pages: set[int] = set()
    for value in values or ():
        for part in value.split(","):
            token = part.strip()
            if not token:
                continue
            if "-" in token:
                first_text, last_text = token.split("-", 1)
                if not first_text.isdigit() or not last_text.isdigit():
                    raise ValueError(f"invalid page range: {token!r}")
                first, last = int(first_text), int(last_text)
                if first < 1 or last < first:
                    raise ValueError(f"invalid page range: {token!r}")
                pages.update(range(first, last + 1))
            else:
                if not token.isdigit() or int(token) < 1:
                    raise ValueError(f"invalid page number: {token!r}")
                pages.add(int(token))
    return pages


def expected_marker_pages(
    markers: Sequence[PageMarker],
    expected_count: int | None,
    skip_pages: set[int],
) -> list[int]:
    """Build the expected sequence for a whole file or a page segment."""

    if expected_count is not None:
        return [page for page in range(1, expected_count + 1) if page not in skip_pages]
    if not markers:
        return []
    first = markers[0].page
    last = markers[-1].page
    if last < first:
        return []
    return [page for page in range(first, last + 1) if page not in skip_pages]


def marker_sequence_problems(
    markers: Sequence[PageMarker],
    expected_count: int | None = None,
    skip_pages: set[int] | None = None,
) -> list[str]:
    """Return human-readable page marker order/count problems."""

    skipped = set(skip_pages or ())
    problems: list[str] = []
    if expected_count is not None and expected_count < 1:
        problems.append("expected count must be at least 1")
        return problems
    if expected_count is not None:
        outside = sorted(page for page in skipped if page > expected_count)
        if outside:
            problems.append(
                "skip pages outside expected range: " + ", ".join(map(str, outside))
            )

    actual = [marker.page for marker in markers]
    for previous, following in zip(markers, markers[1:]):
        if following.page <= previous.page:
            problems.append(
                f"page {following.page} at line {following.line_number} does not follow "
                f"page {previous.page} at line {previous.line_number}"
            )

    expected = expected_marker_pages(markers, expected_count, skipped)
    if actual != expected:
        actual_set = set(actual)
        expected_set = set(expected)
        missing = [page for page in expected if page not in actual_set]
        unexpected = [page for page in actual if page not in expected_set]
        if missing:
            problems.append("missing page markers: " + ", ".join(map(str, missing)))
        if unexpected:
            problems.append(
                "unexpected page markers: " + ", ".join(map(str, unexpected))
            )
        if not missing and not unexpected:
            problems.append("page markers are not in the expected order")
    return problems


@dataclass(frozen=True)
class PageManifestRecord:
    scan_page: int
    render_path: Path
    render_sha256: str
    marker_expected: bool


@dataclass(frozen=True)
class PageManifestData:
    source_sha256: str
    expected_scan_pages: int
    pages: tuple[PageManifestRecord, ...]


def load_page_manifest(manifest_path: Path) -> PageManifestData:
    """Load the provenance fields used by both render and marker audits."""

    with manifest_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle, object_pairs_hook=_JsonObject)
    if not isinstance(raw, _JsonObject):
        raise ValueError("page manifest root must be a JSON object")

    root: dict[str, object] = {}
    for key, value in raw:
        if key in root:
            raise ValueError(f"duplicate page manifest key: {key!r}")
        root[key] = value
    required_root = {"source_sha256", "expected_scan_pages", "pages"}
    missing_root = sorted(required_root - set(root))
    if missing_root:
        raise ValueError(
            "page manifest is missing required key(s): " + ", ".join(missing_root)
        )

    source_digest = root["source_sha256"]
    if not isinstance(source_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_digest
    ):
        raise ValueError("page manifest source_sha256 must be a lowercase SHA-256")
    expected_pages = root["expected_scan_pages"]
    if (
        not isinstance(expected_pages, int)
        or isinstance(expected_pages, bool)
        or expected_pages < 1
    ):
        raise ValueError("page manifest expected_scan_pages must be positive")
    raw_pages = root["pages"]
    if not isinstance(raw_pages, list) or isinstance(raw_pages, _JsonObject):
        raise ValueError("page manifest pages must be a JSON array")

    pages: list[PageManifestRecord] = []
    for index, raw_page in enumerate(raw_pages, 1):
        if not isinstance(raw_page, _JsonObject):
            raise ValueError(f"page manifest record {index} must be a JSON object")
        record: dict[str, object] = {}
        for key, value in raw_page:
            if key in record:
                raise ValueError(
                    f"duplicate page manifest record {index} key: {key!r}"
                )
            record[key] = value
        required_record = {"scan_page", "render_file", "render_sha256"}
        missing_record = sorted(required_record - set(record))
        if missing_record:
            raise ValueError(
                f"page manifest record {index} is missing required key(s): "
                + ", ".join(missing_record)
            )

        scan_page = record["scan_page"]
        if (
            not isinstance(scan_page, int)
            or isinstance(scan_page, bool)
            or scan_page < 1
        ):
            raise ValueError(
                f"page manifest record {index} scan_page must be positive"
            )
        render_file = record["render_file"]
        if not isinstance(render_file, str) or not render_file:
            raise ValueError(
                f"page manifest record {index} render_file must be non-empty"
            )
        render_digest = record["render_sha256"]
        if not isinstance(render_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", render_digest
        ):
            raise ValueError(
                f"page manifest record {index} render_sha256 must be a lowercase "
                "SHA-256"
            )
        marker_expected = record.get("marker_expected", True)
        if not isinstance(marker_expected, bool):
            raise ValueError(
                f"page manifest record {index} marker_expected must be boolean"
            )
        render_path = Path(render_file)
        if not render_path.is_absolute():
            render_path = manifest_path.resolve().parent / render_path
        pages.append(
            PageManifestRecord(
                scan_page=scan_page,
                render_path=render_path.resolve(),
                render_sha256=render_digest,
                marker_expected=marker_expected,
            )
        )

    return PageManifestData(source_digest, expected_pages, tuple(pages))


def page_manifest_problems(
    manifest_path: Path, *, source_pdf_path: Path
) -> list[tuple[str, str]]:
    """Verify source identity, scan inventory, and every declared render hash."""

    manifest = load_page_manifest(manifest_path)
    problems: list[tuple[str, str]] = []
    if not source_pdf_path.is_file():
        problems.append(
            ("source-pdf-missing", f"selected source PDF is missing: {source_pdf_path}")
        )
    elif file_sha256(source_pdf_path) != manifest.source_sha256:
        problems.append(
            (
                "source-pdf-hash-mismatch",
                f"selected source PDF does not match page manifest SHA-256: "
                f"{source_pdf_path}",
            )
        )

    scan_pages = [page.scan_page for page in manifest.pages]
    seen_render_paths: list[Path] = []
    for page in manifest.pages:
        render_path = page.render_path
        if any(
            paths_refer_to_same_file(render_path, previous)
            for previous in seen_render_paths
        ):
            problems.append(
                (
                    "page-manifest-duplicate-render",
                    f"multiple scan records use the same render file: {render_path}",
                )
            )
        else:
            seen_render_paths.append(render_path)
        if not render_path.is_file():
            problems.append(
                (
                    "render-missing",
                    f"scan page {page.scan_page} render is missing: {render_path}",
                )
            )
        elif file_sha256(render_path) != page.render_sha256:
            problems.append(
                (
                    "render-hash-mismatch",
                    f"scan page {page.scan_page} render changed: {render_path}",
                )
            )

    expected_sequence = list(range(1, manifest.expected_scan_pages + 1))
    if scan_pages != expected_sequence:
        problems.append(
            (
                "page-manifest-scan-sequence",
                "page manifest scan_page sequence is not exactly "
                f"1-{manifest.expected_scan_pages}",
            )
        )
    if len(manifest.pages) != manifest.expected_scan_pages:
        problems.append(
            (
                "page-manifest-page-count",
                f"page manifest declares {manifest.expected_scan_pages} scan pages "
                f"but contains {len(manifest.pages)} record(s)",
            )
        )
    return problems


def page_manifest_marker_coverage_problems(
    manifest_path: Path, *, annotated_path: Path
) -> list[tuple[str, str]]:
    """Bind a sealed annotated marker inventory to its sealed page manifest."""

    manifest = load_page_manifest(manifest_path)
    skipped_pages = {
        page.scan_page for page in manifest.pages if not page.marker_expected
    }
    with annotated_path.open("r", encoding="utf-8", newline="") as handle:
        annotated_text = handle.read()
    markers, malformed = find_page_markers(split_lines(annotated_text))
    problems = [
        (
            "page-manifest-marker-malformed",
            f"annotated input line {line_number} has malformed page marker: {body!r}",
        )
        for line_number, body in malformed
    ]
    problems.extend(
        ("page-manifest-marker-coverage", problem)
        for problem in marker_sequence_problems(
            markers, manifest.expected_scan_pages, skipped_pages
        )
    )
    return problems


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file without decoding its contents."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paths_refer_to_same_file(first: Path, second: Path) -> bool:
    """Compare paths safely, including existing symlinks and hard links."""

    if first.resolve() == second.resolve():
        return True
    try:
        if os.path.samefile(first, second):
            return True
    except OSError:
        pass

    first_parent = first.resolve().parent
    second_parent = second.resolve().parent
    parents_match = first_parent == second_parent
    if not parents_match:
        try:
            parents_match = os.path.samefile(first_parent, second_parent)
        except OSError:
            parents_match = False
    if not parents_match:
        return False

    def portable_name(path: Path) -> str:
        return unicodedata.normalize("NFC", path.name).casefold()

    # Conservatively reject case-only aliases even on a case-sensitive volume.
    return portable_name(first) == portable_name(second)


def load_validated_release_plan(plan_path: Path) -> ReleasePlanInventory:
    """Validate and strictly parse one unchanged release-plan snapshot."""

    try:
        sealed_plan = plan_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"release plan does not exist: {plan_path}") from error
    if not sealed_plan.is_file():
        raise ValueError(f"release plan is not a regular file: {plan_path}")

    before_validation = file_sha256(sealed_plan)
    report = validate_plan(sealed_plan)
    after_validation = file_sha256(sealed_plan)
    if before_validation != after_validation:
        raise ValueError("release plan changed while it was being validated")
    if not report["ok"]:
        details = "; ".join(str(error) for error in report["errors"])
        raise ValueError(f"release plan validation failed: {details}")

    plan = load_release_plan_json(sealed_plan)
    after_parse = file_sha256(sealed_plan)
    if after_parse != after_validation:
        raise ValueError("release plan changed between validation and strict parsing")
    if not isinstance(plan, dict):
        raise ValueError("release plan root must be a JSON object")
    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("release plan artifacts must be an array")
    discovery = plan.get("discovery")
    if not isinstance(discovery, dict) or not isinstance(
        discovery.get("globs"), list
    ):
        raise ValueError("release plan discovery.globs must be an array")

    base = sealed_plan.parent.resolve(strict=True)
    classified_paths: list[Path] = []
    classified_sha256: list[str] = []
    active_paths: list[Path] = []
    active_sha256: list[str] = []
    for index, artifact in enumerate(raw_artifacts):
        if not isinstance(artifact, dict):
            raise ValueError(f"release plan artifacts[{index}] must be an object")
        path = release_artifact_path(
            base,
            artifact.get("path"),
            f"release plan artifacts[{index}].path",
        )
        classified_paths.append(path)
        declared_sha256 = artifact.get("sha256")
        if not isinstance(declared_sha256, str):
            raise ValueError(
                f"release plan artifacts[{index}].sha256 must be a string"
            )
        current_sha256 = file_sha256(path)
        if current_sha256 != declared_sha256:
            raise ValueError(
                f"release artifact changed after plan validation: {path}"
            )
        classified_sha256.append(declared_sha256)
        if (
            artifact.get("classification") == "include"
            and artifact.get("lifecycle") == "active"
            and artifact.get("validation") == "pass"
        ):
            active_paths.append(path)
            active_sha256.append(declared_sha256)

    globs = discovery["globs"]
    if not all(isinstance(pattern, str) for pattern in globs):
        raise ValueError("release plan discovery.globs must contain only strings")
    if file_sha256(sealed_plan) != after_parse:
        raise ValueError("release plan changed while artifact closure was parsed")
    return ReleasePlanInventory(
        plan_path=sealed_plan,
        plan_sha256=after_parse,
        active_paths=tuple(active_paths),
        active_sha256=tuple(active_sha256),
        classified_paths=tuple(classified_paths),
        classified_sha256=tuple(classified_sha256),
        discovery_globs=tuple(globs),
    )


def release_plan_snapshot_problems(
    inventory: ReleasePlanInventory,
) -> list[str]:
    """Detect plan or classified-evidence drift from a validated snapshot."""

    problems: list[str] = []
    if not inventory.plan_path.is_file():
        problems.append(f"release plan disappeared: {inventory.plan_path}")
    elif file_sha256(inventory.plan_path) != inventory.plan_sha256:
        problems.append("release plan changed after validation")
    for path, expected in zip(
        inventory.classified_paths, inventory.classified_sha256, strict=True
    ):
        if not path.is_file():
            problems.append(f"classified release artifact disappeared: {path}")
        elif file_sha256(path) != expected:
            problems.append(
                f"classified release artifact changed after validation: {path}"
            )
    return problems


def release_plan_discovery_matches(
    inventory: ReleasePlanInventory, candidate: Path
) -> tuple[str, ...]:
    """Return discovery globs that would classify a not-yet-created path."""

    # Resolve the parent but preserve the final directory-entry name.  A
    # dangling symlink does not satisfy ``Path.exists()``, yet writing through
    # it can make the symlink itself become a regular-file discovery match.
    lexical_candidate = candidate.parent.resolve(strict=False) / candidate.name
    try:
        relative = lexical_candidate.relative_to(inventory.plan_path.parent)
    except ValueError:
        return ()
    parts = relative.parts

    def matches(pattern: str) -> bool:
        # A trailing separator asks pathlib for directories only.  Generated
        # output and receipt paths are files, so such a glob cannot select one.
        if pattern.endswith(os.sep):
            return False
        pattern_parts = Path(pattern).parts

        def visit(pattern_index: int, path_index: int) -> bool:
            if pattern_index == len(pattern_parts):
                return path_index == len(parts)
            current = pattern_parts[pattern_index]
            if current == "**":
                if pattern_index + 1 == len(pattern_parts):
                    # ``base.glob('prefix/**')`` selects descendants and the
                    # prefix directory, but not a file at the prefix itself.
                    return path_index < len(parts)
                return visit(pattern_index + 1, path_index) or (
                    path_index < len(parts)
                    and visit(pattern_index, path_index + 1)
                )
            if path_index >= len(parts):
                return False
            direct = fnmatch.fnmatchcase(parts[path_index], current)
            portable = fnmatch.fnmatchcase(
                parts[path_index].casefold(), current.casefold()
            )
            return (direct or portable) and visit(
                pattern_index + 1, path_index + 1
            )

        return visit(0, 0)

    return tuple(pattern for pattern in inventory.discovery_globs if matches(pattern))


def _receipt_path(receipt_path: Path, target_path: Path) -> str:
    """Store a path relative to the receipt so a project tree can be moved."""

    return os.path.relpath(target_path.resolve(), receipt_path.resolve().parent)


def build_assembly_receipt(
    receipt_path: Path,
    *,
    annotated_path: Path,
    boundary_manifest_path: Path,
    release_plan_path: Path,
    output_path: Path,
    output_text: str,
    upstream_paths: Sequence[Path] = (),
) -> dict[str, object]:
    """Build a hash receipt for every input that makes a final OCR artefact."""

    named_inputs = [
        ("annotated", annotated_path),
        ("boundary-manifest", boundary_manifest_path),
        ("release-plan", release_plan_path),
        *(("upstream", path) for path in upstream_paths),
    ]

    seen: list[tuple[Path, str]] = []
    inputs: list[dict[str, str]] = []
    for role, path in named_inputs:
        duplicate_role = next(
            (
                previous_role
                for previous_path, previous_role in seen
                if paths_refer_to_same_file(path, previous_path)
            ),
            None,
        )
        if duplicate_role is not None:
            raise ValueError(
                f"duplicate assembly input {path}: already recorded as "
                f"{duplicate_role}"
            )
        if paths_refer_to_same_file(path, output_path):
            raise ValueError(f"assembly input must differ from output: {path}")
        if paths_refer_to_same_file(path, receipt_path):
            raise ValueError(f"assembly input must differ from receipt: {path}")
        seen.append((path, role))
        inputs.append(
            {
                "role": role,
                "path": _receipt_path(receipt_path, path),
                "sha256": file_sha256(path),
            }
        )

    if paths_refer_to_same_file(receipt_path, output_path):
        raise ValueError("receipt path must differ from output path")

    return {
        "schema_version": 2,
        "inputs": inputs,
        "output": {
            "path": _receipt_path(receipt_path, output_path),
            "sha256": hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
        },
    }


def write_assembly_receipt(path: Path, receipt: dict[str, object]) -> None:
    """Write a deterministic UTF-8 JSON receipt."""

    rendered = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(rendered)


def _json_object_mapping(
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
        expected = ", ".join(sorted(expected_keys))
        raise ValueError(f"{context} must contain exactly: {expected}")
    return result


def _receipt_record(
    value: object, *, context: str, include_role: bool
) -> dict[str, str]:
    expected_keys = {"path", "sha256"}
    if include_role:
        expected_keys.add("role")
    record = _json_object_mapping(
        value, context=context, expected_keys=expected_keys
    )
    for key in expected_keys:
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError(f"{context} {key!r} must be a non-empty string")
    digest = record["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{context} has an invalid SHA-256 digest")
    return {key: str(record[key]) for key in expected_keys}


def _load_assembly_receipt(
    receipt_path: Path,
) -> tuple[int, list[dict[str, str]], dict[str, str]]:
    """Load and validate receipt schema before any trust decision is made."""

    with receipt_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle, object_pairs_hook=_JsonObject)
    root = _json_object_mapping(
        raw,
        context="assembly receipt root",
        expected_keys={"schema_version", "inputs", "output"},
    )
    schema_version = root["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in {1, 2}
    ):
        raise ValueError("assembly receipt schema_version must be integer 1 or 2")

    raw_inputs = root["inputs"]
    if not isinstance(raw_inputs, list) or isinstance(raw_inputs, _JsonObject):
        raise ValueError("assembly receipt inputs must be a JSON array")
    records = [
        _receipt_record(item, context=f"assembly input {index}", include_role=True)
        for index, item in enumerate(raw_inputs, 1)
    ]
    output = _receipt_record(
        root["output"], context="assembly output", include_role=False
    )

    roles = [record["role"] for record in records]
    allowed_roles = {"annotated", "boundary-manifest", "upstream"}
    if schema_version == 2:
        allowed_roles.add("release-plan")
    unexpected_roles = sorted(set(roles) - allowed_roles)
    if unexpected_roles:
        raise ValueError(
            "assembly receipt has invalid input role(s): "
            + ", ".join(unexpected_roles)
        )
    required_roles = ["annotated", "boundary-manifest"]
    if schema_version == 2:
        required_roles.append("release-plan")
    for required in required_roles:
        if roles.count(required) != 1:
            raise ValueError(
                f"assembly receipt must contain exactly one {required!r} input"
            )
    return schema_version, records, output


def _resolve_receipt_path(receipt_path: Path, recorded_path: str) -> Path:
    path = Path(recorded_path)
    if not path.is_absolute():
        path = receipt_path.resolve().parent / path
    return path.resolve()


def _normalized_path(path: Path) -> str:
    return unicodedata.normalize(
        "NFC", os.path.normcase(str(path.resolve(strict=False)))
    ).casefold()


def _release_plan_receipt_problems(
    receipt_path: Path,
    records: Sequence[dict[str, str]],
    output: dict[str, str],
) -> list[tuple[str, str]]:
    """Revalidate a v2 plan and bind receipt upstreams to its active closure."""

    problems: list[tuple[str, str]] = []
    by_role: dict[str, list[Path]] = {}
    for record in records:
        by_role.setdefault(record["role"], []).append(
            _resolve_receipt_path(receipt_path, record["path"])
        )
    plan_path = by_role["release-plan"][0]
    try:
        inventory = load_validated_release_plan(plan_path)
    except (OSError, UnicodeError, ValueError) as error:
        return [
            (
                "assembly-receipt-release-plan-invalid",
                f"current release plan is invalid: {error}",
            )
        ]

    annotated_path = by_role["annotated"][0]
    boundary_path = by_role["boundary-manifest"][0]

    def active_matches(path: Path) -> list[Path]:
        return [
            candidate
            for candidate in inventory.active_paths
            if paths_refer_to_same_file(path, candidate)
        ]

    if len(active_matches(annotated_path)) != 1:
        problems.append(
            (
                "assembly-receipt-annotated-not-active",
                "receipt annotated input is not exactly one active included "
                "release-plan artifact",
            )
        )
    if len(active_matches(boundary_path)) != 1:
        problems.append(
            (
                "assembly-receipt-boundary-not-active",
                "receipt boundary manifest is not exactly one active included "
                "release-plan artifact",
            )
        )

    recorded_output = _resolve_receipt_path(receipt_path, output["path"])
    for role, generated in (
        ("assembly output", recorded_output),
        ("assembly receipt", receipt_path.resolve()),
    ):
        if any(
            paths_refer_to_same_file(generated, artifact)
            for artifact in inventory.classified_paths
        ):
            problems.append(
                (
                    "assembly-receipt-generated-path-classified",
                    f"{role} aliases a classified release-plan artifact: {generated}",
                )
            )

    expected_upstreams = [
        path
        for path in inventory.active_paths
        if not paths_refer_to_same_file(path, annotated_path)
        and not paths_refer_to_same_file(path, boundary_path)
    ]
    actual_upstreams = by_role.get("upstream", [])
    if [_normalized_path(path) for path in actual_upstreams] != [
        _normalized_path(path) for path in expected_upstreams
    ]:
        problems.append(
            (
                "assembly-receipt-release-plan-closure-mismatch",
                "receipt upstream paths do not exactly match the ordered active "
                "release-plan closure after annotated/boundary exclusions",
            )
        )
    return problems


def assembly_receipt_verified_break_lines(receipt_path: Path) -> frozenset[int]:
    """Return paragraph/structural break lines proven by sealed assembly inputs.

    Invalid receipt schema remains a hard error.  Missing or stale assembly inputs
    return no exemptions here and are reported with richer diagnostics by
    :func:`assembly_receipt_problems`.
    """

    schema_version, records, output = _load_assembly_receipt(receipt_path)
    if schema_version != 2:
        return frozenset()
    records_by_role = {
        role: next(record for record in records if record["role"] == role)
        for role in ("annotated", "boundary-manifest")
    }
    annotated_record = records_by_role["annotated"]
    boundary_record = records_by_role["boundary-manifest"]
    annotated_path = _resolve_receipt_path(receipt_path, annotated_record["path"])
    boundary_path = _resolve_receipt_path(receipt_path, boundary_record["path"])
    output_path = _resolve_receipt_path(receipt_path, output["path"])
    if _release_plan_receipt_problems(receipt_path, records, output):
        return frozenset()
    if not all(
        path.is_file() for path in (annotated_path, boundary_path, output_path)
    ):
        return frozenset()
    try:
        if (
            file_sha256(annotated_path) != annotated_record["sha256"]
            or file_sha256(boundary_path) != boundary_record["sha256"]
            or file_sha256(output_path) != output["sha256"]
        ):
            return frozenset()
        with annotated_path.open("r", encoding="utf-8", newline="") as handle:
            annotated_text = handle.read()
        boundaries = load_boundary_manifest(boundary_path)
        rebuilt, break_lines = apply_boundary_contract_with_break_lines(
            annotated_text, boundaries
        )
        if output_path.read_bytes() != rebuilt.encode("utf-8"):
            return frozenset()
    except (OSError, UnicodeError, ValueError):
        return frozenset()
    return break_lines


def assembly_receipt_problems(
    receipt_path: Path,
    *,
    expected_output_path: Path,
    required_input_paths: Sequence[Path] = (),
    page_manifest_path: Path | None = None,
) -> list[tuple[str, str]]:
    """Verify a final output and every recorded input against a hash receipt.

    ``required_input_paths`` seals interpretation files supplied to the final
    audit, such as a notation policy or source-exception ledger.  Otherwise a
    new unrecorded exception file could change the audit result after the
    release candidate was finalized.  When ``page_manifest_path`` is present,
    its per-page marker policy is also checked against the sealed annotated
    input, closing the gap between render provenance and Markdown coverage.
    """

    schema_version, records, output = _load_assembly_receipt(receipt_path)

    problems: list[tuple[str, str]] = []
    if schema_version == 1:
        problems.append(
            (
                "assembly-receipt-release-plan-missing",
                "legacy v1 receipt has no dedicated release-plan input; "
                "re-finalize with a validated release plan",
            )
        )
    else:
        problems.extend(
            _release_plan_receipt_problems(receipt_path, records, output)
        )
    recorded_output = _resolve_receipt_path(receipt_path, output["path"])
    if recorded_output != expected_output_path.resolve():
        problems.append(
            (
                "assembly-output-mismatch",
                f"receipt output is {recorded_output}, not audited target "
                f"{expected_output_path.resolve()}",
            )
        )

    seen_paths: list[Path] = []
    paths_by_role: dict[str, list[Path]] = {}
    for record in records:
        path = _resolve_receipt_path(receipt_path, record["path"])
        if any(paths_refer_to_same_file(path, previous) for previous in seen_paths):
            raise ValueError(f"assembly receipt repeats input path: {path}")
        seen_paths.append(path)
        paths_by_role.setdefault(record["role"], []).append(path)
        if not path.is_file():
            problems.append(
                (
                    "assembly-input-missing",
                    f"recorded {record['role']} input is missing: {path}",
                )
            )
            continue
        if file_sha256(path) != record["sha256"]:
            problems.append(
                (
                    "assembly-input-changed",
                    f"recorded {record['role']} input changed after finalization: {path}",
                )
            )

    for required_path in required_input_paths:
        resolved_required = required_path.resolve()
        if not any(
            paths_refer_to_same_file(resolved_required, recorded_path)
            for recorded_path in seen_paths
        ):
            problems.append(
                (
                    "assembly-audit-input-unsealed",
                    "final-audit interpretation input is not sealed by the "
                    f"assembly receipt: {resolved_required}",
                )
            )

    if not expected_output_path.is_file():
        problems.append(
            ("assembly-output-missing", f"final output is missing: {expected_output_path}")
        )
    elif file_sha256(expected_output_path) != output["sha256"]:
        problems.append(
            (
                "assembly-output-changed",
                f"final output changed after receipt creation: {expected_output_path}",
            )
        )

    annotated_path = paths_by_role["annotated"][0]
    boundary_path = paths_by_role["boundary-manifest"][0]
    if page_manifest_path is not None and annotated_path.is_file():
        problems.extend(
            page_manifest_marker_coverage_problems(
                page_manifest_path, annotated_path=annotated_path
            )
        )
    if any(
        paths_refer_to_same_file(recorded_output, input_path)
        for input_path in seen_paths
    ):
        raise ValueError("assembly receipt output aliases a recorded input")
    if (
        annotated_path.is_file()
        and boundary_path.is_file()
        and expected_output_path.is_file()
    ):
        try:
            with annotated_path.open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                annotated_text = handle.read()
            current_boundaries = load_boundary_manifest(boundary_path)
            rebuilt = apply_boundary_contract(annotated_text, current_boundaries)
            current_output = expected_output_path.read_bytes()
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(
                (
                    "assembly-rebuild-failed",
                    f"could not rebuild final from current annotated and boundary "
                    f"inputs: {error}",
                )
            )
        else:
            if current_output != rebuilt.encode("utf-8"):
                problems.append(
                    (
                        "assembly-final-stale",
                        "final output is not the exact result of applying the current "
                        "boundary manifest to the current annotated input",
                    )
                )
    return problems
