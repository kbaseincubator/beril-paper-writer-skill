"""p0_gate.py — Stage 4 Tier S (2026-05-18). P0 gate counter + renderer.

After `phase_review` runs its Tier 1+2+3 cascade, two JSON files in
`<draft_dir>/audit/` carry structured findings:

  * ``adversarial_review.json`` — Tier 3 canonical adversarial output.
    Schema ``adversarial-review-paper.vN``; each finding has a
    ``severity`` field with values like ``P0``, ``P1``, ``P2``, ``info``.
    The relevant fields for gating are ``id``, ``class``, ``severity``,
    ``issue``, ``fix_target``, ``fix_hint``, ``section_label``,
    ``paragraph_quote``.

  * ``numeric_grounding.json`` — Tier 1 deterministic numeric grounding.
    Schema ``v1``; each finding has ``severity`` (always ``P0`` in
    strict mode), ``claim_text``, ``matched_text``,
    ``normalized_value``, ``match_class``, ``section``, ``paragraph``,
    ``char_offset``, ``rationale``.

This module is a pure-function library: no I/O of its own, no side
effects beyond reading the two JSON files and writing one markdown
file. Tests pin its behaviour exhaustively before any orchestrator
wiring touches it.

The orchestrator's ``phase_p0_review`` consumes ``count_p0_findings``
to decide whether to halt at the gate. ``render_p0_findings_md`` is
the human-readable view the operator sees while paused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# Names used in by-source dicts. Stable; the renderer keys on these.
SOURCE_ADVERSARIAL = "adversarial"
SOURCE_NUMERIC = "numeric_grounding"

# Severity label that triggers the gate.
SEVERITY_P0 = "P0"


@dataclass
class P0Finding:
    """One P0 finding normalised across the two source schemas.

    The renderer and the orchestrator's remediation prompt builder
    both consume this shape; centralising the normalisation keeps
    the gate code agnostic of producer schema drift.

    Stage 7 Patch 3 (2026-05-18): added ``filter_reason`` so the
    gate can demote false-positive findings without losing the
    audit trail. ``None`` means the finding is a real P0; a set
    value means the finding has been demoted (does not count
    toward the gate total but stays visible in the rendered view).
    """

    source: str          # SOURCE_ADVERSARIAL | SOURCE_NUMERIC
    finding_id: str      # adversarial 'id' | synthesised 'NG-<offset>'
    finding_class: str   # adversarial 'class' | numeric 'match_class'
    severity: str        # always "P0" by construction in this helper
    location: str        # section + paragraph (human-readable)
    description: str     # adversarial 'issue' | numeric 'rationale'
    fix_target: str      # adversarial 'fix_target' | "" for numeric
    fix_hint: str        # adversarial 'fix_hint' | constructed for numeric
    quote: str           # paragraph_quote | matched_text
    filter_reason: Optional[str] = None  # P3: false-positive demotion tag

    def to_dict(self) -> dict[str, Any]:
        out = {
            "source": self.source,
            "finding_id": self.finding_id,
            "finding_class": self.finding_class,
            "severity": self.severity,
            "location": self.location,
            "description": self.description,
            "fix_target": self.fix_target,
            "fix_hint": self.fix_hint,
            "quote": self.quote,
        }
        if self.filter_reason is not None:
            out["filter_reason"] = self.filter_reason
        return out


@dataclass
class P0Summary:
    """Aggregate result of counting P0s across the two audit JSONs.

    A ``P0Summary`` is the gate's contract with the rest of the
    pipeline: the orchestrator decides pause/advance/dispatch off
    the ``total`` field, and the renderer materialises the
    human-readable view from ``findings``.

    ``per_source`` and ``per_class`` are summary counts reflecting
    only the non-demoted findings (the ones that count toward the
    gate decision). ``notes`` captures partial-input conditions
    (missing JSON files, malformed payloads).

    Stage 7 Patch 3 (2026-05-18): added ``demoted_findings`` so
    the gate can preserve the audit trail for false-positives it
    filtered out (e.g., adversarial-flagged ``[NEEDS CITATION:]``
    placeholders that are intentional intermediate-state markers,
    or pre-compliance_gate ``missing_section`` findings about Data
    Availability that compliance_gate will autofix). The demoted
    findings carry their ``filter_reason`` so the renderer + audit
    trail show why they were filtered.
    """

    total: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    per_class: dict[str, int] = field(default_factory=dict)
    findings: list[P0Finding] = field(default_factory=list)
    demoted_findings: list[P0Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "per_source": dict(self.per_source),
            "per_class": dict(self.per_class),
            "findings": [f.to_dict() for f in self.findings],
            "demoted_findings": [
                f.to_dict() for f in self.demoted_findings
            ],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


def _load_json_safe(path: Path) -> tuple[Optional[Any], Optional[str]]:
    """Read JSON. Return (payload, None) on success, (None, note) on
    missing or malformed input. Never raises — the gate must continue
    even under partial telemetry."""
    if not path.is_file():
        return None, f"{path.name} not found at {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name} malformed JSON: {exc.msg}"
    except OSError as exc:
        return None, f"{path.name} unreadable: {exc!r}"


def _normalise_adversarial(payload: Any) -> tuple[list[P0Finding], list[str]]:
    """Extract P0 findings from an adversarial_review.json payload."""
    findings: list[P0Finding] = []
    notes: list[str] = []
    if not isinstance(payload, dict):
        notes.append("adversarial payload not a dict; skipping")
        return findings, notes

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        notes.append("adversarial payload has no 'findings' array")
        return findings, notes

    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        if raw.get("severity") != SEVERITY_P0:
            continue
        finding_id = str(raw.get("id") or "")
        finding_class = str(raw.get("class") or "unknown")
        section_label = raw.get("section_label") or ""
        # Adversarial does not carry paragraph indices reliably.
        location = section_label or "(section unspecified)"
        description = str(raw.get("issue") or "")
        fix_target = str(raw.get("fix_target") or "")
        fix_hint = str(raw.get("fix_hint") or "")
        quote = str(raw.get("paragraph_quote") or "")
        findings.append(
            P0Finding(
                source=SOURCE_ADVERSARIAL,
                finding_id=finding_id,
                finding_class=finding_class,
                severity=SEVERITY_P0,
                location=location,
                description=description,
                fix_target=fix_target,
                fix_hint=fix_hint,
                quote=quote,
            )
        )
    return findings, notes


def _normalise_numeric(payload: Any) -> tuple[list[P0Finding], list[str]]:
    """Extract P0 findings from a numeric_grounding.json payload."""
    findings: list[P0Finding] = []
    notes: list[str] = []
    if not isinstance(payload, dict):
        notes.append("numeric_grounding payload not a dict; skipping")
        return findings, notes

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        notes.append("numeric_grounding payload has no 'findings' array")
        return findings, notes

    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        # Numeric grounding emits "P0" in strict mode; defensive check
        # in case a future schema introduces P1+ for soft warnings.
        if raw.get("severity") != SEVERITY_P0:
            continue
        section = str(raw.get("section") or "(section unspecified)")
        paragraph = raw.get("paragraph")
        matched_text = str(raw.get("matched_text") or "")
        match_class = str(raw.get("match_class") or "ungrounded_numeric")
        char_offset = raw.get("char_offset")
        # Synthesise a stable finding_id since the numeric checker
        # doesn't emit one (its outputs are positional, not registry-keyed).
        if isinstance(char_offset, int):
            finding_id = f"NG-{char_offset:06d}"
        else:
            finding_id = f"NG-{len(findings):04d}"
        location = (
            f"{section} para {paragraph}"
            if isinstance(paragraph, int)
            else section
        )
        rationale = str(raw.get("rationale") or "")
        claim_text = str(raw.get("claim_text") or "")
        description = (
            rationale
            or f"Ungrounded numeric {matched_text!r} ({match_class})."
        )
        # No fix_hint comes through from the numeric checker; build one
        # so the remediation prompt has actionable guidance.
        fix_hint = (
            f"Remove or revise the numeric {matched_text!r}: it has "
            "no match in claim_inventory.tsv (Tier A) or REPORT.md "
            "(Tier B). Do NOT replace with an invented number. If "
            "the prose needs a quantity, hedge it qualitatively."
        )
        findings.append(
            P0Finding(
                source=SOURCE_NUMERIC,
                finding_id=finding_id,
                finding_class=match_class,
                severity=SEVERITY_P0,
                location=location,
                description=description,
                fix_target="",
                fix_hint=fix_hint,
                quote=claim_text or matched_text,
            )
        )
    return findings, notes


# Filter reason tags. These are the only values that show up as
# `filter_reason` on demoted P0Findings; renderer / audit consumers
# can pattern-match against them.
FILTER_NEEDS_CITATION_PLACEHOLDER = "needs-citation-placeholder"
FILTER_PRE_COMPLIANCE_MISSING_SECTION = "pre-compliance-missing-section"

# Section labels that compliance_gate autofixes. Adversarial findings
# of class `missing_section` that mention any of these in description
# OR fix_target are demoted on the assumption that compliance_gate
# (which runs after the P0 gate) will populate them. If the same
# finding appears AFTER compliance_gate ran, the gate sees real defect.
# (v1 simplification: we always demote at gate-evaluation time since
#  the gate only fires pre-compliance. v1.1 could pass state.phase
#  through and reverse-promote post-compliance.)
_COMPLIANCE_AUTOFIX_SECTIONS = (
    "Data Availability",
    "Code Availability",
    "data availability",
    "code availability",
)


def _apply_false_positive_filters(
    findings: list[P0Finding],
) -> tuple[list[P0Finding], list[P0Finding], list[str]]:
    """Stage 7 Patch 3 (2026-05-18): demote known false-positive
    adversarial findings without losing them from the audit trail.

    Two rules in v1:

    1. ``citation_reality`` + manuscript quote / description contains
       ``[NEEDS CITATION:`` → demote. These are intentional pre-
       supplementary-pool markers awaiting WebSearch resolution. The
       adversarial reviewer (which knows nothing about pipeline phase
       state) sees them as fabricated citations; they are not.

    2. ``missing_section`` + finding text mentions Data Availability
       or Code Availability → demote. These are autofixed by
       compliance_gate, which runs AFTER the P0 gate in the v0.8
       pipeline order. Flagging them as P0 at gate-evaluation time
       pre-empts compliance_gate's intended behaviour.

    Returns ``(kept, demoted, filter_notes)``. Filter notes summarise
    what was filtered for the rendered audit trail.
    """
    kept: list[P0Finding] = []
    demoted: list[P0Finding] = []
    notes: list[str] = []

    n_needs_citation = 0
    n_missing_section = 0

    for f in findings:
        # Rule 1: NEEDS CITATION placeholder false-positive.
        # Check both description AND quote — the adversarial schema
        # is inconsistent about which field carries the manuscript text.
        if (
            f.source == SOURCE_ADVERSARIAL
            and f.finding_class == "citation_reality"
            and (
                "[NEEDS CITATION" in f.description
                or "[NEEDS CITATION" in f.quote
            )
        ):
            f.filter_reason = FILTER_NEEDS_CITATION_PLACEHOLDER
            demoted.append(f)
            n_needs_citation += 1
            continue

        # Rule 2: pre-compliance-gate missing Data/Code Availability.
        if (
            f.source == SOURCE_ADVERSARIAL
            and f.finding_class == "missing_section"
            and any(
                tag in f.description or tag in f.fix_target
                for tag in _COMPLIANCE_AUTOFIX_SECTIONS
            )
        ):
            f.filter_reason = FILTER_PRE_COMPLIANCE_MISSING_SECTION
            demoted.append(f)
            n_missing_section += 1
            continue

        kept.append(f)

    if n_needs_citation > 0:
        notes.append(
            f"filter_applied: demoted {n_needs_citation} citation_reality "
            "finding(s) flagging [NEEDS CITATION:] placeholders — these "
            "are intentional pre-supplementary-pool markers. See "
            "demoted_findings."
        )
    if n_missing_section > 0:
        notes.append(
            f"filter_applied: demoted {n_missing_section} missing_section "
            "finding(s) about Data Availability / Code Availability — "
            "compliance_gate autofixes these post-gate. See "
            "demoted_findings."
        )

    return kept, demoted, notes


def count_p0_findings(audit_dir: Path) -> P0Summary:
    """Read both audit JSONs from ``audit_dir`` and return a P0Summary.

    Never raises. Missing or malformed inputs reduce signal but do
    not block the gate from making a decision — partial-input notes
    are surfaced via ``P0Summary.notes`` and rendered to the operator.

    Stage 7 Patch 3 (2026-05-18): after normalising findings from
    both producers, apply false-positive filters and split into
    ``findings`` (real P0s that drive the gate) and
    ``demoted_findings`` (filtered-out but preserved for audit).
    """
    summary = P0Summary()
    audit_dir = Path(audit_dir)

    raw_findings: list[P0Finding] = []

    adv_payload, adv_note = _load_json_safe(
        audit_dir / "adversarial_review.json"
    )
    if adv_note:
        summary.notes.append(adv_note)
    if adv_payload is not None:
        adv_findings, adv_inner_notes = _normalise_adversarial(adv_payload)
        raw_findings.extend(adv_findings)
        summary.notes.extend(adv_inner_notes)

    num_payload, num_note = _load_json_safe(
        audit_dir / "numeric_grounding.json"
    )
    if num_note:
        summary.notes.append(num_note)
    if num_payload is not None:
        num_findings, num_inner_notes = _normalise_numeric(num_payload)
        raw_findings.extend(num_findings)
        summary.notes.extend(num_inner_notes)

    # Apply false-positive filters (P3). kept drives the gate; demoted
    # stays visible in the rendered audit.
    kept, demoted, filter_notes = _apply_false_positive_filters(raw_findings)
    summary.findings = kept
    summary.demoted_findings = demoted
    summary.notes.extend(filter_notes)

    # Aggregate counts — only over kept findings (the ones that
    # actually count toward the gate decision).
    summary.total = len(summary.findings)
    for f in summary.findings:
        summary.per_source[f.source] = summary.per_source.get(f.source, 0) + 1
        summary.per_class[f.finding_class] = (
            summary.per_class.get(f.finding_class, 0) + 1
        )
    return summary


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int = 280) -> str:
    """Trim long strings for the markdown view. Preserves the
    head + tail of long quotes/descriptions so the operator can
    eyeball the issue without scrolling pages of text."""
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    if len(s) <= n:
        return s
    head_n = (n - 16) // 2
    tail_n = n - 16 - head_n
    return f"{s[:head_n]} … [trunc] … {s[-tail_n:]}"


def render_p0_findings_md(
    summary: P0Summary,
    *,
    draft_dir: Path,
    cycles_used: int,
    max_cycles: int,
    cycles_exhausted: bool,
) -> str:
    """Render a human-readable Markdown view of the P0 gate state.

    Args:
      summary           — Output of ``count_p0_findings``.
      draft_dir         — Absolute path; embedded in the proceed
                          instructions so the operator can copy-paste.
      cycles_used       — len(state.remediation_cycles) at this point.
      max_cycles        — Configured cap.
      cycles_exhausted  — True when ``cycles_used >= max_cycles`` AND
                          the operator already attempted ``--remediate``;
                          this reframes the proceed instructions away
                          from "try remediation" toward
                          "ship-with-p0s, raise the cap, or edit by hand".

    The output is deterministic given (summary, cycles state); no
    timestamps or other run-specific noise appears in the body, so
    tests can compare byte-for-byte.
    """
    lines: list[str] = []
    lines.append("# P0 gate — pause point\n")

    # Header summary block.
    lines.append("## Summary\n")
    lines.append(f"- **Total P0 findings:** {summary.total}")
    if summary.per_source:
        per_src = ", ".join(
            f"{src}={summary.per_source[src]}"
            for src in sorted(summary.per_source)
        )
        lines.append(f"- **By source:** {per_src}")
    if summary.per_class:
        per_cls = ", ".join(
            f"{cls}={summary.per_class[cls]}"
            for cls in sorted(summary.per_class)
        )
        lines.append(f"- **By class:** {per_cls}")
    lines.append(
        f"- **Remediation cycles used:** {cycles_used} / {max_cycles}"
    )
    if cycles_exhausted:
        lines.append(
            "- **Cycle cap exhausted.** Further `--remediate` calls "
            "with the current cap will refuse to dispatch."
        )
    if summary.notes:
        lines.append("- **Telemetry notes:**")
        for n in summary.notes:
            lines.append(f"  - {n}")
    lines.append("")

    # Proceed instructions — embedded in the file so the operator
    # doesn't have to consult docs.
    lines.append("## How to proceed\n")
    if summary.total == 0:
        lines.append(
            "There are no P0 findings. This file should not have "
            "been generated; if you are reading it, the gate ran in "
            "degraded mode (see the Telemetry notes above) or "
            "stale state.\n"
        )
    elif cycles_exhausted:
        lines.append(
            "The remediation cycle cap was reached without "
            "eliminating P0s. Three options:\n"
        )
        lines.append(
            f"1. **Ship anyway** (acknowledge the risk):\n\n"
            f"   ```\n"
            f"   beril-paper-writer continue {draft_dir} --ship-with-p0s\n"
            f"   ```\n"
        )
        lines.append(
            f"2. **Raise the cap and try again** (more LLM cost; "
            f"may or may not converge):\n\n"
            f"   ```\n"
            f"   beril-paper-writer continue {draft_dir} --remediate "
            f"--max-remediate-cycles {max_cycles + 2}\n"
            f"   ```\n"
        )
        lines.append(
            f"3. **Edit `manuscript.md` by hand** and re-run "
            f"(the gate re-runs phase_review on any manuscript edit "
            f"it detects):\n\n"
            f"   ```\n"
            f"   # ...edit {draft_dir}/manuscript.md...\n"
            f"   beril-paper-writer continue {draft_dir}\n"
            f"   ```\n"
        )
    else:
        lines.append(
            "Three options:\n"
        )
        lines.append(
            f"1. **Attempt automated remediation** (re-drafts the "
            f"manuscript with anti-fabrication discipline; cap "
            f"defaults to 2 cycles):\n\n"
            f"   ```\n"
            f"   beril-paper-writer continue {draft_dir} --remediate\n"
            f"   ```\n"
        )
        lines.append(
            f"2. **Ship anyway** (advance to optimize despite P0s; "
            f"the optimizer's subtraction-only invariant still "
            f"applies, but the P0 findings will land in the audit "
            f"trail unaddressed):\n\n"
            f"   ```\n"
            f"   beril-paper-writer continue {draft_dir} --ship-with-p0s\n"
            f"   ```\n"
        )
        lines.append(
            f"3. **Edit `manuscript.md` by hand** and re-run "
            f"(the gate re-runs phase_review on any manuscript edit "
            f"it detects):\n\n"
            f"   ```\n"
            f"   # ...edit {draft_dir}/manuscript.md...\n"
            f"   beril-paper-writer continue {draft_dir}\n"
            f"   ```\n"
        )
    lines.append("")

    # Stage 7 Patch 3: filtered (informational) findings — these
    # don't count toward the gate but the operator should see them.
    # Surface BEFORE the load-bearing findings section so the audit
    # trail is reviewed first.
    if summary.demoted_findings:
        lines.append("## Filtered findings (not counted in P0 total)\n")
        lines.append(
            "These adversarial findings were demoted by the gate's "
            "false-positive filter. They are recorded here for "
            "audit-trail completeness. Each carries a "
            "`filter_reason` documenting why it was demoted.\n"
        )
        ordered_demoted = sorted(
            summary.demoted_findings,
            key=lambda f: (f.source, f.finding_id),
        )
        for f in ordered_demoted:
            lines.append(
                f"### {f.finding_id} — {f.finding_class} "
                f"({f.source}) — filter: {f.filter_reason}\n"
            )
            lines.append(f"- **Location:** {f.location}")
            lines.append(
                f"- **Issue (demoted):** {_truncate(f.description)}"
            )
            if f.quote:
                lines.append(
                    f"- **Quote:** `{_truncate(f.quote, n=200)}`"
                )
            lines.append("")

    # Per-finding detail. Stable ordering: adversarial first, then
    # numeric_grounding, each sorted by finding_id so the file is
    # byte-stable across re-renders of the same state.
    if summary.findings:
        lines.append("## Findings\n")
        ordered = sorted(
            summary.findings,
            key=lambda f: (
                0 if f.source == SOURCE_ADVERSARIAL else 1,
                f.finding_id,
            ),
        )
        for f in ordered:
            lines.append(
                f"### {f.finding_id} — {f.finding_class} "
                f"({f.source})\n"
            )
            lines.append(f"- **Location:** {f.location}")
            if f.fix_target:
                lines.append(f"- **Fix target:** {f.fix_target}")
            lines.append(f"- **Issue:** {_truncate(f.description)}")
            if f.fix_hint:
                lines.append(
                    f"- **Suggested fix:** {_truncate(f.fix_hint)}"
                )
            if f.quote:
                lines.append(
                    f"- **Quote:** "
                    f"`{_truncate(f.quote, n=200)}`"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
