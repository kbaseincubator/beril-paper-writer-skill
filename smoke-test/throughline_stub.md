# Throughline (chosen candidate)

This is a hand-crafted throughline for the citation_pool.v1 smoke
test, derived from the actual `REPORT.md` of `functional_dark_matter`
without invoking `plan.v1`. Edit if you want a different story
emphasis before running the smoke test.

The smoke test passes this file to `citation_pool.v1` as
`THROUGHLINE_PATH`; the pool builder uses the claim + evidence map
as the anchor for which citations to verify and include.

---

## Candidate TL1: Cross-organism integration of fitness data, conservation, and biogeography prioritizes 17,344 dark genes for experimental follow-up across 48 bacterial species, with 65 ortholog groups showing condition-class-conserved phenotypes as the strongest candidates

**Evidence map:**

| Sub-claim | Source | Strength |
|---|---|---|
| Across 48 organisms, 57,011 (24.9%) of 228,709 genes lack functional annotation; 17,344 of these are experimentally actionable (strong fitness OR essential) | REPORT.md §"Finding 1" | ✓ direct |
| 39,532 dark genes link to the pangenome via conservation_vs_fitness; 6,142 belong to ICA fitness modules | REPORT.md §"Finding 2" | ✓ direct |
| GapMind identifies 1,256 organism-pathway pairs with metabolic gaps in species harboring dark genes; domain matching narrows to 5,398 high-confidence (EC prefix) candidates | REPORT.md §"Finding 3" + NB10 §1 | ✓ direct |
| 65 ortholog groups show cross-organism fitness concordance — same dark gene producing same condition-class phenotypes across 3+ species | REPORT.md §"Finding 4" | ✓ direct |
| 30,756 dark gene clusters span diverse taxonomic breadth; 99.9% map to "universal" eggNOG OGs (so breadth classification is coarse and the species-count metric carries the discrimination) | REPORT.md §"Finding 5" | ⚠ partial — coarse-grained breadth labels acknowledged in REPORT |
| Cross-organism concordance is hypothesis-generating (suggests conserved function) but cannot establish mechanism | REPORT.md §"Interpretation" | ⚠ partial — REPORT explicitly disclaims mechanism |
| Biogeographic and pangenome patterns correlate with condition-specific fitness, but the biogeographic analysis depends on a `fb_pangenome_link` snapshot with known coverage gaps for recently-sequenced genomes | REPORT.md §"Data" + REVIEW.md if present | ⚠ partial |

**Weakness inventory:**

- Gap: No mechanistic validation of any prioritized candidate. The
  output is a ranked list, not a tested hypothesis. Discussion will
  need to disclaim this.
- Gap: GapMind co-occurrence analysis identifies organism-pathway
  pairs but does not assert any specific gene fills any specific
  gap — the domain-matching extension narrows the search space but
  is still a candidate set, not a confirmed assignment. The
  manuscript must report this caveat carefully.
- Rebuttal a sharp reviewer would offer: "Your '95 dark genes show
  cross-organism concordance' result is still 95 candidates that
  could each be wrong; what's the FDR controlling for, and what
  fraction of orthogonal organism-condition pairs do you'd expect
  to show concordance by chance?" — this requires explicit
  null-distribution analysis we should engage with in Discussion.
- Methodological caveat: 99.9% of dark gene clusters mapping to
  "universal" eggNOG OGs means the project's breadth analysis is
  effectively dominated by the species-count metric, not the
  taxonomic-breadth metric REPORT initially proposed. The
  manuscript needs to be clear about which metric carried the
  discriminating signal.

**What this paper would NOT include if this is chosen:**

- Single-organism deep-dive case studies (e.g. *D. vulgaris*
  Hildenborough alone) — orthogonal to the cross-organism
  prioritization story; → appendix or out.
- Mechanistic interpretation of any specific OG (e.g.
  Peptidase_M50 / ParE_toxin / DUF5064 functional speculation) —
  goes beyond the project's evidence; → Discussion limitations,
  not Results claims.
- A standalone GapMind methods paper — the GapMind analysis is one
  evidence layer among five here; a separate Methods paper would
  pull it out of context.
- Detailed biogeographic mapping per geographic region — REPORT
  has these as supporting data but not as a load-bearing finding;
  → appendix.

---

**Note for smoke-test reader:** This is a single-candidate file
because we're skipping `plan.v1`. In a production run, `plan.v1`
would produce 2–3 candidates in `throughline_candidates.md`, the
user would pick, and the orchestrator would write the chosen
candidate here at `00_throughline.md` in the same template format.
The candidate above is one of three plausible STRONG-tier
throughlines the project supports — the cross-organism
prioritization narrative. The other two would be:

- A GapMind-centric narrative (Finding 3 as the headline; cross-
  organism is supporting).
- A phylogenetic-breadth + experimental-priority narrative
  (Findings 5–7 as the headline; the manuscript reports the
  prioritized candidate list as actionable predictions).

Smoke test uses TL1 because it's the most quantitative and has the
clearest evidence chain.
