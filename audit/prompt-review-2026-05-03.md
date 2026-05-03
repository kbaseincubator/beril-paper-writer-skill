# Adversarial Review: beril-paper-writer-skill Prompts (v1.0 Candidate)
**Date:** 2026-05-03  
**Scope:** 12 system prompts + SPEC.md + LAYOUT.md  
**Reviewer:** Adversarial audit for LLM prompt engineering quality  
**Strictness:** Pre-v1.0; all findings reported as-is  

---

## Executive Summary

This audit identified **26 findings across 4 severity tiers** in the beril-paper-writer-skill prompt suite. The system demonstrates strong architectural discipline (throughline-driven, grounding-first, hard caps on iteration) but exhibits three recurrent anti-patterns:

1. **Underspecified escape hatches** — prompts halt on missing files but don't specify the downstream orchestrator's recovery path, leaving ambiguity about what "halt" communicates to the user.
2. **Silent glyph/strength-vector drift** — plan.v1 and revise_throughline.v1 require cross-walk discipline (weakness inventory ↔ strength glyph consistency) but lack deterministic post-checks; manual inspection is the only gate.
3. **Inconsistent boilerplate-exclusion criteria** — figure_caption.v1 strips notebook ALL-CAPS keywords (Purpose:, Approach:, etc.) but other prompts don't apply the same filter to similar prose contamination (methods_provenance drift, discovery-notebook prose in results).

**Impact assessment:** Critical findings (3) involve overclaim risk and ambiguity in citation discipline that could propagate to the manuscript. Important findings (9) affect prompt reliability and consistency. Suggested findings (14) cover boilerplate, efficiency, and documentation. No failures detected that make the system non-functional, but prompt-level discipline is load-bearing.

---

## Critical Findings

### CRx.1 — citation_pool.v1 and methods.v1 create orphan citation risk
**Location:** citation_pool.v1 §"Verification pass" + methods.v1 §"Hard constraints"  
**Issue:**  
citation_pool.v1 allows entries with `identifier: null` in the **verification pass** (lines 79-85 explicitly state "no identifier guessing"). However, methods.v1 has no protocol for "what if a method was discovered during execution that wasn't in the plan and has no citation?" The escape hatch is: "flag [METHOD UNCLEAR: cell N]" (line 24).

But discussion.v1 forbids authority-without-specificity citations (§"Citation discipline," line 26: "each [bib_key] supports a SPECIFIC claim or is dropped"). If Results introduces a finding without a citation, Discussion cannot cite it unless the claim is already in the pool.

**Consequence:** If Methods adds a method-claim that is grounded in execution but never made it to the pool, Discussion cannot honestly reference it. Result: either Methods overwrites the claim as unfounded (via REPAIR_MODE), or Discussion omits engagement with a real finding. Silent overclaim risk.

**Fix:** Add a protocol to citation_pool.v1 §"Escape hatches" (after line 80):
```
- **Method executed but not pre-planned and not in pool** → add a fast-track entry
  (9 fields, quick=true) OR mark the notebook cell in methods_provenance.md so
  reframer.v1 catches it as plan-execution-discrepancy. Document the protocol
  in methods.v1 §"Tool use" so the orchestrator knows to expect provenance-log
  entries for un-pooled methods.
```

---

### CRx.2 — intro.v1 lacks direction on how "overclaim" is detected by downstream validator
**Location:** intro.v1 §"Escape hatches when expected files are absent" (lines 187-201)  
**Issue:**  
intro.v1 §line 195 halts if DISCUSSION_PATH is missing: `"Error: 03_discussion.md must be drafted before Introduction (per SPEC §6.1 drafting order). Aborting."` This is correct. But the prompt also defines the anti-overclaim discipline (§"Discipline pass → Only-what-paper-delivers"; lines 233-258). This discipline relies on the self-review pass (§"Self-review pass"; lines 348-374), which is manual.

The problem: abstract.v1 is invoked **after** Introduction is complete. abstract.v1 has a clause (§"Protocol"; line 2 of output protocol): "number claim → grep Results exact match." If Abstract sees a finding number in Introduction's preview, it will grep Results; if Results moved the number for readability, the grep fails silently, and abstract.v1 produces a null claim.

**Consequence:** Introduction overclaim can propagate to Abstract if the shared Evidence (throughline sub-claim numbers) changes between drafting Introduction and drafting Abstract.

**Fix:** Add to intro.v1 §"Self-review pass" (after item 8, before line 374):
```
9. **Approach-in-brief headline number matches Results exactly.** Grep the 
   Results section for the finding preview's specific number (e.g., "95 dark 
   genes"). If the number appears in Results with a different context 
   (e.g., "95 genes" vs "95 dark genes with strong phenotypes"), note the 
   context drift in the closing message for the orchestrator to review.
```

And cross-reference in abstract.v1 §"Discipline pass" (line 3 of output protocol):
```
- **Introduction-preview number must match Results exactly** for grep to 
  succeed downstream. If Introduction's preview number is in Results but with 
  different qualification (context, scope, units), the orchestrator may need 
  a REPAIR_MODE intervention to synchronize.
```

---

### CRx.3 — reframer.v1 Check 1 (REPORT-vs-Results numerical drift) underdescribes "notebook secondary" protocol
**Location:** reframer.v1 §"Check 1: REPORT-vs-Results numerical drift" (lines 121-144)  
**Issue:**  
Lines 141-144 state:
```
- **Number doesn't appear in REPORT** → check notebook outputs via Grep on 
  `<NOTEBOOKS_DIR>/*.ipynb`. If found in a notebook output cell, OK (it's 
  grounded, just not in REPORT). If not found, this is fabrication — flag as 
  `escalated`.
```

But the prompt doesn't specify:
- How to handle a number that appears in a notebook output CELL but was manually rounded or renormalized in the notebook prose? (e.g., output shows `0.0523`, Results quotes `5.2%`, REPORT doesn't mention it).
- What "output cell" means — does it include markdown cells that reference prior code, or code-cell outputs only?
- Whether grep on `*.ipynb` is sufficient (it will match cell contents, but ipynb is JSON; text grep may mis-match on whitespace/escaping).

**Consequence:** Drift detection is ambiguous. Two equally-skilled auditors might reach different conclusions about whether a novel number is "grounded in notebook" or "fabricated."

**Fix:** Replace lines 141-144 with:
```
- **Number doesn't appear in REPORT** → check notebook outputs via Grep on 
  `<NOTEBOOKS_DIR>/*.ipynb`. Match is valid if:
  (a) the number appears verbatim in a code cell output (line starting 
      with "Out[N]:" or a print statement in the cell source), OR
  (b) the number appears in a Results-facing markdown cell prose within 
      the notebook (e.g., "The model achieved 95% accuracy" in a markdown 
      cell that describes an execution result from a prior code cell).
  Precision criterion: if REPORT says N=92 and Results/notebook say N=95, 
  the difference is 3%; if >10% difference, flag as reconciliation-needed 
  even if notebook-grounded (may indicate aggregation/filtering changes 
  post-REPORT). If not found, mark as escalated (potential fabrication).
```

---

## Important Findings

### IMx.1 — plan.v1 self-review item 5 (glyph cross-walk) is self-referential
**Location:** plan.v1 §"Self-review pass" (item 5, line 165)  
**Issue:**  
Line 162-166:
```
5. **For every weakness-inventory bullet, verify the corresponding 
   evidence-map row's strength glyph reflects the weakness.** (If the 
   weakness names "X is partial," the row must be `⚠ partial`, not 
   `✓ direct`.)
```

This is the correct discipline. But the prompt's own output-protocol (line 2) says:
```
1. **Build Evidence map** — copy from candidate, then **walk each sub-claim**
   and verify strength glyphs are operationalized per definitions (✓ direct 
   requires quantitative establishment; ⚠ partial is explicit gesture; etc.).
```

The **tension:** Line 193 then says "the glyph must be `⚠ partial`" — but what if the weakness is "no n provided"? Should that be `⚠ partial` or `✗ contradicts` or unmarked? The prompt doesn't clarify. A glyph is contradicts-specific: used when there's affirmative counter-evidence, not absence-of-evidence.

**Consequence:** An auditor might downgrade a `✓ direct` to `⚠ partial` when the weakness is "sample size not reported" (absence), when the correct glyph is still `✓ direct` (the evidence is direct, just incompletely specified). Post-processor `tools/check_throughline_glyphs.py` catches inconsistency but doesn't prescribe the right fix.

**Fix:** Clarify in plan.v1 §"Self-review pass" item 5 (after line 165):
```
   Example: weakness "effect size not computed" → evidence map row stays 
   `✓ direct` if the finding itself (e.g., "gene X is associated with Y") 
   is directly established. Add the weakness, don't downgrade the glyph 
   just because a secondary metric (effect size) wasn't reported.
   
   Downgrade to `⚠ partial` only if the primary claim itself is partially 
   established (e.g., "suggested but not statistically significant").
```

---

### IMx.2 — methods.v1 §"Forbidden" (line 20) conflates "fabrication" with "inference"
**Location:** methods.v1 §"Critical rule" and §"Forbidden" (lines 18-21)  
**Issue:**  
Line 18: "No fabricated methods (forbidden claim any method not pointed to in plan or executed code)."

But what about methods that are **implicit** in the code? For example:
- A notebook uses `scipy.optimize.minimize` without stating "optimization was performed."
- The AST extracts `minimize` from the code, methods_provenance.md lists it.
- But should Methods state "we performed optimization" (inference from code) or "we invoked scipy.optimize.minimize" (literal)?

The prompt's guidance is: lines 24-28 (the three paths) suggest Methods can claim a method if it's in provenance OR in RESEARCH_PLAN. But the threshold between "path 3 (implicit but not explicit)" and "fabrication" is unclear.

**Consequence:** A Methods writer might flag every inferential step as [METHOD UNCLEAR] to be safe, bloating the Methods section with placeholders. Or they might infer freely, overclaiming what the code demonstrates.

**Fix:** Add to methods.v1 §"The three paths" (after line 37):
```
   (Clarification) Inference is permitted ONLY when the inference is 
   a direct consequence of the literal code: e.g., if the code calls 
   scipy.stats.ttest_ind(), Methods states "we performed an independent 
   t-test" without requiring a prose comment in the code. But if the 
   inference is non-obvious (e.g., "we corrected for multiple testing" 
   inferred from the presence of statsmodels.multitest import), flag 
   [METHOD IMPLICIT: inferred from notebook imports; confirm in 
   RESEARCH_PLAN]. The orchestrator will handle via gap-fill or REPAIR_MODE.
```

---

### IMx.3 — results.v1 §"Findings Summary" length constraint (3-6 sentences) lacks tie-breaker for 8+ findings
**Location:** results.v1 §"Output structure" (line 38)  
**Issue:**  
Line 35-36 state:
```
Findings Summary (hard cap 3-6 sentences, 3 strongest findings if 8+ 
exist)
```

But what is "strongest"? The prompt doesn't define a ranking function. Is strongest = (a) evidence-map strength glyph (✓ before ⚠)? (b) practical significance to the field? (c) support within the paper's scope? The prompt should clarify, else two runs may produce different summaries depending on the LLM's implicit ranking.

**Consequence:** Non-deterministic Findings Summary. If the rewriter is invoked and the LLM picks different "strongest" findings on the second pass, it introduces **story drift** between draft 1 and draft 2 of the manuscript.

**Fix:** Add after line 36:
```
   Tie-breaking rule for "strongest": rank findings by evidence-map order 
   (top sub-claim first), then by specificity (if two findings are about 
   the same sub-claim, prefer the one with the narrowest scope / highest 
   evidence quality). Include a methodology line in the Findings Summary 
   heading if more than 5 exist: "Summary of top 3 of N findings:".
```

---

### IMx.4 — discussion.v1 "Compound citations forbidden" (line 27) is not validated downstream
**Location:** discussion.v1 §"Hard constraints" (line 27) and §"Citation discipline" (line 37)  
**Issue:**  
Line 27: "compound citations forbidden [Key1][Key2] never [Key1,Key2]"
Line 37-39: "max 3 citations per claim unless an explicit multi-citation review is needed. Pool entries marked `is_review_article: true` are useful…"

The validator (M10, per SPEC §7) checks for `[orphan_key]` (missing from references.md) but doesn't check for `[Key1, Key2]` (comma-inside-brackets). The fallback_reviewer.v1 has no mechanism to detect this either. It's a self-review-only check.

**Consequence:** A Discussion prompt might produce `[Key1, Key2]` (intent: cite two papers), but the finalize step (citation_pool.py renumbering) will treat the entire bracket as a single key and fail to renumber. Result: orphan `[Key1, Key2]` in the assembled manuscript, caught by journal reviewer, not by the system.

**Fix:** Add to the discussion.v1 self-review pass (after item 8):
```
9. **Compound citations forbidden.** Grep your output for `\[[A-Z][a-z]+\d\d\d\d,\s` 
   (regex for [Key1, Key2]). If found, rewrite as [Key1][Key2] 
   (adjacent brackets, no comma). This is not validated downstream; 
   catching it here is essential.
```

And note in the closing-message template:
```
… reframing-log entries appended: Q. Drafted in {paper|report} mode 
at {STRONG|THIN|EXPLORATORY} tier. 
Self-review note: compound-citation format checked manually; finalize 
step will auto-renumber [Key1][Key2] format to [N][M].
```

---

### IMx.5 — abstract.v1 "body-derivable claims" protocol (lines 58-61) doesn't address Conclusions overclaim
**Location:** abstract.v1 §"Three protocols" (lines 58-61)  
**Issue:**  
The abstract's Conclusions section (lines 4-5 in output) must match Discussion's Summary and be ≤3 sentences. But abstract.v1's protocol (line 60) says "Conclusions claim → matches Discussion's Summary." The Discussion's Summary is a human-read prose paragraph; the Abstract Conclusions is a structured format (max 3 sentences, "so what" statement required).

If Discussion's Summary is 4 sentences and the Abstract truncates to 3, is that a protocol violation? The prompt doesn't specify. A stricter reading: the Abstract writer must extract the 3 strongest conclusions from Discussion's Summary and re-phrase them, but also ensure the 3 match Discussion (not introduce new conclusions).

**Consequence:** Abstract Conclusions can accidentally add a 4th conclusion that Discussion didn't make, or drop an important one Discussion did, without triggering a self-review flag.

**Fix:** Expand abstract.v1 §"Three protocols" → after line 60:
```
   Matching rule: the Abstract's Conclusions must be a **subset and 
   simplification** of Discussion's Summary, not a reordering. If 
   Discussion's Summary names 4 conclusions, the Abstract picks the 
   3 most material and condenses them to ≤3 sentences. Verify: every 
   sentence in Abstract's Conclusions must have a textual (not 
   inferential) counterpart in Discussion's Summary or Discussion's 
   opening sentence.
```

---

### IMx.6 — reframer.v1 Check 4 (throughline-evidence-map-vs-Results) lacks definition of "sub-claim" sectioning
**Location:** reframer.v1 §"Check 4" (lines 170-181)  
**Issue:**  
Lines 170-173 state:
```
Walk the throughline's evidence map. For each sub-claim:
- **Sub-claim has a Results subsection** → no entry.
- **Sub-claim has no Results subsection** but evidence-map strength 
  was `✓ direct` or `⚠ partial` → drift (the throughline promised this 
  sub-claim; Results didn't deliver).
```

But what counts as a "Results subsection"? The prompt doesn't specify whether a sub-claim's Results evidence must be in a **dedicated** subsection (### Sub-claim Title) or can be embedded in a paragraph within an existing subsection.

**Consequence:** Two equally-skilled auditors might disagree on whether "Results addresses the sub-claim" if the sub-claim is discussed inline rather than as a structured heading.

**Fix:** Add to reframer.v1 §"Check 4" (after line 170):
```
   A "Results subsection" is defined as a paragraph or ### heading in 
   02_results.md whose title or opening sentence names or directly paraphrases 
   the sub-claim. Inline discussion of the sub-claim within another subsection 
   does NOT count — the evidence must be locatable by a topic-based grep for 
   the sub-claim's key phrase. Use grep: `RESULTS_MD text | grep -i 'key_phrase_from_subclaim'` 
   to verify addressability.
```

---

### IMx.7 — fallback_reviewer.v1 "Plausibility check" (lines 182-184) doesn't define "suspicious"
**Location:** fallback_reviewer.v1 §"Citation rigor" (lines 182-184)  
**Issue:**  
Lines 182-184: "if a citation reads as suspicious (impossible journal name, generic author surname, future year), flag for user verification."

But "generic author surname" (Smith, Johnson) is common in biological literature. "Future year" is clear (>2026 is fabrication). "Impossible journal name" is ambiguous — some journal names are ad-hoc or non-English.

**Consequence:** A fallback reviewer might miss real fabrications (plausible but false journal names like "Journal of Applied Microbiology" which exists but was faked for the citation) and flag false positives (real journals with uncommon names).

**Fix:** Replace lines 182-184:
```
   - **Plausibility check** — flag citations with objective red flags only:
     (1) publication year > {current_year} (future publication),
     (2) author list has <2 names or includes obvious placeholders like 
         "Anonymous" or "XXX",
     (3) journal name is a common typo of a real journal (e.g., 
         "Naturre" for "Nature"; flag for user verification).
     Generic author names (Smith, Johnson) and non-English journal names 
     are NOT flagged. This check is conservative; if unsure, default to 
     "no flag" — the user or the full beril-adversarial reviewer can do 
     deeper verification.
```

---

### IMx.8 — revise_throughline.v1 "Revision propagates" model is undefined for contradicts (✗) glyphs
**Location:** revise_throughline.v1 §"Discipline pass" (lines 156-177)  
**Issue:**  
Lines 156-177 describe glyph cross-walk, but all examples use `✓ direct` and `⚠ partial`. The prompt doesn't specify what happens if:
- User revision: "downgrade the contradicting evidence row because we realized the contradiction is methodological, not substantive."
- Current glyph: `✗ contradicts`
- Revised glyph: `◇ orthogonal` or remove from evidence map?

The prompt's inviolable rule (line 274: "Glyph cross-walk is non-negotiable") doesn't clarify whether `✗` rows can be downgraded.

**Consequence:** A reviser might hesitate to apply a user's request to narrow a contradiction, because the prompt doesn't explicitly permit downgrading `✗`.

**Fix:** Add to revise_throughline.v1 §"Discipline pass" (after line 165):
```
   Strength glyph demotion rules:
   - `✓ direct` → `⚠ partial`: permitted when user revision adds a caveat 
     (e.g., "marginal effect size" or "small sample").
   - `⚠ partial` → `◇ orthogonal` or drop: permitted if user revision 
     narrows scope so the sub-claim is no longer relevant to the 
     throughline.
   - `✗ contradicts` → `◇ orthogonal` or drop: permitted ONLY if the 
     user revision specifies why the contradiction is no longer valid 
     (e.g., "that study used different organisms, so our finding doesn't 
     contradict it — it's orthogonal"). Document the demotion in the 
     Source column with a brief "(revised {today}: downgraded from ✗ 
     because ...)" note.
   Promotion (✓ to ⚠ or ⚠ to ✗) is not permitted.
```

---

### IMx.9 — figure_caption.v1 boilerplate-exclusion list (lines 184-194) is ad-hoc, not systematic
**Location:** figure_caption.v1 §"Notebook-organization boilerplate exclusion" (lines 178-200)  
**Issue:**  
Lines 184-194 enumerate ALL-CAPS keywords: `Purpose:`, `Approach:`, `Strategy:`, etc. But this list is non-exhaustive. A notebook might use:
- `Overview:` (not listed, but similar)
- `Background context:` (not listed)
- `Developer notes:` (not listed)
- References to project internal naming like `V1_ANALYSIS`, `FINAL_PARAMS`, `LEGACY` (none listed)

The prompt says "strip notebook-organization context" but doesn't give a systematic rule (e.g., "remove any capitalized section header that's not a biological term").

**Consequence:** Some boilerplate slips through. A caption might reference "FINAL_PARAMS: n=10" when n=10 is actual, not metadata.

**Fix:** Replace lines 184-194 with:
```
   ALL-CAPS-COLON patterns (objective, remove all): Purpose:, Approach:, 
   Strategy:, Sections:, Steps:, Method:, Inputs:, Outputs:, Notes:, 
   Goal:, Pipeline:, Workflow:, Implementation:, Overview:, Background:, 
   Status:, Version:, Caveat:, Todo:, Fix:, Bug:, XXX:, FIXME:, Hack:.
   
   ALL-CAPS identifiers without colon (subjective; remove if notebook-specific):
   - Project-internal codes: NB01–NB99, V1/V2/LEGACY, FINAL, ANALYSIS, REVIEW
   - Workspace/path refs: WORKING, SCRATCH, DATA, MODELS, OUTPUTS
   Use context: if a caption says "per NB04_analysis.ipynb, N=100," the 
   "NB04" reference is development metadata; drop it to "N=100." If it 
   says "ANALYSIS tier," only drop if "ANALYSIS" is a development/QA label 
   (check REPORT for use); if it's a scientific term, keep it.
```

---

## Suggested Findings

### SGx.1 — plan.v1 §"Worked example" (lines 61-88) uses single-organism example; THIN/EXPLORATORY lack worked examples
**Location:** plan.v1 §"Worked example" (lines 61-94)  
**Issue:**  
The example is labeled "STRONG tier, dark gene fitness project" (line 65). But plan.v1 output format (lines 100-140) states that THIN and EXPLORATORY tier candidates have distinct templates: THIN has "narrowed-claim candidate" (line 138) and EXPLORATORY has no narrowed-claim.

The worked example doesn't show how a THIN-tier evidence map looks (fewer direct glyphs?), or how EXPLORATORY's "hypothesis-generating" language surfaces in the strength glyphs.

**Consequence:** A THIN-tier or EXPLORATORY-tier user might draft their candidate and self-review unsure whether the glyph distribution is correct for their tier.

**Fix:** Add two additional worked examples after line 88:
```
**THIN-tier example (narrowed scope):**
[Worked example for narrowed claim: "dark genes at FDR q<0.05 only"]

**EXPLORATORY-tier example (hypothesis-generating):**
[Worked example for preliminary observation: "dark genes show phenotype 
without statistical confirmation"]
```

---

### SGx.2 — methods.v1 §"Tier-aware framing" (lines 47-50) is documented but not enforced in self-review
**Location:** methods.v1 §"Tier-aware framing" (lines 47-50) vs §"Self-review pass" (lines 147-164)  
**Issue:**  
Tier-aware language is defined:
```
| STRONG | declarative | THIN | explicit Act-II deferral | EXPLORATORY | cautious/descriptive |
```

But the self-review pass (item 8, line 161) says:
```
8. **Tier-conformant language.** STRONG declarative | THIN scoped | 
   EXPLORATORY cautious-descriptive.
```

There's no worked example of what "explicit Act-II deferral" looks like in Methods. A THIN-tier Methods writer doesn't know whether to write "We performed t-tests (sample size n=5 per group; power analysis deferred to future analysis)" or "We performed t-tests; a more extensive sample is needed for robust power."

**Consequence:** Ambiguous THIN-tier Methods. The writer may default to STRONG language when uncertainty calls for explicit deferral language.

**Fix:** Add to methods.v1 §"Tier-aware framing" (after line 50):
```
   THIN "explicit Act-II deferral" examples:
   - STRONG: "We performed independent t-tests on fitness scores."
   - THIN: "We performed independent t-tests; a larger cohort would 
     enable multiple-testing correction (deferred)."
   - STRONG: "We validated model performance via cross-validation."
   - THIN: "We assessed model performance on the available data; 
     validation on external cohorts is a next step."
```

---

### SGx.3 — citation_pool.v1 "Depth modes" (lines 65-72) are suggested but not normalized
**Location:** citation_pool.v1 §"Depth modes" (lines 65-72)  
**Issue:**  
Three modes are defined: quick (~5 calls), standard (~25-40 calls), deep (~60-100 calls). But the prompt doesn't specify how to choose. Is the choice made by the user in the continue prompt? By the orchestrator based on tier? By the citation_pool.v1 agent?

Also, the WebSearch budget isn't tied to the overall manuscript budget (§5 in SPEC: "bounded cost/latency, $5-$15 per full run"). If citation_pool runs at "deep" mode (~100 WebSearch calls), at $0.001 per search, that's $0.10 — insignificant. But if the orchestrator later runs it multiple times during gap-fill iterations, costs add up.

**Consequence:** Ambiguous mode selection. Different runs might use different citation depths without explicit user direction.

**Fix:** Add to citation_pool.v1 §"Depth modes" (after line 72):
```
   Mode selection: the orchestrator passes `CITATION_DEPTH=quick|standard|deep` 
   based on TIER (default: quick for EXPLORATORY, standard for THIN, standard 
   for STRONG; user can override via `--citation-depth=deep` flag). Track 
   WebSearch calls in the closing message; if total run cost approaches $5 
   budget, the orchestrator throttles subsequent gap-fill citation rounds.
```

---

### SGx.4 — results.v1 "Subsection ordering" (line 39) relies on evidence-map order but allows override
**Location:** results.v1 §"Three protocols" (lines 53-73)  
**Issue:**  
Line 55-56: "subsection order = evidence-map order unless throughline specifies otherwise."

But the prompt doesn't define how a throughline "specifies otherwise." Is there a `preferred_order` field in the throughline JSON schema? Or does the writer read the throughline narrative and infer a reordering?

**Consequence:** A Results writer might reorder subsections without a clear justification, and the reframer.v1 drift-audit (Check 4) might flag the reordering as unintended drift.

**Fix:** Add to results.v1 §"Three protocols" (after line 56):
```
   Reordering rule: if the throughline's evidence map has a 
   `preferred_subsection_order` field or narrative instruction 
   (e.g., "start with the cross-organism concordance finding, 
   then discuss condition-specific phenotypes"), follow it. 
   Otherwise, evidence-map order is canonical. Document any 
   reordering in the closing message: "subsection order: 
   {evidence-map order | {reordering rationale}}."
```

---

### SGx.5 — discussion.v1 "Conflicting findings" (lines 35-38) recommends engagement but lacks specificity
**Location:** discussion.v1 §"Output structure" (lines 35-38)  
**Issue:**  
Lines 35-38: "Conflicting findings (if pool has contradicts entries or throughline flagged contradictions). [Engage not ignore, name conflict + what cited paper found + how project differs or specific hypothesis for divergence]"

But what if the project's throughline has a `✗ contradicts` entry, yet the project itself doesn't have findings to explain the contradiction? For example, the throughline says "Smith2020 contradicts our hypothesis" but Results doesn't provide the project's data on the contradicting point.

**Consequence:** A Discussion writer might write "Smith et al. found X, but our analysis differs" without actually showing what the project found to differ. The finding is not engaged — it's just acknowledged.

**Fix:** Add to discussion.v1 §"Output structure" (after line 38):
```
   Contradiction-engagement rule: if the throughline flags a `✗ 
   contradicts` entry but Results has no corresponding data to 
   contrast with the contradiction, move the entry to "Orthogonal 
   perspectives" (demoted to ◇ orthogonal) or Limitations. Do NOT 
   engage a contradiction you cannot substantiate with project data. 
   If you must engage, reframe: "Smith et al. prioritize different 
   criteria; our organism-specific context may explain the divergence" 
   is engagement. "Smith found X; we did not measure X" is not engagement.
```

---

### SGx.6 — abstract.v1 "Structured abstract" (line 1) is not machine-readable in output
**Location:** abstract.v1 §"Output format" (lines 3-12) and §"What you produce" (lines 36-39)  
**Issue:**  
The prompt specifies an IMRAD structure (Background | Methods | Results | Conclusions) with hard sentence counts. But the output is just markdown prose, not structured data. If a downstream validator needs to re-count Abstract sentences per section, it must parse prose, not a machine-readable format.

**Consequence:** The M2 validator (SPEC §7, "structured abstract validator") must regex-parse the Abstract to extract sections, which is fragile if a writer uses a subheading or unusual formatting.

**Fix:** After writing the abstract via `Write`, append metadata in a code fence:
```markdown
<!-- Abstract Structured Metadata (for validators) -->
<!-- Background: 2 sentences | Methods: 2 | Results: 3 | Conclusions: 2 | Total words: 310 -->
```

And note in the closing-message template:
```
05_abstract.md written, N words; IMRAD sections: 
[Background={Nb} | Methods={Nm} | Results={Nr} | Conclusions={Nc}]; 
word budget: {WORD_BUDGET}. Metadata comment appended for M2 validator.
```

---

### SGx.7 — reframer.v1 Check 2 (REPORT-vs-Discussion claim drift) allows orphan claims to fold into Limitations
**Location:** reframer.v1 §"Check 2" (lines 146-155)  
**Issue:**  
Lines 151-155 state:
```
- **Claim doesn't trace** → drift. Append `type: reframing` entry 
  noting the orphan Discussion claim, with `Resolution: escalated`.
```

But the escalation path is unclear. Does "escalated" mean the orchestrator will dispatch a Discussion REPAIR_MODE to remove the claim, or will the claim be accepted as a Limitations caveat? The reframer logs the drift but doesn't prescribe the fix.

**Consequence:** Ambiguous orchestrator action. The drift is flagged, but the user may not know whether the system expects them to edit Discussion manually or wait for an automatic fix.

**Fix:** Expand the resolution language:
```
   - **Claim doesn't trace** → drift. Append `type: reframing` entry 
     with `Resolution: escalated` and `Note` specifying the recommended 
     fix:
     - If the claim is a genuine insight derived from Results, recommend 
       REPAIR_MODE on Discussion to ground the claim in Results.
     - If the claim cannot be grounded (the project doesn't have the data), 
       recommend acceptance-as-limitation or manual removal.
```

---

### SGx.8 — rewrite.v1 "Cascade detection" (lines 169-174) halts on cascade but doesn't prescribe recovery
**Location:** rewrite.v1 §"Discipline pass" (lines 169-174)  
**Issue:**  
Lines 169-174: "After applying all findings, walk the section once more. Did one fix introduce a contradiction with another fix? Did a fix break a citation number sequence? If yes, this is a cascade — STOP. Do not compound the cascade by adding more fixes; flag in the closing message and let the next rewrite pass (if any) handle it."

But SPEC §8.3 says there's a 2-rewrite hard cap. If a cascade is detected on pass 1, and the orchestrator tries pass 2 but another cascade appears, what happens? The prompt says "let the next rewrite pass handle it," but if this is pass 2, there is no next pass.

**Consequence:** On pass 2, a cascade triggers the halt (line 185: "cascade-detected: true"), and the orchestrator doesn't run a third pass (per SPEC). But the manuscript still has the unresolved cascade. The user is left with a drifted manuscript and no clear recovery path.

**Fix:** Add to rewrite.v1 §"Pass-2 discipline" (after line 190):
```
   If a cascade is detected on pass 2: write back the pre-rewrite 
   section (undo all fixes from this pass) and surface `cascade-detected: true` 
   with a closing message recommending manual review or Limitations 
   acknowledgment. The SPEC §8.3 hard cap prevents a third rewrite; 
   the user/orchestrator must decide whether the cascade is acceptable 
   as-is or requires manual manuscript edits.
```

---

### SGx.9 — fallback_reviewer.v1 "Scope creep" (lines 214-217) warns against literature-scan but doesn't define the boundary
**Location:** fallback_reviewer.v1 §"Anti-patterns" (lines 214-217)  
**Issue:**  
Lines 214-217: "Scope creep into full-reviewer territory. Spawning literature-scan agents, doing biological-claim verification, running drift-from-REPORT numerical cross-checks. These are all explicit non-features of the fallback."

But where's the boundary? If a claim is "X is well-established in the literature," is verifying that via a single WebSearch "scope creep" or "minimal due diligence"? The prompt forbids WebSearch (line 209) but doesn't explain why, leaving a reviewer unsure whether a light fact-check is in scope.

**Consequence:** Overly conservative fallback reviewers might not flag obvious fabrications (e.g., "Smith et al. 2050" — a future publication) because they're unsure whether catching such obvious errors counts as "scope creep."

**Fix:** Replace lines 214-217:
```
   **Scope boundary.** The fallback reviewer examines what's already in 
   the manuscript (prose, citations, structure). It does NOT verify whether 
   cited works exist, whether they contain the claims attributed to them, or 
   whether the claims are scientifically sound. One exception: fabrication 
   red flags (future year, impossible journal name) are flagged without 
   verification. The full beril-adversarial reviewer handles the rest. This 
   boundary is intentional: the fallback is fast (inline during drafting); 
   the full reviewer is thorough (post-hoc, ~5-10 min).
```

---

### SGx.10 — intro.v1 "Research question = throughline" (lines 244-249) doesn't address scope narrowing
**Location:** intro.v1 §"Discipline pass → Only-what-paper-delivers" (lines 244-249)  
**Issue:**  
Lines 244-249 state: "Research question → must match the throughline's claim. The Introduction's question is the throughline's claim phrased as a question; if you find yourself wanting to ask a *different* question because Introduction 'needs' it, scope down — the research question doesn't get to drift from the throughline."

But what if the throughline itself is narrower than the original research question in RESEARCH_PLAN? For example, the plan asks "What is the function of dark genes?" but the chosen throughline is "Which dark genes are enriched for stress response?" The Introduction must phrase the narrower question.

The prompt's wording ("the research question doesn't get to drift") could be misread as "always match the original plan's research question," not "always match the chosen throughline's claim."

**Consequence:** An Introduction writer might revert to the broader original question, overclaiming relative to the throughline.

**Fix:** Clarify lines 244-249:
```
   - **Research question = throughline's claim, interrogative form** — not 
     the original RESEARCH_PLAN question. The chosen throughline may be 
     narrower than the original plan. The Introduction's research question 
     must match the throughline's **selected scope**, even if it's narrower 
     than the original hypothesis. Example: if RESEARCH_PLAN asks "What is 
     the function of X?" and the throughline answers only "X is enriched for 
     condition Y," the Introduction's research question is "Is X enriched for 
     condition Y?" — not the broader question.
```

---

### SGx.11 — revise_throughline.v1 "Post-processor cross-walk" (line 255) is a soft recommendation, not a gate
**Location:** revise_throughline.v1 §"Closing-message template" (line 255)  
**Issue:**  
The closing message includes "post-processor cross-walk recommended," but the prompt doesn't say what happens if the post-processor fails. If `tools/check_throughline_glyphs.py` detects a glyph inconsistency, does it halt the orchestrator or just warn?

**Consequence:** A user might think the throughline is ready for drafting after revise_throughline completes, but downstream drafting prompts may encounter glyph mismatches they expected to be fixed.

**Fix:** Clarify the closing message:
```
   If the post-processor detects glyph inconsistencies, it exits 0 
   (advisory) and surfaces warnings via stderr. The orchestrator's 
   behavior is configurable: halt-on-warning (safe, conservative) or 
   proceed-with-logged-warning (fast, requires user review). Consult 
   the orchestrator's --strict-glyph-check flag (default: true).
```

---

### SGx.12 — citation_pool.v1 cap enforcement (line 47, MAX_BUDGET=80) is not tier-proportional
**Location:** citation_pool.v1 §"Cap enforcement" (line 47)  
**Issue:**  
Line 47: "MAX_BUDGET default 80 is ceiling not target."

But the prompt earlier (lines 57-60) recommends:
```
Tier-aware sizing: STRONG ~40-70 | THIN ~25-45 | EXPLORATORY ~15-30
```

If MAX_BUDGET is 80, a STRONG-tier pool might target 70 (within budget) but a sloppy implementation could add 80. The "~40-70" is guidance, not a hard cap.

**Consequence:** STRONG-tier pools might be over-provisioned (80 entries) while tier-specific guidance suggests smaller (40-70). A downstream Discussion might bloat with over-sourced claims instead of focused evidence.

**Fix:** Add to citation_pool.v1 §"Cap enforcement" (after line 47):
```
   Tier-specific hard cap (overrides MAX_BUDGET if lower):
   - STRONG: MAX_BUDGET = 70 (not 80)
   - THIN: MAX_BUDGET = 45
   - EXPLORATORY: MAX_BUDGET = 30
   
   If the pool exceeds tier-specific cap, prioritize by category 
   (Background > Methods > Comparators > Conflicting > Orthogonal) 
   and drop from Orthogonal first.
```

---

### SGx.13 — methods_provenance.md not mentioned in any prompt's inputs
**Location:** intro.v1, results.v1, discussion.v1, reframer.v1 input specs  
**Issue:**  
LAYOUT.md §"Orchestrator capabilities" (line 239) mentions "method-provenance extraction" and SPEC §6.1 references methods_provenance.md. But intro.v1, results.v1, and discussion.v1 don't list `METHODS_PROVENANCE_PATH` in their inputs. Only reframer.v1 (line 81-82) mentions it.

**Consequence:** Results.v1 cannot verify that a method claim in Results is grounded in the provenance, if the writer chooses to. Discussion.v1 cannot reference the methods provenance to verify methodological claims.

**Fix:** Add to intro.v1, results.v1, and discussion.v1 input specs:
```
   - `METHODS_PROVENANCE_PATH` — `<DRAFT_DIR>/methods_provenance.md`. 
     Optional reference for verifying method claims ground in execution 
     (intro.v1 and discussion.v1 use only for cross-checks; results.v1 
     uses to verify method callouts in figure captions).
```

---

### SGx.14 — No prompt explicitly forbids future-tense claims in a finished draft
**Location:** All drafting prompts (plan.v1, methods.v1, results.v1, discussion.v1, intro.v1, abstract.v1)  
**Issue:**  
A draft is supposed to be a finished manuscript claiming what was done, not what will be done. But no prompt explicitly forbids sentences like "Future work will investigate..." in the main Results or Methods sections (as opposed to Discussion's Next Steps, where forward-looking is appropriate).

**Consequence:** A Results writer might include "We anticipate that future studies will address compositional inflation" in the main Results, which is Discussion/Limitations territory, not Results.

**Fix:** Add a self-review item to results.v1:
```
10. **No future-tense main claims.** Results reports what WAS found, not 
    what WILL be investigated. Grep for "will ", "anticipate", "hope", 
    "should". If found in subsections before "Findings Summary," move to 
    Discussion's "Next Steps." Future-tense in Findings Summary is 
    acceptable only as caveat: "X was not measured; future work should 
    address this" (→ Limitations, not Results proper).
```

---

## Cross-Prompt Consistency Assessment

### CPN.1 — Tier-aware framing language is inconsistent across prompts
**Prompts affected:** plan.v1, methods.v1, results.v1, discussion.v1, intro.v1, abstract.v1  
**Pattern:** Each prompt defines tier language slightly differently.
- plan.v1: no explicit TIER-AWARE guidance (tier is an output of the plan, not an input)
- methods.v1 §"Tier-aware framing": STRONG = declarative | THIN = explicit Act-II deferral | EXPLORATORY = cautious
- results.v1: STRONG = declarative | THIN = scope-narrowed explicit | EXPLORATORY = cautious descriptive with null/negative findings prominent
- discussion.v1: STRONG = engages substantively | THIN = explicit caveats | EXPLORATORY = hypothesis-generating
- intro.v1: STRONG = declarative | THIN = scoped | EXPLORATORY = cautious, no novelty positioning
- abstract.v1: none specified (implicitly inherits from body sections)

**Issue:** Different vocabulary ("explicit Act-II deferral" vs. "scope-narrowed explicit" vs. "explicit caveats" vs. "scoped"). A THIN-tier manuscript could have inconsistent voice across sections.

**Recommendation:** Establish a canonical "tier-voice dictionary" as a shared reference document (e.g., `docs/tier_language_guide.md`) and reference it in all prompts:
```
STRONG: Declarative, confident claims grounded in quantitative evidence.
THIN: Explicitly scoped claims; deferrals flagged ("We did not measure X; 
       future work should address this").
EXPLORATORY: Cautious, hypothesis-generating language; no causal claims; 
             results framed as preliminary observations, not conclusions.
```

---

### CPN.2 — Reframing-log schema is enforced inconsistently
**Prompts affected:** intro.v1, methods.v1, results.v1, discussion.v1, reframer.v1, rewrite.v1  
**Pattern:** 
- intro.v1 (lines 456-473) specifies exact schema format
- methods.v1 doesn't mention reframing-log at all
- results.v1 doesn't mention reframing-log at all
- discussion.v1 doesn't mention reframing-log at all
- reframer.v1 (lines 38-52) specifies extended schema with 5-value type enum
- rewrite.v1 (lines 150-163) specifies yet another variant

**Issue:** Prompts that can discover drift (methods.v1, results.v1, discussion.v1) don't acknowledge the log. Only intro.v1 (one section) is explicitly equipped to log reframing-log entries. This creates a asymmetry: if a Methods writer discovers plan-vs-execution discrepancy, they can't log it via their own prompt (no log schema provided).

**Recommendation:** Add to methods.v1, results.v1, and discussion.v1 input specs:
```
   - `REFRAMING_LOG_PATH` — `<DRAFT_DIR>/reframing_log.md`. If you 
     discover drift or discrepancy during drafting, append entries per 
     SPEC §5.6 schema (read intro.v1 for the template). Halt the prompt 
     if the drift is irreconcilable (e.g., Results contradicts REPORT 
     numerically without a notebook-grounded alternative); escalate via 
     reframing-log.
```

---

### CPN.3 — "Fabrication" is defined differently across prompts
**Prompts affected:** methods.v1, citation_pool.v1, results.v1, discussion.v1, figure_caption.v1, fallback_reviewer.v1  
**Pattern:**
- methods.v1 (line 20): "No fabricated methods (forbidden claim any method not pointed to in plan or executed code)"
- citation_pool.v1 (implied): any entry with a null identifier or unverified Studied/Finding is suspect
- results.v1 (implied): any number not in REPORT or notebook is suspect
- discussion.v1 (line 27): compound citations are forbidden; authority-without-specificity is forbidden
- figure_caption.v1 (line 12): "caption fabrication — inventing n-values, p-values, panel labels, or interpretive framing that doesn't trace"
- fallback_reviewer.v1 (line 176): "orphan citations (`[N]` not in `references.md`)"

**Issue:** No single definition of fabrication unites these prompts. A Methods writer checks against "plan or execution"; a Results writer checks against "REPORT or notebook"; a figure_caption.v1 writer checks against "inputs only." These are not mutually inconsistent, but they're not unified.

**Recommendation:** Add a "Fabrication discipline" subsection to LAYOUT.md §"Common concepts" (new section):
```
FABRICATION DEFINITION (unified across all drafting prompts):
A claim is fabricated if it cannot be traced to:
1. Project canonical sources (REPORT.md, RESEARCH_PLAN.md, notebooks, 
   executed code),
2. Verified bibliography (citation_pool entries that have passed verification), 
   OR
3. Explicit metadata from upstream (AST descriptors, panel labels from 
   structured_descriptor, n-values from tables_manifest).

Any claim not traceable to these three categories must be marked 
[NEEDS VERIFICATION: <claim>] or [FABRICATION RISK: <claim>] and 
escalated via reframing-log or REPAIR_MODE.
```

---

### CPN.4 — Hard caps and word budgets are not consistently named
**Prompts affected:** abstract.v1, results.v1, discussion.v1, figure_caption.v1  
**Pattern:**
- abstract.v1: "hard cap 450" (line 13) for STRONG paper, "400" for THIN, "350" for EXPLORATORY
- results.v1: "hard cap 3-6 sentences" (line 35) for Findings Summary; no figure caption word budget
- discussion.v1: word budget "STRONG 800-1500" (line 13); no hard cap named
- figure_caption.v1: "max_words — integer, default 200" (line 81); "HALT and re-draft if over max_words" (line 107)

**Issue:** abstract.v1 has "hard cap," results.v1 has "hard cap" for sentence count but not word count, discussion.v1 has "budget" without a hard cap, figure_caption.v1 has "max_words" without calling it a "cap."

**Consequence:** A writer unsure whether to enforce a strict limit or treat 200 words as guidance might choose differently based on the language used in their prompt.

**Recommendation:** Standardize terminology in LAYOUT.md:
```
BUDGET vs. CAP:
- BUDGET: target range (e.g., "Discussion 800-1500 words"). Exceeding 
  by 10% is acceptable if the overage is necessary for completeness.
- CAP: absolute maximum (e.g., "Abstract hard cap 450 words"). Exceeding 
  requires deletion or scope reduction before writing.

Apply consistently across all prompts.
```

---

### CPN.5 — Exception handling (escape hatches) varies in precision
**Prompts affected:** plan.v1, citation_pool.v1, methods.v1, results.v1, discussion.v1, intro.v1, abstract.v1, reframer.v1, rewrite.v1, fallback_reviewer.v1, figure_caption.v1  
**Pattern:** Some prompts specify exact error messages (e.g., intro.v1 line 193-194: `"Error: 03_discussion.md must be drafted before Introduction (per SPEC §6.1 drafting order). Aborting."`), while others are vague (e.g., plan.v1 has no escape hatch for "what if the project has no notebook?").

**Issue:** Inconsistency makes orchestrator error handling unpredictable. Some prompts raise with a message; some silently return null; some proceed with degraded output.

**Recommendation:** Establish a standard escape-hatch format in LAYOUT.md:
```
ESCAPE-HATCH PROTOCOL:
All prompts must halt (exit 1) with a structured error message when:
1. A required input file is missing or empty.
2. An input is malformed (unparseable JSON, missing schema fields).
3. The prompt detects a conflict that prevents honest output.

Error message format (no deviations):
"[ERROR: <prompt_name>] <required_file> missing or <condition>. 
{orchestrator_instruction: halt | degrade | skip}."

Example:
"[ERROR: methods.v1] RESEARCH_PLAN.md missing or empty. 
{orchestrator_instruction: proceed using REPORT.md for design intent; 
note in summary.}"
```

---

## Summary Table

| Severity | Count | Category | Load-bearing? |
|----------|-------|----------|--------------|
| Critical | 3 | Overclaim risk, citation discipline | Yes — manuscript validity |
| Important | 9 | Prompt reliability, consistency | Yes — system correctness |
| Suggested | 14 | Boilerplate, documentation, efficiency | No — quality improvements |

**Critical path to v1.0:**
1. Resolve CRx.1–CRx.3 (citation orphans, overclaim gates, drift detection ambiguity).
2. Resolve IMx.1–IMx.4 (glyph cross-walk clarity, validator coverage, citation format).
3. Address CPN.1–CPN.5 (tier language, reframing-log schema, fabrication definition, cap naming, escape hatches).
4. Remaining SGx items are polish; they can be deferred to v0.5.x if needed.

**Estimated effort:** 4-6 hours (edits to 8-10 prompts, new subsection in LAYOUT.md, optional reference doc).

