# beril-paper-writer-skill — release notes

**Current:** v0.8.0 + Stage 3 (closed 2026-05-17).

This file is the **cumulative current-version log** — a brief summary
of where the skill is, what shipped in the most recent stage, and
pointers to per-version detail. Per-version release notes live in
[`release-notes/`](release-notes/).

## v0.8.0 + Stage 3 — current state (2026-05-17)

**Status:** production. **965 unit tests pass.** Live-tested on
`ibd_phage_targeting`, `functional_dark_matter`,
`genotype_to_phenotype_enigma`.

### What's in the box

- **8-phase pipeline** (Python orchestrator):
  init → extract → triage → plan → throughline_pick (PAUSE) →
  citation_pool → drafting → review → optimize → supplementary_pool →
  compliance_gate → assemble → assembled.
- **Holistic drafter** — a single Opus pass produces the entire
  manuscript. The legacy sectional flow (per-section files
  `01_methods.md`, etc.) is gone; output is a single `manuscript.md`.
- **Three-tier review cascade** —
  Tier 1 (deterministic) + Tier 2 (Haiku light) +
  Tier 3 (canonical `beril-adversarial` with loud-warn fallback to
  `fallback_reviewer.v1` if not installed).
- **Subtraction-only optimizer** — can remove unbacked claims and
  insert `[NEEDS CITATION:]` markers, but cannot fabricate new
  numbers, citations, or evidence.
- **Verified citation pool** — every entry has a DOI or PMID that
  resolves; supplementary phase resolves new markers via WebSearch.
- **Figure embedding** — `manuscript.docx` ships with figures
  inline; the canonical `<project>/figures/` is symlinked into the
  draft dir before the renderer runs.
- **Compliance gate** — ICMJE checks (AI-disclosure, Data
  Availability, etc.) with autofix.

### Stage 3 (Tiers A–K, 2026-05-12 → 2026-05-17)

The post-v0.8 in-situ defects surfaced by the BERIL slash-command
runs on `ibd_phage_targeting`. Eleven tiers shipped:

| Tier | Fix |
|---|---|
| A + J.1 | Figure staging — `<project>/figures/` symlinked into `<draft_dir>/figures/` before assembly (empty pre-existing dirs are replaced; user-managed real dirs are preserved) |
| B | `holistic_draft.v1.md` pinned the bare image-block form (anti-pattern: `> ![...]` blockquote silently dropped by the renderer) |
| C | `supplementary_citations.v1.md` + `holistic_draft.v1.md` — citation-pool array key is `entries[]`, not `citations[]` |
| D | `state.tier` populated from `throughline_candidates.md` (was `None` on every Python-flow draft, defaulting downstream consumers to EXPLORATORY) |
| F | Slash-command markdown rewritten for v0.8 phase sequence (was describing the v0.6 sectional flow) |
| G | `phase_triage` LLM calls now pinned (`model=self.model`) and cost-tracked. Fixes the `source_notebook` regression trigger and closes a cost-tracking hole |
| H | `extract_claims.v1.md` — explicit exact-filename rule + worked counter-example for `source_notebook` |
| I | `validate_claim_inventory.py` — conservative repair pass via unambiguous notebook-ID match (recovered 183/191 on reconstructed draft_9) |
| J | `resolve_claude_bin()` — absolute-path resolution at orchestrator init via `BERIL_CLAUDE_BIN` env → `shutil.which` → well-known locations. All four `claude -p` call sites use the absolute path. Plus `draft.py` `projects/<id>/` path fallback (was in `--help` but never coded) and clean stillborn-dir handling |
| K | `resolve_adversarial_bin()` — parallel to Tier J but optional (returns None on miss). Orchestrator logs loud WARNING at init if canonical reviewer is missing AND `--no-adversarial` not set. `phase_review` Tier 3 branches three ways; `audit/review_mode.json` records which reviewer ran |
| — | Default `self.model` flipped from Sonnet 4.5 → Opus 4.6 for the reasoning-heavy phases (plan, triage, optimizer). Holistic drafter was already Opus; the silent Sonnet default for scaffolding was backwards |

See [`STAGED_IMPROVEMENT_PLAN.md`](STAGED_IMPROVEMENT_PLAN.md) for the
full closure table with verification evidence and
[`DECISIONS.md`](DECISIONS.md) D-041 through D-051 for the design
rationale per tier.

### Post-Stage-3 audit cleanup (2026-05-17)

Doc + code reorg per [`audit/audit-2026-05-17.md`](audit/audit-2026-05-17.md):

- **SPEC merge** — old `SPEC.md` (v0.1, foundation) merged into the
  v0.8 spec; canonical file is now [`SPEC.md`](SPEC.md). Old v0.1
  archived at [`release-notes/SPEC_v0_1.md`](release-notes/SPEC_v0_1.md).
- **Doc reorg** — historical planning docs (M1, V0_7 punch lists)
  moved to `archive-planning/`. Reviews moved to `archive-reviews/`.
  Per-version release notes moved to `release-notes/`. Spec proposals
  that were never merged moved to `archive-planning/spec-additions/`.
- **Code cleanup** — `llm_client.py` removed (dead); three broken test
  files removed (referenced pre-Stage-1 design); `_locate_paper_writer_sh`
  dead function removed from `draft.py`; stale docstrings rewritten.
- **Bash-flow retirement track** — `paper_writer.sh` and
  `paper_writer_helpers.py` audited; on the retirement track. The
  Python orchestrator (`orchestrator.py`) is the canonical entry
  point. Bash flow preserved as a safety net during the transition.

## Per-version notes

Per-stage and per-version detail lives at:

| File | Coverage |
|---|---|
| [release-notes/v0_1.md](release-notes/v0_1.md) | First usable release (2026-04-27) |
| [release-notes/v0_2.md](release-notes/v0_2.md) | Repair-mode + post-checker hardening |
| [release-notes/v0_3.md](release-notes/v0_3.md) | Figure-embed loop (Tier 3 sub-tiers) |
| [release-notes/v0_4.md](release-notes/v0_4.md) | Caption-richness gap closure |
| [release-notes/v0_5.md](release-notes/v0_5.md) | Caption-quality tightening (point release) |
| [release-notes/v0_6.md](release-notes/v0_6.md) | Dual-reviewer architecture decision |
| (gap: v0.7) | Stage 1 + Stage 2 work documented in `STAGED_IMPROVEMENT_PLAN.md` |
| (current: v0.8 + Stage 3) | This file + `STAGED_IMPROVEMENT_PLAN.md` |
| [release-notes/SPEC_v0_1.md](release-notes/SPEC_v0_1.md) | Original v0.1 SPEC, merged into current `SPEC.md` |

## Pointers

- [`README.md`](README.md) — quick-start
- [`SPEC.md`](SPEC.md) — foundation + v0.8 architecture + ICMJE appendix
- [`STAGED_IMPROVEMENT_PLAN.md`](STAGED_IMPROVEMENT_PLAN.md) — Stage 1/2/3 closure tables + backlog
- [`DECISIONS.md`](DECISIONS.md) — design decisions (D-001 through D-051)
- [`audit/audit-2026-05-17.md`](audit/audit-2026-05-17.md) — repo-wide audit doc driving the post-Stage-3 cleanup
