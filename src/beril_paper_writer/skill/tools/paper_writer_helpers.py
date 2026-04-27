#!/usr/bin/env python3
"""paper_writer_helpers.py — Python shims for paper_writer.sh.

Bash is fine for sequencing prompt invocations and copying files, but it
is bad at mutating JSON files atomically and at building structured
handoff payloads. This module gives the orchestrator a small set of
single-shot CLI subcommands for those tasks.

Subcommands (each callable as `python3 paper_writer_helpers.py <cmd> ...`):

    write-handoff <draft_dir>
        --phase <phase>
        --prompt-to-user <text>
        [--choice id1=label1] [--choice id2=label2] ...
        [--advisory-warning <text>] [--advisory-warning ...]
        [--candidates-path <path>]
        [--review-path <path>]
        [--resume-command <text>]
        [--needs-citation-count <int>]
            Writes <draft_dir>/.handoff.json atomically. Always overwrites
            any prior handoff (the orchestrator emits a new one at every
            pause). The schema is documented inline.

    update-state <draft_dir>
        --phase <phase>
        [--throughline-id <id>] [--throughline-revision <text>]
        [--add-cost <usd>]
        [--add-elapsed-seconds <int>]
            Atomic mutation of state.json. Loads via state.load_state,
            applies the named changes, saves via state.save_state. Bash
            cannot do atomic JSON edits; this is the canonical mutation
            point for cost/elapsed accounting and phase transitions.

    fill-template <template_path> <output_path>
        --var key=value [--var key=value ...]
            Reads the template, substitutes `{key}` placeholders, writes
            the result atomically to output_path. Used for AI_DISCLOSURE
            and data-availability template fills.

    init-reframing-log <draft_dir>
            Creates <draft_dir>/reframing_log.md with a `# Reframing Log`
            header + blank line if the file does not yet exist. Idempotent.

    validate-handoff <draft_dir>
            Reads <draft_dir>/.handoff.json and verifies it matches the
            schema (required fields per phase). Used by continue_run to
            sanity-check before parsing user input. Exit 0 = valid;
            exit 1 = malformed; exit 2 = file missing.

    aggregate-metadata <draft_dir> [--out <path>]
            Reads <draft_dir>/audit/*.metadata.json sidecars (written by
            stream_progress.py) and produces a cumulative summary JSON
            at <path> (default: <draft_dir>/audit/run_metadata.json).
            Sums tokens, costs, elapsed seconds across all sub-calls.

Exit codes:
    0  success
    1  user error (missing required arg, file not found, malformed input)
    2  not-yet-existent file the orchestrator should have created
       (e.g., .handoff.json missing when validate-handoff runs)
    3  internal error (file system, JSON encode failure)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

# Import the package state module by relative-path hack: this script ships
# under the skill/tools/ tree but the orchestrator runs it via python3
# directly (not as a module), so the `beril_paper_writer.state` import
# would fail. Re-implement the minimal pieces needed (atomic write,
# state.json round-trip) inline. Trade-off: small duplication of the
# atomic-write helper, in exchange for the helper script being usable
# without the package being importable. This matches how stream_progress.py
# works (no package imports).


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Write JSON to target atomically. Creates parent dirs if needed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_text(target: Path, content: str) -> None:
    """Write text to target atomically. Creates parent dirs if needed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Subcommand: write-handoff
# ---------------------------------------------------------------------------


# Phase-specific required fields. The orchestrator must supply these or
# the handoff is malformed; validate-handoff enforces this.
_HANDOFF_REQUIRED_FIELDS_BY_PHASE = {
    "throughline_pick": ("prompt_to_user", "choices", "candidates_path", "resume_command"),
    "review": ("prompt_to_user", "review_path", "resume_command"),
    "drafted": ("prompt_to_user",),  # final pause when manuscript is done
    "halted": ("prompt_to_user",),   # error pause; the orchestrator failed somewhere
}


def cmd_write_handoff(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    phase = args.phase
    payload: dict[str, Any] = {
        "phase": phase,
        "prompt_to_user": args.prompt_to_user,
    }

    # Choices: prefer --choices-json (robust to spaces/quotes/punctuation
    # in labels); fall back to --choice id=label flags for back-compat
    # with simple cases. The JSON-file path is what the orchestrator
    # uses in production after the live-run argparse failure (2026-04-26)
    # showed bash word-splitting candidate labels with spaces.
    if args.choices_json:
        try:
            with Path(args.choices_json).open(encoding="utf-8") as f:
                choices_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"error: --choices-json {args.choices_json!r} could not be "
                f"loaded: {e}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(choices_data, list):
            print(
                f"error: --choices-json must contain a JSON list of "
                f"{{id, label}} objects; got {type(choices_data).__name__}",
                file=sys.stderr,
            )
            return 1
        payload["choices"] = choices_data
    elif args.choice:
        choices = []
        for entry in args.choice:
            if "=" not in entry:
                print(
                    f"error: --choice expects id=label form; got: {entry!r}",
                    file=sys.stderr,
                )
                return 1
            cid, _, label = entry.partition("=")
            choices.append({"id": cid.strip(), "label": label.strip()})
        payload["choices"] = choices

    # Advisory warnings: prefer --advisory-warnings-json (same robustness
    # rationale as choices). Fall back to repeated --advisory-warning flags.
    if args.advisory_warnings_json:
        try:
            with Path(args.advisory_warnings_json).open(encoding="utf-8") as f:
                warnings_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"error: --advisory-warnings-json {args.advisory_warnings_json!r} "
                f"could not be loaded: {e}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(warnings_data, list):
            print(
                f"error: --advisory-warnings-json must contain a JSON list "
                f"of strings; got {type(warnings_data).__name__}",
                file=sys.stderr,
            )
            return 1
        payload["advisory_warnings"] = [str(w) for w in warnings_data]
    elif args.advisory_warning:
        payload["advisory_warnings"] = list(args.advisory_warning)

    if args.candidates_path:
        payload["candidates_path"] = str(Path(args.candidates_path).expanduser())

    if args.review_path:
        payload["review_path"] = str(Path(args.review_path).expanduser())

    if args.resume_command:
        payload["resume_command"] = args.resume_command

    if args.needs_citation_count is not None:
        payload["needs_citation_count"] = int(args.needs_citation_count)

    # Per-phase required-field check. Soft (warning, not error) so the
    # orchestrator can emit halted handoffs without strict-mode pain.
    required = _HANDOFF_REQUIRED_FIELDS_BY_PHASE.get(phase, ())
    missing = [f for f in required if f not in payload]
    if missing:
        print(
            f"warning: handoff for phase {phase!r} missing recommended "
            f"fields: {missing}. Writing anyway.",
            file=sys.stderr,
        )

    target = draft_dir / ".handoff.json"
    _atomic_write_json(target, payload)
    print(f"wrote {target}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: update-state
# ---------------------------------------------------------------------------


def cmd_update_state(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    state_path = draft_dir / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"error: state.json is malformed: {e}", file=sys.stderr)
            return 3
    else:
        # Fresh state — minimal valid structure. The full schema is in
        # state.py; we replicate enough fields to round-trip cleanly with
        # state.load_state.
        state = {
            "version": "0.1",
            "project_id": "",
            "draft_number": 1,
            "phase": "init",
            "mode": "paper",
            "tier": None,
            "throughline": {
                "candidate_id": None,
                "chosen_at": None,
                "revision": 0,
                "artifact_hash_at_confirmation": None,
                "reevaluations": [],
            },
            "source_artifacts": [],
            "manuscript_files": [],
            "analysis_requests": [],
            "iteration": {"rewrite_passes": 0, "gap_fill_rounds": 0},
            "validator_status": {},
            "cost_so_far_usd": 0.0,
            "elapsed_seconds": 0.0,
            "last_updated": None,
        }

    # Apply mutations.
    if args.phase:
        state["phase"] = args.phase

    if args.throughline_id:
        state.setdefault("throughline", {})["candidate_id"] = args.throughline_id
        # Bump revision counter on every update.
        rev = int(state["throughline"].get("revision", 0))
        state["throughline"]["revision"] = rev + 1

    if args.add_cost:
        state["cost_so_far_usd"] = float(state.get("cost_so_far_usd", 0.0)) + float(args.add_cost)

    if args.add_elapsed_seconds:
        state["elapsed_seconds"] = float(state.get("elapsed_seconds", 0.0)) + float(
            args.add_elapsed_seconds
        )

    # Always touch last_updated.
    from datetime import datetime, timezone
    state["last_updated"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    _atomic_write_json(state_path, state)
    print(f"updated {state_path} (phase={state['phase']})", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: fill-template
# ---------------------------------------------------------------------------


def cmd_fill_template(args: argparse.Namespace) -> int:
    template_path = Path(args.template_path).expanduser().resolve()
    output_path = Path(args.output_path).expanduser().resolve()

    if not template_path.is_file():
        print(f"error: template not found: {template_path}", file=sys.stderr)
        return 1

    text = template_path.read_text(encoding="utf-8")

    if args.var:
        for entry in args.var:
            if "=" not in entry:
                print(
                    f"error: --var expects key=value form; got: {entry!r}",
                    file=sys.stderr,
                )
                return 1
            key, _, value = entry.partition("=")
            text = text.replace("{" + key.strip() + "}", value)

    _atomic_write_text(output_path, text)
    print(f"wrote {output_path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: init-reframing-log
# ---------------------------------------------------------------------------


_REFRAMING_LOG_HEADER = "# Reframing Log\n\n"


def cmd_init_reframing_log(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    target = draft_dir / "reframing_log.md"
    if target.is_file():
        print(f"reframing_log.md already exists at {target} (no-op)", file=sys.stderr)
        return 0
    _atomic_write_text(target, _REFRAMING_LOG_HEADER)
    print(f"wrote {target}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: validate-handoff
# ---------------------------------------------------------------------------


def cmd_validate_handoff(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    target = draft_dir / ".handoff.json"
    if not target.is_file():
        print(f"error: .handoff.json not found at {target}", file=sys.stderr)
        return 2

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: .handoff.json is malformed JSON: {e}", file=sys.stderr)
        return 1

    phase = payload.get("phase")
    if not phase:
        print("error: .handoff.json missing 'phase' field", file=sys.stderr)
        return 1

    required = _HANDOFF_REQUIRED_FIELDS_BY_PHASE.get(phase)
    if required is None:
        print(
            f"warning: phase {phase!r} not in known-phase set; not enforcing fields",
            file=sys.stderr,
        )
        return 0

    missing = [f for f in required if f not in payload]
    if missing:
        print(
            f"error: .handoff.json for phase {phase!r} missing required "
            f"fields: {missing}",
            file=sys.stderr,
        )
        return 1

    print(f"ok: phase={phase} (all required fields present)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: aggregate-metadata
# ---------------------------------------------------------------------------


def cmd_acquire_lock(args: argparse.Namespace) -> int:
    """Acquire a best-effort exclusive lock on <draft_dir>/.lock.

    Replaces the bash `flock` invocation (flock isn't available on macOS
    by default — installable via `brew install flock` but we don't want
    to require that). Implementation: PID-file with liveness check.

    Race window: if two helpers fire simultaneously, both could pass the
    no-existing-lock check and both write. Race window ~1ms; documented
    as best-effort, not POSIX-strict mutex. Sufficient for the use case
    ("user accidentally invokes resume twice in two seconds"); not for
    high-contention scenarios.

    Lock file body:
        pid=<orchestrator-bash-PID> verb=<draft|resume> started=<UTC ISO> host=<hostname>

    The orchestrator passes its own PID via $$, not the helper's PID.
    On script exit (normal or via trap on INT/TERM), the orchestrator
    removes the lock file. A stale lock (file exists but holder PID
    is dead) is overwritten on next acquire — kill -0 check.

    Exit codes:
        0  acquired (or stale-lock overwritten)
        1  contention — active holder named in stderr
        2  internal error (filesystem, etc.)
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 2
    lock_file = draft_dir / ".lock"

    # Check existing lock for liveness.
    if lock_file.is_file():
        try:
            existing = lock_file.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        # Parse pid=N from the lock body
        m = re.search(r"pid=(\d+)", existing)
        existing_pid: Optional[int] = None
        if m:
            try:
                existing_pid = int(m.group(1))
            except ValueError:
                existing_pid = None
        # Liveness check via os.kill(pid, 0) — POSIX, raises ProcessLookupError
        # on dead PID, raises PermissionError on alive-but-foreign PID.
        if existing_pid is not None:
            holder_alive = False
            try:
                os.kill(existing_pid, 0)
                holder_alive = True
            except ProcessLookupError:
                holder_alive = False
            except PermissionError:
                # Different user owns the process — alive but not signalable.
                # Conservative: treat as alive (we don't want to clobber
                # someone else's lock).
                holder_alive = True
            except OSError:
                holder_alive = True   # err on side of "active"
            if holder_alive:
                print(
                    f"error: lock held by active process — {existing}",
                    file=sys.stderr,
                )
                print(
                    f"  Lock file: {lock_file}",
                    file=sys.stderr,
                )
                print(
                    "  If you believe this is stale (e.g., you killed an "
                    "earlier paper_writer with kill -9 and the trap didn't "
                    "fire), remove the lock file manually:",
                    file=sys.stderr,
                )
                print(f"    rm {lock_file}", file=sys.stderr)
                return 1
        # Stale lock — fall through and overwrite.
        print(
            f"note: overwriting stale lock (holder pid={existing_pid} "
            f"is no longer alive)",
            file=sys.stderr,
        )

    # Build the lock body.
    from datetime import datetime, timezone
    import socket
    holder_pid = args.pid if args.pid else os.getppid()
    body = (
        f"pid={holder_pid} verb={args.verb} "
        f"started={datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')} "
        f"host={socket.gethostname()}\n"
    )
    _atomic_write_text(lock_file, body)
    print(f"acquired lock for pid={holder_pid} verb={args.verb}", file=sys.stderr)
    return 0


def cmd_release_lock(args: argparse.Namespace) -> int:
    """Release the lock by removing <draft_dir>/.lock.

    Idempotent: removing a non-existent lock is OK. The orchestrator's
    bash trap calls this on EXIT/INT/TERM, so the lock is released even
    on abnormal termination — within the bash trap's reliability
    envelope (kill -9 won't fire the trap).
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    lock_file = draft_dir / ".lock"
    if lock_file.is_file():
        try:
            lock_file.unlink()
            print(f"released lock at {lock_file}", file=sys.stderr)
        except OSError as e:
            print(f"warning: could not remove lock file: {e}", file=sys.stderr)
            return 1
    return 0


def cmd_emit_next_actions(args: argparse.Namespace) -> int:
    """Aggregate validator failures + reviewer-flagged issues + citation
    finalize warnings into <draft_dir>/next_actions.md.

    Sources:
      - audit/validation.json — validator pass/fail (M1-M10)
      - reviews/draft_*_review_*.md (latest) — adversarial or fallback
        reviewer's findings; we extract critical-only by default
      - finalize_warnings.md — orphaned [bib_key] citations from
        citation_pool.py finalize

    The output is a checklist the user works through before submission.
    Does NOT block the run; orchestrator surfaces it via the final handoff.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    lines: list[str] = ["# Next Actions", ""]
    lines.append(
        "Aggregated from `audit/validation.json` + adversarial-review "
        "output + `finalize_warnings.md` at end of pipeline. Work "
        "through this checklist before submission."
    )
    lines.append("")

    # 1. Validator failures
    validation_path = draft_dir / "audit" / "validation.json"
    val_failures: list[dict] = []
    if validation_path.is_file():
        try:
            v = json.loads(validation_path.read_text(encoding="utf-8"))
            for entry in v.get("validators", []):
                if entry.get("status") == "fail":
                    val_failures.append(entry)
        except json.JSONDecodeError:
            pass

    lines.append("## Validator failures (mechanized M1-M10 checks)")
    lines.append("")
    if val_failures:
        lines.append(f"_{len(val_failures)} validator(s) failing._")
        lines.append("")
        for entry in val_failures:
            for viol in entry.get("violations", []):
                msg = viol.get("message", "(no message)")
                escalation = viol.get("escalation_path", "user-modify")
                lines.append(f"- **{entry['id']}** ({entry.get('name', '')}) — {msg}")
                lines.append(f"    - Escalation: `{escalation}`")
        lines.append("")
    else:
        lines.append("_All validators pass. (None failing as of pipeline end.)_")
        lines.append("")

    # 2. Citation finalize warnings (orphans)
    warnings_path = draft_dir / "finalize_warnings.md"
    lines.append("## Citation orphans (from finalize)")
    lines.append("")
    if warnings_path.is_file():
        body = warnings_path.read_text(encoding="utf-8")
        if "orphaned citation" in body.lower():
            # Pull out the orphan-list portion (everything after the heading)
            after_h1 = body.split("\n", 2)[2] if body.count("\n") >= 2 else body
            lines.append(after_h1.strip())
            lines.append("")
        else:
            lines.append("_No orphaned `[bib_key]` citations._")
            lines.append("")
    else:
        lines.append("_(finalize_warnings.md not present — citation finalize may not have run.)_")
        lines.append("")

    # 3. Reviewer issues (critical only)
    reviews_dir = draft_dir / "reviews"
    review_files: list[Path] = []
    if reviews_dir.is_dir():
        review_files = sorted(reviews_dir.glob("draft_*_review_*.md"))
    latest_review = review_files[-1] if review_files else None

    lines.append("## Reviewer issues — critical only")
    lines.append("")
    if latest_review and latest_review.is_file():
        body = latest_review.read_text(encoding="utf-8")
        lines.append(f"Source: `{latest_review.name}`")
        lines.append("")
        # Extract `## Critical` (or `### Critical`) section content; stop at
        # next H1/H2/H3 of the same or higher level.
        crit_match = re.search(
            r"^(#{2,3})\s+Critical\s*$([\s\S]*?)(?=^#{1,3}\s+|\Z)",
            body, re.MULTILINE,
        )
        if crit_match:
            crit_body = crit_match.group(2).strip()
            if crit_body:
                lines.append(crit_body)
            else:
                lines.append("_No critical issues flagged in the review._")
        else:
            # No "Critical" section header — surface a count-only summary
            n_lines = body.count("\n")
            lines.append(
                f"_(Review file is {n_lines} lines; no `## Critical` header "
                f"found. Read the file directly: `{latest_review}`)_"
            )
        lines.append("")
        # Also count ##/### sections for context
        all_headings = re.findall(r"^#{2,3}\s+(.+?)$", body, re.MULTILINE)
        if all_headings:
            lines.append(
                f"_Review headings found: {', '.join(all_headings[:10])}"
                f"{' ...' if len(all_headings) > 10 else ''}_"
            )
            lines.append("")
    else:
        lines.append("_(No review file at `reviews/draft_*_review_*.md`.)_")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*Generated by `paper_writer_helpers.py emit-next-actions` at "
        "pipeline end. Re-runnable; this file is rewritten on every "
        "orchestrator final-handoff emission.*"
    )

    target = draft_dir / "next_actions.md"
    _atomic_write_text(target, "\n".join(lines) + "\n")
    print(
        f"wrote {target} ({len(val_failures)} validator failure(s), "
        f"{'orphans present' if (warnings_path.is_file() and 'orphaned citation' in warnings_path.read_text(encoding='utf-8').lower()) else 'no orphans'}, "
        f"{'review found' if latest_review else 'no review'})",
        file=sys.stderr,
    )
    return 0


def cmd_aggregate_metadata(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    audit_dir = draft_dir / "audit"
    if not audit_dir.is_dir():
        print(f"error: audit dir not found: {audit_dir}", file=sys.stderr)
        return 1

    sidecars = sorted(audit_dir.glob("*.metadata.json"))
    if not sidecars:
        print(
            f"warning: no *.metadata.json sidecars found in {audit_dir}; "
            f"writing empty aggregate.",
            file=sys.stderr,
        )

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "estimated_cost_usd": 0.0,
        "elapsed_seconds": 0,
        "calls": [],
    }

    for sc in sidecars:
        try:
            d = json.loads(sc.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"warning: skipping malformed sidecar {sc}", file=sys.stderr)
            continue
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            totals[k] += int(d.get(k, 0))
        totals["estimated_cost_usd"] += float(d.get("estimated_cost_usd", 0.0))
        totals["elapsed_seconds"] += int(d.get("elapsed_seconds", 0))
        totals["calls"].append(
            {
                "sidecar": sc.name,
                "label": d.get("label", sc.stem.replace(".metadata", "")),
                "input_tokens": d.get("input_tokens", 0),
                "output_tokens": d.get("output_tokens", 0),
                "elapsed_seconds": d.get("elapsed_seconds", 0),
                "estimated_cost_usd": d.get("estimated_cost_usd", 0.0),
                "model": d.get("model"),
            }
        )

    out_path = Path(args.out).expanduser().resolve() if args.out else (audit_dir / "run_metadata.json")
    _atomic_write_json(out_path, totals)
    print(
        f"wrote {out_path}: {len(totals['calls'])} call(s), "
        f"${totals['estimated_cost_usd']:.3f} total",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    # write-handoff
    p_wh = sub.add_parser("write-handoff", help="Write .handoff.json atomically.")
    p_wh.add_argument("draft_dir")
    p_wh.add_argument("--phase", required=True)
    p_wh.add_argument("--prompt-to-user", required=True)
    p_wh.add_argument("--choice", action="append", default=[],
                      help="id=label entry (repeat for multiple choices). "
                           "Fragile when label contains spaces/quotes; prefer --choices-json.")
    p_wh.add_argument("--choices-json", default=None,
                      help="Path to JSON file containing a list of {id, label} "
                           "objects. Robust to special chars; canonical path used by "
                           "the orchestrator.")
    p_wh.add_argument("--advisory-warning", action="append", default=[],
                      help="Repeat for multiple warnings. Fragile when warning contains "
                           "special chars; prefer --advisory-warnings-json.")
    p_wh.add_argument("--advisory-warnings-json", default=None,
                      help="Path to JSON file containing a list of warning strings.")
    p_wh.add_argument("--candidates-path")
    p_wh.add_argument("--review-path")
    p_wh.add_argument("--resume-command")
    p_wh.add_argument("--needs-citation-count", type=int)
    p_wh.set_defaults(func=cmd_write_handoff)

    # update-state
    p_us = sub.add_parser("update-state", help="Atomic state.json mutation.")
    p_us.add_argument("draft_dir")
    p_us.add_argument("--phase")
    p_us.add_argument("--throughline-id")
    p_us.add_argument("--throughline-revision")
    p_us.add_argument("--add-cost", type=float)
    p_us.add_argument("--add-elapsed-seconds", type=int)
    p_us.set_defaults(func=cmd_update_state)

    # fill-template
    p_ft = sub.add_parser("fill-template", help="Substitute {key} placeholders.")
    p_ft.add_argument("template_path")
    p_ft.add_argument("output_path")
    p_ft.add_argument("--var", action="append", default=[],
                      help="key=value (repeat for multiple keys)")
    p_ft.set_defaults(func=cmd_fill_template)

    # init-reframing-log
    p_ir = sub.add_parser("init-reframing-log", help="Create reframing_log.md if absent.")
    p_ir.add_argument("draft_dir")
    p_ir.set_defaults(func=cmd_init_reframing_log)

    # validate-handoff
    p_vh = sub.add_parser("validate-handoff", help="Sanity-check .handoff.json.")
    p_vh.add_argument("draft_dir")
    p_vh.set_defaults(func=cmd_validate_handoff)

    # aggregate-metadata
    p_am = sub.add_parser("aggregate-metadata", help="Sum *.metadata.json sidecars.")
    p_am.add_argument("draft_dir")
    p_am.add_argument("--out", help="Output path (default: <draft_dir>/audit/run_metadata.json)")
    p_am.set_defaults(func=cmd_aggregate_metadata)

    # emit-next-actions
    p_na = sub.add_parser(
        "emit-next-actions",
        help="Aggregate validator failures + reviewer issues + citation orphans into next_actions.md.",
    )
    p_na.add_argument("draft_dir")
    p_na.set_defaults(func=cmd_emit_next_actions)

    # acquire-lock / release-lock — replaces bash flock (not on macOS).
    p_al = sub.add_parser(
        "acquire-lock",
        help="Best-effort exclusive lock via PID file (replaces bash flock).",
    )
    p_al.add_argument("draft_dir")
    p_al.add_argument("--verb", required=True, help="draft | resume")
    p_al.add_argument(
        "--pid", type=int, default=None,
        help="Holder PID (default: parent shell PID). Orchestrator passes $$.",
    )
    p_al.set_defaults(func=cmd_acquire_lock)

    p_rl = sub.add_parser(
        "release-lock",
        help="Remove the lock file. Idempotent.",
    )
    p_rl.add_argument("draft_dir")
    p_rl.set_defaults(func=cmd_release_lock)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
