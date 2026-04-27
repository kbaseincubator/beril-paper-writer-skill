# beril-paper-writer-skill — v0.2.0 release notes

**Release date:** 2026-04-27
**Status:** v0.2 — discipline-hardening + auto-repair tier. Pre-1.0;
expect breaking changes between minor versions until the architectural
shape stabilizes.

This document is the authoritative release-handoff for v0.2. It lists
what's new since v0.1.0 (`88eac762`), what's deferred to v0.3, and the
specific user-visible behavior changes. The v0.1.0 ship notes at
`RELEASE_NOTES.md` remain authoritative for the foundational features.

---

## What v0.2 adds since v0.1.0

Three architectural tiers landed: **Tier 1** (cross-walk post-processors),
**Tier 3** (REPAIR_MODE auto-repair + review-rewrite loop), **Tier 5**
(defensive features). The unifying thesis: prompt-level discipline is
not enough — back every cross-walk requirement with a programmatic
post-processor, and back every "user fixes by hand" path with an
orchestrator-side auto-repair loop.

### Tier 1 — Cross-walk post-processors (advisory; no live-LLM cost)

Two new post-processors join `check_throughline_glyphs.py` (which shipped
in v0.1.0). Same architectural pattern: standalone Python tool invoked
from the orchestrator, parses a draft artifact, applies a cross-walk
check, emits stderr `WARN` lines, **always exits 0**. The orchestrator
surfaces warnings via `next_actions.md`.

- **`tools/check_scope_coherence.py`** — Discussion ↔ Results scope
  cross-walk. Walks `03_discussion.md` for sentence-level claims,
  cross-references numerical anchors against `02_results.md`, and
  cross-walks against `00_throughline.md`'s "Would NOT include" bullet
  list. Three FP-mitigation moves: literature-attribution skip
  (citations precede the number → out of scope), distinctive-keyword
  filtering on the Would-NOT-include cross-walk, top-8 keywords with
  3-hit threshold.
- **`tools/check_overclaim.py`** — Abstract / Discussion strong-claim
  vs ⚠ partial / ✗ contradicts evidence-map cross-walk. Walks
  `05_abstract.md` and `03_discussion.md` for sentences containing
  strong-claim verbs (validates, demonstrates, yields, establishes,
  proves, confirms, shows that, finds that, …); fuzzy-matches each
  against guarded sub-claim keywords from the throughline's evidence
  map. ≥2-distinctive-overlap gate eliminates cross-claim FPs.
  Marks `[caveat-acknowledged]` when the sentence inline contains
  the caveat's vocabulary or numerical anchors.

Wired in `paper_writer.sh` between `phase_finalize_citations` and
`phase_assemble`. Smoke against `functional_dark_matter` `draft_1`
produced 4 + 4 = 8 advisory WARNs, all real-signal except 1
documented borderline FP.

### Tier 3 — REPAIR_MODE harness + review-rewrite loop (~$6.36 live retest)

The biggest architectural piece. Converts validator failures + reviewer
critical issues from "user fixes by hand" to "writer addresses + re-
validates" via a bounded retry loop.

- **`phase_repair_validators` in paper_writer.sh** — runs after
  `phase_assemble`. Reads `audit/validation.json`, dispatches each
  failed validator (M2-M10) to the section prompt that owns repairs
  per the LAYOUT.md:419 dispatch table, in REPAIR_MODE with the four
  documented inputs (`REPAIR_MODE`, `NAMED_VALIDATOR`,
  `VALIDATOR_OUTPUT_PATH`, `REPAIR_TARGET_PATH`). Bounded retry: 2
  dispatches per validator; M1 (missing IMRAD section) and M4 (data-
  availability template) escalate per LAYOUT.md, not REPAIR_MODE.
  M10 has location-based tie-breaker (Results-only orphan → results.v1;
  Introduction-only → intro.v1; otherwise discussion.v1).
- **`phase_review_rewrite` in paper_writer.sh** — runs after
  `phase_review`. Parses the latest review.md for Critical+Important
  findings (pass 1) or Critical-only (pass 2), groups by primary
  section, dispatches `rewrite.v1.md` per section with `FINDING_IDS`
  JSON array, re-assembles + re-validates after each pass, runs
  reviewer pass 2 (and 3 if needed), respects SPEC §8.3 hard cap of
  2 rewrite passes.
- **`tools/check_repair_scope.py`** — fourth cross-walk post-processor.
  Three checks per repair attempt: pre/post byte-identical (Write
  no-op detection), bounded diff via `difflib.SequenceMatcher` (≥30%
  WARN, ≥60% strong WARN, calibrated against M9 stub-Limitations
  expansion semantics), validator regression (any `pre.pass → post.fail`
  flip across the repair invocation).
- **5 new helper subcommands in `paper_writer_helpers.py`**:
  `prepare-repair`, `check-repair-status`, `list-failed-validators`,
  `parse-review`, `count-review-criticals`.
- **`emit-next-actions` extended** with two new sections in
  `next_actions.md`: `## REPAIR_MODE outcomes` (escalations + repaired
  + invocation failures, prioritized for review) and
  `## Review-rewrite outcomes` (per-pass dispatch + remaining criticals
  + hard-cap residuals).

**No section-prompt edits required** — audit confirmed all 5 prompts
(methods, results, discussion, intro, abstract) already had REPAIR_MODE
behavior sections + closing-message contracts documented from v0.1.

### Tier 5 — Defensive features

- **`tools/paper_writer_helpers.py extract-data-availability`** — replaces
  v0.1's `[TBD]` markers in `07_data_availability.md` with real values.
  Three filled blocks: K-BERDL databases (extracted from
  `methods_provenance.md` SQL via regex on `FROM <db>.<table>` patterns
  — deterministic), public accessions (named sources via curated
  pattern set covering Fitness Browser, GTDB, NMDC, AlphaEarth,
  GapMind, eggNOG, Bakta, STRING, PubMed, PaperBLAST + typed
  accessions: BioProject, GEO, SRA, GenBank, DOI, PMID), restricted
  access (defensive default). Falls back to `[TBD]` markers if both
  extractors return nothing. Smoke on `functional_dark_matter`:
  3 K-BERDL databases (`kbase_ke_pangenome`, `kescience_fitnessbrowser`,
  `nmdc_arkin`) with full table lists + 9 named sources + 17 PMIDs.
- **`--max-cost-usd N` CLI flag** — cost circuit breaker.
  `paper_writer.sh` now accepts the flag (env var: not yet — TBD if
  needed); `invoke_claude_with_retry` checks cumulative
  `audit/*.metadata.json` `estimated_cost_usd` before each LLM call.
  If cumulative exceeds the cap, halts via `halt_with` with a
  structured handoff offering "re-run with higher cap or accept
  partial draft." New helper `paper_writer_helpers.py cumulative-cost`
  exposes the cumulative for diagnostics.

### Bug fix — review-finding parser tolerance

The 2026-04-27 Tier-3 retest exposed that `fallback_reviewer.v1`
emits at least three different finding-header shapes — Form A in
review_1, Forms B and C in review_2:

- **Form A:** `**C1: Abstract line 18 — "..."**`
- **Form B:** `- **C1: Abstract functional hypotheses claim (line 20)** — "..."`
- **Form C:** `- **C3: "Multi-dimensional ..." (Abstract line 18, line 20; Discussion line 176)** — ...`

`paper_writer_helpers.py _parse_review_findings` previously only
recognized Form A, silently returning 0 of 3 Criticals on review_2.md
and terminating the rewrite loop one cycle early. Fix: full-header-
line matching + section-name scan over header + next 6 body lines
(header-first, body-fallback). Tested against both shipped review
files; no regression on Form A; 3/3 on Forms B+C.

Tightening the prompt itself to mandate one canonical header format
is owed as a v0.3 follow-up (deferred to TODO #21).

### State schema — unchanged from v0.1

`STATE_SCHEMA_VERSION = "0.1"` unchanged in v0.2. Existing draft
directories continue to work without migration. The v0.1.0 release
notes' forward-looking comment ("v0.2 will bump STATE_SCHEMA_VERSION
and ship a migration tool") was incorrect — v0.2's changes added new
audit files but did not modify `state.json` structure. Schema bump +
migration tool deferred to whichever future tier modifies `state.json`
(likely Item 4.3 throughline re-evaluation persistence).

---

## What v0.2 deliberately does NOT ship (deferred to v0.3)

| Feature | Why deferred | Workaround in v0.2 |
|---|---|---|
| **Figures + docx assembly (Tier 2)** | Highest-priority v0.3 work. `commands/assemble.py` is still a stub; `tools/assemble_docx.py` not yet implemented; `(Fig. N)` callouts in `results.v1` are still advisory | `manuscript.md` is the v0.2 deliverable; figures available at `<draft_dir>/figures/`; insert manually |
| **Conceptual diagrams via mermaid (Tier 7)** | New scope per 2026-04-27 conversation. Hard dependency on Tier 2's docx assembler. Mermaid-in-markdown approach selected; render pipeline (Kroki vs mermaid-cli) is a v0.3 decision | None — diagrams are user-supplied for v0.2 |
| **Card elicitation pre-drafting checkpoint (4.1)** | Adam-flagged "critical stage later"; deferred until augmentation stream has a 2nd user | Database-specific Methods phrasing relies on pitfalls.md / runtime REST discovery (already in BERIL) |
| **Citation-pool exhaustion user pause (4.2)** | Lower priority while single-user; pump-through with scope-down default works for the operator | discussion.v1 reframes claims that hit `[NEEDS CITATION]` |
| **Throughline re-evaluation prompt (4.3)** | Drift detection wired (state.py:diff_artifacts); LLM-driven re-evaluation prompt deferred. Implicates state-schema bump | Manual user review if source artifacts changed mid-draft |
| **State schema migration tool (5.3)** | Conditional on STATE_SCHEMA_VERSION bump. v0.2 doesn't bump | N/A — schema unchanged |
| **fallback_reviewer.v1 prompt tightening** | Parser is now tolerant of all 3 observed forms. Tightening the prompt to mandate one canonical form is the second half of the discipline lesson; TODO #21 in next-cycle backlog | None needed; parser handles drift |
| **Auto-generated illustrative images** | Declined permanently 2026-04-27. Image-gen API → scientific manuscript pipelines have known correctness problems | User brings illustrations via BioRender / Inkscape if needed |

---

## Live retest results (2026-04-27)

Targeted retest on existing `draft_1` exercising Tier 3's new code
paths against the known C1-residual review:

| Metric | Value |
|---|---|
| Total cost | **$6.36** across 14 LLM calls |
| Wall clock | ~17 min |
| Stochastic Write retries | 2 (abstract, intro) — recovered cleanly via existing 3-attempt retry |
| `phase_repair_validators` | No-op as expected (0 validator failures pre-run) |
| `phase_review_rewrite` pass 1 | Dispatched 4 sections (abstract C1+I3, results I1+I2+I4, intro I5, discussion I6) — all wrote successfully |
| Abstract diff | Scoped fix applied for C1 (NMDC compositional-coupling caveat added inline, mechanism-disclaimer softened) — exactly the suggested-fix shape from review_1 |
| Critical-finding count over loop | review_1: 1 → review_2: 3 (3 are issues review_1 missed, NOT regressions; the original C1 cleared) |
| Loop termination | **One cycle short** due to parser-bug — fixed post-retest. Pass 2 dispatch path is wired and synthetically verified but not live-exercised in v0.2 |

**Live coverage gap.** The full v0.2 pipeline (Tier 1 post-processors
inside a real `phase_assemble`, Tier 5 data-availability extraction
inside a real `phase_data_avail`, cost circuit breaker tripping during
a real run) is not exercised live in v0.2. The orchestrator bash glue
is small and `bash -n` clean; the helpers are smoke-tested end-to-end
via direct invocation. **First fresh-draft run on v0.2 should be
treated as a final live-test gate.** If anomalies surface, the v0.1.0
fallback path is a one-line revert (use the v0.1.0 tag).

---

## Architecture deltas since v0.1.0

The post-processor pattern grew from one to four:

```
v0.1.0: tools/check_throughline_glyphs.py            (plan.v1 strength glyphs)
v0.2.0: + tools/check_scope_coherence.py             (Discussion ↔ Results scope)
        + tools/check_overclaim.py                   (strong verbs vs ⚠ sub-claims)
        + tools/check_repair_scope.py                (REPAIR_MODE post-check)
```

Three architectural moves emerged from the v0.2 cycle that should
generalize to future skills:

1. **Python-helpers-emit-bash-eval-able-output, but no `eval`.** New
   helper subcommands (`prepare-repair`, `parse-review`) print
   `KEY=value` lines for bash to parse with raw `${line%%=*}` /
   `${line#*=}` rather than `eval` (which would expand backticks /
   `$`-vars in the values). For richer structures, helpers print JSON
   to stdout and bash invokes inline `python3 -c` to extract.
2. **Pre-snapshot for post-checker.** `phase_repair_validators` copies
   the section file to `audit/repair_<VID>_pre.md` before dispatch;
   `check_repair_scope.py` diffs pre vs post. Pattern available for
   any prompt that writes back to a known target file.
3. **Bounded loops with structured escalation.** Both Tier-3 phases
   write per-cycle outcome lines to dedicated `audit/{repair,rewrite}_summary.txt`
   files. `emit-next-actions` splits by category (escalations / repaired
   / loop trace) with prioritized display order. SPEC §8.3 hard cap is
   enforced bash-side; rewrite-loop residuals get an explicit
   "hard cap reached" line that surfaces as **unresolved** in
   next_actions.md.

---

## Migration notes — v0.1.0 → v0.2.0

**No state migration required.** Existing draft directories work
unchanged. New audit files (`audit/repair_summary.txt`,
`audit/rewrite_summary.txt`, `audit/scope_warnings.txt`,
`audit/overclaim_warnings.txt`, `audit/data_availability_extraction.json`)
are produced by v0.2 phases on new runs; existing drafts will not
have them and `emit-next-actions` handles the missing-file case
defensively.

**Re-running an existing v0.1 draft via `beril-paper-writer continue`**
will:
- Re-enter the resume case for the draft's current `state.json` phase
- Skip phases whose output already exists (idempotency)
- Run new v0.2 phases against existing artifacts (e.g.,
  `phase_check_scope_coherence` would run on existing section files
  and produce `audit/scope_warnings.txt`; `phase_repair_validators`
  would no-op if `audit/validation.json` shows all pass)

**`07_data_availability.md` regeneration.** Existing drafts have the
v0.1 [TBD] template fill. v0.2's improved extraction does NOT
regenerate the file (idempotent skip). To get the v0.2 extraction on
an existing draft, delete `07_data_availability.md` and re-run; the
phase will produce the new content.

---

## Known issues / caveats

- **Live coverage gap (above)** — the full v0.2 pipeline hasn't been
  exercised end-to-end live. Treat the first v0.2 fresh-draft run as
  a smoke-gate.
- **fallback_reviewer.v1 prompt drift** — the reviewer emits multiple
  finding-header formats. v0.2 fixes the parser; v0.3 will tighten
  the prompt.
- **Tier 1 post-checker borderline FP** — `check_scope_coherence`'s
  Would-NOT-include cross-walk on broad-summary sentences will
  occasionally flag the integrative summary that names protected
  layers. Documented in the file's docstring; FP cost acceptable in
  the advisory contract.
- **Tier 7 (diagrams) not yet started** — manuscripts continue to
  ship without conceptual diagrams. User adds them post-draft if
  needed. v0.3 work.

---

## Backlog leftover for v0.3+

In rough priority order:

1. **Tier 2 — figures + docx assembly.** Highest-priority v0.3 work.
   ~1.5 wk + ~$5 smoke per the v0.2 punch list.
2. **Tier 7 — conceptual diagrams via mermaid-in-markdown.** Hard dep
   on Tier 2.3 (`assemble_docx.py`). ~2-3 wk + ~$10-15 smoke. Design
   notes captured in `smoke-test/v0_2_punch_list.md` post-v0.2 section.
3. **Follow-up #21 — tighten `fallback_reviewer.v1.md`** to mandate one
   canonical finding-header format with anti-example pairs.
4. **Tier 4 — interactive checkpoints.** Deferred until augmentation
   stream has a 2nd user.
5. **Item 4.3 / 5.3 — throughline re-eval + state-schema migration.**
   Bundled together when 4.3 lands (since it's what triggers the
   schema bump).

---

*Release notes authored 2026-04-27 alongside the v0.2 ship cycle.
Companion to `RELEASE_NOTES.md` (v0.1.0). For per-tier scope and
acceptance criteria, see `smoke-test/v0_2_punch_list.md`.*
