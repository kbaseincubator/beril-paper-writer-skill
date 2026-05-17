# Extended reframing-log schema — design spec (v0.1 forward-looking)

**Status:** spec-additions (not yet merged into SPEC.md / LAYOUT.md /
the prompts). Drafted 2026-04-25 after the citation_pool.v1 +
methods.v1 smoke tests; **simplified 2026-04-26** following spec
review by collapsing the originally-proposed separate
"discrepancy_register.md" file into additive fields on the existing
SPEC §5.6 reframing-log schema.

**Filename note:** this file is named `discrepancy_register.md` for
historical continuity with the original review trail. The active
proposal in this file is now: **add two fields to the existing
reframing_log.md, do NOT create a separate register file.** Renaming
the file is deferred to keep cross-references stable; the title and
content reflect the simplified design.

---

## Why this exists

The smoke-test runs of `citation_pool.v1` and `methods.v1` surfaced
unexpected-but-good defensive behaviors that the existing
SPEC §5.6 reframing-log schema doesn't model cleanly:

- **citation_pool.v1** corrected 5 real PMID typos in the project's
  curated `references.md` — the prompt's verify-by-resolution
  discipline is doing higher-value work than originally designed
  for.
- **methods.v1** caught a real plan-vs-execution divergence in the
  project's threshold filters and logged it.

Both are **detected by the prompt during drafting**, but the
existing schema only models *resolved* events (auto-fixed,
escalated, accepted-as-limitation, etc.). It can't model
"detected-but-not-yet-resolved" — the active queue of issues that
need a follow-on agent, user input, or surfacing in the manuscript.

The original review proposed a separate `discrepancy_register.md`
file with explicit lifecycle (open → resolved → mirrored to
reframing_log). That design carries lifecycle complexity (move-not-
copy on closure, GC of closed entries, cross-draft persistence)
without proportional value. **Simplification:** add `Status:` and
`Resolution path:` fields to the existing reframing-log schema and
let one file do both jobs.

## The two new fields

Extend the SPEC §5.6 entry schema:

```markdown
## Entry {N} — {ISO timestamp} — type: {type}

- **Status:** open | closed
- **Resolution path:** self-resolvable | user-resolvable | document-as-known-issue | accepted-as-no-action | (or omit if Status is "closed" and the existing Resolution field describes the closure)
- **Issue:** {what was found / changed}
- **Source:** {REPORT.md §X | validator M_n | notebook X cell Y | RESEARCH_PLAN §Z}
- **Manuscript impact:** {which section(s); what language was added}
- **Where in manuscript:** {section + paragraph; "TBD" if pre-drafting (the section hasn't been drafted yet); or "[location unknown at assembly time]" if drafting completed but the assembler couldn't locate the anchor span}
- **Resolution:** {auto-fixed | escalated | accepted as Limitations | user-modified | scope-narrowed | documented-as-known-issue} (when Status: closed)
- **Note:** {context for future reviewers; one paragraph max}

---
```

Three fields are added or extended:

1. **`Status:`** (NEW) — `open` | `closed`. Open entries are the
   active queue; closed entries are the audit history. The same
   file holds both.
2. **`Resolution path:`** (NEW) — only meaningful when `Status:
   open`. Names which of the four paths the entry is on:
   - `self-resolvable` — a follow-on agent (provenance_audit /
     reframer) can ground-truth this.
   - `user-resolvable` — needs human judgment; orchestrator queues
     a gap-fill request.
   - `document-as-known-issue` — divergence the project's evidence
     can't resolve; assembler emits inline anchor + appendix entry
     in the .docx (per `word_comments_at_assembly.md`).
   - `accepted-as-no-action` — informational only.
3. **`Where in manuscript:`** (NEW field, NOT a `Resolution path`
   variant) — names the section + paragraph where the entry's
   issue lives in the manuscript. Used by the assembler for anchor
   placement. Allows three values: a real section/paragraph, "TBD"
   (pre-drafting), or "[location unknown at assembly time]"
   (assembler couldn't locate).

The existing `Resolution:` field remains; it's populated when
`Status: closed`.

## Lifecycle

```
                  +---------------------+
                  | Detected by prompt  |
                  +---------------------+
                            |
                            v
              +------------- reframing_log.md -------+
              | (Status: open)                       |
              | (Resolution path: one of four)       |
              +-+----------+----------+--------+-----+
                |          |          |        |
   self-resolvable:        |          |        |
   provenance_audit / reframer        |        |
   ground-truths; updates the entry   |        |
   to Status: closed with Resolution. |        |
                                      |        |
   user-resolvable: orchestrator queues       |
   gap-fill in analysis_requests.md;          |
   user response triggers entry update        |
   to Status: closed.                         |
                                              |
   document-as-known-issue: stays open       |
   through assembly. Assembler emits inline   |
   anchor + appendix entry; updates entry     |
   to Status: closed with Resolution:         |
   "documented-as-known-issue".               |
                                              |
   accepted-as-no-action: orchestrator        |
   surfaces in closing summary; user marks    |
   Status: closed (manual override).
```

After resolution, the entry stays in `reframing_log.md`; the
`Status:` flips from `open` to `closed`. No file moves, no
mirroring logic, no GC. The log grows monotonically; the active
queue is just the open entries.

## Type enum (extends existing SPEC §5.6)

The existing SPEC §5.6 type enum is `reframing | validator-escalated
| accepted-limitation | plan-execution-discrepancy | manual-override`.
Extend with these (carried over from the smoke-test review):

| Type | Detected by | Example |
|---|---|---|
| `reframing` (existing) | any drafter | scope-narrowing; demoted-to-appendix |
| `validator-escalated` (existing) | validator dispatcher | M-tier failure escalated |
| `accepted-limitation` (existing) | discussion.v1 | folded into Limitations |
| `plan-execution-discrepancy` (existing) | methods.v1 | plan says X, notebook does Y |
| `manual-override` (existing) | user | user-resolved unilaterally |
| `seed-typo-correction` (NEW) | citation_pool.v1 | references.md PMID doesn't resolve |
| `pitfall-violation` (NEW) | methods.v1, provenance_audit.v1 | known DB gotcha not mitigated |
| `unverifiable-step` (NEW) | methods.v1 | implied step has no notebook cell |
| `threshold-ambiguity` (NEW) | methods.v1 | plan and notebooks use different thresholds |
| `missing-snapshot-version` (NEW) | methods.v1, citation_pool.v1 | DB snapshot SHA not in notebooks |
| `report-figure-drift` (NEW) | reframer.v1, results.v1 | caption claims X; figure shows Y |
| `inter-notebook-drift` (NEW) | provenance_audit.v1 | NB01 reports N; NB05 reports M |
| `self-contradiction` (NEW) | reframer.v1 | REPORT Finding 3 vs Finding 7 conflict |

## Per-prompt integration pattern

Every drafting prompt (citation_pool, methods, results, discussion,
intro, abstract) gains a small section in its Discipline pass:

> **Reframing-log entries.** When you find a discrepancy that
> requires resolution beyond your in-pass capability, append an
> entry to `REFRAMING_LOG_PATH` per the SPEC §5.6 schema (extended
> with `Status:` and `Resolution path:` fields per
> `spec-additions/discrepancy_register.md`). For active discrepancies
> needing follow-on work, set `Status: open` and pick a
> `Resolution path:` from {self-resolvable | user-resolvable |
> document-as-known-issue | accepted-as-no-action}. For events you
> are resolving inline (e.g., applying a fix during your own pass),
> set `Status: closed` directly with the appropriate `Resolution:`
> as before.
>
> Append; do not modify earlier entries' Status without
> orchestrator coordination. Use the next sequential `Entry {N}`
> from `max(N) + 1` over existing entries (per LAYOUT's
> "Reframing-log entry-numbering contract").

Special cases:

- **`provenance_audit.v1`** (new prompt, runs pre-drafting):
  appends entries from inter-artifact integrity checks before any
  section drafts.
- **`reframer.v1`** (existing post-drafting audit): processes
  `Status: open AND Resolution path: self-resolvable` entries by
  ground-truthing against the finalized drafts; flips them to
  `Status: closed` with Resolution.
- **`citation_pool.v1`**: seed-typo corrections are appended with
  `Status: closed` directly (the correction was applied inline) and
  `Resolution: auto-fixed; corrected via WebSearch verification`.

## How the assembler uses it

At .docx emission time, the assembler reads `reframing_log.md` and
filters for `Status: open AND Resolution path: document-as-known-issue`.
Each matching entry produces:

- An inline anchor `[KI-{N}]` in the manuscript at the
  `Where in manuscript:` location.
- An entry in the "Known Issues at Draft Time" appendix at the end
  of the manuscript.

After emission, the assembler updates each emitted entry's
`Status:` to `closed` with `Resolution: documented-as-known-issue`.

See `word_comments_at_assembly.md` for the emission mechanism
(simplified to tier-2-only after the spec review).

## Cross-draft behavior

When draft_N+1 is started from draft_N's state, the orchestrator
copies `reframing_log.md` forward. Entries with `Status: open` from
draft_N are still open in draft_N+1; entries with `Status: closed`
are still closed. The orchestrator may surface still-open entries
to the user at draft_N+1 startup ("3 open entries from draft_1
need attention; review now or defer?").

## What's not in v0.1

- **Severity tagging.** The reviewer noted overuse risk: every
  minor thing gets registered, users can't triage. A future
  enhancement adds a `Severity: critical | moderate | minor` field;
  the assembler respects severity for known-issue surfacing
  (minor entries don't become inline anchors). Defer to v0.2.
- **Re-open of closed entries.** Currently entries flip from open
  to closed once. If a closed entry's resolution is later disputed
  (user changes mind), the user manually appends a new entry
  referencing the closed one. v0.1 doesn't model re-open.
- **Concurrent-write coordination.** LAYOUT's reframing-log
  numbering contract assumes sequential v0.1 execution. If
  parallelization is introduced later, entry-numbering needs
  orchestrator-side coordination as documented in the contract.

## Open questions

1. **Bulk close on user dismissal.** When a user reviews a batch of
   `Status: open` entries and dismisses several at once, is there a
   bulk-close affordance, or must each entry be individually
   resolved? Current proposal: individual is fine for v0.1; bulk-
   dismissal is post-MVP.
2. **Surfacing at orchestrator startup.** When a fresh conversation
   resumes a paused draft, the orchestrator presumably summarizes
   open entries. What's the threshold for surfacing automatically
   vs. on-demand-only? Current proposal: always summarize at
   startup; the orchestrator's startup banner includes "N open
   reframing-log entries from prior runs."
