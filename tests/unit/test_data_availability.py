r"""Tests for v0.7.2 Data Availability extraction pipeline.

Validates the rewritten extraction functions that parse REPORT.md
structured tables (### Sources, ### Generated data) instead of regex-
matching methods_provenance.md or curated _KNOWN_DATA_SOURCES patterns.

Tests cover three bugs from the ibd_phage_targeting live test:
  1. Confabulated K-BERDL databases from filename.ext matches
  2. PMIDs listed as data accessions (they're bibliography)
  3. Incidental STRING pattern matches from curated list

Plus the new extraction and formatting functions added in v0.7.2.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
import importlib.util

_HELPERS_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "paper_writer_helpers.py"
)


@pytest.fixture(scope="module")
def helpers():
    """Load paper_writer_helpers.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "paper_writer_helpers", _HELPERS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paper_writer_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: synthetic REPORT.md content
# ---------------------------------------------------------------------------

REPORT_WITH_SOURCES = textwrap.dedent("""\
    # REPORT

    Some preamble text.

    ## Data

    ### Sources

    | Collection | Tables used | Purpose |
    |---|---|---|
    | `phagefoundry_strain_modelling` | `strain`, `host_interaction` | NB12–NB13: phage-host interaction modelling |
    | `kescience_fitnessbrowser` | `gene`, `specificphenotype`, `experiment` | NB00–NB03: fitness data for gene essentiality |
    | `kescience_paperblast` | `locus2papers` | NB05: literature cross-reference for gene annotations |
    | `kbase_ke_pangenome` | `ortholog_cluster` | queued for NB04+; pangenome context |
    | `curatedMetagenomicData_v4` | `relative_abundance`, `sample_metadata` | NB06–NB08: IBD microbiome profiles |

    ### Generated data

    | File | Rows | Description |
    |---|---|---|
    | `data/nb00_gene_essentiality.tsv` | 4521 | Gene essentiality scores from NB00 fitness analysis |
    | `data/nb05_tier_a_scored.tsv` | 312 | Tier A candidates scored by NB05 pipeline |
    | `data/nb04_tier_a_candidates.tsv` | 89 | NB04 candidate list **(retracted)** |
    | `data/nb06_ibd_profiles.parquet` | 1543 | HMP2 MetaPhlAn3 relative abundance profiles |
    | `data/nb12_phage_host_matrix.tsv` | varies | PhageFoundry phage-host interaction matrix |
    | `data/nb13_final_predictions.tsv` | 45 | Final phage targeting predictions from NB13 |
    | `data/nb99_unused.tsv` | — | Exploratory analysis, not used in manuscript |

    ## Methods

    Some methods text.
""")

REPORT_NO_DATA_SECTION = textwrap.dedent("""\
    # REPORT

    Some preamble text.

    ## Methods

    Methods here.

    ## Results

    Results here.
""")

REPORT_EMPTY_TABLES = textwrap.dedent("""\
    # REPORT

    ## Data

    ### Sources

    No structured data sources were used.

    ### Generated data

    No generated data files.
""")


# ---------------------------------------------------------------------------
# A1: _parse_markdown_table
# ---------------------------------------------------------------------------

class TestParseMarkdownTable:
    """Tests for the generic markdown table parser."""

    def test_parses_sources_table(self, helpers):
        rows = helpers._parse_markdown_table(REPORT_WITH_SOURCES, "### Sources")
        assert len(rows) == 5
        assert rows[0]["Collection"] == "`phagefoundry_strain_modelling`"
        assert rows[0]["Tables used"] == "`strain`, `host_interaction`"
        assert "NB12" in rows[0]["Purpose"]

    def test_parses_generated_data_table(self, helpers):
        rows = helpers._parse_markdown_table(
            REPORT_WITH_SOURCES, "### Generated data"
        )
        assert len(rows) == 7
        assert rows[0]["File"] == "`data/nb00_gene_essentiality.tsv`"
        assert rows[0]["Rows"] == "4521"

    def test_missing_section_returns_empty(self, helpers):
        rows = helpers._parse_markdown_table(
            REPORT_NO_DATA_SECTION, "### Sources"
        )
        assert rows == []

    def test_section_without_table_returns_empty(self, helpers):
        rows = helpers._parse_markdown_table(
            REPORT_EMPTY_TABLES, "### Sources"
        )
        assert rows == []

    def test_handles_varies_and_dash_rows(self, helpers):
        rows = helpers._parse_markdown_table(
            REPORT_WITH_SOURCES, "### Generated data"
        )
        varies_row = [r for r in rows if "varies" in r.get("Rows", "")]
        assert len(varies_row) == 1
        dash_row = [r for r in rows if r.get("Rows", "") == "—"]
        assert len(dash_row) == 1


# ---------------------------------------------------------------------------
# A1: _extract_report_sources
# ---------------------------------------------------------------------------

class TestExtractReportSources:
    """Tests for REPORT.md ### Sources table extraction."""

    def test_extracts_all_sources(self, helpers):
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        assert len(sources) == 5
        collections = [s["collection"] for s in sources]
        assert "`phagefoundry_strain_modelling`" in collections
        assert "`kescience_fitnessbrowser`" in collections

    def test_preserves_tables_and_purpose(self, helpers):
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        fb = [s for s in sources if "fitnessbrowser" in s["collection"]][0]
        assert "gene" in fb["tables"]
        assert "NB00" in fb["purpose"]

    def test_fail_closed_on_missing_section(self, helpers):
        sources = helpers._extract_report_sources(REPORT_NO_DATA_SECTION)
        assert sources == []

    def test_fail_closed_on_empty_table(self, helpers):
        sources = helpers._extract_report_sources(REPORT_EMPTY_TABLES)
        assert sources == []

    def test_does_not_match_filename_extensions(self, helpers):
        """Regression: the old regex matched extract_methods.py as
        database=extract_methods table=py. The new parser reads
        structured tables, so this class of bug is impossible."""
        tricky_report = textwrap.dedent("""\
            # REPORT

            References to extract_methods.py and requirements.txt
            and research_plan.md should NOT produce databases.

            ## Data

            ### Sources

            | Collection | Tables used | Purpose |
            |---|---|---|
            | `real_database` | `real_table` | NB01 analysis |
        """)
        sources = helpers._extract_report_sources(tricky_report)
        assert len(sources) == 1
        assert sources[0]["collection"] == "`real_database`"
        # No extract_methods, requirements, research_plan entries.
        collections = [s["collection"] for s in sources]
        assert not any("extract_methods" in c for c in collections)
        assert not any("requirements" in c for c in collections)


# ---------------------------------------------------------------------------
# A2: _ACCESSION_PATTERNS (PMID removed)
# ---------------------------------------------------------------------------

class TestAccessionPatterns:
    """Tests that PMID is no longer in _ACCESSION_PATTERNS."""

    def test_pmid_not_in_patterns(self, helpers):
        """Regression: v0.7.1 had PMID regex that caught bibliography."""
        kinds = [kind for _, kind in helpers._ACCESSION_PATTERNS]
        assert "PMID" not in kinds

    def test_bioproject_still_extracted(self, helpers):
        text = "Data deposited under BioProject PRJNA123456."
        result = helpers._extract_typed_accessions(text)
        assert ("BioProject", "PRJNA123456") in result

    def test_geo_still_extracted(self, helpers):
        text = "Raw data at GEO: GSE123456."
        result = helpers._extract_typed_accessions(text)
        assert ("GEO", "GSE123456") in result

    def test_sra_study_still_extracted(self, helpers):
        text = "Sequencing reads: SRP654321."
        result = helpers._extract_typed_accessions(text)
        assert ("SRA Study", "SRP654321") in result

    def test_sra_run_still_extracted(self, helpers):
        text = "Individual run: SRR998877."
        result = helpers._extract_typed_accessions(text)
        assert ("SRA Run", "SRR998877") in result

    def test_biosample_still_extracted(self, helpers):
        text = "BioSample SAMN12345678."
        result = helpers._extract_typed_accessions(text)
        assert ("BioSample", "SAMN12345678") in result

    def test_cmd_version_extracted(self, helpers):
        """v0.7.2 addition: curatedMetagenomicData version."""
        text = "Using cMD v4.1 for microbiome profiles."
        result = helpers._extract_typed_accessions(text)
        assert any(k == "cMD version" for k, _ in result)

    def test_pmid_text_produces_no_accessions(self, helpers):
        """Regression test: PMID citations should not appear as accessions."""
        text = (
            "PMID: 39188957, PMID: 29769716, PMID 12345678. "
            "These are bibliography entries (Price et al., Smith et al.)."
        )
        result = helpers._extract_typed_accessions(text)
        # None of these should be extracted.
        pmid_results = [r for r in result if "PMID" in r[0]]
        assert len(pmid_results) == 0

    def test_deduplication(self, helpers):
        text = "PRJNA123456 and again PRJNA123456."
        result = helpers._extract_typed_accessions(text)
        bioproject = [r for r in result if r[1] == "PRJNA123456"]
        assert len(bioproject) == 1

    def test_doi_prefix_stripped(self, helpers):
        text = "DOI: 10.1234/test.5678"
        result = helpers._extract_typed_accessions(text)
        doi_results = [r for r in result if r[0] == "DOI"]
        assert len(doi_results) == 1
        assert doi_results[0][1] == "10.1234/test.5678"


# ---------------------------------------------------------------------------
# A4: _extract_generated_data
# ---------------------------------------------------------------------------

class TestExtractGeneratedData:
    """Tests for REPORT.md ### Generated data table extraction."""

    def test_extracts_all_rows(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        assert len(generated) == 7

    def test_preserves_file_paths(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        files = [g["file"] for g in generated]
        assert "`data/nb00_gene_essentiality.tsv`" in files
        assert "`data/nb13_final_predictions.tsv`" in files

    def test_handles_varies_row_count(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        varies = [g for g in generated if g["rows"] == "varies"]
        assert len(varies) == 1
        assert "phage_host_matrix" in varies[0]["file"]

    def test_handles_dash_row_count(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        dashes = [g for g in generated if g["rows"] == "—"]
        assert len(dashes) == 1

    def test_preserves_retracted_annotation(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        retracted = [g for g in generated if "retracted" in g["description"].lower()]
        assert len(retracted) == 1
        assert "nb04" in retracted[0]["file"]

    def test_fail_closed_on_missing_section(self, helpers):
        generated = helpers._extract_generated_data(REPORT_NO_DATA_SECTION)
        assert generated == []

    def test_integer_row_counts_preserved(self, helpers):
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        nb00 = [g for g in generated if "nb00" in g["file"]][0]
        assert nb00["rows"] == "4521"


# ---------------------------------------------------------------------------
# A3: _extract_external_sources
# ---------------------------------------------------------------------------

class TestExtractExternalSources:
    """Tests for deriving external data sources from REPORT.md tables."""

    def test_curatedmetagenomicdata_detected(self, helpers):
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        external = helpers._extract_external_sources(sources, generated)
        names = [e["name"].lower() for e in external]
        assert any("curatedmetagenomicdata" in n for n in names)

    def test_hmp2_detected_from_generated_data(self, helpers):
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        external = helpers._extract_external_sources(sources, generated)
        names = [e["name"] for e in external]
        assert any("HMP2" in n for n in names)

    def test_phagefoundry_detected(self, helpers):
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        external = helpers._extract_external_sources(sources, generated)
        names = [e["name"] for e in external]
        assert any("PhageFoundry" in n for n in names)

    def test_string_absent_when_not_in_tables(self, helpers):
        """Regression: v0.7.1 matched STRING incidentally from curated
        patterns. Now only detected if it's in the REPORT tables."""
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        external = helpers._extract_external_sources(sources, generated)
        names = [e["name"] for e in external]
        assert not any("STRING" == n for n in names)

    def test_deduplication(self, helpers):
        """Same external source from both tables should appear once."""
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        external = helpers._extract_external_sources(sources, generated)
        names_lower = [e["name"].lower() for e in external]
        # No duplicates.
        assert len(names_lower) == len(set(names_lower))

    def test_empty_inputs_returns_empty(self, helpers):
        external = helpers._extract_external_sources([], [])
        assert external == []


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

class TestFormatKberdlBlock:
    """Tests for _format_kberdl_block (v0.7.2 version)."""

    def test_empty_sources_emits_tbd(self, helpers):
        block = helpers._format_kberdl_block([])
        assert "[TBD" in block or "TBD" in block

    def test_sources_formatted_with_collection_names(self, helpers):
        sources = [
            {"collection": "my_db", "tables": "t1, t2", "purpose": "NB01"},
        ]
        block = helpers._format_kberdl_block(sources)
        assert "my_db" in block
        assert "t1, t2" in block
        assert "K-BERDL" in block

    def test_missing_tables_shows_see_purpose(self, helpers):
        sources = [
            {"collection": "my_db", "tables": "", "purpose": "NB01 analysis"},
        ]
        block = helpers._format_kberdl_block(sources)
        assert "see Purpose" in block


class TestFormatExternalSourcesBlock:
    """Tests for _format_external_sources_block."""

    def test_empty_emits_tbd(self, helpers):
        block = helpers._format_external_sources_block([])
        assert "TBD" in block

    def test_sources_listed(self, helpers):
        external = [
            {"name": "HMP2", "source": "Generated data"},
            {"name": "GTDB", "source": "Sources table"},
        ]
        block = helpers._format_external_sources_block(external)
        assert "HMP2" in block
        assert "GTDB" in block
        assert "publicly available" in block


class TestFormatDerivedDataBlock:
    """Tests for _format_derived_data_block."""

    def test_empty_emits_tbd(self, helpers):
        block = helpers._format_derived_data_block([])
        assert "TBD" in block

    def test_files_listed_with_rows(self, helpers):
        generated = [
            {"file": "data/nb01.tsv", "rows": "100", "description": "Scores"},
        ]
        block = helpers._format_derived_data_block(generated)
        assert "nb01.tsv" in block
        assert "100 rows" in block
        assert "Scores" in block

    def test_dash_rows_omitted(self, helpers):
        generated = [
            {"file": "data/nb99.tsv", "rows": "—", "description": "Unused"},
        ]
        block = helpers._format_derived_data_block(generated)
        assert "rows" not in block.lower() or "—" not in block


class TestFormatAccessionsBlock:
    """Tests for _format_accessions_block."""

    def test_empty_emits_tbd(self, helpers):
        block = helpers._format_accessions_block([])
        assert "TBD" in block

    def test_accessions_listed(self, helpers):
        accessions = [("BioProject", "PRJNA123456"), ("GEO", "GSE789")]
        block = helpers._format_accessions_block(accessions)
        assert "PRJNA123456" in block
        assert "GSE789" in block
        assert "BioProject" in block


# ---------------------------------------------------------------------------
# Integration: cmd_extract_data_availability output shape
# ---------------------------------------------------------------------------

class TestCmdExtractDataAvailabilityOutputShape:
    """Validate the JSON output contract of cmd_extract_data_availability."""

    def test_output_has_v072_keys(self, helpers):
        """The v0.7.2 output must have the new block names."""
        # We can't easily call cmd_ without argparse, but we can verify
        # the expected keys are what the formatters produce.
        expected_keys = {
            "kberdl_block",
            "external_block",
            "derived_block",
            "accessions_block",
            "restricted_block",
            "reproducibility_block",
            "diagnostics",
        }
        # Verify by reading the function source and checking for the keys.
        import inspect
        src = inspect.getsource(helpers.cmd_extract_data_availability)
        for key in expected_keys:
            assert f'"{key}"' in src, f"Missing key {key} in cmd output"

    def test_old_keys_removed(self, helpers):
        """v0.7.1 keys should not appear in the new function."""
        import inspect
        src = inspect.getsource(helpers.cmd_extract_data_availability)
        assert '"kberdl_databases_block"' not in src
        assert '"public_accessions_block"' not in src


# ---------------------------------------------------------------------------
# Regression: the three bugs from ibd_phage_targeting
# ---------------------------------------------------------------------------

class TestIbdPhageTargetingRegressions:
    """End-to-end regression tests for the three bugs that triggered v0.7.2."""

    def test_bug1_no_filename_extension_databases(self, helpers):
        """Bug 1: extract_methods.py interpreted as database.table.

        The old _extract_kberdl_databases scanned the entire file when
        the SQL section was missing and matched filename.ext as
        FROM <database>.<table>. The new _extract_report_sources reads
        a structured table, making this impossible.
        """
        # A report that mentions extract_methods.py in prose but has
        # a real Sources table.
        report = textwrap.dedent("""\
            # REPORT

            See extract_methods.py and requirements.txt for details.

            ## Data

            ### Sources

            | Collection | Tables used | Purpose |
            |---|---|---|
            | `real_collection` | `real_table` | NB01 |
        """)
        sources = helpers._extract_report_sources(report)
        collections = [s["collection"] for s in sources]
        assert "`real_collection`" in collections
        assert not any("extract_methods" in c for c in collections)
        assert not any("requirements" in c for c in collections)

    def test_bug2_no_pmids_as_accessions(self, helpers):
        """Bug 2: 45 PMIDs listed as 'accessions'.

        The old _ACCESSION_PATTERNS included a PMID regex that matched
        every literature citation. PMIDs are now removed from patterns.
        """
        text = "\n".join(
            f"PMID: {39188957 + i}" for i in range(45)
        )
        result = helpers._extract_typed_accessions(text)
        assert len(result) == 0, (
            f"PMIDs should not appear as accessions; got {len(result)}"
        )

    def test_bug3_no_incidental_string_match(self, helpers):
        """Bug 3: STRING listed as data source without actual usage.

        The old _KNOWN_DATA_SOURCES had a STRING pattern that matched
        on any mention of the word. Now only detected if STRING appears
        in the REPORT.md tables.
        """
        report = textwrap.dedent("""\
            # REPORT

            We considered using STRING for network analysis but decided
            against it. The word STRING appears here incidentally.

            ## Data

            ### Sources

            | Collection | Tables used | Purpose |
            |---|---|---|
            | `kescience_fitnessbrowser` | `gene` | NB01 |

            ### Generated data

            | File | Rows | Description |
            |---|---|---|
            | `data/nb01.tsv` | 100 | Gene fitness scores |
        """)
        sources = helpers._extract_report_sources(report)
        generated = helpers._extract_generated_data(report)
        external = helpers._extract_external_sources(sources, generated)
        names = [e["name"] for e in external]
        assert "STRING" not in names, (
            "STRING should not appear unless it's in the REPORT tables"
        )


# ---------------------------------------------------------------------------
# Tier B: Cross-walk filter
# ---------------------------------------------------------------------------

METHODS_WITH_NOTEBOOKS = textwrap.dedent("""\
    ## Methods

    ### Data acquisition

    Gene essentiality data was obtained from the Fitness Browser
    (notebooks/NB00_gene_essentiality.ipynb through NB03_fitness_validation.ipynb).

    ### Candidate scoring

    Tier A candidates were scored using the pipeline in NB05
    (notebooks/NB05_tier_a_scoring.ipynb).

    ### IBD microbiome profiling

    We analyzed IBD cohort microbiome data using curatedMetagenomicData
    (NB06–NB08, see notebooks/NB06_ibd_profiles.ipynb).

    ### Phage-host prediction

    Phage-host interaction predictions were generated using PhageFoundry
    strain modelling data (NB12+, notebooks/NB13_phagefoundry_predictions.ipynb).
""")

METHODS_NO_NOTEBOOKS = textwrap.dedent("""\
    ## Methods

    Standard bioinformatics analysis was performed. No notebook
    references are present in this text.
""")


class TestExtractCitedNotebooks:
    """Tests for _extract_cited_notebooks (Tier B1)."""

    def test_extracts_inline_nb_references(self, helpers):
        nbs = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)
        assert "NB00" in nbs
        assert "NB05" in nbs
        assert "NB13" in nbs

    def test_extracts_path_references(self, helpers):
        nbs = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)
        # notebooks/NB06_ibd_profiles.ipynb
        assert "NB06" in nbs
        # notebooks/NB13_phagefoundry_predictions.ipynb
        assert "NB13" in nbs

    def test_extracts_range_references(self, helpers):
        """NB06–NB08 should expand to include NB06, NB07, NB08 base IDs."""
        nbs = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)
        assert "NB06" in nbs
        # NB08 appears in "NB06–NB08" — the range endpoint after the dash.
        assert "NB08" in nbs

    def test_extracts_nb_plus_references(self, helpers):
        """NB12+ should extract NB12."""
        nbs = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)
        assert "NB12" in nbs

    def test_through_nb03(self, helpers):
        """'NB00_gene_essentiality.ipynb through NB03_fitness_validation'
        should catch both NB00 and NB03."""
        nbs = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)
        assert "NB00" in nbs
        assert "NB03" in nbs

    def test_no_notebooks_returns_empty(self, helpers):
        nbs = helpers._extract_cited_notebooks(METHODS_NO_NOTEBOOKS)
        assert nbs == set()

    def test_suffix_stripped(self, helpers):
        """NB04b should normalize to NB04."""
        text = "Analysis in NB04b and NB04c."
        nbs = helpers._extract_cited_notebooks(text)
        assert "NB04" in nbs
        assert "NB04b" not in nbs  # Suffixes stripped.


class TestCrosswalkSources:
    """Tests for _crosswalk_sources (Tier B2)."""

    def test_queued_uncited_excluded(self, helpers):
        """kbase_ke_pangenome (queued for NB04+, NB04 not cited) excluded."""
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        cited = {"NB00", "NB01", "NB02", "NB03", "NB05", "NB06",
                 "NB07", "NB08", "NB12", "NB13"}
        filtered = helpers._crosswalk_sources(sources, cited)
        collections = [s["collection"] for s in filtered]
        assert not any("pangenome" in c for c in collections)

    def test_queued_cited_included(self, helpers):
        """phagefoundry_strain_modelling (queued for NB12+, NB12 cited) included."""
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        # NB12 is cited.
        cited = {"NB12", "NB13"}
        filtered = helpers._crosswalk_sources(sources, cited)
        collections = [s["collection"] for s in filtered]
        assert any("phagefoundry" in c for c in collections)

    def test_nonqueued_sources_included(self, helpers):
        """Non-queued sources are always included."""
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        cited = {"NB00"}  # Only NB00.
        filtered = helpers._crosswalk_sources(sources, cited)
        # fitnessbrowser references NB00-NB03, NB00 is cited → included.
        collections = [s["collection"] for s in filtered]
        assert any("fitnessbrowser" in c for c in collections)

    def test_empty_cited_set_keeps_nonqueued(self, helpers):
        """With no cited notebooks, non-queued sources still included."""
        sources = [
            {"collection": "active_db", "tables": "t1", "purpose": "general use"},
            {"collection": "queued_db", "tables": "t2", "purpose": "queued for NB99"},
        ]
        filtered = helpers._crosswalk_sources(sources, set())
        collections = [s["collection"] for s in filtered]
        assert "active_db" in collections
        assert "queued_db" not in collections


class TestCrosswalkGeneratedData:
    """Tests for _crosswalk_generated_data (Tier B3)."""

    def test_retracted_excluded(self, helpers):
        """Retracted entries excluded regardless of notebook match."""
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        cited = {"NB04"}  # NB04 is cited, but nb04 file is retracted.
        filtered = helpers._crosswalk_generated_data(generated, cited)
        files = [g["file"] for g in filtered]
        assert not any("nb04" in f for f in files)

    def test_cited_notebook_included(self, helpers):
        """NB05 cited → nb05_tier_a_scored.tsv included."""
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        cited = {"NB05"}
        filtered = helpers._crosswalk_generated_data(generated, cited)
        files = [g["file"] for g in filtered]
        assert any("nb05" in f for f in files)

    def test_uncited_notebook_excluded(self, helpers):
        """NB99 not cited → nb99_unused.tsv excluded."""
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        cited = {"NB00", "NB05"}
        filtered = helpers._crosswalk_generated_data(generated, cited)
        files = [g["file"] for g in filtered]
        assert not any("nb99" in f for f in files)

    def test_hmp2_description_matches_nb06(self, helpers):
        """nb06_ibd_profiles.parquet (HMP2 MetaPhlAn3) included when NB06 cited."""
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        cited = {"NB06"}
        filtered = helpers._crosswalk_generated_data(generated, cited)
        files = [g["file"] for g in filtered]
        assert any("nb06" in f for f in files)

    def test_empty_cited_set_returns_nothing(self, helpers):
        """No cited notebooks → no generated data passes filter."""
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        filtered = helpers._crosswalk_generated_data(generated, set())
        assert filtered == []

    def test_superseded_excluded(self, helpers):
        """Superseded entries excluded like retracted ones."""
        generated = [
            {"file": "data/nb01_old.tsv", "rows": "100",
             "description": "Old version **(superseded)**"},
            {"file": "data/nb01_new.tsv", "rows": "150",
             "description": "NB01 current version"},
        ]
        cited = {"NB01"}
        filtered = helpers._crosswalk_generated_data(generated, cited)
        files = [g["file"] for g in filtered]
        assert "data/nb01_new.tsv" in files
        assert "data/nb01_old.tsv" not in files


class TestCrosswalkIntegration:
    """End-to-end cross-walk tests using the full synthetic REPORT."""

    def test_ibd_phage_crosswalk_scenario(self, helpers):
        """Simulate the ibd_phage_targeting scenario from the punch list.

        Cited notebooks: NB00-NB03, NB05, NB06-NB08, NB12, NB13.
        Expected: kbase_ke_pangenome (queued for NB04+) excluded.
        Expected: phagefoundry_strain_modelling included (NB12+ purpose).
        Expected: nb04 retracted file excluded.
        Expected: nb99 unused file excluded.
        """
        sources = helpers._extract_report_sources(REPORT_WITH_SOURCES)
        generated = helpers._extract_generated_data(REPORT_WITH_SOURCES)
        cited = helpers._extract_cited_notebooks(METHODS_WITH_NOTEBOOKS)

        filtered_sources = helpers._crosswalk_sources(sources, cited)
        filtered_generated = helpers._crosswalk_generated_data(
            generated, cited
        )

        # Verify collections.
        src_collections = [s["collection"] for s in filtered_sources]
        assert any("phagefoundry" in c for c in src_collections), \
            "phagefoundry should be included (NB12+ matches NB12)"
        assert not any("pangenome" in c for c in src_collections), \
            "pangenome should be excluded (queued for NB04+, NB04 not cited)"

        # Verify generated data.
        gen_files = [g["file"] for g in filtered_generated]
        assert any("nb05" in f for f in gen_files), \
            "nb05 should be included (NB05 cited)"
        assert not any("nb04" in f for f in gen_files), \
            "nb04 should be excluded (retracted)"
        assert not any("nb99" in f for f in gen_files), \
            "nb99 should be excluded (not cited)"
        assert any("nb00" in f for f in gen_files), \
            "nb00 should be included (NB00 cited)"
