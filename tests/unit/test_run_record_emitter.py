"""Tests for paper-writer's run-record.v1 emitter (Cycle 3 / DP1).

Covers:
- The four-state lifecycle (running → halted → completed) via the
  native Python emitter API.
- run-N no-clobber allocation + canonical/archive dual write.
- The FINALIZE GUARD (record_finalize must not overwrite halted).
- Phase-driven terminal status (the orchestrator decides
  completed|failed; record_finalize rejects a non-terminal status).
- Atomic write leaves no .tmp behind.
- The three committed goldens validate against the shared
  craft.run_record validator (the Family-E roundtrip from inside the
  skill; graceful-skips when craft-platform isn't editable-installed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SKILL_TOOLS = (
    Path(__file__).resolve().parents[2]
    / "src" / "beril_paper_writer" / "skill" / "tools"
)
sys.path.insert(0, str(_SKILL_TOOLS))

import run_record_emitter as rr  # noqa: E402

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "run_record_v1"


def _read_canonical(draft_dir: Path) -> dict:
    return json.loads(
        (draft_dir / "audit" / "run_record.json").read_text(encoding="utf-8")
    )


def _try_import_validator():
    try:
        from craft.run_record import validate_run_record  # type: ignore
        return validate_run_record
    except ImportError:
        return None


def _mk_draft(tmp_path: Path, mode: str = "paper") -> Path:
    draft = tmp_path / "papers" / "draft_1"
    (draft / "audit").mkdir(parents=True)
    if mode is not None:
        (draft / "audit" / "user_intent.json").write_text(
            json.dumps({"mode": mode}), encoding="utf-8")
    return draft


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_record_start_writes_running(tmp_path):
    draft = _mk_draft(tmp_path)
    run_n = rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    assert run_n == 1
    rec = _read_canonical(draft)
    assert rec["schema_version"] == "run-record.v1"
    assert rec["skill"] == "paper-writer"
    assert rec["run_id"] == "run-1"
    assert rec["status"] == "running"
    assert rec["mode"] == "paper"
    assert rec["finished_at"] is None and rec["exit_code"] is None
    assert rec["current_stage"] is None and rec["stages"] == []
    # archive written too
    assert (draft / "audit" / "runs" / "run-1" / "run_record.json").is_file()


def test_record_start_no_clobber_allocates_run_2(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    n2 = rr.record_start(draft, started_at="2026-06-08T17:00:00Z")
    assert n2 == 2
    assert _read_canonical(draft)["run_id"] == "run-2"
    assert (draft / "audit" / "runs" / "run-1" / "run_record.json").is_file()
    assert (draft / "audit" / "runs" / "run-2" / "run_record.json").is_file()


def test_record_stage_appends_and_sets_current(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(
        draft, stage_id="triage", status="completed",
        model="claude-opus-4-7", input_tokens=15000, output_tokens=2200,
        cost_usd=0.41, elapsed_seconds=300,
    )
    rec = _read_canonical(draft)
    stage = next(s for s in rec["stages"] if s["id"] == "triage")
    assert stage["cost_usd"] == 0.41
    assert stage["input_tokens"] == 15000
    assert rec["current_stage"] == "triage"
    assert rec["totals"]["cost_usd"] == 0.41
    assert rec["models_used"] == ["claude-opus-4-7"]


def test_record_stage_idempotent_on_id_collision(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="review", status="running")
    rr.record_stage(draft, stage_id="review", status="completed",
                    cost_usd=1.40)
    rec = _read_canonical(draft)
    review = [s for s in rec["stages"] if s["id"] == "review"]
    assert len(review) == 1  # replaced, not duplicated
    assert review[0]["status"] == "completed"
    assert rec["totals"]["cost_usd"] == 1.40  # no double-count


def test_non_llm_stage_records_zero_cost(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="extract", status="completed",
                    elapsed_seconds=180)
    stage = next(
        s for s in _read_canonical(draft)["stages"] if s["id"] == "extract")
    assert stage["cost_usd"] == 0.0 and stage["model"] is None


# ---------------------------------------------------------------------------
# Halt + the finalize guard
# ---------------------------------------------------------------------------

def test_record_halt_flips_status_and_names_gate(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="plan", status="completed", cost_usd=0.28)
    rr.record_halt(draft, gate_id="throughline_pick")
    rec = _read_canonical(draft)
    assert rec["status"] == "halted"
    assert rec["current_stage"] == "throughline_pick"
    assert rec["finished_at"] is None and rec["exit_code"] is None
    # gate is referentially present in stages[]
    assert any(s["id"] == "throughline_pick" for s in rec["stages"])


def test_finalize_guard_preserves_halted(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_halt(draft, gate_id="throughline_pick")
    before = (draft / "audit" / "run_record.json").read_bytes()
    rr.record_finalize(draft, status="completed")
    after = (draft / "audit" / "run_record.json").read_bytes()
    assert before == after  # guard: halt untouched
    assert _read_canonical(draft)["status"] == "halted"


def test_record_stage_refuses_to_mutate_halted(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_halt(draft, gate_id="throughline_pick")
    out = rr.record_stage(draft, stage_id="citation_pool", status="completed")
    assert out is None
    assert _read_canonical(draft)["status"] == "halted"


def test_record_start_always_allocates_even_over_halted(tmp_path):
    """record_start's contract is unconditional allocation (used for
    genuine fresh runs). The RESUME path uses record_resume_or_start
    instead — see the P0-2 tests below."""
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_halt(draft, gate_id="throughline_pick")
    n2 = rr.record_start(draft, started_at="2026-06-08T17:00:00Z")
    assert n2 == 2
    rec = _read_canonical(draft)
    assert rec["status"] == "running" and rec["run_id"] == "run-2"


# ---------------------------------------------------------------------------
# v1.3.1 / Cycle-3 follow-up P0-2 — resume re-opens, doesn't allocate
# ---------------------------------------------------------------------------

def test_resume_reopens_halted_record_same_run(tmp_path):
    """A handshake-halt → continue must stay ONE run record: re-open
    (flip halted→running), keep run_id + started_at + cumulative totals
    + stages[]. NOT a fresh run-N+1 (the P0-2 fragmentation bug)."""
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="triage", status="completed",
                    model="claude-opus-4-7", input_tokens=15000,
                    output_tokens=2200, cost_usd=0.41, elapsed_seconds=300)
    rr.record_halt(draft, gate_id="throughline_pick")
    pre = _read_canonical(draft)
    assert pre["status"] == "halted" and pre["run_id"] == "run-1"

    run_n, action = rr.record_resume_or_start(
        draft, started_at="2026-06-08T17:00:00Z")
    assert action == "reopened" and run_n == 1
    rec = _read_canonical(draft)
    assert rec["run_id"] == "run-1"                      # same run
    assert rec["status"] == "running"                    # flipped back
    assert rec["started_at"] == "2026-06-08T16:00:00Z"   # ORIGINAL start
    assert rec["finished_at"] is None and rec["exit_code"] is None
    assert rec["totals"]["cost_usd"] == 0.41             # cumulative, not $0
    assert {s["id"] for s in rec["stages"]} >= {"triage", "throughline_pick"}
    run_dirs = sorted(p.name for p in (draft / "audit" / "runs").iterdir())
    assert run_dirs == ["run-1"]                          # exactly one


def test_resume_on_running_record_reopens(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="extract", status="completed")
    run_n, action = rr.record_resume_or_start(
        draft, started_at="2026-06-08T18:00:00Z")
    assert action == "reopened" and run_n == 1


def test_resume_on_completed_record_allocates_fresh(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_finalize(draft, status="completed")
    run_n, action = rr.record_resume_or_start(
        draft, started_at="2026-06-08T18:00:00Z")
    assert action == "allocated" and run_n == 2
    assert _read_canonical(draft)["status"] == "running"


def test_resume_no_record_allocates_fresh(tmp_path):
    draft = _mk_draft(tmp_path)
    run_n, action = rr.record_resume_or_start(
        draft, started_at="2026-06-08T18:00:00Z")
    assert action == "allocated" and run_n == 1


def test_resume_then_complete_one_record(tmp_path):
    """Full halt→resume→complete: ONE record, cumulative total, status
    completed, stages span the halt."""
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="triage", status="completed",
                    cost_usd=0.41)
    rr.record_halt(draft, gate_id="throughline_pick")
    rr.record_resume_or_start(draft, started_at="2026-06-08T17:00:00Z")
    rr.record_stage(draft, stage_id="drafting", status="completed",
                    cost_usd=5.42)
    rr.record_finalize(draft, status="completed")
    rec = _read_canonical(draft)
    assert rec["status"] == "completed" and rec["run_id"] == "run-1"
    assert abs(rec["totals"]["cost_usd"] - 5.83) < 1e-9
    assert {"triage", "drafting"} <= {s["id"] for s in rec["stages"]}
    run_dirs = sorted(p.name for p in (draft / "audit" / "runs").iterdir())
    assert run_dirs == ["run-1"]


# ---------------------------------------------------------------------------
# Terminal status (phase-driven)
# ---------------------------------------------------------------------------

def test_record_finalize_completed(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="assemble", status="completed",
                    elapsed_seconds=120)
    rr.record_finalize(draft, status="completed")
    rec = _read_canonical(draft)
    assert rec["status"] == "completed"
    assert rec["exit_code"] == 0
    assert rec["current_stage"] is None
    assert rec["finished_at"] is not None


def test_record_finalize_failed(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_finalize(draft, status="failed", exit_code=1)
    rec = _read_canonical(draft)
    assert rec["status"] == "failed" and rec["exit_code"] == 1


def test_record_finalize_rejects_non_terminal_status(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    with pytest.raises(ValueError):
        rr.record_finalize(draft, status="running")


def test_no_mode_yields_null_mode(tmp_path):
    draft = _mk_draft(tmp_path, mode=None)  # no user_intent.json
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    assert _read_canonical(draft)["mode"] is None


def test_atomic_write_leaves_no_tmp(tmp_path):
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    rr.record_stage(draft, stage_id="extract", status="completed")
    leftovers = list((draft / "audit").glob(".*tmp*")) + \
        list((draft / "audit").glob("*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# Orchestrator-side per-phase token accumulation (multi-call phases)
# ---------------------------------------------------------------------------

def _bare_orchestrator(draft: Path):
    """Construct a PaperWriterOrchestrator WITHOUT running __init__'s
    claude-CLI resolution — we only exercise the run-record telemetry
    plumbing (accumulator + advance_phase recording), not the LLM path."""
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator
    from beril_paper_writer.state import load_state
    orch = object.__new__(PaperWriterOrchestrator)
    orch.draft_dir = draft
    orch.max_cost_usd = None
    orch.state = load_state(draft)
    orch._run_record_started = False
    orch._last_recorded_cost = 0.0
    orch._phase_token_accumulator = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }
    orch._phase_models = []
    orch._last_call_telemetry = {}
    return orch


def test_multi_call_phase_sums_tokens_not_last_call(tmp_path):
    """A phase that makes SEVERAL claude calls must record the SUM of
    every call's tokens (the presmaker reference's sum-every-call rule),
    not just the last call's — otherwise multi-call phases (drafting,
    remediate) undercount silently."""
    draft = (tmp_path / "papers" / "draft_1")
    (draft / "audit").mkdir(parents=True)
    (draft / "audit" / "user_intent.json").write_text(
        json.dumps({"mode": "paper"}), encoding="utf-8")
    (draft / "state.json").write_text(json.dumps({
        "project_id": "proj", "draft_number": 1,
        "phase": "init", "cost_so_far_usd": 0.0,
    }), encoding="utf-8")
    orch = _bare_orchestrator(draft)

    def simulate_call(it, ot, model, cost):
        # Mirror what _run_claude_p_with_cost does to the accumulator.
        acc = orch._phase_token_accumulator
        acc["input_tokens"] += it
        acc["output_tokens"] += ot
        if model not in orch._phase_models:
            orch._phase_models.append(model)
        orch.state.cost_so_far_usd = (orch.state.cost_so_far_usd or 0.0) + cost

    orch.state.phase = "init"
    orch.advance_phase("extract")   # starts run; init skipped, no reset
    orch.state.phase = "drafting"
    simulate_call(50000, 20000, "claude-opus-4-7", 3.00)
    simulate_call(42000, 11000, "claude-opus-4-7", 2.42)
    orch.advance_phase("review")    # records drafting with the SUM

    rec = _read_canonical(draft)
    drafting = next(s for s in rec["stages"] if s["id"] == "drafting")
    assert drafting["input_tokens"] == 92000   # 50000 + 42000
    assert drafting["output_tokens"] == 31000  # 20000 + 11000
    assert abs(drafting["cost_usd"] - 5.42) < 1e-9
    assert drafting["model"] == "claude-opus-4-7"
    # accumulator reset at the advance_phase boundary
    assert orch._phase_token_accumulator["input_tokens"] == 0
    assert orch._phase_models == []


def test_non_llm_phase_records_zero_tokens(tmp_path):
    """A phase with no claude call (accumulator never touched) records a
    clean zero-token, null-model entry."""
    draft = (tmp_path / "papers" / "draft_1")
    (draft / "audit").mkdir(parents=True)
    (draft / "state.json").write_text(json.dumps({
        "project_id": "proj", "draft_number": 1,
        "phase": "init", "cost_so_far_usd": 0.0,
    }), encoding="utf-8")
    orch = _bare_orchestrator(draft)
    orch.state.phase = "init"
    orch.advance_phase("extract")
    orch.state.phase = "extract"
    orch.advance_phase("triage")   # extract made no calls
    rec = _read_canonical(draft)
    extract = next(s for s in rec["stages"] if s["id"] == "extract")
    assert extract["input_tokens"] == 0 and extract["output_tokens"] == 0
    assert extract["cost_usd"] == 0.0 and extract["model"] is None


# ---------------------------------------------------------------------------
# Goldens + Family-E roundtrip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "paper_writer_running", "paper_writer_halted", "paper_writer_completed",
])
def test_golden_parses(name):
    rec = json.loads((_FIXTURES / f"{name}.json").read_text())
    assert rec["schema_version"] == "run-record.v1"
    assert rec["skill"] == "paper-writer"


@pytest.mark.parametrize("name", [
    "paper_writer_running", "paper_writer_halted", "paper_writer_completed",
])
def test_golden_validates_against_shared_validator(name):
    validate = _try_import_validator()
    if validate is None:
        pytest.skip(
            "craft-platform not editable-installed alongside; the "
            "Family-E roundtrip runs at craft-platform's conformance "
            "pytest. Locally: pip install -e ../craft-platform"
        )
    rec = json.loads((_FIXTURES / f"{name}.json").read_text())
    assert validate(rec) == []


def test_emitted_lifecycle_validates_against_shared_validator(tmp_path):
    """The full lifecycle's emitted records each validate clean."""
    validate = _try_import_validator()
    if validate is None:
        pytest.skip("craft-platform not editable-installed alongside")
    draft = _mk_draft(tmp_path)
    rr.record_start(draft, started_at="2026-06-08T16:00:00Z")
    assert validate(_read_canonical(draft)) == []
    rr.record_stage(draft, stage_id="triage", status="completed",
                    model="claude-opus-4-7", input_tokens=15000,
                    output_tokens=2200, cost_usd=0.41, elapsed_seconds=300)
    assert validate(_read_canonical(draft)) == []
    rr.record_halt(draft, gate_id="throughline_pick")
    assert validate(_read_canonical(draft)) == []
    rr.record_finalize(draft, status="completed")  # guard → stays halted
    rec = _read_canonical(draft)
    assert validate(rec) == []
    assert rec["status"] == "halted"
