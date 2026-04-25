# Reporting standards extract for BERIL paper-writer

Sources read:

1. **ICMJE Recommendations** — https://www.icmje.org/recommendations/
   (Sections IV.A — Manuscript Preparation, V.A — Roles of Authors)
2. **SAMPL Guidelines** — Statistical Analyses and Methods in the Published
   Literature, https://www.equator-network.org/wp-content/uploads/2013/07/SAMPL-Guidelines-6-27-13.pdf
3. **Ou et al. 2020** — "Guidelines for Statistical Reporting in Medical
   Journals," J Thorac Oncol 15(11):1722–1726, doi:10.1016/j.jtho.2020.08.019,
   PMID: PMC7642026 (uploaded by user)

This document extracts the items from these sources that informed SPEC §7's
mechanized + aspirational checklists. Items not relevant to BERIL's
typical project shape (computational reanalysis of public bacterial
microbiology datasets) are explicitly noted as out-of-scope.

---

## 1. ICMJE structural requirements (Section IV.A)

### IMRAD structure (quoted)

> "The text of articles reporting original research is usually divided
> into Introduction, Methods, Results, and Discussion sections. This
> so-called IMRAD structure is not an arbitrary publication format but a
> reflection of the process of scientific discovery."

### Required manuscript sections (IV.A.3)

In order:

1. **Title page** — title, authors, affiliations, ORCID, funding,
   word count, conflicts of interest
2. **Abstract** — structured (Background/Objective, Methods, Results,
   Conclusions); funding sources; data-availability identifier where
   applicable
3. **Introduction** — problem context, significance, specific
   objective/hypothesis. Cite prior work, not your results.
4. **Methods** — study design; participant/data selection; measurements
   and procedures; statistical methods; ethics approval; AI tool use
5. **Results** — findings in logical sequence; primary outcomes first;
   absolute numbers + derivatives; tables/figures for key data
6. **Discussion** — summary of findings; mechanisms/explanations;
   limitations; implications; novel hypotheses (if warranted); no
   data repetition
7. **References** — numbered in order of first citation; standard
   format (NLM style); no AI-generated material as primary source
8. **Tables** — numbered; self-explanatory titles; abbreviations
   defined in footnotes
9. **Figure legends** — separate page; sufficient detail for stand-alone
   reading
10. **Supplementary material** (if applicable) — methods, extended
    data, code repositories

### Methods principle (IV.A.3.d quoted via Ou et al. 2020)

> "Describe statistical methods with enough detail to enable a
> knowledgeable reader with access to the original data to judge its
> appropriateness for the study and to verify the reported results."

### Data availability

ICMJE requires data-availability statement. Acceptable: repository URL,
accession number, justified explicit restriction. **Not acceptable:**
"available upon request" or "by contacting authors."

---

## 2. ICMJE AI disclosure (Section V.A, January 2026)

Quoted in full because this is load-bearing for the writer's auto-emitted
disclosure paragraph:

> "Chatbots (such as ChatGPT) and other AI-assisted tools should not be
> listed as authors because they cannot be responsible for the accuracy,
> integrity, and originality of the work, and these responsibilities are
> required for authorship. Therefore, humans are responsible for any
> submitted material that included the use of AI-assisted technologies.
> Authors should carefully review and edit the AI-generated content as
> the output can be incorrect, incomplete, or biased. Authors should be
> able to assert that there is no plagiarism in their paper, including
> in text and images produced by the AI. Humans must ensure there is
> appropriate attribution of all quoted material, including full
> citations. **Referencing AI-generated material as the primary source
> is not acceptable.**"

> "Nondisclosure of AI use may require corrective action and may be
> construed as misconduct in some circumstances."

Implications for writer:

- Auto-emit AI-disclosure paragraph (M3 validator)
- No AI tool as author
- Author list, funding, conflicts: TBD placeholders, user fills
- AI-generated content cannot be a primary source for any claim — the
  writer's own outputs must be grounded in project artifacts or in
  cited primary literature

---

## 3. SAMPL — directly applicable items

SAMPL is for medical journals but the general statistical-reporting
items transfer cleanly to computational biology.

### §1 General (preliminary reporting)

- Software name and version (e.g., "R 4.1.2, lme4 v1.1-27")
- Statistical methods named (e.g., "Welch's t-test, two-sided")
- Significance level stated (typically α = 0.05)
- One-sided vs two-sided tests stated
- Multiple-comparisons handling stated explicitly

### §2 Numerical results

- Effect sizes always reported (not just p-values)
- 95% confidence intervals around effect sizes (or Bayesian credible intervals)
- Counts (n) precede derivatives (%): "42/156 (26.9%)" not "26.9%"
- Exact p-values, not just "<0.05" or "NS"
- For continuous variables: mean (SD) for normal; median (range or IQR)
  for skewed
- Categorical: count + percentage; specify denominator

### §3 Multiple comparisons

- If multiple statistical tests reported, name the correction method
  (Bonferroni, Benjamini-Hochberg FDR, Holm-Sidak, etc.)
- For genome-wide / high-dimensional: state the corrected significance
  threshold

### §4 Tables and figures

- Self-contained captions
- Statistical test named in caption where relevant
- N and error-bar type specified
- Units on every axis

---

## 4. Ou et al. 2020 — directly applicable items

Ou et al. is JTO-specific (oncology + clinical trials), but extracts
several principles cleanly.

### Methods section (page 2)

- Statistical analysis plan determined PRIOR to analysis
- Analysis software and version (because "they may use different
  optimization and numerical routines which produce slightly different
  results")
- For observational studies (BERIL applies): explicitly state whether
  hypothesis-testing or hypothesis-generating
- Method(s) for handling missing data specified
- For data-driven variable selection: describe the steps

### P-value reporting (page 3)

- Magnitude of effect MUST be reported alongside p-value (point estimate
  + CI, not just p)
- Precision rules:
  - Two decimal places when p > 0.01
  - Three decimal places when p < 0.01
  - Acceptable to write `p < 0.001` for very small p-values
- Two-sided p-values unless one-sided is explicitly designed
- "Strongly discouraged" for secondary and subgroup analyses where point
  estimates and CIs are preferred
- "Trend" should ONLY be used for statistical tests for trends, not for
  borderline p-values

### Categorical outcomes (pages 3–4)

- Confidence intervals with point estimates
- Denominator clearly stated for each percentage
- Statistical test named (Chi-square, Fisher's exact, Z-test)
- 1 decimal place for percentages when n > 200

### Conclusion section (page 5)

- "P-value > 0.05 does not mean equivalence" — must NOT conclude
  groups are similar from a non-significant p
- For observational studies: discuss potential bias and unmeasured
  confounders explicitly
- A single observational study is not sufficient to establish causation

### Out of scope for BERIL (Ou-specific items)

- Forest plots for clinical trial subgroups
- Kaplan-Meier survival curves with at-risk counts (most BERIL projects
  are not survival analyses)
- Predictive vs prognostic biomarkers framing (oncology-specific)
- Clinical trial registration numbers

---

## 5. Reporting standard alignment for BERIL

BERIL's typical project shape is **computational reanalysis of public
bacterial microbiology datasets** — closer to STROBE (observational
epidemiology) than CONSORT (RCTs), STARD (diagnostic accuracy), or
PRISMA (systematic review). The writer should adopt STROBE-adjacent
norms by default.

**STROBE-derived items (Ou et al. references STROBE — von Elm et al.
2007):**

- Study design explicitly named in Methods opening
- Data sources and selection criteria described
- Variables defined explicitly
- Statistical methods described including handling of missing data
- Limitations include sources of bias and study design constraints
- Discussion engages with generalizability

---

## 6. Mapping to SPEC §7 mechanized validators

For traceability, every M-tier validator in SPEC §7.1 cites a source:

| Validator | Source(s) |
|---|---|
| M1 — IMRAD sections present | ICMJE IV.A.3 |
| M2 — Structured abstract | ICMJE IV.A.3.b |
| M3 — AI disclosure | ICMJE V.A (Jan 2026) |
| M4 — Data availability with URL/accession | ICMJE IV.A |
| M5 — Statistical software + version | SAMPL §1; Ou et al. p.2 |
| M6 — Multiple-testing correction declared | SAMPL §3 |
| M7 — Effect size + CI + exact p (not bare p) | SAMPL §2; Ou et al. p.3 |
| M8 — Counts before derivatives | ICMJE IV.A.3.e; SAMPL §2 |
| M9 — Limitations section non-trivial | ICMJE IV.A.3.f |
| M10 — Citations in prose ↔ references.md ↔ bibliography.bib | basic integrity |

---

## 7. Items mechanized in spec but with caveats

- **M5 (software + version):** regex looks for patterns like `R 4.1.2`,
  `scipy 1.11`, `pandas 2.0.3`. False positives possible if a tool name
  doesn't match common patterns; Methods agent's prompt must encourage
  the explicit pattern.
- **M6 (multiple-testing correction):** trigger threshold is "if >10
  p-values reported in Results, expect a correction-method statement."
  Ten is a heuristic; can be tuned.
- **M7 (effect size + CI):** the prose-walk for bare percentages requires
  some judgment. The validator flags as a soft warning, not a hard fail
  on first pass.

---

## 8. Items deliberately NOT mechanized

- Sex/gender stratification (BERIL projects are typically bacterial; not
  applicable). Conditional on `metadata.subject_type == "human"` —
  becomes M-tier when applicable.
- Pre-registration / trial registration (not applicable to computational
  reanalysis).
- IRB approval (not applicable when no human subjects).
- CONSORT / PRISMA / STARD checklists (different study types).
- Forest plots / Kaplan-Meier specifics (study-type-specific).

These are explicit non-goals (per SPEC §1.2 and §7.3).

---

## 9. Aspirational guidance items in prompts (not auto-checked)

Drawn from across the three sources; lives in per-section system prompts:

- Lead with novelty in Abstract and Introduction; do not rehash prior work
- Distinguish prespecified from exploratory analyses explicitly
- Discuss alternative explanations; do not rest on a single mechanistic
  interpretation
- Engage with conflicting prior findings when they exist; do not ignore
- Translate effect sizes to biologically meaningful units where possible
- Use conservative language for observational/correlational claims;
  reserve causal language for designs that support it
- Validate predictions against held-out data if making predictive claims
- For computational reanalysis: state data snapshot date and reanalysis
  rationale
- Acknowledge uncertainty in point estimates; report CIs / posteriors
- "P > 0.05 does not mean equivalence" — discipline language in
  Discussion and Limitations
- For observational designs: discuss potential bias and unmeasured
  confounders
- Two-sided p-values unless explicitly one-sided
- Avoid "trend" for borderline p-values

---

## 10. Subagent extraction note

The first-pass extraction by an agent (recorded to a draft file before
human review) proposed 20 mechanized items + 15 aspirational items. The
final SPEC §7 trims this to 10 + ~12 because (a) several proposed items
were too judgment-heavy to mechanize without false-positive risk
(e.g., "title includes study design signal"), (b) several were
study-type-specific and deferred to conditional checks (sex/gender, IRB),
and (c) the smaller mechanized set is easier to maintain and trust.

The subagent's PMC7642026 fetch was blocked at extraction time; Adam
subsequently uploaded the PDF (Ou et al. 2020), which is incorporated
above.
