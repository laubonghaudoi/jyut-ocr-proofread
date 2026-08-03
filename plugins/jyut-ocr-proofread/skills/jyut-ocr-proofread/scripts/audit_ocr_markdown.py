#!/usr/bin/env python3
"""Audit annotated or final historical-Chinese OCR Markdown."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Sequence

from _ocr_markdown import (
    apply_boundary_contract,
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
    parse_page_spec,
    split_lines,
)


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    line: int | None
    message: str


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
            if match and match.group(1).startswith(open_fence[0]):
                open_fence = None
        elif match:
            protected[index] = True
            open_fence = match.group(1)
    return protected


def _ordinary_line(line: str, protected: bool) -> bool:
    if protected or is_blank(line) or is_structural_line(line):
        return False
    stripped = line_body(line).strip()
    if stripped.startswith(("<", "[//]:", "[^")):
        return False
    return True


def _markdown_structure_issues(text: str, scope: str) -> list[Issue]:
    lines = split_lines(text)
    protected = _protected_lines(lines)
    issues: list[Issue] = []

    for index in range(len(lines) - 1):
        if _ordinary_line(lines[index], protected[index]) and _ordinary_line(
            lines[index + 1], protected[index + 1]
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
        previous_match = LIST_ITEM_RE.match(line_body(lines[previous]))
        following_match = LIST_ITEM_RE.match(line_body(lines[following]))
        if (
            previous_match
            and following_match
            and previous_match.group("indent") == following_match.group("indent")
        ):
            issues.append(
                Issue(
                    "error",
                    "loose-list-blank",
                    blank_start + 1,
                    f"{scope}: remove blank line between consecutive tight-list items",
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


def _residue_issues(lines: Sequence[str], mode: str) -> list[Issue]:
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
        if not re.match(r"^\[[^\]]+\]:", without_links) and (
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


def audit_text(
    text: str,
    *,
    mode: str = "auto",
    expected_count: int | None = None,
    skip_pages: set[int] | None = None,
    duplicate_min_characters: int = 120,
    boundary_contract: dict[int, str] | None = None,
) -> AuditResult:
    """Audit Markdown text without relying on Git tracking state."""

    if mode not in {"auto", "annotated", "final"}:
        raise ValueError(f"unknown mode: {mode}")
    if duplicate_min_characters < 1:
        raise ValueError("duplicate paragraph minimum must be at least 1")

    lines = split_lines(text)
    markers, malformed = find_page_markers(lines)
    resolved_mode = (
        "annotated" if mode == "auto" and (markers or malformed) else "final"
    )
    if mode != "auto":
        resolved_mode = mode

    issues = _residue_issues(lines, resolved_mode)
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
                structural_text = apply_boundary_contract(text, boundary_contract)
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

    issues.extend(_markdown_structure_issues(structural_text, structural_scope))
    issues.extend(
        _duplicate_paragraph_issues(
            structural_text, duplicate_min_characters, structural_scope
        )
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
        help="pages intentionally without markers, for example 17 or 2,5-7",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        skip_pages = parse_page_spec(args.skip_pages)
        boundary_contract = (
            load_boundary_manifest(args.boundary_manifest)
            if args.boundary_manifest is not None
            else None
        )
        text = _read_utf8(args.target)
        result = audit_text(
            text,
            mode=args.mode,
            expected_count=args.expected_count,
            skip_pages=skip_pages,
            duplicate_min_characters=args.duplicate_min_chars,
            boundary_contract=boundary_contract,
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
        print(
            f"{issue.severity.upper()} {location} [{issue.code}] {issue.message}"
        )
    print(
        f"SUMMARY errors={result.error_count} warnings={result.warning_count} "
        f"markers={result.marker_count}"
    )
    return 1 if result.error_count else 0


if __name__ == "__main__":
    sys.exit(main())
