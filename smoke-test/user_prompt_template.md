Run the citation pool builder against the project at $PROJECT_ROOT
to produce $POOL_JSON_PATH. The system prompt above describes your
discipline; this user-prompt provides the concrete inputs.

## Inputs

- `PROJECT_ROOT` = `$PROJECT_ROOT`
- `DRAFT_DIR` = `$DRAFT_DIR`
- `POOL_JSON_PATH` = `$POOL_JSON_PATH`
- `MODE` = `$MODE`
- `TIER` = `$TIER`
- `THROUGHLINE_PATH` = `$THROUGHLINE_PATH`
- `EXISTING_REFERENCES_MD` = `$EXISTING_REFERENCES_MD`  (the
  project's pre-existing `references.md` — use as a seed of
  unverified candidates per your verification pass)
- `MAX_BUDGET` = `$MAX_BUDGET`  (smaller than the default 80 — this
  is a smoke-test run with bounded cost)
- `DEPTH` = `$DEPTH`
- `POOL_VALIDATOR_CMD` = `$VALIDATOR_CMD`

No `EXISTING_POOL_PATH` (this is a fresh build).
No `TOPIC_SCOPE` (cover everything per tier sizing).

## What you should do

1. Read THROUGHLINE_PATH (the chosen claim + evidence map you must
   anchor against), REPORT.md (the canonical findings),
   RESEARCH_PLAN.md (design intent — what the project's authors
   said they would do), then EXISTING_REFERENCES_MD (the seed
   candidates).
2. Build the verified citation pool per your system prompt's
   discipline. Probe-once for PubMed MCP at start; fall back to
   WebSearch if absent. Verify every entry per the verification
   pass; do not include unverified entries.
3. Apply the throughline filter as a final pass before self-review.
4. Run the self-review checklist; fix any issues before writing.
5. Write `POOL_JSON_PATH` via the `Write` tool.
6. Run `POOL_VALIDATOR_CMD` via Bash.
7. Emit the closing message in the required exact format.

## Smoke-test note

This is a smoke test, not a production run. The orchestrator
(`paper_writer.sh`) does not exist yet — the inputs above were
hand-constructed for this run. The throughline at `THROUGHLINE_PATH`
was written by hand from REPORT.md, not produced by `plan.v1` —
treat it as if `plan.v1` had produced it and a user had picked it.

The cost cap is reduced (`MAX_BUDGET=30` vs. the default 80) to
keep this smoke test bounded. The pool target for STRONG tier at
this budget is ~20–28 entries; do not pad to fill.
