"""Shared page-marker helpers for the Jyut OCR proofreading scripts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

from jyut_ocr.core import tables as _tables
from jyut_ocr.core.note_markers import NOTE_CIRCLED, NOTE_LINE_RE


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
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])[ \t]+\S")
TABLE_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
FENCE_RE = _tables.FENCE_RE
BOUNDARY_LABELS = frozenset(
    {"start", "join", "paragraph", "structural", "list-cont", "list-new", "table-cont"}
)
THEMATIC_BREAK_RE = re.compile(r" {0,3}(?:[-*_][ \t]*){3,}")
FOOTNOTE_HEAD_RE = re.compile(r" {0,3}\[\^(?P<label>[^\]\s]+)\]:")
FOOTNOTE_CALL_RE = re.compile(r"\[\^(?P<label>[^\]\s]+)\]")


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


table_cells = _tables.table_cells


def is_table_row(line: str) -> bool:
    """Whether a line is a Markdown table row under the skill's unified definition."""
    return table_cells(line) is not None


def html_table_lines(lines: Sequence[str]) -> frozenset[int]:
    """Indices of the lines that lie inside an HTML <table>...</table>."""
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    inside: set[int] = set()
    for start, end in _tables.html_table_spans("".join(lines)):
        inside.update(range(bisect_right(starts, start) - 1, bisect_left(starts, end)))
    return frozenset(inside)


def is_structural_line(line: str) -> bool:
    """Return whether a line must retain a Markdown line boundary."""

    body = line_body(line)
    stripped = body.strip()
    if not stripped:
        return True
    if body.startswith(("  ", "\t")):
        return True
    if HEADING_RE.match(body) or LIST_ITEM_RE.match(body) or is_table_row(body):
        return True
    if _tables.HTML_TABLE_LINE_RE.match(body):
        return True
    if FENCE_RE.match(body):
        return True
    if stripped.startswith((">", "<!--", "```", "~~~")):
        return True
    if THEMATIC_BREAK_RE.fullmatch(body):
        return True
    return False


def foot_notes(lines: Sequence[str], end: int) -> int:
    """Where the foot notes that end LINES[:END] start, or END when none.

    Includes lines before END opening with a note number (NOTE_LINE_RE) or
    with a footnote definition's head (`[^1-2]: `, the label _footnotes.py
    gives a printed number used again), blank lines between them, and an
    optional printed rule (THEMATIC_BREAK_RE) right above them so a `join`
    boundary can carry foot-of-page notes past the end of the split paragraph.
    """
    start, index = end, end - 1
    while index >= 0:
        body = line_body(lines[index])
        if not body.strip():
            index -= 1
        elif NOTE_LINE_RE.match(body) or (body.startswith("[^") and FOOTNOTE_HEAD_RE.match(body)):
            start, index = index, index - 1
        else:
            if start < end and THEMATIC_BREAK_RE.fullmatch(body):
                start = index
            break
    return start


def _takes_no_run_on(body: str) -> bool:
    """Whether a line is structure that no join may run on to."""
    return bool(
        HEADING_RE.match(body)
        or FENCE_RE.match(body)
        or THEMATIC_BREAK_RE.fullmatch(body)
        or body.lstrip().startswith("<")
    )


def _join_previous(lines: Sequence[str], index: int) -> tuple[int | None, int]:
    """Return (previous prose line before foot notes, foot-notes start index)."""
    notes = foot_notes(lines, index)
    previous, _ = _nearest_nonblank(lines, notes - 1, -1)
    if (
        previous is not None
        and notes < index
        and PAGE_MARKER_RE.fullmatch(line_body(lines[previous]))
    ):
        previous = None
    return previous, notes


def place_footnotes(
    lines: Sequence[str], breaks: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """Relocate unambiguous `[^label]:` definitions right after their calling blocks.

    Only labels with exactly one definition and one call (outside definitions)
    move; other definitions stay in place. Disabled by default so printed
    page-foot or section-foot order is preserved unless opt-in `--relocate-footnotes`
    is requested.
    """

    def gap(line: str) -> bool:
        return is_blank(line) or line_body(line) in breaks

    heads: dict[int, str] = {}
    calls: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        body = line_body(line)
        head = FOOTNOTE_HEAD_RE.match(body)
        if head:
            heads[index] = head.group("label")
        for call in FOOTNOTE_CALL_RE.finditer(body, head.end() if head else 0):
            calls.setdefault(call.group("label"), []).append(index)
    defined = Counter(heads.values())
    moving = {
        index: label
        for index, label in heads.items()
        if defined[label] == 1
        and len(calls.get(label, ())) == 1
        and calls[label][0] not in heads
    }
    if not moving:
        return list(lines)
    rest: list[str] = []
    at: dict[int, int] = {}
    closing = False
    for index, line in enumerate(lines):
        if index in moving:
            closing = True
            continue
        if closing and gap(line) and (not rest or gap(rest[-1])):
            if rest and line_body(line) in breaks:
                rest[-1] = line
            continue
        closing = closing and gap(line)
        at[index] = len(rest)
        rest.append(line)
    if closing:
        while rest and is_blank(rest[-1]):
            rest.pop()
    after: dict[int, list[str]] = {}
    for index, label in moving.items():
        end = at[calls[label][0]]
        if not HEADING_RE.match(line_body(rest[end])):
            while end + 1 < len(rest) and not gap(rest[end + 1]):
                end += 1
        while (
            end + 2 < len(rest)
            and gap(rest[end + 1])
            and FOOTNOTE_HEAD_RE.match(line_body(rest[end + 2]))
        ):
            end += 2
        after.setdefault(end, []).append(label)
    order = {label: index for index, label in moving.items()}
    out: list[str] = []
    for index, line in enumerate(rest):
        out.append(line)
        if index not in after:
            continue
        newline = preferred_newline(rest, index)
        if not line_ending(line):
            out[-1] += newline
        for label in sorted(after[index], key=lambda item: (len(item), item)):
            definition = lines[order[label]]
            out += [
                newline,
                definition if line_ending(definition) else definition + newline,
            ]
        if index + 1 < len(rest) and not gap(rest[index + 1]):
            out.append(newline)
    if lines and not line_ending(lines[-1]) and out:
        out[-1] = _remove_line_ending(out[-1])

    def kept(text: Sequence[str]) -> list[str]:
        return sorted(line_body(line) for line in text if not gap(line))

    assert kept(out) == kept(lines), "placing the footnotes lost or doubled a line"
    return out


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
    lines: Sequence[str],
    marker: PageMarker,
    table_lines: frozenset[int] | None = None,
) -> MarkerBoundary:
    """Classify a marker as join, break, line, or edge.

    ``join`` removes the physical newline between ordinary prose lines.
    ``break`` preserves a paragraph break represented by surrounding blanks.
    ``line`` preserves one newline for Markdown structures such as lists.
    ``edge`` is a marker without content on one side.
    """

    if table_lines is None:
        table_lines = html_table_lines(lines)
    previous, blank_before = _nearest_nonblank(lines, marker.line_index - 1, -1)
    following, blank_after = _nearest_nonblank(lines, marker.line_index + 1, 1)
    if previous is None or following is None:
        kind = "edge"
    elif blank_before or blank_after:
        kind = "break"
    elif (
        marker.indent
        or is_structural_line(lines[previous])
        or is_structural_line(lines[following])
        or previous in table_lines
        or following in table_lines
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
    table_lines = html_table_lines(lines)
    return [classify_marker_boundary(lines, marker, table_lines) for marker in markers]


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


def _line_is_table_for_boundary(
    lines: Sequence[str], index: int, table_lines: frozenset[int]
) -> bool:
    body = line_body(lines[index])
    return (
        is_table_row(body)
        or index in table_lines
        or _tables.HTML_TABLE_LINE_RE.match(body) is not None
    )


def join_touches_table(
    lines: Sequence[str], marker: PageMarker, table_lines: frozenset[int]
) -> bool:
    """Whether joining across MARKER would splice a Markdown or HTML table line."""
    previous, _ = _join_previous(lines, marker.line_index)
    following, _ = _nearest_nonblank(lines, marker.line_index + 1, 1)
    return any(
        index is not None and _line_is_table_for_boundary(lines, index, table_lines)
        for index in (previous, following)
    )


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
    table_lines = html_table_lines(lines)
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
        notes = marker.line_index
        if label == "join":
            previous, notes = _join_previous(lines, marker.line_index)
        else:
            previous, _ = _nearest_nonblank(lines, marker.line_index - 1, -1)
        following, _ = _nearest_nonblank(lines, marker.line_index + 1, 1)
        if previous is None or (following is None and label != "paragraph"):
            problems.append(
                f"boundary {page} ({label}) needs nonblank content on both sides"
            )
            continue
        if label != "paragraph" and any(
            PAGE_MARKER_RE.fullmatch(line_body(lines[idx]))
            for idx in (previous, following)
            if idx is not None
        ):
            problems.append(
                f"boundary {page} ({label}) needs nonblank content on both sides"
            )
            continue
        if following is None:
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
            _line_is_table_for_boundary(lines, index, table_lines)
            for index in (previous, following)
        ):
            problems.append(
                f"boundary {page} uses table-cont but both sides are not table rows"
            )
        if label == "join" and any(
            _line_is_table_for_boundary(lines, index, table_lines)
            for index in (previous, following)
        ):
            problems.append(
                f"boundary {page} cannot join a table row: joining splices two rows, "
                "or a row and a paragraph, into one line"
            )
        elif (
            label == "join"
            and notes < marker.line_index
            and _takes_no_run_on(line_body(lines[previous]))
        ):
            problems.append(
                f"boundary {page} cannot join past the page's foot notes onto a heading "
                "or another structural line"
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
    text: str,
    boundaries: dict[int, str],
    *,
    relocate_footnotes: bool = False,
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

        assert previous is not None
        newline = preferred_newline(lines, index)
        if label == "paragraph" and following is None:
            if not line_ending(lines[previous]):
                lines[previous] += newline
            del lines[previous + 1 :]
            continue
        assert following is not None
        if label == "paragraph" and PAGE_MARKER_RE.fullmatch(
            line_body(lines[previous])
        ):
            lines[previous + 1 : following] = []
            continue
        if label == "join":
            previous, notes = _join_previous(lines, index)
            assert previous is not None
            carried = lines[notes:index]
            while carried and is_blank(carried[-1]):
                carried.pop()
            lines[previous] = _remove_line_ending(lines[previous])
            lines[previous + 1 : following] = []
            if carried:
                end = previous + 1
                while (
                    end + 1 < len(lines)
                    and not is_blank(lines[end + 1])
                    and line_body(lines[end + 1]) not in break_sentinels
                ):
                    end += 1
                if not line_ending(lines[end]):
                    lines[end] += newline
                lines[end + 1 : end + 1] = [newline, *carried]
        elif label in {"paragraph", "structural"}:
            sentinel = f"\0JYUT_OCR_VERIFIED_BREAK_{marker.page}\0"
            if sentinel in text:
                raise ValueError("annotated input contains reserved boundary sentinel")
            break_sentinels.add(sentinel)
            lines[previous + 1 : following] = [sentinel + newline]
        else:  # List and table continuations retain one physical newline.
            lines[previous + 1 : following] = []

    final_lines = split_lines("".join(lines))
    if relocate_footnotes:
        final_lines = place_footnotes(final_lines, break_sentinels)
    verified_break_lines: set[int] = set()
    for index, line in enumerate(final_lines):
        if line_body(line) not in break_sentinels:
            continue
        verified_break_lines.add(index + 1)
        final_lines[index] = line_ending(line) or "\n"
    return "".join(final_lines), frozenset(verified_break_lines)


def apply_boundary_contract(
    text: str,
    boundaries: dict[int, str],
    *,
    relocate_footnotes: bool = False,
) -> str:
    """Remove page markers using only explicit manifest labels."""

    result, _ = apply_boundary_contract_with_break_lines(
        text, boundaries, relocate_footnotes=relocate_footnotes
    )
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
    source_kind: str = "pdf"


def images_inventory_sha256(pages: Sequence[PageManifestRecord]) -> str:
    """Canonical SHA-256 over (scan_page, render_sha256) for PDF-less image folders."""

    payload = "\n".join(
        f"{page.scan_page}:{page.render_sha256}" for page in pages
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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

    source_kind = root.get("source_kind", "pdf")
    if source_kind not in {"pdf", "images-dir"}:
        raise ValueError("page manifest source_kind must be 'pdf' or 'images-dir'")

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

    return PageManifestData(
        source_digest, expected_pages, tuple(pages), str(source_kind)
    )


def page_manifest_problems(
    manifest_path: Path, *, source_pdf_path: Path | None = None
) -> list[tuple[str, str]]:
    """Verify source identity, scan inventory, and every declared render hash."""

    manifest = load_page_manifest(manifest_path)
    problems: list[tuple[str, str]] = []
    if manifest.source_kind == "images-dir":
        if images_inventory_sha256(manifest.pages) != manifest.source_sha256:
            problems.append(
                (
                    "source-images-hash-mismatch",
                    "page manifest source_sha256 does not match its declared "
                    f"render inventory: {manifest_path}",
                )
            )
    else:
        if source_pdf_path is None or not source_pdf_path.is_file():
            problems.append(
                (
                    "source-pdf-missing",
                    f"selected source PDF is missing: {source_pdf_path}",
                )
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
