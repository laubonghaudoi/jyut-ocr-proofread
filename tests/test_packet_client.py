from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    REPO_ROOT / "plugins" / "jyut-ocr-proofread" / "skills" / "jyut-ocr-proofread" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _packet_client as pc  # noqa: E402
import apply_packet_answers  # noqa: E402
import book_profile  # noqa: E402
import proofread_pages as pp  # noqa: E402


def _make_solid_png(width: int = 120, height: int = 160, rgb: tuple[int, int, int] = (250, 250, 250)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class PacketClientTests(unittest.TestCase):
    def test_three_wave_packet_replay_and_corrupt_jsonl_tolerance(self) -> None:
        png_bytes = _make_solid_png()
        with tempfile.TemporaryDirectory() as tmp:
            packets_dir = Path(tmp) / "packets"

            # Wave 1: two unanswered requests queued and flushed into wave-01 bundles.
            client1 = pc.PacketClient(packets_dir, max_bundles=2)
            with self.assertRaises(pc.PacketPending):
                client1.ask_answering("sys-a", "user-q1", png_bytes, 128, kind="adjudicate")
            with self.assertRaises(pc.PacketPending):
                client1.fused_census_and_punctuate(
                    crop_bytes=png_bytes,
                    span="甲乙丙丁",
                    hint="",
                    leaders=None,
                    show_doubled=True,
                    max_tokens=256,
                    census_system="census-sys",
                    punct_system="punct-sys",
                )
            wave1_info = client1.write_wave_bundles(stage="proofread")
            self.assertEqual(wave1_info["pending"], 2)
            wave1_dir = Path(wave1_info["waveDir"])
            self.assertEqual(wave1_dir.name, "wave-01")
            bundles = sorted(wave1_dir.glob("bundle-*.json"))
            self.assertEqual(len(bundles), 2)

            # Reject unknown request_id unless --allow-unknown-ids; reject incomplete wave unless --allow-partial.
            bad_answers = wave1_dir / "bad.json"
            bad_answers.write_text(
                json.dumps({"answers": [{"request_id": "0" * 24, "answer": "甲"}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                apply_packet_answers.main([str(packets_dir), str(bad_answers)]),
                2,
            )

            # Write valid Wave-1 answers per bundle.
            for idx, bundle_path in enumerate(bundles, start=1):
                bdata = json.loads(bundle_path.read_text(encoding="utf-8"))
                ans_rows = []
                for item in bdata["items"]:
                    if item["kind"] == "census+punctuate":
                        ans_rows.append(
                            {
                                "request_id": item["request_id"],
                                "census": "符號：1\n雙圈：1\n形狀：。。",
                                "punctuated": "甲乙丙丁。。",
                            }
                        )
                    else:
                        ans_rows.append({"request_id": item["request_id"], "answer": "甲"})
                (wave1_dir / f"answers-{idx:02d}.json").write_text(
                    json.dumps({"answers": ans_rows}, ensure_ascii=False), encoding="utf-8"
                )

            rc = apply_packet_answers.main([str(packets_dir), str(wave1_dir)])
            self.assertEqual(rc, 0)

            # Append a truncated/corrupt trailing line to answers.jsonl and ensure PacketClient tolerates it.
            with (packets_dir / "answers.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{corrupt_trailing_json_line\n")

            # Wave 2: Wave 1 replays in 0ms; a dependent Wave-2 structure call queues into wave-02.
            client2 = pc.PacketClient(packets_dir, max_bundles=4)
            ans1, _ = client2.ask_answering("sys-a", "user-q1", png_bytes, 128, kind="adjudicate")
            self.assertEqual(ans1, "甲")
            census_txt, punct_txt, _ = client2.fused_census_and_punctuate(
                crop_bytes=png_bytes,
                span="甲乙丙丁",
                hint="",
                leaders=None,
                show_doubled=True,
                max_tokens=256,
                census_system="census-sys",
                punct_system="punct-sys",
            )
            self.assertIn("雙圈：1", census_txt)
            self.assertEqual(punct_txt, "甲乙丙丁。。")

            with self.assertRaises(pc.PacketPending):
                client2.ask_answering("sys-struct", f"format: {punct_txt}", png_bytes, 512, kind="structure")
            wave2_info = client2.write_wave_bundles(stage="proofread")
            self.assertEqual(Path(wave2_info["waveDir"]).name, "wave-02")

    def test_book_profile_short_document_fast_path_requires_zero_calibration_packets(self) -> None:
        png_bytes = _make_solid_png(width=600, height=900)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            renders = root / "renders"
            ocr_a = root / "ocr-paddle"
            ocr_b = root / "ocr-hunyuan"
            for d in (renders, ocr_a, ocr_b):
                d.mkdir(parents=True)

            # 2-page short document (<= 4 pages) with slight disagreement between engines A and B;
            # page 1 has 32 CJK characters (1 untiled block >= NOTATION_MINIMUM_CHARACTERS) while page 2 has 8.
            long_body = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽。"
            for page_no, (body_a, body_b) in (
                (1, (long_body, long_body.replace("宙", "宇", 1))),
                (2, ("短頁總結全文完。", "短頁總結全文完。")),
            ):
                stem = f"page-{page_no:04d}"
                (renders / f"{stem}.png").write_bytes(png_bytes)
                blocks = [
                    {
                        "text": "固定書眉卷一",
                        "blockLabel": "header",
                        "boundingBox": [0.1, 0.02, 0.8, 0.03],
                        "confidence": 0.98,
                    },
                    {
                        "text": body_a,
                        "blockLabel": "text",
                        "boundingBox": [0.1, 0.12, 0.08, 0.25],
                        "confidence": 0.99,
                    },
                ]
                full_a = "\n\n".join(b["text"] for b in blocks) + "\n"
                (ocr_a / f"{stem}.txt").write_text(full_a, encoding="utf-8")
                (ocr_a / f"{stem}.json").write_text(
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "scanPage": page_no,
                            "renderWidth": 600,
                            "renderHeight": 900,
                            "lines": blocks,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (ocr_b / f"{stem}.txt").write_text(body_b + "\n", encoding="utf-8")

            profile_path = root / "book-profile.json"
            packets_dir = root / "packets"
            argv = [
                str(renders),
                str(ocr_a),
                str(ocr_b),
                str(profile_path),
                "--backend",
                "packet",
                "--packets-dir",
                str(packets_dir),
            ]
            rc1 = book_profile.main(argv)
            # Only 1 notation packet is queued; ZERO engine-calibration adjudicate packets!
            self.assertEqual(rc1, pc.EXIT_PACKETS_PENDING)
            wave_dir = packets_dir / "wave-01"
            bundle = json.loads((wave_dir / "bundle-01.json").read_text(encoding="utf-8"))
            self.assertEqual([item["kind"] for item in bundle["items"]], ["notation"])

            (wave_dir / "answers-01.json").write_text(
                json.dumps(
                    {
                        "answers": [
                            {
                                "request_id": bundle["items"][0]["request_id"],
                                "answer": "符號：。\n雙圈：有\n圈點：圈\n點數：4",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(apply_packet_answers.main([str(packets_dir), str(wave_dir)]), 0)

            rc2 = book_profile.main(argv)
            self.assertEqual(rc2, 0)
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(profile["engines"]["preferEngine"], "adjudicate")
            self.assertEqual(profile["engines"]["stopped"], "short-document fast path")
            self.assertEqual(profile["notation"]["blocksSurveyed"], 1)
            self.assertTrue(profile["notation"]["doubled"])
            self.assertIn("固定書眉卷一", profile["layout"]["runningHeads"])

    def test_proofread_pages_table_orientation_flag_and_provenance(self) -> None:
        parser = pp.build_parser()
        args_auto = parser.parse_args(["r", "d", "o"])
        self.assertEqual(args_auto.table_orientation, "auto")
        self.assertNotIn("tableOrientation", pp.decision_provenance(args_auto))

        args_printed = parser.parse_args(["r", "d", "o", "--table-orientation", "as-printed"])
        self.assertEqual(args_printed.table_orientation, "as-printed")
        self.assertEqual(pp.decision_provenance(args_printed).get("tableOrientation"), "as-printed")


if __name__ == "__main__":
    unittest.main()
