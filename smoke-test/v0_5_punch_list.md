# beril-paper-writer v0.5 — punch list

**Created:** 2026-04-29 (post v0.4.0 ship)
**Status:** v0.5.0 SHIPPING (cycle complete; awaiting Adam's commit + tag).
**Cadence:** small focused cycle — 1 substantive change + 1 small formula.
Single-session work; no multi-week phasing.
**Operator:** Adam Arkin, single-user.

This is the authoritative scope for the v0.5 cycle. v0.4 ship's
visual review surfaced two findings carried into v0.5:

1. Boilerplate-heavy notebook prose passed the v0.4 sufficiency gate
   but produced poor docx captions (descriptor-derived; full of
   `Purpose:` / `Approach:` / `Sections:` etc.).
2. Multi-panel figures truncated mid-word at the universal 200-word
   cap on Source 4 LLM captions.

Tables (Tier 9 — Adam's other priority) are deferred to v0.6 as their
own multi-week cycle.

---

## Phase A — Switch sufficiency gate to use _strip_prose_for_inline

**Status: DONE** (2026-04-29).

Change in `paper_writer_helpers.py:_passes_sufficiency_gate`:
swap `_strip_heading_lines(notebook_prose)` for
`_strip_prose_for_inline(notebook_prose)` in the word-count check.

The newer strip drops keyword headers (both `**X:**` and `**X**:`
bold idioms), inline-cascade keyword chains, project-internal artifact
references, numbered list items, and bold-Bold:Bold patterns. Boilerplate-
heavy notebook prose now correctly fails the gate (post-strip word
count <30 because the keyword content is removed).

Effect: figures whose notebook prose is mostly dev-process metadata
(figs 8/9/10 in `functional_dark_matter` draft_3) route to Source 4
LLM, which has explicit anti-pattern discipline against the same
boilerplate. Result: clean ICMJE-conventional captions instead of
descriptor-derived metadata-soup.

Tests: 2 new (boilerplate-only descriptor fails; substantive
descriptor passes); existing tests still pass (430 total).

`_strip_heading_lines` is no longer called by the gate but is retained
in the source as a stable helper (other future code paths may want
simple heading-strip semantics).

---

## Phase B — Panel-count-scaled max_words for Source 4 captions

**Status: DONE** (2026-04-29).

New helper in `paper_writer_helpers.py`:

```python
def _caption_max_words(panel_count: int) -> int:
    if panel_count <= 0:
        return 200
    return 200 + 50 * panel_count
```

`cmd_build_caption_bundles` computes `panel_count` from
`descriptor.panels` (AST-detected) and uses the formula for each LLM-
routed figure's bundle. Without this, the v0.4 universal 200-word cap
truncated 4-panel figures mid-word.

The `--max-words` CLI flag is preserved as a ceiling-override for
testing but defaults to None so the formula prevails.

Word-budget by panel count:
- 0 panels (single) → 200 words
- 1 panel  → 250 words
- 2 panels → 300 words
- 3 panels → 350 words
- 4 panels → 400 words (e.g., fig 8 in functional_dark_matter)
- 6 panels → 500 words

Tests: 2 new (formula values for 0-6 panels; integration test confirms
`cmd_build_caption_bundles` writes panel-scaled `max_words` per
bundle). The synthetic-fixture `_build_draft` helper extended to emit
`_Panels:_` block in the v2 inventory so panel parsing round-trips.

---

## Phase C — Validate via draft_3 re-test (deferred to local execution)

**Status: AC-DESIGN COMPLETE; live execution by Adam.**

Validation steps (run from Adam's Mac shell after v0.5 install):

```bash
DRAFT_3="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3"

# 1. Reinstall pipx with v0.5 patches
cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force --editable .

# 2. Delete audit captions for figs 6-10 (which v0.4 marked deterministic
#    but v0.5 should now route to LLM due to boilerplate-heavy prose)
rm -f "$DRAFT_3/audit/figure_caption.v1.metadata.json"
rm -f "$DRAFT_3/audit/figure_caption_"{6,7,8,9,10}.md
rm -rf "$DRAFT_3/audit/caption_bundles/"

# 3. Reset state.json and re-run
python3 -c "
import json
from pathlib import Path
p = Path('$DRAFT_3/state.json')
state = json.loads(p.read_text(encoding='utf-8'))
state['phase'] = 'drafting'
p.write_text(json.dumps(state, indent=2), encoding='utf-8')
"
beril-paper-writer continue "$DRAFT_3"
```

**Expected (v0.5 behavior):**
- `build-caption-bundles`: 0-2 deterministic / 8-10 LLM (was 5/5 in v0.4
  on draft_3; v0.5 routes more figures to LLM due to boilerplate density).
- LLM cost: ~$0.50-0.70 (was ~$0.30 in v0.4; more figures going through).
- Caption_provenance check: should remain mostly clean (LLM has
  anti-pattern prompt for boilerplate; no fabricated content).
- Final docx: figs 8/9/10 have panel-by-panel descriptions; fig 8
  uses up to ~400 words (4 panels); no `Purpose:`/`Approach:`/etc.
  residue.

**Cost: ~$0.50 LLM.** Comfortably bounded.

---

## Phase D — Ship v0.5.0

**Status: ARTIFACTS READY; awaiting Adam's commit + tag.**

Standard ship pattern (matches v0.1-v0.4 lineage):

- pyproject.toml + __init__.py: 0.4.0 → 0.5.0 ✓
- RELEASE_NOTES_v0_5.md ✓
- smoke-test/v0_5_punch_list.md ✓ (this file)
- smoke-test/v0_5_0_ship_runbook.sh ✓
- .commit-message-v0_5_0.txt ✓
- Memory: project_paper_writer_v0_5.md ✓ (supersedes v0.4)
- Tag v0.5.0 after Adam's review.

---

*Authored 2026-04-29; cycle complete in single session.*
