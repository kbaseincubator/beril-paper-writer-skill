# beril-paper-writer-skill — release notes

**Current:** v1.2.0 (2026-06-07).

This file is the **cumulative current-version log** — where the skill
is, what shipped, and pointers to per-version detail. Per-version
release notes for the v0.x line live in
[`release-notes/`](release-notes/).

## v1.2.0 — Cycle 2: pre-handoff deliverable validation + DP9b-analogue fix (2026-06-07)

**Minor release.** Second CRAFT hardening-cycle deliverable —
extends Cycle 1 (presentation-maker v1.2.0) to paper-writer. Adds
a deterministic Tier-1 gate at the pre-handoff point that catches
the silent omissions/defects v1.1.x had to fix manually after they
shipped — without discarding the deliverable or forcing an
expensive re-run. Companion CRAFT v0.3.3 re-pin delivers it.

**Theory tested:** *a deterministic pre-handoff gate catches the
silent omissions/defects that today only the human or adversarial
review catches.* Test: regression fixtures per gate (drawn from the
paper-writer caulobacter draft_2 + Cycle-1 lessons) all fire as
expected; the clean fixture passes (modulo M-series advisories that
are accurate-and-expected on a synthetic minimal manuscript).

### New gates — `validate_deliverable` (4 checks)

Runs at the pre-handoff point in `orchestrator.run_pipeline`, right
after `phase_assemble` and just before "Pipeline complete. Paper
assembled." Each gate maps to a real defect class:

  1. **G1 section_completeness** — WRAPS
     `validate_manuscript.run_all_validators` (M1–M10) and projects
     each Violation onto the deliverable-validation.v1 contract.
     `escalation_path` projects to remediation `kind`:
     `auto-fix → auto + rerun_validate`, `escalate → targeted`,
     `user-modify → targeted`, `accept-as-limitation → advisory`.
     **Does NOT re-implement** M1–M10; the section logic stays in
     validate_manuscript. (Decision 1: complement, not absorb.)

     *Followup (Cowork verification, 2026-06-07): the raw M1-M10
     projection over-fired on real well-formed manuscripts.* M1
     reports "missing required paper-mode section: 'title'" P0 on
     every H1-titled paper (because it looks for a literal `## Title`
     section); M2 (structured-abstract-subsections) + M9
     (limitations) report P0 on prose-handled papers. Calibrated
     projection: (a) M1 "missing title" is **suppressed** when
     manuscript.md has a leading H1 (the H1 + author block IS the
     title page); (b) M2 + M9 errors are **demoted from P0/auto to
     P1/advisory** (these are publishing-house style preferences,
     not deliverable completeness blocks — many real papers ship
     prose abstracts and in-discussion limitations). M3 ai_disclosure
     + M4 data_availability **stay P0/auto** (those are genuine
     ICMJE blocks). A well-formed manuscript with H1 + author block
     + prose abstract + in-prose limitations + AI Disclosure +
     Data Availability now passes G1 with NO spurious P0 (regression
     fixture pinned: `test_g1_well_formed_h1_titled_paper_passes_no_p0`).
  2. **G2 placeholder_or_leaked_template** — no placeholder tokens
     (TBD, TK, TO BE COMPLETED, TO BE FILLED, FILL IN, PLACEHOLDER,
     XXX, `[...]`, empty `[]`) in title / author / affiliation /
     body. Title + author line present (P0/blank → targeted; P1
     missing-label → targeted). Affiliation placeholder also P0.
     Dirname-leak in title (P1 + TARGETED — operator rewrites;
     **never auto-strip**, per the Cycle-1 G1 lesson). The narrowed
     detector fires only on (a) verbatim full slug or (b) ≥2
     ADJACENT dir-segments — lone "Caulobacter"/"Loss" **must NOT**
     fire (regression fixture pinned).

     *Followup (Cowork verification, 2026-06-07): the original
     `_TBD_RE` only matched `\\bTBD\\b`.* Real draft_2 shipped with
     `**Authors:** [AUTHOR LIST TO BE COMPLETED]` and
     `**Affiliations:** [TO BE COMPLETED]` — both slid through
     unflagged. Broadened the vocab to the in-the-wild template set
     above (alphabetic + bracketed-ellipsis + empty-bracketed).
     Added the previously-missing **affiliation check**
     (`g2:affiliations_tbd`, P0 when the `Affiliations:` label is
     present with a placeholder/empty value). Author-label parser
     now correctly strips bold markdown markers around the colon
     (`**Authors:** value` extracts to `value`, not `** value`).
     Body-wide finding renamed `g2:tbd_in_body → g2:placeholder_in_body`
     to reflect the broadened vocab. Regression fixtures pinned for
     each case, including the two literal draft_2 strings.
  3. **G3 figure_resolution_and_embedding** — every block-image
     reference in manuscript.md resolves on disk via the same
     lookup `assemble_docx.render_image` uses (draft_dir/figures
     first, then project_dir/figures); when manuscript.docx is
     present, every resolvable reference must have a matching
     embedded picture.
  4. **G4 mode_depth_vs_user_intent** — the DP9b-analogue. Compares
     the persisted user intent (`audit/user_intent.json`) to
     `state.json` mode and (if present)
     `audit/validate_manuscript.json` mode. Catches the silent
     `--mode` drop class (see DP9b fix below).

Findings emit to `audit/deliverable_validation.json` under the
**same `deliverable-validation.v1` schema** as
beril-presentation-maker (Decision: schema reused VERBATIM — the
cross-skill finding contract). Projectable fields (`gate`,
`severity`, `remediation.kind`) are tokens drawn from frozen
vocabularies — telemetry-ready for the later run-record contract
(Cycle 2+) without a rewrite.

### Never-discard remediation policy

A blocking finding does NOT delete the deliverable or force a full
re-run. Each finding carries a `remediation` block keyed to cost:

- **auto** (deterministic, manuscript-read-only) —
  `finalize_deliverable.py` runs handlers + re-validates. Two
  handlers:
    - `rerun_validate` — re-run validate_manuscript with the
      resolved mode; write `audit/validate_manuscript.json`.
      Pure read on manuscript.md.
    - `reassemble` — re-run `assemble_docx.main` against the
      current manuscript.md. No LLM.
  **`strip_dirname_token` is intentionally absent** — fuzzy
  auto-mutation of titles is the Cycle-1 G1 lesson (it deleted
  organism names on real Caulobacter titles).
- **targeted** (cheap, one-stage) — emit the exact
  `beril-paper-writer continue …` command; operator runs it.
- **advisory** — surfaced, never blocks.

The deliverable is ALWAYS produced; readiness is what the validator
reports; nothing expensive is recomputed.

### DP9b-analogue fix — `--mode` / `--depth` plumbing

Two silent drops fixed:

1. **`draft.py`** parsed `--mode` and `--depth` but the
   `PaperWriterOrchestrator` constructor had no parameters for
   them — they were silently dropped. Now: forwarded into
   `Orchestrator(..., mode=…, depth=…)`, which stores them on
   `state.mode` (mode) and writes `audit/user_intent.json`
   (mode + the explicit-sentinel that says "user picked this vs.
   inheriting a default").
2. **`continue_run.py`** had no `--mode`/`--depth` flags at all;
   only `--model` was forwarded. Now: same flags as `draft`,
   plumbed the same way. The idempotent user_intent merge
   ensures process-1's explicit pick wins over process-2's
   defaults (and a deliberate explicit mode-flip on resume
   surfaces as a G4 mismatch finding).

### `user_intent.py` — COPIED VERBATIM from presentation-maker v1.2.0

Per the CRAFT `llm_config` copy-not-share convention (Decision 2):
the persistence layer is copied byte-identical to the
beril-presentation-maker v1.2.0 source (commit `7b0baed`, blob
`0ae779ed…`). The new test `test_user_intent_byte_identical_
to_presentation_maker` asserts the byte identity locally; the
craft-platform conformance fixture will assert it cross-repo (to
be extended in the CRAFT v0.3.3 re-pin commit).

Vocabulary note: user_intent's `tier` slot uses presentation-
maker's STRONG/THIN/EXPLORATORY vocabulary — which does NOT match
paper-writer's `--depth` (quick/standard/deep). So depth is NOT
persisted via user_intent; it's threaded into the orchestrator and
into state-related artifacts only. A future depth-vs-output check
is deferred to a later cycle.

### New files

- `src/beril_paper_writer/skill/tools/user_intent.py` — COPIED
  VERBATIM from beril-presentation-maker v1.2.0.
- `src/beril_paper_writer/skill/tools/validate_deliverable.py` —
  the 4-gate pure read-only check + `deliverable-validation.v1`
  emitter + `check` CLI.
- `src/beril_paper_writer/skill/tools/finalize_deliverable.py` —
  the auto-remediation + re-validation + targeted-route emitter
  + `finalize` CLI.
- `tests/unit/test_validate_deliverable.py` — 27 tests: clean
  fixture, per-gate regressions (including the Cycle-1 correct-
  Caulobacter-title regression case), DP9b-analogue mode-drop,
  schema-shape, finalize-handler-removal pins, byte-identity
  check.

### Modified files

- `src/beril_paper_writer/orchestrator.py` — `__init__` accepts
  `mode` + `depth`; `_initialize_state` writes
  `audit/user_intent.json` via the copied user_intent module +
  applies explicit mode to `state.mode` on first init;
  `run_pipeline` calls `_run_deliverable_validation` after
  `phase_assemble`.
- `src/beril_paper_writer/commands/draft.py` — forwards
  `args.mode` + `args.depth` to the orchestrator constructor.
- `src/beril_paper_writer/commands/continue_run.py` — adds
  `--mode` + `--depth` CLI args + forwards them through
  `_resume_via_orchestrator`.
- `src/beril_paper_writer/skill/tools/assemble_docx.py:70` —
  bonus: stale `ArkinLaboratory` URL in the python-docx
  ImportError message → `kbaseincubator`.
- `pyproject.toml` + `src/beril_paper_writer/__init__.py` —
  version `1.1.0 → 1.2.0` (both files, per the CRAFT-sync gotcha
  pinned in v1.1.0).

### Verification (CC-local)

- pytest unit suite: **1105 pass** (+ 27 new in this commit; full
  count includes all existing tests). 0 regressions.
- Ruff: clean on all new files; existing-file delta from this
  cycle's edits = 0 net new issues (pre-existing nits in
  orchestrator.py / assemble_docx.py left alone for scope
  discipline).
- The live verification gate (Cowork: run validate_deliverable
  against the caulobacter draft_2 → clean except for known
  M-series advisories; doctored fixtures per gate → each fires
  loud) and the hub re-run (Adam) remain external.

### Git disposition

Commit-local on `beril-paper-writer` main; NOT pushed, NOT
tagged. Cowork verifies independently; Adam pushes + tags v1.2.0;
then CRAFT v0.3.3 re-pin (submodule → v1.2.0, pyproject pin,
CRAFT version) — and in that same craft-platform commit, the
conformance fixture is extended to assert user_intent.py matches
across presentation-maker + paper-writer (both at v1.2.0).

---

## v1.1.0 — CRAFT runtime-config standardization (2026-06-06)

**Coordinated CRAFT release.** Ships the CRAFT runtime-config arc
(CRAFT-CONTRACT.md §3.4 v2). No `tests/integration` schema change, no
manuscript-rendering behavior change.

**What's new (operational, not schema-level):**

- **Provider abstraction.** `ACTIVE_PROVIDER ∈ {anthropic, cborg,
  subscription}` selects the reasoning backend for both `claude -p` and
  app-internal calls. If unset, it is **inferred** for backward
  compatibility (`CBORG_API_KEY` → `cborg`; `ANTHROPIC_API_KEY` →
  `anthropic`; neither → `subscription`).
- **Three model tiers.** `MODEL_REASONING` / `MODEL_STANDARD` /
  `MODEL_FAST` replace per-phase model env vars. paper-writer's
  orchestrator routes each phase through Claude Code's native
  `--model` aliases (opus / sonnet / haiku) resolved against
  `<BERIL_ROOT>/.claude/settings.json` written by `configure`.
  Per-phase mapping (D-055): throughline / synthesis /
  review-incorporation → **reasoning**; body drafting → **standard**;
  claim classification → **fast**. A caller's explicit `--model`
  still wins.
- **`configure` is now CRAFT-bootstrap.** `beril-paper-writer
  configure` is the runtime-config bootstrap: read `.env`, discover
  the provider's model list (`/v1/models`), pin tier models
  (interactive picker for unresolved tiers on a TTY; fail-loud
  non-interactive), write `<BERIL_ROOT>/.claude/settings.json` (+
  gitignored `settings.local.json`), run a response-asserting
  validation ping against the reasoning tier with auto-fallback if
  the pin fails. The pre-1.1 environment-audit incarnation is gone;
  the genuinely-required preflight runs automatically inside the new
  flow.
- **Additive-only `.env`.** The shared CRAFT block + per-skill marker
  are appended idempotently; existing keys (credentials, tier pins)
  are **never re-declared** — re-declaration would shadow values
  BERIL and other processes already set. `parse_env_text` strips
  inline `#` comments from unquoted values.
- **`app_internal_base_url()` canonical helper** (Stage 6) —
  symmetric `/v1`-keeping sibling of `bare_host`. Verbatim in the
  canonical `llm_config.py` copy across all CRAFT skills for
  cross-skill conformance parity (CI-enforced via the craft-platform
  conformance fixture).
- **Tier-2 review routing fix (Stage 6 fixup).** `phase_review`'s
  narrative-light review tier now resolves the model via the CRAFT
  tier system rather than the legacy `HAIKU_MODEL` env knob. The
  legacy knob is honored when explicitly set (back-compat hatch);
  unset, the resolution flows through the canonical helper and stops
  silently 404'ing under CBORG.

**Backward compatibility.** Explicitly preserved: an old-style `.env`
that only sets `CBORG_API_KEY` (no `ACTIVE_PROVIDER`, no `MODEL_*`)
upgrades cleanly — `infer_provider` returns `cborg`,
`compose_env_append` does NOT re-declare `CBORG_API_KEY`, discovery
pins the tier models. Pinned by `test_old_style_env_upgrades_cleanly`
in `tests/test_llm_config.py`. v1.0.x callers passing explicit
`--model claude-opus-4-X` still bypass tier resolution as before.

**Decision record.** `DECISIONS.md` D-055 captures the conformance
choice; full rationale in `CRAFT-CONTRACT.md §3.4`.

**References.** `CRAFT-CONTRACT.md §3.4`;
`handoffs/CRAFT-config-round2-CC-brief.md` for the paper-writer
specifics (`HAIKU_MODEL` route fix, the configure-shape and tier-vars
choices); `handoffs/CRAFT-config-stage6-CC-brief.md` for
`app_internal_base_url`.

## v1.0.2 — docs: terminology + URL migration (2026-06-03)

**Docs-only.** No code change.

- README + pyproject description: "BERDL analysis projects" →
  "BERIL analysis projects". BERDL has been deprecated as the
  co-scientist name; the data layer is "KBase Lakehouse" and the
  co-scientist is "BERIL". Operational artifacts (prompts, audit
  messages, code identifiers like `berdl_query`/`berdl_start`) keep
  BERDL by design.
- README cross-skill links: sister-skill repos migrated from
  `ArkinLaboratory` to `kbaseincubator` on 2026-06-03. Updated
  beril-adversarial + beril-presentation-maker links; the
  beril-atlas link is intentionally left at `ArkinLaboratory`
  (atlas did not migrate).
- README install hint: `pipx install` URL updated to
  `kbaseincubator/beril-paper-writer-skill`.

CRAFT submodule pin bumps from v1.0.1 → v1.0.2 in CRAFT v0.2.2.

## v1.0.1 — adversarial exit-code contract (2026-05-25)

A compatibility patch for beril-adversarial v0.7.0.8, which made a
schema-invalid-but-parseable `adversarial_review.json` surface as
exit 4 instead of exit 0.

Before this patch, `phase_review` handled the canonical adversarial
reviewer with a binary `if rc != 0` check: it logged a failure but then
advanced unconditionally, and the two downstream consumers
(`phase_p0_review` via the P0 gate, and `phase_optimize`) read
`adversarial_review.json` off disk keyed on file presence, not on the
exit code. A non-consumer-safe exit-4 `.json` that still parsed as JSON
would have been counted as real P0 findings.

v1.0.1 routes on the exit code (`classify_adversarial_exit`): exit 0/2
are consumer-safe; exit 3/4/other quarantine the on-disk
`adversarial_review.json` into `audit/rejected/` and fall back to the
inline reviewer — the same graceful path used when the canonical CLI is
absent. A clean (exit 0) run is unchanged. See DECISIONS.md D-054 and
CONTRACT.md.

## v1.0.0 — first stable release (2026-05-20)

beril-paper-writer reaches v1.0.0. It drafts ICMJE-conformant
scientific manuscripts from BERDL analysis projects end-to-end —
throughline selection, a verified-citation pool, a single holistic
Opus draft, a three-tier review cascade, subtraction-only
optimization, a compliance gate, and docx assembly — without
fabricating evidence.

### How v1.0 was validated — the Stage 7 v1-MVP campaign

v1.0 is not a "we think it works" release. It was scored against a
locked success bar over six BERDL projects.

**The bar — v1-bar v2b** (encoded in
`smoke-test/stage7/collect_metrics.py`; rationale and the full
revision history in `STAGED_IMPROVEMENT_PLAN.md`). A draft passes when
it:

- reached the review measurement point (`p0_review` or beyond),
- has ≤ 5 ungrounded Tier-T numerics (deterministic),
- resolved 100% of its claim markers (deterministic),
- cost ≤ $10 / draft.

Adversarial P0 count is **reported but advisory — not gating**. The
adversarial reviewer is a sampling estimator with run-to-run variance;
gating a v1 success criterion on a noisy LLM-opinion count is not
defensible. The deterministic axes do the gating. (See Known limits.)

**The campaign** — 3 dev projects (`conservation_vs_fitness`,
`amr_pangenome_atlas`, `phb_granule_ecology`) plus 3 **blind** holdouts
(`respiratory_chain_wiring`, `adp1_triple_essentiality`,
`metal_specificity`):

- 6/6 reached the measurement point.
- 6/6 resolved 100% of claim markers.
- 6/6 stayed within the cost budget.
- 5/6 had clean Tier T.
- **3/3 dev pass; 2/3 holdout pass.**

The single holdout failure (`metal_specificity`) failed the Tier-T
axis on a real, characterized drafter-discipline defect — not a
mysterious generalization gap. Per the Stage 7 ship rule, 2/3 holdout
→ ship v1 with documented limits.

### What shipped since v0.8 / Stage 3 → v1.0

**Stage 4 — the gated review pipeline.**

- **Tier R** — early `references.md` render in the drafting phase.
- **Tier S** — the **P0 gate + remediation loop**. The pipeline pauses
  at `phase=p0_review` when P0 findings are present; the operator
  decides whether to remediate (`continue --remediate`). Defensive
  contract checks (Tier S-9) backstop adversarial silent-failure.
- **Tier T** — `check_numeric_grounding.py`: every numeric claim in
  the manuscript is grounded against `claim_inventory.tsv` (Tier A)
  and `REPORT.md` (Tier B); ungrounded numerics are P0.

**Stage 6 (partial) — claim markers.** `[C-NNN]` markers emitted
inline after quantitative claims, resolved against the claim
inventory by `check_claim_markers.py`.

**Stage 7 — multi-project validation + the v1 bar.**

- A validation harness (`smoke-test/stage7/`) running projects
  unattended and scoring them against the bar.
- **#41 / D-052** — the Tier-T extractor gained scientific-notation
  (`1.1 x 10^-130` ↔ `1.1e-130`), K/M/G/T-suffix, and trailing-zero
  normalization. Forensic analysis of the dev runs showed most
  "ungrounded" numerics were extractor false positives, not drafter
  fabrications; the fix cut dev-set Tier-T findings 23 → 5.
- **v1-bar v2a → v2b** — the success bar was revised after the
  campaign: dropped criteria the no-auto-remediate harness cannot
  measure, gated only the deterministic axes, made adversarial P0
  advisory.

**D-053 — `paper_writer.sh` retired.** The v0.x shell orchestrator
(~3468 LOC) and 12 checker tools reachable only through it were
deleted, completing the v0.8 shell→Python migration. The Python
`PaperWriterOrchestrator` is the sole orchestrator.

### Known limits — read before relying on v1.0

v1.0 ships with these documented. None is silent; each is detected
and/or tracked. Full detail in [`V1_X_BACKLOG.md`](V1_X_BACKLOG.md).

- **#46 — drafter can treat `RESEARCH_PLAN.md` predictions as
  results.** On one holdout the drafter pulled predicted/hypothesis
  values (`OR=2.08`, threshold percentages) from `RESEARCH_PLAN.md`
  into the Results/Introduction as if measured. This was the single
  holdout failure. It is **loudly detected** — both Tier T and the
  adversarial reviewer fire on it — not a silent error. P1, fix in
  v1.x.
- **#48 — the Tier-1 deterministic check table is partially
  implemented.** v1.0 runs the numeric-grounding and claim-marker
  legs. Figure/table-callout resolution and language-quality
  advisories (SPEC §7.2's remaining rows) are deferred to v1.1; the
  canonical adversarial reviewer covers that ground judgmentally in
  the meantime.
- **#40 / #44 — minor Tier-T residue.** A few ungrounded numerics
  per draft can be legitimate-but-unbacked (external-citation values,
  source-dataset definitional thresholds). The bar tolerates ≤ 5.
- **#37 — adversarial review is non-deterministic.** The reviewer
  samples the defect surface; P0 counts vary ±2–5 run to run. This is
  why adversarial is advisory, not gating, in v1-bar v2b.
- **#36 residual — adversarial JSON can be malformed.** The
  adversarial reviewer occasionally emits unescaped inner quotes
  (stochastic, cross-skill). When it happens, that run's adversarial
  advisory reads UNMEASURABLE; the deterministic verdict is
  unaffected.
- **#43 — `review_cost_usd` not populated.** Per-draft cost reported
  at the p0_review measurement point is conservatively *high* by the
  unattributed post-remediation review spend (~$0.30–0.80). Does not
  affect any v1 verdict.
- **Cost scope.** `state.cost_so_far_usd` tracks paper-writer's own
  LLM spend; the `beril-adversarial` reviewer bills separately
  (~$0.50–1.50/run). Budget total accordingly.

### Pointers

- [`README.md`](README.md) — quick-start.
- [`SPEC.md`](SPEC.md) — foundation + v0.8 architecture + ICMJE appendix.
- [`STAGED_IMPROVEMENT_PLAN.md`](STAGED_IMPROVEMENT_PLAN.md) — Stage 1–7
  closure tables + the v1-bar revision history.
- [`V1_X_BACKLOG.md`](V1_X_BACKLOG.md) — the v1.x backlog (known limits + v1.1 work).
- [`DECISIONS.md`](DECISIONS.md) — design decisions D-001 through D-053.

---

## Prior: v0.8.0 + Stage 3 (2026-05-17)

The v0.8 production pipeline, as it stood at Stage 3 close. Retained
here as history; superseded by the v1.0.0 section above.

### What v0.8 established

- **8-phase pipeline** (Python orchestrator):
  init → extract → triage → plan → throughline_pick (PAUSE) →
  citation_pool → drafting → review → optimize → supplementary_pool →
  compliance_gate → assemble → assembled.
- **Holistic drafter** — a single Opus pass produces the entire
  manuscript. The legacy sectional flow (per-section files
  `01_methods.md`, etc.) is gone; output is a single `manuscript.md`.
- **Three-tier review cascade** — Tier 1 (deterministic) + Tier 2
  (Haiku light) + Tier 3 (canonical `beril-adversarial` with
  loud-warn fallback to `fallback_reviewer.v1` if not installed).
- **Subtraction-only optimizer** — can remove unbacked claims and
  insert `[NEEDS CITATION:]` markers, but cannot fabricate new
  numbers, citations, or evidence.
- **Verified citation pool** — every entry has a DOI or PMID that
  resolves; the supplementary phase resolves new markers via WebSearch.
- **Figure embedding** — `manuscript.docx` ships with figures inline.
- **Compliance gate** — ICMJE checks (AI-disclosure, Data
  Availability, etc.) with autofix.

### Stage 3 (Tiers A–K, 2026-05-12 → 2026-05-17)

Closed the post-v0.8 in-situ defects surfaced by the BERIL
slash-command runs on `ibd_phage_targeting`: figure staging,
absolute-path resolution for `claude` and `beril-adversarial`,
model-pin on all LLM calls, citation-pool schema corrections,
source-notebook recovery, and the loud-warn adversarial fallback.
Full closure table in [`STAGED_IMPROVEMENT_PLAN.md`](STAGED_IMPROVEMENT_PLAN.md);
design rationale in [`DECISIONS.md`](DECISIONS.md) D-041 through D-051.

Note: the Stage-3 audit cleanup put `paper_writer.sh` on a retirement
track; it was fully retired at v1.0 (D-053).

## Per-version notes

| File | Coverage |
|---|---|
| [release-notes/v0_1.md](release-notes/v0_1.md) | First usable release (2026-04-27) |
| [release-notes/v0_2.md](release-notes/v0_2.md) | Repair-mode + post-checker hardening |
| [release-notes/v0_3.md](release-notes/v0_3.md) | Figure-embed loop (Tier 3 sub-tiers) |
| [release-notes/v0_4.md](release-notes/v0_4.md) | Caption-richness gap closure |
| [release-notes/v0_5.md](release-notes/v0_5.md) | Caption-quality tightening (point release) |
| [release-notes/v0_6.md](release-notes/v0_6.md) | Dual-reviewer architecture decision |
| (v0.7 / v0.8 / Stages 1–7) | `STAGED_IMPROVEMENT_PLAN.md` + this file |
| [release-notes/SPEC_v0_1.md](release-notes/SPEC_v0_1.md) | Original v0.1 SPEC, merged into current `SPEC.md` |
