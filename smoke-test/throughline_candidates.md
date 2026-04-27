# Throughline Candidates

## Candidate TL1: Multi-dimensional evidence integration enables experimental prioritization of functionally dark bacterial genes with measurable phenotypes

**Evidence map:**

| Sub-claim | Source | Strength |
|---|---|---|
| 57,011 dark genes (24.9%) identified across 48 organisms; 17,344 have measurable phenotypes (fitness or essentiality) | REPORT §Finding 1 | ✓ direct |
| Multi-dimensional scoring (6 axes: fitness, conservation, inference, pangenome, biogeography, tractability) produces ranked candidates | REPORT §Finding 8; notebook 05_prioritization_dossiers.ipynb | ✓ direct |
| Top 100 candidates have 82% high-confidence functional hypotheses supported by 3+ evidence types | REPORT §Finding 8 | ✓ direct |
| Evidence-weighted set-cover selects 42 organisms (28 genera) covering 95% of priority | REPORT §Finding 13; notebook 09_final_synthesis.ipynb | ✓ direct |
| Conserved gene neighborhoods (21,011 pairs, 10,150 conserved in ≥3 organisms) and co-fitness validation (2,899 pairs, 1,129 mutual top-5) strengthen predictions | REPORT §Finding 12; notebook 08_improved_neighborhoods.ipynb | ✓ direct |
| Darkness spectrum (T1 Void through T5 Dawn) classifies all dark genes by evidence depth; only 7.5% have zero evidence | REPORT §Finding 13 | ✓ direct |
| Bakta reannotation reclassifies 83.7% of linked dark genes; all 100 top candidates gain functional descriptions | REPORT §Finding 15; notebook 12_bakta_enrichment.ipynb | ✓ direct |

**Weakness inventory:**

- Gap: Annotation bias — some "hypothetical" genes may have annotations in databases not checked; dark gene count (57,011) likely overestimates true unknowns (Limitation #3).
- Gap: Essential gene scoring penalty — 9,557 essential dark genes (55% of scored) score poorly in NB05 framework because they lack fitness magnitudes; separate NB07 prioritization required (Limitation #7).
- Gap: Scoring weights are expert-assigned and arbitrary; top-50 lists moderately sensitive to weight perturbations (64% retention for conservation-dominant config; Limitation #11). Robust rank indicators provided for 18 always-top-50 fitness-active and 6 always-top-50 essential genes.
- Gap: Bakta reclassification finding (§15) reveals annotation vintage issue — top candidates are genes annotated in UniProt but not in Fitness Browser's older annotation set. This complicates the claim that these are "functionally dark" vs "annotation-lagging."
- Methodological caveat: Module predictions are guilt-by-association, not experimental validation (Limitation #4). The "high confidence" label reflects evidence convergence, not experimental proof.
- Rebuttal a sharp reviewer would offer: "The prioritization is circular — you score genes highly because they have multiple evidence lines, but those evidence lines (module membership, co-fitness, domains) are themselves computational predictions, not experimental ground truth. You've built a consensus of predictions, not a validation."

**What this paper would NOT include if this is chosen:**

- Lab-field concordance findings (§7) — methodologically important but orthogonal to the prioritization claim; → appendix or supplementary with discussion of biogeographic validation as supporting evidence for ecological relevance
- Full pangenome conservation analysis (§14 NB11/11b/11c) — conservation-weighted Route B is a parallel prioritization approach optimized for different objectives (discovery vs hypothesis-testing); including both routes in the main paper would confuse the narrative → supplementary methods with brief main-text mention that two complementary routes exist
- H1b rejection (stress vs carbon/nitrogen accessory rates) — formally tested null result; → appendix with discussion of why the hypothesis was wrong
- NMDC trait-condition validation (§7, NB06 Section 3) — compositional coupling inflates significance; compositional inflation factor ~20×; → supplementary with caveat or out

---

## Candidate TL2: Genome-wide fitness profiling combined with biogeographic validation reveals that dark genes' lab phenotypes correspond to environmental selection pressures

**Evidence map:**

| Sub-claim | Source | Strength |
|---|---|---|
| 7,787 dark genes show strong fitness effects (|fit| ≥ 2); 9,557 are essential | REPORT §Finding 1 | ✓ direct |
| Within-species carrier vs non-carrier tests find 10/137 clusters with significant environmental enrichment (FDR < 0.05) | REPORT §Finding 6; notebook 03_biogeographic_analysis.ipynb | ✓ direct |
| Lab-field concordance rate 61.7% (29/47 testable clusters); binomial p=0.072 (marginal); Fisher's combined p=0.031 | REPORT §Finding 7; NB10 Section 5 formal test | ⚠ partial — binomial marginal, Fisher's combined carries the load |
| NMDC independent validation confirms 4/4 pre-registered abiotic predictions (nitrogen~nitrogen, pH~pH, anaerobic~dissolved oxygen) | REPORT §Finding 7; notebook 04_lab_field_concordance.ipynb | ✓ direct |
| Top candidate AO356_11255 (*P. putida* N2C3) shows strongest biogeographic signal: 80% carriers from soil/freshwater/wastewater vs 8.3% non-carriers (OR=44, FDR=0.093), matching nitrogen utilization lab phenotype | REPORT §Finding 7 | ✓ direct |
| *Pseudomonas* dark genes with stress/nitrogen phenotypes enriched in clinical isolates; *P. syringae* dark genes with in-planta phenotypes enriched in plant-associated genomes | REPORT §Finding 6 | ✓ direct |
| Cross-organism fitness concordance identifies 65 ortholog groups with conserved phenotypes across 3+ organisms | REPORT §Finding 4; notebook 02_gapmind_concordance_phylo.ipynb | ✓ direct |

**Weakness inventory:**

- Gap: AlphaEarth embeddings cover only 28% of genomes (83K/293K); NCBI isolation source metadata inconsistent; limits biogeographic test power (Limitation #1).
- Gap: Lab-field concordance binomial test is marginal (p=0.072); Wilson CI [0.474, 0.742] includes 0.50; Fisher's combined p=0.031 provides stronger aggregate evidence but is indirect (NB10 Section 5; Limitation #9).
- Gap: Dark-vs-annotated biogeographic null control was not run; binomial test and Fisher's combined probability provided but full null comparison using annotated accessory genes would strengthen H0 rejection (Limitation #9).
- Gap: NMDC validation operates at genus level; misses species-specific signals; only 5/6 carrier genera mapped; high significance rate (76/105) likely reflects dominance of common genera (*Pseudomonas*, *Klebsiella*) in both datasets (Limitation #2).
- Gap: NMDC trait-condition correlations (7/7 pre-registered confirmed, FDR < 10⁻²¹) likely reflect compositional coupling — genera abundant in sample contribute to both carrier abundance and community trait scores; inflation factor ~20× for exploratory tests (Limitation #8).
- Methodological caveat: Pre-registered condition-environment mapping specified 7 mappings; implementation used 6 (consolidating metal/osmotic/oxidative into "stress" because FB expGroup field uses broad categories; adding motility/anaerobic as they emerged). Consolidation was necessary given data structure but deviates from plan.
- Rebuttal a sharp reviewer would offer: "The 61.7% concordance rate is barely above chance (binomial p=0.072), and the NMDC correlations are confounded by taxonomic abundance. The biogeographic signal could reflect phylogenetic structure rather than gene function — common genera carrying common genes in common environments."

**What this paper would NOT include if this is chosen:**

- GapMind pathway gap-filling (§3) — organism-level co-occurrence, not direct gene-to-step assignments; domain matching (NB10) partially addresses but still not validated → appendix or out
- Full prioritization scoring framework (§8, NB05) — orthogonal to biogeographic claim; this candidate focuses on ecological validation, not experimental prioritization → brief mention or out
- Essential gene prioritization (§11, NB07) — separate analysis using different evidence (neighbor context, CRISPRi tractability) → out or separate paper
- Conservation-weighted Route B (§14, NB11) — parallel approach optimized for discovery rather than hypothesis-testing → supplementary or out
- Synteny/co-fitness validation (§12, NB08) — strengthens operon predictions but doesn't bear on biogeographic claim → out

---

## Candidate TL3: Pangenome-scale conservation analysis reveals that over half of bacterial dark gene families are kingdom-level — broadly conserved across thousands of species yet functionally uncharacterized, defining fundamental knowledge gaps in microbial biology

**Evidence map:**

| Sub-claim | Source | Strength |
|---|---|---|
| Full pangenome query (27,690 species, 93.5M gene clusters) maps dark gene root OGs to species counts ranging 1–27,482 (median 135) | REPORT §Finding 14; notebook 11b_extended_conservation.ipynb | ✓ direct |
| 55.9% of dark gene OGs are kingdom-level (pan-bacterial, present across multiple phyla); at gene level 51.4% kingdom-level | REPORT §Finding 14 | ✓ direct |
| Top-ranked OGs by importance (conservation × ignorance) include COG0468 (27,427 species, 142 phyla), COG0443 (27,279 species), COG0491 (27,393 species) — all true knowledge gaps with zero functional evidence | REPORT §Finding 14 | ✓ direct |
| Dual-route covering set optimization: Route A (evidence-weighted) vs Route B (conservation-weighted) produce different organism orderings reflecting different experimental strategies; 39/42 organisms shared | REPORT §Finding 14; Experimental Recommendations dual-route table | ✓ direct |
| Extended covering set (73 organisms: 48 FB + 25 literature-curated) produces 50-organism set covering 98.7% of OGs across 6 phyla vs 41 organisms/4 phyla for FB-only | REPORT §Finding 14; notebook 11c_extended_covering_set.ipynb | ✓ direct |
| Darkness spectrum classification (T1 Void through T5 Dawn) shows only 7.5% have zero evidence; 39.5% T4 Penumbra have 3–4 converging lines | REPORT §Finding 13 | ✓ direct |
| OG_id propagation recovers 5,206 additional dark genes (57.5% → 66.6% coverage) by transferring root_og assignments within 48-organism ortholog groups | REPORT §Finding 14; notebook 11b | ✓ direct |

**Weakness inventory:**

- Gap: Prior NB05 phylogenetic breadth used coarse eggNOG classification (99.9% "universal"); species-count metric provides finer resolution but NB11 full-pangenome query required to properly rank conservation (Limitation per NB11 intro; Finding 5 note).
- Gap: 48 FB organisms are 77% Pseudomonadota (37/48); major phyla (Bacillota, Actinomycetota, Campylobacterota) absent or underrepresented; extended covering set partially addresses (6 phyla vs 4) but non-FB organisms lack FB condition profiling (Limitation #12).
- Gap: Non-FB OG coverage estimated at genus level (any species in genus has OG); overestimates individual organism coverage; Bacillota organisms (B. subtilis, S. aureus) not selected by covering set algorithm because OGs are subsets of Pseudomonadota coverage, despite value for Gram-positive context (Limitation #12).
- Gap: Conservation-weighted Route B organism list has no condition-specific experiment protocols (true knowledge gaps lack fitness data by definition); can only recommend broad phenotypic screens, not targeted experiments (noted in Experimental Recommendations).
- Gap: Hypothesis status classification (strong/weak/true knowledge gap) uses thresholds for evidence types that are somewhat arbitrary; "true knowledge gap" = zero evidence, but "weak lead" vs "strong hypothesis" boundary is expert judgment.
- Methodological caveat: Mobile element detection via phylogenetic patchiness (present in distant phyla but few species per phylum) + COG-X; 6.5% of OGs classified as mobile, but this heuristic may misclassify genuinely patchy distributions.
- Rebuttal a sharp reviewer would offer: "The kingdom-level OGs may be universally conserved *because* they have essential functions that produce no differential fitness signal — calling them 'true knowledge gaps' overstates the case. They may be invisible to your evidence layers not because we know nothing, but because the evidence types you use (fitness, modules, GapMind) don't apply to housekeeping genes."

**What this paper would NOT include if this is chosen:**

- Biogeographic validation (§6, §7) — orthogonal to conservation claim; focuses on accessory gene environmental distributions rather than kingdom-level conservation → out or brief discussion
- GapMind pathway gap-filling (§3) — limited to amino acid biosynthesis and carbon utilization; doesn't capture signaling/regulation/structural roles where kingdom-level OGs likely reside → out
- Lab-field concordance (§7) — tests ecological relevance of condition-specific phenotypes; conservation-weighted Route B targets genes with no condition predictions → out
- Essential gene CRISPRi prioritization (§11, NB07) — separate scoring framework; some overlap with Route B (essential genes in tractable organisms) but methodologically distinct → brief mention or out
- H1 sub-hypothesis testing (H1a–H1e) — formulated before NB11 full-pangenome analysis existed; conservation claim is a new finding not part of original hypothesis structure → discussion can relate to H1c (cross-organism concordance) but not central

---
