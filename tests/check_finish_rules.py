#!/usr/bin/env python3
"""Offline check of what the skill's text tells the agent to do once phase 3
has sealed the book: run scripts/finish_book.py, deliver final.md and
doubts.md, and never review pages itself.

Measured (Qwen Code end to end, 30 pages, the old skill and the new): both
sealed all 30 pages in about 59 minutes; then the agent cut crops, read them
with zoom_image and read_file into its own conversation and adjudicated
glyphs itself, for one hour (new) or two (old), until Qwen Code stopped with
"Context is too large to send safely after automatic compression ... hard
limit: 177000 ... COMPRESSION_FAILED_OUTPUT_TRUNCATED" (exit 1), no book
assembled.  The transcripts show the wording that sent it there: the rule on
claiming a page-by-page visual check (read as a duty to do one), the
low-confidence check "by context and image", "your eyes are for the dispute
sites and research items", the research queue read from the run report, the
basic profile's page-by-page image check, the core flow's page-by-page pass,
"only the main agent assembles", and doubts that "need the image checked".

- SKILL.md (a run sheet since 2026-10-05) has the finishing step
  (finish_book.py in the background, waited on with wait_for_run.py, then
  clean_book.py --all), its steps in the pipeline's order (the OCR phase
  one ocr_book.py command), and the two rule sets of user decision 3: for
  rule set B (the local model proofreads, 一) the rule that forbids the
  agent's own review (page images and crops, zoom_image, read_file on
  images, sealed pages and run reports, adjudicating, mending by hand, no
  subagent doing it), the crash that says why; for rule set A (the agent
  answers packets) no review past the bundles' questions; for both, that
  'visually checked page by page' must not be claimed, and a
  short delivery naming X.md and X-doubts.md, never staged or committed
  (the PDF's folder may be the person's git repository).  After a fix the
  person asks for, the final audit is run on X.md, never finish_book.py
  again.  A reference is read one section at a time, to the next heading of
  its level or higher.  The measured crash in full is
  long-book-workflow.md's 〈agent 規矩嘅實測根據〉.
- The wording that drove the review is gone from SKILL.md and references/,
  and the references say their image checks are the pipeline's or the
  user's.  Three of the phrases stay in the manual route alone - the
  segment workflow of structured-workflow.md (no OCR engine, or a
  transcription the person asks to be done by hand; rule set B never uses
  it) and the page-by-page final pass of jyut-ocr-detailed-rules.md, which
  is the manual route's or the person's - each under the line that says
  so; nowhere else.  jyut-ocr-detailed-rules.md's image checks on blank
  pages, cells both engines missed and the last pages (render, zoom,
  inverted crops) are one line scoped to the manual route or the person's
  review, which says the pipeline's agent looks only where a question asks
  (and at SKILL.md 四.1's blank candidates); the unscoped lines are gone
  (the phase-E review's E-R6: they contradicted SKILL.md 四.1).  local-ocr.md's 〈輸出、續跑同核圖〉, where SKILL.md
  sends both rule sets when a draft is blocked or cut off, says rule set
  B's agent reads no page image there: a page whose engine B reply was cut
  off is merged up to engine B's loop by proofread_pages.py (the
  engine-b-truncated doubt), a failed page is handled from the run report's
  reason (rule set B reads the RESULT line and tells the person), and
  neither is mended from the page image.
- Every switch of finish_book.py is named in SKILL.md, and SKILL.md names
  its exit codes, all of them and no other; wait_for_run.py knows the step.
- The rules on faithful transcription are still there.
- What the review found (each check failed before its fix): the wait rule
  still left "zoom into page images to study the book" for after a step;
  nothing kept a subagent from doing the review, and the other sections'
  image checks (subagent visual audit, heading sizes read off the PDF, the
  contents' 100% image check) were not said to be the pipeline's or the
  user's; a short book could run finish_book.py in the foreground (a
  harness time limit kills it); exit 1 left the agent to mend the book
  itself; the edited final.md kept aside (and so no rerun just to check an
  edited final.md), the refused second run and the WARNING lines in
  finish-book.log were not in the text.

Usage:
    python3 tests/check_finish_rules.py
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def section(text: str, heading: str) -> str:
    """The body of the `## HEADING...` section of TEXT."""
    start = text.find(f"\n## {heading}")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 4)
    return text[start:end if end >= 0 else len(text)]


# Wording the e2e transcripts show sending the agent to review pages itself
# (quoted in the agent's own reasoning, or the step it took right after).
OLD_WORDING = {
    "未逐頁視覺對照，就唔可以話": "the claim rule, read as a duty to check every page",
    "聲稱逐頁視覺完成時": "what a page-by-page visual claim needs",
    "要按語境同圖像檢查": "the low-confidence check by context and image",
    "你嘅眼留返畀": "the agent's eyes for dispute sites and research items",
    "跑完之後睇 run report 嘅 `researchQueue`": "reading the research queue from the run report",
    "唔會降低逐頁視覺校對標準": "the profile that never lowers the page-by-page visual standard",
    "逐頁圖文核對": "the basic profile's page-by-page image check",
    "完成逐頁視覺對照": "the core flow's page-by-page visual pass",
    "每次視覺確認一個無括號錯字後": "the agent confirming glyphs by sight",
    "只由主 agent 組裝最終 Markdown": "the main agent assembling the book",
    "主 agent 要 review 每段": "the main agent reviewing every segment",
    "是否逐頁用 individual original": "the delivery reporting a page-by-page visual check",
    "自己組裝全書時": "the agent assembling the book itself",
    "例如放大頁圖研究本書、寫筆記；要做就等呢步做完先做": "zooming into page images left for after a step",
    "幾十頁嘅短書可以前景跑": "the finishing step in the foreground, where a time limit kills it",
}
OLD_IN_REFERENCES = {
    "只由主 agent 或唯一 assembly owner 合併 segments": "the main agent merging the segments",
    "doubt 係交人核圖嘅問題，runner": "doubts as image checks for whoever reads them",
    "逐頁重看 assembled Markdown 同 PDF": "the page-by-page final pass as the agent's step",
    "所有可能改文嘅獨立 visual sweep": "the visual sweep before finalize",
    "Only the main agent assembles the final Markdown": "the main agent assembling and running the visual closure",
    "按原圖補返截斷部分": "a page cut off in engine B's reply mended from the page image",
    "失敗頁仍要按原圖處理": "a failed page handled from the page image",
    # wait_for_run.py's line names the failed pages and why (E-R5): rule set B
    # reads it alone, never the run report or the log's end.
    "讀嗰行、run report 同 log 尾": "every agent told to read the run report and the log's end on exit 1",
    # detailed-rules' image checks past the bundles' questions (E-R6): they
    # are the manual route's and the person's, under the line that says so.
    "兩引擎同漏亦要由原圖補核": "a cell both engines missed checked from the image by whoever reads it",
    "OCR 判空白嘅頁仍要 render 放大": "every page OCR called blank rendered and zoomed by whoever reads it",
    "疑似家具同正文黐住時，先 render 對位": "a render to place furniture by whoever reads it",
}
# Where some of them stay: the manual route (P's structured-workflow.md, the
# segment workflow for a host with no OCR engine or a transcription by hand,
# and detailed-rules' final pass, manual or the person's), each only under
# the words that scope it so.  file -> (section heading or "" for the whole
# file, the scoping words, the phrases allowed there).
MANUAL_ROUTE = {
    "structured-workflow.md": ("", ("B 套（本機模型做 agent）唔用呢份", "唔係腳本流程嘅步驟"),
                               ("只由主 agent 或唯一 assembly owner 合併 segments", "所有可能改文嘅獨立 visual sweep")),
    "jyut-ocr-detailed-rules.md": ("## 逐頁 final pass", ("人手轉錄（`structured-workflow.md`）或者用戶覆核嗰陣",
                                                         "腳本流程嘅 agent 唔做呢一步"),
                                   ("逐頁重看 assembled Markdown 同 PDF",)),
}


def skill_checks(skill: str, workflow: str) -> None:
    # The rules both rule sets keep (二); rule set B's ban is in 一, where
    # the proofreader is chosen (user decision 3: the agent proofreads by
    # packets by default; the local model over HTTP, its images never in
    # the agent's context).
    rules = section(skill, "二、規矩")
    sets = section(skill, "一、")
    rule = sets[sets.find("**B 套唔准自己覆核**"):]
    rule = rule[:rule.find("\n- ", 1)] if "\n- " in rule[1:] else rule
    run = section(skill, "四、點跑")
    packets = section(skill, "六、")
    exits = section(skill, "七、")
    talk = section(skill, "九、同用戶講嘢")
    py = '"$JYUT_OCR_PYTHON" scripts/'
    order = [run.find(py + command) for command in (
        "survey_pdf.py PDF BOOK", "ocr_book.py BOOK", "build_draft.py BOOK",
        "book_profile.py BOOK/renders BOOK/ocr-paddle BOOK/ocr-hunyuan BOOK/book-profile.json",
        "proofread_pages.py BOOK/renders BOOK/ocr-paddle BOOK/proofread --draft2-directory BOOK/ocr-hunyuan",
        "finish_book.py BOOK > BOOK/finish.log 2>&1", "clean_book.py BOOK --all")]
    wanted = {
        "the steps in the pipeline's order, survey to clean_book.py --all": -1 not in order and order == sorted(order),
        "finish_book.py in the background, always": py + "finish_book.py BOOK > BOOK/finish.log 2>&1" in run
        and "一律放背景" in run and "唔好前景跑" in run,
        "waited on with wait_for_run.py": "`wait_for_run.py BOOK BOOK/finish.log` 等" in run
        and "600000" in section(skill, "五、長步驟同 GPU 交接"),
        "X.md and X-doubts.md delivered": "`X.md`" in talk and "`X-doubts.md`" in talk,
        "rule set B: the forbidding rule": "**B 套唔准自己覆核**" in rule and "**唔准**" in rule,
        "rule set B: no page images or crops, zoom_image or read_file on images":
            all(w in rule for w in ("開頁圖或者裁圖", "`zoom_image`", "`read_file`")),
        "rule set B: no adjudicating, no mending by hand, scripts and RESULT lines only":
            "自己裁定字或者手改書" in rule and "淨係跑 script、讀佢哋最尾嘅 RESULT 行" in rule,
        "rule set B: no reading sealed pages or the run report to hunt errors": "sealed `.md`／`.json`" in rule
        and "run report" in rule,
        "rule set B: before, during and after": "由開工到交付之後" in rule,
        "rule set B: no subagent does it instead": "亦唔准叫 subagent 代你做" in rule,
        "rule set A: no review past the bundles' questions, no hand edits":
            "bundle 冇問嘅頁唔使自己覆核" in packets and "唔准手改 `answers.jsonl` 或者封存頁" in packets
            and "流程淨係喺佢問嘅位睇圖" in rules,
        "doubts.md's image checks are the user's": "要對住原書核" in talk and "係畀用戶做嘅" in workflow,
        "rule set B, why: every image stays in the context, and the crash": "每幅讀入對話嘅圖都留喺 context" in rule
        and "hard limit: 177000" in rule and "冇 `final.md`" in rule,
        "the measured crash quoted in the reference": all(w in workflow for w in (
            "hard limit: 177000", "COMPRESSION_FAILED_OUTPUT_TRUNCATED", "59 分鐘", "1 個鐘", "2 個鐘")),
        "no image review handed to anyone in the agent's text": "獨立核圖" not in skill and "visual audit" not in skill,
        "a short delivery": "**交付要短**" in talk and "唔好將 `final.md`、`doubts.md` 或者 log 成段讀入對話" in talk,
        "'visually checked page by page' must not be claimed":
            "**唔可以話「已逐頁視覺校對」「已逐頁同 PDF 對照」" in rules and "冇逐頁視覺核對" in talk,
        "the delivery reply says so too": "冇逐頁視覺核對（唔可以話有）" in talk,
        "the research queue goes to doubts.md, searched by text only": "〈要查資料嘅專名〉" in talk
        and "淨係用文字搜尋" in talk and "唔好放大頁圖睇" in talk,
        "the step 1 look: rule set A whole blank candidates only, rule set B none":
            "`blank_pages` 全頁一眼" in run and "B 套唔睇" in run,
        "exit 1: told to the user, not mended by hand": "照實話畀用戶知" in exits
        and "唔好自己拼書、改封存頁、切裁圖或者手寫 `final.md` 嚟補" in exits,
        "an edited final.md kept aside is passed on": "`final.edited-<時間>.md`" in talk and "有 `WARNING` 就照講" in talk,
        "one run per book; wait for the first": "第二個乜都唔寫就 exit 2" in exits,
        "the WARNING lines grepped in finish-book.log": "`grep -n WARNING BOOK/finish/finish-book.log`" in run,
        "the references read one section at a time, never whole": "唔好成份讀 `references/`" in rules
        and "淨係讀嗰節" in section(skill, "十、"),
        "a section read to the next heading of its level or higher": "下一個同級或者更高級嘅標題" in section(skill, "十、"),
        "no finish rerun to check an edit; the audit command instead": "唔好為咗核而再跑 `finish_book.py`" in talk
        and py + "audit_ocr_markdown.py X.md --mode final --summary-only" in talk,
        "the delivered files never staged or committed": "交付檔唔好 `git add` 或者 commit" in talk,
    }
    for name, ok in wanted.items():
        check(ok, f"SKILL.md: {name}")


def wording_checks(skill: str, references: dict[str, str]) -> None:
    for words, what in OLD_WORDING.items():
        where = [name for name, text in {"SKILL.md": skill, **references}.items() if words in text]
        check(not where, f"gone: {what} ({words!r}{' in ' + ', '.join(where) if where else ''})")
    for words, what in OLD_IN_REFERENCES.items():
        where = [name for name, text in references.items() if words in text and not manual_route(name, text, words)]
        check(not where, f"gone from references/ but the manual route: {what} "
                         f"({words!r}{' in ' + ', '.join(where) if where else ''})")
    for name, (heading, scope, _) in MANUAL_ROUTE.items():
        text = references.get(name, "")
        part = section(text, heading[3:]) if heading else text
        check(bool(part) and all(w in part for w in scope),
              f"references/{name}{' ' + heading[3:] if heading else ''}: scoped to the manual route or the person")
    tables = section(references.get("jyut-ocr-detailed-rules.md", ""), "表格、表單、圖同空白頁")
    scoped = next((line for line in tables.splitlines() if "render 放大" in line or "由原圖補核" in line), "")
    check(scoped.startswith("- **淨係人手轉錄（`structured-workflow.md`）或者用戶覆核嗰陣**")
          and all(w in scoped for w in ("兩個引擎都漏咗嘅格由原圖補核", "OCR 判空白嘅頁 render 放大", "反色圖同局部 crop",
                                        "腳本流程嘅 agent 唔做呢啲", "A 套淨係睇 packet 題目問嘅位", "`SKILL.md` 四.1",
                                        "B 套唔睇圖"))
          and sum("render 放大" in line or "由原圖補核" in line or "反色圖" in line for line in tables.splitlines()) == 1,
          "references/jyut-ocr-detailed-rules.md 〈表格、表單、圖同空白頁〉: its image checks past the questions are "
          "the manual route's or the person's, in one scoped line")
    for name in ("jyut-ocr-detailed-rules.md", "common-ocr-traps.md", "long-book-workflow.md"):
        check("agent 喺對話入面唔讀頁圖" in references.get(name, ""),
              f"references/{name}: its image checks are the pipeline's or the user's")
    outputs = section(references.get("local-ocr.md", ""), "輸出、續跑同核圖")
    check("B 套（本機模型校對）嘅 agent 喺對話入面唔讀頁圖、唔答 packet 題" in outputs
          and "淨係跑 script、讀 RESULT 行" in outputs,
          "references/local-ocr.md 〈輸出、續跑同核圖〉: rule set B reads no page image and answers no packet there")
    check("唔使人手補：`proofread_pages.py` 會將引擎 B 開始重複之前讀到嘅字當第二份讀法合併" in outputs
          and "`engine-b-truncated` 疑點" in outputs
          and "加大 `--max-tokens` 再加 `--overwrite`" in outputs,
          "references/local-ocr.md: a cut-off engine B draft is the pipeline's to merge up to its loop, or OCR again")
    check("失敗頁按 run report 寫嘅原因處理（B 套淨係睇 RESULT 行、話畀用戶知）" in outputs,
          "references/local-ocr.md: a failed page handled from the run report's reason, rule set B by its RESULT line")
    waits = section(references.get("local-ocr.md", ""), "等背景步驟跑完")
    exit1 = next((line for line in waits.splitlines() if line.startswith("- exit `1`")), "")
    check("嗰行就列出頁同 run report `pages` 寫嘅原因" in exit1 and "B 套淨係讀嗰行，將頁碼同原因話畀用戶知" in exit1
          and "唔開 report、唔讀 log" in exit1,
          "references/local-ocr.md 〈等背景步驟跑完〉 exit 1: the line names the failed pages and why; rule set B "
          "reads it alone")
    workflow = references.get("long-book-workflow.md", "")
    check("係畀用戶做嘅" in workflow and "`finish_book.py` 將佢哋按類同頁碼寫入 `doubts.md`" in workflow,
          "long-book-workflow.md: each doubt's image check is the user's, via doubts.md")
    check("## 一個 command 做完（finish_book.py）" in workflow, "long-book-workflow.md: the finish_book.py section")
    check("`final.edited-<時間>.md`" in workflow and "第二個喺寫任何嘢之前就 exit 2" in workflow
          and "即刻 exit 4" in workflow and "`.assembled.partial/`" in workflow,
          "long-book-workflow.md: edited copies kept, a refused run writes nothing, a stop at once, no input in "
          "assembled/")


def manual_route(name: str, text: str, words: str) -> bool:
    """Whether every occurrence of WORDS in reference NAME lies in its manual
    route part (MANUAL_ROUTE), under the words that scope it."""
    heading, scope, allowed = MANUAL_ROUTE.get(name, ("", (), ()))
    if words not in allowed:
        return False
    part = section(text, heading[3:]) if heading else text
    return all(w in part for w in scope) and text.count(words) == part.count(words)


def tool_checks(skill: str) -> None:
    import finish_book
    import wait_for_run

    source = (ROOT / "scripts" / "finish_book.py").read_text(encoding="utf-8")
    parser = finish_book.build_parser()
    switches = sorted({o for action in parser._actions for o in action.option_strings if o.startswith("--")}
                      - {"--help"})
    missing = [s for s in switches if f"`{s}`" not in skill]
    check(not missing and len(switches) >= 10, f"every finish_book.py switch named in SKILL.md ({missing or switches})")
    codes = {finish_book.EXIT_DONE, finish_book.EXIT_FAILED, finish_book.EXIT_USAGE, finish_book.EXIT_NOT_READY,
             finish_book.EXIT_STOPPED}
    finish = section(skill, "七、")
    named = {int(code) for code in re.findall(r"\*\*exit (\d+)\*\*", finish)}
    check(named == codes, f"SKILL.md's section on finish_book.py's exits names every exit code and no other "
                          f"({sorted(named)} vs {sorted(codes)})")
    docstring = finish_book.__doc__ or ""
    check(all(re.search(rf"^\s+{code}\s", docstring, re.M) for code in codes),
          "finish_book.py's docstring documents each exit code")
    check(wait_for_run.SCRIPTS.get("finish_book.py", ("",))[0] == "finish",
          "wait_for_run.py knows finish_book.py as a step it can wait on")
    check(not re.search(r"\b(?:zoom_image|read_file)\b", source) and "Image.open" not in source,
          "finish_book.py opens no image")


def faithful_checks(skill: str) -> None:
    kept = {
        "the PDF image is the final authority": "以 PDF 圖像為最終依據" in skill,
        "variant forms kept, no normalising, no archaising": "保留原有異體字、舊字形同舊式標點" in skill
        and "亦唔好過度古化" in skill,
        "an earlier transcription is only a draft": "既有轉錄" in skill and "唔係 ground truth" in skill,
        "no invented characters where the PDF is unclear": "唔好憑空補字" in skill,
        "the source's own notation kept": "唔可以當 residual 一律刪" in skill,
        "the page decides the variant form": "頁面印乜字形就寫乜字形" in skill,
    }
    for name, ok in kept.items():
        check(ok, f"still there: {name}")


def main() -> int:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    references = {path.name: path.read_text(encoding="utf-8") for path in sorted((ROOT / "references").glob("*.md"))}
    skill_checks(skill, references.get("long-book-workflow.md", ""))
    wording_checks(skill, references)
    tool_checks(skill)
    faithful_checks(skill)
    print(f"{'FAIL' if FAILURES else 'ok  '} the finishing step in the skill's text: {len(FAILURES)} problem(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
