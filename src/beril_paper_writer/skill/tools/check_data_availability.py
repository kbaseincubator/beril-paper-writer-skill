#!/usr/bin/env python3
"""check_data_availability.py — Data Availability section validator (advisory).

Standalone script invoked by the shell orchestrator after phase_data_avail:

    python3 "$SKILL_DIR/tools/check_data_availability.py" \
        "$DRAFT_DIR" --report "$PROJECT_ROOT/REPORT.md"

Validates the generated 07_data_availability.md against known failure modes
from the ibd_phage_targeting live test (v0.7.2):

  1. **File-extension false positives:** Collection names ending in .py,
     .txt, .md in the K-BERDL block → confabulated databases from regex
     fallback (v0.7.1 bug).
  2. **PMIDs in accessions block:** Bibliography references misclassified
     as data accessions (v0.7.1 bug).
  3. **Collection cross-reference:** Every K-BERDL collection in Data
     Availability should also appear in REPORT.md ### Sources table.
  4. **External sources have URLs:** Incomplete entries without URLs.
  5. **[TBD] marker count:** Surfaces how many placeholders remain for
     user action.

Behavior:
  - Emits stderr WARN/NOTE lines per anomaly + a final summary count.
  - **Always exits 0.** Advisory; same contract as other post-checkers.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_file_extension_false_positives(da_text: str) -> list[str]:
    """Warn if K-BERDL block contains collection or table names that look
    like filenames or file extensions.

    Catches the v0.7.1 bug where extract_methods.py was parsed as
    database=extract_methods, table=py. In the formatted output, this
    appears as collection `extract_methods` with table `py`.
    """
    warnings = []
    kberdl_section = _extract_section(da_text, "Data sources — BERDL / K-BERDL")
    if not kberdl_section:
        kberdl_section = _extract_section(da_text, "Data sources")
    if not kberdl_section:
        return warnings

    _FILE_EXTS = {"py", "txt", "md", "sh", "yml", "toml", "cfg",
                  "json", "tsv", "csv", "rst", "ini", "lock"}

    # Check collection names ending in file extensions.
    file_ext_re = re.compile(
        r"`([^`]+\.(?:" + "|".join(_FILE_EXTS) + r"))`"
    )
    for m in file_ext_re.finditer(kberdl_section):
        name = m.group(1)
        if "/" in name:
            continue
        warnings.append(
            f"WARN: K-BERDL collection name looks like a filename: "
            f"`{name}` — likely a regex false positive"
        )

    # Check for table names that are bare file extensions (e.g., table `py`
    # from parsing extract_methods.py as database.table).
    for line in kberdl_section.split("\n"):
        if not line.strip().startswith("- **"):
            continue
        # Extract table names from "tables: `py`, `txt`" patterns.
        tables_m = re.search(r"tables:\s*(.+?)(?:\s*\(|$)", line)
        if not tables_m:
            continue
        table_names = re.findall(r"`([^`]+)`", tables_m.group(1))
        for tname in table_names:
            if tname.lower() in _FILE_EXTS:
                # Extract collection name for the warning.
                coll_m = re.search(r"\*\*`([^`]+)`\*\*", line)
                coll = coll_m.group(1) if coll_m else "unknown"
                warnings.append(
                    f"WARN: K-BERDL table name `{tname}` in collection "
                    f"`{coll}` looks like a file extension — likely a "
                    f"regex false positive (e.g., {coll}.{tname} parsed "
                    f"as database.table)"
                )

    return warnings


def _check_pmids_in_accessions(da_text: str) -> list[str]:
    """Warn if PMIDs appear in the accessions block."""
    warnings = []
    accessions_section = _extract_section(da_text, "Data accessions")
    if not accessions_section:
        return warnings

    pmid_re = re.compile(r"\bPMID:?\s*`?\d+`?\b", re.IGNORECASE)
    for m in pmid_re.finditer(accessions_section):
        warnings.append(
            f"WARN: PMID in accessions block: `{m.group(0)}` — "
            f"PMIDs are bibliography references, not data accessions"
        )
    return warnings


def _check_collection_crossref(
    da_text: str, report_text: str
) -> list[str]:
    """Warn if a K-BERDL collection in DA doesn't appear in REPORT.md."""
    warnings = []
    if not report_text:
        return warnings

    kberdl_section = _extract_section(da_text, "Data sources — BERDL / K-BERDL")
    if not kberdl_section:
        return warnings

    # Extract collection names from the DA block.
    collection_re = re.compile(r"\*\*`([^`]+)`\*\*")
    da_collections = [m.group(1) for m in collection_re.finditer(kberdl_section)]

    for coll in da_collections:
        if coll not in report_text:
            warnings.append(
                f"WARN: Collection `{coll}` in Data Availability not found "
                f"in REPORT.md — possible confabulation"
            )
    return warnings


def _check_external_urls(da_text: str) -> list[str]:
    """Warn if external sources are listed without URLs."""
    warnings = []
    external_section = _extract_section(
        da_text, "Data sources — external / public"
    )
    if not external_section:
        return warnings

    # Each line starting with "- **" should ideally have a URL somewhere.
    for line in external_section.split("\n"):
        if line.strip().startswith("- **"):
            name_m = re.match(r"- \*\*([^*]+)\*\*", line.strip())
            if name_m and "http" not in line.lower():
                # This is informational, not an error — URLs come from
                # the REPORT table which may not have them.
                warnings.append(
                    f"NOTE: External source `{name_m.group(1)}` listed "
                    f"without URL — consider adding before submission"
                )
    return warnings


def _check_tbd_markers(da_text: str) -> list[str]:
    """Count and report [TBD ...] markers remaining."""
    warnings = []
    tbd_re = re.compile(r"\[(?:[A-Z_ ]*:?\s*)?TBD\b[^\]]*\]")
    matches = tbd_re.findall(da_text)
    if matches:
        warnings.append(
            f"NOTE: {len(matches)} [TBD] marker(s) remaining — "
            f"review and fill before submission"
        )
        for m in matches:
            truncated = m[:80] + "..." if len(m) > 80 else m
            warnings.append(f"  → {truncated}")
    return warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_section(text: str, heading: str) -> str | None:
    """Extract text under a ## heading until the next ## or EOF."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$\n([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advisory validator for 07_data_availability.md"
    )
    parser.add_argument(
        "draft_dir",
        help="Path to draft directory containing 07_data_availability.md",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to REPORT.md for collection cross-reference",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    da_path = draft_dir / "07_data_availability.md"

    if not da_path.is_file():
        print(
            "NOTE: 07_data_availability.md not found — skipping checks",
            file=sys.stderr,
        )
        return 0

    da_text = da_path.read_text(encoding="utf-8")

    report_text = ""
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        if report_path.is_file():
            report_text = report_path.read_text(encoding="utf-8")

    # Run all checks.
    all_warnings: list[str] = []
    all_warnings.extend(_check_file_extension_false_positives(da_text))
    all_warnings.extend(_check_pmids_in_accessions(da_text))
    all_warnings.extend(_check_collection_crossref(da_text, report_text))
    all_warnings.extend(_check_external_urls(da_text))
    all_warnings.extend(_check_tbd_markers(da_text))

    # Emit.
    warn_count = sum(1 for w in all_warnings if w.startswith("WARN"))
    note_count = sum(1 for w in all_warnings if w.startswith("NOTE"))

    for w in all_warnings:
        print(w, file=sys.stderr)

    if all_warnings:
        print(
            f"\n[check_data_availability] {warn_count} warning(s), "
            f"{note_count} note(s)",
            file=sys.stderr,
        )
    else:
        print(
            "[check_data_availability] all checks passed",
            file=sys.stderr,
        )

    # Always exit 0 — advisory only.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
