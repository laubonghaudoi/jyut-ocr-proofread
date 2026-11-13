"""Run a copied skill from an unrelated working directory, without model services."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]


def blank_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 50, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\xff\xff" * 40) * 50))
            + chunk(b"IEND", b""))


class RelocatedSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="relocated skill ")
        cls.root = Path(cls.temp.name)
        cls.skill = cls.root / "arbitrary agent directory" / "renamed 'skill copy"
        # Copy the complete skill tree, including the adapters/metadata that may
        # be present. Do not make the original repository available via symlinks.
        shutil.copytree(REPO_ROOT, cls.skill, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".venv", "node_modules", "tmp"))
        cls.cwd = cls.root / "unrelated working directory"
        cls.cwd.mkdir()
        cls.env = dict(os.environ)
        cls.env.pop("PYTHONPATH", None)
        cls.env.update({"PROOFREAD_BACKEND": "http", "PROOFREAD_ENDPOINT": "https://unused.invalid/api",
                        "PROOFREAD_MODEL": "unrelated-http-model"})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def run_command(self, command, *, expected: int = 0):
        result = subprocess.run(command, cwd=self.cwd, env=self.env, text=True, capture_output=True,
                                shell=isinstance(command, str), timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_skill_references_and_policy_assets_load_from_relocated_tree(self) -> None:
        text = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        paths = set(re.findall(r"`((?:references|scripts)/[\w./-]+\.(?:md|py|json|txt|swift))`", text))
        self.assertGreater(len(paths), 10, "SKILL.md resource references are not being inspected")
        for path in paths:
            with self.subTest(resource=path):
                self.assertTrue((self.skill / path).is_file(), path)
        program = (
            "import json,sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
            "import proofread_pages as p, book_profile, _ocr_markdown, repair_book; "
            "assert p.user_variant_rulings(); assert p.opencc_allographs(); "
            "print(json.dumps({'source':p.__file__,'variants':str(p.VARIANT_LIST),'hashes':p._LIST_SHA256}))"
        )
        result = self.run_command([sys.executable, "-c", program, str(self.skill / "scripts")])
        evidence = json.loads(result.stdout)
        self.assertEqual(Path(evidence["source"]).parent, self.skill / "scripts")
        self.assertEqual(Path(evidence["variants"]), self.skill / "references/variant-equivalents.json")
        self.assertEqual(evidence["hashes"]["variantList"], hashlib.sha256(
            (self.skill / "references/variant-equivalents.json").read_bytes()).hexdigest())

    def test_cli_help_imports_without_original_checkout_or_custom_agents(self) -> None:
        for filename in ("survey_pdf.py", "book_profile.py", "proofread_pages.py", "repair_book.py",
                         "apply_packet_answers.py", "validate_review_evidence.py"):
            with self.subTest(script=filename):
                result = self.run_command([sys.executable, str(self.skill / "scripts" / filename), "--help"])
                self.assertIn("usage:", result.stdout)

    def test_packet_round_trip_uses_portable_quoted_apply_command(self) -> None:
        book = self.root / "synthetic book's pages"
        renders, drafts, output = book / "renders", book / "drafts", book / "proofread"
        renders.mkdir(parents=True)
        drafts.mkdir()
        png = blank_png()
        (renders / "page-0001.png").write_bytes(png)
        (drafts / "page-0001.txt").write_text("", encoding="utf-8")
        (book / "book-profile.json").write_text('{"preferEngine":"adjudicate"}', encoding="utf-8")
        invocation = [sys.executable, str(self.skill / "scripts/proofread_pages.py"), str(renders), str(drafts),
                      str(output), "--workers", "1"]
        self.run_command(invocation, expected=10)
        wave = book / "packets/wave-01"
        bundle = json.loads((wave / "bundle-01.json").read_text())
        self.assertEqual([item["kind"] for item in bundle["items"]], ["page-inventory"])
        item = bundle["items"][0]
        self.assertEqual(Path(item["image_path"]), renders / "page-0001.png")
        self.assertEqual(item["image_sha256"], hashlib.sha256(png).hexdigest())
        # This is a synthetic protocol reply, not a model accuracy trial.
        blank = {"schema_version": 1, "page_state": "blank", "regions": [], "features": [],
                 "evidence": "The fixture is an all-white image with no printed content."}
        Path(bundle["expected_answers_file"]).write_text(json.dumps({"answers": [{
            "request_id": item["request_id"], "answer": json.dumps(blank)}]}), encoding="utf-8")
        manifest = json.loads((wave / "manifest.json").read_text())
        command = manifest["apply_command"]
        self.assertEqual(shlex.split(command), [sys.executable, str(self.skill / "scripts/apply_packet_answers.py"),
                                               str(book / "packets"), str(wave)])
        self.run_command(command)
        self.run_command(invocation)
        self.assertEqual((output / "page-0001.md").read_text(), "")
        final = json.loads((output / "page-0001.json").read_text())
        self.assertEqual(final["status"], "complete")
        self.assertEqual(final["provenance"]["proofreadTransport"], "packet")
        self.assertEqual(final["provenance"]["proofreadModel"], "agent-packet")


if __name__ == "__main__":
    unittest.main()
