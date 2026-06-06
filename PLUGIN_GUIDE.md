# beril-paper-writer — Plugin Guide

> **Status: comprehensive reference, not canonical entry point.**
> This guide consolidates install, configure, test, and operate
> content in a single artifact. For focused documentation, prefer:
>
> - **New users →** [TUTORIAL.md](TUTORIAL.md)
> - **Hub operators →** [HUB_INSTALL.md](HUB_INSTALL.md)
> - **Configuration tuning →** [CONFIGURE.md](CONFIGURE.md)
> - **Cross-skill participants →** the
>   [PARTICIPANT-RUNBOOK.md](https://github.com/kbaseincubator/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md)
>   (covers all 4 BERIL skills end-to-end)
>
> This file is retained as a comprehensive reference but is not part
> of the uniform cross-skill doc set.

End-to-end guide to installing, configuring, testing, and operating
the `beril-paper-writer` skill within a BERIL deployment.

> **Audience.** Researchers using BERIL on the JupyterHub or a local
> fork who want to draft scientific manuscripts from a BERDL project;
> integrators wiring this skill into automated pipelines; operators
> deploying it on shared infrastructure. Not the design rationale —
> for that read [`SPEC.md`](SPEC.md) and [`DECISIONS.md`](DECISIONS.md).

> **Skill version.** This guide tracks `beril-paper-writer-skill
> v1.0.0`. For the changelog, see the per-minor `RELEASE_NOTES`
> files.

---

## Table of contents

1. [What this skill does and where it fits in BERIL](#1-what-this-skill-does-and-where-it-fits-in-beril)
2. [3-minute orientation](#2-3-minute-orientation)
3. [Installation](#3-installation)
4. [Skill deployment into BERIL](#4-skill-deployment-into-beril)
5. [Configuration](#5-configuration)
6. [Testing the skill](#6-testing-the-skill)
7. [Operation inside BERIL workflow](#7-operation-inside-beril-workflow)
8. [The drafting pipeline](#8-the-drafting-pipeline)
9. [Paper-writer's specific role](#9-paper-writers-specific-role)
10. [Cross-skill integration](#10-cross-skill-integration)
11. [Troubleshooting](#11-troubleshooting)
12. [Where to read more](#12-where-to-read-more)

---

## 1. What this skill does and where it fits in BERIL

`beril-paper-writer` drafts ICMJE-conformant scientific manuscripts
from completed BERDL analysis projects. Given a project containing
`REPORT.md`, `RESEARCH_PLAN.md`, notebooks, and figures, the skill
produces a per-draft directory (`papers/draft_N/`) with the assembled
manuscript, supporting metadata (throughline, references, citation
map, reframing log, methods provenance, figure/table inventories),
and an optional adversarial review.

The skill extracts 2–3 candidate scientific throughlines with evidence
maps. **The user picks** which narrative arc to pursue — the skill
never autonomously chooses the paper's story. After the pick, it
drafts each IMRAD section in a fixed order (Methods → Results →
Discussion → Introduction → Abstract), grounds every claim against
project sources, verifies citations against a pre-built pool, runs an
in-loop review with rewrite passes, and assembles the result into
markdown and Word formats.

**Position in the BERIL lifecycle:**

```
/berdl_start → (iterate) → /synthesize → REPORT.md
     │
     ▼
/beril-adversarial --type project    (optional pre-draft project audit)
     │
     ▼
/beril-paper-writer                  draft manuscript
     │
     ▼
user picks throughline → beril-paper-writer continue --pick TL1
     │
     ▼
(automatic: drafting → review → rewrite → assembly)
     │
     ▼
beril-paper-writer assemble          → manuscript.docx
     │
     ▼
/beril-adversarial --type paper      (pre-submission audit)
```

---

## 2. 3-minute orientation

Most common use case, on the BERIL hub, after the skill is installed:

```bash
# In your shell, on the hub, at BERIL_ROOT:
git checkout projects/my_project_id

# In Claude Code:
/beril-paper-writer
```

The agent confirms the project (from the branch name), verifies
inputs, and runs the plan phase. It pauses with 2–3 throughline
candidates. Pick one:

```
/beril-paper-writer-continue papers/draft_1 --pick TL1
```

The pipeline runs the full drafting sequence (~15–25 min on standard
depth). Output lands at
`projects/my_project_id/papers/draft_1/manuscript.md`. Read
`p0_findings.md` for any P0-gate issues and `audit/adversarial_review.md`
for the review; run `assemble` for the Word document.

If you're outside Claude Code or scripting:

```bash
beril-paper-writer draft projects/my_project_id --depth standard
# ... pause for throughline ...
beril-paper-writer continue projects/my_project_id/papers/draft_1 --pick TL1
beril-paper-writer assemble projects/my_project_id/papers/draft_1
```

For everything else read on.

---

## 3. Installation

### Prerequisites

- **Python 3.10 or newer.**
- **`pipx`** for isolated installation. Install with `python3 -m pip
  install --user pipx && python3 -m pipx ensurepath` if missing. PEP
  668-locked installs may need `--break-system-packages`.
- **`claude` CLI** on PATH. The skill shells out to `claude -p` for
  every pipeline phase. `which claude` must return a path.
- **`bash`** (3.2+ — macOS default is fine).
- **Optional: `beril-adversarial`** v0.7.0+ for standalone pre-ship
  audits.

### Install from GitHub (recommended)

```bash
pipx install --force git+https://github.com/kbaseincubator/beril-paper-writer-skill.git
```

### Install from a wheel (offline / pinned)

```bash
pipx install --force /path/to/beril_paper_writer_skill-0.7.1-py3-none-any.whl
```

### SSH alternative

```bash
pipx install --force git+ssh://git@github.com/kbaseincubator/beril-paper-writer-skill.git
```

### Verify

```bash
beril-paper-writer --version    # should print "beril-paper-writer-skill 1.1.0"
beril-paper-writer --help       # lists subcommands: draft, install-skill, configure, continue, assemble
```

### Updating

```bash
pipx install --force git+https://github.com/kbaseincubator/beril-paper-writer-skill.git@v1.1.0
```

After every update: **re-run `beril-paper-writer install-skill
<BERIL_ROOT>`** to refresh deployed skill files.

---

## 4. Skill deployment into BERIL

`beril-paper-writer` is a Claude Code skill — beyond the CLI, it
ships a "skill subtree" (prompts, orchestrator script, SKILL.md,
slash command definitions) that must be deployed into your BERIL
fork's `.claude/skills/` directory for Claude Code to discover it.

### Deploy

```bash
cd /path/to/your/beril-fork
beril-paper-writer install-skill .
```

### What gets deployed

```
<BERIL_ROOT>/.claude/skills/beril-paper-writer/
├── SKILL.md                     ← Read by the in-hub Claude Code agent
├── commands/                    ← Slash command definitions
│   ├── beril-paper-writer.md
│   └── beril-paper-writer-continue.md
├── prompts/                     ← 10 versioned drafting prompts
│   ├── plan.v1.md               ← Triage + throughline candidates
│   ├── methods.v1.md            ← Notebook-grounded methods
│   ├── results.v1.md            ← Results with figure embedding
│   ├── discussion.v1.md         ← Discussion + limitations
│   ├── introduction.v1.md       ← Introduction (written after Discussion)
│   ├── abstract.v1.md           ← Abstract (written last)
│   ├── citation_pool.v1.md      ← Literature scan + 9-field verification
│   ├── reframer.v1.md           ← Detect drift from REPORT.md
│   ├── fallback_reviewer.v1.md  ← In-loop reviewer (3 classes)
│   └── rewrite.v1.md            ← Apply review-driven fixes
├── references/                  ← Reporting standards checklist
├── tools/                       ← Python helpers
│   ├── extract_methods.py       ← Notebook AST walker
│   ├── extract_figures.py       ← Figure selection + caption extraction
│   ├── extract_tables.py        ← Table extraction from notebooks
│   ├── citation_pool.py         ← Literature-pool builder + verifier
│   ├── validate_manuscript.py   ← M1–M10 mechanized checks
│   ├── check_figures_manifest.py
│   ├── check_tables_manifest.py
│   ├── check_scope_coherence.py
│   ├── check_throughline_glyphs.py
│   ├── assemble_docx.py         ← Markdown → .docx via python-docx
│   └── stream_progress.py       ← Write-tool verification + cost
└── state/                       ← Preserved across re-installs
```

### Idempotency and state

`install-skill` is **idempotent**: re-running overwrites every
shipped file with the current package version. The `state/` directory
is the only thing it preserves. Per-draft directories under
`projects/<id>/papers/` are NOT touched by install-skill — those are
project artifacts, not skill artifacts.

---

## 5. Configuration

The skill has **no runtime configuration files**. See
[`CONFIGURE.md`](CONFIGURE.md) for the full reference. Key points:

- **`claude` CLI** is the only hard runtime dependency.
- **Model default** is Opus. Override per-invocation with
  `--model <id>`.
- **Cost controls** via `--max-cost-usd`, `--depth`, and
  `--no-adversarial`.
- **No per-project config.** All tuning is via CLI flags.
- **BERIL_ROOT** auto-detected from cwd / branch / env var /
  install path.

---

## 6. Testing the skill

### Unit tests (fast, no LLM cost)

```bash
git clone https://github.com/kbaseincubator/beril-paper-writer-skill.git
cd beril-paper-writer-skill
pip install -e ".[dev]"     # or --break-system-packages if PEP 668
pytest tests/ -v
```

Run `pytest` from the repo root; the suite should pass clean. Tests
cover validator behavior (M1–M10), post-checkers (figures, tables,
scope coherence, throughline glyphs), state management, discovery,
CLI surface, and prompt structural integrity.

### Live test against a real BERDL project (LLM cost ~$5–15)

```bash
beril-paper-writer draft projects/<small_project_id> \
    --depth quick --no-adversarial
# ... pick throughline when prompted ...
beril-paper-writer continue projects/<id>/papers/draft_1 --pick TL1
```

Verify after the run:

- `manuscript.md` exists and is non-empty (typically 3000–8000 words).
- `00_throughline.md` contains the chosen narrative arc.
- `references.md` contains a numbered reference list.
- `p0_findings.md` lists any P0-gate issues; `state.json` carries
  the cumulative cost (`cost_so_far_usd`).
- `audit/adversarial_review.{md,json}` contains the review (or
  `reviews/fallback_review.md` if the fallback reviewer ran).
- M1–M10 validator results are recorded under `audit/`.

### Cross-skill integration smoke

Paper-writer's `CONTRACT.md` documents the consumer-side contract
with `beril-adversarial`. The adversarial skill's test suite includes
`tests/integration/test_paper_writer_interop.py` (6 tests) that
verify the producer-side shape.

---

## 7. Operation inside BERIL workflow

### Two surfaces

| | Slash command | Python CLI |
|---|---|---|
| Invocation | `/beril-paper-writer` | `beril-paper-writer draft` |
| Subcommand keyword | NONE — flags follow directly | **`draft`** required |
| Best for | Interactive use by a researcher | Programmatic / scripted |

Both drive the same Python orchestrator
(`src/beril_paper_writer/orchestrator.py`, class
`PaperWriterOrchestrator`) and produce identical output. Pick
whichever fits your context.

### Project resolution

When invoked, the skill figures out which project you mean using a
4-signal resolution tree (in priority order):

1. **Explicit argument.** If you typed a project_id after the slash
   command or CLI verb, that's used directly.
2. **Git branch.** The hub uses `projects/<id>` as the branch-naming
   convention. If your current branch matches that pattern, the agent
   infers the project from it. **Strongest signal on the hub.**
3. **cwd.** If you `cd`-ed into `projects/<id>/`, that's the project.
4. **Ask you.** If none of the above resolve, the agent lists
   projects and asks you to pick.

The agent **always confirms** before proceeding when project
resolution comes from a signal other than your explicit argument.

### Draft auto-numbering

Each `draft` invocation creates a new `papers/draft_N/` directory
(auto-incrementing N). Drafts are immutable — re-running `draft`
creates `draft_{N+1}`, never overwrites an existing draft. Use
`continue` to modify an in-progress draft.

---

## 8. The drafting pipeline

The skill executes a **phased drafting pipeline**:

### Phase 1 — Plan + triage

Reads project artifacts (`REPORT.md`, `RESEARCH_PLAN.md`, notebooks,
figures). Classifies project quality as STRONG / THIN / EXPLORATORY.
Extracts 2–3 candidate scientific throughlines with evidence maps.
Each candidate has strength glyphs (✓ direct / ⚠ partial /
✗ contradicts / ◇ orthogonal), a weakness inventory, and a "would
NOT include if chosen" list.

Wall clock: ~2–3 min on Opus.

### Phase 2 — Throughline pick (user gate)

The pipeline **pauses** and presents the candidates. The user picks
one (and optionally provides a revision note). This is the load-
bearing user decision — the entire manuscript's narrative arc depends
on it.

Resume: `beril-paper-writer continue <draft_dir> --pick TL1`

### Phase 3 — Citation pool curation

`citation_pool.v1` scans the project's `references.md` (if present)
and builds a verified citation pool. Each reference is verified by
DOI/PMID resolution via WebSearch. References that fail verification
are excluded from the pool. The verify-by-resolution discipline
catches typos, non-existent DOIs, and miscited papers.

### Phase 4 — Per-section drafting

The orchestrator runs drafting prompts in IMRAD order:

1. **Methods** (`methods.v1`) — grounded in `methods_provenance.md`
   (extracted from notebook AST). Every protocol statement traces to
   a specific notebook + cell.
2. **Results** (`results.v1`) — grounded in `REPORT.md`. Every
   number must be grep-verifiable in the source. Figure and table
   embedding happens here.
3. **Discussion** (`discussion.v1`) — interpretive claims grounded
   in Results + bibliography. Includes Limitations subsection.
4. **Introduction** (`introduction.v1`) — written after Discussion
   so it can reference what the paper actually says.
5. **Abstract** (`abstract.v1`) — written last. Every claim must
   exist in a body section. Structured subsections (Background,
   Methods, Results, Conclusions) with sentence-count caps.

Each prompt enforces the skill's fabrication discipline: every
factual claim must trace to a canonical project source, verified
bibliography entry, or explicit metadata. No LLM training knowledge
allowed as a source.

Wall clock: ~10–25 min on Opus depending on `--depth` and tier.

### Phase 5 — Validation + assembly

The orchestrator runs `validate_manuscript.py` (M1–M10 mechanized
checks), concatenates sections into `manuscript.md`, and runs
post-checkers (figures manifest, tables manifest, scope coherence).

### Phase 6 — Ensemble review + rewrite loop

Three independent fallback reviews run in parallel (v0.7.0 ensemble).
Findings are deduplicated by section + textual overlap and scored by
agreement (3/3, 2/3 → routed to rewrite loop; 1/3 → advisory only).
The bounded rewrite loop (up to 2 passes per SPEC §8.3) dispatches
`rewrite.v1` per affected section with parallel candidates.

### Phase 7 — Final output

The pipeline writes `p0_findings.md` (P0-gate findings and proceed
options), updates `state.json` with final cost and status, and
pauses. Run
`beril-paper-writer assemble <draft_dir>` to generate the Word
document.

---

## 9. Paper-writer's specific role

### Two-tools-two-purposes (with adversarial)

| | paper-writer's `fallback_reviewer.v1.md` | beril-adversarial (canonical) |
|---|---|---|
| Detection classes | 3 (overclaim, citation rigor, scope alignment) | 10 |
| Time per review | ~30 seconds | 5–10 minutes |
| Cost per review | ~$0.05 | ~$0.50–$1 |
| When to use | In-loop revision triage; fast convergence | Pre-ship audit; thorough scientific critique |
| Where it lives | This skill (paper-writer) | `beril-adversarial` skill |

The fallback reviewer is for in-loop revision: paper-writer drafts a
section, fallback flags obvious issues, paper-writer revises, repeat.
The canonical adversarial is what you run before sending the draft to
coauthors.

### What it isn't

- **Not a typesetter.** Output is generic IMRAD markdown + basic
  .docx. No LaTeX, no journal-specific formatting.
- **Not a citation manager.** Consumes a curated reference list from
  the project; doesn't search PubMed independently.
- **Not a figure generator.** Consumes existing figures from the
  project's `figures/` directory. Missing figures become
  `analysis_requests`.
- **Not a peer reviewer.** The fallback reviewer is light triage;
  the canonical adversarial is the actual audit.
- **Not infallible.** The fabrication discipline catches most issues,
  but some will slip through. Always read the manuscript critically.

---

## 10. Cross-skill integration

### Consumes from

- **BERDL project artifacts:** `REPORT.md`, `RESEARCH_PLAN.md`,
  notebooks, figures. These are the manuscript's ground truth.
- **`beril-adversarial`** (optional but recommended): the canonical
  adversarial reviewer runs **in-pipeline** at Tier 3 of `phase_review`,
  producing `audit/adversarial_review.{md,json}`. If the CLI is absent
  the pipeline falls back to an inline reviewer (loud warning; D-051).

### Produces for

- **Human reviewers / coauthors:** `manuscript.md` and
  `manuscript.docx` are for humans.
- **`beril-adversarial` (canonical):** the per-draft directory layout
  is what `beril-adversarial review --type paper` reads. See
  [`CONTRACT.md`](CONTRACT.md) for the exact required-input contract.

### Consumer-side smoke test

Per the recurring cross-skill drift pattern
(see `feedback_cross_skill_contract_drift.md`), paper-writer should
verify that its output directory layout is readable by the canonical
adversarial reviewer:

```python
# tests/integration/test_adversarial_interop.py
import json, subprocess

def test_adversarial_review_against_synthetic_draft(tmp_path):
    # Create a synthetic paper-writer per-draft directory
    # ... populate with manuscript.md, 00_throughline.md,
    #     references.md, citation_map.md, REPORT.md ...
    result = subprocess.run(
        ["beril-adversarial", "review", "--type", "paper",
         str(draft_dir), "--beril-root", str(tmp_path)],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode in (0, 2)
    audit_json = draft_dir / "audit" / "adversarial_review.json"
    assert audit_json.is_file()
    doc = json.loads(audit_json.read_text())
    assert doc["schema_version"] == "adversarial-review-paper.v3"
```

### Adversarial schema compatibility

Paper-writer v1.0 accepts adversarial schema v2 and v3. The v3
schema renamed `narrative_weakness` → `central_objection` and added
`citation_reality`. See [`CONTRACT.md`](CONTRACT.md) for the full
severity vocabulary mapping and class enum.

---

## 11. Troubleshooting

### "REPORT.md required for plan phase"

Your project hasn't been synthesized yet. Run `/synthesize` in a
BERIL session first.

### "claude: command not found"

The Claude CLI isn't on your PATH. On the hub, check that
`~/.local/bin` is in your PATH. Run `beril-paper-writer configure`
to diagnose.

### "PYTHON_BIN: command not found"

The orchestrator can't find a Python with the required deps. The
script auto-resolves the pipx venv's Python via the
`beril-paper-writer` CLI's shebang. If `which beril-paper-writer`
shows nothing, re-run `pipx install --force`.

### "BERIL_ROOT does not contain .claude/skills/"

Either pass `--beril-root <path>` explicitly, set `$BERIL_ROOT` env
var, or `cd` into BERIL_ROOT before invoking.

### Pipeline halts mid-run

State is saved in `state.json`. Run
`beril-paper-writer continue <draft_dir>` to resume. Phases are
idempotent — re-running a completed phase is a no-op.

### Cost exceeded limit

The `--max-cost-usd` cap triggered a halt. State is saved. Increase
the limit and `continue`.

### Figures not appearing in manuscript

Check `p0_findings.md` for figure-manifest warnings. Figures must be
in the project's `figures/` directory with filenames matching
`fig<N>_<name>.<ext>`.

### Wrong project_id detected

The agent inferred from your git branch but you wanted a different
project. Pass the project_id explicitly — explicit arguments always
win over branch / cwd inference.

### Adversarial review captured argparse stderr

If `beril-adversarial` is installed but at a pre-v0.6.0 version,
the CLI shape is different and the orchestrator may capture argparse
usage text as the "review file." Fix: upgrade to
`beril-adversarial` v0.7.0+.

### Abstract sentence-count warnings

The M2 validator emits `soft-warning` when abstract subsections
exceed sentence caps (Background 3, Methods 3, Results 4,
Conclusions 3). These are advisory — the pipeline does not halt.
The rewrite loop will attempt to tighten if a reviewer flags the
abstract.

---

## 12. Where to read more

- **[`README.md`](README.md)** — repo overview, quick-start, status.
- **[`TUTORIAL.md`](TUTORIAL.md)** — step-by-step user guide.
- **[`HUB_INSTALL.md`](HUB_INSTALL.md)** — operator runbook for
  JupyterHub.
- **[`CONFIGURE.md`](CONFIGURE.md)** — configuration deep-dive.
- **[`CONTRACT.md`](CONTRACT.md)** — consumer-side interop contract
  with beril-adversarial.
- **[`SPEC.md`](SPEC.md)** — design rationale.
- **[`LAYOUT.md`](LAYOUT.md)** — internal architecture and CLI
  details.
- **[`DECISIONS.md`](DECISIONS.md)** — running log of design
  decisions.
- **[`CONTRIBUTION.md`](CONTRIBUTION.md)** — how to contribute
  prompt improvements, validators, and fixes.
- **Per-phase prompt files** —
  `src/beril_paper_writer/skill/prompts/*.v1.md`.
- **Validator source** —
  `src/beril_paper_writer/skill/tools/validate_manuscript.py`.

---

## Document version

This guide tracks `beril-paper-writer-skill v1.0.0`. Update at every
minor release; refresh examples and counts at every major.
