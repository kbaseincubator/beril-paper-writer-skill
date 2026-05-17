"""Tests for Stage 3 additions to PaperWriterOrchestrator.

Covers:
  - `_stage_figures_for_assemble`: symlink-or-copy of <project>/figures
    into <draft_dir>/figures so the renderer's relative-path contract
    resolves. Idempotent re-runs leave correct symlinks alone.
  - `_classify_tier_from_candidates`: tier extraction from
    throughline_candidates.md, with EXPLORATORY default and respect
    for explicit user-set tier.
  - `resolve_claude_bin`: absolute-path resolution of the `claude` CLI
    (Tier J) — BERIL_CLAUDE_BIN override, PATH lookup, well-known
    locations, loud failure listing what was searched.

Background. These address bugs surfaced by draft_9–draft_13 of the
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
  * Backgrounded `beril-paper-writer` raised `FileNotFoundError:
    'claude'` while the identical foreground command succeeded — the
    orchestrator spawned `claude` by bare name, relying on a PATH
    lookup that the launch context did not satisfy. resolve_claude_bin
    pins it to an absolute path so the spawn is context-independent.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the orchestrator. Tests do not invoke the network/LLM path —
# they exercise the deterministic helpers only.
from beril_paper_writer.orchestrator import (
    PaperWriterOrchestrator,
    resolve_claude_bin,
    resolve_adversarial_bin,
)


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
    """A pre-existing real directory with content is left alone (user-managed)."""
    staged = orch.draft_dir / "figures"
    staged.mkdir()
    user_file = staged / "user_edit.png"
    user_file.write_bytes(b"do_not_touch")

    orch._stage_figures_for_assemble()

    # Still a real directory, not a symlink, and the user file survives.
    assert staged.is_dir()
    assert not staged.is_symlink()
    assert user_file.read_bytes() == b"do_not_touch"


def test_stage_figures_replaces_empty_real_directory(
    orch: PaperWriterOrchestrator,
) -> None:
    """Tier J.1: an empty pre-existing real directory is replaced with the
    project-figures symlink. Empty dirs are almost always side effects of
    earlier pipeline phases (e.g. extract_figures --output-dir <draft_dir>),
    not user content. Deferring to them makes Tier A a no-op and the
    renderer warns `image file not found` for every figure (observed on
    draft_1 of ibd_phage_targeting, 2026-05-15)."""
    staged = orch.draft_dir / "figures"
    staged.mkdir()
    assert staged.is_dir() and not staged.is_symlink()
    # Sanity: an empty dir.
    assert not any(staged.iterdir())

    orch._stage_figures_for_assemble()

    # Now a symlink to the project's figures, and a sample file is reachable.
    assert staged.is_symlink()
    assert staged.resolve() == (orch.project_dir / "figures").resolve()
    assert (staged / "fig1.png").is_file()


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


# ---------------------------------------------------------------------------
# resolve_claude_bin — Tier J
# ---------------------------------------------------------------------------


def test_resolve_claude_bin_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BERIL_CLAUDE_BIN pointing at a real file wins over everything else."""
    fake_claude = tmp_path / "my-claude"
    fake_claude.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("BERIL_CLAUDE_BIN", str(fake_claude))
    # Even if PATH has a different claude, the override wins.
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which",
        lambda name: "/somewhere/else/claude",
    )
    assert resolve_claude_bin() == str(fake_claude.resolve())


def test_resolve_claude_bin_env_override_not_a_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BERIL_CLAUDE_BIN set to a non-file is a loud error, not a silent fallthrough."""
    monkeypatch.setenv("BERIL_CLAUDE_BIN", str(tmp_path / "does-not-exist"))
    with pytest.raises(RuntimeError, match="BERIL_CLAUDE_BIN"):
        resolve_claude_bin()


def test_resolve_claude_bin_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no override, shutil.which result is used."""
    monkeypatch.delenv("BERIL_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    assert resolve_claude_bin() == "/usr/local/bin/claude"


def test_resolve_claude_bin_unresolvable_raises_with_searched_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No override, not on PATH, not in well-known locations → loud RuntimeError
    that names what was searched (so the operator can act on it)."""
    monkeypatch.delenv("BERIL_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which", lambda name: None,
    )
    # Point Path.home() at an empty dir, and make every candidate path
    # report "not a file" — the well-known list has hard-coded absolute
    # paths (e.g. /usr/local/bin/claude) that may genuinely exist in the
    # test environment, so isolating via Path.home() alone is not enough.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(RuntimeError) as exc:
        resolve_claude_bin()
    msg = str(exc.value)
    assert "Cannot locate the `claude` CLI" in msg
    assert "Searched:" in msg
    assert "BERIL_CLAUDE_BIN" in msg


# ---------------------------------------------------------------------------
# resolve_adversarial_bin — Tier K
# ---------------------------------------------------------------------------


def test_resolve_adversarial_bin_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BERIL_ADVERSARIAL_BIN pointing at a real file wins over PATH."""
    fake_adv = tmp_path / "my-beril-adversarial"
    fake_adv.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("BERIL_ADVERSARIAL_BIN", str(fake_adv))
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which",
        lambda name: "/somewhere/else/beril-adversarial",
    )
    assert resolve_adversarial_bin() == str(fake_adv.resolve())


def test_resolve_adversarial_bin_env_override_not_a_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BERIL_ADVERSARIAL_BIN set to a non-file is a hard error — explicit
    misconfiguration should fail loud, not silently fall through to the
    inline fallback reviewer."""
    monkeypatch.setenv(
        "BERIL_ADVERSARIAL_BIN", str(tmp_path / "does-not-exist"),
    )
    with pytest.raises(RuntimeError, match="BERIL_ADVERSARIAL_BIN"):
        resolve_adversarial_bin()


def test_resolve_adversarial_bin_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No override → shutil.which result is used."""
    monkeypatch.delenv("BERIL_ADVERSARIAL_BIN", raising=False)
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which",
        lambda name: (
            "/usr/local/bin/beril-adversarial"
            if name == "beril-adversarial" else None
        ),
    )
    assert resolve_adversarial_bin() == "/usr/local/bin/beril-adversarial"


def test_resolve_adversarial_bin_returns_none_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike resolve_claude_bin (which raises), resolve_adversarial_bin
    returns None when the canonical reviewer isn't installed — the
    orchestrator handles that case by warning loudly + falling back to
    the inline reviewer rather than halting."""
    monkeypatch.delenv("BERIL_ADVERSARIAL_BIN", raising=False)
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.shutil.which", lambda name: None,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert resolve_adversarial_bin() is None


# ---------------------------------------------------------------------------
# Tier K — orchestrator init logs warning when adversarial missing
# ---------------------------------------------------------------------------


def test_orchestrator_warns_when_adversarial_missing(
    project_with_figures: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When beril-adversarial is unavailable and --no-adversarial wasn't
    set, orchestrator __init__ emits a WARNING log that surfaces the
    install instruction. This warning fires at construction time so the
    user knows minutes before phase_review what kind of review they're
    heading toward."""
    # Force adversarial resolver to return None.
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.resolve_adversarial_bin",
        lambda: None,
    )
    draft_dir = project_with_figures / "papers" / "draft_1"
    with caplog.at_level("WARNING", logger="orchestrator"):
        orch = PaperWriterOrchestrator(draft_dir=draft_dir)
    assert orch.adversarial_bin is None
    assert orch.no_adversarial is False
    msgs = " ".join(r.message for r in caplog.records)
    assert "beril-adversarial" in msgs
    assert "FALL BACK" in msgs or "fall back" in msgs.lower()
    assert "BERIL_ADVERSARIAL_BIN" in msgs or "pipx install" in msgs


def test_orchestrator_no_warning_when_adversarial_resolved(
    project_with_figures: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When adversarial resolves, an INFO log records the path and no
    WARNING fires."""
    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.resolve_adversarial_bin",
        lambda: "/usr/local/bin/beril-adversarial",
    )
    draft_dir = project_with_figures / "papers" / "draft_1"
    with caplog.at_level("INFO", logger="orchestrator"):
        orch = PaperWriterOrchestrator(draft_dir=draft_dir)
    assert orch.adversarial_bin == "/usr/local/bin/beril-adversarial"
    # No fallback warning emitted.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not any("beril-adversarial" in r.message for r in warnings)


def test_orchestrator_no_adversarial_flag_skips_resolution(
    project_with_figures: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When --no-adversarial is set, resolve_adversarial_bin is not even
    called (it's an explicit user opt-out, not a missing-tool warning)."""
    called = {"count": 0}

    def _spy():
        called["count"] += 1
        return "/usr/local/bin/beril-adversarial"

    monkeypatch.setattr(
        "beril_paper_writer.orchestrator.resolve_adversarial_bin", _spy,
    )
    draft_dir = project_with_figures / "papers" / "draft_1"
    with caplog.at_level("INFO", logger="orchestrator"):
        orch = PaperWriterOrchestrator(
            draft_dir=draft_dir, no_adversarial=True,
        )
    assert orch.no_adversarial is True
    assert orch.adversarial_bin is None
    assert called["count"] == 0  # resolver not invoked
    msgs = " ".join(r.message for r in caplog.records)
    assert "--no-adversarial" in msgs


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


# ---------------------------------------------------------------------------
# Stage 4 Tier R-2: _finalize_citation_render
#
# This is the load-bearing fix for the references.md / citation_map.md
# 0-byte regression first surfaced by ibd_phage_targeting draft_1 in
# the v0.8.0 live test. The unit tests below cover the contract-end
# of the fix; the citation_pool unit tests in test_citation_pool.py
# (TestStage4HolisticWalker, TestStage4FinalizeCLI) cover the walker
# and CLI-side.
# ---------------------------------------------------------------------------


def _write_citation_pool(draft_dir: Path, bib_keys: list[str]) -> None:
    """Seed <draft_dir>/citation_pool.json with one entry per bib_key."""
    entries = []
    for i, key in enumerate(bib_keys):
        # Match the synthetic-test convention from test_citation_pool.py.
        # Each entry has a unique DOI so the dedup doesn't collapse them.
        entries.append({
            "authors": [f"Author{i} A"],
            "year": 2020 + i,
            "title": f"Paper {key}",
            "venue": "Journal X 1(1):1-10",
            "doi": f"10.X/{key}",
            "pmid": None,
            "studied": "stub",
            "finding": "stub",
            "scope_alignment": "direct",
            "assessment": "supports",
            "bib_key": key,
        })
    (draft_dir / "citation_pool.json").write_text(
        json.dumps({"entries": entries}, indent=2),
        encoding="utf-8",
    )


def test_finalize_render_populates_references_md_for_holistic_flow(
    orch: PaperWriterOrchestrator,
) -> None:
    """The headline fix: a fresh holistic-flow draft with
    citation_pool.json + manuscript.md (no per-section files) must
    end up with non-empty references.md and citation_map.md after
    _finalize_citation_render. This is exactly the v0.8.0 draft_1
    failure case that drove Stage 4."""
    _write_citation_pool(orch.draft_dir, ["Smith2020", "Jones2021"])
    (orch.draft_dir / "manuscript.md").write_text(
        "# Title\n\n## Methods\n\nMethods cite [Smith2020].\n\n"
        "## Results\n\nResults cite [Jones2021].\n\n## References\n\n",
        encoding="utf-8",
    )
    # Sanity: both rendered files don't yet exist.
    assert not (orch.draft_dir / "references.md").is_file()
    assert not (orch.draft_dir / "citation_map.md").is_file()

    orch._finalize_citation_render()

    refs = (orch.draft_dir / "references.md").read_text()
    assert refs.strip(), "references.md must be non-empty post-finalize"
    cmap = (orch.draft_dir / "citation_map.md").read_text()
    assert cmap.strip(), "citation_map.md must be non-empty post-finalize"

    # Pool was overwritten with citation_map populated.
    pool_after = json.loads(
        (orch.draft_dir / "citation_pool.json").read_text()
    )
    assert pool_after.get("citation_map"), \
        "citation_pool.json should have citation_map populated"
    assert set(pool_after["citation_map"].keys()) == {"Smith2020", "Jones2021"}


def test_finalize_render_writes_orphan_warnings_when_present(
    orch: PaperWriterOrchestrator,
) -> None:
    """A bib_key cited in manuscript.md but absent from the pool is
    surfaced via finalize_warnings.md. Pipeline continues regardless
    (advisory)."""
    _write_citation_pool(orch.draft_dir, ["Known2020"])
    (orch.draft_dir / "manuscript.md").write_text(
        "## Results\n\nCite [Known2020] and [Unknown9999].\n",
        encoding="utf-8",
    )
    orch._finalize_citation_render()

    warnings = (orch.draft_dir / "finalize_warnings.md").read_text()
    assert "Unknown9999" in warnings
    assert "orphan" in warnings.lower()


def test_finalize_render_no_orphan_warnings_file_when_clean(
    orch: PaperWriterOrchestrator,
) -> None:
    """No warnings file should be written when every cited key resolves.

    (The legacy CLI _cmd_finalize always writes the file with an
    'all clean' body. The orchestrator helper writes ONLY when there
    are orphans, to keep the draft_dir less noisy.)"""
    _write_citation_pool(orch.draft_dir, ["Smith2020"])
    (orch.draft_dir / "manuscript.md").write_text(
        "## Methods\n\nCite [Smith2020].\n",
        encoding="utf-8",
    )
    orch._finalize_citation_render()

    assert not (orch.draft_dir / "finalize_warnings.md").is_file()


def test_finalize_render_handles_missing_pool_gracefully(
    orch: PaperWriterOrchestrator,
    caplog,
) -> None:
    """Missing citation_pool.json triggers a WARNING + early return,
    not an exception. Pipeline must continue; the references files
    will simply remain unwritten (same end-state as today's bug, but
    now LOUD)."""
    (orch.draft_dir / "manuscript.md").write_text(
        "## Methods\n\nCite [Smith2020].\n",
        encoding="utf-8",
    )
    # No citation_pool.json present.
    import logging
    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        orch._finalize_citation_render()

    msgs = " ".join(r.message for r in caplog.records)
    assert "citation_pool.json" in msgs
    assert not (orch.draft_dir / "references.md").is_file()
    assert not (orch.draft_dir / "citation_map.md").is_file()


def test_finalize_render_handles_missing_manuscript_gracefully(
    orch: PaperWriterOrchestrator,
) -> None:
    """Missing manuscript.md returns an empty bib_key list — finalize
    still rewrites the pool (with no citation_map) and writes empty
    rendered files. The walker dispatch returns ([], []) when neither
    section files nor manuscript.md exist; this is the partial-state
    branch."""
    _write_citation_pool(orch.draft_dir, ["Smith2020"])
    # No manuscript.md, no per-section files.

    orch._finalize_citation_render()

    # references.md is created but is the "uncited entries only" form.
    assert (orch.draft_dir / "references.md").is_file()
    # citation_map.md is created but contains no entries (empty mapping).
    assert (orch.draft_dir / "citation_map.md").is_file()
    # Pool gets rewritten with empty citation_map.
    pool_after = json.loads(
        (orch.draft_dir / "citation_pool.json").read_text()
    )
    assert pool_after.get("citation_map", {}) == {}


def test_finalize_render_handles_malformed_pool_gracefully(
    orch: PaperWriterOrchestrator,
    caplog,
) -> None:
    """A malformed pool JSON triggers a WARNING + early return.
    Same fail-loud-then-continue discipline as the missing-pool case."""
    (orch.draft_dir / "citation_pool.json").write_text(
        "this is not valid JSON", encoding="utf-8",
    )
    (orch.draft_dir / "manuscript.md").write_text(
        "## Methods\n\nCite [Smith2020].\n", encoding="utf-8",
    )
    import logging
    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        orch._finalize_citation_render()

    msgs = " ".join(r.message for r in caplog.records)
    assert "failed to load pool" in msgs
    assert not (orch.draft_dir / "references.md").is_file()
