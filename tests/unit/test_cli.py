"""Tests for beril_paper_writer.cli — top-level argparse + dispatch.

Coverage:
  - --version prints the package version
  - Each subcommand parses its own arguments correctly
  - continue / assemble stubs return exit code 2 (not implemented)
  - Help output shows all four subcommands
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


def test_continue_stub_returns_2(tmp_path: Path, capsys) -> None:
    rc = cli.main(["continue", str(tmp_path)])
    assert rc == 2
    captured = capsys.readouterr()
    assert "not\nyet implemented" in captured.err or "not yet" in captured.err.replace("\n", " ")


def test_assemble_stub_returns_2(tmp_path: Path, capsys) -> None:
    rc = cli.main(["assemble", str(tmp_path), "--format", "docx"])
    assert rc == 2


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
