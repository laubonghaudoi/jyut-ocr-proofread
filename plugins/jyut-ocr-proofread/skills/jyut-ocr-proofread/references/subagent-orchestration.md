# Subagent orchestration contract

Use this contract when one historical Chinese PDF is split among multiple LLM
agents. It supplements the normal proofreading rules; it does not lower the
requirement to treat the PDF image as the text authority.

The invoking session's agent model performs intelligent proofreading and final
content review. Writers and auditors follow that session's effective model
policy, subject to explicit user/platform overrides; the skill pins no model ID.
Choosing a local OCR draft backend never selects a separate proofreading model
or requires a local Qwen/NInfer service.

## Two separate kinds of parallelism

- LLM subagents transcribe, proofread, or audit bounded scan ranges. Only the
  main agent may create and coordinate them. A child agent must not create
  another agent.
- Apple Vision OCR uses a separate bounded local process pool, not LLM agents. It
  has no model or reasoning setting, and its draft output never becomes text
  authority. Do not spend LLM-agent slots merely to wrap identical Vision
  processes.
- On Ubuntu/Linux, the main agent runs HunyuanOCR for drafts and optionally
  PaddleOCR-VL for selected scans. These are GPU models: on a single GPU, run
  one OCR backend at a time, with one persistent server/worker. Do not apply
  the Vision process-pool limit to them, and do not let children start OCR
  backends or stop shared inference services. See `local-ocr.md`.
- Keep concurrently active LLM children at or below
  `min(runtime child slots, 8)`. A smaller runtime limit always wins. Do not
  queue overlapping writers merely to fill the available slots.

## Assignment contract

The main agent owns range partitioning and final assembly. Before a child
starts, give it one complete assignment containing:

1. a unique assignment ID and exactly one role:
   `jyut-ocr-segment-writer` or `jyut-ocr-visual-auditor`;
2. the exact source PDF path, its SHA-256, and the in-scope PDF scan count;
3. an inclusive owned scan range or explicit scan set, plus the adjacent
   boundary-context scans that may be read but not written;
4. an exact mapping from every assigned scan to its individual
   original/full-resolution render, current render SHA-256, temporary page
   marker, and printed-page label when one exists;
5. the writer's single segment output path, or for an auditor, the candidate
   path and sealed candidate SHA-256 to inspect;
6. the selected `basic`, `structured`, or `release` profile; local repo rules;
   notation policy; known corrections; and risk flags such as tables, rotated
   spreads, duplicates, missing pages, faint text, footnotes, running furniture,
   or uncertain boundaries; and
7. the required report fields and any project-local manifests or evidence paths
   the child must read.

Do not start an assignment with a missing field, a source/render hash mismatch,
an output path owned by another writer, or an owned scan that overlaps another
active writer. Report the conflict to the main agent instead.

## Segment writer lifecycle

The writer owns only its assigned segment and scan range.

1. Verify the source and render hashes before reading OCR drafts or changing
   text. Treat HunyuanOCR, PaddleOCR-VL, Apple Vision, GJ.cool, Gemini, and other
   OCR as hints only. Missing optional-engine pages are not blank readings.
2. Read boundary-context scans for joins, headings, lists, tables, and footnotes,
   but do not transcribe them into a second segment or modify their owner's
   files.
3. Write only the assigned segment output. Never edit the final assembled
   Markdown, another segment, a page/render manifest, or release evidence owned
   by the main agent.
4. Check every owned scan against its individual original/full-resolution
   render. Preserve temporary page markers and report any unresolved character,
   missing/duplicate scan, or boundary decision.
5. Finish the segment, compute SHA-256 from its exact bytes, and return a
   candidate seal containing assignment ID, path, SHA-256, owned scan set,
   marker coverage, unresolved findings, and boundary notes.
6. Enter **STOP-WRITE** immediately after returning the seal. Do not make even a
   small cleanup edit, respond to a review finding with a patch, or refresh the
   hash unless the main agent explicitly opens a new amendment assignment.

When drafts are available, retain their file identities and pass substantive
draft-to-candidate text differences to the existing audit as review priorities.
Do not replace full-scan review with a diff-only pass. An ambiguous alignment is
a block-level risk, not an exact character locator. Keep unresolved readings
separate from actionable fixes, with enough context to distinguish repeated
occurrences; a group label alone does not establish which occurrences were read.

Any byte change after sealing invalidates that candidate and every audit based
on it. The main agent must designate one writer, produce a new SHA-256 seal, and
send the affected scans and boundary context to a different auditor again.

## PacketClient wave bundle lifecycle (dual-OCR scripted pipeline)

When dual-OCR alignment and crop preparation run through `book_profile.py` and
`proofread_pages.py --backend packet`, deterministic Python settles agreed text
first and emits compact work bundles (`packets/wave-NN/bundle-XX.json` plus PNG
crops under `packets/crops/`).

1. The main agent dispatches at most `min(runtime child slots, 8)` concurrent
   workers (`jyut-ocr-segment-writer` in Codex/Claude Code, or `invoke_subagent`
   inheriting the session model in Antigravity), assigning each worker one
   mutually exclusive input `bundle-XX.json` (with its SHA-256) and one output
   `packets/wave-NN/answers-XX.json`.
2. Each worker inspects the exact PNG crop (or full-page render for `format`
   items) referenced by each request in its bundle:
   - `adjudicate`: read the highlighted glyph spot directly from the crop and
     follow the prompt's answer format (`字: ...`).
   - `table-rebuild`: rebuild the table from the crop using every `required`
     character and only `allowed` characters unless genuinely printed outside
     both drafts.
   - `census+punctuate`: count printed punctuation marks on the upscaled block
     tile (`census`) and insert those exact marks into the locked character
     span (`punctuated`) without adding, deleting, or swapping any character.
   - `orient-table` / `cell-direction` / `notation`: answer the exact structural
     or notation question from the supplied crop/grid.
   - `format`: add Markdown structure (headings, paragraphs, lists, tables) and
     remove margin running heads/folios while keeping every locked character,
     locked punctuation mark, and `⟦Tn⟧` table placeholder intact.
3. Each worker writes only its assigned `answers-XX.json`, computes its SHA-256,
   reports the answered `request_id` count, and enters **STOP-WRITE**
   immediately. Workers never touch `packets/answers.jsonl` or `page-NNNN.md`.
4. Only the main agent runs `apply_packet_answers.py` to validate and append
   worker bundles into `packets/answers.jsonl`, then re-runs
   `proofread_pages.py --backend packet` to execute deterministic guards
   (`verify_characters`, `restore_merged_characters`, `check_rebuilt_table`,
   `punctuation_pass_ok`, `deleted_runs`) and advance to the next wave or seal
   `page-NNNN.md` + `page-NNNN.json`.

## Independent visual auditor lifecycle

The auditor must be a different agent from the writer and is read-only. It must
not patch the candidate, use shell redirection to change it, or generate a
replacement segment.

Before spawning it, the main agent must confirm that the platform's effective
permission/sandbox mode is still read-only. A live parent `yolo`, bypass, or
other broader runtime override can supersede an adapter default; an agent
started under such an override does not qualify as the read-only auditor in
this contract. Start the audit in an effective read-only/plan turn instead.

1. Recompute the candidate SHA-256 and reject drift from the writer's seal.
2. Verify the source and render hashes, then inspect 100% of the assigned scans
   using the individual original/full-resolution renders. Contact sheets,
   thumbnails, and OCR output are navigation aids only.
3. Inspect the supplied boundary-context scans as context, without claiming
   ownership or duplicating their coverage.
4. Check faithful text, original glyphs and punctuation, page furniture,
   marker coverage and order, cross-page joins, headings, lists, tables,
   footnotes, and every assignment-specific risk flag.
5. Return the sealed SHA-256, exact checked scan set, exact context scan set,
   per-scan evidence disposition, boundary conclusions, and an explicit list of
   open findings. An audit passes only with 100% assigned-scan coverage and zero
   open findings.

Resolve supplied uncertainties at their stated locations. A statement that the
page was checked does not individually resolve them, and checking one glyph
does not resolve every glyph in its surrounding phrase. A retained best reading
with indistinct source strokes stays an explicit source limitation; report it
separately from the count of actionable fixes. See `human-review-feedback.md`
when handing those items to a human or reconciling later human decisions.

If the candidate needs a patch, report the finding and stop. A read-only audit
never converts itself into a writer assignment.

## Main-agent closure

Before assembly, the main agent must verify that:

- writer-owned scan sets are mutually exclusive and together cover the intended
  scope;
- every current segment byte-matches its writer seal;
- every segment has a passing audit from a different agent, with 100% owned
  scans plus the required boundary context accounted for;
- no writer remains active or may still modify a sealed segment; and
- cross-segment boundaries, marker order, structure, and unresolved findings
  are reconciled.

Only the main agent assembles the final Markdown. After assembly it runs the
applicable deterministic and visual closure. A late patch invalidates the
affected seal and audit and returns that range to the amendment lifecycle; it
must not be applied silently to an already accepted candidate.
