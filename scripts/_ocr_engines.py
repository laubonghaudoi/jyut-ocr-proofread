"""Public OCR routes, internal engine identities and immutable book selection."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_ROUTE = "hunyuan-paddle"
DEFAULT_ENGINES = ("hunyuan", "paddle")
ROUTES = {DEFAULT_ROUTE: DEFAULT_ENGINES, "apple-vision": ("vision",), "hunyuan": ("hunyuan",)}
BOOK_ENGINE_RECORD = "ocr-engines.json"
ROUTE_GUIDANCE = "Choose --engine apple-vision or --engine hunyuan-paddle (Linux/WSL2 also supports --engine hunyuan)."


def require_supported_host(system: str | None = None, machine: str | None = None) -> None:
    system = platform.system() if system is None else system
    machine = platform.machine().lower() if machine is None else machine.lower()
    if system == "Darwin" and machine not in {"arm64", "aarch64"}:
        raise ValueError("Intel Macs are no longer supported. macOS requires Apple Silicon and a native arm64 "
                         "runtime; on Apple Silicon under Rosetta, rerun with native arm64 Python.")


def route_engines(request: str, *, system: str | None = None, machine: str | None = None) -> tuple[str, ...]:
    system = platform.system() if system is None else system
    require_supported_host(system, machine)
    if request not in ROUTES:
        raise ValueError(f"Retired or unknown OCR route {request!r}. {ROUTE_GUIDANCE}")
    if request == "apple-vision" and system != "Darwin":
        raise ValueError("Apple Vision requires Apple Silicon macOS; use --engine hunyuan-paddle on Linux/WSL2.")
    if request == "hunyuan" and system == "Darwin":
        raise ValueError("Hunyuan-only is no longer a macOS route. " + ROUTE_GUIDANCE)
    return ROUTES[request]


def _record_engines(values: Sequence[str]) -> tuple[str, ...]:
    names = tuple(values)
    if len(names) == 2 and set(names) == set(DEFAULT_ENGINES):
        return DEFAULT_ENGINES
    if names in (("vision",), ("hunyuan",)):
        return names
    raise ValueError("This configuration/book uses a retired or invalid OCR engine combination. "
                     + ROUTE_GUIDANCE + " Keep the old book unchanged and use a new work directory.")


def resolve_engines(request: str | None = None, *, single_engine: bool = False,
                    environ: Mapping[str, str] | None = None, book: Path | None = None,
                    system: str | None = None, machine: str | None = None) -> tuple[str, ...]:
    """Explicit route, saved route, book record, default; never reinterpret a retired book."""
    require_supported_host(system, machine)
    env = os.environ if environ is None else environ
    saved = read_book_engines(book) if book is not None else None
    if single_engine:
        if request not in (None, "hunyuan"):
            raise ValueError("--single-engine selects Hunyuan only; choose --engine apple-vision for Vision OCR.")
        request = "hunyuan"
    value = request or env.get("JYUT_OCR_ENGINE")
    if value is not None:
        names = route_engines(value, system=system, machine=machine)
    else:
        effective = env.get("JYUT_OCR_ENGINES")
        names = _record_engines(effective.split(",")) if effective else saved or DEFAULT_ENGINES
        route = next(key for key, engines in ROUTES.items() if engines == names)
        route_engines(route, system=system, machine=machine)
    if request is None and env.get("JYUT_OCR_ENGINES"):
        if _record_engines(env["JYUT_OCR_ENGINES"].split(",")) != names:
            raise ValueError("Saved OCR route and engines disagree; rerun setup. " + ROUTE_GUIDANCE)
    if saved is not None and saved != names:
        raise ValueError("This book was created with different OCR engines; use a new work directory for the new route.")
    return names


def engine_a(engines: Sequence[str]) -> str | None:
    return "paddle" if tuple(engines) == DEFAULT_ENGINES else None


def draft_a_directory(book: Path, engines: Sequence[str]) -> Path:
    return book / ("ocr-vision" if tuple(engines) == ("vision",) else
                   "ocr-paddle" if engine_a(engines) else "ocr-hunyuan")


def read_book_engine_record(book: Path) -> dict:
    path = book / "reports" / BOOK_ENGINE_RECORD
    if not path.exists():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schemaVersion") != 1:
            raise ValueError("invalid engine record schema")
        record["engines"] = list(_record_engines(record["engines"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Cannot resume {path}: {exc}") from exc
    return record


def read_book_engines(book: Path) -> tuple[str, ...] | None:
    record = read_book_engine_record(book)
    return tuple(record["engines"]) if record else None


def recorded_draft_directory(book: Path, role: str) -> Path | None:
    value = read_book_engine_record(book).get("draft" + role)
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else book / path
