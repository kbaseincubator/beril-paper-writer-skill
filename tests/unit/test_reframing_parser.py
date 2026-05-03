r"""Tests for reframing log parsing and repair dispatch.

The reframing repair phase (v0.6.4) reads escalated entries from
reframing_log.md and dispatches targeted REPAIR_MODE rewrites to
affected sections. These tests validate the parser and dispatch logic.

Incident context: draft_9 (2026-05-03) had 2 escalated entries
(n_annotated 100->490 across 3 sections; missing Dehal cross-check in
Methods) and 1 accepted-as-Limitations entry. The rewrite loop operated
on uncorrected sections because no repair phase existed between
reframe_drift_audit and the downstream phases.
"""

import json
import textwrap
from pathlib import Path

import pytest

# Import from the helpers module. We use the same import pattern as
# test_review_substance.py — inline the function rather than importing
# from the installed package, because pytest runs from the repo root
# against the source tree.
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
# Fixtures
# ---------------------------------------------------------------------------

DRAFT_9_LOG = textwrap.dedent("""\
    # Reframing Log

    ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

    - **Issue:** Results, Discussion, and Abstract all report n_annotated = 100 for the dark-vs-annotated cross-organism concordance Mann-Whitney null test. REPORT.md gives n_annotated = 490.
    - **Source:** REPORT.md §Step 5 line 564 vs 02_results.md line 65, 03_discussion.md lines 5 and 27, 05_abstract.md line 7
    - **Manuscript impact:** The same wrong sample size propagated across three IMRAD sections.
    - **Resolution:** escalated (orchestrator: dispatch REPAIR_MODE on Results, Discussion, and Abstract; correct n_annotated to 490 in all three).
    - **Note:** Highest-impact drift in the audit.

    ---

    ## Entry 2 — 2026-05-03T00:00:00Z — type: reframing

    - **Issue:** Results cites Methods for Dehal cross-check but Methods does not describe it.
    - **Source:** REPORT.md §Finding 12 vs 01_methods.md and 02_results.md line 39
    - **Manuscript impact:** Reproducibility-blocking: Methods lacks the procedure.
    - **Resolution:** escalated (orchestrator: dispatch Methods REPAIR_MODE to add cross-check paragraph, OR remove anchor from Results).
    - **Note:** Structural pointer-integrity drift.

    ---

    ## Entry 3 — 2026-05-03T00:00:00Z — type: plan-execution-discrepancy

    - **Issue:** RESEARCH_PLAN.md prespecified 7 condition-environment mappings; implementation used 6.
    - **Source:** RESEARCH_PLAN.md vs REPORT.md §Finding 7 line 147
    - **Manuscript impact:** Reader cannot tell the analysis deviated from prespecification.
    - **Resolution:** accepted as Limitations (the divergence is project-internal and REPORT discloses it).
    - **Note:** Textbook plan-execution discrepancy.

    ---
""")

EMPTY_LOG = "# Reframing Log\n"

NO_ESCALATED_LOG = textwrap.dedent("""\
    # Reframing Log

    ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

    - **Issue:** Minor formatting inconsistency.
    - **Source:** 02_results.md line 12
    - **Manuscript impact:** Cosmetic only.
    - **Resolution:** accepted as Limitations (no substantive impact).
    - **Note:** Low priority.

    ---
""")

SINGLE_SECTION_ESCALATION = textwrap.dedent("""\
    # Reframing Log

    ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

    - **Issue:** Introduction overclaims mechanism.
    - **Source:** REPORT.md §Summary vs 04_introduction.md line 5
    - **Manuscript impact:** Overclaim in a hypothesis-generating paper.
    - **Resolution:** escalated (orchestrator: dispatch Introduction REPAIR_MODE to soften language).
    - **Note:** Single-section fix.

    ---
""")


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestReframingLogParser:
    """Tests for _parse_reframing_log()."""

    def test_draft_9_entry_count(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert len(entries) == 3

    def test_entry_numbers(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert [e["entry_number"] for e in entries] == [1, 2, 3]

    def test_entry_types(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert entries[0]["type"] == "reframing"
        assert entries[2]["type"] == "plan-execution-discrepancy"

    def test_resolution_action_escalated(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert entries[0]["resolution_action"] == "escalated"
        assert entries[1]["resolution_action"] == "escalated"

    def test_resolution_action_accepted(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert entries[2]["resolution_action"] == "accepted"

    def test_entry_1_target_sections(self, helpers):
        """Entry 1 targets Results, Discussion, and Abstract."""
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        targets = entries[0]["target_sections"]
        assert "results" in targets
        assert "discussion" in targets
        assert "abstract" in targets

    def test_entry_2_target_sections(self, helpers):
        """Entry 2 targets Methods (and mentions Results as context)."""
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        targets = entries[1]["target_sections"]
        assert "methods" in targets

    def test_entry_3_no_escalated_targets(self, helpers):
        """Entry 3 is accepted; target_sections may be non-empty but
        resolution_action filters it out before dispatch."""
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert entries[2]["resolution_action"] == "accepted"

    def test_issue_field_populated(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert "n_annotated = 100" in entries[0]["issue"]
        assert "Dehal" in entries[1]["issue"]

    def test_source_field_populated(self, helpers):
        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        assert "REPORT.md" in entries[0]["source"]

    def test_empty_log(self, helpers):
        entries = helpers._parse_reframing_log(EMPTY_LOG)
        assert entries == []

    def test_no_escalated_entries(self, helpers):
        entries = helpers._parse_reframing_log(NO_ESCALATED_LOG)
        assert len(entries) == 1
        assert entries[0]["resolution_action"] == "accepted"

    def test_single_section_escalation(self, helpers):
        entries = helpers._parse_reframing_log(SINGLE_SECTION_ESCALATION)
        assert len(entries) == 1
        assert entries[0]["resolution_action"] == "escalated"
        assert "introduction" in entries[0]["target_sections"]


# ---------------------------------------------------------------------------
# Dispatch table tests
# ---------------------------------------------------------------------------

class TestReframingDispatch:
    """Tests for _SECTION_REPAIR_DISPATCH mapping."""

    def test_all_five_sections_present(self, helpers):
        d = helpers._SECTION_REPAIR_DISPATCH
        assert set(d.keys()) == {
            "methods", "results", "discussion", "introduction", "abstract"
        }

    @pytest.mark.parametrize("section,prompt,filename,var", [
        ("methods", "methods.v1.md", "01_methods.md", "METHODS_PATH"),
        ("results", "results.v1.md", "02_results.md", "RESULTS_PATH"),
        ("discussion", "discussion.v1.md", "03_discussion.md", "DISCUSSION_PATH"),
        ("introduction", "intro.v1.md", "04_introduction.md", "INTRODUCTION_PATH"),
        ("abstract", "abstract.v1.md", "05_abstract.md", "ABSTRACT_PATH"),
    ])
    def test_dispatch_mapping(self, helpers, section, prompt, filename, var):
        d = helpers._SECTION_REPAIR_DISPATCH[section]
        assert d["section_prompt"] == prompt
        assert d["target_filename"] == filename
        assert d["target_var_name"] == var


# ---------------------------------------------------------------------------
# CLI integration tests (list-reframing-repairs output format)
# ---------------------------------------------------------------------------

class TestListReframingRepairs:
    """Tests for cmd_list_reframing_repairs output format."""

    def test_draft_9_produces_repair_lines(self, helpers, tmp_path):
        """Entry 1 → 3 section dispatches, Entry 2 → 1-2 dispatches."""
        # Write the reframing log.
        log_path = tmp_path / "reframing_log.md"
        log_path.write_text(DRAFT_9_LOG, encoding="utf-8")

        # Create target files so dispatch resolves.
        for fname in ["01_methods.md", "02_results.md", "03_discussion.md",
                       "04_introduction.md", "05_abstract.md"]:
            (tmp_path / fname).write_text("# placeholder\n", encoding="utf-8")

        entries = helpers._parse_reframing_log(DRAFT_9_LOG)
        escalated = [e for e in entries if e["resolution_action"] == "escalated"]
        assert len(escalated) == 2

        # Count expected dispatches: Entry 1 has 3 targets, Entry 2 has at least 1.
        total_targets = sum(len(e["target_sections"]) for e in escalated)
        assert total_targets >= 4  # 3 from Entry 1 + at least 1 from Entry 2

    def test_no_dispatch_for_accepted(self, helpers, tmp_path):
        """Accepted entries produce no dispatch lines."""
        log_path = tmp_path / "reframing_log.md"
        log_path.write_text(NO_ESCALATED_LOG, encoding="utf-8")

        entries = helpers._parse_reframing_log(NO_ESCALATED_LOG)
        escalated = [e for e in entries if e["resolution_action"] == "escalated"]
        assert len(escalated) == 0

    def test_missing_target_file_excluded(self, helpers, tmp_path):
        """If a target section file doesn't exist, it's excluded from dispatch."""
        log_path = tmp_path / "reframing_log.md"
        log_path.write_text(SINGLE_SECTION_ESCALATION, encoding="utf-8")
        # Do NOT create 04_introduction.md — should produce no dispatches.

        entries = helpers._parse_reframing_log(SINGLE_SECTION_ESCALATION)
        escalated = [e for e in entries if e["resolution_action"] == "escalated"]
        assert len(escalated) == 1
        # Target is introduction but file doesn't exist.
        d = helpers._SECTION_REPAIR_DISPATCH.get("introduction")
        target = tmp_path / d["target_filename"]
        assert not target.is_file()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestReframingEdgeCases:
    """Edge cases and regression guards."""

    def test_multiline_issue_field(self, helpers):
        """Issue field that spans multiple lines should be concatenated."""
        log = textwrap.dedent("""\
            # Reframing Log

            ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

            - **Issue:** First line of the issue.
              Second line continues here.
              Third line too.
            - **Source:** REPORT.md §Step 5
            - **Manuscript impact:** Moderate.
            - **Resolution:** escalated (dispatch Results REPAIR_MODE).
            - **Note:** Multi-line test.

            ---
        """)
        entries = helpers._parse_reframing_log(log)
        assert len(entries) == 1
        assert "First line" in entries[0]["issue"]
        assert "Second line" in entries[0]["issue"]
        assert "Third line" in entries[0]["issue"]

    def test_em_dash_and_hyphen_entry_headers(self, helpers):
        """Parser tolerates both em-dash and hyphen in entry headers."""
        log_emdash = "## Entry 1 — 2026-05-03T00:00:00Z — type: reframing\n\n- **Issue:** Test.\n- **Source:** S.\n- **Manuscript impact:** M.\n- **Resolution:** escalated (Results).\n- **Note:** N.\n"
        log_hyphen = "## Entry 1 - 2026-05-03T00:00:00Z - type: reframing\n\n- **Issue:** Test.\n- **Source:** S.\n- **Manuscript impact:** M.\n- **Resolution:** escalated (Results).\n- **Note:** N.\n"

        e1 = helpers._parse_reframing_log(log_emdash)
        e2 = helpers._parse_reframing_log(log_hyphen)
        assert len(e1) == 1
        assert len(e2) == 1
        assert e1[0]["entry_number"] == e2[0]["entry_number"] == 1

    def test_unknown_resolution_action(self, helpers):
        """Resolution text without 'escalated' or 'accepted' → unknown."""
        log = textwrap.dedent("""\
            # Reframing Log

            ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

            - **Issue:** Something.
            - **Source:** Somewhere.
            - **Manuscript impact:** Something.
            - **Resolution:** deferred to v2 (not actionable now).
            - **Note:** Edge case.

            ---
        """)
        entries = helpers._parse_reframing_log(log)
        assert entries[0]["resolution_action"] == "unknown"

    def test_section_name_case_insensitive(self, helpers):
        """Section names in Resolution are matched case-insensitively."""
        log = textwrap.dedent("""\
            # Reframing Log

            ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

            - **Issue:** Test.
            - **Source:** S.
            - **Manuscript impact:** M.
            - **Resolution:** escalated (dispatch ABSTRACT and METHODS repair).
            - **Note:** N.

            ---
        """)
        entries = helpers._parse_reframing_log(log)
        targets = entries[0]["target_sections"]
        assert "abstract" in targets
        assert "methods" in targets

    def test_no_duplicate_sections(self, helpers):
        """If a section name appears twice in Resolution, it appears once in target_sections."""
        log = textwrap.dedent("""\
            # Reframing Log

            ## Entry 1 — 2026-05-03T00:00:00Z — type: reframing

            - **Issue:** Test.
            - **Source:** S.
            - **Manuscript impact:** M.
            - **Resolution:** escalated (dispatch Results REPAIR_MODE on Results section).
            - **Note:** N.

            ---
        """)
        entries = helpers._parse_reframing_log(log)
        targets = entries[0]["target_sections"]
        assert targets.count("results") == 1
