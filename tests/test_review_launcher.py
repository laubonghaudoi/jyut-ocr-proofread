"""Exercise the launcher in fresh interpreters without a model or review server."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "scripts/review.py"


class ReviewLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = self.root / "consumer/scripts/review.py"
        self.launcher.parent.mkdir(parents=True)
        shutil.copyfile(LAUNCHER, self.launcher)
        self.env = dict(os.environ)
        self.env.pop("JYUT_OCR_BENCH_ROOT", None)
        self.env.pop("PYTHONPATH", None)

    def tool(self, name="jyut-ocr-bench", api="1"):
        root = self.root / name
        package = root / "jyut_ocr_review"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"REVIEW_API_VERSION = {api}\n", encoding="utf-8")
        (package / "server.py").write_text(
            "import json, os\n"
            "def main(argv=None):\n"
            "    print(json.dumps({'args': argv, 'cwd': os.getcwd(), 'origin': __file__}))\n"
            "    return 7\n", encoding="utf-8")
        return root

    def run_launcher(self, *args, path=None):
        return subprocess.run([sys.executable, str(path or self.launcher), *args],
                              cwd=self.root, env=self.env, text=True, capture_output=True)

    def test_missing_root_reports_configuration_error(self):
        result = self.run_launcher("--project-root", "book")
        self.assertEqual(result.returncode, 2)
        self.assertIn("JYUT_OCR_BENCH_ROOT", result.stderr)

    def test_help_works_without_the_checkout(self):
        # The launcher's own usage, exit 0, where the tool cannot be loaded
        # (not found, or the wrong API); other arguments still exit 2.
        bad = self.tool("api-3", "3")
        for args in (["--help"], ["-h"], ["--tool-root", str(bad), "--help"]):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith("usage: review.py [--tool-root PATH]"), result.stdout)
                self.assertIn("the tool's own options are not shown", result.stderr)
        self.assertEqual(self.run_launcher("--tool-root", str(bad), "serve").returncode, 2)

    def test_explicit_root_overrides_environment_and_preserves_arguments_and_cwd(self):
        root = self.tool("selected tool")
        self.env["JYUT_OCR_BENCH_ROOT"] = str(self.root / "wrong")
        args = ["--project-root", "書 本", "--state-dir", "my review", "--port", "8765"]
        result = self.run_launcher("--tool-root", str(root), *args)
        self.assertEqual(result.returncode, 7, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["args"], args)
        self.assertEqual(output["cwd"], str(self.root))
        self.assertEqual(Path(output["origin"]), root / "jyut_ocr_review/server.py")

    def test_invalid_explicit_root_never_falls_back(self):
        self.tool()
        result = self.run_launcher("--tool-root", str(self.root / "missing"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid shared review checkout", result.stderr)

    def test_invalid_environment_never_falls_back(self):
        self.tool()
        for value in (str(self.root / "missing"), ""):
            with self.subTest(value=value):
                self.env["JYUT_OCR_BENCH_ROOT"] = value
                result = self.run_launcher()
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)

    def test_sibling_checkout_resolves_from_source(self):
        root = self.tool()
        result = self.run_launcher("--dataset-root", "data")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["origin"]), root / "jyut_ocr_review/server.py")

    def test_symlink_install_uses_source_sibling(self):
        root = self.tool()
        installed = self.root / "elsewhere/skill/scripts/review.py"
        installed.parent.mkdir(parents=True)
        installed.symlink_to(self.launcher)
        result = self.run_launcher("--audit-root", "audit", path=installed)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["origin"]), root / "jyut_ocr_review/server.py")

    def test_nested_copied_skill_uses_explicit_environment(self):
        root = self.tool("configured")
        installed = self.root / ".agents/skills/skill/scripts/review.py"
        installed.parent.mkdir(parents=True)
        shutil.copyfile(self.launcher, installed)
        self.env["JYUT_OCR_BENCH_ROOT"] = str(root)
        result = self.run_launcher("validate-project", "--project-root", "book", path=installed)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout)["args"], ["validate-project", "--project-root", "book"])

    def test_subcommands_are_forwarded_without_reinterpreting_options(self):
        self.tool()
        for args in (["import-goldgate", "--help"], ["--help"], ["--tool", "unrecognized"]):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(json.loads(result.stdout)["args"], args)

    def test_wrong_api_rejected_before_server_import(self):
        # A newer API needs a newer launcher; an older or broken one a newer bench.
        for api, fix in (("3", "Update this skill"), ("0", "Update jyut-ocr-bench"),
                         ("True", "Update jyut-ocr-bench"), ("None", "Update jyut-ocr-bench")):
            with self.subTest(api=api):
                root = self.tool(f"api-{api}", api)
                (root / "jyut_ocr_review/server.py").write_text("raise RuntimeError('must not import')\n")
                result = self.run_launcher("--tool-root", str(root))
                self.assertEqual(result.returncode, 2)
                self.assertIn("Unsupported review API", result.stderr)
                self.assertIn(fix, result.stderr)
                self.assertNotIn("must not import", result.stderr)

    def test_api_2_starts_and_its_retired_api_1_options_are_named(self):
        # jyut-ocr-bench's review API 2 (sample-catalogue answers only): the
        # catalogue route is forwarded unchanged; API 1's project queue, audit
        # and Goldgate options stop in the launcher with the reason.
        root = self.tool("bench-v2", "2")
        for args in (["--port", "8765"], ["--port", "0", "--state-dir", "second review"], ["--api-version"], []):
            with self.subTest(args=args):
                result = self.run_launcher("--tool-root", str(root), *args)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(json.loads(result.stdout)["args"], args)
        for args, named in ((["--project-root", "book"], "--project-root"), (["--audit-root=audit"], "--audit-root"),
                            (["--root", "audit", "--port", "8765"], "--root"),
                            (["validate-project", "--project-root", "book"], "validate-project, --project-root"),
                            (["import-goldgate", "--help"], "import-goldgate")):
            with self.subTest(args=args):
                result = self.run_launcher("--tool-root", str(root), *args)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)
                self.assertIn(named, result.stderr)
                self.assertIn("review API 2", result.stderr)
                self.assertIn("sample-catalogue/v1.json", result.stderr)
        # The same options still reach an API 1 checkout.
        old = self.tool("bench-v1", "1")
        result = self.run_launcher("--tool-root", str(old), "--project-root", "book")
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_pythonpath_cannot_select_another_server(self):
        selected = self.tool()
        shadow = self.tool("shadow")
        self.env["PYTHONPATH"] = str(shadow)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["origin"]), selected / "jyut_ocr_review/server.py")

    def test_preloaded_foreign_package_is_rejected(self):
        selected = self.tool()
        shadow = self.tool("shadow")
        bootstrap = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                     "import jyut_ocr_review; sys.argv = [sys.argv[2], '--tool-root', sys.argv[3]]; "
                     "runpy.run_path(sys.argv[0], run_name='__main__')")
        result = subprocess.run([sys.executable, "-c", bootstrap, str(shadow), str(self.launcher), str(selected)],
                                cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Wrong module origin", result.stderr)

    def test_server_symlink_outside_selected_checkout_is_rejected(self):
        selected = self.tool()
        outside = self.root / "other-server.py"
        outside.write_text("raise RuntimeError('must not import')\n")
        server = selected / "jyut_ocr_review/server.py"
        server.unlink()
        server.symlink_to(outside)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid shared review checkout", result.stderr)


class ReviewDocsTests(unittest.TestCase):
    def test_readme_says_where_each_api_keeps_the_answers(self):
        """README's review paragraph names API 2's answer store; the
        project-local answer history is API 1's only (manual-review-tool.md)."""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        start = readme.index("## 可選人工標註")
        section = readme[start:readme.index("\n## ", start + 1)]
        self.assertIn("API 2", section)
        self.assertIn("`sample-catalogue/v1.json`", section)
        project_lines = [part for part in re.split(r"[。；]", section) if "相應 project" in part]
        self.assertTrue(project_lines)
        for part in project_lines:
            self.assertIn("API 1", part)


if __name__ == "__main__":
    unittest.main()
