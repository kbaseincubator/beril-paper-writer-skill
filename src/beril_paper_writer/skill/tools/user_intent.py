#!/usr/bin/env python3
"""user_intent.py — DP9b fix + Cycle 1 Gate-4 substrate.

Persist the user-selected run intent (mode / tier / audience and the
sentinel that says "the user explicitly picked this vs. inheriting a
default") at the EARLIEST point in the pipeline so downstream stages
+ the deliverable validator can compare actual artifacts against the
user's stated intent rather than against each other.

Why this exists
---------------
The v1.1.1 hotfix added a resume-time mode-recovery hook that reads
`working/slide_spec.json`'s `mode` field. That fix closed the
`continue --resume-from image_gen` mode drop (the resume path most
operators were hitting) but DID NOT close the more fundamental gap:
the **draft path itself** loses mode across the throughline-pick
handoff.

Sequence (caulobacter run, 2026-06-07):

  1. Operator runs `draft --mode talk-45 <proj>`.
     Shell parses MODE=talk-45 → stages 1-2 run at talk-45 → halt at
     throughline-pick gate → shell exits 0. Process ends. MODE=talk-45
     is lost.
  2. Operator runs `continue <draft_dir> --pick TL2` (no --mode).
     `continue_run.py` invokes the shell with `--resume-from
     substory_design` and forwards `--mode` ONLY if the operator
     re-passed it. They didn't. Shell defaults MODE=talk-30.
  3. The v1.1.1 hook tries to recover MODE from
     `working/slide_spec.json` — but slide_spec.json is written by
     `stage_merge_and_assemble`, which hasn't run yet on this resume
     path. So the hook is a no-op; MODE stays talk-30.
  4. `merge_compose_fragments.py --mode talk-30` writes
     `slide_spec.json` with `mode: talk-30`. Every downstream
     artifact agrees on talk-30. The v1.1.1 cross-artifact
     consistency assertion passes (they all agree — uniformly wrong).

The two-process flow needs a persistence layer that:
  - is written the moment MODE is first parsed (process 1, before
    the halt-and-handoff exit),
  - is read by the resume hook BEFORE slide_spec.json exists (process
    2, on resume),
  - is the source of truth the validator compares spec/decisions/qa
    against (Gate 4 — "the mode the user actually selected").

CLI surface
-----------
  user_intent.py write <draft_dir> --mode <m> --tier <t> --audience <a>
                                   --mode-explicit {0|1}
                                   --tier-explicit {0|1}
                                   --audience-explicit {0|1}
      Idempotent merge: if user_intent.json already exists and a
      field was marked explicit there, the existing value wins (the
      original choice from process 1 outranks process 2's defaults).
      If the new call ALSO marks the same field explicit and the
      values disagree, fail loud with exit 1 — a user mode-flip on
      resume is almost always a typo (mirrors the v1.1.1 hotfix
      policy on mode flips).

  user_intent.py read <draft_dir> --field {mode|tier|audience}
                                   [--fallback <str>]
      Print the persisted value of <field>. On missing file / missing
      field / unparseable, print the fallback (default: empty
      string). Exit 0 regardless — readers handle empty-string the
      way they handle missing.

Schema: `audit/user_intent.json`, `user-intent.v1`. Lives at a stable
path readable from BOTH the bash orchestrator (via subprocess) and
the validator (via direct import + Path).

  {
    "schema_version": "user-intent.v1",
    "mode": "talk-45",
    "mode_explicit": true,
    "tier": "STRONG",
    "tier_explicit": true,
    "audience": "peer",
    "audience_explicit": false,
    "written_at": "2026-06-07T15:00:00Z"
  }
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "user-intent.v1"

# Mirror of slide_spec.MODES + the per-field choice sets. Duplicated
# here to keep this helper free of cross-module imports (called as
# bare `python3 user_intent.py …` from bash).
MODES: tuple[str, ...] = (
    "talk-15", "talk-30", "talk-45", "lightning-5",
    "poster-h", "poster-v", "paper",
)
TIERS: tuple[str, ...] = ("STRONG", "THIN", "EXPLORATORY")
AUDIENCES: tuple[str, ...] = ("peer", "general", "expert")

FIELDS: tuple[str, ...] = ("mode", "tier", "audience")
ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "mode": MODES,
    "tier": TIERS,
    "audience": AUDIENCES,
}


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def user_intent_path(draft_dir: Path) -> Path:
    """Stable location: `<draft_dir>/audit/user_intent.json`. The
    `audit/` zone is where per-run provenance lives per the 4-zone
    layout; user_intent is conceptually a run-provenance record."""
    return Path(draft_dir) / "audit" / "user_intent.json"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def read_user_intent(draft_dir: Path) -> dict | None:
    """Return the parsed user_intent payload (a dict), or None on
    any failure (missing file, parse error, wrong shape). Callers
    treat None as 'no persisted intent — use defaults'."""
    path = user_intent_path(draft_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_field(draft_dir: Path, field: str) -> str | None:
    """Return the persisted value of `field` (mode|tier|audience),
    validating against the allowed-value set. None on any failure."""
    if field not in FIELDS:
        return None
    data = read_user_intent(draft_dir)
    if data is None:
        return None
    value = data.get(field)
    if not isinstance(value, str) or value not in ALLOWED_VALUES[field]:
        return None
    return value


def field_was_explicit(draft_dir: Path, field: str) -> bool:
    """True iff `<field>_explicit` was recorded True in user_intent.json.
    Used by the resume hook to distinguish "user picked X" from
    "shell defaulted to X" — only the former blocks a mode-flip."""
    if field not in FIELDS:
        return False
    data = read_user_intent(draft_dir)
    if data is None:
        return False
    return bool(data.get(f"{field}_explicit"))


def write_user_intent(
    draft_dir: Path,
    *,
    mode: str,
    tier: str,
    audience: str,
    mode_explicit: bool,
    tier_explicit: bool,
    audience_explicit: bool,
    now: str | None = None,
) -> tuple[Path, list[str]]:
    """Idempotent merge-write of user_intent.json.

    Returns (path, conflicts). `conflicts` is a list of "field: old=X
    new=Y" strings if an existing-explicit field would be silently
    overwritten by a new-explicit-and-different value. Caller decides
    whether to fail loud on conflicts (the bash orchestrator does on
    a mode-flip; see write_cmd).

    Merge rules (per-field, applied independently):
      1. New value marked explicit AND old value marked explicit AND
         they differ → CONFLICT (recorded in `conflicts`, NOT written).
      2. New value marked explicit AND (old absent OR old not explicit)
         → write new, mark explicit.
      3. New value NOT marked explicit AND old absent → write new,
         mark not-explicit.
      4. New value NOT marked explicit AND old present (either flavor)
         → KEEP old (the original choice wins over a later default).

    This implements "first-pass user intent persists; later-process
    defaults never overwrite it."
    """
    path = user_intent_path(draft_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_user_intent(draft_dir) or {}

    inputs = {
        "mode": (mode, mode_explicit),
        "tier": (tier, tier_explicit),
        "audience": (audience, audience_explicit),
    }
    conflicts: list[str] = []
    merged: dict = {"schema_version": SCHEMA_VERSION}

    for field, (new_val, new_explicit) in inputs.items():
        old_val = existing.get(field)
        old_explicit = bool(existing.get(f"{field}_explicit"))

        if new_explicit and old_explicit and old_val != new_val:
            conflicts.append(
                f"{field}: old={old_val!r} (explicit) -> "
                f"new={new_val!r} (explicit)"
            )
            # Keep the old explicit value when writing back; the
            # caller decides whether to abort on conflicts.
            merged[field] = old_val
            merged[f"{field}_explicit"] = True
        elif new_explicit:
            merged[field] = new_val
            merged[f"{field}_explicit"] = True
        elif old_val is not None:
            merged[field] = old_val
            merged[f"{field}_explicit"] = old_explicit
        else:
            merged[field] = new_val
            merged[f"{field}_explicit"] = False

    merged["written_at"] = now or _utc_iso_now()
    path.write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path, conflicts


def _utc_iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _bool01(s: str) -> bool:
    if s in ("0", "false", "False"):
        return False
    if s in ("1", "true", "True"):
        return True
    raise argparse.ArgumentTypeError(
        f"expected 0|1|true|false, got {s!r}"
    )


def _cmd_write(args: argparse.Namespace) -> int:
    for field, value in (("mode", args.mode), ("tier", args.tier),
                         ("audience", args.audience)):
        if value not in ALLOWED_VALUES[field]:
            print(
                f"user_intent: --{field} {value!r} not in "
                f"{ALLOWED_VALUES[field]}",
                file=sys.stderr,
            )
            return 2

    path, conflicts = write_user_intent(
        Path(args.draft_dir).resolve(),
        mode=args.mode, tier=args.tier, audience=args.audience,
        mode_explicit=args.mode_explicit,
        tier_explicit=args.tier_explicit,
        audience_explicit=args.audience_explicit,
    )
    if conflicts:
        print(
            f"user_intent: CONFLICT — {len(conflicts)} field(s) where "
            f"the existing explicit value disagrees with the new "
            f"explicit value (kept the existing value; aborting):",
            file=sys.stderr,
        )
        for c in conflicts:
            print(f"  - {c}", file=sys.stderr)
        print(
            "  This usually means a `continue` invocation re-passed "
            "an option with a different value than the original draft "
            "run. To switch intent, start a new draft. To resume with "
            "the original intent, omit the conflicting option.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    value = read_field(Path(args.draft_dir).resolve(), args.field)
    print(value if value is not None else args.fallback)
    return 0


def _cmd_explicit(args: argparse.Namespace) -> int:
    """Print '1' if <field> was marked explicit, else '0'. Exit 0
    regardless — readers grep the output."""
    is_explicit = field_was_explicit(
        Path(args.draft_dir).resolve(), args.field,
    )
    print("1" if is_explicit else "0")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="user_intent",
        description=(
            "Persist + read the user-selected run intent "
            "(mode/tier/audience). DP9b fix + Cycle 1 Gate-4 substrate."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_w = sub.add_parser("write", help="Write/merge user_intent.json.")
    p_w.add_argument("draft_dir")
    p_w.add_argument("--mode", required=True)
    p_w.add_argument("--tier", required=True)
    p_w.add_argument("--audience", required=True)
    p_w.add_argument("--mode-explicit", type=_bool01, required=True)
    p_w.add_argument("--tier-explicit", type=_bool01, required=True)
    p_w.add_argument("--audience-explicit", type=_bool01, required=True)
    p_w.set_defaults(func=_cmd_write)

    p_r = sub.add_parser(
        "read",
        help="Print the persisted value of <field> (or --fallback).",
    )
    p_r.add_argument("draft_dir")
    p_r.add_argument("--field", required=True, choices=list(FIELDS))
    p_r.add_argument(
        "--fallback", default="",
        help="String to print when the field can't be resolved.",
    )
    p_r.set_defaults(func=_cmd_read)

    p_e = sub.add_parser(
        "explicit",
        help="Print '1' if <field> was marked explicit, else '0'.",
    )
    p_e.add_argument("draft_dir")
    p_e.add_argument("--field", required=True, choices=list(FIELDS))
    p_e.set_defaults(func=_cmd_explicit)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
