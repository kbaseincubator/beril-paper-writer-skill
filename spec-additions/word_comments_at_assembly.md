# Known-issue surfacing at assembly time — design spec (v0.1 forward-looking)

**Status:** spec-additions (not yet merged into LAYOUT.md /
DECISIONS.md / `tools/assemble_docx.py`). Drafted 2026-04-25;
**simplified 2026-04-26** following spec review by dropping the
native-Word-comment XML emission (tier-1) and committing to inline
anchor + appendix surfacing (tier-2-only) for v0.1.

**Filename note:** the file retains its name `word_comments_at_assembly.md`
for review-trail continuity, even though the simplified design
emits inline anchors + an appendix rather than literal Word comments.
Native Word comments are deferred to v0.2 if user demand surfaces
during real use.

---

## Why this exists

The companion spec `discrepancy_register.md` (which proposes
extending the reframing-log schema with `Status: open|closed` and
`Resolution path:` fields) defines `document-as-known-issue` as one
of four resolution paths. Some discrepancies cannot be fixed by
either the writer or the user — the project's evidence genuinely
doesn't support a resolution. The assembler is the right component
to surface these because it has access to (a) the reframing-log,
(b) the assembled manuscript prose, and (c) the python-docx
Document object before final save.

## What the assembler emits (simplified, tier-2-only)

For every reframing-log entry with `Status: open AND Resolution
path: document-as-known-issue`, the assembler emits **two
artifacts** in the .docx output:

1. **An inline anchor** at the manuscript span the entry's
   `Where in manuscript:` field names: an italicized `[KI-N]`
   marker (Known Issue N) embedded in the prose. Format:

   ```
   ...the analysis identifies 95 dark genes with cross-organism
   concordance *[KI-3]*. Cross-organism concordance is...
   ```

   The anchor is unobtrusive (one short bracketed marker per entry)
   and platform-independent (any word processor renders italicized
   bracketed text correctly).

2. **An entry in the "Known Issues at Draft Time" appendix** —
   a new section appended at the end of the manuscript (after
   Limitations, before References). One entry per `[KI-N]` anchor,
   with the full text of the reframing-log entry's `Issue:`,
   `Source:`, `Manuscript impact:`, and `Note:` fields, plus a
   pointer back to the inline anchor's location. Format:

   ```markdown
   ## Known Issues at Draft Time

   This section lists discrepancies the writer surfaced during
   drafting that the project's evidence could not resolve. Each
   issue is anchored in the body via [KI-N] markers; reviewers
   are encouraged to consider these explicitly before submission.

   ### KI-3 (Methods §"Quality Control / Filters", paragraph 1)

   - **Issue:** Fitness-effect threshold mismatch between
     RESEARCH_PLAN and notebook implementation.
   - **Source:** RESEARCH_PLAN.md §"Phase 1: Integration & Census"
     vs notebooks 01_integration_census.ipynb cell 11,
     06_robustness_checks.ipynb cell 19.
   - **Manuscript impact:** Methods §"Quality Control / Filters"
     reports the executed threshold (|fit| > 2, |t| > 4 for
     "strong"); the looser plan-specified threshold appears in
     provenance SQL but is not the primary filter applied.
   - **Note:** The plan's intent was to use the looser threshold
     for the initial census and the stricter threshold for
     prioritization; the notebooks collapsed this into a single
     stricter threshold applied throughout.

   ### KI-{N+1} ...
   ```

The appendix is the audit trail; the inline anchors are the
reviewer-experience layer. Together they convey the same
information that native Word comments would, but in plain markdown
that survives any rendering pipeline.

## Why not native Word comments

The original spec proposed a tier-1 emission strategy via direct
lxml manipulation of `word/comments.xml` + `commentRangeStart/End`
markers. The reviewer's verdict (per
`spec-additions/spec_review_2026_04_25.md`):

- ~150 lines of fragile lxml code (depends on python-docx internal
  package layout).
- Cross-platform .docx rendering testing burden (Word, LibreOffice,
  Google Docs, Apple Pages each render OOXML comments differently).
- Tier-2 conveys the same information; tier-1 is a UX nicety, not
  a blocker.

For v0.1, the simpler path wins. If users in real use ask for
margin-style comments, tier-1 is added in v0.2 with the lxml
helper as an opt-in feature.

## Mapping reframing-log entries to anchor spans

Each entry's `Where in manuscript:` field names the section +
paragraph (e.g., `Methods §"Statistical Analysis", para 2` or
`Results §"Cross-organism concordance", sentence about OG10428`).
The assembler:

1. Locates the section in the assembled markdown via
   `## Section Name` matching.
2. Locates the paragraph or sentence within that section. For
   paragraph-level granularity (the common case), the inline anchor
   appends to the end of the matching paragraph. For sentence-level
   granularity (when the entry's evidence quotes specific
   phrasing), the anchor is inserted after the sentence containing
   the quoted phrase.
3. **Fallback** when neither section nor paragraph can be located
   (manuscript edited since entry was written, or entry's
   `Where in manuscript:` is `[location unknown at assembly time]`):
   - Skip the inline anchor (no `[KI-N]` in prose).
   - Still emit the appendix entry with location-unknown noted.
   This means some appendix entries may not have inline anchors;
   that's accepted.

## Configuration

In the slash-command interface (LAYOUT §"Slash commands"):

```
/beril-paper-writer-assemble <draft_dir>
    [--format docx|pdf|md]
    [--known-issues true|false]
    [--known-issues-section true|false]
```

Defaults:
- `--format docx`
- `--known-issues true` — inline anchors + appendix both emitted
- `--known-issues-section true` — appendix is always emitted when
  the log has any `document-as-known-issue` entries

`--known-issues false` suppresses both inline anchors and the
appendix (suitable for journal-submission render where the
manuscript should look clean). The reframing-log entries are still
in the audit trail; the .docx just doesn't surface them.

## Round-trip with reframing_log.md

When the assembler emits the inline anchor + appendix entry for a
reframing-log entry, it transitions the entry's status:

```
Status: closed
Resolution: documented-as-known-issue (emitted as KI-{N} in manuscript.docx)
```

The orchestrator updates the entry in `reframing_log.md` after the
.docx is successfully written. Atomic transaction — if .docx write
fails, the entry stays `Status: open` and the orchestrator surfaces
the failure to the user.

## What the assembler does NOT do

- **It does not auto-resolve other entries.** Resolution paths
  `self-resolvable` and `user-resolvable` must be closed by their
  respective agents (provenance_audit / reframer / orchestrator
  surfacing gap-fills) BEFORE assembly. If they're still open at
  assembly time, the assembler logs a warning and surfaces them as
  `document-as-known-issue` for that emission (with a note that the
  resolution path was different).
- **It does not modify entries with `Status: closed`.** Already-
  closed entries are read-only at assembly time.
- **It does not insert anchors for `accepted-as-no-action`
  entries.** Those are informational and do not surface in the
  manuscript.

## Implementation sequence

1. **(Phase 4a, ~2 hours)** Extend `tools/assemble_docx.py` to read
   `reframing_log.md`, filter for `Status: open AND
   Resolution path: document-as-known-issue`, and emit the inline
   anchors + appendix.
2. **(Phase 4a, ~30 min)** Add the `Known Issues at Draft Time`
   appendix-builder. Markdown formatting; no docx-specific work.
3. **(Phase 4b)** Wire the orchestrator to transition entries to
   `Status: closed` after .docx assembly. ~15 lines in
   `paper_writer.sh`.

Total: ~2.5 hours of focused work. No new module needed (extends
existing `assemble_docx.py`); no python-docx XML manipulation; no
cross-platform testing burden.

Compare to original spec's ~5 hours + cross-platform validation.
**Net savings: ~3 hours plus the validation work that wasn't in
the original budget.**

## Open questions

1. **Markdown intermediate.** The `--format md` output skips
   .docx generation entirely. The inline anchors and appendix
   degrade gracefully when read directly as markdown — this is
   actually a pleasant property of the tier-2-only design.
2. **PDF rendering.** `--format pdf` (post-MVP) goes through .docx
   → pandoc → PDF. The inline `[KI-3]` markers and the appendix
   render cleanly in PDF without special handling.
