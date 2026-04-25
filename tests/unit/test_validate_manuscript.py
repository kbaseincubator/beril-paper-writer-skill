"""Tests for skill/tools/validate_manuscript.py — M1-M10 validators.

Coverage:
  - Markdown section parsing (split_into_sections, find_section)
  - Citation/reference number extraction
  - Each validator individually with synthetic markdown:
      M1: required IMRAD sections present (paper + report mode)
      M2: structured abstract subsections
      M3: AI-disclosure presence (tool name + action verb)
      M4: data-availability content (length + URL/accession)
      M5: software+version (soft-warning logic)
      M6: multiple-testing correction (≥5 p-values triggers requirement)
      M7: effect-size + CI accompanying p-values
      M8: counts before percentages
      M9: limitations section length
      M10: citation cross-reference (prose ↔ references.md ↔ bibliography.bib)
  - Top-level run_all_validators against a synthetic happy-path draft
  - CLI invocation via subprocess (one end-to-end test)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Direct module import for unit tests of individual validator functions.
from beril_paper_writer.skill.tools import validate_manuscript as vm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _happy_paper_sections() -> dict[str, str]:
    """A complete, M1-M10-passing paper section dict for happy-path tests."""
    return {
        "title": "# Title\n\nA Computational Reanalysis of X",
        "abstract": (
            "## Abstract\n\n"
            "**Background:** X is studied.\n\n"
            "**Methods:** We did Y.\n\n"
            "**Results:** We found Z.\n\n"
            "**Conclusions:** Z matters.\n"
        ),
        "introduction": (
            "## Introduction\n\n"
            "Background context. Prior work [1].\n"
            "Specific objective."
        ),
        "methods": (
            "## Methods\n\n"
            "We performed Welch's t-test, Wilcoxon test, Fisher's exact test, "
            "and a Mann-Whitney U test for between-group comparisons. "
            "Statistical analyses were carried out using scipy 1.11.0, "
            "statsmodels 0.14.0, and pandas 2.0.3 in Python 3.11. All "
            "p-values were corrected for multiple comparisons via "
            "Benjamini-Hochberg FDR at q<0.05. Effect sizes were reported "
            "as odds ratios with 95% confidence intervals computed by "
            "exact methods. The data were obtained from the public "
            "PRJNA000001 archive on 2026-03-15 and processed with our "
            "in-house pipeline whose dependencies are pinned in "
            "requirements.txt for reproducibility. Quality control "
            "thresholds (minimum read depth of 10, base quality at least "
            "Q20) followed published protocols. Methods were drawn from "
            "Smith et al. [1] and adapted with permission to the present "
            "dataset; the original protocol used Welch's correction whereas "
            "we additionally applied bootstrap confidence intervals to all "
            "summary statistics. Cross-validation was performed using "
            "5-fold stratified splits with the random seed fixed at 42 for "
            "reproducibility. All analyses were executed inside a Docker "
            "container whose definition is provided in the supplementary "
            "materials and whose specific image hash is recorded in the "
            "project's reproducibility log alongside the precise commit "
            "of the analysis repository.\n\n"
            "### AI-Assisted Analysis\n\n"
            "Manuscript drafting was performed with the BERIL paper-writer "
            "skill (claude-sonnet-4) per ICMJE V.A. Authors reviewed and "
            "edited all output and accept full responsibility."
        ),
        "results": (
            "## Results\n\n"
            "Of 156 isolates, 42 (26.9%) showed phenotype A "
            "(p < 0.001, OR=2.4, 95% CI [1.6, 3.5]).\n\n"
            "Of 200 controls, 50 (25.0%) showed phenotype B "
            "(p = 0.012, mean difference = 0.3, 95% CI [0.1, 0.5]).\n"
        ),
        "discussion": (
            "## Discussion\n\n"
            "Findings reframe X. Discussion text [2].\n"
        ),
        "limitations": (
            "## Limitations\n\n"
            "Sample size was modest. Generalizability to other organisms "
            "is uncertain because all isolates were from a single site. "
            "Computational predictions were not experimentally validated.\n"
        ),
        "data availability": (
            "## Data Availability\n\n"
            "All data are available from the BERDL repository at "
            "https://berdl.kbase.us under accession PRJNA000001. "
            "Code is available at https://github.com/example/repo."
        ),
        "references": (
            "## References\n\n"
            "[1] Smith J et al. Title. Journal 2023.\n"
            "[2] Doe J et al. Title. Journal 2024.\n"
        ),
    }


def _happy_references_md() -> str:
    return (
        "# References\n\n"
        "[1] Smith J et al. Title 1. Journal 2023; 1:1-10.\n"
        "[2] Doe J et al. Title 2. Journal 2024; 2:11-20.\n"
    )


def _happy_bibliography_bib() -> str:
    return (
        "@article{Smith2023a,\n"
        '  title = {Title 1},\n'
        '  author = {Smith, J.},\n'
        "  year = {2023},\n"
        "}\n\n"
        "@article{Doe2024b,\n"
        '  title = {Title 2},\n'
        '  author = {Doe, J.},\n'
        "  year = {2024},\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

class TestMarkdownParsing:
    def test_split_into_sections_basic(self):
        text = "# A\nbody A\n# B\nbody B"
        sections = vm.split_into_sections(text)
        assert "a" in sections
        assert "b" in sections
        assert "body A" in sections["a"]
        assert "body B" in sections["b"]

    def test_split_h2_sections(self):
        text = "## Methods\nM body\n## Results\nR body"
        sections = vm.split_into_sections(text)
        assert "methods" in sections
        assert "results" in sections

    def test_h3_kept_inside_parent_section(self):
        text = "## Methods\nM body\n### Subsection\nsubbody\n## Results\nR body"
        sections = vm.split_into_sections(text)
        assert "subsection" not in sections
        assert "Subsection" in sections["methods"]

    def test_find_section_alias_match(self):
        sections = {"materials and methods": "M body"}
        result = vm.find_section(sections, vm.PAPER_REQUIRED_SECTIONS["methods"])
        assert result == "M body"

    def test_find_section_no_match(self):
        sections = {"randomname": "x"}
        result = vm.find_section(sections, ("methods",))
        assert result is None


# ---------------------------------------------------------------------------
# Citation/reference extraction
# ---------------------------------------------------------------------------

class TestCitationExtraction:
    def test_single_citation(self):
        nums = vm.extract_citation_numbers("text [3] more")
        assert nums == {3}

    def test_comma_list(self):
        nums = vm.extract_citation_numbers("text [1,2,5] more")
        assert nums == {1, 2, 5}

    def test_range(self):
        nums = vm.extract_citation_numbers("text [3-7] more")
        assert nums == {3, 4, 5, 6, 7}

    def test_mixed(self):
        nums = vm.extract_citation_numbers("a [1] b [3,4] c [10-12]")
        assert nums == {1, 3, 4, 10, 11, 12}

    def test_no_citations(self):
        assert vm.extract_citation_numbers("plain text") == set()

    def test_extract_references_numbered(self):
        md = "[1] Foo\n[2] Bar\n[3] Baz\n"
        assert vm.extract_reference_numbers(md) == {1, 2, 3}

    def test_extract_references_dotted(self):
        md = "1. Foo\n2. Bar\n10. Baz\n"
        assert vm.extract_reference_numbers(md) == {1, 2, 10}

    def test_extract_bib_keys(self):
        bib = "@article{Smith2023, title={X}}\n@misc{Doe2024, title={Y}}\n"
        assert vm.extract_bib_keys(bib) == {"Smith2023", "Doe2024"}


# ---------------------------------------------------------------------------
# M1 — IMRAD sections present
# ---------------------------------------------------------------------------

class TestM1ImradSections:
    def test_pass_paper_mode(self):
        result = vm.validate_M1_imrad_sections(_happy_paper_sections(), "paper")
        assert result.status == "pass"
        assert result.violations == []

    def test_fail_missing_methods(self):
        sections = _happy_paper_sections()
        del sections["methods"]
        result = vm.validate_M1_imrad_sections(sections, "paper")
        assert result.status == "fail"
        assert any("methods" in v.message.lower() for v in result.violations)

    def test_fail_missing_multiple_sections(self):
        sections = _happy_paper_sections()
        del sections["methods"]
        del sections["discussion"]
        result = vm.validate_M1_imrad_sections(sections, "paper")
        assert result.status == "fail"
        assert len(result.violations) == 2

    def test_accepts_methods_alias(self):
        sections = _happy_paper_sections()
        sections["materials and methods"] = sections.pop("methods")
        result = vm.validate_M1_imrad_sections(sections, "paper")
        assert result.status == "pass"

    def test_report_mode_different_sections(self):
        report_sections = {
            "project summary": "...",
            "background and question": "...",
            "what was done": "...",
            "what was observed": "...",
            "observations and open questions": "...",
            "limitations and caveats": "...",
            "next steps": "...",
        }
        result = vm.validate_M1_imrad_sections(report_sections, "report")
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# M2 — Structured abstract
# ---------------------------------------------------------------------------

class TestM2StructuredAbstract:
    def test_pass_with_bold_subsections(self):
        result = vm.validate_M2_structured_abstract(
            _happy_paper_sections(), "paper"
        )
        assert result.status == "pass"

    def test_pass_with_h3_subsections(self):
        sections = {
            "abstract": (
                "## Abstract\n\n"
                "### Background\nB\n\n"
                "### Methods\nM\n\n"
                "### Results\nR\n\n"
                "### Conclusions\nC\n"
            ),
        }
        result = vm.validate_M2_structured_abstract(sections, "paper")
        assert result.status == "pass"

    def test_fail_unstructured_prose(self):
        sections = {
            "abstract": "## Abstract\n\nA single paragraph of prose.",
        }
        result = vm.validate_M2_structured_abstract(sections, "paper")
        assert result.status == "fail"

    def test_fail_no_abstract(self):
        sections = {"methods": "## Methods\nx"}
        result = vm.validate_M2_structured_abstract(sections, "paper")
        assert result.status == "fail"

    def test_not_applicable_in_report_mode(self):
        result = vm.validate_M2_structured_abstract({}, "report")
        assert result.status == "not-applicable"


# ---------------------------------------------------------------------------
# M3 — AI disclosure
# ---------------------------------------------------------------------------

class TestM3AiDisclosure:
    def test_pass_with_tool_and_action(self):
        result = vm.validate_M3_ai_disclosure(_happy_paper_sections(), "paper")
        assert result.status == "pass"

    def test_fail_no_tool_name(self):
        sections = {
            "methods": "## Methods\n\nWe did the analysis ourselves.",
        }
        result = vm.validate_M3_ai_disclosure(sections, "paper")
        assert result.status == "fail"

    def test_pass_with_acknowledgments(self):
        sections = {
            "acknowledgments": (
                "## Acknowledgments\n\n"
                "Manuscript drafting used the Claude-based BERIL paper-writer."
            ),
        }
        result = vm.validate_M3_ai_disclosure(sections, "paper")
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# M4 — Data availability
# ---------------------------------------------------------------------------

class TestM4DataAvailability:
    def test_pass_with_url(self):
        result = vm.validate_M4_data_availability(
            _happy_paper_sections(), "paper"
        )
        assert result.status == "pass"

    def test_fail_no_section(self):
        sections = {"methods": "x"}
        result = vm.validate_M4_data_availability(sections, "paper")
        assert result.status == "fail"

    def test_fail_too_short(self):
        sections = {
            "data availability": "## Data Availability\n\nUpon request.",
        }
        result = vm.validate_M4_data_availability(sections, "paper")
        assert result.status == "fail"
        assert "too short" in result.violations[0].message.lower() or \
               "too short" in result.violations[0].message

    def test_fail_no_url_or_accession(self):
        sections = {
            "data availability": (
                "## Data Availability\n\n"
                "All data referenced in the manuscript are available "
                "from the corresponding author upon reasonable request "
                "after appropriate ethical clearance has been obtained "
                "from the relevant institutional review boards."
            ),
        }
        result = vm.validate_M4_data_availability(sections, "paper")
        assert result.status == "fail"
        assert "available upon request" in result.violations[0].message.lower() \
               or "url" in result.violations[0].message.lower() \
               or "accession" in result.violations[0].message.lower()

    def test_pass_with_doi(self):
        sections = {
            "data availability": (
                "## Data Availability\n\n"
                "All data are deposited at the European Nucleotide Archive "
                "under doi:10.5061/dryad.example12345 and the related "
                "code is at https://github.com/example/repo."
            ),
        }
        result = vm.validate_M4_data_availability(sections, "paper")
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# M5 — Software + version (soft-warning)
# ---------------------------------------------------------------------------

class TestM5SoftwareVersion:
    def test_pass_with_versions(self):
        result = vm.validate_M5_software_versions(
            _happy_paper_sections(), "paper"
        )
        assert result.status == "pass"

    def test_pass_with_requirements_reference(self):
        sections = {
            "methods": (
                "## Methods\n\n"
                + "We performed Fisher's exact test, Wilcoxon, and a t-test. " * 30
                + " Software pinned in requirements.txt."
            ),
        }
        result = vm.validate_M5_software_versions(sections, "paper")
        assert result.status == "pass"

    def test_soft_warning_when_no_version(self):
        sections = {
            "methods": (
                "## Methods\n\n"
                + "We performed Fisher's exact test, Wilcoxon test, and t-test. " * 30
            ),
        }
        result = vm.validate_M5_software_versions(sections, "paper")
        assert result.status == "soft-warning"

    def test_not_applicable_when_methods_short(self):
        sections = {"methods": "## Methods\n\nShort methods."}
        result = vm.validate_M5_software_versions(sections, "paper")
        assert result.status == "not-applicable"


# ---------------------------------------------------------------------------
# M6 — Multiple-testing correction
# ---------------------------------------------------------------------------

class TestM6MultipleTesting:
    def test_pass_few_pvalues(self):
        sections = {"results": "## Results\n\np = 0.03 reported."}
        result = vm.validate_M6_multiple_testing(sections, "paper")
        assert result.status == "pass"

    def test_pass_many_pvalues_with_correction(self):
        sections = {
            "results": "## Results\n\n" + "p < 0.05. " * 10,
            "methods": "## Methods\n\nBenjamini-Hochberg FDR was applied.",
        }
        result = vm.validate_M6_multiple_testing(sections, "paper")
        assert result.status == "pass"

    def test_fail_many_pvalues_without_correction(self):
        sections = {
            "results": (
                "## Results\n\n"
                "We found p < 0.001, p = 0.02, p = 0.03, p < 0.05, "
                "p < 0.01, p = 0.04 across all comparisons."
            ),
        }
        result = vm.validate_M6_multiple_testing(sections, "paper")
        assert result.status == "fail"
        assert result.violations[0].escalation_path == "escalate"


# ---------------------------------------------------------------------------
# M7 — Effect sizes + CIs
# ---------------------------------------------------------------------------

class TestM7EffectSizes:
    def test_pass_with_effect_and_ci(self):
        result = vm.validate_M7_effect_sizes(_happy_paper_sections(), "paper")
        assert result.status == "pass"

    def test_soft_warning_pvalue_without_effect(self):
        sections = {"results": "## Results\n\nWe found p < 0.001 in our cohort."}
        result = vm.validate_M7_effect_sizes(sections, "paper")
        assert result.status == "soft-warning"


# ---------------------------------------------------------------------------
# M8 — Counts before percentages
# ---------------------------------------------------------------------------

class TestM8CountsBeforePct:
    def test_pass_with_counts(self):
        result = vm.validate_M8_counts_before_percentages(
            _happy_paper_sections(), "paper"
        )
        assert result.status == "pass"

    def test_warn_bare_percentage(self):
        sections = {
            "results": "## Results\n\nThe outcome was observed in 42% of subjects."
        }
        result = vm.validate_M8_counts_before_percentages(sections, "paper")
        assert result.status == "soft-warning"

    def test_skip_confidence_levels(self):
        sections = {
            "results": "## Results\n\nReported with 95% CI [1.2, 3.4]."
        }
        result = vm.validate_M8_counts_before_percentages(sections, "paper")
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# M9 — Limitations
# ---------------------------------------------------------------------------

class TestM9Limitations:
    def test_pass_substantive(self):
        result = vm.validate_M9_limitations(_happy_paper_sections(), "paper")
        assert result.status == "pass"

    def test_fail_no_section(self):
        sections = {"methods": "x"}
        result = vm.validate_M9_limitations(sections, "paper")
        assert result.status == "fail"

    def test_fail_too_short(self):
        sections = {"limitations": "## Limitations\n\nNone."}
        result = vm.validate_M9_limitations(sections, "paper")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# M10 — Citation cross-reference
# ---------------------------------------------------------------------------

class TestM10CitationCrossref:
    def test_pass_all_citations_resolved(self):
        result = vm.validate_M10_citations_crossref(
            _happy_paper_sections(),
            "paper",
            references_md=_happy_references_md(),
            bibliography_bib=_happy_bibliography_bib(),
        )
        assert result.status == "pass"

    def test_pass_no_citations(self):
        sections = {"introduction": "Plain text. No citations."}
        result = vm.validate_M10_citations_crossref(
            sections, "paper", references_md=None, bibliography_bib=None,
        )
        assert result.status == "pass"

    def test_fail_missing_reference(self):
        sections = {
            "introduction": "Citation [3] without entry [1].",
        }
        result = vm.validate_M10_citations_crossref(
            sections, "paper",
            references_md="[1] Foo\n",  # 3 missing
            bibliography_bib=None,
        )
        assert result.status == "fail"

    def test_warn_bib_count_mismatch(self):
        sections = {"intro": "Cite [1] and [2]."}
        result = vm.validate_M10_citations_crossref(
            sections, "paper",
            references_md="[1] Foo\n[2] Bar\n",
            bibliography_bib="@article{Foo,}",  # only 1 bib for 2 refs
        )
        assert result.status == "soft-warning"


# ---------------------------------------------------------------------------
# Integration: run_all_validators end-to-end
# ---------------------------------------------------------------------------

class TestRunAllValidators:
    def test_happy_path(self, tmp_path: Path):
        # Build a per-section draft directory
        draft = tmp_path / "draft_1"
        draft.mkdir()
        sections = _happy_paper_sections()
        # Write per-section files in IMRAD order
        section_to_filename = {
            "title": "00_throughline.md",
            "abstract": "05_abstract.md",
            "introduction": "04_introduction.md",
            "methods": "01_methods.md",
            "results": "02_results.md",
            "discussion": "03_discussion.md",
            "limitations": "06_limitations.md",
            "data availability": "07_data_availability.md",
            "references": "references.md",
        }
        for sec_name, fname in section_to_filename.items():
            (draft / fname).write_text(sections[sec_name], encoding="utf-8")
        (draft / "bibliography.bib").write_text(
            _happy_bibliography_bib(), encoding="utf-8"
        )

        report = vm.run_all_validators(draft, mode="paper")
        d = report.to_dict()
        # M5 may be not-applicable because section_word_count is uncertain in
        # synthetic short fixture; M7/M8 may be pass; M1-M10 should all be
        # pass / not-applicable / soft-warning at worst.
        assert d["summary"]["overall_status"] in ("pass", "warn"), (
            f"happy path should pass or warn, not fail; got summary "
            f"{d['summary']}"
        )

    def test_failing_draft(self, tmp_path: Path):
        draft = tmp_path / "draft_bad"
        draft.mkdir()
        # Write only an Abstract, missing everything else
        (draft / "05_abstract.md").write_text(
            "## Abstract\n\nUnstructured prose.\n", encoding="utf-8"
        )
        report = vm.run_all_validators(draft, mode="paper")
        d = report.to_dict()
        assert d["summary"]["overall_status"] == "fail"
        assert d["summary"]["failed"] >= 1


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------

class TestCLI:
    SCRIPT = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "validate_manuscript.py"
    )

    def test_cli_exit_1_on_failing_draft(self, tmp_path: Path):
        draft = tmp_path / "draft_bad"
        draft.mkdir()
        (draft / "05_abstract.md").write_text(
            "## Abstract\n\nx\n", encoding="utf-8"
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(draft), "--mode", "paper"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        # Stdout should be valid JSON
        report = json.loads(proc.stdout)
        assert report["summary"]["overall_status"] == "fail"

    def test_cli_exit_2_on_missing_dir(self, tmp_path: Path):
        bogus = tmp_path / "nope"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(bogus)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        assert "does not exist" in proc.stderr
