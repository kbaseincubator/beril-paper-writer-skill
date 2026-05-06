r"""Tests for check_data_availability.py post-checker (v0.7.2 Tier D).

Validates the advisory post-checker catches all three failure modes from
the ibd_phage_targeting live test plus [TBD] marker counting.
"""

import sys
import textwrap
from pathlib import Path

import pytest
import importlib.util

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "check_data_availability.py"
)


@pytest.fixture(scope="module")
def checker():
    """Load check_data_availability.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "check_data_availability", _CHECKER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_data_availability"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic Data Availability sections
# ---------------------------------------------------------------------------

CLEAN_DA = textwrap.dedent("""\
    # Data Availability

    ## Code

    Available at https://github.com/example/repo (commit abc123).

    ## Data sources — BERDL / K-BERDL

    - **`phagefoundry_strain_modelling`** — tables: strain, host (NB12–NB13)
    - **`kescience_fitnessbrowser`** — tables: gene, experiment (NB00–NB03)

    K-BERDL is the BERDL data-lakehouse query layer.

    ## Data sources — external / public

    - **curatedMetagenomicData** (from Sources table)
    - **HMP2** (from Generated data)

    ## Derived data artifacts

    - `data/nb00_gene_essentiality.tsv` (4521 rows) — Gene essentiality

    ## Data accessions

    - BioProject: `PRJNA123456`

    ## Restricted access

    All data sources are publicly available.

    ## Methods reproducibility pointer

    See Methods.
""")

BUGGY_DA_V071 = textwrap.dedent("""\
    # Data Availability

    ## Code

    Available at [CODE REPO: TBD — fill before submission].

    ## Data sources — BERDL / K-BERDL

    - **`extract_methods`** — tables: `py`
    - **`requirements`** — tables: `txt`
    - **`phagefoundry_strain_modelling`** — tables: strain, host

    K-BERDL is the BERDL data-lakehouse query layer.

    ## Data sources — external / public

    - **STRING** (from pattern match)

    ## Derived data artifacts

    [DERIVED DATA: TBD — no Generated data table found.]

    ## Data accessions

    - PMID: `39188957`
    - PMID: `29769716`
    - BioProject: `PRJNA123456`

    ## Restricted access

    [RESTRICTED ACCESS: TBD — confirm before submission.]

    ## Methods reproducibility pointer

    See Methods.
""")

REPORT_TEXT = textwrap.dedent("""\
    ## Data

    ### Sources

    | Collection | Tables used | Purpose |
    |---|---|---|
    | `phagefoundry_strain_modelling` | `strain`, `host` | NB12–NB13 |
    | `kescience_fitnessbrowser` | `gene`, `experiment` | NB00–NB03 |
""")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFileExtensionFalsePositives:
    """Check 1: collection names that look like filenames."""

    def test_clean_da_no_warnings(self, checker):
        warnings = checker._check_file_extension_false_positives(CLEAN_DA)
        assert len(warnings) == 0

    def test_buggy_da_catches_extract_methods_py(self, checker):
        """The table name `py` in collection `extract_methods` should flag."""
        warnings = checker._check_file_extension_false_positives(BUGGY_DA_V071)
        assert any("extract_methods" in w and "py" in w for w in warnings)

    def test_buggy_da_catches_requirements_txt(self, checker):
        """The table name `txt` in collection `requirements` should flag."""
        warnings = checker._check_file_extension_false_positives(BUGGY_DA_V071)
        assert any("requirements" in w and "txt" in w for w in warnings)


class TestPmidsInAccessions:
    """Check 2: PMIDs misclassified as data accessions."""

    def test_clean_da_no_warnings(self, checker):
        warnings = checker._check_pmids_in_accessions(CLEAN_DA)
        assert len(warnings) == 0

    def test_buggy_da_catches_pmids(self, checker):
        warnings = checker._check_pmids_in_accessions(BUGGY_DA_V071)
        assert len(warnings) == 2  # Two PMIDs.
        assert all("PMID" in w for w in warnings)


class TestCollectionCrossref:
    """Check 3: K-BERDL collections cross-referenced against REPORT.md."""

    def test_clean_da_all_present(self, checker):
        warnings = checker._check_collection_crossref(CLEAN_DA, REPORT_TEXT)
        assert len(warnings) == 0

    def test_buggy_da_catches_confabulated(self, checker):
        warnings = checker._check_collection_crossref(
            BUGGY_DA_V071, REPORT_TEXT
        )
        # extract_methods and requirements are not in REPORT.
        assert any("extract_methods" in w for w in warnings)
        assert any("requirements" in w for w in warnings)

    def test_no_report_skips_check(self, checker):
        warnings = checker._check_collection_crossref(BUGGY_DA_V071, "")
        assert len(warnings) == 0


class TestTbdMarkers:
    """Check 5: [TBD] marker counting."""

    def test_clean_da_no_tbd(self, checker):
        warnings = checker._check_tbd_markers(CLEAN_DA)
        assert len(warnings) == 0

    def test_buggy_da_counts_tbd(self, checker):
        warnings = checker._check_tbd_markers(BUGGY_DA_V071)
        # Should find 3 TBD markers: CODE REPO, DERIVED DATA, RESTRICTED.
        summary = [w for w in warnings if w.startswith("NOTE:")]
        assert len(summary) == 1
        assert "3" in summary[0]


class TestMainExitCode:
    """Integration: main() always returns 0."""

    def test_clean_da_exits_0(self, checker, tmp_path):
        da_path = tmp_path / "07_data_availability.md"
        da_path.write_text(CLEAN_DA)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_buggy_da_exits_0(self, checker, tmp_path):
        """Advisory — even with warnings, exit code is 0."""
        da_path = tmp_path / "07_data_availability.md"
        da_path.write_text(BUGGY_DA_V071)
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_missing_da_exits_0(self, checker, tmp_path):
        result = checker.main([str(tmp_path)])
        assert result == 0

    def test_with_report_crossref(self, checker, tmp_path):
        da_path = tmp_path / "07_data_availability.md"
        da_path.write_text(BUGGY_DA_V071)
        report_path = tmp_path / "REPORT.md"
        report_path.write_text(REPORT_TEXT)
        result = checker.main([
            str(tmp_path), "--report", str(report_path)
        ])
        assert result == 0


class TestRegressionAllThreeBugs:
    """The checker would have caught all 3 bugs from the live test."""

    def test_all_three_bugs_detected(self, checker):
        all_warnings = []
        all_warnings.extend(
            checker._check_file_extension_false_positives(BUGGY_DA_V071)
        )
        all_warnings.extend(
            checker._check_pmids_in_accessions(BUGGY_DA_V071)
        )
        all_warnings.extend(
            checker._check_collection_crossref(BUGGY_DA_V071, REPORT_TEXT)
        )

        warn_messages = [w for w in all_warnings if w.startswith("WARN")]
        # Bug 1: at least 2 file-extension false positives.
        file_ext = [w for w in warn_messages if "false positive" in w]
        assert len(file_ext) >= 2, f"Expected >=2 file-ext warnings, got {file_ext}"

        # Bug 2: 2 PMIDs in accessions.
        pmid = [w for w in warn_messages if "PMID" in w]
        assert len(pmid) == 2, f"Expected 2 PMID warnings, got {pmid}"

        # Bug 3 (confabulated collections): extract_methods, requirements.
        confab = [w for w in warn_messages if "not found in REPORT" in w]
        assert len(confab) >= 2, f"Expected >=2 confabulation warnings, got {confab}"
