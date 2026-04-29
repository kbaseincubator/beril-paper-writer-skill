#!/bin/bash
# v0.5.0 ship runbook — caption-quality tightening
#
# Created: 2026-04-29.
# Per CLAUDE.md "Do not push to ArkinLaboratory skill repos without
# confirmation": review every `git` line below. Commit message at
# .commit-message-v0_5_0.txt.
#
# v0.5 is a focused point release: switch sufficiency gate to the
# aggressive _strip_prose_for_inline (boilerplate-heavy prose now
# correctly fails the gate and routes to LLM); add panel-count-scaled
# max_words formula (multi-panel figures get more word budget).
# Live validation of v0.5 against draft_3 is recommended but not
# strictly required pre-ship — the changes are well-tested in
# isolation (430 unit tests pass).

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — remove with: rm .git/index.lock"
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version bump 0.4.0 → 0.5.0 ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm v0.5.0 commit message present ==="
ls -lh .commit-message-v0_5_0.txt

echo
echo "=== Pre-flight: confirm v0.5 surfaces present ==="
test -f RELEASE_NOTES_v0_5.md && echo "  ✓ RELEASE_NOTES_v0_5.md"
test -f smoke-test/v0_5_punch_list.md && echo "  ✓ smoke-test/v0_5_punch_list.md"
test -f smoke-test/v0_5_0_ship_runbook.sh && echo "  ✓ smoke-test/v0_5_0_ship_runbook.sh"
grep -q '_caption_max_words' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ _caption_max_words helper"
grep -q '_strip_prose_for_inline(prose)' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ sufficiency gate uses _strip_prose_for_inline"

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"

echo
echo "=== Pre-flight: full unit suite ==="
# Note: pin to a known-compatible Python (3.10/3.12) — Python 3.14 has
# 8 pre-existing test-collection errors unrelated to v0.5. Use the pipx
# venv's Python (which sees 426/426 + v0.5 additions = 430) or another
# 3.10/3.12 venv.
python3 -m pytest tests/unit/ 2>&1 | tail -3

echo
echo "=== Pre-flight: review changes ==="
git status --short

echo
echo "=== Manual steps (run after reviewing the diff) ==="
cat <<'EOF'

# 1. Stage the v0.5.0 changes:
git add \
    pyproject.toml \
    src/beril_paper_writer/__init__.py \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    tests/unit/test_embed_figures.py \
    RELEASE_NOTES_v0_5.md \
    smoke-test/v0_5_punch_list.md \
    smoke-test/v0_5_0_ship_runbook.sh \
    .commit-message-v0_5_0.txt

# 2. Verify staged set:
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_5_0.txt

# 4. Tag the release:
git tag -a v0.5.0 -m "v0.5.0 — caption-quality tightening (boilerplate-aware gate + panel-scaled word cap)"

# 5. Push to ArkinLaboratory:
git remote -v
git push origin main
git push origin v0.5.0

EOF
