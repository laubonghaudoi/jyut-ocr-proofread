from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _table_candidates as tc
import proofread_pages as pp
from test_fidelity_switch_and_seals import Fake, page, run, between


def blocks(*texts):
    return [{"text": text, "box": [.1, .1 + i * .1, .7, .08], "label": "text", "order": i}
            for i, text in enumerate(texts)]


class VisionTableCandidates(unittest.TestCase):
    table = "| 物名 | 件數 |\n| --- | --- |\n| 黑板 | 12 |\n| 粉筆 | 34 |"

    def test_explicit_table_maps_complete_blocks_and_unions_boxes(self):
        found, = tc.candidates(blocks("物名 件數", "黑板 12", "粉筆 34"), self.table)
        self.assertEqual(found["route"], "union-crop")
        self.assertEqual(found["blocks"], [0, 1, 2])
        for actual, expected in zip(found["box"], [.1, .1, .7, .28]):
            self.assertAlmostEqual(actual, expected)

    def test_ambiguous_partial_and_missing_cells_use_full_image(self):
        for texts in [("物名 件數", "黑板 12", "粉筆 34", "黑板"),
                      ("表外正文物名 件數", "黑板 12", "粉筆 34"),
                      ("物名 件數", "黑板 12")]:
            with self.subTest(texts=texts):
                found, = tc.candidates(blocks(*texts), self.table)
                self.assertEqual(found["route"], "full-page")
                self.assertIn("全頁圖", tc.fallback_hint([found]))

    def test_numeric_table_needs_no_cjk_anchor(self):
        numeric = "| 12 | 34 |\n| --- | --- |\n| 56 | 78 |"
        found, = tc.candidates(blocks("12 34", "56 78"), numeric)
        self.assertEqual(found["route"], "union-crop")
        lock = Counter("12345678")
        self.assertIsNotNone(pp.check_rebuilt_table(numeric, lock, lock, numeric, literal=True)[0])
        self.assertIsNone(pp.check_rebuilt_table(numeric.replace("78", "79"), lock, lock,
                                               numeric, literal=True)[0])
        self.assertIsNone(pp.check_rebuilt_table(numeric.replace("78", "7"), lock, lock,
                                               numeric, literal=True)[0])

    def test_fenced_and_malformed_markup_is_not_table_authority(self):
        self.assertEqual(tc.candidates(blocks("物名 件數"), "```\n" + self.table + "\n```"), [])
        self.assertEqual(tc.candidates(blocks("甲 乙 丙"),
                         "<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td></tr></table>"), [])
        captioned = "<table><caption>表外標題</caption><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td><td>丁</td></tr></table>"
        self.assertEqual(tc.candidates(blocks("甲乙", "丙丁"), captioned)[0]["route"], "full-page")

    def test_numeric_punctuation_is_not_discarded_by_mapping_or_rebuild(self):
        original = "| 12.5 | -2 |\n| --- | --- |\n| 10% | 1:3 |"
        lock = Counter(tc.characters(original))
        self.assertIsNotNone(pp.check_rebuilt_table(original, lock, lock, original, literal=True)[0])
        for before, after in [("12.5", "125"), ("-2", "2"), ("10%", "10"), ("1:3", "13")]:
            with self.subTest(before=before):
                changed = original.replace(before, after)
                self.assertIsNone(pp.check_rebuilt_table(changed, lock, lock, original, literal=True)[0])
                found, = tc.candidates(blocks("12.5 -2".replace(before, after),
                                                "10% 1:3".replace(before, after)), original)
                self.assertEqual(found["route"], "full-page")

    def test_numeric_page_reaches_full_image_writer_with_explicit_table_hint(self):
        numeric = "| 12 | 34 |\n| --- | --- |\n| 56 | 78 |"
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), "12 34\n56 78", numeric,
                             blocks=[("text", "12 34", [.1, .1, .7, .1]),
                                     ("text", "56 78", [.1, .3, .7, .1])])
            record = json.loads(job.draft_json.read_text())
            record["layoutContract"] = {"nativeTableLayout": False}
            job.draft_json.write_text(json.dumps(record))
            client = Fake(writer=numeric)
            sealed, output = run(job, args, client)
            self.assertEqual(output.strip(), numeric)
            self.assertEqual(sealed["mode"], "transcribe")
            self.assertIn("全頁圖", next(user for kind, user in client.calls if kind == "writer"))
            self.assertEqual(len(sealed["engineBTableCandidates"]), 1)

    def test_mapped_table_reaches_crop_rebuild_and_preserves_digits(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            raw = ["物名 件數", "黑板 12", "粉筆 34"]
            job, args = page(Path(tmp), "\n".join(raw), self.table,
                             "--table-orientation", "as-printed",
                             blocks=[("text", b["text"], b["box"]) for b in blocks(*raw)])
            Image.new("RGB", (600, 800), "white").save(job.render)
            record = json.loads(job.draft_json.read_text())
            record["layoutContract"] = {"nativeTableLayout": False}
            job.draft_json.write_text(json.dumps(record))
            client = Fake(**{"table-rebuild": self.table, "format": lambda u, n: between(u)})
            sealed, output = run(job, args, client)
            self.assertIn("table-rebuild", client.kinds())
            self.assertEqual(sealed["tablesRebuilt"][0]["outcome"], "accepted")
            self.assertIn("12", output)
            self.assertIn("34", output)
            self.assertEqual(len(pp._tables.find_tables(output)), 1)

    def test_ambiguous_numeric_table_on_mixed_page_is_given_to_full_image_format(self):
        from PIL import Image
        prose = "本頁表內所列各項數目均係本年收支之數"
        numeric = "| 12 | 34 |\n| --- | --- |\n| 56 | 78 |"
        with tempfile.TemporaryDirectory() as tmp:
            raw = prose + " 12 34 56 78"
            job, args = page(Path(tmp), raw, prose + "\n" + numeric,
                             "--table-orientation", "as-printed",
                             blocks=[("text", raw, [.1, .1, .7, .7])])
            Image.new("RGB", (600, 800), "white").save(job.render)
            record = json.loads(job.draft_json.read_text())
            record["layoutContract"] = {"nativeTableLayout": False}
            job.draft_json.write_text(json.dumps(record))
            client = Fake(format=prose + "\n" + numeric)
            sealed, output = run(job, args, client)
            self.assertEqual(sealed["engineBTableCandidates"][0]["route"], "full-page")
            prompt = next(user for kind, user in client.calls if kind == "format")
            self.assertIn(numeric, prompt)
            self.assertIn("全頁圖", prompt)
            self.assertIn("78", output)
            self.assertEqual(len(pp._tables.find_tables(output)), 1)


if __name__ == "__main__":
    unittest.main()
