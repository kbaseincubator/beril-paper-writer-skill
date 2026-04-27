#!/usr/bin/env python3
"""check_repair_scope.py — REPAIR_MODE scope-violation post-checker (advisory).

Standalone script invoked by the shell orchestrator immediately after a
REPAIR_MODE dispatch succeeds (named validator now passes). It is the
fourth post-processor in the architectural pattern that started with
`check_throughline_glyphs.py` (Tier 0) and grew through
`check_scope_coherence.py` and `check_overclaim.py` (Tier 1).

    python3 "$SKILL_DIR/tools/check_repair_scope.py" \\
        --pre /audit/repair_M9_pre.md \\
        --post /draft_dir/03_discussion.md \\
        --validator M9 \\
        --draft-dir /path/to/draft_dir

Why this exists. REPAIR_MODE asks the section prompt to fix only the
named span — not regenerate the section, not introduce new claims, not
delete grounded claims that other validators didn't flag. Prompt-level
discipline alone cannot guarantee this; the architectural lesson at
`feedback_prompt_discipline_needs_post_check.md` says: back cross-walk
discipline with a programmatic post-processor. This script is that
backstop for the REPAIR_MODE contract.

Three checks performed:

1. **Write actually invoked.** The orchestrator's `invoke_claude_with_retry`
   uses `stream_progress.py` to verify the Write tool fired; that's the
   primary check. As a defensive secondary check, we compare the pre-
   snapshot to the post file: identical content means Write was a no-op
   (e.g., the prompt declared a fix without actually editing). WARN.

2. **Bounded diff (over-eager regeneration).** Compute line-level diff
   between pre and post. If the change rate is high (added_lines +
   removed_lines exceeds ~30% of the pre-snapshot's total lines), the
   prompt likely regenerated more than the named span. WARN with the
   diff stats. NOTE for moderate change rates (10-30%) — these can be
   legitimate when the named span is large (e.g., M9 expanding a stub
   Limitations subsection).

3. **Validator regression.** Compare pre-repair validation.json against
   post-repair validation.json (the file the orchestrator wrote after
   this repair attempt). Any validator that was passing and is now
   failing is a regression caused by this repair → WARN.

All output is stderr WARN/NOTE/summary lines; exit code is always 0
(advisory). The orchestrator surfaces warnings via
`audit/repair_<VID>_scope_warnings.txt` → next_actions.md.

Importable for unit testing — pure helpers (line-diff metrics,
validator-regression diff) don't depend on the CLI.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Diff thresholds. Calibrated for "named-span repair" semantics: a typical
# M9 (Limitations expansion) adds 5-50 lines to a 200-line section, so
# ~25% change rate is the legitimate ceiling. Above 40% is almost always
# regeneration.
DIFF_NOTE_THRESHOLD = 0.10   # below = silent
DIFF_WARN_THRESHOLD = 0.30   # above = WARN
DIFF_HARD_THRESHOLD = 0.60   # above = WARN with stronger language


# ---------------------------------------------------------------------------
# Pure helpers (importable for tests)
# ---------------------------------------------------------------------------


def diff_metrics(pre_text: str, post_text: str) -> dict:
    """Compute line-level diff metrics between two texts.

    Returns a dict with: pre_lines, post_lines, added, removed, common,
    change_rate, identical (bool).
    """
    pre_lines = pre_text.splitlines()
    post_lines = post_text.splitlines()
    if pre_text == post_text:
        return {
            "pre_lines": len(pre_lines),
            "post_lines": len(post_lines),
            "added": 0,
            "removed": 0,
            "common": len(pre_lines),
            "change_rate": 0.0,
            "identical": True,
        }

    sm = difflib.SequenceMatcher(a=pre_lines, b=post_lines, autojunk=False)
    added = 0
    removed = 0
    common = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a_block = i2 - i1
        b_block = j2 - j1
        if tag == "equal":
            common += a_block
        elif tag == "delete":
            removed += a_block
        elif tag == "insert":
            added += b_block
        elif tag == "replace":
            removed += a_block
            added += b_block

    base = max(len(pre_lines), 1)  # avoid div-zero on empty pre
    change_rate = (added + removed) / base
    return {
        "pre_lines": len(pre_lines),
        "post_lines": len(post_lines),
        "added": added,
        "removed": removed,
        "common": common,
        "change_rate": change_rate,
        "identical": False,
    }


def validator_regressions(
    pre_validation: dict, post_validation: dict
) -> list[dict]:
    """Compare two ValidationReport dicts. Return entries that flipped
    pass→fail across the repair invocation.

    Each returned entry: {id, name, pre_status, post_status,
    new_violation_count, sample_message}.
    """
    pre_index = {e.get("id"): e for e in pre_validation.get("validators", [])}
    post_index = {e.get("id"): e for e in post_validation.get("validators", [])}
    regressions: list[dict] = []
    for vid, post_entry in post_index.items():
        pre_entry = pre_index.get(vid)
        if pre_entry is None:
            continue
        pre_status = pre_entry.get("status")
        post_status = post_entry.get("status")
        if pre_status == "pass" and post_status == "fail":
            violations = post_entry.get("violations", [])
            sample = ""
            if violations:
                sample = violations[0].get("message", "")
                if len(sample) > 140:
                    sample = sample[:137] + "..."
            regressions.append({
                "id": vid,
                "name": post_entry.get("name", ""),
                "pre_status": pre_status,
                "post_status": post_status,
                "new_violation_count": len(violations),
                "sample_message": sample,
            })
    return regressions


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


def check(
    pre_path: Path,
    post_path: Path,
    validator: str,
    draft_dir: Path,
    *,
    verbose: bool = True,
) -> int:
    """Run the three checks; emit warnings to stderr. Returns WARN count."""
    n_warnings = 0

    # Read pre/post files.
    if not pre_path.is_file():
        print(
            f"[check_repair_scope] WARN [{validator}] "
            f"pre-repair snapshot missing at {pre_path}; cannot diff",
            file=sys.stderr,
        )
        return 1
    if not post_path.is_file():
        print(
            f"[check_repair_scope] WARN [{validator}] "
            f"post-repair file missing at {post_path}; the repair may have erased it",
            file=sys.stderr,
        )
        return 1

    pre_text = pre_path.read_text(encoding="utf-8")
    post_text = post_path.read_text(encoding="utf-8")

    # ----- Check 1: Write invoked? -----
    metrics = diff_metrics(pre_text, post_text)
    if metrics["identical"]:
        print(
            f"[check_repair_scope] WARN [{validator}] "
            f"pre and post are byte-identical — Write tool was a no-op or "
            f"prompt declared success without editing. "
            f"Validator passed by other means (re-validation noise?) — review.",
            file=sys.stderr,
        )
        n_warnings += 1
    elif verbose:
        print(
            f"[check_repair_scope] [{validator}] diff: "
            f"pre={metrics['pre_lines']}L post={metrics['post_lines']}L "
            f"+{metrics['added']} -{metrics['removed']} "
            f"({metrics['change_rate']:.1%} change rate)",
            file=sys.stderr,
        )

    # ----- Check 2: bounded diff -----
    rate = metrics["change_rate"]
    if not metrics["identical"]:
        if rate >= DIFF_HARD_THRESHOLD:
            print(
                f"[check_repair_scope] WARN [{validator}] "
                f"change rate {rate:.1%} (≥{DIFF_HARD_THRESHOLD:.0%}) suggests "
                f"the repair regenerated the section. REPAIR_MODE contract: "
                f"fix only the named span. Review the diff: "
                f"+{metrics['added']} / -{metrics['removed']} lines on a "
                f"{metrics['pre_lines']}-line section.",
                file=sys.stderr,
            )
            n_warnings += 1
        elif rate >= DIFF_WARN_THRESHOLD:
            print(
                f"[check_repair_scope] WARN [{validator}] "
                f"change rate {rate:.1%} (≥{DIFF_WARN_THRESHOLD:.0%}); "
                f"large diff for a scoped repair. Confirm the change is "
                f"actually scoped to the named violation: "
                f"+{metrics['added']} / -{metrics['removed']} lines on a "
                f"{metrics['pre_lines']}-line section.",
                file=sys.stderr,
            )
            n_warnings += 1
        elif rate >= DIFF_NOTE_THRESHOLD and verbose:
            print(
                f"[check_repair_scope] NOTE [{validator}] "
                f"change rate {rate:.1%}; moderate diff (legitimate when the "
                f"named span is itself a substantial fraction of the section, "
                f"e.g., M9 stub Limitations expansion).",
                file=sys.stderr,
            )

    # ----- Check 3: validator regression -----
    pre_val_path = draft_dir / "audit" / "validation.json"
    post_val_path = draft_dir / "audit" / f"repair_{validator}_post_validation.json"
    if pre_val_path.is_file() and post_val_path.is_file():
        try:
            pre_val = json.loads(pre_val_path.read_text(encoding="utf-8"))
            post_val = json.loads(post_val_path.read_text(encoding="utf-8"))
            regressions = validator_regressions(pre_val, post_val)
            if regressions:
                for r in regressions:
                    print(
                        f"[check_repair_scope] WARN [{validator}] "
                        f"regression: {r['id']} ({r['name']}) flipped "
                        f"pass → fail; {r['new_violation_count']} new "
                        f"violation(s); sample: {r['sample_message']}",
                        file=sys.stderr,
                    )
                    n_warnings += 1
            elif verbose:
                print(
                    f"[check_repair_scope] [{validator}] "
                    f"no validator regressions detected.",
                    file=sys.stderr,
                )
        except json.JSONDecodeError as e:
            print(
                f"[check_repair_scope] NOTE [{validator}] "
                f"validation.json parse error during regression check: {e}",
                file=sys.stderr,
            )
    elif verbose:
        msg = []
        if not pre_val_path.is_file():
            msg.append(f"pre missing at {pre_val_path}")
        if not post_val_path.is_file():
            msg.append(f"post missing at {post_val_path}")
        print(
            f"[check_repair_scope] NOTE [{validator}] "
            f"regression check skipped — {'; '.join(msg)}",
            file=sys.stderr,
        )

    return n_warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--pre", type=Path, required=True,
                    help="Pre-repair snapshot file (audit/repair_<VID>_pre.md).")
    ap.add_argument("--post", type=Path, required=True,
                    help="Post-repair section file (e.g. <DRAFT_DIR>/03_discussion.md).")
    ap.add_argument("--validator", required=True,
                    help="Named validator (M1..M10) — for diagnostic context.")
    ap.add_argument("--draft-dir", type=Path, required=True,
                    help="Draft directory; used to find pre/post validation.json.")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress NOTE / per-check summary lines.")
    args = ap.parse_args(argv)

    if not args.draft_dir.is_dir():
        print(
            f"[check_repair_scope] WARN missing draft_dir: {args.draft_dir}",
            file=sys.stderr,
        )

    n = check(
        args.pre, args.post, args.validator, args.draft_dir,
        verbose=not args.quiet,
    )
    print(
        f"[check_repair_scope] complete: {n} warning(s) on {args.validator}.",
        file=sys.stderr,
    )
    # Always 0 — advisory.
    return 0


if __name__ == "__main__":
    sys.exit(main())
