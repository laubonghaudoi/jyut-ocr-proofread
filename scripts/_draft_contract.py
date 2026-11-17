"""Semantic identity and capabilities of OCR inputs consumed downstream.

Native pre-contract drafts remain readable; new backends must state their
geometry contract rather than borrowing another engine's name.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

LAYOUT_CONTRACT = {"version": 1, "role": "layout",
                   "coordinateSystem": "normalized-top-left-xywh", "textJoin": "\n"}
SEMANTIC_KEYS = ("draftSemanticsSha256", "draft2SemanticsSha256")


def read_record(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def requires_semantics(record: Mapping[str, Any]) -> bool:
    profile = record.get("ocrProfile") or {}
    profile = profile if isinstance(profile, dict) else {}
    identity = " ".join(str(value) for value in
                        (record.get("engine"), record.get("backend"), profile.get("backend"), profile.get("vlBackend")))
    return bool(record.get("layoutContract")) or "vision" in identity.lower() or "mlx" in identity.lower()


def _semantic_value(value: Any) -> Any:
    """Drop execution bookkeeping and location-only fields recursively."""
    if isinstance(value, dict):
        return {key: _semantic_value(item) for key, item in value.items()
                if not (key.endswith(("Path", "File", "Directory", "Dir", "At", "Seconds"))
                        or key in {"path", "file", "directory", "dir", "execution", "provenance", "generatedAt", "timestamp", "endpoint", "url", "port", "pid"})}
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    return value


def semantic_digest(path: Path | None) -> str:
    """Hash only consumed identity/layout/text, never timing or self seals."""
    record = read_record(path)
    if not record:
        return ""
    value = {key: record.get(key) for key in
             ("engine", "backend", "model", "modelId", "ocrProfile", "layoutContract",
              "coordinateSystem", "layoutVersion", "visionProfile", "backendIdentity", "regions") if key in record}
    value["lines"] = [{key: line.get(key) for key in
                       ("text", "boundingBox", "blockLabel", "blockOrder", "lineIDs",
                        "furnitureCandidate") if key in line}
                      for line in record.get("lines") or [] if isinstance(line, dict)]
    return hashlib.sha256(json.dumps(_semantic_value(value), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def layout_problem(record: Mapping[str, Any], text: str | None = None) -> str | None:
    contract = record.get("layoutContract")
    if not isinstance(contract, dict) or any(contract.get(k) != v for k, v in LAYOUT_CONTRACT.items()):
        return "missing normalized top-left layout/text contract"
    if not isinstance(record.get("engine"), str) or not record["engine"].strip():
        return "missing actual engine identity"
    lines = record.get("lines")
    if not isinstance(lines, list):
        return "layout lines are not an array"
    for index, line in enumerate(lines):
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            return f"layout block {index} has no text"
        box = line.get("boundingBox")
        if (not isinstance(box, list) or len(box) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in box)):
            return f"layout block {index} has invalid geometry"
        x, y, width, height = box
        if min(box) < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
            return f"layout block {index} is outside normalized page coordinates"
        if not isinstance(line.get("blockLabel"), str) or not line["blockLabel"]:
            return f"layout block {index} has no label"
        if isinstance(line.get("blockOrder"), bool) or line.get("blockOrder") != index:
            return f"layout block {index} has inconsistent reading order"
    if text is not None and text != "\n".join(line["text"] for line in lines) + "\n":
        return "layout JSON text/order differs from TXT"
    return None
