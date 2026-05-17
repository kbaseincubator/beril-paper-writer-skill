# Scenario Walkthrough: functional_dark_matter Draft Run — Contract-Gap Review

## Summary

The six-prompt suite has three categories of input-contract gaps that will block a clean orchestrator implementation:

1. **Named-file handoffs with no canonical producer.** Citation pool expects `references.md` as an optional input to seed verification; results and discussion expect `methods_provenance.md` as a required input, but neither prompt documents how to invoke `extract_methods.py` or pass its output path into the downstream prompts.

2. **Templates passed verbatim with no specification.** Methods expects `AI_DISCLOSURE_TEMPLATE` and discussion expects it in `pool.json`, but neither the SPEC nor LAYOUT names where these templates live or how the orchestrator should read and pass them.

3. **REPAIR_MODE contract assumes orchestrator can dispatch per-validator failures, but the validator-output format is unspecified.** Methods, results, and discussion prompts expect `VALIDATOR_OUTPUT_PATH` to contain structured failure detail (which span, which file, exact text), but `validate_manuscript.py` is not documented; the prompts cannot know what shape they'll receive.

These gaps do not break individual prompts run in isolation but will force the orchestrator to either (a) guess at file layouts and template locations, (b) pre-generate validator contracts it hasn't seen, or (c) fail to dispatch repairs cleanly.

## Per-Prompt Input-Contract Audit

**plan.v1** (`throughline_candidates.md`): All required inputs are producible. `REPORT.md`, `RESEARCH_PLAN.md`, `NOTEBOOKS_DIR` are project artifacts; `FIGURES_INVENTORY_PATH` is optional and produced by `extract_figures.py`; output paths are orchestrator-controlled. No gaps.

**citation_pool.v1** (`pool.json`, `references.md`, `bibliography.bib`, `citation_map.md`): Inputs are clear except: (1) the prompt says "Downstream (`citation_pool.py format`) will render it into `references.md`, `bibliography.bib`, and `citation_map.md`" but does not specify whether the orchestrator or the prompt invokes this Python tool. Read the prompt's Output protocol: it writes only `pool.json` and runs `POOL_VALIDATOR_CMD` verbatim. So the orchestrator must invoke the Python formatter after validation. But the exact invocation is not documented in LAYOUT. (2) `EXISTING_REFERENCES_MD` is optional but the prompt calls it "seed of unverified candidates" — the verb "seed" is clear, but the contract is "what if EXISTING_REFERENCES_MD is present in the project but citation_pool.v1 hasn't run yet on draft_N?" This is logically possible (the project has a `references.md` from a prior draft or from manual curation) but the interaction is not spelled out in either the SPEC or the prompt.

**methods.v1** (`01_methods.md`): (1) **CRITICAL GAP**: `METHODS_PROVENANCE_PATH` is required input pointing to `methods_provenance.md`, which is produced by `extract_methods.py`. The prompt says this tool runs "before this prompt runs" but does not specify:
  - Whether the orchestrator runs it or the user pre-runs it (implied: orchestrator)
  - The exact command-line invocation (LAYOUT mentions `tools/extract_methods.py` but the invocation signature is not shown)
  - Where `methods_provenance.md` lives (implied: `<DRAFT_DIR>/` but not stated)
  - Whether the orchestrator passes the output path into the methods prompt or the prompt derives it from `PROJECT_ROOT`

(2) `AI_DISCLOSURE_TEMPLATE` is a required input (per M3 validator awareness). The prompt says "the orchestrator passes it filled with `{X.Y}`, `{model_id}`, `{project_id}`, `{sha}`, `{N}`." But the SPEC's §10.1 (`AI_DISCLOSURE_TEMPLATE` template) is not provided in the read sections. Where does the orchestrator get the original template? How does it fill the placeholders? Without seeing SPEC §10.1, this is unresolvable.

(3) `REFRAMING_LOG_PATH` is an output (append-only). The prompt says "Append plan-vs-execution discrepancy entries" per SPEC §5.6. But if this is the first section prompt to run, does the log already exist? The logic is: "Read the existing file, add your entries at the end, Write the full result back." If the file doesn't exist, the prompt should initialize it. The escape-hatch section says "if empty → proceed" but there is no escape hatch for "if missing." This is a silent assumption the orchestrator must handle.

**results.v1** (`02_results.md`): (1) **CRITICAL GAP**: `METHODS_PATH` is a required input pointing to `01_methods.md` produced by the prior methods prompt. This creates a **serial dependency** that must be enforced by the orchestrator: methods → results. The prompt says "Already drafted; you reference Methods sections by name but do not re-state them." But what happens if methods.v1 fails partway and writes only a partial `01_methods.md`? The prompt does not specify validation of the input file's completeness.

(2) `FIGURES_INVENTORY_PATH` is required. Produced by `extract_figures.py`. Same gaps as methods_provenance_path: invocation contract not specified.

(3) `FIGURES_OUT_DIR` is required input for output. The prompt says "Where selected figures land (copy or symlink, paper-order named)." But the prompt does not invoke `cp` or `ln` directly — it says "(copy or symlink, paper-order renamed)" in a subsection on "Output format," implying the orchestrator must handle the file ops. This is a hidden contract: results.v1 *logically* needs to copy/symlink figures but the prompts cannot call Bash directly. The orchestrator must do this, but it is not documented who is responsible.

**discussion.v1** (`03_discussion.md`): (1) `POOL_JSON_PATH` and `REFERENCES_MD_PATH` are required. The prompt says "every [N] in your prose must resolve to an entry in `REFERENCES_MD_PATH`" — but this requires the Python `citation_pool.py format` tool to have already run and produced the markdown output. If this has not run, the prompt cannot validate. No escape hatch for "pool not yet formatted." The prompt assumes `REFERENCES_MD_PATH` exists and is well-formed; if citation_pool.v1 halted before formatting or the format tool failed, the discussion prompt will fail with ambiguous error (missing file vs. format error).

(2) `ANALYSIS_REQUESTS_PATH` and `PRIOR_REVIEW_PATH` are optional but their absence is treated as "no contribution to Limitations from those sources." This is correct, but the escape hatch is: "absent → no contribution; proceed." What if the file is present but empty? What if it's malformed? No clear contract.

**reframer.v1** (appended entries to `reframing_log.md`): The prompt reads all five drafted sections (methods, results, discussion, intro, abstract). This requires a **strict sequential order** and assumes all five exist. But the SPEC and prompts do not enforce this order in the orchestrator contract. Per the LAYOUT, the order is: Plan → Throughline → (Citation Pool → Methods → Results → Discussion → Introduction → Abstract) → Reframer. But if any section fails, does the orchestrator halt or skip to reframer? The prompt says "Any drafted section missing → halt." So the orchestrator must guarantee all five exist before invoking reframer. This is a load-bearing assumption not documented in a central place (the orchestrator contract).

## End-to-End Data-Flow Gaps

### Gap 1: Extract-tool invocation contract is implicit

**Flow:** orchestrator runs `extract_methods.py` → produces `methods_provenance.md` → orchestrator passes path to methods.v1 → methods.v1 reads it.

**Problem:** Neither methods.v1 nor LAYOUT specifies:
- The exact command line: `python extract_methods.py <project_root> <notebooks_dir> <output_path>`? Or does it read paths from state.json?
- Whether the orchestrator or the user is responsible for running it.
- Error handling: if extract_methods.py fails, does the orchestrator retry, halt, or proceed with a warning?

**Consequence:** A fresh orchestrator implementation will have to either (a) reverse-engineer from the Python tool itself (brittle), or (b) ask for clarification.

### Gap 2: Template initialization (AI_DISCLOSURE_TEMPLATE, data_availability_template)

**Flow:** orchestrator reads `data_availability_template.md` from `reference/` → fills placeholders → writes to `07_data_availability.md`. orchestrator reads AI-disclosure template → fills placeholders → passes string to methods.v1.

**Problem:** The LAYOUT mentions both templates exist but does not:
- Specify where templates live (assumed: `reference/` but not explicit)
- Specify the placeholder syntax (assumed: `{field}` but not explicit)
- Specify which orchestrator component fills them (implied: a Python helper, but not named)
- Provide the actual template text or a pointer to it

**Consequence:** The orchestrator must either hardcode templates or implement a template-loading mechanism with an undocumented format.

**Specific example:** methods.v1 says `AI_DISCLOSURE_TEMPLATE` is passed "filled with `{X.Y}`, `{model_id}`, `{project_id}`, `{sha}`, `{N}`." What are X and Y? Without seeing the template, these placeholders are undefined.

### Gap 3: Validator-output format for REPAIR_MODE

**Flow:** orchestrator runs `validate_manuscript.py` M1–M10 → gets failures → orchestrator parses failures → dispatches each failure to the relevant section prompt in REPAIR_MODE with `VALIDATOR_OUTPUT_PATH` pointing to structured failure detail.

**Problem:** The section prompts (methods.v1, results.v1, discussion.v1) expect `VALIDATOR_OUTPUT_PATH` to contain "structured failure detail (which span, which file, exact text)" but:
- The validator tool is not documented (no `validate_manuscript.py` specification visible in reads).
- The failure-output format is not specified (is it JSON? plain text? line-number + span format?).
- The recovery contract is unclear: does the prompt read the file and self-parse it, or does the orchestrator pre-parse and pass a structured object?

**Consequence:** The orchestrator cannot design the validator until it knows what output format to expect. The section prompts cannot implement REPAIR_MODE until they know the failure format.

**Concrete example from methods.v1:** "Fix only the named issue" — but the prompt does not show how it will parse `VALIDATOR_OUTPUT_PATH` to know which span to fix.

### Gap 4: Reference-markdown not documented as output of pool build

**Flow:** citation_pool.v1 writes `pool.json` → orchestrator invokes `citation_pool.py format` (implied, not documented) → writes `references.md`, `bibliography.bib`, `citation_map.md` → discussion.v1 reads `REFERENCES_MD_PATH`.

**Problem:** The handoff from citation_pool.v1 to the format tool is not documented in any of the prompts or the LAYOUT. The citation_pool.v1 prompt says "Downstream (`citation_pool.py format`) will render it" but does not specify:
- Is this a separate tool invocation or part of the citation_pool.py script?
- What are the command-line flags?
- Does the orchestrator or the prompt invoke it?
- If formatting fails, does citation_pool.v1 need to re-run?

**Consequence:** A naive orchestrator will write `pool.json` and assume `references.md` exists; if it doesn't, discussion.v1 will fail with "file not found" on a perfectly valid pool.

### Gap 5: Figures copy/symlink logic is undefined

**Flow:** results.v1 is invoked with `FIGURES_INVENTORY_PATH` (from extract_figures.py) and `FIGURES_OUT_DIR` → results.v1 selects 4–8 figures → (implied) orchestrator copies or symlinks them into `FIGURES_OUT_DIR`.

**Problem:** The results.v1 prompt says "copy or symlink, paper-order named" but does not call any Bash tool to do this. The Output protocol does not mention file operations. This implies the orchestrator is responsible, but:
- Which operation (copy vs. symlink)?
- Does the orchestrator decide, or is it configurable?
- What if a figure cannot be read/copied?
- Are symlinks relative or absolute?

**Consequence:** The orchestrator must implement a figures-copy logic without guidance from the prompts.

## Orchestrator-Capability List

Based on the contract gaps above, the orchestrator must provide (none of which are documented in a central place):

1. **Extract-tool dispatcher** for `extract_methods.py` and `extract_figures.py`. Signature, invocation, error handling.
2. **Template loader and placeholder-filler** for `AI_DISCLOSURE_TEMPLATE` (from SPEC §10.1, not provided here) and `data_availability_template.md`.
3. **Validator invocation and result-parser** for `validate_manuscript.py`. Signature, output format, mapping failures to section prompts.
4. **Citation-pool formatter dispatcher** to invoke `citation_pool.py format` after the pool prompt completes.
5. **Sequential orchestration enforcer** to ensure Plan → Throughline → [Citation Pool & Methods in parallel or serial?] → Results → Discussion → Introduction → Abstract → Reframer, with clear error-handling at each step.
6. **Figures-copy logic** (copy vs. symlink, naming, error handling).
7. **Reframing-log initializer** if the log doesn't exist on first write.
8. **State-diff logic** (per LAYOUT, hash-diffs source artifacts on `continue` and reports new/changed files).

## Three Concrete Fixes

**Fix 1: Add an "Orchestrator Contract" section to LAYOUT.md**

Insert after the "CLI" section, before "Slash commands." Specify:
- Extract-tool invocation: "The orchestrator runs `python <BERIL_ROOT>/.claude/skills/beril-paper-writer/tools/extract_methods.py <PROJECT_ROOT> <NOTEBOOKS_DIR> <DRAFT_DIR>/methods_provenance.md` before invoking methods.v1. On failure, halt with the error verbatim."
- Template loading: "The orchestrator reads `<BERIL_ROOT>/.claude/skills/beril-paper-writer/reference/ai_disclosure_template.md` and `data_availability_template.md`, fills placeholders `{X.Y}`, `{model_id}`, `{project_id}`, `{sha}`, `{N}` from state.json, and passes the filled strings to the section prompts."
- Validator output: "validate_manuscript.py produces JSON with structure `{failures: [{validator: 'M3', span: {start_line, end_line}, text: '...'}, ...]}`. The orchestrator parses this and passes the JSON-stringified failure list as `VALIDATOR_OUTPUT_PATH`."

**Fix 2: Update citation_pool.v1's closing-message to name the formatter step**

Change:
```
pool.json written, N entries (cap M, mode {quick|standard|deep}, ...); categories covered: [...].
```

To:
```
pool.json written, N entries (cap M, mode {quick|standard|deep}, ...); categories covered: [...]. 
Note: Orchestrator must invoke citation_pool.py format before discussion.v1 reads references.md.
```

This makes the contract explicit to the reader.

**Fix 3: Add an escape hatch to reframer.v1 for missing reframing_log**

Change the escape-hatch section from:
```
- **`REFRAMING_LOG_PATH` missing or empty** → proceed; this just means no prior entries. Initialize the file with a `# Reframing Log` header on first write.
```

To:
```
- **`REFRAMING_LOG_PATH` missing** → read will fail. Orchestrator must ensure the file exists (empty but present, with a `# Reframing Log` header) before invoking reframer.v1. 
- **`REFRAMING_LOG_PATH` empty** → proceed; append new entries to the file.
```

This shifts the responsibility explicitly to the orchestrator and removes ambiguity.

