"""Stage 7 v1-MVP — multi-project aggregator.

Collects metrics across the locked dev + holdout set (read from
project_set.tsv) and emits a single Markdown results table + per-set
pass-rate. The MVP success bar is 3/3 holdout pass; this script makes
the call.

Usage:
    python aggregate.py                # writes to results/stage7_results_<ts>.md
    python aggregate.py --stdout       # print to stdout instead
    python aggregate.py --set dev      # restrict to dev set
    python aggregate.py --set holdout  # restrict to holdout set

Assumes each project has had `run_project.sh <project_id>` executed
beforehand and a draft directory exists under
<beril_extended>/projects/<project_id>/papers/draft_N/. Auto-discovers
the highest-numbered draft per project.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import sys
from pathlib import Path
from typing import Optional

# Local imports (script lives next to collect_metrics.py).
HARNESS_DIR = Path(__file__).parent
sys.path.insert(0, str(HARNESS_DIR))

from collect_metrics import (  # noqa: E402
    PASS_CRITERIA,
    ProjectMetrics,
    autodiscover_draft_dir,
    collect,
)


PROJECT_SET_TSV = HARNESS_DIR / "project_set.tsv"


def load_project_set(set_filter: Optional[str] = None) -> list[dict[str, str]]:
    """Read the locked project_set.tsv. Optionally filter to one set."""
    if not PROJECT_SET_TSV.is_file():
        raise SystemExit(
            f"error: project_set.tsv not found at {PROJECT_SET_TSV}"
        )
    with PROJECT_SET_TSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)
    if set_filter:
        rows = [r for r in rows if r.get("set") == set_filter]
    return rows


def render_markdown(
    rows: list[tuple[dict[str, str], Optional[ProjectMetrics]]],
) -> str:
    """Build the full Markdown report from collected metrics."""
    lines: list[str] = []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append("# Stage 7 v1-MVP — multi-project validation results\n")
    lines.append(f"**Generated:** {now}\n")
    lines.append("**Success bar (locked 2026-05-18):**\n")
    lines.append(f"- reaches_assembled = {PASS_CRITERIA['reached_assembled']}")
    lines.append(
        f"- validators_pass_or_na >= "
        f"{PASS_CRITERIA['min_validators_pass_or_na']}"
    )
    lines.append(f"- p0_count <= {PASS_CRITERIA['max_p0_findings']}")
    lines.append(f"- cost_so_far_usd <= ${PASS_CRITERIA['max_cost_usd']:.2f}\n")

    # Render per set.
    for set_name in ("dev", "holdout"):
        subset = [(r, m) for (r, m) in rows if r.get("set") == set_name]
        if not subset:
            continue

        lines.append(f"## {set_name.capitalize()} set\n")

        # Headline pass-rate.
        with_metrics = [m for (_, m) in subset if m is not None]
        pass_count = sum(1 for m in with_metrics if m.crit_all)
        total = len(subset)
        not_run = total - len(with_metrics)
        lines.append(
            f"**Pass rate:** {pass_count} / {total}"
            + (f"  *({not_run} not yet run)*" if not_run else "")
            + "\n"
        )

        # Table.
        lines.append(
            "| project | shape | tier_est | "
            "phase | val (pass+NA / total) | P0 | cost | "
            "cycles | silent | overall |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|---|"
        )
        for r, m in subset:
            if m is None:
                lines.append(
                    f"| {r['project_id']} | {r.get('shape','')} | "
                    f"{r.get('tier_estimate','')} | "
                    f"_not run_ | — | — | — | — | — | — |"
                )
                continue
            overall = "**PASS**" if m.crit_all else "**FAIL**"
            phase_marker = (
                f"`{m.final_phase}`"
                + ("" if m.reached_assembled else " ⚠")
            )
            val_str = (
                f"{m.validators_pass_or_na}/{m.validators_total}"
                + ("" if m.crit_validators_ok else " ⚠")
            )
            p0_str = f"{m.p0_total}" + ("" if m.crit_p0_ok else " ⚠")
            cost_str = (
                f"${m.cost_so_far_usd:.2f}"
                + ("" if m.crit_cost_ok else " ⚠")
            )
            silent_str = str(m.silent_failures) + (
                "" if m.silent_failures == 0 else " ⚠"
            )
            lines.append(
                f"| {r['project_id']} | {r.get('shape','')} | "
                f"{r.get('tier_estimate','')} | "
                f"{phase_marker} | {val_str} | {p0_str} | "
                f"{cost_str} | {m.remediation_cycles_used} | "
                f"{silent_str} | {overall} |"
            )
        lines.append("")

        # Per-project failure notes — only for FAIL rows.
        fail_rows = [(r, m) for (r, m) in subset if m and not m.crit_all]
        if fail_rows:
            lines.append(f"### {set_name.capitalize()} set — failure details\n")
            for r, m in fail_rows:
                lines.append(f"**{r['project_id']}**")
                lines.append("")
                bullets = []
                if not m.crit_reached_assembled:
                    bullets.append(
                        f"- Did not reach assembled (phase=`{m.final_phase}`)"
                    )
                if not m.crit_validators_ok:
                    bullets.append(
                        f"- Validators: {m.validators_pass_or_na}/"
                        f"{m.validators_total} pass+NA "
                        f"(need ≥ {PASS_CRITERIA['min_validators_pass_or_na']}); "
                        f"breakdown {m.validator_breakdown}"
                    )
                if not m.crit_p0_ok:
                    bullets.append(
                        f"- P0 findings: {m.p0_total} "
                        f"(need ≤ {PASS_CRITERIA['max_p0_findings']}); "
                        f"by class {m.p0_by_class}"
                    )
                if not m.crit_cost_ok:
                    bullets.append(
                        f"- Cost: ${m.cost_so_far_usd:.2f} "
                        f"(need ≤ ${PASS_CRITERIA['max_cost_usd']:.2f})"
                    )
                if m.silent_failures > 0:
                    bullets.append(
                        f"- Tier S-9a silent-failure observed "
                        f"({m.silent_failures} occurrence(s))"
                    )
                for n in m.notes:
                    bullets.append(f"- Note: {n}")
                for b in bullets:
                    lines.append(b)
                lines.append("")

    # Final v1 ship/no-ship call (holdout-driven).
    holdout_subset = [(r, m) for (r, m) in rows if r.get("set") == "holdout"]
    holdout_metrics = [m for (_, m) in holdout_subset if m is not None]
    holdout_passes = sum(1 for m in holdout_metrics if m.crit_all)
    if len(holdout_metrics) == len(holdout_subset) and holdout_subset:
        # All holdout runs collected.
        lines.append("## v1 ship decision\n")
        if holdout_passes == len(holdout_subset):
            lines.append(
                f"**SHIP.** Holdout {holdout_passes}/{len(holdout_subset)} "
                "passed — v1-MVP success bar met.\n"
            )
        elif holdout_passes >= 2:
            lines.append(
                f"**HOLD — post-mortem required.** Holdout "
                f"{holdout_passes}/{len(holdout_subset)} passed. "
                "Diagnose the single failure; ship vs defer is an "
                "Adam-decision based on whether failure is "
                "operator-actionable or hard.\n"
            )
        else:
            lines.append(
                f"**DON'T SHIP v1.** Holdout {holdout_passes}/"
                f"{len(holdout_subset)} passed. Need another dev cycle "
                "or deeper architectural look.\n"
            )

    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--set",
        choices=["dev", "holdout"],
        default=None,
        help="Restrict to one set. Default: both.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of writing to results/.",
    )
    args = parser.parse_args(argv)

    rows = load_project_set(set_filter=args.set)
    if not rows:
        print(
            f"error: no projects matched set filter {args.set!r}",
            file=sys.stderr,
        )
        return 1

    # Collect per-project metrics.
    collected: list[tuple[dict[str, str], Optional[ProjectMetrics]]] = []
    for r in rows:
        pid = r["project_id"]
        draft = autodiscover_draft_dir(pid)
        if draft is None:
            collected.append((r, None))
            continue
        try:
            m = collect(draft, project_id=pid)
            collected.append((r, m))
        except Exception as exc:
            print(
                f"warning: collect failed for {pid}: {exc!r}",
                file=sys.stderr,
            )
            collected.append((r, None))

    out = render_markdown(collected)

    if args.stdout:
        print(out)
    else:
        results_dir = HARNESS_DIR / "results"
        results_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H-%M-%SZ"
        )
        out_path = results_dir / f"stage7_results_{ts}.md"
        out_path.write_text(out, encoding="utf-8")
        print(f"results written to {out_path}", file=sys.stderr)

    # Exit 0 if everything passed, 2 if any FAILed, 1 if anything not-run.
    has_not_run = any(m is None for (_, m) in collected)
    has_fail = any(m is not None and not m.crit_all for (_, m) in collected)
    if has_not_run:
        return 1
    if has_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
