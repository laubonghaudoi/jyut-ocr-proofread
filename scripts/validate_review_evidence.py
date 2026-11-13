#!/usr/bin/env python3
"""Validate canonical OCR late-amendment review ledgers.

The v1 schema is intentionally small and closed.  Unknown fields and duplicate
JSON keys are rejected at every level::

    {
      "schema_version": 1,
      "artifact_type": "ocr-review-amendment",
      "status": "independent-pass-after-amendment",
      "scan_range": {"start": 10, "end": 20},
      "writer": "writer-id",
      "independent_verifier": "reviewer-id",
      "reviewer_differs_from_writer": true,
      "changed_scans": [14],
      "reviewed_scans": [13, 14, 15],
      "current_content_hashes": {
        "segments/p0010-p0020.md": "<sha256>"
      },
      "changes": [
        {
          "scan_page": 14,
          "before": "old text",
          "after": "new text",
          "render": {"path": "renders/page-014.png", "sha256": "<sha256>"},
          "evidence": {"path": "evidence/change-014.md", "sha256": "<sha256>"}
        }
      ],
      "unresolved_count": 0
    }

All file references are safe, canonical relative paths beneath the validation
root and must match the current full-file SHA-256.  By default each ledger's
parent directory is its root; ``--root`` supplies one shared root for all
ledgers.  Every changed scan and its immediate in-range neighbours must appear
in ``reviewed_scans``.  Multiple change records may share a scan page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import unicodedata
from typing import Any, Iterable, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_type",
        "status",
        "scan_range",
        "writer",
        "independent_verifier",
        "reviewer_differs_from_writer",
        "changed_scans",
        "reviewed_scans",
        "current_content_hashes",
        "changes",
        "unresolved_count",
    }
)
SCAN_RANGE_FIELDS = frozenset({"start", "end"})
CHANGE_FIELDS = frozenset({"scan_page", "before", "after", "render", "evidence"})
FILE_REF_FIELDS = frozenset({"path", "sha256"})
ARTIFACT_TYPE = "ocr-review-amendment"
PASS_STATUS = "independent-pass-after-amendment"


class LedgerError(ValueError):
    """A ledger cannot be parsed safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise LedgerError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    except (OSError, UnicodeError) as error:
        raise LedgerError(f"cannot read ledger: {error}") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shape_errors(
    value: dict[str, Any], allowed: frozenset[str], required: Iterable[str], label: str
) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(value) - allowed)
    missing = sorted(set(required) - set(value))
    if unknown:
        errors.append(f"{label} has unknown field(s): {', '.join(unknown)}")
    if missing:
        errors.append(f"{label} is missing field(s): {', '.join(missing)}")
    return errors


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_identity(value: object, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return None
    if value != value.strip():
        errors.append(f"{label} must not have leading or trailing whitespace")
        return None
    return unicodedata.normalize("NFKC", value).casefold()


def _safe_file(
    root: Path,
    raw_path: object,
    label: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{label} must be a non-empty relative path string")
        return None
    if "\\" in raw_path:
        errors.append(f"{label} must use canonical POSIX separators: {raw_path!r}")
        return None
    candidate = PurePosixPath(raw_path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != raw_path
    ):
        errors.append(f"{label} must be a safe canonical relative path: {raw_path!r}")
        return None
    unresolved = root.joinpath(*candidate.parts)
    try:
        resolved = unresolved.resolve(strict=True)
    except (FileNotFoundError, OSError, ValueError) as error:
        errors.append(f"{label} does not resolve to an existing file: {raw_path!r} ({error})")
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes the validation root: {raw_path!r}")
        return None
    if not resolved.is_file():
        errors.append(f"{label} is not a regular file: {raw_path!r}")
        return None
    return resolved


def _check_hash(
    path: Path | None,
    expected: object,
    label: str,
    errors: list[str],
    cache: dict[Path, str],
) -> None:
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        errors.append(f"{label}.sha256 must be a lowercase 64-character SHA-256")
        return
    if path is None:
        return
    try:
        actual = cache.get(path)
        if actual is None:
            actual = _sha256_file(path)
            cache[path] = actual
    except OSError as error:
        errors.append(f"{label} cannot be hashed: {error}")
        return
    if actual != expected:
        errors.append(
            f"{label} hash mismatch: expected {expected}, current file is {actual}"
        )


def _validate_file_ref(
    value: object,
    root: Path,
    label: str,
    errors: list[str],
    cache: dict[Path, str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return
    errors.extend(_shape_errors(value, FILE_REF_FIELDS, FILE_REF_FIELDS, label))
    path = _safe_file(root, value.get("path"), f"{label}.path", errors)
    _check_hash(path, value.get("sha256"), label, errors, cache)


def _validate_scan_array(
    value: object,
    label: str,
    scan_start: int | None,
    scan_end: int | None,
    errors: list[str],
    *,
    require_nonempty: bool,
) -> list[int] | None:
    if not isinstance(value, list) or (require_nonempty and not value):
        qualifier = "non-empty " if require_nonempty else ""
        errors.append(f"{label} must be a {qualifier}array of scan-page integers")
        return None
    if not all(_is_integer(scan) for scan in value):
        errors.append(f"{label} must contain only scan-page integers")
        return None
    scans = list(value)
    if scans != sorted(set(scans)):
        errors.append(f"{label} must be strictly increasing with no duplicates")
    if scan_start is not None and scan_end is not None:
        outside = [scan for scan in scans if not scan_start <= scan <= scan_end]
        if outside:
            errors.append(
                f"{label} contains scan(s) outside {scan_start}-{scan_end}: "
                + ", ".join(str(scan) for scan in outside)
            )
    return scans


def _validate_current_hashes(
    value: object,
    root: Path,
    errors: list[str],
    cache: dict[Path, str],
) -> int:
    label = "current_content_hashes"
    if not isinstance(value, dict) or not value:
        errors.append(f"{label} must be a non-empty path-to-SHA-256 object")
        return 0
    resolved_by_path: dict[Path, str] = {}
    for raw_path in sorted(value):
        path_label = f"{label}[{raw_path!r}]"
        path = _safe_file(root, raw_path, f"{path_label}.path", errors)
        if path is not None:
            previous = resolved_by_path.get(path)
            if previous is not None:
                errors.append(
                    f"{label} paths {previous!r} and {raw_path!r} alias the same file"
                )
            else:
                resolved_by_path[path] = raw_path
        _check_hash(path, value[raw_path], path_label, errors, cache)
    return len(value)


def validate_ledger(document: object, root: Path) -> tuple[list[str], dict[str, int]]:
    """Return validation errors and deterministic summary counts."""

    errors: list[str] = []
    summary = {
        "scan_start": 0,
        "scan_end": 0,
        "changed_scans": 0,
        "reviewed_scans": 0,
        "content_files": 0,
        "changes": 0,
    }
    if not isinstance(document, dict):
        return ["ledger root must be a JSON object"], summary
    errors.extend(_shape_errors(document, ROOT_FIELDS, ROOT_FIELDS, "ledger"))

    if document.get("schema_version") != 1 or isinstance(
        document.get("schema_version"), bool
    ):
        errors.append("schema_version must equal 1")
    if document.get("artifact_type") != ARTIFACT_TYPE:
        errors.append(f"artifact_type must equal {ARTIFACT_TYPE!r}")
    if document.get("status") != PASS_STATUS:
        errors.append(f"status must equal {PASS_STATUS!r}")
    if document.get("reviewer_differs_from_writer") is not True:
        errors.append("reviewer_differs_from_writer must be true")
    if document.get("unresolved_count") != 0 or isinstance(
        document.get("unresolved_count"), bool
    ):
        errors.append("unresolved_count must be the integer 0")

    writer = _validate_identity(document.get("writer"), "writer", errors)
    verifier = _validate_identity(
        document.get("independent_verifier"), "independent_verifier", errors
    )
    if writer is not None and verifier is not None and writer == verifier:
        errors.append("writer and independent_verifier must identify different reviewers")

    scan_start: int | None = None
    scan_end: int | None = None
    scan_range = document.get("scan_range")
    if not isinstance(scan_range, dict):
        errors.append("scan_range must be a JSON object")
    else:
        errors.extend(
            _shape_errors(
                scan_range, SCAN_RANGE_FIELDS, SCAN_RANGE_FIELDS, "scan_range"
            )
        )
        start = scan_range.get("start")
        end = scan_range.get("end")
        if not _is_integer(start) or start < 1:
            errors.append("scan_range.start must be an integer of at least 1")
        else:
            scan_start = start
            summary["scan_start"] = start
        if not _is_integer(end) or end < 1:
            errors.append("scan_range.end must be an integer of at least 1")
        else:
            scan_end = end
            summary["scan_end"] = end
        if scan_start is not None and scan_end is not None and scan_end < scan_start:
            errors.append("scan_range.end must be greater than or equal to scan_range.start")

    changed = _validate_scan_array(
        document.get("changed_scans"),
        "changed_scans",
        scan_start,
        scan_end,
        errors,
        require_nonempty=True,
    )
    reviewed = _validate_scan_array(
        document.get("reviewed_scans"),
        "reviewed_scans",
        scan_start,
        scan_end,
        errors,
        require_nonempty=True,
    )
    if changed is not None:
        summary["changed_scans"] = len(changed)
    if reviewed is not None:
        summary["reviewed_scans"] = len(reviewed)

    hash_cache: dict[Path, str] = {}
    summary["content_files"] = _validate_current_hashes(
        document.get("current_content_hashes"), root, errors, hash_cache
    )

    changes = document.get("changes")
    change_pages: list[int] = []
    if not isinstance(changes, list) or not changes:
        errors.append("changes must be a non-empty array")
    else:
        summary["changes"] = len(changes)
        for index, change in enumerate(changes):
            label = f"changes[{index}]"
            if not isinstance(change, dict):
                errors.append(f"{label} must be a JSON object")
                continue
            errors.extend(_shape_errors(change, CHANGE_FIELDS, CHANGE_FIELDS, label))
            scan_page = change.get("scan_page")
            if not _is_integer(scan_page):
                errors.append(f"{label}.scan_page must be an integer")
            else:
                change_pages.append(scan_page)
                if (
                    scan_start is not None
                    and scan_end is not None
                    and not scan_start <= scan_page <= scan_end
                ):
                    errors.append(
                        f"{label}.scan_page {scan_page} is outside "
                        f"{scan_start}-{scan_end}"
                    )
            before = change.get("before")
            after = change.get("after")
            if not isinstance(before, str) or not before.strip():
                errors.append(f"{label}.before must be non-empty text")
            if not isinstance(after, str) or not after.strip():
                errors.append(f"{label}.after must be non-empty text")
            if isinstance(before, str) and isinstance(after, str) and before == after:
                errors.append(f"{label}.before and {label}.after must differ")
            _validate_file_ref(change.get("render"), root, f"{label}.render", errors, hash_cache)
            _validate_file_ref(
                change.get("evidence"), root, f"{label}.evidence", errors, hash_cache
            )
        if change_pages != sorted(change_pages):
            errors.append("changes must be ordered by non-decreasing scan_page")

    if (
        changed is not None
        and isinstance(changes, list)
        and len(change_pages) == len(changes)
    ):
        recorded = set(changed)
        observed = set(change_pages)
        if recorded != observed:
            missing = sorted(observed - recorded)
            extra = sorted(recorded - observed)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(map(str, missing)))
            if extra:
                details.append("extra " + ", ".join(map(str, extra)))
            errors.append("changed_scans must exactly match changes: " + "; ".join(details))

    if changed is not None and reviewed is not None:
        changed_set = set(changed)
        reviewed_set = set(reviewed)
        missing_changed = sorted(changed_set - reviewed_set)
        if missing_changed:
            errors.append(
                "reviewed_scans does not cover changed scan(s): "
                + ", ".join(map(str, missing_changed))
            )
        if scan_start is not None and scan_end is not None:
            required_context: set[int] = set()
            for scan in changed_set:
                required_context.update(
                    candidate
                    for candidate in (scan - 1, scan, scan + 1)
                    if scan_start <= candidate <= scan_end
                )
            missing_context = sorted(required_context - reviewed_set)
            if missing_context:
                errors.append(
                    "reviewed_scans is missing immediate in-range neighbour/context "
                    "scan(s): " + ", ".join(map(str, missing_context))
                )

    return errors, summary


def _resolve_root(raw_root: Path) -> Path:
    try:
        root = raw_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise LedgerError(f"validation root does not exist: {raw_root} ({error})") from error
    if not root.is_dir():
        raise LedgerError(f"validation root is not a directory: {raw_root}")
    return root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate canonical OCR late-amendment review evidence."
    )
    parser.add_argument("ledgers", nargs="+", type=Path, help="ledger JSON file(s)")
    parser.add_argument(
        "--root",
        type=Path,
        help="shared root for all relative evidence paths (default: each ledger parent)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    shared_root: Path | None = None
    if arguments.root is not None:
        try:
            shared_root = _resolve_root(arguments.root)
        except LedgerError as error:
            print(f"ERROR --root: {error}", file=sys.stderr)
            return 1

    valid_count = 0
    invalid_count = 0
    for ledger_argument in arguments.ledgers:
        label = ledger_argument.as_posix()
        try:
            ledger_path = ledger_argument.resolve(strict=True)
            if not ledger_path.is_file():
                raise LedgerError("ledger is not a regular file")
            document = _load_json(ledger_path)
            root = shared_root or _resolve_root(ledger_path.parent)
            errors, summary = validate_ledger(document, root)
        except (LedgerError, FileNotFoundError, OSError) as error:
            errors = [str(error)]
            summary = {
                "scan_start": 0,
                "scan_end": 0,
                "changed_scans": 0,
                "reviewed_scans": 0,
                "content_files": 0,
                "changes": 0,
            }

        if errors:
            invalid_count += 1
            print(f"INVALID {label}", file=sys.stderr)
            for error in errors:
                print(f"ERROR {label}: {error}", file=sys.stderr)
        else:
            valid_count += 1
            print(
                f"VALID {label} "
                f"range={summary['scan_start']}-{summary['scan_end']} "
                f"changed={summary['changed_scans']} "
                f"reviewed={summary['reviewed_scans']} "
                f"content_files={summary['content_files']} "
                f"changes={summary['changes']}"
            )

    summary_line = (
        f"SUMMARY ledgers={len(arguments.ledgers)} "
        f"valid={valid_count} invalid={invalid_count}"
    )
    print(summary_line, file=sys.stderr if invalid_count else sys.stdout)
    return 1 if invalid_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
