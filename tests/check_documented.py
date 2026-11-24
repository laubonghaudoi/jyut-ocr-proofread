#!/usr/bin/env python3
"""Offline check that what phase 3 seals is written in the skill's text.

- Every --no-* switch of scripts/proofread_pages.py is named in SKILL.md or
  references/.
- Every decision it records in decisionChanges, and every doubt kind it
  seals, is named in references/long-book-workflow.md, the reference that
  says what each one means and what a person should check.

Found in the round-3 review: a proposed decision, a user's ruling
(headerCharacters), their two switches and three doubt kinds went in with no
word in either file, where every earlier decision had its switch documented.
The patterns are the literal forms the runner writes them in; a new way of
writing one needs a new pattern here.  The switches of the decided rules,
the trials and the measuring-only paths went on 2026-10-04: what is left is
a handful of switches, each sealed in provenance.

- Every switch of scripts/repair_book.py (the book repair tool) is named in
  SKILL.md or references/, and every doubt kind it writes and every counter of
  scripts/_book_lint.py is named in long-book-workflow.md.

- Every option SKILL.md cites exists (skill_options): each
  `python3 scripts/X.py ...` command's options are in X's usage, and every
  other `--option` is in the usage of a script SKILL.md names.  SKILL.md is
  the agent's run sheet (2026-10-05): the agent runs its commands as written,
  so an option a script lost would fail there, not here.  Each section it
  sends the agent to (〈name〉 after a `references/X.md` in its last section)
  is a heading of that file: the agent reads that section alone.

- A comment or docstring of scripts/ that says something was found (found
  in review, found when two branches were merged) says what it was found on:
  a made-up page (the check that builds it) or a measurement (unsourced_findings).

- Every doubt kind phase 3 seals has its words in finish_book.py's KINDS, the
  label doubts.md gives it (unlabelled_doubts); every kind finish_book.py
  writes itself has them in the section it writes it in, and is named in
  long-book-workflow.md (finish_kinds); and none of the round-4 fixes,
  on by default, is called off by default in scripts/, SKILL.md or
  references/ (called_off).  Found in the round-4 integration review: the
  repair branch's KINDS had no words for the two doubt kinds the round-4
  branch added (neighbour-copy, arabic-figure-unwritten), so doubts.md would
  have headed them with the English kind; and three comments and one
  reference entry still called a fix off by default after the switch.

- Each `SKILL.md, step N` a script's docstring or help cites is a step of
  SKILL.md's run sheet (四) that names a script the citing sentence names
  (skill_steps).  Found in the phase-E review: build_draft.py's docstring
  still sent the reader to "SKILL.md, step 3" and "step 8", the old run
  sheet's numbers, where the draft is step 2 (the OCR step) and
  finish_book.py step 5.

Usage:
    python3 tests/check_documented.py
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
# skill_options runs the scripts' --help as processes: they inherit an
# endpoint nothing listens on.
import offline  # noqa: E402,F401


def main() -> int:
    runner = "".join(
        path.read_text(encoding="utf-8")
        for path in [ROOT / "scripts" / "proofread_pages.py",
                     *sorted((ROOT / "scripts" / "jyut_ocr" / "proofread").glob("*.py"))]
    )
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    references = "".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "references").glob("*.md")))
    workflow = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    switches = sorted(set(re.findall(r'add_argument\(\s*"(--no-[a-z-]+)"', runner)))
    decisions = sorted(set(re.findall(r'"decision":\s*"([A-Za-z0-9][A-Za-z0-9-]*)"', runner)))
    kinds = sorted(set(re.findall(r'"kind":\s*"([a-z-]+)",\s*"detail"', runner)))
    assert len(switches) >= 5 and len(decisions) > 10 and len(kinds) > 20, \
        "the runner's switches, decisions and doubt kinds were not found"
    problems = ([f"switch {name} is in neither SKILL.md nor references/" for name in switches
                 if name not in skill and name not in references]
                + [f"decision {name} is not in long-book-workflow.md" for name in decisions
                   if not re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![0-9])", workflow)]
                + [f"doubt kind `{name}` is not in long-book-workflow.md" for name in kinds
                   if f"`{name}`" not in workflow])
    for problem in problems:
        print("FAIL", problem)
    print(f"{'FAIL' if problems else 'ok  '} {len(switches)} switches, {len(decisions)} decisions, "
          f"{len(kinds)} doubt kinds: {len(problems)} not documented")
    repair = repair_problems(skill, references, workflow)
    for problem in repair:
        print("FAIL", problem)
    print(f"{'FAIL' if repair else 'ok  '} the book repair tool's switches, doubt kinds and counters are "
          f"documented: {len(repair)} not")
    problems += repair
    stale = still_proposed(runner, skill + references)
    for problem in stale:
        print("FAIL", problem)
    print(f"{'FAIL' if stale else 'ok  '} the decisions the user ruled on are not called proposed or pending: "
          f"{len(stale)} found")
    unsourced = unsourced_findings({path.relative_to(ROOT): path.read_text(encoding="utf-8")
                                    for path in sorted((ROOT / "scripts").rglob("*.py"))})
    for problem in unsourced:
        print("FAIL", problem)
    print(f"{'FAIL' if unsourced else 'ok  '} what the scripts' comments say was found says what it was found on: "
          f"{len(unsourced)} not")
    unlabelled = unlabelled_doubts(kinds)
    for problem in unlabelled:
        print("FAIL", problem)
    print(f"{'FAIL' if unlabelled else 'ok  '} every doubt kind phase 3 seals has its label in doubts.md: "
          f"{len(unlabelled)} not")
    own = finish_kinds(workflow)
    for problem in own:
        print("FAIL", problem)
    print(f"{'FAIL' if own else 'ok  '} every doubt kind finish_book.py writes itself has its label in its section "
          f"and is in long-book-workflow.md: {len(own)} not")
    unlabelled += own
    cited = skill_options(skill) + skill_sections(skill)
    for problem in cited:
        print("FAIL", problem)
    print(f"{'FAIL' if cited else 'ok  '} every option SKILL.md cites is a script's, every section it sends "
          f"the agent to a heading: {len(cited)} not")
    steps = skill_steps(skill, {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
                                for path in sorted((ROOT / "scripts").rglob("*.py"))})
    for problem in steps:
        print("FAIL", problem)
    print(f"{'FAIL' if steps else 'ok  '} every SKILL.md step the scripts cite is the run sheet's step for "
          f"what they cite it for: {len(steps)} not")
    cited += steps
    called = called_off([ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md")),
                         *sorted((ROOT / "scripts").rglob("*.py"))])
    for problem in called:
        print("FAIL", problem)
    print(f"{'FAIL' if called else 'ok  '} the round-4 fixes, on by default, are not called off by default: "
          f"{len(called)} found")
    return 1 if problems or stale or unsourced or unlabelled or cited or called else 0


OPTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?![\w-])")
COMMAND = re.compile(r"python3 scripts/([a-z_]+\.py)((?:[^;|>#`\n&]|&(?!&))*)")
# Options SKILL.md names that belong to another program.
EXTERNAL = {"--max-concurrency": "the LLM server's"}


def usage_options(script: str) -> set[str]:
    """The options in SCRIPT's usage block (its --help, up to the first blank line)."""
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    return set(OPTION.findall(out.stdout.split("\n\n", 1)[0])) if out.returncode == 0 else set()


def skill_options(skill: str) -> list[str]:
    """Each option of each `python3 scripts/X.py ...` command in SKILL.md that X's
    usage lacks (a command wrapped by run_ocr_phase.py's literal -- is read as
    its own command), and each other `--option` SKILL.md cites that no script
    it names has."""
    named = sorted({name for name in re.findall(r"([a-z_]+\.py)", skill) if (ROOT / "scripts" / name).is_file()})
    usage = {name: usage_options(name) for name in named}
    assert all(usage.values()) and len(named) >= 10, f"the scripts' usage was not read: {sorted(usage)}"
    out, in_commands = [], set()
    for line in skill.splitlines():
        for piece in line.split(" -- "):
            for match in COMMAND.finditer(piece):
                for option in OPTION.findall(match.group(2)):
                    in_commands.add(option)
                    if option not in usage[match.group(1)]:
                        out.append(f"SKILL.md runs {match.group(1)} with {option}, which it does not have")
    every = set().union(*usage.values())
    out += [f"SKILL.md cites {option}, which no script it names has" for option in sorted(set(OPTION.findall(skill)))
            if option not in every and option not in in_commands and option not in EXTERNAL]
    return out


STEP_CITE = re.compile(r"SKILL\.md(?:'s)?,? step (\d+)")


def skill_steps(skill: str, sources: dict[str, str]) -> list[str]:
    """Each `SKILL.md, step N` (or `SKILL.md's step N`) in SOURCES (script
    name -> text) whose step N of SKILL.md's run sheet (四: the lines that
    start `N. `) is missing or names none of the scripts the citing item
    names (the item: from the blank line or list item before the cite to
    it; the citing script itself when it names none)."""
    sheet = skill.split("\n## 四、", 1)[-1].split("\n## ", 1)[0] if "\n## 四、" in skill else ""
    lines = {match.group(1): match.group(0) for match in re.finditer(r"^(\d+)\. .*$", sheet, re.M)}
    out = []
    for name, text in sources.items():
        for match in STEP_CITE.finditer(text):
            before = text[:match.start()]
            start = max(before.rfind("\n\n"), *(m.start() for m in re.finditer(r"\n\s*(?:\d+\.|-) ", before)))
            scripts = set(re.findall(r"([a-z_]+\.py)", text[max(start, 0):match.end()])) or {name}
            line = lines.get(match.group(1), "")
            if not any(script in line for script in scripts):
                out.append(f"scripts/{name} cites {match.group(0)!r} for {sorted(scripts)}, "
                           + (f"which that step does not name" if line else "which SKILL.md 四 does not have"))
    return out


def skill_sections(skill: str) -> list[str]:
    """Each 〈name〉 SKILL.md's last section names after `references/X.md` that
    starts no heading of that file."""
    last = skill[skill.rfind("\n## "):]
    out, named = [], 0
    for line in last.splitlines():
        files = re.findall(r"`(references/[\w.-]+\.md)`", line)
        if not files:
            continue
        path = ROOT / files[0]
        headings = re.findall(r"^#+ (.+)$", path.read_text(encoding="utf-8"), re.M) if path.is_file() else []
        for name in re.findall(r"〈([^〉]+)〉", line):
            named += 1
            if not any(heading.startswith(name) for heading in headings):
                out.append(f"SKILL.md sends the agent to {files[0]}〈{name}〉, which is no heading there")
    assert named >= 5, "SKILL.md's last section names no reference section"
    return out


def tuple_strings(source: str, name: str) -> list[str]:
    """The quoted strings of the tuple assigned to NAME in SOURCE."""
    match = re.search(rf"^{name} = \((.*?)^\)", source, re.M | re.S)
    return re.findall(r'"([A-Za-z0-9-]+)"', match.group(1)) if match else []


def repair_problems(skill: str, references: str, workflow: str) -> list[str]:
    """The book repair tool (scripts/repair_book.py): each switch is named in
    SKILL.md or references/ (the agent never runs it: finish_book.py does); each
    doubt kind it writes (_repair_edits.DOUBT_KINDS) and each defect counter
    (_book_lint.COUNTERS) is named in long-book-workflow.md."""
    scripts = ROOT / "scripts"
    repair = (scripts / "repair_book.py").read_text(encoding="utf-8")
    stage_switches = sorted(set(re.findall(r'Stage\("[a-z-]+",\s*"(--[a-z-]+)"', repair)))
    other = sorted(set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', repair)) - set(stage_switches))
    kinds = tuple_strings((scripts / "jyut_ocr" / "repair" / "edits.py").read_text(encoding="utf-8"), "DOUBT_KINDS")
    counters = tuple_strings((scripts / "jyut_ocr" / "repair" / "book_lint.py").read_text(encoding="utf-8"), "COUNTERS")
    assert len(stage_switches) >= 8 and len(other) >= 4 and len(kinds) >= 9 and len(counters) >= 20, \
        "the repair tool's switches, doubt kinds and counters were not found"
    return ([f"repair_book.py switch {name} is in neither SKILL.md nor references/"
             for name in stage_switches + other if name not in skill and name not in references]
            + [f"repair doubt kind `{name}` is not in long-book-workflow.md" for name in kinds
               if f"`{name}`" not in workflow]
            + [f"lint counter `{name}` is not in long-book-workflow.md" for name in counters
               if f"`{name}`" not in workflow])


# The proposed decisions the user ruled on, all on (the round-2 decision
# register, 2026-09-27: D15, D16 - after its fix, the answer accepted where the
# reading stands nearby - and D20-D24; D25 went with the chart step).
RULED = ("D15", "D16", "D20", "D21", "D22", "D23", "D24")


def still_proposed(runner: str, text: str) -> list[str]:
    """Where the runner's help and comments, SKILL.md or references/ still call
    a decision in RULED proposed or pending the user's ruling.  Found merging
    round 3: both branches carried the round-2 labels, and the skill's text
    called D16 ruled on in one sentence and its switch's help pending."""
    flat = re.sub(r"\s*\n\s*#?\s*", " ", runner)
    out = []
    for name in RULED:
        number = rf"{name}(?![0-9])"
        if re.search(rf"[Pp]roposed decision {number}|{number}\W{{1,3}}pending the user|"
                     rf"{number} \(--no-[a-z-]+; not yet ruled", flat):
            out.append(f"scripts/proofread_pages.py calls {name} proposed or pending")
        if re.search(rf"建議決定 {number}|{number}，用戶未裁", text):
            out.append(f"SKILL.md or references/ call {name} a proposed decision (建議決定 / 用戶未裁)")
    return out


# What a finding may be sourced by: the check that builds the made-up page it
# was shown on, or words that say it was made up or measured.
FINDING = re.compile(r"\bfound (?:in (?:the )?[\w -]{0,30}review|when [^.:]{0,40}merg\w*|merging|integrating)\b",
                     re.I)
SOURCED = re.compile(r"\bcheck_[a-z_]+|made-up|made up|measured|corpus|benchmark", re.I)


def unsourced_findings(sources: dict[Path, str]) -> list[str]:
    """Each sentence of the scripts' comments and docstrings that says
    something was found and not what it was found on (SOURCED).  The rule is
    the user's: say in comments what was measured.  Found in the round-3
    integration review: a comment presented a made-up test page as something
    found (a prose line written twice when the branches were merged), naming
    neither the page nor its check, so a reader takes it for a corpus
    measurement.  A sentence runs from the words to the first full stop
    followed by a space or the first closing parenthesis, whichever comes
    first (a finding written inside parentheses ends there)."""
    out = []
    for path, text in sources.items():
        flat = re.sub(r"\s*\n\s*#?\s*", " ", text)
        for match in FINDING.finditer(flat):
            rest = flat[match.start():]
            ends = [m.start() for m in (re.search(r"\.(?:\s|$)", rest), re.search(r"\)", rest)) if m]
            sentence = rest[:min(ends)] if ends else rest[:400]
            if not SOURCED.search(sentence):
                out.append(f"{path}: \"{sentence[:90]}…\" says what was found, not on what "
                           "(a made-up page and its check, or a measurement)")
    return out


def unlabelled_doubts(kinds: list[str]) -> list[str]:
    """The doubt kinds of KINDS (the ones phase 3 seals) that finish_book.py's
    KINDS gives no section and words: doubts.md would head them with the
    English kind, in the text section's end."""
    finish = (ROOT / "scripts" / "finish_book.py").read_text(encoding="utf-8")
    match = re.search(r"^KINDS: .*? = \{(.*?)^\}", finish, re.M | re.S)
    labelled = set(re.findall(r'^\s*"([A-Za-z0-9-]+)": \("[a-z]+", "[^"]+"\)', match.group(1), re.M)) if match else set()
    assert len(labelled) > 40, "finish_book.py's KINDS was not found"
    return [f"doubt kind `{name}` has no label in finish_book.py's KINDS" for name in kinds if name not in labelled]


def finish_kinds(workflow: str) -> list[str]:
    """The doubt kinds finish_book.py writes itself (Item("section", "kind",
    ...), or Item(KINDS["kind"][0], "kind", ...)): each has its label in
    KINDS, in the same section, and is named in long-book-workflow.md, as
    phase 3's and the repair's kinds are.  Found in the #36/#37 review
    (measured on the tree, 2026-10-24): deleting note-unmatched from
    long-book-workflow.md, or giving its Item another section than KINDS,
    passed every check."""
    finish = (ROOT / "scripts" / "finish_book.py").read_text(encoding="utf-8")
    match = re.search(r"^KINDS: .*? = \{(.*?)^\}", finish, re.M | re.S)
    labelled = dict(re.findall(r'^\s*"([A-Za-z0-9-]+)": \("([a-z]+)", "[^"]+"\)', match.group(1), re.M)) \
        if match else {}
    own = re.findall(r'Item\((?:"([a-z]+)"|KINDS\["([a-z-]+)"\]\[0\]), "([a-z-]+)"', finish)
    assert len(own) >= 5, "finish_book.py's own doubt kinds were not found"
    return ([f"finish doubt kind `{kind}` has no label in KINDS in section {section}" for section, keyed, kind in own
             if kind not in labelled or (section or labelled[kind]) != labelled[kind] or keyed not in ("", kind)]
            + [f"finish doubt kind `{kind}` is not in long-book-workflow.md" for _, _, kind in own
               if f"`{kind}`" not in workflow])


# The round-4 fixes, on by default since the user's decision of 2026-09-30
# (proofread_pages.ROUND4_DEFAULTS), and the words that call a switch off.
ROUND4 = ("--keep-arabic-numerals", "--loop-detector", "--neighbour-guard", "--learned-heads")
CALLED_OFF = r"off by default|off until measured|default off|預設關|等實測先定|等 GPU 實測先定"


def called_off(paths: list[Path]) -> list[str]:
    """Where a round-4 fix is called off by default: the switch, then within
    the same sentence (no full stop and space, or 。, between) and 45
    characters the words."""
    out = []
    for path in paths:
        flat = re.sub(r"\s*\n\s*#?\s*", " ", path.read_text(encoding="utf-8"))
        for switch in ROUND4:
            for match in re.finditer(rf"{re.escape(switch)}(?![a-z-])(?:(?!\. |。).){{0,45}}?(?:{CALLED_OFF})", flat):
                out.append(f"{path.relative_to(ROOT)}: \"{match.group(0)[:90]}\" calls {switch} off by default")
    return out


if __name__ == "__main__":
    sys.exit(main())
