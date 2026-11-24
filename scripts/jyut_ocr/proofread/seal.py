"""Provenance hashes, resume validation, round-4 switches, and page sealing."""

from __future__ import annotations

from collections import Counter
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Collection, Iterable, Iterator, Mapping, Sequence


from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes, sha256_file,
)
from jyut_ocr.core import draft_contract as _draft_contract  # noqa: E402
from jyut_ocr.core import tables as _tables  # noqa: E402
from jyut_ocr.proofread import loop_watch as _loop_watch  # noqa: E402
from jyut_ocr.core import content_fidelity as _content_fidelity  # noqa: E402
from jyut_ocr.core import note_markers as _note_markers  # noqa: E402
from jyut_ocr.proofread import packet_events as _packet_events  # noqa: E402
from jyut_ocr.proofread import punctuation_fusion as _punctuation_fusion  # noqa: E402
from jyut_ocr.proofread.core import (  # noqa: E402
    DEFAULT_MODEL, ENGINE_A_NAME, ENGINE_B_NAME, MISSING_ENGINE_DOUBT, PageJob, SCHEMA_VERSION,
    facade_attr, punctuation_mode,
)
from jyut_ocr.proofread.variants import (  # noqa: E402
    opencc_allographs, user_variant_rulings, _LIST_SHA256,
)
from jyut_ocr.proofread.adjudicate import NEIGHBOUR_WINDOW  # noqa: E402
from jyut_ocr.proofread.furniture import learned_heads_seal  # noqa: E402
from jyut_ocr.proofread.markers import figure_evidence_seal  # noqa: E402
from jyut_ocr.paths import script_path  # noqa: E402
from jyut_ocr.proofread.table_rebuild import DRAFT2_KEY  # noqa: E402


PROOFREAD_SCRIPT_PATH = script_path("proofread_pages.py")


def sealed_inputs(job: PageJob) -> dict[str, str]:
    """The input hashes a page's seal carries: its render and both engines'
    drafts."""
    return {"renderSha256": facade_attr("sha256_file", sha256_file)(job.render), "draftSha256": facade_attr("sha256_file", sha256_file)(job.draft_text),
            DRAFT2_KEY: facade_attr("sha256_file", sha256_file)(job.draft2_text) if job.draft2_text is not None else "",
            "draftSemanticsSha256": _draft_contract.semantic_digest(job.draft_json),
            "draft2SemanticsSha256": _draft_contract.semantic_digest(
                job.draft2_text.with_suffix(".json") if job.draft2_text else None)}


def validate_completion(job: PageJob, expect: Mapping[str, str]) -> tuple[bool, str]:
    if not job.out_json.is_file() or not job.out_md.is_file():
        return False, "no existing output"
    try:
        raw = job.out_json.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return False, f"unreadable: {error}"
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return False, "legacy schema"
    if raw != canonical_json_bytes(record):
        return False, "not canonical or edited"
    provenance = record.get("provenance") or {}
    # Rule keys the seal was made before (LEGACY_ABSENT_KEYS): taken as sealed.
    legacy: list[str] = []
    for key, value in expect.items():
        # The switches' keys (last in EXPECT) are compared below, both ways;
        # the informational keys not at all; engine B's draft and the
        # fidelity switch below.
        if (key in OPTIONAL_PROVENANCE or key in INFORMATIONAL_PROVENANCE
                or key in (DRAFT2_KEY, CONTENT_FIDELITY_KEY, "punctuationMode")):
            continue
        if key not in provenance and key in _draft_contract.SEMANTIC_KEYS:
            source = (job.draft_json if key == "draftSemanticsSha256" else
                      job.draft2_text.with_suffix(".json") if job.draft2_text else None)
            if not _draft_contract.requires_semantics(_draft_contract.read_record(source)):
                legacy.append(key)
                continue
        if key not in provenance and key in LEGACY_ABSENT_KEYS:
            legacy.append(key)
            continue
        if provenance.get(key, PROVENANCE_ABSENT_MEANS.get(key)) != value:
            return False, f"provenance {key} differs"
    # Before mode was explicit, packet always fused and HTTP always split.
    # An old seal with no transport evidence may resume only under auto;
    # explicit experiments must never silently reuse an unknown mode.
    if "punctuationMode" in expect:
        sealed_mode = provenance.get("punctuationMode")
        if sealed_mode is None:
            transport = provenance.get("proofreadTransport")
            sealed_mode = {"packet": "fused", "http": "split"}.get(transport)
        if sealed_mode is None:
            if expect.get("punctuationModeRequested", "auto") != "auto":
                return False, "provenance punctuationMode is unknown in legacy seal"
        elif sealed_mode != expect["punctuationMode"]:
            return False, "provenance punctuationMode differs"
    # Engine B's draft: a page sealed with another (or none, where there now
    # is one) was merged from other readings.  A seal without the key was
    # made before it was recorded and is taken as it is.
    if DRAFT2_KEY in provenance and provenance[DRAFT2_KEY] != expect.get(DRAFT2_KEY, ""):
        return False, ("engine B's draft differs from the one the page was sealed with"
                       if provenance[DRAFT2_KEY] else "engine B's draft was added after the page was sealed")
    # A run with --content-fidelity on asks for its evidence, not for the
    # key: a page sealed by a skill that always ran the v2 questions carries
    # the current contract with no switch key, and validate_completed_record
    # below decides whether that evidence holds.
    if (expect.get(CONTENT_FIDELITY_KEY) and provenance.get(CONTENT_FIDELITY_KEY) != expect[CONTENT_FIDELITY_KEY]
            and record.get("contentFidelityContract") != _content_fidelity.CONTRACT_VERSION):
        return False, (f"provenance {CONTENT_FIDELITY_KEY} differs: sealed without --content-fidelity on, "
                       "which this run asks for")
    for key in OPTIONAL_PROVENANCE:
        # Absent means off: a page sealed with the switch on is not current
        # for a run with it off.
        if key not in ROUND4_DEFAULTS.values() and provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    # The round-4 fixes: a page sealed with one off and asked on, or the other
    # way round (a book begun before they were on by default), is told which
    # switches resume it as it was sealed.
    turned = [switch for switch, key in ROUND4_DEFAULTS.items() if (key in provenance) != (key in expect)]
    if turned:
        sealed_off = [switch for switch in turned if ROUND4_DEFAULTS[switch] not in provenance]
        sealed_on = [switch for switch in turned if switch not in sealed_off]
        return False, ("provenance " + ", ".join(ROUND4_DEFAULTS[switch] for switch in turned)
                       + (" differs: " if len(turned) == 1 else " differ: ")
                       + "; ".join(part for part in (
                           f"sealed with {', '.join(sealed_off)} off, this run has "
                           f"{'it' if len(sealed_off) == 1 else 'them'} on (resume with "
                           + " ".join("--no-" + switch[2:] for switch in sealed_off) + ")" if sealed_off else "",
                           f"sealed with {', '.join(sealed_on)} on, this run has "
                           f"{'it' if len(sealed_on) == 1 else 'them'} off" if sealed_on else "") if part))
    for key in ROUND4_DEFAULTS.values():
        if provenance.get(key) != expect.get(key):
            return False, f"provenance {key} differs"
    body = job.out_md.read_bytes()
    if sha256_bytes(body) != provenance.get("markdownSha256"):
        return False, "markdown does not match its seal"
    sealed = provenance.get("jsonPayloadSha256")
    unsealed = json.loads(json.dumps(record, ensure_ascii=False))
    del unsealed["provenance"]["jsonPayloadSha256"]
    if sha256_bytes(canonical_json_bytes(unsealed)) != sealed:
        return False, "payload seal does not match"
    # Content-fidelity evidence is checked wherever a page carries it (it
    # must still be bound to this render and text); a page with none - the
    # switch off, or sealed before it existed - needs none.
    if _content_fidelity.carries_record(record) or expect.get(CONTENT_FIDELITY_KEY):
        try:
            _content_fidelity.validate_completed_record(record, body.decode("utf-8"), provenance["renderSha256"])
        except (ValueError, KeyError, TypeError) as error:
            return False, f"content fidelity: {error}"
    if legacy:
        return True, LEGACY_REASON + ", ".join(legacy)
    return True, "current"


def page_spans(pages: Collection[int]) -> str:
    """PAGES as runs: 1-3, 7, 9-10."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(f"{lo}-{hi}" if hi > lo else str(lo) for lo, hi in runs)


def round4_split(output: Path, rewritten: Collection[int], expect: Mapping[str, Any]) -> str | None:
    """Why a run with EXPECT's provenance would leave the book in OUTPUT decided
    two ways, else None: a page sealed there that this run does not write again
    (REWRITTEN: the pages it proofreads with --overwrite) with one of the
    round-4 fixes (ROUND4_DEFAULTS) on where this run has it off, or off where
    it has it on.  validate_completion blocks such a page, but the run would
    go on to decide the book's other pages with this run's switches: a book
    begun before the fixes were on by default and resumed without the four
    --no-* switches would be sealed half with them off and half on, and no
    later run finds every page current without --overwrite.  So the run does
    not start (main); the reason says which switches resume the book as its
    sealed pages were decided."""
    wanted = {key for key in ROUND4_DEFAULTS.values() if key in expect}
    skip = set(rewritten)
    states: dict[frozenset, list[int]] = {}
    for path in sorted(output.glob("page-*.json")):
        match = re.fullmatch(r"page-(\d{4})\.json", path.name)
        if not match or int(match.group(1)) in skip:
            continue
        try:
            provenance = json.loads(path.read_text(encoding="utf-8")).get("provenance")
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
        if not isinstance(provenance, dict) or "renderSha256" not in provenance:
            continue
        states.setdefault(frozenset(key for key in ROUND4_DEFAULTS.values() if key in provenance),
                          []).append(int(match.group(1)))
    other = {state: pages for state, pages in states.items() if state != wanted}
    if not other:
        return None
    switch = {key: name for name, key in ROUND4_DEFAULTS.items()}
    order = list(ROUND4_DEFAULTS.values())

    def names(keys: Collection[str]) -> list[str]:
        return [switch[key] for key in order if key in set(keys)]

    if len(states) > 1:
        # The sealed pages disagree among themselves already: no run resumes
        # them all.
        return ("the sealed pages in " + str(output) + " were decided with the on-by-default switches set in "
                + str(len(states)) + " ways: "
                + "; ".join(f"pages {page_spans(pages)} with "
                            + (", ".join(names(state)) or "none of " + ", ".join(ROUND4_DEFAULTS)) + " on"
                            for state, pages in sorted(states.items(), key=lambda item: min(item[1])))
                + " - no run finds them all current; decide one part again with --overwrite and that part's "
                  "--pages, with the switches of the other; nothing was proofread")
    (state, pages), = other.items()
    sealed_off, sealed_on = names(wanted - state), names(state - wanted)
    return ("resume "
            + " and ".join(part for part in (
                "with " + " ".join("--no-" + name[2:] for name in sealed_off) if sealed_off else "",
                "without " + " ".join("--no-" + name[2:] for name in sealed_on) if sealed_on else "") if part)
            + f": {len(pages)} sealed page(s) in {output} ({page_spans(pages)}) were decided with "
            + "; ".join(part for part in (
                f"{', '.join(sealed_off)} off" if sealed_off else "",
                f"{', '.join(sealed_on)} on" if sealed_on else "") if part)
            + ", this run the other way; a book is decided one way throughout, so nothing was proofread "
              "(to decide every page again with this run's switches: --overwrite, every page)")


def draft_digest(pages: Sequence[int], drafts: Path, drafts2: Path | None, *, legacy: bool = False) -> str:
    """Identity of the drafts a profile was measured on (its probe pages only)."""
    digest = hashlib.sha256()
    for page in pages:
        for directory in (drafts, drafts2):
            path = (directory / f"page-{page:04d}.txt") if directory else None
            digest.update(f"page-{page:04d}.txt".encode())
            digest.update(facade_attr("sha256_file", sha256_file)(path).encode() if path and path.is_file() else b"-")
            if not legacy:
                digest.update(b"\0semantics\0")
                digest.update(_draft_contract.semantic_digest(path.with_suffix(".json") if path else None).encode())
    return digest.hexdigest()


def profile_digest_matches(measured: str | None, pages: Sequence[int], drafts: Path, drafts2: Path | None) -> bool:
    if measured == draft_digest(pages, drafts, drafts2):
        return True
    requires = any(_draft_contract.requires_semantics(_draft_contract.read_record(
        directory / f"page-{page:04d}.json")) for page in pages for directory in (drafts, drafts2) if directory)
    return not requires and measured == draft_digest(pages, drafts, drafts2, legacy=True)


def profile_fingerprint(profile: Mapping[str, Any] | None) -> str:
    """Hash of what proofreading consumes from a profile, not of the file.

    Timestamps, usage and evidence change on every re-profile; sealing the
    file bytes would mark every page stale whenever the evidence was
    refreshed but the decisions were not.
    """
    if not profile:
        return ""
    engines = profile.get("engines") or {}
    notation = {k: v for k, v in (profile.get("notation") or {}).items() if k != "blocks"}
    return sha256_bytes(canonical_json_bytes({
        "schemaVersion": profile.get("schemaVersion"),
        "preferEngine": profile.get("preferEngine"),
        "decision": engines.get("decision"),
        "notation": notation,
        "layout": profile.get("layout"),
        "pairClasses": profile.get("pairClasses"),
    }))


# decision_provenance keys written only when a switch is on (absent = off).
# tieBreak, censusConfirmsEngineA, folioFurniture, topRungCaps,
# textColumnLayout, engineAConfirmsFirstAnswer and censusSkipOverCeiling are
# the keys of trials and switches that are gone (the last three replaced by
# the rule agreedMarks): no run writes them now, and a page sealed with one
# on is not current.
OPTIONAL_PROVENANCE = ("tieBreak", "censusConfirmsEngineA", "folioFurniture", "topRungCaps", "textColumnLayout",
                       "keepArabicNumerals", "loopDetector", "neighbourGuard")
# Switch --learned-heads (learned_heads_seal).
OPTIONAL_PROVENANCE += ("learnedHeads",)
OPTIONAL_PROVENANCE += ("engineAConfirmsFirstAnswer", "censusSkipOverCeiling")
# Switch --same-print-marks (agreed_marks, same_print).
OPTIONAL_PROVENANCE += ("samePrintMarks",)
# Switch --same-marks-any-form (agreed_marks, FORM_FOLD).
OPTIONAL_PROVENANCE += ("sameMarksAnyForm",)
OPTIONAL_PROVENANCE += ("unmarkedPageSample",)
# The round-4 fixes, on by default since the user's decision of 2026-09-30
# (after the round-4 benchmark), each with the provenance key it seals when
# on.  --no-<switch> turns one off.  Provenance records each one's state
# (present when on, absent when off), so a page sealed under the old
# defaults (all four off) is not current for a run with the new ones
# (validate_completion says which switches resume it), and a run that would
# decide the rest of such a book under the new ones does not start
# (round4_split): a book begun under the old defaults resumes with the four
# --no-* switches (its sealed pages stay current and the rest are decided
# the same way), or is proofread again, every page, with --overwrite; its
# sealed pages are never mixed with pages decided under the new defaults.
ROUND4_DEFAULTS = {"--keep-arabic-numerals": "keepArabicNumerals", "--loop-detector": "loopDetector",
                   "--neighbour-guard": "neighbourGuard", "--learned-heads": "learnedHeads"}
# Rules that are always on now, with the value a page sealed with each on
# carries: their switches went (2026-10-04) - the user's decided rules
# (D1-D5, D9, D15, D16, D20-D25, D27), the switches kept only for measuring,
# the chart step (its keys charts, chartVariants, chartReview, foldCut) and
# the proposed grid follow (gridFollow, never fired) - and their keys stay so
# that the pages sealed before are still current (validate_completion).
SEALED_ON = {
    "circleRule": "on", "bracketRule": "on", "doubledCensus": "on", "insertionCrop": "on",
    "furnitureInsertion": "on", "boundaryCrop": "on", "moveOnce": "on", "answerDeletionGuard": "on",
    "wholeRunCrop": "on", "foldedAlignment": "on", "lopsidedSplit": "on", "mergeLossGuard": "on",
    "cropCoverageCheck": "on", "answerNearby": "on", "leaderMarks": "on", "bulletMarks": "on", "markRule": "on",
    "charts": "on", "chartVariants": "on", "chartReview": "doubt", "rebuildLock": "both",
    "markAwareWitness": "on", "rebuildRegion": "on", "gridFollow": "on", "tableEdgeText": "on",
    "rebuiltOrientation": "on", "verdictPropagation": "on", "foldCut": "on",
}


# Recorded in every seal but never compared (validate_completion) and kept
# out of packet request IDs (packet_request_config): which transport, model
# and endpoint proofread a page, and the bytes of the helper code.  The
# user's decision of 2026-11-14: editing the scripts, switching between the
# agent's packets and a local model, or renaming the model or the endpoint
# does not itself make a sealed page stale or orphan a stored answer. The
# explicit punctuation mode is compared separately: transport auto defaults
# differ, so changing transport can change the decision mode.
INFORMATIONAL_PROVENANCE = ("proofreadTransport", "proofreadModel", "proofreadEndpoint",
                            "proofreadScriptSha256", "contentFidelitySha256", "contentNoteMarkersSha256",
                            "punctuationModeRequested", "punctuationFusionSha256")
# Keys decision_provenance gained after pages were first sealed without them,
# with the value their absence stands for: a page sealed before the key
# existed was decided that way.
PROVENANCE_ABSENT_MEANS = {"tableOrientation": "auto"}
# Rule keys the old skill added to its seals as its rules came on: the
# variant pairs from the lists alone, agreed marks, unread table characters
# and the rules always on now (SEALED_ON).  A page it sealed before one of
# them carries no such key and was decided without that rule.  The user's
# decision of 2026-11-14 accepts the old skill's seals as they are, with no
# proofreading again, so such a page is current; validate_completion says
# which keys it lacks (LEGACY_REASON) and the run report marks it legacy.
# A key that is present is compared as always.
LEGACY_ABSENT_KEYS = frozenset({"variantPairs", "agreedMarks", "unreadTableCharacters", *SEALED_ON})
LEGACY_REASON = "legacy seal, accepted as sealed; made before the rules "
# Switch --content-fidelity on (absent: off).  Compared one way only
# (validate_completion): a run with it on does not take a page sealed
# without its evidence (a page with the current v2 contract and no key, as
# the skill sealed every page before the switch, is judged by its evidence),
# a run with it off takes either.
CONTENT_FIDELITY_KEY = "contentFidelity"


def content_fidelity_on(arguments: Any) -> bool:
    """Switch --content-fidelity: the three whole-page image questions
    (inventory, content recovery, coverage review).  Off unless asked for."""
    return getattr(arguments, "content_fidelity", "off") == "on"


def punctuation_fusion_summary(passes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Completed passes only; success means no independent fallback/retry."""
    rows = [p["fusion"] for p in passes if p.get("fusion", {}).get("attempted")]
    return {"attempts": len(rows), "successes": sum(bool(r.get("success")) for r in rows),
            "fallbacks": sum(bool(r.get("fallbackReason")) for r in rows),
            "fallbackReasons": dict(Counter(r["fallbackReason"] for r in rows if r.get("fallbackReason")))}


def sum_fusion_summaries(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    summaries = [row.get("punctuationFusion") or {} for row in rows]
    return {**{key: sum(row.get(key, 0) for row in summaries)
               for key in ("attempts", "successes", "fallbacks")},
            "fallbackReasons": dict(sum((Counter(row.get("fallbackReasons") or {})
                                          for row in summaries), Counter()))}


def packet_request_config(arguments: Any) -> dict[str, str]:
    """The part of decision_provenance a packet request ID is bound to: what
    decides an answer, not the informational keys nor the fidelity switch
    (the question asked is the same either way)."""
    config = {key: value for key, value in decision_provenance(arguments).items()
              if key not in INFORMATIONAL_PROVENANCE and key != CONTENT_FIDELITY_KEY}
    # Packet's historical default was fusion. The exact same question keeps
    # its stored answer ID, whether requested as auto or explicitly fused.
    if punctuation_mode(arguments) == "fused":
        config.pop("punctuationMode", None)
    return config


def decision_provenance(arguments: Any) -> dict[str, str]:
    """What decided a page's characters, sealed so a resume under different
    decisions (another profile, new rulings, a switch set otherwise) is not
    mixed in.  INFORMATIONAL_PROVENANCE is recorded with them, not compared."""
    return {
        "proofreadTransport": getattr(arguments, "backend", "packet"),
        "punctuationMode": punctuation_mode(arguments),
        "punctuationModeRequested": getattr(arguments, "punctuation_mode", "auto"),
        "punctuationFusionSha256": facade_attr("sha256_file", sha256_file)(Path(_punctuation_fusion.__file__)),
        # The packet label, or the HTTP model the client resolved (main).
        "proofreadModel": getattr(arguments, "model", None)
                          or (DEFAULT_MODEL if getattr(arguments, "backend", "packet") == "packet" else ""),
        "proofreadEndpoint": (getattr(arguments, "endpoint", None) or "")
                             if getattr(arguments, "backend", "packet") == "http" else "",
        "proofreadScriptSha256": facade_attr("sha256_file", sha256_file)(PROOFREAD_SCRIPT_PATH),
        "contentFidelitySha256": facade_attr("sha256_file", sha256_file)(Path(_content_fidelity.__file__)),
        "contentNoteMarkersSha256": facade_attr("sha256_file", sha256_file)(Path(_note_markers.__file__)),
        "profileSha256": getattr(arguments, "profile_sha", "") or "",
        "enginePreference": getattr(arguments, "prefer_engine", "") or "",
        "fallbackEngine": getattr(arguments, "fallback_engine", "") or "",
        "variantRulingsSha256": getattr(arguments, "variant_rulings_sha", "") or "",
        # The book-independent lists that also decide characters: which pairs
        # are two forms of one character (variant_classes).
        "variantListSha256": (user_variant_rulings(), _LIST_SHA256.get("variantList", ""))[1],
        "allographsSha256": (opencc_allographs(), _LIST_SHA256.get("allographs", ""))[1],
        # Since 2026-10-13 only the lists say which pairs are forms of one
        # character, never the model (classify_pair), so a page sealed when
        # the model's answer could decide is not current.
        "variantPairs": "lists",
        **SEALED_ON,
        # Whether a rebuilt table may write up to UNREAD_TABLE_LIMIT characters
        # no engine read, each confirmed by 裁決 (headerCharacters, confirm_unread).
        "unreadTableCharacters": "off" if getattr(arguments, "no_unread_table_characters", False) else "on",
        # The glyphs the mark rule takes for marks come from the book's drafts
        # (mark_aliases), so a page sealed under other evidence is not current.
        "markAliasesSha256": (lambda found: sha256_bytes(canonical_json_bytes(found))
                              if any(found.values()) else "")(getattr(arguments, "mark_aliases", None) or {}),
        # The rule agreedMarks, always on (the user's ruling of 2026-10-04, no
        # switch): a block whose marks both engines read alike is written with
        # them, unasked (agreed_marks).  A page sealed before it is not
        # current.
        "agreedMarks": "on",
        # Switch --same-print-marks: the engines' marks also for a block whose
        # print both engines read alike (agreed_marks, same_print).  Only when on.
        **({"samePrintMarks": "on"} if getattr(arguments, "same_print_marks", False) else {}),
        # Switch --same-marks-any-form: the engines' marks also where the two
        # wrote one mark in two forms (agreed_marks with FORM_FOLD).  Only when on.
        **({"sameMarksAnyForm": "on"} if getattr(arguments, "same_marks_any_form", False) else {}),
        # Switch --unmarked-page-sample: on a page neither engine read marks
        # on, a sample of the blocks is asked and the rest are taken as
        # unmarked when the sample is (unmarked_candidates).  Only when on.
        **({"unmarkedPageSample": "on"} if getattr(arguments, "unmarked_page_sample", False) else {}),
        # Engine A's figures written where they are the only reading of their
        # print (--keep-arabic-numerals): the book's figures and the glyphs
        # engine B writes for them, from the drafts (figure_evidence), so a
        # page sealed under other evidence is not current.  Only when on.
        **({"keepArabicNumerals": figure_evidence_seal(arguments)}
           if getattr(arguments, "keep_arabic_numerals", False) else {}),
        # Switch --loop-detector: requests streamed and a rung given up in a
        # word-for-word loop of its reasoning, by this rule (_loop_watch).
        # Only when on.
        **({"loopDetector": _loop_watch.RULE.text()} if getattr(arguments, "loop_detector", False) else {}),
        # Switch --neighbour-guard: a 裁決 answer that copies the text beside
        # its spot is not written (neighbour_copy), within this window.  Only
        # when on.
        **({"neighbourGuard": f"window={NEIGHBOUR_WINDOW}"} if getattr(arguments, "neighbour_guard", False)
           else {}),
        # Switch --learned-heads: the running head learned by place, sealed by
        # what was learned (learned_heads_seal), so a page sealed under another
        # book's evidence or another profile's is not current.  Only when on.
        **({"learnedHeads": learned_heads_seal(arguments)} if getattr(arguments, "learned_heads", False) else {}),
        "tableOrientation": getattr(arguments, "table_orientation", "auto") or "auto",
        **({CONTENT_FIDELITY_KEY: "on"} if content_fidelity_on(arguments) else {}),
    }


@contextlib.contextmanager
def output_lock(directory: Path) -> Iterator[None]:
    """An exclusive lock on OUTPUT_DIR/.table-stitch.lock.

    The table stitch (stitch_tables.py) holds it from reading the sealed pages
    to writing its plan; a page's seal is written under it too.  Otherwise a
    page proofread again by another run sharing the directory could be sealed
    inside the stitch's window, and the stitch would write a plan built from
    the page before it over the new one: the new proofreading lost, and the
    merged table carrying the old page's cells.
    """
    with open(directory / ".table-stitch.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def seal_page(job: PageJob, markdown: bytes, record: Mapping[str, Any],
              *, events: _packet_events.PacketEvents | None = None) -> None:
    """Write a page's Markdown and its seal, under the output directory's lock."""
    with output_lock(job.out_json.parent):
        job.out_md.write_bytes(markdown)
        job.out_json.write_bytes(canonical_json_bytes(record))
    if events is not None:
        events.record("seal", artifact="page", scan_page=job.scan_page)


def missing_engine_doubt(job: PageJob) -> dict[str, str]:
    """The doubt on a page of a two-engine run sealed from one engine's draft
    because the other's is missing (job.missing_engine)."""
    if job.missing_engine == "B":
        detail = (f"ENGINE B DRAFT MISSING: this page has no {ENGINE_B_NAME} draft; it is transcribed from "
                  f"engine A's ({ENGINE_A_NAME}) draft alone, with no second reading to check it against - "
                  "check it against the image, or OCR it with engine B and proofread it again (--overwrite)")
    else:
        detail = (f"ENGINE A DRAFT MISSING: this page has no {ENGINE_A_NAME} draft; it is transcribed from "
                  f"engine B's ({ENGINE_B_NAME}) draft alone, without engine A's layout and with no second "
                  "reading to check it against - check it against the image, or OCR it with engine A and "
                  "proofread it again (--overwrite)")
    return {"kind": MISSING_ENGINE_DOUBT, "detail": detail}
