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
# Subcommand: extract-tier
# ---------------------------------------------------------------------------

_VALID_TIERS = {"STRONG", "THIN", "EXPLORATORY"}

# Regex patterns for tier extraction, ordered by reliability.
# Pattern 1 (v0.6.4+): structured Triage header in throughline_candidates.md
#   **Tier:** EXPLORATORY
_TIER_STRUCTURED_RE = re.compile(
    r"^\*\*Tier:\*\*\s*(STRONG|THIN|EXPLORATORY)\b",
    re.MULTILINE | re.IGNORECASE,
)
# Pattern 2: closing-message format from plan.v1's closing template
#   tier: EXPLORATORY, recommended mode: report
_TIER_CLOSING_RE = re.compile(
    r"\btier:\s*(STRONG|THIN|EXPLORATORY)\b",
    re.IGNORECASE,
)


def _extract_tier_from_text(text: str) -> str | None:
    """Extract tier verdict from throughline_candidates.md text.

    Returns uppercase tier string or None if not found.
    """
    # Try structured header first (most reliable).
    m = _TIER_STRUCTURED_RE.search(text)
    if m:
        return m.group(1).upper()
    # Fall back to closing-message pattern.
    m = _TIER_CLOSING_RE.search(text)
    if m:
        return m.group(1).upper()
    return None


def cmd_extract_tier(args: argparse.Namespace) -> int:
    """Extract tier from throughline_candidates.md and write to state.json.

    Prints the extracted tier to stdout (for bash capture).
    Returns 0 on success, 1 on missing file, 2 on tier-not-found (warning).
    """
    candidates_path = Path(args.candidates_path).expanduser().resolve()
    if not candidates_path.is_file():
        print(f"error: candidates file not found: {candidates_path}",
              file=sys.stderr)
        return 1

    text = candidates_path.read_text(encoding="utf-8")
    tier = _extract_tier_from_text(text)

    if tier is None:
        print("warning: no tier verdict found in candidates file; "
              "defaulting to EXPLORATORY (conservative)",
              file=sys.stderr)
        tier = "EXPLORATORY"
        exit_code = 2
    else:
        exit_code = 0

    # Write to state.json if --draft-dir provided.
    if args.draft_dir:
        draft_dir = Path(args.draft_dir).expanduser().resolve()
        state_path = draft_dir / "state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
            state["tier"] = tier
            _atomic_write_json(state_path, state)
            print(f"wrote tier={tier} to {state_path}", file=sys.stderr)
        else:
            print(f"warning: state.json not found at {state_path}; "
                  f"tier={tier} not persisted", file=sys.stderr)

    # Always print tier to stdout for bash capture.
    print(tier)
    return exit_code


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

    # 3b. Figures-manifest warnings (Tier 2.1b — schema, file existence,
    # orphans, callout cross-walk)
    fig_path = draft_dir / "audit" / "figures_manifest_warnings.txt"
    lines.append("## Figures-manifest warnings")
    lines.append("")
    figures_warnings: list[str] = []
    figures_notes: list[str] = []
    if fig_path.is_file():
        for raw in fig_path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("[check_figures_manifest] WARN"):
                figures_warnings.append(
                    raw.replace("[check_figures_manifest] ", "", 1)
                )
            elif raw.startswith("[check_figures_manifest] NOTE"):
                figures_notes.append(
                    raw.replace("[check_figures_manifest] ", "", 1)
                )
    if figures_warnings or figures_notes:
        n_w = len(figures_warnings)
        n_n = len(figures_notes)
        lines.append(
            f"_{n_w} warning(s) + {n_n} note(s) from "
            "`tools/check_figures_manifest.py` (advisory; review each)._"
        )
        lines.append("")
        # Surface WARN first (higher priority).
        for w in figures_warnings[:10]:
            lines.append(f"- {w}")
        if n_w > 10:
            lines.append(
                f"- ... ({n_w - 10} more WARN — see "
                "`audit/figures_manifest_warnings.txt`)"
            )
        for w in figures_notes[:5]:
            lines.append(f"- {w}")
        if n_n > 5:
            lines.append(
                f"- ... ({n_n - 5} more NOTE — see "
                "`audit/figures_manifest_warnings.txt`)"
            )
        lines.append("")
    elif fig_path.is_file():
        lines.append("_No figures-manifest warnings._")
        lines.append("")
    else:
        lines.append(
            "_(audit/figures_manifest_warnings.txt not present — "
            "check_figures_manifest may not have run.)_"
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
        f"{len(figures_warnings)} figures-manifest warning(s), "
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
# Reframing-log parsing + repair dispatch (v0.6.4)
# ---------------------------------------------------------------------------

# Section name → (prompt, filename, var_name) for reframing repair dispatch.
_SECTION_REPAIR_DISPATCH: dict[str, dict] = {
    "methods": {
        "section_prompt": "methods.v1.md",
        "target_filename": "01_methods.md",
        "target_var_name": "METHODS_PATH",
    },
    "results": {
        "section_prompt": "results.v1.md",
        "target_filename": "02_results.md",
        "target_var_name": "RESULTS_PATH",
    },
    "discussion": {
        "section_prompt": "discussion.v1.md",
        "target_filename": "03_discussion.md",
        "target_var_name": "DISCUSSION_PATH",
    },
    "introduction": {
        "section_prompt": "intro.v1.md",
        "target_filename": "04_introduction.md",
        "target_var_name": "INTRODUCTION_PATH",
    },
    "abstract": {
        "section_prompt": "abstract.v1.md",
        "target_filename": "05_abstract.md",
        "target_var_name": "ABSTRACT_PATH",
    },
}


def _parse_reframing_log(text: str) -> list[dict]:
    r"""Parse reframing_log.md into structured entries.

    Each entry is delimited by ``## Entry N`` headers. Within an entry,
    the parser extracts bullet fields:
      - **Issue:** ...
      - **Source:** ...
      - **Manuscript impact:** ...
      - **Resolution:** ...
      - **Note:** ...

    Returns a list of dicts with keys: entry_number (int), type (str),
    issue, source, manuscript_impact, resolution, note, resolution_action
    ("escalated" | "accepted" | "unknown"), and target_sections (list of
    lowercase section names extracted from the Resolution text).
    """
    entries: list[dict] = []
    current: Optional[dict] = None
    current_field: Optional[str] = None

    # Regex for entry headers: ## Entry 1 — 2026-05-03T... — type: reframing
    entry_re = re.compile(
        r"^##\s+Entry\s+(\d+)\s*(?:—|-).*?type:\s*(.+?)\s*$", re.IGNORECASE
    )
    # Regex for bullet fields: - **Issue:** text
    field_re = re.compile(
        r"^-\s+\*\*(\w[\w\s]*?):\*\*\s*(.*)", re.DOTALL
    )

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # New entry header?
        m = entry_re.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "entry_number": int(m.group(1)),
                "type": m.group(2).strip(),
                "issue": "",
                "source": "",
                "manuscript_impact": "",
                "resolution": "",
                "note": "",
            }
            current_field = None
            continue

        if current is None:
            continue

        # HR separator — ignore
        if line.startswith("---"):
            continue

        # Field bullet?
        fm = field_re.match(line)
        if fm:
            field_name = fm.group(1).strip().lower().replace(" ", "_")
            field_val = fm.group(2).strip()
            if field_name in current:
                current[field_name] = field_val
                current_field = field_name
            else:
                current_field = None
            continue

        # Continuation line for the current field (multi-line values).
        if current_field and line.strip():
            current[current_field] += " " + line.strip()

    if current is not None:
        entries.append(current)

    # Post-process: extract resolution_action and target_sections.
    section_names = {"methods", "results", "discussion", "introduction",
                     "intro", "abstract"}

    for entry in entries:
        res = entry["resolution"].lower()
        if "escalated" in res:
            entry["resolution_action"] = "escalated"
        elif "accepted" in res:
            entry["resolution_action"] = "accepted"
        else:
            entry["resolution_action"] = "unknown"

        # Extract target section names from the Resolution text.
        # Match section names that appear as standalone words.
        found: list[str] = []
        res_words = re.findall(r"[A-Za-z]+", entry["resolution"])
        for w in res_words:
            wl = w.lower()
            if wl in section_names:
                # Normalize "intro" → "introduction"
                canonical = "introduction" if wl == "intro" else wl
                if canonical not in found:
                    found.append(canonical)
        entry["target_sections"] = found

    return entries


def cmd_parse_reframing_log(args: argparse.Namespace) -> int:
    """Parse reframing_log.md; emit JSON of entries with dispatch info.

    With --escalated-only, filters to entries where resolution_action ==
    "escalated". Each entry gains a ``dispatches`` list of per-section
    repair records: {section, section_prompt, target_filename,
    target_var_name, target_path, status}.
    """
    log_path = Path(args.reframing_log).expanduser().resolve()
    if not log_path.is_file():
        print(json.dumps({"entries": [], "note": "reframing_log.md not found"}))
        return 0

    text = log_path.read_text(encoding="utf-8")
    entries = _parse_reframing_log(text)

    if args.escalated_only:
        entries = [e for e in entries if e["resolution_action"] == "escalated"]

    draft_dir = Path(args.draft_dir).expanduser().resolve() if args.draft_dir else None

    for entry in entries:
        dispatches = []
        for section in entry["target_sections"]:
            d = _SECTION_REPAIR_DISPATCH.get(section)
            if d is None:
                dispatches.append({
                    "section": section,
                    "status": "unknown_section",
                })
                continue
            rec = dict(d)
            rec["section"] = section
            if draft_dir:
                target = draft_dir / rec["target_filename"]
                rec["target_path"] = str(target)
                rec["status"] = "ready" if target.is_file() else "target_missing"
            else:
                rec["status"] = "ready"
            dispatches.append(rec)
        entry["dispatches"] = dispatches

    print(json.dumps({"entries": entries}, indent=2))
    return 0


def cmd_list_reframing_repairs(args: argparse.Namespace) -> int:
    """Print one repair dispatch line per escalated section target.

    Output format (for bash consumption):
        ENTRY=1|SECTION=results|PROMPT=results.v1.md|FILE=02_results.md|VAR=RESULTS_PATH
        ENTRY=1|SECTION=discussion|PROMPT=discussion.v1.md|FILE=03_discussion.md|VAR=DISCUSSION_PATH
        ...

    Bash loops over these lines; each triggers a REPAIR_MODE invocation.
    """
    log_path = Path(args.reframing_log).expanduser().resolve()
    if not log_path.is_file():
        return 0

    text = log_path.read_text(encoding="utf-8")
    entries = _parse_reframing_log(text)
    draft_dir = Path(args.draft_dir).expanduser().resolve()

    for entry in entries:
        if entry["resolution_action"] != "escalated":
            continue
        for section in entry["target_sections"]:
            d = _SECTION_REPAIR_DISPATCH.get(section)
            if d is None:
                continue
            target = draft_dir / d["target_filename"]
            if not target.is_file():
                continue
            print(
                f"ENTRY={entry['entry_number']}"
                f"|SECTION={section}"
                f"|PROMPT={d['section_prompt']}"
                f"|FILE={d['target_filename']}"
                f"|VAR={d['target_var_name']}"
            )

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


def _parse_figures_manifest(manifest_path: Path) -> list[dict]:
    """Parse figures_manifest.tsv into a list of row dicts.

    Schema (per Wrinkle A canonicalization, v0_3_punch_list.md):
        paper_order_n\tfilename\tinventory_lookup_name

    Returns [] if the manifest is missing. WARN to stderr on any
    schema deviation; rows that fail validation are skipped.
    """
    if not manifest_path.is_file():
        return []
    raw = manifest_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines:
        return []
    expected = ["paper_order_n", "filename", "inventory_lookup_name"]
    header = lines[0].split("\t")
    if header != expected:
        sys.stderr.write(
            f"WARN: figures_manifest.tsv header mismatch: got {header!r}, expected {expected!r}\n"
        )
    rows: list[dict] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 3:
            sys.stderr.write(
                f"WARN: figures_manifest.tsv line {lineno}: expected 3 cells, got {len(cells)}; skipping\n"
            )
            continue
        try:
            n = int(cells[0].strip())
        except ValueError:
            sys.stderr.write(
                f"WARN: figures_manifest.tsv line {lineno}: paper_order_n not an integer: {cells[0]!r}; skipping\n"
            )
            continue
        # v0.4 retest finding (2026-04-28): results.v1 occasionally
        # emits inventory_lookup_name and filename with a `figures/`
        # directory prefix instead of the basename-only form the schema
        # specifies. All downstream lookups (descriptors, captions,
        # embed) are keyed by basename, so the prefix breaks every
        # lookup silently — degrading to filename-derived captions and
        # empty descriptors. Fix: defensive Path().name normalization
        # at parse time. Idempotent on already-clean basenames.
        rows.append({
            "paper_order_n": n,
            "filename": Path(cells[1].strip()).name,
            "inventory_lookup_name": Path(cells[2].strip()).name,
        })
    return rows


def _parse_figures_inventory_captions(inventory_path: Path) -> dict[str, str]:
    """Parse figures_inventory.md into {inventory_filename: top_caption}.

    For each `### \\`figures/<name>\\`` heading, extract the first bullet's
    caption text from the "**Caption candidates:**" block. The inventory
    bullets are pre-sorted by extract_figures.py in priority order
    (REPORT-derived first, notebook-context second, filename third), so
    the first bullet is the highest-priority caption for that figure.

    If a figure entry has no caption candidates, falls back to a
    filename-derived caption (strip `figNN_` prefix, replace `_` with
    space, capitalize first word).

    Returns {} if the inventory file is missing.
    """
    if not inventory_path.is_file():
        return {}
    text = inventory_path.read_text(encoding="utf-8")
    captions: dict[str, str] = {}
    # Split on each ### `figures/<name>` heading. The split keeps the
    # captured filename in the result list, alternating with the body.
    parts = re.split(r"^### `figures/(.+?)`\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, fname1, body1, fname2, body2, ...]
    for i in range(1, len(parts), 2):
        fname = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        # First bullet under "Caption candidates:": `- **<source>**: <text>`
        cap_match = re.search(
            r"\*\*Caption candidates:\*\*\s*\n+\s*-\s*\*\*[^*]+\*\*:\s*(.+?)$",
            body,
            flags=re.MULTILINE,
        )
        if cap_match:
            captions[fname] = cap_match.group(1).strip()
        else:
            captions[fname] = _filename_to_caption(fname)
    return captions


def _filename_to_caption(filename: str) -> str:
    """Filename → human-readable caption fallback.

    Strip `figNN_` paper-order prefix, replace `_` with space, capitalize
    first word. e.g. `fig01_dark_gene_census.png` → "Dark gene census".
    """
    stem = Path(filename).stem
    cleaned = re.sub(r"^fig\d+_", "", stem)
    cleaned = cleaned.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else filename


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — inventory v2 Description block parser
# ---------------------------------------------------------------------------

# Bullet-line patterns for the structured fields inside a Description block.
# extract_figures.py emits these as italic-labeled bullets:
#     - _Title:_ <text>
#     - _Axes:_ <label>; <label>
#     - _Legend:_ <label>; <label>
#     - _Panels:_ (A) Title; (B) Title; ...
# Single-line, semicolon-separated where lists are involved.
_DESCRIPTOR_BULLET_RE = re.compile(
    r"^\s*-\s+_(?P<label>[A-Za-z][A-Za-z ]*?):_\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
# Panel block parser: `(A) Title; (B) Title; ...`. Empty title (just `(A)`)
# is allowed and yields a panel with title=None.
_PANEL_ENTRY_RE = re.compile(r"\(([A-Z])\)\s*([^;]*?)(?=;|$)")


def _parse_figures_inventory_descriptions(
    inventory_path: Path,
) -> dict[str, dict]:
    """Parse v2 figures_inventory.md into {inventory_filename: descriptor}.

    Descriptor schema (matches the structure of CaptionDescriptor.to_dict()
    minus serialization noise):

        {
          "title": str | None,
          "axes_labels": list[str],
          "legend_labels": list[str],
          "panels": list[{"letter": str, "title": str | None}],
          "notebook_prose": str | None,    # blockquote contents joined
          "source_refs": list[str],
        }

    Returns {} if the inventory is missing OR is v1 (no schema header
    comment). Caller should fall back gracefully when the descriptor
    is empty.

    Schema-version detection: looks for `<!-- inventory_schema_version: 2 -->`
    on the first non-blank line. v1 inventories have no header comment;
    they get an empty {} return so the caller defaults to the existing
    behavior (caption-only).
    """
    if not inventory_path.is_file():
        return {}
    text = inventory_path.read_text(encoding="utf-8")
    # Schema-version sniff: only attempt to parse v2. v1 returns {}.
    first_line = next(
        (line for line in text.split("\n") if line.strip()), "",
    )
    if "inventory_schema_version: 2" not in first_line:
        return {}

    descriptors: dict[str, dict] = {}
    parts = re.split(r"^### `figures/(.+?)`\s*$", text, flags=re.MULTILINE)
    # parts = [preamble, fname1, body1, fname2, body2, ...]
    for i in range(1, len(parts), 2):
        fname = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        descriptors[fname] = _parse_one_description_block(body)
    return descriptors


def _parse_one_description_block(body: str) -> dict:
    """Parse a single figure's body section into a descriptor dict.

    A Description block looks like:

        **Description:**

        - _Title:_ Foo
        - _Axes:_ X; Y
        - _Panels:_ (A) Left; (B) Right

        _Notebook prose:_

        > ## Fig 1
        > Multi-line content...

        _Source refs:_ matplotlib_ast(nb.ipynb), notebook_md_walkback(nb.ipynb)

    Returns a descriptor dict (see _parse_figures_inventory_descriptions
    docstring). Missing fields → None / [].
    """
    descriptor: dict = {
        "title": None,
        "axes_labels": [],
        "legend_labels": [],
        "panels": [],
        "notebook_prose": None,
        "source_refs": [],
    }
    if "**Description:**" not in body:
        return descriptor

    # Slice out only the Description section: from `**Description:**` to
    # the next `**` block header (e.g. `**Generated by:**`) or end-of-body.
    desc_start = body.index("**Description:**")
    after = body[desc_start + len("**Description:**"):]
    next_section = re.search(r"^\*\*[A-Z][^*]+:\*\*", after, flags=re.MULTILINE)
    desc_block = after[:next_section.start()] if next_section else after

    # Bullet fields (Title / Axes / Legend / Panels).
    for m in _DESCRIPTOR_BULLET_RE.finditer(desc_block):
        label = m.group("label").strip().lower()
        value = m.group("value").strip()
        if label == "title":
            descriptor["title"] = value
        elif label == "axes":
            descriptor["axes_labels"] = [
                v.strip() for v in value.split(";") if v.strip()
            ]
        elif label == "legend":
            descriptor["legend_labels"] = [
                v.strip() for v in value.split(";") if v.strip()
            ]
        elif label == "panels":
            panels: list[dict] = []
            for pm in _PANEL_ENTRY_RE.finditer(value):
                letter = pm.group(1)
                title = pm.group(2).strip() or None
                panels.append({"letter": letter, "title": title})
            descriptor["panels"] = panels

    # Notebook prose: blockquote following `_Notebook prose:_`.
    prose_match = re.search(
        r"_Notebook prose:_\s*\n((?:^>.*\n?)+)",
        desc_block,
        flags=re.MULTILINE,
    )
    if prose_match:
        bq = prose_match.group(1)
        # Strip leading '> ' from each line, preserve blank lines.
        prose_lines = []
        for line in bq.split("\n"):
            if line.startswith("> "):
                prose_lines.append(line[2:])
            elif line == ">":
                prose_lines.append("")
            elif line.strip() == "":
                continue
        prose = "\n".join(prose_lines).strip()
        descriptor["notebook_prose"] = prose if prose else None

    # Source refs.
    refs_match = re.search(
        r"_Source refs:_\s*(.+?)(?:\n|$)", desc_block,
    )
    if refs_match:
        refs = [r.strip() for r in refs_match.group(1).split(",")]
        descriptor["source_refs"] = [r for r in refs if r]

    return descriptor


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — prose-side panel-callout detection (Stratum 2)
# ---------------------------------------------------------------------------

# Match `(Fig. N[A-Z])` panel callouts. Pattern: a digit immediately
# followed by an uppercase letter inside a Fig. callout. Also matches
# `(Fig. N, panel A)` and `Fig. NA` outside parens.
_PANEL_CALLOUT_RE = re.compile(
    r"\bFig\.\s*(?P<n>\d+)(?:[A-Z]|\s*[,;]\s*panel\s+[A-Z]|\s*panel\s+[A-Z])"
)
# Fine-grained: extract the panel letter from a matched group.
_PANEL_LETTER_RE = re.compile(
    r"Fig\.\s*\d+([A-Z])|panel\s+([A-Z])", re.IGNORECASE,
)


def _detect_prose_panel_callouts(text: str, n: int) -> dict[str, str]:
    """Scan `text` for `(Fig. N[A-Z])` callouts referring to figure N.

    Returns {letter: ±1-sentence-context} where the context is roughly
    one sentence preceding/containing the callout. When the same letter
    appears multiple times, the FIRST occurrence's context is kept
    (consistent with first-occurrence-wins semantics elsewhere).

    Letters are uppercase. Ignores callouts to other figure numbers.
    """
    result: dict[str, str] = {}
    fig_n_pattern = re.compile(
        rf"\bFig\.\s*{n}(?:[A-Z]|\s*[,;]\s*panel\s+[A-Z]|\s*panel\s+[A-Z])"
    )
    for m in fig_n_pattern.finditer(text):
        # Extract the letter from this match.
        match_text = m.group(0)
        lm = _PANEL_LETTER_RE.search(match_text)
        if not lm:
            continue
        letter = (lm.group(1) or lm.group(2)).upper()
        if letter in result:
            continue
        # Extract ±1 sentence context: walk backward to prior sentence
        # boundary, then forward to next sentence boundary.
        ctx_start = _walk_back_to_sentence_start(text, m.start())
        ctx_end = _walk_forward_to_sentence_end(text, m.end())
        context = text[ctx_start:ctx_end].strip()
        # Collapse internal whitespace for cleaner inline display.
        context = re.sub(r"\s+", " ", context)
        result[letter] = context
    return result


def _walk_back_to_sentence_start(text: str, pos: int) -> int:
    """Return the start of the sentence containing position `pos`."""
    i = pos
    while i > 0:
        ch = text[i - 1]
        if ch in ".!?\n":
            # Step over the sentence-end punctuation.
            return i
        i -= 1
    return 0


def _walk_forward_to_sentence_end(text: str, pos: int) -> int:
    """Return the position immediately after the next sentence-ending
    `.!?` at or after `pos`. Reuses _find_sentence_end_after's heuristic."""
    return _find_sentence_end_after(text, pos)


# ---------------------------------------------------------------------------
# v0.4 Phase 3 — description-text assembly
# ---------------------------------------------------------------------------

def _assemble_description_text(
    descriptor: dict,
    prose_panel_callouts: Optional[dict[str, str]] = None,
    max_chars: int = 1500,
) -> str:
    """Compose a single-line description string from a structured descriptor
    plus optional prose-side panel callouts.

    Output form (single-panel):
        "<title>. X-axis: <xlabel>; y-axis: <ylabel>. <notebook-prose-snippet>."

    Output form (multi-panel):
        "<title>. (A) <panel-A>. (B) <panel-B>. ... <notebook-prose-snippet>."

    Empty-field elision: skips empty or None segments. No 'Figure N: . . .'
    artifacts. All newlines in source content are flattened to single
    spaces (single-line output).

    Capped at `max_chars` (default 1500) — the markdown italic paragraph
    needs to be readable, not a full ICMJE caption. Source 4 (LLM) generates
    ICMJE-length captions separately when the gate triggers.
    """
    if prose_panel_callouts is None:
        prose_panel_callouts = {}
    sentences: list[str] = []

    title = (descriptor.get("title") or "").strip()
    if title:
        sentences.append(_ensure_period(title))

    panels = descriptor.get("panels") or []
    # Merge prose-detected panel letters as additional panels (no AST title
    # but might have prose context).
    seen_letters = {p["letter"] for p in panels if p.get("letter")}
    for letter, ctx in (prose_panel_callouts or {}).items():
        if letter not in seen_letters:
            panels = panels + [{"letter": letter, "title": None,
                                "_prose_only": True, "_prose_ctx": ctx}]
    # Sort panels by letter for stable rendering.
    panels = sorted(panels, key=lambda p: p.get("letter", ""))

    if panels:
        panel_parts: list[str] = []
        for p in panels:
            letter = p.get("letter", "")
            title_p = (p.get("title") or "").strip()
            ctx_p = (p.get("_prose_ctx") or "").strip()
            label = f"({letter})"
            text_p = title_p or ctx_p
            # Strip notebook-author panel-letter prefixes that duplicate
            # our own `(A)` rendering. Common pattern from
            # `axes[0].set_title('A. Gene annotation classes')` — without
            # this, output reads `(A) A. Gene annotation classes`.
            if text_p and letter:
                text_p = re.sub(
                    rf"^{re.escape(letter)}[.)\s]\s*",
                    "",
                    text_p,
                ).strip() or text_p
            if text_p:
                panel_parts.append(f"{label} {text_p}")
            else:
                panel_parts.append(label)
        sentences.append(_ensure_period(" ".join(panel_parts)))
    else:
        # No panels — emit axes labels as a sentence.
        axes = descriptor.get("axes_labels") or []
        if axes:
            sentences.append(_ensure_period("Axes: " + "; ".join(axes)))

    # Notebook prose: snippet (first ~300 chars, cleaned)
    prose = (descriptor.get("notebook_prose") or "").strip()
    if prose:
        prose_clean = _strip_prose_for_inline(prose)
        if prose_clean:
            sentences.append(prose_clean)

    # Join, flatten whitespace, cap length.
    out = " ".join(s for s in sentences if s)
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > max_chars:
        out = out[:max_chars - 3].rstrip() + "..."
    return out


def _ensure_period(s: str) -> str:
    """Append a period if `s` doesn't already end in `.!?`."""
    s = s.rstrip()
    if not s:
        return s
    if s[-1] in ".!?":
        return s
    return s + "."


_BOILERPLATE_KEYWORD_ALT = (
    r"Purpose|Approach|Strategy|Sections?|Steps?|Method|Methods|"
    r"Inputs?|Outputs?|Notes?|Test|Tests|Goal|Objective|Pipeline|"
    r"Workflow|Implementation|Setup|Background|Rationale|Dependencies|"
    r"Problem|Overview|Context|Motivation|Summary|Description|Analysis"
)
_NOTEBOOK_BOILERPLATE_KEYWORDS_RE = re.compile(
    # Trailing `\s*` (not `\s+`) so a standalone keyword line like
    # `Sections:` (with the content on the NEXT line) also matches.
    # Caught in v0.4 Phase 5b re-render review on figure 8.
    rf"^(?:{_BOILERPLATE_KEYWORD_ALT}):\s*$|"
    # Inline keyword: same set, requires content after the colon.
    rf"^(?:{_BOILERPLATE_KEYWORD_ALT}):\s+\S",
)
# v0.4 Phase 5b refinement #3: inline keyword-cascade stripping. The
# line-prefix filter catches keyword-on-its-own-line cases; this regex
# handles the inline cascade ("Sentence A. Purpose: stuff. Approach:
# stuff. Sentence B.") that escapes the line-prefix filter when prose
# is flattened to a single paragraph. Caught in v0.4 Phase 5b second
# re-render: figure 8 had "Description... Purpose: Address... Approach:
# Single supplementary..." inline; my regex required keyword at line
# start. This regex strips "Keyword: stuff." substrings anywhere in
# the prose, taking content up to the next sentence terminator.
_INLINE_BOILERPLATE_CASCADE_RE = re.compile(
    rf"(?<=[.!?])\s+(?:{_BOILERPLATE_KEYWORD_ALT}):\s+[^.!?]*[.!?]+",
    flags=0,
)
# Project-internal artifact references (file names, notebook ids).
# Matched with word boundaries so "REPORT" inside a sentence isn't
# stripped — only the suffix `.md` form. NB\d+ + nb\d+ catch both
# casings; lowercase variant requires a digit immediately after to
# avoid stripping random "nb" character pairs. Optional possessive
# `'s` consumed too — without this, `NB07's gene` becomes `'s gene`,
# an orphaned possessive caught in v0.4 Phase 5b re-render review.
_PROJECT_INTERNAL_ARTIFACTS_RE = re.compile(
    r"\b(?:REVIEW\.md|REPORT\.md|RESEARCH_PLAN\.md|"
    r"NB\d+[a-z]?|nb\d+[a-z]?)(?:[’']s)?\b",
)


def _strip_prose_for_inline(prose: str) -> str:
    """Reduce a multi-line markdown blockquote-prose (potentially
    containing headings, bullets, tables, notebook-organization
    keywords, project-internal references) to a single-line snippet
    suitable for inline italic rendering.

    - Drop lines beginning with `#` (section headings — already captured
      via `descriptor.title` if present).
    - Drop lines beginning with `|` (markdown table rows).
    - Drop lines beginning with `-` or `*` IF they look like list bullets
      (common pattern: bullet lists in notebook prose that don't render
      meaningfully when flattened to one line).
    - **v0.4 Phase 5b:** drop lines beginning with notebook-organization
      keyword headers (`Purpose:`, `Approach:`, `Sections:`, `Steps:`,
      `Method:`, etc.) — these are project-internal documentation, not
      figure content. Discovered when fig08/9/10 descriptions hallucinated
      "Purpose: Address 2 critical and 4 important suggestions from
      automated review (REVIEW.md). Approach: Single supplementary
      notebook using pandas/scipy only..." consuming the LLM word budget
      and crowding out actual panel descriptions.
    - **v0.4 Phase 5b:** strip inline references to project-internal
      artifacts (`REVIEW.md`, `REPORT.md`, `RESEARCH_PLAN.md`,
      `NB\\d+`, `nb\\d+`). The figure caption is for the manuscript
      reader, not the notebook author.
    - Collapse remaining content to single line; strip leading
      `**Bold:**` style labels.
    - Cap at 300 chars (the prose snippet is supplementary, not the
      whole description).
    """
    # Pre-process: strip markdown blockquote prefixes (`> `) from each
    # line BEFORE any other processing. The figures_inventory stores
    # notebook prose in blockquote format; without this, `> ## Problem`
    # escapes the `^#` heading filter, `> Strategy:` escapes the keyword
    # filter, and critique prose from Problem/Strategy sections leaks
    # into figure captions. Caught in v0.6.4: draft_9 fig 8 had raw
    # NB08 critique text ("gene neighborhood analysis uses a minimal
    # positional heuristic...") ending mid-sentence in the caption.
    prose = re.sub(r"^>\s?", "", prose, flags=re.MULTILINE)

    # Pre-process: drop entire notebook-organization sections.
    # After blockquote stripping, notebook markdown has `## Problem`,
    # `## Strategy`, `## Inputs`, `## Outputs`, etc. These are
    # project-internal sections whose BODY content is not figure
    # description. The line-by-line keyword filter drops keyword-header
    # lines but their body paragraphs (regular prose) leak through.
    # Fix: detect `## <BoilerplateKeyword>` section headers and drop
    # everything until the next `##` header, `---` separator, or EOF.
    #
    # IMPORTANT: the header must be EXACTLY the keyword (+ optional
    # colon/number suffix like `## Section 1` or `## Inputs:`), NOT
    # keyword-prefixed content descriptions like `## Section 1:
    # Conserved Gene Neighborhoods`. The latter are real content
    # sections that should be kept. The regex requires the keyword
    # to be followed by only optional `: <digits>` or whitespace to
    # end-of-line.
    #
    # Caught in v0.6.4: NB08's `## Problem` body ("gene neighborhood
    # analysis uses a minimal positional heuristic...") leaked into
    # fig 8's caption.
    _section_strip_keywords = (
        "Problem|Strategy|Overview|Context|Motivation|Purpose|Approach|"
        "Method|Methods|Inputs|Outputs|Setup|Background|Dependencies|"
        "Implementation|Analysis|Rationale|Pipeline|Workflow|"
        "Goal|Objective|Notes"
    )
    prose = re.sub(
        rf"^##\s+(?:{_section_strip_keywords})(?:\s*:?\s*(?:\d+\s*)?)?\s*$"
        rf".*?(?=^##|\Z)",
        "",
        prose,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )

    # Pre-process: strip `**Bold:**` markers BEFORE the line-prefix
    # boilerplate filter, so `**Goal:** Test ...` becomes `Goal: Test ...`
    # which the line filter then drops. Without this pre-pass, bold-
    # formatted notebook keywords slip through.
    # Markdown allows TWO idiomatic forms with the same visual rendering:
    #   `**Goal:**` (colon inside the bold delimiters)
    #   `**Goal**:` (colon outside; more common in observed prose)
    # Both must be normalized. Caught in v0.4 Phase 5b third re-render:
    # figure 7's actual notebook_prose used the colon-outside form,
    # which my single-form regex missed.
    prose = re.sub(r"\*\*([^*]+?):\*\*", r"\1:", prose)   # **X:**
    prose = re.sub(r"\*\*([^*]+?)\*\*\s*:", r"\1:", prose)  # **X**:
    # Pre-process: strip project-internal artifacts globally BEFORE
    # the inline-cascade. Otherwise `REVIEW.md` inside keyword content
    # provides a spurious `.` that the cascade's sentence-boundary
    # anchor matches against, leaving a `.md)` residue. Caught in
    # v0.4 Phase 5b second re-render: fig 8 emitted "Domain matching
    # analysis.md). All inputs are saved..." after cascade.
    prose = _PROJECT_INTERNAL_ARTIFACTS_RE.sub("", prose)
    prose = re.sub(r"  +", " ", prose)
    # Inline-cascade strip: catches "Sentence A. Keyword: content.
    # Keyword2: content. Sentence B." patterns that flatten across
    # paragraphs and escape the per-line filter. Applied iteratively
    # until no more matches (each pass strips one keyword chunk; cascades
    # need multiple passes since the regex anchors on the preceding
    # `.!?` and only consumes one chunk per match).
    while True:
        new_prose = _INLINE_BOILERPLATE_CASCADE_RE.sub("", prose)
        if new_prose == prose:
            break
        prose = new_prose

    lines = []
    for line in prose.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") or s.startswith("|"):
            continue
        # Drop bullet-list items (`- foo`, `* foo`) AND numbered-list
        # items (`1. foo`, `2) foo`). Numbered lists are common after
        # `Sections:` / `Steps:` keyword headers; without this they leak
        # through as bare items even when the parent header is dropped.
        if re.match(r"^(?:[-*]|\d+[.)])\s", s):
            continue
        if _NOTEBOOK_BOILERPLATE_KEYWORDS_RE.match(s):
            continue
        # Strip inline project-internal artifact references.
        s = _PROJECT_INTERNAL_ARTIFACTS_RE.sub("", s)
        # Collapse double-spaces left by the strip.
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        lines.append(s)
    flat = " ".join(lines)
    flat = re.sub(r"\s+", " ", flat).strip()
    if len(flat) > 300:
        flat = flat[:297].rstrip() + "..."
    return flat


def cmd_resolve_figures(args: argparse.Namespace) -> int:
    """Join figures_manifest.tsv with figures_inventory.md; emit TSV.

    Output to stdout: header row + one row per selected figure:

        paper_order_n\tfilename\tcaption\tdescription

    v0.4 Phase 3 added the `description` column (4th) — pre-prose-merge
    description string assembled from the inventory's v2 Description
    block (title / axes / panels / notebook prose). Empty when the
    inventory is v1 or has no Description block for this figure. The
    actual embed-time description in the manuscript markdown is
    re-assembled inside `cmd_embed_figures` with section-prose panel
    callouts merged in (Stratum 2 enrichment); this CLI output is the
    static descriptor-only version, useful for downstream tooling and
    debugging.

    Captions sourced from inventory's caption-candidate ranking
    (REPORT-derived first, notebook second, filename third). Fallback to
    filename-derived caption when the inventory has no entry for the
    manifest's `inventory_lookup_name`.

    Banned-tab and banned-newline discipline applied to BOTH caption
    and description: tabs/newlines become spaces (with stderr WARN).
    Both would break the TSV row contract for the consumer.

    Returns 0 always; this is an advisory/lookup helper, not a validator.
    Missing manifest is a NOTE, not an error.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    manifest_path = draft_dir / "figures_manifest.tsv"
    inventory_path = draft_dir / "figures_inventory.md"

    rows = _parse_figures_manifest(manifest_path)
    if not rows:
        sys.stderr.write(
            f"NOTE: no figures to resolve (manifest empty or missing): {manifest_path}\n"
        )
        # Emit header only so consumers can parse a (possibly empty) TSV.
        print("paper_order_n\tfilename\tcaption\tdescription")
        return 0

    captions = _parse_figures_inventory_captions(inventory_path)
    if not captions:
        sys.stderr.write(
            f"WARN: figures_inventory.md missing or empty: {inventory_path}; "
            "will emit filename-derived captions for all rows.\n"
        )
    descriptors = _parse_figures_inventory_descriptions(inventory_path)
    # descriptors is {} if v1 inventory; description column will be empty.

    print("paper_order_n\tfilename\tcaption\tdescription")
    for row in rows:
        n = row["paper_order_n"]
        filename = row["filename"]
        inv_name = row["inventory_lookup_name"]
        caption = captions.get(inv_name)
        if caption is None:
            caption = _filename_to_caption(inv_name)
            sys.stderr.write(
                f"WARN: inventory has no entry for {inv_name!r} "
                f"(paper_order_n={n}); using filename-derived caption: {caption!r}\n"
            )
        if "\t" in caption:
            sys.stderr.write(
                f"WARN: caption for paper_order_n={n} contains tab; replacing with space\n"
            )
            caption = caption.replace("\t", " ")
        if "\n" in caption:
            sys.stderr.write(
                f"WARN: caption for paper_order_n={n} contains newline; replacing with space\n"
            )
            caption = caption.replace("\n", " ")

        # v0.4 Phase 3: composed description from descriptor (empty when
        # v1 inventory or no Description block).
        descriptor = descriptors.get(inv_name, {})
        description = _assemble_description_text(descriptor)
        if "\t" in description:
            description = description.replace("\t", " ")
        if "\n" in description:
            description = description.replace("\n", " ")

        print(f"{n}\t{filename}\t{caption}\t{description}")

    return 0


# ---------------------------------------------------------------------------
# Tier 2.2 — phase_embed_figures helpers
# ---------------------------------------------------------------------------

# (Fig. N) callout regex. Matches inside or outside parens (so multi-figure
# callouts like "(Fig. 3 and Fig. 5)" surface both Ns). Optional panel
# suffix [A-Z]. Word-boundary anchored to avoid matching in
# `figureN_something.png`-style filenames.
_FIG_CALLOUT_RE = re.compile(r"\bFig\.\s*(\d+)[A-Z]?\b")

# Match an already-embedded figure for paper_order_n=N. Used for
# idempotency — re-running phase_embed_figures must not double-inject.
# v0.6.1: new format uses `**Figure N.**` visible-caption paragraph
# (matches table convention). Old format `![Figure N: ...](...)` kept
# for backward compat with pre-v0.6.1 drafts.
_EMBEDDED_FIGURE_RE_NEW = re.compile(r"\*\*Figure\s+(\d+)\.\*\*")
_EMBEDDED_FIGURE_RE_OLD = re.compile(
    r"!\[Figure\s+(\d+)\s*:.*?\]\(figures/[^)]+\)"
)


def _find_sentence_end_after(text: str, start: int) -> int:
    """Find the position immediately after the next sentence-ending
    `.!?` at or after `start`.

    Skips over `.` characters that are clearly not sentence terminators:
      - `.` inside `Fig. <digit>` patterns (next non-space is a digit).
      - `.` followed by lowercase (abbreviations like `e.g.`, `et al.`).
      - `.` followed by closing brackets/quotes; the actual terminator
        is found after the bracket.

    Heuristic: a `.` (or `!` `?`) is treated as a sentence end when the
    next non-whitespace, non-bracket character is uppercase, OR when
    the punctuation is followed by a newline / end-of-string.

    Returns `len(text)` if no sentence end is found before EOF.
    """
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ".!?":
            j = i + 1
            # Skip past closing brackets / quotes that may follow the
            # sentence-ending punctuation (e.g. `(Fig. 3).` form).
            while j < n and text[j] in ")]}\"'":
                j += 1
            # End-of-string or newline → sentence end.
            if j >= n:
                return j
            if text[j] == "\n":
                return j
            if text[j].isspace():
                # Look past whitespace to the next non-space character.
                k = j
                while k < n and text[k].isspace():
                    k += 1
                if k >= n:
                    return j
                # Sentence end iff next non-space char is uppercase.
                # Excludes `Fig. 5` (digit) and `e.g.` (lowercase).
                if text[k].isupper():
                    return j
        i += 1
    return n


def _build_figure_map(
    draft_dir: Path,
) -> tuple[dict[int, dict], list[str]]:
    """Build paper_order_n → entry-dict from manifest + inventory.

    Each entry-dict has keys:
        filename:   str  — paper-order filename in <draft_dir>/figures/
        caption:    str  — short caption (one phrase) for the alt-text
        descriptor: dict — v0.4 Phase 3 / v2 inventory Description block
                          (empty dict for v1 inventories)

    v0.4 Phase 3: this returns dict-of-dict instead of dict-of-tuple
    (was `(filename, caption)` 2-tuple in v0.3). Callers must use named
    keys, not positional unpacking. The descriptor is the structured
    figures_inventory.md Description block; empty {} when the inventory
    is v1 or has no Description block for this figure.

    Banned-tab / banned-newline / banned-`]` discipline applied to the
    SHORT caption only; descriptor fields are sanitized at render time
    (in `_assemble_description_text`).
    """
    warnings: list[str] = []
    manifest_path = draft_dir / "figures_manifest.tsv"
    inventory_path = draft_dir / "figures_inventory.md"

    rows = _parse_figures_manifest(manifest_path)
    if not rows:
        return {}, [f"NOTE: figures_manifest.tsv missing or empty: {manifest_path}"]

    captions = _parse_figures_inventory_captions(inventory_path)
    if not captions:
        warnings.append(
            f"WARN: figures_inventory.md missing or empty: {inventory_path}; "
            "using filename-derived captions for all rows"
        )
    descriptors = _parse_figures_inventory_descriptions(inventory_path)
    # descriptors is {} if the inventory is v1; that's fine, callers
    # see entry["descriptor"] = {} and skip description rendering.

    figure_map: dict[int, dict] = {}
    for row in rows:
        n = row["paper_order_n"]
        filename = row["filename"]
        inv_name = row["inventory_lookup_name"]
        caption = captions.get(inv_name)
        if caption is None:
            caption = _filename_to_caption(inv_name)
            warnings.append(
                f"WARN: inventory has no entry for {inv_name!r} (paper_order_n={n}); "
                f"using filename-derived caption: {caption!r}"
            )
        # Banned-tab / banned-newline discipline.
        if "\t" in caption:
            caption = caption.replace("\t", " ")
        if "\n" in caption:
            caption = caption.replace("\n", " ")
        # Banned closing-bracket: caption ends up inside `![alt](path)` —
        # a `]` in the caption breaks the markdown image-tag parser. Rare
        # in REPORT-derived captions but surface it defensively.
        if "]" in caption:
            warnings.append(
                f"WARN: caption for paper_order_n={n} contains ']'; "
                "replacing with ')' to keep the markdown image tag parseable"
            )
            caption = caption.replace("]", ")")
        # v0.4 Phase 5c: pull in Source 4 LLM-synthesized caption if it
        # exists. Without this loop-closure, the Source 4 work was
        # write-only — figure_caption_<N>.md was generated and validated
        # but never embedded in the manuscript. Detected during visual
        # review of draft_3: figures 1-5 (LLM-synthesized) had
        # descriptor-derived captions in the docx, not the LLM output.
        # The synthesized file's existence is the signal — no need to
        # check source_chosen in metadata.
        synthesized_caption: Optional[str] = None
        audit_caption_path = draft_dir / "audit" / f"figure_caption_{n}.md"
        if audit_caption_path.is_file():
            try:
                raw = audit_caption_path.read_text(encoding="utf-8").strip()
                # Single-line sanitization for inline alt-text embedding;
                # the LLM may emit minor whitespace variation.
                cleaned = re.sub(r"\s+", " ", raw).strip()
                if cleaned:
                    synthesized_caption = cleaned
            except OSError:
                pass

        figure_map[n] = {
            "filename": filename,
            "caption": caption,
            "descriptor": descriptors.get(inv_name, {}),
            "synthesized_caption": synthesized_caption,
        }
    return figure_map, warnings


def _embed_figures_in_text(
    text: str,
    figure_map: dict[int, dict],
) -> tuple[str, dict[int, str], list[int]]:
    """Inject visible-caption figure blocks after each first `(Fig. N)`
    callout in `text`. v0.6.1 format:

        ![](figures/<filename>)

        **Figure N.** Caption text.

    Matches the table convention for visible captions in rendered markdown.

    Idempotent: if `text` already contains an embedded figure tag for N,
    that N is not re-injected (and any pre-existing description paragraph
    is preserved untouched).

    v0.4 Phase 3: figure_map is dict[int, dict] with keys filename,
    caption, descriptor (was tuple[str, str] in v0.3). When descriptor
    is non-empty, the assembled description is appended as a single-line
    italic paragraph immediately after the image tag. Prose-side panel
    callouts (Stratum 2) are detected per-figure within `text` and
    merged with the AST-derived panels before assembly.

    Multi-figure sentences: when multiple distinct Ns occur in the same
    sentence (e.g. "(Fig. 3 and Fig. 5)"), all are injected after the
    sentence in N-ascending order. Each gets its own optional description
    paragraph.

    Returns (new_text, injected_by_n, skipped_callout_ns) where
      - injected_by_n: {N: filename} for each successfully-injected figure.
      - skipped_callout_ns: callouts whose N has no manifest entry.
    """
    # Idempotency: collect Ns already embedded (union of new + old format).
    already_embedded = {int(m.group(1)) for m in _EMBEDDED_FIGURE_RE_NEW.finditer(text)}
    already_embedded |= {int(m.group(1)) for m in _EMBEDDED_FIGURE_RE_OLD.finditer(text)}

    # First-occurrence per N.
    first_match_per_n: dict[int, "re.Match[str]"] = {}
    for m in _FIG_CALLOUT_RE.finditer(text):
        n = int(m.group(1))
        if n not in first_match_per_n:
            first_match_per_n[n] = m

    skipped: list[int] = []
    injection_points: dict[int, list[int]] = {}  # sentence_end_pos → [N, ...]
    for n, match in first_match_per_n.items():
        if n in already_embedded:
            continue
        if n not in figure_map:
            skipped.append(n)
            continue
        sentence_end = _find_sentence_end_after(text, match.end())
        injection_points.setdefault(sentence_end, []).append(n)

    # Sort each group ascending by N for stable multi-figure order.
    for pos in injection_points:
        injection_points[pos].sort()

    # Apply injections in reverse position order so earlier positions
    # don't shift as later ones are inserted.
    new_text = text
    injected_by_n: dict[int, str] = {}
    for pos in sorted(injection_points.keys(), reverse=True):
        ns = injection_points[pos]
        chunks: list[str] = []
        for n in ns:
            entry = figure_map[n]
            filename = entry["filename"]
            caption = entry["caption"]
            descriptor = entry.get("descriptor") or {}
            # v0.4 Phase 5c: Source 4 loop-closure. If the LLM
            # synthesized a caption for this figure (audit/figure_caption
            # _<N>.md exists), use IT as the description verbatim — the
            # prompt's output protocol already produces a polished
            # ICMJE-style legend, so no descriptor-assembly needed.
            # Falls back to descriptor-based assembly when no synth.
            synthesized = entry.get("synthesized_caption")
            if synthesized:
                description = synthesized
            else:
                prose_panels = _detect_prose_panel_callouts(text, n)
                description = _assemble_description_text(descriptor, prose_panels)
            # v0.4 Phase 5b (visual-review patch): combine short caption +
            # description into a SINGLE alt-text. Eliminates the prior
            # two-paragraph "Figure N: caption" + "*Description: ...*"
            # layout, which ICMJE/Nature convention does not match.
            # Reader sees one ICMJE-style Caption paragraph below each
            # v0.6.1: visible-caption format. Image tag has empty alt;
            # caption is a separate **Figure N.** paragraph below the
            # image — visible in markdown rendering (matches table
            # convention). The docx renderer detects this paragraph
            # after an image block and applies Caption style.
            short = caption.replace("*", "")
            if description:
                desc = description.replace("*", "")
                caption_text = f"{short}. {desc}"
            else:
                caption_text = short
            chunks.append(
                f"\n\n![](figures/{filename})\n\n"
                f"**Figure {n}.** {caption_text}"
            )
            injected_by_n[n] = filename
        chunks.append("\n\n")
        inject = "".join(chunks)
        new_text = new_text[:pos] + inject + new_text[pos:]

    return new_text, injected_by_n, sorted(skipped)


def cmd_embed_figures(args: argparse.Namespace) -> int:
    """Walk section files for `(Fig. N)` callouts; inject markdown image
    tags after the first occurrence of each N's containing sentence.

    Reads `<draft_dir>/figures_manifest.tsv` + `figures_inventory.md`,
    builds paper_order_n → (filename, caption), then walks
    `02_results.md`, `01_methods.md`, `03_discussion.md` for callouts
    and injects `![Figure N: caption](figures/<filename>)` after each
    sentence containing the first occurrence of `(Fig. N)`.

    Idempotent: re-running does not double-inject.

    Multi-figure callouts ("(Fig. 3 and Fig. 5)") inject both tags
    after the same sentence in N-ascending order.

    Returns 0 always; missing manifest is a NOTE, not an error
    (consistent with v0.1+ pump-through philosophy: figureless drafts
    are valid output).

    Stdout: one-line summary: `embedded: K total across N section(s)`.
    Stderr: per-section embed counts + any WARNs.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        sys.stderr.write(f"WARN: draft_dir does not exist: {draft_dir}\n")
        print("embedded: 0")
        return 0

    figure_map, warnings = _build_figure_map(draft_dir)
    for w in warnings:
        sys.stderr.write(f"{w}\n")

    if not figure_map:
        print("embedded: 0 (no manifest)")
        return 0

    section_files = ("02_results.md", "01_methods.md", "03_discussion.md")
    total_embedded = 0
    sections_touched = 0
    all_skipped: dict[str, list[int]] = {}

    for section_name in section_files:
        section_path = draft_dir / section_name
        if not section_path.is_file():
            continue
        text = section_path.read_text(encoding="utf-8")

        new_text, injected_by_n, skipped = _embed_figures_in_text(text, figure_map)

        if skipped:
            all_skipped[section_name] = skipped

        if new_text == text:
            sys.stderr.write(f"  {section_name}: no embeds (idempotent skip or no callouts)\n")
            continue

        section_path.write_text(new_text, encoding="utf-8")
        n_inj = len(injected_by_n)
        total_embedded += n_inj
        sections_touched += 1
        sys.stderr.write(
            f"  {section_name}: embedded {n_inj} figure(s): "
            f"{sorted(injected_by_n.keys())}\n"
        )

    if all_skipped:
        for sec, ns in all_skipped.items():
            sys.stderr.write(
                f"WARN: {sec} cites (Fig. {ns}) but manifest has no rows for "
                f"these Ns; phase_check_figures_manifest will surface this\n"
            )

    print(
        f"embedded: {total_embedded} total across {sections_touched} section(s)"
    )
    return 0


# ---------------------------------------------------------------------------
# v0.4 Phase 4c — caption synthesis: bundle builder + sufficiency gate
# ---------------------------------------------------------------------------


def _strip_heading_lines(text: str) -> str:
    """Drop lines whose first non-blank char is `#` (markdown headings).

    Used by the Phase 4c sufficiency-gate word-count check so that
    section-only walk-backs (e.g. `## 4. Figures` — 13 chars but only
    a heading) correctly fail the gate.
    """
    out_lines: list[str] = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _word_count(text: str) -> int:
    """Count whitespace-separated tokens in `text`."""
    return sum(1 for tok in re.split(r"\s+", text.strip()) if tok)


_SUFFICIENCY_PROSE_MIN_WORDS = 30


def _caption_max_words(panel_count: int) -> int:
    """Per-figure ICMJE-conventional caption word budget; scales with
    panel count.

    v0.5 addition. Single-panel figures get 200 words (the v0.4 default).
    Multi-panel figures get 200 + 50*N where N = number of panels —
    e.g., 250 for 1-panel, 300 for 2-panel, 400 for 4-panel, 500 for
    6-panel. Without this scaling, the v0.4 universal 200-word cap
    truncated 4-panel figures (like fig 8 in functional_dark_matter
    draft_3) mid-word, making panels B/C/D undescribed.

    Negative or zero panel_count → 200 (single-panel default).
    """
    if panel_count <= 0:
        return 200
    return 200 + 50 * panel_count


def _passes_sufficiency_gate(descriptor: dict) -> bool:
    """Sufficiency-gate v0.5 (revised after Phase 5b visual-review feedback).

    A descriptor PASSES (no Source 4 invocation needed) iff BOTH:
      a) `word_count(_strip_prose_for_inline(notebook_prose)) >= 30`, AND
      b) descriptor.title is non-None OR descriptor.axes_labels is non-empty.

    Equivalently, FAILS (Source 4 needed) iff EITHER:
      a) prose is too sparse OR boilerplate-dense (<30 real words after
         the aggressive boilerplate strip), OR
      b) AST yielded no title AND no axes labels.

    Why this changed in v0.5: the v0.4 gate used `_strip_heading_lines`
    which only dropped `#`-prefixed lines. Boilerplate-heavy notebook
    prose (Purpose:/Approach:/Sections:/etc.) passed the gate but
    produced poor captions in the docx because the deterministic
    description-assembly path ran on prose that was 80% boilerplate.

    `_strip_prose_for_inline` is the more aggressive strip already used
    at description-render time — keyword headers (both `**X:**` and
    `**X**:` bold idioms), inline-cascade keywords, numbered lists,
    project-internal artifact references, and bold-Bold:Bold patterns
    all get dropped. Using it as the gate predicate ensures figures
    whose prose is mostly boilerplate route to the LLM (which has
    explicit anti-pattern discipline against the same boilerplate).

    Returns True iff the descriptor is sufficient. Empty descriptor →
    False (insufficient).
    """
    if not isinstance(descriptor, dict) or not descriptor:
        return False
    prose = descriptor.get("notebook_prose") or ""
    stripped = _strip_prose_for_inline(prose)
    if _word_count(stripped) < _SUFFICIENCY_PROSE_MIN_WORDS:
        return False
    title = descriptor.get("title")
    axes = descriptor.get("axes_labels") or []
    if title is None and not axes:
        return False
    return True


def _extract_report_prose_for_figure(
    report_text: str, inv_name: str, paper_order_n: int,
) -> str:
    """Pull ±2 paragraphs from REPORT.md around any reference to this
    figure's filename or paper-order number. Empty string if no
    reference is found.
    """
    if not report_text:
        return ""
    # Try filename match first; fall back to (Fig. N) form.
    needles = []
    if inv_name:
        needles.append(inv_name)
        # Also try the basename without extension.
        stem = Path(inv_name).stem
        if stem and stem != inv_name:
            needles.append(stem)
    needles.append(f"Fig. {paper_order_n}")
    needles.append(f"Figure {paper_order_n}")

    paragraphs = re.split(r"\n\s*\n", report_text)
    for i, para in enumerate(paragraphs):
        if any(n in para for n in needles):
            # ±2 paragraphs of context.
            lo = max(0, i - 2)
            hi = min(len(paragraphs), i + 3)
            return "\n\n".join(paragraphs[lo:hi]).strip()
    return ""


def _extract_results_section_prose_for_figure(
    results_text: str, paper_order_n: int,
) -> str:
    """Pull the (Fig. N) callout sentence + ±2 sentences of surrounding
    context from 02_results.md. Empty string if no callout found.
    """
    if not results_text:
        return ""
    callout_re = re.compile(rf"\bFig\.\s*{paper_order_n}\b")
    m = callout_re.search(results_text)
    if not m:
        return ""
    # Walk backward to start of paragraph (or 2 sentence-ends back).
    start = m.start()
    sent_terminators = 0
    i = start
    while i > 0 and sent_terminators < 3:
        if results_text[i] in ".!?":
            sent_terminators += 1
        i -= 1
    paragraph_start = max(0, i)
    # Walk forward similarly.
    end = m.end()
    sent_terminators = 0
    j = end
    while j < len(results_text) and sent_terminators < 3:
        if results_text[j] in ".!?":
            sent_terminators += 1
        j += 1
    excerpt = results_text[paragraph_start:j].strip()
    return excerpt


def cmd_build_caption_bundles(args: argparse.Namespace) -> int:
    """Build per-figure input bundles for figure_caption.v1 invocations.

    Reads:
      - <draft_dir>/figures_manifest.tsv (manifest)
      - <draft_dir>/figures_inventory.md (v2 schema with descriptors)
      - <project_root>/REPORT.md
      - <draft_dir>/02_results.md

    Writes:
      - <bundles_dir>/figure_<N>.bundle.json (one per figure needing
        Source 4)
      - <draft_dir>/audit/figure_caption.v1.metadata.json (initial
        version with all manifest figures classified by source_chosen;
        LLM entries lack closing_message until Phase 4c orchestrator
        updates them).

    Stdout: one figure_id per line for figures needing Source 4
    invocation. Bash iterates this list. Empty stdout means all
    figures pass the sufficiency gate.

    Always exits 0; missing manifest is a NOTE on stderr.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    bundles_dir = Path(args.bundles_dir).expanduser().resolve()
    bundles_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = draft_dir / "figures_manifest.tsv"
    inventory_path = draft_dir / "figures_inventory.md"
    report_path = project_root / "REPORT.md"
    results_path = draft_dir / "02_results.md"

    rows = _parse_figures_manifest(manifest_path)
    if not rows:
        sys.stderr.write(
            f"[caption-bundles] NOTE: figures_manifest.tsv missing or empty: "
            f"{manifest_path}; phase_caption_synthesis has nothing to do.\n"
        )
        # Still emit an empty metadata file so the post-checker has
        # something to find.
        meta_path = audit_dir / "figure_caption.v1.metadata.json"
        meta_path.write_text(json.dumps({"schema_version": 1, "captions": []}),
                              encoding="utf-8")
        return 0

    descriptors = _parse_figures_inventory_descriptions(inventory_path)
    captions_short = _parse_figures_inventory_captions(inventory_path)
    report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    results_text = results_path.read_text(encoding="utf-8") if results_path.is_file() else ""

    metadata_entries: list[dict] = []
    figure_ids_for_llm: list[int] = []
    # v0.5: max_words now scales by panel_count per
    # `_caption_max_words` (200 for single-panel, +50 per panel for
    # multi-panel). The --max-words CLI override is preserved as a
    # ceiling-override for testing but defaults to None so the formula
    # prevails. Multi-panel figures need more word budget to describe
    # each panel without truncating mid-word; ICMJE convention allows
    # 300-400 words for complex multi-panel legends.
    max_words_override = getattr(args, "max_words", None)

    for row in rows:
        n = row["paper_order_n"]
        filename = row["filename"]
        inv_name = row["inventory_lookup_name"]
        descriptor = descriptors.get(inv_name, {})
        short_caption = captions_short.get(inv_name, _filename_to_caption(inv_name))

        if _passes_sufficiency_gate(descriptor):
            metadata_entries.append({
                "figure_id": n,
                "filename": filename,
                "inventory_lookup_name": inv_name,
                "source_chosen": "deterministic",
                "reason": "Sources 2+3 sufficient (notebook prose ≥30 real words AND title-or-axes populated)",
            })
            continue

        # Source 4 needed — build the input bundle.
        prose_panel_callouts = _detect_prose_panel_callouts(results_text, n)
        report_prose = _extract_report_prose_for_figure(
            report_text, inv_name, n,
        )
        results_section_prose = _extract_results_section_prose_for_figure(
            results_text, n,
        )

        # v0.5: compute panel-count-scaled word budget. Use AST-detected
        # panels (descriptor.panels) since that's the deterministic
        # signal; if AST missed and only prose panels exist, those
        # surface in prose_panel_callouts but we don't double-count.
        panel_count = len(descriptor.get("panels") or [])
        if max_words_override is not None:
            max_words = int(max_words_override)
        else:
            max_words = _caption_max_words(panel_count)

        bundle = {
            "figure_id": n,
            "short_caption": short_caption,
            "structured_descriptor": descriptor,
            "prose_panel_callouts": prose_panel_callouts,
            "report_prose": report_prose,
            "results_section_prose": results_section_prose,
            "max_words": max_words,
        }
        bundle_path = bundles_dir / f"figure_{n}.bundle.json"
        bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        metadata_entries.append({
            "figure_id": n,
            "filename": filename,
            "inventory_lookup_name": inv_name,
            "output_path": f"audit/figure_caption_{n}.md",
            "input_bundle": bundle,
            "source_chosen": "llm",
            # closing_message left absent; phase_caption_synthesis fills.
        })
        figure_ids_for_llm.append(n)

    # Write initial metadata.json (Phase 4c orchestrator updates entries
    # for LLM figures after each invocation).
    meta_path = audit_dir / "figure_caption.v1.metadata.json"
    meta_path.write_text(
        json.dumps({"schema_version": 1, "captions": metadata_entries},
                   indent=2),
        encoding="utf-8",
    )

    # Stdout: figure_ids for bash to iterate.
    for fid in figure_ids_for_llm:
        print(fid)
    sys.stderr.write(
        f"[caption-bundles] {len(rows)} figures total: "
        f"{len(rows) - len(figure_ids_for_llm)} pass gate (deterministic), "
        f"{len(figure_ids_for_llm)} need Source 4 (llm)\n"
    )
    return 0


def cmd_compute_caption_stats(args: argparse.Namespace) -> int:
    """Compute word/panel/numerical-claim counts for an LLM-written caption,
    then update audit/figure_caption.v1.metadata.json's entry.

    Reads `<draft_dir>/audit/figure_caption_<N>.md`; counts:
      - word_count: whitespace-separated tokens.
      - panel_count: distinct panel-letter mentions (regex-matched
        `(A)`, `(panel A)`, `panel A`, `panel labeled A` forms).
      - traceable_claims: distinct numerical tokens (the same ones the
        provenance checker enumerates).

    Updates the corresponding entry in metadata.json with a
    `closing_message` block. Idempotent (re-running overwrites the
    closing_message for that figure).

    Always exits 0. WARN to stderr on missing inputs.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    figure_id = int(args.figure_id)

    meta_path = draft_dir / "audit" / "figure_caption.v1.metadata.json"
    if not meta_path.is_file():
        sys.stderr.write(
            f"[compute-caption-stats] WARN: metadata not found at {meta_path}\n"
        )
        return 0
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(
            f"[compute-caption-stats] WARN: metadata invalid JSON: {e}\n"
        )
        return 0

    entries = data.get("captions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        sys.stderr.write(
            "[compute-caption-stats] WARN: metadata missing captions list\n"
        )
        return 0

    target_entry = None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("figure_id") == figure_id:
            target_entry = entry
            break
    if target_entry is None:
        sys.stderr.write(
            f"[compute-caption-stats] WARN: figure_id={figure_id} "
            f"not in metadata\n"
        )
        return 0

    output_path = target_entry.get("output_path")
    if not output_path:
        sys.stderr.write(
            f"[compute-caption-stats] WARN: figure_id={figure_id} "
            f"has no output_path in metadata\n"
        )
        return 0
    cap_path = draft_dir / output_path
    if not cap_path.is_file():
        sys.stderr.write(
            f"[compute-caption-stats] WARN: caption file not found at "
            f"{cap_path}; LLM may have failed to call Write\n"
        )
        return 0

    text = cap_path.read_text(encoding="utf-8").strip()
    word_count = sum(1 for tok in re.split(r"\s+", text) if tok)

    # Panel count: same regex shape as check_caption_provenance.
    panel_re = re.compile(
        r"\((?:panel\s+)?([A-Z])\)|"
        r"\bpanel\s+(?:labeled\s+)?([A-Z])\b",
    )
    panels: set[str] = set()
    for m in panel_re.finditer(text):
        letter = (m.group(1) or m.group(2) or "").upper()
        if letter:
            panels.add(letter)

    # Traceable-claims count: numerical tokens (matches checker's regex).
    num_re = re.compile(
        r"\b(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:%|x|×|±|~)?\b",
    )
    numerical = {m.group(0) for m in num_re.finditer(text)}

    target_entry["closing_message"] = {
        "word_count": word_count,
        "panel_count": len(panels),
        "traceable_claims": len(numerical),
    }

    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(
        f"figure_caption_{figure_id} word_count {word_count} "
        f"traceable_claims {len(numerical)} panel_count {len(panels)}"
    )
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
# v0.6 Tier 9 — Tables: caption gate, inventory parsing, embed
# ---------------------------------------------------------------------------

# Stopwords for the table caption relevance check (column-overlap gate).
_TABLE_CAPTION_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "is", "are", "was", "were", "with", "by", "from", "as", "no", "not",
    "per", "vs", "all", "each", "any",
})


def _table_caption_passes_gate(
    caption_text: str,
    column_names: list[str],
) -> bool:
    """Sufficiency gate for table captions (v0.6).

    A caption PASSES (no LLM fallback needed) iff:
      a) caption_text is non-empty,
      b) caption has >5 words after stripping markdown formatting,
      c) EITHER the caption is descriptive enough (>10 words — it's
         likely a finding-level heading like "Finding 6: X reveals Y"),
         OR at least 1 word in caption also appears in column_names
         (case-insensitive, stopwords excluded).

    The >10-word bypass exists because BERIL section headings describe
    findings, not table structure. "Finding 6: Within-species biogeographic
    analysis reveals 10 significant clusters" is a perfectly good caption
    even though it shares zero words with column names like "Organism",
    "Locus", "FDR". Short captions like "Results summary" still need the
    column-overlap check to filter out generic headings.

    Returns True iff sufficient.
    """
    if not caption_text or not caption_text.strip():
        return False
    # Strip markdown bold/italic markers
    clean = re.sub(r"[*_`#]", "", caption_text).strip()
    words = clean.split()
    if len(words) <= 5:
        return False
    # Descriptive captions (>10 words) pass without column overlap.
    if len(words) > 10:
        return True
    # Short captions (6–10 words) need column-name word overlap.
    col_words = set()
    for col_name in column_names:
        for w in re.sub(r"[|*_`]", "", col_name).split():
            lw = w.lower().strip()
            if lw and lw not in _TABLE_CAPTION_STOPWORDS:
                col_words.add(lw)
    if not col_words:
        # If all column names are stopwords (unlikely), pass the gate
        return True
    caption_words = {w.lower().strip(".,;:()") for w in words}
    overlap = caption_words & col_words
    return len(overlap) >= 1


def _parse_tables_manifest(manifest_path: Path) -> list[dict]:
    """Parse tables_manifest.tsv into a list of row dicts.

    Returns [] if file missing or unparseable. Tolerates extra
    whitespace in cells. Skips blank lines after the header.
    """
    if not manifest_path.is_file():
        return []
    text = manifest_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) < 2:
        return []
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 3:
            continue
        try:
            n = int(cells[0].strip())
        except ValueError:
            continue
        rows.append({
            "paper_order_n": n,
            "table_id": cells[1].strip(),
            "inventory_lookup_name": cells[2].strip(),
        })
    return rows


def _parse_tables_inventory(inventory_path: Path) -> dict[str, dict]:
    """Parse tables_inventory.md into {table_id: {"caption": str, "content": str}}.

    Extracts the best caption (heading > preceding_sentence > fallback)
    and the full markdown table content for each entry.
    """
    if not inventory_path.is_file():
        return {}

    text = inventory_path.read_text(encoding="utf-8")
    entries: dict[str, dict] = {}
    current_id: Optional[str] = None
    current_data: dict = {}
    in_content = False
    content_lines: list[str] = []
    caption_candidates: list[tuple[str, str]] = []  # (source, text)
    column_names: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()

        # New entry header
        m = re.match(r"^###\s+(report_tbl_\d+)\s", stripped)
        if m:
            # Flush previous entry
            if current_id:
                _flush_table_entry(
                    entries, current_id, current_data, caption_candidates,
                    content_lines, column_names,
                )
            current_id = m.group(1)
            current_data = {}
            in_content = False
            content_lines = []
            caption_candidates = []
            column_names = []
            continue

        if not current_id:
            continue

        # Column names from the _Columns: line
        cm = re.match(r"_Columns:\s*\d+\s*\((.+)\)_", stripped)
        if cm:
            column_names = [c.strip() for c in cm.group(1).split("|")]
            continue

        # Caption candidates
        cap_m = re.match(
            r"^-\s+\*\*(section heading|preceding sentence|LLM-generated)\*\*:\s+(.+)$",
            stripped,
        )
        if cap_m:
            caption_candidates.append((cap_m.group(1), cap_m.group(2)))
            continue

        # Content block: starts with "**Content (first 3 rows):**"
        if stripped == "**Content (first 3 rows):**":
            in_content = True
            content_lines = []
            continue

        if in_content:
            if stripped.startswith("|") and stripped.endswith("|"):
                content_lines.append(line)
            elif stripped.startswith("_(") and "data rows total" in stripped:
                # End of preview — we need to read full content from the
                # table's markdown_content field. But the inventory only
                # shows a preview. We'll use the preview for now; the
                # full content is in the JSON report.
                in_content = False
            elif stripped == "" and content_lines:
                # Blank line after content block
                in_content = False
            elif not stripped and not content_lines:
                # Blank line before content starts — keep waiting
                pass
            else:
                in_content = False

    # Flush last entry
    if current_id:
        _flush_table_entry(
            entries, current_id, current_data, caption_candidates,
            content_lines, column_names,
        )

    return entries


def _flush_table_entry(
    entries: dict[str, dict],
    table_id: str,
    data: dict,
    caption_candidates: list[tuple[str, str]],
    content_lines: list[str],
    column_names: list[str],
) -> None:
    """Finalize one table entry and add it to the entries dict."""
    # Pick best caption: heading > preceding_sentence > llm
    caption = ""
    priority = {"section heading": 0, "preceding sentence": 1, "LLM-generated": 2}
    if caption_candidates:
        sorted_caps = sorted(caption_candidates, key=lambda x: priority.get(x[0], 99))
        caption = sorted_caps[0][1]

    entries[table_id] = {
        "caption": caption,
        "content": "\n".join(content_lines) if content_lines else "",
        "column_names": column_names,
    }


def _build_table_map(
    draft_dir: Path,
) -> dict[int, dict]:
    """Join tables_manifest.tsv with tables_inventory.md.

    Returns {paper_order_n: {"table_id": ..., "caption": ..., "content": ...,
    "column_names": [...]}}.
    """
    manifest_path = draft_dir / "tables_manifest.tsv"
    inventory_path = draft_dir / "tables_inventory.md"

    manifest_rows = _parse_tables_manifest(manifest_path)
    inventory = _parse_tables_inventory(inventory_path)

    table_map: dict[int, dict] = {}
    for row in manifest_rows:
        inv_name = row["inventory_lookup_name"]
        entry = inventory.get(inv_name, {})
        table_map[row["paper_order_n"]] = {
            "table_id": row["table_id"],
            "caption": entry.get("caption", ""),
            "content": entry.get("content", ""),
            "column_names": entry.get("column_names", []),
            "inventory_lookup_name": inv_name,
        }
    return table_map


# Table callout regex — matches (Table N) in prose.
_TABLE_CALLOUT_RE = re.compile(r"\bTable\s+(\d+)\b")

# Idempotency guard: detects **Table N.** blocks already injected.
_EMBEDDED_TABLE_RE = re.compile(r"\*\*Table\s+(\d+)\.\*\*")


def _embed_tables_in_text(
    text: str,
    table_map: dict[int, dict],
    already_embedded: set[int],
) -> str:
    """Walk text for (Table N) callouts and inject markdown table blocks.

    Injection point: after the first sentence containing `(Table N)`.
    Injected block:

        **Table N.** Caption text.

        | col1 | col2 | ... |
        |------|------|-----|
        | data | data | ... |

    Idempotent: pre-scans for existing **Table N.** blocks and adds
    their Ns to already_embedded before processing callouts.
    """
    # Pre-scan for already-embedded tables (idempotency).
    for m in _EMBEDDED_TABLE_RE.finditer(text):
        already_embedded.add(int(m.group(1)))

    lines = text.split("\n")
    output: list[str] = []

    for line in lines:
        output.append(line)

        # Check for Table N callouts in this line
        matches = list(_TABLE_CALLOUT_RE.finditer(line))
        if not matches:
            continue

        # Collect unique N values from this line, in ascending order
        ns = sorted(set(int(m.group(1)) for m in matches))
        for n in ns:
            if n in already_embedded:
                continue
            if n not in table_map:
                continue

            entry = table_map[n]
            caption = entry.get("caption", "")
            content = entry.get("content", "")

            if not content:
                continue

            # Inject after this line
            output.append("")
            if caption:
                output.append(f"**Table {n}.** {caption}")
            else:
                output.append(f"**Table {n}.**")
            output.append("")
            output.append(content)
            output.append("")
            already_embedded.add(n)

    return "\n".join(output)


def cmd_embed_tables(args: argparse.Namespace) -> int:
    """Walk section files for (Table N) callouts; inject markdown table
    blocks after the first occurrence of each N's containing line.
    Idempotent.

    v0.6 Tier 9 — mirrors cmd_embed_figures.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        sys.stderr.write(f"embed-tables: draft_dir not found: {draft_dir}\n")
        return 1

    table_map = _build_table_map(draft_dir)
    if not table_map:
        print("embedded: 0")
        sys.stderr.write(
            "embed-tables: no tables to embed (empty manifest or inventory)\n"
        )
        return 0

    section_files = ["02_results.md", "01_methods.md", "03_discussion.md"]
    already_embedded: set[int] = set()
    total_embedded = 0
    sections_touched = 0

    # Pre-scan all sections for existing **Table N.** blocks (idempotency).
    # This ensures already_embedded is populated before any per-section
    # counting, so before_count/after_count accurately reflect NEW
    # injections only.
    for section_name in section_files:
        section_path = draft_dir / section_name
        if not section_path.is_file():
            continue
        text = section_path.read_text(encoding="utf-8")
        for m in _EMBEDDED_TABLE_RE.finditer(text):
            already_embedded.add(int(m.group(1)))

    for section_name in section_files:
        section_path = draft_dir / section_name
        if not section_path.is_file():
            continue

        text = section_path.read_text(encoding="utf-8")
        before_count = len(already_embedded)
        new_text = _embed_tables_in_text(text, table_map, already_embedded)
        after_count = len(already_embedded)

        if after_count > before_count:
            section_path.write_text(new_text, encoding="utf-8")
            n_new = after_count - before_count
            total_embedded += n_new
            sections_touched += 1
            sys.stderr.write(
                f"embed-tables: {section_name}: embedded {n_new} table(s)\n"
            )

    # stdout summary (orchestrator greps for '^embedded: ')
    print(
        f"embedded: {total_embedded} total across {sections_touched} section(s)"
    )

    # stderr diagnostics
    sys.stderr.write(
        f"embed-tables: total embedded: {total_embedded} "
        f"across {sections_touched} section(s)\n"
    )

    # Report un-embedded tables (in manifest but no callout in prose)
    for n, entry in sorted(table_map.items()):
        if n not in already_embedded:
            sys.stderr.write(
                f"embed-tables: NOTE: Table {n} ({entry['table_id']}) is in "
                f"manifest but had no (Table {n}) callout in prose\n"
            )

    return 0


def cmd_apply_table_captions(args: argparse.Namespace) -> int:
    """Apply caption sufficiency gate to tables; report which need LLM fallback.

    v0.6 Tier 9 Phase 4. Reads tables_inventory.md, applies the gate,
    emits table_ids that need LLM-generated captions to stdout.

    For v0.6, the LLM fallback is deferred to the orchestrator (shell
    side) which decides whether to invoke a lightweight LLM call per
    failed table. This command only IDENTIFIES the failures.
    """
    draft_dir = Path(args.draft_dir).expanduser().resolve()
    inventory_path = draft_dir / "tables_inventory.md"

    inventory = _parse_tables_inventory(inventory_path)
    if not inventory:
        sys.stderr.write(
            "apply-table-captions: no tables in inventory; nothing to check\n"
        )
        return 0

    failed: list[str] = []
    passed: list[str] = []
    for table_id, entry in sorted(inventory.items()):
        caption = entry.get("caption", "")
        col_names = entry.get("column_names", [])
        if _table_caption_passes_gate(caption, col_names):
            passed.append(table_id)
        else:
            failed.append(table_id)
            sys.stderr.write(
                f"apply-table-captions: GATE FAIL: {table_id} — "
                f"caption={caption!r:.60}, columns={col_names}\n"
            )

    sys.stderr.write(
        f"apply-table-captions: {len(passed)} pass, {len(failed)} fail "
        f"(of {len(inventory)} total)\n"
    )

    # Emit failed table_ids to stdout (one per line) for orchestrator consumption
    for tid in failed:
        print(tid)

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

    # extract-tier
    p_et = sub.add_parser("extract-tier",
                          help="Extract tier from throughline_candidates.md → state.json.")
    p_et.add_argument("candidates_path", help="Path to throughline_candidates.md")
    p_et.add_argument("--draft-dir", help="Draft dir to write tier into state.json")
    p_et.set_defaults(func=cmd_extract_tier)

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

    # parse-reframing-log (v0.6.4) — parse reframing_log.md; emit JSON
    # of entries with dispatch info for repair.
    p_rl2 = sub.add_parser(
        "parse-reframing-log",
        help="Parse reframing_log.md; emit JSON of entries with repair dispatch info.",
    )
    p_rl2.add_argument("reframing_log", help="Path to reframing_log.md")
    p_rl2.add_argument("--draft-dir", default=None,
                       help="Draft directory (for target_path resolution).")
    p_rl2.add_argument("--escalated-only", action="store_true",
                       help="Only emit entries with resolution_action == escalated.")
    p_rl2.set_defaults(func=cmd_parse_reframing_log)

    # list-reframing-repairs (v0.6.4) — emit one pipe-delimited line per
    # section-target for escalated reframing entries (bash loop consumer).
    p_lr = sub.add_parser(
        "list-reframing-repairs",
        help="Print one repair dispatch line per escalated section target.",
    )
    p_lr.add_argument("reframing_log", help="Path to reframing_log.md")
    p_lr.add_argument("draft_dir", help="Draft directory.")
    p_lr.set_defaults(func=cmd_list_reframing_repairs)

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

    # resolve-figures (v0.3 Tier 2.1c) — join figures_manifest.tsv with
    # figures_inventory.md; emit a TSV of (paper_order_n, filename,
    # caption) for phase_embed_figures to consume.
    p_rf = sub.add_parser(
        "resolve-figures",
        help="Join figures_manifest.tsv with figures_inventory.md; "
             "emit TSV of (paper_order_n, filename, caption) to stdout.",
    )
    p_rf.add_argument("draft_dir")
    p_rf.set_defaults(func=cmd_resolve_figures)

    # embed-figures (v0.3 Tier 2.2) — walk section files for (Fig. N)
    # callouts; inject markdown image tags after the first occurrence
    # of each N's containing sentence. Idempotent.
    p_ef = sub.add_parser(
        "embed-figures",
        help="Inject ![Figure N: caption](figures/<filename>) markdown "
             "image tags into section files after each first (Fig. N) "
             "callout's sentence. Idempotent.",
    )
    p_ef.add_argument("draft_dir")
    p_ef.set_defaults(func=cmd_embed_figures)

    # build-caption-bundles (v0.4 Phase 4c) — assemble per-figure input
    # bundles for figure_caption.v1; emits figure_ids needing Source 4.
    p_bcb = sub.add_parser(
        "build-caption-bundles",
        help="Build per-figure caption bundles + apply sufficiency gate. "
             "Emits figure_ids needing Source 4 (LLM) to stdout.",
    )
    p_bcb.add_argument("--draft-dir", required=True)
    p_bcb.add_argument("--project-root", required=True)
    p_bcb.add_argument("--bundles-dir", required=True,
                        help="Directory to write per-figure bundle JSONs.")
    p_bcb.add_argument("--max-words", type=int, default=200,
                        help="Per-caption word ceiling (passed to prompt).")
    p_bcb.set_defaults(func=cmd_build_caption_bundles)

    # compute-caption-stats (v0.4 Phase 4c) — count word/panel/numerical
    # claims for an LLM-written caption; update metadata.json.
    p_ccs = sub.add_parser(
        "compute-caption-stats",
        help="Compute word/panel/traceable-claims counts for an "
             "LLM-written caption; update metadata.json.",
    )
    p_ccs.add_argument("--draft-dir", required=True)
    p_ccs.add_argument("--figure-id", required=True, type=int)
    p_ccs.set_defaults(func=cmd_compute_caption_stats)

    # embed-tables (v0.6 Tier 9) — walk section files for (Table N)
    # callouts; inject markdown table blocks. Idempotent.
    p_et = sub.add_parser(
        "embed-tables",
        help="Inject markdown table blocks into section files after each "
             "first (Table N) callout. Idempotent.",
    )
    p_et.add_argument("draft_dir")
    p_et.set_defaults(func=cmd_embed_tables)

    # apply-table-captions (v0.6 Tier 9 Phase 4) — sufficiency gate for
    # table captions; emits table_ids needing LLM fallback.
    p_atc = sub.add_parser(
        "apply-table-captions",
        help="Apply caption sufficiency gate to tables; emit table_ids "
             "needing LLM fallback to stdout.",
    )
    p_atc.add_argument("--draft-dir", required=True)
    p_atc.set_defaults(func=cmd_apply_table_captions)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
