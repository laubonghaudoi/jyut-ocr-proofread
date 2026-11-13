"""The book repair tool's inputs (scripts/repair_book.py): where they are,
what they must hold before anything is repaired, and their hashes.

Everything here reads; nothing writes.  The inputs are the sealed pages
(proofread/page-NNNN.{md,json}), the annotated assembly and its page list,
both engine drafts, the renders, the book profile and the page manifest.  The
tool refuses (InputError, exit 2) when:

- a sealed page's Markdown or JSON does not match its own seal (the hashes
  phase 3 writes in `provenance`), or the page list's recorded hashes;
- the assembled page text is not the sealed page text (both `strip()`ped),
  or the assembly's page markers are not the marker pages;
- a render's hash is not the page manifest's.

and it refuses an output folder (OutputError) that is, or lies inside, an
input folder, or that already exists without --resume (or, with it, that
this tool did not write, or that holds a link where the tool writes).
hash_inputs() hashes
every file read, before the run and again after it, so a run that changed
an input is reported, whoever changed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import proofread_pages as pp
from _ocr_markdown import PAGE_MARKER_RE, load_page_manifest


# The skill's book layout (SKILL.md, long-book-workflow.md): what each input
# defaults to under the book folder.  The page manifest the assembly was made
# with sits beside it; a book assembled without one keeps the render-time
# manifest at the book's root.
LAYOUT = {
    "assembled": ("assembled/combined-annotated.md",),
    "page_sources": ("assembled/page-sources.json",),
    "proofread": ("proofread",),
    "renders": ("renders",),
    "ocr_a": ("ocr-paddle",),
    "ocr_b": ("ocr-hunyuan",),
    "profile": ("book-profile.json",),
    "page_manifest": ("assembled/page-manifest.json", "page-manifest.json"),
}
# The folders the tool never writes in: sealed or produced upstream.
SEALED_FOLDERS = ("proofread", "assembled", "renders", "ocr_a", "ocr_b")
# What an output folder the tool wrote carries, so --resume never reuses a
# folder something else made.
REPORT_NAME = "repair-report.json"
TOOL_NAME = "repair_book"
# What a run writes in its output folder, besides the report: files (with
# those an earlier version wrote there: its plan, its questions and the
# model's answers), and the folder it writes files in.  A resumed run
# removes an earlier run's before it writes (repair_book.py), so the folder
# never mixes two runs; an earlier version's question crops (crops/) go with
# the finish's cleanup (clean_book.py).
RUN_FILES = ("combined-repaired.md", "repair-log.jsonl", "repair-plan.jsonl", "doubts.json", "lint.json",
             "book-model.json", "boundaries.json", "boundary-evidence.json", "final.md", "final-audit.json",
             "questions.json", "answers.jsonl")
RUN_FOLDERS = ("pages",)


class InputError(Exception):
    """The inputs failed verification (exit 2)."""


class OutputError(Exception):
    """The output folder is refused: EXISTS is True when it already exists
    (exit 3), False when it lies in or is an input folder (exit 2)."""

    def __init__(self, message: str, exists: bool):
        super().__init__(message)
        self.exists = exists


@dataclass
class InputPaths:
    book: Path
    assembled: Path | None
    page_sources: Path | None
    proofread: Path
    renders: Path
    ocr_a: Path
    ocr_b: Path
    profile: Path | None
    page_manifest: Path | None
    # Whether the assembly was named on the command line (--assembled): with
    # --pages and no --assembled the annotated text is built from the pages.
    assembled_named: bool = False

    @classmethod
    def resolve(cls, book: Path, given: Mapping[str, Path | None]) -> "InputPaths":
        """Each input as given, else the book layout's first existing default
        (the folders' first default whether or not it exists)."""
        book = book.expanduser().resolve()
        found: dict[str, Path | None] = {}
        for key, defaults in LAYOUT.items():
            value = given.get(key)
            if value is not None:
                found[key] = Path(value).expanduser().resolve()
                continue
            candidates = [book / d for d in defaults]
            existing = [c for c in candidates if c.exists()]
            if key in ("proofread", "renders", "ocr_a", "ocr_b"):
                found[key] = (existing or candidates)[0]
            else:
                found[key] = existing[0] if existing else None
        return cls(book=book, assembled_named=given.get("assembled") is not None, **found)  # type: ignore[arg-type]

    def folders(self) -> dict[str, Path]:
        """The folders the tool never writes in, by name: the sealed and
        upstream folders it reads, the folder of every input file but the
        book folder itself (the page list and manifest sit in assembled/
        before there is an assembly), and the layout's own sealed folders
        under the book - proofread/, assembled/, renders/ and every ocr-*
        folder - whether or not this run reads them."""
        out = {}
        for key in SEALED_FOLDERS:
            value = getattr(self, key)
            if value is None:
                continue
            out[key] = value.parent if key == "assembled" else value
        for key in ("page_sources", "page_manifest", "profile"):
            value = getattr(self, key)
            if value is not None and value.parent.resolve() != self.book:
                out.setdefault(f"{key} folder", value.parent)
        for name in ("proofread", "assembled", "renders"):
            out.setdefault(f"layout {name}", self.book / name)
        if self.book.is_dir():
            for folder in sorted(self.book.glob("ocr-*")):
                if folder.is_dir():
                    out.setdefault(f"layout {folder.name}", folder)
        return out


def page_stem(scan: int) -> str:
    return f"page-{scan:04d}"


@dataclass
class Page:
    """One sealed page and its engine drafts.  sealed_text is the page's
    Markdown exactly as sealed; record its sealed JSON."""
    scan: int
    sealed_text: str
    record: dict[str, Any]
    marker: bool
    paths: dict[str, Path]
    _a_text: str | None = field(default=None, repr=False)
    _a_record: dict[str, Any] | None = field(default=None, repr=False)
    _b_text: str | None = field(default=None, repr=False)

    def engine_a_text(self) -> str:
        if self._a_text is None:
            self._a_text = _read_text(self.paths["a_txt"])
        return self._a_text

    def engine_a_record(self) -> dict[str, Any]:
        if self._a_record is None:
            try:
                self._a_record = json.loads(self.paths["a_json"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._a_record = {}
        return self._a_record

    def engine_a_blocks(self) -> list[dict[str, Any]]:
        """Engine A's layout blocks (phase 3's load_blocks), each with its
        position in the layout (`index`)."""
        blocks = pp.load_blocks(self.paths["a_json"] if self.paths["a_json"].is_file() else None)
        for k, block in enumerate(blocks):
            block["index"] = k
        return blocks

    def engine_a_size(self) -> tuple[int, int] | None:
        record = self.engine_a_record()
        width, height = record.get("renderWidth"), record.get("renderHeight")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return width, height
        return None

    def engine_b_text(self) -> str:
        if self._b_text is None:
            self._b_text = pp.strip_grounding(_read_text(self.paths["b_txt"]))
        return self._b_text

    @property
    def render(self) -> Path:
        return self.paths["render"]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@dataclass
class Assembly:
    """The annotated book as page markers around page texts.  Each marker
    page's body is LEAD + the page text stripped + TRAIL; compose() puts
    repaired page texts back in the same frame, byte for byte where a page
    is unchanged."""
    preamble: str
    order: list[int]
    marker_lines: dict[int, str]
    leads: dict[int, str]
    trails: dict[int, str]
    bodies: dict[int, str]
    source: str

    def compose(self, texts: Mapping[int, str]) -> str:
        out = [self.preamble]
        for scan in self.order:
            out.append(self.marker_lines[scan])
            text = texts[scan].strip() if scan in texts else self.bodies[scan]
            lead, trail = self.leads[scan], self.trails[scan]
            if text and not self.bodies[scan]:
                # A page sealed empty has no frame of its own (its whitespace
                # is all lead, its trail is empty): a text written into it
                # takes the skill's frame, a blank line each side, or it runs
                # into the next marker (the modern book's title page, scan 3,
                # written by N2: `2002<!-- page_004 -->` failed the contract).
                lead, trail = "\n", "\n\n" if scan != self.order[-1] else "\n"
            out.append(lead + text + trail)
        return "".join(out)


def parse_assembly(text: str, source: str = "file") -> Assembly:
    """The page frame of an annotated book (PAGE_MARKER_RE lines)."""
    lines = text.splitlines(keepends=True)
    preamble: list[str] = []
    order: list[int] = []
    marker_lines: dict[int, str] = {}
    chunks: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines:
        match = PAGE_MARKER_RE.fullmatch(line.rstrip("\r\n"))
        if match:
            scan = int(match.group("page"))
            if scan in marker_lines:
                raise InputError(f"the assembly has two markers for page {scan}")
            order.append(scan)
            marker_lines[scan] = line
            chunks[scan] = []
            current = scan
        elif current is None:
            preamble.append(line)
        else:
            chunks[current].append(line)
    leads, trails, bodies = {}, {}, {}
    for scan in order:
        body = "".join(chunks[scan])
        stripped = body.strip()
        lead_len = len(body) - len(body.lstrip())
        trail_len = len(body) - len(body.rstrip())
        leads[scan] = body[:lead_len] if stripped else body
        trails[scan] = body[len(body) - trail_len:] if stripped and trail_len else ""
        bodies[scan] = stripped
    return Assembly("".join(preamble), order, marker_lines, leads, trails, bodies, source)


def built_assembly(texts: Mapping[int, str], order: Sequence[int]) -> Assembly:
    """An annotated book built from sealed pages (--pages without
    --assembled, or a book with no assembly): the skill's marker frame, a
    blank line on each side of every page."""
    marker_lines = {scan: f"<!-- page_{scan:03d} -->\n" for scan in order}
    leads = {scan: "\n" for scan in order}
    trails = {scan: "\n\n" for scan in order}
    if order:
        trails[order[-1]] = "\n"
    bodies = {scan: texts[scan].strip() for scan in order}
    return Assembly("", list(order), marker_lines, leads, trails, bodies, "built")


def seal_problems(scan: int, md_bytes: bytes, json_bytes: bytes) -> list[str]:
    """Why a sealed page does not match its own seal: phase 3's canonical
    JSON, its markdownSha256 and its jsonPayloadSha256 (validate_completion's
    checks), a complete status and its own scan page."""
    try:
        record = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        return [f"page {scan}: sealed JSON unreadable: {error}"]
    if not isinstance(record, dict):
        return [f"page {scan}: sealed JSON is not an object"]
    problems = []
    if json_bytes != pp.canonical_json_bytes(record):
        problems.append(f"page {scan}: sealed JSON is not canonical (edited after sealing)")
    provenance = record.get("provenance") or {}
    if pp.sha256_bytes(md_bytes) != provenance.get("markdownSha256"):
        problems.append(f"page {scan}: Markdown does not match its seal (markdownSha256)")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    (unsealed.get("provenance") or {}).pop("jsonPayloadSha256", None)
    if pp.sha256_bytes(pp.canonical_json_bytes(unsealed)) != provenance.get("jsonPayloadSha256"):
        problems.append(f"page {scan}: JSON payload does not match its seal (jsonPayloadSha256)")
    if record.get("status") != "complete":
        problems.append(f"page {scan}: status is {record.get('status')!r}, not complete")
    if record.get("scanPage") != scan:
        problems.append(f"page {scan}: sealed JSON is for page {record.get('scanPage')!r}")
    return problems


@dataclass
class Book:
    paths: InputPaths
    pages: dict[int, Page]
    # The pages under repair (--pages, else every sealed page); `pages` holds
    # every sealed page of the book.
    order: list[int]
    assembly: Assembly
    profile: dict[str, Any]
    manifest_pages: dict[int, Any]
    expected_count: int | None
    selected: list[int]
    notes: list[str]

    def marker_pages(self) -> list[int]:
        return list(self.assembly.order)


def _load_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InputError(f"{path}: unreadable JSON: {error}") from error


def sealed_scans(folder: Path) -> list[int]:
    out = []
    for path in folder.glob("page-*.md"):
        match = re.fullmatch(r"page-(\d+)\.md", path.name)
        if match:
            out.append(int(match.group(1)))
    return sorted(out)


def load_book(paths: InputPaths, pages: Iterable[int] | None = None) -> Book:
    """Read and verify the inputs; InputError with every problem found."""
    problems: list[str] = []
    notes: list[str] = []
    if not paths.proofread.is_dir():
        raise InputError(f"no sealed pages: {paths.proofread} is not a folder")
    all_scans = sealed_scans(paths.proofread)
    if not all_scans:
        raise InputError(f"no sealed pages in {paths.proofread}")
    wanted = sorted(set(pages)) if pages is not None else all_scans
    missing = [scan for scan in wanted if scan not in all_scans]
    if missing:
        problems.append(f"pages asked for but not sealed: {missing[:20]}")
    selected = [scan for scan in wanted if scan in all_scans]

    manifest_pages: dict[int, Any] = {}
    expected_count = None
    if paths.page_manifest is not None:
        try:
            manifest = load_page_manifest(paths.page_manifest)
        except Exception as error:  # noqa: BLE001 - the loader raises its own kinds
            raise InputError(f"{paths.page_manifest}: {error}") from error
        manifest_pages = {record.scan_page: record for record in manifest.pages}
        expected_count = manifest.expected_scan_pages

    page_sources = _load_json(paths.page_sources)
    source_rows: dict[int, dict[str, Any]] = {}
    if page_sources is not None:
        rows = page_sources.get("pages") if isinstance(page_sources, dict) else None
        if not isinstance(rows, list):
            raise InputError(f"{paths.page_sources}: no `pages` list")
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("scanPage"), int):
                source_rows[row["scanPage"]] = row

    def marker_expected(scan: int) -> bool:
        row = source_rows.get(scan)
        if row is not None and "markerExpected" in row:
            return bool(row["markerExpected"])
        record = manifest_pages.get(scan)
        return bool(record.marker_expected) if record is not None else True

    # Every sealed page is read and verified, the selected ones or not: the
    # book model is the whole book's (a subset of pages is repaired and
    # counted against what the whole book shows).
    loaded: dict[int, Page] = {}
    for scan in all_scans:
        stem = page_stem(scan)
        page_paths = {
            "md": paths.proofread / f"{stem}.md",
            "json": paths.proofread / f"{stem}.json",
            "a_txt": paths.ocr_a / f"{stem}.txt",
            "a_json": paths.ocr_a / f"{stem}.json",
            "b_txt": paths.ocr_b / f"{stem}.txt",
            "render": paths.renders / f"{stem}.png",
        }
        try:
            md_bytes = page_paths["md"].read_bytes()
            json_bytes = page_paths["json"].read_bytes()
        except OSError as error:
            problems.append(f"page {scan}: {error}")
            continue
        page_problems = seal_problems(scan, md_bytes, json_bytes)
        row = source_rows.get(scan)
        if row is not None:
            if row.get("markdownSha256") not in (None, pp.sha256_bytes(md_bytes)):
                page_problems.append(f"page {scan}: Markdown hash differs from {paths.page_sources.name}")
            if row.get("jsonSha256") not in (None, pp.sha256_bytes(json_bytes)):
                page_problems.append(f"page {scan}: JSON hash differs from {paths.page_sources.name}")
        record = manifest_pages.get(scan)
        if record is not None and page_paths["render"].is_file():
            if pp.sha256_file(page_paths["render"]) != record.render_sha256:
                page_problems.append(f"page {scan}: render hash differs from the page manifest")
        problems.extend(page_problems)
        if page_problems:
            continue
        for key in ("a_txt", "a_json", "b_txt", "render"):
            if not page_paths[key].is_file():
                notes.append(f"page {scan}: no {key} ({page_paths[key]})")
        loaded[scan] = Page(scan=scan, sealed_text=md_bytes.decode("utf-8"),
                            record=json.loads(json_bytes.decode("utf-8")),
                            marker=marker_expected(scan), paths=page_paths)

    use_file = paths.assembled is not None and (paths.assembled_named or pages is None)
    if use_file:
        try:
            assembled_text = paths.assembled.read_text(encoding="utf-8")
        except OSError as error:
            raise InputError(f"{paths.assembled}: {error}") from error
        assembly = parse_assembly(assembled_text)
        wanted_markers = [scan for scan in selected if marker_expected(scan)]
        present = [scan for scan in assembly.order if scan in set(selected)]
        if pages is None and assembly.order != [scan for scan in all_scans if marker_expected(scan)]:
            extra = sorted(set(assembly.order) - set(wanted_markers))
            lost = sorted(set(wanted_markers) - set(assembly.order))
            problems.append(f"the assembly's markers are not the marker pages (extra {extra[:20]}, "
                            f"missing {lost[:20]}, or out of order)")
        elif present != wanted_markers:
            problems.append("the assembly's markers are not the selected marker pages")
        differing = [scan for scan in present if scan in loaded
                     and assembly.bodies[scan] != loaded[scan].sealed_text.strip()]
        if differing:
            problems.append(f"assembled page text differs from the sealed page on {len(differing)} page(s): "
                            f"{differing[:20]}")
        if pages is not None:
            # A subset keeps the assembly's frame for its own pages only.
            keep = [scan for scan in assembly.order if scan in set(selected)]
            assembly = Assembly("", keep, {s: assembly.marker_lines[s] for s in keep},
                                {s: assembly.leads[s] for s in keep}, {s: assembly.trails[s] for s in keep},
                                {s: assembly.bodies[s] for s in keep}, "file-subset")
    else:
        order = [scan for scan in selected if scan in loaded and loaded[scan].marker]
        assembly = built_assembly({scan: loaded[scan].sealed_text for scan in order}, order)
        notes.append("annotated text built from the sealed pages"
                     + (" (--pages without --assembled)" if paths.assembled is not None else " (no assembly found)"))

    if problems:
        raise InputError("\n".join(problems))
    profile = _load_json(paths.profile) or {}
    return Book(paths=paths, pages=loaded, order=[scan for scan in selected if scan in loaded],
                assembly=assembly, profile=profile,
                manifest_pages=manifest_pages, expected_count=expected_count, selected=selected, notes=notes)


def input_files(book: Book) -> list[Path]:
    """Every file the run reads: the assembly, the page list, the profile,
    the manifest, and every page's files (the book model reads them all)."""
    files = [p for p in (book.paths.assembled if book.assembly.source.startswith("file") else None,
                         book.paths.page_sources, book.paths.profile, book.paths.page_manifest) if p is not None]
    for scan in sorted(book.pages):
        page = book.pages.get(scan)
        if page is None:
            continue
        files.extend(path for path in page.paths.values() if path.is_file())
    return files


def hash_inputs(files: Sequence[Path]) -> dict[str, str | None]:
    """SHA-256 of each file (None for a file that is gone)."""
    out: dict[str, str | None] = {}
    for path in files:
        try:
            out[str(path)] = pp.sha256_file(path)
        except OSError:
            out[str(path)] = None
    return out


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_output(output: Path, paths: InputPaths, resume: bool) -> Path:
    """The output folder, resolved, if the tool may write it; OutputError
    otherwise.  Refused: the book folder itself, an input folder or a folder
    inside one, a folder holding an input folder, and an existing folder -
    unless RESUME and it is one this tool wrote (its REPORT_NAME names it)."""
    output = output.expanduser().resolve()
    if output == paths.book:
        raise OutputError(f"--output {output} is the book folder itself", exists=False)
    for name, folder in paths.folders().items():
        folder = folder.resolve()
        if output == folder or _inside(output, folder):
            raise OutputError(f"--output {output} is inside the input folder {folder} ({name})", exists=False)
        if _inside(folder, output):
            raise OutputError(f"--output {output} holds the input folder {folder} ({name})", exists=False)
    for name in ("assembled", "page_sources", "profile", "page_manifest"):
        value = getattr(paths, name)
        if value is not None and _inside(value.resolve(), output):
            raise OutputError(f"--output {output} holds the input file {value}", exists=False)
    if output.exists():
        if not resume:
            raise OutputError(f"--output {output} already exists (pass --resume to repair into it again)",
                              exists=True)
        report = output / REPORT_NAME
        try:
            tool = json.loads(report.read_text(encoding="utf-8")).get("tool")
        except (OSError, json.JSONDecodeError, AttributeError):
            tool = None
        if tool != TOOL_NAME:
            raise OutputError(f"--output {output} exists and is not a folder repair_book.py wrote "
                              f"(no {REPORT_NAME} naming it)", exists=True)
        # A link where the tool writes or removes would take the write (or
        # the removal of an earlier run's pages) out of the folder.
        for name in (REPORT_NAME, *RUN_FILES, *RUN_FOLDERS):
            if (output / name).is_symlink():
                raise OutputError(f"--output {output}: {name} is a link; the tool writes only inside its folder",
                                  exists=True)
    return output
