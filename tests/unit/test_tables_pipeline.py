"""Unit tests for v0.6 Tier 9 tables pipeline in paper_writer_helpers.py.

Tests cover: caption sufficiency gate, manifest parsing, inventory
parsing, table map building, table embedding, and the apply-table-captions
command.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools.paper_writer_helpers import (
    _build_table_map,
    _embed_tables_in_text,
    _parse_tables_inventory,
    _parse_tables_manifest,
    _table_caption_passes_gate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _table_caption_passes_gate
# ---------------------------------------------------------------------------

class TestTableCaptionGate:
    def test_good_caption_passes(self):
        assert _table_caption_passes_gate(
            "Pathway gap summary across 48 organisms",
            ["Pathway", "Category", "Organisms with gaps", "Example organisms"],
        )

    def test_empty_caption_fails(self):
        assert not _table_caption_passes_gate("", ["Pathway"])

    def test_none_caption_fails(self):
        assert not _table_caption_passes_gate(None, ["Pathway"])

    def test_short_caption_fails(self):
        assert not _table_caption_passes_gate("Summary table", ["A", "B"])

    def test_no_column_overlap_fails(self):
        """Caption with >5 words but zero overlap with column names."""
        assert not _table_caption_passes_gate(
            "This describes something entirely different from the data shown",
            ["Pathway", "Category", "Organism"],
        )

    def test_overlap_with_markdown_stripped(self):
        """Column names with markdown formatting should still match."""
        assert _table_caption_passes_gate(
            "Finding 4: Cross-organism fitness concordance identifies groups",
            ["Ortholog group", "Condition", "Organisms", "Concordance"],
        )

    def test_short_generic_caption_fails_without_overlap(self):
        """Short caption (6-10 words) with no column overlap fails."""
        assert not _table_caption_passes_gate(
            "Summary results shown below here",  # 5 words — below threshold
            ["Organism", "Locus", "Condition"],
        )
        # 7 words, no overlap with column names → fail
        assert not _table_caption_passes_gate(
            "Overview showing key metrics across samples",
            ["Pathway", "Category", "Count"],
        )

    def test_all_stopword_columns_passes(self):
        """If ALL column names are stopwords, gate passes (edge case)."""
        assert _table_caption_passes_gate(
            "This describes all the important results for the study",
            ["a", "the", "of"],
        )

    def test_pipe_in_column_names(self):
        """|fit| column name should have 'fit' extracted for overlap."""
        assert _table_caption_passes_gate(
            "Carrier versus non-carrier fitness divergence by organism and locus",
            ["Organism", "Locus", "|fit|", "Condition"],
        )

    def test_real_fdm_heading_passes(self):
        """Real heading from functional_dark_matter REPORT.md."""
        assert _table_caption_passes_gate(
            "Finding 6: Within-species biogeographic analysis reveals 10 significant dark gene clusters",
            ["Organism", "Locus", "Condition", "|fit|", "Carrier env",
             "Odds ratio", "FDR", "Breadth", "Module prediction"],
        )


# ---------------------------------------------------------------------------
# _parse_tables_manifest
# ---------------------------------------------------------------------------

class TestParseTablesManifest:
    def test_valid_manifest(self, tmp_path):
        _write(tmp_path / "tables_manifest.tsv", textwrap.dedent("""\
            paper_order_n\ttable_id\tinventory_lookup_name
            1\ttable01_gaps\treport_tbl_01
            2\ttable02_concordance\treport_tbl_02
        """))
        rows = _parse_tables_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 2
        assert rows[0]["paper_order_n"] == 1
        assert rows[1]["table_id"] == "table02_concordance"

    def test_missing_file(self, tmp_path):
        rows = _parse_tables_manifest(tmp_path / "tables_manifest.tsv")
        assert rows == []

    def test_blank_lines_skipped(self, tmp_path):
        _write(tmp_path / "tables_manifest.tsv",
               "paper_order_n\ttable_id\tinventory_lookup_name\n"
               "1\ta\tb\n\n2\tc\td\n")
        rows = _parse_tables_manifest(tmp_path / "tables_manifest.tsv")
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# _parse_tables_inventory
# ---------------------------------------------------------------------------

class TestParseTablesInventory:
    def test_parses_entry(self, tmp_path):
        _write(tmp_path / "tables_inventory.md", textwrap.dedent("""\
            <!-- inventory_schema_version: 1 -->
            # Tables Inventory

            ## Tables

            ### report_tbl_01 — Pathway gap summary

            _Columns: 3 (Pathway | Category | Count)_
            _Rows: 6_

            **Caption candidates:**

            - **section heading**: Pathway gap summary
            - **preceding sentence**: The following pathways have gaps.

            **Content (first 3 rows):**

            | Pathway | Category | Count |
            |---------|----------|------:|
            | Fucose | carbon | 32 |
            | Rhamnose | carbon | 31 |
            | Sorbitol | carbon | 30 |

            _(6 data rows total)_

            _Source: REPORT.md lines 33–39_
        """))
        inv = _parse_tables_inventory(tmp_path / "tables_inventory.md")
        assert "report_tbl_01" in inv
        entry = inv["report_tbl_01"]
        assert entry["caption"] == "Pathway gap summary"  # heading wins
        assert "Fucose" in entry["content"]
        assert entry["column_names"] == ["Pathway", "Category", "Count"]

    def test_missing_inventory(self, tmp_path):
        inv = _parse_tables_inventory(tmp_path / "tables_inventory.md")
        assert inv == {}

    def test_preceding_sentence_fallback(self, tmp_path):
        _write(tmp_path / "tables_inventory.md", textwrap.dedent("""\
            ### report_tbl_01 — (no heading)

            _Columns: 2 (A | B)_

            **Caption candidates:**

            - **preceding sentence**: The data are summarized below.

            **Content (first 3 rows):**

            | A | B |
            |---|---|
            | 1 | 2 |
        """))
        inv = _parse_tables_inventory(tmp_path / "tables_inventory.md")
        assert inv["report_tbl_01"]["caption"] == "The data are summarized below."


# ---------------------------------------------------------------------------
# _build_table_map
# ---------------------------------------------------------------------------

class TestBuildTableMap:
    def test_joins_manifest_and_inventory(self, tmp_path):
        _write(tmp_path / "tables_manifest.tsv",
               "paper_order_n\ttable_id\tinventory_lookup_name\n"
               "1\ttable01_gaps\treport_tbl_01\n")
        _write(tmp_path / "tables_inventory.md", textwrap.dedent("""\
            ### report_tbl_01 — Pathway gaps

            _Columns: 2 (Pathway | Count)_

            **Caption candidates:**

            - **section heading**: Pathway gaps

            **Content (first 3 rows):**

            | Pathway | Count |
            |---------|------:|
            | Fucose | 32 |
        """))
        tmap = _build_table_map(tmp_path)
        assert 1 in tmap
        assert tmap[1]["caption"] == "Pathway gaps"
        assert "Fucose" in tmap[1]["content"]


# ---------------------------------------------------------------------------
# _embed_tables_in_text
# ---------------------------------------------------------------------------

class TestEmbedTablesInText:
    def test_single_callout(self):
        table_map = {
            1: {
                "table_id": "table01",
                "caption": "Pathway gaps summary",
                "content": "| A | B |\n|---|---|\n| 1 | 2 |",
            }
        }
        text = "The results are shown in (Table 1) below."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.** Pathway gaps summary" in result
        assert "| A | B |" in result
        assert 1 in already

    def test_idempotent(self):
        table_map = {
            1: {
                "table_id": "table01",
                "caption": "Caption",
                "content": "| A |\n|---|\n| 1 |",
            }
        }
        text = "See (Table 1)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        # Second pass should not inject again
        result2 = _embed_tables_in_text(result, table_map, already)
        assert result2.count("**Table 1.**") == 1

    def test_multiple_callouts(self):
        table_map = {
            1: {"table_id": "t1", "caption": "Cap 1",
                "content": "| A |\n|---|\n| 1 |"},
            2: {"table_id": "t2", "caption": "Cap 2",
                "content": "| B |\n|---|\n| 2 |"},
        }
        text = "See (Table 1).\n\nAlso see (Table 2)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.**" in result
        assert "**Table 2.**" in result
        assert already == {1, 2}

    def test_no_content_skips(self):
        table_map = {
            1: {"table_id": "t1", "caption": "Cap", "content": ""},
        }
        text = "See (Table 1)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.**" not in result

    def test_missing_from_map_skips(self):
        table_map = {}
        text = "See (Table 99)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert result == text

    def test_no_caption_still_embeds(self):
        table_map = {
            1: {"table_id": "t1", "caption": "",
                "content": "| A |\n|---|\n| 1 |"},
        }
        text = "See (Table 1)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.**" in result
        assert "| A |" in result

    def test_compound_callout_same_line(self):
        """(Table 1 and Table 3) on the same line should embed both."""
        table_map = {
            1: {"table_id": "t1", "caption": "Cap 1",
                "content": "| A |\n|---|\n| 1 |"},
            3: {"table_id": "t3", "caption": "Cap 3",
                "content": "| C |\n|---|\n| 3 |"},
        }
        text = "Results in (Table 1 and Table 3)."
        already = set()
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.**" in result
        assert "**Table 3.**" in result
        assert already == {1, 3}

    def test_second_occurrence_in_different_section(self):
        """Second callout to same Table N should not re-embed."""
        table_map = {
            1: {"table_id": "t1", "caption": "Cap",
                "content": "| A |\n|---|\n| 1 |"},
        }
        already = {1}  # already embedded in results
        text = "As shown in (Table 1), the data confirm..."
        result = _embed_tables_in_text(text, table_map, already)
        assert "**Table 1.**" not in result
