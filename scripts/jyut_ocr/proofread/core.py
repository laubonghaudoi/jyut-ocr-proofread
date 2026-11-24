"""Shared constants, prompts, low-level helpers, and facade hooks for proofread_pages."""

from __future__ import annotations

from collections import Counter
import concurrent.futures as cf
import contextlib
import contextvars
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence


from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes, sha256_file, utc_now,
)
from jyut_ocr.core import tables as _tables  # noqa: E402
from jyut_ocr.proofread.packet_client import (  # noqa: E402
    PACKET_USAGE_SOURCES, PacketClient, PacketPending,
)


_FACADE: Any = None


def set_facade(module: Any) -> None:
    """Bind the public facade module so internal helpers observe test monkeypatches."""
    global _FACADE
    _FACADE = module


def facade_attr(name: str, default: Any) -> Any:
    """Resolve NAME on the bound facade module, falling back to DEFAULT."""
    return getattr(_FACADE, name, default) if _FACADE is not None else default


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


def aggregate_usage(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """New charged usage only; preserve unknowns and identify historical replay.

    Response IDs deduplicate actual usage. Conflicting repeated provider values
    fail instead of silently selecting a different accounting history.

    A packet answer (usage_source in PACKET_USAGE_SOURCES) is replayed from
    answers.jsonl by every run, but the agent that answered it spent its
    tokens on it: its recorded usage counts, once per response identity,
    and an answer recorded without usage makes the totals unknown (None),
    never zero.  Other replays (reused, or charged False) count nothing.
    """
    fields = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens")
    totals: dict[str, Any] = {key: 0 for key in fields}
    totals.update(charged_calls=0, reused_calls=0, unknown_calls=0, packet_answers=0)
    seen: dict[str, tuple[Any, ...]] = {}
    pending = list(calls)
    while pending:
        call = pending.pop(0)
        if "fidelity_attempt_metrics" in call:
            pending[0:0] = call["fidelity_attempt_metrics"]
            continue
        if not call:
            continue  # no request (e.g. a skipped structure stage)
        packet_answer = call.get("usage_source") in PACKET_USAGE_SOURCES
        reused = not packet_answer and (call.get("reused") is True or call.get("charged") is False)
        if reused:
            totals["reused_calls"] += 1
            continue
        identity = call.get("response_id") or call.get("request_id")
        values = tuple(call.get(key) for key in fields)
        if identity:
            if identity in seen:
                if seen[identity] != values:
                    raise ValueError("conflicting usage for the same response identity")
                continue
            seen[identity] = values
        totals["packet_answers" if packet_answer else "charged_calls"] += 1
        totals["unknown_calls"] += any(value is None for value in values)
        for key, value in zip(fields, values):
            if value is None:
                totals[key] = None
            elif type(value) not in {int, float} or value < 0:
                raise ValueError("invalid provider usage count")
            elif totals[key] is not None:
                totals[key] += value
    return totals


def summarize_calls(calls: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-kind accounting: unknown timings remain unknown, replay costs zero."""
    out: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for call in calls:
        groups.setdefault(call.get("kind") or "other", []).append(call)
    for kind, group in groups.items():
        usage = aggregate_usage(group)
        row: dict[str, Any] = {"n": len(group), "truncated": 0, "padded": 0,
                               "chargedCalls": usage["charged_calls"], "reusedCalls": usage["reused_calls"],
                               "packetAnswers": usage["packet_answers"],
                               "unknownUsageCalls": usage["unknown_calls"]}
        for key, field in (("promptTokens", "prompt_tokens"), ("cachedTokens", "cached_tokens"),
                           ("completionTokens", "completion_tokens"), ("reasoningTokens", "reasoning_tokens")):
            row[key] = usage[field]
        for key in ("seconds", "waitSeconds", "serverPromptMs", "serverDecodeMs"):
            values = [call.get(key) for call in group]
            row[key] = round(sum(values), 2) if all(type(v) in {int, float} for v in values) else None
        completions = [c.get("completion_tokens") for c in group if c.get("charged") is not False and not c.get("reused")]
        row["maxCompletion"] = max(completions, default=0) if all(type(v) in {int, float} for v in completions) else None
        # The rung each request of the kind was asked on (an HTTP call's
        # "effort"; a packet answer has none): how often a kind began where
        # --effort-policy put it, and how often the ladder stepped down.
        efforts = Counter(str(call["effort"] or "none") for call in group
                          if "effort" in call and call.get("usage_source") not in PACKET_USAGE_SOURCES)
        if efforts:
            row["efforts"] = dict(sorted(efforts.items()))
        for call in group:
            row["truncated"] += call.get("finish_reason") == "length"
            row["padded"] += bool(call.get("padded"))
            if call.get("loopWatched"):
                row["loopCut"] = row.get("loopCut", 0) + bool(call.get("loopCut"))
                row["loopCutTokensSaved"] = row.get("loopCutTokensSaved", 0) + (call.get("loopCutTokensSaved") or 0)
        out[kind] = row
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
如果裁圖根本影唔到爭議嗰個位置——喺圖上搵唔到上文同下文之間嗰個位，或者個位喺圖嘅邊界外面——
唔好靠上下文估，亦唔好寫隔籬嘅字：寫 `判定：睇唔到`，定案嗰行亦寫 `定案：睇唔到`。

有啲爭議係**單靠字形永遠解決唔到**嘅——特別係人名、地名、官職、年號、書名。
呢類情況下，幾個候選可能都符合圖上嘅筆畫，要靠歷史脈絡同外部資料先分得出。
你冇上網能力，所以唔好靠記憶亂猜一個名，亦唔好因為某個名眼熟就揀佢。

輸出最尾係兩行，各自獨立一行：先係題目要求嘅 `定案：<字>`（同你嘅判定一樣），
之後最後一行，格式一模一樣：
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
# A leader as engine A writes it: a run of ellipses, or of two or more ASCII
# full stops ("....." on four contents pages of an archive volume and two of
# a modern book; engine A wrote no run of middle dots in twelve books).
LEADER_READ = re.compile(r"[…⋯]+|\.{2,}")


# The line adjudicate_prompt asks 裁決 for after its reasoning (ADJUDICATE_SYSTEM
# asks for the RESEARCH line after it, last).
ANSWER_LINE = re.compile(r"^[ \t]*定案[：:][ \t]*(\S+)", re.M)

# Answers that mean "the crop does not show the spot" (ADJUDICATE_SYSTEM asks
# for 睇唔到; the others were written unasked).  No verdict: the fallback
# engine's reading stands and the record says so (adjudicator-invalid, unseen).
# Measured on two runs of the benchmark's 28 samples with a small model
# answering (2026-11-17): 3 of 337 verdicts wrote 無法判定 or 無法由圖判定, each on a
# one-character dispute, where an answer that long is no verdict already; on
# a dispute of two characters or more it would have been written into the page.
UNSEEN_VERDICTS = {"睇唔到", "睇唔見", "見唔到", "看不到", "看不見", "無法判定", "無法由圖判定", "無法判斷",
                   "不能判定"}


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


# How far from a spot, in characters of the merged text either side, a
# reading 裁決's answer leaves out may already stand for D16 to let the answer
# go (reading_nearby).  The second copy of text it let go on the benchmark
# replay stood one character from the spot; forty takes in a line or two of
# text around it.
NEARBY_WINDOW = 40


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
    # "A" or "B": the engine whose draft this page lacks on a two-engine run
    # (the page is transcribed from the other's, with a doubt saying so).
    missing_engine: str | None = None


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
LARGE_IMAGE_PIXEL_THRESHOLD = 20_000_000
_LARGE_IMAGE_SEM = threading.BoundedSemaphore(2)


@contextlib.contextmanager
def _large_image_guard(pixel_count: int) -> Iterator[None]:
    """Bound concurrent large-image decodes and resizes in host RAM."""
    if pixel_count <= LARGE_IMAGE_PIXEL_THRESHOLD:
        yield
        return
    _LARGE_IMAGE_SEM.acquire()
    try:
        yield
    finally:
        _LARGE_IMAGE_SEM.release()


MULTIMODAL_PLACEHOLDER_RE = re.compile(
    r"<(/?(?:image(?:_pad)?|video(?:_pad)?|audio(?:_pad)?|\|(?:image(?:_pad)?|video(?:_pad)?|audio(?:_pad)?|vision_(?:start|end))\|))>",
    re.IGNORECASE,
)


def neutralize_multimodal_placeholders(text: str) -> str:
    """Break literal multimodal placeholder tokens (such as ``<image>`` or
    ``<|vision_start|>``) so chat templates and vision servers do not count them
    as extra image parts (HTTP 400)."""
    return MULTIMODAL_PLACEHOLDER_RE.sub(lambda m: f"< {m.group(1)} >", text)


_PADDLEX_IMG_TAG_PATTERN = (
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:"imgs/img_in_[^"]*"|'imgs/img_in_[^']*'|imgs/img_in_[^\s>]+)[^>]*>"""
)
PADDLEX_IMG_TAG_RE = re.compile(_PADDLEX_IMG_TAG_PATTERN, re.IGNORECASE)
PADDLEX_IMG_LINE_RE = re.compile(
    r"^[ \t]*" + _PADDLEX_IMG_TAG_PATTERN + r"[ \t]*(?:\r?\n|$)",
    re.IGNORECASE | re.MULTILINE,
)


def strip_paddlex_img_tags(text: str) -> str:
    """Strip PaddleX's synthetic ``<img src="imgs/img_in_...">`` HTML tags
    from OCR drafts, blocks, and proofread page output."""
    if "img_in_" not in text.lower():
        return text
    cleaned = PADDLEX_IMG_LINE_RE.sub("", text)
    return PADDLEX_IMG_TAG_RE.sub("", cleaned)


# What of a call's site (call_site) says where it is, as every decisionChanges
# entry says it (loop_cut_changes); the rest of the site - the crop, a pass's
# range and tile - goes with the evidence.
SITE_PLACE_KEYS = ("block", "contextBefore", "contextAfter", "engineA", "engineB", "mergedOffset")


# The rung a structure pass that answered nothing is asked again from: the
# budget goes to the answer, not the reasoning.  Measured on that page: the
# same prompt answered at medium in three runs of four, and in the fourth
# every rung was cut off.
NO_ANSWER_RETRY_EFFORT = "low"


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
            text = strip_paddlex_img_tags(text)
            if not text.strip():
                continue
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


CHINESE_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


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
3. 刪走欄外書眉、頁碼等頁面家具（OCR 底稿會將佢哋混入正文）。

**絕對唔准改字，亦唔准加任何漢字。** 唔准正規化異體字，唔准「修正」你覺得怪嘅字。

呢一條最常犯嘅錯係**改編號**：舊書凡例好多時每條都用同一個「一」做項目符號，
唔係一二三四五。你**唔准**幫佢重新編號——字串入面係咩就係咩，重複嘅「一」照抄。
要做 list 就用 Markdown 嘅 `-`，唔好自己補漢字數字。
但係如果頁上嘅編號係括住嘅（例如「（一）」「（二）」），括號要加返——括號係標點，唔係字；
括號入面嘅數字就照字串，唔准改。
畀你嘅字係由兩個獨立 OCR 引擎核對過嘅，比你單獨睇圖準。你改咗就係倒退。
唯一準你刪嘅係頁面家具；正文一個字（包括外文同數字）都唔准動。

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
2. 刪走欄外書眉、頁碼等頁面家具（OCR 底稿會將佢哋混入正文）。
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
唯一準你刪嘅係頁面家具；正文一個字（包括外文同數字）、一個符號都唔准動。

輸出：淨係輸出加咗結構嘅 Markdown 正文。唔好解釋，唔好 code fence。"""

def _reviewed_prompt(prompt: str, passages: Sequence[tuple[str, str]]) -> str:
    """PROMPT with each (passage, replacement) of PASSAGES replaced; a passage
    it lacks is a programming error."""
    for passage, replacement in passages:
        if passage not in prompt:
            raise AssertionError(f"prompt passage not found: {passage[:30]}")
        prompt = prompt.replace(passage, replacement)
    return prompt


# Switch --content-fidelity: once a content-recovery review has looked at the
# page image, it has removed the furniture it confirmed, so the formatter
# keeps every glyph it is given (verify_characters' strict rule).  Without
# that review nothing else removes running heads and folios: the formatter
# is told to, as the old skill did, and may delete them alone.
FORMAT_SYSTEM_REVIEWED = _reviewed_prompt(FORMAT_SYSTEM, [
    ("3. 刪走欄外書眉、頁碼等頁面家具（OCR 底稿會將佢哋混入正文）。\n",
     "3. 來源覆核已經移除確認嘅家具；保留收到嘅全部文字、數字、外文同算式。\n"),
    ("畀你嘅字係由兩個獨立 OCR 引擎核對過嘅，比你單獨睇圖準。你改咗就係倒退。\n"
     "唯一準你刪嘅係頁面家具；正文一個字（包括外文同數字）都唔准動。\n",
     "本階段只做格式；如懷疑來源讀法有問題，留待隨後整頁圖像覆核裁定。兩引擎一致唔係正確保證。\n"
     "本階段唔准刪字；家具判斷只由整頁圖像覆核處理。\n")])
STRUCTURE_SYSTEM_REVIEWED = _reviewed_prompt(STRUCTURE_SYSTEM, [
    ("2. 刪走欄外書眉、頁碼等頁面家具（OCR 底稿會將佢哋混入正文）。\n",
     "2. 來源覆核已經移除確認嘅家具；唔准再刪收到嘅文字、數字、外文同算式。\n"),
    ("唯一準你刪嘅係頁面家具；正文一個字（包括外文同數字）、一個符號都唔准動。\n",
     "本階段唔准刪字或符號；家具判斷只由整頁圖像覆核處理。\n")])


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


def punctuation_mode(arguments: Any, client: Any = None) -> str:
    """Resolve auto without tying the shared fusion to a provider or model."""
    requested = getattr(arguments, "punctuation_mode", "auto")
    if requested in ("split", "fused"):
        return requested
    backend = getattr(arguments, "backend", None)
    if backend in ("packet", "http"):
        return "fused" if backend == "packet" else "split"
    # Compatibility for library callers that supplied a client and a small
    # Namespace before the CLI mode existed.
    if client is not None:
        return "fused" if getattr(client, "supports_fused_punctuate", False) else "split"
    return "fused"  # The CLI's default backend is packet.


def profile_section(arguments: Any, key: str) -> dict[str, Any]:
    return ((getattr(arguments, "profile_data", None) or {}).get(key)) or {}


# How far a block's size per character may be from a running head's measured
# glyph (runningHeadGlyph) and still be set in the head's type size.
HEAD_SIZE_RATIO = (0.7, 1.4)
# A mark that opens belongs with the character after it; every other mark with
# the character before it.
OPENING_MARKS = frozenset("（「『《〈")
FALLBACK_MARKS = PUNCT_MARKS + "“”‘’"


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


# The blocks whose own text the formatter may delete on the whole-page path
# (removable_text): a running head, a folio, a seal.
WHOLE_PAGE_FURNITURE_LABELS = frozenset({"header", "footer", "number", "seal"})


RULE_COVERAGE = 0.5      # a row/column this inked is a printed rule, not text
INK_MARGIN = 45          # levels below the page's own paper before it counts as ink
MEASURE_WIDTH = 500      # downsample: the measure only needs to be approximate

# A page of text that only ONE engine saw at all.  Both numbers are deliberately
# far apart: this is not "the engines disagree", it is "one engine read a full
# page and the other read nothing".
UNCORROBORATED_MINIMUM = 200

# Distinct 4-grams as a fraction of all 4-grams.  Real prose sits near 1.0
# (median 0.993 over 314 pages).  This alone is NOT a safe test - a genuine
# page of budget boilerplate measured 0.051, below a known repetition loop at
# 0.068 - so it is only ever used together with the corroboration test.
DEGENERATE_NGRAM_RATIO = 0.20


def distinct_ngram_ratio(text: str, size: int = 4) -> float:
    """How much of the text is not a repeat of itself."""
    grams = [text[i:i + size] for i in range(len(text) - size + 1)]
    return len(set(grams)) / len(grams) if grams else 1.0
# A draft that is one folio and nothing else: figures (at most
# FOLIO_MAX_FIGURES, put in for %d) or a roman numeral, with any dashes or
# brackets round it.
LONE_FOLIO = (r"[\W_]*(?:\d{1,%d}|(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})"
              r"(?:ix|iv|v?i{0,3}))[\W_]*")


# The segment key that marks engine B's reading of a quarantined block.
LOOPED_READING = "readingOfLoopedBlock"


def layout_index(blocks: Sequence[Mapping[str, Any]], position: int) -> int:
    """The index engine A's layout gave the block now at POSITION: blocks keep
    it through a move (quarantine_looped_block), and every block number sealed
    is the layout's, so passes, tables and doubts name the same block the
    layout file and engineALoopsDropped do."""
    return blocks[position].get("layoutIndex", position)


# The formatter may delete page furniture - running heads, folios - but nothing
# checked WHAT it deleted, and it was removing real text: a 38-character 凡例
# clause on p.3 and a whole table row (夏同和 / 陳光遠代表江西督軍秘書 / 前清狀元 /
# 貴州) on p.207, both read by BOTH engines. A run this long that two independent
# engines agree on is on the page; furniture is short (a 6-character running head,
# a folio) and stays under the threshold.
DELETED_RUN_LIMIT = 12
# The most figures a folio is taken to have: a run of numerals longer than
# this beside the head is not its folio.
FOLIO_MAX_FIGURES = 4


# The characters of the merged text a stretch of an engine's reading is looked
# for in, as a moved copy (readings_dropped): three, so one character misread in
# the copy does not hide a stretch that moved.
DROP_GRAM = 3


# Line breaks and table cells: a run across them is two pieces of text that
# happen to be written next to each other, not something any engine read.
# Measured: of 41 sealed pages holding a character pair more often than either
# engine read it, 6 held it only across such a seam - table cells written in
# another order than the engines read them, a heading or a list item set on a
# line of its own.
TEXT_SEAMS = re.compile(r"\n|\||<[^>]+>")


# The two engines of the default route as their runners name themselves in
# their drafts' JSON (`engine`: run_paddleocr_vl.py, run_hunyuanocr.py), and
# engine B's folder in a book's layout.
ENGINE_A_NAME = "PaddleOCR-VL"
ENGINE_B_NAME = "HunyuanOCR"
ENGINE_B_FOLDER = "ocr-hunyuan"
# The block labels engine B's runners write: a whole page, a second look's crop.
ENGINE_B_LABELS = frozenset({"whole-page", "second-look"})
ROLE_SAMPLE_PAGES = 8
# A document of at most this many rendered pages needs no book profile for
# two engines: calibration would have too little to measure (book_profile.py
# skips it too: short_document_engines).
SHORT_DOC_MAX_PAGES = 4
MISSING_ENGINE_DOUBT = "engine-draft-missing"

# Set to 1 or 0 to say whether the agent running the scripts is itself the
# local proofreading model; otherwise agent_is_local_model() works it out.
LOCAL_AGENT_ENV = "JYUT_OCR_AGENT_IS_LOCAL_MODEL"
# What Qwen Code exports to every shell it runs (getShellContextEnvVars in
# its bundle, checked in 0.27.3): the model of the session running the shell.
QWEN_CODE_MODEL_ENV = "QWEN_CODE_MODEL"
