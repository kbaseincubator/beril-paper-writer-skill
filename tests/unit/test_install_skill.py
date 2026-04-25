"""Tests for beril_paper_writer.commands.install_skill.

Coverage:
  - install-skill creates skill dir + state/ subdir
  - install-skill produces state/README.md
  - install-skill --no-smoke-test exit status
  - install-skill rejects non-BERIL paths
  - --force flag is plumbed through (no observable difference in v0.1.0-spec
    because skill/ tree is empty, but argument is accepted)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from beril_paper_writer import discovery
from beril_paper_writer.commands import install_skill


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _make_beril_root(base: Path) -> Path:
    root = base / "BERIL-extended"
    root.mkdir()
    (root / ".env").write_text("# fixture\n", encoding="utf-8")
    skills = root / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "berdl").mkdir()
    return root


def _ns(**kwargs) -> argparse.Namespace:
    """argparse.Namespace constructor with sensible defaults for install-skill."""
    defaults = {
        "beril_root": ".",
        "force": False,
        "no_smoke_test": True,  # tests skip the smoke test by default
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_install_skill_creates_skill_dir(tmp_path: Path, capsys) -> None:
    root = _make_beril_root(tmp_path)
    rc = install_skill.run(_ns(beril_root=str(root)))
    assert rc == 0
    skill_dir = discovery.get_skill_dir(root)
    assert skill_dir.is_dir()


def test_install_skill_creates_state_subdir(tmp_path: Path, capsys) -> None:
    root = _make_beril_root(tmp_path)
    install_skill.run(_ns(beril_root=str(root)))
    state_dir = discovery.get_state_dir(root)
    assert state_dir.is_dir()


def test_install_skill_writes_state_readme(tmp_path: Path, capsys) -> None:
    root = _make_beril_root(tmp_path)
    install_skill.run(_ns(beril_root=str(root)))
    state_readme = discovery.get_state_dir(root) / "README.md"
    assert state_readme.is_file()
    content = state_readme.read_text(encoding="utf-8")
    assert "learned-patterns" in content
    assert "NEVER shipped" in content


def test_install_skill_idempotent_state_readme(tmp_path: Path, capsys) -> None:
    """Running install-skill twice does not overwrite a hand-edited state/README.md."""
    root = _make_beril_root(tmp_path)
    install_skill.run(_ns(beril_root=str(root)))
    state_readme = discovery.get_state_dir(root) / "README.md"
    state_readme.write_text("# my custom notes\n", encoding="utf-8")
    install_skill.run(_ns(beril_root=str(root)))
    assert state_readme.read_text(encoding="utf-8") == "# my custom notes\n"


# --------------------------------------------------------------------------
# Error paths
# --------------------------------------------------------------------------

def test_install_skill_rejects_non_beril_path(tmp_path: Path, capsys) -> None:
    not_beril = tmp_path / "not_beril"
    not_beril.mkdir()
    rc = install_skill.run(_ns(beril_root=str(not_beril)))
    assert rc == 1
    captured = capsys.readouterr()
    assert "not a BERIL checkout" in captured.err or "Error" in captured.err


def test_install_skill_default_path_uses_cwd(tmp_path: Path, capsys, monkeypatch) -> None:
    """Default beril_root='.' means current directory; reject with code 1 if not BERIL."""
    monkeypatch.chdir(tmp_path)
    rc = install_skill.run(_ns(beril_root="."))
    assert rc == 1


# --------------------------------------------------------------------------
# Force flag
# --------------------------------------------------------------------------

def test_install_skill_force_flag_accepted(tmp_path: Path, capsys) -> None:
    """--force does not error; in v0.1.0-spec the skill/ tree is empty so
    no observable behavior change, but the flag is plumbed through correctly."""
    root = _make_beril_root(tmp_path)
    rc = install_skill.run(_ns(beril_root=str(root), force=True))
    assert rc == 0
