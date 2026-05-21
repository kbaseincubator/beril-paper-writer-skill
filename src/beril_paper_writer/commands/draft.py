"""`beril-paper-writer draft <project>` — start a fresh paper draft.

Invokes the Python orchestrator (`PaperWriterOrchestrator.run_pipeline`)
to take the project through init → extract → triage → plan, halting at
the throughline-pick gate. The user picks a candidate, then resumes via
`beril-paper-writer continue <draft_dir> --pick TLN`.

This command:
  1. Resolves the project argument (path, or bare project_id under
     `<cwd>/projects/<id>/` — Stage 3 Tier J fallback)
  2. Allocates the next `papers/draft_N/` directory
  3. Constructs the orchestrator (resolves `claude` and
     `beril-adversarial` to absolute paths up front, fails loud if
     `claude` is missing, warns at init if `beril-adversarial` is
     missing — Tier J + Tier K)
  4. Runs the pipeline asynchronously; the orchestrator catches
     `PipelineHalted` internally and prints the throughline-pick
     handoff summary itself

See LAYOUT.md 'Slash commands' for the user-facing CLI shape and
orchestrator.py for the pipeline state machine. (Historical: this
command was originally a thin wrapper around `tools/paper_writer.sh
draft`; the Python orchestrator took over in Stage 1, and the bash
orchestrator was retired 2026-05-20 — D-053.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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
        help=(
            "Override the holistic-draft model (default: Opus 4.6, per "
            "D-050). Forwarded to the orchestrator."
        ),
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
    # Stage 4 Tier S (2026-05-18): pre-disable the P0 gate from the
    # start. The remediation loop only kicks in via `continue --remediate`
    # because `draft` is a fresh run; the only useful Tier-S flag here
    # is the gate-bypass for operators who want a one-shot draft without
    # any pause.
    p.add_argument(
        "--ship-with-p0s",
        action="store_true",
        help=(
            "Pre-disable the P0 gate on this run. The pipeline still "
            "computes adversarial_review.json and numeric_grounding.json "
            "but does not pause if P0 findings are present — phase_review "
            "advances straight through to optimize. Use only when you "
            "are intentionally running an exploratory or known-degraded "
            "draft."
        ),
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    import asyncio
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator
    
    project_dir = Path(args.project).expanduser().resolve()
    if not project_dir.is_dir():
        # Stage 3 Tier J: the `project` arg may be a bare project_id
        # rather than a path. The documented behavior (and the
        # slash-command's expectation) is to interpret it as
        # <cwd>/projects/<id>/. The code never implemented that
        # fallback — `beril-paper-writer draft ibd_phage_targeting`
        # from the BERIL root looked for <root>/ibd_phage_targeting/
        # and failed. Implement the documented fallback.
        fallback = (Path.cwd() / "projects" / args.project).resolve()
        if fallback.is_dir():
            project_dir = fallback
        else:
            print("error: project not found.", file=sys.stderr)
            print(f"  tried as path:       {project_dir}", file=sys.stderr)
            print(f"  tried as project_id: {fallback}", file=sys.stderr)
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
    
    # Stage 3 Tier J: the orchestrator constructor resolves the `claude`
    # CLI to an absolute path (resolve_claude_bin) and raises RuntimeError
    # if it cannot. Catch that here for a clean exit with a stillborn-dir
    # hint instead of an uncaught traceback — the draft dir was just
    # created above but nothing has been written into it yet.
    try:
        # Stage 3 (2026-05-12): default `model` is Opus 4.6, not Sonnet.
        # `model` drives the reasoning-heavy phases (plan, triage,
        # optimizer); a bare `beril-paper-writer draft` should not
        # silently scaffold the manuscript on Sonnet. `--model` still
        # overrides. See the orchestrator constructor for rationale.
        # Stage 3 Tier K (2026-05-16): plumb --no-adversarial through.
        # Default False; when set, phase_review skips the canonical
        # beril-adversarial reviewer and uses the inline fallback
        # explicitly (no warning — user has chosen).
        orch = PaperWriterOrchestrator(
            draft_dir,
            max_cost_usd=getattr(args, "max_cost_usd", None),
            model=getattr(args, "model", None) or "claude-opus-4-6",
            model_writing=getattr(args, "model_writing", None) or "claude-opus-4-6",
            no_adversarial=getattr(args, "no_adversarial", False),
            # Stage 4 Tier S: --ship-with-p0s is the only gate-related
            # flag exposed on `draft`. --remediate has no meaning on a
            # fresh run (there are no P0s to remediate yet), and
            # --max-remediate-cycles is policy for the continue path
            # only.
            ship_with_p0s=getattr(args, "ship_with_p0s", False),
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        print(
            f"\nThe draft directory {draft_dir} was created but the run "
            f"never started; it is safe to remove:\n  rm -rf {draft_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        asyncio.run(orch.run_pipeline())
        # run_pipeline catches PipelineHalted internally and prints the
        # throughline-pick handoff itself; a normal return means the
        # pipeline paused cleanly or finished.
        return 0
    except Exception as e:
        print(f"error: pipeline execution failed: {e}", file=sys.stderr)
        return 2