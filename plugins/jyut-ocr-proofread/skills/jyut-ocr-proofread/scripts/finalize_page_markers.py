#!/usr/bin/env python3
"""Safely remove temporary OCR page markers from Markdown."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Sequence

from _ocr_markdown import (
    ReleasePlanInventory,
    apply_boundary_contract,
    assembly_receipt_problems,
    build_assembly_receipt,
    boundary_contract_problems,
    find_page_markers,
    load_boundary_manifest,
    marker_context,
    marker_sequence_problems,
    load_validated_release_plan,
    parse_page_spec,
    paths_refer_to_same_file,
    release_plan_discovery_matches,
    release_plan_snapshot_problems,
    split_lines,
    write_assembly_receipt,
)


def _read_utf8(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_utf8(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _validated_release_upstreams(
    plan_path: Path,
    *,
    annotated_path: Path,
    boundary_manifest_path: Path,
    output_path: Path,
    receipt_path: Path,
    requested_upstreams: Sequence[Path],
) -> tuple[ReleasePlanInventory, tuple[Path, ...]]:
    """Validate the plan and return its complete active receipt closure."""

    inventory = load_validated_release_plan(plan_path)
    active_paths = inventory.active_paths

    def active_matches(path: Path) -> list[Path]:
        return [
            candidate
            for candidate in active_paths
            if paths_refer_to_same_file(path, candidate)
        ]

    if len(active_matches(annotated_path)) != 1:
        raise ValueError(
            "annotated TARGET must be exactly one active included release artifact"
        )
    if len(active_matches(boundary_manifest_path)) != 1:
        raise ValueError(
            "--boundary-manifest must be exactly one active included release artifact"
        )
    for role, path in (("--output", output_path), ("--receipt", receipt_path)):
        if any(
            paths_refer_to_same_file(path, artifact)
            for artifact in inventory.classified_paths
        ):
            raise ValueError(
                f"{role} must not alias any classified release-plan artifact; "
                "generated files cannot overwrite release evidence"
            )
        if paths_refer_to_same_file(path, inventory.plan_path):
            raise ValueError(f"{role} must differ from --release-plan")
        if not path.exists():
            matches = release_plan_discovery_matches(inventory, path)
            if matches:
                raise ValueError(
                    f"{role} would be discovered by release-plan glob(s) after "
                    f"write: {', '.join(repr(pattern) for pattern in matches)}"
                )

    unique_requested: list[Path] = []
    for requested in requested_upstreams:
        if not active_matches(requested):
            raise ValueError(
                f"--upstream is not in the active included release closure: {requested}"
            )
        if any(
            paths_refer_to_same_file(requested, previous)
            for previous in unique_requested
        ):
            continue
        unique_requested.append(requested)

    receipt_upstreams: list[Path] = []
    for active_path in active_paths:
        if paths_refer_to_same_file(active_path, annotated_path) or (
            paths_refer_to_same_file(active_path, boundary_manifest_path)
        ):
            continue
        receipt_upstreams.append(active_path)
    return inventory, tuple(receipt_upstreams)


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
            "write a SHA-256 assembly receipt beside --output; requires "
            "--release-plan, whose full active closure is sealed automatically"
        ),
    )
    parser.add_argument(
        "--release-plan",
        type=Path,
        help=(
            "validated release-plan.json; required by --receipt and used to "
            "derive the complete active included upstream closure"
        ),
    )
    parser.add_argument(
        "--upstream",
        action="append",
        type=Path,
        default=[],
        help=(
            "optional assertion that an input belongs to the active release-plan "
            "closure; repeated aliases are deduplicated and no unclassified input "
            "can be added"
        ),
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="suppress per-marker context while retaining errors and summaries",
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
    if args.receipt is not None and args.release_plan is None:
        errors.append("--receipt requires --release-plan")
    if args.release_plan is not None and args.receipt is None:
        errors.append("--release-plan requires --receipt")
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
    release_inventory: ReleasePlanInventory | None = None
    receipt_upstreams: tuple[Path, ...] = ()
    if (
        not errors
        and args.receipt is not None
        and args.release_plan is not None
        and args.boundary_manifest is not None
        and args.output is not None
    ):
        try:
            release_inventory, receipt_upstreams = _validated_release_upstreams(
                args.release_plan,
                annotated_path=args.target,
                boundary_manifest_path=args.boundary_manifest,
                output_path=args.output,
                receipt_path=args.receipt,
                requested_upstreams=args.upstream,
            )
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(str(error))
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
        if not args.summary_only:
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
        assert release_inventory is not None
        if not args.receipt.resolve().parent.is_dir():
            print(
                f"ERROR {args.receipt}: receipt parent directory does not exist",
                file=sys.stderr,
            )
            return 1
        try:
            snapshot_errors = release_plan_snapshot_problems(release_inventory)
            if snapshot_errors:
                raise ValueError("; ".join(snapshot_errors))
            receipt_data = build_assembly_receipt(
                args.receipt,
                annotated_path=args.target,
                boundary_manifest_path=args.boundary_manifest,
                release_plan_path=release_inventory.plan_path,
                output_path=destination,
                output_text=result,
                upstream_paths=receipt_upstreams,
            )
            refreshed_inventory = load_validated_release_plan(
                release_inventory.plan_path
            )
            if refreshed_inventory != release_inventory:
                raise ValueError(
                    "release plan or classified artifact closure changed between "
                    "validation and receipt construction"
                )
        except (OSError, ValueError) as error:
            print(f"ERROR {args.receipt}: {error}", file=sys.stderr)
            return 1
    try:
        _write_utf8(destination, result)
    except (OSError, UnicodeError) as error:
        print(f"ERROR {destination}: {error}", file=sys.stderr)
        return 1
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
        try:
            post_write_problems = assembly_receipt_problems(
                args.receipt,
                expected_output_path=destination,
            )
        except (OSError, UnicodeError, ValueError) as error:
            print(
                f"ERROR {args.receipt}: post-write receipt validation failed: "
                f"{error}",
                file=sys.stderr,
            )
            return 1
        if post_write_problems:
            for code, message in post_write_problems:
                print(
                    f"ERROR {args.receipt}: [{code}] {message}",
                    file=sys.stderr,
                )
            print(
                f"SUMMARY errors={len(post_write_problems)} markers={len(markers)} "
                "written=yes receipt_valid=no",
                file=sys.stderr,
            )
            return 1
    print(
        f"WROTE {destination}: removed {len(markers)} page marker(s); {counts}"
    )
    if args.receipt is not None:
        print(
            f"RECEIPT {args.receipt}: inputs={len(receipt_data['inputs'])} "
            "algorithm=sha256"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
