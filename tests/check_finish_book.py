#!/usr/bin/env python3
"""Offline check of scripts/finish_book.py, the finishing chain in one command.

No GPU, no corpus, no model server: a made-up book (tests/repair_fixture.py)
laid out the way phase 3 leaves one - sealed pages with their render and
draft in the provenance, both engines' drafts, the renders, the profile and
survey_pdf.py's page manifest, no assembly.  The repair asks no model.

- The chain on the book: the assembly (one segment with canonical markers,
  the ordered manifest, the page list with each page's hashes, a byte copy of
  the page manifest; assemble_ocr_segments.py replays it), the repair
  (repaired/, with no question, answer or crop), final.md with no page
  marker, doubts.md and finish/doubts.json.  stdout is the RESULT line and
  the two paths, nothing else, and names no image; the book's inputs keep
  their bytes.
- doubts.md: the research queue (a proper name, and a flagged reading whose
  context the final text no longer holds), phase 3's doubts by kind with
  their pages, the repair's doubts, a page the manifest leaves out; ranked
  (a count of every kind; the sections what most likely changes the text
  first; a doubt whose flagged text is the running head's folded last,
  counted, no detail - one naming body text past what doubts.md shows of
  its detail is not); no image
  named (doubts.json keeps the detail as written); as many items as the
  RESULT line says.  Every item's detail: a kind of more than ten shows ten
  and folds the rest in a <details> block that counts them and names their
  pages, a line a page (two items on one page in one line); nothing points
  at finish/doubts.json, which the cleanup deletes.
- Run again: the assembly and the repair are kept, final.md and doubts.md
  keep their bytes and times; with another OpenCC table (proofread_pages.py
  and the lists it reads are the repair's code too) the repair is not
  current.  A page sealed again: the old assembly is moved
  aside (not deleted), the book is assembled again, the repair resumed, the
  new text reaches final.md.  An earlier repair folder left running is
  resumed.
- Refusals: a page not sealed (exit 3, NOT READY, the page named), a sealed
  page edited after sealing (exit 2), two folders of sealed pages (exit 2,
  both named), another finish_book.py on the book (exit 2).
- Layouts: renders in renders/renders with the manifest in renders/ (what
  survey_pdf.py writes when given renders/), drafts in ocr-a/ocr-b and sealed
  pages in output/ are found by their files.
- As its own process with wait_for_run.py: while the repair runs (held,
  in the check, inside its book model) the wait says RUNNING with the step
  (exit 75); SIGTERM makes RESULT exit 4 at once and the wait says STOPPED
  (exit 4); the run after it resumes the repair and finishes, and the wait
  says DONE with the two paths (exit 0); a book not ready is FAILED (exit
  1).
- What the review found (each check failed before its fix):
  - a final.md or doubts.md edited after the run (the user's corrections,
    or a final.md this command never wrote) is moved aside to
    final.edited-<time>.md / doubts.edited-<time>.md when the command runs
    again, never overwritten, and the RESULT line names the copy;
  - the command's own WARNING lines (engine A's drafts are not the ones
    phase 3 sealed against) reach the RESULT line and the wait's DONE line;
  - a second run refused by the lock leaves the running run's log and
    report alone: the wait still shows the running step;
  - phase 3's pages (or the page manifest) inside assembled/, the folder
    this command writes and moves aside when stale, are refused (exit 2) and
    stay where they are.

Usage:
    python3 tests/check_finish_book.py
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
import offline  # noqa: E402,F401  (private stores, closed endpoint)
import repair_fixture as fx  # noqa: E402
import proofread_pages as pp  # noqa: E402
import finish_book  # noqa: E402

FINISH = ROOT / "scripts" / "finish_book.py"
WAIT = ROOT / "scripts" / "wait_for_run.py"
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


# --- the made-up book, as phase 3 leaves it ---------------------------------------------------

def reseal(book: Path, scan: int, change: Callable[[dict[str, Any]], None] | None = None,
           text: str | None = None, folder: str = "proofread") -> None:
    """Seal page SCAN again after CHANGE to its record (and TEXT as its page)."""
    stem = f"page-{scan:04d}"
    md = text if text is not None else (book / folder / f"{stem}.md").read_text(encoding="utf-8")
    record = json.loads((book / folder / f"{stem}.json").read_text(encoding="utf-8"))
    if change is not None:
        change(record)
    (book / folder / f"{stem}.md").write_text(md, encoding="utf-8")
    (book / folder / f"{stem}.json").write_bytes(fx.seal(md, record))


def phase3_book(root: Path, excluded: tuple[int, ...] = (), spec: dict[int, dict[str, Any]] | None = None) -> Path:
    """The fixture book (SPEC, or tests/repair_fixture.py's) without its
    assembly: the page manifest at the book's root (survey_pdf.py's), each
    sealed page with the render and engine A draft it was sealed on, the
    drafts' JSON naming their engines."""
    book = fx.write_book(root, spec or fx.make_spec(), assemble=False, excluded=excluded)
    manifest = json.loads((book / "assembled" / "page-manifest.json").read_text(encoding="utf-8"))
    for page in manifest["pages"]:
        page["render_file"] = page["render_file"].replace("../", "")
    (book / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    shutil.rmtree(book / "assembled")
    for scan in range(1, len(manifest["pages"]) + 1):
        stem = f"page-{scan:04d}"

        def provenance(record: dict[str, Any], stem: str = stem) -> None:
            record["provenance"]["renderSha256"] = pp.sha256_file(book / "renders" / f"{stem}.png")
            record["provenance"]["draftSha256"] = pp.sha256_file(book / "ocr-paddle" / f"{stem}.txt")
            record.setdefault("needsResearch", [])
            record["auditorClean"] = True
        reseal(book, scan, provenance)
        a = json.loads((book / "ocr-paddle" / f"{stem}.json").read_text(encoding="utf-8"))
        a["engine"] = finish_book.ENGINE_A_NAME
        (book / "ocr-paddle" / f"{stem}.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        (book / "ocr-hunyuan" / f"{stem}.json").write_text(
            json.dumps({"engine": finish_book.ENGINE_B_NAME, "scanPage": scan}), encoding="utf-8")
    return book


def with_doubts(book: Path) -> None:
    """Phase 3's doubts and research flags on some pages."""
    def page6(record: dict[str, Any]) -> None:
        record["auditorClean"] = False
        record["doubts"] = [{"kind": "nothing-unconfirmed", "detail": "READING KEPT OVER AN UNCONFIRMED 無: at 1 "
                             "spot(s) - check the text against the image"}]

    def page9(record: dict[str, Any]) -> None:
        record["auditorClean"] = False
        record["doubts"] = [{"kind": "punctuation-partial", "detail": "PUNCTUATION PARTIAL: block 2 - check "
                             "them against the image"},
                            {"kind": "table-continuation-undecided", "detail": "see page-0009-stitch-01.png"},
                            # Two spots: the running head's, then - past what
                            # doubts.md shows of a detail - body text kept.
                            {"kind": "deletion-refused", "detail": "READING KEPT OVER A SHORTER ANSWER: at 2 "
                             "spot(s) 裁決's answer (neither engine's reading, or nothing printed) would have left "
                             "out more than 2 characters one engine read; that reading is kept: "
                             f"秋月[A {fx.HEAD}甲編 / B -]照林" + "，" * 120 + "；春風[A - / B 小橋流水]人家"}]

    def page1(record: dict[str, Any]) -> None:
        record["needsResearch"] = [{"kind": "front-matter", "text": "青山文叢全編題字某某",
                                    "why": "題名頁：題字者要查出版脈絡"}]
        record["adjudications"] = [{"context_before": "書眉某某", "context_after": "第九集", "draft_reading": "",
                                    "writer_reading": "一口", "resolved": "高", "needs_research": True,
                                    "crop": "page-0001-adjudication-01.png", "verdict": "定案：高"}]
    def page7(record: dict[str, Any]) -> None:
        # The running head and its suffix read in another order (noise: the
        # band's text), and a body reading kept (a character to check).
        record["auditorClean"] = False
        record["doubts"] = [{"kind": "moved-placed-once", "detail": f"TEXT READ IN ANOTHER ORDER PLACED ONCE: "
                             f"{fx.HEAD}甲編 (engine B read it at …|春風…, engine A at …[{fx.HEAD}甲編]…; kept at "
                             "engine A's) - check the order against the image"},
                            {"kind": "deletion-refused", "detail": "READING KEPT OVER A SHORTER ANSWER: at 1 spot(s) "
                             "裁決's answer would have left out more than 2 characters one engine read; that reading is "
                             "kept: 春風[A - / B 小橋流水]人家"}]
    reseal(book, 6, page6)
    reseal(book, 9, page9)
    reseal(book, 1, page1)
    reseal(book, 7, page7)


def in_order(text: str, parts: list[str]) -> bool:
    """Whether every one of PARTS is in TEXT, each after the one before."""
    at = -1
    for part in parts:
        at = text.find(part, at + 1)
        if at < 0:
            return False
    return True


def inputs_hashes(book: Path) -> dict[str, str]:
    """Every file phase 3 and the survey left: they must keep their bytes."""
    out = {}
    for name in ("proofread", "renders", "ocr-paddle", "ocr-hunyuan"):
        for path in sorted((book / name).rglob("*")):
            if path.is_file():
                out[str(path.relative_to(book))] = pp.sha256_file(path)
    for name in ("page-manifest.json", "book-profile.json"):
        out[name] = pp.sha256_file(book / name)
    return out


# --- running it ----------------------------------------------------------------------------------

def finish(book: Path, *argv: str, deliver: bool = False, keep_work: bool = True) -> tuple[int, list[str]]:
    """finish_book.main on BOOK; without DELIVER, --no-deliver (the fixture's
    manifest names a PDF that does not exist: its copies are delivery_checks').
    With KEEP_WORK, --keep-work: the fifth step would remove the renders a
    second run here needs (the step itself is check_clean_book's)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = finish_book.main([str(book), "--workers", "1", *argv, *(() if deliver else ("--no-deliver",)),
                                 *(("--keep-work",) if keep_work else ())])
    return code, out.getvalue().splitlines()


def report(book: Path) -> dict[str, Any]:
    return json.loads((book / "finish" / finish_book.REPORT_NAME).read_text(encoding="utf-8"))


def result_ok(lines: list[str], book: Path, code: int = 0) -> bool:
    state = finish_book.STATE[code]
    head = f"{finish_book.RESULT_PREFIX} exit={code} {state} finish_book.py: "
    if code != 0:
        return len(lines) == 1 and lines[0].startswith(head)
    return (len(lines) == 3 and lines[0].startswith(head) and lines[1] == f"final.md: {book / 'final.md'}"
            and lines[2] == f"doubts.md: {book / 'doubts.md'}")


# --- the checks ----------------------------------------------------------------------------------

def chain_checks(tmp: Path) -> Path:
    book = phase3_book(tmp / "book", excluded=(4,))
    with_doubts(book)
    before = inputs_hashes(book)
    code, lines = finish(book)
    check(code == 0 and result_ok(lines, book), f"a finished book: exit 0, the RESULT line and two paths only ({lines})")
    check(not any(".png" in line for line in lines), "stdout names no image")
    check(inputs_hashes(book) == before, "the sealed pages, drafts, renders, profile and manifest keep their bytes")

    assembled = book / "assembled"
    ordered = json.loads((assembled / "ordered-segments.json").read_text(encoding="utf-8"))
    sources = json.loads((assembled / "page-sources.json").read_text(encoding="utf-8"))
    segment = (assembled / ordered["segments"][0]["path"]).read_text(encoding="utf-8")
    markers = [int(line[10:13]) for line in segment.splitlines() if line.startswith("<!-- page_")]
    rows = {row["scanPage"]: row for row in sources["pages"]}
    check(len(ordered["segments"]) == 1 and ordered["scan_start"] == 1 and ordered["scan_end"] == 20
          and markers == [s for s in range(1, 21) if s != 4],
          f"one segment over the book, a canonical marker for every page but the one the manifest leaves out "
          f"({markers})")
    check((assembled / "page-manifest.json").read_bytes() == (book / "page-manifest.json").read_bytes()
          and ordered["page_manifest"]["sha256"] == pp.sha256_file(book / "page-manifest.json"),
          "the assembly holds a byte copy of the page manifest, sealed in the ordered manifest")
    check(all(rows[s]["markdownSha256"] == pp.sha256_file(book / "proofread" / f"page-{s:04d}.md")
              and rows[s]["jsonSha256"] == pp.sha256_file(book / "proofread" / f"page-{s:04d}.json")
              for s in range(1, 21)) and rows[4]["includedInAssembly"] is False and rows[4]["excludedText"],
          "the page list holds each sealed page's hashes, and the left-out page's text")
    code, said = finish_book._call(finish_book.Log(io.StringIO()),
                                   finish_book._module("assemble_ocr_segments").main,
                                   [str(assembled / "ordered-segments.json"), "--output",
                                    str(assembled / "combined-annotated.md")])
    check(code == 0 and (assembled / "segment-assembly-receipt.json").is_file(),
          f"assemble_ocr_segments.py replays the segment to combined-annotated.md, with its receipt ({said})")

    final = (book / "final.md").read_text(encoding="utf-8")
    repaired = json.loads((book / "repaired" / "repair-report.json").read_text(encoding="utf-8"))
    check("<!-- page_" not in final and final.startswith("# 青山文叢全編")
          and repaired["status"] == "finished" and "model" not in repaired
          and not any((book / "repaired" / name).exists() for name in ("questions.json", "answers.jsonl", "crops")),
          "final.md is the repaired book with no page marker; the repair asked no model (no question, answer or "
          "crop)")
    preface = fx.paragraph(4, 60)[:12]
    check(preface not in final, "the page the manifest leaves out is not in final.md")

    doubts = (book / "doubts.md").read_text(encoding="utf-8")
    listed = json.loads((book / "finish" / "doubts.json").read_text(encoding="utf-8"))
    count = int(lines[0].split(" doubts on ")[0].rsplit("; ", 1)[-1]) if " doubts on " in lines[0] else -1
    check(len(listed) == count and f"疑問 {count} 項" in doubts,
          f"doubts.md and finish/doubts.json hold as many items as the RESULT line says ({len(listed)}, {count})")
    wanted = {
        "the heading": doubts.startswith("# 疑問清單："),
        "no one read the pages page by page": "冇人逐頁睇過 PDF" in doubts,
        "the research queue": "## 要查資料嘅專名" in doubts and "第 1 頁：「青山文叢全編題字某某」" in doubts,
        "a flagged reading whose context final.md lacks": "final.md 搵唔返呢段上下文" in doubts,
        "phase 3's doubt by kind and page": "### 裁決話冇字，但證實唔到，留咗引擎讀到嘅字（1 項，第 6 頁）" in doubts,
        "punctuation": "## 標點" in doubts and "第 9 頁：PUNCTUATION PARTIAL" in doubts,
        "tables": "## 表格同圖" in doubts and "似係跨頁表" in doubts,
        "the repair's doubts": "頁碼跳咗，可能漏咗頁（1 項，第 17 頁）" in doubts,
        "the page left out": "冇入書嘅頁" in doubts and "第 4 頁" in doubts,
        "no image named": ".png" not in doubts and "裁圖" in doubts,
        "doubts.json keeps the detail as written": any("page-0009-stitch-01.png" in i["detail"] for i in listed),
        # Ranked: what most likely changes the text first, a count of every
        # kind, the running head's noise folded last with no detail.
        "a count of every kind": "## 每類幾多項" in doubts and "| 字：最可能要改 | 裁決嘅答案會刪走" in doubts,
        "sections in rank order": in_order(doubts, ["## 要查資料嘅專名", "## 字：最可能要改", "## 標點",
                                                   "## 分頁、標題同目錄", "## 表格同圖", "## 書眉、頁碼嘅雜訊（摺埋）"]),
        "a body reading kept is a character to check": in_order(doubts, ["## 字：最可能要改", "第 7 頁：READING KEPT",
                                                                        "## 標點"]),
        "a doubt that names body text past the part shown is not folded":
            any(i["kind"] == "deletion-refused" and i["page"] == 9 and not i["noise"] for i in listed)
            and in_order(doubts, ["## 字：最可能要改", "第 9 頁：READING KEPT", "## 標點"]),
        "the running head's text is folded, counted, without its detail":
            "- 兩個引擎喺唔同位置讀到嘅同一段字，淨係放咗一次：1 項，第 7 頁" in doubts
            and "PLACED ONCE: " + fx.HEAD not in doubts
            and any(i["noise"] and i["kind"] == "moved-placed-once" for i in listed),
    }
    for name, ok in wanted.items():
        check(ok, f"doubts.md: {name}")
    return book


def long_kind_checks() -> None:
    """doubts.md on its own: a kind of fourteen items lists all fourteen,
    the last four folded a line a page (two of them on page 12); the noise
    points at no work file."""
    pages = [*range(1, 13), 12, 13]
    items = [finish_book.Item("marks", "punctuation-partial", page, f"PUNCTUATION PARTIAL {n}", "repair")
             for n, page in enumerate(pages, 1)]
    items.append(finish_book.Item("noise", "moved-placed-once", 2, "PLACED ONCE: 青山 (", "phase3", noise=True))
    sealed = finish_book.Sealed(b"", 13, {n: True for n in range(1, 14)}, {}, {}, [], None)
    text = finish_book.doubts_markdown(Path("/nowhere"), sealed, items)
    shown, folded = text.split("<details>", 1) if "<details>" in text else (text, "")
    check(all(f"- 第 {n} 頁：PUNCTUATION PARTIAL {n}\n" in shown for n in range(1, 11))
          and folded.startswith("\n<summary>仲有 4 項，第 11–13 頁</summary>\n\n"
                                "- 第 11 頁：PUNCTUATION PARTIAL 11\n"
                                "- 第 12 頁：PUNCTUATION PARTIAL 12；PUNCTUATION PARTIAL 13\n"
                                "- 第 13 頁：PUNCTUATION PARTIAL 14\n\n</details>\n")
          and "doubts.json" not in text,
          "doubts.md: a kind of fourteen shows ten and folds the other four in a <details> block naming their "
          "pages, a line a page; no pointer to finish/doubts.json")

    # An engine's reading may hold HTML (a table's tags): listed as text,
    # shown or folded, or a preview opens a table and takes the rest of the
    # list into it.
    html = [finish_book.Item("marks", "run-one-engine", page, f"A「<table><tr><td>{page}」 & B「{page}」", "repair")
            for page in range(1, 12)]
    text = finish_book.doubts_markdown(Path("/nowhere"), sealed, html)
    check("<table" not in text and "<tr>" not in text and "<td>" not in text
          and "- 第 1 頁：A「&lt;table>&lt;tr>&lt;td>1」 &amp; B「1」\n" in text
          and "- 第 11 頁：A「&lt;table>&lt;tr>&lt;td>11」 &amp; B「11」\n" in text
          and text.count("<details>") == text.count("</details>") == 1,
          "doubts.md: an item's HTML tags and & are written as entities, shown or folded")


def rerun_checks(tmp: Path, book: Path) -> None:
    times = {name: (book / name).stat().st_mtime_ns for name in ("final.md", "doubts.md")}
    data = {name: (book / name).read_bytes() for name in ("final.md", "doubts.md")}
    time.sleep(0.05)
    code, lines = finish(book)
    steps = report(book)["steps"]
    check(code == 0 and steps["assemble"]["status"] == "kept" and steps["repair"]["status"] == "kept"
          and steps["finalize"]["status"] == "unchanged",
          f"run again: the assembly and the repair are kept ({steps['assemble']['status']}, "
          f"{steps['repair']['status']}, {steps['finalize']['status']})")
    check(all((book / n).read_bytes() == data[n] and (book / n).stat().st_mtime_ns == times[n] for n in data),
          "run again: final.md and doubts.md keep their bytes and times")

    # The repair writes by phase 3's functions and the lists they read (V1 by
    # OpenCC's table): another table leaves the finished repair not current.
    import repair_book
    current = finish_book.repair_current(book)
    table = tmp / "references" / "opencc-ts-characters.txt"
    table.parent.mkdir()
    table.write_bytes(pp.TS_CHARACTERS.read_bytes() + "蕆\t蒇\n".encode("utf-8"))
    kept, pp.TS_CHARACTERS = pp.TS_CHARACTERS, table
    try:
        changed = finish_book.repair_current(book)
    finally:
        pp.TS_CHARACTERS = kept
    files = repair_book.code_identity()["files"]
    check(current[0] and changed == (False, "the repair code changed since")
          and {"scripts/proofread_pages.py", "references/opencc-ts-characters.txt"} <= set(files),
          f"the repair's code identity covers proofread_pages.py and OpenCC's table: another table, the repair is "
          f"not current ({current}, {changed})")

    # The book's folder copied: the copy's repair names the first book's files,
    # which are unchanged, and is still not kept as the copy's own.
    copy = tmp / "copied-book"
    shutil.copytree(book, copy, symlinks=True)
    code, lines = finish(copy)
    check(code == 0 and report(copy)["steps"]["repair"]["status"] == "resumed"
          and (copy / "final.md").read_bytes() == data["final.md"],
          f"a copied book: the repair of the first book's files is not kept, it is resumed ({lines[:1]})")

    # An earlier repair left half way (its report says running): resumed.
    path = book / "repaired" / "repair-report.json"
    path.write_text(json.dumps({"tool": "repair_book", "status": "running"}), encoding="utf-8")
    code, lines = finish(book)
    check(code == 0 and report(book)["steps"]["repair"]["status"] == "resumed"
          and (book / "final.md").read_bytes() == data["final.md"],
          f"a repair left running is resumed, and gives the same final.md ({lines[:1]})")

    # Phase 3 seals a page again (--overwrite): assembled again, repair resumed.
    new_text = (book / "proofread" / "page-0007.md").read_text(encoding="utf-8").replace("。", "。新封一句。", 1)
    reseal(book, 7, text=new_text)
    code, lines = finish(book)
    steps = report(book)["steps"]
    stale = sorted(p.name for p in book.glob("assembled.stale-*"))
    check(code == 0 and steps["assemble"]["status"] == "written" and stale
          and (book / stale[0] / "combined-annotated.md").is_file()
          and steps["repair"]["status"] == "resumed" and "新封一句" in (book / "final.md").read_text(encoding="utf-8"),
          f"a page sealed again: the old assembly moved aside ({stale}), assembled again, the repair resumed, "
          f"the new text in final.md")


def refusal_checks(tmp: Path) -> None:
    book = phase3_book(tmp / "not-ready")
    for suffix in (".md", ".json"):
        (book / "proofread" / f"page-0007{suffix}").unlink()
    code, lines = finish(book)
    check(code == 3 and result_ok(lines, book, 3) and "not sealed: 7" in lines[0] and not (book / "final.md").exists()
          and not (book / "assembled").exists(),
          f"a page not sealed: exit 3, NOT READY, the page named, nothing assembled ({lines})")

    book = phase3_book(tmp / "edited")
    with open(book / "proofread" / "page-0006.md", "a", encoding="utf-8") as handle:
        handle.write("改\n")
    code, lines = finish(book)
    check(code == 2 and "fail their seals" in lines[0] and "page 6" in lines[0],
          f"a sealed page edited after sealing: exit 2 ({lines})")

    book = phase3_book(tmp / "two")
    shutil.copytree(book / "proofread", book / "proofread-old")
    shutil.move(str(book / "proofread"), str(book / "proofread-new"))
    code, lines = finish(book)
    check(code == 2 and "proofread-new" in lines[0] and "proofread-old" in lines[0] and "--proofread" in lines[0],
          f"two folders of sealed pages: exit 2, both named ({lines})")
    code, lines = finish(book, "--proofread", str(book / "proofread-new"))
    check(code == 0, f"--proofread names the one to use ({lines[:1]})")

    book = phase3_book(tmp / "locked")
    (book / "finish").mkdir()
    # What the run that holds the lock has written so far.
    own_log = book / "finish" / finish_book.LOG_NAME
    own_log.write_text("[finish] 2026-01-01T00:00:00Z finish_book.py BOOK\n[finish] step 2 of 5: repair the book "
                       "(repair_book.py)\n", encoding="utf-8")
    own_report = book / "finish" / finish_book.REPORT_NAME
    own_report.write_text('{"tool": "finish_book.py", "steps": {}}\n', encoding="utf-8")
    kept = own_log.read_bytes(), own_report.read_bytes()
    with open(book / "finish" / finish_book.LOCK_NAME, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        code, lines = finish(book)
    check(code == 2 and result_ok(lines, book, 2) and "another finish_book.py" in lines[0],
          f"another finish_book.py on the book: exit 2 ({lines})")
    check((own_log.read_bytes(), own_report.read_bytes()) == kept,
          "a run refused by the lock writes nothing into the running run's log or report (the wait reads its "
          "step there)")

    # Phase 3's pages, or the manifest, inside a folder this command writes
    # and moves aside when stale.
    book = phase3_book(tmp / "inside-own")
    shutil.move(str(book / "proofread"), str(book / "assembled"))
    code, lines = finish(book)
    check(code == 2 and result_ok(lines, book, 2) and "assembled" in lines[0]
          and len(list((book / "assembled").glob("page-*.md"))) == 20 and not list(book.glob("assembled.stale-*")),
          f"sealed pages in assembled/: refused (exit 2), not moved aside ({lines})")
    book = phase3_book(tmp / "manifest-inside-own")
    (book / "assembled").mkdir()
    shutil.move(str(book / "page-manifest.json"), str(book / "assembled" / "page-manifest.json"))
    code, lines = finish(book, "--page-manifest", str(book / "assembled" / "page-manifest.json"))
    check(code == 2 and "assembled" in lines[0] and (book / "assembled" / "page-manifest.json").is_file()
          and not list(book.glob("assembled.stale-*")),
          f"the page manifest in assembled/: refused (exit 2), not moved aside ({lines})")


def edited_checks(tmp: Path) -> None:
    """final.md and doubts.md edited after the run are kept, never overwritten."""
    book = phase3_book(tmp / "edited-final")
    finish(book)
    fresh = (book / "final.md").read_bytes(), (book / "doubts.md").read_bytes()
    with open(book / "final.md", "a", encoding="utf-8") as handle:
        handle.write("\n用戶核圖之後叫改嘅一句。\n")
    with open(book / "doubts.md", "a", encoding="utf-8") as handle:
        handle.write("\n- [x] 用戶核咗第 6 頁\n")
    code, lines = finish(book)
    finals, doubts = sorted(book.glob("final.edited-*.md")), sorted(book.glob("doubts.edited-*.md"))
    check(code == 0 and len(finals) == 1 and "用戶核圖之後叫改嘅一句" in finals[0].read_text(encoding="utf-8")
          and (book / "final.md").read_bytes() == fresh[0],
          f"an edited final.md is kept aside, and final.md written again ({[p.name for p in finals]})")
    check(len(doubts) == 1 and "用戶核咗第 6 頁" in doubts[0].read_text(encoding="utf-8")
          and (book / "doubts.md").read_bytes() == fresh[1],
          f"an edited doubts.md is kept aside, and doubts.md written again ({[p.name for p in doubts]})")
    check(bool(finals) and bool(doubts) and finals[0].name in lines[0] and doubts[0].name in lines[0],
          f"the RESULT line names the copies kept ({lines[:1]})")
    code, lines = finish(book)
    check(code == 0 and len(list(book.glob("*.edited-*.md"))) == 2 and "edited" not in lines[0],
          f"run again with nothing edited: nothing more kept aside ({lines[:1]})")

    # A final.md this command never wrote (a book finished by hand before).
    book = phase3_book(tmp / "own-final")
    (book / "final.md").write_text("# 人手整嘅定稿\n", encoding="utf-8")
    code, lines = finish(book)
    finals = sorted(book.glob("final.edited-*.md"))
    check(code == 0 and len(finals) == 1 and finals[0].read_text(encoding="utf-8") == "# 人手整嘅定稿\n",
          f"a final.md not written by finish_book.py is kept aside ({[p.name for p in finals]})")


def warning_checks(tmp: Path) -> None:
    """The command's own WARNING lines reach the RESULT line and the wait."""
    book = phase3_book(tmp / "warned")
    for path in (book / "ocr-paddle").glob("page-*.txt"):
        path.write_text(path.read_text(encoding="utf-8") + "別", encoding="utf-8")
    log = book.parent / "warned.log"
    with open(log, "wb") as handle:
        done = subprocess.run([sys.executable, str(FINISH), str(book), "--workers", "1"],
                              stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=300)
    said = log.read_text(encoding="utf-8").splitlines()
    check(done.returncode == 0 and bool(said) and "WARNING" in said[0],
          f"engine A's drafts not the sealed ones: the RESULT line says there is a WARNING ({said[:1]})")
    code, line = wait(book, log, 5)
    check(code == 0 and "WARNING" in line and "--ocr-a" in line,
          f"the wait's DONE line shows the WARNING ({code}: {line})")


def layout_checks(tmp: Path) -> None:
    """survey_pdf.py given renders/ (renders/renders/, renders/page-manifest.json),
    the drafts in ocr-a/ocr-b and the sealed pages in output/."""
    book = phase3_book(tmp / "agent-layout")
    shutil.move(str(book / "renders"), str(tmp / "renders-moving"))
    (book / "renders").mkdir()
    shutil.move(str(tmp / "renders-moving"), str(book / "renders" / "renders"))
    shutil.move(str(book / "page-manifest.json"), str(book / "renders" / "page-manifest.json"))
    shutil.move(str(book / "ocr-paddle"), str(book / "ocr-a"))
    shutil.move(str(book / "ocr-hunyuan"), str(book / "ocr-b"))
    shutil.move(str(book / "proofread"), str(book / "output"))
    code, lines = finish(book)
    found = report(book).get("inputs") or {}
    check(code == 0 and found.get("proofread", "").endswith("/output") and found.get("ocr_a", "").endswith("/ocr-a")
          and found.get("ocr_b", "").endswith("/ocr-b") and found.get("renders", "").endswith("/renders/renders")
          and found.get("page_manifest", "").endswith("/renders/page-manifest.json"),
          f"another layout is found by its files ({lines[:1]}, {found})")


# --- as its own process, with wait_for_run.py ------------------------------------------------------

# Loaded at the start of a finish_book.py process the check starts with
# FINISH_HOLD set (its folder on PYTHONPATH): the repair's book model waits,
# once it is reached, until the file FINISH_HOLD names exists - a repair
# that is running when the check looks.
HOLD = """import os, sys, time
hold = os.environ.get("FINISH_HOLD")
if hold:
    sys.path.insert(0, os.environ["FINISH_HOLD_SCRIPTS"])
    import _repair_model
    real = _repair_model.learn
    def learn(*args, **kwargs):
        open(hold + ".reached", "w").close()
        while not os.path.exists(hold):
            time.sleep(0.05)
        return real(*args, **kwargs)
    _repair_model.learn = learn
"""


def spawn(book: Path, log: Path, hold: Path | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    if hold is not None:
        (hold.parent / "sitecustomize.py").write_text(HOLD, encoding="utf-8")
        env.update(FINISH_HOLD=str(hold), FINISH_HOLD_SCRIPTS=str(ROOT / "scripts"),
                   PYTHONPATH=os.pathsep.join(filter(None, (str(hold.parent), env.get("PYTHONPATH")))))
    handle = open(log, "wb")
    process = subprocess.Popen([sys.executable, str(FINISH), str(book), "--workers", "1"],
                               stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    handle.close()
    return process


def wait(book: Path, log: Path, timeout: float) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(WAIT), str(book), str(log), "--timeout", str(timeout),
                           "--interval", "0.2"], capture_output=True, text=True, timeout=timeout + 60)
    lines = done.stdout.splitlines()
    return done.returncode, (lines[0] if len(lines) == 1 and not done.stderr else f"{done.stdout!r} {done.stderr!r}")


def process_checks(tmp: Path) -> None:
    book = phase3_book(tmp / "process")
    holding = tmp / "hold"
    holding.mkdir()
    release = holding / "release"
    log = book.parent / "finish-process.log"
    process = spawn(book, log, hold=release)
    try:
        stop = time.monotonic() + 60
        while not Path(str(release) + ".reached").exists() and process.poll() is None and time.monotonic() < stop:
            time.sleep(0.1)
        code, line = wait(book, log, 1)
        check(code == 75 and "RUNNING finish_book.py: step 2 of 5 (repair the book" in line,
              f"while the repair runs: the wait says RUNNING and the step ({code}: {line})")
        process.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        process.wait(timeout=60)
        took = time.monotonic() - sent
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    said = log.read_text(encoding="utf-8").splitlines()
    check(process.returncode == 4 and len(said) == 1 and said[0].startswith("[finish] RESULT exit=4 STOPPED")
          and "SIGTERM" in said[0] and took < 10,
          f"SIGTERM: RESULT exit 4 on stdout, alone, at once ({process.returncode} after {took:.1f}s, {said})")
    code, line = wait(book, log, 5)
    check(code == 4 and line.startswith("[wait] RESULT exit=4 STOPPED finish_book.py"),
          f"the wait says STOPPED, run it again ({code}: {line})")

    log = book.parent / "finish-process-2.log"
    process = spawn(book, log)
    process.wait(timeout=120)
    code, line = wait(book, log, 5)
    check(process.returncode == 0 and report(book)["steps"]["repair"]["status"] == "resumed",
          f"the run after it resumes the repair and finishes ({process.returncode}, "
          f"{report(book)['steps'].get('repair')})")
    check(code == 0 and line.startswith("[wait] RESULT exit=0 DONE finish_book.py: 20 pages in final.md")
          and str(book / "final.md") in line and str(book / "doubts.md") in line,
          f"the wait says DONE with the two paths ({code}: {line})")

    book = phase3_book(tmp / "process-not-ready")
    for suffix in (".md", ".json"):
        (book / "proofread" / f"page-0003{suffix}").unlink()
    log = book.parent / "finish-not-ready.log"
    process = spawn(book, log)
    process.wait(timeout=60)
    code, line = wait(book, log, 5)
    check(process.returncode == 3 and code == 1 and "FAILED finish_book.py: not ready" in line,
          f"a book not ready: the wait says FAILED ({code}: {line})")


def delivery_checks(tmp: Path) -> None:
    """final.md and doubts.md copied beside the PDF the page manifest names, as
    X.md and X-doubts.md (scripts/_deliver.py), the doubt list's final.md
    reading X.md; the lines name them; a PDF that has moved is a WARNING, never
    a failure, and the book's own files stand."""
    book = phase3_book(tmp / "delivery" / "work")
    shelf = tmp / "delivery" / "shelf"
    shelf.mkdir()
    pdf = shelf / "海港 年報.pdf"
    pdf.write_bytes(b"%PDF-1.4 made up\n")
    manifest = json.loads((book / "page-manifest.json").read_text(encoding="utf-8"))
    manifest.update(source_pdf=str(pdf), source_sha256=pp.sha256_file(pdf))
    (book / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    code, lines = finish(book, deliver=True)
    final, doubts = shelf / "海港 年報.md", shelf / "海港 年報-doubts.md"
    check(code == 0 and final.is_file() and final.read_bytes() == (book / "final.md").read_bytes(),
          f"final.md copied beside the PDF as X.md ({code}, {sorted(p.name for p in shelf.iterdir())})")
    own = (book / "doubts.md").read_text(encoding="utf-8")
    check(doubts.is_file() and doubts.read_text(encoding="utf-8") == own.replace("final.md", final.name)
          and "final.md" in own and "final.md" not in doubts.read_text(encoding="utf-8"),
          "doubts.md copied as X-doubts.md, its final.md reading X.md")
    check(lines[3:] == [f"final.md beside the PDF: {final}", f"doubts.md beside the PDF: {doubts}"]
          and "WARNING" not in lines[0], f"the lines name the copies, no WARNING ({lines})")
    check(sorted(p.name for p in shelf.iterdir()) == sorted([pdf.name, final.name, doubts.name]),
          "nothing else beside the PDF")
    import wait_for_run
    check(wait_for_run.beside_pdf(book) == f"; beside the PDF: {final}, {doubts}",
          f"the wait's DONE line names the copies ({wait_for_run.beside_pdf(book)!r})")
    code, lines = finish(book, deliver=True)
    check(code == 0 and "WARNING" not in lines[0] and not list(shelf.glob("*.edited-*")),
          f"run again: its own copies replaced, nothing kept aside ({lines[:1]})")
    pdf.rename(shelf / "renamed.pdf")
    code, lines = finish(book, deliver=True)
    log = (book / "finish" / finish_book.LOG_NAME).read_text(encoding="utf-8")
    check(code == 0 and "2 WARNING line(s)" in lines[0] and len(lines) == 3
          and "final.md not copied beside the PDF: the PDF is no longer at" in log,
          f"the PDF moved: DONE with a WARNING per copy, the book's own files stand ({lines[:1]})")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="finish-book-") as name:
        tmp = Path(name)
        book = chain_checks(tmp)
        long_kind_checks()
        rerun_checks(tmp, book)
        refusal_checks(tmp)
        edited_checks(tmp)
        layout_checks(tmp)
        process_checks(tmp)
        warning_checks(tmp)
        delivery_checks(tmp)
    print(f"{'FAIL' if FAILURES else 'ok  '} finish_book.py: {len(FAILURES)} problem(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
