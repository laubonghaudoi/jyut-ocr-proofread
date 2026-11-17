"""Small shared engine-selection contract for installation and book runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_ENGINES = ("hunyuan", "paddle")
VISION_DUAL = ("vision", "hunyuan")
BOOK_ENGINE_RECORD = "ocr-engines.json"


def resolve_engines(request: str | None = None, *, single_engine: bool = False,
                    environ: Mapping[str, str] | None = None, book: Path | None = None) -> tuple[str, ...]:
    """CLI, then machine env, book record, native default; --single-engine is B-only."""
    env = os.environ if environ is None else environ
    saved = read_book_engines(book) if book is not None and request is None and "JYUT_OCR_ENGINES" not in env else None
    value = request if request is not None else env.get("JYUT_OCR_ENGINES", ",".join(saved or DEFAULT_ENGINES))
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    if value == "auto":
        names = DEFAULT_ENGINES
    if len(names) != len(set(names)):
        raise ValueError("duplicate OCR engines")
    if set(names) == set(DEFAULT_ENGINES):
        names = DEFAULT_ENGINES
    elif set(names) == set(VISION_DUAL):
        names = VISION_DUAL
    elif names not in (("hunyuan",), ("vision",)):
        raise ValueError("engines must be hunyuan,paddle; vision,hunyuan; hunyuan; or vision")
    if single_engine:
        if request is not None and names != ("hunyuan",):
            raise ValueError("--single-engine cannot be combined with a different --engines selection")
        return ("hunyuan",)
    return names


def engine_a(engines: Sequence[str]) -> str | None:
    return "vision" if tuple(engines) == VISION_DUAL else "paddle" if "paddle" in engines else None


def draft_a_directory(book: Path, engines: Sequence[str]) -> Path:
    return book / ("ocr-vision" if "vision" in engines else
                   "ocr-paddle" if engine_a(engines) == "paddle" else "ocr-hunyuan")


def read_book_engine_record(book: Path) -> dict:
    try:
        record = json.loads((book / "reports" / BOOK_ENGINE_RECORD).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schemaVersion") != 1:
            return {}
        record["engines"] = list(resolve_engines(",".join(record["engines"]), environ={}))
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def read_book_engines(book: Path) -> tuple[str, ...] | None:
    record = read_book_engine_record(book)
    return tuple(record["engines"]) if record else None


def recorded_draft_directory(book: Path, role: str) -> Path | None:
    value = read_book_engine_record(book).get("draft" + role)
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else book / path
