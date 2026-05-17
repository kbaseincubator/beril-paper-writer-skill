# Cross-skill interop contract — paper-writer consumer side

**Status:** Created at v0.6.5 (2026-05-03). Consumer-side contract for
beril-paper-writer's dependency on beril-adversarial.

**Companion:** beril-adversarial's producer-side CONTRACT.md (v0.7.0)
documents the full CLI surface, JSON schemas, and migration procedures.
This document pins **what paper-writer expects** and **how it handles
the adversarial skill's output**.

---

## Dependency relationship

Paper-writer has a **soft dependency** on beril-adversarial (per
DECISIONS.md D-005: loose coupling + inline fallback). The adversarial
skill is never imported as a library; all interaction is via the CLI.

Two reviewer modes exist:

| Mode | When used | CLI | Speed | Classes | Tool access |
|---|---|---|---|---|---|
| **Fallback inline** | Default in v0.6.x; always used in rewrite loop | `claude -p` with `fallback_reviewer.v1.md` | ~30s | 3 (overclaim, citation rigor, scope alignment) | None |
| **Canonical adversarial** | Post-rewrite audit (v0.7.0+) | `beril-adversarial review <dir> --type paper` | 5–10 min | 10 | PubMed, WebSearch, Read |

The fallback reviewer is **not a degraded adversarial** — it's a
purpose-built fast reviewer for the rewrite loop. The canonical
reviewer serves a different purpose: thorough audit of a finished draft.
Both are load-bearing (confirmed 2026-05-03 in adversarial taxonomy
review: "two tools, two purposes, stable equilibrium").

---

## What paper-writer provides (adversarial's inputs)

The canonical adversarial reviewer reads paper-writer's per-draft
directory layout. The contract below is the **minimum** layout that
the reviewer requires:

```
projects/<project_id>/papers/draft_N/
├── manuscript.md           ← REQUIRED
├── 00_throughline.md       ← REQUIRED
├── references.md           ← REQUIRED
├── citation_map.md         ← REQUIRED (underscore, not hyphen)
├── reframing_log.md        ← OPTIONAL (report_drift detection degrades without it)
├── methods_provenance.md   ← OPTIONAL
├── figures_inventory.md    ← OPTIONAL
├── tables_inventory.md     ← OPTIONAL (v0.6+ tables pipeline)
└── audit/                  ← created/overwritten by reviewer
```

Plus from the project root:

```
projects/<project_id>/
├── REPORT.md               ← REQUIRED (quantitative grounding truth)
└── RESEARCH_PLAN.md        ← OPTIONAL (missing-section detection)
```

**Breaking-layout note:** If paper-writer v0.7+ changes the per-draft
directory layout (e.g., introducing zone dirs like presentation-maker's
deliverable/narrative/working/audit split), both this contract AND
beril-adversarial's CONTRACT.md must be updated simultaneously. See
memory entry `feedback_cross_skill_contract_drift.md` for the pattern
that went wrong twice in 24 hours.

---

## What paper-writer consumes (adversarial's outputs)

### Output paths (deterministic)

```
projects/<project_id>/papers/draft_N/audit/
├── adversarial_review.md                     ← human-readable
├── adversarial_review.json                   ← machine-readable (contract surface)
└── adversarial_review.original-summary.json  ← sidecar if auto-correction fired
```

Both `.md` and `.json` are written on every successful run. Existing
files are overwritten (no auto-numbering in v0.6.x). If paper-writer
needs to preserve prior reviews across rounds, it must rename/move
the `audit/` directory between runs (Pattern B from adversarial's
CONTRACT.md).

### JSON schema version

Paper-writer v0.7.x accepts: **`adversarial-review-paper.v2`** or
**`adversarial-review-paper.v3`** (v3 is the current adversarial
output as of adversarial v0.7.0+). v3 changes: class rename
`narrative_weakness` → `central_objection`; new `citation_reality`
class; `--output` flag honored. See adversarial CONTRACT.md §v0.7.0
migration.

**Note:** Paper-writer v0.7.x does not yet invoke the canonical
adversarial reviewer in-pipeline (`phase_adversarial_audit` is
planned but unimplemented). The canonical reviewer is used
standalone via `beril-adversarial review --type paper`. The
in-pipeline rewrite loop uses the fallback inline reviewer only.

Schema version is found in the JSON at `.schema_version`.

### Exit code handling

Paper-writer's orchestrator (paper_writer.sh) should handle adversarial
exit codes as follows:

| Exit | Meaning | Paper-writer policy |
|---|---|---|
| 0 | Clean pass | Parse JSON; route P0 findings to rewrite loop |
| 2 | Auto-corrected (advisory) | Treat as 0; JSON is consumer-safe. Log the auto-correction for user awareness |
| 1 | Validation failure | Retry once. If second run also exits 1, fall back to inline reviewer + warn user. Most common cause: unescaped `"` in JSON string field (per `feedback_llm_json_unfixable_in_parser.md`) |
| 3 | Config error | `claude` CLI missing or prompt missing. Surface error; do not retry |

### Severity vocabulary mapping

The v2 JSON uses bug-tracker conventions. Paper-writer's fallback
reviewer and rewrite loop use legacy labels. The mapping is bijective:

| v2 JSON (`severity`) | Paper-writer label | Rewrite-loop behavior |
|---|---|---|
| `P0` | Critical | Triggers rewrite dispatch to affected section |
| `P1` | Important | Surfaces in next_actions.md; rewrite-eligible at pass 1 |
| `P2` | Suggested | next_actions.md only; not auto-rewritten |
| `info` | _(no action)_ | Single `central_objection` finding (v3; was `narrative_weakness` in v2); strategic note for author. Not a fix target |

Consumer-side translation:

```python
SEVERITY_MAP = {"P0": "critical", "P1": "important", "P2": "suggested"}

def count_actionable(findings: list[dict]) -> dict[str, int]:
    counts = {"critical": 0, "important": 0, "suggested": 0}
    for f in findings:
        sev = SEVERITY_MAP.get(f["severity"])
        if sev:
            counts[sev] += 1
    return counts
```

### Class enum (paper.v2/v3 — 10 classes)

Paper-writer routes findings by `fix_target` rather than by class.
The class enum is documented here for completeness and for routing
classes that need special handling:

| Class | Shared with pres? | Special handling in paper-writer |
|---|---|---|
| `claim_evidence` | yes | Routes to fix_target section's rewrite prompt |
| `unbacked_quantitative` | yes | Routes to fix_target; highest rewrite priority |
| `register_drift` | yes | Routes to fix_target |
| `citation_reality` | paper-only | Routes to references.v1.md rewrite + citation_map fix |
| `report_drift` | paper-only | Routes to fix_target + reframing_log.md update |
| `abstract_body_mismatch` | paper-only | Routes to abstract.v1.md rewrite |
| `missing_section` | paper-equiv | Surfaces in next_actions.md (cannot auto-generate a section) |
| `section_arc` | paper-equiv | Routes to fix_target section's rewrite prompt |
| `throughline` | yes | Surfaces in next_actions.md (throughline is user-owned) |
| `central_objection` (v3) / `narrative_weakness` (v2) | yes | No action — strategic note (info severity) |

### fix_target values (paper-writer prompt names)

The adversarial JSON's `fix_target` field contains prompt filenames
that correspond to paper-writer's section prompts:

```
methods.v1.md, results.v1.md, discussion.v1.md, introduction.v1.md,
abstract.v1.md, limitations.v1.md, references.v1.md,
00_throughline.md, reframing_log.md, manuscript.v1.md
```

Paper-writer's rewrite dispatcher maps these to the actual rewrite
invocation. If a `fix_target` value does not match a known prompt,
the finding is logged to `audit/rewrite_summary.txt` and surfaced
in `next_actions.md` for manual resolution.

---

## Version compatibility

### Current compatible versions

| Paper-writer | Adversarial | Schema | Notes |
|---|---|---|---|
| v0.6.x | v0.6.0–v0.6.3 | paper.v2 | Current stable pair |
| v0.6.x | v0.7.0 | paper.v3 | v3 output readable but `narrative_weakness` → `central_objection` class rename requires paper-writer parser update |
| v0.7.0 (planned) | v0.7.0 | paper.v3 | Full v3 adoption |

### Runtime version check (v0.7.0+)

Paper-writer's orchestrator should verify adversarial is installed and
at a compatible version before invoking the canonical reviewer:

```bash
# In paper_writer.sh, before phase_adversarial_audit:
if ! command -v beril-adversarial &>/dev/null; then
    log_warn "beril-adversarial not installed; skipping canonical audit"
    log_warn "Install: pipx install beril-adversarial-skill"
    return 0
fi

adv_version=$(beril-adversarial --version 2>/dev/null || echo "unknown")
log_step "beril-adversarial version: $adv_version"

# Version compatibility gate (update when schema changes):
case "$adv_version" in
    0.6.*|0.7.*|0.8.*)
        ;;  # Compatible range
    *)
        log_warn "beril-adversarial $adv_version may be incompatible"
        log_warn "Expected 0.6.x–0.8.x for adversarial-review-paper.v2/v3"
        log_warn "Falling back to inline reviewer"
        return 0
        ;;
esac
```

**Note:** `beril-adversarial --version` outputs the version string
to stdout (verified at v0.7.0). The check is advisory — an
unrecognized version falls back to the inline reviewer rather than
failing the pipeline.

---

## Fallback reviewer coordination

When the canonical adversarial reviewer is unavailable (not installed,
wrong version, or exit 3), paper-writer uses `fallback_reviewer.v1.md`.
The two reviewers produce different output formats:

| Property | Fallback | Canonical |
|---|---|---|
| Output path | `reviews/fallback_review.md` (Python flow) / `reviews/draft_N_review_M.md` (legacy bash flow) | `audit/adversarial_review.{md,json}` |
| Format | Markdown with YAML frontmatter | Markdown + JSON (schema-versioned) |
| Machine-parseable | No (finding headers only: `**C\d+:`, `**I\d+:`, `**S\d+:`) | Yes (JSON `findings[]` array) |
| Severity vocab | Critical / Important / Suggested | P0 / P1 / P2 / info |
| `fallback: true` header | Yes (YAML frontmatter) | No |
| Optimizer dispatch compatible | No (no structured JSON to dispatch on; optimizer skips with a `findings missing` warning) | Yes |

The post-Stage-3 Python orchestrator (Tier K) selects which reviewer
runs at Tier 3 of the review cascade — see "review_mode.json consumer
contract" below.

---

## review_mode.json consumer contract (Stage 3 Tier K, 2026-05-16)

When paper-writer's `phase_review` runs, it writes
`papers/draft_N/audit/review_mode.json` recording which Tier-3
reviewer fired. Downstream consumers (other skills, CI checks, manual
auditors) can rely on this artifact to know what kind of review the
manuscript carries without parsing the review file itself.

### Schema

```json
{
  "reviewer": "canonical|canonical-failed|fallback|fallback-failed|none",
  "note": "free-text context (reason tag, error summary, etc.)",
  "timestamp": "ISO-8601 UTC, e.g. 2026-05-16T14:30:00Z"
}
```

### `reviewer` field semantics

| Value | Meaning |
|---|---|
| `canonical` | beril-adversarial ran successfully; `audit/adversarial_review.{md,json}` exists |
| `canonical-failed` | beril-adversarial was invoked but exited non-zero; manuscript review may be incomplete; `note` carries the exit-code summary |
| `fallback` | inline `fallback_reviewer.v1.md` ran; `reviews/fallback_review.md` exists; `note` indicates `reason=adversarial-missing` (canonical was unreachable) or `reason=explicit-opt-out` (`--no-adversarial` flag) |
| `fallback-failed` | inline fallback was invoked but exited non-zero; manuscript is effectively unreviewed |
| `none` | the fallback prompt file is missing on disk; manuscript is unreviewed and Tier 3 was silently skipped |

### When to read it

- **Cross-skill consumers** (e.g., a downstream presentation-maker
  that wants to know whether the manuscript was canonically reviewed
  before pulling content from it): always check
  `audit/review_mode.json`. If `reviewer != "canonical"`, the
  consumer should flag the source manuscript as unverified by the
  heavy reviewer.
- **Auditors / operators**: after a run completes, this file tells
  you in one read whether the canonical reviewer fired without
  needing to dig through logs.
- **Stability**: schema additions are additive (new fields may
  appear; existing fields preserved). The `reviewer` enum may gain
  new values but the four listed above will remain.

---

## claim_inventory_validation.json additive fields (Stage 3 Tier I, 2026-05-12)

`papers/draft_N/audit/claim_inventory_validation.json` (the post-validator
for `claim_inventory.tsv`) gained two additive fields in Stage 3 Tier I:

```json
{
  "rows_repaired_this_run": <int>,
  "repaired_notebooks": {
    "<original_source_notebook_value>": "<resolved_full_filename>",
    ...
  }
}
```

All pre-existing fields preserved:
- `total_rows`, `rows_with_source_notebook`, `rows_updated_this_run`,
  `rows_already_marked_unresolved`, `unique_invalid_notebooks` (the
  list of values that stayed CLEARED — not repaired).

### Consumer implications

Cross-skill consumers that read `claim_inventory.tsv` for
notebook-grounding (e.g., presentation-maker's no-paper originate
path, which vendors paper-writer's `extract_claims.v1.md` + this
validator):

- A row's `source_notebook` is now potentially **repaired** in place
  rather than only cleared. Trustworthy provenance is now
  `non-empty source_notebook AND (notes doesn't start with
  unresolved-notebook: OR notes starts with notebook-repaired:)`.
- If consumer logic keyed on the `unresolved-notebook:` note prefix
  as the "bad" signal, treat `notebook-repaired:` as the
  "good/recovered" signal — don't lump them.

---

## Cross-skill integration test

Paper-writer's test suite should include a cross-skill integration
test that verifies the contract surface when adversarial is installed:

```python
# tests/integration/test_adversarial_interop.py

def test_adversarial_json_schema_readable():
    """Verify paper-writer can parse adversarial's output schema."""
    # Fixture: minimal adversarial-review-paper.v2 JSON
    fixture = {
        "schema_version": "adversarial-review-paper.v2",
        "findings": [
            {
                "class": "claim_evidence",
                "severity": "P0",
                "section": "Results",
                "fix_target": "results.v1.md",
                "title": "Test finding",
                "paragraph_quote": "...",
                "line_range": "L42-L45",
                "evidence": "...",
                "recommendation": "..."
            }
        ],
        "summary": {"total": 1, "P0": 1, "P1": 0, "P2": 0, "info": 0}
    }
    # ... parse and verify routing
```

A live integration test (invoking `beril-adversarial review` against
a synthetic draft) lives in beril-adversarial's own test suite at
`tests/integration/test_paper_writer_interop.py` (6 tests as of
v0.6.0). Paper-writer does NOT duplicate that test; it trusts
adversarial's CI to verify its own output contract.

---

## Contract change protocol

When either side needs to change an interface that the other depends on:

1. **File consumer-update tasks BEFORE tagging the producer.**
   (Per `feedback_cross_skill_contract_drift.md`.)
2. **Update both CONTRACT.md files** (this one + adversarial's)
   in the same release cycle.
3. **Add a cross-skill smoke test** if one doesn't already exist for
   the changed surface.
4. **Backwards-compat detection:** when reading adversarial output,
   prefer the pattern `if new_field: use; elif old_field: use; else: error`
   for one transition release.

---

## References

- beril-adversarial CONTRACT.md (producer side): `spike/beril-adversarial-skill-draft/CONTRACT.md`
- DECISIONS.md D-005: loose coupling rationale
- paper_writer.sh lines 2242–2262: architecture decision comment
- fallback_reviewer.v1.md: inline reviewer prompt (3-class scope)
- Memory: `feedback_cross_skill_contract_drift.md` (why this contract exists)
- Memory: `project_adversarial_v0_6_0.md` (adversarial v0.6.0 paper alignment)
- Memory: `project_adversarial_v0_6_x_taxonomy.md` (taxonomy review + v0.7.0 roadmap)
