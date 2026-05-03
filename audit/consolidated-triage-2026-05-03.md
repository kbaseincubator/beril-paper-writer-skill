# Consolidated Triage: beril-paper-writer-skill v0.6.4 → v1.0

**Date:** 2026-05-03
**Source reviews:** code-review, docs-review, prompt-review, repo-design-review (all 2026-05-03)
**Method:** 4 independent memoryless adversarial reviewers; findings cross-referenced for convergence, filtered for false positives, ordered by impact.

---

## How to read this

Findings are grouped into 5 tiers by action urgency:

- **Tier A — Real bugs, fix now** (before next tag)
- **Tier B — Structural debt, fix before v1.0 handoff** (engineering weeks)
- **Tier C — Prompt/design improvements, fix before v1.0** (prompt-edit hours)
- **Tier D — Documentation debt, fix before v1.0** (writing hours)
- **Tier E — Aspirational / post-v1.0** (nice-to-have)

Within each tier, items are ordered by estimated impact. Cross-reviewer convergence is noted where multiple reviewers independently flagged the same issue — these carry higher confidence.

**Noise/false positives** are listed at the end with rationale for dismissal.

---

## Tier A — Real Bugs (fix before next tag)

### A1. Null-coalescing in cost/elapsed accumulators
**Source:** code-review Critical #1
**File:** `paper_writer_helpers.py` lines 301, 304
**Bug:** `state.get("cost_so_far_usd", 0.0)` returns `None` (not `0.0`) when the key exists with a JSON null value. TypeError on `float(None)`.
**Impact:** Silent crash mid-run; cost audit trail lost.
**Fix:** `float((state.get("cost_so_far_usd") or 0.0))` — the `(x or default)` pattern. 2 lines.
**Effort:** 10 minutes + 2 unit tests.
**Confidence:** HIGH — this is the exact pattern from `feedback_dict_get_default_vs_null.md`; has bitten us before (v0.1.6 hub crash).

### A2. Uncaught JSONDecodeError in emit_next_actions
**Source:** code-review Important #5
**File:** `paper_writer_helpers.py` lines 654-661
**Bug:** `json.loads(validation_path.read_text())` has no try-except. Truncated or corrupted validation.json crashes the next-actions emitter, which runs after every phase.
**Impact:** User loses the "what to do next" summary on any validation-file corruption.
**Fix:** Wrap in try-except JSONDecodeError, emit a warning line instead of crashing. ~10 lines.
**Effort:** 15 minutes + 1 test.
**Confidence:** HIGH — deterministic failure on malformed JSON.

### A3. Silent error in _write_throughline_verbatim on malformed header
**Source:** code-review Important #9
**File:** `continue_run.py` lines 171-199
**Bug:** If throughline candidate header lacks a colon (`## Candidate TL2` instead of `## Candidate TL2: Title`), the function writes a throughline with an empty statement. Pipeline continues with blank throughline.
**Impact:** Entire manuscript drafted against an empty throughline statement. All downstream sections are ungrounded.
**Fix:** Validate colon presence + non-empty title; raise ValueError if malformed. ~5 lines.
**Effort:** 15 minutes + 2 tests.
**Confidence:** HIGH — the header format varies across LLM runs (known from `feedback_prompt_output_shape_drift.md`).

### A4. Review substance check — extend to reframing parser
**Source:** code-review Critical #3 (partial)
**File:** `paper_writer_helpers.py` `_parse_reframing_log`
**Bug:** Multi-line field values where a continuation line lacks a leading `- ` are silently dropped. The field only accumulates the first line.
**Impact:** Reframing entries with wrapped text lose content; repair dispatch gets incomplete instructions.
**Fix:** In the parser's line-processing loop, treat non-bullet lines following a field as continuation text. ~8 lines.
**Effort:** 20 minutes + 3 tests. (The v0.6.4 test suite covers the happy path; need edge-case tests for wrapped text.)
**Confidence:** MEDIUM — depends on whether reframing_log.md entries actually wrap lines in practice. Worth fixing defensively.

### A5. check_repair_status returns "pass" for non-failing validators
**Source:** code-review Important #7
**File:** `paper_writer_helpers.py` lines 1233-1258
**Bug:** `cmd_check_repair_status` returns the validator's current status regardless. If a validator already passes and the orchestrator calls this function, it gets "pass" — and the REPAIR_MODE loop thinks repair succeeded when it was never needed.
**Impact:** Wasted repair cycles or incorrect skip of actual failures.
**Fix:** Return "unknown" if status != "fail". ~3 lines.
**Effort:** 10 minutes + 1 test.
**Confidence:** MEDIUM — requires the orchestrator to call check_repair_status on a passing validator, which is unlikely in the current flow but possible after code changes.

---

## Tier B — Structural Debt (fix before v1.0 handoff)

These are not bugs but architectural risks that will bite the professional team. Ordered by probability × impact.

### B1. Cross-skill contract documentation ⟵ CONVERGENCE: 3 reviewers
**Sources:** repo-design C-003, docs-review Critical #3 (missing CONTRACT.md), code-review (implicit in version-negotiation gap)
**Problem:** Paper-writer shells out to `beril-adversarial review --type paper` expecting `adversarial-review-paper.v2` JSON. No vendored schema copy, no version check at runtime, no backwards-compat layer. When adversarial ships v0.7, paper-writer breaks silently.
**Evidence:** This exact pattern has bitten us twice in May 2026 (presentation-maker v0.3.1 broke adversarial's read paths; adversarial v0.5.2 had to adapt).
**Fix:**
1. Create `CONTRACT.md` in paper-writer documenting the schema it expects (~1 hour)
2. Add `beril-adversarial review --version` check in paper_writer.sh (~30 min)
3. Add cross-skill integration test (`tests/integration/test_paper_adversarial_roundtrip.py`) (~4 hours)
**Effort:** 1 day.
**Priority:** HIGHEST in this tier — the failure mode is deterministic and the fix is bounded.

### B2. State schema unversioned ⟵ CONVERGENCE: 2 reviewers
**Sources:** repo-design C-002 (state machine coupling), code-review Critical #4 (race condition in lock)
**Problem:** state.json has no `state_version` field. Old v0.5 drafts cannot be distinguished from v0.6 drafts. If v0.7 adds a phase, old drafts loaded in v0.7 may corrupt. Additionally, phase names are magic strings scattered across 3 files (paper_writer.sh, helpers.py, continue_run.py).
**Fix (minimal):**
1. Add `state_schema_version: "0.6"` field to state.json initialization (~15 min)
2. Add migration check on resume: if version < current, emit warning (~30 min)
3. Centralize phase names as constants in state.py (~1 hour)
**Fix (full, per repo-design recommendation):** phase_log.jsonl append-only log. This is better architecture but higher effort (1-2 sprint days). Recommend the minimal fix for v1.0; full fix for v1.1.
**Effort:** Minimal: 2 hours. Full: 1-2 days.

### B3. God module refactor (helpers.py) ⟵ CONVERGENCE: 2 reviewers
**Sources:** repo-design C-001 (4,147 lines, 25+ functions), code-review (implicit in testing gaps)
**Problem:** paper_writer_helpers.py does citation verification, manuscript validation, state machine logic, figure/table processing, caption gates, JSON mutation, CLI parsing — all in a flat namespace. Testability ceiling (~30% covered); onboarding friction for professional team.
**Assessment:** The repo-design reviewer's recommendation (extract into ManuscriptValidator / PhaseOrchestrator / LLMGate classes) is sound but the 2-3 sprint day estimate is optimistic for a refactor of this scope without regression risk. The file grew organically through 6 point releases; each function has implicit coupling to bash-script conventions.
**Recommendation:** Do NOT attempt a full refactor before v1.0. Instead:
1. Extract the 3 most independent subsystems as separate modules: `validators.py` (M1-M10), `caption_gates.py` (sufficiency + synthesis), `reframing_parser.py` (the v0.6.4 parser). ~1 day each.
2. Leave the remaining helpers.py as the "orchestration shim" with clear imports from the new modules.
3. Full class hierarchy refactor is a v1.1 task for the professional team with proper test coverage tooling.
**Effort:** 3 days (3 module extractions at 1 day each).

### B4. Pipe exit-code masking in invoke_claude
**Source:** code-review Important #6
**File:** `paper_writer.sh` lines 309-324
**Problem:** When piping `claude -p` through `stream_progress.py`, bash reports stream_progress's exit code, not claude's. If stream_progress crashes (disk full, OOM), the orchestrator treats it as "permanent failure" and deletes the file — even though claude successfully wrote it.
**Fix:** Capture both exit codes separately (bash `PIPESTATUS` array or background-process pattern).
**Effort:** 30 minutes.

### B5. Path traversal in assemble_docx image rendering
**Source:** code-review Important #8
**File:** `assemble_docx.py` lines 352-368
**Problem:** Image-path validation checks for `..` in parts but calls `.resolve()` after, which normalizes the traversal. A malicious markdown source could embed `figures/../../../etc/passwd`.
**Practical risk:** LOW — the markdown source is LLM-generated from project artifacts, not user-supplied. But defense-in-depth matters for v1.0.
**Fix:** Resolve path first, then check `full_path.relative_to(base_dir)`. 4 lines.
**Effort:** 15 minutes + 1 test.

### B6. Lock acquisition race condition
**Source:** code-review Critical #4
**File:** `paper_writer_helpers.py` lines 537-599
**Problem:** Two concurrent `paper_writer.sh` processes can both pass the "no existing lock" check within a ~1ms window and clobber each other's lock.
**Practical risk:** LOW for current usage (single user, sequential runs). MEDIUM at v1.0 scale if parallel drafts are ever supported.
**Fix:** Use `os.open(path, os.O_CREAT | os.O_EXCL)` for atomic lock creation. ~15 lines.
**Effort:** 30 minutes + 2 tests.

### B7. String injection in phase_init bash heredoc
**Source:** code-review Critical #2
**File:** `paper_writer.sh` lines 429-436
**Problem:** `project_id` is interpolated into a Python string literal without escaping. A project_id containing `'` breaks the inline Python.
**Practical risk:** LOW — project_id is typically a slug like `functional_dark_matter`. No user-supplied arbitrary strings. But it's a correctness issue.
**Fix:** Pass via JSON file per `feedback_bash_to_argparse_use_json_files.md`. ~20 lines.
**Effort:** 30 minutes.

---

## Tier C — Prompt Improvements (fix before v1.0)

These are prompt-engineering improvements that reduce overclaim risk and improve cross-prompt consistency. None are bugs — the current prompts produce valid manuscripts — but they close ambiguity gaps that could produce silent quality drift.

### C1. Unified fabrication definition across prompts ⟵ CONVERGENCE: prompts CPN.3
**Problem:** "Fabrication" means different things in different prompts (methods: "not in plan or code"; results: "not in REPORT or notebook"; caption: "inventing n-values"). No single definition.
**Fix:** Add a "Fabrication discipline" subsection to LAYOUT.md defining the 3 valid trace-back categories (canonical sources, verified bibliography, explicit metadata). Reference from all drafting prompts.
**Effort:** 1 hour (LAYOUT edit + cross-references in 6 prompts).
**Impact:** HIGH — this is the anti-hallucination contract. Inconsistency here is the highest-risk prompt issue.

### C2. Tier-voice dictionary ⟵ CONVERGENCE: prompts CPN.1
**Problem:** STRONG/THIN/EXPLORATORY language is defined slightly differently across 6 prompts ("explicit Act-II deferral" vs "scope-narrowed explicit" vs "explicit caveats"). A THIN manuscript could have inconsistent voice across sections.
**Fix:** Create a tier-voice reference table in LAYOUT.md; replace per-prompt definitions with a cross-reference.
**Effort:** 1 hour.
**Impact:** MEDIUM — affects manuscript voice consistency, not correctness.

### C3. Citation orphan risk protocol (citation_pool ↔ methods gap)
**Source:** prompts CRx.1
**Problem:** If Methods adds a method-claim grounded in execution but never in the citation pool, Discussion cannot cite it. The method is real but unreferenceable.
**Fix:** Add a "fast-track entry" protocol to citation_pool.v1 for methods discovered during execution. ~10 lines of prompt edit.
**Effort:** 30 minutes.
**Impact:** MEDIUM — affects completeness of citation coverage for novel methods.

### C4. Compound-citation format validation
**Source:** prompts IMx.4
**Problem:** `[Key1, Key2]` (comma inside brackets) is forbidden by discussion.v1 but not validated by any downstream tool. The finalize step's renumbering will fail silently.
**Fix:** Add self-review grep item to discussion.v1 + regex check in the citation renumbering step.
**Effort:** 30 minutes (prompt) + 30 minutes (code).
**Impact:** MEDIUM — produces orphan citations in assembled manuscript.

### C5. Reframer notebook-secondary protocol clarification
**Source:** prompts CRx.3
**Problem:** "Check notebook outputs via Grep" is ambiguous: ipynb is JSON, text grep may mis-match; "output cell" vs "markdown cell" undefined; renormalized numbers (0.0523 → 5.2%) unclear.
**Fix:** Replace 4 lines in reframer.v1 Check 1 with explicit match criteria (code-cell output, markdown-cell prose, ±10% precision criterion).
**Effort:** 30 minutes.
**Impact:** MEDIUM — affects drift-detection reliability for edge cases.

### C6. Standardized escape-hatch protocol
**Source:** prompts CPN.5
**Problem:** Some prompts halt with exact error messages; some are vague; some proceed with degraded output. Orchestrator error handling is unpredictable.
**Fix:** Add a standard escape-hatch format to LAYOUT.md: `[ERROR: <prompt>] <condition>. {orchestrator_instruction: halt|degrade|skip}`.
**Effort:** 2 hours (LAYOUT + updates to 8 prompts).
**Impact:** LOW-MEDIUM — affects error recovery clarity, not normal-path correctness.

### C7. Reframing-log schema in all drafting prompts
**Source:** prompts CPN.2
**Problem:** Only intro.v1 has the reframing-log schema. Methods, Results, Discussion can discover drift during drafting but have no schema to log it.
**Fix:** Add REFRAMING_LOG_PATH input + append-entry protocol to methods.v1, results.v1, discussion.v1.
**Effort:** 1 hour.
**Impact:** LOW-MEDIUM — the reframer.v1 catches most drift post-hoc; this would catch it earlier.

### C8–C14. Remaining prompt suggestions (batch)
These are individually low-impact but collectively improve prompt precision:
- **C8:** plan.v1 glyph cross-walk clarification for absence-of-evidence vs partial (IMx.1) — 15 min
- **C9:** methods.v1 inference-vs-fabrication boundary clarification (IMx.2) — 15 min
- **C10:** results.v1 "strongest" ranking function for 8+ findings (IMx.3) — 15 min
- **C11:** abstract.v1 Conclusions-as-subset-of-Discussion rule (IMx.5) — 15 min
- **C12:** revise_throughline.v1 ✗-glyph demotion rules (IMx.8) — 15 min
- **C13:** figure_caption.v1 boilerplate-exclusion list expansion (IMx.9) — 15 min
- **C14:** results.v1 "no future-tense main claims" self-review item (SGx.14) — 10 min
**Total batch effort:** ~2 hours.

---

## Tier D — Documentation Debt (fix before v1.0)

### D1. Version banners on SPEC.md, LAYOUT.md ⟵ CONVERGENCE: docs Critical #1
**Problem:** SPEC and LAYOUT describe v0.1 design. Code is at v0.6.4. No warning.
**Fix:** Add a 3-line header banner to each: "This document describes v0.1.0. Current version is v0.6.4. See RELEASE_NOTES for changes."
**Effort:** 15 minutes.

### D2. Backfill DECISIONS.md D-027 through D-033 ⟵ CONVERGENCE: docs Critical #2
**Problem:** Last entry is D-026 (2026-04-27). v0.3–v0.6 architectural decisions are unrecorded.
**Fix:** Write retrospective entries for: dual-reviewer architecture, adversarial-review-paper.v2 schema, JSON-validity hardening, caption sufficiency redesign, reframing repair dispatch, tier detection default, boilerplate section stripping.
**Effort:** 2–3 hours.

### D3. Update README.md status + install instructions ⟵ CONVERGENCE: docs High #1, #4
**Problem:** README says "v0.1 — specification only. No code." Installation marked "planned." Both false.
**Fix:** Update status line, verify install commands, remove "planned" markers.
**Effort:** 30 minutes.

### D4. Update SKILL.md status and feature list
**Source:** docs Medium (SKILL.md)
**Problem:** Status line says "v0.1 — first release" with REPAIR_MODE/rewrite-loop/assemble listed as deferred. All are now shipped.
**Fix:** Update status to v0.6.4; list shipped features accurately.
**Effort:** 20 minutes.

### D5. Remove vestigial /prompts/ directory at repo root
**Source:** repo-design I-004
**Problem:** Empty `/prompts/.gitkeep` confuses new contributors. Real prompts are in `src/.../skill/prompts/`.
**Fix:** `rm -rf prompts/` + note in LAYOUT.md.
**Effort:** 5 minutes.

### D6. Consolidate RELEASE_NOTES into CHANGELOG.md
**Source:** repo-design I-001 (partial)
**Problem:** 6 separate RELEASE_NOTES files; no single place to see version history.
**Fix:** Create CHANGELOG.md with entries extracted from each RELEASE_NOTES. Keep originals for detail.
**Effort:** 1 hour.

---

## Tier E — Aspirational / Post-v1.0

These are good ideas that don't justify pre-v1.0 effort:

- **E1.** Release.py automation script (repo I-001) — saves 10 min per release; not worth building for the 2-3 releases remaining before handoff.
- **E2.** Bash test harness via BATS (repo-design §7 recommendation) — high setup cost; bash orchestrator is stable and unlikely to change materially before v1.0.
- **E3.** ROADMAP.md (docs §6.3) — the roadmap lives in augmentation-stream-plan.md and punch-list files; a dedicated file adds maintenance burden.
- **E4.** docs-audit automation script (docs §5.1) — nice for a 10-person team; overkill for Adam + Claude.
- **E5.** TROUBLESHOOTING.md (docs §6.2) — useful post-v1.0 when users other than Adam exist.
- **E6.** Tier-specific worked examples in plan.v1 for THIN/EXPLORATORY (prompts SGx.1) — helpful but no live THIN/EXPLORATORY projects exist yet to test against.
- **E7.** Abstract machine-readable metadata comment (prompts SGx.6) — over-engineering; the M2 validator works fine with regex.
- **E8.** Optional `beril-adversarial-skill` dependency in pyproject.toml (repo S-006) — pip optional extras are fragile across pipx boundaries.
- **E9.** Full phase_log.jsonl append-only architecture (repo C-002 full version) — better than minimal state versioning but 1-2 sprint days for a pattern that hasn't caused a real failure yet.
- **E10.** `invoke_claude` per-call timeout (code Suggested #19) — `timeout 5m` is a good idea but requires testing the timeout-as-retryable path end-to-end.

---

## Noise / False Positives

| Finding | Source | Why dismissed |
|---------|--------|---------------|
| Mutable default in cmd_write_handoff | code Suggested #15 | Reviewer admits no actual bug exists. The code is correct. |
| Circular refs in diff_artifacts | code Suggested #17 | Theoretical; inputs come from a deterministic path-walker that cannot produce duplicates. |
| importlib.resources fallback for <3.10 | repo S-003 | pyproject.toml requires >=3.10. No action needed. |
| Unused dist/ directory | repo S-004 | Already .gitignored; local working-tree artifact only. |
| No module docs for sub-packages | repo S-001 | True but cosmetic. Docstrings can be added by the professional team during onboarding. |
| Test naming inconsistency | repo S-002 | Cosmetic. pytest discovers tests regardless of class naming convention. |
| Commented-out code in paper_writer.sh | repo S-005 | The comment already references the punch list. Adding more explanation doesn't help. |
| LLM output empty-file validation (code #20) | code Suggested #20 | The orchestrator already has post-write validators for specific phases (caption sufficiency, citation count). A generic validator in stream_progress.py would add complexity for marginal gain. |
| figure_caption.v1 boilerplate list non-exhaustive (prompts IMx.9) | prompts | The Python-side `_strip_prose_for_inline` already handles this mechanically with regex. The prompt list is a fallback hint, not the enforcement layer. |
| citation_pool depth mode selection (prompts SGx.3) | prompts | The orchestrator already passes `--citation-depth` via state.json. The prompt is advisory; the code controls it. |

---

## Effort Summary

| Tier | Items | Effort | Cumulative |
|------|-------|--------|------------|
| A (real bugs) | 5 | ~2 hours | 2 hours |
| B (structural) | 7 | ~5 days | 5.25 days |
| C (prompts) | 14 | ~8 hours | 6.25 days |
| D (docs) | 6 | ~5 hours | 6.9 days |
| **Total pre-v1.0** | **32** | **~7 days** | |
| E (post-v1.0) | 10 | ~5 days | deferred |

**Critical path:** A1-A5 (2 hours) → B1 (1 day, highest structural priority) → C1 (1 hour, highest prompt priority) → D1-D2 (3 hours, highest docs priority). This sequence covers the highest-impact items across all 4 reviews in ~2 days.

---

## Cross-Reviewer Convergence Map

Findings independently flagged by 2+ reviewers (highest confidence):

| Theme | Reviewers | Triage items |
|-------|-----------|-------------|
| Cross-skill contract drift | repo-design, docs, code | B1 |
| State schema unversioned | repo-design, code | B2 |
| God module / helpers.py size | repo-design, code | B3 |
| Documentation staleness (v0.1 → v0.6) | docs, repo-design | D1, D2, D3 |
| Null/None coalescing pattern | code (+ prior memory) | A1 |
| Error handling inconsistency | repo-design, code, prompts | A2, B4, C6 |
| Fabrication definition inconsistency | prompts (CPN.3) | C1 |
| Tier language inconsistency | prompts (CPN.1) | C2 |

---

*Triage completed 2026-05-03. Next action: fix Tier A items, then sequence Tier B starting with B1 (CONTRACT.md).*
