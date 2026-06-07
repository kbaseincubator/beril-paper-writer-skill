#!/usr/bin/env python3
"""finalize_deliverable.py — Cycle 2 remediation half (paper-writer).

Reads the most-recent `audit/deliverable_validation.json` produced by
`validate_deliverable.py check`, applies the deterministic auto-
remediations the findings prescribe, re-runs validate, and writes the
final readiness verdict. Targeted-remediable + advisory findings are
surfaced (not auto-applied) — the operator runs those.

Pattern source: beril-presentation-maker-skill v1.2.0 tools/
finalize_deliverable.py. Same separation discipline (detection in
validate_deliverable.py is pure; remediation here may mutate audit
files but NEVER the manuscript itself).

CONTRACT WITH validate_deliverable.py:
  - validate emits Findings with `remediation.kind ∈ {auto, targeted,
    advisory}`.
  - For each `kind=auto` finding, we dispatch on `remediation.action`
    to a handler. Two handlers ship:
        reassemble        — re-run assemble_docx via the assemble.py
                            command path (markdown -> docx). Re-emits
                            the same pictures the spec describes; no
                            LLM, no mutation of manuscript.md.
        rerun_validate    — re-run validate_manuscript.run_all_validators
                            and write audit/validate_manuscript.json
                            so the section-completeness gate has a
                            fresh ValidationReport to project from.
                            Pure read; never mutates manuscript.md.
  - After all auto-remediations attempt to apply, finalize calls
    validate_deliverable.validate() again. The second pass's findings
    are the canonical "what's left."
  - Exit code = readiness_exit_code(second-pass findings). The
    deliverable is ALWAYS produced; never deleted, never recomputed
    upstream of assemble.

Cycle-1 G1 followup applies here too: auto-remediation fires ONLY on
unambiguous deterministic fixes. Fuzzy detections (e.g. the dirname-
leak title finding) are TARGETED — never auto-mutate the manuscript.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))


# ---------------------------------------------------------------------------
# Auto-remediation handlers
# ---------------------------------------------------------------------------


def _rerun_validate_manuscript(draft_dir: Path) -> tuple[bool, str]:
    """Auto-action rerun_validate. Re-run validate_manuscript with the
    user's resolved mode and write `audit/validate_manuscript.json`
    so the next validate_deliverable pass projects from the fresh
    report. Pure read; never mutates manuscript.md."""
    try:
        import validate_manuscript as vm  # noqa: E402
    except Exception as exc:
        return False, f"could not import validate_manuscript: {exc!r}"

    # Resolve mode the same way validate_deliverable.validate does.
    mode = "paper"
    try:
        import user_intent  # noqa: E402
        ui_mode = user_intent.read_field(draft_dir, "mode")
        if ui_mode is not None and user_intent.field_was_explicit(
                draft_dir, "mode"):
            mode = ui_mode
    except Exception:
        pass
    # Fall back to state.json if user_intent didn't override.
    if mode == "paper":
        state_path = draft_dir / "state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state_mode = state.get("mode") if isinstance(state, dict) else None
                if isinstance(state_mode, str):
                    mode = state_mode
            except (OSError, json.JSONDecodeError):
                pass

    try:
        report = vm.run_all_validators(draft_dir, mode=mode)
    except Exception as exc:
        return False, f"validate_manuscript.run_all_validators failed: {exc!r}"

    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / "validate_manuscript.json"
    out.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8",
    )
    return True, f"re-ran validate_manuscript (mode={mode!r}); wrote {out}"


def _reassemble(draft_dir: Path) -> tuple[bool, str]:
    """Auto-action reassemble. Re-run assemble_docx against the current
    manuscript.md; writes manuscript.docx. No LLM."""
    import importlib.util
    asm_py = _TOOLS_DIR / "assemble_docx.py"
    if not asm_py.is_file():
        return False, f"assemble_docx.py not found at {asm_py}"

    spec = importlib.util.spec_from_file_location(
        "_fd_assemble_docx", asm_py,
    )
    if spec is None or spec.loader is None:
        return False, "could not load assemble_docx spec"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_fd_assemble_docx"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        return False, f"could not exec assemble_docx: {exc!r}"

    manuscript_md = draft_dir / "manuscript.md"
    if not manuscript_md.is_file():
        return False, "manuscript.md missing; cannot reassemble"
    out_docx = draft_dir / "manuscript.docx"

    # assemble_docx exposes a `main` CLI; the cleanest way to drive
    # it programmatically is to call into the underlying convert
    # function if exposed, else shell out. The shipped module has a
    # `main(argv=None)` entry — use that.
    main_fn = getattr(mod, "main", None)
    if not callable(main_fn):
        return False, "assemble_docx has no callable `main`"
    try:
        rc = main_fn([str(manuscript_md), "--output", str(out_docx)])
    except SystemExit as exc:
        # argparse / main can call sys.exit; trap.
        rc = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        return False, f"assemble_docx.main raised: {exc!r}"

    if rc != 0:
        return False, f"assemble_docx.main exited rc={rc}"
    if not out_docx.is_file():
        return False, (
            "assemble_docx.main exited 0 but manuscript.docx was not "
            "written; check assemble_docx logs"
        )
    return True, f"reassembled docx at {out_docx}"


_AUTO_HANDLERS = {
    "reassemble": _reassemble,
    "rerun_validate": _rerun_validate_manuscript,
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _load_findings(draft_dir: Path) -> dict | None:
    """Read audit/deliverable_validation.json. None on missing/malformed."""
    path = draft_dir / "audit" / "deliverable_validation.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def finalize(draft_dir: Path) -> dict:
    """Apply auto-remediations from the first-pass findings; re-run
    validate; return a summary dict."""
    payload = _load_findings(draft_dir)
    if payload is None:
        return {
            "error": (
                "no audit/deliverable_validation.json found; "
                "run `validate_deliverable check <draft_dir>` first."
            ),
        }
    first_pass = payload.get("findings") or []

    # De-dup the auto-actions: rerun_validate first (so the next pass
    # has the fresh ValidationReport to project from), then reassemble.
    # Spec-mutating actions: none here — paper-writer's auto-handlers
    # are read-only by design (cycle-1 G1 lesson).
    requested_actions: list[str] = []
    for f in first_pass:
        rem = f.get("remediation") or {}
        if rem.get("kind") != "auto":
            continue
        action = rem.get("action")
        if action in _AUTO_HANDLERS and action not in requested_actions:
            requested_actions.append(action)

    # Order: rerun_validate before reassemble (so a fresh validation
    # report is available for any post-reassemble inspection; not
    # strictly required, but matches the natural data flow).
    ordered: list[str] = []
    for preferred in ("rerun_validate", "reassemble"):
        if preferred in requested_actions:
            ordered.append(preferred)

    actions_applied: list[dict] = []
    for action in ordered:
        handler = _AUTO_HANDLERS[action]
        ok, msg = handler(draft_dir)
        actions_applied.append({
            "action": action,
            "applied": bool(ok),
            "message": msg,
        })

    # Re-validate.
    import validate_deliverable as vd  # noqa: E402
    second_pass = vd.validate(draft_dir)
    vd.write_findings(draft_dir, second_pass)
    second_summary = vd._summarize(second_pass)

    # Surface targeted + advisory findings from the second pass — those
    # are what the operator still needs to act on.
    targeted_commands: list[dict] = []
    advisory_notes: list[dict] = []
    for f in second_pass:
        rem = f.remediation
        if rem.kind == "targeted":
            targeted_commands.append({
                "id": f.id,
                "gate": f.gate,
                "severity": f.severity,
                "command": rem.command,
                "note": rem.note,
            })
        elif rem.kind == "advisory":
            advisory_notes.append({
                "id": f.id,
                "gate": f.gate,
                "severity": f.severity,
                "message": f.message,
                "note": rem.note,
            })

    return {
        "actions_applied": actions_applied,
        "second_pass_summary": second_summary,
        "second_pass_readiness_rc": vd.readiness_exit_code(second_pass),
        "targeted_commands": targeted_commands,
        "advisory_notes": advisory_notes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_finalize(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).resolve()
    if not draft_dir.is_dir():
        print(
            f"finalize_deliverable: draft_dir not found: {draft_dir}",
            file=sys.stderr,
        )
        return 2

    result = finalize(draft_dir)
    if "error" in result:
        print(f"finalize_deliverable: {result['error']}", file=sys.stderr)
        return 2

    actions = result["actions_applied"]
    if actions:
        print(
            f"finalize_deliverable: auto-applied {len(actions)} "
            f"remediation action(s):",
            file=sys.stderr,
        )
        for a in actions:
            tag = "✓" if a["applied"] else "·"
            print(f"  {tag} {a['action']}: {a['message']}", file=sys.stderr)
    else:
        print(
            "finalize_deliverable: no auto-remediations requested.",
            file=sys.stderr,
        )

    s = result["second_pass_summary"]
    print(
        f"finalize_deliverable: post-remediation — total={s['total']}, "
        f"P0={s['by_severity'].get('P0', 0)}, "
        f"P1={s['by_severity'].get('P1', 0)}, "
        f"advisory={s['by_severity'].get('advisory', 0)}",
        file=sys.stderr,
    )

    if result["targeted_commands"]:
        print(
            f"finalize_deliverable: {len(result['targeted_commands'])} "
            f"targeted-remediation command(s) — these need operator action:",
            file=sys.stderr,
        )
        for c in result["targeted_commands"]:
            print(f"  [{c['severity']}] {c['gate']}: {c['note']}", file=sys.stderr)
            if c["command"]:
                print(f"    $ {c['command']}", file=sys.stderr)

    if result["advisory_notes"]:
        print(
            f"finalize_deliverable: {len(result['advisory_notes'])} "
            f"advisory finding(s):",
            file=sys.stderr,
        )
        for n in result["advisory_notes"]:
            print(f"  • {n['gate']}: {n['message']}", file=sys.stderr)

    return result["second_pass_readiness_rc"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="finalize_deliverable",
        description=(
            "Cycle 2 remediation half (paper-writer). Reads "
            "audit/deliverable_validation.json, applies auto-"
            "remediations (rerun_validate, reassemble — both pure-"
            "read on the manuscript), re-runs validation, surfaces "
            "targeted commands + advisories. Never re-runs the "
            "pipeline. Never mutates manuscript.md. Exit code = "
            "post-remediation readiness."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    p_fin = sub.add_parser("finalize", help="Apply auto-remediations + re-validate.")
    p_fin.add_argument("draft_dir")
    p_fin.set_defaults(func=_cmd_finalize)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
