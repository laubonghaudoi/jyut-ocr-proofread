"""Managed MLX installation and unified-memory policies, without models or services."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _setup_ocr as setup
import install
import run_ocr_phase as phase

ROOT = Path(__file__).resolve().parents[1]


class MlxSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.options = setup.SetupOptions(ROOT, self.base / "config", self.base / "data", engine="auto")
        self.lock = copy.deepcopy(setup.load_lock(ROOT))
        self.lock["mlx"]["enabled_by_default"] = True
        mac = patch.object(setup.platform, "mac_ver", return_value=("14.0", (), "arm64"))
        mac.start()
        self.addCleanup(mac.stop)

    def probe(self, **updates):
        value = {"python": [3, 12, 16], "prefix": "/mlx", "base_prefix": "/base",
                 "dists": {item["name"]: item["version"] for item in self.lock["mlx"]["packages"]}}
        value.update(updates)
        return value

    def choose(self, request="auto", device="auto", system="Darwin", machine="arm64"):
        return setup.choose_paddle_backend(request, device, self.lock, system=system, machine=machine)

    def test_backend_defaults_preserve_explicit_requests_and_platforms(self):
        self.assertEqual(self.choose(), "mlx-vlm-server")
        self.assertEqual(self.choose(device="cpu"), "native")
        self.assertEqual(self.choose(request="native"), "native")
        self.assertEqual(self.choose(system="Linux", machine="x86_64"), "native")
        self.assertEqual(self.choose(machine="x86_64"), "native")
        self.lock["mlx"]["enabled_by_default"] = False
        self.assertEqual(self.choose(), "native")
        self.assertEqual(self.choose(request="mlx-vlm-server"), "mlx-vlm-server")
        with self.assertRaisesRegex(setup.SetupError, "conflicts"):
            self.choose(request="mlx-vlm-server", device="cpu")
        with self.assertRaisesRegex(setup.SetupError, "Apple Silicon"):
            self.choose(request="mlx-vlm-server", system="Linux")

    def test_older_macos_keeps_native_and_rejects_explicit_mlx(self):
        with patch.object(setup.platform, "mac_ver", return_value=("13.6", (), "arm64")):
            self.assertEqual(self.choose(), "native")
            with self.assertRaisesRegex(setup.SetupError, "macOS 14"):
                self.choose(request="mlx-vlm-server")

    def test_an_auto_computed_cpu_device_can_upgrade_but_a_cpu_request_cannot(self):
        existing = {"JYUT_OCR_DEVICE": "auto", "OCR_PADDLE_DEVICE": "cpu", "OCR_PADDLE_VL_BACKEND": "native"}
        with patch.object(setup, "metal_available", return_value=True):
            plan = setup.plan_engines(self.options, existing, self.lock, system="Darwin", machine="arm64")[2]
            self.assertEqual((plan.backend, plan.device), ("mlx-vlm-server", "cpu"))
            existing["JYUT_OCR_DEVICE"] = "cpu"
            self.assertEqual(setup.plan_engines(self.options, existing, self.lock,
                                               system="Darwin", machine="arm64")[2].backend, "native")
        self.assertEqual(setup.paddle_backend_request("default", {"JYUT_OCR_PADDLE_VL_BACKEND": "native"}), "native")
        self.assertEqual(setup.paddle_backend_request("auto", {"JYUT_OCR_PADDLE_VL_BACKEND": "native"}), "auto")

    def test_vision_hunyuan_omits_paddle_installation(self):
        options = replace(self.options, engine="vision,hunyuan", device="metal")
        with patch.object(setup, "metal_available", return_value=True):
            engines, devices, paddle, *_ = setup.plan_engines(options, {}, self.lock, system="Darwin", machine="arm64")
        self.assertEqual(engines, ("vision", "hunyuan"))
        self.assertEqual(devices, {"vision": "vision-managed", "hunyuan": "metal"})
        self.assertIsNone(paddle)
        with self.assertRaises(setup.SetupError):
            setup.choose_engines("vision,hunyuan", system="Linux", machine="x86_64")

    def test_public_installer_forwards_backend_and_engine_request(self):
        with patch.object(install, "setup", return_value={"status": "ready"}) as run:
            self.assertEqual(install.main(["--env-only", "--engine", "vision,hunyuan",
                                           "--paddle-vl-backend", "native"]), 0)
        self.assertEqual(run.call_args.args[0].paddle_vl_backend, "native")
        self.assertEqual(run.call_args.args[0].engine, "vision,hunyuan")

    def test_metadata_probe_does_not_import_mlx(self):
        result = type("Result", (), {"stdout": json.dumps(self.probe())})()
        with patch.object(setup, "run", return_value=result) as run:
            self.assertIsNone(setup.mlx_mismatch(setup.mlx_probe(Path("/mlx/python"), self.lock), self.lock))
        code = run.call_args.args[0][2]
        self.assertIn("importlib.metadata", code)
        self.assertNotIn("import mlx", code)
        changed = self.probe()
        changed["dists"]["mlx-vlm"] = "0.8.1"
        self.assertIn("mlx-vlm==0.8.0", setup.mlx_mismatch(changed, self.lock))
        self.assertIsNotNone(setup.mlx_mismatch(self.probe(python=[3, 13, 1]), self.lock))
        self.assertIsNotNone(setup.mlx_mismatch(self.probe(prefix="/base"), self.lock))

    def test_verified_external_mlx_environment_is_reused_without_installing(self):
        python = self.base / "external/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        with patch.object(setup, "mlx_probe", return_value=self.probe()), patch.object(setup, "run") as run:
            selected, _ = setup.ensure_mlx_python(replace(self.options, offline=True), {"MLX_VLM_PYTHON": str(python)}, self.lock)
        self.assertEqual(selected, python)
        run.assert_not_called()

    def test_mlx_cannot_reuse_the_configured_paddle_or_core_interpreter(self):
        python = self.base / "shared/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        for key in ("PADDLEOCR_PYTHON", "JYUT_OCR_PYTHON"):
            with self.subTest(key=key), patch.object(setup, "mlx_probe", return_value=self.probe()):
                self.assertIsNone(setup.reusable_mlx_python(self.options, {key: str(python), "MLX_VLM_PYTHON": str(python)}, self.lock))

    def test_mlx_install_uses_hashes_in_both_installers_and_honors_offline(self):
        options = replace(self.options, offline=True)
        for uv in (None, Path("/uv")):
            with self.subTest(uv=uv), patch.object(setup, "run") as run:
                setup.install_requirements(options, Path("/mlx/python"), [], uv=uv,
                    requirement_file=ROOT / self.lock["mlx"]["requirements"], require_hashes=True)
            argv = [str(item) for item in run.call_args.args[0]]
            self.assertIn("--require-hashes", argv)
            self.assertIn("--offline" if uv else "--no-index", argv)
            self.assertIn(str(ROOT / self.lock["mlx"]["requirements"]), argv)

    def test_a_mismatched_external_environment_is_not_modified(self):
        external = self.base / "external/bin/python"
        external.parent.mkdir(parents=True)
        external.touch()
        wrong = self.probe(dists={})
        with patch.object(setup, "mlx_probe", side_effect=[wrong, self.probe()]), \
                patch.object(setup, "find_uv", return_value=Path("/uv")), \
                patch.object(setup, "paddle_venv_python", return_value=Path("/base/python")), \
                patch.object(setup, "install_requirements") as install_packages, patch.object(setup, "run") as run:
            selected, _ = setup.ensure_mlx_python(self.options, {"MLX_VLM_PYTHON": str(external)}, self.lock)
        self.assertEqual(selected, self.options.data_dir / "mlx-python/bin/python")
        self.assertEqual(install_packages.call_args.args[1], selected)
        self.assertTrue(install_packages.call_args.kwargs["require_hashes"])
        self.assertEqual(run.call_args.args[0][-1], selected.parent.parent)
        self.assertEqual(external.read_bytes(), b"")

    def test_prepare_only_records_both_requested_and_effective_backend(self):
        options = replace(self.options, prepare_only=True)
        with patch.object(setup, "platform_name", return_value="Darwin"), patch.object(setup, "machine_name", return_value="arm64"), \
                patch.object(setup, "metal_available", return_value=True), patch.object(setup, "load_lock", return_value=self.lock), \
                patch.object(setup, "ensure_dependencies"), patch.object(setup, "disk_needs", return_value=[]), \
                patch.object(setup, "ensure_python", return_value=Path("/core/bin/python")), \
                patch.object(setup, "ensure_models", return_value={}), \
                patch.object(setup, "ensure_runtime", return_value=(Path("/runtime/server"), {})), \
                patch.object(setup, "ensure_paddle_weights", return_value={}), \
                patch.object(setup, "ensure_paddle_python", return_value=(Path("/paddle/bin/python"), {})), \
                patch.object(setup, "ensure_mlx_python", return_value=(Path("/mlx/bin/python"), self.probe())) as mlx, \
                patch.object(setup, "environment_identity", return_value={"engines": ["hunyuan", "paddle"]}), \
                patch.object(setup, "run_smokes") as smoke:
            result = setup.setup(options)
        self.assertEqual(result["status"], "pending-smoke")
        config = setup.read_environment(options.config_dir / "env.sh")
        self.assertEqual(config["JYUT_OCR_PADDLE_VL_BACKEND"], "auto")
        self.assertEqual(config["OCR_PADDLE_VL_BACKEND"], "mlx-vlm-server")
        self.assertEqual(config["MLX_VLM_PYTHON"], "/mlx/bin/python")
        self.assertEqual(config["OCR_PADDLE_DEVICE"], "cpu")
        self.assertEqual(config["OCR_HUNYUAN_SLOTS"], "1")
        mlx.assert_called_once()
        smoke.assert_not_called()

    def test_backend_and_locked_mlx_packages_change_setup_fingerprint(self):
        config = {"JYUT_OCR_PYTHON": "/core/bin/python", "PADDLEOCR_PYTHON": "/paddle/bin/python",
                  "PADDLE_VL_MODEL_DIR": "/weights/vl", "PADDLE_LAYOUT_MODEL_DIR": "/weights/layout",
                  "MLX_VLM_PYTHON": "/mlx/bin/python"}
        core = {"version_info": [3, 12, 16], "prefix": "/core"}
        paddle = {"python": [3, 12, 16], "prefix": "/paddle", "dists": {
            "paddlepaddle": {"version": self.lock["paddle"]["paddle_version"]},
            **{item["distribution"]: {"version": item["version"]} for item in self.lock["paddle"]["packages"]}}}
        with patch.object(setup, "python_identity", return_value=core), patch.object(setup, "paddle_probe", return_value=paddle), \
                patch.object(setup, "paddle_model_verified", return_value=True), patch.object(setup, "mlx_probe", return_value=self.probe()):
            native = setup.environment_identity(self.options, config, ("paddle",), "cpu", self.lock)
            mlx = setup.environment_identity(self.options, dict(config, OCR_PADDLE_VL_BACKEND="mlx-vlm-server"),
                                             ("paddle",), "cpu", self.lock)
        self.assertNotIn("vlBackend", native["paddle"])
        self.assertEqual(mlx["paddle"]["mlx"]["requirementsSha256"], self.lock["mlx"]["requirements_sha256"])
        self.assertNotEqual(setup.setup_fingerprint(native), setup.setup_fingerprint(mlx))

    def test_mlx_requirements_tampering_is_rejected(self):
        root = self.base / "skill"
        (root / "runtime").mkdir(parents=True)
        (root / "runtime/ocr-lock.json").write_text(json.dumps(self.lock))
        (root / self.lock["mlx"]["requirements"]).write_text("mlx-vlm==0.8.1\n")
        with self.assertRaises(setup.SetupError):
            setup.load_lock(root)

    def test_check_reports_backend_migration_without_writing_or_loading(self):
        self.options.config_dir.mkdir()
        config = self.options.config_dir / "env.sh"
        config.write_text("export JYUT_OCR_ENGINES=hunyuan,paddle\nexport JYUT_OCR_ENGINE=auto\n"
                          "export JYUT_OCR_DEVICE=auto\nexport OCR_PADDLE_DEVICE=cpu\n"
                          "export OCR_HUNYUAN_DEVICE=metal\n")
        before = config.read_bytes()
        with patch.object(setup, "platform_name", return_value="Darwin"), patch.object(setup, "machine_name", return_value="arm64"), \
                patch.object(setup, "load_lock", return_value=self.lock), patch.object(setup, "ensure_mlx_python") as ensure, \
                patch.object(setup, "run") as run:
            result = setup.inspect_setup(replace(self.options, engine="default"))
        self.assertEqual(result["status"], "action-required")
        self.assertIn("stale", result["reason"])
        self.assertEqual(config.read_bytes(), before)
        ensure.assert_not_called()
        run.assert_not_called()

    def test_mlx_smoke_passes_explicit_backend_interpreter_and_revision(self):
        directory = setup.smoke_directory(self.options, "a" * 64, "paddle")
        (directory / "renders").mkdir(parents=True)
        (directory / "renders/page-0001.png").touch()
        config = {"JYUT_OCR_PYTHON": "/core/python", "PADDLEOCR_PYTHON": "/paddle/python",
                  "OCR_PADDLE_VL_BACKEND": "mlx-vlm-server", "MLX_VLM_PYTHON": "/mlx/python"}
        _, command = setup.prepare_smoke(self.options, config, "paddle", "cpu", "a" * 64)
        self.assertEqual(command[command.index("--vl-backend") + 1], "mlx-vlm-server")
        self.assertEqual(command[command.index("--mlx-python") + 1], "/mlx/python")
        self.assertEqual(command[command.index("--model-revision") + 1], self.lock["paddle"]["models"][0]["revision"])

    def test_darwin_setup_uses_the_phase_memory_probe_and_preflights_cpu(self):
        with patch.object(setup, "platform_name", return_value="Darwin"), patch.object(phase, "mac_free_mib", return_value=12000), \
                patch.object(setup, "run") as run:
            self.assertEqual(setup.free_gpu_mib(), 12000)
            self.assertTrue(setup.gpu_device("hunyuan", "metal"))
            self.assertTrue(setup.gpu_device("paddle", "cpu"))
            with self.assertRaisesRegex(setup.SetupError, "GPU is busy"):
                setup.smoke_test(self.options, {}, "paddle", "cpu", "a" * 64)
        run.assert_not_called()
        with patch.object(setup, "platform_name", return_value="Darwin"), patch.object(phase, "mac_free_mib", return_value=None):
            with self.assertRaisesRegex(setup.SetupError, "vm_stat"):
                setup.free_gpu_mib()

    def test_unknown_unified_memory_uses_a_configured_handover(self):
        config = {"OCR_PHASE_RELEASE_CMD": "fake-release", "OCR_PHASE_RESTORE_CMD": "fake-restore"}
        with patch.object(setup, "platform_name", return_value="Darwin"), \
                patch.object(setup, "free_gpu_mib", side_effect=setup.SetupError("unknown")), \
                patch.object(setup, "handover_smokes", return_value={}) as handover:
            setup.run_smokes(self.options, config, ("vision", "hunyuan"),
                             {"vision": "vision-managed", "hunyuan": "metal"}, "a" * 64)
        self.assertEqual(handover.call_args.args[-1], setup.hunyuan_need_mib(1))
        self.assertEqual(setup.smoke_need_mib("vision", {}), 0)
        self.assertEqual(setup.smoke_need_mib("paddle", {"OCR_PHASE_REQUIRED_MIB": "15000"}), 15000)


if __name__ == "__main__":
    unittest.main()
