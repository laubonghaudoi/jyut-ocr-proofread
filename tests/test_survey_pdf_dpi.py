#!/usr/bin/env python3
"""Offline checks for survey_pdf adaptive render DPI (#11)."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import survey_pdf  # noqa: E402


class SurveyPdfDpiTests(unittest.TestCase):
    def test_parse_dpi_spec_variants_and_validation(self) -> None:
        self.assertEqual(survey_pdf.parse_dpi_spec(None), ("fixed", 300))
        self.assertEqual(survey_pdf.parse_dpi_spec("300"), ("fixed", 300))
        self.assertEqual(survey_pdf.parse_dpi_spec("150"), ("fixed", 150))
        self.assertEqual(survey_pdf.parse_dpi_spec("auto"), ("auto", None))
        self.assertEqual(survey_pdf.parse_dpi_spec(" AUTO "), ("auto", None))
        for bad in ("71", "0", "-10", "abc"):
            with self.assertRaises(survey_pdf.SurveyError):
                survey_pdf.parse_dpi_spec(bad)

    def test_pdf_page_sizes_and_image_ppis_parsing(self) -> None:
        pdfinfo_out = (
            "Title: Sample\n"
            "Pages: 4\n"
            "Page size: 595.22 x 842 pts\n"
            "Page    1 size: 2380.32 x 3522.00 pts (A0)\n"
            "Page    2 size: 1273.32 x 1834.32 pts\n"
            "Page    3 size: 595.22 x 842 pts (A4)\n"
        )
        pdfimages_out = (
            "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
            "--------------------------------------------------------------------------------------------\n"
            "   1     0 image    2380  3522  rgb     3   8  jpeg   no         7  0    72    72  450K 2.0%\n"
            "   2     0 stencil  1666  2400  gray    1   1  ccitt  no         9  0    94    94  120K 1.0%\n"
            "   2     1 image     833  1200  rgb     3   8  jpeg   no        10  0    47    47   90K 1.5%\n"
            "   3     0 smask      64    64  gray    1   8  flate  no        11  0   600   600    2K 0.5%\n"
            "   3     1 image      96    96  gray    1   8  flate  no        12  0   600   600    3K 0.5%\n"
            "   4     0 image     384   591  rgb     3   8  jpeg   no        15  0    46    51   40K 3.0%\n"
        )

        with mock.patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    [], 0, stdout=pdfinfo_out, stderr=""
                )
                sizes = survey_pdf.pdf_page_sizes(
                    Path("dummy.pdf"), 4, {"Page size": "595.22 x 842 pts"}
                )
        self.assertEqual(sizes[1], (2380.32, 3522.0))
        self.assertEqual(sizes[2], (1273.32, 1834.32))
        self.assertEqual(sizes[3], (595.22, 842.0))
        self.assertEqual(sizes[4], (595.22, 842.0))

        with mock.patch.object(survey_pdf, "find_pdfimages", return_value="/usr/bin/pdfimages"):
            with mock.patch("subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    [], 0, stdout=pdfimages_out, stderr=""
                )
                ppis, avail = survey_pdf.pdf_image_ppis(Path("dummy.pdf"))
        self.assertTrue(avail)
        self.assertEqual(ppis[1], 72.0)
        self.assertEqual(ppis[2], 94.0)  # max(94, 47) stencil + image
        self.assertNotIn(3, ppis)  # smask and <128px thumbnail ignored
        self.assertEqual(ppis[4], 51.0)

        with mock.patch.object(survey_pdf, "find_pdfimages", return_value=None):
            ppis_none, avail_none = survey_pdf.pdf_image_ppis(Path("dummy.pdf"))
            self.assertFalse(avail_none)
            self.assertEqual(ppis_none, {})

    def test_choose_page_dpis_caps_oversized_and_preserves_300ppi_and_coalesces_ranges(self) -> None:
        sizes = {
            1: (2380.32, 3522.0),   # 72-ppi giant scan -> 72 dpi (~8.4 MP instead of 145.5 MP)
            2: (1273.32, 1834.32),  # 94-ppi oversize -> 100 dpi
            3: (2380.32, 3522.0),   # vector giant (no embedded image) -> capped under 40M px
            4: (595.22, 842.0),     # standard 300-ppi scan -> 300 dpi
            5: (595.22, 842.0),     # blank/text A4 leaf -> 300 dpi
            6: (595.22, 842.0),     # standard 46x51 ppi scan -> floored at 72 dpi
        }
        ppis = {1: 72.0, 2: 94.0, 4: 299.5, 6: 51.0}
        page_dpis, ranges, summary = survey_pdf.choose_page_dpis(
            [1, 2, 3, 4, 5, 6],
            sizes,
            ppis,
            dpi_mode="auto",
            max_pixels=40_000_000,
            pdfimages_available=True,
        )
        expected_cap_p3 = math.floor(
            math.sqrt(40_000_000 / ((2380.32 / 72.0) * (3522.0 / 72.0)))
        )
        self.assertEqual(page_dpis[1], 72)
        self.assertEqual(page_dpis[2], 100)
        self.assertEqual(page_dpis[3], expected_cap_p3)
        self.assertLess(page_dpis[3], 180)
        self.assertEqual(page_dpis[4], 300)
        self.assertEqual(page_dpis[5], 300)
        self.assertEqual(page_dpis[6], 72)
        self.assertEqual(
            ranges,
            [
                {"first": 1, "last": 1, "dpi": 72},
                {"first": 2, "last": 2, "dpi": 100},
                {"first": 3, "last": 3, "dpi": expected_cap_p3},
                {"first": 4, "last": 5, "dpi": 300},
                {"first": 6, "last": 6, "dpi": 72},
            ],
        )
        self.assertEqual(summary["pagesWithImages"], 4)
        self.assertEqual(summary["cappedPages"], 1)

        fixed_dpis, fixed_ranges, _ = survey_pdf.choose_page_dpis(
            [1, 2, 3], sizes, ppis, dpi_mode="fixed", fixed_dpi=200
        )
        self.assertEqual(fixed_dpis, {1: 200, 2: 200, 3: 200})
        self.assertEqual(fixed_ranges, [{"first": 1, "last": 3, "dpi": 200}])

    def test_auto_dpi_end_to_end_and_renders_only_uses_recorded_ranges(self) -> None:
        if not (shutil.which("pdftoppm") and shutil.which("pdfinfo")):
            self.skipTest("poppler pdftoppm/pdfinfo not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            out = root / "book"
            # Large 18x24 inch PDF at 72 dpi so --dpi 300 would be massive,
            # while --dpi auto with mocked 72-ppi image chooses 72 dpi on page 1 and 120 on page 2.
            p1 = Image.new("RGB", (600, 800), (255, 255, 255))
            p2 = Image.new("RGB", (600, 800), (250, 250, 250))
            p1.save(pdf, "PDF", resolution=72.0, save_all=True, append_images=[p2])

            with mock.patch.object(
                survey_pdf,
                "pdf_image_ppis",
                return_value=({1: 72.0, 2: 118.0}, True),
            ):
                rc = survey_pdf.main([str(pdf), str(out), "--dpi", "auto", "--jobs", "1"])
            self.assertEqual(rc, 0)

            report = json.loads((out / "survey-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["render"]["dpi"], "auto")
            self.assertEqual(report["render"]["dpiMode"], "auto")
            self.assertEqual(
                report["render"]["dpiRanges"],
                [{"first": 1, "last": 1, "dpi": 72}, {"first": 2, "last": 2, "dpi": 120}],
            )
            p1_png = out / "renders" / "page-0001.png"
            p2_png = out / "renders" / "page-0002.png"
            with Image.open(p1_png) as im1, Image.open(p2_png) as im2:
                w1, h1 = im1.size
                w2, h2 = im2.size
            self.assertLess(w1 * h1, w2 * h2)
            p1_sha = survey_pdf.sha256_file(p1_png)
            p2_sha = survey_pdf.sha256_file(p2_png)
            p1_png.unlink()
            p2_png.unlink()

            # --renders-only without --dpi must restore page 1 at 72 dpi and page 2 at 120 dpi!
            rc_re = survey_pdf.main([str(pdf), str(out), "--renders-only", "--jobs", "1"])
            self.assertEqual(rc_re, 0)
            self.assertEqual(survey_pdf.sha256_file(p1_png), p1_sha)
            self.assertEqual(survey_pdf.sha256_file(p2_png), p2_sha)


if __name__ == "__main__":
    unittest.main()
