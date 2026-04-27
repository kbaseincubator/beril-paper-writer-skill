---
description: Resume a paused paper-writer draft. Reads state.json and continues from wherever the previous session paused (throughline-pick, drafting, or review).
argument-hint: "<draft_dir> [--pick TLN] [--revision \"text\"] [--no-adversarial] [--no-stream] [--model <model_id>]"
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# /beril-paper-writer-continue

Resume a paused paper-writer draft. Use this when:

- The previous session was closed mid-draft and you're picking up later.
- A run halted at the throughline-pick gate and you're ready to choose
  a candidate.
- A pipeline failure halted the run (claude retry exhaustion, file
  system error) and you want to retry the failed phase idempotently.

The `<draft_dir>` argument is the path to the paused draft directory
(e.g., `projects/<id>/papers/draft_1/`). The pause state is recorded
in `<draft_dir>/state.json` and `<draft_dir>/.handoff.json`.

## Step 1 — Verify and locate

Run in a Bash block:

    beril-paper-writer --version

If the command isn't found, follow the same install guidance as in
`/beril-paper-writer` Step 1.

Validate the draft_dir exists:

    test -d <draft_dir> && cat <draft_dir>/state.json | python3 -m json.tool 2>/dev/null

If the directory or state.json is missing, stop and tell the user the
draft can't be resumed — they may have meant a different path.

## Step 2 — Read the pause state

Read `<draft_dir>/state.json` and (if present) `<draft_dir>/.handoff.json`.
The state's `phase` field determines what to do.

**Cross-check `.handoff.json` against `state.json`.** If the handoff's
`phase` contradicts the state's `phase`, **trust `state.json`** and read
the relevant on-disk artifact directly. The orchestrator emits a
fresh handoff at every pause and halts loudly on handoff-write failure,
so stale handoffs should be rare — but be defensive:

- `state.phase=throughline_pick` but handoff says `halted` →
  read `<draft_dir>/throughline_candidates.md` and present candidates
  directly.
- `state.phase=review` but handoff says something else → read
  `<draft_dir>/reviews/` for the latest review.
- `state.phase=assembled` but handoff says something else → manuscript
  is already finalized at `<draft_dir>/manuscript.md`.

If the cross-check surfaces a mismatch, print a brief stderr note: e.g.
"Note: `.handoff.json` shows `phase=halted` but `state.json` shows
`throughline_pick`; reading candidates from disk directly."

The `phase` field's value (after cross-check) determines the next step:

- `init` — A prior draft attempt halted before plan.v1 finished.
  Re-running will retry init+extract+plan idempotently. Skip to
  Step 4 (resume).
- `throughline_pick` — Plan.v1 finished and the user needs to pick a
  candidate. Go to Step 3 (pick).
- `drafting` — Drafting pipeline is mid-flight or failed. Re-running
  will retry the failed phase (idempotent: section files that
  already exist are skipped). Skip to Step 4 (resume).
- `review` — Adversarial review is mid-flight or failed. Re-running
  will retry the review pass. Skip to Step 4 (resume).
- `assembled` — Pipeline is complete. Tell the user the draft is done
  and where to find the manuscript and review. Stop.

## Step 3 — Throughline pick (only when phase=throughline_pick)

If the user already passed `--pick TLN` (and optionally `--revision
"text"`) on the slash command, skip directly to Step 4.

Otherwise:

1. Read `<draft_dir>/.handoff.json` to get the candidate list and any
   advisory warnings.
2. Read `<draft_dir>/throughline_candidates.md` to surface the full
   candidate text for the user.
3. Use AskUserQuestion to ask which candidate to pursue. Show
   advisory warnings clearly. Same framing as
   `/beril-paper-writer` Step 5.
4. After the pick, ask via AskUserQuestion whether the user wants to
   apply a revision note (skip is fine).

## Step 4 — Resume

**Run the bash command in the FOREGROUND.** Drafting pipeline takes
8-15 minutes on Sonnet for STRONG tier; longer for deep mode.

For phase=throughline_pick (with a pick now selected):

    beril-paper-writer continue <draft_dir> --pick <pick_id> \
        [--revision '<revision_text>']

For other phases (no --pick needed):

    beril-paper-writer continue <draft_dir>

Forward `--no-adversarial`, `--no-stream`, `--model` if the user
passed them.

The continue command will:

- For `phase=throughline_pick` with a pick: write `00_throughline.md`
  (verbatim or via `revise_throughline.v1` if `--revision` non-empty),
  set `phase=drafting`, and dispatch to `paper_writer.sh resume`.
- For `phase=init` / `phase=drafting` / `phase=review`: dispatch
  directly to `paper_writer.sh resume` which uses idempotent phase
  functions (each checks if its output exists and skips if so).

The pipeline runs to its next pause point or to completion.

## Step 5 — Read the post-resume handoff

After the bash command exits, read `<draft_dir>/.handoff.json` again
to see where the pipeline paused or completed.

Possible end states:

- `phase=throughline_pick` — only happens if you started with
  `phase=init` and the resume completed plan.v1. Return to Step 3.
- `phase=review` — drafting completed, review ran, final pause. Read
  the review and present to the user (same as
  `/beril-paper-writer` Step 8).
- `phase=assembled` — pipeline complete. Same as Step 5 above with the
  "Already complete" framing.
- `phase=halted` — pipeline failed mid-way. Surface the
  `prompt_to_user` field verbatim; the bash output's stderr has the
  full diagnostic. The state.json records what got done; the user can
  re-run `/beril-paper-writer-continue` after fixing the underlying
  issue.

## Notes for the agent

- The continue path is idempotent. If the user runs
  `/beril-paper-writer-continue` twice in a row on the same draft,
  the second invocation should be a near-no-op (each phase function
  checks whether its output file exists and skips if so).
- The `--pick` flag is only meaningful when `phase=throughline_pick`.
  Passing it at any other phase has no effect.
- Cost accumulation: `<draft_dir>/audit/run_metadata.json` is
  rewritten at every pause; it always reflects total cost across all
  prior calls in this draft (not just the current resume).
- This command never edits project source files. All writes are
  scoped to `<draft_dir>/`.
- Surface bash stderr verbatim if the resume fails; paper_writer.sh's
  diagnostics are informative.

## Pitfall detection

When you encounter errors during this skill — script failures, missing
state.json, malformed handoff — follow the pitfall-capture protocol.
Read `.claude/skills/pitfall-capture/SKILL.md` and follow its
instructions.
