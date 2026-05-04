---
name: beril-paper-writer
description: |
  Draft ICMJE-conformant scientific manuscripts from BERDL analysis
  projects. Pipeline: triage + 2-3 throughline candidates (user picks) →
  citation pool with DOI/PMID verification → IMRAD section drafting →
  validator pass → adversarial review. Resumable across sessions via
  state.json + .handoff.json. Use when a BERDL project (REPORT.md +
  notebooks + RESEARCH_PLAN) has reached the "ready to write" stage and
  you want a structured first draft to iterate from.
allowed-tools: Bash, Read, Write, AskUserQuestion
user-invocable: true
---

# BERIL Paper Writer

Drafts a scientific manuscript from a BERDL analysis project. Reads
the project's `REPORT.md`, `RESEARCH_PLAN.md`, notebooks, figures, and
(optionally) curated `references.md`; produces a complete IMRAD draft
plus an adversarial review.

The skill ships as a pip-installable Python package
(`beril-paper-writer-skill`) plus a Claude Code skill installed at
`<BERIL>/.claude/skills/beril-paper-writer/`. The Python layer handles
install + configuration + user-input parsing (the throughline-pick
flow). The drafting itself runs through a shell orchestrator
(`tools/paper_writer.sh`) that invokes per-section prompts as `claude
-p` subagents. State persists in `papers/draft_N/state.json`; pause
points emit `papers/draft_N/.handoff.json` for the slash-command
parser.

**Status: v0.7.0 — pipeline reliability.** Full IMRAD pipeline with
review-rewrite loop (v0.2+), figure + table embedding (v0.3/v0.6),
caption-richness via Source 4 LLM (v0.4), markdown→docx assembly,
and v0.7.0 ensemble review (3× fallback reviewer + agreement scoring),
parallel rewrite candidates, and best-of-3 caption generation.

## Slash commands

### `/beril-paper-writer` — start a new draft

```
/beril-paper-writer [<project_id>] [--mode paper|report]
                    [--depth quick|standard|deep]
                    [--model <model_id>]
                    [--no-adversarial] [--no-stream]
```

**Arguments:**

- `<project_id>` — project directory under `projects/`. Optional if
  cwd is inside `projects/<id>/`.
- `--mode paper|report` — `paper` is the standard journal-submission
  format; `report` is a less-formal structure for EXPLORATORY-tier
  projects. Default: tier-driven (STRONG/THIN → `paper`; EXPLORATORY
  → `report` per SPEC §3.2).
- `--depth quick|standard|deep` — drafting thoroughness. Default
  `standard` (~15-25 min total wall clock for the full pipeline).
  `quick` (~5-10 min) cuts citation-pool budget and trims weakness
  inventories. `deep` (~30-50 min) expands literature scan and
  multi-source verification.
- `--model <model_id>` — override default model. Default Sonnet
  (~3× cheaper than Opus on this pipeline).
- `--no-adversarial` — skip the `beril-adversarial` canonical review
  and use the inline `fallback_reviewer.v1` prompt. Useful when
  `beril-adversarial` isn't installed.
- `--no-stream` — disable the `stream_progress.py` wrapper. Loses
  Write-tool verification + cost summary. Useful only for debugging.

### `/beril-paper-writer-continue` — resume a paused draft

```
/beril-paper-writer-continue <draft_dir>
                             [--pick TLN] [--revision "text"]
                             [--no-adversarial] [--no-stream]
                             [--model <model_id>]
```

**Arguments:**

- `<draft_dir>` — path to the paused draft directory (e.g.,
  `projects/<id>/papers/draft_1/`).
- `--pick TLN` — required when `state.phase=throughline_pick`. The
  candidate id (TL1, TL2, ...) the user chose from
  `throughline_candidates.md`.
- `--revision "text"` — optional revision note for the chosen
  throughline. If non-empty, invokes `revise_throughline.v1` to
  refine the candidate; cost ~$0.30 on Sonnet. If absent or empty,
  the chosen candidate is copied verbatim into `00_throughline.md`.

The continue command auto-detects the resume point from
`state.json`'s `phase` field. If the previous run halted mid-pipeline
(`phase=halted` in `.handoff.json`), the same continue command
idempotently retries the failed phase.

### `/beril-paper-writer-configure` — verify environment

(Not yet a separate slash command; verification runs as part of
`/beril-paper-writer` Step 1.)

## Workflow

### Step 1 — Resolve project + verify install

The slash command verifies `beril-paper-writer` is on PATH, then
resolves the project (auto-detect from cwd if inside `projects/<id>/`,
else use the explicit argument). Confirms the project has the
required inputs: `REPORT.md`, `RESEARCH_PLAN.md`, at least one
`notebooks/*.ipynb`.

### Step 2 — Initialize and run plan.v1

The orchestrator creates `papers/draft_N/` (auto-incrementing N), runs
`extract_methods.py` + `extract_figures.py` for grounding, then
invokes `plan.v1` to triage the project (STRONG / THIN / EXPLORATORY)
and produce 2-3 candidate throughlines (or 4 with the THIN narrowed-
claim variant per SPEC §3.3). Each candidate has an evidence map with
strength glyphs (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal),
a weakness inventory, and a "would NOT include if chosen" list.

Wall clock: ~2-3 min on Sonnet for STRONG-tier projects.

### Step 3 — Throughline pick (load-bearing user gate)

The orchestrator pauses with `phase=throughline_pick` in
`.handoff.json`. The slash command reads the handoff, presents the
candidates via `AskUserQuestion`, and surfaces any advisory warnings
from `tools/check_throughline_glyphs.py` (advisory cross-walk between
the evidence-map glyphs and weakness-inventory caveats; flags
candidates whose glyphs look inflated).

The user picks one (and optionally provides a one-line revision
note). The slash command runs `beril-paper-writer continue <draft_dir>
--pick TLN [--revision "text"]`.

### Step 4 — Drafting pipeline

After the pick, the orchestrator runs:
`citation_pool.v1` (literature scan + 9-field verification) →
`citation_pool.py format` (renders `references.md`,
`bibliography.bib`, `citation_map.md`) →
`methods.v1` (notebook-grounded methods prose) →
`results.v1` (plus figure copy) →
`discussion.v1` →
`intro.v1` →
`abstract.v1` →
orchestrator-side data-availability template fill →
concatenate to `manuscript.md` →
`validate_manuscript.py` (runs M1-M10 mechanized checks; reports only
in v0.1, no auto-fix).

Wall clock: ~10-25 min on Sonnet depending on `--depth` and project
tier.

### Step 5 — Ensemble review + rewrite loop

The orchestrator runs 3 independent fallback reviews in parallel
(v0.7.0 ensemble), deduplicates findings by section + textual overlap,
and scores by agreement (3/3, 2/3 → routed to rewrite loop; 1/3 →
advisory only). The bounded rewrite loop (up to 2 passes per SPEC §8.3)
dispatches `rewrite.v1` per affected section with parallel candidates
(v0.7.0 R3). A canonical `beril-adversarial review --type paper` audit
is planned as a post-loop quality gate (phase_adversarial_audit).

### Step 6 — Final pause

The orchestrator pauses with `phase=review` in `.handoff.json`. The
slash command reads it, presents the review path + manuscript path +
cumulative cost from `audit/run_metadata.json`, and tells the user
the draft is complete from the orchestrator's perspective.

### Failure mode: phase=halted

Any mid-pipeline failure writes a `phase=halted` handoff with
`prompt_to_user` describing the halt reason and a recovery hint
(typically `Re-run: beril-paper-writer continue <draft_dir>`). The
slash-command parser surfaces the halt to the user; the user chooses
whether to retry. Phases are idempotent — retrying skips already-
completed steps.

## Output artifacts

```
projects/<project_id>/papers/draft_N/
├── state.json                   ← persistent state (phase, throughline, costs)
├── .handoff.json                ← current pause/halt state for slash-command parser
├── manuscript.md                ← assembled draft (concatenated sections)
├── 00_throughline.md            ← chosen throughline + evidence map
├── 01_methods.md
├── 02_results.md
├── 03_discussion.md
├── 04_introduction.md
├── 05_abstract.md
├── 07_data_availability.md      ← orchestrator-filled template (with [TBD] markers in v0.1)
├── references.md                ← human-readable, numbered
├── bibliography.bib             ← machine-readable (BibTeX)
├── citation_map.md              ← claim → reference index
├── pool.json                    ← citation pool with full provenance
├── methods_provenance.md        ← Methods statements ↔ notebook+cell from extract_methods.py
├── figures_inventory.md         ← from extract_figures.py
├── reframing_log.md             ← deviations from REPORT.md (auditable)
├── analysis_requests.md         ← gap-fill requests, statuses (mostly empty in v0.1)
├── throughline_candidates.md    ← rejected alternatives, kept for audit
├── figures/
│   └── (figures referenced by 02_results.md, copied from project's figures/)
├── reviews/
│   └── draft_N_review_M.md      ← single-pass adversarial review
└── audit/
    ├── plan.metadata.json
    ├── citation_pool.metadata.json
    ├── methods.metadata.json
    ├── ... (one per LLM call)
    ├── run_metadata.json        ← cumulative cost summary
    └── validation.json          ← M1-M10 validator results
```

`/submit` does NOT clear any of these. Each new `/beril-paper-writer`
invocation creates a new `papers/draft_{N+1}/` (auto-incrementing).

## Resume contract

The slash-command parser's read-after-every-bash-call contract:

| bash exit | `.handoff.json` phase | meaning |
|---|---|---|
| 0 | `throughline_pick` | paused; drive `AskUserQuestion` pick UX |
| 0 | `review` | paused at final; present manuscript + review |
| 0 | `assembled` | already complete (resume on a finished draft) |
| non-zero | `halted` | failure mid-pipeline; surface halt reason + retry option |
| non-zero | (no handoff) | pre-init failure (no draft_dir context yet); surface stderr |

`state.json.phase` is the resume anchor. On halt, state's phase stays
at the in-progress value; the halted handoff's phase=halted is the
parser-facing signal. Rerunning `beril-paper-writer continue
<draft_dir>` dispatches on state.phase and idempotently retries the
failed step.

## When to use this skill vs. alternatives

| Scenario | Use |
|---|---|
| BERDL project ready to write up | `/beril-paper-writer` |
| Existing draft to review | `beril-adversarial review --type paper` (sibling skill) |
| Plan-stage manuscript outline | (not yet — `/beril-paper-writer --mode report` is closest, but it's tier-driven; v0.2 may add a planning-stage prompt) |
| Slide-deck companion | `/beril-presentation-maker` (sibling skill, mid-flight) |

## Reviewer / drafter memory

The skill maintains cross-draft meta-memory at
`.claude/skills/beril-paper-writer/state/learned-patterns.md` (this
install only — never shipped). Used for "patterns the writer has
flagged before that generalize" — typo-corrections in seed
bibliographies, recurring strength-glyph cross-walk failure modes,
etc.

The reframing-log discipline (per SPEC §5.6) keeps per-draft
plan-vs-execution discrepancies in `papers/draft_N/reframing_log.md`,
co-located with the draft.

## Notes

- The system prompts (`prompts/*.v1.md`) are the locus of drafting
  intelligence. They iterate via `.v{N}.md` versioning.
- The reference templates (`references/ai_disclosure_template.md`,
  `references/data_availability_template.md`) are orchestrator-filled
  via `tools/paper_writer_helpers.py fill-template`. Single-brace
  `{key}` placeholders.
- This skill never modifies project source files (no edits to
  `REPORT.md`, `RESEARCH_PLAN.md`, notebooks). All output is scoped
  to `papers/draft_N/`.
- Adversarial review coupling is loose. The writer shells out to
  `beril-adversarial review --type paper` if on PATH; missing-binary
  triggers the fallback reviewer with a stderr warning.
- For provider/model configuration: the `claude` CLI carries its own
  config. This skill does not edit `.env` or hold API keys.

## Pitfall detection

When you encounter errors, unexpected results, or surprising
drafting outcomes during invocation of this skill, follow the
pitfall-capture protocol. Read
`.claude/skills/pitfall-capture/SKILL.md` and follow its instructions
to determine whether the issue belongs in `docs/pitfalls.md`.
Drafting-meta-patterns (recurring failures the writer surfaces) belong
in `state/learned-patterns.md` (the writer manages that file
directly).
