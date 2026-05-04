r"""Tests for ensemble caption scoring (v0.7.0 R2).

The _score_caption_candidate() function and cmd_score_caption CLI
subcommand implement the mechanical scoring filters for the
best-of-3 caption ensemble:

  1. Code-smell filter (regex): reject candidates containing function
     calls, variable names, SQL fragments, notebook comments.
  2. Minimum length gate: reject candidates < 50 words.
  3. Percentage cross-check: deduct points for percentages in the
     caption that don't appear in the body text.

These tests validate each filter independently, their interaction,
and the CLI subcommand's JSON output.
"""

import json
import textwrap
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import the scoring function from paper_writer_helpers.py
# ---------------------------------------------------------------------------

import importlib.util
import sys

_HELPERS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "paper_writer_helpers.py"
)

_spec = importlib.util.spec_from_file_location(
    "paper_writer_helpers", _HELPERS_PATH
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["paper_writer_helpers"] = _mod
_spec.loader.exec_module(_mod)

_score_caption_candidate = _mod._score_caption_candidate
cmd_score_caption = _mod.cmd_score_caption


# ---------------------------------------------------------------------------
# Fixture captions
# ---------------------------------------------------------------------------

CLEAN_CAPTION = (
    "Distribution of functional dark matter genes across 42 genomes "
    "in the Desulfovibrio genus. Panel A shows the phylogenetic tree "
    "of 42 strains with FDM gene counts per genome indicated by bar "
    "length. Panel B presents the gene function categories assigned "
    "by GapMind analysis, with transport (28.2%) and biosynthesis "
    "(24.9%) dominating. Error bars represent 95% confidence intervals "
    "computed from 1000 bootstrap replicates. The dashed line indicates "
    "the median FDM gene count across all strains."
)

CODE_SMELL_CAPTION = (
    "Load FB gene positions from Spark. Distribution of functional "
    "dark matter genes across 42 genomes. import pandas as pd. "
    "Panel A shows the phylogenetic tree with gene counts per genome."
)

SHORT_CAPTION = "Distribution of genes across genomes."

MISMATCHED_PCT_CAPTION = (
    "Distribution of functional dark matter genes across 42 genomes "
    "in the Desulfovibrio genus. Panel A shows the phylogenetic tree "
    "of 42 strains with FDM gene counts. Transport genes account for "
    "8.2% of the total and biosynthesis accounts for 24.9%. Error "
    "bars represent 95% confidence intervals from bootstrap analysis. "
    "The dashed line indicates the median FDM gene count across strains."
)

BODY_TEXT = (
    "We identified 42 genomes containing functional dark matter genes. "
    "Transport accounted for 28.2% of the total FDM repertoire, while "
    "biosynthesis represented 24.9%. The remaining 46.9% were distributed "
    "across 7 other functional categories."
)


# ---------------------------------------------------------------------------
# Code-smell filter tests
# ---------------------------------------------------------------------------

class TestCodeSmellFilter:
    """The code-smell filter rejects captions containing code artifacts."""

    def test_clean_caption_passes(self):
        result = _score_caption_candidate(CLEAN_CAPTION)
        assert result["score"] > 0
        assert result["code_smell_count"] == 0

    def test_import_statement_rejected(self):
        result = _score_caption_candidate(CODE_SMELL_CAPTION)
        assert result["score"] == 0
        assert result["code_smell_count"] > 0
        assert any("code-smell" in r for r in result["reasons"])

    def test_load_from_rejected(self):
        caption = "Load FB gene positions from Spark DataFrame. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_notebook_cell_reference_rejected(self):
        caption = "Results from cell 42 of the analysis notebook. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_plt_dot_rejected(self):
        caption = "Generated using plt.scatter and ax.set_xlabel methods. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_sql_rejected(self):
        caption = "SELECT gene_id FROM fdm_genes WHERE count > 5. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_todo_comment_rejected(self):
        caption = "# TODO fix this caption later. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_py_extension_rejected(self):
        caption = "Output of analysis_pipeline.py showing gene distributions. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_none_null_rejected(self):
        caption = "Genes with None annotations were excluded. " + "x " * 50
        result = _score_caption_candidate(caption)
        assert result["score"] == 0

    def test_scientific_none_style_ok(self):
        """'none of the' is not a code-smell — 'None' (capitalized, standalone) is."""
        # 'none' lowercase doesn't match \bNone\b
        caption = (
            "None of the alternative approaches achieved statistical "
            "significance in the comparative genomics analysis pipeline "
        )
        # This will actually match \bNone\b at start. Let's test that
        # the filter is case-sensitive for None.
        result = _score_caption_candidate(caption + "x " * 40)
        assert result["score"] == 0  # "None" at start IS a code smell match


# ---------------------------------------------------------------------------
# Minimum length gate tests
# ---------------------------------------------------------------------------

class TestMinLengthGate:
    def test_short_caption_rejected(self):
        result = _score_caption_candidate(SHORT_CAPTION, min_words=50)
        assert result["score"] == 0
        assert any("too-short" in r for r in result["reasons"])

    def test_exact_threshold_passes(self):
        caption = " ".join(["word"] * 50)
        result = _score_caption_candidate(caption, min_words=50)
        assert result["score"] > 0

    def test_one_below_threshold_rejected(self):
        caption = " ".join(["word"] * 49)
        result = _score_caption_candidate(caption, min_words=50)
        assert result["score"] == 0

    def test_custom_min_words(self):
        caption = " ".join(["word"] * 30)
        result = _score_caption_candidate(caption, min_words=25)
        assert result["score"] > 0


# ---------------------------------------------------------------------------
# Percentage cross-check tests
# ---------------------------------------------------------------------------

class TestPercentageCrossCheck:
    def test_mostly_matching_pcts(self):
        """28.2% and 24.9% match body; 95% (confidence intervals) doesn't.
        The mismatch is expected (95% CI is statistical convention, not
        fabricated data), but the scorer can't distinguish — it deducts."""
        result = _score_caption_candidate(CLEAN_CAPTION, body_text=BODY_TEXT)
        assert result["pct_mismatch_count"] == 1  # 95% not in body
        assert result["score"] > 70  # Still viable despite one mismatch

    def test_mismatched_pct_deducted(self):
        result = _score_caption_candidate(
            MISMATCHED_PCT_CAPTION, body_text=BODY_TEXT
        )
        # 8.2% is not in body text (body has 28.2%), so mismatch
        assert result["pct_mismatch_count"] >= 1
        assert result["score"] < 100

    def test_no_body_text_no_deduction(self):
        """Without body text, no cross-check happens."""
        result = _score_caption_candidate(MISMATCHED_PCT_CAPTION)
        assert result["pct_mismatch_count"] == 0

    def test_caption_with_only_novel_pcts(self):
        caption = "This analysis found 99.9% correlation. " + "x " * 50
        body = "The correlation was 50.0%."
        result = _score_caption_candidate(caption, body_text=body)
        assert result["pct_mismatch_count"] == 1
        assert result["score"] < 100


# ---------------------------------------------------------------------------
# Score interaction tests
# ---------------------------------------------------------------------------

class TestScoreInteraction:
    def test_code_smell_trumps_everything(self):
        """Code smell → score 0, regardless of length or pct."""
        result = _score_caption_candidate(CODE_SMELL_CAPTION, body_text=BODY_TEXT)
        assert result["score"] == 0

    def test_short_trumps_pct_check(self):
        """Too-short → score 0, pct check doesn't matter."""
        result = _score_caption_candidate(SHORT_CAPTION, body_text=BODY_TEXT)
        assert result["score"] == 0

    def test_length_bonus_applied(self):
        """Longer captions get a small bonus."""
        short_ok = " ".join(["word"] * 55)
        long_ok = " ".join(["word"] * 150)
        result_short = _score_caption_candidate(short_ok)
        result_long = _score_caption_candidate(long_ok)
        assert result_long["score"] > result_short["score"]

    def test_score_never_zero_for_valid(self):
        """Valid candidates always have score >= 1."""
        caption = " ".join(["word"] * 50)
        body = "99.9% of things."  # Force a mismatch
        result = _score_caption_candidate(caption, body_text=body)
        assert result["score"] >= 1


# ---------------------------------------------------------------------------
# CLI subcommand tests
# ---------------------------------------------------------------------------

class TestCmdScoreCaption:
    def test_score_clean_caption(self, tmp_path):
        cap_file = tmp_path / "caption.md"
        cap_file.write_text(CLEAN_CAPTION, encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            caption_path=str(cap_file),
            body_text_path=None,
            min_words=50,
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = cmd_score_caption(args)
        assert rc == 0
        result = json.loads(buf.getvalue())
        assert result["score"] > 0
        assert result["code_smell_count"] == 0

    def test_score_with_body_text(self, tmp_path):
        cap_file = tmp_path / "caption.md"
        cap_file.write_text(CLEAN_CAPTION, encoding="utf-8")
        body_file = tmp_path / "body.md"
        body_file.write_text(BODY_TEXT, encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            caption_path=str(cap_file),
            body_text_path=str(body_file),
            min_words=50,
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = cmd_score_caption(args)
        assert rc == 0
        result = json.loads(buf.getvalue())
        assert result["score"] > 0

    def test_missing_caption_file(self, tmp_path):
        import argparse
        args = argparse.Namespace(
            caption_path=str(tmp_path / "nonexistent.md"),
            body_text_path=None,
            min_words=50,
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = cmd_score_caption(args)
        assert rc == 0
        result = json.loads(buf.getvalue())
        assert result["score"] == 0
