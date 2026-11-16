#!/usr/bin/env python3
"""Offline checks of the doubts that keep a page from being sealed clean.

No model, no GPU, no corpus: synthetic readings and a fake client only, so the
checks run anywhere the scripts do.  Each case is a shape measured on real
sealed pages (see the docstrings of punctuation_doubts and extra_copies); the
text here is made up.

Usage:
    python3 tests/check_sealing.py
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

BODY = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏"
HEAD = "閏餘成歲"


def kinds(doubts: list[dict]) -> list[str]:
    return [d["kind"] for d in doubts]


def check_punctuation_doubts() -> None:
    refused = {"ok": False, "census": 12, "marks": 0, "block": 3, "tile": 1,
               "range": [40, 80], "reason": "characters changed"}
    doubts = pp.punctuation_doubts([refused])
    assert kinds(doubts) == ["punctuation-left-bare"], doubts
    assert "block 3 tile 1" in doubts[0]["detail"] and "12" in doubts[0]["detail"], doubts

    # Passes that held, or found nothing printed, are not doubts.
    held = {"ok": True, "census": 9, "marks": 9, "block": 0, "range": [0, 30]}
    nothing = {"ok": True, "census": 0, "marks": 0, "block": 1, "range": [0, 20],
               "reason": "no printed marks"}
    assert pp.punctuation_doubts([held, nothing]) == []

    # Census unknown: a doubt only when something says marks are printed.
    unknown = {"ok": False, "census": None, "marks": 0, "block": 2, "range": [0, 20],
               "reason": "characters changed"}
    assert pp.punctuation_doubts([unknown]) == []
    assert pp.punctuation_doubts([unknown, nothing]) == []
    assert len(pp.punctuation_doubts([unknown, held])) == 1
    offered = dict(unknown, refusedOutputs=["甲乙。丙丁。"])
    assert len(pp.punctuation_doubts([offered])) == 1

    # A retry is accepted on the characters alone: a bare retry where the
    # census counted marks, or one far from the count either way, is a doubt.
    retried = {"ok": True, "census": 12, "block": 4, "range": [0, 60],
               "reason": "0 marks written but 12 counted on the page (after retry)"}
    doubts = pp.punctuation_doubts([dict(retried, marks=0)])
    assert kinds(doubts) == ["punctuation-left-bare"] and "a retry kept none" in doubts[0]["detail"], doubts
    under = pp.punctuation_doubts([dict(retried, census=117, marks=63)])
    assert kinds(under) == ["punctuation-count-mismatch"] and "117" in under[0]["detail"], under
    # A seal from before the census said how it counts: 26 marks, 13 of them in
    # doubled circles, are 13 places - within the tolerance of a census of 14
    # that counted each pair once, as the model did in most such passes.
    assert pp.punctuation_doubts([dict(retried, census=14, marks=26, doubled=13)]) == []
    over = pp.punctuation_doubts([dict(retried, census=8, marks=26, doubled=13)])
    assert kinds(over) == ["punctuation-count-mismatch"] and "13 of them in doubled" in over[0]["detail"], over
    # A seal whose census counted glyphs (the census before it reported doubled
    # circles apart) is held to glyphs, one that counts places to places.
    glyphs = dict(retried, census=14, marks=26, doubled=13, censusConvention="glyphs")
    assert kinds(pp.punctuation_doubts([glyphs])) == ["punctuation-count-mismatch"]
    places = dict(retried, census=26, marks=26, doubled=13, censusConvention="places", doubledCensus=13)
    doubts = pp.punctuation_doubts([places])
    assert kinds(doubts) == ["punctuation-count-mismatch"] and "26 places" in doubts[0]["detail"], doubts
    assert pp.punctuation_doubts([dict(places, census=14)]) == []
    # The shape: doubled circles kept against the census's count of them.
    shape = pp.punctuation_doubts([dict(places, census=14, doubledCensus=2)])
    assert kinds(shape) == ["doubled-count-mismatch"] and "counted 2 doubled" in shape[0]["detail"], shape
    assert pp.punctuation_doubts([dict(places, census=14, doubledCensus=11)]) == []
    # Inside the tolerance the first answer is held to: no doubt.
    assert pp.punctuation_doubts([dict(retried, census=20, marks=15)]) == []
    assert pp.punctuation_doubts([dict(retried, census=3, marks=5)]) == []

    # The shape re-check re-read the block: each pass is judged by the marks
    # the re-read put in its own range.
    covered = dict(refused, shapeRecheck="， -> 。", marks=11, marksBeforeRecheck=0)
    assert pp.punctuation_doubts([covered]) == []
    hole = dict(covered, marks=0)
    assert kinds(pp.punctuation_doubts([hole])) == ["punctuation-left-bare"]
    short = dict(covered, marks=3)
    assert kinds(pp.punctuation_doubts([short])) == ["punctuation-count-mismatch"]
    unknown_hole = dict(unknown, shapeRecheck="， -> ", marks=0)
    doubts = pp.punctuation_doubts([unknown_hole, held])
    assert kinds(doubts) == ["punctuation-left-bare"] and "re-check" in doubts[0]["detail"], doubts
    assert pp.punctuation_doubts([dict(unknown_hole, marks=4), held]) == []

    # Seals older than ranges carry the block's total on every pass: judged
    # once per block against the summed census.
    legacy = [{"ok": True, "census": 18, "marks": 25, "block": 5, "tile": 0, "shapeRecheck": "， -> 。"},
              {"ok": False, "census": 12, "marks": 25, "block": 5, "tile": 1, "shapeRecheck": "， -> 。",
               "reason": "characters changed"}]
    assert pp.punctuation_doubts(legacy) == []
    legacy_bare = [dict(entry, marks=0) for entry in legacy]
    doubts = pp.punctuation_doubts(legacy_bare)
    assert kinds(doubts) == ["punctuation-left-bare"] and "block 5:" in doubts[0]["detail"], doubts
    print("punctuation_doubts: refused, bare or miscounted retry, per-range re-check, legacy block totals")


class FakeClient:
    """Census then two punctuation answers, in order; no model."""

    model, effort = "fake", None

    def __init__(self, census: int, answers: list[str]):
        self.census, self.answers = census, list(answers)

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        metrics = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        if system == pp.CENSUS_SYSTEM:
            return f"符號：{self.census}", metrics
        return self.answers.pop(0), metrics


class Arguments:
    max_tokens = 100
    profile_data = None


def check_punctuate_part_retry() -> None:
    span = BODY
    marked = "".join(c + ("。" if i % 3 == 2 else "") for i, c in enumerate(span))
    # The punctuator writes no marks twice: the first answer is refused on
    # the census, the identical retry passes the character check.
    out, record, _ = pp.punctuate_part(FakeClient(8, [span, span]), Arguments(), None, span, "", 30.0)
    assert out == span and record["ok"] and record["marks"] == 0, record
    assert kinds(pp.punctuation_doubts([dict(record, block=0)])) == ["punctuation-left-bare"]
    # Retry far over the count.
    out, record, _ = pp.punctuate_part(FakeClient(3, [marked, marked]), Arguments(), None, span, "", 60.0)
    assert record["ok"] and record["marks"] == 8, record
    assert kinds(pp.punctuation_doubts([dict(record, block=0)])) == ["punctuation-count-mismatch"]
    # Matching the count on the first answer: nothing to report.
    out, record, _ = pp.punctuate_part(FakeClient(8, [marked]), Arguments(), None, span, "", 60.0)
    assert record["ok"] and pp.punctuation_doubts([dict(record, block=0)]) == []
    print("punctuate_part: a retry accepted bare or off the count is a doubt, not a clean seal")


def check_extra_copies() -> None:
    # One engine reads the heading first, the other last; the merge kept both.
    first, second = f"{HEAD}\n{BODY}", f"{BODY}\n{HEAD}"
    runs = pp.extra_copies(f"{HEAD}\n\n{BODY}\n\n{HEAD}", first, second)
    assert [(r["text"], r["page"], r["engineA"], r["engineB"]) for r in runs] == [(HEAD, 2, 1, 1)], runs
    assert sorted(site["unreadBy"] for site in runs[0]["sites"]) == ["A", "B"], runs

    # Printed twice and read twice by both engines: not an extra copy.
    twice = f"{HEAD}{BODY}{HEAD}"
    assert pp.extra_copies(twice, twice, twice) == []

    # Written twice where both engines read it once in the same place: a site
    # neither engine read, the one shape text alone can call a duplication.
    runs = pp.extra_copies(f"{BODY}\n\n{BODY[:12]}", BODY, BODY)
    assert runs and runs[0]["text"] == BODY[:12], runs
    assert [site["unreadBy"] for site in runs[0]["sites"]] == ["AB"], runs
    doubts = pp.extra_copy_doubts(f"{BODY}\n\n{BODY[:12]}", BODY, BODY)
    assert kinds(doubts) == ["extra-copy"] and "both engines left unread" in doubts[0]["detail"], doubts

    # KNOWN AMBIGUOUS: each engine missed a different printed copy, and the
    # union is the page.  The drafts are the same as for a reading placed
    # twice, so these are flagged - worded as a question for the image.
    b1, b2, b3 = BODY[:8], BODY[8:16], BODY[16:24]
    page = f"{HEAD}\n\n{b1}\n\n{HEAD}\n\n{b2}\n\n{HEAD}\n\n{b3}"          # a head on each leaf
    first = f"{b1}\n{HEAD}\n{b2}\n{HEAD}\n{b3}"
    second = f"{HEAD}\n{b1}\n{HEAD}\n{b2}\n{b3}"
    runs = pp.extra_copies(page, first, second)
    assert [(r["text"], r["page"], r["engineA"], r["engineB"]) for r in runs] == [(HEAD, 3, 2, 2)], runs
    doubts = pp.extra_copy_doubts(page, first, second)
    assert "two different misses look the same" in doubts[0]["detail"], doubts
    assert "both engines left unread" not in doubts[0]["detail"], doubts
    # A common word printed twice, each engine skipping a different stretch
    # around one copy (one engine missed six characters, the other a column).
    page = "甲乙丙丁代表戊己庚辛壬癸子丑寅卯辰巳代表午未申酉戌亥"
    first = page.replace("丁代表戊", "")
    second = page.replace("巳代表午", "")
    runs = pp.extra_copies(page, first, second)
    assert [r["text"] for r in runs] == ["代表"], runs

    # A clause dropped between two readings joins a pair the page holds twice;
    # both engines read the joined copy's characters where they sit.
    reading = "山高月小水落石出山高水長"
    assert pp.extra_copies("山高水落石出山高水長", reading, reading) == []

    # Across a line break or a table cell nothing was read together.
    table = "| 春夏 | 秋冬 |\n| --- | --- |\n| 春夏 | 秋冬 |"
    assert pp.extra_copies(table, "春夏秋冬\n春夏秋冬", "春夏\n秋冬\n春夏\n秋冬") == []

    # Figures alone carry no order evidence.
    assert pp.extra_copies("一二三\n\n四一二三", "一二三四", "一二三四") == []
    print("extra_copies: moved heading, copy no engine read; heads and words each engine missed "
          "once flagged as ambiguous; printed twice, joined pairs, seams and figures left alone")


# The book profile the runs below are given (stand_in.preferring), and its
# fingerprint in the provenance.
PROFILE = {"preferEngine": "A"}


def seal(record: dict, markdown: str, render: Path, draft: Path, out: Path) -> None:
    decided = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A",
                                                     profile_sha=pp.profile_fingerprint(PROFILE)))
    record["provenance"] = {"renderSha256": pp.sha256_file(render), "draftSha256": pp.sha256_file(draft),
                            **decided, "markdownSha256": pp.sha256_bytes(markdown.encode("utf-8"))}
    record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
    out.with_suffix(".md").write_text(markdown, encoding="utf-8")
    out.write_bytes(pp.canonical_json_bytes(record))


def check_resume_old_defaults() -> None:
    """Pages sealed with the round-4 fixes off are resumed only with their
    --no-* switches, and then as they were sealed, doubts and all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("renders", "a", "b", "out"):
            (root / name).mkdir()
        for page in (1, 2):
            (root / "renders" / f"page-{page:04d}.png").write_bytes(b"not an image")
            (root / "a" / f"page-{page:04d}.txt").write_text(f"{HEAD}\n{BODY}", encoding="utf-8")
            (root / "b" / f"page-{page:04d}.txt").write_text(f"{BODY}\n{HEAD}", encoding="utf-8")
        base = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "mode": "merge",
                "auditorClean": True, "auditorReport": "", "doubts": [],
                "punctuationPasses": [{"ok": True, "census": 0, "marks": 0, "block": 0}]}
        text = f"{HEAD}\n\n{BODY}"
        seal(dict(base, scanPage=1), text, root / "renders" / "page-0001.png",
             root / "a" / "page-0001.txt", root / "out" / "page-0001.json")
        seal(dict(base, scanPage=2, auditorClean=False, auditorReport="X",
                  doubts=[{"kind": "table-missing", "detail": "X"}]), text,
             root / "renders" / "page-0002.png", root / "a" / "page-0002.txt", root / "out" / "page-0002.json")
        before = (root / "out" / "page-0001.json").read_bytes()
        report = root / "report.json"
        # Sealed with the round-4 fixes off (seal: none of their keys), as
        # before they were on by default: the run that resumes such pages
        # says so (ROUND4_DEFAULTS); without, they are not current, and the
        # run does not start (round4_split; check_resume_one_way).
        old_defaults = ["--no-" + switch[2:] for switch in pp.ROUND4_DEFAULTS]
        root.joinpath("profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
        arguments = [str(root / "renders"), str(root / "a"), str(root / "out"),
                     "--draft2-directory", str(root / "b"), *stand_in.preferring(root),
                     "--pages", "1-2", "--backend", "http", "--endpoint", "http://127.0.0.1:9/v1", "--report", str(report)]
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            assert pp.main(arguments) == 2
        assert said.getvalue().startswith("ERROR resume with " + " ".join(old_defaults)), said.getvalue()
        assert not report.exists()
        run_with = pp.build_parser().parse_args(arguments)
        run_with.prefer_engine, run_with.fallback_engine = "A", "A"
        run_with.profile_sha = pp.profile_fingerprint(PROFILE)
        expect = {"renderSha256": pp.sha256_file(root / "renders" / "page-0001.png"),
                  "draftSha256": pp.sha256_file(root / "a" / "page-0001.txt"), **pp.decision_provenance(run_with)}
        job = SimpleNamespace(out_json=root / "out" / "page-0001.json", out_md=root / "out" / "page-0001.md")
        current, why = pp.validate_completion(job, expect)
        assert not current and why.endswith(
            "sealed with " + ", ".join(pp.ROUND4_DEFAULTS) + " off, this run "
            "has them on (resume with " + " ".join(old_defaults) + ")"), why
        assert (root / "out" / "page-0001.json").read_bytes() == before
        status = pp.main(arguments + old_defaults)
        assert status == 0, status
        run = json.loads(report.read_text(encoding="utf-8"))
        assert run["counts"]["skippedCurrent"] == 2, run["counts"]
        first, second = run["pages"]
        assert first["status"] == "skipped-current" and first["doubts"] == [], first
        assert second["doubts"] == ["table-missing"], second
        assert run["doubtPages"] == {"table-missing": 1}, run
        assert (root / "out" / "page-0001.json").read_bytes() == before, "the seal was rewritten"
    print("resume: pages sealed with the round-4 fixes off resume only with their --no-* switches, as sealed")


def check_resume_one_way() -> None:
    """A book is decided with the round-4 fixes one way throughout: a run that
    would seal some of its pages with them set the other way from its sealed
    pages does not start (round4_split), before any call or write."""
    pages = {n: ([stand_in.block(f"{BODY[n:n + 12]}", [0.2, 0.2, 0.5, 0.05])], BODY[n:n + 12])
             for n in (1, 2, 3, 4)}
    old_defaults = ["--no-" + switch[2:] for switch in pp.ROUND4_DEFAULTS]
    keys = set(pp.ROUND4_DEFAULTS.values())
    model = stand_in.StandIn()
    real = pp.Client
    pp.Client = lambda *args, **kwargs: model
    try:
        with stand_in.book(pages) as root:
            out = root / "out"
            base = [str(root / "renders"), str(root / "a"), str(out), "--draft2-directory", str(root / "b"),
                    *stand_in.preferring(root), "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, "--report", str(root / "r.json")]

            def run(*extra: str) -> tuple[int, str]:
                said = io.StringIO()
                with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
                    status = pp.main([*base, *extra])
                return status, "\n".join(line for line in said.getvalue().splitlines() if line.startswith("ERROR"))

            def sealed() -> dict[str, bytes]:
                return {path.name: path.read_bytes() for path in sorted(out.iterdir())}

            def switches(page: int) -> set[str]:
                record = json.loads((out / f"page-{page:04d}.json").read_text(encoding="utf-8"))
                return keys & set(record["provenance"])

            # A book begun before the fixes were on by default: pages 1-2
            # sealed with them off.
            assert run("--pages", "1-2", *old_defaults) == (0, "")
            before, calls, report = sealed(), len(model.calls), (root / "r.json").read_bytes()
            resume = ("ERROR resume with " + " ".join(old_defaults) + f": 2 sealed page(s) in {out} (1-2) were "
                      "decided with " + ", ".join(pp.ROUND4_DEFAULTS) + " off, "
                      "this run the other way; a book is decided one way throughout, so nothing was proofread")
            # Resumed with the new defaults, the selection its sealed pages
            # and more, other pages only, or one of them decided again: the
            # run does not start, and says how to resume.
            for extra in (("--pages", "1-3"), ("--pages", "3-4"), ("--pages", "1", "--overwrite")):
                status, said = run(*extra)
                assert status == 2 and said.startswith(resume.replace("2 sealed page(s)", "1 sealed page(s)").replace(
                    "(1-2)", "(2)") if "--overwrite" in extra else resume), (extra, said)
            assert sealed() == before and len(model.calls) == calls, "a run that did not start wrote or asked"
            assert (root / "r.json").read_bytes() == report, "a run that did not start wrote a report"
            # With the --no-* switches it resumes: the sealed pages are
            # current, the rest decided with the fixes off like them.
            assert run("--pages", "1-4", *old_defaults) == (0, "")
            counts = json.loads((root / "r.json").read_text(encoding="utf-8"))["counts"]
            assert counts["skippedCurrent"] == 2 and counts["completed"] == 2, counts
            assert all(switches(page) == set() for page in (1, 2, 3, 4))
            # The other way round: a book decided with them on, a run with one off.
            assert run("--pages", "1-4", "--overwrite") == (0, "")
            assert all(switches(page) == keys for page in (1, 2, 3, 4))
            status, said = run("--pages", "1-4", "--no-loop-detector")
            assert status == 2 and said.startswith(
                f"ERROR resume without --no-loop-detector: 4 sealed page(s) in {out} (1-4) were decided with "
                "--loop-detector on"), said
            # Sealed two ways already (by code before this rule): no run
            # resumes them all, and the reason says which pages are which.
            record = json.loads((out / "page-0002.json").read_text(encoding="utf-8"))
            for key in keys:
                record["provenance"].pop(key)
            (out / "page-0002.json").write_bytes(pp.canonical_json_bytes(record))
            for extra in ((), tuple(old_defaults)):
                status, said = run("--pages", "3", *extra)
                assert status == 2 and said.startswith(
                    f"ERROR the sealed pages in {out} were decided with the on-by-default switches set in 2 ways: pages 1, "
                    "3-4 with " + ", ".join(pp.ROUND4_DEFAULTS) + " on; pages "
                    "2 with none of"), said
            # Deciding every page again with --overwrite makes it one way.
            assert run("--pages", "1-4", "--overwrite", *old_defaults) == (0, "")
            assert all(switches(page) == set() for page in (1, 2, 3, 4))
    finally:
        pp.Client = real
    print("resume: a book sealed with the round-4 fixes one way is never resumed the other way - the run does "
          "not start and names the switches that resume it; --overwrite of every page decides it again")


def check_resume_removed_trial() -> None:
    """A page sealed with a trial since removed on (its key, tieBreak) is not
    current: the key stays one validate_completion compares."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page-0001.json"
        markdown = f"{HEAD}\n\n{BODY}"
        off = pp.decision_provenance(SimpleNamespace(prefer_engine="A", fallback_engine="A"))
        on = dict(off, tieBreak="v2")
        job = SimpleNamespace(out_json=out, out_md=out.with_suffix(".md"))
        for sealed_with, want in ((on, False), (off, True)):
            record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "scanPage": 1,
                      "provenance": {**sealed_with, "markdownSha256": pp.sha256_bytes(markdown.encode("utf-8"))}}
            record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
            out.with_suffix(".md").write_text(markdown, encoding="utf-8")
            out.write_bytes(pp.canonical_json_bytes(record))
            current, why = pp.validate_completion(job, off)
            assert current is want, (sealed_with.get("tieBreak"), why)
    print("resume: a page sealed with a removed trial on is not current")


def check_block_spans_across_blocks() -> None:
    """A resolved disagreement over two blocks keeps block-by-block punctuation."""
    head, folio, body = "閏餘成歲", "八六", "天地玄黃宇宙洪荒"
    blocks = [{"text": body, "label": "text"}, {"text": head, "label": "header"},
              {"text": folio, "label": "number"}]
    draft = body + head + folio
    segments = [{"tag": "equal", "a": body, "b": body, "before": "", "after": ""},
                {"tag": "delete", "a": head + folio, "b": "", "before": body[-8:], "after": ""}]
    # 裁決 kept the running head and dropped the folio: each kept character goes
    # back to the block engine A read it in.
    spans = pp.block_spans(blocks, segments, [body, head], draft)
    assert spans == [body, head, ""], spans
    # Resolved to nothing (裁決 answered 無): nothing to place, still block by block.
    assert pp.block_spans(blocks, segments, [body, ""], draft) == [body, "", ""]
    # A resolution sharing nothing with engine A cannot be placed: whole page.
    assert pp.block_spans(blocks, segments, [body, "日月"], draft) is None
    # A character read in the folio's place goes to the folio's block; one read
    # where engine A had nothing goes to the block of the character after it.
    assert pp.place_by_alignment(head + folio, head + "乙", 8, [8, 12, 14]) == [(1, head), (2, "乙")]
    assert pp.place_by_alignment(head + folio, head + "乙" + folio, 8, [8, 12, 14]) == [(1, head), (2, "乙" + folio)]
    # A character that replaces one of engine A's goes where that character was:
    # 甲 for the running head stays in the head block, not with the folio.
    assert pp.place_by_alignment(head + folio, "甲" + folio, 8, [8, 12, 14]) == [(1, "甲"), (2, folio)]
    # The body's last character misread and the head kept: the misread stays in
    # the body block, not in the head (a furniture block) where it would be lost.
    blocks2 = [{"text": body, "label": "text"}, {"text": head, "label": "header"}]
    segments2 = [{"tag": "equal", "a": body[:-1], "b": body[:-1], "before": "", "after": ""},
                 {"tag": "replace", "a": body[-1] + head, "b": "芒", "before": "", "after": ""}]
    assert pp.block_spans(blocks2, segments2, [body[:-1], "芒" + head], body + head) == [body[:-1] + "芒", head]
    # A ruled variant pair keeps each character in its own block.
    assert pp.place_by_alignment("國體", "國体", 0, [1, 2]) == [(0, "國"), (1, "体")]
    print("block_spans: a resolution over two blocks is split where engine A read its characters")


def check_whole_page_doubt() -> None:
    assert "different orders" in pp.whole_page_reason([], BODY, "merge-order-conflict")
    assert "no layout blocks" in pp.whole_page_reason([], BODY, "merge")
    # The reason names engine B's text taken whole only when that happened.
    assert "engine B's text was taken whole" in pp.whole_page_reason(
        [], BODY, "merge-order-conflict-fell-back", "B")
    blocks = [{"text": BODY[:10], "label": "text"}]
    assert "do not account" in pp.whole_page_reason(blocks, BODY, "merge-order-conflict", "A")
    print("whole page: a merged page punctuated without a block pass is a doubt, with its reason")


def check_order_conflict_block_path() -> None:
    """An order conflict that keeps engine A's own reading is punctuated block by
    block; one that takes engine B's reading still goes whole."""
    first, second = BODY[:16], BODY[16:]
    blocks = [stand_in.block(first, [0.55, 0.1, 0.3, 0.8]), stand_in.block(second, [0.15, 0.1, 0.3, 0.8])]
    marked = lambda span: "".join(c + ("。" if i % 4 == 3 else "") for i, c in enumerate(span))
    # Engine B looped: nothing to align, engine A's reading is taken whole.
    with stand_in.book({1: (blocks, "八" * 400)}) as root:
        model = stand_in.StandIn(census=4, punctuate=marked)
        record, markdown = stand_in.run(root, model)[1]
    assert record["mode"] == "merge-order-conflict", record["mode"]
    assert [p["block"] for p in record["punctuationPasses"]] == [0, 1], record["punctuationPasses"]
    assert "punctuation-whole-page" not in kinds(record["doubts"]), record["doubts"]
    assert model.kinds().count("census") == 2 and "format" in model.kinds(), model.kinds()
    assert pp.CJK(markdown) == BODY and markdown.count("。") == len(first) // 4 + len(second) // 4, markdown
    # The structure pass, not the whole-page formatter: its prompt locks the marks.
    assert any(text.startswith("字同標點都已定") for kind, text in model.calls if kind == "format")
    # Engine A read a fragment of what engine B read: engine B's text, whole.
    with stand_in.book({1: ([stand_in.block(BODY[:6], [0.3, 0.1, 0.4, 0.2])], "日月" + BODY[::-1])}) as root:
        record, _ = stand_in.run(root, stand_in.StandIn(census=4, punctuate=marked))[1]
    assert record["mode"] == "merge-order-conflict-fell-back", record["mode"]
    assert record["punctuationPasses"] == [], record["punctuationPasses"]
    whole = [d["detail"] for d in record["doubts"] if d["kind"] == "punctuation-whole-page"]
    assert whole and "engine B's text was taken whole" in whole[0], record["doubts"]
    print("order conflict: engine A's own reading goes block by block, engine B's whole")


if __name__ == "__main__":
    check_punctuation_doubts()
    check_punctuate_part_retry()
    check_extra_copies()
    check_resume_old_defaults()
    check_resume_one_way()
    check_resume_removed_trial()
    check_block_spans_across_blocks()
    check_whole_page_doubt()
    check_order_conflict_block_path()
    print("all sealing checks passed")
