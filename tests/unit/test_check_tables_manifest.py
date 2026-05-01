"""Unit tests for check_tables_manifest.py — v0.6 Tier 9 post-checker."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.check_tables_manifest import (
    TABLE_CALLOUT_RE,
    check_callouts_match_manifest,
    check_duplicates,
    check_inventory_xref,
    check_wide_tables,
    collect_table_callouts,
    parse_inventory_ids,
    parse_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_manifest(tmp_path: Path, rows: list[str]) -> Path:
    header = "paper_order_n\ttable_id\tinventory_lookup_name"
    content = header + "\n" + "\n".join(rows)
    return _write(tmp_path / "tables_manifest.tsv", content)


def _make_inventory(tmp_path: Path, entries: list[tuple[str, int]]) -> Path:
    """entries: list of (table_id, column_count)"""
    lines = ["<!-- inventory_schema_version: 1 -->", "# Tables Inventory", ""]
    for tid, ncol in entries:
        cols = " | ".join([f"C{i}" for i in range(ncol)])
        lines.append(f"### {tid} — Heading for {tid}")
        lines.append(f"_Columns: {ncol} ({cols})_")
        lines.append("")
    return _write(tmp_path / "tables_inventory.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------

class TestParseManifest:
    def test_valid_manifest(self, tmp_path):
        _make_manifest(tmp_path, [
            "1\ttable01_gaps\treport_tbl_01",
            "2\ttable02_concordance\treport_tbl_02",
        ])
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 2
        assert len(warnings) == 0
        assert rows[0]["paper_order_n"] == 1
        assert rows[0]["table_id"] == "table01_gaps"
        assert rows[0]["inventory_lookup_name"] == "report_tbl_01"

    def test_missing_manifest(self, tmp_path):
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 0
        assert len(warnings) == 1
        assert "not found" in warnings[0]

    def test_empty_manifest(self, tmp_path):
        _write(tmp_path / "tables_manifest.tsv", "")
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 0
        assert "empty" in warnings[0]

    def test_wrong_header(self, tmp_path):
        _write(tmp_path / "tables_manifest.tsv",
               "wrong\theader\tformat\n1\ta\tb\n")
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert any("header mismatch" in w for w in warnings)

    def test_bad_paper_order_n(self, tmp_path):
        _make_manifest(tmp_path, ["X\ttable01\treport_tbl_01"])
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 0
        assert any("not an integer" in w for w in warnings)

    def test_too_few_cells(self, tmp_path):
        _make_manifest(tmp_path, ["1\tonly_two"])
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 0
        assert any("expected 3 cells" in w for w in warnings)

    def test_too_many_cells(self, tmp_path):
        _make_manifest(tmp_path, ["1\ta\tb\textra"])
        rows, warnings = parse_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 1  # first 3 cells used
        assert any(">3 tab-separated" in w for w in warnings)


# ---------------------------------------------------------------------------
# parse_inventory_ids
# ---------------------------------------------------------------------------

class TestParseInventoryIds:
    def test_valid_inventory(self, tmp_path):
        _make_inventory(tmp_path, [
            ("report_tbl_01", 4),
            ("report_tbl_02", 9),
        ])
        entries, warnings = parse_inventory_ids(
            tmp_path / "tables_inventory.md",
        )
        assert len(entries) == 2
        assert entries["report_tbl_01"]["column_count"] == 4
        assert entries["report_tbl_02"]["column_count"] == 9

    def test_missing_inventory(self, tmp_path):
        entries, warnings = parse_inventory_ids(
            tmp_path / "tables_inventory.md",
        )
        assert len(entries) == 0
        assert any("not found" in w for w in warnings)


# ---------------------------------------------------------------------------
# callout collection
# ---------------------------------------------------------------------------

class TestCollectTableCallouts:
    def test_finds_callouts(self, tmp_path):
        _write(tmp_path / "02_results.md",
               "The data are shown in (Table 1) and discussed (Table 3).")
        callouts = collect_table_callouts([tmp_path / "02_results.md"])
        assert 1 in callouts
        assert 3 in callouts

    def test_no_callouts(self, tmp_path):
        _write(tmp_path / "02_results.md", "No table references here.")
        callouts = collect_table_callouts([tmp_path / "02_results.md"])
        assert len(callouts) == 0

    def test_missing_section_file(self, tmp_path):
        callouts = collect_table_callouts([tmp_path / "nonexistent.md"])
        assert len(callouts) == 0


# ---------------------------------------------------------------------------
# Cross-walk checks
# ---------------------------------------------------------------------------

class TestCheckInventoryXref:
    def test_valid_xref(self):
        rows = [{"lineno": 2, "paper_order_n": 1,
                 "inventory_lookup_name": "report_tbl_01"}]
        entries = {"report_tbl_01": {"column_count": 4}}
        warnings = check_inventory_xref(rows, entries)
        assert len(warnings) == 0

    def test_missing_entry(self):
        rows = [{"lineno": 2, "paper_order_n": 1,
                 "inventory_lookup_name": "report_tbl_99"}]
        entries = {"report_tbl_01": {"column_count": 4}}
        warnings = check_inventory_xref(rows, entries)
        assert len(warnings) == 1
        assert "not found" in warnings[0]


class TestCheckCalloutsMatchManifest:
    def test_matching(self):
        rows = [{"paper_order_n": 1, "lineno": 2}]
        callouts = {1: ["02_results.md"]}
        warnings = check_callouts_match_manifest(rows, callouts)
        assert len(warnings) == 0

    def test_callout_without_manifest_row(self):
        rows = [{"paper_order_n": 1, "lineno": 2}]
        callouts = {1: ["02_results.md"], 5: ["02_results.md"]}
        warnings = check_callouts_match_manifest(rows, callouts)
        assert any("Table 5" in w and "no row" in w for w in warnings)

    def test_manifest_row_without_callout(self):
        rows = [{"paper_order_n": 1, "lineno": 2},
                {"paper_order_n": 2, "lineno": 3}]
        callouts = {1: ["02_results.md"]}
        warnings = check_callouts_match_manifest(rows, callouts)
        assert any("NOTE" in w and "paper_order_n=2" in w for w in warnings)


class TestCheckWideTables:
    def test_no_warning_at_8_columns(self):
        rows = [{"paper_order_n": 1, "lineno": 2,
                 "inventory_lookup_name": "report_tbl_01"}]
        entries = {"report_tbl_01": {"column_count": 8}}
        warnings = check_wide_tables(rows, entries)
        assert len(warnings) == 0

    def test_warning_at_9_columns(self):
        rows = [{"paper_order_n": 1, "lineno": 2,
                 "inventory_lookup_name": "report_tbl_01"}]
        entries = {"report_tbl_01": {"column_count": 9}}
        warnings = check_wide_tables(rows, entries)
        assert len(warnings) == 1
        assert "9 columns" in warnings[0]


class TestCheckDuplicates:
    def test_no_duplicates(self):
        rows = [
            {"lineno": 2, "paper_order_n": 1,
             "inventory_lookup_name": "report_tbl_01"},
            {"lineno": 3, "paper_order_n": 2,
             "inventory_lookup_name": "report_tbl_02"},
        ]
        warnings = check_duplicates(rows)
        assert len(warnings) == 0

    def test_duplicate_paper_order_n(self):
        rows = [
            {"lineno": 2, "paper_order_n": 1,
             "inventory_lookup_name": "report_tbl_01"},
            {"lineno": 3, "paper_order_n": 1,
             "inventory_lookup_name": "report_tbl_02"},
        ]
        warnings = check_duplicates(rows)
        assert len(warnings) == 1
        assert "duplicate paper_order_n=1" in warnings[0]

    def test_duplicate_inventory_lookup(self):
        rows = [
            {"lineno": 2, "paper_order_n": 1,
             "inventory_lookup_name": "report_tbl_01"},
            {"lineno": 3, "paper_order_n": 2,
             "inventory_lookup_name": "report_tbl_01"},
        ]
        warnings = check_duplicates(rows)
        assert len(warnings) == 1
        assert "duplicate inventory_lookup_name" in warnings[0]


# ---------------------------------------------------------------------------
# Callout regex
# ---------------------------------------------------------------------------

class TestCalloutRegex:
    def test_simple_callout(self):
        m = TABLE_CALLOUT_RE.search("(Table 1)")
        assert m and m.group(1) == "1"

    def test_multi_digit(self):
        m = TABLE_CALLOUT_RE.search("(Table 12)")
        assert m and m.group(1) == "12"

    def test_no_match_on_plain_text(self):
        assert TABLE_CALLOUT_RE.search("table of contents") is None

    def test_compound_callout(self):
        matches = TABLE_CALLOUT_RE.findall("(Table 1 and Table 3)")
        assert matches == ["1", "3"]
