#!/usr/bin/env python3
"""Score a proofread output directory against the golden reference cases.

Compatibility scorer: its existing ``cer`` field is a legacy alignment-based
metric, also exposed as ``legacy_alignment_cer``. It is not the independent
benchmark's minimum-edit-distance CER. Algorithms and ratchets stay unchanged.

Why this exists: every accuracy number in this skill came from ad-hoc
measurement on one book, and changes were argued rather than measured. Twice in
one session a change looked like a clean win and was not - a punctuation prompt
that removed every wrongly-invented comma while making one page emit a mark
after almost every character, and a crop matcher that shipped 141 images of
which the first one checked was of the wrong block. Both would have failed here.

Assertions carry their provenance, because they are not equally trustworthy:

    human       a person read the page image and said what it says. Hard failure.
    regression  a bug that was found, fixed, and must not come back. Hard failure.
    machine     a model's reading, adversarially checked but not human-confirmed.
                Reported, but does not fail the run - it is evidence, not truth.

A golden_page assertion scores whole pages against a person's transcription and
always reports its metrics.  It fails only against a ratchet (maxErrors,
minMarkRecall, minMarkPrecision, maxStructureErrors) recorded from a measured
output - the case's ratchetFrom names it - so a change may not make any of
them worse; without a ratchet it is a measurement ("info"), not a verdict.

The ratchet may differ by stage: the same golden is scored on phase-3 pages
(the GPU run, where two runs of one code differ) and on repaired pages
(model-off, deterministic).  "ratchets": {"phase3": {"maxErrors": ...},
"repaired": {...}} holds each stage's fields (and its own ratchetFrom), and
--stage picks one: that block replaces the top-level fields.  Without --stage,
or for a stage the assertion has no block for, the top-level fields are the
ratchet, as before.  A golden_table passes only when every cell is right,
unless it carries a ratchet (minCellsExact, also per stage): then it fails only
when fewer cells are right than the ratchet records.

A golden_page also scores the page's structure (structureErrors).  Each line,
of the golden and of the output, is a heading (any level), a list item (a -,
* or + bullet; a printed number such as 1. or （一） is text), a footnote (a
line opening with a note marker: the golden's [^N]:, the output's ①), a table
row, or plain.  Through the character alignment each golden line is paired
with the output line holding most of its characters, and each output line
with its golden line; a pair of two kinds is an error (structureMismatches):
a heading lost or added, plain lines made a list, a note written as a list
item or as body text.  Pairs with a table row are left to the table scoring,
and heading levels that differ are listed apart (headingLevels), not counted:
the levels are a book-wide choice.  A line with no Han character pairs with
nothing, so a note of Western text alone is not compared.

A note marker is one mark token carrying its number, "[^N]", in either
convention: the goldens' Markdown footnotes ([^N] in the text, [^N]: at the
note) and the circled figures the pipeline writes (_NOTE_MARKER has the
forms), so a kept marker scores right whichever way it is written.

Without --case every case is tried, and a case is skipped when every page it
recorded a render hash for (source.renderSha256) has another one in the output:
the output is another book's.  A case named with --case is always scored.

Usage:
    python3 tests/run_regression.py OUTPUT_DIR                # every case whose book this is
    python3 tests/run_regression.py OUTPUT_DIR --case cangwingming
    python3 tests/run_regression.py OUTPUT_DIR --strict       # machine fails too
    python3 tests/run_regression.py OUTPUT_DIR --require-all-pages   # missing page = fail
    python3 tests/run_regression.py BOOK/repaired/pages --stage repaired   # that stage's ratchets
"""

from __future__ import annotations

import argparse
import collections
import difflib
import itertools
import json
import re
from pathlib import Path
import sys
import unicodedata

CASES = Path(__file__).resolve().parent / "cases"
GOLDEN = Path(__file__).resolve().parent / "golden"
HARD = {"human", "regression"}
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jyut_ocr.core import tables as _tables  # noqa: E402  (scripts/ is not a package)
from jyut_ocr.core.trees import brace_tree, tree_label, tree_parses  # noqa: E402,F401  (golden_tree; one parser with the pipeline)


def cjk(text: str) -> str:
    # Same character classes the merger keeps: BMP unified ideographs,
    # Extension B and beyond, and 〇々.  Extension-B glyphs used to be dropped
    # here, which turned `absent: 𠯲` into a check for the empty string.
    return "".join(c for c in text
                   if "㐀" <= c <= "鿿" or "\U00020000" <= c <= "\U0003134F" or c in "〇々")


EQUIVALENTS = Path(__file__).resolve().parents[1] / "references" / "variant-equivalents.json"
ALLOGRAPHS = EQUIVALENTS.with_name("opencc-allographs.txt")


def _classes(path: Path = EQUIVALENTS) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("classes") or []
    except (OSError, ValueError):
        return []


def _allograph_groups(path: Path = ALLOGRAPHS) -> list[list[str]]:
    """One list per line of OpenCC's allograph list (the # lines are its header)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [list(dict.fromkeys(c for c in line if not c.isspace()))
            for line in lines if line.strip() and not line.startswith("#")]


def _fold_table(path: Path = EQUIVALENTS, allographs: Path | None = None) -> dict[int, str]:
    """Map every form in an acceptable class to one representative.

    The user ruled those swaps not to be errors (e.g. 即/卽), so a golden page
    must score whichever form the output writes.  Classes sharing a character
    (并/幷 and 並/幷) are joined, so 并 and 並 fold together too.  With
    ALLOGRAPHS, OpenCC's allograph groups join as well - the second layer,
    below the user: a join that would put together any pair the user ruled
    apart is left out, so ruling an OpenCC pair apart in the user's list is
    how the user overrides it.  A user list that contradicts itself (a rejected
    pair joined by its own acceptable classes) stops the run.
    """
    classes = _classes(path)
    parent: dict[str, str] = {}
    def root(x: str) -> str:
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    def forms_of(entry: dict) -> list[str]:
        return [f for f in entry.get("forms") or [] if isinstance(f, str) and len(f) == 1]
    rejected = [pair for entry in classes if not entry.get("acceptable")
                for pair in itertools.combinations(forms_of(entry), 2)]
    for entry in classes:
        forms = forms_of(entry)
        if entry.get("acceptable"):
            for f in forms[1:]:
                parent[root(f)] = root(forms[0])
    joined = ["/".join(pair) for pair in rejected if root(pair[0]) == root(pair[1])]
    if joined:
        raise SystemExit(f"the user's variant list accepts and rejects the same pair: {', '.join(joined)}")
    for group in _allograph_groups(allographs) if allographs is not None else []:
        for f in group[1:]:
            a, b = root(group[0]), root(f)
            if a != b and not any({root(x), root(y)} == {a, b} for x, y in rejected):
                parent[b] = a
    return {ord(c): root(c) for c in parent if root(c) != c}


# FOLD holds both layers and scores golden pages; USER_FOLD, the user's layer
# alone, tells which layer accepted a swap and is what single assertions use:
# an assertion is a person's ruling on a form, which OpenCC must not wave through.
# Both lists live in the skill's references/: a copy of tests/ run outside the
# skill tree finds neither and would score every acceptable swap as an error.
for _listing in (EQUIVALENTS, ALLOGRAPHS):
    if not _listing.is_file():
        raise SystemExit(f"variant list not found: {_listing} (run the scorer from inside the skill tree)")
FOLD = _fold_table(allographs=ALLOGRAPHS)
USER_FOLD = _fold_table()


# Item bullets the user ruled symbol variants of one bullet, accepted either
# way (decision D5 after the baseline): a solid disc ●, a circle with a dot
# ⦿ and a fisheye ◉, each printed single or doubled (⦿⦿).  A run of them, in
# the golden or the output, is folded to one ● wherever marks are compared,
# so any shape matches any other; the goldens keep the printed form.  Only
# these: ◎ (two concentric circles) and every other mark keep their shape.
_BULLET_RUN = re.compile("[●⦿◉]+")


def bullet_fold(text: str) -> str:
    return _BULLET_RUN.sub("●", text)


# A note marker, in the goldens' Markdown ([^12], and [^12]: opening the note)
# or as the pipeline writes it: a circled figure (①-㊿, and the engines'
# other circled sets ❶ ➀ ⓫ ⓵), past ㊿ a circled figure with superscript
# figures after it (⑦² for 72, ②⁷⁶ for 276), or superscript figures alone
# (⁷⁸).  Read by the character, golden [^1] against output ① was four marks
# missed and one extra.  Left as they are, because the same shapes print
# other things: parenthesised figures ((130), （134）: years, enumerations),
# a circled figure with ASCII figures after it (⑨0 for 90, but ①1996年 opens
# a note with a year), and LaTeX an engine left in a cell ($^{203}$).
_NOTE_MARKER = re.compile(r"\[\^(\d+)\]|[①-⑳㉑-㉟㊱-㊿⓪-⓿❶-➓][⁰¹²³⁴-⁹]*|[⁰¹²³⁴-⁹]+")


def note_marker(match: re.Match) -> str:
    """The mark token of a _NOTE_MARKER match: [^N], N its number."""
    number = match.group(1) or "".join(str(int(unicodedata.numeric(c))) for c in match.group(0))
    return f"[^{number}]"


def user_fold(text: str) -> str:
    return bullet_fold(text).translate(USER_FOLD)


def opencc_only(value: str, text: str) -> str:
    """' (…matches only as an OpenCC allograph…)' when VALUE is in TEXT under
    both layers but not under the user's, else ''."""
    return (" (it matches only if OpenCC allographs count; rule the pair in "
            "variant-equivalents.json if it should)") if fold(value) in fold(text) else ""


def fold(text: str) -> str:
    return bullet_fold(text).translate(FOLD)


def page_text(directory: Path, page: int) -> str | None:
    for suffix in (".md", ".txt"):
        candidate = directory / f"page-{page:04d}{suffix}"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None


def page_json(directory: Path, page: int) -> dict:
    candidate = directory / f"page-{page:04d}.json"
    if not candidate.is_file():
        return {}
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def page_tables(text: str) -> list[list[list[str]]]:
    """Every table on the page - Markdown or HTML - as rows of cell texts.

    Read by _tables, the parser the pipeline writes tables with and the auditor
    checks them with, so the three cannot disagree about what a table or a row
    is.  A merged cell's text stands in every row it spans: a row of a table
    whose notes cell covers three rows still carries that note.  A blank row is
    a row - the separator line (| --- |) must actually contain dashes - because
    dropping blank rows once shifted every row below them.
    """
    return [table.grid(fill_spans=True) for table in _tables.find_tables(text)]


def longest_repeat(text: str) -> int:
    return max((len(list(group)) for _, group in itertools.groupby(text)), default=0)


def squash(value: str) -> str:
    return re.sub(r"\s+", "", value)


# --- whole-page golden -------------------------------------------------------

_METADATA = re.compile(r"\A\s*---[ \t]*\n(?:.*?\n)?---[ \t]*(?:\n|\Z)", re.S)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}[-:| \t]*$", re.M)
_SHAPE_TWIN = {"。。": "。", "。": "。。"}


def _is_han(c: str) -> bool:
    # cjk()'s classes plus the compatibility ideographs (U+F900-FAFF): a golden
    # may print one, and scoring it as a mark would turn a right character into
    # a wrong mark.  Escaped, because NFC normalisation maps each literal
    # compatibility ideograph to its unified twin and would move the range.
    return bool(cjk(c)) or "\uf900" <= c <= "\ufaff"


def strip_metadata(text: str) -> tuple[str, bool]:
    """The text without a leading --- metadata block, and whether it had one.

    The block is bibliographic (a reprint's title page goes there by
    convention), not page text.  Its position decides, not its lines: the
    user writes it free-form - of the 111 transcriptions in jyut-ocr that open
    with one, 12 hold YAML list items or bare title-page lines with no colon -
    so a rule on the lines' shape scored those blocks as body text.  What
    position alone could mistake is a page that opens with a --- rule and has
    another further down; none of the 830 output pages (page-NNNN.md) in the
    corpus and ~/ocr-runs opens with ---.
    """
    match = _METADATA.match(text)
    return (text[match.end():], True) if match else (text, False)


def golden_units(text: str) -> tuple[str, dict[int, list[str]]]:
    """The characters of a page and the mark tokens in each gap between them.

    gaps[i] holds the marks written before character i (after character i-1).
    。。 is ONE token: a printed double circle written as a single 。 is a shape
    error, not half a hit.  Measured on the golden with the most double circles
    (490): the best run so far wrote 244 of them single, which counting 。 by
    the character would have scored as 244 half-right marks.  A run of the
    bullets the user ruled one (●, ⦿, ◉, single or doubled; bullet_fold) is
    one token, ●, and a note marker is one token, [^N] (note_marker).
    """
    return page_units(text)[:2]


def page_units(text: str) -> tuple[str, dict[int, list[str]], list[str], list[int]]:
    """golden_units' characters and gaps, the page's lines, and the line each
    character is on.  The lines are taken before the markup goes, so a row's
    <tr> is still there for line_kind, and every cut keeps its line breaks,
    so the numbers hold."""
    text = strip_metadata(text)[0]
    breaks = lambda match: "\n" * match.group(0).count("\n")
    text = re.sub(r"<!--.*?-->", breaks, text, flags=re.S)    # page markers, notes
    lines = text.split("\n")
    text = re.sub(r"〔印：[^〕]*〕", breaks, text)              # seals are not the book's text: never scored (user ruling)
    text = re.sub(r"<[^>]+>", breaks, text)                   # HTML table tags
    text = _TABLE_RULE.sub("", text)                          # | --- | rules are syntax, not dashes
    text = re.sub(r"^[^\S\n]*(#+|[-*>]|\|)[^\S\n]*", "", text, flags=re.M)
    text = re.sub(r"^([^\S\n]*\[\^\d+\]):", r"\1", text, flags=re.M)   # a note's head [^N]: is its marker
    text = text.replace("|", "").replace("*", "")
    chars: list[str] = []
    char_line: list[int] = []
    gaps: dict[int, list[str]] = collections.defaultdict(list)
    i = line = 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            line += c == "\n"
            i += 1
        elif _is_han(c):
            chars.append(c)
            char_line.append(line)
            i += 1
        elif text.startswith("。。", i):
            gaps[len(chars)].append("。。")
            i += 2
        elif _BULLET_RUN.match(text, i):
            gaps[len(chars)].append("●")
            i = _BULLET_RUN.match(text, i).end()
        elif marker := _NOTE_MARKER.match(text, i):
            gaps[len(chars)].append(note_marker(marker))
            i = marker.end()
        else:
            gaps[len(chars)].append(c)
            i += 1
    return "".join(chars), gaps, lines, char_line


_HEADING = re.compile(r"[^\S\n]*(#+)\s")
_LIST_ITEM = re.compile(r"[^\S\n]*[-*+]\s")
# A note's head: the golden's [^N]:, or a note marker as the pipeline writes
# one opening the line.  [^N] without the colon is a marker in the text.
_NOTE_HEAD = re.compile(r"[^\S\n]*(\[\^\d+\]:|(?!\[)(" + _NOTE_MARKER.pattern + "))")
_TABLE_ROW = re.compile(r"[^\S\n]*(\||</?t(able|head|body|r|h|d)\b)")


def line_kind(line: str) -> str:
    """heading, list, footnote, table or plain (the structure score).  A list
    item is a bullet; a printed number (1. or （一）) is the line's text."""
    if _HEADING.match(line):
        return "heading"
    if _LIST_ITEM.match(line):
        return "list"
    if _NOTE_HEAD.match(line):
        return "footnote"
    if _TABLE_ROW.match(line):
        return "table"
    return "plain"


def score_structure(g_lines: list[str], g_line: list[int], o_lines: dict, o_line: list,
                    anchors: list[tuple[int, int]]) -> tuple[list[dict], list[dict]]:
    """The kind errors and the heading levels that differ, between the golden
    lines and the output lines (line_kind).  G_LINE and O_LINE give the line
    of each character, an index of G_LINES and a key of O_LINES.

    Each golden line is paired with the output line that holds most of its
    aligned characters, and each output line with the golden line most of
    its characters come from; the pairs of both ways are compared once.  The
    first way finds a heading run into a paragraph, the second a heading
    made of the first words of one."""
    shared = collections.Counter((g_line[i], o_line[j]) for i, j in anchors)
    to_output: dict = {}
    to_golden: dict = {}
    for (gl, ol), n in shared.items():
        if n > to_output.get(gl, (0, None))[0]:
            to_output[gl] = (n, ol)
        if n > to_golden.get(ol, (0, None))[0]:
            to_golden[ol] = (n, gl)
    pairs = {(gl, ol) for gl, (_, ol) in to_output.items()} | {(gl, ol) for ol, (_, gl) in to_golden.items()}
    errors, levels = [], []
    for gl, ol in sorted(pairs):
        gk, ok = line_kind(g_lines[gl]), line_kind(o_lines[ol])
        found = {"at": g_lines[gl].strip()[:16], "written": o_lines[ol].strip()[:16], "page": ol[0]}
        if "table" in (gk, ok):
            continue                  # golden_table and tables_as_golden score tables
        if gk != ok:
            errors.append(dict(found, golden=gk, output=ok))
        elif gk == "heading":
            level = [len(_HEADING.match(line)[1]) for line in (g_lines[gl], o_lines[ol])]
            if level[0] != level[1]:
                levels.append(dict(found, golden=level[0], output=level[1]))
    return errors, levels


def score_golden_page(golden_text: str, pages: list[tuple[int, str]],
                      stop_at: str | None = None) -> dict:
    """Characters and marks of the output pages against a golden transcription.

    Characters: difflib alignment of the CJK text with the user-ruled
    acceptable variants folded on both sides, so a swap inside a class is
    aligned as a match and counted apart as an acceptable swap.  Aligning the
    raw text instead turned golden 卽 against output 〇即 into one 2-character
    replacement, i.e. 2 errors, where the truth is 1 inserted 〇 + 1 acceptable
    swap.  Errors = substitutions + insertions + deletions; CER = errors /
    golden characters.

    Marks: judged in the regions between consecutive aligned characters.  A
    region with no character on one side - a pure insertion or deletion, or
    two adjacent aligned characters - is compared as a multiset with the other
    side's gaps in it, so a mark next to an inserted or dropped character still
    counts at its place instead of being both missed and extra.  An unequal
    replacement has no character correspondence inside it, so only its two
    boundary gaps (after the aligned character before it, before the aligned
    character after it) are compared; a mark inside it is missed or extra.
    Pooling the whole replacement instead gave full credit to an output whose
    every mark sat one character off inside a run of wrong characters.
    len(extraMarks) == marksOutput - marksRight: a written twin consumed as a
    shape confusion (。 for a printed 。。) is listed with shapeOf.

    stop_at: CJK text that begins the part the golden does not cover (a golden
    that ends mid-page).  The output is cut there, so the rest of the page is
    not scored as insertions.  Without it the whole output counts, and text the
    output adds after the golden's end is an error like any other.

    metadataBlock: whether a leading --- block was set aside, for the golden
    and per output page, so moving text into or out of it is visible.

    Structure: score_structure on the same alignment, an output line keyed
    by its page and its line there.
    """
    g, g_gaps, g_lines, g_line = page_units(golden_text)
    o, o_page, o_meta = "", [], []
    o_lines: dict[tuple[int, int], str] = {}
    o_line: list[tuple[int, int]] = []
    # A mark keeps the page it was written on: a gap between two pages holds
    # the first page's closing marks and the second page's opening ones.
    o_gaps: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for page, text in pages:
        if strip_metadata(text)[1]:
            o_meta.append(page)
        chars, gaps, lines, char_line = page_units(text)
        for slot, tokens in gaps.items():
            o_gaps[len(o) + slot].extend((t, page) for t in tokens)
        o += chars
        o_page += [page] * len(chars)
        o_lines.update(((page, n), line) for n, line in enumerate(lines))
        o_line += [(page, n) for n in char_line]
    gf, of = fold(g), fold(o)

    cut, anchor_found = len(o), None
    if stop_at:
        anchor = fold(golden_units(stop_at)[0])
        found = [m.start() for m in re.finditer(re.escape(anchor), of)] if anchor else []
        anchor_found = bool(found)
        if len(found) == 1:
            cut = found[0]
        else:
            # Zero or several occurrences: where the golden's last aligned
            # character lands decides, so an earlier repeat of the anchor text
            # cannot cut the page short.
            blocks = [b for b in difflib.SequenceMatcher(None, gf, of, autojunk=False)
                      .get_matching_blocks() if b.size]
            end = blocks[-1].b + blocks[-1].size if blocks else len(o)
            cut = min(found, key=lambda k: abs(k - end)) if found else end
    o, of, o_page = o[:cut], of[:cut], o_page[:cut]

    def context(text: str, a: int, b: int) -> str:
        return f"{text[max(0, a - 5):a]}[{text[a:b]}]{text[b:b + 5]}"

    anchors: list[tuple[int, int]] = []
    edits: list[dict] = []
    errors = swaps = opencc = 0
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, gf, of, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and a2 - a1 == b2 - b1):
            anchors.extend(zip(range(a1, a2), range(b1, b2)))   # a substitution keeps its place
        if tag == "equal":
            for i, j in zip(range(a1, a2), range(b1, b2)):
                if g[i] != o[j]:
                    swaps += 1
                    user = g[i].translate(USER_FOLD) == o[j].translate(USER_FOLD)
                    opencc += not user
                    edits.append({"label": "acceptable" if user else "acceptable-opencc",
                                  "golden": context(g, i, i + 1),
                                  "output": context(o, j, j + 1), "page": o_page[j]})
            continue
        # difflib leaves no common character inside a replace block, so the
        # block's edit distance is exactly its longer side.
        n = max(a2 - a1, b2 - b1)
        errors += n
        edits.append({"label": "error", "op": tag, "n": n, "golden": context(g, a1, a2),
                      "output": context(o, b1, b2),
                      "page": o_page[min(b1, len(o_page) - 1)] if o_page else None})
    structure, levels = score_structure(g_lines, g_line, o_lines, o_line, anchors)

    golden_n, output_n, right = collections.Counter(), collections.Counter(), collections.Counter()
    shape: collections.Counter = collections.Counter()
    missed, extra = [], []

    def at(text: str, s: int) -> str:
        return text[max(0, s - 6):s] + "|" + text[s:s + 3]

    def judge(golden_slots: range | list[int], output_slots: range | list[int]) -> None:
        want = [(s, t) for s in golden_slots for t in g_gaps.get(s, ())]
        got = [(s, t, p) for s in output_slots for t, p in o_gaps.get(s, ())]
        golden_n.update(t for _, t in want)
        output_n.update(t for _, t, _ in got)
        unmatched = []
        for s, t in want:
            k = next((k for k, item in enumerate(got) if item[1] == t), None)
            if k is None:
                unmatched.append((s, t))
            else:
                got.pop(k)
                right[t] += 1
        for s, t in unmatched:
            miss = {"mark": t, "at": at(g, s), "_slot": s}
            k = next((k for k, item in enumerate(got) if item[1] == _SHAPE_TWIN.get(t)), None)
            if k is not None:
                js, jt, jp = got.pop(k)
                shape[f"{t} written {jt}"] += 1
                miss["writtenAs"] = jt
                extra.append({"mark": jt, "at": at(o, js), "page": jp, "shapeOf": t, "_slot": js})
            missed.append(miss)
        extra.extend({"mark": t, "at": at(o, s), "page": p, "_slot": s} for s, t, p in got)

    prev_i = prev_j = -1
    for i, j in anchors + [(len(g), len(o))]:        # the sentinel closes the gap after the last character
        if i - prev_i > 1 and j - prev_j > 1:          # unequal replacement: characters on both sides
            judge([prev_i + 1], [prev_j + 1])
            judge([i], [j])
            judge(range(prev_i + 2, i), ())
            judge((), range(prev_j + 2, j))
        else:
            judge(range(prev_i + 1, i + 1), range(prev_j + 1, j + 1))
        prev_i, prev_j = i, j
    for found in (missed, extra):                     # judged boundary-first; report in text order
        found.sort(key=lambda item: item.pop("_slot"))

    marks_golden, marks_output, marks_right = (sum(c.values()) for c in (golden_n, output_n, right))
    return {
        "goldenChars": len(g), "outputChars": len(o),
        "errors": errors, "cer": round(100 * errors / max(len(g), 1), 2),
        "legacy_alignment_cer": round(100 * errors / max(len(g), 1), 2),
        "cerMetric": "legacy_alignment_cer",
        "acceptableSwaps": swaps,
        # Of those, the swaps only OpenCC's allograph list accepts (not the user's).
        "acceptableSwapsOpencc": opencc,
        "marksGolden": marks_golden, "marksOutput": marks_output, "marksRight": marks_right,
        "markRecall": round(100 * marks_right / max(marks_golden, 1), 1),
        "markPrecision": round(100 * marks_right / max(marks_output, 1), 1),
        "marks": {t: {"golden": golden_n[t], "output": output_n[t], "right": right[t]}
                  for t in sorted(set(golden_n) | set(output_n))},
        "shapeConfusions": dict(shape),
        "structureErrors": len(structure), "structureMismatches": structure, "headingLevels": levels,
        "stopAnchorFound": anchor_found,
        "metadataBlock": {"golden": strip_metadata(golden_text)[1], "outputPages": o_meta},
        "edits": edits, "missedMarks": missed, "extraMarks": extra,
    }


# Ratchet fields: (assertion field, metric, True when a larger metric is better).
RATCHETS = (("maxErrors", "errors", False),
            ("minMarkRecall", "markRecall", True),
            ("minMarkPrecision", "markPrecision", True),
            ("maxStructureErrors", "structureErrors", False))
# A golden_table's ratchet: without it the table passes only when every cell is right.
TABLE_RATCHETS = (("minCellsExact", "cellsExact", True),)
# The stage the output is from (--stage): an assertion's "ratchets" block for
# it replaces the top-level fields.  None reads the top-level fields.
STAGES = ("phase3", "repaired")
STAGE: str | None = None


def golden_page_summary(metrics: dict) -> str:
    double = metrics["marks"].get("。。")
    written_single = metrics["shapeConfusions"].get("。。 written 。", 0)
    parts = [f"errors {metrics['errors']}/{metrics['goldenChars']} (CER {metrics['cer']:.2f}%)",
             f"acceptable swaps {metrics['acceptableSwaps']}"
             + (f" ({metrics['acceptableSwapsOpencc']} by OpenCC allographs)"
                if metrics.get("acceptableSwapsOpencc") else ""),
             f"marks recall {metrics['markRecall']}% precision {metrics['markPrecision']}% "
             f"({metrics['marksRight']}/{metrics['marksGolden']} golden, {metrics['marksOutput']} written)"]
    if double:
        parts.append(f"。。 {double['right']}/{double['golden']} right, {written_single} written 。")
    kinds = collections.Counter(f"{e['golden']} as {e['output']}" for e in metrics["structureMismatches"])
    parts.append(f"structure errors {metrics['structureErrors']}"
                 + (f" ({', '.join(f'{k} {n}' for k, n in kinds.most_common())})" if kinds else "")
                 + (f", heading level differs on {len(metrics['headingLevels'])} line(s) (not an error)"
                    if metrics["headingLevels"] else ""))
    if metrics["stopAnchorFound"] is False:
        parts.append("stopAt anchor NOT found (cut after the last aligned character)")
    if metrics["metadataBlock"]["outputPages"]:
        parts.append("--- metadata block set aside on p." + ",".join(map(str, metrics["metadataBlock"]["outputPages"])))
    return "; ".join(parts)


def skip_between(pages: list[tuple[int, str]], after: str, before: str) -> tuple[list[tuple[int, str]], int]:
    """The pages with the lines strictly between an `after` line and the next
    `before` line taken out, and how many lines that was (-1 when no `before`
    line follows an `after` line; the text is then unchanged).

    For a golden_page whose page also prints something scored by another
    assertion - a chart between two paragraphs, read by golden_tree in whatever
    form the output draws it - so the chart's text is not counted as inserted
    characters here.  The cut starts at the LAST `after` line before the
    `before` line: an output that repeats the anchor sentence keeps its copies
    in the score (measured: a looped anchor line cut from the first copy scored
    as clean).  Lines are searched across the pages in order, so a chart that
    runs over a page break is cut too.  Anchors are compared on characters
    alone, with the user-ruled variants folded, as stopAt is."""
    want_after, want_before = fold(cjk(after)), fold(cjk(before))
    if not want_after or not want_before:
        return pages, -1
    lines = [(k, line) for k, (_, text) in enumerate(pages) for line in text.split("\n")]
    keys = [fold(cjk(line)) for _, line in lines]
    first = next((n for n, key in enumerate(keys) if want_after in key), None)
    end = (next((n for n in range(first + 1, len(keys)) if want_before in keys[n]), None)
           if first is not None else None)
    if end is None:
        return pages, -1
    start = max(n for n in range(first, end) if want_after in keys[n])
    kept = lines[:start + 1] + lines[end:]
    out = [(page, "\n".join(line for k2, line in kept if k2 == k)) for k, (page, _) in enumerate(pages)]
    return out, end - start - 1


def tables_as_golden(golden: str, pages: list[tuple[int, str]], stop_at: str | None,
                     turned: list[str]) -> tuple[list[tuple[int, str]], dict]:
    """PAGES with each of their tables written in the orientation
    (_tables.orientations: as written, transposed) the page score reads
    best: another orientation only where the pages score strictly fewer
    errors with it than with the table as written - a turn is taken for
    what it does to the score, never for where its cells stand alone.  A
    note per table turned goes into TURNED.  The cells keep their own text,
    marks and all: only the order the page score reads them in changes.
    Returns the pages and their metrics (score_golden_page).

    Found in review: choosing the turn by the cells' places alone, a table
    written in the golden's own order, whose title the output wrote as a
    line above it instead of a title row, was read upside down - the missing
    title row put no cell at its place as written - and a page with no error
    scored 60%."""
    pages = list(pages)
    metrics = score_golden_page(golden, pages, stop_at)
    for index, (page, text) in enumerate(pages):
        for table in reversed(_tables.find_tables(text)):
            best = None
            for name, turn in _tables.orientations(table)[1:]:
                trial = pages[:index] + [(page, text[:table.start] + _tables.render(turn) + text[table.end:])] \
                    + pages[index + 1:]
                scored = score_golden_page(golden, trial, stop_at)
                if scored["errors"] < (best or (None, None, metrics))[2]["errors"]:
                    best = (name, trial, scored)
            if best is not None:
                name, pages, metrics = best
                text = pages[index][1]
                turned.append(f"p.{page} {name}")
    return pages, metrics


def check_golden_page(assertion: dict, directory: Path, require_all: bool) -> tuple[str, str, dict | None]:
    first, last = assertion.get("pages") or (assertion["page"], assertion["page"])
    pages = [(page, page_text(directory, page)) for page in range(first, last + 1)]
    missing = [page for page, text in pages if text is None]
    if missing:
        # A partial output would score the absent pages as deleted text, a
        # number that measures the run's coverage rather than its reading.
        return ("fail" if require_all else "skip"), f"page(s) {missing} not in this output", None
    golden = (GOLDEN / assertion["value"]).read_text(encoding="utf-8")
    skipped = None
    if assertion.get("skipBetween"):
        pages, skipped = skip_between(pages, *assertion["skipBetween"])
    # A table written in the other orientation is read in the golden's
    # (tables_as_golden), as golden_table scores it: the user's ruling of
    # 2026-09-27 accepts either orientation.
    turned: list[str] = []
    if _tables.find_tables(golden):
        pages, metrics = tables_as_golden(golden, pages, assertion.get("stopAt"), turned)
    else:
        metrics = score_golden_page(golden, pages, assertion.get("stopAt"))
    detail = golden_page_summary(metrics)
    if turned:
        metrics["tablesTurned"] = turned
        detail += "; table(s) read turned: " + ", ".join(turned)
    if skipped is not None:
        metrics["skippedLines"] = skipped
        detail += (f"; {skipped} line(s) between the skipBetween anchors not scored" if skipped >= 0
                   else "; skipBetween: no `before` line after an `after` line (nothing skipped)")
    judged = judge_ratchets(assertion, metrics, detail, RATCHETS)
    return judged if judged else ("info", detail, metrics)


def judge_ratchets(assertion: dict, metrics: dict, detail: str,
                   fields: tuple) -> tuple[str, str, dict] | None:
    """(status, detail, metrics) against the assertion's ratchet, None without one.

    The --stage block, when the assertion has one for that stage, replaces the
    top-level fields."""
    stage = (assertion.get("ratchets") or {}).get(STAGE)
    bounds = assertion if stage is None else stage
    named = "" if stage is None else f" ({STAGE})"
    set_ratchets = [r for r in fields if r[0] in bounds]
    if not set_ratchets:
        return None
    broken, better = [], []
    for field, metric, larger_better in set_ratchets:
        bound, value = bounds[field], metrics[metric]
        if (value < bound) if larger_better else (value > bound):
            broken.append(f"{metric} {value} vs {field} {bound}")
        elif value != bound:
            better.append(f"{field} {bound}->{value}")
    if broken:
        return "fail", detail + f" | RATCHET{named} BROKEN: " + ", ".join(broken), metrics
    if better:
        detail += f" | better than the ratchet{named}; tighten " + ", ".join(better)
    return "pass", detail, metrics


def check(assertion: dict, directory: Path, require_all: bool = False) -> tuple[str, str, dict | None]:
    """Return (status, detail, metrics).

    Status is pass, fail, skip or info (a measurement with no ratchet to judge
    it against).  metrics is None except for the golden assertions; its cer is
    a percent in both.
    require_all: a page the case names but the output lacks is a failure
    instead of a skip (default skip, because partial runs are normal).
    """
    if assertion["type"] == "golden_page":
        return check_golden_page(assertion, directory, require_all)
    if assertion["type"] == "golden_table":
        return check_golden_table(assertion, directory, require_all)
    if assertion["type"] == "golden_tree":
        return check_golden_tree(assertion, directory, require_all)
    return (*check_text(assertion, directory, require_all), None)


# Decision D10 (the user, 2026-09-26): a node the golden marks uncertain - where
# it attaches is not clear on the scan - may be under any of the parents the
# assertion lists (uncertainParents).  --no-uncertain-parents turns it off: the
# golden's own parent only.
UNCERTAIN_PARENTS = True


def settle_uncertain(edges: list[tuple[str, str]], golden_edges: list[tuple[str, str]],
                     uncertain: dict[str, list[str]]) -> tuple[collections.Counter, list[dict]]:
    """The output's links, with a node the golden marks uncertain counted under
    the golden's parent where the output put it under another parent the
    assertion allows (decision D10); and each such link taken.

    Only a golden link the output lacks is settled, and by one link the output
    has and the golden does not: a node written under two allowed parents
    keeps one of them wrong.  The golden's own parent is always allowed."""
    want, got = collections.Counter(golden_edges), collections.Counter(edges)
    taken = []
    for child, parents in (uncertain or {}).items():
        missing = [e for e in (want - got).elements() if e[1] == child]
        allowed = set(parents) | {e[0] for e in missing}
        spare = [e for e in (got - want).elements() if e[1] == child and e[0] in allowed]
        for golden, written in zip(missing, spare):
            got[written] -= 1
            got[golden] += 1
            taken.append({"child": child, "golden": golden[0], "written": written[0]})
    return +got, taken


def check_golden_tree(assertion: dict, directory: Path, require_all: bool) -> tuple[str, str, dict | None]:
    # A printed chart (a brace tree, an organisation chart) a person rendered as
    # a nested list.  Passing needs every parent->child link and every bracketed
    # note of the golden; the output may draw it in any form tree_parses reads.
    # A node the golden marks uncertain (uncertainParents: child -> the parents
    # it may be under) counts under any of them (decision D10, settle_uncertain).
    page = assertion["page"]
    text = page_text(directory, page)
    if text is None:
        return ("fail" if require_all else "skip"), f"page {page} not in this output", None
    golden_text = (GOLDEN / assertion["value"]).read_text(encoding="utf-8")
    golden_edges, golden_notes = tree_parses(golden_text).get("list", ([], []))
    if not golden_edges:
        return "skip", f"no nested list in {assertion['value']}", None
    want = collections.Counter(golden_edges)
    names = {n for edge in golden_edges for n in edge}
    best, parsed_notes = None, []
    uncertain = (assertion.get("uncertainParents") or {}) if UNCERTAIN_PARENTS else {}
    for form, (edges, notes) in tree_parses(text).items():
        got, taken = settle_uncertain(edges, golden_edges, uncertain)
        right = sum((want & got).values())
        # A link between two of the golden's nodes that the golden does not have:
        # a node under the wrong parent, even if it is also under the right one.
        wrong = sum((collections.Counter({e: k for e, k in got.items() if e[0] in names and e[1] in names})
                     - want).values())
        if best is None or (right, -wrong) > (best["edgesRight"], -best["edgesWrong"]):
            best, parsed_notes = {"form": form, "edgesRight": right, "edgesWrong": wrong,
                                  "edgesWritten": len(edges), "uncertainParentsTaken": taken}, notes
    if best is None:
        best = {"form": None, "edgesRight": 0, "edgesWrong": 0, "edgesWritten": 0, "uncertainParentsTaken": []}
    # A note counts only as a bracketed note in the tree that was read (a note
    # wrapped over several lines of a drawing is joined back by the parse), not
    # as text found anywhere on the page.
    notes_right = sum(1 for n in golden_notes if any(n in p for p in parsed_notes))
    best.update({"edgesGolden": sum(want.values()), "notesGolden": len(golden_notes), "notesRight": notes_right})
    detail = (f"tree links {best['edgesRight']}/{best['edgesGolden']}, {best['edgesWrong']} wrong "
              f"(read as {best['form'] or 'no tree'}, {best['edgesWritten']} written), "
              f"notes {notes_right}/{len(golden_notes)}")
    if best["uncertainParentsTaken"]:
        detail += "; uncertain parent taken (uncertainParents, D10): " + ", ".join(
            f"{x['child']} under {x['written']} for the golden's {x['golden']}"
            for x in best["uncertainParentsTaken"])
    ok = (best["edgesRight"] == best["edgesGolden"] and best["edgesWrong"] == 0
          and notes_right == len(golden_notes))
    return ("pass" if ok else "fail"), detail, best


def check_golden_table(assertion: dict, directory: Path, require_all: bool) -> tuple[str, str, dict | None]:
    # A whole table a person transcribed from the page image.  Passing needs
    # the same shape and merged-cell layout and every cell right; the detail
    # line always carries the measurement, so progress shows before it passes.
    # A table printed across several pages ("pages": [first, last]) is one table:
    # its golden is the merged whole, and the output passes only if it writes
    # the merged table whole on one of those pages.  Tables are found page by
    # page (markup left open on one page must not run into the next) and the
    # best single table is compared, so output that leaves a piece of the table
    # on each page scores what one piece covers.
    first, last = assertion.get("pages") or (assertion["page"], assertion["page"])
    texts = [(page, page_text(directory, page)) for page in range(first, last + 1)]
    missing = [page for page, text in texts if text is None]
    if missing:
        return ("fail" if require_all else "skip"), f"page(s) {missing} not in this output", None
    golden_tables = _tables.find_tables((GOLDEN / assertion["value"]).read_text(encoding="utf-8"))
    if not golden_tables:
        return "skip", f"no table in {assertion['value']}", None
    # Cells are compared with the acceptable variants folded, as a golden
    # page's characters are: a cell writing the acceptable 算 for a printed
    # 𮅕 is right, not a wrong cell.
    folded = lambda table: _tables.with_texts(table, [fold(cell.text) for cell in table.cells])
    golden = folded(golden_tables[assertion.get("tableIndex", 0)])
    produced = [folded(table) for _, text in texts for table in _tables.find_tables(text)]
    if not produced:
        return "fail", f"no table in the output (golden {golden.rows}x{golden.cols})", None
    # Each table as written, or in the other orientation the user's ruling
    # accepts (_tables.orientations: transposed, the title and note rows kept)
    # where that scores better: a table printed in vertical columns is written
    # a record per row, and scored either way (2026-09-27) - a price table
    # written a column per row is not a wrong table.  Better means at least as
    # many cells exact and a CER at least as low, and one of them strictly: a
    # turn is never taken for its cells' places alone (found in review: a
    # table whose title row the output wrote as a line above it had every
    # cell off its place as written, and a turn with a cell or two at their
    # places by chance was scored at a CER eight times the table's own), nor
    # for a lower CER with fewer cells at their places (measured: a textbook
    # table with 17 cells exact as written read closer row by row turned,
    # with 2).
    def fitted(table: "_tables.Table") -> dict:
        found = [dict(_tables.compare(golden, turned), orientation=name)
                 for name, turned in _tables.orientations(table)]
        written = found[0]
        better = [fit for fit in found[1:] if fit["cellsExact"] >= written["cellsExact"] and fit["cer"] <= written["cer"]
                  and (fit["cellsExact"], -fit["cer"]) != (written["cellsExact"], -written["cer"])]
        return min(better, key=lambda fit: (-fit["cellsExact"], fit["cer"])) if better else written

    best = min((fitted(t) for t in produced), key=lambda m: (m["cer"], -m["cellsExact"]))
    if last > first:
        best = dict(best, tablesInOutput=len(produced))
    # _tables.compare gives a fraction; the metrics carry a percent, as a
    # golden_page's do, so metrics.cer means one thing across the JSON.
    best = dict(best, cer=round(100 * best["cer"], 2))
    best.update(legacy_alignment_cer=best["cer"], cerMetric="legacy_alignment_cer")
    detail = (f"shape {best['shape']} vs golden {best['goldenShape']}, cells {best['cellsExact']}/"
              f"{best['cells']} exact, CER {best['cer']:.1f}%")
    if best["orientation"] != _tables.AS_WRITTEN:
        detail += f"; scored {best['orientation']}"
    if last > first:
        detail += f"; {best['tablesInOutput']} table(s) written across pp.{first}-{last}"
    # With a ratchet (minCellsExact) a table may not lose a cell it had right;
    # without one it passes only whole.
    judged = judge_ratchets(assertion, best, detail, TABLE_RATCHETS)
    if judged:
        return judged
    ok = best["shapeMatch"] and best["cellsExact"] == best["cells"]
    return ("pass" if ok else "fail"), detail, best


def check_text(assertion: dict, directory: Path, require_all: bool) -> tuple[str, str]:
    kind = assertion["type"]

    if kind == "note":
        return "skip", "informational"

    if kind.startswith("all_pages"):
        offenders = []
        sealed = sorted(directory.glob("page-*.json"))
        if not sealed:
            return "skip", "no sealed JSON in this output"
        for path in sealed:
            record = json.loads(path.read_text(encoding="utf-8"))
            value = record.get(assertion["field"])
            if kind == "all_pages_metric_max":
                if value is not None and value > assertion["value"]:
                    offenders.append(f"p.{record.get('scanPage')}={value}")
            elif kind == "all_pages_empty" and value:
                offenders.append(f"p.{record.get('scanPage')}={value}")
        if offenders:
            return "fail", f"{len(offenders)} page(s): " + ", ".join(offenders[:6])
        return "pass", "all pages within range"

    missing = "fail" if require_all else "skip"
    page = assertion["page"]
    # "pages": [first, last] reads those pages as one text, in order: text in a
    # table printed across pages may be written wherever the output puts the
    # merged table.
    first, last = assertion.get("pages") or (page, page)
    texts = [page_text(directory, p) for p in range(first, last + 1)]
    if any(t is None for t in texts):
        gone = [p for p, t in zip(range(first, last + 1), texts) if t is None]
        return missing, f"page(s) {gone} not in this output"
    text = "\n".join(texts)

    if kind in ("contains", "absent"):
        # A value of characters alone is compared on characters alone, so
        # punctuation and layout cannot fail a character ruling.  A value that
        # has a mark in it (，, 。, a bracket) is about that mark, so it is
        # compared verbatim on the whitespace-squashed text, as contains_exact
        # is: projected to characters, `absent ，` became a check for the empty
        # string and could never pass, and `absent 卿，雲` or `contains 及半。遂復`
        # silently lost the very mark they pin.
        value = assertion["value"]
        if any(not cjk(c) and not c.isspace() for c in value):
            want, have = squash(value), squash(text)
        else:
            want, have = cjk(value), cjk(text)
        present = user_fold(want) in user_fold(have)
        if kind == "contains":
            return ("pass", "") if present else ("fail", f"missing {value!r}" + opencc_only(want, have))
        return ("fail", f"present but must not be: {value!r}") if present else ("pass", "")

    if kind == "contains_exact":
        # Verbatim, punctuation included; only whitespace is ignored, so a
        # dropped 。 fails where plain `contains` (characters only) would pass.
        want, have = squash(assertion["value"]), squash(text)
        return (("pass", "") if user_fold(want) in user_fold(have)
                else ("fail", f"missing exact {assertion['value']!r}" + opencc_only(want, have)))

    if kind == "json_field":
        record = page_json(directory, page)
        if not record:
            return missing, f"page {page} has no sealed JSON in this output"
        got = record.get(assertion["field"])
        return ("pass", "") if got == assertion["value"] else ("fail", f"{assertion['field']}={got!r}")

    if kind == "table_row":
        want = [cjk(c) for c in assertion["value"] if cjk(c)]
        for table in page_tables(text):
            for row in table:
                got = [cjk(c) for c in row if cjk(c)]
                if all(any(w in g for g in got) for w in want):
                    return "pass", ""
        return "fail", "no table row matching " + " | ".join(assertion["value"])

    if kind == "table_count":
        found = len(page_tables(text))
        if found >= assertion["value"]:
            return "pass", f"{found} table(s)"
        return "fail", f"{found} table(s), expected at least {assertion['value']}"

    if kind == "no_repeat_run":
        run = longest_repeat(cjk(text))
        return ("fail", f"longest repeated run is {run}") if run >= assertion["value"] else ("pass", "")

    return "skip", f"unknown assertion type {kind}"


def foreign_pages(case: dict, directory: Path) -> list[int]:
    """Pages whose sealed render is not the one the case recorded for its book.

    Empty unless every comparable page differs: one match means the output is
    this book's (a page re-rendered since is then scored and fails on its own
    merits), and a page with no sealed JSON or no recorded render is no
    evidence either way.  The render hash identifies the page image: across
    the 164 directories of page JSON in the corpus and ~/ocr-runs, every page a
    case names has exactly one render hash among its own book's sealed
    outputs, so a mismatch means another book's page of the same number.
    Without this check the all-cases run scored other books' pages against
    each golden: the table golden of one book failed on p.24 of three others.
    """
    known = (case.get("source") or {}).get("renderSha256") or {}
    same, other = [], []
    for page, sha in known.items():
        provenance = page_json(directory, int(page)).get("provenance")
        got = provenance.get("renderSha256") if isinstance(provenance, dict) else None
        if got:
            (same if got == sha else other).append(int(page))
    return [] if same else sorted(other)


def where(assertion: dict) -> str:
    pages = assertion.get("pages")
    if pages and pages[0] != pages[1]:
        return f"pp.{pages[0]}-{pages[1]}"
    return f"p.{assertion['page']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--case", action="append", help="case id (default: all)")
    parser.add_argument("--strict", action="store_true",
                        help="let machine-provenance assertions fail the run too")
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--require-all-pages", action="store_true",
                        help="a page a case names but the output lacks fails instead of skipping")
    parser.add_argument("--no-uncertain-parents", action="store_true",
                        help="decision D10 off: a golden_tree node the golden marks uncertain "
                             "(uncertainParents) counts only under the golden's own parent")
    parser.add_argument("--stage", choices=STAGES,
                        help="the stage the output is from: a golden_page's ratchets block for it "
                             "replaces its top-level ratchet fields")
    arguments = parser.parse_args(argv)
    global UNCERTAIN_PARENTS, STAGE
    UNCERTAIN_PARENTS = not arguments.no_uncertain_parents
    STAGE = arguments.stage

    files = sorted(CASES.glob("*.json"))
    if arguments.case:
        wanted = set(arguments.case)
        files = [f for f in files if f.stem in wanted]
    if not files:
        print("no cases found", file=sys.stderr)
        return 2

    results, hard_failures, other_books = [], 0, 0
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n{case['book']}  ({case['bookId']}, {len(case['assertions'])} assertions)")
        print(f"  covers    : {', '.join(case.get('traits', []))}")
        print(f"  NOT covered: {', '.join(case.get('notCovered', []))}")
        tally = {"pass": 0, "fail": 0, "skip": 0, "info": 0}
        foreign = foreign_pages(case, arguments.output_directory)
        if foreign:
            pages = ", ".join(f"p.{page}" for page in foreign)
            if not arguments.case:
                # Only the all-cases run guesses which cases apply; a case the
                # user names is scored whatever the renders say.
                other_books += 1
                print(f"  skipped: another book's output - its render differs from this book's on "
                      f"{pages} (source.renderSha256); name it with --case to score it anyway")
                for assertion in case["assertions"]:
                    results.append(dict(case=case["bookId"], id=assertion["id"],
                                        provenance=assertion["provenance"], status="skip",
                                        detail="output is another book's (render mismatch: " + pages + ")"))
                continue
            print(f"  warn: the output's render differs from this book's on {pages} "
                  f"(source.renderSha256); scored because --case names it")
        for assertion in case["assertions"]:
            status, detail, metrics = check(assertion, arguments.output_directory,
                                            arguments.require_all_pages)
            tally[status] += 1
            fails_run = status == "fail" and (
                assertion["provenance"] in HARD or arguments.strict)
            if fails_run:
                hard_failures += 1
            # A golden page's numbers are the point of it, so they are printed
            # whether it passed, failed or has no ratchet yet.
            if status == "fail" or (assertion["type"] == "golden_page" and status != "skip"):
                mark = {"pass": "pass", "info": "info"}.get(status, "FAIL" if fails_run else "warn")
                print(f"  {mark}  {where(assertion):<7} [{assertion['provenance']}] {detail}")
                if status == "fail":
                    print(f"        {assertion['note']}")
            result = dict(case=case["bookId"], id=assertion["id"],
                          provenance=assertion["provenance"], status=status, detail=detail)
            if metrics is not None:
                result["metrics"] = metrics
            results.append(result)
        print(f"  -> {tally['pass']} pass, {tally['fail']} fail, {tally['skip']} skip"
              + (f", {tally['info']} measured without a ratchet" if tally["info"] else ""))

    print(f"\n{'PASS' if not hard_failures else 'FAIL'}: "
          f"{hard_failures} hard failure(s) across {len(files)} case file(s)"
          + (f", {other_books} skipped as another book's" if other_books else ""))
    if arguments.json:
        arguments.json.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
