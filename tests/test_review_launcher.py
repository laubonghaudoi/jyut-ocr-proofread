"""Exercise the launcher in fresh interpreters without a model or review server."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts/review.py"


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
        for api in ("2", "True", "None"):
            with self.subTest(api=api):
                root = self.tool(f"api-{api}", api)
                (root / "jyut_ocr_review/server.py").write_text("raise RuntimeError('must not import')\n")
                result = self.run_launcher("--tool-root", str(root))
                self.assertEqual(result.returncode, 2)
                self.assertIn("Unsupported review API", result.stderr)
                self.assertNotIn("must not import", result.stderr)

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


if __name__ == "__main__":
    unittest.main()
