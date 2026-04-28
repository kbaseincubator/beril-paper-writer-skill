# beril-paper-writer v0.4 — punch list

**Created:** 2026-04-28 (post v0.3.0 ship; cycle scope locked this date)
**Cadence:** push hard, evaluable output at every step, decision points
between items. Same tiered structure that worked for v0.1.x / v0.2 / v0.3
(see `feedback_punch_list_release_pattern.md` — Adam-endorsed pattern,
fifth consecutive cycle).
**Operator:** Adam Arkin, single-user, hands-on through the cycle.

This document is the authoritative scope for the v0.4 cycle. Tier 8 —
figure caption richness — is the v0.4 thesis. v0.3.1 cosmetics are
absorbed into v0.4-dev's first commit (no separate v0.3.1 release). Tier 7
(conceptual diagrams via mermaid) is deferred to v0.5; design notes
preserved in `v0_3_punch_list.md` "Post-v0.3 deferred" section.

**Predecessor:** `v0_3_punch_list.md` — closed; v0.3.0 shipped 2026-04-27
at commit 9c4a1e7 (or whatever the actual tag SHA is; verify before ship).

---

## Cycle scope (locked 2026-04-28)

**v0.4.0 ships:**
- Tier 8 (caption richness) full ladder: Sources 2 + 3 deterministic
  enrichment, Source 4 LLM-synthesis fallback with provenance checker
- Multi-panel awareness Strata 1+2 (AST `subplots`/`subplot` detection +
  prose `(Fig. NA)` callout detection)
- v0.3.1 cosmetic patches (already landed Phase 0)

**v0.4.0 does NOT ship:**
- Tier 7 mermaid diagrams (v0.5 candidate)
- Multi-panel Stratum 3: PIL/Inkscape post-hoc composed figures (vision
  required; v0.5+)
- `gridspec.GridSpec` / `subplot2grid` complex panel layouts (v0.5)
- `inset_axes` / `add_axes` sub-region detection (v0.5; edge case)
- Tier 4 interactive checkpoints (deferred until 2nd user)
- M10 architectural redesign (citation-pool-aware repair routing)
- Items 4.3 / 5.3 (state-schema migration; bundled when 4.3 lands)

---

## Tier 8 — Figure caption richness (the v0.4 thesis)

**Problem.** v0.3 ships with terse REPORT-derived captions (one noun
phrase per figure). For ICMJE / Nature / Science caption convention
(50–200 words: what's shown, what to notice, panels A/B/C, n values,
error bars, statistical context), v0.3 is insufficient. Adam flagged this
2026-04-27 immediately after the v0.3 Tier 2 smoke.

**Source ladder** (cheapest → richest):

- **Source 1 — REPORT.md surrounding prose** (already in v0.3;
  `extract_figures.py` REPORT-image-references parser).
- **Source 2 — notebook markdown walkback** (v0.4 work). Currently
  `extract_figures.py` has a broken "consume one md cell" attribution
  model that fails on the dominant idiom (md → data-prep code →
  plot+savefig code). Phase 1a fixes this.
- **Source 3 — matplotlib AST extraction** (v0.4 work). Walks savefig
  cell + 1 preceding code cell for `title`/`xlabel`/`ylabel`/`subplots`
  calls. Phase 2.
- **Source 4 — LLM caption synthesis** (v0.4 work, with provenance
  checker). New prompt `figure_caption.v1.md`; new sixth post-checker
  `tools/check_caption_provenance.py`. Phase 4.
- **Source 5 — vision on PNG** (declined; v0.5+ if ever).

---

### Design-lock: seven corrections to the v0.3 Tier 8 design notes

These are corrections to the Tier 8 design as sketched in
`v0_3_punch_list.md` lines 580-652 ("Tier 8 — figure caption richness"
section). Locked 2026-04-28.

**1. Notebook cell-attribution is broken in current `extract_figures.py`.**
Lines 347-359 of `_walk_notebook_savefigs` use a "consume" pattern that
attributes a markdown cell to the FIRST following code cell. In the
dominant idiom (md → data-prep code → plot+savefig code), the savefig
cell sees `preceding_md_cell_index = None`. Source 2 returns nothing on
the majority of `functional_dark_matter`'s notebooks. **Fix in Phase 1a:**
replace consume model with per-savefig backward walk; section break =
markdown cell whose first non-blank line begins with `#`. Concatenate
all markdown content in the walk-back span. Multiple savefigs may
legitimately share the same upstream description.

**2. `_truncate(., 280)` is incompatible with Tier 8's caption length
target.** ICMJE-style captions are 50-200 words ≈ 250-1500 chars. **Fix in
Phase 1b:** add separate `description_text` field on `CaptionCandidate`
(4000-char cap or unbounded — pick after measuring functional_dark_matter
actuals). Existing `text` field stays at 280 chars for short-caption
display use.

**3. Source 3 (matplotlib AST) needs a hard scope cap.** Modern notebook
idiom uses wrapper functions (`my_volcano_plot(df)`) where labels are
buried in wrapper bodies — possibly in another module. **Fix in Phase 2:**
AST walks ONLY (a) the savefig cell and (b) the immediately preceding
code cell. No function-call chasing. No `gridspec.GridSpec` /
`subplot2grid` (deferred to v0.5).

**4. Source merging strategy is unspecified in v0.3 punch list.** **Fix in
Phase 1b:** define structured `CaptionDescriptor`:
```python
{
  "title": str | None,
  "axes_labels": list[str],           # xlabel, ylabel, colorbar label
  "legend_labels": list[str],
  "notebook_prose": str | None,
  "panels": list[{letter, title, xlabel, ylabel, prose_context}],
  "source_refs": list[str],            # provenance trace
}
```
`resolve-figures` assembles from this via deterministic template with
empty-field elision (no `Figure N: . . .` artifacts).

**5. AC enforcement for Source 4 needs a checker.** v0.3 punch list AC
said "every claim traceable; no fabrication" with no enforcement
mechanism. **Fix in Phase 4b:** new sixth post-processor
`tools/check_caption_provenance.py` joins the existing five
(`check_throughline_glyphs`, `check_scope_coherence`, `check_overclaim`,
`check_repair_scope`, `check_figures_manifest`). Checks: numerical-claim
trace, named-entity trace, panel-letter hallucination, word-count
compliance.

**6. Schema migration is unspecified in v0.3 punch list.** **Decision:**
v0.4 always regenerates `figures_inventory.md` to v2 schema (structured
Description block); no v1 read path. Cheap (no LLM cost in
`extract_figures.py`); avoids forking parsers. Verify
`check_figures_manifest.py` + `paper_writer_helpers.py resolve-figures` +
`results.v1.md` are unaffected by the v2 shape (they only consume
short-caption candidates and the manifest, both of which keep v1
compatibility). Inventory schema header line: `<!-- inventory_schema_version: 2 -->`.

**7. Multi-panel awareness was originally written out-of-scope; partially
restored.** `functional_dark_matter`'s draft_1 had `(Fig. N)` callouts
with panel suffixes A/B per the v0.3 retest log. Multi-panel is the
dominant molecular-biology figure idiom; deferring entirely would mean
v0.4 captions are factually incomplete on the majority of figures. **Fix
in Phases 2 + 3 + 4a + 4b:** restore Strata 1 + 2 awareness:
- Stratum 1 (Phase 2): AST detects `plt.subplots(N,M)`,
  `subplot(N,M,k)`, `axes[i,j].set_*` calls. Letters by row-major grid
  position.
- Stratum 2 (Phase 3): `resolve-figures` regex-scans REPORT.md +
  Results-section prose for `(Fig. N[A-Z])` callouts. Merges with
  AST-derived panel list.
- Stratum 3 (PIL/Inkscape composed figures): out-of-scope; v0.5+ work.
- Phase 4a prompt instructed for panel discipline.
- Phase 4b checker validates panel-letter mentions trace to descriptor
  panels OR prose callouts.

---

## Phase ordering

Sequenced from `TaskList` (this cycle's tasks #1-#10). Dependencies are
encoded in TaskList; this section is the human-readable view.

| # | Task | Cost | LOC est | Smoke |
|---|------|------|---------|-------|
| 0 | v0.3.1 cosmetics + design lock | $0 | ~20 | DONE 2026-04-28 |
| 1a | Notebook cell-attribution fix | $0 | ~80 | functional_dark_matter |
| 1b | description_text field + v2 inventory schema | $0 | ~120 | functional_dark_matter |
| 2 | Source 3: matplotlib AST + multi-panel Stratum 1 | $0 | ~150 | functional_dark_matter |
| 3 | resolve-figures + embed + assemble multi-line + multi-panel Stratum 2 | $0 | ~200 | functional_dark_matter |
| 4a | figure_caption.v1.md prompt | ~$0.10 | new prompt | synthetic fixture |
| 4b | check_caption_provenance.py | $0 | ~250 | unit tests |
| 4c | Wire phase_caption_synthesis + provenance phase | $0 | ~80 | dry-run |
| 5 | Live retest gate ($10 cap) | ≤$10 | — | full draft |
| 6 | Ship v0.4.0 | $0 | release artifacts | — |

**Total dev cost ceiling:** $10 (live retest only; all other phases $0
or smoke-fixture). Comparable to v0.3 ($6.49 actual).

**Source 2+3 sufficiency gate** (Phase 4c decides which figures invoke
Source 4): proposed initial threshold — Source 4 invoked iff
`notebook_prose < 50 chars` OR (`title is None` AND `axes_labels is
empty`). Tune empirically after Phase 1+2 by measuring
`functional_dark_matter`'s 10 figures. Don't lock the threshold until
data is in.

---

## Phase 1a status (DONE 2026-04-28)

**Cell-attribution rewrite landed:** new `_collect_md_walkback(cells,
savefig_raw_idx)` helper in `extract_figures.py`; `_walk_notebook_savefigs`
refactored to walk back per savefig cell. 13 new unit tests; 295 total
tests pass (no regressions). Live smoke against `functional_dark_matter`
(41 figures across 8+ notebooks):

| Walk-back outcome | v0.3 | v0.4 (Phase 1a) |
|---|---|---|
| Rich content (≥5 non-heading words) | ~0 | **24/41 (59%)** |
| Just a section heading (`## ...`) | ~0 | 15/41 (37%) |
| No notebook_md attached | ~41 | 2/41 (5%) |

**Critical empirical finding for Phase 4c sufficiency-gate design.** The
just-heading bucket (15/41 — e.g., fig01-fig07 all get "`## 4. Figures`",
13 chars) demonstrates that the dominant idiom in
`functional_dark_matter` is figures-generated-en-bloc under one section
heading, with no per-figure descriptive markdown. The original gate I
proposed (`notebook_prose < 50 chars`) would over-pass these cases — a
heading like `## Section 5: Environmental Characterization and
Visualization` is 60 chars but contains zero per-figure substance.

**Revised Phase 4c gate (lock target):**

  Source 4 (LLM) is invoked iff EITHER:
    a) word_count(strip_heading_lines(notebook_prose)) < 30, OR
    b) descriptor.title is None AND descriptor.axes_labels is empty.

  Where strip_heading_lines drops every line whose first non-blank char
  is `#`. The 30-word threshold maps roughly to "two short sentences"
  and is the empirical floor for Source 2+3 to plausibly write a
  Nature/ICMJE-style legend without LLM augmentation.

This shifts the expected v0.4 LLM cost up: at the 30-word threshold,
~17/41 figures (15 just-heading + 2 no-md) on `functional_dark_matter`
fail Source 2 sufficiency. With Source 3 (AST, Phase 2) catching some
fraction, expect ~10-12 figures × $0.30/figure = $3-4 of LLM cost in
Phase 5's full draft. Still under the $10 cap but worth tracking.

**Implication for Phase 1b:** the v2 inventory schema's
`description_text` field MUST distinguish heading-only from
heading-plus-prose. The simplest representation is to emit raw
walk-back content; downstream consumers (resolve-figures + the
sufficiency gate) apply the strip_heading_lines transform.
`description_text` is the unredacted source; transformations are
caller-side.

---

## Phase 4c status (DONE 2026-04-28)

**Orchestrator wiring complete.** Two new bash phases in
`paper_writer.sh` plus two new Python helper subcommands.

**Python helpers added to `paper_writer_helpers.py`:**

- `cmd_build_caption_bundles` — reads manifest + v2 inventory + REPORT
  + 02_results.md; for each figure, applies the sufficiency gate;
  writes per-figure JSON bundles + initial metadata.json. Stdout is
  the list of figure_ids needing Source 4 (one per line).
- `cmd_compute_caption_stats` — after the LLM writes the caption,
  counts word_count / panel_count / traceable_claims (using the same
  regexes as `check_caption_provenance.py`); updates the entry in
  `audit/figure_caption.v1.metadata.json`. Idempotent.
- Plus internal helpers `_strip_heading_lines`, `_word_count`,
  `_passes_sufficiency_gate`, `_extract_report_prose_for_figure`,
  `_extract_results_section_prose_for_figure`.

**Sufficiency gate** (revised after Phase 1a empirical data):

  PASSES (no Source 4) iff BOTH:
    a) word_count(strip_heading_lines(notebook_prose)) >= 30, AND
    b) descriptor.title is non-None OR descriptor.axes_labels non-empty.

  FAILS (Source 4 needed) otherwise.

**Bash phases added to `paper_writer.sh`:**

- `phase_caption_synthesis "$project_root" "$draft_dir" "$model"`:
  1. Calls `build-caption-bundles` → list of figure_ids on stdout.
  2. For each figure_id: reads bundle JSON, formats user_prompt with
     the JSON inlined, invokes `figure_caption.v1.md` via
     `invoke_claude_with_retry` with output path
     `audit/figure_caption_<N>.md` and metadata path
     `audit/figure_caption_<N>.invoke.metadata.json`. Cost circuit-
     breaker (--max-cost-usd) applies.
  3. After Write succeeds, calls `compute-caption-stats` to populate
     closing-message stats.
  4. LLM-failure fallback: log_warn + `continue` → orchestrator
     proceeds; the figure falls back to the deterministic descriptor
     description embedded by Phase 3.

- `phase_check_caption_provenance "$draft_dir"`:
  - Standard post-checker invocation (mirrors phase_check_figures_manifest).
  - Pipes stderr to `audit/caption_provenance_warnings.txt`.
  - Surfaces WARNs to log + `next_actions.md` via emit-next-actions.

**Wiring in main case block:**

```
phase_check_figures_manifest "$draft_dir"
phase_caption_synthesis "$project_root" "$draft_dir" "$model"   ← NEW
phase_check_caption_provenance "$draft_dir"                      ← NEW
phase_embed_figures "$draft_dir"
```

**Rewrite-loop integration** is intentionally OMITTED for v0.4 — the
rewrite-loop's `phase_embed_figures` call is preserved (idempotent),
but Source 4 isn't re-invoked on rewrite. If rewrites introduce new
(Fig. N) callouts, those figures get the deterministic descriptor
description (Phase 3) rather than re-paying for synthesis. v0.5
candidate to add `--recaption` flag for selective re-invocation.

**Tests:** 11 new (`TestSufficiencyGate` 6 cases, `TestBuildCaptionBundles`
2 end-to-end cases on synthetic drafts, `TestComputeCaptionStats` 1
roundtrip case, plus shared infrastructure). Full suite: 414 pass, 0 fail.

**Bash syntax lint clean** (`bash -n`).

**Dry-smoke against `functional_dark_matter` draft_2 (10-figure manifest):**

| Bucket | Count | Expected LLM cost |
|---|---|---|
| `source_chosen=deterministic` (gate passes) | 3 | $0 |
| `source_chosen=llm` (gate fails) | 7 | ~$0.30/fig × 7 = ~$2.10 |

LLM figure_ids: [1, 2, 3, 4, 5, 6, 10]. These are the figures whose
notebook authors generated them en bloc under a single section
heading (Phase 1a finding); their walk-back returns just the heading,
sufficiency gate fails on the prose-words check.

The 3 passing figures (7, 8, 9 in this manifest) had rich descriptive
markdown alongside their code — Phase 1a's walk-back captured that
fully and Phase 2's AST extracted axes labels.

**Phase 5 cost ceiling holds:** $2.10 expected × 1.5 safety factor =
~$3.15. Far below the $10 --max-cost-usd cap.

---

## Phase 4b status (DONE 2026-04-28)

**Sixth post-checker shipped:** `tools/check_caption_provenance.py`
(361 lines + 27 unit tests).

**Pattern compliance** (joins the existing five —
`check_throughline_glyphs`, `check_scope_coherence`, `check_overclaim`,
`check_repair_scope`, `check_figures_manifest`):
- Standalone Python script.
- Reads `<draft_dir>/audit/figure_caption.v1.metadata.json` (Phase 4c
  emits it; checker treats absence as a NOTE-only outcome).
- Iterates `entry["captions"]` where `source_chosen == "llm"`; reads the
  caption text from `<draft_dir>/<output_path>`; runs four checks per
  caption.
- stderr WARN/NOTE; final summary line; **always exits 0**.
- Importable as a module for unit testing.

**Four checks** (matching the prompt's discipline pass — second-layer
enforcement of what `figure_caption.v1.md` asks the LLM to self-check):

1. **`check_numerical_claims`** — every digit token in the caption
   (regex `\b\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?(?:%|x|×|±|~)?\b`) must
   appear in the flattened bundle corpus. Comma-normalized lookup
   (`1,000` ↔ `1000`); unit-suffix-stripped lookup (`2.6%` ↔ `2.6`);
   dedupe per-caption (one WARN per ungrounded value, not per
   occurrence).

2. **`check_named_entities`** — multi-word capitalized phrases (regex
   `\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b`) must appear in corpus. Common
   prose phrases allow-listed (`Each Panel`, `The Distribution`,
   `Both Panels`, `Panel A`/`B`/`C`/`D`, etc.) — those are not
   fabrication signals.

3. **`check_panel_letters`** — each `(A)` / `(panel A)` / `panel A` /
   `panel labeled A` mention in the caption must trace to either
   `descriptor.panels[*].letter` OR `prose_panel_callouts` keys.
   Otherwise WARN with the valid-letters list for diagnostic clarity.

4. **`check_word_count`** — must be in `[30, 200]` (Tier 8 AC).
   Below 30 → WARN with explicit fall-back recommendation. Above 200
   → WARN that the prompt's word-count discipline failed.

**Bundle corpus assembly** (`_flatten_bundle_text`): concatenates
descriptor.title / axes_labels / legend_labels / panels[i] all sub-fields
/ notebook_prose, prose_panel_callouts values, report_prose,
results_section_prose, AND short_caption (the orchestrator passes this;
it's a legitimate source). Empty-everywhere → empty corpus → all numbers
WARN as ungrounded. That's correct; matches the prompt's "if descriptor
+ report + results all empty, refuse to fabricate" discipline.

**Tests:** 27 new (flatten-bundle field coverage, numerical-claim
grounded/ungrounded/comma-normalized/percent-suffix/dedup, named-entity
grounded/ungrounded/allow-listed, panel-letter
grounded-in-descriptor/grounded-in-callouts/ungrounded/Word-form,
word-count under/in/over/at-boundary, full-pipeline clean/fabricated-n/
fabricated-panel, end-to-end main() with no metadata / clean caption /
deterministic-source-skipped / fabrication-WARNs).

**Behavior NOT yet shipped (Phase 4c):**
- `phase_caption_synthesis` in `paper_writer.sh`: invokes
  `figure_caption.v1.md` per figure failing the sufficiency gate;
  parses closing-message; writes the metadata.json schema this checker
  reads.
- `phase_check_caption_provenance` orchestrator wiring: invokes this
  checker after caption synthesis, surfaces WARNs in `next_actions.md`
  via emit-next-actions.

---

## Phase 4a status (DONE 2026-04-28)

**Prompt drafted:** `prompts/figure_caption.v1.md` (294 lines).

**Structure** (mirrors results.v1's skeleton):
- Mission framing: anti-fabrication is the primary failure mode; cite
  SPEC §6.1.
- Input contract: 7 fields (figure_id, short_caption,
  structured_descriptor, prose_panel_callouts, report_prose,
  results_section_prose, max_words). LLM does NOT read files; all
  inputs flow through the user_prompt.
- Output format: single markdown paragraph; 50-200 words; written via
  `Write` tool to `output_path`.
- Discipline pass: three checks before emit:
  1. Numerical-claim trace — every digit greppable in inputs; common
     fabrication patterns enumerated.
  2. Panel-letter trace — each `(X)` mentioned must trace to either
     descriptor.panels OR prose_panel_callouts; HALT otherwise.
  3. Word-count compliance — over `max_words` HALT-and-trim;
     under 30 words triggers a fall-back signal to the orchestrator.
- Worked example: synthetic input bundle showing fig03's
  multi-panel descriptor + prose panel callouts → ~100-word output
  caption with traceability annotated.
- Closing-message template:
  `figure_caption_<N> word_count <W> traceable_claims <K> panel_count <P>`
  Parsed by Phase 4c orchestrator wiring.
- Escape hatches: empty-everywhere → halt with explicit error;
  AST/prose disagreement on panel letters → AST wins for technical
  claims, treats unverified prose-only letters as `[panel X
  unverified]` placeholders.
- REPAIR_MODE: not applicable (Source 4 is a fresh-only path; failed
  provenance check falls back to deterministic short caption, doesn't
  re-invoke).

**Live smoke deferred to Phase 5.** A standalone $0.10 smoke would
require Phase 4c orchestrator wiring to assemble the input bundle.
Phase 5's full draft retest exercises the prompt against 10-12
real figures from `functional_dark_matter`'s sufficiency-gate-failure
set, which is the same evaluable surface for less plumbing overhead.

**Behavior NOT yet shipped (deferred to Phase 4b/4c):**
- `tools/check_caption_provenance.py` — sixth post-checker that
  validates the prompt's output against the input bundle.
- Phase 4c orchestrator wiring: phase_caption_synthesis builds the
  input bundle, invokes the prompt, parses the closing message, runs
  provenance check.

---

## Phase 3 status (DONE 2026-04-28)

**Structured CaptionDescriptor flows out to user-visible output.**
Three layers landed:

1. **`paper_writer_helpers._parse_figures_inventory_descriptions`** — v2
   inventory parser. Reads the `**Description:**` block per figure;
   schema-version sniff on line 1 (`<!-- inventory_schema_version: 2 -->`);
   v1 inventories return `{}` for graceful fallback. Parses italic-labeled
   bullets (`_Title:_`, `_Axes:_`, `_Legend:_`, `_Panels:_`), the
   `_Notebook prose:_` blockquote, and `_Source refs:_` line.

2. **`_detect_prose_panel_callouts(text, n)`** — Stratum 2 prose-side
   panel detection. Regex-scans section text for `(Fig. N[A-Z])`,
   `(Fig. N, panel A)`, and `panel A` patterns; returns
   `{letter: ±1-sentence-context}`. First-occurrence-wins per letter.

3. **`_assemble_description_text`** — composes the single-line
   description string. Single-panel: `<title>. Axes: X; Y. <prose>.`
   Multi-panel: `<title>. (A) <panel-A>. (B) <panel-B>. ... <prose>.`
   Empty-field elision; redundant author-side panel-letter prefix
   stripping (`set_title('A. Foo')` → renders as `(A) Foo`, not
   `(A) A. Foo`); flattens newlines to spaces; capped at `max_chars`
   (default 1500).

**Pipeline wiring:**

- `_build_figure_map` returns `dict[int, dict]` (was `tuple` in v0.3);
  each entry has `filename` / `caption` / `descriptor`. Existing test
  fixture updated to match.
- `cmd_resolve_figures` emits 4-column TSV: `paper_order_n\tfilename\tcaption\tdescription`.
  `description` is the descriptor-only assembled string (prose-panel
  callouts merged in at embed time, not resolve time, since the
  resolve helper is called outside section-file context).
- `_embed_figures_in_text` injects italic `*Description: <text>*`
  paragraph after the image tag when descriptor is non-empty. Per-call
  prose-panel detection merges section-text panel callouts (Stratum 2)
  with AST-derived panels.
- `assemble_docx.render_document` upgrades any italic
  `*Description: ...*` paragraph IMMEDIATELY following an image to
  `Caption` style (visual continuity with the figure caption above).

**Tests:** 22 new (descriptor parser v1/v2 round-trip, prose panel
detector, assemble_description_text empty/title-only/multi-panel/prose-merge/cap/newline-flatten/letter-prefix-strip,
embed-with-descriptor injection, embed idempotency with descriptor,
assemble_docx is-italic-description matcher, post-image Caption-style
upgrade, NOT-after-image stays Normal). Full suite: 378 pass, 0 fail.

**Bug found + fixed during smoke**: panel-entry separator was emitted
as `, ` by `format_figures_inventory_md` but parsed as `;` by
`_PANEL_ENTRY_RE`. Result: multi-panel descriptors collapsed all panels
into the first letter's title. Fix: emit `; ` separator (consistent
with Axes / Legend bullets); parse as `;`. Pinned by an integration
round-trip test against `functional_dark_matter`.

**Smoke against `functional_dark_matter` (10-figure draft_2 manifest):**

| paper_order_n | description (excerpt) |
|---|---|
| 1 | `(A) Gene annotation classes (all 228K genes) (B) Dark gene fraction by organism.` |
| 2 | `Dark gene evidence coverage. Axes: Number of genes.` |
| 3 | `Condition classes where dark genes show strong fitness effects. Axes: ...` |
| 6 | `Lab-Field Concordance Rate. Axes: Concordance rate (%); ...; ... For each cluster with a mapped condition class AND environment data...` |
| 7 | `NMDC: Dark Gene Carrier Abundance. Axes: ... Use NMDC metagenomic data as an independent check. For taxa that carry dark genes...` |

End-to-end docx render verified: 1-figure synthetic input → manuscript
docx with `Caption`-styled "Figure 1: ..." paragraph immediately
followed by `Caption`-styled "Description: ..." paragraph. Visual
continuity confirmed.

**Behavior NOT yet shipped (Phase 4):**

- `phase_caption_synthesis` (Source 4 LLM) — invoked when sufficiency
  gate flags a figure. Phase 4a's prompt + Phase 4b's checker.
- `phase_check_caption_provenance` — sixth post-checker.
- `phase_caption_synthesis` wiring in `paper_writer.sh`.

These three close the v0.4 thesis end-to-end.

---

## Phase 2 status (DONE 2026-04-28)

**matplotlib AST extraction (Source 3) landed.** Net-new in
`extract_figures.py`:

- Helpers: `_string_literal` (Constant + non-interpolated f-string;
  interpolated f-strings return None — no fabrication),
  `_extract_subscript_indices` (1D + 2D constant indices only),
  `_idxs_to_letter` (row-major from grid_cols).
- `_classify_plot_call` — single-dispatch classifier for matplotlib
  calls: `title` / `suptitle` / `axes_label` (xlabel/ylabel/colorbar
  set_label) / `legend` / `subplots_grid` / `subplot_pos` / `panel_set`
  / `figure_call`. Wrapper functions (`volcano_plot(df)`) are
  intentionally not classified — honest about the scope cap.
- `PlotCallExtraction` dataclass + `_extract_plot_calls(setup_cell,
  savefig_cell, savefig_line, prev_savefig_line)`. Per-savefig scope
  partitioning via line-number windows.
- `SavefigCall.plot_calls: Optional[PlotCallExtraction]` carries the
  extraction through the pipeline; `build_figure_records` merges the
  first non-empty extraction into `FigureRecord.description`
  (title / axes / legend / panels) with a `matplotlib_ast(<notebook>)`
  source-ref.

**Cross-cell over-attribution bug discovered + fixed during smoke.**
First Phase 2 smoke against `functional_dark_matter` showed fig01 and
fig02 with IDENTICAL panel titles — fig01's `axes[i].set_title(...)`
calls were leaking into fig02's extraction because fig01's draw cell
was fig02's `setup_cell_source` and `_attach_panel`'s
first-occurrence-wins semantics let the stale data win. Fix: treat
`plt.figure()` and `plt.subplots()` as figure-boundary calls that
WIPE per-figure state (title, axes_labels, legend_labels, panels,
grid_cols, current_subplot_position) before processing subsequent
calls in scope. `plt.subplot(N, M, k)` does NOT reset (it activates
an existing subplot in the current figure, not a new figure). Three
new tests cover the boundary behavior; smoke re-verified clean.

**Tests:** 41 new (string literal, subscript indices, letter mapping,
call classifier, AST extraction, notebook-walker integration,
build-records merge, boundary reset). Full suite: 356 pass, 0 fail.

**Smoke against `functional_dark_matter` (post-fix):**

| Field | Populated |
|---|---|
| `matplotlib_ast` in source_refs | 39/41 |
| `description.title` | 28/41 |
| `description.axes_labels` | 31/41 |
| `description.panels` (multi-panel) | 7/41 |
| `description.legend_labels` | 0/41 |

The 7/41 multi-panel detections are the genuinely multi-panel figures
(verified by spot-check of fig01/fig02 source); the prior 10 included
3 bleed-throughs that the boundary-reset fix correctly cleared.

**Known Phase 2 limitations (deferred to v0.5+, NOT shipping in v0.4):**

- **Legend label extraction is 0/41** because the dominant idiom is
  `axes[i].legend()` with labels coming from `plot(..., label='X')`
  kwargs in earlier `.plot()` / `.hist()` / `.scatter()` calls. My
  classifier only inspects the `legend()` call's args/kwargs, not the
  upstream plot calls. Fix would require walking all plot-style calls
  and collecting `label=` kwargs into a labels-buffer keyed by axis.
  Not blocker for v0.4 — Source 4 (LLM, Phase 4) will fill this gap
  via Results-section prose.
- **Cross-cell over-attribution still possible without boundary call**:
  if the savefig cell does NOT call `plt.figure()` / `plt.subplots()`
  but its prior cell did set state, the prior state still leaks. This
  is correct matplotlib semantics — `plt.title()` applies to the
  current figure, which spans cells until a new `plt.figure()`. So
  it's not actually a bug in idiomatic notebooks; documented for
  awareness.
- **`gridspec.GridSpec` + `subplot2grid` panel layouts** (asymmetric
  grids): not detected. v0.5+.
- **Wrapper-function plot calls** (`volcano_plot(df)` whose body is
  in another module): no signal recovered. By design — Source 4
  fallback handles this.

---

## Phase 1b status (DONE 2026-04-28)

**Schema enrichment landed.** Net-new in `extract_figures.py`:

- `PanelDescriptor` dataclass (letter, title, xlabel, ylabel,
  prose_context). Phase 2 + Phase 3 will populate; Phase 1b leaves the
  field empty by design.
- `CaptionDescriptor` dataclass (title, axes_labels, legend_labels,
  notebook_prose, panels, source_refs) with `is_empty()` predicate.
  Field-population schedule documented in the docstring covers Phase
  1b through Phase 4.
- `FigureRecord.description: CaptionDescriptor` — default factory is an
  empty CaptionDescriptor; serialized via `to_dict()`.
- `_DESCRIPTION_TEXT_CAP = 4000` — empirical: max walk-back across
  `functional_dark_matter` is 3424 chars (p95=2782, median=121); 4000
  gives comfortable headroom while bounding pathological cases.
- `build_figure_records` populates `description.notebook_prose` from
  the FIRST non-empty walk-back per figure (the `_truncate(., 280)`
  reduction stays only for the existing CaptionCandidate.text field).
  Multiple savefig origins are still recorded; only the first
  walk-back's prose is stored to avoid duplication.
- `format_figures_inventory_md` bumped to v2:
  - `<!-- inventory_schema_version: 2 -->` is now line 1 of every
    inventory (parseable from a fixed offset by future tooling).
  - Per-figure `**Description:**` block emitted only when
    `description.is_empty()` is False. Otherwise omitted (no noisy
    empty-section headers).
  - `notebook_prose` rendered as CommonMark blockquote (multi-line
    safe; blank lines render as bare `>`).
  - Title / axes / legend / panels rendered as italic-labeled bullets
    (e.g. `_Title:_ Foo`, `_Axes:_ X; Y`); none of these populate in
    Phase 1b but the renderer is wired for Phase 2.
  - `_Source refs:_` provenance line included.

**Tests:** 17 new (CaptionDescriptor, FigureRecord.description,
inventory schema v2, multi-savefig first-walkback-wins, cap
enforcement, downstream-parser round-trip lock). Full suite: 313 pass,
0 fail. v2-vs-v1 contract locked by
`test_v2_inventory_round_trips_through_downstream_parser` —
`paper_writer_helpers._parse_figures_inventory_captions` recovers
captions from a v2 inventory without drift.

**Smoke against `functional_dark_matter`:**
- Inventory: 54177 chars, 1391 lines.
- 39/41 figures emit a `**Description:**` block (matches the 39 with
  notebook_md from Phase 1a smoke).
- All 41 `### figures/...` headings preserved; downstream parser
  recovers all 41 captions (`Annotation breakdown by organism`,
  `Fitness distributions for dark vs annotated genes`, etc.).
- Schema header on line 1 verified.

**Downstream consumers verified unaffected by v2 schema bump:**
- `paper_writer_helpers._parse_figures_inventory_captions` — parses v2
  inventory; recovered 41/41 captions via direct integration test.
- `tools/check_figures_manifest.py` — reads the manifest TSV + section
  files only; never opens `figures_inventory.md` (line 90 + line 147).
  Unaffected.
- `paper_writer.sh` — references inventory by path only (no greps,
  no parsers). Unaffected.
- `prompts/results.v1.md` — declares `FIGURES_INVENTORY_PATH` as a
  Read-tool source; the v2 Description block adds richer source
  material for the LLM, doesn't break the prompt's parser-free contract.

**Behavior NOT yet shipped (deferred to Phase 3):**
- `paper_writer_helpers.py resolve-figures` still emits 3-column TSV
  (paper_order_n, filename, caption). The new `description` column is
  Phase 3 work, gated on Phase 2 (AST) populating the structured
  fields it will assemble from.
- `phase_embed_figures` still injects single-line image tags. Phase 3
  adds the italic `*Description: ...*` paragraph form.

---

## Phase 0 status (DONE 2026-04-28)

**Cosmetic 1: misleading "Copied 0 figure(s)" log.**
- Removed lines 925-936 of `paper_writer.sh` (the legacy filename-grep
  fallback in `phase_results`).
- Replaced with a comment block explaining v0.4 rationale:
  `check_figures_manifest.py` is the authoritative dangling-reference
  detector; the fallback was meaningful in v0.1/v0.2 prose but
  always reported "Copied 0 figure(s)" in v0.3+ prose form.

**Cosmetic 2: `FIGURES_OUT_DIR` not explicitly passed to `results.v1`.**
- Added `- \`FIGURES_OUT_DIR\` = \`$draft_dir/figures\`` to user_prompt
  template at paper_writer.sh:908.
- Verified `results.v1.md` line 107 already declares `FIGURES_OUT_DIR`
  as an expected input; the orchestrator was previously failing to pass
  it (the LLM was inferring the path from `DRAFT_DIR`, working but
  brittle). Contract now consistent: prompt declares + orchestrator
  passes.

**Validation:** Both edits are no-LLM-cost. No regression test required;
`functional_dark_matter` draft_1 doesn't need re-running. Phase 5 live
retest will exercise both implicitly.

---

## v0.4.0 ship checklist (Phase 6)

Same pattern as v0.1 / v0.2 / v0.3 ship cycles. Reference:
`feedback_punch_list_release_pattern.md` (Adam-endorsed; fifth
consecutive cycle).

- `pyproject.toml` + `src/beril_paper_writer/__init__.py` 0.3.0 → 0.4.0
- `RELEASE_NOTES_v0_4.md` authored alongside ship cycle. Documents:
  Tier 8 thesis, v0.3.1 cosmetics absorbed, four-source caption ladder,
  multi-panel awareness Strata 1+2, deferred Tier 7 + Stratum 3 +
  gridspec, real numbers from Phase 5 retest.
- `.commit-message-v0_4_0.txt` with full audit trail per v0.2/v0.3
  pattern. Adam runs `git commit -F .commit-message-v0_4_0.txt` from
  his Mac shell after review.
- `smoke-test/v0_4_0_ship_runbook.sh` with manual git commands printed
  for review (project rule: no git writes in sandbox bash on
  host-mounted repos; memory `feedback_no_git_writes_in_sandbox.md`).
- Tag `v0.4.0` after Adam's review.
- Update auto-memory: supersede `project_paper_writer_v0_3.md` with
  `project_paper_writer_v0_4.md`; cite Tier 7 + v0.5 backlog priority.

---

## v0.5+ deferred (design notes only)

Re-stated for self-containment in this doc:

- **Tier 7 — conceptual diagrams via mermaid-in-markdown.** Hard
  dependency on Tier 2.3 (the docx assembler — satisfied since v0.3.0).
  Adam confirmed 2026-04-27 — option (a) mermaid-in-markdown over
  matplotlib. Renderer choice (Kroki vs mermaid-cli) is a v0.5
  decision. Will add the orchestrator's mermaid → PNG render pipeline
  + a seventh post-checker `tools/check_diagrams.py`.

- **Multi-panel Stratum 3 — vision on PNG for PIL/Inkscape composed
  figures.** Honest gap: figures composed via
  Inkscape/Illustrator post-hoc with no code trace can't be detected
  by AST or prose-regex. Vision API on PNG is the only path. New
  dependency, new cost surface.

- **`gridspec.GridSpec` + `subplot2grid` panel layouts.** Asymmetric
  grid handling. Adds complexity to AST extraction; v0.5 if the smoke
  retest in Phase 5 shows panels are missing for these layouts.

- **`inset_axes` + `add_axes` sub-region detection.** Inset axes
  inside a single panel. Edge case; v0.5 candidate.

- **Tier 4 — interactive checkpoints.** Card elicitation pre-drafting,
  citation-pool exhaustion user pause, throughline re-evaluation
  on artifact drift. Deferred until augmentation stream has a 2nd
  user.

- **Items 4.3 + 5.3 (state-schema bump + migration tool).** Bundled
  when 4.3 lands; 5.3 is the migration tool 4.3 triggers.

- **Follow-up #21** — tighten `fallback_reviewer.v1.md` to mandate
  one canonical finding-header format.

- **M10 architectural redesign** — citation-pool-aware repair routing.

---

*Punch list authored 2026-04-28 immediately after v0.3.0 ship + cycle
scope lock conversation. Multi-panel awareness Strata 1+2 added
mid-conversation per Adam's pushback against the original "defer all
multi-panel to v0.5" framing. Future conversations can pivot off this
doc for fast start; do not re-derive scope.*
