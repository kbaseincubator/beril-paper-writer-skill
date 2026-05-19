"""check_claim_markers.py — Stage 6 partial (v1-MVP, 2026-05-18).

Deterministic check that every [C-NNN] marker in the manuscript
resolves to a real `claim_id` in `claim_inventory.tsv`. This extends
the grounding chain past citation reality into per-claim provenance:
numeric grounding (Tier T) closed the *number* leak, this closes
the *claim-marker* leak.

Output: <draft_dir>/audit/claim_marker_check.json with schema
``claim-marker-check.v1`` and one finding per unresolved marker.
Severity is P1 in v1 (advisory — does not gate the P0 review);
promotes to P0 in v1.1 if multi-project data shows the failure
mode worth gating on.

Scope of v1:
  - **DOES** check: every [C-NNN] in manuscript.md → claim_inventory.tsv
    row exists.
  - **DOES NOT** check: every numeric in manuscript has a marker
    (reverse-direction; v1.1).
  - **DOES NOT** check: prose around [C-NNN] semantically matches the
    inventory row's claim_text (fuzzy match; v1.1).

Pattern mirrors check_numeric_grounding.py:
  - load_inventory_claim_ids()         — read TSV, return set of valid ids
  - extract_markers_from_manuscript()  — regex walk + paragraph metadata
  - run_marker_check()                 — pure function: compares the two
    sets, returns findings + summary
  - main()                             — CLI entry; writes the JSON
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = "claim-marker-check.v1"
TOOL_VERSION = "0.1.0-stage6-partial"

# Marker regex — `[C-NNN]` with at least one digit, no upper bound.
# Three digits is the convention in the live data (C-001 ... C-342)
# but we accept any digit run to keep the format extensible.
MARKER_RE = re.compile(r"\[(C-\d+)\]")

# Severity for unresolved markers in v1.
SEVERITY_UNRESOLVED = "P1"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MarkerFinding:
    """One unresolved [C-NNN] marker."""

    marker:          str        # the exact token, e.g. "C-999"
    section:         str        # canonicalised section label
    paragraph:       int        # 1-indexed paragraph within section
    char_offset:     int        # offset into manuscript.md
    surrounding:     str        # ~100 chars of context for human review
    severity:        str        # always P1 in v1
    rationale:       str        # human-readable explanation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarkerCheckReport:
    """Top-level JSON shape emitted to audit/claim_marker_check.json."""

    schema_version:        str
    tool:                  str
    tool_version:          str
    draft_dir:             str
    manuscript_path:       str
    inventory_path:        Optional[str]
    totals:                dict
    findings:              list[dict]
    notes:                 list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_inventory_claim_ids(inventory_path: Path) -> set[str]:
    """Read claim_inventory.tsv and return the set of claim_id values.

    Returns an empty set on missing or malformed input. The caller
    decides what 'empty inventory' means for the check (currently:
    the check still runs, every emitted marker is flagged unresolved).
    """
    if not inventory_path.is_file():
        return set()
    ids: set[str] = set()
    try:
        with inventory_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                cid = (row.get("claim_id") or "").strip()
                if cid:
                    ids.add(cid)
    except (OSError, csv.Error):
        return set()
    return ids


# ---------------------------------------------------------------------------
# Manuscript walker
# ---------------------------------------------------------------------------


def _canonicalize_heading(line: str) -> str:
    """Strip the leading `## ` and any trailing punctuation/numbering."""
    s = line.lstrip("#").strip()
    s = re.sub(r"[\s:.;,]+$", "", s)
    return s.lower()


def extract_markers_from_manuscript(
    manuscript_text: str,
) -> list[tuple[str, str, int, int, str]]:
    """Walk the manuscript and yield each [C-NNN] occurrence with metadata.

    Returns a list of tuples:
        (marker, section, paragraph_1indexed, char_offset, surrounding)

    `section` is the most-recent ``## <Heading>`` canonicalised.
    Markers before any heading attribute to "front-matter".
    `paragraph` resets to 1 at each new section heading and increments
    on each blank-line-separated block within the section.
    `surrounding` is up to 120 chars of context (60 before + the marker
    + 60 after) for human review of unresolved findings.
    """
    out: list[tuple[str, str, int, int, str]] = []
    section = "front-matter"
    paragraph = 1
    in_paragraph = False
    char_offset = 0

    for line in manuscript_text.splitlines(keepends=True):
        # Heading detection (## level).
        if line.lstrip().startswith("## "):
            section = _canonicalize_heading(line) or section
            paragraph = 1
            in_paragraph = False
        elif line.strip() == "":
            # Blank line: end the current paragraph.
            if in_paragraph:
                paragraph += 1
                in_paragraph = False
        else:
            # Non-empty body line.
            in_paragraph = True
            for m in MARKER_RE.finditer(line):
                marker = m.group(1)
                abs_offset = char_offset + m.start()
                # Surrounding context for human-readable findings.
                line_start_global = char_offset
                head = max(0, m.start() - 60)
                tail = min(len(line), m.end() + 60)
                ctx = line[head:tail].replace("\n", " ").strip()
                out.append((marker, section, paragraph, abs_offset, ctx))
        char_offset += len(line)
    return out


# ---------------------------------------------------------------------------
# Pure-function comparator
# ---------------------------------------------------------------------------


def run_marker_check(
    manuscript_text: str,
    inventory_ids: set[str],
) -> tuple[list[MarkerFinding], dict]:
    """Compare manuscript markers against inventory ids. Returns
    (findings, totals)."""
    matches = extract_markers_from_manuscript(manuscript_text)

    cited_ids: set[str] = set()
    findings: list[MarkerFinding] = []
    for marker, section, paragraph, offset, surrounding in matches:
        cited_ids.add(marker)
        if marker not in inventory_ids:
            findings.append(
                MarkerFinding(
                    marker=marker,
                    section=section,
                    paragraph=paragraph,
                    char_offset=offset,
                    surrounding=surrounding,
                    severity=SEVERITY_UNRESOLVED,
                    rationale=(
                        f"Marker {marker} appears in manuscript prose but "
                        "no row with this claim_id exists in "
                        "claim_inventory.tsv. Either the drafter fabricated "
                        "the marker, or the inventory is incomplete. "
                        "Inspect the inventory for a closely-numbered row "
                        "(possible off-by-one), or verify the claim is "
                        "actually backed by source data."
                    ),
                )
            )

    totals = {
        "markers_in_manuscript":     len(matches),
        "unique_markers_in_manuscript": len(cited_ids),
        "inventory_size":            len(inventory_ids),
        "cited_and_resolved":        len(cited_ids & inventory_ids),
        "cited_but_unresolved":      len(cited_ids - inventory_ids),
        "in_inventory_but_uncited":  len(inventory_ids - cited_ids),
    }
    return findings, totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manuscript", required=True,
        help="Path to manuscript.md.",
    )
    parser.add_argument(
        "--inventory", required=True,
        help="Path to claim_inventory.tsv.",
    )
    parser.add_argument(
        "--out", required=True,
        help="Path to write claim_marker_check.json.",
    )
    args = parser.parse_args(argv)

    manuscript_path = Path(args.manuscript)
    inventory_path = Path(args.inventory)
    out_path = Path(args.out)

    notes: list[str] = []
    if not manuscript_path.is_file():
        print(
            f"error: manuscript not found: {manuscript_path}",
            file=sys.stderr,
        )
        return 1

    manuscript_text = manuscript_path.read_text(encoding="utf-8")

    if not inventory_path.is_file():
        notes.append(
            f"claim_inventory.tsv not found at {inventory_path} — "
            "all emitted markers will be flagged unresolved."
        )
        inventory_ids: set[str] = set()
    else:
        inventory_ids = load_inventory_claim_ids(inventory_path)
        if not inventory_ids:
            notes.append(
                "claim_inventory.tsv exists but yielded zero claim_ids "
                "(malformed or empty). Marker check will flag all "
                "emitted markers."
            )

    findings, totals = run_marker_check(manuscript_text, inventory_ids)

    report = MarkerCheckReport(
        schema_version=SCHEMA_VERSION,
        tool="check_claim_markers",
        tool_version=TOOL_VERSION,
        draft_dir=str(manuscript_path.parent),
        manuscript_path=str(manuscript_path),
        inventory_path=str(inventory_path) if inventory_path.is_file() else None,
        totals=totals,
        findings=[f.to_dict() for f in findings],
        notes=notes,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(report), indent=2) + "\n",
        encoding="utf-8",
    )

    # Print a human-readable one-liner summary so orchestrator logs
    # don't need to read the JSON.
    print(
        f"check_claim_markers: "
        f"{totals['markers_in_manuscript']} markers ("
        f"{totals['unique_markers_in_manuscript']} unique); "
        f"{totals['cited_and_resolved']} resolved; "
        f"{totals['cited_but_unresolved']} UNRESOLVED → {out_path}"
    )
    return 0 if totals["cited_but_unresolved"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
