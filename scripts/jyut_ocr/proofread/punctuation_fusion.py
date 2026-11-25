"""Transport-independent punctuation fusion; historical packet prompt bytes stay stable.

Only protocol shape is checked here. The caller retains its image census ceiling,
character, punctuation, doubled-circle, leader and realignment validators.
"""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Mapping

FUSED_DELIMITER = "---PUNCTUATED---"


def normalize_fused_answer(raw: Any) -> str:
    """Canonicalize a `census+punctuate` answer into `census\\n---PUNCTUATED---\\npunctuated`."""
    if isinstance(raw, Mapping):
        census_part = str(raw.get("census") or "").strip()
        punct_part = str(raw.get("punctuated") or "").strip(" \t\r\n")
        return f"{census_part}\n{FUSED_DELIMITER}\n{punct_part}"
    text = str(raw or "").strip(" \t\r\n")
    if text.startswith("{") and text.endswith("}"):
        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(text)
            if isinstance(parsed, Mapping) and ("census" in parsed or "punctuated" in parsed):
                census_part = str(parsed.get("census") or "").strip()
                punct_part = str(parsed.get("punctuated") or "").strip(" \t\r\n")
                return f"{census_part}\n{FUSED_DELIMITER}\n{punct_part}"
    return text


def split_fused_answer(text: str, fallback_span: str) -> tuple[str, str]:
    """Split a canonicalized `census+punctuate` answer into `(census_text, punctuated_text)`."""
    normalized = normalize_fused_answer(text)
    if FUSED_DELIMITER in normalized:
        left, _, right = normalized.partition(FUSED_DELIMITER)
        punct = right.strip(" \t\r\n")
        if punct in ("無標點", "無", "(無標點)"):
            punct = fallback_span
        return left.strip(), punct
    # Graceful fallback if the answer omitted the delimiter after reporting 符號：0
    lines = normalized.splitlines()
    census_lines: list[str] = []
    body_lines: list[str] = []
    in_header = True
    for line in lines:
        if in_header and re.match(r"^\s*(?:符號|雙圈|形狀)[：:]", line):
            census_lines.append(line)
        elif in_header and not line.strip() and census_lines:
            continue
        else:
            in_header = False
            body_lines.append(line)
    census_text = "\n".join(census_lines).strip()
    punct_text = "\n".join(body_lines).strip(" \t\r\n")
    if punct_text in ("", "無標點", "無", "(無標點)"):
        punct_text = fallback_span
    return census_text, punct_text


def fused_prompts(*, span: str, hint: str, leaders: int | None, show_doubled: bool,
                  census_system: str, punct_system: str) -> tuple[str, str]:
    """The original packet question, byte for byte, shared by both transports."""
    combined_system = (
        "你同時負責「標點點算」同「逐欄加標點」兩步，對住同一張版面裁圖一次過完成。\n\n"
        "【第一步：點算標點】\n"
        f"{census_system}\n\n"
        "【第二步：喺已定嘅字入面加返標點】\n"
        f"{punct_system}\n\n"
        "【輸出格式（嚴格遵守，唔好加 code fence 或解釋）】\n"
        "符號：<印咗符號嘅位置數；雙圈算一處>\n"
        "雙圈：<其中幾多處係雙圈；冇就寫 0>\n"
        "形狀：<出現過嘅符號種類，冇就寫 無>\n"
        f"{FUSED_DELIMITER}\n"
        "<如果符號係 0，原封不動照抄上面已定嘅字；如果符號 > 0，輸出加返標點嘅同一段字（一行，唔准改任何漢字）>"
    )
    leader_note = (
        f"裁圖上量到 {leaders} 串點（導引點線或者刪節號），每串計一處，喺正文寫「……」。\n"
        if leaders
        else ""
    )
    doubled_note = (
        "雙圈（兩個圈連住印）喺點算計一處並報喺「雙圈：」，喺正文寫兩個圈「。。」。\n"
        if show_doubled
        else ""
    )
    user_text = (
        f"已定嘅字（唔准加、刪、改任何漢字，次序唔准變）：\n---\n{span}\n---\n"
        f"{leader_note}{doubled_note}{hint}"
        f"請先輸出三行點算（符號／雙圈／形狀），跟住一行 `{FUSED_DELIMITER}`，再輸出加咗標點嘅正文。"
    )
    return combined_system, user_text


def fused_format_problem(raw: Any) -> str:
    """Check the framing, retaining the historical delimiterless zero form."""
    normalized = normalize_fused_answer(raw)
    if normalized.count(FUSED_DELIMITER) > 1:
        return "multiple-delimiters"
    if FUSED_DELIMITER in normalized:
        census, _, body = normalized.partition(FUSED_DELIMITER)
        if not census.strip():
            return "missing-census"
        if not body.strip():
            return "missing-punctuated-text"
        return ""
    # Older packet answers could omit the delimiter when reporting no marks.
    # Do not treat a positive count without a separated body as valid fusion.
    census, _ = split_fused_answer(normalized, "")
    if re.search(r"(?:^|\n)\s*符號[：:]\s*[0０〇零]\s*(?:\n|$)", census):
        return ""
    return "missing-delimiter"


def ask_fused(client: Any, *, crop_bytes: bytes | None, span: str, hint: str,
              leaders: int | None, show_doubled: bool, max_tokens: int,
              census_system: str, punct_system: str) -> tuple[str, str, dict[str, Any]]:
    """Ask through the ordinary client interface, retaining diagnostic framing."""
    system, user = fused_prompts(span=span, hint=hint, leaders=leaders,
                                show_doubled=show_doubled, census_system=census_system,
                                punct_system=punct_system)
    raw, metrics = client.ask_answering(system, user, crop_bytes, max_tokens,
                                       kind="census+punctuate", start="")
    census, punctuated = split_fused_answer(raw, span)
    return census, punctuated, {**metrics, "fusionFormatProblem": fused_format_problem(raw)}
