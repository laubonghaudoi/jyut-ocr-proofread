from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def test_every_agent_gets_the_background_prepare_route_and_a_foreground_test(self):
        """install.md gives every agent (not only one that is the local model:
        the phase-E review's E-R9) the long download in the background with
        --prepare-only, a marker line that says when it ended, and the setup
        test in the foreground after it; SKILL.md 三 points there.  The
        documented command, run with a stand-in install.sh that ends as
        --prepare-only does (its JSON, status pending-smoke, exit 2), leaves
        that marker last in its log, directly and wrapped in nohup."""
        text = (ROOT / "references" / "install.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        bullet = text[text.index("**下載長過 shell 上限（任何 agent，唔止上面講嘅本機模型）**"):]
        bullet = bullet[:bullet.index("\n\nFingerprint ")]
        for words in ("agent 唔理係咪本機模型都分三步", "`run_in_background`", "`is_background`", "`nohup sh -c",
                      "`[prepare] exit=N`", '`"status": "pending-smoke"`', "`./install.sh --env-only`，**唔准放背景**",
                      "`./install.sh --env-only --check`", "唔好喺背景跑會試跑或者交接嘅命令"):
            self.assertIn(words, bullet)
        self.assertIn("長嘅下載先背景跑 `--prepare-only`，做完再前景跑 `./install.sh --env-only` 試跑",
                      skill.split("\n## 三、", 1)[1].split("\n## ", 1)[0])
        self.assertIn("--prepare-only", {o for a in install.build_parser()._actions for o in a.option_strings})
        command = bullet.split("跑 `", 1)[1].split("`；冇背景 tool", 1)[0]
        self.assertTrue(command.startswith("./install.sh --env-only --prepare-only > "), command)
        with tempfile.TemporaryDirectory(prefix="prepare route ") as directory:
            folder = Path(directory)
            stand_in = folder / "install.sh"
            stand_in.write_text("#!/bin/sh\nprintf '{\\n  \"status\": \"pending-smoke\"\\n}\\n'\nexit 2\n")
            stand_in.chmod(0o755)
            environment = dict(os.environ, TMPDIR=directory)
            log = folder / "jyut-ocr-prepare.log"
            subprocess.run(["sh", "-c", command], cwd=folder, env=environment, check=True, timeout=30)
            self.assertEqual(log.read_text().splitlines()[-1], "[prepare] exit=2")
            self.assertIn('"status": "pending-smoke"', log.read_text())
            log.unlink()
            wrapped = "nohup sh -c '" + command + "' >/dev/null 2>&1 &"
            subprocess.run(["sh", "-c", wrapped], cwd=folder, env=environment, check=True, timeout=30)
            for _ in range(200):
                if log.is_file() and log.read_text().endswith("[prepare] exit=2\n"):
                    break
                time.sleep(0.05)
            self.assertEqual(log.read_text().splitlines()[-1], "[prepare] exit=2")

    def test_rule_set_b_takes_the_server_concurrency_from_env_sh(self):
        """Rule set B's book profile and phase 3 ask the local model over
        HTTP as many requests at once as its server runs.  Nothing gave that
        number (the phase-E review's E-R10): the installer's
        --local-model-concurrency N writes it to env.sh as
        PROOFREAD_MAX_INFLIGHT, which proofread_pages.py --max-inflight (and
        book_profile.py --workers over HTTP) take as their default.  So the
        run sheet's steps 3 and 4 add --workers N / --max-inflight N only
        without the key, and 一 B has the agent ask the person for N before
        the run rather than guess it."""
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        text = (ROOT / "references" / "install.md").read_text(encoding="utf-8")
        sets = skill.split("\n## 一、", 1)[1].split("\n## ", 1)[0]
        run = skill.split("\n## 四、", 1)[1].split("\n## ", 1)[0]
        steps = {line[:2]: line for line in run.splitlines() if line[:2] in ("3.", "4.")}
        self.assertIn("B 套加 `--backend http`，冇 `PROOFREAD_MAX_INFLIGHT` 先再加 `--workers N`", steps["3."])
        self.assertIn("B 套加 `--backend http`（冇 `PROOFREAD_MAX_INFLIGHT` 先再加 `--max-inflight N`）", steps["4."])
        self.assertIn("冇 `PROOFREAD_MAX_INFLIGHT`（server 一次跑幾多個請求）就開工前問用戶，唔好估", sets)
        self.assertNotIn("N 係 server 嘅並行數", skill)
        self.assertIn("| `--local-model-concurrency` |", text)
        self.assertIn("--local-model-concurrency 4", text)
        self.assertIn("安裝器寫入 `PROOFREAD_MAX_INFLIGHT`", text)
        self.assertIn("--local-model-concurrency N writes it",
                      (ROOT / "env.sh.example").read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "scripts"))
        import proofread_pages
        with patch.dict(os.environ, {"PROOFREAD_MAX_INFLIGHT": "3"}):
            arguments = proofread_pages.build_parser().parse_args(
                ["r", "d", "o", "--backend", "http", "--endpoint", "http://127.0.0.1:9/v1/chat/completions"])
        self.assertEqual(arguments.max_inflight, 3)


if __name__ == "__main__":
    unittest.main()
