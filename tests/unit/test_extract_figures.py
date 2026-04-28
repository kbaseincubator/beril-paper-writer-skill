"""Tests for skill/tools/extract_figures.py — figure inventory + captions.

Coverage:
  - Image-format inference + filename detection
  - filename_to_caption (strips fig/NB prefixes, replaces underscores)
  - REPORT.md image-reference parsing (alt text + section context)
  - Path-expression parser for savefig args (string, BinOp, os.path.join,
    Path(), str(), JoinedStr)
  - Savefig call detection (any *.savefig)
  - Notebook walker pairs preceding markdown with code cells
  - Caption priority: REPORT > notebook context > filename
  - Inventory summary fields
  - figures_inventory.md formatter
  - End-to-end against a synthetic project
  - CLI invocation
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools import extract_figures as ef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CELL_ID = [0]


def _next_id() -> str:
    _CELL_ID[0] += 1
    return f"cell-{_CELL_ID[0]:08d}"


def _code_cell(source: str) -> dict:
    return {
        "id": _next_id(),
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def _md_cell(source: str) -> dict:
    return {
        "id": _next_id(),
        "cell_type": "markdown",
        "metadata": {},
        "source": source,
    }


def _write_notebook(path: Path, cells: list[dict]) -> None:
    import nbformat
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nbformat.from_dict(nb), f)


# ---------------------------------------------------------------------------
# Image-format helpers
# ---------------------------------------------------------------------------

class TestImageFormatInference:
    def test_known_extensions(self):
        assert ef.is_image_file(Path("foo.png"))
        assert ef.is_image_file(Path("foo.jpg"))
        assert ef.is_image_file(Path("foo.SVG"))
        assert not ef.is_image_file(Path("foo.txt"))

    def test_format_from_extension(self):
        assert ef.infer_image_format(Path("a.png")) == "png"
        assert ef.infer_image_format(Path("a.jpeg")) == "jpeg"
        assert ef.infer_image_format(Path("a.JPG")) == "jpeg"
        assert ef.infer_image_format(Path("a.unknown")) == "unknown"


# ---------------------------------------------------------------------------
# filename_to_caption
# ---------------------------------------------------------------------------

class TestFilenameToCaption:
    def test_strip_fig_prefix(self):
        assert ef.filename_to_caption("fig01_growth_curves.png") == "Growth curves"

    def test_strip_numeric_prefix(self):
        assert ef.filename_to_caption("01_carbon_util.png") == "Carbon util"

    def test_strip_nb_prefix(self):
        assert ef.filename_to_caption("NB00_per_strain.png") == "Per strain"

    def test_strip_figure_word(self):
        assert ef.filename_to_caption("figure_3_inhibition.png") == "Inhibition"

    def test_no_prefix(self):
        assert ef.filename_to_caption("inhibition_heatmap.png") == "Inhibition heatmap"

    def test_empty_after_strip_falls_back(self):
        # If stripping leaves nothing, use the original stem
        result = ef.filename_to_caption("fig01.png")
        assert result  # non-empty
        assert "fig01" in result.lower() or "1" in result


# ---------------------------------------------------------------------------
# REPORT.md image-reference parser
# ---------------------------------------------------------------------------

class TestReportImageRefs:
    def test_simple_image_ref(self):
        text = "Some prose.\n![alpha](figures/a.png)\nMore prose."
        refs = ef.parse_report_image_references(text)
        assert len(refs) == 1
        assert refs[0].alt_text == "alpha"
        assert refs[0].url == "figures/a.png"
        assert refs[0].line == 2

    def test_section_context(self):
        text = (
            "## Results\n"
            "Some prose.\n"
            "![a](figures/x.png)\n"
            "## Discussion\n"
            "![b](figures/y.png)\n"
        )
        refs = ef.parse_report_image_references(text)
        assert len(refs) == 2
        assert refs[0].section == "Results"
        assert refs[1].section == "Discussion"

    def test_multiple_images_per_line(self):
        text = "![a](x.png) and ![b](y.png)\n"
        refs = ef.parse_report_image_references(text)
        assert len(refs) == 2
        assert {r.alt_text for r in refs} == {"a", "b"}

    def test_no_images(self):
        text = "Just prose. No images here.\n"
        assert ef.parse_report_image_references(text) == []


# ---------------------------------------------------------------------------
# Path-expression parser for savefig args
# ---------------------------------------------------------------------------

class TestPathExprParser:
    def _last_str(self, expr: str) -> str | None:
        node = ast.parse(expr, mode="eval").body
        return ef._last_string_in_path_expr(node)

    def test_string_literal(self):
        assert self._last_str("'foo.png'") == "foo.png"

    def test_path_div_string(self):
        assert self._last_str("FIGS / 'foo.png'") == "foo.png"

    def test_path_div_chain(self):
        assert self._last_str("FIGS / 'sub' / 'foo.png'") == "foo.png"

    def test_path_constructor(self):
        assert self._last_str("Path('foo.png')") == "foo.png"

    def test_os_path_join(self):
        assert self._last_str("os.path.join(FIGS, 'foo.png')") == "foo.png"

    def test_str_wrapper(self):
        assert self._last_str("str(FIGS / 'foo.png')") == "foo.png"

    def test_unrecoverable_returns_none(self):
        assert self._last_str("some_function()") is None
        assert self._last_str("a_variable") is None


# ---------------------------------------------------------------------------
# Savefig detection
# ---------------------------------------------------------------------------

class TestSavefigDetection:
    def test_plt_savefig(self):
        node = ast.parse("plt.savefig('a.png')", mode="eval").body
        assert ef._is_savefig_call(node)

    def test_fig_savefig(self):
        node = ast.parse("fig.savefig('a.png')", mode="eval").body
        assert ef._is_savefig_call(node)

    def test_chained_attribute(self):
        node = ast.parse("ax.figure.savefig('a.png')", mode="eval").body
        assert ef._is_savefig_call(node)

    def test_not_savefig(self):
        node = ast.parse("plt.show()", mode="eval").body
        assert not ef._is_savefig_call(node)


# ---------------------------------------------------------------------------
# Notebook walker
# ---------------------------------------------------------------------------

class TestNotebookWalker:
    def test_savefig_call_extracted(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell(
                "import matplotlib.pyplot as plt\n"
                "plt.figure()\n"
                "plt.savefig('figures/foo.png', dpi=150)\n"
            ),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        assert savefigs[0].saved_basename == "foo.png"

    def test_path_expr_savefig(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell("plt.savefig(FIGS / '01_growth_curves.png', dpi=150)\n"),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        assert savefigs[0].saved_basename == "01_growth_curves.png"

    def test_preceding_markdown_captured(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _md_cell("# Section title\n\nThis figure shows growth curves."),
            _code_cell("plt.savefig('a.png')\n"),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        # md_map is keyed by code-cell index (1-based)
        assert 1 in md_map
        assert "growth curves" in md_map[1].lower()

    def test_no_savefig_returns_empty(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell("import pandas\nplt.show()\n"),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert savefigs == []

    def test_magic_lines_dont_crash(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell("%matplotlib inline\n!pip install foo\nplt.savefig('a.png')\n"),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1


# ---------------------------------------------------------------------------
# Phase 2 — matplotlib AST extraction (Source 3)
# ---------------------------------------------------------------------------


class TestStringLiteral:
    def _node(self, expr: str):
        return ast.parse(expr, mode="eval").body

    def test_constant_string(self):
        assert ef._string_literal(self._node("'foo'")) == "foo"

    def test_fstring_no_interpolation(self):
        assert ef._string_literal(self._node("f'foo'")) == "foo"

    def test_fstring_with_interpolation_returns_none(self):
        # f'foo {x}' has both a Constant and a FormattedValue → not a
        # pure string literal.
        assert ef._string_literal(self._node("f'foo {x}'")) is None

    def test_non_string_returns_none(self):
        assert ef._string_literal(self._node("42")) is None
        assert ef._string_literal(self._node("some_var")) is None


class TestSubscriptIndices:
    def _node(self, expr: str):
        return ast.parse(expr, mode="eval").body

    def test_single_constant_index(self):
        assert ef._extract_subscript_indices(self._node("axes[0]")) == (0,)

    def test_two_dim_constant_index(self):
        assert ef._extract_subscript_indices(self._node("axes[0, 1]")) == (0, 1)

    def test_variable_index_returns_none(self):
        assert ef._extract_subscript_indices(self._node("axes[i]")) is None

    def test_non_subscript_returns_none(self):
        assert ef._extract_subscript_indices(self._node("axes")) is None


class TestIdxsToLetter:
    def test_1d_index_to_letter(self):
        assert ef._idxs_to_letter((0,), None) == "A"
        assert ef._idxs_to_letter((1,), None) == "B"
        assert ef._idxs_to_letter((25,), None) == "Z"

    def test_2d_row_major_with_grid_cols(self):
        # 2x2 grid; cols=2; (0,0)=A, (0,1)=B, (1,0)=C, (1,1)=D
        assert ef._idxs_to_letter((0, 0), 2) == "A"
        assert ef._idxs_to_letter((0, 1), 2) == "B"
        assert ef._idxs_to_letter((1, 0), 2) == "C"
        assert ef._idxs_to_letter((1, 1), 2) == "D"

    def test_2d_without_grid_cols_returns_none(self):
        # No grid declaration → can't honestly assign letters.
        assert ef._idxs_to_letter((0, 1), None) is None


class TestClassifyPlotCall:
    def _classify(self, expr: str):
        node = ast.parse(expr, mode="eval").body
        return ef._classify_plot_call(node)

    def test_plt_title(self):
        kind, info = self._classify("plt.title('Foo')")
        assert kind == "title" and info == "Foo"

    def test_plt_xlabel(self):
        kind, info = self._classify("plt.xlabel('X')")
        assert kind == "axes_label" and info == "X"

    def test_ax_set_title(self):
        kind, info = self._classify("ax.set_title('Foo')")
        assert kind == "title" and info == "Foo"

    def test_axes_subscript_set_title_emits_panel_set(self):
        kind, info = self._classify("axes[0, 1].set_title('B')")
        assert kind == "panel_set"
        field, idxs, val = info
        assert field == "title"
        assert idxs == (0, 1)
        assert val == "B"

    def test_plt_suptitle(self):
        kind, info = self._classify("plt.suptitle('Top')")
        assert kind == "suptitle" and info == "Top"

    def test_plt_subplots_grid(self):
        kind, info = self._classify("plt.subplots(2, 3)")
        assert kind == "subplots_grid"
        assert info == {"cols": 3}

    def test_plt_subplots_kwarg_grid(self):
        kind, info = self._classify("plt.subplots(nrows=2, ncols=4)")
        assert kind == "subplots_grid"
        assert info == {"cols": 4}

    def test_plt_subplot_position(self):
        kind, info = self._classify("plt.subplot(2, 2, 3)")
        assert kind == "subplot_pos"
        assert info == {"position": 3}

    def test_legend_with_label_list(self):
        kind, info = self._classify("plt.legend(['a', 'b'])")
        assert kind == "legend"
        assert info == ["a", "b"]

    def test_legend_with_kwarg_labels(self):
        kind, info = self._classify("ax.legend(handles=h, labels=['x', 'y'])")
        assert kind == "legend"
        assert info == ["x", "y"]

    def test_wrapper_function_unrecognized(self):
        # Direct function call (not attribute) → not classified by design.
        kind, info = self._classify("volcano_plot(df)")
        assert kind is None


class TestExtractPlotCalls:
    def test_bare_pyplot_pattern(self):
        savefig_src = (
            "plt.figure()\n"
            "plt.title('Growth curves')\n"
            "plt.xlabel('Time (h)')\n"
            "plt.ylabel('OD600')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=5)
        assert ext.title == "Growth curves"
        assert "Time (h)" in ext.axes_labels
        assert "OD600" in ext.axes_labels
        assert ext.panels == []

    def test_object_oriented_pattern(self):
        savefig_src = (
            "fig, ax = plt.subplots()\n"
            "ax.set_title('Titration')\n"
            "ax.set_xlabel('pH')\n"
            "fig.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=4)
        assert ext.title == "Titration"
        assert "pH" in ext.axes_labels

    def test_fstring_with_interpolation_skipped(self):
        savefig_src = (
            "var = 'X'\n"
            "plt.title(f'Distribution of {var}')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=3)
        assert ext.title is None

    def test_fstring_without_interpolation_recovered(self):
        savefig_src = (
            "plt.title(f'Static title')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=2)
        assert ext.title == "Static title"

    def test_2x2_subplots_yields_four_panels(self):
        savefig_src = (
            "fig, axes = plt.subplots(2, 2)\n"
            "axes[0, 0].set_title('A')\n"
            "axes[0, 1].set_title('B')\n"
            "axes[1, 0].set_title('C')\n"
            "axes[1, 1].set_title('D')\n"
            "fig.savefig('multi.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=6)
        letters = [p.letter for p in ext.panels]
        assert letters == ["A", "B", "C", "D"]
        titles = [p.title for p in ext.panels]
        assert titles == ["A", "B", "C", "D"]

    def test_1x3_subplots_uses_1d_letters(self):
        savefig_src = (
            "fig, axes = plt.subplots(1, 3)\n"
            "axes[0].set_title('One')\n"
            "axes[1].set_title('Two')\n"
            "axes[2].set_title('Three')\n"
            "fig.savefig('row.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=5)
        assert [p.letter for p in ext.panels] == ["A", "B", "C"]
        assert [p.title for p in ext.panels] == ["One", "Two", "Three"]

    def test_subplot_position_pattern(self):
        savefig_src = (
            "plt.subplot(1, 2, 1)\n"
            "plt.title('Left')\n"
            "plt.subplot(1, 2, 2)\n"
            "plt.title('Right')\n"
            "plt.savefig('two.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=5)
        assert [p.letter for p in ext.panels] == ["A", "B"]
        assert [p.title for p in ext.panels] == ["Left", "Right"]

    def test_wrapper_function_yields_empty(self):
        # Direct function call (no setup, no plt.* attributes) → no signal.
        savefig_src = (
            "volcano_plot(df)\n"
            "plt.savefig('v.png')\n"
        )
        ext = ef._extract_plot_calls(None, savefig_src, savefig_line=2)
        assert ext.is_empty()

    def test_setup_cell_calls_carry_over(self):
        setup_src = (
            "plt.figure()\n"
            "plt.title('From setup cell')\n"
            "plt.xlabel('X')\n"
        )
        savefig_src = (
            "plt.ylabel('Y')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(setup_src, savefig_src, savefig_line=2)
        assert ext.title == "From setup cell"
        # Both axes labels merged
        assert "X" in ext.axes_labels
        assert "Y" in ext.axes_labels

    def test_two_savefigs_partition_scopes(self):
        # Two savefigs in same cell; each must see only its own setup.
        savefig_src = (
            "plt.figure()\n"           # line 1
            "plt.title('First')\n"     # line 2
            "plt.savefig('a.png')\n"   # line 3
            "plt.figure()\n"           # line 4
            "plt.title('Second')\n"    # line 5
            "plt.savefig('b.png')\n"   # line 6
        )
        # First savefig at line 3; scope is lines 1-3.
        ext1 = ef._extract_plot_calls(None, savefig_src,
                                       savefig_line=3,
                                       prev_savefig_line=None)
        assert ext1.title == "First"
        # Second savefig at line 6; scope is lines 4-6 (between prior savefig
        # at line 3 and this savefig).
        ext2 = ef._extract_plot_calls(None, savefig_src,
                                       savefig_line=6,
                                       prev_savefig_line=3)
        assert ext2.title == "Second"

    def test_subplots_in_savefig_cell_wipes_setup_cell_panels(self):
        """v0.4 Phase 2 cross-cell bug fix: when the savefig cell creates
        its own figure (plt.subplots/plt.figure), state accumulated from
        the setup cell must be discarded — the setup cell was for a
        previous figure (typical idiom: each cell draws + saves one
        figure, so the immediately-preceding cell IS another figure's
        draw cell, not 'setup').
        """
        # Setup cell: draws a 1x2 multi-panel with titles A/B (intended
        # for some EARLIER savefig — already happened, irrelevant here).
        setup_src = (
            "fig, axes = plt.subplots(1, 2)\n"
            "axes[0].set_title('STALE A')\n"
            "axes[1].set_title('STALE B')\n"
        )
        # Savefig cell: draws ITS OWN 1x2 figure with new titles.
        savefig_src = (
            "fig, axes = plt.subplots(1, 2)\n"
            "axes[0].set_title('FRESH A')\n"
            "axes[1].set_title('FRESH B')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(setup_src, savefig_src, savefig_line=4)
        # Fresh titles only; no leakage from setup cell.
        assert [p.title for p in ext.panels] == ["FRESH A", "FRESH B"]

    def test_plt_figure_resets_setup_cell_title(self):
        # plt.figure() also resets state.
        setup_src = (
            "plt.figure()\n"
            "plt.title('STALE')\n"
        )
        savefig_src = (
            "plt.figure()\n"
            "plt.title('FRESH')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(setup_src, savefig_src, savefig_line=3)
        assert ext.title == "FRESH"

    def test_no_boundary_keeps_setup_calls(self):
        # When the savefig cell does NOT create a new figure, setup
        # cell state remains valid (e.g., setup cell creates fig and
        # sets title; savefig cell only adds axes labels then saves).
        setup_src = (
            "plt.figure()\n"
            "plt.title('Setup title')\n"
        )
        savefig_src = (
            "plt.xlabel('X')\n"
            "plt.savefig('a.png')\n"
        )
        ext = ef._extract_plot_calls(setup_src, savefig_src, savefig_line=2)
        assert ext.title == "Setup title"
        assert "X" in ext.axes_labels

    def test_setup_two_cells_back_NOT_recovered(self):
        # Hard scope cap: only the IMMEDIATELY preceding code cell is
        # walked. A title set two cells back is invisible.
        # (Simulated by passing a setup_cell_source that only has the
        # 1-back content; the 2-back content is unreachable to this
        # function regardless. This test documents the contract.)
        ext = ef._extract_plot_calls(
            setup_cell_source=None,   # 1-back is empty
            savefig_cell_source="plt.savefig('a.png')\n",
            savefig_line=1,
        )
        assert ext.is_empty()


class TestNotebookWalkerPlotCalls:
    def test_savefig_with_pyplot_title_attaches_extraction(
        self, tmp_path: Path,
    ):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell(
                "import matplotlib.pyplot as plt\n"
                "plt.figure()\n"
                "plt.title('Growth curves')\n"
                "plt.xlabel('Time (h)')\n"
                "plt.savefig('a.png')\n"
            ),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        assert savefigs[0].plot_calls is not None
        assert savefigs[0].plot_calls.title == "Growth curves"
        assert "Time (h)" in savefigs[0].plot_calls.axes_labels

    def test_setup_in_prior_code_cell(self, tmp_path: Path):
        # Markdown cells between setup and savefig don't disrupt
        # prev_code_source carry-over.
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell(
                "plt.figure()\n"
                "plt.title('From prior cell')\n"
            ),
            _md_cell("Some intervening markdown."),
            _code_cell(
                "plt.xlabel('X')\n"
                "plt.savefig('a.png')\n"
            ),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        assert savefigs[0].plot_calls is not None
        assert savefigs[0].plot_calls.title == "From prior cell"
        assert "X" in savefigs[0].plot_calls.axes_labels

    def test_no_signal_yields_none_plot_calls(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell("plt.savefig('a.png')\n"),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert savefigs[0].plot_calls is None

    def test_multipanel_subplots_in_one_cell(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell(
                "fig, axes = plt.subplots(2, 2)\n"
                "axes[0, 0].set_title('A')\n"
                "axes[0, 1].set_title('B')\n"
                "axes[1, 0].set_title('C')\n"
                "axes[1, 1].set_title('D')\n"
                "fig.savefig('multi.png')\n"
            ),
        ])
        savefigs, _ = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        ext = savefigs[0].plot_calls
        assert ext is not None
        assert [p.letter for p in ext.panels] == ["A", "B", "C", "D"]
        assert [p.title for p in ext.panels] == ["A", "B", "C", "D"]


class TestBuildFigureRecordsPlotCalls:
    def test_plot_calls_merged_into_description(self, tmp_path: Path):
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01.png"
        f.write_bytes(b"x")

        plot_ext = ef.PlotCallExtraction(
            title="Growth curves",
            axes_labels=["Time", "OD600"],
            panels=[ef.PanelDescriptor(letter="A", title="WT")],
        )
        savefigs = [ef.SavefigCall(
            notebook="nb.ipynb", cell=2, line=5,
            saved_basename="fig01.png",
            raw_call="plt.savefig('fig01.png')",
            preceding_md_cell_index=2,
            plot_calls=plot_ext,
        )]
        records = ef.build_figure_records([f], tmp_path, [], savefigs, {})
        assert len(records) == 1
        d = records[0].description
        assert d.title == "Growth curves"
        assert "Time" in d.axes_labels
        assert "OD600" in d.axes_labels
        assert len(d.panels) == 1 and d.panels[0].letter == "A"
        # Provenance trace updated
        assert any("matplotlib_ast" in r for r in d.source_refs)

    def test_first_non_empty_plot_calls_wins(self, tmp_path: Path):
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01.png"
        f.write_bytes(b"x")

        savefigs = [
            ef.SavefigCall(
                notebook="nb_first.ipynb", cell=1, line=1,
                saved_basename="fig01.png", raw_call="plt.savefig(...)",
                preceding_md_cell_index=None,
                plot_calls=ef.PlotCallExtraction(title="First title"),
            ),
            ef.SavefigCall(
                notebook="nb_second.ipynb", cell=1, line=1,
                saved_basename="fig01.png", raw_call="plt.savefig(...)",
                preceding_md_cell_index=None,
                plot_calls=ef.PlotCallExtraction(title="Second title"),
            ),
        ]
        records = ef.build_figure_records([f], tmp_path, [], savefigs, {})
        assert records[0].description.title == "First title"

    def test_no_plot_calls_leaves_descriptor_ast_fields_empty(
        self, tmp_path: Path,
    ):
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01.png"
        f.write_bytes(b"x")

        savefigs = [ef.SavefigCall(
            notebook="nb.ipynb", cell=1, line=1,
            saved_basename="fig01.png", raw_call="plt.savefig(...)",
            preceding_md_cell_index=None,
            plot_calls=None,
        )]
        records = ef.build_figure_records([f], tmp_path, [], savefigs, {})
        # No AST fields populated; no matplotlib_ast in source_refs.
        d = records[0].description
        assert d.title is None
        assert d.axes_labels == []
        assert d.panels == []
        assert not any("matplotlib_ast" in r for r in d.source_refs)


# ---------------------------------------------------------------------------
# Markdown walk-back (Phase 1a)
# ---------------------------------------------------------------------------

class TestMdWalkbackUnit:
    """Unit tests for _collect_md_walkback against in-memory cell lists.

    These exercise the algorithm directly (no nbformat round-trip) to
    isolate the walk-back logic from notebook I/O and AST parsing.
    """

    @staticmethod
    def _make_cells(spec: list[tuple[str, str]]):
        """spec is a list of (cell_type, source) tuples."""
        class _C:
            def __init__(self, cell_type: str, source: str):
                self.cell_type = cell_type
                self.source = source
        return [_C(t, s) for t, s in spec]

    def test_empty_walkback(self):
        cells = self._make_cells([("code", "plt.savefig('a.png')")])
        assert ef._collect_md_walkback(cells, 0) is None

    def test_single_md_before_savefig(self):
        cells = self._make_cells([
            ("markdown", "Description of figure."),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 1)
        assert result == "Description of figure."

    def test_section_header_stops_walkback(self):
        cells = self._make_cells([
            ("markdown", "# Earlier section"),
            ("markdown", "Should NOT be included."),
            ("markdown", "## Current section"),
            ("markdown", "Should be included."),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 4)
        assert "Should be included." in result
        assert "## Current section" in result
        assert "Should NOT be included." not in result
        assert "# Earlier section" not in result

    def test_section_header_inclusive(self):
        # The section-header cell IS included (heading text is descriptive).
        cells = self._make_cells([
            ("markdown", "## Fig 1: Growth curves"),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 1)
        assert result == "## Fig 1: Growth curves"

    def test_skips_code_cells_during_walkback(self):
        """The dominant idiom that broke v0.3: md → data-prep → savefig."""
        cells = self._make_cells([
            ("markdown", "## Fig 1: Fitness landscape across pH"),
            ("markdown", "We measured fitness at pH 5, 7, 9."),
            ("code", "df = load_data()  # data prep, no savefig"),
            ("code", "plt.plot(df.x, df.y)\nplt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 3)
        assert "Fig 1: Fitness landscape" in result
        assert "pH 5, 7, 9" in result
        # Chronological order: header first, then descriptive prose.
        assert result.index("Fitness landscape") < result.index("pH 5, 7, 9")

    def test_skips_empty_md_cells(self):
        cells = self._make_cells([
            ("markdown", "# Section header"),
            ("markdown", "   \n  \n  "),  # whitespace-only
            ("markdown", "Real content."),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 3)
        assert "Real content." in result
        assert "Section header" in result
        # Empty cell should not appear (whitespace-only)
        assert result.count("\n\n") <= 1  # At most one separator between included cells

    def test_multiple_md_cells_concatenated(self):
        cells = self._make_cells([
            ("markdown", "# Top heading"),
            ("markdown", "Middle paragraph 1."),
            ("markdown", "Middle paragraph 2."),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 3)
        # Chronological order
        assert result.index("Top heading") < result.index("paragraph 1")
        assert result.index("paragraph 1") < result.index("paragraph 2")

    def test_no_section_header_walks_to_start(self):
        # No # anywhere → walk-back goes all the way to notebook start.
        cells = self._make_cells([
            ("markdown", "Plain prose 1."),
            ("markdown", "Plain prose 2."),
            ("code", "plt.savefig('a.png')"),
        ])
        result = ef._collect_md_walkback(cells, 2)
        assert "Plain prose 1." in result
        assert "Plain prose 2." in result


class TestNotebookWalkerWalkback:
    """Integration tests for the walk-back through nbformat round-trip."""

    def test_dominant_idiom_md_then_dataprep_then_savefig(self, tmp_path: Path):
        """The bug fix: v0.3 lost the md cell here; v0.4 must capture it."""
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _md_cell("## Fig 1: Fitness landscape\n\nMeasured at pH 5, 7, 9."),
            _code_cell("# data prep\ndf = load()"),
            _code_cell("plt.plot(df.x, df.y)\nplt.savefig('a.png')"),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        # savefig is in the 2nd code cell (1-based code-cell index = 2)
        assert savefigs[0].cell == 2
        assert 2 in md_map
        assert "Fitness landscape" in md_map[2]
        assert "pH 5, 7, 9" in md_map[2]

    def test_two_savefigs_in_adjacent_cells_share_section_md(self, tmp_path: Path):
        """Multiple savefigs under one section may legitimately share md."""
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _md_cell("## Section title"),
            _md_cell("Description for both figures."),
            _code_cell("plt.figure()\nplt.savefig('a.png')"),
            _code_cell("plt.figure()\nplt.savefig('b.png')"),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 2
        # Both savefigs in different code cells (1 and 2)
        cell_ids = {s.cell for s in savefigs}
        assert cell_ids == {1, 2}
        # Both code cells should have md_walkback populated
        assert 1 in md_map
        assert 2 in md_map
        assert "Section title" in md_map[1]
        assert "Section title" in md_map[2]
        assert "Description for both" in md_map[1]
        assert "Description for both" in md_map[2]

    def test_two_savefigs_in_one_cell_share_md(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _md_cell("## Side-by-side panels"),
            _code_cell(
                "plt.figure()\nplt.savefig('left.png')\n"
                "plt.figure()\nplt.savefig('right.png')\n"
            ),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 2
        # Both in code cell 1
        assert all(s.cell == 1 for s in savefigs)
        # md_map has one entry, used by both savefigs
        assert 1 in md_map
        assert "Side-by-side panels" in md_map[1]

    def test_section_break_isolates_savefigs(self, tmp_path: Path):
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _md_cell("## Section A"),
            _md_cell("Description A."),
            _code_cell("plt.savefig('a.png')"),
            _md_cell("## Section B"),
            _md_cell("Description B."),
            _code_cell("plt.savefig('b.png')"),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 2
        # First savefig (cell 1) should see Section A only
        assert "Section A" in md_map[1]
        assert "Description A" in md_map[1]
        assert "Section B" not in md_map[1]
        # Second savefig (cell 2) should see Section B only
        assert "Section B" in md_map[2]
        assert "Description B" in md_map[2]
        assert "Section A" not in md_map[2]

    def test_savefig_at_notebook_start(self, tmp_path: Path):
        """No preceding markdown → md_map entry absent for that cell."""
        nb_path = tmp_path / "test.ipynb"
        _write_notebook(nb_path, [
            _code_cell("plt.savefig('a.png')"),
        ])
        savefigs, md_map = ef._walk_notebook_savefigs(nb_path, tmp_path)
        assert len(savefigs) == 1
        assert 1 not in md_map  # No preceding md → no entry


# ---------------------------------------------------------------------------
# build_figure_records
# ---------------------------------------------------------------------------

class TestBuildFigureRecords:
    def test_caption_priority(self, tmp_path: Path):
        # Create a fake figure file
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01_growth.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        report_refs = [ef.ReportImageRef(
            line=10,
            alt_text="Growth curves of PA14 across substrates",
            url="figures/fig01_growth.png",
            section="Results",
        )]
        savefigs = [ef.SavefigCall(
            notebook="notebooks/01.ipynb",
            cell=3,
            line=8,
            saved_basename="fig01_growth.png",
            raw_call="plt.savefig('fig01_growth.png')",
            preceding_md_cell_index=3,
        )]
        md_map = {"notebooks/01.ipynb": {3: "Plot the growth curves."}}

        records = ef.build_figure_records(
            [f], tmp_path, report_refs, savefigs, md_map,
        )
        assert len(records) == 1
        rec = records[0]
        # Caption ordering: report first, notebook_md second, filename third
        sources = [c.source for c in rec.captions]
        assert sources[0] == "report"
        assert sources[-1] == "filename"
        assert "Growth curves of PA14" in rec.captions[0].text
        assert rec.savefig_origins[0].notebook == "notebooks/01.ipynb"

    def test_filename_only_when_no_other_sources(self, tmp_path: Path):
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig02_orphan.png"
        f.write_bytes(b"x")
        records = ef.build_figure_records([f], tmp_path, [], [], {})
        assert len(records) == 1
        assert len(records[0].captions) == 1
        assert records[0].captions[0].source == "filename"
        assert records[0].savefig_origins == []
        # v0.4 Phase 1b: empty figure has empty descriptor
        assert records[0].description.is_empty()


# ---------------------------------------------------------------------------
# v0.4 Phase 1b: CaptionDescriptor + FigureRecord.description
# ---------------------------------------------------------------------------

class TestCaptionDescriptor:
    def test_empty_descriptor_is_empty(self):
        d = ef.CaptionDescriptor()
        assert d.is_empty()

    def test_descriptor_with_title_not_empty(self):
        d = ef.CaptionDescriptor(title="Fig 1: Growth")
        assert not d.is_empty()

    def test_descriptor_with_notebook_prose_not_empty(self):
        d = ef.CaptionDescriptor(notebook_prose="Some prose.")
        assert not d.is_empty()

    def test_descriptor_with_empty_lists_is_empty(self):
        # Lists default to empty; that alone doesn't count as "populated"
        d = ef.CaptionDescriptor(axes_labels=[], legend_labels=[], panels=[])
        assert d.is_empty()

    def test_descriptor_with_panels_not_empty(self):
        d = ef.CaptionDescriptor(panels=[ef.PanelDescriptor(letter="A")])
        assert not d.is_empty()

    def test_descriptor_to_dict_round_trips(self):
        d = ef.CaptionDescriptor(
            title="T",
            axes_labels=["X", "Y"],
            legend_labels=["L1"],
            notebook_prose="Prose.",
            panels=[ef.PanelDescriptor(letter="A", title="Panel A")],
            source_refs=["notebook_md_walkback(nb.ipynb)"],
        )
        out = d.to_dict()
        assert out["title"] == "T"
        assert out["axes_labels"] == ["X", "Y"]
        assert out["legend_labels"] == ["L1"]
        assert out["notebook_prose"] == "Prose."
        assert out["panels"][0]["letter"] == "A"
        assert out["panels"][0]["title"] == "Panel A"
        assert out["source_refs"] == ["notebook_md_walkback(nb.ipynb)"]


class TestFigureRecordDescriptor:
    def test_default_description_is_empty(self):
        rec = ef.FigureRecord(
            path="figures/x.png", filename="x.png",
            size_bytes=100, format="png",
        )
        assert isinstance(rec.description, ef.CaptionDescriptor)
        assert rec.description.is_empty()

    def test_to_dict_includes_description(self):
        rec = ef.FigureRecord(
            path="figures/x.png", filename="x.png",
            size_bytes=100, format="png",
        )
        rec.description.notebook_prose = "Prose."
        rec.description.source_refs.append("notebook_md_walkback(nb.ipynb)")
        d = rec.to_dict()
        assert "description" in d
        assert d["description"]["notebook_prose"] == "Prose."
        assert d["description"]["source_refs"] == ["notebook_md_walkback(nb.ipynb)"]

    def test_build_records_populates_description_from_walkback(self, tmp_path: Path):
        # Full walkback content gets stored UNREDACTED (not 280-truncated
        # like the CaptionCandidate.text field).
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01_growth.png"
        f.write_bytes(b"x")

        # 350-char preceding_md (longer than CaptionCandidate.text's 280
        # cap; should be preserved in description.notebook_prose).
        long_md = (
            "## Fig 1: Growth\n\n"
            + ("Prose paragraph describing the figure in detail. " * 6)
        )
        savefigs = [ef.SavefigCall(
            notebook="nb.ipynb", cell=2, line=5,
            saved_basename="fig01_growth.png",
            raw_call="plt.savefig('fig01_growth.png')",
            preceding_md_cell_index=2,
        )]
        md_map = {"nb.ipynb": {2: long_md}}
        records = ef.build_figure_records([f], tmp_path, [], savefigs, md_map)
        assert len(records) == 1
        rec = records[0]
        # CaptionCandidate.text is 280-truncated
        nb_md_caption = next(
            c for c in rec.captions if c.source == "notebook_md")
        assert len(nb_md_caption.text) <= 280
        # CaptionDescriptor.notebook_prose has the FULL walkback
        assert rec.description.notebook_prose == long_md
        assert "notebook_md_walkback(nb.ipynb)" in rec.description.source_refs

    def test_description_text_cap_enforced(self, tmp_path: Path):
        # Walkbacks exceeding _DESCRIPTION_TEXT_CAP are truncated.
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01.png"
        f.write_bytes(b"x")

        huge_md = "x" * (ef._DESCRIPTION_TEXT_CAP + 500)
        savefigs = [ef.SavefigCall(
            notebook="nb.ipynb", cell=1, line=1,
            saved_basename="fig01.png", raw_call="plt.savefig('fig01.png')",
            preceding_md_cell_index=1,
        )]
        md_map = {"nb.ipynb": {1: huge_md}}
        records = ef.build_figure_records([f], tmp_path, [], savefigs, md_map)
        assert len(records[0].description.notebook_prose) == ef._DESCRIPTION_TEXT_CAP

    def test_first_walkback_wins_with_multiple_savefigs(self, tmp_path: Path):
        # When a figure is saved by multiple savefigs across notebooks,
        # the FIRST non-empty walkback is used (avoids duplication).
        figdir = tmp_path / "figures"
        figdir.mkdir()
        f = figdir / "fig01.png"
        f.write_bytes(b"x")

        savefigs = [
            ef.SavefigCall(
                notebook="nb_first.ipynb", cell=1, line=1,
                saved_basename="fig01.png", raw_call="plt.savefig(...)",
                preceding_md_cell_index=1,
            ),
            ef.SavefigCall(
                notebook="nb_second.ipynb", cell=1, line=1,
                saved_basename="fig01.png", raw_call="plt.savefig(...)",
                preceding_md_cell_index=1,
            ),
        ]
        md_map = {
            "nb_first.ipynb": {1: "First notebook prose."},
            "nb_second.ipynb": {1: "Second notebook prose."},
        }
        records = ef.build_figure_records([f], tmp_path, [], savefigs, md_map)
        assert records[0].description.notebook_prose == "First notebook prose."
        assert "notebook_md_walkback(nb_first.ipynb)" in records[0].description.source_refs
        # Both savefigs still recorded as origins
        assert len(records[0].savefig_origins) == 2


# ---------------------------------------------------------------------------
# Inventory summary
# ---------------------------------------------------------------------------

class TestInventorySummary:
    def test_summary_counts(self):
        report = ef.FigureInventoryReport(
            project_dir="/tmp/x",
            figures_dirs=["figures"],
            figures=[
                ef.FigureRecord(
                    path="figures/a.png", filename="a.png",
                    size_bytes=100, format="png",
                    captions=[
                        ef.CaptionCandidate(source="report", text="a"),
                        ef.CaptionCandidate(source="filename", text="A"),
                    ],
                    savefig_origins=[ef.SavefigOrigin(
                        notebook="n.ipynb", cell=1, line=1, raw_call="x")],
                ),
                ef.FigureRecord(
                    path="figures/b.png", filename="b.png",
                    size_bytes=200, format="png",
                    captions=[ef.CaptionCandidate(source="filename", text="B")],
                ),
                ef.FigureRecord(
                    path="figures/c.svg", filename="c.svg",
                    size_bytes=50, format="svg",
                    captions=[ef.CaptionCandidate(source="filename", text="C")],
                ),
            ],
        )
        s = report.to_dict()["summary"]
        assert s["total_figures"] == 3
        assert s["total_size_bytes"] == 350
        assert s["by_format"] == {"png": 2, "svg": 1}
        assert s["with_notebook_origin"] == 1
        assert s["with_report_reference"] == 1
        assert s["filename_only"] == 2


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------

class TestMdFormatter:
    def test_empty_renders(self):
        report = ef.FigureInventoryReport(
            project_dir="/tmp/x", figures_dirs=[], figures=[],
        )
        md = ef.format_figures_inventory_md(report)
        assert "Figures Inventory" in md
        assert "No figures directory found" in md

    def test_with_figures_renders(self):
        report = ef.FigureInventoryReport(
            project_dir="/tmp/x",
            figures_dirs=["figures"],
            figures=[ef.FigureRecord(
                path="figures/a.png", filename="a.png",
                size_bytes=1024, format="png",
                captions=[
                    ef.CaptionCandidate(
                        source="report", text="Growth curves",
                        context={"section": "Results", "line": 10},
                    ),
                ],
                savefig_origins=[ef.SavefigOrigin(
                    notebook="notebooks/01.ipynb", cell=3, line=5,
                    raw_call="plt.savefig('a.png')",
                )],
            )],
        )
        md = ef.format_figures_inventory_md(report)
        assert "figures/a.png" in md
        assert "Growth curves" in md
        assert "in Results" in md
        assert "notebooks/01.ipynb" in md


# ---------------------------------------------------------------------------
# v0.4 Phase 1b: inventory schema v2 (Description block)
# ---------------------------------------------------------------------------

class TestInventorySchemaV2:
    def _make_report_with_descriptor(self, descriptor: ef.CaptionDescriptor):
        return ef.FigureInventoryReport(
            project_dir="/tmp/x",
            figures_dirs=["figures"],
            figures=[ef.FigureRecord(
                path="figures/a.png", filename="a.png",
                size_bytes=1024, format="png",
                captions=[ef.CaptionCandidate(
                    source="filename", text="A")],
                description=descriptor,
            )],
        )

    def test_v2_schema_header_comment_present(self):
        report = ef.FigureInventoryReport(
            project_dir="/tmp/x", figures_dirs=[], figures=[],
        )
        md = ef.format_figures_inventory_md(report)
        # Schema header comment must be the FIRST line so consumers can
        # detect schema version from a fixed offset.
        first_line = md.split("\n", 1)[0]
        assert first_line == "<!-- inventory_schema_version: 2 -->"

    def test_description_block_omitted_when_descriptor_empty(self):
        # Empty descriptor → no Description block in output (avoids
        # noisy "**Description:**\n" header with no content).
        report = self._make_report_with_descriptor(ef.CaptionDescriptor())
        md = ef.format_figures_inventory_md(report)
        assert "**Description:**" not in md

    def test_description_block_with_notebook_prose(self):
        report = self._make_report_with_descriptor(ef.CaptionDescriptor(
            notebook_prose="## Fig 1\n\nGrowth curves of PA14.",
            source_refs=["notebook_md_walkback(nb.ipynb)"],
        ))
        md = ef.format_figures_inventory_md(report)
        assert "**Description:**" in md
        assert "Notebook prose:" in md
        # Multi-line prose rendered as blockquote
        assert "> ## Fig 1" in md
        assert "> Growth curves of PA14." in md
        # Provenance trace included
        assert "notebook_md_walkback(nb.ipynb)" in md

    def test_description_block_with_title_and_axes(self):
        # Phase 2-shaped descriptor: title + axes (no prose).
        report = self._make_report_with_descriptor(ef.CaptionDescriptor(
            title="Fitness vs coverage",
            axes_labels=["Fitness score", "Coverage"],
            legend_labels=["DvH", "PA14"],
            source_refs=["matplotlib_ast"],
        ))
        md = ef.format_figures_inventory_md(report)
        assert "_Title:_ Fitness vs coverage" in md
        assert "_Axes:_ Fitness score; Coverage" in md
        assert "_Legend:_ DvH; PA14" in md

    def test_description_block_with_panels(self):
        report = self._make_report_with_descriptor(ef.CaptionDescriptor(
            title="Multi-panel summary",
            panels=[
                ef.PanelDescriptor(letter="A", title="Left panel"),
                ef.PanelDescriptor(letter="B", title="Right panel"),
            ],
            source_refs=["matplotlib_ast"],
        ))
        md = ef.format_figures_inventory_md(report)
        assert "_Panels:_" in md
        assert "(A) Left panel" in md
        assert "(B) Right panel" in md

    def test_v2_inventory_round_trips_through_downstream_parser(
        self, tmp_path: Path,
    ):
        """Lock the v2 schema contract: extract_figures.py emits v2;
        paper_writer_helpers._parse_figures_inventory_captions consumes it
        without drift. If a future schema change breaks this round-trip,
        either the schema or the parser must be updated in lock-step.
        """
        import importlib.util
        import sys
        # Find paper_writer_helpers via the same path the package uses.
        from beril_paper_writer.skill.tools import extract_figures as ef_mod
        helpers_path = (
            Path(ef_mod.__file__).parent / "paper_writer_helpers.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_helpers_for_v2_test", helpers_path,
        )
        helpers = importlib.util.module_from_spec(spec)
        sys.modules["_helpers_for_v2_test"] = helpers
        spec.loader.exec_module(helpers)

        # Build a synthetic v2 inventory with 2 figures, one with rich
        # description and one with only filename caption.
        report = ef.FigureInventoryReport(
            project_dir="/tmp/x",
            figures_dirs=["figures"],
            figures=[
                ef.FigureRecord(
                    path="figures/fig01.png", filename="fig01.png",
                    size_bytes=1024, format="png",
                    captions=[
                        ef.CaptionCandidate(
                            source="report",
                            text="Growth curves of PA14",
                            context={"section": "Results", "line": 10},
                        ),
                        ef.CaptionCandidate(
                            source="filename", text="Fig01",
                        ),
                    ],
                    description=ef.CaptionDescriptor(
                        notebook_prose="## Fig 1\n\nDescriptive prose.",
                        source_refs=["notebook_md_walkback(nb.ipynb)"],
                    ),
                ),
                ef.FigureRecord(
                    path="figures/fig02.png", filename="fig02.png",
                    size_bytes=512, format="png",
                    captions=[ef.CaptionCandidate(
                        source="filename", text="Fig02",
                    )],
                    # Empty description; should produce no Description block.
                ),
            ],
        )
        inv_path = tmp_path / "figures_inventory.md"
        inv_path.write_text(
            ef.format_figures_inventory_md(report), encoding="utf-8",
        )

        captions = helpers._parse_figures_inventory_captions(inv_path)
        assert set(captions.keys()) == {"fig01.png", "fig02.png"}
        # First figure: top caption is REPORT-derived
        assert captions["fig01.png"] == "Growth curves of PA14"
        # Second figure: only filename candidate, that's the top
        assert captions["fig02.png"] == "Fig02"

    def test_blockquote_handles_blank_lines_in_prose(self):
        # Blank lines in prose render as bare ">" (not "> " + space) so
        # CommonMark blockquote semantics are preserved without trailing
        # whitespace artifacts.
        report = self._make_report_with_descriptor(ef.CaptionDescriptor(
            notebook_prose="Para 1.\n\nPara 2.",
            source_refs=["notebook_md_walkback(nb.ipynb)"],
        ))
        md = ef.format_figures_inventory_md(report)
        # Either ">\n" (blank-line variant) or "> " followed by content
        lines = md.split("\n")
        # Find the blockquote section
        bq_lines = [l for l in lines if l.startswith(">")]
        assert any(l == ">" for l in bq_lines), \
            "Blank line in prose should render as bare '>' for CommonMark"


# ---------------------------------------------------------------------------
# End-to-end against synthetic project
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_synthetic_project(self, tmp_path: Path):
        proj = tmp_path / "p"
        (proj / "figures").mkdir(parents=True)
        (proj / "notebooks").mkdir()

        # Figure file
        (proj / "figures" / "01_growth.png").write_bytes(b"\x89PNG" + b"\x00" * 50)

        # REPORT.md with a caption
        (proj / "REPORT.md").write_text(
            "## Results\n\nWe observed growth.\n"
            "![Growth curves of PA14](figures/01_growth.png)\n",
            encoding="utf-8",
        )

        # Notebook that produces it
        _write_notebook(proj / "notebooks" / "01.ipynb", [
            _md_cell("Plot the growth curves."),
            _code_cell(
                "import matplotlib.pyplot as plt\n"
                "plt.figure()\n"
                "plt.savefig('../figures/01_growth.png', dpi=150)\n"
            ),
        ])

        report = ef.extract_figures(proj)
        d = report.to_dict()
        assert d["summary"]["total_figures"] == 1
        assert d["summary"]["with_notebook_origin"] == 1
        assert d["summary"]["with_report_reference"] == 1
        fig = d["figures"][0]
        sources = [c["source"] for c in fig["captions"]]
        # Report caption first; filename last; notebook_md somewhere in between
        assert sources[0] == "report"
        assert sources[-1] == "filename"


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------

class TestCLI:
    SCRIPT = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "extract_figures.py"
    )

    def test_cli_runs(self, tmp_path: Path):
        proj = tmp_path / "p"
        (proj / "figures").mkdir(parents=True)
        (proj / "figures" / "a.png").write_bytes(b"x")
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(proj)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        report = json.loads(proc.stdout)
        assert report["summary"]["total_figures"] == 1

    def test_cli_writes_inventory_md(self, tmp_path: Path):
        proj = tmp_path / "p"
        (proj / "figures").mkdir(parents=True)
        (proj / "figures" / "a.png").write_bytes(b"x")
        outdir = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(proj),
             "--output-dir", str(outdir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert (outdir / "figures_inventory.md").is_file()

    def test_cli_missing_dir_returns_1(self, tmp_path: Path):
        bogus = tmp_path / "nope"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(bogus)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1
