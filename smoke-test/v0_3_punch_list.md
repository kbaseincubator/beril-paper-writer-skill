# beril-paper-writer v0.3 — punch list

**Created:** 2026-04-27 (post v0.2.1 ship)
**Status:** v0.3.0 SHIPPED 2026-04-27 (live retest passed; $6.49; 10
Pictures + 10 Caption paragraphs in functional_dark_matter docx).
**Cadence:** push hard, evaluable output at every step, decision points
between items. Same tiered structure that worked for v0.1.x and v0.2
(see `feedback_punch_list_release_pattern.md` — Adam-endorsed).
**Operator:** Adam Arkin, single-user, hands-on through the cycle.

This document is the authoritative scope for the v0.3 patch cycle.
Tier 2 — figures + docx assembly — is the v0.3 thesis. Tier 7
(conceptual diagrams via mermaid) is post-Tier-2 and out of scope for
v0.3 unless Tier 2 ships fast enough that Tier 7 fits in the same
cycle (unlikely; design notes preserved in v0.2 punch list and
re-cited at the bottom of this doc).

**Hard cycle constraint:** Adam confirmed 2026-04-27 — sequence (a):
2.4 → 2.3 → 2.1 → 2.2 → live retest → ship. Rationale: 2.4 + 2.3
produce a real evaluable docx artifact from the existing draft_1
without any LLM cost; 2.1 + 2.2 then close the figure-embedding loop
with a $5 retest at the end.

---

## Tier 2 — Figures + docx assembly

**Biggest visible UX improvement of the cycle.** v0.2's manuscript
ships as `manuscript.md` only; users insert figures by hand. v0.3
closes this gap and produces submission-ready `manuscript.docx` with
inline figures and figure captions.

### Item 2.4 — Wire `commands/assemble.py` to `tools/assemble_docx.py`

**Status: DONE** (2026-04-27).

**Shipped:**

- `tools/assemble_docx.py` — standalone script with `render_stub`
  emitting a 4-paragraph valid docx via `python-docx`. Argparse over
  `(input_md, output_docx)`. Full markdown→docx rendering deferred to
  2.3.
- `commands/assemble.py` — replaces the not-implemented stub.
  Validates draft_dir + manuscript.md, dispatches by `--format`:
  `md` is identity (prints path), `docx` subprocesses
  `tools/assemble_docx.py` with `sys.executable`, `pdf` rejected with
  explanatory stderr (post-MVP).
- `tests/unit/test_cli.py` — 5 new tests; all pass. Pre-existing
  `test_continue_stub_returns_2` failure is stale and unrelated to
  this work (continue_run.py was un-stubbed in v0.1).

**Smoke evidence:** `beril-paper-writer assemble draft_1 --format
docx` produces a valid `manuscript.docx` openable in Word/Pages/
LibreOffice. `--format md` and `--format pdf` paths verified.

---

### Item 2.3 — Implement `tools/assemble_docx.py` (markdown→docx renderer)

**Status: DONE** (2026-04-27). 470 lines. 26 unit tests pass. Live retest
produced 64KB docx from 84KB manuscript.md with 8/8 H1, 35/35 H2, 7/7 H3,
143/143 bullets, 57/57 [N] citations preserved; bold/italic round-trips
correctly (verified via run inspection).

**Problem.** v0.3-stub `render_stub` produces a placeholder; 2.3
replaces it with a real markdown→docx renderer.

**Constraint:** no new dependencies. python-docx (declared in
pyproject.toml v0.2.1) is the only library; markdown parsing is
hand-rolled. Per Adam's "should not add new deps" call on 2026-04-27.

**Design — block-level parser.** Line-based block parse with state
machine for multi-line elements (tables, code blocks). Each block
type maps to a python-docx render function:

| Markdown block | docx output |
|---|---|
| `# Heading` | `add_heading(text, level=1)` |
| `## Heading` | `add_heading(text, level=2)` |
| `### Heading` | `add_heading(text, level=3)` |
| `#### Heading` | `add_heading(text, level=4)` |
| Paragraph (blank-line separated) | `add_paragraph()` + inline runs |
| `- item` (bullet list) | `add_paragraph(text, style='List Bullet')` |
| `1. item` (numbered list) | `add_paragraph(text, style='List Number')` |
| <code>&#124; col1 &#124; col2 &#124;</code> table | `add_table()` with parsed cells |
| `> blockquote` | `add_paragraph(text, style='Quote')` |
| <code>&#96;&#96;&#96; code &#96;&#96;&#96;</code> fenced block | `add_paragraph` with monospace runs |
| `![alt](path)` (line alone) | Picture + Caption-styled paragraph |
| `---` horizontal rule | `add_paragraph()` (visual separator; thin) |

**Inline parsing.** Within paragraphs and headings, regex-based
inline span detection (order matters):

1. Code spans `` `text` `` first (consume backtick-delimited regions
   so other regex don't see brackets/asterisks inside code).
2. Image tags `![alt](path)` — only match when on their own line at
   block level; in inline context, treat as text (will not occur in
   v0.3 since 2.2 always emits images as own-line blocks).
3. Bold `**text**`.
4. Italic `*text*` or `_text_`.
5. Inline links `[text](url)` — render as text + (URL) parenthetical
   for v0.3 (no clickable hyperlinks; defer python-docx hyperlink
   plumbing to v0.4 if requested).
6. Bare `[N]` citation form preserved verbatim — important: do NOT
   match citations as link syntax (the surrounding `(url)` is what
   distinguishes a link from a citation; bare `[N]` stays text).

**Image rendering — the load-bearing path** (2.2's contract):

- Block image tag form: `![Figure N: caption text](figures/<name>)`
  per Wrinkle B canonicalization (see below).
- Path resolved relative to the input markdown file's directory
  (i.e. `<draft_dir>/figures/<name>`). Reject paths outside the
  draft_dir as a defensive measure (no `../` or absolute paths).
- python-docx: `doc.add_picture(path, width=Inches(6))` for ~standard
  6-inch column width fitting US Letter / A4. Picture goes in its
  own paragraph (centered).
- Caption paragraph follows: alt-text from the image tag, styled
  `Caption`. If `Caption` style isn't in the document's default
  template, fallback to italic Normal.
- Width-fit: cap at `Inches(6)`; figures wider than that scale down
  preserving aspect ratio (python-docx handles aspect when only one
  dimension is supplied).

**Table rendering.**

- Detect table block: ≥2 lines, first line contains `|`, second line
  is the separator (`|---|---|` form), subsequent lines are data
  rows until blank line.
- Build python-docx table with `add_table(rows=N, cols=M)`.
- Apply `Table Grid` style for visible borders.
- First row gets bold runs (treat as header row).
- Cell content inline-parsed (bold/italic/code etc.).

**Citation handling.** `[N]` numeric citations from
`citation_pool.py render-with-numbers` (v0.1+) pass through
verbatim. The references.md section already produces a numbered list
of references; the renderer just preserves the `[N]` markers in
prose and the numbered-list paragraphs in references.

**Code blocks.**

- Fenced ` ``` ` blocks: detect opening fence, consume until closing
  fence, render each line as a paragraph with monospace font (Courier
  New, 10pt) — no syntax highlighting.
- Indented code (4-space prefix): NOT supported in v0.3 (rare in
  scientific manuscripts; defer if needed).

**File layout.**

`tools/assemble_docx.py`, expected ~400–500 lines. Module structure:

```
_check_python_docx()                 -- existing
parse_blocks(text) -> list[Block]    -- block parser (state machine)
render_inline(paragraph, text)       -- inline runs into paragraph
render_image(doc, alt, path, base)   -- Picture + Caption
render_table(doc, lines)             -- markdown table → docx table
render_code_block(doc, lines)        -- fenced code → monospace paragraph
render_document(input_md, output)    -- top-level dispatcher (replaces render_stub)
main(argv)                           -- existing argparse harness; calls render_document
```

`Block` is a small dataclass: `kind: str`, `content: str | list[str]`,
`raw_lines: list[str]`. Pure-Python, no nesting beyond what python-docx's
Document model handles natively.

**Synthetic fixture for image-path coverage** (the integration-risk
mitigation Adam called out): tests/unit/test_assemble_docx.py with
fixtures including:

- Heading-only markdown
- Paragraph with bold + italic + code spans
- Paragraph + image tag block + paragraph (verifies Picture insertion +
  caption)
- Markdown table (3 rows × 2 cols)
- Numbered list of 3 items
- Fenced code block

The image fixture uses a 1×1-pixel PNG generated in the test setup
(no real image files committed to the repo).

**AC:**

- Run `beril-paper-writer assemble draft_1 --format docx` against
  the existing `functional_dark_matter` draft_1's `manuscript.md`;
  produces `manuscript.docx` with: title block, IMRAD section
  headings, paragraphs of prose, the references list at the end. No
  python-docx exceptions on a real ~84KB manuscript.md.
- Visually open the docx in Word/Pages/LibreOffice; verify section
  headings are styled, paragraphs flow, no obvious rendering
  failures.
- Synthetic-fixture tests pass (heading variants + bold/italic/code
  + paragraph + image + table + list + code block).
- No new pyproject.toml dependencies.

**Smoke gate:** $0 LLM cost. Adam reviews the docx visually; thumbs
up before moving to 2.1.

**Estimated effort:** 3–5 days.

---

### Item 2.1 — `results.v1` prompt edit + figures_manifest.tsv contract + post-checker + resolve-figures helper

**Status: DONE** (2026-04-27). All three landings shipped:
- 2.1a: 6 prompt edits to results.v1.md (HALT, manifest emission,
  closing-message, anti-example pair, output-protocol step 4, callout
  load-bearing).
- 2.1b: tools/check_figures_manifest.py (290 lines; 4 cross-walks).
  Wired in paper_writer.sh; surfaces in next_actions.md via emit-next-actions.
- 2.1c: paper_writer_helpers.py resolve-figures subcommand. Smoke against
  live functional_dark_matter inventory: 4/4 captions correctly resolved
  from REPORT-derived bullets.

Three landings, all ~$0 to evaluate (smoke against existing draft_1
artifacts).

#### 2.1a — `results.v1.md` prompt edit

Per current shipped state, `results.v1.md` lines 49–50 mark figure
callouts as advisory ("when applicable"). The current `draft_1`
already has 11 `(Fig. N)` callouts in `02_results.md` — the
discipline already works in practice on at least this project. 2.1
hardens the contract:

- Anti-example pair: FAIL = prose with no figure callouts (figures
  selected but not cited); PASS = `(Fig. N)` callout after each
  sentence the figure supports.
- HALT in self-review: `if FIGURES_INVENTORY_PATH is provided AND
  no (Fig. N) callouts appear in your prose, HALT and re-walk. The
  orchestrator's figure-embedding step depends on these.`
- Contract documented: results.v1 produces `(Fig. N)` callouts;
  orchestrator's `phase_embed_figures` (Item 2.2) injects
  `![Figure N: <caption>](figures/<name>)` after the first sentence
  containing each `(Fig. N)`.

Plus the new manifest emission (Wrinkle A canonicalization):

- After selecting + copying figures, `results.v1` emits
  `<DRAFT_DIR>/figures_manifest.tsv` with three tab-separated
  columns: `paper_order_n`, `filename`, `inventory_lookup_name`.
  Header row is the column names. One row per selected figure.
- `paper_order_n` is the integer N that the prose's `(Fig. N)`
  callouts use. `filename` is the paper-order rename
  (e.g. `fig01_dark_gene_census.png`) — the file as it now exists
  in `<DRAFT_DIR>/figures/`. `inventory_lookup_name` is the original
  filename in `figures_inventory.md` (e.g.
  `fig01_annotation_breakdown.png`) — the join key for caption
  resolution.
- **Banned-tab discipline:** none of the three values may contain a
  tab character. Filenames are filesystem-safe by construction.
  `paper_order_n` is integer. `inventory_lookup_name` is also a
  filesystem name. The prompt is *never* asked to write captions
  into the manifest; captions live in `figures_inventory.md` and are
  resolved at consume time. This avoids the JSON-quoting trap from
  `feedback_llm_json_unfixable_in_parser.md`.
- Closing-message contract grows by one line: `figures_manifest.tsv
  emitted with K rows.` K must equal the number of fig01_*.png …
  fig0N_*.png files in `<DRAFT_DIR>/figures/`.

**AC:**

- Run `results.v1` in REPAIR_MODE on existing draft_1 (or against a
  synthetic fixture) — verify the new HALT triggers if `(Fig. N)`
  callouts are absent.
- Verify `figures_manifest.tsv` is written with the documented
  schema; banned-tab check holds.
- No regression on the v0.2.1 closing-message parsing.

#### 2.1b — `tools/check_figures_manifest.py` post-checker

Fifth post-processor in the v0.2 pattern (joins
`check_throughline_glyphs`, `check_scope_coherence`, `check_overclaim`,
`check_repair_scope`).

Three checks per draft:

1. **Manifest existence + parsability:** `figures_manifest.tsv` exists
   in `<draft_dir>`; readable as TSV with header
   `paper_order_n\tfilename\tinventory_lookup_name`. WARN if missing,
   ERROR-style WARN if malformed.
2. **Filename existence:** every row's `filename` column resolves to a
   real file in `<draft_dir>/figures/`. WARN per missing file.
3. **Closing-message round-trip:** if `audit/results.metadata.json`
   shows the closing message included a `figures_manifest.tsv emitted
   with K rows` line, K must equal the manifest row count. WARN on
   mismatch.

stderr WARN/NOTE; always exit 0; orchestrator surfaces via
`emit-next-actions`.

Wired in `paper_writer.sh` between `phase_results` and
`phase_check_scope_coherence`. Same skeleton as the four existing
post-processors.

#### 2.1c — `paper_writer_helpers.py resolve-figures` subcommand

Joins `figures_manifest.tsv` against `figures_inventory.md` to
produce paper_order_n → caption + filename for 2.2's consumption.

CLI: `paper_writer_helpers.py resolve-figures <draft_dir>` →
emits one TSV-formatted line per selected figure to stdout:

```
paper_order_n\tfilename\tcaption
```

Caption sourced from inventory's caption-candidate ranking
(REPORT-derived first, notebook-context second, filename third —
already established in `extract_figures.py`'s output). The helper
parses `figures_inventory.md`'s caption candidate blocks; picks the
highest-ranked candidate per `inventory_lookup_name`.

**Banned-tab discipline applied here too**: caption strings may
contain newlines but never tabs in the inventory format we ship.
Defensive: replace any tab in the caption with a single space at
emit time + emit a stderr WARN. Documented in the helper's
docstring.

**AC:**

- `paper_writer_helpers.py resolve-figures draft_1` against an
  existing manifest produces N TSV rows where N = manifest row
  count.
- Captions match the REPORT-derived strings from `figures_inventory.md`
  (verified manually for 2-3 entries on first smoke).
- Helper handles missing inventory entries (row has manifest entry
  but inventory lookup misses) by falling back to filename-derived
  caption with a stderr WARN.

**Estimated effort for 2.1 (a + b + c):** 1–2 days. Smoke: $0.

---

### Item 2.2 — `phase_embed_figures` + orchestrator-side stale-file cleanup

**Status: DONE** (2026-04-27). Implementation in
paper_writer_helpers.py: `cmd_embed_figures` + `_find_sentence_end_after`
(skips Fig. N internal periods, e.g. lowercase abbreviations) +
`_build_figure_map` + `_embed_figures_in_text`. 14 unit tests pass.
phase_embed_figures wired in paper_writer.sh main case block AND in
phase_review_rewrite re-assembly path (idempotent semantics ensure no
double-injection). Stale-file cleanup at top of phase_results
(drafting-mode-only, skip in REPAIR_MODE) per D-025.

Live retest: first pass embedded 10/10 figures into 02_results.md
(callouts went up to N=8 with panel suffixes A/B; 8 unique Ns +
multi-figure handling worked); rewrite-loop passes 2-3 correctly
no-opped (idempotent skip). Final docx has 10 Pictures + 10 Caption
paragraphs.

New phase in `paper_writer.sh` between `phase_results` and the
existing `phase_check_scope_coherence` (i.e. before assembly).

**Behavior.**

1. Read `figures_manifest.tsv` (skip if missing — log warning).
2. Run `paper_writer_helpers.py resolve-figures` to get the
   paper_order_n → caption + filename mapping.
3. Walk each section markdown file (`02_results.md`, primarily; also
   `01_methods.md`, `03_discussion.md` if they cite figures —
   currently rare but the phase shouldn't assume).
4. For each `(Fig. N)` regex match (pattern:
   `\(Fig\. (\d+)[A-Z]?[,)\s]`), find the *first* occurrence in the
   section file. Identify the sentence containing that occurrence
   (sentence boundary regex:
   `(?<=[.!?])\s+`). Inject after the sentence:

   ```
   \n\n![Figure N: <caption>](figures/<filename>)\n\n
   ```

   where N is the matched integer, `<caption>` from resolve-figures,
   `<filename>` from the manifest.

5. Multi-figure callouts (`(Fig. 3 and Fig. 5)` or `(Fig. 3, panel A)`):
   handle via repeated regex match; inject all unique figure tags
   after the same sentence in callout order.
6. Subsequent `(Fig. N)` references to a figure already embedded
   stay textual — only the *first* occurrence per figure embeds.
7. Idempotent: if the section file already contains
   `![Figure N: ...](figures/<filename>)` markdown image tag for
   this figure, skip embedding (re-running the phase is safe).

**Stale-file cleanup** (also part of 2.2 because it lives in
`paper_writer.sh`):

In `phase_results`, drafting-mode-only (skip in REPAIR_MODE), add a
pre-LLM step:

```bash
# Clean stale paper-order figures from prior rewrite-loop iterations.
# results.v1 will re-copy fresh figures from inventory.
rm -f "$FIGURES_OUT_DIR"/fig*.png
```

3 lines. Documented in DECISIONS.md as a new D-NN entry: orchestrator
owns `<draft_dir>/figures/` for paper-order names; user-curated
figures should live in a separate path. Revisit if a 2nd user shows
up with hand-curation needs.

**AC:**

- Run `phase_embed_figures` against existing `draft_1` after results.v1
  has emitted the manifest. Each `(Fig. N)` first-occurrence in
  `02_results.md` gets a markdown image tag injected after its
  sentence.
- Manuscript.md (post-`phase_assemble`) contains
  `![Figure 1: ...](figures/fig01_*.png)` tags inline.
- assemble_docx.py renders these as Picture + Caption paragraphs.
- Re-running the phase is idempotent (no double-embedding).
- Stale-file cleanup removes the existing `fig01_dark_gene_census_fitness.png`
  + `fig05_biogeographic_lab_field.png` + similar collisions on a
  fresh drafting run.

**Estimated effort:** 2–3 days.

---

## Wrinkle A — figures_manifest.tsv canonicalization

**Locked 2026-04-27.**

**Storage format:** TSV at `<DRAFT_DIR>/figures_manifest.tsv`. Three
columns, tab-separated, with header row:

```
paper_order_n	filename	inventory_lookup_name
1	fig01_dark_gene_census.png	fig01_annotation_breakdown.png
2	fig02_evidence_coverage.png	fig03_dark_gene_coverage.png
...
```

**Why TSV not JSON:** memory entry
`feedback_llm_json_unfixable_in_parser.md` (caught in beril-atlas L2
cache work, cost a v0.1.9 release with full L2 cache invalidation):
unescaped `"` inside JSON string values is unrecoverable in the
parser; only fix is prompt-level anti-pattern + version bump. Captions
from REPORT.md can contain quotes. By keeping captions OUT of the
manifest entirely, we sidestep the entire trap. The manifest holds
filesystem-safe identifiers only.

**Banned-tab discipline:** none of the three columns may contain a
tab. `paper_order_n` is integer. `filename` and `inventory_lookup_name`
are both filesystem names from extract_figures.py output. The check
runs in `tools/check_figures_manifest.py`; a violation is ERROR-style
WARN.

**Caption resolution lives orchestrator-side:**
`paper_writer_helpers.py resolve-figures` joins manifest with
`figures_inventory.md` and emits paper_order_n → caption + filename.
Captions are *never* re-emitted by the LLM; they come from the
project-authored inventory. This is consistent with the existing
caption-authority order doctrine in `results.v1.md` lines 238–241
(REPORT-derived first, notebook-context second, filename third).

---

## Wrinkle B — embedded image-tag form

**Locked 2026-04-27.**

**Markdown form:** `![Figure N: <caption>](figures/<filename>)`

Both `Figure N:` prefix and the caption text live in the alt-text.
The figure number N comes from the preceding `(Fig. N)` callout in
the prose (results.v1 owns the numbering; 2.2 reads it). The caption
comes from `figures_inventory.md` via `resolve-figures`.

**Why N from the (Fig. N) callout, not from manifest counting:**
results.v1's prose already declares paper-order N. If 2.2 ever has
to renumber (figures dropped, added, reordered), renumbering happens
in results.v1's output and propagates naturally — 2.2 just reads
what's in the prose. Counter-pattern (have 2.2 count and renumber)
doubles the renumbering authority and creates a drift surface.

**Embed once per figure:** subsequent `(Fig. N)` references to an
already-embedded figure stay textual. Two-line state machine in 2.2.

**docx rendering** (Item 2.3 path): `![alt](path)` block-form →
`add_picture(path, width=Inches(6))` in its own paragraph + Caption-
styled paragraph immediately following with the alt-text as the
caption text. python-docx handles aspect-ratio preservation when
only one dimension is supplied.

---

## Tier 2 completion gate

- 2.4 done (2026-04-27)
- 2.3 done: `manuscript.docx` opens cleanly with section headings,
  paragraphs, image tags rendered as Picture + Caption, references
  list at end, on the existing `draft_1`.
- 2.1 done: `results.v1` HALT discipline lands; manifest contract
  emitted; post-checker green; resolve-figures helper smoke-tested.
- 2.2 done: `phase_embed_figures` injects image tags idempotently;
  stale-file cleanup wired and documented in DECISIONS.md.
- Live retest below passes.

---

## Live retest gate ($5 LLM cost)

Fresh drafting run on `functional_dark_matter` (or a smaller test
project if an EXPLORATORY-tier alternative becomes available).
Validates:

1. `phase_results` (drafting mode) — pre-clean removes pre-existing
   `fig*.png` from `<draft_dir>/figures/`.
2. results.v1 — emits `figures_manifest.tsv` with K rows; closing
   message reports K.
3. `tools/check_figures_manifest.py` — green (no advisory WARNs, or
   only documented borderline FPs).
4. `phase_embed_figures` — injects K markdown image tags into section
   files at first-occurrence-of-(Fig. N) sentence boundaries.
5. `phase_assemble` — concatenates section files; manuscript.md
   contains `![Figure N: ...]` tags inline.
6. `beril-paper-writer assemble draft_N --format docx` — produces
   manuscript.docx with K Pictures + K Caption paragraphs.

**Cost cap:** `--max-cost-usd 10` (defensive — full draft is
typically $4–6).

**Decision point:** ship as v0.3.0 if green; punch-list more if
patches surface.

---

## v0.3.0 ship items

Same pattern as v0.2 ship + v0.2.1 same-day patch (memory:
`feedback_punch_list_release_pattern.md`).

- `pyproject.toml` + `__init__.py` 0.2.1 → 0.3.0
- `RELEASE_NOTES_v0_3.md` authored alongside the ship cycle. Documents
  what shipped (Tier 2 in full), what's deferred to v0.4 (Tier 7
  diagrams, Tier 4 interactive, M10 architectural redesign), real
  numbers from the live retest.
- `.commit-message-v0_3_0.txt` with full audit trail per the v0.2
  pattern — Adam runs `git commit -F .commit-message-v0_3_0.txt`
  from his Mac shell after review.
- `smoke-test/v0_3_0_ship_runbook.sh` with manual git commands
  printed for review (per project rule: no git writes in sandbox
  bash on host-mounted repos; memory
  `feedback_no_git_writes_in_sandbox.md`).
- Tag `v0.3.0` after Adam's review.

---

## Post-v0.3 deferred (design notes only)

These are out of scope for v0.3 and tracked in v0.2 punch list's
"Post-v0.2 future tiers" section + memory entries. Re-cited here so
this document is self-contained for next-cycle handoff.

- **Tier 7 — conceptual diagrams via mermaid-in-markdown.** Hard
  dependency on Tier 2.3 (the docx assembler). Adam confirmed
  2026-04-27 — option (a) mermaid-in-markdown over matplotlib.
  Renderer choice (Kroki vs mermaid-cli) is a v0.4 decision. Will
  add the orchestrator's mermaid → PNG render pipeline + a fifth
  post-checker `tools/check_diagrams.py`. Auto-generated illustrative
  images permanently declined.

- **v0.3.1 cosmetic follow-ups** (30-min audit-pass fixes; not blockers):
  - **"Copied 0 figure(s)" log line in `phase_results` is misleading**
    in v0.3. The grep+cp fallback at paper_writer.sh:912-919 was
    meaningful in v0.1/v0.2 when results.v1 prose contained filenames;
    in v0.3 prose contains `(Fig. N)` callouts and the actual figure
    copy happens inside results.v1's Bash tool calls. Fix: remove
    the fallback OR reframe the log message to "fallback copy: 0
    (expected — results.v1 owns figure copy)".
  - **`FIGURES_OUT_DIR` not explicitly passed to results.v1
    user_prompt** at paper_writer.sh:885-902. The LLM infers the path
    from `DRAFT_DIR` (works; verified by 10/10 Pictures in live
    retest) but the contract is brittle. Fix: add
    `FIGURES_OUT_DIR = $draft_dir/figures` line to the user_prompt
    template.

- **Tier 8 — figure caption richness.** v0.3 ships with terse
  REPORT-derived captions (one noun phrase per figure, sourced from
  `figures_inventory.md`'s top bullet). For ICMJE / Nature / Science
  caption convention (50–200 words: what's shown, what to notice,
  panels A/B/C, n values, error bars, statistical context), v0.3 is
  insufficient. Adam flagged this 2026-04-27 immediately after the
  v0.3 Tier 2 smoke; deferred to v0.4 because Tier 2's thesis is
  "close the embedding loop" and caption-richness is orthogonal.

  **Source ladder** (cheapest → richest, validated 2026-04-27 against
  the `functional_dark_matter` artifacts):

  - **Source 1 — REPORT.md surrounding prose.** Find each figure's
    name in REPORT, grab ±2 sentences of context. Deterministic; no
    LLM. Quality ceiling limited by REPORT's own caption style (often
    terse).
  - **Source 2 — notebook markdown cell preceding `savefig`.** The
    cell typically has descriptive prose. `extract_figures.py`
    currently captures only the section header; would need ~50 lines
    of additional logic to walk back from the savefig cell through
    markdown cells until a section break, capturing the descriptive
    text. Best quality-per-effort; fully deterministic. **Recommended
    starting point for v0.4.**
  - **Source 3 — matplotlib AST parsing of the savefig cell.** Walk
    `ast.parse(cell.source)` for `Call` nodes named `title`, `xlabel`,
    `ylabel`, `set_title`, `legend`, `colorbar`. Reconstructs axes
    labels and panel structure. ~80 lines; brittle on computed-string
    plot calls but works on simple cases.
  - **Source 4 — LLM synthesis.** Feed (inventory entry + REPORT
    context + notebook markdown + Results-section prose around the
    (Fig. N) callout) to a small caption-generation prompt; returns
    100-word legend in scientific style. ~$0.10–0.30 per figure × 8
    = ~$2 per draft. New prompt with anti-fabrication discipline
    (every numerical claim must trace; no inventing panel labels).
  - **Source 5 — vision on the PNG.** Highest quality, highest
    fabrication risk, new vision-API dep. Likely overkill.

  **Recommended v0.4 sequence:** Sources 2 + 3 first (deterministic,
  smoke-testable against `functional_dark_matter` for $0). If
  insufficient richness, add Source 4 (LLM) with prompt-side
  anti-fabrication discipline — every claim in the synthesized
  caption must trace to one of the source documents, mirroring the
  number-trace discipline in `results.v1.md`'s prompt.

  **Known limitation Sources 2+3 cannot fix:** if the analyst didn't
  author descriptive markdown cells in the notebook AND the matplotlib
  call uses computed strings (`title(f"Distribution of {var}")`),
  the deterministic paths return little signal. Source 4 (LLM) is the
  fallback; Source 5 (vision) is the last resort.

  **Plumbing changes required:**
  - `extract_figures.py`: enrich each inventory entry with new
    `description` field beyond the current bullet list.
  - `figures_inventory.md` schema: bump to v2 with structured
    description block (or stay markdown but add a `**Description:**`
    sub-section per entry).
  - `paper_writer_helpers.py resolve-figures`: emit new `description`
    column alongside `caption` (or replace `caption` with the richer
    text).
  - `phase_embed_figures` / `_embed_figures_in_text`: image-tag
    alt-text becomes `Figure N: <title>. <description sentence(s)>.`
    or split across the alt-text + a separate Caption-styled
    paragraph in the docx (the latter requires
    `tools/assemble_docx.py` to know about a "rich caption" form).
  - `tests/unit/test_embed_figures.py`: add fixtures with rich
    descriptions to verify the assembler renders them as multi-line
    Caption paragraphs.

  **Acceptance criteria for v0.4 Tier 8:** the `functional_dark_matter`
  draft's docx has 8 figure captions averaging ≥30 words each, with
  every claim traceable to one of the source documents (REPORT,
  notebooks, manuscript prose). Adam reviews each caption visually;
  no fabrication.
- **Tier 4 — interactive checkpoints.** Card elicitation pre-drafting,
  citation-pool exhaustion user pause, throughline re-evaluation
  on artifact drift. Deferred until augmentation stream has a 2nd
  user. Per Adam 2026-04-27.
- **Items 4.3 + 5.3 (state-schema bump + migration tool).** Bundled
  when 4.3 lands; 5.3 is the migration tool 4.3 triggers. 4.3
  introduces `state.py:diff_artifacts`'s LLM-driven re-evaluation
  prompt.
- **Follow-up #21** — tighten `fallback_reviewer.v1.md` to mandate
  one canonical finding-header format. Parser is tolerant (v0.2.0
  fix); prompt is loose. Second half of the discipline lesson.
- **M10 architectural redesign** — surfaced by v0.2.1's live test.
  M10 (orphan citation) repair via discussion.v1 in REPAIR_MODE
  can't actually fix M10 because remediation usually requires
  editing references.md (outside section-prompt scope). Route M10
  to a citation-pool-aware repair path.

---

*Punch list authored 2026-04-27 immediately after v0.2.1 ship +
2.4 wiring smoke. Tier 2 sequence (a) confirmed by Adam. Wrinkles A
+ B canonicalized in this doc. Future conversations can pivot off
this doc for fast start; do not re-derive scope.*
