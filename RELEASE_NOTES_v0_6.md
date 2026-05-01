# beril-paper-writer v0.6.0 — Release Notes

**Date:** 2026-04-29 (pending live retest)
**Prior:** v0.5.0 (commit c234645)

## Summary

v0.6.0 adds **Tier 9 — Tables**: a full pipeline to extract, select,
caption, and embed tabular data from REPORT.md into scientific
manuscripts. The architecture mirrors the v0.3 figures pipeline
(extract → inventory → LLM selection → manifest → post-checker →
embed → docx render) but is simpler — tables are text, not binary
files, so no file-copy or AST panel-detection is needed.

## New features

**Tables pipeline (6 components):**

- `extract_tables.py` — scans REPORT.md for markdown pipe-tables,
  handles the `|fit|` double-pipe column-name trap (two-pass triplet
  detection), produces structured `tables_inventory.md` with caption
  candidates (section heading + preceding sentence), column types
  (numeric/text/mixed), and content previews (first 3 rows).

- `results.v1.md` prompt extension — adds `TABLES_INVENTORY_PATH`
  input variable, table selection sub-process (1–6 tables from
  inventory), `tables_manifest.tsv` emission contract
  (`paper_order_n | table_id | inventory_lookup_name`), escape hatches
  for missing/empty inventory, HALT discipline for callout↔manifest
  consistency, and self-review checklist item 6b.

- `check_tables_manifest.py` — advisory post-checker (always exits 0).
  5 checks: schema validation, inventory cross-reference, `(Table N)`
  callout cross-walk, wide-table warning (>8 columns), duplicate
  detection.

- Caption sufficiency gate — deterministic filter in
  `paper_writer_helpers.py`. >10-word headings pass without column
  overlap; short captions (6–10 words) need ≥1 column-name word
  match; stopword-only columns pass as edge case.

- `embed-tables` CLI command — walks section files for `(Table N)`
  callouts, injects `**Table N.** Caption` + markdown table content
  after the callout line. Idempotent via `_EMBEDDED_TABLE_RE`
  pre-scan (re-running doesn't double-inject).

- Orchestrator wiring — `paper_writer.sh` runs `extract_tables.py`
  in the extract phase, passes `TABLES_INVENTORY_PATH` to results.v1,
  and invokes `phase_check_tables_manifest` + `phase_embed_tables`
  in both the main flow and the rewrite-loop re-assembly path.

**Backlog items (carried from v0.4/v0.5):**

- `--max-cost-usd` and `--recaption` flags exposed on Python CLI
  (`beril-paper-writer draft` and `beril-paper-writer continue`).
  Previously only available via `paper_writer.sh` directly.

- `phase_caption_synthesis` skip-existing-captions: LLM figure
  captions are not re-synthesized on pipeline re-run if
  `audit/figure_caption_<N>.md` already exists. Use `--recaption`
  to force regeneration.

- `reviews/` directory handling: `continue_run.py` no longer crashes
  on `phase=assembled` when `reviews/` is absent.

## Test count

540 tests (430 baseline + 59 extract_tables + 26 check_tables_manifest
+ 25 tables_pipeline). All pass.

## Design decisions

- **REPORT.md-only source for v0.6.** TSV/CSV/DataFrame notebook
  outputs are backing data, not paper artifacts. Deferred to v0.7.

- **No LLM caption fallback in v0.6.** The sufficiency gate is
  deterministic-only. LLM caption synthesis (Source 4 equivalent for
  tables) deferred to v0.7 if the gate proves too strict.

- **Manifest uses `table_id` not `filename`.** Tables have no binary
  files; the slug is for readability only. The join key is
  `inventory_lookup_name` (e.g. `report_tbl_01`).

- **`\bTable\s+(\d+)\b` regex for callouts.** Uses word boundary
  instead of paren anchor to catch compound callouts like
  `(Table 1 and Table 3)`.

## Migration

No breaking changes. v0.5 drafts work unmodified; tables are
additive. Existing `figures_manifest.tsv` and figure pipeline
unchanged.
