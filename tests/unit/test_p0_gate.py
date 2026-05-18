"""Stage 4 Tier S — pure-function tests for skill.tools.p0_gate.

The gate's counter + renderer are pure; everything in orchestrator
land that touches them (phase_p0_review's dispatch logic, manual-edit
detection, remediation cycle bookkeeping) lives in a separate test
file (test_orchestrator_stage4_tier_s.py). This file exercises the
data layer only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.p0_gate import (
    P0Finding,
    P0Summary,
    SEVERITY_P0,
    SOURCE_ADVERSARIAL,
    SOURCE_NUMERIC,
    count_p0_findings,
    render_p0_findings_md,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_adversarial(audit_dir: Path, findings: list[dict]) -> None:
    payload = {
        "schema_version": "adversarial-review-paper.v3",
        "draft_dir": str(audit_dir.parent),
        "summary": {
            "total_findings": len(findings),
            "by_severity": {},
            "by_class": {},
        },
        "findings": findings,
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "adversarial_review.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


def _write_numeric(audit_dir: Path, findings: list[dict]) -> None:
    payload = {
        "schema_version": "v1",
        "tool": "check_numeric_grounding",
        "tool_version": "0.1.0",
        "draft_dir": str(audit_dir.parent),
        "manuscript_path": str(audit_dir.parent / "manuscript.md"),
        "inventory_path": str(audit_dir.parent / "claim_inventory.tsv"),
        "report_path": str(audit_dir.parent.parent.parent / "REPORT.md"),
        "totals": {
            "numeric_matches_in_manuscript": len(findings),
            "grounded_tier_a_inventory": 0,
            "grounded_tier_b_report_md": 0,
            "allowlisted": 0,
            "ungrounded": len(findings),
        },
        "findings": findings,
        "allowlisted": [],
        "notes": [],
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "numeric_grounding.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# count_p0_findings: empty-input degenerate cases
# ---------------------------------------------------------------------------


def test_count_returns_zero_when_audit_dir_missing(tmp_path: Path) -> None:
    """A non-existent audit_dir must not raise; it returns zero with
    notes recording the two missing JSON files."""
    summary = count_p0_findings(tmp_path / "does_not_exist")
    assert summary.total == 0
    assert summary.findings == []
    assert any("adversarial_review.json" in n for n in summary.notes)
    assert any("numeric_grounding.json" in n for n in summary.notes)


def test_count_returns_zero_when_audit_dir_empty(tmp_path: Path) -> None:
    """An empty audit_dir is the legitimate state before phase_review
    has ever run; the counter must return zero without raising."""
    audit = tmp_path / "audit"
    audit.mkdir()
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert len(summary.notes) == 2  # one per missing producer


# ---------------------------------------------------------------------------
# count_p0_findings: adversarial side
# ---------------------------------------------------------------------------


def test_count_picks_only_p0_from_adversarial(tmp_path: Path) -> None:
    """A mix of P0/P1/P2/info findings must collapse to only P0s."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "missing citation", "fix_hint": "add ref",
         "fix_target": "Methods", "section_label": "Methods",
         "paragraph_quote": "..."},
        {"id": "F002", "class": "register_drift", "severity": "P1",
         "issue": "informal", "fix_hint": "tighten"},
        {"id": "F003", "class": "claim_evidence", "severity": "P2",
         "issue": "weak claim", "fix_hint": "..."},
        {"id": "F004", "class": "info", "severity": "info",
         "issue": "fyi"},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    assert summary.findings[0].finding_id == "F001"
    assert summary.findings[0].source == SOURCE_ADVERSARIAL
    assert summary.findings[0].finding_class == "citation_reality"
    assert summary.findings[0].fix_target == "Methods"


def test_count_handles_missing_adversarial_optional_fields(
    tmp_path: Path,
) -> None:
    """Adversarial findings can omit section_label / paragraph_quote;
    the counter must default cleanly rather than raise."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {"id": "F001", "class": "unbacked_quantitative", "severity": "P0",
         "issue": "CI not in REPORT"},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    f = summary.findings[0]
    assert f.location == "(section unspecified)"
    assert f.fix_target == ""
    assert f.fix_hint == ""
    assert f.quote == ""


def test_count_skips_malformed_adversarial_entries(tmp_path: Path) -> None:
    """A non-dict entry in the findings array must be skipped silently
    (don't let one malformed entry kill the whole gate)."""
    audit = tmp_path / "audit"
    payload = {
        "schema_version": "adversarial-review-paper.v3",
        "findings": [
            "not-a-dict",
            None,
            {"id": "F002", "class": "citation_reality", "severity": "P0",
             "issue": "x"},
        ],
    }
    audit.mkdir()
    (audit / "adversarial_review.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    summary = count_p0_findings(audit)
    assert summary.total == 1
    assert summary.findings[0].finding_id == "F002"


# ---------------------------------------------------------------------------
# count_p0_findings: numeric side
# ---------------------------------------------------------------------------


def test_count_picks_only_p0_from_numeric(tmp_path: Path) -> None:
    """Numeric grounding emits P0 in strict mode; non-P0 must still be
    rejected if a future schema introduces P1 soft-warnings."""
    audit = tmp_path / "audit"
    _write_numeric(audit, [
        {"claim_text": "We saw 105 of 137 samples.",
         "matched_text": "105 of 137",
         "normalized_value": "105/137",
         "match_class": "count_of",
         "section": "Results", "paragraph": 30, "char_offset": 12345,
         "severity": "P0",
         "rationale": "Neither 105 nor 137 appears in REPORT or inventory."},
        {"claim_text": "We saw 50 things.",
         "matched_text": "50",
         "normalized_value": "50",
         "match_class": "n_count",
         "section": "Results", "paragraph": 12, "char_offset": 99,
         "severity": "P1",  # hypothetical future soft-warning
         "rationale": "..."},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    f = summary.findings[0]
    assert f.source == SOURCE_NUMERIC
    assert f.finding_class == "count_of"
    assert f.location == "Results para 30"
    assert f.finding_id == "NG-012345"  # char_offset embedded


def test_count_numeric_synthesises_id_when_offset_missing(
    tmp_path: Path,
) -> None:
    """If a numeric finding lacks char_offset, the counter synthesises
    a positional id so the renderer has something stable to display."""
    audit = tmp_path / "audit"
    _write_numeric(audit, [
        {"matched_text": "42", "match_class": "n_count",
         "section": "Results", "paragraph": 1, "severity": "P0",
         "rationale": "no offset"},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    assert summary.findings[0].finding_id.startswith("NG-")


# ---------------------------------------------------------------------------
# count_p0_findings: combined sides
# ---------------------------------------------------------------------------


def test_count_combines_both_sources(tmp_path: Path) -> None:
    """Total + per_source must add up across both producers."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
        {"id": "F002", "class": "report_drift", "severity": "P0",
         "issue": "y"},
        {"id": "F003", "class": "register_drift", "severity": "P1",
         "issue": "z"},
    ])
    _write_numeric(audit, [
        {"matched_text": "42", "match_class": "n_count",
         "section": "Results", "paragraph": 1, "severity": "P0",
         "rationale": "stray"},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 3
    assert summary.per_source == {
        SOURCE_ADVERSARIAL: 2, SOURCE_NUMERIC: 1,
    }
    assert summary.per_class == {
        "citation_reality": 1, "report_drift": 1, "n_count": 1,
    }


# ---------------------------------------------------------------------------
# count_p0_findings: degraded inputs
# ---------------------------------------------------------------------------


def test_count_records_note_on_malformed_json(tmp_path: Path) -> None:
    """A JSON file that doesn't parse should add a note, not raise."""
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "adversarial_review.json").write_text(
        "{ this is not json", encoding="utf-8",
    )
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert any("malformed" in n.lower() for n in summary.notes)


def test_count_handles_payload_not_dict(tmp_path: Path) -> None:
    """A JSON top-level list (instead of dict) shouldn't raise."""
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "adversarial_review.json").write_text(
        "[1, 2, 3]", encoding="utf-8",
    )
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert any("not a dict" in n for n in summary.notes)


def test_count_handles_missing_findings_array(tmp_path: Path) -> None:
    """A dict payload without a 'findings' field shouldn't raise."""
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "adversarial_review.json").write_text(
        json.dumps({"schema_version": "v3", "summary": {}}),
        encoding="utf-8",
    )
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert any("findings" in n for n in summary.notes)


# ---------------------------------------------------------------------------
# render_p0_findings_md
# ---------------------------------------------------------------------------


def _summary_with_one_finding() -> P0Summary:
    return P0Summary(
        total=1,
        per_source={SOURCE_ADVERSARIAL: 1},
        per_class={"citation_reality": 1},
        findings=[
            P0Finding(
                source=SOURCE_ADVERSARIAL,
                finding_id="F001",
                finding_class="citation_reality",
                severity=SEVERITY_P0,
                location="Methods",
                description="missing citation for X",
                fix_target="references.md",
                fix_hint="resolve via WebSearch",
                quote="some prose claiming X",
            ),
        ],
    )


def test_render_includes_summary_counts() -> None:
    s = _summary_with_one_finding()
    md = render_p0_findings_md(
        s, draft_dir=Path("/tmp/x"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    assert "Total P0 findings:** 1" in md
    assert "By source:** adversarial=1" in md
    assert "By class:** citation_reality=1" in md
    assert "0 / 2" in md  # cycles_used / max_cycles


def test_render_offers_three_proceed_options_normal_mode() -> None:
    """Normal pause: remediate / ship-with-p0s / manual-edit."""
    md = render_p0_findings_md(
        _summary_with_one_finding(),
        draft_dir=Path("/tmp/draft_3"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    assert "--remediate" in md
    assert "--ship-with-p0s" in md
    assert "continue /tmp/draft_3" in md
    # Manual-edit hint references the manuscript path.
    assert "/tmp/draft_3/manuscript.md" in md


def test_render_reframes_proceed_options_when_cycles_exhausted() -> None:
    """When cycles_exhausted: drop the bare '--remediate' option;
    surface raise-the-cap and ship-with-p0s and manual-edit."""
    md = render_p0_findings_md(
        _summary_with_one_finding(),
        draft_dir=Path("/tmp/draft_3"),
        cycles_used=2, max_cycles=2, cycles_exhausted=True,
    )
    assert "Cycle cap exhausted" in md
    # The "raise the cap and try again" option shows the new ceiling.
    assert "--max-remediate-cycles 4" in md
    assert "--ship-with-p0s" in md


def test_render_lists_findings_with_stable_ordering() -> None:
    """Adversarial findings come before numeric_grounding, and within
    each source the ordering is by finding_id."""
    s = P0Summary(
        total=3,
        per_source={SOURCE_ADVERSARIAL: 2, SOURCE_NUMERIC: 1},
        findings=[
            P0Finding(
                source=SOURCE_NUMERIC, finding_id="NG-000999",
                finding_class="count_of", severity="P0",
                location="Results para 1", description="...",
                fix_target="", fix_hint="...", quote="",
            ),
            P0Finding(
                source=SOURCE_ADVERSARIAL, finding_id="F002",
                finding_class="report_drift", severity="P0",
                location="Discussion", description="...",
                fix_target="", fix_hint="...", quote="",
            ),
            P0Finding(
                source=SOURCE_ADVERSARIAL, finding_id="F001",
                finding_class="citation_reality", severity="P0",
                location="Methods", description="...",
                fix_target="", fix_hint="...", quote="",
            ),
        ],
    )
    md = render_p0_findings_md(
        s, draft_dir=Path("/tmp/x"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    # The order in the rendered Findings section.
    f1_idx = md.index("### F001 ")
    f2_idx = md.index("### F002 ")
    ng_idx = md.index("### NG-000999 ")
    assert f1_idx < f2_idx < ng_idx


def test_render_truncates_long_strings() -> None:
    """A wall-of-text description must be trimmed so the markdown file
    stays inspectable in a terminal."""
    long_issue = "A" * 2000
    s = P0Summary(
        total=1,
        per_source={SOURCE_ADVERSARIAL: 1},
        findings=[
            P0Finding(
                source=SOURCE_ADVERSARIAL, finding_id="F001",
                finding_class="citation_reality", severity="P0",
                location="Methods", description=long_issue,
                fix_target="", fix_hint="", quote="",
            ),
        ],
    )
    md = render_p0_findings_md(
        s, draft_dir=Path("/tmp/x"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    assert "[trunc]" in md
    # Body length cap: well under raw 2000.
    assert "A" * 1500 not in md


def test_render_zero_findings_states_no_p0s() -> None:
    """If somehow the renderer is invoked with total=0, it should say
    so explicitly rather than print the proceed instructions."""
    s = P0Summary(total=0)
    md = render_p0_findings_md(
        s, draft_dir=Path("/tmp/x"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    assert "no p0 findings" in md.lower()
    # The three proceed options should NOT be present.
    assert "--remediate" not in md
    assert "--ship-with-p0s" not in md


def test_render_surfaces_telemetry_notes() -> None:
    """When the gate ran in degraded mode, the markdown surfaces the
    notes so the operator can audit why."""
    s = P0Summary(
        total=1,
        per_source={SOURCE_ADVERSARIAL: 1},
        findings=[
            P0Finding(
                source=SOURCE_ADVERSARIAL, finding_id="F001",
                finding_class="x", severity="P0",
                location="X", description="x",
                fix_target="", fix_hint="", quote="",
            ),
        ],
        notes=["numeric_grounding.json not found at /tmp/x"],
    )
    md = render_p0_findings_md(
        s, draft_dir=Path("/tmp/x"),
        cycles_used=0, max_cycles=2, cycles_exhausted=False,
    )
    assert "Telemetry notes" in md
    assert "numeric_grounding.json not found" in md
