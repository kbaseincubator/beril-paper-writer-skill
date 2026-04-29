#!/bin/bash
# v0.4 retest RECOVERY runbook (Option B from the diagnosis).
#
# Created: 2026-04-28.
# Context: the 2026-04-28 Phase 5 retest exposed an LLM-emission drift —
# results.v1 wrote `figures_manifest.tsv` with `figures/`-prefixed values
# in the filename and inventory_lookup_name columns. All downstream
# lookups (descriptor, captions, embed) are keyed by basename, so the
# prefix silently broke caption_synthesis (all 10 figures fell through
# the sufficiency gate with empty descriptors), embed_figures (filename-
# derived captions instead of REPORT-derived), and Phase 3 description
# rendering (no panel structure for any figure).
#
# Patch: defensive Path().name normalization in _parse_figures_manifest
# (paper_writer_helpers.py:~1684) + WARN-on-detect in
# check_figures_manifest.py. Idempotent on basename-only manifests; v0.3
# happy path unchanged. 3 new regression tests in test_embed_figures.py.
#
# This runbook recovers draft_3 by:
#   1. Reinstalling pipx package (picks up the patch).
#   2. Deleting LLM-synthesized captions + metadata + bundles (forces
#      phase_caption_synthesis to re-run from scratch).
#   3. Stripping image tags + Description paragraphs from 02_results.md
#      (forces phase_embed_figures to re-inject with correct captions).
#   4. Resetting state.json from "review" → "drafting" (re-enters the
#      main case block).
#   5. Re-running `beril-paper-writer continue draft_3`.
#
# Expected cost: ~$0.62 caption_synthesis (now ~7 figures with proper
# sufficiency gate, not 10) + potential review_rewrite re-loop (~$1.20
# if rewrite hits hard cap fresh; $0 if existing rewrite_summary.txt
# short-circuits). Total ~$0.62-1.82. Defensive cap: --max-cost-usd 5.

set -euo pipefail

# === Paths ===
SKILL_SRC="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter"
DRAFT_3="$PROJECT_ROOT/papers/draft_3"

cd "$SKILL_SRC"

echo "=== Pre-flight: verify draft_3 exists + has the 'figures/' prefix bug ==="
if [[ ! -d "$DRAFT_3" ]]; then
    echo "ERROR: draft_3 not found at $DRAFT_3" >&2
    exit 1
fi
echo "  ✓ draft_3 at $DRAFT_3"

if grep -q "^[0-9].*figures/" "$DRAFT_3/figures_manifest.tsv" 2>/dev/null; then
    echo "  ✓ Confirmed: manifest has 'figures/' prefix (the bug we're recovering from)"
    head -3 "$DRAFT_3/figures_manifest.tsv" | sed 's/^/    /'
else
    echo "  WARN: no 'figures/' prefix in manifest. Either already-recovered or"
    echo "        the prefix bug isn't present here. Re-confirm before proceeding."
fi
echo

echo "=== Pre-flight: run unit suite to confirm patch is in working tree ==="
python3 -m pytest tests/unit/test_embed_figures.py::TestManifestPrefixNormalization -v 2>&1 | tail -8
echo

echo "=== Pre-flight: confirm patch landed in installed pipx version ==="
PIPX_VENV="$HOME/.local/pipx/venvs/beril-paper-writer-skill"
if [[ ! -d "$PIPX_VENV" ]]; then
    echo "ERROR: pipx venv not found at $PIPX_VENV — adjust to your install path" >&2
    exit 1
fi
"$PIPX_VENV/bin/python" -c "
import importlib, sys
sys.path.insert(0, '.')
from beril_paper_writer.skill.tools import paper_writer_helpers as h
import inspect
src = inspect.getsource(h._parse_figures_manifest)
if 'Path(cells[1].strip()).name' in src and 'Path(cells[2].strip()).name' in src:
    print('  ✓ Patch detected in pipx-installed paper_writer_helpers.py')
else:
    print('  ✗ Patch NOT detected — reinstall required (next step)')
    sys.exit(1)
" 2>&1 || REINSTALL_NEEDED=1
echo

cat <<'EOF'
=== Manual steps (run after reviewing the diff above) ===

# ---------------------------------------------------------------------------
# Step 1: Reinstall pipx package (forces venv to pick up patched
#         paper_writer_helpers.py + check_figures_manifest.py).
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force --editable .
beril-paper-writer --version    # still 0.3.0 (no version bump for the patch)

# Verify the patch is now live in the installed venv:
~/.local/pipx/venvs/beril-paper-writer-skill/bin/python -c "
import inspect
from beril_paper_writer.skill.tools import paper_writer_helpers as h
src = inspect.getsource(h._parse_figures_manifest)
assert 'Path(cells[1].strip()).name' in src, 'patch missing'
assert 'Path(cells[2].strip()).name' in src, 'patch missing'
print('OK: patch is live')
"

# ---------------------------------------------------------------------------
# Step 2: Clean draft_3 LLM-synthesized outputs so caption_synthesis
#         re-runs from scratch with the patched parser.
# ---------------------------------------------------------------------------

DRAFT_3="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3"

# Backup the bad-state artifacts before deletion (small safety net).
mkdir -p "$DRAFT_3/audit/.recovery_backup"
mv "$DRAFT_3/audit/figure_caption_"*.md \
   "$DRAFT_3/audit/figure_caption_"*.invoke.metadata.json \
   "$DRAFT_3/audit/figure_caption.v1.metadata.json" \
   "$DRAFT_3/audit/.recovery_backup/" 2>/dev/null || true
[[ -d "$DRAFT_3/audit/caption_bundles" ]] && \
    mv "$DRAFT_3/audit/caption_bundles" "$DRAFT_3/audit/.recovery_backup/" || true

# ---------------------------------------------------------------------------
# Step 3: Strip image tags + Description paragraphs from 02_results.md.
#         phase_embed_figures has idempotency on existing tags
#         (_EMBEDDED_FIGURE_RE) — already-embedded figures with WRONG
#         captions won't be re-embedded, so we have to remove them first.
#         Phase 3's italic *Description: ...* paragraphs go too.
# ---------------------------------------------------------------------------

python3 <<PY
import re
from pathlib import Path
section = Path("$DRAFT_3/02_results.md")
text = section.read_text(encoding="utf-8")

# Strip Markdown image tags + the optional italic Description paragraph
# that v0.4 Phase 3 emits immediately after each Picture.
embedded_re = re.compile(
    r"\n\n!\[Figure\s+\d+:[^\]]*\]\(figures/[^)]+\)"
    r"(?:\n\n\*Description:[^*]*\*)?",
    flags=re.DOTALL,
)
new_text, n = embedded_re.subn("", text)
section.write_text(new_text, encoding="utf-8")
print(f"Stripped {n} embedded image+description blocks from 02_results.md")
PY

# Sanity-check the section still has the (Fig. N) callouts intact (they
# drive re-injection):
echo
echo "(Fig. N) callouts surviving in 02_results.md:"
grep -oE '\(Fig\. [0-9]+[A-Z]?\)' "$DRAFT_3/02_results.md" | sort -u

# ---------------------------------------------------------------------------
# Step 4: Reset state.json's phase from "review" back to "drafting" so
#         the orchestrator re-enters the main case block. This re-runs
#         phase_check_figures_manifest → phase_caption_synthesis (NEW
#         outputs) → phase_check_caption_provenance → phase_embed_figures
#         → phase_check_scope/overclaim → phase_assemble → repair → review.
# ---------------------------------------------------------------------------

python3 <<PY
import json
from pathlib import Path
state_path = Path("$DRAFT_3/state.json")
state = json.loads(state_path.read_text(encoding="utf-8"))
state["phase"] = "drafting"
state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
print(f"state.json phase reset to 'drafting'")
PY

# ---------------------------------------------------------------------------
# Step 5: (Optional but recommended) delete review files so phase_review
#         re-runs against the corrected manuscript. Cost: ~$0.38 + $0.45
#         for review pass 1 + rewrite pass 1. If you skip this, the
#         existing reviews stay in place and the rewrite loop applies
#         them to the new manuscript text — usually fine but reviews
#         were against filename-derived captions that no longer exist.
# ---------------------------------------------------------------------------

# Decide: delete reviews (forces fresh review pass) OR keep (faster, but
# review findings reference the old text). For ship-quality recovery,
# delete and re-run:

mkdir -p "$DRAFT_3/.recovery_backup"
mv "$DRAFT_3/reviews" "$DRAFT_3/.recovery_backup/" 2>/dev/null || true
[[ -f "$DRAFT_3/audit/rewrite_summary.txt" ]] && \
    mv "$DRAFT_3/audit/rewrite_summary.txt" "$DRAFT_3/.recovery_backup/" || true

# ---------------------------------------------------------------------------
# Step 6: Re-run continue. With --max-cost-usd 5 as a defensive cap:
#         expected $0.62-1.82, cap should NOT trip.
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev"
beril-paper-writer continue \
    "$DRAFT_3" \
    --max-cost-usd 5

# ---------------------------------------------------------------------------
# Step 7: Re-run docx assembly.
# ---------------------------------------------------------------------------

beril-paper-writer assemble "$DRAFT_3" --format docx

EOF

echo
echo "=== After Step 7 — re-run the gate verification commands from v0_4_retest_runbook.sh ==="
echo
echo "Specifically, verify:"
echo "  • Gate 3: caption_synthesis stats — expect ~3 deterministic / ~7 llm"
echo "    (was 0/10 in the buggy run)"
echo "  • Gate 5: 'WARN: inventory has no entry for...' should NOT appear"
echo "    in audit/embed_figures.log"
echo "  • Gate 6: docx Caption paragraphs include real REPORT-derived"
echo "    captions (e.g. 'Annotation breakdown by organism' not"
echo "    'Annotation breakdown')"
echo "  • Gate 4: caption-provenance check — figure 5's '0.62' fabrication"
echo "    may persist (the LLM regenerates fresh, may or may not invent"
echo "    a similar number)"
echo
echo "If all green, proceed to Phase 6 ship. If new issues surface,"
echo "capture in smoke-test/v0_4_retest_findings.md and we patch."
