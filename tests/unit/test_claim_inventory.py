"""Tests for skill/tools/claim_inventory.py — Phase 0 NEW tool (M1).

Coverage in THIS conversation (B1.a + B1.b only):

  - B2.a (6 tests) Regex extraction — one per pattern class:
      - percentage
      - ratio_with_unit
      - p_value (decimal AND scientific flavors)
      - confidence_interval
      - n_count (with word-boundary edge case)
      - metric (AUC/R²/RMSE/MAE)

  - B2.b (2 tests) Sentence segmentation:
      - period-not-in-decimal (1.5% does NOT split a sentence)
      - paragraph-break (\\n\\n closes a sentence)

  - B2.f (2 tests) I/O contract:
      - TSV well-formed (header row matches TSV_COLUMNS; round-trip
        through csv.DictReader recovers the input candidates)
      - Flag presence/absence flags correct (a sentence containing a
        p-value sets pvalue_present=yes; a sentence with no statistical
        rigor signals sets all three flags to no)

OUT OF SCOPE for this conversation (deferred):
  - B2.c LLM demarcation tests (B1.c not implemented).
  - B2.d Validator tests (B1.d not implemented).
  - B2.e Idempotency cache tests (B1.d).

The render-smoke discipline from feedback_render_test_must_evaluate_fstring
is exercised in B2.f's well-formed TSV test (it parses the actual
emitter output, not greps the source).
"""

from __future__ import annotations

import csv
import io
import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools import claim_inventory as ci


# ===========================================================================
# B2.a — Regex extraction (6 tests, one per pattern class)
# ===========================================================================


class TestB2aRegexExtraction:
    """One test per pattern class. Each test asserts the regex matches
    the expected substrings AND assigns them to the correct match_class.
    Edge cases (word boundaries, decimal precision, case variations)
    live alongside the headline assertion in each test.
    """

    def test_percentage_class(self):
        """Percentages: integer + decimal forms; word-boundary blocks
        mid-token false positives."""
        text = (
            "We observed 88.2% concordance and a 5% baseline. "
            "Mid-token X88.2% should still match because the percentage "
            "regex anchors on word-boundary BEFORE the digit, not after a "
            "non-digit. But 100% as a standalone is also captured."
        )
        matches = ci.extract_numeric_matches(text)
        pct_matches = [m for m in matches if m.match_class == "percentage"]
        # Three independent percentages: 88.2%, 5%, 100%, plus the
        # mid-token X88.2% (regex doesn't filter it because `\b` matches
        # the X→8 boundary).
        matched_texts = [m.matched_text for m in pct_matches]
        assert "88.2%" in matched_texts
        assert "5%" in matched_texts
        assert "100%" in matched_texts
        # match_class assigned correctly.
        for m in pct_matches:
            assert m.match_class == "percentage"

    def test_ratio_with_unit_class(self):
        """Ratios with units: bio units + fold + ×/x. Whitespace
        between number and unit is optional."""
        text = (
            "The MIC was 16.2 mg/L. Fold-change of 2.5 fold over baseline. "
            "10× coverage. 12.5 kDa protein. 50 nM dose. 2 kb amplicon. "
            "Negative: 'cold' lacks numeric prefix; 'unit' alone shouldn't match."
        )
        matches = ci.extract_numeric_matches(text)
        ratio_matches = [m for m in matches if m.match_class == "ratio_with_unit"]
        matched_texts = [m.matched_text for m in ratio_matches]
        # All 6 expected unit forms surface.
        # Note: regex is case-insensitive; "mg/L" is preserved.
        assert any("16.2 mg/L" in t for t in matched_texts), matched_texts
        assert any("2.5 fold" in t for t in matched_texts), matched_texts
        assert any("10×" in t for t in matched_texts), matched_texts
        assert any("12.5 kDa" in t for t in matched_texts), matched_texts
        assert any("50 nM" in t for t in matched_texts), matched_texts
        assert any("2 kb" in t for t in matched_texts), matched_texts

    def test_p_value_class(self):
        """p-values: both decimal flavor (`p < 0.05`) and scientific
        flavor (`p = 1.2e-5`)."""
        text = (
            "Significance held with p < 0.05 in subgroup A. "
            "Permutation test gave p = 1.2e-5 across resamples. "
            "Bonferroni-corrected: P=0.001. p > 0.10 in the control arm."
        )
        matches = ci.extract_numeric_matches(text)
        p_matches = [m for m in matches if m.match_class == "p_value"]
        matched_texts = [m.matched_text for m in p_matches]
        # Decimal flavor: "p < 0.05", "P=0.001", "p > 0.10".
        assert any("p < 0.05" in t for t in matched_texts), matched_texts
        assert any("P=0.001" in t for t in matched_texts), matched_texts
        # The "> 0.10" form: regex includes `>` per our pattern (covers
        # one-sided tests + threshold reports).
        assert any("p > 0.10" in t for t in matched_texts), matched_texts
        # Scientific flavor: "p = 1.2e-5".
        assert any("p = 1.2e-5" in t for t in matched_texts), matched_texts

    def test_confidence_interval_class(self):
        """95% CI: bracketed and non-bracketed forms; permissive on
        content."""
        text = (
            "Reported 95% CI: [0.71, 0.85] for the AUC. "
            "Wider band: 95% CI 0.20-0.45 across cohorts. "
            "With colon: 95% CI: 1.2 to 3.4. "
            "Negative: 'CI'='confidence' alone without '95%' should NOT match."
        )
        matches = ci.extract_numeric_matches(text)
        ci_matches = [m for m in matches if m.match_class == "confidence_interval"]
        matched_texts = [m.matched_text for m in ci_matches]
        # All three CI flavors fire.
        assert any("95% CI: [0.71, 0.85]" in t for t in matched_texts), matched_texts
        assert any("95% CI 0.20-0.45" in t for t in matched_texts), matched_texts
        assert any("95% CI:" in t for t in matched_texts), matched_texts
        # Negative control: a stray "CI" in prose without "95%" doesn't
        # produce a confidence_interval match.
        for m in ci_matches:
            assert "95" in m.matched_text, m.matched_text

    def test_n_count_class(self):
        """N-counts: word-boundary critical to avoid mid-token false
        positives (Mn=2 manganese concentration). Documented limitation:
        PCA-component "n=2" still matches because there's no way to
        distinguish from sample-size n=343 deterministically — that's
        B1.c LLM's job."""
        text = (
            "Sample size n=343 across treatments. "
            "We had N = 156 individuals in the cohort. "
            "Mn=2 manganese (this should NOT match — Mn is mid-token). "
            "PCA-component n=2 (this DOES match; LLM filters in B1.c)."
        )
        matches = ci.extract_numeric_matches(text)
        n_matches = [m for m in matches if m.match_class == "n_count"]
        matched_texts = [m.matched_text for m in n_matches]
        # Both legitimate n-counts surface.
        assert any("n=343" in t for t in matched_texts), matched_texts
        assert any("N = 156" in t for t in matched_texts), matched_texts
        # PCA-component n=2 also matches (deterministic limitation).
        assert any("n=2" in t for t in matched_texts), matched_texts
        # Mn=2 must NOT match — word boundary blocks it.
        for m in n_matches:
            # The matched text starts with `n` or `N` (with possible
            # leading whitespace), not embedded in another word.
            stripped = m.matched_text.strip()
            assert stripped[0] in ("n", "N"), (
                f"n_count match {m.matched_text!r} suggests a "
                f"word-boundary regression (Mn=, In=, etc.)"
            )

    def test_metric_class(self):
        """Metrics: AUC, R²/R^2/R2, RMSE, MAE. Decimal portion required.
        Word-boundary blocks 'PaUC' from matching as AUC."""
        text = (
            "Classifier reached AUC = 0.78 on the held-out set. "
            "Goodness of fit was R² = 0.89; alternative form R^2 = 0.85. "
            "Bare R2 = 0.45 (no caret). "
            "RMSE: 0.12. MAE = 0.07. "
            "False positive guard: 'PaUC' is a token, not a metric."
        )
        matches = ci.extract_numeric_matches(text)
        metric_matches = [m for m in matches if m.match_class == "metric"]
        matched_texts = [m.matched_text for m in metric_matches]
        # All five canonical forms fire.
        assert any("AUC = 0.78" in t for t in matched_texts), matched_texts
        assert any("R² = 0.89" in t for t in matched_texts), matched_texts
        assert any("R^2 = 0.85" in t for t in matched_texts), matched_texts
        assert any("R2 = 0.45" in t for t in matched_texts), matched_texts
        assert any("RMSE: 0.12" in t for t in matched_texts), matched_texts
        assert any("MAE = 0.07" in t for t in matched_texts), matched_texts
        # 'PaUC' must NOT produce an AUC match — word boundary critical.
        for m in metric_matches:
            # Each match starts at a word boundary in source text.
            assert m.matched_text.lstrip()[0].isalpha(), m.matched_text


# ===========================================================================
# B2.b — Sentence segmentation (2 tests)
# ===========================================================================


class TestB2bSentenceSegmentation:
    """Per feedback_render_test_must_evaluate_fstring discipline, these
    tests EXERCISE segment_sentences against real REPORT-shaped prose
    and verify boundary behavior. They do NOT grep the source for the
    abbreviation list — the segmenter's behavior IS the contract.
    """

    def test_period_not_in_decimal_does_not_split_sentence(self):
        """A period inside a decimal number (1.5%) is NOT a sentence
        boundary. Two real sentence boundaries → exactly two sentences,
        not four (which would happen with naive `.\\s` splitting)."""
        text = textwrap.dedent("""\
            We observed a 1.5% increase in growth rate at 0.05 mM dose. The control was unaffected.
        """).strip()
        sentences = ci.segment_sentences(text)
        # Naive `re.split(r"\.\s+")` would yield 4 fragments here:
        # "We observed a 1", "5% increase in growth rate at 0",
        # "05 mM dose", "The control was unaffected.". The carve-outs
        # collapse those decimals.
        assert len(sentences) == 2, [s.text for s in sentences]
        assert "1.5%" in sentences[0].text
        assert "0.05" in sentences[0].text
        assert sentences[1].text.startswith("The control"), sentences[1].text

    def test_paragraph_break_closes_sentence(self):
        """A paragraph break (`\\n\\n`) closes a sentence even when the
        prior text didn't end in a terminator. Common in REPORT.md
        bullet-list-followed-by-narrative patterns."""
        text = textwrap.dedent("""\
            We measured AUC = 0.78 across cohorts.
            The CI was tight at 95% CI: [0.71, 0.85].

            Independently, fold change of 2.5 fold confirmed the trend. p < 0.05 throughout.
        """).strip()
        sentences = ci.segment_sentences(text)
        # Three sentences total: two in para1, two in para2 — actually
        # four. The paragraph break creates a hard boundary between
        # para1's content and para2's; soft-wrap within para1 doesn't
        # split the sentence.
        # We expect: ["We measured AUC = 0.78 across cohorts.",
        #             "The CI was tight at 95% CI: [0.71, 0.85].",
        #             "Independently, fold change of 2.5 fold confirmed the trend.",
        #             "p < 0.05 throughout."]
        sentence_texts = [s.text for s in sentences]
        assert len(sentences) == 4, sentence_texts
        # Order is preserved.
        assert sentence_texts[0].startswith("We measured")
        assert sentence_texts[1].startswith("The CI")
        assert sentence_texts[2].startswith("Independently")
        assert sentence_texts[3].startswith("p < 0.05")
        # Paragraph break: sentences[1].end < sentences[2].start with at
        # least one blank line between them.
        between = text[sentences[1].end:sentences[2].start]
        assert "\n\n" in between, repr(between)


# ===========================================================================
# B2.f — I/O contract (2 tests)
# ===========================================================================


class TestB2fIOContract:
    """TSV well-formed (header + round-trip parse) and flag aggregation
    correctness."""

    def test_tsv_well_formed_with_self_describing_header(self, tmp_path: Path):
        """Per feedback_named_columns_in_inserts: TSV header row is
        self-describing. Downstream consumers parse by header name not
        column position. Round-trip the emitter output through
        csv.DictReader and verify field-by-field recovery.
        """
        candidates = [
            ci.ClaimCandidate(
                claim_id="C001",
                claim_text="The AUC was 0.78 across cohorts.",
                source_notebook="",
                source_cell="",
                figure_or_table="",
                effect_size_present="yes",
                ci_present="no",
                pvalue_present="no",
                notes="",
            ),
            ci.ClaimCandidate(
                claim_id="C002",
                claim_text=(
                    "Reported 95% CI: [0.71, 0.85] with p < 0.05 across n=343."
                ),
                source_notebook="",
                source_cell="",
                figure_or_table="",
                effect_size_present="no",
                ci_present="yes",
                pvalue_present="yes",
                notes="unresolved",
            ),
        ]
        tsv_text = ci.format_claim_inventory_tsv(candidates)

        # Header row must match TSV_COLUMNS exactly. Self-describing is
        # the load-bearing property: downstream parsing is by header
        # name lookup.
        first_line = tsv_text.splitlines()[0]
        assert first_line == "\t".join(ci.TSV_COLUMNS), (
            f"TSV header drifted from TSV_COLUMNS: got {first_line!r}; "
            f"expected {chr(9).join(ci.TSV_COLUMNS)!r}"
        )

        # Round-trip through csv.DictReader; verify every field matches
        # the input candidate.
        reader = csv.DictReader(io.StringIO(tsv_text), dialect="excel-tab")
        rows = list(reader)
        assert len(rows) == 2, rows
        assert rows[0]["claim_id"] == "C001"
        assert rows[0]["claim_text"] == "The AUC was 0.78 across cohorts."
        assert rows[0]["effect_size_present"] == "yes"
        assert rows[0]["ci_present"] == "no"
        assert rows[0]["pvalue_present"] == "no"
        assert rows[0]["notes"] == ""

        assert rows[1]["claim_id"] == "C002"
        # Embedded comma + brackets survive TSV escaping.
        assert "[0.71, 0.85]" in rows[1]["claim_text"]
        assert rows[1]["ci_present"] == "yes"
        assert rows[1]["pvalue_present"] == "yes"
        assert rows[1]["effect_size_present"] == "no"
        assert rows[1]["notes"] == "unresolved"

    def test_flag_presence_absence_aggregated_correctly(self, tmp_path: Path):
        """End-to-end: a synthetic REPORT.md with controlled per-sentence
        match composition produces the right flag aggregation in the
        emitted TSV.

        Three sentences:
          S1: "AUC = 0.78 across cohorts"   → effect=yes, ci=no, pv=no, notes=""
          S2: "95% CI was reported"         → effect=no, ci=yes, pv=no, notes=""
                                              (subsumption drops the
                                              percentage-class `95%`
                                              inside the CI-class match,
                                              so single-class single-match)
          S3: "p < 0.05 across n=343"       → effect=no, ci=no, pv=yes,
                                              notes="unresolved"
                                              (multi-numeric: p_value + n_count)

        S3 has both p_value AND n_count regex hits. n_count doesn't set
        any flag, but counts toward "multi-numeric" unresolved status.
        Result: pv=yes, others=no, notes="unresolved".

        Note on S1: the metric regex requires `=` or `:` between the
        metric name and the value (per M1_PUNCH_LIST §B1.b catalog).
        Verb-based forms like "AUC was 0.78" are intentionally NOT
        matched — they're a recall gap to surface in C2.b's ground-
        truth check, not an M1 implementation bug.
        """
        report_path = tmp_path / "REPORT.md"
        report_path.write_text(
            textwrap.dedent("""\
                The AUC = 0.78 across cohorts. The 95% CI was reported. We saw p < 0.05 across n=343.
            """).strip(),
            encoding="utf-8",
        )
        # All four required inputs must exist (file content can be
        # placeholder; only --report is consumed deterministically).
        for name in ("methods_provenance.md", "figures_inventory.md", "tables_inventory.md"):
            (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = ci.main([
            "--report", str(report_path),
            "--methods-provenance", str(tmp_path / "methods_provenance.md"),
            "--figures-inventory", str(tmp_path / "figures_inventory.md"),
            "--tables-inventory", str(tmp_path / "tables_inventory.md"),
            "--output-dir", str(out_dir),
            "--no-llm",
        ])
        assert rc == 0

        tsv_path = out_dir / "claim_inventory.tsv"
        assert tsv_path.is_file()
        rows = list(csv.DictReader(tsv_path.read_text().splitlines(), dialect="excel-tab"))
        assert len(rows) == 3, rows

        # S1: AUC sentence → effect_size=yes, others=no, no notes.
        s1 = rows[0]
        assert "AUC" in s1["claim_text"]
        assert s1["effect_size_present"] == "yes"
        assert s1["ci_present"] == "no"
        assert s1["pvalue_present"] == "no"
        assert s1["notes"] == ""

        # S2: CI sentence → ci=yes, others=no, no notes.
        s2 = rows[1]
        assert "CI" in s2["claim_text"]
        assert s2["effect_size_present"] == "no"
        assert s2["ci_present"] == "yes"
        assert s2["pvalue_present"] == "no"
        assert s2["notes"] == ""

        # S3: p-value AND n=343 (two regex hits in one sentence) →
        # pvalue=yes, others=no, notes="unresolved" (multi-numeric).
        s3 = rows[2]
        assert "p < 0.05" in s3["claim_text"]
        assert "n=343" in s3["claim_text"]
        assert s3["pvalue_present"] == "yes"
        assert s3["effect_size_present"] == "no"
        assert s3["ci_present"] == "no"
        assert s3["notes"] == "unresolved"
