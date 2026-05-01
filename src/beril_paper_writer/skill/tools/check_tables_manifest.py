#!/usr/bin/env python3
"""check_tables_manifest.py — tables_manifest.tsv cross-walk (advisory).

Standalone script invoked by the shell orchestrator after results.v1
writes the tables manifest:

    python3 "$SKILL_DIR/tools/check_tables_manifest.py" \
        "$DRAFT_DIR" --inventory "$DRAFT_DIR/tables_inventory.md"

Validates the tables_manifest.tsv contract from the artifact side:
schema, inventory cross-reference, and cross-walk against the prose's
`(Table N)` callouts.

Five checks:
  1. **Schema:** manifest exists + has correct header
     (paper_order_n / table_id / inventory_lookup_name) + every data
     row has 3 tab-separated cells with valid integer in column 1.
  2. **Inventory cross-reference:** every row's `inventory_lookup_name`
     resolves to an entry in tables_inventory.md.
  3. **Callout cross-walk:** for each `(Table N)` callout in the section
     files (02_results.md primarily; also 01_methods.md,
     03_discussion.md), the manifest has a row with
     `paper_order_n == N`.
  4. **Wide-table warning:** for each manifest entry, look up
     column_count from inventory; warn if >8 columns.
  5. **Duplicate detection:** no two manifest rows share the same
     paper_order_n or inventory_lookup_name.

Behavior:
  - Emits stderr WARN/NOTE lines per anomaly + a final summary count.
  - **Always exits 0.** Advisory; same contract as
    check_figures_manifest.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPECTED_HEADER = ["paper_order_n", "table_id", "inventory_lookup_name"]
SECTION_FILES = ("02_results.md", "01_methods.md", "03_discussion.md")

# Match Table N callouts in prose. Loose enough to match
# "(Table 3)" / "(Table 3 and Table 5)" / "(Tables 1–3)" /
# "Table 2" in mid-sentence position. The \b word boundary on
# the left prevents matching "DataTable" etc.
TABLE_CALLOUT_RE = re.compile(r"\bTable\s+(\d+)\b")


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------


def parse_manifest(manifest_path: Path) -> tuple[list[dict], list[str]]:
    """Parse tables_manifest.tsv into rows + a list of schema warnings.

    Returns ([], [<warnings>]) if file missing.
    Returns (rows, warnings) on partial / full success; rows that fail
    parsing are skipped and noted in warnings.
    """
    warnings: list[str] = []
    if not manifest_path.is_file():
        warnings.append(f"tables_manifest.tsv not found at {manifest_path}")
        return [], warnings

    text = manifest_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        warnings.append(f"tables_manifest.tsv is empty: {manifest_path}")
        return [], warnings

    header = lines[0].split("\t")
    if header != EXPECTED_HEADER:
        warnings.append(
            f"tables_manifest.tsv header mismatch: got {header!r}, "
            f"expected {EXPECTED_HEADER!r}"
        )
    rows: list[dict] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 3:
            warnings.append(
                f"tables_manifest.tsv line {lineno}: expected 3 cells, "
                f"got {len(cells)}; skipping"
            )
            continue
        if len(cells) > 3:
            warnings.append(
                f"tables_manifest.tsv line {lineno}: row has >3 tab-separated "
                f"cells; first 3 cells used"
            )
        try:
            n = int(cells[0].strip())
        except ValueError:
            warnings.append(
                f"tables_manifest.tsv line {lineno}: paper_order_n not an "
                f"integer: {cells[0]!r}; skipping"
            )
            continue
        rows.append({
            "lineno": lineno,
            "paper_order_n": n,
            "table_id": cells[1].strip(),
            "inventory_lookup_name": cells[2].strip(),
        })
    return rows, warnings


def parse_inventory_ids(inventory_path: Path) -> tuple[dict[str, dict], list[str]]:
    """Parse tables_inventory.md for entry IDs and column counts.

    Returns ({inventory_lookup_name: {"column_count": N, "heading": ...}},
    [warnings]).
    """
    warnings: list[str] = []
    if not inventory_path.is_file():
        warnings.append(
            f"tables_inventory.md not found at {inventory_path}"
        )
        return {}, warnings

    text = inventory_path.read_text(encoding="utf-8")
    entries: dict[str, dict] = {}

    # Parse ### headers like "### report_tbl_01 — Pathway gap summary"
    current_id: Optional[str] = None
    current_data: dict = {}
    for line in text.split("\n"):
        stripped = line.strip()
        m = re.match(r"^###\s+(report_tbl_\d+)\s", stripped)
        if m:
            if current_id:
                entries[current_id] = current_data
            current_id = m.group(1)
            current_data = {"heading": stripped, "column_count": 0}
            continue

        if current_id and stripped.startswith("_Columns:"):
            # Parse "_Columns: 9 (Organism | Locus | ...)"
            cm = re.match(r"_Columns:\s*(\d+)", stripped)
            if cm:
                current_data["column_count"] = int(cm.group(1))

    if current_id:
        entries[current_id] = current_data

    return entries, warnings


def collect_table_callouts(
    section_paths: Iterable[Path],
) -> dict[int, list[str]]:
    """Walk section files for `(Table N)` callouts.

    Returns {N: [section_filename, ...]} so a callout can be traced back
    to which section files reference it.
    """
    callouts: dict[int, list[str]] = {}
    for section_path in section_paths:
        if not section_path.is_file():
            continue
        text = section_path.read_text(encoding="utf-8")
        for m in TABLE_CALLOUT_RE.finditer(text):
            n = int(m.group(1))
            callouts.setdefault(n, []).append(section_path.name)
    for n in list(callouts.keys()):
        callouts[n] = sorted(set(callouts[n]))
    return callouts


# ---------------------------------------------------------------------------
# Cross-walk checks
# ---------------------------------------------------------------------------


def check_inventory_xref(
    rows: list[dict], inventory_entries: dict[str, dict],
) -> list[str]:
    """Each row's inventory_lookup_name must exist in the inventory."""
    warnings: list[str] = []
    for row in rows:
        inv_name = row["inventory_lookup_name"]
        if inv_name not in inventory_entries:
            warnings.append(
                f"tables_manifest.tsv row {row['lineno']} "
                f"(paper_order_n={row['paper_order_n']}): "
                f"inventory_lookup_name {inv_name!r} not found in "
                f"tables_inventory.md"
            )
    return warnings


def check_callouts_match_manifest(
    rows: list[dict],
    callouts: dict[int, list[str]],
) -> list[str]:
    """Every (Table N) callout's N must have a manifest row with that N."""
    warnings: list[str] = []
    manifest_ns = {row["paper_order_n"] for row in rows}
    for n in sorted(callouts.keys()):
        if n not in manifest_ns:
            sections = ", ".join(callouts[n])
            warnings.append(
                f"prose cites (Table {n}) in [{sections}] but the manifest "
                f"has no row with paper_order_n={n}; phase_embed_tables "
                f"will not be able to inject this table"
            )
    callout_ns = set(callouts.keys())
    for n in sorted(manifest_ns):
        if n not in callout_ns:
            warnings.append(
                f"NOTE: paper_order_n={n} is in manifest but no "
                f"(Table {n}) callout found in section prose; this table "
                f"will not be embedded by phase_embed_tables"
            )
    return warnings


def check_wide_tables(
    rows: list[dict], inventory_entries: dict[str, dict],
) -> list[str]:
    """Warn for manifest entries whose tables have >8 columns."""
    warnings: list[str] = []
    for row in rows:
        inv_name = row["inventory_lookup_name"]
        entry = inventory_entries.get(inv_name)
        if not entry:
            continue
        col_count = entry.get("column_count", 0)
        if col_count > 8:
            warnings.append(
                f"Table {row['paper_order_n']} ({inv_name}) has "
                f"{col_count} columns; may render narrow in docx. "
                f"Consider whether all columns are necessary."
            )
    return warnings


def check_duplicates(rows: list[dict]) -> list[str]:
    """No two rows should share paper_order_n or inventory_lookup_name."""
    warnings: list[str] = []
    seen_n: dict[int, int] = {}
    seen_inv: dict[str, int] = {}
    for row in rows:
        n = row["paper_order_n"]
        inv = row["inventory_lookup_name"]
        if n in seen_n:
            warnings.append(
                f"duplicate paper_order_n={n} at lines "
                f"{seen_n[n]} and {row['lineno']}"
            )
        else:
            seen_n[n] = row["lineno"]
        if inv in seen_inv:
            warnings.append(
                f"duplicate inventory_lookup_name={inv!r} at lines "
                f"{seen_inv[inv]} and {row['lineno']}"
            )
        else:
            seen_inv[inv] = row["lineno"]
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_tables_manifest.py",
        description=(
            "Cross-walk tables_manifest.tsv against the tables inventory "
            "and section prose's (Table N) callouts. Advisory; always exits 0."
        ),
    )
    parser.add_argument(
        "draft_dir",
        help="Path to the paper draft directory (e.g., papers/draft_1/).",
    )
    parser.add_argument(
        "--inventory",
        default=None,
        help=(
            "Path to tables_inventory.md (default: <draft_dir>/tables_inventory.md)."
        ),
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        sys.stderr.write(
            f"[check_tables_manifest] WARN: draft_dir does not exist: "
            f"{draft_dir}; skipping checks\n"
        )
        return 0

    manifest_path = draft_dir / "tables_manifest.tsv"
    inventory_path = (
        Path(args.inventory).expanduser().resolve()
        if args.inventory
        else draft_dir / "tables_inventory.md"
    )

    all_lines: list[str] = []

    # Check 1: schema
    rows, schema_warnings = parse_manifest(manifest_path)
    all_lines.extend(
        f"[check_tables_manifest] WARN: schema: {w}" for w in schema_warnings
    )

    if not manifest_path.is_file():
        all_lines.append(
            "[check_tables_manifest] NOTE: tables_manifest.tsv missing — "
            "phase_embed_tables will have nothing to inject."
        )
        for w in all_lines:
            sys.stderr.write(f"{w}\n")
        sys.stderr.write(
            f"[check_tables_manifest] summary: 0 manifest rows, "
            f"{len(schema_warnings)} schema warnings\n"
        )
        return 0

    # Check 5 (early): duplicates
    dup_warnings = check_duplicates(rows)
    all_lines.extend(
        f"[check_tables_manifest] WARN: duplicate: {w}" for w in dup_warnings
    )

    # Parse inventory for checks 2 and 4
    inventory_entries, inv_warnings = parse_inventory_ids(inventory_path)
    all_lines.extend(
        f"[check_tables_manifest] WARN: inventory: {w}" for w in inv_warnings
    )

    # Check 2: inventory cross-reference
    if rows and inventory_entries:
        xref_warnings = check_inventory_xref(rows, inventory_entries)
        all_lines.extend(
            f"[check_tables_manifest] WARN: inventory_xref: {w}"
            for w in xref_warnings
        )

    # Check 3: callout cross-walk
    section_paths = [draft_dir / name for name in SECTION_FILES]
    callouts = collect_table_callouts(section_paths)
    callout_warnings = check_callouts_match_manifest(rows, callouts)
    for w in callout_warnings:
        if w.startswith("NOTE:"):
            all_lines.append(
                f"[check_tables_manifest] NOTE: callout_unused: {w[5:].strip()}"
            )
        else:
            all_lines.append(
                f"[check_tables_manifest] WARN: callout_orphan: {w}"
            )

    # Check 4: wide-table warning
    if inventory_entries:
        wide_warnings = check_wide_tables(rows, inventory_entries)
        all_lines.extend(
            f"[check_tables_manifest] WARN: wide_table: {w}"
            for w in wide_warnings
        )

    # Emit.
    for w in all_lines:
        sys.stderr.write(f"{w}\n")

    n_warn = sum(1 for w in all_lines if "WARN" in w[:30])
    n_note = sum(1 for w in all_lines if "NOTE" in w[:30])
    sys.stderr.write(
        f"[check_tables_manifest] summary: {len(rows)} manifest rows, "
        f"{len(callouts)} distinct callout numbers, "
        f"{n_warn} WARN, {n_note} NOTE\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
