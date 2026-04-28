"""Tests for beril_paper_writer.cli — top-level argparse + dispatch.

Coverage:
  - --version prints the package version
  - Each subcommand parses its own arguments correctly
  - continue rejects nonexistent draft_dir + corrupt state.json
  - assemble validates draft_dir + manuscript.md, rejects pdf, identity for md
  - Help output shows all four subcommands

v0.3 update (Tier 2.4): assemble is no longer a stub. It validates inputs
and dispatches --format docx to tools/assemble_docx.py. The unit tests
cover the path-validation gates; the docx subprocess path is exercised
manually in v0.3 smoke runs (see smoke-test/v0_3_punch_list.md).

v0.3 cleanup: removed `test_continue_stub_returns_2` — continue_run.py
was un-stubbed in v0.1; the test asserted outdated "not yet implemented"
stderr. Replaced with `test_continue_corrupt_state_returns_2` which
exercises the real OSError/ValueError catch path (state.json malformed).
"""

from __future__ import annotations

import io
import contextlib
from pathlib import Path

import pytest

from beril_paper_writer import __version__, cli


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_no_args_prints_help(capsys) -> None:
    rc = cli.main([])
    assert rc == 0
    captured = capsys.readouterr()
    # argparse emits help to stdout
    assert "install-skill" in captured.out
    assert "configure" in captured.out
    assert "continue" in captured.out
    assert "assemble" in captured.out


def test_help_lists_all_subcommands(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    captured = capsys.readouterr()
    assert "install-skill" in captured.out
    assert "configure" in captured.out
    assert "continue" in captured.out
    assert "assemble" in captured.out


def test_continue_corrupt_state_returns_2(tmp_path: Path, capsys) -> None:
    """Corrupt state.json → continue exits 2 with an explanatory stderr.

    Exercises the OSError/ValueError catch in continue_run.run() (lines
    338-342). Writing non-JSON content to state.json triggers
    ValueError out of json.loads via state.load_state.
    """
    (tmp_path / "state.json").write_text("not valid json {{{", encoding="utf-8")
    rc = cli.main(["continue", str(tmp_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "cannot load state.json" in captured.err


def test_assemble_rejects_nonexistent_dir(tmp_path: Path, capsys) -> None:
    """assemble exits 1 when draft_dir doesn't exist."""
    bogus = tmp_path / "does_not_exist"
    rc = cli.main(["assemble", str(bogus), "--format", "docx"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_assemble_rejects_missing_manuscript(tmp_path: Path, capsys) -> None:
    """assemble exits 1 when draft_dir has no manuscript.md."""
    rc = cli.main(["assemble", str(tmp_path), "--format", "docx"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "manuscript.md not found" in captured.err


def test_assemble_md_format_is_identity(tmp_path: Path, capsys) -> None:
    """--format md is identity: manuscript.md already exists; print path, exit 0."""
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("# Title\n\nbody.\n", encoding="utf-8")
    rc = cli.main(["assemble", str(tmp_path), "--format", "md"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "manuscript.md" in captured.out
    assert str(manuscript) in captured.out


def test_assemble_pdf_format_rejected(tmp_path: Path, capsys) -> None:
    """--format pdf is post-MVP: exit 1 with explanatory stderr."""
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text("# Title\n\nbody.\n", encoding="utf-8")
    rc = cli.main(["assemble", str(tmp_path), "--format", "pdf"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "post-MVP" in captured.err


def test_assemble_format_validation(tmp_path: Path, capsys) -> None:
    """--format only accepts docx | pdf | md."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["assemble", str(tmp_path), "--format", "xyz"])
    assert exc.value.code == 2  # argparse uses 2 for usage errors


def test_continue_rejects_nonexistent_dir(tmp_path: Path, capsys) -> None:
    bogus = tmp_path / "does_not_exist"
    rc = cli.main(["continue", str(bogus)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "does not exist" in captured.err
