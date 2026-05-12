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
    p.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help=(
            "Halt with handoff if cumulative LLM spend exceeds N USD. "
            "Checked before each LLM call. Default: no cap."
        ),
    )
    p.add_argument(
        "--recaption",
        action="store_true",
        help=(
            "Force re-synthesis of LLM figure captions. Default: skip "
            "figures with existing audit/figure_caption_<N>.md files."
        ),
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
    import asyncio
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator
    
    project_dir = Path(args.project).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"error: project directory does not exist: {project_dir}", file=sys.stderr)
        return 1

    papers_dir = project_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    
    # Find next draft_N
    existing = [d for d in papers_dir.iterdir() if d.is_dir() and d.name.startswith("draft_")]
    draft_num = 1
    if existing:
        nums = []
        for d in existing:
            try:
                nums.append(int(d.name.split("_")[1]))
            except ValueError:
                pass
        if nums:
            draft_num = max(nums) + 1
            
    draft_dir = papers_dir / f"draft_{draft_num}"
    draft_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"▸ Initializing new draft at {draft_dir}", file=sys.stderr)
    
    # Stage 1 Tier B: 'claude-opus-4-7' is an invalid model identifier
    # (Opus 4.6 is the real model per SPEC §6.7). Same correction lands
    # in the orchestrator constructor default; this overrides via getattr
    # so the fix had to land here too.
    orch = PaperWriterOrchestrator(
        draft_dir,
        max_cost_usd=getattr(args, "max_cost_usd", None),
        model=getattr(args, "model", "claude-sonnet-4-5-20250929") or "claude-sonnet-4-5-20250929",
        model_writing=getattr(args, "model_writing", "claude-opus-4-6") or "claude-opus-4-6",
    )
    
    try:
        asyncio.run(orch.run_pipeline())
        
        # At this point, the pipeline should raise PipelineHalted or finish.
        # If it halted at throughline_pick, it should have printed the handoff message.
        return 0
    except Exception as e:
        if type(e).__name__ == "PipelineHalted":
            # Read .handoff.json to print the summary.
            # Stage 1 Tier B contract-drift fix (2026-05-11): the plan.v1
            # prompt emits 'candidates_summary' (dict TLN→description) and
            # 'next_steps' (free-form prose). The legacy draft.py was
            # reading 'choices' (array) and 'resume_command' (string) —
            # neither of which the prompt produces. Result: the handoff
            # printer surfaced nothing useful and the user had to dig
            # through the JSON to find the resume command. This block now
            # reads BOTH schemas defensively.
            handoff = draft_dir / ".handoff.json"
            if handoff.is_file():
                try:
                    import json
                    with handoff.open() as f:
                        data = json.load(f)
                    print("", file=sys.stderr)
                    print("─── Draft paused at throughline_pick ───", file=sys.stderr)
                    print(f"  draft_dir: {draft_dir}", file=sys.stderr)
                    print(f"  handoff:   {handoff}", file=sys.stderr)

                    # Candidate count + descriptions — accept either
                    # schema. plan.v1 emits 'candidates_summary' (dict);
                    # earlier design emitted 'choices' (list).
                    candidates_summary = data.get("candidates_summary") or {}
                    choices_list = data.get("choices") or []
                    n_candidates = (
                        len(candidates_summary)
                        if isinstance(candidates_summary, dict) and candidates_summary
                        else len(choices_list)
                    )
                    print(f"  candidates: {n_candidates}", file=sys.stderr)
                    if isinstance(candidates_summary, dict):
                        for cid, desc in list(candidates_summary.items())[:6]:
                            # Truncate long descriptions for readability.
                            desc_str = str(desc)
                            if len(desc_str) > 200:
                                desc_str = desc_str[:197] + "..."
                            print(f"    {cid}: {desc_str}", file=sys.stderr)

                    n_warnings = len(data.get("advisory_warnings", []))
                    if n_warnings:
                        print(f"  advisory warnings: {n_warnings}", file=sys.stderr)

                    # Resume command — accept 'resume_command' (explicit),
                    # else derive from 'next_steps' (prose), else synthesize
                    # a defensible default for throughline_pick.
                    resume = data.get("resume_command")
                    next_steps = data.get("next_steps")
                    if not resume and next_steps:
                        # Show the prompt's prose; user reads + extracts.
                        print(f"  next_steps:", file=sys.stderr)
                        for line in str(next_steps).splitlines():
                            print(f"    {line}", file=sys.stderr)
                    if not resume:
                        # Synthesize the canonical throughline-pick resume.
                        # The continue command requires --pick TLN; the
                        # user has to choose. We surface the canonical form
                        # so the next step is obvious.
                        cid_hint = (
                            next(iter(candidates_summary)) if candidates_summary
                            else "TL1"
                        )
                        resume = (
                            f"beril-paper-writer continue {draft_dir} "
                            f"--pick {cid_hint}  "
                            f"# pick one of: "
                            f"{', '.join(candidates_summary.keys()) if candidates_summary else 'TL1, TL2, TL3'}"
                        )
                    print(f"  resume:    {resume}", file=sys.stderr)
                    print("", file=sys.stderr)
                    print(draft_dir)
                except Exception as ex:
                    print(f"Warning: could not read handoff JSON at {handoff}: {ex}", file=sys.stderr)
            return 0
            
        print(f"error: pipeline execution failed: {e}", file=sys.stderr)
        return 2