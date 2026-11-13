#!/usr/bin/env python3
"""Proofread one or two OCR drafts against complete page images.

The default packet transport writes resumable question bundles and exits 10
until the invoking agent has inspected the images and supplied answers. The
same agent can answer sequentially; parallel workers are optional. An external
HTTP model is used only with explicit --backend http and --endpoint options.

A single draft provides reading hints for direct image transcription. A second
OCR draft enables alignment and targeted disagreement checks; agreement between
engines never proves that either engine captured all printed content.

Every page first receives a whole-image content inventory, including apparently
blank pages. Content pages then receive image-based recovery, guarded formatting,
and a final coverage check. Numbers, formulas, note markers, table structure,
and uncertain readings remain part of those checks rather than being discarded
as formatting noise. A second pass by the same agent is self-review, not an
independent audit; an independent audit requires a distinct reviewer.

Output is a sealed per-page JSON/Markdown candidate with source, decision, and
transport provenance for validated resume. A completed candidate does not by
itself establish independent visual approval or a measured accuracy rate.
"""

from __future__ import annotations

import argparse
import base64
import bisect
from collections import Counter
import concurrent.futures as cf
import contextlib
import contextvars
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import fcntl
import functools
import hashlib
import inspect
import io
import itertools
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable, Collection, Iterable, Iterator, Mapping, Sequence
import unicodedata
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _tables  # noqa: E402
import _loop_watch  # noqa: E402
import _content_fidelity  # noqa: E402
from _packet_client import (  # noqa: E402
    EXIT_PACKETS_PENDING,
    PacketClient,
    PacketPending,
    packet_page_context,
)

SCHEMA_VERSION = 1
TEXT_AUTHORITY = "proofread-candidate-not-final-authority"
DEFAULT_ENDPOINT = "http://127.0.0.1:8090/v1/chat/completions"
DEFAULT_MODEL = "agent-packet"
DEFAULT_EFFORT = "xhigh"
DEFAULT_WORKERS = 8
DEFAULT_PAGE_CONCURRENCY = 4
_CALL_LEDGER: contextvars.ContextVar[list | None] = contextvars.ContextVar("call_ledger", default=None)
_LOOP_CUTS: contextvars.ContextVar[list | None] = contextvars.ContextVar("loop_cuts", default=None)
_CALL_SITE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("call_site", default=None)


@contextlib.contextmanager
def call_site(site: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The model calls made inside are about SITE, a place on the page (_CALL_SITE)."""
    token = _CALL_SITE.set(site)
    try:
        yield site
    finally:
        _CALL_SITE.reset(token)


def in_context(pool: cf.Executor, fn: Callable[..., Any], items: Sequence[Any]) -> list[Any]:
    """pool.map that carries the caller's context into each worker, in order.
    When running under PacketClient, all items in the batch run to register
    their pending packets before PacketPending is raised."""
    futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
    pending_err: PacketPending | None = None
    out: list[Any] = []
    try:
        for future in futures:
            try:
                out.append(future.result())
            except PacketPending as err:
                if pending_err is None:
                    pending_err = err
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    if pending_err is not None:
        raise pending_err
    return out


def summarize_calls(calls: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per kind of call: how many, how long, how many tokens, how much of
    the prompt the server served from its prefix cache, and how many images
    were padded to the server's aspect ratio (fit_media_aspect).
    With the loop detector on (--loop-detector), every kind has loopCut, how
    many of its calls it gave up in a word-for-word loop, and
    loopCutTokensSaved, what those left unspent of their max_tokens."""
    out: dict[str, dict[str, Any]] = {}
    for call in calls:
        row = out.setdefault(call.get("kind") or "other", {
            "n": 0, "seconds": 0.0, "waitSeconds": 0.0, "serverPromptMs": 0.0,
            "serverDecodeMs": 0.0, "promptTokens": 0, "cachedTokens": 0,
            "completionTokens": 0, "reasoningTokens": 0, "maxCompletion": 0,
            "truncated": 0, "padded": 0})
        row["n"] += 1
        row["seconds"] = round(row["seconds"] + call.get("seconds", 0.0), 2)
        row["waitSeconds"] = round(row["waitSeconds"] + call.get("waitSeconds", 0.0), 2)
        row["serverPromptMs"] = round(row["serverPromptMs"] + call.get("serverPromptMs", 0.0), 1)
        row["serverDecodeMs"] = round(row["serverDecodeMs"] + call.get("serverDecodeMs", 0.0), 1)
        for key, field in (("promptTokens", "prompt_tokens"), ("cachedTokens", "cached_tokens"),
                           ("completionTokens", "completion_tokens"),
                           ("reasoningTokens", "reasoning_tokens")):
            row[key] += call.get(field, 0) or 0
        row["maxCompletion"] = max(row["maxCompletion"], call.get("completion_tokens", 0) or 0)
        row["truncated"] += call.get("finish_reason") == "length"
        row["padded"] += bool(call.get("padded"))
        if call.get("loopWatched"):
            row["loopCut"] = row.get("loopCut", 0) + bool(call.get("loopCut"))
            row["loopCutTokensSaved"] = row.get("loopCutTokensSaved", 0) + (call.get("loopCutTokensSaved") or 0)
    return out


PUNCT ="。，、；：？！　 （）()「」《》·．,"

WRITER_SYSTEM = """你負責本頁轉錄。將歷史中文書頁忠實轉錄成 Markdown。

原文依據（最高優先）：
- 以頁圖為最終依據。OCR 底稿只係提示，唔係權威。
- 保留原有異體字、舊字形、舊式標點，例如 粤/粵、淸/清、决/決、吿/告、塲/場。唔好靜靜正規化。
  **但亦唔好過度古化。** 唔可以用「邊個字形舊啲／罕見啲」做判準——頁圖有咩就寫咩。
  一個罕見字形睇落似「忠實」，但如果頁上本來就係通行字形，寫罕見嗰個一樣係錯。
  異體字對（例如 即/卽、弒/弑、刊/刋、款/欵）**兩個方向都會錯**，唔可以靠通則決定。
- 原書有標點就照抄；原書冇標點就唔好加。唔好現代化、改寫或潤飾。
- 原書用 □ 表示破損就保留 □；唔好靠上下文猜補。

**標點必須逐個由頁圖讀出，唔可以由底稿推斷。**
OCR 引擎寫唔寫句讀，要睇本書點印：同一個引擎，有啲書頁上嘅符號差唔多全部寫出嚟，有啲書成本得十幾個；
寫出嚟嘅形狀亦唔一定同頁上一樣，有時仲會將符號寫成似樣嘅字。所以底稿冇標點**唔係**原書冇標點嘅證據，
底稿有標點亦唔係頁上印咗嘅證明。
反過來如果原書真係冇標點，你亦唔好加。兩邊都要靠頁圖判斷，唔可以靠底稿。

句讀符號嘅位置隨版式而變：直排時喺字嘅右側或右下，橫排時喺字嘅右下或正下。
兩種情況都好細粒，容易當咗污點或者印刷瑕疵。有幾多個就寫幾多個。

漏標點會直接令你**認錯字**，唔淨係少咗標點。原理：句號夾喺兩個字中間嗰陣，
如果你見唔到個句號，就會將前後兩字當成一個詞，而揀一個「讀得通」嘅詞。
所以**見到一個順口嘅雙音詞跨過可能嘅句界，要即刻回圖再睇**。

常見高信心錯字（要按語境同圖像查，唔係固定替換表）：
- 令/合、另/男、札/礼、道/逍/遣/遺、已/己/巳、戌/戍
- 人名、地名、官職先查頁圖，唔好靠現代記憶直接改。

版面：**先由頁圖判斷版式**——直排定橫排、右至左定左至右、單欄定多欄、有冇欄外書眉
同頁碼。唔好假設任何一種：同一套書、甚至同一本書入面都可能唔一致（正文直排、
表格或西文夾註橫排）。判斷咗之後照住重建閱讀次序、heading、list、段落。原書編號照抄。
表格：冇合併格寫 Markdown 表；有合併格（一格跨幾行或者幾欄）寫 HTML `<table>`，合併格用
rowspan／colspan 表示——Markdown 表表達唔到合併格，唔好拆格、補空格或者將同一段字抄入幾格。

輸出：只輸出該頁 Markdown 正文，唔好加解釋、唔好加 code fence。"""

AUDITOR_SYSTEM = """你負責唯讀覆核候選轉錄同頁圖。同一轉錄者重讀只算自我覆核，唔構成獨立審核。

只報告實際差異。每項寫：位置、候選讀法、你由圖像睇到嘅正確讀法、理據（部件／語境）。
特別檢查：閱讀次序、編號（一二三四）、近形字、人名官職、標點有無。

**標點要獨立數過。** OCR 引擎寫唔寫句讀、寫成咩形狀，要睇本書點印，所以「候選冇標點」呢件事
本身完全唔係證據。你要喺頁圖上逐句數清楚有幾多個句讀符號（位置隨版式而變，細粒），
再同候選比。候選漏標點同亂加標點一樣係差異，一定要報。

如果候選完全正確，第一個字答 "CLEAN"。唔好客氣式確認，要具體。
答 CLEAN 之前先問自己：我係咪真係喺圖上逐句數過標點？"""

ADJUDICATE_SYSTEM = """你係古籍字形鑑定專家。下面係一個放大嘅書頁區域。

**次序好重要：先描述，後判定。唔好由候選倒推。**

1. 先淨係描述你喺圖上見到嘅構件：左旁係咩、右邊係咩、幾多筆、有冇某個部件。
   呢一步唔准提任何候選字，唔准講「似 A」或者「似 B」。
2. 然後先至由構件推出係邊個字。
3. 最後同候選對照。

候選只係提示，唔係選擇題嘅全部選項。**正確答案好可能兩個候選都唔係**——
舊書大量用異體字，而 OCR 同轉錄兩邊都會靜靜正規化成通行字形。
實例：圖上係一個部件異體（同一偏旁，底部差一筆），但一邊讀成右旁完全唔同嘅另一個字、另一邊正規化成同義正字，兩個都錯。
所以見到構件同兩個候選都對唔上嗰陣，要寫出你真正見到嘅字，唔好夾硬揀一個。

答案格式：先寫 `構件：…`，再寫 `判定：<字>`，再講點解。
如果判定唔屬於任何一個候選，明確寫 `判定：<字>（兩個候選皆非）`。
如果爭議係「有冇字」（一方冇字），而你喺圖上嗰個位置真係冇字——例如另一方將括號、格線或者筆畫讀成咗「一」——就寫 `判定：無`。
如果嗰個位置係括住嘅編號（例如「（二）」），定案寫括號入面嘅數字，唔好寫上下文嘅字。

有啲爭議係**單靠字形永遠解決唔到**嘅——特別係人名、地名、官職、年號、書名。
呢類情況下，幾個候選可能都符合圖上嘅筆畫，要靠歷史脈絡同外部資料先分得出。
你冇上網能力，所以唔好靠記憶亂猜一個名，亦唔好因為某個名眼熟就揀佢。

輸出最後必須有獨立一行，格式一模一樣：
RESEARCH: yes
或
RESEARCH: no

`yes` = 呢個爭議涉及專名，而且淨靠字形分唔出，需要查外部資料先能定案。
`no`  = 純字形問題，你已經用部件證據定咗案。"""

RESEARCH_LINE = re.compile(r"^\s*RESEARCH:\s*(yes|no)\s*$", re.I | re.M)

# A blind reading of a crop (confirm_nothing's coverage read): the crop alone,
# no candidates, no context.
BLIND_SYSTEM = """你係古籍抄寫員。畀你一張書頁裁圖（通常係直排嘅一欄或者幾欄，由上至下、由右至左讀）。

照印抄出圖上每一個字：
- 由上至下，一欄抄完先抄左邊下一欄，一字唔漏、唔加。
- 印乜字形就寫乜字形：唔好改做通行字、簡體字，亦唔好改做你覺得語意通順嘅字。
- 圈、點、括號等標點符號唔使抄；格線、污點唔係字。
- 睇唔清嘅字，寫最似圖上筆畫嘅字。

淨係輸出抄出嚟嘅字，一行，唔好解釋。"""
BLIND_USER = "抄出呢張裁圖入面嘅所有字。"

# Answers that mean "there is no character here" - an engine read a bracket,
# a rule or a stroke as 一.  Parsed as a deliberate empty, never as a fallback.
# 裁決 writes 无 in a book printed in simplified script (a multi-volume archival compilation 3 scan 3).
EMPTY_VERDICTS = {"無", "冇", "冇字", "無字", "空", "不存在", "唔存在", "無字符", "无", "无字", "无字符"}
NUMERAL_CHARS = frozenset("〇零一二三四五六七八九十百千萬")
NUMERAL_CONFLICT_LIMIT = 4


RARE_CODEPOINT = 0x20000  # CJK Extension B and beyond


FRONT_MATTER_LABELS = frozenset({"doc_title", "figure_title", "seal"})
FRONT_MATTER_LIMIT = 80


def front_matter_research(blocks: Sequence[Mapping[str, Any]], resolved: str) -> list[dict[str, Any]]:
    """Flag a title page whose text cannot be settled from the strokes.

    A cover or title leaf carries a book title, an inscriber's name and often a
    seal, typically in calligraphy.  Two engines and two vision passes read one
    such inscription four different ways, all wrong; the right name came from
    the book's publication history, not from the image.  So a short page with
    title-class layout blocks is queued for external research rather than
    trusted.  The merge still runs - this only adds the flag.
    """
    labels = {str(b.get("label") or "") for b in blocks}
    if not (labels & FRONT_MATTER_LABELS) or len(CJK(resolved)) > FRONT_MATTER_LIMIT:
        return []
    return [{
        "kind": "front-matter",
        "text": resolved,
        "why": ("題名頁／題字頁：書名、題字者、印章等專名淨靠字形核唔到，"
                "要查本書出版脈絡（題字者係邊個、出版年、出版社）先可以定案"),
    }]


def is_rare_codepoint_conflict(seg: dict[str, Any]) -> bool:
    """A short disagreement where one side uses a codepoint beyond the BMP.

    HunyuanOCR keeps glyph-level variants and emits Extension B-F codepoints
    where PaddleOCR-VL normalises; measured on one page it was right once
    (𭔿 for a 尊 variant) and wrong once (𠯲 for 明).  Preference cannot tell
    the two apart; the crop can.
    """
    a, b = seg["a"], seg["b"]
    if not (a and b) or len(a) > NUMERAL_CONFLICT_LIMIT or len(b) > NUMERAL_CONFLICT_LIMIT:
        return False
    return any(ord(c) >= RARE_CODEPOINT for c in a + b)


def is_numeral_conflict(seg: dict[str, Any]) -> bool:
    """A disagreement made entirely of numerals, on either or both sides.

    Numerals are where engines hallucinate strokes (vertical parentheses read
    as 一, a rule read as 一) and where list numbering lives, and neither
    engine preference settles 一 against 二.  Measured on a three-page smoke
    run: five such segments on one page, every one resolved wrongly by
    prefer-engine B (（一）（二）（三）（四） became 一一一／一一一／一一三／八四一).
    These go to the crop regardless of the engine preference.
    """
    a, b = seg["a"], seg["b"]
    if not (a or b) or len(a) > NUMERAL_CONFLICT_LIMIT or len(b) > NUMERAL_CONFLICT_LIMIT:
        return False
    return all(c in NUMERAL_CHARS for c in a) and all(c in NUMERAL_CHARS for c in b)


CIRCLE_CHARS = frozenset("〇○")


def is_lone_circle(seg: Mapping[str, Any], block: Mapping[str, Any] | None) -> bool:
    """A single printed circle that one engine read as the numeral 〇 and the
    other not at all, with no numeral beside it, outside a table: taken as a
    mark, not a character.

    Books punctuated with circles (and emphasis circles beside every character)
    give engine A a 〇 where engine B reads nothing.  Sent to the crop as numeral
    disagreements, they cost a call each and the answers went wrong: measured on
    one page, 23 such calls (22 of them a single 〇), 12 answered 無 and 11 put
    〇, 〇〇, 一 or 二 into the text where the golden has a mark - 14 of the 31
    reading errors across five pages.  A numeral 〇 stands next to another
    numeral (一九〇五, 五〇) or in a table cell, and both keep going to the crop;
    so does anything whose block cannot be found, since a table cannot then be
    ruled out.  So do two circles: 〇〇 is also how documents print a withheld
    name (張〇〇), and a placeholder dropped here would be lost without a trace
    in the text.  A single 〇 can be a placeholder too (第〇號); none occurs in
    the goldens, and every drop is kept in engineDisagreements
    (circle-not-numeral) and counted in the run report.  The circle itself is
    left to the punctuation step, which reads marks off the crop.
    """
    a, b = seg["a"], seg["b"]
    lone = a or b
    if (a and b) or len(lone) != 1 or lone not in CIRCLE_CHARS:
        return False
    if block is None or block.get("label") == "table":
        return False
    near = (seg.get("before") or "")[-1:] + (seg.get("after") or "")[:1]
    return not any(c in NUMERAL_CHARS or c in CIRCLE_CHARS or c.isdigit() for c in near)


# Round brackets as the engines write them: full width, ASCII and the vertical
# presentation forms.  Written back into the text as （ and ）.
OPEN_BRACKETS = frozenset("（(︵")
CLOSE_BRACKETS = frozenset("）)︶")
# The brackets of another shape than round, each as the round one facing its
# way it stands for (place_brackets).
OTHER_BRACKETS = {**{c: "（" for c in "〔［[【"}, **{c: "）" for c in "〕］]】"}}
# brackets_by_block's key for the round brackets of a gap that one engine
# read beside a bracket of another shape facing the same way: place_brackets
# writes those even beside the punctuator's.
BRACKET_BESIDE = {"（": "〔", "）": "〕"}
# How far (in character gaps) a bracket the punctuator wrote may sit from one an
# engine read and still be taken for the same bracket, misplaced.
BRACKET_SLIP = 2
# What an engine writes for a vertical bracket it misreads as a stroke: measured,
# ︵ as 一 or 八 and ︶ as 一.  A 三 beside a bracket is a numeral, not the bracket.
BRACKET_MISREADS = frozenset("一八")

# Engine B's grounding output: the box of a picture or a region it did not
# transcribe, as (x,y),(x,y) in its 0-999 page coordinates.  Measured on the
# corpus: 21 pages of three books carry one, every one on a line of its own and
# none in engine A's drafts.  Its ASCII parentheses are brackets to engine_gaps,
# so a box at the head of a page counted as two bracket pairs engine B read there,
# which the bracket rule wrote into the text; and its digits stood as engine B's
# reading of digits in a table cell (engine_table).  Only a line holding
# nothing but boxes is taken for one: printed text such as (1,2) inside a line
# is left alone.
GROUNDING_LINE = re.compile(
    r"^[ \t]*\(\s*\d+\s*,\s*\d+\s*\)(?:\s*,\s*\(\s*\d+\s*,\s*\d+\s*\))*[ \t]*(?:\n|\Z)", re.M)


def strip_grounding(text: str) -> str:
    """Engine B's text without its grounding boxes (GROUNDING_LINE)."""
    return GROUNDING_LINE.sub("", text)


def engine_gaps(text: str) -> tuple[str, list[str]]:
    """An engine's characters, as CJK() counts them, and the raw text in each
    gap between them: gaps[k] is what it wrote before character k, and
    gaps[len(characters)] what it wrote after the last one."""
    chars: list[str] = []
    gaps = [""]
    for c in text:
        kept = CJK(c)
        if kept:
            chars.append(kept)
            gaps.append("")
        else:
            gaps[-1] += c
    return "".join(chars), gaps


def bracket_kinds(gap: str) -> list[str]:
    """The round brackets in a gap, in order, each as （ or ）."""
    return ["（" if c in OPEN_BRACKETS else "）" for c in gap
            if c in OPEN_BRACKETS or c in CLOSE_BRACKETS]


# A pair of round brackets an engine opened and closed between two of its
# characters: around a figure, Latin or nothing, which the merge (characters
# only) does not write.  The bracket rule wrote such a pair back as 「）（」 -
# the opening bracket goes with the character after it, the closing one with
# the character before - and, where engine B's reading was a character off, a
# pair inside a name (「瑚）（图里」): measured on the sealed contents pages of
# a book listing names, each with its page number in brackets, 20 such pairs
# on one page of 18 names.  With --keep-arabic-numerals only, which writes the
# figures it keeps and reports those it does not (engine_brackets_merged).
GAP_PAIR = re.compile(r"[（(︵][^（(︵）)︶]*[）)︶]")


def unpaired_brackets(gap: str) -> list[str]:
    """The round brackets of an engine's gap (bracket_kinds) less each pair
    opened and closed in it (GAP_PAIR)."""
    return bracket_kinds(GAP_PAIR.sub("", gap))


def explain_by_brackets(mine: str, gaps: Sequence[str], other: str, others_own: int) -> str | None:
    """OTHER with the numerals that this engine's brackets account for taken
    out, or None when no bracket accounts for any of them.

    MINE is this engine's reading of a numeral disagreement and GAPS the raw
    text it wrote in the len(MINE) + 1 gaps around and between those
    characters.  An engine that printed a bracket where the other engine has a
    numeral saw the bracket the other misread: engine B reads a vertical ︵ as
    八 or 一 and ︶ as 一, so engine A's （七） comes back 八七一.  Each bracket
    accounts for at most one of the other's numerals, and only brackets the
    other engine did not print itself count (OTHERS_OWN of them it did).  The
    other's remaining characters are returned in order, one for each of MINE:
    equal to MINE when the brackets explain the whole disagreement, and a
    shorter question (三 against 二 instead of 三 against 一一二一) when they do
    not.  Only a stroke shaped like a bracket (BRACKET_MISREADS) is taken for
    one.  Measured on one page: 裁決 answered 二, 一, 一 and 五 at four
    brackets and changed a bracketed 三 to 二, where engine A had printed every
    bracket.  When the brackets explain everything the engines read the same
    characters, and two-engine agreement is kept, as it is everywhere else -
    even the one time it was wrong in the goldens (both engines read the list
    number （二） as 一; 裁決 recovered the 二 in two of five runs).
    """
    tokens: list[tuple[str, str]] = [("X", k) for k in bracket_kinds(gaps[0])]
    for index, char in enumerate(mine):
        tokens.append(("C", char))
        tokens.extend(("X", k) for k in bracket_kinds(gaps[index + 1]))
    cap = sum(1 for kind, _ in tokens if kind == "X") - others_own
    if cap <= 0:
        return None

    @functools.lru_cache(maxsize=None)
    def best(i: int, j: int, used: int) -> tuple[int, int, str] | None:
        # (substitutions, -absorbed, the other's characters under mine)
        if i == len(tokens):
            return (0, 0, "") if j == len(other) else None
        kind, value = tokens[i]
        options = []
        if kind == "X":
            skip = best(i + 1, j, used)
            if skip:
                options.append(skip)
            if j < len(other) and used < cap and other[j] in BRACKET_MISREADS:
                taken = best(i + 1, j + 1, used + 1)
                if taken:
                    options.append((taken[0], taken[1] - 1, taken[2]))
        elif j < len(other):
            rest = best(i + 1, j + 1, used)
            if rest:
                options.append((rest[0] + (other[j] != value), rest[1], other[j] + rest[2]))
        return min(options) if options else None

    found = best(0, 0, 0)
    if found is None or found[1] == 0:
        return None
    return found[2]


def bracket_explanation(seg: Mapping[str, Any], a_start: int, b_start: int,
                        stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                        ) -> tuple[str, Any] | None:
    """How the engines' own brackets settle a numeral disagreement.

    ("A", reading) or ("B", reading) when one engine's brackets account for
    every numeral the other has in excess, so both read the same characters
    and the brackets were misread as strokes; ("reduced", segment) when they
    account for some, leaving a shorter question for 裁決; None otherwise.
    The streams are engine_gaps() of each engine's page text; a segment whose
    characters are not found at its start in either stream is left alone.
    """
    a, b = seg["a"], seg["b"]
    (chars_a, gaps_a), (chars_b, gaps_b) = stream_a, stream_b
    if chars_a[a_start:a_start + len(a)] != a or chars_b[b_start:b_start + len(b)] != b:
        return None
    around_a = gaps_a[a_start:a_start + len(a) + 1]
    around_b = gaps_b[b_start:b_start + len(b) + 1]
    own_a = sum(len(bracket_kinds(g)) for g in around_a)
    own_b = sum(len(bracket_kinds(g)) for g in around_b)
    core = explain_by_brackets(a, around_a, b, own_b)
    if core == a:
        return "A", a
    core_b = explain_by_brackets(b, around_b, a, own_a)
    if core_b == b:
        return "B", b
    if core is not None and core:
        return "reduced", dict(seg, b=core, bracketReduced={"engineA": a, "engineB": b})
    return None


def engine_brackets_merged(stream: tuple[str, list[str]], side: str,
                           segments: Sequence[Mapping[str, Any]],
                           pieces: Sequence[str],
                           figures_at: Collection[int] | None = None,
                           beside: bool = False) -> list[tuple[int, str]] | None:
    """The round brackets one engine read that the bracket rule writes back,
    as (gap in the merged text, bracket).

    With FIGURES_AT (--keep-arabic-numerals) not a pair it opened and closed
    between two characters (unpaired_brackets), around a figure or Latin the
    merge does not write, save at a gap of FIGURES_AT, where the merge writes
    a figure (brackets_by_block takes the figure's own brackets out); a
    figure or mark so left out that the page holds no copy of is a doubt
    (bracketed_pairs).  With FIGURES_AT None (the switch off, or not run on
    the page) every bracket, pairs too, as before: the merge keeps no figure
    and reports none, and the pair puts the punctuator's brackets back
    beside engine B's stand-in for the figure (「：丄）（土地」, which repair
    K1 writes 「：（1）土地」).  Measured on a book sealed with the switch off
    (canzungsiling, 2026-10-10): with the pair left out, on 4 pages whose two
    punctuation answers were refused the brackets stood where the fallback
    put them, split round the stand-in (「：）丄（土地」), and on 2 of them K1
    wrote 「：）：（1）土地」.

    SIDE is the engine's key in the segments ("a" or "b").  A bracket before
    one of the engine's characters goes before that character's place in the
    merged text; inside a disagreement resolved to other characters, before the
    character at the same offset, or after the resolution's last.  None when
    the segments do not spell out this engine's text.  Segments that carry
    their place in engine B's text (bStart: text engine B read in another order
    was placed at engine A's site, place_moved_once) are read by it.

    BESIDE: only the round brackets of a gap that also holds a bracket of
    another shape facing the same way (OTHER_BRACKETS: the engine read both,
    （〔清〕), which place_brackets writes even beside the punctuator's.
    """
    chars, gaps = stream

    def kinds(gap: str, at: int) -> list[str]:
        found = bracket_kinds(gap) if figures_at is None or at in figures_at else unpaired_brackets(gap)
        return [kind for kind in found if not beside or kind in {OTHER_BRACKETS.get(c) for c in gap}]
    if side == "b" and segments and all("bStart" in x for x in segments):
        where: list[int | None] = [None] * (len(chars) + 1)
        m = 0
        for seg, piece in zip(segments, pieces):
            text, e = seg["b"], seg["bStart"]
            if chars[e:e + len(text)] != text or any(where[e + k] is not None for k in range(len(text))):
                return None
            for k in range(len(text)):
                where[e + k] = m + (k if piece == text else min(k, len(piece)))
            if text and e + len(text) == len(chars):
                where[len(chars)] = m + len(piece)
            m += len(piece)
        if where[len(chars)] is None:
            where[len(chars)] = m
        if any(w is None for w in where):
            return None
        return [(where[g], kind) for g, gap in enumerate(gaps) for kind in kinds(gap, where[g])]
    if chars != "".join(x[side] for x in segments):
        return None
    where = [0] * (len(chars) + 1)
    e = m = 0
    for seg, piece in zip(segments, pieces):
        text = seg[side]
        for k in range(len(text)):
            where[e + k] = m + (k if piece == text else min(k, len(piece)))
        e += len(text)
        m += len(piece)
    where[len(chars)] = m
    return [(where[g], kind) for g, gap in enumerate(gaps) for kind in kinds(gap, where[g])]


# Printed marks as an engine writes them, for the mark rule (mark_explanation):
# the full-width sentence marks, quotes and brackets, their vertical
# presentation forms, the item bullets and the leader dots.  ASCII is left out:
# engine B writes its grounding boxes as (x,y) pairs, and markup is not a mark.
PRINTED_MARKS = frozenset("。，、；：？！「」『』《》〈〉（）"
                          "︐︑︒︓︔︕︖︙︰︵︶﹁﹂﹃﹄▲△●◎◆■…⋯")
# The mark rule's search over one disagreement: tokens x characters x marks
# spent.  A bound on work, not on what counts as a mark: the disagreements it
# settles hold a few characters (measured: the longest a book's aliases
# accounted for was 3 characters against 2 marks), and a disagreement larger
# than this - two engines reading a stretch in different orders - is left to
# the ordinary path, unexplained.
MARK_SEARCH_LIMIT = 250_000


def engine_marks(text: str) -> tuple[str, list[str]]:
    """engine_gaps(), with only the printed marks (PRINTED_MARKS) kept in each gap."""
    chars, gaps = engine_gaps(text)
    return chars, ["".join(c for c in gap if c in PRINTED_MARKS) for gap in gaps]


def explain_by_marks(mine: str, gaps: Sequence[str], other: str, others_own: int,
                     aliases: Mapping[str, Mapping[str, int]] | frozenset | set,
                     ) -> tuple[str, list[tuple[str, str]]] | None:
    """OTHER's characters left once this engine's printed marks account for
    some of them, and the (character, mark) pairs accounted for; None when no
    mark accounts for any.

    explain_by_brackets for every printed mark.  MINE is this engine's reading
    of a disagreement and GAPS the marks it printed in the len(MINE) + 1 gaps
    around and between those characters (engine_marks); each of MINE is
    matched, in order, to one of OTHER's characters.  A mark accounts for at
    most one of OTHER's characters, and only marks OTHER did not print there
    itself count (OTHERS_OWN of them it did): an engine that printed the comma
    itself did not read it as 丶.  Only a character in ALIASES - the glyphs
    this book's drafts show the other engine writing for marks (mark_aliases)
    - is taken for one; with a mapping, its per-mark counts break ties between
    readings that account for as many characters (爹一一 against ；（: which
    glyph was which mark), toward the pairs the book showed more often.  A
    character no mark accounts for stays in the result, in order: a smaller
    question than the whole disagreement (査 against 查 instead of 査丶
    against 查).  The search minimises characters left over, then
    substitutions, then maximises the evidence.
    """
    tokens: list[tuple[str, str]] = [("X", m) for m in gaps[0]]
    for index, char in enumerate(mine):
        tokens.append(("C", char))
        tokens.extend(("X", m) for m in gaps[index + 1])
    cap = sum(1 for kind, _ in tokens if kind == "X") - others_own
    if cap <= 0 or not any(c in aliases for c in other):
        return None
    if len(tokens) * (len(other) + 1) * (cap + 1) > MARK_SEARCH_LIMIT:
        return None
    weighted = isinstance(aliases, Mapping)

    def weight(char: str, mark: str) -> int:
        return (aliases[char].get(mark, 0) if weighted else 0) if char in aliases else 0

    n_t, n_o = len(tokens), len(other)
    # best[(i, j, u)] = (key, choice) for tokens[i:], other[j:], u marks spent;
    # key = (left over, substitutions, -evidence, -absorbed), filled bottom-up.
    best: dict[tuple[int, int, int], tuple[tuple[int, int, int, int], str]] = {}
    for i in range(n_t, -1, -1):
        for j in range(n_o, -1, -1):
            for u in range(cap, -1, -1):
                if i == n_t and j == n_o:
                    best[(i, j, u)] = ((0, 0, 0, 0), "end")
                    continue
                options = []
                if j < n_o and (i, j + 1, u) in best:           # other[j] accounted for by nothing
                    k = best[(i, j + 1, u)][0]
                    options.append(((k[0] + 1, k[1], k[2], k[3]), "left"))
                if i < n_t:
                    kind, value = tokens[i]
                    if kind == "X":
                        if (i + 1, j, u) in best:                # the mark stands for nothing of other's
                            options.append((best[(i + 1, j, u)][0], "skip"))
                        if j < n_o and u < cap and other[j] in aliases and (i + 1, j + 1, u + 1) in best:
                            k = best[(i + 1, j + 1, u + 1)][0]
                            options.append(((k[0], k[1], k[2] - weight(other[j], value), k[3] - 1), "absorb"))
                    elif j < n_o and (i + 1, j + 1, u) in best:
                        k = best[(i + 1, j + 1, u)][0]
                        options.append(((k[0], k[1] + (other[j] != value), k[2], k[3]), "match"))
                if options:
                    best[(i, j, u)] = min(options)
    if (0, 0, 0) not in best:
        return None
    kept, pairs = [], []
    i = j = u = 0
    while (i, j) != (n_t, n_o):
        choice = best[(i, j, u)][1]
        if choice == "left":
            kept.append(other[j])
            j += 1
        elif choice == "skip":
            i += 1
        elif choice == "absorb":
            pairs.append((other[j], tokens[i][1]))
            i, j, u = i + 1, j + 1, u + 1
        else:
            kept.append(other[j])
            i, j = i + 1, j + 1
    return ("".join(kept), pairs) if pairs else None


def mark_explanation(seg: Mapping[str, Any], a_start: int, b_start: int,
                     stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                     aliases: Mapping[str, Any]) -> tuple[str, Any, list[tuple[str, str]], str] | None:
    """How one engine's printed marks settle the other's extra characters.

    ("A", reading, pairs, "B") when engine A's marks account for every
    character engine B has in excess - engine B read engine A's marks as
    characters (丶 for ，, 厶 for ▲) and both read the same characters
    otherwise; ("B", reading, pairs, "A") the other way round; ("reduced",
    segment, pairs, side) when they account for some, leaving a smaller
    question for the ordinary path; None otherwise.  The last item names the
    engine whose glyphs were taken for marks.  STREAMS are engine_marks() of
    each engine's page text, ALIASES the glyphs each engine is seen to write
    for marks on this book (mark_aliases: {"A": ..., "B": ...}); a segment
    whose characters are not found at its start in either stream is left
    alone.
    """
    a, b = seg["a"], seg["b"]
    (chars_a, gaps_a), (chars_b, gaps_b) = stream_a, stream_b
    if chars_a[a_start:a_start + len(a)] != a or chars_b[b_start:b_start + len(b)] != b:
        return None
    around_a = gaps_a[a_start:a_start + len(a) + 1]
    around_b = gaps_b[b_start:b_start + len(b) + 1]
    own_a, own_b = sum(map(len, around_a)), sum(map(len, around_b))
    for mine, around, other, own, side, key in ((a, around_a, b, own_b, "B", "b"),
                                                (b, around_b, a, own_a, "A", "a")):
        if len(other) <= len(mine):
            continue
        found = explain_by_marks(mine, around, other, own, aliases.get(side) or {})
        if not found:
            continue
        kept, pairs = found
        if kept == mine:
            return ("A" if side == "B" else "B"), mine, pairs, side
        return "reduced", dict(seg, **{key: kept}, markReduced={"engineA": a, "engineB": b,
                                                                "marks": [list(p) for p in pairs]}), pairs, side
    return None


def circle_withheld(seg: Mapping[str, Any], a_start: int, b_start: int, found: Sequence[Any],
                    stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]],
                    block: Mapping[str, Any] | None) -> str | None:
    """Why the mark rule may not take a circle for a mark, or None when it may.

    FOUND is mark_explanation's answer.  A 〇 it would take for one of the
    other engine's marks is held to is_lone_circle's context: beside a
    numeral or another circle it may be a zero (二〇, 一九〇五) or part of a
    withheld name (張〇〇), and in a table block (or one that cannot be found)
    a figure; those go on to the crop, as the circle rule sends them.
    Measured on every draft pair of six books: of the 3,383 disagreements the
    rule settles, 13 take a circle, and 7 of those have a numeral beside it -
    six a circle next to a figure in prose, one the last digit of a
    five-figure number - which the circle rule and the numeral protection
    had kept for 裁決; the other 6 stand alone.  The neighbours are the
    engine's own characters either side of the circle, in its stream.
    """
    pairs, side = found[2], found[3]
    if not any(char in CIRCLE_CHARS for char, _ in pairs):
        return None
    if block is None or block.get("label") == "table":
        return "a circle in a table block, or in no block found"
    chars, start, text = (stream_a[0], a_start, seg["a"]) if side == "A" else (stream_b[0], b_start, seg["b"])
    for j, char in enumerate(text):
        if char in CIRCLE_CHARS:
            near = chars[max(0, start + j - 1):start + j] + chars[start + j + 1:start + j + 2]
            if any(c in NUMERAL_CHARS or c in CIRCLE_CHARS or c.isdigit() for c in near):
                return "a circle beside a numeral or another circle"
    return None


# The longest one-engine insertion put to 裁決 under a preference (decision D3:
# "1-2 character insertions").  Measured after the mark rule, per page: 5.3 such
# insertions on the magazine (mostly pieces of a running head only engine B
# read, which the furniture test keeps out), 3.1 on one book, 1.7 and 0.7 on two
# more; a call each, about 57 s and 7k tokens on the bench.  Longer runs are
# text read in another order or a line one engine skipped (place_moved_once,
# the preference).
INSERTION_LIMIT = 2

# The most characters one engine read that a 裁決 answer - a third reading, or
# "nothing printed" - may leave out at one spot (adjudicate_segment).  A
# couple: a bracket or a rule read as 一, a mark read as a character.  Measured
# on the sealed benchmark runs (four runs of the current code): every answer
# that wrote three or more fewer characters than an engine read, not being a
# numeral question or the book's running head, lost printed text - a
# 26-character column seen in part (7 written), a table header's four
# characters answered with one of them (three were printed), and two table
# cells engine B read - three and six characters, printed - answered 無.
# It is also the most characters an answer may write beyond the longer of the
# two readings (adjudicate_segment): a guard against prose given for an
# answer.  Read from the last answer line (verdict_answer), six sealed books
# (4,967 verdicts, 2026-10-24): 16 answers are one or two characters longer
# (folio or volume figures an engine read in part, both candidates written,
# misreadings), and one is eight longer - the answer line itself prose ("the
# glyph cannot settle it").  The 6 and 12 longer of the first-match reading
# were #33 itself.
ANSWER_DELETION_LIMIT = 2


# The two gates on a glyph taken for a mark, from this book's own drafts
# (mark_aliases).  Measured over five books' drafts, excess occurrences of a
# glyph at a gap where the other engine printed a mark it did not, against its
# excess occurrences anywhere else: a mark reading concentrates at the marks
# (engine B's 丶 1,682:32 on a magazine printing full-cell commas, 口 274:13,
# 号 51:2; 厶 101:60 for ▲ on another book), a character engine A simply missed
# spreads over the page (1:107, 1:93, 1:244 for three that each met a mark gap
# once).  One site is a coincidence, so it takes two, and more explained than
# unexplained.  Nothing about which glyphs: a book that prints its marks
# plainly shows none (engine B's on one book: none at all).
MARK_ALIAS_MINIMUM = 2


def mark_evidence(pairs: Sequence[tuple[Path, Path]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per engine and glyph, how often its excess occurrences sit where the other
    engine printed a mark (explained, per mark) or not (unexplained), over
    every draft pair in the book, with no model call.

    Each disagreement where one engine read more characters is searched with
    no alias restriction (explain_by_marks over every glyph it holds): what the
    other engine's marks can account for counts as explained, the rest of its
    excess as unexplained.  A page where either engine looped is left out -
    one engine repeating a glyph thousands of times would outvote the book -
    and so is a disagreement too large to search (MARK_SEARCH_LIMIT), which
    says nothing either way.
    """
    explained = {"A": {}, "B": {}}
    unexplained = {"A": Counter(), "B": Counter()}
    for path_a, path_b in pairs:
        try:
            text_a = path_a.read_text(encoding="utf-8")
            text_b = strip_grounding(path_b.read_text(encoding="utf-8"))
        except OSError:
            continue
        if any(len(CJK(t)) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(CJK(t)) < DEGENERATE_NGRAM_RATIO
               for t in (text_a, text_b)):
            continue
        (_, gaps_a), (_, gaps_b) = engine_marks(text_a), engine_marks(text_b)
        a_at = b_at = 0
        for seg in align_drafts(text_a, text_b):
            a, b = seg["a"], seg["b"]
            if seg["tag"] != "equal" and len(a) != len(b):
                side, short, long_ = ("B", a, b) if len(b) > len(a) else ("A", b, a)
                s_gaps = (gaps_a[a_at:a_at + len(a) + 1] if side == "B" else gaps_b[b_at:b_at + len(b) + 1])
                l_gaps = (gaps_b[b_at:b_at + len(b) + 1] if side == "B" else gaps_a[a_at:a_at + len(a) + 1])
                tokens = len(short) + sum(map(len, s_gaps))
                if tokens * (len(long_) + 1) * (sum(map(len, s_gaps)) + 1) <= MARK_SEARCH_LIMIT:
                    found = explain_by_marks(short, s_gaps, long_, sum(map(len, l_gaps)), frozenset(long_))
                    taken = Counter(c for c, _ in found[1]) if found else Counter()
                    for char, mark in (found[1] if found else []):
                        explained[side].setdefault(char, Counter())[mark] += 1
                    unexplained[side].update(Counter(long_) - Counter(short) - taken)
            a_at += len(a)
            b_at += len(b)
    return {side: {char: {"explained": sum(explained[side].get(char, Counter()).values()),
                          "unexplained": unexplained[side][char],
                          "marks": dict(explained[side].get(char, Counter()))}
                   for char in set(explained[side]) | set(unexplained[side])}
            for side in ("A", "B")}


def mark_aliases(evidence: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, dict[str, int]]]:
    """The glyphs each engine is seen to write for the other's printed marks on
    this book (mark_evidence), each with its per-mark counts: explained at
    MARK_ALIAS_MINIMUM sites or more, and more often explained than not."""
    return {side: {char: dict(row["marks"]) for char, row in (evidence.get(side) or {}).items()
                   if row["explained"] >= MARK_ALIAS_MINIMUM and row["explained"] > row["unexplained"]}
            for side in ("A", "B")}


def place_brackets(text: str, span: str, wanted: Mapping[tuple[int, str], int]) -> tuple[str, int, int]:
    """TEXT, a punctuated block, with the round brackets the engines read.

    WANTED counts brackets by (gap, kind): gap k is before the block's
    character k.  A bracket the punctuator already wrote at that gap is kept;
    so is one of another shape facing the same way (OTHER_BRACKETS: 〔 for a
    wanted （), which stands for it.  Until 2026-10-26 the round one was
    written beside it; measured on the sealed pages of five simplified books
    (2026-10-25), at each such doubled bracket one engine read 〔 and the
    other （, and the punctuator's shape was the print's (repair's B2 takes
    those round brackets out of the pages sealed before).  Not for the round
    brackets WANTED keys (gap, 〔) or (gap, 〕) counts, which one engine read
    beside that shape (brackets_by_block): the print holds both there
    (（〔清〕林時益輯）, both engines reading （〔), so those are written
    beside the punctuator's 〔 as before.  One it wrote
    within BRACKET_SLIP gaps of a wanted bracket of the same kind that is
    not there is taken for that bracket misplaced and moved; any other
    bracket it wrote is kept.  Returns the text, how many brackets were
    written in and how many of the punctuator's were moved.  The characters
    are never touched; a text whose characters are not SPAN is returned as
    is.
    """
    raw_chars: list[str] = []
    gaps: list[list[str]] = [[]]
    for c in text:
        if CJK(c):
            raw_chars.append(c)
            gaps.append([])
        else:
            gaps[-1].append(c)
    if CJK("".join(raw_chars)) != CJK(span):
        return text, 0, 0
    need = Counter({key: n for key, n in wanted.items() if n > 0 and key[1] in "（）"})
    loose: list[tuple[int, int, str]] = []
    for g, gap in enumerate(gaps):
        for pos, c in enumerate(gap):
            if c in OPEN_BRACKETS or c in CLOSE_BRACKETS:
                kind = "（" if c in OPEN_BRACKETS else "）"
                if need[(g, kind)] > 0:
                    need[(g, kind)] -= 1
                else:
                    loose.append((g, pos, kind))
    for g, gap in enumerate(gaps):
        for c in gap:
            kind = OTHER_BRACKETS.get(c)
            if kind and need[(g, kind)] > wanted.get((g, BRACKET_BESIDE[kind]), 0):
                need[(g, kind)] -= 1
    claimed: Counter = Counter()
    drop: set[tuple[int, int]] = set()
    for g, pos, kind in loose:
        near = sorted((abs(g2 - g), g2) for (g2, k2), n in need.items()
                      if k2 == kind and n - claimed[(g2, k2)] > 0 and abs(g2 - g) <= BRACKET_SLIP)
        if near:
            claimed[(near[0][1], kind)] += 1
            drop.add((g, pos))
    out: list[str] = []
    for g, gap in enumerate(gaps):
        kept = [c for pos, c in enumerate(gap) if (g, pos) not in drop]
        first_open = next((i for i, c in enumerate(kept) if c in OPEN_BRACKETS), len(kept))
        kept = (kept[:first_open] + ["）"] * need[(g, "）")] + kept[first_open:]
                + ["（"] * need[(g, "（")])
        out.append("".join(kept))
        if g < len(raw_chars):
            out.append(raw_chars[g])
    return "".join(out), sum(need.values()), len(drop)


def brackets_by_block(stream_a: tuple[str, list[str]], stream_b: tuple[str, list[str]] | None,
                      segments: Sequence[Mapping[str, Any]], pieces: Sequence[str], spans: Sequence[str],
                      own: Mapping[tuple[int, str], int] | None = None) -> dict[int, Counter]:
    """The round brackets either engine read (engine_brackets_merged; both
    reading one bracket is one bracket), by block: block index -> Counter of
    (gap in the block's span, bracket), for place_brackets.  OWN
    (--keep-arabic-numerals) counts, by
    (gap in the merged text, bracket), the brackets of the figures the merge
    writes, which go with their figure (figure_in_gap) and are not written
    here: a figure starting a block would otherwise see its closing bracket
    written at the end of the block before, with the character before it.
    Nor is a pair an engine read around no character, elsewhere
    (unpaired_brackets).  OWN None (the switch off): every bracket either
    engine read is written (engine_brackets_merged).  Keyed (gap, 〔) and
    (gap, 〕): how many of the round brackets wanted at the gap one engine
    read beside a bracket of another shape facing the same way (BRACKET_BESIDE)."""
    ends = list(itertools.accumulate(len(span) for span in spans))
    total = ends[-1] if ends else 0
    wanted: Counter = Counter()
    beside: Counter = Counter()
    figures_at = None if own is None else {gap for gap, _ in own}
    for stream, side in ((stream_a, "a"), (stream_b, "b")):
        for key, n in Counter(engine_brackets_merged(stream, side, segments, pieces, figures_at) or []).items():
            wanted[key] = max(wanted[key], n)
        for (gap, kind), n in Counter(engine_brackets_merged(stream, side, segments, pieces, figures_at,
                                                             beside=True) or []).items():
            beside[(gap, BRACKET_BESIDE[kind])] = max(beside[(gap, BRACKET_BESIDE[kind])], n)
    if own:
        wanted -= Counter(own)
    per_block: dict[int, Counter] = {}
    for (gap, kind), n in (wanted + beside).items():
        if not total:
            break
        # An opening bracket belongs with the character after it, a closing
        # one with the character before it.
        anchor = min(max(gap if kind in "（〔" else gap - 1, 0), total - 1)
        k = bisect.bisect_right(ends, anchor)
        start = ends[k - 1] if k else 0
        per_block.setdefault(k, Counter())[(gap - start, kind)] += n
    return per_block


# Switch --keep-arabic-numerals (on by default; ROUND4_DEFAULTS).  The merge keeps characters
# only (CJK), so an Arabic figure engine A read - a list item's (1), a contents
# entry's 12, a document heading's 5. - stands in the page only where the
# punctuator or the structure pass writes it, and engine B's reading of it (a
# glyph such as 丄 or 工 for 1, 八…一 for its brackets) could stand instead.
# Measured on the sealed pages of a 488-page book printing Arabic item,
# contents and heading numbers: the survey of its print counted 95 contents
# numbers lost or misread, 51 bracketed item numbers written as engine B's
# glyphs with reversed brackets, 17 heading numbers misread and 9 written as a
# stray mark.  With the switch the merge carries engine A's figure where the
# figure is the only reading of that print (the merge's walk and
# arabic_figure_kept): engine B read nothing there, or marks, or glyphs this
# book's drafts show engine B writing for A's figures and marks, none a numeral
# (figure_stand_ins, figure_aliases, mark_aliases), or 裁決 read the figure (or
# no character) there (figure_verdict).  And only a figure that continues a numbering: the figure engine A
# read before it is one less, or the one after it one more (figures_confirmed)
# - measured on the same book, engine A read 1 of about 75 heading and item
# figures wrong (a 41 read 44), and that one breaks the numbering on both
# sides - or a contents entry's page number: a figure ending a line that holds a
# leader before it (arabic_figures' "leader").  A contents page's numbers rise by
# pages, not by one: measured on a contents page of 26 names, 8 of its
# numbers continued no numbering, and two pages whose engine A wrote its
# leaders as ASCII dots ("..... (141)") had no figure found at all - one left
# an empty 「（）」 after every entry, the other 「）（」.  No character both
# engines read is replaced.
ARABIC_FIGURE_DIGITS = 3
_MARK_CLASS = re.escape("".join(sorted(PRINTED_MARKS)))
# A gap of engine A's text holding one figure and nothing else but printed
# marks and white space: 1 to ARABIC_FIGURE_DIGITS digits, in brackets or with
# a trailing point as engine A read it.
ARABIC_FIGURE_GAP = re.compile(
    rf"^(?P<lead>[\s{_MARK_CLASS}]*?)(?P<figure>[（(︵]?[0-9]{{1,{ARABIC_FIGURE_DIGITS}}}[）)︶]?\.?)"
    rf"(?P<tail>[\s{_MARK_CLASS}]*)$")
FIGURE_BRACKETS = str.maketrans({"(": "（", "︵": "（", ")": "）", "︶": "）"})
# A leader as engine A writes it: a run of ellipses, or of two or more ASCII
# full stops ("....." on four contents pages of an archive volume and two of
# a modern book; engine A wrote no run of middle dots in twelve books).
LEADER_READ = re.compile(r"[…⋯]+|\.{2,}")


def arabic_figures(blocks: Sequence[Mapping[str, Any]], draft: str,
                   dotted: bool = False) -> list[dict[str, Any]]:
    """Engine A's Arabic figures that may number something (--keep-arabic-numerals),
    in reading order: each alone in a gap of its block's text (ARABIC_FIGURE_GAP)
    in a block of engine A's layout that is no furniture or table
    (FURNITURE_LABELS) - a folio, a library stamp's number or a table's figures
    are not a list's numbers.  Each gives its gap in engine A's stream of
    characters (as CJK counts them), its block and its gap there, the figure as
    written (brackets as full-width brackets) and its value, the marks engine
    A read after it, the brackets it read in the gap besides the figure's
    (a parenthesis closed right before it), and whether it is a contents
    entry's page number ("leader": it ends its line or block, and the line
    holds a leader before it, LEADER_READ; 「东瀛纪事……杨廷理（169）」 too).
    With DOTTED, a run of ASCII dots before the figure is a leader too
    (engine A's "..... (141)"); without it, as before the rule for leaders,
    for the book's numbering sealed in provenance (figure_evidence: so pages
    sealed before stay current).  Empty when the blocks do not spell engine
    A's text, where no gap of a block is a gap of the stream."""
    lengths = [len(CJK(str(block.get("text") or ""))) for block in blocks]
    if not blocks or sum(lengths) != len(CJK(draft)):
        return []
    found, offset = [], 0
    for k, block in enumerate(blocks):
        chars, gaps = engine_gaps(str(block.get("text") or ""))
        if block.get("label") not in FURNITURE_LABELS:
            # Whether the line read so far holds a leader.
            on_line = False
            for j, gap in enumerate(gaps):
                match = ARABIC_FIGURE_GAP.match(LEADER_READ.sub(LEADER, gap) if dotted else gap)
                if match:
                    figure = match.group("figure").translate(FIGURE_BRACKETS)
                    digits = re.sub(r"[^0-9]", "", figure)
                    found.append({"gap": offset + j, "block": k, "at": j, "figure": figure,
                                  "digits": digits, "value": int(digits),
                                  "after": "".join(c for c in match.group("tail") if c in PRINTED_MARKS),
                                  "otherBrackets": dict(Counter(bracket_kinds(match.group("lead")
                                                                              + match.group("tail")))),
                                  "leader": bool((on_line or LEADER_READ.search(match.group("lead")))
                                                 and ("\n" in match.group("tail") or j == len(gaps) - 1))})
                line = gap.rsplit("\n", 1)
                on_line = bool(LEADER_READ.search(line[-1])) or (on_line and len(line) == 1)
        offset += len(chars)
    return found


def figures_confirmed(values: Sequence[int], before: int | None, after: int | None) -> list[bool]:
    """Whether each of a page's figures (arabic_figures), in reading order,
    continues a numbering: the figure engine A read before it - on the page,
    or the last on the pages before (BEFORE) - is one less, or the one after it
    (on the page, or the first on the pages after: AFTER) one more.  Evidence,
    not a rule of the print: a figure no neighbour confirms is not kept (as
    without the switch), and one engine A misread breaks the numbering on
    both sides of it."""
    sequence = [before, *values, after]
    return [(sequence[i] is not None and sequence[i] == value - 1)
            or (sequence[i + 2] is not None and sequence[i + 2] == value + 1)
            for i, value in enumerate(values)]


def figure_neighbours(numbering: Mapping[int, Sequence[int]], page: int) -> tuple[int | None, int | None]:
    """The value of the last figure engine A read on the pages before PAGE, and
    of the first on the pages after (NUMBERING: page -> its figures' values)."""
    before = [numbering[p][-1] for p in sorted(numbering) if p < page and numbering[p]]
    after = [numbering[p][0] for p in sorted(numbering) if p > page and numbering[p]]
    return (before[-1] if before else None), (after[0] if after else None)


def figure_evidence(pairs: Sequence[tuple[int, Path, Path]],
                    aliases: Mapping[str, Any]) -> dict[str, Any]:
    """What --keep-arabic-numerals reads off the whole book's drafts, with no
    model call: each page's figures (arabic_figures; numbering: page -> their
    values, for figures_confirmed), and per glyph of engine B how often it is
    what engine B read in excess where engine A read a confirmed figure and
    nothing else (atFigure) - after the mark rule took out what engine A's
    marks there account for (mark_explanation with the book's ALIASES), at
    most as many glyphs as the figure has signs - or anywhere else (elsewhere),
    as mark_evidence counts a mark's glyphs.  PAIRS are (page, engine A's
    text, engine B's text); engine A's layout is beside its text.  A page
    where either engine looped is left out, as mark_evidence leaves it."""
    numbering: dict[int, list[int]] = {}
    figures: dict[int, list[dict[str, Any]]] = {}
    texts: dict[int, tuple[str, str]] = {}
    for page, path_a, path_b in pairs:
        try:
            text_a = path_a.read_text(encoding="utf-8").strip()
            text_b = strip_grounding(path_b.read_text(encoding="utf-8")) if path_b.is_file() else None
        except OSError:
            continue
        found = arabic_figures(load_blocks(path_a.with_suffix(".json")), text_a)
        figures[page] = found
        numbering[page] = [f["value"] for f in found]
        if text_b is not None:
            texts[page] = (text_a, text_b)
    at_figure, elsewhere = Counter(), Counter()
    for page, (text_a, text_b) in texts.items():
        if any(len(CJK(t)) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(CJK(t)) < DEGENERATE_NGRAM_RATIO
               for t in (text_a, text_b)):
            continue
        before, after = figure_neighbours(numbering, page)
        confirmed = figures_confirmed(numbering[page], before, after)
        sites = {f["gap"]: f for f, ok in zip(figures[page], confirmed) if ok}
        marks_a, marks_b = engine_marks(text_a), engine_marks(text_b)
        a_at = b_at = 0
        for seg in align_drafts(text_a, text_b):
            a, b = seg["a"], seg["b"]
            if seg["tag"] != "equal" and len(b) > len(a):
                reduced = seg
                found = mark_explanation(seg, a_at, b_at, marks_a, marks_b, aliases)
                if found and found[0] in ("A", "B"):
                    reduced = dict(seg, b=seg["a"]) if found[3] == "B" else seg
                elif found:
                    reduced = found[1]
                excess = Counter(reduced["b"]) - Counter(reduced["a"])
                figure = sites.get(a_at) if not a else None
                if figure is not None and len(reduced["b"]) <= len(figure["figure"]):
                    at_figure.update(excess)
                else:
                    elsewhere.update(excess)
            a_at += len(a)
            b_at += len(b)
    return {"numbering": numbering,
            "glyphs": {char: {"atFigure": at_figure[char], "elsewhere": elsewhere[char]}
                       for char in at_figure}}


def figure_aliases(evidence: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """The glyphs engine B is seen to write for engine A's figures on this book
    (figure_evidence): at MARK_ALIAS_MINIMUM confirmed figures or more, and
    more often there than anywhere else - the gates of mark_aliases."""
    return {char: dict(row) for char, row in (evidence.get("glyphs") or {}).items()
            if row["atFigure"] >= MARK_ALIAS_MINIMUM and row["atFigure"] > row["elsewhere"]}


def figure_stand_ins(read: str, figure: Mapping[str, Any], glyphs: Collection[str], brackets_read: int) -> bool:
    """Whether READ, what engine B read where engine A read FIGURE and nothing
    else (once the bracket and mark rules took out what engine A's marks there
    account for), is engine B's reading of the figure (--keep-arabic-numerals):
    the figure's brackets read as strokes (BRACKET_MISREADS, at either end;
    only as many as the figure has brackets that neither the mark rule took
    for them nor engine B printed there itself: BRACKETS_READ), and at most one
    glyph for each of its digits and its point, each one of GLYPHS - the
    glyphs this book's drafts show engine B writing for engine A's figures or
    marks (figure_aliases, mark_aliases) - and none a numeral.  A numeral may
    be the print's own figure in another script, which 裁決 reads off the
    crop (figure_verdict), as without the switch."""
    rest = list(read)
    free = figure["figure"].count("（") + figure["figure"].count("）") - brackets_read
    while free > 0 and rest and rest[0] in BRACKET_MISREADS:
        rest.pop(0)
        free -= 1
    while free > 0 and rest and rest[-1] in BRACKET_MISREADS:
        rest.pop()
        free -= 1
    return (len(rest) <= len(figure["digits"]) + figure["figure"].count(".")
            and all(c in glyphs and c not in NUMERAL_CHARS for c in rest))


def figure_in_gap(gap: str, figure: str, after: str = "", keep: Mapping[str, int] | None = None) -> str:
    """GAP, the text between two characters, with FIGURE written in it: the
    figure's own brackets, where the gap holds them (a punctuator wrote them
    with nothing between them: （）, or ）（), taken out and written around
    the figure; the marks AFTER - those engine A read right after the figure (a heading's 3， or 5.，) -
    that the gap ends with go after the figure, as read; of the rest, the
    marks before the gap's first opening mark go before the figure (a comma,
    the close of a sentence, a heading's markup), the others after it (an
    opening quote).  KEEP counts, by bracket (（ or ）), the brackets of other
    print the gap holds - the bracket rule's (brackets_by_block with the
    figures' own left out), or engine A's own reading of the gap beside the
    figure: only brackets beyond those are the figure's.  A closing bracket
    written just before the figure is the close of the text before it (a
    parenthesis closed right before a list's (2)), not the figure's."""
    marks = list(gap)
    for bracket, forms in (("（", OPEN_BRACKETS), ("）", CLOSE_BRACKETS)):
        spare = sum(1 for c in marks if c in forms) - (keep or {}).get(bracket, 0)
        if bracket in figure and spare > 0:
            del marks[max(i for i, c in enumerate(marks) if c in forms)]
    tail: list[str] = []
    for mark in reversed(after):
        if marks and marks[-1] == mark:
            tail.insert(0, marks.pop())
        else:
            break
    cut = next((i for i, c in enumerate(marks) if c in OPENING_MARKS), len(marks))
    return "".join(marks[:cut]) + figure + "".join(tail) + "".join(marks[cut:])


def figure_written(gap: str, digits: str) -> bool:
    """Whether GAP holds the figure DIGITS (not as part of a longer number)."""
    return re.search(rf"(?<![0-9]){digits}(?![0-9])", gap) is not None


def bracketed_pairs(blocks: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, str]]:
    """Each pair of brackets engine A read between two characters of a block
    that is no furniture or table (FURNITURE_LABELS), a pair of its gap
    (GAP_PAIR), around 1 to ARABIC_FIGURE_DIGITS digits and nothing else, or
    around no letter or digit (a mark - 「以『義氣』(？)相投」, once in twelve
    books - or nothing), beside note markers or markup or not (「」。⑲（2）」).  Not a
    year, a longer number or Latin text (「（1513-95）」, 「（G. W. Skinner）」),
    which the repair's runs stage writes back.  As (its gap in engine A's
    stream of characters, what the page holds of it - a figure's digits, or
    the pair in full-width brackets - the characters around it).  The bracket
    rule does not write such a pair alone (unpaired_brackets): one the page
    does not hold is a doubt (bracketed_unwritten)."""
    found, offset = [], 0
    for block in blocks:
        chars, gaps = engine_gaps(str(block.get("text") or ""))
        if block.get("label") not in FURNITURE_LABELS:
            for j, gap in enumerate(gaps):
                for pair in GAP_PAIR.findall(gap):
                    inside = pair[1:-1].strip()
                    if re.fullmatch(rf"[0-9]{{1,{ARABIC_FIGURE_DIGITS}}}", inside) or not re.search(r"[^\W_]", inside):
                        written = f"（{inside}）"
                        found.append((offset + j, written if not inside.isdigit() else inside,
                                      f"…{chars[max(0, j - 4):j]}[{written}]{chars[j:j + 4]}…"))
        offset += len(chars)
    return found


def bracketed_unwritten(bracketed: Sequence[tuple[int, str, str]], kept: Sequence[Mapping[str, Any]],
                        text: str, why: Mapping[int, str]) -> list[str]:
    """The pairs of BRACKETED (bracketed_pairs) that the page TEXT does not
    hold, each as the characters around it and WHY the merge did not keep its
    figure (by gap).  A figure the merge kept (KEPT: its gap and figure) is
    written; another pair is held where TEXT holds a copy of it - a figure's
    digits, the punctuator's or the structure pass's, or the pair as written -
    counted page-wide once the figures the merge kept have taken theirs."""
    held = Counter(re.findall(r"[0-9]+", text)
                   + [f"（{inside.strip()}）" for inside in re.findall(r"（([^（）\w]*)）", text)])
    for figure in kept:
        digits = re.sub(r"[^0-9]", "", figure["figure"])
        held[digits] -= held[digits] > 0
    kept_at = {figure["gap"] for figure in kept}
    lost = []
    for gap, key, context in bracketed:
        if gap in kept_at:
            continue
        if held[key] > 0:
            held[key] -= 1
        else:
            lost.append(context + (f" ({why[gap]})" if gap in why else ""))
    return lost


# How place_arabic_figures left a figure: standing at its gap (the text the
# structure pass is given holds it there), in its block (at its gap or the
# punctuator's copy near it), and written by the switch (the figure, or its
# brackets around the punctuator's copy).
FIGURE_AT_GAP = ("written", "already", "already-bracketed")
FIGURE_IN_BLOCK = FIGURE_AT_GAP + ("elsewhere", "elsewhere-bracketed")
FIGURE_WRITTEN = ("written", "already-bracketed", "elsewhere-bracketed")


def place_arabic_figures(text: str, span: str, figures: Sequence[tuple[Any, ...]]) -> tuple[str, list[str]]:
    """TEXT, a punctuated block whose characters are SPAN, with each (gap,
    figure[, the marks engine A read after it[, the brackets of other print
    there]]) of FIGURES written at its gap (figure_in_gap), and for each how:
    "written"; "already", where the gap holds its digits; "elsewhere", where
    the block holds a copy of its digits within NEARBY_WINDOW characters of the
    gap (the punctuator wrote the figure a gap or a line away, and it is not
    written twice); "unplaced" where TEXT's characters are not SPAN.  The
    bracket rule leaves a figure's brackets to it (brackets_by_block own), so
    a copy of the punctuator's that lacks the brackets engine A read around
    the figure gets them, next to its digits ("already-bracketed",
    "elsewhere-bracketed"): else the page has the figure's digits and not
    its brackets, where without the switch the bracket rule wrote them."""
    chars, gaps = engine_gaps(text)
    if chars != CJK(span):
        return text, ["unplaced"] * len(figures)
    how = [""] * len(figures)
    digits_of = [re.sub(r"[^0-9]", "", figure[1]) for figure in figures]
    copy_at: dict[int, int] = {}
    for k, (gap, *_) in enumerate(figures):
        if not 0 <= gap < len(gaps):
            how[k] = "unplaced"
        elif figure_written(gaps[gap], digits_of[k]):
            how[k] = "already"
            copy_at[k] = gap
    # Copies of a figure's digits the block holds within NEARBY_WINDOW
    # characters of its gap, at no gap that holds them as a figure written
    # there: the punctuator's for that figure, a gap or a line away (a
    # contents entry's number written after its title).  Each stands for one
    # figure to write.
    taken = {figures[k][0] for k in range(len(figures)) if how[k] == "already"}
    spare = [[g, digits] for g, gap in enumerate(gaps) if g not in taken
             for digits in re.findall(r"[0-9]+", gap)]
    for k in sorted(range(len(figures)), key=lambda k: figures[k][0]):
        if how[k]:
            continue
        near = next((copy for copy in spare if copy[1] == digits_of[k]
                     and abs(copy[0] - figures[k][0]) <= NEARBY_WINDOW), None)
        if near is not None:
            spare.remove(near)
            how[k] = "elsewhere"
            copy_at[k] = near[0]
        else:
            how[k] = "written"
    # Which of each copied figure's brackets the punctuator wrote at the
    # figure's gap, beyond the brackets of other print there (KEEP): those
    # are the figure's, wherever in the gap they stand.
    held = {k: {bracket: sum(1 for c in gaps[figures[k][0]] if c in forms)
                > (figures[k][3] if len(figures[k]) > 3 else {}).get(bracket, 0)
                for bracket, forms in (("（", OPEN_BRACKETS), ("）", CLOSE_BRACKETS))}
            for k in copy_at}
    for k in sorted(range(len(figures)), key=lambda k: -figures[k][0]):
        if how[k] == "written":
            gaps[figures[k][0]] = figure_in_gap(gaps[figures[k][0]], *figures[k][1:])
    for k, g in copy_at.items():
        # The punctuator's copy of the figure: the figure's brackets it wrote
        # neither at the figure's gap nor beside the copy's digits, written
        # beside them (the point after them stays after the close).
        found = re.search(rf"(?<![0-9]){digits_of[k]}(?![0-9])", gaps[g])
        figure = figures[k][1]
        if found is None:
            continue
        s, e = found.span()
        opening = ("（" in figure and not held[k]["（"]
                   and not (s and gaps[g][s - 1] in OPEN_BRACKETS))
        closing = ("）" in figure and not held[k]["）"]
                   and not gaps[g][e:e + 1] in CLOSE_BRACKETS)
        if opening or closing:
            gaps[g] = (gaps[g][:s] + ("（" if opening else "") + gaps[g][s:e] + ("）" if closing else "")
                       + gaps[g][e:])
            how[k] += "-bracketed"
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out), how


def restore_arabic_figures(given: str, formatted: str, figures: Sequence[tuple[Any, ...]]) -> tuple[str, list[str]]:
    """FORMATTED (the structure pass's page) with each (gap, figure[, marks
    after it]) of FIGURES
    the text GIVEN to it held - gap k before GIVEN's character k as CJK counts
    them - that FORMATTED no longer holds, written back at the same place
    (figure_in_gap): "kept" where its gap still holds its digits, "moved"
    where the page holds as many copies of them as GIVEN, "restored", or
    "unplaced" where neither character beside the gap is in FORMATTED.  The
    copies are counted gap by gap: two figures one character apart are two
    numbers, not one (a 1 moved next to the 2 of 甲乙1丙2丁 is still there).
    Only brackets beyond those GIVEN held there besides the figure's own are
    taken for the figure's (figure_in_gap's KEEP)."""
    source, target = CJK(given), CJK(formatted)
    matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
    where: dict[int, int] = {}
    for i, j, n in matcher.get_matching_blocks():
        for d in range(n):
            where[i + d] = j + d
    chars, gaps = engine_gaps(formatted)
    given_gaps = engine_gaps(given)[1]
    how = [""] * len(figures)
    counts_given = Counter(re.findall(r"[0-9]+", given))
    for k in sorted(range(len(figures)), key=lambda k: -figures[k][0]):
        gap, figure = figures[k][:2]
        digits = re.sub(r"[^0-9]", "", figure)
        at = (where[gap] if gap in where else where[gap - 1] + 1 if gap - 1 in where else None)
        if at is None:
            how[k] = "unplaced"
        elif figure_written(gaps[at], digits):
            how[k] = "kept"
        elif sum((Counter(re.findall(r"[0-9]+", g)) for g in gaps), Counter())[digits] >= counts_given[digits]:
            how[k] = "moved"
        else:
            held = Counter(bracket_kinds(given_gaps[gap] if 0 <= gap < len(given_gaps) else ""))
            keep = {bracket: max(0, held[bracket] - figure.count(bracket)) for bracket in "（）"}
            gaps[at] = figure_in_gap(gaps[at], figure, *(figures[k][2:3] or [""]), keep=keep)
            how[k] = "restored"
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out), how


def arabic_figure_kept(page_figures: Mapping[int, Sequence[Mapping[str, Any]]],
                       segments: Sequence[Mapping[str, Any]], starts: Sequence[int], pieces: Sequence[str],
                       after_rules: Mapping[int, Mapping[str, Any]],
                       settled: Mapping[int, tuple[str, bool]],
                       blocks: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Which of engine A's figures on a page the merge writes
    (--keep-arabic-numerals), once every piece is settled: (the figures kept,
    each with its gap in the merged text; every figure, kept or not and why,
    for the page's record).  A figure is kept where it is confirmed
    (figures_confirmed, or a contents entry's page number: arabic_figures'
    "leader"), alone at its gap, and the only reading of its print:
    the gap lies in a stretch both engines read alike, or between two
    readings each engine read as many characters of once the bracket and mark
    rules took out what engine A's marks there account for (AFTER_RULES),
    and whatever engine B read at the gap itself the figure's rules settled
    as no text (SETTLED: its brackets or marks, its stand-in glyphs, or 裁決's
    answer that it is the figure or no character; and whether the merge
    would write characters there without the figure).  PAGE_FIGURES maps a gap
    of engine A's stream to the figures there (arabic_figures)."""
    chars = "".join(x["a"] for x in segments)
    kept, seen = [], []
    for g in sorted(page_figures):
        figures = page_figures[g]
        how, engine_b, replaced, why = "nothing", "", False, None
        if len(figures) > 1:
            why = "two figures at one place"
        elif not figures[0]["confirmed"]:
            why = "numbering not continued"
        for i, (x, s) in enumerate(zip(segments, starts)):
            la = len(x["a"])
            if why or s > g:
                break
            if s + la < g or (x["tag"] == "equal" and "window" not in x and not x.get(LOOPED_READING)):
                continue
            if "window" in x or x.get(LOOPED_READING):
                why = "an order-conflict window or a looped block"
            elif not la and s == g:
                if i not in settled:
                    why = "engine B's reading there stands"
                else:
                    (how, replaced), engine_b = settled[i], x["b"]
            elif la and s < g < s + la:
                why = "inside a replacement"
            elif la and (s == g or s + la == g):
                y = after_rules.get(i)
                if y is None or not y["a"] or len(y["a"]) != len(y["b"]):
                    why = "beside a replacement of unequal readings"
        figure = figures[0]
        entry = {"figure": figure["figure"], "value": figure["value"],
                 "block": layout_index(blocks, figure["block"]),
                 "contextBefore": chars[max(0, g - 8):g], "contextAfter": chars[g:g + 8],
                 "confirmed": figure["confirmed"], **({"leader": True} if figure.get("leader") else {})}
        if why:
            seen.append({**entry, "kept": False, "why": why})
            continue
        merged = 0
        for x, s, piece in zip(segments, starts, pieces):
            la = len(x["a"])
            if s + la < g or (la and s + la == g) or (not la and s < g):
                merged += len(CJK(piece))
                continue
            if la and s < g:
                merged += g - s
            break
        seen.append({**entry, "kept": True, "how": how, **({"engineB": engine_b} if engine_b else {})})
        kept.append({**figure, "merged": merged, "how": how, "engineB": engine_b, "replaced": replaced,
                     "record": seen[-1]})
    return kept, seen


def figure_evidence_seal(arguments: Any) -> str:
    """The seal of what --keep-arabic-numerals read off the book's drafts: each
    page's figures and the glyphs taken for engine B's readings of them."""
    return sha256_bytes(canonical_json_bytes({
        "numbering": {str(page): values for page, values in
                      ((getattr(arguments, "figure_evidence", None) or {}).get("numbering") or {}).items()},
        "aliases": sorted(getattr(arguments, "figure_aliases", None) or {})}))


def figure_verdict(record: Mapping[str, Any], figure: Mapping[str, Any]) -> str | None:
    """What 裁決's answer (its sealed RECORD) says of engine B's reading where
    engine A read FIGURE (--keep-arabic-numerals): "figure" where the answer
    is the figure's digits - in brackets or with a point, as adjudicate_segment
    parses an answer - and was not used (a figure is no character:
    engine-A-default); "nothing" where it answered that no character is
    printed there and that stood (adjudicator-empty); else None - an answer
    that reads a character there, or that was refused, leaves the merge as it
    is without the switch."""
    if record.get("resolved_from") == "adjudicator-empty":
        return "nothing"
    if record.get("resolved_from") != "engine-A-default":
        return None
    answer = verdict_answer(str(record.get("verdict") or "")) or ""
    found = re.fullmatch(r"([0-9]+)\.?", unicodedata.normalize("NFKC", answer))
    return "figure" if found and found.group(1) == figure["digits"] else None


def split_edge_circle(seg: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int] | None:
    """A single circle at either end of a disagreement, split off from the rest.

    A printed circle next to a character the engines also read differently
    comes out of the alignment fused with that substitution - engine A 〇旂,
    engine B 旒 - so it is neither a lone circle nor a numeral conflict, and the
    whole segment went to the engine preference, circle and all (measured: 3
    of the 20 reading errors left on five pages after the lone-circle rule).
    Returns the two segments in reading order and the index of the circle's,
    or None when neither side has a single circle at an end the other lacks.
    The caller keeps the split only when the circle's segment is a lone circle
    (is_lone_circle), so nothing else changes.  Contexts are cut from engine
    A's stream, as align_drafts cuts them.
    """
    if seg["tag"] == "equal":
        return None
    for side, other in (("a", "b"), ("b", "a")):
        text, facing = seg[side], seg[other]
        if len(text) < 2:
            continue
        if text[0] in CIRCLE_CHARS and text[1] not in CIRCLE_CHARS and facing[:1] not in CIRCLE_CHARS:
            lead, circle, rest = True, text[0], text[1:]
            beside = rest[:1] + facing[:1]
        elif text[-1] in CIRCLE_CHARS and text[-2] not in CIRCLE_CHARS and facing[-1:] not in CIRCLE_CHARS:
            lead, circle, rest = False, text[-1], text[:-1]
            beside = rest[-1:] + facing[-1:]
        else:
            continue
        # The character next to the circle in the engine that read it, and the
        # other engine's character at that end: a numeral in either makes the
        # circle a likely zero (engine A 一九工, engine B 一九二〇), which the
        # contexts - cut from engine A's stream - would not show.
        if any(c in NUMERAL_CHARS or c.isdigit() for c in beside):
            continue
        on_a = side == "a"
        remainder = {side: rest, other: facing}
        remainder["tag"] = "replace" if remainder["a"] and remainder["b"] else ("delete" if remainder["a"] else "insert")
        mark = {"tag": "delete" if on_a else "insert", "a": circle if on_a else "", "b": "" if on_a else circle}
        if "lopsided" in seg:
            # A piece of a split lopsided replacement stays one (split_lopsided).
            remainder["lopsided"] = mark["lopsided"] = seg["lopsided"]
        before, after = seg["before"], seg["after"]
        if lead:
            mark.update(before=before, after=(remainder["a"] + after)[:8])
            remainder.update(before=(before + mark["a"])[-8:], after=after)
            return [mark, remainder], 0
        mark.update(before=(before + remainder["a"])[-8:], after=after)
        remainder.update(before=before, after=(mark["a"] + after)[:8])
        return [remainder, mark], 1
    return None


def split_lopsided(seg: Mapping[str, Any], a_edges: Collection[int] = (),
                   b_edges: Collection[int] = ()) -> tuple[list[dict[str, Any]], str] | None:
    """A lopsided replacement - one engine read INSERTION_LIMIT characters or
    more beyond the other there - split into a like-for-like part and the
    surplus, which is text only one engine read (the user's fix of 2026-09-27).

    The alignment fuses a line one engine skipped with a character or two the
    engines read differently next to it, and the preference then wrote the
    preferred engine's short side, deleting the rest with no doubt: measured,
    engine A skipped a printed line and a different form of the next heading's
    first character (決/决) fused them, 17 characters lost on two runs; with
    engine B v1.0, engine A's bullet read as 〇 lost 8 characters of
    signatures on one page, and engine A's two-figure folio a 7-character
    list item on another - neither trigger a variant, so the folded
    alignment alone does not stop them.  An insertion was always kept (the
    preference falls back to the side that has text); a lopsided replacement
    was not.

    The like-for-like part is the character or two read differently beside
    the skipped line: a short side of more than INSERTION_LIMIT characters is
    taken for text of its own, not a misreading of part of the other side
    (measured on the benchmark replay, with every page split: engine A's
    four-character fold title, set against a sentence only engine B read,
    was paired with the sentence's first four characters where a line of
    engine B's happened to break after them).  Where it lies is read off
    each engine's own structure, as positions inside the sides where a
    layout block of engine A's begins or ends (A_EDGES) or a line of engine
    B's does (B_EDGES):

    - either side is whole blocks or lines of its engine (it begins and ends
      at one), or the short side is longer than INSERTION_LIMIT: the other
      side is no reading of any part of it - both are text only one engine
      read ("none"; the short side first when it ends a block or line of its
      own engine, else after: an insertion at a block's end goes to the next
      block, block_spans);
    - else the surplus ends where a block or line of the long side's engine
      begins, with the short side read against what follows ("end"), or
      begins where one ends ("start") - when only one of the two holds;
    - else the short side begins a block or line of its engine, so the text
      its engine skipped came before it ("end"), or ends one ("start");
    - else nothing says, and both are text only one engine read ("none").

    Returns the segments in reading order and how the sides were set, or None
    when SEG is not lopsided (or is a numeral run fused for the crop, engine
    B's reading of a looped block, a window of an order-conflict table
    block).  Contexts are cut from engine A's stream, as align_drafts cuts
    them.
    """
    a, b = seg["a"], seg["b"]
    if (seg["tag"] != "replace" or not a or not b or abs(len(a) - len(b)) <= INSERTION_LIMIT
            or seg.get("fused") or seg.get(LOOPED_READING) or "window" in seg or "settled" in seg
            or is_numeral_conflict(dict(seg)) or is_rare_codepoint_conflict(dict(seg))):
        # A number (or a rare codepoint) is read off the crop whole, never
        # settled by a preference.
        return None
    long_side, short_side = ("a", "b") if len(a) > len(b) else ("b", "a")
    n, m = len(seg[long_side]), len(seg[short_side])
    edges = {"a": set(a_edges), "b": set(b_edges)}
    own, other = edges[long_side], edges[short_side]
    if m > INSERTION_LIMIT or (0 in own and n in own) or (0 in other and m in other):
        how = "none"
    elif (n - m in own) != (m in own):
        how = "end" if n - m in own else "start"
    elif (0 in other) != (m in other):
        how = "end" if 0 in other else "start"
    else:
        how = "none"
    if how == "none":
        alone = {long_side: seg[long_side], short_side: ""}
        short = {short_side: seg[short_side], long_side: ""}
        parts = [short, alone] if m in other else [alone, short]
    else:
        cut = n - m if how == "end" else m
        head = {long_side: seg[long_side][:cut], short_side: seg[short_side] if how == "start" else ""}
        tail = {long_side: seg[long_side][cut:], short_side: seg[short_side] if how == "end" else ""}
        parts = [head, tail]
    out, a_seen = [], ""
    for part in parts:
        a_rest = "".join(p["a"] for p in parts[len(out) + 1:])
        tag = "replace" if part["a"] and part["b"] else "delete" if part["a"] else "insert"
        out.append({"tag": tag, "a": part["a"], "b": part["b"],
                    "before": (seg["before"] + a_seen)[-8:], "after": (a_rest + seg["after"])[:8],
                    "lopsided": {"engineA": a, "engineB": b, "how": how,
                                 "surplus": tag != "replace"}})
        a_seen += part["a"]
    return out, how


def gram_counts(text: str) -> Counter:
    """How often each DROP_GRAM-character stretch stands in TEXT."""
    return Counter(text[i:i + DROP_GRAM] for i in range(len(text) - DROP_GRAM + 1))


def moved_share(text: str, reading: str) -> float:
    """How much of TEXT the other engine's READING holds as well: the share of
    its characters inside a DROP_GRAM-character stretch that stands in the
    reading too (both variant-folded).

    Anywhere in the reading, not as often as in TEXT's engine's reading
    (readings_dropped counts): a line one engine read twice has each stretch
    twice in its reading and once in the other's, and counted so its second
    copy was held 0.00 and split into a second written copy (measured: a line
    of signatures, 1.00 by this share).  Counted against TEXT's own
    stretches instead, the parallel list item readings_dropped now reports
    stays 0.78, and 9 of the 448 lopsided replacements of the corpus's page
    pairs (folded) cross MOVED_SHARE, three of them a run of one character
    one engine repeated (餘餘…, 〇〇〇…) set against the other's text, which
    this share leaves to the preference."""
    text, reading = variant_fold(CJK(text)), variant_fold(CJK(reading))
    if not text:
        return 0.0
    grams = {reading[i:i + DROP_GRAM] for i in range(len(reading) - DROP_GRAM + 1)}
    held = bytearray(len(text))
    for i in range(len(text) - DROP_GRAM + 1):
        if text[i:i + DROP_GRAM] in grams:
            held[i:i + DROP_GRAM] = b"\1" * DROP_GRAM
    return sum(held) / len(text)


# Above this share of a lopsided replacement's longer reading held by the other
# engine's reading of the page, the replacement is text read in another place,
# not a line one engine skipped (split_lopsided_segments).
MOVED_SHARE = 0.5


def looped_reading(text: str) -> bool:
    """Whether an engine's reading (of a page, or of one stretch) repeats
    itself: the order conflict's test."""
    chars = CJK(text)
    return len(chars) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars) < DEGENERATE_NGRAM_RATIO


def read_elsewhere(long_text: str, other_reading: str, page_looped: bool) -> bool:
    """Whether a lopsided replacement's longer reading LONG_TEXT is no line its
    engine read and the other skipped, and so stays one replacement for the
    preference or 裁決 to settle: it loops, or its engine's reading of the
    page does (PAGE_LOOPED), or more than MOVED_SHARE of it stands in the
    other engine's reading of the page, OTHER_READING (moved_share) - text
    read in another order, or read twice by its engine.  The test
    split_lopsided_segments and align_drafts' fold both apply."""
    return page_looped or looped_reading(long_text) or moved_share(long_text, other_reading) > MOVED_SHARE


def split_lopsided_segments(segments: Sequence[dict[str, Any]], block_ends: Sequence[int] | None,
                            draft: str, draft2: str, prefer: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every lopsided replacement of SEGMENTS the engine preference PREFER ("A"
    or "B") would settle with its engine's shorter reading split
    (split_lopsided), with engine A's block boundaries at BLOCK_ENDS (None
    when the blocks do not split engine A's stream) and engine B's lines from
    DRAFT2.  Returns the segments and one record per split - the replacement
    as aligned, where it stood in each engine's stream, how it was split -
    whose number each of its pieces carries (lopsided["id"]).

    Only where the preferred engine's reading is the shorter: where it is the
    longer, the preference writes it and loses nothing (measured over the
    corpus's 1,898 pages with both engines' drafts, aligned folded: 91 such
    replacements on the three books with a preference, against 37 with the
    preferred reading the shorter - a split of the 91 would only have made
    questions of the other engine's character or two).

    Not where the longer reading repeats itself, or its engine's reading of
    the page does (the order conflict's test): measured on the same pages,
    engine B ran into a loop at the foot of four pages (up to 7,704
    characters, 0.00 to 0.07 distinct 4-grams over its page), which the
    preference had set against engine A's last character or two - split, the
    loop would have been written into the page.

    Not a replacement whose longer reading the other engine read somewhere
    on the page (more than MOVED_SHARE of it, moved_share against the other
    engine's whole reading, DRAFT or DRAFT2): that is text read in another
    order, not a line skipped, and a split would write it twice.  Measured
    on the 26 replacements the round-2 scan found settled by the preference
    with three or more characters fewer than the longer reading: engine A
    read a line of signatures twice, and on another page two tiers of text
    in another order than engine B, and split, both came out with a second
    copy (their longer readings 1.00 and 0.94 held by the other engine); the
    three lines an engine skipped were held 0.00.  A running head the other
    engine read elsewhere on the page (0.40 to 1.00 held) is left to the
    preference as well when more than half of it is held, which wrote one
    copy of it before; split off otherwise, its surplus is left out as the
    book's furniture (lopsided_furniture)."""
    readings = {"a": CJK(draft), "b": CJK(draft2 or "")}
    looped = {side: looped_reading(text) for side, text in readings.items()}
    a_bounds = set([0, *block_ends]) if block_ends else set()
    b_bounds, at = set(), 0
    for line in strip_grounding(draft2 or "").splitlines():
        b_bounds.add(at)
        at += len(CJK(line))
    b_bounds.add(at)
    out: list[dict[str, Any]] = []
    splits: list[dict[str, Any]] = []
    a_at = b_at = 0
    for seg in segments:
        la, lb = len(seg["a"]), len(seg["b"])
        # Where engine B read it: its own place once D2 has placed a run
        # (bStart), else in segment order.
        b_at = seg.get("bStart", b_at)
        # D2's pieces of a run placed once (place_moved_once) are placed already.
        longer = "a" if len(seg["a"]) > len(seg["b"]) else "b"
        kept_whole = (longer == prefer.lower()
                      or read_elsewhere(seg[longer], readings["b" if longer == "a" else "a"], looped[longer]))
        found = (None if "movedFrom" in seg or "movedRemainderOf" in seg or kept_whole else
                 split_lopsided(seg, {p - a_at for p in a_bounds if a_at <= p <= a_at + la},
                                {p - b_at for p in b_bounds if b_at <= p <= b_at + lb}))
        if found:
            number = len(splits)
            splits.append({"id": number, "segment": dict(seg), "aStart": a_at, "bStart": b_at, "how": found[1]})
            b_piece = b_at
            for part in found[0]:
                piece = dict(part, lopsided=dict(part["lopsided"], id=number))
                if "bStart" in seg:
                    piece["bStart"] = b_piece
                b_piece += len(part["b"])
                out.append(piece)
        else:
            out.append(seg)
        a_at += la
        b_at += lb
    return out, splits


def fuse_numeral_runs(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent all-numeral segments into one conflict.

    When engine A reads （一） for a （二） and engine B reads 一一一, the
    alignment yields an *equal* 一 next to an *insert* 一一.  Adjudicating the
    insert alone can only add or drop strokes around a number that both engines
    got wrong, and the equal 一 is never questioned - measured result: （一）一本書
    and （二四四）.  So every maximal run of numeral-only segments that contains
    a disagreement is put to the crop as a single 甲/乙 pair covering the whole
    number; the verdict replaces the run.
    """
    def numeral_only(seg: dict[str, Any]) -> bool:
        return bool(seg["a"] or seg["b"]) and all(c in NUMERAL_CHARS for c in seg["a"] + seg["b"])

    # Peel numerals off the ends of equal segments so a shared 一 next to a
    # disputed 一一 is part of the same run.
    peeled: list[dict[str, Any]] = []
    for seg in segments:
        if seg["tag"] != "equal":
            peeled.append(seg); continue
        text = seg["a"]
        lead = 0
        while lead < len(text) and lead < NUMERAL_CONFLICT_LIMIT and text[lead] in NUMERAL_CHARS:
            lead += 1
        trail = 0
        while trail < len(text) - lead and trail < NUMERAL_CONFLICT_LIMIT and text[-1 - trail] in NUMERAL_CHARS:
            trail += 1
        parts = [text[:lead], text[lead:len(text) - trail], text[len(text) - trail:]]
        stream = seg["before"] + text + seg["after"]
        cursor = len(seg["before"])
        for part in parts:
            if part:
                peeled.append({"tag": "equal", "a": part, "b": part,
                               "before": stream[max(0, cursor - 8):cursor],
                               "after": stream[cursor + len(part):cursor + len(part) + 8]})
                cursor += len(part)
    segments = peeled

    out: list[dict[str, Any]] = []
    i = 0
    while i < len(segments):
        if not numeral_only(segments[i]):
            out.append(segments[i]); i += 1
            continue
        j = i
        while j < len(segments) and numeral_only(segments[j]):
            j += 1
        run = segments[i:j]
        a = "".join(x["a"] for x in run); b = "".join(x["b"] for x in run)
        if all(x["tag"] == "equal" for x in run) or len(a) > NUMERAL_CONFLICT_LIMIT or len(b) > NUMERAL_CONFLICT_LIMIT:
            out.extend(run)
        else:
            out.append({"tag": "replace", "a": a, "b": b,
                        "before": run[0]["before"], "after": run[-1]["after"], "fused": len(run),
                        # Digits both engines agree on inside the run, in order.
                        # Two-engine agreement outranks a verdict: a marker 二
                        # fused with the figure 十七 that followed it was
                        # answered "二", and 十七 - read by both engines - was lost.
                        "agreed": [x["a"] for x in run if x["tag"] == "equal" and x["a"]]})
        i = j
    return out


def measure_columns(render: Path, box: Sequence[float], text_only: bool = False,
                    turned: bool = False) -> list[tuple[int, int, int, int]] | None:
    """Real column geometry of a vertical-text block from its ink.

    Returns, right to left, (x0, x1, top, bottom) in render pixels for every
    ink column: x from the horizontal ink projection, top/bottom from the
    column's own vertical ink extent (a paragraph that ends half-way down
    leaves the rest blank).  No assumption about how many characters a
    column holds - that comes from the extent.  None when nothing usable is
    measured.

    TEXT_ONLY: the columns of text among them (text_columns), what
    crop_glyph cuts a disputed spot from.  column_layout takes every ink
    column.  TURNED: the render turned a quarter right (clockwise), and the
    pixels are the turned render's: the lines of horizontal text are then
    columns, the first on the right, each read top to bottom (crop_glyph).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(render) as image:
            x, y, w, h = box
            if turned:
                image = image.transpose(Image.ROTATE_270)
                x, y, w, h = 1 - y - h, x, h, w
            width, height = image.size
            left, top = int(x * width), int(y * height)
            region = image.convert("L").crop((left, top, int((x + w) * width), int((y + h) * height)))
    except Exception:  # noqa: BLE001
        return None
    rw, rh = region.size
    if rw < 20 or rh < 20:
        return None
    ink = ink_of(region)
    columns = ink_columns(ink)
    if text_only:
        columns = text_columns(ink, columns)
    return [(left + a, left + b, top + y0, top + y1) for a, b, y0, y1 in columns] or None


def text_columns(ink: "Image.Image", columns: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """The columns of text among COLUMNS, a block's ink columns (ink_columns
    of INK, in its own pixels), for measure_columns' TEXT_ONLY.

    Columns of text only: an ink column narrower than 0.4 of the block's
    text width is dropped, the fraction ink_columns drops slivers at.  Its
    own cut is taken against the median of all the block's ink runs, and
    where thin runs beside the columns (a dashed rule, a sideline, specks
    of the scan) outnumber the columns, the median is one of them.
    Measured on a page of a 488-page book: 10 of a block's 17 ink runs were
    1-13 px wide against 61-72 px of text, so six of them were counted as
    columns, the glyph pitch was fitted at 124 px instead of 65, and a
    disputed character's crop (crop_glyph) was a strip 4 px wide beside a
    column - no character on it, and too thin for the server, which lost
    the page.  On the vertical blocks of seven books 467 of 5,830 had such
    columns; on 256 of them the pitch was fitted 1.5 times too large or
    more.  The text width is the width-weighted median of the columns
    (half of the columns' width lies in columns at least that wide): thin
    runs carry little width however many there are.

    A short column is measured again before it is dropped.  ink_columns
    takes a column's width from its ink averaged over the whole block's
    height, so the last column of a paragraph that ends a few characters
    down is thin there although its characters are as wide as the others':
    measured on the same 488-page book, a last column of five characters,
    285 px of a block 2,400 px high, was 23 px wide against 59, and the
    crop of a spot in it was then the foot of the column before.  So a
    column narrower than the cut and shorter than half the block's longest
    (only such a column loses half its density or more to the average) is
    measured on its own rows, between its neighbours, at the block's own
    ink cut (own_rows_extent); where it is as wide as the text there it is
    kept, with that extent.  A rule, a sideline or a column of marks runs
    most of the block and is not measured again.  On the vertical blocks of
    seven books (5,378) 186 columns in 106 blocks are kept again; of 72
    looked at, 56 were the last characters of a paragraph, now measured
    their full width (before the cut they were kept as slivers 1-23 px
    wide), and 16 were strokes of figures, a form's dot leaders or a column
    of circles, which had been columns before the cut too.
    """
    if not columns:
        return columns
    from PIL import Image
    rw = ink.width
    widths = sorted((b - a for a, b, _, _ in columns), reverse=True)
    reached, text_width = 0, widths[0]
    for text_width in widths:
        reached += text_width
        if 2 * reached >= sum(widths):
            break
    peak = max(ink.resize((rw, 1), Image.BOX).getdata()) or 1
    longest = max(y1 - y0 for _, _, y0, y1 in columns)
    kept = []
    for i, (a, b, y0, y1) in enumerate(columns):
        if b - a < 0.4 * text_width:
            if 2 * (y1 - y0) >= longest:
                continue
            lo = columns[i + 1][1] if i + 1 < len(columns) else 0
            hi = columns[i - 1][0] if i > 0 else rw
            a, b = own_rows_extent(ink, (a, b, y0, y1), lo, hi, peak, 0.25 * text_width)
            if b - a < 0.4 * text_width:
                continue
        kept.append((a, b, y0, y1))
    return kept


def own_rows_extent(ink: "Image.Image", column: tuple[int, int, int, int], lo: int, hi: int,
                    peak: float, gap: float) -> tuple[int, int]:
    """An ink column's x extent measured on its own rows (top to bottom of its
    ink), between LO and HI, at the same ink density as the block's: more than
    a tenth of PEAK, the block's densest x, as ink_columns cuts; runs split by
    less than GAP are one."""
    from PIL import Image
    a, b, y0, y1 = column
    if hi - lo < 1 or y1 - y0 < 1:
        return a, b
    profile = list(ink.crop((lo, y0, hi, y1)).resize((hi - lo, 1), Image.BOX).getdata())
    runs: list[list[int]] = []
    for x, value in enumerate(profile):
        if value > 0.10 * peak:
            if runs and x - runs[-1][1] < gap:
                runs[-1][1] = x + 1
            else:
                runs.append([x, x + 1])
    through = [(lo + s, lo + e) for s, e in runs if lo + s < b and lo + e > a]
    if not through:
        return a, b
    return min(a, min(s for s, _ in through)), max(b, max(e for _, e in through))


def ink_of(region: "Image.Image") -> "Image.Image":
    """The ink of a grayscale region: 255 where darker than its own paper by
    more than 60 levels (the paper is the modal level), 0 elsewhere."""
    hist = region.histogram()
    background = max(range(256), key=lambda i: hist[i])
    threshold = background - 60
    return region.point(lambda v: 255 if v < threshold else 0)


def ink_columns(ink: "Image.Image") -> list[tuple[int, int, int, int]]:
    """measure_columns on an ink image: every ink column, right to left, as
    (x0, x1, top, bottom) in the image's own pixels."""
    from PIL import Image
    rw, rh = ink.size
    xprof = list(ink.resize((rw, 1), Image.BOX).getdata())
    peak = max(xprof) or 1
    on = [v > 0.10 * peak for v in xprof]
    runs: list[list[int]] = []
    start = None
    for i, flag in enumerate(on + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append([start, i]); start = None
    if not runs:
        return []
    widths = sorted(b - a for a, b in runs)
    typical = widths[len(widths) // 2]
    # Merge runs split by a thin gap inside a glyph column; drop slivers.
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] < 0.25 * typical:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    columns = [(a, b) for a, b in merged if b - a >= 0.4 * typical]
    out: list[tuple[int, int, int, int]] = []
    for a, b in columns:
        col = ink.crop((a, 0, b, rh))
        yprof = list(col.resize((1, rh), Image.BOX).getdata())
        ypeak = max(yprof) or 1
        rows = [i for i, v in enumerate(yprof) if v > 0.10 * ypeak]
        if not rows:
            continue
        out.append((a, b, rows[0], rows[-1] + 1))
    out.sort(key=lambda c: -c[0])   # right to left
    return out


# A dot leader (目錄、表格入面嘅導引點線) is a run of small dots down a column,
# evenly spaced, with no character between them; so is a printed ellipsis, and
# both are written 「……」.  Their count is taken from the crop's ink with no
# model (leader_count), as the census of dot runs: the model's census counted 0
# marks on a table of contents printing 7 leaders, and the pass was skipped.  A
# dot is a blob of ink rows narrower than LEADER_DOT_SIZE of the column's width
# and wider than LEADER_DOT_MINIMUM of it (characters span the column; specks
# are smaller), round or two or three dots touching; a run is its dots in a row
# with no character between, each step within half to LEADER_STEP times their
# median spacing, and it counts from LEADER_MINIMUM_DOTS.  Measured on every
# page of six books (1,066 pages, vertical blocks): the 7 leaders of the table
# of contents ran 63-84 dots and nothing else there did; 9 runs of 8-11 dots
# on other pages were printed ellipses in prose, 5 of which engine A had
# written (engine B none); 2 more were not dots (a faint ruled line's fragments, a column of
# marks in a table); every other run was 7 dots or fewer, most of them 2 (a
# circle and a comma).  A printed ellipsis of 6 dots falls under the cut and is
# left to the model's census, so the count is a floor, not a ceiling.
# Vertical text only: a horizontal block is not measured.
LEADER_DOT_SIZE = 0.35
LEADER_DOT_MINIMUM = 0.10
LEADER_STEP = 2.5
LEADER_MINIMUM_DOTS = 8


def leader_dot_runs(region: "Image.Image") -> list[tuple[int, int, list[int]]] | None:
    """Per ink column of a grayscale block image, (x0, x1, [the length of each
    run of evenly spaced dots down it]); None when there are no columns."""
    from PIL import Image
    ink = ink_of(region)
    rw, rh = ink.size
    if rw < 20 or rh < 20:
        return None
    columns = ink_columns(ink)
    if not columns:
        return None
    # Text columns only: a printed rule between columns is a column of ink
    # too, a few pixels wide, and on a grey scan it breaks into fragments
    # that line up like dots.  Measured against the wider half of the
    # block's columns, which are text whatever else the block holds (a
    # heading column is wider still, rules far narrower).
    widths = sorted((x1 - x0 for x0, x1, _, _ in columns), reverse=True)
    upper = widths[:max(1, (len(widths) + 1) // 2)]
    text_width = upper[len(upper) // 2]
    out = []
    for x0, x1, top, bottom in columns:
        width = x1 - x0
        if width < 0.5 * text_width:
            continue
        strip = ink.crop((x0, top, x1, bottom))
        yprof = list(strip.resize((1, bottom - top), Image.BOX).getdata())
        blobs, start = [], None
        for y, v in enumerate(yprof + [0]):
            if v and start is None:
                start = y
            elif not v and start is not None:
                band = strip.crop((0, start, width, y)).resize((width, 1), Image.BOX)
                xs = [i for i, value in enumerate(band.getdata()) if value]
                extent = (xs[-1] - xs[0] + 1) if xs else 0
                blobs.append((start, y, extent))
                start = None
        runs, current = [], []
        for y0, y1, extent in blobs:
            # Narrow is what tells a dot from a character, which spans the
            # column: two or three dots printed touching make one taller blob,
            # still narrow, and a run must not break there.  Specks of the scan
            # are smaller than any printed dot, and a fragment of a faint ruled
            # line is far taller than it is wide, where a dot is round.
            dot = (LEADER_DOT_MINIMUM * width <= extent <= LEADER_DOT_SIZE * width
                   and y1 - y0 <= 3 * min(extent, LEADER_DOT_SIZE * width))
            if dot and current:
                pitches = [b[0] - a[0] for a, b in zip(current, current[1:])]
                pitch = sorted(pitches)[len(pitches) // 2] if pitches else None
                step = y0 - current[-1][0]
                if pitch is None or 0.5 * pitch <= step <= LEADER_STEP * pitch:
                    current.append((y0, y1))
                    continue
            if len(current) >= 2:
                runs.append(len(current))
            current = [(y0, y1)] if dot else []
        if len(current) >= 2:
            runs.append(len(current))
        out.append((x0, x1, runs))
    return out


def leader_count(crop: bytes | None) -> int | None:
    """How many dot leaders a block's crop prints (leader_dot_runs), or None
    when it cannot be measured: no crop, no ink columns, or horizontal text."""
    if not crop:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(crop)) as image:
            region = image.convert("L")
            region.load()
    except Exception:  # noqa: BLE001 - an unreadable crop is not measured
        return None
    ink = ink_of(region)
    xprof = list(ink.resize((region.width, 1), Image.BOX).getdata())
    yprof = list(ink.resize((1, region.height), Image.BOX).getdata())
    if not max(xprof) or not max(yprof):
        return None
    # block_is_vertical's test: vertical text leaves gaps between columns.
    xgap = sum(1 for v in xprof if v < 0.05 * max(xprof)) / region.width
    ygap = sum(1 for v in yprof if v < 0.05 * max(yprof)) / region.height
    if xgap < ygap:
        return None
    runs = leader_dot_runs(region)
    if runs is None:
        return None
    return sum(1 for _, _, lengths in runs for n in lengths if n >= LEADER_MINIMUM_DOTS)


TILE_CHARACTERS = 160   # a block larger than this is punctuated in column groups

def column_layout(render: Path, block: Mapping[str, Any]) -> list[tuple[int, int, int, int, int]] | None:
    """Columns of a vertical block, right to left, with their character counts.

    Each column's share of the block's characters is its ink extent divided by
    the glyph pitch, fitted so the shares add up to the block's character count.
    None when the geometry cannot be trusted.

    The columns are every ink column measure_columns finds, a rule or a
    sideline beside the text too (crop_glyph takes the columns of text only,
    text_columns).  The layout decides the column groups a large block's
    census and punctuate calls are cut in (block_tiles, column_reach, and
    the book profile's notation survey) and the edges a boundary crop shows
    (edge_region).
    """
    n = len(CJK(str(block["text"])))
    columns = measure_columns(render, block["box"])
    if not columns or n == 0:
        return None
    extents = [max(bottom - top, 1) for _, _, top, bottom in columns]
    pitch = sum(extents) / n
    if pitch <= 0:
        return None
    counts = [max(1, round(e / pitch)) for e in extents]
    if abs(sum(counts) - n) > max(2, n // 10):
        return None
    return [(x0, x1, top, bottom, count) for (x0, x1, top, bottom), count in zip(columns, counts)]


def block_tiles(render: Path, block: Mapping[str, Any], span_length: int,
                limit: int = TILE_CHARACTERS) -> list[tuple[bytes, int, int]] | None:
    """Split a large vertical block into groups of whole columns.

    The model cannot see marks a few pixels wide on a crop of a whole page
    (measured: on 570-character blocks the notation survey answered "no marks"
    with "570 marks" - it counted characters - on a book printed with circles
    throughout).  Block size is the layout detector's choice, not the book's,
    so crops are bounded here.  Returns (crop, start, end) with start/end in the
    block's merged text, scaled from the columns' character counts; None when
    the block is small, horizontal or its columns cannot be measured.
    """
    if span_length <= limit or block.get("label") == "table":
        return None
    if block_is_vertical(render, block["box"]) is False:
        return None
    layout = column_layout(render, block)
    if not layout:
        return None
    total = sum(c[4] for c in layout)
    scale = span_length / max(total, 1)
    groups, current, size = [], [], 0
    for column in layout:
        if current and size + column[4] > limit:
            groups.append(current); current, size = [], 0
        current.append(column); size += column[4]
    if current:
        groups.append(current)
    if len(groups) < 2:
        return None
    out, consumed = [], 0
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
            for number, group in enumerate(groups):
                x0 = min(c[0] for c in group); x1 = max(c[1] for c in group)
                top = min(c[2] for c in group); bottom = max(c[3] for c in group)
                colw = (x1 - x0) / len(group)
                # Right margin is kept (marks sit right of or below a character
                # in vertical setting); the left edge stops at the group so the
                # next group's marks are not counted twice.
                crop = image.crop((max(0, int(x0 - 0.15 * colw)), max(0, int(top - 0.02 * height)),
                                   min(width, int(x1 + 0.4 * colw)), min(height, int(bottom + 0.02 * height))))
                buffer = io.BytesIO(); crop.save(buffer, format="PNG")
                characters = sum(c[4] for c in group)
                start = consumed
                end = span_length if number == len(groups) - 1 else min(span_length,
                                                                       round((consumed / scale + characters) * scale))
                out.append((upscale_crop(buffer.getvalue()) or buffer.getvalue(), start, end))
                consumed = end
    except Exception:  # noqa: BLE001 - fall back to the whole block
        return None
    return out if out and out[-1][2] == span_length else None


def column_reach(render: Path, block: Mapping[str, Any], span_length: int) -> int:
    """One column's worth of a block's characters (its longest column, scaled
    as block_tiles scales them): how far a column group's text may be off its
    crop at either end (realign_answer); 0 when the columns cannot be measured."""
    layout = column_layout(render, block)
    if not layout:
        return 0
    counts = [column[4] for column in layout]
    return math.ceil(max(counts) * span_length / max(sum(counts), 1))


def crop_glyph(render: Path, block: Mapping[str, Any], index: int, run: int,
               context: int = 4, pad: float = 0.02) -> bytes | None:
    """Crop the cells of characters index..index+run of a block.

    Columns come from measure_columns, the columns of text only (TEXT_ONLY:
    a rule or a sideline beside the text is not a column, text_columns),
    whatever column_layout takes; each column's share of the block's
    characters is its ink extent divided by the glyph pitch, with the pitch
    fitted so the shares add up to the block's character count.  Measured
    need: a uniform-column estimate on a block of short paragraphs put the
    crop one column off, and the adjudicator confidently read the next item's
    number.

    RUN is the longer of the two engines' readings, and the crop shows all of
    it, with 2 * CONTEXT characters of reach above and below: the estimate
    drifts either way (an ink column that holds no text of the block's - a
    fold strip, a column engine A never read - makes the fitted pitch too
    small, and the spot then sits lower than estimated; measured: a crop that
    reached four characters above started one character below the disputed
    one, and 裁決 wrote the crop's top character for it).  A run that runs past
    its column's foot goes on at the head of the next column, and one that
    runs past the block's end was read before the spot, not after it: the
    crop is the box of every column the run touches.  Measured: 26 characters
    engine B read where engine A read the block's last four were cropped from
    those four down, clamped at the column's foot - the crop showed the last 7
    of them, 裁決 answered those 7, and 19 printed characters were deleted.

    Horizontal text is cut the same way on the render turned a quarter right
    (measure_columns' TURNED), where its lines are columns read right to
    left, each top to bottom, and the crop is turned back.  Which way a
    block's lines run is block_is_vertical's call, except for one line or
    one column, which it cannot tell (there is no gap between lines, and
    across the line its characters stand apart like columns).  So a block is
    measured first along its box's length (turned when the box is wider than
    tall), and one column there is a line read along it; a block of more
    lines is measured block_is_vertical's way where that fits.  A single
    column two characters' pitch wide or wider does not fit: it is the lines
    of a paragraph measured across, one ink column over the whole of them.

    Measured: the book profile cut the variant crops of books printed
    across as if in columns, the spot counted from the line's right end, or
    a strip across the whole paragraph through the middle of a line; for
    諭/谕, the crop showed 決, the line's last character.  Looked at, the
    crops of three such books showed the asked character on 7 of 181 and
    show it turned on 188 of 195; the other 7 lie a line or a few characters off,
    where the full-width marks, which take a cell in such print but are not
    counted, moved the estimate.  On every third page of one of them, 182
    of 821 blocks are single lines block_is_vertical takes for vertical,
    which phase 3 cut here (it shows a block it takes for horizontal whole,
    segment_crop_kind); of the whole book's sealed adjudications, 10 sat in
    such a line and 6 of them wrote neither engine's reading (杨彲 sealed as
    杨閣, the heading's third character).  On every other page of a book printed in columns,
    385 of 1,171 blocks are single columns it takes for horizontal (turned
    on its word, the crop of one, 1對十一師教導營訓話, was two characters,
    not the one asked); measured along their length they are cut as before,
    and that book's 120 variant crops are byte for byte the same.

    Not done: a block of two or three lines that block_is_vertical takes for
    vertical, its characters standing apart across the lines, is still cut
    as columns where they fit.  The title pages of the three books set 合编
    beside two lines, which measured along the box are then one ink column
    and do not fit; cut as columns, 合编 was sealed as 大, 社, 无 or 一 (7
    adjudications on five title pages).  Neither rule tried tells such
    blocks from blocks printed in columns (every third page of twelve
    books): reading a wide box along its lines whenever they fit turned 7
    blocks of four books printed in columns, and refusing a fit with a
    column two pitches wide left blocks of columns without one.
    """
    n = len(CJK(str(block["text"])))
    if n == 0:
        return None

    def fit(turned: bool) -> tuple[list[tuple[int, int, int, int]], list[int]] | None:
        columns = measure_columns(render, block["box"], text_only=True, turned=turned)
        if not columns:
            return None
        extents = [max(bottom - top, 1) for _, _, top, bottom in columns]
        pitch = sum(extents) / n
        counts = [max(1, round(e / pitch)) for e in extents]
        if abs(sum(counts) - n) > max(2, n // 10):
            return None
        if len(columns) == 1 and columns[0][1] - columns[0][0] >= 2 * pitch:
            return None   # the lines of a paragraph measured across, not a column
        return columns, counts

    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001
        return None
    # Measured first along the box's length: one column there is one line.
    turned = block["box"][2] * width > block["box"][3] * height
    fitted = fit(turned)
    if not (fitted and len(fitted[0]) == 1) and (block_is_vertical(render, block["box"]) is False) != turned:
        # More lines than one: block_is_vertical's way, where they fit.
        other = fit(not turned)
        if other:
            turned, fitted = not turned, other
    if not fitted:
        return None
    columns, counts = fitted
    run = max(run, 1)
    total = sum(counts)
    # The run as the columns hold it, moved back to end at the block's end
    # when it would run past it.
    start = max(0, min(index, total - run))
    end = min(total, start + run)
    boxes = []
    first = 0
    for (x0, x1, top, bottom), count in zip(columns, counts):
        last = first + count
        local_pitch = (bottom - top) / count
        if first < end and last > start:
            # The reach stays inside the columns the run touches.
            lo, hi = max(first, start - 2 * context), min(last, end + 2 * context)
            boxes.append((x0, x1, top + (lo - first) * local_pitch, top + (hi - first) * local_pitch))
        first = last
    if not boxes:
        return None
    x0, x1 = min(b[0] for b in boxes), max(b[1] for b in boxes)
    y0, y1 = min(b[2] for b in boxes), max(b[3] for b in boxes)
    colw = max(b[1] - b[0] for b in boxes)
    try:
        from PIL import Image
        with Image.open(render) as image:
            if turned:
                image = image.transpose(Image.ROTATE_270)
            width, height = image.size
            crop = image.crop((max(0, int(x0 - 0.3 * colw)), max(0, int(y0)),
                               min(width, int(x1 + 0.3 * colw)), min(height, int(y1))))
            if turned:
                crop = crop.transpose(Image.ROTATE_90)
            buffer = io.BytesIO(); crop.save(buffer, format="PNG")
            return upscale_crop(buffer.getvalue())
    except Exception:  # noqa: BLE001
        return None


def crop_band(render: Path, block: Mapping[str, Any], seg: Mapping[str, Any],
              reach: float = 0.10, pad: float = 0.02, index: int | None = None) -> bytes | None:
    """Crop a horizontal band of a block around a disagreement.

    A whole vertical block is a strip of thousands of pixels in which one
    character must be found; a faint stroke at the block's edge is lost to the
    box's own boundary (measured: a 二 whose lower stroke printed light at a
    column foot was cropped to 一).  The position is estimated from the
    character's index in the block text, with columns approximated from the
    block's shape, and the band is padded well beyond the box.
    """
    text = norm_for_diff(str(block["text"]))
    # Geometry in CJK units, the same units as an offset from the merge: a table
    # block's raw text carries HTML tags that would inflate the glyph count.
    n = max(len(CJK(str(block["text"]))), 1)
    if index is None:
        index = -1
        after_key = norm_for_diff(seg["after"][:6])
        if len(after_key) >= 2 and after_key in text:
            index = max(0, text.find(after_key) - max(len(norm_for_diff(seg["a"])), 1))
        else:
            for key in (norm_for_diff(seg["a"]), norm_for_diff(seg["b"]), norm_for_diff(seg["before"][-6:])):
                if len(key) >= 2 and key in text:
                    index = text.find(key); break
    x, y, w, h = block["box"]
    if index < 0 or w <= 0 or h <= 0:
        return crop_block(render, block["box"], pad)
    # Glyph pitch from area: n glyphs fill w*h, so pitch ~ sqrt(w*h/n) (page fractions,
    # corrected for the page's aspect ratio being applied per axis at crop time).
    columns = max(1, round(w / max((w * h / n) ** 0.5, 1e-6)))
    per_column = n / columns
    fraction = (index % per_column) / per_column if per_column else 0.0
    # Reach at least the disputed run plus three glyphs each side, in glyph
    # units - a fixed fraction of a short block of figures was one character
    # (measured: a three-digit conflict judged on its first digit alone).
    run = max(len(seg.get("a", "")), len(seg.get("b", "")))
    reach = max(reach, (run + 3) / per_column if per_column else reach)
    top = y + max(0.0, fraction - reach * 0.5) * h
    bottom = y + min(1.0, fraction + reach) * h
    # Narrow to the estimated column (right-to-left) with a half-column margin:
    # a neighbouring column's own marker in the crop was measured to be read as
    # the answer.
    column = min(columns - 1, int(index // per_column)) if per_column else 0
    column_width = w / columns
    right = x + w - column * column_width + 0.2 * column_width
    left = x + w - (column + 1) * column_width - 0.2 * column_width
    left, right = max(x - pad, left), min(x + w + pad, right)
    return upscale_crop(crop_block(render, [left, top, right - left, bottom - top], pad / 2))


def edge_region(render: Path, block: Mapping[str, Any], end: bool, run: int, size: tuple[int, int],
                context: int = 4, pad: float = 0.01) -> tuple[int, int, int, int]:
    """The render pixels (left, top, right, bottom) at one edge of a layout
    block: its first glyphs (END false) or its last (END true), for
    boundary_crop.

    A vertical block's end is the foot of its last (leftmost) column, from
    CONTEXT glyphs above the column's last ink to RUN + CONTEXT glyphs below
    it - past the box, in the reading direction, since a character engine A
    left out of its reading may be left out of its box too.  Its start is cut
    as crop_glyph cuts the block's first character.  Columns and pitch come
    from column_layout; when they cannot be measured, from the box's shape as
    crop_band estimates them, the whole column's height taken.  A horizontal
    block or a table is the whole box, as segment_crop_kind shows it.
    """
    width, height = size
    x, y, w, h = block["box"]
    whole = (max(0, int((x - pad) * width)), max(0, int((y - pad) * height)),
             min(width, int((x + w + pad) * width)), min(height, int((y + h + pad) * height)))
    if block.get("label") == "table" or block_is_vertical(render, block["box"]) is False:
        return whole
    layout = column_layout(render, block)
    if layout:
        x0, x1, top, bottom, count = layout[-1] if end else layout[0]
        pitch = (bottom - top) / count
        if end:
            y0, y1 = bottom - min(count, context) * pitch, bottom + (run + context) * pitch
        else:
            y0, y1 = top, top + min(count, run + 2 * context) * pitch
        margin = 0.3 * (x1 - x0)
        return (max(0, int(x0 - margin)), max(0, int(y0)), min(width, int(x1 + margin)), min(height, int(y1)))
    n = max(len(CJK(str(block["text"]))), 1)
    if w <= 0 or h <= 0:
        return whole
    columns = max(1, round(w / max((w * h / n) ** 0.5, 1e-6)))
    column_width, pitch = w / columns, h * columns / n
    left = x if end else x + w - column_width
    top, bottom = (y, y + h + (run + context) * pitch) if end else (y, y + min(h, (run + 2 * context) * pitch))
    return (max(0, int((left - 0.2 * column_width) * width)), max(0, int((top - pad) * height)),
            min(width, int((left + 1.2 * column_width) * width)), min(height, int(bottom * height)))


# What 裁決 is told about a boundary crop's two parts, by how they are set.
BOUNDARY_NOTES = {
    "columns": "裁圖由兩段拼成，中間隔住一條空白：右邊係上文所在嗰段字嘅尾，左邊係下文所在嗰段字嘅頭。"
               "爭議位喺兩段交界：右邊嗰段最尾或者左邊嗰段最頭。",
    "rows": "裁圖由兩段拼成，中間隔住一條空白：上面係上文所在嗰段字嘅尾，下面係下文所在嗰段字嘅頭。"
            "爭議位喺兩段交界：上面嗰段最尾或者下面嗰段最頭。",
    "end": "裁圖係上文所在嗰段字嘅尾，連埋佢後面嘅空位。爭議位喺呢段字最尾。",
}


def boundary_crop(render: Path, previous: Mapping[str, Any], following: Mapping[str, Any] | None,
                  run: int) -> tuple[bytes | None, str]:
    """The crop for text only engine B read where engine A's reading passes
    from one layout block to the next, and how its parts are set (a
    BOUNDARY_NOTES key).

    The alignment cannot tell whether such text ends PREVIOUS or begins
    FOLLOWING, and the two blocks may stand anywhere on the page (another
    tier, another column group).  The crop from the insertion's place in
    engine A's stream showed the head of FOLLOWING alone, which cannot show a
    character printed at the foot of PREVIOUS: measured on a page whose two
    blocks stand on different tiers, the character printed at the foot of
    the first (inside its box; engine A's reading had left it out) was not
    on the crop, and 裁決 would delete it.  On one book 7 of the 16 one- and
    two-character insertions only engine B read sit on such an edge; on the
    stand-in benchmark pages 17 questions on 7 pages.  So both edges are
    shown, cut from the render at one scale and set
    side by side in reading order (right to left, when both blocks are
    vertical; else one above the other), with a strip of paper between; at the
    page's end, the end of PREVIOUS alone.  (None, "") when nothing can be cut.
    """
    try:
        from PIL import Image
        with Image.open(render) as opened:
            image = opened.convert("L") if opened.mode not in ("L", "RGB") else opened.copy()
        size = image.size
        foot = image.crop(edge_region(render, previous, True, run, size))
        if min(foot.size) < 2:
            return None, ""
        if following is None:
            buffer = io.BytesIO(); foot.save(buffer, format="PNG")
            return upscale_crop(buffer.getvalue()), "end"
        head = image.crop(edge_region(render, following, False, run, size))
        # The strip between the parts is the page's own paper (its modal level).
        levels = image.convert("L").histogram()
        paper = max(range(256), key=lambda i: levels[i])
    except Exception:  # noqa: BLE001 - the ordinary crop stands instead
        return None, ""
    if min(head.size) < 2:
        return None, ""
    vertical = all(block.get("label") != "table" and block_is_vertical(render, block["box"]) is not False
                   for block in (previous, following))
    fill = paper if image.mode == "L" else (paper, paper, paper)
    if vertical:
        gap = max(8, min(foot.width, head.width) // 2)
        joined = Image.new(foot.mode, (foot.width + gap + head.width, max(foot.height, head.height)), fill)
        joined.paste(head, (0, 0))
        joined.paste(foot, (head.width + gap, 0))
    else:
        gap = max(8, min(foot.height, head.height) // 4)
        joined = Image.new(foot.mode, (max(foot.width, head.width), foot.height + gap + head.height), fill)
        joined.paste(foot, (0, 0))
        joined.paste(head, (0, foot.height + gap))
    buffer = io.BytesIO(); joined.save(buffer, format="PNG")
    return upscale_crop(buffer.getvalue()), "columns" if vertical else "rows"


def upscale_crop(png: bytes | None, minimum_side: int = 700) -> bytes | None:
    """Enlarge a small crop so glyph strokes survive the vision encoder.

    Measured: a clear （二） at ~70 px glyph height was read as 一; stroke
    counting needs pixels.  Enlarging adds no information, but it stops the
    encoder's own downsampling from discarding what is there.
    """
    if not png:
        return png
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            short = min(image.size)
            if short >= minimum_side:
                return png
            factor = min(4.0, minimum_side / max(short, 1))
            enlarged = image.resize((int(image.width * factor), int(image.height * factor)), Image.LANCZOS)
            buffer = io.BytesIO(); enlarged.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - never lose the crop over a resize
        return png


VARIANT_LIST = Path(__file__).resolve().parent.parent / "references" / "variant-equivalents.json"
_VARIANT_RULINGS: dict[frozenset, bool] | None = None
# OpenCC's list of allographs: characters Unicode encodes at different code
# points that are one character (淸/清, 愼/慎, 旣/既).  Second to the user's own
# rulings: where both cover a pair they agreed on all 15 pairs, and it groups
# none of the pairs dictionaries and the model got wrong (刺/剌, 着/著, 已/巳)
# nor the two the user ruled apart (碍/礙, 赶/趕).
ALLOGRAPHS = VARIANT_LIST.with_name("opencc-allographs.txt")
_ALLOGRAPH_PAIRS: frozenset | None = None
# The hash of the bytes each list was parsed from, for the seal: the lists are
# read once per run, so a file edited mid-run must not be sealed as the one
# that decided the pages.
_LIST_SHA256: dict[str, str] = {}


def user_variant_rulings(path: Path = VARIANT_LIST) -> dict[frozenset, bool]:
    """Every pair the user ruled on: True = two forms of one character, False = not.

    A class of three forms (刧/劫/刼) rules each of its three pairs.  A missing
    or unreadable list rules nothing, so the model decides as before.
    """
    global _VARIANT_RULINGS
    if _VARIANT_RULINGS is None:
        ruled: dict[frozenset, bool] = {}
        try:
            raw = path.read_bytes()
            _LIST_SHA256["variantList"] = sha256_bytes(raw)
            classes = json.loads(raw.decode("utf-8")).get("classes") or []
        except (OSError, ValueError):
            classes = []
        for entry in classes:
            forms = [f for f in entry.get("forms") or [] if isinstance(f, str) and len(f) == 1]
            for x, y in itertools.combinations(forms, 2):
                if x != y:
                    ruled[frozenset((x, y))] = bool(entry.get("acceptable"))
        _VARIANT_RULINGS = ruled
    return _VARIANT_RULINGS


def opencc_allographs(path: Path | None = None) -> frozenset:
    """Every pair of characters one line of the allograph list puts together.

    A missing or unreadable list contributes nothing, so classification falls
    through to the profile and the model as before.
    """
    global _ALLOGRAPH_PAIRS
    if path is not None or _ALLOGRAPH_PAIRS is None:
        pairs: set[frozenset] = set()
        try:
            raw = (path or ALLOGRAPHS).read_bytes()
            lines = raw.decode("utf-8").splitlines()
            if path is None:
                _LIST_SHA256["allographs"] = sha256_bytes(raw)
        except (OSError, UnicodeError):
            lines = []
        for line in lines:
            forms = [c for c in line.strip() if not c.isspace()]
            if not forms or line.startswith("#"):
                continue
            pairs.update(frozenset(p) for p in itertools.combinations(dict.fromkeys(forms), 2))
        if path is not None:
            return frozenset(pairs)
        _ALLOGRAPH_PAIRS = frozenset(pairs)
    return _ALLOGRAPH_PAIRS


# OpenCC's table of traditional characters and their simplified forms
# (TSCharacters.txt), vendored byte for byte and pinned like the allograph
# list: a character it maps only to other characters exists only in
# traditional script (後 -> 后; not 乾, which simplified script writes too).
# In a book printed in simplified script (simplified_script) it tells the
# pairs whose forms differ in script alone, written as engine B read them where
# the book's rulings leave them open (resolve_variant), and the form an answer
# in traditional script is written in (simplified_as_read).  The lint reads it
# too (TEXT-TRADITIONAL).
TS_CHARACTERS = VARIANT_LIST.with_name("opencc-ts-characters.txt")
_TRADITIONAL_FORMS: dict[str, str] | None = None


def traditional_forms() -> dict[str, str]:
    """Each character only traditional script has -> its simplified forms, in
    the table's order (崙 -> 仑𪨧).  A missing or unreadable table gives none."""
    global _TRADITIONAL_FORMS
    if _TRADITIONAL_FORMS is None:
        try:
            lines = TS_CHARACTERS.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        table = {}
        for line in lines:
            key, _, values = line.partition("\t")
            forms = values.split()
            if not line.startswith("#") and len(key) == 1 and forms and key not in forms:
                table[key] = "".join(forms)
        _TRADITIONAL_FORMS = table
    return _TRADITIONAL_FORMS


def simplified_form(text: str) -> str:
    """TEXT with each character only traditional script has in its first
    simplified form (來 -> 来, 崙 -> 仑): for comparing an answer in
    traditional script with text printed in simplified script, never for
    writing."""
    table = traditional_forms()
    return "".join(table[c][0] if c in table else c for c in text)


def simplified_script(texts: Iterable[str]) -> bool:
    """Whether a book is printed in simplified script: the simplified forms of
    the characters only traditional script has outnumber those characters.
    Measured on both engines' drafts of five books (2026-10-10): 500 to 1,240
    times as many in three simplified archive volumes (a multi-volume archival compilation 2, 3, 5), about a
    seventh as many in two traditional books (后, 干, 里 are traditional
    characters too)."""
    table = traditional_forms()
    simplified = set("".join(table.values()))
    traditional = plain = 0
    for text in texts:
        for c in text:
            if c in table:
                traditional += 1
            elif c in simplified:
                plain += 1
    return plain > traditional


def simplified_as_read(answer: str, a: str, b: str) -> str:
    """ANSWER, 裁決's at a spot engine A read A and engine B read B, in a
    book printed in simplified script: each character only traditional
    script has that engine B did not read there is written in its simplified
    form where an engine read that form there (懸 where engine B read 悬 is
    悬); where neither engine read the character or any form of it, in its
    one simplified form when the table gives just one (蕆 where the engines
    read 藏 and 葳 is 蒇).  Otherwise as answered: 崙 where the engines read
    崙 and 嵛 stays 崙, and so does 崙 neither engine read, with two forms
    (仑 and 𪨧).  Engine B reads the script as printed (resolve_variant);
    裁決 often answers in traditional script.  The one form where neither
    engine read one, measured on the 2,051 phase 3 records of a multi-volume archival compilation 1-4
    whose printed reading is known (2026-10-25): 31 answers now as printed
    (30 of them 蒇, the engines reading 藏, 葳, 臧 or 赦), 5 no longer - 2
    on a handwritten plate printed in traditional script, 2 a name's
    character the measurement's reference held in traditional script, which
    the print gives in simplified script elsewhere (likely no break), 1
    title-page noise.  A name printed in traditional script is one an
    engine read there and stays: every such character converted instead
    fixes 38 and breaks 29."""
    table = traditional_forms()
    return "".join(c if c in b else next((form for form in table.get(c, "") if form in a + b),
                                         table[c] if c not in a and len(table.get(c, "")) == 1 else c)
                   for c in answer)


def converted_against_b(answer: str, a: str, b: str) -> str:
    """The characters simplified_as_read writes for ANSWER in a form engine
    B did not read, which a person checks (answer-script-converted): engine
    A's form where engine B read another character, none of the answer's
    forms in either script (塵 written 尘 as engine A read it, where engine B
    read 廑) - an answer in the wrong script that settled a disagreement
    against engine B's reading - and the table's one form, which neither
    engine read (蕆 written 蒇 where engine A read 藏 and engine B 葳 or
    nothing).  Measured on a multi-volume archival compilation 2, 3 and 5 (2026-10-10): 18 of the 91
    answers written in an engine's form, 11 of them misreadings either way
    (9 printed as engine B read them), 6 engine A's right reading, 1
    unclear; of the other 73, 3 were misreadings (engine B's 恒 where engine
    A read the printed 恇).  The one form is doubted where engine B read
    nothing too: TEXT-TRADITIONAL flagged the character while it stayed
    traditional, and a misreading there has no other flag (a multi-volume archival compilation 1 scan 66:
    相連變化 answered where engine A read the printed 水为先地下 and engine
    B nothing; replayed on the sealed records of a multi-volume archival compilation 1-5, 2026-10-26, the
    one spot this adds)."""
    table = traditional_forms()
    # What engine B read there, each character with its simplified forms.
    read_b = "".join(x + table.get(x, "") for x in b)
    return "".join(w for c, w in zip(answer, simplified_as_read(answer, a, b))
                   if c != w and (w not in a + b or (b and not any(form in read_b for form in table.get(c, "")))))


def variant_source(a: str, b: str) -> str | None:
    """Which list decides whether two characters are one: "user" (the user's
    own ruling, either way), "opencc" (an allograph pair), "script" (one is
    the other's form in simplified script, by OpenCC's table), or None."""
    pair = frozenset((a, b))
    if pair in user_variant_rulings():
        return "user"
    if pair in opencc_allographs():
        return "opencc"
    table = traditional_forms()
    if b in table.get(a, "") or a in table.get(b, ""):
        return "script"
    return None


def classify_pair(a: str, b: str) -> str:
    """'variant' when two single characters are forms of one character, else 'different'.

    Variant-form disputes (粵/粤, 既/旣) are not reading errors: which form a
    page prints is a question the vision judge was measured unable to answer
    (the same pair in one book's single type split 19:9), so they are kept
    out of the engine calibration and resolved per book instead.  Whether a
    pair IS a variant pair is knowledge, not vision, and only the lists are
    trusted with it: the user's own rulings (references/variant-equivalents.json)
    decide first, then OpenCC's allograph list (references/opencc-allographs.txt),
    then OpenCC's table of traditional characters and their simplified forms
    (references/opencc-ts-characters.txt).  A pair none of them covers is two
    characters, read from the image like any other disagreement.

    Until 2026-10-13 the model was asked about the pairs no list covers, and
    its answer did not hold: it called 刺/剌, 盡/藎, 祛/袪, 祇/祗 and 监/鉴
    forms of one character, and a multi-volume archival compilation 3 was delivered with 仰祈圣鉴 written
    圣监 four times, with no doubt.  On the six delivered books such pairs
    decided 442 sites: 71 were two different characters, 162 two characters
    some prints use for each other (于/於, 並/幷: only the print can tell),
    199 forms of one character (劫/刼, 出/岀), 10 misreadings or unsure.
    On a book with no engine preference (all six) those sites are now read
    from the image: 441 more 裁決 calls, about 1% of the model's time on five
    of the books and 5% on the sixth (measured 2026-10-13 on their seals); a
    form of one character no list holds can come out in either form.
    """
    a, b = CJK(a), CJK(b)
    if len(a) != 1 or len(b) != 1 or a == b:
        return "different"
    ruled = user_variant_rulings().get(frozenset((a, b)))
    if ruled is not None:
        return "variant" if ruled else "different"
    return "variant" if variant_source(a, b) else "different"


def variant_segment(seg: Mapping[str, Any]) -> list[tuple[str, str]] | None:
    """The variant pairs a disagreement consists of, or None if it is not one.

    A segment of equal length whose every differing position is a variant
    pair (況粵/况粤 is two) is a variant dispute.
    """
    a, b = CJK(seg["a"]), CJK(seg["b"])
    if not a or len(a) != len(b):
        return None
    pairs = [(x, y) for x, y in zip(a, b) if x != y]
    if not pairs or any(classify_pair(x, y) != "variant" for x, y in pairs):
        return None
    return pairs


def locate_segment(blocks: Sequence[Mapping[str, Any]], block_ends: Sequence[int],
                   block_lengths: Sequence[int], offsets_valid: bool, a_start: int,
                   seg: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, int | None]:
    """The engine-A layout block a disagreement sits in, and its offset there.

    Taken straight from the disagreement's position in engine A's character
    stream; text search at a block boundary was measured to crop the running
    foot instead of a table's last figure and delete it.
    """
    block, offset = None, None
    window = seg.get("window")
    if offsets_valid and isinstance(window, int) and 0 <= window < len(blocks):
        # A segment of an order-conflict table block read from engine B's
        # lines (window_merge) is that block's: its segments run in engine B's
        # order, so its place in engine A's stream only says which block it
        # is in, and at the block's end it would name the next block.
        start = block_ends[window - 1] if window else 0
        return blocks[window], min(max(a_start - start, 0), max(block_lengths[window] - 1, 0))
    if offsets_valid and blocks:
        k = next((i for i, end in enumerate(block_ends) if a_start < end), len(blocks) - 1)
        if not seg["a"] and k > 0 and a_start == block_ends[k - 1] and block_lengths[k] == 0:
            k -= 1
        block = blocks[k]
        offset = a_start - (block_ends[k - 1] if k else 0)
    if block is None:
        block = locate_block(list(blocks), dict(seg))
    return block, offset


def block_is_vertical(render: Path, box: Sequence[float]) -> bool | None:
    """Whether a block's text runs in vertical columns, from its ink alone.

    Vertical text leaves clear gaps between columns in the horizontal ink
    profile and an almost continuous vertical profile; horizontal text is the
    reverse.  None when the block is too small or blank to tell.
    """
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
            x, y, w, h = box
            region = image.convert("L").crop((int(x * width), int(y * height),
                                              int((x + w) * width), int((y + h) * height)))
    except Exception:  # noqa: BLE001
        return None
    rw, rh = region.size
    if rw < 20 or rh < 20:
        return None
    hist = region.histogram()
    background = max(range(256), key=lambda i: hist[i])
    ink = region.point(lambda v: 255 if v < background - 60 else 0)
    xprof = list(ink.resize((rw, 1), Image.BOX).getdata())
    yprof = list(ink.resize((1, rh), Image.BOX).getdata())
    if not max(xprof) or not max(yprof):
        return None
    xgap = sum(1 for v in xprof if v < 0.05 * max(xprof)) / rw
    ygap = sum(1 for v in yprof if v < 0.05 * max(yprof)) / rh
    return xgap >= ygap


def segment_crop(render: Path, render_bytes: bytes | None, block: Mapping[str, Any] | None,
                 offset: int | None, seg: Mapping[str, Any]) -> bytes | None:
    """The image an adjudicator is shown for one disagreement."""
    return segment_crop_kind(render, render_bytes, block, offset, seg)[0]


def segment_crop_kind(render: Path, render_bytes: bytes | None, block: Mapping[str, Any] | None,
                      offset: int | None, seg: Mapping[str, Any]) -> tuple[bytes | None, str]:
    """segment_crop, and which kind of crop it is: "glyph" or "band" (cut to the
    disputed characters' column), "block" (a whole horizontal block), "table"
    (a whole table) or "page" (the whole render)."""
    if block is not None and block.get("label") != "table" and \
            block_is_vertical(render, block["box"]) is False:
        # Column geometry is measured for vertical text only; for a horizontal
        # block show the whole block - larger, but it always contains the
        # disputed character, and the prompt's context locates it.
        crop = upscale_crop(crop_block(render, block["box"], pad=0.01))
        return (crop, "block") if crop else (render_bytes, "page")
    if block is not None and block.get("label") == "table":
        # A table block's text is HTML in row-major order; a position estimated
        # as if it were columns of running text lands on the wrong cell
        # (measured: three figures replaced by digits from elsewhere in the
        # grid). Show the whole table.
        crop = upscale_crop(crop_block(render, block["box"], pad=0.01))
        return (crop, "table") if crop else (render_bytes, "page")
    measured = (crop_glyph(render, block, offset, max(len(seg["a"]), len(seg["b"]), 1))
                if block is not None and offset is not None else None)
    if measured:
        return measured, "glyph"
    band = crop_band(render, block, seg, index=offset) if block else None
    return (band, "band") if band else (render_bytes, "page")


def segment_winner(seg: Mapping[str, Any], resolved: str, resolved_from: str) -> str:
    """Which engine the adjudicator sided with: A, B, neither, or undecided.

    This is the measurement behind a book's engine preference - counted by
    the profiler on probe pages and by the drift audit during a run.
    """
    if resolved_from in ("adjudicator-invalid", "adjudicator-partial", "adjudicator-deletion-refused",
                         "adjudicator-empty-unconfirmed", "adjudicator-neighbour-copy", "engine-A-default"):
        # The verdict was not used as given: no engine won it.
        return "undecided"
    a, b, got = CJK(seg["a"]), CJK(seg["b"]), CJK(resolved)
    if a == b:
        return "undecided"
    if got == a:
        return "A"
    if got == b:
        return "B"
    return "neither"


def adjudicate_prompt(seg: Mapping[str, Any], swap: bool = False, note: str = "") -> str:
    """The 裁決 question about one disagreement SEG (its before and after
    context, engine A's reading a and engine B's b), as adjudicate_segment
    asks it; SWAP shows engine B's reading first, NOTE is said before the
    context."""
    numeral = is_numeral_conflict(dict(seg))
    rare = is_rare_codepoint_conflict(dict(seg))
    rare_hint = (
        "\n呢個爭議有一方用咗罕見碼位（Unicode 擴展區）。佢可能係頁上真正嘅異體字形，"
        "亦可能係引擎亂配嘅碼位。請照圖上部件判定；如果圖上係常見字形，定案寫常見字。\n"
    ) if rare else ""
    numeral_hint = (
        "\n呢個爭議淨係關於數字／筆畫。甲乙兩邊寫嘅係**成個數字**（唔係其中一劃）："
        "一方多咗「一」之類，好多時係括號、格線或者筆畫被讀成咗字。"
        "請寫出圖上呢個位置**整個**數字（例如「二」「四」「十二」）；"
        "如果圖上根本冇數字，寫 `定案：無`。"
        "直排文字入面，括號係上下兩半（「︵」喺數字上面、「︶」喺下面），"
        "夾喺中間疊起嘅橫畫係**一個**數字：一橫係「一」、兩橫係「二」、三橫係「三」，"
        "唔好逐劃寫成「一一」。數橫畫之前，先由上至下逐項列出你見到嘅每一樣嘢"
        "（上弧、第一橫、第二橫……、下弧），上弧唔係橫畫，唔好將佢同第一橫合併計。\n") if numeral else ""
    prompt = (
        "兩個獨立 OCR 引擎喺呢個位置讀法唔同。兩個都可能錯，"
        "尤其係一方或雙方將異體字正規化咗。" + numeral_hint + rare_hint + (f"\n{note}\n" if note else "") + "\n"
        f"  上文：…{seg['before']}\n"
        # Which engine is shown first is varied by the caller: a judge that
        # leans toward the first option would otherwise be counted as one
        # engine being more accurate.
        f"  甲) {(seg['b'] if swap else seg['a']) or '（冇字）'}\n"
        f"  乙) {(seg['a'] if swap else seg['b']) or '（冇字）'}\n"
        f"  下文：{seg['after']}…\n\n"
        "先淨係描述圖上構件（唔准提甲乙），再由構件推出係邊個字，最後先同甲乙對照。"
        "若兩者皆非，寫出你見到嘅字。最後另起一行淨係寫：`定案：<字>`")
    return prompt


# The line adjudicate_prompt asks 裁決 to end on.
ANSWER_LINE = re.compile(r"^[ \t]*定案[：:][ \t]*(\S+)", re.M)


def verdict_answer(verdict: str) -> str | None:
    """裁決's answer in VERDICT: the first word of the last line that begins
    with 定案： (adjudicate_prompt asks for it last, on a line of its own), up
    to an opening bracket - what follows is commentary（兩個候選皆非）and must
    never reach the merge - or None where no line begins so.

    Not the first 定案： anywhere: the reasoning says it too.  Measured over
    six sealed books (4,967 verdicts, 2026-10-24): 4 wrote 定案： inside a
    sentence before the answer line; on 3 the merge wrote that sentence's
    words for the one character settled (#33: a radical's name and "is
    enough to rule out" for the character printed), the fourth D16 refused.
    None wrote 定案： only inside a line, and every answer line began at its
    first character (no markdown); 28 have no such line (12 empty, 16 a 判定
    line only), and as before they have no answer: engine A's reading
    stands (engine-A-default)."""
    lines = ANSWER_LINE.findall(verdict or "")
    if not lines:
        return None
    return re.split(r"[（(「\[]", lines[-1].lstrip("（(「"))[0].strip("）)」")


def adjudicate_segment(client: "Client", crop: bytes | None, seg: Mapping[str, Any],
                       max_tokens: int, fallback_engine: str | None = "A", swap: bool = False,
                       note: str = "", simplified: bool = False) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Ask the model to read one disagreement off its crop.

    NOTE says how the crop is made when it is not one stretch of the page
    (boundary_crop, BOUNDARY_NOTES).  SIMPLIFIED says the book is printed in
    simplified script (simplified_script): a character of the answer only
    traditional script has, which engine B did not read there, is written in
    the simplified form an engine read there, or in its one simplified form
    where neither engine read it or a form of it (simplified_as_read), and the
    answer as given is kept in the record (answeredTraditional).  Measured on
    a multi-volume archival compilation 2, 3 and 5 (2026-10-10): 91 answers so written (懸 where engine B
    read 悬), all looked at against the images: 76 now as printed, 14
    misreadings either way, 1 unclear.  The misreadings lost the
    TEXT-TRADITIONAL doubt that flagged 12 of them by chance (the lint takes
    恆 and 恒, 脫 and 脱 for one character); the 11 written in engine A's
    form where engine B read another character are a doubt now (塵 where 廑
    is printed: now 尘; answer-script-converted, converted_against_b), and
    so is each character written in the one form neither engine read.
    Returns the text to put in the merge, the sealed adjudication record
    (without its crop file name) and the call's usage metrics.
    """
    numeral = is_numeral_conflict(dict(seg))
    rare = is_rare_codepoint_conflict(dict(seg))
    prompt = adjudicate_prompt(seg, swap, note)
    verdict, metrics = client.ask_answering(ADJUDICATE_SYSTEM, prompt, crop, max_tokens, kind="adjudicate")
    verdict = (verdict or "").strip()
    match = RESEARCH_LINE.search(verdict)
    answer = verdict_answer(verdict)
    chosen, answer = answer is not None, answer or ""
    # 無 (and 冇, 空) is also a character.  When an engine read exactly that
    # character here, the answer is the character, not "no character":
    # measured, 裁決 read 無論 right, agreeing with engine A, and the page lost
    # its 無.
    explicit_empty = (bool(chosen) and answer in EMPTY_VERDICTS
                      and answer not in (CJK(seg["a"]), CJK(seg["b"])))
    # "Nothing printed here" answers a character one engine read out of a
    # bracket, a rule or a stroke.  Where both engines read ordinary text it
    # is no verdict - it would delete a printed character - and the fallback
    # engine's reading stands.
    empty_not_allowed = explicit_empty and bool(CJK(seg["a"]) and CJK(seg["b"])) and not numeral
    if empty_not_allowed:
        explicit_empty = False
    picked = "" if explicit_empty else (CJK(answer) if chosen and not empty_not_allowed else "")
    as_given, picked = picked, (simplified_as_read(picked, CJK(seg["a"]), CJK(seg["b"])) if simplified else picked)
    converted = picked != as_given
    # An answer longer than both readings by more than ANSWER_DELETION_LIMIT
    # characters is words of the reasoning, not a reading of the spot (the
    # measurement at ANSWER_DELETION_LIMIT): no verdict either.  A numeral
    # question's answer is tested as given, so the script it is written in
    # cannot make it invalid: 萬 neither engine read is written 万, which
    # NUMERAL_CHARS does not hold, and the fallback would write engine A's
    # misread with no doubt.
    longer = max(len(CJK(seg["a"])), len(CJK(seg["b"])))
    invalid = (empty_not_allowed or (bool(as_given) and numeral and not all(c in NUMERAL_CHARS for c in as_given))
               or len(picked) > longer + ANSWER_DELETION_LIMIT)
    agreed = seg.get("agreed") or []
    if picked and agreed and not all(part in picked for part in agreed):
        # The verdict drops digits both engines read: keep them. With a
        # single agreed run at one end, splice it back around the verdict;
        # otherwise the verdict is not usable and the fallback stands.
        lead = seg["a"].startswith(agreed[0]) and len(agreed) == 1
        tail = seg["a"].endswith(agreed[-1]) and len(agreed) == 1
        if lead and not picked.startswith(agreed[0]):
            picked = agreed[0] + picked
        elif tail and not picked.endswith(agreed[-1]):
            picked = picked + agreed[-1]
        elif not all(part in picked for part in agreed):
            picked, invalid = "", True
    # Decision D3 asks whether anything is printed where one engine read a
    # short run the other did not (insertionQuestion).  An answer that writes
    # some text there but less than that engine read says text is printed and
    # disputes only how much: a count of glyphs, which is what failed -
    # measured on one ink-blotted spot, identical crop in four runs, 裁決 kept
    # one of the two characters engine B read in three of them, each time
    # wrong (the render shows both half-covered glyphs), and none of the 20
    # D3 questions of the benchmark had a right shorter answer.  The engine
    # that read the text counted it off the image; its reading is written and
    # the count is left to a person (insertion-partial).  An empty answer
    # (nothing printed) and one as long as the engine's reading stand.
    inserted = CJK(seg["a"] or seg["b"]) if seg.get("insertionQuestion") else ""
    partial = (not invalid and not explicit_empty and bool(inserted) and bool(picked)
               and len(picked) < len(inserted))
    # Any question: an answer that is neither engine's reading, or says nothing
    # is printed, and leaves out more than ANSWER_DELETION_LIMIT characters of
    # the reading it is a view of (the one it shares most characters with, the
    # longer on a tie) deletes printed text on a partial view - a crop that
    # showed part of a column, a table header read in part.  That reading is
    # kept and a person looks (deletion-refused).  Not a numeral question, which
    # asks for 無 where a stroke or a bracket was read as a figure, nor a
    # reading the book's own furniture explains (furnitureSides: its running
    # head, which 裁決 rightly says is not printed there).  Decision D16
    # (the user's ruling of 2026-09-27).  A question with deletionGuard False
    # (one character no engine read, confirm_unread) has 裁決's answer written
    # as it is.
    kept_side = None
    written = "" if explicit_empty else picked
    if seg.get("deletionGuard", True) and not (partial or invalid or numeral) and (explicit_empty or (
            picked and picked not in (CJK(seg["a"]), CJK(seg["b"])))):
        side, reading = max(((side, CJK(seg[side])) for side in ("a", "b") if CJK(seg[side])),
                            key=lambda pair: (sum((Counter(pair[1]) & Counter(written)).values()), len(pair[1])),
                            default=(None, ""))
        if (side is not None and len(reading) - len(written) > ANSWER_DELETION_LIMIT
                and side not in (seg.get("furnitureSides") or ())):
            kept_side = side
    if partial:
        piece = seg["a"] or seg["b"]
    elif kept_side is not None:
        piece = seg[kept_side]
    elif invalid:
        # A numeral question answered with prose (measured: 廣東 from the
        # running foot written into a figure), or any answer far longer than
        # both readings. Not a verdict: fall back.
        picked = ""
        piece = (seg["b"] if fallback_engine == "B" else seg["a"]) or seg["a"] or seg["b"]
    elif explicit_empty:
        # Digits both engines read stay: the verdict only says the disputed
        # one is not printed.
        piece = "".join(agreed)
    else:
        piece = picked or seg["a"] or seg["b"]
    resolved_from = ("adjudicator-partial" if partial
                     else "adjudicator-deletion-refused" if kept_side is not None
                     else "adjudicator-invalid" if invalid
                     else "adjudicator-empty" if explicit_empty
                     else "adjudicator" if picked else "engine-A-default")
    record = {
        "trigger": ("numeral-disagreement" if numeral
                    else "rare-codepoint-disagreement" if rare else "engine-disagreement"),
        "context_before": seg["before"], "context_after": seg["after"],
        "draft_reading": seg["a"], "writer_reading": seg["b"],
        "shownFirst": "B" if swap else "A",
        "resolved": piece,
        "resolved_from": resolved_from,
        "winner": segment_winner(seg, piece, resolved_from),
        "needs_research": (match.group(1).lower() == "yes") if match else True,
        "research_classified": match is not None,
        "verdict": RESEARCH_LINE.sub("", verdict).strip(),
        # What 裁決 wrote where its answer was not used as written: in a book
        # printed in simplified script, in the form an engine read (the text
        # D16 measures, and writes where the reading stands nearby), with the
        # answer as given in answeredTraditional, which the doubts quote.
        **({"answered": written} if partial or kept_side is not None else {}),
        **({"answeredTraditional": as_given} if converted else {}),
    }
    return piece, record, metrics


def left_out_of(reading: str, answer: str) -> str:
    """The characters of READING an answer leaves out, in order (variant-folded
    alignment; the characters are READING's)."""
    out = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, variant_fold(reading), variant_fold(answer),
                                                     autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            out.append(reading[i1:i2])
    return "".join(out)


# How far from a spot, in characters of the merged text either side, a
# reading 裁決's answer leaves out may already stand for D16 to let the answer
# go (reading_nearby).  The second copy of text it let go on the benchmark
# replay stood one character from the spot; forty takes in a line or two of
# text around it.
NEARBY_WINDOW = 40


def reading_nearby(left_out: str, before: str, after: str, window: int = NEARBY_WINDOW) -> dict[str, Any] | None:
    """Where the characters LEFT_OUT already stand in the merged text around a
    spot (BEFORE and AFTER: the merged text either side of it), within WINDOW
    characters of it: all of them, in their order, close together - spread
    over at most twice their number of characters, others between.  The
    nearest such stretch, {side, distance, span, text}, or None - always None
    for a reading of numerals only.

    D16 keeps an engine's reading 裁決 answers away; measured on four runs, 10
    of its 26 refusals kept a second copy of text the page already had beside
    the spot: a table header of two-character cells engine A read row by row,
    interleaving the cells, where engine B read three of their characters by
    columns first (4 refusals), and figures engine B read a second time
    under the next row (6).  In order and close together, not merely present:
    a six-character price cell only engine B read must not be answered away
    because two of its characters stand in the next cell.

    Not figures.  Which of two copies of a number is the second one the page
    does not say: on the benchmark replay the one figure refusal the test let
    go (in two runs) took away the copy in its right cell and left the other,
    run onto the cell before it - one more table cell wrong in each run.  And
    figures stand around any place in a table by chance: every stretch of the
    replayed benchmark pages' merged text, looked for around its own place
    with no copy there, is found 27% of the time for three numerals and 7%
    for five in order within twice their span (1.1% and 0.4% for other
    text), and even the same figures in a row stand there 15% and 5% of the
    time (a table repeats its zeros).  A figure refusal stays refused, and a
    person looks (deletion-refused)."""
    want = variant_fold(CJK(left_out))
    if not want or all(c in NUMERAL_CHARS for c in want):
        return None
    best: dict[str, Any] | None = None
    for side, text in (("before", CJK(before)[-window:] if window else ""), ("after", CJK(after)[:window])):
        folded = variant_fold(text)
        for start in range(len(folded)):
            if folded[start] != want[0]:
                continue
            k, at = 0, start
            while at < len(folded) and k < len(want) and at - start < 2 * len(want):
                if folded[at] == want[k]:
                    k += 1
                at += 1
            if k < len(want):
                continue
            distance = len(folded) - at if side == "before" else start
            if best is None or distance < best["distance"]:
                best = {"side": side, "distance": distance, "span": at - start, "text": text[start:at]}
    return best


# How near a spot, in characters of the merged text either side, the text a
# 裁決 answer repeats may stand for --neighbour-guard to take the answer for a
# copy of it (neighbour_copy): 1 is the character right beside the spot.
NEIGHBOUR_WINDOW = 2
# The shortest one-engine reading that, written right beside its own copy,
# is doubted as a line read twice (beside_copies): shorter stretches repeat
# in print (個個, 年年, a table's zeros).  Also the shortest stretch read_twice
# settles as read twice: two measured double reads of 5 characters stay
# written twice.
BESIDE_COPY_MIN = 6


def read_twice(segments: Sequence[Mapping[str, Any]], blocks: Sequence[Mapping[str, Any]],
               block_ends: Sequence[int]) -> list[tuple[int, int]]:
    """The disagreements whose engine-A side is engine A reading again the
    text right beside it, each written once (engine-A-read-twice), with K,
    how many of its characters the copy has before it (the rest after): a
    stretch of BESIDE_COPY_MIN characters or more that engine A's own text
    holds again right before it, right after it, or split across both (A
    read P1 P1 P2 P2 where B read P1 P2: the stretch P1 P2), variant-folded,
    where engine A's layout (BLOCKS, BLOCK_ENDS: where each block's text
    ends in engine A's stream) says the same print was read twice: the
    stretch and its copy in one block, or in blocks whose boxes intersect.
    Not a stretch that is one unit repeated (哈哈…, 同上同上…, a run of 〇),
    nor one in a table block, where print repeats itself beside itself.

    Measured over the 4,241 pages of 13 books that have both engines'
    drafts, aligned as the merge aligns them, model off (2026-10-24; the 90
    order-conflict pages are not merged): 13 such copies of 7 to 116
    characters, each looked at on the page image, all a line or two printed
    once and read twice - a second layout box lying on the first or last
    line of another block (boxes overlapping by 34-70%), and once two
    paragraphs decoded twice inside one block.  9 of them stand on pages
    sealed earlier (six books): 3 were written whole (one by the block
    union, #34), 4 with a scrap of 裁決's answer beside the copy - shown the
    line printed once, it answers a piece of it - and 2 once (裁決 answered
    無).  The 11 exact copies printed twice (a chapter title in the running
    head and as the heading, a title printed on both leaves of a scan) all
    stand in blocks whose boxes do not meet.  Two more double reads, of 5
    characters, are shorter than BESIDE_COPY_MIN and go the usual way.
    Exact copies only: a near copy inside one block is parallel text (two
    entries of a list of offices that differ only in the office's name).
    Engine B's own repeats are not looked at: it has no layout to tell a
    line read twice from one printed twice."""
    fa = variant_fold("".join(x["a"] for x in segments))
    if not blocks or not block_ends or block_ends[-1] != len(fa):
        return []

    def spanned(lo: int, hi: int) -> range:
        """The blocks engine A's characters LO..HI-1 stand in."""
        return range(bisect.bisect_right(block_ends, lo), bisect.bisect_right(block_ends, hi - 1) + 1)

    def meet(j: int, k: int) -> bool:
        p, q = blocks[j].get("box"), blocks[k].get("box")
        return j == k or (bool(p) and bool(q) and min(p[0] + p[2], q[0] + q[2]) > max(p[0], q[0])
                          and min(p[1] + p[3], q[1] + q[3]) > max(p[1], q[1]))

    found: list[tuple[int, int]] = []
    at = 0
    for i, x in enumerate(segments):
        copy, n = variant_fold(x["a"]), len(x["a"])
        if (x["tag"] != "equal" and n >= BESIDE_COPY_MIN and (copy + copy).find(copy, 1) == n
                and not x.get(LOOPED_READING) and "settled" not in x and "window" not in x):
            # K characters of the copy before the stretch, the rest after it.
            k = next((k for k in range(n, -1, -1)
                      if fa[:at].endswith(copy[:k]) and fa.startswith(copy[k:], at + n)), None)
            if k is not None:
                here = [*spanned(at, at + n)]
                there = ([*spanned(at - k, at)] if k else []) + ([*spanned(at + n, at + 2 * n - k)] if k < n else [])
                if (all(blocks[j].get("label") != "table" for j in here + there)
                        and all(meet(j, t) for j in here for t in there)):
                    found.append((i, k))
        at += n
    return found


def neighbour_copy(answer: str, reading_a: str, reading_b: str, before: str, after: str,
                   shown_before: str | None = None, shown_after: str | None = None,
                   window: int = NEIGHBOUR_WINDOW, simplified: bool = False) -> dict[str, Any] | None:
    """Switch --neighbour-guard: whether 裁決's ANSWER at a spot, which is
    neither engine's reading there (READING_A, READING_B), is the text the
    merged text already has beside the spot (BEFORE and AFTER: the merged
    text either side of it) - the whole answer, standing WINDOW characters or
    fewer from the spot (distance 1: right beside it) - while neither
    engine's reading at the spot holds it.  Given SHOWN_BEFORE and
    SHOWN_AFTER (the context 裁決 was shown: engine A's text either side),
    the text must stand at the same place there too: a character of the
    merged text beside the spot that the context does not have there is
    another question's answer, which 裁決 never saw - measured on a folio,
    two answers that each read 二, two places apart, where the print has
    no 二; taken for a copy, the second would have given way to engine A's
    two characters of furniture.  Returns {side, distance, text} (the
    nearest such copy, "before" first on a tie), or None.  Variant-folded:
    an answer that is an engine's reading in another form is that reading;
    in a book printed in simplified script (SIMPLIFIED), in another script
    too (simplified_form): 裁決 often answers in traditional script there.
    Measured on four simplified volumes' sealed answers (2026-10-24): 241
    written as given hold a character only traditional script has; 5 of
    them are a copy of the text beside the spot in its simplified form and
    no copy as given, all 5 wrong, 3 where the fallback engine read right.

    What it assumes: an answer that repeats the text beside its spot, where
    neither engine read that text a second time, is 裁決 reading the
    neighbour - a crop that does not show the spot, or shows the neighbour
    beside a mark, a bracket, a folio or a rule an engine read as a
    character - not a character printed twice.  Evidence, not a law: where
    either engine read the answer at the spot too (the doubling read by an
    engine), or the answer stands farther away, it is not taken for a copy;
    a doubled character that both engines misread at its second place is
    the case this gets wrong (the engines' reading is then kept, with a
    doubt).  Measured on the sealed runs: of the 'neither engine' answers of
    one character, the share that repeats a character d places from the spot
    is several times the chance rate at d = 1 and 2 and near it from 3 on,
    and every answer this takes for a copy there, checked against a golden
    or opened on its crop and page, was wrong (the numbers are in the
    reference, long-book-workflow.md)."""
    fold = (lambda text: variant_fold(simplified_form(text))) if simplified else variant_fold
    got = fold(CJK(answer))
    if not got:
        return None
    for reading in (reading_a, reading_b):
        if got in fold(CJK(reading)):
            return None
    size = len(got)

    def ending(text: str, distance: int) -> str:
        """The SIZE characters of TEXT that end DISTANCE - 1 characters before its end."""
        end = len(text) - (distance - 1)
        return text[end - size:end] if end - size >= 0 else ""

    def starting(text: str, distance: int) -> str:
        return text[distance - 1:distance - 1 + size]

    left, right = CJK(before), CJK(after)
    for distance in range(1, window + 1):
        for side, text, shown, cut in (("before", left, shown_before, ending), ("after", right, shown_after, starting)):
            if fold(cut(text, distance)) != got:
                continue
            if shown is not None and fold(cut(CJK(shown), distance)) != got:
                continue
            return {"side": side, "distance": distance, "text": cut(text, distance)}
    return None


def confirm_nothing(client: "Client", crop: bytes | None, seg: Mapping[str, Any],
                    answer: tuple[str, dict[str, Any], dict[str, Any]], max_tokens: int,
                    fallback_engine: str = "A") -> tuple[str, dict[str, Any], dict[str, Any]]:
    """裁決's "nothing is printed here" (無), on a crop cut where the spot was
    estimated to be (SEG["coverage"]: a glyph or band crop), deletes what an
    engine read only when the crop is shown to hold the spot: the user's fix
    of 2026-09-27 (measured: two crops that did not show the spot, 無 both
    times, a character engine A read right deleted in two runs of two
    pages).

    The crop is transcribed blind (BLIND_SYSTEM: no candidates, no
    context) and the spot is looked for in the
    transcription between the characters engine A read around it
    (crop_shows_spot).  Shown, with what the answer writes read there -
    nothing, or on a numeral run the digits both engines read, which a 無
    keeps (adjudicate_segment) - the answer stands.  Otherwise - the context
    is not on the crop, or the transcription reads something else at the
    spot - the reading is kept (the engine's the
    transcription has there, else the fallback engine's where both engines
    read one) and the page gets a doubt
    (adjudicator-empty-unconfirmed; coverageRead holds what was read).  Not
    for a reading the book's furniture explains (furnitureSides: its running
    head, which 裁決 rightly says is not printed there, may go on any crop),
    nor for a crop of a whole block, table or page, or of both edges of a
    block boundary, which hold the spot by how they are cut.
    """
    piece, record, metrics = answer
    coverage = seg.get("coverage")
    if not coverage or record.get("resolved_from") != "adjudicator-empty":
        return answer
    furniture = set(seg.get("furnitureSides") or ())
    deleted = [side for side in ("a", "b")
               if CJK(seg[side]) and side not in furniture and CJK(seg[side]) != CJK(piece)]
    if not deleted:
        return answer
    transcript, used = client.ask_answering(BLIND_SYSTEM, BLIND_USER, crop, max_tokens, kind="coverage-read")
    metrics = dict(metrics)
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
        metrics[key] = (metrics.get(key) or 0) + (used.get(key) or 0)
    shown, reading, how = crop_shows_spot(transcript or "", seg, coverage["beforeRoom"], coverage["afterRoom"])
    # What the answer writes, not nothing: a 無 on a numeral run keeps the
    # digits both engines read, and a crop that shows them there shows the
    # answer right (on the round-2 scan's pages, emulated: a numeral both
    # engines read, engine A with the circle mark after it as 〇, 無 answered
    # on a crop showing the numeral alone - taken as unconfirmed, it wrote the
    # same numeral with a doubt and a textLoss entry that changed nothing).
    # A transcription that writes the circle there as 〇 (the prompt says to
    # leave marks out) is left unconfirmed: a withheld name (張〇〇) and a
    # figure (五〇) print the same glyph, which the blind read cannot tell
    # from a mark.
    confirmed = shown and reading == variant_fold(CJK(piece))
    record = dict(record, coverageRead={"crop": coverage["kind"], "found": how, "reading": reading,
                                        "confirmed": confirmed, "transcript": (transcript or "").strip()[-600:]})
    if confirmed:
        return piece, record, metrics
    # The engine whose reading the blind read has at the spot, else the
    # fallback engine's where both engines read one.
    matched = [seg[side] for side in ("a", "b") if CJK(seg[side]) and variant_fold(CJK(seg[side])) == reading]
    kept = (matched[0] if matched else
            (seg["b"] if fallback_engine == "B" else seg["a"]) or seg["a"] or seg["b"])
    record.update(resolved=kept, resolved_from="adjudicator-empty-unconfirmed", winner="undecided", answered=piece)
    return kept, record, metrics


# How many of the characters around a spot, in a row, a transcription must
# have on each side for the spot to be on the crop (crop_shows_spot).
SPOT_ANCHOR = 2


def crop_shows_spot(transcript: str, seg: Mapping[str, Any], before_room: int = 8,
                    after_room: int = 8) -> tuple[bool, str, str]:
    """Whether a transcription of a crop shows a disagreement's spot, what it
    reads there, and how it was found: (shown, reading, how).

    Shown means the characters engine A read around the spot are in the
    transcription on each side: BEFORE_ROOM is how many of its context
    characters before the spot are printed in the spot's own layout block,
    AFTER_ROOM the same after it.  A side with room is anchored by SPOT_ANCHOR
    of those characters (fewer where the room is less) in a row, in a row in
    the transcription too (variant-folded): one common character (之, 國) is
    found on any crop, two in a row were not on the crops measured.  A side
    with no room - the spot begins or ends its block - is anchored by
    SPOT_ANCHOR in a row of the neighbouring block's text there, when the
    crop shows it, and is not shown otherwise: a transcription that begins
    with the characters after the spot is the same whether the crop shows
    the spot empty or begins a character below it (the round-3 review; the
    measured miss was of this kind, mid-block), and the spot at the page's
    first or last character has no neighbour to show.  The context
    characters between the nearest anchor and the spot count as read there,
    misread or not.  How: "placed", "before-unshown", "after-unshown",
    "unanchored" or "misplaced" (anchored on both sides, the readings do not
    fit between).

    Measured on the round-2 runs: 裁決 answered 無 for a character one engine
    read on crops that did not show it - one began a character below the
    spot, at the head of the next column (the column fit put the spot one
    character late); the other was a column two columns away, where the
    block held a fold strip and a column engine A never read - and a
    character engine A had read right was deleted, in both runs of both
    pages.
    """
    t = variant_fold(CJK(transcript))
    before, after = variant_fold(CJK(seg.get("before") or "")), variant_fold(CJK(seg.get("after") or ""))
    # A side with room: its own block's characters; with none, the
    # neighbouring block's, SPOT_ANCHOR of them in a row.
    if before_room > 0:
        before = before[len(before) - min(len(before), before_room):]
    if after_room > 0:
        after = after[:after_room]
    need_before = min(SPOT_ANCHOR, len(before)) if before_room > 0 else SPOT_ANCHOR
    need_after = min(SPOT_ANCHOR, len(after)) if after_room > 0 else SPOT_ANCHOR
    readings = [variant_fold(CJK(seg["a"])), variant_fold(CJK(seg["b"]))]
    best: tuple[bool, int, str, str] | None = None
    for middle in dict.fromkeys(readings):
        ref = before + middle + after
        lo, hi = len(before), len(before) + len(middle)
        start = end = None
        anchored = 0
        for m in difflib.SequenceMatcher(None, ref, t, autojunk=False).get_matching_blocks():
            if not m.size:
                continue
            # The part of a matching run inside each side's context.
            b_lo, b_hi = m.a, min(m.a + m.size, lo)
            if b_hi > b_lo and b_hi - b_lo >= need_before:
                start = m.b + (b_hi - m.a) + (lo - b_hi)
                anchored += b_hi - b_lo
            a_lo, a_hi = max(m.a, hi), m.a + m.size
            if end is None and a_hi > a_lo and a_hi - a_lo >= need_after:
                end = m.b + (a_lo - m.a) - (a_lo - hi)
                anchored += a_hi - a_lo
        left, right = start is not None, end is not None
        if not (left and right):
            how = ("unanchored" if not (left or right) else "before-unshown" if not left else "after-unshown")
            found = (False, anchored, "", how)
        else:
            span = max(map(len, readings))
            fits = 0 <= start <= end <= len(t) and end - start <= span + 3
            found = (fits, anchored, t[start:end] if fits else "", "placed" if fits else "misplaced")
        if best is None or found[:2] > best[:2]:
            best = found
    assert best is not None
    return best[0], best[2], best[3]


def judged_misreads(asked: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, tuple[str, int]]:
    """Engine A's characters the page's verdicts judged misread: for each
    (segment asked, sealed verdict) of the page, the disputed characters of a
    like-for-like substitution (both engines read as many characters, the
    verdict as many) that 裁決 replaced; returned as
    {engine A's character: (the character written instead, sites)} for a
    character replaced at every disputed site of the page, always by the same
    one.  A numeral is no glyph question (a stroke or a bracket read as 一),
    and a verdict that kept engine A's character anywhere on the page says it
    is read right there."""
    replaced: dict[str, set[str]] = {}
    count: Counter = Counter()
    kept: Counter = Counter()
    for seg, record in asked:
        a, b, got = CJK(seg["a"]), CJK(seg["b"]), CJK(str(record.get("resolved") or ""))
        if (not a or len(a) != len(b) or len(got) != len(a)
                or record.get("resolved_from") != "adjudicator"):
            continue
        for x, y, z in zip(a, b, got):
            if x == y or x in NUMERAL_CHARS:
                continue
            if z == x:
                kept[x] += 1
            else:
                replaced.setdefault(x, set()).add(z)
                count[x] += 1
    return {x: (next(iter(zs)), count[x]) for x, zs in replaced.items() if len(zs) == 1 and not kept[x]}


def propagation_spots(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str], union_of: Collection[int],
                      asked_slots: Collection[int], misread: Mapping[str, tuple[str, int]]
                      ) -> list[tuple[int, int, dict[str, Any]]]:
    """The characters decision D24 asks about: (segment, position,
    the question) for each character of a reading only engine A made, written
    as engine A read it, that judged_misreads found misread on the page.

    A reading already put to 裁決 (ASKED_SLOTS: an insertion question, or an
    answer the deletion guard or D3's partial rule refused, which keeps the
    engine's reading) is left alone: 裁決 has answered for that text, and a
    character written into it would make a mix no verdict wrote, while the
    seal says the engine's reading was kept."""
    spots: list[tuple[int, int, dict[str, Any]]] = []
    for i, x in enumerate(segments):
        only_a = (x["a"] and not x["b"] and pieces[i] == x["a"] and not x.get(LOOPED_READING)
                  and i not in asked_slots
                  and ((x["tag"] == "delete" and "settled" not in x and i not in union_of)
                       or (x["tag"] == "equal" and "window" in x)))
        if not only_a:
            continue
        for p, char in enumerate(x["a"]):
            if char in misread:
                sub = {"tag": "replace", "a": char, "b": misread[char][0],
                       "before": (x["before"] + x["a"][:p])[-8:], "after": (x["a"][p + 1:] + x["after"])[:8],
                       **({"window": x["window"]} if "window" in x else {})}
                spots.append((i, p, sub))
    return spots


def propagation_note(x: str, z: str) -> str:
    """What 裁決 is told when asked about a character only engine A read
    because the page's verdicts judged engine A's same character a misread
    (decision D24): the second reading is not another engine's."""
    return (f"呢個位淨係一個引擎讀到。乙唔係第二個引擎嘅讀法：同一頁其他位置，對住裁圖判定過呢個引擎嘅「{x}」"
            f"係「{z}」讀錯。呢度係咪一樣，照圖定。")


def locate_block(blocks: list[dict[str, Any]], seg: dict[str, Any]) -> dict[str, Any] | None:
    """Find the layout block a disagreement sits in, by the text around it.

    Side A alone cannot locate an insertion (A is empty there), so the search
    uses the six characters before and after as well.
    """
    # After-context first: a list marker or heading number opens its block, so
    # the text before it belongs to the previous column's block (measured: three
    # of four markers cropped from the wrong block when before-context led).
    probes = [seg["after"][:6], seg["a"], seg["b"], seg["before"][-6:]]
    for probe in probes:
        key = norm_for_diff(probe)[:6] if probe else ""
        if len(key) < 2:
            continue
        for block in blocks:
            if key in norm_for_diff(str(block["text"])):
                return block
    return None


class ProofreadError(RuntimeError):
    """The phase cannot proceed."""


@dataclass(frozen=True)
class PageJob:
    scan_page: int
    render: Path
    draft_text: Path
    draft_json: Path | None
    draft2_text: Path | None
    out_json: Path
    out_md: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
    ).encode("utf-8")


def strip_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\A```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```\Z", "", text)
    return text.strip()


def norm_for_diff(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"^[#\-\s*]+", "", text, flags=re.M)
    return "".join(c for c in text if c not in PUNCT and not c.isspace())


# The vision server refuses an image whose decoded size is above its pixel budget
# (ninfer: 64 Mi pixels, HTTP 400 media_budget_exceeded) and the page is lost.
# Measured: a book scanned at 77-83 MP per page lost every page.  The model sees at
# most a few tens of megapixels anyway (its vision tokens are capped), so an image
# above the budget is scaled down to fit; every other image is sent unchanged.
MEDIA_PIXEL_LIMIT = 60_000_000


def fit_media_budget(image_bytes: bytes, limit: int = MEDIA_PIXEL_LIMIT) -> bytes:
    """The image as sent: unchanged within the budget, else scaled down (aspect kept) to fit."""
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        if width * height <= limit:
            return image_bytes
        scale = math.sqrt(limit / (width * height))
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        small = image.convert("RGB" if image.mode not in ("1", "L", "RGB") else image.mode).resize(size, Image.LANCZOS)
    out = io.BytesIO()
    small.save(out, "PNG")
    return out.getvalue()


# The vision server also refuses an image whose long side is more than 200 times
# its short side (ninfer's image processor: HTTP 400 invalid_prompt, "image
# aspect ratio must be at most 200"), and the page is lost with it.  Measured: on
# a 488-page book one page was lost to a 裁決 crop 4 px wide and 1,875 high (a
# strip beside a column, measure_columns), enlarged to 16 x 7,500 (upscale_crop).
# Such an image is padded on its short side with its own paper (its modal
# colour, as boundary_crop fills its gap), centred, to MEDIA_ASPECT_LIMIT - half
# the server's limit, for margin.  It is never scaled: shrinking the long side
# shrinks the text on it.  Every other image is sent as given, byte for byte.
MEDIA_ASPECT_LIMIT = 100


def fit_media_aspect(image_bytes: bytes, limit: float = MEDIA_ASPECT_LIMIT) -> bytes:
    """The image as sent: unchanged within LIMIT, else its short side padded
    with its own paper, the image centred, to LIMIT."""
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        if max(width, height) <= limit * min(width, height):
            return image_bytes
        source = image.convert("L" if image.mode in ("1", "L") else "RGB")
    histogram = source.histogram()
    paper = tuple(max(range(256), key=lambda level: histogram[256 * band + level])
                  for band in range(len(source.getbands())))
    side = math.ceil(max(width, height) / limit)
    size = (side, height) if width < height else (width, side)
    padded = Image.new(source.mode, size, paper if len(paper) > 1 else paper[0])
    padded.paste(source, ((size[0] - width) // 2, (size[1] - height) // 2))
    out = io.BytesIO()
    padded.save(out, "PNG")
    return out.getvalue()


class Client:
    """Minimal chat client. Retries only on transport errors, never on HTTP 4xx."""

    def __init__(self, endpoint: str, model: str, effort: str | None, timeout: float,
                 temperature: float = 0.0, max_inflight: int | None = None,
                 loop_detector: _loop_watch.Rule | None = None):
        self.endpoint = endpoint
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self.temperature = temperature
        # Requests on the wire at once, across every page and every page's
        # worker threads.  The server queues what it cannot admit and answers
        # 503 after its pending timeout, which this client treats as fatal, so
        # the queue is kept here instead, where waiting costs nothing.
        self.inflight = threading.BoundedSemaphore(max_inflight) if max_inflight else None
        # Switch --loop-detector: every request is streamed and its reasoning
        # watched for a word-for-word loop by this rule (_loop_watch); None:
        # off, and requests are sent unstreamed.
        self.loop_detector: _loop_watch.Rule | None = loop_detector

    def request_body(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
    ) -> dict[str, Any]:
        """The JSON body of one request, as ask() sends it."""
        return self._request(system, user_text, image_bytes, max_tokens, effort_override)[0]

    def _request(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
    ) -> tuple[dict[str, Any], bool]:
        """The JSON body of one request, as ask() sends it (request_body), and
        whether its image was padded to the server's aspect ratio
        (fit_media_aspect), which the call ledger records."""
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        padded = False
        if image_bytes is not None:
            # Padded first, then scaled to the budget, which keeps the aspect
            # within a pixel: padding after scaling could take it over the budget.
            shaped = fit_media_aspect(image_bytes)
            padded = shaped is not image_bytes
            b64 = base64.b64encode(fit_media_budget(shaped)).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Greedy by default.  Two reasons, and neither is about quality.
            # Transcription wants the most likely reading, not a sample from the
            # distribution.  And without it nothing here is reproducible: an A/B
            # over six pages returned exactly opposite results on two runs of
            # the SAME code, and the sealed SHA-256 provenance could never be
            # checked by re-running.  Measured: greedy gives byte-identical
            # output three times out of three, default sampling three different
            # answers out of three.
            "temperature": self.temperature,
            "top_p": 1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        effort = self.effort if effort_override == "" else effort_override
        if effort:
            body["reasoning_effort"] = effort
        if self.loop_detector is not None:
            # Streamed, so the reasoning can be watched as it comes and the
            # request given up in a loop (_read_stream): the usage at the end
            # (stream_options), and the server's own count of the tokens
            # generated on every event (timings_per_token, as llama.cpp's
            # server and ninfer take it), so a request given up says what
            # it spent.
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
            body["timings_per_token"] = True
        return body, padded

    def ask(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
        kind: str = "other",
    ) -> tuple[str, dict[str, Any]]:
        """One request.

        With the loop detector on (--loop-detector) the request is streamed
        and given up in a word-for-word loop (_read_stream): it comes back as
        a cut at max_tokens (finish_reason "length"), its metrics say loopCut
        (what was found), and the ledger marks every call watched
        (loopWatched) and a call given up (loopCut) with what it left of its
        max_tokens (loopCutTokensSaved)."""
        body, padded = self._request(system, user_text, image_bytes, max_tokens, effort_override)
        effort = self.effort if effort_override == "" else effort_override
        started = time.monotonic()
        payload, waited = self._send(body, kind)
        usage = payload.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        timings = payload.get("timings") or {}
        choice = payload["choices"][0]
        loop = payload.get("loopCut")
        metrics = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": details.get("reasoning_tokens", 0),
            "finish_reason": choice.get("finish_reason"),
            "effort": effort,
            **({"loopCut": loop} if loop else {}),
        }
        ledger = _CALL_LEDGER.get()
        if ledger is not None:
            # Wall time from the caller's side; the server's own split (prompt,
            # of which cache_n tokens were reused, then decode) comes from the
            # llama.cpp-style `timings` ninfer adds to every response.  Wall
            # minus both is time spent waiting to be admitted.
            ledger.append({
                "kind": kind, "maxTokens": max_tokens,
                "seconds": round(time.monotonic() - started - waited, 2),
                "waitSeconds": round(waited, 2),
                "serverPromptMs": timings.get("prompt_ms", 0.0) or 0.0,
                "serverDecodeMs": timings.get("predicted_ms", 0.0) or 0.0,
                "cached_tokens": timings.get("cache_n", 0) or 0,
                "padded": padded,
                **metrics,
                # What a loop cut left of the rung's max_tokens: the saving
                # where the loop would have gone on to max_tokens, as 17 of
                # the 19 loops in the measured replays did; the other two
                # ended on their own with no answer (_loop_watch).
                **({"loopWatched": True,
                    **({"loopCutTokensSaved": max(0, max_tokens - (metrics["completion_tokens"] or 0))}
                       if loop else {})}
                   if self.loop_detector is not None else {}),
            })
        return choice["message"].get("content") or "", metrics

    def _send(self, body: dict[str, Any], kind: str) -> tuple[dict[str, Any], float]:
        """POST BODY to the endpoint: the response payload, and the seconds
        spent waiting for a free lane (the in-flight cap).  KIND is the call's
        kind, which the endpoint is not sent; a stand-in model in the checks
        answers by it."""
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("PROOFREAD_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload_body = dict(body)
        request = urllib.request.Request(
            self.endpoint, json.dumps(payload_body).encode(), headers
        )
        last: Exception | None = None
        waited = 0.0
        for attempt in range(3):
            try:
                queued = time.monotonic()
                if self.inflight:
                    self.inflight.acquire()
                waited += time.monotonic() - queued
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        # A streamed request (--loop-detector) is read as one payload.
                        payload = (self._read_stream(response) if payload_body.get("stream")
                                   else json.loads(response.read().decode()))
                        return payload, waited
                finally:
                    if self.inflight:
                        self.inflight.release()
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace")[:300]
                if error.code == 400 and "reasoning_effort" in detail and "reasoning_effort" in payload_body:
                    payload_body.pop("reasoning_effort", None)
                    request = urllib.request.Request(
                        self.endpoint, json.dumps(payload_body).encode(), headers
                    )
                    continue
                # A 4xx is a contract problem; retrying sends the same bad request.
                raise ProofreadError(f"HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                last = error
                time.sleep(2 * (attempt + 1))
        raise ProofreadError(f"endpoint unreachable after 3 attempts: {last}")

    def _read_stream(self, response: Any) -> dict[str, Any]:
        """A streamed response (switch --loop-detector) as the one payload the
        endpoint answers unstreamed: the reasoning and the answer joined from
        their deltas, the finish reason, and the usage and timings of the last
        events that carried them.  RESPONSE gives the stream's lines (the SSE
        of an OpenAI-compatible endpoint) and has close().

        The reasoning is watched as it comes, the answer never
        (_loop_watch.LoopWatch).  When the reasoning ends in a word-for-word
        loop the connection is closed there - the server stops generating
        when it finds its client gone - and the payload is that of a request
        cut off at its max_tokens: finish_reason "length", no answer, the
        tokens generated so far (the server's own count on the last event,
        timings_per_token; else one per delta, fewer than it generated where
        a delta held several) as completion and reasoning tokens, and
        loopCut: what was found (_loop_watch.loop_at), cutTokens and
        tokensCountedBy ("server" or "deltas").

        A stream that ends before the model finished (no finish reason) is a
        transport failure, retried as a dropped connection is; so is an event
        that cannot be read (not UTF-8, not JSON, not an object): it may hold a
        piece of the answer, and an answer read without it would have lost
        that piece, so the whole request is asked again.  What that assumes:
        one event per data line, as ninfer and llama.cpp's server write them;
        a data line with nothing after "data:" holds no delta and is passed
        over, as are comments (": keep-alive") and blank lines.  A stream
        that finished but ended before its usage event keeps its answer, and
        its tokens are the server's live count on the last event that had one
        (timings_per_token; none without it).  An error event from the server
        is a ProofreadError, as an HTTP error is, and so is a JSON body where
        a stream was asked for (a server that does not stream: asking again
        would generate the whole answer again)."""
        watch = _loop_watch.LoopWatch(self.loop_detector or _loop_watch.RULE)
        reasoning: list[str] = []
        content: list[str] = []
        head: dict[str, Any] = {}
        finish = usage = timings = found = None
        deltas = 0
        for raw in response:
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError as error:
                raise ConnectionError(f"an event of the stream is not UTF-8 ({error})") from error
            if line.startswith("{"):
                raise ProofreadError("--loop-detector: the endpoint answered a streamed request with a JSON "
                                     "body, not a stream; it needs a server that streams")
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            if not data:
                continue
            try:
                event = json.loads(data)
            except ValueError as error:
                raise ConnectionError(f"an event of the stream is not JSON ({error}): {data[:80]!r}") from error
            if not isinstance(event, dict):
                raise ConnectionError(f"an event of the stream is not a JSON object: {data[:80]!r}")
            if event.get("error") is not None:
                raise ProofreadError(f"stream error: {json.dumps(event['error'], ensure_ascii=False)[:300]}")
            for key in ("id", "created", "model"):
                if key in event:
                    head.setdefault(key, event[key])
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if thought:
                    reasoning.append(thought)
                    deltas += 1
                    if not content:
                        found = watch.feed(thought)
                if delta.get("content"):
                    content.append(delta["content"])
                    deltas += 1
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            if found is not None:
                response.close()
                break
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        payload: dict[str, Any] = {**head, "object": "chat.completion"}
        if found is not None:
            counted = (timings or {}).get("predicted_n")
            tokens = counted if isinstance(counted, int) and counted > 0 else deltas
            prompt = ((timings.get("prompt_n") or 0) + (timings.get("cache_n") or 0)) if timings else 0
            payload.update({
                "choices": [{"index": 0, "message": message, "finish_reason": "length"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": tokens,
                          "completion_tokens_details": {"reasoning_tokens": tokens}},
                **({"timings": timings} if timings else {}),
                "loopCut": {**found, "cutTokens": tokens,
                            "tokensCountedBy": "server" if tokens == counted else "deltas"}})
            return payload
        if finish is None:
            raise ConnectionError("the stream ended before the model finished")
        if usage is None and timings and isinstance(timings.get("predicted_n"), int):
            # Finished, but the stream ended before its usage event: the
            # server's live count on the last event that had one.
            usage = {"prompt_tokens": (timings.get("prompt_n") or 0) + (timings.get("cache_n") or 0),
                     "completion_tokens": timings["predicted_n"]}
        payload.update({"choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": usage or {}, **({"timings": timings} if timings else {})})
        return payload

    # Effort ladder used to recover a page that thought past its token budget.
    # Only reasoning_effort works per-request on an OpenAI-compatible endpoint;
    # an Anthropic-style thinking budget is ignored there (verified).
    EFFORT_LADDER = ["xhigh", "medium", "low", None]

    def ask_answering(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        kind: str = "other",
        start: str | None = "",
    ) -> tuple[str, dict[str, Any]]:
        """ask(), but guarantee the model actually answers.

        With a high reasoning effort a request can spend its whole max_tokens on
        reasoning and return empty content with finish_reason "length" - observed
        losing a page outright in a production run. Retrying the same request is
        pointless; it fails the same way. So each retry steps DOWN the effort
        ladder, which shifts budget from reasoning to the answer. The last rung
        disables reasoning entirely, where an empty answer is a real failure
        rather than a budget artifact.

        START names the rung to begin on instead of the client's effort ("",
        the default): a retry of a call that answered nothing (ask_lower).  It
        only ever lowers the effort: a client set below START begins at its
        own rung.

        Switch --loop-detector (loop_detector): a rung given up in a
        word-for-word loop (_read_stream) is a cut at max_tokens - the ladder
        goes on to the next rung, the rung is asked and cut off (rungsAsked,
        rungsCutOff).  The answer's metrics count the rungs given up
        (loopCuts) and the page's decisionChanges hold the call
        (loop_cut_changes).
        """
        try:
            own = self.EFFORT_LADDER.index(self.effort)
        except ValueError:
            own = 0
        try:
            start = own if start == "" else max(own, self.EFFORT_LADDER.index(start))
        except ValueError:
            start = own
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        best: tuple[str, dict[str, Any]] | None = None
        asked = cut = 0
        # The rungs given up in a loop (--loop-detector), in the order asked.
        loops: list[dict[str, Any]] = []

        def noted(text: str, m: dict[str, Any], rung_tokens: int | None) -> tuple[str, dict[str, Any]]:
            if not loops:
                return text, m
            degraded = bool(m.get("degraded"))
            call = {
                "kind": kind, "effort": self.EFFORT_LADDER[own] or "none", "maxTokens": max_tokens,
                "answeredAt": None if degraded else (m.get("effort") or "none"),
                "answerTokens": None if degraded else rung_tokens,
                "rungsAsked": m["rungsAsked"] if degraded else asked + 1,
                "degraded": degraded,
                "questionSha256": hashlib.sha256(
                    system.encode("utf-8") + b"\0" + user_text.encode("utf-8") + b"\0"
                    + (image_bytes or b"")).hexdigest(),
                "answer": text,
                # Where on the page the question is, when its caller says (call_site).
                "site": _CALL_SITE.get(),
            }
            m["loopCuts"] = len(loops)
            notes = _LOOP_CUTS.get()
            if notes is not None:
                notes.append({**call, "loops": loops})
            return text, m

        for rung in self.EFFORT_LADDER[start:]:
            text, m = self.ask(system, user_text, image_bytes, max_tokens, effort_override=rung, kind=kind)
            for k in totals:
                totals[k] += m.get(k, 0)
            truncated = m.get("finish_reason") == "length"
            if m.get("loopCut"):
                # Given up in a loop (--loop-detector): the next rung, as after
                # a cut at max_tokens.
                loops.append({"effort": rung or "none", "maxTokens": max_tokens, **m["loopCut"]})
            if text.strip() and not truncated:
                rung_tokens = m.get("completion_tokens", 0)
                m.update(totals)
                return noted(text, m, rung_tokens)   # complete answer - done
            asked, cut = asked + 1, cut + truncated
            # Keep the longest partial seen, in case every rung truncates.
            if best is None or len(text.strip()) > len(best[0].strip()):
                best = (text, dict(m))
            if not truncated:
                break  # empty for some other reason; stepping down will not help
        text, m = best if best else ("", {})
        m.update(totals)
        m["degraded"] = True           # never silently pass off a truncated page
        # How the ladder ended, for no_answer: the rungs asked and how many of
        # them were cut off at the token budget.  An empty reply that finished
        # on its own is degraded too, after one rung, cut off at none.
        m["rungsAsked"], m["rungsCutOff"] = asked, cut
        return noted(text, m, None)


# What of a call's site (call_site) says where it is, as every decisionChanges
# entry says it (loop_cut_changes); the rest of the site - the crop, a pass's
# range and tile - goes with the evidence.
SITE_PLACE_KEYS = ("block", "contextBefore", "contextAfter", "engineA", "engineB", "mergedOffset")


def loop_cut_changes(notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The decisionChanges entries of switch --loop-detector: one per call of
    the page with a rung the loop detector gave up in a word-for-word loop of
    its reasoning (Client.ask_answering, _loop_watch).

    Where: SITE_PLACE_KEYS from the call's call_site (the rest of the site
    - crop, range, tile - in the evidence), and questionSha256 tells a call
    with no place from the page's others.  "after" is the answer the ladder
    gave (the longest partial when no rung answered), "afterBy" the rung
    that gave it.  The evidence holds each rung given up: its effort,
    max_tokens, the tokens spent when given up (cutTokens; tokensCountedBy
    says whose count), and the loop found (period, copies, repeatedChars,
    repeatsFrom, atChar: characters of the reasoning).  In order of kind,
    then place, so the order does not follow the threads' timing.
    questionSha256 is the SHA-256 of the question (system prompt, text and
    image): the seal holds no prompt, so it can only be matched against the
    same question asked again (a replay)."""
    def site(note: Mapping[str, Any]) -> Mapping[str, Any]:
        return note.get("site") or {}

    return [{"decision": "loopDetector", "rule": "reasoning-loop", "kind": note["kind"],
             **{key: site(note)[key] for key in SITE_PLACE_KEYS if key in site(note)},
             "after": note["answer"], "afterBy": note["answeredAt"],
             "evidence": {"loops": note["loops"],
                          **{key: note[key] for key in ("maxTokens", "answerTokens", "rungsAsked", "degraded",
                                                        "questionSha256")},
                          **{key: value for key, value in site(note).items() if key not in SITE_PLACE_KEYS}}}
            for note in sorted(notes, key=lambda n: (n["kind"], site(n).get("mergedOffset") is None,
                                                     site(n).get("mergedOffset") or 0, n["questionSha256"],
                                                     n["answer"]))]


def ask_lower(client: "Client", effort: str | None, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """client.ask_answering with its effort ladder begun at EFFORT ("" is
    the client's own); a client with no ladder (a stand-in model) is asked
    as it always is."""
    try:
        takes = "start" in inspect.signature(client.ask_answering).parameters
    except (TypeError, ValueError):
        takes = False
    return client.ask_answering(*args, **kwargs, **({"start": effort} if takes else {}))


def no_answer(text: str, metrics: Mapping[str, Any]) -> str | None:
    """Why a structure pass's answer is no answer, or None when it is one:
    nothing written, or cut off at the token budget on every rung of the
    effort ladder (ask_answering's "degraded": the longest partial comes
    back).  Measured on a page whose engine grid was refused, so the
    structure pass had to write the table: all four rungs used their whole
    budget reasoning, the answer was empty, and an empty answer invents no
    character, deletes no run of prose (the page has none) and changes no
    mark - so it passed every check, and the page was sealed as flat text
    with "STRUCTURE PASS REJECTED: characters preserved".

    The reason says which of the two happened, from the rungs ask_answering
    counted: a reply that finished on its own with nothing in it is
    degraded as well, after its first rung, and is no cut.  Measured: a
    title page's structure pass reasoned 2,237 tokens at the top effort and
    answered a bare line break, finish reason "stop", in two runs of four
    (the other two answered at the same effort).  A client that counts no
    rungs (a stand-in) is judged by the answer's own finish reason, and a
    partial answer, which only a cut leaves.  Rungs the loop detector gave
    up (--loop-detector, loopCuts) are cuts, and the reason says how many."""
    if not metrics.get("degraded"):
        return None if text.strip() else "no answer: the reply was empty"
    written = len(CJK(text))
    asked, cut = metrics.get("rungsAsked"), metrics.get("rungsCutOff")
    if cut is None:
        asked = cut = int(metrics.get("finish_reason") == "length" or bool(text.strip()))
    if not cut:
        return "no answer: the reply was empty"
    tail = f" ({written} character(s) written before the cut)" if written else ", nothing written"
    loops = (f" ({metrics['loopCuts']} of them given up in a word-for-word loop)"
             if metrics.get("loopCuts") else "")
    if cut < asked:
        return (f"no answer: cut off at the token budget at {cut} effort(s){loops}, "
                f"then an empty reply{tail}")
    return "no answer: cut off at the token budget at every effort" + loops + tail


def no_answer_cut(reason: str | None) -> bool:
    """Whether no_answer's REASON is a cut at the token budget (the retry
    then begins lower and says it thought too long), not an empty reply."""
    return bool(reason) and "cut off at the token budget" in reason


# The rung a structure pass that answered nothing is asked again from: the
# budget goes to the answer, not the reasoning.  Measured on that page: the
# same prompt answered at medium in three runs of four, and in the fourth
# every rung was cut off.
NO_ANSWER_RETRY_EFFORT = "low"


TABLE_MINIMUM_ROWS = 2
# How long a stretch of a table cell must be before engine B's reading of the
# page is allowed to testify about it.  Shorter matches land by coincidence.
# Measured by treating each of the 1,482 non-table blocks of the five
# calibration books as one long cell: 6 come out "read in another order" with
# 4-character pieces, 3 with 8.  The ones lost on the way are two heads that
# each engine read at a different place on the page and a 4-character
# coincidence in a name list; the three left are real order disagreements (two
# rosters printed as unruled grids, and the one table the detector missed,
# which it read column-interleaved).  On the 45 engine tables that have an
# engine-B page the whole of witness_refusal refuses the same two garbled
# tables at 8 and at 10; shorter, the testimony check starts refusing real
# tables of figures - cells of 12 or 8 characters that engine B read with a
# digit different here and there hold no piece that long - and 6 tables are
# refused in all at 6, 15 at 4.  8 is the shortest length that refuses no
# real table.
WITNESS_ANCHOR = 8


def witness_anchors(cell: str, witness: str,
                    matcher: difflib.SequenceMatcher | None = None,
                    unique: bool = True) -> list[tuple[int, int, int]]:
    """Where the pieces of one cell sit in another engine's reading of the page.

    Each piece is the longest stretch of the cell that the other reading also
    has, at least WITNESS_ANCHOR long.  With `unique` it is kept only when it
    occurs there exactly once - a stretch read twice cannot say where the cell
    is; without, every piece counts, which is the question "did the other
    engine read this text at all".  Returns (cell offset, witness offset,
    length) in cell order.
    """
    if matcher is None:
        matcher = difflib.SequenceMatcher(None, "", witness, autojunk=False)
    found: list[tuple[int, int, int]] = []
    pending = [(0, len(cell))]
    while pending:
        lo, hi = pending.pop()
        if hi - lo < WITNESS_ANCHOR:
            continue
        matcher.set_seq1(cell[lo:hi])
        match = matcher.find_longest_match(0, hi - lo, 0, len(witness))
        if match.size < WITNESS_ANCHOR:
            continue
        piece = cell[lo + match.a:lo + match.a + match.size]
        if not unique or witness.count(piece) == 1:
            found.append((lo + match.a, match.b, match.size))
        pending += [(lo, lo + match.a), (lo + match.a + match.size, hi)]
    return sorted(found)


def witness_refusal(table: "_tables.Table", witness: str | None) -> str | None:
    """Why the other engine's reading of the page will not vouch for this grid.

    A cell is one printed box, so its text is one stretch of the page, and an
    independent reading of the page meets that stretch once, in one order.
    Three ways the grid fails that.  The first two are checked on every cell
    long enough to hold two anchors, the third on every cell holding one; a
    shorter cell gives no testimony either way.

    - the other engine read none of the cell.  Without this the other two
      checks are silent exactly when the witness is worthless - a reading that
      looped, stopped early, or is missing - and a garbled long-cell table went
      through on a looped reading.  Measured on the 1,107 non-table blocks of
      16 or more characters on pages whose engine-B reading did not loop: 21
      have no piece of 8 in it at all (it skipped them, or engine A garbled
      them), none has a share between 0 and 0.2, the rest 0.2 or more; every
      long cell of the corpus's engine tables has 0.4 or more.  So the test is
      "some piece", not a share: any share up to 0.2 draws the same line.
    - two pieces of a cell come in the other reading the other way round, or
      overlap there by a whole anchor (the same stretch used twice): engine A
      spliced text from elsewhere into the cell, or repeated it.  An overlap
      shorter than an anchor is a reduplicated character (糾糾, 個個) that one
      engine read once, not a repeat - counting any overlap flagged three such
      blocks of prose.
    - two cells claim one stretch the other engine read once: a note copied
      into every row it spans, or a figure written twice.  Measured: fires on
      two engine tables, both garbled against the page (the continuation of
      the 教授要言 table in the corpus's 1910 government report p.62, where a twenty-
      character run also repeats back to back inside one cell; a list of eight
      years and eight figures in a fiscal report p.681 that came back with
      eleven years and ten figures, two of them twice).  With the prose blocks
      of each page taken as the cells of one table, it fires on one page of
      the corpus, where engine A read the end of one block again at the start
      of the next.

    This replaces a 60-character cap on cell length, which called any long cell
    "prose the layout detector swallowed".  It was a false invariant: a notes
    column holds real cells of 75, 80 and 105 characters (the corpus's 1910 government report
    p.61, transcribed by a person), and the page it was written for, p.62, is
    not prose either - it is the same table continued, which the engine garbled.
    Other candidates were measured on every engine table in the corpus and
    dropped: the longest run repeated inside a table (p.62 has 40, real tables
    up to 8, but real pages print runs of up to 34 twice, so length says
    nothing on its own); runs the table repeats more often than engine B does
    (fires on 15 real tables for runs of 2-3 characters engine B simply did not
    read); how much of the table aligns in order with engine B (0.05-1.00 on
    real tables - it reads them in another order - against 0.50 on p.62).
    """
    reading = CJK(witness or "")
    matcher = difflib.SequenceMatcher(None, "", reading, autojunk=False)
    claimed: list[tuple["_tables.Cell", int, int]] = []
    for cell in table.cells:
        text = CJK(cell.text)
        if len(text) < WITNESS_ANCHOR:
            continue
        where = f"row {cell.row + 1} column {cell.col + 1}"
        anchors = witness_anchors(text, reading, matcher)
        if len(text) >= 2 * WITNESS_ANCHOR:
            if not anchors and not witness_anchors(text, reading, matcher, unique=False):
                return (f"unwitnessed: engine B's reading holds none of the {len(text)} characters "
                        f"at {where} ({text[:10]}…) - it skipped them, looped or is missing, "
                        "so nothing vouches for the cell")
            for (a1, b1, n1), (a2, b2, n2) in zip(anchors, anchors[1:]):
                if b2 < b1 or b1 + n1 - b2 >= WITNESS_ANCHOR:
                    return (f"engine B reads {where} in another order - {text[a1:a1 + n1][:10]}… "
                            f"before {text[a2:a2 + n2][:10]}…, which it reads the other way round "
                            "or once: the cell was spliced or repeated")
        for a, b, n in anchors:
            for other, b2, n2 in claimed:
                if other is not cell and min(b + n, b2 + n2) - max(b, b2) >= WITNESS_ANCHOR:
                    return (f"{where} and row {other.row + 1} column {other.col + 1} both hold "
                            f"{text[a:a + n][:10]}…, which engine B reads once: a cell was copied")
            claimed.append((cell, b, n))
    return None


def is_furniture_text(text: str, heads: Sequence[str] = (),
                      furniture: frozenset[str] | set[str] = frozenset()) -> bool:
    """Whether a table row's text is page furniture - the running head.

    Evidence the page run already has: the book profile's measured running
    heads, and the n-grams that recur across the book's pages (the deletion
    guard's furniture, same test and same leftover limit).
    """
    text = CJK(text)
    return bool(text) and bool((heads and is_running_head(text, heads))
                               or (furniture and _is_furniture(text, furniture, DELETED_RUN_LIMIT)))


def furniture_row(table: "_tables.Table", heads: Sequence[str] = (),
                  furniture: frozenset[str] | set[str] = frozenset()) -> int | None:
    """The first row of the grid that is page furniture, or None.

    The corpus's 1910 government report p.62 came back with its running head as the table's
    first row.  Measured on the 410 rows holding text of all 47 engine tables:
    the book's recurring n-grams mark that row and no other; the profile heads
    mark none (three of the five books have measured heads, p.62's is not one).
    """
    for r in range(table.rows):
        if is_furniture_text("".join(cell.text for cell in table.cells if cell.row == r),
                             heads, furniture):
            return r
    return None


# A second reading of a grid vouches for it when the two agree cell for cell:
# at most this share of the cells holding CJK may differ, and a differing pair
# must be the same cell read with a character or two apart (vouched_grid).
VOUCH_CELLS_DIFFER = 0.1
VOUCH_CELL_DISTANCE = 2


def vouched_grid(table: "_tables.Table", witness: str | None) -> tuple["_tables.Table | None", str]:
    """Engine B's own grid of TABLE, when it agrees with engine A's, else None
    and why not.

    HunyuanOCR writes a table it reads as HTML too, so a page often holds two
    independent readings of one grid.  They agree when the two grids have the
    same rows and columns, lay their merged cells the same way, and read the
    same characters at every position (a merged cell's text repeated into every
    slot it covers; a cell with no CJK by its letters and digits): the layout -
    a rowspan, a colspan, a cell wrapped onto two lines - is then not one
    engine's word but both.  Up to VOUCH_CELLS_DIFFER of the cells holding
    text may differ by a character or two (a variant sort, a
    character one engine dropped - 張翀/張狒, 正德十一年至十三年/正德十一至十三年
    on the modern book): those are the same cell read apart, and the merge
    settles the characters; anything further - a cell shifted into its
    neighbour, a column the other engine never cut - is a different grid, and
    so is any difference in a slot a merged cell covers (an empty slot where
    engine A spans: engine B saw no merge there).  Engine B writing the
    merged cell's text in every slot it covers agrees with the merge: that is
    how a reader without rowspans writes one.
    Measured on the one modern book (609 pages, 30 engine A grids refused,
    1,450 figures in them that the rebuild from bare characters lost): engine
    B's grid vouches for 9 - 6 refused for a cell line break, 3 for merged
    cells, 326 figures; on 13 engine B wrote no grid of the same shape and on 7
    its grid differs further (a row shifted into its neighbours' cells, the
    same cell wrapped), and those 20 stay refused.
    """
    if not witness or "<table" not in witness:
        return None, "engine B wrote no grid"
    def key(text: str) -> str:
        # A cell's characters; one with no CJK (a year, a page reference, a
        # name in Latin letters) by its letters and digits, so that a figure
        # standing in another slot is a difference and a dash's width is not.
        return CJK(text) or "".join(ch for ch in text if ch.isalnum())

    keyed = [[key(text) for text in row] for row in table.grid(fill_spans=True)]
    # The slots a merged cell covers: there the other reading must agree to
    # the character, since the merge is what is being vouched for.
    covered = {(r, k) for cell in table.cells if cell.rowspan > 1 or cell.colspan > 1
               for r in range(cell.row, cell.row + cell.rowspan) for k in range(cell.col, cell.col + cell.colspan)}
    nearest: tuple[int, str] | None = None
    for match in re.finditer(r"<table\b.*?</table>", witness, flags=re.S | re.I):
        other = _tables.parse_html_table(match.group(0), source_breaks=True)
        if other is None:
            continue
        other = _tables.simplify(other)
        if (other.rows, other.cols) != (table.rows, table.cols):
            note = f"engine B's grid is {other.rows} rows x {other.cols} columns"
            nearest = nearest or (10 ** 6, note)
            continue
        got = other.grid(fill_spans=True)
        holding = differing = 0
        apart: list[str] = []
        for r in range(table.rows):
            for k in range(table.cols):
                mine, theirs = keyed[r][k], key(got[r][k])
                if not mine and not theirs:
                    continue
                holding += 1
                if mine != theirs:
                    differing += 1
                    if (r, k) in covered or _tables._edit_distance(mine, theirs) > VOUCH_CELL_DISTANCE:
                        apart.append(f"row {r + 1} column {k + 1} ({mine[:8] or 'empty'} / {theirs[:8] or 'empty'})")
        if differing > max(1, int(holding * VOUCH_CELLS_DIFFER)) or apart:
            note = (f"engine B's grid of the same shape reads {differing} of {holding} cells otherwise"
                    + (f", e.g. {apart[0]}" if apart else ""))
            if nearest is None or differing < nearest[0]:
                nearest = (differing, note)
            continue
        return other, f"vouched by engine B's grid ({holding} cells, {differing} read a character or two apart)"
    return None, nearest[1] if nearest else "engine B wrote no grid"


def engine_table(html_text: str, witness: str | None = None, heads: Sequence[str] = (),
                 furniture: frozenset[str] | set[str] = frozenset()) -> tuple[str | None, str]:
    """Convert one engine <table> to page markup, or explain the refusal.

    Detected is not the same as correct, so this refuses more than it accepts,
    and only what some evidence vouches for comes back.  A grid with a cell
    wrapped onto two lines, or with a merged cell, is refused on engine A's
    word alone and accepted when engine B's own grid agrees with it
    (vouched_grid) - as HTML when a cell is merged, Markdown otherwise.
    `witness` is the other engine's text for the same page (vouched_grid,
    witness_refusal); `heads` and `furniture` are the running heads and
    recurring n-grams of the book (furniture_row).
    """
    if "<table" not in html_text:
        return None, "not table markup"
    table = _tables.parse_html_table(html_text, source_breaks=True)
    if table is None:
        return None, "unparseable table markup"
    # A grid that only renders after repair (a rowspan off the end, a colspan
    # into a cell from above, a short row) is not one the engine described.
    if table.problems:
        return None, "inconsistent grid: " + "; ".join(table.problems[:3])
    table = _tables.simplify(table)
    # Blank rows stay in the grid; only rows holding text count toward the
    # minimum.  The old harvester dropped blank rows, but printed tables rule
    # empty rows (a tax line with no figure that year) and a rowspan counts
    # them: on a multi-page fiscal report p.29 the figures lost three of their nineteen
    # rows and no longer lined up with the nineteen labels printed beside them.
    rows = len({cell.row for cell in table.cells if cell.text.strip()})
    if rows < TABLE_MINIMUM_ROWS:
        return None, f"{rows} row(s); a table needs at least {TABLE_MINIMUM_ROWS}"
    # A line break inside a cell - <br>, a newline between two characters, or
    # the literal backslash-n this engine writes - is either the cell's own
    # text wrapping or several columns collapsed into one cell, and the grid
    # cannot say which.  The corpus has both: its 1910 government report p.71
    # collapsed every row's last four columns into one cell (價值\\n購置處\\n…),
    # while a fiscal report p.261 prints one vertical character per line inside
    # single cells and p.30 has a diagonal corner cell.  Matching a cell's
    # fragments against the width of the neighbouring rows does not separate
    # them either - every row of p.71 is collapsed the same way, so the match
    # holds for 2 of its 12 collapsed cells, and also for 3 of p.261's 31
    # wrapped cells and for p.30's corner.  What does separate them is engine
    # B's own grid: a collapsed cell is a column the other engine cut, and the
    # grids differ; a wrapped cell is one both read as one cell, with engine B
    # writing it on one line.  So the table is refused and rebuilt from the
    # page image unless engine B's grid vouches for it (vouched_grid), and
    # then the break is the cell's wrapping, written <br> (_tables.render).
    # On the modern book, 13 grids refused here held 1,450 figures the rebuild
    # from bare characters lost.
    broken = [cell for cell in table.cells if "\n" in cell.text or "\\n" in cell.text]
    vouched: "_tables.Table | None" = None
    vouch_note = ""
    if broken:
        vouched, vouch_note = vouched_grid(table, witness)
        if vouched is None or any(
                "\n" in other.text or "\\n" in other.text for other in vouched.cells
                if any((other.row, other.col) == (cell.row, cell.col) for cell in broken)):
            why = ("engine B's grid wraps the same cell" if vouched is not None else vouch_note)
            return None, (f"{len(broken)} cell(s) hold line breaks - the cell's own text wrapping, or "
                          f"several columns collapsed into one cell, which the grid cannot tell apart; {why}")
        # The engine's literal backslash-n is the cell's line break from here on.
        table = _tables.with_texts(table, [cell.text.replace("\\n", "\n") for cell in table.cells])
    # The engine's merged cells are not evidence of the page's.  Of the six
    # engine tables in the corpus whose grid keeps a merged cell after
    # simplify, every one places a merge wrong against the page (checked on the
    # renders): the sentence above a table as a full-width row, a form's title
    # printed outside its frame as a full-height column, row labels as
    # full-height columns, a year heading split one column off, sub-headings
    # put under the next column.  The one right merge, a diagonal corner cell,
    # sits in a grid garbled elsewhere.  On the engine's word alone nothing
    # vouches for a rowspan or colspan, and enforcing one would overwrite the
    # formatter's own reading of the image, so the table goes back to the
    # formatter to be rebuilt from the image as HTML - unless the second
    # reading of the grid, engine B's own, lays the merged cells the same way
    # and reads the same characters in every slot (vouched_grid): then the
    # merge is two engines' word, and the grid stands as HTML.
    merged = [cell for cell in table.cells if cell.rowspan > 1 or cell.colspan > 1]
    if merged:
        if vouched is None:
            vouched, vouch_note = vouched_grid(table, witness)
        if vouched is None:
            first = merged[0]
            return None, (f"merged layout unverified: {len(merged)} merged cell(s), e.g. row "
                          f"{first.row + 1} column {first.col + 1} ({CJK(first.text)[:10] or 'empty'}, "
                          f"{first.rowspan} row(s) x {first.colspan} column(s)); nothing but the "
                          f"engine vouches for the merge ({vouch_note})")
    # The evidence checks: every one that fails is reported, so the sealed
    # record shows the whole case against the grid, not the first symptom.
    evidence: list[str] = []
    head = furniture_row(table, heads, furniture)
    if head is not None:
        text = CJK("".join(cell.text for cell in table.cells if cell.row == head))
        evidence.append(f"row {head + 1} ({text[:16]}) is page furniture - the running head - "
                        "which the detector pulled into the grid")
    spliced = witness_refusal(table, witness)
    if spliced:
        evidence.append(spliced)
    # A cell with no CJK is outside everything that checks characters: the
    # merge aligns CJK only.  Marks alone (a printed leader ……, a dash) are
    # kept - there is nothing to misread into them and dropping them deletes
    # print.  Letters or digits must be in engine B's reading too: a fiscal
    # report p.681 came back with ASCII 00 where the page prints 〇〇, which
    # engine B read as 〇.
    squashed = re.sub(r"\s+", "", witness or "")
    unread = next((cell for cell in table.cells if cell.text and not CJK(cell.text) and any(
        unicodedata.category(ch)[0] in "LN" for ch in cell.text) and (
        re.sub(r"\s+", "", cell.text) not in squashed)), None)
    if unread is not None:
        evidence.append(f"row {unread.row + 1} column {unread.col + 1} holds {unread.text[:12]!r}, "
                        "letters or digits that neither the character merge nor engine B's "
                        "reading holds")
    if evidence:
        return None, "; ".join(evidence)
    note = f"{table.rows} rows x {table.cols} columns"
    if vouched is not None:
        note += f"; {len(broken)} wrapped cell(s), {len(merged)} merged cell(s), {vouch_note}"
    return _tables.render(table), note


def load_blocks(draft_json: Path | None) -> list[dict[str, Any]]:
    """Block text plus pixel bbox, used to crop the region behind a disagreement."""
    if draft_json is None or not draft_json.is_file():
        return []
    try:
        record = json.loads(draft_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    blocks = []
    for line in record.get("lines") or []:
        if not isinstance(line, dict):
            continue
        text = line.get("text")
        box = line.get("boundingBox")
        if isinstance(text, str) and isinstance(box, list) and len(box) == 4:
            blocks.append({
                "text": text,
                "box": [float(v) for v in box],
                # Carried through so the merge can route by block type. This was
                # produced by both OCR runners and read by nothing, which is why
                # a detected table went down the same path as a paragraph.
                "label": str(line.get("blockLabel") or "unknown"),
                "order": line.get("blockOrder"),
            })
    return blocks


ORIENT_SYSTEM = """你係古籍表格判讀員。畀你原掃描頁圖同一個現有格仔
（Markdown 或 HTML；rowspan／colspan 係跨格）。逐格對返圖，分清原圖物理位置同現有格仔位置。
今次政策係 auto：有充分圖像依據先將格仔整理成每行一筆紀錄、欄名一行嘅邏輯表。
原圖文字可以橫排或直排、由左至右或由右至左；唔可以用全書慣例、語意通順、表格高闊或
合計數目推斷方向。合計只係核對線索，唔證明邊條軸係一筆紀錄。

問題係：喺畀你嘅格仔入面，完整紀錄已經逐行排（row），定係逐列排（column）？
欄名條帶亦要對圖：如果 Nx1 係欄名而同表其餘部分清楚證明應該成為邏輯欄名一行，可以答
column；只有孤立 Nx1／1xN 條帶，睇唔到足夠表格關係，就答 unknown，唔好借用另一個表。
兩種方向都可能、格位對唔返、圖模糊或相關續頁未提供，同樣答 unknown。

只輸出兩行：
RECORD: row
或 RECORD: column
或 RECORD: unknown
EVIDENCE: 指出原圖可見嘅欄名／格線／紀錄對應及現有格仔位置；唔清楚就講缺少乜證據。
row = 現有格仔已經係邏輯行；column = 要交換軸，首筆紀錄次序另行對圖確認。"""

ORIENT_LINE = re.compile(r"^\s*RECORD:\s*(column|row|unknown)\s*$", re.I | re.M)
ORIENT_EVIDENCE_LINE = re.compile(r"^\s*EVIDENCE:[ \t]*(\S[^\n]*)$", re.I | re.M)


def first_table(text: str) -> "_tables.Table | None":
    """The table a recovered-table string holds, Markdown or HTML.

    Parsed by _tables, the parser the auditor and the regression suite use, so
    a blank row stays a row: an earlier splitter here called a blank row a
    separator (the empty set is a subset of everything) and dropped it, which
    collapsed an 18-row table to 10 and shifted every figure onto the wrong
    tax line when the grid was rotated.
    """
    tables = _tables.find_tables(text)
    return tables[0] if tables else None


def rotate_table(table_text: str) -> str:
    """Rotate a table 90 degrees counter-clockwise; merged cells turn with it.

    Not a bare transpose: printed columns run RIGHT TO LEFT, so the engine's
    last column is the first logical row.  A bare transpose would put the header
    row last - which is exactly what the corner label 年度 landing at the end of
    the engine's header row tells you has happened.
    """
    table = first_table(table_text)
    if table is None:
        return table_text
    return _tables.render(_tables.rotate_counterclockwise(table))


FIGURE_DIGITS = "〇零一二三四五六七八九"
GROUP_MARKS = "、,，"
FIGURE_TOKEN = re.compile(f"[{FIGURE_DIGITS}]+(?:[{GROUP_MARKS}][{FIGURE_DIGITS}]+)*")


def figure_groups(text: str) -> tuple[int, ...] | None:
    """How a printed figure's digits are grouped: 七、〇五八、二一九 -> (1, 3, 3).

    None when the text is anything but positional digits and group marks.
    """
    squashed = re.sub(r"\s+", "", text.translate(ZERO_FORMS))
    if not FIGURE_TOKEN.fullmatch(squashed):
        return None
    return tuple(len(group) for group in re.split(f"[{GROUP_MARKS}]", squashed))


def witness_figures(witness: str | None) -> dict[str, set[str]]:
    """Every figure of two or more digits engine B read, by its digits: the
    ways (group marks included) it wrote them."""
    found: dict[str, set[str]] = {}
    for match in FIGURE_TOKEN.finditer((witness or "").translate(ZERO_FORMS)):
        digits = re.sub(f"[{GROUP_MARKS}]", "", match.group(0))
        if len(digits) >= 2:
            found.setdefault(digits, set()).add(match.group(0))
    return found


def grouping_fits(groups: Sequence[int], size: int) -> bool:
    """Whether a figure is grouped in `size`s, counted from its last digit."""
    return 1 <= groups[0] <= size and all(n == size for n in groups[1:])


def table_places(sequence: str, target: str, cells: tuple[int, int] | None = None,
                 lines: Sequence[int] | None = None, base: int = 0) -> tuple[list[int], list[bool], int]:
    """Where each character of a table's SEQUENCE (its cells' characters in
    reading order) falls in TARGET, the merged text it is restated from:
    at[k] for each character and at[len(SEQUENCE)] where the last one ends;
    found[k], whether it is matched as written; and how many are.

    A character the merge replaced falls in the text that replaced it, one it
    deleted where it would have stood.  The end is the end of the table's
    LAST matched character, never of TARGET: an insertion after the table
    (text after it, such as the running foot) is not the last cell's.

    For the table's edge text (table_edges), a
    lopsided replacement at the table's first character - more than
    INSERTION_LIMIT characters longer than the table's characters it
    replaces, which are INSERTION_LIMIT or fewer, and not a numeral conflict
    - ends with them: what comes before is text before the table.  One at its
    last character begins with them, and what comes after is text after the
    table.  split_lopsided's rule: the table's characters begin (end) engine
    A's block, so what engine A did not read there came before (after) them.
    The alignment joins an insertion beside a table and a character the
    merge changed next to it into one replacement, and without this the
    first cell took everything from the replacement's start: on a made-up
    page (check_tables, check_table_edge_text_substitution) the title above
    the table only engine B read, and with the whole page as the target the
    prose before it too, written twice.  A replacement a character or two
    longer is a reading of the cell (a figure one engine read a digit longer)
    and stays whole in it, as does one over the whole table.

    Then what engine B read in one line or table cell with the
    table's first cell goes into it, and so for its last: LINES gives the
    line of engine B's each character of the page's merged text was read
    in (engine_b_lines_merged; TARGET starts at BASE there), CELLS the
    number of characters of the table's first and last cells.  When the
    first cell's characters that engine B read where they stand are all of
    one line of engine B's, the text before it on that line is the cell's,
    unless the line runs on before TARGET - engine B then read the block
    before on it too, and its lines say nothing of where the table begins.
    The split above cannot tell a cell engine A read short from a title:
    on made-up pages (check_tables, check_table_edge_text_cell_read_short)
    a first cell engine A read 目 and engine B 項目 left 項 on a line of its
    own above the table, a last figure engine A read a digit short left
    that digit below it, and a last cell engine A read 另如 where engine B
    read 另加五成照舊 was cut to 另加, 五成照舊 on a line below - where the
    table ends the page, the table restated against the whole page had
    kept the cell whole.
    """
    at = [0] * (len(sequence) + 1)
    found = [False] * len(sequence)
    mapped = [False] * len(sequence)
    end = 0
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, sequence, target, autojunk=False).get_opcodes():
        n, m = a2 - a1, b2 - b1
        split = (tag == "replace" and m - n > INSERTION_LIMIT >= n and (a1 == 0) != (a2 == len(sequence))
                 and not is_numeral_conflict({"a": sequence[a1:a2], "b": target[b1:b2]}))
        shift = m - n if split and a1 == 0 else 0
        for k in range(n):
            at[a1 + k] = b1 + shift + min(k, max(m - shift - 1, 0)) if tag != "delete" else b1
            found[a1 + k] = tag == "equal"
            mapped[a1 + k] = tag != "delete"
        if a2 == len(sequence) and a1 < a2:
            end = (b1 + n if split else b2) if tag != "delete" else b1
    at[len(sequence)] = end
    if cells is not None and lines is not None and sequence:

        def line_of(ks: range) -> int | None:
            """The one line of engine B's the characters KS of SEQUENCE were
            read in, where engine B read them where they stand."""
            own = {lines[base + at[k]] for k in ks if mapped[k] and at[k] < len(target)} - {-1}
            return own.pop() if len(own) == 1 else None

        q = line_of(range(cells[0]))
        start = at[0]
        while q is not None and start > 0 and lines[base + start - 1] == q:
            start -= 1
        if not (start == 0 and base > 0 and lines[base - 1] == q):
            at[0] = start
        q = line_of(range(len(sequence) - cells[1], len(sequence)))
        stop = at[len(sequence)]
        while q is not None and stop < len(target) and lines[base + stop] == q:
            stop += 1
        if not (stop == len(target) and base + stop < len(lines) and lines[base + stop] == q):
            at[len(sequence)] = stop
    return at, found, sum(found)


def restate_table(table_text: str, resolved: str, witness: str | None = None,
                  regrouped: list[str] | None = None, span: tuple[int, int] | None = None,
                  held: list[tuple[int, int]] | None = None,
                  lines: Sequence[int] | None = None) -> str | None:
    """Rewrite a recovered table using the MERGED characters.

    Structure comes from the engine; characters must come from the merge, or
    the two disagree wherever they chose different variant sorts (a table
    that said 價值 and 教員 where the merge had 價値 and 敎員 cost a page its
    table when verify_characters rejected the copy).

    Each cell is mapped into the merged text through the character alignment,
    never by counting widths: a single adjudication that drops a grid line
    read as 一, or adds a zero one engine missed, used to shift every later
    cell and scramble a page of figures.  When a cell keeps its character
    count, its own punctuation - thousands separators 、, brackets - is kept in
    place; when the count changed, the merged characters stand alone.  A cell
    with no CJK at all keeps its own text as read, spaces included (Hong Kong
    is two words): the merge has nothing to say about it, dropping it would
    delete what the engine read, and engine_table has already refused a table
    whose letters or digits engine B does not also read.

    A figure's group marks (、) are the engine's reading too, and engine A
    misplaces them: measured on a page of 21 figures, 7 came back with marks
    inside a group - a seven-digit figure grouped 1-1-2-3 where the print
    (and engine B, `witness`) groups it 1-3-3.  Where the merge changed a figure's
    digits, engine A's marks no longer fit it at all.  So a figure takes engine
    B's grouping when B read exactly the figure's merged digits, in one way,
    and either the merge changed the digit count or engine A's grouping does
    not fit the page's.  The page's grouping is what the figures both engines
    group alike show: when all their groups after the first have one size,
    a figure fits it when its own do and its first group is no longer; when
    they show several sizes, or there are none, no grouping is judged wrong.
    Otherwise engine A's marks stay, and a changed figure stands bare as
    before - no grouping is ever made up.  Each figure regrouped is noted in
    `regrouped`.

    What the table holds is the merged text from its first cell's first
    character to its last cell's last; HELD, when given, gets that stretch as
    (start, end) in CJK(RESOLVED).  The rest of the table block's stretch is
    the table's edge text: recover_tables finds it, and the page writes it
    beside the table (table_edges).  SPAN - the block's stretch, (start, end) - is where the table's
    characters are looked for, a replacement at the table's first or last
    character longer than the table's characters there is split at the table's
    edge, and what engine B read in one line or cell with the first (last)
    cell goes into it (table_places; LINES, engine B's line of each character
    of CJK(RESOLVED), engine_b_lines_merged).

    Markdown and HTML tables alike: cells are taken in reading order (row by
    row, a merged cell where its top-left slot is), and the table comes back in
    the markup it came in, spans untouched.

    Returns None when the table's text cannot be located in the merged text
    (or in SPAN), which means the two are not describing the same region.
    """
    table = first_table(table_text)
    if table is None:
        return None
    texts = [cell.text for cell in table.cells]
    sequence = "".join(CJK(text) for text in texts)
    if not sequence:
        return None
    merged_all = CJK(resolved)
    # With SPAN (the table's edge text), the table's characters are looked for
    # in its own block's stretch of the merged text only: they are engine A's
    # reading of that block, and block_spans hands each of them to it.
    base = span[0] if span is not None else 0
    if span is not None:
        merged_all = merged_all[span[0]:span[1]]
    # Position in the merged text of every character of the table sequence; the
    # end of the table is the end of its LAST matched character (table_places).
    # Engine B's lines place the first and last cells' edges only inside the
    # block's own span: against the whole page a line of engine B's that ran
    # on from the prose before would take the prose into the first cell.
    lines = lines if span is not None else None
    widths = [len(CJK(text)) for text in texts if CJK(text)]
    a2b, _, covered = table_places(sequence, merged_all, (widths[0], widths[-1]), lines, base)
    if covered < len(sequence) * 0.9:
        return None
    merged_all_end = a2b[len(sequence)]

    merged_cells: list[str | None] = []
    cursor = 0
    for text in texts:
        width = len(CJK(text))
        if not width:
            merged_cells.append(None)
            continue
        lo = a2b[cursor]
        hi = a2b[cursor + width] if cursor + width < len(sequence) else merged_all_end
        merged_cells.append(merged_all[lo:hi])
        cursor += width
    if held is not None:
        held.append((base + a2b[0], base + merged_all_end))

    read_by_b = witness_figures(witness)

    def b_reading(digits: str) -> str | None:
        ways = read_by_b.get(digits) or set()
        return next(iter(ways)) if len(ways) == 1 else None

    # The page's grouping: group sizes after the first, in figures both
    # engines read with the same digits and the same marks.
    sizes: set[int] = set()
    for text, merged_cell in zip(texts, merged_cells):
        groups = figure_groups(text)
        if groups is None or merged_cell is None or CJK(text) != merged_cell:
            continue
        other = b_reading(merged_cell)
        if other is not None and figure_groups(other) == groups:
            sizes.update(groups[1:])
    size = next(iter(sizes)) if len(sizes) == 1 else None

    rebuilt: list[str] = []
    for index, (text, merged_cell) in enumerate(zip(texts, merged_cells)):
        if merged_cell is None:
            rebuilt.append(text)
            continue
        own = figure_groups(text)
        other = b_reading(merged_cell) if own is not None and figure_groups(merged_cell) else None
        if other is not None and figure_groups(other) != own and (
                len(merged_cell) != len(CJK(text))
                or (size is not None and not grouping_fits(own, size)
                    and grouping_fits(figure_groups(other), size))):
            if regrouped is not None:
                regrouped.append(f"{text.strip()} -> {other} (engine B's grouping)")
            rebuilt.append(other)
        elif len(merged_cell) == len(CJK(text)):
            chars = iter(merged_cell)
            rebuilt.append("".join(next(chars) if CJK(ch) else ch
                                   for ch in text if ch == "\n" or not ch.isspace()))
        else:
            rebuilt.append(merged_cell)
    restated = _tables.with_texts(table, rebuilt)
    return (_tables.render_html(restated) if table.kind == "html"
            else _tables.render_markdown(restated))


CHINESE_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
TOTAL_LABELS = ("合計", "總計", "共計", "計合", "計總", "計共")


def chinese_figure(cell: str) -> int | None:
    """Parse a printed figure like 二、三一五、六六三 into 2315663.

    These are positional digit strings with 、 as the group separator, not the
    多位 form (二千三百…), so each character is one digit.  Returns None for
    anything that is not purely digits and separators.
    """
    digits = [c for c in cell if c not in "、,， \t"]
    if len(digits) < 2 or not all(c in CHINESE_DIGITS for c in digits):
        return None
    value = 0
    for c in digits:
        value = value * 10 + CHINESE_DIGITS[c]
    return value


def totals_axis(table_text: str) -> str | None:
    """Which axis holds an exact arithmetic total, as a review clue only.

    Both a total across fields and a total across records can reconcile, so
    arithmetic does not establish which axis is a record or authorize a
    transform.  A merged cell counts once, in its top-left slot.
    """
    table = first_table(table_text)
    if table is None or table.rows < 3:
        return None
    rows = table.grid()
    width = table.cols

    def labelled(cells):
        return next((i for i, c in enumerate(cells)
                     if any(t in c.replace(" ", "") for t in TOTAL_LABELS)), None)

    # A total sitting at the end of a COLUMN of labels means the figures beside
    # it accumulate down that column.
    for index, row in enumerate(rows):
        if labelled(row) is None:
            continue
        for column in range(width):
            total = chinese_figure(row[column])
            if total is None:
                continue
            series = [chinese_figure(rows[r][column]) for r in range(len(rows))
                      if r != index]
            values = [v for v in series if v is not None]
            if len(values) >= 2 and sum(values) == total:
                return "column"
    # The mirror image: a total at the end of a row of figures.
    for column in range(width):
        column_cells = [rows[r][column] for r in range(len(rows))]
        if labelled(column_cells) is None:
            continue
        for index, row in enumerate(rows):
            total = chinese_figure(row[column])
            if total is None:
                continue
            values = [v for v in (chinese_figure(c) for i, c in enumerate(row)
                                  if i != column) if v is not None]
            if len(values) >= 2 and sum(values) == total:
                return "row"

    # No 合計 LABEL, but the total is usually still there as the last figure of
    # the series.  A continuation page carries the figures without the stub -
    # a fiscal report p.25 to p.29 - so the label lives on the first page of the
    # span and only the arithmetic is left here.  Three or more large figures
    # summing exactly to a fourth is not a coincidence worth worrying about.
    def series_ends_in_total(cells: Sequence[str]) -> bool:
        figures = [v for v in (chinese_figure(c) for c in cells) if v is not None]
        return len(figures) >= 4 and sum(figures[:-1]) == figures[-1]

    columns_total = sum(1 for c in range(width)
                        if series_ends_in_total([rows[r][c] for r in range(len(rows))]))
    rows_total = sum(1 for r in rows if series_ends_in_total(r))
    if columns_total and columns_total > rows_total:
        return "column"
    if rows_total and rows_total > columns_total:
        return "row"
    return None


DIRECTION_SYSTEM = """你係古籍表格逐格核圖員。依據附上嘅原掃描圖，同現有表格、block 定位及
每格嘅零起算 row／col／rowspan／colspan，先確定圖上對應嗰一格，再判斷字嘅閱讀次序。
甲乙係同一格兩種相反讀法，必須由圖上字形位置、該格局部排字方向同相鄰格關係定案。
唔可以因為成詞、通順、常見姓名／地名、全書直排慣例、其他格嘅方向，或者另一引擎同意就倒轉。
原書可能真係印咗睇似反序或唔通順嘅字；兩個字嘅短格亦冇例外。
格仔本身可能已經轉軸；row／col 指下面提供嘅現有表格位置，唔直接代表原圖座標。
如果定位唔唯一、格內有混合方向、圖像模糊、兩個讀法都可能或都唔符，就答「兩個都唔係」。
淨係輸出 JSON array，每個候選一個 object，次序相同：
{"row": 0, "col": 1, "choice": "甲", "evidence": "圖上該格可見字序與局部方向"}
choice 只可係「甲」、「乙」、「兩個都得」或「兩個都唔係」。evidence 必須講圖像依據；唔肯定就講缺少乜。
唔好改字、補字或者推算答案。"""

# Paired marks swap when a cell is turned round: reversed as a string,
# （甲項　乙項） would read ）項乙　項甲（.
MIRRORED = dict(zip("（）()「」『』《》〈〉︵︶", "）（)(」「』『》《〉〈︶︵"))
DIRECTION_ANSWERS = {"甲": "first", "a": "first", "乙": "second", "b": "second",
                     "兩個都得": "both", "both": "both", "兩個都唔係": "neither", "neither": "neither"}


def turned_round(text: str) -> str:
    """A cell read the other way: characters reversed, paired marks mirrored."""
    return "".join(MIRRORED.get(char, char) for char in reversed(text))


def direction_candidates(table: "_tables.Table") -> list["_tables.Cell"]:
    """Header cells and both outside label axes, each physical cell once.

    A right-to-left grid can put labels on its right edge.  A spanned cell
    reaches that edge when its covered columns do, not only when its anchor
    does.  Candidate selection only schedules visual review; it never
    authorizes a reversal, including for a short label.
    """
    return [cell for cell in table.cells
            if (cell.header or cell.row == 0 or cell.col == 0
                or cell.col + cell.colspan == table.cols)
            and len(CJK(cell.text)) >= 2 and CJK(cell.text) != CJK(cell.text)[::-1]
            and chinese_figure(cell.text) is None]


def parse_direction_answers(reply: str, count: int) -> list[str | None]:
    """Parse candidate choices.  Visual decisions also require exact locators
    and evidence through parse_visual_direction_answers below."""
    try:
        match = re.search(r"\[.*\]", reply or "", re.S)
        answers = json.loads(match.group(0)) if match else []
    except (json.JSONDecodeError, AttributeError):
        answers = []
    if not isinstance(answers, list) or len(answers) != count:
        return [None] * count
    return [DIRECTION_ANSWERS.get(str(answer).strip().strip("\"'「」").lower()) for answer in answers]


def parse_visual_direction_answers(reply: str, cells: Sequence["_tables.Cell"]
                                   ) -> list[tuple[str | None, str]]:
    """Reject absent, shifted or duplicated locators and evidence-free answers.

    Legacy text-only arrays and fragments embedded in contradictory prose
    do not authorize image-backed changes.
    """
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate answer field")
            result[key] = value
        return result

    try:
        answers = json.loads(reply or "", object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        answers = []
    if not isinstance(answers, list) or len(answers) != len(cells):
        return [(None, "invalid visual answer list")] * len(cells)
    locators = [(answer.get("row"), answer.get("col")) for answer in answers
                if isinstance(answer, dict) and type(answer.get("row")) is int
                and type(answer.get("col")) is int]
    if len(set(locators)) != len(locators):
        return [(None, "duplicated cell locator")] * len(cells)
    out: list[tuple[str | None, str]] = []
    for cell, answer in zip(cells, answers):
        if (not isinstance(answer, dict) or type(answer.get("row")) is not int
                or type(answer.get("col")) is not int
                or (answer["row"], answer["col"]) != (cell.row, cell.col)):
            out.append((None, "missing or mismatched cell locator"))
            continue
        evidence = answer.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            out.append((None, "missing visual evidence"))
            continue
        choice = DIRECTION_ANSWERS.get(str(answer.get("choice", "")).strip().lower())
        out.append((choice, evidence.strip()))
    return out


def fix_cell_direction(table_text: str, client: "Client | None", max_tokens: int,
                       block: int | None = None, *, render_bytes: bytes | None = None,
                       context: str = "", table_orientation: str = "auto"
                       ) -> tuple[str, list[dict[str, Any]]]:
    """Reverse a label only after two image-backed, located visual verdicts.

    The option order is exchanged between the two questions.  Both must
    identify the same current-grid cell and support its reversed reading
    from the image.  Missing images, ambiguous correspondence and malformed
    answers preserve the reading and emit an explicit undecided record.
    The transformation changes cell text only, preserving dimensions/spans
    under either output policy.  Records bind decisions to input bytes.
    """
    table = first_table(table_text)
    cells = direction_candidates(table) if table is not None else []
    if not cells:
        return table_text, []
    readings = [(cell.text, turned_round(cell.text)) for cell in cells]
    common = {"tableOrientation": table_orientation,
              "tableSha256": sha256_bytes(table_text.encode("utf-8")),
              "sourceImageSha256": sha256_bytes(render_bytes) if render_bytes else None,
              "context": context}

    def ask(turned_first: bool) -> list[tuple[str | None, str]]:
        listing = "\n".join(
            f"row={cell.row}, col={cell.col}, rowspan={cell.rowspan}, colspan={cell.colspan}: "
            f"甲「{turned if turned_first else read}」 乙「{read if turned_first else turned}」"
            for cell, (read, turned) in zip(cells, readings))
        reply, _ = client.ask_answering(
            DIRECTION_SYSTEM,
            f"block={block}; policy={table_orientation}; grid={table.rows} x {table.cols}\n"
            f"定位／轉軸紀錄：{context or '以表格全部格位對照原圖；不能唯一定位就未定'}\n"
            f"現有表格：\n---\n{table_text}\n---\n"
            f"要核圖嘅格（row/col 由零開始）：\n{listing}\n"
            "按圖上實際字序回答，輸出含 row、col、choice、evidence 嘅 JSON array。",
            render_bytes, max_tokens, kind="cell-direction")
        return parse_visual_direction_answers(reply, cells)

    pending_err: PacketPending | None = None
    as_read_first: list[tuple[str | None, str]] = []
    turned_first: list[tuple[str | None, str]] = []
    if not render_bytes or client is None or table.problems:
        reason = ("no source image" if not render_bytes else "no visual reviewer" if client is None
                  else "source grid requires repair: " + "; ".join(table.problems))
        as_read_first = turned_first = [(None, reason)] * len(cells)
    else:
        for is_turned, sink in ((False, as_read_first), (True, turned_first)):
            try:
                sink.extend(ask(is_turned))
            except PacketPending as err:
                if pending_err is None:
                    pending_err = err
        if pending_err is not None:
            raise pending_err
    first_order = {"first": "as read", "second": "turned"}
    second_order = {"first": "turned", "second": "as read"}
    records: list[dict[str, Any]] = []
    texts = {(cell.row, cell.col): cell.text for cell in table.cells}
    for cell, (read, turned), one, two in zip(cells, readings, as_read_first, turned_first):
        verdicts = [first_order.get(one[0], one[0]), second_order.get(two[0], two[0])]
        outcome = ("turned" if verdicts == ["turned", "turned"]
                   else "kept" if verdicts == ["as read", "as read"] else "undecided")
        if outcome == "turned":
            texts[(cell.row, cell.col)] = turned
        records.append({**common, **({"block": block} if block is not None else {}),
                        "row": cell.row, "col": cell.col,
                        "rowspan": cell.rowspan, "colspan": cell.colspan,
                        "read": read, "turned": turned, "verdicts": verdicts,
                        "visualEvidence": [one[1], two[1]], "outcome": outcome})
    if not any(record["outcome"] == "turned" for record in records):
        return table_text, records
    fixed = _tables.with_texts(table, [texts[(cell.row, cell.col)] for cell in table.cells])
    return (_tables.render_html(fixed) if table.kind == "html" else _tables.render_markdown(fixed)), records


def cells_as_read(table_text: str, records: Sequence[Mapping[str, Any]]) -> str:
    """The table with every cell fix_cell_direction turned round put back as read.

    For the whole-page pass, which has no block spans to stand a token in and
    hands the formatter the recovered tables to copy.  Its deletion guard
    compares runs of the merged text, where a label printed right to left
    stands as the engines read it, so a table copied with its labels turned
    breaks those runs and the pass is refused as having deleted them.
    Measured with a stand-in formatter that copies the tables as the prompt
    tells it: a page whose eighteen labels were turned lost the pass and its
    retry, and was written as the bare merged text with the table missing.
    So the formatter copies the cells as read, and enforce_recovered_tables
    then writes the recovered table, turned cells and all, in their place
    (it matches a written table to a recovered one whichever way its cells
    read).  `records` are fix_cell_direction's for this table.
    """
    turned = {(record["row"], record["col"]): record["read"]
              for record in records if record.get("outcome") == "turned"}
    table = first_table(table_text) if turned else None
    if table is None:
        return table_text
    read = _tables.with_texts(table, [turned.get((cell.row, cell.col), cell.text) for cell in table.cells])
    return _tables.render_html(read) if table.kind == "html" else _tables.render_markdown(read)


def _covers(text: str, wanted: str, share: float = 0.8) -> bool:
    """Whether `text` contains most of `wanted`, allowing for reading-order shuffle."""
    if not wanted:
        return False
    matcher = difflib.SequenceMatcher(None, wanted, text, autojunk=False)
    matched = sum(b.size for b in matcher.get_matching_blocks())
    return matched >= len(wanted) * share


SINGLE_LINE_KEPT = "kept — a single line of cells"


def single_line_grid(table_text: str) -> bool:
    """Whether a recovered table is one row or one column of cells."""
    table = first_table(table_text)
    return table is not None and (table.rows == 1 or table.cols == 1)


def orientation_undecided(how: str) -> bool:
    """Whether an orientation note preserves a grid pending source review."""
    return "UNDECIDED" in how or how.startswith(SINGLE_LINE_KEPT) or "NO VERDICT PARSED" in how


def parse_table_visual_verdict(reply: str, directive: str) -> str | None:
    """Accept only the two-line visual contract, never a usable fragment.

    Searching for one well-formed line would overlook a second, malformed
    or qualified directive that contradicts it.  Extra prose/directives and
    empty evidence therefore leave the decision unresolved.
    """
    lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    pattern = ORIENT_LINE if directive == "RECORD" else FIRST_LINE
    verdict = pattern.fullmatch(lines[0])
    evidence = ORIENT_EVIDENCE_LINE.fullmatch(lines[1])
    return verdict.group(1).lower() if verdict is not None and evidence is not None else None


def table_orientation_record(before: str, after: str, how: str,
                             render_bytes: bytes | None, policy: str,
                             block: int | None = None) -> dict[str, Any]:
    """Keep the source grid and bind the selected representation to its image."""
    original, output = first_table(before), first_table(after)
    return {**({"block": block} if block is not None else {}),
            "policy": policy, "decision": how,
            "outcome": "undecided" if orientation_undecided(how) else "resolved",
            "inputText": before, "outputText": after,
            "inputShape": [original.rows, original.cols] if original else None,
            "outputShape": [output.rows, output.cols] if output else None,
            "inputSha256": sha256_bytes(before.encode("utf-8")),
            "outputSha256": sha256_bytes(after.encode("utf-8")),
            "sourceImageSha256": sha256_bytes(render_bytes) if render_bytes else None}


def orient_table(table_text: str, render_bytes: bytes | None, client: "Client | None",
                 max_tokens: int, turn: Callable[[str], tuple[str, str]] | None = None,
                 table_orientation: str = "auto", *, context: str = "") -> tuple[str, str]:
    """Select logical rows only when the source image establishes their axes.

    As-printed preserves the existing physical grid, including merged cells.
    Auto checks both axes and record order visually; arithmetic and OCR
    witnesses supply review clues only.  An isolated header strip can be
    oriented when the image/context establishes its table relationship,
    otherwise it stays explicitly undecided.  No missing/unknown answer
    falls back to a book-wide reading direction.
    """
    if table_orientation not in {"auto", "as-printed"}:
        raise ValueError(f"unknown table orientation policy: {table_orientation}")
    if table_orientation == "as-printed":
        return table_text, "kept — policy=as-printed; physical grid and spans preserved"
    table = first_table(table_text)
    if table is None:
        return table_text, "kept, UNDECIDED — policy=auto; table could not be parsed"

    def undecided(reason: str) -> tuple[str, str]:
        prefix = SINGLE_LINE_KEPT if single_line_grid(table_text) else "kept"
        return table_text, f"{prefix}, UNDECIDED — policy=auto; {reason}"

    if not render_bytes or client is None:
        return undecided("no source image" if not render_bytes else "no visual reviewer")
    if table.problems:
        return undecided("source grid requires repair: " + "; ".join(table.problems))
    axis = totals_axis(table_text)
    hint = f"數目核對線索：現有格仔 {axis} 軸加總吻合；呢個唔證明紀錄方向。" if axis else ""
    verdict, _ = client.ask_answering(
        ORIENT_SYSTEM,
        f"定位／相關表格上下文：{context or '只可用附圖中同一個表嘅證據'}\n"
        f"現有格仔 ({table.rows} x {table.cols})：\n---\n{table_text}\n---\n{hint}\n"
        "逐格對照原圖，再答 RECORD: row / column / unknown 同 EVIDENCE: 圖上依據。",
        render_bytes, max_tokens, kind="orient")
    axis = parse_table_visual_verdict(verdict, "RECORD")
    said = (verdict or "").strip().replace("\n", " ")[:300] or "(empty reply)"
    if axis not in {"row", "column"}:
        return undecided(said)
    if axis == "row":
        return table_text, f"kept — policy=auto; {said}"
    if turn is None:
        oriented, how = turn_records(table_text, None, ask=lambda grid: ask_first_record(
            grid, render_bytes, client, max_tokens, context=context))
    else:
        oriented, how = turn(table_text)
    if orientation_undecided(how):
        return undecided(f"axis=column; {how}; {said}")
    return oriented, f"{how} — policy=auto; {said}"


FIRST_RECORD_SYSTEM = """你係古籍表格判讀員。畀你原掃描頁圖，同埋現有格仔
（Markdown 或 HTML）。圖像覆核已確認要交換軸，令每列內容變成邏輯上嘅一行。
再逐格對圖確認：第一筆紀錄（或第一組欄名）喺現有格仔最左列，定最右列？
唔可以假定直排書一定由右至左、OCR 一定由左至右，亦唔可以單憑文字排列／語意決定。
要指出圖上原格位同現有格仔嘅對應；位置唔唯一、看唔清或欠相關續頁就答 unknown。
只輸出兩行：
FIRST: left
或 FIRST: right
或 FIRST: unknown
EVIDENCE: 指出原圖可見嘅閱讀次序同現有格仔位置；唔清楚就講缺少乜證據。"""

FIRST_LINE = re.compile(r"^\s*FIRST:\s*(left|right|unknown)\s*$", re.I | re.M)


def ask_first_record(table_text: str, render_bytes: bytes | None, client: "Client | None",
                     max_tokens: int, *, context: str = "") -> tuple[str | None, str]:
    """Read record order from the source image; missing evidence stays unknown."""
    if not render_bytes or client is None:
        return None, "no source image" if not render_bytes else "no visual reviewer"
    reply, _ = client.ask_answering(
        FIRST_RECORD_SYSTEM,
        f"定位／相關表格上下文：{context}\n格仔：\n---\n{table_text}\n---\n"
        "對住原圖答 FIRST: left / right / unknown 同 EVIDENCE: 圖上依據。",
        render_bytes, max_tokens, kind="record-order")
    first = parse_table_visual_verdict(reply, "FIRST")
    said = (reply or "").strip().replace("\n", " ")[:300] or "(empty reply)"
    return (first if first in {"left", "right"} else None), said


def turn_records(table_text: str, witness: str | None,
                 ask: Callable[[str], tuple[str | None, str]] | None = None) -> tuple[str, str]:
    """Exchange already-verified axes using a visually established record order.

    An OCR witness may suggest where to inspect but cannot authorize a
    transpose/rotation.  Missing or conflicting visual order preserves the
    original grid.  A single column needs no left/right choice: transposition
    and rotation have identical order in that case.
    """
    table = first_table(table_text)
    if table is None:
        return table_text, "kept, UNDECIDED — unparsed table"
    if table.cols == 1:
        return _tables.render(_tables.transpose(table)), "transposed (one column; no record-order ambiguity)"
    if ask is None:
        return table_text, "kept, UNDECIDED — no visual record-order decision"
    first, said = ask(table_text)
    if first == "left":
        return _tables.render(_tables.transpose(table)), f"transposed (visual first=left; {said})"
    if first == "right":
        return rotate_table(table_text), f"rotated (visual first=right; {said})"
    return table_text, f"kept, UNDECIDED — visual record order unknown; {said}"


TABLE_BRACKET_REACH = 4   # characters of engine A's reading either side that place a bracket in a cell


def table_brackets(table_text: str, sources: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """TABLE_TEXT, a table the formatter rebuilt from a region, with the round
    brackets engine A printed in the region (SOURCES, its readings of the
    region's blocks, marks and markup included) written back into the cells
    they stand in; and what was done: brackets written in, the model's own
    moved (place_brackets), and the ones no cell could be found for.

    A table block gets no punctuation pass, the structure pass may add no
    mark, and the rebuild was measured leaving out every bracket: a textbook
    table printing four brackets, all read by engine A, came back from all
    three accepted rebuilds with none.  Each
    bracket is placed by engine A's characters either side of it,
    TABLE_BRACKET_REACH each way, variants folded: where the two together
    stand once in the table's cells, the bracket goes between them; where
    they do not (the bracket at a cell's edge, or a character of the context
    read otherwise), an opening bracket by the characters after it and a
    closing one by those before it, when they stand once in one cell.  A
    bracket placed twice (engine A's reading repeats a run) is written once;
    one that no cell holds is reported, never guessed.  The characters are
    never touched."""
    table = first_table(table_text)
    done: dict[str, Any] = {"inserted": 0, "moved": 0, "unplaced": []}
    if table is None:
        return table_text, done
    cells = [variant_fold(CJK(cell.text)) for cell in table.cells]
    joined = "\0".join(cells)
    starts = list(itertools.accumulate([0] + [len(cell) + 1 for cell in cells]))[:-1]

    def spot(at: int) -> tuple[int, int] | None:
        index = bisect.bisect_right(starts, at) - 1
        offset = at - starts[index]
        return (index, offset) if 0 <= offset <= len(cells[index]) else None

    wanted: dict[int, Counter] = {}
    for source in sources:
        chars, gaps = engine_gaps(source)
        folded = variant_fold(chars)
        for g, gap in enumerate(gaps):
            for kind in bracket_kinds(gap):
                before = folded[max(0, g - TABLE_BRACKET_REACH):g]
                after = folded[g:g + TABLE_BRACKET_REACH]
                found = None
                if before and after and joined.count(before + after) == 1:
                    found = spot(joined.index(before + after) + len(before))
                elif kind == "（" and len(after) >= 2 and joined.count(after) == 1:
                    found = spot(joined.index(after))
                elif kind == "）" and len(before) >= 2 and joined.count(before) == 1:
                    found = spot(joined.index(before) + len(before))
                if found is None:
                    done["unplaced"].append(f"{chars[max(0, g - TABLE_BRACKET_REACH):g]}{kind}"
                                            f"{chars[g:g + TABLE_BRACKET_REACH]}")
                    continue
                wanted.setdefault(found[0], Counter())[(found[1], kind)] = 1
    if not wanted:
        return table_text, done
    texts = []
    for index, cell in enumerate(table.cells):
        if index in wanted:
            text, inserted, moved = place_brackets(cell.text, CJK(cell.text), wanted[index])
            done["inserted"] += inserted
            done["moved"] += moved
            texts.append(text)
        else:
            texts.append(cell.text)
    if not done["inserted"] and not done["moved"]:
        return table_text, done
    fixed = _tables.with_texts(table, texts)
    return (_tables.render_html(fixed) if table.kind == "html" else _tables.render_markdown(fixed)), done


# A note marker as an engine writes it inside a table cell: a LaTeX or HTML
# superscript, or a run of circled figures (recover_tables).
CELL_NOTE_MARKER = re.compile(r"\$\s*\^\{[^$]{1,12}\}\s*\$|<sup>[^<]{1,8}</sup>|[\u2460-\u2473\u3251-\u325f\u32b1-\u32bf\u24ea-\u24ff]+")


def recover_tables(
    blocks: Sequence[Mapping[str, Any]], resolved: str,
    render_bytes: bytes | None = None, client: "Client | None" = None,
    max_tokens: int = 16000, witness: str | None = None,
    heads: Sequence[str] = (), furniture: frozenset[str] | set[str] = frozenset(),
    spans: Sequence[str] | None = None, lines: Sequence[int] | None = None,
    table_orientation: str = "auto",
) -> tuple[list[str], list[dict[str, Any]], list[str], list[int], dict[str, Any]]:
    """Every block the engine structured as a table, as page markup, plus refusals.

    The engine solves tables that the character merge then destroys: align_drafts
    reduces both drafts to bare CJK, so <table><tr><td>物名</td>… arrives at the
    model as 物名件數約計價值黑板一二元… with the grid gone.  Recovering it here
    costs no model call, because the work was already done upstream.

    What engine_table vouches for comes back as a Markdown table; a table with
    merged cells is refused there and left to the formatter.  `witness` is the
    other engine's text for the page, `heads` and `furniture` the book's
    running heads and recurring n-grams (engine_table).  Returns the tables,
    the refusals, the orientation notes, the position in BLOCKS each table
    came from, and what the page's seal needs besides: `doubts` ({kind,
    detail}) and `figureGrouping` (figures that took engine B's group marks,
    restate_table), and `cellDirection` (the label cells asked which way they
    read, fix_cell_direction).  The refusals, notes, doubts and records name
    blocks by their layout index (layout_index), as everything sealed does.

    `spans` are the merged text's block spans (block_spans; None where the
    page has none).  A table is looked for in its own block's span, and
    `edges` in what is returned holds, by block position, each table whose
    block's span holds text before or after its cells: the stretches of the
    merged text (CJK offsets) before it (`lead`) and after it (`trail`), the
    table as restated (`restated`, before its orientation), and whether its
    characters were found only outside its block's span (`outside`).  The page
    writes the stretches beside the table (table_edges).  Found on made-up pages
    (check_tables, check_table_edge_text): a title only engine B read above
    a table - which block_spans hands to the block after it, the table - was
    in no cell, and the table's token stood for the block's whole span, so the
    title was on neither the table nor the page, with no doubt.  `lines` is
    engine B's line of each character of the merged text
    (engine_b_lines_merged): what engine B read with a table's first or last
    cell on one line or in one cell is that cell's (table_places).
    """
    recovered: list[str] = []
    rejected: list[dict[str, Any]] = []
    orientations: list[str] = []
    sources: list[int] = []
    sealed: dict[str, Any] = {"doubts": [], "figureGrouping": [], "cellDirection": [],
                              "tableOrientationEvidence": [], "edges": {}}
    span_at = (list(itertools.accumulate((len(CJK(text)) for text in spans), initial=0))
               if spans is not None else None)
    for index, block in enumerate(blocks):
        if block.get("label") != "table":
            continue
        # Sealed as the layout's block number; `sources` stays positional.
        layout = layout_index(blocks, index)
        table, why = engine_table(str(block.get("text") or ""), witness, heads, furniture)
        if not table:
            rejected.append({"block": layout, "reason": why})
            continue
        regrouped: list[str] = []
        held: list[tuple[int, int]] = []
        span = (span_at[index], span_at[index + 1]) if span_at is not None else None
        restated = restate_table(table, resolved, witness, regrouped, span=span, held=held, lines=lines)
        outside = False
        if restated is None and span is not None:
            # Not in its own block's span: looked for on the whole page, as it
            # was before, so that no table is refused for it.
            regrouped, held = [], []
            restated = restate_table(table, resolved, witness, regrouped, held=held, lines=lines)
            outside = restated is not None
        if restated is not None:
            lead, trail = table_edges(span, held[-1]) if span is not None else ((0, 0), (0, 0))
            if lead[1] > lead[0] or trail[1] > trail[0]:
                sealed["edges"][index] = {"lead": lead, "trail": trail, "restated": restated,
                                          "outside": outside}
        sealed["figureGrouping"] += [f"block {layout}: {note}" for note in regrouped]
        if restated is None:
            rejected.append({"block": layout,
                             "reason": "table text not found in the merged text"})
            continue
        markers = CELL_NOTE_MARKER.findall(restated)
        if markers:
            # A note marker in a cell is engine A's reading and nothing checks
            # it: the modern book prints circled three-figure note numbers
            # (199, 200) that engine A wrote as ①⑨ and ②⑩ and engine B as ⑲
            # and ⑳ (checked against the page images, 2 of 8 vouched rows).
            sealed["doubts"].append({"kind": "table-note-marker-unverified", "detail": (
                f"TABLE NOTE MARKERS UNVERIFIED: block {layout}: {len(markers)} note marker(s) in its cells as "
                f"engine A read them ({'、'.join(m.strip() for m in markers[:6])}{'…' if len(markers) > 6 else ''}); "
                "a circled number above 20 is often misread")})
        before_orientation = restated
        context = (f"block={layout}; page box (x,y,width,height)={block.get('box')}; "
                   f"source grid before orientation:\n{before_orientation}")
        try:
            restated, how = orient_table(
                restated, render_bytes, client, max_tokens,
                table_orientation=table_orientation, context=context)
            orientations.append(f"block {layout}: {how}")
            sealed["tableOrientationEvidence"].append(table_orientation_record(
                before_orientation, restated, how, render_bytes, table_orientation, block=layout))
            if orientation_undecided(how):
                sealed["doubts"].append({"kind": "table-orientation-undecided", "detail": (
                    f"TABLE ORIENTATION UNDECIDED: block {layout}: {how}")})
            restated, directions = fix_cell_direction(
                restated, client, max_tokens, block=layout, render_bytes=render_bytes,
                context=f"{context}\norientation decision: {how}", table_orientation=table_orientation)
            sealed["cellDirection"] += directions
            undecided = [record["read"] for record in directions if record["outcome"] == "undecided"]
            if undecided:
                sealed["doubts"].append({"kind": "cell-direction-undecided", "detail": (
                    f"CELL DIRECTION UNDECIDED: block {layout}: {len(undecided)} label cell(s) kept "
                    f"as read ({'、'.join(undecided[:6])}{'…' if len(undecided) > 6 else ''}); "
                    "source image evidence was absent, ambiguous or not agreed")})
        except PacketPending:
            sealed["packetPending"] = True
        recovered.append(restated)
        sources.append(index)
    return recovered, rejected, orientations, sources, sealed


def table_edges(span: tuple[int, int], held: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """The stretches of a table block's SPAN, (start, end) in the merged text,
    before and after HELD, the stretch its table's cells hold: the text the
    block's span holds and no cell of the table does, which the page writes
    as text before and after the table (restate_table's edges).  A table held
    wholly outside SPAN leaves the whole span on the side it is not on."""
    s_lo, s_hi = span
    t_lo, t_hi = held
    lead = (s_lo, min(max(t_lo, s_lo), s_hi))
    return lead, (max(min(t_hi, s_hi), lead[1]), s_hi)


def edge_copies(merged: str, lo: int, hi: int, draft: str, draft2: str | None,
                origin: Sequence[str] | None = None) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], the edge text of a table (table_edges), written
    beside the table, would be an extra copy: text the page already holds
    where the other engine read it.  The stretches of DROP_GRAM characters
    (the text itself when it is shorter) that MERGED, the merged text with
    the edge text in it, holds more often than either engine's reading does
    - the count extra_copies takes of a page, variant-folded - and that
    stand elsewhere on the page (outside LO:HI) as engine A's reading alone,
    cover more than MOVED_SHARE of its characters.  Returns that evidence,
    or None.

    Engine A's reading alone: more than half the characters of a copy
    elsewhere are ones the merge wrote from engine A's reading where engine
    B read nothing or something else (ORIGIN, merged_origins; without it
    the count alone decides).  The count alone also fires where engine B
    misread the copy elsewhere: its reading then holds the stretch once too
    few.  On a made-up page (check_tables,
    check_table_edge_text_misread_elsewhere) a title only engine B read
    restated two of the table's column labels, engine B read one of those
    labels with one character wrong in its cells, and the title was held
    back as an extra copy of the labels (57%), under 裁決 too, which had
    answered it printed: engine B read the labels where they stand, so the
    title is no reading of them.

    A one-engine reading at a block boundary goes to the next block
    (block_spans), and where that is a table, what an engine read of the page
    in another order lands at its edge.  Measured on the corpus's 48 pages
    with a table block in engine A's layout, replayed offline (both engine B
    versions, 裁決 answered from the sealed runs): on two pages of one book
    (its second scan, engine B v1.0) the edge text was engine B's reading of
    what engine A read elsewhere on the page - the running foot and its
    folio, which engine A read as its footer and number blocks (a share of
    0.67), and the folio with the table's own column labels, which engine A
    read in its cells (0.75).  The merge held both because 裁決 answered 無
    there and D16 refused the answer (engine A's copy is beyond its reach);
    before the edge text was written, the table's token dropped them.  Text
    read in another order is placed once
    by D2 (place_moved_once), but not a run shorter than MOVE_MINIMUM: on a
    made-up page (check_tables, check_table_edge_text_read_by_a) a
    two-character title engine A read below the table and engine B above it
    came out twice.  A title only engine B read, the made-up page the rule is
    for (check_table_edge_text), has none of its stretches held so: 0.  A
    stretch of figures alone is no such evidence (extra_copies), nor is a
    single character."""
    text = merged[lo:hi]
    if len(text) < 2:
        return None
    folded, page = variant_fold(text), variant_fold(merged)
    readings = (variant_fold(CJK(draft)), variant_fold(CJK(draft2 or "")))
    size = min(DROP_GRAM, len(text))

    def read_by_a_alone(gram: str) -> bool:
        """Whether GRAM stands outside LO:HI where engine A's reading alone
        wrote most of it."""
        if origin is None:
            return True
        at = page.find(gram)
        while at >= 0:
            if (at + size <= lo or at >= hi) and 2 * sum(origin[at + j] == "a" for j in range(size)) > size:
                return True
            at = page.find(gram, at + 1)
        return False

    held = bytearray(len(text))
    stretches: list[str] = []
    for i in range(len(text) - size + 1):
        gram = folded[i:i + size]
        if set(gram) <= NUMERAL_CHARS:
            continue
        counts = (page.count(gram), *(reading.count(gram) for reading in readings))
        if counts[0] > max(counts[1:]) and read_by_a_alone(gram):
            held[i:i + size] = b"\1" * size
            stretches.append(f"{text[i:i + size]} {counts[0]}/{counts[1]}/{counts[2]}")
    share = sum(held) / len(text)
    return ({"share": round(share, 2), "stretches": stretches[:12]} if share > MOVED_SHARE else None)


def engine_b_table_lines(text: str) -> set[int]:
    """The lines of engine B's reading TEXT, numbered as
    engine_b_lines_merged numbers them, that are cells of a table in it
    (Markdown or HTML markup).  For the table-edge-text doubt: engine B may
    read the text beside a table in its own table's cells - its reading of
    the table's first cells, which engine A read differently - and the doubt
    says so.  On a made-up page (check_tables,
    check_table_edge_text_read_in_cells) engine A misread the table's first
    three characters, engine B read a title above the table and those
    cells, the merge kept both readings (a replacement too uneven to pair),
    and engine B's reading of the cells was written above the table with
    the title, the doubt saying only "written"."""
    spans = [(table.start, table.end) for table in _tables.find_tables(text)]
    out: set[int] = set()
    if not spans:
        return out
    number, count, gap = 0, 0, ""
    for offset, char in enumerate(text):
        if not CJK(char):
            gap += char
            continue
        if count and TEXT_SEAMS.search(gap):
            number += 1
        if any(lo <= offset < hi for lo, hi in spans):
            out.add(number)
        count, gap = count + 1, ""
    return out


def edge_judged(text: str) -> bool:
    """Whether edge_copies can judge TEXT: two characters or more, and a
    stretch of them not all figures."""
    size = min(DROP_GRAM, len(text))
    return len(text) >= 2 and any(not set(text[i:i + size]) <= NUMERAL_CHARS
                                  for i in range(len(text) - size + 1))


def block_copy(merged: str, lo: int, hi: int, draft: str, draft2: str | None, origin: Sequence[str] | None,
               blocks_at: Sequence[tuple[int, int, int]]) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], a piece of a table's edge text, is the whole
    of a block of engine A's elsewhere on the page - BLOCKS_AT, (layout
    number, start, end) in MERGED - that the merge wrote as engine A's
    reading alone (ORIGIN, merged_origins: more than half its characters),
    and the page holds it more often than either engine read it: engine B's
    reading of that block, read in another place.  Returns that evidence,
    or None.

    Figures alone and two characters, which edge_copies' stretches cannot
    weigh (a pair of numerals says nothing of order): on a made-up page
    (check_tables, check_table_edge_text_whole_block) engine A read the folio
    十二 as its number block, engine B read it after a table that ends the
    page, and the folio was written twice; a prose block there keeps it
    twice too (D2 never places a run of figures).  The whole block, standing
    where engine B read nothing, is the evidence a stretch of figures
    lacks."""
    text = variant_fold(merged[lo:hi])
    if len(text) < 2 or origin is None:
        return None
    for layout, start, end in blocks_at:
        if (end - start != len(text) or not (end <= lo or start >= hi)
                or variant_fold(merged[start:end]) != text
                or 2 * sum(origin[p] == "a" for p in range(start, end)) <= end - start):
            continue
        counts = (variant_fold(merged).count(text), variant_fold(CJK(draft)).count(text),
                  variant_fold(CJK(draft2 or "")).count(text))
        if counts[0] > max(counts[1:]):
            return {"share": 1.0, "stretches": [f"{merged[lo:hi]} {counts[0]}/{counts[1]}/{counts[2]}"],
                    "wholeBlock": layout}
    return None


def cells_copy(merged: str, lo: int, hi: int, draft: str, draft2: str | None, cells: str) -> dict[str, Any] | None:
    """Whether MERGED[LO:HI], a piece of a table's edge text, is engine B's
    second reading of the table's own cells: it stands whole in CELLS (the
    table's characters in reading order, across cells and rows), it is not
    figures alone, it is DROP_GRAM characters or more, and engine B read it
    more often than engine A, who read it (and the page holds it more often
    than engine A read it).  Returns that evidence, or None.

    edge_copies does not see it: engine B read it twice, so the page holds
    it no more often than engine B read it, and the copy in the cells is
    both engines' reading.  On a made-up page (check_tables,
    check_table_edge_text_cells_again) engine B read the table's last two
    rows again after a table that ends the page; the merge keeps a run only
    one engine read under every preference, and the rows were written again
    after the table.  Whether a page prints a table's cells twice the text
    cannot tell - so it is a doubt, never a silent drop; a table's title
    that is nothing but its column labels in order would be held back too."""
    text = variant_fold(merged[lo:hi])
    if len(text) < DROP_GRAM or set(text) <= NUMERAL_CHARS or text not in variant_fold(cells):
        return None
    counts = (variant_fold(merged).count(text), variant_fold(CJK(draft)).count(text),
              variant_fold(CJK(draft2 or "")).count(text))
    if counts[2] > counts[1] >= 1 and counts[0] > counts[1]:
        return {"share": 1.0, "stretches": [f"{merged[lo:hi]} {counts[0]}/{counts[1]}/{counts[2]}"],
                "tableCells": True}
    return None


def edge_verdicts(merged: str, lo: int, hi: int, draft: str, draft2: str | None,
                  origin: Sequence[str] | None = None, lines: Sequence[int] | None = None,
                  blocks_at: Sequence[tuple[int, int, int]] = (), cells: str = "") -> list[dict[str, Any]]:
    """The edge text MERGED[LO:HI] of a table, piece by piece, each piece
    with edge_copies' verdict: {"lo", "hi", "judged", "held"} - "held" the
    evidence that it is an extra copy (not written), or None (written).

    A piece is what engine B read on one line or in one table cell (LINES,
    engine_b_lines_merged: each merged character's line of engine B's; the
    characters of no line of engine B's make pieces of their own), or the
    whole stretch without LINES.  Judged whole, a stretch of engine B's
    reading of text engine A read elsewhere and a line only engine B read
    beside it went together: on a made-up page (check_tables,
    check_table_edge_text_pieces) engine B read, after a table that ends
    the page, a unit line only it read and then the running foot, which
    engine A read as its footer block (D2 does not place a run whose pairs
    include two figures); the foot was 60% of the stretch, and the unit line
    was not written with it, the doubt blaming it on a reading elsewhere.

    A piece that is the whole of a block engine A alone read elsewhere
    (BLOCKS_AT, block_copy) is held back too, figures and all, and so is one
    that is engine B's second reading of the table's own cells (CELLS, the
    table's characters: cells_copy).  A piece edge_copies cannot judge
    (edge_judged: one character, or figures alone) and neither of those
    holds goes with the nearest piece before it on the same side that was
    judged, else the nearest after it, and is written where there is none.
    Measured on the corpus's 48 pages with a table block in engine A's
    layout, replayed offline: the two pages whose edge text was held back
    (edge_copies) each hold one - a folio's first digits on a line after
    the running foot, in one cell of engine B's (the foot held back, 0.80),
    and the end of engine B's line of the running head and folio, before
    the column labels it read again (each label 1.00).  The digits alone
    say nothing, and written they were furniture."""
    bounds = [lo]
    if lines is not None:
        bounds += [p for p in range(lo + 1, hi) if lines[p] != lines[p - 1]]
    bounds.append(hi)
    parts = []
    for start, end in zip(bounds, bounds[1:]):
        judged = edge_judged(merged[start:end])
        held = edge_copies(merged, start, end, draft, draft2, origin) if judged else None
        # The whole of a block engine A alone read elsewhere (block_copy):
        # figures too, which the stretches cannot weigh.  Or engine B's
        # second reading of the table's own cells (cells_copy).
        held = (held or block_copy(merged, start, end, draft, draft2, origin, blocks_at)
                or cells_copy(merged, start, end, draft, draft2, cells))
        parts.append({"lo": start, "hi": end, "judged": judged or held is not None, "held": held})
    for k, part in enumerate(parts):
        if part["judged"]:
            continue
        near = (next((q for q in reversed(parts[:k]) if q["judged"]), None)
                or next((q for q in parts[k + 1:] if q["judged"]), None))
        if near is not None and near["held"] is not None:
            part["held"] = {"share": near["held"]["share"], "stretches": [],
                            "with": merged[near["lo"]:near["hi"]]}
    return parts


def merged_other_readings(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str]) -> list[str]:
    """For each character of the merged text ("".join(pieces)), the other
    engine's character there where the engines read one character for one
    and the merge wrote one of them (the D20 lock lets a rebuilt table write
    the other), and "" elsewhere or where the two are one character."""
    out: list[str] = []
    for seg, piece in zip(segments, pieces):
        a, b = seg["a"], seg["b"]
        if seg["tag"] != "equal" and len(a) == len(b) == len(piece) and piece in (a, b):
            out += [y if y != x else "" for x, y in zip(piece, b if piece == a else a)]
        else:
            out += [""] * len(piece)
    return out


# The shortest run of a rebuilt table's characters matched in order with its
# region's merged text that places the table there (rebuilt_edges): twice
# DROP_GRAM.  A title shares words with its table's rows - on the made-up
# page of check_table_edge_text_rebuilt three characters - and a run that
# short, met by chance where the rebuild wrote the rows in another order,
# would cut the title there.
REBUILT_ANCHOR = 6


def rebuilt_edges(table: str, region: str, other: Sequence[str], origin: Sequence[str],
                  first: int, last: int) -> tuple[int, int]:
    """How many characters at the start and at the end of REGION - a rebuilt
    table's region's merged text, its blocks' spans joined - the table's
    characters TABLE (as the rebuild answered, in its order) do not hold:
    its edge text, from the region's first character on within its first
    block's span (FIRST characters), and back from its last within its last
    block's (LAST).

    A character of REGION is the table's where the table holds it: matched
    in order in a run of REBUILT_ANCHOR characters or more; or where the
    table wrote, one for one in its place, the other engine's reading of it
    (OTHER, merged_other_readings - the D20 lock lets a rebuild write it);
    or, else, counted: while REGION holds the character more often than the
    table does, less the characters so written, it is not the table's.  All
    variant-folded.  And a character of engine A's reading alone (ORIGIN,
    merged_origins) is the table's too: a rebuilt table stands for engine
    A's block, and what engine A alone read in it and the rebuild left out
    is its reading of the table, which the rebuilt-unvouched doubt names -
    the text a table's edge holds is text engine A did not read in its
    block (block_spans hands engine B's reading at a block's edge to the
    block after it).  Measured offline on the benchmark (the runs whose
    order-conflict page has a grid refused for copies and rebuilt from
    engine B's lines, with a stand-in rebuild that writes only the
    characters it must, the first of each): the window merge wrote engine
    A's second copy of a character after the block's last window, and it
    was written after the table on a line of its own though the page
    prints it once, inside the table.

    Counted alone, as the rule first counted, from the first character on
    while the count held, the walk went by characters, not by their places.  On
    made-up pages (check_tables, check_table_edge_text_rebuilt_places): a
    title only engine B read above a nominated block, 各種讀本年用法一覽表,
    and a rebuild that wrote engine A's 年 where the merge wrote engine B's
    季 - the table's 年 used up the title's, the walk stopped there, and
    年用法一覽表 was lost while the doubt said 各種讀本 was written; the
    region's first character, 弟 as the merge wrote it, which the rebuild
    wrote as engine B's 第, was written again on a line of its own; and a
    character engine B read once more in the middle of the first row, one
    the rebuild left out, was taken for the region's first character, which
    the table's first cell holds, and written twice."""
    t, r = variant_fold(table), variant_fold(region)
    anchored, written = bytearray(len(r)), bytearray(len(r))
    held, count = Counter(t), Counter(r)
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, t, r, autojunk=False).get_opcodes():
        if tag == "equal" and a2 - a1 >= REBUILT_ANCHOR:
            anchored[b1:b2] = b"\1" * (b2 - b1)
        elif tag == "replace" and a2 - a1 == b2 - b1:
            for k in range(a2 - a1):
                if other[b1 + k] and variant_fold(other[b1 + k]) == t[a1 + k] != r[b1 + k]:
                    written[b1 + k] = 1
                    held[t[a1 + k]] -= 1
                    count[r[b1 + k]] -= 1
    left = count - held

    def beside(p: int) -> bool:
        """Whether REGION's character P is none of the table's."""
        return not anchored[p] and not written[p] and origin[p] != "a" and left[r[p]] > 0

    x = 0
    while x < first and beside(x):
        left[r[x]] -= 1
        x += 1
    y = len(r)
    while y > max(x, len(r) - last) and beside(y - 1):
        left[r[y - 1]] -= 1
        y -= 1
    return x, len(r) - y


def merged_origins(segments: Sequence[Mapping[str, Any]], pieces: Sequence[str]) -> list[str]:
    """Whose reading each character of the merged text ("".join(pieces))
    is, where it stands: "both" where the two engines read that character
    there (agreed, or one for one in another variant form), "a" where the
    merge wrote engine A's reading and engine B read nothing or something
    else there, "b" the same the other way round, and "" where the merge
    wrote neither reading as it stands (裁決's third reading).  For
    edge_copies: a copy elsewhere is engine A's reading alone; and for
    rebuilt_edges.  A segment of an order-conflict table block read from
    engine B's lines (window_merge) may stand "equal" with one side empty:
    it is the reading of the side that has it."""
    out: list[str] = []
    for seg, piece in zip(segments, pieces):
        a, b = seg["a"], seg["b"]
        if seg["tag"] == "equal" and a and b:
            out += ["both"] * len(piece)
        elif len(a) == len(b) == len(piece) and piece in (a, b):
            side = "a" if piece == a else "b"
            out += ["both" if variant_fold(x) == variant_fold(y) else side for x, y in zip(a, b)]
        else:
            out += [("a" if piece == a else "b" if piece == b else "")] * len(piece)
    return out


def crop_block(render: Path, box: Sequence[float], pad: float = 0.01,
               pad_left: float | None = None) -> bytes | None:
    """Crop a normalised [x, y, w, h] top-left-origin box out of the render.

    `pad_left` overrides the padding on the left edge alone: in vertical
    setting a mark sits at the lower right of its character, so the marks of
    the column to the LEFT of a block lie in the gutter against the block's
    left edge, and ordinary padding pulls them into frame (measured: a
    four-character heading came back with a circle at each end).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(render) as image:
            width, height = image.size
            x, y, w, h = box
            left = max(0, int((x - (pad if pad_left is None else pad_left)) * width))
            top = max(0, int((y - pad) * height))
            right = min(width, int((x + w + pad) * width))
            bottom = min(height, int((y + h + pad) * height))
            if right - left < 8 or bottom - top < 8:
                return None
            buffer = io.BytesIO()
            image.crop((left, top, right, bottom)).save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a bad crop must not kill the page
        return None


def nominate_tables(blocks: Sequence[Mapping[str, Any]], witness: str | None) -> list[dict[str, Any]]:
    """Blocks the layout model did not call tables that engine B reads as one.

    A table read as running text comes out of each engine in its own order -
    one reads the records row by row, the other column by column - while
    prose is read in one order by both.  So a block of text (not furniture)
    long enough to hold two anchors, which witness_refusal - the block taken
    as one cell - finds engine B reading in another order, is nominated.
    Measured on the merge-mode pages of the corpus (3,305 blocks of 16 or
    more characters): 11 are nominated; 4 are tables (a ruled textbook
    table, two unruled rosters, a list of coin weights) and 7 are prose,
    several of them facsimile blocks that cross the fold between two
    leaves.  (An earlier count, before two books were added, had 0 prose
    blocks in 1,107.)  So a nomination is a question, not a verdict: the
    rebuild asks the page (rebuild_table), and the model may answer that it
    is not a table.  Ruled lines do not settle it either: measured on these
    crops, text columns and grey scans give interior rules as often as the
    tables do.  Order-conflict pages are not nominated at all: there
    the engines read the whole page in different orders, so one block read
    in another order says nothing about that block.
    """
    nominated = []
    for index, block in enumerate(blocks):
        text = CJK(str(block.get("text") or ""))
        if block.get("label") in FURNITURE_LABELS or len(text) < 2 * WITNESS_ANCHOR:
            continue
        why = witness_refusal(_tables.Table("markdown", 1, 1, [_tables.Cell(0, 0, 1, 1, text)]), witness)
        if why and "another order" in why:
            nominated.append({"block": index, "label": block.get("label"), "characters": len(text),
                              "signal": why.replace("row 1 column 1", "the block")})
    return nominated


def engine_b_as_settled(x: Mapping[str, Any], a_at: int, b_at: int,
                        streams: tuple[tuple[str, list[str]], tuple[str, list[str]]] | None,
                        marks: tuple[tuple[str, list[str]], tuple[str, list[str]], Mapping[str, Any]] | None,
                        ) -> str:
    """Engine B's side of one disagreement without the characters the page's
    rules take for engine A's printed marks: the bracket rule's numerals
    (bracket_explanation, when STREAMS - engine_gaps() of each page - are
    given) and the mark rule's glyphs (decision D1, mark_explanation, when
    MARKS - engine_marks() of each page and the book's mark_aliases - are).
    What is left is a subsequence of engine B's reading.  A circle taken for
    a mark is kept: circle_withheld may keep it as a zero."""
    b = x["b"]
    if x["tag"] == "equal" or not b or x.get(LOOPED_READING) or "window" in x:
        return b
    seg = x
    if streams is not None and is_numeral_conflict(dict(seg)):
        found = bracket_explanation(seg, a_at, b_at, *streams)
        if found and found[0] == "A":
            return found[1]
        if found and found[0] == "reduced":
            # The walk asks about what the brackets leave, and the mark rule
            # finds nothing on a reduced reading (its characters are no longer
            # engine B's at B_AT), as here.
            return found[1]["b"]
    if marks is not None:
        found = mark_explanation(seg, a_at, b_at, *marks)
        if found and found[3] == "B" and not any(char in CIRCLE_CHARS for char, _ in found[2]):
            return found[1] if found[0] in ("A", "B") else found[1]["b"]
    return b


def marks_left_out(segments: Sequence[Mapping[str, Any]], starts: Sequence[int], b_starts: Sequence[int],
                   chars_b: str, settled: Sequence[str]) -> tuple[str, int]:
    """Engine B's reading (CHARS_B, its characters) with the characters the
    page's rules take for engine A's printed marks left out (SETTLED, each
    segment's engine-B side as engine_b_as_settled leaves it), and how many
    were left out: the witness nominate_tables reads a block against
    (decision D21).

    A witness piece is WITNESS_ANCHOR characters engine B read without a
    break, and a glyph engine B writes for a printed mark (丶 for a full-cell
    ，) breaks every piece it falls in.  Measured on a magazine's price table
    printed in a ruled box, which the layout model called text: engine B
    wrote the comma in its nine-character note as 丶, so the note held no
    piece and the block one anchor where two in the other order make a
    nomination, and the table was not nominated - on a page where the mark
    rule settled 丶 as a mark 33 times.  Only this page's settled sites are left out,
    never a list of glyphs."""
    drop = [False] * len(chars_b)
    for x, b_at, kept in zip(segments, b_starts, settled):
        if kept == x["b"] or chars_b[b_at:b_at + len(x["b"])] != x["b"]:
            continue
        # What the rules keep of engine B's reading is a subsequence of it;
        # the rest is what they took for marks.
        j = 0
        for k, char in enumerate(x["b"]):
            if j < len(kept) and kept[j] == char:
                j += 1
            else:
                drop[b_at + k] = True
    return "".join(char for char, gone in zip(chars_b, drop) if not gone), sum(drop)


def settle_by_union(segments: Sequence[Mapping[str, Any]], indices: Sequence[int],
                    elsewhere: str = "") -> dict[int, str]:
    """The pieces for a block's order disagreements: moved text once.

    Where engine B reads a block in another order, what the alignment calls
    a disagreement there is mostly text the two engines put in different
    places: a run deleted here is the run inserted there.  Settled one by one
    (a preference or 裁決 per disagreement) that text is written twice, or
    dropped where 裁決 answers "nothing here" - measured on a missed table,
    裁決 deleted runs both engines read (a book title with its figures, and
    part of a header).

    Text counts as moved only by runs.  A run engine B read in the block (an
    insertion, or its side of a replacement) is engine A's text when it
    stands whole in engine A's side of these disagreements, or by pieces of
    WITNESS_ANCHOR or more (variants folded; each stretch of engine A's is
    matched once, engine B's longest runs first).  And engine A's run only
    stands for text engine B read in the block when engine B did not read it
    `elsewhere` - engine B's reading of the page outside the block - whole
    or by such a piece.  Characters are not matched one by one: pooled that
    way, a genuine insertion lost every character that also occurred in an
    unrelated run of engine A whose own engine-B copy the alignment had put
    outside the block (measured on a roster: a seven-character post engine
    A missed kept four characters, the other three taken by a name and post
    of engine A's that engine B read at the running head), and 裁決 was
    never asked.

    Every disagreement settled here keeps engine A's reading in place, and
    the part of engine B's run not matched is added after it.  A run of
    engine B's with nothing matched is not shown to be moved text: it is
    left to the usual way (a preference or 裁決) when engine A read nothing
    there, or when none of engine A's text there was matched either.
    Returns the piece of each disagreement settled here; the ones left to
    the usual way are not in it.
    """
    far = variant_fold(elsewhere)
    far_matcher = difflib.SequenceMatcher(None, "", far, autojunk=False)
    owner: list[int | None] = []
    pool: list[str] = []
    for i in indices:
        text = variant_fold(segments[i]["a"])
        if not text or (far and (text in far or witness_anchors(text, far, far_matcher, unique=False))):
            continue
        pool += list(text) + ["\0"]
        owner += [i] * len(text) + [None]
    matched_a: set[int | None] = set()
    held: dict[int, list[bool]] = {}
    matcher = difflib.SequenceMatcher(None, "", "", autojunk=False)

    def take(b_at: int, a_at: int, size: int, flags: list[bool]) -> None:
        flags[b_at:b_at + size] = [True] * size
        pool[a_at:a_at + size] = ["\0"] * size
        matched_a.update(owner[a_at:a_at + size])

    for i in sorted((i for i in indices if segments[i]["b"]), key=lambda i: (-len(segments[i]["b"]), i)):
        run = variant_fold(segments[i]["b"])
        flags = held[i] = [False] * len(run)
        at = "".join(pool).find(run)
        if at >= 0:
            take(0, at, len(run), flags)
            continue
        probe = list(run)
        while True:
            matcher.set_seqs("".join(probe), "".join(pool))
            match = matcher.find_longest_match(0, len(probe), 0, len(pool))
            if match.size < WITNESS_ANCHOR:
                break
            probe[match.a:match.a + match.size] = ["\1"] * match.size
            take(match.a, match.b, match.size, flags)
    pieces = {}
    for i in indices:
        a, b = segments[i]["a"], segments[i]["b"]
        flags = held.get(i, [])
        if b and not any(flags) and (not a or i not in matched_a):
            continue
        pieces[i] = a + "".join(char for char, flag in zip(b, flags) if not flag)
    return pieces


def engine_b_outside(segments: Sequence[Mapping[str, Any]], starts: Sequence[int],
                     b_starts: Sequence[int], lo: int, hi: int) -> str:
    """Engine B's reading of the page outside the block at [LO, HI) of engine
    A's stream, its stretches apart by "\0": settle_by_union's `elsewhere`.

    The block's own stretch of engine B is what its segments hold there - the
    part of an agreed segment inside the block, and each disagreement inside
    it - and engine B's text is laid out in engine B's order, each segment at
    its B_STARTS.  Without text placed at engine A's site (place_moved_once)
    that is the segments' order, and the block's stretch one piece of it.  With
    it, a run engine B read elsewhere and D2 placed in the block is where
    engine B read it: marked as the block's there, and the text between left
    outside; and a run D2 took out of the block's stretch to another site
    leaves nothing in the stretch.  (Joining the segments' engine-B text in
    their order, as this was first done, cut engine B's reading at engine B
    positions of a text in another order.)  Engine B's reading of a set-aside
    looped block (quarantine_looped_block) is that block's, outside this one.
    """
    total = max((b + len(x["b"]) for x, b in zip(segments, b_starts)), default=0)
    text = [""] * total
    for x, b in zip(segments, b_starts):
        text[b:b + len(x["b"])] = list(x["b"])
    taken = [False] * total
    cuts: set[int] = set()
    for x, a_at, b_at in zip(segments, starts, b_starts):
        if x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if x["tag"] == "equal":
            if not (a_at < hi and a_end > lo):
                continue
            first, last = b_at + max(lo, a_at) - a_at, b_at + min(hi, a_end) - a_at
        elif (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi):
            first, last = b_at, b_at + len(x["b"])
        else:
            continue
        taken[first:last] = [True] * (last - first)
        cuts.add(first)
    out: list[str] = []
    for p in range(total + 1):
        if (p in cuts or (p < total and taken[p])) and (not out or out[-1] != "\0"):
            out.append("\0")
        if p < total and not taken[p]:
            out.append(text[p])
    return "".join(out)


_FOLD: dict[str, str] | None = None


def variant_fold(text: str) -> str:
    """Each character as one representative of its variant class: the user's
    pairs ruled one character, then OpenCC's allographs unless the user ruled
    the pair apart.  For finding where two readings of the same print meet
    (爲 in one engine and 為 in the other break every anchor across them),
    never for writing: the characters written are the engines'."""
    global _FOLD
    if _FOLD is None:
        ruled = user_variant_rulings()
        pairs = [tuple(pair) for pair, same in ruled.items() if same and len(pair) == 2]
        pairs += [tuple(pair) for pair in opencc_allographs() if len(pair) == 2 and pair not in ruled]
        parent: dict[str, str] = {}

        def root(char: str) -> str:
            while parent.get(char, char) != char:
                char = parent[char]
            return char

        for x, y in pairs:
            rx, ry = root(x), root(y)
            if rx != ry:
                parent[max(rx, ry)] = min(rx, ry)
        _FOLD = {char: root(char) for char in parent}
    return "".join(_FOLD.get(char, char) for char in text)


def window_merge(block: Mapping[str, Any], witness: str, heads: Sequence[str] = (),
                 furniture: frozenset[str] | set[str] = frozenset(),
                 elsewhere: str = "") -> list[dict[str, Any]] | None:
    """Engine A's refused table block, read against engine B's lines, as segments.

    On an order-conflict page the whole page is engine A's reading, and a
    table block of it that engine B contradicts (witness_refusal: a cell
    spliced or copied; furniture_row: the running head as a row) went into
    the page as the spliced text the refusal had rejected.  Measured on a
    textbook table's continuation page: engine A's grid carried the running
    head as a row and one twenty-character run three times, while engine B
    read the notes once each, in print order, and read the page's rare
    form of one character as another rare codepoint where A has a common
    character.

    So the block is rebuilt from engine B's lines: every line of B that
    shares a piece of WITNESS_ANCHOR or more with the block (or lies between
    two that do) is aligned with the stretch of engine A its anchors fall in
    - a window - and the window's disagreements become segments like any in
    merge mode, so a substitution there (a numeral, a rare codepoint) reaches
    裁決 or the preference.  Engine A's text outside every window is kept
    when engine B read no piece of it (labels and figures B never read) and
    dropped when B read it in another window (a copy); the furniture row is
    dropped.  Returns the segments, each character of engine A's block in
    exactly one of them (so the page's positions in A's stream hold), the
    windows in engine B's order and the rest after them in A's; or None
    when the block is not a grid whose cells account for its text, or B
    has no line in it.  `elsewhere` is engine A's reading of the rest of
    the page (CJK, the parts apart by "\\0"), which says where a line of
    engine B's runs on out of the block.
    """
    grid = _tables.parse_html_table(str(block.get("text") or ""), source_breaks=True)
    if grid is None:
        return None
    grid = _tables.simplify(grid)
    a_text = CJK(str(block.get("text") or ""))
    if "".join(CJK(cell.text) for cell in grid.cells) != a_text:
        return None
    head = furniture_row(grid, heads, furniture)
    in_head = [cell.row == head for cell in grid.cells for _ in CJK(cell.text)]
    body_at = [i for i, flag in enumerate(in_head) if not flag]
    owner = [n for n, cell in enumerate(grid.cells) for _ in CJK(cell.text)]
    body_cell = [owner[i] for i in body_at]
    cell_positions: dict[int, list[int]] = {}
    for position, cell in enumerate(body_cell):
        cell_positions.setdefault(cell, []).append(position)
    body = "".join(a_text[i] for i in body_at)
    lines = [CJK(line) for line in (witness or "").splitlines()]
    lines = [line for line in lines if line]
    # Anchors are found on the variant-folded texts (same lengths, so the
    # offsets hold for the texts as read).
    folded_body = variant_fold(body)
    anchors = [witness_anchors(variant_fold(line), folded_body, unique=False)
               if len(line) >= WITNESS_ANCHOR else [] for line in lines]
    anchored = [i for i, found in enumerate(anchors) if found]
    if not anchored:
        return None
    first, last = anchored[0], anchored[-1]
    claimed = [False] * len(body)
    windows: list[dict[str, Any]] = []
    far = variant_fold(elsewhere)
    far_matcher = difflib.SequenceMatcher(None, "", far, autojunk=False)
    for i in range(first, last + 1):
        line = lines[i]
        runs: list[list[tuple[int, int, int]]] = []
        for anchor in anchors[i]:
            if runs and anchor[1] > runs[-1][-1][1]:
                runs[-1].append(anchor)
            else:
                runs.append([anchor])
        if not runs:
            windows.append({"b": line, "lo": None, "hi": None})
            continue
        # Engine B writes a whole table as one line, with whatever it read
        # next to it (a caption, the next table, the prose after it).  Past
        # the line's outermost anchors, a piece engine A read in another
        # block of the page is that block's text: the line is cut after the
        # last such piece before its first anchor and before the first one
        # after its last anchor.  Measured on a year table whose slices ran
        # to the line's ends: the sentence after the table and the whole of
        # the next table were written into it, each a second time.  What
        # engine B read there that engine A read nowhere stays, as before.
        # (Tried on made-up year tables: cutting at the outermost anchors'
        # own cells dropped the first or last cell of a line wherever engine
        # A had read that cell as another.)
        head, tail = 0, len(line)
        if far:
            start_at, end_at = runs[0][0][0], runs[-1][-1][0] + runs[-1][-1][2]
            before = witness_anchors(variant_fold(line[:start_at]), far, far_matcher, unique=False)
            after = witness_anchors(variant_fold(line[end_at:]), far, far_matcher, unique=False)
            head = max((offset + size for offset, _, size in before), default=0)
            tail = end_at + min((offset for offset, _, _ in after), default=len(line) - end_at)
        # A line whose anchors go backwards in engine A crosses a splice:
        # each forward run is its own window, cut halfway between runs.
        cuts = [head] + [(r[-1][0] + r[-1][2] + n[0][0]) // 2 for r, n in zip(runs, runs[1:])] + [tail]
        for run, lo_b, hi_b in zip(runs, cuts, cuts[1:]):
            lo = max(0, run[0][1] - (run[0][0] - lo_b))
            hi = min(len(body), run[-1][1] + run[-1][2] + (hi_b - run[-1][0] - run[-1][2]))
            while lo < hi and claimed[lo]:
                lo += 1
            while hi > lo and claimed[hi - 1]:
                hi -= 1
            if lo >= hi or any(claimed[lo:hi]):
                # Engine A's stretch for it is another window's already:
                # engine B read that text again, or anchored on text engine
                # A has in another window.  Tested as a copy below.
                windows.append({"b": line[lo_b:hi_b], "lo": None, "hi": None, "claimed": True})
                continue
            for k in range(lo, hi):
                claimed[k] = True
            windows.append({"b": line[lo_b:hi_b], "lo": lo, "hi": hi})
        for piece in (line[:head], line[tail:]):
            if piece:
                windows.append({"b": piece, "lo": None, "hi": None, "outside": True})
    # Engine B's reading of the block: the lines' text the windows take.
    stretch = "\0".join(window["b"] for window in windows if not window.get("outside"))

    def seg(tag: str, a: str, b: str, at: int, settled: tuple[str, str] | None = None) -> dict[str, Any]:
        out = {"tag": tag, "a": a, "b": b, "before": body[max(0, at - 8):at],
               "after": body[at + len(a):at + len(a) + 8]}
        if settled is not None:
            out["settled"] = settled
        return out

    segments: list[dict[str, Any]] = []
    # The windows' engine-A text, and where each of its characters is in the
    # block, for the slices tested as copies.
    held_text, held_at = "", []
    for w in windows:
        if w["lo"] is not None:
            held_text += variant_fold(body[w["lo"]:w["hi"]]) + "\0"
            held_at += list(range(w["lo"], w["hi"])) + [-1]
    folded_stretch = variant_fold(stretch)
    written_again: Counter = Counter()
    for window in windows:
        if window.get("outside"):
            segments.append(seg("insert", "", window["b"], 0, ("", "engine-B-beyond-the-block-windows")))
            continue
        if window.get("claimed"):
            # A slice whose stretch of engine A is another window's.  A
            # piece of it (WITNESS_ANCHOR or more) found in the windows'
            # engine-A text is a copy as far as it covers whole cells there
            # - a cell is one printed box, and a year table repeats its
            # words from cell to cell, so a piece that joins the end of one
            # figure, a year and the start of another is no copy - and only
            # while the windows hold it at least as often as engine B read
            # it (each as often as the engine that read it more often).  A
            # stretch shorter than an anchor between two copied pieces is
            # part of the copy (a character one reading has as another);
            # the rest stands as engine B read it.  Measured: a year table's
            # slice anchored on another window's stretch was written whole,
            # and a figure engine A holds in that window came out twice.
            b = window["b"]
            folded = variant_fold(b)
            copied = [False] * len(b)
            for offset, at, size in witness_anchors(folded, held_text, unique=False):
                where = held_at[at:at + size]
                inside = set(where)
                whole = [p >= 0 and all(q in inside for q in cell_positions[body_cell[p]]) for p in where]
                t = 0
                while t < size:
                    u = t
                    while u < size and whole[u] == whole[t]:
                        u += 1
                    piece = folded[offset + t:offset + u]
                    if whole[t] and held_text.count(piece) + written_again[piece] >= folded_stretch.count(piece):
                        copied[offset + t:offset + u] = [True] * (u - t)
                    elif whole[t]:
                        written_again[piece] += 1
                    t = u
            j = 0
            while j < len(b):
                stop = j
                while stop < len(b) and copied[stop] == copied[j]:
                    stop += 1
                if not copied[j] and 0 < j and stop < len(b) and stop - j < WITNESS_ANCHOR:
                    copied[j:stop] = [True] * (stop - j)
                j = stop
            start = 0
            for j in range(1, len(b) + 1):
                if j == len(b) or copied[j] != copied[start]:
                    piece = b[start:j]
                    segments.append(seg("insert", "", piece, 0, (
                        ("", "engine-B-copy-of-a-window") if copied[start]
                        else (piece, "engine-B-line-in-table-block"))))
                    start = j
            continue
        if window["lo"] is None:
            # A line of engine B inside the block's stretch that anchors
            # nowhere in engine A: B's reading stands (engine A's own text
            # for it, if any, is tested as a copy below).
            segments.append(seg("insert", "", window["b"], 0,
                                (window["b"], "engine-B-line-in-table-block")))
            continue
        lo, hi = window["lo"], window["hi"]
        a = body[lo:hi]
        # Numerals fused per window, as align_drafts' output is for the page.
        segments.extend(fuse_numeral_runs([
            seg(tag, a[i1:i2], window["b"][j1:j2], lo + i1)
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, window["b"], autojunk=False).get_opcodes()]))
    # A window's edge is projected from its anchors, so where the two
    # readings differ in length there one character can fall to the wrong
    # side: the same text deleted at the end of one window and inserted at
    # the start of the next is one character both read.
    merged: list[dict[str, Any]] = []
    for item in segments:
        if (merged and item["tag"] == "insert" and "settled" not in item
                and merged[-1]["tag"] == "delete" and merged[-1]["a"] == item["b"]):
            merged[-1] = dict(merged[-1], tag="equal", b=item["b"])
            continue
        merged.append(item)
    segments = merged
    # Engine A outside every window: a piece engine B read elsewhere is a
    # copy; what B read no piece of is engine A's alone and stays.
    matcher = difflib.SequenceMatcher(None, "", variant_fold(stretch), autojunk=False)
    k = 0
    while k < len(body):
        if claimed[k]:
            k += 1
            continue
        end = k
        while end < len(body) and not claimed[end]:
            end += 1
        run = body[k:end]
        copied = [False] * len(run)
        for offset, _, size in witness_anchors(variant_fold(run), variant_fold(stretch), matcher,
                                               unique=False):
            copied[offset:offset + size] = [True] * size
        # A stretch too short to anchor, inside a cell engine B read elsewhere,
        # is part of that copy: a character the copy misread between two
        # copied pieces (a character engine B read as another), or a piece the windows
        # already hold as often as engine B read it.  A whole cell is never
        # taken this way - a label engine B did not read stays.
        j = 0
        while j < len(run):
            if copied[j]:
                j += 1
                continue
            stop = j
            while stop < len(run) and not copied[stop]:
                stop += 1
            cells = {body_cell[k + x] for x in range(j, stop)}
            others = [q for q in cell_positions[next(iter(cells))] if not k + j <= q < k + stop] \
                if len(cells) == 1 else []
            if any(claimed[q] or (k <= q < end and copied[q - k]) for q in others):
                piece = variant_fold(run[j:stop])
                read = variant_fold(stretch).count(piece)
                between = 0 < j and stop < len(run) and stop - j < WITNESS_ANCHOR
                held = sum(variant_fold(body[w["lo"]:w["hi"]]).count(piece)
                           for w in windows if w["lo"] is not None)
                if between or (read and held >= read):
                    copied[j:stop] = [True] * (stop - j)
            j = stop
        start = 0
        for j in range(1, len(run) + 1):
            if j == len(run) or copied[j] != copied[start]:
                piece = run[start:j]
                segments.append(seg("delete" if copied[start] else "equal", piece, "", k + start,
                                    ("", "copy-engine-B-read-once") if copied[start] else None))
                start = j
        k = end
    head_text = "".join(a_text[i] for i, flag in enumerate(in_head) if flag)
    if head_text:
        segments.append({"tag": "delete", "a": head_text, "b": "", "before": "", "after": "",
                         "settled": ("", "table-furniture-row")})
    return segments


REBUILD_SYSTEM = """你係 jyut-ocr-table-builder。畀你一個版面區域嘅裁圖，同埋兩個 OCR 引擎喺呢個區域讀到嘅字。
兩個引擎讀表格會跳格、搬格，所以啲字嘅**次序可能亂咗**，但字本身已經核對過。

先睇裁圖：呢個區域係咪一個表格（有欄有行、一格格嘅資料，有冇格線都算）？
- 如果唔係表格（係一段段嘅文字、一條條直行嘅正文），淨係答：唔係表格
- 如果係表格，對住裁圖將佢寫出嚟：
  1. 由裁圖重建物理格網：上至下係行、左至右係欄，記清楚邊啲格合併咗。
     本步唔轉置；下一步會獨立對圖判斷紀錄軸同閱讀方向。
     唔可以因為直排中文就假定最右先，亦唔可以靠合計方向決定轉置。
  2. 冇合併格寫 Markdown 表；有合併格（一格跨幾行或者幾欄）寫 HTML `<table>`，用 rowspan／colspan，
     唔好拆格、補空格或者將同一段字抄入幾格。
  3. **字淨係可以用畀你嗰啲**：每個字最多用佢喺下面出現嘅次數，可以按裁圖重新排次序，
     但唔准加任何其他漢字，唔准改字、唔准正規化異體字。下面嘅字應該差唔多全部用上，唔好漏。
  4. 圖上印咗嘅括號、頓號（「、」）等照圖加。

輸出：淨係輸出個表（或者「唔係表格」），唔好解釋，唔好 code fence。"""

REBUILD_AS_PRINTED_SYSTEM = """你係 jyut-ocr-table-builder。畀你一個版面區域嘅裁圖，同埋兩個 OCR 引擎喺呢個區域讀到嘅字。
兩個引擎讀表格會跳格、搬格，所以啲字嘅**次序可能亂咗**，但字本身已經核對過。

先睇裁圖：呢個區域係咪一個表格（有欄有行、一格格嘅資料，有冇格線都算）？
- 如果唔係表格（係一段段嘅文字、一條條直行嘅正文），淨係答：唔係表格
- 如果係表格，對住裁圖將佢寫出嚟：
  1. 由裁圖判斷結構：幾多行、幾多欄、邊啲格合併咗。**照版面印出嚟嘅橫行同直欄原樣排格網**（由上至下係行、由左至右係欄），唔好將直欄轉置成橫行。
  2. 冇合併格寫 Markdown 表；有合併格（一格跨幾行或者幾欄）寫 HTML `<table>`，用 rowspan／colspan，
     唔好拆格、補空格或者將同一段字抄入幾格。
  3. **字淨係可以用畀你嗰啲**：每個字最多用佢喺下面出現嘅次數，可以按裁圖重新排次序，
     但唔准加任何其他漢字，唔准改字、唔准正規化異體字。下面嘅字應該差唔多全部用上，唔好漏。
  4. 圖上印咗嘅括號、頓號（「、」）等照圖加。

輸出：淨係輸出個表（或者「唔係表格」），唔好解釋，唔好 code fence。"""

NOT_A_TABLE = "唔係表格"
# What 裁決 is told when asked about a character a rebuilt table wrote that
# no engine read (confirm_unread): 甲 is engine A's reading there (nothing).
UNREAD_NOTE = ("呢個位係一個表格：乙係重建表格嗰陣寫低嘅字，兩個 OCR 引擎都冇讀到佢（所以甲冇字）。"
               "睇清楚嗰個位有冇印字、印嘅係乜字；冇印字就寫 `定案：無`。")


def check_rebuilt_table(answer: str, allowed: Counter, required: Counter,
                        source: str, unread: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """A rebuilt table, or why it cannot stand.

    `allowed` is what may appear (every character either engine read in the
    region, each as often as the engine that read it more often); `required`
    what must (the characters both engines read there).  One table, at
    least TABLE_MINIMUM_ROWS rows holding text, no character beyond
    `allowed` - after the mechanical repair of a variant swap against
    `source`, the region's text - and none of `required` left out.

    UNREAD, when given, is filled when characters beyond `allowed` are all
    that refuse the table (nothing both engines read left out): the table
    ("markup"), those characters as the answer wrote them ("characters"),
    and what its reason would be without them ("accepted") - for
    confirm_unread, which may ask 裁決 about them.
    """
    answer = strip_fence(answer or "").strip()
    tables = _tables.find_tables(answer)
    if not tables:
        return None, ("not a table (the model, on the crop)" if NOT_A_TABLE in answer
                      else "no table in the answer")
    if len(tables) > 1:
        return None, f"{len(tables)} tables in the answer, one region"
    table = _tables.simplify(tables[0])
    rows = len({cell.row for cell in table.cells if CJK(cell.text)})
    if rows < TABLE_MINIMUM_ROWS:
        return None, f"{rows} row(s) holding text"
    markup = _tables.render(table)
    invented = Counter(CJK(markup)) - allowed
    if invented:
        repaired = restore_merged_characters(source, markup)
        if repaired is not None and not Counter(CJK(repaired)) - allowed:
            markup, invented = repaired, Counter()
    # An acceptable variant of a character an engine read is that character
    # (variant_fold: the user's list, then OpenCC's).  Measured on a textbook
    # table: the rebuild's retry was refused, and the table lost, for writing
    # 算 where engine B read the acceptable 𮅕 (engine A 筍); a region rebuild
    # for 冊 where the engines read 册.  The answer's forms are kept: which
    # form each site prints is no count's to say.
    folded = False
    if invented:
        invented = Counter(variant_fold(CJK(markup))) - Counter(variant_fold("".join(allowed.elements())))
        folded = not invented
    lost = Counter(variant_fold("".join(required.elements()))) - Counter(variant_fold(CJK(markup)))
    if invented:
        named = fold_named(invented, CJK(markup), "".join(allowed.elements()))
        if unread is not None and not lost:
            unread.update({"markup": markup, "characters": named,
                           "accepted": f"{table.rows} rows x {table.cols} columns"})
        return None, "characters neither engine read here: " + "".join(sorted(named))[:30]
    if lost:
        return None, ("characters both engines read here left out: "
                      + "".join(sorted(fold_named(lost, "".join(required.elements()), CJK(markup))))[:30])
    return markup, f"{table.rows} rows x {table.cols} columns" + ("; acceptable variants folded" if folded else "")


def fold_named(excess: Counter, written: str, held: str) -> str:
    """The characters of WRITTEN that EXCESS - counts by variant_fold
    representative, what WRITTEN has beyond HELD - stands for, in WRITTEN's
    own forms, in its order: of each class, the forms WRITTEN has more often
    than HELD has them as written.

    A refusal names what the answer wrote (or, for characters left out, what
    the engines read), never the class's representative.  Measured: a
    rebuild refused for copying a note into three rows was told the lock's
    representatives - five characters every engine had read in their printed
    forms, named in the other forms the fold picks for their classes - and
    its retry wrote those forms into the table."""
    beyond = Counter(written) - Counter(held)
    need = Counter(excess)
    out = []
    for char in written:
        key = variant_fold(char)
        if need[key] > 0 and beyond[char] > 0:
            out.append(char)
            need[key] -= 1
            beyond[char] -= 1
    return "".join(out)


# How much of a neighbour's text engine B must have read inside a nominated
# block's region before the rebuild takes it in (rebuild_region,
# region_witness): most of it.  Measured on the neighbours of the corpus's 12
# nominations that engine B does not read in engine A's order: the blocks of
# a table the layout split off hold 59%, 76% and 95% of their text in engine
# B's reading of the region (a coin table's block, a textbook table's header
# column, a price table's other half); the fourth, two characters engine A
# read a second time as a block of their own - engine B read them once, with
# the block beside it - 0%.  A block engine B skipped has none.
REGION_WITNESS_SHARE = 0.5


def region_witness(blocks: Sequence[Mapping[str, Any]], segments: Sequence[Mapping[str, Any]],
                   starts: Sequence[int], k: int, j: int) -> str:
    """The characters of block J's text (engine A's reading) that engine B
    read inside the union of blocks K and J beyond block K's own text - its
    reading of the two blocks' stretches (engine_b_inside, on SEGMENTS as the
    merge placed them: text engine B read in another order and D2 placed at
    engine A's site counts), less engine A's text of K.  In J's order.

    Engine B reading the union of a table's two blocks reads both; engine B
    that skipped J, or read J somewhere outside the union, leaves nothing
    of J beyond K's own text.  Two stretches that meet are one, so a
    disagreement across the blocks' boundary is inside the union."""
    ends = list(itertools.accumulate(len(CJK(str(b.get("text") or ""))) for b in blocks))
    stretches: list[list[int]] = []
    for lo, hi in sorted(((ends[i - 1] if i else 0), ends[i]) for i in (k, j)):
        if stretches and stretches[-1][1] == lo:
            stretches[-1][1] = hi
        else:
            stretches.append([lo, hi])
    read = Counter(CJK("".join(engine_b_inside(segments, starts, lo, hi) for lo, hi in stretches)))
    beyond = read - Counter(CJK(str(blocks[k].get("text") or "")))
    out = []
    for ch in CJK(str(blocks[j].get("text") or "")):
        if beyond[ch] > 0:
            out.append(ch)
            beyond[ch] -= 1
    return "".join(out)


def rebuild_region(blocks: Sequence[Mapping[str, Any]], k: int, size: tuple[int, int],
                   agreed: Sequence[str], witness: Callable[[int], str],
                   nominated: Collection[int] = ()) -> list[int]:
    """The other blocks a nominated block's rebuild takes in: each block of
    text (not furniture, not NOMINATED itself) whose
    box overlaps block K's or lies within one glyph pitch of it, whose top or
    bottom edge is within one glyph pitch of K's, whose own text engine B
    does not read in engine A's order - AGREED (agreed_block_text, on the
    alignment before D2 places text read in another order once) holds less
    of it than the share the page's order test takes as reading order
    (READING_ORDER_AGREEMENT) - and most of whose text engine B did read
    inside the region: WITNESS(j) (region_witness) holds more of it than
    REGION_WITNESS_SHARE.  The pitch is block K's: its box over its
    characters, in the render's pixels (SIZE).  Returns positions in BLOCKS,
    in order.

    Not read in engine A's order is no evidence on its own: a title or
    caption beside the table that engine B skipped agrees on nothing either,
    and was taken in and left the page with the table.  A block nominated
    itself is a table of its own: two adjacent tables asked as one region
    answered two tables, refused.

    A table the layout model called text can come back as several blocks:
    its header column as a block of its own, or its halves either side of a
    ruled line.  Rebuilt from the nominated block alone, the table has no
    header row, and the rest of its text stays beside it as prose.
    Measured on every nomination of the corpus's 692 merge-mode pages (12
    blocks; 16 neighbours overlap one or lie within a pitch): the rule takes
    a textbook table's header column (overlapping, the same top edge, 47% of
    it agreed), the other half of a ruled price table (0.12 pitch apart, its
    edges within 0.12 pitch, 67% agreed before D2, 95% after, D2 having
    placed its runs read in another order once) and two blocks of a coin
    table (0% and 47% agreed).  The neighbours of the prose nominations
    share an edge too - columns of one leaf - but both engines read them in
    one order (98-100% agreed), so no prose nomination takes a block; a
    section heading engine B read elsewhere (0%) shares no edge.  Two more
    blocks of the coin table (79%, 80%) are not taken: the region is grown
    only where the evidence is plain.  Of the coin table's two, the 0% block
    is engine A's second reading of two characters engine B read once, with
    the block beside it: it has no witness and is no longer taken.  No
    neighbour of the twelve is nominated itself."""
    if not blocks or not (0 <= k < len(blocks)):
        return []
    width, height = size
    x, y, w, h = blocks[k]["box"]
    count = len(CJK(str(blocks[k].get("text") or "")))
    if not count or w <= 0 or h <= 0:
        return []
    pitch = ((w * width) * (h * height) / count) ** 0.5
    taken = []
    for j, other in enumerate(blocks):
        text = CJK(str(other.get("text") or ""))
        if j == k or not text or other.get("label") in FURNITURE_LABELS or other.get("quarantined"):
            continue
        ox, oy, ow, oh = other["box"]
        apart_x = max(ox - (x + w), x - (ox + ow), 0.0) * width
        apart_y = max(oy - (y + h), y - (oy + oh), 0.0) * height
        if apart_x >= pitch or apart_y >= pitch:
            continue
        if min(abs(oy - y), abs((oy + oh) - (y + h))) * height >= pitch:
            continue
        if j < len(agreed) and len(agreed[j]) >= READING_ORDER_AGREEMENT * len(text):
            continue
        if j in nominated:
            continue
        if len(witness(j)) <= REGION_WITNESS_SHARE * len(text):
            continue
        taken.append(j)
    return taken


def engine_b_inside(segments: Sequence[Mapping[str, Any]], starts: Sequence[int], lo: int, hi: int) -> str:
    """Engine B's reading of engine A's stretch [LO, HI) - a block: the part of
    each agreed segment inside it and the engine-B side of each disagreement
    inside it (block_union's test), in the segments' order.  Engine B's
    reading of a set-aside looped block is that block's (engine_b_outside)."""
    out: list[str] = []
    for x, a_at in zip(segments, starts):
        if x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if x["tag"] == "equal":
            if a_at < hi and a_end > lo and len(x["b"]) == len(x["a"]):
                out.append(x["b"][max(lo, a_at) - a_at:min(hi, a_end) - a_at])
        elif (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi):
            out.append(x["b"])
    return "".join(out)


def unwritten_readings(segments: Sequence[Mapping[str, Any]], starts: Sequence[int],
                       pieces: Sequence[str], lo: int, hi: int) -> list[tuple[str, str]]:
    """The engines' readings of each disagreement inside engine A's stretch
    [LO, HI) that the merge did not write as read, as (engine, reading):
    engine B's side, then engine A's, in the segments' order."""
    out: list[tuple[str, str]] = []
    for x, a_at, piece in zip(segments, starts, pieces):
        if x["tag"] == "equal" or x.get(LOOPED_READING):
            continue
        a_end = a_at + len(x["a"])
        if not ((lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi)):
            continue
        out += [(engine, text) for engine, text in (("B", x["b"]), ("A", x["a"])) if text and text != piece]
    return out


def rebuild_lock(merged: str, engine_a: str, engine_b: str,
                 unwritten: Sequence[tuple[str, str]] = ()) -> tuple[str, list[tuple[str, str]]]:
    """The characters a rebuilt table of a region may hold, as the text the
    rebuild is shown: MERGED (the merge's text of the region) and, each on a
    line of its own, runs of the characters engine A (ENGINE_A, its reading
    of the region) or engine B (ENGINE_B, its reading inside the region,
    engine_b_inside) read there more often than the merge wrote them - so
    each character stands at the highest of its counts in the three
    (check_rebuilt_table's "every character either engine read in the
    region, each as often as the engine that read it more often"; the
    merge's own count too, for a character 裁決 read that neither engine
    did).  The runs are cut from the disagreements' unwritten readings
    (UNWRITTEN, unwritten_readings) first, where the characters stand in the
    order an engine read them, then from the engines' readings.  Returns the
    text and the runs added, each with the engine it came from.

    The merge's text alone was the lock.  Measured on a ruled textbook
    table nominated as a missed table: engine B read a two-digit figure cell
    that the alignment paired with another cell of engine A's, a one-digit
    figure; 裁決, shown engine A's site, kept engine A's, and the merge
    dropped engine B's two digits.  The one
    rebuild (of four runs) that wrote the figure the page prints was refused
    as inventing it, and its retry too; a person's transcription of the
    table's records is refused the same way under the merge's text, and
    passes under this lock."""
    deficit = (Counter(CJK(merged)) | Counter(CJK(engine_a)) | Counter(CJK(engine_b))) - Counter(CJK(merged))
    runs: list[tuple[str, str]] = []
    for engine, source in [*unwritten, ("A", engine_a), ("B", engine_b)]:
        run = ""
        for char in CJK(source) + "\0":
            if char != "\0" and deficit[char] > 0:
                deficit[char] -= 1
                run += char
            elif run:
                runs.append((engine, run))
                run = ""
    return merged + "".join("\n" + run for _, run in runs), runs


# How much of a refused rebuild answer the seal keeps (a table of a few hundred cells fits).
REFUSED_ANSWER_LIMIT = 20000


# How many characters no engine read a rebuilt table may write, each
# confirmed by 裁決 on a crop of its own spot (confirm_unread): the user's
# ruling of 2026-09-27 (round-2 register, headerCharacters) - "a table rebuild
# may add at most 2 characters no engine read, each confirmed by 裁決 on its
# own crop and recorded as a doubt".
UNREAD_TABLE_LIMIT = 2


def unread_sites(markup: str, characters: str) -> list[tuple[str, str, int]]:
    """Every place in a rebuilt table's cells holding a character of the
    variant classes of CHARACTERS (characters no engine read): (the
    character as written, its cell's text, its offset in the cell's CJK), in
    reading order.  Every place of such a class, not only the ones beyond
    the lock: which of several is the one no engine read no count says."""
    table = _tables.find_tables(markup)
    classes = {variant_fold(char) for char in CJK(characters)}
    out = []
    for cell in (table[0].cells if table else []):
        text = CJK(cell.text)
        out += [(char, text, at) for at, char in enumerate(text) if variant_fold(char) in classes]
    return out


def unread_spot(texts: Sequence[str], cell: str, at: int) -> tuple[int, int, str, str] | None:
    """Where the character a rebuilt table wrote at offset AT of CELL (one no
    engine read) stands in engine A's reading of the region's blocks (TEXTS,
    each a block's CJK): (the block, the position in its text the character
    would take, the cell's text before it and after it, as far as engine A
    read it there).  The spot is fixed by the longest stretch of the cell
    just before the character, or just after it, that engine A read exactly
    once in the region - one character will do, a stretch read twice or not
    at all says nothing.  None when no stretch of the cell around it does."""
    cell = CJK(cell)
    before, after = cell[:at], cell[at + 1:]
    found: list[tuple[int, int, int, str, str]] = []
    for size in range(len(before), 0, -1):
        piece = before[-size:]
        hits = [(j, text.find(piece)) for j, text in enumerate(texts) if piece in text]
        if sum(text.count(piece) for text in texts) == 1:
            j, p = hits[0]
            found.append((size, j, p + size, piece, texts[j][p + size:p + size + 4]))
            break
    for size in range(len(after), 0, -1):
        piece = after[:size]
        hits = [(j, text.find(piece)) for j, text in enumerate(texts) if piece in text]
        if sum(text.count(piece) for text in texts) == 1:
            j, p = hits[0]
            found.append((size, j, p, texts[j][max(0, p - 4):p], piece))
            break
    if not found:
        return None
    _, j, position, left, right = max(found, key=lambda spot: spot[0])
    return j, position, left, right


# How far past engine A's box, in glyph pitches, the crop of a character no
# engine read reaches when its spot is at the box's edge (unread_crop).
UNREAD_CROP_REACH = 2


def unread_crop(render: Path, render_bytes: bytes | None, block: Mapping[str, Any], position: int,
                seg: Mapping[str, Any]) -> bytes | None:
    """The crop 裁決 is asked about a character no engine read on: the
    disagreement crop at POSITION of BLOCK's text (segment_crop) where the
    spot lies inside engine A's reading of the block, and the block's box
    grown by UNREAD_CROP_REACH glyph pitches every way (the pitch from the
    box's area over its characters) where it lies at either end - a
    character engine A did not read is often outside the box engine A drew
    round what it did.  Measured on a textbook table's header column: the
    box ends in the upper part of the header's last character, the one no
    engine read, and the block's own crop showed only the tops of its
    strokes."""
    count = len(CJK(str(block.get("text") or "")))
    if 0 < position < count:
        return segment_crop(render, render_bytes, block, position, seg)
    try:
        from PIL import Image
        with Image.open(render) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001 - the ordinary crop stands instead
        return segment_crop(render, render_bytes, block, position, seg)
    x, y, w, h = block["box"]
    pitch = ((w * width) * (h * height) / max(count, 1)) ** 0.5
    grow_x, grow_y = UNREAD_CROP_REACH * pitch / width, UNREAD_CROP_REACH * pitch / height
    return (upscale_crop(crop_block(render, [x - grow_x, y - grow_y, w + 2 * grow_x, h + 2 * grow_y], pad=0.0))
            or segment_crop(render, render_bytes, block, position, seg))


def confirm_unread(answer: str, allowed: Counter, required: Counter, source: str,
                   confirm: Callable[[str, str, int], dict[str, Any]], asked: list[dict[str, Any]],
                   limit: int = UNREAD_TABLE_LIMIT) -> tuple[str | None, str]:
    """A rebuilt table refused only for characters no engine read, accepted
    when there are at most LIMIT places of them (unread_sites) and 裁決
    confirms every one on a crop of its own spot (CONFIRM: character, cell,
    offset -> the question's record, "confirmed" true or false).  The
    questions go into ASKED.  Returns the table and its reason, or None and
    why not ("" when the answer is not such a table).

    Measured on a textbook table's header: engine A read the fifth header
    cell but for its last character, engine B (v1.0) none of the cell, and
    the region rebuild (D22) was refused in all six benchmark runs for that
    character, the one printed header character no engine read.  The
    rebuild misreads too - in one run it wrote another character there, in
    another it misread the cell's first - so no such character stands on the
    rebuild's word: each is asked on its own crop, and a person is told
    (table-unread-confirmed)."""
    found: dict[str, Any] = {}
    check_rebuilt_table(answer, allowed, required, source, unread=found)
    if not found:
        return None, ""
    sites = unread_sites(found["markup"], found["characters"])
    if not sites or len(sites) > limit:
        return None, (f"{len(sites)} place(s) of characters no engine read ({found['characters'][:10]}), "
                      f"more than the {limit} 裁決 may confirm")
    results = [confirm(char, cell, at) for char, cell, at in sites]
    asked.extend(results)
    unconfirmed = [r for r in results if not r.get("confirmed")]
    if unconfirmed:
        return None, "裁決 did not confirm " + ", ".join(
            f"{r['char']} ({'read ' + r['answered'] if r.get('answered') else r.get('why') or 'nothing printed'})"
            for r in unconfirmed)
    return found["markup"], (f"{found['accepted']}; {len(results)} character(s) no engine read, "
                             f"confirmed by 裁決 ({''.join(r['char'] for r in results)})")


def rebuild_table(client: "Client", crop: bytes | None, text: str, required: Counter,
                  max_tokens: int, confirm: Callable[[str, str, int], dict[str, Any]] | None = None,
                  table_orientation: str = "auto"
                  ) -> tuple[str | None, dict[str, Any]]:
    """Ask the formatter to write one region as a table, from its crop, with its
    characters locked to `text` (what may appear) and `required` (what must).

    Not an engine re-read: the characters are the ones the engines read there
    (rebuild_lock; for a region read from engine B's lines, those lines).
    One retry names what was wrong; an answer that the region is not a table
    is taken as it stands.  With CONFIRM, an answer refused only for up to
    UNREAD_TABLE_LIMIT characters no engine read stands when 裁決 confirms
    each on its own crop (confirm_unread); the retry is told the lock's
    reason, not 裁決's reading.  Returns the table or None, and the record
    sealed in tablesRebuilt.
    """
    rebuild_system = REBUILD_AS_PRINTED_SYSTEM if table_orientation == "as-printed" else REBUILD_SYSTEM
    allowed = Counter(CJK(text))
    record: dict[str, Any] = {"locked": sum(allowed.values()), "required": sum(required.values())}
    ask = (f"呢個區域兩個引擎讀到嘅字（次序可能亂咗）：\n---\n{text}\n---\n"
           "睇裁圖：係表格就用上面啲字寫出個表，唔係就答「唔係表格」。")
    asked: list[dict[str, Any]] = []
    notes: list[str] = []

    def judged(answer: str, table: str | None, why: str) -> tuple[str | None, str]:
        if table is not None or confirm is None:
            return table, why
        confirmed, how = confirm_unread(answer, allowed, required, text, confirm, asked)
        if confirmed is not None:
            return confirmed, how
        if how:
            notes.append(how)
        return None, why

    answer, _ = client.ask_answering(rebuild_system, ask, crop, max_tokens, kind="table-rebuild")
    table, why = judged(answer, *check_rebuilt_table(answer, allowed, required, text))
    refused = [] if table is not None else [answer or ""]
    if table is None and not why.startswith(("not a table", "no table")):
        answer, _ = client.ask_answering(
            rebuild_system, ask + f"\n你上一次嘅答案唔收得，原因：{why}。淨係用上面啲字，一個都唔好漏，重新寫。",
            crop, max_tokens, kind="table-rebuild-retry")
        table, again = judged(answer, *check_rebuilt_table(answer, allowed, required, text))
        why = again if table is not None else f"{why}; retry: {again}"
        if table is None:
            refused.append(answer or "")
    record["outcome"] = "accepted" if table is not None else "refused"
    record["reason"] = why + (f" ({'; '.join(notes)})" if notes and table is None else "")
    if asked:
        # The characters no engine read that 裁決 was asked about (confirm_unread).
        record["unreadAsked"] = asked
    if refused:
        # A refused answer is sealed, as the formatter's is (formatterRejectedOutput):
        # a table lost to the lock is diagnosed from what the model wrote, not guessed.
        record["refusedAnswers"] = [a[:REFUSED_ANSWER_LIMIT] for a in refused]
    return table, record


SENTENCE_PUNCT = "。，、；：？！"


def punctuation_density(text: str) -> float:
    """Sentence-punctuation marks per 100 CJK characters.

    Recorded as an observation, never judged against a threshold. Punctuation
    practice varies within a single book - a punctuated 序 in front of an
    unpunctuated 正文, unpunctuated quotations inside punctuated commentary -
    so there is no book-wide density a page can be "too far" from.
    """
    cjk = sum(1 for c in text if "\u3400" <= c <= "\u9fff")
    if cjk < 40:
        return -1.0
    return 100.0 * sum(text.count(p) for p in SENTENCE_PUNCT) / cjk

def validate_completion(job: PageJob, expect: Mapping[str, str]) -> tuple[bool, str]:
    if not job.out_json.is_file() or not job.out_md.is_file():
        return False, "no existing output"
    try:
        raw = job.out_json.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return False, f"unreadable: {error}"
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return False, "legacy schema"
    if raw != canonical_json_bytes(record):
        return False, "not canonical or edited"
    provenance = record.get("provenance") or {}
    for key, value in expect.items():
        # The switches' keys (last in EXPECT) are compared below, both ways.
        if key not in OPTIONAL_PROVENANCE and provenance.get(key) != value:
            return False, f"provenance {key} differs"
    for key in OPTIONAL_PROVENANCE:
        # Absent means off: a page sealed with the switch on is not current
        # for a run with it off.
        if key not in ROUND4_DEFAULTS.values() and provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    # The round-4 fixes: a page sealed with one off and asked on, or the other
    # way round (a book begun before they were on by default), is told which
    # switches resume it as it was sealed.
    turned = [switch for switch, key in ROUND4_DEFAULTS.items() if (key in provenance) != (key in expect)]
    if turned:
        sealed_off = [switch for switch in turned if ROUND4_DEFAULTS[switch] not in provenance]
        sealed_on = [switch for switch in turned if switch not in sealed_off]
        return False, ("provenance " + ", ".join(ROUND4_DEFAULTS[switch] for switch in turned)
                       + (" differs: " if len(turned) == 1 else " differ: ")
                       + "; ".join(part for part in (
                           f"sealed with {', '.join(sealed_off)} off, this run has "
                           f"{'it' if len(sealed_off) == 1 else 'them'} on (resume with "
                           + " ".join("--no-" + switch[2:] for switch in sealed_off) + ")" if sealed_off else "",
                           f"sealed with {', '.join(sealed_on)} on, this run has "
                           f"{'it' if len(sealed_on) == 1 else 'them'} off" if sealed_on else "") if part))
    for key in ROUND4_DEFAULTS.values():
        if provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    body = job.out_md.read_bytes()
    if sha256_bytes(body) != provenance.get("markdownSha256"):
        return False, "markdown does not match its seal"
    sealed = provenance.get("jsonPayloadSha256")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed)) != sealed:
        return False, "payload seal does not match"
    return True, "current"


def page_spans(pages: Collection[int]) -> str:
    """PAGES as runs: 1-3, 7, 9-10."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(f"{lo}-{hi}" if hi > lo else str(lo) for lo, hi in runs)


def round4_split(output: Path, rewritten: Collection[int], expect: Mapping[str, Any]) -> str | None:
    """Why a run with EXPECT's provenance would leave the book in OUTPUT decided
    two ways, else None: a page sealed there that this run does not write again
    (REWRITTEN: the pages it proofreads with --overwrite) with one of the
    round-4 fixes (ROUND4_DEFAULTS) on where this run has it off, or off where
    it has it on.  validate_completion blocks such a page, but the run would
    go on to decide the book's other pages with this run's switches: a book
    begun before the fixes were on by default and resumed without the four
    --no-* switches would be sealed half with them off and half on, and no
    later run finds every page current without --overwrite.  So the run does
    not start (main); the reason says which switches resume the book as its
    sealed pages were decided."""
    wanted = {key for key in ROUND4_DEFAULTS.values() if key in expect}
    skip = set(rewritten)
    states: dict[frozenset, list[int]] = {}
    for path in sorted(output.glob("page-*.json")):
        match = re.fullmatch(r"page-(\d{4})\.json", path.name)
        if not match or int(match.group(1)) in skip:
            continue
        try:
            provenance = json.loads(path.read_text(encoding="utf-8")).get("provenance")
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
        if not isinstance(provenance, dict) or "renderSha256" not in provenance:
            continue
        states.setdefault(frozenset(key for key in ROUND4_DEFAULTS.values() if key in provenance),
                          []).append(int(match.group(1)))
    other = {state: pages for state, pages in states.items() if state != wanted}
    if not other:
        return None
    switch = {key: name for name, key in ROUND4_DEFAULTS.items()}
    order = list(ROUND4_DEFAULTS.values())

    def names(keys: Collection[str]) -> list[str]:
        return [switch[key] for key in order if key in set(keys)]

    if len(states) > 1:
        # The sealed pages disagree among themselves already: no run resumes
        # them all.
        return ("the sealed pages in " + str(output) + " were decided with the on-by-default switches set in "
                + str(len(states)) + " ways: "
                + "; ".join(f"pages {page_spans(pages)} with "
                            + (", ".join(names(state)) or "none of " + ", ".join(ROUND4_DEFAULTS)) + " on"
                            for state, pages in sorted(states.items(), key=lambda item: min(item[1])))
                + " - no run finds them all current; decide one part again with --overwrite and that part's "
                  "--pages, with the switches of the other; nothing was proofread")
    (state, pages), = other.items()
    sealed_off, sealed_on = names(wanted - state), names(state - wanted)
    return ("resume "
            + " and ".join(part for part in (
                "with " + " ".join("--no-" + name[2:] for name in sealed_off) if sealed_off else "",
                "without " + " ".join("--no-" + name[2:] for name in sealed_on) if sealed_on else "") if part)
            + f": {len(pages)} sealed page(s) in {output} ({page_spans(pages)}) were decided with "
            + "; ".join(part for part in (
                f"{', '.join(sealed_off)} off" if sealed_off else "",
                f"{', '.join(sealed_on)} on" if sealed_on else "") if part)
            + ", this run the other way; a book is decided one way throughout, so nothing was proofread "
              "(to decide every page again with this run's switches: --overwrite, every page)")


# Ideographs, plus the few CJK Symbols that are TEXT rather than punctuation.
# 〇 (U+3007) is the ideographic zero and sits at U+3007, below the ideograph
# block - so the original range silently deleted it from every figure in the
# corpus: 八〇六、三五五 became 八六、三五五 and 一、五五四、九〇〇 became
# 一、五五四、九.  272 of them across four books, all in financial tables, and
# because cells are filled by counting these characters the missing digits also
# shifted whole columns onto the wrong rows.
CJK_EXTRA = "\u3007\u3005\u3006"   # 〇 ideographic zero, 々 iteration mark, 〆
# PaddleOCR-VL writes the numeral zero in tables as a geometric circle (○ U+25CB,
# sometimes ◯ U+25EF); HunyuanOCR writes 〇 U+3007.  Same digit on the page, so
# they are read as one character - otherwise every zero on a page of figures is a
# "disagreement" (measured: 33 on one page, each sent to the crop, all confirming 〇).
ZERO_FORMS = str.maketrans({"\u25cb": "\u3007", "\u25ef": "\u3007", "\uff10": "\u3007"})
CJK = lambda s: "".join(
    c for c in s.translate(ZERO_FORMS)
    if "\u3400" <= c <= "\u9fff" or "\U00020000" <= c <= "\U0003ffff" or c in CJK_EXTRA
)

FORMAT_SYSTEM = """你係 jyut-ocr-formatter。下面畀你一串**已經定咗嘅字**，同埋嗰版嘅頁圖。

你**唯一**嘅工作係加返啲字以外嘅嘢：
1. 句讀——由頁圖逐個讀出。你收到嘅字串係淨係得字嘅合併文本，OCR 引擎寫過嘅符號喺合併時已經拎走，
   所以字串冇標點**唔係**原書冇標點嘅證據。原書有幾多個就加幾多個；原書真係冇就唔好加。
   **符號嘅形狀要同頁圖一致**：圖上係圈就寫「。」，係點就寫「，」，係頓號就寫「、」。
   準則係頁圖印咗乜，唔係你覺得句子結構應該用乜——好多舊書通篇淨係印圈，冇點，
   遇到呢種書就唔好用「，」。
   兩個圈連住印（雙圈）就寫兩個：「。。」，唔好合併做一個。
   項目符號（▲ △ ● ◎ ⦿ ◆ ■ 之類）照印寫。
   一串點（目錄、表格入面嘅導引點線，或者刪節號）寫「……」，唔理印咗幾多粒點。
   但係**唔准每個字後面都加符號**：原書印幾多個你就加幾多個，位置要同頁圖對應。
   密度冇標準答案——散文可能每十幾二十個字一個圈，四字句嘅駢文可能每四五個字就一個圈，
   唯一準則係頁圖：逐欄數返頁上有幾多個圈，就出幾多個，一個都唔好慳。
   如果你發現自己差唔多逐個字都加緊，即係做錯咗。
2. 版面結構——heading、段落、list、原書編號、表格。先由頁圖判斷版式（直排／橫排、
   欄數、閱讀方向），唔好假設。
   表格：冇合併格就寫 Markdown 表；有合併格（一格跨幾行或者幾欄，例如一格附註管住幾行、
   一個標題蓋住幾欄）就寫 HTML `<table>`，合併格用 rowspan／colspan 表示。
   Markdown 表表達唔到合併格，唔好為咗塞入 Markdown 而拆格、補空格，或者將同一段字抄入幾格。
3. 來源覆核已經移除確認嘅家具；保留收到嘅全部文字、數字、外文同算式。

**絕對唔准改字，亦唔准加任何漢字。** 唔准正規化異體字，唔准「修正」你覺得怪嘅字。

呢一條最常犯嘅錯係**改編號**：舊書凡例好多時每條都用同一個「一」做項目符號，
唔係一二三四五。你**唔准**幫佢重新編號——字串入面係咩就係咩，重複嘅「一」照抄。
要做 list 就用 Markdown 嘅 `-`，唔好自己補漢字數字。
但係如果頁上嘅編號係括住嘅（例如「（一）」「（二）」），括號要加返——括號係標點，唔係字；
括號入面嘅數字就照字串，唔准改。
本階段只做格式；如懷疑來源讀法有問題，留待隨後整頁圖像覆核裁定。兩引擎一致唔係正確保證。
本階段唔准刪字；家具判斷只由整頁圖像覆核處理。

輸出：淨係輸出加咗標點同結構嘅 Markdown 正文。唔好解釋，唔好 code fence。"""


PUNCT_SYSTEM = """你係 jyut-ocr-punctuator。畀你一段**已經定咗嘅字**，同埋嗰段字喺頁上嘅裁圖
（一個版面 block，通常係一欄或者幾欄直排文字）。

你唯一嘅工作係加返頁圖上**印咗**嘅標點：圈「。」、點「，」、頓號「、」、括號、引號等等。
- 逐個位置由裁圖讀：圖上有就加，冇就唔加。符號嘅形狀要同圖一致：圈→「。」，點→「，」，頓→「、」。
- 密度冇標準答案——駢文可以每四五個字一個圈，散文可能十幾廿個字先一個。唯一準則係裁圖：
  逐欄數返有幾多個符號，就出幾多個，一個都唔好慳，亦一個都唔好多。
- 兩個圈連住印（雙圈）就寫兩個：「。。」，唔好合併做一個。
- 項目符號（▲ △ ● ◎ ⦿ ◆ ■ 之類）照印寫。
- 一串點（目錄、表格入面嘅導引點線，或者刪節號）寫「……」，唔理印咗幾多粒點。
- 除非裁圖上真係一串字逐個字旁邊都印住圈或點（圈點），否則唔好差唔多逐個字都加符號——
  嗰個係錯覺，唔係頁上嘅嘢。有圈點嘅就照圖逐個寫。
- 直排嘅括號「︵︶」輸出做「（」「）」。
- 直排嘅圈、點印喺字嘅**右下角**。裁圖最左邊緣貼住嘅符號屬於隔籬欄（唔喺呢段字入面），唔好計；
  裁圖右邊緣以外嘅嘢一律唔理。
- **唔准加、刪、改任何漢字**，唔准正規化異體字，唔准「修正」你覺得怪嘅字；
  字係兩個獨立 OCR 引擎核對過嘅，比你單獨睇圖準。
- 唔准分段、唔准加 heading、唔准加 list 符號、唔准加 Markdown——版面結構唔係你嘅工作。

輸出：淨係輸出加咗標點嘅同一段字，一行，唔好解釋，唔好 code fence。"""

CENSUS_SYSTEM = """你係古籍標點點算員。畀你一個版面 block 嘅裁圖（直排文字，一欄或者幾欄）。

淨係做一件事：數清楚圖上**印咗**標點符號嘅位置——圈「。」、點「，」、頓號「、」、
括號「︵︶」（一對計兩處）、引號等等。逐欄由右至左、由上至下數。
- 兩個圈上下疊住或者連住印（雙圈）係**一處**；另外喺第二行報其中有幾多處係雙圈。
- 一串字每個字旁邊都有嘅圈點逐個計。
- 項目符號（▲ △ ● ◎ ⦿ ◆ ■ 之類）每個計一處，兩個疊住印嘅（例如 ⦿⦿）都係一處；形狀行照印寫佢嘅形狀。
- 一串點（導引點線、刪節號）每串計一處，唔理有幾多粒點。
- 直排嘅圈、點印喺字嘅右下角。裁圖最左邊緣貼住嘅符號屬於隔籬欄，唔好計。
- 格線、版框、污點、字嘅筆畫都唔係符號。
- 好多舊書通篇冇標點，數到零係正常結果，唔好夾硬數出嚟。

輸出格式（淨係呢三行，唔好解釋）：
符號：<印咗符號嘅位置數；雙圈算一處>
雙圈：<其中幾多處係雙圈；冇就寫 0>
形狀：<出現過嘅符號種類，例如 。、（）；冇就寫 無>"""


STRUCTURE_SYSTEM = """你係 jyut-ocr-formatter。下面畀你一頁**字同標點都已經定咗**嘅文本（逐個版面 block 一行；
有導引點嘅目錄 block 就照原書每行一行），同埋嗰版嘅頁圖。

你**唯一**嘅工作係加版面結構：
1. heading、段落、list、原書編號——先由頁圖判斷版式（直排／橫排、欄數、閱讀方向），唔好假設。
2. 來源覆核已經移除確認嘅家具；唔准再刪收到嘅文字、數字、外文同算式。
3. 表格：文本入面自己一行嘅表格記號（例如 ⟦T1⟧）代表一個已經定咗嘅表——嗰行原封不動照抄、擺喺原位，
   唔好自己寫嗰個表，亦唔好刪走或者重複個記號。
   頁上明明係表格、但文本入面冇記號嘅，按頁圖重建：冇合併格寫 Markdown 表；有合併格（一格跨幾行
   或者幾欄）寫 HTML `<table>`，合併格用 rowspan／colspan，唔好拆格、補空格或者將同一段字抄入幾格。

**絕對唔准改字、加字，亦唔准改標點**：每一個「。」「，」「、」括號都要原封不動保留喺原位，
唔准加、唔准刪、唔准搬。標點已經逐欄對過圖，你再改就係倒退。
唯一例外：頁上嘅 list 編號如果印住括號（例如「（一）」），而文本入面漏咗，可以加返括號——
括號係結構，唔係字；括號入面嘅數字照文本，唔准改。
舊書凡例好多時每條都用同一個「一」做項目符號，唔係一二三四五；你**唔准**幫佢重新編號，
要做 list 就用 Markdown 嘅 `-`。
本階段唔准刪字或符號；家具判斷只由整頁圖像覆核處理。

輸出：淨係輸出加咗結構嘅 Markdown 正文。唔好解釋，唔好 code fence。"""

SENTENCE_MARKS = "。，、；：？！（）「」『』《》〈〉"
# Printed item bullets, kept as printed (decision D5), and the dots of a leader
# or an ellipsis, always written 「……」 whatever their number (decision D4).
# Before, neither was a mark here: nothing
# counted, asked for or kept them - a magazine's ▲ and a table of contents' 7
# leaders were lost, and engine B's 厶 stood where the ▲ is printed.
BULLET_MARKS = "▲△●◎◆■⦿◉"
# ⦿ is in the vocabulary so that a model writing it keeps it as a mark: a page
# printing ⦿⦿ before each item heading had them written 。。 by the vocabulary
# without it.  ◉ likewise, the third shape the user named.  Their shape is not
# enforced and not asked about: the user ruled ●, ⦿ and ◉, printed single or
# doubled (⦿⦿), symbol variants of one bullet, accepted either way (decision
# D5 after the baseline; BULLET_SHAPES, and the scorer folds them).  The
# prompts are unchanged: they name ⦿ and say 照印寫; whether naming ◉ there
# would make a model write it for a printed ◉, or for a printed ●, is not
# measured (no engine draft, golden or benchmark output of the calibration
# books writes ◉).
BULLET_SHAPES = str.maketrans({"⦿": "●", "◉": "●"})
LEADER_MARKS = "…⋯"
LEADER = "……"
# The marks the punctuation step counts and keeps.
PUNCT_MARKS = SENTENCE_MARKS + BULLET_MARKS + LEADER_MARKS
FURNITURE_LABELS = frozenset({"header", "footer", "number", "seal", "image", "figure", "table"})


def punctuation_counts(text: str) -> Counter:
    """The marks in TEXT; a run of leader dots is one mark, 「……」 (D4: a
    leader is written so whatever its number of dots)."""
    counts: Counter = Counter()
    # Not "", which is `in` every string: a leader opening TEXT went uncounted.
    previous = "\n"
    for c in text:
        if c in PUNCT_MARKS:
            if c in LEADER_MARKS:
                if previous not in LEADER_MARKS:
                    counts[LEADER] += 1
            else:
                counts[c] += 1
        previous = c
    return counts


def punctuation_delta(before: str, after: str) -> str:
    gained = punctuation_counts(after) - punctuation_counts(before)
    lost = punctuation_counts(before) - punctuation_counts(after)
    parts = []
    if gained:
        parts.append("added " + "".join(f"{k}×{v}" for k, v in gained.items()))
    if lost:
        parts.append("removed " + "".join(f"{k}×{v}" for k, v in lost.items()))
    return "; ".join(parts) or "same counts, different placement"


CENSUS_LINE = re.compile(r"符號[：:]\s*([0-9０-９〇零一二三四五六七八九十百]+)")
DOUBLED_LINE = re.compile(r"雙圈[：:]\s*([0-9０-９〇零一二三四五六七八九十百]+)")


def parse_census(text: str, line: re.Pattern = CENSUS_LINE) -> int | None:
    """The printed-mark count the model reported, or None when it gave none.
    With DOUBLED_LINE, the number of those places that are doubled circles."""
    match = line.search(text)
    if not match:
        return None
    raw = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    if raw in CHINESE_DIGITS:
        return CHINESE_DIGITS[raw]
    if raw == "十":
        return 10
    if "十" in raw and all(c in CHINESE_DIGITS or c == "十" for c in raw):
        tens, _, ones = raw.partition("十")
        return (CHINESE_DIGITS.get(tens, 1) if tens else 1) * 10 + (CHINESE_DIGITS.get(ones, 0) if ones else 0)
    return None


def table_spans(text: str) -> list[tuple[int, int]]:
    """Character spans [start, end) of every table in a page, in page order.

    Markdown and HTML tables as _tables finds them, plus runs of pipe rows a
    model wrote without the | --- | line: no renderer calls that a table, but
    it is still the formatter's copy of one, and must be found to be replaced.
    Nothing inside a code fence counts - it renders as code, not as a table.
    """
    spans = [(t.start, t.end) for t in _tables.find_tables(text)]
    offset, run = 0, None
    for line in _tables.mask_fences(text).split("\n") + [""]:
        inside = any(a <= offset < b for a, b in spans)
        if line.strip().startswith("|") and not inside:
            run = (run[0] if run else offset, offset + len(line))
        elif run:
            spans.append(run)
            run = None
        offset += len(line) + 1
    return sorted(spans)


def _cell_key(text: str) -> str:
    """A cell's characters in sorted order: the same cell read either way round."""
    return "".join(sorted(CJK(text)))


def written_cells(text: str) -> list[str]:
    """The cell texts of a written table in reading order, including pipe rows
    written without the | --- | line, which no parser calls a table."""
    tables = _tables.find_tables(text)
    if tables:
        return [cell.text for table in tables for cell in table.cells]
    return [cell for line in text.splitlines() for cell in (_tables.table_cells(line) or [])]


def table_signature(text: str) -> list[str]:
    """The non-empty cells of a table as sorted characters, sorted: the same
    table whatever its orientation, its markup, or the direction a cell was
    read in.  A formatter that turns a cell round (a right-to-left label it
    reads the other way) still wrote the recovered table, and a signature of
    exact cell strings called such a table missing while it stood on the
    page."""
    return sorted(_cell_key(cell) for cell in written_cells(text) if CJK(cell))


def recovered_runs(formatted: str, tables: Sequence[str]) -> list[tuple[int, int, int, int]]:
    """Written tables made of exactly the cells of recovered tables first..last,
    consecutive, as (start, end, first, last) in page order.

    Cells are compared as table_signature compares them, so a table counts
    in any orientation or markup.  first == last is one recovered table
    written out; last > first is several joined into one.
    """
    signatures = [Counter(table_signature(table)) for table in tables]
    found = []
    for start, end in table_spans(formatted):
        written = Counter(table_signature(formatted[start:end]))
        if not written:
            continue
        match = next(((first, last) for first in range(len(tables))
                      for last in range(first, len(tables))
                      if sum(signatures[first:last + 1], Counter()) == written), None)
        if match:
            found.append((start, end, *match))
    return found


# In the structure pass a recovered table stands in the text as one line of
# this token, so the formatter never writes the table itself.  Not CJK and no
# mark: the character and mark checks do not see it.
TABLE_TOKEN = "⟦T{}⟧"


def table_tokens(count: int) -> list[str]:
    return [TABLE_TOKEN.format(number + 1) for number in range(count)]


def place_recovered_tables(formatted: str, tables: Sequence[str]
                           ) -> tuple[str, str | None, list[str]]:
    """The structure pass's output with each token line replaced by its table.

    The formatter was measured rewriting recovered tables it was told to copy:
    turning them back into layout order, joining two into one, turning cells
    round, running out of tokens on a page of figures (30k tokens and a
    truncation for one page), and a recovered table that was sealed never
    reached the page.  With a token in its place it only has to keep one line.

    Each token must be there exactly once; otherwise the output is returned
    unchanged with the reason, and the caller treats it as a refused pass
    (retry, then the per-block assembly).  A table the formatter wrote anyway
    whose cells are a recovered table's (or several joined) is its own copy
    and is dropped, since the token puts the table there.  Returns the text,
    the problem or None, and a note per copy dropped.
    """
    tokens = table_tokens(len(tables))
    lost = [token for token in tokens if token not in formatted]
    repeated = [token for token in tokens if formatted.count(token) > 1]
    if lost or repeated:
        problem = "; ".join(part for part in (
            ("lost " + " ".join(lost)) if lost else "",
            ("repeated " + " ".join(repeated)) if repeated else "") if part)
        return formatted, f"table placeholder line(s) {problem} - each must be kept exactly once", []
    notes = []
    for start, end, first, last in reversed(recovered_runs(formatted, tables)):
        formatted = formatted[:start] + formatted[end:]
        notes.append(f"the formatter's own copy of table(s) {first + 1}"
                     + (f"-{last + 1}" if last > first else "") + " dropped; the recovered table stands")
    for token, table in zip(tokens, tables):
        # The token's whole line, with any list or quote marker put in front
        # of it; a token inside a line of text still becomes its own block.
        line = re.compile(rf"^[ \t]*(?:[-*+>][ \t]*|\d+[.)][ \t]*)?{re.escape(token)}[ \t]*$", re.M)
        block = f"\n\n{table}\n\n"
        formatted = (line.sub(lambda _: block, formatted, count=1) if line.search(formatted)
                     else formatted.replace(token, block, 1))
    formatted = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", formatted).strip("\n")
    return formatted, None, notes


def cells_turned_as_recovered(written: str, recovered: "_tables.Table") -> str | None:
    """A written table that stands as written (tablesDisputed) with each cell
    that reads the other way round from a recovered cell turned to match it.

    The formatter's layout stands there, but which way a label reads was
    settled by fix_cell_direction, and on the whole-page pass the formatter
    was given the cells as read (cells_as_read).  A cell that is a recovered
    cell as written is matched first, so a label that reads either way is
    never turned.  Returns None when no cell is turned, or when the table
    cannot be written again without losing text outside its cells.
    """
    got = first_table(written)
    if got is None:
        return None
    wanted = Counter(CJK(cell.text) for cell in recovered.cells if CJK(cell.text))
    texts = [cell.text for cell in got.cells]
    matched = [False] * len(texts)
    for n, text in enumerate(texts):
        if wanted[CJK(text)] > 0:
            wanted[CJK(text)] -= 1
            matched[n] = True
    turned = False
    for n, text in enumerate(texts):
        if not matched[n] and CJK(text) and wanted[CJK(text)[::-1]] > 0:
            wanted[CJK(text)[::-1]] -= 1
            texts[n], turned = turned_round(text), True
    if not turned:
        return None
    rewritten = _tables.with_texts(got, texts)
    markup = (_tables.render_html(rewritten) if got.kind == "html" or rewritten.has_spans()
              else _tables.render_markdown(rewritten))
    return markup if Counter(CJK(markup)) == Counter(CJK(written)) else None


def enforce_recovered_tables(formatted: str, tables: Sequence[str]
                             ) -> tuple[str, list[str], list[int], list[int]]:
    """Put each recovered table back where the formatter rendered it.

    The formatter is told the tables are already in reading order and to copy
    them, and it usually does; but a model looking at a page of vertical
    columns will sometimes transpose the copy back into layout order (measured
    on the same page, same decision upstream, two runs apart).  The recovered
    table's orientation was settled by the totals arithmetic or an explicit
    verdict, so an output table holding the same cells is put back in the
    recovered layout, with the recovered cell texts: which way a label cell
    reads was settled by fix_cell_direction's checked question, and a cell
    the formatter turned round anyway is put back.  On the whole-page pass
    the formatter copies the cells as the engines read them (cells_as_read),
    and this is where the turned ones are written.  A <caption> it gave the
    table stays with it.

    The recovered layout is not forced over everything.  An output table that
    merges cells the recovered grid keeps apart is the formatter's reading of
    the image, and the engine's plain grid is no evidence against it - engines
    flatten merges too - so it is kept and reported as disputed.  So is a table
    holding text outside its cells, which a replacement would delete.

    Returns the text, a note per table touched, the index of every recovered
    table that is not in the output as a table at all (written as prose, left
    out, or fenced as code), and the index of every disputed table; the caller
    flags the page for both.
    """
    notes: list[str] = []
    missing: list[int] = []
    disputed: list[int] = []
    # A written table holding exactly the cells of several recovered tables
    # is those tables joined, not a table of the page: the engine read them
    # as separate blocks.  Measured: the formatter joined a year table and
    # the label column of the next table into one three-column table, which
    # put one table's figures under the other's categories; its cells were
    # the two recovered tables' cells exactly (50 = 50).  It is put back as
    # the recovered tables, each then enforced below.
    for start, end, first, last in reversed(recovered_runs(formatted, tables)):
        if last > first:
            formatted = (formatted[:start] + "\n\n".join(tables[first:last + 1])
                         + formatted[end:])
            notes.append(f"tables {first + 1}-{last + 1} were written as one table; put back "
                         "as the separate recovered tables")
    for number, table in enumerate(tables):
        want = table_signature(table)
        recovered = first_table(table)
        if not want or recovered is None:
            continue
        kind = "HTML" if recovered.kind == "html" else "Markdown"
        for start, end in reversed(table_spans(formatted)):
            written = formatted[start:end]
            if table_signature(written) != want:
                continue
            if written.strip() == table.strip():
                break
            got = first_table(written)
            got_kind = "HTML" if got is not None and got.kind == "html" else "Markdown"
            if got is not None and _tables.simplify(got).has_spans() and (
                    _tables.simplify(got).layout() != recovered.layout()):
                disputed.append(number)
                turned = cells_turned_as_recovered(written, recovered)
                if turned is not None:
                    formatted = formatted[:start] + turned + formatted[end:]
                notes.append(f"table {number + 1}: the formatter's {got_kind} table merges cells the "
                             f"recovered {kind} table does not; kept as written - nothing vouches "
                             "for either layout" + (", its cells turned as the recovered table's"
                                                    if turned is not None else ""))
                break
            restored = _tables.with_texts(recovered, [cell.text for cell in recovered.cells])
            caption = got.caption if got is not None else ""
            if recovered.kind == "html":
                restored.caption = caption or restored.caption
                markup = _tables.render_html(restored)
            else:
                markup = (caption + "\n\n" if caption else "") + _tables.render_markdown(restored)
            if Counter(CJK(markup)) != Counter(CJK(written)):
                disputed.append(number)
                turned = cells_turned_as_recovered(written, recovered)
                if turned is not None:
                    formatted = formatted[:start] + turned + formatted[end:]
                notes.append(f"table {number + 1}: the formatter's {got_kind} table holds text "
                             "outside its cells; kept as written rather than delete it"
                             + (", its cells turned as the recovered table's" if turned is not None else ""))
            elif markup.strip() != written.strip():
                formatted = formatted[:start] + markup + formatted[end:]
                notes.append(f"table {number + 1} restored as the recovered {kind} table "
                             f"(the formatter had written a different {got_kind} table)")
            break
        else:
            missing.append(number)
            notes.append(f"table {number + 1} ({kind}) is not in the formatter output as a table")
    return formatted, notes, missing, disputed


def agreed_block_text(blocks: Sequence[Mapping[str, Any]],
                      segments: Sequence[Mapping[str, Any]]) -> list[str]:
    """Per engine-A block, the characters both engines read there: the merge's
    agreed segments, placed by their offset in engine A's stream."""
    ends = list(itertools.accumulate(len(CJK(str(b.get("text") or ""))) for b in blocks))
    out: list[list[str]] = [[] for _ in blocks]
    cursor, k = 0, 0
    for seg in segments:
        if seg["tag"] == "equal":
            for offset, ch in enumerate(seg["a"]):
                while k < len(ends) - 1 and cursor + offset >= ends[k]:
                    k += 1
                out[k].append(ch)
        cursor += len(seg["a"])
    return ["".join(chars) for chars in out]


def table_characters_lost(blocks: Sequence[Mapping[str, Any]], agreed: Sequence[str],
                          written: str, heads: Sequence[str] = (),
                          furniture: frozenset[str] | set[str] = frozenset(),
                          beside: Mapping[int, str] | None = None) -> str:
    """Characters both engines read inside the table blocks that the page lacks.

    The deletion guard (deleted_runs) compares runs of prose and leaves table
    blocks out, because a table rotated into reading order breaks the engine's
    runs by design; and tablesMissing only asks whether a recovered table is
    there.  So a refused table - whose text the formatter must rebuild - could
    lose characters without anything noticing.  Counts, not runs: the page
    must hold each character at least as often as the table blocks alone do
    (prose only adds to the page's count, so it cannot cause a report, only
    hide one).  `agreed` is agreed_block_text: characters only one engine has
    at a table's place are left out, because engine B's reading of something
    else lands in that gap - measured on the 14 accepted tables of merge-mode
    pages, the merged span of 5 holds characters their table does not (the
    book's running head in 3), which the formatter rightly removes; counting
    agreed characters, a page holding exactly the recovered tables reports
    nothing.  A table row that is the running head (furniture_row) is not
    counted either.  Measured on the existing phase-3 outputs (22 merge-mode
    pages with a table block): this reports 8, six of them sealed clean.
    Seven are losses against the page - zeros the old character class
    dropped, figures dropped from tables the old restatement scrambled, every
    table on a page the old fallback discarded; the eighth is two characters
    both engines misread and the formatter deleted, a deletion it is not
    allowed either.  Returns the lost characters in page order, or "".

    BESIDE (block position -> characters) adds blocks that are not tables
    but must be counted as if they were: the blocks a rebuilt table's
    refused region named, which stay text beside it (beside_table), each
    with what both engines read of it.
    """
    beside = beside or {}
    needed: Counter = Counter()
    for index, (block, text) in enumerate(zip(blocks, agreed)):
        if index in beside and block.get("label") != "table":
            needed.update(CJK(beside[index]))
        if block.get("label") != "table":
            continue
        needed.update(text)
        grid = _tables.parse_html_table(str(block.get("text") or ""), source_breaks=True)
        for r in range(grid.rows if grid is not None else 0):
            row = "".join(cell.text for cell in grid.cells if cell.row == r)
            if is_furniture_text(row, heads, furniture):
                needed.subtract(CJK(row))
    lost = +(needed - Counter(CJK(written)))
    out = []
    for index, (block, text) in enumerate(zip(blocks, agreed)):
        if block.get("label") == "table" or index in beside:
            for ch in (text if block.get("label") == "table" else CJK(beside[index])):
                if lost[ch] > 0:
                    out.append(ch)
                    lost[ch] -= 1
    return "".join(out)


def punctuation_kept(before: str, after: str) -> bool:
    """Every mark survives; the only additions allowed are list-number brackets."""
    gained = punctuation_counts(after) - punctuation_counts(before)
    lost = punctuation_counts(before) - punctuation_counts(after)
    return not lost and set(gained) <= {"（", "）"}


def is_running_head(text: str, heads: Sequence[str], similarity: float = 0.8) -> bool:
    """Whether a block's text is one of the book's measured running heads.

    Similarity rather than equality: the same head is OCR'd a little
    differently from copy to copy, and an exact test left misread copies in
    the body.  The length check stops a sentence that merely contains the
    head from being taken for it.
    """
    text = CJK(text)
    for head in heads:
        if not head or not text:
            continue
        if abs(len(text) - len(head)) > max(1, len(head) // 4):
            continue
        if text == head or difflib.SequenceMatcher(None, text, head, autojunk=False).ratio() >= similarity:
            return True
    return False


def draft_digest(pages: Sequence[int], drafts: Path, drafts2: Path | None) -> str:
    """Identity of the drafts a profile was measured on (its probe pages only)."""
    digest = hashlib.sha256()
    for page in pages:
        for directory in (drafts, drafts2):
            path = (directory / f"page-{page:04d}.txt") if directory else None
            digest.update(f"page-{page:04d}.txt".encode())
            digest.update(sha256_file(path).encode() if path and path.is_file() else b"-")
    return digest.hexdigest()


def profile_fingerprint(profile: Mapping[str, Any] | None) -> str:
    """Hash of what proofreading consumes from a profile, not of the file.

    Timestamps, usage and evidence change on every re-profile; sealing the
    file bytes would mark every page stale whenever the evidence was
    refreshed but the decisions were not.
    """
    if not profile:
        return ""
    engines = profile.get("engines") or {}
    notation = {k: v for k, v in (profile.get("notation") or {}).items() if k != "blocks"}
    return sha256_bytes(canonical_json_bytes({
        "schemaVersion": profile.get("schemaVersion"),
        "preferEngine": profile.get("preferEngine"),
        "decision": engines.get("decision"),
        "notation": notation,
        "layout": profile.get("layout"),
        "pairClasses": profile.get("pairClasses"),
    }))


# decision_provenance keys written only when a switch is on (absent = off).
# tieBreak, censusConfirmsEngineA, folioFurniture, topRungCaps,
# textColumnLayout, engineAConfirmsFirstAnswer and censusSkipOverCeiling are
# the keys of trials and switches that are gone (the last three replaced by
# the rule agreedMarks): no run writes them now, and a page sealed with one
# on is not current.
OPTIONAL_PROVENANCE = ("tieBreak", "censusConfirmsEngineA", "folioFurniture", "topRungCaps", "textColumnLayout",
                       "keepArabicNumerals", "loopDetector", "neighbourGuard")
# Switch --learned-heads (learned_heads_seal).
OPTIONAL_PROVENANCE += ("learnedHeads",)
OPTIONAL_PROVENANCE += ("engineAConfirmsFirstAnswer", "censusSkipOverCeiling")
# The round-4 fixes, on by default since the user's decision of 2026-09-30
# (after the round-4 benchmark), each with the provenance key it seals when
# on.  --no-<switch> turns one off.  Provenance records each one's state
# (present when on, absent when off), so a page sealed under the old
# defaults (all four off) is not current for a run with the new ones
# (validate_completion says which switches resume it), and a run that would
# decide the rest of such a book under the new ones does not start
# (round4_split): a book begun under the old defaults resumes with the four
# --no-* switches (its sealed pages stay current and the rest are decided
# the same way), or is proofread again, every page, with --overwrite; its
# sealed pages are never mixed with pages decided under the new defaults.
ROUND4_DEFAULTS = {"--keep-arabic-numerals": "keepArabicNumerals", "--loop-detector": "loopDetector",
                   "--neighbour-guard": "neighbourGuard", "--learned-heads": "learnedHeads"}
# Rules that are always on now, with the value a page sealed with each on
# carries: their switches went (2026-10-04) - the user's decided rules
# (D1-D5, D9, D15, D16, D20-D25, D27), the switches kept only for measuring,
# the chart step (its keys charts, chartVariants, chartReview, foldCut) and
# the proposed grid follow (gridFollow, never fired) - and their keys stay so
# that the pages sealed before are still current (validate_completion).
SEALED_ON = {
    "circleRule": "on", "bracketRule": "on", "doubledCensus": "on", "insertionCrop": "on",
    "furnitureInsertion": "on", "boundaryCrop": "on", "moveOnce": "on", "answerDeletionGuard": "on",
    "wholeRunCrop": "on", "foldedAlignment": "on", "lopsidedSplit": "on", "mergeLossGuard": "on",
    "cropCoverageCheck": "on", "answerNearby": "on", "leaderMarks": "on", "bulletMarks": "on", "markRule": "on",
    "charts": "on", "chartVariants": "on", "chartReview": "doubt", "rebuildLock": "both",
    "markAwareWitness": "on", "rebuildRegion": "on", "gridFollow": "on", "tableEdgeText": "on",
    "rebuiltOrientation": "on", "verdictPropagation": "on", "foldCut": "on",
}


def decision_provenance(arguments: Any) -> dict[str, str]:
    """What decided a page's characters, sealed so a resume under different
    decisions (another profile, new rulings, a switch set otherwise) is not
    mixed in."""
    return {
        "proofreadTransport": getattr(arguments, "backend", "packet"),
        "proofreadModel": getattr(arguments, "model", None) or DEFAULT_MODEL,
        "proofreadEndpoint": (getattr(arguments, "endpoint", None) or "")
                             if getattr(arguments, "backend", "packet") == "http" else "",
        "proofreadScriptSha256": sha256_file(Path(__file__)),
        "contentFidelitySha256": sha256_file(Path(_content_fidelity.__file__)),
        "contentNoteMarkersSha256": sha256_file(Path(_content_fidelity.__file__).with_name("_note_markers.py")),
        "profileSha256": getattr(arguments, "profile_sha", "") or "",
        "enginePreference": getattr(arguments, "prefer_engine", "") or "",
        "fallbackEngine": getattr(arguments, "fallback_engine", "") or "",
        "variantRulingsSha256": getattr(arguments, "variant_rulings_sha", "") or "",
        # The book-independent lists that also decide characters: which pairs
        # are two forms of one character (variant_classes).
        "variantListSha256": (user_variant_rulings(), _LIST_SHA256.get("variantList", ""))[1],
        "allographsSha256": (opencc_allographs(), _LIST_SHA256.get("allographs", ""))[1],
        # Since 2026-10-13 only the lists say which pairs are forms of one
        # character, never the model (classify_pair), so a page sealed when
        # the model's answer could decide is not current.
        "variantPairs": "lists",
        **SEALED_ON,
        # Whether a rebuilt table may write up to UNREAD_TABLE_LIMIT characters
        # no engine read, each confirmed by 裁決 (headerCharacters, confirm_unread).
        "unreadTableCharacters": "off" if getattr(arguments, "no_unread_table_characters", False) else "on",
        # The glyphs the mark rule takes for marks come from the book's drafts
        # (mark_aliases), so a page sealed under other evidence is not current.
        "markAliasesSha256": (lambda found: sha256_bytes(canonical_json_bytes(found))
                              if any(found.values()) else "")(getattr(arguments, "mark_aliases", None) or {}),
        # The rule agreedMarks, always on (the user's ruling of 2026-10-04, no
        # switch): a block whose marks both engines read alike is written with
        # them, unasked (agreed_marks).  A page sealed before it is not
        # current.
        "agreedMarks": "on",
        # Engine A's figures written where they are the only reading of their
        # print (--keep-arabic-numerals): the book's figures and the glyphs
        # engine B writes for them, from the drafts (figure_evidence), so a
        # page sealed under other evidence is not current.  Only when on.
        **({"keepArabicNumerals": figure_evidence_seal(arguments)}
           if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Switch --loop-detector: requests streamed and a rung given up in a
        # word-for-word loop of its reasoning, by this rule (_loop_watch).
        # Only when on.
        **({"loopDetector": _loop_watch.RULE.text()} if getattr(arguments, "loop_detector", False) else {}),
        # Switch --neighbour-guard: a 裁決 answer that copies the text beside
        # its spot is not written (neighbour_copy), within this window.  Only
        # when on.
        **({"neighbourGuard": f"window={NEIGHBOUR_WINDOW}"} if getattr(arguments, "neighbour_guard", False)
           else {}),
        # Switch --learned-heads: the running head learned by place, sealed by
        # what was learned (learned_heads_seal), so a page sealed under another
        # book's evidence or another profile's is not current.  Only when on.
        **({"learnedHeads": learned_heads_seal(arguments)} if getattr(arguments, "learned_heads", False) else {}),
        "tableOrientation": getattr(arguments, "table_orientation", "auto") or "auto",
    }


def profile_section(arguments: Any, key: str) -> dict[str, Any]:
    return ((getattr(arguments, "profile_data", None) or {}).get(key)) or {}


def variant_ruling_by(entry: Mapping[str, Any]) -> str:
    """Who made one ruling of a book's variant-rulings.json: "model" or
    "person".  The model's are those of the variant vote book_profile.py ran
    until 2026-10-12, in the files it wrote then; they are applied as a
    person's are.  A ruling the model wrote whose printed form a person has
    since changed is the person's: it no longer says what the model chose
    (modelPrinted)."""
    if entry.get("ruledBy") == "model" and entry.get("printed") == entry.get("modelPrinted"):
        return "model"
    return "person"


def variant_person_unruled(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The entries of a rulings file's modelUnruled (pairs the variant vote
    left unruled, in a file it wrote; it never gave them a printed form) that
    a person gave a printed form of the pair: that person's rulings, where
    they found the pair."""
    out = []
    for entry in raw.get("modelUnruled") or []:
        if not isinstance(entry, Mapping):
            continue
        forms = [CJK(str(f)) for f in entry.get("forms") or []]
        if len(forms) == 2 and CJK(str(entry.get("printed") or "")) in forms:
            out.append(entry)
    return out


def variant_rulings_seal(raw: Mapping[str, Any], path: Path, rulings: Mapping[frozenset, str],
                         ruled_by: Mapping[frozenset, str]) -> str:
    """The hash of a book's variant rulings its pages seal
    (decision_provenance's variantRulingsSha256).

    A file a person wrote is sealed by its bytes, as it always was.  A file
    the variant vote wrote (book_profile.py, until 2026-10-12) was written
    again every time the book was profiled, with that run's own record - its
    time, seconds and usage, each crop's reply - so its bytes changed when no
    ruling did, and a resumed run would have refused every page it sealed.
    Such a file is sealed by what decides the pages: RULINGS, each pair's
    printed form as loaded, and RULED_BY, who ruled it (which the pages
    record); the pages sealed under it stay current."""
    entries = [e for e in raw.get("rulings") or [] if isinstance(e, Mapping)]
    if not ("modelVote" in raw or "modelUnruled" in raw or any(e.get("ruledBy") == "model" for e in entries)):
        return sha256_file(path)
    return sha256_bytes(canonical_json_bytes(sorted(
        ["|".join(sorted(key)), printed, ruled_by.get(key, "person")] for key, printed in rulings.items())))


def resolve_variant(seg: Mapping[str, Any], arguments: Any) -> tuple[str, str, list[str]]:
    """Resolve a variant-form disagreement from the book's per-pair rulings.

    A pair without a ruling takes the engine the book's rulings favour (the
    engine whose forms were ruled printed more often); with no rulings at all,
    the reading preference.  Unruled pairs are reported, not hidden.  A
    ruling the model made (variant_ruling_by) is used as a person's is.
    In a book printed in simplified script (simplified_script), a pair
    without a ruling whose forms differ in script alone (one is the other's
    only in traditional script: 諭/谕, 並/并; traditional_forms) takes engine
    B's form, and is not reported unruled: engine B reads the script as
    printed, engine A does not.  Measured against the images of a multi-volume archival compilation 2, 3
    and 5 (2026-10-10): engine A read unit titles printed in simplified
    script in traditional script (a multi-volume archival compilation 3: 63 sites, which the engine A
    default wrote), and traditional forms printed in body text in simplified
    script (舖, 趲, 雋, 旂 on a multi-volume archival compilation 3; 並, 貲, 揀, 執 on a multi-volume archival compilation 5);
    engine B read the printed form at 29 of the 31 sites looked at (not 恥
    and 學, where engine A read 耻 and 学 as printed).

    The label is the segment's, which may hold several sites:
    variant-ruling when a person's rulings decided every site,
    variant-model-ruling when the model's did, variant-person-and-model-ruling
    when both did, variant-script when the print's script decided every site,
    and the engine the unruled sites took otherwise (a site the script
    decided counts as ruled there).  Which sites the model ruled is listed per
    site (variant_model_sites).
    """
    rulings = getattr(arguments, "variant_rulings", None) or {}
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    engine = getattr(arguments, "variant_engine", None) or (
        arguments.prefer_engine if arguments.prefer_engine in ("A", "B")
        else getattr(arguments, "fallback_engine", "A"))
    script = traditional_forms() if getattr(arguments, "simplified_script", False) else {}
    a, b = CJK(seg["a"]), CJK(seg["b"])
    out, unruled, ruled, by_model, by_script = [], [], 0, 0, 0
    for x, y in zip(a, b):
        if x == y:
            out.append(x)
            continue
        printed = rulings.get(frozenset((x, y)))
        if printed in (x, y):
            out.append(printed)
            ruled += 1
            by_model += ruled_by.get(frozenset((x, y))) == "model"
        elif y in script.get(x, "") or x in script.get(y, ""):
            out.append(y)
            by_script += 1
        else:
            out.append(y if engine == "B" else x)
            unruled.append(f"{x}|{y}")
    how = ("variant-script" if not unruled and not ruled
           else "variant-model-ruling" if not unruled and by_model == ruled
           else "variant-person-and-model-ruling" if not unruled and by_model
           else "variant-ruling" if not unruled
           else f"variant-unruled-engine-{engine}" if not ruled and not by_script
           else f"variant-partly-ruled-engine-{engine}")
    return "".join(out), how, unruled


def variant_model_sites(disagreements: Sequence[Mapping[str, Any]], arguments: Any) -> list[str] | None:
    """The variant pairs (engine A|engine B, one per site) a page wrote from
    the model's rulings, in every segment resolve_variant decided, whatever
    else the segment held; None when the book has no model ruling, so a page
    of a book ruled by a person alone is sealed as it always was."""
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    if "model" not in ruled_by.values():
        return None
    rulings = getattr(arguments, "variant_rulings", None) or {}
    return [f"{x}|{y}" for d in disagreements if str(d.get("resolved_by") or "").startswith("variant-")
            for x, y in zip(CJK(d.get("engineA") or ""), CJK(d.get("engineB") or ""))
            if x != y and ruled_by.get(frozenset((x, y))) == "model" and rulings.get(frozenset((x, y))) in (x, y)]


def variant_rulings_report(arguments: Any, model_sites: Mapping[str, int]) -> dict[str, Any]:
    """The run report's account of the book's variant rulings: the file, and
    which pairs a person ruled and which the model did, with the sites
    the model's rulings decided on the pages of this report (MODEL_SITES,
    engine A|engine B per site)."""
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    rulings = getattr(arguments, "variant_rulings", None) or {}
    labels = getattr(arguments, "variant_ruling_forms", None) or {}
    sites: Counter = Counter()
    for pair, n in model_sites.items():
        sites[frozenset(pair.split("|"))] += n
    # sha256 is what the pages sealed (variant_rulings_seal); fileSha256 the
    # file's bytes as read.
    out: dict[str, Any] = {"file": getattr(arguments, "variant_rulings_file", None),
                           "sha256": getattr(arguments, "variant_rulings_sha", "") or "",
                           "fileSha256": getattr(arguments, "variant_rulings_file_sha", "") or "",
                           "person": [], "model": []}
    for key, printed in rulings.items():
        row = {"pair": labels.get(key) or "|".join(sorted(key)), "printed": printed}
        if ruled_by.get(key) == "model":
            out["model"].append(dict(row, occurrences=sites.get(key, 0)))
        else:
            out["person"].append(row)
    out["model"].sort(key=lambda row: -row["occurrences"])
    return out


# How far a block's size per character may be from a running head's measured
# glyph (runningHeadGlyph) and still be set in the head's type size.
HEAD_SIZE_RATIO = (0.7, 1.4)


def head_size_matches(block: Mapping[str, Any], text: str, arguments: Any) -> bool:
    """Whether a block matching a running head is set in the head's own type size."""
    glyph = profile_section(arguments, "layout").get("runningHeadGlyph") or {}
    heads = [h for h in glyph if is_running_head(text, [h])]
    box = block.get("box")
    if not heads or not (isinstance(box, list) and len(box) == 4):
        return not glyph  # profiles without sizes keep the text-only test
    size = max(abs(box[2]), abs(box[3])) / max(len(CJK(text)), 1)
    return any(HEAD_SIZE_RATIO[0] <= size / glyph[h] <= HEAD_SIZE_RATIO[1] for h in heads if glyph[h])


def density_limit(arguments: Any) -> float:
    """Degenerate-output bound, scaled to this book's densest printed block.

    A fixed bound encoded one book's habits: a page printing a circle after
    every character (圈點, marks per character ~1) would be rejected as
    garbage.  With a profile the bound is a quarter above the densest block
    the probe actually measured; without one it stays at the old constant.
    """
    measured = profile_section(arguments, "notation").get("maxMarksPerCharacter")
    if not measured:
        return DEGENERATE_PUNCTUATION_DENSITY
    return max(DEGENERATE_PUNCTUATION_DENSITY, min(250.0, 100.0 * measured * 1.25))


def census_ceiling(arguments: Any, characters: int) -> int:
    """Largest believable census for a block of this many characters."""
    measured = profile_section(arguments, "notation").get("maxMarksPerCharacter")
    ratio = 0.5 if not measured else max(0.5, min(2.2, measured * 1.5))
    return int(characters * ratio) + 2


def notation_hint(arguments: Any) -> str:
    """What the probe saw this book print, as evidence for the punctuator."""
    notation = profile_section(arguments, "notation")
    lines = []
    counts = notation.get("markBlocks") or {}
    total = notation.get("blocksSurveyed") or 0
    if counts and total:
        seen = "、".join(f"「{mark}」{n}" for mark, n in counts.items())
        lines.append(f"本書抽樣 {total} 個 block，見到各符號嘅 block 數：{seen}。"
                     "少數 block 先見到嘅符號可能係誤認（例如雙圈細看似「，」），寫之前要喺圖上確認形狀。")
    elif notation.get("marks"):
        lines.append(f"本書抽樣頁見過嘅符號：{''.join(notation['marks'])}。")
    if notation.get("doubled"):
        lines.append("本書有雙圈：圖上兩個圈連住印，就寫「。。」，唔好合併做一個。")
    emphasis = notation.get("emphasis")
    if emphasis:
        shown = notation.get("emphasisBlocks") or {}
        lines.append(f"抽樣 {total or '若干'} 個 block 之中有 {shown.get(emphasis, '幾')} 個報告有圈點"
                     f"（一串字逐個字旁邊印{emphasis}）：只有裁圖上真係逐個字旁邊都印咗先照寫，"
                     f"{'圈寫「。」' if emphasis == '圈' else '點寫「、」'}，逐個跟圖，唔好推廣去冇印嘅字。")
    return ("\n".join(lines) + "\n") if lines else ""


def census_tolerance(census: int) -> int:
    """How far the marks written may stray from the census and still match it.

    One definition for the pass that is refused on it and for the seal that
    reports a kept stretch outside it (punctuation_doubts).
    """
    return max(2, census // 4)


def mark_places(text: str) -> int:
    """How many marks TEXT carries as the census counts them: places, where a
    doubled circle 。。 is one (the census reports 雙圈 apart), and so is a
    bullet printed twice (⦿⦿, bullet_runs)."""
    marks = sum(punctuation_counts(text).values())
    return marks - text.count("。。") - sum(len(run) - 1 for _, run in bullet_runs(text))


def bullet_runs(text: str) -> list[tuple[int, str]]:
    """Each bullet TEXT writes, as (where in TEXT, the run): one bullet, or the
    same one printed twice or more in a row (⦿⦿), which marks one item.  ●, ⦿
    and ◉ are one bullet (BULLET_SHAPES, the user's ruling), so a run mixing
    them (●⦿) is one item too."""
    out: list[tuple[int, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in BULLET_MARKS:
            end = index
            shape = char.translate(BULLET_SHAPES)
            while end < len(text) and text[end] in PUNCT_MARKS and text[end].translate(BULLET_SHAPES) == shape:
                end += 1
            out.append((index, text[index:end]))
            index = end
            continue
        index += 1
    return out


SHAPE_LINE = re.compile(r"形狀[：:][ \t]*([^\n]*)")


def bullet_contradicted(bullet: str, shapes: str | None) -> bool:
    """Whether the census's shape line (what it saw on the crop) names a
    bullet, but not BULLET: two readings of one crop that disagree on the
    shape.  Sealed as evidence beside the bullet's D5 entry
    (shapeContradicted), never asked about: the user does not want bullet
    shapes put to them, and the bullet stays as written.  A line naming no
    bullet says nothing about one, and ●, ⦿ and ◉, one or two of them, are
    one bullet (BULLET_SHAPES, the user's ruling)."""
    named = [c.translate(BULLET_SHAPES) for c in (shapes or "") if c in BULLET_MARKS]
    return bool(named) and bullet[:1].translate(BULLET_SHAPES) not in named


def doubled_shown(arguments: Any) -> bool:
    """Whether the census's doubled-circle count is put to the punctuator as a
    shape to write, or kept for the seal alone.

    The count is one reading of one crop; the profile's notation survey is the
    book's.  Where the survey looked and found no doubled circles, a crop count
    above zero is recorded and reported (doubled-count-mismatch) but not
    written: a false count would put 。。 into a book that prints none, and
    nothing on the page could correct it.  With no survey there is no book
    evidence against it and the crop's count stands.
    """
    notation = profile_section(arguments, "notation")
    return bool(notation.get("doubled")) or not notation.get("blocksSurveyed")


def census_hint(census: int | None, doubled: int | None, show: bool) -> str:
    """The census, told to the punctuator: places and, where they are to be
    written (doubled_shown), how many are doubled circles."""
    if census is None:
        return ""
    if show and doubled:
        shape = f"，其中 {doubled} 處係雙圈（兩個圈連住，寫「。。」）"
    elif show and doubled == 0:
        shape = "，冇雙圈"
    elif doubled is None:
        shape = "（兩個圈連住嘅雙圈算一處）"
    else:
        shape = ""
    return f"你先前數過呢個 block 有 {census} 處印咗符號{shape}；輸出應該接近呢個數，位置逐個由圖讀。\n"


# The forms engines and models write for the marks PUNCT_MARKS names: vertical
# presentation forms and ASCII brackets.
MARK_FORMS = str.maketrans({"︵": "（", "︶": "）", "(": "（", ")": "）",
                            "﹁": "「", "﹂": "」", "﹃": "『", "﹄": "』"})
# A mark that opens belongs with the character after it; every other mark with
# the character before it.
OPENING_MARKS = frozenset("（「『《〈")
# The marks a pass whose answers are both refused keeps where a refused
# answer and engine A put them at one place (agreed_punctuation): the marks
# the step counts, and the curly quotation marks, which it neither counts nor
# asks for but which a book in simplified script prints and both the
# punctuator and engine A write.  Measured on such a book (2026-10-25): on a
# page whose two answers were refused (12 quotation marks in each, 12 in
# engine A), the fallback kept 116 marks and no quotation mark (11 lost
# against the user's copy of the book); replayed offline on the sealed
# answers with these marks, 127, 11 quotation marks among them.  Only this
# fallback reads them (anchored_marks' VOCABULARY), so nothing else the step
# counts or places changes; the opening ones anchor to the character after.
OPENING_QUOTES = frozenset("“‘")
FALLBACK_MARKS = PUNCT_MARKS + "“”‘’"
# Markup in an engine's gap (HTML tags and entities): not print.
GAP_MARKUP = re.compile(r"<[^>]*>|&[A-Za-z0-9#]+;")


def gap_sides(gap: str, by_print: bool = True) -> tuple[str, str]:
    """The marks of one gap of an engine's text (engine_gaps), in the order
    written, parted by where they go - (with the character before, with the
    character after) - for the text beside a table and a block written with
    the engines' marks (seam_sides, marks_as_written).  The marks are
    PUNCT_MARKS.

    Print in the gap that is no mark (digits or letters, which the merge
    does not align) parts it: the marks written before it go with the
    character before, the marks after it with the character after.  Engine A
    read a numbered item's head 。(2) between two characters; anchored_marks
    ties 。 and ) after the character before and ( before the one after, so
    the pair comes out reversed, 。）…（ (the replay of one book's sealed pages
    wrote 。） at the three such heads of a page, where the print is 。（2）).
    With no such print, the gap parts at its first opening mark, as
    anchored_marks ties them, but a mark written after an opening one stays
    after it: （） is not written ）（.  With BY_PRINT false the gap parts as
    though it held no such print (marks_as_written: nothing in the merged
    text stands for it)."""
    written = GAP_MARKUP.sub("", gap).translate(MARK_FORMS)
    parted = by_print and any(unicodedata.category(c)[0] in "LN" for c in written)
    lead, tail = [], []
    after = False
    for c in written:
        if c in PUNCT_MARKS:
            # With print in the gap, the marks after it; without, the marks
            # from the first opening one on.
            after = after or (c in OPENING_MARKS and not parted)
            (tail if after else lead).append(c)
        elif parted and unicodedata.category(c)[0] in "LN":
            after = True
    return "".join(lead), "".join(tail)


def seam_sides(gap: str, by_print: bool = True) -> tuple[str, str]:
    """The marks (PUNCT_MARKS) of one gap of an engine's text, in the order
    written, parted by where they go - (with the character before, with the
    character after) - for the text beside a table (marks_as_written).

    As gap_sides parts them (BY_PRINT as there), but a seam (TEXT_SEAMS: a
    line break, a table cell's markup) parts the gap first: a mark stays with
    the line it is written on - before the gap's first seam with the
    character before, after its last seam with the character after, between
    two seams (a line of marks alone) as gap_sides parts them - except that
    the opening marks and bullets that end the line before (after its last
    other mark or print) open the line after, and the closing and sentence
    marks that start the line after (before its first opening mark, bullet
    or print) close the line before: wrapped there, as a comma an engine
    writes at the head of the next line.  Engine B read a title ending in
    （） on its own line above a table: with no seam the pair goes with the
    table's first cell, as a mark after an opening one does, and the edge
    text lacks it.  A numbered head (2) or a bullet ▲ starting a line goes
    with that line, where the old order split the pair or gave the bullet to
    the line before.  The order written is kept: once a mark goes with the
    character after, so do the marks written after it.  Dashes are left
    out, as anchored_marks leaves them: nothing counts or keeps them here."""
    parts = TEXT_SEAMS.split(gap)
    if len(parts) == 1:
        lead, tail = gap_sides(gap, by_print)
        return "".join(c for c in lead if c in PUNCT_MARKS), "".join(c for c in tail if c in PUNCT_MARKS)
    opens = OPENING_MARKS | frozenset(BULLET_MARKS)

    def line(part: str) -> list[str]:
        """A line's marks and print (letters, digits), in the order written."""
        written = GAP_MARKUP.sub("", part).translate(MARK_FORMS)
        return [c for c in written if c in PUNCT_MARKS or unicodedata.category(c)[0] in "LN"]

    # (mark, whether it goes with the character before), in the order written.
    sides: list[tuple[str, bool]] = []
    first = line(parts[0])
    k = len(first)
    while k and first[k - 1] in opens:
        k -= 1
    sides += [(c, j < k) for j, c in enumerate(first) if c in PUNCT_MARKS]
    lead, tail = gap_sides("".join(parts[1:-1]), by_print)
    sides += [(c, True) for c in lead if c in PUNCT_MARKS] + [(c, False) for c in tail if c in PUNCT_MARKS]
    last = line(parts[-1])
    k = 0
    while k < len(last) and last[k] in PUNCT_MARKS and last[k] not in opens:
        k += 1
    sides += [(c, j < k) for j, c in enumerate(last) if c in PUNCT_MARKS]
    before, after, parted = [], [], False
    for c, with_before in sides:
        parted = parted or not with_before
        (after if parted else before).append(c)
    return "".join(before), "".join(after)


def anchored_marks(text: str, vocabulary: str = PUNCT_MARKS) -> tuple[str, list[tuple[int, bool, str]]]:
    """TEXT's characters (as CJK counts them) and its marks (those of
    VOCABULARY), each tied to a character: (index, before, mark) - before
    that character for an opening mark, after it for any other.  A mark with
    no character on its side (a closing mark before the first character, an
    opening one after the last) is tied to the nearest one."""
    chars, gaps = engine_gaps(text)
    out: list[tuple[int, bool, str]] = []
    for g, gap in enumerate(gaps):
        for mark in gap.translate(MARK_FORMS):
            if mark not in vocabulary:
                continue
            opening = mark in OPENING_MARKS or mark in OPENING_QUOTES
            if chars and (opening and g < len(chars) or g == 0):
                out.append((g, True, mark))
            elif chars:
                out.append((g - 1, False, mark))
    return chars, out


def tied_marks(length: int, marks: Sequence[tuple[int, bool, str]],
               place: Mapping[int, int]) -> tuple[list[str], list[str]]:
    """The marks tied after and before each of a text's LENGTH characters:
    PLACE maps a marked character (anchored_marks) to its index there; a mark
    whose character has no place is dropped."""
    after, before = [""] * length, [""] * length
    for index, is_before, mark in marks:
        at = place.get(index)
        if at is None:
            continue
        if is_before:
            before[at] += mark
        else:
            after[at] += mark
    return after, before


def marks_as_written(text: str, place: Mapping[int, int], length: int) -> tuple[list[str], list[str]]:
    """TEXT's marks tied after and before each of LENGTH characters (as
    tied_marks ties them, for gaps_of), in the order TEXT writes them, for
    the text beside a table (engine_marks_merged, engine_b_marks_merged with
    WRITTEN): each gap's marks parted by seam_sides, those that go with the
    character before tied after it and the rest before the character after
    (all of them to the nearest character past TEXT's ends).  PLACE maps
    TEXT's characters (engine_gaps) to their index there; a mark whose
    character has no place is dropped.

    Print in a gap that is no character (a bracketed Arabic numeral) parts
    it (gap_sides) only where the gap's two characters are not neighbours in
    the merged text - there the merge holds another engine's reading of that
    print, as engine A's 第(2)表 against the merge's 第二表 is 第（二）表.
    Where they are neighbours, nothing stands for the print, and the marks
    part as though it were not there: a bracket pair stays together, with
    the line it is written on where a seam parts the gap, else with the
    character after - at the edge of a stretch (gaps_of), both brackets or
    neither.

    anchored_marks ties every closing mark of a gap after the character
    before and every opening one before the character after, so a gap reads
    closing marks first: a bracket pair with no character between its
    brackets, 第(2)表 or 表（）, came out 第）（表, and at the edge of a stretch
    with one bracket, 表）.  A block written with the engines' marks
    (agreed_marks) has its marks placed the same way."""
    chars, gaps = engine_gaps(text)
    after, before = [""] * length, [""] * length
    for g, gap in enumerate(gaps):
        left = place.get(g - 1) if g > 0 else None
        right = place.get(g) if g < len(chars) else None
        lead, tail = seam_sides(gap, left is not None and right is not None and right != left + 1)
        if g == 0:
            lead, tail = "", lead + tail
        if g == len(chars):
            lead, tail = lead + tail, ""
        if lead and left is not None:
            after[left] += lead
        if tail and right is not None:
            before[right] += tail
    return after, before


def gaps_of(tied: tuple[Sequence[str], Sequence[str]], start: int = 0,
            length: int | None = None) -> list[str]:
    """The marks in each of the length + 1 gaps of the stretch [start, start +
    length) of a text, from tied_marks: a mark tied to a character outside the
    stretch is not the stretch's, even at its edge.  Closing marks come first
    in a gap."""
    after, before = tied
    length = len(after) - start if length is None else length
    return [(after[start + g - 1] if g else "") + (before[start + g] if g < length else "")
            for g in range(length + 1)]


def engine_marks_merged(text: str, segments: Sequence[Mapping[str, Any]],
                        pieces: Sequence[str], blocks: Sequence[Mapping[str, Any]] = (),
                        placed: dict[int, str] | None = None,
                        written: bool = False, vocabulary: str = PUNCT_MARKS) -> tuple[list[str], list[str]] | None:
    """The marks engine A printed, tied to the characters of the merged text
    ("".join(pieces)) - tied_marks, for gaps_of; with WRITTEN, in the order
    engine A wrote them (marks_as_written: the text beside a table).
    VOCABULARY: the marks read (anchored_marks; tied_marks only).

    A mark is tied to the character it goes with (anchored_marks) and placed
    only where that character's place in the merged text is exact: agreed, a
    disagreement settled on engine A's own reading, or a one-for-one
    substitution.  None when the segments do not spell engine A's text.

    A table block of an order-conflict page read from engine B's lines
    (window_merge) is a run of segments carrying "window": k.  Together they
    hold each of the block's characters once, but in engine B's order, so
    they spell the block's stretch of engine A's text only as a whole: the
    run stands for that stretch (block k's text, where BLOCKS is given), and
    no mark is placed inside it.  The text around it keeps its exact places.
    Before, one such block made the segments fail to spell engine A's text
    and every block of the page lost engine A's marks (agreed_punctuation).

    PLACED, when given, gets the merged text's characters whose place in
    engine A's reading is exact - those whose marks were placed - each with
    engine A's own character there.
    """
    chars, marks = anchored_marks(text, vocabulary)
    found = engine_places(chars, segments, pieces, blocks)
    if found is None:
        return None
    place, m = found
    if placed is not None:
        placed.update((at, chars[k]) for k, at in place.items())
    if written:
        return marks_as_written(text, place, m)
    return tied_marks(m, marks, place)


def engine_places(chars: str, segments: Sequence[Mapping[str, Any]], pieces: Sequence[str],
                  blocks: Sequence[Mapping[str, Any]] = ()) -> tuple[dict[int, int], int] | None:
    """Where each of engine A's characters CHARS stands in the merged text
    ("".join(pieces)) when its place there is exact (engine_marks_merged), and
    the merged text's length; None when the segments do not spell CHARS."""
    place: dict[int, int] = {}
    e = m = i = 0
    while i < len(segments):
        window = segments[i].get("window")
        if window is not None:
            j = i
            while j < len(segments) and segments[j].get("window") == window:
                j += 1
            own = "".join(seg["a"] for seg in segments[i:j])
            stretch = chars[e:e + len(own)]
            if Counter(stretch) != Counter(own) or (
                    blocks and (not isinstance(window, int) or not 0 <= window < len(blocks)
                                or CJK(str(blocks[window].get("text") or "")) != stretch)):
                return None
            e += len(own)
            m += sum(len(piece) for piece in pieces[i:j])
            i = j
            continue
        own, piece = segments[i]["a"], pieces[i]
        if chars[e:e + len(own)] != own:
            return None
        if piece == own or len(piece) == len(own):
            place.update((e + k, m + k) for k in range(len(own)))
        e += len(own)
        m += len(piece)
        i += 1
    if e != len(chars):
        return None
    return place, m


def gap_marks(gap: str) -> str:
    """One gap's marks as two readings are compared (agreed_marks): ●, ⦿ and
    ◉ one bullet (BULLET_SHAPES, the user's ruling) and a run of leader dots
    「……」 whatever its number of dots (leaders_written, D4)."""
    return leaders_written(gap.translate(BULLET_SHAPES))


# The rule agreedMarks (C1): a block whose marks both engines read alike, at
# least this many places, is written with them and neither counted nor
# punctuated (agreed_marks).
AGREED_MARKS_MINIMUM = 2


def engine_reading(text: str, place: Mapping[int, int] | None, length: int
                   ) -> tuple[list[int | None], str, list[str], tuple[list[str], list[str]]] | None:
    """An engine's reading TEXT against the merged text's LENGTH characters,
    PLACE mapping its characters to theirs where the place is exact
    (engine_places, engine_b_places): for each merged character the index of
    the engine's character there (None where none is), the engine's
    characters and the text in each gap between them (engine_gaps), and its
    marks tied to the merged characters in the order it wrote them
    (marks_as_written).  None when PLACE is."""
    if place is None:
        return None
    own: list[int | None] = [None] * length
    for k, at in place.items():
        own[at] = k
    chars, gaps = engine_gaps(text)
    return own, chars, gaps, marks_as_written(text, place, length)


def other_print(gap: str) -> bool:
    """Whether an engine's gap holds print the mark vocabulary does not:
    figures, Latin text, a note marker (an engine's LaTeX around one too), a
    dash, 〔〕, a middle dot (HTML markup and spaces aside; vertical forms and
    ASCII brackets are marks, MARK_FORMS)."""
    gap = GAP_MARKUP.sub("", gap).translate(MARK_FORMS)
    return any(not c.isspace() and c not in PUNCT_MARKS for c in gap)


def agreed_marks(span: str, start: int, readings: Sequence[tuple[list[int | None], str, list[str],
                                                                 tuple[list[str], list[str]]] | None]) -> str | None:
    """SPAN, a block's stretch of the merged text from START, with the marks
    both engines printed there, when the rule agreedMarks takes it: in each
    of the two READINGS (engine_reading) SPAN is an exact stretch - every
    character the engine's own, the same character, in consecutive places -
    with no print beyond the marks in its gaps (other_print; at its two ends
    only the part on the stretch's side of a line break), and at each of
    SPAN's gaps the two engines wrote the same marks (gap_marks: the three
    bullets one shape, a run of leader dots one), at least
    AGREED_MARKS_MINIMUM places (mark_places).  Engine A's marks are written.
    None otherwise: the block is counted and punctuated, and the punctuator
    writes off the crop the print the merged text leaves out (it holds
    characters alone) - a year, a page number, a Western name, a note marker.

    The census, punctuate and count-retry calls were 81% of the GPU time of a
    modern book's phase 3.  Replayed offline on three finished books (this
    code, a made-up model; the cost from their seals): the rule takes 280 of
    the modern book's 4,401 passes (3.9% of its completion tokens: most of
    its blocks hold figures, note markers or Western text), 949 of 2,047 and
    1,256 of 2,613 passes of two older horizontally set books (30% and 32%);
    on the modern book's golden pages its 4 blocks are right at all 363
    gaps.  Without the condition on other print it took 3,139 of the modern
    book's blocks, and the sealed pages hold 5,854 characters of print in
    1,712 of them (figures, note markers, 〔〕, middle dots, Latin) that the
    engines' marks would have dropped.  A vertical page seldom qualifies:
    engine B read 66 marks in a whole vertically set book."""
    if len(readings) != 2 or not span:
        return None
    gaps = []
    for reading in readings:
        if reading is None:
            return None
        own, chars, written, tied = reading
        first = own[start] if start < len(own) else None
        if first is None or start + len(span) > len(own) or any(
                own[start + k] != first + k or chars[first + k] != char for k, char in enumerate(span)):
            return None
        ends = [TEXT_SEAMS.split(written[first])[-1], TEXT_SEAMS.split(written[first + len(span)])[0]]
        if any(other_print(gap) for gap in written[first + 1:first + len(span)] + ends):
            return None
        gaps.append(gaps_of(tied, start, len(span)))
    if any(gap_marks(x) != gap_marks(y) for x, y in zip(*gaps)):
        return None
    text = "".join(gap + char for gap, char in zip(gaps[0], span)) + gaps[0][-1]
    return text if mark_places(text) >= AGREED_MARKS_MINIMUM else None


def engine_b_places(chars: str, segments: Sequence[Mapping[str, Any]],
                    pieces: Sequence[str]) -> dict[int, int] | None:
    """Where each of engine B's characters (CHARS, as engine_gaps counts
    them) stands in the merged text ("".join(pieces)), where its place is
    exact: the merge wrote engine B's reading there, or one character for
    one.  Segments that carry their place in engine B's text (bStart: text
    engine B read in another order was placed at engine A's site,
    place_moved_once) are read by it.  None when the segments do not spell
    engine B's text."""
    if segments and all("bStart" in x for x in segments):
        b_at = [x["bStart"] for x in segments]
    else:
        b_at = list(itertools.accumulate((len(x["b"]) for x in segments), initial=0))
        if b_at[-1] != len(chars):
            return None
    place: dict[int, int] = {}
    m = 0
    for seg, piece, e in zip(segments, pieces, b_at):
        own = seg["b"]
        if chars[e:e + len(own)] != own:
            return None
        if own and (piece == own or len(piece) == len(own)):
            place.update((e + k, m + k) for k in range(len(own)))
        m += len(piece)
    return place


def engine_b_marks_merged(text: str, segments: Sequence[Mapping[str, Any]], pieces: Sequence[str],
                          placed: dict[int, str] | None = None,
                          written: bool = False) -> tuple[list[str], list[str]] | None:
    """The marks engine B printed, tied to the characters of the merged text
    as engine_marks_merged ties engine A's (WRITTEN as there): placed only
    where a character's place is exact (engine_b_places).  None when the
    segments do not spell engine B's text.  PLACED gets the merged characters
    placed, each with engine B's own character there.  For the text beside a
    table that only engine B read (table_edges): no punctuation pass reads a
    table block's text."""
    chars, marks = anchored_marks(text)
    place = engine_b_places(chars, segments, pieces)
    if place is None:
        return None
    if placed is not None:
        placed.update((at, chars[k]) for k, at in place.items())
    if written:
        return marks_as_written(text, place, sum(len(piece) for piece in pieces))
    return tied_marks(sum(len(piece) for piece in pieces), marks, place)


def engine_b_lines_merged(text: str, segments: Sequence[Mapping[str, Any]],
                          pieces: Sequence[str]) -> list[int] | None:
    """For each character of the merged text ("".join(pieces)), the line of
    engine B's reading TEXT it was read in - its lines and its table cells,
    split where TEXT_SEAMS splits (a line break, a cell's markup), numbered
    in engine B's order - where its place is exact (engine_b_places), and
    -1 elsewhere.  None when the segments do not spell engine B's text.
    For the text beside a table (edge_verdicts): what engine B read on one
    line is judged as one."""
    chars, gaps = engine_gaps(text)
    place = engine_b_places("".join(chars), segments, pieces)
    if place is None:
        return None
    line, number = [], 0
    for k in range(len(chars)):
        if k and TEXT_SEAMS.search(gaps[k]):
            number += 1
        line.append(number)
    out = [-1] * sum(len(piece) for piece in pieces)
    for k, at in place.items():
        out[at] = line[k]
    return out


def marked_stretch(merged: str, lo: int, hi: int,
                   engines: Sequence[tuple[tuple[list[str], list[str]] | None, Mapping[int, str]]]) -> str:
    """MERGED[LO:HI] with its marks: in each gap, the marks of the first of
    ENGINES (each an engine's marks tied to the merged text, and the
    characters it placed; engine_marks_merged, engine_b_marks_merged) that
    read a character beside the gap exactly, as read.  A mark tied to a
    character outside the stretch is not the stretch's (gaps_of)."""
    length = hi - lo
    shown = [(gaps_of(tied, lo, length), placed) for tied, placed in engines if tied is not None]
    out = []
    for g in range(length + 1):
        for gaps, placed in shown:
            if (g > 0 and lo + g - 1 in placed) or (g < length and lo + g in placed):
                out.append(gaps[g])
                break
        if g < length:
            out.append(merged[lo + g])
    return "".join(out)


def agreed_punctuation(span: str, refused: Sequence[str], printed: Sequence[str],
                       doubled: bool) -> tuple[str, int]:
    """SPAN with the marks a refused punctuation answer and engine A both put
    at the same gap, and how many.

    A refused answer is refused for its characters (a character dropped or
    changed), its count or its density, not for its marks, and engine A's
    own marks are an independent reading of the same print: measured on one
    magazine page, engine A wrote 44 of the 45 printed marks and none wrong,
    while both punctuation answers on six of its blocks were refused (they
    dropped engine B's look-alike characters for the marks they are) and the
    page kept 11.  Offline, keeping the marks the refused answers and engine
    A agree on took it to 39 of 45 with 40 written.  PRINTED holds engine A's
    marks at each of SPAN's gaps (engine_marks_merged, gaps_of).  Each
    answer's marks are placed on SPAN by aligning its characters, and at each
    gap a mark is kept as often as both wrote it; the answer that agrees more
    is taken gap by gap, never the two added together.  A circle is kept only
    as the same run: 。 against 。 or 。。 against 。。.  Where the book prints
    doubled circles (DOUBLED) engine A's single 。 says a circle is there but
    not whether one or two - engine A wrote 17 。。 in two such books, and on
    one book's golden pages 55 of its 470 marks were right - so it agrees
    with nothing.
    """
    best: list[str] = [""] * (len(span) + 1)
    for answer in refused:
        chars, marks = anchored_marks(answer, FALLBACK_MARKS)
        matcher = difflib.SequenceMatcher(None, chars, span, autojunk=False)
        place = {block.a + k: block.b + k for block in matcher.get_matching_blocks()
                 for k in range(block.size)}
        for g, (ours, theirs) in enumerate(zip(gaps_of(tied_marks(len(span), marks, place)), printed)):
            wanted = Counter(ours) & Counter(theirs)
            circles = theirs.count("。")
            wanted["。"] = circles if (ours.count("。") == circles and not (doubled and circles == 1)) else 0
            kept, left = [], +wanted
            for mark in theirs:
                if left[mark] > 0:
                    kept.append(mark)
                    left[mark] -= 1
            if len(kept) > len(best[g]):
                best[g] = "".join(kept)
    text = "".join(best[g] + span[g] for g in range(len(span))) + best[len(span)]
    return text, sum(map(len, best))


def realign_answer(block_span: str, lo: int, hi: int, answer: str, reach: int) -> tuple[int, int, int] | None:
    """Where in its block a punctuation answer for the stretch [LO, HI) of
    BLOCK_SPAN reads, when its characters are not that stretch's: (a, b, how
    many of its characters are another form of the block's), for the stretch
    [a, b) whose characters, variant-folded (variant_fold), are exactly the
    answer's, with a and b each within REACH of LO and HI - the nearest such.
    None when there is none: an answer that drops, adds or changes a
    character inside the stretch is no reading of any stretch.

    A column group's text is scaled from its columns' measured lengths
    (block_tiles), and a bracket takes a whole cell in vertical print, so a
    tile's text drifts off its crop by a few characters at either end.  The
    punctuator writes the text its crop shows.  Measured on one page, in six
    runs of two code versions: tile 0's answer left out the four characters
    printed at the head of tile 1's crop, tile 1's added them and left out
    its last one, printed on tile 2's crop; both were refused as "characters
    changed" and the stretches kept no circle (recall 41-51%).  Realigned and
    their marks seated on the block's characters, offline: 77-85%.  REACH 0
    folds variants alone (an answer that wrote another form of a character
    the variant lists rule one with the merge's)."""
    got = variant_fold(CJK(answer))
    if not got:
        return None
    folded = variant_fold(block_span)
    found = [(abs(a - lo) + abs(a + len(got) - hi), a) for a in range(max(0, lo - reach), lo + reach + 1)
             if a + len(got) <= len(block_span) and abs(a + len(got) - hi) <= reach
             and folded[a:a + len(got)] == got]
    if not found:
        return None
    a = min(found)[1]
    b = a + len(got)
    return a, b, sum(1 for x, y in zip(CJK(answer), block_span[a:b]) if x != y)


def seat_marks(answer: str, stretch: str) -> str:
    """ANSWER's marks on STRETCH's characters, by position: the answer's k-th
    character stands for STRETCH's k-th (realign_answer found them the same
    but for variant forms).  The characters written are always STRETCH's."""
    chars, marks = anchored_marks(answer)
    after, before = tied_marks(len(stretch), marks, {k: k for k in range(min(len(chars), len(stretch)))})
    return "".join(before[k] + stretch[k] + after[k] for k in range(len(stretch)))


def assemble_passes(span: str, parts: Sequence[tuple[str, int, int, int, int]]) -> tuple[str, list[tuple[int, int]]]:
    """A block's punctuated text from its passes, each (text, a, b, lo, hi): the
    text of SPAN's characters [a, b) it read - its own stretch [lo, hi), or
    another where its answer was realigned (realign_answer).  The passes'
    texts joined when every pass read its own stretch; otherwise every mark on
    SPAN's characters by position, from the pass that read the character - a
    character two passes read is the one's that read it outside its own
    stretch, which it can only have seen on its crop.  Also the stretches of
    SPAN no pass read, which keep no mark."""
    if all((a, b) == (lo, hi) for _, a, b, lo, hi in parts):
        return "".join(text for text, *_ in parts), []
    after, before = [""] * len(span), [""] * len(span)
    reader: list[bool | None] = [None] * len(span)
    for text, a, b, lo, hi in parts:
        chars, marks = anchored_marks(text)
        own_after, own_before = tied_marks(b - a, marks, {k: k for k in range(min(len(chars), b - a))})
        for k in range(b - a):
            outside = not (lo <= a + k < hi)
            if reader[a + k] is None or (outside and not reader[a + k]):
                reader[a + k] = outside
                after[a + k], before[a + k] = own_after[k], own_before[k]
    unread = [(k, k + 1) for k, who in enumerate(reader) if who is None]
    merged: list[tuple[int, int]] = []
    for lo, hi in unread:
        if merged and merged[-1][1] == lo:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return "".join(before[k] + span[k] + after[k] for k in range(len(span))), merged


def agreed_readings(block_span: str, first: tuple[str, Sequence[int]], second: tuple[str, Sequence[int]]
                    ) -> tuple[str, int, list[int]] | None:
    """The marks two punctuation answers put at the same place, each (text on
    BLOCK_SPAN's characters, the stretch [a, b) it read): the text of the
    stretch both read, with those marks, how many, and the stretch.  A circle
    only as the same run (。 against 。, 。。 against 。。), as
    agreed_punctuation holds them.  None when the stretches do not meet."""
    (text_1, (a_1, b_1)), (text_2, (a_2, b_2)) = first, second
    a, b = max(a_1, a_2), min(b_1, b_2)
    if a >= b:
        return None

    def tied(text: str, length: int) -> tuple[list[str], list[str]]:
        chars, marks = anchored_marks(text)
        return tied_marks(length, marks, {k: k for k in range(min(len(chars), length))})

    def both(x: str, y: str) -> str:
        wanted = Counter(x) & Counter(y)
        if x.count("。") != y.count("。"):
            wanted["。"] = 0
        kept = []
        for mark in x:
            if wanted[mark] > 0:
                kept.append(mark)
                wanted[mark] -= 1
        return "".join(kept)

    after_1, before_1 = tied(text_1, b_1 - a_1)
    after_2, before_2 = tied(text_2, b_2 - a_2)
    out, kept = [], 0
    for k in range(a, b):
        ahead, behind = both(before_1[k - a_1], before_2[k - a_2]), both(after_1[k - a_1], after_2[k - a_2])
        out.append(ahead + block_span[k] + behind)
        kept += len(ahead) + len(behind)
    return "".join(out), kept, [a, b]


def punctuate_part(client: "Client", arguments: Any, crop: bytes | None, span: str, hint: str,
                   limit: float, printed: Sequence[str] | None = None,
                   block: tuple[str, int, int, int] | None = None
                   ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Census then punctuation for one block or column group, against its crop.

    The census reports the places a mark is printed and, apart, how many of
    them are doubled circles (CENSUS_SYSTEM); the punctuator is told both
    (census_hint), and an answer is held to the places, so a pair written 。。
    is never refused as one mark too many.  Measured on one doubled-circle
    book's golden stretches over eight runs: where the census had counted the
    pairs as two marks the first answers wrote about all of them as 。。 (787
    of 700 golden), where it counted a pair as one they wrote 67 of 2,202, and
    the count gate refused right answers (44 marks written, 22 counted - 21
    doubled circles and a circle).  When the answer's doubled circles stray
    from the count by more than the tolerance, one retry names both numbers,
    and whichever answer is nearer the count is kept.

    Runs of printed dots - leaders, long ellipses - are counted off the crop
    with no model (leader_count, decision D4): the punctuator is told how many
    to write as 「……」, and the places an answer is held to are never fewer
    (a census of 0 on a table of contents printing 7 leaders skipped the pass).

    BLOCK is (the block's text, where SPAN is in it [lo, hi), a column's
    worth of characters): an answer refused for its characters that reads a
    stretch of the block within that reach of SPAN's ends, but for variant
    forms (realign_answer), is that stretch's reading - the tile's text
    drifted off its crop - and is kept, its marks seated on the block's own
    characters (seat_marks; the merged characters are written, never the
    answer's), and the pass records it (realigned: read, range, variants
    folded) and returns the stretch's text.  Reach 0: variant forms alone.
    A retry is not trusted so far: a retry is told the census, and measured
    on the drifted tiles of one page (four runs) the retries that read their
    stretch padded their marks toward the count (22 places to 31, with 7 of
    them wrong), where the first answers had 1-3 wrong.  A retry that needs
    realigning keeps only the marks it and the first answer (realigned, or
    on the tile's own characters) put at one place (agreed_readings): the
    pass stays refused and partial.  Offline on those tiles: 13-21 marks
    kept per tile with 0-1 wrong, against 16-24 with 2-10 wrong for the
    retry alone.

    Notes are counted too.  Measured on 30 scans of a horizontally set book
    with many footnotes, run twice each way: with no census on its footnotes
    and bibliography entries, the differing marks there were right on the
    image at 10 and 9 places against 21 and 24 with it - the count refusal's
    retry is what writes back a Western citation, a dash or a note marker
    the first answer left out.
    """
    used: list[dict[str, Any]] = []

    def judge(text: str) -> tuple[bool, str, str, dict[str, Any] | None]:
        """punctuation_pass_ok on TEXT, or on its realigned reading (BLOCK):
        (ok, reason, the text kept, the realignment)."""
        ok, reason = punctuation_pass_ok(span, text, limit, census)
        if ok or block is None or not reason.startswith("characters"):
            return ok, reason, text, None
        whole, lo, hi, reach = block
        found = realign_answer(whole, lo, hi, text, reach)
        if not found:
            return ok, reason, text, None
        a, b, folded = found
        seated = seat_marks(text, whole[a:b])
        if not punctuation_pass_ok(whole[a:b], seated, limit, census)[0]:
            return ok, reason, text, None
        return True, "", seated, {"read": [a, b], "range": [lo, hi], "variantsFolded": folded}

    punct_system = PUNCT_SYSTEM
    leaders = leader_count(crop)
    show = doubled_shown(arguments)
    # Census first: how many marks are PRINTED here. A count read from the
    # image gates the pass (zero printed -> nothing to add) and checks its
    # output; measured without it, an unpunctuated 1910 report came back with
    # 。and ，throughout.
    ceiling = census_ceiling(arguments, len(CJK(span)))
    fused = getattr(client, "supports_fused_punctuate", False)
    fused_out = ""
    if fused:
        census_text, fused_out, m = client.fused_census_and_punctuate(
            crop_bytes=crop,
            span=span,
            hint=hint,
            leaders=leaders,
            show_doubled=show,
            max_tokens=arguments.max_tokens,
            census_system=CENSUS_SYSTEM,
            punct_system=punct_system,
        )
        used.append(m)
    else:
        census_text, m = client.ask_answering(
            CENSUS_SYSTEM, "數清楚呢個 block 印咗幾多個標點符號。", crop, arguments.max_tokens, kind="census")
        used.append(m)
    census = parse_census(census_text)
    doubled_census = parse_census(census_text, DOUBLED_LINE)
    # The shapes the census saw, as it wrote them: evidence for a bullet's
    # shape (bullet_contradicted).
    shape_line = SHAPE_LINE.search(census_text or "")
    if census is not None and census + (doubled_census or 0) > ceiling:
        # More marks than this book ever prints per character is not a count of
        # marks (measured: 178 for a 430-character block - it counted
        # characters). Unknown, not zero.
        census = doubled_census = None
    counted = {"censusConvention": "places", "doubledCensus": doubled_census}
    if shape_line and any(c in PUNCT_MARKS or c in BULLET_MARKS for c in shape_line.group(1)):
        counted["censusShapes"] = shape_line.group(1).strip()[:40]
    if leaders is not None:
        counted["leaderCensus"] = leaders
    if leaders and census is not None and census < leaders:
        # The dot runs on the crop are printed marks the model's count left
        # out: the census held to is at least that many.
        counted["modelCensus"], census = census, leaders
    if census == 0:
        return span, {"ok": True, "marks": 0, "places": 0, "census": 0, "reason": "no printed marks", **counted}, used
    told = census_hint(census, doubled_census, show) + (
        f"裁圖上量到 {leaders} 串點（導引點線或者刪節號），每串寫「……」。\n" if leaders else "")
    if fused:
        out = fused_out
    else:
        ask = (f"已定嘅字（唔准改）：\n---\n{span}\n---\n" + told + hint +
               "睇裁圖，逐欄加返頁上印咗嘅標點；唔准加、刪、改任何漢字。")
        out, m = client.ask_answering(punct_system, ask, crop, arguments.max_tokens, kind="punctuate")
        used.append(m)
    out = strip_fence(out).strip()
    first = {"firstAnswerMarks": sum(punctuation_counts(out).values()), "firstAnswerDoubled": out.count("。。")}
    answered = out
    ok_p, reason_p, out, realigned = judge(out)
    # The first answer on the block's characters and the stretch it read,
    # whatever its count: what a realigned retry is held to.
    first_read = ((out, realigned["read"] if realigned else [block[1], block[2]])
                  if ok_p and block is not None else None)
    agreement = None
    if ok_p and census is not None:
        places = mark_places(out)
        if abs(places - census) > census_tolerance(census):
            reason_p = f"{places} places marked but {census} counted on the page"
            ok_p = False
    # Kept when refused: a refused pass leaves its text bare, and without the
    # output nobody can tell a wrong refusal from a right one afterwards.
    refused = [] if ok_p else [answered]
    if not ok_p:
        realigned = None
        # The retry is told the census too (it was not, and a retry after a
        # count refusal then wrote to no number at all).
        retry, m = client.ask_answering(
            punct_system,
            f"已定嘅字（唔准改）：\n---\n{span}\n---\n"
            f"你上一次嘅輸出被拒絕，原因：{reason_p}。" + told + hint +
            "淨係加圖上印咗嘅標點，字要同上面一模一樣、次序都唔准變。重新輸出。",
            crop, arguments.max_tokens, kind="punctuate-retry")
        used.append(m)
        retry = strip_fence(retry).strip()
        ok_r, _, kept_retry, realigned_retry = judge(retry)
        if ok_r and realigned_retry is None:
            out, ok_p, reason_p, realigned = kept_retry, True, reason_p + " (after retry)", realigned_retry
        else:
            refused.append(retry)
            if ok_r and first_read is not None:
                # A retry that reads another stretch than its tile's: only
                # what it and the first answer agree on (agreed_readings).
                agreement = agreed_readings(block[0], first_read, (kept_retry, realigned_retry["read"]))
    record = {"ok": ok_p, "census": census, "reason": reason_p, **counted, **first}
    if ok_p and refused:
        # The first answer, refused, when the retry was kept: sealed too, so
        # a retry far off the census can be audited against it.
        record["firstAnswer"] = refused[0][:2000]
    if ok_p and show and doubled_census is not None and (
            abs(out.count("。。") - doubled_census) > census_tolerance(doubled_census)):
        # The shape: as many doubled circles as the census found, within the
        # tolerance the places are held to.  One retry that names both
        # numbers; the answer nearer the count stands, and only one that keeps
        # the characters and the places.
        again, m = client.ask_answering(
            punct_system,
            f"已定嘅字（唔准改）：\n---\n{span}\n---\n" + told +
            f"你上一次寫咗 {out.count('。。')} 處雙圈「。。」，但數過嘅係 {doubled_census} 處"
            f"（{census} 處符號入面）。返去裁圖逐個符號睇清楚：係一個圈，定係兩個圈連住。" + hint +
            "淨係加圖上印咗嘅標點，字要同上面一模一樣、次序都唔准變。重新輸出。",
            crop, arguments.max_tokens, kind="punctuate-shape")
        used.append(m)
        again = strip_fence(again).strip()
        good, _, kept_again, realigned_again = judge(again)
        good = good and (census is None or abs(mark_places(kept_again) - census)
                         <= census_tolerance(census))
        nearer = abs(again.count("。。") - doubled_census) < abs(out.count("。。") - doubled_census)
        record["shapeRetry"] = {"doubled": again.count("。。"), "marks": sum(punctuation_counts(again).values()),
                                "kept": bool(good and nearer)}
        if good and nearer:
            out, realigned = kept_again, realigned_again
    if not ok_p:
        out = span
        record["refusedOutputs"] = [text[:2000] for text in refused]
        if agreement is not None and agreement[1]:
            # Both answers read their stretches (realign_answer): the marks
            # they agree on, on the block's characters.  Still a refused pass.
            out, kept, read = agreement
            record["partial"] = {"kept": kept, "agreedWith": "both answers"}
            record["realigned"] = {"read": read, "range": [block[1], block[2]], "variantsFolded": None}
        elif printed is not None and len(printed) == len(span) + 1 and CJK(span) == span:
            # Both answers refused: keep what a refused answer and engine A's
            # own marks agree on (agreed_punctuation), never the bare text
            # alone when two readings put a mark there.  Still a refused pass:
            # the seal says partial and the page is not clean.  Doubled
            # circles: this book's profile, the census on this crop, or the
            # answers themselves.
            doubled = bool(profile_section(arguments, "notation").get("doubled")) or any(
                "。。" in text for text in refused) or bool(doubled_census)
            partial, kept = agreed_punctuation(span, refused, printed, doubled)
            if kept and punctuation_pass_ok(span, partial, limit)[0]:
                out = partial
                record["partial"] = {"kept": kept, "engineA": sum(map(len, printed)),
                                     "doubledCircles": doubled}
    if ok_p and realigned:
        record["realigned"] = realigned
    record["marks"] = sum(punctuation_counts(out).values())
    # How many of the marks are doubled circles: a census that counts each as
    # one mark sees half of them (measured on a sealed page: census 23 where
    # 38 marks were kept, 15 doubled circles among them - 23 counting each as
    # one), so the reader of a count mismatch needs the number.
    record["doubled"] = out.count("。。")
    # And the places they hold as the places census counts them (mark_places:
    # a doubled circle one, a bullet printed twice one), which the pass was
    # held to: what the seal holds the kept stretch to (punctuation_doubts).
    record["places"] = mark_places(out)
    # The leaders and bullets kept (decisions D4, D5), for decisionChanges.
    record["leaders"] = punctuation_counts(out)[LEADER]
    record["bullets"] = sum(out.count(bullet) for bullet in BULLET_MARKS)
    return out, record, used


def leader_runs(text: str) -> list[tuple[int, int]]:
    """Where TEXT holds runs of leader dots, as (start, end) in TEXT (D4)."""
    return [(m.start(), m.end()) for m in re.finditer(f"[{LEADER_MARKS}]+", text)]


def leaders_written(text: str) -> str:
    """TEXT with each run of leader dots written 「……」 (D4: whatever the number
    of dots printed or written)."""
    runs = leader_runs(text)
    for start, end in reversed(runs):
        text = text[:start] + LEADER + text[end:]
    return text


def leader_rewrites(text: str) -> tuple[str, list[dict[str, Any]]]:
    """A page's text with each run of leader dots written 「……」 (D4), and a
    decisionChanges entry for each run that was not: one a structure pass
    wrote with another number of dots than its block pass (the lock counts a
    run as one mark) or a recovered table's cell holds.  Page offsets, since
    the formatter drops furniture.  The run as written is what the page held."""
    chars = CJK(text)
    out: list[dict[str, Any]] = []
    for start, end in leader_runs(text):
        if text[start:end] == LEADER:
            continue
        at = len(CJK(text[:start]))
        out.append({
            "decision": "D4", "rule": "dot-leader-rewritten", "block": None,
            "contextBefore": chars[max(0, at - 4):at], "contextAfter": chars[at:at + 4],
            "mergedOffset": None, "pageOffset": at,
            "after": LEADER, "evidence": {"written": text[start:end]},
        })
    return leaders_written(text), out


# A block of engine A's layout that lists entries, each with a leader and
# its page number (a contents page): two or more of its lines hold a leader
# (LEADER_READ).  Its block pass reads the block's characters as one run, so
# the structure pass was given it as one line, and where that pass was
# refused (one paragraph per block) a whole contents page was sealed as one
# line: measured on a contents page of 36 lines and on one of 18 names, the
# second sealed by an accepted structure pass that kept the line it was given.
LIST_LEADER_LINES = 2


def leader_list_breaks(text: str) -> list[int]:
    """Where engine A broke the lines of TEXT, its reading of one layout
    block, when the block lists entries with leaders (two or more of its
    lines hold one, LIST_LEADER_LINES): each gap of its characters
    (engine_gaps) a line break is in.  Empty for any other block: a
    paragraph's lines are where the print wrapped, not entries."""
    if sum(1 for line in text.split("\n") if LEADER_READ.search(line)) < LIST_LEADER_LINES:
        return []
    chars, gaps = engine_gaps(text)
    return [g for g in range(1, len(chars)) if "\n" in gaps[g]]


def break_lines(text: str, span: str, at: Collection[int]) -> str:
    """TEXT, a punctuated block whose characters are SPAN, with a line break
    in each gap of AT (gap k is before character k): after the marks there
    that end the line (a leader, a page number and its brackets), before
    those that open the next (OPENING_MARKS: the bracket of a list's
    「（一）」).  TEXT as it is where its characters are not SPAN."""
    chars, gaps = engine_gaps(text)
    if chars != CJK(span):
        return text
    for g in at:
        if 0 < g < len(chars) and "\n" not in gaps[g]:
            gap = gaps[g]
            cut = len(gap)
            while cut and gap[cut - 1] in OPENING_MARKS:
                cut -= 1
            gaps[g] = gap[:cut].rstrip() + "\n" + gap[cut:].lstrip()
    out = [gaps[0]]
    for char, gap in zip(chars, gaps[1:]):
        out += [char, gap]
    return "".join(out)


def mark_changes(text: str, base: int, block: int | None, passes: Sequence[Mapping[str, Any]],
                 arguments: Any) -> tuple[str, list[dict[str, Any]]]:
    """TEXT with each run of leader dots written 「……」 (leaders_written), and
    decisionChanges entries for each bullet (D5) and each run of dots (D4) it
    keeps: a block's punctuated text (BASE its offset in the merged text,
    BLOCK its layout index, PASSES its punctuation passes, for the counts
    behind it) or a page the formatter punctuated whole (BASE 0, no block:
    pageOffset, not mergedOffset, since the formatter drops furniture).

    A punctuator that writes a leader as three dots, or one, had its answer
    kept as written while the entry said 「……」; now the page is what the
    entry says, and the run as written is in the entry's evidence.
    """
    chars = CJK(text)
    runs = dict(leader_runs(text))
    # A bullet printed twice (⦿⦿) marks one item: one entry for the run.
    bullets = dict(bullet_runs(text))
    out: list[dict[str, Any]] = []
    written: list[str] = []
    at, index = 0, 0
    while index < len(text):
        c = text[index]
        if CJK(c):
            at += 1
        leader = index in runs
        if leader or index in bullets:
            own = next((p for p in passes if p.get("range") and p["range"][0] <= at <= p["range"][1]), None)
            shapes = own.get("censusShapes") if own else None
            out.append({
                "decision": "D4" if leader else "D5", "rule": "dot-leader" if leader else "bullet",
                "block": block, "contextBefore": chars[max(0, at - 4):at], "contextAfter": chars[at:at + 4],
                **({"mergedOffset": base + at} if block is not None else {"mergedOffset": None, "pageOffset": at}),
                "after": LEADER if leader else bullets[index],
                "evidence": {"census": own.get("census") if own else None,
                             "leaderCensus": own.get("leaderCensus") if own else None,
                             **({"written": text[index:runs[index]]} if leader else {}),
                             # What the census saw there, and whether it names
                             # another bullet (bullet_contradicted).
                             **({"censusShapes": shapes,
                                 "shapeContradicted": bullet_contradicted(bullets[index], shapes)}
                                if not leader and shapes else {})},
            })
        if leader:
            written.append(LEADER)
            index = runs[index]
            continue
        if index in bullets:
            written.append(bullets[index])
            index += len(bullets[index])
            continue
        written.append(c)
        index += 1
    return "".join(written), out


def punctuation_pass_ok(span: str, out: str, limit: float | None = None,
                        census: int | None = None) -> tuple[bool, str]:
    """Exact character sequence, marks within the degenerate bound.

    A count read off the image outranks the density bound: when the marks
    written match the census - as places, a doubled circle one
    (mark_places) - a dense block is the page, not a runaway.
    """
    if CJK(out) != CJK(span):
        if Counter(CJK(out)) == Counter(CJK(span)):
            return False, "characters reordered"
        return False, "characters changed"
    if census is not None and abs(mark_places(out) - census) <= census_tolerance(census):
        return True, ""
    density = punctuation_density(out)
    if density > (DEGENERATE_PUNCTUATION_DENSITY if limit is None else limit):
        return False, f"over-punctuated ({density:.1f} marks per 100 characters)"
    return True, ""


def place_by_alignment(a_text: str, piece: str, a_start: int,
                       ends: Sequence[int]) -> list[tuple[int, str]] | None:
    """PIECE split into (block index, text) runs by aligning it to engine A's
    A_TEXT, which starts at A_START in A's stream; ENDS are the blocks' ends.

    A character of the piece that matches one of A's, or replaces one, goes to
    the block A read that character in (a replacement of a different length is
    spread over the characters it replaces); a character with nothing of A's
    under it goes to the block of the A character that follows it, as
    block_spans places an insertion at a boundary, or the last one at the end.
    An empty piece (the disagreement resolved to nothing) places nothing; None
    when the piece shares no character with A.
    """
    if not piece:
        return []
    def block_of(position: int) -> int:
        return next((i for i, end in enumerate(ends) if position < end), len(ends) - 1)
    matcher = difflib.SequenceMatcher(None, a_text, piece, autojunk=False)
    if not any(n for _, _, n in matcher.get_matching_blocks()):
        return None
    owner: list[int] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            continue
        if tag == "insert":
            at = i1 if i1 < len(a_text) else len(a_text) - 1
            owner += [block_of(a_start + at)] * (j2 - j1)
            continue
        span = i2 - i1
        owner += [block_of(a_start + i1 + (t * span) // (j2 - j1)) for t in range(j2 - j1)]
    runs: list[list] = []
    for char, block in zip(piece, owner):
        if runs and runs[-1][0] == block:
            runs[-1][1] += char
        else:
            runs.append([block, char])
    return [(block, text) for block, text in runs]


def block_spans(blocks: Sequence[Mapping[str, Any]], segments: Sequence[Mapping[str, Any]],
                pieces: Sequence[str], draft: str) -> list[str] | None:
    """Split the merged text into the engine-A layout blocks it came from.

    The merge walks engine A's character stream; block boundaries are known
    in that stream, so every piece of the merged text can be handed to the
    block it belongs to.  Insertions (engine B only) at a boundary go to the
    following block, where a list marker's stroke debris belongs.  Returns
    None when the blocks do not account for the whole draft, and the page
    falls back to whole-page formatting.
    """
    lengths = [len(CJK(b["text"])) for b in blocks]
    if not blocks or sum(lengths) != len(CJK(draft)):
        return None
    ends = list(itertools.accumulate(lengths))
    spans = [[] for _ in blocks]
    a_cursor, k = 0, 0
    for seg, piece in zip(segments, pieces):
        a_len = len(seg["a"])
        if seg.get(LOOPED_READING):
            # Engine B's reading of a block whose own reading was a loop
            # (quarantine_looped_block) belongs to that block, wherever it sits.
            quarantined = [i for i, block in enumerate(blocks) if block.get("quarantined")]
            if len(quarantined) != 1:
                return None
            spans[quarantined[0]].append(piece)
            a_cursor += a_len
            continue
        if "window" in seg:
            # A segment of an order-conflict table block read from engine B's
            # lines (window_merge) belongs to that block.  Its segments hold
            # each of the block's characters once, but in engine B's order, so
            # text only engine B read at the end of its last window stands
            # where engine A's stream has already passed the block, and would
            # otherwise go to the next block.
            spans[seg["window"]].append(piece)
            a_cursor += a_len
            continue
        if a_len and piece == seg["a"]:
            # Engine A's own text (agreed, or chosen where the engines
            # disagree) may straddle blocks: split at the boundaries it
            # crosses.  A disagreement is not assigned whole to the block it
            # starts in: when the other engine reads a multi-leaf page in a
            # different order, one disagreement can run across several blocks,
            # and a running-head block was measured absorbing 115 characters of
            # body text that were then dropped with it.
            offset = 0
            while offset < a_len:
                while k < len(blocks) and a_cursor + offset >= ends[k]:
                    k += 1
                idx = min(k, len(blocks) - 1)
                take = min(a_len - offset, ends[idx] - (a_cursor + offset)) if k < len(blocks) else a_len - offset
                spans[idx].append(piece[offset:offset + take])
                offset += max(take, 1)
        else:
            while k < len(blocks) and a_cursor >= ends[k] and (a_len or k < len(blocks) - 1):
                k += 1
            first = min(k, len(blocks) - 1)
            last = next((i for i, end in enumerate(ends) if a_cursor + a_len <= end), len(blocks) - 1)
            if a_len and last > first:
                # A resolved disagreement over several of engine A's blocks.
                # Where the resolution keeps engine A's characters (裁決 kept a
                # running head and dropped the folio A read after it), each
                # kept character goes back to the block A read it in.  Text
                # sharing nothing with A cannot be placed block by block, so the
                # page is punctuated whole rather than risk text landing in the
                # wrong block (a furniture block, a table).  Measured: one such
                # head-and-folio 裁決 sent a page to whole-page punctuation, which
                # invented ，：「」 on a book printed with 。 only.
                placed = place_by_alignment(seg["a"], piece, a_cursor, ends)
                if placed is None:
                    return None
                for idx, text in placed:
                    spans[idx].append(text)
                a_cursor += a_len
                continue
            spans[first].append(piece)
        a_cursor += a_len
    joined = ["".join(x) for x in spans]
    if "".join(joined) != "".join(pieces):
        return None
    return joined


def align_drafts(a: str, b: str, folded: bool | None = None, prefer: str | None = None) -> list[dict[str, Any]]:
    """Character-level alignment of two independent OCR readings.

    Agreement between two engines from different vendors is the strongest
    character evidence available - measured 11/11 on this project's adjudicated
    items, against 8/11 and 10/11 for the engines alone and 4-7/11 for a model
    re-transcribing the page. So agreement is frozen and only differences are
    put to a model.

    The readings are matched on variant-folded text (variant_fold; FOLDED
    False matches them as written, as the book profile does), so two forms of one character
    never move an anchor: measured, engine B wrote a heading's second
    character as 决 where engine A wrote 決, difflib anchored the heading's
    first character on an earlier copy of it instead, and a line only engine
    B read (engine A skipped it) came out fused with that one character as a
    replacement of 1 against 18, which the preference then settled with
    engine A's single character - 17 printed characters lost with no doubt
    (split_lopsided is the other half of the fix).  The characters in the
    segments stay the engines' own: where the folded match pairs two
    different forms, those positions are a replacement of their own (a
    variant question for variant_segment), the rest of the match is
    agreement.

    Not, on a page the engine preference PREFER ("A" or "B") settles, where
    the fold takes apart a lopsided replacement - one reading more than
    INSERTION_LIMIT characters longer than the other - whose longer reading
    is the other engine's, into text only that engine read (more than
    INSERTION_LIMIT characters) which is no line the preferred engine
    skipped: the preferred engine read it elsewhere on the page, or it loops
    (read_elsewhere, the test split_lopsided_segments applies).  That
    stretch keeps the replacement the characters as written make, and the
    preference settles it as before.  Only there: where the fold matches
    such a stretch with the preferred engine's reading instead, the
    alignment as written had anchored on another copy of a sentence, and
    the fold is what puts it right - measured on the corpus, a page whose
    engines read in order 0.88 folded and 0.53 as written (a sentence
    written with 真 and its later copy with 眞); kept as written wherever
    the fold took a lopsided replacement apart, a made-up page like it
    (check_text_loss), preferring engine A, read as an order conflict
    again.  Measured: engine A read a line of signatures twice, its
    second copy fused with a variant form beside it (暨 against engine B's
    曁); aligned folded, the copy became text only engine A read, which a
    preference for engine B keeps (the preference falls back to the side
    that has text), and 11 characters were written twice with no doubt -
    the copy all held by engine B's reading (moved_share 1.00).  Where the
    longer reading is the preferred engine's, the preference writes it
    either way; on a page 裁決 reads every disagreement off its crop, it
    sees both questions the fold makes (D16 keeps a longer reading the
    answer leaves out, unless the merged text holds it nearby).
    """
    ca, cb = CJK(a), CJK(b)
    fold = True if folded is None else folded
    fa, fb = (variant_fold(ca), variant_fold(cb)) if fold else (ca, cb)
    out = []

    def emit(tag: str, i1: int, i2: int, j1: int, j2: int) -> None:
        out.append({"tag": tag, "a": ca[i1:i2], "b": cb[j1:j2],
                    "before": ca[max(0, i1 - 8):i1], "after": ca[i2:i2 + 8]})

    def opcodes(x: str, y: str, i0: int = 0, j0: int = 0) -> list[tuple[str, int, int, int, int]]:
        return [(tag, i1 + i0, i2 + i0, j1 + j0, j2 + j0) for tag, i1, i2, j1, j2
                in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes()]

    def lopsided(op: tuple[str, int, int, int, int]) -> bool:
        tag, i1, i2, j1, j2 = op
        return tag == "replace" and i2 > i1 and j2 > j1 and abs((i2 - i1) - (j2 - j1)) > INSERTION_LIMIT

    ops = opcodes(fa, fb)
    if prefer in ("A", "B") and (fa != ca or fb != cb):
        # Text only the other engine read, as the fold aligns it, that it read
        # elsewhere (or that loops): where it lies inside a lopsided
        # replacement of the characters as written that the fold takes apart,
        # with that engine's reading the longer, the replacement stands.
        still = {(ca[i1:i2], cb[j1:j2]) for _, i1, i2, j1, j2 in filter(lopsided, ops)}
        other_side = "b" if prefer == "A" else "a"
        mine, theirs = (cb, ca) if other_side == "b" else (ca, cb)
        page_looped = looped_reading(mine)
        alone = [(i1, i2) if other_side == "a" else (j1, j2) for tag, i1, i2, j1, j2 in ops
                 if tag == ("delete" if other_side == "a" else "insert")
                 and (i2 - i1 if other_side == "a" else j2 - j1) > INSERTION_LIMIT]
        alone = [(lo, hi) for lo, hi in alone if read_elsewhere(mine[lo:hi], theirs, page_looped)]
        keep = []
        for op in (filter(lopsided, opcodes(ca, cb)) if alone else ()):
            _, i1, i2, j1, j2 = op
            lo, hi = (i1, i2) if other_side == "a" else (j1, j2)
            if ((ca[i1:i2], cb[j1:j2]) not in still and (i2 - i1 > j2 - j1) == (other_side == "a")
                    and any(a_lo < hi and lo < a_hi for a_lo, a_hi in alone)):
                keep.append(op)
        if keep:
            # Folded between them, as written across them.
            hybrid, i0, j0 = [], 0, 0
            for op in keep:
                _, i1, i2, j1, j2 = op
                hybrid.extend(opcodes(fa[i0:i1], fb[j0:j1], i0, j0))
                hybrid.append(op)
                i0, j0 = i2, j2
            hybrid.extend(opcodes(fa[i0:], fb[j0:], i0, j0))
            ops = [op for op in hybrid if op[2] > op[1] or op[4] > op[3]]
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal" or ca[i1:i2] == cb[j1:j2]:
            emit(tag, i1, i2, j1, j2)
            continue
        k = 0
        while k < i2 - i1:
            same = ca[i1 + k] == cb[j1 + k]
            end = k
            while end < i2 - i1 and (ca[i1 + end] == cb[j1 + end]) == same:
                end += 1
            emit("equal" if same else "replace", i1 + k, i1 + end, j1 + k, j1 + end)
            k = end
    return out


def restore_merged_characters(resolved: str, formatted: str) -> str | None:
    """Keep the formatter's STRUCTURE but the merge's CHARACTERS.

    The formatter's job is layout; the characters were already settled by two
    engines and must not drift.  But a model reversing a right-to-left cell
    naturally writes the variant it knows (内 becomes 內, 烟 becomes 煙), and
    one such slip used to cost a page its whole table.

    Only glyph-for-glyph substitutions are repaired, and each invented glyph is
    paired with a dropped one by EVIDENCE: the characters adjacent to it in the
    output must also be adjacent to the dropped glyph in the merge.  Adjacency
    is unordered because the formatter legitimately reverses cells and
    transposes tables.  An invented glyph whose neighbours match no dropped
    glyph, or more than one, is left alone and the page goes to the retry.
    """
    source, output = Counter(CJK(resolved)), Counter(CJK(formatted))
    surplus = output - source          # glyphs the formatter brought in
    deficit = source - output          # glyphs it dropped in exchange
    if not surplus or surplus == deficit or sum(surplus.values()) > 6:
        return None

    def neighbour_sets(text: str, glyph: str) -> list[set[str]]:
        seq = CJK(text)
        return [{seq[i - 1] if i > 0 else "", seq[i + 1] if i + 1 < len(seq) else ""} - {""}
                for i, c in enumerate(seq) if c == glyph]

    pairs: list[tuple[str, str]] = []
    for wrong in surplus:
        contexts = neighbour_sets(formatted, wrong)
        matches = set()
        for right in deficit:
            if right == wrong:
                continue
            right_contexts = neighbour_sets(resolved, right)
            if all(any(ctx & rc for rc in right_contexts) for ctx in contexts if ctx):
                matches.add(right)
        if len(matches) != 1:
            return None
        pairs.append((wrong, matches.pop()))
    repaired = formatted
    for wrong, right in pairs:
        repaired = repaired.replace(wrong, right)
    return repaired


def verify_characters(resolved: str, formatted: str) -> tuple[bool, str]:
    """Formatting may rearrange text, but cannot invent or delete glyphs.

    Image-confirmed furniture removal happens in content-recovery before this
    lock.  Include Latin, numerals and formula signs; a CJK-only subsequence
    check used to accept both a missing digit and a completely empty output.
    Position-sensitive source features are checked separately against their
    image-confirmed anchors and in the final full-page coverage review.
    """
    have, output = (_content_fidelity.character_counts(t) for t in (resolved, formatted))
    invented = output - have
    if invented:
        detail = "".join(char * count for char, count in invented.items())
        return False, (f"formatter introduced {sum(invented.values())} character(s) "
                       f"not in the merged text: {detail[:40]}")
    lost = have - output
    if lost:
        detail = "".join(char * count for char, count in lost.items())
        return False, f"formatter deleted {sum(lost.values())} retained character(s): {detail[:40]}"
    return True, "characters preserved"


RULE_COVERAGE = 0.5      # a row/column this inked is a printed rule, not text
INK_MARGIN = 45          # levels below the page's own paper before it counts as ink
MEASURE_WIDTH = 500      # downsample: the measure only needs to be approximate


def page_ink_fraction(render: Path) -> float:
    """Fraction of the page carrying TEXT ink, or -1.0 if it cannot be read.

    Two corrections over a plain darkness count, both forced by real pages:

    An absolute cutoff assumes white paper.  The corpus's 1910 government report is scanned
    grey - its paper sits well below level 200 - so counting pixels under 200
    made every blank page read as 99.9% ink and three genuine blanks went
    undetected.  Ink is contrast against the paper this page was scanned on.

    And ink is not the same as text.  That book's blank leaves still carry a
    printed border frame and column rules, so they measure MORE ink (6.4%) than
    a real table page in a fiscal report (4.0%).  Rows and columns that are
    almost entirely inked are rules; what is left is text.  A blank ruled leaf
    then measures near zero, which is the whole point.

    Pure PIL - the runtime has no numpy - using row-slice byte counts, so the
    per-page cost stays in the milliseconds.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - PIL is needed for crops anyway
        return -1.0
    try:
        with Image.open(render) as opened:
            image = opened.convert("L")
            if image.width > MEASURE_WIDTH:
                height = max(1, round(image.height * MEASURE_WIDTH / image.width))
                image = image.resize((MEASURE_WIDTH, height), Image.BOX)
            histogram = image.histogram()
            total = sum(histogram)
            if not total:
                return -1.0
            paper = max(range(len(histogram)), key=lambda level: histogram[level])
            cutoff = paper - INK_MARGIN
            if cutoff <= 0:
                # Modal level already black: no usable contrast to measure.
                return -1.0
            binary = image.point(lambda level: 255 if level < cutoff else 0)
            width, height = binary.size
            rows = binary.tobytes()
            columns = binary.transpose(Image.ROTATE_90).tobytes()
    except OSError:
        return -1.0

    row_ink = [rows[y * width:(y + 1) * width].count(255) for y in range(height)]
    column_ink = [columns[x * height:(x + 1) * height].count(255) for x in range(width)]
    rule_columns = sum(1 for ink in column_ink if ink >= height * RULE_COVERAGE)
    text_rows = [ink for ink in row_ink if ink < width * RULE_COVERAGE]
    if not text_rows:
        return 0.0
    # Every surviving row still crosses the rule columns; discount them.
    text_ink = sum(max(0, ink - rule_columns) for ink in text_rows)
    area = len(text_rows) * max(1, width - rule_columns)
    return text_ink / area


# A blank leaf is far below the sparsest real page once rules are discounted.
# Used only as a recorded measurement and a tiebreaker - never as the sole
# gate, because it does not separate cleanly across scans: an empty ruled leaf
# of the corpus's 1910 government report measures 0.024 against 0.0065 for a genuinely sparse
# text page of a historical monograph.  The reliable signal is two engines, not pixels.
BLANK_INK_FRACTION = 0.004

# A page of text that only ONE engine saw at all.  Both numbers are deliberately
# far apart: this is not "the engines disagree", it is "one engine read a full
# page and the other read nothing".
UNCORROBORATED_MINIMUM = 200
UNCORROBORATED_WITNESS = 10

# Distinct 4-grams as a fraction of all 4-grams.  Real prose sits near 1.0
# (median 0.993 over 314 pages).  This alone is NOT a safe test - a genuine
# page of budget boilerplate measured 0.051, below a known repetition loop at
# 0.068 - so it is only ever used together with the corroboration test.
DEGENERATE_NGRAM_RATIO = 0.20


def distinct_ngram_ratio(text: str, size: int = 4) -> float:
    """How much of the text is not a repeat of itself."""
    grams = [text[i:i + size] for i in range(len(text) - size + 1)]
    return len(set(grams)) / len(grams) if grams else 1.0


def unsupported_reading(draft: str, draft2: str | None) -> str:
    """The text one engine returned for a page the other did not see.

    Returns the unsupported text, or "" when the reading is corroborated.

    On the corpus's 1910 government report p.73 - an empty ruled leaf - PaddleOCR emitted
    4,244 characters of 欽定四庫全書卷一卷二卷三… while HunyuanOCR read nothing.
    Neither test catches that safely on its own: p.48 of the same book is a
    genuine page whose repetition score is LOWER still, and a page legitimately
    read by one engine alone must not be thrown away.  Both together are safe.
    """
    first, second = CJK(draft), CJK(draft2 or "")
    longer, shorter = (first, second) if len(first) >= len(second) else (second, first)
    if len(longer) < UNCORROBORATED_MINIMUM or len(shorter) >= UNCORROBORATED_WITNESS:
        return ""
    if distinct_ngram_ratio(longer) > DEGENERATE_NGRAM_RATIO:
        return ""
    return longer


def looped_blocks(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str | None) -> list[int]:
    """Engine-A blocks whose reading is a repetition loop engine B does not share.

    unsupported_reading's two tests, block by block: long and repeating itself
    (more than UNCORROBORATED_MINIMUM characters, fewer than DEGENERATE_NGRAM_RATIO
    distinct 4-grams), and not corroborated - engine B reads the block's most
    repeated 4-gram fewer times than the block does, so a printed table of
    repeating boilerplate that engine B reads too is not taken for a loop.  Only
    when the blocks account for engine A's text and engine B's own page is not
    a loop.  Measured over the 794 pages of six books with both drafts: three
    blocks are this long and repetitive, every one a loop whose most repeated
    4-gram engine B never read - a chart's cells repeating (one 4-gram 415
    times) beside a clean leaf (3,677 characters, 0.072) and a title list
    over an empty ruled area (4,244, 0.032), both on pages sent whole to
    engine B's text, and the same list on a page engine B left blank (an
    unsupported_reading).
    """
    if not blocks or draft2 is None or sum(len(CJK(str(b["text"]))) for b in blocks) != len(CJK(draft)):
        return []
    other = CJK(draft2)
    if len(other) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(other) < DEGENERATE_NGRAM_RATIO:
        return []
    out = []
    for index, block in enumerate(blocks):
        text = CJK(str(block["text"]))
        if len(text) <= UNCORROBORATED_MINIMUM or distinct_ngram_ratio(text) >= DEGENERATE_NGRAM_RATIO:
            continue
        top, count = Counter(text[i:i + 4] for i in range(len(text) - 3)).most_common(1)[0]
        # Counted the same way on both sides, overlapping: str.count skips
        # overlaps, so a printed repetition of period two or three (〇〇 cells,
        # a repeated field) that engine B reads just as often counted half or a
        # third as many there and was taken for a loop.
        if sum(other.startswith(top, i) for i in range(len(other) - 3)) < count:
            out.append(index)
    return out


# The segment key that marks engine B's reading of a quarantined block.
LOOPED_READING = "readingOfLoopedBlock"


def layout_index(blocks: Sequence[Mapping[str, Any]], position: int) -> int:
    """The index engine A's layout gave the block now at POSITION: blocks keep
    it through a move (quarantine_looped_block), and every block number sealed
    is the layout's, so passes, tables and doubts name the same block the
    layout file and engineALoopsDropped do."""
    return blocks[position].get("layoutIndex", position)


def quarantine_looped_block(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str,
                            prefer: str | None = None, folded: bool | None = None) -> dict[str, Any] | None:
    """Engine A's page with its one looped block set aside, or None.

    A loop in one layout block made the whole page an order conflict and sent it
    to engine B's bare text (the loop outweighs the page, and the page's reading
    counts as looped): measured on one page, a 3,677-character loop in the lower
    leaf's block threw away engine A's clean upper leaf, whose 475 of 495
    characters align with engine B in order.  Here the block's text is emptied
    - its box kept, so its crop still frames the region, and its label kept in
    `quarantined` while it is handled as text, since whatever it holds now is
    engine B's plain reading - engine A's stream is rebuilt from the blocks and
    aligned with engine B again.  Engine
    B's reading of the region is the stretch only engine B read that shares
    more 4-grams with the loop's own text (a loop usually starts as a reading
    of the region before it repeats: 146 of the 336 there) than with the rest
    of engine A's page; it is kept as the block's text, the block moved to the
    block boundary where engine B read it so the page follows engine B's order,
    and left out of the agreement's denominator.  With none, the block stays
    empty where it was: nothing engine B read alone is the region's (the empty
    ruled area).

    The reading is used only when it is found whole and at a block boundary.
    Where the alignment joined it with a neighbouring disagreement - engine A
    read a fold label there, engine B a column engine A skipped and then the
    region - the replacement is split: engine A's side becomes its own
    one-engine reading and engine B's side the region's text, on the boundary
    side of it.  Otherwise the reading could not stand as the block's text and
    would go down the ordinary path, where a preference for engine A keeps the
    label and drops everything engine B read there, and 裁決 may answer "no
    character" to a one-engine insertion: a reading in several pieces, or
    inside another block's text, keeps the page's old judgement (None).

    Returns the new blocks, draft and segments, the agreement for the order
    test, and what was dropped; None when there is not exactly one looped
    block (two or more keep the whole-page fallback) or when engine B's
    reading of the region cannot be placed.
    """
    looped = looped_blocks(blocks, draft, draft2)
    if len(looped) != 1:
        return None
    index = looped[0]
    loop = CJK(str(blocks[index]["text"]))
    emptied = {**blocks[index], "text": "", "label": "text",
               "quarantined": {"label": blocks[index]["label"]}}
    # Each block keeps the index engine A's layout gave it (layout_index): the
    # emptied block may move, and what is sealed about a block must still name
    # the one engineALoopsDropped names and the layout file lists.
    kept = [dict(emptied if i == index else b, layoutIndex=layout_index(blocks, i)) for i, b in enumerate(blocks)]
    reduced = "\n".join(str(b["text"]) for b in kept)
    segments = fuse_numeral_runs(align_drafts(reduced, draft2, folded, prefer))
    rest = CJK(reduced)

    def grams(text: str) -> set[str]:
        return {text[i:i + 4] for i in range(len(text) - 3)}

    loop_grams, rest_grams = grams(loop), grams(rest)
    starts = set(itertools.accumulate([0] + [len(CJK(str(b["text"]))) for b in kept]))
    # Every stretch only engine B read that reads like the loop's region.
    candidates, at = [], 0
    for position, seg in enumerate(segments):
        if seg["tag"] != "equal" and seg["b"]:
            own = [seg["b"][i:i + 4] for i in range(len(seg["b"]) - 3)]
            shared = sum(g in loop_grams for g in own)
            if shared and shared > sum(g in rest_grams for g in own):
                candidates.append((position, at))
        at += len(seg["a"])
    if len(candidates) > 1:
        return None
    read, reading_at, found, shared_with = 0, 0, "none", None
    if candidates:
        position, at = candidates[0]
        seg = segments[position]
        end = at + len(seg["a"])
        if seg["tag"] == "insert" and at in starts:
            found, reading_at = "insertion", at
        elif seg["tag"] == "replace" and (end in starts or at in starts):
            # Engine B's side on the boundary side of engine A's: the reading
            # after the label when the label's end is a block boundary.
            first = end not in starts
            reading = {"tag": "insert", "a": "", "b": seg["b"],
                       "before": seg["before"] if first else (seg["before"] + seg["a"])[-8:],
                       "after": (seg["a"] + seg["after"])[:8] if first else seg["after"]}
            own = {"tag": "delete", "a": seg["a"], "b": "", "before": seg["before"], "after": seg["after"]}
            segments[position:position + 1] = [reading, own] if first else [own, reading]
            found, shared_with, reading_at, seg = "split", seg["a"], (at if first else end), reading
        else:
            return None
    if found != "none":
        seg[LOOPED_READING] = True
        read = len(seg["b"])
        # The block goes before the first block holding text that starts where
        # engine B read the region, or last.
        moved = kept.pop(index)
        offset, slot = 0, len(kept)
        for i, block in enumerate(kept):
            length = len(CJK(str(block["text"])))
            if offset == reading_at and length:
                slot = i
                break
            offset += length
        kept.insert(slot, moved)
    agreed = sum(len(x["a"]) for x in segments if x["tag"] == "equal")
    total = max(len(rest), len(CJK(draft2)) - read, 1)
    return {"blocks": kept, "draft": reduced, "segments": segments, "agreement": agreed / total,
            "dropped": {"block": layout_index(blocks, index), "label": blocks[index]["label"],
                        "characters": len(loop),
                        "distinct4gram": round(distinct_ngram_ratio(loop), 3),
                        "engineBCharacters": read, "text": loop[:2000],
                        # How engine B's reading of the region was found: a
                        # one-engine insertion, split off a replacement it
                        # shared with engine A's text (sharedWith), or none.
                        "engineBReading": {"found": found, "sharedWith": shared_with}}}


def order_judgement(blocks: Sequence[Mapping[str, Any]], draft: str, draft2: str,
                    prefer: str | None = None, folded: bool | None = None) -> dict[str, Any]:
    """The page's order test on these readings, before anything is merged:
    the segments, the agreement - what the engines read in the same order,
    over the longer reading, engine B's reading of a looped block
    (quarantine_looped_block) left out - and the quarantine when it applies
    (it sets its one looped block aside and what is left merges), else None.
    PREFER is the page's engine preference and FOLDED how the drafts are
    matched (align_drafts)."""
    segments = fuse_numeral_runs(align_drafts(draft, draft2, folded, prefer))
    agreed = sum(len(x["a"]) for x in segments if x["tag"] == "equal")
    agreement = agreed / max(len(CJK(draft)), len(CJK(draft2)), 1)
    quarantine = quarantine_looped_block(blocks, draft, draft2, prefer, folded)
    if quarantine is not None and quarantine["agreement"] >= READING_ORDER_AGREEMENT:
        return {"segments": quarantine["segments"], "agreement": quarantine["agreement"],
                "quarantine": quarantine}
    return {"segments": segments, "agreement": agreement, "quarantine": None}


# A page where nearly every character is followed by a mark is a degenerate
# formatter output, not a style of book: measured 96.6 marks per 100 characters
# on one page against 4-25 for normal ones.  It is a retry signal, not a fact
# about the original.
DEGENERATE_PUNCTUATION_DENSITY = 60.0


# The formatter may delete page furniture - running heads, folios - but nothing
# checked WHAT it deleted, and it was removing real text: a 38-character 凡例
# clause on p.3 and a whole table row (夏同和 / 陳光遠代表江西督軍秘書 / 前清狀元 /
# 貴州) on p.207, both read by BOTH engines. A run this long that two independent
# engines agree on is on the page; furniture is short (a 6-character running head,
# a folio) and stays under the threshold.
DELETED_RUN_LIMIT = 12

# Below this share of the page's characters aligned in order, the two engines
# read the page in different orders and it is not merged (merge-order-conflict).
READING_ORDER_AGREEMENT = 0.70

# In an order conflict the whole page comes from one engine.  If the preferred
# engine read less than this share of what the other read, it did not read the
# page - it read a fragment of it - and the preference is overridden.
ORDER_CONFLICT_FLOOR = 0.6

# A running head is short and thin.  Anything longer or larger that happens to
# share the folio's margin is content, not furniture.
MARGIN_FURNITURE_LIMIT = 40
MARGIN_FURNITURE_AREA = 0.20


FURNITURE_GRAM = 8
# A string on this share of the book's pages is a running head or a folio, not
# a sentence.  Measured: a fiscal report's running head appears on 19 of 28
# sampled pages, while the real clause a historical monograph lost off p.3 appears on 1
# of 344.  Length cannot separate those two - the running head is 33 characters
# and the lost clause 38 - but recurrence separates them cleanly.
FURNITURE_PAGE_SHARE = 0.15
FURNITURE_MINIMUM_PAGES = 3


def furniture_grams(draft_paths: Sequence[Path]) -> set[str]:
    """Character n-grams that recur across the book's pages.

    Computed once per run from the OCR drafts, with no model call.  Without it
    the deletion guard blocks the formatter from removing a long running head,
    which is exactly what it is supposed to remove.
    """
    seen: dict[str, int] = {}
    pages = 0
    for path in draft_paths:
        try:
            text = CJK(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        pages += 1
        for gram in {text[i:i + FURNITURE_GRAM]
                     for i in range(len(text) - FURNITURE_GRAM + 1)}:
            seen[gram] = seen.get(gram, 0) + 1
    if pages < FURNITURE_MINIMUM_PAGES:
        return set()
    floor = max(FURNITURE_MINIMUM_PAGES, int(pages * FURNITURE_PAGE_SHARE))
    return {gram for gram, count in seen.items() if count >= floor}


# A running head or folio sits at the page boundary; body text does not.  This
# is what separates a provincial finance report第三編省稅第十二章稅捐第二十三節花筵搨及花搨附加九三
# (33 characters, page start, all furniture) from the 38-character 凡例 clause
# a historical monograph lost off the middle of p.3.
FURNITURE_EDGE = 8


def margin_furniture(blocks: Sequence[Mapping[str, Any]],
                     labels: Sequence[str] = ("header", "footer", "number")) -> list[str]:
    """Text of blocks that sit in the page's furniture margin.

    Recurrence needs a book; a single-page run has none, and the bare length
    limit then rejects the formatter for removing a 16-character running head -
    which is the realistic case when someone OCRs a handful of pages.

    The anchor is the folio.  PaddleOCR's `number` label is the one furniture
    label that has been reliable across all four calibration books, while
    `header`, `footer`, `text` and `figure_title` have each been observed on
    real body content.  Whatever shares the folio's margin band is furniture:
    on a multi-page fiscal report p.24 the running head and the folio both sit at
    x≈0.05 while the page's real title sits at x≈0.85.  `labels`: the blocks
    taken by their label alone (the table stitch takes only the folio's).
    """
    return [text for text in (CJK(str(b.get("text") or "")) for b in margin_blocks(blocks, labels)) if text]


def margin_blocks(blocks: Sequence[Mapping[str, Any]],
                  labels: Sequence[str] = ("header", "footer", "number")) -> list[Mapping[str, Any]]:
    """The blocks margin_furniture reads: those labelled `labels`, then, per
    folio, the short thin blocks in its margin band."""
    folios = [b for b in blocks if b.get("label") == "number"
              and isinstance(b.get("box"), list) and len(b["box"]) == 4]
    found = [b for b in blocks if b.get("label") in labels]
    for folio in folios:
        fx, fy = folio["box"][0], folio["box"][1]
        for block in blocks:
            box = block.get("box")
            if not (isinstance(box, list) and len(box) == 4) or block is folio:
                continue
            # Same vertical band (a horizontal running head) or the same
            # horizontal band (a vertical one, as in these books).
            if not (abs(box[0] - fx) <= 0.06 or abs(box[1] - fy) <= 0.06):
                continue
            # Size is what stops this from waving through real content: on this
            # very page the TABLE block also sits in the folio's margin band,
            # and treating it as furniture would let a deleted table pass
            # unreported.  A running head is short and thin; a table is not.
            text = CJK(str(block.get("text") or ""))
            area = abs(box[2]) * abs(box[3]) if len(box) == 4 else 1.0
            if len(text) <= MARGIN_FURNITURE_LIMIT and area <= MARGIN_FURNITURE_AREA:
                found.append(block)
    return found


def furniture_text(text: str, heads: Sequence[str], furniture: frozenset[str] | set[str] | None) -> bool:
    """Whether TEXT, a block's own reading, is the book's furniture (furniture_band):
    one of the running heads its profile measured (is_running_head), or text
    that those heads - with the folio's figures beside them - and the n-grams
    recurring across the book's pages (furniture_grams) explain but for at
    most INSERTION_LIMIT characters, one at least explained
    (furniture_leftover).  A block's layout label is not this evidence: the
    layout labels section headings, table cells and body lines header, and a
    signature number (the review's scan of the whole corpus)."""
    chars = CJK(text)
    if not chars:
        return False
    if heads and is_running_head(chars, heads):
        return True
    left = furniture_leftover(chars, furniture or frozenset(), heads)
    return left <= INSERTION_LIMIT and left < len(chars)


def at_head_size(block: Mapping[str, Any], text: str, arguments: Any) -> bool:
    """Whether BLOCK, reading TEXT, is set in the type size of one
    of the running heads the book's profile measured (runningHeadGlyph); False
    where it measured none.  Unlike head_size_matches, for any text: a head
    with its volume or chapter beside it is the head's size too."""
    glyph = [size for size in (profile_section(arguments, "layout").get("runningHeadGlyph") or {}).values() if size]
    box = block.get("box")
    chars = len(CJK(text))
    if not (glyph and chars and isinstance(box, list) and len(box) == 4):
        return False
    size = max(abs(box[2]), abs(box[3])) / chars
    return any(HEAD_SIZE_RATIO[0] <= size / g <= HEAD_SIZE_RATIO[1] for g in glyph)


def furniture_band(blocks: Sequence[Mapping[str, Any]], heads: Sequence[str], arguments: Any,
                   furniture: frozenset[str] | set[str] | None = None) -> dict[int, str]:
    """The positions of engine A's layout blocks in the page's running-head
    band, each with why it is there.  The repair's book model takes it
    (_repair_model); phase 3 wrote one-sided readings in it as nothing in a
    trial since removed.

    The folios are the blocks the layout labelled number that read numerals,
    or no character (figures, a mark).  The anchors are the blocks whose own
    text is the book's furniture with a second piece of evidence: read as one
    of the profile's running heads at the head's own type size
    (is_running_head, head_size_matches), or its text the book's furniture
    (furniture_text) and labelled header or footer, set in a head's type size
    (at_head_size), or with half its width in a folio's column or half its
    height in a folio's row.  A label alone is not enough: a section heading
    at the start of the body labelled header would otherwise be the band's.

    Any other block is in the band when it is short and thin (margin_blocks'
    limits), holds no more characters than the anchor, and at least half its
    width lies in the column of a tall anchor, or half its height in the row
    of a wide one.  A folio extends the band nowhere: its box says nothing of
    which way the band runs, and its column and row cross the body - a body
    column printed under a folio in the top margin, a heading above the body
    in the column of a folio centred at the foot (made-up pages in the same
    check; the review's corpus scan found the first on real pages).  The band
    is where the page prints its furniture - a side column, the fold between
    two leaves, a margin - not a fixed strip at the top or the bottom.

    A table block (labelled table) is never the band's, whatever its size and
    place: its text is the table's cells."""
    def box_of(block: Mapping[str, Any]) -> list[float] | None:
        box = block.get("box")
        return box if isinstance(box, list) and len(box) == 4 else None

    def share(lo: float, hi: float, anchor_lo: float, anchor_hi: float) -> float:
        return 0.0 if hi <= lo else max(0.0, min(hi, anchor_hi) - max(lo, anchor_lo)) / (hi - lo)

    def in_line(box: Sequence[float], anchor: Sequence[float], column: bool, row: bool) -> str | None:
        x, y, w, h = box
        ax, ay, aw, ah = anchor
        if column and share(x, x + w, ax, ax + aw) >= 0.5:
            return "column"
        if row and share(y, y + h, ay, ay + ah) >= 0.5:
            return "row"
        return None

    band: dict[int, str] = {}
    folios = [k for k, block in enumerate(blocks) if block.get("label") == "number"
              and all(c in NUMERAL_CHARS for c in CJK(str(block.get("text") or "")))]
    for k in folios:
        band[k] = "folio"
    anchors: list[int] = []
    for k, block in enumerate(blocks):
        text = str(block.get("text") or "")
        label = block.get("label")
        box = box_of(block)
        if k in band or label == "table":
            continue
        if heads and is_running_head(text, heads) and head_size_matches(block, text, arguments):
            why = "running head"
        elif not furniture_text(text, heads, furniture):
            continue
        elif label in ("header", "footer"):
            why = f"label {label}, furniture text"
        elif at_head_size(block, text, arguments):
            why = "head's size, furniture text"
        else:
            line = next(((line, f) for f in folios if box and box_of(blocks[f])
                         for line in [in_line(box, blocks[f]["box"], True, True)] if line), None)
            if line is None:
                continue
            why = f"{line[0]} of block {layout_index(blocks, line[1])}, furniture text"
        band[k] = why
        if box:
            anchors.append(k)
    for k, block in enumerate(blocks):
        box = box_of(block)
        if k in band or box is None or block.get("label") == "table":
            continue
        size = len(CJK(str(block.get("text") or "")))
        if size > MARGIN_FURNITURE_LIMIT or abs(box[2]) * abs(box[3]) > MARGIN_FURNITURE_AREA:
            continue
        for a in anchors:
            ax, ay, aw, ah = blocks[a]["box"]
            if size > len(CJK(str(blocks[a].get("text") or ""))):
                continue
            line = in_line(box, blocks[a]["box"], ah >= aw, aw > ah)
            if line:
                band[k] = f"{line} of block {layout_index(blocks, a)}"
                break
    return band


# Switch --learned-heads (on by default; ROUND4_DEFAULTS): the running head learned by where it
# is printed, not by what recurs verbatim alone.
#
# The profile keeps a block as a running head only when its text recurs
# verbatim on half the pages (survey_layout): the title every head prints.
# What is printed beside the title changes: a volume or part label every few
# hundred pages, a contents page's own word, the folio every page.  None of it
# recurs on half the pages, so none of it was furniture evidence, and the
# profile drop needs a block reading the title alone (is_running_head).
# Measured on a 488-page book whose head column prints title, volume label and
# folio (a survey of its sealed pages, every instance checked on the render):
# the head, its volume label or its folio was left in the body on 63 pages -
# the title with its label as one paragraph (ten characters, not the
# seven-character title), the label in a block of its own written as a
# heading once the title was dropped, the folio written as a line - and on
# the pages whose structure pass was refused the fallback kept every head
# block the layout had not labelled header (184 of 400).
#
# learn_heads learns, from engine A's blocks of every page: the band - the
# blocks holding a measured running head (a stem) at its type size (anchors)
# and the short thin blocks in an anchor's column or row; the labels printed
# in the band beside the stem on LEARNED_LABEL_MINIMUM_PAGES pages or more,
# and the families of labels that differ only in a numeral (a volume label's
# number); the folio sequence (the scan's page less a constant, over runs of
# pages, from the folios read in the band); and where the anchors stand on
# odd and on even pages (regions).  What it assumes, each gated on the book's
# own evidence: the running head is printed in one band with the stem the
# profile measured (a book whose title never stands verbatim on half its
# pages learns nothing: no stem, no band); a label is a label when the book
# prints it beside the stem on several pages; the band's place on a page is
# its anchors' column or row, and a learned region stands in for a page whose
# anchor engine A misread only for text holding a stem or a label.
LEARNED_LABEL_MINIMUM_PAGES = 3
# A single character beside the stem is not taken for a label: measured on a
# facsimile's pages, the stem's own first character misread (the same wrong
# character each time) stood beside the stem on three pages.
LEARNED_LABEL_MINIMUM_CHARACTERS = 2
# A folio offset (the printed page number against the scan's) is learned when
# this many pages read it, and a region when this many pages' anchors stand
# there.
LEARNED_FOLIO_MINIMUM_PAGES = 3
LEARNED_REGION_MINIMUM_PAGES = 3
# Anchor centres further apart than this (page fraction) are two bands: the
# outer margin of odd pages and of even pages, the leaves of a facsimile.
LEARNED_REGION_GAP = 0.05
# The most figures a folio is taken to have: a run of numerals longer than
# this beside the head is not its folio.
FOLIO_MAX_FIGURES = 4
# How far a band block's thickness - its width across a column, its height
# across a row - may be from the stem's and still be the head's type size
# (head_size_fits).
HEAD_THICKNESS_RATIO = (0.6, 1.5)
NUMERAL_DIGITS = "〇一二三四五六七八九"
NUMERAL_VALUES = {**{c: i for i, c in enumerate(NUMERAL_DIGITS)}, "零": 0}
NUMERAL_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000}
# The tens a folio may print as one character (廿二, 卅五): a facsimile's leaf
# folios, measured on a book of 26 pages, write 21-39 so.
NUMERAL_TENS = {"廿": 20, "卅": 30, "卌": 40}
FOLIO_CHARS = NUMERAL_CHARS | frozenset(NUMERAL_TENS)
NUMERAL_RUN = "[" + "".join(sorted(FOLIO_CHARS)) + "]"


def numeral_value(text: str) -> int | None:
    """The value TEXT's numerals write, figure by figure (一七七, 二〇) or
    counted (一百七十七, 十二, 廿二); None when TEXT holds anything else."""
    chars = CJK(text)
    if not chars or any(c not in FOLIO_CHARS for c in chars):
        return None
    if not any(c in NUMERAL_UNITS or c in NUMERAL_TENS for c in chars):
        return int("".join(str(NUMERAL_VALUES[c]) for c in chars))
    total = section = 0
    digit = None
    for c in chars:
        if c in NUMERAL_VALUES:
            digit = NUMERAL_VALUES[c]
            continue
        if c in NUMERAL_TENS:
            section, digit = section + NUMERAL_TENS[c], None
            continue
        unit = NUMERAL_UNITS[c]
        if unit == 10000:
            total += (section + (digit or 0)) * unit
            section = 0
        else:
            section += (1 if digit is None else digit) * unit
        digit = None
    return total + section + (digit or 0)


def numeral_forms(value: int) -> set[str]:
    """VALUE written as a folio is printed: figure by figure (一七七, 二〇) and
    counted (一百七十七, 一百〇五 or 一百零五, 十二 or 一十二, 二十二 or 廿二)."""
    figures = "".join(NUMERAL_DIGITS[int(d)] for d in str(value))
    if value <= 0 or value >= 10000:
        return {figures}
    counted, zero = "", False
    for unit, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit = value // unit % 10
        if not digit:
            zero = zero or bool(counted)
            continue
        if zero:
            counted, zero = counted + "〇", False
        counted += NUMERAL_DIGITS[digit] + name
    forms = {figures, counted, counted.replace("〇", "零")}
    if counted.startswith("一十"):
        forms.add(counted[1:])
    for tens, value_of in NUMERAL_TENS.items():
        written = NUMERAL_DIGITS[value_of // 10] + "十"
        if written in counted:
            forms.add(counted.replace(written, tens))
    return forms


def head_size_fits(block: Mapping[str, Any], characters: int, glyph: float | None, thickness: float | None,
                   axis: str | None = None) -> bool:
    """Whether BLOCK, reading CHARACTERS characters, is set in a running head's
    type size: its thickness across the band - its width in a column (AXIS
    "column"), its height in a row ("row"), its shorter side when AXIS is
    None - within HEAD_THICKNESS_RATIO of the stem's THICKNESS, and its long
    side no shorter than its characters take at the stem's size GLYPH (page
    fraction per character; HEAD_SIZE_RATIO's lower bound).  Not the long
    side's length against the characters: measured on a book's pages, the
    layout's box for the head took in the label printed after it that it did
    not read (a box of ten characters' length reading the seven-character
    title), and a contents page's two-character label was spaced out to half
    again the title's length per character.  A title set large is thicker:
    the same book's volume titles on its divider pages are twice the head's
    width."""
    box = block.get("box")
    if not (glyph and thickness and characters and isinstance(box, list) and len(box) == 4):
        return False
    w, h = abs(box[2]), abs(box[3])
    across, along = (w, h) if axis == "column" else (h, w) if axis == "row" else (min(w, h), max(w, h))
    return (HEAD_THICKNESS_RATIO[0] * thickness <= across <= HEAD_THICKNESS_RATIO[1] * thickness
            and along >= HEAD_SIZE_RATIO[0] * glyph * characters)


def short_thin(block: Mapping[str, Any]) -> bool:
    """A block that may be a margin's furniture: short and thin (the limits of
    margin_blocks), with a box, not a table."""
    box = block.get("box")
    return (block.get("label") != "table" and isinstance(box, list) and len(box) == 4
            and len(CJK(str(block.get("text") or ""))) <= MARGIN_FURNITURE_LIMIT
            and abs(box[2]) * abs(box[3]) <= MARGIN_FURNITURE_AREA)


def stem_windows(chars: str, stem: str) -> list[tuple[float, int, int]]:
    """Every stretch of CHARS is_running_head takes for STEM, as (how like it,
    start, length): the stem's own length with characters misread in place
    (a substitution, nothing gained or lost), or one character shorter (a
    character not read), the stem's own length first, then the likest.  Not
    one character longer, nor the stem's length with a character lost and
    one gained: either takes in the character beside the stem - a label's,
    or the body's, a title that is the stem with a word after it (a preface
    to the book) read as the head."""
    found = []
    for length in (len(stem), len(stem) - 1):
        for start in range(0, len(chars) - length + 1) if length >= 2 else ():
            window = chars[start:start + length]
            if not is_running_head(window, [stem]):
                continue
            matcher = difflib.SequenceMatcher(None, window, stem, autojunk=False)
            if length == len(stem) and any(tag in ("insert", "delete") for tag, *_ in matcher.get_opcodes()):
                continue
            found.append((length == len(stem), 1.0 if window == stem else matcher.ratio(), -start, length))
    return [(ratio, -start, length) for _, ratio, start, length in sorted(found, reverse=True)]


def head_band_blocks(blocks: Sequence[Mapping[str, Any]], stems: Sequence[str], glyph: Mapping[str, float],
                     thickness: Mapping[str, float],
                     is_head: Callable[[str], bool] | None = None) -> dict[int, dict[str, Any]]:
    """Engine A's blocks in a page's running-head band, by position: the
    anchors - short thin blocks holding one of STEMS (stem_windows) set in its
    type size (head_size_fits, GLYPH and THICKNESS) - and the short thin
    blocks with half their width in a tall anchor's column or half their
    height in a wide one's row, each reading numerals or nothing (a folio,
    figures) or set in the anchor's type size across the band.  A title set
    large, the body, a table are not.
    IS_HEAD, when given, must also take an anchor's own text for the running
    head: a body line the layout read in one block with the head beside it
    holds the stem too, and its column is the body's."""
    anchors: dict[int, dict[str, Any]] = {}
    for k, block in enumerate(blocks):
        if not short_thin(block):
            continue
        chars = CJK(str(block.get("text") or ""))
        for stem in stems:
            windows = stem_windows(chars, stem)
            if (windows and head_size_fits(block, len(chars), glyph.get(stem), thickness.get(stem))
                    and (is_head is None or is_head(chars))):
                anchors[k] = {"role": "anchor", "stem": stem, "at": windows[0][1], "length": windows[0][2]}
                break
    band = dict(anchors)
    for k, block in enumerate(blocks):
        if k in band or not short_thin(block):
            continue
        x, y, w, h = (float(v) for v in block["box"])
        chars = CJK(str(block.get("text") or ""))
        for a, anchor in anchors.items():
            ax, ay, aw, ah = (float(v) for v in blocks[a]["box"])
            tall = abs(ah) >= abs(aw)
            lo, hi, alo, ahi = (x, x + w, ax, ax + aw) if tall else (y, y + h, ay, ay + ah)
            if hi <= lo or max(0.0, min(hi, ahi) - max(lo, alo)) / (hi - lo) < 0.5:
                continue
            if (not chars or all(c in FOLIO_CHARS for c in chars)
                    or head_size_fits(block, len(chars), glyph.get(anchor["stem"]), thickness.get(anchor["stem"]),
                                      "column" if tall else "row")):
                band[k] = {"role": "member", "anchor": a}
                break
    return band


def split_folio(piece: str) -> tuple[str, list[str]]:
    """PIECE, text printed in the running-head band beside the stem or apart
    from it, as its label and the folio figures at either end of it: numerals
    at a piece's start or end are the folio's, what is between them a label
    (a volume label's own numeral is inside it)."""
    start, end = 0, len(piece)
    while start < end and piece[start] in FOLIO_CHARS:
        start += 1
    while end > start and piece[end - 1] in FOLIO_CHARS:
        end -= 1
    return piece[start:end], [f for f in (piece[:start], piece[end:]) if f]


def label_skeleton(label: str) -> str:
    """LABEL with each run of numerals written N: the family it belongs to."""
    return re.sub(NUMERAL_RUN + "+", "N", label)


def folio_runs(observed: Sequence[tuple[int, int]], minimum: int = LEARNED_FOLIO_MINIMUM_PAGES
               ) -> list[dict[str, int]]:
    """The folio sequence from OBSERVED (scan page, folio read): runs of pages
    whose folio is the scan's page less one offset, each offset read on
    MINIMUM pages or more.  A folio misread is an offset read on fewer, and
    neither starts nor breaks a run."""
    counts = Counter(page - value for page, value in observed)
    runs: list[dict[str, int]] = []
    for page, value in sorted(observed):
        offset = page - value
        if counts[offset] < minimum:
            continue
        if runs and runs[-1]["offset"] == offset:
            runs[-1]["last"], runs[-1]["pages"] = page, runs[-1]["pages"] + 1
        else:
            runs.append({"first": page, "last": page, "offset": offset, "pages": 1})
    merged: list[dict[str, int]] = []
    for run in runs:
        if run["pages"] < minimum:
            continue
        if merged and merged[-1]["offset"] == run["offset"]:
            merged[-1]["last"], merged[-1]["pages"] = run["last"], merged[-1]["pages"] + run["pages"]
        else:
            merged.append(dict(run))
    return merged


def expected_folios(learned: Mapping[str, Any], page: int) -> set[int]:
    """The folio the learned sequence prints on scan PAGE: its run's; between
    two runs, either's; before the first or after the last, the nearest's."""
    runs = (learned.get("folio") or {}).get("runs") or []
    if not runs:
        return set()
    inside = [run for run in runs if run["first"] <= page <= run["last"]]
    if inside:
        return {page - run["offset"] for run in inside}
    before = [run for run in runs if run["last"] < page]
    after = [run for run in runs if run["first"] > page]
    near = ([before[-1]] if before else []) + ([after[0]] if after else [])
    return {page - run["offset"] for run in near if page - run["offset"] > 0}


def learn_heads(page_blocks: Mapping[int, Sequence[Mapping[str, Any]]], heads: Sequence[str]) -> dict[str, Any]:
    """Switch --learned-heads: the book's running head learned by where it is
    printed (see LEARNED_LABEL_MINIMUM_PAGES), from PAGE_BLOCKS (scan page ->
    engine A's blocks) and HEADS, the running heads the profile measured by
    recurrence (the stems).  No model call."""
    stems = sorted({CJK(h) for h in heads if len(CJK(h)) >= 2}, key=lambda h: (-len(h), h))
    record: dict[str, Any] = {"stems": stems, "glyph": {}, "thickness": {}, "labels": {}, "families": [],
                              "folio": {"observed": 0, "runs": []}, "regions": [],
                              "pagesExamined": len(page_blocks), "pagesWithBand": 0}
    # The stems' type size, from the blocks reading one alone: the length of a
    # character along the band, and the band's thickness across it.
    sizes: dict[str, list[tuple[float, float]]] = {stem: [] for stem in stems}
    for blocks in page_blocks.values():
        for block in blocks:
            chars, box = CJK(str(block.get("text") or "")), block.get("box")
            if chars in sizes and isinstance(box, list) and len(box) == 4:
                sizes[chars].append((max(abs(box[2]), abs(box[3])) / len(chars), min(abs(box[2]), abs(box[3]))))
    glyph = {stem: sorted(v[0] for v in values)[len(values) // 2] for stem, values in sizes.items() if values}
    thickness = {stem: sorted(v[1] for v in values)[len(values) // 2] for stem, values in sizes.items() if values}
    record["glyph"] = {stem: round(size, 5) for stem, size in glyph.items()}
    record["thickness"] = {stem: round(size, 5) for stem, size in thickness.items()}
    labels: dict[str, set[int]] = {}
    observed: list[tuple[int, int]] = []
    anchored: list[tuple[int, str, float, float]] = []
    for page in sorted(page_blocks):
        blocks = page_blocks[page]
        band = head_band_blocks(blocks, stems, glyph, thickness)
        if not band:
            continue
        record["pagesWithBand"] += 1
        for k, role in sorted(band.items()):
            chars = CJK(str(blocks[k].get("text") or ""))
            if role["role"] == "anchor":
                pieces = [chars[:role["at"]], chars[role["at"] + role["length"]:]]
                x, y, w, h = (float(v) for v in blocks[k]["box"])
                anchored.append((page, "column", x, x + w) if abs(h) >= abs(w) else (page, "row", y, y + h))
            else:
                pieces = [chars]
            # A folio read partly in figures (二〇 as 20, or 一〇四 as 10四)
            # has lost them from its characters: not a reading of the folio.
            figures = bool(re.search(r"[0-9A-Za-z]", str(blocks[k].get("text") or "")))
            for piece in pieces:
                label, folios = split_folio(piece)
                observed.extend((page, value) for value in (numeral_value(f) for f in folios
                                                            if len(f) <= FOLIO_MAX_FIGURES and not figures)
                                if value is not None)
                if len(label) >= LEARNED_LABEL_MINIMUM_CHARACTERS:
                    labels.setdefault(label, set()).add(page)
    min_label_pages = 2 if (len(page_blocks) == 2 and stems) else LEARNED_LABEL_MINIMUM_PAGES
    min_region_pages = 2 if (len(page_blocks) == 2 and stems) else LEARNED_REGION_MINIMUM_PAGES
    learned = {label: len(pages) for label, pages in labels.items() if len(pages) >= min_label_pages}
    record["labels"] = dict(sorted(learned.items(), key=lambda kv: (-kv[1], kv[0])))
    families: dict[str, set[str]] = {}
    for label in learned:
        if label_skeleton(label) != label:
            families.setdefault(label_skeleton(label), set()).add(label)
    record["families"] = sorted(skeleton for skeleton, members in families.items() if len(members) >= 2)
    record["folio"] = {"observed": len(observed), "runs": folio_runs(observed)}
    by_side: dict[tuple[int, str], list[tuple[float, float, float, int]]] = {}
    for page, axis, lo, hi in anchored:
        by_side.setdefault((page % 2, axis), []).append(((lo + hi) / 2, lo, hi, page))
    for (parity, axis), items in sorted(by_side.items()):
        items.sort()
        clusters = [[items[0]]]
        for item in items[1:]:
            if item[0] - clusters[-1][-1][0] > LEARNED_REGION_GAP:
                clusters.append([])
            clusters[-1].append(item)
        for cluster in clusters:
            pages = {item[3] for item in cluster}
            if len(pages) >= min_region_pages:
                record["regions"].append({"parity": "odd" if parity else "even", "axis": axis,
                                          "span": [round(min(i[1] for i in cluster), 4),
                                                   round(max(i[2] for i in cluster), 4)],
                                          "pages": len(pages)})
    return record


def learned_band(blocks: Sequence[Mapping[str, Any]], learned: Mapping[str, Any] | None,
                 page: int) -> dict[int, str]:
    """Switch --learned-heads: engine A's blocks of scan PAGE in the running
    head's band, each with why: an anchor - its own text the learned head but
    for INSERTION_LIMIT characters (learned_leftover) - or a block in an
    anchor's column or row (head_band_blocks), or a short thin block set in
    the head's type size in a region where the book's anchors stand on pages
    of this parity ("region", which learned_head_block takes only for text
    holding a stem or a label: a page's own folio is known by its own
    anchor's column).  A region stands in only for the anchor engine A did
    not read: where one of the page's anchors stands in it, the band there is
    that anchor's column or row, and the region is not used.  Its span is the
    head's drift over the book - measured on a 488-page book, the odd pages'
    anchors over 18% of the page's width, where the head itself is 3% wide -
    and on a page whose head is read it takes in the body's columns beside
    the head at the head's type size (on that book's contents pages, the
    entries; made-up page in tests/check_learned_heads.py: a column beside
    the head reading a volume's name alone)."""
    if not learned or not learned.get("stems"):
        return {}
    glyph = learned.get("glyph") or {}
    thickness = learned.get("thickness") or {}
    band: dict[int, str] = {}

    def is_head(chars: str) -> bool:
        return learned_leftover(chars, learned, page)[0] <= INSERTION_LIMIT

    def share(block: Mapping[str, Any], region: Mapping[str, Any]) -> float:
        """How much of BLOCK's width (a column region) or height (a row
        region) is inside REGION's span."""
        x, y, w, h = (float(v) for v in block["box"])
        lo, hi = (x, x + w) if region["axis"] == "column" else (y, y + h)
        rlo, rhi = region["span"]
        return max(0.0, min(hi, rhi) - max(lo, rlo)) / (hi - lo) if hi > lo else 0.0

    anchors = []
    for k, role in head_band_blocks(blocks, learned["stems"], glyph, thickness, is_head).items():
        if role["role"] == "anchor":
            band[k] = f"anchor ({role['stem']})"
            anchors.append(k)
        else:
            _, _, aw, ah = blocks[role["anchor"]]["box"]
            band[k] = f"{'column' if abs(ah) >= abs(aw) else 'row'} of block {layout_index(blocks, role['anchor'])}"
    parity = "odd" if page % 2 else "even"
    regions = [region for region in learned.get("regions") or [] if region["parity"] == parity
               and not any(share(blocks[a], region) >= 0.5 for a in anchors)]
    for k, block in enumerate(blocks):
        if k in band or not short_thin(block):
            continue
        chars = CJK(str(block.get("text") or ""))
        if not chars:
            continue
        for region in regions:
            if not any(head_size_fits(block, len(chars), glyph.get(stem), thickness.get(stem), region["axis"])
                       for stem in learned["stems"]):
                continue
            if share(block, region) >= 0.5:
                rlo, rhi = region["span"]
                band[k] = f"region ({parity} pages' {region['axis']} {rlo:.3f}-{rhi:.3f})"
                break
    return band


def learned_leftover(text: str, learned: Mapping[str, Any] | None, page: int | None = None
                     ) -> tuple[int, dict[str, list[str]]]:
    """Switch --learned-heads: how many of TEXT's characters the learned
    running head leaves unexplained, and what explains the rest: each
    stretch a stem matches (stem_windows, the likest first), each learned
    label and each label of a learned family (a numeral of one to
    FOLIO_MAX_FIGURES figures where the family has one), and each run of up to
    FOLIO_MAX_FIGURES numerals touching one of those, or making up the whole
    text, or read in the folio the learned sequence prints on scan PAGE
    (numeral_forms, expected_folios)."""
    chars = CJK(text)
    if not learned or not chars:
        return len(chars), {"stems": [], "labels": [], "folio": []}
    windows = sorted(((ratio, -start, length) for stem in learned.get("stems") or []
                      for ratio, start, length in stem_windows(chars, stem)), reverse=True)
    patterns = [re.escape(label) for label in sorted(learned.get("labels") or {}, key=len, reverse=True)]
    patterns += [re.escape(skeleton).replace("N", NUMERAL_RUN + "{1,%d}" % FOLIO_MAX_FIGURES)
                 for skeleton in learned.get("families") or []]
    forms = {form for value in (expected_folios(learned, page) if page is not None else ())
             for form in numeral_forms(value)}

    def explain(first: int | None) -> tuple[int, float, dict[str, list[str]]]:
        """The stretches explained taking stem window FIRST before the others
        (greedy, the likest first), then the labels, then the folio."""
        covered = bytearray(len(chars))
        found: dict[str, list[str]] = {"stems": [], "labels": [], "folio": []}
        likeness = 0.0
        for ratio, start, length in ([windows[first]] if first is not None else []) + windows:
            start = -start
            if not any(covered[start:start + length]):
                covered[start:start + length] = b"\1" * length
                found["stems"].append(chars[start:start + length])
                likeness += ratio
        for pattern in patterns:
            for match in re.finditer(pattern, chars):
                if not any(covered[match.start():match.end()]):
                    covered[match.start():match.end()] = b"\1" * (match.end() - match.start())
                    found["labels"].append(match.group())
        for match in re.finditer(NUMERAL_RUN + "+", chars):
            lo, hi = match.span()
            if any(covered[lo:hi]) or hi - lo > FOLIO_MAX_FIGURES:
                continue
            touches = (lo > 0 and covered[lo - 1]) or (hi < len(chars) and covered[hi])
            if touches or (lo == 0 and hi == len(chars)) or any(match.group() in form for form in forms):
                covered[lo:hi] = b"\1" * (hi - lo)
                found["folio"].append(match.group())
        return len(chars) - sum(covered), likeness, found

    # A stem window one character longer or shorter than the stem can take a
    # character of the label beside it, or leave out a misread one of its
    # own: each window is tried first, and the reading that leaves the fewest
    # characters unexplained stands (the likest, between equals).
    left, _, found = min((explain(first) for first in [None, *range(len(windows))]),
                         key=lambda result: (result[0], -result[1]))
    return left, found


def learned_record(arguments: Any) -> dict[str, Any] | None:
    """Switch --learned-heads: the learned running head (learn_heads), when
    the switch is on and the book has one."""
    learned = getattr(arguments, "learned", None) if getattr(arguments, "learned_heads", False) else None
    return learned if learned and learned.get("stems") else None


def learned_heads_seal(arguments: Any) -> str:
    """What provenance seals of switch --learned-heads: the hash of the
    learned running head the page was decided with."""
    return sha256_bytes(canonical_json_bytes(getattr(arguments, "learned", None) or {}))


def learned_head_block(span: str, why: str | None, learned: Mapping[str, Any] | None,
                       page: int, own: str | None = None) -> dict[str, Any] | None:
    """Switch --learned-heads: the evidence that a block in scan PAGE's
    learned band (WHY, learned_band) whose merged text is SPAN is the running
    head, else None: every character of SPAN the learned head's
    (learned_leftover) - a stem, a label, the folio beside them - or SPAN
    numerals alone (the folio) in an anchor's column or row.  Or OWN, engine
    A's reading of the block, is all the learned head and SPAN is what is
    left of it, character for character in order (a subsequence): the merge
    left some of the head out - measured on the offline replay of a book's
    sealed pages, with a trial since removed, the first character of a
    volume label was written as nothing and the two left of it were no
    label, and stayed.  A block in the band only by a learned region must hold a stem or
    a label: numerals there may be the body's (a list number, a date) on a
    page whose anchor engine A did not read."""
    chars = CJK(span)
    if not chars or why is None:
        return None
    left, found = learned_leftover(chars, learned, page)
    read = CJK(own or "")
    if left and read and read != chars:
        rest = iter(read)
        if all(c in rest for c in chars):
            left, found = learned_leftover(read, learned, page)
            found = dict(found, engineA=[read]) if not left else found
    if left:
        return None
    if why.startswith("region") and not (found["stems"] or found["labels"]):
        return None
    return {"band": why, **{key: value for key, value in found.items() if value}}


def _is_furniture(run: str, furniture: frozenset[str] | set[str],
                  minimum: int, at_edge: bool = False) -> bool:
    """Whether a deleted run is a running head or folio rather than text.

    The test is a CONTIGUOUS recurring stretch, not a fraction of the whole,
    because a running head is a book title plus a chapter title: a provincial finance report第三
    編省稅 recurs across the whole book while 第八章保險稅 recurs only inside one
    chapter, so only half its n-grams are book-wide and a fraction test misses
    it.  What is left over must itself be too short to be a lost sentence,
    otherwise a deletion of real text WITH a running head attached would be
    waved through.
    """
    if len(run) < FURNITURE_GRAM:
        return False
    # Union coverage, not a contiguous stretch: the running head is itself OCR'd
    # differently from page to page (a provincial finance report / 記實, 第三編 / 第三輯), and a
    # mutation in the MIDDLE splits a contiguous match into two halves that are
    # each too short.  Measured on a fiscal report: the 記 variant survived the
    # contiguous test because the damage was near the start, the 輯 variant did
    # not because it was in the middle.  What is left uncovered must still be
    # too short to be a lost sentence.
    covered = bytearray(len(run))
    for index in range(len(run) - FURNITURE_GRAM + 1):
        if run[index:index + FURNITURE_GRAM] in furniture:
            for offset in range(index, index + FURNITURE_GRAM):
                covered[offset] = 1
    hits = sum(covered)
    if hits < FURNITURE_GRAM:
        return False
    if (len(run) - hits) < minimum:
        return True
    # A run at the page edge that is anchored by the book's own recurring head
    # is the running head, even when it trails a section title unique to this
    # page: a fiscal report prints [book][chapter][section][folio] up there, and
    # only the first half recurs.  Away from the edge the same leftover would be
    # a lost sentence, so the position is doing real work here.
    return at_edge


def folio_figures(chars: str, at: int, step: int) -> list[int]:
    """Where the figures of a folio printed beside a running head stand in
    CHARS, reading from AT away from the head (STEP -1 before it, 1 after
    it): the numerals in a row, however many, passing over one character
    that is not a numeral - a figure misread, not returned, so it is still
    counted as unexplained - when figures stand beyond it and the folio has
    two or more.  Measured on a book's drafts: engine B wrote 口 (once 囗)
    for the folio's 〇 beside the head on 39 of its 344 pages, as the last
    figure next to the head or the middle one (engine A: on none); a
    character with one figure or none beyond it is not taken for a figure
    misread."""
    figures: list[int] = []
    misread = None
    while 0 <= at < len(chars):
        if chars[at] in NUMERAL_CHARS:
            figures.append(at)
        elif misread is None:
            misread = at
        else:
            break
        at += step
    if misread is not None and (len(figures) < 2 or not any((at - misread) * step > 0 for at in figures)):
        figures = [at for at in figures if (at - misread) * step < 0]
    return figures


def furniture_leftover(text: str, furniture: frozenset[str] | set[str], heads: Sequence[str],
                       span: tuple[int, int] | None = None) -> int:
    """How many of TEXT's characters the book's furniture leaves unexplained:
    every stretch one of its measured running heads matches (is_running_head),
    with the folio printed beside it - the numerals touching that stretch on
    either side, however many - and every recurring n-gram (furniture_grams)
    is taken out, and what is left is counted: a figure misread, or text.

    SPAN (start, end) counts only those of TEXT's characters: a reading where
    its engine read it, TEXT being that engine's reading of the page, so a
    head or a recurring stretch the reading only touches explains it as well.
    The alignment, or a run placed once (D2), leaves a head in another
    disagreement than its folio.

    The folio is not counted against a length: past its 99th page a book
    prints three figures, and counting them as leftover (two were allowed)
    kept every such head and folio one engine read, written into the body
    with a deletion-refused doubt.  Measured on a book whose engine B read
    its head and folio where engine A read nothing there (every sealed 裁決
    answer at such a spot was 無, 9 runs of 9): in a stand-in run of the
    whole book, 裁決 answering 無 there, 45 of the 55 pages with that doubt
    lose it: the head and a three-figure folio, or the folio alone beside
    the head in engine B's reading (one, a piece of the head alone).  A
    figure misread (engine B's 口 for 〇) is left over, and the allowance for
    leftover takes it - as it takes any character or two beside the
    furniture, which an answer may leave out anywhere (ANSWER_DELETION_LIMIT;
    measured once in that run: a character engine A did not read before the
    folio, at the foot of the page's last column)."""
    chars = CJK(text)
    lo, hi = span if span is not None else (0, len(chars))
    if span is not None:
        # Only what can reach the span: a head its folio's figures lead to, a
        # head or an n-gram overlapping it.
        reach = max([FURNITURE_GRAM, *(len(CJK(head)) for head in heads)])
        left = min([lo, *folio_figures(chars, lo - 1, -1)])
        right = max([hi - 1, *folio_figures(chars, hi, 1)]) + 1
        cut = max(0, left - 1 - reach)
        chars, lo, hi = chars[cut:right + 1 + reach], lo - cut, hi - cut
    covered = bytearray(len(chars))
    for head in heads:
        head = CJK(head)
        # The stretches most like the head, when is_running_head takes them for
        # it: an exact copy, or else the misread one - the best of the
        # stretches that overlap, so a copy one character off, which takes in
        # a character of the text beside the head, is not the head.
        found = [(difflib.SequenceMatcher(None, chars[start:start + len(head)], head, autojunk=False).ratio(),
                  start) for start in (range(len(chars) - len(head) + 1) if head else ())
                 if is_running_head(chars[start:start + len(head)], [head])]
        for ratio, start in found:
            if any(other > ratio and abs(where - start) < len(head) for other, where in found):
                continue
            covered[start:start + len(head)] = b"\1" * len(head)
            for step in (-1, 1):
                for at in folio_figures(chars, start - 1 if step < 0 else start + len(head), step):
                    covered[at] = 1
    for start in range(len(chars) - FURNITURE_GRAM + 1):
        if chars[start:start + FURNITURE_GRAM] in furniture:
            covered[start:start + FURNITURE_GRAM] = b"\1" * FURNITURE_GRAM
    return (hi - lo) - sum(covered[lo:hi])


# The characters of the merged text a stretch of an engine's reading is looked
# for in, as a moved copy (readings_dropped): three, so one character misread in
# the copy does not hide a stretch that moved.
DROP_GRAM = 3


def readings_dropped(readings: Mapping[str, str], merged: str,
                     furniture: frozenset[str] | set[str] = frozenset(), heads: Sequence[str] = (),
                     marks: Mapping[str, Collection[str]] | None = None,
                     explained: Mapping[str, Collection[tuple[int, int]]] | None = None) -> list[dict[str, Any]]:
    """Each place where the merge dropped more than ANSWER_DELETION_LIMIT
    characters one engine read (READINGS: engine -> its reading as merged)
    that the merged text holds nowhere: the user's fix of 2026-09-27, a doubt
    whenever any rule drops three characters or more.

    Measured: a line engine A skipped was fused with the heading beside it and
    the preference wrote engine A's one character for the 18 engine B read -
    17 printed characters gone, with nothing to say so (engineCoverage recorded
    merged 1392 < engine A 1395 < engine B 1411, and nothing read it;
    droppedRuns compares the merged text with the formatted page only).

    Net: each engine's reading is aligned with the merged text (both
    variant-folded), and where it has more than ANSWER_DELETION_LIMIT
    characters more than the merged text there, a character of it is dropped
    only when the merged text holds fewer of that character than the reading
    does, all over the page, and no DROP_GRAM characters around it stand in
    the merged text as often as in the reading.  So text written once in
    another place (D2, an order conflict's other reading), a table header one
    engine read by rows and the other by columns are not dropped; nor is a
    substitution (as many characters written as read).  Measured on the
    round-2 benchmark replay: with the n-gram test alone, a header read by
    rows (6 characters) and an inscription both engines misread, each
    differently (6 characters with the title beside it, two of them
    misread), were reported dropped.  As often, not merely anywhere (the
    round-3 review): a list item only engine B read, fused with engine A's
    folio and settled by the preference for engine A, worded like the item
    beside it (the same nine characters but two) had every stretch but
    those two characters' somewhere in the merged text - in the other item -
    and 9 printed characters went with no doubt.  But where every character
    dropped so stands in the merged text inside a DROP_GRAM-character
    stretch, only fewer times, the stretch is a second copy of text written
    there - the engine read it once more - and is not reported: measured on
    the benchmark replay, engine A read the notes of a textbook table twice
    (in another order, and part of them a third time), engine B once, the
    page wrote them once, and counting alone reported engine A's other copy
    (38 to 77 characters, every run); and a line of signatures engine A read
    twice would be reported where the preference for engine B wrote it once,
    as printed.  The parallel list item has characters in no stretch of the
    merged text (the two it words differently), and is reported whole.
    Which of two copies the print has, where the page prints a line twice
    and one engine read it once, the texts cannot say either way.

    Not dropped either: the book's furniture (a place its running head, the
    folio beside it and its recurring n-grams explain but for
    INSERTION_LIMIT, furniture_leftover, where the engine read it); the glyphs
    this book's drafts show that engine writing for printed marks (MARKS,
    mark_aliases); an engine's reading of the page that loops (the order
    conflict's test, unsupported_reading - measured: engine B read a table
    header some 30 times over, 1,661 characters at 0.05 distinct 4-grams, and
    every copy was reported); and EXPLAINED (engine -> spans of its reading), what a
    rule that reads it as no text of the page left out - furniture only one
    engine read (D15, a split's surplus), a table's furniture row, a bracket, a
    mark or a lone circle read as a character - or what D16 let 裁決 answer
    away because the merged text holds it nearby (reading_nearby).  Measured on
    the same replay: brackets engine B read as numerals around a figure,
    which the bracket rule took for brackets, were reported dropped."""
    folded = variant_fold(CJK(merged))
    grams = gram_counts(folded)
    held_counts = Counter(folded)
    out: list[dict[str, Any]] = []
    for engine, text in readings.items():
        chars = CJK(text or "")
        if len(chars) > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars) < DEGENERATE_NGRAM_RATIO:
            continue
        mine = variant_fold(chars)
        own = gram_counts(mine)
        glyphs = set((marks or {}).get(engine) or ())
        # How many of each character the reading has beyond the merged text.
        excess = Counter(mine)
        excess.subtract(held_counts)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mine, folded, autojunk=False).get_opcodes():
            if tag == "equal" or (i2 - i1) - (j2 - j1) <= ANSWER_DELETION_LIMIT:
                continue
            held = bytearray(i2 - i1)
            for k in range(max(0, i1 - DROP_GRAM + 1), min(i2, len(mine) - DROP_GRAM + 1)):
                if grams[mine[k:k + DROP_GRAM]] >= own[mine[k:k + DROP_GRAM]]:
                    for at in range(max(k, i1), min(k + DROP_GRAM, i2)):
                        held[at - i1] = 1
            for lo, hi in (explained or {}).get(engine) or ():
                for at in range(max(lo, i1), min(hi, i2)):
                    held[at - i1] = 1
            lost = []
            for k in range(i1, i2):
                if not held[k - i1] and excess[mine[k]] > 0:
                    excess[mine[k]] -= 1
                    lost.append(k)
            if len(lost) <= ANSWER_DELETION_LIMIT or {chars[k] for k in lost} <= glyphs:
                continue
            lo, hi = lost[0], lost[-1] + 1
            if furniture_leftover(chars, furniture, heads, (lo, hi)) <= INSERTION_LIMIT:
                continue
            if all(any(grams[mine[j:j + DROP_GRAM]] for j in range(max(0, k - DROP_GRAM + 1),
                                                                  min(k, len(mine) - DROP_GRAM) + 1))
                   for k in lost):
                # Each character of it stands in the merged text with the
                # characters around it, only fewer times: a second copy of text
                # written there, which the engine read once more.
                continue
            if hi - lo > UNCORROBORATED_MINIMUM and distinct_ngram_ratio(chars[lo:hi]) < DEGENERATE_NGRAM_RATIO:
                continue
            out.append({"engine": engine, "text": chars[lo:hi], "characters": len(lost), "at": lo,
                        "contextBefore": chars[max(0, lo - 8):lo], "contextAfter": chars[hi:hi + 8]})
    return out


def deleted_runs(resolved: str, formatted: str,
                 minimum: int = DELETED_RUN_LIMIT,
                 furniture: frozenset[str] | set[str] = frozenset(),
                 margin: Sequence[str] = (),
                 known: Sequence[str] = (),
                 whole: str | None = None,
                 prose_only: bool = False) -> list[str]:
    """Long stretches of the merged text the formatter dropped.

    A run built mostly from strings that recur across the book is furniture the
    formatter was asked to remove, so it is not reported.

    Nor is a run the formatter moved.  The sequence diff reports text written
    elsewhere on the page as a deletion (and an insertion), and moving text is
    the formatter's job - verify_characters counts characters for that very
    reason.  So a run counts as deleted only when at least MINIMUM of its
    characters are missing from the output altogether: counted against WHOLE
    (the whole merged text, table blocks included, when RESOLVED is only its
    prose - the output holds the tables too, and their characters must not
    stand in for prose that is gone).  Measured: a colophon engine A read in
    another column order, written right by the formatter in four runs out of
    four, was refused each time for "deleting" a 21-character run - the
    answers lacked no character at all - and the page fell back to engine A's
    order (31 errors).  The nine other such refusals sealed in the corpus
    (pages of a year table formatted whole) lacked 17-18 characters each,
    every one of them the running head, which is furniture; the runs they were
    refused for were table rows of figures written in another order.  A run the formatter
    really dropped lacks every one of its characters, and is reported.

    With PROSE_ONLY (RESOLVED is the prose blocks' text, the tables' kept
    apart) the sequence diff is taken against the output with its tables
    left out (table_spans): the prose is compared with the prose.  A table holds
    characters the prose around it holds too, and the diff matched them one
    by one, cutting a dropped run into pieces shorter than MINIMUM.  Measured
    on a textbook page: the structure pass left out the table's 20-character
    header column, a block of prose beside the rebuilt body, and the diff
    against the page with the table matched five of its characters in the
    table's cells - the longest piece left was 4 characters, and the page
    was sealed with the header gone and nothing saying so.  The
    multiset test still counts the whole output, tables included, so text
    the formatter moved into a table lacks nothing and is not reported.
    """
    src, out = CJK(resolved), CJK(formatted)
    # What the output lacks, as a multiset: a moved run lacks nothing.
    lacking = Counter(CJK(whole) if whole is not None else src) - Counter(out)
    if sum(lacking.values()) < minimum:
        return []
    beside = formatted
    for start, end in (reversed(table_spans(formatted)) if prose_only else ()):
        beside = beside[:start] + "\n" + beside[end:]
    matcher = difflib.SequenceMatcher(None, src, CJK(beside), autojunk=False)
    runs = []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "delete" or (i2 - i1) < minimum:
            continue
        run = src[i1:i2]
        if sum((Counter(run) & lacking).values()) < minimum:
            # Its characters stand elsewhere in the output: moved, not dropped.
            continue
        if known:
            # Furniture the book's profile measured (running heads repeated on
            # the page or across the book): a run made of them, with nothing
            # sentence-sized left over, was removed on purpose.
            rest = run
            for text in sorted(known, key=len, reverse=True):
                if text:
                    rest = rest.replace(text, "")
            if len(rest) < minimum:
                continue
        at_edge = i1 <= FURNITURE_EDGE or i2 >= len(src) - FURNITURE_EDGE
        if furniture and _is_furniture(run, furniture, minimum, at_edge):
            continue
        # No book to learn recurrence from, or a head this book prints only
        # once: fall back to the page's own margin blocks.
        if margin and any(
                difflib.SequenceMatcher(None, run, text, autojunk=False).ratio() >= 0.85
                for text in margin):
            continue
        runs.append(run)
    return runs


def punctuation_doubts(passes: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Stretches whose kept marks disagree with what the page prints.

    Every pass records the census (the marks the model counted on the crop)
    and the marks the page kept there, and the page used to be sealed clean
    whatever they said.  Measured over the 546 sealed passes on 74 pages:

    - A refused pass keeps its characters and drops every mark: 22 passes on
      15 pages, every one after a census had counted marks there, 14 of the
      pages sealed clean.  A census of 0 never reaches a refusal - the pass
      returns early - so a refusal is a stretch left bare, unless the census
      is unknown and nothing suggests marks: no other pass on the page wrote
      or counted any, and the refused output itself had none.  A refused pass
      that kept the marks a refused answer and engine A agree on
      (agreed_punctuation, `partial`) is reported partial, whatever the census:
      the marks only one of them saw are missing.
    - A retry is accepted on the character check alone; the census that
      refused the first answer is not asked again, so the retry may keep any
      number of marks, none included.  12 of the 53 passes accepted after a
      retry kept a count outside the census tolerance, 4 under and 8 over.
      Neither reading is the print by default.  Three of the twelve lie
      where a golden covers the stretch: in the under-count the kept text was
      wrong (census 117, golden 118, kept 63), in both over-counts the census
      was (it counted a doubled circle as one then: kept 26 and 14, golden 25
      and 14).  A third over-count matches its census exactly with each
      doubled circle as one, and its crop prints doubled circles.  So the
      doubt says the two readings disagree, with the doubled circles kept,
      and a person looks at the crop.
    - The shape re-check re-reads a whole block and is accepted on the
      character check alone too.  Its marks are counted per pass (range), so
      a refused tile it left bare stays bare here.  Seals older than ranges
      carry the block's total on every pass; those are judged once per block,
      against the block's summed census.

    The tolerance is the one the first answer is refused on (census_tolerance),
    so the seal asks of the kept text exactly what the pass asked of itself.
    It is applied in the census's own count (censusConvention): places, a
    doubled circle one, for the census that reports doubled circles apart;
    glyphs for the older one; either, for a seal that does not say.  Where the
    census counted doubled circles, kept doubled circles outside the tolerance
    of that count are a doubt of their own (doubled-count-mismatch).
    """
    def offered(entry: Mapping[str, Any]) -> bool:
        return any(punctuation_counts(text) for text in entry.get("refusedOutputs") or [])

    units = []
    legacy: dict[Any, list[Mapping[str, Any]]] = {}
    for entry in passes:
        if entry.get("shapeRecheck") and "range" not in entry:
            if entry.get("block") not in legacy:
                units.append(("block", entry.get("block")))
            legacy.setdefault(entry.get("block"), []).append(entry)
        else:
            units.append(("pass", entry))
    doubts = []
    for shape, unit in units:
        group = legacy[unit] if shape == "block" else [unit]
        first = group[0]
        censuses = [entry.get("census") for entry in group]
        census = None if any(c is None for c in censuses) else sum(censuses)
        marks = first.get("marks") or 0
        refused = [entry for entry in group if not entry.get("ok")]
        rechecked = bool(first.get("shapeRecheck"))
        reason = "; ".join(sorted({entry.get("reason") or "" for entry in group} - {""}))
        where = f"block {first.get('block')}" + (
            f" tile {first['tile']}" if shape == "pass" and first.get("tile") is not None else "")
        others = [entry for entry in passes if not any(entry is mine for mine in group)]
        elsewhere = any((other.get("marks") or 0) > 0 or (other.get("census") or 0) > 0
                        for other in others)
        evidence = ("no census, but marks elsewhere on the page" if elsewhere
                    else "no census, but the refused output wrote marks"
                    if any(offered(entry) for entry in refused) else "")
        if refused and not rechecked:
            partial = sum((entry.get("partial") or {}).get("kept", 0) for entry in group)
            if partial:
                # Both answers refused; the stretch keeps only the marks a
                # refused answer and engine A agree on (agreed_punctuation).
                # Always a doubt: whatever else is printed there is missing.
                counted = f"census counted {census} marks" if census is not None else "no census"
                # Kept by agreed_punctuation (a refused answer and engine A) or
                # by agreed_readings (the two answers, each realigned).
                between = ("the two answers" if any((entry.get("partial") or {}).get("agreedWith")
                                                    for entry in group)
                           else "a refused answer and engine A")
                doubts.append({"kind": "punctuation-partial", "detail": (
                    f"PUNCTUATION PARTIAL: {where}: {counted}; both answers refused "
                    f"({reason or 'no reason recorded'}), kept only the {partial} mark(s) {between} "
                    "printed at the same place")})
                continue
            if census is None:
                if not evidence:
                    continue
                counted = evidence
            else:
                counted = f"census counted {census} marks"
            doubts.append({"kind": "punctuation-left-bare",
                           "detail": f"PUNCTUATION LEFT BARE: {where}: {counted}, none kept "
                                     f"(pass refused: {reason or 'no reason recorded'})"})
            continue
        kept_by = ("the shape re-check" if rechecked
                   else "a retry" if "(after retry)" in reason else "the pass")
        if census is None:
            # No count to hold the kept marks to; only a refused stretch the
            # re-check also left bare is known to be missing its marks.
            if refused and marks == 0 and evidence:
                doubts.append({"kind": "punctuation-left-bare", "detail": (
                    f"PUNCTUATION LEFT BARE: {where}: {evidence}; the pass was refused "
                    f"({reason}) and the shape re-check kept none here")})
            continue
        if census == 0:
            continue
        why = (f" (first answer refused: {reason.replace(' (after retry)', '')})"
               if kept_by == "a retry" else "")
        doubled = (first.get("doubled") or 0) if shape == "pass" else 0
        convention = first.get("censusConvention") if shape == "pass" else "glyphs"
        # The census counts places (a doubled circle is one, and so is a
        # bullet printed twice) since it reports doubled circles apart, glyphs
        # before.  The places kept are sealed as
        # the pass counted them (mark_places); a seal from before they were
        # sealed counts the doubled circles off.  A seal older than either
        # convention does not say which count its model made, and in 21-27 of
        # 41 doubled-circle passes per run it was places although asked for
        # glyphs: either within the tolerance is a match.
        places = first.get("places", marks - doubled) if shape == "pass" else marks - doubled
        off = {"places": [places], "glyphs": [marks]}.get(convention, [marks, places])
        if marks == 0:
            doubts.append({"kind": "punctuation-left-bare", "detail": (
                f"PUNCTUATION LEFT BARE: {where}: census counted {census} marks, "
                f"{kept_by} kept none{why}")})
        elif all(abs(count - census) > census_tolerance(census) for count in off):
            shown = (f"{marks} ({doubled} of them in doubled circles 。。)" if doubled else str(marks)) + (
                f", {places} places" if convention == "places" and places != marks else "")
            doubts.append({"kind": "punctuation-count-mismatch", "detail": (
                f"PUNCTUATION COUNT MISMATCH: {where}: census counted {census} "
                f"{'places' if convention == 'places' else 'marks'}, {kept_by} kept {shown}{why}")})
        counted_doubled = first.get("doubledCensus") if shape == "pass" else None
        if marks and counted_doubled is not None and (
                abs(doubled - counted_doubled) > census_tolerance(counted_doubled)):
            # The shape: 。 where 。。 is printed, or the reverse.  Kept as a
            # doubt whether or not the count was put to the punctuator
            # (doubled_shown): the two readings disagree and the crop decides.
            doubts.append({"kind": "doubled-count-mismatch", "detail": (
                f"DOUBLED CIRCLES MISMATCH: {where}: census counted {counted_doubled} doubled "
                f"circle(s) 。。 among {census} places, {kept_by} kept {doubled}{why}")})
    return doubts


# Line breaks and table cells: a run across them is two pieces of text that
# happen to be written next to each other, not something any engine read.
# Measured: of 41 sealed pages holding a character pair more often than either
# engine read it, 6 held it only across such a seam - table cells written in
# another order than the engines read them, a heading or a list item set on a
# line of its own.
TEXT_SEAMS = re.compile(r"\n|\||<[^>]+>")


def extra_copies(text: str, draft: str, draft2: str) -> list[dict[str, Any]]:
    """Text the page holds at more sites than either engine read it.

    The merge keeps what either engine read, so text the two engines place in
    different spots - a heading, a leaf label, a column read before its
    neighbour by one and after it by the other - aligns as a deletion plus an
    insertion, and both copies are kept.  The worst case measured is a
    186-character passage written twice while each engine read it once.

    Counting against the drafts is what separates this from a phrase the book
    really prints twice: both engines read that one twice.  The unit counted is
    a character pair, the shortest string that has an order; one character
    over-counted is an ordinary union of two readings.  A count alone still
    fires where readings are joined up - a character, clause or running head
    dropped between two words makes a pair the page holds twice - so each
    engine must also have left a copy of the run unread where it sits: more
    than half of the copy's characters unmatched when the page is aligned with
    that engine's reading.

    What text cannot tell is WHY each engine left a site unread.  One reading
    placed twice and two engines each missing a different printed copy (a
    running head on each leaf, a word printed ten times) leave identical
    drafts, so the result is a question for the image, never a reason to
    delete: each run is returned with the sites each engine left unread.  A
    site both engines left unread is the one unambiguous shape - no engine
    put the text there (one run on the corpus, a word written twice side by
    side).

    Measured over the 511 sealed merge pages of five books: 98 runs are held
    more often than either engine read them inside a line or cell; 82 runs on
    21 pages have a copy each engine left unread.  The page set is the same
    for any unread share from a half up to the whole copy; counting a copy
    with fewer than half its characters unmatched adds two pages that hold no
    duplication.  Of the 21, one is two engines missing different copies of
    a common word; the rest hold a passage, running heads, leaf labels,
    headings, roster entries, verse lines or table labels written twice.  An
    earlier test asked the unread stretch around each copy to be mostly text
    the engine read elsewhere (moved, not missed); at its cut of a half it
    dropped five real duplications (0.29 to 0.33) for the one union of misses
    (0.20), and no cut is evidence where the drafts are identical by
    construction, so it was removed.  Returns the runs longest first.
    """
    lines = [piece for piece in (CJK(part) for part in TEXT_SEAMS.split(text)) if piece]
    page, first, second = "".join(lines), CJK(draft), CJK(draft2)

    def pairs(value: str) -> Counter:
        return Counter(value[i:i + 2] for i in range(len(value) - 1))

    held = Counter()
    for line in lines:
        held.update(pairs(line))
    read = (pairs(first), pairs(second))
    # A pair of numerals says nothing about order: a handful of digits make so
    # few pairs that figures set in another order over-count them by chance.
    # In an offline mock a figure table written as one paragraph was confirmed
    # on numeral pairs alone; no duplication measured on the corpus consists of
    # figures only.
    surplus = {pair for pair, count in held.items()
               if count >= 2 and count > max(read[0][pair], read[1][pair]) >= 1
               and not set(pair) <= NUMERAL_CHARS}
    if not surplus:
        return []
    runs = set()
    for line in lines:
        start = end = -1
        for i in range(len(line) - 1):
            if line[i:i + 2] not in surplus:
                continue
            if i < end:
                end = i + 2
            else:
                if end > start >= 0:
                    runs.add(line[start:end])
                start, end = i, i + 2
        if end > start >= 0:
            runs.add(line[start:end])
    runs = {run for run in runs if page.count(run) >= 2
            and page.count(run) > max(first.count(run), second.count(run))}
    if not runs:
        return []

    def unread_mask(reading: str) -> bytearray:
        mask = bytearray([1]) * len(page)
        matcher = difflib.SequenceMatcher(None, page, reading, autojunk=False)
        for tag, i1, i2, _, _ in matcher.get_opcodes():
            if tag == "equal":
                mask[i1:i2] = bytes(i2 - i1)
        return mask

    unread = (unread_mask(first), unread_mask(second))
    found: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        sites = []
        for match in re.finditer(re.escape(run), page):
            o, e = match.start(), match.end()
            by = "".join(name for name, mask in zip("AB", unread) if sum(mask[o:e]) * 2 > len(run))
            if by:
                shown = run if len(run) <= 8 else run[:6] + "…"
                sites.append({"at": o, "unreadBy": by,
                              "context": f"{page[max(0, o - 4):o]}[{shown}]{page[e:e + 4]}"})
        if any("A" in site["unreadBy"] for site in sites) and any("B" in site["unreadBy"] for site in sites):
            found[run] = sites
    kept: list[str] = []
    for run in sorted(found, key=lambda run: (-len(run), page.find(run))):
        if not any(run in longer for longer in kept):
            kept.append(run)
    return [{"text": run, "page": page.count(run), "engineA": first.count(run),
             "engineB": second.count(run), "sites": found[run]} for run in kept]


def extra_copy_doubts(text: str, draft: str, draft2: str) -> list[dict[str, str]]:
    """extra_copies as a doubt: what was counted, and where to look."""
    runs = extra_copies(text, draft, draft2)
    if not runs:
        return []

    def sites(run: Mapping[str, Any]) -> str:
        shown = []
        for key, label in (("AB", "both engines"), ("A", "engine A"), ("B", "engine B")):
            at = [site["context"] for site in run["sites"] if site["unreadBy"] == key][:2]
            if at:
                shown.append(f"{label} left unread …{'…, …'.join(at)}…")
        return "; ".join(shown)

    both_unread = any(site["unreadBy"] == "AB" for run in runs for site in run["sites"])
    return [{"kind": "extra-copy", "detail": (
        "TEXT AT MORE SITES THAN EITHER ENGINE READ IT: "
        + " | ".join(f"{run['text'][:20]}{'…' if len(run['text']) > 20 else ''} "
                     f"({len(run['text'])} characters, {run['page']} times here, engine A "
                     f"{run['engineA']}, engine B {run['engineB']}; {sites(run)})"
                     for run in runs[:3])
        + (" - at a site both engines left unread, the text was put there from a reading elsewhere"
           if both_unread else "")
        + " - where each engine left a different site unread, one reading placed twice and "
          "two different misses look the same in text: check the image at those sites")}]


# Characters matched in all before a one-engine run counts as engine B's reading
# of text engine A read elsewhere (place_moved_once).  A pair is the unit of the
# evidence, as in extra_copies, but a pair alone also coincides - the one page
# of 21 where extra_copies found two engines each missing a different printed
# copy was a two-character word.  Three is the shortest real move measured: a
# colophon's three-character labels, read label by label by one engine and all
# labels first by the other.
MOVE_MINIMUM = 3


def place_moved_once(segments: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Text engine B read in another order, placed once, at engine A's site
    (decision D2).

    The merge keeps what either engine read, so text the two engines read in
    different places - a colophon engine A read labels first and engine B
    label by value, a heading, a running head - aligns as engine A's
    one-engine text in one place and engine B's in another, and both copies
    were kept (extra_copies found them afterwards, as a doubt).  Here each of
    engine B's one-engine insertions is matched against engine A's text that
    engine B did not read where it stands (delete and replace segments),
    pairs first: a stretch counts only through pairs the old merge would hold
    more often than either engine read them (extra_copies' test), at least
    MOVE_MINIMUM characters in all.  Then:

    - what matches becomes text both engines read, at engine A's site - the
      page follows engine A's layout blocks, so its place is the one kept;
    - what differs between the two copies there becomes an ordinary
      disagreement at that site (a price one engine read with 一 and the other
      with 二 is a numeral question for the crop), and engine B's characters
      between two matched stretches
      that face nothing of engine A's are engine B's alone, there;
    - what engine A never read (engine B's characters before the first or
      after the last match) stays at engine B's site, as before.

    A replacement whose engine-A side is taken this way gives up its engine-B
    side, which is tried the same way and otherwise kept as engine B's
    one-engine text where it was; one whose engine-A side would be left in two
    pieces is not touched, since its engine-B side could then belong to
    either.  Pairs of numerals prove nothing about order (extra_copies), so a
    run of figures alone never moves.  Nothing is dropped: every character
    either engine read is on the page once, agreed or in a disagreement.

    Returns the segments, each with its place in engine B's text (bStart),
    and one record per move; the input unchanged, with no bStart, when nothing
    moved.
    """
    a_text = "".join(seg["a"] for seg in segments)
    b_text = "".join(seg["b"] for seg in segments)
    starts, a_at, b_at = [], 0, 0
    for seg in segments:
        starts.append((a_at, b_at))
        a_at += len(seg["a"])
        b_at += len(seg["b"])
    # Engine A's characters engine B did not read where they stand: the
    # engine-A side of a delete or replace (a fused numeral run keeps its
    # own treatment).
    owner: list[int | None] = [None] * len(a_text)
    for k, seg in enumerate(segments):
        if seg["tag"] in ("delete", "replace") and not seg.get("fused") and not seg.get(LOOPED_READING):
            for p in range(starts[k][0], starts[k][0] + len(seg["a"])):
                owner[p] = k

    def pairs(text: str) -> Counter:
        return Counter(text[i:i + 2] for i in range(len(text) - 1))

    read_a, read_b = pairs(a_text), pairs(b_text)
    page = read_a + sum((pairs(seg["b"]) for seg in segments if seg["tag"] == "insert"), Counter())

    def surplus(pair: str) -> bool:
        return page[pair] >= 2 and page[pair] > max(read_a[pair], read_b[pair]) and not set(pair) <= NUMERAL_CHARS

    claims: dict[int, tuple[int, str, int, int]] = {}      # A position -> (run, kind, r_lo, r_hi)
    runs = [{"site": k, "text": seg["b"], "bStart": starts[k][1], "released": False}
            for k, seg in enumerate(segments)
            if seg["tag"] == "insert" and len(seg["b"]) >= 2 and not seg.get(LOOPED_READING)]

    def leftovers(k: int, taken: Mapping[int, Any]) -> int:
        a0 = starts[k][0]
        free = [p not in taken for p in range(a0, a0 + len(segments[k]["a"]))]
        return sum(1 for i, f in enumerate(free) if f and (i == 0 or not free[i - 1]))

    def attempt(number: int) -> bool:
        text = runs[number]["text"]
        masked = "".join(c if owner[p] is not None and p not in claims else "\0" for p, c in enumerate(a_text))
        blocks = [m for m in difflib.SequenceMatcher(None, text, masked, autojunk=False).get_matching_blocks()
                  if m.size]
        anchors = [m for m in blocks if m.size >= 2 and all(surplus(text[m.a + i:m.a + i + 2])
                                                             for i in range(m.size - 1))]
        if not anchors:
            return False
        # One site only: the longest matching stretch stands once among engine
        # A's text engine B did not read, or the run could belong to either
        # copy (a word a chart prints in two cells, both engines garbled).
        longest = max(anchors, key=lambda m: m.size)
        if masked.count(text[longest.a:longest.a + longest.size]) != 1:
            return False
        inside = [m for m in blocks if anchors[0].a <= m.a and m.a + m.size <= anchors[-1].a + anchors[-1].size]
        # A matching stretch one character away on either side, with at most
        # one of engine A's facing it, is the same copy read with one
        # character different (a label, then a figure's first digit read 一
        # by one engine and 二 by the other): it joins, and the difference
        # becomes the site's disagreement.
        outside = [m for m in blocks if m not in inside]
        for m in reversed([m for m in outside if m.a < inside[0].a]):
            gap_r = inside[0].a - (m.a + m.size)
            gap_a = sum(1 for p in range(m.b + m.size, inside[0].b) if masked[p] != "\0")
            if gap_r > 1 or gap_a > 1:
                break
            inside.insert(0, m)
        for m in [m for m in outside if m.a > inside[-1].a]:
            gap_r = m.a - (inside[-1].a + inside[-1].size)
            gap_a = sum(1 for p in range(inside[-1].b + inside[-1].size, m.b) if masked[p] != "\0")
            if gap_r > 1 or gap_a > 1:
                break
            inside.append(m)
        # The run is the text read elsewhere only if most of it matches, as a
        # copy counts as unread in extra_copies when most of it is: measured,
        # a heading engine A missed shared three of its ten characters with
        # the running head engine A read, and moving those three would have
        # cut them out of the heading.  On six books' merged pages 10 runs
        # matched half their characters or fewer: two headings of this kind,
        # garbled running heads, a scrambled list, a row of figures.
        matched = sum(m.size for m in inside)
        if matched < MOVE_MINIMUM or 2 * matched <= len(text):
            return False
        new: dict[int, tuple[int, str, int, int]] = {}
        inserts: list[tuple[int, int, int]] = []
        for m in inside:
            for i in range(m.size):
                new[m.b + i] = (number, "match", m.a + i, m.a + i + 1)
        for m1, m2 in zip(inside, inside[1:]):
            r_lo, r_hi = m1.a + m1.size, m2.a
            free = [p for p in range(m1.b + m1.size, m2.b) if masked[p] != "\0"]
            if free and r_hi > r_lo:
                if free[-1] - free[0] + 1 != len(free) or len({owner[p] for p in free}) != 1:
                    return False        # engine A's facing text is in two places
                for p in free:
                    new[p] = (number, "face", r_lo, r_hi)
            elif r_hi > r_lo:
                inserts.append((m1.b + m1.size, r_lo, r_hi))
        trial = {**claims, **new}
        if any(segments[k]["b"] and leftovers(k, trial) > 1 for k in {owner[p] for p in new}):
            return False
        claims.update(new)
        runs[number]["placement"] = {"inserts": inserts, "head": (0, inside[0].a),
                                     "tail": (inside[-1].a + inside[-1].size, len(text)),
                                     "matched": sum(m.size for m in inside),
                                     "at": (min(new), max(new) + 1)}
        return True

    released: set[int] = set()
    progress = True
    while progress:
        progress = False
        for number in range(len(runs)):
            if "placement" not in runs[number] and attempt(number):
                progress = True
        for k, seg in enumerate(segments):
            if (seg["tag"] == "replace" and seg["a"] and k not in released and owner[starts[k][0]] == k
                    and leftovers(k, claims) == 0):
                # Its engine-B side is now text only engine B read, as an
                # insertion is: counted so, and tried the same way.
                released.add(k)
                page.update(pairs(seg["b"]))
                runs.append({"site": k, "text": seg["b"], "bStart": starts[k][1], "released": True})
                progress = True
    if not any("placement" in run for run in runs):
        return list(segments), []

    def piece(tag: str, a: str, b: str, p: int, q: int, b_start: int, **extra: Any) -> dict[str, Any]:
        return {"tag": tag, "a": a, "b": b, "before": a_text[max(0, p - 8):p], "after": a_text[q:q + 8],
                "bStart": b_start, **extra}

    def left_at_site(run: Mapping[str, Any], p: int) -> list[dict[str, Any]]:
        """What of a run stays where engine B read it: all of it if unplaced."""
        text, spans = run["text"], ([run["placement"]["head"], run["placement"]["tail"]]
                                    if "placement" in run else [(0, len(run["text"]))])
        mark = {"movedRemainderOf": run["bStart"]} if "placement" in run else {}
        return [piece("insert", "", text[lo:hi], p, p, run["bStart"] + lo, **mark)
                for lo, hi in spans if hi > lo]

    inserts_at: dict[int, list[tuple[int, int, int]]] = {}
    for number, run in enumerate(runs):
        for gap, lo, hi in (run.get("placement") or {}).get("inserts", []):
            inserts_at.setdefault(gap, []).append((number, lo, hi))
    moved_sites = {run["site"]: run for run in runs if not run["released"] and "placement" in run}
    released_sites = {run["site"]: run for run in runs if run["released"]}
    out: list[dict[str, Any]] = []
    for k, seg in enumerate(segments):
        a0, b0 = starts[k]
        end = a0 + len(seg["a"])
        if k in moved_sites:
            out.extend(left_at_site(moved_sites[k], a0))
            continue
        if not any(p in claims for p in range(a0, end)):
            out.append(dict(seg, bStart=b0))
            continue
        own_b = "" if k in released_sites else seg["b"]
        if k in released_sites:
            out.extend(left_at_site(released_sites[k], a0))
        p = a0
        while p < end:
            if p not in claims:
                q = p
                while q < end and q not in claims:
                    q += 1
                out.append(piece("replace" if own_b else "delete", a_text[p:q], own_b, p, q, b0))
                own_b = ""
            else:
                number, kind, r_lo, r_hi = claims[p]
                run = runs[number]
                q = p + 1
                if kind == "match":
                    while q < end and claims.get(q, (None, None, None))[:2] == (number, "match") and \
                            claims[q][2] == r_lo + (q - p):
                        q += 1
                    out.append(piece("equal", a_text[p:q], a_text[p:q], p, q, run["bStart"] + r_lo,
                                     movedFrom=run["bStart"]))
                else:
                    while q < end and claims.get(q) == claims[p]:
                        q += 1
                    out.append(piece("replace", a_text[p:q], run["text"][r_lo:r_hi], p, q, run["bStart"] + r_lo,
                                     movedFrom=run["bStart"]))
            for number, lo, hi in inserts_at.get(q, []):
                run = runs[number]
                out.append(piece("insert", "", run["text"][lo:hi], q, q, run["bStart"] + lo,
                                 movedFrom=run["bStart"]))
            p = q
    moves = []
    for run in runs:
        if "placement" not in run:
            continue
        where = run["placement"]["at"]
        site = starts[run["site"]][0]
        moves.append({"text": run["text"], "engineBOffset": run["bStart"],
                      "readAt": {"engineAGap": site, "context": a_text[max(0, site - 8):site] + "|" + a_text[site:site + 8]},
                      "placedAt": {"engineA": list(where),
                                   "context": a_text[max(0, where[0] - 4):where[0]] + "["
                                   + a_text[where[0]:where[1]] + "]" + a_text[where[1]:where[1] + 4]},
                      "matched": run["placement"]["matched"],
                      "leftAtSite": "".join(run["text"][lo:hi] for lo, hi in (run["placement"]["head"],
                                                                              run["placement"]["tail"])),
                      "released": run["released"]})
    return out, moves


def whole_page_reason(blocks: Sequence[Mapping[str, Any]], draft: str, mode: str,
                      order_text: str | None = None) -> str:
    """Why a merged page could not be punctuated block by block (block_spans).

    ORDER_TEXT is the engine whose reading an order-conflict page takes whole:
    engine B's has no layout blocks at all, engine A's goes block by block
    unless its blocks fail it as a merged page's would.
    """
    if mode != "merge" and order_text != "A":
        return (f"the engines read the page in different orders and engine B's text was "
                f"taken whole, with no layout ({mode})")
    if not blocks:
        return "engine A gave no layout blocks"
    if sum(len(CJK(str(b["text"]))) for b in blocks) != len(CJK(draft)):
        return "engine A's layout blocks do not account for its text"
    return "a disagreement read across several layout blocks shares no character with engine A's reading"


def proofread_page(
    job: PageJob, client: Client, arguments: argparse.Namespace
) -> dict[str, Any]:
    with packet_page_context(job.scan_page, job.render, sha256_file(job.render)):
        return _proofread_page_inner(job, client, arguments)


def _proofread_page_inner(
    job: PageJob, client: Client, arguments: argparse.Namespace
) -> dict[str, Any]:
    started = time.monotonic()
    ledger: list[dict[str, Any]] = []
    _CALL_LEDGER.set(ledger)   # this page's thread; its worker threads get it via in_context
    # The calls with a rung the loop detector gave up, for decisionChanges.
    loop_cuts: list[dict[str, Any]] = []
    _LOOP_CUTS.set(loop_cuts)
    render_bytes = job.render.read_bytes()
    draft = job.draft_text.read_text(encoding="utf-8").strip()
    metrics = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    page_concurrency = max(1, getattr(arguments, "page_concurrency", DEFAULT_PAGE_CONCURRENCY) or 1)
    table_orientation_mode = getattr(arguments, "table_orientation", "auto") or "auto"
    recovery_packet_pending = False
    rebuild_packet_pending = False

    def add(m: Mapping[str, Any]) -> None:
        # Token counts only: a call's metrics also carry finish_reason and effort.
        for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens"):
            metrics[k] += m.get(k, 0) or 0

    draft2 = (strip_grounding(job.draft2_text.read_text(encoding="utf-8")).strip()
              if job.draft2_text is not None and job.draft2_text.is_file() else None)
    adjudications: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    punctuation_passes: list[dict[str, Any]] = []
    # Brackets the engines read that the punctuator left out (written in) or
    # put a character or two away (moved): see place_brackets.
    engine_brackets = {"inserted": 0, "moved": 0}
    profile_furniture: list[str] = []
    # Switch --learned-heads: the blocks left out as the learned running head
    # (learned_head_block), by position.
    learned_dropped: set[int] = set()
    variant_unruled: list[str] = []
    rejected_output: str | None = None
    # Why the formatter's first answer was refused, when it was (a retry followed).
    format_retry_reason: str | None = None
    # Why the retry was refused too, and what it wrote: before, only the first
    # answer and its reason were sealed, so a page that fell back could not be
    # audited for what the retry did (formatterRetryRefusal).
    retry_refusal: str | None = None
    rejected_retry: str | None = None
    # Every reason the page is not clean, {kind, detail}: auditorClean is "no
    # doubts" and auditorReport their details, so a run report can count kinds.
    doubts: list[dict[str, Any]] = []
    # Defined here because the single-engine path never reaches the merge branch
    # but the sealed record always reports them.
    tables: list[str] = []
    table_sources: list[int] = []
    table_notes: list[str] = []
    tables_missing: list[int] = []
    tables_disputed: list[int] = []
    table_characters: str = ""
    prose: str | None = None
    guarded: str | None = None
    known_heads: list[str] = []
    tables_rejected: list[dict[str, Any]] = []
    table_orientation: list[str] = []
    table_orientation_evidence: list[dict[str, Any]] = []
    source_table_mapping_current = True
    recovery_table_correspondence: list[dict[str, Any]] = []
    table_grouping: list[str] = []
    cell_direction: list[dict[str, Any]] = []
    table_nominations: list[dict[str, Any]] = []
    table_rebuilds: list[dict[str, Any]] = []
    # Switch --keep-arabic-numerals: engine A's figures the merge writes, with
    # their gaps in the merged text, and every figure of the page, kept or not
    # (arabic_figure_kept).
    figures_kept: list[dict[str, Any]] = []
    arabic_figures_seen: list[dict[str, Any]] = []
    # And the figures and marks engine A read in brackets (bracketed_pairs),
    # for the doubt where the page does not hold one.
    page_bracketed: list[tuple[int, str, str]] = []
    # Why the merge did not keep the figure at each gap of engine A's stream.
    figure_why: dict[int, str] = {}
    # An engine-A block whose reading was a loop, set aside (quarantine_looped_block).
    loops_dropped: list[dict[str, Any]] = []
    # Every text change made by a rule that depends on one of the user's
    # decisions: which decision and rule, where (layout block, engine A's
    # context, offset in the merged text), what it wrote there ("after") and
    # the evidence.  The run report counts them by decision.
    decision_changes: list[dict[str, Any]] = []
    # Each lopsided replacement split into its like-for-like part and text only
    # one engine read (split_lopsided), with what the merge wrote for it.
    lopsided: list[dict[str, Any]] = []
    # Places where the merge dropped text one engine read, which the merged text
    # holds nowhere (readings_dropped); and what rules that read it as no text of
    # the page left out (brackets and marks read as characters), which is not.
    readings_lost: list[dict[str, Any]] = []
    explained: dict[str, list[tuple[int, int]]] = {"A": [], "B": []}
    engine_a_characters = len(CJK(draft))
    engine_b_characters = len(CJK(draft2 or ""))

    # Every page first receives a full-image inventory, including pages for
    # which both CJK streams are empty.  Packet replay binds image and prompt;
    # no standalone old sidecar can authorize a blank or a content repair.
    inventory, inventory_metrics = _content_fidelity.inventory_page(
        client, render_bytes, [draft, draft2 or ""], arguments.max_tokens)
    add(inventory_metrics)
    content_recovery: dict[str, Any] | None = None
    coverage_review: dict[str, Any] | None = None

    def image_review(candidate: str, stage: str) -> tuple[str, dict[str, Any]]:
        result, evidence, used = _content_fidelity.review_page(
            client, render_bytes, candidate, inventory, arguments.max_tokens, stage=stage,
            table_orientation=table_orientation_mode)
        add(used)
        return result, evidence

    # A page carrying no text either engine can corroborate.  Two shapes:
    # genuinely blank (both engines silent) and unsupported (one engine emitted
    # a page of degenerate text the other never saw).  Both are sealed rather
    # than failed, so the assembled deliverable has no hole where a page should
    # be - three such pages failed the whole phase before this existed.
    unsupported = unsupported_reading(draft, draft2)
    if inventory["page_state"] == "blank" or (not CJK(draft) and not CJK(draft2 or "")) or unsupported:
        if unsupported:
            doubts.append({"kind": "unsupported-reading", "detail": (
                f"one engine returned {len(CJK(unsupported))} characters the other "
                f"never saw, and the text repeats itself "
                f"({distinct_ngram_ratio(CJK(unsupported)):.3f} distinct 4-grams); "
                "requires a full-image transcription rather than a blank shortcut")})
        writer_text = ""
        if inventory["page_state"] != "blank":
            writer_text, content_recovery = image_review(draft if not unsupported else "", "content-recovery")
            writer_text, coverage_review = image_review(writer_text, "coverage-review")
            if not coverage_review["complete"]:
                raise ProofreadError("full-page coverage unresolved: " + json.dumps(coverage_review, ensure_ascii=False))
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "scanPage": job.scan_page,
            "status": "complete",
            "textAuthority": TEXT_AUTHORITY,
            "generatedAt": utc_now(),
            "model": client.model,
            "reasoningEffort": client.effort,
            "renderFile": str(job.render),
            # An unsupported reading is not a clean page: a human should look.
            "auditorClean": not doubts,
            "mode": "blank-page" if inventory["page_state"] == "blank" else "image-recovery",
            "pageInventory": inventory,
            "contentRecovery": content_recovery,
            "coverageReview": coverage_review,
            "inkFraction": round(page_ink_fraction(job.render), 6),
            "engineDisagreements": [],
            "auditorReport": "; ".join(d["detail"] for d in doubts),
            "doubts": doubts,
            # Kept, not dropped: if this judgement is ever wrong, the text is
            # still here to recover.
            "discardedOnBlankPage": unsupported[:2000] if unsupported else "",
            "adjudications": [],
            "usage": metrics,
            "punctuationDensity": 0.0,
            "degraded": False,
            "seconds": round(time.monotonic() - started, 1),
            "calls": summarize_calls(ledger),
            "provenance": {
                "renderSha256": sha256_file(job.render),
                "draftSha256": sha256_file(job.draft_text),
                **decision_provenance(arguments),
                "markdownSha256": sha256_bytes(writer_text.encode("utf-8")),
            },
        }
        record["provenance"]["jsonPayloadSha256"] = sha256_bytes(
            canonical_json_bytes(record))
        seal_page(job, writer_text.encode("utf-8"), record)
        return record

    if draft2 is None:
        # Single-engine fallback: no second reading to merge against, so the
        # model must transcribe. Less accurate - measured 4/11 vs 11/11 for two
        # agreeing engines - but it is all that is available.
        writer_text, m = client.ask_answering(
            WRITER_SYSTEM,
            f"OCR 底稿（提示，非權威）：\n---\n{draft}\n---\n\n"
            "睇頁圖，輸出呢一頁嘅忠實 Markdown 轉錄。",
            render_bytes, arguments.max_tokens, kind="writer")
        add(m)
        writer_text = strip_fence(writer_text)
        if not writer_text:
            raise ProofreadError("writer returned empty output")
        resolved, mode = writer_text, "transcribe"
        writer_text, content_recovery = image_review(writer_text, "content-recovery")
    else:
        # MERGE. Where two independent engines agree, that reading is frozen and
        # no model is allowed to revise it. Only differences go to adjudication.
        mode = "merge"
        # On an order conflict, the engine whose reading the page takes whole.
        order_text: str | None = None
        blocks = load_blocks(job.draft_json)
        # One looped engine-A block is set aside rather than the whole of engine
        # A's page - only when what is left then merges; otherwise the page is
        # judged as it always was (order_judgement).
        # The engine preference that settles this page's disagreements.
        page_prefer = arguments.prefer_engine
        judged = order_judgement(blocks, draft, draft2, prefer=page_prefer)
        segments, agreement, quarantine = judged["segments"], judged["agreement"], judged["quarantine"]
        if quarantine is not None:
            blocks, draft = quarantine["blocks"], quarantine["draft"]
            dropped = quarantine["dropped"]
            loops_dropped.append(dropped)
            found = dropped["engineBReading"]
            doubts.append({"kind": "engine-a-loop-dropped", "detail": (
                f"ENGINE A LOOPED IN ONE BLOCK: block {dropped['block']} ({dropped['label']}, "
                f"{dropped['characters']} characters, {dropped['distinct4gram']:.3f} distinct 4-grams) "
                "repeats itself where engine B does not; its reading is set aside (engineALoopsDropped) "
                "and the region is "
                + (f"engine B's reading alone ({dropped['engineBCharacters']} characters)"
                   if found["found"] == "insertion" else
                   f"engine B's reading alone ({dropped['engineBCharacters']} characters), split off "
                   f"engine A's 「{found['sharedWith']}」, which the alignment had set against it and "
                   "which stays as engine A's own reading"
                   if found["found"] == "split" else
                   "left empty: nothing only engine B read shares the loop's 4-grams, so no reading "
                   "of the region was found")
                + " - check it against the image")})
        if agreement < READING_ORDER_AGREEMENT:
            # The engines disagree about READING ORDER, not just characters -
            # observed on a vertical title page where one engine scrambled the
            # columns. Character alignment is meaningless then and produces
            # spliced nonsense, so take the preferred engine's page whole.
            mode = "merge-order-conflict"
            # Take the preferred engine's page whole - but NEVER at the cost of
            # most of the page.  Measured on a fiscal report (NLC) p.24: HunyuanOCR
            # read 36 characters (the title and running head, missing the table
            # entirely) while PaddleOCR read 151 including a complete 19-row
            # table.  Preferring B discarded the table, and because the loss
            # happened at merge time rather than format time, droppedRuns saw
            # nothing and the page was sealed auditorClean.  A preference is for
            # choosing between two readings of the same page, not for choosing
            # a fragment over a page.
            order_engine = (arguments.prefer_engine if arguments.prefer_engine in ("A", "B")
                            else getattr(arguments, "fallback_engine", "A"))
            preferred = CJK(draft2 if order_engine == "B" else draft)
            other = CJK(draft if order_engine == "B" else draft2)
            # Length alone is not a quality signal in EITHER direction.  Too
            # short and the preferred engine read a fragment, not the page; too
            # long and it looped.  Measured on a multi-page fiscal report: p.24 HunyuanOCR
            # gave 36 characters against PaddleOCR's 157, while p.28 and p.29 it
            # gave 3,996 and 7,990 - 八八八八… and 〇〇〇〇… - against 339 and 308.
            # An earlier version guarded only the short side and then happily
            # took the flood.  Repetition is what tells them apart: genuine
            # readings here score 0.91-0.99 distinct 4-grams, the loops 0.007.
            preferred_looped = (len(preferred) > UNCORROBORATED_MINIMUM
                                and distinct_ngram_ratio(preferred) < DEGENERATE_NGRAM_RATIO)
            other_looped = (len(other) > UNCORROBORATED_MINIMUM
                            and distinct_ngram_ratio(other) < DEGENERATE_NGRAM_RATIO)
            # In an order conflict the whole page comes from one engine, so the
            # choice should favour the reading we can still structure.  Engine A
            # supplies the layout blocks, so when it holds a table that engine B
            # never saw, taking B throws that table away and the recovery then
            # cannot find its text in the merged page - which is what happened on
            # a multi-page fiscal report p.25, where the two readings were within five
            # characters of each other and only one carried the grid.
            table_text = CJK("".join(str(b.get("text") or "") for b in blocks
                                     if b.get("label") == "table"))
            structured_only_in_other = bool(table_text) and (
                order_engine == "B") and (
                _covers(other, table_text) and not _covers(preferred, table_text))

            other_engine = "A" if order_engine == "B" else "B"
            if preferred_looped and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back-degenerate"
            elif len(preferred) < len(other) * ORDER_CONFLICT_FLOOR and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back"
            elif structured_only_in_other and not other_looped:
                chosen, order_text, mode = other, other_engine, "merge-order-conflict-fell-back-structured"
            else:
                chosen, order_text = preferred, order_engine
            segments = [{"tag": "equal", "a": chosen,
                         "b": "", "before": "", "after": ""}]
        # Text engine B read in another order is placed once, at engine A's
        # site (decision D2, place_moved_once).
        moves: list[dict[str, Any]] = []
        # The segments as aligned, before any run moved: what both engines read
        # in each block, for D22's region (rebuild_region), and the stretch a
        # moved run's site entry covers (site_span).
        aligned = segments
        if mode == "merge":
            segments, moves = place_moved_once(segments)
        # A lopsided replacement - a line one engine skipped, fused with a
        # character or two read differently beside it - is split into its
        # like-for-like part and text only one engine read, before anything is
        # decided (split_lopsided; the user's fix of 2026-09-27): the surplus
        # goes the way an insertion goes.  After D2, which reads a moved run
        # against engine A's side of a replacement whole (a price's label read
        # in two places and its first figure read 一 and 二 - split first, the
        # figures became two questions instead of one).  Only where the
        # engine preference settles a replacement: on a page 裁決 reads every
        # disagreement off its crop (no preference) it sees
        # the replacement whole, and D16 keeps the longer reading when the
        # answer leaves most of it out.  Measured on the benchmark replay, the
        # split there made two or three questions of one and took a fold
        # title engine A read against a sentence only engine B read (answered
        # with one character, D16 kept the sentence) for text of its own - a
        # question 裁決 can only answer by keeping it, or by 無 that D16 then
        # refuses: the title would stand in the body either way.
        if mode == "merge" and page_prefer in ("A", "B"):
            split_lengths = [len(CJK(b["text"])) for b in blocks]
            segments, lopsided = split_lopsided_segments(
                segments, list(itertools.accumulate(split_lengths))
                if blocks and sum(split_lengths) == len(CJK(draft)) else None, draft, draft2 or "", page_prefer)
        pieces: list[str] = []
        # Block index and offset of each disagreement, straight from its position
        # in engine A's stream; text search at a block boundary was measured to
        # crop the running foot instead of a table's last figure and delete it.
        block_lengths = [len(CJK(b["text"])) for b in blocks]
        block_ends = list(itertools.accumulate(block_lengths))
        offsets_valid = bool(blocks) and sum(block_lengths) == len(CJK(draft))
        book_heads = sorted(set(profile_section(arguments, "layout").get("runningHeads") or []))
        book_furniture = getattr(arguments, "furniture", frozenset())
        table_work = offsets_valid
        # An order-conflict page that took engine A's reading takes a table
        # block of it that engine B contradicts from engine B's lines instead
        # (window_merge): engine A's copies and splices do not stand, and a
        # substitution inside still reaches 裁決.  Each such block's segments
        # carry "window" (its index), and ones decided without a question
        # "settled".  Not when engine B's reading looped, the same test the
        # order conflict applies above: its lines are no reading of the
        # block (measured: a year table read against a looped line came
        # out with a company name 85 times, 3,837 characters for engine A's
        # 357).
        windowed: dict[int, list[dict[str, Any]]] = {}
        witness_text = CJK(draft2)
        witness_looped = (len(witness_text) > UNCORROBORATED_MINIMUM
                          and distinct_ngram_ratio(witness_text) < DEGENERATE_NGRAM_RATIO)
        if (table_work and mode == "merge-order-conflict" and segments[0]["a"] == CJK(draft)
                and not witness_looped):
            for k, block in enumerate(blocks):
                if block.get("label") != "table":
                    continue
                grid, why = engine_table(str(block.get("text") or ""), draft2, book_heads, book_furniture)
                if grid is None and any(sign in why for sign in
                                        ("in another order", "both hold", "page furniture")):
                    # Engine A's reading of the rest of the page: where a line
                    # of engine B's runs on into another block (window_merge).
                    a_text, a_lo = CJK(draft), (block_ends[k - 1] if k else 0)
                    found = window_merge(block, draft2 or "", book_heads, book_furniture,
                                         elsewhere=a_text[:a_lo] + "\0" + a_text[block_ends[k]:])
                    if found:
                        windowed[k] = [dict(x, window=k) for x in found]
        if windowed:
            chosen_text, rebuilt, cursor = segments[0]["a"], [], 0
            for k, (lo, hi) in enumerate(zip([0] + block_ends[:-1], block_ends)):
                if k not in windowed:
                    continue
                if lo > cursor:
                    rebuilt.append({"tag": "equal", "a": chosen_text[cursor:lo], "b": "",
                                    "before": "", "after": ""})
                rebuilt.extend(windowed[k])
                cursor = hi
            if cursor < len(chosen_text):
                rebuilt.append({"tag": "equal", "a": chosen_text[cursor:], "b": "", "before": "", "after": ""})
            segments = rebuilt
        prefer = arguments.prefer_engine
        fallback = getattr(arguments, "fallback_engine", "A")
        # A printed circle one engine read as the numeral 〇 and the other not
        # at all is a mark, not a character (is_lone_circle); it is dropped
        # here, before any budget is set aside for numerals.
        # A circle fused with a neighbouring substitution (A 〇旂, B 旒) is first
        # split off (split_edge_circle), where it would be dropped.
        split: list[dict[str, Any]] = []
        a_at = 0
        for x in segments:
            # Engine B's reading of a set-aside block is kept whole, and a
            # window of an order-conflict table block (window_merge) keeps
            # its own segments.
            parts = (split_edge_circle(x) if not is_numeral_conflict(x) and not x.get(LOOPED_READING)
                     and "window" not in x else None)
            if parts:
                pieces_of, at = parts
                offset = a_at + sum(len(p["a"]) for p in pieces_of[:at])
                mark = pieces_of[at]
                if is_lone_circle(mark, locate_segment(blocks, block_ends, block_lengths,
                                                       offsets_valid, offset, mark)[0]):
                    if "bStart" in x:
                        pieces_of[0]["bStart"] = x["bStart"]
                        pieces_of[1]["bStart"] = x["bStart"] + len(pieces_of[0]["b"])
                    split.extend(pieces_of)
                    a_at += len(x["a"])
                    continue
            split.append(x)
            a_at += len(x["a"])
        segments = split
        if mode == "merge" and offsets_valid:
            # A stretch engine A read twice, the copy beside it standing for
            # it (read_twice), is not written, and nothing is asked about it:
            # engine B's side of a substitution goes the usual way.
            for i, k in reversed(read_twice(segments, blocks, block_ends)):
                x = segments[i]
                rest = ([{"tag": "insert", "a": "", "b": x["b"], "before": x["before"], "after": x["after"],
                          **({"bStart": x["bStart"]} if "bStart" in x else {})}] if x["b"] else [])
                copy = [dict(x, tag="delete", b="", settled=("", "engine-A-read-twice"))]
                # Engine B's side stands on the side of the stretch away from
                # the copy: where the copy follows (K == 0), before it - the
                # stretch is a second box on the next block's first line, and
                # what engine B read there ends the block before.
                segments[i:i + 1] = rest + copy if k == 0 else copy + rest
        starts = [0] + list(itertools.accumulate(len(x["a"]) for x in segments))[:-1]
        # Where each segment's engine-B text is in engine B's own: in segment
        # order, unless text read in another order was placed at engine A's
        # site, when each segment says (bStart).
        b_starts = ([x["bStart"] for x in segments] if segments and all("bStart" in x for x in segments)
                    else [0] + list(itertools.accumulate(len(x["b"]) for x in segments))[:-1])
        # Each engine's own marks, for the bracket rule: a numeral one engine
        # read where the other printed a bracket is that bracket, and the
        # brackets the engines read are written back after punctuation.
        bracket_rule = mode == "merge"
        stream_a, stream_b = engine_gaps(draft), engine_gaps(draft2)
        # And every printed mark, for the mark rule (decision D1): a character
        # one engine has in excess where the other printed a mark is that mark
        # misread, when the glyph is one this book's drafts show that engine
        # writing for marks (mark_explanation, mark_aliases).
        mark_rule = mode == "merge"
        marks_a, marks_b = engine_marks(draft), engine_marks(draft2)
        aliases = getattr(arguments, "mark_aliases", None) or {"A": {}, "B": {}}
        # Switch --keep-arabic-numerals: engine A's figures on this page
        # (arabic_figures, its ASCII dots before a figure a leader too) by
        # their gap in engine A's stream, each confirmed or not by the
        # numbering (figures_confirmed, with the book's figures before and
        # after the page) - a contents entry's page number (a figure ending a
        # line that holds a leader) needs none - and the glyphs engine B
        # writes for them or for marks on this book (figure_aliases,
        # mark_aliases).
        figure_rule = getattr(arguments, "keep_arabic_numerals", False) and mode == "merge" and offsets_valid
        page_figures: dict[int, list[dict[str, Any]]] = {}
        figure_glyphs: set[str] = set()
        if figure_rule:
            found_figures = arabic_figures(blocks, draft, dotted=True)
            for figure, confirmed in zip(found_figures, figures_confirmed(
                    [f["value"] for f in found_figures],
                    *figure_neighbours((getattr(arguments, "figure_evidence", None) or {}).get("numbering") or {},
                                       job.scan_page))):
                page_figures.setdefault(figure["gap"], []).append(
                    dict(figure, confirmed=confirmed or figure["leader"]))
            figure_glyphs = set(aliases.get("B") or {}) | set(getattr(arguments, "figure_aliases", None) or {})
            page_bracketed = bracketed_pairs(blocks)

        def figure_at(gap: int) -> dict[str, Any] | None:
            """The confirmed figure engine A read at GAP of its stream, alone there."""
            here = page_figures.get(gap) or []
            return here[0] if len(here) == 1 and here[0]["confirmed"] else None

        # Each segment's reading once the bracket and mark rules took out what
        # engine A's brackets and marks account for (a figure's neighbours are
        # like for like, arabic_figure_kept); each insertion at a figure's gap
        # the figure settles: how (marks, stand-in, verdict), and whether the
        # merge would write characters there without the figure (a stand-in's
        # question not asked: yes).
        after_rules: dict[int, Mapping[str, Any]] = {}
        figure_settled: dict[int, tuple[str, bool]] = {}
        # Questions the switch did not ask (the stand-ins of a figure): each
        # takes the budget it would have taken asked.
        figure_taken = 0

        def to_judge(seg: Mapping[str, Any], a_start: int, b_start: int) -> bool:
            """Whether the walk below would ask 裁決 about SEG, its budget aside:
            not where a variant ruling or the preference settles it (a numeral
            or rare-codepoint conflict and a short insertion D3 asks about go
            to 裁決 under a preference too)."""
            numeral_or_rare = is_numeral_conflict(dict(seg)) or is_rare_codepoint_conflict(dict(seg))
            if not numeral_or_rare and variant_segment(seg) is not None:
                return False
            return not (prefer in ("A", "B") and not numeral_or_rare and not short_insertion(seg, a_start, b_start))

        # Decision D3: a short insertion only one engine read is asked of 裁決.
        insertion_rule = mode == "merge"
        page_heads = sorted(set(profile_section(arguments, "layout").get("runningHeads") or []))
        page_furniture = getattr(arguments, "furniture", frozenset())

        def short_insertion(seg: Mapping[str, Any], a_start: int, b_start: int) -> bool:
            """Decision D3: text of INSERTION_LIMIT characters or fewer that one
            engine read and the other did not, not explained by the mark or the
            bracket rule (those settled it before this is asked) nor by the
            book's furniture - a piece of a running head one engine read and
            the other left out, which the formatter removes as furniture."""
            if not insertion_rule or bool(seg["a"]) == bool(seg["b"]):
                return False
            own = CJK(seg["a"] or seg["b"])
            if not own or len(own) > INSERTION_LIMIT:
                return False
            chars, at = (stream_a[0], a_start) if seg["a"] else (stream_b[0], b_start)
            if chars[at:at + len(own)] != own:
                return True
            lo, hi = at, at + len(own)
            for start in range(max(0, hi - FURNITURE_GRAM), min(lo, len(chars) - FURNITURE_GRAM) + 1):
                if chars[start:start + FURNITURE_GRAM] in page_furniture:
                    return False
            for head in page_heads:
                for start in range(max(0, hi - len(head)), min(lo, len(chars) - len(head)) + 1):
                    if is_running_head(chars[start:start + len(head)], [head]):
                        return False
            return True

        # Decision D15 (the user's ruling of 2026-09-27): the book's furniture
        # one engine read and the other did not is left out before anything
        # is asked.
        furniture_rule = mode == "merge"
        block_starts = [0] + block_ends[:-1]

        def furniture_insertion(seg: Mapping[str, Any], a_start: int, b_start: int) -> dict[str, Any] | None:
            """Decision D15: text one engine read and the other did not, every
            character of it inside a stretch that recurs across the book's pages
            (furniture_grams: a reprint's margin title, a running head), which is
            a whole layout block of engine A's or reaches the edge of the page as
            its engine read it.  Measured on a lecture page of a reprint: engine A
            read the reprint's margin title as a block of its own, engine B did
            not read it, and with no engine preference the insertion went to
            裁決, which rightly said it is printed - and the page kept 14 (in two
            runs 19, the verdict running on into the volume number) characters of
            margin furniture as a heading.  The recurrence the formatter's
            deletion guard already accepts as furniture covered all seven of its
            8-grams.  Recurrence, not the margin: the layout's margin blocks on
            that page included its real chapter headings.  Returns the evidence,
            or None."""
            if not furniture_rule or bool(seg["a"]) == bool(seg["b"]):
                return None
            own = CJK(seg["a"] or seg["b"])
            if len(own) < FURNITURE_GRAM or furniture_leftover(own, page_furniture, ()):
                return None
            side = "A" if seg["a"] else "B"
            chars, at = (stream_a[0], a_start) if side == "A" else (stream_b[0], b_start)
            if chars[at:at + len(own)] != own:
                return None
            edge = at == 0 or at + len(own) == len(chars)
            whole = side == "A" and offsets_valid and any(
                lo == a_start and hi == a_start + len(own) for lo, hi in zip(block_starts, block_ends))
            if not (edge or whole):
                return None
            return {"readBy": side, "characters": len(own), "wholeBlock": whole, "pageEdge": edge}

        # Switch --learned-heads: the learned running head, in the merge.
        learned_merge = learned_record(arguments) if mode == "merge" else None

        def learned_insertion(seg: Mapping[str, Any], a_start: int) -> dict[str, Any] | None:
            """Switch --learned-heads: text one engine read and the other did
            not that is all the learned running head with a stem in it
            (learned_leftover: the stem, the labels, the folio beside them),
            to be left out before anything is asked.  Engine B reads the head
            column in its stream wherever it falls - before the body, between
            two of its columns - where engine A read it as a block of its own
            or not at all: measured on a 488-page book's sealed pages, six
            such readings stood in the body, the title with the volume label
            and the folio, one letter of the title misread in two of them,
            which the recurring n-grams (D15) did not cover.  A stem is
            required: a label or numerals alone are what the body prints too.
            And a place apart from the body's words, in engine A's stream (its
            layout blocks, offsets_valid): engine A's text at an edge of its
            blocks - a block of its own, or the start or end of one the layout
            read with a body column - and engine B's where engine A passes
            from one block to the next or at the page's start or end; inside
            a block only where what engine A read between it and the block's
            start or end is the learned head too (the folio both engines read
            beside the title: measured on the replay of a book's sealed pages,
            engine A read a body line, the title with its label, and the
            folio as one block, and engine B the folio before the title).
            Never with the body's words of one block on both sides.  The text
            alone is no evidence: a stem is the title's words, a window of
            them one short or one misread (stem_windows), and a book's body
            names its own subject - measured on a history of a rebellion, the
            title's first five characters (the rebel's name and the word for
            the rebellion; one short of the title, a stem window) stand in
            the body of nine of its 344 pages; a made-up page
            (tests/check_learned_heads.py) naming the book in a sentence whose
            words engine A did not read.  Returns the evidence, or None."""
            if not learned_merge or bool(seg["a"]) == bool(seg["b"]) or "window" in seg or not offsets_valid:
                return None
            own = CJK(seg["a"] or seg["b"])
            left, found = learned_leftover(own, learned_merge, job.scan_page)
            if not own or left or not found["stems"]:
                return None
            a_end = a_start + len(seg["a"])
            if any(lo < a_start and a_end < hi
                   and learned_leftover(stream_a[0][lo:a_start], learned_merge, job.scan_page)[0]
                   and learned_leftover(stream_a[0][a_end:hi], learned_merge, job.scan_page)[0]
                   for lo, hi in zip(block_starts, block_ends)):
                return None
            # The blocks it is (engine A's) or stands between (engine B's).
            where = [k for k, (lo, hi) in enumerate(zip(block_starts, block_ends))
                     if lo < hi and (lo < a_end and a_start < hi if seg["a"] else a_start in (lo, hi))]
            return {"readBy": "A" if seg["a"] else "B", "characters": len(own),
                    "blocks": [layout_index(blocks, k) for k in where],
                    **{key: value for key, value in found.items() if value}}

        def lopsided_furniture(seg: Mapping[str, Any], a_start: int, b_start: int) -> str | None:
            """The engine ("A" or "B") whose text only it read, the surplus of a
            split lopsided replacement (split_lopsided), the book's furniture
            explains: all but INSERTION_LIMIT of its characters are its
            running head, the folio beside it or its recurring n-grams
            (furniture_leftover, where its engine read it) - the test, and the
            allowance, an answer of 裁決's has beside the furniture
            (adjudicate_segment, furnitureSides).  Else None."""
            if not (seg.get("lopsided") or {}).get("surplus") or bool(seg["a"]) == bool(seg["b"]):
                return None
            side = "a" if seg["a"] else "b"
            own = CJK(seg[side])
            chars, at = (stream_a[0], a_start) if side == "a" else (stream_b[0], b_start)
            left = (furniture_leftover(chars, page_furniture, page_heads, (at, at + len(own)))
                    if chars[at:at + len(own)] == own else furniture_leftover(own, page_furniture, page_heads))
            return side.upper() if len(own) > INSERTION_LIMIT and left <= INSERTION_LIMIT else None

        def boundary_of(seg: Mapping[str, Any], a_start: int) -> tuple[int, int | None] | None:
            """The layout blocks either side of text only engine B read where
            engine A's reading passes from one block to the next: (the block
            that ends there, the next block holding text, or None at the
            page's end); None inside a block, at the page's start, or when
            the blocks do not split engine A's stream.  boundary_crop shows
            both edges."""
            if seg["a"] or not seg["b"] or not offsets_valid:
                return None
            if "window" in seg:
                # Engine B's reading of a window of an order-conflict table
                # block (window_merge): its place in engine A's stream is not
                # a place on the page - the block is the window's, and its
                # crop the block's (locate_segment).
                return None
            ending = next((j for j, (end, n) in enumerate(zip(block_ends, block_lengths))
                           if n and end == a_start), None)
            if ending is None:
                return None
            return ending, next((j for j in range(ending + 1, len(blocks)) if block_lengths[j]), None)

        def reduced_by(seg: Mapping[str, Any]) -> dict[str, Any]:
            """The engines' readings before the mark rule took out the characters
            the other engine's marks account for (or the circles it was held
            back from taking, circle_withheld), and where engine B read a
            reading placed at engine A's site (place_moved_once), for the record
            of whatever settles the rest."""
            return {**({"markReduced": seg["markReduced"]} if seg.get("markReduced") else {}),
                    **({"markWithheld": seg["markWithheld"]} if seg.get("markWithheld") else {}),
                    **({"movedFrom": seg["movedFrom"]} if "movedFrom" in seg else {})}

        def site(seg: Mapping[str, Any], a_start: int) -> dict[str, Any]:
            """Where a disagreement is, for decisionChanges: its layout block and the
            engines' readings with engine A's context (mergedOffset is filled in
            once the page's text is settled)."""
            block, _ = locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_start, seg)
            position = next((i for i, b in enumerate(blocks) if b is block), None)
            return {"block": layout_index(blocks, position) if position is not None else None,
                    "contextBefore": seg["before"], "contextAfter": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"]}

        own_reading = {"a": CJK(draft), "b": CJK(draft2 or "")}

        def explain(seg: Mapping[str, Any], a_start: int, b_start: int,
                    kept: Mapping[str, str] | None = None, sides: str = "ab") -> None:
            """Mark what SEG's SIDES leave out - all of it, or what KEPT (the
            segment as a rule reduced it, or the piece written) does not hold -
            as read as no text of the page, in each engine's reading
            (explained, for readings_dropped).  Where the segment's place in
            the stream is not its text in the engine's reading (a window of an
            order-conflict table block runs in engine B's order), its nearest
            copy there; none, nothing is marked."""
            for side, at in (("a", a_start), ("b", b_start)):
                own, reading = CJK(seg[side]), own_reading[side]
                if side not in sides or not own:
                    continue
                if reading[at:at + len(own)] != own:
                    found = [i for i in range(len(reading) - len(own) + 1) if reading.startswith(own, i)]
                    if not found:
                        continue
                    at = min(found, key=lambda i: abs(i - at))
                rest = "" if kept is None else kept[side]
                for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, variant_fold(own), variant_fold(CJK(rest)),
                                                                 autojunk=False).get_opcodes():
                    if tag in ("delete", "replace"):
                        explained[side.upper()].append((at + i1, at + i2))
        circles = set()
        for i, (x, a_at) in enumerate(zip(segments, starts)):
            if x["tag"] != "equal" and is_numeral_conflict(x) and is_lone_circle(
                    x, locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_at, x)[0]):
                circles.add(i)
        # A block engine B reads in another order (nominate_tables) is a
        # table the layout model missed, or a question for the rebuild: the
        # text its disagreements move is settled once, by runs, not one
        # disagreement at a time (settle_by_union) - except a like-for-like
        # substitution, and a run of engine B's not shown to be moved, which
        # go the usual way.
        # Each segment's engine-B side without the characters the bracket rule
        # and the mark rule (D1) take for engine A's printed marks: the
        # witness a nomination is read against (D21) and engine B's reading
        # a rebuilt table may hold (D20).
        b_settled = [engine_b_as_settled(x, a_at, b_at, (stream_a, stream_b) if bracket_rule else None,
                                         (marks_a, marks_b, aliases) if mark_rule else None)
                     for x, a_at, b_at in zip(segments, starts, b_starts)] if table_work else []
        nominations = nominate_tables(blocks, draft2) if table_work and mode == "merge" else []
        if table_work and mode == "merge":
            # Decision D21: the witness is engine B's reading with the glyphs
            # this page's rules settle as marks left out (marks_left_out).
            # A block nominated on engine B's reading as read stays nominated.
            witness, left_out = marks_left_out(segments, starts, b_starts, stream_b[0], b_settled)
            if left_out:
                plain = {n["block"] for n in nominations}
                nominations = sorted(nominations + [
                    dict(n, witness={"marksLeftOut": left_out})
                    for n in nominate_tables(blocks, witness) if n["block"] not in plain],
                    key=lambda n: n["block"])
        union_pieces: dict[int, str] = {}

        def block_union(segs: Sequence[Mapping[str, Any]], seg_starts: Sequence[int],
                        seg_b_starts: Sequence[int], k: int,
                        skip: set[int] | frozenset[int] = frozenset()) -> tuple[list[int], dict[int, str]]:
            """Block K's disagreements the union may settle, and the pieces it
            settles (settle_by_union), for the segments SEGS."""
            lo, hi = (block_ends[k - 1] if k else 0), block_ends[k]
            in_block: list[int] = []
            for i, (x, a_at) in enumerate(zip(segs, seg_starts)):
                if x["tag"] == "equal" or x.get(LOOPED_READING) or "settled" in x:
                    # Engine B's reading of a set-aside looped block is that
                    # block's (quarantine_looped_block), wherever it sits; a
                    # stretch engine A read twice is settled (read_twice).
                    continue
                a_end = a_at + len(x["a"])
                inside = (lo <= a_at < hi) if not x["a"] else (lo <= a_at and a_end <= hi)
                # Numerals and rare codepoints keep their own rules, which hold
                # whatever the order: a rule or a bracket read as 一 is no
                # character anywhere.
                if inside and not (i in skip or (x["tag"] == "replace" and len(CJK(x["a"])) == len(CJK(x["b"])))
                                   or is_numeral_conflict(x) or is_rare_codepoint_conflict(x)):
                    in_block.append(i)
            # Engine B's reading outside the block: an engine-A run it holds
            # is not the text B read in the block (settle_by_union).
            return in_block, settle_by_union(segs, in_block, engine_b_outside(segs, seg_starts, seg_b_starts, lo, hi))

        for nomination in nominations:
            in_block, settled = block_union(segments, starts, b_starts, nomination["block"], circles)
            union_pieces.update(settled)
            nomination["union"] = {"settled": len(settled), "leftToUsualWay": len(in_block) - len(settled)}
        union_of = set(union_pieces)
        # Numeral and rare-codepoint conflicts are read off the crop on every
        # page; keep budget for them so the page's prose cannot use it up.
        protected_left = sum(1 for i, x in enumerate(segments) if x["tag"] != "equal" and i not in circles
                             and i not in union_of and "settled" not in x
                             and (is_numeral_conflict(x) or is_rare_codepoint_conflict(x)))
        # The segments are walked in order to decide each one - the budget
        # depends only on how many were sent to 裁決 before, never on what they
        # answered - and the calls themselves then run together.
        planned: list[tuple[int, dict[str, Any], bytes | None, Path, str]] = []
        # (slot in pieces, decisionChanges entry) for each text change a
        # decision-dependent rule makes; its outcome is filled in once known.
        changes: list[tuple[int, dict[str, Any]]] = []
        # Short one-engine insertions D3 would have asked about with the
        # budget spent (insertion-unjudged).
        unjudged: list[str] = []
        for index, seg in enumerate(segments):
            a_start = starts[index]
            if seg["tag"] == "equal":
                pieces.append(seg["a"])
                continue
            if seg.get(LOOPED_READING):
                # Engine A's reading of this region was a loop and is set
                # aside: engine B's is the only reading, not a disagreement.
                pieces.append(seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": seg["b"], "resolved_by": "engine-B-only-reading-A-looped",
                })
                continue
            if "settled" in seg or index in union_of:
                # window_merge's copies and furniture row, a stretch engine A
                # read twice (read_twice), and a nominated block's order
                # disagreements settled by the union.
                piece, how = seg.get("settled") or (union_pieces[index], "order-disputed-block-union")
                if how == "table-furniture-row":
                    explain(seg, a_start, b_starts[index])
                pieces.append(piece)
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": piece, "resolved_by": how,
                })
                continue
            if index in circles:
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": "circle-not-numeral",
                })
                continue
            surplus = lopsided_furniture(seg, a_start, b_starts[index])
            if surplus:
                # The surplus of a split lopsided replacement that the book's
                # furniture explains (its running head with the folio beside
                # it, its recurring n-grams): left out, as the preference left
                # it out before the split - a running head is not revived as
                # body text.
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"lopsided-surplus-furniture-{surplus}",
                    "lopsided": seg["lopsided"],
                })
                continue
            furniture_found = furniture_insertion(seg, a_start, b_starts[index])
            if furniture_found:
                # Decision D15: the book's furniture only one engine read, left out.
                changes.append((len(pieces), {"decision": "D15", "rule": "furniture-insertion",
                                              **site(seg, a_start), "evidence": furniture_found}))
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"furniture-one-engine-{furniture_found['readBy']}",
                })
                continue
            learned_found = learned_insertion(seg, a_start)
            if learned_found:
                # Switch --learned-heads: the learned running head only one
                # engine read, left out.
                changes.append((len(pieces), {"decision": "learnedHeads", "rule": "learned-head-reading",
                                              **site(seg, a_start), "evidence": learned_found}))
                explain(seg, a_start, b_starts[index])
                pieces.append("")
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": "", "resolved_by": f"learned-head-one-engine-{learned_found['readBy']}",
                })
                continue
            if bracket_rule and is_numeral_conflict(seg):
                settled = bracket_explanation(seg, a_start, b_starts[index], stream_a, stream_b)
                if settled and settled[0] in ("A", "B"):
                    protected_left -= 1
                    explain(seg, a_start, b_starts[index], {"a": settled[1], "b": settled[1]})
                    if figure_rule:
                        after_rules[index] = dict(seg, a=settled[1], b=settled[1])
                        if not seg["a"]:
                            figure_settled[index] = ("marks", bool(CJK(settled[1])))
                    pieces.append(settled[1])
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": settled[1], "resolved_by": f"bracket-not-numeral-{settled[0]}",
                    })
                    continue
                if settled:
                    # Only the numerals left after the brackets go to 裁決.
                    explain(seg, a_start, b_starts[index], settled[1])
                    seg = settled[1]
            if mark_rule:
                marked = mark_explanation(seg, a_start, b_starts[index], marks_a, marks_b, aliases)
                withheld = marked and circle_withheld(
                    seg, a_start, b_starts[index], marked, marks_a, marks_b,
                    locate_segment(blocks, block_ends, block_lengths, offsets_valid, a_start, seg)[0])
                if withheld:
                    # A circle that may be a zero or a placeholder: the segment
                    # goes on whole, as with the rule off, and says why.
                    seg = dict(seg, markWithheld={"marks": [list(pair) for pair in marked[2]], "why": withheld})
                    marked = None
                if marked:
                    changes.append((len(pieces), {
                        "decision": "D1", "rule": "mark-not-character", **site(seg, a_start),
                        # The glyphs taken for marks and how often this book's
                        # drafts showed each at a mark the other engine printed.
                        "evidence": {"marks": [list(pair) for pair in marked[2]], "glyphsOf": marked[3],
                                     "aliases": {char: aliases[marked[3]].get(char, {})
                                                 for char, _ in marked[2]}}}))
                if marked and marked[0] in ("A", "B"):
                    # Engine B read engine A's marks as characters (or the other
                    # way round): both read the same characters otherwise.
                    if is_numeral_conflict(seg) or is_rare_codepoint_conflict(seg):
                        protected_left -= 1
                    explain(seg, a_start, b_starts[index], {"a": marked[1], "b": marked[1]})
                    if figure_rule:
                        after_rules[index] = dict(seg, a=marked[1], b=marked[1])
                        if not seg["a"]:
                            figure_settled[index] = ("marks", bool(CJK(marked[1])))
                    pieces.append(marked[1])
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": marked[1], "resolved_by": f"mark-not-character-{marked[0]}",
                        "marks": [list(pair) for pair in marked[2]],
                    })
                    continue
                if marked:
                    # Only the characters no mark accounts for go on (査丶 against
                    # 查 becomes the variant question 査 against 查).
                    explain(seg, a_start, b_starts[index], marked[1])
                    seg = marked[1]
            if figure_rule:
                after_rules[index] = seg
                standing = figure_at(a_start) if not seg["a"] and seg["b"] else None
                if standing is not None and not figure_stand_ins(
                        seg["b"], standing, figure_glyphs,
                        sum(1 for _, mark in (seg.get("markReduced") or {}).get("marks") or [] if mark in "（）")
                        + sum(len(bracket_kinds(gap)) for gap in
                              stream_b[1][b_starts[index]:b_starts[index] + len(segments[index]["b"]) + 1])):
                    # Something engine B read there is no stand-in: 裁決 is
                    # asked as without the switch, and told nothing new; its
                    # answer may still read the figure (figureAt).
                    seg = dict(seg, figureAt=a_start)
                    standing = None
                if standing is not None:
                    # Switch --keep-arabic-numerals: all engine B read at the
                    # gap where engine A read a confirmed figure (the marks
                    # taken out) is the figure's brackets read as strokes, or
                    # glyphs this book's drafts show engine B writing for
                    # figures or marks, none a numeral (figure_stand_ins):
                    # engine B's reading of the figure, written as nothing; the
                    # figure is written there (arabic_figure_kept).  A question
                    # it would have asked takes the budget it would have taken
                    # (figure_taken), so every other disagreement is decided
                    # as with it off.
                    numeral_or_rare = is_numeral_conflict(seg) or is_rare_codepoint_conflict(seg)
                    if numeral_or_rare:
                        protected_left -= 1
                    if to_judge(seg, a_start, b_starts[index]):
                        budget = arguments.max_adjudications - (0 if numeral_or_rare else max(0, protected_left))
                        if len(planned) + figure_taken < budget:
                            figure_taken += 1
                    explain(seg, a_start, b_starts[index])
                    figure_settled[index] = ("stand-in", True)
                    pieces.append("")
                    disagreements.append({
                        "context_before": seg["before"], "context_after": seg["after"],
                        "engineA": seg["a"], "engineB": seg["b"],
                        "resolved": "", "resolved_by": "arabic-figure-A", "figure": standing["figure"],
                        **reduced_by(seg),
                    })
                    continue
            numeral = is_numeral_conflict(seg)
            rare = is_rare_codepoint_conflict(seg)
            if numeral or rare:
                protected_left -= 1
            elif variant_segment(seg) is not None:
                # Two forms of one character: which form is printed is ruled per
                # pair for the whole book, never asked of the judge (measured
                # unable to tell them apart).
                piece, how, unruled = resolve_variant(seg, arguments)
                pieces.append(piece)
                variant_unruled.extend(unruled)
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": piece, "resolved_by": how,
                    **reduced_by(seg),
                })
                continue
            # Decision D3: a short one-engine insertion is a question for the
            # crop, not for the preference, which was measured on substitutions
            # only and says nothing about text one engine read and the other
            # did not (short_insertion).
            insertion = prefer in ("A", "B") and not (numeral or rare) and short_insertion(seg, a_start, b_starts[index])
            if prefer in ("A", "B") and not (numeral or rare) and not insertion:
                # Deterministic: the better engine wins. No model call, so this
                # cannot introduce the variant-normalisation errors that every
                # model-chooses design produced (4-7/11 vs the engine's 10/11).
                # Numeral-only disagreements are the exception: see
                # is_numeral_conflict.
                pick = seg["b"] if prefer == "B" else seg["a"]
                pieces.append(pick or seg["a"] or seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": pick or (seg["a"] or seg["b"]),
                    "resolved_by": f"prefer-engine-{prefer}",
                    **reduced_by(seg),
                })
                continue
            budget = arguments.max_adjudications - (0 if (numeral or rare) else max(0, protected_left))
            if len(planned) + figure_taken >= budget:
                # Out of budget: the engine this book's profile measured as the
                # better reader (the stated preference, or the probe's leader
                # when the probe was not confident enough to state one).
                engine = prefer if prefer in ("A", "B") else fallback
                pick = seg["b"] if engine == "B" else seg["a"]
                pieces.append(pick or seg["a"] or seg["b"])
                disagreements.append({
                    "context_before": seg["before"], "context_after": seg["after"],
                    "engineA": seg["a"], "engineB": seg["b"],
                    "resolved": pick or (seg["a"] or seg["b"]),
                    "resolved_by": f"prefer-engine-{engine}-adjudication-budget-spent",
                    **reduced_by(seg),
                })
                if insertion:
                    # Kept as it always was, but not settled: a question the
                    # crop was not asked.
                    unjudged.append(f"{seg['before'][-4:]}[{seg['a'] or seg['b']}]{seg['after'][:4]} "
                                    f"(engine {'A' if seg['a'] else 'B'} only)")
                continue
            if insertion:
                changes.append((len(pieces), {
                    "decision": "D3", "rule": "insertion-to-crop", **site(seg, a_start),
                    "evidence": {"characters": len(CJK(seg["a"] or seg["b"])),
                                 "readBy": "A" if seg["a"] else "B"}}))
                seg = dict(seg, insertionQuestion=True)
            block, offset = locate_segment(blocks, block_ends, block_lengths,
                                           offsets_valid, a_start, seg)
            crop, edge = None, boundary_of(seg, a_start)
            crop_kind = "boundary"
            if edge is not None:
                # Between two layout blocks (or at the page's end): both
                # edges, not the next block's head alone (boundary_crop).
                crop, setting = boundary_crop(job.render, blocks[edge[0]],
                                              blocks[edge[1]] if edge[1] is not None else None,
                                              len(CJK(seg["b"])) or 1)
                if crop:
                    shown = {"blocks": [layout_index(blocks, j) if j is not None else None for j in edge],
                             "set": setting}
                    seg = dict(seg, boundaryCrop=shown)
                    if insertion:
                        changes[-1][1]["evidence"]["boundaryCrop"] = shown
            if not crop:
                crop, crop_kind = segment_crop_kind(job.render, render_bytes, block, offset, seg)
            if crop_kind in ("glyph", "band") and block is not None and offset is not None:
                # A crop cut where the spot is estimated to be (crop_glyph,
                # crop_band) must be shown to hold it before 裁決's 無 deletes
                # what an engine read there (confirm_nothing): how much of the
                # context the spot's own block prints either side of it.
                in_block = len(CJK(str(block.get("text") or "")))
                seg = dict(seg, coverage={"kind": crop_kind, "beforeRoom": max(0, offset),
                                          "afterRoom": max(0, in_block - offset - len(seg["a"]))})
            if (crop and crop_kind != "page" and block is not None and offset is not None and seg["a"]
                    and offset + len(seg["a"]) <= len(CJK(str(block.get("text") or "")))):
                # The crop was cut from the one layout block that holds all of
                # engine A's reading here (D16's answer that copies the neighbour).
                seg = dict(seg, cropHoldsA=True)
            # Keep the exact pixels the adjudicator saw next to the sealed page,
            # so a verdict can be audited against its evidence.
            crop_path = job.out_json.with_name(
                f"{job.out_json.stem}-adjudication-{len(planned) + 1:02d}.png")
            if crop:
                crop_path.write_bytes(crop)
            # A reading the book's furniture explains - its running head with
            # the folio's figures beside it, however many, its recurring
            # n-grams, as its engine read them around the reading, and at most
            # INSERTION_LIMIT characters more (a figure misread) - may be
            # answered away; any other, read by one engine at more characters
            # than 裁決 writes, is kept (adjudicate_segment, deletion-refused).
            # Each reading is taken where its engine read it, when it still
            # stands there (the mark and bracket rules may have reduced it).
            read_at = {"a": (stream_a[0], a_start), "b": (stream_b[0], b_starts[index])}

            def leftover(side: str) -> int:
                chars, at = read_at[side]
                own = CJK(seg[side])
                if chars[at:at + len(own)] == own:
                    return furniture_leftover(chars, page_furniture, page_heads, (at, at + len(own)))
                return furniture_leftover(own, page_furniture, page_heads)

            seg = dict(seg, furnitureSides=[side for side in ("a", "b")
                                            if len(CJK(seg[side])) > ANSWER_DELETION_LIMIT
                                            and leftover(side) <= INSERTION_LIMIT],
                       # Where the spot is, for a D16 change if the answer is refused,
                       # and where each engine read it (explain).
                       answerSite=site(seg, a_start), streamAt=(a_start, b_starts[index]))
            pieces.append("")          # filled in below, in this order
            planned.append((len(pieces) - 1, seg, crop, crop_path, prefer if prefer in ("A", "B") else fallback))

        # Each question's place (call_site: its spot, its crop, and where it
        # stands in the merged text), for the loop detector's entry of a call
        # on it; filled in once known.
        spot_sites: dict[int, dict[str, Any]] = {slot: {} for slot, *_ in planned}

        def adjudicate(item: tuple[int, dict[str, Any], bytes | None, Path, str]):
            slot, seg, crop, _, engine = item
            with call_site(spot_sites[slot]):
                answer = adjudicate_segment(client, crop, seg, arguments.max_tokens, fallback_engine=engine,
                                            note=BOUNDARY_NOTES.get((seg.get("boundaryCrop") or {}).get("set"), ""),
                                            simplified=getattr(arguments, "simplified_script", False))
                return confirm_nothing(client, crop, seg, answer, arguments.max_tokens, engine)

        # D3 questions whose answer wrote less than the engine read (insertion-partial),
        # and answers that would have left out a longer reading (deletion-refused).
        partial_insertions: list[str] = []
        refused_deletions: list[str] = []
        # D16's refusals, (slot, question, sealed verdict, crop, fallback
        # engine), settled once every answer is in (reading_nearby); and 無
        # answers not shown to hold the spot (confirm_nothing).
        refusals: list[tuple[int, dict[str, Any], dict[str, Any], bytes | None, str]] = []
        unconfirmed: list[str] = []
        # Answers --neighbour-guard did not write: copies of the text beside their spot.
        neighbour_copies: list[str] = []
        beside_copies: list[str] = []
        # Answers in traditional script written in engine A's form against engine B's
        # other character, or in the one form neither engine read (converted_against_b).
        script_converted: list[str] = []
        if planned:
            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                answers = in_context(pool, adjudicate, planned)
            for (slot, asked, crop, crop_path, engine), (piece, record, m) in zip(planned, answers):
                add(m)
                pieces[slot] = piece
                record["crop"] = crop_path.name if crop else None
                spot_sites[slot].update(asked["answerSite"], crop=record["crop"])
                if asked.get("bracketReduced"):
                    # The engines' readings before the bracket rule took out
                    # the numerals engine A's brackets account for.
                    record["bracketReduced"] = asked["bracketReduced"]
                record.update(reduced_by(asked))
                if asked.get("insertionQuestion"):
                    # Asked because of decision D3 (short_insertion).
                    record["askedFor"] = "D3"
                if record.get("resolved_from") == "adjudicator-partial":
                    # 裁決 wrote less than the engine read: the engine's
                    # reading stands (adjudicate_segment), the count is a
                    # person's to check against the image.
                    partial_insertions.append(
                        f"{asked['before'][-4:]}[{asked['a'] or asked['b']}]{asked['after'][:4]} "
                        f"(engine {'A' if asked['a'] else 'B'} only; 裁決 wrote "
                        f"「{record.get('answeredTraditional', record['answered'])}」)")
                if record.get("resolved_from") == "adjudicator-deletion-refused":
                    # 裁決's answer would have left out more than a couple of
                    # characters one engine read: that reading stands (decision
                    # D16; with the switch off, the answer as written)
                    # - unless the merged text holds it nearby, which is looked
                    # at once every answer is in (below).
                    refusals.append((slot, asked, record, crop, engine))
                if record.get("resolved_from") == "adjudicator-empty-unconfirmed":
                    # 裁決 answered 無 on a crop not shown to hold the spot
                    # (confirm_nothing): the reading stands, and a person looks.
                    cover = record["coverageRead"]
                    changes.append((slot, {
                        "decision": "textLoss", "rule": "nothing-answer-unconfirmed", **asked["answerSite"],
                        "evidence": {key: cover[key] for key in ("crop", "found", "reading")}}))
                    shown = ("the crop does not show the spot" if cover["found"] != "placed"
                             else "read there blind: " + cover["reading"])
                    unconfirmed.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} ({shown}; kept {record['resolved'][:8]})")
                if asked.get("boundaryCrop"):
                    # Shown both blocks' edges (boundary_crop): which, and how set.
                    record["boundaryCrop"] = asked["boundaryCrop"]
                read_figure = (figure_verdict(record, figure_at(asked["figureAt"]))
                               if asked.get("figureAt") is not None and figure_at(asked["figureAt"]) else None)
                if read_figure:
                    # Switch --keep-arabic-numerals: 裁決 read the figure engine
                    # A read there, or that no character is printed there -
                    # engine B's glyphs are its reading of the figure, written
                    # as nothing, and the figure is written (arabic_figure_kept).
                    figure_settled[slot] = ("verdict", bool(CJK(piece)))
                    explain(asked, *asked["streamAt"])
                    pieces[slot] = ""
                    record["figureKept"] = {"figure": figure_at(asked["figureAt"])["figure"], "read": read_figure,
                                            "resolvedBefore": record["resolved"]}
                    record["resolved"] = ""
                adjudications.append(record)
            # D16 as the user ruled on 2026-09-27: 裁決's answer is refused only
            # where what it leaves out of the reading is not already in the
            # merged text nearby (reading_nearby) - measured, 10 of the 26
            # refusals of four runs kept a second copy of text read beside the
            # spot in another order (a table header read row by row; figures
            # read again under the next row, which stay refused).
            for slot, asked, record, crop, engine in refusals:
                kept = "A" if record["resolved"] == asked["a"] else "B"
                # Nor where engine B read nothing there, and 裁決, shown the
                # block engine A's reading stands in, answered the text beside
                # the spot (neighbour_copy, the neighbour guard's test): two
                # readings of three find no such text, and 裁決 read the
                # neighbour, so engine B's nothing is written (a person checks
                # it: neighbour-copy, and reading-dropped unless the page holds
                # the reading as a second copy).  Measured over the 220
                # refusals of six sealed books (2026-10-24): 4 so, and none
                # printed - a line engine A read in a margin strip of
                # bleed-through (#34), and three lines engine A read twice,
                # the second copy misread.  Every refusal of a reading only
                # engine A made that kept printed text answered 無 or something
                # not beside the spot (most often a piece of the reading; once
                # a misread of it, 岡 for a note's 同), or was shown another
                # block than the reading's (a heading read across two blocks).
                copied = (neighbour_copy(record["answered"], asked["a"], asked["b"],
                                         CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])),
                                         asked["before"], asked["after"],
                                         simplified=getattr(arguments, "simplified_script", False))
                          if getattr(arguments, "neighbour_guard", False) and not CJK(asked["b"])
                          and asked.get("cropHoldsA") else None)
                if copied:
                    answered = record["answered"]
                    pieces[slot] = asked["b"]
                    copied.update(keptEngine="B", keptBy="engine-B-read-nothing")
                    record.update(resolved=asked["b"], resolved_from="adjudicator-neighbour-copy",
                                  winner="undecided", neighbourCopy=copied)
                    changes.append((slot, {
                        "decision": "D16", "rule": "answer-copies-neighbour", **asked["answerSite"],
                        "evidence": {**copied, "readingCharacters": len(CJK(asked["a"])),
                                     "answeredCharacters": len(CJK(answered)), "window": NEIGHBOUR_WINDOW}}))
                    neighbour_copies.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B -]{asked['after'][:4]} "
                        f"(裁決 wrote 「{answered}」, the text {copied['distance']} character(s) {copied['side']} "
                        f"the spot; engine B read nothing there: engine A's 「{asked['a'][:8]}」 not written)")
                    continue
                reading = CJK(asked["a"] if kept == "A" else asked["b"])
                left_out = left_out_of(reading, CJK(record["answered"]))
                near = reading_nearby(left_out, CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])))
                if near:
                    near["leftOut"] = left_out
                evidence = {"keptEngine": kept, "readingCharacters": len(reading),
                            "answeredCharacters": len(CJK(record["answered"])), "limit": ANSWER_DELETION_LIMIT,
                            "furnitureSides": [side.upper() for side in asked.get("furnitureSides") or []]}
                if near and not CJK(record["answered"]) and asked.get("coverage"):
                    # A 無 deletes only once its crop is shown to hold the spot
                    # (confirm_nothing), here as anywhere: a reading nearby says
                    # the page has the text once more, not that the crop shows
                    # the spot empty.  Not shown, the refusal stands.
                    with call_site(spot_sites[slot]):
                        _, checked, m = confirm_nothing(
                            client, crop, asked,
                            (record["answered"], dict(record, resolved_from="adjudicator-empty"), {}),
                            arguments.max_tokens, engine)
                    add(m)
                    if "coverageRead" in checked:
                        record["coverageRead"] = checked["coverageRead"]
                        evidence["coverageRead"] = {key: checked["coverageRead"][key]
                                                    for key in ("crop", "found", "reading", "confirmed")}
                        if not checked["coverageRead"]["confirmed"]:
                            near = None
                if near:
                    # The answer stands: the reading is written nearby already.
                    answered = record.pop("answered")
                    pieces[slot] = answered
                    record.update(resolved=answered, resolved_from="adjudicator" if CJK(answered)
                                  else "adjudicator-empty", readingNearby=near)
                    record["winner"] = segment_winner(asked, answered, record["resolved_from"])
                    # What it leaves out of the kept reading is written nearby: no
                    # dropped reading (readings_dropped).
                    explain(asked, *asked["streamAt"], {"a": answered, "b": answered}, kept.lower())
                    changes.append((slot, {
                        "decision": "D16", "rule": "answer-accepted-reading-nearby", **asked["answerSite"],
                        "evidence": {**evidence, "nearby": near}}))
                    continue
                changes.append((slot, {
                    "decision": "D16", "rule": "answer-deletion-guard", **asked["answerSite"],
                    "evidence": evidence}))
                refused_deletions.append(
                    f"{asked['before'][-4:]}[A {asked['a'][:16] or '-'} / B {asked['b'][:16] or '-'}]"
                    f"{asked['after'][:4]} (裁決 wrote "
                    + (f"「{record.get('answeredTraditional', record['answered'])}」" if record["answered"]
                       else "that nothing is printed")
                    + ("; a blind read of the crop did not show the spot empty" if "coverageRead" in record else "")
                    + f"; kept {record['resolved'][:16]})")
            if getattr(arguments, "neighbour_guard", False):
                # Switch --neighbour-guard: an answer, neither engine's reading,
                # that is the text the merged text has right beside its spot, as
                # the context 裁決 was shown has it there, and neither engine
                # read at the spot (neighbour_copy), is not written: 裁決 read
                # the neighbour.  Once every answer is in, so the text beside a
                # spot is what the page writes there; in the page's order, each
                # seeing the ones before it settled.  Not an answer D16 let go
                # because its left-out reading stands nearby (readingNearby),
                # which that rule settled.
                for (slot, asked, _, _, engine), (_, record, _) in zip(planned, answers):
                    if record.get("resolved_from") != "adjudicator" or "readingNearby" in record:
                        continue
                    copied = neighbour_copy(pieces[slot], asked["a"], asked["b"],
                                            CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:])),
                                            asked["before"], asked["after"],
                                            simplified=getattr(arguments, "simplified_script", False))
                    if copied is None:
                        continue
                    # What stands instead: on a D3 question (one engine read a
                    # short run the other did not) the reading of the engine
                    # that read it, as D3 keeps it where 裁決 wrote less of it
                    # (insertion-partial); otherwise the reading of the engine
                    # the page falls back on (its preference, else the probe's
                    # leader), as that engine read it - nothing where it read
                    # nothing.  A person checks it (neighbour-copy).
                    if asked.get("insertionQuestion"):
                        side, kept_by = ("a" if asked["a"] else "b"), "D3-reading"
                    else:
                        side, kept_by = ("b" if engine == "B" else "a"), "fallback-engine"
                    answered = pieces[slot]
                    pieces[slot] = asked[side]
                    copied.update(keptEngine=side.upper(), keptBy=kept_by)
                    record.update(resolved=asked[side], resolved_from="adjudicator-neighbour-copy",
                                  winner="undecided", answered=answered, neighbourCopy=copied)
                    changes.append((slot, {
                        "decision": "neighbourGuard", "rule": "neighbour-copy", **asked["answerSite"],
                        "evidence": {**copied, "window": NEIGHBOUR_WINDOW}}))
                    neighbour_copies.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} (裁決 wrote 「{answered}」, the text {copied['distance']} "
                        f"character(s) {copied['side']} the spot; kept engine {side.upper()}'s "
                        f"「{asked[side][:8]}」)")
        if planned:
            # A reading only one engine made, of some length, that 裁決 confirmed
            # whole and that is exactly the text standing right beside the spot
            # in the merged text (the engine read a line twice; 裁決, shown the
            # spot between two blocks, answered the next line): written, since
            # the answer is an engine's reading, but a doubt - the page may hold
            # the line twice.  Not where a side stands in a furniture band: a
            # chapter title printed in the running head and as the heading is
            # two copies in print, and the furniture one goes later (measured on
            # the modern book: 3 such titles, none a copy; one body line read
            # twice by engine B on scan 33, written twice in one run of two).
            for (slot, asked, _, _, engine), (_, record, _) in zip(planned, answers):
                if record.get("resolved_from") != "adjudicator" or asked.get("furnitureSides"):
                    continue
                readings = [CJK(asked["a"]), CJK(asked["b"])]
                written = CJK(pieces[slot])
                if sum(1 for r in readings if r) != 1 or len(written) < BESIDE_COPY_MIN or written not in readings:
                    continue
                before, after = CJK("".join(pieces[:slot])), CJK("".join(pieces[slot + 1:]))
                side = ("after" if after.startswith(written) else "before" if before.endswith(written) else None)
                if side is None:
                    continue
                record["copyBeside"] = side
                beside_copies.append(f"{asked['before'][-4:]}[{'A' if readings[0] else 'B'} {written[:10]}…]{asked['after'][:4]} "
                                     f"(the same {len(written)} characters stand right {side} the spot)")
            # In a book printed in simplified script, an answer in traditional
            # script written in engine A's form where engine B read another
            # character, or in the table's one form, which neither engine read
            # (converted_against_b): a person looks.
            for (slot, asked, _, _, _), (_, record, _) in zip(planned, answers):
                given = record.get("answeredTraditional")
                if (given and record.get("resolved_from") == "adjudicator"
                        and converted_against_b(given, CJK(asked["a"]), CJK(asked["b"]))):
                    script_converted.append(
                        f"{asked['before'][-4:]}[A {asked['a'][:8] or '-'} / B {asked['b'][:8] or '-'}]"
                        f"{asked['after'][:4]} (裁決 wrote 「{given}」; written {pieces[slot][:8]})")
        # Decision D24: a character only engine A read, which the page's
        # verdicts judged a misread of engine A's at every site they were
        # asked about (judged_misreads), is asked about too, on its own crop,
        # with the verdicts' reading as the second.  Measured on a textbook
        # table's continuation page: 裁決 read one character of engine A's as
        # another at all three sites engine B had read (as a rare codepoint),
        # and a label cell holding it, in a band engine B never read, kept
        # engine A's misread - nothing was asked.
        # The segments as the table checks count what engine A read there
        # (agreed_block_text): a character D24 asked about stands as written.
        agreed_segments = segments
        if planned:
            misread = judged_misreads([(asked, record) for (_, asked, _, _, _), (_, record, _)
                                       in zip(planned, answers)])
            spots = propagation_spots(segments, pieces, union_of, {slot for slot, *_ in planned}, misread)
            spots = spots[:max(0, arguments.max_adjudications - len(planned) - figure_taken)]

            # Each question's place (call_site), as its D24 entry gives it, for
            # the loop detector's entry of a call on it.
            propagated_sites: dict[tuple[int, int], dict[str, Any]] = {(i, p): {} for i, p, _ in spots}

            def propagate(spot: tuple[int, int, dict[str, Any]]):
                i, p, sub = spot
                block, offset = locate_segment(blocks, block_ends, block_lengths, offsets_valid, starts[i] + p, sub)
                crop = segment_crop(job.render, render_bytes, block, offset, sub)
                with call_site(propagated_sites[(i, p)]):
                    return crop, adjudicate_segment(client, crop, sub, arguments.max_tokens, fallback_engine="A",
                                                    note=propagation_note(sub["a"], sub["b"]),
                                                    simplified=getattr(arguments, "simplified_script", False))

            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                propagated = in_context(pool, propagate, spots)
            for number, ((i, p, sub), (crop, (piece, record, m))) in enumerate(zip(spots, propagated), 1):
                add(m)
                # One character for one: never a deletion or an insertion of
                # text engine A read (an answer that is neither is not written).
                written = piece if len(CJK(piece)) == 1 else sub["a"]
                pieces[i] = pieces[i][:p] + written + pieces[i][p + 1:]
                if written != sub["a"]:
                    # Engine A's reading there is not required in a table
                    # (engine B read none of it): what is written there is.
                    if agreed_segments is segments:
                        agreed_segments = list(segments)
                    agreed_segments[i] = dict(agreed_segments[i], a=pieces[i])
                crop_path = job.out_json.with_name(
                    f"{job.out_json.stem}-adjudication-{len(planned) + number:02d}.png")
                if crop:
                    crop_path.write_bytes(crop)
                # Not an engine disagreement: 乙 is no engine's reading, so the
                # verdict says nothing about which engine reads better
                # (engineTally, the drift audit).
                record.update({"crop": crop_path.name if crop else None, "askedFor": "D24",
                               "trigger": "verdict-propagation",
                               "propagatedFrom": {"pair": [sub["a"], sub["b"]], "sites": misread[sub["a"]][1]}})
                adjudications.append(record)
                block, _ = locate_segment(blocks, block_ends, block_lengths, offsets_valid, starts[i] + p, sub)
                position = next((j for j, b in enumerate(blocks) if b is block), None)
                decision_changes.append({
                    "decision": "D24", "rule": "verdict-propagated",
                    "block": layout_index(blocks, position) if position is not None else None,
                    "contextBefore": sub["before"], "contextAfter": sub["after"],
                    "engineA": sub["a"], "engineB": "", "after": written,
                    "mergedOffset": len(CJK("".join(pieces[:i]))) + p,
                    "evidence": {"pair": [sub["a"], sub["b"]], "judgedSites": misread[sub["a"]][1],
                                 "resolved": piece}})
                propagated_sites[(i, p)].update({key: decision_changes[-1][key] for key in SITE_PLACE_KEYS},
                                                crop=record["crop"])
        if unjudged:
            doubts.append({"kind": "insertion-unjudged", "detail": (
                f"ONE-ENGINE INSERTION KEPT UNJUDGED: 裁決 budget spent; {len(unjudged)} short insertion(s) "
                "only one engine read were kept by the preference as before: " + "; ".join(unjudged[:6])
                + " - check them against the image")})
        if refused_deletions:
            doubts.append({"kind": "deletion-refused", "detail": (
                f"READING KEPT OVER A SHORTER ANSWER: at {len(refused_deletions)} spot(s) 裁決's answer (neither "
                f"engine's reading, or nothing printed) would have left out more than {ANSWER_DELETION_LIMIT} "
                "characters one engine read; that reading is kept: " + "; ".join(refused_deletions[:6])
                + " - check the text against the image")})
        if unconfirmed:
            doubts.append({"kind": "nothing-unconfirmed", "detail": (
                f"READING KEPT OVER AN UNCONFIRMED 無: at {len(unconfirmed)} spot(s) 裁決 answered that nothing is "
                "printed where one engine read text, on a crop cut where the spot was estimated to be, and a "
                "blind reading of that crop did not show the spot empty between the characters around it; "
                "the reading is kept: " + "; ".join(unconfirmed[:6]) + " - check it against the image")})
        if partial_insertions:
            doubts.append({"kind": "insertion-partial", "detail": (
                f"ONE-ENGINE INSERTION KEPT WHOLE: 裁決 said text is printed at {len(partial_insertions)} "
                "spot(s) only one engine read, but wrote fewer characters than that engine read; the "
                "engine's reading is kept: " + "; ".join(partial_insertions[:6])
                + " - count the characters against the image")})
        if beside_copies:
            doubts.append({"kind": "neighbour-copy", "detail": (
                f"ONE-ENGINE READING WRITTEN BESIDE ITS OWN COPY: at {len(beside_copies)} spot(s) 裁決 confirmed a "
                "reading only one engine made, and the same characters stand right beside the spot already; the "
                "engine may have read the line twice: " + "; ".join(beside_copies[:6])
                + " - check against the image whether the line is printed once or twice")})
        if script_converted:
            doubts.append({"kind": "answer-script-converted", "detail": (
                f"ANSWER IN TRADITIONAL SCRIPT WRITTEN IN SIMPLIFIED SCRIPT: at {len(script_converted)} spot(s) "
                "裁決's answer, in traditional script in a book printed in simplified script, is written in the "
                "form engine A read where engine B read another character, or in OpenCC's one simplified form, "
                "which neither engine read: " + "; ".join(script_converted[:6])
                + " - check the character against the image")})
        if neighbour_copies:
            doubts.append({"kind": "neighbour-copy", "detail": (
                f"ANSWER COPIED FROM BESIDE THE SPOT: at {len(neighbour_copies)} spot(s) 裁決's answer (neither "
                f"engine's reading) is the text the page has within {NEIGHBOUR_WINDOW} character(s) of the spot, "
                "which neither engine read at the spot; it is not written (--neighbour-guard) and an engine's "
                "reading is kept: " + "; ".join(neighbour_copies[:6])
                + " - check the spot against the image (a character printed twice there is written once)")})
        # Each decision's text change, now that every piece is settled: what it
        # wrote there and where that is in the merged text.
        for slot, change in changes:
            change["after"] = pieces[slot]
            change["mergedOffset"] = len(CJK("".join(pieces[:slot])))
            decision_changes.append(change)
        if loop_cuts:
            # And each question's, for the loop detector's entries (spot_sites).
            for slot, place in spot_sites.items():
                place["mergedOffset"] = len(CJK("".join(pieces[:slot])))
        # Each lopsided replacement split (split_lopsided): what the merge wrote
        # for its pieces.
        for split in lopsided:
            slots = [i for i, x in enumerate(segments) if (x.get("lopsided") or {}).get("id") == split["id"]]
            written = "".join(pieces[i] for i in slots)
            split.update(written=written, pieces=len(slots))
            if slots:
                decision_changes.append({
                    "decision": "textLoss", "rule": "lopsided-split", **site(split["segment"], split["aStart"]),
                    "after": written, "mergedOffset": len(CJK("".join(pieces[:slots[0]]))),
                    "evidence": {"how": split["how"],
                                 "pieces": [{"engineA": segments[i]["a"], "engineB": segments[i]["b"],
                                             "written": pieces[i]} for i in slots]}})
        # Each run placed once (D2): at engine B's site the merge now writes only
        # what engine A never read, and the rest stands at engine A's site.
        # Both sites are sealed: engine A's site holds what the two copies
        # share and engine B's characters between them that face nothing of
        # engine A's, which the read site's entry alone did not show.
        draft_chars = "".join(x["a"] for x in segments)

        def merged_offset(gap: int) -> int:
            """Where engine A's position GAP falls in the merged text: the start of
            the first segment at or after it, or the text's end (a run engine B
            read at the page's end, after all of engine A's text)."""
            return len(CJK("".join(pieces[:bisect.bisect_left(starts, gap)])))

        def within(tag_a: str, at: int, lo: int, hi: int) -> bool:
            """Whether a segment of engine-A text TAG_A starting at AT belongs to
            engine A's stretch [LO, HI): its text overlaps it, or it is text
            only engine B read strictly inside it."""
            return (at < hi and at + len(tag_a) > lo) if tag_a else lo < at < hi

        aligned_at = [0] + list(itertools.accumulate(len(x["a"]) for x in aligned))[:-1]

        def site_span(move: Mapping[str, Any]) -> tuple[int, int]:
            """Engine A's stretch at a moved run's site, widened to the whole
            aligned segments the placement touched: what engine B read there
            beside the run, read otherwise than engine A, is in the entry."""
            lo, hi = move["placedAt"]["engineA"]
            touched = [k for k, x in enumerate(aligned) if x["a"] and within(x["a"], aligned_at[k], lo, hi)]
            return (min([lo] + [aligned_at[k] for k in touched]),
                    max([hi] + [aligned_at[k] + len(aligned[k]["a"]) for k in touched]))

        def placed_site(lo: int, hi: int, placed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
            """The decisionChanges entry for engine A's stretch [LO, HI) where the
            runs PLACED were put: what is written there now, engine B's
            characters there that face nothing of engine A's included."""
            now = [i for i, x in enumerate(segments) if within(x["a"], starts[i], lo, hi)]
            there = {"a": draft_chars[lo:hi], "b": "".join(segments[i]["b"] for i in now),
                     "before": draft_chars[max(0, lo - 8):lo], "after": draft_chars[hi:hi + 8]}
            return {
                "decision": "D2", "rule": "moved-placed-once", "moveSite": "placed",
                **site(there, lo), "mergedOffset": merged_offset(lo),
                "after": "".join(pieces[i] for i in now),
                "evidence": {"moves": [{"engineBOffset": move["engineBOffset"], "readAt": move["readAt"],
                                        "matched": move["matched"]} for move in placed]}}

        for move in moves:
            gap = move["readAt"]["engineAGap"]
            left = "".join(pieces[i] for i, x in enumerate(segments)
                           if x.get("movedRemainderOf") == move["engineBOffset"])
            read = {"tag": "insert", "a": "", "b": move["text"], "before": draft_chars[max(0, gap - 8):gap],
                    "after": draft_chars[gap:gap + 8]}
            disagreements.append({
                "context_before": read["before"], "context_after": read["after"],
                "engineA": "", "engineB": move["text"], "resolved": left, "resolved_by": "moved-kept-once",
                "placedAt": move["placedAt"]["context"],
            })
            decision_changes.append({
                "decision": "D2", "rule": "moved-placed-once", "moveSite": "read",
                **site(read, gap), "mergedOffset": merged_offset(gap), "after": left, "evidence": {"placedAt": move["placedAt"], "matched": move["matched"],
                                            "engineBOffset": move["engineBOffset"]}})
        # Engine A's sites, one entry each: runs placed in one stretch (a label
        # and the value engine B read after it, both in one of engine A's
        # segments) share it, so no character is counted twice.
        placements = sorted((site_span(move), number) for number, move in enumerate(moves))
        groups: list[list[Any]] = []
        for (lo, hi), number in placements:
            if groups and lo < groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], hi)
                groups[-1][2].append(moves[number])
            else:
                groups.append([lo, hi, [moves[number]]])
        decision_changes.extend(placed_site(lo, hi, placed) for lo, hi, placed in groups)
        if figure_rule:
            figures_kept, arabic_figures_seen = arabic_figure_kept(
                page_figures, segments, starts, pieces, after_rules, figure_settled, blocks)
            figure_why = {g: entry["why"] for g, entry in zip(sorted(page_figures), arabic_figures_seen)
                          if not entry["kept"]}
        if moves:
            doubts.append({"kind": "moved-placed-once", "detail": (
                "TEXT READ IN ANOTHER ORDER PLACED ONCE: " + " | ".join(
                    f"{move['text'][:12]}{'…' if len(move['text']) > 12 else ''} (engine B read it at "
                    f"…{move['readAt']['context']}…, engine A at …{move['placedAt']['context']}…; kept at "
                    f"engine A's{', what engine A never read left where engine B read it: ' + move['leftAtSite'] if move['leftAtSite'] else ''})"
                    for move in moves[:4])
                + " - check the order against the image")})
        resolved = "".join(pieces)
        if not resolved.strip():
            raise ProofreadError("merge produced no text")
        # Whatever rule dropped it - the preference, 裁決's answer, a
        # furniture test, the order conflict's choice of one reading - more
        # than ANSWER_DELETION_LIMIT characters one engine read that the
        # merged text holds nowhere leave the page unclean
        # (readings_dropped; the user's fix of 2026-09-27).
        readings_lost = readings_dropped({"A": draft, "B": draft2 or ""}, resolved, page_furniture,
                                         page_heads, {engine: set(aliases.get(engine) or {})
                                                      for engine in ("A", "B")}, explained)
        for drop in readings_lost:
            key, asked_key = (("engineA", "draft_reading") if drop["engine"] == "A"
                              else ("engineB", "writer_reading"))
            grams = {drop["text"][i:i + DROP_GRAM] for i in range(len(drop["text"]) - DROP_GRAM + 1)}
            how = ([d.get("resolved_by") for d in disagreements
                    if any(g in variant_fold(CJK(d.get(key) or "")) for g in map(variant_fold, grams))]
                   + [a.get("resolved_from") for a in adjudications
                      if any(g in variant_fold(CJK(a.get(asked_key) or "")) for g in map(variant_fold, grams))])
            drop["by"] = sorted(set(filter(None, how))) or [mode]
        if readings_lost:
            doubts.append({"kind": "reading-dropped", "detail": (
                f"READING DROPPED AT MERGE: at {len(readings_lost)} spot(s) more than {ANSWER_DELETION_LIMIT} "
                "characters one engine read are nowhere in the merged text: " + "; ".join(
                    f"engine {drop['engine']} 「{drop['text'][:20]}{'…' if len(drop['text']) > 20 else ''}」 "
                    f"({drop['characters']}) at …{drop['contextBefore'][-4:]}|{drop['contextAfter'][:4]}… "
                    f"({', '.join(drop['by'])})" for drop in readings_lost[:6])
                + " - check them against the image")})
        # Stage A - punctuation per layout block, against the block's own crop.
        # A whole-page pass on a long page was measured to land anywhere between
        # every printed circle and none (or outright rejection) across runs of
        # the same input; a block is short enough to be read, and its crop shows
        # exactly the text being punctuated.  Characters are checked as an exact
        # sequence here - a punctuator has no business moving anything.
        # An order conflict that keeps engine A's own reading is engine A's
        # stream whole, so its blocks split it exactly: such a page went whole
        # only because this was asked of merge mode alone.  Measured on the
        # corpus: 77 of 94 order-conflict pages keep engine A's reading and
        # block_spans splits every one; four of them replayed offline went from
        # no block pass to 4-20 each.  A page that takes engine B's reading
        # has no layout for it and still goes whole.  Its one segment is engine
        # A's text marked equal, so the table checks that count the characters
        # both engines read (agreed_block_text) count engine A's there: the page
        # has no other reading to lose.  Table recovery takes these spans too
        # (a table's edge text, table_edges).
        spans = (block_spans(blocks, segments, pieces, draft)
                 if mode == "merge" or order_text == "A" else None)

        # The table's edge text: what a table block's span holds before or
        # after the cells of the table that stands for it is written beside
        # the table, not dropped with the block's text (restate_table,
        # table_edges).  By block position, the stretches of the merged text
        # before and after each such table (recover_tables' `edges`; for a
        # rebuilt table, what of its region's span it does not hold at either
        # end).
        table_edge: dict[int, dict[str, Any]] = {}
        # And, by block position, the edge text as written before and after
        # the table, marks and all (set with the structure pass's input).
        edge_written: dict[int, tuple[str, str]] = {}
        # The merged characters whose place in each engine's reading is exact,
        # with that engine's own character there (engine_marks_merged,
        # engine_b_marks_merged): what each engine read of the edge text.
        edge_placed: tuple[dict[int, str], dict[int, str]] = ({}, {})
        span_at = (list(itertools.accumulate((len(CJK(span)) for span in spans), initial=0))
                   if spans is not None else None)
        # The line or table cell of engine B's each merged character was read
        # in (engine_b_lines_merged): what engine B read with a table's first
        # or last cell on one line is that cell's (table_places), and the
        # edge text is judged line by line (edge_verdicts).
        b_lines = engine_b_lines_merged(draft2, segments, pieces) if draft2 is not None else None
        if b_lines is not None and len(b_lines) != len(CJK(resolved)):
            b_lines = None
        # Engine B's reading is the witness for the grid engine A built, and
        # the book's running heads tell a head row from a table row
        # (engine_table).
        tables, tables_rejected, table_orientation, table_sources, recovery = recover_tables(
            blocks, resolved, render_bytes, client, arguments.max_tokens,
            witness=draft2, heads=book_heads, furniture=book_furniture, spans=spans, lines=b_lines,
            table_orientation=table_orientation_mode)
        recovery_packet_pending = bool(recovery.get("packetPending"))
        doubts.extend(recovery["doubts"])
        table_grouping = recovery["figureGrouping"]
        cell_direction = recovery["cellDirection"]
        table_orientation_evidence.extend(recovery.get("tableOrientationEvidence", []))
        table_edge.update((index, dict(found, source="recovered"))
                          for index, found in recovery["edges"].items() if index in table_sources)

        # Tables rebuilt from the image, with their characters locked: a
        # block nominated as a missed table (its text is the union of both
        # readings), and a grid engine B contradicted on an order-conflict
        # page (its text from engine B's lines, window_merge).  What both
        # engines read there must be in the table, nothing else either read
        # may be added (rebuild_table).  A nominated block is only rebuilt
        # where the page has block spans: the table takes the block's place.
        table_nominations = [dict(n) for n in nominations]
        candidates = ([(n["block"], "nominated") for n in table_nominations if spans is not None]
                      + [(k, "grid refused, text from engine B's lines") for k in sorted(windowed)])
        if spans is None:
            for nomination in table_nominations:
                nomination["outcome"] = "not rebuilt: the page has no block spans"
        agreed_text = agreed_block_text(blocks, agreed_segments) if candidates else []
        # Characters the rebuilt tables hold beyond the merged text (D20): the
        # structure pass writes the tables as they stand, so its character
        # check counts them as sanctioned too.
        table_added: Counter = Counter()
        # Decision D22: a nominated block's rebuild also takes in the blocks
        # of its table the layout model split off (rebuild_region); each
        # block a table took in, by position, with the position of the block
        # whose table took it in.  Their text is the table's: it is not
        # written again beside it.
        absorbed_into: dict[int, int] = {}
        # What of each block taken in its table must hold (both engines'
        # reading of it): the fallback and tableCharactersLost count it as
        # they count a table block's agreed text.
        absorbed_held: dict[int, str] = {}
        # The blocks a refused region named, beside a table rebuilt from the
        # nominated block alone, with what both engines read of each (the
        # region's table would have had to hold it): they stay text, and the
        # structure pass may not leave them out (beside_lost); a page that
        # lacks them seals it in tableCharactersLost.  Measured on a textbook
        # page: the region of a table's body and its header column was refused
        # for one character no engine read, the body alone was rebuilt, and
        # the structure pass left the 20-character header out - the deletion
        # guard cut it into short pieces against the table's cells, and
        # tableCharactersLost counted table blocks only, so nothing said so.
        beside_table: dict[int, str] = {}
        # Characters no engine read that a rebuilt table may write, each
        # confirmed by 裁決 (the user's headerCharacters ruling, confirm_unread),
        # and the crops they were asked on.
        no_unread = getattr(arguments, "no_unread_table_characters", False)
        unread_crops: list[Path] = []
        # 裁決's answer on each spot asked (a retry that writes the same
        # character there is not asked again).
        unread_verdicts: dict[tuple[int, int, str, str, str], dict[str, Any]] = {}
        agreed_aligned = (agreed_block_text(blocks, aligned) if mode == "merge"
                          and any(source == "nominated" for _, source in candidates) else [])
        render_size: tuple[int, int] | None = None
        # Engine B's reading as the page settled it: its glyphs the page's
        # rules take for printed marks (丶 for ，, 一 for a bracket) are no
        # reading of a character (the D20 lock, D22's witness).
        as_settled = [dict(x, b=b) for x, b in zip(segments, b_settled)] if candidates else []
        # The other engine's character at each merged character the engines
        # read one for one (merged_other_readings), for a rebuilt table's
        # edge text: set when first needed.
        other_readings: list[str] | None = None
        region_origin: list[str] = []
        for k, source in candidates:
            # Sealed as the layout's block number, as every block number is
            # (layout_index); k stays the block's position here.
            layout = layout_index(blocks, k)
            if k in absorbed_into:
                for nomination in table_nominations:
                    if nomination["block"] == k:
                        nomination["outcome"] = (f"taken into block {layout_index(blocks, absorbed_into[k])}'s "
                                                 "rebuilt table")
                continue
            region = [k]
            # What of each block taken in engine B read inside the region
            # (region_witness): the evidence that took it in.
            witnessed: dict[int, str] = {}
            if source == "nominated" and agreed_aligned:
                if render_size is None:
                    from PIL import Image
                    with Image.open(job.render) as image:
                        render_size = image.size
                region = sorted([k] + [j for j in rebuild_region(
                    blocks, k, render_size, agreed_aligned,
                    witness=lambda j, k=k: witnessed.setdefault(
                        j, region_witness(blocks, as_settled, starts, k, j)),
                    nominated={n["block"] for n in table_nominations}) if j not in absorbed_into])

            def ask_region(region: list[int]) -> tuple[str, str, list, str | None,
                                                      dict[str, Any], dict[int, str]]:
                """The rebuild of REGION: its lock, its merged text, the runs
                the lock added, the table or None, the record, and what of
                each block taken in the table must hold."""
                text = ("\n".join(spans[j] for j in region) if spans is not None
                        else "".join(piece for x, piece in zip(segments, pieces) if x.get("window") == k))
                merged_text, lock_runs = text, []
                if source == "nominated":
                    # Decision D20: a nominated block's table may hold what either
                    # engine read there, each character at the higher engine's
                    # count, not only what the merge wrote (rebuild_lock).  A
                    # block read from engine B's lines keeps its lines' text:
                    # engine A's reading of it is the grid refused for copies.
                    bounds = [((block_ends[j - 1] if j else 0), block_ends[j]) for j in region]
                    text, lock_runs = rebuild_lock(
                        merged_text, "\n".join(CJK(str(blocks[j]["text"])) for j in region),
                        "\n".join(engine_b_inside(as_settled, starts, lo, hi) for lo, hi in bounds),
                        [run for lo, hi in bounds for run in unwritten_readings(as_settled, starts, pieces, lo, hi)])
                boxes = [blocks[j]["box"] for j in region]
                x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
                union_box = [x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0]
                crop = upscale_crop(crop_block(job.render, union_box, pad=0.01)) or render_bytes
                # What the table must hold: what both engines read in each
                # block of the region, and of a block taken in (D22) also
                # what of it engine B read inside the region - both engines'
                # reading of it too, only not in one order.  An accepted
                # table stands for every block of its region, so a block
                # whose text is not in it is not the table's: a title engine
                # B read after the table was aligned against one of its rows,
                # agreed on nothing, and left the page with the table.
                allowed = Counter(CJK(text))
                need = {j: Counter(agreed_text[j]) | (Counter(witnessed.get(j, "")) & allowed)
                        for j in region if j != k}
                required = sum(need.values(), Counter(agreed_text[k]))
                table, rebuild = rebuild_table(client, crop, text, required, arguments.max_tokens,
                                               confirm=None if no_unread else lambda char, cell, at: confirm_spot(
                                                   region, char, cell, at),
                                               table_orientation=table_orientation_mode)
                held = {}
                for j, counts in need.items():
                    chars = []
                    for ch in CJK(str(blocks[j].get("text") or "")):
                        if counts[ch] > 0:
                            chars.append(ch)
                            counts[ch] -= 1
                    held[j] = "".join(chars)
                return text, merged_text, lock_runs, table, rebuild, held

            def confirm_spot(region: list[int], char: str, cell: str, at: int) -> dict[str, Any]:
                """裁決 asked about a character no engine read that a rebuilt
                table wrote (confirm_unread), on a crop of its spot: found by
                the cell's characters around it in engine A's reading of the
                region (unread_spot), cut as a disagreement's crop there is,
                or past the box where the spot is at its edge (unread_crop),
                and sealed next to the page."""
                asked: dict[str, Any] = {"char": char, "cell": cell[:40], "at": at}
                spot = unread_spot([CJK(str(blocks[j].get("text") or "")) for j in region], cell, at)
                if spot is None:
                    return dict(asked, confirmed=False,
                                why="no stretch of its cell around it stands once in engine A's reading")
                j, position, left, right = spot
                key = (region[j], position, left, right, char)
                if key in unread_verdicts:
                    return dict(asked, **unread_verdicts[key])
                seg = {"tag": "insert", "a": "", "b": char, "before": left, "after": right, "deletionGuard": False}
                spot_crop = unread_crop(job.render, render_bytes, blocks[region[j]], position, seg)
                unread_crops.append(job.out_json.with_name(
                    f"{job.out_json.stem}-table-unread-{len(unread_crops) + 1:02d}.png"))
                if spot_crop:
                    unread_crops[-1].write_bytes(spot_crop)
                # Its place (call_site): the spot's block and context, as its
                # adjudication record gives them, and where the region's text
                # begins in the merged text, as the table's entries give it.
                with call_site({"block": layout_index(blocks, region[j]), "contextBefore": left, "contextAfter": right,
                                "engineA": "", "engineB": "",
                                "mergedOffset": len(CJK("".join(spans[:region[0]]))) if spans is not None else None,
                                "crop": unread_crops[-1].name if spot_crop else None}):
                    _, verdict, used = adjudicate_segment(client, spot_crop, seg, arguments.max_tokens,
                                                          note=UNREAD_NOTE,
                                                          simplified=getattr(arguments, "simplified_script", False))
                add(used)
                read = CJK(verdict["resolved"]) if verdict["resolved_from"] in ("adjudicator", "adjudicator-empty") else None
                verdict.update({"crop": unread_crops[-1].name if spot_crop else None, "askedFor": "headerCharacters",
                                "block": layout_index(blocks, region[j])})
                adjudications.append(verdict)
                unread_verdicts[key] = dict(block=layout_index(blocks, region[j]), crop=verdict["crop"],
                                            answered=read or "",
                                            confirmed=bool(read) and variant_fold(read) == variant_fold(char),
                                            **({} if read is not None else {"why": "no verdict in 裁決's answer"}))
                return dict(asked, **unread_verdicts[key])

            try:
                text, merged_text, lock_runs, table, rebuild, held = ask_region(region)
                region_refused = None
                if table is None and len(region) > 1:
                    # The region refused (not a table, a block taken in not in
                    # the table, two tables in the answer): the nominated block
                    # alone, the rebuild D22 off asks, before the block is given
                    # up as text - a refused region must not cost the page the
                    # table D22 off keeps.  A made-up page of two adjacent tables
                    # the layout called text, asked as one region: the answer
                    # held two tables, refused, and the page kept neither; with
                    # D22 off each was accepted.
                    region_refused = {"region": [layout_index(blocks, j) for j in region if j != k],
                                      "reason": rebuild["reason"],
                                      **({"unreadAsked": rebuild["unreadAsked"]} if rebuild.get("unreadAsked") else {})}
                    refused_held = {j: held[j] for j in region if j != k}
                    region = [k]
                    text, merged_text, lock_runs, table, rebuild, held = ask_region(region)
                    if table is not None:
                        # The blocks the refused region named stay text beside
                        # the table rebuilt without them, and what both engines
                        # read of them must stay on the page (beside_table).
                        beside_table.update(refused_held)
            except PacketPending:
                rebuild_packet_pending = True
                continue
            # The table as the rebuild answered it, in its order: before its
            # orientation, what of the region's text it holds in order
            # (rebuilt_edges).
            answered = table or ""
            rebuild = {"block": layout, "from": source, **rebuild,
                       **({"region": [layout_index(blocks, j) for j in region if j != k]} if len(region) > 1 else {}),
                       **({"regionRefused": region_refused} if region_refused else {}),
                       **({"lockAdded": [run for _, run in lock_runs]} if lock_runs else {})}
            table_rebuilds.append(rebuild)
            beyond = Counter(CJK(table)) - Counter(CJK(merged_text)) if table is not None else Counter()
            table_added.update(beyond)
            # The characters no engine read that 裁決 confirmed (the user's
            # headerCharacters ruling, confirm_unread): theirs, not D20's.
            confirmed_unread = ([r for r in rebuild.get("unreadAsked") or [] if r.get("confirmed")]
                                if table is not None else [])
            beyond = beyond - Counter(r["char"] for r in confirmed_unread)
            at = len(CJK("".join(spans[:region[0]]))) if spans is not None else None
            merged_cjk = CJK(resolved)
            where = {"block": layout,
                     "contextBefore": merged_cjk[max(0, at - 8):at] if at is not None else "",
                     "contextAfter": merged_cjk[at + len(CJK(merged_text)):][:8] if at is not None else ""}
            if confirmed_unread:
                doubts.append({"kind": "table-unread-confirmed", "detail": (
                    f"TABLE CHARACTER NO ENGINE READ: block {layout} ({source}): the rebuilt table writes "
                    + "; ".join(f"「{r['char']}」 in 「{r['cell'][:12]}」 (crop {r['crop']})" for r in confirmed_unread)
                    + ", which neither engine read; 裁決 read the same on its spot - check it against the image")})
                decision_changes.append({
                    "decision": "headerCharacters", "rule": "table-unread-confirmed",
                    **where, "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                    "evidence": {"confirmed": [{key: r.get(key) for key in ("char", "cell", "at", "block", "crop",
                                                                             "answered")}
                                               for r in confirmed_unread]}})
            if table is not None and len(region) > 1:
                # Decision D22: the blocks taken in are the table's text.
                for j in region:
                    if j != k:
                        absorbed_into[j] = k
                        absorbed_held[j] = held[j]
                        blocks[j] = dict(blocks[j], label="table", nominated=True, absorbedInto=layout)
                decision_changes.append({
                    "decision": "D22", "rule": "rebuild-region", **where,
                    "engineA": "", "engineB": "", "after": table, "mergedOffset": at,
                    "evidence": {"region": [layout_index(blocks, j) for j in region if j != k],
                                 "held": [held[j] for j in region if j != k]}})
            if beyond:
                # Characters the table holds that the merge did not write there
                # (decision D20).
                decision_changes.append({
                    "decision": "D20", "rule": "rebuild-lock-both-readings", **where,
                    "engineA": "".join(run for engine, run in lock_runs if engine == "A"),
                    "engineB": "".join(run for engine, run in lock_runs if engine == "B"),
                    "after": "".join(beyond.elements()), "mergedOffset": at,
                    "evidence": {"lockAdded": [run for _, run in lock_runs],
                                 "locked": rebuild["locked"], "merged": len(CJK(merged_text))}})
            nominated_by = next((n for n in table_nominations if n["block"] == k and source == "nominated"), None)
            if table is not None and nominated_by is not None and nominated_by.get("witness"):
                # Decision D21: nominated only on the witness with this page's
                # mark glyphs left out (marks_left_out).
                decision_changes.append({
                    "decision": "D21", "rule": "nomination-witness-marks-left-out", **where,
                    "engineA": CJK(str(blocks[k]["text"])), "engineB": "",
                    "after": table, "mergedOffset": at,
                    "evidence": {"signal": nominated_by["signal"], **nominated_by["witness"]}})
            for nomination in table_nominations:
                if nomination["block"] == k:
                    nomination["outcome"] = rebuild["outcome"] + ": " + rebuild["reason"]
                    if len(region) > 1:
                        nomination["region"] = [layout_index(blocks, j) for j in region if j != k]
                    if region_refused:
                        nomination["regionRefused"] = region_refused
            if table is None:
                if not rebuild["reason"].startswith("not a table"):
                    doubts.append({"kind": "table-rebuild-refused", "detail": (
                        f"TABLE REBUILD REFUSED: block {layout} ({source}): {rebuild['reason']}")})
                if spans is not None and CJK(text) and blocks[k].get("label") in FURNITURE_LABELS:
                    # A table block gets no punctuation pass (FURNITURE_LABELS),
                    # so its text goes to the structure pass bare, and that pass
                    # may not add a mark: nothing reads the marks printed in it.
                    # Such a page went whole before it had block spans, and the
                    # formatter read them (sealed punctuation-whole-page).
                    # Measured: a textbook table whose four printed marks are
                    # all brackets, window-read, its rebuild refused - the page
                    # was sealed with nothing saying its marks were never read.
                    # A nominated block keeps its own label, and its pass.
                    a_marks = punctuation_counts(str(blocks[k].get("text") or "").translate(MARK_FORMS))
                    doubts.append({"kind": "table-marks-unread", "detail": (
                        f"TABLE MARKS NOT READ: block {layout} ({source}): no table was rebuilt "
                        f"({rebuild['reason'][:80]}), and a table block's text goes to the structure "
                        "pass with no punctuation pass and its marks locked"
                        + (f"; engine A read {sum(a_marks.values())} mark(s) in it ("
                           + "".join(f"{mark}×{n}" for mark, n in a_marks.items()) + ")" if a_marks else "")
                        + " - check the marks against the image")})
                continue
            as_written = table
            orientation_context = (f"rebuilt block {layout}, full-page boxes "
                                   f"{[blocks[j].get('box') for j in region]}; source grid:\n{table}")
            # Orientation is a separate image-backed decision after rebuilding
            # the physical grid.  Arithmetic and OCR order are hints only.
            try:
                oriented, how = orient_table(
                    table, render_bytes, client, arguments.max_tokens,
                    turn=lambda written: turn_records(written, draft2, ask=lambda grid: ask_first_record(
                        grid, render_bytes, client, arguments.max_tokens, context=orientation_context)),
                    table_orientation=table_orientation_mode, context=orientation_context)
                table_orientation_evidence.append(table_orientation_record(
                    as_written, oriented, how, render_bytes, table_orientation_mode, layout))
                if orientation_undecided(how):
                    doubts.append({"kind": "table-orientation-undecided", "detail": (
                        f"TABLE ORIENTATION UNDECIDED: block {layout}; kept as rebuilt: {how}")})
                if oriented != as_written:
                    decision_changes.append({
                        "decision": "D23", "rule": "rebuilt-table-oriented",
                        **where, "engineA": "", "engineB": "", "after": oriented, "mergedOffset": at,
                        "evidence": {"orientation": how[:300]}})
                table = oriented
                table, directions = fix_cell_direction(
                    table, client, arguments.max_tokens, block=layout, render_bytes=render_bytes,
                    context=orientation_context + "\norientation: " + how,
                    table_orientation=table_orientation_mode)
            except PacketPending:
                rebuild_packet_pending = True
                continue
            cell_direction = cell_direction + directions
            undecided = [record["read"] for record in directions if record["outcome"] == "undecided"]
            if undecided:
                doubts.append({"kind": "cell-direction-undecided", "detail": (
                    f"CELL DIRECTION UNDECIDED: block {layout}: {len(undecided)} label cell(s) kept as read "
                    f"({'、'.join(undecided[:6])}{'…' if len(undecided) > 6 else ''})")})
            table_orientation.append(f"block {layout}: rebuilt from the crop, {how}")
            # The brackets engine A printed in the region, written back
            # cell by cell (table_brackets): the bracket rule's write-back,
            # for a table no punctuation pass reads.
            table, brackets = table_brackets(table, [str(blocks[j].get("text") or "") for j in region])
            if brackets["inserted"] or brackets["moved"] or brackets["unplaced"]:
                rebuild["brackets"] = brackets
            if brackets["unplaced"]:
                doubts.append({"kind": "table-marks-unread", "detail": (
                    f"TABLE MARKS NOT READ: block {layout} ({source}): the table was rebuilt, but "
                    f"{len(brackets['unplaced'])} bracket(s) engine A read there stand in no cell of it "
                    f"({'; '.join(brackets['unplaced'][:4])}) - check the marks against the image")})
            unused = Counter(CJK(text)) - Counter(CJK(table))
            rebuild["unused"] = "".join(unused.elements())[:200]
            doubts.append({"kind": "table-rebuilt-unvouched", "detail": (
                f"TABLE REBUILT FROM THE IMAGE: block {layout} ({source}); no engine grid vouches for "
                f"its layout" + (f"; {sum(unused.values())} character(s) one engine read there are "
                                 f"not in it ({''.join(unused.elements())[:20]})" if unused else ""))})
            if blocks[k].get("label") != "table":
                blocks[k] = dict(blocks[k], label="table", nominated=True)
            if span_at is not None:
                # The table's edge text: what of its region's span the rebuilt
                # table does not hold, from the region's first character on and
                # back from its last, stays text beside it (rebuilt_edges,
                # table_edges).  The rest it does not hold is the unvouched
                # doubt's, as before.
                merged_cjk = CJK(resolved)
                at_region = [p for j in region for p in range(span_at[j], span_at[j + 1])]
                if other_readings is None:
                    other_readings = merged_other_readings(segments, pieces)
                    region_origin = merged_origins(segments, pieces)
                lead, trail = rebuilt_edges(
                    CJK(answered), "".join(merged_cjk[p] for p in at_region),
                    [other_readings[p] if p < len(other_readings) else "" for p in at_region],
                    [region_origin[p] if p < len(region_origin) else "" for p in at_region],
                    span_at[region[0] + 1] - span_at[region[0]], span_at[region[-1] + 1] - span_at[region[-1]])
                lo, hi = span_at[region[0]], span_at[region[-1] + 1]
                if lead or trail:
                    table_edge[k] = {"lead": (lo, lo + lead), "trail": (hi - trail, hi),
                                     "restated": table, "outside": False, "source": "rebuilt"}
            # In page order with the recovered tables (token numbers, joins).
            in_order = sorted(zip(table_sources + [k], tables + [table]), key=lambda pair: pair[0])
            table_sources, tables = [s for s, _ in in_order], [t for _, t in in_order]
        def beside_lost(text: str) -> str:
            """What TEXT lacks of the blocks beside a rebuilt table
            (beside_table), beyond what it lacks of the table blocks."""
            if not beside_table:
                return ""
            agreed_now = [absorbed_held.get(index, held_text) for index, held_text
                          in enumerate(agreed_block_text(blocks, agreed_segments))]
            both = table_characters_lost(blocks, agreed_now, text, book_heads, book_furniture, beside_table)
            tables_only = table_characters_lost(blocks, agreed_now, text, book_heads, book_furniture)
            short = Counter(both) - Counter(tables_only)
            out = []
            for char in both:
                if short[char] > 0:
                    out.append(char)
                    short[char] -= 1
            return "".join(out)

        # The whole-page formatter is told to write a doubled circle as 。。, as
        # the punctuator is.
        format_input, punct_locked = resolved, False
        format_system = FORMAT_SYSTEM
        punctuated: list[str] = []
        # The blocks listing entries with leaders, given engine A's line breaks
        # (break_lines).
        listed: set[int] = set()
        whole_page_hint = ""
        if spans is None and CJK(resolved):
            # No block-by-block pass: the formatter punctuates the whole page
            # with no census to check it against.  Not clean - measured: such a
            # page came out with ，：「」 throughout a book printed with 。 only,
            # and was sealed clean - and the formatter is at least given what
            # the probe saw this book print.
            doubts.append({"kind": "punctuation-whole-page",
                           "detail": "PUNCTUATED AS A WHOLE PAGE: "
                                     + whole_page_reason(blocks, draft, mode, order_text)
                                     + "; no mark count was taken, so check the marks against the image"})
            whole_page_hint = notation_hint(arguments)
            # Switch --keep-arabic-numerals: the figures the merge keeps go
            # into the characters the formatter is given, at their gaps (the
            # merged text holds characters only, so a gap is its index).
            for kept in sorted(figures_kept, key=lambda f: -f["merged"]):
                kept["placed"], kept["given"] = "written", kept["merged"]
                format_input = format_input[:kept["merged"]] + kept["figure"] + format_input[kept["merged"]:]
        if spans is not None:
            heads = set(profile_section(arguments, "layout").get("runningHeads") or [])
            hint = notation_hint(arguments)
            limit = density_limit(arguments)
            # Which blocks are punctuated at all is settled first; then every
            # (block, tile) pass runs at once.  The passes are independent - each
            # sees only its own crop and its own characters - so running them
            # together changes no answer, only how long the page takes.
            punctuated = [""] * len(blocks)
            to_punctuate: list[int] = []
            # Switch --learned-heads: the page's blocks in the learned running
            # head's band (learned_band).
            learned = learned_record(arguments)
            learned_here = learned_band(blocks, learned, job.scan_page) if learned else {}

            for index, (block, span) in enumerate(zip(blocks, spans)):
                if heads and is_running_head(str(block["text"]), sorted(heads)) and \
                        is_running_head(span, sorted(heads)) and \
                        head_size_matches(block, str(block["text"]), arguments):
                    # A running head the probe measured for this book (repeated
                    # on the page, or on most pages): furniture by evidence.
                    profile_furniture.append(CJK(span))
                    continue
                found = (learned_head_block(span, learned_here.get(index), learned, job.scan_page, str(block["text"]))
                         if learned else None)
                if found:
                    # Switch --learned-heads: a block of the band whose merged
                    # text is all the learned running head - the title with the
                    # label beside it, the label alone, the folio - is left out
                    # before punctuation, so neither the structure pass nor its
                    # fallback has it, whatever its layout label.  Off, it is
                    # punctuated (a folio labelled number handed over as it is)
                    # and the structure pass is asked to take the furniture out.
                    learned_dropped.add(index)
                    decision_changes.append({
                        "decision": "learnedHeads", "rule": "learned-head-block",
                        "block": layout_index(blocks, index),
                        "contextBefore": CJK("".join(spans[:index]))[-8:],
                        "contextAfter": CJK("".join(spans[index + 1:]))[:8],
                        "mergedOffset": sum(len(other) for other in spans[:index]),
                        "after": "", "evidence": {"label": block.get("label"), "text": span, **found}})
                    continue
                if not CJK(span) or block["label"] in FURNITURE_LABELS:
                    punctuated[index] = span
                    continue
                to_punctuate.append(index)

            # Engine A's own marks, tied to the merged text's characters: what a
            # pass whose answers are both refused is held to (agreed_punctuation,
            # its marks FALLBACK_MARKS).
            a_marks_merged = engine_marks_merged(draft, segments, pieces, blocks, vocabulary=FALLBACK_MARKS)
            span_starts = [0] + list(itertools.accumulate(len(span) for span in spans))
            # The rule agreedMarks (C1): a block whose marks both engines read
            # alike (agreed_marks) is written with them, its census and
            # punctuate calls not made.  Not on a book whose profile saw
            # doubled circles or emphasis marks: an engine's 。 there does not
            # say whether one circle or two is printed, and neither engine reads
            # the marks beside each character.
            notation = profile_section(arguments, "notation")
            agreed: dict[int, str] = {}
            if draft2 is not None and not (notation.get("doubled") or notation.get("emphasis")):
                found_a = engine_places(engine_gaps(draft)[0], segments, pieces, blocks)
                readings = [engine_reading(draft, found_a[0] if found_a else None, span_starts[-1]),
                            engine_reading(draft2, engine_b_places(engine_gaps(draft2)[0], segments, pieces),
                                           span_starts[-1])]
                for index in to_punctuate:
                    text = agreed_marks(spans[index], span_starts[index], readings)
                    if text is not None:
                        agreed[index] = text

            def cut(index: int) -> tuple[bool, list[tuple[bytes | None, int, int]], int]:
                """Whether block INDEX is cut in column groups, its (crop, start,
                end) parts, and how far a part's text may be off its crop at
                either end (a column's worth; 0 for a block punctuated whole,
                where only variant forms are folded - realign_answer).  A block
                the rule agreedMarks writes is one part, with no crop."""
                block, span = blocks[index], spans[index]
                if index in agreed:
                    return False, [(None, 0, len(span))], 0
                tiles = block_tiles(job.render, block, len(span))
                return bool(tiles), tiles or [
                    (upscale_crop(crop_block(job.render, block["box"], pad=0.012, pad_left=0.0))
                     or render_bytes, 0, len(span))], column_reach(job.render, block, len(span)) if tiles else 0

            def punctuate(item: tuple[int, int, bytes | None, int, int, bool, int]):
                index, tile_number, crop, lo, hi, tiled, reach = item
                if index in agreed:
                    # The rule agreedMarks (C1): the engines' marks, no call;
                    # the pass records it (censusNotAsked, rule engines-agree).
                    out = agreed[index]
                    return out, {"ok": True, "census": None, "reason": "",
                                 "censusNotAsked": {"rule": "engines-agree"},
                                 "marks": sum(punctuation_counts(out).values()), "doubled": out.count("。。"),
                                 "places": mark_places(out), "leaders": punctuation_counts(out)[LEADER],
                                 "bullets": sum(out.count(bullet) for bullet in BULLET_MARKS)}, []
                printed = (gaps_of(a_marks_merged, span_starts[index] + lo, hi - lo)
                           if a_marks_merged is not None else None)
                # The pass's place (call_site): its block, its tile and the
                # stretch it is asked - its punctuationPasses record's range,
                # or realigned.range where the answer read another stretch.
                with call_site({"block": layout_index(blocks, index), "mergedOffset": span_starts[index] + lo,
                                "range": [lo, hi], **({"tile": tile_number} if tiled else {})}):
                    return punctuate_part(client, arguments, crop, spans[index][lo:hi], hint, limit,
                                          printed=printed, block=(spans[index], lo, hi, reach))

            with cf.ThreadPoolExecutor(page_concurrency) as pool:
                cuts = in_context(pool, cut, to_punctuate)
                passes = [(index, tile_number, crop, lo, hi, tiled, reach)
                          for index, (tiled, parts, reach) in zip(to_punctuate, cuts)
                          for tile_number, (crop, lo, hi) in enumerate(parts)]
                answers = in_context(pool, punctuate, passes)
            # Each pass's text with the stretch of its block it read: its own
            # [lo, hi), or where its answer was realigned (realign_answer).
            pieces_out: dict[int, list[tuple[str, int, int, int, int]]] = {index: [] for index in to_punctuate}
            records_out: dict[int, list[dict[str, Any]]] = {index: [] for index in to_punctuate}
            for (index, tile_number, _, lo, hi, tiled, _), (out, record, used) in zip(passes, answers):
                # Not `for metrics in used`: that rebinds the page's running
                # total, which add() then sums into itself (every seal before
                # this held the last punctuation call's usage, doubled).
                for used_metrics in used:
                    add(used_metrics)
                read = (record.get("realigned") or {}).get("read") or [lo, hi]
                pieces_out[index].append((out, read[0], read[1], lo, hi))
                records_out[index].append(record)
                # range: where this pass sits in the block's text (the stretch
                # it read), so a later re-read of the whole block can still be
                # judged stretch by stretch against this pass's census.
                record.update({"block": layout_index(blocks, index), "label": blocks[index]["label"],
                               "range": list(read)})
                if tiled:
                    record["tile"] = tile_number
                punctuation_passes.append(record)
            for index in to_punctuate:
                punctuated[index], unread = assemble_passes(spans[index], pieces_out[index])
                if unread:
                    # Realigned passes left characters between them that no
                    # pass read: they stand, with no mark.
                    doubts.append({"kind": "punctuation-unread", "detail": (
                        f"PUNCTUATION UNREAD: block {layout_index(blocks, index)}: "
                        + ", ".join(f"「{spans[index][lo:hi][:12]}」" for lo, hi in unread[:4])
                        + " lie between column groups whose answers read the text their crops show "
                        "(realigned), and no answer read them; any marks there are missing - "
                        "check them against the image")})
            # Switch --keep-arabic-numerals: the brackets the bracket rule
            # writes, by block (brackets_by_block, the figures' own left out) -
            # at a figure's gap, the brackets of other print (figure_in_gap).
            rule_brackets: dict[int, Counter] = {}
            if bracket_rule:
                # The punctuator reads marks off the crop alone and was measured
                # keeping 3 of 14 bracket pairs that engine A had read, every one
                # right.  The brackets either engine read are written back into
                # the block they fall in; a bracket the punctuator wrote a
                # character or two away is taken for the same one, misplaced.
                # Switch --keep-arabic-numerals: the brackets of each figure
                # the merge writes go with the figure (figure_in_gap), and a
                # pair around a figure it does not keep is not written alone;
                # without the switch (or where it did not run on the page)
                # every pair is (engine_brackets_merged).
                figure_brackets = Counter()
                for kept in figures_kept:
                    figure_brackets.update({(kept["merged"], "（"): kept["figure"].count("（"),
                                            (kept["merged"], "）"): kept["figure"].count("）")})
                per_block = brackets_by_block(stream_a, stream_b, segments, pieces, spans,
                                              own=figure_brackets if figure_rule else None)
                rule_brackets = per_block
                for index in to_punctuate:
                    if index in per_block:
                        text, inserted, moved = place_brackets(punctuated[index], spans[index], per_block[index])
                        punctuated[index] = text
                        engine_brackets["inserted"] += inserted
                        engine_brackets["moved"] += moved
            for index in to_punctuate:
                own = [entry for entry in punctuation_passes if entry["block"] == layout_index(blocks, index)]
                punctuated[index], found = mark_changes(punctuated[index], span_starts[index],
                                                        layout_index(blocks, index), own, arguments)
                decision_changes.extend(found)
            if figures_kept:
                # Switch --keep-arabic-numerals: each figure the merge keeps is
                # written into its block at its gap, among the marks the block
                # pass and the bracket rule wrote there (place_arabic_figures).
                by_block: dict[int, list[tuple[dict[str, Any], int]]] = {}
                for kept in figures_kept:
                    k = kept["block"]
                    at = kept["merged"] - span_starts[k]
                    if k in absorbed_into or k in table_sources or blocks[k].get("label") == "table":
                        kept["placed"] = "in a table"
                    elif k not in to_punctuate and (CJK(spans[k]) or blocks[k]["label"] in FURNITURE_LABELS):
                        kept["placed"] = "in a block not punctuated"
                    elif not 0 <= at <= len(spans[k]) or (kept["at"] == 0 and at != 0):
                        kept["placed"] = "unplaced"
                    else:
                        by_block.setdefault(k, []).append((kept, at))
                for k, here in by_block.items():
                    # The brackets of other print at each figure's gap: those
                    # the bracket rule wrote there, or with the rule off those
                    # engine A read there besides the figure's.
                    punctuated[k], placed_how = place_arabic_figures(
                        punctuated[k], spans[k],
                        [(at, kept["figure"], kept["after"],
                          {bracket: rule_brackets.get(k, Counter())[(at, bracket)] for bracket in "（）"}
                          if bracket_rule else kept.get("otherBrackets") or {})
                         for kept, at in here])
                    for (kept, at), placed_as in zip(here, placed_how):
                        kept["placed"], kept["blockGap"] = placed_as, at
            places_a = engine_places(engine_gaps(draft)[0], segments, pieces, blocks)
            # A block listing entries with leaders (a contents page) keeps
            # engine A's line breaks: before the place in the merged text of
            # the first character of each of its printed lines, where that
            # place is exact (engine_places).  Before, not after the last
            # character of the line before: what engine B alone read between
            # them (a name's character read twice) stays with the line before,
            # with its leader and page number.  The structure pass is given
            # one printed line a line, and its fallback writes one a paragraph
            # (leader_list_breaks, break_lines).
            for index in to_punctuate:
                at = set()
                for g in leader_list_breaks(str(blocks[index]["text"])) if places_a else []:
                    if block_starts[index] + g in places_a[0]:
                        at.add(places_a[0][block_starts[index] + g] - span_starts[index])
                if at:
                    punctuated[index] = break_lines(punctuated[index], spans[index], at)
                    listed.add(index)
            # A recovered table never goes through the formatter: its block
            # stands in the text as one token line (place_recovered_tables).
            tokens = dict(zip(table_sources, table_tokens(len(tables))))
            # The table's edge text goes on its own lines around the token, with
            # the marks the engines read in it: a table block has no
            # punctuation pass, so what either engine read there is all there
            # is (marked_stretch; engine A's first, where it read the
            # characters beside a gap, else engine B's), in the order the
            # engine wrote them (marks_as_written), as a block written with the
            # engines' marks (agreed_marks) has them.  Nor a census, and the
            # bracket rule writes only the punctuated blocks
            # (tests/check_compositions.py).  A block a rebuilt table took in
            # (D22) is the table's text, and has none of its own.
            for index in list(table_edge):
                if index not in tokens or index in absorbed_into:
                    del table_edge[index]
            if any(found["lead"][1] > found["lead"][0] or found["trail"][1] > found["trail"][0]
                   for found in table_edge.values()):
                a_placed, b_placed = edge_placed
                engines_marks = [(engine_marks_merged(draft, segments, pieces, blocks, placed=a_placed,
                                                      written=True), a_placed),
                                 (engine_b_marks_merged(draft2, segments, pieces, placed=b_placed, written=True)
                                  if draft2 is not None else None, b_placed)]
                merged_cjk = CJK(resolved)
                # Whose reading each merged character is: a copy elsewhere
                # counts only where engine A's reading alone wrote it.
                origin = merged_origins(segments, pieces)
                # Each of engine A's blocks' stretch of the merged text: a
                # piece of edge text that is the whole of one is engine B's
                # reading of it (block_copy).
                blocks_at = ([(layout_index(blocks, j), span_at[j], span_at[j + 1]) for j in range(len(blocks))]
                             if span_at is not None else [])
                for index, found in table_edge.items():
                    written = []
                    for side, (lo, hi) in (("before", found["lead"]), ("after", found["trail"])):
                        parts = edge_verdicts(merged_cjk, lo, hi, draft, draft2,
                                              origin if len(origin) == len(merged_cjk) else None, b_lines,
                                              blocks_at, CJK(found["restated"]))
                        held = [dict(part["held"], text=merged_cjk[part["lo"]:part["hi"]], mergedOffset=part["lo"])
                                for part in parts if part["held"] is not None]
                        if held:
                            # Extra copies: read elsewhere on the page, which
                            # holds them there (edge_copies).
                            found.setdefault("heldElsewhere", {})[side] = held
                        # What is written: each run of pieces not held back,
                        # a line of its own.
                        runs: list[list[int]] = []
                        for part in parts:
                            if part["held"] is None and part["hi"] > part["lo"]:
                                if runs and runs[-1][1] == part["lo"]:
                                    runs[-1][1] = part["hi"]
                                else:
                                    runs.append([part["lo"], part["hi"]])
                        written.append("\n".join(marked_stretch(merged_cjk, start, end, engines_marks)
                                                  for start, end in runs))
                    edge_written[index] = (written[0], written[1])
            # A block a rebuilt table took in (D22) is the table's text.
            format_input = "\n".join(
                "\n".join(part for part in (edge_written.get(index, ("", ""))[0], tokens[index],
                                            edge_written.get(index, ("", ""))[1]) if part)
                if index in tokens else text
                for index, text in enumerate(punctuated) if index not in absorbed_into)
            if figures_kept:
                # Where each figure written into a block stands in the text the
                # structure pass is given, as CJK counts its characters: the
                # gap the structure pass must keep it at (restore_arabic_figures).
                running, first_of = 0, {}
                for index, text in enumerate(punctuated):
                    if index in absorbed_into:
                        continue
                    first_of[index] = running
                    running += len(CJK("".join(edge_written.get(index, ("", ""))) if index in tokens else text))
                for kept in figures_kept:
                    if (kept.get("placed") in FIGURE_AT_GAP and kept["block"] in first_of
                            and kept["block"] not in tokens):
                        kept["given"] = first_of[kept["block"]] + kept["blockGap"]
            format_system, punct_locked = STRUCTURE_SYSTEM, True
        placeholders = punct_locked and bool(tables)
        if recovery_packet_pending or rebuild_packet_pending:
            raise PacketPending("table-wave")

        # Before formatting, review the actual candidate including full tables.
        # This is the one bounded opportunity to recover regions or non-CJK
        # content dropped by the CJK merge, and to remove image-confirmed
        # furniture.  Never restore raw OCR digits by agreement alone.
        if placeholders:
            format_input, misplaced_input, _ = place_recovered_tables(format_input, tables)
            if misplaced_input:
                raise ProofreadError("content-recovery table expansion failed: " + misplaced_input)
            placeholders = False
        format_input, content_recovery = image_review(format_input, "content-recovery")
        # Keep a recovered table's confirmed repairs when the formatter copies
        # it.  A changed table count cannot be mapped to old block IDs safely;
        # final image coverage will verify those regions without such mapping.
        recovered_grids = [format_input[a:b] for a, b in table_spans(format_input)]
        source_table_mapping_current, recovery_table_correspondence = _content_fidelity.refresh_table_mapping(
            tables, recovered_grids)
        if source_table_mapping_current:
            tables = recovered_grids

        # Stage B - structure.  With punctuation locked the model may only add
        # headings, lists, tables and remove furniture; never touch a mark.
        table_hint = ""
        if placeholders:
            table_hint = (
                f"\n文本入面有 {len(tables)} 行表格記號（{'、'.join(table_tokens(len(tables)))}）："
                "每一行代表一個已經由引擎格仔定咗嘅表格，內容同方向都唔使你理。"
                "每行記號原封不動照抄一次，自己一行，擺喺個表喺頁上嘅位置（通常就係佢喺文本入面嘅位置）；"
                "唔好自己寫呢啲表，唔好刪走、改寫或者重複記號。\n")
        elif tables and source_table_mapping_current:
            # The whole-page path has no block spans to put a token in: the
            # formatter copies the recovered tables, with their label cells
            # as the engines read them (cells_as_read: the deletion guard
            # below compares runs of the merged text, table included), and
            # enforce_recovered_tables puts back what it changes, turning
            # the cells fix_cell_direction decided.
            joined = "\n\n".join(
                cells_as_read(table, [record for record in cell_direction
                                     if record.get("block") == layout_index(blocks, source)])
                for source, table in zip(table_sources, tables))
            orient_note = (
                "呢啲表保持**版面原印方向**，你唔使轉置，原樣照抄結構。\n"
                if table_orientation_mode == "as-printed"
                else "呢啲表已經**擺返做閱讀次序**（該轉置嘅已經轉咗），你唔使再轉，照抄結構。\n"
            )
            table_hint = (
                f"\n呢一頁有 {len(tables)} 個表格，下面係已經由引擎格仔定咗嘅表：\n---\n"
                f"{joined}\n---\n"
                f"{orient_note}"
                "冇合併格嘅表係 Markdown 表，照抄做 Markdown 表；有合併格（一格跨幾行或幾欄）嘅表係 "
                "HTML `<table>`，照抄做 HTML，rowspan／colspan 一個都唔准改，"
                "唔准拆返做 Markdown 表（Markdown 表表達唔到合併格），亦唔准寫成散文；"
                "兩個表就寫兩個表，唔准併埋一個；格入面嘅字一個都唔准改、唔准倒轉。\n"
                "表格入面嘅字已經包含喺上面『已定嘅字』入面，唔好喺表外重複寫一次。\n")
        if any(block.get("label") == "table" and index not in table_sources and index not in absorbed_into
               for index, block in enumerate(blocks)):
            # Tables the formatter must build from the image: the engine's grid
            # was refused (tablesRejected) or not used, so which way a record
            # runs is the formatter's to read off the page (unless as-printed).
            if table_orientation_mode == "as-printed":
                table_hint += (
                    "\n頁上有表格要你對住頁圖重建（引擎嘅格仔信唔過，冇畀你）。"
                    "**照頁圖印出嚟嘅橫行同直欄原樣排格網**（由上至下係行、由左至右係欄），"
                    "唔好將直欄轉置成橫行。\n"
                    "重建淨係搬字，一個字都唔准加、唔准刪、唔准換。\n")
            else:
                table_hint += (
                    "\n頁上有表格要你對住頁圖重建（引擎嘅格仔信唔過，冇畀你）。"
                    "按當前表格圖像確認欄名、紀錄軸同每筆紀錄嘅位置。"
                    "唔可以只因直排中文就假定最右先，亦唔可以靠合計方向決定轉置。"
                    "冇清楚圖像證據就保留物理格網，由整頁覆核記為未定。\n"
                    "重建淨係搬字，一個字都唔准加、唔准刪、唔准換。\n")
        layout = profile_section(arguments, "layout")
        layout_hint = ""
        if (layout.get("leavesPerPage") or 1) > 1:
            layout_hint += (f"本書係影印本：量度過，一頁通常印咗 {layout['leavesPerPage']} 葉原書，"
                            "每葉有自己嘅書眉同葉碼——全部係家具，刪走；正文按葉嘅次序接落去。\n")
        if layout.get("runningHeads"):
            layout_hint += ("以下字串喺本書量度到係書眉（同一頁重複或者差唔多每頁都有）："
                            + "、".join(layout["runningHeads"][:8]) + "；出現喺正文以外就刪走。\n")
        # Switch --learned-heads: a page whose every block with text was the
        # learned running head has nothing left for the structure pass, which
        # given nothing answers nothing, a failed pass (measured on the
        # offline replay of a book's sealed pages: its two pages printing the
        # running head alone were sealed structure-rejected).
        head_only = bool(learned_dropped) and punct_locked and not CJK(format_input) and not tables
        if head_only:
            # No format call, so none cut off (the seal's "degraded").
            formatted, m = "", {}
        else:
            formatted, m = client.ask_answering(
                format_system,
                (f"字同標點都已定（唔准改）：\n---\n{format_input}\n---\n{table_hint}{layout_hint}\n"
                 "睇頁圖，加版面結構（heading／段落／list／表格），保留全部來源覆核後嘅字、數字、算式同符號。標點原封不動。")
                if punct_locked else
                (f"已定嘅字（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}"
                 "睇頁圖，加返句讀同版面結構，保留全部來源覆核後嘅字、數字、算式同符號。"),
                render_bytes, arguments.max_tokens, kind="format")
            add(m)
        formatted = strip_fence(formatted)
        # An answer that is none - empty, or cut off at every effort - is a
        # failed pass, whatever the checks below would say of it (no_answer).
        silent = None if head_only else no_answer(formatted, m)
        # The tables go in where their tokens are before anything is checked,
        # so every check below sees the page as it will be written.
        placement_notes: list[str] = []
        misplaced = None
        if placeholders:
            formatted, misplaced, placement_notes = place_recovered_tables(formatted, tables)
        # Source recovery has already removed only image-confirmed furniture;
        # formatting has no remaining deletion exemption, for any script.
        sanctioned = format_input
        ok, reason = ((False, silent) if silent else (False, misplaced) if misplaced
                      else verify_characters(sanctioned, formatted))
        if ok and _content_fidelity.feature_losses(formatted, inventory):
            ok, reason = False, "formatter lost or displaced an image-confirmed source feature"
        # The recovered tables carry their own marks (、 separators, brackets)
        # that the block text does not, so they count as expected input.
        lock_input = format_input
        if ok and punct_locked and not punctuation_kept(lock_input, formatted):
            ok, reason = False, "changed punctuation: " + punctuation_delta(lock_input, formatted)
        if ok and punctuation_density(formatted) > density_limit(arguments):
            ok = False
            reason = (f"punctuation density {punctuation_density(formatted):.1f} per 100 "
                      "characters - a mark after almost every character")
        furniture = getattr(arguments, 'furniture', frozenset())
        margin_texts = margin_furniture(blocks)
        if not ok and "introduced" in reason:
            missing = Counter(CJK(resolved)) - Counter(CJK(formatted))
            hint = "".join(sorted(missing))[:20]
            if hint:
                reason += f"；合併文本入面對應嘅字係：{hint}"
        if not ok and not misplaced and not silent:
            # Before a retry, try the mechanical repair: if the formatter only
            # swapped glyphs, put the merge's characters back and keep its layout.
            # Never on no answer: a cut-off answer that also swapped a glyph
            # was "repaired" into an accepted page, and what came after the
            # cut was lost with no retry and no doubt (a tail shorter than
            # DELETED_RUN_LIMIT is not a deleted run).
            repaired = restore_merged_characters(sanctioned, formatted)
            if repaired is not None:
                ok2r, reason2r = verify_characters(sanctioned, repaired)
                if ok2r and _content_fidelity.feature_losses(repaired, inventory):
                    ok2r = False
                if ok2r:
                    formatted, ok = repaired, True
                    reason = "characters restored from the merge after a variant swap"
        # The deletion guard looks for contiguous runs of the merged text; a
        # table rotated into reading order breaks the engine's row-major runs
        # by design, and its characters are already checked cell by cell in
        # the restatement, so the guard runs on the prose blocks only - and on
        # the text written beside a table (table_edges), which is prose too.
        # A block --learned-heads left out is not the prose the structure pass
        # was given: its text is not looked for in the answer (and is not
        # furniture the guard lets a deleted run consist of - a folio of one
        # numeral would let any run with that character in it go).
        prose = ("".join(span if block.get("label") != "table" else "".join(edge_written.get(index, ("", "")))
                         for index, (block, span) in enumerate(zip(blocks, spans)) if index not in learned_dropped)
                 if spans is not None else resolved)
        known_heads = sorted(set(profile_furniture))
        dropped = (deleted_runs(prose, formatted, furniture=furniture, margin=margin_texts,
                                known=known_heads, whole=resolved, prose_only=spans is not None) if ok else [])
        if dropped:
            ok = False
            reason = ("deleted " + " / ".join(f"{len(d)} characters ({d[:18]}…)"
                                              for d in dropped[:3]))
        gone_beside = beside_lost(formatted) if ok else ""
        if gone_beside:
            # The text beside a table rebuilt without it (beside_table) may not
            # be left out, however short: the deletion guard's runs are no
            # measure of a header column of a few characters.
            ok = False
            reason = (f"deleted {len(gone_beside)} characters of the text beside a rebuilt table "
                      f"({gone_beside[:18]}…)")
        if not ok:
            # Why the first answer was refused, sealed: the page's text may be
            # the retry's, and without this nobody can tell afterwards what the
            # first answer did wrong (measured: a whole-page answer whose retry
            # was sealed, with nothing saying why the first was not).
            format_retry_reason = reason
            # Tell it exactly what it invented. A blind retry repeats the error;
            # naming the characters lets it drop them and keep the formatting.
            # The recovered tables go with it: a retry that never saw them
            # cannot copy them, and the page then loses its tables for good.
            # A pass that answered nothing is asked its own question again:
            # one cut off at the token budget from a lower effort
            # (NO_ANSWER_RETRY_EFFORT), told it thought too long; one whose
            # reply was empty at its own effort, told the reply was empty
            # (the budget was not what failed: the same question answered at
            # the top effort in other runs, no_answer).
            if silent:
                cut_off = no_answer_cut(silent)
                retry, m = ask_lower(
                    client, NO_ANSWER_RETRY_EFFORT if cut_off else "", format_system,
                    (f"字同標點都已定（唔准改）：\n---\n{format_input}\n---\n{table_hint}{layout_hint}\n"
                     if punct_locked else
                     f"已定嘅字（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}")
                    + ("你上一次諗得太耐，喺 token 上限之前冇寫出答案。唔使逐個字再諗，直接對住頁圖輸出。\n"
                       if cut_off else
                       "你上一次交咗個空白答案，一個字都冇寫。直接對住頁圖輸出。\n")
                    + ("睇頁圖，加版面結構（heading／段落／list／表格），保留全部來源覆核後嘅字、數字、算式同符號。標點原封不動。"
                       if punct_locked else "睇頁圖，加返句讀同版面結構，保留全部來源覆核後嘅字、數字、算式同符號。"),
                    render_bytes, arguments.max_tokens, kind="format-retry")
            else:
                retry, m = client.ask_answering(
                    format_system,
                    f"{'字同標點都已定' if punct_locked else '已定嘅字'}（唔准改）：\n---\n{format_input}\n---\n{table_hint}\n{whole_page_hint}"
                    f"你上一次嘅輸出被拒絕，原因：{reason}\n"
                    + ("如果係改咗標點：每一個符號都要原封不動，唔准加、刪、搬。\n" if punct_locked else "")
                    + ("如果係表格記號唔見咗或者重複咗：每一行記號都要原封不動照抄一次，唔好自己寫嗰啲表。\n"
                       if placeholders else "")
                    +
                    "如果係多咗漢字：嗰啲字唔喺原文入面，好可能你自己幫個 list "
                    "重新編號咗——唔好咁做，重複嘅編號照抄。\n"
                    "如果係標點太密：你差唔多逐個字都加咗符號。返去頁圖數清楚"
                    "真係印咗幾多個，冇印就唔好加。\n"
                    "如果係刪走咗字：本階段收到嘅文字必須保留，唔可以用格式化決定刪字。"
                    "家具已喺來源覆核處理；表格入面嘅格"
                    "(人名、職銜、籍貫嗰啲)係正文，唔係書眉，照留。\n\n"
                    "重新輸出：加句讀同結構，但一個漢字都唔准加，亦唔准刪正文。",
                    render_bytes, arguments.max_tokens, kind="format-retry")
            add(m)
            retry = strip_fence(retry)
            silent_retry = no_answer(retry, m)
            retry_notes: list[str] = []
            misplaced = None
            if placeholders:
                retry, misplaced, retry_notes = place_recovered_tables(retry, tables)
            ok2, reason2 = ((False, "retry: " + silent_retry) if silent_retry
                            else (False, "retry: " + misplaced) if misplaced
                            else verify_characters(sanctioned, retry))
            if ok2 and _content_fidelity.feature_losses(retry, inventory):
                ok2, reason2 = False, "retry lost or displaced an image-confirmed source feature"
            if ok2 and punct_locked and not punctuation_kept(lock_input, retry):
                ok2, reason2 = False, "retry still changed punctuation"
            if ok2 and punctuation_density(retry) > density_limit(arguments):
                ok2, reason2 = False, "retry still over-punctuated"
            still = (deleted_runs(prose, retry, furniture=furniture, margin=margin_texts,
                                  known=known_heads, whole=resolved, prose_only=spans is not None) if ok2 else [])
            if still:
                # Keep the better of the two rather than blocking the page, and
                # record what went missing so a later pass can find it.
                dropped = still
                ok2, reason2 = False, "retry still deleted " + " / ".join(still[:2])
            gone_beside = beside_lost(retry) if ok2 else ""
            if gone_beside:
                ok2, reason2 = False, ("retry still deleted the text beside a rebuilt table "
                                       f"({gone_beside[:18]}…)")
            if ok2 and retry.strip():
                formatted, ok, reason = retry, True, reason2 + " (after retry)"
                placement_notes = retry_notes
            else:
                retry_refusal = reason2 if not ok2 else "the retry answered nothing"
                rejected_retry = retry[:4000]
        rejected_output = None if ok else formatted[:4000]
        if ok and tables and source_table_mapping_current:
            # droppedRuns reports what the formatter dropped, so it is taken
            # on the text the deletion guard passed: enforcement only puts a
            # written table's cells in the recovered order and direction, which
            # breaks the merged text's runs without dropping anything.
            guarded = formatted
            formatted, table_notes, tables_missing, tables_disputed = enforce_recovered_tables(
                formatted, tables)
            table_notes = placement_notes + table_notes
        if ok and (formatted.strip() or head_only):
            if not punct_locked:
                # The whole-page formatter wrote the marks (no block pass).
                formatted, found = mark_changes(formatted, 0, None, [], arguments)
                decision_changes.extend(found)
            # Switch --keep-arabic-numerals: a figure the text given held at its
            # gap that the structure pass left out is written back there (the
            # character checks count characters, and a figure is none).
            given = [kept for kept in figures_kept if "given" in kept]
            if given:
                formatted, restored = restore_arabic_figures(
                    format_input, formatted, [(kept["given"], kept["figure"], kept["after"]) for kept in given])
                for kept, how in zip(given, restored):
                    kept["structure"] = how
            writer_text = formatted
            if tables_missing:
                # A table the engine structured and the checks vouched for is not
                # in the page as a table: the formatter wrote it as prose or left
                # it out.  deleted_runs cannot see this - table blocks are kept out
                # of its comparison - so the page is flagged here.
                doubts.append({"kind": "table-missing", "detail": (
                    "RECOVERED TABLE NOT IN OUTPUT: block(s) "
                    + ", ".join(str(layout_index(blocks, table_sources[i])) for i in tables_missing))})
            if tables_disputed:
                # The formatter's table and the recovered one disagree about the
                # layout (merged cells), or the formatter's holds text outside
                # its cells; the formatter's stands, and a person decides.
                doubts.append({"kind": "table-disputed", "detail": (
                    "RECOVERED TABLE LAYOUT DISPUTED: block(s) "
                    + ", ".join(str(layout_index(blocks, table_sources[i])) for i in tables_disputed))})
        else:
            # Keep the latest image-reviewed checkpoint after either formatter
            # fails.  Reconstructing from punctuated blocks or the older CJK
            # merge would lose confirmed additions and could reapply stale
            # source-table mappings.  Final coverage still checks this fallback.
            writer_text = format_input
            for kept in figures_kept:
                if "given" in kept:
                    kept["structure"] = "source-recovery fallback"
            doubts.append({"kind": "structure-rejected" if punct_locked else "formatter-rejected",
                           "detail": f"FORMATTER REJECTED: {reason}; image-reviewed input preserved"
                           + (f"; retry refused: {retry_refusal}" if retry_refusal else "")})
        # Every run of dots the page keeps is 「……」 (D4).  The block passes'
        # and the whole-page formatter's are written so and sealed where they
        # are punctuated; one a structure pass wrote with another number of
        # dots (its lock counts a run as one mark) or a recovered table holds
        # is written so here, and sealed as rewritten.
        writer_text, found = leader_rewrites(writer_text)
        decision_changes.extend(found)
        if spans is not None:
            # A block a rebuilt table took in (D22) counts what its table had
            # to hold of it: both engines read it, only not in one order.
            # And the blocks beside a table rebuilt without them (beside_table).
            table_characters = table_characters_lost(
                blocks, [absorbed_held.get(index, text)
                         for index, text in enumerate(agreed_block_text(blocks, agreed_segments))],
                writer_text, book_heads, book_furniture, beside_table)
            if table_characters:
                read_by = ("engine A read (the page's only reading)" if order_text == "A"
                           else "both engines read")
                doubts.append({"kind": "table-characters-lost", "detail": (
                    f"TABLE CHARACTERS NOT IN OUTPUT: {len(table_characters)} "
                    f"character(s) {read_by} in the table blocks"
                    + (" and the blocks beside a table rebuilt without them ("
                       + ", ".join(str(layout_index(blocks, j)) for j in sorted(beside_table)) + ")"
                       if beside_table else "")
                    + f" ({table_characters[:30]})")})
        if table_edge:
            # The table's edge text (table_edges): each table whose block's span
            # held text beside its cells, or whose restatement the rule
            # changed, sealed in decisionChanges; the text written beside a
            # table is a doubt, whether the page still holds it or not.
            merged_cjk, page_cjk = CJK(resolved), CJK(writer_text)

            def read_there(lo: int, hi: int) -> dict[str, str]:
                """What each engine read of the merged text's [LO, HI): its own
                characters where its reading's place there is exact."""
                return {name: "".join(placed[at] for at in range(lo, hi) if at in placed)
                        for name, placed in zip(("engineA", "engineB"), edge_placed)}

            # The lines of engine B's that are cells of its own tables.
            b_cells = engine_b_table_lines(draft2) if draft2 is not None else set()

            def not_written(held: Mapping[str, Any]) -> str:
                """Why a piece of the edge text was held back (edge_verdicts)."""
                piece = held["text"]
                head = f"「{piece[:24]}{'…' if len(piece) > 24 else ''}」 not written - "
                if held.get("with"):
                    return (head + "one character or figures alone, which say nothing by themselves, "
                            f"go with 「{held['with'][:12]}」 beside them")
                if held.get("tableCells"):
                    return (head + "it stands whole in the table's own cells, and engine B read it more often "
                            "than engine A, who read it there (page/engine A/engine B: "
                            + ", ".join(held["stretches"][:1]) + "): engine B's second reading of the cells, "
                            "it may be")
                if held.get("wholeBlock") is not None:
                    return (head + f"it is the whole of block {held['wholeBlock']}, which engine A alone read "
                            "there (page/engine A/engine B: " + ", ".join(held["stretches"][:1]) + ")")
                return (head + f"{held['share']:.0%} of it is in stretches the page would hold more often "
                        "than either engine read them (page/engine A/engine B: "
                        + ", ".join(held["stretches"][:4]) + "), each standing elsewhere on the page as "
                        "engine A's reading alone")

            beside_notes: list[str] = []
            for index, found in sorted(table_edge.items()):
                layout = layout_index(blocks, index)
                written = edge_written.get(index, ("", ""))
                sides = []
                elsewhere = found.get("heldElsewhere") or {}
                for side, (lo, hi), text in (("before", found["lead"], written[0]),
                                             ("after", found["trail"], written[1])):
                    if not text and side not in elsewhere:
                        continue
                    read = read_there(lo, hi)
                    kept = bool(text) and CJK(text) in page_cjk
                    # What of it engine B read in its own table's cells: its
                    # reading of the table's first (last) cells, it may be,
                    # set beside engine A's in the table.
                    in_cells = ("".join(merged_cjk[at] for at in range(lo, hi) if b_lines[at] in b_cells)
                                if b_lines is not None and b_cells else "")
                    sides.append({"side": side, "written": text, "mergedOffset": lo, **read, "onPage": kept,
                                  **({"heldElsewhere": elsewhere[side]} if side in elsewhere else {}),
                                  **({"inEngineBTable": in_cells} if in_cells else {})})
                    shown = merged_cjk[lo:hi]
                    state = ("written" if kept else "written, and not on the page: the structure pass left it out")
                    beside_notes.append(
                        f"block {layout}: 「{shown[:24]}{'…' if len(shown) > 24 else ''}」 {side} the table "
                        f"({found['source']}; engine A read {len(read['engineA'])} of its {hi - lo} character(s) "
                        f"there, engine B {len(read['engineB'])}): "
                        + ("; ".join(not_written(held) for held in elsewhere[side][:4])
                           + (f"; the rest {state} (「{CJK(text)[:24]}」)" if text else "")
                           if side in elsewhere else state)
                        + (f"; engine B read 「{in_cells[:24]}{'…' if len(in_cells) > 24 else ''}」 of it in its own "
                           "table's cells - its reading of the table's own cells, it may be, where the table holds "
                           "engine A's" if in_cells else ""))
                if not any(entry["written"] for entry in sides):
                    # Nothing written beside the table: the page's text is the table's.
                    continue
                at = sides[0]["mergedOffset"]
                decision_changes.append({
                    "decision": "textLoss", "rule": "table-edge-text",
                    "block": layout, "contextBefore": merged_cjk[max(0, at - 8):at],
                    "contextAfter": merged_cjk[at:at + 8],
                    "engineA": "".join(entry["engineA"] for entry in sides),
                    "engineB": "".join(entry["engineB"] for entry in sides),
                    "after": "\n\n".join(part for part in (written[0], found["restated"], written[1]) if part),
                    "mergedOffset": at,
                    "evidence": {"source": found["source"], "beside": sides,
                                 "foundOutsideBlock": found["outside"]}})
            if beside_notes:
                doubts.append({"kind": "table-edge-text", "detail": (
                    "TEXT AT A TABLE'S EDGE: the table block's merged text holds it before the table's first "
                    "cell or after its last, and no cell of the table does: " + "; ".join(beside_notes[:6])
                    + " - check it against the image")})
        # The steps below each passed or failed silently before: a refused or
        # miscounted punctuation pass left its stretch bare or short, and a
        # merge that placed one reading twice kept both, and the page was
        # sealed clean either way (punctuation_doubts, extra_copies).
        doubts.extend(punctuation_doubts(punctuation_passes))
        doubts.extend(extra_copy_doubts(writer_text, draft, draft2))
    # Switch --loop-detector: each call with a rung given up in a loop.
    decision_changes.extend(loop_cut_changes(loop_cuts))
    # Switch --keep-arabic-numerals: each figure the merge kept, where it wrote
    # the figure or left out engine B's reading of it; and a doubt where it
    # could write neither the figure nor what was there before.
    unwritten = []
    for kept in figures_kept:
        # Not in the page after all: the structure pass left it out where
        # its place was not found, or the page fell back to text without it
        # (the formatter rejected, or its block left out).
        lost = kept.get("structure") in ("unplaced", "formatter rejected", "block left out")
        written = kept.get("placed") in FIGURE_WRITTEN and not lost
        kept["record"].update(placed=kept.get("placed"), **({"structure": kept["structure"]}
                                                           if "structure" in kept else {}))
        if written or kept["replaced"]:
            # A change: the figure written, or characters the merge would
            # write at its gap without it written as nothing.
            decision_changes.append({
                "decision": "keepArabicNumerals", "rule": "arabic-figure-kept",
                "block": kept["record"]["block"], "contextBefore": kept["record"]["contextBefore"],
                "contextAfter": kept["record"]["contextAfter"], "engineA": kept["figure"],
                "engineB": kept["engineB"],
                "after": kept["figure"] if written else "", "mergedOffset": kept["merged"],
                "evidence": {"how": kept["how"], "placed": kept.get("placed"),
                             **({"structure": kept["structure"]} if "structure" in kept else {})}})
        if kept.get("placed") not in FIGURE_IN_BLOCK or lost:
            unwritten.append(f"…{kept['record']['contextBefore'][-4:]}[{kept['figure']}]"
                             f"{kept['record']['contextAfter'][:4]}… ({kept.get('structure') or kept.get('placed')})")
    # A figure or mark engine A read in brackets that the merge did not keep
    # (bracketed_pairs): the bracket rule does not write its brackets alone
    # (unpaired_brackets), so where the page holds no copy of it it is lost,
    # and said so (bracketed_unwritten).
    unkept = bracketed_unwritten(page_bracketed, figures_kept, writer_text or "", figure_why)
    if unwritten or unkept:
        doubts.append({"kind": "arabic-figure-unwritten", "detail": (
            "ARABIC FIGURE NOT WRITTEN: "
            + "; ".join(part for part in (
                f"{len(unwritten)} figure(s) engine A read, which the merge kept "
                "(--keep-arabic-numerals), could not be written at their place: " + "; ".join(unwritten[:6])
                if unwritten else "",
                f"{len(unkept)} figure(s) or mark(s) engine A read in brackets, which the merge did not keep, "
                "are not on the page (the bracket rule writes no pair alone): " + "; ".join(unkept[:6])
                if unkept else "") if part)
            + " - check the numbers against the image")})

    # Final full-image coverage is after table enforcement and every mechanical
    # rewrite.  A rejected formatter fallback is still a candidate, never
    # evidence of full-page coverage.  Unresolved critical losses fail the page
    # explicitly and cannot be resumed as a completed seal.
    writer_text, coverage_review = image_review(writer_text, "coverage-review")
    if not coverage_review["complete"]:
        raise ProofreadError("full-page coverage unresolved: " + json.dumps(coverage_review, ensure_ascii=False))
    final_tables = [writer_text[a:b] for a, b in table_spans(writer_text)]
    table_mapping_current, table_correspondence = _content_fidelity.refresh_table_mapping(tables, final_tables)
    table_mapping_current = source_table_mapping_current and table_mapping_current
    if table_mapping_current:
        tables = final_tables
    for evidence in table_orientation_evidence:
        evidence["status"] = ("active" if evidence.get("outputSha256") in
                              {sha256_bytes(t.encode("utf-8")) for t in final_tables} else "historical")
        evidence["finalCoverageSha256"] = coverage_review["result_sha256"]
    model_sites = variant_model_sites(disagreements, arguments)
    record: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "scanPage": job.scan_page,
        "status": "complete",
        "textAuthority": TEXT_AUTHORITY,
        "generatedAt": utc_now(),
        "model": client.model,
        "reasoningEffort": client.effort,
        "renderFile": str(job.render),
        "auditorClean": not doubts,
        "mode": mode,
        "pageInventory": inventory,
        "contentRecovery": content_recovery,
        "coverageReview": coverage_review,
        "engineDisagreements": disagreements,
        "auditorReport": "; ".join(d["detail"] for d in doubts),
        "doubts": doubts,
        "adjudications": adjudications,
        "punctuationPasses": punctuation_passes,
        "engineBrackets": engine_brackets,
        "profileFurnitureDropped": profile_furniture,
        # Genuine substitutions only - both engines produced text - the same
        # population the profile measured, so the two can be compared.
        "engineTally": dict(Counter(a.get("winner", "undecided") for a in adjudications
                                    if a.get("trigger") == "engine-disagreement"
                                    and CJK(a.get("draft_reading", "")) and CJK(a.get("writer_reading", "")))),
        "variantUnruled": variant_unruled,
        # The sites the model's vote decided (variant_model_sites), on books it ruled.
        **({"variantRuledByModel": model_sites} if model_sites is not None else {}),
        # An engine-A block whose reading was a repetition loop engine B does not
        # share, set aside (quarantine_looped_block): kept here, as a page's
        # discarded reading is, so the judgement can be undone.
        "engineALoopsDropped": loops_dropped,
        # Each text change a decision-dependent rule made (see decision_changes).
        "decisionChanges": decision_changes,
        # Switch --keep-arabic-numerals: every figure engine A read that may
        # number something (arabic_figures), kept or not and why.  Only when on.
        **({"arabicFigures": arabic_figures_seen} if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Each lopsided replacement split (split_lopsided), and what was written for it.
        "lopsidedSplits": [{"engineA": split["segment"]["a"], "engineB": split["segment"]["b"],
                            "contextBefore": split["segment"]["before"], "contextAfter": split["segment"]["after"],
                            "how": split["how"], "written": split.get("written")} for split in lopsided],
        # Places where the merge dropped more than ANSWER_DELETION_LIMIT characters
        # one engine read that the merged text holds nowhere, and the rules that
        # settled them (readings_dropped): each a reading-dropped doubt.
        "readingsDropped": readings_lost,
        "formatterRejectedOutput": rejected_output,
        "formatterRetryReason": format_retry_reason,
        # The retry's own refusal and answer, when it was refused as well.
        "formatterRetryRefusal": retry_refusal,
        "formatterRejectedRetry": rejected_retry,
        "needsResearch": front_matter_research(blocks, resolved) if mode.startswith("merge") else [],
        "usage": metrics,
        "punctuationDensity": round(punctuation_density(writer_text), 2),
        "droppedRuns": (deleted_runs(prose if prose is not None else resolved,
                                     guarded if guarded is not None else writer_text,
                                     furniture=getattr(arguments, "furniture", frozenset()),
                                     margin=margin_furniture(blocks),
                                     known=sorted(set(profile_furniture)), whole=resolved,
                                     prose_only=prose is not None and spans is not None)
                        if draft2 is not None else []),
        # Evidence, not decoration: a refused table is a page where the engine
        # found a grid we would not vouch for, and someone may want to look.
        # A merge-time loss is invisible to droppedRuns, which only compares the
        # merged text against the formatted text.  This compares the merged text
        # against what each engine actually read.  A merge longer than both
        # engines is NOT a doubt by itself: the merge keeps text either engine
        # read alone, so it is longer on many pages (measured: 31 of the 77
        # sealed pages that record it, by 1 to 186 characters).  Every page of
        # the 77 with an extra copy is among the 31, but only 9 of the 31 hold
        # one, and a page 28 characters over held none while one 3 over did.
        # Text placed twice is looked for directly (extra_copies).
        "engineCoverage": {
            "engineA": engine_a_characters,
            "engineB": engine_b_characters,
            "merged": len(CJK(resolved)) if draft2 is not None else None,
        },
        "tablesRecovered": len(tables),
        # Each recovered table with the block it came from (its layout index,
        # as the stitch reads it against engine A's layout file), as it is to
        # stand on the page (a whole-page pass was offered it with its label
        # cells as read, cells_as_read); tablesEnforced, tablesMissing and
        # tablesDisputed say what became of it on the page.
        "tables": [{"block": layout_index(blocks, block),
                    "format": "html" if "<table" in table else "markdown",
                    "text": table} for block, table in zip(table_sources, tables)],
        "tablesEnforced": table_notes,
        "tablesMissing": [layout_index(blocks, table_sources[i]) for i in tables_missing],
        "tablesDisputed": [layout_index(blocks, table_sources[i]) for i in tables_disputed],
        # Characters both engines read in the table blocks that the page lacks -
        # also for a refused table, which the formatter rebuilds
        # (table_characters_lost).
        "tableCharactersLost": table_characters,
        "tableOrientation": table_orientation,
        "tableOrientationEvidence": table_orientation_evidence,
        "tableMappingCurrent": table_mapping_current,
        "tableCorrespondence": table_correspondence,
        "recoveryTableCorrespondence": recovery_table_correspondence,
        "finalTables": [{"text": table, "sha256": sha256_bytes(table.encode("utf-8")),
                         "format": "html" if "<table" in table else "markdown"} for table in final_tables],
        # Figures whose group marks were taken from engine B (restate_table).
        "tableFigureGrouping": table_grouping,
        # The label cells asked which way they read, with both answers
        # (fix_cell_direction).
        "cellDirection": cell_direction,
        # Blocks engine B reads in another order, nominated as tables the
        # layout model missed (nominate_tables), with how many of their
        # disagreements settle_by_union settled and how many it left to the
        # usual way; and every table rebuilt from the image with its
        # characters locked (rebuild_table): where from, how many characters
        # were allowed and required, the outcome.
        "tableNominations": [dict(nomination, block=layout_index(blocks, nomination["block"]))
                             for nomination in table_nominations],
        "tablesRebuilt": table_rebuilds,
        "tablesRejected": tables_rejected,
        "degraded": bool(m.get("degraded")),
        "seconds": round(time.monotonic() - started, 1),
        # Where the page's time went, per kind of model call.  Seconds are the
        # caller's wall time per call (they overlap when calls run in parallel),
        # waitSeconds the time spent queued for a free lane.
        "calls": summarize_calls(ledger),
    }
    record["provenance"] = {
        "renderSha256": sha256_file(job.render),
        "draftSha256": sha256_file(job.draft_text),
        **decision_provenance(arguments),
        "markdownSha256": sha256_bytes(writer_text.encode("utf-8")),
    }
    record["provenance"]["jsonPayloadSha256"] = sha256_bytes(canonical_json_bytes(record))
    seal_page(job, writer_text.encode("utf-8"), record)
    return record


@contextlib.contextmanager
def output_lock(directory: Path) -> Iterator[None]:
    """An exclusive lock on OUTPUT_DIR/.table-stitch.lock.

    The table stitch (stitch_tables.py) holds it from reading the sealed pages
    to writing its plan; a page's seal is written under it too.  Otherwise a
    page proofread again by another run sharing the directory could be sealed
    inside the stitch's window, and the stitch would write a plan built from
    the page before it over the new one: the new proofreading lost, and the
    merged table carrying the old page's cells.
    """
    with open(directory / ".table-stitch.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def seal_page(job: PageJob, markdown: bytes, record: Mapping[str, Any]) -> None:
    """Write a page's Markdown and its seal, under the output directory's lock."""
    with output_lock(job.out_json.parent):
        job.out_md.write_bytes(markdown)
        job.out_json.write_bytes(canonical_json_bytes(record))


class AgentTransportParser(argparse.ArgumentParser):
    """External model routing always needs an explicit command-line choice.

    Native agent sessions answer packet questions. Ambient HTTP settings must
    not redirect a skill invocation to a different model or endpoint.
    """

    def parse_args(self, args=None, namespace=None):
        arguments = super().parse_args(args, namespace)
        if arguments.backend == "http":
            if not arguments.endpoint or not arguments.endpoint.strip():
                self.error("--backend http requires an explicit --endpoint; PROOFREAD_ENDPOINT is not used")
            arguments.model = arguments.model or os.environ.get("PROOFREAD_MODEL") or DEFAULT_MODEL
        else:
            if arguments.endpoint is not None:
                self.error("--endpoint requires explicit --backend http")
            arguments.model = arguments.model or DEFAULT_MODEL
        return arguments


def build_parser() -> argparse.ArgumentParser:
    parser = AgentTransportParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("render_directory", type=Path)
    parser.add_argument("draft_directory", type=Path, help="OCR runner output (page-NNNN.txt/.json)")
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--profile", type=Path,
                        help="book-profile.json from scripts/book_profile.py (default: "
                             "book-profile.json next to the draft directory, if present)")
    parser.add_argument("--variant-rulings", type=Path,
                        help="per-pair variant-form rulings (default: variant-rulings.json next "
                             "to the profile, if present)")
    parser.add_argument("--draft2-directory", type=Path,
                        help="optional second OCR draft directory for alignment and disagreement checks; "
                             "both drafts still require whole-image coverage checks")
    selector = parser.add_mutually_exclusive_group(required=False)
    selector.add_argument("--pages", help="comma-separated pages/ranges, e.g. 1,3-5")
    selector.add_argument("--first-page", type=int)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"pages processed concurrently (default {DEFAULT_WORKERS}); "
                             "packet answers may be supplied sequentially or by optional workers")
    parser.add_argument("--backend", choices=("packet", "http"),
                        default="packet",
                        help="model transport: 'packet' (default) writes checkpoint-replay wave bundles "
                             "for the invoking agent or optional workers and exits 10 until answered; "
                             "'http' requires an explicit --endpoint")
    parser.add_argument("--packets-dir", type=Path, default=None,
                        help="packet checkpoint directory for --backend packet "
                             "(default: <output_directory.parent>/packets)")
    parser.add_argument("--max-bundles", type=int,
                        default=int(os.environ.get("PROOFREAD_MAX_BUNDLES") or 8),
                        help="maximum question bundle files per wave in --backend packet (default 8)")
    parser.add_argument("--table-orientation", choices=("auto", "as-printed"),
                        default=os.environ.get("PROOFREAD_TABLE_ORIENTATION", "auto"),
                        help="table orientation policy: 'auto' (default) puts vertical-column records into "
                             "logical rows; 'as-printed' keeps the printed row/column grid without transposing")
    parser.add_argument("--endpoint", default=None,
                        help="explicit HTTP endpoint; required with --backend http (no environment fallback)")
    parser.add_argument("--model", default=None,
                        help="HTTP model or packet provenance label; PROOFREAD_MODEL applies only to explicit HTTP")
    parser.add_argument("--reasoning-effort", default=os.environ.get("PROOFREAD_EFFORT", DEFAULT_EFFORT),
                        help=f"low|medium|xhigh, or 'none' to omit (default {DEFAULT_EFFORT})")
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--max-adjudications", type=int, default=24,
                        help="per-page cap on escalated forced-choice calls")
    parser.add_argument("--no-learned-heads", dest="learned_heads", action="store_false", default=True,
                        help="switch the learned running head off (on by default since the round-4 benchmark, "
                             "the user's decision of 2026-09-30; ROUND4_DEFAULTS): the running head learned by "
                             "where it is printed "
                             "(learn_heads): the profile's (book_profile.py, unless --no-learned-heads there) or, "
                             "for a profile measured without it, learned from engine A's layout of every page in "
                             "the draft directory - "
                             "the stems the profile measured, the labels printed beside them on several pages "
                             "and their families (a volume's number), the folio sequence, where the band stands "
                             "on odd and even pages.  A block in the page's band whose merged text is all the "
                             "learned head (a stem, a label, the folio beside them; the folio alone in an "
                             "anchor's column or row) is left out before punctuation and so out of the structure "
                             "pass and its fallback, whatever its layout label; text only one engine read that "
                             "is the learned head with a stem, at an edge of engine A's blocks or between two, is "
                             "left out before 裁決")
    parser.add_argument("--no-keep-arabic-numerals", dest="keep_arabic_numerals", action="store_false",
                        default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) writing "
                             "an Arabic figure engine A read - a list item's (1), "
                             "a contents entry's 12, a heading's 5. - into the merged text at its place where "
                             "it is the only reading of its print (arabic_figure_kept): engine B read nothing "
                             "there, or marks, or glyphs this book's drafts show engine B writing for figures "
                             "or marks, which are written as nothing (figure_aliases, mark_aliases), or 裁決 "
                             "read the figure or no character there; and only a figure that continues a "
                             "numbering, the figure engine A read before it one less or the one after it one "
                             "more (figures_confirmed), or a contents entry's page number (ending a line "
                             "that holds a leader).  No character both engines read is replaced")
    parser.add_argument("--no-loop-detector", dest="loop_detector", action="store_false", default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) the loop "
                             "detector: stream every model request and watch its reasoning as it "
                             "comes: when the reasoning ends in one passage written three times back to back, "
                             "word for word, at least 800 characters repeating (_loop_watch.RULE), the "
                             "request is given up there and the effort ladder treats it exactly as a cut at "
                             "max_tokens - the next rung, never the same rung again (Client.ask_answering); "
                             "the answer after the reasoning is never watched.  Measured on 68 top-effort "
                             "replays: every loop found, none of the 49 that ended with an answer fired on")
    parser.add_argument("--no-neighbour-guard", dest="neighbour_guard", action="store_false", default=True,
                        help="switch off (on by default since the round-4 benchmark; ROUND4_DEFAULTS) the "
                             "neighbour guard: do not write a 裁決 answer that is neither engine's reading "
                             "and is the text the merged text has within NEIGHBOUR_WINDOW characters of the "
                             "spot, which neither engine read at the spot (neighbour_copy): 裁決 read the "
                             "neighbour, not the spot.  The reading of the engine the page falls back on stands, "
                             "as that engine read it (on a D3 question, the reading of the engine that read the "
                             "text), with a neighbour-copy doubt")
    parser.add_argument("--page-concurrency", type=int, default=DEFAULT_PAGE_CONCURRENCY,
                        help=f"model calls one page runs at once: its 裁決, its "
                             f"punctuation passes, its shape re-checks "
                             f"(default {DEFAULT_PAGE_CONCURRENCY}; 1 = one after another)")
    parser.add_argument("--max-inflight", type=int,
                        default=int(os.environ.get("PROOFREAD_MAX_INFLIGHT") or 0) or None,
                        help="requests on the wire at once across all pages (env "
                             "PROOFREAD_MAX_INFLIGHT; default --workers). Set it to the "
                             "server's --max-concurrency: a request the server cannot "
                             "admit waits in its queue and fails after its pending timeout"),
    parser.add_argument("--no-unread-table-characters", action="store_true",
                        help="the user's headerCharacters ruling (2026-09-27) off: a rebuilt table that "
                             "writes a character no engine read in its region is refused, as before, "
                             f"instead of standing when there are at most {UNREAD_TABLE_LIMIT} such places "
                             "and 裁決 reads the same character on a crop of each (confirm_unread)")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="sampling temperature (default 0: greedy, and therefore "
                             "reproducible). Raise it only to explore alternatives; "
                             "any measurement taken above 0 is not comparable.")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="seconds a read of the server's answer may wait: the whole answer of a request "
                             "that is not streamed, the next event of one that is (the loop detector, on unless "
                             "--no-loop-detector), so a streamed request may take longer in all (default 1800)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def rendered_pages(render_directory: Path) -> list[int]:
    """Every scan page that has a page-NNNN.png render; the default selection."""
    pages: list[int] = []
    for path in render_directory.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d{4})\.png", path.name)
        if match:
            pages.append(int(match.group(1)))
    return sorted(pages)


def parse_pages(arguments: argparse.Namespace, render_directory: Path) -> list[int]:
    if arguments.pages is not None:
        if arguments.last_page is not None:
            raise ProofreadError("--last-page cannot be combined with --pages")
        pages: set[int] = set()
        for token in arguments.pages.split(","):
            token = token.strip()
            if not token:
                continue
            match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", token)
            if not match:
                raise ProofreadError(f"invalid page selector: {token!r}")
            first = int(match.group(1))
            last = int(match.group(2) or first)
            if last < first:
                raise ProofreadError(f"descending range: {token!r}")
            pages.update(range(first, last + 1))
        if not pages:
            raise ProofreadError("no pages selected")
        return sorted(pages)
    if arguments.first_page is None and arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ProofreadError(f"no page-NNNN.png renders in {render_directory}")
        return pages
    if arguments.first_page is None:
        raise ProofreadError("--last-page needs --first-page")
    if arguments.last_page is None:
        pages = rendered_pages(render_directory)
        if not pages:
            raise ProofreadError(f"no page-NNNN.png renders in {render_directory}")
        arguments.last_page = pages[-1]
    if arguments.last_page < arguments.first_page:
        raise ProofreadError("--last-page must be at least --first-page")
    return list(range(arguments.first_page, arguments.last_page + 1))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    started_at = utc_now()
    started = time.monotonic()
    try:
        renders = arguments.render_directory.expanduser().resolve()
        pages = parse_pages(arguments, renders)
        drafts = arguments.draft_directory.expanduser().resolve()
        output = arguments.output_directory.expanduser().resolve()
        drafts2 = (arguments.draft2_directory.expanduser().resolve()
                   if arguments.draft2_directory else None)
        for label, path in (("render", renders), ("draft", drafts)):
            if not path.is_dir():
                raise ProofreadError(f"{label} directory does not exist: {path}")
        output.mkdir(parents=True, exist_ok=True)
        if arguments.workers < 1:
            raise ProofreadError("--workers must be at least 1")
        effort = None if arguments.reasoning_effort.lower() == "none" else arguments.reasoning_effort
        # The book profile: which engine reads THIS book better, what it
        # prints, how its pages are laid out.  Nothing about a book is assumed.
        profile_path = (arguments.profile.expanduser().resolve() if arguments.profile
                        else drafts.parent / "book-profile.json")
        arguments.profile_data, arguments.profile_sha = None, ""
        if profile_path.is_file():
            arguments.profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            arguments.profile_sha = profile_fingerprint(arguments.profile_data)
            # A profile describes one book's drafts; applied to other drafts
            # (another book, re-OCR'd pages, engines swapped) it is wrong.
            sources = arguments.profile_data.get("sources") or {}
            probe = arguments.profile_data.get("probePages") or []
            if probe and draft_digest(probe, drafts, drafts2) != sources.get("probeDraftsSha256"):
                raise ProofreadError(
                    f"{profile_path} was measured on drafts A={sources.get('draftA')} "
                    f"B={sources.get('draftB')}; the probe pages' drafts under A={drafts} "
                    f"B={drafts2} differ (another book, re-OCR'd pages, or engines swapped) - "
                    "re-run scripts/book_profile.py for these drafts")
        elif arguments.profile:
            raise ProofreadError(f"profile does not exist: {profile_path}")
        # Variant-form rulings, one per pair for the whole book: a person's,
        # or the model's in a file the variant vote wrote (variant_ruling_by).
        # A person's ruling stands over the model's for the same pair wherever
        # it is in the file.
        rulings_path = (arguments.variant_rulings.expanduser().resolve() if arguments.variant_rulings
                        else profile_path.with_name("variant-rulings.json"))
        arguments.variant_rulings_data, arguments.variant_rulings_sha = {}, ""
        arguments.variant_ruled_by, arguments.variant_ruling_forms = {}, {}
        arguments.variant_rulings_file, arguments.variant_rulings_file_sha = None, ""
        if rulings_path.is_file():
            raw_rulings = json.loads(rulings_path.read_text(encoding="utf-8"))
            arguments.variant_rulings_file = str(rulings_path)
            arguments.variant_rulings_file_sha = sha256_file(rulings_path)
            # A person may rule a pair the model left unruled on its own
            # entry there (variant_person_unruled): theirs too.
            entries = [(ruling, variant_ruling_by(ruling)) for ruling in raw_rulings.get("rulings") or []]
            entries += [(ruling, "person") for ruling in variant_person_unruled(raw_rulings)]
            for ruling, by in entries:
                forms = [CJK(f) for f in ruling.get("forms") or []]
                printed = CJK(ruling.get("printed") or "")
                if len(forms) == 2 and printed in forms:
                    if by == "model" and arguments.variant_ruled_by.get(frozenset(forms)) == "person":
                        continue
                    arguments.variant_rulings_data[frozenset(forms)] = printed
                    arguments.variant_ruled_by[frozenset(forms)] = by
                    arguments.variant_ruling_forms[frozenset(forms)] = "|".join(forms)
            arguments.variant_rulings_sha = variant_rulings_seal(
                raw_rulings, rulings_path, arguments.variant_rulings_data, arguments.variant_ruled_by)
        elif arguments.variant_rulings:
            raise ProofreadError(f"variant rulings do not exist: {rulings_path}")
        arguments.variant_rulings = arguments.variant_rulings_data
        if arguments.profile_data is None and drafts2 is not None:
            raise ProofreadError(
                "no engine preference: which OCR engine reads a book better differs from "
                "book to book, so measure it first - run scripts/book_profile.py "
                f"RENDER_DIR DRAFT_DIR DRAFT2_DIR {drafts.parent / 'book-profile.json'}")
        arguments.prefer_engine = (arguments.profile_data or {}).get("preferEngine") or "adjudicate"
        preference_source = (f"profile {profile_path.name}" if arguments.profile_data is not None else
                             "single-engine image transcription; no engine calibration")
        # When the profile could not state a preference, budget overflow and
        # unusable verdicts still need an engine: the probe's leader.
        votes = (((arguments.profile_data or {}).get("engines") or {}).get("decision") or {}).get("pairVotes") or {}
        arguments.fallback_engine = "B" if votes.get("B", 0) > votes.get("A", 0) else "A"
        if arguments.prefer_engine in ("A", "B"):
            arguments.fallback_engine = arguments.prefer_engine
        # Which engine's forms the rulings (the model's and a person's) found
        # printed more often: the default for variant pairs not ruled.
        arguments.variant_engine = None
        listed = ((arguments.profile_data or {}).get("variants") or {}).get("pairs") or []
        side = Counter()
        for pair in listed:
            printed = arguments.variant_rulings.get(frozenset((pair.get("engineA"), pair.get("engineB"))))
            if printed == pair.get("engineA"):
                side["A"] += 1
            elif printed == pair.get("engineB"):
                side["B"] += 1
        if side and side["A"] != side["B"]:
            arguments.variant_engine = "A" if side["A"] > side["B"] else "B"
        # Pairs the variant lists now decide differently from this profile's
        # classification (a profile made before 2026-10-13 holds the model's
        # answers too).  Merging follows the lists, but the profile's engine
        # calibration left out the pairs it called variants, and its list of
        # variant pairs, with their crops, holds no pair it called different.
        arguments.stale_pair_classes = sorted(
            key for key, verdict in ((arguments.profile_data or {}).get("pairClasses") or {}).items()
            if len(key.split("|")) == 2 and classify_pair(*key.split("|")) != verdict)
        if arguments.stale_pair_classes:
            print(f"  WARNING {len(arguments.stale_pair_classes)} pair(s) are classified differently by "
                  "the variant lists than by this profile (e.g. "
                  + ", ".join(arguments.stale_pair_classes[:5]) + "); merging follows the lists, but the "
                  "profile's engine calibration and variant list do not: rebuild the profile (book_profile.py)",
                  flush=True)
        # The glyphs each engine writes for the other's printed marks on this
        # book, from every draft pair in the directories (not only the pages
        # selected), with no model call: measured about a second for a
        # 344-page book.  Before any page is judged current, since they decide
        # characters and the seal carries them.
        arguments.mark_evidence, arguments.mark_aliases = {}, {"A": {}, "B": {}}
        if drafts2 is not None:
            arguments.mark_evidence = mark_evidence(
                [(path, drafts2 / path.name) for path in sorted(drafts.glob("page-*.txt"))
                 if (drafts2 / path.name).is_file()])
            arguments.mark_aliases = mark_aliases(arguments.mark_evidence)
        # The script the book is printed in, from every draft of both engines
        # in the directories (not only the pages selected), with no model call:
        # in simplified script, a variant pair the rulings leave open whose
        # forms differ in script alone is written as engine B read it
        # (resolve_variant), and an answer in traditional script in the
        # simplified form an engine read (adjudicate_segment).
        arguments.simplified_script = simplified_script(
            path.read_text(encoding="utf-8") for folder in (drafts, drafts2) if folder is not None
            for path in sorted(folder.glob("page-*.txt")))
        # Switch --keep-arabic-numerals: engine A's figures on every page and the
        # glyphs engine B writes for them, from every draft in the directories
        # (not only the pages selected: a page's figure is confirmed by the
        # figures before and after it), with no model call.
        arguments.figure_evidence, arguments.figure_aliases = {}, {}
        if arguments.keep_arabic_numerals and drafts2 is not None:
            arguments.figure_evidence = figure_evidence(
                [(int(path.stem.split("-")[-1]), path, drafts2 / path.name)
                 for path in sorted(drafts.glob("page-*.txt")) if path.stem.split("-")[-1].isdigit()],
                arguments.mark_aliases)
            arguments.figure_aliases = figure_aliases(arguments.figure_evidence)
        # Switch --learned-heads: the running head learned by where it is
        # printed - the profile's (book_profile.py --learned-heads) or, for a
        # profile measured without the switch, learned here the same way from
        # engine A's layout of the pages the profile learns from (every page
        # with both drafts, a render and text; not only the pages selected: a
        # label is learned over the book).  No model call.
        arguments.learned, arguments.learned_from = None, None
        if arguments.learned_heads:
            layout = profile_section(arguments, "layout")
            if layout.get("learnedHeads") is not None:
                arguments.learned, arguments.learned_from = layout["learnedHeads"], "profile"
            else:
                pool = {}
                for path in sorted(drafts.glob("page-*.txt")):
                    match = re.fullmatch(r"page-(\d{4})\.txt", path.name)
                    if (match and (renders / f"page-{int(match.group(1)):04d}.png").is_file()
                            and (drafts2 is None or (drafts2 / path.name).is_file())
                            and CJK(path.read_text(encoding="utf-8"))):
                        pool[int(match.group(1))] = load_blocks(path.with_suffix(".json"))
                arguments.learned = learn_heads(pool, layout.get("runningHeads") or [])
                arguments.learned_from = "drafts"
    except ProofreadError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2

    # The round-4 fixes are on or off for the whole book: a run that would
    # leave pages sealed with them set the other way (a book begun before they
    # were on by default, resumed without the four --no-* switches) does not
    # start, before a client is made or any page decided (round4_split).
    two_ways = round4_split(output, [page for page in pages if arguments.overwrite
                                     and (renders / f"page-{page:04d}.png").is_file()
                                     and (drafts / f"page-{page:04d}.txt").is_file()],
                            decision_provenance(arguments))
    if two_ways:
        print(f"ERROR {two_ways}", file=sys.stderr)
        return 2

    packets_dir = (arguments.packets_dir.expanduser().resolve()
                   if arguments.packets_dir is not None else output.parent / "packets")
    if arguments.backend == "packet":
        try:
            client: Any = PacketClient(
                packets_dir,
                max_bundles=arguments.max_bundles,
                model=arguments.model,
                effort=effort,
                call_ledger_var=_CALL_LEDGER,
                call_site_var=_CALL_SITE,
            )
        except ValueError as error:
            print(f"ERROR {error}", file=sys.stderr)
            return 2
    else:
        client = Client(arguments.endpoint, arguments.model, effort, arguments.timeout, arguments.temperature,
                        max_inflight=arguments.max_inflight or arguments.workers,
                        loop_detector=_loop_watch.RULE if arguments.loop_detector else None)
    jobs: list[PageJob] = []
    results: list[dict[str, Any]] = []
    skipped = blocked = 0
    run_tally: Counter = Counter()
    variant_queue: Counter = Counter()
    variant_by_model: Counter = Counter()
    doubt_pages: Counter = Counter()

    def add_tally(record: Mapping[str, Any], doubts: Sequence[Mapping[str, Any]]) -> None:
        run_tally.update(Counter(record.get("engineTally") or {}))
        variant_queue.update(record.get("variantUnruled") or [])
        variant_by_model.update(record.get("variantRuledByModel") or [])
        doubt_pages.update({doubt.get("kind") for doubt in doubts})

    for scan_page in pages:
        render = renders / f"page-{scan_page:04d}.png"
        draft_text = drafts / f"page-{scan_page:04d}.txt"
        draft_json = drafts / f"page-{scan_page:04d}.json"
        if not render.is_file() or not draft_text.is_file():
            results.append({"scanPage": scan_page, "status": "failed",
                            "error": "missing render or draft"})
            blocked += 1
            continue
        d2 = (drafts2 / f"page-{scan_page:04d}.txt") if drafts2 else None
        job = PageJob(scan_page, render, draft_text,
                      draft_json if draft_json.is_file() else None,
                      d2 if (d2 and d2.is_file()) else None,
                      output / f"page-{scan_page:04d}.json",
                      output / f"page-{scan_page:04d}.md")
        expect = {"renderSha256": sha256_file(render), "draftSha256": sha256_file(draft_text),
                  **decision_provenance(arguments)}
        valid, reason = validate_completion(job, expect)
        if valid and not arguments.overwrite:
            skipped += 1
            record = json.loads(job.out_json.read_text(encoding="utf-8"))
            entry = {"scanPage": scan_page, "status": "skipped-current",
                     "auditorClean": record.get("auditorClean")}
            doubts = record.get("doubts") or []
            entry["doubts"] = sorted({doubt["kind"] for doubt in doubts})
            results.append(entry)
            # Pages sealed under the same decisions still count toward the
            # audit, so a resumed or chunked run judges the whole range.
            add_tally(record, doubts)
            continue
        if (os.path.lexists(job.out_json) or os.path.lexists(job.out_md)) and not arguments.overwrite:
            blocked += 1
            results.append({"scanPage": scan_page, "status": "blocked",
                            "error": f"existing output rejected: {reason}; use --overwrite"})
            continue
        jobs.append(job)

    completed = failed = pending_pages = 0
    research_queue: list[dict[str, Any]] = []
    call_totals: dict[str, Counter] = {}
    if jobs:
        # Learn this book's running heads and folios once, before any page is
        # proofread.  A single page cannot tell a long running head from a
        # sentence; the book can.  Read EVERY draft in the directory, not just
        # the selected pages: a six-page run learns almost nothing, and the
        # guard then rejects the formatter for removing a running head it could
        # not recognise.
        corpus = sorted(drafts.glob("page-*.txt")) or [j.draft_text for j in jobs]
        arguments.furniture = furniture_grams(corpus)
        print(f"  engine preference {arguments.prefer_engine} (from {preference_source}); printed in "
              f"{'simplified' if arguments.simplified_script else 'traditional'} script (the drafts)", flush=True)
        if drafts2 is not None:
            print("  glyphs taken for marks (mark rule): engine A "
                  f"{''.join(sorted(arguments.mark_aliases['A'])) or '-'}, engine B "
                  f"{''.join(sorted(arguments.mark_aliases['B'])) or '-'}", flush=True)
        print(f"  proofreading {len(jobs)} page(s), {arguments.workers} in flight, "
              f"backend={arguments.backend}, effort={effort or 'default'}, "
              f"{len(arguments.furniture)} recurring furniture n-gram(s)", flush=True)
        if arguments.learned is not None:
            learned = arguments.learned
            print(f"  learned running head (--learned-heads, from the {arguments.learned_from}): stems "
                  f"{'、'.join(learned.get('stems') or []) or '-'}; labels "
                  f"{'、'.join(list(learned.get('labels') or {})[:12]) or '-'}; families "
                  f"{'、'.join(learned.get('families') or []) or '-'}; "
                  f"{len((learned.get('folio') or {}).get('runs') or [])} folio run(s); "
                  f"{len(learned.get('regions') or [])} region(s); a band on "
                  f"{learned.get('pagesWithBand', 0)} of {learned.get('pagesExamined', 0)} pages", flush=True)
        with cf.ThreadPoolExecutor(arguments.workers) as pool:
            futures = {pool.submit(proofread_page, j, client, arguments): j for j in jobs}
            for future in cf.as_completed(futures):
                job = futures[future]
                try:
                    record = future.result()
                except PacketPending:
                    pending_pages += 1
                    results.append({"scanPage": job.scan_page, "status": "pending-packets"})
                    continue
                except Exception as error:  # noqa: BLE001 - one page must not kill the batch
                    failed += 1
                    results.append({"scanPage": job.scan_page, "status": "failed",
                                    "error": str(error)[:300]})
                    print(f"  page {job.scan_page:04d}: FAILED {str(error)[:120]}", file=sys.stderr)
                    continue
                completed += 1
                add_tally(record, record.get("doubts") or [])
                for kind, row in (record.get("calls") or {}).items():
                    total = call_totals.setdefault(kind, Counter())
                    total.update({k: v for k, v in row.items() if isinstance(v, (int, float))
                                  and k != "maxCompletion"})
                    total["maxCompletion"] = max(total["maxCompletion"], row.get("maxCompletion", 0))
                research = [a for a in record["adjudications"] if a.get("needs_research")]
                research_queue.extend(
                    {
                        "scanPage": job.scan_page,
                        "trigger": flag["kind"],
                        "context": None,
                        "candidates": [flag["text"][:120]],
                        "evidence": flag["why"],
                    }
                    for flag in record.get("needsResearch", [])
                )
                research_queue.extend(
                    {
                        "scanPage": job.scan_page,
                        "blockIndex": a.get("block_index"),
                        "context": (a.get("context_before","") + "|" +
                                    a.get("context_after","")) or None,
                        "trigger": a.get("trigger"),
                        "candidates": [a["draft_reading"][:120], a["writer_reading"][:120]],
                        "evidence": a["verdict"][:600],
                    }
                    for a in research
                )
                results.append({
                    "scanPage": job.scan_page, "status": "completed",
                    "seconds": record["seconds"], "auditorClean": record["auditorClean"],
                    "doubts": sorted({doubt["kind"] for doubt in record.get("doubts") or []}),
                    "adjudications": len(record["adjudications"]),
                    "needsResearch": len(research) + len(record.get("needsResearch", [])),
                    "reasoningTokens": record["usage"]["reasoning_tokens"],
                    "calls": sum(row.get("n", 0) for row in (record.get("calls") or {}).values()),
                    "circlesDropped": sum(1 for d in record.get("engineDisagreements") or []
                                          if d.get("resolved_by") == "circle-not-numeral"),
                    "bracketNumerals": sum(1 for d in record.get("engineDisagreements") or []
                                           if str(d.get("resolved_by", "")).startswith("bracket-not-numeral")),
                    "bracketsWritten": (record.get("engineBrackets") or {}).get("inserted", 0),
                    "bracketsMoved": (record.get("engineBrackets") or {}).get("moved", 0),
                    "engineALoopsDropped": len(record.get("engineALoopsDropped") or []),
                    "marksNotCharacters": sum(1 for d in record.get("engineDisagreements") or []
                                              if str(d.get("resolved_by", "")).startswith("mark-not-character")),
                    "movedPlacedOnce": sum(1 for d in record.get("engineDisagreements") or []
                                           if d.get("resolved_by") == "moved-kept-once"),
                    "boundaryCrops": sum(1 for a in record.get("adjudications") or [] if a.get("boundaryCrop")),
                    "furnitureInsertionsDropped": sum(
                        1 for d in record.get("engineDisagreements") or []
                        if str(d.get("resolved_by", "")).startswith("furniture-one-engine")),
                    "decisionChanges": dict(Counter(c.get("decision") for c in record.get("decisionChanges") or [])),
                    # Passes whose census was not asked, by the rule
                    # agreedMarks's reason (engines-agree: no punctuate call
                    # either).
                    "censusNotAsked": dict(Counter((entry.get("censusNotAsked") or {}).get("rule")
                                                   for entry in record.get("punctuationPasses") or []
                                                   if entry.get("censusNotAsked"))),
                    # Requests given up in a word-for-word loop (--loop-detector; only when on).
                    **({"loopCuts": sum(row.get("loopCut", 0) for row in (record.get("calls") or {}).values())}
                       if getattr(arguments, "loop_detector", False) else {}),
                    # Blocks left out and one engine's readings left out as the
                    # learned running head (--learned-heads; only when on).
                    **({"learnedHeadBlocks": sum(1 for c in record.get("decisionChanges") or []
                                                 if c.get("rule") == "learned-head-block"),
                        "learnedHeadReadings": sum(1 for c in record.get("decisionChanges") or []
                                                   if c.get("rule") == "learned-head-reading")}
                       if getattr(arguments, "learned_heads", False) else {}),
                    # Engine A's figures written, by how, and not written, by why
                    # (--keep-arabic-numerals; only when on).
                    **({"arabicFiguresKept": dict(Counter(f.get("how") for f in record.get("arabicFigures") or []
                                                          if f.get("kept"))),
                        "arabicFiguresNotKept": dict(Counter(f.get("why") for f in record.get("arabicFigures") or []
                                                             if not f.get("kept")))}
                       if getattr(arguments, "keep_arabic_numerals", False) else {}),
                    # 裁決 answers not written as copies of the text beside
                    # their spot, by the engine kept (--neighbour-guard; only when on).
                    **({"neighbourCopies": dict(Counter(
                        (a.get("neighbourCopy") or {}).get("keptEngine") for a in record.get("adjudications") or []
                        if a.get("resolved_from") == "adjudicator-neighbour-copy"))}
                       if getattr(arguments, "neighbour_guard", False) else {}),
                })
                print(f"  page {job.scan_page:04d}: {record['seconds']}s  "
                      f"clean={record['auditorClean']}  "
                      f"adjudicated={len(record['adjudications'])}  "
                      f"calls={results[-1]['calls']}  "
                      f"reasoning={record['usage']['reasoning_tokens']:,}", flush=True)

    # A table printed across pages: the pages above are sealed one by one, and
    # nothing that sees one page can join a table the page break cut.  The
    # stitch runs once they are all sealed, over every sealed page in the
    # directory (a table can cross the edge of this run's pages), rewrites the
    # pages it joins and reseals them; see stitch_tables.py.  Model-free.
    stitched: dict[str, Any] = {}
    wave_summary: dict[str, Any] | None = None
    if pending_pages > 0 and isinstance(client, PacketClient):
        wave_summary = client.write_wave_bundles(stage="proofread")
    elif pending_pages == 0:
        # When this file runs as a script, the stitch's own import of it must
        # find this module, not load a second copy.
        sys.modules.setdefault("proofread_pages", sys.modules[__name__])
        import stitch_tables
        furniture = getattr(arguments, "furniture", None)
        if furniture is None:
            furniture = furniture_grams(sorted(drafts.glob("page-*.txt")))
        stitched = stitch_tables.stitch_directory(
            output, renders, drafts, drafts2,
            sorted(set(profile_section(arguments, "layout").get("runningHeads") or [])), furniture)
        # The pages it rewrote carry other doubts now; the report says so.
        for entry in results:
            if entry["scanPage"] not in stitched.get("pagesRewritten", []):
                continue
            record = json.loads((output / f"page-{entry['scanPage']:04d}.json").read_text(encoding="utf-8"))
            old, new = set(entry.get("doubts") or []), {d["kind"] for d in record.get("doubts") or []}
            doubt_pages.subtract(old - new)
            doubt_pages.update(new - old)
            entry["doubts"], entry["auditorClean"] = sorted(new), record.get("auditorClean")
        for kind in [kind for kind, n in doubt_pages.items() if n <= 0]:
            del doubt_pages[kind]

    results.sort(key=lambda r: r["scanPage"])
    wall = time.monotonic() - started
    report = {
        "reportSchemaVersion": SCHEMA_VERSION,
        "startedAt": started_at, "finishedAt": utc_now(),
        "wallSeconds": round(wall, 1),
        "secondsPerPageEffective": round(wall / max(completed, 1), 1),
        "backend": arguments.backend,
        "endpoint": arguments.endpoint if arguments.backend == "http" else None,
        "packetsDir": str(packets_dir) if arguments.backend == "packet" else None,
        "model": arguments.model,
        "reasoningEffort": effort, "workers": arguments.workers,
        "tableOrientation": arguments.table_orientation,
        "counts": {"requested": len(pages), "completed": completed,
                   "pending": pending_pages,
                   "skippedCurrent": skipped, "blocked": blocked, "failed": failed},
        "pages": results,
        # Items the model judged unresolvable from glyph shape alone -
        # proper nouns needing external lookup. The runner has no tools, so
        # these are handed UP to an agent that does.
        "researchQueue": research_queue,
        "enginePreference": {"engine": arguments.prefer_engine, "source": preference_source,
                             "profileSha256": getattr(arguments, "profile_sha", "")},
        # Verdicts counted during the run.
        "engineTallyAdjudicated": dict(run_tally),
        # Variant pairs decided without a ruling, most frequent first.
        # Nothing waits for them.
        "variantQueue": [{"pair": pair, "occurrences": n} for pair, n in variant_queue.most_common()],
        # Which pairs a person ruled, and which the model (variant_ruling_by).
        "variantRulings": variant_rulings_report(arguments, variant_by_model),
        # Pages per kind of doubt (a page counts once per kind): what the
        # not-clean pages need a person for.  Resumed pages count too, with
        # the doubts they were sealed with.
        "doubtPages": dict(doubt_pages.most_common()),
        # Model calls of the pages completed in this run, per kind (the sum of
        # each page's sealed `calls`), with how the calls were run.  Call
        # seconds overlap when calls run together; wallSeconds is the run's.
        "calls": {kind: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in sorted(row.items())}
                  for kind, row in sorted(call_totals.items())},
        "pageConcurrency": getattr(arguments, "page_concurrency", DEFAULT_PAGE_CONCURRENCY),
        "maxInflight": arguments.max_inflight or arguments.workers,
        # Single circles one engine read as 〇, dropped as printed marks
        # (is_lone_circle); each is in its page's engineDisagreements.
        "circlesDropped": sum(r.get("circlesDropped", 0) for r in results),
        # Numeral disagreements settled by the other engine's brackets, and
        # brackets the engines read written into (or moved in) the punctuated
        # text (bracket_explanation, place_brackets).
        "bracketNumerals": sum(r.get("bracketNumerals", 0) for r in results),
        "bracketsWritten": sum(r.get("bracketsWritten", 0) for r in results),
        "bracketsMoved": sum(r.get("bracketsMoved", 0) for r in results),
        # Engine-A blocks set aside as repetition loops (quarantine_looped_block).
        "engineALoopsDropped": sum(r.get("engineALoopsDropped", 0) for r in results),
        # Disagreements settled as one engine's printed marks read as characters
        # by the other (mark_explanation), and the glyphs taken for marks on
        # this book with their evidence (mark_aliases, mark_evidence).
        "marksNotCharacters": sum(r.get("marksNotCharacters", 0) for r in results),
        # Runs engine B read in another order, placed once at engine A's site
        # (place_moved_once); each is in its page's decisionChanges.
        "movedPlacedOnce": sum(r.get("movedPlacedOnce", 0) for r in results),
        # 裁決 questions between two layout blocks shown both blocks' edges
        # (boundary_crop); each adjudication records which.
        "boundaryCrops": sum(r.get("boundaryCrops", 0) for r in results),
        # One-engine text left out as the book's furniture (decision D15,
        # furniture_insertion); each is in its page's decisionChanges.
        "furnitureInsertionsDropped": sum(r.get("furnitureInsertionsDropped", 0) for r in results),
        "markAliases": getattr(arguments, "mark_aliases", {}),
        "markEvidence": {side: {char: row for char, row in rows.items() if row["explained"]}
                         for side, rows in (getattr(arguments, "mark_evidence", None) or {}).items()},
        # Text changes per decision, the user's or a proposed one (each page's
        # decisionChanges).
        "decisionChanges": dict(sum((Counter(r.get("decisionChanges") or {}) for r in results), Counter())),
        # The rule agreedMarks: the passes of the pages completed in this run
        # whose census was not asked, by reason - engines-agree (the block
        # written with the marks both engines read: a census and a punctuate
        # call not made).
        "censusNotAsked": dict(sum((Counter(r.get("censusNotAsked") or {}) for r in results), Counter())),
        # Switch --learned-heads, only when on: where the learned running head
        # came from and what it is (learn_heads), and how many blocks and one
        # engine's readings the pages completed in this run left out as it.
        **({"learnedHeads": {
            "from": getattr(arguments, "learned_from", None), "learned": getattr(arguments, "learned", None),
            "blocksLeftOut": sum(r.get("learnedHeadBlocks", 0) for r in results),
            "readingsLeftOut": sum(r.get("learnedHeadReadings", 0) for r in results)}}
           if getattr(arguments, "learned_heads", False) else {}),
        # Switch --keep-arabic-numerals, only when on: engine A's figures on the
        # pages completed in this run, written (by how engine B's reading
        # there was settled) or not and why (arabic_figure_kept), and what it
        # read off the book's drafts: the glyphs engine B writes for figures
        # with their counts (figure_evidence, figure_aliases).
        **({"keepArabicNumerals": {
            "kept": dict(sum((Counter(r.get("arabicFiguresKept") or {}) for r in results), Counter())),
            "notKept": dict(sum((Counter(r.get("arabicFiguresNotKept") or {}) for r in results), Counter())),
            "figureAliases": getattr(arguments, "figure_aliases", {}),
            "figureGlyphs": (getattr(arguments, "figure_evidence", None) or {}).get("glyphs") or {}}}
           if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Switch --loop-detector, only when on: the rule, and for each kind of
        # call how many requests of the pages completed in this run it gave
        # up in a word-for-word loop (loopCut) and what they left of their
        # max_tokens (loopCutTokensSaved): the saving where the loop would
        # have run on to max_tokens.  Each is in its page's calls and
        # decisionChanges.
        **({"loopDetector": {"rule": _loop_watch.RULE.text(),
                             "loopCuts": {kind: {"loopCut": row.get("loopCut", 0),
                                                 "loopCutTokensSaved": row.get("loopCutTokensSaved", 0)}
                                          for kind, row in sorted(call_totals.items())}}}
           if getattr(arguments, "loop_detector", False) else {}),
        # Switch --neighbour-guard, only when on: the window, and how many 裁決
        # answers of the pages completed in this run were not written as
        # copies of the text beside their spot, by the engine whose reading
        # stands; each is in its page's adjudications, decisionChanges and a
        # neighbour-copy doubt.
        **({"neighbourGuard": {
            "window": NEIGHBOUR_WINDOW,
            "notWritten": dict(sum((Counter(r.get("neighbourCopies") or {}) for r in results), Counter()))}}
           if getattr(arguments, "neighbour_guard", False) else {}),
        # Profile pair classes the variant lists now overrule; the variant
        # sheet needs a rebuilt profile to show them.
        "profilePairClassesStale": getattr(arguments, "stale_pair_classes", []),
        # Tables printed across pages and written as one (stitch_tables.py),
        # every page break the stitch decided on, and the pages it rewrote.
        "tablesStitched": stitched.get("tablesStitched", []),
        "tableStitchDecisions": stitched.get("decisions", []),
        "tableStitchPagesRewritten": stitched.get("pagesRewritten", []),
        **({"packetWave": wave_summary} if wave_summary is not None else {}),
    }
    report_path = (arguments.report.expanduser().resolve() if arguments.report
                   else output.parent / "proofread-run.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report))

    print(f"  requested {len(pages)} | completed {completed} | pending {pending_pages} | "
          f"skipped {skipped} | blocked {blocked} | failed {failed}", flush=True)
    if wave_summary is not None:
        print(f"  [packets] PENDING: {wave_summary['pending']} request(s) across "
              f"{len(wave_summary['bundles'])} bundle(s) in {wave_summary['waveDir']}", flush=True)
        print(f"  [packets] after subagents write answers-*.json, run: {wave_summary['applyCommand']}",
              flush=True)
    if doubt_pages:
        print("  pages with doubts, by kind: "
              + ", ".join(f"{kind} {n}" for kind, n in doubt_pages.most_common()), flush=True)
    padded = sum(row.get("padded", 0) for row in call_totals.values())
    if padded:
        print(f"  {padded} image(s) padded to the server's aspect ratio (fit_media_aspect; "
              "calls.<kind>.padded)", flush=True)
    print(f"  wall {wall:.1f}s -> {wall/max(completed,1):.1f}s/page effective", flush=True)
    if research_queue:
        print(f"  {len(research_queue)} item(s) need external research "
              f"(proper nouns unresolvable from glyphs) - see researchQueue", flush=True)
    print(f"  report: {report_path}", flush=True)
    if failed or blocked:
        return 1
    if pending_pages > 0:
        return EXIT_PACKETS_PENDING
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
