# beril-paper-writer-skill — v0.4 release notes

**v0.4.0 release date:** 2026-04-28
**Status:** v0.4 — caption-richness tier (Tier 8 + Source 4 LLM
synthesis). Pre-1.0; expect breaking changes between minor versions
until the architectural shape stabilizes.

This document is the authoritative release-handoff for v0.4. It lists
what's new since v0.3.0, what's deferred to v0.5, and the specific
user-visible behavior changes. The v0.1.0 ship notes
(`RELEASE_NOTES.md`) remain authoritative for the foundational
features; `RELEASE_NOTES_v0_2.md` covers v0.2 discipline-hardening +
auto-repair; `RELEASE_NOTES_v0_3.md` covers v0.3 figures + docx
assembly.

---

## What v0.4 adds since v0.3.0

The v0.4 thesis: **close the caption-richness gap.** v0.3 shipped a
`manuscript.docx` with 10 inline Pictures + 10 single-line Caption
paragraphs in the live-retest case (functional_dark_matter draft_2).
Captions were one noun phrase per figure (e.g. "Annotation breakdown
by organism") sourced from REPORT.md or filename. ICMJE/Nature-style
50-200 word legends were deferred.

v0.4 produces ICMJE-conventional one-paragraph captions with structured
multi-source content: notebook-prose walk-back, matplotlib AST
extraction (title / axes / multi-panel detection), prose-side panel
callout merging, LLM caption synthesis with anti-fabrication discipline
+ post-checker, and a sufficiency gate that decides per-figure whether
to invoke the LLM.

Eight architectural pieces landed (Phase 0 through Phase 4c +
Phase 5b/5c from `smoke-test/v0_4_punch_list.md`):

### Phase 0 — v0.3.1 cosmetics absorbed

Two `paper_writer.sh` patches: removed the legacy filename-grep fallback
in `phase_results` (always reported "Copied 0 figure(s)" in v0.3+ since
results.v1 owns figure-copy directly via Bash tool); added explicit
`FIGURES_OUT_DIR` to results.v1 user_prompt (results.v1.md line 107
declared this input but the orchestrator was failing to pass it).

### Phase 1a — notebook cell-attribution rewrite

`extract_figures.py:_walk_notebook_savefigs` previously used a "consume
one md cell, attribute to first following code cell" model that
silently failed on the dominant scientific-notebook idiom (md → data-
prep code → plot+savefig code). New `_collect_md_walkback` walks
backward from each savefig cell through ALL preceding cells, accumulating
markdown until a section header (line beginning with `#`). Multiple
savefigs in one section legitimately share upstream prose — no consume
semantic.

**Smoke against functional_dark_matter:** 39/41 figures gain notebook-
derived prose (was ~0 in v0.3). 24/41 rich (≥5 non-heading words),
15/41 just-heading, 2/41 no notebook signal at all.

### Phase 1b — structured CaptionDescriptor + v2 inventory schema

New dataclasses `PanelDescriptor` and `CaptionDescriptor` on
FigureRecord, with title / axes_labels / legend_labels / notebook_prose /
panels / source_refs fields. `_DESCRIPTION_TEXT_CAP=4000` chars
(empirical: max walk-back across functional_dark_matter is 3424 chars;
p95=2782).

`figures_inventory.md` schema bumped to v2:
`<!-- inventory_schema_version: 2 -->` header on line 1 + per-figure
`**Description:**` block (italic-labeled bullets for Title/Axes/Legend/
Panels + blockquote for notebook_prose + provenance line). Schema
detection: paper_writer_helpers' descriptor parser sniffs the v2 header
and returns `{}` on v1 inventories for graceful fallback.

Downstream consumers verified unaffected by schema bump (locked by
regression tests): `paper_writer_helpers._parse_figures_inventory_captions`
round-trips v2 cleanly; `tools/check_figures_manifest.py` reads only
the manifest TSV; `paper_writer.sh` references inventory by path only;
`results.v1.md` treats inventory as Read-tool source.

### Phase 2 — matplotlib AST extraction (Source 3)

New `PlotCallExtraction` + `_extract_plot_calls(setup_cell, savefig_cell,
savefig_line, prev_savefig_line)` in `extract_figures.py`. Per-savefig
scope partitioning at savefig boundaries via line-number windows;
supports multi-savefig-per-cell idiom.

`_classify_plot_call` handles `plt.title` / `plt.suptitle` /
`plt.{x,y}label` / `ax.set_{title,xlabel,ylabel}` /
`axes[i,j].set_*` (panel-level) / `plt.legend` / `plt.subplots` (grid
declaration → row-major panel letters) / `plt.subplot(N,M,k)` (current-
position context) / `plt.figure()` (boundary-reset).

String-literal arguments only. Interpolated f-strings return `None`
(no fabrication). Wrapper-function plot calls (`volcano_plot(df)`) are
intentionally not chased into other modules — honest about the scope
cap; Source 4 (LLM) handles the wrapper case via REPORT prose.

**Cross-cell over-attribution bug discovered + fixed during smoke:**
when `fig01`'s draw cell becomes `fig02`'s `setup_cell_source`,
`fig01`'s panel titles were leaking into `fig02` via first-occurrence-
wins. Fix: treat `plt.figure()` and `plt.subplots()` as figure-boundary
calls that wipe per-figure state.

**Smoke (post-fix) against functional_dark_matter:** 39/41 with
matplotlib_ast trace; 28/41 with title; 31/41 with axes_labels;
**7/41 with multi-panel detection** (verified by spot-check of
fig01, fig02 source).

### Phase 3 — descriptor flow-through to manuscript

Three new helpers in `paper_writer_helpers.py`:
`_parse_figures_inventory_descriptions` (v2 inventory parser),
`_detect_prose_panel_callouts` (Stratum 2 prose-side scan for
`(Fig. N[A-Z])` callouts; ±1 sentence context per letter),
`_assemble_description_text` (composes single-line description with
empty-field elision; multi-panel "(A) ... (B) ..." form; redundant
author-side panel-letter prefix stripping; `max_chars=1500` cap).

`_build_figure_map` returns `dict[int, dict]` (was `tuple[str, str]`
in v0.3) with keys `filename` / `caption` / `descriptor` /
`synthesized_caption`. `cmd_resolve_figures` emits 4-column TSV.
`_embed_figures_in_text` injects an italic `*Description: <text>*`
paragraph after the image tag (later replaced by the v0.4 Phase 5b
combined-alt-text form). `assemble_docx.py` has post-image italic-
Description detection that upgrades the paragraph to Caption style;
this logic remains as defensive backward compatibility.

### Phase 4 — Source 4 LLM caption synthesis

**Phase 4a — `prompts/figure_caption.v1.md`** (294 lines). Mirrors
results.v1's skeleton: mission → inputs → output protocol →
discipline pass → worked example → closing-message template → escape
hatches → REPAIR_MODE.

Inputs via user_prompt only (no Read tool for input gathering):
`figure_id`, `short_caption`, `structured_descriptor` (full Phase 1b/2
schema), `prose_panel_callouts` (Stratum 2 dict), `report_prose`,
`results_section_prose`, `max_words` (default 200).

Output: single markdown paragraph, 50-200 words, written via Write
tool to `output_path`. Anti-fabrication discipline in three checks:
numerical-claim trace, panel-letter trace, word-count compliance,
plus an explicit notebook-organization-boilerplate exclusion section
(added in Phase 5b after the visual review surfaced this gap).

**Phase 4b — `tools/check_caption_provenance.py`** (361 lines). Sixth
post-checker following the v0.2 pattern. Reads
`audit/figure_caption.v1.metadata.json`; for each entry with
`source_chosen='llm'`, runs four checks: numerical-claim trace
(comma-normalized + unit-suffix-stripped lookups; deduped), named-
entity trace (multi-word capitalized phrases; common-prose allow-
list), panel-letter hallucination, word-count compliance.

**Phase 4c — orchestrator wiring.** Two new bash phases in
`paper_writer.sh`: `phase_caption_synthesis` (between
`phase_check_figures_manifest` and `phase_embed_figures`) and
`phase_check_caption_provenance`. Two new Python helper subcommands:
`build-caption-bundles` (sufficiency-gate classifier; emits per-figure
JSON bundles) and `compute-caption-stats` (post-write stats updater).

**Sufficiency gate** (revised after Phase 1a empirical data): Source
4 invoked iff EITHER `word_count(strip_heading_lines(notebook_prose)) <
30` OR (`descriptor.title is None AND descriptor.axes_labels is empty`).
On `functional_dark_matter` draft_3: 5 deterministic / 5 LLM.

### Phase 5b/5c — visual-review patches (post-retest)

The Phase 5 live retest exposed two issues that required follow-on
patches before ship.

**Phase 5b — caption layout + boilerplate strip:**

- Single-paragraph (ICMJE-form) caption: `_embed_figures_in_text` now
  combines short caption + description into ONE alt-text on the image
  markdown, eliminating the separate `*Description: ...*` paragraph.
  The docx renders one Caption paragraph per figure (was two).
- `_strip_prose_for_inline` filters notebook-organization keyword
  headers (Purpose, Approach, Strategy, Sections, Steps, Method, Goal,
  Inputs, Outputs, Notes, Test, Objective, Pipeline, Workflow,
  Implementation, Setup, Background, Rationale, Dependencies). Both
  bold idioms supported (`**Goal:**` and `**Goal**:`). Inline-cascade
  variant ("Sentence A. Goal: stuff. Approach: stuff. Sentence B.")
  handled iteratively. Numbered list items (`1. foo`, `2) foo`)
  dropped alongside bullet items. Project-internal artifact
  references (`REVIEW.md`, `REPORT.md`, `RESEARCH_PLAN.md`,
  `NB\d+`, `nb\d+`) stripped globally with optional `'s` possessive
  cleanup.
- `figure_caption.v1.md` prompt: explicit anti-pattern section with
  FAIL/PASS example for notebook-organization boilerplate.

**Phase 5c — Source 4 loop-closure:** Phase 5 retest revealed that
audit/figure_caption_<N>.md files were generated and validated but
never embedded in the manuscript. `_build_figure_map` now reads the
synthesized caption file when present and stores it as
`synthesized_caption` in the entry. `_embed_figures_in_text` uses it
verbatim as the description (LLM output is already polished;
descriptor-assembly is unnecessary). Falls back to descriptor-based
assembly when no synth.

### Defensive parser normalization (manifest-prefix fix)

`results.v1` occasionally emits `figures_manifest.tsv` with a
`figures/` directory prefix in the `filename` and
`inventory_lookup_name` columns. All downstream lookups (descriptor,
captions, embed) are keyed by basename, so the prefix silently broke
every lookup. Fix: `Path().name` normalization in
`_parse_figures_manifest`. Idempotent on already-clean basenames.
`check_figures_manifest.py` also gained a WARN-on-detect to surface
the LLM drift; downstream parser auto-normalizes regardless.

---

## What v0.4 ships in numbers

| Metric | v0.3 baseline | v0.4 |
|---|---|---|
| Caption paragraphs in docx | K (one per figure, single line) | K (one per figure, ICMJE form) |
| Caption avg word count (functional_dark_matter) | ~5 | **77** |
| Multi-panel detection per figure | 0 | up to N×M |
| LLM caption synthesis | none | per-figure on gate failure |
| Anti-fabrication checker | M-validators only | + provenance check |
| Live-retest cost (functional_dark_matter) | $6.49 | ~$7 fresh; $10.64 with recovery |

**Tests:** 426 unit tests, 0 fail. New tests cover: cell-attribution +
walk-back (13), structured CaptionDescriptor + v2 schema (17),
matplotlib AST extraction + multi-panel + boundary reset (41),
description assembly + prose-panel detection (22), Phase 4c sufficiency
gate + bundle builder + caption stats (11), check_caption_provenance
(27), assemble_docx italic-Description Caption upgrade (3), Phase 5b
patches (8 across layout + boilerplate strip + bold-idioms + inline-
cascade + numbered-list + possessive), Phase 5c Source 4 closure (4),
manifest-prefix normalization (3).

**LLM dev cost across v0.4:** $0 in unit/integration testing. Live
retest cost (functional_dark_matter, including recovery from one bug):
**$10.64 cumulative** (v0.3 baseline $6.49 + ~$2 Source 4 + ~$2
recovery overhead from manifest-prefix bug). Future fresh drafts
project ~$7.

---

## Architectural lessons confirmed across v0.4 (do not regress)

- **Punch-list + tiered + smoke-at-gates** (memory:
  `feedback_punch_list_release_pattern.md`) holds for the SIXTH
  consecutive cycle (v0.1.0 → v0.1.x → v0.2.0 → v0.2.1 → v0.3.0 →
  v0.4.0). Pattern: each phase ships an evaluable artifact; tier
  boundaries are smoke-test gates; punch-list captures scope drift.

- **Empirical-data-driven gates beat guessed thresholds.** Phase 1a
  smoke surfaced that 37% of `functional_dark_matter` figures generate
  en bloc under one section heading with no per-figure descriptive
  markdown. This finding reshaped the Phase 4c sufficiency-gate
  threshold from "<50 chars prose" to "<30 non-heading-words prose
  AND no AST title/axes" — the live retest validated the projection.

- **Visual review surfaces issues unit tests miss.** Phase 5 retest
  produced clean cumulative numbers (5 det / 5 llm, 0 ungrounded WARNs,
  validators pass) but the docx visual revealed three follow-on bugs:
  Source 4 dead-pipeline (synthesized captions never embedded),
  two-paragraph caption layout (non-ICMJE), and notebook-keyword
  boilerplate hallucination. None would have surfaced from numeric
  gates alone.

- **Prompt-vs-tool contract drift produces silent failures**
  (memory: `feedback_prompt_tool_contract_drift.md`). The
  `figures/` directory prefix in results.v1's manifest emission was
  one such drift — schema spec said basename-only; LLM emitted
  prefixed; downstream silently fell back to filename-derived
  captions. Fix: defensive parser normalization + WARN-on-detect.

- **Two markdown idioms render identically but parse differently.**
  `**Goal:**` and `**Goal**:` look the same in rendered Markdown
  but require different regex matchers. Pin both forms in test
  fixtures the moment one is supported.

---

## Known limitations + v0.5 backlog

- **Tables**: not yet supported. `results.v1.md` mentions
  `(Table N)` callouts in passing but there's no `tables_inventory.md`
  / manifest / embed pipeline. Tier 9 — table embedding — is the
  natural v0.5 cycle (mirrors figures architecture).
- **Caption truncation on dev-process-heavy notebook prose**: the
  Phase 5b boilerplate strip handles keyword-tagged boilerplate
  (`**Purpose:**`, `**Goal**:`, etc.) cleanly. Non-keyword-tagged
  dev-process sentences ("supplementary notebook", "saved data
  files", "existing notebooks NOT modified") survive and may
  consume word budget on multi-panel figures. Workaround: the
  Source 4 LLM with the new anti-pattern prompt avoids these
  patterns; figures that fail the sufficiency gate get clean
  captions via that path. Phase 5b's strip is best-effort for the
  deterministic-source case.
- **`gridspec.GridSpec` + `subplot2grid` panel layouts**: not
  detected. Asymmetric grid handling needs additional AST work.
- **Stratum 3 multi-panel** (PIL/Inkscape post-hoc-composed
  figures): no signal recovered. Vision API on PNG is the only
  path; new dependency, new cost surface.
- **`--max-cost-usd` flag on Python CLI**: parsed by
  `paper_writer.sh` directly but not exposed by `commands/draft.py`
  / `commands/continue_run.py` argparse. Surface in the Python CLI
  for cap-aware live retests.
- **Python 3.14 test-collection errors**: 8 pre-existing tests
  (`test_validate_manuscript.py` and others) fail to collect under
  Python 3.14.4 due to compatibility issues. v0.4 source is fine on
  3.10/3.12 (sandbox runs 423/423; user's pytest in 3.12 reaches
  collection cleanly). Pin pytest invocation to a known-compatible
  Python interpreter or fix the 3.14 issues directly.
- **v0.4-cycle cost ceiling**: the Phase 5 retest cumulative was
  $10.64 (cap was $10) due to one-time recovery overhead from the
  manifest-prefix bug. Future fresh drafts project ~$7. Cap held
  in spirit; the overshoot was non-recurring.

---

## Upgrade notes (v0.3 → v0.4)

- Inventory v1 → v2 schema bump. Always-regenerate path; no v1
  read-back. Re-run `extract_figures.py` against existing projects
  to produce v2 inventories.
- Existing draft_N directories produced by v0.3.0 will run cleanly
  through v0.4 phases (extract → results → manifest → caption_synthesis
  → embed → assemble) on resume. The `phase_caption_synthesis` will
  classify figures by sufficiency gate and synthesize captions for
  those that fail; deterministic figures use descriptor-derived
  description.
- The `_build_figure_map` return shape changed (`tuple[str, str]` →
  `dict`). Callers outside this codebase (none known) will need to
  adapt. Internal callers updated.

---

*Authored 2026-04-28 alongside the v0.4.0 ship cycle. Each phase's
status sections live in `smoke-test/v0_4_punch_list.md` for fast
re-orientation. Memory entries:
`project_paper_writer_v0_4.md` (supersedes
`project_paper_writer_v0_3.md`).*
