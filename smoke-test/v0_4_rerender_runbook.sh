#!/bin/bash
# v0.4 Phase 5b re-render runbook (post-patch).
#
# Created: 2026-04-28.
# Context: visual review of draft_3 (post-recovery) flagged two findings:
#   1. Two-paragraph caption layout — Figure + Description rendered as
#      two separate Caption paragraphs is non-ICMJE.
#   2. Notebook-organization boilerplate — figure 8/9/10 descriptions
#      hallucinated `Purpose:`, `Approach:`, `Sections:`, REVIEW.md,
#      NBxx references, crowding out actual panel content and
#      truncating mid-word.
#
# Patches applied:
#   - paper_writer_helpers.py: combined alt-text in _embed_figures_in_text
#     (single Caption paragraph, ICMJE convention); _strip_prose_for_inline
#     filters notebook-keyword headers (`Purpose:` etc.) + project-internal
#     artifact references (REVIEW.md, NBxx).
#   - figure_caption.v1.md prompt: explicit anti-pattern with FAIL/PASS
#     example for notebook-organization boilerplate.
#
# This runbook re-renders draft_3 with the patches applied — $0 LLM cost
# because the existing audit/figure_caption_*.md captions are reused
# (not regenerated). Only embed_figures + assemble re-run, picking up
# the new layout + boilerplate-strip behavior.
#
# Note on Source 4 captions (Phase 5c closure):
#   The Phase 5c patch closes the Source 4 loop — figure_caption_<N>.md
#   files NOW flow into the manuscript (they were dead-pipeline before).
#   For figures 1-5 in draft_3 (LLM-synthesized), the docx will now show
#   the LLM's polished caption, not the descriptor's notebook_prose.
#
#   HOWEVER: those audit files were synthesized PRE-PATCH (with the old
#   prompt that didn't have the boilerplate anti-pattern). They MAY
#   contain "Purpose:" / "Approach:" etc. boilerplate that the prompt
#   now forbids. If visual review of figures 1-5 shows boilerplate,
#   regenerate them with the new prompt:
#
#     rm "$DRAFT_3"/audit/figure_caption_{1,2,3,4,5}.md
#     rm "$DRAFT_3"/audit/figure_caption.v1.metadata.json
#     # Reset state.json to "drafting"; run continue.
#     # Cost: ~$0.30 for 5 figures.
#
#   For figures 6-10 (deterministic), the Phase 5b boilerplate-strip
#   patch handles them at embed time — no regeneration needed.

set -euo pipefail

# === Paths ===
SKILL_SRC="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
PROJECT_ROOT="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter"
DRAFT_3="$PROJECT_ROOT/papers/draft_3"

cd "$SKILL_SRC"

echo "=== Pre-flight: patches in working tree ==="
grep -q "Phase 5b (visual-review patch)" \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    && echo "  ✓ Phase 5b: _embed_figures_in_text combined-alt-text patch"
grep -q "_NOTEBOOK_BOILERPLATE_KEYWORDS_RE" \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    && echo "  ✓ Phase 5b: _strip_prose_for_inline boilerplate filter"
grep -q "Notebook-organization boilerplate exclusion" \
    src/beril_paper_writer/skill/prompts/figure_caption.v1.md \
    && echo "  ✓ Phase 5b: figure_caption.v1.md anti-pattern section"
grep -q "Phase 5c: Source 4 loop-closure" \
    src/beril_paper_writer/skill/tools/paper_writer_helpers.py \
    && echo "  ✓ Phase 5c: Source 4 closure (LLM caption used in manuscript)"

echo
echo "=== Pre-flight: unit suite ==="
python3 -m pytest tests/unit/ 2>&1 | tail -3

echo
cat <<'EOF'
=== Manual steps ===

# ---------------------------------------------------------------------------
# Step 1: Reinstall pipx package to pick up the patches.
# ---------------------------------------------------------------------------

cd "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft"
pipx install --force --editable .
beril-paper-writer --version    # still 0.3.0 (no version bump)

# Verify the patch is live in the installed venv:
~/.local/pipx/venvs/beril-paper-writer-skill/bin/python -c "
import inspect
from beril_paper_writer.skill.tools import paper_writer_helpers as h
src = inspect.getsource(h._embed_figures_in_text)
assert 'Phase 5b' in src, 'combined-alt-text patch missing'
src2 = inspect.getsource(h._strip_prose_for_inline)
assert '_NOTEBOOK_BOILERPLATE_KEYWORDS_RE' in src2, 'boilerplate filter missing'
print('OK: Phase 5b patches are live')
"

# ---------------------------------------------------------------------------
# Step 2: Strip existing image tags + Description paragraphs from
#         02_results.md so the patched embed_figures injects fresh
#         combined-alt-text. Same regex as the recovery runbook.
# ---------------------------------------------------------------------------

DRAFT_3="$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3"

python3 <<PY
import re
from pathlib import Path
section = Path("$DRAFT_3/02_results.md")
text = section.read_text(encoding="utf-8")
embedded_re = re.compile(
    r"\n\n!\[Figure\s+\d+:[^\]]*\]\(figures/[^)]+\)"
    r"(?:\n\n\*Description:[^*]*\*)?",
    flags=re.DOTALL,
)
new_text, n = embedded_re.subn("", text)
section.write_text(new_text, encoding="utf-8")
print(f"Stripped {n} embedded image+description blocks from 02_results.md")
PY

# Sanity-check the (Fig. N) callouts survive:
echo
echo "(Fig. N) callouts surviving in 02_results.md:"
grep -oE '\(Fig\. [0-9]+[A-Z]?\)' "$DRAFT_3/02_results.md" | sort -u | wc -l

# ---------------------------------------------------------------------------
# Step 3: Re-run embed_figures (no LLM cost; pure operation).
# ---------------------------------------------------------------------------

beril-paper-writer-helpers() {
    "$HOME/.local/pipx/venvs/beril-paper-writer-skill/bin/python" \
        "$HOME/Documents/Claude/Projects/research-coscientist-dev/spike/beril-paper-writer-skill-draft/src/beril_paper_writer/skill/tools/paper_writer_helpers.py" \
        "$@"
}
beril-paper-writer-helpers embed-figures "$DRAFT_3"

# ---------------------------------------------------------------------------
# Step 4: Re-run assemble (concat sections + render docx).
# ---------------------------------------------------------------------------

beril-paper-writer assemble "$DRAFT_3" --format docx

# ---------------------------------------------------------------------------
# Step 5: Visual verification.
# ---------------------------------------------------------------------------

# Spot-check the new alt-text format in 02_results.md:
echo
echo "First 3 image markdown tags in 02_results.md:"
grep -E '^\!\[Figure ' "$DRAFT_3/02_results.md" | head -3 | sed 's/^/  /'

# Inspect the docx structure (uses pipx venv's Python for python-docx):
~/.local/pipx/venvs/beril-paper-writer-skill/bin/python << 'PY'
from docx import Document
import os, re
d_path = os.path.expanduser("~/Documents/Claude/Projects/research-coscientist-dev/spike/beril-extended/projects/functional_dark_matter/papers/draft_3/manuscript.docx")
d = Document(d_path)
print(f"size:                {os.path.getsize(d_path):,} bytes")
print(f"inline shapes:       {len(d.inline_shapes)}")
caps = [p for p in d.paragraphs if p.style.name == 'Caption']
print(f"Caption paragraphs:  {len(caps)}")
fig = sum(1 for p in caps if p.text.startswith("Figure "))
desc = sum(1 for p in caps if p.text.startswith("Description: "))
print(f"  'Figure N: ...':    {fig}  (expect: 10)")
print(f"  'Description: ...': {desc}  (expect: 0 — Phase 5b layout fix)")
words = [len(re.findall(r"\S+", p.text)) for p in caps]
if words:
    print(f"  word counts: min={min(words)} max={max(words)} avg={sum(words)/len(words):.0f}")
print()
print("First 5 Caption paragraphs (combined alt-text form):")
for p in caps[:5]:
    print(f"  [{p.style.name}] {p.text[:140]}")
PY

# Open the docx for visual review:
open "$DRAFT_3/manuscript.docx"

# ---------------------------------------------------------------------------
# Step 6: Visual checklist (run mentally with docx open):
# ---------------------------------------------------------------------------

# □ Each figure has ONE Caption paragraph (not two) directly below the
#   Picture.
# □ Caption form: "Figure N: <short caption>. <description>." with no
#   "Description:" label visible.
# □ Multi-panel figures (fig01 if it has panels=2) describe each panel
#   inside the Caption paragraph.
# □ Figure 8/9/10 (LLM-synthesized; previously had boilerplate) — the
#   `_strip_prose_for_inline` patch removed boilerplate AT EMBED TIME,
#   so the docx Caption paragraph should NOT contain "Purpose:",
#   "Approach:", "Sections:", or REVIEW.md / NBxx references EVEN
#   THOUGH the underlying audit/figure_caption_<N>.md may still
#   contain them (those were synthesized pre-patch).
#
# If issues remain on 8/9/10 specifically, you can re-run JUST those
# captions through Source 4 with the new prompt:
#   rm "$DRAFT_3/audit/figure_caption_8.md" "$DRAFT_3/audit/figure_caption_9.md" "$DRAFT_3/audit/figure_caption_10.md"
#   rm "$DRAFT_3/audit/figure_caption.v1.metadata.json"
#   # state.json must be at "drafting" + the helper rebuilds metadata
#   beril-paper-writer continue "$DRAFT_3"
# (Cost: ~$0.20 for 3 figs.)

EOF
