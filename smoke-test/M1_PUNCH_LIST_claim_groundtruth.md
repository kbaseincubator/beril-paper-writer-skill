# M1 §C2.b — Q2 Ground-Truth Completeness Check (`claim_inventory`)

**Filed:** 2026-05-07
**Smoke target:** `spike/beril-extended/projects/ibd_phage_targeting/REPORT.md`
**Tool under test:** `claim_inventory.py` v `0.8.0-m1-B1.abcd`
**Punch-list reference:** `M1_PUNCH_LIST.md` §C2.b
**Decision being evaluated:** D-034 Q2 — full coverage; every numeric
claim in REPORT.md gets a `claim_id`. False-negatives are the killer.

---

## 1. The Q2 contract (per §C2.b)

> Manual hand-count of numerics in `REPORT.md`:
>   - Adam (or Claude as a parallel agent) reads `REPORT.md` and produces
>     a hand-list of every numeric assertion before running the inventory.
>   - Compare hand-list vs `claim_inventory.tsv`. Compute precision +
>     recall.
>   - **Gate:** recall ≥ 0.90.
>   - If recall <0.90, identify the missed pattern class, extend B1.b's
>     regex catalog, rerun. Iterate until recall passes the gate.

The hand-list lives at `m1_claim_inventory_groundtruth.txt`. It is a
**representative sample**, not a complete enumeration of every numeric
in REPORT.md (REPORT.md is 2003 lines and contains ~250+ numerics by
crude `grep` counting). The sampling rule is: cover each B1.b regex
class with enough breadth that a class-level false-negative is
detectable, AND deliberately include patterns from four out-of-catalog
classes (correlations, log₂FC, OR, counts) to detect class-level gaps.

---

## 2. Pattern-class coverage of the hand-list

| Class                    | Patterns | Catalog status | Notes |
|--------------------------|----------|----------------|-------|
| Percentages              | 12       | In catalog (`PERCENTAGE_RE`) | All 12 are space-form (`95 %`); regex does not allow space |
| p-values (decimal)       | 7        | In catalog (`P_VALUE_RE`) | Mix of space + no-space |
| p-values (sci-notation)  | 3        | In catalog (`P_VALUE_RE` 2nd branch) | One has no dot in mantissa (`p=7e-17`) |
| n-counts                 | 5        | In catalog (`N_COUNT_RE`) | Mix of `n = 8,489` and `n=130` |
| Chi-squared              | 3        | NOT in catalog | Surface only via co-occurring p-value match |
| AUC / metrics            | 3        | In catalog (`METRIC_RE`) | Markdown-bolded value (`**0.799**`) |
| Fold-change / log₂FC     | 3        | NOT in catalog | log₂FC has no class; `14×` matches `RATIO_WITH_UNIT_RE` only with the `×` suffix |
| Correlations (r, ρ)      | 4        | NOT in catalog | r=, ρ= forms outside METRIC_RE keyword set |
| Odds ratios              | 4        | NOT in catalog | OR= form not in any class |
| Counts (X of Y, X cands) | 3        | NOT in catalog | Pure counts, no class |
| Cliff δ / effect deltas  | 1        | NOT in catalog | δ-form not in any class |
| **Total**                | **48**   | — | — |

---

## 3. Sandbox dry-run measurement (--no-llm leg, pre-flight)

Run on 2026-05-07, sandbox bash, against the dry-run TSV produced by
`claim_inventory.py --no-llm`:

```
Total patterns                : 48
In REPORT.md (curated correctly): 48
In claim_text blob (matched)  : 27
Recall (matched / total)      : 0.562
```

**Recall = 0.562. The 0.90 gate FAILS.**

### 3.a 21 misses, fully classified

| Class                     | Pattern               | Why missed |
|---------------------------|-----------------------|------------|
| percentage-with-space     | `95 %`                | `PERCENTAGE_RE = \b\d+(?:\.\d+)?%` — no `\s*` between digit and `%`. REPORT.md uses space form throughout. |
| percentage-with-space     | `80.4 %`              | Same as above |
| percentage-with-space     | `61 %`                | Same |
| percentage-with-space     | `35 %`                | Same |
| percentage-with-space     | `91 %`                | Same |
| percentage-with-space     | `83 %`                | Same |
| percentage-with-space     | `78 %`                | Same |
| percentage-with-space     | `70 %`                | Same |
| percentage-with-space     | `73 %`                | Same |
| sci-notation p-value      | `p=7e-17`             | `P_VALUE_RE` 2nd branch is `p\s*=\s*\d+\.\d+e-?\d+` — REQUIRES a dot in the mantissa. `7e-17` has no dot. |
| chi-squared (table-row)   | `χ²(6)=88.3`          | Not a regex class; hosted in a sentence whose only other numeric is `p=7e-17` (also missed). |
| log₂FC                    | `log₂FC +2.67`        | NOT in catalog. Subscript-2 (U+2082); `RATIO_WITH_UNIT_RE` doesn't include `FC` or `log₂FC` as a unit. |
| log₂FC                    | `log₂FC +5.66`        | Same |
| Pearson r                 | `r = 0.96`            | NOT in catalog. `METRIC_RE` keywords are `AUC, R^?2, RMSE, MAE` only — no `r`. |
| odds ratio                | `OR=8.1`              | NOT in catalog |
| odds ratio                | `OR=44.4`             | NOT in catalog |
| odds ratio                | `OR=14.6`             | NOT in catalog |
| count "X of Y"            | `14 of 23`            | NOT in catalog |
| count "X candidates"      | `51 candidates`       | NOT in catalog |
| ratio "X / Y"             | `3,929 / 17,672`      | NOT in catalog |
| cliff δ effect-delta      | `cliff δ = +0.50`     | NOT in catalog |

### 3.b Class-level breakdown

| Class                     | Hand-list size | Matched | Recall  | Verdict |
|---------------------------|----------------|---------|---------|---------|
| Percentage (space form)   | 12             | 3       | 0.250   | **FAIL** — regex requires no-space |
| p-value (decimal)         | 7              | 7       | 1.000   | PASS  |
| p-value (sci-notation)    | 3              | 2       | 0.667   | partial — dot-less mantissa missed |
| n-count                   | 5              | 5       | 1.000   | PASS  |
| Chi-squared (co-occur)    | 3              | 2       | 0.667   | partial — depends on p-value co-occurrence |
| AUC / metric              | 3              | 3       | 1.000   | PASS  |
| log₂FC                    | 3              | 1       | 0.333   | **FAIL** — `14× expansion` matched via × unit; log₂FC missed |
| Correlations (r, ρ)       | 4              | 1       | 0.250   | **FAIL** — only `ρ = 1.000` matched (via co-occurrence) |
| Odds ratios               | 4              | 1       | 0.250   | **FAIL** — only `OR=1.38` matched (via co-occurrence with p-value) |
| Counts (M of N)           | 3              | 0       | 0.000   | **FAIL** — entire class uncovered |
| Cliff δ                   | 1              | 1       | 1.000   | partial pass (host sentence has co-occurring numeric) |
| **Overall**               | **48**         | **27**  | **0.562** | **FAIL** |

### 3.c Why recall isn't fixable by switching --no-llm → default mode

The B1.c LLM demarcation pass is, by spec (`SPEC_v0_8.md` §4.6 + the
prompt at `prompts/claim_demarcate.v1.md`), constrained to:

* Demarcate multi-numeric sentences into one claim per numeric.
* Resolve `source_notebook` + `source_cell` via `methods_provenance.md`.

It does NOT introduce new candidate sentences. The candidate pool is
fixed by the deterministic regex sweep. Patterns whose host sentence
never triggered a regex never enter the inventory.

Therefore the ≥ 0.90 recall gate, applied to the v `0.8.0-m1-B1.abcd`
regex catalog as shipped, will **continue to fail at 0.562 in default
mode**. No re-run resolves it.

---

## 4. Q2 verdict — gate fails; recommend D-036 + B1.e patch

Per §C2.b AC: "If recall <0.90, identify the missed pattern class,
extend B1.b's regex catalog, rerun. Iterate until recall passes the
gate." Per §E1.b: "If C2.b drives a non-trivial extension to the regex
catalog ..., file D-036 documenting the catalog change with rationale."

### 4.a D-036 candidate text

> **D-036** — `claim_inventory.py` B1.b regex catalog under-covers
> patterns required to clear the §C2.b ≥ 0.90 recall gate against
> realistic BERIL project content. Hand-list of 48 representative
> numerics from `ibd_phage_targeting/REPORT.md` produces recall = 0.562
> on v `0.8.0-m1-B1.abcd`. Confirmed extractor gaps:
>
> 1. **`PERCENTAGE_RE`** rejects whitespace between digit and `%`.
>    REPORT.md uses `95 %` (space form) throughout; the catalog matches
>    only `95%` (no-space). Recommended fix: relax to
>    `\b\d+(?:\.\d+)?\s*%`.
> 2. **`P_VALUE_RE` 2nd branch** requires a decimal point in the
>    mantissa for scientific-notation p-values. `p=7e-17` and
>    `p<1e-31` do not match. Recommended fix: make the dot+fractional
>    optional: `\b[pP]\s*[<=≤]\s*\d+(?:\.\d+)?[eE]-?\d+`. Also
>    extends to `≤` (U+2264) which BERIL Methods sections use.
> 3. **No correlation class.** Pearson `r = 0.96`, Spearman `ρ`-not-
>    matched-by-METRIC_RE forms, and signed correlations like
>    `r=+0.456` are out-of-catalog. Recommended new class:
>    `r_or_rho`, regex
>    `\b[rρ]\s*[=:]\s*[+−-]?\d+(?:\.\d+)?` (Unicode minus + ASCII).
> 4. **No odds-ratio class.** `OR=8.1` etc. uncovered. Recommended new
>    class: `odds_ratio`, regex `\bOR\s*[=:]\s*\d+(?:\.\d+)?`.
> 5. **No log₂FC class.** REPORT.md uses subscript form
>    `log₂FC +2.67`. Recommended new class: `log_fc`, regex
>    `\blog[₂2]?\s*FC\s*[+−-]?\d+(?:\.\d+)?`.
> 6. **No counts/proportions class.** `14 of 23`, `51 candidates`,
>    `3,929 / 17,672` are central to UC Davis cohort claims and
>    uncovered. Defer to LLM demarcation OR add a count regex
>    `\b\d+(?:,\d{3})*\s+(?:of|candidates?|/)\s+\d+`.
> 7. **No cliff δ effect-delta class.** Used throughout NB07a/b. Defer
>    to a class extension for non-parametric effect sizes.
>
> Catalog extension is a B1.e patch on top of B1.abcd. Each new regex
> class needs a unit test in `test_claim_inventory.py` (per §B2.a's
> "one test per pattern class" rule). Re-run §C2.b smoke after patch;
> ship M1 only when recall ≥ 0.90.

### 4.b Why this isn't a hand-list curation problem

All 48 hand-list patterns were verified to appear in REPORT.md as a
literal byte-string before inclusion (`grep -F` against the file). The
21 missed patterns are genuinely in the source document but never
surface in `claim_inventory.tsv`. The first hand-list draft contained
5 list-curation errors (e.g., `p = 0.008` written with spaces when
REPORT.md uses `p=0.008`); those were corrected in revision before
this measurement. The 0.562 number is the honest measurement against
B1.b as shipped.

### 4.c Ship verdict for M1

**M1 §C2 does NOT ship until B1.e regex-catalog extension lands.**
Per §E gating ("E2 + E3 + E4 gate on Tier C smokes passing"), M2
unblock is held by Q2 recall < 0.90.

The ABLE-TO-SHIP-NOW slice is:

* C2.a smoke harness (`m1_claim_inventory_smoke.py`) is correct and
  ready; it will pass the AC sub-gates (≥ 30 claim_ids, schema valid,
  cost ≤ $0.10) on the v0.8.0-m1-B1.abcd output (87 claims emitted by
  --no-llm; LLM-assisted should match or exceed this count).
* C2.b ground-truth file (`m1_claim_inventory_groundtruth.txt`) is
  curated against REPORT.md and ready to gate the B1.e re-test.
* This report.

The HOLD-UNTIL-B1.e slice is:

* The recall ≥ 0.90 gate.
* DECISIONS.md D-036 entry (Adam to add when B1.e is decided).
* `M1_PUNCH_LIST.md` Tier C close + Tier E close.
* M2 unblock.

The B1.e patch fits the Tier-A/Tier-B template — small surface
(~7 regex changes + 7 unit tests + a re-run of this smoke). Estimated
~150 LOC + ~7 tests. Not a milestone-level rework; can land in a
single Tier-A-style patch.

---

## 4.d UPDATE 2026-05-07 — live-LLM run reveals 2 NEW C2 ship-blockers

Adam ran the smokes against live LLM. **C1 all green (smoke + ablation +
idempotency).** **C2 fully blocked** by two new defects on top of the
recall gap from §3:

### 4.d.i Validator over-strict: real notebook rejected as "fabricated"

```
error: validator rejected LLM output: validator: row 0's source_notebook
  is not a substring of methods_provenance.md (LLM may have fabricated
  the path). Got: 'notebooks/NB10a_kumbhari_strain_adaptation.ipynb'
```

`NB10a_kumbhari_strain_adaptation.ipynb` IS a real file:
`spike/beril-extended/projects/ibd_phage_targeting/notebooks/NB10a_kumbhari_strain_adaptation.ipynb`
(188 KB, modified 2026-05-06). The LLM correctly identified it as the
source notebook for H3b strain-adaptation claims (OR=1.38; p=2.4e-6 from
NB10a per REPORT.md §5/Item 4). The validator at
`claim_inventory.py:1341` rejects because **NB10a is not listed in
`methods_provenance.md`**.

**Sandbox-verified surface area of the structural defect:** of 32
`.ipynb` files under `notebooks/`, only **13** appear in
`methods_provenance.md`. **19 / 32 (59%) of the project's notebooks are
INVISIBLE to the validator's substring check** — including all of
NB07a/b/c/d (pathway DA), NB08a/b (BGC), NB09a-d (metabolomics), NB10a/b
(strain adaptation), NB11 (serology), NB12-NB17 (Pillars 4–5).

These 19 are precisely the notebooks that produced **most of REPORT.md's
quantitative claims** (Pillars 3–5 ≈ 60% of the report). The validator
will reject any LLM-emitted source_notebook for any of those 19 → the
candidate that triggered the rejection halts the entire B1.c run with
exit 4 → **zero claims emitted**.

**Root cause:** `methods_provenance.md` is the output of
`extract_methods.py`, which catalogs notebooks via AST-detected
statistical-test invocations (`scipy.stats.*`, `statsmodels.*`,
`sklearn` classifiers/metrics, etc.). Notebooks that produce results via
pandas / custom-code / SQL queries / data joins — without a named
stat-test call — are absent. The contract gap is:

* SPEC §4.6: "B1.c LLM uses [methods_provenance.md] to ground
  source_notebook + source_cell cites; validator rejects cites that
  don't appear in this file."
* Reality: methods_provenance.md is a strict subset of project
  notebooks. The intended "ground truth for cites" is project
  filesystem, not the test-only catalog.

**Recommended fix (B1.e patch surface):**

1. Pass `project_root` to `validate_demarcations` (currently only takes
   `methods_provenance_text` + `figures_inventory_text` + `tables_inventory_text`).
2. Replace the substring check at `claim_inventory.py:1341` with:
   ```python
   nb_path = project_root / e.source_notebook
   if not nb_path.is_file():
       raise ValidationError(...)
   ```
   OR accept either condition (substring OR disk file) for backwards
   compatibility with synthetic-fixture tests that don't have a real
   project_root.
3. Update `B2.d` validator unit tests: add a "real notebook on disk
   not in methods_provenance" PASS case + a "notebook not on disk"
   FAIL case.
4. Update SPEC §4.6 wording: "validator rejects cites for notebooks
   not present in `<project_root>/notebooks/` (or per project layout
   convention)."

This is a B1.e patch on top of B1.abcd, parallel to D-036 (regex
catalog extension). Both ship in the same B1.e cycle.

### 4.d.ii Cost ceiling fragile: $0.1124 > $0.1000 (12% overrun)

```
warn: demarcator call cost $0.1124 exceeded ceiling $0.1000/run
```

The demarcator overran the SPEC §4.6 cost ceiling by ~12%. Live LLM
cost was billed in full; the validator-rejection path then exited 4,
so no TSV was produced for the $0.11 spend.

**Modest overrun, but two compounding concerns:**

1. The 87 candidates from --no-llm dry-run had only 10 unresolved
   (multi-numeric). At ~$0.011 / unresolved, 10 demarcations land
   right at $0.11 — and the per-call output token count drifts on real
   prose. `ibd_phage_targeting`'s sentences are dense (multi-clause
   tables) so each demarcation produces longer outputs.
2. The `audit JSONL` records `cost_usd=0.0` on the exit-4 path (per
   A1 audit watch-for #4 generalizes to B1 — same defect). So this
   $0.11 doesn't appear in `audit/phase0.jsonl`. Reconcile against the
   stderr warning, not the audit cost.

**Recommended fix:** The simplest move is to bump the ceiling to
$0.15 in SPEC §4.6 — `ibd_phage_targeting` ran 12% over on a dense-
prose project, and `functional_dark_matter` is denser still. $0.15
keeps the cost an order of magnitude under Sonnet pricing while
avoiding the warn-then-fail dynamic. Alternative: tighten the
demarcator prompt's output budget (currently emits per-claim severity
+ rationale; reducing to claim_text + cell_index would shave ~30% off
output tokens but lose the per-claim rationale that B1.c was designed
to surface). Bumping the ceiling is the lower-risk path.

A separate v0.8.x bug to file: `emit_audit_line` should record
`cost_usd > 0.0` even on the exit-4 validator-rejection path. Same
gap as A1's watch-for #4. Probably already in the v0.8.x backlog;
amend if not.

### 4.d.iii Updated B1.e patch surface (consolidated)

| ID    | Type           | Surface                                 | LOC est. | Tests |
|-------|----------------|-----------------------------------------|----------|-------|
| D-036 | regex catalog  | 7 catalog extensions per §4.a           | ~150     | +7    |
| —     | validator fix  | `is_file()` over substring per §4.d.i   | ~30      | +2    |
| —     | cost ceiling   | $0.10 → $0.15 in SPEC §4.6 + tool       | ~5       | +1    |
| —     | audit cost-on-fail | exit-4 path emits real cost          | ~20      | +1    |
| **Total B1.e** | — | —                                          | **~205** | **+11** |

All four are independently testable; no inter-dependencies. Any one
can land first. The validator fix is the unblocker — without it, no
TSV is produced, no recall is measurable.

### 4.d.iv What ships now from this conversation's work

* C1 smoke harness — green; runbook in `M1_PUNCH_LIST_ablation_notes.md` §6.
* C1.b ablation — green; report identifies architectural gap (overlap=0).
* C3 idempotency (discrepancy leg) — green.
* **B1.e patch landed in this commit cycle** (Adam-authorized 2026-05-07):
  validator project_root fallback + D-036 regex catalog extension +
  cost-cap reframing (D-037) + exit-4 cost recording. 60/60 unit tests
  green (33 disc + 27 claim, +10 new in B1.e).
* C2 smoke harness + groundtruth file + this report — staged.

What still gates M1 close-out:

* Adam re-runs C2.a / C2.b / C3-claim-leg from Mac shell with live LLM
  to confirm post-patch behavior. Per the sandbox dry-run with B1.e
  applied, recall is **1.000 (48/48)**, so C2.b is expected to pass.
  C2.a should pass at ≥30 claims (post-B1.e dry-run produced 401).
  C3-claim-leg should pass on rerun (cache hit + byte-stable).
* Tier E close-out (memory entry + commit + M2 unblock) gated on
  Adam's confirmation of the live run.

### 4.h POST-B1.g LIVE-LLM UPDATE 2026-05-07 — LLM cite fabrication, B1.h filed

Adam ran the B1.g patch against live LLM. Batching + retry worked
(retry round 1 fired in C2.a smoke, picking up [39, 95, 118]). But
the validator caught two new fabrications:

| Run | Defect | Sample |
|---|---|---|
| C2.a | source_notebook fabricated (truncated real filename) | `notebooks/NB07a_H3a_falsifiability.ipynb` (real: `..._pathway_DA_H3a_...`) |
| C2.b | figure_or_table fabricated (notebook ID misused as figure label) | `Fig NB15` (NB15 is a notebook, not a figure) |
| C3 | same as C2.a (LLM repeats the same fabrication on rerun) | (same) |

These survive bounded retries because they're systematic LLM errors,
not non-deterministic dropouts. The model was paraphrasing the
notebook by what it does (per RESEARCH_PLAN.md framing) rather than
copying the actual filename, and treating notebook IDs as figure
labels.

**B1.h fix (filed in same commit cycle 2026-05-07).** Two layers:
1. `build_demarcator_user_prompt` extracts notebook paths AND
   figure/table labels from the input contexts, then emits them as
   explicit "VALID values" allowlists at the TOP of the user prompt
   (before INPUTS). Notebook extraction merges:
   (a) `notebooks/*.ipynb` paths mentioned in methods_provenance.md;
   (b) when `project_root` is provided, every `*.ipynb` file under
   `<project_root>/notebooks/` — so notebooks that exist on disk but
   aren't in methods_provenance.md (60% of disk notebooks on
   `ibd_phage_targeting`) are surfaced. Figure/table extraction
   handles three heading forms: short canonical (`## Fig 3`,
   `## Tbl 2`), path form (`### figures/NAME.png` — used by
   `ibd_phage_targeting`'s extract_figures.py emitter), id form
   (`### report_tbl_NN — Description`).
2. System prompt at `prompts/claim_demarcate.v1.md` gains anti-pattern
   worked examples for the two specific failure modes observed.

The validator's per-row checks remain unchanged — the allowlist is a
guide, not a gate. The validator still rejects fabrication; the
allowlist makes the LLM less likely to fabricate in the first place.

Test sweep post-B1.h: **66/66 unit tests green** (33 disc + 33 claim).
+2 new tests `TestB1hAllowlistsInUserPrompt`:
* extractor regression — pulls sorted unique notebook paths +
  ordered unique fig/tbl labels from realistic input markdown.
* user-prompt regression — both allowlist sections emit before
  INPUTS, with the anti-pattern reminders in place.

Expected post-B1.h behavior on `ibd_phage_targeting`:
* C2.a smoke → exit 0, ≥30 claims, cost ~$0.60–$0.90.
* C2.b recall → exit 0, recall ≈ 1.000.
* C3 idempotency → exit 0, cache hit + byte-stable rerun.

The Mac-shell runbook below targets B1.h state.

### 4.g POST-B1.f LIVE-LLM UPDATE 2026-05-07 — non-deterministic LLM dropouts, B1.g filed

Adam ran the B1.f patch against live LLM. The batching fix worked
(no more truncation/timeout — total run completed at $0.57 across 9
batches). But the validator caught **non-deterministic missing indices
on every run**:

| Run | Mode | Missing indices | Cost |
|---|---|---|---|
| 1 | C2.a smoke | [69, 72] | $0.5717 |
| 2 | C2.b recall | [39, 95] | (similar) |
| 3 | C3-claim-leg | [39, 72, 95] | (similar) |

Different indices on each run. Root cause: the Haiku 4.5 demarcator
intrinsically drops ~1.5–2.5% of inputs per dense-project batch as a
non-deterministic variance, not a systematic gap. Re-running covers
different rows.

**B1.g fix (filed in same commit cycle 2026-05-07).** Add a bounded
retry loop in `demarcate_unresolved_with_llm`: after the initial
batched pass, identify missing indices and re-batch them into a
fresh LLM call. Up to `max_retries=3` rounds. Residuals after retries
fall back to the original `notes='unresolved'` row via the orchestrator's
existing empty-rows pass-through; validator gains `allow_missing`
kwarg to honor the residual set. Cache schema persists
`tolerated_missing` so reruns are byte-stable. See DECISIONS.md
D-039 for full calibration.

Test sweep post-B1.g: **64/64 unit tests green** (32 disc + 32 claim).
+2 new tests `TestB1gRetryOnMissingIndices`:
* retry round recovers a one-index initial drop → exit 0, full TSV;
* persistent drop across initial + 3 retries → exit 0 with
  `tolerated_missing` in cache, original unresolved row preserved
  in TSV.

Expected post-B1.g behavior on `ibd_phage_targeting`:
* C2.a smoke → exit 0, ≥30 claims, cost ~$0.60–$0.90 (initial 9
  batches + 1–2 retry rounds).
* C2.b recall → exit 0, recall ≈ 1.000 (the residual unresolved
  rows still surface in claim_text via the original sentence and
  contain all original numerics).
* C3 idempotency → exit 0 + cache hit on rerun + byte-stable TSV.

The Mac-shell runbook below (§5) targets the B1.g-applied state.

### 4.f POST-B1.e LIVE-LLM UPDATE 2026-05-07 — two new defects, B1.f filed

Adam ran the B1.e patch against live LLM. C1 trio remained green. C2
trio surfaced two NEW defects not visible in the sandbox dry-run:

**Defect 1 — output truncation on 133-candidate single demarcator call.**
The first attempt billed $0.1856 but the LLM returned demarcations for
only 91 of 133 candidate indices; validator caught the coverage gap and
rejected with exit 4 + a missing-indices list of 42 entries. Likely
cause: effective per-response output-token cap on Haiku 4.5 at
~50K output chars.

**Defect 2 — subprocess timeout on subsequent runs.** C2.b recall and
C3-claim-leg both hit `claude -p timed out after 180.0s without
responding`. With 133 unresolved candidates × heavy demarcator prompt,
wall time exceeds the subprocess wrapper's budget. The hard-coded 180s
timeout in `claim_inventory.py:1085` is too tight for dense projects.

Both defects share a root cause: a single LLM call is asked to handle
too many candidates at once. **B1.f patch (filed in same commit cycle
2026-05-07) chunks the demarcator call** into batches of `--batch-size`
(default 15). On `ibd_phage_targeting`: 9 batches × 30s ≈ 5 min wall,
9 × ~$0.10 ≈ ~$0.90 total spend. See DECISIONS.md D-038 for the full
calibration rationale.

Test sweep post-B1.f: **62/62 unit tests green** (33 disc + 29 claim).
+2 new tests `TestB1fDemarcatorBatching` confirm batched coverage +
cache-key invalidation on batch_size change.

The Mac-shell runbook below targets the B1.f-applied state. Re-run
expected to pass C2.a / C2.b / C3-claim-leg.

### 4.e POST-B1.e UPDATE 2026-05-07 — sandbox dry-run after patch

Re-ran with the B1.e patch applied (validator project_root fallback +
D-036 regex catalog + cost-cap reframing + exit-4 cost recording):

```
python3 -m beril_paper_writer.skill.tools.claim_inventory \
    --report REPORT.md \
    --methods-provenance papers/draft_1/methods_provenance.md \
    --figures-inventory papers/draft_1/figures_inventory.md \
    --tables-inventory  papers/draft_1/tables_inventory.md \
    --output-dir /tmp/dryrun_b1e \
    --no-llm

Wrote claim_inventory.tsv (401 claim(s); 133 unresolved (multi-numeric))
```

Recall against 48-pattern ground-truth: **48/48 = 1.000.**

Cost implication of the catalog extension: 133 unresolved sentences
on this project × ~$0.011 each ≈ ~$1.50 on the live demarcator run
(15× the prior `_DEMARCATOR_COST_CEILING_USD = 0.10` constant). Per
D-037 cost reframing (Adam, 2026-05-07), the per-tool ceiling is no
longer enforced; cost is recorded in audit JSONL for later tightening
at the M2 orchestrator layer.

Unit-test sweep: 60/60 green
(`tests/unit/test_discrepancy_register.py` 33 + `test_claim_inventory.py` 27).

The B1.e change set is non-regressive on the discrepancy_register
smoke (C1.a / C1.b / C3-disc-leg all still green in sandbox) and
non-regressive on the 18 + 32 pre-existing unit tests.

---

## 5. Mac-shell runbook (Adam to confirm against live LLM)

The sandbox dry-run measured recall against `--no-llm` output. The
default LLM-assisted run should produce identical recall on the
hand-list (B1.c demarcation does not introduce new candidates; see
§3.c). Adam should still confirm against live LLM for audit-trail
completeness:

```bash
WORKSPACE=~/Documents/Claude/Projects/research-coscientist-dev
SKILL_DIR="$WORKSPACE/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$WORKSPACE/spike/beril-extended/projects/ibd_phage_targeting"
GT_FILE="$SKILL_DIR/smoke-test/m1_claim_inventory_groundtruth.txt"

# Pipx venv's Python (anthropic SDK + nbformat).
PYTHON_BIN="$(awk 'NR==1 && /^#!/ {sub(/^#!/, ""); split($0, a, " "); print a[1]; exit}' "$(command -v beril-paper-writer)")"
echo "PYTHON_BIN=$PYTHON_BIN"
"$PYTHON_BIN" -c "import anthropic, nbformat; print('deps OK')"

cd "$SKILL_DIR"
PYTHONPATH=src "$PYTHON_BIN" smoke-test/m1_claim_inventory_smoke.py \
    --mode recall \
    --project-root "$PROJECT_ROOT" \
    --groundtruth-file "$GT_FILE" \
    --staging-dir /tmp/m1-claim-recall
echo "exit=$?"
```

Expected exit 1 (FAIL: recall 0.562 < gate 0.90). The script prints
the missed-pattern list. Forward expected behavior:

1. Adam acknowledges the gate failure + reviews D-036 candidate text.
2. Adam authorizes B1.e patch.
3. Patch lands.
4. Re-run this command; expect exit 0 with recall ≥ 0.90.
5. Then E1/E2/E3 close-out + M2 unblock.

If the live-LLM run produces a recall *higher* than 0.562, that is
unexpected — investigate (the LLM demarcator may be doing something
beyond its spec; surface as a B1.c contract drift). If it produces a
recall *lower* than 0.562, also investigate (LLM demarcation should
not lose deterministic candidates).

---

## 6. Coordinates with the A1 audit's watch-fors

Watch-for #4 from `.auto-memory/project_paper_writer_v0_8_m1_a1.md`
(the cost_usd=0.0 on validator-rejection path) does NOT manifest in
this Q2 work because B1.c's demarcation does not throw ValidationError
on the deterministic-only path. Carry forward into the live-LLM rerun
diagnostics: if Adam's run shows `exit_status=4` audit lines, expect
under-reported cost.
