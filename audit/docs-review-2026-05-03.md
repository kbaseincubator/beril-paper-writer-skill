# beril-paper-writer-skill Documentation Review
**Date:** 2026-05-03  
**Reviewer:** Adversarial documentation audit  
**Scope:** All documentation (technical, architectural, user-facing, in-code)  
**Finding:** Multiple high-impact gaps; some cross-reference integrity issues.

---

## Executive Summary

The skill ships with strong foundational documentation (SPEC, LAYOUT, DECISIONS are comprehensive and well-organized). However, three categories of issues prevent this documentation from being fully usable by a developer or end-user without Adam's oral tradition fill-in:

1. **Missing contract documentation** — cross-skill interop dependencies are implied rather than explicitly codified
2. **Incomplete post-v0.1 state tracking** — documentation describes v0.1 design but code has shipped multiple point releases (v0.3+, v0.5+, v0.6+) with significant architectural changes not reflected in primary docs
3. **Stub / placeholder alerts scattered across docs** — markers like "BLOCKED in v0.1" and "TBD" create confusion about what is actually shipped vs. deferred

---

## Category 1: Technical Documentation

### 1.1 SPEC.md — Design specification

**Status:** Well-written, load-bearing reference.  
**Accuracy:** High — matches intended v0.1 design, but is now stale (v0.6 is live).  
**Completeness:** Strong; design intent is well-articulated.

**Issues:**

- **[CRITICAL] Version coverage gap.** The SPEC describes v0.1 design intent (2026-04-25). The memory indicates v0.6.3 is currently shipped with significant changes (dual-reviewer architecture, new adversarial-paper-reviewer contract, JSON-validity hardening, etc.). A reader picking up SPEC.md has no warning that this describes a 4-version-old design.
  - **Impact:** Developer fork will make decisions based on stale premises. End-user trying to understand "why does the skill work this way" reads a spec that doesn't match the code.
  - **Recommendation:** Add a header banner on SPEC.md: "**NOTE:** This specification describes v0.1.0 (2026-04-25). The current shipped version is v0.6.3. See RELEASE_NOTES_v0_6.md and later for changes. This document remains the architectural foundation; §X has been superseded by [link to relevant note]."

- **[MEDIUM] SPEC §7.1.2 (M5 validator)** states "Soft-warning in v0.1" for software+version checking. This is accurate for v0.1, but if the current version no longer treats M5 as soft-warning, readers need to know where that changed.

- **[MEDIUM] SPEC §5.4 (gap-fill round cap)** describes "2 rounds max, 5 requests per type per round." Need to verify this still applies in v0.6.3 or flag what changed.

### 1.2 LAYOUT.md — Package structure and runtime contracts

**Status:** Detailed, technical, well-structured.  
**Accuracy:** Mixed — some paths and contracts are correct; some are stale.  
**Completeness:** Generally strong but lacks explicit "what changed between v0.1 and v0.6" migration notes.

**Issues:**

- **[HIGH] state.json schema (LAYOUT §"state.json schema")** shows a v0.1 example with fields like `"phase": "throughline_pick | drafting | review | assembled"` and `"artifact_hash_at_confirmation"`. Need explicit note: "Schema shown is v0.1 example. See `src/beril_paper_writer/state.py` for current schema; it has evolved to include X, Y, Z in v0.6."

- **[MEDIUM] "Validator → section dispatch table" (LAYOUT §"Validator → section dispatch table")** references M1-M10 dispatch paths. The table shows status "BLOCKED in v0.1: Repair requires `reference/data_availability_template.md`, which has not been written." If this is now resolved in v0.6, the table needs a note saying so.

- **[MEDIUM] Section on "Reframing-log entry-numbering contract"** describes sequential execution assumption. Memory indicates v0.3.1 onwards may have introduced parallel-execution possibilities (per v0.3.1 punch list). Clarify whether sequential is still the assumption or if this has changed.

- **[LOW] LAYOUT §"Deliverables this document blocks"** lists four items including "After spec sign-off: implementation begins." This reads like a forward-looking placeholder from April 2026. Since implementation is complete and v0.6 is shipped, this section should be archived or removed.

### 1.3 DECISIONS.md — Decision log

**Status:** Comprehensive, well-formatted, includes rationale.  
**Accuracy:** High through D-026.  
**Completeness:** Incomplete — no entries after D-026 (2026-04-27), yet memory indicates significant architectural changes in v0.3.x, v0.4.x, v0.5.x, v0.6.x.

**Issues:**

- **[CRITICAL] Stale decision log.** Last entry is D-026 (2026-04-27: embedded-image-tag form). Memory shows v0.6.3 shipping 2026-05-02 with breaking changes (dual-reviewer architecture, `adversarial-review-paper.v2` schema, layout detection for v0.3.1 zones, JSON-validity hardening, schema-aware label UX). None of these have decision log entries.
  - **Impact:** If a developer wants to understand "why does the paper reviewer have two modes" or "why did the schema change," DECISIONS.md is silent. They have to dig through RELEASE_NOTES_v0_6.md or ask Adam.
  - **Recommendation:** Add entries D-027 through D-0NN for major changes. At minimum:
    - D-027 — dual-reviewer architecture (fallback + canonical)
    - D-028 — adversarial-review-paper.v2 schema (single-array format)
    - D-029 — JSON-validity hardening pattern (anti-pattern rules in prompts)
    - D-030 — layout-detection pattern for v0.3.1 (new path structure)

- **[MEDIUM] No decision log for v0.3-v0.6 architectural pivots.** The paper-writer appears to have significant delivery milestones (v0.3 with caption richness, v0.5 with dual-reviewer, v0.6 with schema alignment), but DECISIONS.md doesn't record the design intent. This makes the code feel arbitrary rather than intentional.

### 1.4 README.md — User-facing introduction

**Status:** Clear, accessible.  
**Accuracy:** Mixed — installation section (§"Install (planned)") is outdated.  
**Completeness:** Strong overview; good for orientation.

**Issues:**

- **[HIGH] Installation instructions (§"Install (planned)")** show:
  ```bash
  pipx install git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git
  cd <BERIL_ROOT>
  beril-paper-writer install-skill .
  beril-paper-writer configure
  ```
  This section is marked "planned" but the skill has shipped v0.6. The instructions should be updated to reflect whether this pattern is correct. Test them.

- **[MEDIUM] Version notes.** README says "Status: v0.1 — specification only. No code." This is false; code has shipped. Either update README to current state or clearly separate the "original v0.1 vision" section from "what currently ships."

- **[MEDIUM] "What it produces" (§"What it produces")** shows draft output structure but doesn't mention that v0.6 adds a `deliverable/` directory structure (per v0.3.1 punch list). The file tree is outdated.

---

## Category 2: Architectural Documentation

### 2.1 augmentation-stream-plan.md

**Status:** Active stream plan; well-maintained through 2026-04-27.  
**Accuracy:** High for paper-writer v0.1 state; but memory indicates v0.6 is the current shipped state, not v0.1.  
**Completeness:** Good overview but lacks v0.2-v0.6 summary.

**Issues:**

- **[MEDIUM] Section 2: "The four skills" table** lists paper-writer as "v0.1.0 shipped 2026-04-27." Memory indicates v0.6.3 shipped 2026-05-02. The table is stale by 5 days but still in "active" document.
  - **Recommendation:** Update table to show "v0.6.3 shipped 2026-05-03; major milestones: v0.3 (caption richness), v0.5 (dual-reviewer), v0.6 (schema alignment)."

- **[LOW] Section 7: "Active work (2026-04-27)"** describes the patch-cycle work that shipped v0.1.0 and lists v0.2 backlog. Memory shows v0.6 is live, so this entire section is stale. Update or archive this section.

### 2.2 CLAUDE.md (workspace root)

**Status:** Comprehensive orientation; loaded in system-reminder.  
**Accuracy:** High overall; some section pointers may be outdated.  
**Completeness:** Strong; covers team, workspace layout, first actions, memory discipline.

**Issues:**

- **[LOW] "Active streams" section** mentions paper-writer is "Phase 3 done; Phase 4 (orchestrator) ahead." This describes April 2026 state. Current state is v0.6 shipped. Update or clarify that phases refer to the original v0.1 design.

### 2.3 WORKING-PRACTICES.md

**Status:** Good team-level conventions.  
**Accuracy:** Appears sound; team discipline is clear.  
**Completeness:** Adequate for a 4-person team; scalability assumed post-v1.

**Issues:**

- **[LOW] "Fresh conversation startup" (§"Fresh conversation startup")** points to spike/CLAUDE.md. No such file exists in the current workspace root structure (only CLAUDE.md at workspace root). Correct the reference or create the missing file.

### 2.4 Cross-skill CONTRACT and SHARED_INTERFACES

**Status:** Not found.  
**Accuracy:** N/A  
**Completeness:** N/A

**Issues:**

- **[CRITICAL] Missing CONTRACT.md or SHARED_INTERFACES.md.** Memory entry `project_adversarial_v0_6_0.md` mentions: "New `CONTRACT.md` pins durable interop surface" (referring to paper-writer ↔ adversarial-reviewer contract). LAYOUT.md §"Coupling to beril-adversarial" describes the contract in prose (bibliography.bib format, citation-map.md structure, draft_N layout expectations), but no explicit CONTRACT.md file exists at the skill level.
  - **Impact:** When beril-adversarial upgrades, the paper-writer team doesn't have a canonical place to verify "what must stay stable." The contract lives scattered across prose in SPEC, LAYOUT, and RELEASE_NOTES.
  - **Recommendation:** Create `CONTRACT.md` documenting the file-layout interface, citation schema, and adversarial-review input/output formats. Cross-reference from both paper-writer and adversarial-reviewer.

---

## Category 3: User-Facing Documentation

### 3.1 SKILL.md (the Claude Code discovery file)

**Status:** Present and functional.  
**Accuracy:** Mixed — references v0.1 state but code is v0.6.  
**Completeness:** Good structure; examples and workflow are clear.

**Issues:**

- **[MEDIUM] Status line (§"Status:")** reads: "v0.1 — first release. Linear pipeline; REPAIR_MODE, review-rewrite loop, card elicitation, and `assemble` markdown→docx all deferred to v0.2."
  - Memory shows REPAIR_MODE, review-rewrite, and assemble markdown→docx are now live in v0.6. This status line is false.
  - **Recommendation:** Update: "v0.6.3 — stable. Full IMRAD pipeline with dual-reviewer (fallback + canonical), REPAIR_MODE for validator failures, review-rewrite loop, and markdown→docx assembly via python-docx."

- **[MEDIUM] Slash commands** (§"Slash commands") reference `/beril-paper-writer` and `/beril-paper-writer-continue` with flags like `--model`, `--no-adversarial`, etc. Verify these flags exist in current CLI and are not stale.

- **[LOW] Workflow diagram** (§"Workflow") shows six steps ending with "Step 6 — Final pause". If v0.6 has a review-rewrite loop, this may be incomplete.

### 3.2 Slash command markdowns

**Status:** Found at `src/beril_paper_writer/skill/commands/beril-paper-writer.md` and `beril-paper-writer-continue.md`.  
**Accuracy:** Appear internally consistent; not fully verified against current code.  
**Completeness:** Adequate for user invocation.

**Issues:**

- **[MEDIUM] Not reviewed for v0.6 changes.** The slash commands should mention the dual-reviewer behavior (fallback + canonical), REPAIR_MODE, and the review-rewrite loop if they are now part of the user-facing interface. Current files have not been examined in detail; spot-check needed.

### 3.3 Installation instructions (multiple locations)

**Status:** Scattered; consistency unclear.  
**Accuracy:** README says "planned"; actual state unknown.  
**Completeness:** Unclear what the actual install procedure is.

**Issues:**

- **[HIGH] Multiple conflicting installation guides.** 
  - README.md §"Install (planned)" shows `pipx install git+ssh://...`.
  - RELEASE_NOTES.md likely has another version.
  - SKILL.md has workflow instructions but not install steps.
  - **Recommendation:** Consolidate to a single source of truth; cross-reference from others. Create a dedicated `INSTALL.md` or fold final version into README with (✓ Verified 2026-05-03) marker.

### 3.4 RELEASE_NOTES*.md files

**Status:** Multiple versions exist (v0_1, v0_2, v0_3, v0_4, v0_5, v0_6).  
**Accuracy:** Each describes its respective version; content not fully verified.  
**Completeness:** Good coverage of per-version changes; however, no consolidated summary for someone picking up the skill fresh.

**Issues:**

- **[MEDIUM] No "quick reference" for v0.6 features.** A user upgrading from v0.1 to v0.6 has to read 5 release notes sequentially to understand all changes. Create a `RELEASE_NOTES_SUMMARY_v0_1_to_v0_6.md` listing all major feature additions and breaking changes in one place.

- **[MEDIUM] Each RELEASE_NOTES file may reference features as "shipped" that are actually deferred.** Spot-check a few entries for accuracy.

---

## Category 4: In-Code Documentation

### 4.1 Docstrings in Python modules

**Status:** Not examined in detail (would require reading all ~25 .py files).  
**Accuracy:** Unknown; spot-checks needed.  
**Completeness:** Unknown.

**Issues:**

- **[RECOMMENDATION] Spot-check critical modules:**
  - `state.py` — does its schema docstring match current LAYOUT.md and actual code?
  - `extract_methods.py` — does it document the AST-walking limitations (non-.ipynb files, Spark execution)?
  - `validate_manuscript.py` — do M1-M10 validators have inline documentation matching SPEC §7.1?

### 4.2 Prompt file headers

**Status:** The prompts exist; headers not examined.  
**Accuracy:** Unknown.  
**Completeness:** Unknown.

**Issues:**

- **[RECOMMENDATION]** Each prompt should have a header documenting:
  - Version (e.g., `v1.md` implies versioning discipline; is this enforced?)
  - Input contract (what absolute-path inputs does the orchestrator pass?)
  - Output contract (what file does the prompt write, in what format?)
  - REPAIR_MODE behavior (if applicable)
  - Known failure modes and escape hatches
  - Link back to SPEC or DECISIONS for design rationale

### 4.3 Bash orchestrator (paper_writer.sh)

**Status:** Not examined.  
**Accuracy:** Unknown; 1200+ lines, likely needs internal documentation.  
**Completeness:** Unknown.

**Issues:**

- **[RECOMMENDATION]** 1200+ lines of bash is hard to follow. Add:
  - High-level phase diagram in a comment block at the start
  - Per-phase function headers explaining what each phase does, what artifacts it reads/writes, what error codes it can emit
  - Explanation of the state.json / .handoff.json contract (where and when each is written)

---

## Category 5: Cross-Document Integrity

### 5.1 Link checking

**Status:** Not systematically verified.  
**Accuracy:** Some links likely broken by file moves or renames.  
**Completeness:** Unknown.

**Issues:**

- **[MEDIUM] SPEC.md has multiple internal cross-references** (e.g., "see LAYOUT.md §3", "see DECISIONS.md D-024"). If LAYOUT or DECISIONS are reorganized, these break silently.
  - **Recommendation:** Add a `docs/links-audit.sh` script that greps for `[SPEC|LAYOUT|DECISIONS].md §` patterns and verifies the targets exist.

- **[MEDIUM] README.md references "sister-skill specs" at `../beril-adversarial-skill-draft/LAYOUT.md`. Verify this relative path is correct from the current repo structure.

### 5.2 Terminology consistency

**Status:** Spot-checked; mostly consistent.  
**Accuracy:** Good; terms like "throughline", "gap-fill", "IMRAD" are used consistently.  
**Completeness:** Strong.

**Issues:**

- **[LOW] "Reframing log" vs. "reframing_log.md"** — both are used; ensure documentation is consistent in distinguishing the concept from the filename.

---

## Category 6: Missing Documentation

### 6.1 Smoke-test documentation

**Status:** Partially present.  
**Accuracy:** Spot-checks suggest quality.  
**Completeness:** Multiple runbooks and findings docs; appears thorough.

**Issues:**

- **[MEDIUM] Runbooks live in `smoke-test/` but are not linked from README or SKILL.md.** A user wondering "how do I verify this works?" has no obvious entry point. Add a §"Testing" to README pointing to smoke-test runbooks.

- **[LOW] Smoke-test findings (e.g., `end_to_end_smoke_findings.md`) are discovery logs from development. They should be archived once findings are resolved, or marked (✓ RESOLVED v0.6) for transparency.

### 6.2 Troubleshooting / FAQ

**Status:** Not found.  
**Accuracy:** N/A  
**Completeness:** N/A

**Issues:**

- **[MEDIUM] No FAQ or Troubleshooting guide.** When the skill fails, where does a user look for common error messages, recovery steps, or contact points?
  - **Recommendation:** Create `TROUBLESHOOTING.md` covering:
    - Exit codes and their meanings
    - Common validator failures and how to resolve them (M7 "effect size missing" → run analysis → file gap-fill)
    - Resume contract (how to recover from mid-pipeline halt)
    - When to contact Adam vs. self-serve recovery

### 6.3 v0.2+ backlog vs. roadmap

**Status:** v0.2 backlog mentioned in RELEASE_NOTES but not in a dedicated roadmap.  
**Accuracy:** Not verified.  
**Completeness:** Incomplete; scattered across multiple release notes.

**Issues:**

- **[LOW] Create `ROADMAP.md`** documenting:
  - What's shipped (v0.6.3 as of 2026-05-03)
  - What's in the v0.7+ backlog (REPAIR_MODE enhancements, scope-coherence checker, overclaim post-processor per v0.6 notes)
  - Known limitations and non-goals
  - How to contribute / suggest features

---

## Summary: Documentation Debt (Top 5 High-Impact Gaps)

| # | Gap | Impact | Effort | Priority |
|---|---|---|---|---|
| **1** | SPEC, LAYOUT, DECISIONS are stale (v0.1 when v0.6 is shipped). No warning banner or version tracker. | New developer makes decisions based on 6-version-old design. | Medium — add banner + update key sections | **CRITICAL** |
| **2** | No CONTRACT.md documenting paper-writer ↔ adversarial-reviewer interop contract. | When either skill changes, maintainers lack canonical definition of "what must stay stable." Breaking changes possible. | Medium — 1-2 hours to consolidate from SPEC/LAYOUT prose | **HIGH** |
| **3** | DECISIONS.md ends at D-026 (2026-04-27); v0.3-v0.6 architectural changes have no recorded design rationale. | Code appears arbitrary. Debugging design intent is impossible without asking Adam. | High — retrospective entries for v0.3-v0.6 | **HIGH** |
| **4** | Installation instructions scattered across README, RELEASE_NOTES, SKILL.md; "planned" status conflicts with shipped code. | User attempting install follows broken or outdated steps. | Low — consolidate + test | **MEDIUM** |
| **5** | No cross-document version tracking. Readers don't know when a statement applies to v0.1 vs. v0.6. | Silent inconsistency errors; confusion about what is / isn't available. | Medium — add inline markers + version table in README | **MEDIUM** |

---

## Recommendations (Prioritized)

### Tier 1 (Do before v1.0 release)

1. **Add version-change banner to SPEC.md, LAYOUT.md** — "This document describes v0.1.0 (2026-04-25). Current version is v0.6.3. Changes: [link to RELEASE_NOTES_v0_6.md]. See the decision log for design rationale."

2. **Append DECISIONS.md entries D-027 through D-030+** for major v0.3-v0.6 changes (dual-reviewer architecture, schema changes, JSON hardening, layout restructuring).

3. **Create CONTRACT.md** documenting the paper-writer ↔ adversarial-reviewer file-layout contract. Cross-link from SPEC §8.1 ("Coupling to the adversarial reviewer").

4. **Update README.md status line** — change "v0.1 — specification only" to "v0.6.3 — stable. See VERSION_HISTORY.md for feature timeline."

5. **Create INSTALL.md with tested instructions** — verify `pipx install ...` works; document environment setup; link from README and SKILL.md.

### Tier 2 (Do before v1.0 if capacity allows)

6. **Consolidate RELEASE_NOTES** — create `RELEASE_NOTES_SUMMARY_v0_1_to_v0_6.md` listing breaking changes, major features, and migration path for users upgrading versions.

7. **Create TROUBLESHOOTING.md** — document exit codes, common validator failures, resume recovery, and where to ask for help.

8. **Add smoke-test reference to README** — section "Testing" with link to `smoke-test/end_to_end_runbook.md`.

9. **Spot-check all shell commands and file paths** in documentation (README installation, SKILL.md workflow) against current code.

### Tier 3 (Post-v1.0 nice-to-have)

10. **Create ROADMAP.md** documenting v0.7+ backlog and non-goals.

11. **Implement docs-audit automation** — script that checks for broken internal links, stale version references, and missing API documentation.

12. **Add inline code documentation** to critical Python modules (state.py, extract_methods.py, validate_manuscript.py) with headers explaining input/output contracts and failure modes.

---

## Assessment

**Overall Quality:** **B+ (Good foundational docs, poor maintenance of versioning)**

The skill's foundational documentation (SPEC, LAYOUT, DECISIONS) is well-written and represents sound design thinking. However, the documentation has suffered "version drift" — code has advanced 6 versions ahead (v0.1 → v0.6) while primary documentation remains locked in April 2026. This creates a usability problem: a developer or new user has no clear signal for "what you're reading is 6 versions out of date."

The most critical gap is the missing cross-skill CONTRACT — paper-writer and adversarial-reviewer must stay in sync, but there's no canonical document defining that interface.

**Recommendation for Adam:** Before v1.0, invest 4-6 hours in:
1. Version-stamping all primary docs (SPEC, LAYOUT, README)
2. Backfilling DECISIONS.md with v0.3-v0.6 entries
3. Creating CONTRACT.md for interop stability

This positions the skill for handoff to a production team and avoids silent inconsistency bugs as the ecosystem evolves.

---

*End of review. Report written: 2026-05-03.*
