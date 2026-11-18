#!/usr/bin/env python3
"""Share the packet bundles that wait for answers among as many answerers as there is work for.

Usage: python3 scripts/packet_dispatch.py PACKETS_DIR [PACKETS_DIR ...]
           [--max-workers N] [--items-per-worker N] [--out PLAN.json]
           [--apply --answered-by MODEL]

Run it when a step stops with exit 10 or 11 (packet answers wait, --backend packet), and again
when the answerers are back.  PACKETS_DIR is the packets folder the step named (one book's, or a
book profile's); with several - several books stopped at once - their bundles are shared out
together, so one round of answerers serves every book.  It reads each folder's newest wave, takes
the bundles whose answers file is not written yet or does not hold every request once, and prints
a plan as JSON:

  assignments  one per answerer: `bundles` (each bundle file, the answers file it names, its
               questions and kinds) and `prompt`, the whole instruction for a subagent, to be given
               as it is.  The prompt names references/packet-worker.md, which is all an answerer
               reads: it loads no other part of the skill and runs no script.
  apply        for every folder whose wave is answered in full and not applied yet, its apply
               command.  --apply --answered-by MODEL (the answering model's exact id) runs them
               all here, one after the other, and says how each ended
  rerun        the folders whose wave is applied: run the step that wrote it again, the SAME
               command
  waiting      what is left, in numbers

The main agent starts one subagent per assignment, all at once, as many as its platform runs
together (--max-workers, default 24: fewer assignments, each with more bundles, when the work is
more than that many answerers' worth).  A platform with no subagents answers the bundles itself, in
the plan's order, by the same references/packet-worker.md.  --items-per-worker (default 10) is how
many questions one answerer is given: fewer finish a wave sooner and cost a little more, since
every answerer reads its instruction once.

Measured (2026-11-17, a small vision model on the benchmark's samples): 12 answerers given only
their bundles and that one page of instruction answered 127 questions at 6.4 s and 62 thousand
input tokens a question; one agent a book, running the steps and answering every wave in one
session, took 7.6 s and 90 thousand.  With a larger model each answerer had loaded the whole skill
and been given one bundle, and a question took 245 s where a long session took 35.

Nothing is written but --out, and with --apply what apply_packet_answers.py writes.  Exit 0; 2
when a folder has no wave to read; 1 when an apply command failed (its output is in the plan).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any, Sequence

BRIEF = Path(__file__).resolve().parent.parent / "references" / "packet-worker.md"
DEFAULT_MAX_WORKERS = 24
DEFAULT_ITEMS_PER_WORKER = 10

PROMPT = ("Read {brief} and follow it exactly. It is all you need: load no other instructions and run none "
          "of the skill's scripts. Answer these packet bundles and nothing else:\n{bundles}\n"
          "Write each bundle's answers into the answers file that bundle names, never into your reply. "
          "When every bundle is written, reply with one line: how many questions you answered, and any "
          "you were unsure of.")


class DispatchError(RuntimeError):
    """A packets folder with no wave to share out."""


def newest_wave(packets_dir: Path) -> tuple[Path, dict[str, Any]]:
    """The newest wave of PACKETS_DIR and its manifest."""
    waves = sorted((path for path in packets_dir.glob("wave-*") if re.fullmatch(r"wave-\d+", path.name)),
                   key=lambda path: int(path.name.split("-")[1]))
    if not waves:
        raise DispatchError(f"no wave in {packets_dir}: run the step that stops with exit 10 first")
    manifest_path = waves[-1] / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DispatchError(f"cannot read {manifest_path}: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("bundles"), list):
        raise DispatchError(f"{manifest_path} lists no bundles")
    return waves[-1], manifest


def answered_ids(answers_path: Path) -> list[str] | None:
    """The request ids an answers file answers, in any form apply_packet_answers.py takes; None
    when it is not written or not JSON of such a form."""
    try:
        data = json.loads(answers_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("answers"), list):
        data = data["answers"]
    if isinstance(data, dict):
        return [str(key) for key in data]
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        return [str(row.get("request_id")) for row in data]
    return None


def bundle_state(summary: dict[str, Any]) -> tuple[str, int]:
    """Where one bundle of a manifest stands - "answered", "unwritten" or "incomplete" (its answers
    file does not hold every request of the bundle exactly once) - and how many questions it holds."""
    bundle = Path(summary["bundle"])
    try:
        wanted = [str(item["request_id"]) for item in json.loads(bundle.read_text(encoding="utf-8"))["items"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DispatchError(f"cannot read {bundle}: {error}") from error
    answers = Path(summary["expectedAnswers"])
    if not answers.is_file():
        return "unwritten", len(wanted)
    given = answered_ids(answers)
    return ("answered" if given is not None and sorted(given) == sorted(wanted) else "incomplete"), len(wanted)


def applied(manifest: dict[str, Any]) -> bool:
    """Whether every request of a wave has its answer in the folder's answers.jsonl already."""
    wanted = {str(request) for request in manifest.get("request_ids") or []}
    if not wanted:
        return False
    have: set[str] = set()
    try:
        with open(manifest["answers_jsonl"], encoding="utf-8") as handle:
            for line in handle:
                try:
                    have.add(str(json.loads(line).get("request_id")))
                except (ValueError, AttributeError):
                    continue           # a torn line: apply_packet_answers.py's to judge
    except (OSError, KeyError):
        return False
    return wanted <= have


def share(bundles: Sequence[dict[str, Any]], items_per_worker: int, max_workers: int) -> list[list[dict[str, Any]]]:
    """BUNDLES, in order, in runs of about ITEMS_PER_WORKER questions (a bundle is never split, so
    a larger one is a run of its own), and in at most MAX_WORKERS runs: where that many answerers
    are not enough for runs that short, the runs are as much longer as it takes."""
    def runs(target: int) -> list[list[dict[str, Any]]]:
        out: list[list[dict[str, Any]]] = []
        held = 0
        for bundle in bundles:
            if out and held + bundle["items"] <= target:
                out[-1].append(bundle)
                held += bundle["items"]
            else:
                out.append([bundle])
                held = bundle["items"]
        return out

    target = max(1, items_per_worker)
    found = runs(target)
    total = sum(bundle["items"] for bundle in bundles)
    while len(found) > max(1, max_workers) and target < total:
        target += 1
        found = runs(target)
    return found


def plan(packets_dirs: Sequence[Path], max_workers: int = DEFAULT_MAX_WORKERS,
         items_per_worker: int = DEFAULT_ITEMS_PER_WORKER, skip: Sequence[str] = ()) -> dict[str, Any]:
    """The plan for PACKETS_DIRS (the module's docstring).  SKIP: bundle files an answerer has
    already been given and is still at work on (ocr_batch.py): they count as waiting, and no
    assignment holds them."""
    waiting: list[dict[str, Any]] = []
    apply: list[dict[str, Any]] = []
    rerun: list[dict[str, Any]] = []
    folders = []
    for packets_dir in packets_dirs:
        packets_dir = packets_dir.expanduser().resolve()
        wave_dir, manifest = newest_wave(packets_dir)
        states = [(summary, *bundle_state(summary)) for summary in manifest["bundles"]]
        left = [{"bundle": summary["bundle"], "answers": summary["expectedAnswers"], "items": items,
                 "kinds": summary.get("kinds") or [], "state": state}
                for summary, state, items in states if state != "answered"]
        waiting += left
        folders.append({"packets": str(packets_dir), "wave": wave_dir.name, "bundles": len(states),
                        "bundlesWaiting": len(left), "questionsWaiting": sum(row["items"] for row in left)})
        if not left and applied(manifest):
            rerun.append({"packets": str(packets_dir), "wave": wave_dir.name})
        elif not left:
            apply.append({"packets": str(packets_dir), "wave": wave_dir.name, "command": manifest["apply_command"]})
    assignments = []
    taken = set(skip)
    for number, run in enumerate(share([row for row in waiting if row["bundle"] not in taken],
                                       items_per_worker, max_workers), start=1):
        lines = "\n".join(f"- {bundle['bundle']} ({bundle['items']} question{'s' if bundle['items'] != 1 else ''})"
                          for bundle in run)
        assignments.append({"worker": number, "questions": sum(bundle["items"] for bundle in run),
                            "bundles": run, "prompt": PROMPT.format(brief=BRIEF, bundles=lines)})
    return {
        "schema": 1,
        "workerInstructions": str(BRIEF),
        "waiting": {"questions": sum(row["items"] for row in waiting), "bundles": len(waiting),
                    "answerers": len(assignments)},
        "folders": folders,
        "assignments": assignments,
        "apply": apply,
        "rerun": rerun,
        "next": ("start one subagent per assignment, all at once, each with its prompt as it is (no subagents: "
                 "answer the bundles yourself by workerInstructions); when they are back, run this command again"
                 if assignments else
                 "every wave is answered: run this command again with --apply --answered-by <the answering "
                 "model's exact id>, then the step that wrote each wave again, the SAME command"
                 if apply else
                 "every wave is applied: run the step that wrote each one again, the SAME command"),
    }


def run_apply(found: dict[str, Any], answered_by: str) -> bool:
    """Run every apply command of the plan FOUND with --answered-by ANSWERED_BY; each entry of
    its `apply` is given `exit` and the command's last lines.  True when all ended with 0."""
    fine = True
    for entry in found["apply"]:
        done = subprocess.run([*shlex.split(entry["command"]), "--answered-by", answered_by],
                              capture_output=True, text=True)
        entry["exit"] = done.returncode
        entry["output"] = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
        if done.returncode == 0:
            found["rerun"].append({"packets": entry["packets"], "wave": entry["wave"]})
        fine = fine and done.returncode == 0
    if fine and not found["assignments"]:
        found["next"] = "every wave is applied: run the step that wrote each one again, the SAME command"
    return fine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("packets_dirs", nargs="+", type=Path, metavar="PACKETS_DIR",
                        help="a packets folder a step stopped on (several: their bundles are shared out together)")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"the most answerers to plan for: what the platform runs at once (default {DEFAULT_MAX_WORKERS})")
    parser.add_argument("--items-per-worker", type=int, default=DEFAULT_ITEMS_PER_WORKER,
                        help=f"about how many questions one answerer is given (default {DEFAULT_ITEMS_PER_WORKER})")
    parser.add_argument("--out", type=Path, help="also write the plan to this file")
    parser.add_argument("--apply", action="store_true",
                        help="run the apply command of every folder whose wave is answered in full "
                             "(needs --answered-by)")
    parser.add_argument("--answered-by", metavar="MODEL",
                        help="with --apply: the exact id of the model that answered, recorded with every answer")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.apply and not arguments.answered_by:
        parser.error("--apply needs --answered-by MODEL, the exact id of the model that answered")
    try:
        found = plan(arguments.packets_dirs, arguments.max_workers, arguments.items_per_worker)
    except DispatchError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2
    fine = run_apply(found, arguments.answered_by) if arguments.apply else True
    text = json.dumps(found, ensure_ascii=False, indent=2) + "\n"
    if arguments.out:
        arguments.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if fine else 1


if __name__ == "__main__":
    sys.exit(main())
