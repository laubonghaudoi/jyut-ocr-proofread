"""Shared low-level hashing, timestamp, and canonical JSON helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def utc_now(*, milliseconds: bool = False) -> str:
    """Current UTC timestamp in ISO-8601 Z format."""
    if milliseconds:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 digest of DATA."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 digest of PATH, streamed in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(
    value: Any, *, pretty: bool = True, trailing_newline: bool = False
) -> bytes:
    """Deterministic UTF-8 JSON encoding with sorted keys."""
    if pretty:
        text = json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
        )
    else:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if trailing_newline:
        text += "\n"
    return text.encode("utf-8")
