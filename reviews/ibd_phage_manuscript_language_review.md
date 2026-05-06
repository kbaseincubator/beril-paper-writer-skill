# Language Quality Review: ibd_phage_manuscript

**Reviewed:** 2026-05-06
**Reviewer:** Independent agent (Opus, language-focused)
**Scope:** Language quality only — sentence complexity, undefined terms,
transitions, register, LLM writing patterns

---

## Executive Summary

The manuscript has 7 systematic language problems, all prompt-fixable:

1. **Compound-sentence avalanches** — 50-60 word sentences chaining 3+
   ideas with conjunctions. Main verb buried past word 25 in key claims.
2. **Parenthetical overload** — 2-4 nested parenthetical insertions per
   sentence in Results, obscuring main clauses.
3. **Undefined/late-defined terms** — 12+ abbreviations and project-
   internal terms used before definition or never defined (Tier-A,
   phage GAP, CB-ORF, BERDL, K-BERDL, MIBiG, LOSO, etc.).
4. **Quantitative stacking** — sentences embedding 3+ statistical results
   inline, forcing readers to parse multiple fractions mid-sentence.
5. **Hedging cascades** — 2-4 hedging markers stacked on single claims
   ("best understood as a candidate workflow derived from one trajectory,
   not a rule with established external validity").
6. **Missing transitions** — no bridging sentences at Abstract→Intro,
   Methods→Results, between Results subsections, or Results→Discussion.
7. **Echo repetition** — key statistics (88.2% sign-concordance) stated
   4× across Abstract, Results, Findings Summary, and Discussion.

## Top 10 Worst Sentences (with rewrites)

### 1. Abstract opener (59 words, 3 chained ideas)

**Original:** "Crohn's disease and ulcerative colitis manifest
reproducible gut-microbiome dysbiosis that has motivated lytic-phage
cocktails as a compositional therapy, but pathobiont identification from
pooled multi-cohort microbiome data is vulnerable to host-variable
confounding, and lytic-phage availability for the broader gut-anaerobe
pathobiont set has not been systematically stratified against
ecotype-resolved targets."

**Pattern:** Compound-sentence avalanche.

**Rewrite:** "Crohn's disease and ulcerative colitis exhibit reproducible
dysbiotic microbiota signatures, motivating lytic-phage cocktails as
compositional therapy. However, pathobiont identification in pooled
analyses is confounded by host variables. Moreover, lytic-phage
availability for gut anaerobes remains unquantified against
ecotype-resolved targets."

### 2. Methods dataset sentence (3 parenthetical insertions)

**Original:** "We integrated 8,489 curatedMetagenomicData MetaPhlAn3
samples (training), 1,627 HMP2 samples (external validation), and 26
UC Davis Crohn's disease samples from 23 patients with PhageFoundry
experimental susceptibility data (96 phages × 188 Escherichia coli
strains), HMP2 endogenous virome, and paired metabolomics."

**Pattern:** Parenthetical avalanche.

**Rewrite:** "We analyzed three datasets: (1) 8,489 MetaPhlAn3 samples
from curatedMetagenomicData (training), (2) 1,627 HMP2 samples
(external validation), and (3) 26 samples from 23 UC Davis Crohn's
disease patients. We integrated PhageFoundry susceptibility data
(96 phages × 188 E. coli strains), HMP2 endogenous virome, and paired
metabolomics."

### 3. Tier-A scoring sentence (verb at word 48/52)

**Original:** "Integration of ecotype-specific enrichment (E1 or E3
Tier-A membership), cross-ecotype engraftment confirmation (donor 2708
fecal microbiota transplant study pathobionts), co-occurrence module
membership, biosynthetic gene cluster (BGC) repertoire (MIBiG
iron-siderophore and genotoxin annotations), curated
biosynthesis-associated ORF (CB-ORF) Crohn's disease enrichment, and
strain-adaptation gene signatures (Kumbhari IBD-biased gene predictor)
across 71 scored candidates yielded six actionable targets with total
scores ≥ 2.5 on a 0–4 scale."

**Pattern:** Parenthetical complexity + subject-verb separation.

**Rewrite:** "We scored 71 candidate species across six evidence lines:
ecotype-specific enrichment, cross-ecotype engraftment, co-occurrence
module membership, biosynthetic gene clusters (MIBiG annotations),
biosynthesis-associated ORF enrichment, and strain-adaptation genes
(Kumbhari predictor). Six candidates achieved actionable status (total
score ≥ 2.5 on 0–4 scale)."

### 4. Ecotype description mega-sentence (150+ words)

**Original:** "The four consensus ecotypes applied to 8,489
curatedMetagenomicData samples yielded biologically coherent clusters
(Fig. 1B): E0 (n = 3,604 samples, 42.5%) enriched in healthy controls
(3,562 of 5,333 healthy samples, 66.8%) and characterized by diverse
commensals (Faecalibacterium prausnitzii 6.8%, Ruminococcus bromii
4.5%, Bacteroides uniformis 4.6%, Phocaeicola vulgatus 4.4%); E1
(n = 2,601, 30.6%) transitional Bacteroides-dominant..."

**Pattern:** List-ification cascading into appositive stacking.

**Rewrite:** Split into 4 sentences, one per ecotype.

### 5. E1 Tier-A list (appositive stacking)

**Original:** "E1 Tier-A comprised 51 candidates (meta-analysis
HallAB_2017 + NielsenHB_2014, 82 CD / 280 non-IBD), all 100%
sign-concordant across substudies, with top-ranked species M. gnavus
(CLR-Δ +4.85), Streptococcus salivarius (+3.26)..."

**Pattern:** Multiple appositives; ambiguous modifier scope.

**Rewrite:** Split into 3 sentences: sample sizes, concordance, top species.

### 6. HMP2 replication (nested quantitative claims)

**Original:** "Ecotype distribution stratified diagnosis categories
significantly (subject-level χ²(2) = 15.6, p = 0.016; Fig. 2B), with
Crohn's disease and ulcerative colitis patients concentrating in E1
(106 of 130 HMP2 subjects, 82%) relative to healthy controls (3 of 21,
14% E1; 15 of 21, 71% E0)."

**Pattern:** Nested quantitative claims in parentheses.

**Rewrite:** Split chi-squared result from concentration pattern.

### 7. E1 pathobiont module (quantitative ambiguity)

**Original:** "All nine UC Davis E1 patients carried four to six of the
six actionable Tier-A species (mean 4.89 targets per E1 patient;
range 4–6), constituting the full five-species pathobiont module..."

**Pattern:** Quantitative ambiguity ("four to six of the six").

**Rewrite:** "Each E1 patient carried four to six of the six actionable
species (mean 4.89). Together, patients harbored the full five-species
pathobiont module."

### 8. AIEC phage precedent (jargon + vague causality)

**Original:** "The clinical-trial-stage AIEC phage precedent of [20]
in CEABAC10 transgenic mice underwrites our E. coli targeting strategy
and is consistent with the iron-acquisition fitness axis..."

**Pattern:** Jargon ("underwrites") + vague causality chain.

**Rewrite:** "Prior phage targeting of AIEC in transgenic mice [20]
supports our E. coli cocktail design. Mechanistically, AIEC depends on
yersiniabactin-mediated iron acquisition [21]..."

### 9. [METHOD UNCLEAR] marker (unresolved editorial note)

**Original:** "[METHOD UNCLEAR: alpha level for individual Mann-Whitney
tests — FDR correction applied at q < 0.05, but pre-correction alpha
not explicitly stated...]"

**Pattern:** Draft artifact left in manuscript.

**Fix:** Remove bracket; clarify: "We used two-sided Mann-Whitney U
tests with Benjamini-Hochberg FDR correction at q < 0.05."

### 10. Quadruple-hedged dosing claim

**Original:** "The state-dependent dosing rule is therefore best
understood as a candidate clinical workflow derived from one trajectory,
not a rule with established external validity."

**Pattern:** Cascade hedging (4 markers).

**Rewrite:** "The proposed dosing strategy derives from a single
patient's ecotype transition and requires prospective validation."

## Undefined Terms (12 critical)

Tier-A, Tier-B, phage GAP, CB-ORF, AIEC (late expansion), BERDL,
K-BERDL, MIBiG, INPHARED, IMG/VR, LOSO, CEABAC10, "actionable"
(never defined), ecotype (never placed in enterotype context until
Discussion), "confound-controlled within-substudy design" (operational
but not plain-language defined).

## Transition Assessment

| Boundary | Transition | Quality |
|---|---|---|
| Abstract → Intro | None | Missing |
| Intro → Methods | None | Missing |
| Methods → Results | None | Missing |
| Between Results subsections | Formulaic or absent | Weak |
| Results → Discussion | Double-summary (Findings Summary + Discussion opener) | Redundant |
| Between Discussion subsections | Absent | Missing |

## Echo Repetition: "88.2% sign-concordance"

Stated 4 times: Abstract, Results, Findings Summary, Discussion.
Should appear at most in Results (primary) and Abstract (summary).

## 7 Prompt Rules Recommended

1. **25-word verb cap** — main verb within 25 words of sentence start
2. **1 parenthetical per sentence** — restructure if 2+ needed
3. **Abbreviation expansion on first use** — never use before defining
4. **Quantitative stacking limit** — max 2 stats per sentence
5. **Single-hedge rule** — 1 hedging marker per claim; caveats in
   separate sentence
6. **Explicit transitions** — link-back + preview at every section boundary
7. **Notebook citation externalization** — table in Methods, not inline
