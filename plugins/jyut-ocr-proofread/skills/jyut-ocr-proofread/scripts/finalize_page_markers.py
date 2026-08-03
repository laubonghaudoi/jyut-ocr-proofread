#!/usr/bin/env python3
"""Safely remove temporary OCR page markers from Markdown."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Sequence

from _ocr_markdown import (
    apply_boundary_contract,
    boundary_contract_problems,
    find_page_markers,
    load_boundary_manifest,
    marker_context,
    marker_sequence_problems,
    parse_page_spec,
    split_lines,
)


def _read_utf8(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_utf8(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove canonical <!-- page_001 --> comments while preserving join, "
            "paragraph-break, and Markdown-line boundary semantics."
        )
    )
    parser.add_argument("target", type=Path, help="annotated Markdown input")
    parser.add_argument(
        "--output",
        type=Path,
        help="final destination; required by --write and never allowed to equal TARGET",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the result to --output; the annotated TARGET is never overwritten",
    )
    parser.add_argument(
        "--boundary-manifest",
        type=Path,
        help=(
            "JSON contract {'boundaries': {'1': 'start', '2': 'join', ...}}; "
            "required by --write"
        ),
    )
    parser.add_argument(
        "--expected-count",
        "--expected-page-count",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        skip_pages = parse_page_spec(args.skip_pages)
        source = _read_utf8(args.target)
        boundary_contract = (
            load_boundary_manifest(args.boundary_manifest)
            if args.boundary_manifest is not None
            else None
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    lines = split_lines(source)
    markers, malformed = find_page_markers(lines)
    errors: list[str] = []
    for line_number, body in malformed:
        errors.append(
            f"line {line_number}: malformed page marker; use "
            f"'<!-- page_001 -->': {body!r}"
        )
    errors.extend(
        marker_sequence_problems(markers, args.expected_count, skip_pages)
    )
    if args.write and boundary_contract is None:
        errors.append("--write requires --boundary-manifest; inference is forbidden")
    if args.write and args.output is None:
        errors.append("--write requires --output; annotated TARGET is never overwritten")
    if (
        args.write
        and args.output is not None
        and args.output.resolve() == args.target.resolve()
    ):
        errors.append("--output must differ from annotated TARGET")
    if boundary_contract is not None:
        errors.extend(boundary_contract_problems(lines, markers, boundary_contract))
    if errors:
        for error in errors:
            print(f"ERROR {args.target}: {error}", file=sys.stderr)
        print(
            f"SUMMARY errors={len(errors)} markers={len(markers)} written=no",
            file=sys.stderr,
        )
        return 1

    labels = Counter()
    for marker in markers:
        previous, following = marker_context(lines, marker)
        label = (
            boundary_contract[marker.page]
            if boundary_contract is not None
            else "UNRESOLVED"
        )
        if boundary_contract is not None:
            labels[label] += 1
        print(
            "MARKER "
            f"line={marker.line_number} page={marker.page} contract={label} "
            f"left={previous!r} right={following!r}"
        )

    if not args.write:
        if boundary_contract is None:
            print(
                f"DRY-RUN inspected {len(markers)} page marker(s); "
                "boundaries=UNRESOLVED; written=no"
            )
            if args.output is not None:
                print(f"DRY-RUN output path not written: {args.output}")
            print(
                "Provide --boundary-manifest to resolve boundaries; "
                "use --write only after review."
            )
            return 0

        result = apply_boundary_contract(source, boundary_contract)
        counts = " ".join(
            f"{label}={labels.get(label, 0)}"
            for label in (
                "start",
                "join",
                "paragraph",
                "structural",
                "list-cont",
                "list-new",
            )
        )
        print(
            f"DRY-RUN would remove {len(markers)} page marker(s); {counts}; written=no"
        )
        if args.output is not None:
            print(f"DRY-RUN output path not written: {args.output}")
        print("Use --write to modify TARGET or create --output.")
        return 0

    assert boundary_contract is not None
    result = apply_boundary_contract(source, boundary_contract)
    counts = " ".join(
        f"{label}={labels.get(label, 0)}"
        for label in (
            "start",
            "join",
            "paragraph",
            "structural",
            "list-cont",
            "list-new",
        )
    )
    assert args.output is not None
    destination = args.output
    try:
        _write_utf8(destination, result)
    except (OSError, UnicodeError) as error:
        print(f"ERROR {destination}: {error}", file=sys.stderr)
        return 1
    print(
        f"WROTE {destination}: removed {len(markers)} page marker(s); {counts}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
