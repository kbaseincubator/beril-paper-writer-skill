# beril-paper-writer v0.6 — punch list

**Created:** 2026-04-29 (post v0.5.0 ship at commit c234645)
**Status:** PLANNING — design conversation complete; punch list under review.
**Cadence:** multi-phase cycle (mirrors v0.3/v0.4 structure). Expect 2–3
sessions.
**Operator:** Adam Arkin, single-user.

This is the authoritative scope for the v0.6 cycle. v0.6's thesis is
**Tier 9 — Tables**: mirror the figures architecture to extract, select,
caption, and embed tabular data in the manuscript. Phase 0 carries
deferred backlog items that ride alongside without blocking the tables
work.

---

## Design decisions (locked 2026-04-29)

1. **Source: REPORT.md-only.** Tables are extracted exclusively from
   markdown pipe-tables in REPORT.md. TSV/CSV files and notebook
   DataFrame outputs are backing data, not paper artifacts. If a table
   is important enough for the paper, the project author formatted it
   as a markdown table in REPORT.md. Revisit in v0.7 if a project
   surfaces where this assumption fails.

2. **No multi-section tables (1A/1B/1C).** Not observed in
   `functional_dark_matter`. If the LLM sees logically-grouped tables,
   it assigns them sequential paper_order_n (Table 1, Table 2).
   Revisit if a future project's REPORT demands it.

3. **Wide-table handling: warn-only.** `check_tables_manifest.py`
   warns at >8 columns; no auto-truncation or transposition. Author
   decides. Parallel to figures (we don't auto-crop).

4. **Cell-content discipline: trust the source.** REPORT.md tables are
   author-curated. Post-checker validates verbatim match between
   inventory and embedded table. No cell-level provenance tracing
   (that's a program-analysis problem, not a text-processing problem).

5. **Caption strategy: deterministic + sufficiency gate + lightweight
   LLM fallback.** Extract caption candidates from REPORT.md context
   (nearest heading + preceding sentence). Sufficiency gate: (a) exists,
   (b) >5 words, (c) ≥1 word overlaps column headers. Gate failure →
   single LLM call: column headers + first 3 data rows → one-sentence
   ICMJE table title. No 4-source ladder, no caption bundles, no audit
   trail per table. Cost: $0 when deterministic captions suffice (likely
   for `functional_dark_matter`).

---

## Architecture parallel with figures

```
FIGURES PIPELINE (v0.3–v0.5)          TABLES PIPELINE (v0.6)
─────────────────────────────         ─────────────────────────────
extract_figures.py                    extract_tables.py
  → figures_inventory.md (v2)           → tables_inventory.md (v1)
  → PNG files already exist             → table content inline in inventory

results.v1 selects 4–8 figures        results.v1 selects N tables
  → copies to draft_dir/figures/        → emits tables_manifest.tsv
  → emits figures_manifest.tsv          → (no file copy needed; content
     (paper_order_n | filename |           is text, not binary)
      inventory_lookup_name)

check_figures_manifest.py             check_tables_manifest.py
  → schema + existence + callout        → schema + inventory xref +
     cross-walk                            callout cross-walk + wide warn

phase_caption_synthesis               table caption gate + LLM fallback
  → Source 4 LLM for gate failures      → single-call LLM for gate failures
  → caption bundles + audit             → inline in inventory (lightweight)

phase_embed_figures                   phase_embed_tables
  → (Fig. N) → ![Figure N: cap](path)  → (Table N) → markdown table block
                                           + "Table N: caption" paragraph

assemble_docx render_image()          assemble_docx render_table()
  → python-docx Picture                  → python-docx Table Grid (EXISTING)
```

**Key simplification vs. figures:** no binary file copy/symlink step,
no savefig AST extraction, no multi-stratum panel detection, no
4-source caption ladder. Tables are text artifacts with self-describing
column headers.

**Manifest schema for tables** (3 columns, parallel to figures):
```
paper_order_n	table_id	inventory_lookup_name
```
- `paper_order_n`: integer (1, 2, 3, ...) — the Table number in the
  final manuscript.
- `table_id`: short slug assigned by results.v1 (e.g.,
  `table01_pathway_gaps`). Human-readable identifier; NOT a filename
  on disk.
- `inventory_lookup_name`: matches an entry's heading/ID in
  `tables_inventory.md` for content + caption retrieval.

---

## Phase 0 — Backlog items (no dependency on tables work)

**Status:** PENDING.
**Deps:** none (can run in parallel with Phase 1 or before it).

These are small infra fixes deferred from v0.4/v0.5. They ride
alongside the tables work to avoid a dedicated point release.

### 0a. `--max-cost-usd` flag on Python CLI

Expose the existing `--max-cost-usd` flag (currently parsed by
`paper_writer.sh` directly) on `commands/draft.py` and
`commands/continue_run.py` argparse interfaces.

**AC:** `python -m beril_paper_writer draft --max-cost-usd 5.00`
parses without error; value is passed through to the orchestrator.

### 0b. Python 3.14 pytest collection compat

8 pre-existing tests fail to collect under Python 3.14.4. Either fix
the collection issues or document the pin to 3.10/3.12.

**AC:** `pytest --collect-only` succeeds on Python 3.14 OR
`pyproject.toml` documents the supported-Python range explicitly.

### 0c. `phase_caption_synthesis` skip-existing-captions

Currently re-invokes LLM on every run. Detect existing
`audit/figure_caption_<N>.md` files and skip. Add `--recaption` flag
to force regeneration.

**AC:** Second run with no source changes produces no LLM calls.
`--recaption` forces full regeneration.

### 0d. Recovery runbook `reviews/` directory handling

The v0.4 retest recovery runbook moved `reviews/` to
`.recovery_backup/` without recreating it; `stream_progress.py`
then crashed. Ensure `reviews/` is recreated or its absence is
handled gracefully.

**AC:** `stream_progress.py` does not crash when `reviews/` is
absent.

### 0e. `gridspec.GridSpec` / `subplot2grid` panel detection

`_extract_plot_calls` in `extract_figures.py` doesn't detect
`gridspec.GridSpec` or `subplot2grid` panel layouts.

**AC:** Figures created via GridSpec are detected with correct
panel count. Unit tests for both call patterns.

---

## Phase 1 — extract_tables.py + tables_inventory.md

**Status:** PENDING.
**Deps:** none.
**Smoke gate:** run against `functional_dark_matter`; confirm 9
tables inventoried with caption candidates and structure metadata.

### 1a. TableRecord + TableInventoryReport dataclasses

```python
@dataclass
class ReportTableOrigin:
    """Where a table was found in REPORT.md."""
    line_start: int       # first pipe-row line number
    line_end: int         # last pipe-row line number
    section_heading: str  # nearest ## or ### heading above

@dataclass
class TableCaptionCandidate:
    """One caption candidate for a table."""
    source: str           # "heading" | "preceding_sentence" | "llm"
    text: str
    context: str          # section name or surrounding text

@dataclass
class TableRecord:
    """All metadata for one REPORT.md table."""
    table_id: str               # sequential: "report_tbl_01", ...
    section_heading: str        # nearest heading
    column_names: list[str]     # parsed from header row
    column_count: int
    row_count: int              # data rows (excluding header + separator)
    column_types: list[str]     # heuristic: "numeric" | "text" | "mixed"
    markdown_content: str       # full pipe-table text (verbatim)
    captions: list[TableCaptionCandidate]
    origin: ReportTableOrigin

@dataclass
class TableInventoryReport:
    """Top-level report from a table-extraction run."""
    project_dir: str
    source_file: str      # always "REPORT.md" for v0.6
    tables: list[TableRecord]
```

**AC:** dataclasses defined; JSON-serializable via `dataclasses.asdict`.

### 1b. REPORT.md markdown-table scanner

Parse REPORT.md for pipe-delimited table blocks (lines starting and
ending with `|`, with a `|---|` separator on the second line). Reuse
the same regex pattern as `assemble_docx.py:_TABLE_SEPARATOR_RE` for
consistency.

**AC:** scanner finds all 9 tables in `functional_dark_matter`
REPORT.md. Edge cases: (a) table at end of file (no trailing blank
line), (b) table immediately after a heading with no blank line,
(c) pipe characters inside code blocks (must NOT be detected as
tables).

### 1c. Caption candidate extraction

For each table, extract caption candidates:
1. **Heading source:** nearest `##` or `###` heading above the table.
2. **Preceding-sentence source:** last sentence of the paragraph
   immediately before the table (the "introductory sentence").

Walk upward from `line_start` to find heading and preceding paragraph.
If no heading exists within 50 lines, heading source is empty. If no
preceding paragraph exists (table directly after heading), preceding-
sentence source is empty.

**AC:** 9/9 `functional_dark_matter` tables have at least one non-empty
caption candidate. Unit tests for: heading-only, sentence-only, both,
neither (edge case — table at file start).

### 1d. Structure metadata extraction

For each table:
- `column_names`: split header row on `|`, strip whitespace.
- `column_count`: len(column_names).
- `row_count`: count of data rows (lines after separator, still
  matching pipe pattern).
- `column_types`: heuristic per column — scan all data cells; if >80%
  parse as numeric (int/float/scientific), mark "numeric"; if all text,
  mark "text"; else "mixed". `|fit|` columns with leading `|` are a
  known trap — handle the double-pipe pattern.

**AC:** metadata correct for all 9 tables. The `|fit|` column in the
ortholog/carrier tables is correctly parsed (it's a column name with
pipes, not a cell boundary).

### 1e. Emit tables_inventory.md (v1 schema)

Write `<draft_dir>/tables_inventory.md` with one section per table:

```markdown
# Tables inventory (v1)

## report_tbl_01 — Pathway gap summary

_Section: "Dark-gene pathway gaps across 48 organisms"_
_Columns: 4 (Pathway | Category | Organisms with gaps | Example organisms)_
_Rows: 6_
_Column types: text | text | numeric | text_

**Caption candidates:**
- **heading**: Dark-gene pathway gaps across 48 organisms
- **preceding_sentence**: The following table summarizes the most ...

**Content (first 3 rows):**
| Pathway | Category | Organisms with gaps | Example organisms |
|---------|----------|--------------------:|-------------------|
| Fucose utilization | carbon | 32 | Marinobacter, ... |
| Rhamnose utilization | carbon | 31 | Marinobacter, ... |
| Sorbitol utilization | carbon | 30 | *D. desulfuricans*, ... |

_(6 data rows total; see REPORT.md lines 33–39 for full table)_
```

**AC:** inventory written; parseable by downstream phases.

### 1f. Unit tests

Target: ≥15 tests covering scanner (pipe detection, code-block
exclusion, edge positions), caption extraction (heading/sentence/both/
neither), metadata (column types, |fit| trap, row counts), inventory
formatting, JSON serialization.

**AC:** all tests pass; no mocks of REPORT.md content — use synthetic
markdown strings.

---

## Phase 2 — results.v1 contract extension + tables_manifest.tsv

**Status:** PENDING.
**Deps:** Phase 1 (inventory must exist for results.v1 to select from).
**Smoke gate:** run results.v1 against `functional_dark_matter` draft_4;
verify valid `tables_manifest.tsv` emitted alongside `figures_manifest.tsv`.
LLM cost: ~$2–3 for a full results.v1 invocation.

### 2a. Add TABLES_INVENTORY_PATH input variable

Add `TABLES_INVENTORY_PATH` to results.v1's input contract (parallel
to `FIGURES_INVENTORY_PATH`). The orchestrator passes the path to
`tables_inventory.md` produced by Phase 1.

### 2b. Add table selection sub-process to results.v1

Extend the "Figure selection" section with a parallel "Table selection"
block:

- Each selected table must support a specific sub-claim.
- Selection draws from `tables_inventory.md` entries.
- Maximum 4–6 tables (ICMJE convention: figures + tables combined
  rarely exceed 12; with 4–8 figures, 4–6 tables is reasonable).
- If inventory is empty or absent, proceed without tables (soft
  warning, parallel to the figures-absent case at line 146).

### 2c. Add tables_manifest.tsv emission contract

After table selection, emit `<DRAFT_DIR>/tables_manifest.tsv`:

```
paper_order_n	table_id	inventory_lookup_name
1	table01_pathway_gaps	report_tbl_01
2	table02_concordant_groups	report_tbl_02
```

Three columns, tab-separated, header row + one data row per selected
table. Schema documented in the prompt with a worked example.

### 2d. Prompt version bump

Bump `results.v1.md` version comment. The prompt's existing line 258
("Tables follow the same logic but the inventory doesn't enumerate them
(yet)") is replaced with the full contract.

### 2e. Update orchestrator phase_results

In `paper_writer.sh:phase_results()`, pass `TABLES_INVENTORY_PATH`
to the LLM invocation alongside `FIGURES_INVENTORY_PATH`.

### 2f. Unit tests

- Manifest parsing (valid 3-col TSV; malformed input; empty manifest).
- Inventory lookup resolution (table_id → inventory entry).
- Round-trip: synthetic inventory → synthetic manifest → lookup succeeds.

**AC:** ≥8 tests; all pass.

---

## Phase 3 — check_tables_manifest.py post-checker

**Status:** PENDING.
**Deps:** Phase 2 (manifest must exist).

### 3a. Schema validation

Header matches `["paper_order_n", "table_id", "inventory_lookup_name"]`.
Each data row has 3 tab-separated cells. `paper_order_n` is a valid
integer. Mirror `check_figures_manifest.py` lines 75–99.

### 3b. Inventory cross-reference

Each `inventory_lookup_name` resolves to an entry in
`tables_inventory.md`. Warn on unresolvable references.

### 3c. Callout cross-walk

For each `(Table N)` callout in section files (`02_results.md`,
`01_methods.md`, `03_discussion.md`), the manifest has a matching
`paper_order_n`. Surface NOTEs for manifest entries with no callouts
(tables selected but never referenced in prose).

### 3d. Wide-table warning

For each manifest entry, look up column_count from inventory. Warn
if >8 columns: `"WARNING: Table N has K columns; may render narrow
in docx. Column names: [list]. Consider whether all columns are
necessary."` Advisory only (exit 0).

### 3e. Wire into orchestrator

Add `phase_check_tables_manifest()` to `paper_writer.sh`, slotted
immediately after `phase_check_figures_manifest`.

### 3f. Unit tests

≥10 tests: valid manifest, missing columns, bad paper_order_n,
unresolvable inventory ref, missing callout, orphan callout,
wide-table warning fires at 9 columns, does not fire at 8.

**AC:** all pass; checker exits 0 (advisory) in all cases.

---

## Phase 4 — Caption sufficiency gate + lightweight LLM fallback

**Status:** PENDING.
**Deps:** Phase 1 (caption candidates in inventory).

### 4a. Sufficiency gate

`_table_caption_passes_gate(caption_text, column_names) -> bool`:
1. Caption exists and is non-empty.
2. Caption is >5 words after stripping markdown formatting.
3. At least 1 word in caption also appears in column_names
   (case-insensitive; skip stopwords). This is a weak relevance
   signal — it catches "Results summary" (no column words) but
   passes "Pathway gap summary across organisms" when columns
   include "Pathway" and "Organisms".

**AC:** gate passes for all 9 `functional_dark_matter` tables
(verified by unit test with real heading text + column names).

### 4b. Lightweight LLM fallback

When the gate fails, invoke a single LLM call:

**Input:** column headers + first 3 data rows + section heading
(if available).

**Prompt:** "Write a one-sentence ICMJE-style table title for a
scientific manuscript. The title should state what the table shows,
not interpret it. Maximum 25 words. Do not fabricate data not present
in the rows shown."

**Output:** one-sentence caption string.

**Cost:** ~$0.02 per failed table (single short prompt + short
completion). Expected $0 for `functional_dark_matter` (all gates
should pass).

### 4c. Wire into inventory

The LLM-generated caption is added to the TableRecord's `captions`
list with `source="llm"`. The embed phase picks the highest-priority
caption: heading > preceding_sentence > llm.

### 4d. Wire into orchestrator

Add `phase_table_captions()` to `paper_writer.sh`, slotted after
`phase_caption_synthesis` (figures) and before `phase_embed_tables`.

### 4e. Unit tests

- Gate: passes on good heading + relevant column names.
- Gate: fails on empty caption.
- Gate: fails on short caption (<5 words).
- Gate: fails on irrelevant caption (no column-word overlap).
- LLM fallback: mock test confirming the prompt is well-formed and
  output is parsed correctly.
- Priority ordering: heading > preceding_sentence > llm.

**AC:** ≥8 tests; all pass.

---

## Phase 5 — phase_embed_tables

**Status:** PENDING.
**Deps:** Phase 2 (manifest), Phase 3 (checker), Phase 4 (captions).
**Smoke gate:** run against draft_4 output; confirm tables appear
in section markdown with captions.

### 5a. `cmd_embed_tables_in_draft()`

Mirror `cmd_embed_figures_in_draft()`. Steps:
1. Parse `tables_manifest.tsv` → list of (paper_order_n, table_id,
   inventory_lookup_name).
2. Parse `tables_inventory.md` → map of inventory_lookup_name →
   (markdown_content, best_caption).
3. Build `table_map`: paper_order_n → {table_id, caption,
   markdown_content}.
4. Walk section files for `(Table N)` callouts.
5. After the sentence containing the callout, inject:
   ```
   **Table N.** Caption text.

   | col1 | col2 | ... |
   |------|------|-----|
   | data | data | ... |
   ```
6. Track `already_embedded` set for idempotency.

### 5b. Callout regex

`(Table N)` or `(Table N and Table M)` — parallel to the figure
callout regex. Handle `(Tables N–M)` range syntax as a NOTE (not
auto-expanded; warn and skip).

### 5c. Caption paragraph styling

The injected caption paragraph uses `**Table N.** Caption text.`
format. `assemble_docx.py` will render the bold prefix via inline
markdown parsing (already supported by `render_inline_runs`). The
table itself renders via the existing `render_table()` function.

Verify: does `assemble_docx.py` produce a Caption-styled paragraph
for the `**Table N.**` prefix, or do we need to extend
`render_blocks()` to detect table-caption patterns and apply the
Caption style? Check and extend if needed.

### 5d. Verbatim content check

After embedding, the markdown table text injected into the section
file must match the inventory's `markdown_content` exactly (byte-for-
byte after whitespace normalization). This guards against the embed
phase silently corrupting table data. Emit a WARNING to stderr if
mismatch detected.

### 5e. Unit tests

≥12 tests: single callout, multi-callout in one section, callout in
different sections, idempotency (double-run produces same output),
no-callout-in-prose (table selected but not referenced), missing
inventory entry, verbatim match check, caption injection format.

**AC:** all pass.

---

## Phase 6 — Orchestrator wiring + integration test

**Status:** PENDING.
**Deps:** Phases 1–5.

### 6a. Full phase sequence in paper_writer.sh

Insert table phases into the orchestrator:

```
phase_extract          (EXISTING — add extract_tables.py call)
  ...
phase_results          (EXISTING — now emits tables_manifest.tsv)
  ...
phase_check_figures_manifest   (EXISTING)
phase_check_tables_manifest    (NEW — Phase 3)
phase_caption_synthesis        (EXISTING — figures)
phase_table_captions           (NEW — Phase 4)
phase_check_caption_provenance (EXISTING — figures only for now)
phase_embed_figures            (EXISTING)
phase_embed_tables             (NEW — Phase 5)
phase_assemble                 (EXISTING)
```

### 6b. Phase skip logic

If `tables_inventory.md` is empty (no tables in REPORT.md), all
table phases are no-ops with an informational log line. Parallel to
the figures-absent skip.

### 6c. Integration test

Synthetic project with 3 markdown tables in a fake REPORT.md. Run
the full orchestrator sequence (extract → results → check → caption
→ embed → assemble). Verify docx output contains 3 rendered tables
with captions.

**AC:** integration test passes end-to-end. No LLM calls needed
(use `REPAIR_MODE=skip` or synthetic results section).

---

## Phase 7 — Live retest + visual review

**Status:** PENDING.
**Deps:** Phase 6.
**LLM cost:** ~$3–5 for a full draft run (results.v1 is the
expensive phase; table phases add <$0.10).

### 7a. Run draft_4 against `functional_dark_matter`

Full orchestrator run. Confirm:
- `tables_inventory.md` emitted with 9 entries.
- `tables_manifest.tsv` emitted (results.v1 selects ≥3 tables).
- `check_tables_manifest.py` passes with no errors (warnings OK for
  wide tables).
- Table captions pass sufficiency gate (no LLM fallback invoked).
- Tables embedded in section markdown after `(Table N)` callouts.
- `manuscript.md` contains markdown tables.
- `manuscript.docx` contains rendered tables with captions.

### 7b. Visual review of docx

Open docx and check:
- Tables render with visible borders (Table Grid style).
- Header rows are bold.
- Caption paragraphs appear above each table.
- Wide tables (9-column) are readable (columns not collapsed to
  zero width).
- No table data corruption (spot-check 3 cells against REPORT.md).

### 7c. Figures regression check

Confirm figures pipeline is unaffected:
- Same number of figures embedded as v0.5.
- Figure captions unchanged.
- No new validator failures.

---

## Phase 8 — Ship

**Status:** PENDING.
**Deps:** Phase 7 (visual review GREEN).

### 8a. Version bump

`pyproject.toml` + `src/beril_paper_writer/__init__.py`: 0.5.0 → 0.6.0.

### 8b. RELEASE_NOTES_v0_6.md

Document: tables pipeline architecture, design decisions, known
limitations, upgrade notes (v0.5 → v0.6).

### 8c. Wheel build + install-skill round-trip

`pipx install --force .` succeeds; `beril-paper-writer --help` shows
table-related phases.

### 8d. Commit message + tag prep

`.commit-message-v0_6_0.txt` staged for Adam's `git commit -F`.
Tag `v0.6.0` after commit.

### 8e. Update auto-memory

New entry `project_paper_writer_v0_6.md` superseding v0.5.

---

## Deferred to v0.7+

- **TSV/CSV source support.** If a project's important tables live
  in data files rather than REPORT.md, extract_tables.py gains a
  second source path.
- **Multi-section tables (Table 1A/1B/1C).** If a project demands it.
- **Landscape-mode tables in docx.** For truly wide tables that
  can't fit portrait.
- **Table caption provenance checker.** Parallel to
  `check_caption_provenance.py` for figures — detects fabricated
  table captions. Lower priority because table captions are shorter
  and mostly deterministic.
- **Cell-level provenance tracing.** Link table cell values back to
  notebook computations. Research-grade problem.
- **`gridspec.GridSpec` panel detection** (if not completed in
  Phase 0e).
- **Stratum 3 multi-panel (vision API).** Figures composed via
  Inkscape/Illustrator.

---

*Authored 2026-04-29 as the v0.6 cycle planning artifact. Design
decisions locked after critical conversation (Adam + Claude). See
`project_paper_writer_v0_6.md` in auto-memory for cross-session
reference.*
