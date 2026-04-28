#!/bin/bash
# v0.3.0 ship runbook — figures + docx assembly tier
#
# Created: 2026-04-27.
# Per CLAUDE.md "Do not push to ArkinLaboratory skill repos without
# confirmation": review every `git` line below. Commit message at
# .commit-message-v0_3_0.txt. Live retest already passed
# (smoke-test/v0_3_retest_runbook.sh; $6.49; 10 Pictures in docx).

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — remove with: rm .git/index.lock"
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version bump 0.2.1 → 0.3.0 ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm v0.3.0 commit message present ==="
ls -lh .commit-message-v0_3_0.txt

echo
echo "=== Pre-flight: confirm v0.3 surfaces present ==="
test -f RELEASE_NOTES_v0_3.md && echo "  ✓ RELEASE_NOTES_v0_3.md"
test -f smoke-test/v0_3_punch_list.md && echo "  ✓ smoke-test/v0_3_punch_list.md"
test -f smoke-test/v0_3_retest_runbook.sh && echo "  ✓ smoke-test/v0_3_retest_runbook.sh"
test -f src/beril_paper_writer/skill/tools/check_figures_manifest.py && echo "  ✓ tools/check_figures_manifest.py"
test -f src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  ✓ tools/assemble_docx.py"
test -f tests/unit/test_assemble_docx.py && echo "  ✓ tests/unit/test_assemble_docx.py"
test -f tests/unit/test_embed_figures.py && echo "  ✓ tests/unit/test_embed_figures.py"
grep -q 'D-025' DECISIONS.md && echo "  ✓ D-025 in DECISIONS.md"
grep -q 'D-026' DECISIONS.md && echo "  ✓ D-026 in DECISIONS.md"
grep -q 'phase_embed_figures()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_embed_figures wired"
grep -q 'phase_check_figures_manifest()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_check_figures_manifest wired"

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/check_figures_manifest.py && echo "  check_figures_manifest.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  assemble_docx.py: compile OK"

echo
echo "=== Pre-flight: full unit suite (must be all-green before ship) ==="
python3 -m pytest tests/unit/ 2>&1 | tail -3

echo
echo "=== Pre-flight: review changes ==="
git status --short
echo
echo "Files staged for commit (review with):"
echo "  git diff HEAD"

echo
echo "=== Manual steps (run after reviewing the diff) ==="
cat <<'EOF'

# 1. Stage the v0.3.0 changes:
git add \
    pyproject.toml \
    src/beril_paper_writer/__init__.py \
    src/beril_paper_writer/commands/assemble.py \
    src/beril_paper_writer/skill/prompts/results.v1.md \
    src/beril_paper_writer/skill/tools/paper_writer.sh \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    src/beril_paper_writer/skill/tools/assemble_docx.py \
    src/beril_paper_writer/skill/tools/check_figures_manifest.py \
    tests/unit/test_cli.py \
    tests/unit/test_assemble_docx.py \
    tests/unit/test_embed_figures.py \
    DECISIONS.md \
    RELEASE_NOTES_v0_3.md \
    smoke-test/v0_3_punch_list.md \
    smoke-test/v0_3_retest_runbook.sh \
    smoke-test/v0_3_0_ship_runbook.sh \
    .commit-message-v0_3_0.txt

# 2. Verify staged set:
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_3_0.txt

# 4. Tag the release:
git tag -a v0.3.0 -m "v0.3.0 — figures + docx assembly tier"

# 5. Push to ArkinLaboratory:
git remote -v   # confirm 'origin' points at ArkinLaboratory/beril-paper-writer-skill
git push origin main
git push origin v0.3.0

# 6. Reinstall via pipx (replaces the editable install used during retest):
pipx install --force git+ssh://git@github.com/ArkinLaboratory/beril-paper-writer-skill.git@v0.3.0
beril-paper-writer --version   # should print 0.3.0

# 7. Re-install skill into BERIL_ROOT:
cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended"
beril-paper-writer install-skill .

EOF

echo
echo "=== After ship ==="
echo "draft_2 from the live retest is at:"
echo "  $HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2"
echo "Keep it as a v0.3.0 reference, or remove if you don't need it:"
echo "  rm -rf $HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_2"
echo
echo "Two cosmetic v0.3.1 follow-ups are filed in RELEASE_NOTES_v0_3.md"
echo "and v0.3 punch list 'Backlog leftover for v0.4+' section:"
echo "  1. 'Copied 0 figure(s)' log line in phase_results (misleading in v0.3)"
echo "  2. FIGURES_OUT_DIR not explicitly passed to results.v1 user_prompt"
echo
echo "Both are 30-min audit-pass fixes; bundle into v0.3.1 if discovered"
echo "during normal use, or address as part of v0.4 caption-richness work."
