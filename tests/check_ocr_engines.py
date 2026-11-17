#!/usr/bin/env python3
"""Offline checks of the OCR phase's speed work (2026-09-30): survey_pdf.py's sliced
render, the budget flag of run_ocr_phase.py, and the skill text that orders the phase.

No GPU, no model, no corpus.

- scripts/survey_pdf.py: a made-up PDF surveyed with --jobs 1 and with --jobs 4
  (and a scattered --pages selection with --jobs 3 against --jobs 1) gives
  byte-identical renders, and the same manifest and report but for their
  timestamps and paths.  Skipped, and said, where pdftoppm is not installed.
- scripts/run_ocr_phase.py: --max-seconds 0 is refused (exit 2, naming
  OCR_PHASE_UNLIMITED_SHELL) when the server has to be stopped, and accepted
  with --unlimited-shell.  On every host: the swap is forced, and nvidia-smi
  is a stand-in that knows no GPU; the hand-over's state and queues live in a
  temporary folder.
- SKILL.md (the run sheet), references/long-book-workflow.md, env.sh.example:
  the OCR phase is one ocr_book.py command, in the hand-over's foreground
  call when a local model owns the GPU (one run_ocr_phase.py per tool
  call), engine B first, the draft at once, engine A after, then engine B's
  second look, all in one hand-over window (user decision 3); the page
  images of step 1 by rule set: rule set B (the local model proofreads)
  opens none, every image staying in its context; rule set A glances at
  the blank candidates only, whole pages; the unlimited-shell rule and the
  single long wait.  The reference keeps the measurements behind them: the
  retry count, the step 1 look; local-ocr.md the memory the hand-over
  counts for each engine (run_ocr_phase.py's numbers), and no old 4.3 GB.
- scripts/run_hunyuanocr.py: OCR_PROFILE records the penalty the server
  applies by default (1.0, off), not the model card's 1.08, and every
  request sends exactly that value and no other penalty, so no draft is
  read with the card's penalty.

Usage:
    python3 tests/check_ocr_engines.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PY = sys.executable

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    shown = detail if (not ok or len(detail) < 120) else ""
    print(("ok   " if ok else "FAIL ") + name + (f" ({shown})" if shown else ""), flush=True)
    if not ok:
        failures.append(name)


# --- the sliced render -----------------------------------------------------------

def made_up_pdf(path: Path, pages: int = 9) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    images = []
    for index in range(pages):
        image = Image.new("RGB", (300, 420), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle([20 + 5 * index, 30, 120 + 9 * index, 60 + 12 * index], fill="black")
        for row in range(12):
            draw.line([(30, 100 + row * 22), (270 - 7 * ((row + index) % 5), 100 + row * 22)],
                      fill="black", width=3)
        draw.text((40, 380), f"page {index + 1}", fill="black")
        images.append(image)
    images[0].save(path, save_all=True, append_images=images[1:], resolution=72.0)
    return True


def survey(pdf: Path, out: Path, *extra: str) -> tuple[int, str]:
    done = subprocess.run([PY, str(SCRIPTS / "survey_pdf.py"), str(pdf), str(out), "--dpi", "72", *extra],
                          capture_output=True, text=True, timeout=300)
    return done.returncode, done.stdout + done.stderr


def without_stamps(path: Path, folder: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace(str(folder), "<out>")
    data = json.loads(text)
    data.pop("generated_at", None)
    if "manifest" in data and isinstance(data["manifest"], dict):
        data["manifest"].pop("sha256", None)  # the manifest's bytes carry its own timestamp
    return data


def check_survey() -> None:
    if not shutil.which("pdftoppm"):
        print("skip survey_pdf.py: pdftoppm is not installed", flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="survey-jobs-") as folder:
        root = Path(folder)
        pdf = root / "made-up.pdf"
        if not made_up_pdf(pdf):
            print("skip survey_pdf.py: PIL is not installed", flush=True)
            return
        code1, out1 = survey(pdf, root / "one", "--jobs", "1")
        code4, out4 = survey(pdf, root / "four", "--jobs", "4")
        check("survey --jobs 1 and --jobs 4 both exit 0", code1 == 0 and code4 == 0, f"{code1} {code4}\n{out1}\n{out4}")
        check("the rendering line says how many processes", "1 pdftoppm process(es) at a time" in out1
              and "4 pdftoppm process(es) at a time" in out4)
        renders1 = sorted((root / "one" / "renders").glob("page-*.png"))
        renders4 = sorted((root / "four" / "renders").glob("page-*.png"))
        check("nine renders each, named page-NNNN.png", [p.name for p in renders1] == [f"page-{n:04d}.png" for n in range(1, 10)]
              and [p.name for p in renders4] == [p.name for p in renders1])
        check("--jobs 4 renders are byte-identical to --jobs 1", all(
            a.read_bytes() == b.read_bytes() for a, b in zip(renders1, renders4)))
        check("the manifests are the same but for the timestamp",
              without_stamps(root / "one" / "page-manifest.json", root / "one")
              == without_stamps(root / "four" / "page-manifest.json", root / "four"))
        check("the reports are the same but for timestamps and paths",
              without_stamps(root / "one" / "survey-report.json", root / "one")
              == without_stamps(root / "four" / "survey-report.json", root / "four"))
        # A scattered selection: three runs, sliced across three processes.
        code_s1, _ = survey(pdf, root / "s1", "--jobs", "1", "--pages", "1,2,5-9")
        code_s3, out_s3 = survey(pdf, root / "s3", "--jobs", "3", "--pages", "1,2,5-9")
        renders_s1 = sorted((root / "s1" / "renders").glob("page-*.png"))
        renders_s3 = sorted((root / "s3" / "renders").glob("page-*.png"))
        check("a scattered --pages selection sliced over 3 processes: the same files, the same bytes",
              code_s1 == 0 and code_s3 == 0 and [p.name for p in renders_s1] == [p.name for p in renders_s3]
              == [f"page-{n:04d}.png" for n in (1, 2, 5, 6, 7, 8, 9)]
              and all(a.read_bytes() == b.read_bytes() for a, b in zip(renders_s1, renders_s3)), out_s3[-400:])
        check("the scattered manifests agree", without_stamps(root / "s1" / "page-manifest.json", root / "s1")
              == without_stamps(root / "s3" / "page-manifest.json", root / "s3"))
        check("the sliced render's pages are the ones the one-call render made",
              [p.read_bytes() for p in renders_s3] == [p.read_bytes() for p in renders1 if p.name in
                                                        {r.name for r in renders_s3}])
        code0, out0 = survey(pdf, root / "zero", "--jobs", "0")
        check("--jobs 0 is refused", code0 == 2 and "--jobs must be at least 1" in out0)


# --- the budget flag ----------------------------------------------------------------

def check_phase_budget() -> None:
    # The swap is decided on every platform now (OCR_PHASE_SWAP; --force-swap is
    # "always"), so this runs with or without a GPU, never touching a real one.
    scratch = Path(tempfile.mkdtemp(prefix="check-ocr-phase-"))
    fake = scratch / "bin" / "nvidia-smi"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'No devices were found'\nexit 6\n")
    fake.chmod(0o755)
    # Keep the no-GPU fixture independent of the Mac host's current RAM.
    # Otherwise the failed NVIDIA probe falls through to the real vm_stat.
    vm_stat = fake.with_name("vm_stat")
    vm_stat.write_text("#!/bin/sh\nexit 1\n")
    vm_stat.chmod(0o755)
    base = [PY, str(SCRIPTS / "run_ocr_phase.py"), "--force-swap", "--release-cmd", "true",
            "--restore-cmd", "true", "--max-seconds", "0"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OCR_PHASE_", "OCR_GPU_"))}
    env.update(PATH=f"{fake.parent}{os.pathsep}{env.get('PATH', '')}",
               OCR_PHASE_STATE_DIR=str(scratch / "state"), OCR_GPU_QUEUE_DIR=str(scratch / "queue"))
    try:
        check_phase_budget_runs(base, env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def check_phase_budget_runs(base: list[str], env: dict[str, str]) -> None:
    refused = subprocess.run(base + ["--", "true"], env=env, capture_output=True, text=True, timeout=120)
    check("--max-seconds 0 without --unlimited-shell is refused before the server is stopped",
          refused.returncode == 2 and "OCR_PHASE_UNLIMITED_SHELL" in refused.stderr
          and "release: true" not in refused.stdout, f"exit {refused.returncode}: {refused.stderr[-300:]}")
    allowed = subprocess.run(base + ["--unlimited-shell", "--", "true"], env=env, capture_output=True,
                             text=True, timeout=120)
    check("--max-seconds 0 with --unlimited-shell runs the whole runner", allowed.returncode == 0
          and "release: true" in allowed.stdout and "RESULT exit=0" in allowed.stdout,
          f"exit {allowed.returncode}: {(allowed.stdout + allowed.stderr)[-300:]}")
    env_flag = dict(env, OCR_PHASE_UNLIMITED_SHELL="1")
    by_env = subprocess.run(base + ["--", "true"], env=env_flag, capture_output=True, text=True, timeout=120)
    check("OCR_PHASE_UNLIMITED_SHELL=1 in the environment allows it too", by_env.returncode == 0,
          f"exit {by_env.returncode}")


# --- the text -----------------------------------------------------------------------

def section(text: str, heading: str) -> str:
    return text.split(heading, 1)[-1].split("\n## ", 1)[0] if heading in text else ""


def check_text() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    reference = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    example = (ROOT / "env.sh.example").read_text(encoding="utf-8")
    local = (ROOT / "references" / "local-ocr.md").read_text(encoding="utf-8")
    sets = section(skill, "## 一、")
    run = section(skill, "## 四、")
    long_steps = section(skill, "## 五、")
    resume = section(skill, "## 八、")
    seals = section(reference, "## 封條同續跑")
    engines = section(reference, "## OCR 階段（ocr_book.py）")
    survey = run[run.find("1. **Render**"):run.find("2. **OCR**")]
    ocr = run[run.find("2. **OCR**"):run.find("3. **Book profile**")]
    ban = sets[sets.find("**B 套唔准自己覆核**"):]
    order = [ocr.find(text) for text in ("引擎 B（HunyuanOCR）", "草稿 `BOOK/draft.md`", "引擎 A（PaddleOCR-VL）",
                                         "引擎 B 補讀")]
    steps = [engines.find(text) for text in (
        "`run_hunyuanocr.py BOOK/renders BOOK/ocr-hunyuan`", "`build_draft.py BOOK`",
        "`run_paddleocr_vl.py BOOK/renders BOOK/ocr-paddle`", "`run_hunyuanocr_second_look.py")]
    rules = {
        "the draft follows engine B, not both engines": "唔等引擎 A" in ocr,
        "no wait for both engines anywhere": "兩個 OCR 引擎都做完" not in skill and "兩個 runner 都做完" not in skill,
        "step 2: one ocr_book.py, through the hand-over when a local model owns the GPU":
            '"$JYUT_OCR_PYTHON" scripts/run_ocr_phase.py -- "$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK' in ocr
            and "`OCR_PHASE_RELEASE_CMD`" in ocr and '"$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK > BOOK/ocr.log' in ocr,
        "step 2: engine B, then the draft, then engine A, then engine B's second look, in that order":
            -1 not in order and order == sorted(order),
        "step 1: rule set B opens no page image": "B 套唔睇" in survey and "**唔准**開頁圖或者裁圖" in ban,
        "step 1: rule set A only the blank candidates, one whole-page look each":
            "`blank_pages` 全頁一眼" in survey and "真係空白先" in survey,
        "rule set B: every image read stays in the context, the crash measured":
            "每幅讀入對話嘅圖都留喺 context" in ban and "hard limit: 177000" in ban,
        "step 1: why the cap, measured (reference)": "40 幅頁圖" in reference and "13 分鐘" in reference,
        "step 1: survey_pdf.py takes BOOK, not BOOK/renders":
            '"$JYUT_OCR_PYTHON" scripts/survey_pdf.py PDF BOOK' in survey and "唔好畀佢 `BOOK/renders`" in survey,
        "the survey renders on every core (reference)": "`--jobs`" in engines,
        "hand-over: one run_ocr_phase.py per call, in the foreground":
            "一個 tool call 淨係放一個" in long_steps and "GPU 交接一定要喺前景行" in long_steps
            and "`600000`" in long_steps,
        "hand-over: the old order (engine A first) is gone": "先叫 `run_paddleocr_vl.py` 叫到 exit 0" not in skill,
        "hand-over: the retry count is the measured one (reference)": "重試 7 次" in reference
                                                                     and "重試兩次" not in skill + reference,
        "hand-over: the unlimited-shell rule, timeout omitted": "OCR_PHASE_UNLIMITED_SHELL=1" in long_steps
                                                               and "唔好設 `timeout`" in long_steps,
        "hand-over: a call killed anyway means the setting did not take": "即係設定冇生效" in long_steps,
        # A book resumed at step 2 on a hand-over machine runs the same
        # hand-over call; its sealed OCR pages are skipped (E-R8).
        "resume: step 2 through the same hand-over call, sealed OCR pages skipped":
            all(w in resume for w in ("→ 第 2 步（照四.2 揀", "`OCR_PHASE_RELEASE_CMD`", "經五嘅交接前景跑同一條 `run_ocr_phase.py` command",
                                      "唔好直接跑 `ocr_book.py`）。每個 script 都跳過已經封好嘅頁"))
            and all(w in seals for w in ("**續跑 OCR**", "經 `run_ocr_phase.py` 交接", "跳過封好嘅頁",
                                         "最尾一行已經係 `RESULT exit=0`")),
        # The flag is the person's, set in env.sh once the cap is gone and
        # tested; the agent never sets it, and keeps its timeouts without it
        # (the phase-E review's E-R7).
        "hand-over: the unlimited-shell flag is the user's, in env.sh; timeouts kept without it":
            "淨係用戶喺 `env.sh` 設" in long_steps and "你唔好自己設或者 export" in long_steps
            and "冇呢個設定：tool call timeout 照開到最大" in long_steps
            and "`env.sh` 有 `OCR_PHASE_UNLIMITED_SHELL=1`" in long_steps
            and "淨係用戶喺 `env.sh` 設" in local and "Agent 唔好自己設" in local
            and "tool call timeout 照開到最大" in local,
        "hand-over: --max-seconds 0 needs the flag": "要 `OCR_PHASE_UNLIMITED_SHELL=1` 講明" in long_steps,
        "wait: one wait for the whole step where the shell has no cap": "`--timeout 43200`" in long_steps,
        "wait: why no agent turn during a long step, measured (reference)": "八分一" in reference,
        "wait: each call carries a different description (the harness's loop guard)":
            "description 寫唔同嘅字" in long_steps and "連續 5 個一模一樣嘅 tool call" in long_steps,
        "reference: one hand-over window for the whole phase": "一個交接窗口包晒四步" in engines
                                                               and "模型每次 call 停一次、開一次" in engines,
        # Q's "約 5.0 GB VRAM" for engine B was measured on Q's runtime; P's
        # local-ocr.md gives what the hand-over counts, the code's numbers.
        "reference: engine B's memory as the hand-over counts it, no old 4.3 GB":
            "4.3 GB" not in skill + reference and all(w in local for w in (
                "Paddle 約 12 GiB", "每 token 約 96 KiB", "再加約 3.4 GiB")) and memory_numbers(),
        "reference: the phase runs engine B, the draft, engine A, then the second look":
            -1 not in steps and steps == sorted(steps),
        "env.sh.example: the unlimited-shell flag, off": "# export OCR_PHASE_UNLIMITED_SHELL=1" in example,
        "env.sh.example: says what the flag means": "no time limit" in example,
    }
    for name, ok in rules.items():
        check(f"text: {name}", ok)
    # The profile every draft seals says what the requests send.
    sys.path.insert(0, str(SCRIPTS))
    import run_hunyuanocr  # noqa: E402
    check("engine B's profile records the penalty the server applies (llama.cpp default 1.0, off)",
          run_hunyuanocr.OCR_PROFILE["repetitionPenalty"] == 1.0)
    check("every request sends the profile's penalty, 1.0, and no other penalty", request_penalties(run_hunyuanocr)
          == {"repeat_penalty": 1.0}, str(request_penalties(run_hunyuanocr)))


def memory_numbers() -> bool:
    """local-ocr.md's memory figures are run_ocr_phase.py's: Paddle 12 GiB,
    Hunyuan 96 KiB a token of KV cache plus about 3.4 GiB."""
    sys.path.insert(0, str(SCRIPTS))
    import run_ocr_phase  # noqa: E402
    return (run_ocr_phase.PADDLE_MIB == 12 * 1024 and run_ocr_phase.HUNYUAN_KV_BYTES_PER_TOKEN == 96 * 1024
            and round(run_ocr_phase.HUNYUAN_BASE_MIB / 1024, 1) == 3.4)


def request_penalties(run_hunyuanocr) -> dict:
    """The penalties one request of engine B's runner sends (its body's keys
    that name a penalty), read off the request itself: HunyuanServer.ocr on
    a page, with urlopen answering in place of a server."""
    import io
    import urllib.request

    sent: list[dict] = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data.decode("utf-8")))
        return Reply(json.dumps({"model": "hunyuanocr", "choices": [
            {"message": {"content": "字"}, "finish_reason": "stop"}]}).encode("utf-8"))

    server = object.__new__(run_hunyuanocr.HunyuanServer)
    server.process, server.model_id, server.base = None, "hunyuanocr", "http://127.0.0.1:9"
    with tempfile.TemporaryDirectory(prefix="check-penalty-") as folder:
        page = Path(folder) / "page-0001.png"
        page.write_bytes(b"not an image: only its bytes are sent")
        original = urllib.request.urlopen
        urllib.request.urlopen = urlopen
        try:
            server.ocr(page, 64, 5.0)
        finally:
            urllib.request.urlopen = original
    return {key: value for body in sent for key, value in body.items() if "penalty" in key}


def main() -> int:
    check_survey()
    check_phase_budget()
    check_text()
    if failures:
        print(f"FAIL check_ocr_engines: {len(failures)} failure(s)")
        return 1
    print("ok   check_ocr_engines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
