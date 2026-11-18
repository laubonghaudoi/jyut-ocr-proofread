"""Checks for how a 裁決 verdict becomes the text: the answer 無 (or 冇, 空) is the
character when an engine read that character, and "no character here" otherwise.

Usage: python3 tests/check_adjudication.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import proofread_pages as pp  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


class Verdict:
    """Stands in for the model: every call answers with the same verdict text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def ask_answering(self, system, prompt, crop, max_tokens, kind=None):
        return self.text, {}


def judge(a: str, b: str, answer: str) -> tuple[str, str]:
    seg = {"a": a, "b": b, "before": "口用其破壞", "after": "論滿人漢人"}
    piece, record, _ = pp.adjudicate_segment(Verdict(f"構件：略\n判定：{answer}\n定案：{answer}\nRESEARCH: no"),
                                             None, seg, 16000)
    return piece, record["resolved_from"]


# The measured case: engine A read 無 (無論), engine B read 爾, and 裁決 answered 無.
check("無 is the character when an engine read 無", judge("無", "爾", "無"), ("無", "adjudicator"))
check("冇 is the character when an engine read 冇", judge("有", "冇", "冇"), ("冇", "adjudicator"))
# The bracket case the empty answer exists for: one engine read a bracket as 一.
check("無 is no character when neither engine read 無", judge("", "一", "無"), ("", "adjudicator-empty"))
check("無 is no character against two other readings", judge("二", "一", "無"), ("", "adjudicator-empty"))
check("無字 is always no character", judge("", "一", "無字"), ("", "adjudicator-empty"))
# The same in simplified script (天地会 3 scan 3: 判定：无 was written as text).
check("无 is no character when neither engine read 无", judge("", "大", "无"), ("", "adjudicator-empty"))
check("无 is the character when an engine read 无", judge("无", "元", "无"), ("无", "adjudicator"))
check("an ordinary answer is unchanged", judge("其", "萬", "甚"), ("甚", "adjudicator"))
# "Nothing printed" where both engines read ordinary text would delete a character.
check("無 against two ordinary readings is no verdict", judge("懍遵", "懷違", "無"), ("懍遵", "adjudicator-invalid"))


# The answer is the last line that begins with 定案：, not the first 定案： of the reasoning
# (#33, 天地会 3 scan 389: engine A 麾, engine B 廑 as printed; the page read 广旁已足以排除).
REASONED = ("構件：此字為半包圍結構，左外為「广」字頭。\n\n判定：廑\n\n"
            "此處純憑部件即可定案：「广」旁已足以排除「麾」，毋需外查。\n\n定案：廑\nRESEARCH: no")
piece, record, _ = pp.adjudicate_segment(Verdict(REASONED), None,
                                         {"a": "麾", "b": "廑", "before": "下落殊不可解尤深", "after": "念福康安于本月初"},
                                         16000)
check("the answer is the last 定案 line, not 定案： inside the reasoning",
      (piece, record["resolved_from"], record["winner"]), ("廑", "adjudicator", "B"))
check("verdict_answer: the last line beginning with 定案, its first word up to a bracket",
      [pp.verdict_answer(text) for text in (
          "定案：甲", "遂借圖上脈絡定案：下文「其情形」\n定案：團", "  定案：誡（兩個候選皆非）\n說明：略",
          "定案：甲\n說明：略\n定案：乙", "判定：代", "故此處定案：該位置有字", "")],
      ["甲", "團", "誡", "乙", None, None, None])
for fallback in ("A", "B"):
    piece, record, _ = pp.adjudicate_segment(Verdict("構件：略\n此處可以定案：廑"), None,
                                             {"a": "麾", "b": "廑", "before": "", "after": ""}, 16000,
                                             fallback_engine=fallback)
    check(f"定案： only inside a line is no answer: engine A's reading stands (fallback {fallback})",
          (piece, record["resolved_from"]), ("麾", "engine-A-default"))
# An answer far longer than both readings is words of the reasoning (陳總司令言論集 scan 348:
# engine A 這, engine B 還, 定案：無法由本圖字形定案（圖上此位置無該字）): not written.
check("an answer more than ANSWER_DELETION_LIMIT longer than both readings is no verdict",
      judge("這", "還", "無法由本圖字形定案（圖上此位置無該字）"), ("這", "adjudicator-invalid"))
check("... and falls back to the engine the page prefers", pp.adjudicate_segment(
    Verdict("定案：無法由本圖字形定案"), None, {"a": "這", "b": "還", "before": "", "after": ""}, 16000,
    fallback_engine="B")[0], "還")
# A folio's digits where an engine read part of them (陳總司令言論集 scan 113: engine A 四,
# engine B 丶, 裁決 一〇四): two characters more stand.
check("an answer two characters longer than both readings stands", judge("四", "丶", "一〇四"),
      ("一〇四", "adjudicator"))
check("... three characters longer does not", judge("四", "丶", "一〇〇四"), ("四", "adjudicator-invalid"))


# 睇唔到: the crop does not show the spot, which the system prompt asks 裁決 to say and not
# to guess past.  No verdict, whatever the readings' length: the fallback engine's reading
# stands.  Measured (two runs of the benchmark with a small model, 2026-11-17): 無法判定 and
# 無法由圖判定 were written unasked, on one-character disputes, where an answer that long
# is no verdict anyway; on a dispute of two characters 無法判定 is at most two characters
# longer than the readings and was written into the page.
def unseen(a: str, b: str, answer: str, fallback: str = "A"):
    seg = {"a": a, "b": b, "before": "往我等商人受此遭", "after": "告訴無門聽天主意"}
    piece, record, _ = pp.adjudicate_segment(Verdict(f"構件：略\n判定：{answer}\n定案：{answer}\nRESEARCH: no"),
                                             None, seg, 16000, fallback_engine=fallback)
    return piece, record["resolved_from"], record.get("unseen"), record["winner"]


check("睇唔到 is no verdict: engine A's reading stands, and the record says the spot was not seen",
      unseen("刧", "刼", "睇唔到"), ("刧", "adjudicator-invalid", True, "undecided"))
check("... engine B's where the book prefers engine B", unseen("刧", "刼", "睇唔到", "B")[:2],
      ("刼", "adjudicator-invalid"))
check("無法判定 on a dispute of two characters is not written into the page",
      unseen("與興", "與", "無法判定"), ("與興", "adjudicator-invalid", True, "undecided"))
check("... nor with words in brackets after it", unseen("與興", "與", "睇唔到（裁圖冇呢個位）")[:2],
      ("與興", "adjudicator-invalid"))
check("an answer that is a reading is a verdict as before", unseen("刧", "刼", "刼"),
      ("刼", "adjudicator", None, "B"))
# What 裁決 is asked for: the answer by name, and one order for its last two lines (the
# system prompt asked for the RESEARCH line last and the question for the 定案 line last).
question = pp.adjudicate_prompt({"a": "刧", "b": "刼", "before": "受此遭", "after": "告訴無門"})
check("the system prompt names the answer for a crop that does not show the spot",
      "定案：睇唔到" in pp.ADJUDICATE_SYSTEM and "睇唔到" in pp.UNSEEN_VERDICTS, True)
check("the system prompt asks for the 定案 line and then, last, the RESEARCH line",
      0 < pp.ADJUDICATE_SYSTEM.find("先係題目要求嘅 `定案：<字>`") < pp.ADJUDICATE_SYSTEM.find("RESEARCH: yes"), True)
check("the question asks for the 定案 line after the reasoning, not as the last line",
      ("另起一行淨係寫：`定案：<字>`" in question, "最後另起一行" in question), (True, False))
check("a crop set from parts is said to be one (COLUMN_BREAK_NOTE)",
      pp.COLUMN_BREAK_NOTE in pp.adjudicate_prompt({"a": "刧", "b": "刼", "before": "受此遭", "after": "告訴無門"},
                                                   note=pp.COLUMN_BREAK_NOTE), True)


# A book printed in simplified script (simplified=True): 裁決 often answers in
# traditional script.  A character of its answer only traditional script has, which
# engine B did not read there, is written in the simplified form an engine read there;
# the answer as given is sealed (answeredTraditional).  Spots from the kept folders
# (2026-10-10).
def judge_script(a: str, b: str, answer: str, simplified: bool = True):
    seg = {"a": a, "b": b, "before": "连日盼望捷音甚为", "after": "切今阅常青等奏到"}
    piece, record, _ = pp.adjudicate_segment(Verdict(f"構件：略\n定案：{answer}\nRESEARCH: no"),
                                             None, seg, 16000, simplified=simplified)
    return piece, record["resolved_from"], record["winner"], record.get("answeredTraditional")


# 天地会 3 scan 40: engine A 恳, engine B 悬 as printed, 裁決 懸.
check("simplified book: 懸 is written as engine B read it", judge_script("恳", "悬", "懸"),
      ("悬", "adjudicator", "B", "懸"))
check("traditional book: the answer as given", judge_script("恳", "悬", "懸", simplified=False),
      ("懸", "adjudicator", "neither", None))
# 天地会 2 scan 64: engine A 凤 as printed, engine B 风, 裁決 鳳.
check("simplified book: 鳳 is written as engine A read it", judge_script("凤", "风", "鳳"),
      ("凤", "adjudicator", "A", "鳳"))
# 天地会 5 scan 184: a title, engine A 瑜伽, engine B 谕协办 as printed, 裁決 諭協辦.
check("simplified book: each character of a longer answer", judge_script("瑜伽", "谕协办", "諭協辦"),
      ("谕协办", "adjudicator", "B", "諭協辦"))
# 天地会 3 scan 93: 崙 is printed; the engines read 崙 and 嵛, neither 仑.
check("simplified book: no engine read the simplified form - the answer stands", judge_script("崙", "嵛", "崙"),
      ("崙", "adjudicator", "A", None))
# 天地会 1 scan 242: 蒇 is printed; the engines read 藏 and 葳, neither the answer 蕆 nor a
# form of it: its one simplified form.  In a book printed in traditional script, as given.
check("simplified book: neither engine read the character or a form of it - its one simplified form",
      judge_script("藏", "葳", "蕆"), ("蒇", "adjudicator", "neither", "蕆"))
check("traditional book: the answer neither engine read, as given", judge_script("藏", "葳", "蕆", simplified=False),
      ("蕆", "adjudicator", "neither", None))
# A traditional form engine B read stands (天地会 5 scan 177 prints 並, which engine B read).
check("simplified book: a traditional form engine B read stands", judge_script("并末", "並未", "並未"),
      ("並未", "adjudicator", "B", None))
# An answer with nothing converted records no answeredTraditional, though it is not
# used (陳總司令言論集 scan 217: engine A 八, engine B nothing, 裁決 其 on a numeral question).
check("simplified book: nothing converted, nothing recorded", judge_script("八", "", "其"),
      ("八", "adjudicator-invalid", "undecided", None))
# A numeral question's answer is tested as given, so the script it is written in cannot
# refuse it (made up: engine A 千, engine B 十, 裁決 萬, which neither read): written 万,
# its one simplified form, not engine A's 千 as an invalid answer's fallback.
check("simplified book: a numeral answer neither engine read, in its one simplified form",
      judge_script("千", "十", "萬"), ("万", "adjudicator", "neither", "萬"))
check("traditional book: the numeral answer as given", judge_script("千", "十", "萬", simplified=False),
      ("萬", "adjudicator", "neither", None))
# An answer not used (天地会 3 scan 38: engine A read eight names' characters, engine B
# none, 裁決 黄鳳; D16 keeps engine A's reading): the form an engine read is what D16
# measures and writes once the reading stands nearby (answered), the answer as given
# what the doubt quotes (answeredTraditional).
_, refused, _ = pp.adjudicate_segment(Verdict("構件：略\n定案：黄鳳\nRESEARCH: no"), None,
                                      {"a": "黄凤李琪陈筹韩绉", "b": "", "before": "何掌黄助蒋全林云",
                                       "after": "林绍阮套林振水辜"}, 16000, simplified=True)
check("simplified book: an answer refused keeps the form read and the answer as given",
      tuple(refused.get(key) for key in ("resolved", "resolved_from", "answered", "answeredTraditional")),
      ("黄凤李琪陈筹韩绉", "adjudicator-deletion-refused", "黄凤", "黄鳳"))


# An answer that leaves out more than a couple of characters one engine read - a third
# reading, or "nothing printed" - is a partial view, not a verdict: that reading is kept.
# Measured: engine B's 26-character column against engine A's four-character fold label,
# answered with the column's last seven (the crop showed only those); a table header
# answered with one of its four characters; two table cells engine B read answered 無.
COLUMN = "於今日之中國姑從畧焉俟乎閱者諸君之自求之而自得之耳"
check("a third reading that is part of a longer one keeps the longer",
      judge("法學通論", COLUMN, "求之而自得之耳"), (COLUMN, "adjudicator-deletion-refused"))
check("one of four characters engine A read keeps the four", judge("用敎授要", "", "敎"),
      ("用敎授要", "adjudicator-deletion-refused"))
check("nothing printed where engine B read six characters keeps them", judge("", "二册二角八分", "無"),
      ("二册二角八分", "adjudicator-deletion-refused"))
check("... and what 裁決 wrote is sealed", pp.adjudicate_segment(
    Verdict("定案：求之而自得之耳"), None, {"a": "法學通論", "b": COLUMN, "before": "", "after": ""}, 16000)[1]
      ["answered"], "求之而自得之耳")
check("two characters answered away: 裁決's answer stands", judge("", "甲乙", "無"), ("", "adjudicator-empty"))
check("a third reading as long as the readings stands", judge("甲乙丙", "丁戊己", "庚辛壬"), ("庚辛壬", "adjudicator"))
check("a numeral question may answer 無 (strokes and brackets read as figures)", judge("", "一一一", "無"),
      ("", "adjudicator-empty"))
check("an answer that is engine A's reading stands", judge("法學通論", COLUMN, "法學通論"),
      ("法學通論", "adjudicator"))
# A question with deletionGuard False (one character no engine read, confirm_unread):
# the answer as written.
check("the guard off: the part 裁決 wrote stands", pp.adjudicate_segment(
    Verdict("定案：求之而自得之耳"), None, {"a": "法學通論", "b": COLUMN, "before": "", "after": "",
                                     "deletionGuard": False}, 16000)[1]["resolved_from"], "adjudicator")
check("the guard off: nothing printed stands", pp.adjudicate_segment(
    Verdict("定案：無"), None, {"a": "", "b": "二册二角八分", "before": "", "after": "", "deletionGuard": False},
    16000)[:2][0], "")


def judge_furniture(a: str, b: str, answer: str, sides: list[str]) -> tuple[str, str]:
    seg = {"a": a, "b": b, "before": "", "after": "停交易等字樣", "furnitureSides": sides}
    piece, record, _ = pp.adjudicate_segment(Verdict(f"定案：{answer}"), None, seg, 16000)
    return piece, record["resolved_from"]


check("a running head only engine B read may be answered away",
      judge_furniture("", "某某叛國史八六", "無", ["b"]), ("", "adjudicator-empty"))
check("the book's furniture: running head and folio figures leave nothing",
      pp.furniture_leftover("某某叛國史八六", set(), ["某某叛國史"]), 0)
# A folio of three figures (past the book's 99th page) beside the head, before or after
# it, is the folio however long; a figure misread (engine B's 口 for 〇) is left over.
# Measured on cangwingming, where engine B read these at the page's start and engine A
# nothing: pp.306 (二五〇 read 二五口), 312, 320 (the head misread) and three more.
HEAD = ["陳炯明叛國史"]
for reading, left in (("陳炯明叛國史二五口", 1), ("陳炯明叛國史二五六", 0), ("陳炯朗叛國史二六四", 0),
                      ("陳炯明叛國史一八七", 0), ("二三六陳炯明叛國史", 0), ("陳炯明叛國史二口四", 1)):
    check(f"... {reading}: the folio beside the head is furniture", pp.furniture_leftover(reading, set(), HEAD), left)
    check(f"... {reading}: answered 無, it goes", pp.adjudicate_segment(
        Verdict("定案：無"), None, {"a": "", "b": reading, "before": "", "after": "陳氏正作",
                                    "furnitureSides": ["b"] if pp.furniture_leftover(reading, set(), HEAD)
                                    <= pp.INSERTION_LIMIT else []}, 16000)[1]["resolved_from"],
          "adjudicator-empty")
check("... figures not beside the head are not its folio",
      pp.furniture_leftover("陳炯明叛國史陳氏二五〇", set(), HEAD), 5)
check("... nor text after the folio", pp.furniture_leftover("陳炯明叛國史八六陳氏正作", set(), HEAD), 4)
check("... a figure misread passed over, when figures stand beyond it",
      [pp.folio_figures(text, at, step) for text, at, step in (
          ("一四口陳炯明叛國史", 2, -1), ("陳炯明叛國史一口二外校", 6, 1), ("陳炯明叛國史二五口", 6, 1),
          ("陳炯明叛國史第二章", 6, 1), ("三口陳炯明叛國史", 1, -1))],
      [[1, 0], [6, 8], [6, 7], [], []])
# Where the alignment, or a run placed once (D2), left the folio alone beside the head in
# engine B's reading (cangwingming pp.158, 196, 202: the head went to engine A's site), the
# reading is taken where engine B read it.
for stream, span, left in (("陳炯明叛國史一口二外校內圖書", (6, 9), 1), ("一四口陳炯明叛國史心語也雖然", (0, 3), 1),
                           ("一四六陳炯明叛國史遣將助戰", (0, 4), 0), ("二口四陳炯明叛國史下野有利", (0, 3), 1)):
    check(f"... {stream[span[0]:span[1]]} beside the head in the engine's reading is its folio",
          pp.furniture_leftover(stream, set(), HEAD, span), left)
# cangwingming p.246: 吿之 after the head, which engine A did not read, is not the folio: the
# reading stays with the guard (a person looks).
check("... text beside the head and folio is not", pp.furniture_leftover(
    "一九口陳炯明叛國史吿之言如蒙察", set(), HEAD, (0, 11)), 3)
check("... nor text beside the head", pp.furniture_leftover("陳炯明叛國史外校內圖書儀", set(), HEAD, (6, 9)), 3)
check("... a sentence is not furniture", pp.furniture_leftover("於今日之中國姑從", set(), ["某某叛國史"]), 8)
check("... recurring n-grams are", pp.furniture_leftover("廣州大典第二十九輯", {"廣州大典第二十九"}, []), 1)


def judge_fused(a: str, b: str, agreed: list[str], answer: str) -> tuple[str, str]:
    seg = {"a": a, "b": b, "agreed": agreed, "before": "光緒二十年歲入", "after": "兩又"}
    piece, record, _ = pp.adjudicate_segment(Verdict(f"構件：略\n定案：{answer}\nRESEARCH: no"),
                                             None, seg, 16000)
    return piece, record["resolved_from"]


check("無 on a fused numeral run keeps the digits both engines read",
      judge_fused("一九〇五", "一九五", ["一九", "五"], "無"), ("一九五", "adjudicator-empty"))


sys.path.insert(0, str(Path(__file__).resolve().parent))
import stand_in  # noqa: E402

BODY = "萬國博覽會開會末有日本人設人類館"


def extra_page(extra_b: str, profile: dict, extra: tuple[str, ...] | list[str] = ()):
    """A page where engine B read EXTRA_B, five characters engine A did not, and 裁決
    answers that nothing is printed there."""
    model = stand_in.StandIn(census=0, judge=lambda a, b: "無")
    with stand_in.book({1: ([stand_in.block(BODY, [0.3, 0.1, 0.4, 0.8])], BODY[:8] + extra_b + BODY[8:])},
                       profile=profile) as root:
        return stand_in.run(root, model, extra=extra)[1]


record, markdown = extra_page("亞洲各國人", {"preferEngine": "adjudicate"})
check("page: five characters engine B read, answered away, are kept, with a doubt",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]], [d["kind"] for d in record["doubts"]]),
      (BODY[:8] + "亞洲各國人" + BODY[8:], ["adjudicator-deletion-refused"], ["deletion-refused"]))
check("page: the refusal sealed as a D16 change, the guard on in the provenance",
      ([(c["decision"], c["rule"], c["after"], c["mergedOffset"], c["evidence"]) for c in record["decisionChanges"]],
       record["provenance"]["answerDeletionGuard"], record["provenance"]["wholeRunCrop"]),
      ([("D16", "answer-deletion-guard", "亞洲各國人", 8,
         {"keptEngine": "B", "readingCharacters": 5, "answeredCharacters": 0, "limit": 2, "furnitureSides": []})],
       "on", "on"))
record, markdown = extra_page("亞洲各國人", {"preferEngine": "adjudicate", "layout": {"runningHeads": ["亞洲各國人"]}})
check("page: the same five characters as the book's running head are answered away",
      (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]], record["doubts"]),
      (BODY, ["adjudicator-empty"], []))


LONG = BODY + "列亞洲各地人民之風俗器物以供衆覽焉"


def head_page(head_and_folio: str, extra: list[str] | tuple[str, ...] = ()):
    """A page where engine B read the book's running head and its folio before the
    body, engine A read only the body, and 裁決 answers that nothing is printed there."""
    model = stand_in.StandIn(census=0, judge=lambda a, b: "無")
    with stand_in.book({1: ([stand_in.block(LONG, [0.3, 0.1, 0.4, 0.8])], head_and_folio + LONG)},
                       profile={"preferEngine": "adjudicate", "layout": {"runningHeads": HEAD}}) as root:
        return stand_in.run(root, model, extra=extra)[1]


# cangwingming pp.306, 312, 320: the head and a three-figure folio only engine B read.  At
# 60e461e each was kept over 裁決's 無 and written into the body (deletion-refused).
# With the learned running head (--learned-heads, the default) a reading that is all
# the learned head - the title, or the title one character off, and a folio - is
# left out before 裁決; one with a character that is no folio's is still asked and
# answered away.  The page's text is the same either way.
for reading, learned in (("陳炯明叛國史二五口", False), ("陳炯明叛國史二五六", True), ("陳炯朗叛國史二六四", True)):
    record, markdown = head_page(reading, ["--no-learned-heads"])
    check(f"page, --no-learned-heads: the head and folio {reading} only engine B read are answered away",
          (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]], record["doubts"],
           record["auditorClean"]), (LONG, ["adjudicator-empty"], [], True))
    record, markdown = head_page(reading)
    check(f"page: the head and folio {reading} only engine B read are "
          + ("left out as the learned head" if learned else "answered away"),
          (pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
           [c["rule"] for c in record["decisionChanges"] if c["decision"] == "learnedHeads"], record["doubts"],
           record["auditorClean"]),
          (LONG, [] if learned else ["adjudicator-empty"], ["learned-head-reading"] if learned else [], [], True))
# cangwingming p.196: engine A read the head and the folio 一四〇 after the text, engine B
# 一四口 and the head before it; the head is placed once at engine A's site (D2) and the
# folio engine B read is left alone at the page's start.
model = stand_in.StandIn(census=0, judge=lambda a, b: "無")
with stand_in.book({1: ([stand_in.block(LONG, [0.3, 0.1, 0.4, 0.8]),
                         stand_in.block(HEAD[0], [0.05, 0.1, 0.03, 0.3]),
                         stand_in.block("一四〇", [0.05, 0.85, 0.03, 0.05])], "一四口" + HEAD[0] + LONG)},
                   profile={"preferEngine": "adjudicate", "layout": {"runningHeads": HEAD}}) as root:
    record, markdown = stand_in.run(root, model)[1]
check("page: the folio engine B read beside the head, left alone by the alignment, is answered away",
      ("一四口" in pp.CJK(markdown), [(a["writer_reading"], a["resolved_from"]) for a in record["adjudications"]
                                      if a["writer_reading"]], [d["kind"] for d in record["doubts"]]),
      (False, [("一四口", "adjudicator-empty")], ["moved-placed-once"]))


# --- A book printed in simplified script, end to end (proofread_pages.main): the script
# is told from the drafts (simplified_script), and every caller of adjudicate_segment
# writes an answer in traditional script in the form an engine read - the questions of
# the page, decision D24's and a table character no engine read (confirm_spot).
def script_page(pages, judge, profile=None, rebuild=None):
    """PAGES proofread with 裁決 answering as JUDGE: the record, the page's CJK text
    and each 裁決 as (askedFor, engine A, engine B, written, answeredTraditional)."""
    model = stand_in.StandIn(census=0, judge=judge, rebuild=rebuild)
    with stand_in.book(pages, profile=profile) as root:
        record, markdown = stand_in.run(root, model)[1]
    return record, pp.CJK(markdown), [(a.get("askedFor"), a["draft_reading"], a["writer_reading"], a["resolved"],
                                       a.get("answeredTraditional")) for a in record["adjudications"]]


# 天地会 3 scan 40: engine A 恳, engine B 悬 as printed, 裁決 懸; the date line below it.
# The same spot in a book printed in traditional script keeps the answer as given.
for script, line, date in (("simplified", "朕连日盼望捷音甚为{}切今阅常青等奏到情形", "乾隆五十二年七月十三日内阁奉上谕台湾现在剿捕贼匪"),
                           ("traditional", "朕連日盼望捷音甚爲{}切今閱常青等奏到情形", "乾隆五十二年七月十三日內閣奉上諭臺灣現在剿捕賊匪")):
    _, text, asked = script_page({1: ([stand_in.block(line.format("恳"), [0.3, 0.1, 0.4, 0.05]),
                                       stand_in.block(date, [0.3, 0.2, 0.4, 0.05])], line.format("悬") + "\n" + date)},
                                 lambda a, b: "懸" if a == "恳" else a or "無", {"preferEngine": "adjudicate"})
    check(f"page, {script} script: the page's 裁決 answering 懸 where engine B read 悬",
          (text[:len(line) - 1], asked),
          (line.format("悬" if script == "simplified" else "懸")[:len(line) - 1],
           [(None, "恳", "悬", "悬", "懸")] if script == "simplified" else [(None, "恳", "悬", "懸", None)]))

# 天地会 3 scan 181: engine A 尘, engine B 廑 as printed, 裁決 塵: written 尘 as engine A
# read it, against engine B's other character - a doubt (answer-script-converted).  So is
# 天地会 1 scan 242's 蕆 (engine A 藏, engine B 葳), written in its one simplified form 蒇 as
# printed, which neither engine read, and so it is where engine B read nothing there.  Not
# at scan 40, whose 懸 is written as engine B read it.
LINE181, LINE40 = "著福康安等速行驰奏以慰{}念至李侍尧所奏", "朕连日盼望捷音甚为{}切今阅常青等奏到情形"
LINE242 = "不及一月即已收复{}功伊等岂竟未之闻"
DATE = "乾隆五十二年七月十三日内阁奉上谕台湾现在剿捕贼匪"
converted = [script_page({1: ([stand_in.block(line.format(a), [0.3, 0.1, 0.4, 0.05]),
                               stand_in.block(DATE, [0.3, 0.2, 0.4, 0.05])], line.format(b) + "\n" + DATE)},
                         lambda x, y, a=a, answer=answer: answer if x == a else x or "無", {"preferEngine": "adjudicate"})
             for line, a, b, answer in ((LINE181, "尘", "廑", "塵"), (LINE242, "藏", "葳", "蕆"), (LINE242, "藏", "", "蕆"),
                                        (LINE40, "恳", "悬", "懸"))]
HOW = ("ANSWER IN TRADITIONAL SCRIPT WRITTEN IN SIMPLIFIED SCRIPT: at 1 spot(s) 裁決's answer, in traditional script "
       "in a book printed in simplified script, is written in the form engine A read where engine B read another "
       "character, or in OpenCC's one simplified form, which neither engine read: ")
check("page, simplified script: an answer written in engine A's form against engine B's other character, or in "
      "the one form neither engine read, is a doubt",
      [(asked, [d["detail"] for d in record["doubts"] if d["kind"] == "answer-script-converted"], text[:17])
       for record, text, asked in converted],
      [([(None, "尘", "廑", "尘", "塵")],
        [HOW + "驰奏以慰[A 尘 / B 廑]念至李侍 (裁決 wrote 「塵」; written 尘) - check the character against the image"],
        LINE181.format("尘")[:17]),
       ([(None, "藏", "葳", "蒇", "蕆")],
        [HOW + "即已收复[A 藏 / B 葳]功伊等岂 (裁決 wrote 「蕆」; written 蒇) - check the character against the image"],
        LINE242.format("蒇")),
       ([(None, "藏", "", "蒇", "蕆")],
        [HOW + "即已收复[A 藏 / B -]功伊等岂 (裁決 wrote 「蕆」; written 蒇) - check the character against the image"],
        LINE242.format("蒇")),
       ([(None, "恳", "悬", "悬", "懸")], [], LINE40.format("悬")[:17])])
# Nor an answer not written: 天地会 3 scan 38's names with engine B made to read 风 at the
# spot, 裁決 黄鳳 - D16 keeps engine A's reading, and its own doubt says so.
NAMES = ("现将拿获之贼目开列于后何掌黄助蒋全林云", "林绍阮套林振水辜俱已就擒解京审办")
record, _, asked = script_page({1: ([stand_in.block(NAMES[0] + "黄凤李琪陈筹韩绉" + NAMES[1], [0.3, 0.1, 0.4, 0.8])],
                                    NAMES[0] + "风" + NAMES[1])},
                               lambda a, b: "黄鳳" if a else "無", {"preferEngine": "adjudicate"})
check("page, simplified script: an answer converted against engine B but not written is no such doubt",
      (asked, [d["kind"] for d in record["doubts"]]),
      ([(None, "黄凤李琪陈筹韩绉", "风", "黄凤李琪陈筹韩绉", "黄鳳")], ["deletion-refused"]))

# The refusal's doubt quotes the answer as 裁決 gave it (天地会 3 scan 38).
record, text, asked = script_page({1: ([stand_in.block(NAMES[0] + "黄凤李琪陈筹韩绉" + NAMES[1], [0.3, 0.1, 0.4, 0.8])],
                                       NAMES[0] + NAMES[1])},
                                  lambda a, b: "黄鳳" if a else "無", {"preferEngine": "adjudicate"})
check("page, simplified script: an answer refused, its doubt quoting the answer as given",
      (asked, [d["detail"].split(": ", 2)[-1] for d in record["doubts"] if d["kind"] == "deletion-refused"]),
      ([(None, "黄凤李琪陈筹韩绉", "", "黄凤李琪陈筹韩绉", "黄鳳")],
       ["蒋全林云[A 黄凤李琪陈筹韩绉 / B -]林绍阮套 (裁決 wrote 「黄鳳」; kept 黄凤李琪陈筹韩绉) "
        "- check the text against the image"]))

# Decision D3 (a book preferring engine A): engine B read 黄凤 where engine A read nothing,
# and 裁決 wrote one character of the two; engine B's reading stands, and the doubt
# quotes the answer as given.
record, text, asked = script_page({1: ([stand_in.block(NAMES[0] + NAMES[1], [0.3, 0.1, 0.4, 0.8])],
                                       NAMES[0] + "黄凤" + NAMES[1])},
                                  lambda a, b: "鳳" if b == "黄凤" else a or "無")
check("page, simplified script: a D3 answer shorter than the reading, its doubt quoting the answer as given",
      (asked, [d["detail"].split("kept: ", 1)[-1] for d in record["doubts"] if d["kind"] == "insertion-partial"]),
      ([("D3", "", "黄凤", "黄凤", "鳳")],
       ["蒋全林云[黄凤]林绍阮套 (engine B only; 裁決 wrote 「鳳」) - count the characters against the image"]))

# Decision D24: engine A read 恳 where engine B wrote a rare codepoint twice, 裁決 answered
# 悬 there, and 恳 once more in a stretch engine B did not read, where 裁決 answers 懸.
P1, P2, P3 = "天地玄黄宇宙洪荒日月盈昃辰宿列张", "寒来暑往秋收冬藏闰余成岁律吕调阳", "云腾致雨露结为霜金生丽水玉出昆冈"
ONLY_A, P4 = "剑号巨阙恳称夜光果珍李柰", "菜重芥姜海咸河淡鳞潜羽翔龙师火帝"
_, text, asked = script_page({1: ([stand_in.block(P1 + "甚恳" + P2 + "恳切" + P3 + ONLY_A + P4, [0.3, 0.1, 0.4, 0.8])],
                                  P1 + "甚\U0002E155" + P2 + "\U0002E155切" + P3 + P4)},
                             lambda a, b: "悬" if b == "\U0002E155" else "懸" if b == "悬" else a)
check("page, simplified script: D24's 裁決 answering 懸 where the page's verdicts wrote 悬",
      (text, asked[2:]), (P1 + "甚悬" + P2 + "悬切" + P3 + ONLY_A.replace("恳", "悬") + P4, [("D24", "恳", "悬", "悬", "懸")]))

# A table's character no engine read (headerCharacters): the rebuilt table wrote 点 after
# 教授要, which engine A read without it; 裁決 answers 點 on the spot's crop.
FIRST, SECOND = "第一种读本每课可供一小时", "第二种读本每页可习写三日"
PROSE = ("右表所列各本均系本年新颁之书其用法详见各本卷首凡例之中此处不赘"
         "凡塾中教员务须先将各本通读一过然后按表所定年期逐课教授勿稍躐等")
TABLE = (f"| 书名 | 全书册数 | 每册课数 | 备用年期 | 教授要点 |\n| --- | --- | --- | --- | --- |\n"
         f"| {FIRST[:5]} | {FIRST[5:]} | {SECOND[:5]} | {SECOND[5:]} | |")
record, text, asked = script_page(
    {1: ([stand_in.block(PROSE, [0.70, 0.10, 0.25, 0.80]),
          stand_in.block(FIRST + SECOND, [0.10, 0.20, 0.52, 0.70], "vertical_text"),
          stand_in.block("书名册全数书课每数册年备期用教授要", [0.60, 0.20, 0.06, 0.60], "vertical_text")],
         PROSE + "书名全书册数每册课数备用年期" + SECOND + FIRST)},
    lambda a, b: "點" if (a, b) == ("", "点") else a or "無",
    rebuild=lambda text: TABLE if "书名" in text else f"| {FIRST} |\n| --- |\n| {SECOND} |")
check("page, simplified script: a table character no engine read, confirmed by 裁決's 點",
      ("教授要点" in text, asked, [d["kind"] for d in record["doubts"] if d["kind"].startswith("table-unread")]),
      (True, [("headerCharacters", "", "点", "点", "點")], ["table-unread-confirmed"]))

sys.exit(1 if failures else 0)
