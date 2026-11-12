#!/usr/bin/env python3
"""Phase 1: render a source PDF (or stage a scan image folder) and seal a page manifest.

This is the survey pass.  It produces the two artifacts every later phase reads
from disk rather than from conversation context:

    page-manifest.json   the sealed scan inventory (schema per _ocr_markdown.load_page_manifest)
    survey-report.json   render provenance, geometry stats and anomaly candidates

Why files and not context: a long book cannot be proofread inside one context
window, and an agent harness auto-compacts.  Phase 2 (OCR) and phase 3
(proofreading) must therefore be able to start from disk alone.

The manifest deliberately carries only what the page-manifest schema defines.  Anomaly
candidates go in the survey report instead, because the skill requires a blank,
duplicate or rotated page to be confirmed against the page image before anything
is recorded as fact.  In particular this script never sets `marker_expected:
false` on its own: a low-ink page is a candidate for visual review, not a
verified blank.

Usage:
    python3 scripts/survey_pdf.py SOURCE.pdf OUTPUT_DIR [--dpi 300] [--jobs N]
    python3 scripts/survey_pdf.py SOURCE.pdf OUTPUT_DIR --renders-only
    python3 scripts/survey_pdf.py OUTPUT_DIR --images-dir IMAGES_DIR [--jobs N]

OUTPUT_DIR is the book folder: the renders go to OUTPUT_DIR/renders/, the two
files beside them.  When `--images-dir IMAGES_DIR` is supplied instead of a
source PDF, pre-rendered `page-NNNN.png` scans are copied (if outside
`OUTPUT_DIR/renders/`) and sealed under `source_kind: "images-dir"`, with
`source_sha256` computed over the canonical `(scan_page, render_sha256)`
inventory (`_ocr_markdown.images_inventory_sha256`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence

MANIFEST_NAME = "page-manifest.json"
REPORT_NAME = "survey-report.json"
RENDERS_DIRNAME = "renders"
DEFAULT_DPI = 300
DEFAULT_JOBS = os.cpu_count() or 1

BLANK_INK_FRACTION = 0.03
SPREAD_RATIO_TOLERANCE = 0.15


class SurveyError(RuntimeError):
    """The survey cannot proceed."""


@dataclass(frozen=True)
class PageFacts:
    scan_page: int
    render_file: Path
    render_sha256: str
    width: int
    height: int
    ink_fraction: float | None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
    ).encode("utf-8")


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SurveyError(f"{name} is required (install poppler-utils)")
    return path


def pdftoppm_version() -> str:
    """First line of `pdftoppm -v` (poppler's version), recorded in the survey report."""
    tool = shutil.which("pdftoppm")
    if not tool:
        return "pdftoppm not installed"
    try:
        said = subprocess.run([tool, "-v"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return f"{tool} (version unknown)"
    first = next(
        (line.strip() for line in (said.stderr + said.stdout).splitlines() if line.strip()),
        "",
    )
    return first or f"{tool} (version unknown)"


def pdf_facts(pdf_path: Path) -> dict[str, Any]:
    """Page count and nominal page size straight from pdfinfo."""
    result = subprocess.run(
        [require_tool("pdfinfo"), str(pdf_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SurveyError(f"pdfinfo failed: {(result.stderr or '').strip()}")
    facts: dict[str, Any] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        facts[key.strip()] = value.strip()
    if "Pages" not in facts:
        raise SurveyError("pdfinfo did not report a page count")
    try:
        facts["_page_count"] = int(facts["Pages"])
    except ValueError as error:
        raise SurveyError(f"unreadable page count: {facts['Pages']}") from error
    if facts["_page_count"] < 1:
        raise SurveyError("PDF reports no pages")
    return facts


PAGE_TOKEN = re.compile(r"\A(?P<first>\d+)(?:-(?:(?P<last>\d+)))?\Z")
IMAGE_FILE_RE = re.compile(r"\Apage-(\d+)\.png\Z")


def parse_page_selection(expression: str) -> list[int]:
    pages: set[int] = set()
    for token in expression.split(","):
        token = token.strip()
        if not token:
            continue
        match = PAGE_TOKEN.match(token)
        if match is None:
            raise SurveyError(f"invalid page selector: {token!r}")
        first = int(match.group("first"))
        last = int(match.group("last") or first)
        if last < first:
            raise SurveyError(f"descending page range: {token!r}")
        pages.update(range(first, last + 1))
    if not pages:
        raise SurveyError("no pages were selected")
    return sorted(pages)


def contiguous_runs(numbers: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for number in sorted(set(numbers)):
        if runs and number == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], number)
        else:
            runs.append((number, number))
    return runs


def render_slices(runs: list[tuple[int, int]], jobs: int) -> list[tuple[int, int]]:
    total = sum(last - first + 1 for first, last in runs)
    size = max(1, math.ceil(total / max(1, jobs)))
    slices: list[tuple[int, int]] = []
    for first, last in runs:
        start = first
        while start <= last:
            end = min(last, start + size - 1)
            slices.append((start, end))
            start = end + 1
    return slices


def render_runs(
    pdf_path: Path, renders_dir: Path, dpi: int, runs: list[tuple[int, int]], jobs: int = 1
) -> int:
    """Render the page runs with up to JOBS pdftoppm processes at once, then normalise
    the names to fixed 4-digit page-NNNN.png."""
    renders_dir.mkdir(parents=True, exist_ok=True)
    pdftoppm = require_tool("pdftoppm")
    slices = render_slices(runs, jobs)
    with tempfile.TemporaryDirectory(dir=str(renders_dir)) as staging:

        def render(numbered: tuple[int, tuple[int, int]]) -> Path:
            index, (first, last) = numbered
            slice_dir = Path(staging) / f"slice-{index:04d}"
            slice_dir.mkdir()
            result = subprocess.run(
                [
                    pdftoppm,
                    "-r",
                    str(dpi),
                    "-png",
                    "-f",
                    str(first),
                    "-l",
                    str(last),
                    str(pdf_path),
                    str(slice_dir / "page"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise SurveyError(
                    f"pdftoppm failed on pages {first}-{last}: {(result.stderr or '').strip()}"
                )
            return slice_dir

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(jobs, len(slices)))
        ) as pool:
            slice_dirs = list(pool.map(render, enumerate(slices)))
        produced = sorted(item for folder in slice_dirs for item in folder.glob("page-*.png"))
        if not produced:
            raise SurveyError("pdftoppm produced no output")
        pattern = re.compile(r"page-(\d+)\.png\Z")
        for item in produced:
            match = pattern.match(item.name)
            if match is None:
                raise SurveyError(f"unexpected render name: {item.name}")
            number = int(match.group(1))
            os.replace(item, renders_dir / f"page-{number:04d}.png")
    return len(slices)


def discover_image_pages(images_dir: Path) -> dict[int, Path]:
    """Return `{scan_page: path}` for all `page-NNNN.png` files in IMAGES_DIR."""
    if not images_dir.is_dir():
        raise SurveyError(f"--images-dir does not exist or is not a directory: {images_dir}")
    found: dict[int, Path] = {}
    for entry in sorted(images_dir.iterdir()):
        match = IMAGE_FILE_RE.match(entry.name)
        if match is None or not entry.is_file():
            continue
        scan_page = int(match.group(1))
        if scan_page < 1:
            raise SurveyError(f"scan_page must be >= 1: {entry.name}")
        if scan_page in found:
            raise SurveyError(f"duplicate scan_page {scan_page} in {images_dir}")
        found[scan_page] = entry
    if not found:
        raise SurveyError(f"no page-NNNN.png files found in {images_dir}")
    return found


def stage_image_pages(
    source_pages: dict[int, Path],
    renders_dir: Path,
    selected: Sequence[int],
    overwrite: bool,
) -> int:
    """Ensure selected `page-NNNN.png` files exist in `renders_dir`."""
    renders_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for scan_page in selected:
        src = source_pages.get(scan_page)
        if src is None:
            raise SurveyError(f"requested page {scan_page} is missing in source images directory")
        dst = renders_dir / f"page-{scan_page:04d}.png"
        if src.resolve() == dst.resolve():
            continue
        if dst.is_file() and not overwrite:
            continue
        with tempfile.NamedTemporaryFile(
            prefix=f".page-{scan_page:04d}.", suffix=".png", dir=str(renders_dir), delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            shutil.copyfile(src, tmp_path)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, dst)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
        copied += 1
    return copied


RULE_COVERAGE = 0.5
INK_MARGIN = 45


def measure_page(render_file: Path) -> tuple[int, int, float | None]:
    """Pixel size and the fraction of the page carrying TEXT ink."""
    try:
        from PIL import Image
    except ImportError:
        return 0, 0, None
    with Image.open(render_file) as image:
        width, height = image.size
        grey = image.convert("L")
        grey.thumbnail((400, 400))
        histogram = grey.histogram()
        if not sum(histogram):
            return width, height, 0.0
        paper = max(range(len(histogram)), key=lambda level: histogram[level])
        cutoff = paper - INK_MARGIN
        if cutoff <= 0:
            return width, height, None
        binary = grey.point(lambda level: 255 if level < cutoff else 0)
        small_width, small_height = binary.size
        rows = binary.tobytes()
        columns = binary.transpose(Image.ROTATE_90).tobytes()

    row_ink = [
        rows[y * small_width : (y + 1) * small_width].count(255) for y in range(small_height)
    ]
    column_ink = [
        columns[x * small_height : (x + 1) * small_height].count(255) for x in range(small_width)
    ]
    rule_columns = sum(1 for ink in column_ink if ink >= small_height * RULE_COVERAGE)
    text_rows = [ink for ink in row_ink if ink < small_width * RULE_COVERAGE]
    if not text_rows:
        return width, height, 0.0
    text_ink = sum(max(0, ink - rule_columns) for ink in text_rows)
    area = len(text_rows) * max(1, small_width - rule_columns)
    return width, height, text_ink / area


def page_facts(renders_dir: Path, scan_page: int) -> PageFacts:
    render_file = renders_dir / f"page-{scan_page:04d}.png"
    if not render_file.is_file():
        raise SurveyError(f"render missing after rendering: {render_file}")
    width, height, ink = measure_page(render_file)
    return PageFacts(
        scan_page=scan_page,
        render_file=render_file,
        render_sha256=sha256_file(render_file),
        width=width,
        height=height,
        ink_fraction=ink,
    )


def measure_pages(
    renders_dir: Path, selected: list[int], jobs: int, last_page: int
) -> list[PageFacts]:
    pages: list[PageFacts] = []

    def note(facts: PageFacts) -> None:
        pages.append(facts)
        if facts.scan_page % 50 == 0:
            print(f"  measured   : {facts.scan_page}/{last_page}", flush=True)

    if jobs <= 1 or len(selected) < 2:
        for scan_page in selected:
            note(page_facts(renders_dir, scan_page))
        return pages
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=min(jobs, len(selected))
    ) as pool:
        for facts in pool.map(functools.partial(page_facts, renders_dir), selected, chunksize=4):
            note(facts)
    return pages


def images_inventory_digest(pages: Sequence[PageFacts]) -> str:
    payload = "\n".join(f"{p.scan_page}:{p.render_sha256}" for p in pages).encode("utf-8")
    return sha256_bytes(payload)


def build_manifest(
    pdf_path: Path | None,
    source_sha: str,
    pages: list[PageFacts],
    manifest_path: Path,
    *,
    source_kind: str = "pdf",
) -> dict[str, Any]:
    records = [
        {
            "scan_page": page.scan_page,
            "render_file": os.path.relpath(page.render_file, manifest_path.parent),
            "render_sha256": page.render_sha256,
        }
        for page in pages
    ]
    manifest: dict[str, Any] = {
        "source_pdf": str(pdf_path) if pdf_path is not None else "",
        "source_sha256": source_sha,
        "expected_scan_pages": len(pages),
        "generated_at": utc_now(),
        "pages": records,
    }
    if source_kind != "pdf":
        manifest["source_kind"] = source_kind
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="SOURCE_PDF OUTPUT_DIR (PDF mode) or OUTPUT_DIR (with --images-dir)",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="survey pre-rendered page-NNNN.png images without a source PDF",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=None,
        help=f"render resolution (default {DEFAULT_DPI}; --renders-only: the survey report's)",
    )
    parser.add_argument(
        "--last-page",
        type=int,
        help="survey only pages 1..N (smoke tests); default is the whole document",
    )
    parser.add_argument(
        "--pages",
        help="survey an explicit set, e.g. 1,30,80-85",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="re-render or re-copy pages that already exist",
    )
    parser.add_argument(
        "--renders-only",
        action="store_true",
        help="render only the pages missing from OUTPUT_DIR/renders/ for the page manifest already in OUTPUT_DIR",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"processes rendering/measuring at once (default {DEFAULT_JOBS})",
    )
    return parser


def renders_only(
    pdf_path: Path,
    pdf_sha: str,
    output_directory: Path,
    renders_dir: Path,
    selected: list[int] | None,
    dpi: int | None,
    jobs: int,
    overwrite: bool,
) -> int:
    manifest_path = output_directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as error:
        raise SurveyError(f"--renders-only needs the page manifest at {manifest_path} ({error})")
    if not isinstance(manifest, dict) or manifest.get("source_sha256") != pdf_sha:
        raise SurveyError(
            f"{pdf_path} is not the PDF {manifest_path} was made from (its SHA-256 differs)"
        )
    records = {
        p["scan_page"]: p
        for p in manifest.get("pages") or []
        if isinstance(p, dict) and isinstance(p.get("scan_page"), int)
    }
    if not records:
        raise SurveyError(f"{manifest_path} lists no page")
    wanted = sorted(records) if selected is None else sorted(selected)
    recorded: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError, TypeError, AttributeError):
        recorded = (
            json.loads((output_directory / REPORT_NAME).read_text(encoding="utf-8")).get("render")
            or {}
        )
    if dpi is None:
        dpi = DEFAULT_DPI
        with contextlib.suppress(ValueError, KeyError, TypeError):
            dpi = int(recorded["dpi"])
    renders_dir.mkdir(parents=True, exist_ok=True)
    existing = {
        int(p.stem.split("-")[1])
        for p in renders_dir.glob("page-*.png")
        if p.stem.split("-")[-1].isdigit()
    }
    todo = wanted if overwrite else sorted(set(wanted) - existing)
    if todo:
        runs = contiguous_runs(todo)
        shown = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs[:6])
        print(
            f"  rendering  : {len(todo)} page(s) [{shown}] at {dpi} dpi, {jobs} pdftoppm process(es) at a time",
            flush=True,
        )
        render_runs(pdf_path, renders_dir, dpi, runs, jobs)
    else:
        print(f"  rendering  : all {len(wanted)} selected page(s) already present", flush=True)
    differ, missing, unlisted = [], [], []
    for scan in wanted:
        record = records.get(scan)
        path = renders_dir / f"page-{scan:04d}.png"
        if record is None:
            unlisted.append(scan)
        elif not path.is_file():
            missing.append(scan)
        elif sha256_file(path) != record.get("render_sha256"):
            differ.append(scan)
    verified = len(wanted) - len(differ) - len(missing) - len(unlisted)
    print(
        f"  verified   : {verified} of {len(wanted)} page(s) hash to {manifest_path.name}'s render_sha256; "
        f"{manifest_path.name} and {REPORT_NAME} not rewritten",
        flush=True,
    )
    if unlisted:
        print(
            f"  NOTE {len(unlisted)} selected page(s) not in the manifest, so not verified: {unlisted[:10]}",
            flush=True,
        )
    if differ or missing:
        surveyed = recorded.get("tool_version") or "not recorded"
        raise SurveyError(
            f"{len(differ)} render(s) differ from the manifest {differ[:10]}, {len(missing)} missing "
            f"{missing[:10]}: another pdftoppm or dpi than the survey's? (pdftoppm now: "
            f"{pdftoppm_version()}; at the survey: {surveyed})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    dpi = DEFAULT_DPI if arguments.dpi is None else arguments.dpi
    try:
        if arguments.images_dir is not None:
            if len(arguments.paths) != 1:
                raise SurveyError(
                    "--images-dir takes exactly one positional argument: OUTPUT_DIR"
                )
            source_pdf_arg: Path | None = None
            output_arg = arguments.paths[0]
        else:
            if len(arguments.paths) != 2:
                raise SurveyError(
                    "expected SOURCE_PDF OUTPUT_DIR (or OUTPUT_DIR --images-dir IMAGES_DIR)"
                )
            source_pdf_arg, output_arg = arguments.paths[0], arguments.paths[1]

        if arguments.jobs < 1:
            raise SurveyError("--jobs must be at least 1")
        if arguments.pages and arguments.last_page:
            raise SurveyError("--pages cannot be combined with --last-page")

        output_directory = output_arg.expanduser().resolve()
        renders_dir = output_directory / RENDERS_DIRNAME
        output_directory.mkdir(parents=True, exist_ok=True)

        if arguments.images_dir is not None:
            if arguments.renders_only:
                raise SurveyError("--renders-only requires a source PDF")
            images_dir = arguments.images_dir.expanduser().resolve()
            source_pages = discover_image_pages(images_dir)
            available_pages = sorted(source_pages)
            page_count = len(available_pages)
            if arguments.pages:
                selected = sorted(parse_page_selection(arguments.pages))
                outside = [n for n in selected if n not in source_pages]
                if outside:
                    raise SurveyError(
                        f"--pages {outside[:5]} missing from {images_dir}"
                    )
            elif arguments.last_page is not None:
                if arguments.last_page < 1:
                    raise SurveyError("--last-page must be >= 1")
                selected = [n for n in available_pages if n <= arguments.last_page]
                if not selected:
                    raise SurveyError(f"no pages <= {arguments.last_page} in {images_dir}")
            else:
                selected = available_pages
            last_page = max(selected)
            print(f"  source     : {images_dir} ({page_count} image page(s))", flush=True)
            copied = stage_image_pages(source_pages, renders_dir, selected, arguments.overwrite)
            print(
                f"  staging    : {copied} image(s) copied into {renders_dir}",
                flush=True,
            )
            pages = measure_pages(renders_dir, selected, arguments.jobs, last_page)
            source_sha = images_inventory_digest(pages)
            print(f"  sha256     : {source_sha[:16]}… (images inventory)", flush=True)
            source_kind = "images-dir"
            pdf_path: Path | None = None
            facts: dict[str, Any] = {}
        else:
            assert source_pdf_arg is not None
            pdf_path = source_pdf_arg.expanduser().resolve()
            if not pdf_path.is_file():
                raise SurveyError(f"source PDF does not exist: {pdf_path}")
            if dpi < 72:
                raise SurveyError("--dpi below 72 is too low for small CJK glyphs")
            facts = pdf_facts(pdf_path)
            page_count = facts["_page_count"]
            if arguments.pages:
                selected = sorted(parse_page_selection(arguments.pages))
                outside = [n for n in selected if n < 1 or n > page_count]
                if outside:
                    raise SurveyError(
                        f"--pages {outside[:5]} outside the document's 1..{page_count}"
                    )
                last_page = max(selected)
            else:
                last_page = arguments.last_page or page_count
                if last_page < 1 or last_page > page_count:
                    raise SurveyError(
                        f"--last-page {last_page} outside the document's 1..{page_count}"
                    )
                selected = list(range(1, last_page + 1))

            print(f"  source     : {pdf_path.name} ({page_count} pages)", flush=True)
            source_sha = sha256_file(pdf_path)
            print(f"  sha256     : {source_sha[:16]}…", flush=True)
            if arguments.renders_only:
                return renders_only(
                    pdf_path,
                    source_sha,
                    output_directory,
                    renders_dir,
                    selected if (arguments.pages or arguments.last_page) else None,
                    arguments.dpi,
                    arguments.jobs,
                    arguments.overwrite,
                )

            existing = (
                {
                    int(p.stem.split("-")[1])
                    for p in renders_dir.glob("page-*.png")
                    if p.stem.split("-")[-1].isdigit()
                }
                if renders_dir.is_dir()
                else set()
            )
            wanted = set(selected)
            todo = sorted(wanted) if arguments.overwrite else sorted(wanted - existing)

            if todo:
                runs = contiguous_runs(todo)
                shown = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs[:6])
                if len(runs) > 6:
                    shown += f", … {len(runs) - 6} more runs"
                print(
                    f"  rendering  : {len(todo)} page(s) [{shown}] at {dpi} dpi, "
                    f"{arguments.jobs} pdftoppm process(es) at a time",
                    flush=True,
                )
                render_runs(pdf_path, renders_dir, dpi, runs, arguments.jobs)
            else:
                print(
                    f"  rendering  : all {len(wanted)} selected page(s) already present",
                    flush=True,
                )

            pages = measure_pages(renders_dir, selected, arguments.jobs, last_page)
            source_kind = "pdf"
    except SurveyError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    manifest_path = output_directory / MANIFEST_NAME
    manifest = build_manifest(
        pdf_path, source_sha, pages, manifest_path, source_kind=source_kind
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)

    ratios = [p.height / p.width for p in pages if p.width and p.height]
    median_ratio = sorted(ratios)[len(ratios) // 2] if ratios else 0.0
    blank_candidates = [
        p.scan_page
        for p in pages
        if p.ink_fraction is not None and p.ink_fraction < BLANK_INK_FRACTION
    ]
    spread_candidates = [
        p.scan_page
        for p in pages
        if p.width
        and p.height
        and median_ratio
        and abs((p.height / p.width) - median_ratio) / median_ratio > SPREAD_RATIO_TOLERANCE
    ]
    by_hash: dict[str, list[int]] = {}
    for page in pages:
        by_hash.setdefault(page.render_sha256, []).append(page.scan_page)
    duplicate_renders = [v for v in by_hash.values() if len(v) > 1]

    render_meta: dict[str, Any]
    if source_kind == "images-dir":
        render_meta = {
            "tool": "images-dir",
            "source_images_dir": str(arguments.images_dir.expanduser().resolve()),
            "format": "png",
            "directory": str(renders_dir),
            "name_pattern": "page-NNNN.png",
        }
    else:
        render_meta = {
            "tool": "pdftoppm",
            "tool_version": pdftoppm_version(),
            "dpi": dpi,
            "format": "png",
            "directory": str(renders_dir),
            "name_pattern": "page-NNNN.png",
        }

    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "source_kind": source_kind,
        "source_pdf": str(pdf_path) if pdf_path is not None else "",
        "source_sha256": source_sha,
        "pdf_page_count": page_count,
        "surveyed_pages": len(pages),
        "render": render_meta,
        "pdf_info": {k: v for k, v in facts.items() if not k.startswith("_")},
        "geometry": {
            "median_height_over_width": round(median_ratio, 4),
            "distinct_sizes": sorted({f"{p.width}x{p.height}" for p in pages if p.width}),
        },
        "candidates_needing_visual_confirmation": {
            "note": "candidates only; confirm against the page image before "
            "recording any of these as fact (e.g. marker_expected=false)",
            "blank_pages": blank_candidates,
            "spread_pages": spread_candidates,
            "duplicate_renders": duplicate_renders,
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": sha256_bytes(manifest_bytes),
        },
    }
    report_path = output_directory / REPORT_NAME
    report_path.write_bytes(canonical_json_bytes(report))

    print(f"  manifest   : {manifest_path}", flush=True)
    print(f"  report     : {report_path}", flush=True)
    print(
        f"  candidates : {len(blank_candidates)} blank, "
        f"{len(spread_candidates)} spread, {len(duplicate_renders)} duplicate "
        "(all need visual confirmation)",
        flush=True,
    )
    if len(pages) != page_count:
        print(
            f"  NOTE surveyed {len(pages)} of {page_count} pages; this manifest "
            "does not describe the whole document",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
