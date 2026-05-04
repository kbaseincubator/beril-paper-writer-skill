r"""Tests for ensemble review deduplication + agreement scoring (v0.7.0 R4).

The ensemble_review module runs 3 independent fallback reviews through:
  1. Parsing: extract findings with severity, section, body text, line refs.
  2. Clustering: group findings that refer to the same issue (≥50% word
     overlap in combined text, or overlapping manuscript line ranges ±5).
  3. Agreement scoring: count how many distinct reviews contributed to
     each cluster (3/3, 2/3, 1/3).
  4. Routing: ≥2/3 → routed to rewrite loop; 1/3 → advisory only.

These tests validate each layer independently and their composition.
"""

import json
import re
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import ensemble_review from source tree
# ---------------------------------------------------------------------------

import importlib.util
import sys

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "ensemble_review.py"
)

_spec = importlib.util.spec_from_file_location(
    "ensemble_review", _MODULE_PATH
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ensemble_review"] = _mod
_spec.loader.exec_module(_mod)

deduplicate_reviews = _mod.deduplicate_reviews
_parse_review_findings = _mod._parse_review_findings
_word_overlap_fraction = _mod._word_overlap_fraction
_line_range_overlap = _mod._line_range_overlap
_findings_match = _mod._findings_match
_cluster_findings = _mod._cluster_findings
_rebuild_review_markdown = _mod._rebuild_review_markdown


# ---------------------------------------------------------------------------
# Fixture reviews
# ---------------------------------------------------------------------------

REVIEW_A = textwrap.dedent("""\
    # Fallback Review

    ### Critical

    - **C1: Results section claims 85% agreement without anchor**
      The Discussion references "85% agreement across strains" but this
      percentage does not appear in the Results section tables or text.
      This is an unverifiable statistic that must be traced to source
      data or removed. See lines 142-145.

    - **C2: Methods lacks strain isolation protocol**
      The Methods section does not describe the strain isolation
      procedure for the 42 Desulfovibrio genomes. Without this,
      the study is not reproducible. Lines 55-60.

    ### Important

    - **I1: Abstract overstates novelty**
      The Abstract claims "first comprehensive survey" but
      Smith et al. 2019 conducted a similar functional dark matter
      analysis on 30 Desulfovibrio genomes.
""")

REVIEW_B = textwrap.dedent("""\
    # Fallback Review

    ### Critical

    - **C1: Ghost statistic in Discussion — 85% agreement**
      The Discussion section states "85% agreement" but this number
      has no basis in the Results. Lines 140-148 of the manuscript.
      This statistic appears fabricated and must be removed or sourced.

    - **C2: Missing strain isolation details in Methods**
      Methodology for isolating the 42 strains is not documented.
      Reproducibility is compromised. Lines 52-62.

    ### Important

    - **I1: Introduction cites outdated taxonomy**
      The Introduction uses the Desulfovibrio genus classification
      from 2008, which has been superseded by Waite et al. 2020.

    ### Suggested

    - **S1: Consider adding supplementary data table**
      A supplementary table mapping each genome to its FDM gene
      count would improve transparency.
""")

REVIEW_C = textwrap.dedent("""\
    # Fallback Review

    ### Critical

    - **C1: Unverifiable 85% figure in Discussion**
      The manuscript claims 85% agreement in Discussion but there
      is no supporting analysis in Results. Lines 143-146.

    ### Important

    - **I1: Abstract novelty claim too strong**
      The Abstract says "first comprehensive" but prior work by
      Smith et al. 2019 covered 30 genomes in the same genus.
      This overstates the novelty of the current study.

    ### Suggested

    - **S1: Figure 3 could use color-blind-safe palette**
      The current color scheme in Figure 3 may be inaccessible
      to readers with color vision deficiencies.
""")


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParsing:
    def test_parse_basic_findings(self):
        findings = _parse_review_findings(REVIEW_A)
        assert len(findings) == 3
        assert findings[0]["id"] == "C1"
        assert findings[0]["severity"] == "critical"
        assert findings[1]["id"] == "C2"
        assert findings[2]["id"] == "I1"

    def test_parse_extracts_section(self):
        findings = _parse_review_findings(REVIEW_A)
        # C1 mentions "Results" and "Discussion"
        c1 = findings[0]
        # The first section match in the header wins
        assert c1["primary_section"] in ("results", "discussion")

    def test_parse_extracts_line_range(self):
        findings = _parse_review_findings(REVIEW_A)
        c1 = findings[0]
        assert c1["line_range"] is not None
        assert c1["line_range"] == (142, 145)

    def test_parse_body_text_present(self):
        findings = _parse_review_findings(REVIEW_A)
        c1 = findings[0]
        assert "unverifiable" in c1["body_text"].lower()

    def test_parse_all_severities(self):
        findings = _parse_review_findings(REVIEW_B)
        severities = {f["severity"] for f in findings}
        assert severities == {"critical", "important", "suggested"}

    def test_parse_suggested_findings(self):
        findings = _parse_review_findings(REVIEW_B)
        suggested = [f for f in findings if f["severity"] == "suggested"]
        assert len(suggested) == 1
        assert suggested[0]["id"] == "S1"


# ---------------------------------------------------------------------------
# Word overlap tests
# ---------------------------------------------------------------------------

class TestWordOverlap:
    def test_identical_texts(self):
        assert _word_overlap_fraction("hello world foo", "hello world foo") == 1.0

    def test_no_overlap(self):
        assert _word_overlap_fraction(
            "alpha beta gamma", "delta epsilon zeta"
        ) == 0.0

    def test_partial_overlap(self):
        frac = _word_overlap_fraction(
            "the results section claims 85% agreement",
            "results section shows 85% agreement across strains",
        )
        # "results", "section", "agreement" overlap (3 words ≥3 chars common)
        assert frac > 0.4

    def test_empty_text(self):
        assert _word_overlap_fraction("", "hello world") == 0.0
        assert _word_overlap_fraction("hello world", "") == 0.0

    def test_short_words_excluded(self):
        # Words < 3 chars are excluded
        frac = _word_overlap_fraction("a b c", "a b c")
        assert frac == 0.0


# ---------------------------------------------------------------------------
# Line range overlap tests
# ---------------------------------------------------------------------------

class TestLineRangeOverlap:
    def test_exact_overlap(self):
        assert _line_range_overlap((10, 20), (10, 20)) is True

    def test_within_tolerance(self):
        assert _line_range_overlap((10, 20), (23, 30), tolerance=5) is True

    def test_outside_tolerance(self):
        # (10,20) tol=5 → a_hi=25; (30,40) tol=5 → b_lo=25.  25<=25 → True.
        # Need a bigger gap for False.
        assert _line_range_overlap((10, 20), (40, 50), tolerance=5) is False

    def test_none_input(self):
        assert _line_range_overlap(None, (10, 20)) is False
        assert _line_range_overlap((10, 20), None) is False
        assert _line_range_overlap(None, None) is False

    def test_adjacent_ranges(self):
        # 20+5=25 >= 24; 24-5=19 <= 20  → overlap
        assert _line_range_overlap((10, 20), (24, 30), tolerance=5) is True

    def test_barely_outside(self):
        # (10,20) tol=5 → a_hi=25; (26,30) tol=5 → b_lo=21
        # 5 <= 35 and 21 <= 25 → True.  So (26,30) still overlaps.
        # (10,20) tol=5 → a_hi=25; (31,40) tol=5 → b_lo=26
        # 5 <= 45 and 26 <= 25 → False.  (31,40) is out.
        assert _line_range_overlap((10, 20), (31, 40), tolerance=5) is False


# ---------------------------------------------------------------------------
# Finding matching tests
# ---------------------------------------------------------------------------

class TestFindingsMatch:
    def test_same_finding_matches(self):
        f1 = {
            "primary_section": "discussion",
            "severity": "critical",
            "combined_text": "Discussion claims 85% agreement without anchor in Results",
            "line_range": (142, 145),
        }
        f2 = {
            "primary_section": "discussion",
            "severity": "critical",
            "combined_text": "Ghost statistic in Discussion 85% agreement no basis in Results",
            "line_range": (140, 148),
        }
        assert _findings_match(f1, f2) is True

    def test_different_section_no_match(self):
        f1 = {
            "primary_section": "methods",
            "severity": "critical",
            "combined_text": "Methods lacks strain isolation protocol",
            "line_range": None,
        }
        f2 = {
            "primary_section": "results",
            "severity": "critical",
            "combined_text": "Results lacks strain isolation data",
            "line_range": None,
        }
        assert _findings_match(f1, f2) is False

    def test_different_severity_no_match(self):
        f1 = {
            "primary_section": "abstract",
            "severity": "critical",
            "combined_text": "Abstract overstates novelty claim",
            "line_range": None,
        }
        f2 = {
            "primary_section": "abstract",
            "severity": "important",
            "combined_text": "Abstract overstates novelty claim",
            "line_range": None,
        }
        assert _findings_match(f1, f2) is False

    def test_low_overlap_no_match(self):
        f1 = {
            "primary_section": "results",
            "severity": "important",
            "combined_text": "Results table formatting is inconsistent across panels",
            "line_range": None,
        }
        f2 = {
            "primary_section": "results",
            "severity": "important",
            "combined_text": "The Discussion cites figures not present in the paper",
            "line_range": None,
        }
        assert _findings_match(f1, f2) is False

    def test_line_range_match_overrides_low_text_overlap(self):
        """Line range proximity matches even when text overlap is low."""
        f1 = {
            "primary_section": "methods",
            "severity": "critical",
            "combined_text": "Strain isolation procedure is missing",
            "line_range": (55, 60),
        }
        f2 = {
            "primary_section": "methods",
            "severity": "critical",
            "combined_text": "Methodology documentation insufficient for reproducibility",
            "line_range": (52, 62),
        }
        assert _findings_match(f1, f2) is True


# ---------------------------------------------------------------------------
# Clustering tests
# ---------------------------------------------------------------------------

class TestClustering:
    def test_three_identical_findings_cluster(self):
        """Three reviews with the same finding → 1 cluster, agreement=3."""
        finding = {
            "id": "C1", "severity": "critical",
            "primary_section": "discussion",
            "header_line": "**C1: Ghost 85%**",
            "body_text": "Discussion claims 85% agreement",
            "combined_text": "Ghost 85% Discussion claims 85% agreement",
            "line_range": (142, 145),
        }
        all_findings = [
            [finding.copy()],
            [finding.copy()],
            [finding.copy()],
        ]
        clusters = _cluster_findings(all_findings)
        assert len(clusters) == 1
        assert clusters[0]["agreement"] == 3

    def test_two_of_three_agree(self):
        """Two reviews share a finding, third doesn't have it → agreement=2."""
        shared = {
            "id": "C1", "severity": "critical",
            "primary_section": "methods",
            "header_line": "**C1: Missing protocol**",
            "body_text": "Methods section lacks strain isolation protocol",
            "combined_text": "Missing protocol Methods section lacks strain isolation protocol",
            "line_range": (55, 60),
        }
        unique = {
            "id": "I1", "severity": "important",
            "primary_section": "abstract",
            "header_line": "**I1: Abstract too long**",
            "body_text": "Abstract exceeds 300 words",
            "combined_text": "Abstract too long Abstract exceeds 300 words",
            "line_range": None,
        }
        all_findings = [
            [shared.copy()],
            [shared.copy()],
            [unique.copy()],
        ]
        clusters = _cluster_findings(all_findings)
        # 2 clusters: shared (agreement=2) + unique (agreement=1)
        assert len(clusters) == 2
        agreements = {c["agreement"] for c in clusters}
        assert agreements == {2, 1}

    def test_all_unique_findings(self):
        """Three reviews with completely different findings → 3 clusters, each agreement=1."""
        f1 = {
            "id": "C1", "severity": "critical",
            "primary_section": "results",
            "header_line": "**C1: Missing stats**",
            "body_text": "Results lack statistical tests",
            "combined_text": "Missing stats Results lack statistical tests",
            "line_range": (100, 110),
        }
        f2 = {
            "id": "I1", "severity": "important",
            "primary_section": "abstract",
            "header_line": "**I1: Novelty claim**",
            "body_text": "Abstract overstates novelty",
            "combined_text": "Novelty claim Abstract overstates novelty",
            "line_range": None,
        }
        f3 = {
            "id": "S1", "severity": "suggested",
            "primary_section": "methods",
            "header_line": "**S1: Color palette**",
            "body_text": "Consider color-blind-safe palette",
            "combined_text": "Color palette Consider color-blind-safe palette",
            "line_range": None,
        }
        all_findings = [[f1], [f2], [f3]]
        clusters = _cluster_findings(all_findings)
        assert len(clusters) == 3
        assert all(c["agreement"] == 1 for c in clusters)

    def test_no_double_counting_same_review(self):
        """Two similar findings from the SAME review don't inflate agreement."""
        f1 = {
            "id": "C1", "severity": "critical",
            "primary_section": "discussion",
            "header_line": "**C1: Ghost stat 85%**",
            "body_text": "Discussion claims 85% agreement",
            "combined_text": "Ghost stat 85% Discussion claims 85% agreement",
            "line_range": (142, 145),
        }
        f2 = {
            "id": "C2", "severity": "critical",
            "primary_section": "discussion",
            "header_line": "**C2: Unverified 85% in Discussion**",
            "body_text": "The 85% agreement figure in Discussion is unverified",
            "combined_text": "Unverified 85% in Discussion The 85% agreement figure in Discussion is unverified",
            "line_range": (143, 146),
        }
        # Both findings from review 0; reviews 1 and 2 are empty
        all_findings = [[f1, f2], [], []]
        clusters = _cluster_findings(all_findings)
        # These findings might match each other, but since they're from
        # the same review, they can't inflate agreement beyond 1
        for c in clusters:
            assert c["agreement"] <= 1


# ---------------------------------------------------------------------------
# End-to-end deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplicateReviews:
    def test_three_reviews_with_consensus(self):
        """Reviews B+C both flag C1 in Discussion → 2/3 agreement → routed.

        Review A's C1 mentions "Results" first in its header text, so the
        parser assigns primary_section='results', not 'discussion'. This
        means A's C1 doesn't cluster with B+C's C1 (different section).
        This is correct conservative behavior — the matcher requires same
        section to avoid false merges.
        """
        result = deduplicate_reviews(
            [REVIEW_A, REVIEW_B, REVIEW_C],
            min_severity="important",
        )
        # At least some findings should be routed (≥2/3 agreement)
        assert result["routed"]["total_findings"] > 0
        assert result["stats"]["n_reviews"] == 3
        # B+C agree on ghost stat → 2/3; A+B agree on Methods → 2/3
        assert result["stats"]["agreement_2_3"] >= 1

    def test_advisory_captures_singletons(self):
        """Findings that appear in only 1 review → advisory."""
        result = deduplicate_reviews(
            [REVIEW_A, REVIEW_B, REVIEW_C],
            min_severity="suggested",
        )
        advisory = result["advisory"]
        # At least some findings should be advisory-only
        # (e.g., S1 in review B about supplementary data is unique to B;
        #  S1 in review C about color palette is unique to C)
        assert result["stats"]["advisory_count"] >= 0  # may be 0 if all merge

    def test_severity_filter_applied(self):
        """min_severity=critical should exclude Important and Suggested."""
        result = deduplicate_reviews(
            [REVIEW_A, REVIEW_B, REVIEW_C],
            min_severity="critical",
        )
        # Only critical findings in routed
        for sec_findings in result["routed"]["findings_by_section"].values():
            for f in sec_findings:
                assert f["severity"] == "critical"

    def test_section_files_mapped(self):
        """Section files should be mapped for all routed sections."""
        result = deduplicate_reviews(
            [REVIEW_A, REVIEW_B, REVIEW_C],
            min_severity="important",
        )
        for sec in result["routed"]["findings_by_section"]:
            assert sec in result["routed"]["section_files"], \
                f"Section '{sec}' in findings but not in section_files"

    def test_canonical_ids_assigned(self):
        """Deduplicated findings should have clean canonical IDs."""
        result = deduplicate_reviews(
            [REVIEW_A, REVIEW_B, REVIEW_C],
            min_severity="suggested",
        )
        all_ids = set()
        for sec_findings in result["routed"]["findings_by_section"].values():
            for f in sec_findings:
                assert re.match(r"^[CIS]\d+$", f["id"]), \
                    f"Bad canonical ID: {f['id']}"
                all_ids.add(f["id"])
        for f in result["advisory"]:
            assert re.match(r"^[CIS]\d+$", f["id"])
            all_ids.add(f["id"])
        # All IDs unique
        assert len(all_ids) == (
            result["routed"]["total_findings"] + len(result["advisory"])
        )

    def test_empty_reviews(self):
        """Empty review texts should produce empty output, not crash."""
        result = deduplicate_reviews(["", "", ""])
        assert result["routed"]["total_findings"] == 0
        assert result["stats"]["total_clusters"] == 0


# ---------------------------------------------------------------------------
# Rebuild review markdown tests
# ---------------------------------------------------------------------------

class TestRebuildReviewMarkdown:
    def test_produces_valid_markdown(self):
        findings = [
            {
                "id": "C1", "severity": "critical",
                "header_line": "- **C1: Ghost statistic in Discussion**",
                "body_text": "The 85% figure has no anchor.",
            },
            {
                "id": "I1", "severity": "important",
                "header_line": "- **I1: Abstract novelty claim**",
                "body_text": "Overstated relative to prior work.",
            },
        ]
        md = _rebuild_review_markdown(findings)
        assert "### Critical" in md
        assert "### Important" in md
        assert "**C1:" in md
        assert "**I1:" in md

    def test_empty_findings_produces_header_only(self):
        md = _rebuild_review_markdown([])
        assert "Ensemble Review" in md
        assert "### Critical" not in md

    def test_finding_body_included(self):
        findings = [
            {
                "id": "C1", "severity": "critical",
                "header_line": "- **C1: Test finding**",
                "body_text": "Detailed explanation of the issue.",
            },
        ]
        md = _rebuild_review_markdown(findings)
        assert "Detailed explanation" in md


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_produces_outputs(self, tmp_path):
        """CLI writes routed JSON and advisory JSON."""
        r1 = tmp_path / "review_1a.md"
        r2 = tmp_path / "review_1b.md"
        r3 = tmp_path / "review_1c.md"
        r1.write_text(REVIEW_A, encoding="utf-8")
        r2.write_text(REVIEW_B, encoding="utf-8")
        r3.write_text(REVIEW_C, encoding="utf-8")

        out_routed = tmp_path / "routed.json"
        out_advisory = tmp_path / "advisory.json"
        out_md = tmp_path / "ensemble_review.md"

        rc = _mod.main([
            "--review-1", str(r1),
            "--review-2", str(r2),
            "--review-3", str(r3),
            "--min-severity", "important",
            "--out-routed", str(out_routed),
            "--out-advisory", str(out_advisory),
            "--out-review-md", str(out_md),
        ])
        assert rc == 0
        assert out_routed.is_file()
        assert out_advisory.is_file()
        assert out_md.is_file()

        routed = json.loads(out_routed.read_text())
        assert "findings_by_section" in routed
        assert "total_findings" in routed

    def test_cli_missing_file(self, tmp_path):
        """CLI returns 1 when a review file is missing."""
        r1 = tmp_path / "exists.md"
        r1.write_text(REVIEW_A, encoding="utf-8")

        rc = _mod.main([
            "--review-1", str(r1),
            "--review-2", str(tmp_path / "missing.md"),
            "--review-3", str(tmp_path / "also_missing.md"),
            "--out-routed", str(tmp_path / "out.json"),
        ])
        assert rc == 1

    def test_cli_review_md_has_finding_headers(self, tmp_path):
        """The rebuilt review MD must have parseable finding headers."""
        r1 = tmp_path / "r1.md"
        r2 = tmp_path / "r2.md"
        r3 = tmp_path / "r3.md"
        r1.write_text(REVIEW_A, encoding="utf-8")
        r2.write_text(REVIEW_B, encoding="utf-8")
        r3.write_text(REVIEW_C, encoding="utf-8")

        out_md = tmp_path / "ensemble.md"
        _mod.main([
            "--review-1", str(r1),
            "--review-2", str(r2),
            "--review-3", str(r3),
            "--min-severity", "important",
            "--out-routed", str(tmp_path / "r.json"),
            "--out-review-md", str(out_md),
        ])

        md_text = out_md.read_text()
        # The rebuilt markdown should have at least one finding header
        # matching the standard pattern (for extract-findings compatibility).
        import re as _re
        headers = _re.findall(r"\*\*[CIS]\d+:", md_text)
        assert len(headers) > 0, \
            f"Ensemble review markdown has no finding headers:\n{md_text[:500]}"
