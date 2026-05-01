"""Unit tests for extract_tables.py — v0.6 Tier 9 table inventory.

Tests cover: scanner (pipe detection, code-block exclusion, edge
positions), caption extraction (heading/sentence/both/neither),
structure metadata (column types, |fit| trap, row counts), inventory
formatting, JSON serialization.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.extract_tables import (
    ReportTableOrigin,
    TableCaptionCandidate,
    TableInventoryReport,
    TableRecord,
    _classify_column_type,
    _find_nearest_heading,
    _find_preceding_sentence,
    _is_in_fenced_code_block,
    _is_pipe_row,
    _parse_column_names,
    _parse_data_cells,
    extract_tables,
    format_tables_inventory_md,
    scan_report_tables,
)


# ---------------------------------------------------------------------------
# _is_pipe_row
# ---------------------------------------------------------------------------

class TestIsPipeRow:
    def test_simple_row(self):
        assert _is_pipe_row("| a | b | c |")

    def test_no_leading_pipe(self):
        assert not _is_pipe_row("a | b | c |")

    def test_no_trailing_pipe(self):
        assert not _is_pipe_row("| a | b | c")

    def test_single_pipe(self):
        assert not _is_pipe_row("|")

    def test_empty_string(self):
        assert not _is_pipe_row("")

    def test_with_whitespace(self):
        assert _is_pipe_row("  | a | b |  ")


# ---------------------------------------------------------------------------
# _is_in_fenced_code_block
# ---------------------------------------------------------------------------

class TestFencedCodeBlock:
    def test_not_in_block(self):
        lines = ["some text", "| a | b |"]
        assert not _is_in_fenced_code_block(lines, 1)

    def test_inside_backtick_block(self):
        lines = ["```", "| a | b |", "```"]
        assert _is_in_fenced_code_block(lines, 1)

    def test_inside_tilde_block(self):
        lines = ["~~~", "| a | b |", "~~~"]
        assert _is_in_fenced_code_block(lines, 1)

    def test_after_closed_block(self):
        lines = ["```", "code", "```", "| a | b |"]
        assert not _is_in_fenced_code_block(lines, 3)

    def test_nested_not_confused(self):
        # Second ``` closes the first — line 3 is outside
        lines = ["```", "code", "```", "| a | b |"]
        assert not _is_in_fenced_code_block(lines, 3)


# ---------------------------------------------------------------------------
# _parse_column_names — including the |fit| trap
# ---------------------------------------------------------------------------

class TestParseColumnNames:
    def test_simple_columns(self):
        assert _parse_column_names("| a | b | c |") == ["a", "b", "c"]

    def test_extra_whitespace(self):
        assert _parse_column_names("|  a  |  b  |") == ["a", "b"]

    def test_fit_trap(self):
        """The |fit| column name contains pipe chars — must not split."""
        result = _parse_column_names("| Organism | Locus | |fit| | Condition |")
        assert "|fit|" in result
        assert "Organism" in result
        assert "Condition" in result

    def test_fit_trap_does_not_infect_neighbors(self):
        """Condition before |fit| must NOT become |Condition|."""
        result = _parse_column_names(
            "| Organism | Locus | Condition | |fit| | Carrier env |"
        )
        assert result == [
            "Organism", "Locus", "Condition", "|fit|", "Carrier env",
        ]

    def test_single_column(self):
        assert _parse_column_names("| name |") == ["name"]

    def test_complex_header(self):
        result = _parse_column_names(
            "| Rank | Organism | Locus | |fit| | Condition | Score |"
        )
        assert len(result) == 6
        assert result[0] == "Rank"
        assert result[3] == "|fit|"
        assert result[5] == "Score"


# ---------------------------------------------------------------------------
# _parse_data_cells
# ---------------------------------------------------------------------------

class TestParseDataCells:
    def test_simple_row(self):
        cells = _parse_data_cells("| 1 | foo | bar |", 3)
        assert cells == ["1", "foo", "bar"]

    def test_fit_value(self):
        """Data rows with |fit| column values like 4.8."""
        cells = _parse_data_cells(
            "| *P. putida* | PP_0025 | stress | 4.8 | clinical |", 5
        )
        assert len(cells) == 5
        assert cells[3] == "4.8"


# ---------------------------------------------------------------------------
# scan_report_tables
# ---------------------------------------------------------------------------

class TestScanReportTables:
    def test_single_table(self):
        text = textwrap.dedent("""\
            ## Heading

            Some intro text.

            | A | B |
            |---|---|
            | 1 | 2 |
            | 3 | 4 |

            After text.
        """)
        tables = scan_report_tables(text)
        assert len(tables) == 1
        assert len(tables[0]["data_lines"]) == 2

    def test_two_tables(self):
        text = textwrap.dedent("""\
            | A | B |
            |---|---|
            | 1 | 2 |

            | X | Y | Z |
            |---|---|---|
            | a | b | c |
        """)
        tables = scan_report_tables(text)
        assert len(tables) == 2
        assert len(tables[0]["data_lines"]) == 1
        assert len(tables[1]["data_lines"]) == 1

    def test_table_at_end_of_file(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        tables = scan_report_tables(text)
        assert len(tables) == 1

    def test_table_in_code_block_excluded(self):
        text = textwrap.dedent("""\
            ```
            | A | B |
            |---|---|
            | 1 | 2 |
            ```
        """)
        tables = scan_report_tables(text)
        assert len(tables) == 0

    def test_pipe_row_without_separator_not_table(self):
        text = "| not a table | really |\n| also not | a table |"
        tables = scan_report_tables(text)
        assert len(tables) == 0

    def test_table_immediately_after_heading(self):
        text = textwrap.dedent("""\
            ## Summary
            | A | B |
            |---|---|
            | 1 | 2 |
        """)
        tables = scan_report_tables(text)
        assert len(tables) == 1

    def test_wide_table(self):
        cols = " | ".join([f"C{i}" for i in range(10)])
        seps = " | ".join(["---"] * 10)
        vals = " | ".join([str(i) for i in range(10)])
        text = f"| {cols} |\n| {seps} |\n| {vals} |"
        tables = scan_report_tables(text)
        assert len(tables) == 1
        assert len(tables[0]["data_lines"]) == 1


# ---------------------------------------------------------------------------
# Caption extraction
# ---------------------------------------------------------------------------

class TestFindNearestHeading:
    def test_heading_directly_above(self):
        lines = ["## My Table", "", "| A | B |"]
        result = _find_nearest_heading(lines, 2)
        assert result == "My Table"

    def test_h3_heading(self):
        lines = ["### Sub heading", "| A | B |"]
        result = _find_nearest_heading(lines, 1)
        assert result == "Sub heading"

    def test_no_heading_within_lookback(self):
        lines = ["no heading here", "| A | B |"]
        result = _find_nearest_heading(lines, 1)
        assert result is None

    def test_heading_far_above(self):
        lines = ["## Heading"] + [""] * 40 + ["| A | B |"]
        result = _find_nearest_heading(lines, 41)
        assert result == "Heading"

    def test_beyond_lookback_limit(self):
        lines = ["## Heading"] + [""] * 55 + ["| A | B |"]
        result = _find_nearest_heading(lines, 56)
        assert result is None


class TestFindPrecedingSentence:
    def test_paragraph_before_table(self):
        lines = [
            "## Heading",
            "",
            "Here is the introductory sentence.",
            "",
            "| A | B |",
        ]
        result = _find_preceding_sentence(lines, 4)
        assert result == "Here is the introductory sentence."

    def test_short_paragraph_returned_whole(self):
        """Short paragraphs (<100 chars) are returned whole — they're
        typically a single-idea intro to the table."""
        lines = [
            "First sentence. Second sentence. The table follows.",
            "",
            "| A | B |",
        ]
        result = _find_preceding_sentence(lines, 2)
        assert result == "First sentence. Second sentence. The table follows."

    def test_long_paragraph_returns_last_sentence(self):
        """Long paragraphs (>100 chars) return only the last sentence."""
        long_para = (
            "This is a very long introductory paragraph that explains "
            "the methodology in great detail and provides extensive "
            "context for the reader. The following table summarizes the results."
        )
        lines = [long_para, "", "| A | B |"]
        result = _find_preceding_sentence(lines, 2)
        assert result == "The following table summarizes the results."

    def test_heading_directly_above(self):
        """If only a heading is above (no paragraph), return None."""
        lines = ["## Heading", "| A | B |"]
        result = _find_preceding_sentence(lines, 1)
        assert result is None

    def test_no_content_above(self):
        lines = ["| A | B |"]
        result = _find_preceding_sentence(lines, 0)
        assert result is None

    def test_blank_lines_between(self):
        lines = [
            "The concordant groups are shown below.",
            "",
            "",
            "| A | B |",
        ]
        result = _find_preceding_sentence(lines, 3)
        assert result == "The concordant groups are shown below."


# ---------------------------------------------------------------------------
# Column type classification
# ---------------------------------------------------------------------------

class TestClassifyColumnType:
    def test_all_numeric(self):
        assert _classify_column_type(["1", "2.5", "3e-6"]) == "numeric"

    def test_all_text(self):
        assert _classify_column_type(["foo", "bar", "baz"]) == "text"

    def test_mixed(self):
        assert _classify_column_type(["1", "foo", "3", "bar", "5"]) == "mixed"

    def test_numeric_with_dashes(self):
        """Em-dash '—' used as missing-data marker should be ignored."""
        assert _classify_column_type(["1", "2", "—", "4"]) == "numeric"

    def test_percentage(self):
        assert _classify_column_type(["14.3%", "2.5%", "62.5%"]) == "numeric"

    def test_empty_cells(self):
        assert _classify_column_type(["", "", ""]) == "text"

    def test_negative_numbers(self):
        assert _classify_column_type(["-0.298", "+0.157", "0.109"]) == "numeric"

    def test_inf(self):
        assert _classify_column_type(["inf", "27.5", "11.6"]) == "numeric"


# ---------------------------------------------------------------------------
# Full extraction pipeline (with tmp_path fixture)
# ---------------------------------------------------------------------------

def _make_report(tmp_path: Path, content: str) -> Path:
    """Write a REPORT.md to tmp_path and return the project dir."""
    report = tmp_path / "REPORT.md"
    report.write_text(content, encoding="utf-8")
    return tmp_path


class TestExtractTables:
    def test_no_report(self, tmp_path):
        report = extract_tables(tmp_path)
        assert len(report.tables) == 0

    def test_empty_report(self, tmp_path):
        _make_report(tmp_path, "# Report\n\nNo tables here.\n")
        report = extract_tables(tmp_path)
        assert len(report.tables) == 0

    def test_single_table(self, tmp_path):
        content = textwrap.dedent("""\
            # Report

            ## Pathway gaps

            The following pathways have gaps:

            | Pathway | Category | Count |
            |---------|----------|------:|
            | Fucose | carbon | 32 |
            | Rhamnose | carbon | 31 |

            More text follows.
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        assert len(report.tables) == 1
        tbl = report.tables[0]
        assert tbl.table_id == "report_tbl_01"
        assert tbl.column_count == 3
        assert tbl.row_count == 2
        assert tbl.column_names == ["Pathway", "Category", "Count"]
        assert tbl.section_heading == "Pathway gaps"
        assert len(tbl.captions) == 2  # heading + preceding sentence
        assert tbl.captions[0].source == "heading"
        assert tbl.captions[1].source == "preceding_sentence"

    def test_column_types_detected(self, tmp_path):
        content = textwrap.dedent("""\
            ## Data

            | Name | Score | Category |
            |------|------:|----------|
            | foo | 1.5 | A |
            | bar | 2.3 | B |
            | baz | 3.7 | C |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        tbl = report.tables[0]
        assert tbl.column_types[0] == "text"     # Name
        assert tbl.column_types[1] == "numeric"   # Score
        assert tbl.column_types[2] == "text"      # Category

    def test_multiple_tables(self, tmp_path):
        content = textwrap.dedent("""\
            ## Section 1

            | A | B |
            |---|---|
            | 1 | 2 |

            ## Section 2

            | X | Y | Z |
            |---|---|---|
            | a | b | c |
            | d | e | f |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        assert len(report.tables) == 2
        assert report.tables[0].table_id == "report_tbl_01"
        assert report.tables[1].table_id == "report_tbl_02"
        assert report.tables[0].row_count == 1
        assert report.tables[1].row_count == 2

    def test_wide_table_flagged_in_summary(self, tmp_path):
        cols = " | ".join([f"C{i}" for i in range(10)])
        seps = " | ".join(["---"] * 10)
        vals = " | ".join([str(i) for i in range(10)])
        content = f"## Wide\n\n| {cols} |\n| {seps} |\n| {vals} |"
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        summary = report.to_dict()["summary"]
        assert summary["wide_tables_gt8"] == 1

    def test_table_in_code_block_skipped(self, tmp_path):
        content = textwrap.dedent("""\
            ## Data

            ```
            | A | B |
            |---|---|
            | 1 | 2 |
            ```

            Not a table.
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        assert len(report.tables) == 0

    def test_no_heading_still_works(self, tmp_path):
        content = textwrap.dedent("""\
            Some paragraph text introducing a table.

            | A | B |
            |---|---|
            | 1 | 2 |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        assert len(report.tables) == 1
        tbl = report.tables[0]
        assert tbl.section_heading == ""
        # Should still have preceding_sentence caption
        assert any(c.source == "preceding_sentence" for c in tbl.captions)


# ---------------------------------------------------------------------------
# JSON serialization round-trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_round_trip(self, tmp_path):
        content = textwrap.dedent("""\
            ## Test Table

            Intro sentence for the table.

            | Name | Value |
            |------|------:|
            | a | 1 |
            | b | 2 |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        d = report.to_dict()

        # Verify serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)

        assert parsed["source_file"] == "REPORT.md"
        assert len(parsed["tables"]) == 1
        assert parsed["tables"][0]["table_id"] == "report_tbl_01"
        assert parsed["tables"][0]["column_count"] == 2
        assert parsed["summary"]["total_tables"] == 1


# ---------------------------------------------------------------------------
# Inventory markdown formatter
# ---------------------------------------------------------------------------

class TestFormatTablesInventoryMd:
    def test_empty_inventory(self):
        report = TableInventoryReport(
            project_dir="/tmp/test", source_file="REPORT.md", tables=[],
        )
        md = format_tables_inventory_md(report)
        assert "no markdown tables found" in md

    def test_inventory_has_schema_version(self, tmp_path):
        content = textwrap.dedent("""\
            ## Heading

            | A | B |
            |---|---|
            | 1 | 2 |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        md = format_tables_inventory_md(report)
        assert "inventory_schema_version: 1" in md

    def test_inventory_includes_caption(self, tmp_path):
        content = textwrap.dedent("""\
            ## Pathway gaps

            The following pathways have significant gaps:

            | Pathway | Count |
            |---------|------:|
            | Fucose | 32 |
        """)
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        md = format_tables_inventory_md(report)
        assert "section heading" in md
        assert "Pathway gaps" in md
        assert "preceding sentence" in md

    def test_inventory_content_preview(self, tmp_path):
        # 5 data rows — preview should show only 3
        rows = "\n".join([f"| item{i} | {i} |" for i in range(5)])
        content = f"## Data\n\n| Name | Val |\n|------|-----|\n{rows}"
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        md = format_tables_inventory_md(report)
        assert "5 data rows total" in md

    def test_inventory_wide_table_warning(self, tmp_path):
        cols = " | ".join([f"C{i}" for i in range(10)])
        seps = " | ".join(["---"] * 10)
        vals = " | ".join(["x"] * 10)
        content = f"## Wide\n\n| {cols} |\n| {seps} |\n| {vals} |"
        _make_report(tmp_path, content)
        report = extract_tables(tmp_path)
        md = format_tables_inventory_md(report)
        assert "Wide table" in md
