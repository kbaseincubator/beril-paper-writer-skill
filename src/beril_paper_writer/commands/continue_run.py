"""`beril-paper-writer continue <draft_dir> [--pick TLN] [--revision TEXT]`

Resume a paused paper draft. The behavior depends on state.json's `phase`:

- phase=throughline_pick (the most common case)
    Requires --pick TLN. If --revision is non-empty, invokes
    revise_throughline.v1.md via `claude -p` to refine the chosen
    candidate per the user's revision text. Otherwise copies the
    chosen candidate verbatim into 00_throughline.md. Then sets
    phase=drafting and dispatches to paper_writer.sh resume, which
    drafts citation_pool → methods → ... → abstract → assemble →
    review and pauses at phase=review (the final handoff).

- phase=drafting / phase=review
    Re-dispatches to paper_writer.sh resume with no further action.
    Useful when a prior run halted mid-draft (claude failure, retry
    exhaustion). The shell script's idempotency handles the rest.

- phase=assembled
    Reports "already complete" and exits 0.

- phase=init
    Re-dispatches to paper_writer.sh resume which re-runs the early
    phases idempotently and pauses at throughline_pick.

Per SPEC §5.5 (intercalation hash-diff): on resume, source-artifact
hashes are recomputed and compared against state.json's recorded
hashes. Any drift surfaces a warning before drafting continues. v0.1
implementation does the comparison but does NOT auto-rebuild the
throughline; instead emits a warning and continues. v0.2 adds the
re-evaluation prompt path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from beril_paper_writer import __version__, state


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "continue",
        help="Resume a paused paper draft.",
        description=(
            "Resume a paper draft that paused at a user-decision point. "
            "On phase=throughline_pick: requires --pick TLN; if --revision "
            "is provided, invokes revise_throughline.v1.md to refine the "
            "chosen candidate. Otherwise carries the candidate verbatim. "
            "Then runs the drafting pipeline through to phase=review."
        ),
    )
    p.add_argument(
        "draft_dir",
        help="Path to the paper draft directory (e.g. projects/<id>/papers/draft_1/).",
    )
    p.add_argument(
        "--pick",
        default=None,
        help=(
            "Throughline candidate id (e.g. TL2). Required when "
            "phase=throughline_pick. Must match an `## Candidate TLN:` "
            "header in throughline_candidates.md."
        ),
    )
    p.add_argument(
        "--revision",
        default=None,
        help=(
            "Optional revision note for the chosen throughline. If "
            "non-empty, invokes revise_throughline.v1.md to refine "
            "the candidate. If absent or empty, the chosen candidate "
            "is copied verbatim into 00_throughline.md."
        ),
    )
    p.add_argument(
        "--model",
        default=None,
        help="Override default model. Forwarded to paper_writer.sh and the revise step.",
    )
    p.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable stream_progress.py wrapper.",
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


# ----------------------------------------------------------------------------
# Package-data path resolution
# ----------------------------------------------------------------------------


def _locate_skill_resource(*parts: str) -> Path:
    """Resolve a path inside beril_paper_writer/skill/."""
    try:
        ref = resources.files("beril_paper_writer").joinpath("skill", *parts)
        with resources.as_file(ref) as p:
            return Path(p)
    except (ModuleNotFoundError, FileNotFoundError) as e:
        raise FileNotFoundError(
            f"package resource not found: skill/{'/'.join(parts)}. "
            "Reinstall beril-paper-writer-skill."
        ) from e


# ----------------------------------------------------------------------------
# Throughline-pick handling
# ----------------------------------------------------------------------------


def _extract_candidate_block(candidates_path: Path, pick: str) -> str | None:
    """Return the verbatim text of the picked candidate's `## Candidate TLN:`
    block, or None if pick not found.

    The block runs from the `## Candidate TLN:` line through (but not
    including) the next `## Candidate TLN:` H2 or end of file. Trailing
    `---` separator lines are trimmed.
    """
    if not candidates_path.is_file():
        return None
    text = candidates_path.read_text(encoding="utf-8")
    # Find the start of the picked candidate.
    needle = f"## Candidate {pick}:"
    start = text.find(needle)
    if start == -1:
        return None
    # Find the next `## Candidate ` after start.
    next_h2 = text.find("\n## Candidate ", start + len(needle))
    if next_h2 == -1:
        block = text[start:]
    else:
        block = text[start:next_h2]
    # Trim trailing horizontal rules.
    lines = block.rstrip().split("\n")
    while lines and lines[-1].strip() in ("---", ""):
        lines.pop()
    return "\n".join(lines) + "\n"


def _write_throughline_verbatim(
    draft_dir: Path, candidate_block: str, pick: str
) -> None:
    """Write 00_throughline.md as the candidate block converted to a
    single-throughline format (H1 instead of H2 candidate-wrapper)."""
    # Convert `## Candidate TLN: <title>` to H1 `# Throughline\n\n**Selected:** TLN\n\n**Statement:** <title>\n`
    lines = candidate_block.split("\n")
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("## Candidate "):
            # Extract everything after `## Candidate TLN: `
            after_colon = line.split(":", 1)
            title = after_colon[1].strip() if len(after_colon) > 1 else ""
            if not title:
                print(
                    f"warning: candidate header has empty title: {line!r}",
                    file=sys.stderr,
                )
            body_start = i + 1
            break
    rest = "\n".join(lines[body_start:]).strip()

    header = (
        f"# Throughline\n"
        f"\n"
        f"**Selected:** {pick} (carried verbatim from plan.v1 candidate; "
        f"no user revision applied).\n"
        f"\n"
        f"**Statement:** {title}\n"
        f"\n"
    )
    target = draft_dir / "00_throughline.md"
    target.write_text(header + rest + "\n", encoding="utf-8")


def _invoke_revise_throughline(
    draft_dir: Path,
    candidate_block: str,
    pick: str,
    revision_text: str,
    model: str,
    no_stream: bool,
) -> int:
    """Invoke revise_throughline.v1.md via claude -p to produce a refined
    00_throughline.md.

    Returns 0 on success, non-zero on failure. The shell script's
    invoke_claude_with_retry pattern is mirrored here so we get the
    same Write-verification + retry behavior.
    """
    if shutil.which("claude") is None:
        print("error: 'claude' CLI not on PATH; cannot run revise step", file=sys.stderr)
        return 3

    sys_prompt_path = _locate_skill_resource("prompts", "revise_throughline.v1.md")
    sys_prompt = sys_prompt_path.read_text(encoding="utf-8")
    target = draft_dir / "00_throughline.md"
    state_data = state.load_state(draft_dir)
    project_id = state_data.project_id or "(unknown)"
    project_root = draft_dir.parent.parent  # papers/draft_N → ../..

    today_iso = (
        __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .strftime("%Y-%m-%d")
    )

    user_prompt = f"""Run revise_throughline.v1 to produce {target}.

## Inputs

- `CHOSEN_CANDIDATE_BLOCK` (verbatim from throughline_candidates.md):

{candidate_block}

(end of CHOSEN_CANDIDATE_BLOCK)

- `USER_REVISION_TEXT`:

{revision_text}

(end of USER_REVISION_TEXT)

- `THROUGHLINE_OUT_PATH` = `{target}`
- `PROJECT_ROOT` = `{project_root}`
- `REPORT_PATH` = `{project_root}/REPORT.md`
- `RESEARCH_PLAN_PATH` = `{project_root}/RESEARCH_PLAN.md`
- `REFRAMING_LOG_PATH` = `{draft_dir}/reframing_log.md`
- `TODAY` = `{today_iso}`
- `PLAN_RUN_DATE` = `{state_data.last_updated or today_iso}`

Apply the user's revision per your system prompt's discipline pass. Write THROUGHLINE_OUT_PATH via the Write tool, then emit the closing-message template."""

    tools_dir = _locate_skill_resource("tools").parent / "tools"  # ../tools
    stream_script = _locate_skill_resource("tools", "stream_progress.py")
    metadata_path = draft_dir / "audit" / "revise_throughline.metadata.json"
    log_path = draft_dir / "audit" / "revise_throughline.stream.log"

    print(f"▸ Invoking revise_throughline.v1 via claude -p", file=sys.stderr)

    use_stream = not no_stream
    if use_stream:
        # Pipe through stream_progress.py for Write verification.
        claude_cmd = [
            "claude", "-p",
            "--model", model,
            "--system-prompt", sys_prompt,
            "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
            user_prompt,
        ]
        env = {**os.environ, "CLAUDECODE": ""}
        claude = subprocess.Popen(
            claude_cmd,
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        parser_cmd = [
            sys.executable, str(stream_script),
            "--expected-write-path", str(target),
            "--log", str(log_path),
            "--model", model,
            "--metadata-out", str(metadata_path),
            "--label", "revise_throughline.v1",
        ]
        parser = subprocess.Popen(
            parser_cmd,
            stdin=claude.stdout,
            stdout=subprocess.DEVNULL,
        )
        claude.stdout.close()  # type: ignore[union-attr]
        rc = parser.wait()
        claude.wait()
        if rc == 0 and log_path.is_file():
            try:
                log_path.unlink()
            except OSError:
                pass
        return rc
    else:
        # Direct invocation, no stream parser.
        rc = subprocess.run(
            [
                "claude", "-p",
                "--model", model,
                "--system-prompt", sys_prompt,
                "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
                "--dangerously-skip-permissions",
                user_prompt,
            ],
            stdin=subprocess.DEVNULL,
        ).returncode
        return rc


# ----------------------------------------------------------------------------
# Main run logic
# ----------------------------------------------------------------------------



def _resume_via_orchestrator(
    draft_dir: Path,
    max_cost_usd: float | None = None,
    no_adversarial: bool = False,
) -> int:
    import asyncio
    from beril_paper_writer.orchestrator import PaperWriterOrchestrator

    # Stage 3 Tier K (2026-05-16): plumb --no-adversarial through to
    # the orchestrator. Previously the continue path constructed the
    # orchestrator with defaults only, so a user passing
    # `--no-adversarial` to `continue` would have been silently
    # ignored.
    orch = PaperWriterOrchestrator(
        draft_dir,
        max_cost_usd=max_cost_usd,
        no_adversarial=no_adversarial,
    )
    try:
        asyncio.run(orch.run_pipeline())
        return 0
    except Exception as e:
        print(f"error: pipeline execution failed: {e}", file=sys.stderr)
        return 2


def run(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir does not exist: {draft_dir}", file=sys.stderr)
        return 1

    try:
        st = state.load_state(draft_dir)
    except (OSError, ValueError) as e:
        print(f"error: cannot load state.json: {e}", file=sys.stderr)
        return 2

    print(f"  draft_dir: {draft_dir}", file=sys.stderr)
    print(f"  phase:     {st.phase}", file=sys.stderr)
    print(f"  project:   {st.project_id or '(unset)'}", file=sys.stderr)
    print("", file=sys.stderr)

    model = args.model or "claude-sonnet-4-5-20250929"

    # Phase-specific dispatch.
    if st.phase == "throughline_pick":
        if not args.pick:
            print(
                "error: phase=throughline_pick requires --pick TLN. "
                f"Inspect candidates at {draft_dir}/throughline_candidates.md "
                "and choose one.",
                file=sys.stderr,
            )
            return 1

        candidates_path = draft_dir / "throughline_candidates.md"
        block = _extract_candidate_block(candidates_path, args.pick)
        if block is None:
            print(
                f"error: candidate {args.pick!r} not found in {candidates_path}. "
                f"Available candidates can be listed via "
                f"`grep '^## Candidate' {candidates_path}`.",
                file=sys.stderr,
            )
            return 1

        revision = (args.revision or "").strip()
        if revision:
            print(f"▸ Refining {args.pick} per user revision", file=sys.stderr)
            rc = _invoke_revise_throughline(
                draft_dir, block, args.pick, revision,
                model, args.no_stream,
            )
            if rc != 0:
                print(
                    f"error: revise_throughline.v1 invocation failed (exit {rc}); "
                    f"00_throughline.md may not have been written. "
                    "Inspect the audit log and re-run.",
                    file=sys.stderr,
                )
                return 2
        else:
            print(f"▸ Carrying {args.pick} verbatim into 00_throughline.md", file=sys.stderr)
            _write_throughline_verbatim(draft_dir, block, args.pick)

        # Update state.json: phase=drafting, throughline.candidate_id=pick,
        # throughline.chosen_at=now, etc.
        from datetime import datetime, timezone
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        # Stage 2 Tier B (2026-05-11): advance to citation_pool, not
        # drafting. Previously this skipped phase_citation_pool entirely,
        # which is why draft_4's citation_pool.json was never produced
        # and the holistic_draft prompt had no verified pool to draw
        # from — it fabricated 27 inline citations from training
        # knowledge. The run_pipeline state machine flows
        # throughline_pick → citation_pool → drafting; setting "drafting"
        # here bypassed the citation phase. Setting "citation_pool" now
        # makes the natural flow happen.
        st.phase = "citation_pool"
        st.throughline.candidate_id = args.pick
        st.throughline.chosen_at = now_iso
        if revision:
            st.throughline.revision += 1
        state.save_state(draft_dir, st)
        print(f"✓ state.json updated: phase=citation_pool, throughline={args.pick}", file=sys.stderr)

        # Now dispatch to paper_writer.sh resume to run the drafting phases.
        return _resume_via_orchestrator(
            draft_dir,
            max_cost_usd=getattr(args, "max_cost_usd", None),
            no_adversarial=getattr(args, "no_adversarial", False),
        )

    elif st.phase in ("init", "citation_pool", "drafting", "supplementary_pool", "review", "optimize", "rewrite", "compliance_gate", "compliance"):
        # paper_writer.sh handles each of these idempotently.
        return _resume_via_orchestrator(
            draft_dir,
            max_cost_usd=getattr(args, "max_cost_usd", None),
            no_adversarial=getattr(args, "no_adversarial", False),
        )

    elif st.phase == "assembled":
        print("✓ Already complete (phase=assembled).", file=sys.stderr)
        manuscript = draft_dir / "manuscript.md"
        if manuscript.is_file():
            print(f"  Manuscript: {manuscript}", file=sys.stderr)
        reviews_dir = draft_dir / "reviews"
        review = next(iter(reviews_dir.glob("draft_*_review_*.md")), None) if reviews_dir.is_dir() else None
        if review:
            print(f"  Review:     {review}", file=sys.stderr)
        return 0

    else:
        print(
            f"error: unknown phase {st.phase!r} in state.json; cannot resume.",
            file=sys.stderr,
        )
        return 2
