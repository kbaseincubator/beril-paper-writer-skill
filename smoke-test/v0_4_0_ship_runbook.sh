#!/bin/bash
# v0.4.0 ship runbook — caption-richness tier (Tier 8 + Source 4)
#
# Created: 2026-04-28.
# Per CLAUDE.md "Do not push to ArkinLaboratory skill repos without
# confirmation": review every `git` line below. Commit message at
# .commit-message-v0_4_0.txt. Live retest already passed
# (smoke-test/v0_4_retest_runbook.sh + recovery + rerender;
# functional_dark_matter draft_3 has clean ICMJE-form captions, 426
# unit tests pass, validators M1-M10 pass).

set -euo pipefail

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"

echo "=== Pre-flight: orphaned-lock check ==="
if [[ -f .git/index.lock ]]; then
    echo "Found .git/index.lock — remove with: rm .git/index.lock"
    exit 1
fi
echo "  no orphaned lock"

echo
echo "=== Pre-flight: confirm version bump 0.3.0 → 0.4.0 ==="
grep -E '^version' pyproject.toml
grep -E '__version__' src/beril_paper_writer/__init__.py

echo
echo "=== Pre-flight: confirm v0.4.0 commit message present ==="
ls -lh .commit-message-v0_4_0.txt

echo
echo "=== Pre-flight: confirm v0.4 surfaces present ==="
test -f RELEASE_NOTES_v0_4.md && echo "  ✓ RELEASE_NOTES_v0_4.md"
test -f smoke-test/v0_4_punch_list.md && echo "  ✓ smoke-test/v0_4_punch_list.md"
test -f smoke-test/v0_4_retest_runbook.sh && echo "  ✓ smoke-test/v0_4_retest_runbook.sh"
test -f smoke-test/v0_4_retest_recovery_runbook.sh && echo "  ✓ smoke-test/v0_4_retest_recovery_runbook.sh"
test -f smoke-test/v0_4_rerender_runbook.sh && echo "  ✓ smoke-test/v0_4_rerender_runbook.sh"
test -f smoke-test/v0_4_0_ship_runbook.sh && echo "  ✓ smoke-test/v0_4_0_ship_runbook.sh"
test -f src/beril_paper_writer/skill/prompts/figure_caption.v1.md && echo "  ✓ prompts/figure_caption.v1.md"
test -f src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  ✓ tools/check_caption_provenance.py"
test -f tests/unit/test_check_caption_provenance.py && echo "  ✓ tests/unit/test_check_caption_provenance.py"
grep -q 'phase_caption_synthesis()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_caption_synthesis wired"
grep -q 'phase_check_caption_provenance()' src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  ✓ phase_check_caption_provenance wired"
grep -q 'class CaptionDescriptor' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ CaptionDescriptor (Phase 1b)"
grep -q '_extract_plot_calls' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ matplotlib AST extraction (Phase 2)"
grep -q 'inventory_schema_version: 2' src/beril_paper_writer/skill/tools/extract_figures.py && echo "  ✓ v2 inventory schema header"
grep -q 'Phase 5c: Source 4 loop-closure' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ Phase 5c Source 4 closure"
grep -q '_NOTEBOOK_BOILERPLATE_KEYWORDS_RE' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ Phase 5b boilerplate-strip keyword regex"
grep -q '_INLINE_BOILERPLATE_CASCADE_RE' src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  ✓ Phase 5b inline-cascade strip"

echo
echo "=== Pre-flight: bash + Python sanity ==="
bash -n src/beril_paper_writer/skill/tools/paper_writer.sh && echo "  paper_writer.sh: bash -n OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/paper_writer_helpers.py && echo "  paper_writer_helpers.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/check_caption_provenance.py && echo "  check_caption_provenance.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/extract_figures.py && echo "  extract_figures.py: compile OK"
python3 -m py_compile src/beril_paper_writer/skill/tools/assemble_docx.py && echo "  assemble_docx.py: compile OK"

echo
echo "=== Pre-flight: full unit suite (must be all-green before ship) ==="
# Note: pin to a known-compatible Python (3.10/3.12) — Python 3.14 has
# 8 pre-existing test-collection errors unrelated to v0.4. Use the
# pipx venv's Python or a 3.12 venv for this gate.
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

# 1. Stage the v0.4.0 changes:
git add \
    pyproject.toml \
    src/beril_paper_writer/__init__.py \
    src/beril_paper_writer/skill/prompts/figure_caption.v1.md \
    src/beril_paper_writer/skill/tools/check_caption_provenance.py \
    src/beril_paper_writer/skill/tools/extract_figures.py \
    src/beril_paper_writer/skill/tools/paper_writer.sh \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    src/beril_paper_writer/skill/tools/check_figures_manifest.py \
    src/beril_paper_writer/skill/tools/assemble_docx.py \
    tests/unit/test_check_caption_provenance.py \
    tests/unit/test_extract_figures.py \
    tests/unit/test_embed_figures.py \
    tests/unit/test_assemble_docx.py \
    RELEASE_NOTES_v0_4.md \
    smoke-test/v0_4_punch_list.md \
    smoke-test/v0_4_dev_commit_runbook.sh \
    smoke-test/v0_4_retest_runbook.sh \
    smoke-test/v0_4_retest_recovery_runbook.sh \
    smoke-test/v0_4_rerender_runbook.sh \
    smoke-test/v0_4_0_ship_runbook.sh \
    .commit-message-v0_4_0.txt

# 2. Verify staged set:
git diff --cached --stat

# 3. Commit with the prepared message:
git commit -F .commit-message-v0_4_0.txt

# 4. Tag the release:
git tag -a v0.4.0 -m "v0.4.0 — caption-richness tier (Tier 8 + Source 4 LLM synthesis)"

# 5. Push to ArkinLaboratory:
git remote -v   # confirm 'origin' points at ArkinLaboratory/beril-paper-writer-skill
git push origin main
git push origin v0.4.0
EOF
