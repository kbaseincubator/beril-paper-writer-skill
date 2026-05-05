# beril-paper-writer — Configuration guide

**Status:** v0.7.1. Living document.

The paper-writer skill has **no runtime configuration files** — it reads
everything from CLI flags, environment, and the deployed prompts. This
minimizes surprise. This document covers what controls exist and how
to tune them.

## Environment

### Required: `claude` CLI

The orchestrator shells out to `claude -p` for every pipeline phase
(plan, throughline, citation pool, per-section drafting, review,
rewrite). `beril-paper-writer configure` reports `[OK] claude —
<path>` when it's discoverable. If you see `[FAIL]`, ensure Claude
Code is installed and on PATH (`which claude`).

### Required: Python with deps

The pipx venv bundles `nbformat` (notebook parsing for methods
extraction), `python-docx` (markdown → .docx assembly), and standard
library deps. If `configure` reports missing imports, re-run
`pipx install --force` to rebuild the venv.

### Optional: `beril-adversarial` CLI

If `beril-adversarial` is on PATH and at v0.7.0+, it can be used for
standalone adversarial reviews of finished drafts. The paper-writer's
in-pipeline review uses its own fallback reviewer regardless — the
canonical adversarial is a separate pre-ship audit, not an in-loop
dependency.

Run `beril-adversarial --version` to check. If absent, paper-writer
works fine — it warns at configure time and uses the fallback reviewer
for all review passes.

### Optional: `BERIL_ROOT` env var

Both the Python CLI and the bash orchestrator auto-detect BERIL_ROOT
(cwd-walk + script install path). For programmatic invocation from
another skill's orchestrator, you can set `BERIL_ROOT=/abs/path` in
the environment to override.

## CLI flags reference

### `beril-paper-writer draft`

| Flag | Effect | Default |
|---|---|---|
| `<project_id>` | Project directory under `projects/` | Auto-detect from cwd or branch |
| `--mode paper` | IMRAD journal manuscript | Tier-driven (STRONG/THIN → paper) |
| `--mode report` | Structured activity report | Tier-driven (EXPLORATORY → report) |
| `--depth quick` | Fast draft (~5–10 min, lower cost) | `standard` |
| `--depth standard` | Normal draft (~15–25 min) | ← default |
| `--depth deep` | Thorough draft (~30–50 min) | — |
| `--model <id>` | Override LLM model for all phases | Sonnet |
| `--no-adversarial` | Skip adversarial review; use inline reviewer only | Off (adversarial used if installed) |
| `--max-cost-usd N` | Halt pipeline if cumulative cost exceeds $N | None (no cap) |
| `--no-stream` | Disable `stream_progress.py` wrapper | Off |

### `beril-paper-writer continue`

| Flag | Effect | Default |
|---|---|---|
| `<draft_dir>` | Path to paused draft directory | Required |
| `--pick TLN` | Throughline candidate id (TL1, TL2, ...) | Required at throughline_pick phase |
| `--revision "text"` | Revision note for chosen throughline | None |
| `--model <id>` | Override LLM model | Sonnet |
| `--no-adversarial` | Skip adversarial review | Off |

### `beril-paper-writer assemble`

| Flag | Effect | Default |
|---|---|---|
| `<draft_dir>` | Path to completed draft directory | Required |

## Model selection

The default model is Sonnet for all pipeline phases. This is the
cost-performance sweet spot for the paper-writer pipeline, where most
phases are constrained by the prompt (fabrication discipline, evidence
tracing) rather than raw capability.

Override per-invocation with `--model <id>`:

```bash
beril-paper-writer draft my_project --model claude-opus-4-6
```

There is deliberately no per-project or per-phase model config file.
The rationale: the paper-writer's prompts are tuned for a single model
class, and mixing models across phases (e.g., Opus for Discussion,
Sonnet for Methods) introduces register inconsistency in the assembled
manuscript. If you need a different model, override uniformly.

## Cost controls

### `--max-cost-usd`

Sets a hard ceiling on cumulative pipeline cost. The orchestrator
tracks spend in `state.json` and halts cleanly if the limit is
reached. Resume later with `beril-paper-writer continue <draft_dir>`
after raising the limit.

### `--depth`

The primary cost lever. Depth affects literature scan breadth,
multi-source verification thoroughness, and rewrite-pass budget:

| Depth | Typical cost | Wall clock |
|---|---|---|
| `quick` | $5–15 | 5–10 min |
| `standard` | $15–50 | 15–25 min |
| `deep` | $30–80 | 30–50 min |

### `--no-adversarial`

Skips the adversarial review arm, saving ~$5–15. The inline fallback
reviewer (3 detection classes, ~30s per pass) still runs for the
rewrite loop. Use this for quick iterations; run a standalone
`beril-adversarial review --type paper` before submission.

### Cost reporting

The pipeline reports cumulative cost in `state.json` and in the
terminal progress stream. At the end, `next_actions.md` includes the
total spend. No silent costs; nothing accumulates outside the pipeline
run.

## Tier and mode

The plan phase classifies each project as STRONG, THIN, or
EXPLORATORY based on evidence quality in `REPORT.md`. This tier
determines the default `--mode`:

| Tier | Default mode | Behavior |
|---|---|---|
| STRONG | `paper` | Full IMRAD manuscript |
| THIN | `paper` | IMRAD with narrowed-claim variant |
| EXPLORATORY | `report` | Structured activity report |

Override with `--mode paper` or `--mode report` to force the mode
regardless of tier.

## Per-project configuration

There is deliberately no per-project config file. All tuning is via
CLI flags at invocation time. This avoids the "where is the config;
is it being read?" debugging cycle. If you need different settings for
different projects, pass different flags.

## Orchestrator internals

The bash orchestrator (`paper_writer.sh`) discovers its Python
interpreter from the pipx venv's shebang (via `which
beril-paper-writer`). This means the correct Python — with all
bundled deps — is used for all helper invocations
(`extract_methods.py`, `validate_manuscript.py`, `citation_pool.py`,
`assemble_docx.py`, etc.) without requiring the user to manage
virtualenvs.

If `which beril-paper-writer` fails, the orchestrator falls back to
bare `python3`, which may lack `python-docx` or `nbformat` on PEP
668-locked systems. The fix is always to re-run
`pipx install --force`.

## Cross-references

- [`TUTORIAL.md`](TUTORIAL.md) — step-by-step user guide
- [`HUB_INSTALL.md`](HUB_INSTALL.md) — operator runbook for JupyterHub
- [`LAYOUT.md`](LAYOUT.md) — internal architecture and CLI details
- [`SPEC.md`](SPEC.md) — design rationale
