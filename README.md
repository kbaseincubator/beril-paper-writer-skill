# beril-paper-writer-skill

A scientific manuscript drafter for BERDL analysis projects. Takes a finished
project (research plan, report, notebooks, figures, references, optional
adversarial review) and produces an ICMJE-conformant manuscript draft, with
explicit handling of evidence gaps, intercalated calls back to BERIL for
additional analysis when needed, and iterative adversarial-review-driven
revision.

Distributed as a Claude Code skill that runs inside a BERIL deployment.
Sister skill to `/beril-adversarial` (harsh review) and `/beril-atlas`
(corpus metrics).

## Status

**v0.1 — specification only.** No code. The spec, layout, and decision log
are checked in for community review. Implementation begins after spec sign-off.

## What it does (one paragraph)

1. Reads the project artifacts; classifies project quality (strong / thin /
   exploratory).
2. Extracts 2–3 candidate scientific throughlines from the evidence and
   surfaces them to the user with an evidence map per candidate. **The user
   picks** (or `--auto-throughline` opts into the writer's choice).
3. Identifies evidence gaps the chosen throughline needs filled. Emits a
   structured `analysis_requests.md` with paste-ready BERIL slash-command
   suggestions. User takes / defers / drops each.
4. Drafts the manuscript section-by-section in IMRAD order (Methods → Results →
   Discussion → Introduction → Abstract). Each section is grounded in
   project artifacts; methods are extracted from notebooks and code, not
   hallucinated.
5. Verifies all citations against a pre-built pool (DOI / PMID lookup);
   refuses citations outside the pool.
6. Hands off to `/beril-adversarial --type paper` for review (or a fallback
   inline reviewer if `beril-adversarial` is not installed).
7. Applies up to 2 review-driven rewrite passes; remaining unfixable issues
   are folded into `Limitations` or `Next Steps`.
8. Optional final assembly step (`beril-paper-writer assemble`) renders the
   markdown intermediates into a single `.docx`.

The skill **pauses** at user-decision points and resumes via
`beril-paper-writer continue <draft_dir>`. State lives on disk in
`paper_draft_N/state.json`.

## Install (planned)

```bash
pipx install git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git
cd <BERIL_ROOT>
beril-paper-writer install-skill .
beril-paper-writer configure   # sanity-check claude + optional beril-adversarial
```

## Usage (planned)

```
/beril-paper-writer [<project_id>] [--mode paper|report]
                    [--throughline auto|interactive]
                    [--depth quick|standard|deep]
                    [--no-adversarial] [--no-stream]
                    [--max-rewrites N] [--max-gap-rounds N]

beril-paper-writer continue <draft_dir>
beril-paper-writer assemble <draft_dir>   # → manuscript.docx
```

`--mode paper` (default for STRONG/THIN tiers) produces an IMRAD research
paper. `--mode report` (default for EXPLORATORY tier) produces a structured
activity report (no claims-of-significance framing). Either mode can be
forced regardless of tier — see SPEC §3.2.

`<project_id>` auto-detects from cwd if you're inside `projects/<id>/`.

## What it produces

```
projects/<project_id>/
├── README.md, RESEARCH_PLAN.md, REPORT.md, REVIEW.md, ADVERSARIAL_REVIEW_*.md
├── papers/
│   ├── draft_1/
│   │   ├── state.json                  ← stop / resume state
│   │   ├── manuscript.md               ← assembled draft
│   │   ├── 00_throughline.md           ← chosen throughline + evidence map
│   │   ├── 01_methods.md, 02_results.md, 03_discussion.md,
│   │   │   04_introduction.md, 05_abstract.md, 06_limitations.md,
│   │   │   07_data_availability.md
│   │   ├── references.md               ← human-readable, numbered
│   │   ├── bibliography.bib            ← machine-readable (for reviewer)
│   │   ├── citation_map.md             ← claim → reference index
│   │   ├── figures/                    ← curated subset of project figures
│   │   ├── analysis_requests.md        ← gap-fill requests for user / BERIL
│   │   ├── reframing_log.md            ← deviations from REPORT.md (auditable)
│   │   ├── throughline_candidates.md   ← rejected alternatives
│   │   └── manuscript.docx             ← from `beril-paper-writer assemble`
│   ├── draft_2/                        ← next invocation creates new dir
│   └── …
```

Each invocation creates a new numbered draft directory. Manuscripts are
versioned, not edited in place.

## How it fits into the BERIL workflow

`/berdl_start` opens an analysis session. The user iterates on
RESEARCH_PLAN.md and notebooks within that session, calling BERIL skills
(`/berdl-query`, `/berdl-discover`, `/berdl-minio`, `/literature-review`,
etc.) as needed. There is no single "iterate" slash command — iteration
is the work of the session itself. `/synthesize` then produces REPORT.md.

```
  /berdl_start → (iterate within session) → /synthesize → REPORT.md
       │
       ▼
  /beril-adversarial               harsh project review
       │
       ▼
  /beril-paper-writer              draft manuscript ──┐
       │                                              │
       ▼                                              │
  user picks throughline;                             │
  defers/takes gap-fill requests              ◄───────┘
       │                                              │
       ▼                                              │
  (optional) user actions taken gap-fills via         │
  a fresh /berdl_start session — appending to         │
  RESEARCH_PLAN.md and re-iterating until new         │
  artifacts land in the project ─→ updated REPORT     │
       │                                              │
       ▼                                              │
  beril-paper-writer continue                         │
       │                                              │
       ▼                                              │
  /beril-adversarial --type paper  harsh paper review │
       │                                              │
       ▼                                              │
  beril-paper-writer continue (rewrite pass)  ◄───────┘
       │
       ▼
  beril-paper-writer assemble      → manuscript.docx
```

## Status caveats

- v1 reuses existing project figures only — no figure regeneration. Missing
  figures become explicit `analysis_requests`.
- v1 has no journal-specific formatting. Output is generic IMRAD .docx.
  Journal templates are post-MVP.
- v1 declines to write the manuscript on insufficient-evidence projects. It
  reports what's missing instead. See SPEC §6.
- AI-disclosure paragraph is auto-emitted per ICMJE January 2026 guidance.
  Author list, funding, conflicts, and ethics statements are placeholders
  the user must fill before submission.

## See also

- [SPEC.md](SPEC.md) — community-facing design rationale (the load-bearing doc)
- [LAYOUT.md](LAYOUT.md) — internal architecture, CLI, package shape
- [DECISIONS.md](DECISIONS.md) — running log of design decisions with dates
- [reference/](reference/) — supporting research: prior-art scan, reporting-standards extract

## License

MIT (planned).
