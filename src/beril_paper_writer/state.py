"""state.py — persistent draft state and intercalation hash-diff.

The paper-writer is restartable. State lives on disk in
`papers/draft_N/state.json` per SPEC §5.5 and LAYOUT "state.json schema".
On `continue`, the writer hashes all source artifacts (RESEARCH_PLAN.md,
REPORT.md, notebooks, figures, references.md) and manuscript intermediates,
compares against state.json, and reports changes explicitly to the user
before integrating. Silent integration is forbidden (per DECISIONS D-008).

This module provides:

- `DraftState` — typed container for the state.json structure
- `compute_artifact_hashes(...)` — sha256 + mtime + path for each tracked file
- `diff_artifacts(...)` — what changed since the last build
- `load_state(...)` / `save_state(...)` — disk I/O with atomic write
- `is_user_edited(...)` — detect manuscript files the user touched

State schema is documented in LAYOUT.md "state.json schema". Field
additions are non-breaking (new fields default to absent); removals
are breaking and require a state-version bump.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Schema version: bump when removing or renaming fields. Adding new fields
# is non-breaking and does not require a bump.
STATE_SCHEMA_VERSION = "0.1"

# Phases the writer can pause in. Stored in `phase` field of state.json.
VALID_PHASES = frozenset(
    {
        "init",
        "extract",
        "triage",
        "plan",
        "throughline_pick",
        "citation_pool",
        "drafting",
        "supplementary_pool",
        "review",
        # Stage 4 Tier S (2026-05-18): P0 gate + remediation loop sit
        # between `review` and `optimize`. `phase_review` advances into
        # `p0_review`, which decides (a) advance to optimize when no
        # P0s, or `--ship-with-p0s` overrides; (b) dispatch into
        # `remediate` when `--remediate` is set AND cycles remain;
        # (c) raise PipelineHalted with `p0_findings.md` otherwise.
        # `remediate` runs one re-draft cycle then advances back to
        # `review` (the cascade re-runs and the gate re-evaluates).
        "p0_review",
        "remediate",
        "optimize",
        "compliance_gate",
        # Stage 1 Tier B: added "assemble" — explicit Phase 8 docx render
        # between compliance_gate (markdown OK) and assembled (docx done).
        # Previously the pipeline never produced a .docx because no
        # phase_assemble existed in run_pipeline.
        "assemble",
        # Removed "rewrite" — it was dead code; nothing called
        # phase_rewrite (which never existed). If a rewrite is needed,
        # it routes through Phase 2 (targeted holistic re-pass) or
        # Phase 4 (selective optimizer).
        "assembled",
    }
)


# --------------------------------------------------------------------------
# Hash + change tracking
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactHash:
    """sha256 + mtime for one tracked file. Path is relative to project_dir."""

    path: str  # relative path, forward-slash
    sha256: str
    mtime: float
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactHash:
        return cls(
            path=d["path"],
            sha256=d["sha256"],
            mtime=float(d["mtime"]),
            size_bytes=int(d["size_bytes"]),
        )


def hash_file(path: Path, *, chunk_size: int = 1 << 16) -> str:
    """sha256 hex digest of a file's contents. Streams to bound memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_artifact_hashes(
    base_dir: Path,
    relative_paths: list[str],
) -> list[ArtifactHash]:
    """Compute hashes for a list of files relative to base_dir.

    Missing files are silently skipped (returns no entry for them) — caller
    decides how to interpret absence. Use `diff_artifacts` to detect both
    added and removed paths in one pass.
    """
    out: list[ArtifactHash] = []
    for rel in relative_paths:
        # Normalize to forward slashes for cross-platform stability.
        rel_norm = rel.replace(os.sep, "/")
        abs_path = base_dir / rel
        if not abs_path.is_file():
            continue
        stat = abs_path.stat()
        out.append(
            ArtifactHash(
                path=rel_norm,
                sha256=hash_file(abs_path),
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
            )
        )
    return out


@dataclass(frozen=True)
class ArtifactDiff:
    """Per-path change classification for intercalation reporting."""

    added: tuple[str, ...]       # in current, not in previous
    removed: tuple[str, ...]     # in previous, not in current
    changed: tuple[str, ...]     # path present in both, sha256 differs
    unchanged: tuple[str, ...]   # path present in both, sha256 matches

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, "
            f"{len(self.removed)} removed, "
            f"{len(self.changed)} changed, "
            f"{len(self.unchanged)} unchanged"
        )


def diff_artifacts(
    previous: list[ArtifactHash],
    current: list[ArtifactHash],
) -> ArtifactDiff:
    """Compare two artifact-hash lists. Path is the join key."""
    prev_by_path = {a.path: a for a in previous}
    curr_by_path = {a.path: a for a in current}

    prev_paths = set(prev_by_path.keys())
    curr_paths = set(curr_by_path.keys())

    added = tuple(sorted(curr_paths - prev_paths))
    removed = tuple(sorted(prev_paths - curr_paths))

    in_both = curr_paths & prev_paths
    changed: list[str] = []
    unchanged: list[str] = []
    for p in sorted(in_both):
        if prev_by_path[p].sha256 != curr_by_path[p].sha256:
            changed.append(p)
        else:
            unchanged.append(p)

    return ArtifactDiff(
        added=added,
        removed=removed,
        changed=tuple(changed),
        unchanged=tuple(unchanged),
    )


def is_user_edited(
    rel_path: str,
    previous_hashes: list[ArtifactHash],
    current_hashes: list[ArtifactHash],
    writer_generated: bool,
) -> bool:
    """Did the user edit a manuscript file the writer previously generated?

    Returns True iff:
      - The file was previously writer-generated (per state.json record), AND
      - The file's sha256 differs between previous and current.

    A file the user edited is never auto-overwritten on resume; the writer
    surfaces a diff and asks (per SPEC §5.5).
    """
    if not writer_generated:
        return False
    prev = next((a for a in previous_hashes if a.path == rel_path), None)
    curr = next((a for a in current_hashes if a.path == rel_path), None)
    if prev is None or curr is None:
        return False
    return prev.sha256 != curr.sha256


# --------------------------------------------------------------------------
# Throughline reevaluation tracking
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ThroughlineReevaluation:
    """One round of throughline re-evaluation, recorded after artifact diff."""

    round: int
    at: str  # ISO 8601 UTC timestamp
    artifact_change_detected: bool
    changed_paths: tuple[str, ...]
    user_prompt_shown: str
    outcome: str  # "confirmed-still-valid" | "re-picked-as-TLN" | "abandoned"

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["changed_paths"] = list(self.changed_paths)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ThroughlineReevaluation:
        return cls(
            round=int(d["round"]),
            at=str(d["at"]),
            artifact_change_detected=bool(d["artifact_change_detected"]),
            changed_paths=tuple(d.get("changed_paths", [])),
            user_prompt_shown=str(d.get("user_prompt_shown", "")),
            outcome=str(d["outcome"]),
        )


@dataclass
class ThroughlineState:
    """Chosen throughline + history of any re-evaluations after artifact changes."""

    candidate_id: Optional[str] = None
    chosen_at: Optional[str] = None  # ISO 8601 UTC; None until user picks
    revision: int = 0
    artifact_hash_at_confirmation: Optional[str] = None
    reevaluations: list[ThroughlineReevaluation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "chosen_at": self.chosen_at,
            "revision": self.revision,
            "artifact_hash_at_confirmation": self.artifact_hash_at_confirmation,
            "reevaluations": [r.to_dict() for r in self.reevaluations],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ThroughlineState:
        return cls(
            candidate_id=d.get("candidate_id"),
            chosen_at=d.get("chosen_at"),
            revision=int(d.get("revision", 0)),
            artifact_hash_at_confirmation=d.get("artifact_hash_at_confirmation"),
            reevaluations=[
                ThroughlineReevaluation.from_dict(r)
                for r in d.get("reevaluations", [])
            ],
        )


# --------------------------------------------------------------------------
# Analysis requests + iteration counters
# --------------------------------------------------------------------------

VALID_REQUEST_STATUSES = frozenset(
    {"pending", "taken", "deferred", "dropped"}
)
VALID_REQUEST_TYPES = frozenset(
    {
        "analysis-request",
        "figure-request",
        "data-request",
        "citation-request",
        "validator-escalation",
    }
)


@dataclass
class AnalysisRequest:
    """One gap-fill request item, mirrored from analysis_requests.md.

    The markdown file is the source of truth for the request *content*;
    state.json records the parsed metadata for fast lookup and round
    accounting (per SPEC §5.4 grammar). On every `continue`, the writer
    re-parses analysis_requests.md and updates these entries.
    """

    id: str
    type: str
    status: str
    originated_at_round: int
    note: Optional[str] = None  # only set when status startswith "manual:"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnalysisRequest:
        return cls(
            id=str(d["id"]),
            type=str(d["type"]),
            status=str(d["status"]),
            originated_at_round=int(d.get("originated_at_round", 1)),
            note=d.get("note"),
        )


@dataclass
class IterationCounters:
    rewrite_passes: int = 0
    gap_fill_rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IterationCounters:
        return cls(
            rewrite_passes=int(d.get("rewrite_passes", 0)),
            gap_fill_rounds=int(d.get("gap_fill_rounds", 0)),
        )


# --------------------------------------------------------------------------
# Manuscript file tracking
# --------------------------------------------------------------------------

@dataclass
class ManuscriptFile:
    """One manuscript intermediate. user_edited tracked across runs."""

    path: str  # relative to draft_dir
    sha256: str
    writer_generated: bool = True
    user_edited: bool = False  # True once user-edit detected, sticky until regen

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ManuscriptFile:
        return cls(
            path=str(d["path"]),
            sha256=str(d["sha256"]),
            writer_generated=bool(d.get("writer_generated", True)),
            user_edited=bool(d.get("user_edited", False)),
        )


# --------------------------------------------------------------------------
# Remediation cycle tracking (Stage 4 Tier S, 2026-05-18)
# --------------------------------------------------------------------------

# Valid `status` values for RemediationCycle entries.
#   "in_progress"     — cycle started, manuscript snapshot saved, LLM
#                       call may or may not have completed; presence
#                       on disk implies the cycle was launched but
#                       not yet wrapped (crash recovery: re-attempt).
#   "completed"       — cycle finished cleanly; manuscript was rewritten;
#                       costs and p0_after captured.
#   "aborted-budget"  — circuit breaker fired (max_cost_usd exceeded)
#                       partway through the cycle.
#   "aborted-error"   — LLM invocation returned non-zero or another
#                       fatal error; manuscript may be unchanged from
#                       pre-cycle iter_N snapshot.
VALID_REMEDIATION_CYCLE_STATUSES = frozenset(
    {
        "in_progress",
        "completed",
        "aborted-budget",
        "aborted-error",
    }
)


@dataclass
class RemediationCycle:
    """One pass through the Stage 4 Tier S P0 remediation loop.

    Created at the start of each cycle by ``phase_remediate``; updated
    in place when the cycle finishes (or aborts). The list lives on
    DraftState.remediation_cycles and is append-only — abort entries
    are retained, never overwritten, so the audit trail captures every
    attempt.

    Fields:
      cycle_n          — 1-indexed sequence number. cycle_n == 1 is
                         the first remediation pass; cycle_n equals
                         ``len(state.remediation_cycles)`` at entry.
      ts_start         — ISO 8601 UTC string when the cycle began.
      ts_end           — ISO 8601 UTC string when status moved to
                         completed/aborted-*; None while in_progress.
      drafter_cost_usd — Cost of the remediation_draft.v1 LLM call;
                         0.0 if envelope didn't parse or the call
                         hadn't fired yet at abort time.
      review_cost_usd  — Cost of the SUBSEQUENT phase_review cascade
                         (Tier 3 canonical adversarial dominates).
                         Populated lazily by phase_p0_review when it
                         observes a freshly-completed cycle. None
                         until the next review pass concludes.
      p0_before        — Total P0 count across both audit JSONs at
                         cycle entry. Source of truth for the
                         "remediation_progress" log line.
      p0_after         — Total P0 count at the end of the next
                         phase_review. None until phase_p0_review
                         backfills it on the subsequent gate
                         evaluation. A None here on a `completed`
                         cycle means the next review has not yet
                         fired.
      p0_before_by_source / p0_after_by_source —
                         dict[str, int] keyed on
                         {"adversarial", "numeric_grounding"}.
                         Enables per-source convergence tracking
                         (e.g., spotting if remediation closes
                         numeric-grounding findings but the
                         adversarial side stays stuck).
      manuscript_pre_path — Relative path to manuscript.iter_N.md
                            (the manuscript BEFORE this cycle's LLM
                            rewrite). Allows diffing.
      audit_snapshot_dir  — Relative path to audit/iter_N/ (the
                            JSON snapshot of the audit findings
                            that triggered this cycle).
      status            — One of VALID_REMEDIATION_CYCLE_STATUSES.
      note              — Free-text note; on abort, captures the
                          failure mode (e.g., "claude exit 1",
                          "circuit breaker $5.42 >= $5.00").
    """

    cycle_n: int
    ts_start: str
    ts_end: Optional[str] = None
    drafter_cost_usd: float = 0.0
    review_cost_usd: Optional[float] = None
    p0_before: int = 0
    p0_after: Optional[int] = None
    p0_before_by_source: dict[str, int] = field(default_factory=dict)
    p0_after_by_source: Optional[dict[str, int]] = None
    manuscript_pre_path: str = ""
    audit_snapshot_dir: str = ""
    status: str = "in_progress"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RemediationCycle:
        return cls(
            cycle_n=int(d.get("cycle_n", 0)),
            ts_start=str(d.get("ts_start", "")),
            ts_end=d.get("ts_end"),
            drafter_cost_usd=float(d.get("drafter_cost_usd", 0.0)),
            review_cost_usd=(
                float(d["review_cost_usd"])
                if d.get("review_cost_usd") is not None
                else None
            ),
            p0_before=int(d.get("p0_before", 0)),
            p0_after=(
                int(d["p0_after"])
                if d.get("p0_after") is not None
                else None
            ),
            p0_before_by_source=dict(d.get("p0_before_by_source", {})),
            p0_after_by_source=(
                dict(d["p0_after_by_source"])
                if d.get("p0_after_by_source") is not None
                else None
            ),
            manuscript_pre_path=str(d.get("manuscript_pre_path", "")),
            audit_snapshot_dir=str(d.get("audit_snapshot_dir", "")),
            status=str(d.get("status", "in_progress")),
            note=str(d.get("note", "")),
        )


# --------------------------------------------------------------------------
# Validator status
# --------------------------------------------------------------------------

VALID_VALIDATOR_STATUSES = frozenset(
    {
        "pass",
        "fail",
        "soft-warning",
        "escalated",
        "user-fixed",
        "accepted-as-limitation",
        "not-applicable",  # e.g. M2 (Structured Abstract) in --mode report
    }
)


# --------------------------------------------------------------------------
# Top-level state
# --------------------------------------------------------------------------

@dataclass
class DraftState:
    """The full state.json contents for one paper_draft_N/ directory."""

    version: str = STATE_SCHEMA_VERSION
    project_id: str = ""
    draft_number: int = 1
    phase: str = "init"
    mode: str = "paper"  # "paper" | "report"
    tier: Optional[str] = None  # "STRONG" | "THIN" | "EXPLORATORY" | None until triage

    throughline: ThroughlineState = field(default_factory=ThroughlineState)
    source_artifacts: list[ArtifactHash] = field(default_factory=list)
    manuscript_files: list[ManuscriptFile] = field(default_factory=list)
    analysis_requests: list[AnalysisRequest] = field(default_factory=list)
    iteration: IterationCounters = field(default_factory=IterationCounters)
    validator_status: dict[str, str] = field(default_factory=dict)
    # Stage 4 Tier S (2026-05-18): one entry per remediation pass through
    # the P0 gate. Append-only; aborted entries retained for audit. See
    # RemediationCycle docstring for field semantics.
    remediation_cycles: list[RemediationCycle] = field(default_factory=list)
    cost_so_far_usd: float = 0.0
    elapsed_seconds: float = 0.0
    last_updated: Optional[str] = None  # ISO 8601 UTC

    def touch(self) -> None:
        """Update last_updated to now."""
        self.last_updated = _utcnow_isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project_id": self.project_id,
            "draft_number": self.draft_number,
            "phase": self.phase,
            "mode": self.mode,
            "tier": self.tier,
            "throughline": self.throughline.to_dict(),
            "source_artifacts": [a.to_dict() for a in self.source_artifacts],
            "manuscript_files": [m.to_dict() for m in self.manuscript_files],
            "analysis_requests": [r.to_dict() for r in self.analysis_requests],
            "iteration": self.iteration.to_dict(),
            "validator_status": dict(self.validator_status),
            "remediation_cycles": [
                c.to_dict() for c in self.remediation_cycles
            ],
            "cost_so_far_usd": self.cost_so_far_usd,
            "elapsed_seconds": self.elapsed_seconds,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DraftState:
        version = str(d.get("version", STATE_SCHEMA_VERSION))
        if version != STATE_SCHEMA_VERSION:
            # Forward-compat: load anyway but warn loudly. A future major bump
            # will make this raise.
            import sys
            print(
                f"warning: state.json version is {version!r}; "
                f"expected {STATE_SCHEMA_VERSION!r}. Loading optimistically.",
                file=sys.stderr,
            )
        return cls(
            version=version,
            project_id=str(d.get("project_id", "")),
            draft_number=int(d.get("draft_number", 1)),
            phase=str(d.get("phase", "init")),
            mode=str(d.get("mode", "paper")),
            tier=d.get("tier"),
            throughline=ThroughlineState.from_dict(d.get("throughline", {})),
            source_artifacts=[
                ArtifactHash.from_dict(a) for a in d.get("source_artifacts", [])
            ],
            manuscript_files=[
                ManuscriptFile.from_dict(m) for m in d.get("manuscript_files", [])
            ],
            analysis_requests=[
                AnalysisRequest.from_dict(r) for r in d.get("analysis_requests", [])
            ],
            iteration=IterationCounters.from_dict(d.get("iteration", {})),
            validator_status=dict(d.get("validator_status", {})),
            remediation_cycles=[
                RemediationCycle.from_dict(c)
                for c in d.get("remediation_cycles", [])
            ],
            cost_so_far_usd=float(d.get("cost_so_far_usd", 0.0)),
            elapsed_seconds=float(d.get("elapsed_seconds", 0.0)),
            last_updated=d.get("last_updated"),
        )


# --------------------------------------------------------------------------
# Disk I/O
# --------------------------------------------------------------------------

def state_path(draft_dir: Path) -> Path:
    """Canonical location of state.json within a draft directory."""
    return draft_dir / "state.json"


def load_state(draft_dir: Path) -> DraftState:
    """Load state.json from a draft directory.

    Returns a fresh `DraftState` if the file does not exist (caller must
    populate `project_id`, `draft_number`, etc. before saving). Use
    `state_path(draft_dir).is_file()` first if "fresh-start vs resume"
    matters semantically.
    """
    p = state_path(draft_dir)
    if not p.is_file():
        return DraftState()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return DraftState.from_dict(raw)


def save_state(draft_dir: Path, state: DraftState) -> None:
    """Write state.json atomically. Creates draft_dir if missing."""
    draft_dir.mkdir(parents=True, exist_ok=True)
    state.touch()
    target = state_path(draft_dir)
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
    # Atomic write: write to temp file in same dir, then rename. Same-FS
    # rename on POSIX is atomic from the perspective of readers.
    fd, tmp = tempfile.mkstemp(
        prefix=".state.", suffix=".json.tmp", dir=str(draft_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, target)
    except Exception:
        # Best-effort cleanup of the tempfile on any error.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _utcnow_isoformat() -> str:
    """ISO 8601 timestamp in UTC with second precision, ending in Z."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def hash_artifact_set(artifacts: list[ArtifactHash]) -> str:
    """Single sha256 over a sorted-by-path concatenation of all hashes.

    Used as `throughline.artifact_hash_at_confirmation` to detect later
    whether the artifact set has changed since the throughline was chosen.
    """
    h = hashlib.sha256()
    for a in sorted(artifacts, key=lambda x: x.path):
        h.update(f"{a.path}\0{a.sha256}\0".encode("utf-8"))
    return h.hexdigest()
