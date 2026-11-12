from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    REPO_ROOT / "plugins" / "jyut-ocr-proofread" / "skills" / "jyut-ocr-proofread" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _ocr_markdown import images_inventory_sha256, load_page_manifest  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import run_hunyuanocr as hy  # noqa: E402
import run_hunyuanocr_second_look as sl  # noqa: E402
import survey_pdf  # noqa: E402


def _make_solid_png(width: int = 200, height: int = 300, rgb: tuple[int, int, int] = (245, 245, 245)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class SecondLookAndImagesDirTests(unittest.TestCase):
    def test_survey_pdf_images_dir_builds_valid_manifest_for_audit(self) -> None:
        png1 = _make_solid_png(200, 300, (250, 250, 250))
        png2 = _make_solid_png(200, 300, (240, 240, 240))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images_in = root / "raw-scans"
            images_in.mkdir()
            (images_in / "page-0001.png").write_bytes(png1)
            (images_in / "page-0002.png").write_bytes(png2)

            book_dir = root / "book"
            rc = survey_pdf.main([str(book_dir), "--images-dir", str(images_in), "--jobs", "1"])
            self.assertEqual(rc, 0)

            manifest_path = book_dir / "page-manifest.json"
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(raw_manifest["source_kind"], "images-dir")
            self.assertEqual(raw_manifest["source_pdf"], "")
            loaded = load_page_manifest(manifest_path)
            self.assertEqual(loaded.source_kind, "images-dir")
            self.assertEqual(loaded.expected_scan_pages, 2)
            expected_inv = images_inventory_sha256(loaded.pages)
            self.assertEqual(raw_manifest["source_sha256"], expected_inv)

            # Annotated audit accepts images-dir page manifest without requiring --source-pdf.
            annotated = book_dir / "annotated.md"
            boundaries = book_dir / "boundaries.json"
            annotated.write_text(
                "<!-- page_001 -->\n\n第一頁正文。\n\n<!-- page_002 -->\n\n第二頁正文。\n",
                encoding="utf-8",
            )
            boundaries.write_text('{"boundaries": {"1": "start", "2": "paragraph"}}\n', encoding="utf-8")
            rc_audit = audit_ocr_markdown.main(
                [
                    str(annotated),
                    "--mode",
                    "annotated",
                    "--expected-count",
                    "2",
                    "--boundary-manifest",
                    str(boundaries),
                    "--page-manifest",
                    str(manifest_path),
                    "--summary-only",
                ]
            )
            self.assertEqual(rc_audit, 0)

    def test_short_document_extreme_margin_guard_excludes_folio_but_keeps_missed_footnotes(self) -> None:
        blocks = [
            {
                "text": "第 12 頁",
                "blockLabel": "text",
                "boundingBox": [0.42, 0.02, 0.16, 0.03],
            },
            {
                "text": "正文主體段落已經由引擎乙完整讀出無誤。",
                "blockLabel": "text",
                "boundingBox": [0.10, 0.12, 0.80, 0.55],
            },
            {
                "text": "① 頁底小字附註補充說明原文出處與校勘細節。",
                "blockLabel": "text",
                "boundingBox": [0.10, 0.78, 0.80, 0.12],
            },
        ]
        groups = sl.unread_groups(
            blocks,
            "正文主體段落已經由引擎乙完整讀出無誤。",
            total_pages=1,
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["blocks"], [2])
        self.assertIn("頁底小字附註", groups[0]["text"])

    def test_second_look_shared_client_reseals_schema_v2_and_skips_on_rerun(self) -> None:
        png = _make_solid_png(240, 320)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renders = root / "renders"
            ocr_a = root / "ocr-paddle"
            ocr_b = root / "ocr-hunyuan"
            for d in (renders, ocr_a, ocr_b):
                d.mkdir()

            # Fake model/mmproj/server files for identity hashing in shared mode.
            fake_model = root / "fake.gguf"
            fake_mmproj = root / "fake-mmproj.gguf"
            fake_bin = root / "fake-llama-server"
            fake_model.write_bytes(b"gguf-weights")
            fake_mmproj.write_bytes(b"mmproj-weights")
            fake_bin.write_bytes(b"#!/bin/sh\nexit 0\n")
            fake_bin.chmod(0o755)

            render_path = renders / "page-0001.png"
            render_path.write_bytes(png)
            render_sha = hy.sha256_file(render_path)

            # Engine A has body + missed bottom footnote block.
            a_blocks = [
                {
                    "text": "正文主體段落已經由引擎乙完整讀出無誤。",
                    "blockLabel": "text",
                    "boundingBox": [0.10, 0.12, 0.80, 0.50],
                    "confidence": 0.99,
                },
                {
                    "text": "① 頁底小字附註補充說明原文出處與校勘細節。",
                    "blockLabel": "text",
                    "boundingBox": [0.10, 0.76, 0.80, 0.14],
                    "confidence": 0.97,
                },
            ]
            (ocr_a / "page-0001.txt").write_text(
                "\n\n".join(b["text"] for b in a_blocks) + "\n", encoding="utf-8"
            )
            (ocr_a / "page-0001.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "scanPage": 1,
                        "renderWidth": 240,
                        "renderHeight": 320,
                        "lines": a_blocks,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # Seal a valid schemaVersion 2 Engine-B draft missing block 1.
            b_lines = [
                {
                    "text": "正文主體段落已經由引擎乙完整讀出無誤。",
                    "confidence": 0.0,
                    "boundingBox": [0.0, 0.0, 1.0, 1.0],
                    "blockLabel": "whole-page",
                }
            ]
            b_txt_bytes = hy.text_from_lines(b_lines)
            b_record = {
                "schemaVersion": 2,
                "engine": "hunyuanocr",
                "draftOnly": True,
                "visualReviewComplete": False,
                "scanPage": 1,
                "renderFile": "page-0001.png",
                "renderWidth": 240,
                "renderHeight": 320,
                "status": "complete",
                "lines": b_lines,
                "provenance": {
                    "renderSha256": render_sha,
                    "textSha256": hy.sha256_bytes(b_txt_bytes),
                },
            }
            b_record["provenance"]["jsonPayloadSha256"] = hy.sha256_bytes(
                hy.canonical_json_bytes(
                    {**b_record, "provenance": dict(b_record["provenance"])}
                )
            )
            (ocr_b / "page-0001.txt").write_bytes(b_txt_bytes)
            (ocr_b / "page-0001.json").write_bytes(hy.canonical_json_bytes(b_record))

            class FakeSharedClient:
                calls = 0
                report = {"mode": "shared-local-server", "acquired": True}

                def __init__(self, **_kwargs):
                    pass

                def ocr(self, _crop_path: Path, _max_tokens: int, _timeout: float):
                    FakeSharedClient.calls += 1
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": "① 頁底小字附註補充說明原文出處與校勘細節。"
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }

                def close(self) -> None:
                    pass

            report_path = root / "second-look-report.json"
            with mock.patch("_hunyuan_shared.SharedHunyuanClient", FakeSharedClient):
                rc1 = sl.main(
                    [
                        str(renders),
                        str(ocr_a),
                        str(ocr_b),
                        "--server-mode",
                        "shared",
                        "--model",
                        str(fake_model),
                        "--mmproj",
                        str(fake_mmproj),
                        "--llama-server",
                        str(fake_bin),
                        "--report",
                        str(report_path),
                    ]
                )
            self.assertEqual(rc1, 0)
            self.assertEqual(FakeSharedClient.calls, 1)

            updated_json = json.loads((ocr_b / "page-0001.json").read_text(encoding="utf-8"))
            updated_txt = (ocr_b / "page-0001.txt").read_text(encoding="utf-8")
            self.assertEqual(updated_json["secondLook"]["schemaVersion"], 2)
            self.assertIn("頁底小字附註", updated_txt)

            # Second run should detect current secondLook seal and skip without calling broker!
            with mock.patch("_hunyuan_shared.SharedHunyuanClient", FakeSharedClient):
                rc2 = sl.main(
                    [
                        str(renders),
                        str(ocr_a),
                        str(ocr_b),
                        "--server-mode",
                        "shared",
                        "--model",
                        str(fake_model),
                        "--mmproj",
                        str(fake_mmproj),
                        "--llama-server",
                        str(fake_bin),
                        "--report",
                        str(report_path),
                    ]
                )
            self.assertEqual(rc2, 0)
            self.assertEqual(FakeSharedClient.calls, 1)
            rep2 = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(rep2["counts"]["skippedCurrent"], 1)


if __name__ == "__main__":
    unittest.main()
