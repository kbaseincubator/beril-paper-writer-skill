"""check_throughline_numerics.py — Stage 7 Patch 1b (2026-05-18).

Deterministic post-checker for ``plan.v1.md``'s emitted
``throughline_candidates.md``. Extracts every numeric token from each
``## Candidate TLN:`` block and verifies it against REPORT.md (and
claim_inventory.tsv when present). Unverified numerics are P0 in
strict mode — the orchestrator halts plan phase so the operator
never sees a candidate with an invented number.

Why this exists. The D1 (conservation_vs_fitness) live test on
2026-05-18 caught plan.v1 emitting "58.1% hypothetical" in TL1
when REPORT.md says "44.7% hypothetical". The 58.1 doesn't appear
anywhere in REPORT. Prompt-level anti-fabrication discipline alone
has been insufficient (same pattern observed previously in the
optimizer — Stage 1 Tier A). Mechanical post-checks have held;
this validator extends that pattern to plan.v1.

Scope (v1):
  - **DOES** extract every digit run in each Candidate block.
  - **DOES** check each against REPORT.md + claim_inventory.tsv
    (using the same generic source extractor as Tier T post Patch 2,
    so the verification semantics match downstream grounding).
  - **DOES NOT** check derived numerics (e.g., "4.9 pp difference"
    computed from 86.1 − 81.2). The prompt asks the LLM to include
    both source numbers in the row; if it only includes the derived
    value, the validator flags it as ungrounded. Operator decides.
  - **DOES** allowlist trivial small ints in non-claim contexts
    (e.g., "Candidate TL1:", "Phase 2", "Tier 3") so the validator
    doesn't false-positive on its own structure.

Output: ``<draft_dir>/audit/throughline_numeric_check.json`` with
schema ``throughline-numeric-check.v1``. Exit codes:
  - 0  → all candidates clean
  - 1  → setup error (file missing, etc.)
  - 2  → at least one ungrounded numeric found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = "throughline-numeric-check.v1"
TOOL_VERSION = "0.1.0-stage7-patch1b"

SEVERITY_P0 = "P0"

# Regex matching one Candidate TLN block. Greedy up to the next
# Candidate header or end of file.
_CANDIDATE_BLOCK_RE = re.compile(
    r"^## Candidate (TL[\w-]+):(.*?)(?=^## Candidate |\Z)",
    re.DOTALL | re.MULTILINE,
)

# Numeric token regex — match the same shapes Tier T's generic
# source extractor sees. Excludes the leading sign (we handle that
# separately in normalisation).
_NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

# Patterns whose digits should NOT be checked as data claims. These
# are structural / referential tokens — not numerics whose grounding
# we care about.
_ALLOWLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Candidate / throughline IDs.
    re.compile(r"TL[-_]?\w*\d+", re.IGNORECASE),
    # Citation key brackets like [Lloyd-Price2019], [Smith2024].
    # The key part can contain letters, digits, hyphens, underscores;
    # the year (or trailing label) is what carries the digits.
    re.compile(r"\[[A-Za-z][\w\-]*?\d+[\w\-]*\]"),
    # Section / phase references like "Phase 2", "Tier 3", "§4.5",
    # "Stage 1", "Pillar 4", "Hypothesis H1a".
    re.compile(
        r"\b(?:Phase|Tier|Stage|Pillar|Hypothesis|Section|"
        r"Chapter|H|Equation)\s*\d+[a-z]?\b",
        re.IGNORECASE,
    ),
    # Markdown header anchors / section numbering "§3.4".
    re.compile(r"§\s*\d+(?:\.\d+)?"),
    # Notebook cell refs: "cell 6", "notebook 04", "NB04", "NB01b".
    # No trailing \b — the next char is often "_" (e.g.,
    # "notebook 04_essential_conservation.ipynb") which is a
    # word-character, so \b would fail to anchor and the match
    # would fail entirely. Greedy match is sufficient because
    # _is_allowlisted only checks substring containment.
    re.compile(r"\b(?:cell|notebook|NB)\s*\d+[a-z]?", re.IGNORECASE),
    re.compile(r"\bNB\d+[a-z]?", re.IGNORECASE),
    # Standalone publication years (covered by citation_pool format
    # too but defensive).
    re.compile(r"\b(?:19\d\d|20\d\d)\b"),
    # FDR / q-value thresholds — these are statistical constants
    # not data claims (e.g., "q < 0.05", "alpha = 0.10"). The
    # specific cutoff isn't a fabrication risk.
    re.compile(r"\b(?:q|alpha|p)\s*[<≤]\s*0?\.\d+\b", re.IGNORECASE),
)


@dataclass
class ThroughlineFinding:
    """One unverified numeric in a Candidate block."""

    candidate_id:   str       # "TL1", "TL2", "TL-NARROWED", etc.
    numeric:        str       # the literal token
    normalized:     str       # canonicalised for matching
    surrounding:    str       # ~120-char context window
    severity:       str       # always P0 in v1 strict mode
    rationale:      str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThroughlineCheckReport:
    schema_version:    str
    tool:              str
    tool_version:      str
    candidates_path:   str
    report_path:       Optional[str]
    inventory_path:    Optional[str]
    totals:            dict
    findings:          list[dict]
    notes:             list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Candidate-block parser
# ---------------------------------------------------------------------------


@dataclass
class CandidateBlock:
    """One ``## Candidate TLN: ...`` block."""

    candidate_id: str  # e.g., "TL1"
    body: str          # full block text including the header line


def parse_candidates(text: str) -> list[CandidateBlock]:
    """Walk the candidates file and yield one CandidateBlock per
    ``## Candidate TLN:`` header. Returns [] if the file has no
    candidate blocks (e.g., the parser couldn't find headers — the
    caller treats that as "skip" not "fail")."""
    out: list[CandidateBlock] = []
    for m in _CANDIDATE_BLOCK_RE.finditer(text):
        candidate_id = m.group(1).strip()
        body = m.group(0)
        out.append(CandidateBlock(candidate_id=candidate_id, body=body))
    return out


# ---------------------------------------------------------------------------
# Numeric extraction with allowlist
# ---------------------------------------------------------------------------


def _is_allowlisted(numeric: str, context: str) -> bool:
    """Return True iff the numeric appears in a structural / referential
    context that shouldn't be checked as a data claim."""
    # Build a window around the numeric within context to test each pattern.
    for pat in _ALLOWLIST_PATTERNS:
        for m in pat.finditer(context):
            # If the numeric token sits inside any allowlist match's span,
            # it's structural.
            if numeric in m.group(0):
                return True
    return False


def extract_numerics_from_candidate(
    body: str,
) -> list[tuple[str, str, str]]:
    """Yield ``(numeric_token, normalized, surrounding)`` tuples for
    each numeric token in the Candidate body that doesn't match an
    allowlist pattern. The body is the full block text including
    the ``## Candidate TLN:`` header.

    Stage 7 Patch 1b follow-up (2026-05-18): strip thousand-separator
    commas before extraction. Without this, "177,863" matches twice
    as separate tokens "177" and "863", producing false positives
    against REPORT.md's normalized set (which already strips commas).
    Match the source-side normalisation discipline.

    Allowlist evaluation runs against the ORIGINAL body (with
    commas + word boundaries intact) so structural pattern
    recognition isn't broken.
    """
    # Strip commas between digits for tokenisation but keep the
    # original body for context windowing + allowlist evaluation.
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", body)
    out: list[tuple[str, str, str]] = []
    for m in _NUMERIC_RE.finditer(cleaned):
        numeric = m.group(0)
        # Map the match position back to the original body so the
        # context window is human-readable (commas preserved).
        # Since we only removed commas-between-digits, char positions
        # in `cleaned` precede or equal positions in `body`. Compute
        # the offset by counting commas-between-digits before m.start().
        prefix = cleaned[:m.start()]
        # Inverse mapping: count comma-between-digits in original body
        # up to the same character count of digits + non-digits.
        # Simpler approach: re-search the numeric in the original body
        # near the cleaned offset, accepting that surrounding context
        # will be approximate. For v1, use cleaned positions for
        # context — the operator can still read it.
        head = max(0, m.start() - 60)
        tail = min(len(cleaned), m.end() + 60)
        ctx = cleaned[head:tail].replace("\n", " ").strip()
        if _is_allowlisted(numeric, ctx):
            continue
        normalized = numeric.lower()
        out.append((numeric, normalized, ctx))
    return out


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------


def load_report_normalized_set(report_path: Optional[Path]) -> set[str]:
    """Load REPORT.md's normalized numeric set via the same generic
    extractor Tier T uses post-Patch-2. Empty set on missing input."""
    if report_path is None or not report_path.is_file():
        return set()
    # Late import — we don't want check_throughline_numerics to drag
    # in the full Tier T module if it's invoked in isolation.
    from beril_paper_writer.skill.tools import check_numeric_grounding
    return check_numeric_grounding.build_report_normalized_set(report_path)


def load_inventory_normalized_set(
    inventory_path: Optional[Path],
) -> set[str]:
    """Load claim_inventory.tsv's normalized numeric set. Empty set
    if the inventory doesn't yet exist (it's a phase_triage output;
    the validator runs at end of phase_plan which is after triage,
    so it should be present in normal flow)."""
    if inventory_path is None or not inventory_path.is_file():
        return set()
    from beril_paper_writer.skill.tools import check_numeric_grounding
    texts = check_numeric_grounding.load_inventory_claim_texts(
        inventory_path,
    )
    return check_numeric_grounding.build_inventory_normalized_set(texts)


# ---------------------------------------------------------------------------
# Pure-function comparator
# ---------------------------------------------------------------------------


def run_throughline_check(
    candidates_text: str,
    report_normalized: set[str],
    inventory_normalized: set[str],
) -> tuple[list[ThroughlineFinding], dict]:
    """Walk every Candidate block, extract numerics, check each against
    the union of source sets. Returns (findings, totals)."""
    findings: list[ThroughlineFinding] = []
    total = 0
    grounded = 0
    allowlisted = 0  # counted implicitly via extract_numerics_from_candidate
    by_candidate: dict[str, int] = {}

    candidates = parse_candidates(candidates_text)
    for cand in candidates:
        for numeric, normalized, ctx in extract_numerics_from_candidate(
            cand.body,
        ):
            total += 1
            # Normalize the manuscript-side number the same way the
            # source side normalizes (strip commas, lowercase, drop +).
            norm_payload = normalized.replace(",", "")
            if norm_payload.startswith("+"):
                norm_payload = norm_payload[1:]

            if norm_payload in inventory_normalized:
                grounded += 1
                continue
            if norm_payload in report_normalized:
                grounded += 1
                continue

            findings.append(ThroughlineFinding(
                candidate_id=cand.candidate_id,
                numeric=numeric,
                normalized=norm_payload,
                surrounding=ctx,
                severity=SEVERITY_P0,
                rationale=(
                    f"Numeric {numeric!r} in candidate "
                    f"{cand.candidate_id} not found in REPORT.md or "
                    "claim_inventory.tsv. Either verify the source and "
                    "include a verbatim quote in the evidence-map row, "
                    "or rephrase qualitatively (e.g., 'modest "
                    "enrichment' rather than '5% enrichment')."
                ),
            ))
            by_candidate[cand.candidate_id] = (
                by_candidate.get(cand.candidate_id, 0) + 1
            )

    totals = {
        "candidates_parsed":          len(candidates),
        "numerics_in_candidates":     total,
        "grounded":                   grounded,
        "ungrounded":                 len(findings),
        "ungrounded_by_candidate":    by_candidate,
    }
    return findings, totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates", required=True,
        help="Path to throughline_candidates.md.",
    )
    parser.add_argument(
        "--report", required=True,
        help="Path to REPORT.md.",
    )
    parser.add_argument(
        "--inventory", default=None,
        help=(
            "Path to claim_inventory.tsv. Optional — if absent, "
            "verification falls back to REPORT.md only."
        ),
    )
    parser.add_argument(
        "--out", required=True,
        help="Path to write throughline_numeric_check.json.",
    )
    args = parser.parse_args(argv)

    candidates_path = Path(args.candidates)
    report_path = Path(args.report)
    inventory_path = Path(args.inventory) if args.inventory else None
    out_path = Path(args.out)

    notes: list[str] = []
    if not candidates_path.is_file():
        print(
            f"error: throughline_candidates.md not found: {candidates_path}",
            file=sys.stderr,
        )
        return 1
    if not report_path.is_file():
        print(
            f"error: REPORT.md not found: {report_path}",
            file=sys.stderr,
        )
        return 1

    candidates_text = candidates_path.read_text(encoding="utf-8")
    report_norm = load_report_normalized_set(report_path)
    if inventory_path and not inventory_path.is_file():
        notes.append(
            f"claim_inventory.tsv at {inventory_path} not found — "
            "checking against REPORT.md only."
        )
        inventory_norm: set[str] = set()
    else:
        inventory_norm = load_inventory_normalized_set(inventory_path)

    findings, totals = run_throughline_check(
        candidates_text, report_norm, inventory_norm,
    )

    report = ThroughlineCheckReport(
        schema_version=SCHEMA_VERSION,
        tool="check_throughline_numerics",
        tool_version=TOOL_VERSION,
        candidates_path=str(candidates_path),
        report_path=str(report_path),
        inventory_path=(
            str(inventory_path) if inventory_path and inventory_path.is_file()
            else None
        ),
        totals=totals,
        findings=[f.to_dict() for f in findings],
        notes=notes,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(report), indent=2) + "\n",
        encoding="utf-8",
    )

    # Human-readable one-liner.
    print(
        f"check_throughline_numerics: "
        f"{totals['candidates_parsed']} candidates; "
        f"{totals['numerics_in_candidates']} numerics; "
        f"{totals['grounded']} grounded; "
        f"{totals['ungrounded']} UNGROUNDED → {out_path}"
    )
    if totals["ungrounded"] > 0:
        print(
            f"  ungrounded by candidate: "
            f"{totals['ungrounded_by_candidate']}",
            file=sys.stderr,
        )
        for f in findings[:5]:
            print(
                f"  {f.candidate_id}: {f.numeric!r} — "
                f"{f.surrounding[:80]!r}",
                file=sys.stderr,
            )
    return 0 if totals["ungrounded"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
