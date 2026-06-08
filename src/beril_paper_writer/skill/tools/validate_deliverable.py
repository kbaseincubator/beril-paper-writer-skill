#!/usr/bin/env python3
"""validate_deliverable.py — Cycle 2 pre-handoff deterministic gate
(paper-writer edition).

Pure, READ-ONLY check over the produced deliverable (`manuscript.md` +
`manuscript.docx`) + working/audit artifacts. Emits findings under the
**SAME `deliverable-validation.v1` schema** as
beril-presentation-maker (Cycle 1) — the schema is the cross-skill
finding contract, so paper-writer as the second consumer keeps it
verbatim. NOT the cross-skill run-record contract (that's a later
cycle).

Pattern source: `beril-presentation-maker-skill v1.2.0 tools/
validate_deliverable.py`. Paper-writer's gates differ (no slide/
image/aspect concerns; the heavy lifting on section-presence is
already done by validate_manuscript) but the schema, severity/
remediation vocabularies, and detection-separated-from-remediation
discipline are reused unchanged.

Four gates (each maps to a known paper-writer defect class):

  G1 section_completeness
       WRAP validate_manuscript.run_all_validators(draft_dir, mode).
       Map each Violation -> deliverable-validation.v1 Finding with
       severity preserved (error -> P0, warning -> P1) and
       escalation_path projected onto remediation.kind:
           auto-fix             -> auto + "rerun_validate"
                                   (re-runs validate_manuscript so the
                                   downstream Tier-3 reviewer picks
                                   up the fresh report; never mutates
                                   the manuscript itself).
           escalate             -> targeted (operator runs a stage)
           user-modify          -> targeted
           accept-as-limitation -> advisory
       DOES NOT re-implement M1–M10 — the section logic stays in
       validate_manuscript. We only project its results onto the
       deliverable-validation.v1 contract.

  G2 placeholder_or_leaked_template
       No "TBD"/"TBD - …" tokens in the manuscript body (title
       included). No project-directory slug leak in the title.
       Title + author block populated (not blank/TBD).
       APPLY THE CYCLE-1 G1 NARROWING LESSON: the dir-name leak
       check fires ONLY on (a) the verbatim full slug as substring,
       OR (b) ≥2 ADJACENT dir-segments together. A lone segment-
       word like "Caulobacter" or "Loss" does NOT fire. The finding
       is P1 + TARGETED — operator rewrites; never auto-strip.

  G3 figure_resolution_and_embedding
       Every `![alt](path)` block image in manuscript.md resolves on
       disk via assemble_docx's lookup (draft_dir/figures first, then
       project_dir/figures); AND when the docx is present, every
       image reference has a matching embedded picture in the docx.
       Catches stale figure refs the LLM wrote AND silent embed
       failures.

  G4 mode_depth_vs_user_intent
       The DP9b-analogue. Compares the persisted user intent
       (`audit/user_intent.json` from the copied user_intent.py) to:
         - state.json `mode` (paper|report);
         - the validate_manuscript ValidationReport's `mode`;
         - the LLM-emitted depth band (best-effort heuristic — depth
           is not yet recorded in the manuscript artifacts; this
           gate surfaces "user picked X but we have no on-disk
           depth signal" as an advisory rather than blocking, since
           the persistence on its own already fixes 80% of the bug).
       Missing or non-explicit user_intent → advisory only.

Each finding carries a `remediation` keyed to cost. Vocabularies are
identical to Cycle 1 (see Cycle-1 brief for full rationale):

  remediation.kind ∈ {"auto", "targeted", "advisory"}
  remediation.action (kind=auto only) ∈ {"reassemble", "rerun_validate"}
  remediation.command (kind=targeted) — the exact one-stage cmd.

DETECTION vs REMEDIATION. This module is the detection half ONLY.
It is pure: no filesystem mutation. The remediation half lives in
`finalize_deliverable.py`; the orchestrator invokes detection,
optionally remediation + re-detection, then halts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Sibling-tool imports loaded inside gate functions (validate_manuscript
# is heavy; user_intent is small) — defer to keep import-time light.
_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))


SCHEMA_VERSION = "deliverable-validation.v1"

# Severity vocabulary — frozen; projectable telemetry token.
# Identical to beril-presentation-maker v1.2.0.
SEVERITY_P0 = "P0"
SEVERITY_P1 = "P1"
SEVERITY_ADVISORY = "advisory"
SEVERITIES = (SEVERITY_P0, SEVERITY_P1, SEVERITY_ADVISORY)

# Gate vocabulary — frozen; projectable telemetry token.
# Paper-writer has its own set (no slide/image gates), but the shape
# matches Cycle 1's: gate names are kebab-case identifiers.
GATES = (
    "section_completeness",
    "placeholder_or_leaked_template",
    "figure_resolution_and_embedding",
    "mode_depth_vs_user_intent",
)

# Remediation-kind vocabulary — frozen; identical to Cycle 1.
REMEDIATION_AUTO = "auto"
REMEDIATION_TARGETED = "targeted"
REMEDIATION_ADVISORY = "advisory"
REMEDIATION_KINDS = (REMEDIATION_AUTO, REMEDIATION_TARGETED, REMEDIATION_ADVISORY)

# Auto-action vocabulary (only meaningful when kind == "auto").
# `reassemble` = re-run assemble_docx; `rerun_validate` = re-run
# validate_manuscript so its ValidationReport is fresh. NEITHER
# mutates the manuscript — paper-writer auto-remediations are
# safe-by-construction. (Cycle-1 G1 lesson: no fuzzy auto-mutation.)
AUTO_REASSEMBLE = "reassemble"
AUTO_RERUN_VALIDATE = "rerun_validate"


# ---------------------------------------------------------------------------
# Schema dataclasses (shape identical to beril-presentation-maker)
# ---------------------------------------------------------------------------


@dataclass
class Remediation:
    """How (if at all) finalize_deliverable should respond to a finding."""
    kind: str                       # REMEDIATION_KINDS
    action: str | None = None       # auto-action key (kind=auto)
    command: str | None = None      # exact one-stage cmd (kind=targeted)
    note: str | None = None         # operator-readable hint

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "action": self.action,
            "command": self.command,
            "note": self.note,
        }


@dataclass
class Finding:
    """One deliverable-validation finding."""
    id: str                                # stable per-finding id (gate:slot)
    gate: str                              # GATES
    severity: str                          # SEVERITIES
    slide_id_or_target: str | None         # 'manuscript' | 'docx' | section
                                           # name | None. (Field name retained
                                           # for cross-skill schema parity;
                                           # paper-writer never produces a
                                           # slide_id but uses the slot for
                                           # the section name or 'manuscript'.)
    message: str                           # human-readable, free-text
    remediation: Remediation

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "gate": self.gate,
            "severity": self.severity,
            "slide_id_or_target": self.slide_id_or_target,
            "message": self.message,
            "remediation": self.remediation.to_dict(),
        }


# ---------------------------------------------------------------------------
# Path / project helpers
# ---------------------------------------------------------------------------


def _derive_project_dir(draft_dir: Path) -> Path | None:
    """draft_N/ -> project_dir (../../). None when layout doesn't match
    `projects/<id>/papers/draft_N/`."""
    parts = draft_dir.resolve().parts
    if len(parts) < 4:
        return None
    if parts[-2] != "papers":
        return None
    return Path(*parts[:-2])


def _project_dir_token(draft_dir: Path) -> str:
    project_dir = _derive_project_dir(draft_dir)
    return project_dir.name if project_dir else ""


def _read_state_mode(draft_dir: Path) -> str | None:
    """Read state.json's `mode` field (paper|report). None on absent /
    malformed / missing key."""
    state_path = draft_dir / "state.json"
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = data.get("mode") if isinstance(data, dict) else None
    return mode if isinstance(mode, str) else None


def _load_manuscript_md(draft_dir: Path) -> str | None:
    """Read draft_dir/manuscript.md. None if absent."""
    p = draft_dir / "manuscript.md"
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# G1 section_completeness — wrap validate_manuscript
# ---------------------------------------------------------------------------

# Map validate_manuscript's escalation_path -> our remediation.kind +
# (optional) auto-action. Reasoning:
#   auto-fix              -> auto / rerun_validate
#       validate_manuscript marks something the orchestrator could fix
#       deterministically. We do NOT mutate the manuscript from the
#       gate (that's the existing auto-fix path inside the validators,
#       upstream). All we can safely do at the deliverable gate is
#       re-run validate_manuscript so a fresh ValidationReport reflects
#       any operator changes between checks. Conservative by design.
#   escalate              -> targeted
#       Validator says "this needs operator action via a specific stage"
#       — emit a targeted finding so finalize surfaces the cmd.
#   user-modify           -> targeted
#       Operator rewrite. No safe auto-mutation.
#   accept-as-limitation  -> advisory
#       Recorded, not blocking.
_ESCALATION_TO_REMEDIATION: dict[str, tuple[str, str | None]] = {
    "auto-fix":             (REMEDIATION_AUTO, AUTO_RERUN_VALIDATE),
    "escalate":             (REMEDIATION_TARGETED, None),
    "user-modify":          (REMEDIATION_TARGETED, None),
    "accept-as-limitation": (REMEDIATION_ADVISORY, None),
}

# Map Violation.severity -> our SEVERITIES.
_VIOLATION_TO_SEVERITY: dict[str, str] = {
    "error":   SEVERITY_P0,
    "warning": SEVERITY_P1,
}


def check_g1_section_completeness(
    draft_dir: Path, mode: str,
) -> list[Finding]:
    """G1: project validate_manuscript's M1–M10 ValidationReport onto
    deliverable-validation.v1.

    DO NOT reimplement M1–M10. We import the existing module, run it,
    and re-emit its Violations as Findings. Bugs in the section logic
    are fixed in validate_manuscript; this gate is the projection only.
    """
    findings: list[Finding] = []
    try:
        import validate_manuscript as vm  # noqa: E402
    except Exception as exc:  # pragma: no cover — defensive
        return [Finding(
            id="g1:validate_manuscript_import_failed",
            gate=GATES[0],
            severity=SEVERITY_P1,
            slide_id_or_target="manuscript",
            message=(
                f"G1: could not import validate_manuscript ({exc!r}); "
                f"skipping section-completeness check."
            ),
            remediation=Remediation(
                kind=REMEDIATION_ADVISORY,
                note="Engine-side bug; not auto-remediable.",
            ),
        )]

    try:
        report = vm.run_all_validators(draft_dir, mode=mode)
    except Exception as exc:  # pragma: no cover
        return [Finding(
            id="g1:validate_manuscript_crashed",
            gate=GATES[0],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                f"G1: validate_manuscript.run_all_validators crashed "
                f"({exc!r}); cannot project section findings."
            ),
            remediation=Remediation(
                kind=REMEDIATION_ADVISORY,
                note="Inspect logs; not auto-remediable.",
            ),
        )]

    # v1.2.0 followup (Cowork verification, 2026-06-07): calibrate
    # the projection BEFORE the per-violation emit loop. The raw
    # M1-M10 pass produces a mix of "real" deliverable-blocking
    # findings and "structured-paper-prescriptivism" findings that
    # don't match how real manuscripts handle their content. The
    # adjustments below match Adam's calibration:
    #
    # 1. M1 missing-title: validate_manuscript looks for a section
    #    literally named "Title" / "Title Page" via
    #    PAPER_REQUIRED_SECTIONS['title']. A real manuscript whose
    #    title is the leading H1 + an author block IS a valid title
    #    page; we must NOT P0 on every H1-titled paper. Detect the
    #    leading H1 (via _extract_title_line) and SUPPRESS the M1
    #    title violation when present.
    # 2. M2 structured-abstract subsections: a paper-mode manuscript
    #    with a prose abstract (one paragraph, no bold subheadings)
    #    is valid; ICMJE-strict structured-subsection enforcement
    #    is a publishing-house style preference, not a deliverable
    #    completeness check. DEMOTE to P1 + advisory (no command).
    # 3. M9 limitations: many real papers handle limitations in-
    #    prose within the discussion section rather than as a
    #    dedicated `## Limitations` heading. DEMOTE to P1 + advisory.
    # 4. M3 ai_disclosure + M4 data_availability: these ARE
    #    deliverable-blocking (ICMJE requires both for publishable
    #    work). LEAVE as P0/auto.
    #
    # The calibration is per-validator-id (not per-violation-string)
    # so a future M1 change that adds new violation classes won't
    # silently slip through. Anything not in this calibration table
    # falls through to the default escalation-path mapping.
    manuscript_md = _load_manuscript_md(draft_dir)
    has_h1_title = (
        manuscript_md is not None
        and _extract_title_line(manuscript_md) is not None
    )

    def _projected(viol, validator_id: str) -> tuple[str, str, str | None]:
        """Return (severity, kind, action) for one Violation after the
        calibration above. Helper inlined here so the per-violation
        emit loop stays readable; the table is small enough not to
        warrant a module-level dict."""
        # M1 — H1 + author block IS a title page; suppress signaled
        # by returning (None, ...) which the caller skips.
        if (validator_id == "M1"
                and has_h1_title
                and "'title'" in viol.message):
            return ("__suppress__", "", None)
        # M2 / M9 demotion: P0 -> P1 + advisory.
        if validator_id in ("M2", "M9") and viol.severity == "error":
            return (SEVERITY_P1, REMEDIATION_ADVISORY, None)
        # Default: original mapping.
        severity = _VIOLATION_TO_SEVERITY.get(viol.severity, SEVERITY_P1)
        kind, action = _ESCALATION_TO_REMEDIATION.get(
            viol.escalation_path,
            (REMEDIATION_TARGETED, None),
        )
        return (severity, kind, action)

    for validator in report.validators:
        # Only emit Findings for actual violations. pass /
        # not-applicable validators are silent — they're recorded in
        # the underlying ValidationReport (read-if-present pattern).
        for v_idx, viol in enumerate(validator.violations):
            severity, kind, action = _projected(viol, validator.id)
            if severity == "__suppress__":
                continue  # H1-title-suppression branch (M1)
            findings.append(Finding(
                id=f"g1:{validator.id.lower()}_{viol.severity}_{v_idx}",
                gate=GATES[0],
                severity=severity,
                slide_id_or_target=viol.section or "manuscript",
                message=(
                    f"G1/{validator.id} ({validator.name}): "
                    f"{viol.message}"
                    + (f"  [section: {viol.section}]" if viol.section else "")
                    + (f"  [line: {viol.line}]" if viol.line else "")
                ),
                remediation=Remediation(
                    kind=kind,
                    action=action,
                    command=(
                        # For targeted findings, emit a generic re-run
                        # cmd. The Violation message itself carries the
                        # detail the operator needs; we just point at
                        # the right replay surface.
                        f"beril-paper-writer continue {draft_dir}"
                        if kind == REMEDIATION_TARGETED else None
                    ),
                    note=(
                        f"escalation_path={viol.escalation_path!r}. "
                        f"See validate_manuscript M-series docs in SPEC §7.1."
                    ),
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# G2 placeholder_or_leaked_template
# ---------------------------------------------------------------------------

# Placeholder vocabulary. v1.2.0 followup (Cowork verification,
# 2026-06-07): the original detector only matched `\bTBD\b`, which
# under-fires on real LLM-emitted templates. The caulobacter draft_2
# shipped with "**Authors:** [AUTHOR LIST TO BE COMPLETED]" /
# "**Affiliations:** [TO BE COMPLETED]" — both invisible to a TBD-
# only check. Broaden to the full set of in-the-wild bracketed +
# template-language placeholders:
#
#   TBD / tbd                                       (the original)
#   TK                                              (publishing shorthand)
#   TO BE COMPLETED                                 (caulobacter draft_2)
#   AUTHOR LIST TO BE COMPLETED                     (literal LLM output)
#   FILL IN / TO BE FILLED                          (common prompt phrasing)
#   PLACEHOLDER                                     (verbatim)
#   XXX / XXXX                                      (placeholder convention)
#   [...] / […]                                     (bracketed ellipsis)
#
# Each pattern is anchored with \b on alphabetic forms; bracketed
# forms ([TO BE COMPLETED], [TK], [...]) are matched via a separate
# `_BRACKETED_PLACEHOLDER_RE` so a bracket WITHOUT a placeholder
# inside (a legitimate `[Smith 2024]` citation, an `[A]` annotation)
# doesn't false-positive. Substring match on the alphabetic forms
# inside brackets is intentional — `[TO BE COMPLETED]` and
# `[AUTHOR LIST TO BE COMPLETED]` both fire via the same vocab.
_PLACEHOLDER_PATTERNS = (
    r"TBD",
    r"TK",
    r"TO\s+BE\s+COMPLETED",
    r"TO\s+BE\s+FILLED",
    r"FILL\s+IN",
    r"PLACEHOLDER",
    r"XXX+",            # XXX, XXXX, XXXXX, ...
)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:" + "|".join(_PLACEHOLDER_PATTERNS) + r")\b",
    re.IGNORECASE,
)
# Bracketed-ellipsis catches `[...]` and the unicode `[…]` — these
# are unambiguous "fill this in" placeholders even without the
# alphabetic vocab above.
_BRACKETED_ELLIPSIS_RE = re.compile(r"\[\s*(?:\.{3,}|…)\s*\]")

# Author/affiliation value patterns that are placeholder-by-shape:
# empty value after the label, OR an EMPTY bracketed value (e.g.
# `Authors: []`, `Authors: [ ]`). A bracketed value containing real
# content goes through _PLACEHOLDER_RE / _BRACKETED_ELLIPSIS_RE.
_EMPTY_BRACKETED_RE = re.compile(r"^\s*\[\s*\]\s*$")


# Legacy export: many tests + handlers reference `_TBD_RE` directly.
# Keep it as an alias to _PLACEHOLDER_RE so the broadened vocab
# applies at every reading site without renaming the symbol. The
# alphabetic vocab is a superset of the prior `\bTBD\b` pattern.
_TBD_RE = _PLACEHOLDER_RE


def _is_tbd_value(value: str | None) -> bool:
    """True iff value is None/blank, contains a placeholder token
    (TBD/TK/TO BE COMPLETED/…), contains a bracketed ellipsis, or is
    an empty bracketed pair."""
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    return bool(
        _EMPTY_BRACKETED_RE.match(stripped)
        or _PLACEHOLDER_RE.search(stripped)
        or _BRACKETED_ELLIPSIS_RE.search(stripped)
    )


def _contains_dirname_token(
    value: str | None, dirname_token: str,
) -> bool:
    """Strict dirname-leak detector. Ported from
    beril-presentation-maker v1.2.0 with the same semantics — the
    Cycle-1 G1 followup (Adam, 2026-06-07) narrowed this rule after
    the broader version mis-fired on correct titles like "Iron
    regulation in Caulobacter crescentus" and would have led the
    auto-strip path to delete the organism name.

    True iff `value` contains EITHER

      (a) the verbatim full dir-name as substring (case-insensitive),
          e.g. `caulobacter_fur_lipida_loss` literally appears, OR
      (b) at least TWO ADJACENT dir-segments together with any
          whitespace/separator between them — e.g. dir
          `caulobacter_fur_lipida_loss` matches "lipida loss" or
          "fur lipida loss" or "Caulobacter-fur" in the value.

    A single dir-segment word like "Caulobacter" or "Loss" must NOT
    match. The cost of a false-negative here is one finding the
    operator would have wanted but didn't get; we trade that for not
    destroying correct titles.
    """
    if not value or not dirname_token:
        return False
    norm_value = value.lower()
    norm_token = dirname_token.lower()

    if norm_token in norm_value:
        return True

    segments = [s for s in re.split(r"[_\-.\s]+", norm_token) if s]
    if len(segments) < 2:
        return False
    norm_value_collapsed = re.sub(r"[_\-.\s]+", " ", norm_value)
    for i in range(len(segments) - 1):
        for n in range(2, len(segments) - i + 1):
            window = " ".join(segments[i:i + n])
            if window in norm_value_collapsed:
                return True
    return False


def _extract_title_line(manuscript_md: str) -> str | None:
    """Return the first `# <title>` H1 in the manuscript, or None.

    Manuscript convention: a single H1 heading at the top is the
    manuscript title (paper-writer prompts emit this shape). If no H1
    exists, we have no title to check — caller treats that as missing.
    """
    for raw_line in manuscript_md.splitlines():
        line = raw_line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
        if line.startswith("##"):
            # Reached a non-H1 heading before any H1 — give up.
            return None
    return None


# Author / affiliation label patterns. paper-writer's manuscript
# convention can emit several shapes:
#   "Authors: …"                   bare label
#   "**Authors:** …"               bold label (markdown emphasis)
#   "_Authors:_ …"                 italic label
#   "Author: …"                    singular form
#   "By: …"                        casual form
# Trailing colon optional. Bold/italic markers are absorbed so the
# extracted VALUE doesn't include them — that lets _is_tbd_value
# correctly match an empty-after-the-label case (e.g. caulobacter's
# `**Authors:** [AUTHOR LIST TO BE COMPLETED]` extracts to
# `[AUTHOR LIST TO BE COMPLETED]`, which fires via the broadened
# _PLACEHOLDER_RE).
# Label-line regex shape:
#   ^                          start
#   \s*                        optional indent
#   [*_]{0,2}                  leading bold/italic markers (0..2 chars)
#   (?:Authors?|By)            the label word
#   [*_]{0,2}                  trailing emphasis on label (uncommon)
#   \s*:?\s*                   optional colon
#   [*_]{0,2}                  trailing bold/italic markers AFTER colon
#                              (caulobacter shape: `**Authors:** value`)
#   (.*?)                      captured value
#   [*_]{0,2}                  trailing bold/italic markers at line end
#   \s*$                       end
# The two `[*_]{0,2}` groups around the capture are critical — they
# strip the bold pair so the captured value is the actual content
# the user typed, not contaminated with stray `**`.
_AUTHOR_LABEL_RE = re.compile(
    r"^\s*[*_]{0,2}(?:Authors?|By)[*_]{0,2}\s*:?\s*[*_]{0,2}\s*"
    r"(.*?)"
    r"\s*[*_]{0,2}\s*$",
    re.IGNORECASE,
)
# Affiliation label — same shape. New in the v1.2.0 followup
# (Cowork verification, 2026-06-07): the original detector ignored
# affiliations entirely, so `**Affiliations:** [TO BE COMPLETED]`
# slid through unflagged. Affiliations are part of the author block;
# a placeholder affiliation is the same defect class as a placeholder
# author.
_AFFILIATION_LABEL_RE = re.compile(
    r"^\s*[*_]{0,2}(?:Affiliations?)[*_]{0,2}\s*:?\s*[*_]{0,2}\s*"
    r"(.*?)"
    r"\s*[*_]{0,2}\s*$",
    re.IGNORECASE,
)


def _extract_labeled_value(
    manuscript_md: str, label_re: re.Pattern[str],
) -> str | None:
    """Generic helper: scan the first 30 lines for a label match;
    return the captured value (or None if the line is present-but-
    empty after the label, or no line matched at all).

    "Present but empty" still returns None — callers distinguish
    "label missing entirely" from "label present with empty value"
    via the parallel `_label_line_present` helper below. (G2 wants
    to flag BOTH as different findings: `_missing` and `_tbd`.)
    """
    for raw_line in manuscript_md.splitlines()[:30]:
        m = label_re.match(raw_line)
        if m:
            value = m.group(1).strip()
            return value or None
    return None


def _label_line_present(
    manuscript_md: str, label_re: re.Pattern[str],
) -> bool:
    """True iff the label line appears in the first 30 lines at all
    — regardless of whether the value is populated. Used to split
    "missing" (no label line) from "TBD/empty" (label line with no
    real value)."""
    return any(
        label_re.match(raw_line)
        for raw_line in manuscript_md.splitlines()[:30]
    )


def _extract_author_line(manuscript_md: str) -> str | None:
    """Find the author line value near the top of the manuscript."""
    return _extract_labeled_value(manuscript_md, _AUTHOR_LABEL_RE)


def _extract_affiliation_line(manuscript_md: str) -> str | None:
    """Find the affiliation line value near the top of the manuscript."""
    return _extract_labeled_value(manuscript_md, _AFFILIATION_LABEL_RE)


def check_g2_placeholder_or_leaked_template(
    draft_dir: Path, manuscript_md: str | None,
) -> list[Finding]:
    """G2: no TBD in title/authors; no project-slug leak in title."""
    if manuscript_md is None:
        return [Finding(
            id="g2:no_manuscript",
            gate=GATES[1],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                "G2: draft_dir/manuscript.md not found; cannot validate "
                "title/author fields."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Re-run the pipeline so the holistic-draft phase "
                    "produces manuscript.md."
                ),
            ),
        )]

    findings: list[Finding] = []
    dirname_token = _project_dir_token(draft_dir)

    # Title.
    title = _extract_title_line(manuscript_md)
    if title is None:
        findings.append(Finding(
            id="g2:title_missing",
            gate=GATES[1],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                "G2: manuscript.md has no top-level H1 title heading "
                "(`# Title`). The render will produce an untitled docx."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Re-run drafting so the LLM emits a proper title. "
                    "If manuscript.md is hand-edited, add a `# Title` "
                    "as the first heading."
                ),
            ),
        ))
    elif _is_tbd_value(title):
        findings.append(Finding(
            id="g2:title_tbd",
            gate=GATES[1],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                f"G2: manuscript title is TBD/blank (got: {title!r})."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Title is LLM-written from the manuscript prompts "
                    "+ project context; no deterministic source to "
                    "auto-populate from. Operator should either edit "
                    "manuscript.md directly or re-run drafting."
                ),
            ),
        ))
    elif _contains_dirname_token(title, dirname_token):
        findings.append(Finding(
            id="g2:title_dirname_leak",
            gate=GATES[1],
            severity=SEVERITY_P1,
            slide_id_or_target="manuscript",
            message=(
                f"G2: manuscript title contains the project-directory "
                f"slug (dirname={dirname_token!r}, title={title!r}). "
                f"The match is strict — either the full slug or "
                f"≥2 adjacent segments — so this is almost certainly "
                f"an LLM hallucination of the dir-name as science "
                f"terminology. NOT auto-remediated: stripping words "
                f"from a real title via fuzzy match is too destructive "
                f"(Cycle-1 G1 lesson, 2026-06-07); operator rewrites."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                note=(
                    f"Edit draft_dir/manuscript.md: rewrite the top-"
                    f"level `# Title` to a publication-ready phrasing "
                    f"that does not echo the project-slug segments. "
                    f"Then re-run `beril-paper-writer assemble "
                    f"{draft_dir}` to refresh the docx."
                ),
            ),
        ))

    # Authors. v1.2.0 followup (Cowork verification, 2026-06-07):
    # distinguish "no Authors: label at all" (P1 missing) from "label
    # present with placeholder value" (P0 placeholder) — the real
    # caulobacter draft_2 shipped with the second shape, and lumping
    # them under "missing" let it slide. Use _label_line_present to
    # split the two cases; _extract_author_line returns None for
    # both, so check the label-present sentinel first.
    authors_label_present = _label_line_present(
        manuscript_md, _AUTHOR_LABEL_RE,
    )
    authors = _extract_author_line(manuscript_md)
    if not authors_label_present:
        findings.append(Finding(
            id="g2:authors_missing",
            gate=GATES[1],
            severity=SEVERITY_P1,
            slide_id_or_target="manuscript",
            message=(
                "G2: no author line found in the first 30 lines of "
                "manuscript.md (looked for `Authors:` / `Author:` / "
                "`By:` labels). Manuscript may render without "
                "attribution."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Authors are LLM-written from beril.yaml + the "
                    "drafting prompts. If beril.yaml has authors, "
                    "re-run drafting; otherwise add manually."
                ),
            ),
        ))
    elif _is_tbd_value(authors):
        findings.append(Finding(
            id="g2:authors_tbd",
            gate=GATES[1],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                f"G2: author line is a placeholder/blank (got: "
                f"{authors!r}). Matches the broadened placeholder "
                f"vocabulary (TBD / TK / TO BE COMPLETED / "
                f"PLACEHOLDER / FILL IN / XXX / `[...]` / bare `[]`)."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Same path as authors_missing: re-run drafting or "
                    "hand-edit manuscript.md."
                ),
            ),
        ))

    # Affiliations. v1.2.0 followup (2026-06-07): caulobacter draft_2
    # shipped "**Affiliations:** [TO BE COMPLETED]" — the original
    # detector ignored affiliations entirely. We treat a placeholder
    # affiliation the same way as a placeholder author (P0); a missing
    # Affiliations: label is P1 (some manuscripts inline affiliations
    # into the author line — `Adam Arkin (LBNL)` — which is fine,
    # so absence is advisory rather than blocking).
    affiliations_label_present = _label_line_present(
        manuscript_md, _AFFILIATION_LABEL_RE,
    )
    affiliations = _extract_affiliation_line(manuscript_md)
    if affiliations_label_present and _is_tbd_value(affiliations):
        findings.append(Finding(
            id="g2:affiliations_tbd",
            gate=GATES[1],
            severity=SEVERITY_P0,
            slide_id_or_target="manuscript",
            message=(
                f"G2: affiliation line is a placeholder/blank "
                f"(got: {affiliations!r}). Matches the broadened "
                f"placeholder vocabulary."
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Affiliations are LLM-written from beril.yaml. "
                    "If beril.yaml has affiliations, re-run drafting; "
                    "otherwise add manually."
                ),
            ),
        ))

    # Body-wide TBD sweep. The body is everything from the first
    # `# Heading` down — we already checked the title above; we want
    # to surface any later "TBD - production placeholder" lines that
    # leaked through the holistic draft. Conservative: emit ONE
    # finding per slot, capped at 10 to avoid spamming.
    # Body-wide placeholder sweep. v1.2.0 followup: uses the broadened
    # _PLACEHOLDER_RE (alphabetic vocab) AND _BRACKETED_ELLIPSIS_RE
    # (`[...]` / `[…]`) so the same LLM-emitted templates Authors and
    # Affiliations catch above also surface when buried in body prose.
    body_hits: list[re.Match[str]] = list(_PLACEHOLDER_RE.finditer(manuscript_md))
    body_hits.extend(_BRACKETED_ELLIPSIS_RE.finditer(manuscript_md))
    body_hits.sort(key=lambda m: m.start())
    if body_hits:
        slots = []
        for m in body_hits[:10]:
            line_start = manuscript_md.rfind("\n", 0, m.start()) + 1
            line_end = manuscript_md.find("\n", m.end())
            if line_end == -1:
                line_end = len(manuscript_md)
            line = manuscript_md[line_start:line_end].strip()
            line_no = manuscript_md.count("\n", 0, m.start()) + 1
            slots.append((line_no, line[:120]))
        findings.append(Finding(
            id="g2:placeholder_in_body",
            gate=GATES[1],
            severity=SEVERITY_P1,
            slide_id_or_target="manuscript",
            message=(
                f"G2: {len(body_hits)} placeholder token(s) in "
                f"manuscript.md (TBD / TK / TO BE COMPLETED / "
                f"PLACEHOLDER / FILL IN / XXX / `[...]`; first "
                f"{len(slots)} shown):\n  "
                + "\n  ".join(f"L{n}: {ln}" for n, ln in slots)
            ),
            remediation=Remediation(
                kind=REMEDIATION_TARGETED,
                command=f"beril-paper-writer continue {draft_dir}",
                note=(
                    "Each placeholder is a token the drafting LLM "
                    "left for operator follow-through. Edit "
                    "manuscript.md to resolve or re-run drafting on "
                    "the affected sections."
                ),
            ),
        ))

    return findings


# ---------------------------------------------------------------------------
# G3 figure_resolution_and_embedding
# ---------------------------------------------------------------------------

# Block-image regex from assemble_docx.py (line-only `![alt](path)`).
_BLOCK_IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")


def _extract_figure_refs(manuscript_md: str) -> list[tuple[int, str, str]]:
    """Return (line_no, alt, path) for every block-image in the
    manuscript. Mirrors assemble_docx's parser: only line-only image
    tags count; inline images aren't embedded."""
    refs: list[tuple[int, str, str]] = []
    for i, line in enumerate(manuscript_md.splitlines(), start=1):
        m = _BLOCK_IMAGE_RE.match(line)
        if m:
            refs.append((i, m.group(1), m.group(2)))
    return refs


def _resolve_figure_path(rel_path: str, draft_dir: Path) -> Path | None:
    """Mirror of assemble_docx's render_image lookup:
      - reject absolute or parent-relative paths;
      - resolve under draft_dir;
      - fall back to project_dir (the standard layout — REPORT.md
        figures live at `<proj>/figures/X.png`).
    """
    p = Path(rel_path)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    cand = draft_dir / p
    if cand.is_file():
        return cand
    project_dir = _derive_project_dir(draft_dir)
    if project_dir is not None:
        cand_proj = project_dir / p
        if cand_proj.is_file():
            return cand_proj
    return None


def check_g3_figure_resolution_and_embedding(
    draft_dir: Path, manuscript_md: str | None,
) -> list[Finding]:
    """G3: every block-image in manuscript.md resolves to a real file
    AND (when manuscript.docx exists) is embedded in the docx."""
    if manuscript_md is None:
        return []  # G2 already complained about the missing manuscript

    findings: list[Finding] = []
    refs = _extract_figure_refs(manuscript_md)
    if not refs:
        return findings  # no figures referenced; nothing to validate

    # Part 1: on-disk resolution.
    resolved_paths: list[Path] = []
    for line_no, _alt, path in refs:
        resolved = _resolve_figure_path(path, draft_dir)
        if resolved is None:
            findings.append(Finding(
                id=f"g3:unresolved_figure:L{line_no}",
                gate=GATES[2],
                severity=SEVERITY_P0,
                slide_id_or_target="manuscript",
                message=(
                    f"G3: manuscript.md L{line_no} references figure "
                    f"{path!r} which does not resolve under draft_dir/ "
                    f"or project_dir/."
                ),
                remediation=Remediation(
                    kind=REMEDIATION_TARGETED,
                    note=(
                        "Either fix the path in manuscript.md (the "
                        "common case — LLM-emitted relative paths "
                        "sometimes typo the figure stem) or restore "
                        "the missing file. Not auto-remediable; we "
                        "can't guess the intended figure."
                    ),
                ),
            ))
        else:
            resolved_paths.append(resolved)

    # Part 2: docx embedding count. python-docx lets us count
    # inline pictures by walking the body; we don't need byte-
    # comparison, just "did N references produce N+ embeds."
    docx_path = draft_dir / "manuscript.docx"
    if not docx_path.is_file():
        # No docx yet (e.g. validate ran before assemble) — skip; the
        # finalize step will trigger reassemble if needed.
        return findings

    try:
        from docx import Document  # type: ignore[import-untyped]
    except ImportError:
        findings.append(Finding(
            id="g3:python_docx_unavailable",
            gate=GATES[2],
            severity=SEVERITY_ADVISORY,
            slide_id_or_target="docx",
            message=(
                "G3: python-docx unavailable; skipping the embed-count "
                "check (on-disk resolution check was performed)."
            ),
            remediation=Remediation(kind=REMEDIATION_ADVISORY),
        ))
        return findings

    try:
        doc = Document(str(docx_path))
        # Count Picture-like shapes. python-docx exposes inline shapes
        # via doc.inline_shapes; that's the right count for our embeds
        # (assemble_docx.render_image emits a centered inline shape
        # per block-image).
        n_embedded = len(doc.inline_shapes)
    except Exception as exc:  # pragma: no cover
        findings.append(Finding(
            id="g3:docx_unreadable",
            gate=GATES[2],
            severity=SEVERITY_P1,
            slide_id_or_target="docx",
            message=f"G3: cannot read manuscript.docx ({exc!r}).",
            remediation=Remediation(
                kind=REMEDIATION_AUTO,
                action=AUTO_REASSEMBLE,
                note="Re-run assemble.",
            ),
        ))
        return findings

    # Expect at least as many embeds as resolved references. If we
    # have stale image-on-disk failures in the markdown (Part 1
    # findings), assemble_docx logs WARN-and-continues, so the count
    # mismatch is real signal.
    n_resolved = len(resolved_paths)
    if n_embedded < n_resolved:
        findings.append(Finding(
            id="g3:embed_count_mismatch",
            gate=GATES[2],
            severity=SEVERITY_P0,
            slide_id_or_target="docx",
            message=(
                f"G3: manuscript.md references {n_resolved} resolvable "
                f"figure(s) but manuscript.docx embeds only "
                f"{n_embedded}. {n_resolved - n_embedded} figure(s) "
                f"were resolvable but did not land in the docx."
            ),
            remediation=Remediation(
                kind=REMEDIATION_AUTO,
                action=AUTO_REASSEMBLE,
                note=(
                    "Re-run assemble against the current "
                    "manuscript.md. If the mismatch persists after a "
                    "fresh assemble, there's a bug in "
                    "assemble_docx.render_image."
                ),
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# G4 mode_depth_vs_user_intent
# ---------------------------------------------------------------------------


def check_g4_mode_depth_vs_user_intent(draft_dir: Path) -> list[Finding]:
    """G4: persisted user_intent's mode/depth must match the produced
    artifacts. Missing/non-explicit user_intent → advisory only.
    Mirrors Cycle-1 Gate-4, adapted to paper-writer's surfaces:
      - mode source of truth in the run: state.json `mode`
        (validate_manuscript also records it on its report).
      - depth: paper-writer does not yet persist depth in any
        downstream artifact. We surface "user picked depth=X but
        nothing on disk records it" as advisory; the persistence
        layer alone closes the 80%-case (the user can READ
        user_intent.json to confirm their pick was captured).
    """
    findings: list[Finding] = []

    try:
        import user_intent  # noqa: E402
    except Exception:  # pragma: no cover
        return [Finding(
            id="g4:user_intent_import_failed",
            gate=GATES[3],
            severity=SEVERITY_ADVISORY,
            slide_id_or_target="manuscript",
            message="G4: could not load user_intent helper.",
            remediation=Remediation(kind=REMEDIATION_ADVISORY),
        )]

    # mode_explicit / mode read. user_intent.py is the COPIED
    # presentation-maker module (verbatim per llm_config copy-not-
    # share); its `tier` slot has a different vocabulary than paper-
    # writer's --depth, so depth is NOT persisted via user_intent
    # — it's tracked elsewhere (state.json) and the deliverable
    # validator surfaces it as a deferred-cycle advisory below.
    user_mode = user_intent.read_field(draft_dir, "mode")
    mode_explicit = user_intent.field_was_explicit(draft_dir, "mode")

    if user_mode is None:
        return [Finding(
            id="g4:no_user_intent",
            gate=GATES[3],
            severity=SEVERITY_ADVISORY,
            slide_id_or_target="manuscript",
            message=(
                "G4: audit/user_intent.json missing or no mode field — "
                "this draft was created before v1.2.0 or the user_intent "
                "write was skipped. Cannot validate mode against user intent."
            ),
            remediation=Remediation(
                kind=REMEDIATION_ADVISORY,
                note=(
                    "Legacy pre-v1.2.0 drafts won't have this file. "
                    "Future drafts get it automatically on draft entry."
                ),
            ),
        )]

    # Mode check — compare user's explicit pick to state.json's mode.
    if user_mode is not None and mode_explicit:
        state_mode = _read_state_mode(draft_dir)
        if state_mode is not None and state_mode != user_mode:
            findings.append(Finding(
                id="g4:state_mode_mismatch",
                gate=GATES[3],
                severity=SEVERITY_P0,
                slide_id_or_target="manuscript",
                message=(
                    f"G4: user_intent records explicit mode="
                    f"{user_mode!r} but state.json has mode="
                    f"{state_mode!r}. The DP9b-analogue: the orchestrator "
                    f"silently dropped --mode somewhere between CLI "
                    f"parse and persistence."
                ),
                remediation=Remediation(
                    kind=REMEDIATION_TARGETED,
                    command=(
                        f"beril-paper-writer continue {draft_dir} "
                        f"--mode {user_mode}"
                    ),
                    note=(
                        "Re-run from the relevant phase with the "
                        "explicit --mode. If mode shapes "
                        "manuscript.md (paper vs report templates), "
                        "the rerun must reach the drafting phase."
                    ),
                ),
            ))
        # Cross-check validate_manuscript's report.mode (if it was
        # run before us, the field is populated; if not, we'll skip
        # — G1 owns the validator re-run).
        report_path = draft_dir / "audit" / "validate_manuscript.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report_mode = report.get("mode")
                if (isinstance(report_mode, str)
                        and report_mode != user_mode):
                    findings.append(Finding(
                        id="g4:report_mode_mismatch",
                        gate=GATES[3],
                        severity=SEVERITY_P1,
                        slide_id_or_target="manuscript",
                        message=(
                            f"G4: validate_manuscript ran at mode="
                            f"{report_mode!r} but user_intent says "
                            f"{user_mode!r}. Section-presence "
                            f"requirements were evaluated for the "
                            f"wrong mode (paper requires Abstract, "
                            f"report has different IMRAD policy)."
                        ),
                        remediation=Remediation(
                            kind=REMEDIATION_AUTO,
                            action=AUTO_RERUN_VALIDATE,
                            note=(
                                "Re-run validate_manuscript with the "
                                "correct --mode; the deliverable "
                                "report refreshes."
                            ),
                        ),
                    ))
            except (OSError, json.JSONDecodeError):
                pass

    # Depth — paper-writer's --depth (quick/standard/deep) vocabulary
    # does not match user_intent.py's `tier` slot (STRONG/THIN/
    # EXPLORATORY, shared with presentation-maker), so depth is NOT
    # persisted via user_intent. The CLI accepts --depth and forwards
    # it to the orchestrator (Cycle-2 fix); the orchestrator currently
    # uses it only to modulate LLM tier choice at draft time per SPEC
    # §3.4 and does not echo it to any on-disk audit artifact. A real
    # depth-vs-output check (drafting-time vs. delivered) is deferred
    # to a future cycle when a depth-echo audit artifact exists. The
    # persistence-layer fix (CLI → orchestrator plumbing) is shipped
    # this cycle; it already closes the silent-drop class.

    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def validate(draft_dir: Path) -> list[Finding]:
    """Run all four gates against `draft_dir`. Pure: read-only over
    the filesystem. Returns the flat list of findings.

    Resolves the mode to use for G1 in this order:
      1. user_intent's explicit pick (DP9b — primary).
      2. state.json's mode.
      3. default 'paper'.
    """
    # Resolve mode.
    resolved_mode = "paper"
    try:
        import user_intent  # noqa: E402
        ui_mode = user_intent.read_field(draft_dir, "mode")
        if ui_mode is not None and user_intent.field_was_explicit(
                draft_dir, "mode"):
            resolved_mode = ui_mode
        elif _read_state_mode(draft_dir) is not None:
            resolved_mode = _read_state_mode(draft_dir) or "paper"
    except Exception:
        state_mode = _read_state_mode(draft_dir)
        if state_mode is not None:
            resolved_mode = state_mode

    manuscript_md = _load_manuscript_md(draft_dir)

    findings: list[Finding] = []
    findings.extend(check_g1_section_completeness(draft_dir, resolved_mode))
    findings.extend(check_g2_placeholder_or_leaked_template(
        draft_dir, manuscript_md))
    findings.extend(check_g3_figure_resolution_and_embedding(
        draft_dir, manuscript_md))
    findings.extend(check_g4_mode_depth_vs_user_intent(draft_dir))
    return findings


def write_findings(draft_dir: Path, findings: Iterable[Finding]) -> Path:
    """Persist findings to `audit/deliverable_validation.json`. Same
    envelope shape as beril-presentation-maker (cross-skill schema)."""
    findings_list = list(findings)
    summary = _summarize(findings_list)
    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / "deliverable_validation.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "draft_dir": str(draft_dir),
        "summary": summary,
        "findings": [f.to_dict() for f in findings_list],
    }
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out


def _summarize(findings: list[Finding]) -> dict:
    """Aggregate counters; telemetry-ready (token keys)."""
    by_gate: dict[str, int] = {g: 0 for g in GATES}
    by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
    by_kind: dict[str, int] = {k: 0 for k in REMEDIATION_KINDS}
    for f in findings:
        by_gate[f.gate] = by_gate.get(f.gate, 0) + 1
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_kind[f.remediation.kind] = by_kind.get(f.remediation.kind, 0) + 1
    return {
        "total": len(findings),
        "by_gate": by_gate,
        "by_severity": by_severity,
        "by_remediation_kind": by_kind,
        "blocking": by_severity.get(SEVERITY_P0, 0),
    }


def readiness_exit_code(findings: list[Finding]) -> int:
    """Map findings to a readiness exit code:
      0 — clean OR only advisory findings (deliverable ready to hand off).
      1 — at least one P0 or P1 finding; remediation needed. The
          never-discard policy means the deliverable is STILL produced.
    """
    for f in findings:
        if f.severity in (SEVERITY_P0, SEVERITY_P1):
            return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace) -> int:
    draft_dir = Path(args.draft_dir).resolve()
    if not draft_dir.is_dir():
        print(
            f"validate_deliverable: draft_dir not found: {draft_dir}",
            file=sys.stderr,
        )
        return 2
    findings = validate(draft_dir)
    out = write_findings(draft_dir, findings)
    summary = _summarize(findings)
    print(
        f"validate_deliverable: {summary['total']} finding(s) "
        f"(P0={summary['by_severity'].get(SEVERITY_P0, 0)}, "
        f"P1={summary['by_severity'].get(SEVERITY_P1, 0)}, "
        f"advisory={summary['by_severity'].get(SEVERITY_ADVISORY, 0)}); "
        f"wrote {out}",
        file=sys.stderr,
    )
    if args.print_findings and findings:
        for f in findings:
            print(
                f"  [{f.severity}] {f.gate} ({f.id}): {f.message}",
                file=sys.stderr,
            )
            cmd = f.remediation.command
            note = f.remediation.note
            if cmd:
                print(f"      → run: {cmd}", file=sys.stderr)
            elif note:
                print(f"      → {f.remediation.kind}: {note}", file=sys.stderr)
    return readiness_exit_code(findings)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="validate_deliverable",
        description=(
            "Cycle 2 pre-handoff deterministic gate (paper-writer). "
            "Four checks over manuscript.md + manuscript.docx + "
            "working/audit artifacts; emits findings under the "
            "cross-skill deliverable-validation.v1 schema."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    p_chk = sub.add_parser(
        "check",
        help="Run all four gates; write audit/deliverable_validation.json.",
    )
    p_chk.add_argument("draft_dir")
    p_chk.add_argument(
        "--print-findings", action="store_true",
        help="Echo each finding to stderr in addition to writing the JSON.",
    )
    p_chk.set_defaults(func=_cmd_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
