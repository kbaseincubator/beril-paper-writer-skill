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

    # 3. Scope-coherence warnings (Discussion ↔ Results cross-walk)
    scope_path = draft_dir / "audit" / "scope_warnings.txt"
    lines.append("## Scope-coherence warnings")
    lines.append("")
    scope_warnings: list[str] = []
    if scope_path.is_file():
        for raw in scope_path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("[check_scope_coherence] WARN"):
                scope_warnings.append(
                    raw.replace("[check_scope_coherence] ", "", 1)
                )
    if scope_warnings:
        n = len(scope_warnings)
        lines.append(
            f"_{n} scope-coherence warning(s) from "
            "`tools/check_scope_coherence.py` (advisory; review each)._"
        )
        lines.append("")
        # Surface the first 10 verbatim; cap with a "..." pointer if more.
        for w in scope_warnings[:10]:
            lines.append(f"- {w}")
        if n > 10:
            lines.append(
                f"- ... ({n - 10} more — see `audit/scope_warnings.txt`)"
            )
        lines.append("")
    elif scope_path.is_file():
        lines.append("_No scope-coherence warnings._")
        lines.append("")
    else:
        lines.append(
            "_(audit/scope_warnings.txt not present — "
            "check_scope_coherence may not have run.)_"
        )
        lines.append("")

    # 4a. Repair-validators outcomes (REPAIR_MODE dispatch results)
    repair_path = draft_dir / "audit" / "repair_summary.txt"
    lines.append("## REPAIR_MODE outcomes")
    lines.append("")
    if repair_path.is_file():
        body = repair_path.read_text(encoding="utf-8").strip()
        if not body:
            lines.append("_(repair_summary.txt is empty — repair phase ran but emitted no outcomes.)_")
            lines.append("")
        else:
            # Categorize lines for prioritized display.
            body_lines = body.splitlines()
            escalations: list[str] = []
            repaired: list[str] = []
            other: list[str] = []
            for raw in body_lines:
                low = raw.lower()
                if " escalate " in low or "user-modify" in low or "exhausted" in low or "invocation-fail" in low:
                    escalations.append(raw)
                elif "repaired on attempt" in low:
                    repaired.append(raw)
                else:
                    other.append(raw)
            n_esc = len(escalations)
            n_rep = len(repaired)
            lines.append(
                f"_{n_rep} validator(s) auto-repaired, {n_esc} escalation(s) — "
                "review escalations before submission._"
            )
            lines.append("")
            for raw in escalations[:10]:
                lines.append(f"- **escalate:** {raw}")
            for raw in repaired[:8]:
                lines.append(f"- **repaired:** {raw}")
            for raw in other[:4]:
                lines.append(f"- {raw}")
            lines.append("")
    else:
        lines.append(
            "_(audit/repair_summary.txt not present — "
            "repair_validators may not have run.)_"
        )
        lines.append("")

    # 4b. Review-rewrite outcomes (per-pass dispatch + remaining criticals)
    rewrite_path = draft_dir / "audit" / "rewrite_summary.txt"
    lines.append("## Review-rewrite outcomes")
    lines.append("")
    if rewrite_path.is_file():
        body = rewrite_path.read_text(encoding="utf-8").strip()
        if not body:
            lines.append(
                "_(rewrite_summary.txt is empty — rewrite loop ran but produced no outcomes.)_"
            )
            lines.append("")
        else:
            body_lines = body.splitlines()
            # Highlight hard-cap residuals + invocation failures first.
            residuals = [ln for ln in body_lines if "hard cap" in ln.lower() or "invocation-fail" in ln.lower()]
            cycle_lines = [ln for ln in body_lines if ln not in residuals]
            if residuals:
                lines.append(
                    f"_{len(residuals)} unresolved item(s) — review before submission._"
                )
                lines.append("")
                for ln in residuals[:6]:
                    lines.append(f"- **unresolved:** {ln}")
                lines.append("")
            if cycle_lines:
                lines.append("_Rewrite-loop trace:_")
                lines.append("")
                for ln in cycle_lines[:12]:
                    lines.append(f"- {ln}")
                lines.append("")
    else:
        lines.append(
            "_(audit/rewrite_summary.txt not present — "
            "review_rewrite phase may not have run.)_"
        )
        lines.append("")

    # 4. Overclaim warnings (Abstract/Discussion ↔ throughline glyphs cross-walk)
    overclaim_path = draft_dir / "audit" / "overclaim_warnings.txt"
    lines.append("## Overclaim warnings")
    lines.append("")
    overclaim_warnings: list[str] = []
    if overclaim_path.is_file():
        for raw in overclaim_path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("[check_overclaim] WARN"):
                overclaim_warnings.append(
                    raw.replace("[check_overclaim] ", "", 1)
                )
    if overclaim_warnings:
        n = len(overclaim_warnings)
        # Split unacknowledged vs caveat-acknowledged for prioritization.
        unack = [w for w in overclaim_warnings if "[unacknowledged]" in w]
        ack = [w for w in overclaim_warnings if "[caveat-acknowledged]" in w]
        lines.append(
            f"_{n} overclaim warning(s) from "
            "`tools/check_overclaim.py` "
            f"({len(unack)} unacknowledged, {len(ack)} caveat-acknowledged; "
            "advisory; review each)._"
        )
        lines.append("")
        # Surface unacknowledged first (higher priority), then acknowledged.
        for w in unack[:8]:
            lines.append(f"- {w}")
        for w in ack[:4]:
            lines.append(f"- {w}")
        remainder = max(0, n - len(unack[:8]) - len(ack[:4]))
        if remainder:
            lines.append(
                f"- ... ({remainder} more — see `audit/overclaim_warnings.txt`)"
            )
        lines.append("")
    elif overclaim_path.is_file():
        lines.append("_No overclaim warnings._")
        lines.append("")
    else:
        lines.append(
            "_(audit/overclaim_warnings.txt not present — "
            "check_overclaim may not have run.)_"
        )
        lines.append("")

    # 5. Reviewer issues (critical only)
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
        f"{len(scope_warnings)} scope warning(s), "
        f"{len(overclaim_warnings)} overclaim warning(s), "
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
# REPAIR_MODE dispatch + filtering helpers (Item 3.1)
# ---------------------------------------------------------------------------


# Validator → section-prompt dispatch table. Single source of truth on
# the Python side; bash reads via `prepare-repair --emit-dispatch`.
# Mirrors LAYOUT.md line 419 (validator-dispatch table).
#
# Per-row tuple is (section_prompt_basename, target_filename, target_var_name,
# escalation_note). Validators not handled via REPAIR_MODE (M1, M4) carry
# section_prompt = None and an escalation_note explaining the orchestrator
# path.
_VALIDATOR_DISPATCH: dict[str, dict] = {
    "M1": {
        "section_prompt": None,
        "target_filename": None,
        "target_var_name": None,
        "escalation_note": (
            "M1 (Required IMRAD sections) is handled by the orchestrator's "
            "missing-section redraft path per LAYOUT.md:421, NOT REPAIR_MODE. "
            "v0.1 surfaces in next_actions.md as user-modify; "
            "implementation path: dispatch missing section's drafting prompt "
            "in IMRAD order with current draft state."
        ),
    },
    "M2": {
        "section_prompt": "abstract.v1.md",
        "target_filename": "05_abstract.md",
        "target_var_name": "ABSTRACT_PATH",
        "escalation_note": "",
    },
    "M3": {
        "section_prompt": "methods.v1.md",
        "target_filename": "01_methods.md",
        "target_var_name": "METHODS_PATH",
        "escalation_note": "",
    },
    "M4": {
        "section_prompt": None,
        "target_filename": None,
        "target_var_name": None,
        "escalation_note": (
            "M4 (Data availability) is BLOCKED in v0.1 per LAYOUT.md:424 — "
            "the data_availability_template.md has not been written. "
            "Surfaces in next_actions.md as user-modify until the template lands."
        ),
    },
    "M5": {
        "section_prompt": "methods.v1.md",
        "target_filename": "01_methods.md",
        "target_var_name": "METHODS_PATH",
        "escalation_note": "M5 is a soft-warning per LAYOUT.md:425; user-modify or accept-as-limitation are valid.",
    },
    "M6": {
        "section_prompt": "methods.v1.md",
        "target_filename": "01_methods.md",
        "target_var_name": "METHODS_PATH",
        "escalation_note": "M6 often escalates analysis-request per LAYOUT.md:426.",
    },
    "M7": {
        "section_prompt": "results.v1.md",
        "target_filename": "02_results.md",
        "target_var_name": "RESULTS_PATH",
        "escalation_note": "",
    },
    "M8": {
        "section_prompt": "results.v1.md",
        "target_filename": "02_results.md",
        "target_var_name": "RESULTS_PATH",
        "escalation_note": "",
    },
    "M9": {
        "section_prompt": "discussion.v1.md",
        "target_filename": "03_discussion.md",
        "target_var_name": "DISCUSSION_PATH",
        "escalation_note": "",
    },
    "M10": {
        "section_prompt": "discussion.v1.md",
        "target_filename": "03_discussion.md",
        "target_var_name": "DISCUSSION_PATH",
        "escalation_note": (
            "M10 default route per LAYOUT.md:430. Tie-breaker: if orphan "
            "appears only in Results, route to results.v1.md instead "
            "(orchestrator-side override may inspect violation.section)."
        ),
    },
}


def _dispatch_for_m10(violations: list[dict]) -> dict:
    """Apply LAYOUT.md:430 tie-breaker for M10. If all M10 violations live
    in a single section, dispatch there. Otherwise dispatch to discussion.v1
    (the default per LAYOUT)."""
    sections = {v.get("section", "") for v in violations}
    if sections == {"Results"} or sections == {"results"}:
        return {
            "section_prompt": "results.v1.md",
            "target_filename": "02_results.md",
            "target_var_name": "RESULTS_PATH",
            "escalation_note": "M10 routed to results.v1 (orphan only in Results section).",
        }
    if sections == {"Introduction"} or sections == {"introduction"}:
        # Per intro.v1 REPAIR_MODE handling: handles M10 defensively.
        return {
            "section_prompt": "intro.v1.md",
            "target_filename": "04_introduction.md",
            "target_var_name": "INTRODUCTION_PATH",
            "escalation_note": "M10 routed to intro.v1 (orphan only in Introduction).",
        }
    return _VALIDATOR_DISPATCH["M10"]


def cmd_prepare_repair(args: argparse.Namespace) -> int:
    """Filter validation.json to a single validator + write VALIDATOR_OUTPUT_PATH;
    print dispatch info to stdout for bash consumption.

    Output format (one key=value per line; bash sources via `eval`):
        SECTION_PROMPT=abstract.v1.md
        TARGET_FILENAME=05_abstract.md
        TARGET_VAR_NAME=ABSTRACT_PATH
        TARGET_PATH=/abs/path/to/05_abstract.md
        VALIDATOR_OUTPUT_PATH=/abs/path/to/audit/repair_M2_input.json
        VIOLATIONS_COUNT=1
        DISPATCH_STATUS=ready    # ready | escalate | skip
        ESCALATION_NOTE=...      # only when status != ready
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    validation_path = draft_dir / "audit" / "validation.json"
    if not validation_path.is_file():
        print("DISPATCH_STATUS=skip")
        print(f"ESCALATION_NOTE=validation.json not found at {validation_path}")
        return 0
    try:
        v = json.loads(validation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print("DISPATCH_STATUS=skip")
        print(f"ESCALATION_NOTE=validation.json parse error: {e}")
        return 0

    vid = args.validator
    entry = next((e for e in v.get("validators", []) if e.get("id") == vid), None)
    if entry is None:
        print("DISPATCH_STATUS=skip")
        print(f"ESCALATION_NOTE=validator {vid} not present in validation.json")
        return 0

    violations = entry.get("violations", [])
    if entry.get("status") != "fail" or not violations:
        print("DISPATCH_STATUS=skip")
        print(f"ESCALATION_NOTE=validator {vid} not in fail status (found: {entry.get('status')})")
        return 0

    # Resolve dispatch.
    if vid == "M10":
        dispatch = _dispatch_for_m10(violations)
    else:
        dispatch = _VALIDATOR_DISPATCH.get(vid, {
            "section_prompt": None,
            "target_filename": None,
            "target_var_name": None,
            "escalation_note": f"unknown validator id: {vid}",
        })

    if dispatch["section_prompt"] is None:
        print("DISPATCH_STATUS=escalate")
        print(f"ESCALATION_NOTE={dispatch['escalation_note']}")
        return 0

    target_path = draft_dir / dispatch["target_filename"]
    if not target_path.is_file():
        print("DISPATCH_STATUS=skip")
        print(
            f"ESCALATION_NOTE=target file {target_path} not present; "
            "M1 (missing section) escalation may be the underlying cause"
        )
        return 0

    # Filter to single-validator JSON for VALIDATOR_OUTPUT_PATH.
    filtered = {
        "draft_dir": v.get("draft_dir"),
        "mode": v.get("mode"),
        "validators": [entry],
    }
    output_path = draft_dir / "audit" / f"repair_{vid}_input.json"
    _atomic_write_json(output_path, filtered)

    print(f"SECTION_PROMPT={dispatch['section_prompt']}")
    print(f"TARGET_FILENAME={dispatch['target_filename']}")
    print(f"TARGET_VAR_NAME={dispatch['target_var_name']}")
    print(f"TARGET_PATH={target_path}")
    print(f"VALIDATOR_OUTPUT_PATH={output_path}")
    print(f"VIOLATIONS_COUNT={len(violations)}")
    print("DISPATCH_STATUS=ready")
    if dispatch["escalation_note"]:
        print(f"ESCALATION_NOTE={dispatch['escalation_note']}")
    return 0


def cmd_check_repair_status(args: argparse.Namespace) -> int:
    """Read a post-repair validation.json and report whether NAMED_VALIDATOR
    now passes. Stdout: STATUS=pass|fail|unknown.
    """
    val_path = Path(args.validation_json).expanduser().resolve()
    if not val_path.is_file():
        print("STATUS=unknown")
        print(f"NOTE=validation.json not found at {val_path}")
        return 0
    try:
        v = json.loads(val_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print("STATUS=unknown")
        print(f"NOTE=parse error: {e}")
        return 0
    entry = next(
        (e for e in v.get("validators", []) if e.get("id") == args.validator),
        None,
    )
    if entry is None:
        print("STATUS=unknown")
        print(f"NOTE=validator {args.validator} missing from validation.json")
        return 0
    print(f"STATUS={entry.get('status', 'unknown')}")
    print(f"VIOLATION_COUNT={len(entry.get('violations', []))}")
    return 0


def cmd_list_failed_validators(args: argparse.Namespace) -> int:
    """Print one validator id per line for each entry with status=='fail'."""
    val_path = Path(args.validation_json).expanduser().resolve()
    if not val_path.is_file():
        return 0
    try:
        v = json.loads(val_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    for e in v.get("validators", []):
        if e.get("status") == "fail":
            print(e.get("id", ""))
    return 0


# ---------------------------------------------------------------------------
# Review-rewrite parsing (Item 3.3)
# ---------------------------------------------------------------------------


# Section name → section-file basename mapping. Used to convert review's
# location strings (e.g., "Abstract", "Results", "Discussion") to the
# section-file paths that rewrite.v1.md expects as SECTION_PATH.
_SECTION_NAME_TO_FILE = {
    "abstract": "05_abstract.md",
    "methods": "01_methods.md",
    "results": "02_results.md",
    "discussion": "03_discussion.md",
    "introduction": "04_introduction.md",
    "intro": "04_introduction.md",
}


def _normalize_severity_header(text: str) -> Optional[str]:
    """Map a markdown header text to a severity bucket if it matches.

    `### Critical` / `### Important` / `### Suggested` (with optional
    trailing/leading whitespace). Returns the severity tier or None.
    """
    t = text.strip().lower()
    if t == "critical" or t.startswith("critical "):
        return "critical"
    if t == "important" or t.startswith("important "):
        return "important"
    if t == "suggested" or t.startswith("suggested "):
        return "suggested"
    return None


def _parse_review_findings(review_text: str) -> list[dict]:
    """Parse review.md for findings under ### Critical / Important /
    Suggested headers.

    Returns a list of dicts: {id, severity, primary_section, header_line}.
    `primary_section` is determined by scanning the finding header line
    AND up to the next 6 body lines for the first reference to a known
    section name (Abstract / Methods / Results / Discussion /
    Introduction). Header-first; body-fallback. Falls back to the empty
    string if no recognizable section appears.

    Tolerates three finding-header shapes observed across real
    fallback_reviewer.v1 output (prompt-shape drift; see
    `feedback_prompt_output_shape_drift.md`):

      Form A (review_1 first-pass — section name immediately follows `:`):
          **C1: Abstract line 18 — "..."**

      Form B (review_2 second-pass — bullet-prefixed, topic-name-leads):
          - **C1: Abstract functional hypotheses claim (line 20)** — "..."
          - **C2: GapMind "pathway gaps identified" overclaim (Abstract line 17, Introduction line 30)** — ...

      Form C (review_2 — quoted phrase leads, section in parens):
          - **C3: "Multi-dimensional evidence integration converged" (Abstract line 18, line 20; Discussion line 176)** — ...

    The 2026-04-27 v0.2 Tier-3 retest exposed that an earlier, narrower
    regex missed all C3-style headers (quote leading) and mis-mapped
    C2-style headers (topic word leading) — costing 2 of 3 criticals
    in `review_2.md` and silently terminating the rewrite loop.
    """
    findings: list[dict] = []
    current_severity: Optional[str] = None

    # Match any finding-header line: optional `- ` bullet, then `**ID:`
    # somewhere. The closing `**` is optional in capture because Form B/C
    # have trailing text past the `**`; we only need the ID + line index.
    finding_header_re = re.compile(
        r"^\s*(?:-\s+)?\*\*([CIS]\d+):\s+(.*)$"
    )
    severity_header_re = re.compile(r"^\s*###\s+(.+?)\s*$")

    # Known section names — match in priority order. We use word-boundary
    # case-insensitive matching against the header + body text. Keys must
    # align with `_SECTION_NAME_TO_FILE`.
    section_pattern = re.compile(
        r"\b(Abstract|Methods|Results|Discussion|Introduction|Intro)\b",
        re.IGNORECASE,
    )

    lines = review_text.splitlines()
    n_lines = len(lines)

    for i, line in enumerate(lines):
        sm = severity_header_re.match(line)
        if sm:
            current_severity = _normalize_severity_header(sm.group(1))
            continue
        if current_severity is None:
            continue
        fm = finding_header_re.match(line)
        if not fm:
            continue
        fid = fm.group(1)

        # Build search text: the header line + next ~6 body lines, until
        # the next finding header / section-severity header / blank
        # paragraph break. The primary section is the first section-name
        # match in this combined text (header preferred over body).
        header_text = fm.group(2)
        primary_section = ""
        head_match = section_pattern.search(header_text)
        if head_match:
            primary_section = head_match.group(1)
        else:
            for j in range(i + 1, min(i + 7, n_lines)):
                body_line = lines[j]
                # Stop scanning at the next finding header or severity
                # header so we don't grab the next finding's section.
                if finding_header_re.match(body_line):
                    break
                if severity_header_re.match(body_line):
                    break
                bm = section_pattern.search(body_line)
                if bm:
                    primary_section = bm.group(1)
                    break

        findings.append({
            "id": fid,
            "severity": current_severity,
            "primary_section": primary_section,
            "header_line": line.strip(),
        })
    return findings


def cmd_parse_review(args: argparse.Namespace) -> int:
    """Parse a review file; emit JSON to stdout describing findings
    grouped by section, filtered by min severity.

    Output JSON shape:
        {
          "review_path": "...",
          "min_severity": "critical|important|suggested",
          "findings_by_section": {
            "abstract": [{"id":"C1","severity":"critical"}, ...],
            "results": [...],
            ...
          },
          "section_files": {
            "abstract": "05_abstract.md",
            ...
          },
          "total_findings": N,
          "unmapped_sections": ["..."]   # finding sections not in our map
        }
    """
    review_path = Path(args.review_path).expanduser().resolve()
    if not review_path.is_file():
        print(json.dumps({
            "error": f"review file not found: {review_path}",
            "findings_by_section": {},
        }))
        return 0

    review_text = review_path.read_text(encoding="utf-8")
    findings = _parse_review_findings(review_text)

    # Apply severity filter.
    severity_order = {"critical": 0, "important": 1, "suggested": 2}
    min_sev = args.min_severity.lower()
    if min_sev not in severity_order:
        min_sev = "important"  # default
    threshold = severity_order[min_sev]
    findings = [f for f in findings if severity_order[f["severity"]] <= threshold]

    # Group by primary_section (lowercased), mapping to section file.
    findings_by_section: dict[str, list[dict]] = {}
    section_files: dict[str, str] = {}
    unmapped: list[str] = []
    for f in findings:
        sec_key = f["primary_section"].lower()
        sec_file = _SECTION_NAME_TO_FILE.get(sec_key)
        if sec_file is None:
            unmapped.append(f["primary_section"])
            continue
        findings_by_section.setdefault(sec_key, []).append({
            "id": f["id"],
            "severity": f["severity"],
            "header_line": f["header_line"],
        })
        section_files[sec_key] = sec_file

    print(json.dumps({
        "review_path": str(review_path),
        "min_severity": min_sev,
        "findings_by_section": findings_by_section,
        "section_files": section_files,
        "total_findings": sum(len(v) for v in findings_by_section.values()),
        "unmapped_sections": sorted(set(unmapped)),
    }, indent=2))
    return 0


def cmd_count_review_criticals(args: argparse.Namespace) -> int:
    """Print just the Critical-finding count for a review file.
    Used by the orchestrator to decide whether to enter the rewrite loop."""
    review_path = Path(args.review_path).expanduser().resolve()
    if not review_path.is_file():
        print(0)
        return 0
    findings = _parse_review_findings(review_path.read_text(encoding="utf-8"))
    print(sum(1 for f in findings if f["severity"] == "critical"))
    return 0


# ---------------------------------------------------------------------------
# Data availability extraction (Item 5.1)
# ---------------------------------------------------------------------------


# Curated list of public data-source names + their accession patterns.
# Matched against RESEARCH_PLAN.md / REPORT.md text. Order matters for
# display; entries earlier in the list surface first when present.
_KNOWN_DATA_SOURCES = [
    {
        "name": "Fitness Browser (RB-TnSeq)",
        "patterns": [r"Fitness\s+Browser", r"\bFB\b(?!\w)", r"RB-TnSeq", r"Price\s*(?:et\s*al\.?)?\s*\(?20(?:18|24)\)?"],
        "url": "https://fit.genomics.lbl.gov/",
        "citation": "Price et al. (2018) Nature 557:503-509",
    },
    {
        "name": "GTDB",
        "patterns": [r"GTDB\s*(?:r\d+)?", r"Genome\s+Taxonomy\s+Database"],
        "url": "https://gtdb.ecogenomic.org/",
        "citation": "Parks et al. (2022) Nucleic Acids Research",
    },
    {
        "name": "NMDC",
        "patterns": [r"\bNMDC\b", r"National\s+Microbiome\s+Data\s+Collaborative"],
        "url": "https://microbiomedata.org/",
        "citation": "Wood-Charlson et al. (2020) Nature Reviews Microbiology",
    },
    {
        "name": "NCBI BioSample",
        "patterns": [r"NCBI\s+BioSample", r"BioSample"],
        "url": "https://www.ncbi.nlm.nih.gov/biosample/",
        "citation": "Barrett et al. (2012) Nucleic Acids Research",
    },
    {
        "name": "AlphaEarth embeddings",
        "patterns": [r"AlphaEarth", r"alphaearth"],
        "url": "https://github.com/google-deepmind/alphaearth",
        "citation": "see project documentation",
    },
    {
        "name": "PaperBLAST",
        "patterns": [r"PaperBLAST"],
        "url": "https://papers.genomics.lbl.gov/",
        "citation": "Price & Arkin (2017) mSystems",
    },
    {
        "name": "GapMind",
        "patterns": [r"GapMind"],
        "url": "https://papers.genomics.lbl.gov/cgi-bin/gapView.cgi",
        "citation": "Price et al. (2020) PLoS Computational Biology; (2022) mSystems",
    },
    {
        "name": "eggNOG",
        "patterns": [r"eggNOG", r"EggNOG"],
        "url": "http://eggnog5.embl.de/",
        "citation": "Huerta-Cepas et al. (2019) Nucleic Acids Research",
    },
    {
        "name": "Bakta",
        "patterns": [r"Bakta"],
        "url": "https://github.com/oschwengers/bakta",
        "citation": "Schwengers et al. (2021) Microbial Genomics",
    },
    {
        "name": "STRING",
        "patterns": [r"STRING\s*(?:v?\d+)?", r"STRING\s+v\d+"],
        "url": "https://string-db.org/",
        "citation": "Szklarczyk et al. (2023) Nucleic Acids Research",
    },
    {
        "name": "PubMed",
        "patterns": [r"PubMed", r"\bPMID\b"],
        "url": "https://pubmed.ncbi.nlm.nih.gov/",
        "citation": "—",
    },
]

# Typed-accession patterns. Match against any text; collect literal IDs.
_ACCESSION_PATTERNS = [
    (re.compile(r"\bPRJ[A-Z]{2}\d+\b"), "BioProject"),
    (re.compile(r"\b(?:GSE|GSM|GPL)\d+\b"), "GEO"),
    (re.compile(r"\bSRP\d+\b"), "SRA Study"),
    (re.compile(r"\bSRR\d+\b"), "SRA Run"),
    (re.compile(r"\bSAMN\d+\b"), "BioSample"),
    (re.compile(r"\b[A-Z]{2}\d{6}(?:\.\d+)?\b"), "GenBank"),
    (re.compile(r"\bDOI:\s*10\.\d{4,9}/[\w./()-]+\b", re.IGNORECASE), "DOI"),
    (re.compile(r"\bPMID:?\s*\d+\b", re.IGNORECASE), "PMID"),
]


def _extract_kberdl_databases(methods_provenance_text: str) -> list[dict]:
    """Walk SQL blocks under ## Spark / K-BERDL Queries; return one dict per
    unique database (sorted) with the tables seen.

    Returns: [{"database": "kescience_fitnessbrowser",
               "tables": ["gene", "specificphenotype", ...]}, ...].
    """
    # Find the section. Match an H2 line that begins "Spark / K-BERDL".
    sec_re = re.compile(
        r"^##\s+Spark\s*/?\s*K-BERDL[^\n]*\n([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.IGNORECASE,
    )
    m = sec_re.search(methods_provenance_text)
    body = m.group(1) if m else methods_provenance_text  # fallback: scan whole file

    # FROM <db>.<table> and JOIN <db>.<table>. Database names use lowercase
    # underscores; tables similar. Allow optional schema-style backticks.
    qualified_re = re.compile(
        r"\b(?:FROM|JOIN)\s+`?([a-z][a-z0-9_]+)`?\s*\.\s*`?([a-z][a-z0-9_]+)`?",
        re.IGNORECASE,
    )

    db_to_tables: dict[str, set[str]] = {}
    for dbm in qualified_re.finditer(body):
        db = dbm.group(1).lower()
        table = dbm.group(2).lower()
        # Skip obvious aliases / non-database keywords.
        if db in {"select", "where", "and", "or", "as", "on"}:
            continue
        db_to_tables.setdefault(db, set()).add(table)

    return [
        {"database": db, "tables": sorted(tables)}
        for db, tables in sorted(db_to_tables.items())
    ]


def _extract_named_data_sources(text: str) -> list[dict]:
    """Scan combined RESEARCH_PLAN + REPORT text for known data-source
    names (curated `_KNOWN_DATA_SOURCES`). Return the matched entries
    in display order, deduplicated.
    """
    found: list[dict] = []
    for entry in _KNOWN_DATA_SOURCES:
        for pat in entry["patterns"]:
            if re.search(pat, text, re.IGNORECASE):
                found.append(entry)
                break
    return found


def _extract_typed_accessions(text: str) -> list[tuple[str, str]]:
    """Return (kind, accession) tuples for typed identifiers. Deduplicated
    in document order. Normalizes the accession literal: strips the
    type-prefix when present (e.g., `PMID: 29769716` → `29769716`,
    `DOI: 10.x/y` → `10.x/y`) so downstream display can use
    `<kind>: <acc>` without duplication.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pat, kind in _ACCESSION_PATTERNS:
        for m in pat.finditer(text):
            literal = m.group(0)
            # Strip prefix tokens that are themselves the kind label.
            if kind == "PMID":
                literal = re.sub(r"^PMID:?\s*", "", literal, flags=re.IGNORECASE)
            elif kind == "DOI":
                literal = re.sub(r"^DOI:?\s*", "", literal, flags=re.IGNORECASE)
            key = (kind, literal)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _format_kberdl_block(databases: list[dict]) -> str:
    if not databases:
        return (
            "No K-BERDL queries detected in `methods_provenance.md`. "
            "If this analysis used K-BERDL data, review provenance "
            "extraction and fill manually before submission."
        )
    lines = []
    for d in databases:
        tables_str = ", ".join(f"`{t}`" for t in d["tables"])
        lines.append(f"- **`{d['database']}`** — tables: {tables_str}")
    lines.append("")
    lines.append(
        "K-BERDL is the BERDL data-lakehouse query layer "
        "(https://berdatalakehouse.github.io/kberdl-docs/); access requires "
        "BERDL credentials. The full SQL queries are in "
        "`methods_provenance.md` §\"Spark / K-BERDL Queries\"."
    )
    return "\n".join(lines)


def _format_public_accessions_block(
    sources: list[dict], accessions: list[tuple[str, str]]
) -> str:
    if not sources and not accessions:
        return (
            "No public accessions or named external data sources detected. "
            "Review RESEARCH_PLAN.md and fill manually before submission "
            "if external data was used."
        )
    parts: list[str] = []
    if sources:
        parts.append("This analysis incorporated the following publicly available data sources:")
        parts.append("")
        for s in sources:
            parts.append(f"- **{s['name']}** — {s['url']}; {s['citation']}.")
        parts.append("")
    if accessions:
        parts.append("Specific accessions referenced in the manuscript:")
        parts.append("")
        for kind, acc in accessions:
            parts.append(f"- {kind}: `{acc}`")
        parts.append("")
    return "\n".join(parts).rstrip()


def cmd_cumulative_cost(args: argparse.Namespace) -> int:
    """Sum estimated_cost_usd across all *.metadata.json sidecars in
    <draft_dir>/audit/. Prints a single float (4 decimal places) to stdout.
    Used by the cost circuit breaker. Always returns 0; on error, prints
    `0.0000` so the orchestrator's awk comparison treats it as no spend.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    audit_dir = draft_dir / "audit"
    if not audit_dir.is_dir():
        print("0.0000")
        return 0
    total = 0.0
    for sc in sorted(audit_dir.glob("*.metadata.json")):
        try:
            d = json.loads(sc.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        try:
            total += float(d.get("estimated_cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    print(f"{total:.4f}")
    return 0


def cmd_extract_data_availability(args: argparse.Namespace) -> int:
    """Read methods_provenance.md + RESEARCH_PLAN.md + REPORT.md from the
    project; emit three template-fill blocks (kberdl_databases_block,
    public_accessions_block, restricted_access_block) as JSON to stdout.

    The orchestrator's bash phase consumes the JSON and passes the values
    into `fill-template`. Falls back to [TBD] markers if extraction
    surfaces nothing — never blocks the pipeline.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()

    methods_path = draft_dir / "methods_provenance.md"
    research_plan_path = project_root / "RESEARCH_PLAN.md"
    report_path = project_root / "REPORT.md"

    methods_text = methods_path.read_text(encoding="utf-8") if methods_path.is_file() else ""
    research_text = research_plan_path.read_text(encoding="utf-8") if research_plan_path.is_file() else ""
    report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""

    # K-BERDL databases — from methods_provenance.md SQL.
    databases = _extract_kberdl_databases(methods_text)
    kberdl_block = _format_kberdl_block(databases)

    # Public accessions — from RESEARCH_PLAN.md + REPORT.md combined.
    combined = research_text + "\n\n" + report_text
    sources = _extract_named_data_sources(combined)
    accessions = _extract_typed_accessions(combined)
    public_block = _format_public_accessions_block(sources, accessions)

    # Restricted access — defensive default.
    restricted_block = (
        "All data sources used in this analysis are publicly available "
        "via the resources cited above; no restricted-access data was "
        "used. Confirm before submission."
    )

    # Fall back to [TBD] markers ONLY if BOTH extractors return nothing
    # (defensive — covers the case where input files are missing).
    if not databases and not sources and not accessions:
        kberdl_block = (
            "[K-BERDL DATABASES: TBD — extraction surfaced no databases; "
            "review methods_provenance.md and fill manually.]"
        )
        public_block = (
            "[PUBLIC ACCESSIONS: TBD — extraction surfaced no accessions; "
            "review RESEARCH_PLAN.md and fill manually.]"
        )

    out = {
        "kberdl_databases_block": kberdl_block,
        "public_accessions_block": public_block,
        "restricted_access_block": restricted_block,
        "diagnostics": {
            "n_kberdl_databases": len(databases),
            "n_named_sources": len(sources),
            "n_typed_accessions": len(accessions),
            "kberdl_databases": [d["database"] for d in databases],
            "named_sources": [s["name"] for s in sources],
        },
    }
    print(json.dumps(out, indent=2))
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

    # prepare-repair (Item 3.1) — filter validation.json to single validator;
    # emit dispatch info on stdout for bash consumption.
    p_pr = sub.add_parser(
        "prepare-repair",
        help="Resolve REPAIR_MODE dispatch for a single failed validator. "
             "Stdout: shell-eval-able key=value lines.",
    )
    p_pr.add_argument("draft_dir")
    p_pr.add_argument("--validator", required=True, help="M1..M10")
    p_pr.set_defaults(func=cmd_prepare_repair)

    # check-repair-status — read post-repair validation.json; report
    # whether NAMED_VALIDATOR now passes.
    p_cs = sub.add_parser(
        "check-repair-status",
        help="Read a validation.json; print STATUS=pass|fail|unknown for the named validator.",
    )
    p_cs.add_argument("validation_json")
    p_cs.add_argument("--validator", required=True)
    p_cs.set_defaults(func=cmd_check_repair_status)

    # list-failed-validators — convenience for the orchestrator loop.
    p_lf = sub.add_parser(
        "list-failed-validators",
        help="Read a validation.json; print one validator id per line for each fail.",
    )
    p_lf.add_argument("validation_json")
    p_lf.set_defaults(func=cmd_list_failed_validators)

    # parse-review (Item 3.3) — extract findings from review.md, group
    # by primary section, filter by severity, emit JSON to stdout.
    p_pv = sub.add_parser(
        "parse-review",
        help="Parse review.md; emit JSON of findings grouped by primary section.",
    )
    p_pv.add_argument("review_path")
    p_pv.add_argument(
        "--min-severity", default="important",
        help="critical | important | suggested (default: important; pass-2 default: critical)",
    )
    p_pv.set_defaults(func=cmd_parse_review)

    # count-review-criticals — fast bash-side check whether the rewrite
    # loop should fire.
    p_cc = sub.add_parser(
        "count-review-criticals",
        help="Print the number of Critical findings in a review file.",
    )
    p_cc.add_argument("review_path")
    p_cc.set_defaults(func=cmd_count_review_criticals)

    # extract-data-availability (Item 5.1) — emit JSON of the three
    # template-fill blocks for 07_data_availability.md.
    p_ed = sub.add_parser(
        "extract-data-availability",
        help="Extract K-BERDL databases + public accessions from project artifacts; "
             "emit JSON of template-fill blocks for 07_data_availability.md.",
    )
    p_ed.add_argument("draft_dir")
    p_ed.add_argument("--project-root", required=True,
                      help="Path to <projects>/<project_id>/ (where RESEARCH_PLAN.md and REPORT.md live).")
    p_ed.set_defaults(func=cmd_extract_data_availability)

    # cumulative-cost (Item 5.2) — sum estimated_cost_usd across all
    # *.metadata.json sidecars in the draft's audit directory; print a
    # single float on stdout. Used by the orchestrator's cost circuit
    # breaker before each LLM invocation.
    p_cu = sub.add_parser(
        "cumulative-cost",
        help="Sum estimated_cost_usd across all *.metadata.json sidecars in audit/.",
    )
    p_cu.add_argument("draft_dir")
    p_cu.set_defaults(func=cmd_cumulative_cost)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
