"""Horizontal layout, semantic cache identity and orchestration without models."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import sys
import shutil
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _draft_contract as contract
from _ocr_engines import resolve_engines, recorded_draft_directory, draft_a_directory
from _vision_layout import adapt_observations
import ocr_book
import proofread_pages as pp
import run_apple_vision_ocr as vision
from test_apple_vision_pool import Harness
from test_dual_default_plumbing import write_book, run, page_b


def layout(text="甲乙丙丁戊己庚辛壬癸。"):
    return adapt_observations({"engine": vision.ENGINE, "lines": [
        {"text": text, "confidence": 0.9, "boundingBox": [0.1, 0.7, 0.8, 0.1]}]})


class VisionLayoutTests(unittest.TestCase):
    def test_top_left_aggregation_and_raw_line_ids(self):
        raw = {"engine": vision.ENGINE, "lines": [
            {"text": "second", "confidence": 0.7, "boundingBox": [0.1, 0.75, 0.7, 0.04]},
            {"text": "first", "confidence": 0.9, "boundingBox": [0.1, 0.81, 0.7, 0.04]},
            {"text": "folio", "confidence": 0.8, "boundingBox": [0.45, 0.01, 0.1, 0.04]}]}
        result = adapt_observations(raw)
        self.assertEqual(result["rawObservations"][0]["boundingBox"], [0.1, 0.75, 0.7, 0.04])
        self.assertEqual(result["lines"][0]["text"], "first\nsecond")
        self.assertEqual(result["lines"][0]["lineIDs"], [1, 0])
        self.assertAlmostEqual(result["lines"][0]["boundingBox"][1], 0.15)
        self.assertAlmostEqual(result["lines"][0]["boundingBox"][3], 0.1)
        self.assertEqual(result["lines"][1]["furnitureCandidate"], "footer")
        self.assertEqual(result["lines"][1]["blockLabel"], "text")
        self.assertEqual(result, adapt_observations(raw))
        self.assertIsNone(contract.layout_problem(result, "first\nsecond\nfolio\n"))

    def test_nonfinite_and_invalid_geometry_refused(self):
        for box in ([0.1, 0.7, 1, 0.1], [0, 0, float("nan"), 0.1], [0, 0, 0, 0.1]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                adapt_observations({"lines": [{"text": "x", "confidence": 1, "boundingBox": box}]})

    def test_capability_and_text_contract_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = write_book(Path(temporary), pages=1, a="ocr-vision", a_json=layout)
            a, b = book / "ocr-vision", book / "ocr-hunyuan"
            self.assertIsNone(pp.engine_roles_problem(a, b))
            path = a / "page-0001.json"
            record = json.loads(path.read_text())
            record["engine"] = "Another honest layout engine"
            path.write_text(json.dumps(record))
            self.assertIsNone(pp.engine_roles_problem(a, b))
            record["layoutContract"]["coordinateSystem"] = "bottom-left"
            path.write_text(json.dumps(record))
            self.assertIn("invalid layout contract", pp.engine_roles_problem(a, b))
            path.write_text(json.dumps(layout()))
            (a / "page-0001.txt").write_text("different\n")
            self.assertIn("differs from TXT", pp.engine_roles_problem(a, b))
            (a / "page-0001.txt").write_text("甲乙丙丁戊己庚辛壬癸。\n")
            (a / "page-0002.txt").write_text("missing completion\n")
            self.assertIn("paired layout JSON missing", pp.engine_roles_problem(a, b))

    def test_single_engine_refuses_legacy_vision_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            def raw(text):
                result = layout(text)
                result.pop("layoutContract")
                result["lines"] = result.pop("rawObservations")
                return result
            book = write_book(Path(temporary), pages=1, a="ocr-vision", b=None, a_json=raw)
            code, _, error, _ = run(book, "--single-engine", drafts="ocr-vision")
            self.assertEqual(code, 2)
            self.assertIn("missing normalized top-left layout/text contract", error)

    def test_semantics_change_on_consumed_values_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "page.json"
            original = layout()
            original["ocrProfile"] = {"vlBackend": "mlx-vlm-server", "modelRevision": "abc",
                                      "execution": {"serverLog": "/old/log", "port": 111}}
            path.write_text(json.dumps(original))
            before = contract.semantic_digest(path)
            for field, value in (("boundingBox", [0.2, 0.2, 0.7, 0.1]), ("blockLabel", "title"),
                                 ("blockOrder", 1), ("text", "changed")):
                changed = copy.deepcopy(original)
                changed["lines"][0][field] = value
                path.write_text(json.dumps(changed))
                self.assertNotEqual(before, contract.semantic_digest(path), field)
            changed = copy.deepcopy(original)
            changed.update(generatedAt="new", renderFile="/another/book/page.png")
            changed["ocrProfile"]["execution"] = {"serverLog": "/new/log", "port": 222}
            path.write_text(json.dumps(changed))
            self.assertEqual(before, contract.semantic_digest(path))
            changed["ocrProfile"]["vlBackend"] = "other"
            path.write_text(json.dumps(changed))
            self.assertNotEqual(before, contract.semantic_digest(path))

    def test_profile_legacy_native_compatibility_and_new_backend_requirement(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = write_book(Path(temporary), pages=1)
            a, b = book / "ocr-paddle", book / "ocr-hunyuan"
            legacy = pp.draft_digest([1], a, b, legacy=True)
            self.assertTrue(pp.profile_digest_matches(legacy, [1], a, b))
            (a / "page-0001.json").write_text(json.dumps(layout()))
            self.assertFalse(pp.profile_digest_matches(legacy, [1], a, b))

    def test_model_off_proofread_seal_refuses_geometry_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = write_book(Path(temporary), pages=1, a="ocr-vision", a_json=layout)
            code, _, error, _ = run(book, drafts="ocr-vision")
            self.assertEqual(code, 0, error)
            record = json.loads((book / "out" / "page-0001.json").read_text())
            self.assertIn("draftSemanticsSha256", record["provenance"])
            path = book / "ocr-vision" / "page-0001.json"
            changed = json.loads(path.read_text())
            changed["lines"][0]["boundingBox"][0] = 0.11
            path.write_text(json.dumps(changed))
            code, output, error, report = run(book, drafts="ocr-vision")
            self.assertNotEqual(code, 0)
            self.assertIn("draftSemanticsSha256", output + error + json.dumps(report))

    def test_finish_uses_recorded_vision_and_refuses_mutated_layout(self):
        import finish_book
        from check_finish_book import phase3_book, reseal
        import repair_fixture as fx
        with tempfile.TemporaryDirectory() as temporary:
            book = phase3_book(Path(temporary), spec={1: fx.make_spec()[1]})
            (book / "book-profile.json").unlink()  # short documents need no profile
            a = book / "ocr-vision"
            shutil.copytree(book / "ocr-paddle", a)  # old Paddle output stays behind
            path = a / "page-0001.json"
            text = (a / "page-0001.txt").read_text()
            if not text.endswith("\n"):
                text += "\n"
                (a / "page-0001.txt").write_text(text)
            path.write_text(json.dumps(layout(text[:-1])))
            (book / "reports").mkdir(exist_ok=True)
            (book / "reports" / "ocr-engines.json").write_text(json.dumps({
                "schemaVersion": 1, "engines": ["vision", "hunyuan"],
                "draftA": "ocr-vision", "draftB": "ocr-hunyuan"}))
            args = finish_book.build_parser().parse_args([str(book)])
            inputs = finish_book.resolve_inputs(book, args)
            self.assertEqual(inputs.ocr_a, a)
            def semantic_seal(record):
                record["provenance"].update(draftSha256=pp.sha256_file(a / "page-0001.txt"),
                    draftSemanticsSha256=contract.semantic_digest(path),
                    draft2SemanticsSha256=contract.semantic_digest(book / "ocr-hunyuan" / "page-0001.json"))
            reseal(book, 1, semantic_seal)
            finish_book.read_sealed(inputs, finish_book.Log(io.StringIO()))
            changed = json.loads(path.read_text())
            changed["lines"][0]["blockLabel"] = "title"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(finish_book.FinishError, "layout/identity differs"):
                finish_book.read_sealed(inputs, finish_book.Log(io.StringIO()))
            # A stale long-book profile cannot silently select old Paddle.
            (book / "book-profile.json").write_text(json.dumps({"sources": {
                "draftA": str(book / "ocr-paddle"), "draftB": str(book / "ocr-hunyuan")}}))
            with self.assertRaisesRegex(finish_book.FinishError, "but the profile names"):
                finish_book.resolve_inputs(book, args)
            explicit = finish_book.build_parser().parse_args([str(book), "--ocr-a", str(a)])
            self.assertEqual(finish_book.resolve_inputs(book, explicit).ocr_a, a)
            # Even the single-engine route requires the new Vision contract.
            changed.pop("layoutContract")
            path.write_text(json.dumps(changed))
            explicit = finish_book.build_parser().parse_args([str(book), "--single-engine", "--ocr-a", str(a)])
            with self.assertRaisesRegex(finish_book.FinishError, "invalid layout contract"):
                finish_book.resolve_inputs(book, explicit)
            shutil.rmtree(a)
            with self.assertRaisesRegex(finish_book.FinishError, "its drafts are missing"):
                finish_book.resolve_inputs(book, args)


class VisionPhaseTests(unittest.TestCase):
    def test_partial_worker_response_cannot_outlive_phase_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "fake-vision-worker"
            binary.write_text(f"#!{sys.executable}\nimport sys,time\nsys.stdin.readline()\n"
                              "sys.stdout.write('{');sys.stdout.flush();time.sleep(30)\n")
            binary.chmod(0o700)
            with mock.patch.dict(os.environ, {"OCR_PHASE_DEADLINE": str(time.time() + 0.3)}, clear=True):
                worker = vision.SwiftPersistentWorker(binary, 1)
                try:
                    with self.assertRaises(vision.BudgetExpired):
                        worker.process({"scanPage": 1})
                finally:
                    worker.close()
                self.assertIsNotNone(worker.process_handle.poll())

    def test_engine_selection_and_orchestration(self):
        self.assertEqual(resolve_engines("hunyuan,vision", environ={}), ("vision", "hunyuan"))
        with tempfile.TemporaryDirectory() as temporary:
            book = Path(temporary)
            (book / "renders").mkdir()
            (book / "renders" / "page-0002.png").write_bytes(b"fixture")
            (book / "draft.md").write_text("existing draft")
            calls = []
            def step(command, env, progress):
                calls.append(command)
                return 0, time.time(), None, None
            with mock.patch.object(ocr_book, "run_step", side_effect=step), mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(ocr_book.main([str(book), "--engines", "vision,hunyuan"]), 0)
            self.assertEqual([Path(call[1]).name for call in calls],
                             ["run_hunyuanocr.py", "run_apple_vision_ocr.py", "run_hunyuanocr_second_look.py"])
            self.assertEqual(calls[1][-2:], ["--pages", "2"])
            self.assertIn(str(book / "ocr-vision"), calls[2])
            self.assertEqual(recorded_draft_directory(book, "A"), book / "ocr-vision")
            self.assertEqual(resolve_engines(book=book, environ={}), ("vision", "hunyuan"))
            self.assertEqual(resolve_engines(book=book, environ={"JYUT_OCR_ENGINES": "hunyuan,paddle"}),
                             ("hunyuan", "paddle"))
            self.assertEqual(resolve_engines("vision,hunyuan", book=book,
                             environ={"JYUT_OCR_ENGINES": "hunyuan,paddle"}), ("vision", "hunyuan"))
            self.assertEqual(resolve_engines(book=book, single_engine=True, environ={}), ("hunyuan",))
            self.assertEqual(draft_a_directory(book, ("vision",)), book / "ocr-vision")
            calls.clear()
            with mock.patch.object(ocr_book, "run_step", side_effect=step), mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(ocr_book.main([str(book)]), 0)
            self.assertEqual(Path(calls[1][1]).name, "run_apple_vision_ocr.py")

    def test_budget_defers_before_startup_and_records_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = Path(temporary)
            renders = book / "renders"
            renders.mkdir()
            (renders / "page-0001.png").write_bytes(b"fixture")
            harness = Harness()
            args = [str(renders), str(book / "ocr-vision"), "--pages", "1", "--report", str(book / "run.json")]
            env = {"OCR_PHASE_DEADLINE": str(time.time() - 1), "OCR_PHASE_PROGRESS": str(book / "progress")}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(vision.main(args, compiler=harness.compiler, worker_factory=harness.factory), 75)
            self.assertEqual(harness.calls, [])
            self.assertEqual(json.loads((book / "run.json").read_text())["counts"]["deferred"], 1)
            with mock.patch.dict(os.environ, {"OCR_PHASE_PROGRESS": str(book / "progress")}, clear=True):
                self.assertEqual(vision.main(args, compiler=harness.compiler, worker_factory=harness.factory), 0)
            self.assertEqual((book / "progress").read_text(), "1\n")


if __name__ == "__main__":
    unittest.main()
