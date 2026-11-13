#!/usr/bin/env python3
"""Deterministically assemble frozen OCR segments from a sealed manifest.

Manifest schema (unknown fields and duplicate JSON keys are rejected)::

    {
      "schema_version": 1,
      "separator_utf8": "\n",
      "scan_start": 1,
      "scan_end": 160,
      "page_manifest": {
        "path": "page-manifest.json",
        "sha256": "0123...cdef"
      },
      "segments": [
        {
          "path": "segments/p001-080.md",
          "sha256": "0123...cdef",
          "scan_start": 1,
          "scan_end": 80
        }
      ]
    }

``path`` is a relative path beneath the manifest directory.  Absolute paths,
``..`` traversal, symlink escapes, duplicate files, missing files, hash
mismatches, and scan-range gaps/overlaps fail closed.  Segment bytes are joined
exactly in manifest order using the declared UTF-8 separator; no other byte is
inserted, removed, or normalized.  Every segment's canonical page markers must
exactly equal the marker policy in the sealed page manifest for that segment's
declared scan range; malformed, duplicate, missing, extra, or out-of-order
markers fail closed.

The command is read-only by default.  If an existing ``--output`` is supplied,
dry-run compares it byte-for-byte with the replayed assembly.  ``--write``
requires a distinct ``--output`` and verifies the bytes after an atomic write.
``--receipt`` is optional and records the verified manifest, segment, range,
page-manifest, tool, separator, and output hashes plus a deterministic,
path-independent invocation profile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Sequence

from _ocr_markdown import find_page_markers, load_page_manifest, split_lines


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "separator_utf8",
        "scan_start",
        "scan_end",
        "page_manifest",
        "segments",
    }
)
SEGMENT_FIELDS = frozenset({"path", "sha256", "scan_start", "scan_end"})
PAGE_MANIFEST_FIELDS = frozenset({"path", "sha256"})


class ManifestError(ValueError):
    """A deterministic assembly contract is invalid."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise ManifestError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise ManifestError("manifest root must be a JSON object")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_aliases(left: Path, right: Path) -> bool:
    """Detect lexical, symlink, case-only, and existing hard-link aliases."""

    left_resolved = left.expanduser().resolve(strict=False)
    right_resolved = right.expanduser().resolve(strict=False)
    if os.path.normcase(str(left_resolved)) == os.path.normcase(str(right_resolved)):
        return True
    if str(left_resolved).casefold() == str(right_resolved).casefold():
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _safe_input_path(base: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ManifestError(f"{label} must be a non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ManifestError(f"{label} must be relative: {raw_path!r}")
    if any(part == ".." for part in candidate.parts):
        raise ManifestError(f"{label} contains '..': {raw_path!r}")
    try:
        resolved = (base / candidate).resolve(strict=True)
    except FileNotFoundError as error:
        raise ManifestError(f"{label} is missing: {raw_path!r}") from error
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise ManifestError(
            f"{label} escapes the manifest directory: {raw_path!r}"
        ) from error
    if not resolved.is_file():
        raise ManifestError(f"{label} is not a regular file: {raw_path!r}")
    return resolved


def _safe_segment_path(base: Path, raw_path: object, index: int) -> Path:
    return _safe_input_path(base, raw_path, f"segments[{index}].path")


def _validate_sealed_page_manifest(
    base: Path, raw_contract: object
) -> tuple[dict[str, object], object]:
    if not isinstance(raw_contract, dict):
        raise ManifestError("page_manifest must be a JSON object")
    unknown = sorted(set(raw_contract) - PAGE_MANIFEST_FIELDS)
    missing = sorted(PAGE_MANIFEST_FIELDS - set(raw_contract))
    if unknown:
        raise ManifestError(
            "page_manifest has unknown field(s): " + ", ".join(unknown)
        )
    if missing:
        raise ManifestError(
            "page_manifest is missing field(s): " + ", ".join(missing)
        )
    expected_hash = raw_contract["sha256"]
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise ManifestError(
            "page_manifest.sha256 must be 64 lowercase hexadecimal characters"
        )
    path = _safe_input_path(base, raw_contract["path"], "page_manifest.path")
    actual_hash = _sha256_file(path)
    if actual_hash != expected_hash:
        raise ManifestError(
            "page_manifest hash mismatch: "
            f"expected {expected_hash}, got {actual_hash}"
        )
    try:
        data = load_page_manifest(path)
    except (OSError, UnicodeError, ValueError) as error:
        raise ManifestError(f"invalid sealed page manifest: {error}") from error
    expected_sequence = list(range(1, data.expected_scan_pages + 1))
    actual_sequence = [record.scan_page for record in data.pages]
    if actual_sequence != expected_sequence:
        raise ManifestError(
            "sealed page manifest scan_page sequence must be exactly "
            f"1-{data.expected_scan_pages}"
        )
    if len(data.pages) != data.expected_scan_pages:
        raise ManifestError(
            "sealed page manifest record count does not equal expected_scan_pages"
        )
    return (
        {
            "path": raw_contract["path"],
            "resolved_path": path,
            "sha256": actual_hash,
        },
        data,
    )


def _validate_segment_markers(
    data: bytes,
    *,
    index: int,
    scan_start: int,
    scan_end: int,
    page_manifest: object,
) -> list[int]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError(
            f"segments[{index}] is not valid UTF-8: {error}"
        ) from error
    markers, malformed = find_page_markers(split_lines(text))
    if malformed:
        locations = ", ".join(
            f"line {line_number}: {body!r}" for line_number, body in malformed
        )
        raise ManifestError(
            f"segments[{index}] contains malformed page marker(s): {locations}"
        )
    pages = getattr(page_manifest, "pages")
    expected_pages = [
        record.scan_page
        for record in pages
        if scan_start <= record.scan_page <= scan_end and record.marker_expected
    ]
    actual_pages = [marker.page for marker in markers]
    if actual_pages != expected_pages:
        missing = [page for page in expected_pages if page not in actual_pages]
        extra = [page for page in actual_pages if page not in expected_pages]
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(map(str, missing)))
        if extra:
            details.append("extra=" + ",".join(map(str, extra)))
        if not missing and not extra:
            details.append("markers are duplicated or out of order")
        raise ManifestError(
            f"segments[{index}] canonical page markers {actual_pages} do not "
            f"match page_manifest marker_expected pages {expected_pages} for "
            f"declared scan range {scan_start}-{scan_end}: " + "; ".join(details)
        )
    return actual_pages


def _validate_manifest(
    manifest_path: Path,
) -> tuple[
    Path,
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    bytes,
]:
    try:
        sealed_manifest = manifest_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ManifestError(f"manifest does not exist: {manifest_path}") from error
    if not sealed_manifest.is_file():
        raise ManifestError(f"manifest is not a regular file: {manifest_path}")
    manifest = _load_json(sealed_manifest)
    unknown = sorted(set(manifest) - ROOT_FIELDS)
    missing = sorted(ROOT_FIELDS - set(manifest))
    if unknown:
        raise ManifestError(f"unknown manifest field(s): {', '.join(unknown)}")
    if missing:
        raise ManifestError(f"missing manifest field(s): {', '.join(missing)}")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ManifestError("schema_version must equal 1")
    separator = manifest["separator_utf8"]
    if not isinstance(separator, str):
        raise ManifestError("separator_utf8 must be a JSON string")
    if re.fullmatch(r"\n*", separator) is None:
        raise ManifestError(
            "separator_utf8 may contain only LF newlines; semantic text and "
            "other whitespace are forbidden"
        )
    try:
        separator_bytes = separator.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ManifestError("separator_utf8 is not valid UTF-8 text") from error
    declared_start = manifest["scan_start"]
    declared_end = manifest["scan_end"]
    if (
        not isinstance(declared_start, int)
        or isinstance(declared_start, bool)
        or not isinstance(declared_end, int)
        or isinstance(declared_end, bool)
        or declared_start < 1
        or declared_end < declared_start
    ):
        raise ManifestError("manifest scan range must satisfy 1 <= scan_start <= scan_end")
    raw_segments = manifest["segments"]
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ManifestError("segments must be a non-empty ordered array")

    base = sealed_manifest.parent.resolve(strict=True)
    page_manifest_contract, page_manifest = _validate_sealed_page_manifest(
        base, manifest["page_manifest"]
    )
    if declared_end > page_manifest.expected_scan_pages:
        raise ManifestError(
            f"manifest scan_end {declared_end} exceeds sealed page manifest "
            f"expected_scan_pages {page_manifest.expected_scan_pages}"
        )
    verified: list[dict[str, object]] = []
    payload_parts: list[bytes] = []
    expected_scan_start = declared_start
    for index, raw_entry in enumerate(raw_segments):
        if not isinstance(raw_entry, dict):
            raise ManifestError(f"segments[{index}] must be a JSON object")
        unknown_entry = sorted(set(raw_entry) - SEGMENT_FIELDS)
        missing_entry = sorted(SEGMENT_FIELDS - set(raw_entry))
        if unknown_entry:
            raise ManifestError(
                f"segments[{index}] has unknown field(s): {', '.join(unknown_entry)}"
            )
        if missing_entry:
            raise ManifestError(
                f"segments[{index}] is missing field(s): {', '.join(missing_entry)}"
            )
        expected = raw_entry["sha256"]
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ManifestError(
                f"segments[{index}].sha256 must be 64 lowercase hexadecimal characters"
            )
        scan_start = raw_entry["scan_start"]
        scan_end = raw_entry["scan_end"]
        if (
            not isinstance(scan_start, int)
            or isinstance(scan_start, bool)
            or not isinstance(scan_end, int)
            or isinstance(scan_end, bool)
            or scan_start < 1
            or scan_end < scan_start
        ):
            raise ManifestError(
                f"segments[{index}] scan range must satisfy "
                "1 <= scan_start <= scan_end"
            )
        if scan_start != expected_scan_start:
            relation = (
                "overlaps or is out of order"
                if scan_start < expected_scan_start
                else "has a gap"
            )
            raise ManifestError(
                f"segments[{index}] {relation}: expected scan_start "
                f"{expected_scan_start}, got {scan_start}"
            )
        expected_scan_start = scan_end + 1
        path = _safe_segment_path(base, raw_entry["path"], index)
        page_manifest_path = page_manifest_contract["resolved_path"]
        assert isinstance(page_manifest_path, Path)
        if _path_aliases(path, page_manifest_path):
            raise ManifestError(
                f"segments[{index}] aliases the sealed page manifest"
            )
        for earlier in verified:
            earlier_path = earlier["resolved_path"]
            assert isinstance(earlier_path, Path)
            if _path_aliases(path, earlier_path):
                raise ManifestError(
                    f"segments[{index}] duplicates an earlier file: {raw_entry['path']!r}"
                )
        data = path.read_bytes()
        actual = _sha256_bytes(data)
        if actual != expected:
            raise ManifestError(
                f"segments[{index}] hash mismatch for {raw_entry['path']!r}: "
                f"expected {expected}, got {actual}"
            )
        marker_pages = _validate_segment_markers(
            data,
            index=index,
            scan_start=scan_start,
            scan_end=scan_end,
            page_manifest=page_manifest,
        )
        verified.append(
            {
                "path": raw_entry["path"],
                "resolved_path": path,
                "sha256": actual,
                "bytes": len(data),
                "scan_start": scan_start,
                "scan_end": scan_end,
                "marker_pages": marker_pages,
            }
        )
        payload_parts.append(data)
    if expected_scan_start != declared_end + 1:
        raise ManifestError(
            "segment ranges do not end at declared scan_end "
            f"{declared_end}; next uncovered scan is {expected_scan_start}"
        )
    assembly = separator_bytes.join(payload_parts)
    assembly_text = assembly.decode("utf-8")
    assembly_markers, assembly_malformed = find_page_markers(
        split_lines(assembly_text)
    )
    if assembly_malformed:
        locations = ", ".join(
            f"line {line_number}: {body!r}"
            for line_number, body in assembly_malformed
        )
        raise ManifestError(
            "assembled output contains malformed page marker(s): " + locations
        )
    expected_assembly_markers = [
        page
        for entry in verified
        for page in entry["marker_pages"]
    ]
    actual_assembly_markers = [marker.page for marker in assembly_markers]
    if actual_assembly_markers != expected_assembly_markers:
        raise ManifestError(
            "assembled output canonical page markers do not equal the ordered "
            f"segment marker inventory: expected {expected_assembly_markers}, "
            f"got {actual_assembly_markers}"
        )
    contract = {
        "separator_utf8": separator,
        "separator_sha256": _sha256_bytes(separator_bytes),
        "scan_start": declared_start,
        "scan_end": declared_end,
    }
    return sealed_manifest, contract, page_manifest_contract, verified, assembly


def _atomic_write(path: Path, data: bytes) -> None:
    parent = path.parent.resolve(strict=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _clean_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "path": entry["path"],
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
            "scan_start": entry["scan_start"],
            "scan_end": entry["scan_end"],
            "marker_pages": entry["marker_pages"],
        }
        for entry in entries
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and concatenate ordered OCR segment bytes from a sealed JSON "
            "manifest. Dry-run is the default; --write requires --output."
        ),
        epilog=(
            "Manifest v1 declares separator_utf8, the overall scan_start/scan_end, "
            "a sealed page_manifest, and ordered segments with path, sha256, "
            "scan_start, and scan_end. Paths are resolved beneath the manifest "
            "directory; ranges and canonical marker coverage must match exactly."
        ),
    )
    parser.add_argument("manifest", type=Path, help="ordered segment manifest JSON")
    parser.add_argument(
        "--output",
        type=Path,
        help="assembled destination; required by --write and distinct from every input",
    )
    parser.add_argument(
        "--write", action="store_true", help="atomically create or replace --output"
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        help="optional JSON receipt; requires --write and must be a distinct path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the result as one JSON object instead of human-readable text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    errors: list[str] = []
    try:
        manifest_path, contract, page_manifest, entries, payload = _validate_manifest(
            args.manifest
        )
    except (ManifestError, OSError, UnicodeError) as error:
        errors.append(str(error))
        manifest_path = args.manifest.resolve(strict=False)
        contract = {}
        page_manifest = {}
        entries = []
        payload = b""

    if args.write and args.output is None:
        errors.append("--write requires --output")
    if args.receipt is not None and not args.write:
        errors.append("--receipt requires --write")
    if not errors and args.output is not None:
        output = args.output.expanduser()
        if not output.parent.exists() or not output.parent.is_dir():
            errors.append("--output parent directory does not exist")
        if output.exists() and output.is_dir():
            errors.append("--output must not be a directory")
        if _path_aliases(output, manifest_path):
            errors.append("--output must differ from the manifest")
        sealed_page_manifest = page_manifest.get("resolved_path")
        if isinstance(sealed_page_manifest, Path) and _path_aliases(
            output, sealed_page_manifest
        ):
            errors.append("--output must differ from the sealed page manifest")
        for entry in entries:
            segment_path = entry["resolved_path"]
            assert isinstance(segment_path, Path)
            if _path_aliases(output, segment_path):
                errors.append(f"--output aliases segment {entry['path']!r}")
                break
    if not errors and args.receipt is not None:
        receipt = args.receipt.expanduser()
        if not receipt.parent.exists() or not receipt.parent.is_dir():
            errors.append("--receipt parent directory does not exist")
        if receipt.exists() and receipt.is_dir():
            errors.append("--receipt must not be a directory")
        protected_paths: list[object] = [
            manifest_path,
            page_manifest.get("resolved_path"),
            *(entry["resolved_path"] for entry in entries),
        ]
        for protected in protected_paths:
            if protected is None:
                continue
            assert isinstance(protected, Path)
            if _path_aliases(receipt, protected):
                errors.append(
                    "--receipt must differ from the manifest, sealed page manifest, "
                    "and all segments"
                )
                break
        if args.output is not None and _path_aliases(receipt, args.output):
            errors.append("--receipt must differ from --output")

    output_hash = _sha256_bytes(payload) if entries else None
    report: dict[str, object] = {
        "ok": not errors,
        "mode": "write" if args.write else "dry-run",
        "manifest": str(manifest_path),
        "page_manifest": {
            "path": page_manifest.get("path"),
            "sha256": page_manifest.get("sha256"),
        }
        if page_manifest
        else None,
        "segments": _clean_entries(entries),
        "assembly_contract": contract,
        "segment_count": len(entries),
        "output": str(args.output.expanduser().resolve(strict=False))
        if args.output is not None
        else None,
        "output_sha256": output_hash,
        "output_bytes": len(payload),
        "written": False,
        "output_verified": None,
        "errors": errors,
    }
    if errors:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            for error in errors:
                print(f"ERROR {args.manifest}: {error}", file=sys.stderr)
            print(
                f"SUMMARY errors={len(errors)} segments={len(entries)} written=no",
                file=sys.stderr,
            )
        return 1

    if not args.write:
        if args.output is not None and args.output.exists():
            try:
                existing = args.output.read_bytes()
            except OSError as error:
                report["ok"] = False
                report["errors"] = [f"cannot read existing --output: {error}"]
                if args.json:
                    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                else:
                    print(f"ERROR {args.output}: {error}", file=sys.stderr)
                return 1
            report["output_verified"] = existing == payload
            if existing != payload:
                actual_hash = _sha256_bytes(existing)
                message = (
                    "existing --output does not byte-match replayed assembly: "
                    f"expected {len(payload)} bytes/{output_hash}, got "
                    f"{len(existing)} bytes/{actual_hash}"
                )
                report["ok"] = False
                report["errors"] = [message]
                if args.json:
                    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                else:
                    print(f"ERROR {args.output}: {message}", file=sys.stderr)
                return 1
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(
                f"DRY-RUN verified {len(entries)} segment(s); "
                f"bytes={len(payload)} sha256={output_hash}; "
                f"existing-output={'verified' if report['output_verified'] else 'not-present'}; "
                "written=no"
            )
        return 0

    assert args.output is not None
    output = args.output.expanduser()
    try:
        _atomic_write(output, payload)
        actual_output_hash = _sha256_file(output)
        actual_output_bytes = output.stat().st_size
        if actual_output_hash != output_hash or actual_output_bytes != len(payload):
            raise OSError(
                "post-write byte verification failed: "
                f"expected {len(payload)} bytes/{output_hash}, got "
                f"{actual_output_bytes} bytes/{actual_output_hash}"
            )
        report["written"] = True
        report["output_verified"] = True
        if args.receipt is not None:
            page_manifest_path = page_manifest["resolved_path"]
            assert isinstance(page_manifest_path, Path)
            invocation_profile = {
                "profile_version": 1,
                "operation": "assemble-ocr-segments",
                "write": True,
                "receipt": True,
                "report_format": "json" if args.json else "human",
            }
            receipt_data = {
                "schema_version": 1,
                "algorithm": "sha256",
                "tool": {
                    "name": "assemble_ocr_segments.py",
                    "sha256": _sha256_file(Path(__file__).resolve(strict=True)),
                },
                "invocation_profile": invocation_profile,
                "manifest": {
                    "path": os.path.relpath(manifest_path, manifest_path.parent),
                    "sha256": _sha256_file(manifest_path),
                },
                "page_manifest": {
                    "path": str(page_manifest["path"]),
                    "sha256": _sha256_file(page_manifest_path),
                },
                "segments": _clean_entries(entries),
                "assembly_contract": contract,
                "output": {
                    "path": os.path.relpath(
                        output.resolve(strict=True), manifest_path.parent
                    ),
                    "sha256": actual_output_hash,
                    "bytes": actual_output_bytes,
                },
            }
            receipt_bytes = (
                json.dumps(
                    receipt_data, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n"
            ).encode("utf-8")
            _atomic_write(args.receipt.expanduser(), receipt_bytes)
            report["receipt"] = str(args.receipt.expanduser().resolve(strict=True))
    except (OSError, UnicodeError) as error:
        report["ok"] = False
        report["errors"] = [str(error)]
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(f"ERROR {args.output}: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"WROTE {output}: segments={len(entries)} bytes={len(payload)} "
            f"sha256={output_hash}; verified=yes"
        )
        if args.receipt is not None:
            print(f"RECEIPT {args.receipt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
