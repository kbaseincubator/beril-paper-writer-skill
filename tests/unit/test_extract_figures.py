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
