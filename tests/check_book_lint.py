#!/usr/bin/env python3
"""Offline check of the automatic defect counters (scripts/_book_lint.py).

No model, no GPU, no corpus.  The clean made-up book (tests/repair_fixture.py)
counts nothing but its heading levels and its unlabelled page boundaries.
Then one book holds a positive case for each counter, next to a look-alike
that must not count:

- FURN-HEAD: a running-head line on scan 6 and a head glued into prose on
  scan 8 count; the contents title on scan 2, which holds the stem but
  engine A reads in its body, does not.
- FURN-SUFFIX / HEAD-FURNITURE: a `# 甲編` heading on scan 10 counts; the
  divider's own `## 甲編` on scan 5 does not.
- FURN-FOLIO: scan 11's folio 七 left as the last line counts; a 五 at the
  end of scan 14 (whose folio is 二) does not.
- FURN-ANSWER: an adjudicator's answer at the band spot of scan 7 (engine A
  read nothing, engine B the folio) counts.
- MARK-STANDIN: engine B's comma glyph 丶 sealed on scan 9 where engine A
  read a comma counts; the same glyph on scan 15, where engine A read it too,
  does not.
- MARK-REVERSED / MARK-FIGURE-LOST: 丁）（ on scan 17 where engine A read
  （1） counts twice; a ）（ on scan 19 that engine A read too, and a （2） on
  scan 20 that the sealed text keeps, do not.  The numbers engine A read at
  the start of scan 15's title and scan 3's contents line count as lost
  figures too.
- MARK-UNBALANCED: scan 17's reversed pair counts; a bracket opened at the
  end of scan 11 and closed at the start of scan 12 does not; a lone 〉 or
  ” counts, an apostrophe (’) does not.
- MARK-SHAPE: ︵完︶ on scan 18 counts.
- HEAD-NUMBER-LOST: scan 15's unit title without the 2 engine A read counts;
  scan 13's, which keeps its 1, does not.
- TOC-NUMBER-LOST / TOC-UNMATCHED: a contents entry without its number, and
  one with no unit in the body, count - also in a text that has lost a line
  above them since the model read the contents.  On a contents page that
  numbers no entry (each title ends with its page number, bracketed or not,
  and has a date line under it; one line starts with a figure) nothing
  counts but a title whose leaders end its line, an empty bracket after
  them or not (its page number lost).
- HEAD-FRAGMENT: a heading at the top of scan 20 that ends a sentence scan
  19 left unfinished counts; scan 15's unit title after a finished page
  does not.
- ANSWER-PROSE: a ten-character answer to a one-character question counts.
- TEXT-LOST: a character both engines read that scan 9 lost counts; a line
  lost whole is one hit naming it, and so is a heading moved out of its
  place, named with engine A's text either side.  Characters both engines
  read in two scripts (天地会 3 scan 181: 搗賊 and 捣贼, left out by an
  answer) are one hit naming both readings; a page in the other script
  loses nothing.  Characters both engines read a line apart (天地会 5 scan
  3's 合编, 天地会 3 scan 38's 音, each left out by two answers) are one
  hit; a page that holds them at either engine's place loses nothing.  A
  character both engines read written in the other script (后 for 後) is
  lost.  The fold takes variants first (麼 and 麽 one character, named
  once) and a traditional character's first simplified form (開 as 开, not
  𫔭); engine B's reading of a run is what it read aligned with it, not
  its stand-in 丶 inside the run.
- TEXT-TRADITIONAL, on real readings of 天地会 3: scan 75's title, which
  engine A read in traditional script and engine B as printed, in
  simplified script, is one doubt naming each such character but 內 and
  戶 (which the alignment pairs with engine B's 内 and 户); a 彫 engine B
  read where engine A read the printed 彪 (scan 321), an adjudicator's 懸
  where engine A read 恳 and engine B 悬 (scan 40), and a printed 崙 engine
  B read as 嵛 (scan 93; its two simplified forms split by ／) count; scan
  56's 崙, which both engines read, does not.  Each such character is quoted
  with ten characters either side, the pieces joined by …: a line of scan
  119 whose two 崙 are far apart quotes neither the characters between.  A sentence of a traditional
  book (盜區政區) is not in simplified script, the archive's are (a text
  is when the simplified forms outnumber the traditional-only characters:
  諭谕谕, not 諭谕); the made-up book, in traditional script, is not
  measured.  The table: the user's list comes first (則, accepted as 则,
  is not a traditional-only character; 礙, ruled apart from 碍, is); 戶
  and 黃 are not (OpenCC's allographs take them for 户 and 黄), nor 麼 (an
  allograph of 麽, one of its simplified forms), nor 乾 and 於 (their own
  simplified forms too).
- JOIN-UNRESOLVED: every marker without a boundary label; none with one.
- On a subset of the pages the neighbours are the book's pages, not the
  subset's.
- AUDIT-ANNOTATED: a heading with no blank line under it counts.
- A counter whose gate failed is not measured (no count), and the command
  line fails with --fail-on a counter that counts, and with exit 2 on a
  --fail-on name that is no counter (a misspelt one would never fail).

Usage:
    python3 tests/check_book_lint.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
import _book_lint as bl  # noqa: E402
import _repair_inputs as ri  # noqa: E402
import _repair_model as rm  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def lint(root: Path, spec, boundaries=None) -> dict:
    fx.write_book(root, spec)
    book = ri.load_book(ri.InputPaths.resolve(root, {}))
    model = rm.learn(book)
    return bl.lint_book(book, model, boundaries=boundaries)


def pages_of(result: dict, name: str) -> list[int]:
    return sorted(h["page"] for h in result["hits"][name])


def first_chars(spec, scan: int, size: int) -> str:
    """The first SIZE characters of the page's first paragraph."""
    text = next(item[1] for item in spec[scan]["items"] if item[0] == "para")
    return text[:size]


def defects(spec) -> None:
    spec[6]["extraSealed"] = [fx.HEAD + "甲編"]
    first = first_chars(spec, 8, 2)
    spec[8]["sealedEdits"] = [(first, first + fx.HEAD)]
    spec[10]["extraSealed"] = ["# 甲編"]
    spec[11]["extraSealed"] = ["見下頁（甲", "七"]
    first = first_chars(spec, 12, 2)
    spec[12]["sealedEdits"] = [(first, "乙）" + first)]
    spec[14]["extraSealed"] = ["五"]
    body = pp_cjk(fx.printed_text(spec[7]))
    spec[7]["extraSealed"] = ["一"]
    spec[7]["adjudications"] = [{"context_before": body[-8:], "context_after": fx.HEAD + "甲編",
                                 "draft_reading": "", "writer_reading": "三", "resolved": "一",
                                 "resolved_from": "adjudicator"}]
    body12 = pp_cjk(fx.printed_text(spec[12]))
    spec[12]["adjudications"] = [{"context_before": "", "context_after": "", "draft_reading": "甲",
                                  "writer_reading": "乙", "resolved": body12[20:30], "resolved_from": "adjudicator"}]
    spec[9]["sealedEdits"] = [("，", "丶"), (first_chars(spec, 9, 12)[9:12], first_chars(spec, 9, 12)[9:11])]
    spec[15]["sealedEdits"] = [("，", "丶"), ("### 2 田園小記", "### 田園小記")]
    spec[15]["aEdits"] = [("，", "丶")]
    first = first_chars(spec, 17, 2)
    spec[17]["sealedEdits"] = [(first, first + "丁）（")]
    spec[17]["aEdits"] = [(first, first + "（1）")]
    first = first_chars(spec, 19, 2)
    spec[19]["sealedEdits"] = [(first, first + "）（")]
    spec[19]["aEdits"] = [(first, first + "）（")]
    last = fx.printed_text(spec[19]).rstrip()[-3:]
    spec[19]["sealedEdits"].append((last, last[:-1]))
    first = first_chars(spec, 20, 2)
    spec[20]["items"] = [("para", first + "（2）" + item[1][2:], True) if k == 0 else item
                         for k, item in enumerate(spec[20]["items"])]
    spec[20]["sealedEdits"] = [(first + "（2）", "# " + first + "（2）")]
    spec[18]["sealedEdits"] = [("。", "︵完︶。")]
    paragraphs = [item[1] for item in spec[16]["items"] if item[0] == "para"]
    spec[16]["sealedEdits"] = [(paragraphs[1] + "\n\n" + paragraphs[2], paragraphs[1] + paragraphs[2])]
    spec[3]["sealedEdits"] = [("1 晨鐘暮鼓", "晨鐘暮鼓")]
    spec[3]["extraSealed"] = ["4 竹林清話"]
    spec[4]["sealedEdits"] = [(first_chars(spec, 4, 2), "#### 附言\n" + first_chars(spec, 4, 2))]


def pp_cjk(text: str) -> str:
    return fx.pp.CJK(text)


def toc_lost(lines: list[str]) -> list[str]:
    """TOC-NUMBER-LOST on a contents page of LINES, each a contents row the
    book model matched to the body (lint_page on its own)."""
    text = "\n\n".join(lines) + "\n"
    rows = [{"page": 3, "line": 2 * n + 1, "text": pp_cjk(line), "matchedPage": 9} for n, line in enumerate(lines)]
    job = {"scan": 3, "text": text, "entry": {}, "a_body": text, "b_body": text, "adjudications": [],
           "facts": {"stems": [], "suffixes": [], "contentsWord": None, "finals": "。", "standins": []},
           "contents_rows": rows, "previous_text": None}
    return [h["text"] for h in bl.lint_page(job)["TOC-NUMBER-LOST"]]


def text_lost(text: str, a: str, b: str | None = None) -> list[str]:
    """TEXT-LOST on a page whose text is TEXT, engine A reading A and engine
    B reading B (default A too) (lint_page on its own)."""
    job = {"scan": 1, "text": text, "entry": {}, "a_body": a, "b_body": a if b is None else b, "adjudications": [],
           "facts": {"stems": [], "suffixes": [], "contentsWord": None, "finals": "。", "standins": []},
           "contents_rows": [], "previous_text": None}
    return [h["text"] for h in bl.lint_page(job)["TEXT-LOST"]]


def traditional_checks(clean: dict) -> None:
    title = "諭內閣著戶部酌拨銀一百萬兩解閩"
    date = "乾隆五十二年七月十三日 台湾档"
    body = "前往夹攻。讵行至竹仔脚、大崙田洋，该处离鹿仔草十余里"
    # Scan 119's paragraph (cut short): its two 崙 far apart, engine B
    # reading 嵛.
    far = ("十八日带领官兵至埔心、大崙等庄，与贼打仗，竟有万余。官兵奋勇杀出重围，伤亡者亦多，现在禀请拨兵救援。"
           "即于十八日带兵四百名，同往大崙、埔心剿捕，致被围，")
    # Sentences of scans 321, 40 and 93 and 119: sealed, engine A, engine B.
    others = (("适参将潘韬、守备杨彫等，均飞差报称", "适参将潘韬、守备杨彪等，均飞差报称", "适参将潘韬、守备杨彫等，均飞差报称"),
              ("朕连日盼望捷音，甚为懸切。今阅常青等奏到情", "朕连日盼望捷音，甚为恳切。今阅常青等奏到情",
               "朕连日盼望捷音，甚为悬切。今阅常青等奏到情"),
              ("带领官兵义民至埔心、大崙等庄，与贼打仗", "带领官兵义民至埔心、大崙等庄，与贼打仗",
               "带领官兵义民至埔心、大嵛等庄，与贼打仗"),
              (far, far, far.replace("崙", "嵛")))
    text = f"## {title}\n\n{date}\n\n{body}\n\n" + "\n\n".join(o[0] for o in others) + "\n"
    job = {"scan": 75, "text": text, "entry": {},
           "a_body": f"{title}\n{date}\n{body}\n" + "\n".join(o[1] for o in others),
           "b_body": f"## 谕内阁著户部酌拨银一百万两解闽\n{date}\n{body}\n" + "\n".join(o[2] for o in others),
           "adjudications": [],
           "facts": {"stems": [], "suffixes": [], "contentsWord": None, "finals": "。", "standins": [],
                     "traditional": bl.traditional_only()},
           "contents_rows": [], "previous_text": None}
    got = [(h["line"], h["text"]) for h in bl.lint_page(job)["TEXT-TRADITIONAL"]]
    want = [(1, "諭（谕）閣（阁）銀（银）萬（万）兩（两）閩（闽）：## 諭內閣著戶部酌拨銀一百萬兩解閩"),
            (7, "彫（雕）：适参将潘韬、守备杨彫等，均飞差报称"),
            (9, "懸（悬）：朕连日盼望捷音，甚为懸切。今阅常青等奏到情"),
            (11, "崙（仑／𪨧）：领官兵义民至埔心、大崙等庄，与贼打仗"),
            (13, "崙（仑／𪨧）：日带领官兵至埔心、大崙等庄，与贼打仗，竟有…日带兵四百名，同往大崙、埔心剿捕，致被围，")]
    check(got == want, f"TEXT-TRADITIONAL: one doubt a line for the characters only traditional script has where "
                       f"the engines did not both read them, but 內 and 戶, each with ten characters either side "
                       f"({got})")
    traditional = ("明代中期以後，官方在閩粵贛湘交界設置南贛巡撫，正式設立新「政區」，並責付軍事討賊為主的任務，"
                   "不預民事，以解決當地「盜區」層出不窮的「三不管」政治社會問題。")
    check(bl.simplified_book([text, body]) and not bl.simplified_book([traditional]),
          "the archive volume's text is in simplified script, the monograph's is not")
    check(bl.simplified_book(["諭谕谕"]) and not bl.simplified_book(["諭谕"]),
          "a text is in simplified script when the simplified forms outnumber the traditional-only characters")
    entry = clean["counters"]["TEXT-TRADITIONAL"]
    check(entry["count"] is None and "simplified" in entry["notMeasured"],
          f"TEXT-TRADITIONAL is not measured on a book in traditional script ({entry})")
    table = bl.traditional_only()
    check("則" not in table and table.get("礙") == "碍" and table.get("諭") == "谕",
          "the user's variant list comes first: 則/则 accepted (no doubt), 礙/碍 ruled apart (a doubt)")
    check(not set("戶黃麼乾於") & set(table),
          "no doubt for 戶 and 黃 (OpenCC's allographs), 麼 (an allograph of its simplified form 麽), 乾 and 於 "
          "(their own simplified forms too)")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clean = lint(tmp / "clean", fx.make_spec())
        traditional_checks(clean)
        counts = bl.summary(clean)
        counting = {name: n for name, n in counts.items() if n and name not in ("HEAD-LEVELS", "JOIN-UNRESOLVED")}
        check(not counting and counts["JOIN-UNRESOLVED"] == 20,
              f"the clean made-up book counts only its heading levels and its 20 unlabelled boundaries ({counting})")

        spec = fx.make_spec()
        defects(spec)
        result = lint(tmp / "defects", spec)
        expect = {
            "FURN-HEAD": [6, 8], "FURN-SUFFIX": [10], "HEAD-FURNITURE": [10], "FURN-FOLIO": [11],
            "FURN-ANSWER": [7], "MARK-STANDIN": [9], "MARK-REVERSED": [17], "MARK-FIGURE-LOST": [3, 15, 17],
            "MARK-UNBALANCED": [17, 19], "MARK-SHAPE": [18, 18], "HEAD-NUMBER-LOST": [15],
            "TOC-NUMBER-LOST": [3], "TOC-UNMATCHED": [3], "HEAD-FRAGMENT": [20],
            "ANSWER-PROSE": [12], "TEXT-LOST": [9],
        }
        for name, pages in expect.items():
            got = pages_of(result, name)
            check(got == pages, f"{name}: counts on scans {pages} and nowhere else (got {got})")
        # MARK-UNBALANCED counts every pair of CJK print, 《》〈〉“”〔〕 too (a
        # title mark's half, a closing quote with none open), but not ‘’ (’
        # is an apostrophe in Latin text too).
        pages = {1: "卷11藝文〉，頁10a", 2: "众兄弟说：汨君无道，雪恨报仇”。", 3: "〔清〕嚴如，《三省》〈序〉“甲”",
                 4: "It’s the bandits’ era（甲）"}
        got = [h["page"] for h in bl.unbalanced_pages(pages, sorted(pages))]
        check(got == [1, 2], f"MARK-UNBALANCED: a lone 〉 and a lone ” count; paired 〔〕《》〈〉“” and an "
                             f"apostrophe do not (got {got})")
        audit = result["counters"]["AUDIT-ANNOTATED"]
        check(audit["count"] >= 1, f"AUDIT-ANNOTATED counts the heading with no blank line under it ({audit})")

        # The text counted may have lost lines since the model read the
        # contents (a repair deleted scan 3's group line above its entries):
        # each contents row is found again by its characters.
        book = ri.load_book(ri.InputPaths.resolve(tmp / "defects", {}))
        texts = {scan: book.pages[scan].sealed_text for scan in book.assembly.order}
        texts[3] = texts[3].split("\n\n", 1)[1]
        moved = bl.lint_book(book, rm.learn(book), texts)
        lost = [h["text"] for h in moved["hits"]["TOC-NUMBER-LOST"]]
        unmatched = [h["text"] for h in moved["hits"]["TOC-UNMATCHED"]]
        check(lost == ["晨鐘暮鼓"] and unmatched == ["4 竹林清話"],
              f"TOC-NUMBER-LOST / TOC-UNMATCHED find their contents lines by their text once lines above them "
              f"are gone ({lost}, {unmatched})")

        # A contents that numbers no entry: each title ends with its page
        # number (bracketed or not) and has a date line under it.  One line
        # starts with a figure (a repaired date line, as on a 天地会 page):
        # the page still numbers none.
        archive = ["谕福建巡抚查办海口折……（1）", "乾隆五十二年三月十三日批", "闽浙总督奏收复凤山折……3",
                   "乾隆五十二年三月十四日", "福建水师提督奏官兵渡台折……（6）", "乾隆五十二年三月十五日",
                   "（8）（批文日期缺）"]
        check(toc_lost(archive) == [],
              f"TOC-NUMBER-LOST: nothing on a contents page that numbers no entry, its date lines included "
              f"({toc_lost(archive)})")
        archive[2] = "闽浙总督奏收复凤山折……"
        archive[4] = "福建水师提督奏官兵渡台折……（）"
        check(toc_lost(archive) == ["闽浙总督奏收复凤山折……", "福建水师提督奏官兵渡台折……（）"],
              f"TOC-NUMBER-LOST: there, a title whose leaders end its line, an empty bracket after them or not, "
              f"lost its page number ({toc_lost(archive)})")
        # Phase 3 sealed a title and its date line as one line (<br>
        # between): leaders before the <br> lost their number too.
        run_on = ["谕福建巡抚查办海口折……<br>乾隆五十二年三月十三日批", "闽浙总督奏收复凤山折……3<br>乾隆五十二年三月十四日",
                  "福建水师提督奏官兵渡台折……（6）<br>乾隆五十二年三月十五日"]
        check(toc_lost(run_on) == [run_on[0]],
              f"TOC-NUMBER-LOST: a title's leaders before a <br> with no page number after them count, "
              f"leaders before their number do not ({toc_lost(run_on)})")

        # A line lost whole (天地会 5's scan 361: the end of a biography and
        # its source line) is one hit naming it; a section number printed at
        # the foot that the page holds as a heading at its head (陳總司令言論集
        # scan 107) is one too, named with engine A's text either side.
        lost = text_lost("事闻，予恤袭职，\n", "事闻，予恤袭职，祀昭忠祠。\n《彰化县志》，卷三。")
        check(lost == ["…予恤袭职[祀昭忠祠彰化县志卷三]…"],
              f"TEXT-LOST: the characters of a line lost whole are one hit, named with engine A's context ({lost})")
        lost = text_lost("# 五\n\n極應注意的一件事。\n\n第四點，現在是訓政時期，\n",
                         "極應注意的一件事。\n五\n第四點，現在是訓政時期，")
        check(lost == ["…的一件事[五]第四點現…"],
              f"TEXT-LOST: a section number the page holds out of its place is a hit, named with engine A's "
              f"text either side ({lost})")
        # 天地会 3 scan 181 (real readings): engine A read 迸搗賊 in
        # traditional script, engine B 进捣贼, 裁決 answered 进 and the page
        # wrote 「进巢」.  The two characters both engines read, in two
        # scripts, are one hit naming both readings; a page written in the
        # other script than engine A's (蒇 for 蕆, 贼 for 賊) loses nothing.
        a181 = "合柴大紀擒拿賊首賊目，迸搗賊巢，迅速蕆功。"
        b181 = "合柴大纪擒拿贼首贼目，进捣贼巢，迅速蒇功。"
        lost = text_lost("合柴大紀擒拿賊首賊目，进巢，迅速蕆功。\n", a181, b181)
        check(lost == ["…首賊目迸[A 搗賊 / B 捣贼]巢迅速蕆…"],
              f"TEXT-LOST: characters both engines read in two scripts that an answer left out are one hit, "
              f"naming both readings ({lost})")
        lost = text_lost("合柴大纪擒拿贼首贼目，进捣贼巢，迅速蒇功。\n", a181, b181)
        check(lost == [], f"TEXT-LOST: nothing lost where the page holds the engines' reading in the other "
                          f"script ({lost})")
        # 天地会 5 scan 3 (real readings): 合编, printed beside a two-line
        # name, engine A read with its first line and engine B after its
        # second; 裁決 answered 一 at one place and nothing at the other.
        # Characters both engines read a line apart are one hit; a page
        # that holds them at either engine's place loses nothing.
        a3 = "中国人民大学清史研究所 合 编\n中国第一历史档案馆\n中国人民大学出版社出版"
        b3 = "中国人民大学清史研究所\n中国第一历史档案馆\n合编\n中国人民大学出版社出版"
        lost = text_lost("中国人民大学清史研究所一中国第一历史档案馆\n\n中国人民大学出版社出版\n", a3, b3)
        check(lost == ["…史研究所[合编]中国第一…"],
              f"TEXT-LOST: characters both engines read a line apart that two answers left out are one hit ({lost})")
        # 天地会 2 scan 3 read it the other way round: engine B with the
        # first line, engine A after the second.
        lost = text_lost("中国人民大学清史研究所\n中国第一历史档案馆\n中国人民大学出版社出版\n", b3, a3)
        check(lost == ["…史档案馆[合编]中国人民…"],
              f"TEXT-LOST: so are they where engine B read them first ({lost})")
        held = [text_lost(f"中国人民大学清史研究所{x}\n中国第一历史档案馆{y}\n中国人民大学出版社出版\n", a3, b3)
                for x, y in (("合编", ""), ("", "合编"))]
        check(held == [[], []], f"TEXT-LOST: nothing lost where the page holds them at engine A's place or at "
                                f"engine B's ({held})")
        # 天地会 3 scan 38 (real readings and text): a name grid engine B
        # read a row late, its 音 where engine A read 张; 裁決 answered 黄
        # where engine A alone read 音, and 张 at the next place.
        lost = text_lost("以上七十一犯俱正月二十九日正法。\n郑黄张鹤邓钵黄载  \n张合林德郑印林开张  \n黄才陈足李佃\n",
                         "以上七十一犯俱正月二十九日正法。\n郑音 张鹤 邓钵 黄载\n张合 张德 郑印 林开张\n黄才 陈足 李佃",
                         "以上七十一犯俱正月二十九日正法。\n郑 张 鹤 邓 钵 黄 载\n音 合 德 郑 印 林开\n黄 才 陈 足 李 佃")
        check(lost == ["…日正法郑[音]张鹤邓钵…"],
              f"TEXT-LOST: so is one read a row apart where the other engine read another character ({lost})")
        # A character both engines read in one script, written in the
        # other, is lost: in a book printed in traditional script no other
        # counter sees 后 written for 後.
        lost = text_lost("皇后后來到此地。\n", "皇后後來到此地。")
        check(lost == ["…皇后[後]來到此地…"],
              f"TEXT-LOST: a character both engines read, written in the other script, is lost ({lost})")
        # The fold: variants first (麼 and 麽 are one character, named
        # once), then a traditional character's first simplified form (開
        # as 开, not 𫔭).  Engine B's reading of a run is what it read
        # aligned with it: not its stand-in 丶 inside the run (as on
        # 陳總司令言論集 scan 205).
        lost = text_lost("無論是什來世的人\n", "無論是什麼來世的人", "無論是什麽來世的人")
        check(lost == ["…無論是什[麼]來世的人…"], f"TEXT-LOST: 麼 and 麽 are one character, named once ({lost})")
        lost = text_lost("大门了，\n", "大門開了，", "大门开了，")
        check(lost == ["…大門[A 開 / B 开]了…"], f"TEXT-LOST: 開 and 开 are one character ({lost})")
        lost = text_lost("誠不淺也。\n\n本區素有\n", "誠不淺也。\n五本區特殊問題\n一五邑華僑問題\n本區素有",
                         "誠不淺也丶五本區特殊問題一丶五邑華僑問題本區素有")
        check(lost == ["…誠不淺也[五本區特殊問題一五邑華僑問題]本區素有…"],
              f"TEXT-LOST: engine B's reading of a run is the characters it read aligned with it ({lost})")

        subset = ri.load_book(ri.InputPaths.resolve(tmp / "defects", {}), {12, 20})
        sub = bl.lint_book(subset, rm.learn(subset))
        check(pages_of(sub, "MARK-UNBALANCED") == [] and pages_of(sub, "HEAD-FRAGMENT") == [20],
              "on a subset of pages the page before and after are the book's: scan 12's bracket closes one "
              "opened on scan 11, and scan 20's heading ends scan 19's sentence")

        labels = {scan: ("start" if scan == 1 else "paragraph") for scan in range(1, 21)}
        labelled = lint(tmp / "labelled", fx.make_spec(), boundaries=labels)
        check(labelled["counters"]["JOIN-UNRESOLVED"]["count"] == 0, "JOIN-UNRESOLVED is 0 when every marker is labelled")

        headless = fx.make_spec()
        for page in headless.values():
            page["suffix"] = None
            page["folio"] = None
        fx.write_book(tmp / "headless", headless, profile={"notation": {"marks": ["。"]}})
        book = ri.load_book(ri.InputPaths.resolve(tmp / "headless", {}))
        entry = bl.lint_book(book, rm.learn(book))["counters"]["FURN-HEAD"]
        check(entry["count"] is None and "M-2" in entry["notMeasured"],
              f"a counter whose gate failed is not measured ({entry})")

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            failing = bl.main([str(tmp / "defects"), "--fail-on", "FURN-HEAD", "--json", str(tmp / "lint.json")])
            passing = bl.main([str(tmp / "defects"), "--fail-on", "AUDIT-FINAL"])
            misspelt = bl.main([str(tmp / "defects"), "--fail-on", "FURN_HEAD"])
        written = json.loads((tmp / "lint.json").read_text(encoding="utf-8"))
        check(failing == 1 and passing == 0 and written["counters"]["FURN-HEAD"]["count"] == 2
              and "AUDIT-FINAL: not measured" in errors.getvalue(),
              "the command line fails with --fail-on a counter that counts, and writes every hit; a counter "
              "not measured does not fail it and is named")
        check(misspelt == 2, f"--fail-on a name that is no counter fails the command line, not passes it "
                             f"(got {misspelt})")
    print(f"{'FAIL' if FAILURES else 'ok  '} check_book_lint: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
