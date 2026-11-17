"""The two-engine default and its plumbing, model-off.

proofread_pages.py finds engine B's drafts without --draft2-directory and
needs --single-engine for one engine; engine roles are checked from the
drafts' JSON; a page missing one engine's draft is proofread from the other's
with a doubt; a short two-engine document needs no profile; packet runs
report pending waves behind failed pages (exit 11), cap the questions per
bundle, record the answering model and its usage, and take amendments.
finish_book.py honours the profile's sources, refuses swapped engines, labels
every doubt kind and treats "" as no source PDF.  The Hunyuan broker's
runtime folder follows XDG_RUNTIME_DIR/TMPDIR and is cleaned of dead brokers;
tests/run_all.py runs each file without the calling agent's model variables.
"""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))
import stand_in  # noqa: E402 - first: the closed endpoint
from stand_in import CLOSED_ENDPOINT  # noqa: E402
import _hunyuan_shared as hs  # noqa: E402
import _packet_client as pc  # noqa: E402
import apply_packet_answers as apply  # noqa: E402
import finish_book  # noqa: E402
import proofread_pages as pp  # noqa: E402
import run_all  # noqa: E402

TEXT = "甲乙丙丁戊己庚辛壬癸。"
# One text a page: identical pages would ask one identical packet question.
TEXTS = (TEXT, "子丑寅卯辰巳午未申酉。", "東南西北春夏秋冬。", "金木水火土日月星。", "一二三四五六七八九十。")


class Model(stand_in.StandIn):
    """The stand-in, which also transcribes: the writer's answer is its hint."""

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        if system == pp.WRITER_SYSTEM:
            self.calls.append((kind, user_text))
            return stand_in.between(user_text), {"prompt_tokens": 0, "completion_tokens": 0,
                                                 "reasoning_tokens": 0}
        return super().ask_answering(system, user_text, image_bytes, max_tokens, kind)


def page_a(text: str, engine: str | None = pp.ENGINE_A_NAME, label: str | None = "text") -> dict:
    line = {"text": text, "boundingBox": [0.1, 0.1, 0.8, 0.1]}
    if label is not None:
        line["blockLabel"] = label
    return {**({"engine": engine} if engine else {}), "renderWidth": 1000, "renderHeight": 1400,
            "lines": [line]}


def page_b(text: str, engine: str | None = pp.ENGINE_B_NAME) -> dict:
    return {**({"engine": engine} if engine else {}),
            "lines": [{"text": text, "boundingBox": [0, 0, 1, 1], "blockLabel": "whole-page"}]}


def write_book(root: Path, pages: int = 2, *, a: str = "ocr-paddle", b: str | None = "ocr-hunyuan",
               a_json=page_a, b_json=page_b, missing_a=(), missing_b=()) -> Path:
    from PIL import Image

    for name in ("renders", a, *((b,) if b else ())):
        (root / name).mkdir(parents=True, exist_ok=True)
    for scan in range(1, pages + 1):
        stem, text = f"page-{scan:04d}", TEXTS[(scan - 1) % len(TEXTS)]
        Image.new("L", (1000, 1400), 255).save(root / "renders" / f"{stem}.png")
        if scan not in missing_a:
            (root / a / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
            if a_json is not None:
                (root / a / f"{stem}.json").write_text(json.dumps(a_json(text), ensure_ascii=False),
                                                       encoding="utf-8")
        if b and scan not in missing_b:
            (root / b / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
            if b_json is not None:
                (root / b / f"{stem}.json").write_text(json.dumps(b_json(text), ensure_ascii=False),
                                                       encoding="utf-8")
    return root


def run(root: Path, *args: str, drafts: str = "ocr-paddle", model=None, backend: str = "http"):
    """proofread_pages.main on ROOT's book: (exit, stdout, stderr, report or None)."""
    model = model or Model()
    transport = (["--backend", "http", "--endpoint", CLOSED_ENDPOINT, "--model", "stand-in"]
                 if backend == "http" else ["--backend", "packet"])
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(pp, "Client", lambda *a, **k: model), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pp.main([str(root / "renders"), str(root / drafts), str(root / "out"), "--workers", "1",
                        "--report", str(root / "report.json"), *transport, *args])
    report = json.loads((root / "report.json").read_text()) if (root / "report.json").is_file() else None
    return code, out.getvalue(), err.getvalue(), report


def sealed(root: Path, scan: int) -> dict:
    return json.loads((root / "out" / f"page-{scan:04d}.json").read_text(encoding="utf-8"))


class EngineBDefaultTests(unittest.TestCase):
    def test_sibling_engine_b_folder_is_found_and_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp))
            code, out, err, report = run(root)
            self.assertEqual(code, 0, err)
            self.assertIn(f"engine B's drafts: {root / 'ocr-hunyuan'}", out)
            self.assertEqual(report["draftDirectories"]["B"], str(root / "ocr-hunyuan"))
            self.assertFalse(report["singleEngine"])
            self.assertEqual({sealed(root, scan)["mode"] for scan in (1, 2)}, {"merge"})

    def test_no_engine_b_and_no_switch_is_an_error_naming_the_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), b=None)
            code, _, err, report = run(root)
            self.assertEqual(code, 2)
            self.assertIn("no engine B drafts", err)
            self.assertIn("--draft2-directory", err)
            self.assertIn("--single-engine", err)
            self.assertNotIn("book_profile", err)
            self.assertIsNone(report)
            self.assertFalse((root / "out").exists())

    def test_single_engine_switch_reads_one_engine_even_beside_engine_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp))
            code, _, err, report = run(root, "--single-engine")
            self.assertEqual(code, 0, err)
            self.assertTrue(report["singleEngine"])
            self.assertEqual({sealed(root, scan)["mode"] for scan in (1, 2)}, {"transcribe"})
            self.assertFalse(any(sealed(root, scan)["doubts"] for scan in (1, 2)))
            code, _, err, _ = run(root, "--single-engine", "--draft2-directory", str(root / "ocr-hunyuan"))
            self.assertEqual(code, 2)
            self.assertIn("cannot be combined", err)

    def test_a_named_engine_b_folder_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp))
            code, _, err, _ = run(root, "--draft2-directory", str(root / "no-such-folder"))
            self.assertEqual(code, 2)
            self.assertIn("does not exist", err)

    def test_profile_sources_name_engine_b_before_the_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp))
            write_book(root, b="ocr-hunyuan-v2")
            (root / "book-profile.json").write_text(json.dumps({
                "preferEngine": "A",
                "sources": {"draftA": str(root / "ocr-paddle"), "draftB": str(root / "ocr-hunyuan-v2")}}))
            code, out, err, report = run(root)
            self.assertEqual(code, 0, err)
            self.assertEqual(report["draftDirectories"]["B"], str(root / "ocr-hunyuan-v2"))


class EngineRoleTests(unittest.TestCase):
    def refused(self, why: str, **book) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), **book)
            code, _, err, _ = run(root)
            self.assertEqual(code, 2, err)
            self.assertIn(why, err)
            self.assertFalse((root / "out").exists())
            return err

    def test_swapped_engines_are_refused(self):
        err = self.refused("engine roles swapped",
                           a_json=lambda text: page_b(text),
                           b_json=lambda text: page_a(text))
        self.assertIn("pass", err)

    def test_other_engines_in_either_role_are_refused(self):
        err = self.refused("wrong engine roles", a_json=lambda text: page_b(text))
        self.assertIn("--single-engine", err)
        self.refused("wrong engine roles", b_json=lambda text: page_b(text, "Apple Vision VNRecognizeTextRequest"))
        # No engine named, but only engine B's whole-page labels: not engine A.
        self.refused("wrong engine roles", a_json=lambda text: page_b(text, engine=None))

    def test_drafts_that_do_not_say_are_taken_as_given(self):
        for book in ({"a_json": lambda text: page_a(text, engine=None)}, {"b_json": None},
                     {"a_json": lambda text: page_a(text, engine=None, label=None), "b_json": None}):
            with self.subTest(book=sorted(book)), tempfile.TemporaryDirectory() as tmp:
                root = write_book(Path(tmp), **book)
                self.assertEqual(run(root)[0], 0)

    def test_finish_book_refuses_swapped_engines_and_follows_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = write_book(Path(tmp) / "book", pages=1)
            write_book(book, pages=1, a="ocr-paddle-v2", b=None)
            (book / "proofread").mkdir()
            (book / "proofread" / "page-0001.md").write_text(TEXT)
            (book / "proofread" / "page-0001.json").write_text(json.dumps({"provenance": {"markdownSha256": "x"}}))
            (book / "page-manifest.json").write_text("{}")
            profile = book / "book-profile.json"

            def inputs(*argv: str):
                return finish_book.resolve_inputs(book, finish_book.build_parser().parse_args([str(book), *argv]))

            # The profile's sources over the layout's default folder name.
            profile.write_text(json.dumps({"sources": {"draftA": str(book / "ocr-paddle-v2"),
                                                       "draftB": str(book / "ocr-hunyuan")}}))
            self.assertEqual(inputs().ocr_a, book / "ocr-paddle-v2")
            # A copied book: the profile's folders by name in this book.
            profile.write_text(json.dumps({"sources": {"draftA": "/elsewhere/book/ocr-paddle-v2",
                                                       "draftB": "/elsewhere/book/ocr-hunyuan"}}))
            self.assertEqual(inputs().ocr_a, book / "ocr-paddle-v2")
            # Swapped, as the profile names them or as given.
            profile.write_text(json.dumps({"sources": {"draftA": str(book / "ocr-hunyuan"),
                                                       "draftB": str(book / "ocr-paddle")}}))
            for argv in ((), ("--ocr-a", str(book / "ocr-hunyuan"), "--ocr-b", str(book / "ocr-paddle"))):
                with self.subTest(argv=argv), self.assertRaises(finish_book.FinishError) as refused:
                    inputs(*argv)
                self.assertEqual(refused.exception.code, finish_book.EXIT_USAGE)
                self.assertIn("engine roles swapped", str(refused.exception))
                self.assertIn("--ocr-a", str(refused.exception))
            # One engine: no roles to check.
            self.assertEqual(inputs("--single-engine", "--ocr-a", str(book / "ocr-hunyuan")).ocr_a,
                             book / "ocr-hunyuan")


class MissingEngineTests(unittest.TestCase):
    def test_a_page_missing_engine_b_waits_for_it_unless_allowed(self):
        # A partial engine B folder (its OCR still running, or interrupted):
        # the pages it lacks are not sealed from engine A alone by default.
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), pages=4, missing_b=(2, 4))
            code, out, err, report = run(root)
            self.assertEqual(code, 1, err)
            self.assertIn("WARNING engine B's drafts", err)
            self.assertIn("lack 2 of the 4 selected page(s): pages 2,4", err)
            self.assertIn("--allow-missing-engine-b", err)
            self.assertIn("NOT PROOFREAD (counts.engineBMissing)", out)
            counts = report["counts"]
            self.assertEqual((counts["completed"], counts["failed"], counts["engineBMissing"], counts["oneEngine"]),
                             (2, 2, 2, 0))
            self.assertEqual(pp.page_ranges([7, 2, 3, 4, 9, 10]), "2-4,7,9-10")
            for scan in (2, 4):
                entry = report["pages"][scan - 1]
                self.assertEqual(entry["status"], "failed")
                self.assertIn("missing engine B's draft", entry["error"])
                self.assertFalse((root / "out" / f"page-{scan:04d}.json").exists())
            # Engine B's draft arrives: the page is proofread from both engines.
            (root / "ocr-hunyuan" / "page-0002.txt").write_text(TEXTS[1] + "\n", encoding="utf-8")
            code, _, err, report = run(root, "--pages", "2")
            self.assertEqual(code, 0, err)
            self.assertEqual((sealed(root, 2)["mode"], sealed(root, 2)["doubts"]), ("merge", []))
            # A packet run reports the waiting pages beside its pending wave (11).
            code, out, err, report = run(root, "--pages", "3-4", "--overwrite", backend="packet")
            self.assertEqual((code, report["counts"]["engineBMissing"]), (pp.EXIT_PACKETS_PENDING_WITH_FAILURES, 1))

    def test_a_page_missing_engine_b_is_transcribed_from_engine_a_with_a_doubt_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), missing_b=(2,))
            code, out, err, report = run(root, "--allow-missing-engine-b")
            self.assertEqual(code, 0, err)
            self.assertNotIn("WARNING engine B's drafts", err)
            first, second = sealed(root, 1), sealed(root, 2)
            self.assertEqual((first["mode"], first["doubts"]), ("merge", []))
            self.assertEqual(second["mode"], "transcribe")
            self.assertEqual([d["kind"] for d in second["doubts"]], [pp.MISSING_ENGINE_DOUBT])
            self.assertIn("ENGINE B DRAFT MISSING", second["doubts"][0]["detail"])
            self.assertFalse(second["auditorClean"])
            self.assertEqual((report["counts"]["oneEngine"], report["counts"]["engineBMissing"]), (1, 0))
            self.assertEqual(report["doubtPages"], {pp.MISSING_ENGINE_DOUBT: 1})
            self.assertIn("1 page(s) had one engine's draft only", out)
            # Sealed from engine A alone, it stays current without the switch.
            code, _, err, report = run(root)
            self.assertEqual((code, report["counts"]["skippedCurrent"], report["counts"]["oneEngine"]), (0, 2, 1), err)
            # Engine B's draft added later: the page is not current any more.
            (root / "ocr-hunyuan" / "page-0002.txt").write_text(TEXTS[1] + "\n", encoding="utf-8")
            code, _, _, report = run(root)
            self.assertEqual(code, 1)
            self.assertIn("engine B's draft was added", report["pages"][1]["error"])
            code, _, err, report = run(root, "--overwrite", "--pages", "2")
            self.assertEqual(code, 0, err)
            self.assertEqual((sealed(root, 2)["mode"], sealed(root, 2)["doubts"]), ("merge", []))

    def test_a_page_missing_engine_a_falls_back_to_engine_b_with_a_doubt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), missing_a=(2,))
            code, _, err, report = run(root)
            self.assertEqual(code, 0, err)
            record = sealed(root, 2)
            self.assertEqual(record["mode"], "transcribe")
            self.assertEqual([d["kind"] for d in record["doubts"]], [pp.MISSING_ENGINE_DOUBT])
            self.assertIn("ENGINE A DRAFT MISSING", record["doubts"][0]["detail"])
            self.assertEqual(record["provenance"]["draftSha256"],
                             pp.sha256_file(root / "ocr-hunyuan" / "page-0002.txt"))
            self.assertEqual((report["counts"]["failed"], report["counts"]["oneEngine"]), (0, 1))
            # Current on resume while engine A's draft is still missing.
            code, _, _, report = run(root)
            self.assertEqual((code, report["counts"]["skippedCurrent"]), (0, 2))

    def test_a_page_with_no_draft_of_either_engine_fails_and_is_counted_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_book(Path(tmp), missing_a=(2,), missing_b=(2,))
            code, _, _, report = run(root)
            self.assertEqual(code, 1)
            self.assertEqual((report["counts"]["failed"], report["counts"]["blocked"]), (1, 0))
            self.assertEqual(report["pages"][1], {"scanPage": 2, "status": "failed",
                                                 "error": "missing engine A's draft and engine B's draft"})
            self.assertEqual(report["exitStatus"], 1)

    def test_finish_book_labels_every_doubt_kind_it_lists(self):
        # Each in the section collect_items files it under.
        for kind, section in ((pp.MISSING_ENGINE_DOUBT, "text"), ("single-engine-review", "text"),
                              ("single-engine-boundary", "format")):
            self.assertEqual(finish_book.KINDS[kind][0], section)
            self.assertTrue(finish_book.KINDS[kind][1])


class ShortDocumentAndProfileTests(unittest.TestCase):
    def test_short_two_engine_document_needs_no_profile(self):
        for pages, code in ((pp.SHORT_DOC_MAX_PAGES, 0), (pp.SHORT_DOC_MAX_PAGES + 1, 2)):
            with self.subTest(pages=pages), tempfile.TemporaryDirectory() as tmp:
                root = write_book(Path(tmp), pages=pages)
                got, _, err, report = run(root, "--pages", "1")
                self.assertEqual(got, code, err)
                if code == 0:
                    self.assertEqual(report["enginePreference"]["engine"], "adjudicate")
                    self.assertIn("short document", report["enginePreference"]["source"])
                    self.assertEqual(report["enginePreference"]["profileSha256"], "")
                else:
                    self.assertIn("no engine preference", err)

    def test_book_profile_writes_the_same_short_document_engines(self):
        import book_profile
        self.assertEqual(book_profile.SHORT_DOC_MAX_PAGES, pp.SHORT_DOC_MAX_PAGES)
        engines = pp.short_document_engines(3)
        self.assertEqual((engines["preferEngine"], engines["stopped"]), ("adjudicate", "short-document fast path"))
        self.assertIn("3 page(s)", engines["reason"])

    def test_a_profile_for_other_drafts_says_which_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Drafts that do not name their engines, so only the profile tells.
            root = write_book(Path(tmp), a_json=lambda text: page_a(text, engine=None, label=None), b_json=None)
            write_book(root, a="unused", b="ocr-hunyuan-other")
            # Engine B reads one character differently, so a swap changes the digest.
            (root / "ocr-hunyuan" / "page-0001.txt").write_text("甲乙丙丁戊已庚辛壬癸。\n", encoding="utf-8")
            (root / "ocr-hunyuan-other" / "page-0001.txt").write_text("另一個讀法。\n", encoding="utf-8")
            profile = {"preferEngine": "A", "probePages": [1],
                       "sources": {"draftA": str(root / "ocr-paddle"), "draftB": str(root / "ocr-hunyuan"),
                                   "probeDraftsSha256": pp.draft_digest([1], root / "ocr-paddle",
                                                                        root / "ocr-hunyuan")}}
            (root / "book-profile.json").write_text(json.dumps(profile))
            self.assertEqual(run(root)[0], 0)
            for args, drafts, why in (
                    (("--single-engine",), "ocr-paddle", "drop --single-engine"),
                    (("--draft2-directory", str(root / "ocr-paddle")), "ocr-hunyuan", "engine roles swapped"),
                    (("--draft2-directory", str(root / "ocr-hunyuan-other")), "ocr-paddle",
                     f"pass --draft2-directory {root / 'ocr-hunyuan'}")):
                with self.subTest(args=args):
                    code, _, err, _ = run(root, "--profile", str(root / "book-profile.json"), *args, drafts=drafts)
                    self.assertEqual(code, 2)
                    self.assertIn(why, err)
                    self.assertNotIn("re-run scripts/book_profile.py", err)


class PacketPlumbingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = write_book(Path(self.tmp.name), pages=2, b=None)

    def packet(self, *args):
        return run(self.root, "--single-engine", *args, backend="packet")

    def answer_wave(self, *, usage=None, answered_by=None, extra=()):
        wave = sorted((self.root / "packets").glob("wave-*"))[-1]
        rows = []
        for bundle in sorted(wave.glob("bundle-*.json")):
            for item in json.loads(bundle.read_text())["items"]:
                rows.append({"request_id": item["request_id"], "answer": stand_in.between(item["user_text"]),
                             **({"usage": usage} if usage else {})})
        (wave / "answers-01.json").write_text(json.dumps({"answers": rows}, ensure_ascii=False))
        argv = [str(self.root / "packets"), str(wave), *(("--answered-by", answered_by) if answered_by else ()),
                *extra]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(apply.main(argv), 0)
        return out.getvalue()

    def test_pending_waves_are_not_hidden_behind_a_blocked_page(self):
        (self.root / "out").mkdir()
        (self.root / "out" / "page-0001.json").write_text("not a seal")
        code, out, _, report = self.packet()
        self.assertEqual(code, pc.EXIT_PACKETS_PENDING_WITH_FAILURES)
        self.assertEqual(code, 11)
        self.assertEqual((report["exitStatus"], report["counts"]["blocked"], report["counts"]["pending"]),
                         (11, 1, 1))
        self.assertIn("[packets] PENDING", out)
        self.assertIn("exit 11", out)
        self.assertTrue(report["packetWave"]["bundles"])
        (self.root / "out" / "page-0001.json").unlink()
        code, _, _, report = self.packet()
        self.assertEqual((code, report["exitStatus"]), (pc.EXIT_PACKETS_PENDING, 10))

    def test_packet_events_follow_apply_and_resume_without_republishing(self):
        write_book(self.root)
        # Western-only drafts ask the image writer once per page in the dual
        # route, so the fixture can replay simple answers through actual apply.
        for folder in ("ocr-paddle", "ocr-hunyuan"):
            for scan in (1, 2):
                (self.root / folder / f"page-{scan:04d}.txt").write_text(f"Page {scan}\n")
        def packet():
            return run(self.root, backend="packet")

        log = self.root / "events.jsonl"
        code, _, _, report = packet()
        self.assertEqual(code, 10)
        self.assertFalse(log.exists())
        wave = Path(report["packetWave"]["waveDir"])
        published = {path: path.read_bytes() for path in wave.iterdir()}
        with mock.patch.dict(os.environ, {"JYUT_OCR_PACKET_EVENTS": str(log), "JYUT_OCR_PACKET_CASE": "case-1"}):
            self.assertEqual(packet()[0], 10)
            self.assertTrue(all(path.read_bytes() == contents for path, contents in published.items()))
            self.answer_wave()
            checkpoint = self.root / "packets" / "answers.jsonl"
            answers = checkpoint.read_bytes()
            self.answer_wave()
            self.assertEqual(checkpoint.read_bytes(), answers)
            code, _, error, _ = packet()
            self.assertEqual(code, 0, error)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["resume", "emit", "apply", "apply", "resume", "seal", "seal"])
        self.assertEqual({row["case"] for row in rows}, {"case-1"})
        self.assertEqual({row["stage"] for row in rows}, {"proofread"})
        self.assertEqual(rows[0]["invocation_id"], rows[1]["invocation_id"])
        self.assertEqual(rows[1]["wave"], wave.name)
        self.assertEqual(rows[1]["bundles"][0]["sha256"], report["packetWave"]["bundles"][0]["sha256"])
        self.assertEqual((rows[2]["added"], rows[3]["added"], rows[3]["unchanged"]), (2, 0, 2))
        self.assertEqual(rows[4]["checkpoint_answers"], 2)
        self.assertEqual({row["scan_page"] for row in rows if row["event"] == "seal"}, {1, 2})

    def test_packet_event_log_cannot_target_the_answer_checkpoint(self):
        self.assertEqual(self.packet()[0], 10)
        checkpoint = self.root / "packets" / "answers.jsonl"
        with mock.patch.dict(os.environ, {"JYUT_OCR_PACKET_EVENTS": str(checkpoint)}), \
                contextlib.redirect_stderr(io.StringIO()) as warning:
            self.answer_wave()
        self.assertIn("packet event log unavailable", warning.getvalue())
        rows = [json.loads(line) for line in checkpoint.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("request_id" in row and "event" not in row for row in rows))

    def test_answering_model_and_its_usage_reach_the_page_and_the_report(self):
        self.assertEqual(self.packet()[0], 10)
        said = self.answer_wave(usage={"prompt_tokens": 100, "completion_tokens": 20,
                                       "completion_tokens_details": {"reasoning_tokens": 5}},
                                answered_by="test-answering-model")
        self.assertIn("answered by test-answering-model 2", said)
        code, out, err, report = self.packet()
        self.assertEqual(code, 0, err)
        record = sealed(self.root, 1)
        self.assertEqual([record["usage"][k] for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens")],
                         [100, 20, 5])
        self.assertEqual(record["usage"]["packet_answers"], 1)
        self.assertEqual(record["calls"]["writer"]["packetAnswers"], 1)
        self.assertEqual(report["packetAnsweredBy"], {"test-answering-model": 2})
        self.assertEqual(report["pages"][0]["reasoningTokens"], 5)
        self.assertIn("answers replayed, by answering model: test-answering-model 2", out)

    def test_an_answer_with_no_usage_leaves_the_page_usage_unknown(self):
        self.assertEqual(self.packet()[0], 10)
        self.answer_wave()
        code, _, err, report = self.packet()
        self.assertEqual(code, 0, err)
        self.assertIsNone(sealed(self.root, 1)["usage"]["prompt_tokens"])
        self.assertEqual(report["packetAnsweredBy"], {"unrecorded": 2})


class UsageAndBundleTests(unittest.TestCase):
    def test_packet_answers_count_once_and_other_replays_not_at_all(self):
        answered = {"prompt_tokens": 80, "completion_tokens": 30, "reasoning_tokens": 20, "cached_tokens": 0,
                    "reused": True, "charged": False, "usage_source": "answer-record", "response_id": "packet:a"}
        usage = pp.aggregate_usage([answered, dict(answered),
                                    {**answered, "usage_source": None, "response_id": "other"}])
        self.assertEqual([usage[k] for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens")],
                         [80, 30, 20])
        self.assertEqual((usage["packet_answers"], usage["charged_calls"], usage["reused_calls"]), (1, 0, 1))
        unknown = pp.aggregate_usage([{**answered, "usage_source": "unavailable", "prompt_tokens": None}])
        self.assertIsNone(unknown["prompt_tokens"])
        self.assertEqual(unknown["unknown_calls"], 1)

    def test_bundles_hold_at_most_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            for cap, bundles, count in ((3, 2, 10), (pc.DEFAULT_MAX_ITEMS_PER_BUNDLE, 8, 200), (50, 8, 10)):
                with self.subTest(cap=cap, bundles=bundles, count=count):
                    client = pc.PacketClient(Path(tmp) / f"packets-{cap}-{count}", max_bundles=bundles,
                                             max_items_per_bundle=cap)
                    for index in range(count):
                        with self.assertRaises(pc.PacketPending):
                            client.ask_answering("system", f"question {index}", None, 64, kind="writer")
                    wave = client.write_wave_bundles()
                    sizes = [row["itemCount"] for row in wave["bundles"]]
                    self.assertEqual(sum(sizes), count)
                    self.assertLessEqual(max(sizes), cap)
                    self.assertEqual(len(sizes), max(min(count, bundles), -(-count // cap)))

    def test_proofread_takes_the_cap_from_the_command_line(self):
        args = pp.build_parser().parse_args(["r", "d", "o"])
        self.assertEqual(args.max_items_per_bundle, pc.DEFAULT_MAX_ITEMS_PER_BUNDLE)
        self.assertEqual(pp.build_parser().parse_args(["r", "d", "o", "--max-items-per-bundle", "5"])
                         .max_items_per_bundle, 5)


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "packets"
        client = pc.PacketClient(self.root)
        with contextlib.suppress(pc.PacketPending), pc.packet_page_context(7):
            client.ask_answering("system", "question", None, 64, kind="writer")
        self.wave = Path(client.write_wave_bundles()["waveDir"])
        self.rid = json.loads((self.wave / "bundle-01.json").read_text())["items"][0]["request_id"]

    def apply(self, answer, *extra, answered_by=None):
        row = {"request_id": self.rid, "answer": answer, **({"answered_by": answered_by} if answered_by else {})}
        (self.wave / "answers-01.json").write_text(json.dumps({"answers": [row]}, ensure_ascii=False))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = apply.main([str(self.root), str(self.wave), *extra])
        return code, out.getvalue(), err.getvalue()

    def lines(self):
        return [json.loads(line) for line in (self.root / "answers.jsonl").read_text().splitlines()]

    def test_an_amendment_supersedes_the_answer_and_keeps_it_as_the_audit_trail(self):
        self.assertEqual(self.apply("甲", "--answered-by", "model-one")[0], 0)
        code, _, err = self.apply("乙")
        self.assertEqual(code, 2)
        self.assertIn("--amend", err)
        self.assertNotIn("new amendment request", err)
        self.assertEqual(len(self.lines()), 1)
        code, out, _ = self.apply("乙", "--amend", "--reason", "misread character", "--answered-by", "model-two")
        self.assertEqual(code, 0)
        self.assertIn("1 amendment(s)", out)
        self.assertIn("--overwrite --pages 7", out)
        old, new = self.lines()
        self.assertEqual((old["answer"], old["answered_by"]), ("甲", "model-one"))
        self.assertEqual((new["answer"], new["answered_by"]), ("乙", "model-two"))
        self.assertEqual(new["amends"]["answer_sha256"], pc.answer_sha256(old))
        self.assertEqual(new["amends"]["reason"], "misread character")
        self.assertNotEqual(new["response_id"], old["response_id"])
        self.assertEqual(pc.load_answers_map(self.root / "answers.jsonl")[self.rid]["answer"], "乙")
        answer, metrics = pc.PacketClient(self.root).ask_answering("system", "question", None, 64, kind="writer")
        self.assertEqual((answer, metrics["answered_by"]), ("乙", "model-two"))
        # The same amendment again is nothing new; the old answer again is a conflict.
        self.assertEqual(self.apply("乙", "--amend")[0], 0)
        self.assertEqual(len(self.lines()), 2)
        self.assertEqual(self.apply("甲")[0], 2)

    def test_answered_by_from_the_answer_wins_and_must_be_a_model_id(self):
        self.assertEqual(self.apply("甲", "--answered-by", "flag-model", answered_by="own-model")[0], 0)
        self.assertEqual(self.lines()[0]["answered_by"], "own-model")
        for extra in (("--answered-by", ""), ("--answered-by", "a\nb"), ("--reason", "no amendment")):
            with self.subTest(extra=extra):
                self.assertEqual(self.apply("乙", *extra)[0], 2)

    def test_amendments_that_do_not_follow_the_current_answer_are_refused(self):
        path = self.root / "answers.jsonl"
        first = {"request_id": self.rid, "answer": "甲"}
        stale = {"request_id": self.rid, "answer": "丙", "response_id": "packet:x",
                 "amends": {"answer_sha256": "0" * 64}}
        path.write_text(json.dumps(first) + "\n" + json.dumps(stale) + "\n")
        with self.assertRaisesRegex(ValueError, "does not amend the current answer"):
            pc.load_answers_map(path)
        path.write_text(json.dumps(stale) + "\n")
        with self.assertRaisesRegex(ValueError, "amends no recorded answer"):
            pc.load_answers_map(path)


class RuntimeFolderTests(unittest.TestCase):
    def root_for(self, **env) -> Path:
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(hs, "_private_directory"):
            for name in ("XDG_RUNTIME_DIR", "TMPDIR"):
                if name not in env:
                    os.environ.pop(name, None)
            return hs._runtime_root()

    def test_runtime_folder_follows_xdg_then_tmpdir_and_fits_a_socket_path(self):
        # Nothing is made: _private_directory is replaced.
        name = f"jyut-ocr-{os.getuid()}"
        tmp = Path("/tmp").resolve() / name
        self.assertEqual(self.root_for(XDG_RUNTIME_DIR="/", TMPDIR="/tmp"), Path("/") / name)
        self.assertEqual(self.root_for(TMPDIR="/"), Path("/") / name)
        self.assertEqual(self.root_for(), tmp)
        self.assertEqual(self.root_for(XDG_RUNTIME_DIR="relative/dir"), tmp)
        self.assertEqual(self.root_for(XDG_RUNTIME_DIR="/no/such/folder"), tmp)
        with tempfile.TemporaryDirectory() as folder:
            deep = Path(folder) / ("d" * (hs.MAX_SOCKET_PATH - hs.SOCKET_TAIL))
            deep.mkdir()
            # Too long for a socket path: the next one.
            self.assertEqual(self.root_for(XDG_RUNTIME_DIR=str(deep), TMPDIR="/"), Path("/") / name)
            self.assertEqual(self.root_for(XDG_RUNTIME_DIR=str(deep)), tmp)

    def broker_files(self, partition: Path, prefix: str, age: float, sock: bool = True) -> list[Path]:
        partition.mkdir(mode=0o700, exist_ok=True)
        paths = [partition / (prefix + suffix) for suffix in (".json", ".log", ".llama.log", ".owner", ".launch")]
        for path in paths:
            path.write_text("x")
        if sock:
            cwd = os.getcwd()
            os.chdir(partition)
            try:
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(prefix + ".sock")   # relative: short enough for any folder
                listener.close()                  # a dead socket
            finally:
                os.chdir(cwd)
            paths.append(partition / (prefix + ".sock"))
        stamp = time.time() - age
        for path in paths:
            os.utime(path, (stamp, stamp), follow_symlinks=False)
        return paths

    def test_dead_old_brokers_go_and_live_or_recent_ones_stay(self):
        old = hs.STALE_RUNTIME_SECONDS + 60
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dead = self.broker_files(root / ("a" * 20), "1" * 32, old)
            live = self.broker_files(root / ("b" * 20), "2" * 32, old)
            recent = self.broker_files(root / ("c" * 20), "3" * 32, 60)
            mixed = self.broker_files(root / ("d" * 20), "4" * 32, old, sock=False)
            (root / ("d" * 20) / "notes.txt").write_text("not a broker's")
            (root / "unrelated").mkdir()
            with open(live[3], "r+") as owner:   # a live broker holds its owner lock
                fcntl.flock(owner, fcntl.LOCK_EX)
                removed = hs._clean_stale(root)
            self.assertFalse((root / ("a" * 20)).exists())
            self.assertTrue(set(dead) <= set(removed))
            self.assertTrue(all(path.exists() or path.is_socket() for path in live + recent))
            self.assertTrue((root / ("d" * 20) / "notes.txt").exists())
            self.assertFalse(any(path.exists() for path in mixed))
            self.assertTrue((root / "unrelated").is_dir())

    def test_no_cleanup_while_another_runner_holds_the_folder(self):
        old = hs.STALE_RUNTIME_SECONDS + 60
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            self.broker_files(root / ("a" * 20), "1" * 32, old)
            other = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(other, fcntl.LOCK_SH)       # another runner, alive
            try:
                mine = hs._hold_runtime(root)
                self.assertTrue((root / ("a" * 20)).exists())
                os.close(mine)
            finally:
                os.close(other)
            mine = hs._hold_runtime(root)
            try:
                self.assertFalse((root / ("a" * 20)).exists())
                probe = os.open(root / ".lock", os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):   # held shared until close
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe)
            finally:
                os.close(mine)


class SuiteEnvironmentTests(unittest.TestCase):
    def test_each_file_runs_without_the_agent_model_variables(self):
        env = run_all.child_environment({"QWEN_CODE_MODEL": "local-27b", "QWEN_CODE_MODEL_IDENTITY": "x",
                                         "JYUT_OCR_AGENT_IS_LOCAL_MODEL": "1", "PROOFREAD_MODEL": "local-27b",
                                         "PROOFREAD_ENDPOINT": "http://127.0.0.1:8090/v1/chat/completions",
                                         "OCR_PHASE_RELEASE_CMD": "systemctl --user stop x",
                                         "OCR_PHASE_RESTORE_CMD": "systemctl --user start x",
                                         "JYUT_OCR_PACKET_EVENTS": "/live/packet-events.jsonl",
                                         "JYUT_OCR_PACKET_CASE": "live-case",
                                         "KEPT": "1"})
        self.assertEqual(env, {"KEPT": "1", "PROOFREAD_ENDPOINT": CLOSED_ENDPOINT, "PYTHONDONTWRITEBYTECODE": "1"})


if __name__ == "__main__":
    unittest.main()
