"""Shared page-marker helpers for the Jyut OCR proofreading scripts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Sequence


PAGE_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t]*)<!-- page_(?P<page>\d{3,}) -->(?P<trailing>[ \t]*)$"
)
PAGE_MARKER_CANDIDATE_RE = re.compile(r"<!--\s*page_", re.IGNORECASE)
HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+|$)")
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])[ \t]+\S")
TABLE_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
BOUNDARY_LABELS = frozenset(
    {"start", "join", "paragraph", "structural", "list-cont", "list-new"}
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


def load_boundary_manifest(path: Path) -> dict[int, str]:
    """Load a duplicate-safe ``{"boundaries": {page: label}}`` manifest."""

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle, object_pairs_hook=_JsonObject)
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
    return problems


def apply_boundary_contract(text: str, boundaries: dict[int, str]) -> str:
    """Remove page markers using only explicit manifest labels.

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
            lines[previous + 1 : following] = [newline]
        else:  # list-cont and list-new retain exactly one physical newline.
            lines[previous + 1 : following] = []
    return "".join(lines)


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
