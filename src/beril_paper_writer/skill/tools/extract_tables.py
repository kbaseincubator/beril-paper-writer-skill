#!/usr/bin/env python3
"""extract_tables.py — table inventory and caption candidates from REPORT.md.

Per v0.6 punch list (smoke-test/v0_6_punch_list.md): Tier 9 tables pipeline.
v0.6 sources tables EXCLUSIVELY from REPORT.md markdown pipe-tables.
TSV/CSV files and notebook DataFrame outputs are backing data, not paper
artifacts (design decision locked 2026-04-29).

This script inventories every markdown table in REPORT.md and gathers
caption candidates from two sources:

  1. Section heading — the nearest ## or ### heading above the table.
     This is the STRONGEST source (parallel to REPORT.md alt-text for
     figures).

  2. Preceding sentence — the last sentence of the paragraph immediately
     before the table (the introductory sentence). Second priority.

This script does NOT select which tables end up in the manuscript —
that's prompt-driven (results.v1, given the throughline, picks N from
the inventory). Per the figures parallel, the writer must REUSE existing
REPORT.md tables; missing tables become explicit gap-fill requests.

Standalone-script + importable-module pattern, mirroring
extract_figures.py and extract_methods.py.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Table separator detection (shared with assemble_docx.py)
# ---------------------------------------------------------------------------

# Markdown table separator: `|---|---|` or `| --- | --- |` or `|:---:|`
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|\-]+\|$")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ReportTableOrigin:
    """Where a table was found in REPORT.md."""

    line_start: int       # first pipe-row line number (1-based)
    line_end: int         # last pipe-row line number (1-based)
    section_heading: str  # nearest ## or ### heading above

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class TableCaptionCandidate:
    """One caption candidate for a table."""

    source: str           # "heading" | "preceding_sentence"
    text: str
    context: str          # section name or surrounding text

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class TableRecord:
    """All metadata for one REPORT.md markdown table."""

    table_id: str               # sequential: "report_tbl_01", ...
    section_heading: str        # nearest heading
    column_names: list[str]     # parsed from header row
    column_count: int
    row_count: int              # data rows (excluding header + separator)
    column_types: list[str]     # heuristic: "numeric" | "text" | "mixed"
    markdown_content: str       # full pipe-table text (verbatim, including
                                # header + separator + all data rows)
    captions: list[TableCaptionCandidate] = field(default_factory=list)
    origin: ReportTableOrigin = field(
        default_factory=lambda: ReportTableOrigin(0, 0, "")
    )

    def to_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "section_heading": self.section_heading,
            "column_names": list(self.column_names),
            "column_count": self.column_count,
            "row_count": self.row_count,
            "column_types": list(self.column_types),
            "markdown_content": self.markdown_content,
            "captions": [c.to_dict() for c in self.captions],
            "origin": self.origin.to_dict(),
        }


@dataclass
class TableInventoryReport:
    """Top-level report from a table-extraction run."""

    project_dir: str
    source_file: str      # always "REPORT.md" for v0.6
    tables: list[TableRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "project_dir": self.project_dir,
            "source_file": self.source_file,
            "tables": [t.to_dict() for t in self.tables],
        }
        d["summary"] = self._summary()
        return d

    def _summary(self) -> dict:
        total_cols = [t.column_count for t in self.tables]
        total_rows = [t.row_count for t in self.tables]
        wide = sum(1 for t in self.tables if t.column_count > 8)
        with_heading_caption = sum(
            1 for t in self.tables
            if any(c.source == "heading" for c in t.captions)
        )
        with_sentence_caption = sum(
            1 for t in self.tables
            if any(c.source == "preceding_sentence" for c in t.captions)
        )
        return {
            "total_tables": len(self.tables),
            "column_counts": total_cols,
            "row_counts": total_rows,
            "wide_tables_gt8": wide,
            "with_heading_caption": with_heading_caption,
            "with_sentence_caption": with_sentence_caption,
        }


# ---------------------------------------------------------------------------
# REPORT.md table scanner
# ---------------------------------------------------------------------------

def _is_pipe_row(line: str) -> bool:
    """True if ``line`` looks like a markdown table row (starts and ends
    with ``|`` after stripping whitespace)."""
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def _is_in_fenced_code_block(lines: list[str], target_idx: int) -> bool:
    """True if ``target_idx`` falls inside a fenced code block (``` or ~~~)."""
    in_block = False
    for i in range(target_idx):
        stripped = lines[i].strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_block = not in_block
    return in_block


def _parse_column_names(header_line: str) -> list[str]:
    """Parse column names from the header row of a markdown table.

    Handles the ``|fit|`` double-pipe trap: if a header cell itself
    contains pipe characters (e.g. ``|fit|``), the naive split on ``|``
    will produce empty-string fragments.

    The ``|foo|`` pattern in a split looks like: ``['', 'foo', '']`` —
    an empty cell, then a short non-empty cell, then an empty cell.
    This is distinct from a normal cell boundary (``['Condition', '', 'fit', '', 'Carrier']``
    where the empty fragments belong to the ``|foo|`` pattern, not to
    ``Condition``).

    Strategy: first pass identifies ``(empty, short-word, empty)``
    triplets and marks them as ``|word|`` column names. Second pass
    collects remaining non-empty cells.
    """
    # Strip leading/trailing pipe and split on |
    inner = header_line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]

    raw_cells = inner.split("|")

    # First pass: identify |foo| triplets (empty, short-word, empty).
    # Mark consumed indices so second pass skips them.
    consumed: set[int] = set()
    piped_names: dict[int, str] = {}  # index → "|foo|" merged name

    i = 0
    while i < len(raw_cells):
        cell = raw_cells[i].strip()
        if (
            cell == ""
            and i + 2 < len(raw_cells)
            and raw_cells[i + 1].strip() != ""
            and len(raw_cells[i + 1].strip()) < 10
            and raw_cells[i + 2].strip() == ""
        ):
            word = raw_cells[i + 1].strip()
            piped_names[i] = f"|{word}|"
            consumed.update({i, i + 1, i + 2})
            i += 3
        else:
            i += 1

    # Second pass: collect columns in order.
    merged: list[str] = []
    i = 0
    while i < len(raw_cells):
        if i in piped_names:
            merged.append(piped_names[i])
            i += 3  # skip the triplet
        elif i in consumed:
            i += 1
        else:
            cell = raw_cells[i].strip()
            if cell:
                merged.append(cell)
            i += 1

    return merged


def _parse_separator_column_count(sep_line: str) -> int:
    """Count columns from the separator row (e.g. |---|---|---|)."""
    inner = sep_line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return len([c for c in inner.split("|") if c.strip()])


def _parse_data_cells(row_line: str, n_cols: int) -> list[str]:
    """Parse a data row into cells, respecting the column count from the
    header. This avoids the |foo| trap by greedily assigning cells based
    on known column count.

    For v0.6, we do a simple split and use the header's column count to
    validate. If the split produces more cells than expected, we attempt
    the same merge heuristic as column names.
    """
    inner = row_line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]

    raw_cells = inner.split("|")
    cells = [c.strip() for c in raw_cells]

    if len(cells) == n_cols:
        return cells

    # Attempt merge for |foo| triplet patterns (empty, word, empty)
    # — same logic as _parse_column_names second pass.
    consumed: set[int] = set()
    piped_vals: dict[int, str] = {}
    i = 0
    while i < len(raw_cells):
        cell = raw_cells[i].strip()
        if (
            cell == ""
            and i + 2 < len(raw_cells)
            and raw_cells[i + 1].strip() != ""
            and len(raw_cells[i + 1].strip()) < 10
            and raw_cells[i + 2].strip() == ""
        ):
            piped_vals[i] = raw_cells[i + 1].strip()
            consumed.update({i, i + 1, i + 2})
            i += 3
        else:
            i += 1

    merged: list[str] = []
    i = 0
    while i < len(raw_cells):
        if i in piped_vals:
            merged.append(piped_vals[i])
            i += 3
        elif i in consumed:
            i += 1
        else:
            cell = raw_cells[i].strip()
            if cell:
                merged.append(cell)
            i += 1

    if len(merged) == n_cols:
        return merged

    # Fallback: return raw split, trimmed to n_cols
    trimmed = [c.strip() for c in raw_cells if c.strip()]
    return trimmed[:n_cols]


def scan_report_tables(text: str) -> list[dict]:
    """Scan markdown text for pipe-delimited tables.

    Returns a list of dicts, each with:
      - ``lines``: list of raw line strings (header + separator + data)
      - ``line_start``: 1-based line number of the header row
      - ``line_end``: 1-based line number of the last data row
      - ``header_line``: the header row string
      - ``separator_line``: the separator row string
      - ``data_lines``: list of data row strings
    """
    all_lines = text.split("\n")
    tables: list[dict] = []
    i = 0

    while i < len(all_lines):
        stripped = all_lines[i].strip()

        # A markdown table starts with a pipe row whose NEXT line is the
        # separator.
        if (
            _is_pipe_row(stripped)
            and i + 1 < len(all_lines)
            and _TABLE_SEPARATOR_RE.match(all_lines[i + 1].strip())
        ):
            # Check we're not inside a fenced code block
            if _is_in_fenced_code_block(all_lines, i):
                i += 1
                continue

            header_line = all_lines[i]
            separator_line = all_lines[i + 1]
            table_lines = [header_line, separator_line]
            line_start = i + 1  # 1-based

            j = i + 2
            data_lines: list[str] = []
            while j < len(all_lines):
                row_stripped = all_lines[j].strip()
                if _is_pipe_row(row_stripped):
                    data_lines.append(all_lines[j])
                    table_lines.append(all_lines[j])
                    j += 1
                else:
                    break

            line_end = j  # 1-based (j is the line AFTER the last data row)
            if data_lines:
                line_end = (i + 2 + len(data_lines))  # 1-based last data row

            tables.append({
                "lines": table_lines,
                "line_start": line_start,
                "line_end": line_end,
                "header_line": header_line,
                "separator_line": separator_line,
                "data_lines": data_lines,
            })
            i = j  # skip past the table
        else:
            i += 1

    return tables


# ---------------------------------------------------------------------------
# Caption candidate extraction
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# Sentence-ending punctuation followed by whitespace or EOL
_SENTENCE_END_RE = re.compile(r"[.!?:]\s*$")


def _find_nearest_heading(lines: list[str], before_idx: int,
                          max_lookback: int = 50) -> Optional[str]:
    """Walk upward from ``before_idx`` (0-based) to find the nearest heading.

    Returns the heading text (without the ``#`` prefix) or None if no
    heading is found within ``max_lookback`` lines.
    """
    start = max(0, before_idx - max_lookback)
    for i in range(before_idx - 1, start - 1, -1):
        m = _HEADING_RE.match(lines[i].strip())
        if m:
            return m.group(2).strip()
    return None


def _find_preceding_sentence(lines: list[str], before_idx: int) -> Optional[str]:
    """Find the last sentence of the paragraph immediately before
    ``before_idx`` (0-based line index of the table's header row).

    Walk upward from ``before_idx - 1``, skip blank lines, then collect
    the last non-blank paragraph. Return its last sentence.
    """
    # Skip blank lines immediately above the table
    i = before_idx - 1
    while i >= 0 and lines[i].strip() == "":
        i -= 1

    if i < 0:
        return None

    # Check if this is a heading (not a paragraph) — skip to avoid
    # returning the heading as a "sentence"
    if _HEADING_RE.match(lines[i].strip()):
        return None

    # Collect the paragraph (contiguous non-blank, non-heading lines)
    para_lines: list[str] = []
    while i >= 0 and lines[i].strip() != "" and not _HEADING_RE.match(lines[i].strip()):
        para_lines.insert(0, lines[i].strip())
        i -= 1

    if not para_lines:
        return None

    paragraph = " ".join(para_lines)

    # Extract the last sentence. Simple heuristic: split on sentence-
    # ending punctuation, take the last non-empty fragment.
    # For robustness, just return the full paragraph if it's short (<100 chars).
    if len(paragraph) < 100:
        return paragraph

    # Split on sentence boundaries (period/!/? followed by space + capital)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph)
    if sentences:
        return sentences[-1].rstrip()
    return paragraph


# ---------------------------------------------------------------------------
# Column-type heuristic
# ---------------------------------------------------------------------------

_NUMERIC_RE = re.compile(
    r"^[−\-+]?\d+(?:[.,]\d+)?(?:[eE][−\-+]?\d+)?$"  # int/float/scientific
    r"|^[−\-+]?\d+(?:\.\d+)?%$"                       # percentage
    r"|^inf$|^-inf$|^nan$"                              # special
    r"|^\d+\.\d+[eE][−\-+]?\d+$"                      # 7e-6 form
)


def _classify_column_type(cells: list[str]) -> str:
    """Classify a column as 'numeric', 'text', or 'mixed' based on its
    data cells (header excluded)."""
    if not cells:
        return "text"

    numeric_count = 0
    non_empty_count = 0
    for cell in cells:
        cell = cell.strip()
        if cell == "" or cell == "—" or cell == "—":
            continue
        non_empty_count += 1
        # Strip markdown formatting (bold, italic)
        clean = re.sub(r"[*_`]", "", cell).strip()
        if _NUMERIC_RE.match(clean):
            numeric_count += 1

    if non_empty_count == 0:
        return "text"
    ratio = numeric_count / non_empty_count
    if ratio >= 0.8:
        return "numeric"
    elif ratio <= 0.2:
        return "text"
    else:
        return "mixed"


# ---------------------------------------------------------------------------
# Core extraction pipeline
# ---------------------------------------------------------------------------

def extract_tables(project_dir: Path) -> TableInventoryReport:
    """Run the full table-extraction pipeline against a project directory.

    v0.6: sources exclusively from REPORT.md.
    """
    report_path = project_dir / "REPORT.md"
    if not report_path.is_file():
        return TableInventoryReport(
            project_dir=str(project_dir),
            source_file="REPORT.md",
            tables=[],
        )

    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return TableInventoryReport(
            project_dir=str(project_dir),
            source_file="REPORT.md",
            tables=[],
        )

    all_lines = text.split("\n")
    raw_tables = scan_report_tables(text)

    records: list[TableRecord] = []
    for seq, tbl in enumerate(raw_tables, start=1):
        table_id = f"report_tbl_{seq:02d}"
        header_line = tbl["header_line"]
        data_lines = tbl["data_lines"]
        line_start = tbl["line_start"]
        line_end = tbl["line_end"]

        # Column names from header
        column_names = _parse_column_names(header_line)
        column_count = len(column_names)

        # Row count (data rows only)
        row_count = len(data_lines)

        # Column types from data cells
        if data_lines and column_count > 0:
            columns_data: list[list[str]] = [[] for _ in range(column_count)]
            for dline in data_lines:
                cells = _parse_data_cells(dline, column_count)
                for ci, cell in enumerate(cells):
                    if ci < column_count:
                        columns_data[ci].append(cell)
            column_types = [_classify_column_type(col) for col in columns_data]
        else:
            column_types = ["text"] * column_count

        # Full markdown content (verbatim)
        markdown_content = "\n".join(tbl["lines"])

        # Section heading (nearest heading above the table)
        # line_start is 1-based; convert to 0-based for array indexing
        heading_0idx = line_start - 1
        section_heading = _find_nearest_heading(all_lines, heading_0idx) or ""

        # Caption candidates
        captions: list[TableCaptionCandidate] = []
        if section_heading:
            captions.append(TableCaptionCandidate(
                source="heading",
                text=section_heading,
                context=f"REPORT.md line {line_start}",
            ))

        preceding = _find_preceding_sentence(all_lines, heading_0idx)
        if preceding:
            captions.append(TableCaptionCandidate(
                source="preceding_sentence",
                text=preceding,
                context=f"paragraph before table at line {line_start}",
            ))

        origin = ReportTableOrigin(
            line_start=line_start,
            line_end=line_end,
            section_heading=section_heading,
        )

        records.append(TableRecord(
            table_id=table_id,
            section_heading=section_heading,
            column_names=column_names,
            column_count=column_count,
            row_count=row_count,
            column_types=column_types,
            markdown_content=markdown_content,
            captions=captions,
            origin=origin,
        ))

    return TableInventoryReport(
        project_dir=str(project_dir),
        source_file="REPORT.md",
        tables=records,
    )


# ---------------------------------------------------------------------------
# tables_inventory.md formatter
# ---------------------------------------------------------------------------

_INVENTORY_PREVIEW_ROWS = 3


def format_tables_inventory_md(report: TableInventoryReport) -> str:
    """Render the table inventory as a human-readable markdown document.

    The Table-selection sub-process in results.v1 consumes this to choose
    N tables that support the chosen throughline. Format mirrors
    figures_inventory.md.
    """
    out: list[str] = []
    out.append("<!-- inventory_schema_version: 1 -->")
    out.append("# Tables Inventory")
    out.append("")
    out.append(
        f"Auto-generated from `extract_tables.py` over `{report.project_dir}`. "
        f"Each table below comes with caption candidates ranked by source: "
        f"section-heading first (the project's own authored heading), then "
        f"preceding-sentence (the introductory paragraph's last sentence) as "
        f"a fallback. v0.6 sources tables exclusively from REPORT.md markdown "
        f"pipe-tables."
    )
    out.append("")

    s = report.to_dict()["summary"]
    out.append("## Summary")
    out.append("")
    out.append(f"- Total tables: **{s['total_tables']}**")
    out.append(f"- Column counts: {s['column_counts']}")
    out.append(f"- Row counts: {s['row_counts']}")
    if s["wide_tables_gt8"] > 0:
        out.append(
            f"- **Wide tables (>8 columns): {s['wide_tables_gt8']}** "
            f"— may render narrow in docx"
        )
    out.append(f"- With heading caption: {s['with_heading_caption']}")
    out.append(f"- With preceding-sentence caption: {s['with_sentence_caption']}")
    out.append("")

    if not report.tables:
        out.append("_(no markdown tables found in REPORT.md)_")
        return "\n".join(out)

    out.append("## Tables")
    out.append("")
    for tbl in report.tables:
        out.append(f"### {tbl.table_id} — {tbl.section_heading or '(no heading)'}")
        out.append("")
        out.append(
            f"_Columns: {tbl.column_count} "
            f"({' | '.join(tbl.column_names)})_"
        )
        out.append(f"_Rows: {tbl.row_count}_")
        out.append(
            f"_Column types: {' | '.join(tbl.column_types)}_"
        )
        if tbl.column_count > 8:
            out.append(
                f"_**Wide table** ({tbl.column_count} columns) — "
                f"may render narrow in docx_"
            )
        out.append("")

        # Caption candidates
        if tbl.captions:
            out.append("**Caption candidates:**")
            out.append("")
            for c in tbl.captions:
                src_label = {
                    "heading": "section heading",
                    "preceding_sentence": "preceding sentence",
                    "llm": "LLM-generated",
                }.get(c.source, c.source)
                out.append(f"- **{src_label}**: {c.text}")
            out.append("")

        # Content preview (first N rows)
        content_lines = tbl.markdown_content.split("\n")
        # Always show header + separator + up to _INVENTORY_PREVIEW_ROWS data rows
        preview_lines = content_lines[:2 + _INVENTORY_PREVIEW_ROWS]
        out.append("**Content (first 3 rows):**")
        out.append("")
        for line in preview_lines:
            out.append(line)
        total_data = len(content_lines) - 2  # header + separator excluded
        if total_data > _INVENTORY_PREVIEW_ROWS:
            out.append("")
            out.append(
                f"_({total_data} data rows total; see REPORT.md "
                f"lines {tbl.origin.line_start}–{tbl.origin.line_end} "
                f"for full table)_"
            )
        out.append("")

        # Origin
        out.append(
            f"_Source: REPORT.md lines {tbl.origin.line_start}–"
            f"{tbl.origin.line_end}_"
        )
        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="extract_tables.py",
        description=(
            "Inventory markdown tables in a BERIL project's REPORT.md and "
            "gather caption candidates from section headings and preceding "
            "sentences. Writes JSON to stdout and (optionally) "
            "tables_inventory.md to --output-dir. Selection of which "
            "tables to embed is done downstream by results.v1."
        ),
    )
    p.add_argument(
        "project_dir",
        type=Path,
        help="Path to the BERIL project directory (projects/<id>/).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write tables_inventory.md (default: do not "
            "write a file; JSON-only)."
        ),
    )
    p.add_argument(
        "--no-md",
        action="store_true",
        help="Suppress tables_inventory.md write even if --output-dir set.",
    )
    args = p.parse_args(argv)

    if not args.project_dir.is_dir():
        print(
            f"Error: project_dir does not exist or is not a directory: "
            f"{args.project_dir}",
            file=sys.stderr,
        )
        return 1

    report = extract_tables(args.project_dir)
    payload = json.dumps(report.to_dict(), indent=2)
    sys.stdout.write(payload + "\n")

    if args.output_dir is not None and not args.no_md:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = args.output_dir / "tables_inventory.md"
        md_path.write_text(
            format_tables_inventory_md(report), encoding="utf-8",
        )
        print(f"Wrote tables_inventory.md to {md_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
