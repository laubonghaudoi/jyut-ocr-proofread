#!/usr/bin/env python3
"""Offline checks for space-clause books (--space-clause and notation.spaceClause):
fullwidth ideographic space U+3000 vs halfwidth ASCII space U+0020 across profile
notation parsing, Stage A census/punctuation/realignment, Stage B restoration and
paragraph breaks, page sealing, and final Markdown audit.

Usage: python3 tests/check_space_clause.py
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402,F401
import audit_ocr_markdown as audit  # noqa: E402
import book_profile as bp  # noqa: E402
import proofread_pages as pp  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# 1. Notation survey parsing & backwards compatibility.
check(
    "notation probe asks about 空格斷句",
    ("空格斷句：" in bp.NOTATION_SYSTEM, "空格斷句：" in bp.NOTATION_SYSTEM_SINGLE),
    (True, True),
)
check(
    "parse_notation omits spaceClause when absent or 冇",
    (
        "spaceClause" in bp.parse_notation("符號：。\n雙圈：冇\n圈點：冇\n點數：4"),
        "spaceClause" in bp.parse_notation("符號：無\n雙圈：冇\n圈點：冇\n點數：0\n空格斷句：冇"),
    ),
    (False, False),
)
check(
    "parse_notation records spaceClause when 有",
    bp.parse_notation("符號：無\n雙圈：冇\n圈點：冇\n點數：0\n空格斷句：有"),
    {"marks": [], "none": True, "doubled": False, "emphasis": None, "count": None, "spaceClause": True},
)

# 2. Context-aware normalization of CJK clause spaces vs ASCII spaces.
check(
    "normalize promotes CJK-to-CJK ASCII spaces to U+3000 and collapses runs",
    pp.normalize_cjk_clause_spaces("  甲乙丙丁   戊己庚辛\u3000\u3000壬癸子丑  "),
    "甲乙丙丁\u3000戊己庚辛\u3000壬癸子丑",
)
check(
    "normalize promotes spaces beside CJK quotes to U+3000",
    pp.normalize_cjk_clause_spaces("甲乙 「丙丁」 戊己"),
    "甲乙\u3000「丙丁」\u3000戊己",
)
check(
    "normalize keeps Western words, initials, and figure units as ASCII U+0020",
    pp.normalize_cjk_clause_spaces("G. P. Gooch 150 dpi 表 4-6 甲乙 丙丁"),
    "G. P. Gooch 150 dpi 表 4-6 甲乙\u3000丙丁",
)
check(
    "interior tile keeps leading U+3000 for cross-column seam assembly",
    pp.normalize_cjk_clause_spaces("\u3000戊己庚辛  ", trim_leading=False, trim_trailing=True),
    "\u3000戊己庚辛",
)


# 3. Stage A punctuate_part: normal book vs space-clause book (split & fused).
class MockClient:
    def __init__(self, replies: list[str], *, fused: bool = False):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str, str]] = []
        self.supports_fused_punctuate = fused

    def ask_answering(self, system: str, user: str, crop, max_tokens: int, kind: str = "", **kw):
        self.prompts.append((kind, system, user))
        return self.replies.pop(0), {}


SPAN = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳"

normal_args = SimpleNamespace(max_tokens=200, punctuation_mode="split", profile_data=None, space_clause=False)
normal_client = MockClient(["符號：0\n雙圈：0\n形狀：無"])
out_norm, rec_norm, _ = pp.punctuate_part(normal_client, normal_args, None, SPAN, "", 60.0)
check(
    "normal book uses exact CENSUS_SYSTEM identity",
    (normal_client.prompts[0][1] is pp.CENSUS_SYSTEM, out_norm, rec_norm["places"]),
    (True, SPAN, 0),
)

sc_args = SimpleNamespace(
    max_tokens=200,
    punctuation_mode="split",
    profile_data={"notation": {"spaceClause": True, "blocksSurveyed": 6}},
    space_clause=False,
)
sc_client = MockClient([
    "符號：3\n雙圈：0\n形狀：空格",
    "甲乙丙丁 戊己庚辛\u3000壬癸子丑\u3000寅卯辰巳",
])
out_sc, rec_sc, _ = pp.punctuate_part(sc_client, sc_args, None, SPAN, pp.notation_hint(sc_args), 60.0)
check(
    "space-clause split pass counts and normalizes U+3000 with zero doubts",
    (
        sc_client.prompts[0][1] is pp.CENSUS_SYSTEM_SPACE_CLAUSE,
        sc_client.prompts[1][1] is pp.PUNCT_SYSTEM_SPACE_CLAUSE,
        out_sc,
        rec_sc["ok"],
        rec_sc["places"],
        rec_sc.get("clauseSpaces"),
        pp.punctuation_doubts([dict(rec_sc, block=0)]),
    ),
    (
        True,
        True,
        "甲乙丙丁\u3000戊己庚辛\u3000壬癸子丑\u3000寅卯辰巳",
        True,
        3,
        3,
        [],
    ),
)

fused_args = SimpleNamespace(max_tokens=200, punctuation_mode="fused", profile_data=None, space_clause=True)
fused_client = MockClient(
    ["符號：2\n雙圈：0\n形狀：空格\n---PUNCTUATED---\n甲乙丙丁\u3000戊己庚辛壬癸子丑 寅卯辰巳"],
    fused=True,
)
out_f, rec_f, _ = pp.punctuate_part(fused_client, fused_args, None, SPAN, "", 60.0)
check(
    "space-clause fused pass keeps U+3000 and marks fusion success",
    (out_f, rec_f["ok"], rec_f["places"], rec_f["fusion"]["success"]),
    ("甲乙丙丁\u3000戊己庚辛壬癸子丑\u3000寅卯辰巳", True, 2, True),
)

# 4. Tile realignment & assembly preserve U+3000.
realigned = pp.seat_marks("甲乙丙丁\u3000戊己", "甲乙丙丁戊己", space_clause=True)
assembled, unread = pp.assemble_passes(
    "甲乙丙丁戊己庚辛",
    [("甲乙丙丁\u3000戊", 0, 5, 0, 4), ("戊己\u3000庚辛", 4, 8, 4, 8)],
    space_clause=True,
)
check(
    "seat_marks and assemble_passes preserve U+3000 across realigned tiles",
    (realigned, assembled, unread),
    ("甲乙丙丁\u3000戊己", "甲乙丙丁\u3000戊己\u3000庚辛", []),
)

# 5. Stage B restore_clause_spaces: inline restoration, paragraph break drop, Markdown delimiters.
sanctioned = "一　凡例\n甲乙丙丁\u3000戊己庚辛\u3000壬癸子丑\u3000寅卯辰巳"
formatted_dropped = "##\u3000一 凡例\n\n甲乙丙丁戊己庚辛 壬癸子丑\n\n\u3000寅卯辰巳\u3000"
restored = pp.restore_clause_spaces(sanctioned, formatted_dropped)
check(
    "Stage B restores same-line U+3000, drops U+3000 at paragraph break, and fixes ## delimiter",
    restored,
    "## 一　凡例\n\n甲乙丙丁　戊己庚辛　壬癸子丑\n\n寅卯辰巳",
)
check(
    "Stage B punctuation_kept passes when paragraph break replaces U+3000",
    pp.punctuation_kept(sanctioned, restored),
    True,
)

# 6. Provenance sealing: absent when off, 'on' when enabled via flag or profile.
off_prov = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A"))
flag_prov = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A", space_clause=True))
prof_prov = pp.decision_provenance(
    SimpleNamespace(prefer_engine="A", fallback_engine="A", profile_data={"notation": {"spaceClause": True}})
)
check(
    "spaceClause provenance sealed only when enabled",
    ("spaceClause" in off_prov, flag_prov.get("spaceClause"), prof_prov.get("spaceClause"),
     "spaceClause" in pp.OPTIONAL_PROVENANCE),
    (False, "on", "on", True),
)

# 7. Final Markdown audit: inline U+3000 clean, trailing U+3000 and ##\u3000 flagged.
clean_res = audit.audit_text("## 一　凡例\n\n甲乙丙丁　戊己庚辛。\n", mode="final")
trail_res = audit.audit_text("## 一　凡例\n\n甲乙丙丁　\n", mode="final")
delim_res = audit.audit_text("##\u3000一　凡例\n\n甲乙丙丁。\n", mode="final")
check(
    "audit_ocr_markdown accepts inline U+3000 and catches trailing/delimiter U+3000",
    (
        [i.code for i in clean_res.issues],
        [i.code for i in trail_res.issues],
        [i.code for i in delim_res.issues],
    ),
    ([], ["trailing-whitespace"], ["fullwidth-markdown-delimiter"]),
)

sys.exit(1 if failures else 0)
