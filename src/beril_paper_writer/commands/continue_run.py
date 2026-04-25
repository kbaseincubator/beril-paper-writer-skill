"""`beril-paper-writer continue <draft_dir>` — Phase 4 stub.

Resumes a paused paper draft per SPEC §5.5 (intercalation hash-diff +
explicit user confirmation before integrating any changes). The full
implementation lands with Phase 4 (the orchestrator); this stub validates
arguments and reports the planned behavior so users see consistent CLI
help across releases.

When implementation lands, this becomes a thin shell that:
  1. Reads draft_dir/state.json (via state.load_state)
  2. Re-hashes source artifacts; diffs vs state.json
  3. Reports changes to the user via stderr; exits if user has un-resolved
     manuscript edits
  4. Dispatches to the right phase handler based on state.phase
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from beril_paper_writer import __version__, state

_NOT_IMPLEMENTED_MSG = (
    "beril-paper-writer continue is declared in the planned CLI but is not\n"
    "yet implemented. The orchestrator lands in Phase 4 (see SPEC §5.5 and\n"
    "LAYOUT 'Repository tree' for the planned tools/paper_writer.sh).\n"
    "\n"
    "Phase 1 ({ver}) ships only the install / configure / state-tracking\n"
    "primitives. State files at {draft_dir}/state.json can be inspected\n"
    "manually; the resume logic is forthcoming.\n"
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "continue",
        help="Resume a paused paper draft (planned — Phase 4).",
        description=(
            "Resume a paper draft that paused at a user-decision point "
            "(throughline pick, gap-fill response, or review acceptance). "
            "Per SPEC §5.5: hash-diffs source artifacts on resume; reports "
            "changes explicitly before integrating; never silently rebuilds "
            "the throughline."
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
        "--no-stream",
        action="store_true",
        help="Disable stream-json parsing (planned — Phase 4 default is on).",
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

    # If a state.json exists, give the user a brief peek (Phase 1 affordance:
    # at least the user can see what's in there even before continue logic
    # exists). Useful for testing the state module against real layouts.
    state_file = state.state_path(draft_dir)
    if state_file.is_file():
        try:
            st = state.load_state(draft_dir)
            print(f"draft_dir: {draft_dir}")
            print(f"state.json found:")
            print(f"  schema version: {st.version}")
            print(f"  project_id:     {st.project_id or '(unset)'}")
            print(f"  draft_number:   {st.draft_number}")
            print(f"  phase:          {st.phase}")
            print(f"  mode:           {st.mode}")
            print(f"  tier:           {st.tier or '(not yet triaged)'}")
            print(
                f"  throughline:    "
                f"{st.throughline.candidate_id or '(not yet picked)'}"
            )
            print(
                f"  iteration:      "
                f"{st.iteration.gap_fill_rounds} gap-fill round(s), "
                f"{st.iteration.rewrite_passes} rewrite pass(es)"
            )
            print(f"  cost so far:    ${st.cost_so_far_usd:.2f}")
            print(f"  last updated:   {st.last_updated or '(never)'}")
            print()
        except (OSError, ValueError) as e:
            print(
                f"Note: could not read state.json: {e}. "
                f"Continuing with stub message.",
                file=sys.stderr,
            )

    sys.stderr.write(_NOT_IMPLEMENTED_MSG.format(ver=__version__, draft_dir=draft_dir))
    return 2
