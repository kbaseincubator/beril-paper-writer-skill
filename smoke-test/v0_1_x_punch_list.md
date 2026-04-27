# beril-paper-writer v0.1.x — punch list

**Created:** 2026-04-26 (post first end-to-end live smoke run on
`functional_dark_matter`)
**Status:** authoritative; supersedes the §"Remaining for v0.1 ship"
list in `augmentation-stream-plan.md` until 0.1.0 actually ships.

This document is the punch list for the patch cycle between the
first live smoke (which surfaced 8 distinct issues + 3 deferred
architectural lessons) and the 0.1.0 release. Tiered to make the
sequencing explicit:

- **Tier 1**: coupled wiring fixes that must land together (S1 ship-
  blockers; references pipeline + handoff fatality)
- **Tier 2**: sequential wiring fixes (single-component, can land
  independently in any order)
- **Tier 3**: prompt edits (lower-risk than wiring but require more
  careful smoke validation; deferred to after Tiers 1+2 settle)
- **Tier 4**: memory entries capturing architectural lessons
- **Tier 5**: live retest + 0.1.0 ship readiness decision
- **Tier 6**: confirmed v0.2 backlog (out of scope for v0.1.x; here
  for completeness so the deferral list is auditable)

Acceptance criteria for each item are stated inline so we can grep
the file for `AC:` to find what "done" means.

---

## Tier 1 — Coupled wiring (must land together)

These two patches are coupled because Item 1.1's new finalize step
emits a handoff via the same path Item 1.2 is hardening. Landing 1.1
without 1.2 would propagate the existing argparse fragility into the
new finalize step.

### Item 1.1 — References pipeline second-pass renumbering

**Problem.** `citation_pool.py format` runs once before any prose
exists. It outputs `references.md` with all entries marked `[—]`
(uncited). Downstream prompts (methods, results, discussion) cite
freely with `[1]…[24]`. There is no second pass that walks section
files for `[N]` marks, matches them to pool entries, renumbers
references.md by first-citation order, and populates citation_map.md.
M10 cascades; reviewer's I7 cascades.

**Fix.** Add a `citation_pool.py finalize` subcommand that:
1. Reads `pool.json` (full pool)
2. Reads section files in IMRAD order:
   `01_methods.md` → `02_results.md` → `03_discussion.md` →
   `04_introduction.md` → `05_abstract.md` → `06_limitations.md`
   (when extracted) → `07_data_availability.md`
3. Walks each section in order; for every `[N]` mark, records the
   first-citation order
4. Builds a citation map: `{N: pool_entry_id}` resolved by best-guess
   match (the pool entries should have a stable identifier; pool.json
   per `citation_pool.v1.md` schema has `id` field per entry)
5. **Citation strategy: option (a) — citekeys end-to-end** (decision
   2026-04-26 by Adam after initial recommendation of option (c)).

   - Prompts cite by `[Price2018]` form (BibTeX convention: first-
     author lastname + year, with `a/b/c` suffix for disambiguation
     by alphabetical title order)
   - `citation_pool.v1` generates the citekey at pool-build time and
     writes it as `entry.citekey` in pool.json
   - `citation_pool.py format` emits `references.md` with each entry
     prefixed `**[citekey]** Author Year. Title…` so downstream
     prompts can read the file and discover the citekeys
   - Drafting prompts (methods, results, discussion, intro — NOT
     abstract) cite by `[citekey]`; their input contracts gain
     `REFERENCES_MD_PATH` if not already present
   - `citation_pool.py finalize` walks IMRAD-ordered section files
     for `[citekey]` marks, resolves each by exact match (no
     ambiguity), and produces:
     - Renumbered `references.md` with `### [1] Author Year. Title…`
       headings (numbered by first-citation order in IMRAD assembly)
     - Populated `citation_map.md` with the resolution table
     - **Section files preserve `[citekey]` form** (non-destructive;
       finalize is re-runnable)
     - **`manuscript.md` (the assembled output)** has `[N]` numeric
       form, swapped at concat-time in `phase_assemble`

   Why this over option (c): unambiguous resolution, no `[?]`
   fallout, ICMJE-compliant numeric output. Cost: 4 prompt edits +
   1 schema edit. Worth it for clean output.

6. Rewrites `references.md` with numbered headings:
   `### [1] Author2018, Title, …` (using the resolved order)
7. Rewrites `citation_map.md` with the resolution table
8. Optionally re-writes section files to swap any `[?]` marks for
   the resolved `[N]` (only when finalize confidently resolved)

Add an orchestrator step `phase_finalize_citations` invoked between
`phase_assemble` (concatenation step) and `validate_manuscript.py`,
or even before the per-section assemble — placement TBD during
implementation.

**AC:**
- After running on the live `functional_dark_matter` draft (or
  re-running the pipeline), `references.md` has at least 20 entries
  with numbered headings (`### [1]` through `### [N]`)
- `citation_map.md` has > 100 bytes of substantive content (was 208
  bytes of placeholder)
- `validate_manuscript.py` M10 returns `pass` on the resulting draft
- A `citation_resolution_warnings.md` file exists; if any warnings,
  they're surfaced in `next_actions.md` (see Item 2.4)

### Item 1.2 — `write-handoff` JSON-file passing + fatality

**Problem.** `emit_throughline_handoff` in `paper_writer.sh` uses
`eval python3 ... write-handoff $choices_args $warning_args ...`.
Bash word-splits candidate labels containing spaces, hyphens, commas,
quotes — they bleed into argparse as positional args. Live run hit
this; argparse exited non-zero; orchestrator continued; old halted
handoff stayed on disk; only the agent's improvisation rescued the
flow.

**Fix.**
1. Replace the `--choice id=label` argparse flag with a
   `--choices-json <path>` flag that reads a JSON file built by the
   orchestrator. The orchestrator writes
   `<draft_dir>/.handoff_choices.json` first, then invokes
   `write-handoff --choices-json <path>` cleanly (no eval, no word-
   splitting).
2. Same for `--advisory-warning` if its values can ever contain
   spaces (they can — see plan.v1 warning text).
3. Make every write-handoff call check the exit code; on failure,
   `halt_with` rather than continue. The symmetric handoff contract
   says "write-handoff succeeds or pipeline halts" — silent failure
   is forbidden.

**AC:**
- `emit_throughline_handoff` writes `.handoff_choices.json` and
  passes via `--choices-json`; smoke-test with deliberately
  pathological labels containing `"` and `&` and emoji passes
- write-handoff exit-code-non-zero anywhere in `paper_writer.sh`
  routes to `halt_with`
- live re-run: handoff JSON at `phase=throughline_pick` is fresh, not
  stale, after plan.v1 completes

---

## Tier 2 — Sequential wiring fixes (any order)

### Item 2.1 — Stub title block at `phase_assemble` (M1 fix)

**Problem.** No prompt writes a title block. `manuscript.md` starts
with the abstract. M1 fails with "Missing required paper-mode
section: 'title'". Auto-fixable by orchestrator.

**Fix.** In `phase_assemble`, before the section concatenation,
prepend a minimal title block:

```markdown
# {project_id_titlecased} — DRAFT v0.1: assign title before submission

**Authors:** [TBD: list authors before submission]

**Affiliations:** [TBD: list affiliations before submission]
```

**AC:**
- `manuscript.md` contains a top-level `# {title}` heading
- M1 returns `pass` on the resulting draft
- The `[TBD]` markers persist into the final docx (eventually) as
  user-resolves-before-submission flags; orchestrator never invents
  a real title

### Item 2.2 — M4 validator parser fix

**Problem.** `validate_manuscript.py` extracts section text under the
H1 heading only, ignoring H2 sub-content. `07_data_availability.md`
has H1 `# Data Availability` and all 1341 bytes of content in H2
sub-sections. Validator counts 0 chars under H1 → M4 fails.

**Fix.** In `validate_manuscript.py`'s section parser, when computing
M4's content length, sum text under all sub-headings of the matched
section. Same pattern likely applies to M9 (Limitations >150 chars)
if Limitations content is sometimes nested under H2s — verify and
extend the fix if needed.

**AC:**
- M4 returns `pass` on a `07_data_availability.md` with content nested
  under H2s
- Existing tests in `tests/unit/test_validate_manuscript.py` continue
  to pass
- New unit test added: M4 against H2-only-content document

### Item 2.3 — Wire `reframer.v1` after each drafting section

**Problem.** `reframer.v1.md` is a shipped prompt designed to detect
plan-vs-execution drift between REPORT.md and what each manuscript
section says. It's never invoked by the orchestrator. The C9 phantom
finding (Discussion mentioned GapMind 1,256, Results scoped it out)
is exactly the pattern reframer was built to catch.

**Fix.** Add `phase_reframe_after <section>` to `paper_writer.sh`
that's invoked at the end of each drafting phase (methods, results,
discussion, intro, abstract). Pass the section file + REPORT.md +
RESEARCH_PLAN.md + reframing_log.md. The prompt appends drift entries
to the log and emits a closing message naming the entry numbers.

Cost: ~$0.30 per call × 5 calls = $1.50 added per draft. Wall clock:
~30s × 5 = 2.5 min added.

Open design question: should reframer.v1 invocations happen
**inline after each section** (so the log is up-to-date when the next
section runs and can read it) or **as a batch at end** (cheaper if
prompt-caching is well-behaved across reframer invocations)?

**Recommended: inline after each section.** Discussion.v1 should be
able to read methods.v1's drift entries before drafting; that's the
point of the log being shared.

**Implementation deviation (2026-04-26):** reframer.v1's existing
escape hatch reads "Any drafted section missing → halt." Inline-per-
section invocation would always halt because later sections don't yet
exist. Editing reframer.v1 to handle partial drafts is a separate
prompt edit with its own smoke-test risk. Settled on **single
end-of-drafting invocation** — runs once after `phase_abstract`, before
`phase_data_avail`. Coverage of the C9 cross-section drift pattern
is equivalent at one-fifth the cost (~$0.30 / 30s vs ~$1.50 /
2.5min). Trade-off: discussion.v1 cannot read methods.v1's drift
entries pre-draft. v0.2 may revisit if real use shows the inline path
is needed.

**AC:**
- After live re-run, `reframing_log.md` has at least 1 entry per
  drafting section that surfaced drift
- C9-pattern (Discussion claims X not in Results) gets flagged in the
  log if it recurs
- Wall clock + cost match projection (+2.5 min, +$1.50)

### Item 2.4 — `next_actions.md` aggregator at final handoff

**Problem.** Validators and adversarial reviewer collectively produce
4 + 31 = 35 actionable issues per run. None surface in the user-
facing handoff. Users have to know to read `audit/validation.json`
+ `reviews/draft_*_review_*.md` separately.

**Fix.** Add `paper_writer_helpers.py emit-next-actions <draft_dir>`
that:
1. Reads `audit/validation.json`; for each `fail` validator, emits a
   bullet with the violation message + `escalation_path`
2. Reads `reviews/draft_*_review_*.md` (the latest); parses out
   `## Critical` / `## Important` / `## Suggested` sections; emits
   counts and the **critical-only** list (don't bury the user)
3. Reads `citation_resolution_warnings.md` if present (Item 1.1);
   surfaces ambiguous `[?]` cases
4. Writes `<draft_dir>/next_actions.md` with the aggregated list
5. The orchestrator's `emit_review_handoff` adds a
   `next_actions_path` field to the handoff JSON and tells the user
   "before submitting, work through `<draft_dir>/next_actions.md`"

**AC:**
- After live re-run, `next_actions.md` exists, has critical-only
  reviewer issues + validator failures + citation warnings
- Final `.handoff.json` has `next_actions_path` field
- Slash-command markdown surfaces the count: "9 critical issues,
  4 validator failures — see next_actions.md"

### Item 2.5 — Lock file at draft_dir level

**Problem.** No mutual exclusion. Two concurrent invocations against
the same `draft_dir` would race on phase functions. Low probability
but real.

**Fix (revised 2026-04-27 after Tier 2.7).** Initial fix used
`flock`; live retest revealed flock isn't shipped on macOS by default,
which would have required `brew install flock` — an unwanted external
bash dep for an orchestration tool. Replaced with a stdlib Python
PID-file lock implemented as `acquire-lock` / `release-lock`
subcommands in `paper_writer_helpers.py`. The bash `acquire_draft_lock`
function calls the helper + sets a `trap ... EXIT INT TERM` to call
`release-lock` on script termination.

Lock body format:
```
pid=<orchestrator-PID> verb=<draft|resume> started=<UTC ISO> host=<hostname>
```

Contention check uses `os.kill(pid, 0)` for liveness; stale locks
(holder PID dead) are overwritten with a "note: overwriting stale
lock" diagnostic.

**AC (all met):**
- Two concurrent invocations: second exits 1 with "lock held by
  active process" diagnostic naming the holder PID + verb + start time. ✓
- Lock auto-releases on normal exit via bash trap. ✓
- Stale lock (dead holder PID) is overwritten, not refused. ✓
- No external bash binary required (no flock). ✓

### Item 2.6 — Slash-command fallback contract for stale handoff

**Problem.** Live run showed the agent improvising a recovery when
`.handoff.json` was stale (still showed phase=halted from earlier
extract failure even though state.json said phase=throughline_pick).
The agent read state.json directly + read candidates.md directly +
presented choices. This worked but isn't in the contract.

**Fix.** Update `beril-paper-writer-continue.md` (and possibly
`beril-paper-writer.md`) to make the fallback explicit:

> **Cross-check `.handoff.json` against `state.json`.** If the
> handoff's phase contradicts the state's phase, **trust state.json**
> and read the relevant on-disk artifact directly:
> - `state.phase=throughline_pick` → read
>   `<draft_dir>/throughline_candidates.md` and present candidates
>   even if the handoff says halted
> - `state.phase=review` → read `<draft_dir>/reviews/` and surface
>   the latest review even if the handoff is stale
>
> Stale handoffs indicate a write-handoff bug (post-1.2 these should
> be rare, but be defensive).

**AC:**
- Markdown edit lands; YAML frontmatter still parses (run yaml.safe_load
  validation per `feedback_yaml_validate_after_sed.md`)
- Manual review: the fallback contract is unambiguous; a fresh agent
  reading the markdown would handle stale-handoff identically to
  Adam's run

---

## Tier 3 — Prompt edits (after Tiers 1+2 settle)

Prompt edits are deferred to last because:
- They're harder to smoke-test cheaply (a $5 retest per edit)
- Tiers 1+2 may surface additional prompt-level needs once the
  orchestrator is stable
- The plan.v1 saga showed prompt edits often don't take on the first
  pass — better to cluster them once

### Item 3.1 — `abstract.v1` structured-abstract enforcement (M2 fix)

**Problem (initial assumption).** M2 fails: thought abstract.v1
produced narrative paragraphs without `**Background:**` / etc. prefixes.

**Actual root cause (discovered 2026-04-26 during Tier 3 work).**
abstract.v1 was already correctly emitting the structured form, but
in **bold-italic** style (`**_Background:_**`, line 52 of the prompt).
The validator's M2 regex `\*\*<alias>[:\s]?\*\*` matched only
`**Background:**` (bold) and missed the underscore-bracketed bold-
italic form. **Bug was in the validator, not the prompt.**

**Fix.** Extended the M2 regex in `validate_manuscript.py` to accept
optional emphasis chars (`_` / `*`) inside the outer `**`:
`\*\*[_*]?\s*<alias>[:\s]?\s*[_*]?\*\*`. Matches all of `**Background:**`,
`**_Background:_**`, `***Background:***`, `**Background **`.

**AC (revised):**
- Live re-run's abstract continues to use `**_Background:_**` form
  (no prompt change needed)
- M2 returns `pass` against the existing live draft (verified
  2026-04-26: 6→7 pass after this fix)
- Existing tests in `tests/unit/test_validate_manuscript.py` continue
  to pass (extend test fixtures to cover both forms)

---

## Tier 2.7 — Eliminate flock dep + configure audit + dependency-model docs

Added 2026-04-27 after live retest hit a `flock`-missing-on-macOS
blocker. The blocker pattern is bigger than flock: we kept hitting
new environment dependencies at run time when they should be caught
at install time. Three coupled fixes.

### Item 2.7.1 — Replace flock with stdlib Python PID-file lock

Done. See revised Item 2.5 above. ~80 lines net (40 new in
paper_writer_helpers.py, 40 reduced in paper_writer.sh's bash
helper).

### Item 2.7.2 — Comprehensive `configure` audit

`commands/configure.py` extended from ~127 lines to ~250 lines.
Enumerates every external dependency:

- **Hard:** claude CLI, orchestrator's resolved Python interpreter,
  `nbformat`, `python-docx`. Exit 3 if any missing.
- **Soft:** `beril-adversarial-cli`, bash >= 3.2, POSIX core utilities
  (17 commands).
- **Informational:** `flock` (no longer required — note what
  replaced it), WebSearch (verified at run time).

Output format mirrors `[OK]` / `[absent]` / `[MISSING]` from the
original simpler audit. Adds a Summary section with hard-failure
count.

**AC (verified in sandbox):**
- All [OK] checks pass against a healthy install. ✓
- Missing hard requirements bubble up to exit 3. ✓
- Soft warnings don't block exit 0. ✓
- Informational note on flock explains the v0.1.x replacement. ✓

### Item 2.7.3 — Dependency-model docs in RELEASE_NOTES

New "## Dependency model" section before "## Compatibility matrix":
- Hard requirements table (claude, Python, nbformat, python-docx)
- Soft requirements table (beril-adversarial-cli + fallback)
- Explicit "What the skill does NOT depend on" list (flock, pandoc,
  system Python, GNU coreutils)
- Pointer to `beril-paper-writer configure` for install-time audit

Compatibility matrix updated to note "no flock required."

---

## Tier 4 — Memory entries (architectural lessons)

These are the durable lessons. They generalize beyond paper-writer
to the rest of the augmentation stream.

### Item 4.1 — Update `feedback_prompt_discipline_needs_post_check.md`

Extend the existing entry with the cross-walk-at-every-boundary
generalization:

> **Update 2026-04-26:** First end-to-end smoke run on
> functional_dark_matter showed the cross-walk-discipline failure
> mode generalizes beyond plan.v1. Discussion.v1 made claims Results
> didn't show (C9 phantom finding); Abstract+Discussion overclaimed
> beyond Weakness inventory (C1-C7). The lesson: **any cross-section
> or cross-prompt coherence requirement needs a programmatic post-
> processor.** v0.1.x ships one (check_throughline_glyphs.py).
> v0.2 needs at least two more (scope-coherence checker;
> overclaim checker). Future skills with multi-prompt outputs should
> bake post-processors in from the start, not retrofit them.

### Item 4.2 — New memory entry: bash → argparse fragility

New file `feedback_bash_to_argparse_use_json_files.md`:

> When passing multi-word string values from bash to a Python helper,
> NEVER use `eval` + unquoted variables expanded into argparse
> `--key=value` flags. Bash word-splits the values; argparse sees
> them as separate positional args; silent failure if the script
> doesn't `set -e` properly. Always use a JSON file:
>
> 1. Build the data structure in bash (e.g., heredoc-quoted JSON)
> 2. Write to `<draft_dir>/.tmp_<purpose>.json`
> 3. Pass `--json-file <path>` to the Python script
> 4. Python script reads JSON, no escaping issues
>
> Reference: paper-writer's `emit_throughline_handoff` was rewritten
> from `--choice id=label` to `--choices-json` after the live smoke
> hit this on 2026-04-26 (candidate labels with spaces and
> punctuation broke argparse).

---

## Tier 5 — Live retest + 0.1.0 ship readiness

### Item 5.1 — Live retest

After Tiers 1+2+3 land:
1. Adam re-runs `beril-paper-writer install-skill spike/beril-extended`
2. Adam re-runs `/beril-paper-writer functional_dark_matter`
3. Walks the AskUserQuestion → drafting → review → final handoff flow

**AC:**
- Pipeline runs without halts
- All 8 first-run issues resolved (validators 9/10 pass; M5 N/A;
  M10 pass; references properly numbered; reframer log populated;
  next_actions.md emitted)
- Cost ≤ $7 (allows for +$1.50 reframer); wall clock ≤ 35 min
- Reviewer (fallback or beril-adversarial) issue count drops vs
  first run (subjective; track for trend)

### Item 5.2 — Update `RELEASE_NOTES.md` with real numbers

Replace the `[Live numbers TBD]` block with the retest numbers.
Update the smoke-test results placeholder block with PASS/FAIL per §.

### Item 5.3 — Write `end_to_end_smoke_findings.md`

Following the pattern of `citation_pool_v1_smoke_findings.md` and
`plan_v1_smoke_findings.md`:
- Run date / project / model
- Pass/fail by section
- Cost / wall-clock
- Findings (substantive surprises)
- Open issues for v0.2

### Item 5.4 — 0.1.0 ship decision

If Tier 5.1 passes:
- Bump `pyproject.toml` version `0.1.0.dev0` → `0.1.0`
- Commit + push to `ArkinLaboratory/beril-paper-writer-skill`
  (Adam reviews commit message before push per CLAUDE.md)
- Tag v0.1.0
- Announce in whatever channels are appropriate

If Tier 5.1 fails on a NEW issue (not one of the punch-list items):
- Add a Tier 1.5 / Tier 2.5 patch
- Iterate

---

## Tier 6 — Confirmed v0.2 backlog

Documented here so the deferral list is auditable. None of these are
in scope for v0.1.x.

| Item | Why deferred | Provisional v0.2 priority |
|---|---|---|
| Scope-coherence post-processor (Discussion-vs-Results) | Same architectural pattern as Glyph checker but takes thought to design well | High; covers C9-class issues |
| Overclaim post-processor (verbs vs Weakness inventory) | Likewise | High; covers C1-C7 class |
| REPAIR_MODE for validator failures | Always v0.2 per LAYOUT | High |
| Review-rewrite loop with bounded retry | Depends on REPAIR_MODE | High |
| Card elicitation pre-drafting checkpoint | Per spec-additions/database_cards.md | Medium |
| `assemble` markdown→docx | python-docx already in deps; just need the converter | Medium |
| Proper `07_data_availability.md` orchestrator extraction | Replace [TBD] markers with real BERDL DB names + accessions | Medium |
| Citation-pool exhaustion user pause | The B1 path from MVP design discussion | Low (works fine in B2 pump-through) |
| `--max-cost-usd` circuit breaker | Per-call cost is logged; no enforcement yet | Low |
| State schema migration tool | Will need it when `STATE_SCHEMA_VERSION` bumps | When required |

---

## Sequencing summary

```
Tier 1 (1.1 + 1.2 coupled) ──┐
                             ├──> Tier 2 (any order) ──> Tier 3 (3.1) ──> Tier 4 (memory)
                             │                                                  │
                             └──────────────────────────────────────────────────┴──> Tier 5 (live retest + ship decision)
```

Tier 1 lands first. Tier 2 items can land in any internal order
(no sub-dependencies). Tier 3 lands once Tiers 1+2 are stable. Tier 4
memory entries land alongside Tier 3 (they're durable lessons,
not blocking work). Tier 5 is the gate to ship.

Estimated effort:
- Tier 1: 2-3 hours (coupled; references-pipeline is the bulk)
- Tier 2: 3-4 hours (six items, each ~30-60 min)
- Tier 3: 30 min (single prompt edit)
- Tier 4: 30 min (two memory writes)
- Tier 5: ~45 min wall clock for retest + ~30 min for findings doc

Total: ~7-9 hours of focused work + retest budget.

---

*Punch list authored 2026-04-26 from the first end-to-end smoke run
findings. Update inline as work progresses; mark items DONE with date
and commit hash so the file becomes the audit trail for the patch
cycle.*
