"""Stage 7 v1-MVP — per-draft metrics collector.

Reads a paper-writer draft directory's state.json + audit artifacts and
emits the metrics needed to evaluate the v1-MVP success criteria:

    reached_assembled        — phase == "assembled"
    validators_pass_or_na    — count of validators marked "pass" or
                               "not-applicable" in state.validator_status
    p0_count                 — total P0 findings across adversarial +
                               numeric_grounding (post-remediation)
    cost_so_far_usd          — cumulative LLM spend
    silent_failures          — count of canonical-silent-fail review_modes
                               observed (Tier S-9a signal)

Success bar (locked 2026-05-18 by Adam):
    reaches_assembled        = True
    validators_pass_or_na   >= 8
    p0_count                <= 5
    cost_so_far_usd         <= 10.0

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

PASS_CRITERIA = {
    "reached_assembled":       True,
    "min_validators_pass_or_na": 8,
    "max_p0_findings":         5,
    "max_cost_usd":            10.0,
}


@dataclass
class ProjectMetrics:
    """One project's metrics as collected from disk artifacts."""

    project_id:               str
    draft_dir:                str
    final_phase:              str
    reached_assembled:        bool
    validators_pass_or_na:    int
    validators_total:         int
    validator_breakdown:      dict[str, int]    # status → count
    p0_total:                 int
    p0_by_source:             dict[str, int]
    p0_by_class:              dict[str, int]
    cost_so_far_usd:          float
    remediation_cycles_used:  int
    silent_failures:          int
    notes:                    list[str] = field(default_factory=list)

    # Pass/fail booleans against PASS_CRITERIA.
    crit_reached_assembled:   bool = False
    crit_validators_ok:       bool = False
    crit_p0_ok:               bool = False
    crit_cost_ok:             bool = False
    crit_all:                 bool = False

    def evaluate(self) -> None:
        self.crit_reached_assembled = self.reached_assembled == PASS_CRITERIA["reached_assembled"]
        self.crit_validators_ok = (
            self.validators_pass_or_na >= PASS_CRITERIA["min_validators_pass_or_na"]
        )
        self.crit_p0_ok = self.p0_total <= PASS_CRITERIA["max_p0_findings"]
        self.crit_cost_ok = self.cost_so_far_usd <= PASS_CRITERIA["max_cost_usd"]
        self.crit_all = all([
            self.crit_reached_assembled,
            self.crit_validators_ok,
            self.crit_p0_ok,
            self.crit_cost_ok,
        ])

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


def _count_p0s(audit_dir: Path) -> tuple[int, dict[str, int], dict[str, int]]:
    """Mirrors p0_gate.count_p0_findings (but read-only and stand-alone so
    this harness doesn't depend on the skill's package layout at runtime)."""
    total = 0
    by_source: dict[str, int] = {}
    by_class: dict[str, int] = {}

    # Adversarial side.
    adv = _load_json_safe(audit_dir / "adversarial_review.json")
    if isinstance(adv, dict) and isinstance(adv.get("findings"), list):
        for f in adv["findings"]:
            if isinstance(f, dict) and f.get("severity") == "P0":
                total += 1
                by_source["adversarial"] = by_source.get("adversarial", 0) + 1
                cls = str(f.get("class", "unknown"))
                by_class[cls] = by_class.get(cls, 0) + 1

    # Numeric grounding side.
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

    return total, by_source, by_class


def _count_silent_failures(audit_dir: Path) -> int:
    """Tier S-9a signal — review_mode.json with reviewer='canonical-silent-fail'."""
    mode = _load_json_safe(audit_dir / "review_mode.json")
    if isinstance(mode, dict) and mode.get("reviewer") == "canonical-silent-fail":
        return 1
    return 0


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def collect(draft_dir: Path, project_id: Optional[str] = None) -> ProjectMetrics:
    state = _load_state(draft_dir)
    audit_dir = draft_dir / "audit"

    pid = project_id or state.get("project_id") or draft_dir.parent.parent.name
    final_phase = str(state.get("phase", "unknown"))
    validators_ok, validators_total, breakdown = _count_validators(state)
    p0_total, p0_by_source, p0_by_class = _count_p0s(audit_dir)
    cost = float(state.get("cost_so_far_usd", 0.0))
    cycles = len(state.get("remediation_cycles", []))
    silent = _count_silent_failures(audit_dir)

    notes: list[str] = []
    if final_phase not in {"assembled", "p0_review", "compliance_gate"}:
        notes.append(f"unexpected terminal phase: {final_phase}")
    if silent > 0:
        notes.append(
            "adversarial silent-failure observed; gate may have evaluated stale audit"
        )
    if validators_total == 0:
        notes.append("no validators ran (state.validator_status is empty)")

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
        cost_so_far_usd=cost,
        remediation_cycles_used=cycles,
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

    # Human-readable.
    status_glyph = "PASS" if m.crit_all else "FAIL"
    print(f"=== {m.project_id} — {status_glyph} ===")
    print(f"  draft_dir:           {m.draft_dir}")
    print(f"  final_phase:         {m.final_phase}")
    print(
        f"  reached_assembled:   {m.reached_assembled}  "
        f"[crit: {'OK' if m.crit_reached_assembled else 'FAIL'}]"
    )
    print(
        f"  validators_pass+NA:  {m.validators_pass_or_na} / "
        f"{m.validators_total}  "
        f"[crit: >= {PASS_CRITERIA['min_validators_pass_or_na']} → "
        f"{'OK' if m.crit_validators_ok else 'FAIL'}]"
    )
    print(f"  validator breakdown: {m.validator_breakdown}")
    print(
        f"  p0_total:            {m.p0_total}  "
        f"[crit: <= {PASS_CRITERIA['max_p0_findings']} → "
        f"{'OK' if m.crit_p0_ok else 'FAIL'}]"
    )
    print(f"  p0_by_source:        {m.p0_by_source}")
    print(f"  p0_by_class:         {m.p0_by_class}")
    print(
        f"  cost_so_far_usd:     ${m.cost_so_far_usd:.2f}  "
        f"[crit: <= ${PASS_CRITERIA['max_cost_usd']:.2f} → "
        f"{'OK' if m.crit_cost_ok else 'FAIL'}]"
    )
    print(f"  remediation_cycles:  {m.remediation_cycles_used}")
    print(f"  silent_failures:     {m.silent_failures}")
    if m.notes:
        print("  notes:")
        for n in m.notes:
            print(f"    - {n}")
    return 0 if m.crit_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
