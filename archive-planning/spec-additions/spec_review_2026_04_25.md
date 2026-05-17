# Review of Three Spec Additions (2026-04-25)

## Summary

- **Internal consistency is sound** across all three specs. The discrepancy register's lifecycle correctly feeds the word-comment assembler, and both integrate cleanly with the database cards' schema-gotchas entries. No field-name mismatches or broken assumptions.
- **Architectural fit is problematic.** These three specs collectively add ~9–13 hours of implementation work that should not land before the orchestrator (Phase 4) is functional. Adding them now is scope creep that risks the spike's delivery schedule. The register and cards are valuable, but the elicitor's complexity and the word-comment XML manipulation introduce implementation risk that doesn't pay off until you know the orchestrator actually works.
- **Hidden complexity is substantial.** The specs underestimate the cost of: multi-user card attribution and privacy review, cross-platform Word-comment rendering validation, user confusion between "open vs. closed" register entries when they appear in the manuscript simultaneously, and orchestrator coordination of concurrent register writes.
- **Underuse risk is real.** Prompts won't reliably write to the register unless the discipline is baked into every drafting prompt's system message, and even then, the overhead of constructing a well-formed entry will cause silent drops. The register will either stay empty or fill with noise.

**Recommendation:** Defer the discrepancy register and database cards to Phase 4b (after the orchestrator is working). Ship the word-comment assembler no earlier than Phase 4b and only if the register entries are flowing reliably from the prompts. In the current state, all three specs are load-bearing for one another, and bundling them into Phase 4a creates a single point of failure.

---

## Per-Spec Audit

### Discrepancy Register (`discrepancy_register.md`)

**Consistency verdict:** SOUND internally. The lifecycle is clear: open → (self-resolvable | user-resolvable | document-as-known-issue | accepted-as-no-action) → closed with mirror to reframing_log.

**Field alignment with assembler:** The `Where in manuscript:` field format (e.g., `Methods §"Statistical Analysis", para 2`) is well-defined for the word-comment assembler to anchor on. The format is practical and the assembler's fallback (anchor at section header if the paragraph can't be found) is a sensible escape hatch.

**Type enum:** 9 entries, well-chosen. Covers the pitfalls documented in the smoke test (seed-typo-correction, plan-execution, threshold-ambiguity). The types are actionable — no "miscellaneous" bucket that would become a noise sink.

**Per-prompt integration cost:** The spec claims ~30 lines per prompt × 6 prompts = ~180 lines. This is realistic for the per-prompt Discipline section. BUT the per-prompt edits are not the real cost. The real cost is prompts failing to write register entries consistently because:

1. Constructing a properly-formatted DR-N entry requires the prompt to know the next sequential N across the register file. The spec says "read max(N) + 1" which is correct but forces every prompt to include the read-and-parse logic. This is error-prone and will fail silently if a prompt misses a collision.
2. The "Discipline pass" is a myth. The citation_pool.v1 smoke test shows that citation-building is a full-pass activity; prompts don't have clean separation between drafting and discipline. If the register-write is in a "Discipline pass," it will be skipped under time/cost pressure or if the pass logic is incomplete.

**Open questions not addressed:** The spec names three; two are substantive:
- **GC of closed entries.** Hard-delete after assembly vs. keep for historical view. The spec proposes hard-delete to keep the active queue tight. Reasonable, but this design choice affects whether users can recover a closed entry's text if they realize they made a mistake — once GCed, it's only in reframing_log.md in synthesized form, not as the original. No mention of a GC grace period or confirmation step.
- **Cross-draft register persistence.** The spec proposes open entries carry over to draft_N+1. This is correct but creates a surprise: the register from draft_1 will appear in draft_2's initial state, and if the user re-runs a prompt against draft_2, that prompt may re-write entries from draft_1 if it detects the same discrepancy. No mention of deduplication or "don't re-write entries from earlier drafts."

**Missing:** An escape hatch for when a prompt can't construct a valid entry (e.g., "Where in manuscript" is unknown because drafting hasn't reached that section yet). The spec says entries can have "TBD" for the manuscript location, but doesn't mandate how prompts should handle the case where they can't fill the field. This is an edge case but a real one.

---

### Word Comments at Assembly (`word_comments_at_assembly.md`)

**Consistency verdict:** SOUND with the register. The spec correctly reads `Status: open AND Proposed resolution path: document-as-known-issue` entries and omits closed entries and entries that don't match that path.

**Two-tier strategy:** Tier 1 (native Word XML via lxml) is the right choice, and tier 2 (inline + appendix) is a pragmatic fallback. The implementation sequence is sensible. **However**, cross-platform testing is a hidden cost the spec mentions but doesn't budget for:

- Word, LibreOffice, Google Docs, and Apple Pages all render OOXML-compliant comments, but with variations in margin placement, font size, threading behavior, and how comments interact with track-changes. The spec says "testing against python-docx's example documents" but doesn't account for actually opening the generated .docx in each tool and checking rendering. This is manual QA, not automated, and it adds 2–3 hours of wall-clock time before tier-1 can be marked "done."
- The spec's comment-text format is markdown-flavored but the word-comment body is unformatted text. Rendering bold/italic/code in the margin is not supported by OOXML comments as plaintext (you'd need embedded formatting objects, which complicates the lxml code). The spec doesn't clarify whether italics or emphasis in the comment text will survive or strip. This will be discovered during cross-platform testing and may require falling back to tier 2 more often than expected.

**Anchor-span location logic:** The assembler looks for section headers and paragraphs by text matching. This is fragile:

- If the user edits the manuscript between draft generation and assembly, the anchor text may shift. The spec acknowledges this and falls back to anchoring at the section header, which is coarse but safe.
- Sentence-level granularity (for entries with specific quoted evidence) relies on the sentence appearing in the assembled prose as written. If a section prompt rephrases a sentence during a rewrite loop, the anchor fails silently and falls back to section-header level. The spec doesn't mandate re-validation of anchors after rewrites, so this will happen.

**Configuration flags:** `--comments {native | inline-only | none}` with auto-degrade on tier-1 failure. The spec says "automatic degradation to inline-only on any failure" but doesn't define "any failure" — is it an exception, a timeout, a malformed result? The orchestrator will need clear error-handling rules here. Without them, silent degradation could happen unnoticed, and the user's .docx will have tier-2 anchors when they expected native comments.

**Missing:** No mention of how the assembler handles entries where the `Where in manuscript:` field is still "TBD" (pre-drafting discrepancies detected during plan phase). These entries shouldn't appear in the assembled manuscript at all because they were discovered before drafting reached that section. The spec assumes all register entries have a valid `Where in manuscript:` by assembly time, but the register spec allows "TBD." This is a collision.

---

### Database Knowledge Cards (`database_cards.md`)

**Consistency verdict:** SOUND with the register. The spec correctly defines schema-gotchas with stable IDs and integrates them as register entries. The fallback-phrasing system in snapshot-detection is practical.

**Card schema:** The frontmatter + H2-section structure is clean and practical. One issue: `description_source: unknown` + multi-user accumulation. The spec says descriptions accumulate separated by `---` until curation at release time. This creates a problem:

- If User A contributes a description, and then User B on a different project also contributes, the card contains both separated by `---`. Neither description is "authoritative" until curation. But methods.v1 only reads the card once and picks the first Description section it finds (or concatenates all of them, which is undefined). The spec doesn't say how the prompt consumes a multi-contrib card. Does it use the first description? All? The most recent?
- At release time, a human maintainer must synthesize. But the spec says the synthesis should preserve `description_attribution` as historical credit. This is at odds with the synthesis responsibility — if three contradictory descriptions exist, picking one and discarding two means the discarded contributors don't get attribution in the final shipped card. The privacy-review note mentions "inadvertently include identifying detail" but doesn't address the resolution: do you reach back to those contributors to ask for permission to discard? Do you synthesize a new description from scratch and lose attribution entirely?

These are editorial-process questions, not technical ones, but they're blocking the "multi-user contributions accumulate" premise. Without clear answers, the system will accumulate contradictions that require more-than-light curation.

**Elicitor subskill (`database_card_elicitor.v1`):** The ~120-line estimate assumes the prompt is lightweight and "produces a stub the user can curate further." But the prompt's inputs include `CONTRIBUTOR_ID` anonymization logic, privacy warning ("text will be public"), and a response parser that distinguishes "substantive description" from "I don't know". This is not lightweight. The actual logic will be:

1. Read inputs (DATABASE_NAME, TABLES_OBSERVED, SAMPLE_QUERY_EXCERPTS, EXISTING_CARD_PATH, etc.)
2. Construct a user-facing question ~15 lines
3. **Pause and resume** — user interactivity adds complexity the orchestrator must handle (does the orchestrator invoke the elicitor synchronously and wait for the user, or async with a callback?)
4. Parse the response: is it substantive or "I don't know"? The prompt needs to judge this, which requires its own heuristic or a second prompt.
5. Write/update the card: merge the response into the Description section, set `description_source`, add to `description_attribution`.
6. Optional: extract table stubs and add them.

Steps 3–5 are architectural interactions the spec doesn't detail. The 120-line estimate assumes step 3 is trivial; it's not. The pause-and-resume pattern is documented in SPEC §4.2 but not proven to work from a subskill context (citation_pool.v1 doesn't pause; plan.v1 does, but it's part of the main orchestrator, not a subskill invoked mid-flow).

**Version-fetcher tool (`tools/fetch_db_version.py`):** The ~100-line estimate is realistic for the Python code. But:

- The spec says "SQL directive is opt-in and defaults to skip if no connection." This means every deployed system will fall back to the "Fallback phrasing" path by default. The "Fallback phrasing" is how? It's stored in the card and is a canned string like "at time of writing (no snapshot SHA recorded; pangenome typically rebuilds quarterly per GTDB release cycle)". This is fine, but the tool becomes nearly useless for v0.1 if SQL doesn't work. The implementation cost (connecting to K-BERDL via Spark, handling auth, parsing the directives) is deferred to Phase 4b/5 but the tool ships non-functional. This is a trap for users: they install the tool and get no actionable version info.

**Seed cards:** Five cards listed: fitnessbrowser, pangenome, paperblast, nmdc_arkin, pubmed. The spec says "~80–150 lines per card × 5 = 500–700 lines static material." This is realistic. BUT the cards need to ship with stable, correct Schema Gotchas and Snapshot Convention sections before the register and assembler can use them. A gotcha without a stable ID (e.g., "GOTCHA-pangenome-1") is useless to the register. Writing these correctly will require domain expertise (Adam and Paramvir for BERDL cards, community input for others). This is not a task to rush into Phase 4a.

---

## Cross-Spec Architecture Concerns

### 1. The Register as a Single Point of Failure

The three specs form a dependency chain: register → word comments → database cards (for gotchas). If the register stays empty (prompts don't write to it reliably), the entire chain fails. The specs don't establish a baseline for "reliable writing": what percentage of discrepancies must prompts capture? What's the acceptance threshold?

The smoke tests show citation_pool.v1 and methods.v1 detecting real issues (typos, plan-execution divergence). But neither test included the register-writing discipline. We have no evidence that adding 30 lines of "write to register" instruction to each prompt will result in consistent captures. The per-prompt integration pattern assumes Discipline-pass isolation, which doesn't exist in practice.

**Recommendation:** Defer the register until after Phase 4 implementation. Build the orchestrator first, let it stabilize, then add register-writing as a separate feature. This lets you measure baseline adoption without the risk of building Word comments and cards for a register that's empty.

### 2. The Elicitor's Pause-and-Resume Contract

The database-card elicitor invokes mid-drafting if a card is missing. The prompt pauses, the orchestrator surfaces a gap-fill request, the user responds, and the elicitor resumes.

This pattern is proven in the main drafting loop (plan.v1 pauses for throughline pick, per SPEC §4.2). But the spec doesn't detail how the orchestrator handles an elicitor pause inside a drafting subagent. If methods.v1 is running and detects a missing card, does it:

- Halt and surface a gap-fill request? (This blocks the Methods draft.)
- Invoke the elicitor subskill immediately and wait for the response? (This serializes elicitation and blocks drafting.)
- Log a "missing card" register entry and continue drafting, deferring elicitation to a post-phase step?

The spec doesn't say, and the wording "cache-miss user-elicitation" suggests option 2, but that's not explicitly architectural. This is a load-bearing design choice that should be explicit before Phase 4 implementation starts.

### 3. Overlapping Responsibilities

The reframing_log.md already captures plan-execution discrepancies, pitfall violations, and seed-typo corrections. The register duplicates this information (with finer status tracking). When a register entry closes, it mirrors to reframing_log.

Why not unify them? Add `Status: (open | resolved)` and `Resolution path:` fields to reframing_log.md entries and use a single file for both active and historical tracking?

**The cost of unification:** reframing_log would grow larger and require more parsing. The register's lean active-queue view is lost.

**The benefit of unification:** no duplication, no mirror-on-close logic, simpler orchestrator.

The spec doesn't consider this alternative. It's a design trade-off, not a bug, but the trade-off should be explicit.

---

## Risk Analysis

### Underuse Risk: High

**Scenario:** Every drafting prompt includes "append to register" in Discipline, but in practice:

1. The prompt finishes its main work, hits cost/token limits, and skips the Discipline pass.
2. The prompt finds a potential discrepancy but can't decide which resolution path to choose, so it doesn't write the entry to avoid guessing.
3. The prompt successfully writes an entry but uses the wrong entry number (off-by-one or collision with another prompt's entry written concurrently). The entry is silently dropped on parse.

**Mitigation in the specs:** None. The per-prompt integration assumes good faith and sequential execution. No heartbeat logging ("Discipline pass running..."), no schema validation before the entry is stored, no rollback mechanism if numbering collides.

**What would help:** A dedicated register-manager prompt that the orchestrator invokes after each drafting prompt to validate and re-number entries. Or a Python helper that handles entry numbering atomically instead of per-prompt. The spec doesn't propose either.

### Overuse Risk: Moderate

**Scenario:** Prompts start writing register entries for everything. "This citation is from 2018, not 2019 — entry DR-73 (seed-typo-correction)." "This figure's caption doesn't match the results text — entry DR-74 (report-figure-drift)." The register fills with ~50 entries per draft, users can't triage, and review fatigue sets in.

**Mitigation in the specs:** The type enum is well-chosen and scoped, but there's no guidance on severity or salience. A discrepancy that's already mitigated in the manuscript (the register entry says "Methods text reports the executed threshold") might not warrant a Word comment. But the assembler doesn't filter by severity — it emits comments for all `document-as-known-issue` entries, regardless of impact.

**What would help:** A severity field (e.g., `Severity: critical | moderate | minor`) that the assembler respects. Minor register entries don't become Word comments.

---

## Simpler Alternatives

### Alternative 1: Unify register and reframing_log.md

Replace discrepancy_register.md with a single unified log that tracks status (open/closed). Reframing_log.md gains two fields: `Status: (open | closed)` and `Resolution path: (self-resolvable | user-resolvable | document-as-known-issue | accepted-as-no-action)`.

The unified file grows linearly as work completes. Closed entries remain for audit; new entries always append. The word-comment assembler reads the same file, filtering for `Status: open AND Resolution path: document-as-known-issue`.

**Cost:** The reframing_log.md file becomes larger (500+ lines after a full draft cycle, vs. ~100 for register + ~100 for log today). Parsing is more complex (extract open entries, filter by status and resolution path, sort by entry number).

**Benefit:** One source of truth, no mirroring logic, simpler orchestrator (one file to initialize and append to, not two). The complexity is push to the query side (filtering), not the write side.

**Verdict:** This is simpler and worth considering. The spec should at least justify why two files are better than one.

---

### Alternative 2: Defer database cards entirely; inline placeholders in methods.v1

Instead of a card system, methods.v1 simply outputs `[MISSING_DB_DESCRIPTION: kbase_ke_pangenome]` as a placeholder. The user manually fills in the description before submission. The card system becomes a post-publication enhancement: users who want to contribute cards can do so, but it's not a blocker for Phase 4a.

**Cost:** Methods sections contain placeholders. Users must manually curate. The description won't be reusable across projects.

**Benefit:** Card infrastructure (elicitor, version-fetcher, multi-user curation, privacy review) is entirely deferred. Phase 4a is lighter.

**Verdict:** This is appropriate for v0.1. The card system is nice-to-have infrastructure that doesn't unlock any new behavior until the elicitor is functional. Simpler to defer.

---

### Alternative 3: Emit register entries as inline `[KI-N]` anchors, always

Skip tier-1 (native Word XML) entirely. Always emit tier-2 (inline italicized `[KI-N]` anchors + Known Issues appendix). This avoids the lxml complexity and cross-platform testing.

**Cost:** .docx output is less polished than native comments (users see `[KI-3]` in-line rather than in a margin). Appendix is always present even if the user wanted it hidden.

**Benefit:** ~150 lines of lxml code disappears. Cross-platform testing is not needed. Implementation cost drops from ~5 hours to ~1 hour (assembler logic only).

**Verdict:** Reasonable for v0.1. Native Word comments are a UX enhancement, not a blocker. Tier-2 is functional and sufficient.

---

## Phase Ordering and Scope-Creep Verdict

The original 4-week spike scope was:

1. 10 prompts (done).
2. Orchestrator + smoke-test validation.
3. Writeup (handoff artifact).

These three specs add:

- Discrepancy register: 4–6 hours (per-prompt edits + orchestrator integration + state management).
- Word comments at assembly: 5–7 hours (word_comments.py + assemble_docx.py mods + cross-platform testing).
- Database cards: 9–13 hours (card infrastructure + elicitor + version-fetcher + seed-card curation).

**Total: 18–26 hours of unplanned work.**

The spike has 4 weeks (160 hours). The original scope expected ~80–100 hours for orchestrator + smoke test + writeup. These specs consume 18–26 hours that were earmarked for orchestrator implementation and validation.

**Verdict: SCOPE CREEP.** These specs are valuable, but they should not ship before the orchestrator is functional and the prompts are known to write to the register reliably. Deferring all three to Phase 4b (after orchestrator is working) is the right call. If you push them into Phase 4a, you risk:

1. Orchestrator slips because infra work (register management, word-comment anchor resolution) takes longer than estimated.
2. You discover the register is empty (prompts don't write to it reliably) and the word-comment system has no data to work with.
3. The spike's writeup (the handoff artifact) ends up documenting half-finished infrastructure instead of a stable, tested design.

**Recommendation:** Shelve these three specs as "Phase 4b forward-planning" documents. They are well-reasoned and should inform Phase 4 architecture, but they should not be implementation priorities during the spike. Finish the orchestrator, prove the 10 prompts work end-to-end, deliver the writeup. Then Phase 4b picks up the register, cards, and comments.

---

## Prompt-Design Impact

The "Per-prompt integration pattern" in discrepancy_register.md adds ~30 lines per prompt. The existing prompts are already long (citation_pool 505 lines, discussion 542 lines per the memory). Adding register-writing logic compounds length pressure.

**Cumulative impact:**
- citation_pool.v1: +30 lines (now ~535).
- methods.v1: +30 lines (likely ~550+).
- results.v1: +30 lines (likely ~500+).
- discussion.v1: +30 lines (now ~572).

The total is creeping toward 4000+ lines across 10 prompts. This is manageable, but it's moving in the direction of "too long to reason about." The smoke test shows individual prompts work, but we haven't tested the full 10-prompt suite end-to-end.

**Recommendation:** If you add the register discipline, do it cautiously. Consider:

1. Writing the register discipline once in a shared library/template and having each prompt import it, rather than repeating the logic in each prompt's Discipline section.
2. Measuring the impact: add the logic to one prompt, run a live test, measure latency/cost. If it adds >5% cost, reconsider.

---

## Three Concrete Edits

### Edit 1: Discrepancy Register — Add an Escape Hatch for Missing Manuscript Location

**File:** `discrepancy_register.md`, §"Schema (per-entry, markdown)"

**Current text:**
```
- **Where in manuscript:** {section + paragraph or "TBD" if pre-drafting}
```

**Proposed new text:**
```
- **Where in manuscript:** {section + paragraph, "TBD" if pre-drafting, or "[location unknown at assembly time]" if not found during assembly}
```

**Rationale:** The current schema allows "TBD" but doesn't clarify what happens when the section is drafted and the location is still unknown (e.g., a Methods discrepancy detected during plan phase, but Methods drafting hasn't happened yet). Clarifying the third state ("[location unknown at assembly time]") makes the assembler's fallback behavior explicit and prevents silent drops.

---

### Edit 2: Database Cards — Clarify How Methods.v1 Consumes Multi-Contributor Cards

**File:** `database_cards.md`, §"How prompts consume cards", subsection "1. methods.v1"

**Current text:**
```
When the provenance file's "Spark / K-BERDL Queries" section names
a database (e.g., `kbase_ke_pangenome`), `methods.v1` reads
`reference/databases/kbase_ke_pangenome.md` and uses:

- The Description for the Datasets subsection's database
  characterization.
```

**Proposed new text:**
```
When the provenance file's "Spark / K-BERDL Queries" section names
a database (e.g., `kbase_ke_pangenome`), `methods.v1` reads
`reference/databases/kbase_ke_pangenome.md` and uses:

- The Description for the Datasets subsection's database
  characterization. If multiple descriptions exist (separated by `---`
  from multi-user contributions), use the first description and flag
  a `missing-snapshot-version` register entry with proposed-resolution
  path `user-resolvable` to ask the user for clarification on which
  description is authoritative.
```

**Rationale:** The card schema allows multi-user descriptions to accumulate, but methods.v1's consumption is undefined. This edit makes the ambiguity explicit and ensures the register surfaces it for user judgment.

---

### Edit 3: Word Comments — Specify Tier-1 Failure Conditions

**File:** `word_comments_at_assembly.md`, §"Two-tier emission strategy"

**Current text:**
```
If tier-1 emission fails (python-docx version mismatch, lxml issue,
etc.), or if the user opts for a comment-free .docx via
configuration, the assembler degrades to:
```

**Proposed new text:**
```
If tier-1 emission fails (python-docx version mismatch, lxml issue,
exception during XML manipulation, malformed comment metadata, timeout
on >100 simultaneous comments, etc.), or if the user opts for a
comment-free .docx via configuration, the assembler degrades to:
```

**Rationale:** The current text says "python-docx version mismatch, lxml issue, etc." but doesn't enumerate realistic failure conditions. Specifying "exception during XML manipulation" and "timeout on >100 comments" clarifies the contract and helps the orchestrator decide whether to retry or degrade.

---

## Final Takeaway

These specs are architecturally coherent and demonstrate strong design thinking. The register's lifecycle is clear, the word-comment assembler is a practical two-tier design, and the database cards address a real need. **But they are not ready for Phase 4a implementation.** The orchestrator must be proven to work first. Once the orchestrator is stable and prompts are known to write register entries reliably, Phase 4b can pick up this work with confidence. Pushing all three into Phase 4a risks delivering half-finished infrastructure instead of a complete, tested system. Defer them.
