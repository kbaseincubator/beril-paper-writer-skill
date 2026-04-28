#!/bin/bash
# v0.3 retest runbook — fresh draft_2 from scratch on functional_dark_matter.
#
# Created: 2026-04-27.
# Tests: full v0.3 pipeline end-to-end —
#   - results.v1 prompt edit (Tier 2.1a): (Fig. N) callouts + manifest emission
#   - phase_check_figures_manifest (Tier 2.1b): manifest schema + cross-walk
#   - phase_embed_figures (Tier 2.2): inject ![Figure N: caption](path) tags
#   - phase_results stale-file cleanup (D-025)
#   - tools/assemble_docx.py (Tier 2.3): markdown → docx with embedded Pictures
#   - commands/assemble.py (Tier 2.4): CLI surface
# Cost estimate: ~$5 in LLM (v0.1.0 ship reference: $4.20 / 30 min for fresh draft).
# Defensive: --max-cost-usd 10 hard cap (orchestrator halts if cumulative exceeds).
# Side effects: creates a NEW draft_2 next to the existing draft_1 (does NOT modify draft_1).
#
# Inspect this script BEFORE running. Run the printed commands manually
# in your shell — the script does not auto-execute the LLM-burning steps.

set -euo pipefail

# === Paths ===
SKILL_SRC="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter"
DRAFT_2="$PROJECT_ROOT/papers/draft_2"

cd "$SKILL_SRC"

echo "=== Pre-flight: working tree state ==="
echo "  source dir: $SKILL_SRC"
echo "  pyproject version: $(grep -E '^version' pyproject.toml | head -1)"
echo "  __init__ version: $(grep '__version__' src/beril_paper_writer/__init__.py)"
echo "  v0.3 retest runs against working tree (NOT a committed git tag)."
echo "  Version remains 0.2.1; the bump to 0.3.0 happens at ship runbook."
echo

echo "=== Pre-flight: python + bash sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/check_figures_manifest.py && echo "  check_figures_manifest.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  assemble_docx.py: compile OK"
echo

echo "=== Pre-flight: v0.3 surfaces present ==="
test -f src/beril_paper_writer/skill/tools/check_figures_manifest.py && echo "  ✓ check_figures_manifest.py"
test -f src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  ✓ assemble_docx.py"
grep -q 'phase_embed_figures()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_embed_figures in paper_writer.sh"
grep -q 'phase_check_figures_manifest()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_check_figures_manifest in paper_writer.sh"
grep -q 'figures_manifest.tsv' src/beril_paper_writer/skill/prompts/results.v1.md && echo "  ✓ results.v1.md mentions figures_manifest.tsv"
grep -q 'def cmd_embed_figures' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ cmd_embed_figures in helpers"
grep -q 'def cmd_resolve_figures' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ cmd_resolve_figures in helpers"
grep -q 'D-025' DECISIONS.md && echo "  ✓ D-025 (figure cleanup) in DECISIONS.md"
grep -q 'D-026' DECISIONS.md && echo "  ✓ D-026 (image-tag form) in DECISIONS.md"
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
if [[ -d "$DRAFT_2" ]]; then
    echo "WARNING: draft_2 already exists at $DRAFT_2"
    echo "  This retest expects to create draft_2 fresh."
    echo "  Either move/delete draft_2 first, or the orchestrator may resume into it."
fi
echo "  draft_1 exists: $(test -d "$PROJECT_ROOT/papers/draft_1" && echo 'yes' || echo 'no')"
echo

cat <<'EOF'
=== Manual steps (run after reviewing the diff and pre-flight output) ===

# ---------------------------------------------------------------------------
# Step 1: Reinstall pipx package from the local working tree (editable mode
#         so further edits during retest pickup live).
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force --editable .
beril-paper-writer --version    # should print 0.2.1

# ---------------------------------------------------------------------------
# Step 2: Start the fresh draft. This runs init + extract_methods +
#         extract_figures + plan.v1; pauses at throughline_pick.
#         Cost: ~$1.50.
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev"
beril-paper-writer draft \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter" \
    --depth standard

# At pause: read draft_2/throughline_candidates.md; pick a TLN that fits
# the project's frame. Inspect candidates with:
#   cat "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2/throughline_candidates.md"

# ---------------------------------------------------------------------------
# Step 3: Resume drafting with the chosen throughline. This runs
#         throughline-pick → methods.v1 → citation_pool → results.v1
#         (NEW: emits figures_manifest.tsv) → check_figures_manifest (NEW)
#         → embed_figures (NEW) → discussion.v1 → intro.v1 → abstract.v1
#         → finalize_citations → check_scope_coherence → check_overclaim
#         → assemble → repair_validators → review → review_rewrite →
#         emit_review_handoff. Pauses at final review handoff.
#         Cost: ~$3-4. --max-cost-usd 10 is the defensive cap.
# ---------------------------------------------------------------------------

# Replace TL1 with whichever candidate you picked.
beril-paper-writer continue \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2" \
    --pick TL1
# Note: --max-cost-usd is set inside paper_writer.sh's resume call;
# verify the wiring honors it. If you want a tighter cap during retest,
# add --max-cost-usd 8 to the continue invocation (the CLI forwards it).

# ---------------------------------------------------------------------------
# Step 4: Render the docx via the new assemble path.
# ---------------------------------------------------------------------------

beril-paper-writer assemble \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2" \
    --format docx

EOF

echo
echo "=== After Step 4 — verification commands (run each, paste output if anomalies) ==="
cat <<'EOF'

DRAFT_2="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2"

# Check 1: figures_manifest.tsv emitted with K rows.
echo "--- figures_manifest.tsv ---"
cat "$DRAFT_2/figures_manifest.tsv"
echo
echo "Manifest row count: $(($(wc -l < "$DRAFT_2/figures_manifest.tsv") - 1))"  # minus header

# Check 2: post-checker (check_figures_manifest) ran and found 0 WARN
#         (or only documented borderline cases).
echo
echo "--- audit/figures_manifest_warnings.txt ---"
cat "$DRAFT_2/audit/figures_manifest_warnings.txt"

# Check 3: phase_embed_figures injected K image tags.
echo
echo "--- audit/embed_figures.log ---"
cat "$DRAFT_2/audit/embed_figures.log"

# Check 4: 02_results.md has K embedded markdown image tags.
echo
echo "--- (Fig. N) callouts vs embedded image tags in 02_results.md ---"
echo "Distinct callout Ns:    $(grep -oE '\(Fig\. [0-9]+[A-Z]?\)' "$DRAFT_2/02_results.md" | sed -E 's/\(Fig\. ([0-9]+).*/\1/' | sort -u | wc -l)"
echo "Embedded image tags:    $(grep -c '^!\[Figure ' "$DRAFT_2/02_results.md")"

# Check 5: manuscript.md (post-assemble) has the same K image tags inline.
echo
echo "--- manuscript.md image tag count ---"
echo "Image tags in manuscript.md: $(grep -c '^!\[Figure ' "$DRAFT_2/manuscript.md")"

# Check 6: docx has K Pictures + K Captions.
echo
echo "--- docx inspection ---"
python3 << 'PY'
import os, sys
from docx import Document
d_path = os.path.expanduser("~/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2/manuscript.docx")
d = Document(d_path)
print(f"  size:              {os.path.getsize(d_path):,} bytes")
print(f"  paragraphs:        {len(d.paragraphs):,}")
print(f"  inline shapes:     {len(d.inline_shapes)}  (= figures embedded)")
captions = [p for p in d.paragraphs if p.style.name == 'Caption']
print(f"  Caption paragraphs: {len(captions)}")
for p in captions:
    print(f"    [{p.style.name}] {p.text[:90]}")
PY

# Check 7: cumulative cost.
echo
echo "--- cumulative cost ---"
python3 \
    "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools/paper_writer_helpers.py" \
    cumulative-cost "$DRAFT_2"

# Check 8: validator pass/fail summary.
echo
echo "--- audit/validation.json summary ---"
python3 -c "import json; v = json.load(open('$DRAFT_2/audit/validation.json')); print(json.dumps(v.get('summary', {}), indent=2))"

# Check 9: next_actions.md figures-manifest section (should match Check 2).
echo
echo "--- next_actions.md Figures-manifest section ---"
awk '/^## Figures-manifest/{p=1} p && /^## /{if(NR>1 && !/Figures-manifest/) exit} p' "$DRAFT_2/next_actions.md"

EOF

echo
echo "=== Decision criteria ==="
cat <<'EOF'

GREEN (ship v0.3.0):
  - manifest row count matches results.v1's "figures selected: K" closing message
  - 0 WARN in audit/figures_manifest_warnings.txt
  - K embedded image tags in 02_results.md
  - K image tags in manuscript.md
  - K inline shapes + K Caption paragraphs in manuscript.docx
  - Captions visually correct (REPORT-derived strings)
  - Cumulative cost ≤ $7
  - audit/validation.json summary: M1-M10 pass (or known-residual M10 only)

YELLOW (patch and re-test):
  - 1-2 WARN in figures_manifest_warnings.txt that are real signal
  - Image-tag count off by 1-2 (likely sentence-end heuristic edge case)
  - Caption rendering quirk in docx (e.g., Caption style absent in template)

RED (debug before ship):
  - Manifest not emitted (results.v1 prompt edit didn't take)
  - 0 image tags injected (phase_embed_figures didn't run)
  - docx has 0 Pictures (assemble_docx didn't pick up image tags)
  - Cumulative cost > $10 (cost cap should have caught this)

If RED: do NOT ship. Capture findings in
spike/beril-paper-writer-skill-draft/smoke-test/v0_3_retest_findings.md
and we patch.
EOF

echo
echo "=== Backup recommendation ==="
echo "If draft_2 already exists from a prior test, back it up first:"
echo "  mv \"$DRAFT_2\" \"${DRAFT_2}.pre_v0_3_retest.bak\""
echo
echo "After successful ship, the backup can be removed."
