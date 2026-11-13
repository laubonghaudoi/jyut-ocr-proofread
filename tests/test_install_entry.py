from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install

ROOT = Path(__file__).resolve().parents[1]


class InstallEntryTest(unittest.TestCase):
    def test_shell_entry_works_from_another_working_directory_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix="portable skill ") as directory:
            link = Path(directory) / "skill"
            link.symlink_to(ROOT, target_is_directory=True)
            result = subprocess.run([str(link / "install.sh"), "--help"], cwd=directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--engine", result.stdout)
        self.assertIn("--skill-dir", result.stdout)
    def run_fake_bootstrap(self, *, complete="python3.12", override=None):
        with tempfile.TemporaryDirectory(prefix="bootstrap paths ") as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            for name in ("python3", "python3.14", "python3.13", "python3.12", "python3.11", "python3.10"):
                interpreter = fake_bin / name
                capability_exit = "0" if name == complete else "1"
                interpreter.write_text(
                    "#!/bin/sh\n"
                    'if [ "$1" = "-c" ]; then\n'
                    '  case "$2" in *ensurepip*) exit ' + capability_exit + ' ;; esac\n'
                    '  exit 0\nfi\n'
                    "printf '%s\\n' 'SELECTED " + name + "'\n"
                )
                interpreter.chmod(0o755)
            environment = dict(os.environ)
            environment.pop("JYUT_OCR_BOOTSTRAP_PYTHON", None)
            environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "/usr/bin:/bin")
            if override is not None:
                environment["JYUT_OCR_BOOTSTRAP_PYTHON"] = override
            return subprocess.run([str(ROOT / "install.sh"), "--help"], env=environment,
                                  text=True, capture_output=True)
    def test_bootstrap_prefers_venv_and_ensurepip_capable_interpreter(self):
        result = self.run_fake_bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SELECTED python3.12")
    def test_bootstrap_explicit_override_is_respected_without_ensurepip(self):
        result = self.run_fake_bootstrap(override="python3.14")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SELECTED python3.14")
    def test_bootstrap_without_complete_candidate_uses_first_compatible(self):
        result = self.run_fake_bootstrap(complete=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SELECTED python3")
    def test_bootstrap_missing_explicit_override_does_not_silently_switch(self):
        result = self.run_fake_bootstrap(override="/not-an-existing-python-executable")
        self.assertEqual(result.returncode, 2)
        self.assertIn("explicit override was not replaced", result.stderr)
        self.assertNotIn("SELECTED", result.stdout)
    def test_skill_only_install_and_check_custom_collection(self):
        with tempfile.TemporaryDirectory(prefix="custom install ") as directory:
            parent = Path(directory)
            command = ["--skill-only", "--skill-dir", str(parent / "skills"), "--backup-root", str(parent / "backups")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install.main(command), 0)
                self.assertEqual(install.main([*command, "--check"]), 0)
                self.assertEqual(install.main(command), 0)
            target = parent / "skills/jyut-ocr-proofread"
            self.assertEqual(target.resolve(), ROOT)
            self.assertTrue((target / "SKILL.md").is_file())
            self.assertTrue((target / "scripts/run_hunyuanocr.py").is_file())
    def test_env_only_does_not_install_agent_targets(self):
        with patch.object(install, "setup", return_value={"status": "ready"}) as setup_call, patch.object(install, "perform_install") as mutation, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--env-only", "--engine", "hunyuan", "--device", "cpu"]), 0)
        mutation.assert_not_called()
        self.assertEqual(setup_call.call_args[0][0].device, "cpu")
    def test_check_uses_inspection_not_initialization(self):
        with patch.object(install, "inspect_setup", return_value={"status": "action-required", "reason": "missing"}) as inspect_call, patch.object(install, "setup") as setup_call, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--check", "--env-only"]), 2)
        inspect_call.assert_called_once()
        setup_call.assert_not_called()
    def test_default_agent_is_generic_and_custom_adapters_opt_in(self):
        with patch.object(install, "build_portable_specs", return_value=[]) as build, patch.object(install, "perform_install"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--skill-only"]), 0)
        self.assertEqual(build.call_args.kwargs["agents"], ["generic"])
        self.assertFalse(build.call_args.kwargs["with_agents"])
    def test_repeat_agent_and_overrides_reach_components(self):
        with patch.object(install, "build_portable_specs", return_value=[]) as build, patch.object(install, "perform_install"), patch.object(install, "setup", return_value={"status": "ready"}) as setup_call, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--agent", "claude", "--agent", "gemini", "--with-agents", "--config-dir", "/tmp/test-jyut-config", "--data-dir", "/tmp/test-jyut-data"]), 0)
        self.assertEqual(build.call_args.kwargs["agents"], ["claude", "gemini"])
        self.assertTrue(build.call_args.kwargs["with_agents"])
        self.assertEqual(setup_call.call_args[0][0].config_dir, Path("/tmp/test-jyut-config"))
    def test_setup_failure_returns_actionable_exit_without_claiming_ready(self):
        with patch.object(install, "setup", side_effect=install.SetupError("missing dependency")), contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(install.main(["--env-only"]), 2)
        self.assertIn("ACTION REQUIRED", stderr.getvalue())
        self.assertNotIn('"status": "ready"', stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
