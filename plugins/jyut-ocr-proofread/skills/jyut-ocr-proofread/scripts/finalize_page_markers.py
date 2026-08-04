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
    build_assembly_receipt,
    boundary_contract_problems,
    find_page_markers,
    load_boundary_manifest,
    marker_context,
    marker_sequence_problems,
    parse_page_spec,
    paths_refer_to_same_file,
    split_lines,
    write_assembly_receipt,
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
        help=(
            "pages intentionally without markers, for example 17 or 2,5-7; "
            "also record marker_expected=false in a sealed page manifest"
        ),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        help=(
            "write a SHA-256 assembly receipt beside --output; use repeated "
            "--upstream for segment and assembly-manifest dependencies"
        ),
    )
    parser.add_argument(
        "--upstream",
        action="append",
        type=Path,
        default=[],
        help=(
            "upstream segment or manifest to hash into --receipt; repeat in "
            "deterministic assembly order"
        ),
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
    if args.receipt is not None and not args.write:
        errors.append("--receipt requires --write")
    if args.upstream and args.receipt is None:
        errors.append("--upstream requires --receipt")
    if (
        args.write
        and args.output is not None
        and paths_refer_to_same_file(args.output, args.target)
    ):
        errors.append("--output must differ from annotated TARGET")
    if (
        args.write
        and args.output is not None
        and args.boundary_manifest is not None
        and paths_refer_to_same_file(args.output, args.boundary_manifest)
    ):
        errors.append("--output must differ from --boundary-manifest")
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
        print("Use --write to create --output after reviewing every boundary.")
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
    receipt_data: dict[str, object] | None = None
    if args.receipt is not None:
        assert args.boundary_manifest is not None
        if not args.receipt.resolve().parent.is_dir():
            print(
                f"ERROR {args.receipt}: receipt parent directory does not exist",
                file=sys.stderr,
            )
            return 1
        try:
            receipt_data = build_assembly_receipt(
                args.receipt,
                annotated_path=args.target,
                boundary_manifest_path=args.boundary_manifest,
                output_path=destination,
                output_text=result,
                upstream_paths=args.upstream,
            )
        except (OSError, ValueError) as error:
            print(f"ERROR {args.receipt}: {error}", file=sys.stderr)
            return 1
    try:
        _write_utf8(destination, result)
    except (OSError, UnicodeError) as error:
        print(f"ERROR {destination}: {error}", file=sys.stderr)
        return 1
    print(
        f"WROTE {destination}: removed {len(markers)} page marker(s); {counts}"
    )
    if args.receipt is not None:
        assert receipt_data is not None
        try:
            write_assembly_receipt(args.receipt, receipt_data)
        except (OSError, UnicodeError) as error:
            print(
                f"ERROR {args.receipt}: final output was written but receipt failed: "
                f"{error}",
                file=sys.stderr,
            )
            return 1
        print(
            f"RECEIPT {args.receipt}: inputs={len(receipt_data['inputs'])} "
            "algorithm=sha256"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
