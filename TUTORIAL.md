# BERIL Paper Writer — Tutorial

A step-by-step guide for writing scientific manuscripts from BERDL
analysis projects on the BERIL JupyterHub.

**Audience:** Researchers comfortable at a terminal who have a BERDL
project ready (REPORT.md + notebooks + figures).

**Time:** ~5 minutes for install + configure; ~15–50 minutes for a
full paper draft depending on depth.

**Cost:** A standard-depth paper draft on Sonnet costs roughly
$15–80 depending on project complexity and tier. To reduce cost:
use `--depth quick` (~$5–15), use `--no-adversarial` to skip the
review-rewrite loop, or use `--model` to select a cheaper model.

---

## Prerequisites

Before using the paper writer, your BERDL project must have:

- **REPORT.md** — produced by `/synthesize`. This is required; the
  paper writer will halt without it.
- **RESEARCH_PLAN.md** — your research questions and design. Used for
  throughline extraction and author list.
- **Notebooks** (`.ipynb`) — the paper writer extracts methods,
  figures, and tables from these.
- **Figures** — in your project's `figures/` directory.
- A curated **references.md** is optional but strongly recommended.
  The paper writer will build a citation pool from it; without it,
  citations will be sparse.

The typical BERIL workflow that gets you here:

```
/berdl_start           → opens an analysis session
  (iterate: run notebooks, query data, review literature)
/synthesize            → produces REPORT.md from your work
/submit                → finalizes the project
/beril-paper-writer    → drafts the manuscript (you are here)
```

You can also run `/beril-adversarial` before or after the paper
writer to get an independent harsh review of your project or draft.

---

## 1. Install

On the BERIL JupyterHub, open a terminal and run:

```bash
pipx install git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
```

This installs the `beril-paper-writer` CLI. Verify it worked:

```bash
beril-paper-writer --version
```

You should see `beril-paper-writer-skill 0.7.2` (or later).

### Configure

Verify that all dependencies are in place:

```bash
beril-paper-writer configure
```

This checks for:

- **Hard requirements** (must pass): `claude` CLI on PATH, Python
  with `nbformat` and `python-docx`, the orchestrator script.
- **Soft requirements** (warnings): `beril-adversarial` CLI (needed
  for the adversarial review arm; falls back to an inline reviewer
  if missing), bash 3.2+, standard POSIX utilities.

If `configure` exits 0, you're ready. If it exits 3, fix the missing
hard requirements it reports.

### Install the skill into your BERIL deployment

Navigate to your BERIL root directory and run:

```bash
cd /path/to/BERIL-research-observatory
beril-paper-writer install-skill .
```

This copies the skill's prompts and tools into
`.claude/skills/beril-paper-writer/`. The `--force` flag overwrites
existing files if you're upgrading:

```bash
beril-paper-writer install-skill . --force
```

### Installing beril-adversarial (recommended)

If configure warns that `beril-adversarial` is missing and you want
the full adversarial review loop:

```bash
pipx install git+https://github.com/ArkinLaboratory/beril-adversarial-skill.git
cd /path/to/BERIL-research-observatory
beril-adversarial install-skill .
```

---

## 2. Start a paper draft

There are two ways to launch the paper writer: the **CLI** (from a
terminal) or the **slash command** (inside a Claude session). Both
run the same pipeline.

### Option A: CLI

```bash
cd /path/to/BERIL-research-observatory
beril-paper-writer draft projects/my_project
```

Or if you're already inside the project directory:

```bash
cd projects/my_project
beril-paper-writer draft .
```

**Common flags:**

| Flag | Effect | Default |
|---|---|---|
| `--mode paper` | IMRAD journal manuscript | tier-driven |
| `--mode report` | Structured activity report | tier-driven |
| `--depth quick` | Fast draft (~5–10 min, lower cost) | `standard` |
| `--depth deep` | Thorough draft (~30–50 min) | `standard` |
| `--model <id>` | Override LLM model | Sonnet |
| `--no-adversarial` | Skip adversarial review; use inline reviewer | off |
| `--max-cost-usd N` | Halt if cumulative spend exceeds $N | none |

Example — quick draft without adversarial review:

```bash
beril-paper-writer draft projects/my_project --depth quick --no-adversarial
```

### Option B: Slash command (inside Claude)

In a Claude session at your BERIL root:

```
/beril-paper-writer my_project
```

Or with flags:

```
/beril-paper-writer my_project --mode paper --depth standard
```

---

## 3. Pick a throughline

The paper writer pauses after its planning phase and presents 2–3
**throughline candidates** — each is a possible narrative arc for
your paper, with an evidence map showing which notebooks and
findings support it.

The candidates are written to:

```
projects/my_project/papers/draft_1/throughline_candidates.md
```

Read them and pick one. Then resume:

### CLI

```bash
beril-paper-writer continue projects/my_project/papers/draft_1 --pick TL1
```

Replace `TL1` with your chosen candidate (`TL1`, `TL2`, or `TL3`).

To refine your pick with a revision note (e.g., "emphasize the
metabolomics angle more"):

```bash
beril-paper-writer continue projects/my_project/papers/draft_1 \
    --pick TL2 --revision "emphasize the metabolomics validation"
```

### Slash command

```
/beril-paper-writer-continue projects/my_project/papers/draft_1 --pick TL1
```

After you pick, the pipeline runs the full drafting sequence:
Methods → Results → Discussion → Introduction → Abstract →
Limitations → Data Availability → citation verification →
figure/table embedding → adversarial review → rewrite passes →
final assembly.

This is the long step (~15–40 minutes for standard depth). Progress
streams to stderr; you can watch it or come back later — state
persists on disk.

---

## 4. Review the output

When the pipeline finishes, your draft is at:

```
projects/my_project/papers/draft_1/
├── manuscript.md           ← full assembled draft (markdown)
├── manuscript.docx         ← Word document (if assemble ran)
├── next_actions.md         ← checklist of remaining issues
├── 01_methods.md           ← individual sections
├── 02_results.md
├── 03_discussion.md
├── 04_introduction.md
├── 05_abstract.md
├── 00_throughline.md       ← your chosen throughline + evidence map
├── references.md           ← numbered reference list
├── citation_map.md         ← claim → reference index
├── reframing_log.md        ← deviations from REPORT.md (auditable)
└── reviews/                ← adversarial review reports
```

**Start with `next_actions.md`** — it aggregates all remaining
issues from validators, post-checkers, and the adversarial review
into a single checklist.

**Read `reframing_log.md`** if you want to see where the paper
writer deviated from your REPORT.md findings (and why).

### Generate a Word document

If the pipeline didn't auto-assemble a docx:

```bash
beril-paper-writer assemble projects/my_project/papers/draft_1
```

This produces `manuscript.docx` with inline figures and formatted
headings.

---

## 5. Iterate

The paper writer creates a new `draft_N/` directory for each
invocation — it never overwrites a previous draft. To re-draft
after making changes to your project (new notebooks, updated
REPORT.md):

```bash
beril-paper-writer draft projects/my_project
```

This creates `draft_2/` (or the next available number).

### Running an adversarial review separately

You can run a standalone adversarial review on any draft:

```bash
beril-adversarial review --type paper projects/my_project/papers/draft_1 \
    --beril-root /path/to/BERIL-research-observatory
```

This produces a detailed review in `draft_1/audit/` with findings
rated Critical / Important / Suggested. Use these to guide your
manual edits or to inform a re-draft.

---

## Cost management

| Depth | Typical cost | Wall clock |
|---|---|---|
| `quick` | $5–15 | 5–10 min |
| `standard` | $15–50 | 15–25 min |
| `deep` | $30–80 | 30–50 min |

Costs vary with project size (more notebooks = more extraction
tokens) and tier (STRONG projects produce longer drafts than
EXPLORATORY ones).

**To limit spend:**

- Use `--max-cost-usd 20` to set a hard ceiling. The pipeline halts
  cleanly if the limit is reached; you can resume later.
- Use `--no-adversarial` to skip the review-rewrite loop (saves
  ~$5–15 but you lose the automated review).
- Use `--depth quick` for a fast first look before committing to a
  full draft.
- Use `--model` to select a less expensive model (check current
  pricing).

The pipeline reports cumulative cost in `state.json` and in the
terminal progress stream. At the end, `next_actions.md` includes
the total spend.

---

## Troubleshooting

**"REPORT.md required for plan phase"** — Your project hasn't been
synthesized yet. Run `/synthesize` in a BERIL session first.

**"REPORT.md is empty or stub-only"** — Synthesis ran but produced
no structured findings. Check that your notebooks have executed
cells with results.

**Pipeline halts mid-run** — State is saved in `state.json`. Run
`beril-paper-writer continue <draft_dir>` to resume from where it
stopped. The pipeline is idempotent — re-running a completed phase
is a no-op.

**"claude: command not found"** — The Claude CLI isn't on your PATH.
On the hub, check that `~/.local/bin` is in your PATH. Run
`beril-paper-writer configure` to diagnose.

**Cost exceeded limit** — If `--max-cost-usd` triggered a halt, the
draft is incomplete but state is saved. Increase the limit and run
`continue` to finish.

**Figures not appearing in manuscript** — Check `next_actions.md`
for figure-manifest warnings. Figures must be in the project's
`figures/` directory with filenames matching `fig<N>_<name>.<ext>`.

---

## Quick reference

```bash
# Full install sequence
pipx install git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
cd /path/to/BERIL-research-observatory
beril-paper-writer --version
beril-paper-writer configure
beril-paper-writer install-skill .

# Draft a paper
beril-paper-writer draft projects/my_project

# Pick throughline and continue
beril-paper-writer continue projects/my_project/papers/draft_1 --pick TL1

# Generate Word doc
beril-paper-writer assemble projects/my_project/papers/draft_1

# Quick + cheap run
beril-paper-writer draft projects/my_project --depth quick --no-adversarial

# Standalone adversarial review
beril-adversarial review --type paper projects/my_project/papers/draft_1 \
    --beril-root .
```
