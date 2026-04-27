#!/bin/bash
# v0.2.0 ship runbook — for Adam to execute on his Mac shell.
#
# Created: 2026-04-27.
# Per CLAUDE.md "Do not push to ArkinLaboratory skill repos without
# confirmation": review every `git` line below before running. The
# commit message is in `.commit-message-v0_2_0.txt` for `git commit -F`.
#
# Pre-flight (run first):
#   1. Ensure no orphaned .git/index.lock (sandbox may have left one):
#      ls .git/index.lock 2>/dev/null && rm .git/index.lock
#   2. Confirm version files updated:
#      grep '^version' pyproject.toml                  # → version = "0.2.0"
#      grep __version__ src/beril_paper_writer/__init__.py  # → __version__ = "0.2.0"
#   3. Review .commit-message-v0_2_0.txt before committing.

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — likely orphaned from sandbox git invocation."
    echo "  Remove with: rm .git/index.lock"
    echo "  Then re-run this script."
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version bump ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm commit message present ==="
ls -lh .commit-message-v0_2_0.txt

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -c "import py_compile; py_compile.compile('src/beril_paper_writer/skill/tools/paper_writer_helpers.py', doraise=True); print('  paper_writer_helpers.py: compile OK')"
python3 -c "import py_compile; py_compile.compile('src/beril_paper_writer/skill/tools/check_scope_coherence.py', doraise=True); print('  check_scope_coherence.py: compile OK')"
python3 -c "import py_compile; py_compile.compile('src/beril_paper_writer/skill/tools/check_overclaim.py', doraise=True); print('  check_overclaim.py: compile OK')"
python3 -c "import py_compile; py_compile.compile('src/beril_paper_writer/skill/tools/check_repair_scope.py', doraise=True); print('  check_repair_scope.py: compile OK')"

echo
echo "=== Pre-flight: review changes ==="
git status --short
echo
echo "Files staged for commit (review with):"
echo "  git diff HEAD"
echo "  git diff HEAD pyproject.toml src/beril_paper_writer/__init__.py"

echo
echo "=== Manual steps (run after reviewing the diff) ==="
cat <<'EOF'

# 1. Stage everything for the v0.2 ship:
git add \
    pyproject.toml \
    src/beril_paper_writer/__init__.py \
    src/beril_paper_writer/skill/tools/paper_writer.sh \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    src/beril_paper_writer/skill/tools/check_scope_coherence.py \
    src/beril_paper_writer/skill/tools/check_overclaim.py \
    src/beril_paper_writer/skill/tools/check_repair_scope.py \
    smoke-test/v0_2_punch_list.md \
    smoke-test/v0_2_tier3_draft1_retest_runbook.sh \
    smoke-test/v0_2_0_ship_runbook.sh \
    RELEASE_NOTES_v0_2.md

# 2. Verify staged set looks right (no stray files):
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_2_0.txt

# 4. Tag the release:
git tag -a v0.2.0 -m "v0.2.0 — discipline-hardening + auto-repair tier"

# 5. Push to ArkinLaboratory (review the upstream remote name first):
git remote -v   # confirm 'origin' points at ArkinLaboratory/beril-paper-writer-skill
git push origin main         # push the commit
git push origin v0.2.0       # push the tag

# 6. Reinstall via pipx so beril-paper-writer --version reports 0.2.0:
pipx upgrade beril-paper-writer-skill
beril-paper-writer --version   # should print 0.2.0

# 7. Re-install skill into BERIL_ROOT to refresh the shipped files:
cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended"
beril-paper-writer install-skill .
EOF

echo
echo "=== After successful push, optional cleanup ==="
echo "Remove the smoke-test backup from the targeted retest if no longer needed:"
echo "  rm -rf $HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_1.pre_v0_2_retest.bak"
