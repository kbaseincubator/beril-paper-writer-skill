# M1 ↔ M2 Contract — Reviewability-anchored Design (DRAFT)

**Filed:** 2026-05-09. **Status:** DRAFT for review. Supersedes the
implicit contract in SPEC §4.6 + §6.4 + §7.2 if accepted.

**Author:** Claude (Cowork) for Adam Arkin.
**Origin:** the M1 §C2 live-LLM smoke + V1/V2/V3 verification revealed
that claim_inventory.tsv had no consumer code and no consumer-side
contract. This draft anchors the artifact on a stronger principle —
**reviewability** — and derives the schema, catalog scope, cross-walk
operations, and test methodology from that anchor.

---

## 1. The reviewability principle

A scientific manuscript is **reviewable** iff a reader can traverse,
for every reported number, this chain:

```
Results prose claim ("OR=1.38, p=2.4e-6")
    ↓ via claim_id
claim_inventory.tsv row
    ↓ via source_notebook + source_cell
methods_provenance.md entry  (test type, software, version, parameters)
    ↓ via the same notebook + cell
notebooks/<name>.ipynb cell K (the actual code)
```

Every link is the contract that makes the next one meaningful.

**Reviewability is the test of M1 + M2's joint correctness.** It is
not "claim_inventory is well-formed" or "manuscript is fluent" or
"reviewer didn't flag anything." It is "an external peer reviewer
can audit any reported number without leaving the manuscript's
referenced artifacts." Anything that breaks the chain breaks
reviewability.

---

## 2. M1's job, derived from the principle

M1 produces `claim_inventory.tsv` and `discrepancy_register.md`. The
former is the load-bearing concern for the chain; the latter is
orthogonal (drives the Methods+Limitations honesty narrative; not a
chain link).

### 2.1 What goes in claim_inventory

**A row in claim_inventory.tsv represents a numeric claim that
derives from a specific computational operation cataloged in
methods_provenance.md.**

This is the catalog-scope rule. Concrete distinctions:

| In REPORT.md | Computational op? | Methods provenance? | Belongs in inventory? |
|---|---|---|---|
| `OR=1.38, p=2.4e-6` | logistic regression + Wald | yes (NB10a) | **Yes** |
| `AUC = 0.799` | sklearn classifier metric | yes (NB03) | **Yes** |
| `log₂FC +5.66` | differential abundance | yes (NB00/NB04) | **Yes** |
| `K=4 ecotypes` | model-selection (perplexity, ARI) | yes (NB01b) | **Yes** — count, but a computational output |
| `ρ = 1.000` reseq replicate | Spearman correlation | yes | **Yes** |
| `ARI = 0.113` LOSO stability | sklearn.metrics.adjusted_rand_score | yes (NB04f) — *iff extract_methods catalogs ARI* | **Yes** (subject to gap, see §7) |
| `8,489 cMD samples` | data ingestion | no — data description | **No** |
| `23 UC Davis patients` | cohort count | no | **No** |
| `73 % of patients in E1 or E3` | cohort proportion | borderline | **Yes if labeled by an analysis; No if a tabulation** |

**The line is "computational output vs data description."** A number
that came out of a test, model, classifier, correction, permutation,
or selection criterion is a claim with provenance and gets a
claim_id. A number that describes the inputs (sample size, dataset
size, cohort counts) is a Methods cohort description and is not in
this index — Methods describes those numbers as part of its
"Materials" or "Subjects" subsection.

This narrows the regex catalog from "every numeric" (Role A — the
unbounded coverage problem) to the **statistical-output sub-
language**, which is bounded and standardized by statistics journals.

### 2.2 The catalog covers statistical-output shapes

Bounded, well-defined sub-language:

| Class | Examples | Comment |
|---|---|---|
| **p-values** | `p < 0.05`, `p = 2.4e-6`, `q = 0.01`, `FDR < 0.10` | Includes adjusted forms (q, FDR, Padj) |
| **Confidence intervals** | `95% CI [0.71, 0.85]`, `90% CI`, `99% CI` | Generalized %; bracketed or hyphenated |
| **Effect-size keywords** | `OR=`, `aOR=`, `HR=`, `RR=`, `R²=`, `AUC=`, `AUPRC=`, `F1=`, `MCC=`, `Cohen's d`, `Cohen's κ`, `ARI=`, `β=`, `log₂FC=`, `cliff δ=` | Each keyword + value |
| **Test statistics** | `t(29) = 3.4`, `χ²(3) = 10.0`, `F(2,15) = 4.5`, `U = 1234`, `W = 567` | Standardized parenthetical-df form |
| **Model selection criteria** | `BIC = 1023.4`, `AIC = 980`, `perplexity = 12.3` | Numeric model-fit criteria |
| **Empirical p-values** | `empirical p = 0.000 over 200 permutations` | Permutation/bootstrap derived |
| **Concordance / replication metrics** | `88.2% sign-concordance`, `Spearman ρ = 0.79` | Composite-claim shapes |

All of these are detectable by regex. None require LLM disambiguation
to *find*. (LLM may help with sourcing — see §3.)

**Demoted (not in claim_inventory):**

- Generic percentages without statistical context (`27 % of UC Davis`).
- Bare counts (`8,489 samples`, `23 patients`).
- Concentrations / units (`16.2 mg/L MIC`) — these ARE results, but
  they're MIC/IC50/etc. measurements, not statistical-test outputs;
  they belong to Methods (assays performed) and Results (raw values),
  not the statistical-claim chain. **Open question:** should there be
  a separate "measurement_inventory" for these?

### 2.3 Each row's columns and their reviewability roles

```
claim_id  claim_text   source_notebook  source_cell  source_test   figure_or_table  effect_size_present  ci_present  pvalue_present  notes
```

| Column | Reviewability role | Required? |
|---|---|---|
| `claim_id` | The cross-walk anchor. Used in Results-prose markers (`[C012]`) and in Methods-prose grouping ("see C012, C013, C014"). | **Yes** |
| `claim_text` | The verbatim Results-prose form. Substring of REPORT.md. | **Yes, non-empty** |
| `source_notebook` | The **bridge to methods_provenance**. Must resolve to a real notebook under `<project_root>/notebooks/` AND be cataloged in `methods_provenance.md`. | **Yes, non-empty** (raises real error if missing — see §7) |
| `source_cell` | The cell index inside `source_notebook`. Must be a valid index (0..N) of that notebook. | **Yes, non-empty** |
| `source_test` (NEW) | The test type the claim derives from (e.g., `mannwhitneyu`, `chi2_contingency`, `roc_auc_score`). Must match an entry in `methods_provenance.md`. Drives the Methods-section drafting. | **Yes, non-empty** |
| `figure_or_table` | The display item depicting the claim (Methods + Results both reference it). May be empty if the claim is in-text only. | Optional, validated if non-empty |
| `effect_size_present` | yes/no — does the claim include an effect size (any of the §2.2 effect-size keywords)? | Required (yes/no) |
| `ci_present` | yes/no — does the claim include a confidence interval? | Required (yes/no) |
| `pvalue_present` | yes/no — does the claim include a p-value? | Required (yes/no) |
| `notes` | Free-text annotation. `"primary"`, `"sensitivity"`, `"replication"`, `"unresolved"` (failed sourcing), `""`. | Optional |

The `source_test` column is **new in this draft** — it makes the
methods_provenance ↔ claim_inventory linkage explicit at the row
level rather than implicit via `source_notebook + source_cell`. It
also supports the Methods-section drafting rule in §3.

---

## 3. M2's job, derived from the principle

M2's holistic-write prompt drafts `manuscript.md`. Per SPEC §6.4 it
consumes Phase-0 artifacts. Under the reviewability principle, M2's
specific contract is:

### 3.1 Methods drafting

For each notebook with claims (group `claim_inventory.tsv` by
`source_notebook`):

- Look up the notebook's entries in `methods_provenance.md`.
- Emit Methods text describing the analytical operation that
  generated those claims.
- Methods text format mirrors v0.7.x's existing `methods.v1.md`
  conventions but is now *driven by* claim_inventory groupings,
  not by free-form section synthesis.

The Methods section, as a result, has structural correspondence to
the claim_inventory: every `source_test` mentioned in inventory
shows up at least once in Methods; every notebook surfaces at least
once.

### 3.2 Results drafting

For each claim_id picked for Results inclusion (per
`00_story_outline.md`'s pillar/finding budget):

- Emit Results prose that includes the verbatim `claim_text` (or
  a substring of it).
- Tag the claim in prose with `[Cxxx]` markers.
- Apply the hedge rule: claims with all three flags = `no` MUST
  carry an explicit hedge ("our preliminary signal," "in this
  cohort," "exploratory finding," etc.).

`[Cxxx]` markers are **the load-bearing protocol artifact for
reviewability**. They make the cross-walk mechanically checkable.

### 3.3 The forbidden actions remain (per SPEC §6.6)

- No inventing numeric claims (every `[Cxxx]` resolves in
  claim_inventory).
- No inventing citations.
- No inventing methods.
- No notebook-cell citations in main-text prose (those live in
  Methods, anchored by claim_id groups).

---

## 4. Phase 3 Tier 1's job — the reviewability gate

Tier 1's deterministic regex cross-walk is the **mechanical test of
the chain**. It runs at draft time, every time, and is the single
test that decides whether a manuscript is reviewable.

### 4.1 Three cross-walks (replacing SPEC §7.2 line 612)

| Cross-walk | What it checks | Failure → |
|---|---|---|
| **claim_id resolution** | Every `[Cxxx]` in `manuscript.md` prose appears as a row in `claim_inventory.tsv`. | exit 1 (P0) |
| **methods provenance** | Every `claim_inventory.tsv` row used in prose has a `(source_notebook, source_test)` entry in `methods_provenance.md`. | exit 1 (P0) |
| **methods coverage** | Every `source_notebook` used in Results prose is also surfaced in Methods prose (the source_notebook string appears in Methods at least once, or the source_test name does). | exit 1 (P0) |

Pass = all three cross-walks resolve clean. Failure = the chain is
broken somewhere; the manuscript is not reviewable on at least one
claim.

### 4.2 What this DOESN'T do

- Doesn't judge whether the prose is well-written.
- Doesn't check whether the claim is properly hedged (Tier 2's job).
- Doesn't verify the science (Tier 3's job).

The reviewability gate is a structural integrity check — it ensures
the manuscript's chain of reference is intact, not that the chain's
endpoints are correct.

---

## 5. M1's bidirectional consistency checks (testable now)

These two checks can be run on any project with M1's tools, BEFORE
M2 exists. They validate M1's invariants without needing a holistic-
write prompt.

### 5.1 Forward check — every catalog match has provenance

For each row in `claim_inventory.tsv`:

- `source_notebook` is a real file under `<project_root>/notebooks/`.
- `(source_notebook, source_test)` is in `methods_provenance.md`.
- `source_cell` is a valid cell index.

Failure mode: a regex caught a claim, but the claim's source notebook
isn't in methods_provenance. Either methods_provenance is incomplete
(extract_methods.py gap) or the claim's catalog assignment is wrong.

### 5.2 Backward check — every methods_provenance entry has at least one claim

For each entry in `methods_provenance.md` (one per detected test
invocation):

- At least one row in `claim_inventory.tsv` cites this notebook+test
  pair.

Failure mode: methods_provenance lists a test but no claim in REPORT.md
references its output. Either the regex catalog missed a claim, or
the test was run but its output isn't in REPORT.md (Methods says
"we did X" but Results doesn't report what X found — a real
manuscript-quality issue).

### 5.3 Coverage metric (replacing recall)

```
coverage_forward  = |claim_inventory ∩ methods_provenance| / |claim_inventory|
coverage_backward = |methods_provenance ∩ claim_inventory| / |methods_provenance|
```

Both should be 1.0 in the ideal case. Where they're not, the
diagnostic is concrete: a list of claims with no provenance, OR a
list of provenance entries with no claim. Either is actionable.

This replaces recall against a hand-curated list (which we used in
B1.e/h) with bidirectional consistency against an existing artifact
(`methods_provenance.md`). No hand labeling needed; the test is the
artifact's internal coherence.

---

## 6. Multi-project test methodology

The corpus exists: 60+ BERDL projects under
`spike/beril-extended/projects/` with REPORT.md + RESEARCH_PLAN.md +
notebooks/ structure. Domains span microbiology, AMR, metals,
pangenome, fitness, ecotypes, phage, functional analysis. Sizes
range 94 → 2338 lines REPORT.md, 1 → 32 notebooks.

### 6.1 Sampling

Three roles for projects in this validation methodology:

- **Dev (5 projects).** Used for catalog tuning. Free to iterate on
  regex catalog, source_test extraction, Methods drafting, etc.
  Failure modes here drive code changes.
- **Holdout (5 projects).** Used for unbiased measurement after dev
  iteration is complete. Code is FROZEN before holdout runs. Failure
  modes here are documented but don't drive immediate iteration —
  they go into the v0.8.x backlog.
- **Wild (~50 projects).** Used in production once M2 ships. Failure
  modes drive M2-side patches or v0.7.x extractor backlog.

### 6.2 Suggested dev sample (diverse by size + domain)

| Project | REPORT lines | Notebooks | Domain |
|---|---|---|---|
| `cog_analysis` | 96 | 4 | tiny analysis |
| `ecotype_analysis` | 142 | 2 | small ecotype study |
| `enigma_sso_asv_ecology` | 331 | 9 | medium ecology |
| `functional_dark_matter` | 862 | 14 | medium-large functional |
| `ibd_phage_targeting` | 2003 | 32 | large clinical-microbiome |

Spans: 96 → 2003 lines, 2 → 32 notebooks, 5 distinct domains.
Catches regex-catalog brittleness across different statistical-
methodology cultures.

### 6.3 Suggested holdout sample (locked before measurement)

| Project | REPORT lines | Notebooks | Domain |
|---|---|---|---|
| `amr_pangenome_atlas` | 225 | 7 | AMR / pangenome |
| `metal_fitness_atlas` | 282 | 7 | metals / fitness |
| `lab_field_ecology` | 151 | 3 | small ecology |
| `gene_function_ecological_agora` | 2338 | 16 | very large functional |
| `microbeatlas_metal_ecology` | 1388 | 6 | medium-large metals |

Mix of sizes and domains the dev set didn't cover. Locked once
catalog is frozen.

### 6.4 Per-project measurement

For each project in dev or holdout:

1. Run `extract_methods.py` → `methods_provenance.md`.
2. Run `claim_inventory.py --no-llm` (deterministic only) →
   `claim_inventory.tsv`.
3. Compute `coverage_forward` and `coverage_backward`.
4. Categorize misses:
   - Missing claim → catalog gap (file as catalog backlog).
   - Missing provenance → extract_methods.py gap (file as v0.7.x
     backlog).
   - Genuine claim-without-provenance (the analysis was done
     but the test isn't in methods_provenance because the AST
     doesn't catalog it) → real M1 failure mode.
5. Record `coverage_forward × coverage_backward` distribution
   across projects.

**Pass criterion (proposed):** dev mean coverage_forward ≥ 0.85,
coverage_backward ≥ 0.70, and no project below 0.50 on either. Hold
out runs verify the dev tuning generalizes.

These thresholds are starting points. After dev sampling reveals
the actual distribution, thresholds can be tightened or loosened.

---

## 7. Open questions / failure modes

### 7.1 extract_methods.py coverage gaps

methods_provenance.md is the output of `extract_methods.py`, which
AST-detects test invocations. On `ibd_phage_targeting` it catalogs
6 test types from 13 of 32 notebooks. Common gaps observed:

- ARI (sklearn.metrics.adjusted_rand_score) — used in REPORT.md.
- Custom permutation tests (raw numpy.random.shuffle).
- statsmodels.formula.api regressions (vs the AST-detected
  statsmodels.stats.multitest forms).
- Non-test computations that produce claim-shaped numerics
  (cliff δ via scipy.stats.cliffsdelta or custom Python).

**This is upstream work.** Closing methods_provenance gaps
mechanically improves backward coverage. It's outside M1's scope but
gates M1's measurable correctness.

### 7.2 Multi-source claims

"Tier-A replicates at 88.2% sign-concordance across cohorts" — the
provenance is "all of NB04b–h" (the meta-analysis pipeline). One
claim, multiple notebooks. The schema as written supports a single
`source_notebook`; extending to a list is straightforward but
changes the schema. **Open:** how should we represent these?

### 7.3 Borderline claims

"73 % of UC Davis in E1 or E3" — derived from the ecotype-projection
output. Is the projection a "test"? Defensible either way. Need a
heuristic; preferable to err toward inclusion (more conservative re:
reviewability).

### 7.4 source_test column requires extraction

This contract introduces a new schema column. Existing
`claim_inventory.py` doesn't extract it. Implementation:

- For each catalog match, the keyword identifies the test type
  (e.g., `OR=` → odds_ratio).
- methods_provenance.md groups by test name.
- Match the catalog's test type against methods_provenance's test
  types; the matching notebook+cell is the source.

When a test type fires multiple notebooks (e.g., Mann-Whitney is in
5 notebooks on `ibd_phage_targeting`), source disambiguation needs
either the surrounding REPORT.md prose (NB-prefix mentions) or LLM
help. This is a much smaller LLM job than the current B1.c
demarcator (one notebook-disambiguation per claim, not full claim
splitting).

### 7.5 [TBD] discrepancies handled by discrepancy_register

Discrepancy_register is orthogonal to the claim chain — it drives
Methods + Limitations honesty narrative, not reviewability per se.
But: discrepancies can spawn claim-shaped numerics ("plan said
Welch, exec used Mann-Whitney; the Mann-Whitney effect was X").
That claim has a notebook source AND a discrepancy reference. The
schema accommodates this naturally: the row has its source_notebook
+ source_test; the discrepancy reference lives in Methods text via
the discrepancy_register cross-walk.

---

## 8. Implementation impact (if this contract is accepted)

### 8.1 Code that needs to change

- **`claim_inventory.py` regex catalog scope narrowed** to
  statistical-output shapes. PERCENTAGE/RATIO_WITH_UNIT/N_COUNT/
  COUNT_OF demoted out of the inventory (or marked as
  "context-only" — no claim_id row, but contributes to flag
  aggregation for nearby claims).
- **`source_test` column added** to schema. Catalog matches
  identify the test type via regex keyword.
- **Notebook + test disambiguation** via methods_provenance lookup
  + surrounding-prose NB mentions. LLM disambiguation only when
  multiple candidates remain and prose is ambiguous.
- **Backward consistency check tool** — new diagnostic tool that
  computes `coverage_forward` + `coverage_backward` per project.

### 8.2 Code that goes away

- B1.f batching machinery (no need — the LLM call is much smaller
  per project under disambiguation-only role).
- B1.g retry / tolerated_missing (smaller LLM workload; failures
  are rare and localized).
- B1.h allowlists for figure_or_table fabrication (still useful
  for source_notebook disambiguation; keep that part).
- Most of the LLM demarcator. Replaced with deterministic regex
  + a small per-claim sourcing call (or no LLM at all if methods_
  provenance disambiguation is unique).

### 8.3 Documents that need to change

- SPEC §4.6 → updated scope (statistical-output sub-language).
- SPEC §6.4 → M2 contract clarified ([Cxxx] markers, Methods-by-
  notebook drafting).
- SPEC §7.2 → Tier 1 cross-walks specified (three cross-walks per
  §4.1 above).
- DECISIONS.md → file the scope-narrowing decision (D-04N).
- M1_PUNCH_LIST.md → §C smokes shift from recall-against-handlist
  to coverage_forward/backward.

---

## 9. Decision points for sign-off

1. **Catalog scope** — accept the statistical-output sub-language
   framing? (Demote PERCENTAGE/RATIO/N_COUNT/COUNT_OF; keep p-values,
   CIs, effect-size keywords, test statistics, model-selection criteria,
   empirical p-values, concordance metrics.)
2. **Schema column addition** — accept the new `source_test` column?
   It's load-bearing for the methods-by-notebook drafting in §3.1.
3. **Tier 1 reviewability gate** — accept the three cross-walks (claim_id
   resolution + methods provenance + methods coverage) as the structural
   integrity test of the manuscript chain?
4. **Multi-project test methodology** — accept dev (5) / holdout (5) /
   wild (50) split? Specific dev + holdout samples per §6.2/§6.3?
5. **Coverage metrics** — accept `coverage_forward × coverage_backward`
   as M1 measurement, replacing recall-against-handlist?
6. **Implementation scope** — proceed with B1.e validator + cost reframing
   (clean wins) but revert B1.f/g/h LLM demarcator machinery (over-built
   for the new scope)? Then rewrite the demarcator pass as deterministic-
   sourcing-with-LLM-disambiguation?

If accepted, M1 close-out shifts from "ship as built" to "rewrite
catalog scope + add source_test + add bidirectional consistency
checks + smoke against 5 dev projects." Estimated cost: a focused
day of work (~200 LOC catalog + sourcing logic, ~100 LOC consistency
checks, dev-sample smoke runs).

If rejected, M1 ships as currently committed with documented gaps
to revisit at M2.
