"""packet_dispatch.py: the plan that shares a wave's bundles among answerers.

No model, no GPU: waves written by the real PacketClient in a temporary folder, answers files
written as an answerer would, and the apply command the plan names run for real.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _packet_client as pc  # noqa: E402
import packet_dispatch as dispatch  # noqa: E402


def png(width: int = 40, height: int = 40) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes((250, 250, 250)) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def wave(packets_dir: Path, questions: int, max_bundles: int = 8, per_bundle: int = 20, name: str = "q") -> dict:
    """A wave of QUESTIONS adjudications in PACKETS_DIR, as a step leaves it at exit 10."""
    client = pc.PacketClient(packets_dir, max_bundles=max_bundles, max_items_per_bundle=per_bundle)
    for number in range(questions):
        with contextlib.suppress(pc.PacketPending):
            client.ask_answering("system", f"{name} {number}", png(), 64, kind="adjudicate")
    return client.write_wave_bundles(stage="proofread")


def answer(bundle: dict, leave_out: int = 0) -> None:
    """Write BUNDLE's answers file as an answerer does (LEAVE_OUT: so many of its requests missing)."""
    items = json.loads(Path(bundle["bundle"]).read_text(encoding="utf-8"))["items"]
    rows = [{"request_id": item["request_id"], "answer": "構件：略\n定案：甲\nRESEARCH: no"}
            for item in items[:len(items) - leave_out]]
    Path(bundle["answers"]).write_text(json.dumps({"answers": rows}, ensure_ascii=False), encoding="utf-8")


def run(*arguments: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = dispatch.main(list(arguments))
    return status, json.loads(out.getvalue())


class PacketDispatchTests(unittest.TestCase):
    def test_a_wave_is_shared_out_whole_bundle_by_whole_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packets = Path(tmp) / "packets"
            wave(packets, 30, max_bundles=6)                 # six bundles of five
            plan = dispatch.plan([packets])
            self.assertEqual(plan["waiting"], {"questions": 30, "bundles": 6, "answerers": 3})
            self.assertEqual([a["questions"] for a in plan["assignments"]], [10, 10, 10])
            listed = [bundle["bundle"] for a in plan["assignments"] for bundle in a["bundles"]]
            self.assertEqual(listed, sorted(str(path) for path in (packets / "wave-01").glob("bundle-*.json")))
            first = plan["assignments"][0]
            # The prompt is the whole instruction: the one page to read, and the answerer's own bundles.
            self.assertIn(str(dispatch.BRIEF), first["prompt"])
            self.assertTrue(all(bundle["bundle"] in first["prompt"] for bundle in first["bundles"]))
            self.assertFalse(any(bundle["bundle"] in first["prompt"] for bundle in plan["assignments"][1]["bundles"]))
            self.assertEqual((plan["apply"], plan["rerun"]), ([], []))
            self.assertTrue(plan["next"].startswith("start one subagent per assignment, all at once"))

    def test_fewer_answerers_than_the_work_get_more_bundles_each_and_no_bundle_is_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packets = Path(tmp) / "packets"
            wave(packets, 60, max_bundles=12)                # twelve bundles of five
            every = dispatch.plan([packets], items_per_worker=1)
            self.assertEqual(len(every["assignments"]), 12)
            capped = dispatch.plan([packets], max_workers=4, items_per_worker=5)
            self.assertEqual([a["questions"] for a in capped["assignments"]], [15, 15, 15, 15])
            self.assertEqual(sum(len(a["bundles"]) for a in capped["assignments"]), 12)
        bundles = [{"bundle": str(n), "items": size} for n, size in enumerate((20, 3, 3, 20, 1))]
        self.assertEqual([[b["items"] for b in group] for group in dispatch.share(bundles, 10, 24)],
                         [[20], [3, 3], [20], [1]])
        self.assertEqual(len(dispatch.share(bundles, 10, 2)), 2)
        self.assertEqual(dispatch.share([], 10, 24), [])

    def test_what_is_answered_is_not_given_again_and_a_short_answers_file_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packets = Path(tmp) / "packets"
            wave(packets, 30, max_bundles=6)
            bundles = [bundle for a in dispatch.plan([packets])["assignments"] for bundle in a["bundles"]]
            for bundle in bundles[:3]:
                answer(bundle)
            answer(bundles[3], leave_out=1)                  # an answerer that stopped short
            plan = dispatch.plan([packets])
            waiting = [bundle for a in plan["assignments"] for bundle in a["bundles"]]
            self.assertEqual([Path(bundle["bundle"]).name for bundle in waiting],
                             ["bundle-04.json", "bundle-05.json", "bundle-06.json"])
            self.assertEqual([bundle["state"] for bundle in waiting], ["incomplete", "unwritten", "unwritten"])
            self.assertEqual(plan["apply"], [])              # not until the wave is whole

    def test_an_answered_wave_is_applied_from_here_and_then_named_for_the_step_to_run_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packets = Path(tmp) / "packets"
            wave(packets, 12, max_bundles=3)
            for a in dispatch.plan([packets])["assignments"]:
                for bundle in a["bundles"]:
                    answer(bundle)
            status, plan = run(str(packets))
            self.assertEqual((status, plan["assignments"], len(plan["apply"]), plan["rerun"]), (0, [], 1, []))
            self.assertIn("apply_packet_answers.py", plan["apply"][0]["command"])
            self.assertIn("--apply --answered-by", plan["next"])
            status, applied = run(str(packets), "--apply", "--answered-by", "test-model")
            self.assertEqual((status, applied["apply"][0]["exit"]), (0, 0), applied["apply"][0].get("output"))
            self.assertEqual([entry["wave"] for entry in applied["rerun"]], ["wave-01"])
            recorded = [json.loads(line) for line in (packets / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual((len(recorded), {row.get("answered_by") for row in recorded}), (12, {"test-model"}))
            # Asked again, the folder is neither given out nor applied twice.
            status, after = run(str(packets))
            self.assertEqual((status, after["assignments"], after["apply"], len(after["rerun"])), (0, [], [], 1))
            self.assertTrue(after["next"].startswith("every wave is applied"))

    def test_a_model_whose_price_steps_up_has_its_shares_filled_by_tokens(self) -> None:
        self.assertEqual(dispatch.price_step("claude-haiku-5-5"), 100_000)
        self.assertEqual(dispatch.price_step("Claude Haiku 5.5"), 100_000)
        self.assertIsNone(dispatch.price_step("some-other-model"))
        self.assertIsNone(dispatch.share_tokens(None))
        self.assertEqual(dispatch.share_tokens("claude-haiku-5-5"), 100_000 - 48_000 - 12_500)
        with tempfile.TemporaryDirectory() as tmp:
            packets = Path(tmp) / "packets"
            wave(packets, 12, max_bundles=12, per_bundle=1)   # twelve bundles of one
            whole = dispatch.plan([packets])
            self.assertEqual([a["questions"] for a in whole["assignments"]], [10, 2])
            one = max(bundle["tokens"] for a in whole["assignments"] for bundle in a["bundles"])
            self.assertGreater(one, 0)
            self.assertEqual(whole["assignments"][1]["tokens"], sum(b["tokens"] for b in whole["assignments"][1]["bundles"]))
            # Two bundles fit a share and a third does not; three answerers, and the rest waits for the next plan.
            plan = dispatch.plan([packets], max_workers=3, tokens_per_worker=2 * one)
            self.assertEqual([len(a["bundles"]) for a in plan["assignments"]], [2, 2, 2])
            self.assertEqual(plan["waiting"], {"questions": 12, "bundles": 12, "answerers": 3})
            # A bundle larger than a share is a share of its own; the model's name sizes the shares.
            status, found = run(str(packets), "--share-tokens", "1")
            self.assertEqual((status, [a["questions"] for a in found["assignments"]]), (0, [1] * 12))
            status, found = run(str(packets), "--answered-by", "claude-haiku-5-5")
            self.assertEqual((status, [a["questions"] for a in found["assignments"]]), (0, [10, 2]))
            status, found = run(str(packets), "--answered-by", "claude-haiku-5-5", "--share-tokens", "0")
            self.assertEqual((status, [a["questions"] for a in found["assignments"]]), (0, [10, 2]))

    def test_several_books_are_shared_out_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second, third = (Path(tmp) / name / "packets" for name in ("one", "two", "three"))
            wave(first, 4, max_bundles=2, name="a")
            wave(second, 4, max_bundles=2, name="b")
            wave(third, 6, max_bundles=2, name="c")
            for a in dispatch.plan([third])["assignments"]:
                for bundle in a["bundles"]:
                    answer(bundle)
            plan = dispatch.plan([first, second, third], items_per_worker=6)
            self.assertEqual(plan["waiting"], {"questions": 8, "bundles": 4, "answerers": 2})
            books = [{Path(bundle["bundle"]).parents[2].name for bundle in a["bundles"]} for a in plan["assignments"]]
            self.assertEqual(books, [{"one", "two"}, {"two"}])          # one answerer serves two books
            self.assertEqual([Path(entry["packets"]).parent.name for entry in plan["apply"]], ["three"])
            self.assertEqual([folder["questionsWaiting"] for folder in plan["folders"]], [4, 4, 0])

    def test_no_wave_and_apply_with_no_model_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertEqual(dispatch.main([str(Path(tmp) / "packets")]), 2)
            self.assertIn("no wave", said.getvalue())
            wave(Path(tmp) / "packets", 2)
            with self.assertRaises(SystemExit):
                dispatch.main([str(Path(tmp) / "packets"), "--apply"])

    def test_the_answerers_page_says_what_an_answerer_needs(self) -> None:
        page = dispatch.BRIEF.read_text(encoding="utf-8")
        for needed in ("expected_answers_file", "request_id", "image_path", "Answer every item",
                       "定案：睇唔到", "run none of its scripts", "load no other part of the skill", "settled or locked"):
            with self.subTest(needed=needed):
                self.assertIn(needed, page)
        self.assertLess(len(page.splitlines()), 40)          # one page: it is read by every answerer


if __name__ == "__main__":
    unittest.main()
