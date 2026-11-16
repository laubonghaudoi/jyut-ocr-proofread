#!/usr/bin/env python3
"""Offline checks of the two variant layers: the user's rulings, then OpenCC.

No model, no GPU, no corpus.  The pairs are the ones the layers were checked
on when OpenCC's allograph list was adopted: the user's list decides first,
either way; OpenCC's allographs are acceptable where the user has not ruled;
and nothing joins a pair the user ruled apart.  OpenCC's traditional to
simplified table, which the lint reads for characters only traditional
script has (TEXT-TRADITIONAL), is vendored byte for byte too.

Usage:
    python3 tests/check_variant_tiers.py
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import proofread_pages as pp  # noqa: E402
import _book_lint as bl  # noqa: E402
import run_regression as rr  # noqa: E402

UPSTREAM_SHA256 = "edd6b79bb319996d54c5d68c99f12bd4bcf66312032e09b684d9da91664acd36"
TS_UPSTREAM_SHA256 = "9ff46a7d30e5765375eb13d33f2b03a34d298913caf2b120380679f33ae1642d"


def check_vendored_copy() -> None:
    for path, digest in ((pp.ALLOGRAPHS, UPSTREAM_SHA256), (bl.TS_CHARACTERS, TS_UPSTREAM_SHA256)):
        raw = path.read_bytes()
        header, marker, body = raw.partition(b"#---- upstream below ----\n")
        assert marker, f"{path.name} lost its header separator"
        assert hashlib.sha256(body).hexdigest() == digest, f"{path.name} was edited"
        assert digest.encode() in header, f"{path.name}'s header no longer names the upstream hash"
    print("vendored lists: byte-identical to the pinned upstream files")


def check_user_first() -> None:
    classes = json.loads(pp.VARIANT_LIST.read_text(encoding="utf-8"))["classes"]
    rejected = [c["forms"] for c in classes if not c["acceptable"]]
    assert rejected, "the user's list has no rejected pair to test with"
    for forms in rejected:
        for a, b in itertools.combinations(forms, 2):
            assert pp.classify_pair(a, b) == "different", (a, b)
            assert pp.variant_source(a, b) == "user", (a, b)
            assert a.translate(rr.FOLD) != b.translate(rr.FOLD), f"{a}/{b} folded together"
    # A pair the user accepted is a variant whatever OpenCC says.
    assert pp.classify_pair("即", "卽") == "variant"
    assert pp.variant_source("即", "卽") == "user"
    print(f"user first: {len(rejected)} rejected pair(s) stay apart in merging and scoring")


def check_opencc_layer() -> None:
    # 㑹/會 is on OpenCC's list and not on the user's.
    assert frozenset("㑹會") in pp.opencc_allographs()
    assert pp.variant_source("㑹", "會") == "opencc"
    assert pp.classify_pair("㑹", "會") == "variant"
    assert "㑹".translate(rr.FOLD) == "會".translate(rr.FOLD)
    assert "㑹".translate(rr.USER_FOLD) != "會".translate(rr.USER_FOLD)
    # Pairs dictionaries and the model were measured to get wrong are on neither list.
    for a, b in ("刺剌", "着著", "已巳"):
        assert pp.variant_source(a, b) is None, (a, b)
        assert pp.classify_pair(a, b) == "different", (a, b)
    print("OpenCC layer: allographs accepted where the user has not ruled; known false pairs untouched")


def check_lists_only() -> None:
    # A pair no list covers is two characters, whatever the model would say:
    # 圣鉴 printed four times in 天地会 3 was written 圣监 when the model
    # called 监/鉴 one character; 祛/袪 and 祇/祗 likewise.
    for a, b in ("监鉴", "祛袪", "祇祗", "於于"):
        assert pp.variant_source(a, b) is None, (a, b)
        assert pp.classify_pair(a, b) == "different", (a, b)
    assert pp.variant_segment({"a": "圣监", "b": "圣鉴"}) is None
    # OpenCC's simplified forms are the third list: a traditional character
    # and its simplified form are one character (後/后, 譯/译), but not a
    # character simplified script writes too (乾 is not only traditional).
    for a, b in ("後后", "譯译"):
        assert pp.variant_source(a, b) == "script", (a, b)
        assert pp.classify_pair(a, b) == "variant", (a, b)
    assert pp.variant_source("乾", "干") is None
    assert pp.variant_segment({"a": "譯本", "b": "译本"}) == [("譯", "译")]
    # The rule is sealed: a page decided when the model's answer could count
    # is not current.
    assert pp.decision_provenance(type("A", (), {})())["variantPairs"] == "lists"
    print("lists only: a pair no list covers is two characters; simplified forms are one character")


def check_scoring_labels() -> None:
    golden = "會議即日開始"
    output = "㑹議卽日開始"
    metrics = rr.score_golden_page(golden, [(1, output)])
    assert metrics["errors"] == 0, metrics["edits"]
    assert metrics["acceptableSwaps"] == 2 and metrics["acceptableSwapsOpencc"] == 1, metrics
    labels = sorted(e["label"] for e in metrics["edits"])
    assert labels == ["acceptable", "acceptable-opencc"], labels
    print("scoring: a swap only OpenCC accepts is counted and labelled apart")


def check_user_overrides_opencc(tmp: Path) -> None:
    listing = tmp / "list.json"
    groups = tmp / "groups.txt"
    groups.write_text("# header\n甲乙\n丙丁戊\n", encoding="utf-8")
    # Ruling an OpenCC pair apart, including one pair inside a longer group,
    # keeps it apart in scoring; the rest of the group still folds.
    listing.write_text(json.dumps({"classes": [
        {"forms": ["甲", "乙"], "acceptable": False},
        {"forms": ["丙", "戊", "己"], "acceptable": False}]}), encoding="utf-8")
    table = rr._fold_table(listing, groups)
    fold = lambda s: s.translate(table)
    assert fold("甲") != fold("乙"), "an OpenCC pair the user ruled apart was folded"
    assert fold("丙") != fold("戊"), "a pair inside a rejected class of three was folded"
    assert fold("丙") == fold("丁"), "the rest of the OpenCC group should still fold"
    # A user list that accepts and rejects the same pair is a contradiction.
    listing.write_text(json.dumps({"classes": [
        {"forms": ["甲", "乙"], "acceptable": True},
        {"forms": ["乙", "甲"], "acceptable": False}]}), encoding="utf-8")
    try:
        rr._fold_table(listing, groups)
    except SystemExit as error:
        assert "甲/乙" in str(error) or "乙/甲" in str(error), error
    else:
        raise AssertionError("a self-contradicting user list loaded without complaint")
    print("precedence: the user's rejection overrides OpenCC; a self-contradicting list stops the run")


def check_script_table() -> None:
    # Phase 3 reads OpenCC's traditional-to-simplified table whole: a character
    # it maps only to others exists only in traditional script, the variant
    # layers notwithstanding (戶/户 are allographs, and still differ in script).
    forms = pp.traditional_forms()
    assert forms["諭"] == "谕" and forms["崙"] == "仑𪨧" and forms["後"] == "后" and forms["戶"] == "户", forms.get("戶")
    assert "乾" not in forms and "於" not in forms, "simplified script writes 乾 and 於 too"
    assert "戶" not in bl.traditional_only(), "the lint leaves out what the variant layers join"
    # Which script a book is printed in: the simplified forms outnumber the
    # characters only traditional script has (天地会 3 scan 75's title as each
    # engine read it, and the date line below it, in both engines' drafts).
    title_a, title_b = "諭內閣著戶部酌拨銀一百萬兩解閩", "谕内阁著户部酌拨银一百万两解闽"
    date = "乾隆五十二年七月十三日内阁奉上谕台湾现在剿捕贼匪"
    assert pp.simplified_script([title_a, date, title_b, date])
    assert not pp.simplified_script([title_a]), "engine A's title alone: 8 traditional against 拨"
    assert not pp.simplified_script(["此事爲國家計淸理財政"])
    # An answer in traditional script, in the form an engine read: 天地会 3 scan
    # 40 (engine A 恳, engine B 悬, 裁決 懸), scan 93 (崙 printed; engines 崙
    # and 嵛, no engine read 仑), and a traditional form engine B read stands.
    assert pp.simplified_as_read("懸", "恳", "悬") == "悬"
    assert pp.simplified_as_read("崙", "崙", "嵛") == "崙"
    assert pp.simplified_as_read("並未", "并末", "並未") == "並未"
    assert pp.simplified_as_read("鳳", "凤", "风") == "凤"
    # Neither engine read the character or a form of it: its one simplified
    # form (天地会 1 scan 242: 蕆 where the engines read 藏 and 葳, 蒇 printed);
    # not one engine A read there (made up: engine A 蕆), nor one with two
    # forms (崙: 仑, 𪨧).
    assert pp.simplified_as_read("蕆", "藏", "葳") == "蒇"
    assert pp.simplified_as_read("一舉蕆事", "一舉藏事", "一举葳事") == "一举蒇事"
    assert pp.simplified_as_read("蕆", "蕆", "葳") == "蕆"
    assert pp.simplified_as_read("崙", "仓", "嵛") == "崙"
    # Written in engine A's form where engine B read another character, none of
    # the answer's forms in either script (converted_against_b): 天地会 3 scan 181
    # (engine A 尘, engine B 廑 as printed, 裁決 塵), 天地会 5 scan 82 (engine B 欽,
    # 钦 printed), 天地会 2 scan 64 (engine A 凤 as printed, engine B 风); not where
    # engine B read the form (scan 40's 悬, 天地会 5 scan 184's title), another
    # traditional form of it (made up: 髮 where engine B read 發, both 发) or nothing
    # (天地会 5 scan 13: 龍, engine A 龙).  So is the one form neither engine read
    # (天地会 1 scan 242's 蒇), where engine B read nothing too (天地会 1 scan 66:
    # 相連變化 where engine A read the printed 水为先地下) and where it read another
    # traditional form of the character (made up: 爲 where engine B read 為).
    assert [pp.converted_against_b(*spot) for spot in (("塵", "尘", "廑"), ("斂", "敛", "欽"), ("鳳", "凤", "风"),
                                                         ("懸", "恳", "悬"), ("諭協辦", "瑜伽", "谕协办"),
                                                         ("髮", "发", "發"), ("龍", "龙", ""), ("蕆", "藏", "葳"),
                                                         ("蕆", "藏", ""), ("相連變化", "水为先地下", ""),
                                                         ("爲", "馬", "為"))] == \
        ["尘", "敛", "凤", "", "", "", "", "蒇", "蒇", "连变", "为"]
    print("script: OpenCC's table read whole; a book's script told by its drafts; an answer in the form read")


def check_assertions_stay_on_user_layer() -> None:
    # A person's ruling on a form (contains 會) is not met by an OpenCC allograph.
    assert rr.user_fold("會") not in rr.user_fold("㑹議")
    assert rr.opencc_only("會", "㑹議"), "the failure should say OpenCC would accept it"
    assert rr.opencc_only("會", "開議") == ""
    print("assertions: an OpenCC-only match fails and says so")


if __name__ == "__main__":
    import tempfile
    check_vendored_copy()
    check_user_first()
    check_opencc_layer()
    check_lists_only()
    check_scoring_labels()
    check_assertions_stay_on_user_layer()
    check_script_table()
    with tempfile.TemporaryDirectory() as directory:
        check_user_overrides_opencc(Path(directory))
    print("all variant-layer checks passed")
