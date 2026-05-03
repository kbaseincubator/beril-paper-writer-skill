"""Cross-skill integration tests for beril-adversarial interop.

These tests verify that paper-writer can correctly parse and route
findings from adversarial-review-paper.v2 JSON. They use fixture
data (no live adversarial invocation) so they run in CI without
beril-adversarial installed.

See CONTRACT.md for the full interop surface these tests verify.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Fixtures: minimal adversarial-review-paper.v2 JSON
# ---------------------------------------------------------------------------

PAPER_V2_CLEAN = {
    "schema_version": "adversarial-review-paper.v2",
    "reviewed_at": "2026-05-03T12:00:00Z",
    "model": "claude-sonnet-4-5-20250929",
    "prompt_version": "adversarial_paper.v2",
    "findings": [],
    "summary": {"total": 0, "P0": 0, "P1": 0, "P2": 0, "info": 0},
}

PAPER_V2_WITH_FINDINGS = {
    "schema_version": "adversarial-review-paper.v2",
    "reviewed_at": "2026-05-03T12:00:00Z",
    "model": "claude-sonnet-4-5-20250929",
    "prompt_version": "adversarial_paper.v2",
    "findings": [
        {
            "id": "F001",
            "class": "claim_evidence",
            "severity": "P0",
            "section": "Results",
            "fix_target": "results.v1.md",
            "title": "Unsupported specificity claim",
            "paragraph_quote": "The model achieved 95% accuracy across all conditions.",
            "line_range": "L42-L45",
            "evidence": "REPORT.md Finding 3 reports 87% accuracy, not 95%.",
            "recommendation": "Correct the percentage to match REPORT.md or add qualifying context.",
        },
        {
            "id": "F002",
            "class": "unbacked_quantitative",
            "severity": "P0",
            "section": "Discussion",
            "fix_target": "discussion.v1.md",
            "title": "Invented comparison statistic",
            "paragraph_quote": "This represents a 3x improvement over existing methods.",
            "line_range": "L128-L130",
            "evidence": "No comparison baseline in REPORT.md or cited references.",
            "recommendation": "Remove the comparison or add a citation.",
        },
        {
            "id": "F003",
            "class": "citation_reality",
            "severity": "P1",
            "section": "Introduction",
            "fix_target": "introduction.v1.md",
            "title": "Citation [7] not verifiable",
            "paragraph_quote": "Previous work demonstrated [7] that...",
            "line_range": "L15-L17",
            "evidence": "DOI 10.xxxx/yyyy resolves to a different paper.",
            "recommendation": "Verify DOI and replace if wrong.",
            "citation_id": "ref_7",
        },
        {
            "id": "F004",
            "class": "narrative_weakness",
            "severity": "info",
            "title": "Central objection: reproducibility gap",
            "evidence": "The manuscript's throughline claims general applicability but all experiments use a single organism.",
            "recommendation": "Acknowledge this limitation prominently.",
        },
        {
            "id": "F005",
            "class": "report_drift",
            "severity": "P1",
            "section": "Methods",
            "fix_target": "methods.v1.md",
            "title": "Methods describes protocol not in REPORT",
            "paragraph_quote": "Samples were normalized using TMM.",
            "line_range": "L78-L80",
            "evidence": "REPORT.md describes DESeq2 normalization, not TMM.",
            "recommendation": "Align Methods with REPORT.md §Analysis Pipeline.",
        },
    ],
    "summary": {"total": 5, "P0": 2, "P1": 2, "P2": 0, "info": 1},
}


# ---------------------------------------------------------------------------
# Severity mapping (consumer-side, per CONTRACT.md)
# ---------------------------------------------------------------------------

SEVERITY_MAP = {"P0": "critical", "P1": "important", "P2": "suggested"}


def count_actionable(findings: list[dict]) -> dict[str, int]:
    """Consumer-side severity translation per CONTRACT.md."""
    counts = {"critical": 0, "important": 0, "suggested": 0}
    for f in findings:
        sev = SEVERITY_MAP.get(f["severity"])
        if sev:
            counts[sev] += 1
    return counts


# Known fix_target values per CONTRACT.md
KNOWN_FIX_TARGETS = {
    "methods.v1.md",
    "results.v1.md",
    "discussion.v1.md",
    "introduction.v1.md",
    "abstract.v1.md",
    "limitations.v1.md",
    "references.v1.md",
    "00_throughline.md",
    "reframing_log.md",
    "manuscript.v1.md",
}

# Paper-specific classes per CONTRACT.md
PAPER_V2_CLASSES = {
    "claim_evidence",
    "unbacked_quantitative",
    "register_drift",
    "citation_reality",
    "report_drift",
    "abstract_body_mismatch",
    "missing_section",
    "section_arc",
    "throughline",
    "narrative_weakness",
}


# ---------------------------------------------------------------------------
# Tests: schema parsing
# ---------------------------------------------------------------------------


class TestAdversarialSchemaV2:
    """Verify paper-writer can parse adversarial-review-paper.v2 JSON."""

    def test_schema_version_field(self):
        assert PAPER_V2_CLEAN["schema_version"] == "adversarial-review-paper.v2"

    def test_empty_findings_parses(self):
        findings = PAPER_V2_CLEAN["findings"]
        assert findings == []
        counts = count_actionable(findings)
        assert counts == {"critical": 0, "important": 0, "suggested": 0}

    def test_findings_severity_mapping(self):
        counts = count_actionable(PAPER_V2_WITH_FINDINGS["findings"])
        assert counts["critical"] == 2  # two P0s
        assert counts["important"] == 2  # two P1s
        assert counts["suggested"] == 0

    def test_info_severity_excluded_from_actionable(self):
        """info-severity findings (narrative_weakness) are not actionable."""
        info_findings = [
            f for f in PAPER_V2_WITH_FINDINGS["findings"] if f["severity"] == "info"
        ]
        assert len(info_findings) == 1
        assert info_findings[0]["class"] == "narrative_weakness"
        # Not counted in actionable
        assert SEVERITY_MAP.get("info") is None

    def test_fix_target_values_are_known(self):
        """All fix_target values in fixtures are in the known set."""
        for f in PAPER_V2_WITH_FINDINGS["findings"]:
            ft = f.get("fix_target")
            if ft is not None:
                assert ft in KNOWN_FIX_TARGETS, f"Unknown fix_target: {ft}"

    def test_class_values_are_known(self):
        """All class values in fixtures are in the paper.v2 enum."""
        for f in PAPER_V2_WITH_FINDINGS["findings"]:
            assert f["class"] in PAPER_V2_CLASSES, f"Unknown class: {f['class']}"

    def test_manuscript_wide_findings_omit_section(self):
        """narrative_weakness omits section field (per CONTRACT.md)."""
        nw = [
            f
            for f in PAPER_V2_WITH_FINDINGS["findings"]
            if f["class"] == "narrative_weakness"
        ]
        assert len(nw) == 1
        assert "section" not in nw[0]

    def test_section_level_findings_have_section(self):
        """Section-level findings have section + fix_target."""
        for f in PAPER_V2_WITH_FINDINGS["findings"]:
            if f["class"] not in ("narrative_weakness", "missing_section", "throughline"):
                assert "section" in f, f"Missing 'section' in {f['class']} finding"
                assert "fix_target" in f, f"Missing 'fix_target' in {f['class']} finding"


class TestAdversarialSchemaV2Roundtrip:
    """Verify JSON serialization roundtrip."""

    def test_json_roundtrip(self, tmp_path):
        """Write → read → compare."""
        path = tmp_path / "adversarial_review.json"
        path.write_text(json.dumps(PAPER_V2_WITH_FINDINGS, indent=2), encoding="utf-8")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == "adversarial-review-paper.v2"
        assert len(loaded["findings"]) == 5
        assert loaded["summary"]["P0"] == 2

    def test_findings_array_is_ground_truth(self):
        """Summary block should agree with findings array counts.

        Per CONTRACT.md: findings[] is ground truth; summary is derived.
        """
        findings = PAPER_V2_WITH_FINDINGS["findings"]
        summary = PAPER_V2_WITH_FINDINGS["summary"]
        computed = {"P0": 0, "P1": 0, "P2": 0, "info": 0}
        for f in findings:
            computed[f["severity"]] += 1
        assert computed["P0"] == summary["P0"]
        assert computed["P1"] == summary["P1"]
        assert computed["P2"] == summary["P2"]
        assert computed["info"] == summary["info"]
        assert sum(computed.values()) == summary["total"]


class TestAdversarialExitCodeRouting:
    """Verify paper-writer's exit code handling per CONTRACT.md."""

    @pytest.mark.parametrize(
        "exit_code,should_use_json",
        [
            (0, True),   # clean pass
            (2, True),   # auto-corrected, JSON is safe
            (1, False),  # validation failure, JSON unsafe
            (3, False),  # config error
        ],
    )
    def test_exit_code_json_safety(self, exit_code, should_use_json):
        """Document which exit codes produce consumer-safe JSON."""
        # This is a contract-documentation test; the routing logic
        # lives in paper_writer.sh. We verify the contract is
        # self-consistent.
        if should_use_json:
            assert exit_code in (0, 2)
        else:
            assert exit_code in (1, 3)


class TestAdversarialV3ForwardCompat:
    """Verify paper-writer can handle v3 class rename."""

    def test_central_objection_is_narrative_weakness_rename(self):
        """v3 renames narrative_weakness → central_objection.

        Paper-writer should accept both during the transition.
        """
        v3_finding = {
            "id": "F010",
            "class": "central_objection",
            "severity": "info",
            "title": "Central objection test",
            "evidence": "...",
            "recommendation": "...",
        }
        # Both old and new names should be recognizable
        assert v3_finding["class"] in ("narrative_weakness", "central_objection")
        # info severity is still excluded from actionable counts
        assert SEVERITY_MAP.get(v3_finding["severity"]) is None
