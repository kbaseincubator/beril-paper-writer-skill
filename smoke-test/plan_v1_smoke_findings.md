# plan.v1 — first live-LLM smoke-test findings (v1 + v2 + post-processor)

**Run dates:** 2026-04-25 (v1), 2026-04-26 (v2), 2026-04-26 (post-processor build)
**Project:** `functional_dark_matter` (STRONG-tier; 14 notebooks; 271 code cells; canonical REPORT.md and RESEARCH_PLAN.md). Same project used in citation_pool.v1 and methods.v1 smoke tests.
**Throughline:** N/A — plan.v1 *produces* the throughline candidates from the project. Expected tier verdict: STRONG (per substantial REPORT and 14 notebooks).
**Model:** `claude-sonnet-4-5-20250929` (pinned, per Phase 4 cost discipline)
**Verdict:** **PARTIAL PASS** on first-order outputs (3 well-structured candidates, sensible weakness inventories, correct triage to STRONG); **FAIL** on the strength-glyph cross-walk discipline that the prompt was supposed to enforce. Two prompt-edit attempts did not fix the failure mode. Resolution: a programmatic post-processor (`tools/check_throughline_glyphs.py`) replaces the prompt-level discipline.

---

## Run-by-run summary

### v1 (2026-04-25)

- Cost / latency: $0.43 / 120 s wall clock / 9 turns
- Triage verdict: STRONG ✓ (matched expectation)
- Three candidates produced (TL1 prioritization, TL2 biogeographic concordance, TL3 pangenome conservation) — all coherent, well-anchored to REPORT findings
- Section presence: Evidence map ✓, Weakness inventory ✓, "Would NOT include" ✓ for all three
- **Failure:** All 21 Evidence-map sub-claims marked `✓ direct`. Zero `⚠ partial`, zero `✗ contradicts`, zero `◇ orthogonal`.
- Weakness inventories nonetheless named load-bearing caveats — weight-sensitivity (TL1), binomial p=0.072 marginal (TL2), kingdom-level "may be invisible to evidence layers" rebuttal (TL3), annotation-vintage confound (TL1) — that should have surfaced as `⚠ partial` on the corresponding sub-claims.
- Closing-message format: required-exact template emitted, but agent did not flag the all-✓ output as a self-review issue, indicating the prompt's anti-strength-inflation rules did not engage.

### Three-edit prompt fix (2026-04-26)

Three targeted edits to `prompts/plan.v1.md` (499 → 551 lines):

1. New "Cross-walk weakness inventory ↔ evidence map" anti-pattern (~14 lines): explicit rule that if weakness inventory says "X is partial/contested/coarse/weight-sensitive/marginal because Y," corresponding evidence-map entry must be `⚠ partial`.
2. Self-review item 5 expanded with hard constraint: "If any candidate's evidence map has zero `⚠ partial` AND zero `✗ contradicts` entries (i.e., 100% sub-claims marked `✓ direct`), HALT and re-walk." Plus exception clause for genuinely gap-free projects.
3. New self-review item 6 (cross-walk) and expanded item 7 (contradicting evidence including tested-and-rejected sub-hypotheses like H1b).

Plus two concrete anti-example pairs using `functional_dark_matter` caveats: binomial p=0.072 marginal-significance case + H1b rejection case. Self-review checklist re-numbered 1–11 (was 1–10).

### v2 (2026-04-26)

- Cost / latency: $0.42 / 152 s wall clock / 9 turns
- Triage verdict: STRONG ✓ (still correct)
- Three candidates produced — same prioritization / biogeographic / pangenome trio
- Glyph distribution after the fix:
  - TL1: 7 rows, **7 ✓ / 0 ⚠ / 0 ✗ / 0 ◇** (no improvement)
  - TL2: 7 rows, **6 ✓ / 1 ⚠ / 0 ✗ / 0 ◇** (one improvement: the binomial p=0.072 sub-claim was correctly downgraded to `⚠ partial — binomial marginal, Fisher's combined carries the load`. This was one of the explicit anti-example targets.)
  - TL3: 7 rows, **7 ✓ / 0 ⚠ / 0 ✗ / 0 ◇** (no improvement)
- The hard constraint ("HALT and re-walk if 100% ✓ direct") did not fire even though TL1 and TL3 each meet that exact condition.
- Closing message did not acknowledge the new self-review items 5/6/7 as having been run.

**Net of the prompt edits:** 1 of 21 sub-claims moved from `✓` to `⚠`. Two of three candidates still fail the cross-walk. The targeted anti-example pair (binomial p=0.072) propagated correctly; the untargeted caveat patterns (weight-sensitivity, annotation-vintage, kingdom-level invisibility) did not.

---

## Diagnosis: prompt-level discipline alone is insufficient for cross-walk checks

The plan.v1 failure mode is **strength inflation**: the agent operationalizes evidence strength as "is the source claim grounded in REPORT?" rather than "does the operational evidence support the strength-glyph the prompt defines?" A weakness inventory listing weight-sensitivity is *information the agent itself produced*, but the cross-walk back to the evidence-map glyph is a separate cognitive step the prompt asks for explicitly and the agent skips.

Two prompts edits — one a hard "HALT" instruction, one a re-walk anti-pattern, one targeted anti-example — did not reliably engage that step. The single edit that *did* take (binomial p=0.072 → `⚠`) was the one closest to the literal anti-example wording. Every other caveat pattern remained un-cross-walked.

**Conclusion:** prompt-level discipline that requires per-call enforcement of a cross-walk between two sections of the agent's own output is fragile. The expected value of a third prompt-edit attempt is negative; the same model on the same project would likely produce the same cross-walk gap.

The architectural lesson: **back disciplines that require cross-walking with programmatic post-checks**. Prompts should establish the discipline; programmatic checks should enforce it.

---

## Resolution: `tools/check_throughline_glyphs.py`

Built 2026-04-26 at `src/beril_paper_writer/skill/tools/check_throughline_glyphs.py`. Walks `throughline_candidates.md`, parses each `## Candidate TL{N}:` block, counts strength glyphs in the Evidence map markdown table, and cross-walks the Weakness inventory text against a curated keyword list (`marginal`, `weight-sensitive`, `coarse`, `compositional`, `arbitrary`, `annotation vintage`, `barely above chance`, `confounded`, `guilt-by-association`, `may be invisible`, `overstates`, `circular`, etc.) and a `p=0.0XX` p-value pattern.

For each candidate where Evidence map has 0 `⚠ partial` AND 0 `✗ contradicts` entries (≥3 rows total) AND Weakness inventory contains caveat keywords/p-values, emits a `WARN` line to stderr listing the detected caveats. Also emits per-candidate glyph counts and a `NOTE` line for the (rare) genuinely gap-free case.

**Always exits 0** — advisory only. The orchestrator surfaces warnings in plan.v1's closing message; does not gate the run.

### Self-check against the v2 candidates file

```
$ python3 src/beril_paper_writer/skill/tools/check_throughline_glyphs.py \
    smoke-test/throughline_candidates.md

TL1: 7 rows, 7 ✓ / 0 ⚠ / 0 ✗ / 0 ◇
WARN TL1: 7/7 sub-claims marked ✓ direct with 0 ⚠ / 0 ✗, but Weakness
  inventory names caveats: ['annotation vintage', 'annotation-lagging',
  'arbitrary', 'circular', 'guilt-by-association', 'moderately sensitive',
  'not experimental validation', 'sensitive to weight',
  'weight perturbations']. Cross-walk: at least one sub-claim should
  likely be ⚠ partial.
TL2: 7 rows, 6 ✓ / 1 ⚠ / 0 ✗ / 0 ◇
TL3: 7 rows, 7 ✓ / 0 ⚠ / 0 ✗ / 0 ◇
WARN TL3: 7/7 sub-claims marked ✓ direct with 0 ⚠ / 0 ✗, but Weakness
  inventory names caveats: ['arbitrary', 'coarse', 'may be invisible',
  'may misclassify', 'overstate', 'overstates']. Cross-walk: at least one
  sub-claim should likely be ⚠ partial.
complete: 2 warning(s).
```

The two cross-walk-failure candidates are flagged correctly. TL2 (which got the one binomial-p improvement) passes silently. Exit 0.

---

## What this means for orchestrator design (Phase 4)

1. **plan.v1 closing message must include a hand-off step** that the orchestrator can pattern-match: after plan.v1 writes `throughline_candidates.md`, the orchestrator runs `check_throughline_glyphs.py` and either (a) appends the WARN lines to the user-facing closing message before pausing for user-selection of a candidate, or (b) requests a re-walk if any WARN fired. Decision deferred — leaning toward (a) for v0.1 since the user is the panel-of-one reviewer and surfacing the warnings to a human is more honest than auto-rejecting candidates.
2. **The post-processor pattern generalizes.** Other prompts have similar cross-walk requirements (e.g., methods.v1's "operational threshold matches RESEARCH_PLAN's specified threshold," reframer.v1's "every reframing-log entry has a Resolution path:"). A `tools/check_*.py` per such discipline is a cleaner architectural pattern than continuing to try prompt-level enforcement. Each tool is small (~250 lines), unit-testable, and can be invoked from `paper_writer.sh` after the corresponding subagent returns.
3. **Memory entry created** capturing this lesson — see `feedback_prompt_discipline_needs_post_check.md` in auto-memory.
4. **plan.v1 prompt is left at 551 lines.** The three-edit fix did not hurt — the binomial-p case was correctly downgraded — but it's now overspecified for the load it can carry alone. Future revisions should not try to add more prompt-level discipline for cross-walks; instead, extend `check_throughline_glyphs.py`'s keyword list as new failure patterns surface in real use.

---

## Tool-call profile (read from stream-json log, v2 run)

(Pending — runbook §5 Python snippet for counting tool_use events should be applied to the v2 stream-json log when stored. Not load-bearing for the verdict.)

---

## Next-step recommendations

1. **Memorialize the prompt-level-discipline-is-fragile lesson** as a feedback memory (file: `feedback_prompt_discipline_needs_post_check.md`) so future smoke-test diagnoses of similar failure modes go straight to the post-processor pattern instead of looping on more prompt edits.
2. **Commit `tools/check_throughline_glyphs.py` and this findings doc** to the skill's working tree.
3. **Plan orchestrator MVP scope** — per the augmentation-stream-plan.md §7 active-work list. The plan.v1 → cross-walk-check → user-pause pattern is the first concrete handoff the orchestrator must implement. Other phases (citation_pool, methods, results, discussion) follow similar patterns.
4. **Defer further plan.v1 prompt edits** unless a new failure mode surfaces in a different project that the existing post-processor cannot catch with a keyword-list extension.

---

## Open questions deferred

- **Will the cross-walk failure mode reappear on THIN-tier projects?** functional_dark_matter is STRONG; THIN projects have less evidence depth and may have fewer caveats to surface. Worth a smoke run on a smaller project once one is available.
- **Is the keyword list complete?** The 22 keywords + p-value pattern were curated from this run's failure modes. Real-use coverage will reveal gaps. The post-processor's keyword list is the maintenance surface.
- **Does the warning surfacing block user productivity?** If the orchestrator surfaces warnings on every plan.v1 run, and most warnings turn out to be ignorable in real use, a "warnings-as-blockers" framing becomes noise. Surface visibly but not as gate; revisit after a few real runs.

---

*Findings written 2026-04-26 by Claude (Cowork session) based on Adam's executed v1 + v2 runs and the Cowork-side post-processor build.*
