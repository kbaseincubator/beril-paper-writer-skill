# beril-paper-writer — Plugin Guide [SKELETON / DRAFT]

> **STATUS:** Skeleton template adapted from `beril-adversarial`'s `PLUGIN_GUIDE.md`. Sections marked `[FILL: ...]` need paper-writer-specific content from someone who knows this skill in depth. Once filled in and reviewed, rename to `PLUGIN_GUIDE.md` and remove this banner. Common-pattern sections (install, deploy, configure) are pre-filled with paper-writer wording but should be sanity-checked against the actual repo state.

End-to-end guide to installing, configuring, testing, and operating the `beril-paper-writer` skill within a BERIL deployment.

> **Audience.** Researchers using BERIL on the JupyterHub or a local fork who want to draft scientific manuscripts from a BERDL project; integrators wiring this skill into automated pipelines; operators deploying it on shared infrastructure.

> **Skill version.** This guide tracks `beril-paper-writer-skill v0.7.1` [VERIFY]. For the changelog, see [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

---

## Table of contents

1. [What this skill does and where it fits in BERIL](#1-what-this-skill-does-and-where-it-fits-in-beril)
2. [3-minute orientation](#2-3-minute-orientation)
3. [Installation](#3-installation)
4. [Skill deployment into BERIL](#4-skill-deployment-into-beril)
5. [Configuration](#5-configuration)
6. [Testing the skill](#6-testing-the-skill)
7. [Operation inside BERIL workflow](#7-operation-inside-beril-workflow)
8. [The drafting pipeline (per-phase modes)](#8-the-drafting-pipeline)
9. [Paper-writer's specific role](#9-paper-writers-specific-role)
10. [Cross-skill integration](#10-cross-skill-integration)
11. [Troubleshooting](#11-troubleshooting)
12. [Where to read more](#12-where-to-read-more)

---

## 1. What this skill does and where it fits in BERIL

[FILL: 2–3 paragraphs covering paper-writer's mission. Suggested content based on memory:

`beril-paper-writer` drafts ICMJE-conformant scientific manuscripts from a completed BERDL analysis project. Given a project containing `REPORT.md`, `RESEARCH_PLAN.md`, notebooks, and figures, the skill produces a per-draft directory (`papers/draft_N/`) containing the assembled manuscript, supporting metadata files (throughline, references, citation map, reframing log, methods provenance, figure inventory), and embeddable assets.

The skill was rebuilt at v0.6.0 around a per-draft directory layout that makes drafts independent + iteratable; v0.7.x introduced dual-reviewer architecture (fallback for in-loop revision triage; canonical adversarial for pre-ship audit); v0.5.x tightened caption quality with boilerplate-aware sufficiency gates and panel-count-scaled budgets.

Position in BERIL lifecycle: after analyses are complete and REPORT.md is stable; before adversarial review for pre-submission polish.]

**Position in the BERIL lifecycle:**

```
Research Plan ──► Notebooks ──► REPORT.md ──► [paper-writer] ──► papers/draft_N/manuscript.md
                                                       │
                                                       ▼
                                       fallback_reviewer (in-loop, ~30s)
                                                       │
                                                       ▼
                                       /beril-adversarial review --type paper
                                                  (pre-ship audit, 5-10min)
```

---

## 2. 3-minute orientation

[FILL: most-common-use-case in 5–10 lines covering the typical paper-writer entrypoint. Suggested:

```bash
# In your shell, at BERIL_ROOT, on a project branch:
git checkout projects/my_project_id

# In Claude Code, single-command entry point:
/beril-paper-writer
# OR via shell orchestrator:
bash .claude/skills/beril-paper-writer/tools/paper_writer.sh my_project_id [flags]
```

The skill walks the full pipeline: throughline selection → citation pool curation → per-section drafting (introduction, methods, results, discussion, abstract, limitations) → manuscript assembly → optional fallback review → manifest writing. Output lands at `projects/my_project_id/papers/draft_<N>/`.]

For everything else read on.

---

## 3. Installation

### Prerequisites

- **Python 3.10 or newer**
- **`pipx`** for isolated installation. Install with `python3 -m pip install --user pipx && python3 -m pipx ensurepath` if missing. PEP 668-locked installs may need `--break-system-packages`.
- **`claude` CLI** on PATH. The skill shells out to `claude -p` for drafting and review. `which claude` must return a path.
- **`bash`** (any modern version)
- [FILL: any paper-writer-specific deps — e.g., python-docx for assembly? Check pyproject.toml]

### Install from GitHub

```bash
pipx install --force git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
```

### Install from a wheel

```bash
pipx install --force /path/to/beril_paper_writer_skill-VERSION-py3-none-any.whl
```

### Verify

```bash
beril-paper-writer --version    # should print "beril-paper-writer-skill VERSION" [VERIFY CLI surface name]
beril-paper-writer --help
```

### Updating

```bash
pipx upgrade beril-paper-writer-skill
```

After every update: **re-run `beril-paper-writer install-skill <BERIL_ROOT>`** to refresh deployed skill files.

---

## 4. Skill deployment into BERIL

```bash
cd /path/to/your/beril-fork
beril-paper-writer install-skill .
```

### What gets deployed

```
<BERIL_ROOT>/.claude/skills/beril-paper-writer/
├── SKILL.md
├── commands/                    [FILL: list slash commands shipped]
├── prompts/                     # 10+ versioned phase prompts
│   ├── throughline.v1.md
│   ├── citation_pool.v1.md
│   ├── introduction.v1.md
│   ├── methods.v1.md
│   ├── results.v1.md
│   ├── discussion.v1.md
│   ├── abstract.v1.md
│   ├── limitations.v1.md
│   ├── references.v1.md
│   ├── manuscript.v1.md
│   ├── fallback_reviewer.v1.md  # in-loop reviewer (3 classes)
│   └── ...
├── references/
├── tools/                       # orchestrator + helper scripts
│   ├── paper_writer.sh
│   └── ...
└── state/                       # preserved across re-installs
```

### Idempotency and state

`install-skill` is idempotent. The `state/` directory is preserved. Per-draft directories under `<BERIL_ROOT>/projects/<id>/papers/` are NOT touched by install-skill — they're project artifacts, not skill artifacts.

### Verify

```bash
beril-paper-writer install-skill <BERIL_ROOT>
beril-paper-writer configure
```

The configure command [FILL: describe what configure verifies — claude detection, deps, prompts present, etc.]

---

## 5. Configuration

### Required: `claude` CLI

[FILL: paper-writer's claude-CLI dependency — does it use stream-json? Does it have model defaults? Per memory entries, default is claude-sonnet-4-6. Verify.]

### Optional: model override

```bash
beril-paper-writer review --type paper <draft> --model claude-opus-4-x [VERIFY syntax]
```

### Optional: tier and mode parameters

[FILL: tier (STRONG/THIN/EXPLORATORY?) and any mode-shape parameters. Check the actual paper_writer.sh for the flag list.]

### Per-project config

[FILL: does paper-writer use per-project config? The current memory suggests no, but verify in the actual orchestrator.]

---

## 6. Testing the skill

### Unit tests

```bash
git clone https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
cd beril-paper-writer-skill
pip install -e ".[dev]"
pytest tests/ -v
```

[FILL: expected test count. Per memory: "430 unit tests pass" at v0.5; verify current.]

### Cross-skill integration smoke

[FILL: paper-writer's own smoke test — what does it test? Per memory:
- citation_pool.v1 smoke caught seed-bibliography typos (5 errors in fdm)
- Live-test on real projects

If paper-writer has a `tests/integration/test_adversarial_interop.py` or similar (per the cross-skill drift memory recommending a consumer-side smoke), document it. If it doesn't, this is the place to flag adding one as v0.7.1+ work.]

### Live test against a real BERDL project

```bash
beril-paper-writer my_project_id [flags] [VERIFY syntax]
```

[FILL: cost estimate per memory: ~$5–7 for full pipeline; verify. List what to verify after run: draft directory exists, manuscript.md non-empty, throughline + citation_pool + reframing_log + methods_provenance all present, fallback review attached if --review flag used.]

---

## 7. Operation inside BERIL workflow

### Two surfaces

| | Slash command | Python CLI subcommand / Shell orchestrator |
|---|---|---|
| Invocation | `/beril-paper-writer` | `bash paper_writer.sh` OR `beril-paper-writer ...` [VERIFY which] |
| Best for | Interactive use | Programmatic / scripted |

[FILL: the slash command vs CLI surface diagram. Match adversarial's structure. Important: per memory entry feedback_verify_cli_before_recommending — verify the actual CLI shape paper-writer exposes; don't guess.]

### Project resolution

[FILL: paper-writer's project resolution mechanism. Does it use the same 4-signal tree (explicit arg / git branch / cwd / ask user)? Memory entries suggest cwd-based detection is in play; branch detection may not be implemented yet. If branch detection is missing, this is a v0.7.1 alignment item — same fix that adversarial v0.7.0.1 made.]

### Draft auto-detection / next-N

[FILL: paper-writer creates new drafts with auto-numbered draft_N. Document the numbering logic — how it picks the next N, whether it errors on existing drafts, whether it has a --resume mode for picking up a partial draft.]

---

## 8. The drafting pipeline

[FILL: this is the section that most diverges from adversarial. Paper-writer has a phased pipeline rather than a mode-selection matrix. Suggested structure:]

The skill executes a **phased drafting pipeline** when invoked with no specific phase flag:

### Phase 1 — Throughline selection

[FILL: throughline.v1.md prompt; produces `00_throughline.md` with chosen narrative spine + evidence map. User review gate after generation? Auto-advance flag?]

### Phase 2 — Citation pool curation

[FILL: citation_pool.v1.md; resolves seed bibliography against actual literature; per memory, smoke test caught 5 typos in functional_dark_matter project's curated references.md.]

### Phase 3 — Per-section drafting

[FILL: introduction → methods → results → discussion → abstract → limitations. Each phase has its own .v1.md prompt. Phases produce per-section .md files.]

### Phase 4 — Manuscript assembly

[FILL: manuscript.v1.md assembles per-section files into manuscript.md. Figure embedding loop (per v0.3 memory). Caption quality (per v0.5 memory).]

### Phase 5 — Optional in-loop review (fallback_reviewer)

[FILL: fallback_reviewer.v1.md — 3 detection classes, ~30s, in-loop revision triage. NOT the canonical adversarial; that's a separate skill.]

### Repair / revision modes

[FILL: REPAIR_MODE per v0.2 memory; rewrite loop. Document when these fire and how the user invokes.]

### Skipping phases / resume

[FILL: --skip-throughline, --skip-citation-pool, etc.? --resume from a partial draft? Document what's available.]

---

## 9. Paper-writer's specific role

### Two-tools-two-purposes (with adversarial)

| | paper-writer's `fallback_reviewer.v1.md` | beril-adversarial (canonical) |
|---|---|---|
| Detection classes | 3 | 10 |
| Time per review | ~30 seconds | 5–10 minutes |
| Cost per review | ~$0.05 | ~$0.50–$1 |
| When to use | In-loop revision triage; fast convergence | Pre-ship audit; thorough scientific critique |
| Where it lives | This skill (paper-writer) | `beril-adversarial` skill |

The fallback reviewer is for in-loop revision: paper-writer drafts a section, fallback flags obvious issues, paper-writer revises, repeat. The canonical adversarial is what you run before sending the draft to coauthors — see `beril-adversarial`'s [`PLUGIN_GUIDE.md`](../beril-adversarial-skill-draft/PLUGIN_GUIDE.md) §9.

### What it isn't

[FILL: explicit non-goals. Suggested:
- Not a typesetter (no LaTeX, no Word styling beyond basic markdown)
- Not a citation manager (consumes a curated reference list; doesn't search PubMed)
- Not a figure generator (consumes existing figures from the project)
- Not a peer reviewer (fallback reviewer is light triage; canonical adversarial is the actual audit)]

---

## 10. Cross-skill integration

### Consumes from

- **BERDL project artifacts**: `REPORT.md`, `RESEARCH_PLAN.md`, notebooks, figures. Reads these to draft.
- **`beril-atlas`** (potentially): if atlas has scanned the project and surfaced insights, paper-writer can use those as inputs. [VERIFY: is this integration live?]

### Produces for

- **Human reviewers / coauthors**: the assembled `manuscript.md` is for humans.
- **`beril-adversarial` (canonical)**: paper-writer's per-draft directory layout (`papers/draft_N/manuscript.md` + `00_throughline.md` + `references.md` + `citation_map.md` + `reframing_log.md` + `methods_provenance.md` + `figures_inventory.md` + `tables_inventory.md`) is what `beril-adversarial review --type paper` reads. **Read [`CONTRACT.md`](../beril-adversarial-skill-draft/CONTRACT.md) §"Paper review interop" in the adversarial repo for the exact required-input contract.**

### Cross-skill smoke test (recommended)

[FILL: per memory feedback_cross_skill_contract_drift.md — paper-writer should add a consumer-side smoke test asserting that `beril-adversarial review --type paper` succeeds against a synthetic paper-writer draft. The producer-side test on adversarial's side covers shape; the consumer-side test catches CLI invocation drift. Sample test:

```python
# tests/integration/test_adversarial_interop.py
import subprocess, json
def test_adversarial_canonical_review_against_synthetic_draft(tmp_path):
    # Create a synthetic paper-writer per-draft directory
    draft_dir = tmp_path / "papers" / "draft_1"
    # ... populate with manuscript.md + throughline + references + citation_map + REPORT.md ...
    result = subprocess.run(
        ["beril-adversarial", "review", "--type", "paper", str(draft_dir),
         "--beril-root", str(tmp_path)],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode in (0, 2), f"adversarial failed: {result.stderr}"
    audit_json = draft_dir / "audit" / "adversarial_review.json"
    assert audit_json.is_file()
    doc = json.loads(audit_json.read_text())
    assert doc["schema_version"] == "adversarial-review-paper.v3"
    assert isinstance(doc.get("findings"), list)
```

This is the consumer-side mitigation for the recurring drift pattern. The previous incident was paper_writer.sh 0.6.3 calling pre-v0.6.0 adversarial CLI shape and capturing argparse stderr as the "review file"; this smoke would have caught it.]

### Adversarial v0.7.0 migration

If you're updating paper-writer to consume `beril-adversarial` v0.7.0+, you must:

1. Update class enum dispatch: `narrative_weakness` → `central_objection`. Optionally accept BOTH for one transition release.
2. Audit `--output` flag usage. v0.7.0 honors it for paper mode; pre-v0.7.0 it was silently ignored.
3. Add the smoke test above.

See [`CONTRACT.md`](../beril-adversarial-skill-draft/CONTRACT.md) §"v0.7.0 migration" at the top.

---

## 11. Troubleshooting

[FILL: paper-writer-specific troubleshooting. Likely topics based on memory:
- `paper_writer.sh: <something>: unbound variable` — set -u violations
- "discover_python_bin" pattern when bash orchestrator can't find pipx venv python
- Citation_pool resolution failures (URLs unreachable, DOIs malformed)
- Figure embedding failures (figure file paths mismatch)
- REPAIR_MODE didn't trigger when expected
- Auto-numbering picks wrong N (existing draft conflict)
- Caption quality regression after v0.5 (panel-count budgets)
- Adversarial review captured argparse stderr as the "review file" — mitigated by v0.7.0 migration hint, but document the symptom + fix
]

---

## 12. Where to read more

- **[`README.md`](README.md)** — repo overview, quick-start examples
- **[`RELEASE_NOTES.md`](RELEASE_NOTES.md)** — v0.x.x changelog with migration notes
- **[`SKILL.md`](src/beril_paper_writer/skill/SKILL.md)** [VERIFY path] — deployed skill documentation
- **Per-phase prompt files** — `src/beril_paper_writer/skill/prompts/*.v1.md` [VERIFY path]
- **`beril-adversarial`'s [`CONTRACT.md`](../beril-adversarial-skill-draft/CONTRACT.md)** — interop contract for consuming the canonical adversarial reviewer

---

## Document version

This guide tracks `beril-paper-writer-skill v0.7.1` [VERIFY]. Update at every minor release.
