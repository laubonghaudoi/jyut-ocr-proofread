#!/usr/bin/env python3
"""Offline check that the book repair tool (scripts/repair_book.py) reads its
inputs and writes only its output folder, and refuses what it must refuse.

No model, no GPU, no corpus: a made-up book (tests/repair_fixture.py).

- A run on the book writes only --output: every file under the book folder
  keeps its bytes, and the report's input hashes equal the files'.  On a
  book with nothing to repair, the repaired annotated book is the assembly
  byte for byte; the report says which stages ran, which are off and which
  are not built.
- An existing output is refused (exit 3), and so is --resume on a folder the
  tool did not write, or one whose pages/ is a link (into the sealed pages:
  the resumed run would remove and write them); --resume on its own output
  runs.
- An output that is the book folder, or inside proofread/, assembled/,
  renders/ or an ocr-* folder, is refused (exit 2) and nothing is written
  there - also inside assembled/ before the assembly exists (the page list
  and manifest are there), and inside an ocr-* folder the run does not
  read.
- An assembled page that differs from its sealed page, a sealed page whose
  Markdown no longer matches its seal, a sealed JSON edited after sealing, a
  page list whose recorded hash differs, and a render that is not the page
  manifest's are each refused (exit 2) before anything is written.
- --pages without --assembled builds the annotated text from the chosen
  sealed pages; the report says so; the book model is still the whole
  book's.
- An input that changes while the tool runs fails the run (exit 2) and the
  report names it.

Usage:
    python3 tests/check_repair_readonly.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401  (private stores, closed endpoint)
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
import repair_book  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def run(*argv: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return repair_book.main(["--workers", "1", *argv])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        book = fx.write_book(tmp / "book", fx.make_spec())
        before = fx.tree_hashes(book)
        out = tmp / "out"
        status = run(str(book), "--output", str(out))
        check(status == 0, f"a run on the made-up book exits 0 (got {status})")
        check(fx.tree_hashes(book) == before, "every file under the book folder keeps its bytes")
        report = json.loads((out / "repair-report.json").read_text(encoding="utf-8"))
        recorded = report["inputs"]["sha256"]
        check(bool(recorded) and all(fx.pp.sha256_file(Path(p)) == h for p, h in recorded.items()),
              f"the report hashes every input it read ({len(recorded)} files) and each hash is the file's")
        check(report["checks"]["inputsUnchanged"]["passed"] is True, "the report says the inputs are unchanged")
        combined = (book / "assembled" / "combined-annotated.md").read_bytes()
        check((out / "combined-repaired.md").read_bytes() == combined and report["counts"]["edits"] == 0,
              "on a book with nothing to repair the repaired book is the assembly byte for byte")
        built = {stage.name for stage in repair_book.STAGES if stage.run is not None}
        check(all(info["status"] == ("ran" if name in built else "not built")
                  for name, info in report["stages"].items()) and "questions" not in report["stages"],
              "each stage in this build is reported `ran`, every other stage `not built`; no stage asks a model")
        pages = sorted(p.name for p in (out / "pages").glob("page-*.md"))
        check(len(pages) == 20 and (out / "pages" / "page-0007.md").read_text(encoding="utf-8")
              == (book / "proofread" / "page-0007.md").read_text(encoding="utf-8"),
              "pages/ holds every marker page, each the sealed text when nothing changed it")
        for name in ("book-model.json", "repair-log.jsonl", "doubts.json", "lint.json"):
            check((out / name).is_file(), f"{name} is written")
        check(not (out / "questions.json").exists() and not (out / "answers.jsonl").exists()
              and not (out / "crops").exists(), "no question, answer or crop is written")

        status = run(str(book), "--output", str(out))
        check(status == 3, f"an existing output is refused with exit 3 (got {status})")
        foreign = tmp / "foreign"
        foreign.mkdir()
        (foreign / "notes.txt").write_text("not the tool's", encoding="utf-8")
        status = run(str(book), "--output", str(foreign), "--resume")
        check(status == 3 and sorted(p.name for p in foreign.iterdir()) == ["notes.txt"],
              f"--resume on a folder the tool did not write is refused and left alone (got {status})")
        status = run(str(book), "--output", str(out), "--resume")
        check(status == 0, f"--resume on the tool's own output runs (got {status})")
        linked = tmp / "linked"
        shutil.copytree(out, linked)
        shutil.rmtree(linked / "pages")
        (linked / "pages").symlink_to(book / "proofread", target_is_directory=True)
        snapshot = fx.tree_hashes(book)
        status = run(str(book), "--output", str(linked), "--resume")
        check(status == 3 and fx.tree_hashes(book) == snapshot,
              f"--resume on a folder whose pages/ links into the sealed pages is refused and they keep their "
              f"bytes (got {status})")

        for inside in (book, book / "proofread" / "repaired", book / "assembled" / "repaired",
                       book / "renders" / "x", book / "ocr-paddle" / "y"):
            snapshot = fx.tree_hashes(book)
            status = run(str(book), "--output", str(inside))
            check(status == 2 and fx.tree_hashes(book) == snapshot and not (inside != book and inside.exists()),
                  f"--output {inside.relative_to(tmp)} is refused with exit 2 and nothing is written (got {status})")

        # Before the assembly is written, assembled/ holds the page list and
        # the manifest: an output there is refused too, and so is one in an
        # ocr-* folder this run does not read.
        unassembled = fx.write_book(tmp / "unassembled", fx.make_spec(), assemble=False)
        (unassembled / "ocr-other").mkdir()
        for inside in (unassembled / "assembled" / "repaired", unassembled / "ocr-other" / "repaired"):
            status = run(str(unassembled), "--output", str(inside))
            check(status == 2 and not inside.exists(),
                  f"--output {inside.relative_to(tmp)} (no assembly yet) is refused with exit 2 (got {status})")

        def broken(name: str, change) -> None:
            copy = tmp / name
            shutil.copytree(book, copy)
            change(copy)
            target = tmp / f"{name}-out"
            status = run(str(copy), "--output", str(target))
            check(status == 2 and not target.exists(), f"{name}: refused with exit 2, nothing written (got {status})")

        def assembled_differs(root: Path) -> None:
            path = root / "assembled" / "combined-annotated.md"
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("秋夜讀書", "秋夜讀詩", 1), encoding="utf-8")

        def markdown_edited(root: Path) -> None:
            path = root / "proofread" / "page-0006.md"
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        def json_edited(root: Path) -> None:
            path = root / "proofread" / "page-0006.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["mode"] = "edited"
            path.write_bytes(fx.pp.canonical_json_bytes(record))

        def list_differs(root: Path) -> None:
            path = root / "assembled" / "page-sources.json"
            rows = json.loads(path.read_text(encoding="utf-8"))
            rows["pages"][5]["markdownSha256"] = "0" * 64
            path.write_text(json.dumps(rows), encoding="utf-8")

        def render_differs(root: Path) -> None:
            from PIL import Image
            path = root / "renders" / "page-0008.png"
            with Image.open(path) as image:
                image.convert("L").point(lambda v: 255 - v).save(path)

        broken("assembled-differs", assembled_differs)
        broken("markdown-edited", markdown_edited)
        broken("json-edited", json_edited)
        broken("page-list-differs", list_differs)
        broken("render-differs", render_differs)

        subset = tmp / "subset"
        status = run(str(book), "--output", str(subset), "--pages", "6-8")
        report = json.loads((subset / "repair-report.json").read_text(encoding="utf-8")) if subset.is_file() or \
            (subset / "repair-report.json").is_file() else {}
        combined = (subset / "combined-repaired.md").read_text(encoding="utf-8") if status == 0 else ""
        check(status == 0 and report.get("pages", {}).get("assembly") == "built"
              and combined.count("<!-- page_") == 3 and "<!-- page_006 -->" in combined
              and any("built from the sealed pages" in note for note in report.get("notes", [])),
              "--pages without --assembled builds the annotated text from the chosen sealed pages")
        model = json.loads((subset / "book-model.json").read_text(encoding="utf-8")) if status == 0 else {}
        check(model.get("contentsPages") == [2, 3] and model.get("gates", {}).get("M-6", {}).get("passed")
              and len(model.get("pages", {})) == 20,
              "--pages repairs the chosen pages against the whole book's model (its contents pages too)")

        # An input that changes during the run: the model stage rewrites one.
        real = repair_book.rm.learn

        def learn_and_touch(book_inputs, **kwargs):
            target = book / "ocr-hunyuan" / "page-0009.txt"
            original = target.read_bytes()
            target.write_bytes(original + b"\n")
            try:
                return real(book_inputs, **kwargs)
            finally:
                touched.append((target, original))

        touched: list = []
        repair_book.rm.learn = learn_and_touch
        try:
            changed_out = tmp / "changed-out"
            status = run(str(book), "--output", str(changed_out))
        finally:
            repair_book.rm.learn = real
            for target, original in touched:
                target.write_bytes(original)
        report = json.loads((changed_out / "repair-report.json").read_text(encoding="utf-8"))
        changed = report["checks"]["inputsUnchanged"]["changed"]
        check(status == 2 and report["checks"]["inputsUnchanged"]["passed"] is False
              and any(p.endswith("page-0009.txt") for p in changed),
              f"an input changed during the run fails it with exit 2 and is named in the report (got {status})")
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_readonly: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
