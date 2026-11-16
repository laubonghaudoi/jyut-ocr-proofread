#!/usr/bin/env python3
"""Checks for the text-loss fixes the user approved on 2026-09-27.

- The drafts are aligned on variant-folded text (align_drafts).
- A lopsided replacement is split into its like-for-like part and text only one
  engine read (split_lopsided); the surplus goes the way an insertion goes, and the
  book's furniture in it is left out.
- A reading of more than ANSWER_DELETION_LIMIT characters the merge drops is a
  doubt (readings_dropped).
- 裁決's 無 on a crop cut where the spot was estimated to be deletes only once a
  blind reading of the crop shows the spot empty (crop_shows_spot, confirm_nothing).
- D16 lets 裁決's answer go where what it leaves out already stands nearby
  (reading_nearby).

No model, no GPU, no corpus.  The shapes are the round-2 losses: a line engine A
skipped fused with a variant form of the next heading's character, with engine A's
bullet read as 〇, and with engine A's folio; two crops that did not show the spot.

Usage: python3 tests/check_text_loss.py   (exits non-zero when a check fails)
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# --- the alignment, folded -----------------------------------------------------------
LINE = "大鼓掌旋由莫任衡將議案逐條宣佈以次表决乃散會議案錄下"
HEADING = "保全廣東大局議案"
A = "三千萬人爲後盾衆" + "議決" + HEADING
B = "三千萬人爲後盾衆" + LINE + "議决" + HEADING
raw = [(s["tag"], s["a"], s["b"]) for s in pp.align_drafts(A, B, folded=False) if s["tag"] != "equal"]
check("raw alignment: the skipped line fused with 決/决 into a lopsided replacement",
      raw, [("insert", "", "大鼓掌旋由莫任衡將"), ("replace", "決", "案逐條宣佈以次表决乃散會議案錄下議决")])
folded = [(s["tag"], s["a"], s["b"]) for s in pp.align_drafts(A, B) if s["tag"] != "equal"]
check("folded alignment: the line is an insertion, 決/决 a variant question of its own",
      folded, [("insert", "", LINE), ("replace", "決", "决")])
check("folded alignment: the characters are the engines' own",
      ("".join(s["a"] for s in pp.align_drafts(A, B)), "".join(s["b"] for s in pp.align_drafts(A, B))),
      (pp.CJK(A), pp.CJK(B)))


# --- the split -----------------------------------------------------------------------
def split(a: str, b: str, a_edges=(), b_edges=()):
    found = pp.split_lopsided({"tag": "replace", "a": a, "b": b, "before": "前文", "after": "後文"}, a_edges, b_edges)
    return None if found is None else (found[1], [(s["tag"], s["a"], s["b"], s["lopsided"]["surplus"])
                                                  for s in found[0]])


check("not lopsided: two characters more is left alone", split("甲", "乙丙丁"), None)
check("engine B's side is whole lines of its own (signatures), engine A's bullet 〇 begins its block: "
      "both text only one engine read, the lines first",
      split("〇", "陳景華蔣尊韋同啓", a_edges={0}, b_edges={0, 3, 6, 8}),
      ("none", [("insert", "", "陳景華蔣尊韋同啓", True), ("delete", "〇", "", True)]))
check("engine A's folio is a whole block, engine B's list item a whole line: the folio first",
      split("十六", "一承認滿漢一體", a_edges={0, 2}, b_edges={0, 7}),
      ("none", [("delete", "十六", "", True), ("insert", "", "一承認滿漢一體", True)]))
check("engine A's last body character, then its running head and folio blocks: like for like first",
      split("擊陳炯明叛國史一〇八", "掣", a_edges={1, 7, 10}, b_edges={1}),
      ("start", [("replace", "擊", "掣", False), ("delete", "陳炯明叛國史一〇八", "", True)]))
check("engine B's folio and head on the line of the body, engine A's character begins its block: "
      "like for like last",
      split("婦", "二四二陳炯明叛國史辯", a_edges={0}, b_edges={0}),
      ("end", [("insert", "", "二四二陳炯明叛國史", True), ("replace", "婦", "辯", False)]))
check("engine A's side is a block of its own (a fold title): no reading of engine B's sentence, "
      "wherever engine B's lines break",
      split("法學通論", "於今日之中國姑從畧焉俟乎閱者", a_edges={0, 4}, b_edges={4}),
      ("none", [("delete", "法學通論", "", True), ("insert", "", "於今日之中國姑從畧焉俟乎閱者", True)]))
check("nothing says where: both text only one engine read",
      split("決", "案逐條宣佈以次表决乃散會議案錄下議决", b_edges={16}),
      ("none", [("insert", "", "案逐條宣佈以次表决乃散會議案錄下議决", True), ("delete", "決", "", True)]))


# --- pages ---------------------------------------------------------------------------
def page(blocks, text_b, extra=(), profile=None, **model):
    with stand_in.book({1: ([stand_in.block(t, box, *rest) for t, box, *rest in blocks], text_b)},
                       profile=profile) as root:
        return stand_in.run(root, stand_in.StandIn(census=0, **model), extra=list(extra))[1]


# hinghon p.6 with engine B v1.0: engine A read the folio 十六 where engine B read a
# list item engine A skipped.
BODY = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏"
BODY2 = "閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡"
ITEM_BLOCKS = [(BODY + "承認新政府", [0.5, 0.1, 0.03, 0.2]), ("十六", [0.45, 0.8, 0.02, 0.03]),
               ("一用正式公文呈報張督" + BODY2, [0.4, 0.1, 0.03, 0.4])]
ITEM_B = BODY + "承認新政府\n一承認滿漢一體\n一用正式公文呈報張督" + BODY2
record, markdown = page(ITEM_BLOCKS, ITEM_B)
check("page: the list item only engine B read is kept", "一承認滿漢一體" in pp.CJK(markdown), True)
check("page: the split sealed, with what the merge wrote for it",
      [(s["engineA"], s["engineB"], s["how"], s["written"]) for s in record["lopsidedSplits"]],
      [("十六", "一承認滿漢一體", "none", "十六一承認滿漢一體")])
check("page: a textLoss change", [(c["decision"], c["rule"], c["after"])
                                  for c in record["decisionChanges"] if c["decision"] == "textLoss"],
      [("textLoss", "lopsided-split", "十六一承認滿漢一體")])
check("page: nothing dropped, no doubt about it", (record["readingsDropped"],
                                                    [d["kind"] for d in record["doubts"]]), ([], []))
check("page: the switches on in the provenance",
      [record["provenance"][k] for k in ("foldedAlignment", "lopsidedSplit", "mergeLossGuard")], ["on"] * 3)

# hinghon p.4 with engine B v1.5: engine A skipped a line, and engine B wrote the next
# heading's 決 as 决 - before the fold, one replacement the preference settled with 決.
FOLD_A = BODY + BODY2 + "三千萬人爲後盾衆" + "議決" + HEADING + BODY2 + BODY
FOLD_B = BODY + BODY2 + "三千萬人爲後盾衆" + LINE + "議决" + HEADING + BODY2 + BODY
record, markdown = page([(FOLD_A, [0.3, 0.1, 0.4, 0.8])], FOLD_B)
check("page: the fold keeps the line engine A skipped",
      (LINE in pp.CJK(markdown), record["readingsDropped"]), (True, []))

# cangwingming p.274: engine A read a line of signatures twice, its second copy fused with
# the variant pair 暨/曁 beside it.  Folded, the copy would be text only engine A read,
# which a preference for engine B keeps: on such a page the fold leaves that stretch as
# written, and the preference settles it as before.
SIGNED = "黃劍聲潘肇江曁全體華僑公叩五十七人"
TWICE_A = BODY + "黃劍聲潘肇江暨全體華僑公叩五潘肇江暨全體華僑公叩五十七人" + BODY2
TWICE_B = BODY + SIGNED + BODY2


def differences(a, b, prefer=None):
    return [(s["tag"], s["a"], s["b"]) for s in pp.align_drafts(a, b, folded=True, prefer=prefer) if s["tag"] != "equal"]


check("folded alignment, preferring engine B: a second copy of a line engine B read stays one replacement",
      differences(TWICE_A, TWICE_B, "B"), [("replace", "暨全體華僑公叩五潘肇江暨", "曁")])
check("folded alignment, preferring engine A (it writes the copy either way) or none (裁決 reads both "
      "questions): folded", (differences(TWICE_A, TWICE_B, "A"), differences(TWICE_A, TWICE_B)),
      ([("replace", "暨", "曁"), ("delete", "潘肇江暨全體華僑公叩五", "")],) * 2)
check("folded alignment, preferring engine B: a line engine A skipped on the same page is still taken apart",
      differences(FOLD_A + TWICE_A, FOLD_B + TWICE_B, "B"),
      [("insert", "", LINE), ("replace", "決", "决"), ("replace", "暨全體華僑公叩五潘肇江暨", "曁")])
check("folded alignment: the characters are the engines' own, with a stretch left as written",
      ["".join(s[k] for s in pp.align_drafts(FOLD_A + TWICE_A, FOLD_B + TWICE_B, folded=True, prefer="B"))
       for k in "ab"], [pp.CJK(FOLD_A + TWICE_A), pp.CJK(FOLD_B + TWICE_B)])
record, markdown = page([(TWICE_A, [0.3, 0.1, 0.4, 0.8])], TWICE_B, profile={"preferEngine": "B"})
check("page: a line engine A read twice is written once, as the preference wrote it before",
      (pp.CJK(markdown).count("全體華僑公叩"), SIGNED in pp.CJK(markdown),
       [c["rule"] for c in record["decisionChanges"] if c["decision"] == "textLoss"]), (1, True, []))

# canzungsiling p.334: engine A wrote a sentence with 真 and, later, a copy of it with
# 眞; engine B wrote 眞 in both.  As written, difflib anchored engine B's first copy on
# engine A's second and the page read as an order conflict; folded, it merges.
S1A, S2A = "惟有實習始能證明所學得的知識技能是否真的知識技能", "唯有實習始能證明所學得的知識技能是否眞的知識技能"
S1B, S2B = "惟有實習始能證明所學得的知識技能是否眞的知識技能", "唯有實習始能証明所學得的知識技能是否眞的知識技能"
QA = ("儒家有兩位大學者如程子和顏習齋曾有這樣的意見程子嘗說昔嘗見有說虎傷人者衆莫不聞而其間一人神色獨變問其所以"
      "乃嘗傷於虎者也夫虎能傷人人孰不知之而聞之有懼者知有眞有不眞也學者之知道必如此人之知虎然後爲知耳可見眞知是要親身閱歷得來的")
QB = "".join(c if i % 9 != 4 else "口" for i, c in enumerate(QA))     # engine B misreads one in nine
ORDER_A = BODY + S1A + QA + S2A + BODY2
ORDER_B = BODY[:-1] + "口" + S1B + QB + S2B + "口" + BODY2[1:]
check("order test: an order conflict as written, merged folded",
      [round(pp.order_judgement([], ORDER_A, ORDER_B, folded=f)["agreement"], 2) for f in (False, True)], [0.34, 0.92])
record, markdown = page([(ORDER_A, [0.3, 0.1, 0.4, 0.8])], ORDER_B)
check("page, preferring engine A: merged", record["mode"], "merge")

# The profiler measures a book's preference on the drafts aligned as written, whatever
# the run's alignment: its disagreements are those the preference was measured on.
import book_profile as bp  # noqa: E402

with stand_in.book({1: ([stand_in.block(FOLD_A, [0.3, 0.1, 0.4, 0.8])], FOLD_B)}) as root:
    profiled = [(s["seg"]["a"], s["seg"]["b"]) for s in bp.page_segments(1, root / "renders", root / "a", root / "b")]
check("book_profile: the probe's disagreements are the drafts' as written, with the fold on for the run",
      profiled, [(s["a"], s["b"]) for s in pp.fuse_numeral_runs(pp.align_drafts(FOLD_A, FOLD_B, folded=False))
                 if s["tag"] != "equal"])

# The surplus is the book's running head and folio, which only engine A read, on a
# book that prefers engine B (cangwingming's pages in the round-2 scan).
HEAD = "陳炯明叛國史"
head_blocks = [(BODY + "擊", [0.3, 0.1, 0.4, 0.8]), (HEAD, [0.9, 0.05, 0.03, 0.2]), ("一〇八", [0.9, 0.9, 0.03, 0.05])]
record, markdown = page(head_blocks, BODY + "掣", profile={"preferEngine": "B", "layout": {"runningHeads": [HEAD]}})
check("page: the head and folio split off are left out, as the preference left them",
      (pp.CJK(markdown), [(d["engineA"], d["resolved_by"]) for d in record["engineDisagreements"]
                          if d["resolved_by"].startswith("lopsided")]),
      (BODY + "掣", [(HEAD + "一〇八", "lopsided-surplus-furniture-A")]))
check("page: furniture is no dropped reading", record["readingsDropped"], [])

# What is kept whole: where the preferred engine read the longer side (it loses nothing),
# where the longer side loops, and where the other engine read it elsewhere on the page.
def split_of(segments, draft, draft2, prefer):
    return [(s["a"], s["b"]) for s in pp.split_lopsided_segments(segments, None, draft, draft2, prefer)[0]]


LOPSIDED = {"tag": "replace", "a": "十六", "b": "一承認滿漢一體", "before": "", "after": ""}
check("split_lopsided_segments: the preferred engine's shorter side is split",
      split_of([LOPSIDED], "十六", "一承認滿漢一體", "A"), [("", "一承認滿漢一體"), ("十六", "")])
check("split_lopsided_segments: the preferred engine's longer side is left to the preference",
      split_of([LOPSIDED], "十六", "一承認滿漢一體", "B"), [("十六", "一承認滿漢一體")])
LOOP = "眞禍的" * 100
check("split_lopsided_segments: a loop engine B ran into is left to the preference",
      split_of([dict(LOPSIDED, a="也有", b=LOOP)], BODY + "也有", BODY + LOOP, "A"), [("也有", LOOP)])
check("split_lopsided_segments: a line the other engine read elsewhere is left to the preference",
      split_of([dict(LOPSIDED, b="一承認滿漢一體")], BODY + "一承認滿漢一體" + "十六", BODY + "一承認滿漢一體", "A"),
      [("十六", "一承認滿漢一體")])
check("moved_share: a line read elsewhere, and one never read",
      (pp.moved_share("一承認滿漢一體", BODY + "一承認滿漢一體"), pp.moved_share("一承認滿漢一體", BODY)), (1.0, 0.0))

# --- the guard -------------------------------------------------------------------------
check("readings_dropped: a stretch nowhere in the merged text",
      [(d["engine"], d["text"]) for d in pp.readings_dropped({"A": BODY, "B": BODY[:8] + "亞洲各國人" + BODY[8:]}, BODY)],
      [("B", "亞洲各國人")])
check("readings_dropped: two characters are within the allowance",
      pp.readings_dropped({"A": BODY, "B": BODY[:8] + "亞洲" + BODY[8:]}, BODY), [])
check("readings_dropped: text written elsewhere is not dropped (net)",
      pp.readings_dropped({"A": BODY + "亞洲各國人", "B": "亞洲各國人" + BODY}, BODY + "亞洲各國人"), [])
check("readings_dropped: the book's running head is not a dropped reading",
      pp.readings_dropped({"A": BODY, "B": HEAD + "二五六" + BODY}, BODY, heads=[HEAD]), [])
# hinzing-6 p.61: a header of two-character cells engine A read row by row and engine B
# by columns - the same characters, as often, in another order.
check("readings_dropped: a header read in another order is not dropped (counted per character)",
      pp.readings_dropped({"A": "表如左書名册全數書課每數册" + BODY, "B": "表如左書名全書册數每册課數" + BODY},
                          "表如左書名册全數書課每數册" + BODY), [])
# The round-3 review: a list item only engine B read, worded like the item beside it -
# its stretches stand in the merged text, in the other item, but not as often as engine
# B read them.
PARALLEL_A = BODY + "一保全地方治安一保護外人生命財產" + "十六" + "一用正式公文呈報張督" + BODY2
PARALLEL_B = BODY + "一保全地方治安一保護外人生命財產一保護商民生命財產一用正式公文呈報張督" + BODY2
check("readings_dropped: a line worded like the one beside it is counted, not found there",
      [(d["engine"], d["text"]) for d in pp.readings_dropped({"A": PARALLEL_A, "B": PARALLEL_B}, PARALLEL_A)],
      [("B", "一保護商民生命財產")])
check("readings_dropped: a second copy of a line an engine read, written once, is not dropped",
      pp.readings_dropped({"A": TWICE_A, "B": TWICE_B}, TWICE_B), [])
check("moved_share: the parallel item is still mostly held (the split leaves it to the preference)",
      round(pp.moved_share("一保護商民生命財產", PARALLEL_A), 2), 0.78)
check("readings_dropped: an engine's reading of the page that loops is no evidence",
      pp.readings_dropped({"A": BODY, "B": BODY + "眞禍的" * 100}, BODY), [])
check("readings_dropped: what a rule read as no text of the page (explained) is not dropped",
      pp.readings_dropped({"A": BODY, "B": BODY[:8] + "亞洲各國人" + BODY[8:]}, BODY, explained={"B": [(8, 13)]}),
      [])

record, markdown = page([(BODY + "一保全地方治安一保護外人生命財產", [0.5, 0.1, 0.03, 0.2]),
                         ("十六", [0.45, 0.8, 0.02, 0.03]), ("一用正式公文呈報張督" + BODY2, [0.4, 0.1, 0.03, 0.4])],
                        BODY + "一保全地方治安\n一保護外人生命財產\n一保護商民生命財產\n一用正式公文呈報張督" + BODY2)
check("page: the parallel item the preference settled away is a reading-dropped doubt",
      ("一保護商民生命財產" in pp.CJK(markdown), [(d["engine"], d["text"], d["by"]) for d in record["readingsDropped"]],
       [d["kind"] for d in record["doubts"]], record["auditorClean"]),
      (False, [("B", "一保護商民生命財產", ["prefer-engine-A"])], ["reading-dropped"], False))

# --- the crop check --------------------------------------------------------------------
YI = {"a": "一", "b": "", "before": "下輿向各故衣店逐", "after": "勸諭照常貿易勿爲"}
check("crop_shows_spot: a crop beginning after the spot does not show it",
      pp.crop_shows_spot("勸諭照常貿易勿爲謠言", YI), (False, "", "before-unshown"))
check("crop_shows_spot: a crop of another column does not show it (common characters are no anchor)",
      pp.crop_shows_spot("學說之處多據中國人人必讀之典",
                         {"a": "情", "b": "", "before": "不能適合乎我國之", "after": "事他族之名論或不"}),
      (False, "", "unanchored"))
check("crop_shows_spot: shown, and the character read there",
      pp.crop_shows_spot("向各故衣店逐一勸諭照常", YI), (True, "一", "placed"))
check("crop_shows_spot: shown, and nothing there but a bracket",
      pp.crop_shows_spot("向各故衣店逐︵︶勸諭照常", YI), (True, "", "placed"))
# At a block's edge (no room on that side), a crop that begins or ends at the spot and
# one that misses it by a character read the same (the round-3 review): the side is
# anchored only by the neighbouring block's text on the crop.
FIRST = {"a": "一", "b": "", "before": "呈報張督", "after": "承認新政府各界"}
check("crop_shows_spot: a block's first character, the crop beginning after it: not shown",
      pp.crop_shows_spot("承認新政府各界", FIRST, 0, 7), (False, "", "before-unshown"))
check("crop_shows_spot: a block's first character, the neighbouring block's end on the crop: shown",
      pp.crop_shows_spot("呈報張督承認新政府各界", FIRST, 0, 7), (True, "", "placed"))
check("crop_shows_spot: a block's last character, the crop ending before it: not shown",
      pp.crop_shows_spot("照常貿易勿爲謠言所嚇", {"a": "云", "b": "", "before": "勿爲謠言所嚇", "after": "又不開門"}, 6, 0),
      (False, "", "after-unshown"))
check("crop_shows_spot: the page's first character has no neighbour to show it",
      pp.crop_shows_spot("本刊徵文簡約", {"a": "", "b": "厶", "before": "", "after": "本刊徵文簡約"}, 0, 6),
      (False, "", "before-unshown"))

YI_BLOCKS = [("又不開門貿易巡警嚴道經過陶街口覩此情形卽下輿向各故衣店逐一勸諭照常貿易勿爲謠言所嚇云",
              [0.3, 0.1, 0.1, 0.8])]
YI_B = "又不開門貿易巡警嚴道經過陶街口覩此情形卽下輿向各故衣店逐勸諭照常貿易勿爲謠言所嚇云"
nothing = {"judge": lambda a, b: "無"}
record, markdown = page(YI_BLOCKS, YI_B, coverage="勸諭照常貿易勿爲謠言所嚇云", **nothing)
check("page: 無 on a crop that does not show the spot: the reading is kept, with a doubt",
      ("逐一勸" in pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]]),
      (True, ["adjudicator-empty-unconfirmed"], ["nothing-unconfirmed"]))
check("page: sealed as a textLoss change, with what was read",
      [(c["rule"], c["after"], c["evidence"]["found"]) for c in record["decisionChanges"]
       if c["decision"] == "textLoss"], [("nothing-answer-unconfirmed", "一", "before-unshown")])
record, markdown = page(YI_BLOCKS, YI_B, coverage="向各故衣店逐勸諭照常", **nothing)
check("page: 無 on a crop that shows the spot empty: deleted",
      ("逐勸" in pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
       record["adjudications"][0]["coverageRead"]["found"]), (True, ["adjudicator-empty"], "placed"))
check("page: the rule's key in the provenance", record["provenance"]["cropCoverageCheck"], "on")


class Blind:
    """A client whose blind read of any crop is TRANSCRIPT."""

    def __init__(self, transcript):
        self.transcript = transcript

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        return self.transcript, {"prompt_tokens": 1, "completion_tokens": 1, "reasoning_tokens": 0}


# hinghon p.14 (v2): both engines read the numeral, engine A with the circle mark after it.
WAN = {"a": "萬〇", "b": "萬", "before": "三數日間捐集十餘", "after": "此次捐助軍費女子",
       "coverage": {"kind": "glyph", "beforeRoom": 20, "afterRoom": 20}}
answered_nothing = ("", {"resolved_from": "adjudicator-empty", "resolved": ""}, {})
piece, got, _ = pp.confirm_nothing(Blind("數日間捐集十餘萬此次捐助軍費女子尙"), b"", WAN, answered_nothing, 100, "A")
check("confirm_nothing: the reading the blind read shows at the spot is kept, not the fallback engine's",
      (piece, got["resolved_from"], got["coverageRead"]["reading"]), ("萬", "adjudicator-empty-unconfirmed", "萬"))
piece, got, _ = pp.confirm_nothing(Blind("數日間捐集十餘此次捐助軍費女子尙"), b"", WAN, answered_nothing, 100, "A")
check("confirm_nothing: shown empty, the answer stands", (piece, got["resolved_from"]), ("", "adjudicator-empty"))
# A 無 on a numeral run writes the digits both engines read (萬): a crop showing them at
# the spot, and nothing else, shows the answer right.
answered_wan = ("萬", {"resolved_from": "adjudicator-empty", "resolved": "萬"}, {})
piece, got, _ = pp.confirm_nothing(Blind("數日間捐集十餘萬此次捐助軍費女子尙"), b"", WAN, answered_wan, 100, "A")
check("confirm_nothing: a numeral 無 whose kept digits the crop shows at the spot stands",
      (piece, got["resolved_from"], got["coverageRead"]["confirmed"]), ("萬", "adjudicator-empty", True))
piece, got, _ = pp.confirm_nothing(Blind("數日間捐集十餘萬〇此次捐助軍費女子尙"), b"", WAN, answered_wan, 100, "A")
check("confirm_nothing: a transcription with the circle at the spot as 〇 does not confirm it (a figure or "
      "a withheld name prints the same glyph)", (piece, got["resolved_from"]), ("萬〇", "adjudicator-empty-unconfirmed"))
piece, got, _ = pp.confirm_nothing(Blind("女子尙"), b"", dict(WAN, furnitureSides=["a", "b"]), answered_nothing,
                                   100, "A")
check("confirm_nothing: a reading the book's furniture explains is not checked",
      (piece, "coverageRead" in got), ("", False))

# The same on a page: 裁決's 無 on the fused run 萬〇/萬, the crop showing 萬 alone.
WAN_PRINTED = "又不開門貿易巡警嚴道經過陶街口覩此情形三數日間捐集十餘萬此次捐助軍費女子尙能如此況男子乎"
record, markdown = page([(WAN_PRINTED.replace("十餘萬此次", "十餘萬〇此次"), [0.3, 0.1, 0.1, 0.8])], WAN_PRINTED,
                        coverage=WAN_PRINTED, **nothing)
check("page: a numeral 無 whose kept digits the crop shows: written as answered, no doubt, no change entry",
      ("十餘萬此次" in pp.CJK(markdown), [a["resolved_from"] for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]], [c["rule"] for c in record["decisionChanges"]]),
      (True, ["adjudicator-empty"], [], []))

# --- D16 with the reading nearby -------------------------------------------------------
check("reading_nearby: a header read by rows stands right after, in order and close together",
      pp.reading_nearby("全書每", "表如左書名", "册全數書課每數册備用年期"),
      {"side": "after", "distance": 1, "span": 5, "text": "全數書課每"})
check("reading_nearby: a price cell is not found in the cells beside it",
      pp.reading_nearby("二册二角八分", "省城文德里學務公所印刷處", "二角簡易識字課本卷首每册四分同前一册四分"), None)
check("reading_nearby: beyond the window it is not nearby",
      pp.reading_nearby("全書每", "", "某" * pp.NEARBY_WINDOW + "全書每"), None)
check("left_out_of: what an answer leaves out of the reading", pp.left_out_of("用敎授要", "敎"), "用授要")
# nlc-finance pp.27-28 (v1.5): which copy of a figure is the second one the page does not
# say - letting 裁決's 無 go took away the copy in the right cell.
check("reading_nearby: figures are never taken for a second copy, even the same figures in a row",
      (pp.reading_nearby("八八一二五", "九一七七七三九四〇八八一二五三七八二一三四四〇", ""),
       pp.reading_nearby("三六二二四七六", "六七八五七〇四七六三六二二四二五二一九五〇二一九三〇〇〇七六", "")), (None, None))

HEADER_A = "表如左書名" + "册全數書課每數册" + "備用年期" + BODY
HEADER_B = "表如左書名" + "全書每" + "册全數書課每數册" + "備用年期" + BODY
adjudicate = {"preferEngine": "adjudicate"}
record, markdown = page([(HEADER_A, [0.3, 0.1, 0.4, 0.8])], HEADER_B, profile=adjudicate, **nothing)
check("page: 無 for a reading the merged text holds nearby is let go (D16, as the user ruled)",
      (pp.CJK(markdown).count("全書每"), [a["resolved_from"] for a in record["adjudications"]],
       [d["kind"] for d in record["doubts"]]), (0, ["adjudicator-empty"], []))
check("page: sealed as a D16 change", [(c["decision"], c["rule"], c["engineB"], c["after"],
                                        c["evidence"]["nearby"]["text"]) for c in record["decisionChanges"]],
      [("D16", "answer-accepted-reading-nearby", "全書每", "", "全數書課每")])
check("page: the rule's key in the provenance, and the reading let go is no dropped reading",
      (record["provenance"]["answerNearby"], record["readingsDropped"]), ("on", []))

# A 無 D16 would let go because the reading stands nearby is still checked against its
# crop (confirm_nothing): engine A skipped the second of two 大總統, and the crop is of
# another column.
PRINTED = BODY + "茲奉大總統令着大總統府秘書長即日到任" + BODY2
SKIPPED = BODY + "茲奉大總統令着府秘書長即日到任" + BODY2
for coverage, want in (("寒來暑往秋收冬藏閏餘", (1, ["adjudicator-deletion-refused"], ["deletion-refused"],
                                                   [("answer-deletion-guard", "unanchored")])),
                       (None, (0, ["adjudicator-empty"], [], [("answer-accepted-reading-nearby", "placed")]))):
    with stand_in.book({1: ([stand_in.block(SKIPPED, [0.3, 0.1, 0.4, 0.8])], PRINTED)}, profile=adjudicate) as root:
        model = stand_in.StandIn(census=0, coverage=coverage, **nothing)
        record, markdown = stand_in.run(root, model)[1]
    check("page: 無 for a reading that stands nearby, "
          + ("the crop of another column: refused, with a doubt" if coverage else "the crop shows the spot empty: let go"),
          (pp.CJK(markdown).count("着大總統府"), [a["resolved_from"] for a in record["adjudications"]],
           [d["kind"] for d in record["doubts"]],
           [(c["rule"], (c["evidence"].get("coverageRead") or {}).get("found")) for c in record["decisionChanges"]
            if c["decision"] == "D16"]), want)
    check("page: the coverage read is one more call", model.kinds().count("coverage-read"), 1)

if failures:
    print(f"{failures} failure(s)")
    sys.exit(1)
print("all text-loss checks passed")
