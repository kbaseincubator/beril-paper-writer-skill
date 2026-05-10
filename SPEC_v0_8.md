# beril-paper-writer — Specification (v0.8)

**Status:** SIGNED OFF — M0 complete 2026-05-07. M1 unblocked.
**Authored:** 2026-05-07. **Decision context:** see auto-memory entry
`project_paper_writer_v0_8_architecture.md`. All twelve sign-off items
resolved (§19); Q11 housekeeping (rename of prior punch list) executed.
**Relationship to existing docs:**
- Supersedes `SPEC.md` (v0.1) for the architectural layer. SPEC.md's §1–§4
  (purpose, scope, design premises, throughline mechanism) and §10 (ICMJE
  AI-disclosure boilerplate) remain authoritative — v0.8 changes the
  *pipeline* and *review machinery*, not the product mission.
- Supersedes the prior `V0_8_0_PUNCH_LIST.md` (language-quality + post-checkers
  on top of v0.7.x). That punch list's Tier-A prompt rules and Tier-B
  post-checkers are absorbed into this spec's Phase 2 (holistic prompt
  discipline) and Phase 3 (Tier-1/2 deterministic + light reviewer). The
  punch-list file should be archived or renamed to
  `archive-v0_8_language_quality_punch_list.md` once this spec is signed off.
- Sister-skill contract: `beril-adversarial v0.7.x` (paper schema v2 → v3
  trajectory). This spec assumes adversarial v0.7.0+ is installed; the Tier-3
  reviewer is the canonical adversarial CLI invocation.

This document is *what* v0.8 does and *why*. The *how* (package layout, file
paths, state schema, CLI flags) is captured per-milestone in
`LAYOUT_v0_8.md` (M1 deliverable when first touched) and pinned in
`DECISIONS.md` as v0.8 entries land.

---

## 1. Why v0.8 — three converging signals

### 1.1 The per-section sprawl pattern (v0.4 → v0.7.x)

Every live-test failure cycle in v0.4–v0.7.x produced a per-section patch:

- v0.4: caption sufficiency gate, panel-count word budgets.
- v0.5: boilerplate-aware sufficiency (`_strip_prose_for_inline`).
- v0.6: tables pipeline (extract, manifest, post-check, embed).
- v0.7.0/0.1: rewrite_passes counter, abstract italic labels, figure-manifest
  + scope-coherence false positives, M2 sentence caps, abstract anti-patterns.
- v0.7.2 (in flight): Data Availability fixes.
- The deferred v0.8.0 punch list: 7 prompt rules + 3 advisory checkers.

The pattern is asymptotic. Each patch closes one symptom of the same
upstream weakness — the per-section orchestrator hands each prompt a slice
of context and lets the prompt synthesize prose without seeing the whole
manuscript's narrative arc. Symptoms surface downstream as register drift,
echo repetition, sentence-complexity failures, scope-coherence false
positives. Patching downstream is the wrong layer.

### 1.2 The AI Scientist evidence (Lu et al., Nature 2026)

The most-engineered automated-paper system to date — per-section LaTeX-fill
pipeline + 20-round iterative citation injection + 5-reviewer ensemble +
meta-reviewer — reached the **workshop bar but not the main-conference bar**
for fully-AI papers (Lu et al., *Nature* 2026, doi:10.1038/s41586-026-10265-5;
quote: "none met the higher bar for a main ICLR publication"). Per-section
sophistication is not the missing ingredient. The bottleneck is upstream:
story selection, integrative finding curation, novelty argumentation. v0.7.x
already invests in per-section discipline; doubling down does not move the
needle.

What the AI Scientist work *does* validate as a building block: iterative
citation rounds with adaptive stop and per-citation justification. v0.8 adopts
that pattern (Phase 5) — calibrated to the BERIL setting with the verified
citation pool as the source.

### 1.3 The IBD one-shot exercise (2026-05-06/07)

Adam ran `ibd_phage_targeting` through BERIL paper-writer v0.7.x and through
a single Claude conversation with the `paper-review` skill. The two outputs
diverged on which dimension each one won:

- **BERIL won on methodology honesty.** ANCOM-BC failure disclosure,
  AI-disclosure block, scope discipline. These trace directly to the Phase-0
  tooling artifacts (methods_provenance, figures_manifest, citation_pool
  with verify-by-resolution).
- **The one-shot draft won on integrative biology.** NB07d/CC1 single-axis
  claim featured as Results §4. Six-line cross-corroboration narratives
  shaped Results §3. Hourglass arc end-to-end. These come from the model
  seeing the whole project at once and synthesizing a story across the
  evidence, not from per-section prompts knitting localized claims together.

Conclusion: BERIL's competitive advantage is *not* the per-section prompts.
It is the deterministic tooling that produces verifiable artifacts (provenance,
citation pool, discrepancy register, claim inventory). The drafting layer
should be a single holistic pass that consumes those artifacts; the review
layer should catch what the holistic pass misses, with reviewer ensemble
sized to actual failure modes rather than to a theoretical taxonomy.

---

## 2. Design premises (what v0.8 is optimizing for)

The five SPEC.md §2 premises (honesty, auditability, user judgment over LLM
judgment, bounded cost, reuse over generation) carry forward. v0.8 adds two:

6. **Subtraction over addition.** Most v0.4–v0.7.x complexity is patching
   downstream symptoms of upstream prompt weakness. The v0.8 simplification
   is mostly removal. Tooling investment (Phase 0) is the only net-new
   engineering; everything else is reorganization of existing components.
7. **Failure-mode-driven review tiering.** Reviewer cost should match the
   class of failure being detected. Cheap deterministic checks for cheap
   problems (section presence, citation resolution, manifest cross-walks).
   A light Haiku-class reviewer for narrative problems with reproducible
   detection patterns. The expensive canonical adversarial only after the
   cheap layers have done their job.

---

## 3. Pipeline overview — the 8-phase architecture

```
Project artifacts (REPORT, RESEARCH_PLAN, notebooks, figures, references.md)
   │
   ▼
[Phase 0]   Deterministic tooling (run-once per project, idempotent)
   │       ├── methods_provenance.md   (existing v0.7.x)
   │       ├── citation_pool/pool.json (existing v0.7.x; verify-by-resolution)
   │       ├── figures_manifest.tsv +
   │       │   figures_inventory.md    (existing v0.7.x)
   │       ├── tables_inventory.md +
   │       │   tables_manifest.tsv     (existing v0.7.x)
   │       ├── discrepancy_register.md (NEW)
   │       └── claim_inventory.md      (NEW)
   ▼
[Phase 1]   Story builder (INTERACTIVE — the load-bearing user gate)
   │       Input: target journal + arc preference + author block.
   │       Output: 1-page outline + figure/table budget. User approves
   │       or amends; on amend, re-emit until approval.
   ▼
[Phase 2]   Holistic write (one pass)
   │       Input: ALL Phase-0 artifacts + Phase-1 outline.
   │       Output: complete manuscript.md (all sections in one call).
   │       System prompt ≤300 lines.
   ▼
[Phase 3]   Tiered review cascade with fail-fast
   │       ├── Tier 1: deterministic + minimal LLM (~2–5K tokens). Cap 2 passes.
   │       ├── Tier 2: Haiku + subset of beril-adversarial classes
   │       │           (~10–15K tokens). Cap 2 passes.
   │       └── Tier 3: Sonnet + full beril-adversarial v3 canonical.
   │                   Run once; retry once on P0 only. Cap 2 passes.
   ▼
[Phase 4]   Selective per-section optimizers
   │       ├── Abstract optimizer (always, bounded).
   │       ├── Methods reproducibility audit (always, deterministic).
   │       └── Other sections only if reviewer-flagged.
   ▼
[Phase 5]   Iterative citation rounds (5–8 adaptive)
   │       Per-citation justification + integration + manuscript re-check.
   │       Adaptive stop: 2 consecutive rounds with no new candidates.
   ▼
[Phase 6]   Compliance gate (deterministic; build-fails-if-missing)
   │       ICMJE structural items, AI-disclosure block, data-availability,
   │       reference-list integrity.
   ▼
[Phase 7]   Copy edit (low-temp, narrow-scope, diff-capped)
   │       Forbidden from changing claims, structure, or citations.
   │       Cap on lines changed per pass.
   ▼
[Phase 8]   Final docx (existing v0.3+ figure-embedding pipeline)
```

Each phase has: an idempotent contract, a state.json checkpoint, an
audit/ artifact, and a halt-to-handoff failure path that preserves the
parser-facing `phase=halted` shape from v0.7.x.

---

## 4. Phase 0 — Deterministic tooling

Phase 0 is the load-bearing competitive advantage and the only net-new
engineering investment. Everything in §4.1–§4.4 already ships in v0.7.x;
§4.5–§4.6 are NEW for v0.8.

### 4.1 methods_provenance.md (KEPT — `extract_methods.py`)

No change to extraction. Phase 2's holistic prompt consumes the same
provenance artifact methods.v1 currently consumes. Keep `extract_methods.py`,
keep its contracts, keep its tests.

### 4.2 citation_pool/pool.json (KEPT — `citation_pool.v1` + `citation_pool.py`)

The verified-citation-pool builder is the most expensive Phase-0 component
($1–$3 per run; 30–80 verified entries) and the most defensive. Reused as-is.
v0.8 changes citation *use* (Phase 5's iterative rounds) but not citation
*acquisition*.

### 4.3 figures_manifest.tsv + figures_inventory.md (KEPT — `extract_figures.py`)

Reused as-is. The v0.4/v0.5 sufficiency gate, panel-count caption budgets,
and Source-4 LLM caption synthesis remain in Phase 8's docx pipeline.

### 4.4 tables_inventory.md + tables_manifest.tsv (KEPT — `extract_tables.py`)

Reused as-is. v0.6's full tables pipeline (extract → inventory → LLM selection
→ manifest → check → embed) is invoked from Phase 8.

### 4.5 discrepancy_register.md (NEW — `discrepancy_register.py`)

**Purpose.** Plan-vs-execution diff scanner. Surfaces every place where
RESEARCH_PLAN.md prescribed an analysis the notebooks did not execute, OR
the notebooks executed an analysis the plan did not prescribe. This is what
the v0.7.x reframer.v1 prompt does *post hoc, in prose*, after the manuscript
exists. v0.8 lifts it upstream: the holistic write sees the discrepancies
*before* it drafts, and the manuscript opens with a footing already
calibrated to actual execution rather than to the plan's intent.

**Inputs.**
- `methods_provenance.md` (notebook-grounded methods).
- `RESEARCH_PLAN.md` (intent).
- Optional: prior `reframing_log.md` if a draft already exists for this project.

**Output schema** (markdown, append-only across drafts):

```markdown
# Discrepancy Register

## D-001 — type: plan-prescribed-not-executed
- Plan §X: "Welch's t-test with α=0.05 across 343 conditions"
- Execution: notebook NB04 cell 12 implements Mann-Whitney U; α not stated.
- Severity: load-bearing (claim N depends on choice of test).
- Recommendation: report Mann-Whitney U in Methods; re-state Hypothesis
  framing in Intro; note in Limitations if power changed.

## D-002 — type: executed-not-prescribed
- Plan: silent on multiple-testing correction.
- Execution: NB04 cell 18 applies Benjamini-Hochberg FDR.
- Severity: cosmetic (plan was incomplete; execution is correct).
- Recommendation: surface in Methods; no other section impact.
```

**Severity scale:** `load-bearing` (claim depends on the discrepancy) /
`cosmetic` (transparent improvement) / `unclear` (needs human review).

**Cost.** Deterministic Python + small LLM pass (~2K tokens) for the
plan-text classification. **Tracked, not gated** (D-037, 2026-05-07):
the per-call constant `_COST_CEILING_USD = 0.05` remains as
informational; audit JSONL records cost per run; no stderr warning
or smoke gate fires on overrun. **Decided 2026-05-07 (Q1):** LLM-
assisted, not pure string-match — synonym/paraphrase robustness
required for hand-authored RESEARCH_PLAN.md text. **Q1 deferred
2026-05-07 (D-035):** re-evaluation requires fixing two upstream
defects (plan-side heading regex too narrow; overlap-ratio threshold
too restrictive on prose-heavy bullets) which are v0.9 architectural
work; on `ibd_phage_targeting` the LLM never fires because the
deterministic pre-pass produces zero overlap candidates.

**Why upstream rather than post hoc.** v0.7.x's reframer.v1 runs *after*
sections are drafted; if the manuscript has already woven a plan-prescribed
claim into Results, the reframer must rewrite Results, Methods, and
Limitations to reconcile. Lifting discrepancy detection into Phase 0 lets
the holistic write avoid the contradiction in the first place. This is the
single largest lever for the holistic-write quality story.

**Tests (M1).** Synthetic projects with each discrepancy type; confirm
register schema validation; confirm idempotent re-run.

### 4.6 claim_inventory.md (NEW — `claim_inventory.py`)

**Purpose.** Index of every numeric assertion in REPORT.md, traced to its
notebook source (NB#:cell:line) and its supporting figure/table if any.
v0.7.x's M7 validator (numerical claims have n + effect size + 95% CI) checks
the *manuscript* after drafting. v0.8 inverts: produce the inventory of
claimable numbers up front, then the holistic write picks from the inventory
rather than invents numbers.

**Inputs.**
- `REPORT.md` (canonical findings).
- `methods_provenance.md` (so each numeric claim can be linked to the
  notebook cell that produced it).
- `figures_inventory.md` + `tables_inventory.md` (cross-link to display items).

**Output schema (TSV, machine-readable for the holistic prompt's grounding):**

```
claim_id  claim_text                                  source_notebook  source_cell  figure_or_table  effect_size_present  ci_present  pvalue_present  notes
C001      "88.2% of E1 Tier-A signs concordant"      NB07d            14           Fig 3            yes                  yes         yes             primary
C002      "95 of 343 conditions with AUC > 0.75"     NB04             18           Tbl 2            no                   no          no              uncorrected; M7 risk
C003      "16.2 mg/L MIC for compound X"             NB02             7            Fig 1B           no                   no          no              cosmetic
```

**Schema rationale.** Phase 2's holistic prompt is told: *"Every numeric
claim in your manuscript must reference a `claim_id`. Claims without a
`claim_id` are forbidden. Claims with `effect_size_present=no` AND
`ci_present=no` AND `pvalue_present=no` must be qualified with the
appropriate hedge."* This collapses the M7 validator from a post-hoc regex
into a constructive constraint at draft time.

**Cost.** Deterministic regex extraction over REPORT.md + small LLM pass for
ambiguous claim demarcation. **Tracked, not gated** (D-037, 2026-05-07):
constant `_DEMARCATOR_COST_CEILING_USD = 0.10` remains as informational;
audit JSONL records cost; no stderr warning or smoke gate fires on
overrun. The B1.e regex catalog extension (D-036) raised observed
demarcator workload on dense projects (`ibd_phage_targeting`: ~133
multi-numeric sentences ≈ ~$1.00/run with Haiku 4.5 in the B1.f batched
path); cap will be re-tuned from observed data at M2 orchestrator layer
rather than per-tool.

**Batched demarcation (B1.f, D-038, 2026-05-07).** Demarcator calls are
chunked at `--batch-size` (default 15). The LLM sees per-batch local
indices [0..batch_size); the tool offsets back to absolute indices
into `unresolved_candidates` before validation, sums per-call costs,
and validates full coverage. Default batch size of 15 was calibrated
on `ibd_phage_targeting` after a 133-candidate single call truncated
the LLM output (only 91 indices covered → exit 4) and a follow-up run
hit the subprocess wrapper's 180s timeout. Batching trades
"single-LLM-call latency" for "predictable per-call latency × N
batches"; total per-run cost on dense projects is now `ceil(N /
batch_size) × ~$0.10`. The cache key includes `batch_size` so changing
the chunk size invalidates the idempotency cache.

**Cite allowlists in the demarcator user prompt (B1.h, D-040,
2026-05-07).** `build_demarcator_user_prompt` extracts every notebook
path from methods_provenance.md and every `Fig N`/`Tbl N`/`Table N`
label from figures_inventory.md + tables_inventory.md, then emits
two explicit "VALID values" allowlists ABOVE the INPUTS section of
the user prompt. The system prompt at `prompts/claim_demarcate.v1.md`
gains anti-pattern worked examples covering: (1) notebook-name
truncation (paraphrasing by scientific subject instead of copying the
literal filename), (2) treating a notebook ID as a figure label.
Driven by live-LLM smoke after B1.g where the demarcator emitted
`notebooks/NB07a_H3a_falsifiability.ipynb` (real:
`notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb`) and
`figure_or_table="Fig NB15"`. The validator's per-row checks remain
unchanged; the allowlist is a guide, not a gate.

**Bounded retry + tolerated_missing fallback (B1.g, D-039, 2026-05-07).**
Even with batching, the live Haiku 4.5 demarcator non-deterministically
drops 1–3 input candidates per dense-project run (different indices
each run; ~98% per-batch coverage). After the initial batched pass,
missing indices are re-batched into a fresh LLM call, up to
`max_retries=3` rounds. Indices that remain missing after retries fall
back to the original `notes='unresolved'` row via
`expand_with_demarcations`' defensive empty-rows pass-through. The
validator's `allow_missing` kwarg allows residuals through coverage
without failing exit 4. The cache schema gains a `tolerated_missing`
field so reruns are byte-stable. M2's holistic prompt sees a mix of
demarcated rows (notes='') and rare unresolved rows (notes='unresolved')
and is responsible for hedging the latter.

**Notebook-cite grounding (B1.e, 2026-05-07).** The validator accepts
`source_notebook` if EITHER (a) substring of `methods_provenance.md`
OR (b) the path resolves to an existing file under
`<project-root>/`. The disk-fallback was added after live-LLM smoke
on `ibd_phage_targeting` revealed `methods_provenance.md` covers only
~40% of project notebooks (the AST extractor in `extract_methods.py`
catalogs only notebooks with detected stat-test invocations, missing
those that produce numerics via pandas/SQL/custom code). Anti-
fabrication discipline is preserved by the `is_file()` requirement —
the LLM cannot cite a path that doesn't exist on disk. Tests pass
synthetic fixtures with `project_root=None` and rely on the substring
path; production runs derive `project_root` from
`methods_provenance.md`'s expected layout
(`<project>/papers/draft_N/methods_provenance.md`) or take an
explicit `--project-root` flag.

**Regex catalog (B1.e, D-036, 2026-05-07).** 11 pattern classes
(extended from 6 in B1.b): `percentage` (now whitespace-tolerant),
`ratio_with_unit`, `p_value` (now accepts ≤/≥ + dot-less mantissa),
`confidence_interval`, `n_count`, `metric` (AUC/R²/RMSE/MAE),
`correlation` (r/ρ), `odds_ratio` (OR), `log_fc` (log₂FC), `count_of`
(M of N / M / N), `cliff_delta` (cliff δ). Recall on the
ibd_phage_targeting ground-truth check: 0.562 (B1.b) → 1.000 (B1.e).

**Coverage policy.** **Decided 2026-05-07 (Q2):** full coverage — every
numeric claim in REPORT.md gets a `claim_id`. No salience filter in v0.8.0.
The holistic prompt's word budget is the natural cut. Inventory size for
dense projects (functional_dark_matter expects ~40–80 claim_ids) is
tractable at the TSV level. If a future project's inventory exceeds ~150
claim_ids and the holistic prompt's input budget is strained, revisit at
v0.8.x.

**Tests (M1).** Smoke against ibd_phage_targeting; confirm every numeric
claim in REPORT.md surfaces with a `claim_id`; confirm each claim_id resolves
to an extractable notebook cell.

### 4.7 Shared Phase-0 contracts

- All Phase-0 artifacts live in `papers/draft_N/` (the per-draft directory),
  not at the project root. Different drafts may want different inventories
  (e.g., a STRONG-tier paper draft and a separate exploration-report draft
  should each get their own claim_inventory).
- Each Phase-0 tool emits an entry to `papers/draft_N/audit/phase0.jsonl`:
  invocation timestamp, inputs hashed, outputs written, exit status, cost.
- Re-running Phase 0 with unchanged input hashes is a no-op (read cache from
  audit log; re-emit identical artifacts).

---

## 5. Phase 1 — Story builder (interactive)

**Decided 2026-05-07 (Q3):** STRONG/THIN/EXPLORATORY triage (SPEC.md §3.1)
is rolled into the story builder; no separate triage phase. The story
builder's outline is tier-shaped — section budgets, framing language, and
limitations weight follow from the inferred tier. The triage verdict is
recorded explicitly in `00_story_outline.md`'s frontmatter so Phase 2's
holistic prompt and Phase 3's reviewers can reference it.

### 5.1 Why this phase exists separately from Phase 2

The throughline-pick gate (SPEC.md §4) was the v0.7.x mechanism for keeping
a human in the loop on the load-bearing scientific decision. v0.8 keeps that
intent and elevates it: in v0.7.x the user picks among 2–3 candidates *before*
the writer has seen the whole project; in v0.8 the user picks among
candidates *that have been calibrated to* the discrepancy register and claim
inventory.

### 5.2 Input contract

User-facing slash command `/beril-paper-writer` collects (or auto-detects):

| Field | Value | Source |
|---|---|---|
| target_journal | venue name OR "generic-IMRAD" OR "exploration-report" | user supplies; defaults to "generic-IMRAD" |
| arc_preference | "hourglass" / "linear" / "case-study" / "auto" | user supplies; defaults to "auto" |
| author_block | author list, affiliations, corresponding author | from `RESEARCH_PLAN.md` "Authors" section if present; otherwise prompted |
| word_target | per-section soft caps OR "default" | "default" = 4000 main text |
| figure_budget | integer 4–10 | from figures_inventory.md count, capped at 10 |

### 5.3 Output: 1-page outline + figure budget

The story builder LLM (Sonnet, ~5–10K tokens) consumes Phase-0 artifacts and
emits `papers/draft_N/00_story_outline.md`:

```markdown
# Draft N — Story outline

**Target:** {target_journal} / {arc_preference}
**Tier:** STRONG | THIN | EXPLORATORY (per SPEC.md §3.1 triage)
**Mode:** paper | report (per SPEC.md §3.2)

## Throughline (one sentence)
{the central claim the manuscript will make}

## Section budget
| Section | Words | Primary evidence |
|---|---|---|
| Abstract | 250 | C001, C002 (claim inventory) |
| Introduction | 700 | references R1, R3, R7 (citation pool) |
| Methods | 900 | methods_provenance §§1–4 + D-001, D-002 (discrepancy register) |
| Results | 1500 | C001–C012 + Fig 1, Fig 2, Fig 3, Tbl 2 |
| Discussion | 1100 | C001, C005, C012 + references R12, R18 |
| Limitations | 350 | discrepancy register: load-bearing items only |
| Data availability | 100 | template fill |

## Figure plan
Fig 1: NB02 plot from figures_inventory (panel layout: 2x2)
Fig 2: NB04 plot, single panel
Fig 3: NB07 panel grid (4 panels)
Tbl 1: tables_inventory tbl_03 (top-N hits)
Tbl 2: tables_inventory tbl_05 (FDR-corrected counts)

## Discrepancies that shape the story
- D-001 (load-bearing): Methods narrates Mann-Whitney U, not Welch's; Limitations notes the power tradeoff.
- D-002 (cosmetic): Methods includes the BH-FDR step.

## What this draft will NOT include
- (per SPEC.md §4.2 throughline mechanism — finds that fall outside the throughline are noted as "Demoted to appendix" or "Deferred to follow-up")
```

### 5.4 User gate (load-bearing)

The skill pauses with `phase=story_pick` handoff. The user reviews
`00_story_outline.md` and either:

1. **Approve.** `/beril-paper-writer-continue <draft_dir> --approve-story`
2. **Amend.** `/beril-paper-writer-continue <draft_dir> --amend-story "tighten Results to focus on E1; demote E2/E3 to appendix"`. The story builder re-runs with the amendment text included as input; emits a revised outline.
3. **Pick alternative.** If multiple candidate outlines were emitted (story builder may, by request via `--candidates 3`, produce 2–3), `--pick STORY{N}` selects.

**Hard cap:** 3 amendment cycles. After 3, the writer halts with a
`phase=story_blocked` handoff suggesting the user manually edit
`00_story_outline.md` and run `--approve-story`.

### 5.5 Why this is a separate phase, not the front of Phase 2

Two reasons:

- **Cost discipline.** Phase 2's holistic write is the most expensive single
  call in the pipeline (~50K input, ~30K output; $1.50–$3.00). Resuming Phase
  2 from scratch on every story amendment would be punitive. Phase 1's
  outline iteration is small (~10K input, ~5K output; ~$0.10/cycle).
- **Auditability.** The story outline is the artifact every downstream phase
  references. If Phase 7 copy-edit is suspected of changing scope, the diff
  is against `00_story_outline.md`, not against the draft's prior version.

---

## 6. Phase 2 — Holistic write (one pass)

### 6.1 Single LLM call producing the full manuscript

One `claude -p` invocation. Input: all Phase-0 artifacts + the approved
`00_story_outline.md`. Output: a single `manuscript.md` containing every
section in its final position, drafted as one coherent document.

**Why holistic.** The IBD one-shot exercise demonstrated that an LLM
seeing the whole project at once produces better integrative biology than
per-section prompts knitting localized claims together. The "see everything
at once" property is the v0.8 architectural bet.

### 6.2 System prompt budget

≤300 lines (~10K tokens). Replaces the v0.7.x cumulative prompt budget of
plan.v1 (~250 lines) + methods.v1 (~480 lines) + results.v1 (~520 lines) +
discussion.v1 (~410 lines) + intro.v1 (~250 lines) + abstract.v1 (~330 lines)
+ reframer.v1 (~260 lines) ≈ 2500 lines across 7 prompts. The subtraction
is real: most of the per-section discipline collapses to "consume the
artifact in §X" + "obey the prompt rules from V0_8_0_PUNCH_LIST Tier A."

### 6.3 Discipline rules baked into the holistic prompt

The v0.8.0 punch list's Tier-A prompt rules (sentence length, abbreviation
expansion, quantitative stacking, single-hedge, transitions, notebook-citation
externalization, echo repetition) were designed to be applied per-section.
v0.8 folds them into the single holistic prompt with one important change:
they apply to the *manuscript as a whole*, not to each section in isolation.
"Each abbreviation expanded on first use" is now globally checkable; "no
quantitative finding stated more than 2× in the manuscript" needs only one
prompt-level enforcement, not three.

### 6.4 Inputs the holistic prompt consumes (load-bearing)

| Artifact | Role |
|---|---|
| 00_story_outline.md | The narrative skeleton; section word budgets; figure plan. |
| methods_provenance.md | Methods source-of-truth; every protocol claim must trace here. |
| claim_inventory.md | Every numeric claim in the manuscript must reference a `claim_id`. |
| discrepancy_register.md | Methods + Limitations + (sometimes) Results discipline. |
| figures_inventory.md + figures/ | Figure choices + caption candidates. |
| tables_inventory.md | Table choices + caption candidates. |
| pool.json | Citation pool; citations cited in prose must resolve here. |
| references.md | Pre-existing citations user supplied. |

### 6.5 Output contract

Single file: `papers/draft_N/manuscript.md`. Sections delimited by
markdown headers in IMRAD order (per SPEC.md §6.1). Figure callouts use
`(Fig. N)` and table callouts use `(Table N)` (the v0.6 contract). No
inline image tags; embedding is Phase 8's job.

### 6.6 What the holistic prompt is forbidden from doing

- Inventing numeric claims (every number must reference a `claim_id` from
  `claim_inventory.md`).
- Inventing citations (every citation must resolve in `pool.json`).
- Inventing methods (every protocol must trace to `methods_provenance.md`).
- Citing notebook cells in main-text prose (v0.8 punch list A6).
- Stating any quantitative finding more than 2× in the manuscript (A7).

### 6.7 Cost target + model choice

**Decided 2026-05-07 (Q4):** default model is **Opus 4.6**
(`claude-opus-4-6`). Holistic write is the load-bearing integrative-biology
step; the AI Scientist evidence and the IBD one-shot exercise both used
frontier-class models for this kind of synthesis. Sonnet remains available
via `--model claude-sonnet-4-6` for cost-sensitive runs.

50K input + 30K output Opus 4.6 ≈ $4–$8 per pass (roughly 2.5–3× Sonnet).
Compare to v0.7.x's 8 per-section LLM calls (Sonnet for mechanical, Opus
for narrative) totaling ~$2.50–$5.00 cached. Net cost at the holistic-write
phase is +$2–$3 vs v0.7.x; the Phase-0 reframer-equivalent runs cheaper
upstream, partially offsetting. Accepted trade-off: front-loaded quality
investment matches the "subtraction over addition" frame — fewer calls,
each with more context, on a stronger model.

### 6.8 Per-section prompts retired (subtraction)

v0.8's M8 cut-over removes from the orchestrator:
`methods.v1.md`, `results.v1.md`, `discussion.v1.md`, `intro.v1.md`,
`abstract.v1.md`, `reframer.v1.md`, `revise_throughline.v1.md`,
`rewrite.v1.md`, `fallback_reviewer.v1.md`. (`citation_pool.v1.md`,
`figure_caption.v1.md`, and `plan.v1.md` carry forward — citation_pool
is Phase 0; figure_caption is Phase 8; plan's role is absorbed into Phase 1
but the prompt's triage logic may be reused.)

The retired prompts are not deleted; they are moved to
`prompts/archive/v0_7/` and remain reachable for fallback or for resurrection
if M7 cut-over fails.

---

## 7. Phase 3 — Tiered review cascade

### 7.1 Three tiers, fail-fast

Each tier:

- Reads `manuscript.md` + Phase-0 artifacts.
- Emits findings in the beril-adversarial schema (paper v3 single-array
  shape; consumer contract is `adversarial-review-paper.v3` per
  beril-adversarial v0.7.x).
- Hands findings to a deterministic dispatcher that decides whether to
  trigger a Phase-2 rewrite, a Phase-4 selective optimizer, or escalate
  to the next tier.

**Fail-fast** means: if Tier 1 surfaces P0s, do NOT spend on Tier 2/3 until
Tier 1 P0s are resolved. If Tier 2 surfaces P0s, do NOT spend on Tier 3.
This bounds cost in the failure case and matches reviewer cost to defect
class.

### 7.2 Tier 1 — deterministic + minimal LLM (~2–5K tokens)

Pure mechanical checks; LLM pass only for ambiguity resolution.

| Check | Implementation |
|---|---|
| All ICMJE V.A IMRAD sections present | regex |
| AI-disclosure block present + names tool/version/task | regex + minimal LLM |
| Figure callouts resolve to figures_inventory entries | regex cross-walk |
| Reference pool resolution (every prose citation in pool.json) | regex cross-walk |
| Discrepancy-register citation (every load-bearing D-N is acknowledged in Methods or Limitations) | regex |
| Provenance tags (every claim_id referenced in prose resolves to claim_inventory) | regex cross-walk |
| Word count per section within ±20% of `00_story_outline.md` budget | wc -w |
| AI-disclosure block (M3-equivalent) | regex |
| Data-availability statement (M4-equivalent) | regex + length check |

This subsumes v0.7.x's `validate_manuscript.py M1–M10` plus the v0.6
manifest cross-walks (`check_figures_manifest.py`, `check_tables_manifest.py`,
`check_caption_provenance.py`) plus the v0.8 punch list Tier-B advisory
checkers (`check_sentence_complexity.py`, `check_abbreviation_discipline.py`,
`check_echo_repetition.py`).

**Cost:** ~$0.02 per pass. **Cap:** 2 passes; on second-pass failure, the
specific failures route to Phase 4 selective per-section work or to
human-in-loop via `phase=tier1_blocked` handoff.

**Pass criterion:** zero P0 findings. Tier 1 has no P1/P2 — every check is
binary.

### 7.3 Tier 2 — narrative-light (Haiku + subset of beril-adversarial classes)

A focused subset of beril-adversarial v3 detection classes that are
amenable to fast, low-context detection. Sized for Haiku to keep cost low.

**Detection-class subset (PROVISIONAL — empirical refinement at M3):**

| Class | Why Tier 2 |
|---|---|
| claim_evidence | Most-flagged in v0.5.x logs; binary detection with manifest cross-walk |
| register_drift | Fast pattern detection (informal phrases, undefined jargon) |
| qa_softball | Question-mark detection + low-novelty heuristic |
| unbacked_quantitative | Cross-walk against claim_inventory.md (already done in Tier 1; Tier 2 is the *judgment* call about whether the hedging is sufficient) |
| methods_underspecified | Cross-walk against methods_provenance.md gaps |

**Cost:** Haiku at ~10–15K input + ~5K output ≈ $0.05–$0.10 per pass.
**Cap:** 2 passes. Findings dispatch to Phase 2 rewrite (small targeted
section regeneration, not full holistic re-pass) or Phase 4 optimizer.

**Pass criterion:** zero P0 in the Tier-2 subset.

### 7.4 Tier 3 — heavy canonical (Sonnet + full beril-adversarial v3)

Full canonical adversarial review. Run once unconditionally after Tier 2
passes, then retry once if any P0 remains. The full taxonomy from
`beril-adversarial v0.7.x` (paper schema v3): all 10+ detection classes
including central_objection, citation_reality, scope_overreach, novelty_check,
substory_arc, etc.

**Invocation:** `beril-adversarial review --type paper --auto-number <draft_dir>`
(per CONTRACT.md from beril-adversarial v0.6.0+).

**Fallback when beril-adversarial absent (Q5).** **Decided 2026-05-07:**
v0.8.0 does NOT hard-require beril-adversarial. If the canonical CLI is
absent, Tier 3 falls back to an embedded `prompts/fallback_reviewer.v2.md`
(rewritten from v0.7.x's `fallback_reviewer.v1.md` to emit the same
`adversarial-review-paper.v3` schema as the canonical). Detection: shell
`command -v beril-adversarial`; on absence, `configure` warns at install
time and the orchestrator emits a stderr notice at Tier 3. The fallback is
clearly logged in `audit/cascade.jsonl` as `tier:3, reviewer:"fallback"`
so the M7 cut-over score sheet can distinguish runs that exercised the
canonical reviewer from runs that didn't. Tier 1 and Tier 2 are
self-contained (their prompts ship with the paper-writer skill); only
Tier 3 has a fallback path.

**Cost:** Canonical: $1.00–$2.50 per pass; fallback: $0.30–0.80 per pass
(lighter prompt, narrower coverage). **Cap:** 2 passes (run + 1 retry on P0).

**Pass criterion:** zero P0 across the full canonical taxonomy (or the
fallback's coverage subset, with a documented coverage gap in
`audit/cascade.jsonl`). P1/P2 findings dispatch to Phase 4 (abstract
optimizer, methods audit) or land in Limitations as accept-as-limitation
per SPEC.md §7.1.1.

### 7.5 Tier 2/3 split is empirical, not designed

**M3 prerequisite (one afternoon's work).** Before M3 starts, analyze the
v0.5.x–v0.7.x adversarial-findings JSON logs (under
`spike/beril-adversarial-skill-draft/audit/` and per-paper-writer
`papers/draft_*/reviews/`):

- For each detection class, count the P0/P1/P2 distribution across all
  reviews emitted.
- A class that mostly produces P2 in production → Tier 2.
- A class that mostly produces P0 → Tier 3 only.
- Mixed classes go to Tier 2 with escalation to Tier 3 on Tier 2 P0 surfacing.

This calibrates the split to actual failure modes rather than to a
theoretical taxonomy. The §7.3 PROVISIONAL list is M0's best guess and is
**expected to change** at M3.

### 7.6 Cascade orchestration contract

The cascade orchestrator (planned: `tools/review_cascade.py`) reads
`papers/draft_N/state.json` and dispatches the next tier based on prior tier
verdicts. Per-tier audit JSON in `papers/draft_N/audit/cascade.jsonl`:

```jsonl
{"ts":"...","tier":1,"pass":1,"verdict":"PASS","p0_count":0,"cost_usd":0.02}
{"ts":"...","tier":2,"pass":1,"verdict":"FAIL","p0_count":1,"p0_classes":["claim_evidence"],"cost_usd":0.07}
{"ts":"...","tier":2,"pass":2,"verdict":"PASS","p0_count":0,"cost_usd":0.07}
{"ts":"...","tier":3,"pass":1,"verdict":"FAIL","p0_count":2,"cost_usd":1.84}
{"ts":"...","tier":3,"pass":2,"verdict":"PASS","p0_count":0,"cost_usd":1.91}
```

State.json's `phase` advances to `selective_optimizers` only after the
cascade passes (or all tiers' caps are exhausted, in which case unresolved
P0/P1 findings dispatch to Phase 4 with explicit `[NEEDS RESOLUTION]` markers
that downstream phases preserve).

---

## 8. Phase 4 — Selective per-section optimizers

### 8.1 Two always-on optimizers

**Abstract optimizer.** A bounded prompt that tightens the Abstract using
the post-cascade manuscript as ground truth. Inputs: `manuscript.md` (the
authoritative claim source), `claim_inventory.md`, `00_story_outline.md`'s
Abstract budget. Output: replacement Abstract section. Cap: 2 passes if
the abstract optimizer's own self-review fails. Cost: ~$0.20.

**Methods reproducibility audit.** A deterministic Python pass plus a small
LLM cleanup. Reads `methods_provenance.md` and the drafted Methods section;
identifies provenance entries that did not surface in the draft and asks
whether they should be added or noted in Limitations. Cost: ~$0.10.

### 8.2 Reviewer-flagged optimizers (conditional)

If Tier 2 or Tier 3 flagged a specific section's failure class as a P1/P2
worth fixing rather than accepting-as-limitation, a targeted section
optimizer runs. The class → optimizer mapping is the M3 deliverable
(established alongside the Tier 2/3 split):

| Class flagged | Optimizer |
|---|---|
| claim_evidence on Results §K | Results-§K targeted rewrite (reads claim_inventory.md, the §K prose, the cited figures/tables) |
| register_drift in Discussion | Discussion-targeted register-pass |
| substory_arc | Outline-amendment loop (back to Phase 1; rare) |
| methods_underspecified | feeds Methods reproducibility audit (8.1) |

Optimizers are bounded ($0.20–$0.40 each, ≤2 passes).

### 8.3 Phase 4 is fundamentally smaller than v0.7.x's per-section orchestrator

v0.7.x has 8 per-section LLM calls + a reframer + a fallback reviewer. v0.8's
Phase 4 has 2 always-on optimizers + 0–4 conditional optimizers, each
narrowly scoped (≤300-line target sections). Net cost should be flat to
lower vs v0.7.x while yielding tighter targeted improvements.

---

## 9. Phase 5 — Iterative citation rounds

### 9.1 Inspired by AI Scientist; calibrated for BERIL

Per Lu et al. 2026: the citation-iteration step is the only AI-Scientist
component with empirical validation as a quality lever. v0.8 adopts the
pattern with the following BERIL-specific calibrations:

- The candidate-citation source is `pool.json` (verified-by-resolution),
  not free WebSearch. This is the single biggest quality lever — citations
  are real before they enter the loop.
- 5–8 rounds (not the AI Scientist 20). The pool is bounded; diminishing
  returns hit faster.
- Adaptive stop: 2 consecutive rounds with no new candidate citations
  added → terminate.
- Per-citation justification: the LLM emits why each new citation supports
  a specific claim_id, not just where it could be cited.

### 9.2 Per-round contract

Each round:

1. **Candidate identification.** LLM reads current `manuscript.md` +
   `pool.json` + `claim_inventory.md`; identifies up to 3 places where a
   pool entry is unused but supports an existing claim.
2. **Justification.** For each candidate, LLM emits: target_claim_id,
   target_section, proposed_citation_pool_id, justification (≤2 sentences).
3. **Integration.** A bounded edit pass to insert the citation in the
   target section.
4. **Manuscript re-check.** Tier 1 deterministic (cheap, ~$0.02) re-runs;
   if any check regresses, the round's edits are rolled back.

### 9.3 Cost target

5–8 rounds × ($0.10–$0.20 per round + $0.02 Tier-1 re-check) ≈
$0.60–$1.80 total. Bounded.

### 9.4 What Phase 5 (the iterative rounds) does NOT do

- Does NOT change manuscript claims or structure (only adds citation
  references to existing claims).
- Does NOT fix citation orphans (those are Tier-1 deterministic failures
  surfaced earlier).
- Does NOT add new pool entries on its own — pool growth is the explicit
  purview of §9.5 below, separately gated.

### 9.5 Handling `[NEEDS CITATION]` markers (Q6 design proposal)

The holistic write may produce claims for which no pool entry supports the
exact assertion. Rather than forcing the LLM to either (a) fabricate a
plausible-sounding pool match or (b) reframe the claim under pressure (both
of which sacrifice scientific honesty for pipeline cleanliness), v0.8 lets
the holistic prompt emit `[NEEDS CITATION: <topic>]` markers as a
first-class output. Disposition is tiered:

**Tier 1 deterministic counts the markers** as part of its check pass
(non-blocking warning, not a P0). Marker count is recorded in
`audit/needs_citation.json` with each marker's section + claim_id +
topic-text.

**At the Phase 5 boundary, `phase_supplementary_pool` runs** if any
`[NEEDS CITATION]` markers exist. Sub-step:

1. Group markers by topic (LLM clusters near-duplicate topics).
2. For each topic, run a bounded `citation_pool.v1`-style search
   (WebSearch + DOI/PMID verification) limited to **5 candidate
   citations per topic**.
3. Verified candidates merge into `pool.json` with a `source: "supplementary_round"`
   tag (auditable; distinguishes from seed pool).
4. Phase 5's iterative-citation rounds then run as normal — the new
   pool entries become candidates for integration.

**Bounded by:** total supplementary additions ≤ 15 entries across all
gap topics; per-topic verification cap of 5; one supplementary-pool
round per draft (no recursion). On hitting the bound, remaining
`[NEEDS CITATION]` markers are dispatched to user choice via halt:

5. Halt with `phase=citation_gap_blocked` if any `[NEEDS CITATION]`
   markers survive the supplementary round. User picks per-marker:
   - **scope-down** (the claim is reframed or dropped — sends back to
     a Phase-2 *targeted* rewrite of the affected section, scoped to
     the offending claim only),
   - **accept-as-limitation** (claim is folded into Limitations with
     a note that targeted literature engagement was attempted and
     failed),
   - **manual-citation** (user provides a DOI; verifier runs once;
     entry inserts as `source: "user_supplied"`).

**Cost.** Supplementary pool round: $0.30–0.80 (capped). Per-marker
halt-resolution: ≤$0.05 (manual; mostly file edits).

**Why this design.** It preserves the v0.8 architectural bet that
citation acquisition is mostly Phase-0 work, while accommodating the
reality that holistic write surfaces gaps the seed pool may not have
covered. The 15-entry / 5-per-topic / 1-round caps prevent the
supplementary path from absorbing the project's effective citation
discovery (which would defeat Phase 0). The halt-and-resume path
preserves user judgment on the load-bearing decisions (scope-down vs
accept-as-limitation) per SPEC.md §3.

**Open: Q6 sign-off.** Spec proposes the above. Adam's call:
- (a) Adopt as written.
- (b) Tighten — supplementary pool is too generous; cap at 5 entries / 3
  per topic.
- (c) Loosen — let supplementary run twice if first round triggered ≥10
  successful additions.
- (d) Different design — e.g., always halt on marker presence, never
  auto-supplement.

---

## 10. Phase 6 — Compliance gate

### 10.1 Build-fails-if-missing semantics

Every check in Phase 6 is binary: if any fails, the phase exits non-zero,
the orchestrator halts with `phase=compliance_blocked`, and the user gets
an explicit list of missing items. No re-attempts; this is the floor.

### 10.2 Compliance items (deterministic)

| Item | Source |
|---|---|
| ICMJE V.A AI-disclosure block present | SPEC.md §10.1 template; regex match |
| Authors / Affiliations / Funding / Conflicts / Ethics / Corresponding all populated (no `[TBD]`) | regex |
| Data-availability statement substantive (>100 chars, contains URL/accession/restriction) | M4 from SPEC.md §7.1 |
| References list integrity (every prose citation in references.md AND bibliography.bib) | M10 from SPEC.md §7.1 |
| Limitations section ≥150 chars and acknowledges all load-bearing discrepancies | discrepancy_register.md cross-walk |
| Figure embeddings resolved (every `(Fig. N)` callout has a corresponding figure file in `figures/`) | filesystem check |
| Table embeddings resolved | filesystem check |

### 10.3 Why a separate phase, not part of Tier 1

Phase 6 runs *after* Phase 5's citation rounds because citation iteration
can introduce reference-list churn that needs re-verification. Tier 1 also
checks reference resolution, but Phase 6 is the final gate before assembly
— passing here is the contract that downstream consumers (assemble, journal
submission) can rely on.

---

## 11. Phase 7 — Copy edit (clarity + concision pass)

### 11.1 Scope (Q7)

**Decided 2026-05-07 (Q7):** Phase 7 is on by default. Scope is broader
than spelling/grammar — clarity and concision are first-class objectives.
The phase may rewrite sentences for tighter scientific prose. What
distinguishes Phase 7 from Phase 4 (selective optimizers) is *semantic
preservation*: claims, citations, structure, and the manuscript's
quantitative content are invariants.

**Permitted edits.**
- Sentence rewriting at the same semantic level (tightening, voice
  conversion, breaking long sentences).
- Sentence reordering within a paragraph.
- Removing redundancy across paragraphs (echo-repetition cleanup the
  Tier-1 mechanical check flagged as advisory).
- Word-choice tightening (replacing weak verbs, scoping vague modifiers).
- Punctuation/typography normalization.

**Forbidden edits.**
- Changing any *quantitative claim* — numbers, units, ranges, p-values,
  effect sizes, CIs preserved byte-identical.
- Changing any *citation token* — `[R12]` / `(Smith 2024)` / `[doi:...]`
  preserved byte-identical.
- Changing any *hedge marker* in a way that shifts a claim's certainty
  level (e.g., "may" → "does"; "associates" → "causes" is forbidden).
- Adding or deleting paragraphs.
- Reordering at the section or subsection level.
- Adding new claims or citations.

### 11.2 Semantic-preservation post-check (NEW — Q7)

The diff-cap from the prior spec (≤10% line change) is too coarse for the
broader scope; clarity rewrites can legitimately touch most paragraphs.
Replaced with a **per-claim semantic-invariance check** that runs
deterministically pre/post:

1. **Claim_id cross-walk.** Every `claim_id` referenced in pre-edit prose
   MUST appear in post-edit prose at the same section. (Stricter than line
   diff; checks meaning preservation.)
2. **Citation cross-walk.** Every citation token in pre-edit prose MUST
   appear in post-edit prose. Inserts forbidden; removals forbidden.
3. **Numeric token preservation.** Every numeric literal (regex
   `\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b`) in pre-edit prose MUST appear in
   post-edit prose at least as often as in pre-edit. (Allows duplicate
   removal in service of echo-repetition cleanup; forbids invention.)
4. **Hedge-marker level.** Each claim's hedge-marker count (`may`, `might`,
   `suggests`, `appears`, `candidate`, `hypothesis-generating`,
   `preliminary`, `correlates`, `is associated with`) is computed pre and
   post; per-claim level may decrease by ≤1 (cleanup) but not increase
   (no new hedge-injection) and not flip a "scoped" claim to "declarative."
5. **Section header preservation.** Every `^#`/`^##` header in pre-edit
   appears in post-edit at the same nesting level.
6. **Manuscript-level word count delta** ≤ 15% (broader than v0.7-style
   ≤10% line diff; targets concision while bounding the scope).

If any check (1)–(5) fails, the edit is rejected wholesale; the phase
halts with `phase=copyedit_invariance_violated`, the validation output
JSON is written to `audit/copyedit_invariance.json`, and the user can
either accept manually or skip Phase 7.

If only check (6) fails (word count delta too large), the orchestrator
retries with a tightened prompt budget (one retry only); on second-pass
overrun, halt with `phase=copyedit_overrun`.

### 11.3 Cost target

~$0.40–0.80 per pass on Sonnet (40K input + 20K output, broader than v0.7
spelling-pass scope). Single pass; one retry on word-count overrun only.

---

## 12. Phase 8 — Final docx

The existing v0.3+ figure-embedding pipeline plus the v0.6 tables embedding
plus `assemble_docx.py` are reused as-is. No changes vs v0.7.x's
`phase_assemble_docx`. Inputs: `manuscript.md`, `figures_manifest.tsv`,
`tables_manifest.tsv`. Output: `manuscript.docx` with embedded figures and
tables, ICMJE structure, no journal-specific styling.

---

## 13. State + handoff contract

State.json schema preserves v0.7.x's top-level fields and adds v0.8 phase
states. Schema bump: `STATE_SCHEMA_VERSION = "0.8"`. A v0.7→v0.8 migration
script is the M8 deliverable; v0.8 runs do not back-migrate v0.7.x state.

**v0.8 phase enum (state.phase):**

```
initializing → phase0_tooling → story_pick → story_pick_blocked →
holistic_write → cascade_tier1 → cascade_tier2 → cascade_tier3 →
selective_optimizers → citation_rounds → compliance → copyedit →
assembled
```

Plus the halt states: `tier{1,2,3}_blocked`, `compliance_blocked`,
`copyedit_overrun`, `halted` (catch-all).

The v0.7.x handoff JSON contract (`.handoff.json` with `phase`,
`prompt_to_user`, `resume_command`, etc.) carries forward unchanged. The
slash-command parser keeps its "always read .handoff.json" rule.

---

## 14. Cost + latency targets

| Phase | Tokens (in/out) | Model | Cost | Wall-clock |
|---|---|---|---|---|
| 0 — tooling | ~30K/15K | mixed (Sonnet citation_pool + small Opus pass for discrepancy LLM) | $0.55–1.55 | 2–5 min |
| 1 — story builder | 10K/5K | Sonnet | $0.10–0.20 | 1–2 min |
| 2 — holistic write | 50K/30K | **Opus 4.6** (Q4) | $4.00–8.00 | 4–8 min |
| 3 — cascade tier 1 | 5K/2K | Haiku (ambiguity-resolution only) | $0.02–0.05 | <30 s |
| 3 — cascade tier 2 | 12K/5K | Haiku | $0.05–0.10 | 1 min |
| 3 — cascade tier 3 | 80K/15K | Sonnet (canonical adversarial) | $1.00–2.50 | 3–6 min |
| 4 — selective optimizers | 20K/10K | Sonnet (targeted), Opus only for abstract | $0.40–1.00 | 2–4 min |
| 5 — citation rounds (5–8) | 30K/10K total | Sonnet | $0.60–1.80 | 3–6 min |
| 5b — supplementary pool build (only if `[NEEDS CITATION]` triggered; see Q6) | 15K/5K | Sonnet + WebSearch | $0.30–0.80 conditional | 2–4 min if engaged |
| 6 — compliance | <5K | Haiku | $0.02 | <30 s |
| 7 — copy edit | 40K/20K (broader scope per Q7) | Sonnet | $0.40–0.80 | 2–3 min |
| 8 — docx | 0 | — | $0 | <30 s |
| **Total (typical, no supplementary pool)** | — | — | **$7.50–16.00** | **22–38 min** |
| **Total (with supplementary pool, both gap-fills)** | — | — | **$8.00–17.00** | **25–42 min** |

This is roughly 1.7–2.0× v0.7.x's $4–8 / 17–25 min on
functional_dark_matter. The cost increase is concentrated in the Opus
holistic write (Q4) — the explicit accepted trade-off for the integrative-
biology quality gain that motivated v0.8.0. The cut-over gate at M7 will
measure actual cost on ibd_phage_targeting; if v0.8.0 cost lands above
1.3× *and* quality dominance on §16's metrics is not clear, the M7
go/no-go shifts toward "keep v0.7.x as default."

---

## 15. v0.7.x → v0.8.0 migration matrix

| Component | v0.7.x state | v0.8.0 disposition | Notes |
|---|---|---|---|
| `extract_methods.py` | Phase 2 extractor | KEPT (Phase 0 §4.1) | No change to extraction |
| `extract_figures.py` | Phase 2 extractor | KEPT (Phase 0 §4.3) | No change |
| `extract_tables.py` | v0.6 addition | KEPT (Phase 0 §4.4) | No change |
| `citation_pool.py` (Python tool) | builder + verifier | KEPT (Phase 0 §4.2) | No change |
| `prompts/citation_pool.v1.md` | Phase 0 LLM | KEPT (Phase 0 §4.2) | Sole prompt-side keeper |
| `prompts/plan.v1.md` | throughline candidates | RETIRED → archive/ | Phase 1 story builder absorbs the role |
| `prompts/methods.v1.md` | section drafter | RETIRED → archive/ | Holistic write absorbs |
| `prompts/results.v1.md` | section drafter | RETIRED → archive/ | Holistic write absorbs |
| `prompts/discussion.v1.md` | section drafter | RETIRED → archive/ | Holistic write absorbs |
| `prompts/intro.v1.md` | section drafter | RETIRED → archive/ | Holistic write absorbs |
| `prompts/abstract.v1.md` | section drafter | RETIRED → archive/ | Phase 4 abstract optimizer is much smaller |
| `prompts/reframer.v1.md` | drift detection | RETIRED → archive/ | Phase 0 §4.5 discrepancy_register replaces |
| `prompts/revise_throughline.v1.md` | mini-prompt | RETIRED → archive/ | Phase 1 story-amendment cycle replaces |
| `prompts/rewrite.v1.md` | rewrite loop | RETIRED → archive/ | Phase 4 selective optimizers replace |
| `prompts/fallback_reviewer.v1.md` | inline reviewer | REWRITTEN → `fallback_reviewer.v2.md` | Tier 3 fallback when beril-adversarial CLI absent (Q5); rewrites to v3 paper schema; stays in active prompts/, not archive/ |
| `prompts/figure_caption.v1.md` | LLM caption synthesis | KEPT (Phase 8) | No change |
| `validate_manuscript.py` (M1–M10) | post-draft validator | REWRITTEN as Tier 1 cascade | Logic preserved; routing and verbiage change |
| `check_throughline_glyphs.py` | plan.v1 cross-walk | RETIRED → archive/ | Phase 1 story builder absorbs glyph discipline |
| `check_data_availability.py` | M4 helper | KEPT → Phase 6 | Compliance gate item |
| `check_figures_manifest.py` | manifest cross-walk | KEPT → Tier 1 | Mechanical check |
| `check_tables_manifest.py` | manifest cross-walk | KEPT → Tier 1 | Mechanical check |
| `check_caption_provenance.py` | caption integrity | KEPT → Tier 1 | Mechanical check |
| `check_scope_coherence.py` | section drift | RETIRED → archive/ | Tier 2 register_drift class replaces |
| `check_overclaim.py` | overclaim detector | RETIRED → archive/ | Tier 2 unbacked_quantitative class replaces |
| `check_sentence_complexity.py` | language quality | KEPT → Tier 1 | Mechanical (the v0.8.0 punch-list version) |
| `check_abbreviation_discipline.py` | language quality | KEPT → Tier 1 | Mechanical |
| `check_echo_repetition.py` | language quality | KEPT → Tier 1 | Mechanical |
| `check_repair_scope.py` | rewrite-loop scope | RETIRED → archive/ | No rewrite loop in v0.8 |
| `ensemble_review.py` | (currently unused?) | RETIRED if not wired | TBD at M3 |
| `assemble_docx.py` | docx renderer | KEPT (Phase 8) | No change |
| `paper_writer.sh` | 3000+ line orchestrator | REWRITTEN | New phase enum, new dispatch table; ~1500 lines targeted |
| `state.py` | state.json schema | EXTENDED | v0.7→v0.8 migration in M8 |
| `commands/draft.py` + `continue_run.py` | CLI dispatchers | EXTENDED | New phase enum support; v0.7 flags retained for back-compat where possible |

`discrepancy_register.py` and `claim_inventory.py` are NEW (Phase 0 §4.5,
§4.6). `review_cascade.py` is NEW (Phase 3 orchestration). All other Phase
3/4/5/6/7 logic is in the existing tools or the rewritten orchestrator.

---

## 16. Cut-over gate (M7)

A/B run on `ibd_phage_targeting` through v0.7.x (current default) and
v0.8.0 (M0–M6 deliverable). Score on 6 metrics:

1. **Token cost.** Sum of all LLM-call input + output tokens. Objective.
2. **Wall-clock time.** First-byte to last-byte. Objective.
3. **Adversarial findings count after one Tier-3 pass.** Run beril-adversarial
   v0.7.x against both manuscripts with identical settings. Objective.
4. **Plan-vs-execution gap count.** Hand-audit: claims about methods that
   don't match REPORT.md. Objective (with manual labor).
5. **Citation accuracy.** 10% audit sample of citations: % that resolve to
   a paper supporting the claim they're attached to. Objective.
6. **Paper-review skill quality assessment.** Run the paper-review skill
   against both manuscripts; take the qualitative summary. Subjective but
   reproducible.

**Decision rule.** v0.8.0 must dominate v0.7.x on **≥4 of 6** metrics OR
have a documented accepted-trade-off reason for ties/regressions.

**If gate fails:** keep v0.7.x as default; ship v0.8.0 as experimental flag
(`--writer-version v0_8`); file follow-up tasks for the failed metrics. Do
NOT cut over by tradition; the gate is real.

**Reviewer pool (Q8).** **Decided 2026-05-07:** for v0.8.0 the M7
go/no-go is Adam-only. Structured user-centered review — multi-reviewer,
naive-reader pass, colleague cross-evaluation — is deferred to a
post-v0.8.0 launch milestone (planned but unscheduled; not blocking M0–M8).
The intent of the deferral: M7 is a research-iteration decision, not a
public launch; broader review fits the launch event, not the cut-over.

**Sanity-check project:** functional_dark_matter (the v0.7.x calibration
project). Run v0.8.0 against it as well; expect it to pass without surprise.
If functional_dark_matter regresses materially, that's a stop-the-press
signal — the issue is more fundamental than a gate fail.

---

## 17. Milestones M0–M8

**M0 — Spec sign-off (this document).** ~600–1000 lines of `SPEC_v0_8.md`
plus a DECISIONS.md v0.8.0 entry capturing the decision frame. NO code.
End of milestone is Adam's sign-off on the spec questions in §19.

**M1 — Phase 0 NEW tools.** `discrepancy_register.py` +
`claim_inventory.py` + unit tests + smoke against `ibd_phage_targeting`.
Independently testable; no orchestrator changes yet. ~400 LOC + ~40 tests.

**M1 / M2 contract pointer (added 2026-05-07).** Phase 0's extracted
artifacts are **`methods_provenance.md` + `figures_inventory.md` +
`tables_inventory.md`** — the three markdowns produced by v0.7.x's
`extract_methods.py` / `extract_figures.py` / `extract_tables.py`. The
manifest TSVs (`figures_manifest.tsv`, `tables_manifest.tsv`) are NOT
phase_extract artifacts; they are emitted by the `results.v1` LLM prompt
during the writing pipeline and encode `paper_order_n` (throughline-driven,
post-figure-selection). M2's holistic prompt grounds against the three
markdowns; M1's downstream tools (`discrepancy_register.py`,
`claim_inventory.py` per §4.5/§4.6) take the markdowns as inputs and have
no manifest dependency. This contract was discovered via M1 §C0 CLI-surface
verification on 2026-05-07; M1_PUNCH_LIST.md §C0 was corrected
correspondingly. Carry this forward when wiring M2's prompt inputs and
when assessing any future change to phase_extract's surface.

**M2 — Holistic write + story builder.** `paper_writer_v0_8.md` (the
holistic prompt) + `00_story_outline.md` builder prompt + Phase-1
amendment loop + state.phase enum extension. Produces a draft on
`ibd_phage_targeting` end-to-end through Phase 2 (no review yet). The
opt-in flag `--writer-version v0_8` enables this path; v0.7.x default
unchanged.

**M3 — Tiered review cascade.** Tier-1 deterministic checks in
`review_cascade.py` + Tier-2 light reviewer + cascade orchestrator.
Empirical Tier-2/3 split done **before M3 starts**; results captured in a
`tier_split_analysis.md` artifact that pins the §7.3 PROVISIONAL list.

**M4 — Phase 4 selective optimizers.** Abstract optimizer + Methods
reproducibility audit + class→optimizer routing.

**M5 — Phase 5 citation rounds.** Iterative citation injector with adaptive
stop.

**M6 — Phase 6 compliance gate + Phase 7 copy edit.**

**M7 — A/B test + cut-over decision.** Score sheet on
`ibd_phage_targeting`; sanity check on `functional_dark_matter`; explicit
go/no-go decision recorded in DECISIONS.md.

**M8 — Cut-over commit.** Make `--writer-version v0_8` the default;
deprecate v0.7.x section prompts (move to `archive/`); update CLI defaults
+ slash-command markdowns + RELEASE_NOTES; ship MIGRATION_NOTES.md;
state-schema migration script.

---

## 18. Per-milestone discipline

Per `feedback_punch_list_release_pattern.md` and the broader pattern
established for v0.1.0/v0.3.x/v0.6/etc.:

- **Punch list at start of each milestone.** `M{N}_PUNCH_LIST.md` with
  Tier A/B/C structure, explicit AC per item, dep edges between items,
  smoke tests at every tier boundary.
- **Smoke test at end.** `tests/smoke/m{N}_smoke.py` validating the
  milestone's claim end-to-end. Failure of the smoke is a ship-blocker.
- **Decision-log entry.** Any non-obvious choice during the milestone goes
  into `DECISIONS.md` as a new D-N entry.
- **Memory entry.** A summary of what shipped + gotchas + what to watch
  in the next milestone, written to auto-memory under
  `project_paper_writer_v0_8_m{N}.md`. Index entry in `MEMORY.md`.

Per `feedback_cross_skill_contract_drift.md`: any v0.8 change that touches
the per-draft directory layout, the citation-pool schema, the figures or
tables manifest schema, or the state.json schema MUST file consumer-update
tasks for `beril-adversarial` BEFORE the v0.8 milestone tags. The
v0.7.x → v0.8 state migration is a known interface change (tracked at M8).

---

## 19. Decisions captured (2026-05-07)

All twelve sign-off items resolved. They become D-N entries in DECISIONS.md
on M0 commit.

| Q | Decision | Where it lives in this spec |
|---|---|---|
| Q1 | LLM-assisted discrepancy register (string-match too fragile for synonyms/paraphrase) | §4.5 |
| Q2 | Full coverage on claim_inventory; no salience filter in v0.8.0 | §4.6 |
| Q3 | Triage rolled into story builder (no separate `discovery.v1` step) | §5 preamble |
| Q4 | Default holistic-write model is Opus 4.6; +$2–3/run accepted trade-off | §6.7, §14 |
| Q5 | Keep a fallback reviewer; rewrite to v3 schema as `fallback_reviewer.v2.md`; not in archive/ | §7.4, §15 |
| Q6 | `[NEEDS CITATION]` allowed at holistic-write; bounded supplementary pool round at Phase 5 boundary (≤15 entries / 5 per topic / 1 round); halt-and-resume on residual markers | §9.5 |
| Q7 | Phase 7 on by default; broader scope (clarity + concision); semantic-invariance post-check (5 hard invariants + ≤15% word-count delta) replaces line-diff cap | §11 |
| Q8 | M7 cut-over reviewer pool: Adam only; user-centered review deferred to a post-v0.8.0 launch milestone | §16 |
| Q9 | v0.8.0 IS the cut-over after M7 passes; no parallel-track v0.9.0 release shape | §17 M8 |
| Q10 | Archive layout: directory-layout interpretation — `prompts/archive/v0_7/` mirrors active `prompts/` filenames; resurrection is path-mechanical; no separate PROVENANCE.md file (git history is the canonical record) | §15 + (M1 layout) |
| Q11 | Old V0_8_0_PUNCH_LIST.md → renamed `archive-v0_8_language_quality_punch_list.md` (executed 2026-05-07) | (done) |
| Q12 | Keep SPEC.md as the v0.1 baseline; SPEC_v0_8.md as v0.8 spec; consolidate at v1.0 | (status quo) |

M0 is complete on these decisions. M1 (`discrepancy_register.py` +
`claim_inventory.py` + tests + smoke against ibd_phage_targeting) is
unblocked. The 12 decisions above will land in DECISIONS.md as D-034
through D-045 (or the next available range) on M0 commit.

---

## 20. Pointers

- **Architecture decision frame:** auto-memory entry
  `project_paper_writer_v0_8_architecture.md` (2026-05-07).
- **Prior art:** AI Scientist (Lu et al., *Nature* 2026,
  doi:10.1038/s41586-026-10265-5) for citation iteration + reviewer
  ensemble; the IBD one-shot exercise (2026-05-06/07 conversation) for the
  holistic-write empirical comparison.
- **Sister-skill contract:** `beril-adversarial` v0.7.0+ (paper schema v3,
  per `project_adversarial_v0_7_x.md`). Tier 3 of the cascade is the
  canonical adversarial CLI subcommand `beril-adversarial review --type paper`.
- **Reference projects:** `ibd_phage_targeting` (M7 A/B target);
  `functional_dark_matter` (sanity-check second project; STRONG-tier
  baseline).
- **v0.7.x state:** `RELEASE_NOTES_v0_7_1.md`, `RELEASE_NOTES_v0_6.md`,
  `LAYOUT.md`, `DECISIONS.md` D-001 through D-033+. Auto-memory:
  `project_paper_writer_v0_7_1.md`.
- **v0.8 prior plan:** `V0_8_0_PUNCH_LIST.md` (language-quality version,
  superseded by this spec; disposition Q11).
- **Per-milestone discipline reference:**
  `feedback_punch_list_release_pattern.md`,
  `feedback_cross_skill_contract_drift.md`,
  `feedback_no_git_writes_in_sandbox.md`.

---

*This spec is the M0 deliverable. Sign-off is via Adam answering Q1–Q12;
on sign-off, the answers are recorded as DECISIONS.md v0.8.0 entries and
M1 begins. Nothing in M1+ runs before sign-off.*
