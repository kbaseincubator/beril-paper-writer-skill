# beril-paper-writer-skill — package layout + CLI structure

> **Version notice (v0.7.1):** This document was written at v0.1 and is
> incrementally updated as features land. Sections marked "planned" may
> already be implemented; sections without version annotations reflect
> the original v0.1 design. For the current implementation state, see
> DECISIONS.md.

**Date:** 2026-04-25 (originated); updated through v0.7.1.
**Status:** Living document. Updated incrementally as features ship.

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
│   │   └── assemble.py         markdown → docx via python-docx
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
│       │   └── assemble_docx.py       markdown→docx via python-docx (no pandoc)
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
- `python-docx` for `.md` → `.docx` (only at `assemble` step). Pure
  Python, no system pandoc binary needed (D-024).

Nothing about *what* the manuscript says is hardcoded in Python. The
Python layer is install + configure + state-diff + validators + assembly.
Manuscript content = shell + prompts + claude subprocess + project artifacts.

## CLI

```
beril-paper-writer draft <project_id> [--mode paper|report]
                                      [--depth quick|standard|deep]
                                      [--model <model_id>]
                                      [--no-adversarial]
                                      [--max-cost-usd N]
beril-paper-writer install-skill [<BERIL_ROOT>] [--force]
beril-paper-writer configure [--beril-root <path>]
beril-paper-writer continue <draft_dir> --pick <TL_ID> [--revision <note>]
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
concatenates intermediate files in IMRAD order, walks the markdown via
a small converter on top of `python-docx`, reports
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
├── 06_limitations.md           ← extracted by assembler from 03_discussion.md
├── 07_data_availability.md     ← ICMJE-required (M4); orchestrator emits from template
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

### File-ownership notes for the per-section model

Two files in the layout above are **not** written by their section-
labeled drafting prompt:

- **`07_data_availability.md`** — orchestrator emits from a template
  (`reference/data_availability_template.md`, planned), pre-filled
  with project-specific metadata (K-BERDL database name and
  snapshot date if available, code-repo URL, public-accession list
  from `RESEARCH_PLAN.md`). This is metadata-driven boilerplate, not
  a synthesis task that needs LLM judgment. Same pattern as the
  `AI_DISCLOSURE_TEMPLATE` that `methods.v1` consumes verbatim.
- **`06_limitations.md`** — `assemble` extracts the Limitations
  subsection from `03_discussion.md` and writes it to
  `06_limitations.md` as well, so M9 finds it at a top-level
  position in the assembled view. The Limitations content lives
  once (in Discussion's prose); the split is a serialization
  concern. `discussion.v1` does NOT write `06_limitations.md`
  itself.

## Orchestrator capabilities (what `paper_writer.sh` must provide)

The prompts assume an orchestrator (`paper_writer.sh`, planned in
Phase 4) that exposes the following capabilities. None of these are
called from inside a prompt; the orchestrator runs them between
prompt invocations.

### Extract-tool invocation

Before any drafting prompt that depends on extracted facts, the
orchestrator runs the corresponding Phase 2 extractor:

- **`extract_methods.py`** — invocation:
  `python3 <SKILL_TOOLS_DIR>/extract_methods.py <PROJECT_ROOT> --output-dir <DRAFT_DIR>`
  Produces `<DRAFT_DIR>/methods_provenance.md` (consumed by
  `methods.v1`). On exit code 1 (no notebooks) → halt the run with
  the error verbatim. On exit code 2 (some notebooks failed parse)
  → log the per-notebook failures, proceed (the script continues
  with the notebooks that did parse).
- **`extract_figures.py`** — invocation:
  `python3 <SKILL_TOOLS_DIR>/extract_figures.py <PROJECT_ROOT> --output-dir <DRAFT_DIR>`
  Produces `<DRAFT_DIR>/figures_inventory.md` (consumed by
  `results.v1`). Empty figures directory is not an error; the
  inventory will be empty and `results.v1` proceeds without figure
  callouts.

### Template loading and placeholder filling

Two orchestrator-loaded templates are passed verbatim to drafting
prompts:

- **`AI_DISCLOSURE_TEMPLATE`** — read from
  `<SKILL_REFERENCE_DIR>/ai_disclosure_template.md` (the reference
  text per SPEC §10.1). Orchestrator fills placeholders `{X.Y}`
  (skill version), `{model_id}` (e.g. `claude-sonnet-4-20250514`),
  `{project_id}` (from the project directory), `{sha}` (snapshot
  hash from `state.json`'s `source_artifacts`), and `{N}` (rewrite
  pass count from `state.json.iteration.rewrite_passes`). The filled
  string is passed to `methods.v1` as the `AI_DISCLOSURE_TEMPLATE`
  parameter.
- **`data_availability_template.md`** — read from
  `<SKILL_REFERENCE_DIR>/data_availability_template.md` (planned;
  see "BLOCKED in v0.1" note in the validator dispatch table for
  M4). Orchestrator fills with project-specific metadata (K-BERDL
  database name and snapshot date if available, code-repo URL,
  public-accession list from `RESEARCH_PLAN.md`) and writes the
  filled result to `<DRAFT_DIR>/07_data_availability.md`. No prompt
  consumes this template directly.

Placeholder syntax is `{name}` (single-brace, no escape). Template
files use this notation throughout.

### Citation-pool formatter step

After `citation_pool.v1` writes `pool.json` and the schema validator
passes, the orchestrator runs the formatter:

`python3 <SKILL_TOOLS_DIR>/citation_pool.py format <DRAFT_DIR>/pool.json <DRAFT_DIR>`

This produces `<DRAFT_DIR>/references.md`,
`<DRAFT_DIR>/bibliography.bib`, and `<DRAFT_DIR>/citation_map.md`.
`discussion.v1` reads `references.md` (per its `REFERENCES_MD_PATH`
input); the BibTeX is consumed by the adversarial reviewer and at
assembly. **The prompt does NOT invoke this formatter itself** — the
prompt's job ends with `pool.json`, and the orchestrator owns the
format step.

### Validator invocation and result dispatch

After all section drafts complete, the orchestrator runs:

`python3 <SKILL_TOOLS_DIR>/validate_manuscript.py <DRAFT_DIR> --mode <MODE> --output <DRAFT_DIR>/audit/validation.json`

The output JSON has shape `ValidationReport` (per
`validate_manuscript.py`'s dataclass). The orchestrator parses
failures, partitions by validator, and dispatches each named-
validator failure to the relevant section prompt in REPAIR_MODE
(see "REPAIR_MODE" below + "Validator → section dispatch table").

### Figure copy/symlink logic

After `results.v1` returns its closing message naming the K
selected figures (with `paper-order names` like
`fig01_<descriptive>.png`), the orchestrator copies or symlinks
those figures from `<PROJECT_ROOT>/figures/` to
`<DRAFT_DIR>/figures/`. v0.1 default is **copy** (avoids broken
symlinks if the project directory moves); symlink is opt-in via a
configure flag. On copy failure (permission, disk space), halt the
run with the OS error verbatim.

### Reframing-log initialization

Multiple prompts append to `<DRAFT_DIR>/reframing_log.md`. On
first-write into a fresh `draft_N/` directory, the orchestrator
creates the file with a `# Reframing Log` header and a blank line
before any prompt is invoked. Prompts assume the file exists and is
readable; the empty-file case is handled per each prompt's escape
hatches.

## Per-section prompt invocation contract

The orchestrator (`paper_writer.sh`) invokes each section prompt as
a `claude -p` subagent. Two invocation modes:

### Drafting mode (default)

The section prompt is invoked with the full input set (per its
`Inputs the user prompt will pass` section). The prompt drafts the
section, runs its own self-review checklist, writes the output via
the `Write` tool, and emits a one-line closing message.

**The section prompt does NOT invoke the manuscript-level
validators (`validate_manuscript.py` M1–M10).** M1 (IMRAD sections
present) cannot pass on a partial draft; per-section invocation
generates spurious failures. The orchestrator runs the validator
once after all sections are drafted, before the adversarial-review
loop and again at `assemble`.

The exception is `citation_pool.v1`, which runs a *schema*
validation (`citation_pool.py validate`) on its own output —
that's `pool.json`'s structural integrity, not manuscript-level
M1–M10.

### REPAIR_MODE

After running `validate_manuscript.py` and finding failures, the
orchestrator dispatches each failure to the relevant section prompt
in REPAIR_MODE. The section prompt receives **all of its original
drafting-mode inputs** (THROUGHLINE_PATH, REPORT_PATH, RESULTS_PATH,
POOL_JSON_PATH, METHODS_PROVENANCE_PATH, etc.) **plus** the four
REPAIR_MODE-specific fields:

- `REPAIR_MODE` — `"true"`.
- `NAMED_VALIDATOR` — one of `M1`...`M10`. Identifies which
  validator's failure to repair.
- `VALIDATOR_OUTPUT_PATH` — file containing the validator's
  structured failure detail. Format is the JSON shape produced by
  `validate_manuscript.py --output <file>` (a `ValidationReport`
  object per its dataclass), filtered to the single named validator
  via the orchestrator before invoking the prompt. The prompt reads
  the JSON and identifies the specific span(s) to repair.
- `REPAIR_TARGET_PATH` — the section file to modify (e.g.
  `01_methods.md`).

The drafting-mode inputs are necessary because the prompt must read
the existing section, understand what NOT to change (claims that
already pass other validators, content scoped to the throughline,
etc.), and fix only the named span. A REPAIR_MODE invocation without
the drafting-mode inputs would force the prompt to either
re-generate from scratch (forbidden) or guess at scope (worse).

The section prompt's REPAIR_MODE behavior:

1. Read the validator failure detail in `VALIDATOR_OUTPUT_PATH`.
2. **Multiple violations in one invocation:** if the JSON contains
   multiple violations from the same validator, fix each in turn
   within the bounded-retry budget below. Do not split into multiple
   prompt invocations; the orchestrator's responsibility is one
   validator's failures per dispatch.
3. Fix only the named spans. Do not regenerate the section, do not
   introduce new claims, do not delete grounded claims that the
   validator did not flag.
4. Re-write `REPAIR_TARGET_PATH`.
5. Bounded retry: up to 2 attempts per invocation. After the second
   failure on the same validator, halt with the closing message
   below; the orchestrator routes per the four escalation paths
   (auto-fix exhausted → user-modify, escalate-as-analysis-request,
   or accept-as-limitation).

When the prompt halts after exhausted attempts, its closing message
includes a **path recommendation** so the orchestrator (or the user)
has guidance:

```
Halted after 2 repair attempts on <NAMED_VALIDATOR>; recommended
next path: {user-modify | escalate-as-analysis-request |
accept-as-limitation}; rationale: <one-line>.
```

The recommended-path values come from SPEC §7.1.1's four escalation
paths (excluding auto-fix, which has already been exhausted). The
prompt picks based on the failure character: missing-data violations
(M6 multi-test correction without the underlying data) recommend
escalate-as-analysis-request; project-scope-limit violations (M7
effect-size missing because not computed) recommend
accept-as-limitation; user-judgment violations recommend
user-modify. The orchestrator may override; the recommendation is
guidance, not a directive.

On successful repair, the closing message is one line:
`"<REPAIR_TARGET_PATH> repaired for <NAMED_VALIDATOR>; <one-line
summary of the change>."` This goes back to the orchestrator, which
re-runs the validator and updates `state.json`'s `validator_status`
accordingly.

### Validator → section dispatch table

Which section prompt owns repairs for which validator:

| Validator | Section | Notes |
|---|---|---|
| M1 (IMRAD sections) | (assembler) | Missing-section failure → orchestrator invokes the missing section's drafting prompt in IMRAD-order (Methods → Results → Discussion → Introduction → Abstract per SPEC §6.1) with the current draft state. Multiple missing sections re-drafted sequentially, not in parallel. |
| M2 (structured abstract) | `abstract.v1` | |
| M3 (AI-disclosure) | `methods.v1` | Disclosure paragraph lives in Methods §"AI-Assisted Analysis" |
| M4 (data availability) | (orchestrator) | **BLOCKED in v0.1**: Repair requires `reference/data_availability_template.md`, which has not been written. Until the template lands, M4 failures produce a stub `07_data_availability.md` with `[DATA_AVAILABILITY: TBD — template not yet implemented]` and the validator escalates as user-modify. Implementation task: write the template before M4 repair logic is added. |
| M5 (software + version, soft) | `methods.v1` | Soft-warning per §7.1.2; user-modify or accept-as-limitation are valid paths |
| M6 (multi-test correction) | `methods.v1` | Often escalates to analysis-request (re-run analysis with correction) per §7.1.1 |
| M7 (n / effect size / CI / p) | `results.v1` | |
| M8 (counts before percentages) | `results.v1` | |
| M9 (limitations >150 chars) | `discussion.v1` | Repair expands the existing Limitations subsection; assembler then re-extracts to `06_limitations.md` |
| M10 (citation cross-ref) | `discussion.v1` (default) or `results.v1` | Tie-breaker rule: if an orphan citation appears in only one section, dispatch to that section. If it appears in multiple, dispatch to `discussion.v1` (the later section in drafting order) and the prompt verifies the citation is correct in `results.v1` as well, escalating as `analysis-request` if a conflict between sections cannot be resolved by reference-numbering alone. |

## Reframing-log entry-numbering contract

Multiple prompts append entries to `<DRAFT_DIR>/reframing_log.md`
during a single drafting run: `citation_pool.v1`, `methods.v1`,
`results.v1`, `discussion.v1`, `plan.v1`, `reframer.v1` (per
SPEC §5.6). Each prompt assigns a unique entry number to avoid
collisions in the append-only log.

**v0.1 entry-numbering rule (sequential execution):**

1. Each prompt reads the existing `reframing_log.md` before
   appending. (The orchestrator creates the file with a
   `# Reframing Log` header before any prompt runs; see Orchestrator
   capabilities.)
2. The prompt extracts the maximum entry number from existing
   entries via the regex
   `^## Entry (\d+) — `; if no entries exist, max_N = 0.
3. The prompt's first appended entry is `Entry max_N + 1`. If a
   prompt appends multiple entries in one invocation (e.g.,
   `reframer.v1` writing several drift-audit entries), they receive
   `max_N + 1`, `max_N + 2`, etc., in order.
4. After appending, the prompt re-writes the full file content.

This contract assumes **sequential execution**, which is the v0.1
default. SPEC §5.3 caps gap-fill rounds and §6.1 establishes a
strict drafting order; nothing in v0.1 runs prompts concurrently
against the same `draft_N/` directory.

If parallelization is ever introduced (Methods + Results in
parallel per LAYOUT's "Open questions" §3), entry-number
assignment must be coordinated by the orchestrator: either
pre-assign entry numbers before invoking prompts, or have prompts
append without numbering and let the orchestrator renumber on
assembly. Both options break the per-prompt simplicity of the
v0.1 contract; prefer staying sequential unless wall-clock costs
become prohibitive.

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
  "discussion": {
    "pool_exhaustion": {
      "needs_citation_count": 7,
      "options_offered": ["scope-down", "citation-request", "accept-as-limitation"],
      "user_choice": "scope-down",
      "decided_at": "2026-04-25T15:42:00Z",
      "note": "User opted to drop the [NEEDS CITATION] claims rather than spend a gap-fill round."
    }
  },
  "iteration": {"rewrite_passes": 0, "gap_fill_rounds": 0},
  "cost_so_far_usd": 3.42,
  "elapsed_seconds": 1240,
  "validator_status": {
    "M1": "pass",
    "M3": "pass",
    "M5": "soft-warning",
    "M6": "escalated",
    "M7": "user-fixed",
    "M9": "accepted-as-limitation"
  }
}
```

**`validator_status` enum** is per SPEC §7.1.1: one of `pass`,
`soft-warning` (M5 only, per §7.1.2), `escalated`,
`user-fixed`, `accepted-as-limitation`. The example above shows
all five values across different validators. M-tier labels (`M1`
... `M10`) match the SPEC §7.1 numbering.

**`discussion.pool_exhaustion`** is populated by the orchestrator
when `discussion.v1` surfaces non-zero `[NEEDS CITATION]`
placeholders. The user picks one of the three options on `continue`;
the choice is recorded here. `discussion.v1` does NOT write this
field directly — it only surfaces the count and recommended option
in its closing message; the orchestrator owns the field.

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

## Fabrication discipline (cross-prompt contract)

_Added v0.6.5. All drafting prompts must reference this definition._

"Fabrication" in this skill means any prose claim that cannot be traced
back to one of the three valid source categories below. The definition
is deliberately narrow: a plausible-sounding sentence that no source
backs is fabrication, even if the claim is likely true.

### Valid trace-back categories

Every factual claim, number, comparison, or mechanism assertion in the
manuscript MUST trace to exactly one of:

1. **Canonical project sources** — REPORT.md, notebook output cells,
   methods_provenance.md, figures_inventory.md, tables_inventory.md.
   The trace must be grep-verifiable: the number or claim must appear
   verbatim (or with trivially equivalent formatting) in the source.
   Paraphrased claims require an inline provenance note (e.g.,
   `[derived from REPORT §Finding 3]`).

2. **Verified bibliography** — entries in `references.md` /
   `bibliography.bib` that have passed citation_pool.v1's
   verify-by-resolution discipline (DOI/PMID confirmed via
   WebSearch). Claims attributed to bibliography entries must be
   supported by the cited work — attaching a real citation to an
   unsupported claim is fabrication (citation-claim mismatch).

3. **Explicit metadata** — information that is definitionally true
   by the manuscript's own construction: section structure, author
   lists, acknowledgments, data-availability statements referencing
   the project's own artifacts. This category does NOT include
   interpretive claims about the data.

### What is NOT a valid source

- LLM training knowledge (even if correct)
- Plausible inference from partial evidence ("the data suggests...")
  without a traceable source
- RESEARCH_PLAN.md (design intent, not results)
- Other papers' methods or results unless cited from the bibliography

### Per-prompt fabrication variants

Each prompt applies the three categories above to its section's
specific risk profile:

| Prompt | Primary risk | Discipline |
|---|---|---|
| `results.v1.md` | Invented numbers | Every number grep-traced to REPORT.md or notebook cell |
| `methods.v1.md` | Invented protocols | Every method traced to methods_provenance.md (notebook+cell) |
| `discussion.v1.md` | Mechanism fabrication | Interpretive claims grounded in Results + bibliography only |
| `introduction.v1.md` | Citation-claim mismatch | Background claims must cite verified bibliography entries |
| `abstract.v1.md` | Overclaim vs body | Every Abstract claim must exist (possibly condensed) in a body section |
| `caption` synthesis | Invented n-values | Quantitative figure descriptions traced to notebook output or REPORT |

### Failure handling

When a prompt cannot trace a claim to a valid source:

- **Drop the claim** and note the gap with `[DATA NOT AVAILABLE]` or
  `[METHOD UNCLEAR: see notebook X cell Y]`.
- **Never fill the gap with plausible text.** A gap marker is always
  preferable to a fabricated sentence.
- **Log the gap** in `analysis_requests.md` if the missing information
  could be obtained from a BERIL re-analysis.

### Relationship to adversarial review

The adversarial reviewer's `claim_evidence`, `unbacked_quantitative`,
`citation_reality`, and `report_drift` classes are the detection
counterparts of this discipline. This definition is prevention
(compile-time); adversarial review is detection (test-time). Both
are necessary — the reviewer catches what the prompts miss.

---

## Coupling to beril-adversarial

**Loose coupling (preferred and confirmed):**

The writer shells out to `beril-adversarial` as an installed sibling
skill:

```bash
# Inside paper_writer.sh's review phase
beril-adversarial review --type paper "$DRAFT_DIR" 2>&1 | tee "$REVIEW_LOG"
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
LF endings on `.sh`/`.py`/`.md`/`.toml`/`.bib`. The assemble step uses
`python-docx` (pure Python; bundled with the pipx install). No system
binaries required at any point — important for remote BERIL deployments
where `apt-get` / `brew` may not be available (D-024).

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
| Assembly | <1 min | 0, 0 | python-docx only (no LLM call) |
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

1. **Pandoc vs. python-docx.** RESOLVED 2026-04-25: python-docx (D-024).
   Reason: remote BERIL deployments may not have admin to install pandoc
   as a system binary; pipx + python-docx is fully self-contained.
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
