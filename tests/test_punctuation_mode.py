"""Backend-independent punctuation fusion; synthetic answers, no model calls.

The frozen request ID below was captured from the pre-extraction packet wrapper.
These checks exercise fallback and the existing content guards, not OCR accuracy.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _packet_client as packets
import _punctuation_fusion as fusion
import proofread_pages as pp
import stand_in

SPAN = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽"


def fused_answer(body=SPAN + "。", census=1, doubled=0):
    return f"符號：{census}\n雙圈：{doubled}\n形狀：。\n{fusion.FUSED_DELIMITER}\n{body}"


class Answers:
    model, effort = "synthetic", "xhigh"

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other", **kwargs):
        self.calls.append((kind, system, user_text))
        value = self.answers.get(kind, "")
        if isinstance(value, list):
            value = value.pop(0)
        return value, {"kind": kind, "effort": "xhigh"}

    def kinds(self):
        return [row[0] for row in self.calls]


def run_part(mode="fused", backend="http", *, leaders=None, span=SPAN, block=None, **answers):
    args = SimpleNamespace(max_tokens=1000, profile_data=None, punctuation_mode=mode, backend=backend)
    client = Answers(**answers)
    with mock.patch.object(pp, "leader_count", return_value=leaders):
        result = pp.punctuate_part(client, args, None, span, "", 60.0, block=block)
    return client, result


class PunctuationModes(unittest.TestCase):
    def test_defaults_and_explicit_overrides_for_both_backends(self):
        for backend, expected in (("packet", "fused"), ("http", "split")):
            for mode in ("auto", "split", "fused"):
                with self.subTest(backend=backend, mode=mode):
                    args = pp.build_parser().parse_args(["r", "a", "o", "--backend", backend,
                                                        "--punctuation-mode", mode])
                    self.assertEqual(pp.punctuation_mode(args), expected if mode == "auto" else mode)
        args = pp.build_parser().parse_args(["r", "a", "o"])
        self.assertEqual(args.punctuation_mode, "auto")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pp.build_parser().parse_args(["r", "a", "o", "--punctuation-mode", "fast"])

    def test_fused_one_call_and_split_two_calls_keep_identical_text(self):
        for backend in ("packet", "http"):
            with self.subTest(backend=backend):
                fused, (text, record, used) = run_part(backend=backend,
                    **{"census+punctuate": fused_answer()})
                split, (plain, separate, _) = run_part("split", backend,
                    census="符號：1\n雙圈：0\n形狀：。", punctuate=SPAN + "。")
                self.assertEqual(text, plain)
                self.assertTrue(record["ok"] and separate["ok"])
                self.assertEqual(fused.kinds(), ["census+punctuate"])
                self.assertEqual(split.kinds(), ["census", "punctuate"])
                self.assertEqual(len(used), 1)
                self.assertTrue(record["fusion"]["success"])

    def test_auto_obeys_the_backend_and_not_model_label(self):
        for backend, expected in (("packet", ["census+punctuate"]), ("http", ["census"])):
            client, (text, _, _) = run_part("auto", backend, census="符號：0\n形狀：無",
                                          **{"census+punctuate": fused_answer(SPAN, 0)})
            self.assertEqual(client.kinds(), expected)
            self.assertEqual(text, SPAN)

    def test_malformed_fused_answers_fall_back_to_independent_count_and_insertion(self):
        bad = ["符號：1\n雙圈：0\n形狀：。\n" + SPAN + "。",
               f"雙圈：0\n形狀：。\n{fusion.FUSED_DELIMITER}\n{SPAN}。",
               fused_answer(census="不詳"), fused_answer(census=999),
               f"符號：1\n雙圈：0\n形狀：。\n{fusion.FUSED_DELIMITER}\n"]
        for raw in bad:
            with self.subTest(raw=raw):
                client, (text, record, used) = run_part(census="符號：1\n雙圈：0\n形狀：。",
                    punctuate=SPAN + "。", **{"census+punctuate": raw})
                self.assertEqual(text, SPAN + "。")
                self.assertTrue(record["ok"])
                self.assertEqual(client.kinds(), ["census+punctuate", "census", "punctuate"])
                self.assertEqual(len(used), 3)
                self.assertTrue(record["fusion"]["fallbackReason"])
                self.assertFalse(record["fusion"]["success"])

    def test_zero_marks_and_legacy_zero_header_do_not_invent_marks(self):
        for raw in (fused_answer(SPAN, 0), "符號：0\n雙圈：0\n形狀：無"):
            client, (text, record, _) = run_part(**{"census+punctuate": raw})
            self.assertEqual((text, record["census"], record["marks"]), (SPAN, 0, 0))
            self.assertEqual(client.kinds(), ["census+punctuate"])

    def test_json_answer_has_same_count_and_body(self):
        raw = json.dumps({"census": "符號：1\n雙圈：0\n形狀：。", "punctuated": SPAN + "。"}, ensure_ascii=False)
        client, (text, record, _) = run_part(**{"census+punctuate": raw})
        self.assertEqual(text, SPAN + "。")
        self.assertTrue(record["ok"])
        self.assertEqual(client.kinds(), ["census+punctuate"])

    def test_changed_character_still_requires_bounded_retry(self):
        client, (text, record, _) = run_part(**{
            "census+punctuate": fused_answer(SPAN.replace("玄", "元") + "。"),
            "punctuate-retry": SPAN + "。"})
        self.assertEqual(text, SPAN + "。")
        self.assertTrue(record["ok"])
        self.assertEqual(client.kinds(), ["census+punctuate", "punctuate-retry"])
        self.assertIn("characters", record["reason"])

    def test_both_character_answers_rejected_leave_original_characters(self):
        wrong = SPAN.replace("玄", "元") + "。"
        client, (text, record, _) = run_part(**{"census+punctuate": fused_answer(wrong), "punctuate-retry": wrong})
        self.assertEqual(text, SPAN)
        self.assertFalse(record["ok"])
        self.assertEqual(len(record["refusedOutputs"]), 2)
        self.assertEqual(client.kinds(), ["census+punctuate", "punctuate-retry"])

    def test_count_mismatch_uses_existing_retry(self):
        right = "".join(SPAN[i:i + 4] + "。" for i in range(0, len(SPAN), 4))
        client, (text, record, _) = run_part(**{
            "census+punctuate": fused_answer(census=8), "punctuate-retry": right})
        self.assertEqual(text, right)
        self.assertEqual(client.kinds(), ["census+punctuate", "punctuate-retry"])
        self.assertIn("counted", record["reason"])

    def test_double_circle_shape_retry_is_retained(self):
        single = "".join(SPAN[i:i + 8] + "。" for i in range(0, len(SPAN), 8))
        double = single.replace("。", "。。")
        client, (text, record, _) = run_part(**{
            "census+punctuate": fused_answer(single, 4, 4), "punctuate-shape": double})
        self.assertEqual(text, double)
        self.assertEqual(client.kinds(), ["census+punctuate", "punctuate-shape"])
        self.assertTrue(record["shapeRetry"]["kept"])
        self.assertEqual((record["places"], record["doubled"]), (4, 4))

    def test_image_leaders_override_zero_model_count(self):
        body = SPAN[:16] + "……" + SPAN[16:] + "……"
        client, (text, record, _) = run_part(leaders=2, **{"census+punctuate": fused_answer(body, 0)})
        self.assertEqual(text, body)
        self.assertEqual((record["modelCensus"], record["census"], record["leaders"]), (0, 2, 2))
        self.assertEqual(client.kinds(), ["census+punctuate"])

    def test_realign_guard_seats_variant_reading_on_original_characters(self):
        client, (text, record, _) = run_part(block=(SPAN, 0, len(SPAN), 0), **{
            "census+punctuate": fused_answer(SPAN.replace("黃", "黄") + "。")})
        self.assertEqual(text, SPAN + "。")
        self.assertTrue(record["ok"])
        self.assertIn("realigned", record)
        self.assertEqual(client.kinds(), ["census+punctuate"])


class RunReporting(unittest.TestCase):
    def test_run_fusion_summary_covers_current_and_completed_seals_across_replays(self):
        class Model(stand_in.StandIn):
            def __init__(self):
                super().__init__(census=1, punctuate=lambda text: text + "。")
                self.fused_calls = 0

            def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other", **kwargs):
                if kind == "census+punctuate":
                    self.fused_calls += 1
                    self.calls.append((kind, user_text))
                    span = stand_in.between(user_text)
                    # One valid reply, one independently recovered bad frame.
                    raw = fused_answer(span + "。") if self.fused_calls == 1 else "bad frame"
                    return raw, {"kind": kind, "effort": "xhigh"}
                return super().ask_answering(system, user_text, image_bytes, max_tokens, kind)

        block = stand_in.block(SPAN, [0.1, 0.1, 0.8, 0.7])
        with stand_in.book({1: ([block], SPAN), 2: ([block], SPAN)}) as root:
            model = Model()
            with contextlib.redirect_stdout(io.StringIO()):
                pages = stand_in.run(root, model, "1,2", extra=("--punctuation-mode", "fused", "--page-concurrency", "1"))
            self.assertEqual(model.fused_calls, 2)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual((report["punctuationMode"], report["punctuationModeEffective"]), ("fused", "fused"))
            self.assertEqual(report["punctuationFusion"], {
                "attempts": 2, "successes": 1, "fallbacks": 1, "fallbackReasons": {"missing-delimiter": 1}})
            self.assertEqual(sorted(row[0]["punctuationFusion"]["successes"] for row in pages.values()), [0, 1])
            self.assertEqual(sorted(row[0]["punctuationFusion"]["fallbacks"] for row in pages.values()), [0, 1])
            self.assertEqual(pages[1][1], pages[2][1])
            # Fusion is selected-page evidence, including earlier waves;
            # calls and completed counts remain per invocation.
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(pp, "Client", return_value=model):
                rc = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                    "--draft2-directory", str(root / "b"), "--profile", str(root / "profile.json"),
                    "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, "--pages", "1,2",
                    "--punctuation-mode", "fused", "--report", str(root / "report-resume.json")])
            self.assertEqual(rc, 0)
            resumed = json.loads((root / "report-resume.json").read_text())
            self.assertEqual(resumed["counts"]["skippedCurrent"], 2)
            self.assertEqual(resumed["punctuationFusion"], report["punctuationFusion"])
            self.assertEqual(resumed["counts"]["completed"], 0)
            self.assertEqual(model.fused_calls, 2)
            # A later wave can have one current page and one just completed.
            (root / "out" / "page-0002.json").unlink()
            (root / "out" / "page-0002.md").unlink()
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(pp, "Client", return_value=model):
                rc = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                    "--draft2-directory", str(root / "b"), "--profile", str(root / "profile.json"),
                    "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, "--pages", "1,2",
                    "--punctuation-mode", "fused", "--report", str(root / "report-mixed.json")])
            self.assertEqual(rc, 0)
            mixed = json.loads((root / "report-mixed.json").read_text())
            self.assertEqual((mixed["counts"]["skippedCurrent"], mixed["counts"]["completed"]), (1, 1))
            kept_success = pages[1][0]["punctuationFusion"]["successes"]
            self.assertEqual(mixed["punctuationFusion"], {
                "attempts": 2, "successes": kept_success, "fallbacks": 2 - kept_success,
                "fallbackReasons": {"missing-delimiter": 2 - kept_success}})
            self.assertEqual(model.fused_calls, 3)


class HistoricalPacketCompatibility(unittest.TestCase):
    def kwargs(self):
        return dict(crop_bytes=None, span="甲乙丙丁", hint="保留字形。", leaders=2,
                    show_doubled=True, max_tokens=256, census_system="census-sys", punct_system="punct-sys")

    def test_common_helper_and_packet_wrapper_keep_frozen_request_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("wrapper", "shared"):
                client = packets.PacketClient(Path(tmp) / name)
                with self.assertRaises(packets.PacketPending):
                    if name == "wrapper":
                        client.fused_census_and_punctuate(**self.kwargs())
                    else:
                        fusion.ask_fused(client, **self.kwargs())
                self.assertEqual(client.pending_items()[0].request_id, "d2ed8894b9e0165d8dab31f8")

    def test_old_answer_replays_without_new_pending_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {"request_id": "d2ed8894b9e0165d8dab31f8", "answer": fused_answer("甲乙丙丁。。", 1, 1)}
            (root / "answers.jsonl").write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
            client = packets.PacketClient(root)
            census, body, _ = fusion.ask_fused(client, **self.kwargs())
            self.assertIn("雙圈：1", census)
            self.assertEqual(body, "甲乙丙丁。。")
            self.assertEqual(client.pending_count(), 0)

    def test_packet_auto_and_explicit_fused_keep_old_caller_configuration(self):
        auto = pp.build_parser().parse_args(["r", "a", "o", "--backend", "packet"])
        fused = pp.build_parser().parse_args(["r", "a", "o", "--backend", "packet", "--punctuation-mode", "fused"])
        split = pp.build_parser().parse_args(["r", "a", "o", "--backend", "packet", "--punctuation-mode", "split"])
        self.assertEqual(pp.packet_request_config(auto), pp.packet_request_config(fused))
        self.assertNotIn("punctuationMode", pp.packet_request_config(auto))
        self.assertEqual(pp.packet_request_config(split)["punctuationMode"], "split")


class ModeSeals(unittest.TestCase):
    def args(self, backend="packet", mode="auto"):
        return pp.build_parser().parse_args(["r", "a", "o", "--backend", backend, "--punctuation-mode", mode])

    def sealed(self, root, provenance):
        body = "甲乙".encode()
        job = pp.PageJob(1, root / "r.png", root / "a.txt", None, root / "b.txt", root / "p.json", root / "p.md")
        record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "doubts": [],
                  "provenance": {**provenance, "markdownSha256": pp.sha256_bytes(body)}}
        record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
        pp.seal_page(job, body, record)
        return job

    def test_effective_mode_changes_block_seals_but_auto_alias_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = pp.decision_provenance(self.args())
            job = self.sealed(Path(tmp), old)
            self.assertTrue(pp.validate_completion(job, pp.decision_provenance(self.args(mode="fused")))[0])
            for backend, mode in (("packet", "split"), ("http", "auto")):
                ok, reason = pp.validate_completion(job, pp.decision_provenance(self.args(backend, mode)))
                self.assertFalse(ok)
                self.assertIn("punctuation", reason.lower())

    def test_old_seal_uses_recorded_transport_to_identify_mode(self):
        for backend, old_mode, other in (("packet", "fused", "split"), ("http", "split", "fused")):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                provenance = pp.decision_provenance(self.args(backend))
                for key in ("punctuationMode", "punctuationModeRequested"):
                    provenance.pop(key, None)
                job = self.sealed(Path(tmp), provenance)
                self.assertTrue(pp.validate_completion(job, pp.decision_provenance(self.args(backend, old_mode)))[0])
                self.assertFalse(pp.validate_completion(job, pp.decision_provenance(self.args(backend, other)))[0])

    def test_unidentified_legacy_seal_is_auto_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            provenance = pp.decision_provenance(self.args())
            for key in ("punctuationMode", "punctuationModeRequested", "proofreadTransport"):
                provenance.pop(key, None)
            job = self.sealed(Path(tmp), provenance)
            self.assertTrue(pp.validate_completion(job, pp.decision_provenance(self.args()))[0])
            for mode in ("split", "fused"):
                self.assertFalse(pp.validate_completion(job, pp.decision_provenance(self.args(mode=mode)))[0])


class HttpEffort(unittest.TestCase):
    def test_fused_kind_uses_its_policy_and_retry_stays_at_global_ceiling(self):
        seen = []

        def respond(body):
            seen.append(body.get("reasoning_effort"))
            return stand_in.reply(fused_answer())

        endpoint = stand_in.served().endpoint(respond)
        client = pp.Client(endpoint, "stand-in", "xhigh", 30,
                           effort_policy=pp.parse_effort_policy("census+punctuate=low"))
        fusion.ask_fused(client, crop_bytes=None, span=SPAN, hint="", leaders=None,
                         show_doubled=True, max_tokens=1000, census_system=pp.CENSUS_SYSTEM,
                         punct_system=pp.PUNCT_SYSTEM)
        client.ask_answering(pp.PUNCT_SYSTEM, "retry", None, 1000, kind="punctuate-retry")
        self.assertEqual(seen, ["low", "xhigh"])


if __name__ == "__main__":
    unittest.main()
