---
description: Draft an ICMJE-conformant scientific manuscript from a BERDL analysis project. Multi-stage pipeline with a load-bearing throughline-pick gate the user controls.
argument-hint: "[<project_id>] [--mode paper|report] [--depth quick|standard|deep] [--model <model_id>] [--no-adversarial] [--no-stream]"
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# /beril-paper-writer

Draft a manuscript for the analysis project at `projects/<project_id>/`.
The pipeline runs `plan.v1` to produce throughline candidates, pauses
for the user to pick one (the load-bearing user gate), then drafts
`citation_pool` → `methods` → `results` → `discussion` → `intro` →
`abstract`, runs `validate_manuscript.py`, runs the adversarial
reviewer (or fallback if absent), and pauses with a final handoff for
the user to review.

State persists in `papers/draft_N/state.json`; the session can be
closed and resumed via `/beril-paper-writer-continue` between
sessions.

## Step 1 — Verify the package is installed

Run in a Bash block:

    beril-paper-writer --version

If the command is not found, tell the user:

> The `beril-paper-writer` package isn't on your PATH. Install it with:
>
>     pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
>
> If you have an SSH key registered with GitHub you can also use the
> SSH URL — note the explicit `git@`, which is required:
>
>     pipx install --force git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git
>
> Then run `beril-paper-writer install-skill .` from your BERIL root,
> followed by `beril-paper-writer configure`.

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
        [--no-stream]

- Omit `--mode` to let triage decide (STRONG/THIN → `paper`;
  EXPLORATORY → `report` per SPEC §3.2).
- Omit `--depth` if `standard` (default; ~15-25 min total wall clock
  for the full pipeline). Pass `quick` (~5-10 min) for fast iteration.
  Pass `deep` (~30-50 min) for thorough pre-submission drafting.
- Omit `--model` to use Sonnet (default; ~3× cheaper than Opus on
  this pipeline).
- `--no-adversarial` skips `beril-adversarial-cli` and uses the inline
  `fallback_reviewer.v1` prompt. Useful if `beril-adversarial` isn't
  installed.

The pipeline will run init → extract → plan.v1, then EXIT at the
throughline-pick gate. The bash command's exit code is `0` for the
expected pause; anything non-zero is an error.

## Step 4 — Read the handoff

After the bash command exits, the orchestrator has written
`<draft_dir>/.handoff.json` describing the pause state. The bash
output's last line names `<draft_dir>` (e.g., `projects/<id>/papers/draft_1`).

Read the handoff JSON in a Bash block:

    cat <draft_dir>/.handoff.json

The file contains:

- `phase` — should be `"throughline_pick"`
- `prompt_to_user` — the question framing
- `choices` — array of `{id, label}` candidates (typically 2-3, with a
  +1 narrowed-claim for THIN tier per SPEC §3.3)
- `advisory_warnings` — strength-glyph cross-walk warnings from
  `tools/check_throughline_glyphs.py`. These flag candidates whose
  evidence map says "✓ direct" everywhere despite the weakness
  inventory naming caveats. Treat as advisory; the user makes the
  final call.
- `candidates_path` — pointer to the full
  `<draft_dir>/throughline_candidates.md` for read-and-review

If the handoff has `phase: "halted"` instead of `"throughline_pick"`,
the orchestrator failed early. Surface the `prompt_to_user` field
verbatim and stop.

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

For each candidate in `choices`:

- `option`: the candidate id (e.g., `TL1`, `TL2`)
- `description`: use the `picker_description` field from the handoff
  JSON **VERBATIM**. This is pre-formatted to ≤120 chars with the
  candidate title + glyph summary. **Do NOT compose your own
  description. Do NOT read throughline_candidates.md and add evidence-
  map rows, weakness-inventory bullets, or multi-paragraph context.
  Claude Code's AskUserQuestion widget truncates descriptions >1 line
  with a "N lines hidden" collapse that the user cannot expand.
  The full candidate content is in throughline_candidates.md — that is
  where the user reads it, not in the picker widget.**

Question framing (the AskUserQuestion's prompt to the user):

> Which throughline should the manuscript build around? Full evidence
> maps and weakness inventories are at <draft_dir>/throughline_candidates.md.

If the handoff JSON contains advisory warnings (`advisory_warnings`),
**also print them to the conversation as plain text BEFORE the
AskUserQuestion call**, so the user sees them outside the widget's
collapse-truncation. Format:

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

Run the bash command in the FOREGROUND. The drafting pipeline takes
8-15 minutes on Sonnet for STRONG-tier projects (longer for deep
mode). If the bash tool warns about a long-running command, wait for
it.

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
4. Updates `state.json`: `phase=drafting`, `throughline.candidate_id=<pick>`
5. Re-invokes `paper_writer.sh resume`, which drafts citation_pool →
   methods → results → discussion → intro → abstract, then assembles
   `manuscript.md`, runs `validate_manuscript.py`, runs the
   adversarial reviewer (or fallback)
6. Pauses at the final handoff (`phase=review`) with the manuscript
   and review ready for the user

## Step 8 — Read the final handoff and present

After the drafting pipeline completes, read the second handoff:

    cat <draft_dir>/.handoff.json

The phase should now be `"review"` (final pause for the MVP). The
handoff includes:

- `prompt_to_user` — the framing
- `review_path` — pointer to the adversarial review (or fallback)

Also read the cumulative cost from the run-metadata aggregation:

    cat <draft_dir>/audit/run_metadata.json

Present a summary to the user:

- Pointer to the assembled manuscript at `<draft_dir>/manuscript.md`
- Pointer to the adversarial review at `<review_path>`
- Validator pass/fail counts from `<draft_dir>/audit/validation.json`
- Cumulative cost from `run_metadata.json`'s `estimated_cost_usd` field
- Per-call elapsed times from the bash output (already visible in
  the conversation)

Tell the user:

> The MVP rewrite-loop is not yet wired (v0.2 work). To incorporate
> the reviewer's feedback, you can:
>
> - Edit the section files (`01_methods.md`, `02_results.md`, etc.)
>   directly and re-run `beril-paper-writer assemble <draft_dir>`
>   when that command lands (currently a stub).
> - Or wait for v0.2 which adds the rewrite loop.
>
> The state.json shows `phase=assembled`; the draft is complete from
> the orchestrator's perspective.

## Step 9 — Guidance

Branch on validator status:

**Validators all pass (no failures):**

> The pipeline completed cleanly. Validators all pass. The reviewer's
> feedback at `<review_path>` is the next thing to weigh.

**Validators fail (any of M1–M10 fail):**

> The pipeline completed but {N} validator(s) failed:
>   - M{X}: {brief description}
>   - ...
>
> The MVP doesn't auto-fix these (REPAIR_MODE lands in v0.2). You can
> address them by editing the relevant section file and re-running
> assembly when that command lands. The validator output JSON at
> `<draft_dir>/audit/validation.json` has the specific spans flagged.

**Adversarial review surfaces critical issues:**

> The reviewer flagged {N} critical issue(s) at `<review_path>`. These
> are not auto-fixed in MVP; you'll need to address them by hand or
> wait for the v0.2 rewrite loop.

## Notes for the agent

- The pipeline is restartable. If anything halts mid-run (claude
  retry exhaustion, file system error, kernel signal), `state.json`
  records what got done; the user can come back later and run
  `/beril-paper-writer-continue <draft_dir>` to resume.
- Audit logs accumulate per-phase under `<draft_dir>/audit/`. The
  `*.metadata.json` sidecars are aggregated into `run_metadata.json`
  at the end of the pipeline; that's the canonical cost record.
- Stream logs (`*.stream.log`) are kept on Write-verification failure
  for diagnostic purposes; they're deleted on success to keep the
  audit dir clean.
- This command never edits project source files. All writes are
  scoped to `<project>/papers/draft_N/`.
- If the bash command fails at any step, surface the stderr verbatim;
  paper_writer.sh has informative diagnostics for every halt mode.

## Pitfall detection

When you encounter errors during this skill — script failures, missing
prompts, unexpected pipeline output — follow the pitfall-capture
protocol. Read `.claude/skills/pitfall-capture/SKILL.md` and follow
its instructions.
