#!/bin/bash
# v0.2 Tier 3 retest runbook — targeted on existing draft_1.
#
# Created: 2026-04-27.
# Tests: phase_repair_validators (no-op since 0 fails) +
#        phase_review_rewrite (1 Critical to clear: C1 Abstract overclaim).
# Cost estimate: ~$5–10 in LLM (1–2 rewrite cycles × 1 abstract rewrite +
#                1–2 fresh reviewer passes; v0.1.0 ship retest reference: $4.20 / 30 min).
# Side effects: modifies draft_1 in place; backs up to a sibling directory first.
#
# Inspect the script BEFORE running. Paste the run command (last line) into your shell.

set -euo pipefail

# === Paths ===
SKILL_SRC="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
PAPER_WRITER_SH="$SKILL_SRC/src/beril_paper_writer/skill/tools/paper_writer.sh"
HELPERS="$SKILL_SRC/src/beril_paper_writer/skill/tools/paper_writer_helpers.py"
DRAFT="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_1"
BACKUP="${DRAFT}.pre_v0_2_retest.bak"

if [[ ! -f "$PAPER_WRITER_SH" ]]; then
    echo "ERROR: paper_writer.sh not at $PAPER_WRITER_SH" >&2
    exit 1
fi
if [[ ! -d "$DRAFT" ]]; then
    echo "ERROR: draft_1 not at $DRAFT" >&2
    exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: 'claude' CLI not on PATH; orchestrator will fail" >&2
    exit 1
fi

# === Step 1: pre-run snapshot ===
echo "=== Pre-run state ==="
python3 -c "
import json
d = json.load(open('$DRAFT/state.json'))
print(f'  phase: {d.get(\"phase\")}')
print(f'  draft_number: {d.get(\"draft_number\")}')
"
echo "  validation: $(python3 -c "import json; v=json.load(open('$DRAFT/audit/validation.json')); print(v['summary'])")"
echo "  reviews: $(ls "$DRAFT/reviews/" 2>/dev/null | tr '\n' ' ')"
echo "  review_1 critical count: $(python3 "$HELPERS" count-review-criticals "$DRAFT/reviews/draft_1_review_1.md")"
echo

# === Step 2: backup ===
if [[ -d "$BACKUP" ]]; then
    echo "ERROR: backup already exists at $BACKUP" >&2
    echo "  Move or delete it before re-running this script." >&2
    exit 1
fi
echo "Backing up draft_1 → $BACKUP"
cp -r "$DRAFT" "$BACKUP"
echo "✓ Backup complete"
echo

# === Step 3: flip phase to 'review' so resume hits the rewrite-loop path ===
echo "Flipping state.json phase: assembled → review"
python3 -c "
import json
p = '$DRAFT/state.json'
d = json.load(open(p))
d['phase'] = 'review'
json.dump(d, open(p, 'w'), indent=2)
print('  ✓ state.json phase=review')
"
echo

# === Step 4: invoke the orchestrator's resume path ===
echo "=== Invoking paper_writer.sh resume on draft_1 ==="
echo "Watch for:"
echo "  - phase_repair_validators: should be no-op (0 validator failures)"
echo "  - phase_review_rewrite: dispatch rewrite.v1 to abstract for C1"
echo "  - re-assemble + reviewer pass 2 (writes draft_1_review_2.md)"
echo "  - if criticals persist: pass 2 + reviewer pass 3"
echo "  - emit_review_handoff at end"
echo ""
echo "Press Ctrl-C at any time; backup at $BACKUP can be restored via"
echo "  rm -rf '$DRAFT' && cp -r '$BACKUP' '$DRAFT'"
echo ""

bash "$PAPER_WRITER_SH" resume "$DRAFT"

echo
echo "=== Post-run inspection ==="
echo
echo "--- rewrite_summary.txt ---"
cat "$DRAFT/audit/rewrite_summary.txt" 2>/dev/null || echo "(no rewrite_summary.txt — phase didn't run?)"
echo
echo "--- Critical-finding count over the loop ---"
for r in "$DRAFT/reviews/"draft_1_review_*.md; do
    n=$(python3 "$HELPERS" count-review-criticals "$r")
    echo "  $(basename "$r"): $n Critical finding(s)"
done
echo
echo "--- Diff: original 05_abstract.md vs post-rewrite ---"
diff -u "$BACKUP/05_abstract.md" "$DRAFT/05_abstract.md" 2>&1 || true
echo
echo "--- Cost aggregate (this resume only — note: includes existing review_1 cost from v0.1.0 ship) ---"
python3 "$HELPERS" aggregate-metadata "$DRAFT" 2>&1 | tail -5
echo
echo "--- next_actions.md REPAIR_MODE + Review-rewrite sections ---"
sed -n '/^## REPAIR_MODE outcomes/,/^## Overclaim/p' "$DRAFT/next_actions.md" | head -40
echo
echo "Backup at: $BACKUP"
echo "  To restore: rm -rf '$DRAFT' && cp -r '$BACKUP' '$DRAFT'"
echo "  To discard backup: rm -rf '$BACKUP'"
