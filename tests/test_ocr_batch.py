"""ocr_batch.py: several books from their PDFs to delivery in one batch, offline.

The batch script itself is the real one, with the real packet_dispatch.py, PacketClient waves and
apply_packet_answers.py.  The steps of a book (survey_pdf.py, ocr_book.py, book_profile.py,
proofread_pages.py, finish_book.py, clean_book.py) are stand-ins that keep each one's contract -
what it reads, what it writes, its exit codes - so no engine, no model and no GPU is needed, and
a test can make one book slow or make it fail.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
PY = sys.executable

# One file under every step's name: what it does is decided by the name it is called by.  A file
# KNOBS/<book>.<knob> changes one book's behaviour; every call is one line of KNOBS/calls.log.
STEP = r'''
import json, os, shutil, struct, sys, time, zlib
from pathlib import Path
sys.path.insert(0, os.environ["REAL_SCRIPTS"])
name, args = Path(sys.argv[0]).name, sys.argv[1:]
knobs = Path(os.environ["FAKE_KNOBS"])


def knob(book, key, default=None):
    path = knobs / f"{book.name}.{key}"
    return path.read_text().strip() if path.is_file() else default


def once(book, key):
    path = knobs / f"{book.name}.{key}"
    if not path.is_file():
        return None
    value = path.read_text().strip()
    path.unlink()
    return value


def called(book):
    with open(knobs / "calls.log", "a") as handle:
        handle.write(json.dumps({"script": name, "book": book.name, "argv": args, "t": time.time(),
                                 "deadline": os.environ.get("OCR_PHASE_DEADLINE")}) + "\n")


def pages(book):
    return len(json.loads((book / "page-manifest.json").read_text())["pages"])


def png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes((250, 250, 250)) * 40
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 40, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * 40)) + chunk(b"IEND", b""))


def ask(book, stage, questions, bundles):
    """Ask QUESTIONS by packets; exit 10 with a wave written while any has no answer yet."""
    import _packet_client as pc
    client = pc.PacketClient(book / "packets", max_bundles=bundles, max_items_per_bundle=int(knob(book, "per-bundle", 2)))
    pending = 0
    for text in questions:
        try:
            client.ask_answering("system", text, png(), 64, kind="adjudicate")
        except pc.PacketPending:
            pending += 1
    if pending:
        wave = client.write_wave_bundles(stage=stage)
        print(f"[packets] PENDING {pending} question(s) in {wave['waveDir']}")
        sys.exit(10)


def bundles_asked():
    return int(args[args.index("--max-bundles") + 1]) if "--max-bundles" in args else 8


if name == "survey_pdf.py":
    pdf, book = Path(args[0]), Path(args[1])
    called(book)
    time.sleep(float(knob(book, "render-seconds", 0)))
    if knob(book, "render-fail"):
        print("ERROR cannot open the PDF")
        sys.exit(1)
    count = int(knob(book, "pages", 6))
    (book / "renders").mkdir(parents=True, exist_ok=True)
    for number in range(1, count + 1):
        (book / "renders" / f"page-{number:04d}.png").write_bytes(png())
    (book / "page-manifest.json").write_text(json.dumps(
        {"source_pdf": str(pdf), "pages": [{"scan_page": number} for number in range(1, count + 1)]}))
elif name == "ocr_book.py":
    book = Path(args[0])
    called(book)
    if knob(book, "ocr-fail"):
        print("ERROR the engine could not start")
        print("[ocr-book] RESULT exit=1 FAILED - read the runner's summary")
        sys.exit(1)
    if once(book, "ocr-incomplete"):
        print("[ocr-book] RESULT exit=75 INCOMPLETE - time ran out; finished pages are sealed: run the SAME command again")
        sys.exit(75)
    route = args[args.index("--engine") + 1] if "--engine" in args else "hunyuan-paddle"
    folders = ["ocr-hunyuan", "ocr-paddle"] if route == "hunyuan-paddle" else ["ocr-hunyuan"]
    for number in range(1, pages(book) + 1):
        time.sleep(float(knob(book, "page-seconds", 0)))
        for folder in folders:
            (book / folder).mkdir(exist_ok=True)
            (book / folder / f"page-{number:04d}.json").write_text("{}")
    print("[ocr-book] RESULT exit=0 DONE - the selected OCR route has read the book")
elif name == "build_draft.py":
    (Path(args[0]) / "draft.md").write_text("draft\n")
elif name == "book_profile.py":
    profile = Path(args[3])
    book = profile.parent
    called(book)
    if "http" not in args:
        ask(book, "book-profile", [f"{book.name} profile {n}" for n in range(int(knob(book, "profile-questions", 3)))],
            bundles_asked())
    profile.write_text("{}")
elif name == "proofread_pages.py":
    out = Path(args[2])
    book = out.parent
    called(book)
    if knob(book, "proofread-fail"):
        print("WARNING pages failed or were blocked: [failed 2: missing engine B's draft]")
        sys.exit(1)
    if "http" not in args:
        first = [f"{book.name} page question {n}" for n in range(int(knob(book, "proofread-questions", 5)))]
        ask(book, "proofread", first, bundles_asked())
        ask(book, "proofread", first + [f"{book.name} retry question"], bundles_asked())
    out.mkdir(exist_ok=True)
    for number in range(1, pages(book) + 1):
        (out / f"page-{number:04d}.json").write_text("{}")
elif name == "finish_book.py":
    book = Path(args[0])
    called(book)
    code = once(book, "finish-exit")
    if code:
        print(f"[finish] RESULT exit={code} NOT READY finish_book.py: pages 2 are not sealed")
        sys.exit(int(code))
    pdf = Path(json.loads((book / "page-manifest.json").read_text())["source_pdf"])
    final = pdf.with_suffix(".md")
    final.write_text("the book\n")
    (book / "final.md").write_text("the book\n")
    print(f"[finish] RESULT exit=0 DONE finish_book.py: {pages(book)} pages in the book, 2 doubts on 1 page")
    print(f"final.md: {final}")
elif name == "clean_book.py":
    book = Path(args[0])
    called(book)
    if knob(book, "clean-refuse"):
        print("[clean] RESULT exit=3 REFUSED clean_book.py: X.md beside the PDF was edited")
        sys.exit(3)
    shutil.rmtree(book)
    print(f"[clean] RESULT exit=0 DONE clean_book.py: removed {book}")
'''

STEPS = ("survey_pdf.py", "ocr_book.py", "build_draft.py", "book_profile.py", "proofread_pages.py", "finish_book.py",
         "clean_book.py")
REAL = ("ocr_batch.py", "_ocr_engines.py", "packet_dispatch.py", "_phase_budget.py")
MODEL = "test-answerer-1"


class Batch:
    """A temporary skill folder with the real batch script and the stand-in steps, and a library
    of PDFs: each in a folder named by its shelf mark, the file named in Chinese."""

    def __init__(self, books: int = 2) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.scripts = self.root / "skill" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in REAL:
            shutil.copy(SCRIPTS / name, self.scripts / name)
        for name in STEPS:
            (self.scripts / name).write_text(STEP, encoding="utf-8")
        self.knobs = self.root / "knobs"
        self.knobs.mkdir()
        self.work = self.root / "work"
        self.pdfs = []
        for number in range(1, books + 1):
            folder = self.root / "library" / f"SM{number:04d}"
            folder.mkdir(parents=True)
            pdf = folder / f"書名 卷{number}（出版社；1950-03）.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            self.pdfs.append(pdf)
        self.names = [f"sm{number:04d}" for number in range(1, books + 1)]
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("JYUT_OCR_", "OCR_PHASE_", "PROOFREAD_"))}
        self.env.update(REAL_SCRIPTS=str(SCRIPTS), FAKE_KNOBS=str(self.knobs))
        self.batch: Path | None = None
        self.background: list[subprocess.Popen] = []

    def knob(self, book: str, key: str, value: object = 1) -> Path:
        path = self.knobs / f"{book}.{key}"
        path.write_text(str(value))
        return path

    def call(self, *arguments: str, env: dict | None = None) -> tuple[int, str]:
        done = subprocess.run([PY, str(self.scripts / "ocr_batch.py"), *arguments], capture_output=True, text=True,
                              env={**self.env, **(env or {})}, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def begin(self, *extra: str) -> str:
        code, out = self.call(*map(str, self.pdfs), "--work-root", str(self.work), *extra)
        assert code == 0, out
        self.batch = Path(next(line for line in out.splitlines() if line.startswith("[batch] BATCH ")).split(" ", 2)[2])
        return out

    def ocr(self, env: dict | None = None) -> tuple[int, str]:
        return self.call("--ocr", str(self.batch), env=env)

    def ocr_in_background(self) -> subprocess.Popen:
        log = open(self.batch / "ocr.log", "w")
        process = subprocess.Popen([PY, str(self.scripts / "ocr_batch.py"), "--ocr", str(self.batch)], stdout=log,
                                   stderr=subprocess.STDOUT, env=self.env)
        log.close()
        self.background.append(process)
        return process

    def round(self, *extra: str, wait: int = 20) -> tuple[int, str]:
        grace = [] if "--answer-grace" in extra else ["--answer-grace", "0"]
        grace += [] if "--settle" in extra else ["--settle", "0"]
        return self.call("--round", str(self.batch), "--answered-by", MODEL, "--wait", str(wait), "--poll", "0.1",
                         *grace, *extra)

    def answer(self, leave_out_workers: int = 0) -> dict:
        """Answer the newest round as its answerers would: every bundle of every worker file
        (LEAVE_OUT_WORKERS: the last so many answerers write nothing).  Returns the round's plan."""
        newest = sorted((self.batch / "plans").glob("round-*"))[-1]
        plan = json.loads((newest / "plan.json").read_text(encoding="utf-8"))
        workers = plan["assignments"][:len(plan["assignments"]) - leave_out_workers]
        for assignment in workers:
            prompt = (newest / f"worker-{assignment['worker']:02d}.txt").read_text(encoding="utf-8")
            for bundle in assignment["bundles"]:
                assert bundle["bundle"] in prompt
                items = json.loads(Path(bundle["bundle"]).read_text(encoding="utf-8"))["items"]
                rows = [{"request_id": item["request_id"], "answer": "構件：略\n定案：甲\nRESEARCH: no"} for item in items]
                Path(bundle["answers"]).write_text(json.dumps({"answers": rows}, ensure_ascii=False), encoding="utf-8")
        return plan

    def until_done(self, *extra: str, limit: int = 30) -> tuple[int, str, list[dict]]:
        """Run rounds, answering each, until one ends with something else than questions or still
        running: (its exit code, its output, the plans of the rounds answered)."""
        plans = []
        for _ in range(limit):
            code, out = self.round(*extra)
            if code == 10:
                plans.append(self.answer())
            elif code != 75:
                return code, out, plans
        raise AssertionError(f"the batch did not end in {limit} rounds: {out}")

    def record(self) -> dict:
        return json.loads((self.batch / "batch.json").read_text(encoding="utf-8"))

    def status(self) -> dict:
        return {row["name"]: row for row in json.loads((self.batch / "batch-status.json").read_text())["books"]}

    def calls(self, script: str | None = None, book: str | None = None) -> list[dict]:
        try:
            rows = [json.loads(line) for line in (self.knobs / "calls.log").read_text().splitlines()]
        except OSError:
            return []
        return [row for row in rows if script in (None, row["script"]) and book in (None, row["book"])]

    def events(self) -> list[dict]:
        return [json.loads(line) for line in (self.batch / "timeline.jsonl").read_text().splitlines()]

    def settle(self) -> None:
        """Wait for every detached step this batch started (a temporary folder cannot go under one)."""
        import fcntl
        deadline = time.monotonic() + 30
        while self.batch and time.monotonic() < deadline:
            busy = False
            for lock in (self.batch / "marks").glob("*.lock"):
                with open(lock, "a") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    except OSError:
                        busy = True
            recent = any(time.time() - path.stat().st_mtime < 1.0 for path in (self.batch / "marks").glob("*.started"))
            if not busy and not recent:
                return
            time.sleep(0.1)

    def close(self) -> None:
        for process in self.background:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        self.settle()
        self.tmp.cleanup()


class BatchCase(unittest.TestCase):
    books = 2

    def setUp(self) -> None:
        self.b = Batch(self.books)
        self.addCleanup(self.b.close)


class WholeBatchTests(BatchCase):
    def test_two_books_go_from_their_pdfs_to_delivery_and_share_their_rounds(self) -> None:
        b = self.b
        out = b.begin()
        # Folders named in Latin letters, after the folder that holds a file named in Chinese.
        self.assertEqual([entry["name"] for entry in b.record()["books"]], b.names)
        self.assertEqual(b.batch.parent, b.work / "batches")
        self.assertIn(f"--ocr {b.batch} > {b.batch}/ocr.log 2>&1", out)
        self.assertIn(f"--round {b.batch} --answered-by YOUR-MODEL-ID", out)
        self.assertIn("RESULT exit=0 STARTED - 2 book(s)", out)
        self.assertNotIn("run_ocr_phase.py", out)          # no local model holds the GPU here

        code, out = b.ocr()
        self.assertEqual(code, 0, out)
        self.assertIn("[ocr-batch] RESULT exit=0 DONE - 2 of 2 book(s) read", out)
        self.assertEqual([call["book"] for call in b.calls("ocr_book.py")], b.names)     # one after another
        for name in b.names:
            self.assertTrue((b.work / name / "draft.md").is_file())

        code, out, plans = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT exit=0 DONE - 2/2 delivered", out)
        for name, pdf in zip(b.names, b.pdfs):
            self.assertIn(f"[batch] {name}: [finish] RESULT exit=0 DONE finish_book.py: 6 pages in the book", out)
            self.assertIn(f"[batch] {name}: final.md: {pdf.with_suffix('.md')}", out)
            self.assertIn(f"[batch] {name}: [clean] RESULT exit=0 DONE", out)
            self.assertTrue(pdf.with_suffix(".md").is_file())
            self.assertFalse((b.work / name).exists())      # clean_book.py --all took the folder
        # One round of answerers served both books: the profiles' wave, then the pages', then the retries.
        self.assertEqual(len(plans), 3)
        for plan in plans:
            asked = {Path(bundle["bundle"]).parents[2].name for a in plan["assignments"] for bundle in a["bundles"]}
            self.assertEqual(asked, set(b.names))
        self.assertEqual([plan["questions"] for plan in plans], [6, 10, 2])
        # The steps are the one-book commands, with what a new book gets under the packet backend.
        for name in b.names:
            first = b.calls("proofread_pages.py", name)[0]["argv"]
            self.assertEqual(first[:5], [str(b.work / name / "renders"), str(b.work / name / "ocr-paddle"),
                                         str(b.work / name / "proofread"), "--draft2-directory",
                                         str(b.work / name / "ocr-hunyuan")])
            self.assertEqual(first[5:], ["--max-bundles", "20", "--max-items-per-bundle", "10", "--same-print-marks",
                                         "--unmarked-page-sample"])
            self.assertTrue(all(call["argv"] == first for call in b.calls("proofread_pages.py", name)))
            self.assertEqual(b.calls("book_profile.py", name)[0]["argv"][4:], ["--max-bundles", "20"])
            self.assertEqual(b.calls("finish_book.py", name)[0]["argv"], [str(b.work / name)])
            self.assertEqual(b.calls("clean_book.py", name)[0]["argv"], [str(b.work / name), "--all"])
        # The answers were recorded with the answering model, by the real apply_packet_answers.py.
        applied = [event for event in b.events() if event.get("event") == "applied"]
        self.assertEqual(len(applied), 6)
        ended = [(event["book"], event["step"]) for event in b.events() if event.get("event") == "end"]
        for name in b.names:
            for step in ("render", "ocr", "profile", "proofread", "finish"):
                self.assertIn((name, step), ended)
        self.assertEqual({row["state"] for row in b.status().values()}, {"delivered"})
        # Every call afterwards says the same, and starts nothing.
        before = len(b.calls())
        self.assertEqual(b.round()[0], 0)
        self.assertEqual(len(b.calls()), before)

    def test_a_read_book_is_proofread_while_the_next_one_is_still_being_read(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[1], "page-seconds", 1.5)             # the second book's OCR takes about nine seconds
        reader = b.ocr_in_background()
        code, out = b.round()
        self.assertEqual(code, 10, out)
        self.assertIn("question(s) wait in 1 book(s)", out)
        self.assertIn(f"OCR 1 to read ({b.names[1]}:", out)
        self.assertIsNone(reader.poll())                     # the GPU still reads the second book
        self.assertEqual(b.status()[b.names[1]]["state"], "ocr")
        plan = b.answer()
        self.assertEqual({Path(bundle["bundle"]).parents[2].name for a in plan["assignments"] for bundle in a["bundles"]},
                         {b.names[0]})
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(reader.wait(timeout=30), 0)
        delivered = [event["book"] for event in b.events() if event.get("event") == "delivered"]
        self.assertEqual(delivered, b.names)

    def test_the_round_waits_quietly_when_there_is_nothing_to_answer(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "page-seconds", 1.0)
        b.ocr_in_background()
        began = time.monotonic()
        code, out = b.round(wait=2)
        self.assertEqual(code, 75, out)
        self.assertLess(time.monotonic() - began, 8)
        self.assertRegex(out.strip().splitlines()[-1], r"^\[batch\] RESULT exit=75 RUNNING - 0/2 delivered; OCR 2 to read")
        # The line carries the whole command to run again: an agent whose context was cut still has it.
        self.assertIn(f"run this command again: {PY} {b.scripts / 'ocr_batch.py'} --round {b.batch} --answered-by {MODEL}",
                      out)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_the_round_says_so_when_no_ocr_was_started(self) -> None:
        b = self.b
        b.begin()
        code, out = b.round(wait=3)
        self.assertEqual(code, 75, out)
        self.assertIn("no --ocr call is reading right now (call 2)", out)

    def test_a_short_file_has_no_book_profile(self) -> None:
        b = self.b
        b.knob(b.names[0], "pages", 4)
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        code, out, plans = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(b.calls("book_profile.py", b.names[0]), [])
        self.assertTrue(b.calls("book_profile.py", b.names[1]))


class FailureTests(BatchCase):
    def test_a_book_whose_ocr_fails_does_not_stop_the_others_and_can_be_tried_again(self) -> None:
        b = self.b
        b.begin()
        knob = b.knob(b.names[0], "ocr-fail")
        code, out = b.ocr()
        self.assertEqual(code, 1, out)
        self.assertIn(f"{b.names[0]}: FAILED (ocr_book.py exit 1", out)
        self.assertIn(f"RESULT exit=1 FAILED for {b.names[0]} - 1 of 2 book(s) read", out)
        code, out, _ = b.until_done()
        self.assertEqual(code, 1, out)
        self.assertIn(f"[batch] {b.names[0]}: failed: OCR: ocr_book.py exit 1: ERROR the engine could not start", out)
        self.assertIn(f"[batch] {b.names[1]}: [finish] RESULT exit=0 DONE", out)
        self.assertIn(f"RESULT exit=1 DONE, 1 FAILED - 1/2 delivered; FAILED {b.names[0]}", out)
        # Put right, and tried again: the OCR call reads the one book left, the rounds deliver it.
        knob.unlink()
        code, out = b.round("--retry-failed", wait=2)
        self.assertEqual(code, 75, out)
        self.assertEqual(b.ocr()[0], 0)
        self.assertEqual(len(b.calls("ocr_book.py", b.names[1])), 1)       # the delivered book is not read again
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT exit=0 DONE - 2/2 delivered", out)

    def test_a_pdf_that_cannot_be_rendered_fails_alone(self) -> None:
        b = self.b
        b.knob(b.names[1], "render-fail")
        b.begin()
        code, out = b.ocr()
        self.assertEqual(code, 1, out)
        self.assertIn(f"{b.names[1]}: not rendered, left out", out)
        code, out, _ = b.until_done()
        self.assertEqual(code, 1, out)
        self.assertIn(f"[batch] {b.names[1]}: failed: OCR: not rendered: ERROR cannot open the PDF", out)
        self.assertEqual(b.calls("ocr_book.py", b.names[1]), [])

    def test_a_step_that_fails_fails_its_book_with_the_reason(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "proofread-fail")
        self.assertEqual(b.ocr()[0], 0)
        code, out, _ = b.until_done()
        self.assertEqual(code, 1, out)
        self.assertIn(f"[batch] {b.names[0]}: failed: proofread exit 1: WARNING pages failed or were blocked: "
                      "[failed 2: missing engine B's draft]", out)
        self.assertTrue((b.work / b.names[0]).is_dir())      # a failed book keeps its folder
        self.assertFalse((b.work / b.names[1]).exists())

    def test_pages_found_unsealed_at_the_finish_are_proofread_once_more(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "finish-exit", 3)
        self.assertEqual(b.ocr()[0], 0)
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(b.calls("finish_book.py", b.names[0])), 2)
        steps = [call["script"] for call in b.calls(book=b.names[0])]
        self.assertEqual(steps[steps.index("finish_book.py") + 1], "proofread_pages.py")

    def test_a_cleanup_that_is_refused_is_said_and_the_book_stays_delivered(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "clean-refuse")
        self.assertEqual(b.ocr()[0], 0)
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertIn(f"[batch] {b.names[0]}: [clean] RESULT exit=3 REFUSED clean_book.py: X.md beside the PDF was edited",
                      out)
        self.assertTrue((b.work / b.names[0]).is_dir())

    def test_keep_work_leaves_the_book_folders(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        code, out, _ = b.until_done("--keep-work")
        self.assertEqual(code, 0, out)
        self.assertEqual(b.calls("clean_book.py"), [])
        self.assertTrue((b.work / b.names[0] / "final.md").is_file())

    def test_a_bundle_nobody_answers_is_not_handed_out_for_ever(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        codes = []
        for _ in range(8):
            code, out = b.round()
            codes.append(code)
            if code != 10:
                break
        self.assertEqual(codes, [10, 10, 10, 10, 1], out)
        self.assertIn("a bundle was handed out 4 times and never answered in full", out)

    def test_a_step_killed_with_its_session_is_started_again(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        self.assertEqual(b.round()[0], 10)
        b.answer()
        b.settle()
        # As a kill leaves it: the step started long ago, holds no lock and left no exit code.
        for name in b.names:
            (b.batch / "marks" / f"{name}.profile.exit").unlink()
            (b.batch / "marks" / f"{name}.profile.started").write_text("1")
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertEqual(b.record()["books"][0]["lost"], 1)


class PauseTests(BatchCase):
    def test_a_paused_batch_hands_out_nothing_and_goes_on_where_it_was(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        self.assertEqual(b.round()[0], 10)
        b.answer()                                           # the answerers at work finish what they have
        code, out = b.call("--pause", str(b.batch), "--reason", "the cost is the person's to decide")
        self.assertEqual(code, 0, out)
        code, out = b.round()
        self.assertEqual(code, 3, out)
        self.assertIn("RESULT exit=3 PAUSED", out)
        self.assertIn("the cost is the person's to decide", out)
        self.assertIn("Do NOT run this again until the person says to go on", out)
        self.assertIn(f"--resume {b.batch}", out)
        # What was answered in full was handed in and the books' steps go on; nothing new goes out.
        self.assertEqual(len([item for item in b.events() if item.get("event") == "applied"]), 2)
        code, out = b.call("--status", str(b.batch))
        self.assertIn("; PAUSED (the cost is the person's to decide", out)
        b.settle()
        self.assertEqual(b.round()[0], 3)                    # and again: the profiles are done, the pages' step starts
        b.settle()
        self.assertTrue(b.calls("proofread_pages.py"))
        self.assertEqual(b.round()[0], 3)
        self.assertEqual(len(list((b.batch / "plans").glob("round-*"))), 1)      # its questions wait for the resume
        code, out = b.call("--resume", str(b.batch))
        self.assertEqual(code, 0, out)
        self.assertIn("RESUMED", out)
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT exit=0 DONE - 2/2 delivered", out)

    def test_a_batch_that_is_done_is_done_paused_or_not(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        self.assertEqual(b.until_done()[0], 0)
        self.assertEqual(b.call("--pause", str(b.batch))[0], 0)
        code, out = b.round()
        self.assertEqual(code, 0, out)


class RoundSizeTests(BatchCase):
    def test_a_round_hands_out_no_more_than_the_platform_runs_at_once(self) -> None:
        b = self.b
        b.begin()
        for name in b.names:
            b.knob(name, "profile-questions", 6)             # three bundles of two a book
        self.assertEqual(b.ocr()[0], 0)
        code, out = b.round("--max-workers", "2", "--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        self.assertIn("ANSWERERS 2: start them now, all at once, one per file", out)
        self.assertIn("worker-01.txt .. worker-02.txt", out)
        self.assertIn("Read FILE and do exactly what it says.", out)
        self.assertIn("RESULT exit=10 QUESTIONS - 4 question(s) for 2 new answerer(s)", out)
        self.assertIn("; 2 of 2 at work; 8 more question(s) wait for a free place", out)
        first = b.answer()
        # The first book's bundles come first, so it is answered in full the soonest.
        self.assertEqual({Path(bundle["bundle"]).parents[2].name for a in first["assignments"] for bundle in a["bundles"]},
                         {b.names[0]})
        code, out = b.round("--max-workers", "2", "--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        second = json.loads((b.batch / "plans" / "round-0002" / "plan.json").read_text())
        handed = [bundle["bundle"] for a in second["assignments"] for bundle in a["bundles"]]
        self.assertFalse(set(handed) & {bundle["bundle"] for a in first["assignments"] for bundle in a["bundles"]})
        self.assertEqual(second["questions"], 4)

    def test_the_book_with_the_fewest_questions_left_is_served_first(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "profile-questions", 8)
        b.knob(b.names[1], "profile-questions", 2)            # the second book's wave is the short one
        self.assertEqual(b.ocr()[0], 0)
        code, out = b.round("--max-workers", "1", "--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        first = b.answer()
        self.assertEqual({Path(bundle["bundle"]).parents[2].name for a in first["assignments"] for bundle in a["bundles"]},
                         {b.names[1]})
        # Answered in full, the second book goes on to its pages while the first is still at its profile.
        code, out = b.round("--max-workers", "1", "--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        self.assertTrue(b.calls("proofread_pages.py", b.names[1]))
        self.assertEqual(b.calls("proofread_pages.py", b.names[0]), [])

    def test_an_answerer_that_wrote_nothing_has_its_bundles_handed_out_again(self) -> None:
        b = self.b
        b.begin()
        self.assertEqual(b.ocr()[0], 0)
        code, out = b.round("--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        first = b.answer(leave_out_workers=1)
        left = [bundle["bundle"] for bundle in first["assignments"][-1]["bundles"]]
        code, out = b.round("--items-per-worker", "2")
        self.assertEqual(code, 10, out)
        second = json.loads((b.batch / "plans" / "round-0002" / "plan.json").read_text())
        again = [bundle["bundle"] for a in second["assignments"] for bundle in a["bundles"]]
        self.assertEqual(again[:len(left)], left)        # and first: theirs is the shortest wave left now

    def test_a_place_that_comes_free_is_filled_while_the_other_answerers_are_still_at_work(self) -> None:
        b = self.b
        b.begin()
        for name in b.names:
            b.knob(name, "profile-questions", 6)             # three shares of two a book
        self.assertEqual(b.ocr()[0], 0)
        sized = ("--max-workers", "2", "--items-per-worker", "2", "--answer-grace", "600")
        code, out = b.round(*sized)
        self.assertEqual(code, 10, out)
        self.assertIn("then run this command again AT ONCE, without waiting for the answerers", out)
        first = b.answer(leave_out_workers=1)                 # one answerer is back, the other still at work
        code, out = b.round(*sized)
        self.assertEqual(code, 10, out)
        self.assertIn("RESULT exit=10 QUESTIONS - 2 question(s) for 1 new answerer(s)", out)
        self.assertIn("; 2 of 2 at work; 6 more question(s) wait for a free place", out)
        second = json.loads((b.batch / "plans" / "round-0002" / "plan.json").read_text())
        handed = {bundle["bundle"] for a in first["assignments"] for bundle in a["bundles"]}
        self.assertFalse(handed & {bundle["bundle"] for a in second["assignments"] for bundle in a["bundles"]})
        # Both places taken: the call waits and hands nothing out, least of all the same bundles again.
        code, out = b.round(*sized, wait=2)
        self.assertEqual(code, 75, out)
        self.assertIn("2 answerer(s) at work, no place free or nothing more to hand out", out)
        self.assertEqual(len(list((b.batch / "plans").glob("round-*"))), 2)
        # The agent says its answerers are gone (they failed): their shares go out again at once.
        code, out = b.round(*sized, "--answerers-gone")
        self.assertEqual(code, 10, out)
        third = json.loads((b.batch / "plans" / "round-0003" / "plan.json").read_text())
        again = [bundle["bundle"] for a in third["assignments"] for bundle in a["bundles"]]
        unanswered = [bundle["bundle"] for plan in (first, second) for a in plan["assignments"] for bundle in a["bundles"]
                      if not Path(bundle["answers"]).is_file()]
        self.assertEqual(again, unanswered)
        self.assertNotIn("--answerers-gone", out.split("run this command again AT ONCE")[1])
        # Every answer written: the batch goes on to the end with the same commands.
        for folder in sorted((b.batch / "plans").glob("round-*")):
            plan = json.loads((folder / "plan.json").read_text())
            for assignment in plan["assignments"]:
                for bundle in assignment["bundles"]:
                    items = json.loads(Path(bundle["bundle"]).read_text())["items"]
                    rows = [{"request_id": item["request_id"], "answer": "構件：略\n定案：甲\nRESEARCH: no"} for item in items]
                    Path(bundle["answers"]).write_text(json.dumps({"answers": rows}, ensure_ascii=False))
        code, out, _ = b.until_done("--max-workers", "2", "--items-per-worker", "2")
        self.assertEqual(code, 0, out)

    def test_an_answerer_is_at_work_until_its_files_are_written_or_its_time_is_over(self) -> None:
        import ocr_batch
        b = self.b
        folder = b.root / "book"
        folder.mkdir()
        for number in (1, 2, 3):
            (folder / f"bundle-0{number}.json").write_text("{}")
        pair = lambda number: [str(folder / f"bundle-0{number}.json"), str(folder / f"answers-0{number}.json")]
        record = {"flights": [{"t": time.time(), "round": "round-0003", "worker": 1, "bundles": [pair(1), pair(2)]},
                              {"t": time.time(), "round": "round-0003", "worker": 2, "bundles": [pair(3)]},
                              {"t": time.time(), "round": "round-0003", "worker": 3, "bundles": [pair(4)]}]}
        # The third answerer's bundle is gone - its book was delivered and removed: not waited for.
        self.assertEqual([flight["worker"] for flight in ocr_batch.at_work(record, 600)], [1, 2])
        (folder / "answers-01.json").write_text("{}")
        self.assertEqual(len(ocr_batch.at_work(record, 600)), 2)      # one of its two files is not written yet
        (folder / "answers-02.json").write_text("{}")
        self.assertEqual([flight["worker"] for flight in ocr_batch.at_work(record, 600)], [2])
        record["flights"][0]["t"] -= 601                               # its time is over: handed out again
        self.assertEqual(ocr_batch.at_work(record, 600), [])
        # An answerer keeps its place for a moment after its last file: it has yet to return.
        fresh = {"flights": [{"t": time.time(), "round": "round-0004", "worker": 1, "bundles": [pair(1), pair(2)]}]}
        self.assertEqual(len(ocr_batch.at_work(fresh, 600, settle=30)), 1)
        for number in (1, 2):
            os.utime(folder / f"answers-0{number}.json", (time.time() - 31, time.time() - 31))
        self.assertEqual(ocr_batch.at_work(fresh, 600, settle=30), [])
        # A batch that an earlier version handed out as one whole round.
        old = {"lastRound": {"round": "round-02", "t": time.time(), "bundles": [pair(3), pair(4)]}}
        self.assertEqual(len(ocr_batch.at_work(old, 600)), 1)
        self.assertNotIn("lastRound", old)

    def test_two_sessions_answer_one_batch_each_with_its_own_places(self) -> None:
        b = self.b
        b.begin()
        for name in b.names:
            b.knob(name, "profile-questions", 6)             # three shares of two a book
        self.assertEqual(b.ocr()[0], 0)
        sized = ("--max-workers", "2", "--items-per-worker", "2", "--answer-grace", "600")
        code, out = b.round(*sized)
        self.assertEqual(code, 10, out)
        code, out = b.round(*sized, "--caller", "second")
        self.assertEqual(code, 10, out)
        self.assertIn("for 2 new answerer(s)", out)
        self.assertIn("; 2 of 2 at work; 4 more question(s) wait for a free place", out)
        plans = [json.loads((b.batch / "plans" / f"round-000{n}" / "plan.json").read_text()) for n in (1, 2)]
        held = [{bundle["bundle"] for a in plan["assignments"] for bundle in a["bundles"]} for plan in plans]
        self.assertFalse(held[0] & held[1])                   # no bundle goes to both
        self.assertIn("--caller second", out.split("run this command again AT ONCE")[1])
        # The first session's places are all taken, whatever the second one does.
        code, out = b.round(*sized, wait=2)
        self.assertEqual(code, 75, out)
        self.assertIn("2 answerer(s) at work (and 2 of another caller)", out)
        # The second session lost its answerers: its shares go out again, the first one's stay out.
        code, out = b.round(*sized, "--caller", "second", "--answerers-gone")
        self.assertEqual(code, 10, out)
        third = json.loads((b.batch / "plans" / "round-0003" / "plan.json").read_text())
        self.assertEqual({bundle["bundle"] for a in third["assignments"] for bundle in a["bundles"]}, held[1])
        self.assertEqual({flight["caller"] for flight in b.record()["flights"]}, {"", "second"})

    def test_two_round_calls_take_their_passes_in_turn(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "page-seconds", 0.5)               # nothing to answer for a few seconds
        b.ocr_in_background()
        first = subprocess.Popen([PY, str(b.scripts / "ocr_batch.py"), "--round", str(b.batch), "--answered-by", MODEL,
                                  "--wait", "3", "--poll", "0.1"], stdout=subprocess.PIPE, text=True, env=b.env)
        code, out = b.round("--caller", "second", wait=1)     # is not shut out while the first call waits
        self.assertEqual(code, 75, out)
        self.assertIn("RESULT exit=75 RUNNING", out)
        self.assertEqual(first.wait(timeout=30), 75)
        first.stdout.close()

    def test_the_round_needs_the_answering_model(self) -> None:
        b = self.b
        b.begin()
        code, out = b.call("--round", str(b.batch))
        self.assertEqual(code, 2, out)
        self.assertIn("--round needs --answered-by MODEL", out)


class OcrCallTests(BatchCase):
    def test_under_a_time_budget_the_ocr_stops_with_the_unfinished_book(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[1], "ocr-incomplete")
        code, out = b.ocr(env={"OCR_PHASE_DEADLINE": f"{time.time() + 600:.3f}"})
        self.assertEqual(code, 75, out)
        self.assertIn("RESULT exit=75 INCOMPLETE - 1 of 2 book(s) read", out)
        self.assertIn("run the SAME command again", out)
        self.assertTrue(all(call["deadline"] for call in b.calls("ocr_book.py")))   # the budget reaches each book
        code, out = b.ocr()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(b.calls("ocr_book.py", b.names[0])), 1)                 # a read book is not read again

    def test_a_spent_budget_stops_between_two_books(self) -> None:
        b = self.b
        b.begin()
        code, out = b.ocr(env={"OCR_PHASE_DEADLINE": f"{time.time() - 1:.3f}"})
        self.assertEqual(code, 75, out)
        self.assertIn("1 of 2 book(s) read; the time budget is spent", out)
        self.assertEqual(b.ocr()[0], 0)

    def test_the_round_says_when_the_ocr_ended_before_every_book_was_read(self) -> None:
        b = self.b
        b.begin()
        b.settle()
        log = b.batch / "ocr.log"
        log.write_text("[ocr-phase] RESULT exit=76 BUSY - another job holds the GPU; next call: ...\n")
        os.utime(log, (time.time() - 60, time.time() - 60))
        code, out = b.round(wait=5)
        self.assertEqual(code, 4, out)
        self.assertIn("RESULT exit=4 OCR STOPPED", out)
        self.assertIn("ended with: [ocr-phase] RESULT exit=76 BUSY", out)
        self.assertIn(f"--ocr {b.batch}", out)

    def test_two_ocr_calls_do_not_read_one_batch_together(self) -> None:
        b = self.b
        b.begin()
        b.knob(b.names[0], "page-seconds", 0.5)
        b.ocr_in_background()
        time.sleep(1.5)
        code, out = b.ocr()
        self.assertEqual(code, 2, out)
        self.assertIn("another --ocr call is reading this batch", out)

    def test_the_ocr_call_renders_a_book_nobody_rendered(self) -> None:
        b = self.b
        b.begin()
        b.settle()
        shutil.rmtree(b.work / b.names[1])
        (b.batch / "marks" / f"{b.names[1]}.render.exit").unlink()
        (b.batch / "marks" / f"{b.names[1]}.render.started").unlink(missing_ok=True)
        code, out = b.ocr()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(b.calls("survey_pdf.py", b.names[1])), 2)


class StartTests(BatchCase):
    def test_the_same_pdfs_name_the_same_batch_and_keep_what_it_has_reached(self) -> None:
        b = self.b
        b.begin()
        first = b.batch
        self.assertEqual(b.ocr()[0], 0)
        self.assertEqual(b.round()[0], 10)
        b.begin()
        self.assertEqual(b.batch, first)
        self.assertEqual([entry["state"] for entry in b.record()["books"]], ["waiting", "waiting"])
        self.assertEqual(len(b.calls("survey_pdf.py")), 2)   # nothing is rendered twice

    def test_a_book_folder_that_holds_the_same_pdf_is_taken_as_it_is(self) -> None:
        b = self.b
        mine = b.work / "my-own-name"
        (mine / "renders").mkdir(parents=True)
        (mine / "page-manifest.json").write_text(json.dumps(
            {"source_pdf": str(b.pdfs[0]), "pages": [{"scan_page": 1}, {"scan_page": 2}]}))
        b.begin()
        self.assertEqual([entry["name"] for entry in b.record()["books"]], ["my-own-name", b.names[1]])
        b.settle()
        self.assertEqual([call["book"] for call in b.calls("survey_pdf.py")], [b.names[1]])

    def test_a_name_already_taken_gets_a_number(self) -> None:
        b = self.b
        (b.work / b.names[0]).mkdir(parents=True)             # another book's folder, by chance of the same name
        b.begin()
        self.assertEqual([entry["name"] for entry in b.record()["books"]], [f"{b.names[0]}-2", b.names[1]])

    def test_only_pdfs_make_a_batch(self) -> None:
        b = self.b
        code, out = b.call(str(b.root / "library" / "SM0001"), "--work-root", str(b.work))
        self.assertEqual(code, 2, out)
        self.assertIn("is not a PDF file", out)
        self.assertFalse(b.work.exists())

    def test_with_a_local_model_on_the_gpu_the_ocr_goes_through_one_hand_over(self) -> None:
        b = self.b
        b.env["OCR_PHASE_RELEASE_CMD"] = "true"
        out = b.begin()
        self.assertIn("NEXT 1, the OCR of every book in one hand-over window", out)
        batch = re.escape(str(b.batch))
        self.assertRegex(out, rf"run_ocr_phase\.py --background -- \S+ \S+ocr_batch\.py --ocr {batch} > ")
        self.assertRegex(out, rf"in the foreground, again while it ends with 75: \S+ \S+run_ocr_phase\.py -- \S+ "
                              rf"\S+ocr_batch\.py --ocr {batch} > ")

    def test_status_changes_nothing(self) -> None:
        b = self.b
        b.begin()
        b.settle()
        before = (b.batch / "batch.json").read_bytes()
        code, out = b.call("--status", str(b.batch))
        self.assertEqual(code, 0, out)
        self.assertIn(f"[batch] {b.names[0]}: new; 6 page(s), read {{'hunyuan': 0, 'paddle': 0}}, sealed 0", out)
        self.assertEqual((b.batch / "batch.json").read_bytes(), before)


class HttpBackendTests(BatchCase):
    def test_the_local_model_proofreads_after_the_ocr_one_book_at_a_time_and_nobody_is_asked(self) -> None:
        b = self.b
        b.begin()
        code, out = b.call("--round", str(b.batch), "--backend", "http", "--wait", "2", "--poll", "0.1")
        self.assertEqual(code, 75, out)
        self.assertEqual(b.calls("proofread_pages.py"), [])
        self.assertEqual(b.ocr()[0], 0)
        for _ in range(10):
            code, out = b.call("--round", str(b.batch), "--backend", "http", "--wait", "20", "--poll", "0.1")
            if code != 75:
                break
        self.assertEqual(code, 0, out)
        self.assertFalse((b.batch / "plans").exists())
        for name in b.names:
            self.assertEqual(b.calls("book_profile.py", name)[0]["argv"][4:], ["--backend", "http"])
            self.assertEqual(b.calls("proofread_pages.py", name)[0]["argv"][5:], ["--backend", "http"])
        # One book's steps at a time: the model serves one proofreading.
        spans = {}
        for item in b.events():
            if item.get("step") in ("profile", "proofread", "finish"):
                spans.setdefault(item["book"], []).append(item["t"])
        self.assertLessEqual(max(spans[b.names[0]]), min(spans[b.names[1]]) + 0.2)


class SingleEngineTests(BatchCase):
    def test_a_single_engine_route_has_no_profile_and_keeps_its_work(self) -> None:
        b = self.b
        if sys.platform == "darwin":
            self.skipTest("Hunyuan alone is a Linux route")
        b.begin("--engine", "hunyuan")
        self.assertEqual(b.record()["route"], "hunyuan")
        self.assertEqual(b.ocr()[0], 0)
        code, out, _ = b.until_done()
        self.assertEqual(code, 0, out)
        name = b.names[0]
        self.assertEqual(b.calls("ocr_book.py", name)[0]["argv"], [str(b.work / name), "--engine", "hunyuan"])
        self.assertEqual(b.calls("book_profile.py"), [])
        self.assertEqual(b.calls("proofread_pages.py", name)[0]["argv"],
                         [str(b.work / name / "renders"), str(b.work / name / "ocr-hunyuan"),
                          str(b.work / name / "proofread"), "--single-engine", "--max-bundles", "20",
                          "--max-items-per-bundle", "10"])
        self.assertEqual(b.calls("finish_book.py", name)[0]["argv"],
                         [str(b.work / name), "--single-engine", "--ocr-a", str(b.work / name / "ocr-hunyuan")])
        self.assertEqual(b.calls("clean_book.py"), [])


class HandOverTests(unittest.TestCase):
    """run_ocr_phase.py knows the batch's OCR command as it knows ocr_book.py."""

    def setUp(self) -> None:
        import run_ocr_phase
        self.phase = run_ocr_phase
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)

    def record(self, route: str) -> None:
        (self.batch / "batch.json").write_text(json.dumps({"schema": 1, "route": route, "books": []}))

    def test_the_window_is_sized_for_the_engines_of_the_batchs_route(self) -> None:
        phase = self.phase
        self.record("hunyuan-paddle")
        runner = ["python3", "scripts/ocr_batch.py", "--ocr", str(self.batch)]
        one_book = ["python3", "scripts/ocr_book.py", str(self.batch)]
        self.assertEqual(phase.engine_need(runner), phase.engine_need(one_book))
        self.assertEqual(phase.startup_need(runner), phase.startup_need(one_book))
        self.assertGreater(phase.startup_need(runner)[0], 0)
        if sys.platform != "darwin":                        # Hunyuan alone is a Linux route
            self.record("hunyuan")
            self.assertEqual(phase.engine_need(runner), phase.engine_need([*one_book, "--engine", "hunyuan"]))

    def test_only_the_ocr_call_of_a_batch_runs_in_a_window(self) -> None:
        phase = self.phase
        self.record("hunyuan-paddle")
        for runner in (["python3", "scripts/ocr_batch.py", "--round", str(self.batch)],
                       ["python3", "scripts/ocr_batch.py", "A.pdf", "B.pdf"]):
            with self.assertRaisesRegex(phase.PhaseError, "only `--ocr BATCH` runs in a hand-over window"):
                phase.engine_need(runner)
        with self.assertRaisesRegex(phase.PhaseError, "Cannot read the batch's OCR route"):
            phase.engine_need(["python3", "scripts/ocr_batch.py", "--ocr", str(self.batch / "none")])


if __name__ == "__main__":
    unittest.main()
