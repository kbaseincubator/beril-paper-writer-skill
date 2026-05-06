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
4. **Optional but recommended: `beril-adversarial`** v0.7.0+ — for
   standalone adversarial review of finished drafts. The paper-writer
   uses its own inline fallback reviewer for the in-loop rewrite
   cycle; the canonical adversarial reviewer is a separate pre-ship
   audit step.

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

## Install — three steps

### Step 1 — pipx install the package

From any cwd:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
```

Alternative URL forms:

- **SSH (requires registered SSH key):**

  ```bash
  pipx install --force git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git
  ```

- **Specific version (recommended for production / reproducible
  deployments):**

  ```bash
  pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git@v0.7.1
  ```

- **From a wheel file (offline / pinned):**

  ```bash
  pipx install --force /path/to/beril_paper_writer_skill-0.7.1-py3-none-any.whl
  ```

Verify the install:

```bash
beril-paper-writer --version    # should print 0.7.1 or later
```

If `pipx` warns about PATH, run `pipx ensurepath` once and start a
new shell.

### Step 2 — Deploy the skill into BERIL_ROOT

The `install-skill` subcommand copies the bundled SKILL.md, slash
commands, prompts (10 versioned `.v1.md` prompts), the orchestrator
(`tools/paper_writer.sh`), Python helpers (`tools/*.py`), and
references into `<BERIL_ROOT>/.claude/skills/beril-paper-writer/`.
Claude Code auto-discovers skills under `.claude/skills/`, so this
is how the slash commands become available.

```bash
cd "$BERIL_ROOT"
beril-paper-writer --version             # sanity check
beril-paper-writer configure             # verify claude CLI + deps
beril-paper-writer install-skill .       # deploy skill files
```

Or specify the path explicitly from anywhere:

```bash
beril-paper-writer install-skill /path/to/BERIL-research-observatory
```

This will:

- Copy `SKILL.md`, `commands/*.md`, `prompts/*.md`, `tools/*.{py,sh}`,
  `references/*.md` into `.claude/skills/beril-paper-writer/`.
- Make `tools/paper_writer.sh` and `tools/*.py` executable.
- Preserve the `state/` directory verbatim (never overwritten or
  deleted across re-installs).
- Skip if the destination is up-to-date (idempotent).

Verify:

```bash
ls "$BERIL_ROOT/.claude/skills/beril-paper-writer/"
# Expect: SKILL.md, commands/, prompts/, tools/, references/, state/
ls "$BERIL_ROOT/.claude/skills/beril-paper-writer/prompts/"
# Expect: plan.v1.md, methods.v1.md, results.v1.md, discussion.v1.md,
#         introduction.v1.md, abstract.v1.md, citation_pool.v1.md,
#         reframer.v1.md, fallback_reviewer.v1.md, rewrite.v1.md
```

### Step 3 — Configure (verify dependencies)

```bash
beril-paper-writer configure --beril-root "$BERIL_ROOT"
```

This subcommand:

- Confirms `claude` is on PATH and reports the path.
- Confirms Python with `nbformat` and `python-docx` is importable
  (these ride in the pipx venv from `pyproject.toml`).
- Reports whether `beril-adversarial` is installed (optional;
  adversarial review available if present, fallback reviewer used
  if absent).
- Reports the BERIL_ROOT it auto-discovered.
- Does NOT make any LLM calls — this is a fast pre-flight check.

If any hard check fails (exit code 3), fix the missing requirement
and re-run. Common issues:

- **`claude` not found:** install Claude Code per Anthropic's docs;
  verify with `which claude`.
- **`nbformat` / `python-docx` not importable:** re-run
  `pipx install --force` to rebuild the venv with all deps.
- **BERIL_ROOT wrong:** pass `--beril-root <path>` explicitly.

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

- Wall clock: ~5–10 minutes on Sonnet.
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
4. `papers/draft_1/next_actions.md` lists remaining issues.
5. `papers/draft_1/reviews/` contains at least one fallback review.

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

1. Verify `beril-paper-writer --version` returns 0.7.0+.
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

## Upgrading

Re-run pipx install with the new version tag:

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git@v0.7.2
beril-paper-writer --version                      # confirm new version
beril-paper-writer configure                      # verify deps still resolve
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

Check `next_actions.md` for figure-manifest warnings. Figures must be
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
  (within the same v0.7.x).
- **Cost transparency:** each run prints cumulative cost in the
  terminal progress stream and in `state.json`. At the end,
  `next_actions.md` includes the total spend. No silent costs.

## When to use each subcommand

| Subcommand | Use case |
|---|---|
| `beril-paper-writer --version` | Sanity check |
| `beril-paper-writer install-skill <BERIL_ROOT>` | One-time per hub deployment + after each pipx upgrade |
| `beril-paper-writer configure` | One-time per hub deployment + after env changes |
| `beril-paper-writer draft <project_id>` | Start a new manuscript draft |
| `beril-paper-writer continue <draft_dir> --pick TLN` | Resume after throughline selection |
| `beril-paper-writer assemble <draft_dir>` | Generate .docx from finished draft |

End users on the hub will mostly use the slash commands
(`/beril-paper-writer`, `/beril-paper-writer-continue`) inside
Claude Code. The CLI subcommands are for operators, scripted
workflows, and recovery scenarios.
