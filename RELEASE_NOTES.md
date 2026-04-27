# beril-paper-writer-skill — v0.1.0 release notes

**Release date:** 2026-04-27
**Status:** v0.1 — first usable release. Pre-1.0; expect breaking
changes between minor versions until the architectural shape stabilizes.

This document is the authoritative release-handoff for v0.1. It
describes what ships, what's deferred, and what to expect when running
the skill against a real BERDL project. The README is the perennial
"what is this" doc; this file is "what specifically v0.1 contains."

---

## What v0.1 ships

A pipx-installable Claude Code skill that drafts ICMJE-conformant
scientific manuscripts from BERDL analysis projects. The pipeline is
linear, with two load-bearing user pause points:

1. **Throughline pick.** After `plan.v1` produces 2–3 candidate
   throughlines (or 4 with the THIN-tier narrowed-claim variant), the
   user picks one — optionally with a one-line revision note that gets
   applied via the `revise_throughline.v1` mini-prompt before drafting
   begins.
2. **Final review.** After the manuscript is assembled and reviewed
   adversarially (single-pass), the pipeline pauses with a final handoff
   so the user can inspect `manuscript.md` and the review.

Between the two pauses, the pipeline runs:
`citation_pool` → `methods` → `results` → `discussion` → `intro` →
`abstract` → orchestrator-side data-availability template fill →
concatenate to `manuscript.md` → `validate_manuscript.py` →
adversarial review (or fallback if `beril-adversarial` not installed).

State persists in `papers/draft_N/state.json` plus a `.handoff.json`
the slash-command parser reads. The pipeline is fully resumable across
sessions: close Claude Code mid-draft, return three days later, run
`/beril-paper-writer-continue <draft_dir>`, and the orchestrator picks
up at the failed phase via idempotent phase functions.

### Capabilities by component

| Component | What v0.1 ships |
|---|---|
| **CLI** | `beril-paper-writer install-skill / configure / draft / continue / assemble` (assemble is a stub) |
| **Slash commands** | `/beril-paper-writer` (initial draft) + `/beril-paper-writer-continue` (resume) |
| **Orchestrator** | `paper_writer.sh` — phase-dispatched, idempotent, halt-and-resume contract via state.json + .handoff.json |
| **Drafting prompts** | 10 production prompts (plan, citation_pool, methods, results, discussion, intro, abstract, reframer, fallback_reviewer, rewrite) + revise_throughline.v1 mini-prompt |
| **Phase 2 extractors** | extract_methods.py (notebook AST walker) + extract_figures.py (figure inventory) + citation_pool.py (validate / format) |
| **Validators** | validate_manuscript.py with M1–M10 mechanized checks (run once, no auto-fix in v0.1) |
| **Post-processors** | check_throughline_glyphs.py (advisory cross-walk on plan.v1's evidence-map glyphs vs weakness inventory) |
| **Cost accounting** | per-call metadata sidecars + run_metadata.json aggregation |
| **Adversarial coupling** | Loose; shells out to `beril-adversarial-cli --type paper`, falls back to inline `fallback_reviewer.v1` if absent |
| **Resume contract** | state.json's phase field + idempotent phase functions; `--no-elicit` / `--no-adversarial` / `--no-stream` / `--max-rewrites` flags |

---

## What v0.1 deliberately does NOT ship (deferred to v0.2)

| Feature | Why deferred | Workaround in v0.1 |
|---|---|---|
| **REPAIR_MODE for validator failures** | LAYOUT's M1–M10 dispatch table is documented; each failure type maps to a specific section prompt. Wiring this into the orchestrator adds ~400 lines of bash + per-prompt REPAIR_MODE handling | Validator output sits in `audit/validation.json`; user reads + edits section files manually, re-runs assemble (when assemble lands) |
| **Review-rewrite loop with bounded retry** | Depends on REPAIR_MODE being wired first; otherwise rewrites are unbounded scope | Single-pass adversarial review only; user accepts feedback by hand |
| **`assemble` markdown→docx** | `commands/assemble.py` is a stub. python-docx is in `pyproject.toml` deps but the markdown→docx converter at `tools/assemble_docx.py` is not yet implemented | `manuscript.md` (markdown only) is the v0.1 deliverable |
| **Inline figure embedding** | `figures_inventory.md` is built and named figures get copied into `<draft_dir>/figures/`, but neither `(Fig. N)` callouts nor `![caption](path)` image tags are in the assembled markdown | Figures available at `<draft_dir>/figures/`; insert manually or use a downstream pandoc/docx step |
| **Card elicitation pre-drafting checkpoint** | Per `spec-additions/database_cards.md`: orchestrator-driven interactive dialog to elicit per-database knowledge cards. v0.1 forces `--no-elicit` on | Database-specific Methods phrasing relies on pitfalls.md / runtime REST discovery already in BERIL |
| **Citation-pool exhaustion user pause** | When `discussion.v1` surfaces `[NEEDS CITATION]` placeholders, the user is supposed to pick scope-down / citation-request / accept-as-limitation | Pump-through with scope-down default (option B2 from MVP design); discussion.v1 reframes claims that hit `[NEEDS CITATION]` |
| **Throughline re-evaluation prompt path** | Hash-diff drift detection on resume is wired (state.py:diff_artifacts), but the LLM-driven re-evaluation prompt is not. Drift surfaces a stderr warning only | Manual user review if source artifacts changed mid-draft |
| **Multi-draft comparison** | Each `draft` invocation creates a new `draft_N`; comparing across drafts is manual for v0.1 | Diff section files between draft_N and draft_{N+1} by hand |
| **`--max-cost-usd` circuit breaker** | Per-call cost is logged; no enforcement | Watch the stream-progress summary lines; abort by Ctrl-C if cost is climbing unexpectedly |

---

## Architecture (one paragraph)

A pipx-installable Python package ships:
(a) a CLI (`beril-paper-writer`) for install / configure / draft /
continue / assemble, and
(b) the skill payload as package data — prompts, references templates,
shell orchestrator, and Python helper tools. The shell orchestrator
(`paper_writer.sh`) is the single source of orchestration logic; it
invokes each prompt as a `claude -p` subagent piped through
`stream_progress.py` for Write-tool verification + cost accounting. The
Python CLI is a thin wrapper that handles user-input parsing (the
`--pick` / `--revision` flags) and re-dispatches to the shell. Slash-
command markdowns (`skill/commands/`) drive the `AskUserQuestion`-based
pick UX inside Claude Code. State persists in
`papers/draft_N/state.json`; pause points emit
`papers/draft_N/.handoff.json` describing the next user step. All
mid-pipeline failures emit a symmetric `phase=halted` handoff so the
slash-command parser sees a uniform contract: "always read
`.handoff.json` after every bash call."

For the architecture diagram and component count, see
[`LAYOUT.md`](LAYOUT.md). For the design spec, see [`SPEC.md`](SPEC.md).
For decision history, see [`DECISIONS.md`](DECISIONS.md).

---

## Installation

```bash
# Recommended path (HTTPS; works on JupyterHub and other shared hosts):
pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git

cd <BERIL_ROOT>
beril-paper-writer install-skill .
beril-paper-writer configure   # verifies claude is on PATH; warns if beril-adversarial absent
```

If you have an SSH key registered with the ArkinLaboratory GitHub org:

```bash
pipx install --force git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git
```

The `git@` is mandatory; `git+ssh://github.com/...` (without it) fails
auth on private repos. After a fresh pipx install, run `pipx ensurepath`
once if pipx writes its bin dir to a PATH location that isn't yet on
`$PATH`, then `exec $SHELL -l` to reload.

`install-skill` is idempotent: re-running it overwrites the shipped
files (`commands/`, `prompts/`, `references/`, `tools/`, `SKILL.md`)
but preserves `state/` (skill-level memory). To upgrade after a new
release:

```bash
pipx upgrade beril-paper-writer-skill
beril-paper-writer install-skill <BERIL_ROOT>
```

### Optional — install beril-adversarial for review

The paper-writer's review pass shells out to `beril-adversarial-cli
--type paper` if it's on PATH. Install the sibling skill for richer
review output:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-adversarial-skill.git
cd <BERIL_ROOT>
beril-adversarial install-skill .
```

Without it, the writer falls back to `prompts/fallback_reviewer.v1.md`
— a less thorough inline reviewer. A stderr warning is emitted at run
time when the fallback fires.

---

## Quick start

From inside a BERIL deployment, in Claude Code:

```
/beril-paper-writer functional_dark_matter
```

The slash command will:

1. Verify `beril-paper-writer` is installed.
2. Resolve the project (auto-detect from cwd if inside `projects/<id>/`).
3. Run init + extract + plan.v1, pausing at the throughline-pick gate
   (~2–3 minutes on Sonnet for a STRONG-tier project).
4. Present 2–3 throughline candidates via `AskUserQuestion`. Advisory
   warnings from `check_throughline_glyphs.py` are surfaced if the
   evidence-map glyphs look inflated.
5. Optionally ask for a one-line revision note. If non-empty, invokes
   `revise_throughline.v1` to refine the chosen candidate.
6. Run the drafting pipeline (citation_pool → methods → results →
   discussion → intro → abstract → assemble → adversarial review). 8–15
   minutes on Sonnet for STRONG-tier; longer for THIN/EXPLORATORY or
   `--depth deep`.
7. Pause at the final review with the manuscript and adversarial review
   ready for inspection.

Resume from any pause point in a different session:

```
/beril-paper-writer-continue <draft_dir> --pick TL2
```

(Or with `--revision "tighten claim 4 to add caveat about
compositional inflation"` to refine the candidate before drafting.)

### Common flags

| Flag | Default | Effect |
|---|---|---|
| `--mode paper\|report` | tier-driven | Override mode (STRONG/THIN→paper; EXPLORATORY→report per SPEC §3.2) |
| `--depth quick\|standard\|deep` | `standard` | Drafting thoroughness; quick ~5–10 min, standard ~15–25 min, deep ~30–50 min |
| `--model <id>` | `claude-sonnet-4-5-...` | Override default model. Opus is ~3× cost; Sonnet is the recommended default |
| `--no-adversarial` | off | Skip `beril-adversarial-cli`; use inline fallback reviewer |
| `--no-stream` | off | Disable `stream_progress.py` wrapper (loses Write verification + cost summary) |

---

## Pause / resume contract (load-bearing)

The slash-command parser reads `<draft_dir>/.handoff.json` after every
bash call. The handoff schema:

```json
{
  "phase": "throughline_pick | review | halted | assembled",
  "prompt_to_user": "framing for the next user interaction",
  "choices": [{"id": "TL1", "label": "..."}, ...],   // throughline_pick only
  "advisory_warnings": ["WARN ...", ...],            // throughline_pick only
  "candidates_path": "<draft_dir>/throughline_candidates.md",
  "review_path": "<draft_dir>/reviews/draft_1_review_1.md",  // review only
  "resume_command": "beril-paper-writer continue ..."
}
```

| bash exit | `handoff.phase` | meaning |
|---|---|---|
| 0 | `throughline_pick` | paused; drive AskUserQuestion pick UX |
| 0 | `review` | paused at final; present manuscript + review |
| 0 | `assembled` | already complete (resume on a finished draft) |
| non-zero | `halted` | failure mid-pipeline; surface `prompt_to_user` (includes a recovery hint) and offer retry |
| non-zero | (no handoff) | pre-init failure (no draft_dir context yet); surface stderr |

The state.json's `phase` field is the resume anchor. On halt, state's
phase stays at the in-progress value (e.g., `drafting`); the halted
handoff's phase=halted is the parser-facing signal. Rerunning
`beril-paper-writer continue <draft_dir>` dispatches on state.phase
and idempotently retries the failed step.

---

## Cost and wall-clock guidance

End-to-end smoke run on `functional_dark_matter` (STRONG-tier; 14
notebooks; ~228K-gene pangenome analysis) on Sonnet 4.5 with the
fallback reviewer:

| Metric | Result |
|---|---|
| **Total cost** | **$4.20** |
| **Total wall clock** | **17 minutes** |
| LLM calls | 8 (plan, citation_pool, reframer, methods, results, discussion, intro, abstract, fallback_reviewer) |
| Cache reads | 4.0M tokens |
| Cache creates | 554K tokens |
| Cache hit ratio | ~88% |

The cost projection above (the table below) was conservative; live
runs on STRONG-tier projects with high-touch shared context (REPORT,
RESEARCH_PLAN, throughline read by every section prompt) hit cache
heavily. **Do not bank $4.20 as a stable per-run cost**; Anthropic's
prompt-caching pricing can change. Plan for $5–10 per run on Sonnet
as a rough budget.

Per-component projection (revised based on live data; ranges reflect
project size and depth):

| Component | Sonnet cost | Wall clock |
|---|---|---|
| init + extract | ~$0 (no LLM) | ~5–10 s |
| plan.v1 | $0.40–0.60 | 2–3 min |
| citation_pool.v1 (~30 entries, MAX_BUDGET=30) | $1–3 | 3–6 min |
| revise_throughline.v1 (only if --revision provided) | $0.20–0.40 | <1 min |
| methods.v1 | $0.30–0.80 | 1–2 min |
| results.v1 | $0.30–0.80 | 1–2 min |
| discussion.v1 | $0.40–1.00 | 1–2 min |
| intro.v1 | $0.20–0.50 | 1–2 min |
| abstract.v1 | $0.10–0.30 | <1 min |
| reframer.v1 | $0.30–0.50 | <1 min |
| adversarial review (single pass) | $0.50–2.00 | 3–10 min |
| **Total end-to-end (Sonnet, with revision, with fallback reviewer)** | **$4–8** | **15–25 min** |
| **Total end-to-end (Opus)** | **~$15–30** | **same** |

The per-call `audit/*.metadata.json` sidecars aggregate into
`audit/run_metadata.json` at end of pipeline; that's the canonical
cost record. Cost is dominated by `citation_pool.v1` when its budget
is high; cache hits dominate everything else.

---

## Smoke-test results

End-to-end smoke run on `functional_dark_matter` (STRONG-tier;
14 notebooks; ~228K-gene pangenome analysis), executed 2026-04-27:

```
Project:    functional_dark_matter
Model:      claude-sonnet-4-5
Throughline: TL3 (dual-route framework) with user revision
Total cost: $4.20
Wall clock: 17 min
Validators: 9 pass / 0 fail / 1 N/A (M5)
Reviewer:   1 critical, several important + suggested (fallback reviewer; beril-adversarial-cli was absent)
Citation orphans: 0
Verdict:    PASS on §§2-4 of the runbook (init+extract+plan, throughline pick + revision, drafting pipeline). §5 resume-across-sessions not exercised live (no halts to resume from); validated independently in sandbox tests.
```

Full findings document at
[`smoke-test/end_to_end_smoke_findings.md`](smoke-test/end_to_end_smoke_findings.md)
— breaks down per-tier patch validation, the 1 critical reviewer
finding, the 2 validator regex bugs surfaced and fixed post-run, and
ship-readiness assessment.

Per-prompt smoke validations:

- `citation_pool.v1` — PASS on all six runbook criteria; surfaced
  several typo corrections in the curated seed bibliography (a
  higher-value behavior than originally designed). See
  `smoke-test/citation_pool_v1_smoke_findings.md`.
- `methods.v1` — PASS; notebook AST extraction produces clean
  `methods_provenance.md` and Methods prose is grounded against it.
  See `smoke-test/methods_v1_runbook.md`.
- `plan.v1` — PASS on triage verdict and candidate structure. The
  strength-glyph cross-walk discipline (mapping weakness-inventory
  caveats to evidence-map glyphs) is enforced programmatically via
  `tools/check_throughline_glyphs.py` rather than at the prompt
  level; advisory warnings surface in the throughline-pick handoff.
  See `smoke-test/plan_v1_smoke_findings.md`.

End-to-end findings document at
`smoke-test/end_to_end_smoke_findings.md` (written post-run).

---

## Known limitations and workarounds

### Figures are not embedded inline in the assembled manuscript

`extract_figures.py` produces a `figures_inventory.md` enumerating the
project's figures with caption candidates (notebook-context-derived
or REPORT-derived). The orchestrator copies referenced figures into
`<draft_dir>/figures/`. **But the manuscript markdown itself contains
no figure embeddings:**

- No markdown image tags (`![caption](figures/fig01_*.png)`) in any
  section file.
- No inline `(Fig. 1)` callout markers in `results.v1`'s prose
  (`results.v1` instructs the agent to emit them; live runs on Sonnet
  have produced prose without them).

The figure files are available at `<draft_dir>/figures/` for the user
to insert manually before submission, and `figures_inventory.md`
documents the caption candidates per figure. v0.2 work:

- A `results.v1` prompt edit to make figure callouts load-bearing
  (currently advisory).
- An orchestrator-side post-processor that injects markdown image
  tags into section files based on `figures_inventory.md` + the
  prose's `(Fig. N)` callouts.
- The `assemble` step will embed figures into the docx output via
  python-docx (the converter that takes manuscript.md → manuscript.docx
  is itself v0.2 work; figures land there).

**Workaround in v0.1:** insert markdown image tags by hand in the
section files before running a docx converter (e.g., pandoc), or
review `<draft_dir>/figures/` + `figures_inventory.md` alongside the
manuscript when reviewing the draft.

### `07_data_availability.md` ships with `[TBD]` markers

The orchestrator's data-availability template fill currently emits
placeholder markers for `kberdl_databases_block`,
`public_accessions_block`, and `restricted_access_block`. The proper
extraction logic (scan `methods_provenance.md` for Spark queries +
parse `RESEARCH_PLAN.md`'s data-source section) lands in v0.2.

**Workaround:** edit `07_data_availability.md` by hand before
submission. The `[TBD]` markers are validator-aware (M4 emits a
soft warning that you'll see in `audit/validation.json`).

### `assemble` is a stub

`beril-paper-writer assemble <draft_dir>` is documented in the CLI
but exits 2 with a not-yet-implemented message. The MVP delivers
`manuscript.md` (markdown concat) only, not `manuscript.docx`.

**Workaround:** convert the markdown to docx with pandoc if you need
.docx for submission:
`pandoc manuscript.md -o manuscript.docx --reference-doc=...`

### REPAIR_MODE not wired

Validator failures (M1–M10) get reported in
`audit/validation.json` but are not auto-fixed. The MVP ships
the manuscript with whatever validator state it has at end of
drafting.

**Workaround:** read the failure list, edit the relevant section
files by hand, re-run `paper_writer.sh resume <draft_dir>` (which
will skip already-completed phases and re-run the validator only
when the resume reaches a phase that hasn't completed — actually,
in MVP the validator only runs at assemble time and only
informationally; manual edits don't trigger re-validation).

### Stochastic Write-tool silent failures

Sometimes (5–10% of plan.v1 / discussion.v1 calls on Sonnet) the
LLM produces output but never invokes the Write tool. The
`stream_progress.py` parser detects this (exit 2) and the
orchestrator retries up to 3 times. After 3 attempts the phase
halts with an explanatory handoff.

**Workaround:** rerun `beril-paper-writer continue <draft_dir>` —
the same phase will retry with a fresh stochastic draw. If 3 retries
fail and a single re-invocation also fails, the prompt's
discipline-pass language may need tightening (file an issue on the
skill repo with the preserved `.stream.log`).

### Pipx-venv Python discovery (macOS Homebrew + PEP 668)

The orchestrator (`paper_writer.sh`) needs to invoke Python helper
scripts (`extract_methods.py`, `validate_manuscript.py`, etc.) using
the pipx venv's Python — not the system Python. On macOS with
Homebrew Python (PEP 668 locked), bare `python3` resolves to the
system interpreter, which doesn't have the package's runtime deps
(`nbformat`, `python-docx`).

The orchestrator auto-discovers the right Python by reading the
shebang of `which beril-paper-writer` (the pipx wrapper script's
first line points at the venv Python). This works for any standard
pipx install.

**Workaround if auto-discovery fails:** set the env var explicitly
before invoking the slash command:

```bash
export BERIL_PAPER_WRITER_PYTHON="$(pipx environment --value PIPX_LOCAL_VENVS)/beril-paper-writer-skill/bin/python"
```

Or directly:

```bash
export BERIL_PAPER_WRITER_PYTHON=~/.local/pipx/venvs/beril-paper-writer-skill/bin/python
```

Symptom of the issue if it fires: `ImportError: No module named
'nbformat'` (or `python-docx`) during phase_extract or phase_assemble.

### `state.json` schema may evolve

state.py's `STATE_SCHEMA_VERSION = "0.1"`. Any breaking schema
change in v0.2 will require a migration script. v0.1 makes no
forward-compatibility guarantees beyond "load optimistically and
warn." Keep your draft directories small (single project per
`projects/<id>/`) until v0.2 ships.

---

## Dependency model

The skill is designed to install cleanly via pipx with no external bash
binaries beyond POSIX core. Run `beril-paper-writer configure` after
install for a comprehensive environment audit.

### Hard requirements (configure exits 3 if missing)

| Dependency | Source | Why |
|---|---|---|
| `claude` CLI | Anthropic | Every drafting/review prompt runs as a `claude -p` subagent |
| Python 3.10+ | pipx installs into a venv | Helper scripts + assemble step |
| `nbformat` | pipx wheel (pyproject runtime dep) | `extract_methods.py` notebook AST walker |
| `python-docx` | pipx wheel (pyproject runtime dep) | `assemble` markdown→docx (v0.2 land) |

### Soft requirements (graceful fallback)

| Dependency | Source | Fallback |
|---|---|---|
| `beril-adversarial-cli` | sibling skill, pipx install | Inline `fallback_reviewer.v1` prompt (lighter; see SPEC §8.2) |

### What the skill explicitly does NOT depend on

- **External bash binaries beyond POSIX core.** No `flock`, no GNU
  coreutils extensions, no `pandoc`. The lock implementation is a
  stdlib Python PID-file (in `paper_writer_helpers.py`); the markdown
  → docx step uses `python-docx` (per DECISIONS D-024). Tested on
  macOS bash 3.2 and Linux bash 5.x.
- **System Python via Homebrew/apt.** The orchestrator discovers the
  pipx venv's Python via the wrapper script's shebang
  (`discover_python_bin`). System python3 is PEP 668-locked on
  Homebrew anyway, so the pipx venv is the only sensible interpreter.
- **Network at run time.** pipx downloads dependencies from PyPI at
  install time; thereafter the skill runs offline (subject to upstream
  `claude` and `WebSearch` requirements).

### What the configure audit checks

`beril-paper-writer configure` verifies every component above and
reports each as `[OK]` / `[absent]` / `[MISSING]`. Run it after every
install. Surfaces issues at install time rather than mid-pipeline (where
each retry costs $5–15 in API spend and 30 min of wall clock).

## Compatibility matrix

| Component | Required version |
|---|---|
| Python | 3.10+ |
| `claude` CLI | tested with Claude Code 2.x; uses `-p`, `--system-prompt`, `--allowedTools`, `--output-format stream-json`, `--verbose`, `--dangerously-skip-permissions` |
| `beril-adversarial` | optional; v0.1.0+ for `--type paper` mode |
| OS | macOS (bash 3.2+) / Linux (bash 5.x). No external bash binaries beyond POSIX core. |
| BERIL fork point | tested against `2cf6d3de` (`spike/beril-extended/`); should work on any BERIL deployment with the standard skill-pack layout |
| BERDL | tested against the live K-BERDL deployment (per `reference_berdl_access_live.md`); read-only access only — paper-writer never writes to K-BERDL |

---

## Upgrade path from prior dev iterations

If you used pre-release `0.1.0.dev0` builds during development:

```bash
pipx upgrade beril-paper-writer-skill
beril-paper-writer install-skill <BERIL_ROOT> --force
```

Existing `papers/draft_N/state.json` files are forward-compatible
within v0.1.x. v0.2 will bump `STATE_SCHEMA_VERSION` and ship a
migration script (planned: `beril-paper-writer migrate-state
<draft_dir>`).

---

## What v0.2 is targeting (not commitments)

The deferral list in §"What v0.1 deliberately does NOT ship" is the
v0.2 backlog, prioritized roughly:

1. **REPAIR_MODE** for validator failures + the rewrite loop. This is
   the single biggest gap in v0.1 — without it, the user has no
   automated path from "validator says M7 fail on this paragraph" to
   "fixed paragraph."
2. **`assemble` markdown → docx.** Pure-Python via python-docx, no
   pandoc dependency. Includes inline figure embedding from
   `figures_inventory.md` + `<draft_dir>/figures/`. Coupled with a
   `results.v1` prompt edit to make `(Fig. N)` callouts load-bearing
   so the assembler has anchor points to embed against.
3. **Citation-pool exhaustion user pause.** The B1 path from the MVP
   design discussion: when `[NEEDS CITATION]` count > 0, pause for
   user to choose scope-down / citation-request / accept-as-limitation.
4. **Card elicitation pre-drafting checkpoint** — the orchestrator-
   driven interactive dialog from `spec-additions/database_cards.md`.
5. **Throughline re-evaluation** on artifact drift during resume.
6. **`07_data_availability.md` proper fill** (extract from
   methods_provenance + RESEARCH_PLAN).
7. **`--max-cost-usd` circuit breaker** with prompt-level confirmation
   if a single phase is projected to exceed the cap.

---

## Pointers

- **SPEC.md** — what the skill does and why (design contract).
- **LAYOUT.md** — how the skill is packaged + runtime contracts +
  state.json schema.
- **DECISIONS.md** — design decisions log (D-001 through D-024+).
- **`spec-additions/`** — three forward-looking specs for v0.2 and
  beyond: `discrepancy_register.md`, `word_comments_at_assembly.md`,
  `database_cards.md`. Read for context, not as v0.1 contract.
- **`smoke-test/`** — runbooks and findings for per-prompt smoke
  tests (citation_pool / methods / plan) and the end-to-end runbook.
- **Issue tracker:** https://github.com/ArkinLaboratory/beril-paper-writer-skill/issues

---

*See `smoke-test/end_to_end_runbook.md` for the smoke validation
procedure. Findings document at `smoke-test/end_to_end_smoke_findings.md`
captures any surprises from the live validation.*
