"""Tests for beril_paper_writer.state — state.json + intercalation hash-diff.

Coverage:
  - Round-trip: DraftState → dict → DraftState equivalence
  - Atomic write + load
  - Hash computation: deterministic, content-sensitive
  - Diff: added / removed / changed / unchanged classification
  - User-edit detection: only for previously writer-generated files
  - Throughline reevaluation tracking
  - Validator-status enum coverage
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beril_paper_writer import state


# --------------------------------------------------------------------------
# Hash computation
# --------------------------------------------------------------------------

def test_hash_file_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hello world\n", encoding="utf-8")
    assert state.hash_file(f) == state.hash_file(f)


def test_hash_file_changes_with_content(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("alpha\n", encoding="utf-8")
    h1 = state.hash_file(a)
    a.write_text("beta\n", encoding="utf-8")
    h2 = state.hash_file(a)
    assert h1 != h2


def test_compute_artifact_hashes_skips_missing(tmp_path: Path) -> None:
    f = tmp_path / "exists.txt"
    f.write_text("yes\n", encoding="utf-8")
    out = state.compute_artifact_hashes(tmp_path, ["exists.txt", "missing.txt"])
    assert len(out) == 1
    assert out[0].path == "exists.txt"


def test_compute_artifact_hashes_normalizes_separators(tmp_path: Path) -> None:
    """Path field always uses forward slashes regardless of OS separator."""
    sub = tmp_path / "notebooks"
    sub.mkdir()
    f = sub / "x.ipynb"
    f.write_text("{}\n", encoding="utf-8")
    # Pass an OS-style relative path that may contain backslashes on Windows;
    # on POSIX this is a no-op but keeps the test cross-platform.
    out = state.compute_artifact_hashes(tmp_path, ["notebooks/x.ipynb"])
    assert len(out) == 1
    assert "/" in out[0].path
    assert "\\" not in out[0].path


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

def _ah(path: str, sha: str = "0" * 64, mtime: float = 0.0, size: int = 0) -> state.ArtifactHash:
    return state.ArtifactHash(path=path, sha256=sha, mtime=mtime, size_bytes=size)


def test_diff_added_removed_changed_unchanged() -> None:
    prev = [
        _ah("a.txt", sha="aaa"),
        _ah("b.txt", sha="bbb"),
        _ah("c.txt", sha="ccc"),
    ]
    curr = [
        _ah("a.txt", sha="aaa"),    # unchanged
        _ah("b.txt", sha="BBB"),    # changed
        # c.txt removed
        _ah("d.txt", sha="ddd"),    # added
    ]
    diff = state.diff_artifacts(prev, curr)
    assert diff.added == ("d.txt",)
    assert diff.removed == ("c.txt",)
    assert diff.changed == ("b.txt",)
    assert diff.unchanged == ("a.txt",)
    assert diff.has_changes


def test_diff_no_changes_returns_empty_categories() -> None:
    a = [_ah("a.txt", sha="aaa"), _ah("b.txt", sha="bbb")]
    diff = state.diff_artifacts(a, a)
    assert not diff.has_changes
    assert diff.unchanged == ("a.txt", "b.txt")
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.changed == ()


def test_diff_summary_format() -> None:
    prev = [_ah("a.txt", sha="aaa")]
    curr = [_ah("a.txt", sha="AAA"), _ah("b.txt", sha="bbb")]
    diff = state.diff_artifacts(prev, curr)
    summary = diff.summary()
    assert "1 added" in summary
    assert "0 removed" in summary
    assert "1 changed" in summary
    assert "0 unchanged" in summary


# --------------------------------------------------------------------------
# User-edit detection
# --------------------------------------------------------------------------

def test_user_edited_only_when_writer_generated_and_changed() -> None:
    prev = [_ah("01_methods.md", sha="orig")]
    curr = [_ah("01_methods.md", sha="modified")]
    assert state.is_user_edited("01_methods.md", prev, curr, writer_generated=True) is True


def test_user_edited_false_when_unchanged() -> None:
    prev = [_ah("01_methods.md", sha="same")]
    curr = [_ah("01_methods.md", sha="same")]
    assert state.is_user_edited("01_methods.md", prev, curr, writer_generated=True) is False


def test_user_edited_false_when_writer_did_not_generate() -> None:
    """User-authored files (e.g., RESEARCH_PLAN.md) are not "user-edited" in
    the writer's sense — they're always under user control."""
    prev = [_ah("RESEARCH_PLAN.md", sha="v1")]
    curr = [_ah("RESEARCH_PLAN.md", sha="v2")]
    assert state.is_user_edited("RESEARCH_PLAN.md", prev, curr, writer_generated=False) is False


def test_user_edited_false_when_path_missing_either_side() -> None:
    prev: list[state.ArtifactHash] = []
    curr = [_ah("x.md", sha="aaa")]
    assert state.is_user_edited("x.md", prev, curr, writer_generated=True) is False


# --------------------------------------------------------------------------
# Round-trip serialization
# --------------------------------------------------------------------------

def test_draft_state_roundtrip_empty() -> None:
    s = state.DraftState()
    d = s.to_dict()
    s2 = state.DraftState.from_dict(d)
    assert s2.version == state.STATE_SCHEMA_VERSION
    assert s2.phase == "init"
    assert s2.mode == "paper"
    assert s2.tier is None
    assert s2.throughline.candidate_id is None
    assert s2.iteration.gap_fill_rounds == 0


def test_draft_state_roundtrip_populated() -> None:
    s = state.DraftState(
        project_id="functional_dark_matter",
        draft_number=2,
        phase="drafting",
        mode="paper",
        tier="STRONG",
    )
    s.throughline.candidate_id = "TL2"
    s.throughline.chosen_at = "2026-04-25T14:32:00Z"
    s.throughline.revision = 1
    s.throughline.artifact_hash_at_confirmation = "deadbeef" * 8
    s.throughline.reevaluations.append(
        state.ThroughlineReevaluation(
            round=1,
            at="2026-04-25T15:10:00Z",
            artifact_change_detected=True,
            changed_paths=("REPORT.md",),
            user_prompt_shown="confirm?",
            outcome="confirmed-still-valid",
        )
    )
    s.source_artifacts.append(_ah("REPORT.md", sha="abc", mtime=1.0, size=10))
    s.manuscript_files.append(
        state.ManuscriptFile(
            path="01_methods.md", sha256="def", writer_generated=True, user_edited=False
        )
    )
    s.analysis_requests.append(
        state.AnalysisRequest(
            id="REQ-1", type="analysis-request", status="pending", originated_at_round=1
        )
    )
    s.iteration.rewrite_passes = 1
    s.iteration.gap_fill_rounds = 1
    s.validator_status["M1"] = "pass"
    s.validator_status["M6"] = "escalated"
    s.cost_so_far_usd = 3.42
    s.elapsed_seconds = 1240.0

    d = s.to_dict()
    s2 = state.DraftState.from_dict(d)

    assert s2.project_id == "functional_dark_matter"
    assert s2.tier == "STRONG"
    assert s2.throughline.candidate_id == "TL2"
    assert len(s2.throughline.reevaluations) == 1
    assert s2.throughline.reevaluations[0].outcome == "confirmed-still-valid"
    assert s2.source_artifacts[0].sha256 == "abc"
    assert s2.manuscript_files[0].writer_generated is True
    assert s2.analysis_requests[0].status == "pending"
    assert s2.iteration.gap_fill_rounds == 1
    assert s2.validator_status["M6"] == "escalated"
    assert s2.cost_so_far_usd == pytest.approx(3.42)


# --------------------------------------------------------------------------
# Disk I/O
# --------------------------------------------------------------------------

def test_load_state_returns_fresh_when_file_missing(tmp_path: Path) -> None:
    s = state.load_state(tmp_path)
    assert isinstance(s, state.DraftState)
    assert s.project_id == ""
    assert s.draft_number == 1


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    s = state.DraftState(project_id="test", draft_number=5, phase="drafting")
    state.save_state(tmp_path, s)
    p = state.state_path(tmp_path)
    assert p.is_file()
    loaded = state.load_state(tmp_path)
    assert loaded.project_id == "test"
    assert loaded.draft_number == 5
    assert loaded.phase == "drafting"
    # touch() should have set last_updated
    assert loaded.last_updated is not None
    assert loaded.last_updated.endswith("Z")


def test_save_state_is_atomic_no_partial_files(tmp_path: Path) -> None:
    """A successful save leaves no .state.*.tmp temp files behind."""
    s = state.DraftState(project_id="x")
    state.save_state(tmp_path, s)
    leftover = list(tmp_path.glob(".state.*.tmp"))
    assert leftover == []


def test_save_state_writes_json_indented(tmp_path: Path) -> None:
    """state.json is human-readable (indented) so users can eyeball it."""
    s = state.DraftState(project_id="x")
    state.save_state(tmp_path, s)
    raw = state.state_path(tmp_path).read_text(encoding="utf-8")
    assert "\n" in raw
    assert raw.endswith("\n")
    # Sort_keys is on, so the version field is alphabetically late but present.
    parsed = json.loads(raw)
    assert parsed["version"] == state.STATE_SCHEMA_VERSION


# --------------------------------------------------------------------------
# Artifact-set hashing (used for throughline confirmation hash)
# --------------------------------------------------------------------------

def test_hash_artifact_set_order_independent() -> None:
    a = [_ah("a.txt", sha="aaa"), _ah("b.txt", sha="bbb")]
    b = [_ah("b.txt", sha="bbb"), _ah("a.txt", sha="aaa")]
    assert state.hash_artifact_set(a) == state.hash_artifact_set(b)


def test_hash_artifact_set_changes_when_content_changes() -> None:
    a = [_ah("a.txt", sha="aaa")]
    b = [_ah("a.txt", sha="bbb")]
    assert state.hash_artifact_set(a) != state.hash_artifact_set(b)


def test_hash_artifact_set_changes_when_path_added() -> None:
    a = [_ah("a.txt", sha="aaa")]
    b = [_ah("a.txt", sha="aaa"), _ah("new.txt", sha="nnn")]
    assert state.hash_artifact_set(a) != state.hash_artifact_set(b)


# --------------------------------------------------------------------------
# Schema enums (sanity)
# --------------------------------------------------------------------------

def test_valid_phases_complete() -> None:
    expected = {
        "init", "triage", "throughline_pick", "drafting",
        "citation_pool", "review", "rewrite", "assembled",
    }
    assert state.VALID_PHASES == expected


def test_valid_request_types_match_spec() -> None:
    """Per SPEC §5.2, request types are one of these five."""
    expected = {
        "analysis-request", "figure-request", "data-request",
        "citation-request", "validator-escalation",
    }
    assert state.VALID_REQUEST_TYPES == expected


def test_valid_validator_statuses_include_all_paths() -> None:
    """Per SPEC §7.1.1: pass / fail / soft-warning / escalated /
    user-fixed / accepted-as-limitation, plus not-applicable for mode-skip."""
    expected = {
        "pass", "fail", "soft-warning", "escalated",
        "user-fixed", "accepted-as-limitation", "not-applicable",
    }
    assert state.VALID_VALIDATOR_STATUSES == expected
