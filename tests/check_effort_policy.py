#!/usr/bin/env python3
"""Offline checks of switch --effort-policy: the rung of the effort ladder a
kind of question is first asked on over HTTP.

No model, no GPU, no corpus: a model server in this process (stand_in.Served)
that records the reasoning_effort of each request, and a made-up page.

- With no policy every request is asked on the client's effort, as before.
- A kind the policy names begins on its rung; a kind it does not name, a
  retry among them, keeps the client's effort.
- The client's effort is the highest rung of any call: a policy only lowers.
- The ladder steps down from the policy's rung when an answer is cut off.
- A START the caller names (ask_lower) is asked, not the policy's rung.
- A kind or a rung the policy does not know is refused, and the kinds it
  knows are the kinds the scripts ask (EFFORT_KINDS against their source).
- A run of proofread_pages.main() with the switch records it with the page
  and in the run report, asks the census low and the rest on the client's
  effort, and seals the same text as a run without it.

Usage:
    python3 tests/check_effort_policy.py
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FAILED: list[str] = []


def check(name: str, got: object, want: object) -> None:
    if got != want:
        FAILED.append(name)
        print(f"FAIL {name}\n  got  {got!r}\n  want {want!r}")
    else:
        print(f"ok   {name}")


class Recorder:
    """An endpoint of this process's model server that answers as MODEL (a
    stand_in.StandIn) does and keeps each request's (what it was, its
    reasoning_effort); CUT_BELOW: requests asked on these efforts are cut off
    at max_tokens with nothing written."""

    def __init__(self, model: stand_in.StandIn | None = None, cut: tuple[str | None, ...] = ()):
        self.model = model or stand_in.StandIn()
        self.cut = cut
        self.seen: list[tuple[str, str | None]] = []
        self.endpoint = stand_in.served().endpoint(self.respond)

    def respond(self, body: dict) -> dict:
        system = body["messages"][0]["content"]
        parts = body["messages"][1]["content"]
        user_text = next(part["text"] for part in parts if part.get("type") == "text")
        effort = body.get("reasoning_effort")
        what = ("census" if system.startswith(pp.CENSUS_SYSTEM.split("\n")[0])
                else "punctuate" if system.startswith(pp.PUNCT_SYSTEM.split("\n")[0])
                else "adjudicate" if system == pp.ADJUDICATE_SYSTEM
                else "format" if system.startswith("你係 jyut-ocr-formatter") else "other")
        self.seen.append((what, effort))
        if effort in self.cut:
            return stand_in.reply("", finish="length", completion_tokens=body["max_tokens"])
        text, _ = self.model.ask_answering(system, user_text, None, body["max_tokens"])
        return stand_in.reply(text)

    def efforts(self) -> list[str | None]:
        return [effort for _, effort in self.seen]


def client(recorder: Recorder, effort: str | None = "xhigh", policy: str = "") -> pp.Client:
    return pp.Client(recorder.endpoint, "stand-in", effort, 30, effort_policy=pp.parse_effort_policy(policy))


def ask(model: pp.Client, kind: str) -> dict:
    return model.ask_answering(pp.CENSUS_SYSTEM, "數", None, 1000, kind=kind)[1]


def check_rungs() -> None:
    recorder = Recorder()
    plain = client(recorder)
    for kind in ("census", "format", "punctuate", "format-retry"):
        ask(plain, kind)
    check("no policy: every kind on the client's effort", recorder.efforts(), ["xhigh"] * 4)

    recorder = Recorder()
    policed = client(recorder, policy="census=low,format=medium,notation=none")
    for kind in ("census", "format", "punctuate", "format-retry", "notation"):
        ask(policed, kind)
    check("a named kind begins on its rung; an unnamed one (the retry too) on the client's",
          recorder.efforts(), ["low", "medium", "xhigh", "xhigh", None])

    recorder = Recorder()
    ask(client(recorder, effort="low", policy="census=xhigh"), "census")
    ask(client(recorder, effort="medium", policy="census=low"), "census")
    ask(client(recorder, effort=None, policy="census=medium"), "census")
    check("the client's effort is the highest rung: a policy only lowers", recorder.efforts(), ["low", "low", None])

    recorder = Recorder(cut=("low",))
    metrics = ask(client(recorder, policy="census=low"), "census")
    check("cut off on the policy's rung: the next rung down, not back up", recorder.efforts(), ["low", None])
    check("the answer after the step down is a whole answer", bool(metrics.get("degraded")), False)

    recorder = Recorder()
    pp.ask_lower(client(recorder, policy="census=low"), "medium", pp.CENSUS_SYSTEM, "數", None, 1000, kind="census")
    check("a START the caller names is asked, not the policy's rung", recorder.efforts(), ["medium"])


def check_policy_text() -> None:
    policy = pp.parse_effort_policy(" format=LOW, census=none ,adjudicate=medium,")
    check("parsed", policy, {"format": "low", "census": None, "adjudicate": "medium"})
    check("recorded in the order of its kinds", pp.effort_policy_text(policy), "adjudicate=medium,census=none,format=low")
    check("and read back as it was", pp.parse_effort_policy(pp.effort_policy_text(policy)), policy)
    check("no policy", (pp.parse_effort_policy(""), pp.parse_effort_policy(None), pp.effort_policy_text({})), ({}, {}, ""))
    for bad in ("census", "cencus=low", "census=high", "census=low,census=medium", "=low"):
        try:
            pp.parse_effort_policy(bad)
            refused = False
        except pp.ProofreadError:
            refused = True
        check(f"refused: {bad}", refused, True)
    check("the same pair twice is one pair", pp.parse_effort_policy("census=low,census=low"), {"census": "low"})
    recorder = Recorder()
    check("a client with a policy says so", pp.effort_policy_record(client(recorder, policy="format=low,census=low")),
          {"effortPolicy": "census=low,format=low"})
    check("one without says nothing", pp.effort_policy_record(client(recorder)), {})
    check("nor does a client that has none (packets, a stand-in)", pp.effort_policy_record(stand_in.StandIn()), {})


def check_kinds() -> None:
    """EFFORT_KINDS against the scripts: every kind an ask_answering call
    names is one the policy takes (a kind added later and left out could not
    be given a rung), and the fidelity questions' three with their correction."""
    named = set()
    paths = [SCRIPTS / name for name in ("proofread_pages.py", "book_profile.py", "_punctuation_fusion.py")]
    paths += sorted(SCRIPTS.glob("_proofread_*.py"))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        named |= set(re.findall(r'\bkind="([a-z+-]+)"', source))
    fidelity = (SCRIPTS / "_content_fidelity.py").read_text(encoding="utf-8")
    stages = set(re.findall(r'"(page-inventory|content-recovery|coverage-review)"', fidelity))
    check("the fidelity questions are the three", stages, {"page-inventory", "content-recovery", "coverage-review"})
    check("and their one correction is the kind plus -correction", 'kind=kind + "-correction"' in fidelity, True)
    named |= stages | {stage + "-correction" for stage in stages}
    check("every kind the scripts ask can be given a rung", sorted(named - set(pp.EFFORT_KINDS)), [])
    check("and the policy names no kind they do not ask", sorted(set(pp.EFFORT_KINDS) - named), [])


def check_summary() -> None:
    ledger = [{"kind": "census", "effort": "low"}, {"kind": "census", "effort": None},
              {"kind": "census", "effort": "low"}, {"kind": "format", "effort": "xhigh"},
              {"kind": "writer", "effort": "xhigh", "usage_source": next(iter(pp.PACKET_USAGE_SOURCES))}]
    rows = pp.summarize_calls(ledger)
    check("the summary counts the rungs a kind was asked on", rows["census"]["efforts"], {"low": 2, "none": 1})
    check("one rung", rows["format"]["efforts"], {"xhigh": 1})
    check("a packet answer has no rung", "efforts" in rows["writer"], False)


def proofread(root: Path, recorder: Recorder, extra: list[str], *,
              pages: str = "1", overwrite: bool = True) -> tuple[dict, str, dict]:
    status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                      "--draft2-directory", str(root / "b"), *stand_in.preferring(root), "--pages", pages,
                      *(["--overwrite"] if overwrite else []), "--report", str(root / "report.json"),
                      "--backend", "http", "--endpoint", recorder.endpoint, "--model", "stand-in",
                      # Unstreamed requests: the stand-in server answers with one JSON payload.
                      "--no-loop-detector", "--workers", "1", "--page-concurrency", "1", *extra])
    assert status == 0, status
    record = json.loads((root / "out/page-0001.json").read_text(encoding="utf-8"))
    return (record, (root / "out/page-0001.md").read_text(encoding="utf-8"),
            json.loads((root / "report.json").read_text(encoding="utf-8")))


def check_run() -> None:
    page = {1: ([stand_in.block("天地玄黃宇宙洪荒日月盈昃辰宿列張", (0.1, 0.1, 0.4, 0.6), order=0)],
                "天地玄黃宇宙洪荒日月盈昃辰宿列張")}
    with stand_in.book(page) as root:
        plain = Recorder()
        record, markdown, report = proofread(root, plain, [])
        check("no switch: nothing recorded with the page", "effortPolicy" in record, False)
        check("nor in the run report", "effortPolicy" in report, False)
        check("and every request on the client's effort", set(plain.efforts()), {"xhigh"})
        check("the report preserves the page's effort counts", efforts(report), efforts(record))
    with stand_in.book(page) as root:
        policed = Recorder()
        record2, markdown2, report2 = proofread(root, policed, ["--effort-policy", "census=low"])
        check("the same questions, in the same order", [what for what, _ in policed.seen], [what for what, _ in plain.seen])
        check("the census is asked low", {effort for what, effort in policed.seen if what == "census"}, {"low"})
        check("and nothing else is", {effort for what, effort in policed.seen if what != "census"} <= {"xhigh"}, True)
        check("the census was asked at all", "census" in [what for what, _ in policed.seen], True)
        check("recorded with the page, beside its effort", (record2.get("reasoningEffort"), record2.get("effortPolicy")),
              ("xhigh", "census=low"))
        check("and in the run report", report2.get("effortPolicy"), "census=low")
        check("the report preserves the policy's effort counts", efforts(report2), efforts(record2))
        check("the page's text is the same", markdown2, markdown)
        check("and its provenance: the policy is not a decision", record2["provenance"].keys(), record["provenance"].keys())
    with stand_in.book(page) as root:
        try:
            status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                              "--draft2-directory", str(root / "b"), *stand_in.preferring(root), "--pages", "1",
                              "--backend", "http", "--endpoint", Recorder().endpoint, "--model", "stand-in",
                              "--effort-policy", "cencus=low"])
        except SystemExit as stop:
            status = stop.code
        check("a kind the policy does not know stops the run before a page is asked", status, 2)
        check("nothing was sealed", list((root / "out").glob("page-*.json")), [])


def efforts(record: dict) -> dict:
    return {kind: row["efforts"] for kind, row in record["calls"].items() if "efforts" in row}


def check_report_calls() -> None:
    page = ([stand_in.block("天地玄黃宇宙洪荒日月盈昃辰宿列張", (0.1, 0.1, 0.4, 0.6), order=0)],
            "天地玄黃宇宙洪荒日月盈昃辰宿列張")
    with stand_in.book({1: page, 2: page, 3: page}) as root:
        # Each census starts low, is cut off, then succeeds on the last rung.
        recorder = Recorder(cut=("low",))
        extra = ["--effort-policy", "census=low"]
        _, _, report = proofread(root, recorder, extra, pages="1-2")
        records = [json.loads((root / f"out/page-{number:04d}.json").read_text(encoding="utf-8"))
                   for number in (1, 2)]
        expected: dict[str, Counter] = {}
        for record in records:
            for kind, counts in efforts(record).items():
                expected.setdefault(kind, Counter()).update(counts)
        check("two completed pages sum every kind and rung", efforts(report), expected)
        check("both the policy rung and the fallback rung were counted",
              set(expected["census"]), {"low", "none"})
        check("effort counts account for every HTTP request",
              sum(sum(counts.values()) for counts in efforts(report).values()), len(recorder.seen))

        before = {path.name: path.read_bytes() for path in (root / "out").glob("page-*.*")}
        seen = len(recorder.seen)
        _, _, continued = proofread(root, recorder, extra, pages="1-3", overwrite=False)
        third = json.loads((root / "out/page-0003.json").read_text(encoding="utf-8"))
        check("a continued run skips two pages and completes one",
              (continued["counts"]["skippedCurrent"], continued["counts"]["completed"]), (2, 1))
        check("only newly completed pages contribute effort counts", efforts(continued), efforts(third))
        check("the continued report counts only this run's requests",
              sum(sum(counts.values()) for counts in efforts(continued).values()), len(recorder.seen) - seen)
        check("continued pages retain their seals and text",
              {name: (root / "out" / name).read_bytes() for name in before}, before)

        seen = len(recorder.seen)
        _, _, resumed = proofread(root, recorder, extra, pages="1-3", overwrite=False)
        check("an entirely current run has no call totals", resumed["calls"], {})
        check("and makes no requests", len(recorder.seen), seen)

    # This older client shape supplies usage but no recorded effort. The
    # report must not infer a rung from the CLI or the page's reasoningEffort.
    summarize = pp.summarize_calls

    def without_efforts(calls: list) -> dict:
        rows = summarize(calls)
        for row in rows.values():
            row.pop("efforts", None)
        return rows

    with stand_in.book({1: page, 2: page}) as root:
        with mock.patch.object(pp, "summarize_calls", side_effect=without_efforts):
            _, _, report = proofread(root, Recorder(), extra, pages="1-2")
        check("legacy call rows still have numeric totals", bool(report["calls"]), True)
        check("missing effort metadata stays absent", efforts(report), {})


def main() -> int:
    for step in (check_rungs, check_policy_text, check_kinds, check_summary, check_run, check_report_calls):
        step()
    if FAILED:
        print(f"\n{len(FAILED)} check(s) failed: " + "; ".join(FAILED))
        return 1
    print("\neffort policy: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
