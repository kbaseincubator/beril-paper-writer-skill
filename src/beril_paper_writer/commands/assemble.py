"""`beril-paper-writer assemble <draft_dir>` — Phase 5 stub.

Renders markdown intermediates into a single .docx (or .pdf, or .md
concatenation) per SPEC §9. The full implementation lands with Phase 5
(end-to-end with stubbed claude); this stub validates arguments and
reports the planned behavior.

When implementation lands, this becomes a thin shell that:
  1. Verifies pandoc is on PATH (or prints install hint)
  2. Concatenates 00_throughline.md → 01_methods.md → ... → references.md
     into manuscript.md (in IMRAD order per SPEC §6.1)
  3. Runs the M1–M10 validators one final time
  4. Calls pandoc to render manuscript.md → manuscript.docx
  5. Reports validator pass/fail summary
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from beril_paper_writer import __version__, state

_VALID_FORMATS = ("docx", "pdf", "md")

_NOT_IMPLEMENTED_MSG = (
    "beril-paper-writer assemble is declared in the planned CLI but is not\n"
    "yet implemented. The assembly step lands in Phase 5 (see SPEC §9 for\n"
    "the planned IMRAD concatenation order and validator pass).\n"
    "\n"
    "Phase 1 ({ver}) ships only the install / configure / state-tracking\n"
    "primitives. To request output format: --format {fmt} (planned).\n"
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "assemble",
        help="Render markdown intermediates to .docx / .pdf / .md (planned — Phase 5).",
        description=(
            "Concatenate the per-section markdown files in a draft directory "
            "into a single manuscript, run the M1–M10 validators one final "
            "time, then render to the requested format via pandoc. "
            "Markdown intermediates are not modified."
        ),
    )
    p.add_argument(
        "draft_dir",
        help=(
            "Path to the paper draft directory "
            "(e.g. projects/<id>/papers/draft_1/)."
        ),
    )
    p.add_argument(
        "--format",
        choices=_VALID_FORMATS,
        default="docx",
        help="Output format (default: docx).",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(
            f"Error: draft_dir does not exist or is not a directory: {draft_dir}",
            file=sys.stderr,
        )
        return 1

    if args.format not in _VALID_FORMATS:
        print(
            f"Error: --format must be one of {_VALID_FORMATS}; got {args.format!r}",
            file=sys.stderr,
        )
        return 1

    # Phase-1 affordance: at least check whether pandoc exists for the
    # planned docx/pdf paths, and report a state.json peek for the user.
    if args.format in ("docx", "pdf"):
        if shutil.which("pandoc") is None:
            print(
                "Note: pandoc is not on PATH. When `assemble` is implemented "
                "(Phase 5), the docx/pdf paths will require pandoc.",
                file=sys.stderr,
            )

    state_file = state.state_path(draft_dir)
    if state_file.is_file():
        try:
            st = state.load_state(draft_dir)
            print(f"draft_dir: {draft_dir}")
            print(f"state.json found (phase={st.phase}, mode={st.mode}).")
            print(f"requested output format: {args.format}")
            print()
        except (OSError, ValueError) as e:
            print(
                f"Note: could not read state.json: {e}.",
                file=sys.stderr,
            )

    sys.stderr.write(_NOT_IMPLEMENTED_MSG.format(ver=__version__, fmt=args.format))
    return 2
