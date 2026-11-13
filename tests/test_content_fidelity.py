from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _content_fidelity as fidelity
import _packet_client as packets
import proofread_pages as pp


IMAGE = b"synthetic image bytes: no external OCR or service"


def inventory(*, kind="body", features=None, state="content"):
    obj = {"schema_version": 1, "page_state": state, "evidence": "whole original page inspected",
           "regions": [] if state == "blank" else [
               {"id": "r1", "kind": kind, "box": [0.1, 0.1, 0.9, 0.9], "evidence": "central printed region"}],
           "features": features or []}
    return fidelity.parse_inventory(json.dumps(obj), IMAGE)


def feature(text="12-3/2=10.5", before="甲", after="乙", **extra):
    return {"id": "f1", "region_id": "r1", "text": text, "before": before, "after": after, **extra}


def review(candidate, *, edits=None, verdict="complete", status="covered"):
    return json.dumps({"schema_version": 1, "candidate_sha256": fidelity.text_sha256(candidate),
                       "verdict": verdict, "regions": [{"id": "r1", "status": status,
                                                          "evidence": "all source print checked at this region"}],
                       "edits": edits or []})


def edit(old, new, before="", after=""):
    return {"region_id": "r1", "old": old, "new": new, "before": before, "after": after,
            "evidence": "visible ink in the original region confirms this precise replacement"}


class ContentFidelityTests(unittest.TestCase):
    def test_character_guard_rejects_small_losses_in_every_script_and_operator(self):
        source = "甲乙 12-3/2=10.5 x*y + z"
        for value in ("甲", "1", "x", "-", "/", "*", "=", "+"):
            with self.subTest(value=value):
                self.assertFalse(pp.verify_characters(source, source.replace(value, "", 1))[0])
        self.assertFalse(pp.verify_characters("甲乙", "")[0])
        self.assertFalse(pp.verify_characters("x=3", "x=4")[0])

    def test_guard_allows_layout_and_escaped_printed_star_without_html_attributes(self):
        self.assertTrue(pp.verify_characters("甲乙 12", "# 乙\n\n- 甲 12")[0])
        self.assertTrue(pp.verify_characters(r"甲\*乙", "<table><tr><td rowspan='2'>甲*乙</td></tr></table>")[0])
        self.assertTrue(pp.verify_characters("甲 12", "| 甲 | 12 |\n| --- | --- |")[0])
        self.assertTrue(pp.verify_characters("甲乙", "**甲**乙")[0])
        for markup in ("*甲*乙", "甲*乙*", "<strong>甲</strong>乙", "<em>甲</em>乙", "<ul><li>甲</li><li>乙</li></ul>"):
            self.assertTrue(pp.verify_characters("甲乙", markup)[0], markup)
        self.assertFalse(pp.verify_characters("x*y*z", "xyz")[0])

    def test_image_confirmed_formula_insertion_requires_current_unique_locator(self):
        inv = inventory(features=[feature()])
        output, evidence = fidelity.apply_review(review("甲乙", edits=[edit("", "12-3/2=10.5", "甲", "乙")]), "甲乙", inv)
        self.assertEqual(output, "甲12-3/2=10.5乙")
        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["result_sha256"], fidelity.text_sha256(output))
        with self.assertRaisesRegex(fidelity.FidelityError, "stale"):
            fidelity.apply_review(review("甲乙"), "甲丙乙", inv)
        with self.assertRaisesRegex(fidelity.FidelityError, "exactly once"):
            fidelity.apply_review(review("甲乙甲乙", edits=[edit("", "12", "甲", "乙")]), "甲乙甲乙", inv)

    def test_feature_moved_with_same_counts_or_shortened_is_not_complete(self):
        inv = inventory(features=[feature(text="12")])
        for text in ("甲乙12", "甲1乙", "乙12甲"):
            self.assertFalse(fidelity.apply_review(review(text), text, inv)[1]["complete"])
        self.assertTrue(fidelity.apply_review(review("甲12乙"), "甲12乙", inv)[1]["complete"])

    def test_distinct_printed_occurrences_need_distinct_output_occurrences(self):
        inv = inventory(features=[feature("12", "", ""), {**feature("12", "", ""), "id": "f2"}])
        self.assertEqual(fidelity.feature_losses("12", inv), ["f2"])
        self.assertEqual(fidelity.feature_losses("12 12", inv), [])
        inv = inventory(features=[feature("12", "甲", "乙"),
                                  {**feature("12", "", "乙"), "id": "f2"}])
        self.assertEqual(fidelity.feature_losses("甲12乙", inv), ["f2"])

    def test_table_features_follow_logical_cell_after_transpose_and_html_spans(self):
        inv = inventory(kind="table", features=[feature("12", "", "", cell={"row_label": "甲", "column_label": "值"})])
        ordinary = "| 類 | 值 |\n| --- | --- |\n| 甲 | 12 |\n| 乙 | 13 |"
        transposed = "| 類 | 甲 | 乙 |\n| --- | --- | --- |\n| 值 | 12 | 13 |"
        merged = "<table><tr><td>類</td><td>值</td></tr><tr><td rowspan='2'>甲</td><td>12</td></tr><tr><td>14</td></tr></table>"
        for text in (ordinary, transposed, merged):
            with self.subTest(text=text):
                self.assertEqual(fidelity.feature_losses(text, inv), [])
        wrong = ordinary.replace("甲 | 12", "甲 | 13").replace("乙 | 13", "乙 | 12")
        self.assertEqual(fidelity.feature_losses(wrong, inv), ["f1"])

    def test_numeric_note_wrappers_are_equivalent_but_missing_anchor_is_not(self):
        inv = inventory(features=[feature("${}^{\\textcircled{１２}}$", "甲", "乙")])
        self.assertEqual(fidelity.feature_losses("甲<sup>12</sup>乙", inv), [])
        self.assertEqual(fidelity.feature_losses("甲[^12]乙", inv), [])
        self.assertEqual(fidelity.feature_losses("甲乙\n\n[^12]: 註文", inv), ["f1"])

    def test_final_numeric_cell_repairs_refresh_mapping_but_changed_labels_or_grids_do_not(self):
        source = "| 類 | 值 |\n| --- | --- |\n| 甲 |  |"
        final = source.replace("甲 |  |", "甲 | 12-3/2 |")
        current, records = fidelity.refresh_table_mapping([source], [final])
        self.assertTrue(current)
        self.assertEqual(records[0]["final_sha256"], fidelity.text_sha256(final))
        self.assertFalse(fidelity.refresh_table_mapping([source], [final.replace("甲", "乙")])[0])
        self.assertFalse(fidelity.refresh_table_mapping([source], [final, final])[0])
        second = source.replace("甲", "乙")
        self.assertFalse(fidelity.refresh_table_mapping([source, second], [second, source])[0])
        # An invalid pre-format mapping cannot become current merely because
        # the later formatter copies the modified grids exactly.
        for recovered in ([second, source], [source.replace("甲", "丙"), second]):
            early, _ = fidelity.refresh_table_mapping([source, second], recovered)
            later, _ = fidelity.refresh_table_mapping(recovered, recovered)
            self.assertTrue(later)
            self.assertFalse(early and later)

    def test_inventory_rejects_incomplete_geometry_blank_regions_and_duplicate_keys(self):
        base = inventory()
        for bad in ([0, 0, 1], [0, 0, float("nan"), 1], [0.9, 0.1, 0.1, 0.9]):
            base["regions"][0]["box"] = bad
            with self.assertRaises(fidelity.FidelityError):
                fidelity.parse_inventory(json.dumps(base), IMAGE)
        base = inventory()
        base["page_state"] = "blank"
        with self.assertRaises(fidelity.FidelityError):
            fidelity.parse_inventory(json.dumps(base), IMAGE)
        with self.assertRaisesRegex(fidelity.FidelityError, "duplicate"):
            fidelity.parse_inventory('{"schema_version":1,"schema_version":1}', IMAGE)

    def test_omitted_regions_unanchored_or_overlapping_edits_fail_closed(self):
        inv = inventory()
        raw = json.loads(review("甲乙"))
        raw["regions"] = []
        with self.assertRaisesRegex(fidelity.FidelityError, "omitted"):
            fidelity.apply_review(json.dumps(raw), "甲乙", inv)
        with self.assertRaisesRegex(fidelity.FidelityError, "unanchored"):
            fidelity.apply_review(review("甲乙", edits=[edit("", "丙")]), "甲乙", inv)
        with self.assertRaisesRegex(fidelity.FidelityError, "overlapping"):
            fidelity.apply_review(review("甲乙", edits=[edit("甲乙", "丙"), edit("甲", "丁", after="乙")]), "甲乙", inv)

    def test_adjacent_insertion_and_replacement_do_not_depend_on_answer_order(self):
        changes = [edit("甲", "丙", after="乙"), edit("", "丁", "甲", "乙")]
        for edits in (changes, list(reversed(changes))):
            result, _ = fidelity.apply_review(review("甲乙", edits=edits), "甲乙", inventory())
            self.assertEqual(result, "丙丁乙")

    def test_only_inventory_furniture_can_be_excluded_and_uncertainty_never_clean(self):
        with self.assertRaisesRegex(fidelity.FidelityError, "cannot be excluded"):
            fidelity.apply_review(review("甲", status="excluded"), "甲", inventory())
        output, record = fidelity.apply_review(review("甲", status="excluded", edits=[edit("甲", "")]), "甲", inventory(kind="furniture"))
        self.assertEqual(output, "")
        self.assertTrue(record["complete"])
        self.assertFalse(fidelity.apply_review(review("甲", verdict="uncertain"), "甲", inventory())[1]["complete"])
        self.assertFalse(fidelity.apply_review(review("甲"), "甲", inventory(state="uncertain"))[1]["complete"])
        for empty in ("", "# ", "<p></p>", "---", "- "):
            _, deleted = fidelity.apply_review(review("甲", edits=[edit("甲", empty)]), "甲", inventory())
            self.assertFalse(deleted["complete"], empty)
            self.assertTrue(deleted["empty_retained_content"], empty)

    def test_packet_replay_is_bound_to_image_and_candidate_and_validated_by_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = packets.PacketClient(Path(tmp))
            inv = inventory(features=[feature("12", "甲", "乙")])
            with self.assertRaises(packets.PacketPending):
                fidelity.review_page(client, IMAGE, "甲12乙", inv, 1024, stage="coverage-review")
            request = client.pending_items()[0]
            client.answers[request.request_id] = {"answer": review("甲12乙")}
            self.assertTrue(fidelity.review_page(client, IMAGE, "甲12乙", inv, 1024, stage="coverage-review")[1]["complete"])
            with self.assertRaises(packets.PacketPending):
                fidelity.review_page(client, IMAGE, "甲1乙", inv, 1024, stage="coverage-review")
            with self.assertRaisesRegex(fidelity.FidelityError, "different source"):
                fidelity.review_page(client, b"changed image", "甲12乙", inv, 1024, stage="coverage-review")


class SparsePageIntegrationTests(unittest.TestCase):
    def run_page(self, draft, inv, recovered=None, uncertain=False):
        class Client:
            model, effort = "synthetic", "test"
            calls = []
            def ask_answering(self, system, prompt, image, max_tokens, kind):
                self.calls.append(kind)
                if kind == "page-inventory":
                    return json.dumps(inv), {}
                data = json.loads(prompt)
                candidate = data["candidate"]
                changes = [edit(candidate, recovered)] if kind == "content-recovery" and recovered is not None and candidate != recovered else []
                return review(candidate, edits=changes, verdict="uncertain" if uncertain and kind == "coverage-review" else "complete"), {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            render, a, b = root / "page.png", root / "a.txt", root / "b.txt"
            render.write_bytes(IMAGE)
            a.write_text(draft)
            b.write_text(draft)
            job = pp.PageJob(1, render, a, None, b, root / "page.json", root / "page.md")
            client = Client()
            record = pp.proofread_page(job, client, SimpleNamespace(max_tokens=4096))
            return record, job.out_md.read_text(), client.calls

    def test_both_cjk_empty_formula_page_is_not_blank(self):
        inv = inventory(kind="formula", features=[feature("x=12-3", "", "")])
        record, text, calls = self.run_page("x=12-3", inv)
        self.assertEqual(text, "x=12-3")
        self.assertEqual(record["mode"], "image-recovery")
        self.assertEqual(calls, ["page-inventory", "content-recovery", "coverage-review"])

    def test_both_engines_empty_can_recover_sparse_cover_from_current_image(self):
        record, text, _ = self.run_page("", inventory(kind="cover"), recovered="# 甲乙")
        self.assertEqual(text, "# 甲乙")
        self.assertTrue(record["coverageReview"]["complete"])

    def test_blank_requires_full_image_inventory(self):
        record, text, calls = self.run_page("", inventory(state="blank"))
        self.assertEqual(text, "")
        self.assertEqual(record["mode"], "blank-page")
        self.assertEqual(calls, ["page-inventory"])

    def test_source_equivalent_double_circle_transform_keeps_sealed_hash_current(self):
        record, text, _ = self.run_page("", inventory(kind="cover"), recovered="甲\U00016fe6")
        self.assertEqual(text, "甲。。")
        self.assertEqual(record["coverageReview"]["result_sha256"], record["provenance"]["markdownSha256"])
        self.assertEqual(record["contentRecovery"]["double_circle_normalizations"], 1)
        self.assertEqual(record["contentRecovery"]["source_equivalent_transforms"][0]["after_sha256"], fidelity.text_sha256(text))

    def test_uncertain_sparse_page_fails_instead_of_sealing_complete(self):
        with self.assertRaisesRegex(pp.ProofreadError, "coverage unresolved"):
            self.run_page("12", inventory(features=[feature("12", "", "")]), uncertain=True)


class FormatterFallbackIntegrationTests(unittest.TestCase):
    def test_both_formatter_failures_preserve_recovery_before_final_review(self):
        for with_layout in (False, True):
            with self.subTest(with_layout=with_layout), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                render, a, b = root / "page.png", root / "a.txt", root / "b.txt"
                render.write_bytes(IMAGE)
                a.write_text("甲12-3/2乙")
                b.write_text(a.read_text())
                block_file = root / "a.json"
                if with_layout:
                    block_file.write_text(json.dumps({"lines": [{"text": a.read_text(),
                        "boundingBox": [0.1, 0.1, 0.9, 0.9], "blockLabel": "text"}]}))
                inv = inventory(features=[feature("12-3/2")])
                observed = []
                class Client:
                    model, effort = "synthetic", "test"
                    def ask_answering(self, system, prompt, image, max_tokens, kind="other", **kwargs):
                        observed.append(kind)
                        if kind == "page-inventory":
                            return json.dumps(inv), {}
                        if kind in {"content-recovery", "coverage-review"}:
                            candidate = json.loads(prompt)["candidate"]
                            changes = [edit("", "12-3/2", "甲", "乙")] if kind == "content-recovery" else []
                            if kind == "coverage-review":
                                self_checked.assertEqual(candidate, "甲12-3/2乙")
                            return review(candidate, edits=changes), {}
                        if kind in {"format", "format-retry"}:
                            return "甲乙", {}
                        if kind == "census":
                            return "符號：0\n雙圈：0\n形狀：無", {}
                        raise AssertionError(f"unexpected call {kind}")
                self_checked = self
                args = pp.build_parser().parse_args([str(root), str(root), str(root)])
                args.prefer_engine, args.furniture, args.profile_data = "", frozenset(), {}
                job = pp.PageJob(1, render, a, block_file if with_layout else None, b,
                                 root / "out.json", root / "out.md")
                # The source review is a synthetic current-image response;
                # crop geometry is not under test in this regression.
                with mock.patch.object(pp, "block_tiles", return_value=[]), \
                     mock.patch.object(pp, "column_reach", return_value=1):
                    record = pp.proofread_page(job, Client(), args)
                self.assertEqual(job.out_md.read_text(), "甲12-3/2乙")
                self.assertIn("format-retry", observed)
                self.assertTrue(record["coverageReview"]["complete"])
                self.assertTrue(any(d["kind"] in {"formatter-rejected", "structure-rejected"}
                                    for d in record["doubts"]))


if __name__ == "__main__":
    unittest.main()
