"""C1-B (paper-writer): the shared stage-output completion guard
`_assert_artifact_fresh`.

rc==0 from a claude -p subprocess is NOT proof the artifact landed (the
subprocess can emit a Write event that didn't write, or be
content-filter-blocked after a partial write). The guard is the
file-is-the-contract check (``[[feedback_subprocess_mtime_contract]]``):
exists + mtime>=subprocess_start + no surviving in-progress sentinel.

This lifts the previously adversarial-only mtime check into ONE reusable
guard every artifact-writing phase can call (brief C1-B: generalize). The
holistic-drafting phase now hard-fails on a missing manuscript instead of
silently proceeding.

Pure-method tests over a real orchestrator (no LLM fires).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from beril_paper_writer.orchestrator import PaperWriterOrchestrator


@pytest.fixture
def draft_dir(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    draft = proj / "papers" / "draft_1"
    draft.mkdir(parents=True)
    (proj / "REPORT.md").write_text("x", encoding="utf-8")
    (proj / "RESEARCH_PLAN.md").write_text("x", encoding="utf-8")
    (proj / "methods_provenance.md").write_text("x", encoding="utf-8")
    return draft


@pytest.fixture
def orch(draft_dir: Path) -> PaperWriterOrchestrator:
    return PaperWriterOrchestrator(draft_dir=draft_dir)


def test_missing_artifact_fails(orch, draft_dir):
    start = time.time()
    ok, reason = orch._assert_artifact_fresh(
        path=draft_dir / "manuscript.md", subprocess_start=start,
        label="phase_drafting")
    assert ok is False
    assert "does not exist" in reason and "rc==0" in reason


def test_fresh_artifact_passes(orch, draft_dir):
    start = time.time()
    time.sleep(0.01)
    (draft_dir / "manuscript.md").write_text("# Real manuscript\n",
                                             encoding="utf-8")
    ok, reason = orch._assert_artifact_fresh(
        path=draft_dir / "manuscript.md", subprocess_start=start,
        label="phase_drafting")
    assert ok is True and reason == ""


def test_stale_artifact_fails(orch, draft_dir):
    """An output whose mtime predates the subprocess start (a stale
    leftover from a prior run) is a failure — the subprocess didn't
    rewrite it."""
    m = draft_dir / "manuscript.md"
    m.write_text("# Stale\n", encoding="utf-8")
    old = time.time() - 100
    os.utime(m, (old, old))
    start = time.time()
    ok, reason = orch._assert_artifact_fresh(
        path=m, subprocess_start=start, label="phase_drafting")
    assert ok is False
    assert "PREDATES the subprocess start" in reason


def test_surviving_sentinel_fails(orch, draft_dir):
    """A surviving in-progress sentinel = the real write never landed."""
    s = draft_dir / "citation_pool.json"
    s.write_text('{"status": "in_progress"}', encoding="utf-8")
    start = time.time() - 1.0  # sentinel is 'fresh' on mtime, but is a sentinel
    ok, reason = orch._assert_artifact_fresh(
        path=s, subprocess_start=start, label="phase_citation_pool")
    assert ok is False
    assert "sentinel survived" in reason


def test_drafting_hard_fails_on_missing_manuscript_source():
    """C1-B source pin: phase_drafting_concurrent must hard-fail (raise)
    when the manuscript didn't land after a claimed-success draft call —
    not silently proceed. (The behavioral test would need a mocked LLM;
    this pins the guard call + the raise.)"""
    import inspect
    src = inspect.getsource(
        PaperWriterOrchestrator.phase_drafting_concurrent)
    assert "_assert_artifact_fresh" in src, (
        "phase_drafting must call the shared freshness guard")
    # the guard's negative branch raises (hard-fail), not warn-and-proceed
    assert "manuscript did not" in src and "raise RuntimeError" in src, (
        "phase_drafting must raise (hard-fail) when the manuscript "
        "didn't land")
    # the guard call + the raise are both present after the rc check
    assert src.index("_assert_artifact_fresh") < src.index(
        "manuscript did not"), "guard must precede the hard-fail raise"


def test_adversarial_check_delegates_to_shared_guard():
    """C1-B: _check_adversarial_output_fresh now delegates to the shared
    _assert_artifact_fresh (one implementation, not forked)."""
    import inspect
    src = inspect.getsource(
        PaperWriterOrchestrator._check_adversarial_output_fresh)
    assert "_assert_artifact_fresh" in src, (
        "the adversarial freshness check must delegate to the shared guard"
    )
