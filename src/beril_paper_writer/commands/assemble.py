"""`beril-paper-writer assemble <draft_dir>` — Phase 5 stub.

Renders markdown intermediates into a single .docx (or .pdf, or .md
concatenation) per SPEC §9. The full implementation lands with Phase 5
(end-to-end with stubbed claude); this stub validates arguments and
reports the planned behavior.

Per DECISIONS D-024 the renderer uses `python-docx` (pure-Python) rather
than `pandoc` (system binary). This keeps the pipx install fully
self-contained for remote BERIL deployments.

When implementation lands, this becomes a thin shell that:
  1. Verifies `python-docx` is importable (soft fail if not)
  2. Concatenates 00_throughline.md → 01_methods.md → ... → references.md
     into manuscript.md (in IMRAD order per SPEC §6.1)
  3. Runs the M1–M10 validators one final time
  4. Renders manuscript.md → manuscript.docx via tools/assemble_docx.py
  5. Reports validator pass/fail summary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from beril_paper_writer import __version__, state

_VALID_FORMATS = ("docx", "pdf", "md")

_NOT_IMPLEMENTED_MSG = (
    "beril-paper-writer assemble is declared in the planned CLI but is not\n"
    "yet implemented. The assembly step lands in Phase 5 (see SPEC §9 for\n"
    "the planned IMRAD concatenation order and validator pass; D-024 for\n"
    "the python-docx renderer choice).\n"
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
            "time, then render to the requested format via python-docx. "
            "Markdown intermediates are not modified. Pure Python — no "
            "system binaries required."
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

    # Phase-1 affordance: peek at state.json if present so the user sees
    # the writer can read the draft layout, even before assemble lands.
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
