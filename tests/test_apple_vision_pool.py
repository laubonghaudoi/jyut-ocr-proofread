from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
    / "run_apple_vision_ocr.py"
)


def load_module():
    name = "_test_run_apple_vision_ocr"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not import {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ocr = load_module()


class FakeWorker:
    def __init__(
        self,
        worker_id: int,
        calls: list[int],
        lock: threading.Lock,
        *,
        failed_pages: set[int] | None = None,
        delay: float = 0,
    ) -> None:
        self.worker_id = worker_id
        self.calls = calls
        self.lock = lock
        self.failed_pages = failed_pages or set()
        self.delay = delay
        self.closed = False

    def process(self, job):
        page = job["scanPage"]
        with self.lock:
            self.calls.append(page)
        if self.delay:
            time.sleep(self.delay)
        if page in self.failed_pages:
            raise ocr.PageOCRFailure(f"synthetic failure for page {page}")
        line = {
            "text": f"第{page}頁",
            "confidence": 0.875,
            "boundingBox": [0.1, 0.2, 0.3, 0.4],
        }
        raw = {
            "scanPage": page,
            "renderFile": job["renderFile"],
            "engine": job["profile"]["engine"],
            "recognitionLanguages": job["profile"]["recognitionLanguages"],
            "recognitionLevel": job["profile"]["recognitionLevel"],
            "usesLanguageCorrection": job["profile"]["usesLanguageCorrection"],
            "generatedAt": "2026-08-25T00:00:00Z",
            "lines": [line],
        }
        Path(job["jsonOutput"]).write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        Path(job["textOutput"]).write_text(f"第{page}頁\n", encoding="utf-8")
        return {"scanPage": page, "status": "ok", "lineCount": 1}

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, *, failed_pages: set[int] | None = None, delay: float = 0):
        self.calls: list[int] = []
        self.worker_ids: list[int] = []
        self.lock = threading.Lock()
        self.failed_pages = failed_pages or set()
        self.delay = delay

    def compiler(self, swift_source: Path, build_directory: Path):
        binary = build_directory / "mock-worker"
        binary.write_bytes(b"mock worker")
        return binary, {
            "compiler": "mock-swiftc",
            "compilerVersion": "mock 1.0",
            "workerBinarySha256": ocr.sha256_file(binary),
        }

    def factory(self, binary: Path, worker_id: int):
        self.assert_binary(binary)
        with self.lock:
            self.worker_ids.append(worker_id)
        return FakeWorker(
            worker_id,
            self.calls,
            self.lock,
            failed_pages=self.failed_pages,
            delay=self.delay,
        )

    @staticmethod
    def assert_binary(binary: Path) -> None:
        if binary.read_bytes() != b"mock worker":
            raise AssertionError("unexpected mock binary")


class AppleVisionPoolTests(unittest.TestCase):
    def make_layout(self, root: Path, pages: range | list[int]):
        renders = root / "renders"
        output = root / "vision"
        renders.mkdir()
        for page in pages:
            (renders / f"page-{page:04d}.png").write_bytes(
                f"render {page}".encode("ascii")
            )
        return renders, output

    def test_page_selectors_and_worker_bounds(self) -> None:
        self.assertEqual(ocr.parse_pages_expression("1,3-5,3,9"), [1, 3, 4, 5, 9])
        with self.assertRaises(ocr.ConfigurationError):
            ocr.parse_pages_expression("5-3")
        with self.assertRaises(ocr.ConfigurationError):
            ocr.parse_pages_expression("0,2")
        self.assertEqual(
            ocr.resolve_worker_count("auto", 20, physical_cores=12), 8
        )
        self.assertEqual(ocr.resolve_worker_count("auto", 3, physical_cores=12), 3)
        self.assertEqual(ocr.resolve_worker_count("4", 2, physical_cores=12), 2)
        for invalid in ("0", "9", "many"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ocr.ConfigurationError):
                    ocr.resolve_worker_count(invalid, 2, physical_cores=12)

    def test_pool_writes_compatible_sealed_outputs_and_external_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, list(range(1, 5)))
            report = root / "reports" / "run.json"
            harness = Harness(delay=0.01)
            returncode = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--first-page",
                    "1",
                    "--last-page",
                    "4",
                    "--workers",
                    "auto",
                    "--report",
                    str(report),
                ],
                compiler=harness.compiler,
                worker_factory=harness.factory,
                physical_cores_override=3,
            )

            self.assertEqual(returncode, 0)
            self.assertEqual(sorted(harness.calls), [1, 2, 3, 4])
            self.assertEqual(sorted(set(harness.worker_ids)), [1, 2, 3])
            page_json = json.loads((output / "page-0001.json").read_text())
            # Original reader fields remain present.
            for field in (
                "scanPage",
                "renderFile",
                "engine",
                "recognitionLanguages",
                "recognitionLevel",
                "usesLanguageCorrection",
                "generatedAt",
                "lines",
            ):
                self.assertIn(field, page_json)
            self.assertEqual(page_json["schemaVersion"], 2)
            self.assertEqual(page_json["status"], "complete")
            self.assertEqual(
                page_json["textAuthority"], "draft-only-not-text-authority"
            )
            self.assertEqual((output / "page-0001.txt").read_text(), "第1頁\n")
            report_json = json.loads(report.read_text())
            self.assertEqual(report_json["status"], "pass")
            self.assertEqual(report_json["workers"]["resolved"], 3)
            self.assertEqual(report_json["pageSets"]["completed"], [1, 2, 3, 4])
            self.assertEqual(report_json["fallbackPolicy"]["used"], [])
            self.assertFalse(ocr.path_is_within(report, output))

    def test_safe_resume_skips_only_current_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [2])
            first_report = root / "first.json"
            first = Harness()
            first_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "2",
                    "--report",
                    str(first_report),
                ],
                compiler=first.compiler,
                worker_factory=first.factory,
                physical_cores_override=8,
            )
            self.assertEqual(first_code, 0)

            def forbidden_compiler(*_args):
                raise AssertionError("resume unexpectedly compiled a worker")

            second_report = root / "second.json"
            second_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "2",
                    "--report",
                    str(second_report),
                ],
                compiler=forbidden_compiler,
                worker_factory=lambda *_args: None,
                physical_cores_override=8,
            )
            self.assertEqual(second_code, 0)
            resumed = json.loads(second_report.read_text())
            self.assertEqual(resumed["pageSets"]["skippedCurrent"], [2])
            self.assertEqual(resumed["workers"]["resolved"], 0)

    def test_mismatch_requires_overwrite_then_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            harness = Harness()
            self.assertEqual(
                ocr.main(
                    [str(renders), str(output), "--pages", "1", "--report", str(root / "a.json")],
                    compiler=harness.compiler,
                    worker_factory=harness.factory,
                    physical_cores_override=4,
                ),
                0,
            )
            (output / "page-0001.txt").write_text("tampered\n", encoding="utf-8")
            blocked = Harness()
            blocked_code = ocr.main(
                [str(renders), str(output), "--pages", "1", "--report", str(root / "b.json")],
                compiler=blocked.compiler,
                worker_factory=blocked.factory,
                physical_cores_override=4,
            )
            self.assertEqual(blocked_code, 1)
            self.assertEqual(blocked.calls, [])
            self.assertIn(
                "--overwrite",
                json.loads((root / "b.json").read_text())["errors"][0]["error"],
            )

            replacement = Harness()
            replacement_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1",
                    "--overwrite",
                    "--report",
                    str(root / "c.json"),
                ],
                compiler=replacement.compiler,
                worker_factory=replacement.factory,
                physical_cores_override=4,
            )
            self.assertEqual(replacement_code, 0)
            self.assertEqual(replacement.calls, [1])
            self.assertEqual((output / "page-0001.txt").read_text(), "第1頁\n")

    def test_render_hash_change_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            harness = Harness()
            self.assertEqual(
                ocr.main(
                    [str(renders), str(output), "--pages", "1", "--report", str(root / "a.json")],
                    compiler=harness.compiler,
                    worker_factory=harness.factory,
                    physical_cores_override=2,
                ),
                0,
            )
            (renders / "page-0001.png").write_bytes(b"changed render")
            code = ocr.main(
                [str(renders), str(output), "--pages", "1", "--report", str(root / "b.json")],
                compiler=Harness().compiler,
                worker_factory=Harness().factory,
                physical_cores_override=2,
            )
            self.assertEqual(code, 1)
            self.assertIn("renderSha256", (root / "b.json").read_text())

    def test_legacy_pair_is_not_silently_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            output.mkdir()
            legacy = {
                "scanPage": 1,
                "renderFile": str((renders / "page-0001.png").resolve()),
                "engine": ocr.ENGINE,
                "recognitionLanguages": ocr.VISION_PROFILE["recognitionLanguages"],
                "recognitionLevel": "accurate",
                "usesLanguageCorrection": True,
                "generatedAt": "2026-08-25T00:00:00Z",
                "lines": [
                    {
                        "text": "舊底稿",
                        "confidence": 1,
                        "boundingBox": [0, 0, 1, 1],
                    }
                ],
            }
            (output / "page-0001.json").write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )
            (output / "page-0001.txt").write_text("舊底稿\n", encoding="utf-8")
            harness = Harness()
            report = root / "legacy.json"
            code = ocr.main(
                [str(renders), str(output), "--pages", "1", "--report", str(report)],
                compiler=harness.compiler,
                worker_factory=harness.factory,
                physical_cores_override=2,
            )
            self.assertEqual(code, 1)
            self.assertEqual(harness.calls, [])
            self.assertIn("legacy/unknown schema", report.read_text())

    def test_dangling_output_link_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            output.mkdir()
            dangling = output / "page-0001.json"
            os.symlink(str(root / "missing-completion.json"), dangling)

            blocked = Harness()
            blocked_report = root / "blocked.json"
            blocked_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1",
                    "--report",
                    str(blocked_report),
                ],
                compiler=blocked.compiler,
                worker_factory=blocked.factory,
                physical_cores_override=2,
            )
            self.assertEqual(blocked_code, 1)
            self.assertEqual(blocked.calls, [])
            self.assertTrue(dangling.is_symlink())
            self.assertIn("--overwrite", blocked_report.read_text())

            replacement = Harness()
            replaced_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1",
                    "--overwrite",
                    "--report",
                    str(root / "replaced.json"),
                ],
                compiler=replacement.compiler,
                worker_factory=replacement.factory,
                physical_cores_override=2,
            )
            self.assertEqual(replaced_code, 0)
            self.assertFalse(dangling.is_symlink())
            self.assertEqual(replacement.calls, [1])

    def test_compiler_failure_is_reported_without_claiming_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1, 2])
            report = root / "compile-failure.json"

            def failed_compiler(_swift_source, _build_directory):
                raise ocr.ConfigurationError("synthetic compiler failure")

            code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1-2",
                    "--report",
                    str(report),
                ],
                compiler=failed_compiler,
                worker_factory=Harness().factory,
                physical_cores_override=2,
            )
            self.assertEqual(code, 1)
            run = json.loads(report.read_text())
            self.assertEqual(run["pageSets"]["scheduled"], [1, 2])
            self.assertEqual(run["pageSets"]["attempted"], [])
            self.assertEqual(run["pageSets"]["failed"], [1, 2])
            self.assertFalse((output / "page-0001.json").exists())

    def test_partial_failure_continues_and_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1, 2, 3])
            harness = Harness(failed_pages={2})
            report = root / "partial.json"
            code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1-3",
                    "--workers",
                    "1",
                    "--report",
                    str(report),
                ],
                compiler=harness.compiler,
                worker_factory=harness.factory,
                physical_cores_override=8,
            )
            self.assertEqual(code, 1)
            self.assertEqual(harness.calls, [1, 2, 3])
            self.assertTrue((output / "page-0001.json").exists())
            self.assertFalse((output / "page-0002.json").exists())
            self.assertTrue((output / "page-0003.json").exists())
            result = json.loads(report.read_text())
            self.assertEqual(result["status"], "partial-failure")
            self.assertEqual(result["pageSets"]["completed"], [1, 3])
            self.assertEqual(result["pageSets"]["failed"], [2])

            resumed = Harness()
            resume_report = root / "resume.json"
            resume_code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1-3",
                    "--workers",
                    "auto",
                    "--report",
                    str(resume_report),
                ],
                compiler=resumed.compiler,
                worker_factory=resumed.factory,
                physical_cores_override=8,
            )
            self.assertEqual(resume_code, 0)
            self.assertEqual(resumed.calls, [2])
            resumed_report = json.loads(resume_report.read_text())
            self.assertEqual(resumed_report["pageSets"]["skippedCurrent"], [1, 3])
            self.assertEqual(resumed_report["pageSets"]["completed"], [2])

    def test_atomic_promotion_orders_text_before_completion_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            harness = Harness()
            destinations: list[str] = []
            real_replace = ocr.os.replace

            def recording_replace(source, destination):
                destination_path = Path(destination)
                if destination_path.parent.resolve() == output.resolve():
                    destinations.append(destination_path.suffix)
                return real_replace(source, destination)

            with mock.patch.object(ocr.os, "replace", side_effect=recording_replace):
                code = ocr.main(
                    [str(renders), str(output), "--pages", "1", "--report", str(root / "run.json")],
                    compiler=harness.compiler,
                    worker_factory=harness.factory,
                    physical_cores_override=1,
                )
            self.assertEqual(code, 0)
            self.assertEqual(destinations, [".txt", ".json"])

    def test_report_inside_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, [1])
            code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1",
                    "--report",
                    str(output / "run.json"),
                ],
                compiler=Harness().compiler,
                worker_factory=Harness().factory,
                physical_cores_override=2,
            )
            self.assertEqual(code, 2)

    def test_explicit_pool_never_exceeds_eight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            renders, output = self.make_layout(root, list(range(1, 13)))
            harness = Harness(delay=0.005)
            report = root / "run.json"
            code = ocr.main(
                [
                    str(renders),
                    str(output),
                    "--pages",
                    "1-12",
                    "--workers",
                    "8",
                    "--report",
                    str(report),
                ],
                compiler=harness.compiler,
                worker_factory=harness.factory,
                physical_cores_override=12,
            )
            self.assertEqual(code, 0)
            run = json.loads(report.read_text())
            self.assertEqual(run["workers"]["resolved"], 8)
            self.assertEqual(len(run["workers"]["pageSets"]), 8)
            self.assertLessEqual(len(set(harness.worker_ids)), 8)


if __name__ == "__main__":
    unittest.main()
