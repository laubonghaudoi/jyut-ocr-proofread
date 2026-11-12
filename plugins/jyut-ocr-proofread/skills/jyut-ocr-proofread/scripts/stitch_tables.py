#!/usr/bin/env python3
"""Write tables printed across pages as one table, over a phase-3 output directory.

proofread_pages.py runs this pass itself once every page of a run is sealed,
over every sealed page in its output directory (a table can cross the edge of
a chunk).  Run it by hand for a directory sealed before the pass existed, or
with --dry-run to see what it would decide without writing anything:

    python3 scripts/stitch_tables.py OUTPUT_DIR --renders RENDER_DIR \\
        --drafts OCR_A_DIR [--drafts2 OCR_B_DIR] [--profile book-profile.json] [--dry-run]

What it decides and why is in _stitch.py.  What it writes:

- the page where a table starts carries the merged table in its place; a page
  whose first table continued it loses that table (its text is empty when the
  table was all it held);
- every page it touches keeps its text as proofread in its seal
  (`tableStitch.before`) with what was moved and why, carries the doubts the
  pass found (kinds starting `table-continuation-`), and is resealed, so a
  resumed run still finds it current;
- a crop of each page break it decided on, page-NNNN-stitch-01.png beside the
  seal of the page after the break (the end of the one table next to the start
  of the other).

It starts from `tableStitch.before` wherever a page has it, so running it again
gives the same bytes, and a page proofread again rebuilds its chain.  An
exclusive lock on OUTPUT_DIR/.table-stitch.lock keeps two runs sharing a
directory from rewriting the same pages at once, and proofread_pages.py seals
each page under it, so no page is sealed while the stitch plans.  A page that
changed anyway between the stitch's reading and its writing (a writer that
does not take the lock) is read again and the plan made again; nothing is
written over a page the plan did not read.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stitch  # noqa: E402
import proofread_pages as pp  # noqa: E402

PAGE_JSON = re.compile(r"page-(\d{4})\.json")
CROP_FILE = re.compile(r"page-\d{4}-stitch-\d{2}\.png")
CROP_HEIGHT = 1600          # the pair crop's height at most, in pixels: enough to read a cell
# How often the stitch reads the pages again when one changed while it
# planned.  Pages are sealed under the lock the stitch holds, so a change
# comes only from a writer outside it; a directory still changing after this
# many readings is left as it is, and the report says so.
READINGS = 3


def directory_lock(directory: Path) -> contextlib.AbstractContextManager[None]:
    return pp.output_lock(directory)


def page_files(output: Path) -> dict[str, str]:
    """Every page file of the directory with the hash of its bytes: what a
    plan was made from."""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.glob("page-*")) if path.suffix in (".json", ".md") and path.is_file()}


def sealed_page(path: Path) -> tuple[dict[str, Any] | None, str, str]:
    """A page's seal and markdown, or why the stitch leaves the page alone."""
    try:
        raw = path.read_bytes()
        record = json.loads(raw.decode("utf-8"))
        body = path.with_suffix(".md").read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, "", f"unreadable: {error}"
    if record.get("schemaVersion") != pp.SCHEMA_VERSION or record.get("status") != "complete":
        return None, "", "not a completed page of this schema"
    provenance = record.get("provenance") or {}
    if pp.sha256_bytes(body) != provenance.get("markdownSha256"):
        return None, "", "markdown does not match its seal"
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    unsealed["provenance"].pop("jsonPayloadSha256", None)
    if raw != pp.canonical_json_bytes(record) or \
            pp.sha256_bytes(pp.canonical_json_bytes(unsealed)) != provenance.get("jsonPayloadSha256"):
        return None, "", "payload seal does not match"
    return record, body.decode("utf-8"), ""


def resealed(record: Mapping[str, Any], text: str, stitch: Mapping[str, Any] | None,
             doubts: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """The seal with the stitch's notes and doubts in place of the last run's,
    auditorClean and auditorReport restated from the doubts, and both hashes
    taken again."""
    out = {key: value for key, value in record.items() if key != "tableStitch"}
    kept = [d for d in record.get("doubts") or [] if not str(d.get("kind", "")).startswith(_stitch.DOUBT_PREFIX)]
    out["doubts"] = kept + [dict(d) for d in doubts]
    out["auditorClean"] = not out["doubts"]
    out["auditorReport"] = "; ".join(d["detail"] for d in out["doubts"])
    if stitch:
        out["tableStitch"] = dict(stitch)
    provenance = {key: value for key, value in (record.get("provenance") or {}).items()
                  if key != "jsonPayloadSha256"}
    provenance["markdownSha256"] = pp.sha256_bytes(text.encode("utf-8"))
    out["provenance"] = provenance
    # As the seal will read back (lists, not tuples), so an unchanged page
    # compares equal to its seal and is not rewritten.
    out = json.loads(pp.canonical_json_bytes(out).decode("utf-8"))
    out["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(out))
    return out


def pair_crop(render_a: Path | None, box_a: Sequence[float] | None,
              render_b: Path | None, box_b: Sequence[float] | None, right_to_left: bool) -> bytes | None:
    """The table block ending one page next to the block starting the next, at
    one height: the first page on the right for a book read right to left, as
    the spread shows it.  A table with no engine block is shown as its page."""
    try:
        from PIL import Image
    except ImportError:
        return None
    pieces = []
    for render, box in ((render_a, box_a), (render_b, box_b)):
        if render is None or not Path(render).is_file():
            return None
        with Image.open(render) as opened:
            image = opened.convert("RGB")
        if box:
            width, height = image.size
            x, y, w, h = box
            pad = 0.02
            image = image.crop((max(0, int((x - pad) * width)), max(0, int((y - pad) * height)),
                                min(width, int((x + w + pad) * width)), min(height, int((y + h + pad) * height))))
        pieces.append(image)
    height = min(CROP_HEIGHT, max(piece.height for piece in pieces))
    pieces = [piece.resize((max(1, round(piece.width * height / piece.height)), height)) for piece in pieces]
    if right_to_left:
        pieces.reverse()
    gap = max(4, height // 200)
    canvas = Image.new("RGB", (sum(p.width for p in pieces) + gap, height), (128, 128, 128))
    canvas.paste(pieces[0], (0, 0))
    canvas.paste(pieces[1], (pieces[0].width + gap, 0))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def stitch_directory(output: Path, renders: Path | None = None, drafts: Path | None = None,
                     drafts2: Path | None = None, heads: Sequence[str] = (),
                     grams: frozenset[str] | set[str] = frozenset(), dry_run: bool = False,
                     log: Callable[[str], None] = print,
                     output_dir: Path | None = None) -> dict[str, Any]:
    """Run the stitch over every sealed page of `output`; returns the report.

    `renders`, `drafts` and `drafts2` are the directories phase 3 read (the
    render, engine A's layout and engine B's text of each page); `heads` and
    `grams` the book's running heads and recurring n-grams.  With `dry_run`
    nothing is written; with `output_dir`, `output` is left untouched and all
    sealed pages (with stitched updates and pair crops) are written to
    `output_dir`.
    """
    import shutil

    output = Path(output)
    target = Path(output_dir).expanduser().resolve() if output_dir is not None else output
    if target != output and not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(output.glob("page-*")):
            if src_file.suffix in (".json", ".md") and src_file.is_file():
                dst_file = target / src_file.name
                if not dst_file.is_file() or dst_file.read_bytes() != src_file.read_bytes():
                    shutil.copy2(src_file, dst_file)
        with directory_lock(target):
            return _stitch_directory(target, renders, drafts, drafts2, heads, grams, dry_run, log)
    with (contextlib.nullcontext() if dry_run else directory_lock(output)):
        return _stitch_directory(output, renders, drafts, drafts2, heads, grams, dry_run, log)


def _stitch_directory(output: Path, renders: Path | None, drafts: Path | None, drafts2: Path | None,
                      heads: Sequence[str], grams: frozenset[str] | set[str], dry_run: bool,
                      log: Callable[[str], None]) -> dict[str, Any]:
    for reading in range(1, READINGS + 1):
        read = page_files(output)
        report, write = _plan_directory(output, renders, drafts, drafts2, heads, grams, dry_run)
        if dry_run:
            return log_report(report, dry_run, log)
        now = page_files(output)
        changed = sorted(name for name in set(read) | set(now) if read.get(name) != now.get(name))
        if not changed:
            write()
            return log_report(report, dry_run, log)
        log(f"  table stitch: {', '.join(changed[:6])} changed while the stitch planned (reading {reading} "
            f"of {READINGS}); reading the pages again")
    log("  table stitch: pages kept changing while the stitch planned; nothing written - run "
        "stitch_tables.py on the directory again")
    return dict(report, tablesStitched=[], decisions=[], pagesRewritten=[], pagesChanging=changed)


def _plan_directory(output: Path, renders: Path | None, drafts: Path | None, drafts2: Path | None,
                    heads: Sequence[str], grams: frozenset[str] | set[str],
                    dry_run: bool) -> tuple[dict[str, Any], Callable[[], None]]:
    """The report of one reading of the directory, and what writes its plan."""
    inputs: list[_stitch.PageInput] = []
    records: dict[int, dict[str, Any]] = {}
    current: dict[int, str] = {}
    skipped: list[dict[str, Any]] = []
    for path in sorted(output.glob("page-*.json")):
        match = PAGE_JSON.fullmatch(path.name)
        if not match:
            continue
        page = int(match.group(1))
        record, text, why = sealed_page(path)
        if record is None:
            skipped.append({"page": page, "reason": why})
            continue
        if not isinstance(record.get("doubts"), list) and not dry_run:
            # Its auditorClean predates doubts; restating it from a doubts list
            # the page never had would change what it says.
            skipped.append({"page": page, "reason": "sealed before doubts were recorded"})
            continue
        before = (record.get("tableStitch") or {}).get("before")
        blocks = pp.load_blocks(drafts / f"page-{page:04d}.json") if drafts else []
        witness_path = drafts2 / f"page-{page:04d}.txt" if drafts2 else None
        render = renders / f"page-{page:04d}.png" if renders else Path(str(record.get("renderFile") or ""))
        inputs.append(_stitch.PageInput(
            page=page, text=before if isinstance(before, str) else text, record=record, blocks=blocks,
            witness=witness_path.read_text(encoding="utf-8") if witness_path and witness_path.is_file() else None,
            render=render if render.is_file() else None,
            is_furniture=_stitch.furniture_test(blocks, heads, grams)))
        records[page] = record
        current[page] = text

    result = _stitch.plan(inputs)
    by_page = {page.page: page for page in inputs}

    # The pair crops, one per page break decided on.
    crops: dict[str, bytes | None] = {}
    for decision in result["decisions"]:
        if not decision.get("crop") or decision["verdict"] == "separate":
            continue
        first, second = (by_page[p] for p in decision["pages"])
        boxes = []
        for page, block in zip((first, second), decision.get("blocks") or [None, None]):
            box = page.blocks[block]["box"] if block is not None and 0 <= block < len(page.blocks) else None
            boxes.append(box)
        chain_rtl = any(_stitch.orientation_of(first.record, block) == "rotated"
                        for block in (decision.get("blocks") or [])[:1])
        crops[decision["crop"]] = pair_crop(first.render, boxes[0], second.render, boxes[1], chain_rtl)

    rewritten: list[int] = []
    files: list[tuple[Path, bytes]] = []
    for page in inputs:
        planned = result["pages"][page.page]
        record = records[page.page]
        stitch = planned["stitch"]
        had = "tableStitch" in record or any(
            str(d.get("kind", "")).startswith(_stitch.DOUBT_PREFIX) for d in record.get("doubts") or [])
        if not stitch and not planned["doubts"] and not had:
            continue
        if stitch:
            stitch = {"before": page.text, **stitch}
        new_record = resealed(record, planned["text"], stitch, planned["doubts"])
        if planned["text"] == current[page.page] and new_record == record:
            continue
        rewritten.append(page.page)
        files += [(output / f"page-{page.page:04d}.md", planned["text"].encode("utf-8")),
                  (output / f"page-{page.page:04d}.json", pp.canonical_json_bytes(new_record))]

    def write() -> None:
        for path, data in files:
            path.write_bytes(data)
        for path in output.glob("page-*-stitch-*.png"):
            if CROP_FILE.fullmatch(path.name) and path.name not in crops:
                path.unlink()
        for name, png in crops.items():
            path = output / name
            if png is None:
                path.unlink(missing_ok=True)
            elif not path.is_file() or path.read_bytes() != png:
                path.write_bytes(png)

    report = {
        "tablesStitched": result["chains"],
        "decisions": [{k: v for k, v in d.items() if k != "blocks"} for d in result["decisions"]],
        "pagesRewritten": rewritten,
        "pagesSkipped": skipped,
    }
    return report, write


def log_report(report: dict[str, Any], dry_run: bool, log: Callable[[str], None]) -> dict[str, Any]:
    merged = report["tablesStitched"]
    flagged = [d for d in report["decisions"] if d["verdict"] not in ("merge", "separate")]
    if merged or flagged:
        log(f"  tables stitched: {len(merged)}"
            + (" (" + ", ".join(f"p.{c['head']} table {c['table'] + 1} + pp.{c['pages'][1]}-{c['pages'][-1]}, "
                                f"{c['records']} record(s)" for c in merged) + ")" if merged else "")
            + f"; page breaks left unmerged with a doubt: {len(flagged)}"
            + (" (dry run, nothing written)" if dry_run else ""))
    return report


def book_evidence(drafts: Path | None, profile: Path | None) -> tuple[list[str], frozenset[str]]:
    """The book's running heads (profile) and recurring n-grams (every draft)."""
    heads: list[str] = []
    if profile is not None and profile.is_file():
        data = json.loads(profile.read_text(encoding="utf-8"))
        heads = sorted(set(((data.get("layout") or {}).get("runningHeads") or [])))
    grams = frozenset(pp.furniture_grams(sorted(drafts.glob("page-*.txt")))) if drafts else frozenset()
    return heads, grams


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--output-dir", type=Path,
                        help="optional separate output directory so OUTPUT_DIRECTORY stays untouched")
    parser.add_argument("--renders", type=Path, help="the page renders phase 3 read")
    parser.add_argument("--drafts", type=Path, help="engine A's output (page-NNNN.json/.txt)")
    parser.add_argument("--drafts2", type=Path, help="engine B's output (page-NNNN.txt)")
    parser.add_argument("--profile", type=Path, help="book-profile.json (running heads); "
                        "default: next to the engine A directory, if present")
    parser.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    parser.add_argument("--report", type=Path, help="write the report (JSON) here")
    arguments = parser.parse_args(argv)
    output = arguments.output_directory.expanduser().resolve()
    if not output.is_dir():
        print(f"ERROR not a directory: {output}", file=sys.stderr)
        return 2
    drafts = arguments.drafts.expanduser().resolve() if arguments.drafts else None
    profile = arguments.profile or (drafts.parent / "book-profile.json" if drafts else None)
    heads, grams = book_evidence(drafts, profile)
    report = stitch_directory(
        output,
        arguments.renders.expanduser().resolve() if arguments.renders else None,
        drafts,
        arguments.drafts2.expanduser().resolve() if arguments.drafts2 else None,
        heads,
        grams,
        arguments.dry_run,
        output_dir=arguments.output_dir,
    )
    print(f"  {len(report['tablesStitched'])} table(s) stitched, {len(report['pagesRewritten'])} page(s) "
          f"{'would be ' if arguments.dry_run else ''}rewritten, {len(report['pagesSkipped'])} page(s) skipped")
    if arguments.report:
        arguments.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
