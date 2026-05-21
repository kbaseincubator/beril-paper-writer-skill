# Staged Improvement Plan — paper-writer post-M2 implementation

**Filed:** 2026-05-11. **Status:** active plan-of-record.
**Trigger:** end-to-end draft_3 run on `ibd_phage_targeting` produced a
manuscript with structural defects the Tier 3 adversarial reviewer
caught but the pipeline did not gate on, and with a Phase 4
optimizer that fabricated 95% CIs not present in REPORT.md.

**Architectural premise (preserved):** the agent-built Python
orchestrator + holistic-draft prompt + LLM-only Phase-0 extraction is
the right shape. The M1 regex-catalog work (B1.b–B1.h) was
over-engineered for this scope; the new approach is sound. The
problems are NOT architectural — they are missing validators,
broken state-machine branches, an additive optimizer that should be
subtractive, and an unwired citation pool.

**Working principle going forward:** "fix one thing, surface the next
failure, fix that, surface another" was the pattern that produced the
B1.e/f/g/h sprawl. Don't repeat it. Each stage has a bounded scope
and ships. The artifacts of the next run drive the next stage's
scope, not pre-design speculation.

---

## Diagnosis (draft_3 audit, summarised)

**What worked:**
- End-to-end pipeline produced a coherent 11,364-word IMRAD manuscript.
- Methods section accurately uses methods_provenance.md (libraries,
  versions, parameters).
- Throughline mechanism applied user revision.
- 12 figures embedded inline; all 12 disk paths resolve.
- Tier 3 adversarial review fired and surfaced 17 findings
  (12 P0, 3 P1, 1 P2, 1 info).

**What broke (ordered by load):**

1. **Phase 4 optimizer fabricated 4 CIs** (`[+0.31, +0.67]`,
   `[90.5%, 97.3%]`, `[76.1%, 95.6%]`, `[38.5%, 80.3%]`) — none in
   REPORT.md. Reviewer flagged all 6 P0 `unbacked_quantitative`.
   The optimizer's "improvements" actively degraded reviewability.
2. **No citation_pool.json produced.** `references.md` and
   `citation_map.md` empty. The 27 inline citations in manuscript.md
   are LLM-fabricated from training knowledge. Tier 3 flagged 4
   `citation_reality` findings.
3. **claim_inventory.tsv has 4 fabricated source_notebook paths**
   (~10% of unique notebooks). No post-validator caught them.
4. **`source_cell` is empty in ~95% of claim_inventory rows.**
   Reviewability chain breaks at the cell link.
5. **Zero `[Cxxx]` markers in manuscript.md.** No claim_id
   cross-walk protocol; reviewability principle has no machinery.
6. **Tier 1 deterministic review is a stub.** Phase 3 logs
   `"Tier 1: Deterministic checks pass."` and does nothing. Cheap
   checks that would have caught Tier 3's findings never ran.
7. **Pipeline didn't fail-fast on P0.** Advanced to Phase 4 with
   12 P0 findings unresolved.
8. **Phase 4 optimizer ignored the adversarial findings.** Single
   generic 1.1 KB prompt did cosmetic improvements unrelated to
   the structured findings JSON.
9. **`discrepancy_register.md` in wrong format** (JSON in code
   fences, not SPEC §4.5 markdown) AND a duplicate
   `audit_discrepancies.json` exists with different content.
10. **State machine dead branches** (`"compliance"` not in
    VALID_PHASES; `phase_rewrite` missing) prevent reaching
    Phase 6/Phase 8.
11. **`cost_so_far_usd = 0.0`** — cost tracking broken.
12. **Two `missing_section` P0** flagged the infrastructure gaps:
    reframing_log.md absent, references.md + citation_map.md empty.

---

## Six stages

Ordered by ROI on integrity. Each stage ships independently. The
artifacts of the next end-to-end run drive whether to proceed to the
next stage or revisit.

### Stage 1 — Stop the bleeding (CLOSED 2026-05-11)

**Closure result.** draft_4 on `ibd_phage_targeting` cleared every
Stage 1 success criterion:

| Check | Result |
|---|---|
| Pipeline reached `assembled` | ✅ |
| `manuscript.docx` produced (59,860 bytes) | ✅ (vs absent in draft_3) |
| `cost_so_far_usd = $3.99` ($2.64 Opus draft + $0.75 optimize + $0.30 revise + $0.30 plan) | ✅ matches SPEC §6.7 $4–8 ballpark |
| Optimizer post-check: `new_numerics_count = 0`, `suspect_count = 0` | ✅ subtraction-only working |
| All 4 draft_3 fabricated CIs removed from draft_4 | ✅ |
| Tier C validator: 17 rows / 3 unique invalid notebooks caught | ✅ |
| Compliance gate caught "Missing Data Availability" → autofix → assemble | ✅ |
| Handoff-printer fix (Tier B addendum): enables `continue` invocation | ✅ |

**Stage 1 also surfaced two issues that go to Stage 2:**

1. **`supplementary_pool` runs before optimizer** in the pipeline.
   `phase_supplementary_pool` checked for `[NEEDS CITATION]` markers
   at 19:24, found none, skipped. Optimizer ran at 19:34 and (per
   Tier A spec) inserts `[NEEDS CITATION: <topic>]` markers for
   `citation_reality` findings. Those markers are now orphaned in
   the final manuscript with no resolution attempt. Fix: either move
   `phase_supplementary_pool` AFTER `phase_optimize`, or run it
   twice. Goes to Stage 2 §9.5 supplementary-pool wiring.

2. **The LLM still fabricates notebook paths during extract_claims.**
   3 unique invalid notebooks across 17 rows. Validator (Tier C)
   catches them post-hoc; the marking discipline holds. Root cause:
   `extract_claims.v1.md` is too thin — no allowlist of valid
   notebook paths, no anti-fabrication anti-pattern examples. A
   B1.h-style allowlist extension would prevent the fabrication at
   source. Goes to Stage 2 or Stage 3 depending on whether citation
   work (Stage 2) demands attention first.

**Additional Tier B work that landed during the run** (not in the
original Stage 1 list, surfaced when Adam tried `continue`):

- **Handoff-printer contract drift.** The plan.v1 prompt emits
  `candidates_summary` (dict) + `next_steps` (prose). The legacy
  draft.py parser expected `choices` (array) + `resume_command`
  (string). The legacy parser silently failed because both keys
  return defaults when absent. Fix: draft.py now reads both
  schemas defensively, synthesises a resume command from
  candidates_summary keys when not explicitly provided. Lesson:
  whenever a prompt emits structured JSON for a downstream
  consumer, audit prompt-side vs consumer-side schema as part of
  any change touching either. (Same discipline as
  `feedback_cross_skill_contract_drift.md`, but applied within
  the skill.)

---

### Stage 1 (historical, before closure)
*Kept for reference. Original scope below; outcomes above.*

### Stage 1 — Stop the bleeding (≤1 day) [ORIGINAL SCOPE]

**Goal:** prevent the optimizer from making things worse, fix the
state machine so the pipeline can complete, get a clean end-to-end
run that produces a docx.

**Success criterion:** a re-run on `ibd_phage_targeting` produces
draft_4 with: (a) no fabricated CIs in the optimized manuscript,
(b) state machine reaches `assembled`, (c) a .docx file is produced,
(d) cost tracking records non-zero spend, (e) claim_inventory.tsv
has no fabricated notebook paths (any LLM-emitted fakes are flagged
in the `notes` column).

**Out of scope for Stage 1:** citation pool, Tier 1 cross-walks,
class-routed Phase 4 dispatch, `[Cxxx]` markers, multi-project
validation. Those are Stages 2–6.

Tiered punch list:

#### Tier A — Phase 4 optimizer: subtraction-only

The optimizer is currently the highest-load failure point because it
ACTIVELY DEGRADES the manuscript by inventing CIs.

- A1. Rewrite `prompts/optimizer.v1.md`:
  - Reads `audit/adversarial_review.json` (full structured findings,
    not just a "flags" path).
  - For each P0 `unbacked_quantitative` finding: locate the
    `paragraph_quote` in the manuscript and REMOVE the parenthetical
    that contains the unbacked statistic (don't invent a backed
    replacement).
  - For each `citation_reality` finding: replace the cited token
    with `[NEEDS CITATION]` (don't invent a substitute).
  - For each `missing_section` finding tagged at a content section:
    if the missing artifact is a file (reframing_log.md,
    references.md, citation_map.md), STOP and emit a handoff —
    these need deterministic population, not LLM editing.
  - **Explicit forbidden actions** (anti-pattern section in the
    prompt, per `feedback_llm_unreliable.md` discipline):
    - Forbidden: adding numbers, CIs, p-values, or effect sizes
      that don't appear verbatim in REPORT.md.
    - Forbidden: adding or modifying inline citations.
    - Forbidden: rewriting section headers.
    - Permitted: removing unbacked parentheticals; replacing
      citations with `[NEEDS CITATION]`; tightening prose flow
      around removed content.
- A2. Update `phase_optimize` in orchestrator.py:
  - Pass the actual JSON file (not the markdown one) to the
    optimizer prompt.
  - After optimizer returns, run a deterministic check: every
    numeric in the optimized manuscript that wasn't in the original
    must be in REPORT.md as a substring. If any new number isn't in
    REPORT.md, the optimizer regressed — log it as a Stage 1 ship-
    blocker and halt.

#### Tier B — State machine + Phase 8 wiring

- B1. Remove dead `"compliance"` branch from `run_pipeline`.
- B2. Decide on `phase_rewrite`: either remove the call entirely or
  add the method. Recommend: REMOVE — there's no rewrite step in
  the SPEC's 8-phase architecture (the rewrites happen via Phase 4
  optimizer). The line in `run_pipeline` is residual from an
  earlier design.
- B3. Rename either the state phase or the method so `compliance_gate`
  state correctly invokes `phase_compliance_gate`. Currently the
  `compliance_gate` branch raises `PipelineHalted` (a pause), so the
  method never runs. Decision: keep the pause as a user-handoff (the
  SPEC §10 says compliance gate is build-fails-if-missing; a pause
  before user-driven retry is fine) BUT make sure `phase_compliance`
  (the method that actually checks compliance) runs first to produce
  the diagnostics for the user.
- B4. Add `phase_assemble` that invokes `assemble_docx.py` after
  `compliance_gate` clears.
- B5. Update `VALID_PHASES` to include any phases added.

#### Tier C — claim_inventory.tsv post-validator

The B1.e validator logic is good and unused. Adapt it for the new
extract_claims.v1.md output.

- C1. Write `tools/validate_claim_inventory.py` (~80 LOC):
  - Reads the TSV produced by extract_claims.v1.md.
  - For each row: `source_notebook` non-empty AND
    `(project_root / source_notebook).is_file()`.
  - If invalid: set `notes = "unresolved-notebook: <original-value>"`
    and clear `source_notebook`. (Mark rather than reject; the
    holistic prompt has already drafted; reject would invalidate
    work upstream of the validator. Mark + flag in audit log.)
  - Emit count of rejections to stderr + to `audit/claim_inventory_validation.json`.
- C2. Wire into orchestrator `phase_triage` after the
  extract_claims subprocess returns. Run the validator before
  advancing.
- C3. NOT in scope for Stage 1: source_cell validation (vast
  majority are empty currently); source_test extraction (Stage 5
  reviewability work); figure_or_table validation (the LLM uses
  non-standard labels like `Pillar 1 #3 / §3`; needs a separate
  consumer-side decision).

#### Tier D — Cost tracking

- D1. Parse `claude -p` stderr for cost reporting. The CLI emits
  cost info; capture it. If not available, estimate by
  model+token-count (rough is fine for now).
- D2. Increment `state.cost_so_far_usd` after each subprocess.
- D3. Verify `_check_circuit_breaker` actually fires when
  `max_cost_usd` is exceeded.
- D4. Optional Stage 1 stretch: per-phase cost telemetry in
  `state.json` so audit can show "phase X cost $Y."

#### Tier E — Dead code cleanup decision

- E1. Decision: `llm_client.py` + `config.py` are dead code (the
  orchestrator never imports them; everything goes through
  `claude -p` subprocess). Options:
  - (a) Delete them. Reduces surface area; the agent's
    multi-provider intention is lost.
  - (b) Keep them but document them as "alternative LLM path for
    future M2.x work; currently unused."
  - Recommendation: **(b)** for now. Keep + comment them as
    forward-deployed. Defer the decision until a phase emerges that
    needs the multi-provider abstraction. Do not invest more in them
    until a consumer emerges.
- E2. Decision: `claim_inventory.py` + `discrepancy_register.py`
  (B1.b–B1.h work). Options:
  - (a) Delete the Python tools, keep only the prompts +
    orchestrator path.
  - (b) Keep them as importable libraries; their validator logic
    is reused in Tier C above.
  - Recommendation: **(b)** — rescue the validator logic. Mark the
    `main()` CLI entry points as "M1-deferred path; prefer
    orchestrator's prompt-driven flow." Tests for those tools
    continue to run as a regression net.

#### Tier F — Stage 1 end-to-end smoke

- F1. Re-run the orchestrator on `ibd_phage_targeting` from a fresh
  draft directory (draft_4). Compare against draft_3.
- F2. Verify:
  - state.json `phase = "assembled"`.
  - manuscript.md exists and has no NEW fabricated CIs vs draft_3
    (the Abstract should now match REPORT.md or have `[REMOVED:
    unbacked CI]` markers).
  - .docx file exists at the expected path.
  - claim_inventory.tsv has the 4 fabricated notebooks flagged in
    `notes` column.
  - state.json `cost_so_far_usd > 0`.

---

### Stage 2 — Make the citation pool work (CLOSED 2026-05-12)

**Closure result.** draft_8 closed the reviewability chain for
citations:

| Check | Result |
|---|---|
| Pipeline reached `assembled` | ✅ |
| `manuscript.docx` produced (60,846 bytes) | ✅ |
| Cost (8.34 USD) | ✅ lower than draft_7's $9.15 |
| citation_pool ran clean (no filter block) | ✅ 48 entries built, $0.92 |
| **Pool keys normalized: 48/48** | ✅ Tier M deterministic post-step |
| Holistic_draft used [key] form (42 unique) | ✅ zero (Author,Year) leakage |
| Supplementary appended new entries to pool | ✅ 48 → 52 (4 new from WebSearch) |
| **Orphans (in prose, missing from pool): 0** | ✅ **chain integrity proven** |
| `citation_reality` findings ≤ 1 | ⚠️ **5** — not the structural fabrication mode draft_3 had; now semantic attribution fidelity, not chain integrity |

**The Stage 2 architectural goal — every manuscript citation
mechanically resolves to a pool entry — is achieved.** The 5
remaining `citation_reality` findings are about whether each
attribution is semantically correct (e.g., "[Arumugam2011] showed X"
where the paper actually showed Y), not whether the chain exists.
That's Stage 3's "Tier 1 deterministic cross-walks" territory.

**Stage 2 went through 5 iterations (drafts 4–8).** Each surfaced a
real bug the previous one masked. The pattern: drafts 4–5 had the
mechanics of subprocess invocation; draft 6 exposed the content-
filter recovery problem; draft 7 exposed the schema-mismatch on
the `key` field; draft 8 closed the chain. Cost across all 5 runs
≈ $40. That's the price of iterating on a tightly-coupled pipeline
without strong upfront contract specs.

**Carryover into Stage 3 backlog:**

1. **Compliance Data Availability autofix loop** (Tier N, deferred):
   compliance gate flags every run; autofix runs but the post-fix
   state isn't re-checked. Either loosen the detector (also accept
   "data are available" / "Code Availability" / explicit URL section
   headers), or re-check post-autofix and surface if the autofix
   didn't actually fix.
2. **LLM keeps fabricating ~4 notebook paths in extract_claims**
   (NB07_v18_class_enrichment.ipynb, NB07a_pathway_DA.ipynb, etc.).
   Tier C validator catches; never propagates to manuscript. But
   visible in every run. Fix at extract_claims prompt with a
   B1.h-style allowlist of project-disk-resident `.ipynb` files.
3. **citation_reality findings now reflect semantic fidelity.**
   Need a Tier 1 check verifying each [key] is attributed to a
   claim the paper actually supports. Hard problem; needs
   claim_inventory ↔ citation mapping at draft time.
4. **Supplementary_pool occasional "Stream idle timeout"** (draft_7;
   non-recurring on draft_8). Add bounded retry if it recurs.
5. **`audit_discrepancies` writes JSON-in-code-fences to
   `discrepancy_register.md`** (Stage 1 observation, never closed).
   Schema is wrong relative to SPEC §4.5's markdown form. Either
   fix the prompt or accept the JSON form.
6. **Test files `tests/unit/test_{llm_claim_inventory,
   llm_discrepancy_register,orchestrator}.py`** remain broken
   (import errors). Either fix or delete.

---

### Stage 2 (historical, before closure)
*Kept for reference. Original scope below; outcomes above.*

### Stage 2 — Make the citation pool work (≤2 days) [ORIGINAL SCOPE]

**Trigger:** Stage 1 ships and the artifacts confirm the optimizer
no longer fabricates. Then citation_reality findings become the
load-bearing failure class.

**Goal:** the 27 inline citations in manuscript.md must come from
a verified-by-resolution pool, not from LLM training knowledge.

Deliverables:
- citation_pool.v1 actually runs and produces citation_pool.json.
- holistic_draft.v1.md prompt forbidden from inventing citations;
  uses only entries in citation_pool.json or emits `[NEEDS CITATION]`.
- Post-Phase-2 deterministic population of references.md + citation_map.md
  from citation_pool.json (NOT LLM emission).
- Tier 3 adversarial `citation_reality` findings on the next run
  should drop ≥80%.

### Stage 3 — draft_9 BERIL in-situ regression cluster (CLOSED 2026-05-12)

**Trigger.** draft_9 was the first run via the BERIL
`/beril-paper-writer` slash command (production path) rather than the
developer CLI. It surfaced a cluster of regressions — some genuinely
new, some latent since v0.7.0 and only now made visible. This stage
**preempted the originally-planned Stage 3** (Tier 1 deterministic
review, now renumbered Stage 4 below): the regression cluster had to
clear before deterministic cross-walks could be built on a
trustworthy pipeline.

**Closure result.** Eleven tiers shipped; 965 unit tests pass (51 new
this stage: 24 in `test_orchestrator_stage3.py`, 27 in
`test_validate_claim_inventory.py`); end-to-end figure-embed verified
on a re-render of draft_9; pipeline reached the throughline-pick gate
on a live foreground run (draft_13, plan+triage clean, tier STRONG)
and a subsequent full live run completed end-to-end ($12.88 continue
+ later a $53.70 deep run; the $53.70 surfaced Tier K's need).

| Tier | Fix | Verification |
|---|---|---|
| A | Figure staging in `phase_assemble` — symlink `<project>/figures/` → `<draft_dir>/figures/` so the renderer's relative-path contract resolves (copy fallback when symlink fails) | draft_9 re-render: 19/19 figures embedded, 0 `FIGURE MISSING` |
| B | `holistic_draft.v1.md` — pinned the bare `![alt](figures/X.png)` image-block form; anti-pattern callout for the blockquote form that silently dropped every figure since v0.7.0 | prompt review |
| C | `supplementary_citations.v1.md` + `holistic_draft.v1.md` — reversed a schema-directionality bug (pool array key is `entries`, not `citations`) | verified vs `citation_pool.v1.md` + on-disk draft_8/9 pools |
| D | `state.tier` population — `phase_plan` calls the canonical extract-tier regex against `throughline_candidates.md`; was `None` every run, defaulting the adversarial reviewer to EXPLORATORY | extract-tier returns STRONG for draft_8/9 |
| E | end-to-end figure-embed re-render verification | draft_9 → `manuscript_staged.docx`, 19 PNGs in `word/media/` |
| F | slash-command markdown audit — `beril-paper-writer.md` brought into v0.7.x alignment (phase sequence, handoff-schema tolerance, removed dead "v0.2 rewrite loop" framing) | full-file review |
| G | **source_notebook regression trigger** — `phase_triage`'s claim-extraction + discrepancy-audit `claude -p` calls had no `--model` flag and bypassed `_run_claude_p_with_cost`; an unpinned model resolves differently CLI vs nested-Claude-Code. Routed both through the cost helper with `model=self.model` | both calls now pinned + cost-tracked |
| H | `extract_claims.v1.md` — explicit `source_notebook` exact-filename rule + worked counter-example (the amplifier fix) | prompt review |
| I | `validate_claim_inventory.py` — conservative repair pass (notebook-ID match + missing-extension); rewrites `source_notebook` to the full real filename on unambiguous match | reconstructed draft_9: 183/191 repaired, 8 correctly stay cleared |
| J | **`claude` CLI resolution** — `resolve_claude_bin()` resolves `claude` to an absolute path at orchestrator init (`BERIL_CLAUDE_BIN` override → PATH → well-known locations incl. nvm), fails loud at init; all 4 `claude` call sites use `self.claude_bin`. Plus `draft.py`: the documented `projects/<id>/` path fallback (never implemented) + a stillborn-dir hint on a pre-flight `RuntimeError`; `configure.py` reports the same resolution the orchestrator uses | live test (draft_10–13): backgrounded `beril-paper-writer` raised `FileNotFoundError: 'claude'`, foreground worked — bare-name PATH lookup is launch-context-dependent; absolute path is immune |
| K | **`beril-adversarial` resolution + loud-warn fallback** — `resolve_adversarial_bin()` parallels Tier J but returns `None` instead of raising (adversarial is required-by-default-with-fallback, not hard-required). Orchestrator `__init__` resolves and logs a loud WARNING upfront when canonical is missing and `--no-adversarial` wasn't passed. `phase_review` Tier 3 branches three ways (canonical via absolute path / fallback-with-warning / explicit `--no-adversarial` opt-out). New `_run_fallback_reviewer()` invokes `fallback_reviewer.v1.md` via the cost helper. New `audit/review_mode.json` records which reviewer ran (canonical / canonical-failed / fallback / fallback-failed / none). `--no-adversarial` flag plumbed through `draft.py` AND `continue_run.py` (continue was previously ignoring it). | live test (draft_1, $53.70 run): canonical reviewer didn't fire despite being on PATH — bare-name spawn class. Tier K's absolute-path resolution removes the variable; the loud warning + audit/review_mode.json makes the lighter-review state impossible to miss going forward. |

**Root cause of the headline regression (source_notebook).** The
presentation-maker team flagged draft_9's claim_inventory validator
clear-rate at 76% (191/250) vs the ~10% steady-state band of
draft_4–8. The trigger was Tier G: an unpinned `claude -p` model call.
`extract_claims.v1.md` was untouched between draft_8 and draft_9 (mtime
2026-05-10); `methods_provenance.md` was byte-identical across both
drafts. The only variable was execution context — draft_8 from a plain
shell, draft_9 from the nested Claude Code slash-command session — and
an unpinned model resolves differently between the two. Tier G fixes
the trigger; H is the prompt-side amplifier fix; I is the deterministic
backstop. Side effect of Tier G: the two phase_triage calls now also
report cost, closing a hole where draft_9's $7.42 undercounted.

**The "figure insertion" regression was latent, not new.** Tier A's
investigation found draft_8 also shipped zero embedded figures — its
holistic-draft LLM happened to wrap image markdown in blockquotes
(`> ![...]`), which the renderer silently treats as prose. draft_9's
LLM emitted the bare form, which the renderer *does* recognize — and
then failed to resolve the path, making the latent bug visible. The
figure-embed loop had never actually worked in v0.7.x.

**Tier J was surfaced by the live in-situ test (draft_10–13).** The
first attempts to run the pipeline via the BERIL slash command failed
before the first LLM phase with `FileNotFoundError: 'claude'`. The
orchestrator spawned `claude` by bare name, relying on a PATH lookup
at spawn time; backgrounded under Claude Code's Bash tool that lookup
failed, while the identical foreground command succeeded. The fix
resolves `claude` to an absolute path at orchestrator init so the
spawn is launch-context-independent, and fails loud at init rather
than as a bare exception several phases deep. The same investigation
caught two adjacent defects: `draft.py` never implemented the
`projects/<id>/` project-id fallback its own help text documents, and
failed pre-flight runs were silently burning draft numbers
(draft_10/11/12 were stillborn). Both fold into Tier J. The live
foreground run then reached the throughline-pick gate cleanly
(draft_13) — but on pre-Stage-3 installed code; a genuine Stage-3
verification run requires `pipx install --force` of this commit.

**Model-tier default flipped to Opus (2026-05-12).** Alongside the
tiers above: `self.model` — which drives the reasoning-heavy phases
(plan/throughline generation, triage/claim-extraction + discrepancy
audit, the optimizer) — now defaults to `claude-opus-4-6` instead of
Sonnet 4.5. The holistic draft was already Opus; the scaffolding
phases were silently defaulting to Sonnet, which is backwards: plan
and triage are the most load-bearing decisions in the pipeline and
where draft_9's source_notebook regression occurred. The Tier-2 light
review stays on Haiku by design. `--model` still overrides (e.g. to
Sonnet for a cheap iteration pass). Cost is being measured this
cycle, not optimized — revisit per-phase tiering once there's data.

**Closes from the Stage 2 carryover backlog:**
- Item 2 (LLM fabricates ~4 notebook paths in extract_claims) — closed
  by Tiers H+I. Note the failure *mode* widened in draft_9 (bare stems,
  em-dash placeholders, 35 unique) before being fixed.

**Carryover into Stage 4 backlog:**
- `.handoff.json` schema is not pinned anywhere — `plan.v1.md` has the
  LLM improvise the shape every run; three consumers (plan.v1,
  draft.py, the slash-command) each expect a different schema. Filed.
- `beril-paper-writer-continue.md` not yet audited for v0.7.x drift
  (Tier F covered only the main slash-command). Filed.
- `state.json.source_artifacts` ships `[]` despite being declared in
  the LAYOUT schema and referenced by the AI-disclosure template;
  populating it would give a cross-skill drift-detection surface
  (presentation-maker team request). Filed, lower priority.
- Stage 2 carryover items 1, 3, 4, 5, 6 remain open.

**Cross-skill note.** presentation-maker v0.4 M1 vendored
`extract_claims.v1.md` + `validate_claim_inventory.py` byte-portable
for its no-paper originate path. Tiers H+I change both; the
`validate_claim_inventory.py` diagnostic JSON gained two additive
fields (`rows_repaired_this_run`, `repaired_notebooks`) and the
validator now *rewrites* `source_notebook` on an unambiguous repair
rather than only clearing. presentation-maker to re-vendor from our
tree post-tag. The Tier G model-pin is paper-writer-internal — not in
the vendored files — but the same unpinned-`claude -p` trap exists on
their originate path; flagged to them.

---

> **Stage renumber note (2026-05-12).** The original plan numbered the
> next four stages 3–6. The draft_9 regression cluster above took the
> Stage 3 slot in execution order, so the originally-planned Stage 3
> and beyond are renumbered +1 (now Stages 4–7). Scope and triggers
> are unchanged; only the labels shifted.

### Stage 4 — Real Tier 1 deterministic review (≤2 days)
*(originally Stage 3)*

**Trigger:** Stages 1 + 2 + 3 ship. Most P0 fabrications are gone.
Then Tier 1's missing layer becomes the load-bearing gap.

**Goal:** the cross-walks from M1_M2_CONTRACT_DRAFT.md §4.1, but
narrowed to what's needed:

- Numeric grounding (every numeric in manuscript appears in
  REPORT.md or claim_inventory.tsv).
- Citation resolution (every inline cite resolves to references.md).
- Figure callout resolution.
- Discrepancy acknowledgment (load-bearing discrepancies surface in
  Methods or Limitations).
- Source_notebook resolution (claim_inventory side, already done in
  Stage 1 Tier C; repair pass added in Stage 3 Tier I).

Each is regex + file-check; total cost ≤$0.02 per pass.
Fail-fast: any P0 deterministic finding halts before Tier 2 fires.

### Stage 5 — Fail-fast Phase 4 dispatch (≤2 days)
*(originally Stage 4)*

**Trigger:** Stage 4 produces structured deterministic findings.
Phase 4 needs to consume them.

**Goal:** SPEC §8.2 class → optimizer routing actually works.

Deliverables:
- Findings dispatch table.
- Per-class optimizer prompts (small, narrow scope each).
- Bounded passes; halt on second-pass failure.

### Stage 6 — Reviewability scaffolding (~1 week, deferrable)
*(originally Stage 5)*

**Trigger:** Stages 1–5 ship. The pipeline runs reliably; the
remaining quality gap is auditability.

**Goal:** the chain Adam articulated. `[Cxxx]` markers in prose;
claim_inventory → methods_provenance bridge; `source_test` column;
bidirectional consistency check. Per M1_M2_CONTRACT_DRAFT.md
sections 2–5.

### Stage 7 — Multi-project validation (~3 weeks elapsed, ~$70-110 LLM)
*(originally Stage 6; expanded into the v1-MVP path 2026-05-18)*

**Trigger:** Stage 4 closed 2026-05-18 (Tier R + T + S shipped).
Stages 5 + 6 deferred to v1.1 per Adam 2026-05-18; only the
partial-Stage-6 work (`[Cxxx]` markers in prose + marker
validator) ships as part of v1-MVP.

**Goal:** validate that the pipeline holds across BERDL projects
beyond the two STRONG-tier 100KB+ outliers we've tested
(functional_dark_matter, ibd_phage_targeting). The MVP scope is
**6 projects (3 dev + 3 holdout)**, not the full 5/5/~50 split —
wild-set goes to v1.1.

#### v1-MVP success criteria

**Locked 2026-05-18 (original):**

```python
PASS_CRITERIA = {
    "reached_assembled":       True,
    "min_validators_pass_or_na": 8,
    "max_p0_findings":         5,
    "max_cost_usd":            10.0,
}
```

**REVISED to v1-bar v2b, 2026-05-20** — after the 6-project dev +
holdout campaign. Encoded in `smoke-test/stage7/collect_metrics.py`:

```python
PASS_CRITERIA = {  # v1-bar v2b
    "reached_measurement_point":     True,   # p0_review or assembled
    "max_tier_t_ungrounded":         5,      # deterministic, post-#41
    "min_claim_marker_resolved_pct": 100,    # deterministic
    "max_cost_usd":                  10.0,
}
# adversarial_p0: ADVISORY (soft-flag > 8), NOT gating.
```

Why the bar moved (full rationale in `collect_metrics.py` docstring
and v2a/v2b commit messages):

- `reached_assembled` → `reached_measurement_point`. The harness
  stopped auto-remediating (2026-05-19); the pipeline pauses at
  p0_review and never self-reaches `assembled`. The honest
  "ran far enough to measure" equivalent is "reached p0_review."
- `min_validators_pass_or_na` → **dropped**. Validators run only
  post-assemble; the measurement point is pre-assemble — structurally
  unmeasurable at the bar's point.
- `max_p0_findings` (combined adv + Tier T) → **only the
  deterministic leg gates**: `max_tier_t_ungrounded` (trustworthy
  post-#41 / D-052). Adversarial P0 is **advisory** — the holdout
  campaign showed genuine first-cut adversarial P0 of 3-7 with no
  clean good/bad threshold and ±2-5 sampling variance (#37); gating
  a v1 criterion on a noisy LLM-opinion count is not defensible.
- `min_claim_marker_resolved_pct = 100` added — deterministic; all
  six campaign projects hit 100%.
- The `INCONCLUSIVE` verdict (v2a) is retired — it only existed to
  stop a malformed adversarial JSON from claiming a false PASS while
  adversarial gated; with adversarial advisory it is moot.

**SHIP v1** when ≥2/3 holdout projects meet all four v2b criteria
(per the Phase D gate below) — original "3/3" rule reads against
the revised bar.

#### Locked project set (v0.8.x snapshot of the BERDL catalog)

Selected for diversity across scientific shape × REPORT size ×
estimated tier. Both fdm and ibd are EXCLUDED — they are 100KB+
outliers, not representative. Source: `project_set.tsv`.

| Set | Project | REPORT KB | NBs | Shape | Tier estimate |
|---|---|---|---|---|---|
| dev | conservation_vs_fitness | 11 | 4 | conservation/fitness comparative | THIN |
| dev | amr_pangenome_atlas | 20 | 7 | pangenome atlas | STRONG-leaning |
| dev | phb_granule_ecology | 38 | 6 | ecology | STRONG |
| holdout | respiratory_chain_wiring | 14 | 5 | biochemistry mechanistic | THIN-STRONG |
| holdout | adp1_triple_essentiality | 52 | 5 | single-organism deep dive | STRONG |
| holdout | metal_specificity | 18 | 4 | metals mechanism | STRONG-leaning |

#### Phase plan

**A — Pre-flight (DONE 2026-05-18, no LLM).** Harness built under
`smoke-test/stage7/`:
- `project_set.tsv` — single source of truth for the 6 projects.
- `run_project.sh` — unattended driver: `draft → continue --pick TL1
  → continue --remediate --max-remediate-cycles 1`. Auto-detects
  pre-Tier-S installs and refuses. Tees full output to
  `smoke-test/stage7/runs/<project>_<ts>.log`.
- `collect_metrics.py` — per-draft metrics dataclass with auto-
  discovery. Encodes PASS_CRITERIA. Read-only against state.json
  + audit/.
- `aggregate.py` — multi-project results table → `smoke-test/
  stage7/results/stage7_results_<ts>.md` with v1 ship/no-ship
  decision baked in.

**B — Stage 6 partial: `[Cxxx]` markers in prose (3-4 days, ~$10-15
LLM).** PENDING.
- Update `holistic_draft.v1.md` to emit `[Cxxx]` markers inline
  after each quantitative claim and each assertion-shaped
  sentence. Pointers resolve to `claim_inventory.tsv.claim_id`.
- New validator `tools/check_claim_markers.py`. Pattern mirrors
  `check_numeric_grounding.py`. Schema: `claim-marker-check.v1`.
  Severity: **P1 in v1** (advisory; doesn't gate). Promote to P0
  in v1.1 if data warrants.
- Wire into `phase_review` Tier 1.
- ~15 unit tests + 1 smoke test on `ibd_phage_targeting/draft_2`.
- Defers: methods_provenance bridge + `source_test` column +
  bidirectional consistency check — all v1.1.

**C — Dev runs (1-2 weeks, ~$30-60 LLM).** PENDING.
- Run D1 (conservation_vs_fitness) → D2 (amr_pangenome_atlas) →
  D3 (phb_granule_ecology).
- **Patching rule** (locked 2026-05-18 by Adam): patch only
  *systematic* failures (prompt misunderstands a tier, validator
  has wrong assumption about project shape). Project-specific
  quirks → document as known limit in README; no patch.
- Stabilize: re-run D1+D2 with patches after D3, confirm no
  regressions on baselines (fdm + ibd).
- **Gate to phase D:** total dev spend < $80; ≤ 5 patches across
  the 3 projects. Higher → pause and reconsider.

**D — Holdout runs (2-3 days, ~$30 LLM).** DONE 2026-05-20.
- Ran H1+H2+H3 BLIND. No skill patching during the phase (harness
  fixes only — see campaign results).
- **v1 ship gate:** 3/3 holdout pass → ship v1. 2/3 → post-mortem,
  Adam decides ship vs hold. ≤1/3 → don't ship v1.

#### Campaign results (Phases C+D, 6 projects, complete 2026-05-20)

All six projects evaluated under **v1-bar v2b**. Dev set D1/D2/D3
ran pre-#41 under the old auto-remediate harness; their Tier T was
re-evaluated post-#41 by deterministic re-run (D-052) and their
`audit/iter_1/numeric_grounding.json` regenerated accordingly
(pre-#41 artifact preserved as `numeric_grounding.pre41.json`
sidecar). Holdouts H1/H2/H3 ran natively post-#41.

| Project | Tier T | adv P0 (advisory) | markers | cost | **v2b** |
|---|---|---|---|---|---|
| D1 conservation_vs_fitness | 3 | 3 | 100% | $5.26 | PASS |
| D2 amr_pangenome_atlas | 2 | 4 | 100% | $5.01 | PASS |
| D3 phb_granule_ecology | 0 | UNMEASURABLE† | 100% | $8.53 | PASS |
| H1 respiratory_chain_wiring | 0 | 7 | 100% | $4.85 | PASS |
| H2 adp1_triple_essentiality | 1 | 5 | 100% | $6.20 | PASS |
| H3 metal_specificity | 8 | 8 | 100% | $4.98 | **FAIL** |

† D3's first-cut adversarial JSON has unescaped inner quotes
(`feedback_llm_json_unfixable_in_parser` class; not #36). Advisory
only — does not affect the v2b verdict.

**Result: 3/3 dev PASS, 2/3 holdout PASS.** Per the ship gate,
2/3 → post-mortem, Adam decides.

**Post-mortem — the single failure (H3 metal_specificity):** fails
the deterministic Tier T axis at 8 ungrounded numerics. Root cause
is **V1_X_BACKLOG #46** — the drafter pulled predicted/hypothesis
values from `RESEARCH_PLAN.md` (`OR=2.08`, `p=4.3×10⁻¹⁶²`,
threshold percentages) into Results/Introduction as if measured.
Corroborated by two independent detectors (Tier T + adversarial).
This is a characterized, filed, *loud* drafter-discipline defect
with a clear fix path — not a mysterious generalization failure.

**Decision: ship v1 with documented limits.** The pipeline reached
the measurement point on 6/6, resolved 100% of claim markers on
6/6, stayed within cost budget on 6/6, and produced clean Tier T
on 5/6 post-#41. The lone holdout failure is a named known issue
(#46). v1.0 RELEASE_NOTES documents #46, #40, #44, #47 as known
limits. (Holdout-discovered items also closed during the campaign,
not deferred: #41 Tier T extractor, #35 reframing_log stub, #36
adversarial schema — cross-skill v0.7.0.5; plus the run_project.sh
preflight SIGPIPE harness fix.)

**E — Documentation hardening (4-8h, no LLM).** PENDING.
- RELEASE_NOTES.md — write the v1 story (Stage 1 → Stage 4 →
  partial-6 → 7).
- README.md — what the skill does, doesn't do, install steps,
  operator workflow. Document the cumulative `--max-cost-usd`
  semantic.
- SPEC.md + LAYOUT.md + DECISIONS.md — audit current state vs
  code; pin v1 contract.
- Slash-command markdowns — audit for v0.7+ drift (carryover
  from Stage 2 Tier F backlog).
- Tier S-10 UX nit: surface `cost_so_far_usd` in `p0_findings.md`.
  ~20 LOC + 1 test.

**F — Ship v1 (0 work).** Tag v1.0.0; update augmentation-stream-
plan.md; begin drafting stream-level architectural retrospective.

#### Risk register

| # | Risk | Probability | Mitigation |
|---|---|---|---|
| 1 | THIN/EXPLORATORY tier reveals prompt assumes STRONG-tier rigor | High | Iteration in C; document tier behavior in patch log |
| 2 | `--mode=report` path is broken (untested in current pipeline) | Med-high | Explicit dev run with `--mode=report` if any dev project triages to EXPLORATORY; patch in C if broken |
| 3 | Holdout reveals an unfixable generalization gap | Medium | Defer to v1.1; ship v1 with documented limit |
| 4 | Adversarial CLI silent-failure reproduces during stage 7 | Medium | Tier S-9 backstop fires; if frequent, file beril-adversarial bug with logs |
| 5 | Cost ceiling ($10/draft) breached on complex projects | Medium | May indicate the architecture isn't right for that shape; defer to v1.1 |
| 6 | Patches in C cause regressions on fdm + ibd baselines | Low | Smoke-test against both after each patch |
| 7 | Stage 6 `[Cxxx]` marker emission rate too low under Opus | Medium | Iterate B-phase prompt; if it doesn't take, drop Stage 6 from v1 |

#### Deferred to v1.1 (explicit non-goals for v1-MVP)

- **Full Stage 6:** claim_inventory → methods_provenance bridge,
  `source_test` column, bidirectional consistency check. Only
  the `[Cxxx]` markers + marker validator ship in v1.
- **Stage 5:** per-class optimizer dispatch. Tier S largely
  subsumed it.
- Multi-cycle remediation convergence study.
- Per-invocation `--max-cost-usd` flag rework (cumulative
  semantic surprises operators).
- Wild-set validation (~50 projects).
- Multi-cycle remediation patterns.

---

## Stage 1 work order (active)

Order chosen by ROI per hour:

1. **Tier A — optimizer subtraction-only.** Highest load; the
   optimizer is currently the worst-behaving phase. Without this
   fix, every draft ships with fabricated CIs.
2. **Tier B — state machine + Phase 8.** Mechanical bug fixes; the
   pipeline can't finish without these.
3. **Tier C — claim_inventory validator.** Rescue B1.e logic; ~80
   LOC; high integrity payoff.
4. **Tier D — cost tracking.** Required for operational visibility
   and for `max_cost_usd` to mean anything.
5. **Tier E — dead code cleanup decision.** Documentation only;
   defer code changes.
6. **Tier F — end-to-end smoke.** Run draft_4 on
   `ibd_phage_targeting`. Compare to draft_3.

Each tier is an independent commit. Tier F gates Stage 1 close-out.

---

## Discipline notes

- **Adam runs all live LLM tests on Mac shell** per
  `feedback_no_git_writes_in_sandbox.md`. Sandbox is for code edits
  + unit tests + reading artifacts.
- **No new top-level planning docs** per CLAUDE.md. This file at
  `spike/beril-paper-writer-skill-draft/` root is per-skill, not
  workspace-top-level — allowed.
- **No claim_id markers added to holistic_draft yet** — Stage 5
  work. Stage 1 leaves the manuscript format unchanged.
- **No new tests for Stage 1 unless they would block a regression
  detection.** The orchestrator test file is broken; fixing it is
  Stage 1 Tier E adjacency, not load-bearing.

---

## Open questions deferred to later stages

- Multi-source claims (meta-analysis aggregations) — deferred to
  Stage 5 schema work.
- `figure_or_table` column convention (LLM uses `§3`, `Pillar 1 #3`
  — not figure/table inventory entries) — deferred to Stage 5.
- Should `discrepancy_register.md` be canonical-markdown (SPEC §4.5
  format) or is the JSON-in-code-fences a tolerable interim? Deferred
  to Stage 2/3 when consumers actually read it.
- Phase 1 throughline-pick: orchestrator currently halts there for
  user input. The user revision mechanism worked in draft_3 (TL1
  selected, revision applied). Keep as-is.

---

*This document is the active plan-of-record. Update it as stages
close or scope shifts. Do not delete prior stage notes — annotate.*
