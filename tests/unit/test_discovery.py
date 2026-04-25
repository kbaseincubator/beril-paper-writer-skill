"""Tests for beril_paper_writer.discovery — BERIL_ROOT resolution + derived paths.

Mirror beril_adversarial's test_discovery.py structure. Coverage:
  - Explicit path: valid root accepted; invalid path rejected with diagnostic
  - Environment variable: same as explicit
  - Walk-up from cwd: finds nearest valid root; otherwise reports best partial
  - Marker checks: env file, .claude/skills/, BERIL-core skill presence
  - Tiebreaker signals: directory name, .env.example content, DIRECTORY_STRUCTURE.md
  - Derived paths: skill_dir, prompts_dir, references_dir, tools_dir, state_dir,
    projects_dir, papers_dir, draft_dir
"""

from __future__ import annotations

from pathlib import Path

import pytest

from beril_paper_writer import discovery


# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------

def _make_beril_root(
    base: Path,
    *,
    name: str = "beril-extended",
    with_env: bool = True,
    with_claude_skills: bool = True,
    core_skills: tuple[str, ...] = ("submit", "berdl", "suggest-research"),
    with_dir_structure_md: bool = False,
    env_example_has_kbase: bool = False,
) -> Path:
    """Create a directory tree that does (or doesn't) look like BERIL."""
    root = base / name
    root.mkdir(parents=True, exist_ok=True)
    if with_env:
        (root / ".env").write_text("# fixture\n", encoding="utf-8")
    if with_claude_skills:
        skills = root / ".claude" / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        for s in core_skills:
            (skills / s).mkdir(exist_ok=True)
    if with_dir_structure_md:
        (root / "DIRECTORY_STRUCTURE.md").write_text("# fixture\n", encoding="utf-8")
    if env_example_has_kbase:
        (root / ".env.example").write_text(
            "KBASE_AUTH_TOKEN=fixture\n", encoding="utf-8"
        )
    return root


# --------------------------------------------------------------------------
# Explicit-path tests
# --------------------------------------------------------------------------

def test_explicit_valid_root_accepted(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path)
    resolved = discovery.find_beril_root(explicit=root)
    assert resolved == root.resolve()


def test_explicit_path_rejected_when_no_env(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path, with_env=False)
    with pytest.raises(discovery.BerilRootNotFound) as exc:
        discovery.find_beril_root(explicit=root)
    assert ".env" in str(exc.value)


def test_explicit_path_rejected_when_no_claude_skills(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path, with_claude_skills=False)
    with pytest.raises(discovery.BerilRootNotFound) as exc:
        discovery.find_beril_root(explicit=root)
    assert ".claude/skills" in str(exc.value)


def test_explicit_path_rejected_when_no_core_skills(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path, core_skills=())
    with pytest.raises(discovery.BerilRootNotFound) as exc:
        discovery.find_beril_root(explicit=root)
    assert "BERIL-core" in str(exc.value)


def test_explicit_path_accepts_partial_core_skills(tmp_path: Path) -> None:
    """Only one BERIL-core skill is required, not all three."""
    root = _make_beril_root(tmp_path, core_skills=("berdl",))
    resolved = discovery.find_beril_root(explicit=root)
    assert resolved == root.resolve()


# --------------------------------------------------------------------------
# Environment-variable tests
# --------------------------------------------------------------------------

def test_env_var_takes_precedence_over_walkup(tmp_path: Path) -> None:
    explicit_root = _make_beril_root(tmp_path, name="explicit")
    walkup_root = _make_beril_root(tmp_path, name="walkup")
    other_dir = walkup_root / "subdir"
    other_dir.mkdir()
    resolved = discovery.find_beril_root(
        env={"BERIL_ROOT": str(explicit_root)},
        cwd=other_dir,
    )
    assert resolved == explicit_root.resolve()


def test_env_var_invalid_rejected(tmp_path: Path) -> None:
    bad = _make_beril_root(tmp_path, with_env=False)
    with pytest.raises(discovery.BerilRootNotFound):
        discovery.find_beril_root(env={"BERIL_ROOT": str(bad)})


# --------------------------------------------------------------------------
# Walk-up tests
# --------------------------------------------------------------------------

def test_walkup_finds_root_from_subdir(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path)
    deep = root / "projects" / "my_project" / "notebooks"
    deep.mkdir(parents=True)
    resolved = discovery.find_beril_root(cwd=deep, env={})
    assert resolved == root.resolve()


def test_walkup_returns_root_when_started_at_root(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path)
    resolved = discovery.find_beril_root(cwd=root, env={})
    assert resolved == root.resolve()


def test_walkup_fails_when_no_root_in_path(tmp_path: Path) -> None:
    nowhere = tmp_path / "no" / "beril" / "here"
    nowhere.mkdir(parents=True)
    with pytest.raises(discovery.BerilRootNotFound) as exc:
        discovery.find_beril_root(cwd=nowhere, env={})
    msg = str(exc.value)
    assert "could not find BERIL_ROOT" in msg
    assert "Walk-up from" in msg


def test_walkup_diagnostic_names_closest_partial(tmp_path: Path) -> None:
    """When walk-up fails, error names the closest-to-BERIL candidate."""
    # Build a partial candidate (.env present but no .claude/skills/)
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / ".env").write_text("\n", encoding="utf-8")
    sub = partial / "child"
    sub.mkdir()
    with pytest.raises(discovery.BerilRootNotFound) as exc:
        discovery.find_beril_root(cwd=sub, env={})
    assert "Closest candidate" in str(exc.value)


# --------------------------------------------------------------------------
# Tiebreaker signals
# --------------------------------------------------------------------------

def test_tiebreaker_directory_name_pattern() -> None:
    from beril_paper_writer.discovery import DIR_NAME_PATTERN
    assert DIR_NAME_PATTERN.search("BERIL-extended") is not None
    assert DIR_NAME_PATTERN.search("beril_extended") is not None
    assert DIR_NAME_PATTERN.search("beril") is None  # no separator
    assert DIR_NAME_PATTERN.search("randomdir") is None


def test_tiebreaker_signals_recorded_in_marker_check(tmp_path: Path) -> None:
    root = _make_beril_root(
        tmp_path,
        name="BERIL-extended",
        with_dir_structure_md=True,
        env_example_has_kbase=True,
    )
    check = discovery._check_markers(root)
    assert check.is_beril_root
    assert "directory-name-matches-BERIL" in check.tiebreakers
    assert ".env.example-has-KBASE_AUTH_TOKEN" in check.tiebreakers
    assert "DIRECTORY_STRUCTURE.md-present" in check.tiebreakers


# --------------------------------------------------------------------------
# Derived paths
# --------------------------------------------------------------------------

def test_derived_paths_layout(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path)
    assert discovery.get_env_path(root) == root / ".env"
    skill = discovery.get_skill_dir(root)
    assert skill == root / ".claude" / "skills" / "beril-paper-writer"
    assert discovery.get_prompts_dir(root) == skill / "prompts"
    assert discovery.get_references_dir(root) == skill / "references"
    assert discovery.get_tools_dir(root) == skill / "tools"
    assert discovery.get_state_dir(root) == skill / "state"
    assert discovery.get_projects_dir(root) == root / "projects"


def test_derived_paths_paper_specific(tmp_path: Path) -> None:
    """Paper-writer adds project/papers/draft helpers vs. adversarial."""
    root = _make_beril_root(tmp_path)
    proj = discovery.get_project_dir(root, "functional_dark_matter")
    assert proj == root / "projects" / "functional_dark_matter"
    papers = discovery.get_papers_dir(root, "functional_dark_matter")
    assert papers == proj / "papers"
    draft = discovery.get_draft_dir(root, "functional_dark_matter", 3)
    assert draft == papers / "draft_3"


def test_resolve_paths_bundles_everything(tmp_path: Path) -> None:
    root = _make_beril_root(tmp_path)
    paths = discovery.resolve_paths(explicit=root)
    assert paths.beril_root == root.resolve()
    assert paths.skill_dir == root / ".claude" / "skills" / "beril-paper-writer"
    assert paths.projects_dir == root / "projects"


# --------------------------------------------------------------------------
# Constant alignment
# --------------------------------------------------------------------------

def test_skill_dir_name_is_paper_writer() -> None:
    """Sanity check that this skill's name is set, not adversarial's."""
    assert discovery.SKILL_DIR_NAME == "beril-paper-writer"
