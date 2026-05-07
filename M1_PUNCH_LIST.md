# M1 Punch List — Phase 0 NEW tools (`discrepancy_register.py` + `claim_inventory.py`)

**Filed:** 2026-05-07
**Milestone:** M1 of v0.8.0 architectural redesign (per SPEC_v0_8 §17)
**Predecessor:** M0 spec sign-off (shipped 2026-05-07; D-034)
**Successor:** M2 holistic write + story builder
**Scope:** Two new Phase-0 tools + unit tests + smoke against `ibd_phage_targeting`. No orchestrator changes. No prompt changes. Independently testable.
**LOC target:** ~400 LOC + ~40 tests (per SPEC §17 / D-034 Q-rationale).
**Cost ceiling at smoke:** ≤$0.05 (discrepancy_register) + ≤$0.10 (claim_inventory) per run. If smoke spend on `ibd_phage_targeting` exceeds these, reopen the cost decision.

---

## Why a punch list

Per `feedback_punch_list_release_pattern.md`: when a milestone has 4+ patches between first-smoke and ship, structure as tiered AC + dep edges + smoke at every tier boundary. M1 is forecast to have ≥4 patches because (a) two new tools each with synthetic-fixture unit tests AND a real-project smoke, (b) the Q1 LLM-assist cost-justification ablation, (c) the Q2 ground-truth claim-completeness check, (d) cross-skill contract forward task. The pattern earns its keep.

---

## Tier A — `discrepancy_register.py` (the higher-risk new tool)

Higher-risk because it carries the Q1 cost trade-off — LLM-assisted classification was specifically chosen over pure string-match on synonym/paraphrase robustness grounds. If A1's smoke (in Tier C) shows the LLM catches nothing string-match would miss on `ibd_phage_targeting`, Q1 reopens.

### A1. Implement `src/beril_paper_writer/skill/tools/discrepancy_register.py`

**Spec:** SPEC_v0_8 §4.5.

**Inputs (CLI):**
- `--methods-provenance <path>` — required.
- `--research-plan <path>` — required.
- `--reframing-log <path>` — optional (passed through if a prior draft already produced one; not load-bearing for M1).
- `--output-dir <path>` — required; writes `discrepancy_register.md` and an audit JSONL line.
- `--no-llm` — debug flag; runs the deterministic pre-pass only, skips classification call. Used by ablation in Tier C.

**Output schema:** Markdown per SPEC §4.5, append-only across drafts. Each entry:
- `## D-NNN — type: {plan-prescribed-not-executed, executed-not-prescribed}`
- Plan §X quote (≤25 words, single-line).
- Execution citation (notebook ID + cell index, or "no notebook evidence").
- Severity: `load-bearing` / `cosmetic` / `unclear`.
- Recommendation: 1-line prose; downstream consumed by Phase 2 holistic prompt.

**Dependencies between sub-steps:** A1.a → A1.b → A1.c → A1.d.

#### A1.a Module skeleton + argparse + I/O contract

- Shebang + module docstring with refs to SPEC §4.5 + D-034 Q1.
- `argparse` follows the existing `extract_methods.py` template (positional usage rejected; flags only).
- Writes `discrepancy_register.md` to `<output-dir>/` and `<output-dir>/audit/phase0.jsonl` per §4.7.
- Audit JSONL fields: `timestamp`, `tool=discrepancy_register`, `version`, `inputs={methods_provenance: <sha256>, research_plan: <sha256>}`, `output_path`, `entry_count`, `cost_usd`, `exit_status`.
- Exit codes: `0` = success (any entry count, including zero); `2` = input parse error (RESEARCH_PLAN.md or methods_provenance.md unparseable); `3` = LLM call failure with `--no-llm` not set.

**AC:** `--help` works; running with `--no-llm` against a synthetic fixture produces a valid markdown output and an audit JSONL line.

#### A1.b Deterministic pre-pass: extract plan-prescribed analyses + extract executed analyses

- Plan side: parse `RESEARCH_PLAN.md` for analysis declarations. Heuristic — bullet items / numbered lists under headings matching `/analys[ei]s|method|test|stat/i`. Output: `[{plan_section, plan_quote, normalized_phrase}]`.
- Execution side: parse `methods_provenance.md` for the same. Already a structured artifact — easier. Output: `[{notebook_id, cell, normalized_phrase}]`.

**Normalization:** lowercase + remove stopwords + Porter stem. This is the input to both string-match (debug path) and the LLM (default path).

**Prior art:** `extract_methods.py` already parses `methods_provenance.md` for related concerns; reuse its parser if its public API supports the cell-level granularity we need. If not, build minimal parser; do not refactor `extract_methods.py` in M1 (out of scope).

**AC:** Given a synthetic plan with 5 analyses + a synthetic provenance with 4 (3 overlapping + 1 unprescribed), the pre-pass surfaces 6 candidates: 2 plan-only + 1 exec-only + 3 overlapping (the LLM step decides whether the 3 overlapping are paraphrase-equivalent or actual discrepancies). Test fixture lives at `tests/fixtures/m1/discrepancy_synthetic_001/`.

#### A1.c LLM classification pass (Q1 — the cost-justified call)

- Input to LLM: the candidate pairs from A1.b + the original quotes (un-normalized).
- Output from LLM: per-pair {`equivalent` | `paraphrase` | `discrepancy`} + a 1-sentence severity justification.
- Model: Haiku 4.5 (cost ceiling). Token budget: ≤2K input + ≤1K output (per SPEC §4.5 cost target $0.05).
- Prompt lives at `src/beril_paper_writer/skill/prompts/discrepancy_classify.v1.md`. Versioned; future-bumpable.

**Anti-fabrication discipline (per `feedback_llm_arithmetic_unreliable.md` and `feedback_prompt_tool_contract_drift.md`):**
- Prompt MUST require LLM to quote the exact plan/execution string from the provided candidate, not paraphrase. Validator in A1.d rejects entries whose quoted string is not a substring of the input.
- Prompt MUST forbid invention of severity beyond the three-value enum. Validator rejects out-of-enum.

**JSON parsing (per `feedback_llm_json_unfixable_in_parser.md` + `feedback_llm_json_trailing_commas_repairable.md`):**
- Strict `json.loads` first; on `JSONDecodeError`, regex-strip trailing commas; re-try; on second failure raise the original error with diagnostic hint pointing at the suspect token.
- Prompt MUST include the unescaped-quote anti-pattern + 4 escape alternatives, copied from `beril-adversarial`'s presentation.v2 / paper.v2 prompts.

**AC:** Synthetic fixture with 1 paraphrase-equivalent pair (e.g. plan says "Welch's t-test", exec says "two-sample t-test with unequal variances") + 1 actual discrepancy (plan says "Welch's t-test", exec says "Mann-Whitney U") + 1 trivial overlap (both say "PCA"). LLM correctly labels: equivalent / discrepancy / equivalent. The paraphrase case is the **Q1 justification** — string-match would miss it.

#### A1.d Validator + audit emission + idempotency

- Validator runs after LLM: rejects out-of-enum severity, out-of-enum type, quotes that don't substring the input. On rejection, exit code 4 with structured error.
- Idempotency: input-hash-keyed cache at `<output-dir>/audit/discrepancy_cache.json`. On rerun with unchanged input hashes, skip LLM call and re-emit identical output. Bumping the prompt version invalidates the cache (cache key includes prompt SHA).

**AC:** `pytest -k discrepancy and idempotent` passes — tool runs twice, second run makes zero LLM calls, output bytes identical.

### A2. Unit tests for `discrepancy_register.py`

Land at `tests/unit/test_discrepancy_register.py`. Target ~20 tests.

- A2.a Pre-pass parsing: 3 tests (plan-only candidate, exec-only candidate, both-sides candidate).
- A2.b Normalization: 2 tests (stopword removal + stem-equivalence).
- A2.c LLM contract: 4 tests (equivalent label, paraphrase label, discrepancy label, malformed JSON repair).
- A2.d Validator: 4 tests (out-of-enum severity, out-of-enum type, non-substring quote, valid pass-through).
- A2.e Idempotency: 2 tests (identical-input rerun is byte-stable; prompt-bump invalidates).
- A2.f I/O contract: 3 tests (audit JSONL emitted, exit codes correct, --no-llm path bypasses LLM).
- A2.g Render: 2 tests (markdown output is well-formed; D-NNN numbering increments correctly across reruns with new findings).

**AC:** `pytest tests/unit/test_discrepancy_register.py -v` shows 20/20 passing. No LLM calls in tests (use `respx` or equivalent to mock; pattern from existing `tests/unit/test_citation_pool.py` if available).

---

## Tier B — `claim_inventory.py` (lower-risk, ground-truth-validated tool)

Lower-risk than Tier A because there's no contested cost decision; the work is mostly deterministic regex extraction over `REPORT.md` with a small LLM step for ambiguous demarcation. Risk is **completeness**: false negatives are the killer (Q2 watch-for from M0 memory).

### B1. Implement `src/beril_paper_writer/skill/tools/claim_inventory.py`

**Spec:** SPEC_v0_8 §4.6.

**Inputs (CLI):**
- `--report <path>` — required (REPORT.md).
- `--methods-provenance <path>` — required (so each numeric claim links to a notebook cell).
- `--figures-inventory <path>` — required.
- `--tables-inventory <path>` — required.
- `--output-dir <path>` — required.
- `--no-llm` — debug; runs deterministic-only mode, marks ambiguous demarcations as `unresolved`.

**Output:** TSV per SPEC §4.6 schema (claim_id, claim_text, source_notebook, source_cell, figure_or_table, effect_size_present, ci_present, pvalue_present, notes). Plus an audit JSONL entry.

**Prior art:** v0.7.x M7 validator (`tools/check_numerical_claims.py` if present, or whatever the current numerical-claim regex lives in). The regex catalog from M7 is the foundation for B1.b's deterministic extraction. **Read it first; don't re-derive.**

#### B1.a Module skeleton + argparse + I/O contract

Same template as A1.a. Audit JSONL: `tool=claim_inventory`, `inventory_size`, `unresolved_count`, `cost_usd`.

**AC:** `--help` works; `--no-llm` against a synthetic REPORT.md fragment produces a TSV and an audit line.

#### B1.b Deterministic regex extraction over REPORT.md

Catalog of numeric assertion patterns (reuse from M7 if extant):
- Percentages: `\b\d+(?:\.\d+)?%`
- Ratios with units: `\b\d+(?:\.\d+)?\s*(?:mg/L|µM|nM|kb|bp|kDa|fold|×|x)\b`
- p-values: `[pP]\s*[<=]\s*0\.\d+` and `p\s*=\s*\d+\.\d+e-?\d+`
- Confidence intervals: `95%\s*CI[:,]?\s*\[?[\d\.\-,\s]+\]?`
- N-counts: `\bn\s*=\s*\d+\b` (word-boundary critical to avoid PCA-component "n=2").
- AUC / R² / etc.: `\b(?:AUC|R\^?2|RMSE|MAE)\s*[=:]\s*\d+\.\d+`

Each match emits a candidate claim. Sentence-context window: ±1 sentence around the match (use sentence segmentation, not naive `\n\n` split, because REPORT.md has narrative paragraphs).

**AC:** Synthetic REPORT.md fragment with 8 numerics across 5 sentences yields 8 candidate claim rows. Test fixture at `tests/fixtures/m1/claim_inventory_synthetic_001/`.

#### B1.c LLM demarcation pass (small, ~3–5K tokens per SPEC §4.6)

For ambiguous cases — multiple numerics in one sentence, or a numeric whose semantic claim spans 2 sentences — call Haiku 4.5 to demarcate the claim text and assign the source notebook/cell from the provided `methods_provenance.md`.

**Anti-fabrication discipline:**
- LLM MUST cite a notebook ID + cell index that EXISTS in `methods_provenance.md`. Validator rejects fabricated cell references.
- LLM MUST quote the source sentence verbatim from REPORT.md. Validator rejects non-substring claim_text.

**Cost ceiling per call:** $0.10/run on `ibd_phage_targeting` (~40–80 claim_ids expected). If exceeded, log warning + halt; do not silently overrun.

**AC:** Synthetic fixture with 2 multi-numeric sentences (e.g. "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343 conditions") yields 3 distinct claim_ids: AUC value, CI bounds, sample size. Each links to the correct synthetic notebook cell.

#### B1.d Validator + idempotency

Same pattern as A1.d. Cache key includes `--report` SHA + `--methods-provenance` SHA + prompt SHA.

**AC:** Rerun is byte-stable; cache invalidates on input change.

### B2. Unit tests for `claim_inventory.py`

Land at `tests/unit/test_claim_inventory.py`. Target ~18 tests.

- B2.a Regex extraction: 6 tests (one per pattern class — percentages, ratios, p-values, CIs, n-counts, metrics).
- B2.b Sentence segmentation: 2 tests (period-not-in-decimal, paragraph-break).
- B2.c LLM demarcation: 3 tests (multi-numeric sentence split, span-2-sentences merge, ambiguous-pronoun resolution).
- B2.d Validator: 3 tests (fabricated cell rejected, non-substring claim_text rejected, valid pass-through).
- B2.e Idempotency: 2 tests (rerun byte-stable, input-change invalidates).
- B2.f I/O contract: 2 tests (TSV well-formed; flag presence/absence flags correct).

**AC:** `pytest tests/unit/test_claim_inventory.py -v` shows 18/18 passing.

---

## Tier C — Smoke against `ibd_phage_targeting`

This is the milestone gate. Per SPEC §17 M1: "smoke against ibd_phage_targeting." Per `feedback_punch_list_release_pattern.md`: smoke at every tier boundary; ship-blocker on failure.

### C0. Prerequisite: pre-run existing v0.7.x extractors against `ibd_phage_targeting`

**The smoke target's provenance lives in raw artifacts, not in pre-existing markdowns.** Confirmed 2026-05-07 with Adam: every paper-writer project is guaranteed only `README.md` + `RESEARCH_PLAN.md` + `REPORT.md` at root. The true provenance is in the project's notebooks, scripts, and produced data. `methods_provenance.md`, `figures_inventory.md`, `tables_inventory.md` are tier-1 derivatives generated by v0.7.x's `extract_methods.py` / `extract_figures.py` / `extract_tables.py` against those raw artifacts. `ibd_phage_targeting/` is normal in lacking the markdowns; C0 is general project setup, not a smoke-target peculiarity.

**M1 smoke can't fire without them.** C0 is unavoidable plumbing for any project; it is not in the M1 LOC target because it's existing v0.7.x tooling. **Note for M2+:** the holistic prompt grounds against these extracted markdowns. They are tier-1 derivatives of the raw notebook/script/data provenance — if the extractors emit garbage, every downstream phase grounds on garbage. Extractor regressions are load-bearing for the whole pipeline; treat them with the same gravity as a holistic-prompt regression.

**Manifest TSVs are NOT phase_extract artifacts.** Confirmed 2026-05-07 by reading the actual `main()` of `extract_figures.py` / `extract_tables.py` (and grep across the tools tree): `figures_manifest.tsv` and `tables_manifest.tsv` are emitted by the `results.v1` LLM prompt during the writing pipeline (per `prompts/results.v1.md` §"Emit `figures_manifest.tsv` alongside the figure copies…"). They encode `paper_order_n`, which is throughline-driven and cannot honestly exist before figure-selection runs. The extractors produce only the markdown inventories. M1's downstream consumers (`discrepancy_register.py` §A1, `claim_inventory.py` §B1) take only `methods_provenance.md` + `figures_inventory.md` + `tables_inventory.md`; no manifest is on the M1 input contract. §C0.c/§C0.d earlier draft erroneously listed the manifests; they have been removed.

C0.a Create `spike/beril-extended/projects/ibd_phage_targeting/papers/draft_1/` (per §4.7 — Phase-0 artifacts live at `papers/draft_N/`, not project root).
C0.b Run `extract_methods.py` against the project's notebooks → `papers/draft_1/methods_provenance.md`.
C0.c Run `extract_figures.py` → `papers/draft_1/figures_inventory.md`.
C0.d Run `extract_tables.py` → `papers/draft_1/tables_inventory.md`.

**AC:** All three markdown artifacts exist at `papers/draft_1/`. If any extractor fails on `ibd_phage_targeting`, apply the triage rubric below; don't let extractor failure get silently attributed to M1.

**Discipline:** per `feedback_no_git_writes_in_sandbox.md` + `feedback_sandbox_bash_vs_intermediate_checks.md`, the C0 invocations are runbook commands for Adam's Mac shell, not sandbox bash. The punch list documents the commands; Adam runs and reports back.

#### C0 — Mac-shell runbook (Adam runs)

Per `feedback_pipx_venv_python_for_skill_helpers.md`, invoke the extractors via the pipx venv's Python, not bare `python3` (macOS Homebrew system python3 is PEP 668 locked + missing `nbformat`). The recipe below mirrors `paper_writer.sh::discover_python_bin`.

Paths assume Adam's checkout is at `~/Documents/Claude/Projects/research-coscientist-dev`. Adjust `WORKSPACE` if that differs.

```bash
# --- one-time setup for this shell session -------------------------------
WORKSPACE=~/Documents/Claude/Projects/research-coscientist-dev
PROJECT_ROOT="$WORKSPACE/spike/beril-extended/projects/ibd_phage_targeting"
DRAFT_DIR="$PROJECT_ROOT/papers/draft_1"

# Locate the pipx venv's python (the one with nbformat + python-docx).
# Approach 1: read the shebang from the installed CLI (matches discover_python_bin).
PYTHON_BIN="$(awk 'NR==1 && /^#!/ {sub(/^#!/, ""); split($0, a, " "); print a[1]; exit}' "$(command -v beril-paper-writer)")"
# Sanity check — should print a path under ~/.local/pipx/venvs/.../bin/python
echo "PYTHON_BIN=$PYTHON_BIN"
"$PYTHON_BIN" -c "import nbformat, docx; print('deps OK')"

# Source-tree path to the extractor scripts. Reliable for a one-off
# punch-list run; avoids depending on pipx layout heuristics. The
# pipx venv's $PYTHON_BIN can execute scripts from any path because
# the package's runtime deps (nbformat, python-docx) are on its
# sys.path regardless of the script location.
TOOLS_DIR="$WORKSPACE/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools"
ls -la "$TOOLS_DIR/extract_methods.py" "$TOOLS_DIR/extract_figures.py" "$TOOLS_DIR/extract_tables.py"

# --- C0.a: create the per-draft directory --------------------------------
mkdir -p "$DRAFT_DIR/audit"

# --- C0.b: extract_methods → methods_provenance.md -----------------------
"$PYTHON_BIN" "$TOOLS_DIR/extract_methods.py" "$PROJECT_ROOT" \
    --output-dir "$DRAFT_DIR" > /dev/null 2> "$DRAFT_DIR/audit/extract_methods.log"
echo "exit=$?  methods_provenance.md: $(wc -l < "$DRAFT_DIR/methods_provenance.md") lines"

# --- C0.c: extract_figures → figures_inventory.md ------------------------
"$PYTHON_BIN" "$TOOLS_DIR/extract_figures.py" "$PROJECT_ROOT" \
    --output-dir "$DRAFT_DIR" > /dev/null 2> "$DRAFT_DIR/audit/extract_figures.log"
echo "exit=$?  figures_inventory.md: $(wc -l < "$DRAFT_DIR/figures_inventory.md") lines"

# --- C0.d: extract_tables → tables_inventory.md --------------------------
"$PYTHON_BIN" "$TOOLS_DIR/extract_tables.py" "$PROJECT_ROOT" \
    --output-dir "$DRAFT_DIR" > /dev/null 2> "$DRAFT_DIR/audit/extract_tables.log"
echo "exit=$?  tables_inventory.md: $(wc -l < "$DRAFT_DIR/tables_inventory.md") lines"

# --- post-flight: confirm the three artifacts exist ----------------------
ls -la "$DRAFT_DIR/methods_provenance.md" \
       "$DRAFT_DIR/figures_inventory.md" \
       "$DRAFT_DIR/tables_inventory.md"
```

CLI surface notes (verified by reading source):
- All three extractors take `project_dir` as a single positional arg + `--output-dir <dir>` as an opt-in markdown writer. They always emit JSON to stdout (redirected to `/dev/null` above; flip if you want to inspect).
- `extract_methods.py` exits 1 if `<project_root>` lacks any `.ipynb` under `notebooks/` / root / `src/` / `analysis/` (this is the "no notebooks" hard halt; treat as a project-specific failure under the triage rubric below). `extract_figures.py` and `extract_tables.py` exit 0 even on empty inventories — they're not load-bearing in v0.7.x's `phase_extract` (orchestrator only `log_warn`s).

#### C0 — Triage rubric for failures

Apply per-extractor; rubric distinguishes M1-attributable vs not.

| Symptom | Class | Action |
|---|---|---|
| `extract_methods.py` exits 1 with "no notebooks found" | **Project-specific.** `ibd_phage_targeting` lacks the expected layout. | Verify `$PROJECT_ROOT/notebooks/*.ipynb` exists; if it doesn't, escalate as Q-style sign-off: "is `ibd_phage_targeting` ready for M1's smoke target, or do we re-pick?" Do NOT file as M1 work. |
| `extract_*.py` raises `ImportError: nbformat` | **Environment.** `$PYTHON_BIN` is not the pipx venv's Python. | Re-run the `PYTHON_BIN=...` shebang trick; verify `"$PYTHON_BIN" -c "import nbformat"` succeeds. Not an M1 issue. |
| `extract_methods.py` writes `methods_provenance.md` but `## Statistical Tests Detected` is empty when notebooks visibly use `scipy.stats.*` | **v0.7.x extractor regression.** AST walker or `_TEST_NAME_MAP` is broken. | File in v0.7.x's tracker (e.g. `RELEASE_NOTES_v0_7.md` or a new issue on the paper-writer repo). Hand off; do NOT block M1 close-out — proceed against whatever inventory was emitted, then re-run after the v0.7.x patch. |
| `extract_figures.py` writes `figures_inventory.md` with 0 figures despite `figures/` directory containing `.png` files | **v0.7.x extractor regression.** `find_figure_files` or `find_figures_dirs` candidate path missing the project's layout. | Same as above — file in v0.7.x. M1 can proceed if a non-zero subset of figures was inventoried; re-run after patch. |
| `extract_tables.py` writes `tables_inventory.md` with 0 tables despite `REPORT.md` containing markdown tables | **v0.7.x extractor regression.** Markdown-table parser. | Same. M1 can still attempt §A1/§A2 (no tables-dependent input). §B1's `claim_inventory.py` will produce a thinner TSV; flag in §B2's recall ground-truth check. |
| Any extractor traceback | Read the traceback first. | If it points at the extractor's own code → v0.7.x regression. If it points at malformed project content (e.g. corrupt `.ipynb`) → project-specific; escalate. |
| All three extractors succeed but `figures_inventory.md` missing the `<!-- inventory_schema_version: 2 -->` header comment | **v0.7.x format regression** on a path that v0.4 added (Phase 1b/2 schema v2). | File in v0.7.x; M1's `claim_inventory.py` doesn't depend on the v2 header so this is a degraded but tolerable input. |

**Default disposition when in doubt:** if the extractor exits 0 and produces a non-empty markdown file, M1 proceeds. Quality concerns (under-extraction, missing schema fields) get filed against v0.7.x and re-run later — don't block M1 plumbing on extractor-quality issues that aren't in M1's scope.

### C1. Smoke `discrepancy_register.py` end-to-end

C1.a Land `tests/smoke/m1_discrepancy_smoke.py` — runs `discrepancy_register.py` against `papers/draft_1/methods_provenance.md` + project-root `RESEARCH_PLAN.md`. Asserts: register file exists, ≥1 entry, schema-valid markdown, audit JSONL emitted with cost ≤$0.05.

C1.b **Q1 cost-justification ablation (the watch-for).** Run twice on same inputs:
  - Once with default LLM-assist path → record entries E_llm.
  - Once with `--no-llm` → record entries E_strmatch.
  - Compute `delta = E_llm \ E_strmatch` (entries the LLM caught that string-match did not).
  - **Gate:** if `delta` is empty AND `ibd_phage_targeting` has any plan-vs-execution paraphrase pairs we can identify by hand, the Q1 cost decision is suspect and gets reopened in DECISIONS.md as a re-evaluation note. If `delta` is non-empty, the cost is justified for this project and we move on.
  - **What "by hand" means:** before running the ablation, do a 30-min manual scan of `RESEARCH_PLAN.md` vs `methods_provenance.md` for known paraphrase pairs. Document them in `M1_PUNCH_LIST_ablation_notes.md`. The ablation is honest only if we know what the right answer is.

**AC:** Smoke passes; ablation report committed; if Q1 reopens, file `D-035` (or next available) capturing the reconsideration before tagging M1.

### C2. Smoke `claim_inventory.py` end-to-end

C2.a Land `tests/smoke/m1_claim_inventory_smoke.py` — runs `claim_inventory.py` against `papers/draft_1/`. Asserts: TSV exists, ≥30 claim_ids per the M0 memory entry's smoke gate, every `source_notebook` resolves to a real notebook in the project, every `source_cell` resolves to a cell in that notebook, audit JSONL emitted with cost ≤$0.10.

C2.b **Q2 ground-truth completeness check (the watch-for).** Manual hand-count of numerics in `REPORT.md`:
  - Adam (or Claude as a parallel agent) reads `REPORT.md` and produces a hand-list of every numeric assertion before running the inventory. Document at `M1_PUNCH_LIST_claim_groundtruth.md`.
  - Compare hand-list vs `claim_inventory.tsv`. Compute precision (false-positive rate) + recall (false-negative rate).
  - **Gate:** recall ≥0.90 (≥90% of hand-counted numerics make it into the inventory). False negatives are the killer per Q2 — the holistic prompt grounds against this TSV; missing numerics mean the holistic write can fabricate them with no upstream check.
  - If recall <0.90, identify the missed pattern class, extend B1.b's regex catalog, rerun. Iterate until recall passes the gate.

**AC:** Smoke passes; recall ≥0.90; ground-truth doc committed.

### C3. Idempotency smoke (combined)

C3.a Rerun both tools twice in succession; assert audit JSONLs show 0 LLM calls on second run AND output bytes are identical (per A1.d / B1.d AC, but on the real project, not synthetic fixtures).

**AC:** Combined idempotency smoke passes.

---

## Tier D — Forward-looking cross-skill contract task

Per `feedback_cross_skill_contract_drift.md` + SPEC §18: any new per-draft artifact that beril-adversarial may eventually consume needs a forward-looking task on beril-adversarial's CONTRACT.md BEFORE the producer milestone tags. Both `discrepancy_register.md` and `claim_inventory.tsv` are NEW per-draft artifacts.

### D1. File task in beril-adversarial repo

Beril-adversarial's paper reviewer (v0.7.x) currently consumes `papers/draft_N/draft_N.md` + the per-draft layout from paper-writer v0.7+. The new artifacts at `papers/draft_N/discrepancy_register.md` + `papers/draft_N/claim_inventory.tsv` may be valuable for v3 paper-review classes — e.g. an `unbacked_quantitative` finding could cite the claim_inventory entry that's missing CI/p-value, instead of re-extracting the numeric from the draft.

D1.a Open issue / task in `ArkinLaboratory/beril-adversarial` titled "Extend CONTRACT.md to declare optional consumption of paper-writer v0.8.0 Phase-0 artifacts." Body: link to SPEC_v0_8 §4.5/§4.6, note that consumption is optional in v0.7.x of beril-adversarial (paper-writer v0.7.x doesn't emit these), and ask whether v0.8.0 of paper-writer should declare these artifacts as part of its CONTRACT.md surface for forward visibility.

D1.b Update `spike/beril-paper-writer-skill-draft/CONTRACT.md` to add a "Phase-0 artifacts emitted in v0.8.0" section listing the two new files and their schemas. Note in the section: "Optional consumption by sister skills; not yet wired into beril-adversarial v0.7.x. See [link to D1.a issue]."

**AC:** D1.a issue filed and linked from D1.b CONTRACT.md edit. No code changes in beril-adversarial; the task is documentation-forward only. Discovery for v0.8.x or v0.9.x of beril-adversarial when the wiring lands.

---

## Tier E — Close-out

### E1. DECISIONS.md entry

E1.a If C1.b's ablation reopens Q1 → file D-035 with the reconsideration. Otherwise, no new D-NNN entry needed for M1 (M1 is implementation of decisions already locked in D-034).

E1.b If C2.b drives a non-trivial extension to the regex catalog (e.g. a pattern class we didn't anticipate), file D-036 documenting the catalog change with rationale.

### E2. Memory entry

Per SPEC §18: write `project_paper_writer_v0_8_m1.md` summarizing what shipped, what the Q1/Q2 watch-fors revealed, gotchas, and the cross-skill task filed. Index entry in `MEMORY.md` under "Active work — Augmentation stream."

### E3. Tag + commit

Per `feedback_no_git_writes_in_sandbox.md`: stage commit message at `.commit-message-m1.txt`; Adam runs `git commit -F` from his Mac shell. Tag: none — M1 is intra-v0.8.0 progress, not a release. Tagging waits for M8 cut-over.

### E4. M2 unblock

M2 starts when E1 + E2 + E3 are done AND Tier C smokes pass. M2's prerequisite from M1: `papers/draft_1/discrepancy_register.md` + `papers/draft_1/claim_inventory.tsv` exist on `ibd_phage_targeting` and feed the holistic prompt's grounding context.

---

## Dep graph

```
A1.a ─→ A1.b ─→ A1.c ─→ A1.d ─→ A2 ───┐
                                       ├─→ C1 ─→ E
B1.a ─→ B1.b ─→ B1.c ─→ B1.d ─→ B2 ───┤
                                       ├─→ C2 ─→ E
                  C0 (prereq) ─────────┤
                                       └─→ C3 ─→ E
                                                 │
                                       D1 ───────┘  (parallel; not gating on C)
```

C0 blocks C1 + C2 + C3 (but not Tier A or B unit tests, which use synthetic fixtures).
A2 blocks C1 (smoke needs the unit-tested code).
B2 blocks C2.
D1 is independent of A/B/C; can land anytime in M1, gates on tag-readiness.

**Parallelism note (Adam 2026-05-07):** The strict A1.a→b→c→d / B1.a→b→c→d chains are deliberately conservative for panel-of-one execution. A1.c (LLM classifier) and A1.d (validator) can be developed in parallel via mocked outputs at each interface; same for B1.c / B1.d. Track this lever for any subsequent milestone with similar shape (M3's tier cascade is the next candidate — Tier-1 deterministic checks and Tier-2 light reviewer have an obvious mock-and-parallelize seam).

---

## Watch-fors carried into M2

- If C1.b's ablation shows the LLM call value depends heavily on project (`ibd_phage_targeting` may be too clean to surface paraphrase pairs), revisit on `functional_dark_matter` during M7 A/B sanity check.
- If C2.b's regex catalog needs extension, M2's holistic prompt should explicitly reference the new pattern classes so the writer knows what's grounding-protected.
- The two new artifacts join `papers/draft_N/`'s already-busy layout. M2's holistic prompt should treat `discrepancy_register.md` as a ground-truth input (not editable) and `claim_inventory.tsv` as a constraint table (every numeric in the draft references a claim_id). Failure to do either is an M2 ship-blocker, not an M1 issue.

---

## Discipline notes (pre-flight reminders for the implementer)

- `feedback_no_git_writes_in_sandbox.md` — no git ops in sandbox bash. C0/E3 are Mac-shell runbooks.
- `feedback_sandbox_bash_vs_intermediate_checks.md` — pytest in sandbox is fine; pipx + env-dependent smokes go to Adam's shell.
- `feedback_verify_cli_before_recommending.md` — before telling Adam to run any subcommand, run `--help` or read the cli.py first.
- `feedback_help_text_no_magic_line_numbers.md` — A1.a / B1.a usage text should use awk-with-sentinel, not `sed -n 'N,Mp'`.
- `feedback_named_columns_in_inserts.md` — n/a here (no SQL), but B1's TSV header MUST be self-describing; downstream consumers parse by header name not position.
- `feedback_pipx_venv_python_for_skill_helpers.md` — if any bash orchestration emerges, discover Python via `which beril-paper-writer` shebang.
- `feedback_bash_to_argparse_use_json_files.md` — n/a here (pure Python CLI), but if smoke wraps multiple commands in bash, pass JSON files not eval+flags.
- `feedback_llm_arithmetic_unreliable.md` — applies to any future summary/count emitted by the LLM in A1.c / B1.c. Currently neither emits a count; if that changes, post-correct deterministically.
- `feedback_llm_json_unfixable_in_parser.md` + `feedback_llm_json_trailing_commas_repairable.md` — A1.c and B1.c JSON parse sites need both the prompt-level anti-pattern AND the trailing-comma repair pre-flight. Don't ship without both.
- `feedback_render_test_must_evaluate_fstring.md` — A1's markdown emitter and B1's TSV emitter must have render tests that evaluate the actual format string against synthetic data, not just grep the source.
- `feedback_prompt_tool_contract_drift.md` — A1.c / B1.c prompts MUST not invent path conventions or schemas not materialized by the deterministic pre-pass. Validator (A1.d / B1.d) is the post-check that catches drift.
- `feedback_cross_skill_contract_drift.md` — Tier D is the explicit application.

---

*M1 is unblocked on M0 sign-off (D-034). M2 is unblocked on M1 close-out (E4). Smoke gates at Tier C are ship-blockers; any failure that exceeds 4 patches resets this punch list to v2.*
