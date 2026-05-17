# beril-paper-writer — Configuration guide

**Status:** v0.8.0 + Stage 3 (2026-05-17). Living document.

The paper-writer skill has **no runtime configuration files** — it reads
everything from CLI flags, environment, and the deployed prompts. This
minimizes surprise. This document covers what controls exist and how to
tune them.

## Environment

### Required: `claude` CLI

The orchestrator shells out to `claude -p` for every LLM phase (plan,
triage, citation pool, holistic draft, review, optimize, supplementary
citations, compliance autofix). The orchestrator resolves `claude` to an
**absolute path at init** (Stage 3 Tier J) — no PATH lookup happens at
spawn time, so the result is identical foreground, background, nested
under Claude Code, or cron.

Resolution order:

1. `BERIL_CLAUDE_BIN` env var (operator override).
2. `shutil.which("claude")` against the current PATH.
3. Well-known locations (`~/.local/bin/`, `/opt/homebrew/bin/`,
   `/usr/local/bin/`, newest `~/.nvm/versions/node/*/bin/`).

If none resolve, the orchestrator **fails loud at init** with the
searched paths listed. `beril-paper-writer configure` reports the
resolved path (or the failure) the same way the orchestrator will.

```bash
# If claude isn't on PATH, point at it explicitly:
export BERIL_CLAUDE_BIN=/path/to/claude
```

### Required: Python with runtime deps

The pipx venv bundles `nbformat` (notebook parsing), `python-docx`
(markdown → .docx), and standard lib deps. If `configure` reports missing
imports, blow away and reinstall:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
# Do NOT use --editable; pipx's editable mode produces partial installs
# for this package's hatchling layout (missing top-level .py files).
```

### Recommended: `beril-adversarial` CLI

Stage 3 Tier K wires `beril-adversarial` into Tier 3 of the in-pipeline
review cascade — it's no longer just a pre-ship audit. The orchestrator
resolves it the same way as `claude` (absolute path at init, with
fallback chain):

1. `BERIL_ADVERSARIAL_BIN` env var (operator override).
2. `shutil.which("beril-adversarial")`.
3. Well-known locations.

If found, the canonical reviewer runs (10 finding classes, literature
scan, biological-claim verification, drift-from-REPORT cross-check). If
not found, the orchestrator emits a **loud warning at init** and falls
back to the inline `fallback_reviewer.v1` (3 classes, no literature scan
— a degraded review). The fallback's reviewer state is recorded in
`audit/review_mode.json` for machine discovery downstream.

```bash
# If beril-adversarial isn't on PATH:
export BERIL_ADVERSARIAL_BIN=/path/to/beril-adversarial
```

To opt out explicitly (no warning), pass `--no-adversarial` to `draft` or
`continue`. To check current install status:

```bash
beril-adversarial --version    # 0.7.0+ expected
beril-paper-writer configure   # shows what the orchestrator will resolve to
```

### Optional: `BERIL_ROOT` env var

Auto-detected (cwd-walk + script install path). For programmatic
invocation from another skill's orchestrator, set `BERIL_ROOT=/abs/path`
to override.

## CLI flags reference

### `beril-paper-writer draft`

| Flag | Effect | Default |
|---|---|---|
| `<project>` | Project path, OR bare project_id (resolved to `<cwd>/projects/<id>/` per Stage 3 Tier J) | Required |
| `--mode paper` | IMRAD journal manuscript | Tier-driven (STRONG/THIN → paper) |
| `--mode report` | Structured activity report | Tier-driven (EXPLORATORY → report) |
| `--depth quick` | Fast draft (~5–10 min, lower cost) | — |
| `--depth standard` | Normal draft (~15–25 min) | ← default |
| `--depth deep` | Thorough draft (~30–50 min) | — |
| `--model <id>` | Override LLM model for reasoning phases (plan, triage, optimizer) AND holistic draft | `claude-opus-4-6` |
| `--no-adversarial` | Skip canonical reviewer; use inline fallback explicitly | Off (canonical used if installed; loud-warn fallback if missing) |
| `--max-cost-usd N` | Soft cap; checked between LLM calls | None |
| `--no-stream` | Disable progress streaming | Off |
| `--recaption` | Force re-synthesis of LLM figure captions | Off |

### `beril-paper-writer continue`

| Flag | Effect | Default |
|---|---|---|
| `<draft_dir>` | Path to paused draft directory | Required |
| `--pick TLN` | Throughline candidate id (TL1, TL2, ...) | Required at throughline_pick phase |
| `--revision "text"` | Revision note for chosen throughline | None |
| `--model <id>` | Override LLM model | `claude-opus-4-6` |
| `--no-adversarial` | Skip canonical reviewer | Off |
| `--max-cost-usd N` | Soft cap | None |

### `beril-paper-writer assemble`

| Flag | Effect | Default |
|---|---|---|
| `<draft_dir>` | Path to completed draft directory | Required |
| `--format docx|md|pdf` | Output format | `docx` |

## Model selection

Stage 3 changed the default reasoning model from Sonnet 4.5 to **Opus
4.6**. Rationale: `self.model` drives the load-bearing phases (plan
throughline-candidate generation, triage claim extraction, the
subtraction-only optimizer); silently scaffolding the manuscript on
Sonnet was backwards. The holistic drafter and Tier-2 light review have
always run Opus (drafter) and Haiku (light review) respectively.

Override per-invocation:

```bash
# Cheaper iteration on Sonnet
beril-paper-writer draft my_project --model claude-sonnet-4-5

# Force a specific Opus version
beril-paper-writer draft my_project --model claude-opus-4-7
```

The prompts are model-tolerant but were calibrated against Opus 4.6 +
Sonnet 4.5. Off-class models (Haiku, third-party) may need prompt tuning.

## Cost controls

### `--max-cost-usd N`

Sets a *soft serial* cost ceiling. The orchestrator's circuit breaker
checks `state.cost_so_far_usd` before each LLM call and halts if it
exceeds N. Parallel calls (e.g., paper_writer.sh's legacy best-of-3
fallback reviewer, which is on the retirement track per the
2026-05-17 audit) can overshoot the cap because all candidates start
before any returns. The Python orchestrator's review path is serial and
respects the cap cleanly.

### `--depth`

The primary cost lever. Depth affects literature scan breadth,
multi-source verification thoroughness, and rewrite-pass budget:

| Depth | Typical cost | Wall clock |
|---|---|---|
| `quick` | $5–15 | 5–10 min |
| `standard` | $15–50 | 15–30 min |
| `deep` | $30–80 | 30–50 min |

### `--no-adversarial`

Skips the canonical reviewer; the inline fallback runs instead. Saves
~$5–15 but loses literature scan, biological-claim verification, and
drift-from-REPORT cross-check. For quick iterations only; run a
standalone `beril-adversarial review --type paper <draft_dir>` before
trusting the manuscript.

### Cost reporting

The orchestrator increments `state.cost_so_far_usd` after each
`claude -p` call (parsed from the `--output-format json` envelope's
`total_cost_usd` field). End of run: cumulative spend is in
`state.json`. Per-phase telemetry is in `audit/*.metadata.json`.

## Tier and mode

The plan phase classifies each project as STRONG, THIN, or EXPLORATORY
based on evidence quality in `REPORT.md` and `RESEARCH_PLAN.md`. The
tier is parsed from `throughline_candidates.md`'s `**Tier:**` line and
written to `state.tier` (Stage 3 Tier D). The tier determines the
default `--mode`:

| Tier | Default mode | Behavior |
|---|---|---|
| STRONG | `paper` | Full IMRAD manuscript |
| THIN | `paper` | IMRAD with narrowed-claim variant |
| EXPLORATORY | `report` | Structured activity report |

Override with `--mode paper` or `--mode report`.

## Per-project configuration

There is deliberately no per-project config file. All tuning is via CLI
flags at invocation time. This avoids the "where is the config; is it
being read?" debugging cycle. If you need different settings for
different projects, pass different flags.

## Running tests

The pipx venv has all runtime deps. Inject pytest once at setup:

```bash
pipx inject beril-paper-writer-skill pytest

PYBIN=$(pipx environment --value PIPX_LOCAL_VENVS)/beril-paper-writer-skill/bin/python
cd <skill-source-dir>
PYTHONPATH=src $PYBIN -m pytest tests/unit -q   # expect 965 pass
```

Do NOT run via system Python — it won't have `nbformat`, `python-docx`,
or the package itself, and you'll see `ModuleNotFoundError` for the
extract / assemble tests.

## Orchestrator internals (Python flow)

The Python orchestrator (`src/beril_paper_writer/orchestrator.py`)
drives the pipeline: init → extract → triage → plan → throughline_pick
gate → citation_pool → drafting (holistic Opus pass) → review (3-tier
cascade) → optimize → supplementary_pool → compliance_gate → assemble.

External-CLI resolution: both `claude` and `beril-adversarial` are
resolved to absolute paths once at orchestrator init via
`resolve_claude_bin()` and `resolve_adversarial_bin()`. This removes the
PATH-visibility dependency entirely — useful when the orchestrator runs
nested under Claude Code (where the inherited PATH may not include
`~/.nvm/versions/node/*/bin/` even if the interactive shell has it).

The legacy bash flow (`paper_writer.sh`) is on the retirement track per
the 2026-05-17 audit. New invocations go through the Python orchestrator;
the bash flow is preserved as a safety net during the transition.

## Cross-references

- [`README.md`](README.md) — quick-start
- [`TUTORIAL.md`](TUTORIAL.md) — step-by-step user guide
- [`HUB_INSTALL.md`](HUB_INSTALL.md) — operator runbook for JupyterHub
- [`LAYOUT.md`](LAYOUT.md) — internal architecture, CLI details, runtime
  contracts, file paths
- [`SPEC.md`](SPEC.md) — foundation + v0.8 pipeline architecture
- [`STAGED_IMPROVEMENT_PLAN.md`](STAGED_IMPROVEMENT_PLAN.md) — Stage 1/2/3
  closure tables + active backlog
