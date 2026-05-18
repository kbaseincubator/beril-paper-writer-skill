"""Stage 4 Tier S — orchestrator integration tests for the P0 gate and
the remediation loop.

These tests exercise the dispatch / state-machine / I/O surface; the
LLM call is mocked at ``_run_claude_p_with_cost`` so we exercise the
real decision tree without burning tokens.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

from beril_paper_writer.orchestrator import (
    PaperWriterOrchestrator,
    PipelineHalted,
)
from beril_paper_writer.state import (
    DraftState,
    ManuscriptFile,
    RemediationCycle,
    hash_file,
    save_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal BERDL-like project layout sufficient for the gate +
    remediate phases. No real LLM call ever fires; the orchestrator
    only reads files that already exist on disk."""
    proj = tmp_path / "test_project"
    proj.mkdir()
    (proj / "REPORT.md").write_text(
        "# Report\n\nThe Allen lab reported 42 widgets.\n",
        encoding="utf-8",
    )
    (proj / "RESEARCH_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (proj / "methods_provenance.md").write_text(
        "# Methods\n", encoding="utf-8",
    )
    (proj / "papers").mkdir()
    (proj / "papers" / "draft_1").mkdir()
    return proj


@pytest.fixture
def draft_dir(project: Path) -> Path:
    return project / "papers" / "draft_1"


@pytest.fixture
def orch(draft_dir: Path) -> PaperWriterOrchestrator:
    return PaperWriterOrchestrator(draft_dir=draft_dir)


def _write_adv_audit(audit_dir: Path, findings: list[dict]) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "adversarial_review.json").write_text(
        json.dumps({
            "schema_version": "adversarial-review-paper.v3",
            "findings": findings,
        }, indent=2),
        encoding="utf-8",
    )


def _write_num_audit(audit_dir: Path, findings: list[dict]) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "numeric_grounding.json").write_text(
        json.dumps({
            "schema_version": "v1",
            "findings": findings,
            "totals": {"ungrounded": len(findings)},
            "allowlisted": [],
            "notes": [],
        }, indent=2),
        encoding="utf-8",
    )


def _write_manuscript(draft_dir: Path, text: str = "draft body\n") -> None:
    (draft_dir / "manuscript.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# phase_p0_review: decision tree
# ---------------------------------------------------------------------------


def test_gate_advances_when_no_p0_findings(
    orch: PaperWriterOrchestrator, draft_dir: Path,
) -> None:
    """No P0s in either audit JSON → advance to optimize."""
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "register_drift", "severity": "P1",
         "issue": "informal"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    asyncio.run(orch.phase_p0_review())

    assert orch.state.phase == "optimize"


def test_gate_pauses_when_p0_present_and_no_flags(
    orch: PaperWriterOrchestrator, draft_dir: Path,
) -> None:
    """Default behaviour: P0 + no flags → PipelineHalted + write
    p0_findings.md."""
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "missing"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    with pytest.raises(PipelineHalted):
        asyncio.run(orch.phase_p0_review())

    # Side effect: human-readable markdown was written.
    assert (draft_dir / "p0_findings.md").is_file()
    md = (draft_dir / "p0_findings.md").read_text()
    assert "Total P0 findings:** 1" in md
    assert "--remediate" in md


def test_gate_advances_when_ship_with_p0s_overrides(
    draft_dir: Path,
) -> None:
    """--ship-with-p0s short-circuits the gate even with P0 findings.
    p0_findings.md is still written for the audit trail."""
    orch = PaperWriterOrchestrator(
        draft_dir=draft_dir, ship_with_p0s=True,
    )
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    asyncio.run(orch.phase_p0_review())

    assert orch.state.phase == "optimize"
    assert (draft_dir / "p0_findings.md").is_file()


def test_gate_dispatches_to_remediate_when_flag_set_and_cycles_remain(
    draft_dir: Path,
) -> None:
    """--remediate + cycles_used < max → advance to remediate."""
    orch = PaperWriterOrchestrator(
        draft_dir=draft_dir, remediate=True, max_remediate_cycles=2,
    )
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    asyncio.run(orch.phase_p0_review())

    assert orch.state.phase == "remediate"
    # No findings.md written on the dispatch path — only on pause.
    # The remediation prompt's snapshot dir carries the JSON instead.


def test_gate_pauses_when_remediate_set_but_cycle_cap_reached(
    draft_dir: Path,
) -> None:
    """--remediate + cycles_used == max → pause again. The renderer's
    `cycles_exhausted` mode kicks in, reframing the proceed options
    away from a bare --remediate retry toward raising the cap or
    shipping."""
    orch = PaperWriterOrchestrator(
        draft_dir=draft_dir, remediate=True, max_remediate_cycles=2,
    )
    # Pretend two cycles already happened.
    orch.state.remediation_cycles = [
        RemediationCycle(
            cycle_n=1, ts_start="t1", status="completed",
            p0_before=5, p0_after=3,
        ),
        RemediationCycle(
            cycle_n=2, ts_start="t2", status="completed",
            p0_before=3, p0_after=2,
        ),
    ]
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
        {"id": "F002", "class": "report_drift", "severity": "P0",
         "issue": "y"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    with pytest.raises(PipelineHalted):
        asyncio.run(orch.phase_p0_review())

    md = (draft_dir / "p0_findings.md").read_text()
    assert "Cycle cap exhausted" in md
    assert "--max-remediate-cycles 4" in md


def test_gate_backfills_p0_after_on_completed_cycle(
    draft_dir: Path,
) -> None:
    """When phase_p0_review observes a completed cycle whose p0_after
    is None, it backfills it with the current audit's P0 count."""
    orch = PaperWriterOrchestrator(draft_dir=draft_dir)
    orch.state.remediation_cycles = [
        RemediationCycle(
            cycle_n=1, ts_start="t1", status="completed",
            p0_before=10, p0_after=None,  # not yet backfilled
        ),
    ]
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
        {"id": "F002", "class": "report_drift", "severity": "P0",
         "issue": "y"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    _write_manuscript(draft_dir)
    orch.state.phase = "p0_review"
    save_state(draft_dir, orch.state)

    with pytest.raises(PipelineHalted):
        asyncio.run(orch.phase_p0_review())

    assert orch.state.remediation_cycles[0].p0_after == 2
    assert orch.state.remediation_cycles[0].p0_after_by_source == {
        "adversarial": 2,
    }


# ---------------------------------------------------------------------------
# Manual-edit detection
# ---------------------------------------------------------------------------


def test_gate_routes_back_to_review_on_manual_edit(
    orch: PaperWriterOrchestrator, draft_dir: Path,
) -> None:
    """If manuscript.md sha differs from the value recorded on the
    prior gate entry, the gate routes back to phase_review (not
    pause), so the cascade runs on the operator's edit."""
    # First-pass setup: record manuscript hash matching v1 contents.
    _write_manuscript(draft_dir, text="version A\n")
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "p0_review"
    orch.state.manuscript_files = [
        ManuscriptFile(
            path="manuscript.md",
            sha256=hash_file(draft_dir / "manuscript.md"),
        ),
    ]
    save_state(draft_dir, orch.state)

    # Operator hand-edits the manuscript while paused.
    _write_manuscript(draft_dir, text="version B — operator edit\n")

    asyncio.run(orch.phase_p0_review())

    # Should have routed back to review, NOT raised PipelineHalted.
    assert orch.state.phase == "review"
    # And the recorded hash should now match version B.
    recorded = next(
        m.sha256 for m in orch.state.manuscript_files
        if m.path == "manuscript.md"
    )
    assert recorded == hash_file(draft_dir / "manuscript.md")


def test_gate_records_hash_on_first_entry(
    orch: PaperWriterOrchestrator, draft_dir: Path,
) -> None:
    """First time through the gate, no recorded hash exists; the gate
    records the current one and proceeds normally (no false-positive
    manual-edit detection)."""
    _write_manuscript(draft_dir, text="initial\n")
    _write_adv_audit(draft_dir / "audit", [])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "p0_review"
    # No manuscript_files recorded.
    save_state(draft_dir, orch.state)

    asyncio.run(orch.phase_p0_review())

    # Advanced to optimize because no P0s; manuscript hash now recorded.
    assert orch.state.phase == "optimize"
    assert any(
        m.path == "manuscript.md"
        for m in orch.state.manuscript_files
    )


# ---------------------------------------------------------------------------
# phase_remediate: snapshot + cycle bookkeeping
# ---------------------------------------------------------------------------


class _FakeRunClaudeP:
    """Drop-in replacement for _run_claude_p_with_cost. Configurable
    return code and a side-effect that mutates the manuscript on
    'success' to simulate the drafter doing its job."""

    def __init__(
        self,
        *,
        rc: int = 0,
        cost: float = 0.42,
        post_text: Optional[str] = None,
        target_path: Optional[Path] = None,
    ):
        self.rc = rc
        self.cost = cost
        self.post_text = post_text
        self.target_path = target_path
        self.called = 0

    async def __call__(self, *args, **kwargs):
        self.called += 1
        if self.rc == 0 and self.post_text is not None and self.target_path is not None:
            self.target_path.write_text(self.post_text, encoding="utf-8")
        return self.rc, "{}", "", self.cost


def test_remediate_snapshots_audit_and_manuscript(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phase_remediate writes manuscript.iter_<N>.md, audit/iter_<N>/
    with the audit JSONs + a derived p0_findings.json, and appends an
    in-progress RemediationCycle entry BEFORE the LLM call."""
    _write_manuscript(draft_dir, text="pre-remediation body\n")
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "remediate"
    save_state(draft_dir, orch.state)

    fake = _FakeRunClaudeP(
        rc=0, cost=0.42,
        post_text="post-remediation body\n",
        target_path=draft_dir / "manuscript.md",
    )
    monkeypatch.setattr(orch, "_run_claude_p_with_cost", fake)

    asyncio.run(orch.phase_remediate())

    # Snapshot files exist.
    assert (draft_dir / "manuscript.iter_1.md").is_file()
    assert (draft_dir / "manuscript.iter_1.md").read_text() == \
        "pre-remediation body\n"
    snap_dir = draft_dir / "audit" / "iter_1"
    assert (snap_dir / "adversarial_review.json").is_file()
    assert (snap_dir / "numeric_grounding.json").is_file()
    assert (snap_dir / "p0_findings.json").is_file()
    # The combined findings snapshot has total=1.
    snap = json.loads((snap_dir / "p0_findings.json").read_text())
    assert snap["total"] == 1

    # Cycle entry completed, costs captured.
    assert len(orch.state.remediation_cycles) == 1
    c = orch.state.remediation_cycles[0]
    assert c.cycle_n == 1
    assert c.status == "completed"
    assert c.p0_before == 1
    assert c.drafter_cost_usd == pytest.approx(0.42)
    assert c.manuscript_pre_path == "manuscript.iter_1.md"
    assert c.audit_snapshot_dir == "audit/iter_1"
    # Next phase queued.
    assert orch.state.phase == "review"
    # Manuscript hash recorded for the next gate's manual-edit check.
    assert any(
        m.path == "manuscript.md"
        for m in orch.state.manuscript_files
    )


def test_remediate_marks_aborted_on_llm_failure(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rc != 0 → status=aborted-error, ts_end set, note populated,
    pipeline still advances to review (cascade will re-evaluate)."""
    _write_manuscript(draft_dir, text="pre\n")
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "remediate"
    save_state(draft_dir, orch.state)

    fake = _FakeRunClaudeP(rc=2, cost=0.05)
    monkeypatch.setattr(orch, "_run_claude_p_with_cost", fake)

    asyncio.run(orch.phase_remediate())

    assert len(orch.state.remediation_cycles) == 1
    c = orch.state.remediation_cycles[0]
    assert c.status == "aborted-error"
    assert c.ts_end is not None
    assert "exit 2" in c.note
    assert c.drafter_cost_usd == pytest.approx(0.05)
    # Manuscript should still be at pre — fake didn't write.
    assert (draft_dir / "manuscript.md").read_text() == "pre\n"
    assert orch.state.phase == "review"


def test_remediate_handles_missing_manuscript(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
) -> None:
    """If manuscript.md is absent at entry, skip the cycle and let
    phase_review surface the issue."""
    # Note: no _write_manuscript call.
    orch.state.phase = "remediate"
    save_state(draft_dir, orch.state)

    asyncio.run(orch.phase_remediate())

    assert orch.state.phase == "review"
    assert orch.state.remediation_cycles == []


def test_remediate_runs_fabrication_post_check(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-check writes audit/remediation_fabrication_check_iter_<N>.json
    and surfaces suspect new numerics not in REPORT.md."""
    _write_manuscript(draft_dir, text="pre body with 100 and 200\n")
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "remediate"
    save_state(draft_dir, orch.state)

    # Fake drafter introduces 999, which is NOT in REPORT.md
    # (REPORT.md says "42 widgets"). 100 + 200 are in pre, so they
    # don't count as new. 999 is new and suspect.
    fake = _FakeRunClaudeP(
        rc=0, cost=0.1,
        post_text="post body with 100, 200, and a sneaky 999\n",
        target_path=draft_dir / "manuscript.md",
    )
    monkeypatch.setattr(orch, "_run_claude_p_with_cost", fake)

    asyncio.run(orch.phase_remediate())

    post_check_path = (
        draft_dir / "audit" / "remediation_fabrication_check_iter_1.json"
    )
    assert post_check_path.is_file()
    diag = json.loads(post_check_path.read_text())
    assert diag["phase"] == "remediate"
    assert diag["cycle_n"] == 1
    assert "999" in diag["suspect"]


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def test_remediation_cycles_persist_to_state_json(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cycle entry survives a save_state / load_state round-trip
    via the actual on-disk JSON."""
    _write_manuscript(draft_dir, text="pre\n")
    _write_adv_audit(draft_dir / "audit", [
        {"id": "F001", "class": "citation_reality", "severity": "P0",
         "issue": "x"},
    ])
    _write_num_audit(draft_dir / "audit", [])
    orch.state.phase = "remediate"
    save_state(draft_dir, orch.state)

    fake = _FakeRunClaudeP(
        rc=0, cost=0.5,
        post_text="post\n",
        target_path=draft_dir / "manuscript.md",
    )
    monkeypatch.setattr(orch, "_run_claude_p_with_cost", fake)
    asyncio.run(orch.phase_remediate())

    raw = json.loads((draft_dir / "state.json").read_text())
    cycles = raw["remediation_cycles"]
    assert len(cycles) == 1
    assert cycles[0]["cycle_n"] == 1
    assert cycles[0]["status"] == "completed"
    assert cycles[0]["drafter_cost_usd"] == pytest.approx(0.5)
    assert cycles[0]["p0_before"] == 1
    assert cycles[0]["manuscript_pre_path"] == "manuscript.iter_1.md"


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_orchestrator_accepts_tier_s_flags(draft_dir: Path) -> None:
    """The constructor stores the three Tier-S flags as instance
    attributes consumable by phase_p0_review and phase_remediate."""
    orch = PaperWriterOrchestrator(
        draft_dir=draft_dir,
        remediate=True, ship_with_p0s=True, max_remediate_cycles=5,
    )
    assert orch.remediate is True
    assert orch.ship_with_p0s is True
    assert orch.max_remediate_cycles == 5


def test_max_remediate_cycles_clamped_non_negative(
    draft_dir: Path,
) -> None:
    """A negative cycle cap should be clamped to 0 so the gate
    doesn't enter an undefined state."""
    orch = PaperWriterOrchestrator(
        draft_dir=draft_dir, max_remediate_cycles=-3,
    )
    assert orch.max_remediate_cycles == 0


# ---------------------------------------------------------------------------
# Stage 4 Tier S-9: defensive contract check on adversarial output
# ---------------------------------------------------------------------------


def test_adversarial_check_passes_when_file_freshly_written(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
) -> None:
    """The adversarial output mtime equal-or-after the subprocess start
    time means the CLI did its job."""
    import time as _time
    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    adv_json = audit_dir / "adversarial_review.json"
    start = _time.time()
    # Simulate the CLI writing AFTER the subprocess started.
    _time.sleep(0.05)
    adv_json.write_text("{\"findings\": []}", encoding="utf-8")
    assert orch._check_adversarial_output_fresh(
        adv_json_path=adv_json, subprocess_start=start,
    ) is True


def test_adversarial_check_warns_when_file_predates_subprocess(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An mtime predating the subprocess start (with 1s slop) means the
    CLI exited without rewriting. Must log WARNING and return False."""
    import os
    import time as _time
    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    adv_json = audit_dir / "adversarial_review.json"
    # Write the file first, then pretend the subprocess started after.
    adv_json.write_text("{\"findings\": []}", encoding="utf-8")
    stale_mtime = _time.time() - 3600  # one hour ago
    os.utime(adv_json, (stale_mtime, stale_mtime))
    start = _time.time()

    with caplog.at_level("WARNING", logger="orchestrator"):
        result = orch._check_adversarial_output_fresh(
            adv_json_path=adv_json, subprocess_start=start,
        )
    assert result is False
    msgs = " ".join(r.message for r in caplog.records)
    assert "STALE" in msgs.upper() or "predates" in msgs.lower()
    assert "adversarial" in msgs.lower()


def test_adversarial_check_warns_when_file_missing(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the adversarial CLI exits 0 but the output file doesn't even
    exist, that's the most extreme silent-failure variant. Must log
    WARNING and return False."""
    import time as _time
    adv_json = draft_dir / "audit" / "adversarial_review.json"
    # Note: file deliberately not created.
    with caplog.at_level("WARNING", logger="orchestrator"):
        result = orch._check_adversarial_output_fresh(
            adv_json_path=adv_json, subprocess_start=_time.time(),
        )
    assert result is False
    msgs = " ".join(r.message for r in caplog.records)
    assert "does not exist" in msgs


def test_adversarial_check_tolerates_one_second_filesystem_slop(
    orch: PaperWriterOrchestrator,
    draft_dir: Path,
) -> None:
    """Some filesystems have 1-second mtime resolution. A file mtime
    slightly before subprocess_start (within slop) must still count
    as fresh — otherwise we'd false-positive on every fast run."""
    import os
    import time as _time
    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    adv_json = audit_dir / "adversarial_review.json"
    adv_json.write_text("{\"findings\": []}", encoding="utf-8")
    near_mtime = _time.time() - 0.5  # 500ms before subprocess_start
    os.utime(adv_json, (near_mtime, near_mtime))
    start = near_mtime + 0.5
    assert orch._check_adversarial_output_fresh(
        adv_json_path=adv_json, subprocess_start=start,
    ) is True
