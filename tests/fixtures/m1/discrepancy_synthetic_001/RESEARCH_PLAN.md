# Synthetic Research Plan — discrepancy_synthetic_001

This fixture exercises A1.b's pre-pass per M1_PUNCH_LIST.md AC:
"Given a synthetic plan with 5 analyses + a synthetic provenance with 4
(3 overlapping + 1 unprescribed), the pre-pass surfaces 6 candidates:
2 plan-only + 1 exec-only + 3 overlapping."

Five analyses below — three (P1, P2, P3) match (by normalized phrase) the
three statistical tests in methods_provenance.md; two (P4, P5) do not.

## Hypothesis

Compound X reduces growth rate of strain Y under aerobic conditions.
Two-sided alpha = 0.05 throughout, with Benjamini-Hochberg FDR correction
applied at the family level. (These are pre-registered thresholds, not
analyses.)

## Analysis Plan

The following analyses will be performed, each prespecified prior to data
collection:

- Pearson correlation between dose and OD600 endpoint, across the dose
  series.
- Two-sample t-test comparing mean OD600 of strain Y vs the WT control,
  per dose level.
- Fisher's exact test on the 2x2 contingency of viable / non-viable
  colonies, treated vs control.
- Kaplan-Meier survival curve fit to time-to-stationary-phase data,
  with log-rank test for treatment effect.
- Permutation test on the difference of medians, used as a sanity check
  on the parametric test above.

## Out-of-scope

Discussion of metabolomic data is deferred to a separate manuscript and
is intentionally not part of this analysis plan.
