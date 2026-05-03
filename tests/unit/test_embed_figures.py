"""Tests for paper_writer_helpers.py embed-figures helpers (v0.3 Tier 2.2).

Coverage:
  - `_find_sentence_end_after` heuristic on tricky cases:
    * `Fig. 5` internal period not treated as sentence end
    * `e.g.` lowercase followup not treated as sentence end
    * `(Fig. 3).` form (closing paren before terminator)
    * End-of-string + newline
  - `_embed_figures_in_text` core behavior:
    * Single (Fig. N) callout → image tag injected after sentence
    * Multi-figure sentence → multiple tags injected after same sentence
    * Idempotent: re-running does not double-inject
    * Skipped when N has no manifest entry
    * Multiple Ns across different sentences → independent injections
  - End-to-end `cmd_embed_figures` against a synthetic draft fixture.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_HELPERS = (
    _REPO_ROOT / "src" / "beril_paper_writer" / "skill" / "tools"
    / "paper_writer_helpers.py"
)


def _load_helpers():
    spec = importlib.util.spec_from_file_location(
        "paper_writer_helpers_under_test", _HELPERS
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["paper_writer_helpers_under_test"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def helpers():
    return _load_helpers()


# ---------------------------------------------------------------------------
# Sentence-end heuristic
# ---------------------------------------------------------------------------


def test_sentence_end_simple(helpers) -> None:
    text = "First sentence. Second sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # First `.` is at index 14; followed by ` ` then uppercase `S` → sentence end.
    assert pos == 15  # right after the `.`


def test_sentence_end_skips_fig_n_internal_period(helpers) -> None:
    """The `.` in `Fig. 5` (followed by space + digit) is NOT a sentence end."""
    text = "Cite (Fig. 5) and continue. Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # The first `.` (in `Fig. 5`) is followed by ` ` then `5` (digit) — not a
    # sentence end. The next `.` is after `continue` — followed by ` N` (upper).
    expected = text.index("continue.") + len("continue.")
    assert pos == expected


def test_sentence_end_skips_eg_lowercase(helpers) -> None:
    """`e.g.` followed by lowercase is NOT a sentence end."""
    text = "We use methods e.g. similarity scoring. Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    # First `.` (in `e.`) → followed by `g` (lowercase) — not sentence end.
    # Second `.` (in `e.g.`) → followed by ` s` (lowercase) — not sentence end.
    # Third `.` (after `scoring`) → followed by ` N` (uppercase) — yes.
    expected = text.index("scoring.") + len("scoring.")
    assert pos == expected


def test_sentence_end_after_paren(helpers) -> None:
    """`(Fig. 3).` form: the terminating `.` is after the closing paren."""
    text = "Stable point (Fig. 3). Next sentence."
    pos = helpers._find_sentence_end_after(text, 0)
    expected = text.index(").") + len(").")
    assert pos == expected


def test_sentence_end_eof(helpers) -> None:
    """End-of-string returns len(text) when no terminator found."""
    text = "trailing prose with no sentence end"
    pos = helpers._find_sentence_end_after(text, 0)
    assert pos == len(text)


def test_sentence_end_newline_terminator(helpers) -> None:
    """`.` immediately followed by `\\n` is a sentence end (paragraph break)."""
    text = "End of paragraph.\n\nNew paragraph."
    pos = helpers._find_sentence_end_after(text, 0)
    expected = text.index("paragraph.") + len("paragraph.")
    assert pos == expected


# ---------------------------------------------------------------------------
# Embed core
# ---------------------------------------------------------------------------


def _basic_figure_map():
    """v0.4 Phase 3: figure_map is dict[int, dict] (was tuple in v0.3).
    Empty descriptor → no italic Description paragraph injected.
    """
    return {
        1: {"filename": "fig01_a.png", "caption": "Caption for figure one",
            "descriptor": {}},
        2: {"filename": "fig02_b.png", "caption": "Caption for figure two",
            "descriptor": {}},
        3: {"filename": "fig03_c.png", "caption": "Caption for figure three",
            "descriptor": {}},
        5: {"filename": "fig05_e.png", "caption": "Caption for figure five",
            "descriptor": {}},
    }


def test_embed_single_callout(helpers) -> None:
    text = "Stable point (Fig. 1). Next sentence."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert 1 in injected
    # v0.6.1: visible-caption format — empty alt + **Figure N.** paragraph
    assert "![](figures/fig01_a.png)" in new_text
    assert "**Figure 1.** Caption for figure one" in new_text
    assert skipped == []
    # Injection lands AFTER the period, before "Next sentence."
    fig_pos = new_text.index("![](figures/fig01_a.png)")
    period_pos = new_text.index("(Fig. 1).") + len("(Fig. 1).")
    assert fig_pos > period_pos


def test_embed_multi_figure_sentence(helpers) -> None:
    text = "Both apply (Fig. 3 and Fig. 5). Next sentence."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert set(injected.keys()) == {3, 5}
    # Both image tags should appear, in N-ascending order, before "Next sentence."
    fig3_pos = new_text.index("**Figure 3.**")
    fig5_pos = new_text.index("**Figure 5.**")
    next_pos = new_text.index("Next sentence")
    assert fig3_pos < fig5_pos < next_pos


def test_embed_idempotent(helpers) -> None:
    """Re-running does not double-inject."""
    text = "Stable point (Fig. 1). Next."
    once, _, _ = helpers._embed_figures_in_text(text, _basic_figure_map())
    twice, injected, _ = helpers._embed_figures_in_text(once, _basic_figure_map())
    assert once == twice
    assert injected == {}  # nothing injected on the second pass


def test_embed_skips_n_not_in_manifest(helpers) -> None:
    """A (Fig. 9) callout when 9 isn't in the manifest is skipped + flagged."""
    text = "Cite (Fig. 9) here. Next."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert injected == {}
    assert skipped == [9]
    assert new_text == text  # no modification


def test_embed_first_occurrence_only(helpers) -> None:
    """Only the FIRST (Fig. N) per N is embedded; later citations stay textual."""
    text = "First cite (Fig. 1). Second sentence cites (Fig. 1) again."
    new_text, injected, _ = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    # Exactly one caption paragraph.
    assert new_text.count("**Figure 1.**") == 1
    # The image tag is between the first and second occurrences.
    fig_pos = new_text.index("**Figure 1.**")
    first_callout = new_text.index("(Fig. 1)")
    # Second callout is after first; image tag is between them.
    second_callout = new_text.index("(Fig. 1)", first_callout + 1)
    assert first_callout < fig_pos < second_callout


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — descriptor-aware embed (italic *Description: ...* paragraph)
# ---------------------------------------------------------------------------


def test_embed_with_descriptor_combines_into_visible_caption(helpers) -> None:
    """v0.6.1: descriptor content is combined into a visible **Figure N.**
    caption paragraph below the image, not hidden in alt-text."""
    text = "Stable point (Fig. 1). Next sentence."
    figure_map = {
        1: {
            "filename": "fig01_a.png",
            "caption": "Growth curves",
            "descriptor": {
                "title": "Growth curves of PA14",
                "axes_labels": ["Time (h)", "OD600"],
                "legend_labels": [],
                "panels": [],
                "notebook_prose": "We plotted growth across substrates.",
                "source_refs": ["matplotlib_ast(nb.ipynb)"],
            },
        },
    }
    new_text, injected, _ = helpers._embed_figures_in_text(text, figure_map)
    assert 1 in injected
    # Image tag has empty alt-text.
    assert "![](figures/fig01_a.png)" in new_text
    # Visible caption paragraph contains short + description.
    assert "**Figure 1.** Growth curves." in new_text
    assert "Growth curves of PA14" in new_text  # from description.title
    assert "We plotted growth across substrates" in new_text  # from prose
    # NO separate `*Description: ...*` paragraph (legacy form).
    assert "*Description:" not in new_text


def test_embed_with_empty_descriptor_keeps_short_only(helpers) -> None:
    """Empty descriptor → caption is just the short caption."""
    text = "Stable point (Fig. 1). Next sentence."
    figure_map = {
        1: {"filename": "fig01_a.png", "caption": "Cap", "descriptor": {}},
    }
    new_text, _, _ = helpers._embed_figures_in_text(text, figure_map)
    assert "![](figures/fig01_a.png)" in new_text
    assert "**Figure 1.** Cap" in new_text
    assert "*Description:" not in new_text


def test_embed_uses_synthesized_caption_when_present(helpers) -> None:
    """v0.4 Phase 5c: when figure_map[n]['synthesized_caption'] is set,
    use it verbatim as the description (Source 4 LLM output)."""
    text = "Stable point (Fig. 1). Next sentence."
    figure_map = {
        1: {
            "filename": "fig01_a.png",
            "caption": "Short cap",
            "descriptor": {
                "title": "Descriptor title — should be IGNORED",
                "notebook_prose": "Descriptor prose — should be IGNORED",
            },
            "synthesized_caption": (
                "LLM-synthesized polished caption for figure 1 with "
                "panel breakdown and method details that overrides the "
                "descriptor entirely."
            ),
        },
    }
    new_text, _, _ = helpers._embed_figures_in_text(text, figure_map)
    # Visible caption paragraph used:
    assert "**Figure 1.** Short cap." in new_text
    assert "LLM-synthesized polished caption" in new_text
    # Descriptor content NOT used:
    assert "Descriptor title" not in new_text
    assert "Descriptor prose" not in new_text


def test_embed_falls_back_to_descriptor_when_no_synthesis(helpers) -> None:
    """v0.4 Phase 5c fallback: no synthesized_caption → descriptor-assembled."""
    text = "Stable point (Fig. 1). Next sentence."
    figure_map = {
        1: {
            "filename": "fig01_a.png",
            "caption": "Short cap",
            "descriptor": {
                "title": "Descriptor title",
                "notebook_prose": "Descriptor prose here.",
            },
            "synthesized_caption": None,
        },
    }
    new_text, _, _ = helpers._embed_figures_in_text(text, figure_map)
    # Descriptor content used (existing Phase 3/5b behavior):
    assert "Descriptor title" in new_text


def test_build_figure_map_loads_synthesized_caption(
    helpers, tmp_path: Path,
) -> None:
    """v0.4 Phase 5c: _build_figure_map reads audit/figure_caption_<N>.md
    when present and stores it in the entry."""
    draft = tmp_path / "draft"
    audit = draft / "audit"
    audit.mkdir(parents=True)
    (draft / "figures_manifest.tsv").write_text(
        "paper_order_n\tfilename\tinventory_lookup_name\n"
        "1\tfig01.png\torig.png\n",
        encoding="utf-8",
    )
    (draft / "figures_inventory.md").write_text(
        "<!-- inventory_schema_version: 2 -->\n"
        "# Inventory\n\n"
        "### `figures/orig.png`\n\n"
        "**Caption candidates:**\n\n"
        "- **filename**: Orig\n",
        encoding="utf-8",
    )
    # Source 4 LLM-synthesized caption for figure 1.
    (audit / "figure_caption_1.md").write_text(
        "Synthesized\nmulti-line caption with whitespace.",
        encoding="utf-8",
    )
    figure_map, _ = helpers._build_figure_map(draft)
    assert 1 in figure_map
    # Single-line + whitespace-collapsed.
    assert figure_map[1]["synthesized_caption"] == \
        "Synthesized multi-line caption with whitespace."


def test_build_figure_map_synth_absent_when_no_audit_file(
    helpers, tmp_path: Path,
) -> None:
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "figures_manifest.tsv").write_text(
        "paper_order_n\tfilename\tinventory_lookup_name\n"
        "1\tfig01.png\torig.png\n",
        encoding="utf-8",
    )
    (draft / "figures_inventory.md").write_text(
        "<!-- inventory_schema_version: 2 -->\n"
        "### `figures/orig.png`\n\n"
        "**Caption candidates:**\n\n"
        "- **filename**: Orig\n",
        encoding="utf-8",
    )
    figure_map, _ = helpers._build_figure_map(draft)
    assert figure_map[1]["synthesized_caption"] is None


def test_embed_with_multipanel_descriptor(helpers) -> None:
    """Multi-panel descriptor → panel breakdown in visible caption."""
    text = "Both panels show this (Fig. 3). Next sentence."
    figure_map = {
        3: {
            "filename": "fig03.png", "caption": "Multi",
            "descriptor": {
                "title": "Multi-panel",
                "axes_labels": [],
                "legend_labels": [],
                "panels": [
                    {"letter": "A", "title": "Left half"},
                    {"letter": "B", "title": "Right half"},
                ],
                "notebook_prose": None,
                "source_refs": ["matplotlib_ast(nb.ipynb)"],
            },
        },
    }
    new_text, _, _ = helpers._embed_figures_in_text(text, figure_map)
    # Panel content in visible caption paragraph, not alt-text.
    assert "**Figure 3.** Multi." in new_text
    assert "(A) Left half" in new_text
    assert "(B) Right half" in new_text
    assert "*Description:" not in new_text


def test_embed_idempotency_with_old_format(helpers) -> None:
    """v0.6.1 backward compat: text with old-format `![Figure N: ...]()` tags
    must not be double-injected by the new-format embedder."""
    old_text = (
        "Stable point (Fig. 1). "
        "\n\n![Figure 1: Old caption](figures/fig01_a.png)\n\n"
        "Next sentence."
    )
    new_text, injected, _ = helpers._embed_figures_in_text(
        old_text, _basic_figure_map()
    )
    assert injected == {}  # nothing injected — old format detected
    assert new_text == old_text


def test_embed_idempotency_with_descriptor(helpers) -> None:
    """Re-running with descriptor still skips already-embedded figures."""
    text = "Stable point (Fig. 1). Next."
    figure_map = {
        1: {"filename": "fig01_a.png", "caption": "Cap",
            "descriptor": {"title": "Foo", "panels": [], "axes_labels": [],
                           "legend_labels": [], "source_refs": [],
                           "notebook_prose": None}},
    }
    once, _, _ = helpers._embed_figures_in_text(text, figure_map)
    twice, injected2, _ = helpers._embed_figures_in_text(once, figure_map)
    assert once == twice
    assert injected2 == {}


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — _parse_figures_inventory_descriptions (v2 round-trip)
# ---------------------------------------------------------------------------


def test_parse_descriptions_v1_inventory_returns_empty(
    helpers, tmp_path: Path,
) -> None:
    """v1 inventory (no schema header) returns {} for descriptors."""
    inv = tmp_path / "figures_inventory.md"
    inv.write_text(
        "# Figures Inventory\n\n"
        "### `figures/a.png`\n\n"
        "**Caption candidates:**\n\n"
        "- **filename**: A\n",
        encoding="utf-8",
    )
    assert helpers._parse_figures_inventory_descriptions(inv) == {}


def test_parse_descriptions_v2_inventory_extracts_block(
    helpers, tmp_path: Path,
) -> None:
    inv = tmp_path / "figures_inventory.md"
    inv.write_text(
        "<!-- inventory_schema_version: 2 -->\n"
        "# Figures Inventory\n\n"
        "### `figures/a.png`\n\n"
        "**Caption candidates:**\n\n"
        "- **filename**: A\n\n"
        "**Description:**\n\n"
        "- _Title:_ Growth curves\n"
        "- _Axes:_ Time (h); OD600\n"
        "- _Panels:_ (A) Left; (B) Right\n\n"
        "_Notebook prose:_\n\n"
        "> ## Heading\n"
        "> Detailed prose here.\n\n"
        "_Source refs:_ matplotlib_ast(nb.ipynb), notebook_md_walkback(nb.ipynb)\n\n"
        "**Generated by:**\n\n"
        "- `nb.ipynb` cell 3\n",
        encoding="utf-8",
    )
    descs = helpers._parse_figures_inventory_descriptions(inv)
    assert "a.png" in descs
    d = descs["a.png"]
    assert d["title"] == "Growth curves"
    assert d["axes_labels"] == ["Time (h)", "OD600"]
    assert d["panels"] == [
        {"letter": "A", "title": "Left"},
        {"letter": "B", "title": "Right"},
    ]
    assert "Detailed prose" in (d["notebook_prose"] or "")
    assert "matplotlib_ast(nb.ipynb)" in d["source_refs"]
    assert "notebook_md_walkback(nb.ipynb)" in d["source_refs"]


def test_parse_descriptions_v2_no_description_block(
    helpers, tmp_path: Path,
) -> None:
    inv = tmp_path / "figures_inventory.md"
    inv.write_text(
        "<!-- inventory_schema_version: 2 -->\n"
        "# Figures Inventory\n\n"
        "### `figures/a.png`\n\n"
        "**Caption candidates:**\n\n"
        "- **filename**: A\n\n"
        "**Generated by:**\n\n"
        "- `nb.ipynb` cell 3\n",
        encoding="utf-8",
    )
    descs = helpers._parse_figures_inventory_descriptions(inv)
    assert "a.png" in descs
    d = descs["a.png"]
    assert d["title"] is None
    assert d["panels"] == []
    assert d["notebook_prose"] is None


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — _detect_prose_panel_callouts (Stratum 2)
# ---------------------------------------------------------------------------


def test_detect_panel_callouts_simple(helpers) -> None:
    text = "Result holds across organisms (Fig. 3A). Different pattern in (Fig. 3B)."
    panels = helpers._detect_prose_panel_callouts(text, 3)
    assert set(panels.keys()) == {"A", "B"}
    assert "across organisms" in panels["A"] or "Fig. 3A" in panels["A"]


def test_detect_panel_callouts_ignores_other_figures(helpers) -> None:
    text = "Foo (Fig. 5A). Bar (Fig. 3A)."
    panels_for_3 = helpers._detect_prose_panel_callouts(text, 3)
    assert set(panels_for_3.keys()) == {"A"}
    panels_for_5 = helpers._detect_prose_panel_callouts(text, 5)
    assert set(panels_for_5.keys()) == {"A"}


def test_detect_panel_callouts_first_occurrence_wins(helpers) -> None:
    text = "First context (Fig. 3A). Other content. Repeat citation (Fig. 3A)."
    panels = helpers._detect_prose_panel_callouts(text, 3)
    assert "First context" in panels["A"]


def test_detect_panel_callouts_no_panels(helpers) -> None:
    text = "Just (Fig. 3) with no panel suffix."
    panels = helpers._detect_prose_panel_callouts(text, 3)
    assert panels == {}


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — _assemble_description_text
# ---------------------------------------------------------------------------


def test_assemble_description_empty_descriptor(helpers) -> None:
    assert helpers._assemble_description_text({}) == ""


def test_assemble_description_title_only(helpers) -> None:
    out = helpers._assemble_description_text({"title": "Foo"})
    assert out == "Foo."


def test_assemble_description_single_panel_with_axes(helpers) -> None:
    out = helpers._assemble_description_text({
        "title": "Growth curves",
        "axes_labels": ["Time", "OD600"],
        "legend_labels": [],
        "panels": [],
        "notebook_prose": None,
    })
    assert out.startswith("Growth curves.")
    assert "Time" in out and "OD600" in out


def test_assemble_description_multi_panel(helpers) -> None:
    out = helpers._assemble_description_text({
        "title": "Multi",
        "panels": [
            {"letter": "A", "title": "Left"},
            {"letter": "B", "title": "Right"},
        ],
    })
    assert "Multi." in out
    assert "(A) Left" in out
    assert "(B) Right" in out


def test_assemble_description_merges_prose_panel_callouts(helpers) -> None:
    # Descriptor has only panel A; prose mentions both A and B.
    out = helpers._assemble_description_text(
        descriptor={
            "title": "Multi",
            "panels": [{"letter": "A", "title": "AST title for A"}],
        },
        prose_panel_callouts={
            "A": "Prose context for A",  # already covered by AST
            "B": "Prose context for B",  # only source for B
        },
    )
    assert "(A) AST title for A" in out  # AST wins for A
    assert "(B) Prose context for B" in out  # Prose fills B


def test_assemble_description_max_chars_cap(helpers) -> None:
    huge_prose = "x " * 10000
    out = helpers._assemble_description_text({
        "title": "Foo",
        "notebook_prose": huge_prose,
    }, max_chars=200)
    assert len(out) <= 200


def test_strip_prose_drops_notebook_organization_keywords(helpers) -> None:
    """v0.4 Phase 5b: lines beginning with `Purpose:`, `Approach:`,
    `Sections:`, `Steps:`, `Method:` etc. are notebook-organization
    metadata; drop them from the inline prose snippet."""
    raw = (
        "Purpose: Address 2 critical and 4 important suggestions.\n"
        "Approach: Single supplementary notebook using pandas/scipy only.\n"
        "Sections: 1. Gene-to-Gap. 2. Some other thing.\n"
        "We see a clear pattern across the data.\n"
        "Steps: a, b, c.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    # Notebook keywords stripped
    assert "Purpose:" not in out
    assert "Approach:" not in out
    assert "Sections:" not in out
    assert "Steps:" not in out
    # Real prose retained
    assert "clear pattern" in out


def test_strip_prose_drops_bold_prefixed_keyword_headers(helpers) -> None:
    """v0.4 Phase 5b refinement: bold-formatted keyword headers like
    `**Goal:**`, `**Purpose:**` must be stripped. Pre-process bold
    markers before the line-prefix filter so they're caught.

    Caught during draft_3 re-render: figure 7 had 'Goal: Test whether...'
    in the docx because the source prose had `**Goal:**` and the bold
    marker prevented the line-filter from matching."""
    raw = (
        "**Goal:** Test whether dark gene cross-organism concordance is special.\n"
        "**Purpose:** Validate the H1d hypothesis.\n"
        "We see strong concordance across the 65 ortholog groups tested.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    assert "Goal:" not in out
    assert "Purpose:" not in out
    assert "Validate" not in out  # purpose-line content also dropped
    assert "65 ortholog groups" in out


def test_strip_prose_handles_colon_outside_bold(helpers) -> None:
    """v0.4 Phase 5b refinement #4: `**Goal**:` (colon OUTSIDE the bold
    delimiters) must be normalized like `**Goal:**` (colon inside). Both
    visual forms render identically in markdown but parse differently.

    Caught in v0.4 Phase 5b third re-render: figure 7's actual prose used
    `**Goal**: Test whether...` and my single-form regex only handled
    `**Goal:** Test whether...`."""
    raw = (
        "**Goal**: Test whether dark gene concordance is special.\n"
        "**Approach**: Some method.\n"
        "Real content here about the data.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    assert "Goal:" not in out
    assert "Goal" not in out  # bold markers also gone
    assert "Approach:" not in out
    assert "Test whether" not in out  # goal content dropped
    assert "Real content here" in out


def test_strip_prose_handles_possessive_artifact_refs(helpers) -> None:
    """v0.4 Phase 5b refinement: NBxx's gene neighborhood → strip the
    whole `NB07's`, not just `NB07`, to avoid orphaned possessive."""
    raw = "NB07's gene neighborhood analysis uses positional heuristics."
    out = helpers._strip_prose_for_inline(raw)
    assert "NB07" not in out
    assert "'s" not in out
    assert "gene neighborhood analysis" in out


def test_strip_prose_drops_project_internal_artifact_refs(helpers) -> None:
    """v0.4 Phase 5b: REVIEW.md / REPORT.md / NB04 / nb09 are
    project-internal references; strip them from inline prose."""
    raw = (
        "We address findings from REVIEW.md by re-running NB04 and "
        "comparing with REPORT.md and nb09 outputs in our analysis."
    )
    out = helpers._strip_prose_for_inline(raw)
    assert "REVIEW.md" not in out
    assert "REPORT.md" not in out
    assert "NB04" not in out
    assert "nb09" not in out
    # Sentence shape preserved (just artifact-tokens removed; semantic
    # may be slightly degraded but not nonsensical).
    assert "address findings" in out
    assert "comparing with" in out


# ---------------------------------------------------------------------------
# v0.6.4: blockquote stripping + section-level boilerplate stripping
# ---------------------------------------------------------------------------


def test_strip_prose_blockquote_prefixes(helpers) -> None:
    """v0.6.4: blockquote `> ` prefixes must be stripped so that
    `> ## Problem` is seen as a heading and filtered."""
    raw = (
        "> ## Problem\n"
        "> Gene neighborhood analysis uses a minimal positional heuristic.\n"
        "> \n"
        "> ## Strategy\n"
        "> We compare neighborhoods across all organisms.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    assert "Problem" not in out
    assert "Strategy" not in out
    assert "positional heuristic" not in out
    assert "compare neighborhoods" not in out


def test_strip_prose_section_level_strips_boilerplate_body(helpers) -> None:
    """v0.6.4: entire `## <BoilerplateKeyword>` sections (header + body)
    are stripped, not just the heading line. This prevents body paragraphs
    of Problem/Strategy/Inputs/Outputs sections from leaking through the
    line-by-line filter."""
    raw = (
        "## Problem\n"
        "Gene neighborhood analysis uses a minimal positional heuristic "
        "that may miss conserved neighborhoods spanning multiple operons.\n\n"
        "## Strategy\n"
        "We compare neighborhoods across all organisms using a sliding "
        "window approach with configurable overlap.\n\n"
        "## Inputs\n"
        "- Gene annotations from Step 2\n"
        "- Ortholog groups from Step 3\n\n"
        "## Outputs\n"
        "- Conserved neighborhood calls\n"
        "- Confidence scores per neighborhood\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    # All boilerplate section bodies stripped
    assert "positional heuristic" not in out
    assert "sliding window" not in out
    assert "Gene annotations" not in out
    assert "Confidence scores" not in out
    # Result should be empty or near-empty
    assert len(out.split()) < 5


def test_strip_prose_section_level_preserves_real_headings(helpers) -> None:
    """v0.6.4: `## Section 1: Gene Annotation Breakdown` and
    `## Conserved Gene Neighborhoods` are real content — NOT stripped."""
    raw = (
        "## Section 1: Gene Annotation Breakdown\n"
        "Figure 8 shows the distribution of annotated vs dark genes "
        "across 35 organisms.\n\n"
        "## Conserved Gene Neighborhoods\n"
        "These neighborhoods show strong conservation across phyla.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    # Real content preserved (headings stripped by heading filter,
    # but body text kept)
    assert "distribution of annotated" in out
    assert "strong conservation" in out


def test_strip_prose_mixed_boilerplate_and_content(helpers) -> None:
    """v0.6.4: mixed sections — boilerplate stripped, real content kept."""
    raw = (
        "## Problem\n"
        "This is a boilerplate problem statement.\n\n"
        "## Gene Annotation Breakdown\n"
        "Figure 8 shows annotated vs dark genes across 35 organisms.\n\n"
        "## Strategy\n"
        "We compare neighborhoods.\n\n"
        "## Conserved Gene Neighborhoods\n"
        "These neighborhoods show strong conservation.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    assert "boilerplate problem" not in out
    assert "compare neighborhoods" not in out
    assert "annotated vs dark genes" in out
    assert "strong conservation" in out


def test_strip_prose_blockquote_then_section_strip(helpers) -> None:
    """v0.6.4 end-to-end: blockquote-wrapped notebook sections like
    the real NB08 prose in figures_inventory.md."""
    raw = (
        "> ## Problem\n"
        "> Gene neighborhood analysis uses a minimal positional heuristic.\n"
        "> \n"
        "> ## Results\n"
        "> We found 42 conserved neighborhoods across the dataset.\n"
    )
    out = helpers._strip_prose_for_inline(raw)
    # Problem section stripped (boilerplate keyword)
    assert "positional heuristic" not in out
    # Results heading is IMRAD — content should survive
    # (heading line stripped by heading filter, body kept)
    assert "42 conserved neighborhoods" in out


def test_assemble_description_strips_redundant_panel_letter_prefix(
    helpers,
) -> None:
    """When notebook author writes set_title('A. Foo') AND we render `(A)`,
    drop the redundant 'A. ' prefix to avoid `(A) A. Foo`."""
    out = helpers._assemble_description_text({
        "title": "Multi",
        "panels": [
            {"letter": "A", "title": "A. Foo bar"},
            {"letter": "B", "title": "B) Baz qux"},
            {"letter": "C", "title": "Free-form C content"},
        ],
    })
    assert "(A) Foo bar" in out
    assert "(B) Baz qux" in out
    assert "(C) Free-form C content" in out
    # No redundant prefixes
    assert "(A) A." not in out
    assert "(B) B)" not in out


def test_assemble_description_collapses_newlines(helpers) -> None:
    out = helpers._assemble_description_text({
        "title": "Foo\nBar",
        "notebook_prose": "Para 1.\n\nPara 2.",
    })
    assert "\n" not in out


# ---------------------------------------------------------------------------
# Original test resumed below
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# v0.4 Phase 5 retest fix: manifest prefix-normalization regression test
# ---------------------------------------------------------------------------


class TestManifestPrefixNormalization:
    """v0.4 Phase 5 retest finding: results.v1 occasionally emits
    inventory_lookup_name and filename with a `figures/` directory prefix
    instead of basename-only. The prefix breaks every downstream lookup
    silently. Fix: defensive Path().name normalization in
    _parse_figures_manifest.
    """

    def test_strips_directory_prefix_from_inventory_lookup(
        self, helpers, tmp_path: Path,
    ):
        manifest = tmp_path / "figures_manifest.tsv"
        manifest.write_text(
            "paper_order_n\tfilename\tinventory_lookup_name\n"
            "1\tfigures/fig01_dark.png\tfigures/orig_dark.png\n"
            "2\tfig02_clean.png\torig_clean.png\n",
            encoding="utf-8",
        )
        rows = helpers._parse_figures_manifest(manifest)
        assert len(rows) == 2
        # Prefixed row: prefix stripped on BOTH columns.
        assert rows[0]["filename"] == "fig01_dark.png"
        assert rows[0]["inventory_lookup_name"] == "orig_dark.png"
        # Already-basename row: unchanged.
        assert rows[1]["filename"] == "fig02_clean.png"
        assert rows[1]["inventory_lookup_name"] == "orig_clean.png"

    def test_basename_only_manifest_is_unchanged(self, helpers, tmp_path: Path):
        # Idempotent on basename-only manifests (the v0.3 happy path).
        manifest = tmp_path / "figures_manifest.tsv"
        manifest.write_text(
            "paper_order_n\tfilename\tinventory_lookup_name\n"
            "1\tfig01.png\torig01.png\n"
            "2\tfig02.png\torig02.png\n",
            encoding="utf-8",
        )
        rows = helpers._parse_figures_manifest(manifest)
        assert rows[0]["filename"] == "fig01.png"
        assert rows[1]["inventory_lookup_name"] == "orig02.png"

    def test_build_figure_map_works_after_prefix_strip(
        self, helpers, tmp_path: Path,
    ):
        """End-to-end: a prefixed manifest must let _build_figure_map
        correctly look up the inventory descriptor + caption."""
        draft = tmp_path / "draft"
        draft.mkdir()
        # Manifest with prefixed names (the LLM-drift case).
        (draft / "figures_manifest.tsv").write_text(
            "paper_order_n\tfilename\tinventory_lookup_name\n"
            "1\tfigures/fig01.png\tfigures/orig_one.png\n",
            encoding="utf-8",
        )
        # Inventory keyed by basename (extract_figures.py emits this).
        (draft / "figures_inventory.md").write_text(
            "<!-- inventory_schema_version: 2 -->\n"
            "# Inventory\n\n"
            "### `figures/orig_one.png`\n\n"
            "**Caption candidates:**\n\n"
            "- **REPORT.md**: Real REPORT-derived caption\n\n"
            "**Description:**\n\n"
            "- _Title:_ Real title from descriptor\n",
            encoding="utf-8",
        )
        figure_map, warnings = helpers._build_figure_map(draft)
        assert 1 in figure_map
        # Lookup succeeds: we get the REAL caption, not filename-derived.
        assert figure_map[1]["caption"] == "Real REPORT-derived caption"
        assert figure_map[1]["filename"] == "fig01.png"
        # Descriptor populated (sufficiency gate downstream uses this).
        assert figure_map[1]["descriptor"]["title"] == "Real title from descriptor"
        # No "inventory has no entry" warning surfaced.
        assert not any(
            "inventory has no entry" in w for w in warnings
        ), f"unexpected WARN: {warnings}"


# ---------------------------------------------------------------------------
# v0.4 Phase 4c — sufficiency gate + caption-bundle builder
# ---------------------------------------------------------------------------


class TestSufficiencyGate:
    def test_passes_with_title_and_long_prose(self, helpers):
        descriptor = {
            "title": "Foo",
            "axes_labels": [],
            "notebook_prose": " ".join(["word"] * 50),
        }
        assert helpers._passes_sufficiency_gate(descriptor)

    def test_passes_with_axes_only(self, helpers):
        descriptor = {
            "title": None,
            "axes_labels": ["X", "Y"],
            "notebook_prose": " ".join(["word"] * 50),
        }
        assert helpers._passes_sufficiency_gate(descriptor)

    def test_fails_with_only_section_heading(self, helpers):
        # Phase 1a empirical case: just-heading walkbacks like
        # "## 4. Figures" must FAIL the gate — they look 13 chars long
        # but contain zero substance.
        descriptor = {
            "title": "Some title",
            "axes_labels": [],
            "notebook_prose": "## 4. Figures",
        }
        assert not helpers._passes_sufficiency_gate(descriptor)

    def test_fails_with_no_title_and_no_axes(self, helpers):
        descriptor = {
            "title": None,
            "axes_labels": [],
            "notebook_prose": " ".join(["word"] * 100),
        }
        assert not helpers._passes_sufficiency_gate(descriptor)

    def test_empty_descriptor_fails(self, helpers):
        assert not helpers._passes_sufficiency_gate({})

    def test_strip_heading_lines_drops_hashes(self, helpers):
        text = "## Heading\nReal prose here.\n# Another heading"
        stripped = helpers._strip_heading_lines(text)
        assert "Heading" not in stripped
        assert "Real prose here" in stripped

    def test_v0_5_gate_fails_boilerplate_heavy_prose(self, helpers):
        """v0.5 sufficiency-gate change: switch from _strip_heading_lines
        to _strip_prose_for_inline. Boilerplate-heavy prose that v0.4
        let through the gate (because heading-strip alone left the
        keyword content intact) now correctly fails the gate."""
        # Real example pattern from functional_dark_matter fig 8:
        # 50+ words of pure keyword-tagged boilerplate.
        descriptor = {
            "title": "Domain matching analysis",
            "axes_labels": ["X", "Y"],
            "notebook_prose": (
                "**Purpose:** Address 2 critical and 4 important "
                "suggestions from automated review.\n\n"
                "**Approach:** Single supplementary notebook using "
                "pandas/scipy only (no Spark). All inputs are saved.\n\n"
                "**Sections:**\n"
                "1. Gene-to-Gap Enzymatic Matching (Critical Issue 1)\n"
                "2. Domain Coverage Analysis (Critical Issue 2)"
            ),
        }
        # v0.4 behavior would have PASSED this (heading-strip leaves
        # keyword content; word count >> 30). v0.5 must FAIL.
        assert not helpers._passes_sufficiency_gate(descriptor)

    def test_v0_5_max_words_formula(self, helpers):
        """v0.5: caption word budget scales with panel count."""
        assert helpers._caption_max_words(0) == 200   # single-panel
        assert helpers._caption_max_words(1) == 250
        assert helpers._caption_max_words(2) == 300
        assert helpers._caption_max_words(3) == 350
        assert helpers._caption_max_words(4) == 400   # like fig 8
        assert helpers._caption_max_words(6) == 500   # complex multi-panel
        # Negative or zero → defaults to 200 (no negative scaling)
        assert helpers._caption_max_words(-1) == 200

    def test_v0_6_4_gate_fails_blockquote_notebook_prose(self, helpers):
        """v0.6.4: NB08-style notebook prose wrapped in blockquotes with
        ## Problem/Strategy/Inputs/Outputs sections should FAIL the gate
        (routes to Source 4 LLM for proper caption generation)."""
        descriptor = {
            "title": "Gene neighborhood conservation",
            "axes_labels": ["Organism", "Neighborhood count"],
            "notebook_prose": (
                "> ## Problem\n"
                "> Gene neighborhood analysis uses a minimal positional "
                "heuristic that may miss conserved neighborhoods.\n"
                "> \n"
                "> ## Strategy\n"
                "> We compare neighborhoods across all organisms using a "
                "sliding window approach.\n"
                "> \n"
                "> ## Inputs\n"
                "> - Gene annotations from Step 2\n"
                "> - Ortholog groups from Step 3\n"
                "> \n"
                "> ## Outputs\n"
                "> - Conserved neighborhood calls\n"
                "> - Confidence scores per neighborhood\n"
            ),
        }
        assert not helpers._passes_sufficiency_gate(descriptor)

    def test_v0_5_gate_passes_substantive_prose(self, helpers):
        """v0.5 sufficiency-gate: real descriptive prose with no
        boilerplate keywords still passes the gate."""
        descriptor = {
            "title": "Growth curves of PA14",
            "axes_labels": ["Time (h)", "OD600"],
            "notebook_prose": (
                "We measured growth in 96-well plates across 47 carbon "
                "sources. Cultures were inoculated at OD600 0.05 from "
                "overnight starters; growth was tracked by absorbance "
                "every 15 minutes for 24 hours. Three biological "
                "replicates per condition; bars indicate standard error."
            ),
        }
        assert helpers._passes_sufficiency_gate(descriptor)


class TestBuildCaptionBundles:
    def _build_draft(self, tmp_path, *,
                     descriptors: dict[str, dict],
                     captions: dict[str, str],
                     report_text: str = "",
                     results_text: str = "",
                     manifest_rows: list[tuple] | None = None):
        """Create a synthetic draft + project layout. manifest_rows is
        list of (n, paper_filename, inv_lookup_name) tuples."""
        proj = tmp_path / "project"
        draft = tmp_path / "papers" / "draft_1"
        draft.mkdir(parents=True)
        proj.mkdir(parents=True)
        # Manifest
        if manifest_rows:
            mlines = ["paper_order_n\tfilename\tinventory_lookup_name"]
            for n, paper_fn, inv_n in manifest_rows:
                mlines.append(f"{n}\t{paper_fn}\t{inv_n}")
            (draft / "figures_manifest.tsv").write_text(
                "\n".join(mlines), encoding="utf-8")
        # Inventory v2
        body = "<!-- inventory_schema_version: 2 -->\n# Figures Inventory\n\n"
        for inv_name, desc in descriptors.items():
            body += f"### `figures/{inv_name}`\n\n"
            cap = captions.get(inv_name, inv_name)
            body += "**Caption candidates:**\n\n"
            body += f"- **filename**: {cap}\n\n"
            if (desc.get("title") or desc.get("notebook_prose")
                    or desc.get("panels")):
                body += "**Description:**\n\n"
                if desc.get("title"):
                    body += f"- _Title:_ {desc['title']}\n"
                if desc.get("axes_labels"):
                    body += f"- _Axes:_ {'; '.join(desc['axes_labels'])}\n"
                if desc.get("panels"):
                    panel_summary = "; ".join(
                        f"({p['letter']})"
                        + (f" {p['title']}" if p.get("title") else "")
                        for p in desc["panels"]
                    )
                    body += f"- _Panels:_ {panel_summary}\n"
                body += "\n"
                if desc.get("notebook_prose"):
                    body += "_Notebook prose:_\n\n"
                    for line in desc["notebook_prose"].split("\n"):
                        body += f"> {line}\n" if line else ">\n"
                    body += "\n"
        (draft / "figures_inventory.md").write_text(body, encoding="utf-8")
        # REPORT.md + 02_results.md
        if report_text:
            (proj / "REPORT.md").write_text(report_text, encoding="utf-8")
        if results_text:
            (draft / "02_results.md").write_text(results_text, encoding="utf-8")
        return proj, draft

    def test_all_pass_gate_no_llm_invocations(self, helpers, tmp_path, capsys):
        # Both figures have rich enough descriptors to pass the gate.
        long_prose = " ".join(["word"] * 50)
        proj, draft = self._build_draft(tmp_path,
            descriptors={
                "orig1.png": {"title": "Foo", "notebook_prose": long_prose},
                "orig2.png": {"title": "Bar", "notebook_prose": long_prose},
            },
            captions={"orig1.png": "Cap1", "orig2.png": "Cap2"},
            manifest_rows=[(1, "fig01.png", "orig1.png"),
                           (2, "fig02.png", "orig2.png")],
        )
        bdir = draft / "audit" / "caption_bundles"
        class A:
            draft_dir = str(draft)
            project_root = str(proj)
            bundles_dir = str(bdir)
            max_words = 200
        rc = helpers.cmd_build_caption_bundles(A())
        assert rc == 0
        captured = capsys.readouterr()
        # No figure_ids on stdout (all pass)
        assert captured.out.strip() == ""
        # Metadata file exists with both entries marked deterministic.
        meta = json.loads(
            (draft / "audit" / "figure_caption.v1.metadata.json").read_text()
        )
        assert len(meta["captions"]) == 2
        assert all(e["source_chosen"] == "deterministic" for e in meta["captions"])

    def test_v0_5_bundle_max_words_scales_with_panels(self, helpers, tmp_path, capsys):
        """v0.5: bundle's max_words field scales by descriptor.panels count.
        Multi-panel figures get a larger LLM word budget."""
        # Build a draft with two figures: one single-panel, one 4-panel.
        proj, draft = self._build_draft(tmp_path,
            descriptors={
                "single.png": {"title": "Single panel", "axes_labels": ["X"]},
                "multi.png": {
                    "title": "Multi-panel",
                    "axes_labels": ["X"],
                    # No notebook_prose (so they fail gate → bundles built)
                    "panels": [
                        {"letter": "A", "title": "Panel A"},
                        {"letter": "B", "title": "Panel B"},
                        {"letter": "C", "title": "Panel C"},
                        {"letter": "D", "title": "Panel D"},
                    ],
                },
            },
            captions={"single.png": "Single", "multi.png": "Multi"},
            manifest_rows=[(1, "fig01.png", "single.png"),
                           (2, "fig02.png", "multi.png")],
        )
        bdir = draft / "audit" / "caption_bundles"
        class A:
            draft_dir = str(draft)
            project_root = str(proj)
            bundles_dir = str(bdir)
            max_words = None  # Use formula
        rc = helpers.cmd_build_caption_bundles(A())
        assert rc == 0
        # Single-panel bundle has max_words=200 (no panels → default)
        single = json.loads((bdir / "figure_1.bundle.json").read_text())
        assert single["max_words"] == 200
        # 4-panel bundle has max_words=400 (formula: 200 + 50*4)
        multi = json.loads((bdir / "figure_2.bundle.json").read_text())
        assert multi["max_words"] == 400

    def test_one_fails_gate_emits_one_id(self, helpers, tmp_path, capsys):
        long_prose = " ".join(["word"] * 50)
        proj, draft = self._build_draft(tmp_path,
            descriptors={
                "orig1.png": {"title": "Foo", "notebook_prose": long_prose},
                "orig2.png": {"title": None, "notebook_prose": "## Heading only"},
            },
            captions={"orig1.png": "Cap1", "orig2.png": "Cap2"},
            manifest_rows=[(1, "fig01.png", "orig1.png"),
                           (2, "fig02.png", "orig2.png")],
        )
        bdir = draft / "audit" / "caption_bundles"
        class A:
            draft_dir = str(draft)
            project_root = str(proj)
            bundles_dir = str(bdir)
            max_words = 200
        rc = helpers.cmd_build_caption_bundles(A())
        assert rc == 0
        captured = capsys.readouterr()
        # Only figure 2 needs Source 4
        assert captured.out.strip() == "2"
        # Bundle file exists for figure 2
        assert (bdir / "figure_2.bundle.json").is_file()
        # No bundle for figure 1 (it passed)
        assert not (bdir / "figure_1.bundle.json").exists()
        # Metadata has both entries with correct source_chosen
        meta = json.loads(
            (draft / "audit" / "figure_caption.v1.metadata.json").read_text()
        )
        sources = {e["figure_id"]: e["source_chosen"] for e in meta["captions"]}
        assert sources == {1: "deterministic", 2: "llm"}


class TestComputeCaptionStats:
    def test_updates_metadata_with_stats(self, helpers, tmp_path, capsys):
        draft = tmp_path / "draft"
        audit = draft / "audit"
        audit.mkdir(parents=True)
        # Pre-populate metadata.json with one llm entry awaiting stats.
        (audit / "figure_caption.v1.metadata.json").write_text(
            json.dumps({
                "schema_version": 1,
                "captions": [{
                    "figure_id": 3,
                    "output_path": "audit/figure_caption_3.md",
                    "input_bundle": {},
                    "source_chosen": "llm",
                }],
            }),
            encoding="utf-8",
        )
        # LLM-written caption file with >=30 words, 2 panels, 3 numerical claims
        caption_text = (
            "Distribution across 343 conditions in the cohort dataset. "
            "(A) Magnitude density of 3,705 dark genes compared to "
            "annotated genes. (B) Condition breadth in genes with strong "
            "phenotypes; right-skewed pattern observed across the working "
            "dataset under analysis at the standard threshold cutoff."
        )
        (audit / "figure_caption_3.md").write_text(caption_text, encoding="utf-8")
        class A:
            draft_dir = str(draft)
            figure_id = 3
        rc = helpers.cmd_compute_caption_stats(A())
        assert rc == 0
        captured = capsys.readouterr()
        # Closing-message line on stdout
        assert "figure_caption_3 word_count" in captured.out
        # Metadata updated
        meta = json.loads(
            (audit / "figure_caption.v1.metadata.json").read_text()
        )
        cm = meta["captions"][0]["closing_message"]
        assert cm["word_count"] >= 30
        assert cm["panel_count"] == 2
        assert cm["traceable_claims"] >= 2  # 343, 3,705 (or 3705)


def test_embed_multiple_sections_separate_injections(helpers) -> None:
    """Multiple distinct sentences with different Ns each get their own embed."""
    text = (
        "First sentence cites (Fig. 1). "
        "Second sentence cites (Fig. 2). "
        "Third sentence cites (Fig. 3)."
    )
    new_text, injected, _ = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert set(injected.keys()) == {1, 2, 3}
    # Caption paragraphs appear in document order (1, 2, 3).
    assert new_text.index("**Figure 1.**") < new_text.index("**Figure 2.**")
    assert new_text.index("**Figure 2.**") < new_text.index("**Figure 3.**")


# ---------------------------------------------------------------------------
# End-to-end: cmd_embed_figures against a synthetic draft fixture
# ---------------------------------------------------------------------------


def _build_synthetic_draft(tmp_path: Path) -> Path:
    """Build a minimal draft directory with manifest, inventory, and a
    single section file containing two callouts."""
    draft = tmp_path / "draft_x"
    (draft / "figures").mkdir(parents=True)
    # Synthetic figure files (just touch — embed step doesn't read them)
    (draft / "figures" / "fig01_a.png").write_bytes(b"")
    (draft / "figures" / "fig02_b.png").write_bytes(b"")
    # Manifest
    (draft / "figures_manifest.tsv").write_text(
        "paper_order_n\tfilename\tinventory_lookup_name\n"
        "1\tfig01_a.png\torig_one.png\n"
        "2\tfig02_b.png\torig_two.png\n",
        encoding="utf-8",
    )
    # Inventory with caption candidates
    (draft / "figures_inventory.md").write_text(
        "# Inventory\n\n"
        "### `figures/orig_one.png`\n\n"
        "_PNG, 1 KB_\n\n"
        "**Caption candidates:**\n\n"
        "- **REPORT.md _(in Key Findings)_**: Caption for figure one\n"
        "- **filename**: Orig one\n\n"
        "### `figures/orig_two.png`\n\n"
        "_PNG, 1 KB_\n\n"
        "**Caption candidates:**\n\n"
        "- **REPORT.md _(in Key Findings)_**: Caption for figure two\n"
        "- **filename**: Orig two\n",
        encoding="utf-8",
    )
    # Section file with two callouts
    (draft / "02_results.md").write_text(
        "# Results\n\n"
        "First subsection cites (Fig. 1). Next sentence here.\n\n"
        "Second subsection cites (Fig. 2). End of section.\n",
        encoding="utf-8",
    )
    return draft


def test_cmd_embed_figures_end_to_end(helpers, tmp_path: Path, capsys) -> None:
    draft = _build_synthetic_draft(tmp_path)

    class Args:
        draft_dir = str(draft)

    rc = helpers.cmd_embed_figures(Args())
    assert rc == 0
    captured = capsys.readouterr()
    # Stdout summary mentions 2 embedded.
    assert "embedded: 2" in captured.out

    # Section file now contains both visible-caption figure blocks.
    body = (draft / "02_results.md").read_text(encoding="utf-8")
    assert "![](figures/fig01_a.png)" in body
    assert "**Figure 1.** Caption for figure one" in body
    assert "![](figures/fig02_b.png)" in body
    assert "**Figure 2.** Caption for figure two" in body

    # Re-running is idempotent.
    capsys.readouterr()  # clear
    rc2 = helpers.cmd_embed_figures(Args())
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert "embedded: 0" in captured2.out  # nothing new on second pass
    body_after = (draft / "02_results.md").read_text(encoding="utf-8")
    assert body == body_after


def test_cmd_embed_figures_missing_manifest(helpers, tmp_path: Path, capsys) -> None:
    """No manifest → NOTE + zero embeds, never errors."""
    (tmp_path / "draft_y").mkdir()

    class Args:
        draft_dir = str(tmp_path / "draft_y")

    rc = helpers.cmd_embed_figures(Args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "embedded: 0" in captured.out
