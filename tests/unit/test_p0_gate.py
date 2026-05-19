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


# ---------------------------------------------------------------------------
# Stage 7 Patch 3 (2026-05-18) — false-positive filtering
#
# Two filter rules in v1:
#   1. citation_reality + [NEEDS CITATION: in description/quote → demote
#   2. missing_section + Data/Code Availability text → demote
# Demoted findings stay in P0Summary.demoted_findings (audit trail
# preserved) but don't count toward summary.total (the gate signal).
# ---------------------------------------------------------------------------


def test_filter_demotes_needs_citation_placeholder_in_description(
    tmp_path: Path,
) -> None:
    """The headline D1 failure: adversarial flags `[NEEDS CITATION:...]`
    placeholders as P0 citation_reality. These are intentional pre-
    supplementary-pool markers — the gate must demote them."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001",
            "class": "citation_reality",
            "severity": "P0",
            "issue": (
                "Literal unresolved citation placeholder "
                "'[NEEDS CITATION: minimal Mycoplasma genome design]' "
                "appears in the manuscript text."
            ),
        },
        {
            "id": "F002",
            "class": "citation_reality",
            "severity": "P0",
            "issue": "Real fabrication issue without the marker",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1  # only F002 counts
    assert len(summary.demoted_findings) == 1
    assert summary.demoted_findings[0].finding_id == "F001"
    assert (
        summary.demoted_findings[0].filter_reason
        == "needs-citation-placeholder"
    )


def test_filter_demotes_needs_citation_placeholder_in_quote(
    tmp_path: Path,
) -> None:
    """The marker may live in paragraph_quote instead of issue text.
    Both fields are checked."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001",
            "class": "citation_reality",
            "severity": "P0",
            "issue": "Citation flagged.",
            "paragraph_quote": "...the EcoActive phage cocktail [NEEDS CITATION: EcoActive AIEC clinical-trial cocktail]...",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert len(summary.demoted_findings) == 1


def test_filter_demotes_missing_data_availability_section(
    tmp_path: Path,
) -> None:
    """compliance_gate writes Data Availability post-gate. Adversarial
    findings flagging the section as missing AT GATE TIME must be
    demoted — compliance_gate will autofix."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001",
            "class": "missing_section",
            "severity": "P0",
            "issue": (
                "The manuscript contains no Data Availability "
                "statement and no Code Availability statement."
            ),
            "fix_target": "Data Availability",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 0
    assert len(summary.demoted_findings) == 1
    assert (
        summary.demoted_findings[0].filter_reason
        == "pre-compliance-missing-section"
    )


def test_filter_does_not_demote_other_missing_section_findings(
    tmp_path: Path,
) -> None:
    """missing_section findings about other sections (e.g., Limitations,
    Methods) must NOT be filtered — compliance_gate doesn't write those."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001",
            "class": "missing_section",
            "severity": "P0",
            "issue": "The Limitations subsection is absent.",
            "fix_target": "Limitations",
        },
        {
            "id": "F002",
            "class": "missing_section",
            "severity": "P0",
            "issue": "Methods has no statistical-tests subsection.",
            "fix_target": "Methods §statistics",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 2  # both stay
    assert len(summary.demoted_findings) == 0


def test_filter_records_notes_summarising_demotions(
    tmp_path: Path,
) -> None:
    """The summary.notes must include `filter_applied:` lines documenting
    what was demoted so the operator sees the audit trail in
    p0_findings.md."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001", "class": "citation_reality", "severity": "P0",
            "issue": "[NEEDS CITATION: foo]",
        },
        {
            "id": "F002", "class": "missing_section", "severity": "P0",
            "issue": "Missing Data Availability statement.",
        },
    ])
    summary = count_p0_findings(audit)
    notes_text = " ".join(summary.notes)
    assert "filter_applied" in notes_text
    assert "citation_reality" in notes_text
    assert "missing_section" in notes_text


def test_filter_to_dict_preserves_filter_reason(tmp_path: Path) -> None:
    """to_dict() on a demoted finding must include filter_reason so
    the audit JSON is round-trippable and downstream consumers (e.g.,
    a future v1.1 reverse-promotion check) can detect filtered items."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001", "class": "citation_reality", "severity": "P0",
            "issue": "[NEEDS CITATION: x]",
        },
    ])
    summary = count_p0_findings(audit)
    raw = summary.to_dict()
    assert raw["total"] == 0
    assert len(raw["demoted_findings"]) == 1
    assert raw["demoted_findings"][0]["filter_reason"] == "needs-citation-placeholder"
    # Non-demoted findings don't carry the field.
    assert raw["findings"] == []


def test_filter_per_source_and_per_class_count_kept_only(
    tmp_path: Path,
) -> None:
    """per_source and per_class must reflect only non-demoted findings
    since they drive operator-facing decisions."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {  # Demoted
            "id": "F001", "class": "citation_reality", "severity": "P0",
            "issue": "[NEEDS CITATION: foo]",
        },
        {  # Demoted
            "id": "F002", "class": "missing_section", "severity": "P0",
            "issue": "Data Availability statement missing.",
        },
        {  # Kept
            "id": "F003", "class": "register_drift", "severity": "P0",
            "issue": "informal language",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    assert summary.per_source == {SOURCE_ADVERSARIAL: 1}
    assert summary.per_class == {"register_drift": 1}


def test_filter_does_not_affect_numeric_grounding_findings(
    tmp_path: Path,
) -> None:
    """Filters are adversarial-only — numeric grounding findings are
    deterministic and must always count."""
    audit = tmp_path / "audit"
    _write_numeric(audit, [
        {
            "matched_text": "70%", "match_class": "percentage",
            "section": "Results", "paragraph": 1, "severity": "P0",
            "rationale": "fabrication",
        },
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 1
    assert summary.per_source == {SOURCE_NUMERIC: 1}
    assert len(summary.demoted_findings) == 0


def test_filter_handles_mixed_real_and_false_positives_from_d1(
    tmp_path: Path,
) -> None:
    """Regression test based on the real D1 audit shape: 4 adversarial
    P0s (1 NEEDS-CITATION false-positive + 1 Data-Availability false-
    positive + 2 real report_drifts) + 5 numeric P0s. Expected post-
    filter: 2 adversarial + 5 numeric = 7 kept, 2 demoted."""
    audit = tmp_path / "audit"
    _write_adversarial(audit, [
        {
            "id": "F001", "class": "citation_reality", "severity": "P0",
            "issue": "Literal '[NEEDS CITATION: minimal genome]' appears in text",
        },
        {
            "id": "F002", "class": "missing_section", "severity": "P0",
            "issue": "No Data Availability or Code Availability statement.",
            "fix_target": "Data Availability",
        },
        {
            "id": "F003", "class": "report_drift", "severity": "P0",
            "issue": "Throughline says 58.1% but REPORT says 44.7%.",
        },
        {
            "id": "F004", "class": "report_drift", "severity": "P0",
            "issue": "reframing_log.md does not exist.",
        },
    ])
    _write_numeric(audit, [
        {"matched_text": "27,693 of 148,826", "match_class": "count_of",
         "section": "Methods", "paragraph": 7, "severity": "P0", "rationale": "ungrounded"},
        {"matched_text": "12.9%", "match_class": "percentage",
         "section": "Methods", "paragraph": 7, "severity": "P0", "rationale": "ungrounded"},
        {"matched_text": "2 x", "match_class": "ratio_with_unit",
         "section": "Methods", "paragraph": 11, "severity": "P0", "rationale": "ungrounded"},
        {"matched_text": "12.9%", "match_class": "percentage",
         "section": "Results", "paragraph": 9, "severity": "P0", "rationale": "ungrounded"},
        {"matched_text": "33 of 48", "match_class": "count_of",
         "section": "Discussion", "paragraph": 7, "severity": "P0", "rationale": "ungrounded"},
    ])
    summary = count_p0_findings(audit)
    assert summary.total == 7
    assert len(summary.demoted_findings) == 2
    # Real ones kept.
    kept_ids = {f.finding_id for f in summary.findings if f.source == SOURCE_ADVERSARIAL}
    assert kept_ids == {"F003", "F004"}
    # Numeric all kept.
    assert summary.per_source[SOURCE_NUMERIC] == 5


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
