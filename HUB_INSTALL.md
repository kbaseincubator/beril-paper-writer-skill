# beril-paper-writer — JupyterHub install runbook

This is the operator runbook for deploying `beril-paper-writer` on a
KBERDL JupyterHub user environment. It assumes the hub already has
BERIL installed at `<BERIL_ROOT>` (with `.claude/skills/`, `projects/`).

For local dev install, see [`README.md`](README.md).

For end-user docs (slash command usage, throughline selection, output
reading), see [`TUTORIAL.md`](TUTORIAL.md).

For deeper integration / consumer guidance, see
[`PLUGIN_GUIDE.md`](PLUGIN_GUIDE.md) and [`CONTRACT.md`](CONTRACT.md).

## Prerequisites

The hub user environment must have:

1. **`pipx`** — for isolated installs of the package CLI.
2. **`claude` CLI** — Anthropic's Claude Code on PATH. The
   orchestrator invokes `claude -p` per pipeline phase.
3. **Read access to `BERIL_ROOT/projects/`** — at least one project
   with `REPORT.md`, `RESEARCH_PLAN.md`, and `notebooks/*.ipynb`.
4. **Strongly recommended: `beril-adversarial`** v0.7.0+. As of Stage 3
   Tier K (2026-05-17), the paper-writer wires the canonical
   adversarial reviewer **directly into Tier 3 of the in-pipeline
   review cascade**. If installed and resolvable, the canonical
   reviewer runs (10 finding classes, literature scan,
   biological-claim verification, drift-from-REPORT cross-check). If
   missing, the orchestrator emits a loud WARNING at init and falls
   back to the inline `fallback_reviewer.v1` — a degraded review with
   3 finding classes and no literature scan. The fallback path is a
   safety net; for production deployments, beril-adversarial is
   effectively required.

Verify each:

```bash
which pipx                 # /opt/conda/bin/pipx or similar
which claude               # ~/.local/bin/claude or similar
ls "$BERIL_ROOT/projects/" # at least one project_id
which beril-adversarial    # optional; v0.7.0+
```

If `pipx` is missing, install with `python3 -m pip install --user
pipx && python3 -m pipx ensurepath`. PEP 668-locked installs may
need `--break-system-packages`.

If `claude` is missing, install Claude Code per Anthropic's docs.
The skill cannot run without it.

**Env-var overrides (Stage 3 Tier J + K).** If `claude` or
`beril-adversarial` lives in a non-standard location, point at it
explicitly:

```bash
export BERIL_CLAUDE_BIN=/abs/path/to/claude
export BERIL_ADVERSARIAL_BIN=/abs/path/to/beril-adversarial
```

The orchestrator resolves both to absolute paths at init (PATH lookup
fallback → well-known locations fallback), so a binary findable from
your interactive shell but not from a nested Claude Code subshell will
still resolve correctly. `beril-paper-writer configure` reports what
the orchestrator will actually use.

**Multi-shim caveat.** Conda environments often shadow `pipx` shims.
If `which beril-paper-writer` returns `/opt/anaconda3/bin/...` instead
of `~/.local/bin/...`, you have a stale conda-installed copy. Fix:
`/opt/anaconda3/bin/pip uninstall beril-paper-writer-skill` (or
similar), or ensure `~/.local/bin` comes first in PATH.

## Install — three steps

### Step 1 — pipx install the package

From any cwd:

```bash
pipx install --force git+https://github.com/kbaseincubator/beril-paper-writer-skill.git
```

Alternative URL forms:

- **SSH (requires registered SSH key):**

  ```bash
  pipx install --force git+ssh://git@github.com/kbaseincubator/beril-paper-writer-skill.git
  ```

- **Specific version (recommended for production / reproducible
  deployments):**

  ```bash
  pipx install --force git+https://github.com/kbaseincubator/beril-paper-writer-skill.git@v1.1.0
  ```

- **From a wheel file (offline / pinned):**

  ```bash
  pipx install --force /path/to/beril_paper_writer_skill-0.8.0-py3-none-any.whl
  ```

**Do NOT use `--editable`.** pipx's `--editable` mode produces a
partial install for this package's hatchling layout — top-level .py
files (`orchestrator.py`, `config.py`) end up missing from the venv
while the `commands/` subpackage is installed. The Python orchestrator
then fails to import. The non-editable `pipx install` is correct and
takes ~30 seconds to repeat after a source change.

Verify the install:

```bash
beril-paper-writer --version    # should print 0.8.0 or later
```

If `pipx` warns about PATH, run `pipx ensurepath` once and start a
new shell.

### Step 2 — Deploy the skill into BERIL_ROOT

The `install-skill` subcommand copies the bundled SKILL.md, slash
commands, prompts (10 versioned `.v1.md` prompts), the Python helpers
(`tools/*.py`), and references into
`<BERIL_ROOT>/.claude/skills/beril-paper-writer/`. Claude Code
auto-discovers skills under `.claude/skills/`, so this is how the
slash commands become available. The pipeline itself runs from the
installed `beril-paper-writer` package (the Python orchestrator).

```bash
cd "$BERIL_ROOT"
beril-paper-writer --version             # sanity check
beril-paper-writer configure "$BERIL_ROOT"   # bootstrap CRAFT config + preflight
beril-paper-writer install-skill .       # deploy skill files
```

Or specify the path explicitly from anywhere:

```bash
beril-paper-writer install-skill /path/to/BERIL-research-observatory
```

This will:

- Copy `SKILL.md`, `commands/*.md`, `prompts/*.md`, `tools/*.py`,
  `references/*.md` into `.claude/skills/beril-paper-writer/`.
- Make `tools/*.py` executable.
- Preserve the `state/` directory verbatim (never overwritten or
  deleted across re-installs).
- Skip if the destination is up-to-date (idempotent).

Verify:

```bash
ls "$BERIL_ROOT/.claude/skills/beril-paper-writer/"
# Expect: SKILL.md, commands/, prompts/, tools/, references/, state/
ls "$BERIL_ROOT/.claude/skills/beril-paper-writer/prompts/"
# Expect (post-Stage-3, the holistic-draft era):
#   plan.v1.md              ← throughline candidates + tier verdict
#   citation_pool.v1.md     ← verified DOI/PMID pool builder
#   extract_claims.v1.md    ← numeric-claim inventory (Stage 3 Tier H tightened)
#   audit_discrepancies.v1.md
#   holistic_draft.v1.md    ← single Opus pass producing manuscript.md
#   haiku_review.v1.md      ← Tier-2 light review
#   fallback_reviewer.v1.md ← Tier-3 inline fallback (used when beril-adversarial missing)
#   optimizer.v1.md         ← subtraction-only optimizer (Stage 1 Tier A)
#   supplementary_citations.v1.md ← resolves [NEEDS CITATION] markers
#   compliance_fix.v1.md    ← ICMJE compliance autofix
#   revise_throughline.v1.md ← optional revision pass at throughline pick
#   reframer.v1.md          ← discrepancy reframing
#   figure_caption.v1.md    ← LLM caption synthesis
#   _SKELETON.md            ← prompt template (not invoked)
# Also still shipped but deferred (M1 work, not in active path):
#   claim_demarcate.v1.md, discrepancy_classify.v1.md, rewrite.v1.md
```

### Step 3 — Configure (bootstrap CRAFT runtime config)

```bash
beril-paper-writer configure "$BERIL_ROOT"     # positional; omit to auto-discover
```

`configure` makes the deployment ready to draft: it wires `claude -p` to a
CRAFT-contracted provider and runs a prerequisites preflight. In order, it:

1. Extends `<BERIL_ROOT>/.env` with the CRAFT shared-config block + this
   skill's per-skill marker — additively and idempotently (it never
   re-declares a key your `.env` already holds).
2. Selects the provider — `ACTIVE_PROVIDER` ∈ `anthropic | cborg |
   subscription`, inferred from existing keys if unset (`CBORG_API_KEY` →
   `cborg`, `ANTHROPIC_API_KEY` → `anthropic`, neither → `subscription`).
3. Discovers the provider's model list and resolves the
   reasoning/standard/fast tier pins; on a terminal it prompts for any tier
   it can't resolve, and under `--yes`/no-TTY it fails loud rather than
   guessing.
4. Writes `<BERIL_ROOT>/.claude/settings.json` (provider base URL + tier
   model ids) and `settings.local.json` (the secret token, gitignored).
5. Runs a validation ping — a real `claude -p` call that must reply exactly
   `ok` (so a wrong/renamed model id is caught, not silently accepted).
6. Checks the skill's hard runtime prerequisite, `claude` on PATH, plus an
   advisory check for the optional `beril-adversarial` reviewer. (Python
   deps ride in the pipx venv and aren't re-checked.)

Flags: `--no-discover` (pins-only), `--no-ping` (offline), `--yes` (CI;
fail loud on unresolved tiers).

If a check fails (exit 3), fix it and re-run — `configure` is idempotent:

- **`claude` not found:** install Claude Code per Anthropic's docs; `which claude`.
- **Unresolved tier under `--yes`/no-TTY:** re-run on a terminal to pick, or
  pin `MODEL_<TIER>` in `.env`.
- **Ping reply isn't `ok`:** wrong/renamed model for that tier; re-pin and re-run.
- **`beril-adversarial` missing:** advisory only — the inline fallback reviewer
  runs (degraded). Install it for the canonical Tier-3 review.
- **venv broken (`nbformat` / `python-docx` import errors at draft time):**
  `pipx install --force` to rebuild.

## First-run validation

Pick a small project with a completed `REPORT.md` for the first hub
run. The recommended smoke is a quick draft without adversarial
review:

```bash
cd "$BERIL_ROOT"
beril-paper-writer draft projects/<small_project_id> \
    --depth quick --no-adversarial
```

Expected:

- Wall clock: ~5–10 minutes on Opus.
- Cost: ~$5–15.
- Output: `projects/<id>/papers/draft_1/manuscript.md`.

The pipeline will pause after throughline selection. Resume with:

```bash
beril-paper-writer continue projects/<id>/papers/draft_1 --pick TL1
```

Verify after the pipeline completes:

1. `papers/draft_1/manuscript.md` exists and is non-empty.
2. `papers/draft_1/00_throughline.md` contains the chosen narrative
   arc with evidence map.
3. `papers/draft_1/references.md` contains a numbered reference list.
4. `papers/draft_1/p0_findings.md` lists any P0-gate issues and
   proceed options.
5. `papers/draft_1/audit/adversarial_review.md` contains the review
   (or `papers/draft_1/reviews/fallback_review.md` if the fallback
   reviewer ran).

Generate the Word document:

```bash
beril-paper-writer assemble projects/<id>/papers/draft_1
# Verify: papers/draft_1/manuscript.docx exists
```

## Verifying the slash command

Inside Claude Code on the hub, the slash command should auto-discover
after `install-skill`. Type:

```
/beril-paper-writer
```

The Claude Code agent should:

1. Verify `beril-paper-writer --version` returns 0.8.0+.
2. Walk the 4-signal project resolution tree (explicit arg → git
   branch `projects/<id>` → cwd → ask user).
3. Confirm the project has the required inputs (`REPORT.md`,
   `RESEARCH_PLAN.md`, notebooks).
4. Run the orchestrator, pausing at throughline selection.
5. After the user picks a throughline, resume the full pipeline.

If the slash command isn't recognized, check that
`<BERIL_ROOT>/.claude/skills/beril-paper-writer/SKILL.md` exists and
has the `user-invocable: true` frontmatter line. Re-run `install-skill`
if missing.

## Running tests on the hub (optional, for operator validation)

The pipx venv has all runtime deps but doesn't include pytest. Inject
it once:

```bash
pipx inject beril-paper-writer-skill pytest

PYBIN=$(pipx environment --value PIPX_LOCAL_VENVS)/beril-paper-writer-skill/bin/python
cd <path-to-skill-source>   # or wherever you cloned the repo for testing
PYTHONPATH=src $PYBIN -m pytest tests/unit -q   # expect 965 pass
```

Do NOT run tests via the hub's system Python — it won't have
`nbformat`, `python-docx`, or the package itself, and you'll see
`ModuleNotFoundError` for the extract / assemble tests.

## Upgrading

Re-run pipx install with the new version tag:

```bash
pipx install --force git+https://github.com/kbaseincubator/beril-paper-writer-skill.git@v1.1.0
beril-paper-writer --version                      # confirm new version
beril-paper-writer configure "$BERIL_ROOT"        # re-bootstrap CRAFT config (idempotent)
beril-paper-writer install-skill "$BERIL_ROOT"   # refresh skill files
```

The skill files in `<BERIL_ROOT>/.claude/skills/beril-paper-writer/`
get refreshed to match the new package version. Existing draft
directories under `projects/<id>/papers/draft_N/` are unchanged —
drafts are user-owned artifacts, not skill artifacts.

## Uninstalling

```bash
pipx uninstall beril-paper-writer-skill
rm -rf "$BERIL_ROOT/.claude/skills/beril-paper-writer"
```

This removes the CLI and the skill files. Existing drafts under
`projects/<id>/papers/` are NOT touched — those are user-owned
artifacts.

## Troubleshooting

### "REPORT.md required for plan phase"

Your project hasn't been synthesized yet. Run `/synthesize` in a
BERIL session first.

### "REPORT.md is empty or stub-only"

Synthesis ran but produced no structured findings. Check that your
notebooks have executed cells with results.

### "claude: command not found"

The Claude CLI isn't on your PATH. On the hub, check that
`~/.local/bin` is in your PATH. Run `beril-paper-writer configure`
to diagnose.

### "BERIL_ROOT does not contain .claude/skills/"

The orchestrator validates BERIL_ROOT at startup. Either:

1. Pass `--beril-root <path>` explicitly with the correct location.
2. Set `$BERIL_ROOT` env var.
3. `cd` into BERIL_ROOT before invoking.

### "PYTHON_BIN: command not found" or missing Python deps

The orchestrator can't find a Python with the required deps. The
script auto-resolves the pipx venv's Python via the
`beril-paper-writer` CLI's shebang. If `which beril-paper-writer`
shows nothing, the install didn't register. Re-run pipx install.

### Pipeline halts mid-run

State is saved in `state.json`. Run
`beril-paper-writer continue <draft_dir>` to resume from where it
stopped. The pipeline is idempotent — re-running a completed phase
is a no-op.

### Cost exceeded limit

If `--max-cost-usd` triggered a halt, the draft is incomplete but
state is saved. Increase the limit and run `continue` to finish.

### Figures not appearing in manuscript

Check `p0_findings.md` for figure-manifest warnings. Figures must be
in the project's `figures/` directory with filenames matching
`fig<N>_<name>.<ext>`.

### Wrong project_id detected by slash command

The agent inferred the project from your git branch
(`projects/<id>`), but you wanted a different project. Pass the
project_id explicitly:

```
/beril-paper-writer my_project_id
```

Explicit arguments always win over branch / cwd inference.

## Hub-specific notes

- **No image-gen dependency:** unlike `beril-presentation-maker`, the
  paper writer doesn't generate images and doesn't need a
  `CBORG_API_KEY`. It runs purely on text via Claude Code.
- **Per-user storage:** all draft output lives under
  `<BERIL_ROOT>/projects/<id>/papers/draft_N/` in the user's BERIL
  working tree, not in `~/.beril-*` or any user-level state.
  Multiple users on the same hub stay isolated.
- **Concurrency:** running multiple parallel drafts against the same
  project will create separate `draft_N` directories (auto-numbered).
  Concurrent runs against the same `draft_N` are NOT safe — use
  separate draft numbers.
- **Resumability:** `beril-paper-writer continue <draft_dir>` resumes
  from wherever the pipeline stopped. State persists on disk in
  `state.json`. Idempotent across sessions and across pipx upgrades
  (within the same v1.x line).
- **Cost transparency:** each run prints cumulative cost in the
  terminal progress stream and in `state.json` (`cost_so_far_usd`).
  No silent costs.

## When to use each subcommand

| Subcommand | Use case |
|---|---|
| `beril-paper-writer --version` | Sanity check |
| `beril-paper-writer install-skill <BERIL_ROOT>` | One-time per hub deployment + after each pipx upgrade |
| `beril-paper-writer configure [<BERIL_ROOT>]` | Bootstrap/refresh CRAFT runtime config (provider, tiers, `settings.json` + ping); re-run after `.env` or provider changes |
| `beril-paper-writer draft <project_id>` | Start a new manuscript draft |
| `beril-paper-writer continue <draft_dir> --pick TLN` | Resume after throughline selection |
| `beril-paper-writer assemble <draft_dir>` | Generate .docx from finished draft |

End users on the hub will mostly use the slash commands
(`/beril-paper-writer`, `/beril-paper-writer-continue`) inside
Claude Code. The CLI subcommands are for operators, scripted
workflows, and recovery scenarios.
