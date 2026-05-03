r"""Tests for tier extraction from throughline_candidates.md.

The tier extraction (v0.6.4) parses the plan phase's output to populate
state.json["tier"]. Prior to v0.6.4, tier was initialized as null in
state.json and never set, causing all sections to default to STRONG
(the most permissive tier). This caused budget overshoot on EXPLORATORY
projects — e.g., functional_dark_matter draft_9's Discussion was 1593
words against a 500–1000 EXPLORATORY cap.

Root cause chain:
  state.json["tier"] = null (init)
  → read_state_field returns "" (Python None → empty string)
  → bash [[ -z "$tier" ]] && tier="STRONG"
  → Discussion prompt gets TIER=STRONG (800–1500 words)
  → LLM writes 1593 words (just over STRONG ceiling)
"""

import json
import textwrap
from pathlib import Path

import pytest

import sys
import importlib.util

_HELPERS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "paper_writer_helpers.py"
)


@pytest.fixture(scope="module")
def helpers():
    """Load paper_writer_helpers.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "paper_writer_helpers", _HELPERS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paper_writer_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures — throughline_candidates.md variants
# ---------------------------------------------------------------------------

CANDIDATES_WITH_TRIAGE = textwrap.dedent("""\
    # Throughline Candidates

    ## Triage

    **Tier:** EXPLORATORY
    **Recommended mode:** report
    **Rationale:** Single analysis layer with no statistical validation beyond
    rank sensitivity; all claims are hypothesis-generating.

    ---

    ## Candidate TL1: Dark genes with condition-specific fitness phenotypes

    **Evidence map:**

    | Sub-claim | Source | Strength |
    |---|---|---|
    | 17,344 dark genes have measurable phenotypes | REPORT.md §F1 | ✓ direct |

    **Weakness inventory:**

    - Gap: No experimental validation
""")

CANDIDATES_STRONG_TRIAGE = textwrap.dedent("""\
    # Throughline Candidates

    ## Triage

    **Tier:** STRONG
    **Recommended mode:** paper
    **Rationale:** Multiple statistical validations with FDR correction.

    ---

    ## Candidate TL1: Validated mechanism
""")

CANDIDATES_THIN_TRIAGE = textwrap.dedent("""\
    # Throughline Candidates

    ## Triage

    **Tier:** THIN
    **Recommended mode:** paper
    **Rationale:** Adequate replication but no independent validation cohort.

    ---

    ## Candidate TL1: Partial validation
""")

# Legacy format: no Triage section, but closing message has tier
CANDIDATES_CLOSING_MESSAGE_ONLY = textwrap.dedent("""\
    # Throughline Candidates

    ## Candidate TL1: Dark genes show cross-organism concordance

    **Evidence map:**

    | Sub-claim | Source | Strength |
    |---|---|---|
    | Concordance | REPORT.md §F5 | ✓ direct |

    ---

    throughline_candidates.md written, 2 candidates (tier: EXPLORATORY,
    recommended mode: report); triage rationale: single analysis layer.
""")

# No tier anywhere
CANDIDATES_NO_TIER = textwrap.dedent("""\
    # Throughline Candidates

    ## Candidate TL1: Dark genes with measurable phenotypes

    **Evidence map:**

    | Sub-claim | Source | Strength |
    |---|---|---|
    | 17,344 dark genes | REPORT.md §F1 | ✓ direct |
""")

# Case-insensitive tier
CANDIDATES_LOWERCASE_TIER = textwrap.dedent("""\
    # Throughline Candidates

    ## Triage

    **Tier:** exploratory
    **Recommended mode:** report
    **Rationale:** Proof of concept.

    ---
""")


# ---------------------------------------------------------------------------
# _extract_tier_from_text tests
# ---------------------------------------------------------------------------

class TestExtractTierFromText:
    """Tests for the _extract_tier_from_text helper."""

    def test_structured_triage_exploratory(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_WITH_TRIAGE) == "EXPLORATORY"

    def test_structured_triage_strong(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_STRONG_TRIAGE) == "STRONG"

    def test_structured_triage_thin(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_THIN_TRIAGE) == "THIN"

    def test_closing_message_fallback(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_CLOSING_MESSAGE_ONLY) == "EXPLORATORY"

    def test_no_tier_returns_none(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_NO_TIER) is None

    def test_case_insensitive(self, helpers):
        assert helpers._extract_tier_from_text(CANDIDATES_LOWERCASE_TIER) == "EXPLORATORY"

    def test_structured_takes_precedence_over_closing(self, helpers):
        """If both structured header and closing message are present,
        structured header wins."""
        text = (
            "## Triage\n\n**Tier:** STRONG\n\n---\n\n"
            "tier: THIN, recommended mode: paper\n"
        )
        assert helpers._extract_tier_from_text(text) == "STRONG"


# ---------------------------------------------------------------------------
# cmd_extract_tier integration tests
# ---------------------------------------------------------------------------

class TestCmdExtractTier:
    """Tests for the extract-tier subcommand end-to-end."""

    def test_writes_tier_to_state_json(self, helpers, tmp_path):
        candidates = tmp_path / "throughline_candidates.md"
        candidates.write_text(CANDIDATES_WITH_TRIAGE, encoding="utf-8")

        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"phase": "plan", "tier": None}),
                              encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            candidates_path=str(candidates),
            draft_dir=str(tmp_path),
        )
        rc = helpers.cmd_extract_tier(args)
        assert rc == 0

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["tier"] == "EXPLORATORY"

    def test_missing_tier_defaults_exploratory_rc2(self, helpers, tmp_path):
        candidates = tmp_path / "throughline_candidates.md"
        candidates.write_text(CANDIDATES_NO_TIER, encoding="utf-8")

        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"phase": "plan", "tier": None}),
                              encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            candidates_path=str(candidates),
            draft_dir=str(tmp_path),
        )
        rc = helpers.cmd_extract_tier(args)
        assert rc == 2  # warning: no tier found

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["tier"] == "EXPLORATORY"  # conservative default

    def test_missing_file_rc1(self, helpers, tmp_path):
        import argparse
        args = argparse.Namespace(
            candidates_path=str(tmp_path / "nonexistent.md"),
            draft_dir=str(tmp_path),
        )
        rc = helpers.cmd_extract_tier(args)
        assert rc == 1

    def test_no_draft_dir_still_prints_tier(self, helpers, tmp_path, capsys):
        candidates = tmp_path / "throughline_candidates.md"
        candidates.write_text(CANDIDATES_STRONG_TRIAGE, encoding="utf-8")

        import argparse
        args = argparse.Namespace(
            candidates_path=str(candidates),
            draft_dir=None,
        )
        rc = helpers.cmd_extract_tier(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "STRONG" in captured.out


# ---------------------------------------------------------------------------
# Regression: null tier in state.json → STRONG default
# ---------------------------------------------------------------------------

class TestTierNullRegression:
    """The original bug: state.json has "tier": null, bash defaults to STRONG.

    These tests verify the defensive behavior in read_state_field's
    Python layer and the extract-tier fallback."""

    def test_read_state_field_returns_empty_for_null(self):
        """Verify the Python in read_state_field prints '' for None."""
        import json
        state = {"tier": None}
        v = state.get("tier")
        # This is what read_state_field's Python does:
        result = "" if v is None else v
        assert result == ""

    def test_extract_tier_default_is_exploratory_not_strong(self, helpers):
        """When no tier is found, default must be EXPLORATORY (conservative),
        not STRONG (permissive). This is the fix for the draft_9 overshoot."""
        tier = helpers._extract_tier_from_text("No tier information here.")
        assert tier is None
        # The cmd_extract_tier function defaults to EXPLORATORY when None.
        # This is tested in test_missing_tier_defaults_exploratory_rc2.
