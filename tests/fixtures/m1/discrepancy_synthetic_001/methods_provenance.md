# Methods Provenance

Synthetic provenance for discrepancy_synthetic_001. Mirrors the
extract_methods.format_methods_provenance_md output shape so the
discrepancy_register parser can read it.

Four executed statistical tests. Three (X1 Pearson, X2 t-test,
X3 Fisher) overlap with plan items P1/P2/P3 by normalized phrase.
One (X4 Mann-Whitney U) is unprescribed.

## Design Intent (from RESEARCH_PLAN.md)

(Truncated for fixture readability — not consumed by the discrepancy
parser, which reads "Statistical Tests Detected" only.)

## Statistical Tests Detected

### Pearson correlation

- `scipy.stats.pearsonr` in **notebooks/01_dose_response.ipynb** (cell 4, line 12)

### Two-sample t-test (Student or Welch)

- `scipy.stats.ttest_ind` in **notebooks/02_strain_comparison.ipynb** (cell 7, line 18)

### Fisher's exact test

- `scipy.stats.fisher_exact` in **notebooks/03_viability.ipynb** (cell 3, line 9)

### Mann-Whitney U test

- `scipy.stats.mannwhitneyu` in **notebooks/04_nonparametric_check.ipynb** (cell 5, line 14)

## Software and Versions

- **scipy** ==1.11.4  _(from requirements.txt)_

## Summary

- Notebooks scanned: 4
- Statistical test calls: 4 (4 unique)
