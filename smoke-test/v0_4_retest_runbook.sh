#!/bin/bash
# v0.4 retest runbook — fresh draft on functional_dark_matter against v0.4-dev.
#
# Created: 2026-04-28.
# Tests: Tier 8 caption-richness pipeline end-to-end —
#   - extract_figures.py: cell-attribution rewrite (Phase 1a) + v2
#     inventory schema (Phase 1b) + matplotlib AST extraction (Phase 2)
#   - paper_writer_helpers.py: prose-panel detection + description
#     assembler + descriptor flow-through (Phase 3)
#   - assemble_docx.py: italic Description → Caption-style upgrade
#   - figure_caption.v1.md: LLM caption synthesis (Phase 4a)
#   - check_caption_provenance.py: 6th post-checker (Phase 4b)
#   - phase_caption_synthesis + phase_check_caption_provenance:
#     orchestrator wiring (Phase 4c)
# Cost estimate: ~$8-9 (v0.3 retest baseline $6.49 + ~$2 Source 4
# synthesis on the 7 figures expected to fail the sufficiency gate).
# Defensive: --max-cost-usd 10 hard cap (orchestrator halts if
# cumulative exceeds).
# Side effects: creates a NEW draft_3 next to existing draft_1, draft_2;
# does NOT modify earlier drafts.
#
# Inspect this script BEFORE running. The script prints manual commands
# as a heredoc — does NOT auto-execute the LLM-burning steps.

set -euo pipefail

# === Paths ===
SKILL_SRC="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter"
DRAFT_3="$PROJECT_ROOT/papers/draft_3"

cd "$SKILL_SRC"

echo "=== Pre-flight: working tree state ==="
echo "  source dir: $SKILL_SRC"
echo "  pyproject version: $(grep -E '^version' pyproject.toml | head -1)"
echo "  __init__ version: $(grep '__version__' src/beril_paper_writer/__init__.py)"
echo "  v0.4 retest runs against v0.4-dev working tree (committed; pre-ship)"
echo "  Version remains 0.3.0; the bump to 0.4.0 happens at ship runbook."
echo
echo "  Last commit (should be v0.4-dev pre-ship checkpoint):"
git log -1 --oneline 2>/dev/null || echo "  (git not on PATH or not in a repo)"
echo

echo "=== Pre-flight: python + bash sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  check_caption_provenance.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/extract_figures.py && echo "  extract_figures.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  assemble_docx.py: compile OK"
echo

echo "=== Pre-flight: v0.4 surfaces present ==="
test -f src/beril_paper_writer/skill/prompts/figure_caption.v1.md && echo "  ✓ prompts/figure_caption.v1.md (Phase 4a)"
test -f src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  ✓ tools/check_caption_provenance.py (Phase 4b)"
grep -q 'phase_caption_synthesis()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_caption_synthesis (Phase 4c)"
grep -q 'phase_check_caption_provenance()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_check_caption_provenance (Phase 4c)"
grep -q 'cmd_build_caption_bundles' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ build-caption-bundles helper (Phase 4c)"
grep -q 'cmd_compute_caption_stats' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ compute-caption-stats helper (Phase 4c)"
grep -q 'class CaptionDescriptor' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ CaptionDescriptor (Phase 1b)"
grep -q '_extract_plot_calls' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ matplotlib AST extraction (Phase 2)"
grep -q '_assemble_description_text' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ description assembler (Phase 3)"
grep -q '_is_italic_description_paragraph' src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  ✓ italic-Description detector in assembler (Phase 3)"
grep -q 'inventory_schema_version: 2' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ v2 inventory schema header (Phase 1b)"
echo

echo "=== Pre-flight: tooling on PATH ==="
if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: 'claude' CLI not on PATH; orchestrator will fail" >&2
    exit 1
fi
echo "  ✓ claude on PATH ($(which claude))"
if ! command -v pipx >/dev/null 2>&1; then
    echo "ERROR: 'pipx' not on PATH" >&2
    exit 1
fi
echo "  ✓ pipx on PATH ($(which pipx))"
echo

echo "=== Pre-flight: project + draft state ==="
if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "ERROR: functional_dark_matter project not at $PROJECT_ROOT" >&2
    exit 1
fi
echo "  ✓ functional_dark_matter at $PROJECT_ROOT"
if [[ -d "$DRAFT_3" ]]; then
    echo "WARNING: draft_3 already exists at $DRAFT_3"
    echo "  This retest expects to create draft_3 fresh."
    echo "  Either move/delete draft_3 first, or the orchestrator may resume into it."
fi
echo "  draft_1 exists: $(test -d "$PROJECT_ROOT/papers/draft_1" && echo 'yes' || echo 'no')"
echo "  draft_2 exists: $(test -d "$PROJECT_ROOT/papers/draft_2" && echo 'yes' || echo 'no')"
echo

cat <<'EOF'
=== Manual steps (run after reviewing the diff and pre-flight output) ===

# ---------------------------------------------------------------------------
# Step 1: Reinstall pipx package from the local working tree (editable
#         mode so v0.4-dev edits during retest pickup live). MUST be the
#         v0.4-dev working tree — verify the install picks up Phase 4c
#         surfaces by checking that `beril-paper-writer-helpers
#         build-caption-bundles --help` works.
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force --editable .
beril-paper-writer --version    # should print 0.3.0 (no bump for dev)

# Verify the v0.4-dev surfaces installed correctly:
PIPX_PYTHON=$(pipx list --short | grep beril-paper-writer | head -1 | awk '{print $1}')
PIPX_VENV="$HOME/.local/pipx/venvs/beril-paper-writer"
PYTHON_BIN="$PIPX_VENV/bin/python"
"$PYTHON_BIN" "$PIPX_VENV/lib/python"*"/site-packages/beril_paper_writer/skill/tools/paper_writer_helpers.py" \
    build-caption-bundles --help
# Should print the argparse help; if not, install didn't pick up Phase 4c.

# ---------------------------------------------------------------------------
# Step 2: (Optional) Backup draft_3 if it already exists.
# ---------------------------------------------------------------------------

DRAFT_3="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3"
if [[ -d "$DRAFT_3" ]]; then
    mv "$DRAFT_3" "${DRAFT_3}.pre_v0_4_retest.bak"
    echo "Existing draft_3 moved to ${DRAFT_3}.pre_v0_4_retest.bak"
fi

# ---------------------------------------------------------------------------
# Step 3: Start the fresh draft. This runs init + extract_methods +
#         extract_figures (NEW: emits v2 schema with Description blocks)
#         + plan.v1; pauses at throughline_pick. Cost: ~$1.50.
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev"
beril-paper-writer draft \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter" \
    --depth standard

# At pause: read draft_3/throughline_candidates.md; pick a TLN that fits
# the project's frame. Inspect candidates with:
#   cat "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/throughline_candidates.md"

# Also: confirm v2 inventory landed BEFORE proceeding to Step 4:
head -1 "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/figures_inventory.md"
# Should print: <!-- inventory_schema_version: 2 -->
grep -c "^\*\*Description:\*\*" "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/figures_inventory.md"
# Should print >= 30 (most figures have walk-back content).

# ---------------------------------------------------------------------------
# Step 4: Resume drafting with the chosen throughline. Pipeline:
#   throughline-pick → methods.v1 → citation_pool → results.v1 →
#   check_figures_manifest → caption_synthesis (NEW Phase 4c) →
#   check_caption_provenance (NEW Phase 4c) → embed_figures →
#   discussion.v1 → intro.v1 → abstract.v1 → finalize_citations →
#   check_scope_coherence → check_overclaim → assemble →
#   repair_validators → review → review_rewrite → emit_review_handoff.
# Pauses at final review handoff. Cost: ~$5-7 ($3-4 baseline + ~$2
# Source 4 synthesis on ~7 figures from the sufficiency gate).
# --max-cost-usd 10 is the defensive cap.
# ---------------------------------------------------------------------------

# Replace TL1 with whichever candidate you picked.
beril-paper-writer continue \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3" \
    --pick TL1
# If you want a tighter cap during retest, add --max-cost-usd 8 to the
# continue invocation. The Phase 5 cost projection is ~$8-9 max.

# ---------------------------------------------------------------------------
# Step 5: Render the docx via the assemble path.
# ---------------------------------------------------------------------------

beril-paper-writer assemble \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3" \
    --format docx

EOF

echo
echo "=== After Step 5 — verification gates (run each, paste output if anomalies) ==="
cat <<'EOF'

DRAFT_3="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3"

# ===========================================================================
# Gate 1: extract_figures emitted v2 schema with Description blocks.
# ===========================================================================
echo "=== Gate 1: v2 inventory schema ==="
head -1 "$DRAFT_3/figures_inventory.md"
echo "  Expect: <!-- inventory_schema_version: 2 -->"
grep -c "^\*\*Description:\*\*" "$DRAFT_3/figures_inventory.md"
echo "  Expect: >= 30 (most figures have walk-back content)"
echo

# ===========================================================================
# Gate 2: results.v1 + check_figures_manifest still green.
# ===========================================================================
echo "=== Gate 2: figures_manifest cross-walk ==="
echo "--- figures_manifest.tsv row count (minus header) ---"
echo $(($(wc -l < "$DRAFT_3/figures_manifest.tsv") - 1))
echo
echo "--- audit/figures_manifest_warnings.txt ---"
cat "$DRAFT_3/audit/figures_manifest_warnings.txt"
echo "  Expect: 0 WARN lines (or only documented borderline NOTE cases)"
echo

# ===========================================================================
# Gate 3: phase_caption_synthesis ran. Inspect metadata.
# ===========================================================================
echo "=== Gate 3: caption synthesis ran; cost <= \$3 ==="
python3 -c "
import json
d = json.load(open('$DRAFT_3/audit/figure_caption.v1.metadata.json'))
caps = d['captions']
total = len(caps)
det = sum(1 for c in caps if c['source_chosen']=='deterministic')
llm = sum(1 for c in caps if c['source_chosen']=='llm')
print(f'  total figures:       {total}')
print(f'  source=deterministic: {det}')
print(f'  source=llm:          {llm}')
print(f'  llm figure_ids:      {[c[\"figure_id\"] for c in caps if c[\"source_chosen\"]==\"llm\"]}')
# Stats on llm captions
llm_entries = [c for c in caps if c['source_chosen']=='llm']
if llm_entries:
    word_counts = [c.get('closing_message',{}).get('word_count', 0) for c in llm_entries]
    print(f'  llm word_counts:     min={min(word_counts)} max={max(word_counts)} avg={sum(word_counts)/len(word_counts):.0f}')
    print(f'    word_count <30:    {sum(1 for w in word_counts if w < 30)}')
    print(f'    word_count >200:   {sum(1 for w in word_counts if w > 200)}')
"
echo
echo "--- Source 4 invocation cost (sum of figure_caption_*.invoke.metadata.json) ---"
python3 -c "
import json, glob
total = 0.0
for path in sorted(glob.glob('$DRAFT_3/audit/figure_caption_*.invoke.metadata.json')):
    try:
        d = json.load(open(path))
        # invoke metadata schema varies; try common keys
        for k in ('total_cost_usd', 'cost_usd', 'cost'):
            if k in d:
                total += float(d[k])
                break
    except Exception as e:
        print(f'  WARN: {path}: {e}')
print(f'  Source 4 cumulative: \${total:.4f}')
print(f'  Expect: <= \$3.00')
"
echo

# ===========================================================================
# Gate 4: check_caption_provenance — 0 ungrounded WARNs.
# ===========================================================================
echo "=== Gate 4: caption provenance — 0 fabrication WARNs ==="
cat "$DRAFT_3/audit/caption_provenance_warnings.txt"
WARN_COUNT=$(grep -c "^\[check_caption_provenance\] WARN" "$DRAFT_3/audit/caption_provenance_warnings.txt" 2>/dev/null || echo 0)
echo "  Total WARN lines: $WARN_COUNT"
echo "  Expect: 0 (or only word_count band warnings if a caption is borderline)"
echo

# ===========================================================================
# Gate 5: phase_embed_figures injected image tags + Description paragraphs.
# ===========================================================================
echo "=== Gate 5: embed_figures injected multi-line content ==="
echo "--- audit/embed_figures.log ---"
cat "$DRAFT_3/audit/embed_figures.log"
echo
echo "--- 02_results.md image tag + Description paragraph counts ---"
echo "Image tags:           $(grep -c '^!\[Figure ' "$DRAFT_3/02_results.md")"
echo "*Description:* paras: $(grep -c '^\*Description:' "$DRAFT_3/02_results.md")"
echo "  Expect: same K image tags AND >= some Description paragraphs"
echo "  (Description paragraphs only on figures with non-empty descriptors;"
echo "   may be < image-tag count if some figures are LLM-fail / fully-empty)"
echo

# ===========================================================================
# Gate 6: manuscript.docx renders Picture + 2-paragraph Caption per figure.
# ===========================================================================
echo "=== Gate 6: docx structure ==="
python3 << 'PY'
import os
from docx import Document
d_path = os.path.expanduser("~/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/manuscript.docx")
d = Document(d_path)
print(f"  size:                {os.path.getsize(d_path):,} bytes")
print(f"  paragraphs:          {len(d.paragraphs):,}")
print(f"  inline shapes:       {len(d.inline_shapes)}  (= figures embedded)")
captions = [p for p in d.paragraphs if p.style.name == 'Caption']
print(f"  Caption paragraphs:  {len(captions)}")
# Count "Figure N: ..." vs "Description: ..." captions
figure_caps = [p for p in captions if p.text.startswith('Figure ')]
desc_caps   = [p for p in captions if p.text.startswith('Description: ')]
print(f"    'Figure N: ...':   {len(figure_caps)}")
print(f"    'Description: ...':{len(desc_caps)}")
# Word-count average across all captions
import re
word_counts = [len(re.findall(r'\S+', p.text)) for p in captions]
if word_counts:
    print(f"  Caption word counts: min={min(word_counts)} max={max(word_counts)} avg={sum(word_counts)/len(word_counts):.0f}")
    above_30 = sum(1 for w in word_counts if w >= 30)
    print(f"    captions >=30 wds: {above_30}/{len(captions)}")
print()
print("  Sample captions:")
for p in captions[:6]:
    print(f"    [{p.style.name}] {p.text[:120]}")
PY
echo

# ===========================================================================
# Gate 7: cumulative cost ≤ $10 (the cap; expected ~$8-9).
# ===========================================================================
echo "=== Gate 7: cumulative cost ==="
python3 \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools/paper_writer_helpers.py" \
    cumulative-cost "$DRAFT_3"
echo "  Expect: <= \$10.00 (cap); typical ~\$8-9"
echo

# ===========================================================================
# Gate 8: validator pass/fail summary.
# ===========================================================================
echo "=== Gate 8: validation summary ==="
python3 -c "import json; v = json.load(open('$DRAFT_3/audit/validation.json')); print(json.dumps(v.get('summary', {}), indent=2))"
echo "  Expect: M1-M10 pass (or known-residual M10 only)"

EOF

echo
echo "=== Visual review (manual; do NOT skip) ==="
cat <<'EOF'

Open the manuscript.docx in Word/Pages/LibreOffice. Spot-check 3 figures
across the multi-panel + single-panel + LLM-synthesized buckets:

  open "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/manuscript.docx"

For each of 3 sample figures:

  □ Figure number, alt-text caption, and Description paragraph all
    visually adjacent to the Picture.
  □ Description paragraph contains substantive content (not just
    "Figure N: <noun phrase>." which is the alt-text).
  □ Multi-panel figures: Description names each panel by letter with
    distinct content per panel.
  □ Numerical claims in Description are present in REPORT.md,
    figures_inventory.md, or 02_results.md (spot-check 1-2 numbers).
  □ No "n=NNN" / "p<NNN" / "± SEM" claims that don't trace to the
    inputs (anti-fabrication discipline holding).

EOF

echo
echo "=== Decision criteria ==="
cat <<'EOF'

GREEN (proceed to Phase 6 v0.4.0 ship):
  - Gate 1: v2 schema header present + >=30 Description blocks
  - Gate 2: 0 figures-manifest WARN
  - Gate 3: caption_synthesis cost <= $3 + word_counts in [30, 200]
  - Gate 4: 0 caption-provenance WARN (or only word-count band warnings)
  - Gate 5: K image tags + Description paragraphs in 02_results.md
  - Gate 6: K Pictures + (K + ≤K) Caption paragraphs in docx;
           caption avg word count >= 30
  - Gate 7: cumulative cost <= $10
  - Gate 8: M1-M10 pass (or known-residual M10 only)
  - Visual review: all 3 spot-check checkboxes confirmed
  - Total cost: ~$8-9 expected

YELLOW (patch + retest before Phase 6):
  - Gate 4: 1-2 ungrounded-number WARNs that are real signal
    (anti-fabrication missed something in the prompt)
  - Gate 6: caption avg word count < 30 (sufficiency gate too strict
    OR LLM falling short consistently)
  - Visual review: 1-2 panels with subtly wrong content
    (descriptor merge logic edge case)

RED (debug before Phase 6):
  - Gate 1: v1 inventory shipped (Phase 1b regression)
  - Gate 2: > 5 figures-manifest WARN (orchestrator routing broken)
  - Gate 3: caption_synthesis didn't run (build-caption-bundles or
    bash phase routing broken) OR cost > $5 (LLM exceeded ceiling)
  - Gate 4: > 5 ungrounded-number WARNs (anti-fabrication discipline
    failed; prompt needs reinforcement)
  - Gate 6: 0 Description paragraphs in docx (Phase 3 wiring broken)
  - Gate 7: cumulative cost > $10 (cost cap broke; investigate halt path)

If RED: do NOT ship. Capture findings in
spike/beril-paper-writer-skill-draft/smoke-test/v0_4_retest_findings.md
and we patch.

EOF

echo
echo "=== Backup recommendation ==="
echo "If draft_3 already exists from a prior test, back it up first:"
echo "  mv \"$DRAFT_3\" \"${DRAFT_3}.pre_v0_4_retest.bak\""
echo
echo "After successful ship, the backup can be removed."
