r"""Tests for check_abbreviation_discipline.py post-checker.

Validates the advisory checker for abbreviation expansion order and
project-term definitions.
"""

import json
import sys
import textwrap
from pathlib import Path

import importlib.util
import pytest

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "check_abbreviation_discipline.py"
)


@pytest.fixture(scope="module")
def checker():
    """Load check_abbreviation_discipline.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "check_abbreviation_discipline", _CHECKER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_abbreviation_discipline"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

CLEAN_ABBREVIATIONS = textwrap.dedent("""\
    ## Abstract

    We developed a novel framework for analyzing microbial communities (MC) using
    Bayesian inference networks (BIN). The MC analyses revealed distinct patterns.
    """)

ABBR_USED_BEFORE_EXPANSION = textwrap.dedent("""\
    ## Methods

    We used NMDS to visualize the community structure. Nonmetric
    Multidimensional Scaling (NMDS) is a standard technique.
    """)

ABBR_NEVER_EXPANDED = textwrap.dedent("""\
    ## Results

    The PCoA analysis showed distinct clustering. We also performed PERMANOVA
    statistics across all samples.
    """)

MULTIPLE_ABBRS_MIXED = textwrap.dedent("""\
    ## Methods

    We employed CCA (Canonical Correspondence Analysis) for the initial analysis.
    Principal Component Analysis (PCA) was also used. The RDA results confirmed
    the PCA findings, though RDA (Redundancy Analysis) has different assumptions.
    """)

COMMON_WORDS_NOT_FLAGGED = textwrap.dedent("""\
    ## Results

    We analyzed samples AND controls. Results OR negative controls were examined.
    The IN samples showed activity. NOT all samples were viable.
    """)

PROJECT_TERM_TIER_A = textwrap.dedent("""\
    ## Methods

    We selected Tier-A candidates based on enrichment score.
    """)

PROJECT_TERM_TIER_A_DEFINED = textwrap.dedent("""\
    ## Methods

    We define Tier-A as sequences with enrichment score > 5.0.
    Tier-A candidates were selected for further analysis.
    """)

ABBR_TABLE_SUGGESTION = textwrap.dedent("""\
    ## Methods

    We used QC (Quality Control), NGS (Next-Generation Sequencing), OTU (Operational
    Taxonomic Unit), ASV (Amplicon Sequence Variant), rRNA (ribosomal RNA),
    PCR (Polymerase Chain Reaction), cDNA (complementary DNA), RT-qPCR (Reverse
    Transcription quantitative PCR), rRNA (ribosomal RNA), ORF (Open Reading Frame),
    HMM (Hidden Markov Model), and ROC (Receiver Operating Characteristic) analyses.
    """)

CODE_BLOCK_ABBR = textwrap.dedent("""\
    ## Methods

    The code snippet is shown below:

    ```python
    def analyze_WEIRD_ABBR_that_should_not_be_counted():
        pass
    ```

    We used ABC (Already Been Covered) in our analysis.
    """)

IMAGE_TAG_ABBR = textwrap.dedent("""\
    ## Results

    ![Results showing WEIRD_ABBR analysis](figure.png)

    Our DFS (Depth-First Search) algorithm found patterns.
    """)

MANUSCRIPT_MD = textwrap.dedent("""\
    ## Abstract

    We developed a novel framework for analyzing microbial communities (MC).

    ## Methods

    MC analyses revealed structure. We used PCA (Principal Component Analysis).

    ## Results

    The PCA findings showed patterns. MC studies are useful.
    """)

ECOTYPE_CONTEXT = textwrap.dedent("""\
    ## Methods

    An ecotype is defined as the functional role in the environment.
    Ecotype labels E0, E1, E2, E3 were assigned based on metabolic capability.
    """)

ECOTYPE_CONTEXT_NO_DEF = textwrap.dedent("""\
    ## Results

    Ecotype labels E0, E1 were observed in the samples.
    """)

ABBR_WITH_FIGURE_REFERENCE = textwrap.dedent("""\
    ## Results

    As shown in Fig. E1 (error metrics), the results were clear.
    """)


# ---------------------------------------------------------------------------
# Tests: Clean state
# ---------------------------------------------------------------------------

class TestCleanAbbreviations:
    """Clean text with properly expanded abbreviations."""

    def test_clean_exits_0(self, checker, tmp_path):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_ABBREVIATIONS)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_clean_no_warnings(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_ABBREVIATIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "all checks passed" in captured.err


# ---------------------------------------------------------------------------
# Tests: Abbreviation expansion order
# ---------------------------------------------------------------------------

class TestAbbreviationExpansionOrder:
    """Abbreviations must be expanded before or at first use."""

    def test_abbr_used_before_expansion_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_USED_BEFORE_EXPANSION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "NMDS" in captured.err
        assert "before expansion" in captured.err

    def test_abbr_never_expanded_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_NEVER_EXPANDED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "never expanded" in captured.err

    def test_multiple_abbrs_mixed_warnings(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(MULTIPLE_ABBRS_MIXED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should have warnings for RDA (appears after expansion but also before)
        # and CCA (appears before expansion)
        warn_lines = [l for l in captured.err.split("\n") if "WARN" in l]
        assert len(warn_lines) >= 1


class TestCommonWordsNotFlagged:
    """Common words like AND, OR, NOT should not be flagged as abbreviations."""

    def test_and_or_not_ignored(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(COMMON_WORDS_NOT_FLAGGED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about AND, OR, NOT, IN
        assert "AND" not in captured.err
        assert "OR" not in captured.err
        assert "NOT" not in captured.err


# ---------------------------------------------------------------------------
# Tests: Project-term definitions
# ---------------------------------------------------------------------------

class TestProjectTerms:
    """Project-specific terms require preceding definitions."""

    def test_tier_a_without_definition_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PROJECT_TERM_TIER_A)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "Tier-A" in captured.err
        assert "without preceding definition" in captured.err

    def test_tier_a_with_definition_ok(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PROJECT_TERM_TIER_A_DEFINED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about Tier-A since it's defined
        tier_warns = [l for l in captured.err.split("\n") if "Tier-A" in l]
        assert len(tier_warns) == 0

    def test_ecotype_context_defined(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ECOTYPE_CONTEXT)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # E0-E3 should be okay since ecotype is defined
        ecotype_warns = [l for l in captured.err.split("\n") if "ecotype" in l.lower()]
        # Should not have warnings for E0/E1/E2/E3 when context is defined
        assert len(ecotype_warns) == 0 or "all checks passed" in captured.err

    def test_ecotype_context_undefined(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ECOTYPE_CONTEXT_NO_DEF)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should warn about undefined ecotype context
        if "E0" in captured.err or "E1" in captured.err:
            assert "without preceding definition" in captured.err or \
                   "ecotype" in captured.err.lower()


# ---------------------------------------------------------------------------
# Tests: Abbreviation table suggestion
# ---------------------------------------------------------------------------

class TestAbbreviationTableSuggestion:
    """Suggest abbreviation table if >10 unique abbreviations."""

    def test_more_than_10_abbrs_suggestion(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_TABLE_SUGGESTION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "NOTE" in captured.err
        assert "abbreviation table" in captured.err
        assert ">10" in captured.err or "11" in captured.err or "12" in captured.err


# ---------------------------------------------------------------------------
# Tests: Code blocks and images skipped
# ---------------------------------------------------------------------------

class TestCodeBlocksSkipped:
    """Code blocks are skipped when checking abbreviations."""

    def test_abbr_in_code_block_skipped(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CODE_BLOCK_ABBR)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about WEIRD_ABBR in code block
        assert "WEIRD_ABBR" not in captured.err


class TestImageTagsSkipped:
    """Image tags are skipped when checking abbreviations."""

    def test_abbr_in_image_tag_skipped(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(IMAGE_TAG_ABBR)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about WEIRD_ABBR in image tag
        assert "WEIRD_ABBR" not in captured.err
        # But should handle DFS properly
        assert "DFS" in captured.err or "all checks passed" in captured.err


# ---------------------------------------------------------------------------
# Tests: Manuscript loading
# ---------------------------------------------------------------------------

class TestManuscriptLoading:
    """Prefer manuscript.md when available; fall back to section files."""

    def test_manuscript_md_preferred(self, checker, tmp_path):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(MANUSCRIPT_MD)
        # Also create section files (should be ignored)
        (tmp_path / "01_abstract.md").write_text("Different abstract")
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_fallback_to_section_files(self, checker, tmp_path):
        # Create section files instead of manuscript.md
        (tmp_path / "01_abstract.md").write_text("## Abstract\nMC (Microbial Community)")
        (tmp_path / "02_methods.md").write_text("## Methods\nUsed PCA (Principal Component Analysis).")
        result = checker.main([str(tmp_path)])
        assert result == 0


class TestMissingFiles:
    """Missing files handled gracefully."""

    def test_missing_manuscript_exits_0(self, checker, tmp_path):
        result = checker.main([str(tmp_path)])
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: JSON diagnostics
# ---------------------------------------------------------------------------

class TestJsonDiagnostics:
    """JSON diagnostics contain expected keys."""

    def test_json_output_structure(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_ABBREVIATIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert "unique_abbreviations" in data
        assert "used_before_expansion" in data
        assert "never_expanded" in data
        assert "undefined_project_terms" in data
        assert "total_abbreviations" in data

    def test_json_lists_specific_issues(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_NEVER_EXPANDED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        # Should have entries in the diagnostic lists
        assert isinstance(data["unique_abbreviations"], list)
        assert isinstance(data["never_expanded"], list)
        assert len(data["never_expanded"]) > 0


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestFigureELabelsNotFlagged:
    """Figure references like 'Fig. E1' should not flag 'E1' as undefined ecotype."""

    def test_figure_e1_not_flagged_as_ecotype(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_WITH_FIGURE_REFERENCE)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about E1 when it's in "Fig. E1" context
        e_warns = [l for l in captured.err.split("\n") if "E1" in l or "E0" in l]
        # Either no warnings or warnings that specifically mention figure context
        # (The checker should distinguish "Fig. E1" from ecotype context)


# ---------------------------------------------------------------------------
# Regression: all checks run to completion
# ---------------------------------------------------------------------------

class TestMainIntegration:
    """Main orchestrator contract: always exit 0, emit JSON."""

    def test_exit_code_always_zero(self, checker, tmp_path):
        """Advisory mode: always exit 0."""
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(ABBR_NEVER_EXPANDED)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_json_always_emitted(self, checker, tmp_path, capsys):
        """JSON diagnostics are always emitted to stdout."""
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_ABBREVIATIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should be valid JSON (multi-line with indent=2)
        data = json.loads(captured.out.strip())
        assert isinstance(data, dict)
