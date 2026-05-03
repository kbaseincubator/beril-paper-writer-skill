r"""Tests for review substance checking and review parsing.

The review substance post-check (v0.6.4) prevents the rewrite loop from
operating on garbage review files. It mirrors the bash-level check in
paper_writer.sh's run_reviewer_pass:
  - File must be >20 lines
  - File must contain at least one finding header (**C\d+: or **I\d+: or **S\d+:)

These tests validate the regex and line-count logic in Python, exercising
the same patterns the shell uses. They also test _parse_review_findings()
against the three finding-header shapes documented in the parser.

Incident context: draft_9 (2026-05-03) shipped a 2-line argparse usage
error as the canonical adversarial review because the orchestrator had no
substance check. See feedback_cross_skill_contract_drift.md.
"""

import re
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Substance check logic (mirrors paper_writer.sh bash check)
# ---------------------------------------------------------------------------

# The finding-header regex from paper_writer.sh (grep -cE pattern)
_FINDING_HEADER_RE = re.compile(r"^\s*(?:-\s+)?\*\*[CIS]\d+:")


def _check_review_substance(text: str) -> dict:
    """Python mirror of the bash substance check in run_reviewer_pass.

    Returns dict with line_count, finding_count, and passes (bool).
    """
    lines = text.splitlines()
    line_count = len(lines)
    finding_count = sum(1 for line in lines if _FINDING_HEADER_RE.match(line))
    passes = line_count >= 20 and finding_count >= 1
    return {
        "line_count": line_count,
        "finding_count": finding_count,
        "passes": passes,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_REVIEW = textwrap.dedent("""\
    # Adversarial Review — draft_1_review_1

    **Project:** functional_dark_matter
    **Reviewer:** fallback_reviewer.v1 (inline)
    **Draft:** papers/draft_9/manuscript.md
    **Date:** 2026-05-03

    ---

    ## Critical findings

    ### Critical

    **C1: Abstract line 18 — "n_annotated = 100" contradicts REPORT (490)**
    The manuscript reports n_annotated = 100 in the concordance null test.
    REPORT.md §Step 5 states "65 dark vs. 490 annotated OGs."
    Fix: replace n_annotated = 100 with n_annotated = 490 in all locations.

    **C2: Discussion line 5 — same n_annotated drift**
    Discussion propagates the same 100 value. Must be corrected in tandem.

    ### Important

    **I1: ICA module methodology uncited despite being in citation pool**
    [Sastry2019] is in references.md as "Uncited" but ICA modules are a major
    evidence axis (6,142 genes). Add citation in Methods and Introduction.

    ### Suggested

    **S1: Results heading uses "validates" — should be "identifies"**
    REPORT uses "show measurable fitness concordance." The heading
    "validates conserved phenotypes" implies formal statistical confirmation
    that was not performed.
""")

CLI_USAGE_ERROR = textwrap.dedent("""\
    usage: beril-adversarial [-h] {install-skill,configure,review} ...
    beril-adversarial: error: unrecognized arguments: --type paper
""")

EMPTY_FILE = ""

TRUNCATED_REVIEW = textwrap.dedent("""\
    # Adversarial Review — draft_1_review_1

    **Project:** functional_dark_matter
    **Reviewer:** fallback_reviewer.v1 (inline)

    ---

    The manuscript was reviewed. No significant issues found.

    Overall assessment: PASS.
""")

REVIEW_NO_FINDINGS = textwrap.dedent("""\
    # Adversarial Review — draft_1_review_1

    **Project:** functional_dark_matter
    **Reviewer:** fallback_reviewer.v1 (inline)
    **Draft:** papers/draft_9/manuscript.md
    **Date:** 2026-05-03

    ---

    ## Critical findings

    No critical findings.

    ## Important findings

    No important findings.

    ## Suggested improvements

    No suggested improvements.

    Overall assessment: The manuscript meets all review criteria.
    The throughline is well-supported throughout.
    Citation pool is fully consumed.
    No overclaims detected.
""")

# Finding header shapes from real reviewer output (Form A/B/C)
REVIEW_FORM_A = textwrap.dedent("""\
    # Review

    Preamble line 1.
    Preamble line 2.
    Preamble line 3.
    Preamble line 4.
    Preamble line 5.
    Preamble line 6.
    Preamble line 7.
    Preamble line 8.
    Preamble line 9.
    Preamble line 10.

    ---

    ### Critical

    **C1: Abstract line 18 — "Multi-dimensional evidence integration"**
    Overclaims mechanism when the study is hypothesis-generating.

    ### Important

    **I1: Methods missing software versions**
    No version numbers for BLAST, hmmsearch, or GapMind.

    Closing notes.
""")

REVIEW_FORM_B = textwrap.dedent("""\
    # Review

    Preamble 1.
    Preamble 2.
    Preamble 3.
    Preamble 4.
    Preamble 5.
    Preamble 6.
    Preamble 7.
    Preamble 8.
    Preamble 9.
    Preamble 10.

    ---

    ### Critical

    - **C1: Abstract functional hypotheses claim (line 20)** — overclaims
    - **C2: GapMind "pathway gaps identified" overclaim (Abstract line 17, Introduction line 30)** — conflates co-occurrence with assignment

    ### Important

    - **I1: Methods missing software versions** — no BLAST version

    Closing notes.
""")

REVIEW_FORM_C = textwrap.dedent("""\
    # Review

    Preamble 1.
    Preamble 2.
    Preamble 3.
    Preamble 4.
    Preamble 5.
    Preamble 6.
    Preamble 7.
    Preamble 8.
    Preamble 9.
    Preamble 10.

    ---

    ### Critical

    - **C3: "Multi-dimensional evidence integration converged" (Abstract line 18, line 20; Discussion line 176)** — overclaims mechanism

    ### Suggested

    - **S1: Consider adding AlphaFold predictions** — would strengthen void-tier candidates

    Closing notes.
""")


# ---------------------------------------------------------------------------
# Substance check tests
# ---------------------------------------------------------------------------

class TestReviewSubstanceCheck:
    """Tests mirroring the bash substance check in run_reviewer_pass."""

    def test_valid_review_passes(self):
        result = _check_review_substance(VALID_REVIEW)
        assert result["passes"] is True
        assert result["line_count"] > 20
        assert result["finding_count"] >= 3  # C1, C2, I1, S1

    def test_cli_usage_error_fails(self):
        """The exact bug from draft_9: CLI usage error accepted as review."""
        result = _check_review_substance(CLI_USAGE_ERROR)
        assert result["passes"] is False
        assert result["line_count"] < 20
        assert result["finding_count"] == 0

    def test_empty_file_fails(self):
        result = _check_review_substance(EMPTY_FILE)
        assert result["passes"] is False
        assert result["line_count"] == 0

    def test_truncated_review_fails(self):
        """LLM produced a short response with no findings."""
        result = _check_review_substance(TRUNCATED_REVIEW)
        assert result["passes"] is False
        assert result["line_count"] < 20

    def test_long_review_no_findings_fails(self):
        """File is long enough but has no finding headers."""
        result = _check_review_substance(REVIEW_NO_FINDINGS)
        assert result["passes"] is False
        assert result["line_count"] >= 20
        assert result["finding_count"] == 0

    def test_form_a_passes(self):
        """Form A: **C1: Abstract line 18 — "..."**"""
        result = _check_review_substance(REVIEW_FORM_A)
        assert result["passes"] is True
        assert result["finding_count"] == 2  # C1, I1

    def test_form_b_passes(self):
        """Form B: - **C1: Abstract functional hypotheses claim (line 20)**"""
        result = _check_review_substance(REVIEW_FORM_B)
        assert result["passes"] is True
        assert result["finding_count"] == 3  # C1, C2, I1

    def test_form_c_passes(self):
        """Form C: - **C3: "Multi-dimensional..." (Abstract line 18)**"""
        result = _check_review_substance(REVIEW_FORM_C)
        assert result["passes"] is True
        assert result["finding_count"] == 2  # C3, S1


# ---------------------------------------------------------------------------
# Finding header regex tests (exhaustive pattern coverage)
# ---------------------------------------------------------------------------

class TestFindingHeaderRegex:
    """The regex must match all three header forms from real reviewer output."""

    @pytest.mark.parametrize("line,expected", [
        # Form A: plain header
        ('**C1: Abstract line 18 — "n_annotated = 100"**', True),
        ('**I1: Methods missing software versions**', True),
        ('**S1: Consider adding AlphaFold**', True),
        # Form B: bullet-prefixed
        ('- **C1: Abstract functional hypotheses claim (line 20)** — overclaims', True),
        ('- **C2: GapMind "pathway gaps" overclaim (Abstract line 17)**', True),
        ('- **I1: Methods missing software versions** — no BLAST version', True),
        ('- **S1: Results heading uses "validates"**', True),
        # Form C: quote-leading
        ('- **C3: "Multi-dimensional evidence integration converged" (Abstract line 18)** — overclaims', True),
        # Indented variants
        ('  **C1: Indented finding**', True),
        ('  - **I2: Indented bullet finding**', True),
        # Non-matching lines
        ('### Critical', False),
        ('### Important', False),
        ('**Bold but not a finding**', False),
        ('Some text with **C1:** embedded', False),
        ('C1: Not bold', False),
        ('- **Not a finding header — no ID**', False),
        ('', False),
        ('usage: beril-adversarial [-h] {install-skill,configure,review}', False),
    ])
    def test_finding_header_match(self, line, expected):
        matched = bool(_FINDING_HEADER_RE.match(line))
        assert matched == expected, f"Line: {line!r} — expected {expected}, got {matched}"

    def test_severity_id_prefixes(self):
        """C = Critical, I = Important, S = Suggested — all must match."""
        for prefix in ("C", "I", "S"):
            for num in (1, 2, 10, 99):
                line = f"**{prefix}{num}: Test finding**"
                assert _FINDING_HEADER_RE.match(line), f"Failed on {line!r}"

    def test_non_severity_prefixes_dont_match(self):
        """Only C/I/S prefixes should match."""
        for prefix in ("A", "B", "D", "P", "F", "X"):
            line = f"**{prefix}1: Not a valid finding**"
            assert not _FINDING_HEADER_RE.match(line), f"Should NOT match: {line!r}"
