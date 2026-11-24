"""Shared datatypes, path helpers, and page selection for local OCR runners."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jyut_ocr.core.common import canonical_json_bytes, sha256_bytes

PAGE_TOKEN = re.compile(r"(?P<first>[1-9][0-9]*)(?:-(?P<last>[1-9][0-9]*))?\Z")


class ConfigurationError(ValueError):
    """A command-line or filesystem configuration is unsafe or invalid."""


class PageOCRFailure(RuntimeError):
    """The OCR engine rejected one page while the runner remains usable."""


@dataclass(frozen=True)
class PageInput:
    scan_page: int
    render_file: Path
    render_sha256: str
    json_output: Path
    text_output: Path


def profile_sha256(
    profile: Mapping[str, Any], *, trailing_newline: bool = False
) -> str:
    """Deterministic SHA-256 digest of a canonical compact runner profile."""
    return sha256_bytes(
        canonical_json_bytes(profile, pretty=False, trailing_newline=trailing_newline)
    )


def render_path(render_directory: Path, scan_page: int) -> Path:
    return render_directory / f"page-{scan_page:04d}.png"


def output_paths(output_directory: Path, scan_page: int) -> tuple[Path, Path]:
    stem = f"page-{scan_page:04d}"
    return output_directory / f"{stem}.json", output_directory / f"{stem}.txt"


def path_is_within(
    candidate: Path, directory: Path, *, resolve: bool = False
) -> bool:
    if resolve:
        candidate = candidate.resolve()
        directory = directory.resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        return False
    return True


def text_from_lines(lines: Sequence[Mapping[str, Any]]) -> bytes:
    return ("\n".join(str(line["text"]) for line in lines) + "\n").encode("utf-8")


def rendered_pages(render_directory: Path) -> list[int]:
    """Every scan page that has a page-NNNN.png render; the default selection."""
    pages: list[int] = []
    for path in render_directory.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d{4})\.png", path.name)
        if match:
            pages.append(int(match.group(1)))
    return sorted(pages)


def parse_pages_expression(expression: str) -> list[int]:
    pages: set[int] = set()
    for token in expression.split(","):
        token = token.strip()
        if not token:
            continue
        match = PAGE_TOKEN.match(token)
        if match is None:
            raise ConfigurationError(f"invalid page selector: {token!r}")
        first = int(match.group("first"))
        last = int(match.group("last") or first)
        if last < first:
            raise ConfigurationError(f"descending page range: {token!r}")
        pages.update(range(first, last + 1))
    if not pages:
        raise ConfigurationError("no pages were selected")
    return sorted(pages)


def selected_pages(
    arguments: argparse.Namespace, render_directory: Path
) -> tuple[list[int], str]:
    if arguments.pages is not None:
        if arguments.last_page is not None:
            raise ConfigurationError("--last-page cannot be combined with --pages")
        return parse_pages_expression(arguments.pages), "explicit-pages"
    if arguments.first_page is None and arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ConfigurationError(
                f"no page-NNNN.png renders in {render_directory}"
            )
        return pages, "all-renders"
    if arguments.first_page is None:
        raise ConfigurationError("--last-page needs --first-page")
    if arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ConfigurationError(
                f"no page-NNNN.png renders in {render_directory}"
            )
        arguments.last_page = pages[-1]
    if arguments.first_page <= 0 or arguments.last_page <= 0:
        raise ConfigurationError("page numbers must be positive")
    if arguments.last_page < arguments.first_page:
        raise ConfigurationError("--last-page must be at least --first-page")
    return list(range(arguments.first_page, arguments.last_page + 1)), "page-range"
