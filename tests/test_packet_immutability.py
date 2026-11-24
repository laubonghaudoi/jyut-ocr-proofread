"""Synthetic packet transport regressions: no model/OCR service is involved."""
from __future__ import annotations

import contextlib
import contextvars
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jyut_ocr.proofread import packet_client as pc
import apply_packet_answers as apply
import book_profile
import proofread_pages as pp


class PacketImmutabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "packets"

    def queue(self, client, text="question", max_tokens=128, start=""):
        with self.assertRaises(pc.PacketPending) as pending:
            client.ask_answering("system", text, b"synthetic-image", max_tokens,
                                 kind="coverage-review", start=start)
        return pending.exception.request_id

    def wave(self, count=1, **options):
        client = pc.PacketClient(self.root, **options)
        ids = [self.queue(client, f"question-{i}") for i in range(count)]
        result = client.write_wave_bundles()
        return ids, Path(result["waveDir"])

    def answers(self, wave, rows):
        path = wave / "answers-01.json"
        path.write_text(json.dumps({"answers": rows}, ensure_ascii=False))
        return path

    def run_apply(self, *sources, success=True, extra=()):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = apply.main([str(self.root), *(str(p) for p in sources), *extra])
        self.assertEqual(code, 0 if success else 2)

    def test_repeated_wave_is_byte_identical_even_with_new_schedule(self):
        ids, wave = self.wave(3, max_bundles=2)
        sealed = {p.name: p.read_bytes() for p in wave.iterdir()}
        client = pc.PacketClient(self.root, max_bundles=8)
        for i in reversed(range(3)):
            self.queue(client, f"question-{i}")
        with mock.patch.object(pc, "_utc_now", return_value="2099-01-01T00:00:00Z"):
            result = client.write_wave_bundles()
        self.assertEqual(result["waveDir"], str(wave))
        self.assertEqual(sealed, {p.name: p.read_bytes() for p in wave.iterdir()})
        self.assertEqual(result["pending"], len(ids))

    def test_request_ids_include_content_controls_exclude_bundle_schedule(self):
        baseline = self.queue(pc.PacketClient(self.root, model="model", effort="high"))
        same = self.queue(pc.PacketClient(self.root, model="model", effort="high", max_bundles=1))
        self.assertEqual(baseline, same)
        variants = [
            self.queue(pc.PacketClient(self.root, model="other", effort="high")),
            self.queue(pc.PacketClient(self.root, model="model", effort="low")),
            self.queue(pc.PacketClient(self.root, model="model", effort="high"), max_tokens=129),
            self.queue(pc.PacketClient(self.root, model="model", effort="high"), start="low"),
            self.queue(pc.PacketClient(self.root, model="model", effort="high", request_config={"temperature": 0.7})),
        ]
        self.assertEqual(len(set([baseline, *variants])), 6)
        with mock.patch.object(pc, "REQUEST_CONTRACT_VERSION", 999):
            self.assertNotEqual(baseline, self.queue(pc.PacketClient(self.root, model="model", effort="high")))

    def test_helper_drift_invalidates_id(self):
        first = pc.PacketClient(self.root)
        before = self.queue(first)
        second = pc.PacketClient(self.root)
        second.request_config["content_fidelity_sha256"] = "new-helper"
        self.assertNotEqual(before, self.queue(second))

    def test_legacy_answers_are_readable_but_cannot_answer_current_request(self):
        self.root.mkdir()
        image_sha = hashlib.sha256(b"synthetic-image").hexdigest()
        legacy = hashlib.sha256(f"v1\0coverage-review\0\0system\0question\0{image_sha}".encode()).hexdigest()[:24]
        (self.root / "answers.jsonl").write_text(json.dumps({"request_id": legacy, "answer": "old"}) + "\n")
        client = pc.PacketClient(self.root)
        self.assertIn(legacy, client.answers)
        self.assertNotEqual(legacy, self.queue(client))

    def test_same_answer_is_idempotent_and_conflict_preserves_old_bytes(self):
        ids, wave = self.wave()
        self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        self.run_apply(wave)
        checkpoint = self.root / "answers.jsonl"
        old_bytes = checkpoint.read_bytes()
        self.run_apply(wave)
        self.assertEqual(checkpoint.read_bytes(), old_bytes)
        self.answers(wave, [{"request_id": ids[0], "answer": "乙"}])
        self.run_apply(wave, success=False)
        self.assertEqual(checkpoint.read_bytes(), old_bytes)

    def test_duplicates_within_answer_bundle_rejected_even_if_equal(self):
        ids, wave = self.wave()
        answer = {"request_id": ids[0], "answer": "甲"}
        self.answers(wave, [answer, answer])
        self.run_apply(wave, success=False)
        self.assertFalse((self.root / "answers.jsonl").exists())

    def test_duplicate_json_mapping_keys_rejected(self):
        ids, wave = self.wave()
        (wave / "answers-01.json").write_text('{"' + ids[0] + '": "甲", "' + ids[0] + '": "乙"}')
        self.run_apply(wave, success=False)

    def test_duplicate_ids_across_submitted_files_rejected(self):
        ids, wave = self.wave()
        path = self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        (wave / "answers-02.json").write_bytes(path.read_bytes())
        self.run_apply(wave, success=False)

    def test_duplicate_request_bundle_rejected(self):
        ids, wave = self.wave()
        bundle = wave / "bundle-01.json"
        data = json.loads(bundle.read_text())
        data["items"].append(data["items"][0])
        data["schema_version"] = 1
        bundle.write_text(json.dumps(data))
        # Exercise structural duplicate validation independently of the seal.
        (wave / "manifest.json").unlink()
        with self.assertRaisesRegex(apply.ApplyAnswersError, "duplicate request_id"):
            apply.load_wave_items(wave)

    def test_sealed_bundle_modification_rejected_by_replay_and_apply(self):
        ids, wave = self.wave()
        bundle = wave / "bundle-01.json"
        bundle.write_bytes(bundle.read_bytes() + b" ")
        client = pc.PacketClient(self.root)
        self.queue(client, "question-0")
        with self.assertRaisesRegex(ValueError, "sealed wave bundle changed"):
            client.write_wave_bundles()
        self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        self.run_apply(wave, success=False)

    def test_invalid_published_manifest_never_allocates_replacement_wave(self):
        _, wave = self.wave()
        manifest = wave / "manifest.json"
        original = manifest.read_bytes()
        for value in (b"{broken", b"[]", b'{"request_ids":[1]}', None):
            with self.subTest(value=value):
                if value is None:
                    manifest.unlink()
                else:
                    manifest.write_bytes(value)
                client = pc.PacketClient(self.root)
                self.queue(client, "question-0")
                with self.assertRaisesRegex(ValueError, "invalid published wave manifest"):
                    client.write_wave_bundles()
                self.assertEqual(list(self.root.glob("wave-*")), [wave])
                with self.assertRaises(apply.ApplyAnswersError):
                    apply.load_wave_items(wave)
                manifest.write_bytes(original)

    def test_duplicate_key_checkpoint_is_not_treated_as_crash_truncation(self):
        self.root.mkdir()
        path = self.root / "answers.jsonl"
        damaged = ('{"request_id":"' + 'a' * 24 + '","answer":"甲","answer":"乙"}\n').encode()
        path.write_bytes(damaged)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            pc.load_answers_map(path)
        self.assertEqual(path.read_bytes(), damaged)

    def test_unknown_usage_stays_null_and_replay_is_not_charged(self):
        ids, wave = self.wave()
        self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        self.run_apply(wave)
        ledger_var = contextvars.ContextVar("packet-test-ledger", default=None)
        ledger = []
        token = ledger_var.set(ledger)
        self.addCleanup(ledger_var.reset, token)
        client = pc.PacketClient(self.root, call_ledger_var=ledger_var)
        for _ in range(2):
            answer, metrics = client.ask_answering("system", "question-0", b"synthetic-image", 128,
                                                   kind="coverage-review")
            self.assertEqual(answer, "甲")
            self.assertEqual(metrics["request_id"], ids[0])
            self.assertTrue(metrics["reused"])
            self.assertFalse(metrics["charged"])
            for key in pc.USAGE_FIELDS + pc.TIMING_FIELDS:
                self.assertIsNone(metrics[key], key)
        self.assertEqual(len(ledger), 1)
        for key in pc.USAGE_FIELDS + pc.TIMING_FIELDS:
            self.assertIsNone(ledger[0][key], key)

    def test_known_usage_and_subsets_survive_replay(self):
        ids, wave = self.wave()
        usage = {"prompt_tokens": 80, "completion_tokens": 30,
                 "prompt_tokens_details": {"cached_tokens": 50},
                 "completion_tokens_details": {"reasoning_tokens": 20}}
        self.answers(wave, [{"request_id": ids[0], "answer": "甲", "usage": usage,
                             "response_id": "provider-response", "finish_reason": "stop"}])
        self.run_apply(wave)
        _, metrics = pc.PacketClient(self.root).ask_answering("system", "question-0", b"synthetic-image", 128,
                                                             kind="coverage-review")
        self.assertEqual([metrics[k] for k in pc.USAGE_FIELDS], [80, 30, 50, 20])
        self.assertEqual(metrics["response_id"], "provider-response")
        self.assertFalse(metrics["charged"])
        self.assertEqual(metrics["finish_reason"], "stop")

    def test_conflicting_usage_for_same_answer_rejected_without_change(self):
        ids, wave = self.wave()
        self.answers(wave, [{"request_id": ids[0], "answer": "甲", "prompt_tokens": 10}])
        self.run_apply(wave)
        checkpoint = self.root / "answers.jsonl"
        before = checkpoint.read_bytes()
        self.answers(wave, [{"request_id": ids[0], "answer": "甲", "prompt_tokens": 11}])
        self.run_apply(wave, success=False)
        self.assertEqual(checkpoint.read_bytes(), before)

    def test_shared_response_conflicting_usage_rejected_before_any_write(self):
        ids, wave = self.wave(2)
        self.answers(wave, [{"request_id": rid, "answer": "甲", "response_id": "same-response",
                             "prompt_tokens": n} for rid, n in zip(ids, (10, 11))])
        self.run_apply(wave, success=False)
        self.assertFalse((self.root / "answers.jsonl").exists())

    def test_usage_subset_constraints_and_duplicate_fields(self):
        for usage in ({"prompt_tokens": 1, "cached_tokens": 2},
                      {"completion_tokens": 1, "reasoning_tokens": 2},
                      {"prompt_tokens": True}, {"prompt_tokens": -1},
                      {"prompt_tokens": 2, "usage": {"prompt_tokens": 3}}):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                pc.normalize_usage(usage)
        self.assertEqual(pc.normalize_usage({"prompt_tokens": 0})["prompt_tokens"], 0)

    def test_duplicate_checkpoint_conflicts_rejected(self):
        self.root.mkdir()
        path = self.root / "answers.jsonl"
        first = {"request_id": "a" * 24, "answer": "甲"}
        for second in ({**first, "answer": "乙"}, {**first, "prompt_tokens": 1}):
            path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
            with self.assertRaisesRegex(ValueError, "conflicting"):
                pc.load_answers_map(path)
        path.write_text(json.dumps(first) + "\n" + json.dumps(first) + "\n")
        self.assertEqual(len(pc.load_answers_map(path)), 1)

    def test_interrupted_apply_keeps_checkpoint_then_retry_is_idempotent(self):
        ids, wave = self.wave(2)
        self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        self.run_apply(wave, extra=("--allow-partial",))
        checkpoint = self.root / "answers.jsonl"
        before = checkpoint.read_bytes()
        self.answers(wave, [{"request_id": ids[1], "answer": "乙"}])
        with mock.patch.object(pc.os, "replace", side_effect=OSError("synthetic interrupted publication")):
            self.run_apply(wave, success=False)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.run_apply(wave)
        completed = checkpoint.read_bytes()
        self.run_apply(wave)
        self.assertEqual(checkpoint.read_bytes(), completed)
        self.assertEqual(len(pc.load_answers_map(checkpoint)), 2)

    def test_legacy_truncated_tail_recovered_with_exact_evidence_backup(self):
        ids, wave = self.wave(2)
        self.answers(wave, [{"request_id": ids[0], "answer": "甲"}])
        self.run_apply(wave, extra=("--allow-partial",))
        checkpoint = self.root / "answers.jsonl"
        damaged = checkpoint.read_bytes() + b'{"truncated": "\xe4\xb8'
        checkpoint.write_bytes(damaged)
        self.answers(wave, [{"request_id": ids[1], "answer": "乙"}])
        self.run_apply(wave)
        backups = list((self.root / ".recovery").glob("*.jsonl"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), damaged)
        self.assertEqual(len(pc.load_answers_map(checkpoint)), 2)

    def test_interrupted_wave_has_no_partly_published_directory(self):
        client = pc.PacketClient(self.root)
        self.queue(client)
        with mock.patch.object(pc.os, "rename", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                client.write_wave_bundles()
        self.assertEqual(list(self.root.glob("wave-*")), [])
        info = client.write_wave_bundles()
        self.assertEqual(Path(info["waveDir"]).name, "wave-01")
        self.assertTrue((Path(info["waveDir"]) / "manifest.json").is_file())

    def test_competing_apply_processes_preserve_one_answer_under_shared_lock(self):
        ids, wave = self.wave()
        sources = []
        for index, answer in enumerate(("甲", "乙")):
            path = wave / f"candidate-{index}.json"
            path.write_text(json.dumps({"answers": [{"request_id": ids[0], "answer": answer}]}))
            sources.append(path)
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import apply_packet_answers as a; "
                  "print('READY', flush=True); raise SystemExit(a.main(sys.argv[2:]))")
        processes = []
        with (self.root / ".packets.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for source in sources:
                process = subprocess.Popen([sys.executable, "-c", script,
                    str(Path(pp.__file__).parent), str(self.root), str(source)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(process)
                self.addCleanup(lambda p=process: p.kill() if p.poll() is None else None)
                self.assertEqual(process.stdout.readline().strip(), "READY")
            fcntl.flock(lock, fcntl.LOCK_UN)
        for process in processes:
            process.communicate(timeout=10)
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
        records = pc.load_answers_map(self.root / "answers.jsonl")
        self.assertEqual(len(records), 1)
        self.assertIn(records[ids[0]]["answer"], ("甲", "乙"))

    def profile_usage(self, metrics):
        record = {"winner": "undecided", "shownFirst": "A"}
        items = [{"page": 1, "kind": "substitution", "block": {}, "offset": 0, "seg": {}} for _ in metrics]
        replies = [("", dict(record), value) for value in metrics]
        with mock.patch.object(pp, "segment_crop", return_value=None), \
                mock.patch.object(pp, "sha256_file", return_value="0" * 64), \
                mock.patch.object(pp, "adjudicate_segment", side_effect=replies):
            return book_profile.calibrate_engines(object(), items, self.root / "renders", self.root / "crops",
                                                  len(items), 1, 128, 10)["usage"]

    def test_profile_aggregation_preserves_unknowns_and_excludes_replay(self):
        unknown = {key: None for key in pc.USAGE_FIELDS}
        measured = dict(zip(pc.USAGE_FIELDS, (80, 30, 50, 20)), response_id="response-one")
        unknown_usage = self.profile_usage([unknown])
        self.assertIsNone(unknown_usage["prompt_tokens"])
        self.assertEqual(unknown_usage["unknown_calls"], 1)
        deduplicated = self.profile_usage([measured, measured])
        self.assertEqual([deduplicated[k] for k in pc.USAGE_FIELDS], [80, 30, 50, 20])
        self.assertEqual(deduplicated["charged_calls"], 1)
        replay = self.profile_usage([{**measured, "reused": True, "charged": False}])
        self.assertEqual(replay["charged_calls"], 0)
        self.assertEqual(replay["prompt_tokens"], 0)
        with self.assertRaisesRegex(ValueError, "conflicting usage"):
            self.profile_usage([measured, {**measured, "prompt_tokens": 81}])


if __name__ == "__main__":
    unittest.main()
