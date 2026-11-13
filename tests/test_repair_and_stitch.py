from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    REPO_ROOT / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _repair_edits as re_  # noqa: E402
import _repair_rules as rr  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import finalize_page_markers  # noqa: E402
import finish_book  # noqa: E402
import proofread_pages as pp  # noqa: E402
import stitch_tables  # noqa: E402
import survey_pdf  # noqa: E402


def _make_solid_png(width: int = 300, height: int = 400, rgb: tuple[int, int, int] = (252, 252, 252)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _seal_synthetic_page(
    out_dir: Path,
    scan_page: int,
    md_text: str,
    render_path: Path,
    draft_path: Path,
) -> None:
    md_bytes = md_text.encode("utf-8")
    record = {
        "schemaVersion": pp.SCHEMA_VERSION,
        "scanPage": scan_page,
        "status": "complete",
        "mode": "merge",
        "auditorClean": True,
        "auditorReport": "",
        "doubts": [],
        "needsResearch": [],
        "generatedAt": "2026-11-11T00:00:00Z",
        "provenance": {
            "renderSha256": pp.sha256_file(render_path),
            "draftSha256": pp.sha256_file(draft_path),
            "markdownSha256": pp.sha256_bytes(md_bytes),
        },
    }
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(unsealed))
    (out_dir / f"page-{scan_page:04d}.md").write_bytes(md_bytes)
    (out_dir / f"page-{scan_page:04d}.json").write_bytes(pp.canonical_json_bytes(record))


class RepairAndStitchTests(unittest.TestCase):
    def test_repair_forms_escapes_literal_asterisks_and_canonicalizes_u16fe6(self) -> None:
        page_text = (
            "全年經費（人數*日支餉額*天數）計銀壹佰圓\U00016fe6\n\n"
            "* 真正清單項目一\n"
            "* 真正清單項目二\n\n"
            "| 項目 | 算式 |\n"
            "| --- | --- |\n"
            "| 薪餉 | 人數*日支 |\n\n"
            "<table><tr><td>表內格*不跳脫*原字</td></tr></table>\n\n"
            "行內代碼 `code*star` 保持原樣。\n\n"
            "***\n"
        )
        fake_page = types.SimpleNamespace(
            engine_a_text=lambda: page_text,
            engine_b_text=lambda: page_text,
            engine_a_blocks=lambda: [],
            record={},
        )
        fake_book = types.SimpleNamespace(pages={1: fake_page})
        model = {"pages": {"1": {"stems": [], "folio": None}}}

        plan = rr.forms(fake_book, model, {1: page_text}, [1])
        ledger = re_.Ledger({1: page_text})
        ledger.apply("forms", plan.edits)
        out = ledger.texts[1]

        # U+16FE6 canonicalized to 。。
        self.assertNotIn("\U00016fe6", out)
        self.assertIn("計銀壹佰圓。。", out)
        # Prose and Markdown pipe table literal asterisks escaped as \*
        self.assertIn(r"全年經費（人數\*日支餉額\*天數）", out)
        self.assertIn(r"| 薪餉 | 人數\*日支 |", out)
        # Genuine Markdown list bullets, HTML table cells, code spans, and thematic breaks untouched!
        self.assertIn("\n* 真正清單項目一\n", out)
        self.assertIn("<table><tr><td>表內格*不跳脫*原字</td></tr></table>", out)
        self.assertIn("`code*star`", out)
        self.assertIn("\n***\n", out)

    def test_stitch_tables_output_dir_is_non_destructive_and_empty_trailing_page_finalizes(self) -> None:
        png = _make_solid_png()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renders = root / "renders"
            drafts = root / "ocr-paddle"
            proofread = root / "proofread"
            stitched = root / "proofread-stitched"
            for d in (renders, drafts, proofread):
                d.mkdir()

            p1_md = "| 科目 | 數額 |\n| --- | --- |\n| 甲項 | 一百 |\n| 乙項 | 二百 |\n"
            p2_md = "| 科目 | 數額 |\n| --- | --- |\n| 丙項 | 三百 |\n| 丁項 | 四百 |\n"
            for scan, md in ((1, p1_md), (2, p2_md)):
                r_path = renders / f"page-{scan:04d}.png"
                d_path = drafts / f"page-{scan:04d}.txt"
                r_path.write_bytes(png)
                d_path.write_text(md, encoding="utf-8")
                (drafts / f"page-{scan:04d}.json").write_text(
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "scanPage": scan,
                            "renderWidth": 300,
                            "renderHeight": 400,
                            "lines": [
                                {
                                    "text": md,
                                    "blockLabel": "table",
                                    "boundingBox": [0.05, 0.05, 0.90, 0.90],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                _seal_synthetic_page(proofread, scan, md, r_path, d_path)

            before_hashes = stitch_tables.page_files(proofread)
            rc = stitch_tables.main(
                [
                    str(proofread),
                    "--output-dir",
                    str(stitched),
                    "--renders",
                    str(renders),
                    "--drafts",
                    str(drafts),
                ]
            )
            self.assertEqual(rc, 0)
            # Original proofread directory untouched byte-for-byte!
            self.assertEqual(stitch_tables.page_files(proofread), before_hashes)

            # Stitched directory merged page 2's rows onto page 1, leaving page 2 empty "".
            stitched_p1 = (stitched / "page-0001.md").read_text(encoding="utf-8")
            stitched_p2 = (stitched / "page-0002.md").read_text(encoding="utf-8")
            self.assertIn("丙項", stitched_p1)
            self.assertIn("丁項", stitched_p1)
            self.assertEqual(stitched_p2, "")

            # Empty trailing stitched page finalizes and audits cleanly with boundary "paragraph".
            annotated = root / "combined-annotated.md"
            boundaries = root / "boundaries.json"
            final_md = root / "final.md"
            annotated.write_text(
                f"<!-- page_001 -->\n\n{stitched_p1.strip()}\n\n<!-- page_002 -->\n\n",
                encoding="utf-8",
            )
            boundaries.write_text('{"boundaries": {"1": "start", "2": "paragraph"}}\n', encoding="utf-8")
            self.assertEqual(
                finalize_page_markers.main(
                    [
                        str(annotated),
                        "--boundary-manifest",
                        str(boundaries),
                        "--output",
                        str(final_md),
                        "--write",
                        "--summary-only",
                    ]
                ),
                0,
            )
            self.assertEqual(
                audit_ocr_markdown.main(
                    [
                        str(final_md),
                        "--mode",
                        "final",
                        "--annotated-source",
                        str(annotated),
                        "--boundary-manifest",
                        str(boundaries),
                        "--summary-only",
                    ]
                ),
                0,
            )

    def test_finish_book_images_dir_preserves_plate_text_and_printed_footnotes_by_default(self) -> None:
        png = _make_solid_png()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_imgs = root / "scans"
            raw_imgs.mkdir()
            (raw_imgs / "page-0001.png").write_bytes(png)

            book = root / "book"
            self.assertEqual(survey_pdf.main([str(book), "--images-dir", str(raw_imgs), "--jobs", "1"]), 0)

            ocr_a = book / "ocr-paddle"
            ocr_b = book / "ocr-hunyuan"
            proofread = book / "proofread"
            for d in (ocr_a, ocr_b, proofread):
                d.mkdir()

            plate_line = "圖版內原件碑文轉寫字句不可刪除。"
            md_page = (
                f"正文首段引用註號②④⓪。\n\n{plate_line}\n\n---\n\n②④⓪ 頁底原號注文內容。\n"
            )
            (ocr_a / "page-0001.txt").write_text(md_page, encoding="utf-8")
            (ocr_a / "page-0001.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "scanPage": 1,
                        "renderWidth": 300,
                        "renderHeight": 400,
                        "lines": [
                            {
                                "text": "正文首段引用註號②④⓪。",
                                "blockLabel": "text",
                                "boundingBox": [0.1, 0.1, 0.8, 0.2],
                            },
                            {
                                "text": plate_line,
                                "blockLabel": "figure",
                                "boundingBox": [0.1, 0.35, 0.8, 0.35],
                            },
                            {
                                "text": "②④⓪ 頁底原號注文內容。",
                                "blockLabel": "text",
                                "boundingBox": [0.1, 0.8, 0.8, 0.1],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (ocr_b / "page-0001.txt").write_text(md_page, encoding="utf-8")
            _seal_synthetic_page(
                proofread,
                1,
                md_page,
                book / "renders" / "page-0001.png",
                ocr_a / "page-0001.txt",
            )

            rc = finish_book.main([str(book), "--workers", "1"])
            self.assertEqual(rc, 0)

            final_text = (book / "final.md").read_text(encoding="utf-8")
            self.assertIn(plate_line, final_text)
            self.assertNotIn("figures/", final_text)
            self.assertFalse((book / "figures").exists())
            self.assertIn("正文首段引用註號[^240]。", final_text)
            self.assertIn("[^240]: 頁底原號注文內容。", final_text)

            report = json.loads((book / "finish" / "finish-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["exitStatus"], 0)
            self.assertTrue(
                all(not item["written"] and item["path"] is None for item in report["delivered"])
            )
            self.assertIn("1 pages in final.md", report["result"])


if __name__ == "__main__":
    unittest.main()
