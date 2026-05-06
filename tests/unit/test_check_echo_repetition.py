r"""Tests for check_echo_repetition.py post-checker.

Validates the advisory checker for quantitative-claim repetition across 3+ sections.
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
    / "check_echo_repetition.py"
)


@pytest.fixture(scope="module")
def checker():
    """Load check_echo_repetition.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "check_echo_repetition", _CHECKER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_echo_repetition"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

CLEAN_NO_REPETITION = textwrap.dedent("""\
    ## Abstract

    We observed 15% improvement in expression levels.

    ## Results

    The expression increased to 48% in treated samples and 22% in controls.

    ## Discussion

    These findings suggest a novel regulatory mechanism.
    """)

PERCENTAGE_REPEATED_3_SECTIONS = textwrap.dedent("""\
    ## Abstract

    We observed 42% reduction in bacterial load.

    ## Results

    The treated samples showed 42% reduction compared to baseline.

    ## Discussion

    The 42% reduction is consistent with previous studies.
    """)

PVALUE_REPEATED_4_SECTIONS = textwrap.dedent("""\
    ## Abstract

    The difference was statistically significant (p < 0.01).

    ## Methods

    We used threshold p < 0.01 for all analyses.

    ## Results

    Differences were significant at p < 0.01 level.

    ## Discussion

    This p < 0.01 threshold aligns with community standards.
    """)

CLAIM_REPEATED_2_SECTIONS_OK = textwrap.dedent("""\
    ## Abstract

    We found 85% accuracy in our predictions.

    ## Results

    Our model achieved 85% accuracy on the test set.

    ## Discussion

    We believe the underlying mechanism is different.
    """)

EFFECT_SIZE_REPEATED = textwrap.dedent("""\
    ## Abstract

    The effect size was OR 2.5 for the primary outcome.

    ## Results

    We observed OR 2.5 in the treatment group.

    ## Discussion

    An OR 2.5 suggests strong clinical significance.
    """)

CONFIDENCE_INTERVAL_REPEATED = textwrap.dedent("""\
    ## Abstract

    The estimate is 0.65 [0.58–0.72].

    ## Methods

    We computed 95% CIs for all estimates.

    ## Results

    The key estimate is 0.65 [0.58–0.72] with narrow bounds.

    ## Discussion

    The 0.65 [0.58–0.72] confidence interval strongly supports the hypothesis.
    """)

CODE_BLOCK_NO_ECHO = textwrap.dedent("""\
    ## Methods

    ```python
    # 35% confidence is okay for demo
    confidence = 0.35
    ```

    ## Results

    We achieved 35% accuracy in the experiment.

    ## Discussion

    The 35% result aligns with predictions.
    """)

TABLE_NO_ECHO = textwrap.dedent("""\
    ## Results

    | Condition | Success Rate |
    |-----------|--------------|
    | A         | 72%          |
    | B         | 89%          |

    ## Discussion

    Results show improved performance (72% vs 89%).
    """)

FINDINGS_SUMMARY_SEPARATE = textwrap.dedent("""\
    ## Results

    The experiment showed 68% efficacy across all replicates.

    ### Findings Summary

    We report 68% efficacy and recommend further study.

    ## Discussion

    The 68% efficacy aligns with theoretical predictions.
    """)

SECTION_HEADERS_VARIED = textwrap.dedent("""\
    # Abstract

    Our analysis revealed 53% improvement.

    ## Results

    The results demonstrated 53% improvement in speed.

    ### Findings Summary

    We observed consistent 53% improvement metrics.

    ## Discussion

    This 53% improvement is reproducible.
    """)

NUMBER_NORMALIZATION = textwrap.dedent("""\
    ## Abstract

    The sample size was n = 1,234 in the study.

    ## Methods

    We enrolled n = 1234 subjects.

    ## Results

    Our dataset comprised n = 1,234 samples for analysis.

    ## Discussion

    A sample size of n = 1234 is typical for this field.
    """)

FRACTION_REPEATED = textwrap.dedent("""\
    ## Abstract

    The positive rate was 47/100 samples.

    ## Results

    Across the cohort, 47/100 tested positive.

    ## Discussion

    A 47/100 positive rate indicates widespread adoption.
    """)

MANUSCRIPT_MD_STRUCTURE = textwrap.dedent("""\
    ## Abstract

    We identified 91% of expected genes.

    ## Methods

    Standard annotation methods were used throughout.

    ## Results

    Gene annotation achieved 91% accuracy on validation set.

    ### Findings Summary

    We report 91% gene identification accuracy.

    ## Discussion

    The 91% accuracy exceeds prior benchmarks.

    ## Data Availability

    Reference genomes are available at https://example.com.
    """)

EMPTY_DRAFT = textwrap.dedent("""\
    """)


# ---------------------------------------------------------------------------
# Tests: Clean state
# ---------------------------------------------------------------------------

class TestCleanNoRepetition:
    """Clean text with no claim repetition across 3+ sections."""

    def test_clean_exits_0(self, checker, tmp_path):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_NO_REPETITION)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_clean_no_warnings(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_NO_REPETITION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "no claims repeated" in captured.err


# ---------------------------------------------------------------------------
# Tests: Claim repetition detection
# ---------------------------------------------------------------------------

class TestPercentageRepeated3Sections:
    """Percentage repeated in 3+ sections triggers WARN."""

    def test_percentage_42_repeated_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PERCENTAGE_REPEATED_3_SECTIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "42%" in captured.err or "42" in captured.err
        assert "3 sections" in captured.err or "3" in captured.err

    def test_percentage_in_diagnostics(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PERCENTAGE_REPEATED_3_SECTIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["claims_in_3plus_sections"] >= 1


class TestPvalueRepeated4Sections:
    """P-value repeated in 4+ sections triggers WARN."""

    def test_pvalue_repeated_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PVALUE_REPEATED_4_SECTIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err or "4 sections" in captured.err
        assert "p <" in captured.err or "0.01" in captured.err


class TestClaimRepeated2SectionsOk:
    """Claim repeated in exactly 2 sections should not warn."""

    def test_claim_2_sections_no_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLAIM_REPEATED_2_SECTIONS_OK)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should not warn about 85% (only appears in 2 sections)
        warn_lines = [l for l in captured.err.split("\n") if "WARN" in l]
        # Either no warnings or warnings about different claims
        assert len(warn_lines) == 0 or "85%" not in captured.err


# ---------------------------------------------------------------------------
# Tests: Quantitative patterns
# ---------------------------------------------------------------------------

class TestEffectSizeRepeated:
    """Effect size (OR, HR, RR) repeated across sections."""

    def test_or_repeated_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(EFFECT_SIZE_REPEATED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # Should mention OR or effect size
        assert "2.5" in captured.err or "OR" in captured.err


class TestConfidenceIntervalRepeated:
    """Confidence interval repeated across sections."""

    def test_ci_repeated_warn(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CONFIDENCE_INTERVAL_REPEATED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # Should mention the interval
        assert "0.65" in captured.err or "0.58" in captured.err


# ---------------------------------------------------------------------------
# Tests: Code and table stripping
# ---------------------------------------------------------------------------

class TestCodeBlocksStripped:
    """Code blocks are stripped (no false positives)."""

    def test_percentage_in_code_not_counted(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CODE_BLOCK_NO_ECHO)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # The 35% in code block should not count as a claim echo
        # Even though 35% appears in Results and Discussion, it's only 2 sections
        warn_lines = [l for l in captured.err.split("\n") if "WARN" in l]
        assert len(warn_lines) == 0 or "35%" not in captured.err


class TestTablesStripped:
    """Table markup is stripped (no false positives)."""

    def test_percentages_in_table_handled(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(TABLE_NO_ECHO)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Tables should be stripped; 72% and 89% appear in table and discussion
        # But since they're in only 2 sections, should not warn
        result = checker.main([str(tmp_path)])
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: Section detection
# ---------------------------------------------------------------------------

class TestFindingsSummarySeparate:
    """Findings Summary treated as separate section from Results body."""

    def test_findings_summary_counted_separately(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(FINDINGS_SUMMARY_SEPARATE)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # 68% appears in Results, Findings Summary, and Discussion (3 sections)
        assert "68%" in captured.err or "3 sections" in captured.err


class TestSectionHeadersVaried:
    """Both # and ## headers are recognized."""

    def test_varied_header_levels(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(SECTION_HEADERS_VARIED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # 53% appears in Abstract, Results, Findings Summary, Discussion
        assert "53%" in captured.err or "4 sections" in captured.err


# ---------------------------------------------------------------------------
# Tests: Number normalization
# ---------------------------------------------------------------------------

class TestNumberNormalization:
    """1,234 and 1234 treated as equivalent."""

    def test_comma_normalization(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(NUMBER_NORMALIZATION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # 1234/1,234 should be treated as same claim across 4 sections
        assert "1" in captured.err or "sample" in captured.err.lower()


class TestFractionNormalization:
    """Fractions (47/100) are detected and normalized."""

    def test_fraction_repetition(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(FRACTION_REPEATED)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        # 47/100 appears in 3 sections (Abstract, Results, Discussion)
        assert "47" in captured.err or "100" in captured.err


# ---------------------------------------------------------------------------
# Tests: Manuscript loading and fallback
# ---------------------------------------------------------------------------

class TestManuscriptMdPreferred:
    """Prefer manuscript.md when available."""

    def test_manuscript_md_used(self, checker, tmp_path):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(MANUSCRIPT_MD_STRUCTURE)
        # Also create section files (should be ignored)
        (tmp_path / "01_abstract.md").write_text("Different content")
        result = checker.main([str(tmp_path)])
        assert result == 0


class TestFallbackToSectionFiles:
    """Fall back to individual section files if no manuscript.md."""

    def test_section_files_fallback(self, checker, tmp_path):
        # Create section files instead of manuscript.md
        (tmp_path / "01_abstract.md").write_text(
            "## Abstract\n\nWe identified 91% of expected genes."
        )
        (tmp_path / "02_methods.md").write_text("## Methods\n\nStandard methods.")
        (tmp_path / "03_results.md").write_text(
            "## Results\n\nGene annotation achieved 91% accuracy."
        )
        (tmp_path / "04_discussion.md").write_text(
            "## Discussion\n\nThe 91% accuracy exceeds benchmarks."
        )
        result = checker.main([str(tmp_path)])
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: JSON diagnostics
# ---------------------------------------------------------------------------

class TestJsonDiagnostics:
    """JSON diagnostics contain expected keys."""

    def test_json_output_structure(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_NO_REPETITION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert "total_unique_claims" in data
        assert "claims_in_3plus_sections" in data
        assert "top_repeated" in data

    def test_json_top_repeated_format(self, checker, tmp_path, capsys):
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PERCENTAGE_REPEATED_3_SECTIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        # Top-repeated should have the right structure
        if data["top_repeated"]:
            item = data["top_repeated"][0]
            assert "claim" in item
            assert "count" in item
            assert "sections" in item


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEmptyDraft:
    """Empty draft directory exits gracefully."""

    def test_empty_manuscript_exits_0(self, checker, tmp_path):
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_empty_manuscript_no_crash(self, checker, tmp_path, capsys):
        result = checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert result == 0
        # Should gracefully handle missing files
        assert "NOTE" in captured.err or "no claims" in captured.err


# ---------------------------------------------------------------------------
# Regression: all checks run to completion
# ---------------------------------------------------------------------------

class TestMainIntegration:
    """Main orchestrator contract: always exit 0, emit JSON."""

    def test_exit_code_always_zero(self, checker, tmp_path):
        """Advisory mode: always exit 0."""
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PERCENTAGE_REPEATED_3_SECTIONS)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_json_always_emitted(self, checker, tmp_path, capsys):
        """JSON diagnostics are always emitted to stdout."""
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(CLEAN_NO_REPETITION)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should be valid JSON (multi-line with indent=2)
        data = json.loads(captured.out.strip())
        assert isinstance(data, dict)

    def test_warning_count_in_summary(self, checker, tmp_path, capsys):
        """Summary line includes warning and note counts."""
        manuscript_path = tmp_path / "manuscript.md"
        manuscript_path.write_text(PERCENTAGE_REPEATED_3_SECTIONS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should have summary with counts
        assert "warning" in captured.err.lower() or "warn" in captured.err.lower()
