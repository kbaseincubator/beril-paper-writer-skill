"""`beril-paper-writer` top-level CLI entry point.

Dispatches to command modules under beril_paper_writer.commands/.

Subcommands (per LAYOUT.md "Slash commands"):
  install-skill   Copy shipped skill/ tree into BERIL/.claude/skills/beril-paper-writer/.
  configure       Verify claude is on PATH; warn if beril-adversarial absent.
  continue        Resume a paused paper draft (Phase 4 — currently stub).
  assemble        Render markdown intermediates to .docx (Phase 5 — currently stub).

The drafting and review-rewrite paths are NOT Python subcommands — they
will be handled by the planned shipped shell script
tools/paper_writer.sh, which the slash command will invoke directly.
Same pattern as beril-adversarial.

Exit codes:
  0  success
  1  user error (bad args, missing BERIL_ROOT, missing file user should fix)
  2  runtime error (subprocess failed; package data missing; subcommand
     not yet implemented in this release)
  3  config error (claude not installed)
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from beril_paper_writer import __version__
from beril_paper_writer.commands import (
    assemble,
    configure,
    continue_run,
    install_skill,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beril-paper-writer",
        description=(
            "BERIL Paper Writer — scientific manuscript drafter for BERDL "
            "analysis projects. See "
            "https://github.com/ArkinLaboratory/beril-paper-writer-skill."
        ),
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"beril-paper-writer-skill {__version__}",
    )
    subparsers = p.add_subparsers(dest="command", metavar="<command>")

    install_skill.add_parser(subparsers)
    configure.add_parser(subparsers)
    continue_run.add_parser(subparsers)
    assemble.add_parser(subparsers)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
