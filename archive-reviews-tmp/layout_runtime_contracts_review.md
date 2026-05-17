# Review: LAYOUT.md Runtime-Contract Additions

**Date:** 2026-04-25  
**Reviewed sections:** Lines 175–294 (File-ownership notes, Per-section prompt invocation contract, Validator dispatch table, state.json schema)  
**Cross-references:** SPEC.md §5.5–5.6, §6.1, §6.4–6.4.1, §7.1–7.1.2; validate_manuscript.py; prompts discussion.v1, methods.v1

---

## Summary

LAYOUT's new sections add runtime contracts for orchestrator ↔ prompt interaction, validator dispatch, and state management. **Three significant gaps emerge:**

1. **REPAIR_MODE input incompleteness** — four named inputs are insufficient; REPAIR_MODE prompts also need the section's original drafting inputs (THROUGHLINE_PATH, REPORT_PATH, etc.) to avoid regenerating the entire section. No mechanism exists to communicate *which specific span* failed to the prompt.

2. **Validator-dispatch coverage gaps** — M1 dispatch is ambiguous (section fails at assembly, but what triggers re-draft?); M4 is unmapped (data-availability template not yet written); M10 edge case (orphan citations appearing in multiple sections) lacks routing rules.

3. **Reframing-log entry-numbering contract is missing** — six prompts append entries, but no contract specifies how entry N is chosen. Silent race-condition risk if parallelization is added later.

These are not showstoppers, but they create **ambiguity at implementation time** and will require design clarification before coding begins.

---

## REPAIR_MODE Contract Gaps

**LAYOUT §"Per-section prompt invocation contract" names four REPAIR_MODE inputs:**
- `REPAIR_MODE = "true"`
- `NAMED_VALIDATOR` (e.g., `"M9"`)
- `VALIDATOR_OUTPUT_PATH` (file with failure detail)
- `REPAIR_TARGET_PATH` (e.g., `01_methods.md`)

**What's missing:** the section's original *drafting-mode inputs*.

When a prompt is invoked in REPAIR_MODE, it must *read the existing section*, understand what NOT to change (the parts that already pass other validators), and fix *only* the named span. The contract does not pass:

- `THROUGHLINE_PATH` — the prompt needs to know what scope is in-bounds (e.g., M9 Limitations expansion must stay within throughline)
- `REPORT_PATH` — needed by Discussion (M9 repair) and Results (M7/M8 repair) to avoid contradicting the project's findings
- `RESULTS_PATH` — Discussion REPAIR_MODE (M9 Limitations) needs Results to understand what findings the Limitations must acknowledge
- `POOL_JSON_PATH` / `REFERENCES_MD_PATH` — Discussion REPAIR_MODE (M10 citation cross-ref) needs the pool to know which citations are available

**Evidence:** The discussion.v1.md prompt lists these inputs for drafting mode (lines 104–130); REPAIR_MODE is mentioned (lines 131–135) but the contract assumes these inputs are *also* present in REPAIR_MODE without stating it explicitly.

**Failure scenario:** M9 (Limitations) fails on discussion.v1. Orchestrator invokes REPAIR_MODE without passing THROUGHLINE_PATH. Prompt expands Limitations but cannot verify the expanded scope stays within the throughline's claim boundaries. Escalation path is ambiguous.

**Recommendation:** Clarify that REPAIR_MODE invocation includes *all* of the section's drafting-mode inputs, not just the four listed. Alternatively, document which inputs are optional in REPAIR_MODE and what the prompt should do if they're absent.

---

## VALIDATOR_OUTPUT_PATH Format Ambiguity

**LAYOUT states:** `VALIDATOR_OUTPUT_PATH` contains the validator's structured failure detail.

**The contract under-specifies:** what does "structured failure detail" mean?

Read validate_manuscript.py `Violation` class (lines 102–112): a violation has `severity`, `section`, `line`, `message`, and `escalation_path`. The output JSON schema (ValidationReport, lines 134–168) nests these in `validators[].violations[]`.

**What the prompt needs to know:**
- Does VALIDATOR_OUTPUT_PATH contain the entire ValidationReport JSON?
- Or just the `violations[]` array for the specific validator?
- Or just one Violation object?
- How does the prompt identify which file/line to edit if the report contains multiple violations?

**Failure scenario:** M7 (Effect sizes) fails on Results. The output JSON may flag 3+ paragraphs, each missing either an effect size or a CI. Orchestrator passes VALIDATOR_OUTPUT_PATH but the prompt has no contract specifying whether to fix all 3, or whether the orchestrator expects multiple REPAIR_MODE invocations (one per paragraph). Ambiguous escalation.

**Recommendation:** Specify VALIDATOR_OUTPUT_PATH format (e.g., "JSON ValidationReport for the named validator, with violations[] array") and document how the prompt handles multiple violations in a single invocation. If one invocation per violation is expected, state that the orchestrator partitions the violations before invoking REPAIR_MODE.

---

## Validator-Dispatch Table Coverage Gaps

**LAYOUT §"Validator → section dispatch table" (lines 278–293) maps M1–M10.**

### M1 (IMRAD sections) → (assembler)

**Stated:** Missing sections fail at assembly; orchestrator triggers re-draft of the missing section.

**Gap:** Who re-drafts? The contract says "orchestrator triggers" but does not specify:
- Does the orchestrator invoke the missing section's prompt directly?
- Or does it invoke a section-selection step that queries "which section should we draft?"
- What if multiple sections are missing? Sequential re-draft loops, or batch?

**Failure scenario:** Assembly finds both Introduction and Abstract missing. Orchestrator must re-draft both. No contract specifies ordering or whether the prompts run in sequence (Abstract needs the body to be settled per SPEC §6.1) or in parallel. Implementation ambiguity.

**Recommendation:** Specify M1 repair flow: "M1 failure lists missing sections; orchestrator invokes each missing section's prompt in IMRAD-order (Methods before Results before Discussion before Introduction before Abstract) with the current draft state."

### M4 (data availability) → (orchestrator)

**Stated:** Template-driven; orchestrator re-emits with the template's required fields.

**Gap:** The template file does not exist yet. LAYOUT §"File-ownership notes" mentions `reference/data_availability_template.md` as "planned."

**Failure scenario:** M4 fails on a draft. Orchestrator has no template to emit. The contract is forward-looking but breaks until the template is written. This is acceptable for v0.1 spec (pre-implementation), but the spec should flag it as a blocker: *"M4 repair depends on `reference/data_availability_template.md` implementation."*

**Recommendation:** Add a note: "M4 repair path is not yet implementable; the data_availability_template.md must be created before M4 failures can be handled. Blocked implementation task."

### M10 (citation cross-ref) → discussion.v1 OR results.v1

**Stated:** Whichever section has the orphan citation.

**Gap:** What if the orphan citation appears in *both* sections?

For example, a citation [42] is cited in Results (correctly) and in Discussion (orphaned in the references numbering). Which section does the orchestrator dispatch to? The contract gives no tie-breaker rule.

**Failure scenario:** M10 flags `[citation 42 missing from references.md]` but both Results and Discussion cite [42]. Orchestrator must pick one section to repair; prompts may fix it differently (Results might remove the citation; Discussion might fix the numbering). Race condition on which fix wins.

**Recommendation:** Specify: "If an orphan citation appears in multiple sections, dispatch to Discussion (as the later section in the drafting order); Discussion repair should verify that the citation is correct in Results as well and escalate if conflicting."

---

## state.json Schema Gaps

**LAYOUT adds two new schema fields:**

### `discussion.pool_exhaustion` Field (lines 333–339)

**Gap 1: Missing recommended-option field.** The schema shows `user_choice` (what the user picked) but not what the prompt *recommended* before the user picked.

**Failure scenario:** On `continue`, the orchestrator reads state.json and sees `pool_exhaustion = {needs_citation_count: 7, user_choice: "scope-down"}`. But was "scope-down" the prompt's first recommendation, or did the user override a "citation-request" recommendation? The log doesn't record this, so future analysis can't learn why the user rejected the recommendation.

**Recommendation:** Add `recommended_option` field: `{needs_citation_count: 7, recommended_option: "scope-down", user_choice: "scope-down", ...}`. This allows post-hoc analysis of user-override patterns.

### `validator_status` Enum (lines 344–351)

**Gap 2: No field for validators that haven't run yet.** The example shows 6 validators with various statuses; the enum lists 5 values: `pass | soft-warning | escalated | user-fixed | accepted-as-limitation`.

**What's missing:** what if a validator hasn't been invoked yet?

For example, on the first validation pass after drafting, M2 (structured abstract) has never run. What is `validator_status.M2`? Is it `null`? Absent from the dict? A sentinel value like `"not-run"`?

**Failure scenario:** Orchestrator reads state.json after drafting. It iterates over validator_status keys. If the dict is sparse (only contains validators that have run), the orchestrator can't distinguish "validator M5 ran and passed" from "validator M5 hasn't run yet." This breaks the logic for detecting which validators are still pending.

**Recommendation:** Either (a) initialize all M-validators to a `"not-run"` status on first write, or (b) document that validator_status is sparse and the orchestrator must check `"key in dict"` rather than `dict[key] == "pass"`. Add an enum value or clarify in the schema.

### `validator_status` Temporal Semantics

**Gap 3: No timestamp for when each validator's status was set.** The top-level `state.json` has timestamps (chose_at, decided_at) but validator_status entries do not.

**Failure scenario:** User manually edits `01_methods.md` on continue. Orchestrator re-runs M3 (AI-disclosure). Status changes from `"fail"` to `"pass"`. But when did this happen? The state.json `validator_status.M3` changes to `"pass"`, but there's no record of *when* the edit was made or *when* the re-validation succeeded. This breaks audit trail for the reframing_log.

**Recommendation:** Either add `validator_timestamps: {M1: "2026-04-25T15:10:00Z", ...}` alongside validator_status, or move validator_status entries to an array format: `[{id: "M3", status: "pass", at: "2026-04-25T15:10:00Z"}, ...]`.

---

## Reframing-Log Entry-Numbering Contract

**LAYOUT §"File-ownership notes" and §"Per-section prompt invocation contract" state:**
- Six prompts may append to `reframing_log.md` (citation_pool, methods, results, discussion, plan, reframer per SPEC §5.6).
- Each append is append-only; entries are never deleted or reordered.

**Gap: No contract for entry numbering.**

The SPEC §5.6 example shows entries numbered `## Entry {N} — {ISO timestamp} — type: ...` (line 531 of SPEC). But the contract does not specify:

- Does each prompt read the existing log and increment max(N) + 1?
- Or does the orchestrator assign entry numbers before invoking prompts?
- What if two prompts append simultaneously (parallelization future)? Do they collide on entry numbers?

**Failure scenario v1 (sequential, no parallelization):** Methods prompt appends `Entry 3` to reframing_log.md. Results prompt runs next, reads the file, finds max N = 3, appends `Entry 4`. This works fine for sequential execution. But the contract is silent on it, leaving implementation uncertainty.

**Failure scenario v2 (parallelization future):** Discussion and Results run in parallel (both read the log concurrently, both see max N = 2, both append `Entry 3`). Collision. Entry numbers are no longer unique. The log's append-only invariant is violated.

**Recommendation:** If v0.1 is sequential-only (per SPEC §5.3 and gap-fill caps), document: *"Reframing log entry numbers are assigned by prompts reading the existing file and incrementing max(N). Sequential execution ensures uniqueness."* If parallelization is ever introduced, add a contract: either (a) the orchestrator pre-assigns entry numbers before invoking prompts, or (b) prompts append without numbering and the orchestrator renumbers on assembly.

---

## Cross-Spec Consistency Checks

### SPEC §7.1.1 Four Escalation Paths vs. LAYOUT REPAIR_MODE Bounded-Retry

**SPEC §7.1.1 defines four paths for an M-tier failure:**
1. Auto-fix (writer attempts one fix)
2. Escalate as analysis-request
3. User-modify
4. Accept-as-limitation

**LAYOUT §"REPAIR_MODE" defines:**
- Bounded retry: 2 attempts per invocation
- After second failure: "halt with 'Halted after 2 repair attempts...; escalating per SPEC §7.1.1.'"

**Inconsistency:** LAYOUT says the prompt halts after 2 attempts and the orchestrator "then routes per the four escalation paths." But which path?

SPEC §7.1.1 says the user picks the path on `continue` (the prompt does not). But LAYOUT §"REPAIR_MODE" says the prompt decides to escalate and the orchestrator "routes."

**Failure scenario:** M9 (Limitations) fails. Orchestrator invokes discussion.v1 with REPAIR_MODE. Prompt attempts to expand Limitations; first attempt still fails M9 (too short). Second attempt still fails. Prompt halts. Orchestrator reads the halt message but has no routing logic from the prompt about which of the four paths to take. Does it prompt the user? Auto-escalate as analysis-request? This is a logic gap between the contract and the escalation flow.

**Recommendation:** Clarify: Either (a) REPAIR_MODE prompts include escalation-path guidance in their closing message (e.g., "Halted; suggest auto-fix is not viable; user-modify or accept-as-limitation are recommended paths"), or (b) the orchestrator always routes to user-modify on second REPAIR_MODE failure (user decides whether to edit, escalate, or accept-as-limitation).

### SPEC §5.5 Intercalation Hash-Diff vs. LAYOUT state.json Hashing

**SPEC §5.5 describes the hash-diff mechanism on `continue`:** orchestrator hashes source artifacts and compares against state.json's recorded hashes.

**LAYOUT state.json schema (lines 319–322) includes `source_artifacts[]` with `sha256` and `mtime` per artifact.**

**Gap:** The schema does not specify the **order** of hashes when concatenating for the throughline re-evaluation hash (line 307: "sha256 of all source-artifact hashes concatenated, sorted by path").

Is this SHA256(sorted_hashes) or SHA256(concat(sorted(hashes)))?

**Failure scenario:** Orchestrator computes throughline hash by concatenating all artifact hashes in sorted path order. On `continue`, it tries to re-compute the hash to see if it changed. If the concatenation order differs (sorted ascending vs. descending, or paths normalized differently), the hash won't match even though artifacts didn't change. False positive: "Throughline re-evaluation recommended" when it shouldn't be.

**Recommendation:** Specify: "Concatenate artifact hashes in **lexicographically sorted order of their file paths**, then compute SHA256 of the concatenation." Include an example.

### SPEC §6.4.1 Pool-Exhaustion Options vs. state.json Recording

**SPEC §6.4.1 (lines 672–690) defines three options the user picks:**
1. Scope down (default)
2. Spend a gap-fill round on citation-request
3. Accept-as-limitation

**LAYOUT state.json (lines 335–336) shows:**
```
options_offered: ["scope-down", "citation-request", "accept-as-limitation"],
user_choice: "scope-down",
```

**Consistency: OK.** The schema matches SPEC. But see *state.json Schema Gaps > Gap 1* above: the recommended option is missing from the schema.

---

## Three Concrete Fixes

### Fix 1: Clarify REPAIR_MODE Input Completeness

**Location:** LAYOUT.md line 249–256 (REPAIR_MODE section)

**Change:** Replace the four-input list with a note that clarifies the full set:

```
After running `validate_manuscript.py` and finding failures, the
orchestrator dispatches each failure to the relevant section prompt
in REPAIR_MODE. Inputs in addition to the **full drafting-mode input set**:

- `REPAIR_MODE` — `"true"`.
- `NAMED_VALIDATOR` — one of `M1`...`M10`.
- `VALIDATOR_OUTPUT_PATH` — file containing the validator's structured
  failure detail (which span, which file, exact violation message).
- `REPAIR_TARGET_PATH` — the section file to modify (e.g.
  `01_methods.md`).

The section prompt receives all of its original drafting-mode inputs
(THROUGHLINE_PATH, REPORT_PATH, RESULTS_PATH, POOL_JSON_PATH, etc.) in
addition to the above four. This ensures the prompt can read the
existing section, understand what not to change, and fix only the
named span without regenerating unrelated content.
```

### Fix 2: Document M4 Repair as Blocked

**Location:** LAYOUT.md line 287 (M4 row in validator dispatch table)

**Change:** Add a note row or update the Notes column:

```
| M4 (data availability) | (orchestrator) | **BLOCKED in v0.1:** Requires `reference/data_availability_template.md` to be created. Template path not yet written. Orchestrator cannot emit re-formatted data-availability section without the template. Implementation dependency: must write template before M4 repair logic is added. |
```

### Fix 3: Specify Reframing-Log Entry-Numbering Contract

**Location:** LAYOUT.md after line 376 (after the "Path resolution" section)

**New section:**

```markdown
## Reframing-log entry-numbering contract

Multiple prompts may append entries to `reframing_log.md` during a
single drafting run: citation_pool, methods, results, discussion,
reframer per SPEC §5.6. Each prompt must assign a unique entry number
to avoid collisions in the append-only log.

**Entry-numbering rule (v0.1, sequential execution):**

1. Each prompt reads the existing `reframing_log.md` before appending.
2. Prompt extracts the maximum entry number from existing entries:
   `max_N = max([int(m.group(1)) for m in re.finditer(r'^## Entry (\d+)', file_content, re.MULTILINE)])`
   or 0 if the file is empty.
3. Prompt appends its entry with number = `max_N + 1`.

This works correctly for sequential execution (the default in v0.1).
If parallelization is introduced in future versions (multiple section
prompts running concurrently), entry-number assignment must be
coordinated by the orchestrator: pre-assign entry numbers before
invoking prompts, or renumber on assembly.
```

---

## Conclusion

The new LAYOUT sections add valuable runtime contracts but leave three categories of gaps:

1. **Incomplete input specifications** — REPAIR_MODE and M4 repair lack necessary detail.
2. **Dispatch ambiguities** — M1, M10, and the validator-to-escalation-path routing need clarification.
3. **Schema and protocol gaps** — state.json validator_status and reframing-log numbering need explicit contracts.

None of these are showstoppers for v0.1 spec-writing, but they will require design clarification and spec updates before implementation begins. The three fixes above are concrete edits that resolve the highest-impact gaps. Remaining issues (M4 template, VALIDATOR_OUTPUT_PATH format details) should be added to the implementation task list.
