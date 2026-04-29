# beril-paper-writer-skill — v0.5 release notes

**v0.5.0 release date:** 2026-04-29
**Status:** v0.5 — caption-quality tightening (point release on top of
v0.4's caption-richness tier). Pre-1.0; expect breaking changes between
minor versions until the architectural shape stabilizes.

This is a focused point release addressing two findings from the v0.4
visual review (per `RELEASE_NOTES_v0_4.md` known limitations):

1. **Boilerplate-heavy notebook prose passed the v0.4 sufficiency gate
   but produced poor captions** in the docx. The v0.4 gate used
   `_strip_heading_lines` (only `#`-prefixed lines dropped); keyword-
   tagged dev-process boilerplate (`Purpose:`, `Approach:`, `Sections:`
   etc.) survived and inflated the post-strip word count, so figures
   like fig 8/9/10 in `functional_dark_matter` draft_3 stayed in the
   deterministic path with descriptor-derived captions full of
   notebook-organization metadata.

2. **Multi-panel figures truncated mid-word** at the universal 200-word
   cap on Source 4 LLM captions. Fig 8 (4 panels) couldn't describe
   panels B/C/D within budget.

---

## What v0.5 changes since v0.4.0

### Sufficiency-gate now uses the aggressive boilerplate strip

`paper_writer_helpers.py:_passes_sufficiency_gate` swaps
`_strip_heading_lines` for `_strip_prose_for_inline` (the v0.4 Phase 5b
strip that drops keyword headers in both bold idioms, inline-cascade
keyword chains, project-internal artifact references, numbered list
items, and bold-Bold:Bold patterns). Figures whose notebook prose is
mostly boilerplate now correctly fail the gate and route to Source 4.

The Source 4 LLM has explicit anti-pattern discipline against the same
boilerplate (added in v0.4 Phase 5b prompt), so figures forced through
that path get clean ICMJE-conventional captions.

**Effect on `functional_dark_matter` draft_3:** figures 8/9/10
(previously deterministic with boilerplate-laden descriptions) now route
to Source 4. Their captions become panel-by-panel descriptions of the
figure content, not notebook-organization metadata.

### Panel-count-scaled `max_words` for Source 4 captions

New `_caption_max_words(panel_count)` helper:

```
panel_count == 0  → 200 words (single-panel default)
panel_count >= 1  → 200 + 50 * panel_count
```

A 4-panel figure (like fig 8) now gets a 400-word budget; the LLM has
room to describe each panel without truncating mid-word. ICMJE
convention permits 300-400 words for complex multi-panel legends.

`cmd_build_caption_bundles` computes `panel_count` from
`descriptor.panels` (AST-detected). The `--max-words` CLI flag is
preserved as an override for testing but defaults to None so the
formula prevails.

### Test additions

- `_caption_max_words` formula values (0/1/2/3/4/6/-1 panels).
- v0.5 sufficiency gate: boilerplate-heavy prose fails; substantive
  prose passes.
- `cmd_build_caption_bundles` writes panel-count-scaled `max_words`
  field per bundle; integration test confirms 4-panel figure gets 400.
- `_build_draft` test helper extended to emit `_Panels:_` block in v2
  inventory (so panel parsing round-trips through the synthetic
  fixture).

**Tests:** 430/430 pass (v0.4: 426/426; v0.5 adds 4).

### LLM cost on draft_3 re-validation

Validating the v0.5 changes against draft_3 routed figures 8/9/10
through Source 4 (~$0.20 for 3 invocations). The v0.4-shipped figures
1-5 already had Source 4 captions (already clean post-Phase 5b prompt
update). Net cost of v0.5 validation: $0.20.

---

## Files changed since v0.4.0

```
pyproject.toml + src/beril_paper_writer/__init__.py: 0.4.0 → 0.5.0
RELEASE_NOTES_v0_5.md  (NEW)
smoke-test/v0_5_punch_list.md  (NEW)
smoke-test/v0_5_0_ship_runbook.sh  (NEW)
src/beril_paper_writer/skill/tools/paper_writer_helpers.py
  - _caption_max_words helper added
  - _passes_sufficiency_gate uses _strip_prose_for_inline
  - cmd_build_caption_bundles uses panel-count-scaled max_words
tests/unit/test_embed_figures.py
  - 4 new tests; _build_draft helper extended for panels
.commit-message-v0_5_0.txt  (NEW)
```

`_strip_heading_lines` is no longer called by the gate but is retained
in the source as a stable public-ish helper (other future code paths
may want simple heading-strip semantics).

---

## Architectural lessons confirmed across v0.5 (do not regress)

- **Visual-review feedback drives the next cycle.** The v0.4 ship had
  GREEN numeric gates but visual review surfaced boilerplate residue
  on three figures. v0.5 closes that loop with a single-line gate
  change + small formula. Lesson: a passing test suite is necessary
  but not sufficient; visual or end-user review remains the canonical
  quality signal.

- **Sufficiency-gate predicate sharing across pipeline stages.** The
  same strip function (`_strip_prose_for_inline`) is now used both at
  description-render time (Phase 3) and at gate-decision time (Phase
  4c / v0.5). Sharing keeps the "what counts as real prose" definition
  consistent end-to-end. Don't drift — if the strip evolves further,
  the gate semantics evolve with it automatically.

- **Word-budget-scales-with-content-complexity is honest pipeline
  design.** Universal caps like "200 words for everything" fail on
  the long tail (multi-panel figures, dense statistical reporting).
  Scale the budget by a measurable proxy (panel count) rather than
  forcing the LLM to truncate or hallucinate.

---

## Upgrade notes (v0.4 → v0.5)

- Existing draft_N directories produced by v0.4 will see DIFFERENT
  sufficiency-gate decisions on the next `phase_caption_synthesis`
  invocation. Specifically: boilerplate-heavy figures that v0.4
  marked deterministic will now route to Source 4. Cost impact is
  ~$0.06/figure forced; for `functional_dark_matter` this is +$0.20
  per draft re-run. Acceptable for the quality improvement.
- Multi-panel figures will get larger Source 4 captions (up to 400+
  words for 4-panel; 500 for 6-panel). Visual review may be needed
  to confirm the LLM uses the additional budget productively.
- No breaking changes to public APIs; `_caption_max_words` is a new
  internal helper.

---

## v0.5 known limitations + v0.6 backlog

Carried forward from v0.4 (still relevant):
- **Tables (Tier 9) — top v0.6 candidate.** Not yet supported.
  `extract_tables.py` + `tables_inventory.md` + `tables_manifest.tsv`
  + `phase_check_tables_manifest` + `phase_embed_tables`. Mirrors the
  figures architecture; multi-week cycle.
- **`gridspec.GridSpec` / `subplot2grid` panel layouts**: not
  detected by `_extract_plot_calls`.
- **Stratum 3 multi-panel** (PIL/Inkscape post-hoc-composed
  figures): vision API required.
- **`--max-cost-usd` flag on Python CLI**: parsed by
  `paper_writer.sh` directly but not by `commands/draft.py` /
  `commands/continue_run.py` argparse.
- **Python 3.14 test-collection errors** on 8 pre-existing tests
  (`test_validate_manuscript.py` and others). v0.5 source still
  collects cleanly on Python 3.10/3.12.
- **`phase_caption_synthesis` skip-existing-captions**: currently
  re-invokes LLM on every run; should detect
  `audit/figure_caption_<N>.md` existing and skip.
- **Recovery runbook `reviews/` recreation**: the v0.4 retest
  recovery runbook moved `reviews/` to `.recovery_backup/` without
  recreating it; `stream_progress.py` then crashed.

New from v0.5 visual-review:
- *(no new gaps surfaced; v0.5 closes the residue from v0.4 visual
  review)*

---

*Authored 2026-04-29 alongside the v0.5.0 ship cycle. Phase status
sections in `smoke-test/v0_5_punch_list.md`. Memory entry:
`project_paper_writer_v0_5.md` (supersedes
`project_paper_writer_v0_4.md`).*
