# Skeleton Conformance Audit: 5 Paper-Writer Prompts

## Summary

The 5 newer prompts (methods, results, discussion, plan, reframer) show strong compliance with the skeleton's section ordering and slot signatures, but reveal **three systematic deviations** worth addressing:

1. **Missing "Closing-message template" as its own subsection** — all 5 prompts embed the template inside "Output protocol" rather than as a separate callout section. The skeleton positions it as item §17 (distinct from item §16 Output protocol).
2. **REPAIR_MODE handling** — methods, results, discussion all nest REPAIR_MODE behavior as a subsection of "Output protocol" (correct per skeleton), but reframer lacks REPAIR_MODE entirely (appropriate, since reframer is audit-only). Plan has distinct "Drafting mode" and "Re-evaluation mode" sections instead of REPAIR_MODE.
3. **Schema section—absence in plan and reframer is appropriate**, but reframer's "Output format (reframing_log.md entry)" is functionally a schema without the full worked-example formality of methods/results/discussion schemas.

**No ordering violations detected.** All 5 prompts follow the skeleton's top-level section sequence within disciplinary variations.

---

## Per-Prompt Audit

### methods.v1.md

**Section ordering:** Compliant. Runs Role and stakes → What you produce → Output format (schema) → Inputs → What to read → Escape hatches → What must cover (tier table) → Discipline pass → Tool use → Anti-patterns → Self-review → Output protocol (includes REPAIR_MODE as subsection) → Inviolable rules.

**Slot-signature compliance:**
- ✓ Role and stakes: 2 paragraphs, names failure mode (fluent fabricated methods), cites SPEC §6.3 + D-003 / D-018.
- ✓ What you produce: Output path (`papers/draft_N/01_methods.md`), format (markdown), downstream consumers named, Write-tool discipline stated.
- ✓ Output format: No full schema table (Methods is prose, not structured data), but subsection template (Datasets / Analytical Workflow / Statistical Analysis / Software / Computational Environment / AI-Assisted Analysis / optional QC) is well-specified. Worked example of Statistical Analysis given.
- ✓ Inputs: Bullet list, 12 parameters named, optionality marked (e.g., `REPAIR_MODE *(optional)*`), absolute-path discipline enforced.
- ✓ Escape hatches: 4 entries, each `{file/condition} → {behavior}`.
- ✓ What output covers: Subsection list (7 subsections), tier-aware framing table present.
- ✓ Discipline pass: "Methods grounding" — largest section, structured as three paths (grounded in execution / intent / discrepancy).
- ✓ Tool use: 1-paragraph summary, lists Read/Write/Bash/Grep/Glob, no WebSearch / Agent per discipline.
- ✓ Anti-patterns: 6 entries (Fluent fabrication, Plan-as-truth, Glossing over discrepancies, Version laundering, Reproducibility theatre, Stub headers).
- ✓ Self-review: 9 items + anti-example pairs in two groups (Validator-blocking errors, Silent traps).
- ✓ Output protocol: 5 steps, includes self-review pass + Write + no per-section validator invocation + REPAIR_MODE subsection.
- ✓ Closing-message template: Exact format block embedded in Output protocol.
- ✓ Inviolable rules: 4 items, override-only, not body-restatement.

**Cross-cutting consistency:** No rule duplication across sections. Operationalized fuzzy terms (e.g., "implied-but-not-explicit steps"). Escape hatches mandatory. Tier shifts language, never grounding floor (explicitly stated in tier table). No cost checkpoint (N/A — no external budget). No unbounded retry; REPAIR_MODE bounded to 2 attempts. Inviolable rules short (4 items). Length: ~471 lines ✓.

**Matrix alignment:** Skeleton says methods.v1 has Schema=md / What output covers=subsections / Tier=yes / Caps=no / Depth=no / Discipline kind=Grounding. Actual: ✓ matches.

---

### results.v1.md

**Section ordering:** Compliant. Follows skeleton ordering, adding task-specific "Discipline pass — Numerical-claim verification, throughline alignment, figure selection" with three labeled sub-protocols (1. Throughline alignment / 2. Numerical-claim verification / 3. Figure selection).

**Slot-signature compliance:**
- ✓ Role and stakes: 1 paragraph, names failure mode (silent drift from REPORT.md), cites SPEC §6.1 / §6.2 / §3.
- ✓ What you produce: Path, format (markdown), downstream consumers, Write discipline, also mentions appending to reframing_log and figure copying.
- ✓ Output format: Markdown prose organized by throughline sub-claims. Worked example subsection + figure callout + table reference discipline. Clear.
- ✓ Inputs: 11 parameters, absolute paths, optional inputs marked.
- ✓ Escape hatches: 5 entries.
- ✓ What output covers: Throughline's evidence map → subsections. Tier-aware framing table present.
- ✓ Discipline pass: "Numerical-claim verification, throughline alignment, figure selection" — 3 sub-protocols, each operationalized (Throughline alignment symbols explained, Numerical-claim verification with grep budget, Figure selection with 4–8 target).
- ✓ Tool use: Read/Write/Bash/Grep/Glob, no WebSearch / Agent.
- ✓ Anti-patterns: 6 entries (Number fabrication, Silent reframing, Cherry-picking, Bare percentages, Missing CI/effect size, Figure call-out drift, Stub subsections — actually 7).
- ✓ Self-review: 10 items + anti-example pairs (Validator-blocking, Silent traps).
- ✓ Output protocol: 7 steps, includes figure selection / reframing-log append / self-review + Write. REPAIR_MODE subsection present, bounded to 2 attempts.
- ✓ Closing-message template: Exact format in Output protocol.
- ✓ Inviolable rules: 4 items.

**Cross-cutting consistency:** Operationalized "grep-traceable," "partial," "direct," etc. No rule duplication. Tier shifts language, not discipline floor (stated explicitly in tier table). No cost checkpoint. Bounded retry (2 attempts). Inviolable rules short (4 items). Length: ~487 lines ✓.

**Matrix alignment:** Schema=md / What output covers=subsections / Tier=yes / Caps=no / Depth=no / Discipline kind=Grounding+numerical-claim cross-check. Actual: ✓ matches.

---

### discussion.v1.md

**Section ordering:** Compliant. Follows skeleton, with task-specific "Discipline pass — Citation, scope, conflicting-findings, pool exhaustion" having 4 labeled sub-protocols.

**Slot-signature compliance:**
- ✓ Role and stakes: 3 paragraphs, names failure mode (overclaiming in Discussion via scope leap / mechanism inference / generalization), cites SPEC §6.4.
- ✓ What you produce: Path, format (markdown), downstream, Write, also appends to reframing_log.
- ✓ Output format: Markdown organized by thematic interpretation. Subsections: Summary / Findings in context (2–4 thematic) / Conflicting findings / Limitations / Next steps. Worked example given. For report mode, output is "Observations and Open Questions."
- ✓ Inputs: 14 parameters, absolute paths, optionality marked.
- ✓ Escape hatches: 5 entries.
- ✓ What output covers: Throughline's claim, in scope. Tier-aware framing table.
- ✓ Discipline pass: "Citation, scope, conflicting-findings, pool exhaustion" — 4 sub-protocols, each operationalized (Citation discipline with pool rules / Scope discipline with throughline filter / Conflicting findings engagement / Pool exhaustion handling with 3 options).
- ✓ Tool use: Read/Write/Bash/Grep/Glob, no WebSearch / Agent.
- ✓ Anti-patterns: 7 entries (Inferential leap, Authority citation, Conflict erasure, Causal smuggle, Mechanism fabrication, Limitations as ritual, Next-steps as wishlist, Re-introducing numbers — actually 8).
- ✓ Self-review: 10 items + anti-example pairs.
- ✓ Output protocol: 10 steps, includes reframing-log append, self-review + Write. REPAIR_MODE subsection present.
- ✓ Closing-message template: Exact format in Output protocol, includes pool exhaustion option language.
- ✓ Inviolable rules: 4 items.

**Cross-cutting consistency:** Operationalized all fuzzy terms. No duplication. Tier shifts language. No cost checkpoint. Bounded retry. Inviolable rules short (4 items). Length: ~537 lines — **exceeds 500-line target by 37 lines**, but justified (Discussion has 4 discipline sub-protocols vs typical 1–2; pool exhaustion handling adds complexity).

**Matrix alignment:** Schema=md / What output covers=topics / Tier=yes / Caps=no / Depth=no / Discipline kind=Citation-pool constraint+scope discipline. Actual: ✓ matches.

---

### plan.v1.md

**Section ordering:** Compliant, with task-specific structure. Sections flow: Role → What you produce → Output format (template) → Inputs → What to read → Escape hatches → What candidates need to cover (with sub-rubrics) → Discipline pass (3 sub-protocols) → Tool use → Anti-patterns → Self-review → Output protocol (with Drafting mode / Re-evaluation mode subsections) → Inviolable rules.

**Slot-signature compliance:**
- ✓ Role and stakes: 3 paragraphs, names failure mode (LLM auto-picking linear/dramatic narratives over data-fit ones), cites SPEC §3.1 / §4 / §3.3.
- ✓ What you produce: Artifact is `throughline_candidates.md` (not a single throughline; 2–3 candidates for user pick). Path, format (markdown strict template per SPEC §4.2), downstream parsing by agents, pause-and-exit behavior.
- ✓ Output format: Strict per-candidate template (Candidate TL{N} / Evidence map table / Weakness inventory / What this paper would NOT include). Template is load-bearing. For THIN tier, 4th candidate (TL-NARROWED). Strength glyphs defined (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal).
- ✓ Inputs: 7 parameters + optional inputs marked.
- ✓ Escape hatches: 4 entries (empty REPORT triggers stub-candidate + gap-fill request).
- ✓ What output covers: Triage first → tier-aware candidate extraction. Rubric given for STRONG/THIN/EXPLORATORY. Tier-aware phrasing (declarative / scope-narrowed / preliminary).
- ✓ Discipline pass: "Candidate extraction, evidence-map building, weakness inventory" — 3 sub-protocols, each operationalized (avoiding narrative bias / strength glyph operationalization / weakness inventory specificity).
- ✓ Tool use: Read/Write/Bash/Grep/Glob, no WebSearch / Agent.
- ✓ Anti-patterns: 8 entries (Single-candidate output, Strength inflation, Hidden weaknesses, Orthogonal-finding burial, Plan-narrative candidates, Auto-pick framing, Triage-by-vibes).
- ✓ Self-review: 10 items + anti-example pairs.
- ✓ Output protocol: Two modes (Drafting / Re-evaluation) with separate closing-message templates per mode. Drafting: 8 steps. Re-evaluation: 3 outcomes (confirmed-still-valid / re-picked / abandoned).
- ✓ Closing-message template: Two exact-format blocks (drafting mode, re-evaluation mode).
- ✓ Inviolable rules: 4 items.

**Cross-cutting consistency:** Operationalized all terms (✓ direct requires "explicit quantitative establishment"). No duplication. **Note: No "Caps" section** — appropriate per matrix (plan.v1 has Caps=no). **Note: No "Depth" section** — appropriate (plan.v1 has Depth=no). **No cost checkpoint** (N/A). **REPAIR_MODE replaced by mode-specific Output protocol subsections** (Drafting mode / Re-evaluation mode) — semantically equivalent; reframing for the workflow. Length: ~500 lines ✓.

**Matrix alignment:** Schema=throughline-candidates.md / What output covers=2–3 candidates / Tier=yes / Caps=no / Depth=no / Discipline kind=Evidence-mapping. Actual: ✓ matches.

**Deviation note:** Plan's Output protocol structure is unique (Drafting vs Re-evaluation modes) vs standard REPAIR_MODE pattern in section prompts. This is **not a violation**; it reflects plan's different control flow (user pick between modes; orchestrator handles the pick). Skeleton does allow task-specific Output protocol structure.

---

### reframer.v1.md

**Section ordering:** Compliant. Runs Role → What you produce → Output format (log-entry template) → Inputs → What to read → Escape hatches → What to audit (5 checks) → Tool use → Anti-patterns → Self-review → Output protocol → Inviolable rules. **Notably lacks REPAIR_MODE subsection** — appropriate, since reframer is audit-only and does not repair.

**Slot-signature compliance:**
- ✓ Role and stakes: 2 paragraphs, names failure mode (manuscript silently contradicting REPORT), cites SPEC §5.6.
- ✓ What you produce: Appended entries (zero or more) to `reframing_log.md` in SPEC §5.6 format, plus one-line audit summary. Audit findings, not prose corrections.
- ✓ Output format: Log-entry template (Entry {N} / Issue / Source / Manuscript impact / Resolution / Note). Exact format per SPEC §5.6. Five `type:` enum values fixed. **No worked example of a full entry**, but log-entry structure is specified in-line.
- ✓ Inputs: 8 parameters, mode/tier/project root, read-only access to drafted sections + canonical sources.
- ✓ Escape hatches: 4 entries (missing sections / missing canonical sources → halt; missing reframing_log → init).
- ✓ What to audit: 5 drift checks (REPORT-vs-Results numerical / REPORT-vs-Discussion claim / Plan-vs-Methods discrepancy / Throughline-evidence-map vs Results / Abstract-body alignment).
- **Deviation: "What to audit" is not "What output covers"** — but semantically equivalent for this prompt. Reframer outputs log entries documenting drift; "what to audit" describes the detection framework.
- ✓ Tool use: Read/Grep/Glob/Write/Bash, no WebSearch / Agent.
- ✓ Anti-patterns: 6 entries (Spurious entries, Audit-by-paragraph-skim, Auto-fix-language, Generic note language, Stronger-abstract overclaim, Type-laundering).
- ✓ Self-review: 8 items (Existing log read first / All 5 checks run / Each entry complete / Type enum / Numbering preserved / Resolution correct / Notes specific / No drift hidden).
- ✓ Output protocol: 7 steps, includes defensive re-read + append + self-review + Write. **No REPAIR_MODE** (correct; reframer is audit, not repair). **No cost checkpoint** (N/A).
- ✓ Closing-message template: Exact format block in Output protocol. Two variants (K > 0 with counts; K = 0 clean-pass).
- ✓ Inviolable rules: 4 items.

**Cross-cutting consistency:** Operationalized drift-check types. No duplication. **No Tier section** — appropriate (reframer is mode/tier-agnostic). **No Schema section in traditional sense** — "Output format" specifies log-entry structure, which is functionally a schema. Length: ~301 lines ✓.

**Matrix alignment:** Skeleton matrix lists reframer.v1 as Schema=reframing_log.md entries / What output covers=— / Tier=no / Caps=no / Depth=no / Discipline kind=Drift-detection. Actual: ✓ matches (no What output covers section; Tier=no; Discipline kind is drift-detection).

---

## Cross-Prompt Patterns

### Consistent across all 5:

1. **Section ordering identical** — all follow skeleton's top-level sequence. Task-specific subsections (Discipline pass sub-protocols, REPAIR_MODE, Output protocol variants) are well-integrated.
2. **Closing-message template placement** — all 5 embed the template inside "Output protocol" as a separate subsection (`**Closing-message template (required exact format):**`), rather than as a standalone §17. Skeleton positions it as distinct from Output protocol (§16 vs §17), but the prompts' nesting is functionally equivalent and arguably clearer (template is the final step before Write).
3. **Escape hatches always present and mandatory** — all 5 have them, all 5 follow `{file/condition} → {behavior}` format.
4. **Anti-patterns consistently 6–8 entries** — all in 1-paragraph callout form, no duplication with body rules.
5. **Self-review as numbered checklist + anti-example pairs** — all follow the two-group anti-example structure (Validator-blocking / Silent traps).
6. **Inviolable rules all 4 items** — short, override-only, distinct from body-restatement.
7. **No fuzzy terms left operationalized** — all define "direct," "partial," "scope," "grounding," etc. inline.
8. **Tier-aware sections** — all 4 section prompts (methods / results / discussion / plan) have tier-aware framing. Reframer correctly omits (audit is mode/tier-agnostic).
9. **Length target ≤500 lines** — methods (471) / results (487) / plan (500) all on target. Discussion (537) exceeds by 37 due to 4 discipline sub-protocols. Reframer (301) is shortest, appropriate for audit-only.

### Inconsistencies (minor, acceptable):

1. **"What output covers" naming varies** — Methods/Results/Discussion call it "What the [section] must cover (and tier-aware framing)" or "What the [section] must cover + tier-aware sizing." Plan calls it "What the candidates need to cover + tier-aware extraction." Reframer has no section (N/A). This variation is task-appropriate, not a drift.
2. **Discipline pass sub-protocol structure** — Methods (3 paths: execution / intent / discrepancy). Results (3 labeled sub-protocols: alignment / verification / selection). Discussion (4 sub-protocols: citation / scope / conflicting-findings / pool exhaustion). Plan (3 sub-protocols: extraction / evidence-map / weakness). Reframer (5 checks, each its own subsection). All are operationalized; variation is task-driven.
3. **REPAIR_MODE vs mode-specific Output protocol** — Methods/Results/Discussion nest REPAIR_MODE as a subsection of Output protocol. Plan structures Output protocol itself as two modes (Drafting / Re-evaluation). Reframer has neither. Each is task-appropriate; no violation.

---

## Skeleton Update Recommendations

The skeleton is accurate and comprehensive. However, three patterns emerged from the 5 prompts that the skeleton could clarify:

### 1. Closing-message template placement

**Current skeleton language:** "Closing-message template: Inside the Output protocol section. **Required exact format**, not 'something like.'"

**Issue:** Skeleton lists "Closing-message template" as item §17 (after Output protocol §16), but the prompt reference implementation (citation_pool.v1.md) nests it inside Output protocol as a subsection. All 5 newer prompts follow citation_pool's pattern.

**Recommendation:** Clarify in the skeleton that "Closing-message template" is a **required subsection of Output protocol**, not a sibling section. Update the section ordering list to reflect this nesting (e.g., "16. **Output protocol** (numbered steps, including Closing-message template subsection)" or enumerate it as 16a/16b).

### 2. Task-specific discipline sub-protocols are load-bearing

**Issue:** The skeleton's "Discipline pass" slot signature says "The task-specific protocol the prompt enforces" but doesn't specify that for complex prompts (Results with 3 sub-protocols, Discussion with 4, Plan with 3), the sub-protocols should be labeled (1. / 2. / 3.) for clarity.

**Recommendation:** Add to the Discipline pass slot signature: "For complex tasks with multiple sub-protocols, each sub-protocol should be labeled and operationalized independently (e.g., '1. Throughline alignment', '2. Numerical-claim verification')."

### 3. Schema section optional for audit / non-structured-output prompts

**Current skeleton:** "Skip only for prompts whose output is free-form prose (rewrite, fallback_reviewer)."

**Finding:** Reframer's "Output format (reframing_log.md entry, per SPEC §5.6)" is functionally a schema (fixed-field log-entry template with 5 required fields), even though reframer is an audit prompt, not a traditional drafter. The prompt correctly includes this schema-like section.

**Recommendation:** Clarify that "Schema" is required for any prompt whose output has a fixed structure (including audit logs, entry formats, etc.), even if the output is append-only or non-prose. The slot signature remains unchanged; the note should just broaden "structured output" to include metadata/log structures.

---

## Three Concrete Fixes

### Fix 1: Skeleton §9 — Clarify "What output covers" naming flexibility

**Current text:** "What the [output] needs to cover (task-specific; for prompts that produce sectioned content)"

**Problem:** Plan calls it "What the candidates need to cover"; Reframer has no section. The naming is correct per task, but the skeleton could be clearer that it's task-dependent.

**Proposed edit:** Change §9 from "What the [output] needs to cover" to "What the output needs to cover (task-specific; for prompts that produce sectioned content; for audit/other prompts, name the content-scope section to match task)". Example: "What to audit (5 drift checks)" for reframer.

### Fix 2: Methods.v1 output protocol — Remove "REPAIR_MODE behavior" subsection title, nest it under Output protocol step 4

**Current state:** REPAIR_MODE is a subsection of Output protocol (correct per skeleton). But the subsection title "REPAIR_MODE behavior" is not required by skeleton; skeleton says "Bounded retry on validation failure" is step 6.

**Issue:** Minor — not a violation, just inconsistent naming. Methods (steps 1–7 + REPAIR_MODE subsection) is correct.

**Action:** No fix needed; methods.v1 is correct.

### Fix 3: Discussion.v1 — Shorten by consolidating pool-exhaustion language

**Current:** Discussion runs 537 lines (exceeds 500 by 37). Discipline pass § "4. Pool exhaustion handling" repeats language that appears in "Escape hatches" (empty pool → proceed with placeholders) and in "Output protocol" step 5 (count placeholders, surface options).

**Proposed edit:** In Discipline pass §4, consolidate to: "When a Discussion claim needs a citation the pool doesn't have: (1) Check scope first — scope down rather than expand pool. (2) If in-scope, mark `[NEEDS CITATION: ...]`. (3) Output protocol step 5 surfaces options. See SPEC §6.4.1 for the three paths."

**Expected outcome:** Shorten Discussion by ~50 lines, landing it at ~487 (within target).

---

## Conclusion

The 5 prompts demonstrate strong skeleton conformance. **No mandatory fixes required.** The three recommendations above are refinements to the skeleton for future-prompt clarity, not corrections to existing prompts. Citation_pool.v1.md (the reference implementation) and the 5 newer prompts establish a consistent pattern; the skeleton should be updated to reflect that pattern where it diverges from the current text.

