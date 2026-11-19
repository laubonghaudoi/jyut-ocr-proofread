"""Installer: both engines by default, Python 3.12 from uv, pinned Paddle
packages and weights, the llama.cpp release binaries, the GPU hand-over smoke,
the free-disk check and env.sh migration.  No network, GPU or model: the
metadata is the lock's, the servers and commands are fakes."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _setup_ocr as setup
import install

ROOT = Path(__file__).resolve().parents[1]
LOCK = setup.load_lock(ROOT)


def load_generator():
    """runtime/make_paddle_constraints.py, whose requirement, marker and
    version helpers the constraints tests use."""
    spec = importlib.util.spec_from_file_location("make_paddle_constraints", ROOT / "runtime/make_paddle_constraints.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator()


def gpu(**values):
    fields = {"index": 0, "driver": "580.95.05", "total_mib": 32607, "free_mib": 30000,
              "compute_capability": "12.0", "name": "test GPU"}
    fields.update(values)
    return setup.GPU(**fields)


class FileServer:
    """Serves FILES ({url path: bytes}) with Range support; records each path asked for."""
    def __init__(self, files):
        self.files, self.requests = files, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                content = outer.files.get(self.path)
                if content is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.options = setup.SetupOptions(ROOT, self.base / "config", self.base / "data")

    def tearDown(self):
        self.temp.cleanup()


class LockTests(unittest.TestCase):
    def test_lock_pins_both_engines_and_marks_the_gpu_wheels_trust_on_first_use(self):
        paddle = LOCK["paddle"]
        self.assertEqual(paddle["paddle_version"], "3.3.1")
        self.assertEqual({item["distribution"]: item["version"] for item in paddle["packages"]},
                         {"paddleocr": "3.7.0", "paddlex": "3.7.2"})
        self.assertEqual(next(item for item in paddle["packages"] if item["distribution"] == "paddleocr")["extras"],
                         ["doc-parser"])
        self.assertTrue(all(item["sha256"] for item in paddle["cpu_wheels"]))
        self.assertEqual(paddle["gpu_wheels"]["trust"], "trust-on-first-use")
        self.assertTrue(all(item["sha256"] is None for item in paddle["gpu_wheels"]["wheels"]))
        self.assertEqual({(item["index"], item["min_driver"]) for item in paddle["gpu_wheels"]["wheels"]},
                         {("cu130", "580"), ("cu129", "575.51.03"), ("cu126", "560.28.03"), ("cu118", "520.61.05")})
        # The revisions this machine family already caches, so existing drafts stay comparable.
        self.assertEqual({model["name"]: model["revision"] for model in paddle["models"]},
                         {"PaddleOCR-VL-1.6": "c5630abae1d940eafe0697512a0325494b02ab42",
                          "PP-DocLayoutV3": "7b48a7566925fa464281f930c58eee04fe2c862a"})
        weights = {item["name"]: item["sha256"] for model in paddle["models"] for item in model["files"]}
        self.assertEqual(weights["model.safetensors"], "85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db")
        self.assertEqual(weights["inference.pdiparams"], "70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137")
        runtime = LOCK["runtime"]
        self.assertEqual((runtime["tag"], runtime["commit"]), ("b10969", "391fac16460f15233a7740550d858ac96df3419d"))
        names = {item["name"] for item in runtime["prebuilt"]}
        self.assertIn("llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz", names)
        self.assertIn("llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz", names)
        self.assertIn("llama-b10969-bin-macos-arm64.tar.gz", names)
        for item in runtime["prebuilt"]:
            if item["device"] == "cuda":
                self.assertTrue(item["runtime_bundle"]["sha256"])

    def constraint_files(self):
        return [ROOT / setup.PADDLE_CONSTRAINTS,
                *sorted(ROOT.glob(setup.PADDLE_BUILD_CONSTRAINTS.format(index="*", machine="*")))]

    def test_the_paddle_constraints_pin_every_other_package_with_pypi_hashes(self):
        # CD-R10: the Paddle environment's transitive packages, from a working
        # environment (runtime/make_paddle_constraints.py), each with the
        # SHA-256 of its release files on PyPI.
        entries, by_file = {}, {}
        for path in self.constraint_files():
            text = path.read_text()
            by_file[path.name] = set()
            for line in text.replace("\\\n", " ").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                requirement, *options = line.split()
                name, version = requirement.split("==")
                self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertNotIn(name, entries, "one pin per package across the files")
                hashes = [option.removeprefix("--hash=sha256:") for option in options]
                self.assertTrue(hashes, name)
                for digest in hashes:
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")
                entries[name] = version
                by_file[path.name].add(name)
            pins = setup.version_pins(text)
            self.assertNotIn("--hash", pins)
            self.assertEqual([line for line in pins.splitlines() if line and not line.startswith("#")],
                             [f"{name}=={entries[name]}" for name in sorted(by_file[path.name])])
        # The wheels the installer verifies itself are not constrained here.
        self.assertFalse({"paddlepaddle", "paddlepaddle-gpu", "paddleocr", "paddlex"} & set(entries))
        for name in ("numpy", "opencv-contrib-python", "pillow", "safetensors"):
            self.assertIn(name, by_file["paddle-constraints.txt"])
        # E9-R1: the CUDA packages are the cu130 wheel's own, never common.
        self.assertEqual(sorted(by_file), ["paddle-constraints-cu130-x86_64.txt", "paddle-constraints.txt"])
        self.assertIn("nvidia-cudnn-cu13", by_file["paddle-constraints-cu130-x86_64.txt"])
        self.assertIn("cuda-python", by_file["paddle-constraints-cu130-x86_64.txt"])
        self.assertFalse({name for name in by_file["paddle-constraints.txt"] if name.startswith(("nvidia-", "cuda-"))})

    def test_no_paddle_wheel_of_the_lock_asks_a_version_its_constraints_forbid(self):
        # E9-R1: the constraints came from one cu130 environment and pinned
        # cuda-python==13.0.3 for every wheel; the cu129 x86_64 wheel asks
        # cuda-python==12.9.4, so its install had no solution.  Every lock
        # wheel's recorded Requires-Dist (and those of the lock's packages and
        # of what a wheel pins exactly) is checked against the files the
        # installer gives that wheel.
        record = json.loads((ROOT / "tests/paddle-wheel-requires.json").read_text())
        paddle = LOCK["paddle"]
        lock_wheels = sorted([("paddlepaddle", None, item["system"], item["machine"], item["url"])
                              for item in paddle["cpu_wheels"]]
                             + [("paddlepaddle-gpu", item["index"], "Linux", item["machine"], item["url"])
                                for item in paddle["gpu_wheels"]["wheels"]], key=str)
        self.assertEqual(sorted([(item["distribution"], item.get("index"), item["system"], item["machine"], item["url"])
                                 for item in record["wheels"]], key=str), lock_wheels,
                         "tests/paddle-wheel-requires.json is not the lock's: refresh it with "
                         "runtime/make_paddle_constraints.py --record-requires")
        self.assertEqual([(item["distribution"], item["version"]) for item in record["packages"]],
                         [(item["distribution"], item["version"]) for item in paddle["packages"]])
        packages = {GENERATOR.canonical(item["distribution"]): item for item in record["packages"]}
        extras = {GENERATOR.canonical(item["distribution"]): item.get("extras", []) for item in paddle["packages"]}
        pinned = {(item["distribution"], item["version"]): item["requires"] for item in record["pinned"]}
        build_files, problems = set(), []
        for wheel in record["wheels"]:
            files = setup.paddle_constraints(ROOT, wheel)
            build_files.update(path.name for path in files[1:])
            pins = {}
            for path in files:
                pins.update(GENERATOR.read_pins(path.read_text()))
            environment = GENERATOR.marker_environment(wheel["system"], wheel["machine"])
            where = f"{wheel['distribution']} {wheel.get('index') or 'CPU'} {wheel['system']} {wheel['machine']}"
            todo = [(wheel["distribution"], wheel["requires"], "")]
            todo += [(name, packages[name]["requires"], extra) for name in packages for extra in ("", *extras[name])]
            seen = set()
            checked = 0
            while todo:
                owner, requires, extra = todo.pop()
                if (owner, extra) in seen:
                    continue
                seen.add((owner, extra))
                for text in requires:
                    requirement = GENERATOR.parse_requirement(text)
                    if not GENERATOR.marker_true(requirement.marker, environment, extra):
                        continue
                    if requirement.name in pins:
                        checked += 1
                        if not GENERATOR.satisfies(pins[requirement.name], requirement.specifiers):
                            problems.append(f"{where}: {owner} asks {text!r}, the constraints pin "
                                            f"{requirement.name}=={pins[requirement.name]}")
                    if requirement.name in packages:
                        todo += [(requirement.name, packages[requirement.name]["requires"], wanted)
                                 for wanted in ("", *requirement.extras)]
                    version = GENERATOR.exact_pin(requirement)
                    if version and (requirement.name, version) in pinned:
                        todo.append((requirement.name, pinned[(requirement.name, version)], ""))
            self.assertGreater(checked, 20, where)
            # Nothing a Paddle wheel pins exactly is left to the common file.
            common = GENERATOR.read_pins((ROOT / setup.PADDLE_CONSTRAINTS).read_text())
            exact = {GENERATOR.parse_requirement(text).name for text in wheel["requires"]
                     if GENERATOR.exact_pin(GENERATOR.parse_requirement(text))}
            problems += [f"{where}: the wheel pins {name} itself; the common file pins it too"
                         for name in sorted(exact & set(common))]
        if problems:
            self.fail("\n" + "\n".join(problems))
        self.assertEqual(build_files, {"paddle-constraints-cu130-x86_64.txt"})

    def test_each_paddle_wheel_gets_the_common_constraints_and_only_its_own_builds(self):
        common = ROOT / setup.PADDLE_CONSTRAINTS
        build = ROOT / "runtime/paddle-constraints-cu130-x86_64.txt"
        cases = [((gpu(), "Linux", "x86_64"), [common, build]),
                 ((gpu(driver="576.02"), "Linux", "x86_64"), [common]),            # cu129
                 ((gpu(driver="576.02"), "Linux", "aarch64"), [common]),           # cu129 aarch64
                 ((gpu(driver="565.57.01", compute_capability="8.6", total_mib=24576), "Linux", "x86_64"), [common]),
                 ((gpu(driver="535.104.05", compute_capability="8.9", total_mib=24564), "Linux", "x86_64"), [common]),
                 ((None, "Linux", "x86_64"), [common]),
                 ((None, "Darwin", "arm64"), [common])]
        for (card, system, machine), files in cases:
            with self.subTest(card=card, system=system, machine=machine):
                plan, _ = setup.paddle_plan(LOCK, system=system, machine=machine, request="auto",
                                            gpus=[card] if card else [])
                self.assertEqual(setup.paddle_constraints(ROOT, plan.wheel), files)
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(setup.SetupError, "missing"):
            setup.paddle_constraints(Path(directory), {"index": "cu130", "machine": "x86_64"})
        names = setup.tool_hashes(ROOT, "paddle")
        self.assertIn(setup.PADDLE_CONSTRAINTS, names)
        self.assertIn("runtime/paddle-constraints-cu130-x86_64.txt", names)

    def test_the_generator_keeps_what_the_paddle_wheel_pins_out_of_the_common_file(self):
        # E9-R1: from a fake environment (no network: the hashes are stubbed).
        def dist(folder, name, version, *requires):
            info = folder / "lib/python3.12/site-packages" / f"{name.replace('-', '_')}-{version}.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text("Metadata-Version: 2.1\n" + f"Name: {name}\nVersion: {version}\n"
                                           + "".join(f"Requires-Dist: {line}\n" for line in requires))

        def environment(folder, gpu_build):
            here = 'platform_system == "{}" and platform_machine == "{}"'.format(platform.system(), platform.machine())
            paddle = ["paddlepaddle-gpu", "3.3.1"] if gpu_build else ["paddlepaddle", "3.3.1"]
            dist(folder, *paddle, "numpy>=1.21", "opt_einsum==3.3.0", "setuptools; python_version >= \"3.12\"",
                 *([f"cuda-python==13.0.3; {here}", f"nvidia-cudnn-cu13==9.13.0.50; {here}",
                    'nvidia-cuda-runtime-cu12==12.9.37; platform_system == "Windows"'] if gpu_build else []))
            dist(folder, "paddleocr", "3.7.0", "paddlex[ocr-core]<3.8.0,>=3.7.0", "PyYAML>=6",
                 'paddlex[ocr]<3.8.0,>=3.7.0; extra == "doc-parser"')
            dist(folder, "paddlex", "3.7.2", "numpy<2.4,>=1.24", "PyYAML==6.0.2",
                 'opencv-contrib-python==4.10.0.84; extra == "ocr-core"', 'einops; extra == "ocr"')
            for name, version, *requires in (("numpy", "2.3.5"), ("pyyaml", "6.0.2"), ("setuptools", "84.0.0"),
                                             ("opencv-contrib-python", "4.10.0.84", "numpy>=1.21"),
                                             ("einops", "0.8.2"), ("opt-einsum", "3.3.0", "numpy (>=1.7)"),
                                             ("cuda-python", "13.0.3", "cuda-bindings~=13.0.3", "cuda-pathfinder~=1.1",
                                              'cuda-bindings[all]~=13.0.3; extra == "all"'),
                                             ("cuda-bindings", "13.0.3", "cuda-pathfinder~=1.1",
                                              'cuda-toolkit[nvrtc]==13.*; extra == "all"'),
                                             ("cuda-pathfinder", "1.8.1"), ("nvidia-cudnn-cu13", "9.13.0.50", "nvidia-cublas"),
                                             ("nvidia-cublas", "13.0.2.14")):
                if gpu_build or not name.startswith(("cuda-", "nvidia-")):
                    dist(folder, name, version, *requires)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            gpu_venv, cpu_venv = base / "gpu", base / "cpu"
            environment(gpu_venv, True)
            environment(cpu_venv, False)
            index = next(item["index"] for item in LOCK["paddle"]["gpu_wheels"]["wheels"]
                         if item["machine"] == platform.machine()) if platform.machine() in ("x86_64", "aarch64") else None
            with patch.object(GENERATOR, "release_hashes", side_effect=lambda name, version: ["0" * 64]):
                if index:
                    self.assertEqual(GENERATOR.main([str(gpu_venv), "--index", index, "--out-dir", str(base / "gpu-out")]), 0)
                    common = GENERATOR.read_pins((base / "gpu-out/paddle-constraints.txt").read_text())
                    build = GENERATOR.read_pins(
                        (base / f"gpu-out/paddle-constraints-{index}-{platform.machine()}.txt").read_text())
                    self.assertEqual(sorted(common), ["einops", "numpy", "opencv-contrib-python", "pyyaml", "setuptools"])
                    self.assertEqual(sorted(build), ["cuda-bindings", "cuda-pathfinder", "cuda-python", "nvidia-cublas",
                                                     "nvidia-cudnn-cu13", "opt-einsum"])
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        GENERATOR.main([str(gpu_venv), "--out-dir", str(base / "no-index")])
                    self.assertFalse((base / "no-index").exists())
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(GENERATOR.main([str(cpu_venv), "--out-dir", str(base / "cpu-out")]), 0)
                self.assertEqual(sorted(GENERATOR.read_pins((base / "cpu-out/paddle-constraints.txt").read_text())),
                                 ["einops", "numpy", "opencv-contrib-python", "pyyaml", "setuptools"])
                self.assertEqual(sorted(path.name for path in (base / "cpu-out").iterdir()), ["paddle-constraints.txt"])
                self.assertIn("opt-einsum", errors.getvalue())

    def test_pip_gets_the_constraints_version_pins_only(self):
        with tempfile.TemporaryDirectory() as directory:
            options = setup.SetupOptions(ROOT, Path(directory) / "config", Path(directory) / "data")
            seen = {}

            def fake_run(command, **kwargs):
                argv = [str(item) for item in command]
                seen["argv"] = argv
                seen["pins"] = [Path(argv[index + 1]).read_text() for index, item in enumerate(argv) if item == "-c"]
                return type("Result", (), {"stdout": ""})()
            files = self.constraint_files()
            with patch.object(setup, "run", side_effect=fake_run):
                setup.install_requirements(options, Path("/venv/bin/python"), ["paddlex @ file:///w.whl"], uv=None,
                                           constraints=files)
            self.assertEqual(seen["argv"][1:4], ["-m", "pip", "install"])
            self.assertEqual(seen["argv"][-1], "paddlex @ file:///w.whl")
            self.assertEqual(len(seen["pins"]), len(files))
            self.assertIn("numpy==", seen["pins"][0])
            self.assertIn("cuda-python==", seen["pins"][1])
            self.assertNotIn("--hash", "".join(seen["pins"]))
            copies = [Path(seen["argv"][index + 1]) for index, item in enumerate(seen["argv"]) if item == "-c"]
            self.assertFalse(any(copy.exists() for copy in copies))

    def test_a_gpu_wheel_with_a_hash_is_refused_as_a_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "runtime").mkdir()
            changed = json.loads(json.dumps(LOCK))
            changed["paddle"]["gpu_wheels"]["trust"] = "pinned"
            (repo / "runtime/ocr-lock.json").write_text(json.dumps(changed))
            with self.assertRaisesRegex(setup.SetupError, "ocr-lock"):
                setup.load_lock(repo)


class PlatformTests(unittest.TestCase):
    def plan(self, system="Linux", machine="x86_64", request="auto", gpus=()):
        return setup.paddle_plan(LOCK, system=system, machine=machine, request=request, gpus=list(gpus))

    def test_paddle_wheel_follows_driver_capability_and_memory(self):
        cases = [
            (gpu(), "gpu:0", "cu130"),                                                      # Blackwell, driver 580
            (gpu(driver="576.02"), "gpu:0", "cu129"),                                       # Blackwell, driver 575.51+
            (gpu(driver="565.57.01", compute_capability="8.6", total_mib=24576), "gpu:0", "cu126"),
            (gpu(driver="535.104.05", compute_capability="8.9", total_mib=24564), "gpu:0", "cu118"),
            (gpu(driver="510.47.03", compute_capability="8.6"), "cpu", None),               # driver too old
            (gpu(compute_capability="8.6", total_mib=8192), "cpu", None),                   # too small
            (gpu(compute_capability="6.1", total_mib=12288), "cpu", None),                  # too old a card
            (gpu(compute_capability="7.5", total_mib=16384), "cpu", None),                  # no bf16: needs twice
            (gpu(compute_capability="7.5", total_mib=49152, driver="565.57.01"), "gpu:0", "cu126"),
            (gpu(compute_capability="7.5", total_mib=49152), "gpu:0", "cu130"),
            (gpu(compute_capability=None), "cpu", None),
        ]
        for card, device, index in cases:
            with self.subTest(card=card):
                plan, notes = self.plan(gpus=[card])
                self.assertEqual((plan.device, plan.index), (device, index))
                if device == "cpu":
                    self.assertEqual(plan.distribution, "paddlepaddle")
                    self.assertTrue(plan.wheel["sha256"])
                    self.assertTrue(notes)
                else:
                    self.assertEqual(plan.distribution, "paddlepaddle-gpu")
        # Blackwell under an old driver has no CUDA 12.6 or 11.8 build to fall back on.
        self.assertEqual(self.plan(gpus=[gpu(driver="565.57.01")])[0].device, "cpu")
        self.assertEqual(self.plan(gpus=[gpu(compute_capability="7.5", total_mib=49152, driver="565.57.01")])[0].need_mib,
                         2 * setup.PADDLE_NEED_MIB)

    def test_paddle_without_a_gpu_and_on_each_platform(self):
        self.assertEqual(self.plan()[0].device, "cpu")
        self.assertEqual(self.plan(request="cpu", gpus=[gpu()])[0].device, "cpu")
        mac, _ = self.plan(system="Darwin", machine="arm64", gpus=[gpu()])
        self.assertEqual((mac.device, mac.wheel["name"]), ("cpu", "paddlepaddle-3.3.1-cp312-cp312-macosx_11_0_arm64.whl"))
        self.assertIsNone(self.plan(system="Darwin", machine="x86_64")[0])
        # No aarch64 CPU wheel on PyPI: GPU (cu129) or nothing.
        none, notes = self.plan(machine="aarch64")
        self.assertIsNone(none)
        self.assertIn("no CPU wheel", notes[-1])
        self.assertEqual(self.plan(machine="aarch64", gpus=[gpu()])[0].index, "cu129")

    def test_intel_mac_is_rejected_and_linux_keeps_hunyuan_exception(self):
        with self.assertRaisesRegex(setup.SetupError, "native arm64"):
            setup.choose_engines("hunyuan-paddle", system="Darwin", machine="x86_64")
        self.assertEqual(setup.choose_engines("hunyuan", system="Linux", machine="x86_64")[0], ("hunyuan",))

    def test_release_binary_follows_the_driver_and_needs_no_toolkit(self):
        def name(**kwargs):
            asset = setup.release_binary(LOCK, **kwargs)
            return asset and asset["name"]
        self.assertEqual(name(system="Linux", machine="x86_64", device="cuda", gpu=gpu()),
                         "llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz")
        self.assertEqual(name(system="Linux", machine="x86_64", device="cuda", gpu=gpu(driver="572.16")),
                         "llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz")
        self.assertIsNone(name(system="Linux", machine="x86_64", device="cuda", gpu=gpu(driver="560.35.03")))
        self.assertEqual(name(system="Linux", machine="x86_64", device="cpu", gpu=None), "llama-b10969-bin-ubuntu-x64.tar.gz")
        self.assertEqual(name(system="Darwin", machine="arm64", device="metal", gpu=None), "llama-b10969-bin-macos-arm64.tar.gz")
        self.assertEqual(name(system="Darwin", machine="arm64", device="cpu", gpu=None), "llama-b10969-bin-macos-arm64.tar.gz")
        self.assertIsNone(name(system="Darwin", machine="x86_64", device="cpu", gpu=None))

    def test_gpu_detection_needs_the_driver_only_and_metal_no_offline_compiler(self):
        output = "0, 580.95.05, 32607, 1182, 12.0, NVIDIA GeForce RTX 5090\n"
        result = type("Result", (), {"stdout": output})()
        with patch.object(setup.shutil, "which", side_effect=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None), \
                patch.object(setup, "run", return_value=result) as runner:
            gpus = setup.nvidia_gpus()
            self.assertTrue(setup.cuda_available())
        self.assertEqual(gpus, [setup.GPU(0, "580.95.05", 32607, 1182, "12.0", "NVIDIA GeForce RTX 5090")])
        self.assertTrue(all(call.args[0][0] == "nvidia-smi" for call in runner.call_args_list))
        # An older nvidia-smi without compute_cap: asked again without it.
        old = type("Result", (), {"stdout": "0, 470.82.01, 11178, 11000, GeForce GTX 1080 Ti\n"})()
        with patch.object(setup.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
                patch.object(setup, "run", side_effect=[setup.SetupError("not a valid field"), old]):
            self.assertEqual(setup.nvidia_gpus()[0].compute_capability, None)
        with patch.object(setup.platform, "system", return_value="Darwin"), \
                patch.object(setup.platform, "machine", return_value="arm64"), patch.object(setup, "run") as runner:
            self.assertTrue(setup.metal_available())
            runner.assert_not_called()


class EnvironmentTests(Case):
    def test_old_skill_env_is_read_and_its_lines_kept(self):
        self.options.config_dir.mkdir()
        path = self.options.config_dir / "env.sh"
        original = textwrap.dedent("""\
            # Machine-local configuration written for the old skill.
            export PADDLEOCR_PYTHON="$HOME/.venvs/paddleocr/bin/python"
            export HUNYUAN_MODEL="$HOME/models/hunyuanocr/HunyuanOCR-bf16.gguf"
            export OCR_PHASE_RELEASE_CMD="systemctl --user stop $LLM_UNIT"
            export OCR_PHASE_RESTORE_CMD="$(command -v start-llm)"
            export OCR_PHASE_HEALTH_URL="http://127.0.0.1:8090/v1/models"
            export OCR_PHASE_MAX_SECONDS=540
            export PROOFREAD_ENDPOINT="http://127.0.0.1:8090/v1/chat/completions"
            export PROOFREAD_MODEL="local-27b"
            export OCR_HUNYUAN_SLOTS=16
            export SOMETHING_ELSE="$(never-run)"
            """)
        path.write_text(original)
        values = setup.read_environment(path, home=self.base)
        self.assertEqual(values["PADDLEOCR_PYTHON"], str(self.base / ".venvs/paddleocr/bin/python"))
        self.assertEqual(values["OCR_PHASE_RELEASE_CMD"], "systemctl --user stop $LLM_UNIT")
        self.assertEqual(values["OCR_PHASE_RESTORE_CMD"], "$(command -v start-llm)")
        self.assertEqual(values["PROOFREAD_MODEL"], "local-27b")
        self.assertNotIn("SOMETHING_ELSE", values)
        config = dict(values, JYUT_OCR_PYTHON="/data/python/bin/python", PADDLEOCR_PYTHON="/data/paddle-python/bin/python")
        setup.write_environment(path, setup.changed_keys(values, config))
        text = path.read_text()
        for line in original.splitlines():
            if not line.startswith(("export PADDLEOCR_PYTHON=",)):
                self.assertIn(line, text)
        self.assertIn("export PADDLEOCR_PYTHON=/data/paddle-python/bin/python", text)
        self.assertIn("export JYUT_OCR_PYTHON=/data/python/bin/python", text)

    def source(self, path: Path, name: str, **env) -> str:
        """NAME's value after bash sources PATH (with ENV), or the error."""
        result = subprocess.run(["bash", "-c", f'. "$1" && printf %s "${{{name}}}"', "bash", str(path)],
                                env=dict({"PATH": os.environ.get("PATH", "")}, **env),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_a_key_set_in_a_block_or_twice_keeps_every_line_and_gets_an_override(self):
        # CD-R4: the writer kept the first line of an updated key and deleted
        # the rest, emptying an if block (a syntax error for every shell that
        # sources env.sh) and dropping a person's duplicate line.
        self.options.config_dir.mkdir()
        path = self.options.config_dir / "env.sh"
        original = textwrap.dedent("""\
            export HUNYUAN_LLAMA_SERVER="$HOME/llama.cpp/build/bin/llama-server"
            export OCR_HUNYUAN_SLOTS=16  # tuned
            export OCR_HUNYUAN_SLOTS=8
            if [ -n "$X" ]; then
              export HUNYUAN_LLAMA_SERVER=/opt/other
            fi
            export PROOFREAD_MODEL=m; export PROOFREAD_EFFORT=xhigh
            export JYUT_OCR_PYTHON=/old/python
            alias foo=bar
            """)
        path.write_text(original)
        updates = {"HUNYUAN_LLAMA_SERVER": "/data/runtime/bin/llama-server", "OCR_HUNYUAN_SLOTS": "4",
                   "PROOFREAD_MODEL": "local-27b", "JYUT_OCR_PYTHON": "/data/python/bin/python",
                   "OCR_HUNYUAN_DEVICE": "cuda"}
        setup.write_environment(path, updates)
        text = path.read_text()
        self.assertIsNone(setup.shell_syntax_error(text))
        # F2-F4: every line stays; the one plain JYUT_OCR_PYTHON line is kept
        # commented out where it stood, with the new export right after it.
        kept = [("# " + line if line.startswith("export JYUT_OCR_PYTHON=") else line) for line in original.splitlines()]
        self.assertEqual([line for line in text.splitlines() if line in kept], kept)  # all, in order
        self.assertIn("# export JYUT_OCR_PYTHON=/old/python\nexport JYUT_OCR_PYTHON=/data/python/bin/python\n", text)
        self.assertEqual(self.source(path, "JYUT_OCR_PYTHON"), "/data/python/bin/python")
        for name in ("HUNYUAN_LLAMA_SERVER", "OCR_HUNYUAN_SLOTS", "PROOFREAD_MODEL"):
            self.assertEqual(text.count(setup.OVERRIDE_NOTE.format(name=name)), 1, name)
        self.assertNotIn(setup.OVERRIDE_NOTE.format(name="JYUT_OCR_PYTHON"), text)
        self.assertEqual(next((self.options.config_dir / "env-backups").iterdir()).read_text(), original)
        for env in ({}, {"X": "1"}):
            self.assertEqual(self.source(path, "HUNYUAN_LLAMA_SERVER", HOME=str(self.base), **env),
                             "/data/runtime/bin/llama-server")
        self.assertEqual(self.source(path, "PROOFREAD_EFFORT"), "xhigh")
        self.assertEqual(self.source(path, "OCR_HUNYUAN_SLOTS"), "4")
        values = setup.read_environment(path, home=self.base)
        self.assertEqual(values["HUNYUAN_LLAMA_SERVER"], "/data/runtime/bin/llama-server")
        self.assertEqual(values["OCR_HUNYUAN_DEVICE"], "cuda")
        # The next update rewrites the installer's own override: nothing piles up.
        setup.write_environment(path, {"OCR_HUNYUAN_SLOTS": "2", "HUNYUAN_LLAMA_SERVER": "/data/r2/llama-server",
                                       "JYUT_OCR_PYTHON": "/data/python2/bin/python"})
        again = path.read_text()
        self.assertEqual(len(again.splitlines()), len(text.splitlines()))
        self.assertIn("# export JYUT_OCR_PYTHON=/old/python\nexport JYUT_OCR_PYTHON=/data/python2/bin/python\n", again)
        self.assertEqual(self.source(path, "OCR_HUNYUAN_SLOTS"), "2")
        self.assertEqual(self.source(path, "HUNYUAN_LLAMA_SERVER", X="1"), "/data/r2/llama-server")
        self.assertIn("  export HUNYUAN_LLAMA_SERVER=/opt/other\n", again)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_a_replaced_line_is_kept_commented_out_and_the_file_keeps_its_mode(self):
        # F2-F4: the installer rewrote a person's one HUNYUAN_LLAMA_SERVER line
        # in place (its old value was left only in env-backups/), and the file
        # went from 0664 to 0600.
        self.options.config_dir.mkdir()
        path = self.options.config_dir / "env.sh"
        old_line = 'export HUNYUAN_LLAMA_SERVER="$HOME/llama.cpp/build/bin/llama-server"'
        original = textwrap.dedent(f"""\
            # Machine-local configuration.

            # --- engine B ---
            export HUNYUAN_MODEL="$HOME/models/hunyuanocr/HunyuanOCR-bf16.gguf"
            {old_line}
            export OCR_PHASE_RELEASE_CMD="your-service-manager stop your-model-service"
            export OCR_HUNYUAN_SLOTS=16
            """)
        path.write_text(original)
        path.chmod(0o664)
        home = {"HOME": str(self.base)}
        existing = setup.read_environment(path, home=self.base)
        config = dict(existing, HUNYUAN_LLAMA_SERVER="/data/runtimes/b10969/bin/llama-server",
                      JYUT_OCR_PYTHON="/data/python/bin/python")
        setup.write_environment(path, setup.changed_keys(existing, config))
        text = path.read_text()
        lines = text.splitlines()
        # Every old line is still there, in order; the replaced one commented out.
        kept = [("# " + line if line == old_line else line) for line in original.splitlines()]
        self.assertEqual([line for line in lines if line in kept], kept)
        at = lines.index("# " + old_line)
        self.assertRegex(lines[at - 1], setup._REPLACED_NOTE)
        self.assertIn("HUNYUAN_LLAMA_SERVER: replaced by the jyut-ocr installer on", lines[at - 1])
        self.assertEqual(lines[at + 1], "export HUNYUAN_LLAMA_SERVER=/data/runtimes/b10969/bin/llama-server")
        self.assertEqual(lines[-1], "export JYUT_OCR_PYTHON=/data/python/bin/python")
        self.assertEqual(len(lines), len(original.splitlines()) + 3)
        # The shell sees only the new value; the reader too.
        self.assertEqual(self.source(path, "HUNYUAN_LLAMA_SERVER", **home), "/data/runtimes/b10969/bin/llama-server")
        self.assertEqual(setup.read_environment(path, home=self.base)["HUNYUAN_LLAMA_SERVER"],
                         "/data/runtimes/b10969/bin/llama-server")
        # The file keeps its mode; the backup is the old text, readable by its owner only.
        self.assertEqual(path.stat().st_mode & 0o777, 0o664)
        backups = list((self.options.config_dir / "env-backups").iterdir())
        self.assertEqual([backup.read_text() for backup in backups], [original])
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        # A rerun with the same configuration changes nothing.
        again = setup.read_environment(path, home=self.base)
        self.assertEqual(setup.changed_keys(again, config), {})
        setup.write_environment(path, setup.changed_keys(again, config))
        # Its own lines already hold these values: even unfiltered they stay as they are.
        setup.write_environment(path, {key: config[key] for key in ("HUNYUAN_LLAMA_SERVER", "JYUT_OCR_PYTHON")})
        self.assertEqual(path.read_text(), text)
        self.assertEqual(len(list((self.options.config_dir / "env-backups").iterdir())), 1)
        # A later change rewrites the installer's export: no second copy or note.
        setup.write_environment(path, {"HUNYUAN_LLAMA_SERVER": "/data/runtimes/next/bin/llama-server"})
        later = path.read_text()
        self.assertEqual(len(later.splitlines()), len(lines))
        self.assertEqual(later.count("# " + old_line), 1)
        self.assertEqual(len(setup._REPLACED_NOTE.findall(later)), 1)
        self.assertEqual(self.source(path, "HUNYUAN_LLAMA_SERVER", **home), "/data/runtimes/next/bin/llama-server")
        self.assertEqual(path.stat().st_mode & 0o777, 0o664)
        # The note is dated as given.
        self.assertEqual(setup.environment_text("export OCR_HUNYUAN_SLOTS=1\n", {"OCR_HUNYUAN_SLOTS": "2"},
                                                today="2026-11-16"),
                         "# OCR_HUNYUAN_SLOTS: replaced by the jyut-ocr installer on 2026-11-16; the earlier line is "
                         "kept below as a comment.\n# export OCR_HUNYUAN_SLOTS=1\nexport OCR_HUNYUAN_SLOTS=2\n")
        # A new env.sh is the owner's only; a symlinked one keeps its target's mode.
        fresh = self.options.config_dir / "fresh" / "env.sh"
        setup.write_environment(fresh, {"OCR_HUNYUAN_SLOTS": "1"})
        self.assertEqual(fresh.stat().st_mode & 0o777, 0o600)
        target = self.base / "dotfiles/env.sh"
        target.parent.mkdir()
        target.write_text("export OCR_HUNYUAN_SLOTS=1\n")
        target.chmod(0o640)
        link = self.options.config_dir / "linked.sh"
        link.symlink_to(target)
        setup.write_environment(link, {"OCR_HUNYUAN_SLOTS": "2"})
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_a_key_the_installer_appended_is_kept_once_on_its_first_change(self):
        # install.md: a key env.sh lacked is appended as a plain export with no
        # note; its first change keeps that line as a comment once, as for any
        # one-line key, and later changes rewrite only the export.
        text = setup.environment_text("export A=1\n", {"JYUT_OCR_PYTHON": "/d1/python/bin/python"}, today="2026-11-16")
        self.assertEqual(text, "export A=1\nexport JYUT_OCR_PYTHON=/d1/python/bin/python\n")
        first = setup.environment_text(text, {"JYUT_OCR_PYTHON": "/d2/python/bin/python"}, today="2026-11-17")
        self.assertEqual(first, "export A=1\n# JYUT_OCR_PYTHON: replaced by the jyut-ocr installer on 2026-11-17; the earlier "
                                "line is kept below as a comment.\n# export JYUT_OCR_PYTHON=/d1/python/bin/python\n"
                                "export JYUT_OCR_PYTHON=/d2/python/bin/python\n")
        later = setup.environment_text(first, {"JYUT_OCR_PYTHON": "/d3/python/bin/python"}, today="2026-11-18")
        self.assertEqual(later, first.replace("export JYUT_OCR_PYTHON=/d2/", "export JYUT_OCR_PYTHON=/d3/"))
        self.assertEqual(setup.environment_text(later, {"JYUT_OCR_PYTHON": "/d3/python/bin/python"}), later)

    def test_the_top_level_reader_knows_blocks_quotes_and_here_documents(self):
        text = textwrap.dedent("""\
            export A=1
            if true; then
              export A=2
            fi
            setup() {
              export A=3
            }
            case "$x" in
              a) export A=4;;
            esac
            cat <<EOF
            export A=5
            EOF
            export B="two
            export A=6"
            X=$(if true; then
            export A=7
            fi)
            for f in a; do export A=8; done
            export A=9
            """)
        lines = text.splitlines()
        top = setup.top_level_lines(text)
        self.assertEqual([lines[i] for i in sorted(top) if lines[i].startswith(("export", "  export"))],
                         ["export A=1", 'export B="two', "export A=9"])

    def test_a_result_bash_rejects_is_not_written(self):
        self.options.config_dir.mkdir()
        path = self.options.config_dir / "env.sh"
        path.write_text("export OCR_HUNYUAN_SLOTS=1\n")
        if setup.shell_syntax_error("if true; then\nfi\n") is None:
            self.skipTest("needs bash")
        with patch.object(setup, "environment_text", return_value="if true; then\nfi\n"), \
                self.assertRaisesRegex(setup.SetupError, "bash -n"):
            setup.write_environment(path, {"OCR_HUNYUAN_SLOTS": "2"})
        self.assertEqual(path.read_text(), "export OCR_HUNYUAN_SLOTS=1\n")
        self.assertFalse((self.options.config_dir / "env-backups").exists())

    def test_local_model_options_write_the_proofreading_and_hand_over_keys(self):
        local = setup.LocalModel("http://127.0.0.1:8080/v1/chat/completions", "local-27b",
                                 "stop-llm", "start-llm")
        updates = setup.local_model_updates(local, {})
        self.assertEqual(updates, {"PROOFREAD_ENDPOINT": local.endpoint, "PROOFREAD_MODEL": "local-27b",
                                   "OCR_PHASE_RELEASE_CMD": "stop-llm", "OCR_PHASE_RESTORE_CMD": "start-llm",
                                   "OCR_PHASE_HEALTH_URL": "http://127.0.0.1:8080/v1/models",
                                   "OCR_PHASE_MAX_SECONDS": "540"})
        kept = setup.local_model_updates(replace(local, health_url="http://127.0.0.1:8080/health"),
                                         {"OCR_PHASE_MAX_SECONDS": "300"})
        self.assertEqual(kept["OCR_PHASE_HEALTH_URL"], "http://127.0.0.1:8080/health")
        self.assertNotIn("OCR_PHASE_MAX_SECONDS", kept)
        self.assertEqual(set(setup.local_model_updates(setup.LocalModel("https://models.example/v1", "m"), {})),
                         {"PROOFREAD_ENDPOINT", "PROOFREAD_MODEL"})
        for bad in (replace(local, restore_cmd=None), replace(local, endpoint="127.0.0.1:8080"), replace(local, name=" ")):
            with self.subTest(bad=bad), self.assertRaises(setup.SetupError):
                setup.local_model_updates(bad, {})

    def test_the_local_model_concurrency_is_written_as_the_proofreading_inflight_limit(self):
        # E-R10: the server's concurrency reaches env.sh, so rule set B's book
        # profile and phase 3 need no N from the agent.
        local = setup.LocalModel("http://127.0.0.1:8080/v1/chat/completions", "local-27b", concurrency=2)
        self.assertEqual(setup.local_model_updates(local, {}),
                         {"PROOFREAD_ENDPOINT": local.endpoint, "PROOFREAD_MODEL": "local-27b",
                          "PROOFREAD_MAX_INFLIGHT": "2"})
        self.assertNotIn("PROOFREAD_MAX_INFLIGHT", setup.local_model_updates(replace(local, concurrency=None), {}))
        for bad in (0, -1, True, "2"):
            with self.subTest(bad=bad), self.assertRaisesRegex(setup.SetupError, "concurrency"):
                setup.local_model_updates(replace(local, concurrency=bad), {})
        with patch.object(install, "build_portable_specs", return_value=[]), patch.object(install, "perform_install"), \
                patch.object(install, "setup", return_value={"status": "ready"}) as setup_call, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--config-dir", str(self.options.config_dir), "--env-only", "--local-model-endpoint", local.endpoint,
                                           "--local-model-name", "local-27b", "--local-model-concurrency", "2"]), 0)
        self.assertEqual(setup_call.call_args[0][0].local_model.concurrency, 2)
        for argv in (["--local-model-concurrency", "0"], ["--local-model-concurrency", "two"],
                     ["--local-model-concurrency", "2"]):  # the last one without an endpoint and a name
            full = ["--env-only", *(["--local-model-endpoint", local.endpoint, "--local-model-name", "m"]
                                    if argv[-1] != "2" else []), *argv]
            with self.subTest(argv=full), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                install.main(full)
            self.assertEqual(stop.exception.code, 2)
        self.options.config_dir.mkdir()
        environment = self.options.config_dir / "env.sh"
        environment.write_text("export PROOFREAD_MAX_INFLIGHT=8\n")
        setup.write_environment(environment, setup.changed_keys(setup.read_environment(environment),
                                                                setup.local_model_updates(local, {})))
        self.assertEqual(setup.read_environment(environment)["PROOFREAD_MAX_INFLIGHT"], "2")

    def test_install_entry_takes_qwen_and_the_local_model_options(self):
        with patch.object(install, "build_portable_specs", return_value=[]) as build, \
                patch.object(install, "perform_install"), \
                patch.object(install, "setup", return_value={"status": "ready"}) as setup_call, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--config-dir", str(self.options.config_dir), "--agent", "qwen", "--local-model-endpoint", "http://127.0.0.1:8080/v1/chat/completions",
                                           "--local-model-name", "local-27b", "--local-model-release-cmd", "stop-llm",
                                           "--local-model-restore-cmd", "start-llm"]), 0)
        self.assertEqual(build.call_args.kwargs["agents"], ["qwen"])
        options = setup_call.call_args[0][0]
        self.assertEqual((options.engine, options.device), ("default", "default"))
        self.assertLessEqual(options.started_at, setup.time.time())  # CD-R12: the call's start
        self.assertEqual(options.local_model.release_cmd, "stop-llm")
        for argv in (["--env-only", "--local-model-endpoint", "http://127.0.0.1:1/v1"],
                     ["--env-only", "--check", "--local-model-endpoint", "http://127.0.0.1:1/v1", "--local-model-name", "m"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                install.main(argv)
            self.assertEqual(stop.exception.code, 2)
        from install_local_adapters import build_portable_specs
        spec = build_portable_specs(repo_root=ROOT, agents=["qwen"], home=self.base)[0]
        self.assertEqual(spec["target"], self.base / ".qwen/skills/jyut-ocr-proofread")


class PythonTests(Case):
    def test_core_venv_is_python_3_12_from_uv_in_the_data_folder(self):
        calls = []
        venv_python = self.options.data_dir / "python/bin/python"
        found = {"python": False}

        def fake_run(command, **kwargs):
            argv = [str(item) for item in command]
            calls.append((argv, kwargs.get("env") or {}))
            stdout = ""
            if argv[1:] == ["python", "find", "--help"]:
                stdout = "      --system\n          Only find system Python interpreters [env: UV_SYSTEM_PYTHON=]\n"
            elif argv[1:3] == ["python", "find"]:
                if not found["python"]:
                    raise setup.SetupError("no python 3.12")
                stdout = "/data/python-runtimes/cpython-3.12/bin/python3.12\n"
            elif argv[1:] == ["python", "install", "--help"]:
                stdout = "      --no-bin\n          Do not install a Python executable into the `bin` directory\n"
            elif argv[1:3] == ["python", "install"]:
                found["python"] = True
            elif argv[1] == "venv":
                venv_python.parent.mkdir(parents=True)
                venv_python.write_text("python")
            return type("Result", (), {"stdout": stdout})()
        with patch.object(setup, "find_uv", return_value=Path("/tools/uv")), \
                patch.object(setup, "run", side_effect=fake_run), patch.object(setup, "command_ok", return_value=False), \
                patch.object(setup, "python_identity", side_effect=[{"ok": True}]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.ensure_python(self.options, {}), venv_python.absolute())
        steps = [argv[1:3] for argv, _ in calls]
        self.assertEqual(steps, [["python", "find"], ["python", "find"], ["python", "install"], ["python", "install"],
                                 ["python", "find"], ["venv", "--no-config"], ["pip", "install"]])
        self.assertEqual(calls[0][0][1:], ["python", "find", "--help"])
        # F2-F3: never a virtual environment (--system), wherever the installer runs.
        self.assertEqual(calls[1][0][1:], ["python", "find", "--no-config", "--system", "3.12"])
        self.assertEqual(calls[4][0][1:], ["python", "find", "--no-config", "--system", "3.12"])
        self.assertEqual(calls[2][0][1:], ["python", "install", "--help"])
        self.assertEqual(calls[3][0][1:], ["python", "install", "--no-config", "--no-bin", "3.12"])
        self.assertEqual(calls[3][1]["UV_PYTHON_INSTALL_DIR"], str(self.options.data_dir / "python-runtimes"))
        self.assertEqual(calls[3][1]["UV_PYTHON_BIN_DIR"], str(self.options.data_dir / "python-runtimes/bin"))
        self.assertIn(str(ROOT / "requirements.txt"), calls[-1][0])
        # --offline never lets uv download a Python.
        with patch.object(setup, "run", side_effect=setup.SetupError("none")):
            with self.assertRaisesRegex(setup.SetupError, "offline"):
                setup.uv_python(replace(self.options, offline=True), Path("/tools/uv"))

    FAKE_UV = textwrap.dedent("""\
        #!{python}
        # Stands in for uv's `python find` / `python install`: the install writes the
        # Python into UV_PYTHON_INSTALL_DIR and, as uv 0.8+ does unless --no-bin is
        # given, links pythonX.Y into the bin directory (UV_PYTHON_BIN_DIR, else
        # XDG_BIN_HOME, else ~/.local/bin).  `python find` prefers a virtual
        # environment unless --system is given.  OLD=1 is an old uv: no --no-bin
        # (older than 0.8), no executable outside the install directory, and no
        # `python find --system`.
        import os, sys
        from pathlib import Path
        old = {old}
        args = sys.argv[1:]
        home = Path(os.environ.get("UV_PYTHON_INSTALL_DIR", Path.home() / ".local/share/uv/python"))
        python = home / "cpython-3.12.12-linux-x86_64-gnu/bin/python3.12"
        Path(os.environ.get("UV_CACHE_DIR", Path.home() / ".cache/uv")).mkdir(parents=True, exist_ok=True)
        if args[:2] == ["python", "find"] and "--help" in args:
            print("Usage: uv python find [OPTIONS] [REQUEST]")
            if not old:
                print("      --system\\n          Only find system Python interpreters [env: UV_SYSTEM_PYTHON=]")
        elif args[:2] == ["python", "find"]:
            if old and "--system" in args:
                print("error: unexpected argument '--system' found", file=sys.stderr)
                sys.exit(2)
            if "--system" not in args:
                # As uv does: the active virtual environment first, then a
                # .venv in the working directory or any parent.
                venvs = [Path(os.environ["VIRTUAL_ENV"])] if os.environ.get("VIRTUAL_ENV") else []
                venvs += [folder / ".venv" for folder in (Path.cwd(), *Path.cwd().parents)]
                for venv in venvs:
                    if (venv / "pyvenv.cfg").is_file():
                        print(venv / "bin/python3")
                        sys.exit(0)
            if not python.exists():
                print("error: No interpreter found for Python 3.12", file=sys.stderr)
                sys.exit(2)
            print(python)
        elif args[:2] == ["python", "install"] and "--help" in args:
            print("Usage: uv python install [OPTIONS] [TARGETS]...")
            if not old:
                print("      --no-bin\\n          Do not install a Python executable into the `bin` directory")
        elif args[:2] == ["python", "install"]:
            if old and "--no-bin" in args:
                print("error: unexpected argument '--no-bin' found", file=sys.stderr)
                sys.exit(2)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("python")
            if not old and "--no-bin" not in args:
                bin_dir = Path(os.environ.get("UV_PYTHON_BIN_DIR") or os.environ.get("XDG_BIN_HOME")
                               or Path.home() / ".local/bin")
                bin_dir.mkdir(parents=True, exist_ok=True)
                (bin_dir / "python3.12").write_text("python")
        else:
            sys.exit(2)
        """)

    def test_a_python_from_uv_writes_nothing_outside_the_config_and_data_folders(self):
        home = self.base / "home"
        for old in (False, True):
            with self.subTest(old_uv=old):
                shutil.rmtree(self.options.data_dir, ignore_errors=True)
                shutil.rmtree(home, ignore_errors=True)
                home.mkdir()
                uv = self.base / ("uv-old" if old else "uv")
                uv.write_text(self.FAKE_UV.format(python=sys.executable, old=old))
                uv.chmod(0o755)
                environment = {key: value for key, value in os.environ.items()
                               if not key.startswith("UV_") and key != "XDG_BIN_HOME"}
                environment["HOME"] = str(home)
                with patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(io.StringIO()):
                    python = setup.uv_python(self.options, uv)
                self.assertTrue(python.is_file())
                self.assertTrue(python.is_relative_to(self.options.data_dir / "python-runtimes"), python)
                self.assertEqual(sorted(home.rglob("*")), [], "uv wrote under HOME, outside --data-dir")
                written = {path for path in self.base.rglob("*") if path.is_file()} - {self.base / "uv", self.base / "uv-old"}
                outside = [path for path in written if not (path.is_relative_to(self.options.data_dir)
                                                           or path.is_relative_to(self.options.config_dir))]
                self.assertEqual(outside, [])
        # A uv that links an executable despite --no-bin still keeps it in the data folder.
        self.assertEqual(setup.uv_environment(self.options)["UV_PYTHON_BIN_DIR"],
                         str(self.options.data_dir / "python-runtimes/bin"))

    def test_the_python_uv_finds_is_never_a_virtual_environment(self):
        # F2-F3: uv 0.10.2's `python find 3.12`, run from inside a project,
        # returned that project's .venv/bin/python3 (or the active VIRTUAL_ENV's),
        # so the venvs would have been built from it.
        project = self.base / "project"
        (project / ".venv/bin").mkdir(parents=True)
        (project / ".venv/pyvenv.cfg").write_text("version_info = 3.12.12\n")
        (project / ".venv/bin/python3").write_text("python")
        (project / "src/deeper").mkdir(parents=True)
        managed = self.options.data_dir / "python-runtimes/cpython-3.12.12-linux-x86_64-gnu/bin/python3.12"
        home = self.base / "home"
        previous = os.getcwd()
        for old in (False, True):
            uv = self.base / ("uv-old" if old else "uv")
            uv.write_text(self.FAKE_UV.format(python=sys.executable, old=old))
            uv.chmod(0o755)
            for how in ("working directory", "VIRTUAL_ENV"):
                for provisioned in (True, False):
                    with self.subTest(old_uv=old, venv=how, managed_python_present=provisioned):
                        shutil.rmtree(self.options.data_dir, ignore_errors=True)
                        shutil.rmtree(home, ignore_errors=True)
                        home.mkdir()
                        if provisioned:
                            managed.parent.mkdir(parents=True)
                            managed.write_text("python")
                        environment = {key: value for key, value in os.environ.items()
                                       if not key.startswith("UV_") and key not in ("XDG_BIN_HOME", "VIRTUAL_ENV")}
                        environment["HOME"] = str(home)
                        if how == "VIRTUAL_ENV":
                            environment["VIRTUAL_ENV"] = str(project / ".venv")
                        os.chdir(project / "src/deeper" if how == "working directory" else self.base)
                        try:
                            with patch.dict(os.environ, environment, clear=True), \
                                    contextlib.redirect_stdout(io.StringIO()):
                                python = setup.uv_python(self.options, uv)
                        finally:
                            os.chdir(previous)
                        self.assertEqual(python, managed)

    def test_without_uv_the_paddle_venv_needs_a_python_3_12(self):
        with patch.object(setup.shutil, "which", return_value=None):
            with self.assertRaisesRegex(setup.SetupError, "uv"):
                setup.paddle_venv_python(self.options, None)


def probe(distribution="paddlepaddle-gpu", cuda="13.0", archs=(75, 80, 86, 90, 100, 103, 120), **versions):
    dists = {distribution: {"version": versions.get("paddle", "3.3.1"), "cuda": cuda, "archs": list(archs)},
             "paddleocr": {"version": versions.get("paddleocr", "3.7.0")},
             "paddlex": {"version": versions.get("paddlex", "3.7.2")}}
    return {"python": [3, 12, 12], "dists": dists}


class PaddleEnvironmentTests(Case):
    def plan(self, card=None):
        return setup.paddle_plan(LOCK, system="Linux", machine="x86_64", request="auto",
                                 gpus=[card] if card else [])[0]

    def test_a_matching_existing_environment_is_reused_and_never_modified(self):
        existing = self.base / "venvs/paddleocr/bin/python"
        existing.parent.mkdir(parents=True)
        existing.write_text("python")
        with patch.object(setup, "paddle_probe", return_value=probe()), patch.object(setup, "run") as runner:
            python, _ = setup.ensure_paddle_python(self.options, {"PADDLEOCR_PYTHON": str(existing)}, LOCK,
                                                   self.plan(gpu()), gpu(), self.base / "state.json", {})
        self.assertEqual(python, existing)
        runner.assert_not_called()

    def test_mismatches_are_named(self):
        plan = self.plan(gpu())
        self.assertIsNone(setup.paddle_mismatch(probe(), plan, LOCK, gpu()))
        self.assertIn("not paddlepaddle-gpu", setup.paddle_mismatch(probe(distribution="paddlepaddle"), plan, LOCK, gpu()))
        self.assertIn("paddlepaddle-gpu==3.3.1", setup.paddle_mismatch(probe(paddle="3.2.1"), plan, LOCK, gpu()))
        self.assertIn("paddleocr==3.7.0", setup.paddle_mismatch(probe(paddleocr="3.6.0"), plan, LOCK, gpu()))
        self.assertIn("driver", setup.paddle_mismatch(probe(), plan, LOCK, gpu(driver="576.02")))
        self.assertIn("kernels", setup.paddle_mismatch(probe(archs=(80, 86, 90)), plan, LOCK, gpu()))
        self.assertIsNone(setup.paddle_mismatch(probe(cuda="12.6", archs=(80, 86)), plan, LOCK,
                                                gpu(compute_capability="8.9", total_mib=24564)))
        self.assertIn("paddlepaddle-gpu", setup.paddle_mismatch(probe(), self.plan(), LOCK, None))

    def test_managed_environment_installs_verified_wheels_and_trusts_the_gpu_wheel_on_first_use(self):
        content = {"paddle": b"gpu wheel bytes" * 10, "paddlex": b"paddlex wheel", "paddleocr": b"paddleocr wheel"}
        lock = json.loads(json.dumps(LOCK))
        state = self.options.config_dir / "setup-state.json"
        with FileServer({"/gpu.whl": content["paddle"], "/paddlex.whl": content["paddlex"],
                         "/paddleocr.whl": content["paddleocr"]}) as server:
            for package in lock["paddle"]["packages"]:
                key = package["distribution"]
                package.update(url=server.base + f"/{key}.whl", size=len(content[key]),
                               sha256=hashlib.sha256(content[key]).hexdigest())
            plan = self.plan(gpu())
            plan = replace(plan, wheel=dict(plan.wheel, url=server.base + "/gpu.whl", size=len(content["paddle"])))
            managed = self.options.data_dir / "paddle-python/bin/python"
            calls = []
            installed = {"done": False}

            def fake_run(command, **kwargs):
                argv = [str(item) for item in command]
                calls.append(argv)
                if argv[1] == "venv":
                    managed.parent.mkdir(parents=True)
                    managed.write_text("python")
                if argv[1:3] == ["pip", "install"]:
                    installed["done"] = True
                return type("Result", (), {"stdout": ""})()

            def fake_probe(python):
                return probe() if installed["done"] else {"python": [3, 12, 12], "dists": {"paddlepaddle": {"version": "3.3.1"}}}
            # The managed venv runs on 3.12 and holds the CPU Paddle: repaired in place.
            managed.parent.mkdir(parents=True)
            managed.write_text("python")
            with patch.object(setup, "find_uv", return_value=Path("/tools/uv")), \
                    patch.object(setup, "uv_python", return_value=Path("/py/3.12")), \
                    patch.object(setup, "run", side_effect=fake_run), patch.object(setup, "paddle_probe", side_effect=fake_probe), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                python, _ = setup.ensure_paddle_python(self.options, {}, lock, plan, gpu(), state, {})
            self.assertEqual(python, managed.absolute())
            self.assertFalse(any(argv[1] == "venv" for argv in calls))
            self.assertIn("Trust on first use", out.getvalue())
            trusted = json.loads(state.read_text())["trustOnFirstUse"]
            self.assertEqual(trusted["cu130/" + plan.wheel["name"]]["sha256"], hashlib.sha256(content["paddle"]).hexdigest())
            install_call = next(argv for argv in calls if argv[1:3] == ["pip", "install"])
            # CD-R10: everything else the wheels pull in is pinned, with hashes uv
            # checks; E9-R1: the cu130 wheel's own CUDA pins come after the common ones.
            first = install_call.index("-c")
            self.assertEqual(install_call[first:first + 4],
                             ["-c", str(ROOT / setup.PADDLE_CONSTRAINTS),
                              "-c", str(ROOT / "runtime/paddle-constraints-cu130-x86_64.txt")])
            references = install_call[first + 4:]
            self.assertEqual([reference.split(" @ ")[0] for reference in references],
                             ["paddlepaddle-gpu", "paddlex", "paddleocr[doc-parser]"])
            self.assertTrue(all(" @ file://" in reference for reference in references))
            # A CPU Paddle already there is removed first: both install `paddle`.
            self.assertIn(["/tools/uv", "pip", "uninstall", "--no-config", "--python", str(managed), "paddlepaddle"], calls)
            self.assertEqual(list((self.options.data_dir / "downloads").rglob("*.whl")), [])
            # A later download of a different wheel under the same name is refused.
            server.files["/gpu.whl"] = b"GPU WHEEL BYTES" * 10
            with self.assertRaisesRegex(setup.SetupError, "trust on first use"):
                setup.fetch_wheel(self.options, plan.wheel, trusted, state)
            server.files["/gpu.whl"] = content["paddle"]
            self.assertTrue(setup.fetch_wheel(self.options, plan.wheel, trusted, state).is_file())

    def test_a_managed_venv_on_another_python_is_set_aside_and_rebuilt(self):
        managed = self.options.data_dir / "paddle-python/bin/python"
        managed.parent.mkdir(parents=True)
        managed.write_text("python 3.14")
        calls = []

        def fake_run(command, **kwargs):
            argv = [str(item) for item in command]
            calls.append(argv)
            if argv[1] == "venv":
                managed.parent.mkdir(parents=True)
                managed.write_text("python 3.12")
            return type("Result", (), {"stdout": ""})()
        probes = [{"python": [3, 14, 4], "dists": {}}, probe()]
        with patch.object(setup, "find_uv", return_value=Path("/tools/uv")), \
                patch.object(setup, "uv_python", return_value=Path("/py/3.12")), patch.object(setup, "fetch_wheel",
                side_effect=lambda options, wheel, trusted, state: self.base / wheel["name"]), \
                patch.object(setup, "run", side_effect=fake_run), patch.object(setup, "paddle_probe", side_effect=probes):
            setup.ensure_paddle_python(self.options, {}, LOCK, self.plan(gpu()), gpu(), self.base / "state.json", {})
        self.assertEqual([argv[1:3] for argv in calls], [["venv", "--no-config"], ["pip", "install"]])
        aside = list(self.options.data_dir.glob("paddle-python-incomplete-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "bin/python").read_text(), "python 3.14")

    def test_weights_come_from_a_verified_cache_or_the_pinned_revision(self):
        files = {"inference.yml": b"model_name: PP-DocLayoutV3\n", "inference.pdiparams": b"weights"}
        lock = {"paddle": {"models": [{"role": "layout", "repo": "PaddlePaddle/PP-DocLayoutV3", "name": "PP-DocLayoutV3",
                                       "revision": "7b48a7566925fa464281f930c58eee04fe2c862a",
                                       "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                                 for name, data in files.items()]}]}}
        cache = self.base / "paddlex/official_models/PP-DocLayoutV3"
        cache.mkdir(parents=True)
        (cache / "inference.pdiparams").write_bytes(files["inference.pdiparams"])
        (cache / "inference.yml").write_bytes(b"model_name: something else\n")
        revision = "7b48a7566925fa464281f930c58eee04fe2c862a"
        with FileServer({f"/PaddlePaddle/PP-DocLayoutV3/resolve/{revision}/inference.yml": files["inference.yml"]}) as server, \
                patch.dict(os.environ, {"PADDLE_PDX_CACHE_HOME": str(self.base / "paddlex")}):
            original = setup.download_asset

            def local_download(url, target, asset, *, offline=False):
                return original(url.replace("https://huggingface.co", server.base), target, asset, offline=offline)
            with patch.object(setup, "download_asset", side_effect=local_download):
                found = setup.ensure_paddle_weights(self.options, {}, lock)
            self.assertEqual(server.requests, [f"/PaddlePaddle/PP-DocLayoutV3/resolve/{revision}/inference.yml"])
        target = Path(found["PADDLE_LAYOUT_MODEL_DIR"])
        self.assertEqual(target, (self.options.data_dir / "paddle-models/PP-DocLayoutV3" / revision).absolute())
        self.assertEqual((target / "inference.pdiparams").read_bytes(), files["inference.pdiparams"])
        self.assertEqual((cache / "inference.yml").read_bytes(), b"model_name: something else\n")
        # A configured folder that verifies is kept; nothing is fetched.
        with patch.object(setup, "download_asset") as download:
            self.assertEqual(setup.ensure_paddle_weights(self.options, {"PADDLE_LAYOUT_MODEL_DIR": str(target)}, lock),
                             {"PADDLE_LAYOUT_MODEL_DIR": str(target)})
            download.assert_not_called()


def release_archive(path: Path, top: str, files: dict[str, bytes], links: dict[str, str] = {}) -> dict:
    with tarfile.open(path, "w:gz") as tar:
        folder = tarfile.TarInfo(top)
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(folder)
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        for name, target in links.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    data = path.read_bytes()
    return {"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class RuntimeTests(Case):
    def test_release_binary_and_its_cuda_runtime_are_verified_extracted_and_sealed(self):
        archives = self.base / "archives"
        archives.mkdir()
        server_script = b"#!/bin/sh\nprintf 'version: 10969 (391fac164)\\n'\n"
        binary = release_archive(archives / "llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz", "llama-b10969",
                                 {"llama-server": server_script, "libggml-base.so.0.24.0": b"ggml"},
                                 {"libggml-base.so.0": "libggml-base.so.0.24.0"})
        bundle = release_archive(archives / "cudart-llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz",
                                 "cudart-llama-b10969-bin-ubuntu-cuda-13.3-x64", {"libcublasLt.so.13": b"cublas"})
        asset = dict(binary, system="Linux", machine="x86_64", device="cuda", min_driver="580", runtime_bundle=bundle)
        lock = json.loads(json.dumps(LOCK))
        with FileServer({"/" + item["name"]: (archives / item["name"]).read_bytes() for item in (binary, bundle)}) as server:
            lock["runtime"]["release_url"] = server.base + "/"
            path, identity = setup.ensure_runtime(self.options, {}, lock, "cuda", release=asset)
            self.assertEqual(len(server.requests), 2)
            self.assertEqual(identity["verification"], "sealed-release-binary")
            self.assertEqual(identity["compiled_device"], "cuda")
            self.assertEqual(set(identity["libraries"]), {"libggml-base.so.0", "libggml-base.so.0.24.0", "libcublasLt.so.13"})
            self.assertTrue((path.parent / "libggml-base.so.0").is_symlink())
            self.assertEqual(list((self.options.data_dir / "downloads").rglob("*.tar.gz")), [])
            # Reused on the next run, with no download and no build tools.
            with patch.object(setup, "ensure_dependencies") as dependencies:
                again, _ = setup.ensure_runtime(self.options, {"HUNYUAN_LLAMA_SERVER": str(path)}, lock, "cuda", release=asset)
            self.assertEqual((again, len(server.requests)), (path, 2))
            dependencies.assert_not_called()

    def test_a_release_binary_this_system_cannot_run_falls_back_to_a_source_build(self):
        # b10969's Ubuntu CUDA builds need glibc 2.38; Ubuntu 22.04 (M1's nodes) has 2.35.
        archives = self.base / "archives"
        archives.mkdir()
        loader = (b"#!/bin/sh\necho \"llama-server: /lib/x86_64-linux-gnu/libc.so.6: version \\`GLIBC_2.38' not found "
                  b"(required by llama-server)\" >&2\nexit 1\n")
        binary = release_archive(archives / "llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz", "llama-b10969",
                                 {"llama-server": loader}, {})
        bundle = release_archive(archives / "cudart-llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz",
                                 "cudart-llama-b10969-bin-ubuntu-cuda-12.8-x64", {"libcublasLt.so.12": b"cublas"})
        asset = dict(binary, system="Linux", machine="x86_64", device="cuda", min_driver="570.26", runtime_bundle=bundle)
        lock = json.loads(json.dumps(LOCK))
        out = io.StringIO()
        with FileServer({"/" + item["name"]: (archives / item["name"]).read_bytes() for item in (binary, bundle)}) as server:
            lock["runtime"]["release_url"] = server.base + "/"
            with patch.object(setup, "nvcc_path", return_value=None), contextlib.redirect_stdout(out):
                # No toolkit here: the source build it falls back to says so.
                with self.assertRaisesRegex(setup.SetupError, "nvcc"):
                    setup.ensure_runtime(self.options, {}, lock, "cuda", release=asset)
        self.assertIn("does not run on this system (needs GLIBC_2.38)", out.getvalue())

    def test_a_release_binary_that_runs_but_fails_verification_still_stops_setup(self):
        archives = self.base / "archives"
        archives.mkdir()
        binary = release_archive(archives / "llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz", "llama-b10969",
                                 {"llama-server": b"#!/bin/sh\nprintf 'version: 1 (0000000)\\n'\n"}, {})
        bundle = release_archive(archives / "cudart-llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz",
                                 "cudart-llama-b10969-bin-ubuntu-cuda-12.8-x64", {"libcublasLt.so.12": b"cublas"})
        asset = dict(binary, system="Linux", machine="x86_64", device="cuda", min_driver="570.26", runtime_bundle=bundle)
        lock = json.loads(json.dumps(LOCK))
        with FileServer({"/" + item["name"]: (archives / item["name"]).read_bytes() for item in (binary, bundle)}) as server:
            lock["runtime"]["release_url"] = server.base + "/"
            with patch.object(setup, "nvcc_path") as nvcc:
                with self.assertRaisesRegex(setup.SetupError, "pinned llama.cpp commit"):
                    setup.ensure_runtime(self.options, {}, lock, "cuda", release=asset)
            nvcc.assert_not_called()

    def test_unrunnable_reason_reads_the_loader_messages(self):
        self.assertEqual(setup.unrunnable_reason("x: /lib/libm.so.6: version `GLIBC_2.38' not found (required by y)\n"
                                                 "x: /lib/libstdc++.so.6: version `GLIBCXX_3.4.32' not found"),
                         "needs GLIBCXX_3.4.32, GLIBC_2.38")
        self.assertEqual(setup.unrunnable_reason("error while loading shared libraries: libcuda.so.1: cannot open shared "
                                                 "object file: No such file or directory"), "needs libcuda.so.1")
        self.assertIsNone(setup.unrunnable_reason("Existing runtime does not report the pinned llama.cpp commit."))

    def test_archive_members_cannot_escape(self):
        for files, links in (({"../evil": b"x"}, {}), ({}, {"lib.so": "../../etc/passwd"}), ({}, {"lib.so": "/etc/passwd"})):
            with self.subTest(files=files, links=links):
                path = self.base / "bad.tar.gz"
                release_archive(path, "llama-b10969", {"llama-server": b"x", **files}, links)
                with self.assertRaisesRegex(setup.SetupError, "escapes"):
                    setup.extract_archive(path, self.base / "out")

    def test_an_interrupted_gpu_runtime_download_stops_setup_instead_of_moving_to_cpu(self):
        options = replace(self.options, engine="hunyuan")
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "machine_name", return_value="x86_64"), \
                patch.object(setup, "nvidia_gpus", return_value=[gpu()]), \
                patch.object(setup, "check_free_disk"), patch.object(setup, "ensure_dependencies"), \
                patch.object(setup, "ensure_python", return_value=Path("/data/python/bin/python")), \
                patch.object(setup, "ensure_models", return_value={}), \
                patch.object(setup, "ensure_runtime", side_effect=setup.DownloadInterrupted("Download interrupted")) as runtime, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(setup.DownloadInterrupted):
                setup.setup(options)
        self.assertEqual(runtime.call_count, 1)

    def test_cuda_without_a_release_build_or_toolkit_says_why(self):
        with patch.object(setup, "runtime_identity", side_effect=setup.SetupError("absent")), \
                patch.object(setup, "nvcc_path", return_value=None), patch.object(setup, "ensure_dependencies") as dependencies:
            with self.assertRaisesRegex(setup.SetupError, "nvcc"):
                setup.ensure_runtime(self.options, {}, LOCK, "cuda", release=None)
        dependencies.assert_not_called()


def write_runner_outputs(directory: Path) -> None:
    """What run_hunyuanocr.py / run_paddleocr_vl.py leave for one smoke page."""
    render = directory / "renders/page-0001.png"
    output = directory / "ocr"
    output.mkdir(parents=True, exist_ok=True)
    text = output / "page-0001.txt"
    text.write_text("OCR SETUP TEST 12345\n")
    setup.atomic_json(output / "page-0001.json", {"status": "complete", "provenance": {
        "textSha256": setup.sha256_file(text), "renderSha256": setup.sha256_file(render)}})
    setup.atomic_json(directory / "run-report.json", {"complete": True})


class SmokeTests(Case):
    def setUp(self):
        super().setUp()
        # These fixtures emulate an NVIDIA host, independently of the test host.
        patcher = patch.object(setup, "platform_name", return_value="Linux")
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, **extra):
        return {"JYUT_OCR_PYTHON": sys.executable, "HUNYUAN_MODEL": "/m", "HUNYUAN_MMPROJ": "/p",
                "HUNYUAN_LLAMA_SERVER": "/s", "OCR_HUNYUAN_SLOTS": "16", "PADDLEOCR_PYTHON": "/paddle/python",
                "OCR_PADDLE_DEVICE": "gpu:0", **extra}

    def fake_render(self, command, **kwargs):
        argv = [str(item) for item in command]
        if argv[1] == "-c":
            Path(argv[-1]).write_bytes(b"synthetic page")
        return type("Result", (), {"stdout": ""})()

    def test_hunyuan_smoke_uses_the_configured_slots_and_a_bounded_queue_wait(self):
        commands = []

        def fake_run(command, **kwargs):
            argv = [str(item) for item in command]
            if argv[0] == "nvidia-smi":
                return type("Result", (), {"stdout": "31000\n"})()
            if argv[1] == "-c":
                return self.fake_render(command)
            commands.append(argv)
            write_runner_outputs(Path(argv[argv.index("--report") + 1]).parent)
            return type("Result", (), {"stdout": ""})()
        with patch.object(setup, "run", side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
            records = setup.run_smokes(self.options, self.config(), ("hunyuan", "paddle"),
                                       {"hunyuan": "cuda", "paddle": "gpu:0"}, "a" * 64)
        self.assertEqual(set(records), {"hunyuan", "paddle"})
        self.assertTrue(all(setup.check_smoke(record) for record in records.values()))
        hunyuan, paddle = commands
        self.assertEqual(hunyuan[hunyuan.index("--server-slots") + 1], "16")
        self.assertNotIn("--ctx-size", hunyuan)
        self.assertEqual(hunyuan[hunyuan.index("--gpu-queue-timeout") + 1], str(setup.SMOKE_QUEUE_TIMEOUT))
        self.assertTrue(paddle[1].endswith("run_paddleocr_vl.py"))
        self.assertEqual(paddle[paddle.index("--python") + 1], "/paddle/python")
        self.assertEqual(setup.hunyuan_need_mib(16), 28076)
        self.assertEqual(setup.hunyuan_need_mib(1), 5036)

    def test_a_busy_gpu_without_a_hand_over_keeps_the_gpu_and_never_suggests_cpu(self):
        result = type("Result", (), {"stdout": "1182\n"})()
        with patch.object(setup, "run", return_value=result):
            with self.assertRaises(setup.SetupError) as raised:
                setup.run_smokes(self.options, self.config(), ("hunyuan",), {"hunyuan": "cuda"}, "a" * 64)
        message = str(raised.exception)
        self.assertIn("GPU is busy", message)
        self.assertIn("28076", message)
        self.assertNotIn("--device cpu", message)
        self.assertIn("--local-model-release-cmd", message)

    def test_a_queue_deferral_is_reported_as_a_busy_queue(self):
        def fake_run(command, **kwargs):
            if str(command[1]) == "-c":
                return self.fake_render(command)
            error = setup.SetupError("Command failed (75)")
            error.returncode = 75
            raise error
        with patch.object(setup, "run", side_effect=fake_run), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(setup.SetupError, "GPU queue"):
                setup.smoke_test(self.options, self.config(), "hunyuan", "cpu", "a" * 64)

    def test_a_gpu_held_by_the_local_model_runs_both_smokes_in_one_hand_over(self):
        config = self.config(OCR_PHASE_RELEASE_CMD="stop-llm", OCR_PHASE_RESTORE_CMD="start-llm",
                             OCR_PHASE_MAX_SECONDS="540")
        seen = {}

        def fake_status(command, *, env, timeout):
            argv = [str(item) for item in command]
            seen.update(argv=argv, env=env, timeout=timeout)
            plan = json.loads(Path(argv[-1]).read_text())
            for runner in plan["commands"]:
                write_runner_outputs(Path(runner[runner.index("--report") + 1]).parent)
            return 0
        with patch.object(setup, "run", side_effect=lambda command, **kwargs: type("Result", (), {"stdout": "1182\n"})()
                          if str(command[0]) == "nvidia-smi" else self.fake_render(command)), \
                patch.object(setup, "run_status", side_effect=fake_status), contextlib.redirect_stdout(io.StringIO()):
            records = setup.run_smokes(self.options, config, ("hunyuan", "paddle"),
                                       {"hunyuan": "cuda", "paddle": "gpu:0"}, "b" * 64)
        self.assertEqual(set(records), {"hunyuan", "paddle"})
        argv = seen["argv"]
        self.assertTrue(argv[1].endswith("scripts/run_ocr_phase.py"))
        # No start time: the whole budget (CD-R12 gives the wrapper what is left).
        self.assertEqual(argv[2:5], ["--max-seconds", "540", "--"])
        self.assertEqual(argv[6:8], [str(ROOT / "scripts/_setup_ocr.py"), "--run-smoke-plan"])
        self.assertEqual(seen["env"]["OCR_PHASE_RELEASE_CMD"], "stop-llm")
        self.assertEqual(seen["env"]["OCR_PHASE_REQUIRED_MIB"], "28076")
        self.assertEqual(seen["timeout"], 840)

    def test_the_hand_over_smoke_gets_what_is_left_of_the_call_budget(self):
        # CD-R12: the agent's one foreground call has already spent the
        # installer's own time when the hand-over starts.
        config = self.config(OCR_PHASE_RELEASE_CMD="stop-llm", OCR_PHASE_RESTORE_CMD="start-llm",
                             OCR_PHASE_MAX_SECONDS="540")
        seen = []

        def fake_status(command, *, env, timeout):
            argv = [str(item) for item in command]
            seen.append((argv, timeout))
            plan = json.loads(Path(argv[-1]).read_text())
            for runner in plan["commands"]:
                write_runner_outputs(Path(runner[runner.index("--report") + 1]).parent)
            return 0

        def smokes(started_at, config=config):
            options = replace(self.options, started_at=started_at)
            with patch.object(setup, "run", side_effect=lambda command, **kwargs: type("Result", (), {"stdout": "1182\n"})()
                              if str(command[0]) == "nvidia-smi" else self.fake_render(command)), \
                    patch.object(setup, "run_status", side_effect=fake_status), contextlib.redirect_stdout(io.StringIO()):
                return setup.run_smokes(options, config, ("hunyuan", "paddle"), {"hunyuan": "cuda", "paddle": "gpu:0"},
                                        "d" * 64)
        smokes(setup.time.time() - 100)
        argv, timeout = seen[-1]
        left = int(argv[argv.index("--max-seconds") + 1])
        self.assertIn(left, (439, 440))
        self.assertEqual(argv[argv.index("--max-seconds") + 2], "--")
        self.assertEqual(timeout, left + 300)
        # Less than a test window left: nothing is started, nothing stopped.
        with self.assertRaisesRegex(setup.SetupError, r"already run 40\d s of OCR_PHASE_MAX_SECONDS") as raised:
            smokes(setup.time.time() - 400)
        self.assertIn("Nothing was stopped", str(raised.exception))
        self.assertEqual(len(seen), 1)
        # A small budget is held to half of itself; no budget (an unlimited shell) passes none.
        smokes(setup.time.time() - 30, dict(config, OCR_PHASE_MAX_SECONDS="200"))
        self.assertIn(seen[-1][0][seen[-1][0].index("--max-seconds") + 1], ("169", "170"))
        smokes(setup.time.time() - 1000, dict(config, OCR_PHASE_MAX_SECONDS="0"))
        self.assertNotIn("--max-seconds", seen[-1][0])
        self.assertIsNone(seen[-1][1])

    def test_hand_over_exit_codes_are_explained(self):
        config = self.config(OCR_PHASE_RELEASE_CMD="stop-llm", OCR_PHASE_RESTORE_CMD="start-llm")
        for code, text in ((76, "busy with another job"), (75, "time budget"), (3, "NOT restored"), (2, "OCR_PHASE_MAX_SECONDS")):
            with self.subTest(code=code), \
                    patch.object(setup, "run", side_effect=lambda command, **kwargs: type("Result", (), {"stdout": "100\n"})()
                                 if str(command[0]) == "nvidia-smi" else self.fake_render(command)), \
                    patch.object(setup, "run_status", return_value=code), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(setup.SetupError, text):
                    setup.run_smokes(self.options, config, ("hunyuan",), {"hunyuan": "cuda"}, f"{code:064d}")

    def test_the_smoke_plan_runs_in_order_and_stops_at_the_first_failure(self):
        log = self.base / "log"
        plan = self.base / "plan.json"
        step = lambda text, code: [sys.executable, "-c", f"open({str(log)!r}, 'a').write({text!r}); raise SystemExit({code})"]
        setup.atomic_json(plan, {"commands": [step("a", 0), step("b", 75), step("c", 0)]})
        self.assertEqual(setup.run_smoke_plan(plan), 75)
        self.assertEqual(log.read_text(), "ab")

    def test_hand_over_through_the_real_wrapper_with_fake_commands(self):
        """scripts/run_ocr_phase.py's CLI (options, --, runner) with touch-file
        release/restore commands, a fake nvidia-smi and a fake health server."""
        fakebin = self.base / "bin"
        fakebin.mkdir()
        released, restored = self.base / "released", self.base / "restored"
        # The card is full until the release has run, and free after it (the
        # wrapper runs no OCR into a GPU that is still full after the release).
        (fakebin / "nvidia-smi").write_text(textwrap.dedent(f"""\
            #!/bin/sh
            if [ -e '{released}' ] && [ ! -e '{restored}' ]; then used=1000; else used=31000; fi
            case "$*" in
              *memory.used,memory.total*) echo "$used, 32607" ;;
              *memory.free*) echo "$((32607 - used))" ;;
              *compute-apps*) ;;
              *) echo "No devices were found"; exit 6 ;;
            esac
            """))
        (fakebin / "nvidia-smi").chmod(0o755)
        runner = self.base / "fake_runner.py"
        runner.write_text(textwrap.dedent("""\
            import json, os, sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            import _setup_ocr as setup
            report = Path(sys.argv[sys.argv.index("--report") + 1])
            render = report.parent / "renders/page-0001.png"
            output = report.parent / "ocr"
            output.mkdir(parents=True, exist_ok=True)
            text = output / "page-0001.txt"
            text.write_text("OCR SETUP TEST 12345\\n")
            setup.atomic_json(output / "page-0001.json", {"status": "complete", "provenance": {
                "textSha256": setup.sha256_file(text), "renderSha256": setup.sha256_file(render)}})
            setup.atomic_json(report, {"deadline": os.environ.get("OCR_PHASE_DEADLINE")})
            """))

        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = self.config(OCR_PHASE_RELEASE_CMD=f"touch {released}", OCR_PHASE_RESTORE_CMD=f"touch {restored}",
                             OCR_PHASE_HEALTH_URL=f"http://127.0.0.1:{server.server_port}/v1/models",
                             OCR_PHASE_MAX_SECONDS="300")
        original = setup.prepare_smoke

        def prepare(options, config, engine, device, fingerprint):
            # A render already there is kept, so the test needs no Pillow.
            render = setup.smoke_directory(options, fingerprint, engine) / "renders/page-0001.png"
            render.parent.mkdir(parents=True, exist_ok=True)
            render.write_bytes(b"synthetic page")
            directory, _ = original(options, config, engine, device, fingerprint)
            return directory, [sys.executable, runner, str(ROOT / "scripts"), "--report", directory / "run-report.json"]
        environment = {"PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}", "TMPDIR": str(self.base),
                       # The wrapper's state and the shared queue it leases stay in the test folder.
                       "OCR_PHASE_STATE_DIR": str(self.base / "handover"),
                       "OCR_GPU_QUEUE_DIR": str(self.base / "gpu-queue")}
        try:
            with patch.dict(os.environ, environment), patch.object(setup, "prepare_smoke", side_effect=prepare), \
                    contextlib.redirect_stdout(io.StringIO()):
                records = setup.run_smokes(self.options, config, ("hunyuan", "paddle"),
                                           {"hunyuan": "cuda", "paddle": "gpu:0"}, "c" * 64)
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(released.exists() and restored.exists())
        self.assertEqual(set(records), {"hunyuan", "paddle"})
        report = json.loads((setup.smoke_directory(self.options, "c" * 64, "hunyuan") / "run-report.json").read_text())
        self.assertTrue(report["deadline"])


class SetupFlowTests(Case):
    def first_setup(self, card, smokes):
        """A setup with no saved slot count on a Linux machine with CARD; returns (result or the
        error raised, the settings written, what was printed)."""
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "machine_name", return_value="x86_64"), \
                patch.object(setup, "nvidia_gpus", return_value=[card]), \
                patch.object(setup, "check_free_disk"), patch.object(setup, "ensure_dependencies"), \
                patch.object(setup, "ensure_python", return_value=Path("/data/python/bin/python")), \
                patch.object(setup, "ensure_models", return_value={"HUNYUAN_MODEL": "/m", "HUNYUAN_MMPROJ": "/p"}), \
                patch.object(setup, "ensure_runtime", return_value=(Path("/data/runtime/llama-server"), {})), \
                patch.object(setup, "environment_identity",
                             side_effect=lambda options, config, *rest: {"slots": config["OCR_HUNYUAN_SLOTS"]}), \
                patch.object(setup, "run_smokes", side_effect=smokes), contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                result = setup.setup(replace(self.options, engine="hunyuan"))
            except setup.SetupError as error:
                result = error
        return result, setup.read_environment(self.options.config_dir / "env.sh"), out.getvalue()

    def test_a_first_setup_gives_hunyuan_the_slots_the_card_holds(self):
        for mib, expected in ((8 * 1024, 1), (12 * 1024, 4), (16 * 1024, 7), (24 * 1024, 12),
                              (32607, 16), (48 * 1024, 16), (80 * 1024, 16)):
            with self.subTest(mib=mib):
                self.assertEqual(setup.hunyuan_slots_for(gpu(total_mib=mib), "cuda"), expected)
                # What is chosen fits beside the reserve.
                self.assertLessEqual(setup.hunyuan_need_mib(expected) + setup.HUNYUAN_CARD_RESERVE_MIB,
                                     max(mib, setup.hunyuan_need_mib(1) + setup.HUNYUAN_CARD_RESERVE_MIB))
        self.assertEqual(setup.hunyuan_slots_for(gpu(total_mib=None), "cuda"), 1)
        self.assertEqual(setup.hunyuan_slots_for(None, "cuda"), 1)
        self.assertEqual(setup.hunyuan_slots_for(gpu(), "cpu"), 1)
        self.assertEqual(setup.hunyuan_slots_for(gpu(), "metal"), 1)
        seen = []

        def smokes(options, config, engines, devices, fingerprint, paddle_need):
            seen.append(config["OCR_HUNYUAN_SLOTS"])
            return {engine: {"json_output": engine + ".json"} for engine in engines}
        result, written, printed = self.first_setup(gpu(total_mib=24 * 1024), smokes)
        self.assertEqual(result["status"], "ready")
        self.assertEqual((written["OCR_HUNYUAN_SLOTS"], seen), ("12", ["12"]))
        self.assertIn("HunyuanOCR reads 12 pages at once on this card", printed)

    def test_slots_the_setup_test_does_not_pass_with_fall_back_to_one(self):
        seen = []

        def smokes(options, config, engines, devices, fingerprint, paddle_need):
            seen.append((config["OCR_HUNYUAN_SLOTS"], fingerprint))
            if config["OCR_HUNYUAN_SLOTS"] != "1":
                raise setup.SetupError("the test page was not read")
            return {engine: {"json_output": engine + ".json"} for engine in engines}
        result, written, printed = self.first_setup(gpu(), smokes)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([slots for slots, _ in seen], ["16", "1"])
        # The second test is of the settings as they now are, not of the first try's.
        self.assertNotEqual(seen[0][1], seen[1][1])
        self.assertEqual(written["OCR_HUNYUAN_SLOTS"], "1")
        self.assertIn("did not pass with 16 HunyuanOCR slots (the test page was not read); trying one slot", printed)
        state = json.loads((self.options.config_dir / "setup-state.json").read_text())
        self.assertEqual((state["status"], state["identity"]), ("ready", {"slots": "1"}))

    def test_a_saved_slot_count_is_never_changed_or_tried_again_with_one(self):
        self.options.config_dir.mkdir()
        (self.options.config_dir / "env.sh").write_text("export OCR_HUNYUAN_SLOTS=4\n")
        seen = []

        def smokes(options, config, engines, devices, fingerprint, paddle_need):
            seen.append(config["OCR_HUNYUAN_SLOTS"])
            raise setup.SetupError("the test page was not read")
        result, written, _ = self.first_setup(gpu(), smokes)
        self.assertIsInstance(result, setup.SetupError)
        self.assertEqual((seen, written["OCR_HUNYUAN_SLOTS"]), (["4"], "4"))
        state = json.loads((self.options.config_dir / "setup-state.json").read_text())
        self.assertEqual(state["status"], "action-required")

    def test_linux_gpu_installs_both_engines_and_keeps_the_old_skill_lines(self):
        self.options.config_dir.mkdir()
        env = self.options.config_dir / "env.sh"
        original = ('export PADDLEOCR_PYTHON="$HOME/.venvs/paddleocr/bin/python"\n'
                    'export OCR_PHASE_RELEASE_CMD="stop $LLM_UNIT"\nexport OCR_HUNYUAN_SLOTS=16\n'
                    'export JYUT_OCR_ENGINE=hunyuan\nexport OCR_HUNYUAN_DEVICE=cpu\n')
        env.write_text(original)
        calls = {}

        def runtime(options, existing, lock, device, *, release=None):
            calls["runtime"] = (device, release and release["name"])
            return Path("/data/runtime/llama-server"), {}

        def paddle(options, existing, lock, plan, card, state, trusted):
            calls["paddle"] = (plan.device, plan.index, existing.get("PADDLEOCR_PYTHON"))
            return Path("/data/paddle-python/bin/python"), {}

        def smokes(options, config, engines, devices, fingerprint, paddle_need):
            calls["smokes"] = (engines, dict(devices))
            return {engine: {"json_output": engine + ".json"} for engine in engines}
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "machine_name", return_value="x86_64"), \
                patch.object(setup, "nvidia_gpus", return_value=[gpu()]), \
                patch.object(setup, "check_free_disk"), patch.object(setup, "ensure_dependencies"), \
                patch.object(setup, "ensure_python", return_value=Path("/data/python/bin/python")), \
                patch.object(setup, "ensure_models", return_value={"HUNYUAN_MODEL": "/m", "HUNYUAN_MMPROJ": "/p"}), \
                patch.object(setup, "ensure_runtime", side_effect=runtime), \
                patch.object(setup, "ensure_paddle_weights", return_value={"PADDLE_VL_MODEL_DIR": "/w/vl", "PADDLE_LAYOUT_MODEL_DIR": "/w/layout"}), \
                patch.object(setup, "ensure_paddle_python", side_effect=paddle), \
                patch.object(setup, "environment_identity", return_value={"id": 1}), \
                patch.object(setup, "run_smokes", side_effect=smokes), contextlib.redirect_stdout(io.StringIO()) as out:
            result = setup.setup(replace(self.options, engine="hunyuan-paddle"))
        self.assertEqual((result["status"], result["engines"]), ("ready", ["hunyuan", "paddle"]))
        self.assertEqual(result["devices"], {"hunyuan": "cuda", "paddle": "gpu:0"})
        self.assertEqual(calls["runtime"], ("cuda", "llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz"))
        self.assertEqual(calls["paddle"], ("gpu:0", "cu130", str(Path.home() / ".venvs/paddleocr/bin/python")))
        self.assertIn("older installer", out.getvalue())
        text = env.read_text()
        self.assertIn('export OCR_PHASE_RELEASE_CMD="stop $LLM_UNIT"\n', text)
        self.assertIn("export OCR_HUNYUAN_SLOTS=16\n", text)
        values = setup.read_environment(env)
        for key, value in {"JYUT_OCR_ENGINE": "hunyuan-paddle", "JYUT_OCR_ENGINES": "hunyuan,paddle", "JYUT_OCR_DEVICE": "auto",
                           "OCR_HUNYUAN_DEVICE": "cuda", "OCR_PADDLE_DEVICE": "gpu:0",
                           "PADDLEOCR_PYTHON": "/data/paddle-python/bin/python",
                           "PADDLE_VL_MODEL_DIR": "/w/vl", "PADDLE_LAYOUT_MODEL_DIR": "/w/layout"}.items():
            self.assertEqual(values[key], value, key)
        state = json.loads((self.options.config_dir / "setup-state.json").read_text())
        self.assertEqual(set(state["smoke"]), {"hunyuan", "paddle"})

    def test_an_automatic_cpu_fallback_is_not_saved_as_a_choice(self):
        def runtime(options, existing, lock, device, *, release=None):
            if device == "cuda":
                raise setup.SetupError("no CUDA runtime for this driver")
            return Path("/data/cpu/llama-server"), {}
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "machine_name", return_value="x86_64"), \
                patch.object(setup, "nvidia_gpus", return_value=[gpu(driver="550.54.14", compute_capability="8.6", total_mib=8192)]), \
                patch.object(setup, "check_free_disk"), patch.object(setup, "ensure_dependencies"), \
                patch.object(setup, "ensure_python", return_value=Path("/data/python/bin/python")), \
                patch.object(setup, "ensure_models", return_value={}), patch.object(setup, "ensure_runtime", side_effect=runtime), \
                patch.object(setup, "environment_identity", return_value={"id": 1}), \
                patch.object(setup, "run_smokes", return_value={"hunyuan": {"json_output": "h.json"}}), \
                contextlib.redirect_stdout(io.StringIO()):
            result = setup.setup(replace(self.options, engine="hunyuan"))
        self.assertEqual(result["device"], "cpu")
        values = setup.read_environment(self.options.config_dir / "env.sh")
        self.assertEqual((values["OCR_HUNYUAN_DEVICE"], values["JYUT_OCR_DEVICE"]), ("cpu", "auto"))
        self.assertEqual(setup.device_request("default", values), "auto")

    def test_prepare_only_configures_everything_but_runs_no_setup_test(self):
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "machine_name", return_value="x86_64"), \
                patch.object(setup, "check_free_disk"), patch.object(setup, "ensure_dependencies"), \
                patch.object(setup, "ensure_python", return_value=Path("/data/python/bin/python")), \
                patch.object(setup, "ensure_models", return_value={}), \
                patch.object(setup, "ensure_runtime", return_value=(Path("/data/cpu/llama-server"), {})), \
                patch.object(setup, "environment_identity", return_value={"id": 1}), \
                patch.object(setup, "run_smokes") as smokes, contextlib.redirect_stdout(io.StringIO()):
            result = setup.setup(replace(self.options, engine="hunyuan", device="cpu", prepare_only=True))
        smokes.assert_not_called()
        self.assertEqual(result["status"], "pending-smoke")
        state = json.loads((self.options.config_dir / "setup-state.json").read_text())
        self.assertEqual(state["status"], "pending-smoke")
        self.assertEqual(setup.read_environment(self.options.config_dir / "env.sh")["OCR_HUNYUAN_DEVICE"], "cpu")
        with patch.object(install, "setup", return_value=result), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install.main(["--config-dir", str(self.options.config_dir), "--env-only", "--prepare-only"]), 2)

    def test_a_short_disk_stops_before_any_download(self):
        usage = type("Usage", (), {"free": 3 * 10**9})()
        plan, _ = setup.paddle_plan(LOCK, system="Linux", machine="x86_64", request="auto", gpus=[gpu()])
        release = setup.release_binary(LOCK, system="Linux", machine="x86_64", device="cuda", gpu=gpu())
        # disk_needs() also looks for the weights under the home folder: not this machine's.
        with patch.object(Path, "home", return_value=self.base / "home"):
            needs = setup.disk_needs(self.options, {}, LOCK, ("hunyuan", "paddle"), release, plan)
        labels = [label for label, _ in needs]
        self.assertEqual(labels[:3], ["core Python environment", "HunyuanOCR weights",
                                      "llama.cpp llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz"])
        total = sum(size for _, size in needs)
        self.assertGreater(total, 10 * 10**9)
        with patch.object(setup.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(setup.SetupError, "nothing was downloaded"):
                setup.check_free_disk(self.options, needs)

    def test_a_configured_runtime_or_paddle_python_setup_replaces_is_counted(self):
        # F2-F1: a configured HUNYUAN_LLAMA_SERVER of another commit counted as
        # present, so the estimate left out the release runtime the run then
        # downloaded (559 MB) and unpacked (774 MB); the same held for a
        # PADDLEOCR_PYTHON whose packages do not match (a new paddle-python).
        card = gpu()
        plan, _ = setup.paddle_plan(LOCK, system="Linux", machine="x86_64", request="auto", gpus=[card])
        release = setup.release_binary(LOCK, system="Linux", machine="x86_64", device="cuda", gpu=card)
        home = self.base / "home"
        old_server = home / "llama.cpp/build/bin/llama-server"
        old_paddle = home / ".venvs/paddleocr/bin/python"
        for path in (old_server, old_paddle):
            path.parent.mkdir(parents=True)
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
        existing = {"HUNYUAN_LLAMA_SERVER": str(old_server), "PADDLEOCR_PYTHON": str(old_paddle)}
        release_server = (self.options.data_dir / "runtimes" / (LOCK["runtime"]["commit"] + "-release-"
                          + release["name"].removesuffix(".tar.gz")) / "bin/llama-server")
        runtime_label = "llama.cpp " + release["name"]
        paddle_label = "Paddle environment (paddlepaddle-gpu cu130)"

        def needs(verified=(), paddle=None, **devices):
            """The labels disk_needs() counts when runtime_identity verifies only
            VERIFIED and paddle_probe reports PADDLE for every Python, and the
            binaries runtime_identity was asked about."""
            asked = []

            def identity(binary, lock, device):
                asked.append((Path(binary), device))
                if Path(binary) not in verified:
                    raise setup.SetupError("Existing runtime does not report the pinned llama.cpp commit.")
                return {"sha256": "a" * 64}
            with patch.dict(os.environ, {"HOME": str(home)}), \
                    patch.object(setup, "runtime_identity", side_effect=identity), \
                    patch.object(setup, "paddle_probe", return_value=paddle or probe()):
                labels = [label for label, _ in setup.disk_needs(self.options, existing, LOCK, ("hunyuan", "paddle"),
                                                                 release, plan, **devices)]
            return labels, asked

        # The old build and a CPU Paddle are configured: both replacements count.
        labels, asked = needs(paddle=probe(distribution="paddlepaddle"))
        self.assertIn(runtime_label, labels)
        self.assertIn(paddle_label, labels)
        self.assertEqual(asked[0], (old_server, "cuda"))
        self.assertIn((release_server, "cuda"), asked)
        # Paddle packages of another version count too.
        self.assertIn(paddle_label, needs(paddle=probe(paddle="3.2.1"))[0])
        # The configured ones verify: nothing is counted for them.
        labels, _ = needs(verified={old_server})
        self.assertNotIn(runtime_label, labels)
        self.assertNotIn(paddle_label, labels)
        # The release runtime is already in the data folder: reused, not counted.
        self.assertNotIn(runtime_label, needs(verified={release_server})[0])
        # Setup's own device and GPU are what the candidates are judged for.
        _, asked = needs(device="cuda", gpu=card)
        self.assertEqual({device for _, device in asked}, {"cuda"})
        self.assertIn(paddle_label, needs(paddle=probe(), gpu=gpu(driver="576.02"))[0])


class InspectTests(Case):
    def test_check_verifies_the_saved_engines_and_every_engine_smoke(self):
        self.options.config_dir.mkdir()
        (self.options.config_dir / "env.sh").write_text(
            "export JYUT_OCR_ENGINE=hunyuan-paddle\nexport JYUT_OCR_ENGINES=hunyuan,paddle\nexport JYUT_OCR_DEVICE=auto\n"
            "export OCR_HUNYUAN_DEVICE=cuda\nexport OCR_PADDLE_DEVICE=gpu:0\n")
        identity = {"id": 1}
        state = {"schema_version": setup.STATE_SCHEMA, "status": "ready", "fingerprint": setup.canonical_hash(identity),
                 "smoke": {"hunyuan": {"completed_at": "today"}}}
        setup.atomic_json(self.options.config_dir / "setup-state.json", state)
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "missing_dependencies", return_value=[]), \
                patch.object(setup, "nvidia_gpus") as probe, \
                patch.object(setup, "environment_identity", return_value=identity) as identity_call, \
                patch.object(setup, "check_smoke", return_value=True):
            self.assertEqual(setup.inspect_setup(self.options)["status"], "action-required")
            state["smoke"]["paddle"] = {"completed_at": "today"}
            setup.atomic_json(self.options.config_dir / "setup-state.json", state)
            result = setup.inspect_setup(self.options)
        self.assertEqual((result["status"], result["devices"]), ("ready", {"hunyuan": "cuda", "paddle": "gpu:0"}))
        self.assertEqual(identity_call.call_args[0][2], ("hunyuan", "paddle"))
        probe.assert_not_called()

    def test_check_after_prepare_only_says_the_setup_test_is_owed(self):
        # F2-F2: --check after --prepare-only called a merely pending state
        # "absent or stale" and asked to rerun setup.
        self.options.config_dir.mkdir()
        (self.options.config_dir / "env.sh").write_text(
            "export JYUT_OCR_ENGINE=hunyuan-paddle\nexport JYUT_OCR_ENGINES=hunyuan,paddle\nexport JYUT_OCR_DEVICE=auto\n"
            "export OCR_HUNYUAN_DEVICE=cuda\nexport OCR_PADDLE_DEVICE=gpu:0\n")
        identity = {"id": 1}
        state = {"schema_version": setup.STATE_SCHEMA, "status": "pending-smoke", "identity": identity,
                 "fingerprint": setup.canonical_hash(identity)}
        setup.atomic_json(self.options.config_dir / "setup-state.json", state)
        with patch.object(setup, "platform_name", return_value="Linux"), \
                patch.object(setup, "missing_dependencies", return_value=[]), \
                patch.object(setup, "environment_identity", return_value=identity), \
                patch.object(setup, "check_smoke", return_value=True):
            result = setup.inspect_setup(self.options)
            self.assertEqual(result["status"], "pending-smoke")
            self.assertNotIn("stale", result["reason"])
            self.assertIn("setup test is owed", result["reason"])
            self.assertIn(f"Run ./install.sh --env-only --config-dir {self.options.config_dir} --data-dir "
                          f"{self.options.data_dir} in the foreground", result["reason"])
            self.assertEqual(result["devices"], {"hunyuan": "cuda", "paddle": "gpu:0"})
            # The default folders need no options.
            with patch.object(setup, "default_directories", return_value=(self.options.config_dir, self.options.data_dir)):
                self.assertIn("Run ./install.sh --env-only in the foreground", setup.inspect_setup(self.options)["reason"])
            # The exit stays 2: the configuration is not ready yet.
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(install.main(["--env-only", "--check", "--config-dir", str(self.options.config_dir),
                                               "--data-dir", str(self.options.data_dir)]), 2)
            self.assertIn('"status": "pending-smoke"', out.getvalue())
            # Pending for another configuration is stale, as before.
            identity["id"] = 2
            result = setup.inspect_setup(self.options)
        self.assertEqual(result["status"], "action-required")
        self.assertIn("stale", result["reason"])

    def test_paddle_identity_covers_the_package_kind_and_weights_not_paths(self):
        config = {"JYUT_OCR_PYTHON": "/venv/bin/python", "PADDLEOCR_PYTHON": "/paddle/bin/python",
                  "PADDLE_VL_MODEL_DIR": "/w/vl", "PADDLE_LAYOUT_MODEL_DIR": "/w/layout"}
        python = {"version": "3.12.1", "version_info": [3, 12, 1], "prefix": "/venv", "base_prefix": "/usr", "pillow": "12.0.0"}
        with patch.object(setup, "python_identity", return_value=python), patch.object(setup, "paddle_probe", return_value=probe()), \
                patch.object(setup, "verify_asset", return_value=True):
            first = setup.environment_identity(self.options, config, ("paddle",), {"paddle": "gpu:0"}, LOCK)
            moved = setup.environment_identity(self.options, dict(config, PADDLEOCR_PYTHON="/elsewhere/python"),
                                               ("paddle",), {"paddle": "gpu:0"}, LOCK)
            cpu = setup.environment_identity(self.options, config, ("paddle",), {"paddle": "cpu"}, LOCK)
        self.assertEqual(first["paddle"]["package"], "paddlepaddle-gpu")
        self.assertEqual(first["paddle"]["cuda"], "13.0")
        self.assertEqual(setup.setup_fingerprint(first), setup.setup_fingerprint(moved))
        self.assertNotEqual(setup.setup_fingerprint(first), setup.setup_fingerprint(cpu))
        with patch.object(setup, "python_identity", return_value=python), \
                patch.object(setup, "paddle_probe", return_value=probe(paddlex="3.6.0")), \
                patch.object(setup, "verify_asset", return_value=True):
            with self.assertRaisesRegex(setup.SetupError, "paddlex==3.7.2"):
                setup.environment_identity(self.options, config, ("paddle",), {"paddle": "gpu:0"}, LOCK)


if __name__ == "__main__":
    unittest.main()
