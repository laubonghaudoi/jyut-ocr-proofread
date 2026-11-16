#!/usr/bin/env python3
"""Offline checks of what phase 3 takes from the engines' own text beyond characters.

No model, no GPU, no corpus.  Covers engine B's grounding boxes (stripped before
the bracket rule and the table witness), the prompts' account of engine
punctuation, and the per-page mark count engine B's runner seals in place of the
old claim that it writes none.

Usage:
    python3 tests/check_engine_text.py
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import proofread_pages as pp  # noqa: E402
import run_hunyuanocr as hy  # noqa: E402
import stand_in  # noqa: E402

BODY = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽"


def check_strip_grounding() -> None:
    # The shapes measured: one box, or two corner points, alone on a line.
    assert pp.strip_grounding(f"(346,309),(772,738)\n{BODY}") == BODY
    assert pp.strip_grounding(f"(0,0)\n{BODY}") == BODY
    assert pp.strip_grounding(f"{BODY[:6]}\n(150,88),(838,450)\n{BODY[6:]}") == f"{BODY[:6]}\n{BODY[6:]}"
    assert pp.strip_grounding(f"{BODY}\n( 83, 210 ), ( 999, 928 )") == f"{BODY}\n"
    # Printed text with brackets inside a line is not a box.
    assert pp.strip_grounding(f"見第(1,2)條{BODY}") == f"見第(1,2)條{BODY}"
    assert pp.strip_grounding(f"{BODY}\n（一）{BODY}") == f"{BODY}\n（一）{BODY}"
    # Nothing left of a box to count as a bracket.
    _, gaps = pp.engine_gaps(pp.strip_grounding(f"(346,309),(772,738)\n{BODY}"))
    assert not any(pp.bracket_kinds(gap) for gap in gaps), gaps
    print("strip_grounding: a line of boxes goes, brackets in printed text stay")


def check_box_never_written_as_brackets() -> None:
    """A page whose engine B draft opens with a grounding box: before the boxes
    were stripped, the bracket rule wrote their parentheses into the page."""
    blocks = [stand_in.block(BODY, [0.3, 0.1, 0.4, 0.8])]
    with stand_in.book({1: (blocks, f"(346,309),(772,738)\n{BODY}")}) as root:
        model = stand_in.StandIn(census=0)
        record, markdown = stand_in.run(root, model)[1]
    assert record["mode"] == "merge", record["mode"]
    assert not any(c in markdown for c in "（）()"), markdown
    assert record["engineBrackets"] == {"inserted": 0, "moved": 0}, record["engineBrackets"]
    assert pp.CJK(markdown) == BODY, markdown
    print("page run: engine B's grounding box is not written as brackets")


def check_prompts() -> None:
    # The claim that engines never write punctuation, measured false on the
    # corpus (see common-ocr-traps.md), is not told to the model any more.
    for name in ("WRITER_SYSTEM", "AUDITOR_SYSTEM", "FORMAT_SYSTEM"):
        prompt = getattr(pp, name)
        for claim in ("結構性咁", "唔輸出句讀", "完全唔輸出"):
            assert claim not in prompt, (name, claim)
    assert "底稿冇標點**唔係**原書冇標點嘅證據" in pp.WRITER_SYSTEM
    print("prompts: no claim that OCR engines never write punctuation")


def check_hunyuan_record() -> None:
    assert "emitsSentencePunctuation" not in hy.OCR_PROFILE
    page = hy.PageInput(1, Path("page-0001.png"), "0" * 64, Path("page-0001.json"), Path("page-0001.txt"))
    tool = hy.ToolIdentity(Path("run_hunyuanocr.py"), "m", "p", None, "c")
    lines = hy.lines_from_text("天地玄黃。宇宙洪荒，日月盈昃、\n丶辰宿列張")
    raw = {"choices": [{"message": {"content": hy.text_from_lines(lines).decode("utf-8")}, "finish_reason": "stop"}]}
    record = hy.build_record(page, lines, hy.text_from_lines(lines), tool, hy.OCR_PROFILE, raw)
    assert "emitsSentencePunctuation" not in record, record
    # Marks as written: a look-alike character (丶) is a character, not counted.
    assert record["sentenceMarks"] == 3, record["sentenceMarks"]
    print("engine B runner: seals the marks it wrote on the page, no claim about the engine")


if __name__ == "__main__":
    check_strip_grounding()
    check_box_never_written_as_brackets()
    check_prompts()
    check_hunyuan_record()
    print("all engine-text checks passed")
