"""Stage 7 Patch 1b — tests for check_throughline_numerics.

Three layers:
  * parse_candidates: extract Candidate TLN blocks from
    throughline_candidates.md.
  * extract_numerics_from_candidate: numeric token walk + allowlist.
  * run_throughline_check: end-to-end against synthetic sources.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.check_throughline_numerics import (
    SCHEMA_VERSION,
    SEVERITY_P0,
    CandidateBlock,
    extract_numerics_from_candidate,
    main,
    parse_candidates,
    run_throughline_check,
)


# ---------------------------------------------------------------------------
# parse_candidates
# ---------------------------------------------------------------------------


class TestParseCandidates:

    def test_parses_three_canonical_candidates(self):
        text = (
            "# Throughline candidates\n\n"
            "## Candidate TL1: First option\n\n"
            "Body of TL1.\n\n"
            "## Candidate TL2: Second option\n\n"
            "Body of TL2.\n\n"
            "## Candidate TL3: Third option\n\n"
            "Body of TL3.\n"
        )
        blocks = parse_candidates(text)
        assert [b.candidate_id for b in blocks] == ["TL1", "TL2", "TL3"]

    def test_parses_narrowed_candidate(self):
        """plan.v1 may emit a TL-NARROWED block when triage demands."""
        text = (
            "## Candidate TL1: Foo\n\nBody.\n\n"
            "## Candidate TL-NARROWED: Bar\n\nBody.\n"
        )
        blocks = parse_candidates(text)
        assert "TL-NARROWED" in [b.candidate_id for b in blocks]

    def test_returns_empty_when_no_headers(self):
        """A file with no `## Candidate TLN:` headers (e.g. an unrelated
        markdown) yields no blocks — the validator silently skips."""
        text = "# Some other document\n\nNo candidate headers here.\n"
        assert parse_candidates(text) == []

    def test_block_body_includes_header(self):
        """The CandidateBlock.body must contain the full block including
        its header — needed for context-window construction."""
        text = "## Candidate TL1: My statement\n\nThe rest.\n"
        blocks = parse_candidates(text)
        assert blocks[0].body.startswith("## Candidate TL1: My statement")
        assert "The rest." in blocks[0].body


# ---------------------------------------------------------------------------
# Numeric extraction with allowlist
# ---------------------------------------------------------------------------


class TestExtractNumericsAllowlist:

    def test_extracts_simple_numerics(self):
        body = "## Candidate TL1: Foo\n\nEssential genes are 86.1% core.\n"
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        assert "86.1" in nums

    def test_allowlists_candidate_id_in_header(self):
        """'TL1' must not produce a '1' finding."""
        body = (
            "## Candidate TL1: A simple statement.\n\n"
            "No other numerics here.\n"
        )
        results = extract_numerics_from_candidate(body)
        # No findings — the '1' inside 'TL1' is allowlisted.
        assert results == []

    def test_allowlists_section_phase_tier_references(self):
        """Standard structural references — Phase 2, Tier 3, §4.5,
        Pillar 4, Equation 1 — are structural, not data claims."""
        body = (
            "## Candidate TL1: Foo\n\n"
            "Per Phase 2 and Tier 3 and §4.5 and Pillar 4 of the "
            "framework, Equation 1 holds.\n"
        )
        results = extract_numerics_from_candidate(body)
        assert results == []

    def test_allowlists_citation_keys(self):
        """[Lloyd-Price2019] and similar must not surface '2019' as a
        claim — those are citation-shape tokens."""
        body = (
            "## Candidate TL1: Foo\n\n"
            "Per [Lloyd-Price2019] and [Smith2024], the data shows...\n"
        )
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        # Years inside citation keys are allowlisted.
        assert "2019" not in nums
        assert "2024" not in nums

    def test_allowlists_notebook_and_cell_refs(self):
        body = (
            "## Candidate TL1: Foo\n\n"
            "Per notebook 04_essential_conservation.ipynb cell 6 and "
            "NB10a results...\n"
        )
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        assert "04" not in nums
        assert "6" not in nums

    def test_allowlists_standalone_years(self):
        body = (
            "## Candidate TL1: Foo\n\n"
            "Published in 2024 and updated in 2026.\n"
        )
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        assert "2024" not in nums
        assert "2026" not in nums

    def test_strips_thousand_separator_commas_in_compound_numbers(self):
        """Patch 1b follow-up: '177,863' must be tokenised as one
        number '177863', not as two ('177' + '863'). Without this,
        the source side's comma-stripped normalisation ('177863' in
        REPORT) wouldn't match the candidate side's '177' and '863'
        — every compound number in the candidates would be a
        false-positive ungrounded finding."""
        body = (
            "## Candidate TL1: Statement.\n\n"
            "177,863 gene-to-cluster links across the cohort.\n"
        )
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        # The compound number should appear as a single token.
        assert "177863" in nums
        # And the spurious split parts must NOT appear.
        assert "177" not in nums
        assert "863" not in nums

    def test_strips_multiple_comma_groups(self):
        """Bigger numbers with multiple comma groups (e.g., 1,234,567)
        must collapse to a single token."""
        body = "## Candidate TL1: Foo.\n\nWe processed 1,234,567 records.\n"
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        assert "1234567" in nums
        # No false-positive 1/234/567 splits.
        assert "234" not in nums
        assert "567" not in nums

    def test_allowlists_statistical_thresholds(self):
        """'q < 0.05', 'alpha = 0.10', 'p < 0.001' are statistical
        constants, not data claims."""
        body = (
            "## Candidate TL1: Foo\n\n"
            "Significant at q < 0.05 and alpha = 0.10.\n"
        )
        results = extract_numerics_from_candidate(body)
        nums = [r[0] for r in results]
        # Allowlist consumes 0.05 and 0.10 in this shape.
        # The "<" / "=" operator-adjacent matching catches both.
        # If the allowlist doesn't catch ".10" (e.g., bare value),
        # we accept "0.10" being flagged as a numeric — that's a
        # known small gap in v1.
        # Hard requirement: 0.05 in the q-threshold form is suppressed.
        assert "0.05" not in nums


# ---------------------------------------------------------------------------
# run_throughline_check — the comparator
# ---------------------------------------------------------------------------


class TestRunThroughlineCheck:

    def test_catches_fabricated_number_not_in_sources(self):
        """The headline D1 case: 58.1% in the candidate, but REPORT
        says 44.7%. The validator must flag 58.1 as ungrounded."""
        candidates_text = (
            "## Candidate TL1: Statement.\n\n"
            "Essential-unmapped genes are 58.1% hypothetical.\n"
        )
        report_norm = {"44.7", "1259", "18.2"}
        inventory_norm: set[str] = set()
        findings, totals = run_throughline_check(
            candidates_text, report_norm, inventory_norm,
        )
        flagged = {f.numeric for f in findings}
        assert "58.1" in flagged
        assert totals["ungrounded"] >= 1
        # All findings are P0 in strict mode.
        assert all(f.severity == SEVERITY_P0 for f in findings)

    def test_grounds_against_report_alone(self):
        candidates_text = (
            "## Candidate TL1: Statement.\n\n"
            "We saw 86.1% core enrichment vs 81.2% baseline.\n"
        )
        report_norm = {"86.1", "81.2"}
        findings, totals = run_throughline_check(
            candidates_text, report_norm, set(),
        )
        assert totals["ungrounded"] == 0

    def test_grounds_against_inventory(self):
        """Numbers absent from REPORT but present in claim_inventory
        must still ground."""
        candidates_text = (
            "## Candidate TL1: Statement.\n\n"
            "Per-organism median is 1.56.\n"
        )
        report_norm: set[str] = set()
        inventory_norm = {"1.56"}
        findings, totals = run_throughline_check(
            candidates_text, report_norm, inventory_norm,
        )
        assert totals["ungrounded"] == 0

    def test_counts_per_candidate(self):
        """Multi-candidate totals must be broken down by candidate ID."""
        candidates_text = (
            "## Candidate TL1: Statement.\n\nValue 99.9 appears.\n\n"
            "## Candidate TL2: Statement.\n\nValue 88.8 and 77.7 appear.\n"
        )
        findings, totals = run_throughline_check(
            candidates_text, set(), set(),
        )
        assert totals["ungrounded_by_candidate"]["TL1"] == 1
        assert totals["ungrounded_by_candidate"]["TL2"] == 2

    def test_no_candidates_yields_empty_totals(self):
        findings, totals = run_throughline_check("", {"1.0"}, set())
        assert totals["candidates_parsed"] == 0
        assert totals["numerics_in_candidates"] == 0
        assert totals["ungrounded"] == 0
        assert findings == []

    def test_finding_carries_candidate_id_and_context(self):
        """Each finding must include the candidate it came from + a
        readable surrounding context window so the operator can
        eyeball it without opening the source file."""
        candidates_text = (
            "## Candidate TL1: Statement.\n\n"
            "Per-organism essentiality rates ranged from 12.9% to 28.9%.\n"
        )
        findings, _ = run_throughline_check(candidates_text, set(), set())
        assert all(f.candidate_id == "TL1" for f in findings)
        assert all("12.9" in f.surrounding or "28.9" in f.surrounding
                   for f in findings if f.numeric in {"12.9", "28.9"})


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def _write_throughline(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _write_report(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


class TestCLI:

    def test_cli_writes_schema_versioned_json(self, tmp_path: Path):
        cand = tmp_path / "candidates.md"
        report = tmp_path / "REPORT.md"
        out = tmp_path / "audit" / "throughline_numeric_check.json"

        _write_throughline(cand, (
            "## Candidate TL1: Foo.\n\n"
            "We saw 86.1% core vs 81.2% baseline.\n"
        ))
        _write_report(report, "Essential genes are 86.1% core vs 81.2% non-essential.")

        rc = main([
            "--candidates", str(cand),
            "--report", str(report),
            "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text())
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["totals"]["ungrounded"] == 0

    def test_cli_exit_code_2_when_ungrounded(self, tmp_path: Path):
        cand = tmp_path / "candidates.md"
        report = tmp_path / "REPORT.md"
        out = tmp_path / "audit" / "throughline_numeric_check.json"

        # 58.1 in candidate, not in REPORT — fabrication.
        _write_throughline(cand, (
            "## Candidate TL1: Statement.\n\n"
            "Essential-unmapped genes are 58.1% hypothetical.\n"
        ))
        _write_report(report, "Essential-unmapped genes are 44.7% hypothetical.")

        rc = main([
            "--candidates", str(cand),
            "--report", str(report),
            "--out", str(out),
        ])
        assert rc == 2
        payload = json.loads(out.read_text())
        assert payload["totals"]["ungrounded"] >= 1
        flagged = {f["numeric"] for f in payload["findings"]}
        assert "58.1" in flagged

    def test_cli_missing_candidates_returns_1(self, tmp_path: Path):
        report = tmp_path / "REPORT.md"
        _write_report(report, "stub")
        rc = main([
            "--candidates", str(tmp_path / "ghost.md"),
            "--report", str(report),
            "--out", str(tmp_path / "out.json"),
        ])
        assert rc == 1

    def test_cli_missing_report_returns_1(self, tmp_path: Path):
        cand = tmp_path / "candidates.md"
        _write_throughline(cand, "## Candidate TL1: Foo.\n\nBody.\n")
        rc = main([
            "--candidates", str(cand),
            "--report", str(tmp_path / "ghost.md"),
            "--out", str(tmp_path / "out.json"),
        ])
        assert rc == 1

    def test_cli_records_note_when_inventory_absent(self, tmp_path: Path):
        cand = tmp_path / "candidates.md"
        report = tmp_path / "REPORT.md"
        out = tmp_path / "audit" / "throughline_numeric_check.json"
        _write_throughline(cand, "## Candidate TL1: Foo.\n\nThe value 1.5 appears.\n")
        _write_report(report, "Range goes from 1.5 to 2.5.")
        rc = main([
            "--candidates", str(cand),
            "--report", str(report),
            "--inventory", str(tmp_path / "ghost.tsv"),
            "--out", str(out),
        ])
        # Inventory was specified but doesn't exist — notes records this.
        payload = json.loads(out.read_text())
        assert any("claim_inventory" in n for n in payload["notes"])


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_candidates(tmp_path: Path) -> Path:
    """Project layout sufficient for _run_throughline_numeric_check."""
    proj = tmp_path / "test_project"
    (proj / "papers" / "draft_1").mkdir(parents=True)
    (proj / "REPORT.md").write_text(
        "Essential genes are 86.1% core vs 81.2% non-essential.",
        encoding="utf-8",
    )
    return proj


def test_orchestrator_writes_audit_json(project_with_candidates: Path):
    """The orchestrator helper writes audit/throughline_numeric_check.json
    with the v1 schema after running."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_candidates / "papers" / "draft_1"
    (draft / "throughline_candidates.md").write_text(
        "## Candidate TL1: Statement.\n\nWe saw 86.1% vs 81.2%.\n",
        encoding="utf-8",
    )
    orch = PaperWriterOrchestrator(draft_dir=draft)
    orch._run_throughline_numeric_check()

    audit_json = draft / "audit" / "throughline_numeric_check.json"
    assert audit_json.is_file()
    payload = json.loads(audit_json.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["totals"]["ungrounded"] == 0


def test_orchestrator_warns_on_fabrication_but_does_not_halt(
    project_with_candidates: Path,
    caplog: pytest.LogCaptureFixture,
):
    """The headline contract: fabrications surface as WARNING logs,
    but the helper returns cleanly (doesn't raise). v1 is advisory."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_candidates / "papers" / "draft_1"
    (draft / "throughline_candidates.md").write_text(
        "## Candidate TL1: Statement.\n\n"
        "Essential-unmapped genes are 58.1% hypothetical.\n",
        encoding="utf-8",
    )
    orch = PaperWriterOrchestrator(draft_dir=draft)
    with caplog.at_level("WARNING", logger="orchestrator"):
        orch._run_throughline_numeric_check()
    # Method returned normally (no halt). The WARNING log was emitted.
    msgs = " ".join(r.message for r in caplog.records)
    assert "UNGROUNDED" in msgs
    assert "58.1" in msgs


def test_orchestrator_skips_when_candidates_missing(
    project_with_candidates: Path,
    caplog: pytest.LogCaptureFixture,
):
    """If throughline_candidates.md doesn't exist (rare — only on a
    crashed phase_plan), log WARNING and return without writing JSON."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    draft = project_with_candidates / "papers" / "draft_1"
    # Note: no throughline_candidates.md written.

    orch = PaperWriterOrchestrator(draft_dir=draft)
    with caplog.at_level("WARNING", logger="orchestrator"):
        orch._run_throughline_numeric_check()

    audit_json = draft / "audit" / "throughline_numeric_check.json"
    assert not audit_json.exists()
    msgs = " ".join(r.message for r in caplog.records)
    assert "throughline_candidates.md missing" in msgs
