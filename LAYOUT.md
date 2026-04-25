# beril-paper-writer-skill — package layout + CLI structure

**Date:** 2026-04-25
**Status:** v0.1 specification. No code yet. Implementation pending sign-off
on this layout.

This document specifies the shape of `ArkinLaboratory/beril-paper-writer-skill`.
The skill mirrors `beril-adversarial-skill-draft`'s pipx-installable, ships-
the-skill-as-package-data pattern. Read [SPEC.md](SPEC.md) first for *what*
the skill does and *why*; this document is *how* it's packaged.

## Repository tree (planned)

```
ArkinLaboratory/beril-paper-writer-skill/
├── pyproject.toml           hatchling build, zero runtime deps
├── README.md, LICENSE, .gitignore, .gitattributes
├── SPEC.md, LAYOUT.md, DECISIONS.md
├── reference/
│   ├── reporting-standards-extract.md
│   └── prior-art-scan.md
├── src/beril_paper_writer/
│   ├── __init__.py          __version__
│   ├── cli.py               argparse entry: install-skill, configure,
│   │                        continue, assemble
│   ├── discovery.py         BERIL_ROOT resolution (atlas/adversarial pattern)
│   ├── state.py             state.json schema + read/write/diff helpers
│   ├── commands/
│   │   ├── install_skill.py    copies skill/ via importlib.resources
│   │   ├── configure.py        claude-on-PATH; optional beril-adversarial check
│   │   ├── continue_run.py     resume a paused draft
│   │   └── assemble.py         markdown → docx via pandoc
│   └── skill/               ships as package_data → .claude/skills/beril-paper-writer/
│       ├── SKILL.md
│       ├── commands/        slash-command markdowns (.md per CLI verb)
│       ├── tools/
│       │   ├── paper_writer.sh        the orchestrator (planned ~1000 lines)
│       │   ├── stream_progress.py     reused parser pattern from adversarial
│       │   ├── extract_methods.py     notebook AST walker for Methods grounding
│       │   ├── extract_figures.py     figure selection + caption extraction
│       │   ├── citation_pool.py       literature-pool builder + verifier
│       │   ├── validate_manuscript.py M1–M10 mechanized checks
│       │   └── assemble_docx.py       pandoc wrapper for the final pass
│       ├── prompts/
│       │   ├── plan.v1.md             Plan-phase: triage + throughline candidates
│       │   ├── methods.v1.md          Methods extraction (notebook-grounded)
│       │   ├── results.v1.md          Results section
│       │   ├── discussion.v1.md       Discussion section
│       │   ├── introduction.v1.md     Introduction (written after Disc)
│       │   ├── abstract.v1.md         Abstract (written last)
│       │   ├── citation_pool.v1.md    Literature scan + 9-field verification
│       │   ├── reframer.v1.md         Detect drift from REPORT, log honestly
│       │   ├── fallback_reviewer.v1.md  Inline reviewer if beril-adversarial absent
│       │   └── rewrite.v1.md          Apply review-driven fixes to a section
│       └── references/
│           └── reporting-standards-checklist.md  M-tier validators in detail
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_discovery.py
    │   ├── test_install_skill.py
    │   ├── test_state_diff.py            intercalation hash-diff logic
    │   ├── test_validate_manuscript.py   M1–M10 validators
    │   └── test_extract_methods.py       notebook AST walker
    └── integration/
        ├── __init__.py
        ├── conftest.py                   fixture project (small synthetic)
        ├── fixtures/
        │   └── synthetic_project/        a deliberately small project for tests
        │       ├── RESEARCH_PLAN.md
        │       ├── REPORT.md
        │       ├── notebooks/
        │       │   └── 01_demo.ipynb
        │       └── figures/
        │           └── fig01_demo.png
        └── test_full_run.py              end-to-end with stubbed claude
```

## What ships vs. what runs

**Ships in the package (static, versioned):**
- Shell orchestrator `tools/paper_writer.sh`
- Python helpers under `tools/` (extract_methods, extract_figures,
  citation_pool, validate_manuscript, assemble_docx, stream_progress)
- 10 versioned `.v1.md` system prompts under `prompts/`
- Reference rubric `references/reporting-standards-checklist.md`
- SKILL.md and slash command markdowns

**Runs at draft time (dynamic):**
- `claude -p` subprocess for each per-section agent (Plan, Methods,
  Results, Discussion, Introduction, Abstract, Reframer, Citation Pool)
- `python3` helper invocations for:
  - notebook AST extraction (Methods grounding)
  - figure selection from project's `figures/` dir
  - citation DOI/PMID verification (also calls `WebSearch` via claude)
  - M1–M10 validators
  - hash-diff against `state.json` on `continue`
- `pandoc` for `.md` → `.docx` (only at `assemble` step)

Nothing about *what* the manuscript says is hardcoded in Python. The
Python layer is install + configure + state-diff + validators + assembly.
Manuscript content = shell + prompts + claude subprocess + project artifacts.

## CLI

```
beril-paper-writer install-skill [<BERIL_ROOT>] [--force]
beril-paper-writer configure
beril-paper-writer continue <draft_dir> [options]
beril-paper-writer assemble <draft_dir> [--format docx|pdf|md]
```

Exit codes (mirrors adversarial): `0` success / `1` user error / `2` runtime / `3` config.

`install-skill` copies `skill/` into
`<BERIL_ROOT>/.claude/skills/beril-paper-writer/` via `importlib.resources`.
Preserves install-local `state/` (skill-level memory across drafts; e.g.,
"learned-patterns from prior writers"). Sets +x on `tools/*.sh` and
`tools/*.py` after copy.

`configure` verifies `claude` is on PATH and reports whether
`beril-adversarial` is also available (if not, the fallback reviewer will be
used; the user is warned at run time, not at configure time).

`continue` is the resume-after-pause subcommand. Reads `state.json`,
hash-diffs source artifacts, reports new/changed files to user, then
proceeds with whatever phase was paused (throughline-pick, gap-fill response,
review-rewrite acceptance).

`assemble` is the markdown → docx step. Runs final M1–M10 validators,
concatenates intermediate files in IMRAD order, calls pandoc, reports
validator pass/fail summary.

## Slash commands

```
/beril-paper-writer [<project_id>] [--mode paper|report]
                    [--throughline auto|interactive]
                    [--depth quick|standard|deep]
                    [--no-adversarial] [--no-stream]
                    [--max-rewrites N] [--max-gap-rounds N]

/beril-paper-writer-continue <draft_dir>
/beril-paper-writer-assemble <draft_dir> [--format docx|pdf|md]
```

`<project_id>` auto-detects from cwd if inside `projects/<id>/`, matching
the `/berdl-review`, `/submit`, `/beril-adversarial` pattern.

**Defaults:**
- `--mode` is tier-driven (STRONG/THIN → `paper`; EXPLORATORY → `report`)
  per SPEC §3.2, overridable
- `--throughline interactive` (the load-bearing user gate)
- `--depth standard` (~15–25 min; quick is ~5–10, deep is ~30–50)
- `--max-rewrites 2` (hard cap from SPEC §8.3)
- `--max-gap-rounds 2` (hard cap from SPEC §5.3)
- Adversarial review ON by default; `--no-adversarial` falls back to
  inline reviewer

## Output routing

Each invocation creates `papers/draft_N/` under the project directory.
`N` increments from existing draft directories. Drafts are immutable
within a directory (re-run with `continue` modifies in place; new
invocation creates `draft_{N+1}/`).

```
projects/<project_id>/papers/draft_N/
├── state.json                  ← stop/resume state, hashes, choices
├── manuscript.md               ← assembled draft (regenerated on each pass)
├── 00_throughline.md           ← chosen throughline + evidence map
├── 01_methods.md
├── 02_results.md
├── 03_discussion.md
├── 04_introduction.md
├── 05_abstract.md
├── 06_limitations.md
├── references.md               ← human-readable, numbered
├── bibliography.bib            ← machine-readable (BibTeX)
├── citation_map.md             ← claim → reference index
├── methods_provenance.md       ← Methods statements ↔ notebook+cell
├── reframing_log.md            ← deviations from REPORT.md (auditable)
├── analysis_requests.md        ← gap-fill requests, statuses
├── throughline_candidates.md   ← rejected alternatives, kept for audit
├── figures/                    ← curated subset of project figures
│   └── (symlinks or copies of project figures, renamed for paper order)
├── reviews/                    ← if beril-adversarial run
│   └── draft_N_review_M.md
├── audit/                      ← per-call streaming logs, costs
│   ├── plan.stream.log
│   ├── methods.stream.log
│   └── ...
└── manuscript.docx             ← only after `assemble` is invoked
```

## state.json schema (informal)

```json
{
  "version": "0.1",
  "project_id": "functional_dark_matter",
  "draft_number": 1,
  "phase": "throughline_pick | drafting | review | assembled",
  "throughline": {
    "candidate_id": "TL2",
    "chosen_at": "2026-04-25T14:32:00Z",
    "revision": 0,
    "artifact_hash_at_confirmation": "<sha256 of all source-artifact hashes concatenated, sorted by path>",
    "reevaluations": [
      {
        "round": 1,
        "at": "2026-04-25T15:10:00Z",
        "artifact_change_detected": true,
        "changed_paths": ["REPORT.md", "notebooks/02_X.ipynb"],
        "user_prompt_shown": "Throughline TL2 may need re-evaluation given new evidence in REPORT.md §3.2; re-evaluate?",
        "outcome": "confirmed-still-valid | re-picked-as-TL3 | abandoned"
      }
    ]
  },
  "source_artifacts": [
    {"path": "REPORT.md", "sha256": "...", "mtime": 1714000000.0},
    {"path": "notebooks/01_integration_census.ipynb", "sha256": "...", "mtime": ...}
  ],
  "manuscript_files": [
    {"path": "01_methods.md", "sha256": "...", "writer_generated": true},
    {"path": "02_results.md", "sha256": "...", "writer_generated": true,
     "user_edited": false}
  ],
  "analysis_requests": [
    {"id": "REQ-1", "type": "analysis-request", "status": "pending",
     "originated_at_iteration": 1, "beril_command_suggestion": "/berdl ..."}
  ],
  "iteration": {"rewrite_passes": 0, "gap_fill_rounds": 0},
  "cost_so_far_usd": 3.42,
  "elapsed_seconds": 1240,
  "validator_status": {"M1": "pass", "M2": "pass", ...}
}
```

## Path resolution

User prompts pass **absolute paths** for the Write target (lesson learned
from beril-adversarial — relative paths sometimes nest under unexpected
bases). Each per-section subagent gets the absolute path of the file it
should write.

`paper_writer.sh` derives BERIL_ROOT from its install path (symlink-safe
via `pwd -P`) and `cd`'s there before invoking claude. Same pattern as
adversarial.

## Stream-json parser + retry

Reuses the pattern from beril-adversarial:

- `tools/stream_progress.py` (cleanly forked from adversarial; same
  programmatic Write-tool verification + cost summary + sidecar log)
- Per-section calls go through `invoke_claude_with_retry` (max 3 attempts)
- Exit 2 → retry with escalated prompt prefix; exit 3 → hard fail with
  `mv` recovery hint; other non-zero → hard fail with diagnostic

Stream logs are preserved per-section under `audit/<section>.stream.log`
for post-mortem.

## Coupling to beril-adversarial

**Loose coupling (preferred and confirmed):**

The writer shells out to `beril-adversarial` as an installed sibling
skill:

```bash
# Inside paper_writer.sh's review phase
beril-adversarial-cli --type paper "$DRAFT_DIR" 2>&1 | tee "$REVIEW_LOG"
```

Or via the slash-command path if the writer runs inside Claude Code:

```
/beril-adversarial --type paper <project_id>
```

`configure` warns at install time if `beril-adversarial` is not on PATH.
At run time, if invocation fails with "not found," the writer falls back
to the inline `prompts/fallback_reviewer.v1.md` and emits a clear stderr
warning.

**Why loose:** version-drift is bounded (each skill versioned independently);
neither is a runtime dependency of the other; users can pin versions; the
writer can be tested in isolation.

## bibliography.bib (resolving the format-mismatch)

The adversarial reviewer's paper-mode prompt expects `papers/bibliography.bib`
and `papers/citation-map.md`. v0.1 of the writer produces both:

- `references.md` — human-readable, numbered in order of first citation in
  manuscript
- `bibliography.bib` — BibTeX entries (one per reference), keyed by
  Author-Year-FirstWord
- `citation_map.md` — markdown table mapping every in-prose citation
  number to its BibTeX key and the manuscript section/paragraph

This is not "vendor formatting" — it's structured serialization of data the
writer has to track anyway for verification. It also gives users a
journal-ready BibTeX export with no extra effort.

## Reviewer memory (learned-patterns)

`<BERIL_ROOT>/.claude/skills/beril-paper-writer/state/learned-patterns.md`

Cross-project meta-memory of writing patterns the writer should remember.
Same convention as beril-adversarial's learned-patterns. Examples:

- "Projects whose REPORT.md uses 'Act I/Act II' framing are usually
  THIN-tier; check for 'Act II deferred' before drafting."
- "When notebooks use scipy.stats.fisher_exact without explicit alpha,
  Methods must state alpha=0.05 (default)."

Read at start of each Plan phase; appended to at end if a novel pattern
emerged. Install-local; never shipped.

## BERIL_ROOT discovery

`discovery.py` resolves BERIL_ROOT identically to beril-adversarial's
`discovery.py` (intentionally — single source of truth pattern):

1. `--beril-root <path>` flag
2. `BERIL_ROOT` environment variable
3. Walk up from cwd looking for `.env` + `.claude/skills/` + at least
   one BERIL-core skill (`submit/`, `berdl/`, `suggest-research/`)
4. Fail loud with diagnostic naming which marker failed

May literally vendor `discovery.py` from beril-adversarial in v0.1; merge
to a shared dependency post-MVP if drift becomes an issue.

## Cross-platform

Python 3.10+. `pathlib.Path` everywhere. Bash 3.2-compatible (macOS
default), confirmed by `bash -n` syntax check. `.gitattributes` enforces
LF endings on `.sh`/`.py`/`.md`/`.toml`/`.bib`. Pandoc is required for
`assemble` (degraded gracefully — `assemble` errors with a clear
install-pandoc message if not on PATH).

Windows users run under WSL or Git Bash; PowerShell parity not promised.

## Tests (planned)

Initial target: ~25 tests across unit + integration. Modeled on
beril-adversarial's 29-test suite.

- `test_discovery.py` — BERIL_ROOT resolution (vendored from adversarial)
- `test_install_skill.py` — copy + executable-bit + state preservation
- `test_state_diff.py` — hash-diff logic for intercalation; user-edit
  detection; throughline-affecting-change detection
- `test_validate_manuscript.py` — M1–M10 validators (each + edge cases)
- `test_extract_methods.py` — notebook AST walker; statistical-test
  detection; package-version extraction
- `test_full_run.py` — integration test with a small synthetic project
  fixture and a stubbed claude (no live LLM in CI)

Live-LLM tests not in CI (cost + brittleness). Fixture project lives at
`tests/integration/conftest.py`.

## Cost / latency targets (mirrors SPEC §11)

| Phase | Wall clock | Tokens | Notes |
|---|---|---|---|
| Plan + Triage | 1–3 min | ~30K input, ~5K output | reads project, classifies |
| Throughline candidates | 2–5 min | ~50K, ~10K | extracts evidence maps |
| Citation pool build | 3–8 min | ~80K, ~5K | literature scan + verification |
| Methods extraction | 2–4 min | ~80K, ~3K | notebook AST + prose |
| Per-section drafting (×5) | 8–15 min | ~250K, ~25K | parallel where possible |
| Validation pass | <1 min | <1K, <1K | mechanical |
| Adversarial review | 8–14 min | (separate skill cost, ~$1–2) | |
| 1 rewrite pass | 5–10 min | ~150K, ~10K | targeted to flagged sections |
| Assembly | <1 min | 0, 0 | pandoc only |
| **Total (default)** | **30–60 min** | **~640K input, ~60K output** | **~$5–$15 + adversarial** |

If approaching 2× upper bound on either dimension, fail loud with
checkpoint + user prompt to continue. Cost summary in
`audit/cost-summary.md` at end.

## Deliverables this document blocks

1. Repo init: `gh repo create ArkinLaboratory/beril-paper-writer-skill --private --clone`
2. Initial commit + tag `v0.1.0-spec` (spec only, no code)
3. After spec sign-off: implementation begins per LAYOUT
4. After live-test signoff: tag `v0.1.0` (full release)

## Open questions for revisit

1. **Pandoc dependency.** Should we ship a pure-Python docx writer
   (python-docx) instead and drop pandoc? Trade-off: pandoc is more
   capable (citation rendering, math, tables); python-docx is one less
   system dependency.
2. **Citation pool re-use across drafts.** A project's draft_2 should
   probably inherit draft_1's verified citation pool. Implementation
   detail; can punt to v0.2.
3. **Per-section parallel execution.** Methods + Results can be drafted
   in parallel (both need notebooks but not each other). Saves wall-clock;
   adds orchestration complexity. v1 is sequential.
4. **Pre-built prompt-corpus size.** The 10 prompts will probably total
   ~3000 lines. Larger than adversarial's 2150. May need a "prompt-
   compression" pass before release if individual subagent calls hit
   context-window pressure.
