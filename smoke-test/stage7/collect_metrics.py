"""Stage 7 v1-MVP — per-draft metrics collector.

Reads a paper-writer draft directory's state.json + audit artifacts and
emits the metrics needed to evaluate the v1-MVP success criteria.

Success bar (locked 2026-05-18 by Adam, STAGED_IMPROVEMENT_PLAN.md):
    reached_assembled        = True
    validators_pass_or_na   >= 8
    p0_count                <= 5  (combined adversarial + numeric_grounding)
    cost_so_far_usd         <= 10.0

v1-bar v2a (2026-05-20) ships ONLY orthogonal pieces and leaves the
locked bar unchanged:

    1. First-cut measurement via audit/iter_1/ snapshot. When a draft
       has been remediated, the bar evaluates the pre-remediation
       state, not the post-remediation state. Under the no-auto-
       remediate harness (Adam's 2026-05-19 decision) most drafts
       will not have iter_1/; audit/ IS first-cut. iter_1/ remains
       the right read for historical remediated drafts and for any
       project the operator manually remediates.

    2. INCONCLUSIVE verdict for malformed adversarial JSON. When the
       adversarial reviewer emits unescaped inner quotes (known
       failure mode per feedback_llm_json_unfixable_in_parser.md),
       strict json.loads returns 0 P0s — which would FALSELY pass
       the bar on the partial Tier-T-only count. We detect parse
       failure and label INCONCLUSIVE so the verdict is honest.

    3. Cost-first-cut subtraction. When iter_1/ snapshot is in play,
       cost_so_far_usd is rolled back by the remediation drafter +
       review costs to attribute spend correctly. See #43 in
       V1_X_BACKLOG.md — review_cost_usd is currently unpopulated by
       the producer; the subtraction handles it via `or 0.0` for
       forward compatibility.

    4. New diagnostic fields (adversarial_p0_count, tier_t_ungrounded_count,
       claim_markers_*, adversarial_json_parseable) collected from
       disk and reported, but NOT in crit_all. These feed v2b's
       upcoming bar revision after #41 (Tier T extractor false-
       positive fix) ships clean numbers.

v2b will replace PASS_CRITERIA wholesale once #41 + the same-manuscript
double-review measurement (#37 lever 4) are resolved. Under the no-
auto-remediate harness `reached_assembled = True` cannot be satisfied
without operator-driven remediation; that's the semantic mismatch v2b
addresses. v2a stays faithful to what was locked 2026-05-18.

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

# v1-bar v2a (2026-05-20): unchanged from the locked plan
# (STAGED_IMPROVEMENT_PLAN.md, 2026-05-18). v2b will revise after
# #41 + #37-lever-4 land.
#
# NOTE: under the no-auto-remediate harness (Adam's 2026-05-19
# decision) the pipeline pauses at p0_review and the locked
# `reached_assembled = True` cannot be satisfied without operator-
# driven remediation. v2a evaluates faithfully to the locked bar;
# expect every no-auto-remediate run to FAIL on this criterion until
# v2b updates the measurement-point semantic.
PASS_CRITERIA = {
    "reached_assembled":         True,
    "min_validators_pass_or_na": 8,
    "max_p0_findings":           5,
    "max_cost_usd":              10.0,
}


@dataclass
class ProjectMetrics:
    """One project's metrics as collected from disk artifacts.

    Includes both the locked-bar fields and the v1-bar v2a diagnostic
    fields. Only the locked-bar fields enter crit_all evaluation; the
    diagnostics are reported but not gating.
    """

    # Identity / phase.
    project_id:                     str
    draft_dir:                      str
    final_phase:                    str

    # Locked bar inputs.
    reached_assembled:              bool
    validators_pass_or_na:          int
    validators_total:               int
    validator_breakdown:            dict[str, int]
    p0_total:                       int            # adv + Tier T combined
    p0_by_source:                   dict[str, int]
    p0_by_class:                    dict[str, int]
    p0_demoted_count:               int
    cost_so_far_usd:                float          # first-cut adjusted
    remediation_cycles_used:        int
    silent_failures:                int

    # v2a diagnostic fields (NOT in crit_all; feed v2b bar revision).
    reached_p0_review_or_assembled: bool = False   # measurement-point check
    adversarial_p0_count:           int = 0
    tier_t_ungrounded_count:        int = 0
    claim_markers_total:            int = 0
    claim_markers_unique:           int = 0
    claim_markers_resolved:         int = 0
    claim_markers_resolved_pct:     float = 0.0
    adversarial_json_parseable:     bool = True

    notes:                          list[str] = field(default_factory=list)

    # Pass/fail booleans against the LOCKED PASS_CRITERIA.
    crit_reached_assembled:    bool = False
    crit_validators_ok:        bool = False
    crit_p0_ok:                bool = False
    crit_cost_ok:              bool = False
    crit_all:                  bool = False
    overall_label:             str = "FAIL"   # PASS | FAIL | INCONCLUSIVE

    def evaluate(self) -> None:
        """Evaluate the locked 4-criteria bar.

        INCONCLUSIVE handling: when adversarial JSON is unparseable,
        p0_total under-counts (Tier T alone is countable). If the
        partial count would otherwise PASS the bar, the verdict is
        INCONCLUSIVE — we can't claim PASS without measurable
        adversarial findings. If the partial count already exceeds
        the threshold, FAIL is still defensible (we know it fails).
        """
        self.crit_reached_assembled = (
            self.reached_assembled == PASS_CRITERIA["reached_assembled"]
        )
        self.crit_validators_ok = (
            self.validators_pass_or_na >= PASS_CRITERIA["min_validators_pass_or_na"]
        )
        self.crit_p0_ok = self.p0_total <= PASS_CRITERIA["max_p0_findings"]
        self.crit_cost_ok = (
            self.cost_so_far_usd <= PASS_CRITERIA["max_cost_usd"]
        )
        self.crit_all = all([
            self.crit_reached_assembled,
            self.crit_validators_ok,
            self.crit_p0_ok,
            self.crit_cost_ok,
        ])

        if not self.adversarial_json_parseable and self.crit_p0_ok:
            # Adversarial unparseable; partial count looks OK but is
            # incomplete. Cannot claim PASS.
            self.overall_label = "INCONCLUSIVE"
            self.crit_all = False
        elif self.crit_all:
            self.overall_label = "PASS"
        else:
            self.overall_label = "FAIL"

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
    log line `Remediation cycle 1: p0_before=N → p0_after=M`.

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

    # v1-bar v2a: measure the FIRST-CUT state when the draft has been
    # remediated. audit/iter_1/ holds the snapshot of audit JSONs at
    # the first P0 gate pause — that's the first-cut quality we want
    # to measure. If iter_1/ doesn't exist (no remediation has run
    # yet), audit/ IS the first-cut state. Under the no-auto-
    # remediate harness (Adam's 2026-05-19 decision), drafts naturally
    # have audit/ as first-cut (iter_1/ never created).
    iter1_dir = audit_dir / "iter_1"
    if iter1_dir.is_dir() and (iter1_dir / "adversarial_review.json").is_file():
        # Remediated draft — measure pre-remediation state via snapshot.
        measurement_dir = iter1_dir
        # Cost at first-cut = total - remediation drafter + review spend.
        # review_cost_usd is currently unpopulated by the producer
        # (V1_X_BACKLOG.md #43); the `or 0.0` makes this forward-
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

    # Empirical (D3 2026-05-19): adversarial reviewer occasionally
    # emits malformed JSON (unescaped inner quotes per
    # feedback_llm_json_unfixable_in_parser.md). Strict json.loads
    # fails → the strict-count path in p0_gate returns 0 adversarial
    # P0s, which would FALSELY pass a project whose true first-cut
    # adversarial output contained P0s. Detect parse failure and
    # surface as INCONCLUSIVE at evaluate().
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
        adv_json_parseable = False  # missing entirely is also inconclusive

    # v2a diagnostic split — collected but not gating.
    adv_p0 = int(p0_by_source.get("adversarial", 0))
    tier_t_p0 = int(p0_by_source.get("numeric_grounding", 0))

    # Claim marker stats — prefer measurement_dir, fall back to audit/.
    # iter_1/ snapshots from pre-Phase-B drafts may not carry the
    # claim_marker_check.json artifact.
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
    if validators_total == 0 and final_phase != "assembled":
        notes.append(
            "no validators ran — expected when paused at p0_review pre-assemble; "
            "the locked bar's min_validators_pass_or_na=8 cannot be satisfied "
            "in this state (v2b resolves the harness/bar semantic mismatch)"
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
            "(unescaped inner quotes per known LLM failure mode). p0_total "
            "is UNRELIABLE — Tier T findings are counted but adversarial "
            "P0s cannot be enumerated. Verdict labeled INCONCLUSIVE rather "
            "than PASS."
        )

    m = ProjectMetrics(
        project_id=pid,
        draft_dir=str(draft_dir),
        final_phase=final_phase,
        reached_assembled=(final_phase == "assembled"),
        validators_pass_or_na=validators_ok,
        validators_total=validators_total,
        validator_breakdown=breakdown,
        p0_total=p0_total,
        p0_by_source=p0_by_source,
        p0_by_class=p0_by_class,
        p0_demoted_count=p0_demoted,
        cost_so_far_usd=cost_first_cut,
        remediation_cycles_used=cycles_count,
        silent_failures=silent,
        reached_p0_review_or_assembled=reached_measurement_point,
        adversarial_p0_count=adv_p0,
        tier_t_ungrounded_count=tier_t_p0,
        claim_markers_total=cm_total,
        claim_markers_unique=cm_unique,
        claim_markers_resolved=cm_resolved,
        claim_markers_resolved_pct=cm_pct,
        adversarial_json_parseable=adv_json_parseable,
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
        if m.crit_all:
            return 0
        return 3 if m.overall_label == "INCONCLUSIVE" else 2

    # Human-readable (v1-bar v2a).
    print(f"=== {m.project_id} — {m.overall_label} ===")
    print(f"  draft_dir:   {m.draft_dir}")
    print(f"  final_phase: {m.final_phase}")
    print()
    print("  locked bar (STAGED_IMPROVEMENT_PLAN.md 2026-05-18):")
    print(
        f"    reached_assembled:    {m.reached_assembled}  "
        f"[crit: {'OK' if m.crit_reached_assembled else 'FAIL'}]"
    )
    print(
        f"    validators_pass+NA:   {m.validators_pass_or_na} / "
        f"{m.validators_total}  "
        f"[crit: >= {PASS_CRITERIA['min_validators_pass_or_na']} → "
        f"{'OK' if m.crit_validators_ok else 'FAIL'}]"
    )
    if not m.adversarial_json_parseable:
        print(
            f"    p0_total:             {m.p0_total} (Tier T only; adversarial "
            f"UNPARSEABLE)  [crit: <= {PASS_CRITERIA['max_p0_findings']} → "
            f"{'OK' if m.crit_p0_ok else 'FAIL'} (partial)]"
        )
    else:
        print(
            f"    p0_total:             {m.p0_total}  "
            f"[crit: <= {PASS_CRITERIA['max_p0_findings']} → "
            f"{'OK' if m.crit_p0_ok else 'FAIL'}]"
        )
    print(
        f"    cost_so_far_usd:      ${m.cost_so_far_usd:.2f}  "
        f"[crit: <= ${PASS_CRITERIA['max_cost_usd']:.2f} → "
        f"{'OK' if m.crit_cost_ok else 'FAIL'}]"
    )
    print()
    print("  v2a diagnostic detail (not in bar):")
    print(
        f"    reached_measurement_point: {m.reached_p0_review_or_assembled} "
        f"(p0_review|...|assembled)"
    )
    print(f"    adversarial_P0:            {m.adversarial_p0_count}"
          + ("  (UNRELIABLE — JSON malformed)" if not m.adversarial_json_parseable else ""))
    print(f"    Tier T ungrounded:         {m.tier_t_ungrounded_count}")
    print(
        f"    claim markers:             {m.claim_markers_resolved}/"
        f"{m.claim_markers_unique} resolved "
        f"({m.claim_markers_resolved_pct:.1f}%)"
    )
    print(f"    p0_by_source:              {m.p0_by_source}")
    print(f"    p0_by_class:               {m.p0_by_class}")
    if m.p0_demoted_count > 0:
        print(
            f"    p0_demoted:                {m.p0_demoted_count} "
            "(NEEDS CITATION + pre-compliance Data Availability filtered)"
        )
    print(f"    validator_breakdown:       {m.validator_breakdown}")
    print(f"    remediation_cycles:        {m.remediation_cycles_used}")
    print(f"    silent_failures:           {m.silent_failures}")
    if m.notes:
        print()
        print("  notes:")
        for n in m.notes:
            print(f"    - {n}")
    if m.crit_all:
        return 0
    return 3 if m.overall_label == "INCONCLUSIVE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
