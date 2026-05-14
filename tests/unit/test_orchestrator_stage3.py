"""Tests for Stage 3 additions to PaperWriterOrchestrator.

Covers:
  - `_stage_figures_for_assemble`: symlink-or-copy of <project>/figures
    into <draft_dir>/figures so the renderer's relative-path contract
    resolves. Idempotent re-runs leave correct symlinks alone.
  - `_classify_tier_from_candidates`: tier extraction from
    throughline_candidates.md, with EXPLORATORY default and respect
    for explicit user-set tier.

Background. Both methods address bugs surfaced by draft_9 of the
ibd_phage_targeting smoke run:
  * Figures rendered as `[FIGURE MISSING: ...]` placeholders because
    `figures/X.png` resolves against `<draft_dir>/` (which has no
    figures dir) instead of `<project>/figures/`. Pre-v0.7.x masking
    came from the LLM coincidentally wrapping images in blockquotes,
    which the renderer silently dropped as prose — every draft was
    figureless without anyone noticing.
  * state.tier left None because the Python orchestrator did not call
    the v0.6.4 extract-tier helper. Adversarial reviewer + word-budget
    prompts then defaulted to EXPLORATORY regardless of plan-phase
    rigor verdict.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the orchestrator. Tests do not invoke the network/LLM path —
# they exercise the deterministic helpers only.
from beril_paper_writer.orchestrator import PaperWriterOrchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_figures(tmp_path: Path) -> Path:
    """Minimal BERDL-like project layout for tier + figure-staging tests."""
    proj = tmp_path / "test_project"
    proj.mkdir()

    # Canonical figures dir at project root.
    figs = proj / "figures"
    figs.mkdir()
    (figs / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n_stub_1")
    (figs / "fig2.png").write_bytes(b"\x89PNG\r\n\x1a\n_stub_2")

    # Empty REPORT.md so the tier classifier's preconditions pass.
    (proj / "REPORT.md").write_text("# Test project\n", encoding="utf-8")

    # papers/draft_1/ subdir for the orchestrator's draft_dir contract.
    (proj / "papers").mkdir()
    (proj / "papers" / "draft_1").mkdir()

    return proj


@pytest.fixture
def orch(project_with_figures: Path) -> PaperWriterOrchestrator:
    """Construct an orchestrator pointed at the fixture project's draft_1."""
    draft_dir = project_with_figures / "papers" / "draft_1"
    return PaperWriterOrchestrator(draft_dir=draft_dir)


# ---------------------------------------------------------------------------
# _stage_figures_for_assemble
# ---------------------------------------------------------------------------


def test_stage_figures_creates_symlink_when_missing(
    orch: PaperWriterOrchestrator,
) -> None:
    """Fresh run creates a symlink that points at <project>/figures."""
    staged = orch.draft_dir / "figures"
    assert not staged.exists()

    orch._stage_figures_for_assemble()

    assert staged.is_symlink()
    assert staged.resolve() == (orch.project_dir / "figures").resolve()
    # The figures must be reachable through the staged path — that is
    # the actual renderer contract we are protecting.
    assert (staged / "fig1.png").is_file()
    assert (staged / "fig2.png").is_file()


def test_stage_figures_idempotent_correct_symlink(
    orch: PaperWriterOrchestrator,
) -> None:
    """Re-running on a correctly-staged dir is a no-op (no error, no clobber)."""
    orch._stage_figures_for_assemble()
    inode_before = (orch.draft_dir / "figures").lstat().st_ino

    # Re-run.
    orch._stage_figures_for_assemble()
    inode_after = (orch.draft_dir / "figures").lstat().st_ino

    assert inode_before == inode_after
    assert (orch.draft_dir / "figures").is_symlink()


def test_stage_figures_replaces_wrong_target_symlink(
    orch: PaperWriterOrchestrator,
    tmp_path: Path,
) -> None:
    """A symlink pointing at the wrong dir is replaced with a correct one."""
    decoy = tmp_path / "wrong_dir"
    decoy.mkdir()
    staged = orch.draft_dir / "figures"
    staged.symlink_to(decoy)
    assert staged.resolve() == decoy.resolve()

    orch._stage_figures_for_assemble()

    assert staged.is_symlink()
    assert staged.resolve() == (orch.project_dir / "figures").resolve()


def test_stage_figures_preserves_real_directory(
    orch: PaperWriterOrchestrator,
) -> None:
    """A pre-existing real directory is left alone (user-managed)."""
    staged = orch.draft_dir / "figures"
    staged.mkdir()
    user_file = staged / "user_edit.png"
    user_file.write_bytes(b"do_not_touch")

    orch._stage_figures_for_assemble()

    # Still a real directory, not a symlink, and the user file survives.
    assert staged.is_dir()
    assert not staged.is_symlink()
    assert user_file.read_bytes() == b"do_not_touch"


def test_stage_figures_refuses_to_clobber_non_dir_non_link(
    orch: PaperWriterOrchestrator,
) -> None:
    """A regular file at the staged path is a hard error (not a silent drop)."""
    staged = orch.draft_dir / "figures"
    staged.write_text("oops_a_file", encoding="utf-8")

    with pytest.raises(RuntimeError, match="figures"):
        orch._stage_figures_for_assemble()


def test_stage_figures_no_project_figures_dir_logs_warning(
    project_with_figures: Path,
) -> None:
    """Project with no figures/ dir: warn + return, do not crash."""
    # Remove the figures dir to simulate a notebook-only project.
    figs = project_with_figures / "figures"
    for child in figs.iterdir():
        child.unlink()
    figs.rmdir()

    draft_dir = project_with_figures / "papers" / "draft_1"
    orch = PaperWriterOrchestrator(draft_dir=draft_dir)
    # No raise; no staged dir created.
    orch._stage_figures_for_assemble()
    assert not (draft_dir / "figures").exists()


def test_stage_figures_falls_back_to_copy_when_symlink_fails(
    orch: PaperWriterOrchestrator,
) -> None:
    """When os.symlink raises (e.g., Windows without dev mode), fall back to copytree."""
    staged = orch.draft_dir / "figures"

    # Patch Path.symlink_to to raise OSError; the copy fallback must
    # produce a real directory with the same files.
    real_symlink_to = Path.symlink_to

    def boom(self, *args, **kwargs):  # noqa: ANN001 - test stub
        raise OSError("simulated symlink-not-permitted")

    with patch.object(Path, "symlink_to", boom):
        orch._stage_figures_for_assemble()

    assert staged.is_dir()
    assert not staged.is_symlink()
    assert (staged / "fig1.png").read_bytes().endswith(b"_stub_1")
    assert (staged / "fig2.png").read_bytes().endswith(b"_stub_2")

    # Sanity: restore the real method (patch context already did this,
    # but assert behavior is back to normal so later tests are unaffected).
    assert Path.symlink_to is real_symlink_to


# ---------------------------------------------------------------------------
# _classify_tier_from_candidates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict,expected",
    [
        ("STRONG", "STRONG"),
        ("THIN", "THIN"),
        ("EXPLORATORY", "EXPLORATORY"),
    ],
)
def test_classify_tier_reads_structured_header(
    orch: PaperWriterOrchestrator, verdict: str, expected: str,
) -> None:
    """Standard `**Tier:** X` header produces the right state.tier."""
    candidates = textwrap.dedent(f"""\
        # Throughline Candidates
        ## Triage
        **Tier:** {verdict}
        **Recommended mode:** paper
    """)
    (orch.draft_dir / "throughline_candidates.md").write_text(
        candidates, encoding="utf-8",
    )
    # State starts with tier=None.
    assert orch.state.tier is None
    orch._classify_tier_from_candidates()
    assert orch.state.tier == expected
    # Persisted to state.json.
    on_disk = json.loads((orch.draft_dir / "state.json").read_text())
    assert on_disk["tier"] == expected


def test_classify_tier_missing_file_defaults_exploratory(
    orch: PaperWriterOrchestrator,
) -> None:
    """No throughline_candidates.md → conservative default EXPLORATORY."""
    assert orch.state.tier is None
    orch._classify_tier_from_candidates()
    assert orch.state.tier == "EXPLORATORY"


def test_classify_tier_no_verdict_in_file_defaults_exploratory(
    orch: PaperWriterOrchestrator,
) -> None:
    """File present but lacking a tier line → EXPLORATORY (conservative)."""
    (orch.draft_dir / "throughline_candidates.md").write_text(
        "# Throughline Candidates\n\nNo tier verdict here.\n",
        encoding="utf-8",
    )
    orch._classify_tier_from_candidates()
    assert orch.state.tier == "EXPLORATORY"


def test_classify_tier_preserves_explicit_user_set_tier(
    orch: PaperWriterOrchestrator,
) -> None:
    """If state.tier is already set (e.g., user override), do not clobber it."""
    orch.state.tier = "THIN"
    (orch.draft_dir / "throughline_candidates.md").write_text(
        "**Tier:** STRONG\n", encoding="utf-8",
    )
    orch._classify_tier_from_candidates()
    # User's THIN wins over the file's STRONG.
    assert orch.state.tier == "THIN"
