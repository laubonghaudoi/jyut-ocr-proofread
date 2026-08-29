#!/usr/bin/env python3
"""Build a deterministic, page-aware inventory of source notation.

This script classifies locations only.  It does not decide whether a glyph is
authorized by a book's editorial policy; that decision remains in the sealed
notation authority/policy reviewed against the source scans.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import string
import sys
from pathlib import Path
from typing import Sequence

from _ocr_markdown import (
    find_page_markers,
    mask_nonrendered_markdown,
    paths_refer_to_same_file,
    terminal_title_note_heading_marker,
    title_note_definition_marker,
)


WHITE_SQUARE_RE = re.compile(r"□+")
EXPLICIT_UNCERTAIN_RE = re.compile(r"〔[?？]〕")
ELLIPSIS_PAIR_RE = re.compile(r"……")
KEBAB_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
REFERENCE_DEFINITION_RE = re.compile(
    r"^ {0,3}\[(?P<label>(?:\\.|[^\]\\])+)\]:[ \t]*(?P<rest>.*)$"
)

SINGLE_GLYPHS = {
    "ascii-square-bracket-open": "[",
    "ascii-square-bracket-close": "]",
    "fullwidth-square-bracket-open": "［",
    "fullwidth-square-bracket-close": "］",
    "lenticular-bracket-open": "〔",
    "lenticular-bracket-close": "〕",
    "angle-bracket-open": "〈",
    "angle-bracket-close": "〉",
    "rectangle": "▭",
}
STAR_KINDS = {"heading-star-reference", "starred-note-definition"}
BUILTIN_KINDS = {
    *SINGLE_GLYPHS,
    "white-square",
    "explicit-uncertain-marker",
    "ellipsis-pair",
    *STAR_KINDS,
}
BUILTIN_LITERALS = {
    *SINGLE_GLYPHS.values(),
    "□",
    "〔?〕",
    "〔？〕",
    "……",
    "*",
    "**",
}
TOOL_PATH = Path(__file__).resolve()
SHARED_HELPER_PATH = TOOL_PATH.with_name("_ocr_markdown.py")
RELEASE_VALIDATOR_PATH = TOOL_PATH.with_name("validate_release_evidence.py")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_inventory_profile(path: Path) -> tuple[list[tuple[str, str]], str]:
    """Load one strict literal-only notation profile and seal its bytes."""

    raw = path.read_bytes()
    try:
        profile = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as error:
        raise ValueError(
            "invalid inventory profile JSON at line "
            f"{error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(profile, dict):
        raise ValueError("inventory profile root must be a JSON object")
    expected_root_fields = {"schema_version", "artifact_type", "symbols"}
    if set(profile) != expected_root_fields:
        missing = sorted(expected_root_fields - set(profile))
        unknown = sorted(set(profile) - expected_root_fields)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError("invalid inventory profile fields: " + "; ".join(details))
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise ValueError("inventory profile schema_version must equal 1")
    if profile["artifact_type"] != "ocr-notation-inventory-profile":
        raise ValueError(
            "inventory profile artifact_type must equal "
            "'ocr-notation-inventory-profile'"
        )
    raw_symbols = profile["symbols"]
    if not isinstance(raw_symbols, list):
        raise ValueError("inventory profile symbols must be an array")

    symbols: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    seen_literals: set[str] = set()
    for index, record in enumerate(raw_symbols):
        label = f"inventory profile symbols[{index}]"
        if not isinstance(record, dict) or set(record) != {"id", "literal"}:
            raise ValueError(f"{label} must contain exactly id and literal")
        symbol_id = record["id"]
        literal = record["literal"]
        if not isinstance(symbol_id, str) or not KEBAB_ID_RE.fullmatch(symbol_id):
            raise ValueError(f"{label}.id must be kebab-case")
        if symbol_id in BUILTIN_KINDS:
            raise ValueError(f"{label}.id collides with built-in category {symbol_id!r}")
        if symbol_id in seen_ids:
            raise ValueError(f"{label}.id is duplicated: {symbol_id!r}")
        if not isinstance(literal, str) or not literal:
            raise ValueError(f"{label}.literal must be a non-empty string")
        if "\n" in literal or "\r" in literal:
            raise ValueError(f"{label}.literal must not contain a newline")
        if literal in BUILTIN_LITERALS:
            raise ValueError(f"{label}.literal duplicates built-in literal {literal!r}")
        if literal in seen_literals:
            raise ValueError(f"{label}.literal is duplicated: {literal!r}")
        seen_ids.add(symbol_id)
        seen_literals.add(literal)
        symbols.append((symbol_id, literal))
    return symbols, hashlib.sha256(raw).hexdigest()


def _line_body(raw_line: str) -> str:
    return raw_line.rstrip("\r\n")


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int | None:
    """Return the inclusive end of a same-line balanced Markdown construct."""

    if start >= len(text) or text[start] != opening:
        return None
    depth = 0
    index = start
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def normalize_reference_label(label: str) -> str:
    """Return a conservative CommonMark-style normalized reference label."""

    unescaped_characters: list[str] = []
    index = 0
    while index < len(label):
        if (
            label[index] == "\\"
            and index + 1 < len(label)
            and label[index + 1] in string.punctuation
        ):
            unescaped_characters.append(label[index + 1])
            index += 2
            continue
        unescaped_characters.append(label[index])
        index += 1
    unescaped = "".join(unescaped_characters)
    return re.sub(r"\s+", " ", unescaped.strip()).casefold()


def _valid_optional_link_title(value: str) -> bool:
    if not value:
        return True
    opening = value[0]
    closing = {"\"": "\"", "'": "'", "(": ")"}.get(opening)
    if closing is None or len(value) < 2 or value[-1] != closing:
        return False
    cursor = 1
    while cursor < len(value) - 1:
        character = value[cursor]
        if (
            character == "\\"
            and cursor + 1 < len(value) - 1
            and value[cursor + 1] in string.punctuation
        ):
            cursor += 2
            continue
        if character in "\r\n" or character == closing:
            return False
        cursor += 1
    return True


def valid_link_destination_and_title(value: str, *, allow_empty: bool) -> bool:
    """Conservatively recognize a same-line CommonMark destination/title."""

    content = value.strip()
    if not content:
        return allow_empty
    if content.startswith("<"):
        cursor = 1
        destination_end: int | None = None
        while cursor < len(content):
            character = content[cursor]
            if (
                character == "\\"
                and cursor + 1 < len(content)
                and content[cursor + 1] in string.punctuation
            ):
                cursor += 2
                continue
            if character in "\r\n<":
                return False
            if character == ">":
                destination_end = cursor + 1
                break
            cursor += 1
        if destination_end is None:
            return False
    else:
        cursor = 0
        depth = 0
        while cursor < len(content) and not content[cursor].isspace():
            character = content[cursor]
            if ord(character) < 32 or ord(character) == 127:
                return False
            if (
                character == "\\"
                and cursor + 1 < len(content)
                and content[cursor + 1] in string.punctuation
            ):
                cursor += 2
                continue
            if character == "(":
                depth += 1
                if depth > 32:
                    return False
            elif character == ")":
                if depth == 0:
                    return False
                depth -= 1
            cursor += 1
        if cursor == 0 or depth != 0:
            return False
        destination_end = cursor

    remainder = content[destination_end:]
    if not remainder:
        return True
    if not remainder[0].isspace():
        return False
    return _valid_optional_link_title(remainder.strip())


def reference_definition_label(body: str) -> str | None:
    match = REFERENCE_DEFINITION_RE.fullmatch(body)
    if match is None or not valid_link_destination_and_title(
        match.group("rest"), allow_empty=False
    ):
        return None
    return normalize_reference_label(match.group("label"))


def reference_definition_labels(
    lines: Sequence[str],
) -> frozenset[str]:
    """Collect labels whose definition lines are excluded from inventory."""

    labels: set[str] = set()
    for raw_line in lines:
        label = reference_definition_label(_line_body(raw_line))
        if label is not None:
            labels.add(label)
    return frozenset(labels)


def mask_inline_links_and_images(
    line: str, *, reference_labels: frozenset[str]
) -> str:
    """Blank inline Markdown/HTML syntax without changing source columns."""

    masked = list(line)
    index = 0
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        start = index
        if line[index] == "!" and index + 1 < len(line) and line[index + 1] == "[":
            label_start = index + 1
        elif line[index] == "[":
            label_start = index
        else:
            index += 1
            continue
        label_end = _balanced_end(line, label_start, "[", "]")
        if label_end is None:
            index = label_start + 1
            continue
        suffix_start = label_end + 1
        construct_end: int | None = None
        if suffix_start < len(line) and line[suffix_start] == "(":
            suffix_end = _balanced_end(line, suffix_start, "(", ")")
            if suffix_end is not None and valid_link_destination_and_title(
                line[suffix_start + 1 : suffix_end], allow_empty=True
            ):
                construct_end = suffix_end
        elif suffix_start < len(line) and line[suffix_start] == "[":
            suffix_end = _balanced_end(line, suffix_start, "[", "]")
            if suffix_end is not None:
                explicit_label = line[suffix_start + 1 : suffix_end]
                effective_label = (
                    line[label_start + 1 : label_end]
                    if not explicit_label
                    else explicit_label
                )
                if normalize_reference_label(effective_label) in reference_labels:
                    construct_end = suffix_end
        elif normalize_reference_label(line[label_start + 1 : label_end]) in reference_labels:
            # Shortcut reference link/image: ``[label]`` or ``![label]``.
            # Only protect it when the document actually defines that label;
            # unmatched square brackets remain OCR/source-text candidates.
            construct_end = label_end
        if construct_end is None:
            index = label_end + 1
            continue
        for protected_index in range(start, construct_end + 1):
            masked[protected_index] = " "
        index = construct_end + 1
    return "".join(masked)


def occurrence(
    *,
    kind: str,
    token: str,
    line_number: int,
    column: int,
    scan_page: int | None,
    text: str,
    count: int = 1,
) -> dict[str, object]:
    return {
        "kind": kind,
        "token": token,
        "count": count,
        "scan_page": scan_page,
        "line": line_number,
        "column": column,
        "text": text,
    }


def build_inventory(
    target: Path,
    *,
    authority: Path | None,
    inventory_profile: Path | None,
    inventory_profile_sha256: str | None,
    custom_symbols: Sequence[tuple[str, str]],
    require_markers: bool,
    include_starred_title_notes: bool,
) -> tuple[dict[str, object], list[str]]:
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    syntax_lines = mask_nonrendered_markdown(lines, preserve_page_markers=True)
    reference_labels = reference_definition_labels(syntax_lines)
    markers, malformed = find_page_markers(syntax_lines)
    errors = [
        f"line {line_number}: malformed page marker: {body!r}"
        for line_number, body in malformed
    ]
    if require_markers and not markers:
        errors.append("no canonical page markers found")
    marker_pages = [marker.page for marker in markers]
    if any(right <= left for left, right in zip(marker_pages, marker_pages[1:])):
        errors.append("canonical page markers must be unique and strictly increasing")

    marker_by_line = {marker.line_number: marker.page for marker in markers}
    scan_page: int | None = None
    counts = {kind: 0 for kind in SINGLE_GLYPHS}
    counts.update(
        {
            "white-square": 0,
            "explicit-uncertain-marker": 0,
            "ellipsis-pair": 0,
        }
    )
    if include_starred_title_notes:
        counts.update({kind: 0 for kind in STAR_KINDS})
    counts.update({symbol_id: 0 for symbol_id, _literal in custom_symbols})
    occurrences: list[dict[str, object]] = []

    for line_number, (raw_line, syntax_line) in enumerate(zip(lines, syntax_lines), 1):
        if line_number in marker_by_line:
            scan_page = marker_by_line[line_number]
            continue
        source_line = _line_body(raw_line)
        line = _line_body(syntax_line)
        if not line.strip() or reference_definition_label(line) is not None:
            continue
        inventory_line = mask_inline_links_and_images(
            line, reference_labels=reference_labels
        )

        if include_starred_title_notes:
            heading_marker = terminal_title_note_heading_marker(inventory_line)
            if heading_marker is not None:
                stars, star_column = heading_marker
                counts["heading-star-reference"] += len(stars)
                occurrences.append(
                    occurrence(
                        kind="heading-star-reference",
                        token=stars,
                        count=len(stars),
                        line_number=line_number,
                        column=star_column + 1,
                        scan_page=scan_page,
                        text=source_line,
                    )
                )
            stars = title_note_definition_marker(inventory_line)
            if stars is not None:
                counts["starred-note-definition"] += len(stars)
                occurrences.append(
                    occurrence(
                        kind="starred-note-definition",
                        token=stars,
                        count=len(stars),
                        line_number=line_number,
                        column=inventory_line.find(stars) + 1,
                        scan_page=scan_page,
                        text=source_line,
                    )
                )

        for match in WHITE_SQUARE_RE.finditer(inventory_line):
            count = len(match.group(0))
            counts["white-square"] += count
            occurrences.append(
                occurrence(
                    kind="white-square",
                    token=match.group(0),
                    count=count,
                    line_number=line_number,
                    column=match.start() + 1,
                    scan_page=scan_page,
                    text=source_line,
                )
            )

        for match in EXPLICIT_UNCERTAIN_RE.finditer(inventory_line):
            counts["explicit-uncertain-marker"] += 1
            occurrences.append(
                occurrence(
                    kind="explicit-uncertain-marker",
                    token=match.group(0),
                    line_number=line_number,
                    column=match.start() + 1,
                    scan_page=scan_page,
                    text=source_line,
                )
            )

        for match in ELLIPSIS_PAIR_RE.finditer(inventory_line):
            counts["ellipsis-pair"] += 1
            occurrences.append(
                occurrence(
                    kind="ellipsis-pair",
                    token=match.group(0),
                    line_number=line_number,
                    column=match.start() + 1,
                    scan_page=scan_page,
                    text=source_line,
                )
            )

        for kind, glyph in SINGLE_GLYPHS.items():
            start = 0
            while True:
                index = inventory_line.find(glyph, start)
                if index < 0:
                    break
                counts[kind] += 1
                occurrences.append(
                    occurrence(
                        kind=kind,
                        token=glyph,
                        line_number=line_number,
                        column=index + 1,
                        scan_page=scan_page,
                        text=source_line,
                    )
                )
                start = index + len(glyph)

        for kind, literal in custom_symbols:
            start = 0
            while True:
                index = inventory_line.find(literal, start)
                if index < 0:
                    break
                counts[kind] += 1
                occurrences.append(
                    occurrence(
                        kind=kind,
                        token=literal,
                        line_number=line_number,
                        column=index + 1,
                        scan_page=scan_page,
                        text=source_line,
                    )
                )
                start = index + len(literal)

    occurrences.sort(
        key=lambda item: (
            int(item["line"]),
            int(item["column"]),
            str(item["kind"]),
        )
    )
    count_units = {
        kind: (
            "asterisk-characters"
            if kind in STAR_KINDS
            else "literal-tokens"
            if kind in {symbol_id for symbol_id, _literal in custom_symbols}
            else "glyphs"
            if kind in {*SINGLE_GLYPHS, "white-square"}
            else "marker-tokens"
        )
        for kind in counts
    }
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "ocr-notation-inventory",
        "tool": {"name": TOOL_PATH.name, "sha256": sha256_file(TOOL_PATH)},
        "tool_dependencies": [
            {"name": SHARED_HELPER_PATH.name, "sha256": sha256_file(SHARED_HELPER_PATH)},
            {
                "name": RELEASE_VALIDATOR_PATH.name,
                "sha256": sha256_file(RELEASE_VALIDATOR_PATH),
            },
        ],
        "invocation_profile": {
            "require_markers": require_markers,
            "authority_provided": authority is not None,
            "inventory_profile_provided": inventory_profile is not None,
            "include_starred_title_notes": include_starred_title_notes,
        },
        "status": "pass" if not errors else "fail",
        "target": {
            "path": str(target),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "authority": (
            {"path": str(authority), "sha256": sha256_file(authority)}
            if authority is not None
            else None
        ),
        "inventory_profile": (
            {"path": str(inventory_profile), "sha256": inventory_profile_sha256}
            if inventory_profile is not None
            else None
        ),
        "page_marker_count": len(markers),
        "scan_range": (
            {"start": marker_pages[0], "end": marker_pages[-1]}
            if markers
            else None
        ),
        "counts": dict(sorted(counts.items())),
        "count_units": dict(sorted(count_units.items())),
        "occurrence_record_count": len(occurrences),
        "occurrence_semantics": (
            "one record per category-specific matched location; categories may "
            "overlap, and each record count is the number of units at that location"
        ),
        "occurrences": occurrences,
        "errors": errors,
    }
    return payload, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory source/editorial notation with current target hashes and "
            "page-marker provenance."
        )
    )
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--authority",
        type=Path,
        help="sealed notation authority/policy whose bytes support classification",
    )
    parser.add_argument(
        "--require-markers",
        action="store_true",
        help="fail when the annotated target has no canonical page markers",
    )
    parser.add_argument(
        "--inventory-profile",
        type=Path,
        help="strict literal-only JSON profile defining extra notation categories",
    )
    parser.add_argument(
        "--include-starred-title-notes",
        action="store_true",
        help="inventory terminal title stars and starred title-note definitions",
    )
    parser.add_argument(
        "--json-report",
        "--output",
        dest="json_report",
        type=Path,
        help="write the complete deterministic JSON inventory",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.target.is_file():
            raise ValueError(f"target is not a file: {args.target}")
        if args.authority is not None and not args.authority.is_file():
            raise ValueError(f"authority is not a file: {args.authority}")
        if args.authority is not None and paths_refer_to_same_file(
            args.authority, args.target
        ):
            raise ValueError("--authority must not alias target")
        if args.inventory_profile is not None and not args.inventory_profile.is_file():
            raise ValueError(f"inventory profile is not a file: {args.inventory_profile}")
        if args.inventory_profile is not None:
            if paths_refer_to_same_file(args.inventory_profile, args.target):
                raise ValueError("--inventory-profile must not alias target")
            if args.authority is not None and paths_refer_to_same_file(
                args.inventory_profile, args.authority
            ):
                raise ValueError("--inventory-profile must not alias --authority")
            if any(
                paths_refer_to_same_file(args.inventory_profile, protected)
                for protected in (
                    TOOL_PATH,
                    SHARED_HELPER_PATH,
                    RELEASE_VALIDATOR_PATH,
                )
            ):
                raise ValueError("--inventory-profile must not alias the inventory toolchain")
        if args.json_report is not None:
            if paths_refer_to_same_file(args.json_report, args.target):
                raise ValueError("--json-report must not alias target")
            if args.authority is not None and paths_refer_to_same_file(
                args.json_report, args.authority
            ):
                raise ValueError("--json-report must not alias --authority")
            if args.inventory_profile is not None and paths_refer_to_same_file(
                args.json_report, args.inventory_profile
            ):
                raise ValueError("--json-report must not alias --inventory-profile")
            if any(
                paths_refer_to_same_file(args.json_report, protected)
                for protected in (
                    TOOL_PATH,
                    SHARED_HELPER_PATH,
                    RELEASE_VALIDATOR_PATH,
                )
            ):
                raise ValueError("--json-report must not alias the inventory toolchain")
        custom_symbols: list[tuple[str, str]] = []
        inventory_profile_sha256: str | None = None
        if args.inventory_profile is not None:
            custom_symbols, inventory_profile_sha256 = load_inventory_profile(
                args.inventory_profile
            )
        payload, errors = build_inventory(
            args.target,
            authority=args.authority,
            inventory_profile=args.inventory_profile,
            inventory_profile_sha256=inventory_profile_sha256,
            custom_symbols=custom_symbols,
            require_markers=args.require_markers,
            include_starred_title_notes=args.include_starred_title_notes,
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.json_report is not None:
            args.json_report.write_text(rendered, encoding="utf-8", newline="")
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1

    print(
        "NOTATION-INVENTORY "
        f"status={payload['status']} markers={payload['page_marker_count']} "
        f"occurrences={len(payload['occurrences'])} errors={len(errors)}"
    )
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if args.json_report is None:
        print(rendered, end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
