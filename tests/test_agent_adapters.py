from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_AGENTS = REPO_ROOT / "adapters" / "codex" / "agents"
CLAUDE_AGENTS = REPO_ROOT / "adapters" / "claude" / "agents"
CONTRACT = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "references"
    / "subagent-orchestration.md"
)
AGENT_NAMES = ("jyut-ocr-segment-writer", "jyut-ocr-visual-auditor")


def parse_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    """Parse the small YAML subset used by the checked-in Claude adapters."""

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"{path} has no YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError(f"{path} has unterminated YAML frontmatter") from error

    result: dict[str, object] = {}
    active_list: list[str] | None = None
    for raw_line in lines[1:closing]:
        if raw_line.startswith("  - "):
            if active_list is None:
                raise AssertionError(f"{path} has a list item without a key")
            active_list.append(raw_line[4:].strip())
            continue
        if ":" not in raw_line:
            raise AssertionError(f"{path} has invalid frontmatter: {raw_line!r}")
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or key in result:
            raise AssertionError(f"{path} has an invalid or duplicate key: {key!r}")
        if value:
            result[key] = value
            active_list = None
        else:
            active_list = []
            result[key] = active_list

    body = "\n".join(lines[closing + 1 :]).strip()
    return result, body


def comma_list(value: object) -> list[str]:
    if not isinstance(value, str):
        raise AssertionError(f"expected comma-separated string, got {value!r}")
    return [item.strip() for item in value.split(",") if item.strip()]


class CodexAgentAdapterTests(unittest.TestCase):
    def test_toml_agents_are_valid_and_inherit_model_resolution(self) -> None:
        for name in AGENT_NAMES:
            with self.subTest(agent=name):
                path = CODEX_AGENTS / f"{name}.toml"
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["name"], name)
                self.assertIsInstance(data["description"], str)
                self.assertTrue(data["description"].strip())
                self.assertIsInstance(data["developer_instructions"], str)
                expected_instruction = (
                    "STOP-WRITE" if name.endswith("writer") else "100%"
                )
                self.assertIn(expected_instruction, data["developer_instructions"])
                self.assertNotIn("model", data)
                self.assertNotIn("model_reasoning_effort", data)
                self.assertEqual(data["agents"]["enabled"], False)

    def test_codex_auditor_is_read_only(self) -> None:
        auditor = tomllib.loads(
            (CODEX_AGENTS / "jyut-ocr-visual-auditor.toml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(auditor["sandbox_mode"], "read-only")
        instructions = auditor["developer_instructions"].lower()
        self.assertIn("remain read-only", instructions)
        self.assertIn("do not patch", instructions)
        self.assertIn("do not spawn", instructions)


class ClaudeAgentAdapterTests(unittest.TestCase):
    def test_yaml_frontmatter_is_valid_and_model_is_inherited(self) -> None:
        expected_tools = {
            "jyut-ocr-segment-writer": [
                "Read",
                "Grep",
                "Glob",
                "Bash",
                "Edit",
                "Write",
            ],
            "jyut-ocr-visual-auditor": ["Read", "Grep", "Glob", "Bash"],
        }
        for name in AGENT_NAMES:
            with self.subTest(agent=name):
                frontmatter, body = parse_frontmatter(CLAUDE_AGENTS / f"{name}.md")
                self.assertEqual(frontmatter["name"], name)
                self.assertTrue(str(frontmatter["description"]).strip())
                self.assertEqual(frontmatter["model"], "inherit")
                self.assertEqual(frontmatter["skills"], ["jyut-ocr-proofread"])
                self.assertEqual(
                    comma_list(frontmatter["tools"]), expected_tools[name]
                )
                self.assertNotIn("Agent", comma_list(frontmatter["tools"]))
                self.assertIn("Do not spawn", body)

    def test_claude_auditor_is_plan_mode_without_write_tools(self) -> None:
        frontmatter, body = parse_frontmatter(
            CLAUDE_AGENTS / "jyut-ocr-visual-auditor.md"
        )
        self.assertEqual(frontmatter["permissionMode"], "plan")
        tools = comma_list(frontmatter["tools"])
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertIn("Remain read-only", body)
        self.assertIn("100%", body)


class SharedContractTests(unittest.TestCase):
    def test_contract_covers_parallelism_seals_and_independent_audit(self) -> None:
        text = " ".join(CONTRACT.read_text(encoding="utf-8").split())
        for invariant in (
            "min(runtime child slots, 8)",
            "Apple Vision OCR uses a separate bounded local process pool, not LLM agents",
            "STOP-WRITE",
            "100% of the assigned scans",
            "effective permission/sandbox mode is still read-only",
            "Only the main agent assembles the final Markdown",
            "A late patch invalidates the affected seal and audit",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, text)


if __name__ == "__main__":
    unittest.main()
