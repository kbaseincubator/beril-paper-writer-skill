"""run_record_emitter.py — paper-writer's `run-record.v1` emitter
(Cycle 3 / DP1, 2026-06-08).

Projects paper-writer's run-end state into the cross-skill
`run-record.v1` contract (schema + validator of record live in
craft-platform `craft.run_record`; this skill does NOT import that
package at runtime — the emitter CORE is COPY-ADAPTED here per the
copy-not-share convention, the same way llm_config.py / user_intent.py
/ stream_progress.py are vendored). The shared *record* is what's
contracted; each skill's *emitter* differs (project, don't rebuild).

Reference emitter: presentation-maker's finalize_run.py. This is a
faithful copy-adaptation of its CORE semantics:
  * atomic write (tempfile + os.replace in the same dir),
  * no-clobber run-N allocation + canonical/archive dual write,
  * the FINALIZE GUARD (never overwrite an existing `halted`),
  * LOUD reconciliation is N/A here (see "Value sourcing" below).

Paper-writer divergences from the presmaker reference
-----------------------------------------------------
* **Python orchestrator, native hooks.** presmaker wires record-*
  CLI subcommands from a bash orchestrator; paper-writer's
  orchestrator is pure-Python (PaperWriterOrchestrator), so it calls
  these functions DIRECTLY. Single process → no concurrency on the
  canonical record, and the per-phase telemetry (tokens/cost/model)
  is already in-process (parsed from the claude -p json envelope into
  `_last_call_telemetry`), so record_stage takes EXPLICIT values
  rather than reading `.metadata.json` sidecars. Consequently there
  is no finalize-time sidecar reconciliation step — the orchestrator
  is the sole, in-process source of truth.
* **Phase-driven status.** presmaker maps exit_code==0→completed,
  else→failed. paper-writer's terminal status is PHASE-driven:
  reaching the `assembled` phase → completed; an unexpected pipeline
  exception → failed. (The contract mandates the status VOCABULARY +
  invariants, not a shared mapping rule — per-skill mapping is
  expected.)
* **No DraftPaths class.** paper-writer keeps paths ad hoc
  (`draft_dir / "audit"`); this module defines the small set of
  path helpers it needs (audit dir, runs dir, run-N archive,
  canonical record path) locally.
* **run_id = run-N** (monotonic per draft_dir, mirroring presmaker),
  archived under audit/runs/run-N/.

Canonical paths
---------------
  canonical:  <draft_dir>/audit/run_record.json          (latest; pollable)
  archive:    <draft_dir>/audit/runs/run-N/run_record.json
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from beril_paper_writer import __version__ as _skill_version

_SKILL_VERSION = _skill_version

_RUN_RECORD_SCHEMA_VERSION = "run-record.v1"
_PAPER_WRITER_SKILL = "paper-writer"


# ---------------------------------------------------------------------------
# Path helpers (paper-writer has no DraftPaths class)
# ---------------------------------------------------------------------------

def _audit_dir(draft_dir: Path) -> Path:
    return draft_dir / "audit"


def _runs_dir(draft_dir: Path) -> Path:
    return _audit_dir(draft_dir) / "runs"


def _run_record_path(draft_dir: Path) -> Path:
    return _audit_dir(draft_dir) / "run_record.json"


def _run_archive_dir(draft_dir: Path, run_n: int) -> Path:
    return _runs_dir(draft_dir) / f"run-{run_n}"


def _next_run_n(runs_dir: Path) -> int:
    """Allocate the next run-<N> by scanning existing dirs (no-clobber)."""
    if not runs_dir.is_dir():
        return 1
    n = 1
    while (runs_dir / f"run-{n}").is_dir():
        n += 1
        if n > 9999:
            raise RuntimeError(
                f"cannot allocate run dir under {runs_dir}; too many runs"
            )
    return n


# ---------------------------------------------------------------------------
# C1-A2 — cross-record completeness guard (VENDORED from craft.run_record)
# ---------------------------------------------------------------------------
#
# Canonical impl: craft-platform `craft.run_record.check_no_dropped_stages`.
# paper-writer ships STANDALONE (pipx install, no craft-platform on path),
# so — like the rest of this emitter — the function is COPIED, not imported.
# A craft-platform conformance check pins this copy byte-equal to the
# canonical AND to presmaker's vendored copy; keep all three in sync.
#
# A resume must NEVER lose a stage a prior run completed. The per-record
# reconciliation only checks totals==sum(present), so an incomplete record
# reconciles perfectly — the C1-A drop is exactly this class. Called at
# finalize, BEFORE writing status=completed; a non-empty return is a hard,
# loud failure (raises CompletenessError).


class CompletenessError(Exception):
    """Raised at finalize when the C1-A2 completeness guard finds the
    canonical would drop a stage a prior run completed. Carries the list
    of error strings. This is a CORRECTNESS signal, distinct from the
    best-effort telemetry-write failures the orchestrator swallows — the
    orchestrator must let it surface loudly, never log-and-continue."""

    def __init__(self, errors: list):
        self.errors = errors
        super().__init__("; ".join(errors))


def _completed_stage_ids(record: object) -> set:
    """The set of stage ids a record marks `completed`. Tolerant of a
    malformed record. (Vendored from craft.run_record.)"""
    out: set = set()
    if not isinstance(record, dict):
        return out
    stages = record.get("stages")
    if not isinstance(stages, list):
        return out
    for s in stages:
        if (isinstance(s, dict) and s.get("status") == "completed"
                and isinstance(s.get("id"), str) and s["id"]):
            out.add(s["id"])
    return out


def check_no_dropped_stages(canonical: dict, archived_runs: list) -> list:
    """Cross-record completeness guard (C1-A2). The canonical's set of
    `completed` stage ids MUST be a SUPERSET of every archived run's
    `completed` set; returns error strings naming any dropped stage.
    Manifest-free. VENDORED byte-equal from craft.run_record — keep in
    sync (conformance-pinned)."""
    errors: list = []
    canon_completed = _completed_stage_ids(canonical)
    canon_run_id = (canonical.get("run_id")
                    if isinstance(canonical, dict) else None)
    for archived in archived_runs:
        arch_run_id = (archived.get("run_id")
                       if isinstance(archived, dict) else None)
        if arch_run_id is not None and arch_run_id == canon_run_id:
            continue
        arch_completed = _completed_stage_ids(archived)
        dropped = arch_completed - canon_completed
        if dropped:
            errors.append(
                f"completeness: canonical (run_id={canon_run_id!r}) is "
                f"missing {len(dropped)} stage(s) that archived run "
                f"{arch_run_id!r} completed: {sorted(dropped)} — a resume "
                f"must never drop a completed stage (C1-A). Refusing to "
                f"finalize as completed."
            )
    return errors


def _load_archived_runs(draft_dir: Path) -> list:
    """Load every archived runs/run-N/run_record.json as a parsed dict.
    A corrupt forensic archive shouldn't BLOCK finalize, but it must not
    be silent either — loud-warn to stderr and skip it (the guard
    tolerates a missing entry). Never raises."""
    import sys
    out: list = []
    runs_dir = _runs_dir(draft_dir)
    if not runs_dir.is_dir():
        return out
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        rr = run_dir / "run_record.json"
        if not rr.is_file():
            continue
        try:
            out.append(json.loads(rr.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"run_record_emitter: WARNING — could not read archived "
                f"run record {rr} for the completeness guard ({exc}); "
                f"skipping that archive.",
                file=sys.stderr,
            )
    return out


# ---------------------------------------------------------------------------
# Time + atomic write (copy-adapted from presmaker finalize_run.py)
# ---------------------------------------------------------------------------

def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_minus_seconds(iso_ts: str, seconds: float) -> str:
    """`iso_ts` shifted back by `seconds`, same YYYY-MM-DDTHH:MM:SSZ
    shape. Used to back-date a stage's started_at from finished_at +
    elapsed when the caller didn't capture a T0. Unchanged on parse
    failure (better a zero-duration stage than a crash)."""
    try:
        dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc,
        )
        return (dt - timedelta(seconds=float(seconds))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, TypeError):
        return iso_ts


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Atomically write JSON: tempfile in the SAME dir + os.replace, so
    a mid-run `craft status` poll never observes a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _load_existing_record(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _find_run_n(record: dict | None) -> int | None:
    if record is None:
        return None
    rid = record.get("run_id")
    if not isinstance(rid, str):
        return None
    m = re.match(r"^run-(\d+)$", rid)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------

def _refresh_totals(stages: list[dict]) -> dict:
    totals = {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "elapsed_seconds": 0.0,
    }
    for s in stages:
        totals["cost_usd"] += float(s.get("cost_usd", 0.0))
        totals["input_tokens"] += int(s.get("input_tokens", 0))
        totals["output_tokens"] += int(s.get("output_tokens", 0))
        totals["cache_read_tokens"] += int(s.get("cache_read_tokens", 0))
        totals["cache_creation_tokens"] += int(
            s.get("cache_creation_tokens", 0))
        totals["elapsed_seconds"] += float(s.get("elapsed_seconds", 0.0))
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def _models_used(stages: list[dict]) -> list[str]:
    seen: set[str] = set()
    for s in stages:
        m = s.get("model")
        if isinstance(m, str) and m:
            seen.add(m)
    return sorted(seen)


def _resolve_mode(draft_dir: Path) -> str | None:
    """Project `mode` from audit/user_intent.json (paper-writer writes
    it before the pipeline runs). None when absent."""
    ui = _audit_dir(draft_dir) / "user_intent.json"
    if not ui.is_file():
        return None
    try:
        data = json.loads(ui.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    m = data.get("mode")
    return m if isinstance(m, str) else None


def _rel_if_file(draft_dir: Path, p: Path) -> str | None:
    return str(p.relative_to(draft_dir)) if p.is_file() else None


def _project_artifacts(draft_dir: Path) -> dict:
    """The three artifact pointers paper-writer / presmaker share."""
    user_intent = _rel_if_file(
        draft_dir, _audit_dir(draft_dir) / "user_intent.json")
    deliverable_validation = _rel_if_file(
        draft_dir, _audit_dir(draft_dir) / "deliverable_validation.json")
    deliverable = _rel_if_file(draft_dir, draft_dir / "manuscript.docx")
    return {
        "user_intent": user_intent,
        "deliverable_validation": deliverable_validation,
        "deliverable": deliverable,
    }


def _build_record(
    draft_dir: Path,
    *,
    run_n: int,
    status: str,
    started_at: str,
    finished_at: str | None,
    exit_code: int | None,
    current_stage: str | None,
    stages: list[dict],
    skill_version: str,
) -> dict:
    return {
        "schema_version": _RUN_RECORD_SCHEMA_VERSION,
        "skill": _PAPER_WRITER_SKILL,
        "skill_version": skill_version,
        "run_id": f"run-{run_n}",
        "draft_dir": str(draft_dir),
        "mode": _resolve_mode(draft_dir),
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "current_stage": current_stage,
        "stages": stages,
        "totals": _refresh_totals(stages),
        "models_used": _models_used(stages),
        "artifacts": _project_artifacts(draft_dir),
    }


def _write_canonical_and_archive(
    draft_dir: Path, record: dict, run_n: int,
) -> tuple[Path, Path]:
    """Archive FIRST, canonical SECOND (an interrupted write leaves the
    archive intact + canonical at the prior version)."""
    _audit_dir(draft_dir).mkdir(parents=True, exist_ok=True)
    _runs_dir(draft_dir).mkdir(parents=True, exist_ok=True)
    archive_dir = _run_archive_dir(draft_dir, run_n)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / "run_record.json"
    _atomic_write_json(archive_path, record)
    canonical_path = _run_record_path(draft_dir)
    _atomic_write_json(canonical_path, record)
    return canonical_path, archive_path


def _new_stage_entry(
    *,
    stage_id: str,
    status: str,
    model: str | None,
    started_at: str | None,
    finished_at: str | None,
    elapsed_seconds: float,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cost_usd: float,
) -> dict:
    # Timestamp derivation: finished defaults to now for a terminal
    # stage status; started back-dates from finished − elapsed.
    if finished_at is None and status != "running":
        finished_at = _utc_iso_now()
    if started_at is None:
        if finished_at is not None and elapsed_seconds > 0:
            started_at = _iso_minus_seconds(finished_at, elapsed_seconds)
        else:
            started_at = finished_at or _utc_iso_now()
    return {
        "id": stage_id,
        "status": status,
        "model": model,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": float(elapsed_seconds),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cache_creation_tokens": int(cache_creation_tokens),
        "cost_usd": round(float(cost_usd), 6),
        "subrecord": None,
    }


# ---------------------------------------------------------------------------
# Public emitter API (the orchestrator calls these natively)
# ---------------------------------------------------------------------------

def record_start(
    draft_dir: Path,
    *,
    started_at: str | None = None,
    skill_version: str | None = None,
) -> int:
    """Initial write: allocate the next run-N (no-clobber), write a
    status=running record with empty stages. Returns the run_n so the
    orchestrator can stamp it onto subsequent calls (or just rely on
    the canonical record's run_id, which record_stage re-reads)."""
    draft_dir = Path(draft_dir)
    _runs_dir(draft_dir).mkdir(parents=True, exist_ok=True)
    run_n = _next_run_n(_runs_dir(draft_dir))
    record = _build_record(
        draft_dir,
        run_n=run_n,
        status="running",
        started_at=started_at or _utc_iso_now(),
        finished_at=None,
        exit_code=None,
        current_stage=None,
        stages=[],
        skill_version=skill_version or _SKILL_VERSION,
    )
    _write_canonical_and_archive(draft_dir, record, run_n)
    return run_n


def record_resume_or_start(
    draft_dir: Path,
    *,
    started_at: str | None = None,
    skill_version: str | None = None,
) -> tuple[int, str]:
    """Resume-aware entry point (v1.3.1 / Cycle-3 follow-up P0-2).

    The orchestrator calls THIS (not record_start) when it lazily opens
    the run-record, so a handshake-halt + `continue` stays ONE run
    record instead of fragmenting across run-N (halted) + run-N+1
    (resumed) and resetting craft status to $0 mid-manuscript. Decision
    is by the existing canonical record's STATUS:

      status ∈ {halted, running, failed}  → RE-OPEN (flip→running; keep
        run_id + started_at + cumulative totals + stages[]). The
        interrupted run continues; subsequent record_stage calls append,
        and a stage that previously failed upserts failed→completed in
        place on retry. (halted = handshake gate; running = crash/
        re-invoke; FAILED = mid-pipeline stage failure — a `continue`
        after a failure is a CONTINUATION of the same build, not a redo,
        so the stages the failed run completed before the failure MUST be
        carried, not dropped. Bucketing `failed` with `completed` here was
        the C1-A defect: it opened a fresh empty run that lost the
        already-completed stages' cost.)
      status == completed, or no record → ALLOCATE a fresh run-N (a
        genuine redo of a finished manuscript). Per Adam (C1, 2026-06-11):
        the fix is `failed` ONLY; `completed → fresh` stays.

    Returns (run_n, action) where action ∈ {"reopened", "allocated"}.
    """
    draft_dir = Path(draft_dir)
    existing = _load_existing_record(_run_record_path(draft_dir))
    status = existing.get("status") if isinstance(existing, dict) else None

    if status in ("halted", "running", "failed"):
        run_n = _find_run_n(existing)
        if run_n is None:
            return record_start(
                draft_dir, started_at=started_at,
                skill_version=skill_version), "allocated"
        reopened = dict(existing)
        reopened["status"] = "running"
        reopened["finished_at"] = None
        reopened["exit_code"] = None
        # run_id, started_at, stages[], totals, models_used, artifacts
        # carry over unchanged.
        _write_canonical_and_archive(draft_dir, reopened, run_n)
        return run_n, "reopened"

    return record_start(
        draft_dir, started_at=started_at,
        skill_version=skill_version), "allocated"


def record_stage(
    draft_dir: Path,
    *,
    stage_id: str,
    status: str = "completed",
    model: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    elapsed_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cost_usd: float = 0.0,
) -> Path | None:
    """Append-or-replace a stage entry (idempotent on retry by id),
    refresh totals, set current_stage to this id, atomic-write. Values
    come EXPLICITLY from the orchestrator (it holds the in-process
    telemetry from the claude -p envelope) — no sidecar reads.

    Refuses to mutate a terminal/halted record (start a new run first).
    Bootstraps a record-start if none exists (defensive; the
    orchestrator always record_starts first). Never raises on a
    telemetry path — returns None if it declined to write.
    """
    draft_dir = Path(draft_dir)
    existing = _load_existing_record(_run_record_path(draft_dir))
    if existing is None:
        run_n = record_start(draft_dir, started_at=started_at)
        existing = _load_existing_record(_run_record_path(draft_dir))
        if existing is None:
            return None
    else:
        run_n = _find_run_n(existing) or _next_run_n(_runs_dir(draft_dir))

    if existing.get("status") in ("completed", "failed", "halted"):
        # Terminal/halted: don't mutate (a resume's record_start opens a
        # fresh run). Mirrors presmaker's guard on record_stage.
        return None

    entry = _new_stage_entry(
        stage_id=stage_id, status=status, model=model,
        started_at=started_at, finished_at=finished_at,
        elapsed_seconds=elapsed_seconds,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens, cost_usd=cost_usd,
    )
    stages = list(existing.get("stages", []))
    replaced = False
    for i, s in enumerate(stages):
        if isinstance(s, dict) and s.get("id") == stage_id:
            stages[i] = entry
            replaced = True
            break
    if not replaced:
        stages.append(entry)

    record = _build_record(
        draft_dir,
        run_n=run_n,
        status="running",
        started_at=existing.get("started_at") or _utc_iso_now(),
        finished_at=None,
        exit_code=None,
        current_stage=stage_id,
        stages=stages,
        skill_version=existing.get("skill_version", _SKILL_VERSION),
    )
    canonical, _archive = _write_canonical_and_archive(
        draft_dir, record, run_n)
    return canonical


def record_halt(
    draft_dir: Path,
    *,
    gate_id: str,
    skill_version: str | None = None,
) -> Path | None:
    """Halt-gate writer (paper-writer's PipelineHalted handler). Adds
    the gate as a `running` stage entry so the referential check
    (current_stage ∈ stages[].id) passes, flips top-level
    status=halted. The terminal record_finalize MUST then refuse to
    overwrite (the finalize guard) — the halt survives; a resume's
    record_start flips back to running.
    """
    draft_dir = Path(draft_dir)
    existing = _load_existing_record(_run_record_path(draft_dir))
    if existing is None:
        run_n = record_start(draft_dir, skill_version=skill_version)
        existing = _load_existing_record(_run_record_path(draft_dir))
        if existing is None:
            return None
    else:
        run_n = _find_run_n(existing) or _next_run_n(_runs_dir(draft_dir))

    # Already terminal? Don't resurrect.
    if existing.get("status") in ("completed", "failed"):
        return None

    stages = list(existing.get("stages", []))
    if not any(isinstance(s, dict) and s.get("id") == gate_id
               for s in stages):
        stages.append(_new_stage_entry(
            stage_id=gate_id, status="running", model=None,
            started_at=_utc_iso_now(), finished_at=None,
            elapsed_seconds=0.0, input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0, cost_usd=0.0,
        ))

    record = _build_record(
        draft_dir,
        run_n=run_n,
        status="halted",
        started_at=existing.get("started_at") or _utc_iso_now(),
        finished_at=None,
        exit_code=None,
        current_stage=gate_id,
        stages=stages,
        skill_version=existing.get(
            "skill_version", skill_version or _SKILL_VERSION),
    )
    canonical, _archive = _write_canonical_and_archive(
        draft_dir, record, run_n)
    return canonical


def record_finalize(
    draft_dir: Path,
    *,
    status: str,
    exit_code: int = 0,
    skill_version: str | None = None,
) -> Path | None:
    """Terminal write. `status` is PHASE-driven (the orchestrator
    decides: reaching `assembled` → "completed"; an unexpected
    exception → "failed") — paper-writer does NOT map exit_code→status
    the way presmaker does. exit_code is still recorded (0 default).

    THE FINALIZE GUARD (correctness lynchpin): if the existing record
    is `halted`, DO NOT overwrite — a halt at throughline_pick must
    survive process exit; only a resume's record_start clears it.
    """
    if status not in ("completed", "failed"):
        raise ValueError(
            f"record_finalize status must be terminal "
            f"(completed|failed); got {status!r}"
        )
    draft_dir = Path(draft_dir)
    existing = _load_existing_record(_run_record_path(draft_dir))

    if existing is not None and existing.get("status") == "halted":
        # Guard: preserve the halt across the terminal call.
        return _run_record_path(draft_dir)

    if existing is None:
        # No run was started (defensive). Bootstrap a minimal terminal
        # record so `craft status` has something coherent to read.
        run_n = record_start(draft_dir, skill_version=skill_version)
        existing = _load_existing_record(_run_record_path(draft_dir))
        if existing is None:
            return None

    run_n = _find_run_n(existing) or _next_run_n(_runs_dir(draft_dir))
    stages = list(existing.get("stages", []))
    record = _build_record(
        draft_dir,
        run_n=run_n,
        status=status,
        started_at=existing.get("started_at") or _utc_iso_now(),
        finished_at=_utc_iso_now(),
        exit_code=int(exit_code),
        current_stage=None,
        stages=stages,
        skill_version=existing.get(
            "skill_version", skill_version or _SKILL_VERSION),
    )

    # C1-A2 completeness guard: before declaring COMPLETED, the canonical's
    # completed-stage set MUST be a superset of every archived run's. A
    # resume that dropped a completed stage reconciles totals perfectly yet
    # under-reports — this is the only check that catches it. Fail LOUD:
    # raise CompletenessError (a correctness signal the orchestrator must
    # NOT swallow as a best-effort telemetry miss). Only guard the
    # completed path — a `failed` finalize already signals a problem.
    if status == "completed":
        drop_errors = check_no_dropped_stages(
            record, _load_archived_runs(draft_dir))
        if drop_errors:
            raise CompletenessError(drop_errors)

    canonical, _archive = _write_canonical_and_archive(
        draft_dir, record, run_n)
    return canonical
