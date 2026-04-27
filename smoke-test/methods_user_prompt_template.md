Run the Methods drafter against the project at $PROJECT_ROOT to produce
$METHODS_PATH. The system prompt above describes your discipline; this
user-prompt provides the concrete inputs.

## Inputs

- `PROJECT_ROOT` = `$PROJECT_ROOT`
- `DRAFT_DIR` = `$DRAFT_DIR`
- `METHODS_PATH` = `$METHODS_PATH`  (your output goes here)
- `METHODS_PROVENANCE_PATH` = `$METHODS_PROVENANCE_PATH`  (the
  factual anchor — produced by `extract_methods.py` before this
  prompt runs; do NOT re-derive facts that are in this file)
- `RESEARCH_PLAN_PATH` = `$RESEARCH_PLAN_PATH`
- `REPORT_PATH` = `$REPORT_PATH`
- `THROUGHLINE_PATH` = `$THROUGHLINE_PATH`  (chosen single candidate)
- `REFRAMING_LOG_PATH` = `$REFRAMING_LOG_PATH`
- `MODE` = `$MODE`
- `TIER` = `$TIER`
- `AI_DISCLOSURE_TEMPLATE` (verbatim, insert under "AI-Assisted
  Analysis" — do NOT rewrite or paraphrase):

$AI_DISCLOSURE_BODY

(end of AI_DISCLOSURE_TEMPLATE)

No `REPAIR_MODE` (this is a fresh drafting run).

## What you should do

1. Read `METHODS_PROVENANCE_PATH` (the factual anchor),
   `RESEARCH_PLAN_PATH` (design intent), `THROUGHLINE_PATH` (which
   methods are load-bearing), then `REPORT_PATH` for context only.
2. Build the Methods section subsection-by-subsection per the
   ICMJE 7-subsection structure, grounding every claim per your
   discipline pass.
3. Place `[METHOD UNCLEAR: ...]` placeholders for implied-but-not-
   explicit steps, `[METHOD SOURCE NOT EXTRACTED: <path>]` for any
   non-`.ipynb` sources flagged in the provenance file (the
   project may have none — check), and `[VERSION UNCLEAR: <pkg>]`
   for packages imported but not in "Software and Versions".
4. Append plan-vs-execution discrepancy entries to
   `REFRAMING_LOG_PATH` per SPEC §5.6 if any divergences are
   found between the plan's prespecified methods and the
   notebook's executed methods.
5. Run the self-review pass; fix issues before writing.
6. Write `METHODS_PATH` via the `Write` tool.
7. Emit the closing message in the required exact format.

## Smoke-test note

This is a smoke test, not a production run. The orchestrator
(`paper_writer.sh`) does not exist yet — these inputs were
hand-constructed. The throughline at `THROUGHLINE_PATH` is the
same one used in the citation_pool.v1 smoke test (see
`/tmp/citation_pool_smoke/draft_1/00_throughline.md`).

There is no manuscript-level `validate_manuscript.py` invocation
here; per SPEC, validators run at the orchestrator level after all
sections are drafted, not per-section. Self-review is the prompt's
own discipline.
