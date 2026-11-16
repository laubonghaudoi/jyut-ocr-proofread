#!/usr/bin/env python3
"""Offline check of engine B's second look (scripts/run_hunyuanocr_second_look.py).

No GPU, no model: a made-up book of four pages, engine A's blocks with boxes,
engine B's sealed page pairs written by the runner's own functions, and a
stand-in for the OCR server that answers by the crop it is sent.

- A block engine B's page text lacks is read from its crop and put into the
  page after the line of the block before it; two unread blocks that follow one
  another are one crop; the page's TXT, JSON lines, seal and `secondLook`
  record agree, and the runner still takes the page for current.
- Left alone: a block engine B read (in other forms: a variant character); a
  furniture block; a block whose text stands on three pages (a running head);
  a reading that is not the blocks' (it is recorded, not written); a line of a
  reading that the page already holds.
- A second call does nothing; a changed engine A draft makes the page be done
  again from engine B's own lines; the report counts; exit codes.
- scripts/ocr_book.py, with stand-in runners: engine B, the early draft,
  engine A, then the second look on engine A's and engine B's folders;
  --single-engine leaves out the last two.
- SKILL.md runs the step as part of ocr_book.py, after engine A, inside the
  one hand-over call of the OCR phase (one run_ocr_phase.py per tool call),
  and builds the draft again after it, before the book profile;
  references/long-book-workflow.md's 〈OCR 階段（ocr_book.py）〉 gives its
  command, says why it follows engine A, and that one hand-over window
  holds the four steps.  (Q ran it as a hand-over call of its own, three
  calls in all; P's ocr_book.py runs the phase in one window: user
  decision 3, free the GPU, run both engines, restart the model.)

Usage:
    python3 tests/check_second_look.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import run_hunyuanocr as hy  # noqa: E402
import run_hunyuanocr_second_look as sl  # noqa: E402

failures: list[str] = []


def check(ok: bool, name: str) -> None:
    print(("ok   " if ok else "FAIL ") + name, flush=True)
    if not ok:
        failures.append(name)


BODY = "山中有古寺鐘聲遠近聞春風吹過小橋流水人家"
NOTE1 = "參見王某某著山居雜記卷三頁十二至十五上海出版"
NOTE2 = "又見李某某編田園小志第二冊頁四十北京刊行本"
HEAD = "青山文叢甲編卷之一"


def block(text: str, y: float, label: str = "text", height: float = 0.05) -> dict:
    return {"text": text, "boundingBox": [0.1, y, 0.8, height], "blockLabel": label}


def write_page(root: Path, scan: int, a_blocks: list[dict], b_text: str) -> None:
    from PIL import Image
    stem = f"page-{scan:04d}"
    render = root / "renders" / f"{stem}.png"
    Image.new("RGB", (400, 600), "white").save(render)
    (root / "ocr-a" / f"{stem}.json").write_text(json.dumps({"lines": a_blocks, "scanPage": scan}, ensure_ascii=False),
                                               encoding="utf-8")
    page = hy.PageInput(scan, render, hy.sha256_file(render), root / "ocr-b" / f"{stem}.json",
                        root / "ocr-b" / f"{stem}.txt")
    lines = hy.lines_from_text(b_text)
    text_bytes = hy.text_from_lines(lines)
    tool = hy.ToolIdentity(Path(hy.__file__), "m", "p", None, "stand-in-tool")
    raw = {"choices": [{"message": {"content": text_bytes.decode("utf-8")}, "finish_reason": "stop"}]}
    record = hy.build_record(page, lines, text_bytes, tool, dict(hy.OCR_PROFILE), raw)
    page.json_output.write_bytes(hy.canonical_json_bytes(record, pretty=True))
    page.text_output.write_bytes(text_bytes)


class StandIn:
    """Answers by the height of the crop it is sent (which blocks it shows)."""

    def __init__(self, answers: dict[int, str]) -> None:
        self.answers = answers
        self.calls: list[tuple[int, int]] = []

    def ocr(self, path: Path, max_tokens: int, timeout: float) -> str:
        from PIL import Image
        with Image.open(path) as image:
            size = image.size
        self.calls.append(size)
        # Heights: one block 0.05 of 600 = 30 px (+ margin); two blocks more.
        key = min(self.answers, key=lambda h: abs(h - size[1]))
        return self.answers[key]

    def close(self) -> None:
        pass


def run(root: Path, server: StandIn, *extra: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return sl.main([str(root / "renders"), str(root / "ocr-a"), str(root / "ocr-b"),
                        "--report", str(root / "second-look.json"), *extra], server=server)


STAND_IN_RUNNER = """import os, sys
from pathlib import Path
with open(os.environ["STAND_IN_CALLS"], "a", encoding="utf-8") as calls:
    calls.write(" ".join([Path(sys.argv[0]).name] + [Path(a).name for a in sys.argv[1:] if not a.startswith("-")])
                + "\\n")
"""


def phase_order() -> None:
    """ocr_book.py's own order, run on a copy of it beside stand-in runners
    that only record their call: engine B, the draft, engine A, then the
    second look, which reads engine A's blocks and rewrites engine B's pages."""
    import os
    import shutil
    import subprocess
    with tempfile.TemporaryDirectory(prefix="check-phase-order-") as tmp:
        root = Path(tmp)
        scripts = root / "scripts"
        scripts.mkdir()
        for name in ("ocr_book.py", "_phase_budget.py", "_gpu_queue.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        for name in ("run_hunyuanocr.py", "build_draft.py", "run_paddleocr_vl.py", "run_hunyuanocr_second_look.py"):
            (scripts / name).write_text(STAND_IN_RUNNER, encoding="utf-8")
        book = root / "book"
        (book / "renders").mkdir(parents=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith("OCR_PHASE_")}
        said = {}
        for extra in ((), ("--single-engine",)):
            calls = root / f"calls{len(extra)}.txt"
            calls.write_text("", encoding="utf-8")
            done = subprocess.run([sys.executable, str(scripts / "ocr_book.py"), str(book), *extra],
                                  env=dict(env, STAND_IN_CALLS=str(calls)), capture_output=True, text=True,
                                  timeout=120)
            said[extra] = (done.returncode, calls.read_text(encoding="utf-8").splitlines())
            (book / "draft.md").unlink(missing_ok=True)
    # Each runner: its positional folders, then its --report's name.
    check(said[()] == (0, ["run_hunyuanocr.py renders ocr-hunyuan hunyuanocr-run.json", "build_draft.py book",
                           "run_paddleocr_vl.py renders ocr-paddle paddleocr-run.json",
                           "run_hunyuanocr_second_look.py renders ocr-paddle ocr-hunyuan second-look-run.json"]),
          f"ocr_book.py: engine B, the draft, engine A, then the second look on both drafts ({said[()]})")
    check(said[("--single-engine",)] == (0, ["run_hunyuanocr.py renders ocr-hunyuan hunyuanocr-run.json",
                                             "build_draft.py book"]),
          f"ocr_book.py --single-engine: engine B and the draft only ({said[('--single-engine',)]})")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("renders", "ocr-a", "ocr-b"):
            (root / name).mkdir()
        # Page 1: body read by both; two footnotes engine B left out.
        write_page(root, 1, [block(HEAD, 0.02, "text"), block(BODY, 0.10), block(NOTE1, 0.80, "footnote"),
                             block(NOTE2, 0.86, "footnote"), block("一", 0.95, "number")], BODY)
        # Page 2: a title engine B read with a variant character; nothing to do.
        write_page(root, 2, [block(HEAD, 0.02, "text"), block("小结", 0.10, "paragraph_title"), block(BODY, 0.20)],
                   "小結\n" + BODY)
        # Page 3: a caption engine B left out, whose reading comes back as something else.
        write_page(root, 3, [block(HEAD, 0.02, "text"), block(BODY, 0.10), block("圖一春日遊記全圖說明文字", 0.50, "figure_title", 0.2)],
                   BODY)
        # Page 4: only the running head and the body.
        write_page(root, 4, [block(HEAD, 0.02, "text"), block(BODY, 0.10)], BODY)

        server = StandIn({66: f"① {NOTE1}，1996，頁12-15。\n② {NOTE2}。", 36: "小結", 126: "图(18,53),(983,738)"})
        status = run(root, server)
        report = json.loads((root / "second-look.json").read_text(encoding="utf-8"))
        check(status == 0 and report["counts"]["completed"] == 4 and report["counts"]["cropsRead"] == 1,
              f"the run exits 0, every page is checked, one crop is kept (exit {status}, {report['counts']})")
        check(len(server.calls) == 3,
              f"three crops are asked for: the two footnotes as one, the variant title, the caption ({server.calls})")

        text1 = (root / "ocr-b" / "page-0001.txt").read_text(encoding="utf-8")
        record1 = json.loads((root / "ocr-b" / "page-0001.json").read_text(encoding="utf-8"))
        check(text1 == f"{BODY}\n① {NOTE1}，1996，頁12-15。\n② {NOTE2}。\n",
              "the footnotes' reading goes into engine B's page after the body, figures and all")
        check([line["blockLabel"] for line in record1["lines"]] == ["whole-page", "second-look", "second-look"]
              and record1["secondLook"]["groups"][0]["blocks"] == [2, 3],
              "the JSON names the lines read this way and the blocks they are for")
        page = hy.PageInput(1, root / "renders" / "page-0001.png", hy.sha256_file(root / "renders" / "page-0001.png"),
                            root / "ocr-b" / "page-0001.json", root / "ocr-b" / "page-0001.txt")
        current, reason = hy.completion_is_current(page, hy.ToolIdentity(Path(hy.__file__), "m", "p", None, "stand-in-tool"),
                                                   dict(hy.OCR_PROFILE))
        check(current, f"the rewritten page is still the runner's sealed pair ({reason})")
        check(HEAD not in text1 and HEAD not in (root / "ocr-b" / "page-0004.txt").read_text(encoding="utf-8"),
              "a block that stands on three pages (the running head) is not asked for")
        check((root / "ocr-b" / "page-0002.txt").read_text(encoding="utf-8") == "小結\n" + BODY + "\n",
              "a title engine B read in another form is not written a second time")
        record3 = json.loads((root / "ocr-b" / "page-0003.json").read_text(encoding="utf-8"))
        check((root / "ocr-b" / "page-0003.txt").read_text(encoding="utf-8") == BODY + "\n"
              and "notKept" in record3["secondLook"]["groups"][0],
              "a reading that is not the block's is recorded and not written")

        again = StandIn({})
        status = run(root, again)
        report = json.loads((root / "second-look.json").read_text(encoding="utf-8"))
        check(status == 0 and not again.calls and report["counts"]["skippedCurrent"] == 4,
              "a second call asks for nothing: every page was checked on this engine A draft")

        # Engine A's draft of page 1 changes: the page is done again from engine B's own lines.
        write_a = json.loads((root / "ocr-a" / "page-0001.json").read_text(encoding="utf-8"))
        write_a["lines"][3]["text"] = NOTE2 + "補"
        (root / "ocr-a" / "page-0001.json").write_text(json.dumps(write_a, ensure_ascii=False), encoding="utf-8")
        server = StandIn({66: f"① {NOTE1}。\n② {NOTE2}補。"})
        status = run(root, server, "--pages", "1")
        text1 = (root / "ocr-b" / "page-0001.txt").read_text(encoding="utf-8")
        check(status == 0 and text1 == f"{BODY}\n① {NOTE1}。\n② {NOTE2}補。\n" and len(server.calls) == 1,
              "a changed engine A draft: the page is read again, the earlier reading replaced, not doubled")

        kept, why = sl.kept_lines("甲" * 900, NOTE1, BODY)
        check(not kept and "characters" in (why or ""), "a reading many times longer than its blocks (a loop) is not kept")
        kept, why = sl.kept_lines(f"{BODY}\n{NOTE1}", NOTE1, BODY)
        check(kept == [NOTE1], "a line of a reading that the page already holds is left out, the rest kept")
        blocks = [block(BODY, 0.1), block(NOTE1, 0.5, "footnote"), block(BODY[::-1], 0.6), block(NOTE2, 0.7, "footnote")]
        groups = sl.unread_groups(blocks, BODY + BODY[::-1])
        check([g["blocks"] for g in groups] == [[1], [3]], "unread blocks with a read block between them are two crops")

        (root / "ocr-b" / "page-0004.txt").write_text("改咗\n", encoding="utf-8")
        status = run(root, StandIn({}), "--pages", "4")
        check(status == 1, f"a page whose TXT does not match its JSON is refused (exit {status})")

    phase_order()
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    sheet = skill.split("\n## 四、", 1)[-1].split("\n## ", 1)[0] if "\n## 四、" in skill else ""
    handover = skill.split("\n## 五、", 1)[-1].split("\n## ", 1)[0] if "\n## 五、" in skill else ""
    ocr = sheet[sheet.find("2. **OCR**"):sheet.find("3. **Book profile**")]
    phase = ('"$JYUT_OCR_PYTHON" scripts/run_ocr_phase.py -- "$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK '
             '> BOOK/ocr.log 2>&1')
    order = [ocr.find(step) for step in ("引擎 B（HunyuanOCR）", "草稿 `BOOK/draft.md`", "引擎 A（PaddleOCR-VL）",
                                         "引擎 B 補讀")]
    check(phase in ocr and -1 not in order and order == sorted(order)
          and "一個 tool call 淨係放一個" in handover and "GPU 交接一定要喺前景行" in handover,
          "SKILL.md runs the second look after engine A, in the OCR phase's one hand-over call")
    again = '"$JYUT_OCR_PYTHON" scripts/build_draft.py BOOK'
    check(0 <= ocr.find(phase) < ocr.find(again) and "（補讀改咗引擎 B 嘅頁）" in ocr
          and 0 <= sheet.find(again) < sheet.find('"$JYUT_OCR_PYTHON" scripts/book_profile.py'),
          "SKILL.md builds the draft again after it, before the book profile")
    section = (reference.split("\n## OCR 階段（ocr_book.py）", 1)[-1].split("\n## ", 1)[0]
               if "\n## OCR 階段（ocr_book.py）" in reference else "")
    steps = [section.find(command) for command in (
        "`run_hunyuanocr.py BOOK/renders BOOK/ocr-hunyuan`", "`build_draft.py BOOK`",
        "`run_paddleocr_vl.py BOOK/renders BOOK/ocr-paddle`",
        "`run_hunyuanocr_second_look.py BOOK/renders BOOK/ocr-paddle BOOK/ocr-hunyuan`")]
    check(-1 not in steps and steps == sorted(steps) and "要用引擎 A 嘅版面 block" in section
          and "所以一定喺引擎 A 之後" in section and "一個交接窗口包晒四步" in section,
          "the reference says why it runs after engine A, in the one hand-over window of the OCR phase")

    print("\nFAILURES:" if failures else "\nall checks passed")
    for failure in failures:
        print(" -", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
