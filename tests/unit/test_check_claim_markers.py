"""Stage 6 partial — unit tests for check_claim_markers.

Three layers:
  * Pure-function ``extract_markers_from_manuscript`` and
    ``run_marker_check``.
  * ``load_inventory_claim_ids`` against well-formed and malformed TSVs.
  * CLI ``main`` end-to-end against on-disk fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.check_claim_markers import (
    MARKER_RE,
    SCHEMA_VERSION,
    SEVERITY_UNRESOLVED,
    extract_markers_from_manuscript,
    load_inventory_claim_ids,
    main,
    run_marker_check,
)


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------


def test_regex_matches_canonical_format() -> None:
    """The canonical inventory format is [C-NNN]; the regex must match
    that exact shape and not collide with citation keys like
    [Lloyd-Price2019]."""
    assert MARKER_RE.findall("foo [C-001] bar") == ["C-001"]
    assert MARKER_RE.findall("foo [C-188] [C-342] bar") == ["C-188", "C-342"]
    # Should NOT match citation-like tokens.
    assert MARKER_RE.findall("see [Lloyd-Price2019]") == []
    assert MARKER_RE.findall("[NEEDS CITATION: foo]") == []
    # Should NOT match malformed marker tokens.
    assert MARKER_RE.findall("[C001]") == []          # missing hyphen
    assert MARKER_RE.findall("[c-001]") == []         # lowercase
    assert MARKER_RE.findall("[C-]") == []            # no digits


def test_regex_accepts_variable_digit_widths() -> None:
    """Inventory currently uses 3-digit zero-padded ids but the format
    is forward-compatible with larger inventories."""
    assert MARKER_RE.findall("[C-1] [C-12] [C-1234]") == [
        "C-1", "C-12", "C-1234",
    ]


# ---------------------------------------------------------------------------
# Manuscript walker
# ---------------------------------------------------------------------------


def test_walker_attributes_markers_to_section_and_paragraph() -> None:
    text = (
        "Some preface [C-100].\n"
        "\n"
        "## Results\n"
        "\n"
        "First paragraph [C-001].\n"
        "\n"
        "Second paragraph [C-002] and [C-003].\n"
        "\n"
        "## Discussion\n"
        "\n"
        "Disc paragraph [C-004].\n"
    )
    markers = extract_markers_from_manuscript(text)
    by_marker = {m[0]: m for m in markers}
    assert by_marker["C-100"][1] == "front-matter"
    assert by_marker["C-100"][2] == 1
    assert by_marker["C-001"][1] == "results"
    assert by_marker["C-001"][2] == 1
    assert by_marker["C-002"][1] == "results"
    assert by_marker["C-002"][2] == 2
    assert by_marker["C-003"][1] == "results"
    assert by_marker["C-003"][2] == 2
    assert by_marker["C-004"][1] == "discussion"
    assert by_marker["C-004"][2] == 1


def test_walker_captures_surrounding_context() -> None:
    text = "We saw 88.2% sign concordance [C-118] on the held-out cohort.\n"
    markers = extract_markers_from_manuscript(text)
    assert len(markers) == 1
    _, _, _, _, ctx = markers[0]
    assert "[C-118]" in ctx
    assert "88.2% sign concordance" in ctx


def test_walker_empty_manuscript_returns_empty() -> None:
    assert extract_markers_from_manuscript("") == []


def test_walker_canonicalises_heading_case_and_punctuation() -> None:
    text = "## Results:\n\nFoo [C-001].\n"
    markers = extract_markers_from_manuscript(text)
    assert markers[0][1] == "results"


# ---------------------------------------------------------------------------
# Inventory loader
# ---------------------------------------------------------------------------


def _write_inventory(path: Path, claim_ids: list[str]) -> None:
    """Write a well-formed claim_inventory.tsv with just claim_id values."""
    header = (
        "claim_id\tclaim_text\tsource_notebook\tsource_cell\t"
        "figure_or_table\teffect_size_present\tci_present\t"
        "pvalue_present\tnotes\n"
    )
    rows = [
        f"{cid}\tstub claim text\tNB.ipynb\t\tFigure X\tyes\tno\tno\t\n"
        for cid in claim_ids
    ]
    path.write_text(header + "".join(rows), encoding="utf-8")


def test_load_inventory_returns_claim_id_set(tmp_path: Path) -> None:
    inv = tmp_path / "claim_inventory.tsv"
    _write_inventory(inv, ["C-001", "C-002", "C-188"])
    assert load_inventory_claim_ids(inv) == {"C-001", "C-002", "C-188"}


def test_load_inventory_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_inventory_claim_ids(tmp_path / "nonexistent.tsv") == set()


def test_load_inventory_strips_whitespace(tmp_path: Path) -> None:
    """Leading/trailing whitespace in TSV cells shouldn't break id matching."""
    inv = tmp_path / "claim_inventory.tsv"
    inv.write_text(
        "claim_id\tclaim_text\n"
        " C-001 \tfoo\n"
        "C-002\tbar\n",
        encoding="utf-8",
    )
    assert load_inventory_claim_ids(inv) == {"C-001", "C-002"}


def test_load_inventory_skips_empty_claim_id_rows(tmp_path: Path) -> None:
    inv = tmp_path / "claim_inventory.tsv"
    inv.write_text(
        "claim_id\tclaim_text\n"
        "C-001\tfoo\n"
        "\tno_id_here\n"
        "C-003\tbar\n",
        encoding="utf-8",
    )
    assert load_inventory_claim_ids(inv) == {"C-001", "C-003"}


# ---------------------------------------------------------------------------
# Comparator (run_marker_check)
# ---------------------------------------------------------------------------


def test_run_marker_check_all_resolved() -> None:
    text = "## Results\n\nWe saw 88.2% [C-118] and 0.40 threshold [C-119].\n"
    inventory = {"C-118", "C-119", "C-300"}
    findings, totals = run_marker_check(text, inventory)
    assert findings == []
    assert totals["markers_in_manuscript"] == 2
    assert totals["unique_markers_in_manuscript"] == 2
    assert totals["cited_and_resolved"] == 2
    assert totals["cited_but_unresolved"] == 0
    assert totals["in_inventory_but_uncited"] == 1
    assert totals["inventory_size"] == 3


def test_run_marker_check_unresolved_emits_p1_finding() -> None:
    text = "We saw 999 things [C-999].\n"
    inventory = {"C-001", "C-002"}
    findings, totals = run_marker_check(text, inventory)
    assert len(findings) == 1
    assert findings[0].marker == "C-999"
    assert findings[0].severity == SEVERITY_UNRESOLVED == "P1"
    assert "C-999" in findings[0].rationale
    assert totals["cited_but_unresolved"] == 1
    assert totals["cited_and_resolved"] == 0


def test_run_marker_check_mixed_resolved_and_unresolved() -> None:
    text = "## Methods\n\n[C-001] and [C-999] and [C-002] and [C-998].\n"
    inventory = {"C-001", "C-002"}
    findings, totals = run_marker_check(text, inventory)
    assert {f.marker for f in findings} == {"C-999", "C-998"}
    assert all(f.severity == "P1" for f in findings)
    assert totals["cited_and_resolved"] == 2
    assert totals["cited_but_unresolved"] == 2
    assert totals["markers_in_manuscript"] == 4


def test_run_marker_check_repeated_marker_counts_once_in_unique() -> None:
    """If the same marker appears in multiple paragraphs, the unique
    count is 1 but markers_in_manuscript counts all occurrences."""
    text = "[C-001] foo.\n\n[C-001] bar.\n\n[C-001] baz.\n"
    inventory = {"C-001"}
    _, totals = run_marker_check(text, inventory)
    assert totals["markers_in_manuscript"] == 3
    assert totals["unique_markers_in_manuscript"] == 1
    assert totals["cited_and_resolved"] == 1


def test_run_marker_check_empty_inventory_flags_every_marker() -> None:
    """When the inventory is empty, every emitted marker is unresolved."""
    text = "[C-001] and [C-002].\n"
    inventory: set[str] = set()
    findings, totals = run_marker_check(text, inventory)
    assert len(findings) == 2
    assert totals["cited_but_unresolved"] == 2


def test_run_marker_check_empty_manuscript_no_findings() -> None:
    findings, totals = run_marker_check("", {"C-001", "C-002"})
    assert findings == []
    assert totals["markers_in_manuscript"] == 0
    assert totals["in_inventory_but_uncited"] == 2


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_writes_schema_versioned_json(tmp_path: Path) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text(
        "## Results\n\nFoo [C-001] bar [C-002].\n",
        encoding="utf-8",
    )
    inv = tmp_path / "claim_inventory.tsv"
    _write_inventory(inv, ["C-001", "C-002"])
    out = tmp_path / "audit" / "claim_marker_check.json"

    rc = main([
        "--manuscript", str(manuscript),
        "--inventory", str(inv),
        "--out", str(out),
    ])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["totals"]["cited_and_resolved"] == 2
    assert payload["totals"]["cited_but_unresolved"] == 0
    assert payload["findings"] == []


def test_cli_exit_code_2_when_unresolved(tmp_path: Path) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("[C-999] hallucinated.\n", encoding="utf-8")
    inv = tmp_path / "claim_inventory.tsv"
    _write_inventory(inv, ["C-001"])
    out = tmp_path / "audit" / "claim_marker_check.json"
    rc = main([
        "--manuscript", str(manuscript),
        "--inventory", str(inv),
        "--out", str(out),
    ])
    assert rc == 2


def test_cli_handles_missing_inventory_gracefully(tmp_path: Path) -> None:
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("[C-001] foo.\n", encoding="utf-8")
    out = tmp_path / "audit" / "claim_marker_check.json"
    rc = main([
        "--manuscript", str(manuscript),
        "--inventory", str(tmp_path / "nonexistent.tsv"),
        "--out", str(out),
    ])
    # Exit 2: marker exists but inventory is empty → unresolved.
    assert rc == 2
    payload = json.loads(out.read_text())
    assert payload["inventory_path"] is None
    assert any("missing" in n.lower() for n in payload["notes"])
    assert payload["totals"]["cited_but_unresolved"] == 1


def test_cli_missing_manuscript_returns_1(tmp_path: Path) -> None:
    rc = main([
        "--manuscript", str(tmp_path / "ghost.md"),
        "--inventory", str(tmp_path / "ghost.tsv"),
        "--out", str(tmp_path / "out.json"),
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_draft(tmp_path: Path) -> Path:
    """Minimal BERDL-like project with a draft_1 manuscript + inventory."""
    proj = tmp_path / "test_project"
    (proj / "papers" / "draft_1").mkdir(parents=True)
    (proj / "REPORT.md").write_text("# stub\n", encoding="utf-8")
    return proj


def test_orchestrator_run_claim_marker_check_writes_audit_json(
    project_with_draft: Path,
) -> None:
    """The orchestrator helper writes audit/claim_marker_check.json with
    the v1 schema regardless of manuscript content."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_draft / "papers" / "draft_1"
    (draft / "manuscript.md").write_text(
        "## Results\n\nWe saw [C-001] and [C-002].\n",
        encoding="utf-8",
    )
    inv = draft / "claim_inventory.tsv"
    _write_inventory(inv, ["C-001", "C-002", "C-003"])

    orch = PaperWriterOrchestrator(draft_dir=draft)
    orch._run_claim_marker_check()

    audit_json = draft / "audit" / "claim_marker_check.json"
    assert audit_json.is_file()
    payload = json.loads(audit_json.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["totals"]["cited_and_resolved"] == 2
    assert payload["totals"]["cited_but_unresolved"] == 0
    assert payload["totals"]["in_inventory_but_uncited"] == 1


def test_orchestrator_run_claim_marker_check_flags_unresolved(
    project_with_draft: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unresolved markers must surface in the audit JSON AND log
    WARNING with up to 5 inline rationales so the operator notices."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_draft / "papers" / "draft_1"
    (draft / "manuscript.md").write_text(
        "[C-001] is real but [C-999] is fabricated.\n",
        encoding="utf-8",
    )
    inv = draft / "claim_inventory.tsv"
    _write_inventory(inv, ["C-001"])

    orch = PaperWriterOrchestrator(draft_dir=draft)
    with caplog.at_level("WARNING", logger="orchestrator"):
        orch._run_claim_marker_check()

    audit_json = draft / "audit" / "claim_marker_check.json"
    payload = json.loads(audit_json.read_text())
    assert payload["totals"]["cited_but_unresolved"] == 1
    assert payload["findings"][0]["marker"] == "C-999"
    msgs = " ".join(r.message for r in caplog.records)
    assert "UNRESOLVED" in msgs
    assert "C-999" in msgs


def test_orchestrator_run_claim_marker_check_skips_when_manuscript_missing(
    project_with_draft: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When manuscript.md is absent, the helper logs WARNING and
    returns without writing audit/claim_marker_check.json. (This
    happens if phase_review runs before phase_drafting completed.)"""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_draft / "papers" / "draft_1"
    # Note: no manuscript.md written.

    orch = PaperWriterOrchestrator(draft_dir=draft)
    with caplog.at_level("WARNING", logger="orchestrator"):
        orch._run_claim_marker_check()

    assert not (draft / "audit" / "claim_marker_check.json").exists()
    msgs = " ".join(r.message for r in caplog.records)
    assert "manuscript.md missing" in msgs
