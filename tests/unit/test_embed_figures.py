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
    assert "![Figure 1: Caption for figure one](figures/fig01_a.png)" in new_text
    assert skipped == []
    # Injection lands AFTER the period, before "Next sentence."
    fig_pos = new_text.index("![Figure 1:")
    period_pos = new_text.index("(Fig. 1).") + len("(Fig. 1).")
    assert fig_pos > period_pos


def test_embed_multi_figure_sentence(helpers) -> None:
    text = "Both apply (Fig. 3 and Fig. 5). Next sentence."
    new_text, injected, skipped = helpers._embed_figures_in_text(
        text, _basic_figure_map()
    )
    assert set(injected.keys()) == {3, 5}
    # Both image tags should appear, in N-ascending order, before "Next sentence."
    fig3_pos = new_text.index("![Figure 3:")
    fig5_pos = new_text.index("![Figure 5:")
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
    # Exactly one image tag.
    assert new_text.count("![Figure 1:") == 1
    # The image tag is between the first and second occurrences.
    fig_pos = new_text.index("![Figure 1:")
    first_callout = new_text.index("(Fig. 1)")
    # Second callout is after first; image tag is between them.
    second_callout = new_text.index("(Fig. 1)", first_callout + 1)
    assert first_callout < fig_pos < second_callout


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — descriptor-aware embed (italic *Description: ...* paragraph)
# ---------------------------------------------------------------------------


def test_embed_with_descriptor_injects_italic_description(helpers) -> None:
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
    assert "![Figure 1: Growth curves](figures/fig01_a.png)" in new_text
    # Italic Description paragraph appended after the image
    assert "*Description:" in new_text
    assert "Growth curves of PA14" in new_text
    # Picture comes BEFORE the Description paragraph
    pic_pos = new_text.index("![Figure 1:")
    desc_pos = new_text.index("*Description:")
    assert pic_pos < desc_pos


def test_embed_with_empty_descriptor_skips_description(helpers) -> None:
    """Empty descriptor → no Description paragraph (v0.3 behavior preserved)."""
    text = "Stable point (Fig. 1). Next sentence."
    figure_map = {
        1: {"filename": "fig01_a.png", "caption": "Cap", "descriptor": {}},
    }
    new_text, _, _ = helpers._embed_figures_in_text(text, figure_map)
    assert "![Figure 1:" in new_text
    assert "*Description:" not in new_text


def test_embed_with_multipanel_descriptor(helpers) -> None:
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
    assert "(A) Left half" in new_text
    assert "(B) Right half" in new_text


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
            if desc.get("title") or desc.get("notebook_prose"):
                body += "**Description:**\n\n"
                if desc.get("title"):
                    body += f"- _Title:_ {desc['title']}\n"
                if desc.get("axes_labels"):
                    body += f"- _Axes:_ {'; '.join(desc['axes_labels'])}\n"
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
    # Image tags appear in document order (1, 2, 3).
    assert new_text.index("![Figure 1:") < new_text.index("![Figure 2:")
    assert new_text.index("![Figure 2:") < new_text.index("![Figure 3:")


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

    # Section file now contains both image tags with REPORT-derived captions.
    body = (draft / "02_results.md").read_text(encoding="utf-8")
    assert "![Figure 1: Caption for figure one](figures/fig01_a.png)" in body
    assert "![Figure 2: Caption for figure two](figures/fig02_b.png)" in body

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
