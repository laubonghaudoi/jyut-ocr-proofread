#!/usr/bin/env python3
"""Offline checks of the layout regions engine A's worker keeps apart from its lines.

No model, no GPU, no PaddleOCR: a made-up PaddleX page result goes through
paddleocr_vl_worker.normalise_page.  A region the recogniser left without text
(a chart, an image) used to be dropped there, so its box and label never
reached phase 3; it is now kept in `regions`, and the lines - and so the page's
text - are what they were.

Usage:
    python3 tests/check_worker_regions.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import paddleocr_vl_worker as worker  # noqa: E402
import run_paddleocr_vl as runner  # noqa: E402

# A vertical page: two ordered text columns, a chart and an image the layout
# model found with no text, and an unordered table placed by geometry.
RESULT = {
    "width": 1000, "height": 2000,
    "layout_det_res": {"boxes": [{"order": 1, "score": 0.9}, {"order": 2, "score": 0.8}]},
    "parsing_res_list": [
        {"block_label": "vertical_text", "block_content": "天地玄黃宇宙洪荒", "block_bbox": [800, 100, 900, 900],
         "block_order": 1},
        {"block_label": "chart", "block_content": "", "block_bbox": [100, 1000, 900, 1900], "block_order": None},
        {"block_label": "vertical_text", "block_content": "日月盈昃辰宿列張", "block_bbox": [600, 100, 700, 900],
         "block_order": 2},
        {"block_label": "image", "block_content": "  \n", "block_bbox": [100, 100, 300, 500], "block_order": None},
        {"block_label": "table", "block_content": "<table><tr><td>寒來</td></tr></table>",
         "block_bbox": [350, 100, 550, 900], "block_order": None},
    ],
}
# What the worker wrote for this page before it kept regions: the text blocks
# only, in the same order.
LINES = [
    {"text": "天地玄黃宇宙洪荒", "confidence": 0.9, "boundingBox": [0.8, 0.05, 0.1, 0.4],
     "blockLabel": "vertical_text", "blockOrder": 1},
    {"text": "日月盈昃辰宿列張", "confidence": 0.8, "boundingBox": [0.6, 0.05, 0.1, 0.4],
     "blockLabel": "vertical_text", "blockOrder": 2},
    {"text": "<table><tr><td>寒來</td></tr></table>", "confidence": 0.0, "boundingBox": [0.35, 0.05, 0.2, 0.4],
     "blockLabel": "table", "blockOrder": -1},
]


def check_regions_kept() -> None:
    lines, regions, width, height = worker.normalise_page(RESULT)
    assert (width, height) == (1000, 2000)
    assert lines == LINES, lines
    assert [(r["blockLabel"], r["boundingBox"], r["blockOrder"]) for r in regions] == [
        ("chart", [0.1, 0.5, 0.8, 0.45], -1), ("image", [0.1, 0.05, 0.2, 0.2], -1)], regions
    # Where each falls in the reading order: the chart after the two columns,
    # the image (leftmost) after the table.
    assert [r["afterLine"] for r in regions] == [2, 3], regions
    assert all(r["confidence"] == 0.0 for r in regions), regions
    print("normalise_page: text-less chart and image kept as regions, with their place in the order")


def check_lines_unchanged() -> None:
    # normalise_lines, the old entry point, gives the lines alone; the text the
    # runner writes is theirs, so the regions add nothing to it.
    lines, width, height = worker.normalise_lines(RESULT)
    assert lines == LINES and (width, height) == (1000, 2000), lines
    text = runner.text_from_lines(lines)
    assert text == "天地玄黃宇宙洪荒\n日月盈昃辰宿列張\n<table><tr><td>寒來</td></tr></table>\n".encode(), text
    # A page with no text-less region: no regions, the same lines as ever.
    plain = dict(RESULT, parsing_res_list=[b for b in RESULT["parsing_res_list"] if b["block_content"].strip()])
    lines2, regions2, _, _ = worker.normalise_page(plain)
    assert lines2 == LINES and regions2 == [], (lines2, regions2)
    print("normalise_lines: the lines and the text are what they were")


def check_record_contract() -> None:
    lines, regions, _, _ = worker.normalise_page(RESULT)
    assert runner._validate_line_fields({"lines": lines, "regions": regions}) is None
    # A record written before the worker kept regions has none, and is valid.
    assert runner._validate_line_fields({"lines": lines}) is None
    assert runner._validate_line_fields({"lines": lines, "regions": {}}) == "regions is not an array"
    bad = [dict(regions[0], boundingBox=[0.1, 0.2])]
    assert runner._validate_line_fields({"lines": lines, "regions": bad}).startswith("region 0"), bad
    print("runner: regions validated when present, optional in older records")


if __name__ == "__main__":
    check_regions_kept()
    check_lines_unchanged()
    check_record_contract()
    print("all worker-region checks passed")
