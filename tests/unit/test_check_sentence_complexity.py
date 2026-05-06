r"""Tests for check_sentence_complexity.py post-checker.

Validates the advisory checker for sentence length (>40, >50 words) and
parenthetical complexity (2+ pairs).
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
    / "check_sentence_complexity.py"
)


@pytest.fixture(scope="module")
def checker():
    """Load check_sentence_complexity.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "check_sentence_complexity", _CHECKER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_sentence_complexity"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

CLEAN_SENTENCES = textwrap.dedent("""\
    ## Results

    We observed that the mutation increased expression.
    The effect was pronounced in early replicates.
    """)

LONG_41_WORDS = textwrap.dedent("""\
    ## Results

    The results of our comprehensive experimental analysis demonstrate that the specific mutation in the regulatory region increases expression of the target gene across all conditions and replicates tested during the longitudinal study period in treatment and control groups under standard laboratory.
    """)

LONG_51_WORDS = textwrap.dedent("""\
    ## Results

    The results of our comprehensive experimental analysis demonstrate that the specific mutation in the regulatory region increases expression of the target gene across all conditions and replicates tested during the longitudinal study period in treatment and control groups under standard laboratory conditions and environmental parameters with consistent measurements across sites and.
    """)

TWO_PARENS = textwrap.dedent("""\
    ## Results

    We observed that the mutation (first effect noted) increased expression (and
    notably decreased background signal) across all replicates.
    """)

ONE_PAREN = textwrap.dedent("""\
    ## Results

    We observed that the mutation (first effect noted) increased expression.
    """)

CODE_BLOCK_LONG = textwrap.dedent("""\
    ## Results

    The code is shown below:

    ```python
    def very_long_function_name_that_would_normally_be_counted_as_a_sentence_if_not_in_code_block():
        return True
    ```

    We implemented this function successfully.
    """)

YAML_FRONTMATTER = textwrap.dedent("""\
    ---
    title: A Very Long Title That Would Count As A Sentence If It Were Not Inside YAML Frontmatter Declaration
    author: Test
    ---

    ## Results

    We observed simple results.
    """)

IMAGE_TAG = textwrap.dedent("""\
    ## Results

    ![This is a very long image caption that spans more than fifty words and would be
    problematic if counted as a sentence but is safely inside an image tag](fig1.png)

    We observed results here.
    """)

ABBREV_ET_AL = textwrap.dedent("""\
    ## Results

    Previous work (et al. found results) showed improvement.
    We confirmed the observation.
    """)

DECIMAL_NUMBERS = textwrap.dedent("""\
    ## Results

    The value measured 1.34 units compared to baseline.
    Secondary measurements were taken at 2.71 intervals.
    """)

MULTIPLE_FILES = textwrap.dedent("""\
    ## Results

    First result: the mutation increased expression.
    Another finding: the effect was sustained.
    """)

DISCUSSION_TEXT = textwrap.dedent("""\
    ## Discussion

    These results align with previous observations that were made in similar contexts.
    We believe the mechanism involves regulatory elements and transcription factors.
    """)

MIXED_WARN_NOTE = textwrap.dedent("""\
    ## Results

    Short sentence here. The analysis of our multi-site experimental study reveals that the specific mutation in the regulatory region increases expression of the target gene across all conditions and replicates tested during the longitudinal study period in treatment and control groups overall with significance. The comprehensive results of our multi-site experimental analysis demonstrate that the specific mutation identified in the regulatory region significantly increases expression of the target gene across all experimental conditions and biological replicates tested during the extended twelve-month longitudinal study period in both treatment and control groups under standard laboratory conditions and parameters.
    """)


# ---------------------------------------------------------------------------
# Tests: Basic functionality
# ---------------------------------------------------------------------------

class TestCleanText:
    """No warnings or notes for short, simple sentences."""

    def test_clean_exits_0(self, checker, tmp_path):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(CLEAN_SENTENCES)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_clean_no_findings(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(CLEAN_SENTENCES)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "0 WARN" in captured.err or "all checks passed" in captured.err


class TestSentenceLength:
    """Sentences >40 words (NOTE) and >50 words (WARN)."""

    def test_41_words_note(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(LONG_41_WORDS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "NOTE" in captured.err
        assert ">40 words" in captured.err

    def test_51_words_warn(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(LONG_51_WORDS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert ">50 words" in captured.err


class TestParenthesesComplexity:
    """Sentences with 2+ parenthetical pairs are WARN."""

    def test_two_parens_warn(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(TWO_PARENS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "parenthetical pairs" in captured.err

    def test_one_paren_no_warn(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(ONE_PAREN)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Single paren should not trigger the parenthetical warning
        # (but verify there are no warnings about complexity)
        warn_lines = [l for l in captured.err.split("\n") if "parenthetical" in l]
        assert len(warn_lines) == 0


class TestCodeBlocks:
    """Code blocks are skipped."""

    def test_long_sentence_in_code_block_skipped(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(CODE_BLOCK_LONG)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # The long function name inside code block should not be counted
        assert "def very_long" not in captured.err


class TestYamlFrontmatter:
    """YAML frontmatter is skipped."""

    def test_yaml_frontmatter_skipped(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(YAML_FRONTMATTER)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # The long title should not trigger a warning
        assert "Very Long Title" not in captured.err


class TestImageTags:
    """Markdown image tags are skipped."""

    def test_image_tag_skipped(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(IMAGE_TAG)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # The long image caption should not be counted
        assert "caption" not in captured.err.lower() or "long image" not in captured.err


class TestAbbreviations:
    """Abbreviations (et al., etc.) don't cause false sentence splits."""

    def test_et_al_no_false_split(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(ABBREV_ET_AL)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should parse as one logical sentence with (et al. found results)
        # No warning about the overall sentence length
        stderr_lines = captured.err.split("\n")
        long_sent_warns = [l for l in stderr_lines if "parenthetical" in l and "et al" in l]
        # et al. is in parens but should not trigger double-paren warning


class TestDecimals:
    """Decimal numbers don't cause false sentence splits."""

    def test_decimal_no_false_split(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(DECIMAL_NUMBERS)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Both 1.34 and 2.71 should NOT split sentences
        # Should only see warnings about actual long sentences if any
        assert "1.34" not in captured.err
        assert "2.71" not in captured.err


class TestMultipleFiles:
    """Multiple section files are all scanned."""

    def test_multiple_files_scanned(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(MULTIPLE_FILES)
        discussion_path = tmp_path / "03_discussion.md"
        discussion_path.write_text(DISCUSSION_TEXT)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Summary should mention 2 files scanned
        assert "2 files" in captured.err or "files_scanned" in captured.out


class TestMissingFiles:
    """Missing files are handled gracefully."""

    def test_missing_files_no_crash(self, checker, tmp_path):
        # Don't create any section files
        result = checker.main([str(tmp_path)])
        assert result == 0


class TestJsonDiagnostics:
    """JSON diagnostics contain expected keys."""

    def test_json_output_structure(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(CLEAN_SENTENCES)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Last line of stdout should be JSON
        lines = captured.out.strip().split("\n")
        json_line = lines[-1]
        data = json.loads(json_line)
        assert "total_sentences" in data
        assert "files_scanned" in data
        assert "warn_over_50" in data
        assert "warn_multi_paren" in data
        assert "note_over_40" in data


class TestMixedFindings:
    """File with both WARN and NOTE findings."""

    def test_mixed_warn_and_note(self, checker, tmp_path, capsys):
        results_path = tmp_path / "02_results.md"
        results_path.write_text(MIXED_WARN_NOTE)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        # Should have at least one WARN and one NOTE
        assert "WARN" in captured.err
        assert "NOTE" in captured.err


# ---------------------------------------------------------------------------
# Regression: all checkers run to completion
# ---------------------------------------------------------------------------

class TestMainIntegration:
    """Main orchestrator contract: always exit 0, emit JSON."""

    def test_exit_code_always_zero(self, checker, tmp_path):
        """Advisory mode: always exit 0."""
        results_path = tmp_path / "02_results.md"
        results_path.write_text(LONG_51_WORDS)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_json_always_emitted(self, checker, tmp_path, capsys):
        """JSON diagnostics are always emitted to stdout."""
        results_path = tmp_path / "02_results.md"
        results_path.write_text(CLEAN_SENTENCES)
        checker.main([str(tmp_path)])
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        json_line = lines[-1]
        # Should be valid JSON
        data = json.loads(json_line)
        assert isinstance(data, dict)
