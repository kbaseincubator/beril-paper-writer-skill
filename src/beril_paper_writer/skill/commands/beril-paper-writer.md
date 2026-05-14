---
description: Draft an ICMJE-conformant scientific manuscript from a BERDL analysis project. Multi-stage pipeline with a load-bearing throughline-pick gate the user controls.
argument-hint: "[<project_id>] [--mode paper|report] [--depth quick|standard|deep] [--model <model_id>] [--no-adversarial] [--no-stream] [--max-cost-usd N] [--recaption]"
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# /beril-paper-writer

Draft a manuscript for the analysis project at `projects/<project_id>/`.
The pipeline runs `plan.v1` to produce throughline candidates, pauses
for the user to pick one (the load-bearing user gate), then runs the
v0.7.x drafting pipeline: `citation_pool` builds a verified reference
pool, `holistic_draft` writes the entire manuscript in one Opus pass
(Abstract, Introduction, Methods, Results, Discussion), then `review`
→ `optimize` → `supplementary_pool` → `compliance_gate` → `assemble`
runs the adversarial reviewer, applies subtraction-only fixes,
resolves any `[NEEDS CITATION]` markers via verified WebSearch, runs
the compliance gate, and renders `manuscript.md` → `manuscript.docx`.
After the throughline pick it runs straight through to
`phase=assembled` and exits; the user reviews the assembled artifacts
directly (no second handoff in v0.7.x).

State persists in `papers/draft_N/state.json`; the session can be
closed and resumed via `/beril-paper-writer-continue` between
sessions.

## Step 1 — Verify the package is installed

Run in a Bash block:

    beril-paper-writer --version

If the command is not found, tell the user:

> The `beril-paper-writer` package isn't on your PATH. From your BERIL
> root, run the four steps below in order (install package → verify
> CLI loads → configure cross-skill bindings → deploy skill files into
> BERIL):
>
>     cd ~/BERIL-research-observatory
>     pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git \
>       && beril-paper-writer --version \
>       && beril-paper-writer configure \
>       && beril-paper-writer install-skill .
>
> If you have an SSH key registered with GitHub you can also use the
> SSH URL — note the explicit `git@`, which is required:
>
>     pipx install --force git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git

Stop here if the command is missing.

## Step 2 — Resolve the project

If the user passed `<project_id>` explicitly, use it.

Otherwise, check if cwd is inside `projects/<id>/` and auto-detect.
If neither, ask the user via AskUserQuestion which project to draft
for.

Validate that `projects/<project_id>/` exists. If not, stop with an
error.

Confirm the project has the inputs paper-writer requires:

- `REPORT.md` (canonical findings; load-bearing for plan.v1)
- `RESEARCH_PLAN.md` (design intent)
- `notebooks/` directory with at least one `*.ipynb` (load-bearing for
  methods extraction)

If any are missing, stop and tell the user. Don't proceed; the
pipeline will halt at the extraction step anyway with worse
diagnostics.

## Step 3 — Start the draft

**Run the bash command in the FOREGROUND.** Plan.v1 typically takes
2-3 minutes on Sonnet for STRONG-tier projects; longer for THIN /
EXPLORATORY. If the bash tool warns about a long-running command,
wait for it. Backgrounding breaks the user's ability to follow the
pipeline through to its first pause point.

From BERIL_ROOT:

    beril-paper-writer draft <project_id> \
        [--mode <mode>] \
        [--depth <depth>] \
        [--model <model_id>] \
        [--no-adversarial] \
        [--no-stream] \
        [--max-cost-usd <N>] \
        [--recaption]

- Omit `--mode` to let triage decide (STRONG/THIN → `paper`;
  EXPLORATORY → `report` per SPEC §3.2).
- Omit `--depth` if `standard` (default; ~15-25 min total wall clock
  for the full pipeline). Pass `quick` (~5-10 min) for fast iteration.
  Pass `deep` (~30-50 min) for thorough pre-submission drafting.
- Omit `--model` to use Sonnet (default; ~3× cheaper than Opus on
  this pipeline). Note the holistic drafter runs Opus regardless —
  `--model` controls the non-drafting phases.
- `--no-adversarial` skips the canonical `beril-adversarial` reviewer
  and falls back to the inline `fallback_reviewer.v1` prompt. The
  canonical reviewer is the default; the inline fallback is legacy and
  only used when `beril-adversarial` isn't installed or this flag is
  passed.
- `--max-cost-usd N` halts with a handoff if cumulative LLM spend
  exceeds N USD (checked before each LLM call). Default: no cap.
- `--recaption` forces re-synthesis of LLM figure captions; by default
  figures with an existing `audit/figure_caption_<N>.md` are skipped.

The pipeline will run init → extract → triage → plan.v1, then EXIT at
the throughline-pick gate. The bash command's exit code is `0` for the
expected pause; anything non-zero is an error.

## Step 4 — Read the pause state

After the bash command exits, the plan phase has written
`<draft_dir>/.handoff.json`. The bash output's last line names
`<draft_dir>` (e.g., `projects/<id>/papers/draft_1`).

**Important — the `.handoff.json` schema is NOT pinned in v0.7.x.**
`plan.v1` writes this file with an LLM-improvised shape, so the exact
keys vary run to run. Observed shapes include `{phase, status,
candidate_labels, next_action, triage_rationale, notes}` and
`{phase, prompt_to_user, candidates_summary, next_steps}`. **Do not
depend on any specific key.** Read it loosely, for orientation only:

    cat <draft_dir>/.handoff.json

From the handoff, extract whatever is available:

- a phase / status field — confirm it indicates the throughline-pick
  pause (values seen: `"throughline_pick"`, `"plan"` +
  `status: "awaiting_user_pick"`). If it instead says `"halted"` or
  carries an error message, the orchestrator failed early — surface
  that message verbatim and stop.
- a tier verdict (`tier`) and recommended mode (`recommended_mode` /
  `mode`) if present — useful context for the user.
- any advisory / cross-walk warnings (`advisory_warnings`, `notes`) —
  strength-glyph caveats from `tools/check_throughline_glyphs.py` or
  the plan agent's own notes.

**The authoritative source of the candidates is
`<draft_dir>/throughline_candidates.md`, NOT the handoff.** It has a
stable structure: each candidate is a `## Candidate TLN: <title>`
section, and the triage verdict is a `**Tier:** {STRONG|THIN|EXPLORATORY}`
line. Derive the candidate ids by grepping the candidate headers:

    grep -n '^## Candidate ' <draft_dir>/throughline_candidates.md

## Step 5 — Present candidates via AskUserQuestion

Claude Code's AskUserQuestion widget collapses long descriptions with
a "N lines hidden" affordance and as of late-April 2026 has **no
in-widget keybinding to expand them** (Anthropic issues #29125 and
#28991 track this UX gap). Therefore: keep AskUserQuestion descriptions
**ONE LINE, ~100 characters maximum**. The full candidate content
lives in `<draft_dir>/throughline_candidates.md`; the user reads it
there.

**Before invoking AskUserQuestion**, in a Bash block tell the user
where to read the full candidates while the dialog is open:

```bash
cat <draft_dir>/throughline_candidates.md
```

(Print this command verbatim with the actual draft_dir. The user can
run it in a separate terminal or scrollback while the AskUserQuestion
is up.)

**Then invoke AskUserQuestion** with strict one-line descriptions:

For each `## Candidate TLN:` section in `throughline_candidates.md`:

- `option`: the candidate id (e.g., `TL1`, `TL2`)
- `description`: a single ≤100-char line. If the handoff JSON carries
  a per-candidate one-liner (under any key — `candidates_summary[<id>]`,
  a matching entry in `candidate_labels`, or `choices[].label`), use
  that **VERBATIM**. Otherwise, use the candidate's title from its
  `## Candidate TLN: <title>` header, truncated to one line. **Do NOT
  compose multi-line descriptions. Do NOT add evidence-map rows,
  weakness-inventory bullets, or multi-paragraph context. Claude
  Code's AskUserQuestion widget truncates descriptions >1 line with a
  "N lines hidden" collapse that the user cannot expand. The full
  candidate content is in throughline_candidates.md — that is where
  the user reads it, not in the picker widget.**

Question framing (the AskUserQuestion's prompt to the user):

> Which throughline should the manuscript build around? Full evidence
> maps and weakness inventories are at <draft_dir>/throughline_candidates.md.

If the handoff JSON carries advisory / cross-walk warnings (under
`advisory_warnings`, or surfaced in `notes`), **also print them to the
conversation as plain text BEFORE the AskUserQuestion call**, so the
user sees them outside the widget's collapse-truncation. Format:

```
⚠ Cross-walk warnings (plan.v1's evidence-map glyphs vs weakness
inventories may be inflated on these candidates):
  - {warning 1}
  - {warning 2}
```

## Step 6 — Optional revision note

After the user picks (e.g., they pick `TL2`), ask via AskUserQuestion:

> Any revision notes to apply to TL2 before drafting begins? E.g.,
> 'tighten claim 4 to add a caveat about compositional inflation' or
> 'add an explicit limitation about kingdom-level OG knowledge gaps.'
> Press skip if no revision is needed.

If the user provides a revision, capture it as `<revision_text>`.
If they skip, treat the revision as empty.

## Step 7 — Apply the pick and resume drafting

Run the bash command in the FOREGROUND. The v0.7.x drafting pipeline
runs the full sequence (citation_pool → holistic Opus draft → review →
optimize → supplementary_pool → compliance_gate → assemble) and
typically takes 15-30+ minutes of wall clock — the holistic draft is a
single large Opus pass and the review/optimize/supplementary loop adds
several more LLM calls. It runs longer when invoked from inside a
Claude Code session (the orchestrator spawns nested `claude -p`
subprocesses, each paying SDK startup latency) than from a plain
shell. If the bash tool warns about a long-running command, wait for
it — do NOT background it.

Without revision:

    beril-paper-writer continue <draft_dir> --pick <pick_id>

With revision (the revision text must be quoted to preserve spaces and
punctuation):

    beril-paper-writer continue <draft_dir> --pick <pick_id> \
        --revision '<revision_text>'

Forward `--no-adversarial` and `--no-stream` if the user passed them
in step 3.

The continue command:

1. Validates the pick against `throughline_candidates.md`
2. If `--revision` is non-empty, invokes `revise_throughline.v1` to
   refine the candidate (~$0.30 on Sonnet)
3. Otherwise copies the chosen candidate verbatim into
   `00_throughline.md`
4. Updates `state.json`: `phase=citation_pool`,
   `throughline.candidate_id=<pick>`
5. Runs the Python orchestrator (`PaperWriterOrchestrator.run_pipeline`),
   which executes the v0.7.x phase sequence end-to-end:
   `citation_pool` (verified reference pool) → `drafting`
   (`holistic_draft.v1` — one Opus pass writing Abstract, Introduction,
   Methods, Results, Discussion into a single `manuscript.md`) →
   `review` (adversarial reviewer, canonical `beril-adversarial` or
   inline fallback) → `optimize` (subtraction-only fixes; inserts
   `[NEEDS CITATION]` markers, never fabricates) → `supplementary_pool`
   (resolves those markers via verified WebSearch) → `compliance_gate`
   (ICMJE checks + Data Availability autofix) → `assemble`
   (`manuscript.md` → `manuscript.docx`, with the project's `figures/`
   staged next to the manuscript so embeds resolve)
6. Runs straight through to `phase=assembled` — there is no mid-pipeline
   pause after the throughline pick. (A `DiscrepancyInteractiveHalt`
   can pause earlier if triage finds an unresolved plan/methods
   discrepancy that needs the user; that is an exception path, not the
   normal flow.)

## Step 8 — Read the final state and present

v0.7.x does NOT write a second `.handoff.json` at the end. The
continue command runs the orchestrator straight through to
`phase=assembled` and exits. Read the final state and artifacts
directly.

Read the final phase and cumulative cost from `state.json`:

    cat <draft_dir>/state.json

- `phase` should be `"assembled"` — the pipeline completed and
  rendered the docx. Anything else means it halted; surface the
  bash stderr verbatim.
- `cost_so_far_usd` is the cumulative spend across all phases. This
  is the canonical cost record (there is no `run_metadata.json` in
  v0.7.x).
- `tier` should be `STRONG` / `THIN` / `EXPLORATORY` (populated from
  the plan phase's throughline_candidates.md). If it is `null`, the
  tier-extraction step did not fire — note it but do not block.

The v0.7.x deliverable artifacts (all under `<draft_dir>/`):

- `manuscript.md` — the single assembled manuscript (Abstract,
  Introduction, Methods, Results, Discussion, References). There are
  no per-section files; this is the one editable text artifact.
- `manuscript.docx` — the rendered Word document with figures
  embedded and tables converted to grids.
- `audit/adversarial_review.md` and `audit/adversarial_review.json`
  — the adversarial reviewer's findings (canonical `beril-adversarial`
  or the inline fallback).
- `audit/optimization_applied.md` — what the subtraction-only
  optimizer changed in response to the review.
- `audit/optimizer_subtraction_check.json` — the deterministic
  post-check confirming the optimizer only removed content / inserted
  `[NEEDS CITATION]` markers, never fabricated.
- `audit/claim_inventory_validation.json` — flags any claim rows
  whose `source_notebook` could not be resolved on disk.
- `compliance_errors.json` (top-level) — ICMJE compliance-gate
  findings, if any.
- `citation_pool.json` + `references.md` — the verified reference
  pool and the rendered bibliography.

Present a summary to the user:

- Pointer to `<draft_dir>/manuscript.docx` (the primary deliverable)
  and `<draft_dir>/manuscript.md` (the editable source).
- Pointer to the adversarial review at
  `<draft_dir>/audit/adversarial_review.md`, with the finding counts
  by severity (read them from `adversarial_review.json`'s `findings`
  array — count `P0` / `P1` / `P2`, or `Critical` / `Important` /
  `Suggested` depending on the schema version).
- Cumulative cost from `state.json`'s `cost_so_far_usd`.
- Per-call elapsed times from the bash output (already visible in
  the conversation).

## Step 9 — Guidance

Branch on the artifacts:

**Clean run (adversarial review has no P0/Critical findings,
`optimizer_subtraction_check.json` passed, `compliance_errors.json`
empty or absent):**

> The pipeline completed cleanly through `phase=assembled`. The
> adversarial review at `audit/adversarial_review.md` is the next
> thing to weigh — the optimizer already applied subtraction-only
> fixes for what it could safely remove.

**Adversarial review surfaces P0 / Critical findings:**

> The reviewer flagged {N} P0/Critical finding(s) at
> `audit/adversarial_review.md`. The subtraction-only optimizer
> handled what it could remove safely (see `audit/optimization_applied.md`),
> but findings that need *added* content — a missing section, a
> caveat, a re-analysis — are intentionally NOT auto-fixed. Address
> those by editing `manuscript.md` directly, then re-run
> `beril-paper-writer continue <draft_dir>` (it re-enters at
> `phase=assembled` and re-renders the docx), or start a fresh draft.

**Compliance gate flagged issues (`compliance_errors.json` non-empty):**

> The ICMJE compliance gate flagged {N} issue(s) in
> `compliance_errors.json`. The Data Availability autofix runs
> automatically; remaining items (e.g., a missing ethics statement)
> need the user to add the text to `manuscript.md`.

**Claim-inventory validation flagged unresolved notebooks:**

> `audit/claim_inventory_validation.json` shows {N} claim row(s) whose
> `source_notebook` could not be resolved on disk — the drafting LLM
> may have referenced a notebook that does not exist. Spot-check those
> claims in `manuscript.md` against `REPORT.md`.

## Notes for the agent

- The pipeline is restartable. If anything halts mid-run (claude
  retry exhaustion, file system error, kernel signal), `state.json`
  records what got done; the user can come back later and run
  `/beril-paper-writer-continue <draft_dir>` to resume from the
  recorded phase.
- Audit logs accumulate per-phase under `<draft_dir>/audit/`
  (`*.log` stream logs, `*.metadata.json` per-call sidecars). The
  canonical cumulative cost is `state.json`'s `cost_so_far_usd` —
  v0.7.x does not aggregate a separate `run_metadata.json`.
- Stream logs (`*.stream.log`) are kept on Write-verification failure
  for diagnostic purposes; they're deleted on success to keep the
  audit dir clean.
- This command never edits project source files. All writes are
  scoped to `<project>/papers/draft_N/`.
- If the bash command fails at any step, surface the stderr verbatim.
  The `draft` and `continue` CLI commands and the Python orchestrator
  emit informative diagnostics for every halt mode.

## Pitfall detection

When you encounter errors during this skill — script failures, missing
prompts, unexpected pipeline output — follow the pitfall-capture
protocol. Read `.claude/skills/pitfall-capture/SKILL.md` and follow
its instructions.
