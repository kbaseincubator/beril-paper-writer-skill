"""Tests for skill/tools/check_numeric_grounding.py — Stage 4 Tier T.

Coverage:
  - normalize_numeric: payload extraction, comma stripping, scientific
    notation, percentages, "-0" → "0", empty strings.
  - allowlist_reason: each rule (section, citation_bracket,
    figure_ref, sectional_ref, publication_year, trivial_count) and
    crucially the claim-shaped-class guard that prevents the
    trivial-noun-phrase rule from eating count_of / n_count / etc.
  - load_inventory_claim_texts: tsv parsing, header-required, empty
    rows skipped, missing-file → empty list.
  - build_inventory_normalized_set + build_report_normalized_set:
    set semantics for grounding lookups.
  - _iter_manuscript_with_sections: heading walker, paragraph counter
    discipline, front-matter section label.
  - run_grounding: full pipeline on synthetic manuscript covering
    Tier A grounding, Tier B grounding, Tier C ungrounded, allowlist
    suppression, section attribution, ungrounded severity = P0.
  - CLI: happy path on a synthetic draft; missing manuscript →
    non-zero exit; missing inventory + report → both Tier A and B
    notes recorded.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools import check_numeric_grounding as cng


# ---------------------------------------------------------------------------
# normalize_numeric
# ---------------------------------------------------------------------------

class TestNormalizeNumeric:
    def test_strips_thousand_separator_commas(self):
        assert cng.normalize_numeric("219,121") == "219121"
        assert cng.normalize_numeric("1,234,567") == "1234567"

    def test_handles_percentage_with_sign(self):
        assert cng.normalize_numeric("77%") == "77"
        assert cng.normalize_numeric("0.5%") == "0.5"

    def test_handles_scientific_notation(self):
        assert cng.normalize_numeric("4e-4") == "4e-4"
        assert cng.normalize_numeric("5E-3") == "5e-3"
        assert cng.normalize_numeric("p = 1.2e-5") == "1.2e-5"

    def test_handles_negative_zero(self):
        assert cng.normalize_numeric("-0") == "0"
        assert cng.normalize_numeric("-0.0") == "0"

    def test_strips_leading_plus(self):
        assert cng.normalize_numeric("+0.50") == "0.50"

    def test_extracts_first_numeric_payload(self):
        # count_of pattern matches "X of Y" — normalize picks the first.
        assert cng.normalize_numeric("105 of 137") == "105"
        # n=156 pattern — normalize picks "156".
        assert cng.normalize_numeric("n = 156") == "156"

    def test_empty_string_returns_empty(self):
        assert cng.normalize_numeric("") == ""
        assert cng.normalize_numeric("   ") == ""

    def test_no_numeric_payload_returns_empty(self):
        # A malformed match (e.g., the regex caught something with no
        # actual digits inside, which shouldn't happen but the
        # function tolerates it).
        assert cng.normalize_numeric("only words") == ""


# ---------------------------------------------------------------------------
# allowlist_reason
# ---------------------------------------------------------------------------

class TestAllowlistReason:
    def test_section_allowlist_suppresses_references(self):
        # The matched_text doesn't matter when the section is
        # allowlisted — that's the whole point.
        reason = cng.allowlist_reason(
            matched_text="2024",
            match_start=0,
            match_end=4,
            full_text="2024 Smith J et al.",
            section="references",
        )
        assert reason == "section_allowlist:references"

    def test_section_allowlist_suppresses_acknowledgments(self):
        reason = cng.allowlist_reason(
            matched_text="3",
            match_start=0,
            match_end=1,
            full_text="3 reviewers helped.",
            section="acknowledgments",
        )
        assert reason == "section_allowlist:acknowledgments"

    def test_citation_bracket_inner_single(self):
        # "[3]" — the regex catches the inner "3"; allowlist should
        # see the surrounding brackets via _CITATION_BRACKET_RE.
        text = "as in prior work [3]."
        # Locate the "3" position.
        offset = text.index("3")
        reason = cng.allowlist_reason(
            matched_text="3",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="results",
        )
        assert reason == "citation_bracket_inner"

    def test_citation_bracket_multi(self):
        # "[1,2,3]" — the middle "2" is comma-flanked.
        text = "...[1,2,3]..."
        offset = text.index("2")
        reason = cng.allowlist_reason(
            matched_text="2",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="results",
        )
        assert reason == "citation_bracket_inner"

    def test_figure_reference(self):
        text = "as shown in Figure 3, the curve diverges."
        offset = text.index("3")
        reason = cng.allowlist_reason(
            matched_text="3",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="results",
        )
        assert reason == "figure_or_table_ref"

    def test_table_reference(self):
        text = "summarized in Table 2 alongside controls."
        offset = text.index("2")
        reason = cng.allowlist_reason(
            matched_text="2",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="results",
        )
        assert reason == "figure_or_table_ref"

    def test_sectional_reference(self):
        text = "we examine Pillar 4 in detail below."
        offset = text.index("4")
        reason = cng.allowlist_reason(
            matched_text="4",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="results",
        )
        assert reason == "sectional_ref"

    def test_publication_year_4digit(self):
        text = "since 2018, this has been refined."
        offset = text.index("2018")
        reason = cng.allowlist_reason(
            matched_text="2018",
            match_start=offset,
            match_end=offset + 4,
            full_text=text,
            section="results",
        )
        assert reason == "publication_year"

    def test_trivial_noun_phrase_count_suppressed_for_non_claim_class(self):
        # "in 4 cohorts" — small integer in noun-phrase context.
        text = "executed in 4 cohorts across the consortium."
        offset = text.index("4")
        reason = cng.allowlist_reason(
            matched_text="4",
            match_start=offset,
            match_end=offset + 1,
            full_text=text,
            section="methods",
            match_class="",  # unknown class — allowed to be trivial
        )
        assert reason == "trivial_noun_phrase_count"

    def test_count_of_claim_NEVER_suppressed_by_trivial_rule(self):
        """The headline allowlist bug: count_of matches like "5 of 6"
        have a leading "of" that matches the preposition+digit context
        pattern, but these ARE empirical claims and must not be
        suppressed. Live-test draft_1 had 4 such cases incorrectly
        eaten before the match_class guard was added."""
        # Synthesize a count_of context. The pattern requires a
        # leading word context.
        text = "applied to 105 of 137 patients in the sub-cohort."
        offset = text.index("105")
        reason = cng.allowlist_reason(
            matched_text="105 of 137",
            match_start=offset,
            match_end=offset + len("105 of 137"),
            full_text=text,
            section="results",
            match_class="count_of",  # ← the guard
        )
        assert reason is None  # passes through to grounding

    def test_n_count_claim_NEVER_suppressed_by_trivial_rule(self):
        text = "the cohort comprised n=56 patients."
        offset = text.index("n=56") + 2  # the "56" part
        reason = cng.allowlist_reason(
            matched_text="56",
            match_start=offset,
            match_end=offset + 2,
            full_text=text,
            section="methods",
            match_class="n_count",
        )
        assert reason is None

    def test_percentage_claim_NEVER_suppressed_by_trivial_rule(self):
        text = "the response rate was 12% across the arm."
        offset = text.index("12")
        reason = cng.allowlist_reason(
            matched_text="12%",
            match_start=offset,
            match_end=offset + 3,
            full_text=text,
            section="results",
            match_class="percentage",
        )
        assert reason is None

    def test_grounded_claim_in_normal_section_no_suppression(self):
        """A normal claim-shaped number with no allowlist triggers."""
        text = "the AUC was 0.847 on the held-out set."
        offset = text.index("0.847")
        reason = cng.allowlist_reason(
            matched_text="0.847",
            match_start=offset,
            match_end=offset + 5,
            full_text=text,
            section="results",
            match_class="metric",
        )
        assert reason is None


# ---------------------------------------------------------------------------
# Inventory parsing
# ---------------------------------------------------------------------------

def _write_inventory(path: Path, claims: list[str]) -> None:
    """Write a minimal claim_inventory.tsv with the canonical header."""
    header = (
        "claim_id\tclaim_text\tsource_notebook\tsource_cell\t"
        "figure_or_table\teffect_size_present\tci_present\t"
        "pvalue_present\tnotes\n"
    )
    rows = [
        f"C-{i:03d}\t{txt}\tNB00.ipynb\t\t\tno\tno\tno\t"
        for i, txt in enumerate(claims, start=1)
    ]
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


class TestInventoryParsing:
    def test_load_inventory_happy_path(self, tmp_path: Path):
        inv = tmp_path / "claim_inventory.tsv"
        _write_inventory(inv, ["AUC = 0.847 on held-out", "n=156 patients"])
        texts = cng.load_inventory_claim_texts(inv)
        assert texts == ["AUC = 0.847 on held-out", "n=156 patients"]

    def test_load_inventory_missing_returns_empty(self, tmp_path: Path):
        texts = cng.load_inventory_claim_texts(tmp_path / "no_such_file.tsv")
        assert texts == []

    def test_load_inventory_skips_empty_claim_text(self, tmp_path: Path):
        inv = tmp_path / "claim_inventory.tsv"
        _write_inventory(inv, ["claim A", "", "claim B"])
        texts = cng.load_inventory_claim_texts(inv)
        assert texts == ["claim A", "claim B"]

    def test_inventory_normalized_set_extracts_numerics(self, tmp_path: Path):
        inv = tmp_path / "claim_inventory.tsv"
        _write_inventory(inv, [
            "AUC = 0.847 on held-out",
            "n=156 patients",
            "p = 4e-4 on CC1",
        ])
        texts = cng.load_inventory_claim_texts(inv)
        norm_set = cng.build_inventory_normalized_set(texts)
        assert "0.847" in norm_set
        assert "156" in norm_set
        assert "4e-4" in norm_set

    def test_report_normalized_set_missing_file_returns_empty(self, tmp_path: Path):
        assert cng.build_report_normalized_set(tmp_path / "no_report.md") == set()
        assert cng.build_report_normalized_set(None) == set()

    def test_report_normalized_set_indexes_numerics(self, tmp_path: Path):
        report = tmp_path / "REPORT.md"
        report.write_text(
            "## Findings\n\nAUC = 0.847 on held-out set.\n"
            "Sample size n=156. p = 4e-4 on CC1.\n",
            encoding="utf-8",
        )
        norm = cng.build_report_normalized_set(report)
        assert "0.847" in norm
        assert "156" in norm
        assert "4e-4" in norm


# ---------------------------------------------------------------------------
# Manuscript walker
# ---------------------------------------------------------------------------

class TestManuscriptWalker:
    def test_walker_emits_section_labels_from_headings(self):
        text = textwrap.dedent("""\
            # Title

            ## Abstract

            Abstract text.

            ## Methods

            Methods text.
            """)
        lines = list(cng._iter_manuscript_with_sections(text))
        sections = [s for (_l, _o, s, _p) in lines]
        # "# Title" line is NOT a ## heading so the line itself is
        # yielded with section="front-matter".
        assert sections[0] == "front-matter"
        # Subsequent non-blank lines are inside abstract or methods.
        assert "abstract" in sections
        assert "methods" in sections

    def test_walker_resets_paragraph_counter_at_section_boundary(self):
        text = textwrap.dedent("""\
            ## Abstract

            Abstract paragraph 1.

            Abstract paragraph 2.

            ## Methods

            Methods paragraph 1.
            """)
        lines = list(cng._iter_manuscript_with_sections(text))
        # Each non-blank yields. After a "##" heading paragraph counter
        # resets to 1 (note: blank line before the heading already
        # incremented it once, but the heading resets back).
        abstract_para_nums = [p for (_l, _o, s, p) in lines if s == "abstract"]
        methods_para_nums = [p for (_l, _o, s, p) in lines if s == "methods"]
        assert min(abstract_para_nums) == 1
        assert max(abstract_para_nums) == 2
        assert min(methods_para_nums) == 1


# ---------------------------------------------------------------------------
# End-to-end run_grounding
# ---------------------------------------------------------------------------

class TestRunGrounding:
    """End-to-end run_grounding tests use claim-shaped numerics (AUC=X,
    n=N, p=P, X of Y, etc.) because the D-036 regex catalog only
    matches those forms — bare "0.847" in prose is intentionally
    invisible to the extractor. The catalog was tuned in M1_PUNCH_LIST
    §B1 to balance precision and recall on REPORT-style text;
    expanding it is out of scope for Tier T."""

    def test_tier_a_grounded_via_inventory(self, tmp_path: Path):
        """A number present in the inventory grounds at Tier A and
        does NOT appear in findings."""
        ms = "## Results\n\nThe held-out AUC = 0.847 on the test set.\n"
        inv = {"0.847"}
        rep: set[str] = set()
        findings, allowlisted, totals = cng.run_grounding(ms, inv, rep)
        assert totals["grounded_tier_a_inventory"] >= 1
        assert totals["ungrounded"] == 0
        assert findings == []

    def test_tier_b_grounded_via_report(self, tmp_path: Path):
        """A number not in the inventory but present in REPORT.md
        grounds at Tier B."""
        ms = "## Results\n\nThe held-out AUC = 0.847.\n"
        inv: set[str] = set()
        rep = {"0.847"}
        findings, _, totals = cng.run_grounding(ms, inv, rep)
        assert totals["grounded_tier_b_report_md"] >= 1
        assert totals["ungrounded"] == 0
        assert findings == []

    def test_tier_c_ungrounded_marked_p0(self, tmp_path: Path):
        """A number in neither inventory nor REPORT is ungrounded P0."""
        ms = "## Results\n\nThe held-out AUC = 0.999 on test.\n"
        findings, _, totals = cng.run_grounding(ms, set(), set())
        assert totals["ungrounded"] >= 1
        ungrounded_norms = {f.normalized_value for f in findings}
        assert "0.999" in ungrounded_norms
        for f in findings:
            assert f.severity == "P0"

    def test_count_of_ungrounded_is_caught(self):
        """The headline draft_1 case: '105 of 137' is a count_of claim
        not in inventory or REPORT. It must be ungrounded P0."""
        ms = (
            "## Results\n\nAcross sub-studies, 105 of 137 pairs were "
            "significant.\n"
        )
        findings, _, totals = cng.run_grounding(ms, set(), set())
        assert totals["ungrounded"] >= 1
        ungrounded_classes = {f.match_class for f in findings}
        assert "count_of" in ungrounded_classes

    def test_allowlisted_match_does_not_become_finding(self):
        """A claim-shaped numeric inside the References section is
        allowlisted by section, not flagged. Using References (not
        Figure-ref) because bare integers like '3' in 'Figure 3' fall
        below the regex catalog's claim-shape threshold and never get
        extracted in the first place — there's nothing to allowlist."""
        ms = textwrap.dedent("""\
            ## References

            [1] Smith J et al. (2024). AUC = 0.847 on test set.
            doi:10.1234/abc
            """)
        findings, allowlisted, totals = cng.run_grounding(ms, set(), set())
        assert totals["allowlisted"] >= 1
        assert totals["ungrounded"] == 0
        # Reason should be section_allowlist:references
        reasons = {a.reason for a in allowlisted}
        assert any("references" in r for r in reasons)

    def test_section_attribution_correct(self):
        """Findings carry the canonicalized heading as the section."""
        ms = textwrap.dedent("""\
            ## Methods

            We sampled at AUC=0.111.

            ## Results

            The held-out AUC = 0.222 on test.
            """)
        findings, _, _ = cng.run_grounding(ms, set(), set())
        sections = {f.section for f in findings}
        assert "methods" in sections
        assert "results" in sections


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    SCRIPT = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "check_numeric_grounding.py"
    )

    def _build_draft(
        self,
        tmp_path: Path,
        *,
        manuscript_body: str,
        inventory_claims: list[str] = (),
        report_body: str = "",
    ) -> Path:
        """Build a draft_dir at tmp_path mimicking the BERDL layout
        (papers/draft_1 inside a project root)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "REPORT.md").write_text(report_body, encoding="utf-8")
        papers_dir = project_dir / "papers"
        papers_dir.mkdir()
        draft_dir = papers_dir / "draft_1"
        draft_dir.mkdir()
        (draft_dir / "manuscript.md").write_text(
            manuscript_body, encoding="utf-8",
        )
        if inventory_claims:
            _write_inventory(
                draft_dir / "claim_inventory.tsv",
                list(inventory_claims),
            )
        return draft_dir

    def test_cli_happy_path_writes_audit_json(self, tmp_path: Path):
        draft_dir = self._build_draft(
            tmp_path,
            manuscript_body=(
                "## Results\n\nThe held-out AUC = 0.847 (grounded).\n"
                "An ungrounded value: held-out AUC = 0.999 also.\n"
            ),
            inventory_claims=["AUC = 0.847 on held-out"],
            report_body="## Findings\n\nAUC = 0.847 on held-out.\n",
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(draft_dir), "--quiet"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr

        out = json.loads(
            (draft_dir / "audit" / "numeric_grounding.json").read_text()
        )
        assert out["schema_version"] == cng.SCHEMA_VERSION
        assert out["tool"] == "check_numeric_grounding"
        assert out["totals"]["ungrounded"] >= 1
        # The 0.999 must be among the ungrounded findings.
        ungrounded_norms = {
            f["normalized_value"] for f in out["findings"]
        }
        assert "0.999" in ungrounded_norms

    def test_cli_errors_on_missing_manuscript(self, tmp_path: Path):
        # Build a draft_dir without manuscript.md.
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        papers_dir = project_dir / "papers"
        papers_dir.mkdir()
        draft_dir = papers_dir / "draft_1"
        draft_dir.mkdir()
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0
        assert "manuscript.md not found" in proc.stderr

    def test_cli_records_notes_when_inventory_and_report_missing(
        self, tmp_path: Path,
    ):
        # Build a draft_dir with manuscript.md only — no inventory or REPORT.
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        papers_dir = project_dir / "papers"
        papers_dir.mkdir()
        draft_dir = papers_dir / "draft_1"
        draft_dir.mkdir()
        (draft_dir / "manuscript.md").write_text(
            "## Results\n\nThe held-out AUC = 0.847.\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(draft_dir), "--quiet"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        out = json.loads(
            (draft_dir / "audit" / "numeric_grounding.json").read_text()
        )
        # Both notes should be recorded.
        notes_text = " ".join(out["notes"])
        assert "claim_inventory" in notes_text
        assert "REPORT.md" in notes_text
        # 0.847 must be ungrounded (nothing in either source).
        assert out["totals"]["ungrounded"] >= 1


# ---------------------------------------------------------------------------
# Stage 7 Patch 2 (2026-05-18) — generic source-side extractor
#
# D1 (conservation_vs_fitness/draft_1) surfaced that the prior
# build_normalized_set used the claim-shaped extract_numeric_matches
# on REPORT.md, missing numbers that lived in dense prose without
# `n=` or `X of Y` keywords. The patch switches to a generic
# `\d+(?:\.\d+)?` regex so every number on the source side is
# indexed. Plus a range-dash carve-out for "12.9-28.9%" patterns.
# ---------------------------------------------------------------------------


class TestPatch2GenericSourceExtractor:
    """The new build_normalized_set must capture numbers regardless of
    surrounding linguistic shape — the source side's job is value
    presence, not claim recognition."""

    def test_picks_up_numbers_in_dense_prose_without_claim_keywords(self):
        """The actual D1 failure case: a sentence with multiple numbers
        in prose without `n=`, `X of Y`, or other claim keywords. The
        claim-shaped extractor missed these; the generic one should
        catch all of them."""
        text = (
            "27,693 putative essential genes identified (18.6% of "
            "148,826 protein-coding genes across 33 organisms; "
            "range 12.9-28.9% per organism)"
        )
        norm = cng.build_normalized_set(text)
        for n in ["27693", "18.6", "148826", "33", "12.9", "28.9"]:
            assert n in norm, f"{n!r} should be in generic-extracted source set"

    def test_handles_range_dashes_via_unsigned_carveout(self):
        """A range '12.9-28.9%' must contribute BOTH 12.9 and 28.9 to
        the source set. The regex matches '-28.9' (sign-prefixed); the
        carve-out adds '28.9' as an unsigned alias."""
        text = "Per-organism essentiality range 12.9-28.9% across the cohort."
        norm = cng.build_normalized_set(text)
        assert "12.9" in norm
        assert "28.9" in norm
        # Both signed and unsigned forms present for the second value.
        assert "-28.9" in norm

    def test_picks_up_table_cell_numbers(self):
        """Numbers in markdown table cells must be extractable. Tier T
        previously missed these because the claim-shaped regexes
        require specific surrounding tokens."""
        text = (
            "| Essential-core | 19,128 | 22.7% | 44.7% |\n"
            "| Essential-unmapped | 1,259 | 18.2% | 44.7% |\n"
        )
        norm = cng.build_normalized_set(text)
        for n in ["19128", "22.7", "44.7", "1259", "18.2"]:
            assert n in norm, f"{n!r} missing from table-cell extraction"

    def test_strips_commas_consistently(self):
        """'1,259' must normalise to '1259' in the source set, matching
        how manuscript-side normalize_numeric handles it."""
        text = "Range from 1,259 to 124,744 across the cohort."
        norm = cng.build_normalized_set(text)
        assert "1259" in norm
        assert "124744" in norm

    def test_preserves_negative_numbers_in_addition_to_unsigned(self):
        """A legitimate negative number like Cliff's delta '-0.05' must
        be preserved as '-0.05' AND also stored as '0.05' (so a
        manuscript that quotes '0.05' grounds; the Tier 3 reviewer
        catches sign-misuse cases via context)."""
        text = "Cliff's delta = -0.05 (small effect)."
        norm = cng.build_normalized_set(text)
        assert "-0.05" in norm
        assert "0.05" in norm

    def test_handles_scientific_notation(self):
        """p-values in 1e-N form must extract correctly. Note: regex
        lowercases the exponent marker via the unified normalisation."""
        text = "p < 1.5e-6 versus baseline 4E-04 condition."
        norm = cng.build_normalized_set(text)
        assert "1.5e-6" in norm
        assert "4e-04" in norm

    def test_dash_separated_range_grounds_via_both_endpoints(self):
        """End-to-end: a manuscript claim '28.9%' against a REPORT
        containing 'range 12.9-28.9%' must ground via Tier B."""
        manuscript = "## Results\n\nPer-organism essentiality reaches 28.9%.\n"
        report = "Essentiality range is 12.9-28.9% across organisms."
        inv_norm: set[str] = set()
        rep_norm = cng.build_normalized_set(report)
        findings, _, totals = cng.run_grounding(
            manuscript, inv_norm, rep_norm,
        )
        assert totals["ungrounded"] == 0, (
            f"28.9% should ground against the range endpoint; "
            f"got findings: {[f.matched_text for f in findings]}"
        )

    def test_compound_x_of_y_grounds_when_first_number_in_source(self):
        """Manuscript 'X of Y' compound matches normalize to just X
        (first number); if X appears in REPORT, the claim grounds.
        This validates the patch on the headline D1 failure case
        '27,693 of 148,826'."""
        manuscript = (
            "## Methods\n\nThe study covered 27,693 of 148,826 genes.\n"
        )
        report = "27,693 putative essential genes were identified."
        inv_norm: set[str] = set()
        rep_norm = cng.build_normalized_set(report)
        findings, _, totals = cng.run_grounding(
            manuscript, inv_norm, rep_norm,
        )
        assert totals["ungrounded"] == 0

    def test_genuinely_fabricated_number_still_flagged(self):
        """Critical: the looser source extractor must NOT make all
        fabrications ground by accident. '70%' that's nowhere in
        REPORT must stay ungrounded."""
        manuscript = (
            "## Introduction\n\nWe expected 70% conservation per "
            "organism but the cohort showed less.\n"
        )
        report = (
            "Essentiality ranges 12.9-28.9% across organisms; median "
            "odds ratio 1.56 (range 0.83-3.21)."
        )
        inv_norm: set[str] = set()
        rep_norm = cng.build_normalized_set(report)
        findings, _, totals = cng.run_grounding(
            manuscript, inv_norm, rep_norm,
        )
        # 70 should be ungrounded; nothing else from the manuscript
        # is a numeric claim (rough check).
        unrounded_values = {f.matched_text for f in findings}
        assert any("70" in v for v in unrounded_values), (
            f"genuine fabrication '70%' should stay ungrounded; "
            f"findings: {unrounded_values}"
        )

    def test_empty_source_yields_empty_set(self):
        """Edge case: empty source returns empty set, no errors."""
        assert cng.build_normalized_set("") == set()
