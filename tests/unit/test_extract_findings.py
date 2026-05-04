r"""Tests for finding extraction (v0.7.0 R1: rewrite context reduction).

The extract-findings subcommand and its helper _extract_finding_full_text()
produce pre-filtered review excerpts for rewrite.v1 dispatches. Instead of
passing the full review file (~50-200K tokens) to the rewriter, the
orchestrator extracts only the findings routed to the target section
(~1-5K tokens).

These tests validate:
  - Single finding extraction from realistic review markdown
  - Multiple finding extraction
  - Missing finding ID (returns empty string / WARNING)
  - Finding boundary detection at severity headers, section breaks
  - The cmd_extract_findings CLI subcommand (stdout capture)
"""

import re
import textwrap
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import the helpers directly from paper_writer_helpers.py
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

# Load the module via importlib so we don't need the package installed.
_spec = importlib.util.spec_from_file_location(
    "paper_writer_helpers", _HELPERS_PATH
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["paper_writer_helpers"] = _mod
_spec.loader.exec_module(_mod)

_extract_finding_full_text = _mod._extract_finding_full_text


# ---------------------------------------------------------------------------
# Realistic review fixture
# ---------------------------------------------------------------------------

SAMPLE_REVIEW = textwrap.dedent("""\
    # Review: draft_1_review_1

    ## Section: Results

    ### Critical

    - **C1: Overclaim — unsupported causal language**
      The Results section states "FDM genes *caused* the observed phenotype
      shift" (§Results ¶3). The experimental design (single-timepoint
      comparative transcriptomics) does not support causal inference. Replace
      with associative language ("correlated with" / "were associated with").

    - **C2: Ghost statistic — 85/100 agreement with no Results anchor**
      The Discussion claims "85 of 100 bootstrapped trees agreed with the
      primary topology" but this statistic appears nowhere in Results or
      Methods. Either surface the bootstrap analysis in Results or remove
      the claim from Discussion.

    ### Important

    - **I1: Missing error bars on Figure 3 panel B**
      Figure 3B shows mean expression ratios without confidence intervals
      or standard deviations. Add error bars or note their absence in the
      caption as a limitation.

    - **I2: Bare percentage — "73.7%" without adjacent count**
      §Results ¶5 states "73.7% of the identified genes..." without the
      denominator. Rephrase as "N of M genes (73.7%)."

    ### Suggested

    - **S1: Consider citing Rodriguez et al. 2024**
      The phage-host interaction paragraph would benefit from citing
      Rodriguez et al. (2024) which reports similar FDM gene distributions
      in marine sediments.

    ## Section: Methods

    ### Important

    - **I3: Pipeline version not specified**
      The bioinformatics pipeline is described as "standard BERIL pipeline"
      without specifying the version or commit hash. Add the exact version
      for reproducibility.

    ---

    ## Summary

    3 Critical, 4 Important, 1 Suggested findings across Results and
    Methods sections.
""")


# ---------------------------------------------------------------------------
# _extract_finding_full_text tests
# ---------------------------------------------------------------------------

class TestExtractFindingFullText:
    """Tests for the low-level per-finding extractor."""

    def setup_method(self):
        self.lines = SAMPLE_REVIEW.splitlines()

    def test_extract_c1(self):
        text = _extract_finding_full_text(self.lines, "C1")
        assert "Overclaim" in text
        assert "causal inference" in text
        assert "C1:" in text

    def test_extract_c2(self):
        text = _extract_finding_full_text(self.lines, "C2")
        assert "Ghost statistic" in text
        assert "85 of 100" in text

    def test_extract_i1(self):
        text = _extract_finding_full_text(self.lines, "I1")
        assert "error bars" in text
        assert "Figure 3B" in text

    def test_extract_i2(self):
        text = _extract_finding_full_text(self.lines, "I2")
        assert "73.7%" in text
        assert "denominator" in text

    def test_extract_s1(self):
        text = _extract_finding_full_text(self.lines, "S1")
        assert "Rodriguez" in text

    def test_extract_i3_after_section_break(self):
        """I3 is under ## Section: Methods — should still be found."""
        text = _extract_finding_full_text(self.lines, "I3")
        assert "Pipeline version" in text
        assert "BERIL pipeline" in text

    def test_missing_finding_returns_empty(self):
        text = _extract_finding_full_text(self.lines, "C99")
        assert text == ""

    def test_finding_does_not_bleed_into_next(self):
        """C1 text should NOT contain C2 content."""
        text = _extract_finding_full_text(self.lines, "C1")
        assert "Ghost statistic" not in text
        assert "85 of 100" not in text

    def test_finding_stops_at_severity_header(self):
        """C2 (last Critical) stops at ### Important."""
        text = _extract_finding_full_text(self.lines, "C2")
        assert "Missing error bars" not in text

    def test_finding_stops_at_section_break(self):
        """I3 (under Methods) stops at the --- separator."""
        text = _extract_finding_full_text(self.lines, "I3")
        assert "Summary" not in text


# ---------------------------------------------------------------------------
# cmd_extract_findings tests (CLI subcommand)
# ---------------------------------------------------------------------------

class TestCmdExtractFindings:
    """Tests for the extract-findings CLI subcommand."""

    def _run_extract(self, review_text, finding_ids, tmp_path):
        """Write review to a temp file and run cmd_extract_findings."""
        review_file = tmp_path / "review.md"
        review_file.write_text(review_text, encoding="utf-8")

        # Build a fake argparse Namespace
        import argparse
        args = argparse.Namespace(
            review_path=str(review_file),
            finding_ids=finding_ids,
        )

        # Capture stdout
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = _mod.cmd_extract_findings(args)
        return rc, buf.getvalue()

    def test_extract_single_finding(self, tmp_path):
        rc, output = self._run_extract(SAMPLE_REVIEW, "C1", tmp_path)
        assert rc == 0
        assert "Finding C1" in output
        assert "Overclaim" in output

    def test_extract_multiple_findings(self, tmp_path):
        rc, output = self._run_extract(SAMPLE_REVIEW, "C1,I2,I3", tmp_path)
        assert rc == 0
        assert "Finding C1" in output
        assert "Finding I2" in output
        assert "Finding I3" in output

    def test_extract_missing_finding_warns(self, tmp_path):
        rc, output = self._run_extract(SAMPLE_REVIEW, "C99", tmp_path)
        # Returns 1 when no findings extracted at all
        assert rc == 1
        assert "WARNING" in output

    def test_extract_mixed_found_and_missing(self, tmp_path):
        rc, output = self._run_extract(SAMPLE_REVIEW, "C1,C99", tmp_path)
        # Returns 0 because at least C1 was found
        assert rc == 0
        assert "Overclaim" in output
        assert "C99 not found" in output

    def test_missing_review_file(self, tmp_path):
        import argparse
        args = argparse.Namespace(
            review_path=str(tmp_path / "nonexistent.md"),
            finding_ids="C1",
        )
        buf = StringIO()
        with patch("sys.stdout", buf):
            rc = _mod.cmd_extract_findings(args)
        assert rc == 1
        assert "not found" in buf.getvalue()

    def test_empty_finding_ids(self, tmp_path):
        rc, output = self._run_extract(SAMPLE_REVIEW, "", tmp_path)
        assert rc == 1
        assert "no finding IDs" in output

    def test_output_is_self_contained_markdown(self, tmp_path):
        """Output should be valid markdown with headers per finding."""
        rc, output = self._run_extract(SAMPLE_REVIEW, "C1,I1,S1", tmp_path)
        assert rc == 0
        # Should have markdown headers for each finding
        assert output.count("### Finding") == 3
        # Should have the header line identifying the source
        assert "Extracted findings" in output
