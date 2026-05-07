# v0.8.0 Punch List — Language Quality + Structural Post-Checkers

**Created:** 2026-05-06
**Triggered by:** Dual review of ibd_phage_manuscript (v0.7.0.1 output)
**Dependencies:** v0.7.2 shipped (Data Availability fixes)

## Diagnosis

Three independent reviews (paper-review skill Mode A, independent agent,
language-focused agent) converge on the same structural diagnosis: the
paper-writer produces scientifically accurate manuscripts with systematic
language problems that reduce comprehensibility. The problems are
prompt-level and mechanical — they can be fixed without changing the
pipeline architecture.

The v0.8.0 release addresses two areas:
1. **Prompt rules** — 7 new writing discipline rules added to section prompts
2. **Post-checkers** — 3 new advisory validators that catch language patterns

---

## Tier A — Prompt Rules (add to section prompts)

### A1: Sentence length cap (results.v1.md, discussion.v1.md, abstract.v1.md)

**Rule text to add:**
```
SENTENCE DISCIPLINE: Any sentence whose main verb appears after word 25
must be split. Maximum 1 parenthetical insertion per sentence. If you
need 2+ parenthetical asides, restructure as a numbered list or split
into separate sentences.
```

**AC:** Manual inspection of next live-test output shows zero sentences
with main verb past word 25 in Results or Discussion.

### A2: Abbreviation expansion on first use (all section prompts)

**Rule text to add:**
```
ABBREVIATION DISCIPLINE: Every abbreviation must be expanded on its
FIRST USE in the manuscript (not just in each section). Do not use an
abbreviation before its expansion. Project-internal terms (Tier-A,
Tier-B, phage GAP, ecotype labels E0-E3) must be explicitly defined
with a one-sentence operational definition on first use. Create an
abbreviation table in Methods if the manuscript uses 10+ abbreviations.
```

**AC:** grep for undefined abbreviations in next live-test output
returns zero hits.

### A3: Quantitative stacking limit (results.v1.md)

**Rule text to add:**
```
QUANTITATIVE STACKING: Do not embed 3+ statistical results (sample
sizes, p-values, percentages, fold-changes, effect sizes) in a single
sentence. When reporting 3+ numbers, use a structured list, a table
reference, or separate sentences. One sentence = one primary quantitative
claim + at most one supporting statistic.
```

**AC:** No sentence in Results contains 3+ inline statistical values.

### A4: Single-hedge rule (discussion.v1.md, abstract.v1.md)

**Rule text to add:**
```
HEDGING DISCIPLINE: Each claim may carry at most ONE hedging marker
(e.g., "may," "suggests," "appears," "candidate," "hypothesis-
generating"). Do not stack hedges. If a claim needs qualification,
state the limitation in a SEPARATE sentence: "[Claim]. This requires
[validation/replication/etc.]."

BAD:  "best understood as a candidate workflow derived from one
       trajectory, not a rule with established external validity"
GOOD: "Patient 6967's ecotype shift suggests a dosing strategy.
       This requires prospective validation."
```

**AC:** No sentence in Discussion contains 2+ hedging markers.

### A5: Transition sentences required (results.v1.md, discussion.v1.md)

**Rule text to add:**
```
TRANSITIONS: Every major section and subsection must begin with a
transition sentence that: (a) links to what was established in the
previous section, and (b) previews what this section will show and why.
Do NOT use formulaic transitions ("We next examined..."). Instead,
state the logical connection: "Having established X, we now test
whether Y, because Z."
```

**AC:** Every Results subsection boundary has a non-formulaic transition.

### A6: Notebook citation externalization (methods.v1.md)

**Rule text to add:**
```
NOTEBOOK CITATIONS: Do not cite specific notebook cells or line numbers
in the main text (e.g., "notebooks/NB04b.ipynb cell 9, line 36").
Instead, create a "Computational Reproducibility" table in Methods
mapping each analysis to its notebook path. In prose, reference the
table: "Permutation tests used 200 iterations (Methods Table S1,
Pillar 2)."
```

**AC:** Zero notebook path/cell/line citations in main text prose.

### A7: Echo repetition cap (abstract.v1.md, results.v1.md, discussion.v1.md)

**Rule text to add:**
```
ECHO REPETITION: A specific quantitative finding (e.g., "88.2% sign-
concordance") may appear in AT MOST 2 locations in the manuscript:
once in Results (primary report) and once in Abstract (summary). Do
NOT repeat in the Findings Summary, Discussion opener, or Discussion
body. Instead, reference the result: "the E1 Tier-A sign-concordance
reported above."
```

**AC:** No quantitative finding stated more than 2× in the manuscript.

---

## Tier B — Post-Checkers

### B1: check_sentence_complexity.py

**Spec:**
```
Advisory post-checker (exit 0). Scans all section .md files.

Checks:
1. WARN if any sentence exceeds 50 words.
2. WARN if any sentence contains 2+ parenthetical pairs.
3. NOTE if any sentence has main verb past word 25 (heuristic:
   look for first conjugated verb via simple POS pattern).
4. Summary: N sentences flagged / M total sentences.

Input: draft_dir path
Output: stderr warnings + diagnostics JSON
```

**AC:** Catches the 10 worst sentences identified in the language review
when run against the ibd_phage_manuscript output.

**LOC estimate:** ~150

### B2: check_abbreviation_discipline.py

**Spec:**
```
Advisory post-checker (exit 0). Scans assembled manuscript.md.

Checks:
1. WARN if any ALL-CAPS abbreviation (2+ chars) appears before its
   expansion (heuristic: look for "Full Name (ABBR)" pattern).
2. WARN if project-internal terms (Tier-A, Tier-B, phage GAP,
   ecotype E0-E3) appear without a preceding definition sentence.
3. NOTE count of unique abbreviations; suggest abbreviation table
   if count > 10.

Input: draft_dir path
Output: stderr warnings
```

**AC:** Catches the 12 undefined/late-defined terms from the language
review.

**LOC estimate:** ~120

### B3: check_echo_repetition.py

**Spec:**
```
Advisory post-checker (exit 0). Scans assembled manuscript.md.

Checks:
1. Extract quantitative claims (regex: number + unit/% + context).
2. For each claim, count appearances across sections (Abstract,
   Results, Findings Summary, Discussion).
3. WARN if any claim appears 3+ times.
4. NOTE top-5 most-repeated claims.

Input: draft_dir path
Output: stderr warnings + diagnostics JSON
```

**AC:** Catches the "88.2% sign-concordance" 4× repetition from the
ibd_phage_manuscript.

**LOC estimate:** ~130

---

## Tier C — Structural Improvements (from content review)

### C1: Results word budget

Add a word-budget heuristic to results.v1.md:
```
RESULTS LENGTH: Target 1,500-2,500 words for main Results. If the
project has >8 notebooks, select the 3-4 most important findings for
main text and route remaining detail to supplementary materials.
Produce a supplementary_results.md alongside the main results.
```

**Depends on:** Supplementary material routing (C4).

### C2: Competing approaches paragraph in Discussion

Add to discussion.v1.md:
```
COMPETING APPROACHES: Include one paragraph comparing the study's
methodology to 2-3 alternative approaches from the literature. Cite
specific competing methods and explain why the present approach was
chosen. If no competing approaches exist, state that explicitly.
```

### C3: NB05-style scoring specification detection

Add to methods.v1.md:
```
SCORING SYSTEMS: If the analytical workflow includes a scoring or
ranking system (multi-criteria, weighted, composite), you MUST formally
specify: (a) what components are scored, (b) how each component is
scored (binary? continuous? weighted?), (c) how components combine
into the total, (d) what threshold defines "actionable" or equivalent.
A narrative description is not sufficient; provide the formula or
decision rule.
```

### C4: Supplementary material routing (DEFERRED — architectural)

For STRONG-tier projects with >8 notebooks, the orchestrator should
produce `supplementary_results.md` and `supplementary_methods.md`
alongside main sections. This requires changes to:
- results.v1.md (pillar-routing heuristic)
- methods.v1.md (notebook table generation)
- paper_writer.sh (new phase)
- assemble_docx.py (supplementary assembly)

**Complexity:** High. Defer to v0.9.0 unless v0.8.0 scope permits.

---

## Tier D — Reference / Assembly Fixes

### D1: Strip reference QC annotations

assemble_docx.py (or the assembly phase) should strip internal QC
fields from the reference list before producing manuscript.md:
- Remove "Scope alignment:" lines
- Remove "Assessment:" lines
- Remove "Notes:" lines
- Remove "Uncited (in pool but not yet cited in prose)" section

### D2: Figure caption separation

Investigate and fix the rendering artifact where figure captions are
spliced into Results prose. Likely in embed_figures phase or
assemble_docx.py. The symptom is duplicated text fragments like
"Fig. 1A). (B) The four consensus ecotypes..." appearing inline.

---

## Test plan

- All existing 801 tests must pass (zero regressions)
- New tests for B1/B2/B3 post-checkers (~40-50 new tests)
- Regression test: run B1/B2/B3 against ibd_phage_manuscript output;
  verify they catch the specific issues documented in
  reviews/ibd_phage_manuscript_dual_review.md
- Live retest on ibd_phage_targeting after prompt rules applied;
  compare language quality metrics to baseline

## Ship criteria

- All Tier A prompt rules added and tested
- All Tier B post-checkers implemented with tests
- Tier C1-C3 added (C4 deferred)
- Tier D1-D2 fixed
- Live retest shows measurable improvement in sentence complexity,
  abbreviation discipline, and echo repetition
