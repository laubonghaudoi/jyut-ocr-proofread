"""Direct checks for the Markdown masking and title-star helpers in _ocr_markdown."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jyut_ocr.core import ocr_markdown as om  # noqa: E402


class MaskNonrenderedMarkdownTests(unittest.TestCase):
    def test_fences_comments_and_code_spans_are_blanked_in_place(self) -> None:
        text = (
            "<!-- page_001 -->\n## Item*\n\n```text\n* fenced\n```\n"
            "<!--\n* comment\n-->\nBefore `code\n* span` after.\nKeep [source].\n"
        )
        lines = om.split_lines(text)
        masked = om.mask_nonrendered_markdown(lines)

        self.assertEqual([len(line) for line in masked], [len(line) for line in lines])
        self.assertTrue(all(line.endswith("\n") for line in masked))
        self.assertEqual(masked[1], "## Item*\n")
        self.assertEqual(masked[-1], "Keep [source].\n")
        for index in (0, 3, 4, 5, 6, 7, 8):
            with self.subTest(line=index + 1):
                self.assertEqual(masked[index].strip(), "")
        self.assertEqual(masked[9], "Before      \n")
        self.assertEqual(masked[10], "        after.\n")

    def test_page_markers_can_be_preserved(self) -> None:
        lines = om.split_lines("<!-- page_001 -->\n<!-- note -->正文\n")
        masked = om.mask_nonrendered_markdown(lines, preserve_page_markers=True)
        self.assertEqual(masked[0], "<!-- page_001 -->\n")
        self.assertEqual(masked[1], " " * len("<!-- note -->") + "正文\n")


class TitleNoteHeadingMarkerTests(unittest.TestCase):
    def test_terminal_stars_on_a_heading_are_reported_with_their_column(self) -> None:
        self.assertEqual(om.terminal_title_note_heading_marker("## 題名*"), ("*", 5))
        self.assertEqual(om.terminal_title_note_heading_marker("## 題名**"), ("**", 5))
        self.assertEqual(om.terminal_title_note_heading_marker("## 題名*（附註）"), ("*", 5))

    def test_emphasis_headings_and_body_lines_are_not_title_notes(self) -> None:
        for body in ("## *Italic title*", "## **Bold title**", "題名*", "## 題名"):
            with self.subTest(body=body):
                self.assertIsNone(om.terminal_title_note_heading_marker(body))


if __name__ == "__main__":
    unittest.main()
