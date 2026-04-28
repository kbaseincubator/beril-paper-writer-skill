#!/bin/bash
# v0.4-dev commit runbook — pre-Phase-5 checkpoint
#
# Created: 2026-04-28.
# Per CLAUDE.md "no git writes in sandbox bash on host-mounted repos"
# (memory feedback_no_git_writes_in_sandbox.md): all git commands here
# are intended to run from your Mac shell, NOT the sandbox. Review every
# `git` line below before executing. Commit message at
# .commit-message-v0_4_dev.txt.
#
# Scope: single commit covering Phases 0 through 4c. Pre-ship; no
# version bump; no tag; no push (push is your decision after Phase 5).

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — remove with: rm .git/index.lock"
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version is still 0.3.0 (no bump for dev commit) ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm v0.4-dev commit message present ==="
ls -lh .commit-message-v0_4_dev.txt

echo
echo "=== Pre-flight: confirm v0.4 surfaces present ==="
test -f smoke-test/v0_4_punch_list.md && echo "  ✓ smoke-test/v0_4_punch_list.md"
test -f src/beril_paper_writer/skill/prompts/figure_caption.v1.md && echo "  ✓ prompts/figure_caption.v1.md"
test -f src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  ✓ tools/check_caption_provenance.py"
test -f tests/unit/test_check_caption_provenance.py && echo "  ✓ tests/unit/test_check_caption_provenance.py"
grep -q 'phase_caption_synthesis()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_caption_synthesis wired"
grep -q 'phase_check_caption_provenance()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_check_caption_provenance wired"
grep -q 'cmd_build_caption_bundles' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ build-caption-bundles helper"
grep -q 'cmd_compute_caption_stats' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ compute-caption-stats helper"
grep -q 'inventory_schema_version: 2' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ v2 inventory schema header emitted"
grep -q 'class CaptionDescriptor' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ CaptionDescriptor dataclass"
grep -q '_extract_plot_calls' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ matplotlib AST extraction (Phase 2)"

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  check_caption_provenance.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/extract_figures.py && echo "  extract_figures.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  assemble_docx.py: compile OK"

echo
echo "=== Pre-flight: full unit suite (must be all-green before commit) ==="
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

# 1. Stage the v0.4-dev changes (Phases 0-4c):
git add \
    src/beril_paper_writer/skill/prompts/figure_caption.v1.md \
    src/beril_paper_writer/skill/tools/check_caption_provenance.py \
    src/beril_paper_writer/skill/tools/extract_figures.py \
    src/beril_paper_writer/skill/tools/paper_writer.sh \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    src/beril_paper_writer/skill/tools/assemble_docx.py \
    tests/unit/test_check_caption_provenance.py \
    tests/unit/test_extract_figures.py \
    tests/unit/test_embed_figures.py \
    tests/unit/test_assemble_docx.py \
    smoke-test/v0_4_punch_list.md \
    smoke-test/v0_4_dev_commit_runbook.sh \
    .commit-message-v0_4_dev.txt

# 2. Verify staged set:
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_4_dev.txt

# 4. (Optional) Inspect the commit:
git log -1 --stat

# DO NOT push yet. Phase 5 (live retest) follows; if Phase 5 reveals
# issues, you'll want to amend or revert without having pushed. Push
# happens after Phase 6's v0.4.0 ship commit.
EOF
