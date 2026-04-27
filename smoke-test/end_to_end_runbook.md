# End-to-end smoke-test runbook — beril-paper-writer v0.1 MVP

**Purpose.** First end-to-end run of the orchestrator on a real BERDL
project. Validates the full pipeline (init → extract → plan → pause →
revise → drafting × 6 prompts → assemble → adversarial review →
final pause) plus the resume-across-sessions contract.

**Status:** v0.1 MVP scope — linear pipeline, no REPAIR_MODE, no
rewrite loop, no `assemble` markdown→docx step. Three pause points
implemented (throughline_pick + review final; citation-pool
exhaustion pumps through with scope-down default per option B2).

**Test project:** `functional_dark_matter` (STRONG-tier; 14 notebooks;
271 code cells; ~30-entry curated `references.md` seed; same project
used in `citation_pool.v1`, `methods.v1`, and `plan.v1` standalone
smoke tests).

**Prerequisites:**
- `claude` CLI on PATH; `claude --version` works
- `beril-paper-writer` package installed via pipx
  (`pipx install -e .` from the skill dir for dev iteration)
- `beril-paper-writer install-skill <BERIL_ROOT>` has run; the skill
  files are present at `<BERIL_ROOT>/.claude/skills/beril-paper-writer/`
- `beril-adversarial-cli` on PATH (optional; `--no-adversarial`
  fallback if absent)

---

## §1 — Pre-flight checks

Before starting, verify:

1. **Project exists.**

   ```bash
   ls projects/functional_dark_matter/REPORT.md \
      projects/functional_dark_matter/RESEARCH_PLAN.md \
      projects/functional_dark_matter/notebooks/*.ipynb | head -5
   ```

2. **No prior draft conflicts.** The orchestrator auto-numbers via
   `next_draft_dir`, so prior drafts are fine — just confirm the path
   it'll create:

   ```bash
   ls projects/functional_dark_matter/papers/ 2>/dev/null
   # Note the highest existing draft_N; the new run goes to draft_{N+1}.
   ```

3. **Skill prompts present.**

   ```bash
   ls .claude/skills/beril-paper-writer/prompts/ | wc -l
   # Expected: 11 .md files (10 v1 prompts + revise_throughline + _SKELETON)
   ```

4. **Reference templates present.**

   ```bash
   ls .claude/skills/beril-paper-writer/references/
   # Expected: ai_disclosure_template.md, data_availability_template.md
   ```

5. **claude works in non-interactive mode.**

   ```bash
   claude --version
   echo "test" | claude -p --model claude-sonnet-4-5-20250929 \
       --dangerously-skip-permissions \
       "Reply with the single word: ok" 2>&1 | head -5
   ```

   If this fails, the orchestrator will halt at every prompt
   invocation. Fix this before continuing.

---

## §2 — Phase 1: start the draft (init + extract + plan)

### Invocation

In the BERIL_ROOT shell:

```bash
beril-paper-writer draft functional_dark_matter
```

### Expected behavior

Console output (stderr) should show:

1. `═══ Phase: init ═══` — draft directory created
2. `═══ Phase: extract ═══` — extract_methods.py + extract_figures.py run
3. `═══ Phase: plan (plan.v1 → throughline_candidates.md) ═══`
4. Stream-progress summary line for plan.v1 (e.g.,
   `plan.v1: 02:30 · input=... output=... ~$0.42`)
5. Optional: `▸ Running check_throughline_glyphs.py (advisory cross-walk)`
6. Optional: `⚠ Strength-glyph cross-walk warnings detected`
7. `═══ Pause: throughline_pick ═══`
8. `PAUSE: throughline_pick`

Stdout (final line): the absolute path of the new draft_dir.

### Validation — §2 pass criteria

| # | Criterion | How to check |
|---|---|---|
| 1 | Process exited 0 | `echo $?` after the bash call |
| 2 | draft_dir created | `ls projects/functional_dark_matter/papers/draft_N/` |
| 3 | state.json exists, phase=throughline_pick | `python3 -c 'import json;print(json.load(open("...")).get("phase"))'` |
| 4 | reframing_log.md created | `cat <draft_dir>/reframing_log.md` (header line + blank line) |
| 5 | methods_provenance.md non-empty | `wc -l <draft_dir>/methods_provenance.md` (> 50 lines expected) |
| 6 | figures_inventory.md exists (may be empty) | `ls <draft_dir>/figures_inventory.md` |
| 7 | throughline_candidates.md non-empty | `grep -c "^## Candidate TL" <draft_dir>/throughline_candidates.md` ≥ 2 |
| 8 | .handoff.json valid | `python3 .../paper_writer_helpers.py validate-handoff <draft_dir>` |
| 9 | Plan.v1 stream log absent on success | `ls <draft_dir>/throughline_candidates.md.stream.log` should fail (file deleted on success) |
| 10 | audit/plan.metadata.json exists | `cat <draft_dir>/audit/plan.metadata.json` |

### Tool-call profile expectations (read from stream-json log if needed)

Plan.v1 against STRONG-tier projects historically uses:
- 4-8 Read calls (REPORT.md, RESEARCH_PLAN.md, several notebooks)
- 1 Write call (throughline_candidates.md)
- ~10 turns
- ~$0.40-0.50 on Sonnet, ~120-150s wall clock

---

## §3 — Phase 2: pick a throughline (interactive UX test)

### Path A — slash-command UX (production)

Inside Claude Code, the `/beril-paper-writer functional_dark_matter`
slash command runs the bash from §2 then drives the AskUserQuestion
flow. Smoke-testing this end-to-end requires Claude Code; that's a
separate validation pass.

### Path B — direct CLI (smoke-test path)

Pick TL2 (or whichever candidate the user prefers from inspection)
without a revision:

```bash
beril-paper-writer continue <draft_dir> --pick TL2
```

### Expected behavior

1. `▸ Carrying TL2 verbatim into 00_throughline.md`
2. `✓ state.json updated: phase=drafting, throughline=TL2`
3. `▸ Running: bash .../paper_writer.sh resume <draft_dir>`
4. `═══ Resume from phase: drafting ═══`
5. Pipeline begins citation_pool → methods → results → discussion →
   intro → abstract → assemble → review → final pause.

### Validation — §3 pass criteria (pick step only; full pipeline below)

| # | Criterion | How to check |
|---|---|---|
| 1 | 00_throughline.md exists, has `**Selected:** TL2` | `head -5 <draft_dir>/00_throughline.md` |
| 2 | state.json: phase=drafting, throughline.candidate_id=TL2 | python json check |
| 3 | Evidence map preserved from candidate | `grep -c "Sub-claim" <draft_dir>/00_throughline.md` ≥ 1 |

### Path B-with-revision (test the revise_throughline.v1 invocation)

To exercise the revision path on a separate smoke-run:

```bash
beril-paper-writer continue <draft_dir> --pick TL2 \
    --revision 'tighten claim about lab-field concordance to flag the binomial p=0.072 marginal significance'
```

Watch for:
- `▸ Refining TL2 per user revision`
- `▸ Invoking revise_throughline.v1 via claude -p`
- Stream-progress summary line for `revise_throughline.v1`
- `00_throughline.md` should contain a `**User revision applied:**`
  block with the revision text quoted verbatim
- The corresponding evidence-map row's strength glyph should now be
  `⚠ partial` (the prompt's discipline pass should catch this)

Cost: ~$0.30 on Sonnet for the revise step.

---

## §4 — Phase 3: full drafting pipeline

After the pick step, the orchestrator runs through 6 LLM phases plus
3 mechanical phases. Watch the stderr stream for each phase's
`═══ Phase: ... ═══` banner and stream-progress summary line.

### Phase-by-phase expected behavior

| Phase | Output file | Cost on Sonnet | Wall clock |
|---|---|---|---|
| citation_pool | pool.json + references.md + bibliography.bib + citation_map.md | $5-7 (29-30 entries) | 5-10 min |
| methods | 01_methods.md | $0.50-1.00 | 1-2 min |
| results | 02_results.md + figures copied | $0.50-1.00 | 1-2 min |
| discussion | 03_discussion.md | $0.50-1.00 | 1-2 min |
| intro | 04_introduction.md | $0.30-0.60 | 1-2 min |
| abstract | 05_abstract.md | $0.30-0.50 | <1 min |
| data_avail (orchestrator) | 07_data_availability.md (with [TBD] markers) | $0 | <1s |
| assemble (orchestrator) | manuscript.md + audit/validation.json | $0 | <1s |
| review (adversarial OR fallback) | reviews/draft_N_review_1.md | $1-3 (adversarial) / $0.50 (fallback) | 5-15 min / 1-2 min |

**Total cost projection:** $8-15 end-to-end on Sonnet for this
project. ~$25-50 on Opus. Pin Sonnet for the smoke runs unless you
want to test the cost-counter explicitly.

**Total wall clock:** 18-35 minutes typical; 40-60 minutes if
adversarial runs in deep mode.

### Pre-flight check before this phase

The pipeline runs end-to-end without further user intervention. If
you want to abort partway through, Ctrl-C is safe — the next resume
will pick up at the last completed phase (idempotent).

### Validation — §4 pass criteria

After the pipeline pauses (or completes):

| # | Criterion | How to check |
|---|---|---|
| 1 | All 6 LLM section files exist and non-empty | `wc -l <draft_dir>/0[1-5]_*.md <draft_dir>/03_discussion.md` |
| 2 | pool.json validates | `python3 .../citation_pool.py validate <draft_dir>/pool.json` |
| 3 | references.md cited from manuscript prose | `grep -c '\\[[0-9]\\+\\]' <draft_dir>/manuscript.md` should be > 10 |
| 4 | manuscript.md has all sections concatenated | `grep -E '^# ' <draft_dir>/manuscript.md` lists Title/Abstract/Intro/Methods/Results/Discussion/Limitations/Data Availability/References |
| 5 | validate_manuscript.py output exists | `cat <draft_dir>/audit/validation.json | python3 -m json.tool | head -30` |
| 6 | Adversarial review exists (or fallback) | `ls <draft_dir>/reviews/draft_*_review_*.md` |
| 7 | Run metadata aggregated | `cat <draft_dir>/audit/run_metadata.json` shows totals |
| 8 | Final handoff present | `cat <draft_dir>/.handoff.json` shows `phase=review` (mid-pause) or `phase=assembled` (complete) |
| 9 | state.json: phase=assembled | python json check |
| 10 | All audit/*.metadata.json sidecars exist | `ls <draft_dir>/audit/*.metadata.json | wc -l` should be 6-7 |

---

## §5 — Resume-across-sessions test

This validates the load-bearing "between sessions" contract. After a
successful end-to-end run from §2-§4, simulate session loss and
recovery:

### Test 5a — resume after pipeline completion (no-op case)

```bash
# After §4 completes:
beril-paper-writer continue <draft_dir>
```

Expected:
- `phase: assembled`
- `✓ Already complete (phase=assembled).`
- Pointer to manuscript and review.
- Exit 0.

### Test 5b — resume after mid-pipeline halt

This requires deliberately halting mid-pipeline to set up. The cleanest
way: use `Ctrl-C` during phase_methods (after methods.v1 starts but
before it completes). Or kill -9 the bash process from another shell.
Then:

```bash
beril-paper-writer continue <draft_dir>
```

Expected:
- `phase: drafting` (the orchestrator never reached `set_state_phase`
  to advance past drafting)
- `═══ Resume from phase: drafting ═══`
- Phase-by-phase scan: each phase function checks if its output exists
  and skips. citation_pool, methods (if it completed via Write before
  Ctrl-C), and the rest run only the missing ones.
- Pipeline runs to completion / next pause point.

### Test 5c — resume after closing Claude Code mid-pause

Easier setup. After §2 (pause at throughline_pick), close the shell.
In a new shell session, run:

```bash
beril-paper-writer continue <draft_dir>
```

Without `--pick`, this should fail loudly:

> error: phase=throughline_pick requires --pick TLN. Inspect candidates
> at <draft_dir>/throughline_candidates.md and choose one.

Then:

```bash
beril-paper-writer continue <draft_dir> --pick TL2
```

Should resume from §3 onward. Validates that the pause-state survives
session closure.

---

## §6 — Failure-mode tests (defensive)

The orchestrator should fail loud at known problem points. These are
all expected failure paths; verify each behaves cleanly.

### Test 6a — claude not on PATH

```bash
PATH=$(echo "$PATH" | sed 's|[^:]*claude[^:]*:||g') beril-paper-writer draft functional_dark_matter
```

Expected: exit 3 with `❌ 'claude' CLI is not installed or not in PATH`.

### Test 6b — project doesn't exist

```bash
beril-paper-writer draft nonexistent_project
```

Expected: exit 1 with `❌ Cannot resolve project: nonexistent_project`.

### Test 6c — wrong --pick

```bash
beril-paper-writer continue <draft_dir> --pick TL999
```

Expected: exit 1 with `error: candidate 'TL999' not found in
.../throughline_candidates.md`.

### Test 6d — silent-failure retry exhaustion (stochastic)

This is hard to deliberately trigger but happens occasionally with
plan.v1 / discussion.v1 on Sonnet. When it does:
- Stream log is preserved at `<output>.stream.log`
- Three retry attempts, each printing the silent-failure message
- Final exit 2 with the retry-exhaustion banner
- state.json phase still reflects pre-failure state

The user's recovery: re-run `beril-paper-writer continue <draft_dir>`,
which idempotently retries the failed phase with a fresh stochastic
draw.

---

## §7 — Cost auditing

After each end-to-end run, audit the cost record:

```bash
cat <draft_dir>/audit/run_metadata.json | python3 -m json.tool
```

Expected fields:
- `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens` — sums across all phases
- `estimated_cost_usd` — the total
- `elapsed_seconds` — wall clock summed across phases (note: NOT
  end-to-end; doesn't count user-pause time)
- `calls` — array of per-phase records with label, model, tokens,
  cost, elapsed

Compare against the projection in §4. A run that's >2× projected cost
deserves investigation; usually it's because of a stochastic retry
loop or because the project hit deeper-than-expected verification
(e.g., citation_pool found 50 candidates instead of 30).

---

## §8 — What this smoke test deliberately does NOT exercise

- **REPAIR_MODE** for validator failures. v0.2 work. M1-M10 failures
  in MVP just get reported in `audit/validation.json`; no auto-fix.
- **Rewrite loop** based on adversarial review. v0.2 work. The MVP
  ships the manuscript "as drafted" with the reviewer's feedback as
  audit trail.
- **Card elicitation** (database knowledge cards). MVP forces
  `--no-elicit`. v0.2 implements the orchestrator pre-drafting
  checkpoint.
- **assemble markdown → docx**. The `beril-paper-writer assemble`
  subcommand stays a stub in v0.1.
- **Continuous citation-pool re-coverage** during gap-fill rounds.
  MVP runs citation_pool once at the start; pump-through with
  scope-down on `[NEEDS CITATION]` placeholders. v0.2 adds the user-
  pause for the three options.
- **Multi-draft comparison.** Each `beril-paper-writer draft`
  invocation creates a new `draft_N`. v0.2 may add a comparison view
  across drafts.

---

## §9 — Findings template

After running, record findings in `smoke-test/end_to_end_smoke_findings.md`:

```markdown
# End-to-end MVP smoke-test findings

**Run date:** YYYY-MM-DD (executed by ___)
**Project:** functional_dark_matter
**Model:** claude-sonnet-4-5-20250929
**Total cost / wall clock:** $X.XX / Y minutes
**Verdict:** PASS | PARTIAL PASS | FAIL on each of §2 / §3 / §4 / §5 criteria

## Pass/fail summary
| Section | Pass | Notes |
|---|---|---|
| §2 init+extract+plan | ✓/✗ | ... |
| §3 throughline pick | ✓/✗ | ... |
| §4 drafting pipeline | ✓/✗ | ... |
| §5 resume-across-sessions | ✓/✗ | ... |

## Findings (substantive surprises, not just bugs)
- ...

## Open issues for v0.2
- ...
```

The findings doc format mirrors `citation_pool_v1_smoke_findings.md`
and `plan_v1_smoke_findings.md`. Keep them in `smoke-test/`.

---

*Runbook drafted 2026-04-26 based on MVP scope per
augmentation-stream-plan.md §7 and the architectural decisions in this
conversation. Intended for the first end-to-end run on
functional_dark_matter.*
