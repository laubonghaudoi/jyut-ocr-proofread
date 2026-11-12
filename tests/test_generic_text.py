from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]

# Corpus and benchmark book titles / dataset slugs that must never be hardcoded
# in the skill's rules, adapters, references, or bundled scripts.
FORBIDDEN_CORPUS_PATTERNS = re.compile(
    r"天地[会會]|盜區政區|盜區|陳總司令言論集|陳總司令|彰化縣志|彰化|廣東省?財政紀實|"
    r"興漢紀念廣東獨立全案|興漢紀念|剷共半月刊|剷共|中國國民黨史稿|國民黨史稿|"
    r"憲政報[告吿]書|新字典|caangung|hinghon|hinzing|sinzidin|nlc-finance|changhua|tiandihui",
    re.I,
)


class GenericTextTests(unittest.TestCase):
    def test_no_corpus_or_benchmark_book_titles_anywhere_in_skill_or_adapters(self) -> None:
        roots = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "CHANGELOG.md",
            REPO_ROOT / "adapters",
            REPO_ROOT / "plugins",
            REPO_ROOT / "scripts",
        ]
        violations: list[str] = []
        for root in roots:
            if not root.exists():
                continue
            files = [root] if root.is_file() else sorted(root.rglob("*"))
            for path in files:
                if path.is_dir() or "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    match = FORBIDDEN_CORPUS_PATTERNS.search(line)
                    if match:
                        rel = path.relative_to(REPO_ROOT)
                        violations.append(f"{rel}:{line_no}: matched {match.group(0)!r}")
        self.assertEqual(
            violations,
            [],
            "Found hardcoded corpus/benchmark book titles:\n" + "\n".join(violations[:20]),
        )

    def test_variant_equivalents_is_valid_json_without_book_titles(self) -> None:
        variant_path = (
            REPO_ROOT
            / "plugins"
            / "jyut-ocr-proofread"
            / "skills"
            / "jyut-ocr-proofread"
            / "references"
            / "variant-equivalents.json"
        )
        data = json.loads(variant_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("classes"), list)
        self.assertGreater(len(data["classes"]), 10)


if __name__ == "__main__":
    unittest.main()
