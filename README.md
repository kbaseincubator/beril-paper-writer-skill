# beril-paper-writer-skill

Drafts ICMJE-conformant scientific manuscripts from BERDL analysis
projects. Takes a finished project (research plan, report, notebooks,
figures, references) and produces a complete IMRAD draft with citation
verification, figure/table embedding, adversarial review, and iterative
revision.

Distributed as a Claude Code skill that runs inside a
[BERIL](https://github.com/kbaseincubator/BERIL-research-observatory)
deployment. Sister skills:
[beril-adversarial](https://github.com/ArkinLaboratory/beril-adversarial-skill)
(harsh review),
[beril-presentation-maker](https://github.com/ArkinLaboratory/beril-presentation-maker-skill)
(scientific presentations),
[beril-atlas](https://github.com/ArkinLaboratory/beril-atlas-skill)
(corpus observability).

## Status

**v0.7.1** — production pipeline. 720 tests. Live-tested on two
BERDL projects (functional_dark_matter, genotype_to_phenotype_enigma).

## What it does

1. Reads project artifacts (REPORT.md, RESEARCH_PLAN.md, notebooks,
   figures, references); classifies project quality as STRONG / THIN /
   EXPLORATORY.
2. Extracts 2–3 candidate scientific throughlines with evidence maps.
   **The user picks** which narrative arc to pursue.
3. Identifies evidence gaps; emits structured `analysis_requests.md`
   with BERIL slash-command suggestions for gap-filling.
4. Drafts the manuscript section-by-section in IMRAD order (Methods →
   Results → Discussion → Introduction → Abstract). Methods are
   extracted from notebooks and code, not hallucinated.
5. Verifies all citations against a pre-built pool (DOI / PMID lookup);
   refuses citations outside the pool.
6. Runs adversarial review (via
   [beril-adversarial](https://github.com/ArkinLaboratory/beril-adversarial-skill)
   or a built-in fallback reviewer).
7. Applies up to 2 review-driven rewrite passes with parallel
   candidates; remaining issues surface in `next_actions.md`.
8. Assembles markdown intermediates into a single `.docx` with inline
   figures and tables.

The skill **pauses** at user-decision points and resumes via
`beril-paper-writer continue <draft_dir>`. State persists on disk in
`papers/draft_N/state.json`.

## Install

```bash
pipx install git+https://github.com/ArkinLaboratory/beril-paper-writer-skill.git
cd <BERIL_ROOT>
beril-paper-writer install-skill .
beril-paper-writer configure   # verify claude CLI + dependencies
```

## Usage

### CLI

```bash
# Start a new draft
beril-paper-writer draft <project_id> [--mode paper|report]
                                      [--depth quick|standard|deep]
                                      [--model <model_id>]
                                      [--no-adversarial]
                                      [--max-cost-usd N]

# Resume after throughline pick
beril-paper-writer continue <draft_dir> --pick TL1

# Assemble to Word document
beril-paper-writer assemble <draft_dir>
```

### Slash commands (inside Claude / BERIL)

```
/beril-paper-writer [<project_id>] [--mode paper|report]
                    [--depth quick|standard|deep]

/beril-paper-writer-continue <draft_dir> --pick TL1
```

`--mode paper` (default for STRONG/THIN tiers) produces an IMRAD
research paper. `--mode report` (default for EXPLORATORY tier)
produces a structured activity report. `<project_id>` auto-detects
from cwd if you're inside `projects/<id>/`.

## What it produces

```
projects/<project_id>/papers/draft_1/
├── state.json                  ← stop / resume state
├── manuscript.md               ← assembled draft (markdown)
├── manuscript.docx             ← Word document (from assemble)
├── next_actions.md             ← remaining issues checklist
├── 00_throughline.md           ← chosen throughline + evidence map
├── 01_methods.md               ← individual IMRAD sections
├── 02_results.md
├── 03_discussion.md
├── 04_introduction.md
├── 05_abstract.md
├── 06_limitations.md
├── 07_data_availability.md
├── references.md               ← numbered reference list
├── citation_map.md             ← claim → reference index
├── figures_manifest.tsv        ← figure metadata
├── tables_manifest.tsv         ← table metadata
├── reframing_log.md            ← deviations from REPORT.md
├── analysis_requests.md        ← gap-fill requests for BERIL
└── reviews/                    ← adversarial review reports
```

Each invocation creates a new numbered draft directory. Manuscripts
are versioned, not edited in place.

## How it fits into the BERIL workflow

```
/berdl_start → (iterate within session) → /synthesize → REPORT.md
     │
     ▼
/beril-adversarial               harsh project review
     │
     ▼
/beril-paper-writer              draft manuscript
     │
     ▼
user picks throughline → beril-paper-writer continue --pick TL1
     │
     ▼
(automatic: drafting → review → rewrite → assembly)
     │
     ▼
beril-paper-writer assemble      → manuscript.docx
```

## Costs

| Depth | Typical cost | Wall clock |
|---|---|---|
| `quick` | $5–15 | 5–10 min |
| `standard` | $15–50 | 15–25 min |
| `deep` | $30–80 | 30–50 min |

Use `--max-cost-usd N` to set a hard ceiling. Use `--no-adversarial`
to skip the review-rewrite loop (saves ~$5–15).

## Caveats

- Reuses existing project figures only — no figure regeneration.
  Missing figures become `analysis_requests`.
- No journal-specific formatting. Output is generic IMRAD .docx.
- Declines to write the manuscript on insufficient-evidence projects;
  reports what's missing instead.
- AI-disclosure paragraph is auto-emitted per ICMJE January 2026
  guidance. Author list, funding, conflicts, and ethics statements
  are placeholders the user must fill before submission.

## Documentation

- [TUTORIAL.md](TUTORIAL.md) — step-by-step guide for new users
- [SPEC.md](SPEC.md) — design rationale
- [LAYOUT.md](LAYOUT.md) — internal architecture and CLI details
- [DECISIONS.md](DECISIONS.md) — running log of design decisions
- [CONTRACT.md](CONTRACT.md) — cross-skill interop with beril-adversarial

## License

MIT
