#!/usr/bin/env python3
"""check_echo_repetition.py — Quantitative-claim repetition detector (advisory).

Standalone script invoked by the shell orchestrator after manuscript assembly:

    python3 "$SKILL_DIR/tools/check_echo_repetition.py" "$DRAFT_DIR"

Detects quantitative claims (percentages, p-values, effect sizes, sample sizes,
confidence intervals) that are repeated across sections of the assembled
manuscript. Echo-claims indicate either:
  1. Legitimate cross-reference (e.g., abstract repeating key result).
  2. Redundant boilerplate (e.g., methods detail duplicated in results).

Behavior:
  - Scans manuscript.md or falls back to individual section files.
  - Extracts claims with ~5-word context fingerprints.
  - Warns if a claim appears in 3+ sections (strong echo signal).
  - Reports top-5 most-repeated claims.
  - Emits stderr WARN/NOTE lines per finding + a final summary count.
  - **Always exits 0.** Advisory; same contract as other post-checkers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Quantitative pattern matchers
# ---------------------------------------------------------------------------

def _extract_quantitative_claims(
    text: str,
) -> list[str]:
    """Extract quantitative claims as normalized value tokens.

    Returns list of normalized claim strings (the quantitative value itself,
    not surrounding context).  Using the raw value as fingerprint means that
    "42% reduction in load" and "42% reduction compared to baseline" are
    correctly recognised as the same claim echoed across sections.
    """
    # Remove code blocks, image tags, and table markup to avoid false positives.
    text = _strip_code_and_markup(text)

    claims = []

    # Percentage: N% or N.N%
    pct_re = re.compile(r"\b(\d+(?:\.\d+)?%)")
    for m in pct_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    # Fraction: N/N
    frac_re = re.compile(r"\b(\d+/\d+)\b")
    for m in frac_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    # P-values and q-values: p = N, p < N, q = N, q < N
    pval_re = re.compile(
        r"([pq]\s*(?:=|<|≤)\s*(?:0\.)?\d+(?:[eE][+-]?\d+)?)",
        re.IGNORECASE,
    )
    for m in pval_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    # Effect sizes: OR N.N, HR N.N, RR N.N
    effect_re = re.compile(
        r"\b((?:OR|HR|RR)\s+\d+(?:\.\d+)?(?:\s*[–-]\s*\d+(?:\.\d+)?)?)",
        re.IGNORECASE,
    )
    for m in effect_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    # Sample sizes: n = N, N = N,NNN (with optional commas)
    sample_re = re.compile(
        r"\b((?:n|N|sample size)\s*=\s*\d[\d,]*)",
        re.IGNORECASE,
    )
    for m in sample_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    # Confidence intervals: N.N [N.N–N.N]
    ci_re = re.compile(
        r"(\d+(?:\.\d+)?\s*\[\s*\d+(?:\.\d+)?\s*[–-]\s*\d+(?:\.\d+)?\s*\])"
    )
    for m in ci_re.finditer(text):
        claims.append(_normalize_fingerprint(m.group(0)))

    return claims


def _strip_code_and_markup(text: str) -> str:
    """Remove code blocks, image tags, and table markup."""
    # Remove code blocks (```...```).
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code (`...`).
    text = re.sub(r"`[^`]*`", "", text)
    # Remove image tags (![...](url)).
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Remove table markup (lines starting with |).
    text = re.sub(r"^\s*\|.*\|", "", text, flags=re.MULTILINE)
    return text


def _normalize_fingerprint(fp: str) -> str:
    """Normalize fingerprint for comparison.

    Strip commas from numbers, drop trailing zeros after decimal.
    E.g., "88.20% sign" -> "88.2% sign", "1,234 samples" -> "1234 samples".
    """
    # Strip commas in numbers.
    fp = re.sub(r"(\d),(\d)", r"\1\2", fp)
    # Strip trailing zeros after decimal point (but keep at least one digit).
    fp = re.sub(r"(\d)\.0+\b", r"\1", fp)
    fp = re.sub(r"(\d\.\d*[1-9])0+\b", r"\1", fp)
    return fp.lower().strip()


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

def _extract_sections(text: str) -> dict[str, str]:
    """Split text into sections by H1/H2 headers.

    Known sections: Abstract, Introduction, Methods, Results, Findings Summary,
    Discussion, Data Availability, Conclusions, Acknowledgments.

    Returns dict of {section_label: section_text}.
    """
    sections = {}
    known_headers = {
        "Abstract",
        "Introduction",
        "Methods",
        "Results",
        "Findings Summary",
        "Discussion",
        "Data Availability",
        "Conclusions",
        "Acknowledgments",
    }

    # Split by # or ## headers (manuscripts use both conventions).
    parts = re.split(r"^#{1,3}\s+(.+?)$", text, flags=re.MULTILINE)
    # parts is [intro_text, header1, body1, header2, body2, ...]
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""

        # Match header against known section names (case-insensitive,
        # allow partial matches like "Findings summary" within Results).
        for known in known_headers:
            if known.lower() in header.lower():
                # If this section already exists, append (handles
                # "Findings Summary" being a subsection of Results
                # that we want to track separately).
                if known in sections:
                    sections[known] += "\n" + body
                else:
                    sections[known] = body
                break

    return sections


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_claim_repetition(text: str) -> tuple[dict, list[str]]:
    """Check for quantitative claims repeated across 3+ sections.

    Returns (diagnostics_dict, warnings_list).
    """
    sections = _extract_sections(text)
    if not sections:
        return {}, []

    # Collect claims per section.
    claims_by_section: dict[str, list[str]] = {}
    for section_label, section_text in sections.items():
        claims = _extract_quantitative_claims(section_text)
        claims_by_section[section_label] = claims

    # Count unique claims and their occurrences across sections.
    claim_to_sections: dict[str, list[str]] = {}
    for section_label, claims in claims_by_section.items():
        for claim in claims:
            if claim not in claim_to_sections:
                claim_to_sections[claim] = []
            claim_to_sections[claim].append(section_label)

    # Find claims in 3+ sections.
    warnings = []
    repeated_claims = [
        (claim, sections)
        for claim, sections in claim_to_sections.items()
        if len(set(sections)) >= 3
    ]
    repeated_claims.sort(key=lambda x: len(set(x[1])), reverse=True)

    for claim, sections in repeated_claims:
        unique_sections = list(dict.fromkeys(sections))  # Preserve order, dedupe.
        warnings.append(
            f"WARN  claim \"{claim}\" appears in {len(unique_sections)} "
            f"sections: {', '.join(unique_sections)}"
        )

    # Top-5 most-repeated claims.
    top_n = 5
    top_claims = [
        {
            "claim": claim,
            "count": len(set(sections)),
            "sections": list(dict.fromkeys(sections)),
        }
        for claim, sections in repeated_claims[:top_n]
    ]

    if top_claims:
        warnings.append("NOTE  top-5 repeated claims:")
        for i, item in enumerate(top_claims, start=1):
            warnings.append(
                f"  {i}. \"{item['claim']}\" — {item['count']} sections"
            )

    diagnostics = {
        "total_unique_claims": len(claim_to_sections),
        "claims_in_3plus_sections": len(repeated_claims),
        "top_repeated": top_claims,
    }

    return diagnostics, warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_manuscript(draft_dir: Path) -> str | None:
    """Load manuscript.md, falling back to individual section files."""
    # Try assembled manuscript first.
    manuscript_path = draft_dir / "manuscript.md"
    if manuscript_path.is_file():
        return manuscript_path.read_text(encoding="utf-8")

    # Fall back to individual section files (01_abstract.md, etc.).
    section_files = sorted(draft_dir.glob("[0-9][0-9]_*.md"))
    if not section_files:
        return None

    parts = []
    for fpath in section_files:
        parts.append(fpath.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advisory detector for quantitative-claim repetition"
    )
    parser.add_argument(
        "draft_dir",
        help="Path to draft directory containing manuscript.md or section files",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    manuscript_text = _load_manuscript(draft_dir)

    if not manuscript_text:
        print(
            "NOTE: No manuscript.md or section files found — skipping checks",
            file=sys.stderr,
        )
        return 0

    # Run check.
    diagnostics, warnings = _check_claim_repetition(manuscript_text)

    # Emit warnings to stderr.
    warn_count = sum(1 for w in warnings if w.startswith("WARN"))
    note_count = sum(1 for w in warnings if w.startswith("NOTE"))

    for w in warnings:
        print(w, file=sys.stderr)

    if warn_count > 0 or note_count > 0:
        print(
            f"\n[check_echo_repetition] {warn_count} warning(s), "
            f"{note_count} note(s)",
            file=sys.stderr,
        )
    else:
        print(
            "[check_echo_repetition] no claims repeated in 3+ sections",
            file=sys.stderr,
        )

    # Emit JSON diagnostics to stdout.
    print(json.dumps(diagnostics, indent=2))

    # Always exit 0 — advisory only.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
