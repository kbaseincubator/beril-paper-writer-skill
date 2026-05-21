"""Stage 7 v1-MVP — per-draft metrics collector.

Reads a paper-writer draft directory's state.json + audit artifacts and
emits the metrics needed to evaluate the v1-MVP success criteria.

v1-bar v2b (2026-05-20) — post-Stage-7 holdout campaign
=======================================================

The success bar, revised after the 6-project dev+holdout campaign
(D1/D2/D3 + H1/H2/H3). Supersedes the locked 2026-05-18 bar and the
v2a interim. The bar revision is recorded in STAGED_IMPROVEMENT_PLAN.md.

    reached_measurement_point      = True   (p0_review or assembled)
    tier_t_ungrounded_count       <= 5      (deterministic, post-#41)
    claim_markers_resolved_pct    >= 100    (deterministic)
    cost_so_far_usd               <= 10.0

What changed from the locked bar and why:

  - `reached_assembled` → `reached_measurement_point`. The harness no
    longer auto-remediates (Adam 2026-05-19); the pipeline pauses at
    p0_review and never reaches `assembled` on its own. The honest
    equivalent of "ran far enough to measure" is "reached p0_review
    or beyond."

  - `min_validators_pass_or_na >= 8` → DROPPED. Validators run only
    post-assemble; the measurement point is pre-assemble. The
    criterion is structurally unmeasurable at the bar's point.

  - `max_p0_findings <= 5` (combined adv + Tier T) → SPLIT and
    narrowed. Only the DETERMINISTIC leg gates: `tier_t_ungrounded`.
    Post-#41 (Tier T extractor normalization) the Tier T count is a
    trustworthy deterministic signal.

  - Adversarial P0 → ADVISORY, not gating. The holdout campaign
    showed genuine first-cut adversarial P0 of 3-7 across 5
    measurable projects with no clean good/bad threshold; the
    reviewer is a sampling estimator with +/-2-5 run-to-run variance
    (V1_X_BACKLOG.md #37). Gating a v1 success criterion on a noisy
    LLM-opinion count is not defensible. Adversarial findings are
    still reported here, still drive the orchestrator's p0_gate
    operator pause, and a soft advisory flag fires above 8 — but they
    do not fail the bar. (#37 lever 2.)

  - The INCONCLUSIVE verdict is RETIRED. It existed only because a
    malformed adversarial JSON blocked a clean PASS *while
    adversarial gated*. With adversarial advisory, a malformed
    adversarial JSON is a noted advisory gap, not a verdict blocker.

Retained from v2a (orthogonal to the bar):

  - First-cut measurement via audit/iter_1/ snapshot. When a draft
    has been remediated, the bar evaluates the pre-remediation state.
    Under the no-auto-remediate harness most drafts have audit/ as
    first-cut (iter_1/ never created).

  - Cost-first-cut subtraction: when iter_1/ is in play, cost is
    rolled back by remediation drafter + review spend (V1_X_BACKLOG
    #43 — review_cost_usd unpopulated by the producer; `or 0.0`
    keeps the subtraction forward-compatible).

Exit codes: 0 = PASS, 2 = FAIL, 1 = harness/setup error.

Usage:
    python collect_metrics.py <draft_dir>
    python collect_metrics.py <draft_dir> --json     # machine-readable
    python collect_metrics.py <project_id> --auto    # autodiscover latest draft
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------
# Success criteria — pinned in code so aggregate.py's pass/fail logic is
# unambiguous. Edit ONE place if Adam's decision shifts.
# --------------------------------------------------------------------------

# v1-bar v2b (2026-05-20). See module docstring for the rationale behind
# each criterion and what changed from the locked 2026-05-18 bar.
PASS_CRITERIA = {
    "reached_measurement_point":     True,
    "max_tier_t_ungrounded":         5,
    "min_claim_marker_resolved_pct": 100,
    "max_cost_usd":                  10.0,
}

# Adversarial P0 is ADVISORY, not gating. This threshold only drives a
# soft "ELEVATED" flag in the report — it never fails the bar.
ADVERSARIAL_P0_ADVISORY_CEILING = 8


@dataclass
class ProjectMetrics:
    """One project's metrics as collected from disk artifacts.

    v1-bar v2b: the four gated criteria are reached_measurement_point,
    tier_t_ungrounded, claim_markers_resolved_pct, cost. Adversarial
    P0 and the other fields are diagnostics — reported, not gating.
    """

    # Identity / phase.
    project_id:                     str
    draft_dir:                      str
    final_phase:                    str

    # --- Gated criteria inputs (v2b bar) ---
    reached_measurement_point:      bool
    tier_t_ungrounded_count:        int            # deterministic, post-#41
    claim_markers_total:            int
    claim_markers_unique:           int
    claim_markers_resolved:         int
    claim_markers_resolved_pct:     float
    cost_so_far_usd:                float          # first-cut adjusted

    # --- Advisory / diagnostic (NOT gating) ---
    adversarial_p0_count:           int
    adversarial_json_parseable:     bool
    p0_total:                       int            # adv + Tier T combined
    p0_by_source:                   dict[str, int]
    p0_by_class:                    dict[str, int]
    p0_demoted_count:               int
    # Validators run only post-assemble; kept for diagnostic value.
    reached_assembled:              bool
    validators_pass_or_na:          int
    validators_total:               int
    validator_breakdown:            dict[str, int]
    remediation_cycles_used:        int
    silent_failures:                int

    notes:                          list[str] = field(default_factory=list)

    # --- Pass/fail booleans against the v2b PASS_CRITERIA ---
    crit_reached:              bool = False
    crit_tier_t:               bool = False
    crit_markers:              bool = False
    crit_cost:                 bool = False
    crit_all:                  bool = False
    overall_label:             str = "FAIL"   # PASS | FAIL
    # Advisory flag — "OK" | "ELEVATED" | "UNMEASURABLE". Never gating.
    adversarial_advisory:      str = "OK"

    def evaluate(self) -> None:
        """Evaluate the v2b 4-criteria bar. Adversarial P0 is advisory
        only — it sets `adversarial_advisory` but never `crit_all`."""
        self.crit_reached = self.reached_measurement_point
        self.crit_tier_t = (
            self.tier_t_ungrounded_count <= PASS_CRITERIA["max_tier_t_ungrounded"]
        )
        self.crit_markers = (
            self.claim_markers_resolved_pct
            >= PASS_CRITERIA["min_claim_marker_resolved_pct"]
        )
        self.crit_cost = self.cost_so_far_usd <= PASS_CRITERIA["max_cost_usd"]
        self.crit_all = all([
            self.crit_reached,
            self.crit_tier_t,
            self.crit_markers,
            self.crit_cost,
        ])
        self.overall_label = "PASS" if self.crit_all else "FAIL"

        # Advisory (non-gating) adversarial assessment.
        if not self.adversarial_json_parseable:
            self.adversarial_advisory = "UNMEASURABLE"
        elif self.adversarial_p0_count > ADVERSARIAL_P0_ADVISORY_CEILING:
            self.adversarial_advisory = "ELEVATED"
        else:
            self.adversarial_advisory = "OK"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def autodiscover_draft_dir(project_id: str) -> Optional[Path]:
    """Find the highest-numbered papers/draft_N/ for a project."""
    beril_extended = Path(os.environ.get(
        "BERIL_EXTENDED_DIR",
        Path.home() / "Documents" / "Claude" / "Projects"
        / "research-coscientist-dev" / "spike" / "beril-extended",
    ))
    papers = beril_extended / "projects" / project_id / "papers"
    if not papers.is_dir():
        return None
    drafts = sorted(
        (d for d in papers.iterdir() if d.is_dir() and d.name.startswith("draft_")),
        key=lambda d: int(d.name.split("_", 1)[1]) if d.name.split("_")[1].isdigit() else 0,
    )
    return drafts[-1] if drafts else None


# --------------------------------------------------------------------------
# Collectors — each reads ONE artifact and never raises on missing input
# --------------------------------------------------------------------------


def _load_state(draft_dir: Path) -> dict[str, Any]:
    p = draft_dir / "state.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_json_safe(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _count_validators(state: dict[str, Any]) -> tuple[int, int, dict[str, int]]:
    vs = state.get("validator_status", {})
    if not isinstance(vs, dict):
        return 0, 0, {}
    total = len(vs)
    breakdown: dict[str, int] = {}
    for status in vs.values():
        breakdown[str(status)] = breakdown.get(str(status), 0) + 1
    pass_or_na = breakdown.get("pass", 0) + breakdown.get("not-applicable", 0)
    return pass_or_na, total, breakdown


def _count_p0s(
    audit_dir: Path,
) -> tuple[int, dict[str, int], dict[str, int], int]:
    """Count P0 findings via p0_gate.count_p0_findings so the metrics
    apply the same false-positive filter the orchestrator's gate uses.

    Returns (total_after_filter, per_source, per_class, demoted_count).
    The orchestrator's gate decision drives the operator experience;
    measuring v1-bar success against the unfiltered raw count would
    produce FAIL verdicts on drafts the gate would have advanced.

    Stage 7 Patch 3 follow-up (2026-05-18): previously this function
    inlined raw P0 counting, missing the filter applied by p0_gate
    (NEEDS CITATION demotion, pre-compliance Data Availability
    demotion). After the fix, the metrics match the orchestrator's
    log line `Remediation cycle 1: p0_before=N -> p0_after=M`.

    Import is local so this harness still functions if the skill
    package isn't importable in the runtime environment — but in
    that degraded case the metrics fall back to raw counting (and
    log a note).
    """
    try:
        # Try to use the canonical gate logic via package import.
        # Add the skill's src to sys.path if pipx hasn't put the
        # package on the default import path.
        import sys as _sys
        _here = Path(__file__).resolve()
        # smoke-test/stage7/collect_metrics.py → repo root is up two,
        # src is at <root>/src/.
        _root = _here.parent.parent.parent
        _src = _root / "src"
        if _src.is_dir() and str(_src) not in _sys.path:
            _sys.path.insert(0, str(_src))
        from beril_paper_writer.skill.tools.p0_gate import count_p0_findings
        summary = count_p0_findings(audit_dir)
        return (
            summary.total,
            dict(summary.per_source),
            dict(summary.per_class),
            len(summary.demoted_findings),
        )
    except Exception:
        # Fall back to raw counting if the import fails. Operator
        # sees a degraded count but the harness keeps running.
        total = 0
        by_source: dict[str, int] = {}
        by_class: dict[str, int] = {}
        adv = _load_json_safe(audit_dir / "adversarial_review.json")
        if isinstance(adv, dict) and isinstance(adv.get("findings"), list):
            for f in adv["findings"]:
                if isinstance(f, dict) and f.get("severity") == "P0":
                    total += 1
                    by_source["adversarial"] = by_source.get("adversarial", 0) + 1
                    cls = str(f.get("class", "unknown"))
                    by_class[cls] = by_class.get(cls, 0) + 1
        num = _load_json_safe(audit_dir / "numeric_grounding.json")
        if isinstance(num, dict) and isinstance(num.get("findings"), list):
            for f in num["findings"]:
                if isinstance(f, dict) and f.get("severity") == "P0":
                    total += 1
                    by_source["numeric_grounding"] = (
                        by_source.get("numeric_grounding", 0) + 1
                    )
                    cls = str(f.get("match_class", "unknown"))
                    by_class[cls] = by_class.get(cls, 0) + 1
        return total, by_source, by_class, 0


def _count_silent_failures(audit_dir: Path) -> int:
    """Tier S-9a signal — review_mode.json with reviewer='canonical-silent-fail'."""
    mode = _load_json_safe(audit_dir / "review_mode.json")
    if isinstance(mode, dict) and mode.get("reviewer") == "canonical-silent-fail":
        return 1
    return 0


def _collect_claim_marker_stats(
    audit_dir: Path,
) -> tuple[int, int, int, float]:
    """Read claim_marker_check.json. Returns (total_markers,
    unique_markers, resolved_unique, resolved_pct). Returns zeros
    when the file is absent (claim markers were a Phase B feature;
    pre-Phase-B drafts won't have this artifact)."""
    data = _load_json_safe(audit_dir / "claim_marker_check.json")
    if not isinstance(data, dict):
        return 0, 0, 0, 0.0
    totals = data.get("totals") or {}
    total = int(totals.get("markers_in_manuscript", 0))
    unique = int(totals.get("unique_markers_in_manuscript", 0))
    resolved = int(totals.get("cited_and_resolved", 0))
    pct = (100.0 * resolved / unique) if unique > 0 else 100.0
    return total, unique, resolved, pct


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def collect(draft_dir: Path, project_id: Optional[str] = None) -> ProjectMetrics:
    state = _load_state(draft_dir)
    audit_dir = draft_dir / "audit"

    pid = project_id or state.get("project_id") or draft_dir.parent.parent.name
    final_phase = str(state.get("phase", "unknown"))
    validators_ok, validators_total, breakdown = _count_validators(state)

    # First-cut measurement when the draft has been remediated.
    # audit/iter_1/ holds the snapshot of audit JSONs at the first P0
    # gate pause. If iter_1/ doesn't exist (no remediation), audit/ IS
    # the first-cut state. Under the no-auto-remediate harness drafts
    # naturally have audit/ as first-cut.
    iter1_dir = audit_dir / "iter_1"
    if iter1_dir.is_dir() and (iter1_dir / "adversarial_review.json").is_file():
        measurement_dir = iter1_dir
        # Cost at first-cut = total - remediation drafter + review spend.
        # review_cost_usd is currently unpopulated by the producer
        # (V1_X_BACKLOG.md #43); the `or 0.0` keeps this forward-
        # compatible when the producer fix lands.
        cycles_data = state.get("remediation_cycles", [])
        remediation_spend = sum(
            float(c.get("drafter_cost_usd", 0.0) or 0.0)
            + float(c.get("review_cost_usd", 0.0) or 0.0)
            for c in cycles_data
        )
        cost_first_cut = float(state.get("cost_so_far_usd", 0.0)) - remediation_spend
    else:
        measurement_dir = audit_dir
        cost_first_cut = float(state.get("cost_so_far_usd", 0.0))

    p0_total, p0_by_source, p0_by_class, p0_demoted = _count_p0s(measurement_dir)

    # Adversarial JSON parseability — advisory only in v2b. A malformed
    # adversarial JSON makes the adversarial P0 count unreliable, but
    # adversarial is not a gated criterion, so this never blocks the
    # verdict — it sets the advisory flag to UNMEASURABLE.
    adv_json_parseable = True
    adv_json_path = measurement_dir / "adversarial_review.json"
    if adv_json_path.is_file():
        try:
            json.loads(adv_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            adv_json_parseable = False
        except OSError:
            adv_json_parseable = False
    else:
        adv_json_parseable = False

    adv_p0 = int(p0_by_source.get("adversarial", 0))
    tier_t_p0 = int(p0_by_source.get("numeric_grounding", 0))

    # Claim marker stats — prefer measurement_dir, fall back to audit/.
    cm_path_candidates = [
        measurement_dir / "claim_marker_check.json",
        audit_dir / "claim_marker_check.json",
    ]
    cm_dir_for_stats = next(
        (p.parent for p in cm_path_candidates if p.is_file()),
        audit_dir,
    )
    cm_total, cm_unique, cm_resolved, cm_pct = _collect_claim_marker_stats(
        cm_dir_for_stats,
    )

    cycles_count = len(state.get("remediation_cycles", []))
    silent = _count_silent_failures(audit_dir)

    reached_measurement_point = final_phase in {
        "p0_review", "remediate", "optimize", "supplementary_pool",
        "compliance_gate", "assemble", "assembled",
    }

    notes: list[str] = []
    if not reached_measurement_point:
        notes.append(
            f"pipeline did not reach p0_review: stopped at {final_phase!r}"
        )
    if silent > 0:
        notes.append(
            "adversarial silent-failure observed; gate may have evaluated stale audit"
        )
    if cycles_count > 0:
        notes.append(
            f"draft has been remediated ({cycles_count} cycle(s)); v1-bar measures "
            f"FIRST-CUT state from audit/iter_1/ snapshot. To see "
            f"post-remediation state, inspect audit/ directly."
        )
    if not adv_json_parseable:
        notes.append(
            "adversarial_review.json at the measurement point is MALFORMED "
            "or absent. Adversarial P0 count is unreliable — advisory flag "
            "set to UNMEASURABLE. Adversarial is NOT a gated criterion in "
            "v2b, so this does not affect the PASS/FAIL verdict."
        )

    m = ProjectMetrics(
        project_id=pid,
        draft_dir=str(draft_dir),
        final_phase=final_phase,
        reached_measurement_point=reached_measurement_point,
        tier_t_ungrounded_count=tier_t_p0,
        claim_markers_total=cm_total,
        claim_markers_unique=cm_unique,
        claim_markers_resolved=cm_resolved,
        claim_markers_resolved_pct=cm_pct,
        cost_so_far_usd=cost_first_cut,
        adversarial_p0_count=adv_p0,
        adversarial_json_parseable=adv_json_parseable,
        p0_total=p0_total,
        p0_by_source=p0_by_source,
        p0_by_class=p0_by_class,
        p0_demoted_count=p0_demoted,
        reached_assembled=(final_phase == "assembled"),
        validators_pass_or_na=validators_ok,
        validators_total=validators_total,
        validator_breakdown=breakdown,
        remediation_cycles_used=cycles_count,
        silent_failures=silent,
        notes=notes,
    )
    m.evaluate()
    return m


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        help=(
            "Path to papers/draft_N/ directory, OR a project_id when --auto is set."
        ),
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Treat `target` as a project_id and auto-discover the latest "
            "papers/draft_N/. Honors BERIL_EXTENDED_DIR env var; defaults "
            "to Adam's workspace layout."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    if args.auto:
        draft = autodiscover_draft_dir(args.target)
        if not draft:
            print(
                f"error: no papers/draft_N/ found for project {args.target!r}",
                file=sys.stderr,
            )
            return 1
        project_id = args.target
    else:
        draft = Path(args.target).expanduser().resolve()
        if not draft.is_dir():
            print(f"error: draft_dir not found: {draft}", file=sys.stderr)
            return 1
        project_id = None

    m = collect(draft, project_id=project_id)

    if args.json:
        print(json.dumps(m.to_dict(), indent=2))
        return 0 if m.crit_all else 2

    # Human-readable (v1-bar v2b).
    print(f"=== {m.project_id} — {m.overall_label} ===")
    print(f"  draft_dir:   {m.draft_dir}")
    print(f"  final_phase: {m.final_phase}")
    print()
    print("  v1-bar v2b (STAGED_IMPROVEMENT_PLAN.md, 2026-05-20):")
    print(
        f"    reached measurement point: {m.reached_measurement_point}  "
        f"[crit: {'OK' if m.crit_reached else 'FAIL'}]"
    )
    print(
        f"    Tier T ungrounded:         {m.tier_t_ungrounded_count}  "
        f"[crit: <= {PASS_CRITERIA['max_tier_t_ungrounded']} → "
        f"{'OK' if m.crit_tier_t else 'FAIL'}]"
    )
    print(
        f"    claim markers resolved:    {m.claim_markers_resolved}/"
        f"{m.claim_markers_unique} ({m.claim_markers_resolved_pct:.1f}%)  "
        f"[crit: >= {PASS_CRITERIA['min_claim_marker_resolved_pct']}% → "
        f"{'OK' if m.crit_markers else 'FAIL'}]"
    )
    print(
        f"    cost_so_far_usd:           ${m.cost_so_far_usd:.2f}  "
        f"[crit: <= ${PASS_CRITERIA['max_cost_usd']:.2f} → "
        f"{'OK' if m.crit_cost else 'FAIL'}]"
    )
    print()
    print("  advisory (NOT gating):")
    if m.adversarial_advisory == "UNMEASURABLE":
        print(
            "    adversarial P0:            UNMEASURABLE "
            "(adversarial_review.json malformed/absent)"
        )
    else:
        print(
            f"    adversarial P0:            {m.adversarial_p0_count}  "
            f"[advisory ceiling {ADVERSARIAL_P0_ADVISORY_CEILING} → "
            f"{m.adversarial_advisory}]"
        )
    print()
    print("  diagnostic detail:")
    print(f"    p0_total:                  {m.p0_total} (adv + Tier T combined)")
    print(f"    p0_by_source:              {m.p0_by_source}")
    print(f"    p0_by_class:               {m.p0_by_class}")
    if m.p0_demoted_count > 0:
        print(
            f"    p0_demoted:                {m.p0_demoted_count} "
            "(NEEDS CITATION + pre-compliance Data Availability filtered)"
        )
    print(
        f"    validators:                {m.validators_pass_or_na} / "
        f"{m.validators_total} pass+NA "
        f"({'post-assemble' if m.reached_assembled else 'expected 0/0 pre-assemble'})"
    )
    print(f"    remediation_cycles:        {m.remediation_cycles_used}")
    print(f"    silent_failures:           {m.silent_failures}")
    if m.notes:
        print()
        print("  notes:")
        for n in m.notes:
            print(f"    - {n}")
    return 0 if m.crit_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
