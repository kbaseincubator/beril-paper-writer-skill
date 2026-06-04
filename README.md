# beril-paper-writer-skill

Drafts ICMJE-conformant scientific manuscripts from BERIL analysis
projects. Takes a finished project (research plan, report, notebooks,
figures, references) and produces a complete IMRAD draft with verified
citations, embedded figures, adversarial review, and iterative
revision — without fabricating evidence.

Distributed as a Claude Code skill that runs inside a
[BERIL](https://github.com/kbaseincubator/BERIL-research-observatory)
deployment. Sister skills:
[beril-adversarial](https://github.com/kbaseincubator/beril-adversarial-skill)
(harsh review),
[beril-presentation-maker](https://github.com/kbaseincubator/beril-presentation-maker-skill)
(scientific presentations),
[beril-atlas](https://github.com/ArkinLaboratory/beril-atlas-skill)
(corpus observability).

## Status

**v1.0.0** — first stable release. Validated by the Stage 7 v1-MVP
campaign: six BERIL projects (3 dev + 3 blind holdout) run end-to-end
and scored against the v1-bar v2b success criteria. The pipeline
reached the review measurement point on 6/6, resolved 100% of claim
markers on 6/6, and stayed within the $10/draft cost budget on 6/6;
3/3 dev and 2/3 holdout passed the full bar.

v1.0 ships **with documented known limits** — see `RELEASE_NOTES.md`
and `V1_X_BACKLOG.md`. The notable ones: #46 (the drafter can pull
predicted values from `RESEARCH_PLAN.md` into Results as if measured —
this was the single holdout failure, and it is loudly detected, not
silent) and #48 (the deterministic Tier-1 check table is partially
implemented; figure/table-callout and language-quality checks are
deferred to v1.1). See `STAGED_IMPROVEMENT_PLAN.md` for the full
build history. Run `pytest` from the repo root to confirm the suite.

## What it does

1. Reads project artifacts (REPORT.md, RESEARCH_PLAN.md, notebooks,
   figures, references); classifies project quality as STRONG / THIN /
   EXPLORATORY.
2. Extracts 2–3 candidate scientific throughlines with evidence maps and
   weakness inventories. **The user picks** which narrative arc to
   pursue — the load-bearing decision the skill refuses to make for you.
3. Identifies evidence gaps; emits structured `analysis_requests.md` with
   BERIL slash-command suggestions for gap-filling.
4. Builds a verified citation pool: every entry has a DOI or PMID that
   resolves. The drafter is forbidden from inventing citations outside
   the pool; missing-citation needs surface as `[NEEDS CITATION: <topic>]`
   markers, resolved in a separate WebSearch round.
5. Drafts the manuscript in a **single holistic Opus pass** —
   one LLM call produces the entire IMRAD manuscript (Abstract,
   Introduction, Methods, Results, Discussion, References as internal
   sections of `manuscript.md`), with prompt-level discipline against
   fabricating numbers, citations, sample sizes, or tool versions.
   Methods are *extracted from notebooks*, not generated freely.
6. Runs a three-tier review cascade:
   - **Tier 1** — deterministic checks (numeric grounding against
     `claim_inventory.tsv` + REPORT.md; claim-marker resolution).
   - **Tier 2** — Haiku light review.
   - **Tier 3** — canonical adversarial review via
     [beril-adversarial](https://github.com/kbaseincubator/beril-adversarial-skill).
     Falls back to an inline reviewer with a loud warning if the canonical
     CLI isn't installed.
7. Applies a **subtraction-only optimizer**: it can remove unbacked claims
   or convert citations to `[NEEDS CITATION]` markers, but it cannot
   invent new numbers or evidence.
8. Resolves the new `[NEEDS CITATION]` markers via WebSearch, appending
   verified entries to the pool.
9. Runs a compliance gate (ICMJE checks: AI-disclosure, Data Availability,
   etc.) with an autofix pass.
10. Assembles markdown → `.docx` with figures embedded inline. The
    canonical figures dir is auto-staged next to the manuscript so the
    renderer's relative-path contract resolves.

The skill **pauses** at the throughline-pick gate and resumes via
`beril-paper-writer continue <draft_dir> --pick TLN`. State persists on
disk in `papers/draft_N/state.json`.

## Install

```bash
# 1. Install the skill (clean install — do NOT use --editable)
pipx install git+https://github.com/kbaseincubator/beril-paper-writer-skill.git

# 2. Verify
beril-paper-writer --version           # 1.0.0
beril-paper-writer configure           # confirms claude + beril-adversarial paths + Python deps

# 3. Deploy slash-command + skill files into BERIL_ROOT
cd <BERIL_ROOT>
beril-paper-writer install-skill .
```

Two environment overrides if `claude` or `beril-adversarial` isn't on PATH
where the orchestrator can find them:

```bash
export BERIL_CLAUDE_BIN=/path/to/claude              # required CLI
export BERIL_ADVERSARIAL_BIN=/path/to/beril-adversarial  # optional, recommended
```

The orchestrator resolves both to absolute paths at init — `claude` must
resolve or it fails loud; `beril-adversarial` is optional but a missing one
triggers a loud warning at startup and a degraded review at Tier 3.

### Running the test suite

The pipx venv has all runtime deps. Inject pytest once at setup:

```bash
pipx inject beril-paper-writer-skill pytest
PYBIN=$(pipx environment --value PIPX_LOCAL_VENVS)/beril-paper-writer-skill/bin/python
PYTHONPATH=src $PYBIN -m pytest tests/unit -q   # suite should pass clean
```

Do NOT run from system Python — it won't have nbformat / python-docx.

## Usage

### CLI

```bash
# Start a new draft. Default model is Opus 4.6 for reasoning phases.
beril-paper-writer draft <project_id> [--mode paper|report]
                                      [--depth quick|standard|deep]
                                      [--model <model_id>]
                                      [--no-adversarial]
                                      [--max-cost-usd N]
                                      [--recaption]

# Resume after picking a throughline (the load-bearing user gate)
beril-paper-writer continue <draft_dir> --pick TL1
                                        [--revision "tweak text"]
                                        [--remediate]
                                        [--max-remediate-cycles N]
                                        [--no-adversarial]

# Re-render the manuscript without re-running the pipeline
beril-paper-writer assemble <draft_dir>
```

`--remediate` is opt-in: by default `continue` pauses at the P0 gate
(`phase=p0_review`) and the operator decides whether to remediate.
With `--remediate`, the pipeline runs up to `--max-remediate-cycles`
remediation rounds (default 2) before pausing. See CONFIGURE.md for
the complete flag reference.

`--no-adversarial` is an explicit opt-out from the canonical
beril-adversarial reviewer; the inline fallback runs instead with no
warning (user has chosen). Without the flag, the canonical reviewer is
the default; if it's not installed, the orchestrator falls back with a
loud warning.

### Slash commands (inside Claude Code / BERIL)

```
/beril-paper-writer [<project_id>] [--mode paper|report]
                    [--depth quick|standard|deep]

/beril-paper-writer-continue <draft_dir> --pick TL1
```

`--mode paper` (default for STRONG/THIN tiers) produces an IMRAD research
paper. `--mode report` (default for EXPLORATORY tier) produces a
structured activity report. `<project_id>` may be a bare id (auto-resolved
to `<cwd>/projects/<id>/`) or a full path.

## What it produces

```
projects/<project_id>/papers/draft_N/
├── state.json                  ← stop / resume state; phase + cost
├── manuscript.md               ← assembled draft (markdown, single file)
├── manuscript.docx             ← Word document with figures embedded
├── figures/                    ← symlink → <project>/figures/ (auto-staged)
├── 00_throughline.md           ← chosen throughline + evidence map
├── citation_pool.json          ← verified DOI/PMID pool used by the draft
├── references.md               ← rendered bibliography
├── citation_map.md             ← claim → reference index
├── methods_provenance.md       ← extracted-from-notebooks methods
├── claim_inventory.tsv         ← every numeric claim mapped to source notebook
├── throughline_candidates.md   ← what plan.v1 produced before the user picked
├── compliance_errors.json      ← ICMJE compliance-gate flags (if any)
└── audit/
    ├── plan.metadata.json              ← per-phase telemetry
    ├── adversarial_review.{md,json}    ← Tier 3 canonical review (or .md fallback)
    ├── claim_inventory_validation.json ← source-notebook resolution + repairs
    ├── optimizer_subtraction_check.json ← post-check: optimizer didn't fabricate
    ├── optimization_applied.md         ← what the optimizer changed
    ├── review_mode.json                ← which Tier-3 reviewer ran (canonical / fallback / failed)
    ├── extract_{methods,figures,tables}.log
    └── assemble_docx.log
```

Each invocation creates a new numbered draft directory. Manuscripts are
versioned, not edited in place.

The post-Stage-3 manuscript is a single `manuscript.md` produced by the
holistic drafter (one Opus pass). There are no per-section markdown files
(`01_methods.md`, `02_results.md`, etc.) — that was the sectional flow used
through v0.7.x, since superseded.

## How it fits into the BERIL workflow

```
/berdl_start → (iterate within session) → /synthesize → REPORT.md
     │
     ▼
/beril-adversarial               harsh project review (recommended)
     │
     ▼
/beril-paper-writer              draft manuscript — pauses at throughline pick
     │
     ▼
user picks throughline → beril-paper-writer continue --pick TL1
     │
     ▼
(automatic) citation_pool → holistic draft → review → optimize →
            supplementary citations → compliance gate → assemble
     │
     ▼
manuscript.docx + audit trail
```

## Costs

| Depth | Typical cost | Wall clock |
|---|---|---|
| `quick` | $5–15 | 5–10 min |
| `standard` | $15–50 | 15–30 min |
| `deep` | $30–80 | 30–50 min |

Set `--max-cost-usd N` for a soft serial ceiling (checked between LLM
calls). Use `--no-adversarial` to skip canonical review and save $5–15;
the inline fallback reviewer runs instead, but with reduced coverage —
3 finding classes vs the canonical reviewer's 10, no literature scan,
no biological-claim verification.

## Caveats

- Reuses existing project figures only — no figure regeneration. Missing
  figures become `analysis_requests`.
- No journal-specific formatting. Output is generic IMRAD .docx;
  journal templating is post-MVP.
- Declines to write the manuscript on insufficient-evidence projects;
  reports what's missing in `analysis_requests.md`.
- AI-disclosure paragraph is auto-emitted per ICMJE January 2026
  guidance (see SPEC.md Appendix A). Author list, funding, conflicts,
  and ethics statements are placeholders the user must fill before
  submission.
- The canonical adversarial reviewer is `beril-adversarial`; if you opt
  out (`--no-adversarial`) or it's missing, the inline fallback runs but
  the manuscript carries findings the canonical reviewer would have
  caught. Check `audit/review_mode.json` to know which reviewer ran.

## Documentation map

| Doc | Audience | Content |
|---|---|---|
| [TUTORIAL.md](TUTORIAL.md) | Users | Step-by-step drafting guide (install, configure, draft, throughline selection, output reading, iteration) |
| [HUB_INSTALL.md](HUB_INSTALL.md) | Operators | JupyterHub deployment runbook (install, configure, smoke test, troubleshooting) |
| [CONFIGURE.md](CONFIGURE.md) | Users / Operators | CLI flags, env vars, cost controls, model selection, tier/mode |
| [CONTRACT.md](CONTRACT.md) | Integrators | Cross-skill interop surface (beril-adversarial schema versions, severity mapping; the `audit/review_mode.json` consumer contract) |
| [CONTRIBUTION.md](CONTRIBUTION.md) | Contributors | How to contribute (prompt improvements, validator extensions, orchestrator fixes) |
| [SPEC.md](SPEC.md) | Developers | Foundation (mission, scope, throughline mechanism) + v0.8 pipeline architecture + ICMJE appendix |
| [LAYOUT.md](LAYOUT.md) | Developers | Internal architecture, CLI details, runtime contracts |
| [DECISIONS.md](DECISIONS.md) | Developers | Running log of design decisions with rationale (D-001+) |
| [STAGED_IMPROVEMENT_PLAN.md](STAGED_IMPROVEMENT_PLAN.md) | Developers | Active plan-of-record; Stage 1/2/3 closure tables |
| [PLUGIN_GUIDE.md](PLUGIN_GUIDE.md) | Reference | Comprehensive single-artifact guide (retained as reference) |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | All | The v1.0 release story + known limits |
| [release-notes/](release-notes/) | Reference | Per-version release notes (v0_1 through v0_6) |
| [PARTICIPANT-RUNBOOK](https://github.com/kbaseincubator/beril-presentation-maker-skill/blob/main/docs/cross-skill/PARTICIPANT-RUNBOOK.md) | Participants | Cross-skill runbook covering all 4 BERIL skills end-to-end |

## License

MIT
