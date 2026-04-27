Run the Plan-phase agent against the project at $PROJECT_ROOT to
produce $THROUGHLINE_CANDIDATES_PATH. The system prompt above
describes your discipline; this user-prompt provides concrete inputs.

## Inputs

- `PROJECT_ROOT` = `$PROJECT_ROOT`
- `DRAFT_DIR` = `$DRAFT_DIR`
- `THROUGHLINE_CANDIDATES_PATH` = `$THROUGHLINE_CANDIDATES_PATH`  (your output goes here)
- `REPORT_PATH` = `$PROJECT_ROOT/REPORT.md`
- `RESEARCH_PLAN_PATH` = `$PROJECT_ROOT/RESEARCH_PLAN.md`
- `NOTEBOOKS_DIR` = `$PROJECT_ROOT/notebooks`
- `ANALYSIS_REQUESTS_PATH` = `$DRAFT_DIR/analysis_requests.md`  (will be empty unless REPORT triggers gap-fill)

No `FIGURES_INVENTORY_PATH` (optional input; not provided for this smoke test — `extract_figures.py` not run).
No `MODE_OVERRIDE` (let triage decide; default mode follows tier).
No `RE_EVALUATION_MODE` (this is a fresh first-pass run).

## What you should do

1. Read `REPORT_PATH` (canonical findings — read fully), then
   `RESEARCH_PLAN_PATH` (design intent), then individual notebooks
   from `NOTEBOOKS_DIR` for sub-claim grounding as needed.
2. Triage the project as STRONG / THIN / EXPLORATORY per the
   rubric in your system prompt. Name the specific evidence-
   strength criteria the project meets or misses; rubric-driven,
   not vibes.
3. Extract 2–3 candidate throughlines per tier-aware extraction
   rules. For THIN tier, also produce the +1 narrowed-claim
   candidate per SPEC §3.3.
4. Build per-candidate evidence maps with strength glyphs
   (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal).
   Operationalize each glyph against the source — no inflation.
5. Build per-candidate weakness inventories (project-specific, not
   generic) and "what this paper would NOT include if chosen" lists.
6. Run the self-review pass; fix any issues before writing.
7. Write `THROUGHLINE_CANDIDATES_PATH` via the `Write` tool.
8. Pause and exit with the closing-message template (drafting
   mode).

## Smoke-test note

This is a smoke test, not a production run. The orchestrator
(`paper_writer.sh`) does not exist yet — these inputs were
hand-constructed. After you write the candidates and pause, the
smoke test ends; the user reviews the candidates manually.

The project at `$PROJECT_ROOT` is `functional_dark_matter`, the
same project used in the citation_pool.v1 and methods.v1 smoke
tests. Expected tier (per the manuscript's substantial REPORT.md
and ~14 notebooks): STRONG. The smoke test will validate whether
the prompt's rubric-driven triage produces that verdict and
extracts coherent candidates around the project's load-bearing
findings (dark gene catalog, GapMind gap-filling, cross-organism
concordance, biogeography, prioritization).
