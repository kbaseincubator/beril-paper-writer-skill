"""`beril-paper-writer assemble <draft_dir>` — render markdown manuscript to docx.

Wires the user-facing CLI to `tools/assemble_docx.py` for the markdown→docx
path. v0.3 ships the wiring (Tier 2.4) plus a stub renderer; the full
markdown→docx renderer lands in Tier 2.3. See
`smoke-test/v0_3_punch_list.md` for the punch-list scope.

Per DECISIONS D-024 the renderer uses `python-docx` (pure-Python) rather
than `pandoc` (system binary). Keeps the pipx install fully self-contained
for remote BERIL deployments.

CLI:
    beril-paper-writer assemble <draft_dir> [--format docx|pdf|md] [--output PATH]

Behavior:
  - Validates draft_dir exists and contains manuscript.md (the artifact
    produced by paper_writer.sh phase_assemble).
  - --format md: identity — manuscript.md is already the artifact; prints
    its path and exits 0.
  - --format docx: subprocesses tools/assemble_docx.py with the manuscript
    markdown + output docx path. Default output is
    <draft_dir>/manuscript.docx.
  - --format pdf: rejected (post-MVP); use --format docx and convert via
    Word/LibreOffice if needed.

Exit codes (per cli.py contract):
  0  success
  1  user error (missing draft_dir, missing manuscript.md, --format pdf)
  2  runtime error (package data missing; assemble_docx subprocess failed)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib import resources
from pathlib import Path

from beril_paper_writer import state

_VALID_FORMATS = ("docx", "pdf", "md")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "assemble",
        help="Render markdown manuscript to docx (or print md path).",
        description=(
            "Render the assembled markdown manuscript (manuscript.md, "
            "produced by paper_writer.sh phase_assemble) into the requested "
            "format. v0.3 ships a stub renderer for docx; full markdown→docx "
            "lands in Tier 2.3 (see smoke-test/v0_3_punch_list.md)."
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
        help="Output format (default: docx). pdf is post-MVP.",
    )
    p.add_argument(
        "--output",
        default=None,
        help=(
            "Output path (default: <draft_dir>/manuscript.<format>). "
            "Ignored for --format md."
        ),
    )
    p.set_defaults(func=run)
    return p


def _locate_assemble_docx() -> Path:
    """Locate tools/assemble_docx.py in the package data via importlib.resources.

    Same pattern as `commands/draft.py`'s `_locate_paper_writer_sh`. Raises
    FileNotFoundError if package data is missing (broken install).
    """
    try:
        ref = resources.files("beril_paper_writer").joinpath(
            "skill", "tools", "assemble_docx.py"
        )
        with resources.as_file(ref) as p:
            return Path(p)
    except (ModuleNotFoundError, FileNotFoundError) as e:
        raise FileNotFoundError(
            "tools/assemble_docx.py not found in package data. "
            "Reinstall beril-paper-writer-skill (pipx install --force ...)."
        ) from e


def run(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(
            f"Error: draft_dir does not exist or is not a directory: {draft_dir}",
            file=sys.stderr,
        )
        return 1

    if args.format not in _VALID_FORMATS:
        # argparse `choices=` enforces this; defensive double-check.
        print(
            f"Error: --format must be one of {_VALID_FORMATS}; got {args.format!r}",
            file=sys.stderr,
        )
        return 1

    manuscript_md = draft_dir / "manuscript.md"
    if not manuscript_md.is_file():
        print(
            f"Error: manuscript.md not found at {manuscript_md}.\n"
            "Run `beril-paper-writer continue <draft_dir>` first to draft "
            "and assemble the manuscript before invoking assemble.",
            file=sys.stderr,
        )
        return 1

    # Diagnostic: state.json peek — same affordance as the pre-Tier-2.4 stub.
    state_file = state.state_path(draft_dir)
    if state_file.is_file():
        try:
            st = state.load_state(draft_dir)
            print(f"draft_dir: {draft_dir}", file=sys.stderr)
            print(
                f"state.json: phase={st.phase}, mode={st.mode}",
                file=sys.stderr,
            )
        except (OSError, ValueError) as e:
            print(f"Note: could not read state.json: {e}", file=sys.stderr)

    if args.format == "md":
        # Identity: manuscript.md is already the artifact.
        print(f"manuscript.md: {manuscript_md}")
        return 0

    if args.format == "pdf":
        print(
            "Error: --format pdf is post-MVP. Use --format docx and convert "
            "via Word/LibreOffice if needed.",
            file=sys.stderr,
        )
        return 1

    # --format docx — subprocess to tools/assemble_docx.py.
    output_docx = (
        Path(args.output).expanduser().resolve()
        if args.output
        else draft_dir / "manuscript.docx"
    )

    try:
        asm_path = _locate_assemble_docx()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    argv = [
        sys.executable,
        str(asm_path),
        str(manuscript_md),
        str(output_docx),
    ]
    print(f"Running: {' '.join(argv)}", file=sys.stderr)
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        return proc.returncode

    print(f"manuscript.docx: {output_docx}")
    return 0
