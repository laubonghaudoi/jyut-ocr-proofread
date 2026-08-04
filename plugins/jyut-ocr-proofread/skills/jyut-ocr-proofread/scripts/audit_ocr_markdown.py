#!/usr/bin/env python3
"""Audit annotated or final historical-Chinese OCR Markdown."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Sequence

from _ocr_markdown import (
    apply_boundary_contract_with_break_lines,
    assembly_receipt_problems,
    assembly_receipt_verified_break_lines,
    boundary_contract_problems,
    classify_all_boundaries,
    FENCE_RE,
    HEADING_RE,
    LIST_ITEM_RE,
    MarkerBoundary,
    PAGE_MARKER_CANDIDATE_RE,
    find_page_markers,
    is_blank,
    is_structural_line,
    line_body,
    load_boundary_manifest,
    marker_sequence_problems,
    page_manifest_problems,
    parse_page_spec,
    split_lines,
)


DELIMITER_PAIRS = (
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("（", "）"),
    ("［", "］"),
    ("〈", "〉"),
    ("《", "》"),
    ("【", "】"),
    ("〔", "〕"),
)
SOURCE_EXCEPTION_CODES = frozenset(
    {
        "unmatched-opening-delimiter",
        "unmatched-closing-delimiter",
        "misnested-closing-delimiter",
    }
)


class _JsonObject(list[tuple[str, object]]):
    """Retain object pairs so policy and exception duplicates cannot hide."""


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    line: int | None
    message: str
    column: int | None = None
    token: str | None = None


@dataclass(frozen=True)
class AuditResult:
    mode: str
    marker_count: int
    boundaries: tuple[MarkerBoundary, ...]
    issues: tuple[Issue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    @property
    def accepted_count(self) -> int:
        return sum(issue.severity == "accepted" for issue in self.issues)


@dataclass(frozen=True)
class SourceNotationPolicy:
    meaning: str
    scan_page: int
    note: str


@dataclass(frozen=True)
class SourceException:
    code: str
    line: int
    column: int
    delimiter: str
    text: str
    scan_page: int
    note: str


def _json_mapping(
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


def _load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_JsonObject)


def load_notation_policy(path: Path) -> SourceNotationPolicy:
    """Load a book-specific declaration that ASCII brackets are source text."""

    root = _json_mapping(
        _load_json(path),
        context="notation policy root",
        expected_keys={"ascii_square_brackets"},
    )
    policy = _json_mapping(
        root["ascii_square_brackets"],
        context="ascii_square_brackets policy",
        expected_keys={"meaning", "scan_page", "note"},
    )
    if policy["meaning"] != "source-editorial":
        raise ValueError(
            "ascii_square_brackets meaning must be 'source-editorial'"
        )
    scan_page = policy["scan_page"]
    if not isinstance(scan_page, int) or isinstance(scan_page, bool) or scan_page < 1:
        raise ValueError("notation policy scan_page must be a positive integer")
    note = policy["note"]
    if not isinstance(note, str) or not note.strip():
        raise ValueError("notation policy note must be a non-empty string")
    return SourceNotationPolicy("source-editorial", scan_page, note)


def load_source_exceptions(path: Path) -> tuple[SourceException, ...]:
    """Load exact-location exceptions for verified source punctuation."""

    root = _json_mapping(
        _load_json(path),
        context="source exceptions root",
        expected_keys={"exceptions"},
    )
    raw_exceptions = root["exceptions"]
    if not isinstance(raw_exceptions, list) or isinstance(
        raw_exceptions, _JsonObject
    ):
        raise ValueError("source exceptions must be a JSON array")

    exceptions: list[SourceException] = []
    seen: set[tuple[str, int, int, str]] = set()
    for index, raw in enumerate(raw_exceptions, 1):
        record = _json_mapping(
            raw,
            context=f"source exception {index}",
            expected_keys={
                "code",
                "line",
                "column",
                "delimiter",
                "text",
                "scan_page",
                "note",
            },
        )
        code = record["code"]
        line = record["line"]
        column = record["column"]
        delimiter = record["delimiter"]
        text = record["text"]
        scan_page = record["scan_page"]
        note = record["note"]
        if not isinstance(code, str) or code not in SOURCE_EXCEPTION_CODES:
            allowed = ", ".join(sorted(SOURCE_EXCEPTION_CODES))
            raise ValueError(
                f"source exception {index} code must be one of: {allowed}"
            )
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError(f"source exception {index} line must be positive")
        if not isinstance(column, int) or isinstance(column, bool) or column < 1:
            raise ValueError(f"source exception {index} column must be positive")
        allowed_delimiters = {
            character for pair in DELIMITER_PAIRS for character in pair
        }
        if not isinstance(delimiter, str) or delimiter not in allowed_delimiters:
            raise ValueError(
                f"source exception {index} delimiter must be one supported "
                "punctuation character"
            )
        opening_delimiters = {opening for opening, _ in DELIMITER_PAIRS}
        closing_delimiters = {closing for _, closing in DELIMITER_PAIRS}
        if code == "unmatched-opening-delimiter" and delimiter not in opening_delimiters:
            raise ValueError(
                f"source exception {index} needs an opening delimiter for {code}"
            )
        if code != "unmatched-opening-delimiter" and delimiter not in closing_delimiters:
            raise ValueError(
                f"source exception {index} needs a closing delimiter for {code}"
            )
        if not isinstance(text, str) or not text:
            raise ValueError(f"source exception {index} text must be non-empty")
        if (
            not isinstance(scan_page, int)
            or isinstance(scan_page, bool)
            or scan_page < 1
        ):
            raise ValueError(
                f"source exception {index} scan_page must be positive"
            )
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"source exception {index} note must be non-empty")
        key = (code, line, column, delimiter)
        if key in seen:
            raise ValueError(
                f"duplicate source exception for {code!r} at "
                f"line {line}, column {column}"
            )
        seen.add(key)
        exceptions.append(
            SourceException(
                code, line, column, delimiter, text, scan_page, note
            )
        )
    return tuple(exceptions)


def _protected_lines(lines: Sequence[str]) -> list[bool]:
    """Mask YAML frontmatter and fenced code, where prose rules do not apply."""

    protected = [False] * len(lines)
    if lines and line_body(lines[0]).lstrip("\ufeff") == "---":
        protected[0] = True
        for index in range(1, len(lines)):
            protected[index] = True
            if line_body(lines[index]) == "---":
                break

    open_fence: str | None = None
    for index, line in enumerate(lines):
        match = FENCE_RE.match(line_body(line))
        if open_fence is not None:
            protected[index] = True
            stripped = line_body(line).strip()
            if (
                match
                and stripped
                and set(stripped) == {open_fence[0]}
                and len(stripped) >= len(open_fence)
            ):
                open_fence = None
        elif match:
            protected[index] = True
            open_fence = match.group(1)
    return protected


def _protected_region_issues(text: str) -> list[Issue]:
    """Reject unclosed regions that would otherwise mask the remaining audit."""

    lines = split_lines(text)
    issues: list[Issue] = []
    content_start = 0
    if lines and line_body(lines[0]).lstrip("\ufeff") == "---":
        closing = next(
            (
                index
                for index in range(1, len(lines))
                if line_body(lines[index]) == "---"
            ),
            None,
        )
        if closing is None:
            return [
                Issue(
                    "error",
                    "unterminated-frontmatter",
                    1,
                    "opening YAML frontmatter has no closing '---' line",
                )
            ]
        content_start = closing + 1

    open_fence: str | None = None
    opening_line: int | None = None
    for index in range(content_start, len(lines)):
        body = line_body(lines[index])
        match = FENCE_RE.match(body)
        if open_fence is None:
            if match:
                open_fence = match.group(1)
                opening_line = index + 1
            continue
        stripped = body.strip()
        if (
            match
            and stripped
            and set(stripped) == {open_fence[0]}
            and len(stripped) >= len(open_fence)
        ):
            open_fence = None
            opening_line = None
    if open_fence is not None:
        issues.append(
            Issue(
                "error",
                "unterminated-fence",
                opening_line,
                f"opening {open_fence!r} fence has no valid closing fence",
            )
        )
    return issues


def _ordinary_line(line: str, protected: bool) -> bool:
    if protected or is_blank(line) or is_structural_line(line):
        return False
    stripped = line_body(line).strip()
    if stripped.startswith(("<", "[//]:", "[^")):
        return False
    return True


def _indent_width(value: str) -> int:
    return sum(4 if character == "\t" else 1 for character in value)


def _blank_is_inside_tight_list(
    lines: Sequence[str], previous: int, following: int
) -> bool:
    following_body = line_body(lines[following])
    following_match = LIST_ITEM_RE.match(following_body)
    if following_match:
        target_indent = _indent_width(following_match.group("indent"))
    else:
        leading = re.match(r"^[ \t]+", following_body)
        if leading is None:
            return False
        target_indent = _indent_width(leading.group(0))

    for index in range(previous, -1, -1):
        body = line_body(lines[index])
        if not body.strip():
            continue
        match = LIST_ITEM_RE.match(body)
        if match:
            if _indent_width(match.group("indent")) <= target_indent:
                return True
            continue
        if body.startswith(("  ", "\t")):
            continue
        return False
    return False


def _table_cells(value: str) -> list[str] | None:
    """Split a possible Markdown table row while respecting escaped pipes."""

    value = value.strip()
    cells = [""]
    unescaped_pipes = 0
    backslashes = 0
    for character in value:
        if character == "|" and backslashes % 2 == 0:
            cells.append("")
            unescaped_pipes += 1
        else:
            cells[-1] += character
        if character == "\\":
            backslashes += 1
        else:
            backslashes = 0
    if unescaped_pipes == 0:
        return None
    if value.startswith("|"):
        cells = cells[1:]
    trailing_backslashes = len(value[:-1]) - len(value[:-1].rstrip("\\"))
    if value.endswith("|") and trailing_backslashes % 2 == 0:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _table_blocks(
    lines: Sequence[str], protected: Sequence[bool]
) -> list[tuple[int, int, list[list[str]]]]:
    """Locate GFM-style tables, including rows with optional outer pipes."""

    blocks: list[tuple[int, int, list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        if protected[index] or protected[index + 1]:
            index += 1
            continue
        header = _table_cells(line_body(lines[index]))
        separator = _table_cells(line_body(lines[index + 1]))
        if (
            header is None
            or separator is None
            or not separator
            or not all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in separator
            )
        ):
            index += 1
            continue
        rows = [header, separator]
        end = index + 2
        while end < len(lines) and not protected[end] and not is_blank(lines[end]):
            row = _table_cells(line_body(lines[end]))
            if row is None:
                break
            rows.append(row)
            end += 1
        blocks.append((index, end, rows))
        index = end
    return blocks


def _markdown_structure_issues(
    text: str,
    scope: str,
    allowed_loose_list_break_lines: frozenset[int] = frozenset(),
) -> list[Issue]:
    lines = split_lines(text)
    protected = _protected_lines(lines)
    issues: list[Issue] = []
    table_blocks = _table_blocks(lines, protected)
    table_lines = {
        line_index
        for start, end, _ in table_blocks
        for line_index in range(start, end)
    }

    for index in range(len(lines) - 1):
        if (
            index not in table_lines
            and index + 1 not in table_lines
            and _ordinary_line(lines[index], protected[index])
            and _ordinary_line(lines[index + 1], protected[index + 1])
        ):
            issues.append(
                Issue(
                    "error",
                    "ordinary-soft-break",
                    index + 2,
                    f"{scope}: adjacent ordinary prose lines need joining or a blank line",
                )
            )

    for index, line in enumerate(lines):
        if protected[index] or not HEADING_RE.match(line_body(line)):
            continue
        if (
            index > 0
            and not is_blank(lines[index - 1])
            and not PAGE_MARKER_CANDIDATE_RE.search(line_body(lines[index - 1]))
        ):
            issues.append(
                Issue(
                    "error",
                    "heading-spacing-before",
                    index + 1,
                    f"{scope}: heading needs a blank line before it",
                )
            )
        if (
            index + 1 < len(lines)
            and not is_blank(lines[index + 1])
            and not PAGE_MARKER_CANDIDATE_RE.search(line_body(lines[index + 1]))
        ):
            issues.append(
                Issue(
                    "error",
                    "heading-spacing-after",
                    index + 1,
                    f"{scope}: heading needs a blank line after it",
                )
            )

    index = 0
    while index < len(lines):
        if not is_blank(lines[index]):
            index += 1
            continue
        blank_start = index
        while index < len(lines) and is_blank(lines[index]):
            index += 1
        previous = blank_start - 1
        following = index
        if previous < 0 or following >= len(lines):
            continue
        if (
            blank_start + 1 not in allowed_loose_list_break_lines
            and _blank_is_inside_tight_list(lines, previous, following)
        ):
            issues.append(
                Issue(
                    "error",
                    "loose-list-blank",
                    blank_start + 1,
                    f"{scope}: remove blank line inside a tight list",
                )
            )

    for start, _, rows in table_blocks:
        expected = len(rows[0])
        for offset, row in enumerate(rows):
            observed = len(row)
            if observed == expected:
                continue
            issues.append(
                Issue(
                    "error",
                    "table-column-count",
                    start + offset + 1,
                    f"{scope}: table row has {observed} cell(s); expected {expected}",
                )
            )
    return issues


def _delimiter_issues(text: str, scope: str) -> list[Issue]:
    """Report unpaired source punctuation without silently repairing it."""

    lines = split_lines(text)
    protected = _protected_lines(lines)
    issues: list[Issue] = []
    opening_to_closing = dict(DELIMITER_PAIRS)
    closing_to_opening = {
        closing: opening for opening, closing in DELIMITER_PAIRS
    }
    stack: list[tuple[str, str, int, int]] = []
    for index, line in enumerate(lines):
        if protected[index]:
            continue
        for column, character in enumerate(line_body(line), 1):
            if character in opening_to_closing:
                stack.append(
                    (
                        character,
                        opening_to_closing[character],
                        index + 1,
                        column,
                    )
                )
                continue
            if character not in closing_to_opening:
                continue
            if stack and stack[-1][1] == character:
                stack.pop()
                continue
            matching_index = next(
                (
                    stack_index
                    for stack_index in range(len(stack) - 1, -1, -1)
                    if stack[stack_index][1] == character
                ),
                None,
            )
            if matching_index is None:
                issues.append(
                    Issue(
                        "error",
                        "unmatched-closing-delimiter",
                        index + 1,
                        f"{scope}: {character!r} has no matching "
                        f"{closing_to_opening[character]!r}",
                        column=column,
                        token=character,
                    )
                )
                continue
            expected = stack[-1][1]
            issues.append(
                Issue(
                    "error",
                    "misnested-closing-delimiter",
                    index + 1,
                    f"{scope}: {character!r} closes out of order; "
                    f"current nested delimiter expects {expected!r}",
                    column=column,
                    token=character,
                )
            )
            del stack[matching_index]

    for opening, closing, line_number, column in stack:
        issues.append(
            Issue(
                "error",
                "unmatched-opening-delimiter",
                line_number,
                f"{scope}: {opening!r} has no matching {closing!r}",
                column=column,
                token=opening,
            )
        )
    return issues


def _duplicate_paragraph_issues(
    text: str, minimum_characters: int, scope: str
) -> list[Issue]:
    lines = split_lines(text)
    protected = _protected_lines(lines)
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 0

    def finish() -> None:
        nonlocal current, start_line
        if current:
            normalized = re.sub(r"\s+", "", "".join(current))
            if len(normalized) >= minimum_characters:
                paragraphs.append((start_line, normalized))
        current = []
        start_line = 0

    for index, line in enumerate(lines):
        if _ordinary_line(line, protected[index]):
            if not current:
                start_line = index + 1
            current.append(line_body(line))
        else:
            finish()
    finish()

    seen: dict[str, list[int]] = {}
    for line_number, paragraph in paragraphs:
        seen.setdefault(paragraph, []).append(line_number)

    issues: list[Issue] = []
    for locations in seen.values():
        if len(locations) > 1:
            issues.append(
                Issue(
                    "warning",
                    "duplicate-long-paragraph",
                    locations[1],
                    f"{scope}: exact long paragraph repeats at lines "
                    + ", ".join(map(str, locations)),
                )
            )
    return issues


def _residue_issues(
    lines: Sequence[str], mode: str, ascii_brackets_are_source: bool
) -> list[Issue]:
    issues: list[Issue] = []
    protected = _protected_lines(lines)
    for index, line in enumerate(lines):
        body = line_body(line)
        line_number = index + 1
        if body.endswith((" ", "\t")):
            issues.append(
                Issue(
                    "error",
                    "trailing-whitespace",
                    line_number,
                    "line ends with horizontal whitespace",
                )
            )
        if re.search(r" +\t", body):
            issues.append(
                Issue(
                    "error",
                    "space-before-tab",
                    line_number,
                    "spaces appear before a tab character",
                )
            )
        if "\ufffd" in body:
            issues.append(
                Issue(
                    "error",
                    "replacement-character",
                    line_number,
                    "Unicode replacement character remains",
                )
            )
        if protected[index]:
            continue
        if re.search(r"gjcool\s+OCR", body, re.IGNORECASE):
            issues.append(
                Issue("error", "ocr-tool-heading", line_number, "GJ.cool OCR heading remains")
            )
        if "此頁OCR無有效文字" in body:
            issues.append(
                Issue(
                    "error",
                    "ocr-empty-page-notice",
                    line_number,
                    "OCR empty-page notice remains",
                )
            )
        without_links = re.sub(r"!?\[[^\]\n]*\]\([^\)\n]*\)", "", body)
        if not ascii_brackets_are_source and not re.match(
            r"^\[[^\]]+\]:", without_links
        ) and (
            "[" in without_links or "]" in without_links
        ):
            issues.append(
                Issue(
                    "error",
                    "low-confidence-bracket",
                    line_number,
                    "square bracket remains; verify whether it is OCR confidence markup",
                )
            )
        if "∵" in body or "≡" in body:
            symbols = "".join(symbol for symbol in ("∵", "≡") if symbol in body)
            issues.append(
                Issue(
                    "warning",
                    "suspicious-ocr-symbol",
                    line_number,
                    f"verify suspicious OCR symbol(s): {symbols}",
                )
            )
        if mode == "final" and "<!--" in body and not PAGE_MARKER_CANDIDATE_RE.search(
            body
        ):
            issues.append(
                Issue(
                    "warning",
                    "html-comment-in-final",
                    line_number,
                    "HTML comment remains in final Markdown",
                )
            )
    return issues


def _apply_source_exceptions(
    issues: Sequence[Issue], text: str, exceptions: Sequence[SourceException]
) -> list[Issue]:
    """Replace exact, current punctuation issues with auditable acceptances."""

    if not exceptions:
        return list(issues)
    lines = split_lines(text)
    suppressed: set[int] = set()
    additions: list[Issue] = []
    for exception in exceptions:
        line_matches = (
            exception.line <= len(lines)
            and line_body(lines[exception.line - 1]) == exception.text
        )
        delimiter_matches = (
            exception.column <= len(exception.text)
            and exception.text[exception.column - 1] == exception.delimiter
        )
        if not line_matches or not delimiter_matches:
            additions.append(
                Issue(
                    "error",
                    "stale-source-exception",
                    exception.line,
                    f"{exception.code}: exact line, column, or delimiter no longer "
                    "matches the ledger",
                    column=exception.column,
                    token=exception.delimiter,
                )
            )
            continue
        matches = [
            index
            for index, issue in enumerate(issues)
            if issue.code == exception.code
            and issue.line == exception.line
            and issue.column == exception.column
            and issue.token == exception.delimiter
        ]
        if len(matches) != 1:
            additions.append(
                Issue(
                    "error",
                    "stale-source-exception",
                    exception.line,
                    f"{exception.code}: expected exactly one current issue for "
                    f"{exception.delimiter!r} at column {exception.column}, found "
                    f"{len(matches)}",
                    column=exception.column,
                    token=exception.delimiter,
                )
            )
            continue
        suppressed.update(matches)
        additions.append(
            Issue(
                "accepted",
                "source-exception",
                exception.line,
                f"accepted {exception.code} for {exception.delimiter!r} at column "
                f"{exception.column} as printed at scan page "
                f"{exception.scan_page}: {exception.note}",
                column=exception.column,
                token=exception.delimiter,
            )
        )
    return [issue for index, issue in enumerate(issues) if index not in suppressed] + additions


def audit_text(
    text: str,
    *,
    mode: str = "auto",
    expected_count: int | None = None,
    skip_pages: set[int] | None = None,
    duplicate_min_characters: int = 120,
    boundary_contract: dict[int, str] | None = None,
    notation_policy: SourceNotationPolicy | None = None,
    source_exceptions: Sequence[SourceException] = (),
    allowed_loose_list_break_lines: frozenset[int] = frozenset(),
) -> AuditResult:
    """Audit Markdown text without relying on Git tracking state."""

    if mode not in {"auto", "annotated", "final"}:
        raise ValueError(f"unknown mode: {mode}")
    if duplicate_min_characters < 1:
        raise ValueError("duplicate paragraph minimum must be at least 1")

    lines = split_lines(text)
    verified_break_lines = set(allowed_loose_list_break_lines)
    markers, malformed = find_page_markers(lines)
    resolved_mode = (
        "annotated" if mode == "auto" and (markers or malformed) else "final"
    )
    if mode != "auto":
        resolved_mode = mode

    issues = _residue_issues(lines, resolved_mode, notation_policy is not None)
    issues.extend(_protected_region_issues(text))
    if not text:
        issues.append(
            Issue("error", "empty-file", None, "OCR Markdown file is empty")
        )
    elif not text.endswith(("\n", "\r")):
        issues.append(
            Issue(
                "error",
                "missing-final-newline",
                len(lines),
                "file must end with one newline",
            )
        )
    if lines and is_blank(lines[-1]):
        issues.append(
            Issue(
                "error",
                "extra-blank-line-at-eof",
                len(lines),
                "remove blank line at end of file",
            )
        )
    if notation_policy is not None:
        protected = _protected_lines(lines)
        bracket_lines = sum(
            not protected[index]
            and any(
                character in re.sub(
                    r"!?\[[^\]\n]*\]\([^\)\n]*\)", "", line_body(line)
                )
                for character in "[]"
            )
            for index, line in enumerate(lines)
        )
        issues.append(
            Issue(
                "accepted",
                "source-notation-policy",
                None,
                f"ASCII square brackets are source editorial notation on "
                f"{bracket_lines} line(s), declared from scan page "
                f"{notation_policy.scan_page}: {notation_policy.note}",
            )
        )
        if resolved_mode != "final":
            issues.append(
                Issue(
                    "error",
                    "notation-policy-requires-final",
                    None,
                    "source notation policy is a final-mode classification; "
                    "resolve annotated OCR confidence candidates first",
                )
            )
    boundaries: list[MarkerBoundary] = []
    if resolved_mode == "annotated":
        if not markers:
            issues.append(
                Issue(
                    "error",
                    "missing-page-markers",
                    None,
                    "annotated mode requires canonical page markers",
                )
            )
        for line_number, body in malformed:
            issues.append(
                Issue(
                    "error",
                    "malformed-page-marker",
                    line_number,
                    f"use canonical '<!-- page_001 -->' syntax: {body!r}",
                )
            )
        for problem in marker_sequence_problems(
            markers, expected_count, set(skip_pages or ())
        ):
            issues.append(Issue("error", "page-marker-sequence", None, problem))

        boundaries = classify_all_boundaries(lines, markers)
        if boundary_contract is None:
            issues.append(
                Issue(
                    "warning",
                    "unresolved-boundaries",
                    None,
                    "boundary suggestions are heuristic; provide --boundary-manifest "
                    "for an exact virtual-final audit",
                )
            )
            structural_scope = "annotated Markdown within page boundaries"
            structural_text = text
        else:
            contract_problems = boundary_contract_problems(
                lines, markers, boundary_contract
            )
            for problem in contract_problems:
                issues.append(
                    Issue("error", "boundary-contract", None, problem)
                )
            if malformed or contract_problems:
                structural_scope = "annotated Markdown within page boundaries"
                structural_text = text
            else:
                structural_scope = "virtual final from explicit boundary manifest"
                structural_text, virtual_break_lines = (
                    apply_boundary_contract_with_break_lines(text, boundary_contract)
                )
                verified_break_lines.update(virtual_break_lines)
    else:
        for marker in markers:
            issues.append(
                Issue(
                    "error",
                    "page-marker-in-final",
                    marker.line_number,
                    f"page marker {marker.page} remains in final Markdown",
                )
            )
        for line_number, body in malformed:
            issues.append(
                Issue(
                    "error",
                    "page-marker-in-final",
                    line_number,
                    f"page-marker-like comment remains: {body!r}",
                )
            )
        if expected_count is not None or skip_pages:
            issues.append(
                Issue(
                    "warning",
                    "page-expectation-ignored",
                    None,
                    "page expectations cannot be verified in final mode",
                )
            )
        if boundary_contract is not None:
            issues.append(
                Issue(
                    "warning",
                    "boundary-contract-ignored",
                    None,
                    "boundary manifest is ignored in final mode",
                )
            )
        structural_scope = "final Markdown"
        structural_text = text

    issues.extend(
        _markdown_structure_issues(
            structural_text,
            structural_scope,
            frozenset(verified_break_lines),
        )
    )
    if resolved_mode == "final":
        issues.extend(_delimiter_issues(structural_text, structural_scope))
    issues.extend(
        _duplicate_paragraph_issues(
            structural_text, duplicate_min_characters, structural_scope
        )
    )
    if source_exceptions and resolved_mode != "final":
        issues.append(
            Issue(
                "error",
                "source-exceptions-require-final",
                None,
                "exact-location source exceptions can only be applied in final mode",
            )
        )
    else:
        issues = _apply_source_exceptions(
            issues, structural_text, source_exceptions
        )
    return AuditResult(
        mode=resolved_mode,
        marker_count=len(markers),
        boundaries=tuple(boundaries),
        issues=tuple(issues),
    )


def _read_utf8(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit annotated or final Jyut OCR Markdown without Git dependencies."
    )
    parser.add_argument("target", type=Path, help="Markdown file to audit")
    parser.add_argument(
        "--mode",
        choices=("auto", "annotated", "final"),
        default="auto",
        help="auto detects annotated files from page-marker comments",
    )
    parser.add_argument(
        "--expected-count",
        "--expected-page-count",
        "--expected-pages",
        dest="expected_count",
        type=int,
        help="total PDF page count (before --skip-pages exclusions)",
    )
    parser.add_argument(
        "--skip-pages",
        action="append",
        metavar="SPEC",
        help=(
            "annotated-mode pages intentionally without markers, for example "
            "17 or 2,5-7; release manifests must also seal marker_expected=false"
        ),
    )
    parser.add_argument(
        "--boundary-manifest",
        type=Path,
        help="explicit JSON boundary contract for an exact annotated-to-final audit",
    )
    parser.add_argument(
        "--duplicate-min-chars",
        type=int,
        default=120,
        metavar="N",
        help="minimum normalized paragraph length for duplicate warnings (default: 120)",
    )
    parser.add_argument(
        "--notation-policy",
        type=Path,
        help=(
            "book-specific JSON declaration that ASCII square brackets are "
            "source editorial notation rather than OCR confidence markup"
        ),
    )
    parser.add_argument(
        "--source-exceptions",
        type=Path,
        help=(
            "duplicate-safe JSON ledger for exact-location, PDF-verified unmatched "
            "source punctuation; final mode only"
        ),
    )
    parser.add_argument(
        "--assembly-receipt",
        type=Path,
        help=(
            "SHA-256 receipt emitted by finalize_page_markers.py; fail if the "
            "final output or any recorded input changed"
        ),
    )
    parser.add_argument(
        "--page-manifest",
        type=Path,
        help=(
            "page/render provenance manifest; use together with --source-pdf to "
            "rehash the selected PDF and every declared render; with an assembly "
            "receipt, also verify sealed annotated marker coverage"
        ),
    )
    parser.add_argument(
        "--source-pdf",
        type=Path,
        help=(
            "current path of the source PDF whose SHA-256 must match "
            "--page-manifest; use together with --page-manifest"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.page_manifest is None) != (args.source_pdf is None):
        parser.error("--page-manifest and --source-pdf must be used together")
    try:
        skip_pages = parse_page_spec(args.skip_pages)
        boundary_contract = (
            load_boundary_manifest(args.boundary_manifest)
            if args.boundary_manifest is not None
            else None
        )
        notation_policy = (
            load_notation_policy(args.notation_policy)
            if args.notation_policy is not None
            else None
        )
        source_exceptions = (
            load_source_exceptions(args.source_exceptions)
            if args.source_exceptions is not None
            else ()
        )
        text = _read_utf8(args.target)
        preview_markers, preview_malformed = find_page_markers(split_lines(text))
        receipt_target_is_final = args.mode == "final" or (
            args.mode == "auto" and not preview_markers and not preview_malformed
        )
        verified_break_lines = (
            assembly_receipt_verified_break_lines(args.assembly_receipt)
            if args.assembly_receipt is not None and receipt_target_is_final
            else frozenset()
        )
        result = audit_text(
            text,
            mode=args.mode,
            expected_count=args.expected_count,
            skip_pages=skip_pages,
            duplicate_min_characters=args.duplicate_min_chars,
            boundary_contract=boundary_contract,
            notation_policy=notation_policy,
            source_exceptions=source_exceptions,
            allowed_loose_list_break_lines=verified_break_lines,
        )
        release_issues: list[Issue] = []
        if args.page_manifest is not None:
            assert args.source_pdf is not None
            release_issues.extend(
                Issue("error", code, None, message)
                for code, message in page_manifest_problems(
                    args.page_manifest,
                    source_pdf_path=args.source_pdf,
                )
            )
        if args.assembly_receipt is not None:
            if result.mode != "final":
                release_issues.append(
                    Issue(
                        "error",
                        "assembly-receipt-requires-final",
                        None,
                        "assembly receipts can only be verified in final mode",
                    )
                )
            else:
                release_issues.extend(
                    Issue("error", code, None, message)
                    for code, message in assembly_receipt_problems(
                        args.assembly_receipt,
                        expected_output_path=args.target,
                        required_input_paths=tuple(
                            path
                            for path in (
                                args.notation_policy,
                                args.source_exceptions,
                                args.page_manifest,
                            )
                            if path is not None
                        ),
                        page_manifest_path=args.page_manifest,
                    )
                )
        if release_issues:
            result = AuditResult(
                mode=result.mode,
                marker_count=result.marker_count,
                boundaries=result.boundaries,
                issues=(*result.issues, *release_issues),
            )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    print(f"MODE {result.mode}")
    print(f"PAGE MARKERS {result.marker_count}")
    for boundary in result.boundaries:
        marker = boundary.marker
        print(
            "BOUNDARY-SUGGESTION "
            f"line={marker.line_number} page={marker.page} suggested={boundary.kind} "
            f"previous={boundary.previous_line or '-'} next={boundary.next_line or '-'} "
            "authority=heuristic-only"
        )
    for issue in result.issues:
        location = str(args.target)
        if issue.line is not None:
            location += f":{issue.line}"
            if issue.column is not None:
                location += f":{issue.column}"
        print(
            f"{issue.severity.upper()} {location} [{issue.code}] {issue.message}"
        )
    print(
        f"SUMMARY errors={result.error_count} warnings={result.warning_count} "
        f"accepted={result.accepted_count} markers={result.marker_count}"
    )
    return 1 if result.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
