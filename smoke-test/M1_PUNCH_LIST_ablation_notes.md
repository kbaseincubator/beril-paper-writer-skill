# M1 §C1.b — Q1 Cost-Justification Ablation (`discrepancy_register`)

**Filed:** 2026-05-07
**Smoke target:** `spike/beril-extended/projects/ibd_phage_targeting/`
**Tool under test:** `discrepancy_register.py` v `0.8.0-m1-A1.abcd`
**Punch-list reference:** `M1_PUNCH_LIST.md` §C1.b
**Decision being evaluated:** D-034 Q1 — LLM-assisted overlap classifier
vs. pure string-match. Costed at ≤ $0.05/run.

---

## 1. The ablation contract (per §C1.b)

> Run twice on same inputs:
>   - Once with default LLM-assist path → record entries `E_llm`.
>   - Once with `--no-llm` → record entries `E_strmatch`.
>   - Compute `delta = E_llm \ E_strmatch` (entries the LLM caught that
>     string-match did not).
>
> **Gate:** if `delta` is empty AND `ibd_phage_targeting` has any
> plan-vs-execution paraphrase pairs we can identify by hand, the Q1
> cost decision is suspect and gets reopened in `DECISIONS.md` as a
> re-evaluation note.

The hand-list step happens **before** running, so the ablation is
honest with respect to a known answer key.

---

## 2. Architecture in scope

`discrepancy_register.py`'s deterministic pre-pass partitions into:

* `plan_only` — plan-prescribed phrases with no normalized-token overlap
  with any executed phrase. Always emitted as `plan-prescribed-not-executed`.
* `exec_only` — executed phrases with no normalized-token overlap with
  any plan-prescribed phrase. Always emitted as `executed-not-prescribed`.
* `overlap` — both sides match per `is_overlap_match` (containment-
  over-min ratio ≥ `_OVERLAP_RATIO_THRESHOLD = 0.5`, hardcoded at
  `discrepancy_register.py:501`). The LLM (Haiku 4.5) classifies these
  as `equivalent | paraphrase | discrepancy`.

The LLM **only** operates on `overlap` candidates. With `--no-llm`,
overlap pairs are skipped; the markdown contains only deterministic
`plan_only` + `exec_only` entries plus a footer noting the skip count.

`delta = E_llm \ E_strmatch` is therefore by construction equal to
`{overlap candidates the LLM classified as discrepancy}`. Inverse
`E_strmatch \ E_llm` is empty by construction (the LLM cannot remove
plan_only/exec_only entries).

---

## 3. Hand-list of plan ↔ execution pairs (BEFORE running)

Walked `RESEARCH_PLAN.md` and `papers/draft_1/methods_provenance.md`
for ibd_phage_targeting. The plan-side regex
(`analys[ei]s|method|test|stat`, `discrepancy_register.py:260`) scopes
parsing to three sections: `## Methodological norms` (line 181),
`## Analysis Plan — notebook sketch` (line 320), `## Reproduce-and-extend`
(line 354). The extensive `## Hypothesis Framework` (Pillar 1–5,
lines 11–141) and the `## Criteria — the four-tier rubric` (lines
143–177) are NOT parsed — see watch-for #2 in `.auto-memory/
project_paper_writer_v0_8_m1_a1.md`.

`methods_provenance.md` Statistical-Tests section detected six unique
test names: Chi-squared test of independence, Gradient boosting
classifier, Mann-Whitney U test, Multiple-testing correction (BH-FDR),
Principal component analysis, ROC AUC.

### 3.a Paraphrase-equivalent pairs (plan ⇔ exec, same hypothesis,
        same test family, no scientific discrepancy)

| ID | Plan side (verbatim, abbreviated) | Plan §X | Exec side (notebook, library) | Verdict |
|----|-----------------------------------|---------|-------------------------------|---------|
| HE1 | "ref_cd_vs_hc_differential (Mann-Whitney) → verify against ANCOM-BC / MaAsLin2 / LinDA" | Methodological norms (N1) | NB00, NB04, NB04b, NB04h: `scipy.stats.mannwhitneyu` | **Equivalent** for the Mann-Whitney leg. ANCOM-BC/MaAsLin2/LinDA legs are **discrepancies** (see §3.b). |
| HE2 | "multinomial logistic regression OR gradient-boosted classifier on … clinical metadata" | Pillar 1 H1c (NB03) — **NOT REGEX-PARSED** | NB03: `sklearn.ensemble.GradientBoostingClassifier` (+ lightgbm imported) | **Equivalent** (gradient-boosted classifier branch chosen). Plan-side bullet is in an unparsed section, so will not surface as overlap candidate. |
| HE3 | "ROC AUC" implied for H1c classifier | Pillar 1 H1c (NB03) — **NOT REGEX-PARSED** | NB03: `sklearn.metrics.roc_auc_score` | **Equivalent**. Plan-side not in parseable section. |
| HE4 | "χ² or exact test" for H1b ecotype occupancy | Pillar 1 H1b (NB02) — **NOT REGEX-PARSED** | NB04h: `scipy.stats.chi2_contingency` | **Equivalent** (χ²-of-independence is the same family). Plan-side not in parseable section. |
| HE5 | "FDR-corrected" / multi-test correction (referenced throughout norms) | Methodological norms (N2-derived) | Many notebooks: `statsmodels.stats.multitest.multipletests fdr_bh` | **Equivalent**. Plan-side reference is implicit/distributed; not as a single bulletable phrase. |

### 3.b Load-bearing plan-prescribed-not-executed (real discrepancies)

| ID | Plan side (verbatim, abbreviated) | Plan §X | Exec side | Severity (hand-judged) |
|----|-----------------------------------|---------|-----------|------------------------|
| HD1 | "ANCOM-BC / MaAsLin2 / LinDA on `fact_taxon_abundance` per ecotype" | Methodological norms (N1) | **None of these libraries detected** in methods_provenance.md (only Mann-Whitney + multipletests). | load-bearing — Tier-A scoring depends on consensus of three DA methods. |
| HD2 | "DMM" / "topic modeling" / "LDA" (NB01) | Analysis Plan (NB01 row) | **PCA + GMM (sklearn) only**; no DMM, no LDA library, no topic modeling. | load-bearing — H1a stability gate ("retained only if stable under all three methods") presumes ≥2 disjoint methods. |
| HD3 | "MOFA+ on the multi-omics join" (H1a / NB07d) | Pillar 1 H1a — **NOT REGEX-PARSED** | **Not detected.** | load-bearing if MOFA+ result is cited; cosmetic if scope-deferred. (Plan acknowledges some MOFA+ deferral; see RESEARCH_PLAN.md NB01 row.) |
| HD4 | "SparCC / SpiecEasi per ecotype" (H2d) | Pillar 2 H2d — **NOT REGEX-PARSED** | **Not detected.** | load-bearing — H2d co-occurrence-module test depends on this. |
| HD5 | "200-perm null"; permutation falsifiability legs (multiple H3a clauses) | Pillar 3 H3a — **NOT REGEX-PARSED** | **Not detected as a named test in methods_provenance.md** (custom permutation may exist in notebook code, but the AST extractor doesn't catch raw `np.random` shuffles). | load-bearing if H3a is reported; possibly an extractor gap rather than a real discrepancy — flag to v0.7.x extractor backlog. |

### 3.c Executed-not-prescribed (true exec-only)

PCA — `## Methodological norms` and `## Analysis Plan` do not name
PCA. NB01, NB01b, NB02 use it for ecotype training/refit/projection.
Likely cosmetic (PCA is a routine pre-processing step), but not in
the parsed plan. Tagged as `executed-not-prescribed` correctly.

### 3.d Pre-run hand-list summary

* **5 paraphrase-equivalents** — but **only 1 (HE1, Mann-Whitney)** is
  in a regex-parsed plan section. The other 4 are in unparsed Pillar
  sections. The deterministic pre-pass cannot surface them as overlap
  candidates because the plan-side parser never sees the bullets.
* **5 load-bearing discrepancies** — **only HD1 (ANCOM-BC/MaAsLin2/
  LinDA)** is in a regex-parsed plan section. HD2 (LDA/DMM/topic
  modeling) is partially in NB01 row of `## Analysis Plan` (parsed)
  and partially in Pillar 1 H1a (unparsed). HD3-5 are entirely in
  unparsed sections.
* **1 exec-only candidate** (PCA).

---

## 4. Sandbox dry-run ablation result

Run on 2026-05-07, sandbox bash, paths under
`/sessions/admiring-wonderful-clarke/mnt/`:

```
PROJECT=spike/beril-extended/projects/ibd_phage_targeting
PYTHONPATH=src python3 smoke-test/m1_discrepancy_smoke.py \
    --mode ablation \
    --project-root $PROJECT \
    --staging-dir /tmp/m1-disc-ablation
```

Result:

| Leg                | entries | overlap candidates | LLM calls | cost_usd | exit_status |
|--------------------|---------|--------------------|-----------|----------|-------------|
| `--no-llm`         | 37      | 0 (skipped)        | 0         | $0.0000  | 0           |
| default (LLM-on)   | 37      | 0                  | 0         | $0.0000  | 0           |
| `delta = E_llm \ E_strmatch` | **0** | — | — | — | — |

The default (LLM-assisted) leg produced byte-identical output to the
`--no-llm` leg. `cost_usd = 0` on both legs because the deterministic
pre-pass produced **zero overlap candidates**, so the LLM was never
invoked even with `--no-llm` not set.

Note: the sandbox dry-run was conducted with the same code that would
run on Adam's Mac shell; the LLM-on leg's behavior is deterministic
in the zero-overlap case (no API call ⇒ no API key needed). Adam's
Mac-shell rerun is necessary only to (a) confirm the result with the
live LLM seam wired in, and (b) verify the ≤ $0.05 cost ceiling is
honored for the right reason.

### 4.a Composition of the 37 deterministic entries

* 14 × `plan-prescribed-not-executed` — **ALL** sourced from
  `## Methodological norms`. Inspection of the first 10 reveals the
  parser pulls every bullet under that heading, including:
  * 4 × `ref_*_*` table-verification rules (Mann-Whitney/calprotectin/
    viromics/Kumbhari ETL) — **legitimate plan prescriptions**.
  * 6 × BERDL-query-hygiene rules (`kescience_fitnessbrowser` `orgId`
    pre-filter; `kbase_ke_pangenome.gene_cluster` `gtdb_species_clade_id`
    pre-filter; backtick-quote on `order` reserved keyword; pangenome
    taxonomy join key choice; etc.) — **NOT analysis prescriptions**;
    these are SQL hygiene under norm N6, not testable hypotheses.
  * Remaining ~4 are cross-substudy methodology assertions (N12/N13/
    N14 axes) — borderline; arguably methodological but not "did we
    run analysis X" assertions.
* 23 × `executed-not-prescribed` — every detected statistical-test
  invocation in methods_provenance.md, including the genuinely-equivalent
  HE1/HE2/HE3/HE4/HE5 cases that ARE in the plan but in unparsed
  sections.

The 23 `executed-not-prescribed` entries are inflated by the
plan-parser's narrow heading scope; if Pillar sections were parsed,
HE2 (gradient-boosted classifier), HE3 (ROC AUC), HE4 (chi-squared
test), and the Mann-Whitney N1 reference would land as overlap
candidates and the LLM would correctly classify them as `equivalent`
or `paraphrase`, removing those rows from the register.

---

## 5. Q1 cost-justification verdict

### 5.a Strict ablation reading

`delta = ∅` (zero entries the LLM caught that string-match missed).
Per §C1.b's gate, **and** there are paraphrase pairs identifiable by
hand (HE1–HE5 in §3.a), this would normally **suspect Q1** and
trigger D-035.

### 5.b Why the strict reading is misleading on this project

The empty `delta` is **not** evidence the LLM is unhelpful. It is
evidence that the deterministic pre-pass **never produced overlap
candidates** for this project. Of the 5 hand-found paraphrase-equivalent
pairs, only HE1 lives in a regex-parsed plan section; HE2-HE5 live in
the unparsed Pillar sections and are invisible to the plan-side parser.
HE1 itself does not surface as an overlap candidate either, because the
plan-side bullet ("ref_cd_vs_hc_differential (Mann-Whitney) → verify
against ANCOM-BC / MaAsLin2 / LinDA on fact_taxon_abundance per ecotype")
is multi-clause prose; once normalized + tokenized + Porter-stemmed, its
shared-token set with the executed phrase
("Mann-Whitney U test scipy.stats.mannwhitneyu in NB00…cell 15 line 13
alternative two-sided") is small enough that the containment-over-min
ratio falls below the `_OVERLAP_RATIO_THRESHOLD = 0.5`. The plan side
ends up with ~16 stemmed tokens, the exec side with ~12; intersection ≈
{`mann`,`whitney`}; ratio ≈ 2/12 ≈ 0.17 < 0.5.

So the LLM doesn't "fail to add value" — it **never gets the chance
to run** on this project's data. The Q1 cost decision is therefore
**not testable** here in the sense §C1.b assumes.

### 5.c Two structural defects revealed

The ablation is the right vehicle for surfacing both:

1. **Plan-side parser scope is too narrow.** The
   `analys[ei]s|method|test|stat` regex misses the Pillar 1–5
   `*Test*:` lines that contain the bulk of analysis prescriptions in
   `ibd_phage_targeting/RESEARCH_PLAN.md`. Net effect:
   * HE2/HE3/HE4 falsely surface as `executed-not-prescribed` (the plan
     does prescribe them; the parser doesn't see).
   * HD2-HD5 falsely silent (the plan does prescribe them; the parser
     doesn't see; methods_provenance also lacks them, but the discrepancy
     is undetected because both sides are zero on the parser's view).
2. **Plan-side parser includes content that isn't analysis.** Bullets
   under `## Methodological norms` include SQL hygiene rules and
   operational guidance that emit as 6+ false-positive
   `plan-prescribed-not-executed` discrepancies. Net effect: register
   noise that the holistic prompt (M2) will have to filter through.

The `_OVERLAP_RATIO_THRESHOLD = 0.5` is **load-bearing** but **not
the primary defect**. Even with a perfect plan parser, multi-clause
prose bullets will still struggle to cross the 0.5 threshold. The
matching criterion (containment-over-min) was chosen to avoid every
"test"-containing bullet matching every other "test"-containing
bullet (a real concern); on prose-heavy plans it's restrictive in
the opposite direction.

### 5.d Recommendation

Per §C1.b's AC: **file a `D-035` re-evaluation note** capturing what
this ablation revealed:

> **D-035** — Q1 (LLM-assisted overlap classification) is **structurally
> unreviewed** on `ibd_phage_targeting` because the deterministic pre-
> pass produces zero overlap candidates. Two upstream defects make
> overlap-zero the rule, not the exception, on prose-heavy plans:
>
> 1. Plan-side heading regex (`analys[ei]s|method|test|stat`) misses
>    Pillar / hypothesis sections that contain the actual `*Test*:`
>    prescriptions.
> 2. Containment-over-min threshold of 0.5 is too restrictive against
>    multi-clause plan bullets even where the plan section IS parsed.
>
> Q1 cost decision (≤ $0.05/run, LLM-assisted) is **defensible on cost
> grounds** (the LLM doesn't fire ⇒ no cost overrun risk). It is **not
> evidenced as load-bearing**; until upstream parsing produces overlap
> candidates, neither A/B comparison is meaningful. Defer reconsideration
> until v0.9 architectural conversation per watch-for #3 of the A1 audit.

This matches watch-for #3 from `.auto-memory/project_paper_writer_v0_8_m1_a1.md`:

> If C1.b's hand-list of paraphrase pairs reveals the matching threshold
> isn't doing the linkage work, the fix is NOT to expand the LLM's scope
> to plan_only + exec_only pairs (expensive); it's to reconsider the
> matching criterion. Escalate as a v0.9 architectural decision, not a
> v0.8.x patch.

### 5.e Ship verdict for M1

**M1 §C1 ships.** The smoke harness AC is met (≥ 1 entry; schema-valid;
audit emitted; cost ≤ $0.05). The Q1 cost decision is honored on this
project (cost = $0.00 ≤ ceiling). The ablation result is documented
here and feeds D-035; the architectural reconsideration is v0.9 work.

The 37 entries in the register **are** consumable by M2's holistic prompt,
with one caveat that should land in M2's prompt design: the holistic
prompt must treat `## Methodological norms` SQL-hygiene-source entries
as **likely-noise** rather than load-bearing discrepancies. This belongs
in M2's discrepancy_register-consumption guidance, not as an M1 patch.

---

## 6. Mac-shell runbook (Adam to verify before close-out)

The sandbox dry-run is sufficient for the v0.8.0-m1-A1.abcd code path
(zero overlap candidates → no LLM call regardless of mode). For
audit-trail completeness, Adam should still run the ablation once
from the Mac shell to confirm the live-LLM seam doesn't drift the
result.

```bash
WORKSPACE=~/Documents/Claude/Projects/research-coscientist-dev
SKILL_DIR="$WORKSPACE/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$WORKSPACE/spike/beril-extended/projects/ibd_phage_targeting"

# Pipx venv's Python (the one with anthropic SDK + nbformat).
PYTHON_BIN="$(awk 'NR==1 && /^#!/ {sub(/^#!/, ""); split($0, a, " "); print a[1]; exit}' "$(command -v beril-paper-writer)")"
echo "PYTHON_BIN=$PYTHON_BIN"
"$PYTHON_BIN" -c "import anthropic, nbformat; print('deps OK')"

cd "$SKILL_DIR"
PYTHONPATH=src "$PYTHON_BIN" smoke-test/m1_discrepancy_smoke.py \
    --mode ablation \
    --project-root "$PROJECT_ROOT" \
    --staging-dir /tmp/m1-disc-ablation
echo "exit=$?"
```

Expected: exit 0; both legs report 37 entries; `delta = 0`; cost on
default leg = $0.00; printed result line "ablation completes. Q1 verdict
requires hand-list comparison; see M1_PUNCH_LIST_ablation_notes.md."

If the live-LLM run produces a different entry count or a non-zero
`delta`, that's a real divergence worth surfacing — investigate before
shipping M1.
