"""The 2026-11-14 decisions on content fidelity, seals, packet IDs and transport.

Synthetic pages and fake clients only: no model, no OCR engine, no network
(the one HTTP model list is a mocked urlopen).
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jyut_ocr.core import content_fidelity as fidelity  # noqa: E402
import _packet_client as packets  # noqa: E402
import book_profile  # noqa: E402
import proofread_pages as pp  # noqa: E402
import stitch_tables  # noqa: E402

IMAGE = b"synthetic page image bytes, no visual accuracy claim"
IMAGE_SHA = hashlib.sha256(IMAGE).hexdigest()
FIDELITY_KINDS = {"page-inventory", "content-recovery", "coverage-review"}
TABLE = "| 物名 | 件數 |\n| --- | --- |\n| 黑板 | 二 |\n| 粉筆 | 十 |"


def inventory(regions=(("r1", "body"),), features=(), state="content"):
    return {"schema_version": 2, "contract_version": fidelity.CONTRACT_VERSION,
            "source_image_sha256": IMAGE_SHA, "page_state": state, "issues": [],
            "evidence": "synthetic whole page", "features": list(features),
            "regions": [] if state == "blank" else [
                {"id": rid, "kind": kind, "box": [0.1, 0.1 + 0.2 * i, 0.9, 0.2 + 0.2 * i],
                 "evidence": "synthetic region"} for i, (rid, kind) in enumerate(regions)]}


def review(request, *, verdict="complete", edits=(), mappings=(), status="covered"):
    inv = request["inventory"]
    return {"schema_version": 2, "contract_version": fidelity.CONTRACT_VERSION,
            "source_image_sha256": request["source_image_sha256"],
            "inventory_sha256": request["inventory_sha256"], "candidate_sha256": request["candidate_sha256"],
            "verdict": verdict, "edits": list(edits), "resolutions": [], "table_mappings": list(mappings),
            "regions": [{"id": r["id"], "status": status, "evidence": "synthetic check"} for r in inv["regions"]]}


class Fake:
    """Answers by kind: a string, or a function of (user_text, call count of that kind)."""
    model, effort = "fake", None

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def ask_answering(self, system, user_text, image, max_tokens, kind="other", **_):
        self.calls.append((kind, user_text))
        if kind == "census":
            return "符號：0\n形狀：無", {}
        answer = self.answers.get(kind, "")
        if callable(answer):
            answer = answer(user_text, sum(1 for k, _ in self.calls if k == kind))
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)), {}

    def kinds(self):
        return [kind for kind, _ in self.calls]


def between(text):
    return text.split("---\n", 1)[1].split("\n---", 1)[0]


def page(root, draft, draft2, *switches, blocks=None):
    render, a, b = root / "page.png", root / "a.txt", root / "b.txt"
    render.write_bytes(IMAGE)
    a.write_text(draft, encoding="utf-8")
    b.write_text(draft2, encoding="utf-8")
    layout = None
    if blocks is not None:
        layout = root / "a.json"
        layout.write_text(json.dumps({"lines": [
            {"text": text, "boundingBox": box, "blockLabel": label, "blockOrder": n}
            for n, (label, text, box) in enumerate(blocks)]}, ensure_ascii=False), encoding="utf-8")
    job = pp.PageJob(1, render, a, layout, b, root / "page-0001.json", root / "page-0001.md")
    args = pp.build_parser().parse_args([str(root), str(root), str(root), "--backend", "packet", *switches])
    args.prefer_engine, args.fallback_engine, args.furniture, args.profile_data = "A", "A", frozenset(), None
    args.profile_sha, args.variant_rulings, args.variant_rulings_sha = "", {}, ""
    args.variant_engine, args.stale_pair_classes = None, []
    return job, args


def run(job, args, client):
    with mock.patch.object(pp, "block_tiles", return_value=[]), mock.patch.object(pp, "column_reach", return_value=1):
        record = pp.proofread_page(job, client, args)
    return record, job.out_md.read_text(encoding="utf-8")


def expect(job, args):
    return {**pp.sealed_inputs(job), **pp.decision_provenance(args)}


class SwitchOffTests(unittest.TestCase):
    def test_default_off_asks_no_fidelity_question_and_seals_without_its_record(self):
        draft = "甲乙丙丁戊己"
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft)
            self.assertEqual(args.content_fidelity, "off")
            client = Fake(format=lambda u, n: between(u))
            record, text = run(job, args, client)
            self.assertFalse(FIDELITY_KINDS & set(client.kinds()), client.kinds())
            self.assertFalse(fidelity.carries_record(record))
            self.assertNotIn(pp.CONTENT_FIDELITY_KEY, record["provenance"])
            self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))
            # A page sealed with the switch off is not current for a run with it on.
            _, on = page(Path(tmp), draft, draft, "--content-fidelity", "on")
            current, reason = pp.validate_completion(job, expect(job, on))
            self.assertFalse(current)
            self.assertIn("--content-fidelity", reason)

    def test_default_off_blank_page_asks_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), "", "")
            client = Fake()
            record, text = run(job, args, client)
            self.assertEqual((client.calls, text, record["mode"]), ([], "", "blank-page"))
            self.assertFalse(fidelity.carries_record(record))

    # Text with no CJK in it - a Latin title page, a page of figures, a
    # formula - is no blank page: with the switch off it is transcribed from
    # the image, both drafts its hint, and never sealed empty and clean.
    NON_CJK_PAGES = {"latin title page": "A VOCABULARY\nOF A SOUTHERN DIALECT\nPRINTED IN 1850",
                     "figures only": "1 2 3 4 5\n6 7 8 9 10\n1885",
                     "formula": "x=12-3"}

    def test_default_off_non_cjk_page_is_transcribed_not_blank(self):
        for label, text in self.NON_CJK_PAGES.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                job, args = page(Path(tmp), text, text)
                client = Fake(writer=text)
                record, written = run(job, args, client)
                self.assertEqual(client.kinds(), ["writer"])
                self.assertIn("OCR 底稿 B", client.calls[0][1])
                self.assertEqual((written, record["mode"], record["auditorClean"]), (text, "transcribe", True))
                self.assertEqual(record["engineCoverage"]["merged"], None)
                self.assertFalse(fidelity.carries_record(record))
                self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))

    def test_default_off_single_engine_non_cjk_page_is_transcribed(self):
        text = self.NON_CJK_PAGES["latin title page"]
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), text, "")
            job = dataclasses.replace(job, draft2_text=None)
            client = Fake(writer=text)
            record, written = run(job, args, client)
            self.assertEqual((client.kinds(), written, record["mode"]), (["writer"], text, "transcribe"))
            self.assertNotIn("OCR 底稿 B", client.calls[0][1])

    def test_default_off_empty_transcription_of_non_cjk_page_is_a_doubt(self):
        text = self.NON_CJK_PAGES["latin title page"]
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), text, text)
            record, written = run(job, args, Fake(writer=""))
            self.assertEqual(written, "")
            self.assertFalse(record["auditorClean"])
            self.assertEqual([d["kind"] for d in record["doubts"]], ["non-cjk-text-not-transcribed"])

    def test_default_off_folio_or_furniture_alone_is_still_blank(self):
        box = [0.45, 0.9, 0.1, 0.03]
        for draft, draft2, blocks in (("12", "12", None), ("- 12 -", "", None), ("(iv)", "", None),
                                      ("RUNNING HEAD\n12", "12", [("header", "RUNNING HEAD", box),
                                                                  ("number", "12", box)]),
                                      ("<table><tr><td></td></tr></table>", "", None), ("* * *", "", None),
                                      ("(192,171),(744,785)", "(192,171),(744,785)", None)):
            with self.subTest(draft=draft), tempfile.TemporaryDirectory() as tmp:
                job, args = page(Path(tmp), draft, draft2, blocks=blocks)
                client = Fake()
                record, written = run(job, args, client)
                self.assertEqual((client.calls, written, record["mode"]), ([], "", "blank-page"))

    def test_table_placeholders_reach_the_formatter_in_both_modes(self):
        grid = "<table><tr><td>物名</td><td>件數</td></tr><tr><td>黑板</td><td>二</td></tr>" \
               "<tr><td>粉筆</td><td>十</td></tr></table>"
        title, prose = "某處學堂器具表", "右表所列各件均係本年添置之物"
        blocks = [("doc_title", title, [0.8, 0.1, 0.05, 0.5]), ("table", grid, [0.3, 0.1, 0.4, 0.7]),
                  ("text", prose, [0.1, 0.1, 0.1, 0.8])]
        draft = "\n".join(text for _, text, _ in blocks)
        b_text = f"{title}\n物名件數黑板二粉筆十\n{prose}"
        inv = inventory(regions=(("r1", "heading"), ("r2", "table"), ("r3", "body")))

        maps = [{"region_id": "r2", "table_index": 0, "orientation": "identity", "source_rows": 3,
                 "source_cols": 2, "evidence": "synthetic grid"}]

        def coverage(user, _):
            return review(json.loads(user), mappings=maps)

        def recovery(user, _):
            request = json.loads(user)
            self.assertIn(TABLE, request["candidate"])   # the reviewer sees the table written out
            # An image-confirmed edit to the prose: the tables go back to tokens.
            edits = [{"region_id": "r3", "before": "", "old": "右表所列", "after": "各件均係",
                      "new": "右表所開", "evidence": "synthetic ink reads 開"}]
            return review(request, edits=edits, mappings=maps)

        for switch in ("off", "on"):
            with self.subTest(content_fidelity=switch), tempfile.TemporaryDirectory() as tmp:
                job, args = page(Path(tmp), draft, b_text, "--content-fidelity", switch, blocks=blocks)
                client = Fake(**{"format": lambda u, n: between(u), "orient": "RECORD: row",
                                 "page-inventory": inv, "content-recovery": recovery,
                                 "coverage-review": coverage})
                record, text = run(job, args, client)
                prompt = next(u for kind, u in client.calls if kind == "format")
                self.assertIn(pp.TABLE_TOKEN.format(1), between(prompt))
                self.assertNotIn("黑板", between(prompt))
                self.assertIn(TABLE, text)
                if switch == "on":
                    self.assertIn("右表所開", text)
                    self.assertEqual(record["contentFidelityStatus"], "complete", record["auditorReport"])
                    self.assertTrue(record["coverageReview"]["complete"])
                    changes = [c for c in record["decisionChanges"] if c["decision"] == "contentFidelity"]
                    self.assertEqual([(c["rule"], c["before"], c["after"]) for c in changes],
                                     [("content-recovery", "右表所列", "右表所開")])
                    self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))
                else:
                    self.assertFalse(FIDELITY_KINDS & set(client.kinds()))


class DoubtLabelTests(unittest.TestCase):
    def test_every_doubt_kind_a_page_seals_has_a_doubts_md_label(self):
        # doubts.md files each kind under a section with a label
        # (finish_book.KINDS); one without falls to a bare kind name.
        import finish_book
        pp_path = Path(pp.__file__)
        source = "".join(
            path.read_text(encoding="utf-8")
            for path in [pp_path, *sorted(pp_path.parent.glob("_proofread_*.py"))]
        )
        kinds = set(re.findall(r'"kind":\s*"([a-z-]+)",\s*"detail"', source))
        self.assertIn("non-cjk-text-not-transcribed", kinds)
        self.assertEqual(sorted(kinds - set(finish_book.KINDS)), [])


class SwitchOnFaultTests(unittest.TestCase):
    def test_blank_inventory_over_engine_text_is_a_doubt_and_keeps_the_merged_text(self):
        draft = "甲乙丙丁戊己"
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft, "--content-fidelity", "on")
            client = Fake(**{"page-inventory": inventory(state="blank"), "format": lambda u, n: between(u)})
            record, text = run(job, args, client)
            self.assertEqual(text, draft)
            self.assertEqual(record["mode"], "merge")
            self.assertNotIn("content-recovery", client.kinds())
            self.assertNotIn("coverage-review", client.kinds())
            self.assertEqual(record["contentFidelityStatus"], "doubt")
            self.assertTrue(any(d["kind"] == fidelity.DOUBT_KIND and "blank" in d["detail"] for d in record["doubts"]))
            self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))

    def test_blank_inventory_over_non_cjk_text_is_a_doubt_and_the_page_is_transcribed(self):
        text = SwitchOffTests.NON_CJK_PAGES["latin title page"]
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), text, text, "--content-fidelity", "on")
            client = Fake(**{"page-inventory": inventory(state="blank"), "writer": text})
            record, written = run(job, args, client)
            self.assertEqual((client.kinds(), written, record["mode"]), (["page-inventory", "writer"], text, "transcribe"))
            self.assertEqual(record["contentFidelityStatus"], "doubt")
            self.assertTrue(any(d["kind"] == fidelity.DOUBT_KIND and "no CJK" in d["detail"] for d in record["doubts"]))
            self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))

    def test_failed_answers_seal_the_page_with_a_doubt(self):
        draft = "甲乙丙丁戊己"
        for stage in ("page-inventory", "content-recovery", "coverage-review"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                job, args = page(Path(tmp), draft, draft, "--content-fidelity", "on")
                answers = {"page-inventory": inventory(), "format": lambda u, n: between(u),
                           "content-recovery": lambda u, n: review(json.loads(u)),
                           "coverage-review": lambda u, n: review(json.loads(u))}
                answers[stage] = "not json"
                answers[stage + "-correction"] = "[]"
                client = Fake(**answers)
                record, text = run(job, args, client)
                self.assertEqual(text, draft)
                self.assertIn(stage + "-correction", client.kinds())
                self.assertEqual(record["contentFidelityStatus"], "doubt")
                self.assertTrue(any(d["kind"] == fidelity.DOUBT_KIND and stage in d["detail"]
                                    for d in record["doubts"]))
                self.assertTrue(list(Path(tmp).glob("page-0001.fidelity-failure-*.json")))
                self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))

    def test_unresolved_coverage_keeps_safe_edits_logs_them_and_seals_a_doubt(self):
        draft = "甲乙丙丁戊己"
        edit = {"region_id": "r1", "before": "甲乙", "old": "丙", "after": "丁", "new": "两",
                "evidence": "synthetic ink"}
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft, "--content-fidelity", "on")
            client = Fake(**{"page-inventory": inventory(), "format": lambda u, n: between(u),
                             "content-recovery": lambda u, n: review(json.loads(u)),
                             "coverage-review": lambda u, n: review(json.loads(u), verdict="uncertain",
                                                                    edits=[edit], status="uncertain")})
            record, text = run(job, args, client)
            self.assertEqual(text, "甲乙两丁戊己")
            self.assertFalse(record["coverageReview"]["complete"])
            self.assertEqual(record["contentFidelityStatus"], "doubt")
            self.assertTrue(any("edits are kept" in d["detail"] for d in record["doubts"]))
            change = next(c for c in record["decisionChanges"] if c["decision"] == "contentFidelity")
            self.assertEqual((change["rule"], change["before"], change["after"], change["evidence"]["adopted"]),
                             ("coverage-review", "丙", "两", True))
            self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))
            # The doubt must be on the page for the record to stand.
            stripped = {**record, "doubts": []}
            with self.assertRaises(fidelity.FidelityError):
                fidelity.validate_completed_record(stripped, text, IMAGE_SHA)

    def test_a_v2_seal_without_the_switch_key_is_current_for_an_on_run_by_its_evidence(self):
        # A-R6: the skill sealed every page with the v2 questions before the
        # switch existed, so its seals carry a complete v2 record and no
        # contentFidelity key.  An on run judges the evidence, not the key.
        draft = "甲乙丙丁戊己"
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft, "--content-fidelity", "on")
            client = Fake(**{"page-inventory": inventory(), "format": lambda u, n: between(u),
                             "content-recovery": lambda u, n: review(json.loads(u)),
                             "coverage-review": lambda u, n: review(json.loads(u))})
            record, text = run(job, args, client)
            self.assertEqual(record["contentFidelityStatus"], "complete")
            old = json.loads(json.dumps(record))
            del old["provenance"][pp.CONTENT_FIDELITY_KEY]
            del old["contentFidelityStatus"]
            del old["provenance"]["jsonPayloadSha256"]
            old["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(old))
            job.out_json.write_bytes(pp.canonical_json_bytes(old))
            self.assertEqual(pp.validate_completion(job, expect(job, args)), (True, "current"))
            _, off = page(Path(tmp), draft, draft)
            self.assertEqual(pp.validate_completion(job, expect(job, off)), (True, "current"))
            # The evidence must still hold: a review bound to another text is refused.
            broken = json.loads(json.dumps(old))
            broken["coverageReview"]["result_sha256"] = "0" * 64
            del broken["provenance"]["jsonPayloadSha256"]
            broken["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(broken))
            job.out_json.write_bytes(pp.canonical_json_bytes(broken))
            current, reason = pp.validate_completion(job, expect(job, args))
            self.assertFalse(current)
            self.assertTrue(reason.startswith("content fidelity:"), reason)
            # A seal with no v2 record at all still is not current for an on run.
            bare = {key: value for key, value in old.items()
                    if key not in ("contentFidelityContract", "pageInventory", "coverageReview")}
            del bare["provenance"]["jsonPayloadSha256"]
            bare["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(bare))
            job.out_json.write_bytes(pp.canonical_json_bytes(bare))
            current, reason = pp.validate_completion(job, expect(job, args))
            self.assertFalse(current)
            self.assertIn("sealed without --content-fidelity on", reason)

    def test_binding_slip_in_an_answer_gets_its_one_correction(self):
        draft = "甲乙丙丁戊己"
        wrong = {**inventory(), "source_image_sha256": "0" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft, "--content-fidelity", "on")
            client = Fake(**{"page-inventory": wrong, "page-inventory-correction": inventory(),
                             "format": lambda u, n: between(u),
                             "content-recovery": lambda u, n: review(json.loads(u)),
                             "coverage-review": lambda u, n: review(json.loads(u))})
            record, text = run(job, args, client)
            self.assertEqual(client.kinds()[:2], ["page-inventory", "page-inventory-correction"])
            self.assertEqual(record["contentFidelityStatus"], "complete")
            self.assertEqual(record["pageInventory"]["correction"]["validation_error"]["code"], "stale-evidence")

    def test_stitched_page_stays_bound_to_its_coverage_review(self):
        text = "甲乙22"
        inv = fidelity.parse_inventory(json.dumps(inventory(features=[
            {"id": "f1", "region_id": "r1", "text": "22", "before": "乙", "after": ""}])), IMAGE)
        request = {"inventory": inv, "source_image_sha256": IMAGE_SHA,
                   "inventory_sha256": inv["inventory_sha256"], "candidate_sha256": fidelity.text_sha256(text)}
        _, coverage = fidelity.apply_review(json.dumps(review(request)), text, inv)
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "mode": "merge", "doubts": [],
                  "contentFidelityContract": fidelity.CONTRACT_VERSION, "contentFidelityStatus": "complete",
                  "pageInventory": inv, "coverageReview": coverage,
                  "provenance": {"renderSha256": IMAGE_SHA, "markdownSha256": pp.sha256_bytes(text.encode())}}
        fidelity.validate_completed_record(record, text, IMAGE_SHA)
        stitched = text + "\n\n" + TABLE
        sealed = stitch_tables.resealed(record, stitched, {"before": text, "chain": 1}, [])
        fidelity.validate_completed_record(sealed, stitched, IMAGE_SHA)
        self.assertEqual(sealed["tableStitch"]["coverageBinding"]["stitchedSha256"],
                         pp.sha256_bytes(stitched.encode()))
        with self.assertRaises(fidelity.FidelityError):
            fidelity.validate_completed_record(sealed, stitched + "改", IMAGE_SHA)
        unbound = json.loads(json.dumps(sealed))
        del unbound["tableStitch"]["coverageBinding"]
        with self.assertRaises(fidelity.FidelityError):
            fidelity.validate_completed_record(unbound, stitched, IMAGE_SHA)


class SealTests(unittest.TestCase):
    def sealed(self, root, provenance, record_extra=None, body="甲乙".encode()):
        job = pp.PageJob(1, root / "r.png", root / "a.txt", None, root / "b.txt", root / "p.json", root / "p.md")
        for path, data in ((job.render, IMAGE), (job.draft_text, "甲乙".encode()), (job.draft2_text, "甲乙".encode())):
            path.write_bytes(data)
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "doubts": [], **(record_extra or {}),
                  "provenance": {**provenance, "markdownSha256": pp.sha256_bytes(body)}}
        record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
        pp.seal_page(job, body, record)
        return job

    def test_q_seal_without_new_keys_is_current_but_its_integrity_is_still_checked(self):
        args = pp.build_parser().parse_args(["r", "d", "o", "--backend", "packet"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = self.sealed(root, {})
            wanted = {**pp.sealed_inputs(probe), **pp.decision_provenance(args)}
            legacy = {k: v for k, v in wanted.items()
                      if k not in pp.INFORMATIONAL_PROVENANCE and k not in ("tableOrientation", pp.DRAFT2_KEY)}
            job = self.sealed(root, legacy)
            self.assertEqual(pp.validate_completion(job, wanted), (True, "current"))
            self.assertFalse(pp.validate_completion(job, {**wanted, "tableOrientation": "as-printed"})[0])
            job.out_md.write_bytes("甲丙".encode())
            self.assertEqual(pp.validate_completion(job, wanted), (False, "markdown does not match its seal"))

    # Rule keys the old skill added over time; its pages sealed before one of
    # them lack it (variantPairs and agreedMarks on its books, seven SEALED_ON
    # keys and unreadTableCharacters on an older one).
    LATER_RULES = ("variantPairs", "agreedMarks", "unreadTableCharacters", "lopsidedSplit", "gridFollow")

    def test_old_skill_seal_made_before_later_rules_is_current_as_legacy(self):
        # User decision 5 (2026-11-14): the old skill's seals are accepted as
        # they are, with no proofreading again; the reason names the keys.
        args = pp.build_parser().parse_args(["r", "d", "o", "--backend", "packet"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = self.sealed(root, {})
            wanted = {**pp.sealed_inputs(probe), **pp.decision_provenance(args)}
            old = {k: v for k, v in wanted.items() if k not in self.LATER_RULES}
            job = self.sealed(root, old)
            current, reason = pp.validate_completion(job, wanted)
            self.assertTrue(current, reason)
            self.assertTrue(reason.startswith(pp.LEGACY_REASON), reason)
            for key in self.LATER_RULES:
                self.assertIn(key, reason)
            # A key that is there is compared as always.
            job = self.sealed(root, {**old, "variantPairs": "model"})
            self.assertEqual(pp.validate_completion(job, wanted), (False, "provenance variantPairs differs"))
            # Only the old skill's rule keys: a missing input is not legacy.
            job = self.sealed(root, {k: v for k, v in old.items() if k != "profileSha256"})
            self.assertEqual(pp.validate_completion(job, wanted), (False, "provenance profileSha256 differs"))
            # And the seal's integrity is still checked.
            job = self.sealed(root, old)
            job.out_md.write_bytes("甲丙".encode())
            self.assertEqual(pp.validate_completion(job, wanted), (False, "markdown does not match its seal"))

    def test_resume_takes_old_skill_seals_as_legacy_and_proofreads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("renders", "a", "b", "out"):
                (root / name).mkdir()
            (root / "renders" / "page-0001.png").write_bytes(IMAGE)
            for name in ("a", "b"):
                (root / name / "page-0001.txt").write_text("甲乙丙丁戊己", encoding="utf-8")
            (root / "profile.json").write_text(json.dumps({"preferEngine": "A"}), encoding="utf-8")
            arguments = [str(root / "renders"), str(root / "a"), str(root / "out"),
                         "--draft2-directory", str(root / "b"), "--profile", str(root / "profile.json"),
                         "--backend", "http", "--endpoint", "http://127.0.0.1:9/v1", "--model", "stand-in",
                         "--report", str(root / "run.json")]
            fake = Fake(format=lambda u, n: between(u))
            with mock.patch.object(pp, "Client", lambda *a, **k: fake), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main(arguments), 0)
            # Made old: sealed before the later rules, as the old skill sealed it.
            path = root / "out" / "page-0001.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            for key in (*self.LATER_RULES, "jsonPayloadSha256"):
                del record["provenance"][key]
            record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
            path.write_bytes(pp.canonical_json_bytes(record))
            before = path.read_bytes()

            def no_client(*a, **k):
                raise AssertionError("an all-current resume makes no client")

            said = io.StringIO()
            with mock.patch.object(pp, "Client", no_client), contextlib.redirect_stdout(said):
                self.assertEqual(pp.main(arguments), 0)
            run = json.loads((root / "run.json").read_text(encoding="utf-8"))
            self.assertEqual((run["counts"]["skippedCurrent"], run["counts"]["legacy"], run["counts"]["blocked"]),
                             (1, 1, 0))
            self.assertTrue(run["pages"][0]["legacy"].startswith(pp.LEGACY_REASON))
            self.assertIn("legacy seals", said.getvalue())
            self.assertNotIn("--overwrite", said.getvalue())
            self.assertEqual(path.read_bytes(), before)

    def test_engine_b_draft_hash_is_sealed_and_compared_when_present(self):
        args = pp.build_parser().parse_args(["r", "d", "o", "--backend", "packet"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = self.sealed(root, {})
            wanted = {**pp.sealed_inputs(probe), **pp.decision_provenance(args)}
            self.assertEqual(wanted[pp.DRAFT2_KEY], pp.sha256_file(probe.draft2_text))
            job = self.sealed(root, wanted)
            self.assertTrue(pp.validate_completion(job, wanted)[0])
            job.draft2_text.write_text("甲丙", encoding="utf-8")
            changed = {**wanted, **pp.sealed_inputs(job)}
            current, reason = pp.validate_completion(job, changed)
            self.assertFalse(current)
            self.assertIn("engine B", reason)
            # Sealed without engine B, which has since been added.
            single = self.sealed(root, {**wanted, pp.DRAFT2_KEY: ""})
            current, reason = pp.validate_completion(single, changed)
            self.assertEqual((current, reason), (False, "engine B's draft was added after the page was sealed"))
            # A legacy seal without the key is taken as it is.
            legacy = self.sealed(root, {k: v for k, v in wanted.items() if k != pp.DRAFT2_KEY})
            self.assertTrue(pp.validate_completion(legacy, changed)[0])

    def test_script_edit_blocks_neither_seals_nor_packet_answers(self):
        args = pp.build_parser().parse_args(["r", "d", "o", "--backend", "packet"])
        config = pp.packet_request_config(args)
        for key in pp.INFORMATIONAL_PROVENANCE:
            self.assertNotIn(key, config)
        with mock.patch.object(pp, "sha256_file", side_effect=lambda path: "edited-" + Path(path).name):
            self.assertEqual(pp.packet_request_config(args), config)
        with tempfile.TemporaryDirectory() as tmp:
            first = packets.PacketClient(Path(tmp), request_config=config)
            with self.assertRaises(packets.PacketPending) as before:
                first.ask_answering("system", "question", IMAGE, 64, kind="writer")
            self.assertNotIn("content_fidelity_sha256", first.request_config)
            with mock.patch.object(Path, "read_bytes", return_value=b"edited helper bytes"):
                second = packets.PacketClient(Path(tmp), request_config=pp.packet_request_config(args))
            with self.assertRaises(packets.PacketPending) as after:
                second.ask_answering("system", "question", IMAGE, 64, kind="writer")
            self.assertEqual(before.exception.request_id, after.exception.request_id)
            # The question, its model label, effort and budget still decide.
            for change in ({"model": "other"}, {"effort": "low"}):
                other = packets.PacketClient(Path(tmp), request_config=config, **change)
                with self.assertRaises(packets.PacketPending) as moved:
                    other.ask_answering("system", "question", IMAGE, 64, kind="writer")
                self.assertNotEqual(moved.exception.request_id, before.exception.request_id)


class TransportTests(unittest.TestCase):
    CLEAN = ("PROOFREAD_ENDPOINT", "PROOFREAD_MODEL", "PROOFREAD_API_KEY", "QWEN_CODE_MODEL",
             "QWEN_CODE_MODEL_IDENTITY", "PROOFREAD_MAX_INFLIGHT", pp.LOCAL_AGENT_ENV)

    def environment(self, **values):
        env = {k: v for k, v in os.environ.items() if k not in self.CLEAN}
        return mock.patch.dict(os.environ, {**env, **values}, clear=True)

    def parse(self, module, *options):
        positional = ["r", "d", "o"] if module is pp else ["r", "d", "d2", "p.json"]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                return module.build_parser().parse_args([*positional, *options]), ""
            except SystemExit:
                return None, err.getvalue()

    def test_endpoint_and_model_resolution(self):
        self.assertEqual(pp.endpoint_models_url("http://127.0.0.1:8090/v1/chat/completions"),
                         "http://127.0.0.1:8090/v1/models")
        self.assertEqual(pp.endpoint_models_url("https://host.invalid/proxy/v1/"), "https://host.invalid/proxy/v1/models")
        self.assertEqual(pp.endpoint_models_url("http://host.invalid:9"), "http://host.invalid:9/v1/models")
        for module in (pp, book_profile):
            with self.subTest(module=module.__name__):
                with self.environment(PROOFREAD_ENDPOINT="http://env.invalid/v1/chat/completions",
                                      PROOFREAD_MODEL="env-model"):
                    args, _ = self.parse(module, "--backend", "http")
                    self.assertEqual((args.endpoint, args.model), ("http://env.invalid/v1/chat/completions", "env-model"))
                    args, _ = self.parse(module, "--backend", "http", "--model", "flag-model")
                    self.assertEqual(args.model, "flag-model")
                with self.environment():
                    args, error = self.parse(module, "--backend", "http")
                    self.assertIsNone(args)
                    self.assertIn("PROOFREAD_ENDPOINT", error)
                    args, _ = self.parse(module, "--backend", "http", "--endpoint", "http://flag.invalid/v1")
                    self.assertIsNone(args.model)
        listed = io.BytesIO(json.dumps({"data": [{"id": "served-model"}, {"id": "second"}]}).encode())
        seen = []

        def models(request, timeout):
            seen.append(request.full_url)
            return listed
        with self.environment(), mock.patch.object(pp.urllib.request, "urlopen", side_effect=models):
            client = pp.Client("http://127.0.0.1:8090/v1/chat/completions", None, None, 5)
        self.assertEqual((client.model, seen), ("served-model", ["http://127.0.0.1:8090/v1/models"]))
        self.assertEqual(pp.Client("http://x.invalid/v1", "named", None, 5).model, "named")
        with self.environment(), mock.patch.object(pp.urllib.request, "urlopen",
                                                   side_effect=pp.urllib.error.URLError("refused")):
            with self.assertRaisesRegex(pp.ProofreadError, "pass --model"):
                pp.Client("http://127.0.0.1:9/v1/chat/completions", None, None, 5)
        with self.environment(), mock.patch.object(pp.urllib.request, "urlopen",
                                                   return_value=io.BytesIO(b'{"data": []}')):
            with self.assertRaisesRegex(pp.ProofreadError, "names none"):
                pp.Client("http://127.0.0.1:9/v1/chat/completions", None, None, 5)

    def test_http_main_without_any_model_fails_clearly_before_any_page(self):
        with tempfile.TemporaryDirectory() as tmp, self.environment():
            root = Path(tmp)
            (root / "renders").mkdir()
            (root / "drafts").mkdir()
            (root / "renders" / "page-0001.png").write_bytes(IMAGE)
            (root / "drafts" / "page-0001.txt").write_text("甲乙", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err, \
                    mock.patch.object(pp.urllib.request, "urlopen", side_effect=pp.urllib.error.URLError("refused")):
                code = pp.main([str(root / "renders"), str(root / "drafts"), str(root / "out"), "--single-engine",
                                "--backend", "http", "--endpoint", "http://127.0.0.1:9/v1/chat/completions"])
            self.assertEqual(code, 2)
            self.assertIn("pass --model", err.getvalue())
            self.assertEqual(list((root / "out").iterdir()), [])

    def test_local_agent_gets_http_by_default_and_packets_refused(self):
        local = {"QWEN_CODE_MODEL": "local-27b", "PROOFREAD_MODEL": "local-27b",
                 "PROOFREAD_ENDPOINT": "http://127.0.0.1:8090/v1/chat/completions"}
        for module in (pp, book_profile):
            with self.subTest(module=module.__name__):
                with self.environment(**local):
                    args, _ = self.parse(module)
                    self.assertEqual((args.backend, args.endpoint, args.model),
                                     ("http", local["PROOFREAD_ENDPOINT"], "local-27b"))
                    self.assertTrue(args.agent_is_local_model)
                    args, error = self.parse(module, "--backend", "packet")
                    self.assertIsNone(args)
                    self.assertIn("local proofreading model", error)
                    self.assertIn("--backend http", error)
                for env in ({**local, pp.LOCAL_AGENT_ENV: "0"},
                            {**local, "PROOFREAD_ENDPOINT": "https://remote.invalid/v1/chat/completions"},
                            {**local, "QWEN_CODE_MODEL": "another-model"},
                            {k: v for k, v in local.items() if k != "QWEN_CODE_MODEL"}):
                    with self.environment(**env):
                        args, _ = self.parse(module)
                        self.assertEqual(args.backend, "packet", env)
                with self.environment(**{pp.LOCAL_AGENT_ENV: "1",
                                         "PROOFREAD_ENDPOINT": "http://localhost:8090/v1/chat/completions"}):
                    self.assertIsNone(self.parse(module, "--backend", "packet")[0])
                    self.assertEqual(self.parse(module)[0].backend, "http")
                with self.environment(**{pp.LOCAL_AGENT_ENV: "yes"}):
                    args, error = self.parse(module)
                    self.assertIsNone(args)
                    self.assertIn("must be 1 or 0", error)
        for endpoint, loopback in (("http://[::1]:8090/v1", True), ("http://127.1.2.3/v1", True),
                                   ("http://app.localhost/v1", True), ("http://10.0.0.2:8090/v1", False),
                                   ("", False)):
            self.assertEqual(pp.loopback_endpoint(endpoint), loopback, endpoint)

    def test_the_server_concurrency_in_env_sh_is_the_http_parallelism_default(self):
        """E-R10: install.py --local-model-concurrency writes PROOFREAD_MAX_INFLIGHT;
        book_profile.py --workers (over HTTP) and proofread_pages.py
        --max-inflight take it when no number is given."""
        endpoint = "http://127.0.0.1:8090/v1/chat/completions"
        for value, http_workers in (("2", 2), (None, book_profile.DEFAULT_WORKERS)):
            env = {"PROOFREAD_ENDPOINT": endpoint, **({"PROOFREAD_MAX_INFLIGHT": value} if value else {})}
            with self.subTest(env=env), self.environment(**env):
                args, _ = self.parse(book_profile, "--backend", "http")
                self.assertIsNone(args.workers)
                self.assertEqual(book_profile.default_workers(args.backend), http_workers)
                # Packets keep their default; a number given wins.
                self.assertEqual(book_profile.default_workers(self.parse(book_profile)[0].backend),
                                 book_profile.DEFAULT_WORKERS)
                self.assertEqual(self.parse(book_profile, "--backend", "http", "--workers", "6")[0].workers, 6)
                self.assertEqual(self.parse(pp, "--backend", "http")[0].max_inflight, int(value) if value else None)
        # profile_book resolves the default before anything else, and names a bad value.
        with self.environment(PROOFREAD_ENDPOINT=endpoint, PROOFREAD_MAX_INFLIGHT="eight"):
            args, _ = self.parse(book_profile, "--backend", "http")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(book_profile.profile_book(args), 2)
            self.assertIn("PROOFREAD_MAX_INFLIGHT", err.getvalue())
        with self.environment(PROOFREAD_ENDPOINT=endpoint, PROOFREAD_MAX_INFLIGHT="3"), \
                tempfile.TemporaryDirectory() as directory:
            args, _ = self.parse(book_profile, "--backend", "http")
            args.render_directory = args.draft_directory = args.draft2_directory = Path(directory)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(book_profile.profile_book(args), 1)  # no page: stops before any call
            self.assertEqual(args.workers, 3)

    def test_rule_set_b_run_sheet_runs_http_when_the_agent_is_not_recognised(self):
        """SKILL.md's rule set B does not rest on the detection alone.  Qwen
        Code running the local model under another spelling of its id than
        PROOFREAD_MODEL, or with no PROOFREAD_MODEL, is not recognised, and
        its book profile and phase 3 would write packets for it to answer
        with the page images in its own context.  So the run sheet's rule
        set B lines (四, steps 3 and 4) add --backend http, 三's prefix for
        an agent that is the local model exports JYUT_OCR_AGENT_IS_LOCAL_MODEL=1
        in every call (a fresh shell loses an export from the call before),
        and 五 says an exit 10 or 11 is never answered under rule set B."""
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")

        def part(number):
            return skill.split(f"\n## {number}、", 1)[1].split("\n## ", 1)[0]
        sets, folder, run, waits = part("一"), part("三"), part("四"), part("五")
        unrecognised = {"QWEN_CODE_MODEL": "qwen3.8-27b", "PROOFREAD_MODEL": "Qwen3.8-27B-NVFP4",
                        "PROOFREAD_ENDPOINT": "http://127.0.0.1:8090/v1/chat/completions"}
        environments = (unrecognised, {k: v for k, v in unrecognised.items() if k != "PROOFREAD_MODEL"})

        def parse(module, arguments):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                try:
                    return module.build_parser().parse_args(arguments), ""
                except SystemExit:
                    return None, err.getvalue()
        steps = {"3": ("book_profile.py", book_profile), "4": ("proofread_pages.py", pp)}
        for number, (script, module) in steps.items():
            line = next(line for line in run.splitlines() if line.startswith(f"{number}. "))
            command = re.search(rf'`"\$JYUT_OCR_PYTHON" scripts/{re.escape(script)} ([^`>]*)', line)
            extra = re.search(r"B 套加 `([^`]+)`", line)
            self.assertTrue(command and extra, line)
            self.assertIn("--backend http", extra.group(1), line)
            arguments = command.group(1).split() + extra.group(1).replace(" N", " 4").split()
            for env in environments:
                with self.subTest(step=number, env=env), self.environment(**env):
                    args, error = parse(module, arguments)
                    self.assertIsNotNone(args, error)
                    self.assertEqual((args.backend, args.endpoint), ("http", unrecognised["PROOFREAD_ENDPOINT"]))
        prefix = re.search(r'`\. "\$\{XDG_CONFIG_HOME:-\$HOME/\.config\}/jyut-ocr/env\.sh"; '
                           r"export ([A-Z_]+)=(\w+); cd SKILL_DIR`", folder)
        self.assertTrue(prefix, folder)
        self.assertEqual(prefix.group(1), pp.LOCAL_AGENT_ENV)
        for script, module in steps.values():
            for env in environments:
                with self.subTest(script=script, env=env), self.environment(**env, **{prefix.group(1): prefix.group(2)}):
                    positional = ["r", "d", "o"] if module is pp else ["r", "d", "d2", "p.json"]
                    args, error = parse(module, [*positional, "--backend", "packet"])
                    self.assertIsNone(args)
                    self.assertIn("local proofreading model", error)
                    self.assertEqual(parse(module, positional)[0].backend, "http")
        self.assertIn("`book_profile.py`、`proofread_pages.py` 每次都加", sets)
        self.assertIn("`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`（每個 call 照三加）", sets)
        pending = next(line for line in waits.splitlines() if line.strip().startswith("- **exit 10**"))
        self.assertIn("**唔准答 bundle**", pending)
        self.assertIn("加 `--backend http` 再跑同一步", pending)
        both = next(line for line in waits.splitlines() if line.strip().startswith("- **exit 11**"))
        self.assertIn("B 套同 exit 10", both)


if __name__ == "__main__":
    unittest.main()
