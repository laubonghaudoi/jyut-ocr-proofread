#!/usr/bin/env python3
"""Repair an assembled book without re-running its pages: the finishing step
between assembly and finalize_page_markers.py.

It reads the sealed pages (proofread/), the annotated assembly and its page
list, both engine drafts, the renders, the book profile and the page
manifest, and writes a NEW folder (--output).  Nothing it reads is ever
written: an output inside an input folder is refused, and every input file's
SHA-256 is taken before the run and checked after it.

The run, in order:

1. the inputs are verified (each sealed page against its own seal and the
   page list; the assembled page text against the sealed text; each render
   against the page manifest) - exit 2 on any failure;
2. the book model is learned from the book itself (book-model.json:
   running heads, volume labels, folios, dividers, contents pages, engine
   B's stand-ins, the sentence-final marks, each with its gate); a failed
   gate is a `gate-failed` doubt, a folio jump a `leaves-missing` one;
3. the defect counters (_book_lint.py) are taken on the sealed text;
4. the repair stages run in order, each switchable: furniture, marks and
   the editors' brackets 〔〕 the engines read (per page), contents, title page, heading levels, paragraphs and joins
   (book level), and the text runs (digits, Latin letters, kana, □,
   note calls ①) and the marks both engines read that the page lacks, with the marks of a refused
   first answer that engine A confirms (_repair_runs.py); the marks' forms (ASCII marks of
   Chinese text full width, no emphasis markers); 裁決's answers in traditional
   script, in a book printed in simplified script, as phase 3 writes them
   now; last, one
   reading written different ways across the book, named in a doubt
   (_repair_words.py).  Every change is
   an offset edit logged in repair-log.jsonl with its page, the text before
   and after, the rule and the evidence; two edits over the same text are a
   `conflict` doubt.  No model is asked: a site a rule finds but cannot
   settle, where printed text is at stake, is a doubt that names it; a site
   of layout alone (a paragraph break, a heading's level) is left as it is;
5. the pages, the repaired annotated book, the log and the doubts are
   written; the log is replayed on the sealed pages and must give the
   written pages and book byte for byte (exit 5 otherwise); the counters
   are taken again; the inputs are hashed again (exit 2 if any changed);
   repair-report.json says what ran, what changed and what did not.

A stage that is not in this build says so in the report ("not built") and
changes nothing.

Usage:
    python3 scripts/repair_book.py BOOK --output BOOK/repaired [--pages SPEC] [--resume]
        [--no-furniture] [--no-marks] [--no-brackets] [--no-contents]
        [--no-title-page] [--no-levels] [--no-paragraphs] [--no-joins] [--no-runs] [--no-forms] [--no-script]
        [--no-words]

Exit codes: 0 done; 2 inputs failed verification (or changed during the
run, or the output lies in an input folder); 3 the output folder exists;
4 boundaries.json fails the finalize contract; 5 the log replay differs from
the written output.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import _book_lint as lint  # noqa: E402
import _repair_brackets as rb  # noqa: E402
import _repair_edits as re_  # noqa: E402
import _repair_inputs as ri  # noqa: E402
import _repair_model as rm  # noqa: E402
import _repair_joins as rj  # noqa: E402
import _repair_rules as rr  # noqa: E402
import _repair_runs as rn  # noqa: E402
import _repair_structure as rs  # noqa: E402
import _repair_words as rw  # noqa: E402
from _ocr_markdown import parse_page_spec  # noqa: E402


SCHEMA_VERSION = 1

EXIT_DONE = 0
EXIT_INPUTS = 2
EXIT_OUTPUT_EXISTS = 3
EXIT_BOUNDARIES = 4
EXIT_REPLAY = 5


@dataclass
class StageResult:
    """What one stage proposes: edits and doubts (kind, page, detail,
    fields)."""
    edits: list[re_.Edit] = field(default_factory=list)
    doubts: list[tuple[str, int | None, str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class Context:
    """What a stage reads: the book, its model, the ledger (the page texts
    as they stand) and the run's arguments.  A stage that needs the text its
    own first edits leave (the contents stage's passes) applies them with
    apply() before it returns."""
    book: ri.Book
    model: dict[str, Any]
    ledger: re_.Ledger
    arguments: argparse.Namespace
    stage: str = ""
    _facts: rr.BookFacts | None = None
    _readings: rs.Readings | None = None
    # What the joins stage decides: each marker page's label, and why.
    boundaries: dict[int, str] | None = None
    boundary_evidence: dict[str, Any] | None = None

    def facts(self) -> rr.BookFacts:
        """What the rules read of the book model, and the book-wide counts."""
        if self._facts is None:
            self._facts = rr.book_facts(self.book, self.model)
        return self._facts

    def readings(self) -> rs.Readings:
        """The structure rules' page readings, shared by their stages."""
        if self._readings is None:
            self._readings = rs.Readings(self.book, self.model)
        return self._readings

    def apply(self, edits: Sequence[re_.Edit]) -> None:
        self.ledger.apply(self.stage, edits)

    def order(self) -> list[int]:
        return list(self.book.assembly.order)


def furniture_stage(context: Context) -> StageResult:
    """Band text - running heads, run suffixes, folios (_repair_rules F1) -
    and answers at band spots (F6), and the body's characters lost at a
    page's edge (F8)."""
    plan = rr.furniture(context.book, context.model, context.ledger.texts, context.order(), context.facts())
    return StageResult(plan.edits, plan.doubts)


def marks_stage(context: Context) -> StageResult:
    """Engine B's stand-ins, vertical brackets and the marks of headings
    (_repair_rules K1, K5, K9, K11, K-seq)."""
    plan = rr.marks(context.book, context.model, context.ledger.texts, context.order(), context.facts())
    return StageResult(plan.edits, plan.doubts)


def brackets_stage(context: Context) -> StageResult:
    """The editors' brackets 〔〕 the engines read, in that shape on the
    page, and no round bracket doubled beside one (_repair_brackets B1,
    B2)."""
    plan = rb.brackets(context.book, context.model, context.ledger.texts, context.order(), context.facts())
    return StageResult(plan.edits, plan.doubts)


def contents_stage(context: Context) -> StageResult:
    """The contents and their units (_repair_structure S15, S14, S9, S4, S7, S11; and
    K1's dash on the units S9 makes headings), in passes applied before the
    last is proposed."""
    plan = rs.contents_stage(context.book, context.model, context.ledger.texts, context.order(), context.apply,
                             context.facts(), context.readings())
    return StageResult(plan.edits, plan.doubts)


def title_page_stage(context: Context) -> StageResult:
    """A page printed right to left, named in a doubt (_repair_structure
    S13)."""
    plan = rs.title_pages(context.book, context.model, context.ledger.texts, context.order(), context.facts(),
                          context.readings())
    return StageResult(plan.edits, plan.doubts)


def levels_stage(context: Context) -> StageResult:
    """Engine A's titles on a page whose structure pass was refused, made
    headings and applied first (_repair_structure S18); then every
    heading's kind and one level scheme (S5)."""
    first = rs.refused_titles(context.book, context.model, context.ledger.texts, context.order(), context.facts())
    context.apply(first.edits)
    plan = rs.levels(context.book, context.model, context.ledger.texts, context.order(), context.facts(),
                     context.readings())
    return StageResult(plan.edits, first.doubts + plan.doubts)


def paragraphs_stage(context: Context) -> StageResult:
    """Paragraphing within pages (_repair_joins P6, P8; and _repair_rules P5,
    the blank line around a heading)."""
    plan = rj.paragraphs(context.book, context.model, context.ledger.texts, context.order(), context.facts(),
                         context.readings())
    return StageResult(plan.edits, plan.doubts)


def joins_stage(context: Context) -> StageResult:
    """Every page boundary's label (_repair_joins S, T, H, L, J, P), kept on
    the context for boundaries.json; P6 across a join."""
    plan, labels, evidence = rj.joins(context.book, context.model, context.ledger.texts, context.order(),
                                      context.facts(), context.readings())
    context.boundaries, context.boundary_evidence = labels, evidence
    return StageResult(plan.edits, plan.doubts)


def runs_stage(context: Context) -> StageResult:
    """Text runs both engines read that the page lacks, the marks of a
    refused first answer engine A confirms, and the marks both engines read
    alike that the page lacks, in a table's cells and in running text
    (_repair_runs R1, N1, N5, N8, N6, N7, N2); after the joins stage, so
    that it reads the labels that stage wrote (the forms and script stages
    follow it before the words).  R1 is applied before the rest is
    proposed."""
    withdrawn: dict[int, str] = {}
    plan = rn.runs(context.book, context.model, context.ledger, context.order(), context.apply, context.facts(),
                   context.boundaries, withdrawn)
    # A join past foot notes that a line the stage puts back among them gives
    # way to (edge_safe): the boundary is a paragraph, as before #36, and its
    # evidence says why.
    for scan, why in withdrawn.items():
        if context.boundaries is not None:
            context.boundaries[scan] = "paragraph"
        if context.boundary_evidence is not None and str(scan) in context.boundary_evidence:
            context.boundary_evidence[str(scan)] = {**context.boundary_evidence[str(scan)], "label": "paragraph",
                                                    "withdrawnBy": f"runs: {why}"}
    return StageResult(plan.edits, plan.doubts)


def forms_stage(context: Context) -> StageResult:
    """The marks' forms, on the text every other stage has written: ASCII
    marks of Chinese text full width, emphasis runs no engine read out
    (_repair_rules K9, K12)."""
    plan = rr.forms(context.book, context.model, context.ledger.texts, context.order())
    return StageResult(plan.edits, plan.doubts)


def script_stage(context: Context) -> StageResult:
    """裁決's sealed answers in a book printed in simplified script, written
    as phase 3 writes them now: a character only traditional script has in
    the form an engine read there, or in its one simplified form where
    neither engine read it or a form of it (_repair_rules V1); a doubt where
    engine A's form is written and engine B read another character, or the
    one form neither engine read."""
    plan = rr.answer_scripts(context.book, context.ledger.texts, context.order())
    return StageResult(plan.edits, plan.doubts)


def words_stage(context: Context) -> StageResult:
    """One reading written different ways across the book, a doubt that
    names every spot and what the page holds there (_repair_words W1); no
    edit."""
    plan = rw.words(context.book, context.ledger.texts, context.order())
    return StageResult(plan.edits, plan.doubts)


@dataclass(frozen=True)
class Stage:
    name: str
    switch: str | None
    run: Callable[[Context], StageResult] | None


# The stages in the order they run (the design's: per page furniture,
# marks and brackets; then the book-level contents, title page, paragraphs and joins;
# then the text runs; then the marks' forms; then the answers' script, on the
# text the other stages leave (an answer at a band spot is gone, F6); last the
# words, which only doubt).  `run` is None for a stage not in this
# build: it is reported "not built" and changes nothing.
STAGES: tuple[Stage, ...] = (
    Stage("furniture", "--no-furniture", furniture_stage),
    Stage("marks", "--no-marks", marks_stage),
    Stage("brackets", "--no-brackets", brackets_stage),
    Stage("contents", "--no-contents", contents_stage),
    Stage("title-page", "--no-title-page", title_page_stage),
    Stage("levels", "--no-levels", levels_stage),
    Stage("paragraphs", "--no-paragraphs", paragraphs_stage),
    Stage("joins", "--no-joins", joins_stage),
    Stage("runs", "--no-runs", runs_stage),
    Stage("forms", "--no-forms", forms_stage),
    Stage("script", "--no-script", script_stage),
    Stage("words", "--no-words", words_stage),
)
# The rules each built stage runs in this build (the report names them; a
# stage's other rules are not built yet).
STAGE_RULES = {
    "furniture": ["F1", "F6", "F8"],
    "marks": ["K1", "K5", "K9", "K11", "K-seq"],
    "brackets": ["B1", "B2"],
    "contents": ["S15", "S14", "S9", "K1", "S4", "S7", "S11"],
    "title-page": ["S13"],
    "levels": ["S18", "S5"],
    "paragraphs": ["P6", "P8", "P5"],
    "joins": ["S", "T", "H", "L", "J", "P", "P6"],
    "runs": ["R1", "N1", "N5", "N8", "N6", "N7", "N2"],
    "forms": ["K9", "K12"],
    "script": ["V1"],
    "words": ["W1"],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path, help="the book folder (proofread/, assembled/, renders/, ocr-*/)")
    parser.add_argument("--output", type=Path, required=True,
                        help="a new folder for the repaired book; refused if it exists (unless --resume) "
                             "or lies in an input folder")
    for name, what in (("assembled", "the annotated assembly"), ("page-sources", "the assembly's page list"),
                       ("proofread", "the sealed pages' folder"), ("renders", "the renders' folder"),
                       ("ocr-a", "engine A's drafts"), ("ocr-b", "engine B's drafts"),
                       ("profile", "book-profile.json"), ("page-manifest", "the page manifest")):
        parser.add_argument(f"--{name}", type=Path, help=f"{what} (default: the book folder's layout)")
    parser.add_argument("--pages", help="only these scan pages, e.g. 1-9,17 (a benchmark subset); without "
                                        "--assembled the annotated text is built from the sealed pages")
    parser.add_argument("--resume", action="store_true",
                        help="reuse an existing --output this tool wrote (an earlier run's files there are removed "
                             "first)")
    for stage in STAGES:
        if stage.switch:
            parser.add_argument(stage.switch, action="store_true", help=f"skip the {stage.name} stage")
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1),
                        help="CPU processes for the book model and the counters")
    return parser


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def code_identity() -> dict[str, Any]:
    """The skill's commit and whether its scripts differ from it, plus a hash
    of the files the repair writes by: its own, and phase 3's
    proofread_pages.py, whose functions the rules call, with the lists it
    reads (V1 writes by simplified_as_read and OpenCC's table, W1 tells one
    character by classify_pair), so that a change there leaves no finished
    repair counted current (finish_book.repair_current).  Until 2026-10-26
    only the repair tool's own files were hashed."""
    here = Path(__file__).resolve().parent
    paths = sorted(p for p in here.glob("*.py")
                   if p.name.startswith(("_repair", "repair_book", "_book_lint", "proofread_pages"))
                   or p.name in {"_content_fidelity.py", "_note_markers.py"})
    paths += [pp.VARIANT_LIST, pp.ALLOGRAPHS, pp.TS_CHARACTERS]
    names = [f"{path.parent.name}/{path.name}" for path in paths]
    digest = hashlib.sha256()
    for name, path in zip(names, paths):
        digest.update(name.encode() + b"\0" + (path.read_bytes() if path.is_file() else b""))
    out: dict[str, Any] = {"files": names, "sha256": digest.hexdigest(), "commit": None, "dirty": None}
    try:
        out["commit"] = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True,
                                       text=True, timeout=10, check=True).stdout.strip() or None
        # --no-optional-locks: a plain status may rewrite the repository's
        # index, a write outside the output folder.
        status = subprocess.run(["git", "--no-optional-locks", "-C", str(here), "status", "--porcelain", "--", "."],
                                capture_output=True, text=True, timeout=10, check=True).stdout
        out["dirty"] = bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def clear_earlier_run(output: Path) -> list[str]:
    """--resume: remove what an earlier run wrote in OUTPUT.  Only the
    tool's own names are removed: the page texts pages/page-NNNN.md (and
    their temporary files), and ri.RUN_FILES.  Returns what was removed."""
    removed = []
    pages = output / "pages"
    if pages.is_dir() and not pages.is_symlink():
        for path in sorted(pages.iterdir()):
            name = path.name
            if path.is_file() and not path.is_symlink() and re.fullmatch(r"\.?page-\d+\.md(?:\.tmp)?", name):
                path.unlink()
                removed.append(f"pages/{name}")
    for name in ri.RUN_FILES:
        path = output / name
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(name)
    return removed


def model_doubts(model: Mapping[str, Any], ledger: re_.Ledger) -> None:
    """A `gate-failed` doubt for every gate the book failed, one for every
    run of list-like pages M-6 refused as contents on its characters or on
    the count of pages its lines map onto (a contents lost there would
    otherwise be lost in silence), and a `leaves-missing` doubt for every
    jump in the folio sequence."""
    for name, gate in sorted((model.get("gates") or {}).items()):
        if not gate.get("passed"):
            measured = {k: v for k, v in gate.items() if k != "passed"}
            ledger.doubt("gate-failed", None, f"{name} failed on this book; the rules that need it stay silent",
                         gate=name, measured=measured)
        for run in gate.get("rejected") or []:
            where = f"scan {run['pages'][0]}" if len(run["pages"]) == 1 else f"scans {run['pages'][0]}-{run['pages'][-1]}"
            ledger.doubt("gate-failed", run["pages"][0],
                         f"{name} refused the list-like {where} as contents: {run['why']}; a contents there gets no "
                         f"contents rule", gate=name, measured=run)
    for jump in (model.get("folio") or {}).get("jumps") or []:
        if jump.get("kind") == "leaves-missing":
            ledger.doubt("leaves-missing", jump["before"],
                         f"folios {jump['missingFrom']}-{jump['missingTo']} of run {jump['suffix']!r} are not in "
                         f"the scan (between scans {jump['after']} and {jump['before']})", jump=jump)


def run(arguments: argparse.Namespace) -> int:
    started = time.monotonic()
    timings: dict[str, float] = {}
    given = {"assembled": arguments.assembled, "page_sources": arguments.page_sources,
             "proofread": arguments.proofread, "renders": arguments.renders, "ocr_a": arguments.ocr_a,
             "ocr_b": arguments.ocr_b, "profile": arguments.profile, "page_manifest": arguments.page_manifest}
    paths = ri.InputPaths.resolve(arguments.book, given)
    try:
        output = ri.check_output(arguments.output, paths, arguments.resume)
    except ri.OutputError as error:
        print(f"output refused: {error}", file=sys.stderr)
        return EXIT_OUTPUT_EXISTS if error.exists else EXIT_INPUTS
    try:
        pages = parse_page_spec([arguments.pages]) if arguments.pages else None
    except ValueError as error:
        print(f"--pages: {error}", file=sys.stderr)
        return EXIT_INPUTS
    try:
        book = ri.load_book(paths, pages)
    except ri.InputError as error:
        print(f"inputs failed verification:\n{error}", file=sys.stderr)
        return EXIT_INPUTS
    files = ri.input_files(book)
    hashes_before = ri.hash_inputs(files)
    timings["inputs"] = round(time.monotonic() - started, 2)
    output.mkdir(parents=True, exist_ok=arguments.resume)
    # The folder says a run is under way until its report is written at the
    # end: a run that stops half way leaves no earlier run's report beside
    # its own files, and stays resumable.
    atomic_write(output / ri.REPORT_NAME, json_bytes({"tool": ri.TOOL_NAME, "schemaVersion": SCHEMA_VERSION,
                                                      "status": "running", "generatedAt": pp.utc_now()}))
    cleared = clear_earlier_run(output) if arguments.resume else []

    mark = time.monotonic()
    model = rm.learn(book, workers=arguments.workers)
    atomic_write(output / "book-model.json", json_bytes(model))
    timings["model"] = round(time.monotonic() - mark, 2)

    order = book.assembly.order
    sealed = {scan: book.pages[scan].sealed_text for scan in order}
    ledger = re_.Ledger(sealed)
    model_doubts(model, ledger)
    mark = time.monotonic()
    lint_before = lint.lint_book(book, model, sealed, workers=arguments.workers)
    timings["lint-before"] = round(time.monotonic() - mark, 2)

    context = Context(book, model, ledger, arguments)
    stages: dict[str, dict[str, Any]] = {}
    for stage in STAGES:
        if stage.switch and getattr(arguments, stage.switch[2:].replace("-", "_")):
            stages[stage.name] = {"status": f"off ({stage.switch})"}
            continue
        if stage.run is None:
            stages[stage.name] = {"status": "not built"}
            continue
        mark = time.monotonic()
        logged = len(ledger.records)
        context.stage = stage.name
        result = stage.run(context)
        ledger.apply(stage.name, result.edits)
        for kind, page, detail, fields in result.doubts:
            ledger.doubt(kind, page, detail, stage=stage.name, **fields)
        stages[stage.name] = {"status": "ran", "edits": len(ledger.records) - logged, "doubts": len(result.doubts),
                              "rules": STAGE_RULES.get(stage.name), "seconds": round(time.monotonic() - mark, 2)}

    written: dict[str, str] = {}
    log_name = "repair-log.jsonl"
    data = re_.log_bytes(ledger.records)
    atomic_write(output / log_name, data)
    written[log_name] = pp.sha256_bytes(data)
    data = json_bytes(ledger.doubts)
    atomic_write(output / "doubts.json", data)
    written["doubts.json"] = pp.sha256_bytes(data)

    checks: dict[str, Any] = {"logRecords": [p for r in ledger.records for p in re_.record_problems(r)]}
    status = EXIT_DONE
    boundaries = context.boundaries
    combined = book.assembly.compose(ledger.texts)
    if boundaries is not None:
        manifest = {"boundaries": {str(scan): boundaries[scan] for scan in sorted(boundaries)}}
        evidence = {"boundaries": context.boundary_evidence or {}}
        for name, value in (("boundaries.json", manifest), ("boundary-evidence.json", evidence)):
            data = json_bytes(value)
            atomic_write(output / name, data)
            written[name] = pp.sha256_bytes(data)
        problems = rj.contract_problems(combined, boundaries)
        checks["boundaryContract"] = {"passed": not problems, "problems": problems[:50],
                                      "labels": len(boundaries)}
        if problems:
            status = EXIT_BOUNDARIES
    else:
        checks["boundaryContract"] = {"passed": None, "why": "the joins stage did not run"}
    for scan in order:
        data = ledger.texts[scan].encode("utf-8")
        atomic_write(output / "pages" / f"{ri.page_stem(scan)}.md", data)
    data = combined.encode("utf-8")
    atomic_write(output / "combined-repaired.md", data)
    written["combined-repaired.md"] = pp.sha256_bytes(data)
    # The replay: the sealed pages as read again from disk, the log as
    # written, against the files as written.
    try:
        logged = re_.read_log((output / log_name).read_text(encoding="utf-8").splitlines())
        again = {scan: book.pages[scan].paths["md"].read_text(encoding="utf-8") for scan in order}
        replayed = re_.replay(again, logged)
        on_disk = {scan: (output / "pages" / f"{ri.page_stem(scan)}.md").read_text(encoding="utf-8")
                   for scan in order}
        same_pages = replayed == on_disk
        same_book = book.assembly.compose(replayed).encode("utf-8") == (output / "combined-repaired.md").read_bytes()
        checks["logReplay"] = {"passed": same_pages and same_book, "pages": same_pages, "book": same_book,
                               "records": len(logged)}
    except (re_.ReplayError, OSError, json.JSONDecodeError) as error:
        checks["logReplay"] = {"passed": False, "error": str(error)}
    if not checks["logReplay"]["passed"]:
        status = EXIT_REPLAY
    mark = time.monotonic()
    lint_after = lint.lint_book(book, model, ledger.texts, combined, boundaries=boundaries,
                                workers=arguments.workers)
    timings["lint-after"] = round(time.monotonic() - mark, 2)
    data = json_bytes({"before": lint_before, "after": lint_after})
    atomic_write(output / "lint.json", data)
    written["lint.json"] = pp.sha256_bytes(data)
    written["book-model.json"] = pp.sha256_file(output / "book-model.json")

    hashes_after = ri.hash_inputs(files)
    changed = sorted(path for path in hashes_before if hashes_before[path] != hashes_after.get(path))
    checks["inputsUnchanged"] = {"passed": not changed, "changed": changed}
    if changed:
        status = EXIT_INPUTS

    report = {
        "tool": ri.TOOL_NAME,
        "schemaVersion": SCHEMA_VERSION,
        "status": "finished",
        "generatedAt": pp.utc_now(),
        "book": str(paths.book),
        "code": code_identity(),
        "arguments": {"pages": arguments.pages, "resume": arguments.resume,
                      "off": [s.switch for s in STAGES if s.switch
                              and getattr(arguments, s.switch[2:].replace("-", "_"))]},
        "pages": {"selected": len(book.order), "markers": len(order), "assembly": book.assembly.source},
        "notes": book.notes,
        "stages": stages,
        "counts": {
            "edits": len(ledger.records),
            "rules": dict(Counter(r["rule"] for r in ledger.records)),
            "doubts": dict(Counter(d["kind"] for d in ledger.doubts)),
        },
        "gates": {name: bool(g.get("passed")) for name, g in (model.get("gates") or {}).items()},
        "boundaries": None if boundaries is None else {
            "labels": dict(Counter(boundaries.values())),
            "rules": dict(Counter(e.get("rule") for e in (context.boundary_evidence or {}).values()))},
        "lint": {"before": lint.summary(lint_before), "after": lint.summary(lint_after)},
        "checks": checks,
        "resume": {"earlierOutputsRemoved": len(cleared)},
        "inputs": {"files": len(files), "sha256": hashes_before},
        "outputs": written,
        "timings": {**timings, "total": round(time.monotonic() - started, 2)},
        "exitStatus": status,
    }
    atomic_write(output / ri.REPORT_NAME, json_bytes(report))
    print(f"repair_book: {len(order)} marker pages, {len(ledger.records)} edits, {len(ledger.doubts)} doubts; "
          "stages: " + ", ".join(f"{name} {info['status']}" for name, info in stages.items())
          + f"; replay {checks['logReplay'].get('passed')}; inputs unchanged {not changed}; exit {status}")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """The command line."""
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
