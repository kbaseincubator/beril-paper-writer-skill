"""`beril-paper-writer` top-level CLI entry point — STUB for v0.1.0-spec.

This release ships the specification only (SPEC.md, LAYOUT.md, DECISIONS.md,
reference/). The CLI subcommands are declared here so the package shape and
console-script entry point are stable from this commit forward, but invoking
any subcommand currently prints a "not yet implemented" notice and points
the user at the spec.

Planned subcommands (per LAYOUT.md):
  install-skill   Copy shipped skill/ tree into BERIL/.claude/skills/beril-paper-writer/.
  configure       Verify claude is on PATH; warn if beril-adversarial absent.
  continue        Resume a paused paper draft.
  assemble        Render markdown intermediates to .docx.

Exit codes:
  0  success
  1  user error (bad args, etc.)
  2  not yet implemented (this stub release)
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from beril_paper_writer import __version__

SPEC_URL = "https://github.com/ArkinLaboratory/beril-paper-writer-skill/blob/main/SPEC.md"

NOT_IMPLEMENTED_MSG = (
    "beril-paper-writer {ver} is the SPECIFICATION-ONLY release.\n"
    "Subcommand `{cmd}` is declared in the planned CLI but is not yet\n"
    "implemented. See {spec} for the design and DECISIONS.md for the\n"
    "rationale behind the planned behavior.\n"
)


def _stub(name: str) -> int:
    sys.stderr.write(
        NOT_IMPLEMENTED_MSG.format(ver=__version__, cmd=name, spec=SPEC_URL)
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beril-paper-writer",
        description=(
            "BERIL Paper Writer — scientific manuscript drafter for BERDL "
            "analysis projects. v0.1.0-spec: specification only; "
            "see SPEC.md."
        ),
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"beril-paper-writer-skill {__version__} (spec-only)",
    )
    subparsers = p.add_subparsers(dest="command", metavar="<command>")

    # Declare the planned subcommands so users see them in --help and so
    # the package shape is stable. Each is a stub that swallows any
    # additional args (so users get the spec-only message, not an
    # argparse error) and exits with code 2.
    for name, help_text in (
        ("install-skill", "Install skill into BERIL/.claude/skills/ (planned)"),
        ("configure", "Verify CLI dependencies (planned)"),
        ("continue", "Resume a paused paper draft (planned)"),
        ("assemble", "Render markdown intermediates to .docx (planned)"),
    ):
        sp = subparsers.add_parser(name, help=help_text)
        sp.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        sp.set_defaults(_handler=lambda _args, _name=name: _stub(_name))

    return p


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
