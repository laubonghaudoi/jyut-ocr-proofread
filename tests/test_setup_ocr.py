from __future__ import annotations

from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _setup_ocr as setup

ROOT = Path(__file__).resolve().parents[1]


class DownloadServer:
    def __init__(self, content, *, ignore_range=False, bad_range=False, truncate=False):
        self.content = content
        self.requests = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                range_header = self.headers.get("Range")
                outer.requests.append(range_header)
                offset = int(range_header.split("=")[1].split("-")[0]) if range_header else 0
                if ignore_range:
                    offset = 0
                payload = content[offset:]
                self.send_response(206 if range_header and not ignore_range else 200)
                if range_header and not ignore_range:
                    self.send_header("Content-Range", f"bytes {offset + int(bad_range)}-{len(content)-1}/{len(content)}")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:len(payload)//2] if truncate else payload)
                self.wfile.flush()
                if truncate:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/model"
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.options = setup.SetupOptions(ROOT, self.base / "config", self.base / "data", engine="hunyuan", device="cpu")
    def tearDown(self):
        self.temp.cleanup()
    def test_literal_config_never_executes_and_unknown_lines_survive(self):
        config = self.base / "env.sh"
        original = '# user notes\nexport PRIVATE_THING="$(do-not-execute)"\nexport HUNYUAN_MODEL="$HOME/models/a b.gguf"\nexport OCR_HUNYUAN_SLOTS=16\n'
        config.write_text(original)
        values = setup.read_environment(config, home=self.base)
        self.assertEqual(values["HUNYUAN_MODEL"], str(self.base / "models/a b.gguf"))
        setup.write_environment(config, {"OCR_HUNYUAN_DEVICE": "cpu", "OCR_HUNYUAN_SLOTS": values["OCR_HUNYUAN_SLOTS"]})
        self.assertIn('export PRIVATE_THING="$(do-not-execute)"', config.read_text())
        self.assertEqual(values["OCR_HUNYUAN_SLOTS"], "16")
        self.assertEqual(next((self.base / "env-backups").iterdir()).read_text(), original)
        count = len(list((self.base / "env-backups").iterdir()))
        setup.write_environment(config, {"OCR_HUNYUAN_DEVICE": "cpu", "OCR_HUNYUAN_SLOTS": "16"})
        self.assertEqual(len(list((self.base / "env-backups").iterdir())), count)
    def test_dynamic_known_config_is_rejected_without_execution(self):
        config = self.base / "env.sh"
        for value in ('"$(touch /tmp/not-executed)"', '`touch /tmp/not-executed`', '$OTHER/path'):
            config.write_text("export HUNYUAN_MODEL=" + value + "\n")
            with self.assertRaises(setup.SetupError):
                setup.read_environment(config)
    def test_noninteractive_mac_requires_choice_but_saved_choice_resumes(self):
        with self.assertRaisesRegex(setup.SetupError, "requires --engine"):
            setup.choose_engine("auto", {}, system="Darwin", interactive=False)
        self.assertEqual(setup.choose_engine("auto", {"JYUT_OCR_ENGINE": "vision"}, system="Darwin", interactive=False), "vision")
        self.assertEqual(setup.choose_engine("auto", {}, system="Linux", interactive=False), "hunyuan")
        with self.assertRaises(setup.SetupError):
            setup.choose_engine("vision", {}, system="Linux", interactive=False)
    def test_device_auto_falls_back_without_installing_driver(self):
        with patch.object(setup, "cuda_available", return_value=False), patch.object(setup, "metal_available", return_value=False):
            self.assertEqual(setup.choose_device("auto", {}, engine="hunyuan", system="Linux"), "cpu")
            self.assertEqual(setup.choose_device("auto", {}, engine="hunyuan", system="Darwin"), "cpu")
            with self.assertRaisesRegex(setup.SetupError, "never installs"):
                setup.choose_device("cuda", {}, engine="hunyuan", system="Linux")
    def test_existing_device_and_slots_are_preserved(self):
        with patch.object(setup, "cuda_available", return_value=True):
            self.assertEqual(setup.choose_device("auto", {"OCR_HUNYUAN_DEVICE": "cpu"}, engine="hunyuan", system="Linux"), "cpu")
    def test_dependency_instructions_do_not_execute_without_opt_in(self):
        with patch.object(setup, "missing_dependencies", return_value=["pdftoppm"]), patch.object(setup, "package_commands", return_value=[["sudo", "apt-get", "install", "poppler-utils"]]), patch.object(setup, "run") as runner:
            with self.assertRaisesRegex(setup.SetupError, "poppler-utils"):
                setup.ensure_dependencies("hunyuan", self.options, "Linux")
            runner.assert_not_called()
    def test_missing_mac_ui_prerequisite_remains_actionable(self):
        with patch.object(setup, "missing_dependencies", return_value=["Apple Command Line Tools"]), patch.object(setup, "package_commands", return_value=[["xcode-select", "--install"]]), patch.object(setup, "run") as runner:
            with self.assertRaisesRegex(setup.SetupError, "system dialog"):
                setup.ensure_dependencies("vision", replace(self.options, install_system_deps=True), "Darwin")
            runner.assert_not_called()
    def test_opted_in_package_install_rechecks_dependencies(self):
        with patch.object(setup, "missing_dependencies", side_effect=[["git"], []]), patch.object(setup, "package_commands", return_value=[["sudo", "apt-get", "install", "git"]]), patch.object(setup.os, "geteuid", return_value=1000), patch.object(setup, "run") as runner:
            setup.ensure_dependencies("hunyuan", replace(self.options, install_system_deps=True), "Linux")
            runner.assert_called_once_with(["sudo", "-n", "apt-get", "install", "git"])
    def test_check_is_read_only_when_not_installed(self):
        with patch.object(setup, "platform_name", return_value="Linux"), patch.object(setup, "missing_dependencies", return_value=[]):
            result = setup.inspect_setup(self.options)
        self.assertEqual(result["status"], "action-required")
        self.assertEqual(list(self.base.iterdir()), [])
    def test_ready_requires_current_identity_and_smoke(self):
        self.options.config_dir.mkdir()
        (self.options.config_dir / "env.sh").write_text("export JYUT_OCR_ENGINE=hunyuan\nexport OCR_HUNYUAN_DEVICE=cpu\n")
        identity = {"configuration": 1}
        state = {"schema_version": setup.STATE_SCHEMA, "status": "ready", "fingerprint": setup.canonical_hash(identity), "smoke": {"completed_at": "today"}}
        setup.atomic_json(self.options.config_dir / "setup-state.json", state)
        with patch.object(setup, "platform_name", return_value="Linux"), patch.object(setup, "missing_dependencies", return_value=[]), patch.object(setup, "environment_identity", return_value=identity), patch.object(setup, "check_smoke", return_value=True), patch.object(setup, "smoke_test") as smoke:
            self.assertEqual(setup.inspect_setup(self.options)["status"], "ready")
            identity["configuration"] = 2
            self.assertEqual(setup.inspect_setup(self.options)["status"], "action-required")
            smoke.assert_not_called()
    def test_environment_storage_cannot_be_inside_repository(self):
        with self.assertRaisesRegex(setup.SetupError, "outside"):
            setup.setup(replace(self.options, data_dir=ROOT / "scratch-runtime"))
    def test_failed_smoke_cannot_mark_ready(self):
        with patch.object(setup, "platform_name", return_value="Linux"), patch.object(setup, "ensure_dependencies"), patch.object(setup, "ensure_python", return_value=Path("/isolated/python")), patch.object(setup, "ensure_models", return_value={}), patch.object(setup, "ensure_runtime", return_value=(Path("/runtime/llama-server"), {})), patch.object(setup, "environment_identity", return_value={"id": "test"}), patch.object(setup, "smoke_test", side_effect=setup.SetupError("no OCR output")):
            with self.assertRaisesRegex(setup.SetupError, "no OCR"):
                setup.setup(self.options)
        state = json.loads((self.options.config_dir / "setup-state.json").read_text())
        self.assertEqual(state["status"], "action-required")
        self.assertNotIn("smoke", state)
    def test_repeated_setup_reuses_sealed_smoke_and_preserves_slots(self):
        self.options.config_dir.mkdir()
        (self.options.config_dir / "env.sh").write_text("# keep\nexport OCR_HUNYUAN_SLOTS=16\n")
        with patch.object(setup, "platform_name", return_value="Linux"), patch.object(setup, "ensure_dependencies"), patch.object(setup, "ensure_python", return_value=Path("/isolated/python")), patch.object(setup, "ensure_models", return_value={}), patch.object(setup, "ensure_runtime", return_value=(Path("/runtime/llama-server"), {})), patch.object(setup, "environment_identity", return_value={"id": "test"}), patch.object(setup, "smoke_test", return_value={"json_output": "sealed.json"}) as smoke, patch.object(setup, "check_smoke", return_value=True):
            self.assertEqual(setup.setup(self.options)["status"], "ready")
            self.assertEqual(setup.setup(self.options)["smoke"], "already-verified")
            smoke.assert_called_once()
        config = setup.read_environment(self.options.config_dir / "env.sh")
        self.assertEqual(config["OCR_HUNYUAN_SLOTS"], "16")
    def test_gpu_busy_does_not_start_or_stop_services(self):
        result = type("Result", (), {"stdout": "1200\n"})()
        with patch.object(setup, "run", return_value=result) as runner:
            with self.assertRaisesRegex(setup.SetupError, "GPU is busy"):
                setup.smoke_test(self.options, {}, "hunyuan", "cuda", "a" * 64)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args[0][0][0], "nvidia-smi")
        self.assertFalse(self.options.data_dir.exists())
    def test_runtime_cpu_accepts_sealed_cuda_binary_but_not_changed_binary(self):
        binary = self.base / "build/bin/llama-server"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nprintf 'version: 0.3.0-dev (build 1, commit 67a17c1)\\n'\n")
        binary.chmod(0o755)
        lock = setup.load_lock(ROOT)
        setup.atomic_json(self.base / "jyut-ocr-build.json", {"commit": lock["runtime"]["commit"], "binary_sha256": setup.sha256_file(binary), "device": "cuda"})
        self.assertEqual(setup.runtime_identity(binary, lock, "cpu")["compiled_device"], "cuda")
        with self.assertRaises(setup.SetupError):
            setup.runtime_identity(binary, lock, "metal")
        binary.write_text("changed")
        with self.assertRaises(setup.SetupError):
            setup.runtime_identity(binary, lock, "cpu")
    def test_python_and_pillow_versions_are_enforced(self):
        for version_info, pillow in (([3, 9, 20], "12.1.1"), ([3, 10, 0], "9.5.0"), ([3, 14, 0], "13.0.0")):
            value = {"version_info": version_info, "version": "test", "pillow": pillow, "prefix": "/venv", "base_prefix": "/system"}
            result = type("Result", (), {"stdout": json.dumps(value)})()
            with patch.object(setup, "run", return_value=result):
                with self.assertRaises(setup.SetupError):
                    setup.python_identity(Path("/venv/python"))
    def test_invalid_preserved_execution_config_is_never_ready(self):
        for values in ({"OCR_HUNYUAN_SLOTS": "0"}, {"OCR_HUNYUAN_SLOTS": "33"}, {"OCR_HUNYUAN_SLOTS": "n/a"}, {"OCR_HUNYUAN_MODE": "bogus"}):
            with self.assertRaises(setup.SetupError):
                setup.validate_hunyuan_config(values)
        setup.validate_hunyuan_config({"OCR_HUNYUAN_SLOTS": "16", "OCR_HUNYUAN_MODE": "shared"})
    def test_broken_sealed_runtime_cannot_reuse_old_smoke(self):
        binary = self.base / "bin/llama-server"
        binary.parent.mkdir()
        binary.write_text("#!/bin/sh\nexit 127\n")
        binary.chmod(0o755)
        lock = setup.load_lock(ROOT)
        setup.atomic_json(self.base / "jyut-ocr-build.json", {"commit": lock["runtime"]["commit"], "binary_sha256": setup.sha256_file(binary), "device": "cpu"})
        with self.assertRaises(setup.SetupError):
            setup.runtime_identity(binary, lock, "cpu")
    def test_verified_external_models_are_reused_offline_without_download(self):
        content = b"locked model"
        asset = {"role": "model", "name": "model.gguf", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        model = self.base / "existing.gguf"
        model.write_bytes(content)
        lock = {"model": {"repo": "test/test", "revision": "fixed", "files": [asset]}}
        with patch.object(setup, "download_asset") as download:
            result = setup.ensure_models(replace(self.options, offline=True), {"HUNYUAN_MODEL": str(model)}, lock)
        self.assertEqual(result["HUNYUAN_MODEL"], str(model))
        download.assert_not_called()
    def test_cpu_build_is_pinned_and_disables_both_accelerators(self):
        lock = setup.load_lock(ROOT)
        managed = self.options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-cpu")
        binary = managed / "build/bin/llama-server"
        calls = []
        def fake_run(command, **kwargs):
            argv = [str(value) for value in command]
            calls.append(argv)
            stdout = ""
            if argv[:3] == ["git", "rev-parse", "HEAD"]:
                stdout = lock["runtime"]["commit"]
            if argv[:3] == ["git", "remote", "get-url"]:
                stdout = lock["runtime"]["repo"]
            if argv[:2] == ["cmake", "--build"]:
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b"built")
            return type("Result", (), {"stdout": stdout})()
        def identity(candidate, *_):
            if not binary.exists() or candidate != binary:
                raise setup.SetupError("not built")
            return {"verified": True}
        with patch.object(setup, "run", side_effect=fake_run), patch.object(setup, "runtime_identity", side_effect=identity):
            path, verified = setup.ensure_runtime(self.options, {}, lock, "cpu")
        self.assertEqual(path, binary)
        configure = next(command for command in calls if command[:2] == ["cmake", "-S"])
        self.assertIn("-DGGML_CUDA=OFF", configure)
        self.assertIn("-DGGML_METAL=OFF", configure)
        self.assertIn("-DLLAMA_USE_PREBUILT_UI=OFF", configure)
        self.assertIn("-DLLAMA_BUILD_UI=OFF", configure)
        self.assertTrue((managed / "jyut-ocr-build.json").is_file())
        self.assertTrue(verified)
    def test_automatic_accelerator_build_failure_retries_cpu(self):
        with patch.object(setup, "platform_name", return_value="Linux"), patch.object(setup, "cuda_available", return_value=True), patch.object(setup, "ensure_dependencies"), patch.object(setup, "ensure_python", return_value=Path("/isolated/python")), patch.object(setup, "ensure_models", return_value={}), patch.object(setup, "ensure_runtime", side_effect=[setup.SetupError("CUDA compiler rejected"), (Path("/runtime/cpu"), {})]) as runtime, patch.object(setup, "environment_identity", return_value={"id": "test"}), patch.object(setup, "smoke_test", return_value={"json_output": "sealed.json"}):
            result = setup.setup(replace(self.options, device="auto"))
        self.assertEqual(result["device"], "cpu")
        self.assertEqual([call.args[-1] for call in runtime.call_args_list], ["cuda", "cpu"])
    def test_cpu_smoke_ignores_phase_service_commands(self):
        python = "/venv/python"
        calls = []
        def fake_run(command, **kwargs):
            argv = [str(value) for value in command]
            calls.append((argv, kwargs))
            if argv[1] == "-c":
                Path(argv[-1]).write_bytes(b"synthetic raster")
            else:
                output = Path(argv[3])
                output.mkdir()
                render = Path(argv[2]) / "page-0001.png"
                text = output / "page-0001.txt"
                text.write_text("OCR SETUP TEST 12345\n")
                setup.atomic_json(output / "page-0001.json", {"status": "complete", "provenance": {"textSha256": setup.sha256_file(text), "renderSha256": setup.sha256_file(render)}})
                setup.atomic_json(Path(argv[argv.index("--report") + 1]), {"complete": True})
            return type("Result", (), {"stdout": ""})()
        config = {"JYUT_OCR_PYTHON": python, "HUNYUAN_MODEL": "/model", "HUNYUAN_MMPROJ": "/projector", "HUNYUAN_LLAMA_SERVER": "/runtime"}
        with patch.dict(os.environ, {"OCR_PHASE_RELEASE_CMD": "do-not-run", "HUNYUAN_ENDPOINT": "http://do-not-use"}), patch.object(setup, "run", side_effect=fake_run):
            record = setup.smoke_test(self.options, config, "hunyuan", "cpu", "f" * 64)
        self.assertTrue(setup.check_smoke(record))
        self.assertEqual(len(calls), 2)
        environment = calls[1][1]["env"]
        self.assertNotIn("OCR_PHASE_RELEASE_CMD", environment)
        self.assertNotIn("HUNYUAN_ENDPOINT", environment)
        self.assertIn("--device", calls[1][0])
    def test_partial_venv_without_pip_is_backed_up_and_recreated(self):
        venv = self.options.data_dir / "python"
        python = venv / "bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("partial interpreter")
        calls = []
        def fake_run(command, **kwargs):
            argv = [str(value) for value in command]
            calls.append(argv)
            if argv[1:3] == ["-m", "venv"]:
                python.parent.mkdir(parents=True)
                python.write_text("recreated interpreter")
            return type("Result", (), {"stdout": ""})()
        with patch.object(setup, "python_identity", side_effect=[setup.SetupError("missing PIL"), {"valid": True}]), patch.object(setup, "command_ok", side_effect=[True, False]), patch.object(setup, "run", side_effect=fake_run):
            result = setup.ensure_python(self.options, {})
        self.assertEqual(result, python)
        backups = list(self.options.data_dir.glob("python-incomplete-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "bin/python").read_text(), "partial interpreter")
        self.assertEqual(python.read_text(), "recreated interpreter")
        self.assertEqual(calls[0][1:3], ["-m", "venv"])
    def test_linked_runtime_library_changes_invalidate_seal(self):
        binary = self.base / "bin/llama-server"
        binary.parent.mkdir()
        binary.write_text("#!/bin/sh\nprintf 'commit 67a17c1\\n'\n")
        binary.chmod(0o755)
        library = binary.parent / "libllama.so"
        library.write_bytes(b"first library")
        lock = setup.load_lock(ROOT)
        setup.atomic_json(self.base / "jyut-ocr-build.json", {"commit": lock["runtime"]["commit"], "binary_sha256": setup.sha256_file(binary), "device": "cpu", "libraries": setup.runtime_libraries(binary)})
        before = setup.runtime_identity(binary, lock, "cpu")
        self.assertIn("libllama.so", before["libraries"])
        library.write_bytes(b"changed library")
        with self.assertRaises(setup.SetupError):
            setup.runtime_identity(binary, lock, "cpu")
    def test_owned_smoke_timeout_terminates_entire_process_group(self):
        import subprocess
        import signal
        from unittest.mock import Mock
        process = Mock(pid=987654)
        process.communicate.side_effect = [subprocess.TimeoutExpired("runner", 1), (None, None)]
        with patch.object(setup.subprocess, "Popen", return_value=process) as popen, patch.object(setup.os, "killpg") as kill:
            with self.assertRaisesRegex(setup.SetupError, "could not finish"):
                setup.run(["runner"], timeout=1, own_group=True)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual([call.args for call in kill.call_args_list], [(987654, signal.SIGTERM), (987654, signal.SIGKILL)])
    def test_broken_dependency_executable_is_not_treated_as_installed(self):
        with patch.object(setup.shutil, "which", return_value="/broken/cmake"), patch.object(setup, "command_ok", side_effect=lambda command: command[0] != "cmake"):
            self.assertEqual(setup.missing_dependencies("hunyuan", building=True), ["cmake"])
    def test_offline_runtime_resumes_after_fetch_before_checkout(self):
        lock = setup.load_lock(ROOT)
        managed = self.options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-cpu")
        source = managed / "source"
        (source / ".git").mkdir(parents=True)
        binary = managed / "build/bin/llama-server"
        calls = []
        def fake_run(command, **kwargs):
            argv = [str(value) for value in command]
            calls.append(argv)
            if argv[:3] in (["git", "remote", "get-url"], ["git", "rev-parse", "HEAD"]):
                raise setup.SetupError("interrupted before remote/checkout")
            if argv[:2] == ["cmake", "--build"]:
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b"built")
            return type("Result", (), {"stdout": ""})()
        def identity(candidate, *_):
            if not binary.exists() or candidate != binary:
                raise setup.SetupError("not built")
            return {"verified": True}
        with patch.object(setup, "run", side_effect=fake_run), patch.object(setup, "runtime_identity", side_effect=identity):
            setup.ensure_runtime(replace(self.options, offline=True), {}, lock, "cpu")
        self.assertIn(["git", "remote", "add", "origin", lock["runtime"]["repo"]], calls)
        self.assertIn(["git", "checkout", "--detach", lock["runtime"]["commit"]], calls)
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in calls))
    def test_verified_runtime_reuse_does_not_require_build_tools(self):
        lock = setup.load_lock(ROOT)
        binary = self.base / "existing/llama-server"
        with patch.object(setup, "runtime_identity", return_value={"verified": True}), patch.object(setup, "ensure_dependencies") as dependencies:
            path, _ = setup.ensure_runtime(self.options, {"HUNYUAN_LLAMA_SERVER": str(binary)}, lock, "cpu")
        self.assertEqual(path, binary)
        dependencies.assert_not_called()
        with patch.object(setup.shutil, "which", side_effect=lambda name: "/bin/" + name if name in {"pdfinfo", "pdftoppm"} else None), patch.object(setup, "command_ok", return_value=True):
            self.assertEqual(setup.missing_dependencies("hunyuan"), [])
            self.assertEqual(setup.missing_dependencies("hunyuan", building=True), ["git", "cmake", "c++"])
    def test_locked_model_pair_and_runtime_are_exact(self):
        lock = setup.load_lock(ROOT)
        self.assertEqual(lock["runtime"]["commit"], "67a17c17caa95742186f8b1ecadd1b5abd6d5ebb")
        self.assertEqual(lock["model"]["revision"], "8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1")
        self.assertEqual({item["role"] for item in lock["model"]["files"]}, {"model", "mmproj"})


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / "model.gguf"
        self.content = b"source asset exact bytes" * 1024
        self.asset = {"size": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()}
    def tearDown(self):
        self.temp.cleanup()
    def test_first_download_and_offline_reuse(self):
        with DownloadServer(self.content) as server:
            setup.download_asset(server.url, self.target, self.asset)
            self.assertEqual(server.requests, [None])
        self.assertEqual(setup.download_asset("https://not-requested", self.target, self.asset, offline=True), self.target)
    def test_range_resume(self):
        self.target.with_suffix(".gguf.part").write_bytes(self.content[:71])
        with DownloadServer(self.content) as server:
            setup.download_asset(server.url, self.target, self.asset)
            self.assertEqual(server.requests, ["bytes=71-"])
        self.assertEqual(self.target.read_bytes(), self.content)
    def test_ignored_range_restarts_instead_of_appending(self):
        self.target.with_suffix(".gguf.part").write_bytes(self.content[:71])
        with DownloadServer(self.content, ignore_range=True) as server:
            setup.download_asset(server.url, self.target, self.asset)
        self.assertEqual(self.target.read_bytes(), self.content)
    def test_bad_range_never_changes_partial(self):
        partial = self.target.with_suffix(".gguf.part")
        partial.write_bytes(self.content[:71])
        with DownloadServer(self.content, bad_range=True) as server:
            with self.assertRaisesRegex(setup.SetupError, "Content-Range"):
                setup.download_asset(server.url, self.target, self.asset)
        self.assertEqual(partial.read_bytes(), self.content[:71])
        self.assertFalse(self.target.exists())
    def test_interrupted_download_retains_partial_then_resumes(self):
        with DownloadServer(self.content, truncate=True) as server:
            with self.assertRaises(setup.SetupError):
                setup.download_asset(server.url, self.target, self.asset)
        partial = self.target.with_suffix(".gguf.part")
        self.assertGreater(partial.stat().st_size, 0)
        self.assertLess(partial.stat().st_size, len(self.content))
        with DownloadServer(self.content) as server:
            setup.download_asset(server.url, self.target, self.asset)
            self.assertTrue(server.requests[0].startswith("bytes="))
    def test_hash_mismatch_never_promotes_and_retry_can_succeed(self):
        with DownloadServer(b"x" * len(self.content)) as server:
            with self.assertRaisesRegex(setup.SetupError, "SHA-256"):
                setup.download_asset(server.url, self.target, self.asset)
        self.assertFalse(self.target.exists())
        self.assertEqual(len(list(self.target.parent.glob("*.invalid-*"))), 1)
        with DownloadServer(self.content) as server:
            setup.download_asset(server.url, self.target, self.asset)
        self.assertTrue(setup.verify_asset(self.target, self.asset))
    def test_existing_wrong_model_is_not_deleted_or_overwritten(self):
        self.target.write_bytes(b"user file")
        with self.assertRaisesRegex(setup.SetupError, "left untouched"):
            setup.download_asset("https://not-requested", self.target, self.asset)
        self.assertEqual(self.target.read_bytes(), b"user file")
    def test_offline_without_verified_asset_does_not_start_network(self):
        with patch.object(setup.urllib.request, "urlopen") as opener:
            with self.assertRaisesRegex(setup.SetupError, "Offline"):
                setup.download_asset("https://not-requested", self.target, self.asset, offline=True)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
