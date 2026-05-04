#!/usr/bin/env python3
"""ensemble_review.py — Deduplicate + agreement-score fallback reviews (v0.7.0 R4).

The fallback reviewer (fallback_reviewer.v1.md, 3 classes, ~30s) has a
~30% false-positive rate in practice. Running 3 independent reviews and
filtering by agreement reduces noise: findings that 2/3 or 3/3 reviewers
flag are likely real; 1/3 findings are more likely noise.

Architecture:
    1. Parse each review file via _parse_review_findings (from helpers).
    2. Group findings by (primary_section, severity).
    3. Within each group, cluster findings that refer to the same issue:
       - ≥50% word overlap in header+body text, OR
       - Same manuscript line range (±5 lines).
    4. Score each cluster by agreement count:
       - 3/3: high confidence → always routed to rewrite loop.
       - 2/3: medium confidence → routed to rewrite loop.
       - 1/3: low confidence → logged as advisory, NOT routed.
    5. Assign canonical IDs (C1, I1, S1, ...) to deduplicated findings.
    6. Emit two outputs:
       - Deduplicated findings JSON (consumed by rewrite loop).
       - Advisory-only JSON (surfaced in next_actions.md).

CLI usage (from paper_writer.sh):
    python3 ensemble_review.py \\
        --review-1 path/to/review_1a.md \\
        --review-2 path/to/review_1b.md \\
        --review-3 path/to/review_1c.md \\
        --min-severity important \\
        --out-routed path/to/ensemble_routed.json \\
        --out-advisory path/to/ensemble_advisory.json

Exit codes:
    0  success
    1  user error (missing files, bad args)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Review parsing — duplicated from paper_writer_helpers._parse_review_findings
# to keep this module standalone (no cross-import from helpers, which uses
# importlib hacks in test code and is not a proper importable module).
# ---------------------------------------------------------------------------

_SECTION_NAME_TO_FILE = {
    "abstract": "05_abstract.md",
    "introduction": "00_introduction.md",
    "intro": "00_introduction.md",
    "methods": "01_methods.md",
    "results": "02_results.md",
    "discussion": "03_discussion.md",
}


def _normalize_severity_header(raw: str) -> str:
    """Normalize severity headers to canonical form."""
    low = raw.strip().lower()
    if "critical" in low:
        return "critical"
    if "important" in low:
        return "important"
    if "suggested" in low or "minor" in low:
        return "suggested"
    return "suggested"


def _parse_review_findings(review_text: str) -> list[dict]:
    """Parse a fallback-reviewer markdown review into structured findings.

    Returns list of dicts with keys:
        id, severity, primary_section, header_line, body_text, line_range
    """
    findings: list[dict] = []
    current_severity: Optional[str] = None

    finding_header_re = re.compile(
        r"^\s*(?:-\s+)?\*\*([CIS]\d+):\s+(.*)$"
    )
    severity_header_re = re.compile(r"^\s*###\s+(.+?)\s*$")
    section_pattern = re.compile(
        r"\b(Abstract|Methods|Results|Discussion|Introduction|Intro)\b",
        re.IGNORECASE,
    )
    # Line-number references in review text: "line 42", "lines 10-15", "L42"
    line_ref_re = re.compile(
        r"\b(?:lines?\s+(\d+)(?:\s*[-–]\s*(\d+))?|L(\d+))\b", re.IGNORECASE
    )

    lines = review_text.splitlines()
    n_lines = len(lines)

    for i, line in enumerate(lines):
        sm = severity_header_re.match(line)
        if sm:
            current_severity = _normalize_severity_header(sm.group(1))
            continue
        if current_severity is None:
            continue
        fm = finding_header_re.match(line)
        if not fm:
            continue
        fid = fm.group(1)

        # Collect body lines until next finding/severity header
        header_text = fm.group(2)
        body_lines: list[str] = []
        for j in range(i + 1, n_lines):
            body_line = lines[j]
            if finding_header_re.match(body_line):
                break
            if severity_header_re.match(body_line):
                break
            body_lines.append(body_line)

        body_text = "\n".join(body_lines).strip()
        combined_text = header_text + " " + body_text

        # Extract primary section
        primary_section = ""
        head_match = section_pattern.search(header_text)
        if head_match:
            primary_section = head_match.group(1)
        else:
            for bl in body_lines[:6]:
                bm = section_pattern.search(bl)
                if bm:
                    primary_section = bm.group(1)
                    break

        # Extract line references for positional matching
        line_refs: list[int] = []
        for m in line_ref_re.finditer(combined_text):
            if m.group(1):
                line_refs.append(int(m.group(1)))
                if m.group(2):
                    line_refs.append(int(m.group(2)))
            elif m.group(3):
                line_refs.append(int(m.group(3)))

        findings.append({
            "id": fid,
            "severity": current_severity,
            "primary_section": primary_section.lower(),
            "header_line": line.strip(),
            "body_text": body_text,
            "combined_text": combined_text,
            "line_range": (min(line_refs), max(line_refs)) if line_refs else None,
        })

    return findings


# ---------------------------------------------------------------------------
# Word-overlap deduplication
# ---------------------------------------------------------------------------

def _word_set(text: str) -> set[str]:
    """Extract a set of lowercase words ≥3 chars (skip trivial words)."""
    return {
        w.lower()
        for w in re.findall(r"\b\w+\b", text)
        if len(w) >= 3
    }


def _word_overlap_fraction(text_a: str, text_b: str) -> float:
    """Fraction of shared words between two texts (Jaccard-like).

    Returns |A ∩ B| / min(|A|, |B|) — the overlap relative to the
    smaller set. This handles asymmetric cases where one review is
    more verbose than another.
    """
    words_a = _word_set(text_a)
    words_b = _word_set(text_b)
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


def _line_range_overlap(range_a: tuple[int, int] | None,
                        range_b: tuple[int, int] | None,
                        tolerance: int = 5) -> bool:
    """Check whether two line ranges overlap within tolerance."""
    if range_a is None or range_b is None:
        return False
    # Expand both ranges by tolerance, then check overlap
    a_lo, a_hi = range_a[0] - tolerance, range_a[1] + tolerance
    b_lo, b_hi = range_b[0] - tolerance, range_b[1] + tolerance
    return a_lo <= b_hi and b_lo <= a_hi


def _findings_match(f1: dict, f2: dict,
                    word_overlap_threshold: float = 0.50) -> bool:
    """Do two findings refer to the same issue?

    Two findings match if they share the same (section, severity) AND:
      - ≥50% word overlap in combined header+body text, OR
      - Overlapping manuscript line references (±5 lines).
    """
    if f1["primary_section"] != f2["primary_section"]:
        return False
    if f1["severity"] != f2["severity"]:
        return False
    # Word overlap check
    overlap = _word_overlap_fraction(f1["combined_text"], f2["combined_text"])
    if overlap >= word_overlap_threshold:
        return True
    # Line-range check
    if _line_range_overlap(f1.get("line_range"), f2.get("line_range")):
        return True
    return False


# ---------------------------------------------------------------------------
# Clustering + agreement scoring
# ---------------------------------------------------------------------------

def _cluster_findings(all_findings: list[list[dict]],
                      word_overlap_threshold: float = 0.50
                      ) -> list[dict]:
    """Cluster findings from multiple reviews into agreement-scored groups.

    Args:
        all_findings: List of per-review finding lists (each from
            _parse_review_findings). Typically 3 lists for 3 reviews.
        word_overlap_threshold: Minimum word overlap for matching.

    Returns:
        List of cluster dicts, each with:
            - members: list of (review_idx, finding) tuples
            - agreement: int (how many reviews contributed)
            - canonical_finding: representative finding dict
            - severity: canonical severity
            - primary_section: canonical section
    """
    n_reviews = len(all_findings)
    # Tag each finding with its review index
    tagged: list[tuple[int, dict]] = []
    for rev_idx, findings in enumerate(all_findings):
        for f in findings:
            tagged.append((rev_idx, f))

    clusters: list[list[tuple[int, dict]]] = []

    for rev_idx, finding in tagged:
        merged = False
        for cluster in clusters:
            # Check if this finding matches any member in the cluster.
            # Also ensure we don't double-count the same review index.
            review_indices_in_cluster = {ri for ri, _ in cluster}
            if rev_idx in review_indices_in_cluster:
                # Same review already has a finding in this cluster —
                # this is a different finding from the same review. Don't merge.
                continue
            for _, existing in cluster:
                if _findings_match(finding, existing, word_overlap_threshold):
                    cluster.append((rev_idx, finding))
                    merged = True
                    break
            if merged:
                break
        if not merged:
            clusters.append([(rev_idx, finding)])

    # Build scored cluster dicts
    result: list[dict] = []
    for cluster in clusters:
        review_indices = {ri for ri, _ in cluster}
        agreement = len(review_indices)
        # Pick the longest member as canonical (most informative text)
        canonical_idx = max(
            range(len(cluster)),
            key=lambda i: len(cluster[i][1].get("combined_text", ""))
        )
        canonical = cluster[canonical_idx][1]
        result.append({
            "members": cluster,
            "agreement": agreement,
            "canonical_finding": canonical,
            "severity": canonical["severity"],
            "primary_section": canonical["primary_section"],
        })

    # Sort: higher agreement first, then by severity (critical > important > suggested)
    severity_order = {"critical": 0, "important": 1, "suggested": 2}
    result.sort(key=lambda c: (
        -c["agreement"],
        severity_order.get(c["severity"], 3),
    ))

    return result


def deduplicate_reviews(
    review_texts: list[str],
    min_severity: str = "important",
    word_overlap_threshold: float = 0.50,
) -> dict[str, Any]:
    """Main entry point: parse + cluster + score reviews.

    Args:
        review_texts: List of review markdown strings (typically 3).
        min_severity: Minimum severity to include ("critical", "important", "suggested").
        word_overlap_threshold: Word overlap fraction for matching.

    Returns:
        Dict with:
            routed: list of findings with agreement ≥2 (for rewrite loop)
            advisory: list of findings with agreement == 1 (for audit log)
            stats: summary counts
    """
    severity_order = {"critical": 0, "important": 1, "suggested": 2}
    threshold = severity_order.get(min_severity.lower(), 1)

    # Parse each review
    all_findings: list[list[dict]] = []
    for text in review_texts:
        findings = _parse_review_findings(text)
        # Apply severity filter
        filtered = [
            f for f in findings
            if severity_order.get(f["severity"], 3) <= threshold
        ]
        all_findings.append(filtered)

    # Cluster
    clusters = _cluster_findings(all_findings, word_overlap_threshold)

    # Assign canonical IDs and partition by agreement
    routed: list[dict] = []
    advisory: list[dict] = []

    # Counter per severity prefix
    id_counters = {"critical": 0, "important": 0, "suggested": 0}
    prefix_map = {"critical": "C", "important": "I", "suggested": "S"}

    for cluster in clusters:
        sev = cluster["severity"]
        id_counters[sev] = id_counters.get(sev, 0) + 1
        canonical_id = f"{prefix_map.get(sev, 'S')}{id_counters[sev]}"

        entry = {
            "id": canonical_id,
            "severity": sev,
            "primary_section": cluster["primary_section"],
            "header_line": cluster["canonical_finding"]["header_line"],
            "body_text": cluster["canonical_finding"].get("body_text", ""),
            "agreement": cluster["agreement"],
            "n_reviews": len(all_findings),
        }

        if cluster["agreement"] >= 2:
            routed.append(entry)
        else:
            advisory.append(entry)

    # Build output compatible with parse-review JSON shape (for rewrite loop)
    findings_by_section: dict[str, list[dict]] = {}
    section_files: dict[str, str] = {}
    for entry in routed:
        sec = entry["primary_section"]
        findings_by_section.setdefault(sec, []).append({
            "id": entry["id"],
            "severity": entry["severity"],
            "header_line": entry["header_line"],
            "agreement": entry["agreement"],
        })
        if sec in _SECTION_NAME_TO_FILE:
            section_files[sec] = _SECTION_NAME_TO_FILE[sec]

    result = {
        "routed": {
            "findings_by_section": findings_by_section,
            "section_files": section_files,
            "total_findings": len(routed),
            "min_severity": min_severity,
        },
        "advisory": [
            {
                "id": e["id"],
                "severity": e["severity"],
                "primary_section": e["primary_section"],
                "header_line": e["header_line"],
                "agreement": e["agreement"],
            }
            for e in advisory
        ],
        "stats": {
            "n_reviews": len(review_texts),
            "total_parsed": [len(fl) for fl in all_findings],
            "total_clusters": len(clusters),
            "routed_count": len(routed),
            "advisory_count": len(advisory),
            "agreement_3_3": sum(1 for c in clusters if c["agreement"] >= 3),
            "agreement_2_3": sum(1 for c in clusters if c["agreement"] == 2),
            "agreement_1_3": sum(1 for c in clusters if c["agreement"] == 1),
        },
    }

    return result


# ---------------------------------------------------------------------------
# Rebuild review markdown from routed findings (for rewrite.v1 consumption)
# ---------------------------------------------------------------------------

def _rebuild_review_markdown(routed_findings: list[dict]) -> str:
    """Rebuild a review-like markdown from routed findings.

    The rewrite loop's extract-findings subcommand expects review markdown
    with finding headers in the standard format. This rebuilds that format
    from the deduplicated routed findings so the existing rewrite pipeline
    can consume the ensemble output without modification.
    """
    lines: list[str] = [
        "# Ensemble Review (deduplicated)",
        "",
        f"Findings below passed the agreement filter (≥2/3 reviewers).",
        "",
    ]

    # Group by severity for section headers
    by_severity: dict[str, list[dict]] = {}
    for f in routed_findings:
        by_severity.setdefault(f["severity"], []).append(f)

    severity_display = {
        "critical": "Critical",
        "important": "Important",
        "suggested": "Suggested",
    }

    for sev in ["critical", "important", "suggested"]:
        if sev not in by_severity:
            continue
        lines.append(f"### {severity_display[sev]}")
        lines.append("")
        for f in by_severity[sev]:
            lines.append(f["header_line"])
            if f.get("body_text"):
                lines.append("")
                lines.append(f["body_text"])
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Ensemble fallback review: deduplicate + agreement-score 3 reviews."
    )
    p.add_argument("--review-1", required=True, help="Path to first review file")
    p.add_argument("--review-2", required=True, help="Path to second review file")
    p.add_argument("--review-3", required=True, help="Path to third review file")
    p.add_argument(
        "--min-severity", default="important",
        choices=["critical", "important", "suggested"],
        help="Minimum severity to include (default: important)",
    )
    p.add_argument(
        "--out-routed", required=True,
        help="Output path for routed (≥2/3) findings JSON",
    )
    p.add_argument(
        "--out-advisory", default=None,
        help="Output path for advisory (1/3) findings JSON (optional)",
    )
    p.add_argument(
        "--out-review-md", default=None,
        help="Output path for rebuilt review markdown (for rewrite loop consumption)",
    )

    args = p.parse_args(argv)

    # Read review files
    review_paths = [args.review_1, args.review_2, args.review_3]
    review_texts: list[str] = []
    for rp in review_paths:
        path = Path(rp).expanduser().resolve()
        if not path.is_file():
            print(f"ERROR: review file not found: {path}", file=sys.stderr)
            return 1
        review_texts.append(path.read_text(encoding="utf-8"))

    # Run deduplication
    result = deduplicate_reviews(
        review_texts,
        min_severity=args.min_severity,
    )

    # Write routed findings
    out_routed = Path(args.out_routed)
    out_routed.parent.mkdir(parents=True, exist_ok=True)
    out_routed.write_text(
        json.dumps(result["routed"], indent=2) + "\n",
        encoding="utf-8",
    )

    # Write advisory findings (optional)
    if args.out_advisory:
        out_adv = Path(args.out_advisory)
        out_adv.parent.mkdir(parents=True, exist_ok=True)
        out_adv.write_text(
            json.dumps(result["advisory"], indent=2) + "\n",
            encoding="utf-8",
        )

    # Write rebuilt review markdown (optional, for rewrite loop)
    if args.out_review_md:
        routed_entries: list[dict] = []
        for sec_findings in result["routed"]["findings_by_section"].values():
            routed_entries.extend(sec_findings)
        # Need the full routed findings with body_text for rebuild
        # Re-extract from the dedup result
        all_findings_flat: list[list[dict]] = []
        for text in review_texts:
            all_findings_flat.append(_parse_review_findings(text))
        clusters = _cluster_findings(all_findings_flat)

        routed_for_md: list[dict] = []
        id_counters_md = {"critical": 0, "important": 0, "suggested": 0}
        prefix_map_md = {"critical": "C", "important": "I", "suggested": "S"}
        for cluster in clusters:
            if cluster["agreement"] < 2:
                continue
            sev = cluster["severity"]
            id_counters_md[sev] = id_counters_md.get(sev, 0) + 1
            canonical_id = f"{prefix_map_md.get(sev, 'S')}{id_counters_md[sev]}"
            cf = cluster["canonical_finding"]
            routed_for_md.append({
                "id": canonical_id,
                "severity": sev,
                "header_line": cf["header_line"],
                "body_text": cf.get("body_text", ""),
            })

        md_text = _rebuild_review_markdown(routed_for_md)
        out_md = Path(args.out_review_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md_text, encoding="utf-8")

    # Print stats to stderr for orchestrator logging
    stats = result["stats"]
    print(
        f"ensemble: {stats['total_clusters']} clusters from "
        f"{stats['n_reviews']} reviews; "
        f"{stats['routed_count']} routed (≥2/3), "
        f"{stats['advisory_count']} advisory (1/3); "
        f"agreement 3/3={stats['agreement_3_3']} "
        f"2/3={stats['agreement_2_3']} "
        f"1/3={stats['agreement_1_3']}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
