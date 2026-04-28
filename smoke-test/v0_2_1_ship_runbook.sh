#!/bin/bash
# v0.2.1 ship runbook — three same-day bug fixes (Bug 1: stale review reuse;
# Bug 2: phase_assemble log wording; Bug 3: emit_review_handoff picks wrong file).
#
# Created: 2026-04-27.
# Per CLAUDE.md "Do not push to ArkinLaboratory skill repos without confirmation":
# review every `git` line below. Commit message at .commit-message-v0_2_1.txt.

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — remove with: rm .git/index.lock"
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version bump 0.2.0 → 0.2.1 ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm v0.2.1 commit message present ==="
ls -lh .commit-message-v0_2_1.txt

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"

echo
echo "=== Pre-flight: confirm fixes are in place ==="
echo "Bug 1 — stale review reuse fix:"
grep -B 1 -A 1 "rm -f \"\$next_review_path\"" src/beril_paper_writer/skill/tools/paper_writer.sh | head -5
echo
echo "Bug 2 — phase_assemble log wording:"
grep "deferring to phase_repair_validators" src/beril_paper_writer/skill/tools/paper_writer.sh
echo
echo "Bug 3 — review-path numeric sort:"
grep -A 1 "v0.2.1 fix: pick the LATEST review" src/beril_paper_writer/skill/tools/paper_writer.sh | head -3

echo
echo "=== Pre-flight: review changes ==="
git status --short
echo
echo "Files staged for commit (review with):"
echo "  git diff HEAD"

echo
echo "=== Manual steps (run after reviewing the diff) ==="
cat <<'EOF'

# 1. Stage the v0.2.1 changes:
git add \
    pyproject.toml \
    src/beril_paper_writer/__init__.py \
    src/beril_paper_writer/skill/tools/paper_writer.sh \
    RELEASE_NOTES_v0_2.md \
    smoke-test/v0_2_1_ship_runbook.sh

# 2. Verify staged set:
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_2_1.txt

# 4. Tag the patch release:
git tag -a v0.2.1 -m "v0.2.1 — three live-test-surfaced bugs fixed"

# 5. Push to ArkinLaboratory:
git remote -v   # confirm 'origin' points at ArkinLaboratory/beril-paper-writer-skill
git push origin main
git push origin v0.2.1

# 6. Reinstall via pipx:
pipx upgrade beril-paper-writer-skill
beril-paper-writer --version   # should print 0.2.1

# 7. Re-install skill into BERIL_ROOT:
cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended"
beril-paper-writer install-skill .
EOF

echo
echo "=== After ship — optional cleanup ==="
echo "The .pre_v0_2_partial.bak in draft_1 can be removed once you're satisfied with v0.2.1:"
echo "  rm -rf $HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_1.pre_v0_2_partial.bak"
