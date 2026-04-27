"""`beril-paper-writer draft <project>` — start a fresh paper draft.

Thin Python wrapper around `tools/paper_writer.sh draft`. The shell
script does the orchestration; this command:

  1. Resolves the project argument (path or project_id under projects/)
  2. Locates paper_writer.sh from the package's bundled skill data
     (importlib.resources)
  3. Forwards CLI flags (--model, --depth, --mode, etc.) to the shell
  4. Runs the shell in the foreground, streams its output, and returns
     its exit code
  5. On exit 0 with the bash script having paused at throughline_pick,
     reads the resulting <draft_dir>/.handoff.json and prints a friendly
     summary so the slash-command markdown can parse the next step

The shell script is the single source of truth for orchestration logic;
this Python layer is thin glue. See SPEC §5.1 for the drafting flow and
LAYOUT 'Slash commands' for the user-facing CLI shape.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

from beril_paper_writer import __version__


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "draft",
        help="Start a fresh paper draft for a BERDL project.",
        description=(
            "Initialize a new draft directory under "
            "<project>/papers/draft_N/ and run the planning phase "
            "(extract methods + figures, run plan.v1 to produce "
            "throughline candidates). Pauses at the throughline-pick "
            "gate; emits .handoff.json for the slash-command agent to "
            "drive the next user interaction."
        ),
    )
    p.add_argument(
        "project",
        help=(
            "Project path or project_id. If a directory: used directly. "
            "Otherwise interpreted as a project_id under cwd's projects/<id>/."
        ),
    )
    p.add_argument(
        "--model",
        default=None,
        help="Override default model (Sonnet). Forwarded to paper_writer.sh.",
    )
    p.add_argument(
        "--depth",
        default=None,
        choices=["quick", "standard", "deep"],
        help="Depth tier per SPEC §3.4. Default: standard.",
    )
    p.add_argument(
        "--mode",
        default=None,
        choices=["paper", "report"],
        help=(
            "Override mode. Default: tier-driven (STRONG/THIN→paper; "
            "EXPLORATORY→report)."
        ),
    )
    p.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable stream_progress.py wrapper (no Write verification).",
    )
    p.add_argument(
        "--no-adversarial",
        action="store_true",
        help="Skip adversarial reviewer; use fallback inline reviewer.",
    )
    p.set_defaults(func=run)
    return p


def _locate_paper_writer_sh() -> Path:
    """Locate paper_writer.sh in the package data.

    Returns the absolute path to the shipped script. Raises FileNotFoundError
    if the package data is missing (which would mean the install is broken).
    """
    # importlib.resources path: beril_paper_writer/skill/tools/paper_writer.sh
    try:
        # Python 3.9+ pattern.
        ref = resources.files("beril_paper_writer").joinpath(
            "skill", "tools", "paper_writer.sh"
        )
        # ref is a Traversable; .as_posix() works for filesystem-resident packages.
        # Convert to a real Path for subprocess.
        with resources.as_file(ref) as p:
            return Path(p)
    except (ModuleNotFoundError, FileNotFoundError) as e:
        raise FileNotFoundError(
            "paper_writer.sh not found in package data. "
            "Reinstall beril-paper-writer-skill (pipx install --force ...)."
        ) from e


def run(args: argparse.Namespace) -> int:
    try:
        sh_path = _locate_paper_writer_sh()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Build subprocess argv.
    argv = ["bash", str(sh_path), "draft", args.project]
    if args.model:
        argv += ["--model", args.model]
    if args.depth:
        argv += ["--depth", args.depth]
    if args.mode:
        argv += ["--mode", args.mode]
    if args.no_stream:
        argv += ["--no-stream"]
    if args.no_adversarial:
        argv += ["--no-adversarial"]

    print(f"▸ Running: {' '.join(argv)}", file=sys.stderr)
    print("", file=sys.stderr)

    # Foreground; stdout/stderr inherit. paper_writer.sh prints the final
    # draft_dir path on stdout; we capture it for the post-run summary.
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=None,  # inherit so user sees logs in real time
        text=True,
    )

    rc = proc.returncode
    draft_dir = (proc.stdout or "").strip().splitlines()
    draft_dir = draft_dir[-1] if draft_dir else ""

    # On clean pause at throughline_pick, surface the handoff file path
    # so the slash-command markdown can parse it. paper_writer.sh's exit
    # 0 + presence of .handoff.json is the success signal.
    if rc == 0 and draft_dir and (Path(draft_dir) / ".handoff.json").is_file():
        handoff = Path(draft_dir) / ".handoff.json"
        try:
            with handoff.open() as f:
                data = json.load(f)
            print("", file=sys.stderr)
            print("─── Draft paused at throughline_pick ───", file=sys.stderr)
            print(f"  draft_dir: {draft_dir}", file=sys.stderr)
            print(f"  handoff:   {handoff}", file=sys.stderr)
            n_choices = len(data.get("choices", []))
            print(f"  candidates: {n_choices}", file=sys.stderr)
            n_warnings = len(data.get("advisory_warnings", []))
            if n_warnings:
                print(f"  advisory warnings: {n_warnings}", file=sys.stderr)
            print(
                f"  resume:    {data.get('resume_command', '(see handoff JSON)')}",
                file=sys.stderr,
            )
            print("", file=sys.stderr)
            # Stdout: just the draft_dir, for scripted callers.
            print(draft_dir)
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"Warning: could not read handoff JSON at {handoff}: {e}",
                file=sys.stderr,
            )
            print(draft_dir)

    return rc
