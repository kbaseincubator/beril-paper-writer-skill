# v0.7.0 Development Proposal — Pipeline Reliability

**Date:** 2026-05-03  
**Author:** Adam Arkin + Claude (Cowork)  
**Status:** PROPOSED — awaiting Adam's approval before implementation  
**Theme:** Reduce wasted LLM spend, catch errors mechanically, eliminate
single-shot failure modes  
**Timeline:** 1–2 sessions (tight)  
**Prerequisite:** v0.6.5.1 hotfix shipped (adversarial version-check
string parsing)

---

## Motivation

The v0.6.5 live run on `functional_dark_matter/draft_10` exposed
systemic reliability problems:

| Problem | Impact | Root cause |
|---------|--------|------------|
| 45% Write-never-invoked rate on rewrite dispatches (5/11) | ~$4–6 wasted per run; serial retry doubles wall-clock time | Context window too large — model reasons but doesn't act |
| Garbled figure captions (Figs 7, 8 contain leaked notebook text) | Visible quality failure in delivered manuscript | Single-shot caption generation with no mechanical filter |
| Ghost statistic (85/100 "agreement" in Discussion with no Results anchor) | Unverifiable claim in manuscript | No cross-section grounding check |
| False-positive reviewer findings (~30% noise in rewrite loop input) | Rewrite loop wastes passes on non-issues | Single-reviewer signal; no agreement filter |
| Template bugs (AI disclosure "0 pass(es)", cost tracking 0.0) | Credibility gap in ICMJE disclosure | Orchestrator wiring defects |
| M8 bare-percentage flood (46 warnings, mostly noise) | Validator noise drowns real issues | Validator too strict — doesn't recognize adjacent-count patterns |

The AI Scientist paper (Lu et al., Nature 2026) validates two
architectural principles relevant to these failures: (1) post-hoc
validation is permanent infrastructure, not temporary scaffolding —
hallucination rates drop with model scaling, not prompt engineering;
(2) ensemble generation + mechanical selection outperforms single-shot
+ retry for error-prone operations.

---

## Scope — What v0.7.0 delivers

### R1. Rewrite context reduction

**Problem:** `rewrite.v1.md` currently receives the full manuscript
(~15K tokens) + all review findings + the target section + the
throughline. Total context often exceeds 400K tokens (visible in
rewrite_summary.txt: `input=459,839` to `input=1,738,340`). The
model produces analysis but never calls Write.

**Fix:** Feed rewrite.v1 **only** the target section + the specific
findings routed to that section. No manuscript skeleton, no
throughline, no other sections. The rewrite prompt's job is
sentence-level revision against specific findings, not cross-section
coherence (that's the reviewer's job).

**Implementation:** Modify `stream_progress.py`'s rewrite dispatch to
construct a minimal context: (a) the section file contents,
(b) the findings JSON filtered to that section, (c) the rewrite
instruction. Strip everything else.

**Expected impact:** Context drops from 400K–1.7M tokens to
~30–80K tokens per dispatch. Write-never-invoked rate should drop
to <10%. Per-dispatch cost drops proportionally.

**Risk:** A rewrite that fixes a Results claim may introduce
inconsistency with the Abstract or Discussion. Acceptable because:
the next review pass will catch cross-section drift, and the current
architecture already handles this (review → rewrite is iterative).

**Test:** Unit test confirming rewrite dispatch constructs minimal
context. Integration test confirming Write is invoked on first
attempt with reduced context (mock LLM).

### R2. Ensemble caption generation (best-of-3)

**Problem:** `figure_caption.v1` produces garbled captions containing
leaked notebook text ("Load FB gene positions from Spark"), wrong
percentages (8.2% vs 28.2%), and incomplete sentences. Single-shot
generation means any failure ships.

**Fix:** Generate 3 caption candidates per figure. Score each
mechanically:

- **Code-smell filter:** Reject candidates containing function calls,
  variable names, SQL fragments, notebook comments (regex patterns:
  `\b(import|from|def|class|SELECT|INSERT|cell \d+)\b`,
  `Load .* from`, `# (?:cell|notebook|TODO)`).
- **Minimum length gate:** Reject candidates < 50 words.
- **Percentage cross-check:** Extract percentages from candidate;
  reject if any percentage appears in the body text with a different
  value at the same label.

Select the highest-scoring surviving candidate. If all 3 fail
code-smell, retry once with an explicit anti-pattern instruction
(current behavior, but now as last resort instead of first resort).

**Implementation:** New function in `stream_progress.py` or a
dedicated `ensemble_caption.py` helper. The figure_caption.v1
prompt is unchanged — the ensemble wraps the existing dispatch.

**Cost:** ~$0.30–0.90 per figure (3× current). For 8 figures,
adds ~$2.40–7.20 per run. Offset by reduced retry cost.

**Test:** Unit tests for code-smell regex, length gate, percentage
cross-check. Integration test confirming best-of-3 selection.

### R3. Parallel rewrite candidates

**Problem:** When a rewrite dispatch fails (Write never invoked), the
current system retries serially with an escalated prompt. This
doubles wall-clock time and the retry prompt's escalation text
adds context that may not help.

**Fix:** For each rewrite dispatch, launch 2 candidates in parallel.
Take whichever one calls Write first (or the one that calls Write
at all, if only one does). If both call Write, take the one whose
output is longer (crude but effective — longer rewrites tend to
address more findings).

**Implementation:** Modify `stream_progress.py`'s rewrite dispatch
to use parallel subprocess launches. Selection logic: (a) if
exactly one candidate called Write, use it; (b) if both called
Write, use the longer output; (c) if neither called Write, fall
back to current serial retry with escalated prompt (3rd attempt).

**Cost:** Same total token cost as current retry pattern (2
attempts), but parallel instead of serial. No net cost increase.
Wall-clock time per dispatch drops by ~50% in the failure case.

**Expected impact:** Combined with R1 (context reduction), the
3-attempt failure rate (neither parallel candidate nor escalated
retry calls Write) should be <2%.

**Test:** Unit test for selection logic. Integration test confirming
parallel dispatch and selection.

### R4. Ensemble fallback review with agreement scoring

**Problem:** The fallback reviewer produces findings with ~30%
false-positive rate. The rewrite loop wastes passes on non-issues
(e.g., "24.9% should be ~25%" flagged as Critical in review_1 but
not in reviews 2 or 3). No principled way to distinguish
high-confidence findings from noise.

**Fix:** Run 3 fallback reviews per review cycle. Deduplicate
findings by section + severity + textual overlap. Score each
finding by agreement count:

- **3/3 agreement:** High confidence — always routed to rewrite loop.
- **2/3 agreement:** Medium confidence — routed to rewrite loop.
- **1/3 agreement:** Low confidence — logged in audit but NOT routed
  to rewrite loop. Surfaced in next_actions.md as "low-confidence
  advisory."

Deduplication heuristic: two findings match if they target the same
section AND share ≥50% of quoted text (by word overlap), OR if they
reference the same manuscript line number range (±5 lines).

**Implementation:** New `ensemble_review.py` module. Inputs: 3
review markdown files. Output: deduplicated findings JSON with
agreement scores. The rewrite loop reads the deduplicated output
instead of a single review file.

**Cost:** ~$6–9 per review cycle (3× fallback reviewer cost of
~$2–3). For 2 review cycles, adds ~$12–18 per run. This is the
largest cost addition in v0.7.0.

**Risk:** Deduplication heuristic may merge distinct findings that
happen to reference the same section. Mitigation: log all 3 raw
reviews in audit/ for human inspection; the deduplication is for
the rewrite loop's consumption, not for suppressing information.

**Test:** Unit tests for deduplication heuristic (exact match,
partial overlap, no match). Integration test confirming agreement
scoring produces expected 3/3, 2/3, 1/3 buckets from synthetic
review triples.

### R5. Template and orchestrator fixes

Five small fixes, each independently shippable:

**R5a. AI disclosure pass count.** `phase_assemble` or
`phase_finalize` reads the actual rewrite pass count from
`audit/rewrite_summary.txt` and injects it into the AI-Assisted
Analysis section. Current: hardcoded "0 pass(es)".

**R5b. Cost tracking.** Wire per-phase cost accumulation into
`state.json`. Each phase writes cost metadata; the orchestrator
sums them into `cost_so_far_usd` and `elapsed_seconds`. Current:
both are 0.0.

**R5c. M8 adjacent-count recognition.** Tune the M8
bare-percentage validator to recognize "N genes (X%)" and
"N (X%) of M" as satisfying the counts-before-percentages
requirement. Current: flags every bare percentage regardless of
adjacent count presence. Expected: reduce false positives from
~46 to ~5–10 genuine bare percentages.

**R5d. Orphan-PMID check.** Extend M10 to also verify that PMIDs
listed in Data Availability resolve to a [N] reference entry or
are explicitly tagged as "data-source accession (no narrative
citation)." Current: M10 only checks [N] in prose.

**R5e. Rewrite pass count in metadata.** Ensure
`reframing_repairs.json` and review-pass metadata are written to
the audit/ directory so the next run (and human reviewers) can
inspect the full revision history.

**Test:** One unit test per fix. M8 test: synthetic manuscript
with "42 genes (73.7%)" should NOT trigger; bare "73.7%" should
trigger.

---

## Scope — What v0.7.0 does NOT deliver

| Item | Rationale for deferral | Target |
|------|----------------------|--------|
| Numerical consistency post-checker | Right idea but needs design to avoid over-specialization. Internal-consistency-only scope is clear; implementation deferred to avoid packing the release. | v0.7.1 |
| Discussion-grounding post-checker | Same — general forward-reference integrity check needs careful design. | v0.7.1 |
| Caption-body consistency post-checker | Partially addressed by R2's percentage cross-check. Full checker deferred. | v0.7.1 |
| Pre-drafting outline phase | Scope drift is correctly handled by detect-and-surface in v0.6.x. Outline phase is heavier architecture. | v0.8.0 |
| Section-level tree search | Selection function problem unsolved. Cost multiplier too high. | v0.9.0+ |
| Cost budget target | Fix cost tracking first (R5b), measure actual costs across several runs, then set target. | v0.8.0 |
| Canonical adversarial reviewer wiring | `phase_adversarial_audit` is designed in v0.6.3 but not wired. Depends on beril-adversarial v0.7.0+ being stable. | v0.7.1 |

---

## Implementation staging

### Pre-req: Ship v0.6.5.1

```
git add -A && git commit -F .commit-message-v0_6_5_1.txt && git tag v0.6.5.1
```

One-line fix: adversarial version-check string parsing. Already in
working tree.

### Session 1: Core reliability (R1 + R2 + R3 + R5)

**Phase A — Context reduction (R1).** Modify `stream_progress.py`
rewrite dispatch. Write unit test. This is the highest-ROI change
and should land first so everything after benefits.

**Phase B — Ensemble captions (R2).** New `ensemble_caption.py` or
inline in `stream_progress.py`. Code-smell regex, length gate,
percentage cross-check. Write unit tests for each filter.

**Phase C — Parallel rewrites (R3).** Modify rewrite dispatch to
launch 2 candidates. Selection logic. Falls back to serial retry
on double failure. Write unit test for selection.

**Phase D — Template fixes (R5a–R5e).** Five small changes across
orchestrator code. Unit tests for each.

**Phase E — Tests + version bump.** Full suite must pass. Bump to
v0.7.0-rc1 (or v0.7.0 if session 2 is just the live test).

### Session 2: Ensemble review + live test (R4 + validation)

**Phase F — Ensemble review (R4).** New `ensemble_review.py`.
Deduplication heuristic. Agreement scoring. Wire into rewrite
loop. Unit tests.

**Phase G — Integration test.** Synthetic end-to-end test with
mock LLM verifying: reduced context in rewrite dispatch, ensemble
caption selection, parallel rewrite selection, ensemble review
agreement filtering.

**Phase H — Live test on functional_dark_matter.** Re-run
draft_10 (or draft_11) with v0.7.0. Compare against v0.6.5
baseline: Write-never-invoked rate, caption quality, reviewer
false-positive rate, total cost, wall-clock time.

**Phase I — Ship.** Version bump, commit message, tag, install in
beril-extended.

---

## Success criteria

| Metric | v0.6.5 baseline (draft_10) | v0.7.0 target |
|--------|---------------------------|---------------|
| Write-never-invoked rate | 45% (5/11) | <10% |
| Caption code-smell failures | 2/8 figures | 0/8 |
| Reviewer false-positive rate | ~30% (estimated) | <15% (via agreement filter) |
| Rewrite dispatches needing retry | 5/11 | <2/11 |
| Cost tracking accuracy | 0.0 (broken) | Actual ±10% |
| AI disclosure pass count | "0" (wrong) | Correct count |
| M8 false positives | 46 | <10 |
| Total run cost | ~$30–35 (est.) | Track and report (no target) |

---

## Design notes for deferred work

### v0.7.1: Post-checkers (numerical consistency, discussion grounding)

The internal arithmetic checker should be general-purpose: extract all
`(label, number)` pairs from the manuscript; verify (a) same label →
same number everywhere, (b) declared totals match component sums where
relationship is explicit, (c) percentage partitions sum to ~100%.
No assumptions about project structure. No external file references.

The discussion-grounding checker should be a general forward-reference
integrity check: every quantitative claim in Discussion must have a
traceable source in Results or Methods. Not Discussion-specific —
the same principle applies to any section referencing numbers from
an earlier section.

### v0.8.0: Outline phase

Between throughline selection and section drafting, generate a
per-section outline: numbered subsections with one-line descriptions
and the specific throughline sub-claims each delivers. Drafting
prompts receive the outline as a structural constraint. A
`check_scope_adherence.py` post-checker compares actual section
headers against the outline and flags additions. Throughline remains
advisory (scope drift that survives adversarial review may be a
legitimate throughline update, not a manuscript bug).

### AI Scientist patterns to evaluate

- **Semantic Scholar integration for citation validation.** Their
  20-round iterative citation search is stronger than our static
  citation pool. Consider for v0.8.0+ as an enhancement to the
  `citation_pool.v1` phase.
- **Automated reviewer ensemble (5 reviews + meta-review).** Our
  v0.7.0 ensemble (3 fallback reviews) is a step in this direction.
  If the agreement-scoring approach works, consider scaling to 5
  and adding a meta-review synthesis step.
- **Model-pluggable architecture.** The AI Scientist shows quality
  jumps with newer models. Our prompts should be model-agnostic
  where possible; model selection should be a configuration knob,
  not hardcoded.

---

## References

- v0.6.5 live run artifacts: `projects/functional_dark_matter/papers/draft_10/`
- AI Scientist paper: Lu et al. (2026) "Towards end-to-end automation
  of AI research." Nature. doi:10.1038/s41586-026-10265-5
- beril-adversarial CONTRACT.md: cross-skill interop contract
- LAYOUT.md §Fabrication discipline: unified fabrication definition
- DECISIONS.md D-027 through D-033: v0.6.x design decisions
