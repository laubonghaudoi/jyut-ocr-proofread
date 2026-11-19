"""PaddleOCR-VL with its VL recognition on an OpenAI-compatible server someone else runs
(--vl-backend sglang-server / vllm-server): the drafts' identity, the worker's environment, the
attestation of what the worker loaded, and the arguments the backend needs.

Offline: no Paddle, no server, no GPU.  On M1's H200 nodes native Paddle generation never ends a
page (CUDA 12.6 wheel, driver 570); the same pipeline with SGLang 0.5.23 serving the pinned
weights reads one in 1-5 s, which is why this backend exists.

Usage: python3 -m unittest tests.test_paddle_vl_server   (from the skill root)
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_paddleocr_vl as paddle  # noqa: E402


class ServerBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.weights = {}
        for role in ("vl", "layout"):
            directory = base / role
            directory.mkdir()
            (directory / "config.json").write_text('{"dtype": "bfloat16"}')
            (directory / "model.safetensors").write_bytes(role.encode() * 8)
            self.weights[role] = {"directory": str(directory)}
        self.server = {"backend": "sglang-server", "url": "http://127.0.0.1:31500/v1",
                       "model": "PaddleOCR-VL-1.6", "concurrency": 16, "layoutDevice": "gpu:0"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_identity_names_the_server_and_attests_the_pinned_weights(self):
        identity = paddle.server_identity("sglang-server", self.weights, "PaddleOCR-VL-1.6", "dep-1", 16)
        self.assertEqual((identity["name"], identity["apiModel"], identity["serverRevision"],
                          identity["vlRecMaxConcurrency"]), ("sglang-server", "PaddleOCR-VL-1.6", "dep-1", 16))
        self.assertEqual(set(identity["modelAssetsSha256"]), {"vl", "layout"})
        # Other weights, another deployment or another concurrency: another reader.
        (Path(self.weights["vl"]["directory"]) / "model.safetensors").write_bytes(b"other")
        changed = paddle.server_identity("sglang-server", self.weights, "PaddleOCR-VL-1.6", "dep-1", 16)
        self.assertNotEqual(changed["modelAssetsSha256"]["vl"], identity["modelAssetsSha256"]["vl"])
        self.assertNotEqual(paddle.server_identity("sglang-server", self.weights, "PaddleOCR-VL-1.6", "dep-2", 16),
                            changed)

    def test_a_server_draft_has_a_backend_identity_and_a_native_one_does_not(self):
        profile = dict(paddle.OCR_PROFILE, pipelineVersion="v1.6", modelRevision="r")
        native = paddle.draft_identity(profile)
        served = paddle.draft_identity(dict(profile, vlBackend="sglang-server", backendIdentity={"name": "sglang-server"}))
        self.assertNotIn("backend", native)
        self.assertEqual(served["backend"], {"name": "sglang-server"})
        self.assertNotEqual(paddle.profile_sha256(native), paddle.profile_sha256(served))

    def test_the_worker_is_told_the_server_and_keeps_its_layout_device(self):
        environment = paddle.worker_environment("gpu:0", self.weights, server=self.server)
        self.assertEqual(environment["JYUT_PADDLE_DEVICE"], "gpu:0")
        self.assertEqual((environment["JYUT_PADDLE_VL_BACKEND"], environment["JYUT_PADDLE_VL_SERVER_URL"],
                          environment["JYUT_PADDLE_VL_API_MODEL"], environment["JYUT_PADDLE_VL_CONCURRENCY"]),
                         ("sglang-server", "http://127.0.0.1:31500/v1", "PaddleOCR-VL-1.6", "16"))
        self.assertNotIn("JYUT_PADDLE_VL_BACKEND", paddle.worker_environment("gpu:0", self.weights))

    def test_the_attestation_catches_another_server_model_concurrency_or_device(self):
        loaded = {"vlBackend": "sglang-server", "vlServerUrl": "http://127.0.0.1:31500/v1/",
                  "vlApiModel": "PaddleOCR-VL-1.6", "vlMaxConcurrency": 16, "layoutDevice": "gpu:0"}
        self.assertIsNone(paddle.server_execution_problem(loaded, self.server))
        for key, value, words in (("vlServerUrl", "http://127.0.0.1:9/v1", "server URL"),
                                  ("vlApiModel", "other", "vlApiModel"),
                                  ("vlMaxConcurrency", 1, "vlMaxConcurrency"),
                                  ("vlBackend", "vllm-server", "vlBackend"),
                                  ("layoutDevice", "cpu", "layout detector")):
            with self.subTest(key=key):
                self.assertIn(words, paddle.server_execution_problem(dict(loaded, **{key: value}), self.server))

    def test_the_backend_needs_a_url_and_a_declared_revision(self):
        renders = Path(self.tmp.name) / "renders"
        renders.mkdir()
        (renders / "page-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")   # past the render check
        for argv, words in ((["--vl-backend", "sglang-server"], "--vl-server-url"),
                            (["--vl-backend", "sglang-server", "--vl-server-url", "http://127.0.0.1:1/v1"],
                             "--vl-server-revision"),
                            (["--vl-backend", "vllm-server", "--vl-server-url", "ftp://x"], "--vl-server-url")):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    status = paddle.main([str(renders), str(Path(self.tmp.name) / "out"), *argv])
                self.assertEqual(status, 2)
                self.assertIn(words, err.getvalue())


class ServedBlockTextTests(unittest.TestCase):
    """SGLang opens some blocks with a newline the native and MLX readers never write; it went
    into the drafts and led the repair's line pairing astray (s0583 lost its Western lines)."""
    RESULT = {
        "width": 1000, "height": 2000,
        "layout_det_res": {"boxes": [{"order": 1, "score": 0.9}, {"order": 2, "score": 0.8}]},
        "parsing_res_list": [
            {"block_label": "paragraph_title", "block_content": "\n外文專書：", "block_bbox": [100, 100, 400, 150],
             "block_order": 1},
            {"block_label": "reference_content", "block_content": "\nCh'u, T'ung-tsu.\nLocal Government",
             "block_bbox": [100, 200, 900, 300], "block_order": 2},
        ],
    }

    def lines(self, backend):
        import os
        import paddleocr_vl_worker as worker
        old = os.environ.pop("JYUT_PADDLE_VL_BACKEND", None)
        try:
            if backend:
                os.environ["JYUT_PADDLE_VL_BACKEND"] = backend
            return [line["text"] for line in worker.normalise_page(self.RESULT)[0]]
        finally:
            os.environ.pop("JYUT_PADDLE_VL_BACKEND", None)
            if old is not None:
                os.environ["JYUT_PADDLE_VL_BACKEND"] = old

    def test_a_served_block_loses_its_leading_newline_and_keeps_the_rest(self):
        for backend in ("sglang-server", "vllm-server"):
            with self.subTest(backend=backend):
                self.assertEqual(self.lines(backend), ["外文專書：", "Ch'u, T'ung-tsu.\nLocal Government"])

    def test_native_and_mlx_text_is_left_as_read(self):
        for backend in (None, "mlx-vlm-server"):
            with self.subTest(backend=backend):
                self.assertEqual(self.lines(backend), ["\n外文專書：", "\nCh'u, T'ung-tsu.\nLocal Government"])


if __name__ == "__main__":
    unittest.main()
