# paper-writer v1.x backlog

**Last updated:** 2026-05-20 (post-#41 ship: #41 closed with
deterministic re-run results — 23→5 ungrounded across dev set,
78% reduction; #44 filed for the newly-surfaced D2 `77.8%`
residual; #40 updated with confirmed real residuals).
**Status:** carryover ledger for findings surfaced during Stage 7
Phase C (dev runs D1/D2/D3) and BERIL-comparison analysis. Review
before Phase D holdout, and before any v1.0 tag.

## What this file is

Cross-conversation persistent ledger of v1.x candidate work. Items
are findings from live runs, BERIL-comparison analysis, or
architectural observations that need addressing but aren't part of
the active conversation's in-flight work.

Items here are NOT in-flight tasks (those live in the conversation's
TaskList — ephemeral). When work begins on an item, copy its entry
into the conversation and update / close here when done.

Severity tags:
- **P0** — block v1.0 ship.
- **P1** — ship-noted (document as known limit in RELEASE_NOTES).
- **P2** — post-v1.0 work.

Format per entry: stable internal ID (matches the task ID from the
2026-05-18 conversation for continuity), one-line summary, evidence,
lever / proposed fix, status.

---

## P0 — block v1.0 ship

(Active P0 list is empty as of 2026-05-20. #41 shipped — see below.
Promote any entry here when a new P0 surfaces.)

### #41 — Tier T extractor false positives: scientific notation, K-suffix, trailing-zero precision (SHIPPED)

**Evidence (Stage 7 dev runs forensic, 2026-05-20):**
First-pass diagnosis attributed dev-set Tier T failures to drafter
discipline (#40). Reading `audit/iter_1/numeric_grounding.json`
against `claim_inventory.tsv` and `REPORT.md` for D1/D2/D3 inverted
the diagnosis — most ungrounded numerics are **extractor false
positives**, not drafter fabrications.

- **D2 amr_pangenome_atlas — 12 of 14 ungrounded are scientific-
  notation mantissa-only matches.** Every flagged P0 has shape
  `match_class=ratio_with_unit`, `matched_text="N.N x"`,
  `normalized_value="<mantissa>"`. The manuscript text is
  `p = 1.1 x 10^-130 [C-006]` (etc.); the inventory contains
  `Wilcoxon p=1.1e-130` at C-006. Same value, same marker, fails
  set lookup because `normalize_numeric` returns `"1.1"` for the
  manuscript and `"1.1e-130"` for the inventory.
- **D3 phb_granule_ecology — 3 of 3 ungrounded:** two scientific-
  notation (`1.5 x 10^-43` vs `1.5e-43`; `1.77 x 10^-6` vs
  `1.77e-06`) and one K-suffix (`83,000` vs inventory's `83K`).
- **D1 conservation_vs_fitness — 4 of 6 ungrounded are extractor
  artifacts:** comma-tokenization (`n=22` extracted from
  `n=22,751`); `82` vs `82.0` precision mismatch on the same value
  (C-006 inventory has `82.0%`, manuscript has `82%`). The
  remaining 2 (the `95%` pangenome threshold and the `80%`
  Tettelin external citation) are legitimately ungrounded — the
  threshold is a definition from the source dataset; the Tettelin
  number is an external citation, allowlist territory.

**Root cause:**
`src/beril_paper_writer/skill/tools/check_numeric_grounding.py`
`_NUMERIC_PAYLOAD_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")`
requires an `[eE]` exponent marker for scientific notation.
Manuscript `1.1 x 10^-130` tokenizes as three numbers
(`1.1`, `10`, `-130`); inventory `1.1e-130` tokenizes as one. The
two are the same value but never equal as normalized strings.
Compounding: `build_normalized_set` does not normalize K/M/G/T
suffixes; set lookup is exact-string, no trailing-zero tolerance.

**Lever:** Three normalization extensions in
`check_numeric_grounding.py`:

1. **Scientific notation recognition.** Recognize
   `\d+(?:\.\d+)? ?(?:[xX×]) ?10\^?[-+]?\d+` in BOTH the matched
   text and the inventory/REPORT text; normalize to canonical
   `<mantissa>e<exp>` with zero-padded exponent (`1.77e-6` and
   `1.77e-06` collapse to the same key).
2. **K/M/G/T suffix expansion.** `83K` → `83000`, `1.5M` →
   `1500000`. Apply on both sides.
3. **Trailing-zero tolerance in set lookup.** `82` matches
   `82.0`; `0.3` does NOT match `0.302` (lenient on trailing
   zeros, strict on truncation).

**Expected impact (deterministic Python; re-runnable for $0 LLM):**
D2 14 → 0-2, D3 3 → 0, D1 6 → 1-2. The residual 1-2 are real
(D1's `95%` cluster-definition threshold + `80%` Tettelin).

**Status: SHIPPED 2026-05-20.** Code landed in commit
documented at `.commit-message-d052-tier-t-normalization.txt`;
architecture decision at DECISIONS.md D-052. Sandbox in-process
deterministic re-run against `manuscript.iter_1.md` for D1+D2+D3
post-fix:

| Project | Pre-#41 | Post-#41 | Δ |
|---|---|---|---|
| D1 conservation_vs_fitness | 6 | 3 | −3 |
| D2 amr_pangenome_atlas | 14 | 2 | −12 |
| D3 phb_granule_ecology | 3 | 0 | −3 |
| TOTAL | 23 | **5** | **−18 (78% reduction)** |

Confirmed real residuals (NOT extractor artifacts):
- D1 — 2× `95%` pangenome cluster definition + 1× `80%` Tettelin
  external citation. Tracked at #40 (drafter discipline) and
  external-citation allowlist (not yet filed).
- D2 — 2× `77.8%`/`22.2%` mechanism classification. Newly
  surfaced post-#41. Filed as #44 (P2) for investigation.

### #40 — Drafter first-cut Tier T discipline (residual, post-#41)

**Status:** **Demoted to P1 2026-05-20.** The dev-set evidence for
this entry was misattributed; #41 captures the dominant defect
(extractor false positives, not drafter fabrications). The
manuscript text the original entry called out (`p = 1.1 x 10^-130`,
`rho = 0.302, p = 1.5 x 10^-43`, etc.) is grounded in the inventory
and tagged with the correct `[C-NNN]` marker — the drafter did the
right thing; the verifier didn't see it.

This entry is kept open as a **placeholder for the residual** that
may remain after #41 ships. Best estimate of the residual (from
D1's 6 ungrounded breakdown):
- Cluster-definition thresholds the drafter introduces without
  source provenance (e.g. D1's `95%` pangenome core definition).
- External citation numbers (e.g. D1's `80% Tettelin`) — better
  handled by an allowlist rule than by drafter discipline.

**Re-evaluation gate:** after #41 ships and dev-set is rerun
deterministically, if any project's Tier T count is still > 1-2,
re-open this entry with the actual residual cases as evidence.

Lever options (carried over from the original entry, retained for
reference):
1. Tighten `holistic_draft.v1.md` numeric verification rule.
2. **(Closed)** Add `[unverified: ...]` token (#30) as escape hatch
   — see #30 in the Closed section below for the rationale.
3. Drafter-time Tier T check (bounded retry pattern).

---

## P1 — ship-noted (document as known limit)

### #46 — Drafter pulls RESEARCH_PLAN.md predictions into Results as measured findings

**Evidence (Stage 7 holdout H3 `metal_specificity`, 2026-05-20):**
The drafter pulled hypothesis/prediction values from
`RESEARCH_PLAN.md` into the Introduction and Results **as if they
were measured results**:

- `OR=2.08`, `p=4.3×10⁻¹⁶²` (Introduction) — RESEARCH_PLAN figures,
  not in REPORT.md.
- "predicted >90% core" / "predicted <80% core" — RESEARCH_PLAN
  sub-hypotheses H1a/H1b.
- Figure 2 caption "~30% at 1%, ~75% at 20%" — RESEARCH_PLAN
  predictions; REPORT.md actually reports ~41% at 2%, ~67% at 10%.

Corroborated by **two independent detectors**: Tier T flagged all
of them as ungrounded (8 of metal_specificity's Tier T residue),
and the adversarial reviewer independently flagged the same numbers
(F003/F004/F005, `unbacked_quantitative`).

**Diagnosis:** D-018 makes `RESEARCH_PLAN.md` a legitimate grounding
source *for the Methods section* (it documents experimental intent).
The drafter is **over-applying D-018** — treating planned/predicted
values as valid grounding for Results and Introduction numerics,
where only REPORT.md (measured) and claim_inventory should ground.
This is a specific, nameable failure mode distinct from the generic
#40 Tier T residue. Single project (H3) in the dev+holdout set, but
it caused H3 to fail the v1-bar Tier T axis outright.

**Lever options:**
1. Tighten `holistic_draft.v1.md`: explicit rule that RESEARCH_PLAN
   values may ground Methods *only*; Results/Introduction numerics
   must ground in REPORT.md or claim_inventory. Worked anti-pattern
   from H3.
2. Per memory `feedback_prompt_discipline_needs_post_check` — prompt
   rules alone have failed before; back with a post-check. Tier T
   already catches these (it flagged all 8); the gap is the drafter
   emitting them, not the detector missing them. A drafter-time Tier
   T pre-check (bounded retry) is the mechanical backstop.

**Status:** Open, P1. Ship v1 with this documented as a known limit;
the pipeline detects the failure (Tier T + adversarial both fire),
so it is loud, not silent. H3 is the v1 holdout that fails on it.

### #35 — Emit template reframing_log.md in phase_extract — SHIPPED

**Status: SHIPPED 2026-05-20** (pending commit). Implemented as
module-level helper `write_reframing_log_stub(draft_dir)` in
`orchestrator.py`, called from `phase_extract` step 4 (idempotent;
never clobbers a drafter-populated log on resume). 4 unit tests in
`tests/unit/test_reframing_log_stub.py`. Eliminates the recurring
`missing_section` "reframing_log.md missing" P0 (observed on H2 and
H3; H1 happened not to raise it — adversarial sampling variance).

**Evidence:** D1 + D2 + D3 all flagged missing `reframing_log.md`
as a P0 finding (adversarial `missing_section`). Pipeline never
creates the file; drafter doesn't materialise it; compliance_gate
doesn't either.

**Lever:** ~10 LOC in `orchestrator.phase_extract` to emit a stub:

```markdown
# Reframing Log

_(no reframings recorded yet — drafter populates this when the
manuscript departs from REPORT.md framing)_
```

Zeroes out 1 P0 per project. File-existence check is satisfied;
content discipline is still aspirational.

**Status:** Open. Small commit; can land alongside v1-bar v2.

### #36 — Adversarial v3 schema validation strictness — RESOLVED (cross-skill, v0.7.0.5)

**Status: RESOLVED 2026-05-20** by beril-adversarial **v0.7.0.5**.
The Stage 7 holdout campaign surfaced the root cause deterministically:
`line_range` was wrongly required on section-scoped finding classes.
`validate_presentation_review.py`'s `SECTION_LEVEL_REQUIRED_FIELDS`
required both `section` and `line_range` whenever a `section` was
present — but `section_arc` / `throughline` / `missing_section` /
`central_objection` / `abstract_body_mismatch` are section- or
document-scoped and have no single line range. H2
(`adp1_triple_essentiality`) finding F020 (`section_arc`,
`section: "Results"`, no `line_range`) was correctly emitted by the
reviewer and wrongly rejected as non-correctable.

beril-adversarial v0.7.0.5 fixed it as specified in the cross-skill
bug report: new `PAPER_LINE_RANGE_REQUIRED_CLASSES = {register_drift,
claim_evidence, unbacked_quantitative, report_drift}` — `line_range`
required only for those four line-specific classes, optional for the
section/document-scoped ones; mirrors the existing `paragraph_quote`
carve-out. The v3 paper prompt's field-rules table was also corrected.
The companion `BlockingIOError` validator crash (H3) was fixed at root
cause via `_harden_stderr()` (`os.set_blocking(stderr, True)`).

**Verified 2026-05-20:** v0.7.0.5 deployed to `beril-extended` and
re-run against all three holdouts' original adversarial JSONs —
H1/H2/H3 all `PASS`, exit 0 (H2's previously-rejected F020 now
accepted). The bug predated v0.7.0 (`line_range` has been in
`SECTION_LEVEL_REQUIRED_FIELDS` since v0.6.0 paper-schema launch);
the holdout campaign just exercised section-scoped findings hard
enough to surface it.

**Residual (NOT #36, separate issue):** D3's first-cut adversarial
JSON had genuine unescaped inner quotes (`Expecting ',' delimiter`)
— a different failure mode (`feedback_llm_json_unfixable_in_parser`),
not schema strictness. v0.7.0.5 does not address it; it remains a
known stochastic limitation and is why D3's v1-bar verdict is
INCONCLUSIVE. Not v1-blocking.

### #37 — Adversarial reviewer non-determinism (sampling, not converging)

**Evidence (D3, 2026-05-19):** drafter changed 1 word in manuscript
(`"83,000"` → `"approximately 83,000"`). Pre-remediation adversarial
review found 4 P0s in classes `{missing_section, register_drift,
unbacked_quantitative}`. Post-remediation adversarial review found
6 P0s in classes `{missing_section, register_drift, citation_reality}`.
Zero overlapping finding IDs across runs; ~60% content overlap.

**Additional evidence (2026-05-20 forensic on all three dev projects,
keyed by `(class, fix_target)` not fuzzy title):**

| Project | iter_1 P0 | final P0 | Shared | iter_1-only | final-only | MS lines Δ |
|---|---|---|---|---|---|---|
| D1 conservation_vs_fitness | 5 | 6 | 4 | 0 | 1 | 12 |
| D2 amr_pangenome_atlas | 5 | 7 | 2 | 2 | 5 | 22 |
| D3 phb_granule_ecology | 4 | 6 | 3 | 1 | 3 | 2 |

Stable-core rate is 40-80% (not a tight 60-80% band; D2 sits at
40%). New-findings rate is 1-5 (not 1-3; D2 added 5 net-new in
final). D3 with only 2 lines of manuscript change still surfaced
3 entirely new P0 findings — the closest signal we have to pure
sampling variance.

Caveat: this is iter_1 vs post-1-remediation, NOT same-manuscript
double-review. Some "new" findings in D1/D2 may be legitimate
responses to drafter changes (12-22 lines edited). D3's tiny
manuscript change makes it the cleanest variance signal.

**Diagnosis:** The reviewer is sampling a different subset of the
underlying defect surface each run, not converging on a stable
defect set. Each run finds 1-5 NEW real defects in addition to
the stable core.

**Implications:** Gate's P0 count is a sampling estimator with
variance ±2-5 around the core (revised up from ±2-3 in light of
D2). Single-run counts shouldn't be treated as gospel.

**Lever options:**
1. **Multi-run review fusion** (run adversarial 2-3x, count P0 only
   if appears in ≥2 runs). Cost: +$2-3/project.
2. **Demote adversarial from gate count to advisory** (rely on Tier T
   + claim markers for deterministic signal; adversarial findings
   logged but not bar-driving).
3. **Raise gate threshold** to absorb variance (currently `≤8` in
   v1-bar v2 draft; final v2b value pending #41 + a possible same-
   manuscript double-review measurement).
4. **Same-manuscript double-review measurement** (run adversarial
   twice on a SINGLE unchanged manuscript per project; ~$0.30-
   0.50/project). Defensible empirical basis for whatever v2b
   threshold lands. Optional pre-cursor to (1) or (3).

v1-bar v2 picked (3) by default but the underlying issue remains.

**Status:** Document as known characteristic. Multi-run fusion is
v1.x work. Consider (4) before locking v2b's `max_adversarial_p0`.

---

## P2 — post-v1.0

### #48 — Tier 1 native check-table buildout (v1.1)

**Origin:** the paper_writer.sh retirement audit (2026-05-20, D-053).
SPEC §7.2 specifies a 9-row deterministic Tier-1 check table; the v1.0
Python `phase_review` Tier 1 implements only the numeric/claim legs
(`check_numeric_grounding`, `check_claim_markers`,
`check_throughline_numerics`). The rest of the table is unimplemented.

The v0.x tools that historically covered some of these rows
(`check_figures_manifest`, `check_tables_manifest`,
`check_caption_provenance`, `check_sentence_complexity`,
`check_abbreviation_discipline`, `check_echo_repetition`) were deleted
with paper_writer.sh — they were bound to the per-section + `*_manifest.tsv`
artifacts the v0.8 holistic write abandoned, so they could not be
wired in as-is. Their *function* is real and deferred here.

**Scope — build these as v0.8-native Tier-1 regex checks against
holistic-write artifacts (`manuscript.md`, `figures_inventory.md`,
`tables_inventory.md`, `claim_inventory.tsv`):**
- Figure/table callout resolution (every `(Fig. N)` / `(Table N)` in
  prose resolves to an inventory entry) — the cheap, high-value
  mechanical check; a paper with a dangling figure callout is a real
  defect.
- ICMJE IMRAD section presence; AI-disclosure block; data-availability
  statement presence (subsumes the review-timing concern from #47).
- Word-count-per-section vs story-outline budget.
- Language-quality advisories (sentence length, abbreviation
  discipline, echo repetition) — lower priority; advisory, never
  gating; only worth it per `feedback_prompt_discipline_needs_post_check`
  if the copy-edit phase proves insufficient.

**Status:** Open, v1.1. Deliberately deferred from v1.0 per Adam
2026-05-20 — v1.0 ships the deterministic numeric/claim legs + the
canonical adversarial reviewer (which covers overclaim/scope/register
judgmentally). Old tool source is in git history (pre-D-053) for
reference if any logic is worth porting.

### #47 — Data Availability sequencing + orphaned generator machinery — SUPERSEDED

**Status: SUPERSEDED by #48, 2026-05-20.** The orphaned-machinery half
is resolved — `check_data_availability.py` + the pre-v0.8 generator
were deleted with paper_writer.sh (D-053). The residual review-timing
concern (the adversarial reviewer flags "missing Data Availability"
at `phase_review` because the section is added later at
`phase_compliance_gate`) folds into #48's "data-availability statement
presence" Tier-1 row. Kept here for evidence trail; see #48 for the
forward-looking work.

**Evidence (Stage 7 holdouts, 2026-05-20):** Every holdout's
adversarial review flagged "no Data Availability / Code Availability
statement" as a P0 (`missing_section`). It is **not a real gap in
the shipped product** — it is a review-timing artifact:

- `holistic_draft.v1.md` instructs the drafter to write only the

**Evidence (Stage 7 holdouts, 2026-05-20):** Every holdout's
adversarial review flagged "no Data Availability / Code Availability
statement" as a P0 (`missing_section`). It is **not a real gap in
the shipped product** — it is a review-timing artifact:

- `holistic_draft.v1.md` instructs the drafter to write only the
  IMRAD body ("Abstract, Introduction, Methods, Results,
  Discussion"). No back-matter.
- Data Availability + AI Disclosure are added later, at
  `phase_compliance_gate` (which runs AFTER p0_review), via the
  `compliance_fix.v1.md` autofix.
- The adversarial reviewer runs at `phase_review` — before
  compliance_gate — so it sees a body with no Data Availability and
  flags a P0 that the final assembled manuscript will not have.

The p0_gate already demotes this finding ("pre-compliance Data
Availability" demotion), so it does not inflate the gated P0 count
— but it does clutter the adversarial review and is a genuine
sequencing smell.

**Orphaned machinery:** `check_data_availability.py` +
`07_data_availability.md` implement a richer Data Availability
generator with K-BERDL collection cross-referencing. Its docstring
references `phase_data_avail` and "the shell orchestrator" — a
**pre-v0.8 architecture that no longer exists**. The current v0.8
Python orchestrator only *checks* for Data Availability
(compliance_gate ~line 2570) and autofixes via LLM; it does not
appear to invoke the rich generator. Likely orphaned.

**Lever options (decide together):**
1. Deterministic back-matter emitter: append TBD-placeholder Data
   Availability + AI Disclosure sections after `phase_drafting`,
   before `phase_review` (mirrors D-014's TBD-placeholder pattern
   for author/funding/ethics, and #35's deterministic-stub shape).
   Makes the manuscript complete-shaped at review time; removes the
   spurious adversarial finding.
2. Revive `check_data_availability.py` + wire a real
   `phase_data_avail` into the v0.8 orchestrator.
3. Accept as a documented review-timing artifact (zero code).

**Status:** Open, P2. Not v1-blocking — the final assembled
manuscript *does* get a Data Availability section via
compliance_gate. This is a sequencing-cleanliness + dead-code
question, best resolved as one focused piece of work rather than a
rushed fix during the v1 ship push.

### #23 — Track `_audit_discrepancies_interactive` cost

**Evidence:** Pre-existing audit. `_audit_discrepancies_interactive`
uses raw `subprocess`, bypassing `_run_claude_p_with_cost`. The
~$1-2 of LLM spend per call is not counted in
`state.cost_so_far_usd`.

**Lever:** Migrate to `_run_claude_p_with_cost`. ~20 LOC.

**Status:** Pre-existing; low-priority bookkeeping.

### #24 — draft.py PipelineHalted handler is dead code

**Evidence:** `run_pipeline` catches `PipelineHalted` internally
(`orchestrator.py:483`), so it never propagates to `draft.py`'s
outer `except`. The pretty handoff-summary printer there + the
`print(draft_dir)` are dead on the throughline_pick pause path.

**Lever:** Either (a) re-raise from `run_pipeline`'s `PipelineHalted`
catch and let `draft.py` handle, or (b) have `draft.py` inspect
`state.json` after `asyncio.run` returns instead of relying on
exception propagation.

**Status:** Non-blocking because state.json + .handoff.json are
still written correctly. Surfaces operator-experience issues
(no resume command printed).

### #31 — `check_report_numerics.py` validator

**Evidence:** BERIL's `synthesize` skill creates `REPORT.md` from
notebooks with **zero anti-fabrication discipline** (grep of
SKILL.md: `hallucinat | fabricat | inviolable | verify | verbatim
| grounding` → zero matches). REPORT.md isn't ground truth; the
same LLM that writes the manuscript writes REPORT.

**Risk:** Our Tier T grounds against REPORT.md. If REPORT itself
contains a fabricated number, the manuscript's matching number
grounds — a false-negative we can't see.

**Lever:** New `check_report_numerics.py` that verifies REPORT.md
numerics against notebook CSV outputs. Same shape as Tier T; runs
during `phase_extract` or as a BERIL synthesize post-checker.
~200 LOC.

**Status:** Architecturally significant but out of v1.0 MVP scope.

### #32 — Programmatic citation verifier (Crossref + PubMed)

**Evidence:** BERIL's strongest anti-hallucination defense. Every
9-field bibliography block emitted by the reviewer is verified
against Crossref (DOI) and NCBI PubMed (PMID); failed verifications
get a visible `⚠️ CITATION FABRICATED` marker stamped into the
review file.

**Our gap:** `citation_pool.json` verifies citations by resolution
at creation time, but we don't re-verify on each use, and we don't
check post-draft citations against DOI/PMID registries. D1's
F001/F002 (citation key mismatches: `[van2013]` vs pool's
`[Opijnen2013]`; `[Hutchison2016]` vs pool's `[rd2016]`) would have
been caught loud-and-early by Crossref lookup at draft time.

**Lever:** Add Crossref + PubMed verification step. Plumb as Tier 1
check in `phase_review` OR end-of-`phase_drafting` post-checker.
Requires network access; rate-limiting; offline-mode fallback.
~200 LOC + tests.

**Status:** v1.1 candidate.

### #38 — D-NNN + scientific-notation in throughline-numerics allowlist

**Evidence:** `check_throughline_numerics` flags `D-040`, `D-011`
(discrepancy register references) and `1.14e-21` (scientific-notation
p-values) as ungrounded. Noisy but advisory; doesn't halt.

**Lever:** Allowlist `\bD-\d+\b` for discrepancy refs; recognize
scientific-notation tokens when preceded by `p = / p < / rho = `.
~10 LOC patch.

**Status:** Cosmetic improvement; reduces noise in audit JSON.
**Note:** This entry is scoped to `check_throughline_numerics`,
NOT `check_numeric_grounding.py`. The scientific-notation problem
in `check_numeric_grounding.py` (the dominant dev-set defect) is
filed separately as **#41**, currently P0. Don't conflate the two
file scopes.

### #44 — D2 `77.8%`/`22.2%` mechanism classification — Tier T residual post-#41

**Evidence (post-#41 deterministic re-run, 2026-05-20):** D2's two
remaining ungrounded findings after #41 closed the sci-notation
false-positive class:

- Manuscript (Methods): "This approach classified 77.8% of AMR
  clusters into defined mechanism categories, with 22.2% remaining
  as other or unclassified [C-085]."
- Manuscript (Results): "The mechanism classification covers 77.8%
  of AMR clusters; 22.2% remain unclassified."

`[C-085]` is the cited marker; the value `77.8` does NOT appear in
`claim_inventory.tsv` or `REPORT.md` (verified by re-run trace).

**Diagnosis (uninvestigated, hypothesis):** Three possibilities:
1. **Drafter fabrication / rounding.** Drafter computed 77.8% from
   some other source value (e.g., a count ratio in REPORT.md) and
   the literal `77.8%` was never emitted by the source pipeline.
2. **Marker resolution gap.** `[C-085]` resolves to an inventory
   row, but the inventory row's `claim_text` may be a paraphrase
   that omits the literal percentage (we record what claim is
   made, not its numerical surface form).
3. **Source-side extraction miss.** The inventory or REPORT.md
   contains an equivalent representation (e.g., `0.778`,
   `1,234 / 1,587 (77.8%)`) that `build_normalized_set` didn't
   collect — though Patch 2's generic extractor was supposed to
   close exactly this class.

**Lever:** Open `claim_inventory.tsv` for D2's C-085 and read the
linked notebook. If the source has `77.8%` in any form, file as
extractor miss. If the source has only the underlying ratio,
file as drafter discipline (#40 residual).

**Status:** Open, P2. Doesn't block v1 ship. Investigate when
re-running #41 evaluation against holdouts surfaces additional
data points or before v2b threshold-setting.

### #43 — `review_cost_usd` not populated in remediation_cycles

**Evidence (Stage 7 dev runs, 2026-05-20):** All three dev projects'
`state.remediation_cycles[0]` carry both `drafter_cost_usd` and
`review_cost_usd` schema slots, but `review_cost_usd is None` in
all three. The post-remediation adversarial re-review's cost is
lumped unattributed into `cost_so_far_usd`.

| Project | cost_so_far | drafter_remediation | review_remediation |
|---|---|---|---|
| D1 | $6.15 | $0.89 | None (unpopulated) |
| D2 | $6.92 | $1.90 | None |
| D3 | $9.35 | $0.82 | None |

**Impact:** v1-bar v2's "first-cut cost" subtraction
(`cost_so_far - sum(drafter_cost_usd)`) is conservatively HIGH —
overstates first-cut cost by the unattributed re-review cost
(~$0.30-0.80 per project based on adversarial JSON size). Does
not change any current verdict (all under $10) but the bookkeeping
is dishonest.

**Lever:** Producer-side fix in
`src/beril_paper_writer/skill/continue_run.py` (or wherever the
remediation phase records cycle telemetry) — populate
`review_cost_usd` with the re-review LLM spend at cycle close.
Defensive consumer-side: `collect_metrics.py` should subtract
`(c.get("review_cost_usd") or 0.0)` so it picks up the value
when producer is fixed (one-line addition; lands with v1-bar v2a).

**Status:** Small producer bug + one-line collector defensive fix.
Non-blocking.

---

## Closed / superseded

### #30 — `[unverified:]` token vocabulary for drafter

**Closed 2026-05-20.** Original rationale (cite): BERIL's "flag
rather than fabricate" doctrine. Reason for closure:

1. **Architectural mismatch.** BERIL has no `claim_inventory.tsv`
   and no `REPORT.md`-grounded verifier. Their `[unverified:]`
   token is a graceful-degradation tool for a system that cannot
   mechanically verify. We CAN verify; the verifier is just buggy
   (see #41).
2. **Perverse incentive.** Under Opus's compliance-with-instructions
   tendency, "if uncertain, emit `[unverified:]`" trains the
   drafter to escape-hatch instead of grounding. The token becomes
   a substitute for the work we want done.
3. **Dev-set evidence inverted the premise.** The original case
   for #30 was D1/D2 ungrounded numerics under Opus. Forensic
   reading (see #41) showed those are extractor false positives
   on grounded numbers, not drafter fabrications. The drafter is
   doing the work; the verifier isn't seeing it.
4. **The narrow use case has a better mechanism.** External-
   citation numbers (e.g. D1's `80% Tettelin`) are properly
   handled by a Tier T allowlist rule keyed on citation-bracket
   adjacency, not by a drafter-side escape-hatch token.

Reopen only if dev-set re-evaluation post-#41 surfaces a residual
fabrication class for which mechanical grounding is genuinely
impossible.

---

## How to use this file

- **At the start of a new conversation:** read this file to know what's
  open and what's been deferred. Sets context faster than scrolling
  prior conversation transcripts.
- **When finding a new issue mid-task:** append an entry here rather
  than only logging it in the conversation's task list. The
  conversation TaskList is ephemeral; this file persists.
- **When closing an item:** move its entry to "Closed / superseded"
  with a one-line note pointing at the commit / explanation.
- **Do not reorganise the priority tiers without explicit decision** —
  P0/P1/P2 reflect Adam's panel-of-one judgments at the time of
  filing.
