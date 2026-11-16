#!/usr/bin/env python3
"""Checks for bullets and dot leaders as printed marks (decisions D4 and D5): the mark
vocabulary, a run of dots counted as one mark written 「……」, the count of dot runs
taken off the crop with no model, and each one the page keeps sealed in
decisionChanges.

No model, no GPU, no corpus: the images are drawn here.  The shapes are the ones
measured on a magazine: a table of contents whose seven leaders the census counted
as no marks at all, and a ▲ nothing in the pipeline counted or wrote.

Usage: python3 tests/check_leaders.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# The vocabulary.
check("a run of leader dots is one mark", pp.punctuation_counts("篇名……作者，"), {"……": 1, "，": 1})
check("however many dots", pp.punctuation_counts("甲…………乙⋯⋯丙…丁"), {"……": 3})
# A run opening the text: it was never counted (the census match, mark_places, saw one
# place fewer, and the structure pass's lock let it be dropped).
check("a run opening the text is one mark too",
      (pp.punctuation_counts("……甲……乙"), pp.punctuation_counts("⋯"), pp.mark_places("……甲。")),
      ({"……": 2}, {"……": 1}, 2))
check("the structure pass may not drop a leader opening the text", pp.punctuation_kept("……篇名", "篇名"), False)
check("bullets are marks", pp.punctuation_counts("▲本刊●言"), {"▲": 1, "●": 1})
check("the census and the punctuator are told", ["項目符號（▲ △ ● ◎ ⦿ ◆ ■ 之類）" in s and "一串點（" in s
                                                  for s in (pp.CENSUS_SYSTEM, pp.PUNCT_SYSTEM, pp.FORMAT_SYSTEM)],
      [True, True, True])
# ⦿ is a mark (a page printing ⦿⦿ before each heading had them written 。。 by the
# vocabulary without it), but its shape is not enforced: the user ruled ●, ⦿ and a
# doubled ⦿⦿ one bullet, accepted either way (D5 after the baseline).  No prompt calls
# them different symbols or asks for the doubled form.
check("⦿ in the vocabulary, but no prompt tells ● from ⦿ or asks for ⦿⦿",
      [("⦿" in s, "係唔同嘅符號" in s, "「⦿⦿」" in s) for s in (pp.PUNCT_SYSTEM, pp.FORMAT_SYSTEM)],
      [(True, False, False)] * 2)
check("the census counts a bullet printed twice as one place", "（例如 ⦿⦿）都係一處" in pp.CENSUS_SYSTEM, True)
check("⦿ is a mark", pp.punctuation_counts("⦿⦿諮議局電。"), {"⦿": 2, "。": 1})
check("a bullet printed twice is one place, as a doubled circle is", pp.mark_places("⦿⦿諮議局電。。"), 2)
check("bullet runs", pp.bullet_runs("⦿⦿甲●乙▲▲▲丙"), [(0, "⦿⦿"), (3, "●"), (5, "▲▲▲")])
check("a doubled bullet in two of the shapes ruled one (●⦿, ◉●) is one place",
      (pp.bullet_runs("●⦿甲◉●乙●▲丙"), pp.mark_places("●⦿甲")), ([(0, "●⦿"), (3, "◉●"), (6, "●"), (7, "▲")], 1))
# ◉ is the third shape the user named (2026-09-26): a mark, one place printed twice.
check("◉ is a mark, and a doubled ◉◉ one place",
      (pp.punctuation_counts("◉諮議局電。"), pp.mark_places("◉◉諮議局電。"), pp.bullet_runs("◉◉甲")),
      ({"◉": 1, "。": 1}, 2, [(0, "◉◉")]))
check("a bullet the census names otherwise is contradicted; ●, ⦿ and ◉ are one bullet",
      [pp.bullet_contradicted(b, s) for b, s in (("●●", "。⦿"), ("⦿⦿", "。●"), ("⦿⦿", "。⦿"), ("▲", "。●"),
                                                  ("●", "。▲"), ("●", "。、"), ("●", None),
                                                  ("◉", "。●"), ("●●", "。◉"), ("◉", "。▲"))],
      [False, False, False, True, True, False, False, False, False, True])
check("a leader is written 「……」 whatever its dots", "寫「……」，唔理印咗幾多粒點" in pp.PUNCT_SYSTEM, True)


check("both are counted", pp.punctuation_counts("▲本刊……作者，"), {"▲": 1, "……": 1, "，": 1})
check("the structure pass may not drop a leader",
      pp.punctuation_kept("篇名……作者", "篇名作者"), False)


def page_image(dots: int, rule: bool = False, columns: int = 5) -> Image.Image:
    """A vertical block: columns of square glyphs; the first has DOTS round dots
    between a title and an author, or with RULE a faint ruled line's fragments."""
    image = Image.new("L", (columns * 60 + 20, 1400), 255)
    draw = ImageDraw.Draw(image)
    for column in range(columns):
        x = image.width - 70 - column * 60
        glyphs = range(20, 1360, 50) if column else list(range(20, 420, 50)) + [1250, 1300]
        for y in glyphs:
            # A glyph: strokes across the column and one down it, mostly paper.
            top = y + column * 7
            for bar in (0, 20, 40):
                draw.rectangle((x, top + bar, x + 44, top + bar + 3), fill=0)
            draw.rectangle((x + 20, top, x + 23, top + 43), fill=0)
        if column == 0:
            for k in range(dots):
                cy = 440 + 25 * k
                draw.ellipse((x + 17, cy, x + 27, cy + 10), fill=0)
            if rule:
                for k in range(20):
                    draw.rectangle((x + 20, 440 + 30 * k, x + 22, 440 + 30 * k + 22), fill=0)
    return image


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


check("a leader of 20 dots down a column", pp.leader_count(png(page_image(20))), 1)
check("a run of 6 dots, as a short ellipsis prints, is left to the census", pp.leader_count(png(page_image(6))), 0)
check("a faint ruled line's fragments are not dots", pp.leader_count(png(page_image(0, rule=True))), 0)
check("horizontal text is not measured", pp.leader_count(png(page_image(20).transpose(Image.ROTATE_90))), None)
check("no crop, no count", pp.leader_count(None), None)


class Arguments:
    max_tokens = 100
    profile_data = None


class Client:
    model, effort = "fake", None

    def __init__(self, census: str, answers: list[str]):
        self.census, self.answers, self.prompts = census, list(answers), []

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        self.prompts.append(user_text)
        metrics = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        if system is pp.CENSUS_SYSTEM:
            return self.census, metrics
        return self.answers.pop(0), metrics


span = "共匪是內憂也是外患顚公"
answer = "共匪是內憂也是外患……顚公"
client = Client("符號：0\n雙圈：0", [answer])
out, record, _ = pp.punctuate_part(client, Arguments(), png(page_image(20)), span, "", 60.0)
check("a census of 0 where the crop prints a leader: the pass still runs", (out, record["ok"]), (answer, True))
check("the pass records both counts", (record["census"], record["modelCensus"], record["leaderCensus"],
                                       record["leaders"]), (1, 0, 1, 1))
check("the punctuator is told the dot runs", "量到 1 串點" in client.prompts[1], True)
client = Client("符號：0\n雙圈：0", [answer])
out, record, _ = pp.punctuate_part(client, Arguments(), png(page_image(0)), span, "", 60.0)
check("no dots and a census of 0: nothing to add", (out, record["reason"], len(client.prompts)),
      (span, "no printed marks", 1))

# A page: the punctuator writes a bullet and a leader; each is sealed.
text = "本刊徵文簡約反共救民會宣言"
bulleted = lambda s: ("▲" + s[:6] + "……" + s[6:]) if s.startswith("本刊") else s
with stand_in.book({1: ([stand_in.block(text, [0.3, 0.1, 0.4, 0.8])], text)}) as root:
    record, markdown = stand_in.run(root, stand_in.StandIn(census=2, punctuate=bulleted))[1]
check("page: both kept", markdown.strip(), "▲本刊徵文簡約……反共救民會宣言")
check("page: each sealed for its decision",
      [(c["decision"], c["rule"], c["after"], c["block"], c["mergedOffset"], c["contextBefore"], c["contextAfter"])
       for c in record["decisionChanges"]],
      [("D5", "bullet", "▲", 0, 0, "", "本刊徵文"), ("D4", "dot-leader", "……", 0, 6, "徵文簡約", "反共救民")])
check("page: provenance", (record["provenance"]["leaderMarks"], record["provenance"]["bulletMarks"]), ("on", "on"))

# D4 is "always 「……」": a leader the punctuator writes with three dots, or one, is
# written 「……」 on the page, as its entry says, with the run as written kept.
check("leaders_written", pp.leaders_written("甲………乙…丙⋯⋯丁……"), "甲……乙……丙……丁……")
dotted = lambda s: (s[:6] + "………" + s[6:9] + "…" + s[9:]) if s.startswith("本刊") else s
with stand_in.book({1: ([stand_in.block(text, [0.3, 0.1, 0.4, 0.8])], text)}) as root:
    record, markdown = stand_in.run(root, stand_in.StandIn(census=2, punctuate=dotted))[1]
    # A structure pass that writes the block's 「……」 with three dots: its lock counts
    # a run as one mark, so it passes; the page writes the run back.
    stretched, stretched_markdown = stand_in.run(root, stand_in.StandIn(
        census=2, punctuate=bulleted, formatter=lambda t, kind: t.replace("……", "………")))[1]
check("page: every run of dots written 「……」", markdown.strip(), "本刊徵文簡約……反共救……民會宣言")
check("page: each entry keeps the run as written",
      [(c["decision"], c["after"], c["evidence"]["written"], c["mergedOffset"]) for c in record["decisionChanges"]],
      [("D4", "……", "………", 6), ("D4", "……", "…", 9)])
check("structure pass: the run written back as sealed", stretched_markdown.strip(), "▲本刊徵文簡約……反共救民會宣言")
check("structure pass: the rewrite sealed apart",
      [(c["decision"], c["rule"], c["evidence"]["written"], c["after"], c["pageOffset"])
       for c in stretched["decisionChanges"] if c["rule"] == "dot-leader-rewritten"],
      [("D4", "dot-leader-rewritten", "………", "……", 6)])

# A page: the punctuator writes a doubled bullet before the heading.  One entry for the
# run, as written, with what the census saw on the same crop sealed beside it: another
# bullet (▲ for ●) is shapeContradicted, ⦿ and ◉ are not (the same bullet by the user's
# ruling).  Never a doubt: the user does not want to be asked about bullet shapes.
heading = "諮議局電內閣文"
doubled = lambda s: "●●" + s + "。"
for census, contradicted in (("2\n形狀：。⦿", False), ("2\n形狀：。●", False), ("2\n形狀：。◉", False),
                             ("2\n形狀：。▲", True)):
    with stand_in.book({1: ([stand_in.block(heading, [0.3, 0.1, 0.1, 0.5])], heading)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=census, punctuate=doubled))[1]
    check(f"page: the bullet run kept, one D5 entry, the census's shape sealed ({census.split('：')[1]})",
          (markdown.strip(), [(c["after"], c["evidence"].get("censusShapes"), c["evidence"].get("shapeContradicted"))
                              for c in record["decisionChanges"] if c["decision"] == "D5"]),
          ("●●" + heading + "。", [("●●", census.split("：")[1], contradicted)]))
    check(f"page: no doubt about the bullet's shape, clean ({census.split('：')[1]})",
          ([d["kind"] for d in record["doubts"]], record["auditorClean"]), ([], True))

# The seal holds the kept stretch to the census the way the pass was held to it: three
# doubled bullets and three circles are six places (mark_places), sealed as such, and a
# pass the census accepted is not sealed as a count mismatch (nine marks).
three = "諮議局電內閣文總督覆電軍政府佈告"
with stand_in.book({1: ([stand_in.block(three, [0.3, 0.1, 0.1, 0.5])], three)}) as root:
    record, markdown = stand_in.run(root, stand_in.StandIn(
        census="6\n雙圈：0", punctuate=lambda s: "⦿⦿諮議局電內閣文。⦿⦿總督覆電。⦿⦿軍政府佈告。"))[1]
check("page: three doubled bullets and three circles are six places, sealed, and clean",
      ([(p["ok"], p["census"], p["marks"], p.get("places")) for p in record["punctuationPasses"]], record["doubts"],
       record["auditorClean"]), ([(True, 6, 9, 6)], [], True))
check("seal: a pass sealed with its places is held to them; one sealed before, to its doubled circles",
      [[d["kind"] for d in pp.punctuation_doubts([dict(census=6, marks=9, doubled=0, block=0, ok=True,
                                                       censusConvention="places", **extra)])]
       for extra in ({"places": 6}, {})], [[], ["punctuation-count-mismatch"]])

# The scorer folds them too: ●, ⦿ and ⦿⦿ are one bullet wherever marks are compared.
import run_regression as rr  # noqa: E402

golden = "⦿⦿諮議局電內閣文。●連日有謂旗滿。"
for output in ("●諮議局電內閣文。⦿連日有謂旗滿。", "●●諮議局電內閣文。⦿⦿連日有謂旗滿。", golden):
    metrics = rr.score_golden_page(golden, [(1, output)])
    check(f"scorer: {output[:2]}… against the golden's ⦿⦿ and ●: every mark right",
          (metrics["marksRight"], metrics["marksGolden"], metrics["marksOutput"]), (4, 4, 4))
check("scorer: a bullet left out is still missed",
      rr.score_golden_page(golden, [(1, "諮議局電內閣文。●連日有謂旗滿。")])["marksRight"], 3)
check("scorer: contains_exact and absent fold them", (rr.user_fold("⦿⦿諮議局電") in rr.user_fold("。●諮議局電內"),
                                                       rr.user_fold("▲諮議") in rr.user_fold("●諮議")), (True, False))

# The user's ruling of 2026-09-26: ●, ⦿ and ◉, printed single or doubled, are symbol
# variants of one bullet.  A run of them in the golden or the output is one bullet mark
# and any shape matches any shape; the golden keeps its printed form.  ◎ and the other
# marks are not folded.
golden = "◉諮議局電內閣文。⦿⦿連日有謂旗滿。●●善後分所。"
for output in ("⦿⦿諮議局電內閣文。◉連日有謂旗滿。◉◉善後分所。", "●諮議局電內閣文。●連日有謂旗滿。⦿善後分所。",
               "●⦿諮議局電內閣文。◉●連日有謂旗滿。◉⦿善後分所。", golden):
    metrics = rr.score_golden_page(golden, [(1, output)])
    check(f"scorer: {output[:2]}… against the golden's ◉, ⦿⦿ and ●●: every mark right, one mark each",
          (metrics["marksRight"], metrics["marksGolden"], metrics["marksOutput"]), (6, 6, 6))
check("scorer: ◎ is not folded - against a golden ◎ a ● is a wrong mark, and the other way round",
      [(m["marksRight"], m["marksGolden"], m["marksOutput"]) for m in (
          rr.score_golden_page("◎諮議局電。", [(1, "●諮議局電。")]),
          rr.score_golden_page("●諮議局電。", [(1, "◎諮議局電。")]),
          rr.score_golden_page("◉諮議局電。", [(1, "◎諮議局電。")]))], [(1, 2, 2)] * 3)
check("scorer: a golden's doubled ◉◉ is one mark token", rr.golden_units("◉◉諮議")[1][0], ["●"])
check("scorer: contains_exact and absent fold ◉ too",
      (rr.user_fold("◉諮議局電") in rr.user_fold("。⦿⦿諮議局電內"), rr.fold("●●諮議") == rr.fold("◉諮議"),
       rr.user_fold("◎諮議") in rr.user_fold("●諮議")), (True, True, False))

sys.exit(1 if failures else 0)
