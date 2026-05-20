"""Tests for skill/tools/claim_inventory.py — Phase 0 NEW tool (M1).

Coverage (B1.a through B1.d shipped):

  - B2.a (6 tests) Regex extraction — one per pattern class.
  - B2.b (2 tests) Sentence segmentation.
  - B2.c (3 tests) LLM demarcation (mocked seam):
      - multi-numeric sentence split (1 input → N outputs)
      - span-2-sentences merge (multi-match input → 1 output)
      - ambiguous-pronoun resolution (multi-input → multi-output;
        substring narrowing per row)
  - B2.d (3 tests) Validator (exit 4 on schema violation):
      - fabricated source_notebook rejected (anti-fabrication)
      - non-substring claim_text rejected (anti-fabrication)
      - valid pass-through accepted
  - B2.e (2 tests) Idempotency cache:
      - identical-input rerun is byte-stable + zero LLM calls
      - input change invalidates cache → fresh LLM call
  - B2.f (2 tests) I/O contract.

The render-smoke discipline from feedback_render_test_must_evaluate_fstring
is exercised in B2.f's well-formed TSV test (it parses the actual
emitter output, not greps the source).
"""

from __future__ import annotations

import csv
import io
import json
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


# ===========================================================================
# Helpers for B2.c / B2.d / B2.e — common LLM-fixture + canned-call pattern.
# ===========================================================================

# A reusable methods_provenance.md context. Two notebook cites — the
# LLM picks per claim. Mirrors the shape extract_methods.py emits so
# the validator's substring check has realistic content to verify
# against.
_METHODS_PROVENANCE_FIXTURE = textwrap.dedent("""\
    # Methods Provenance

    ## Statistical Tests Detected

    ### ROC AUC

    - `sklearn.metrics.roc_auc_score` in **notebooks/04_classifier.ipynb** (cell 18, line 7)

    ### Mann-Whitney U test

    - `scipy.stats.mannwhitneyu` in **notebooks/05_subgroup.ipynb** (cell 12, line 14)
""")

_FIGURES_INVENTORY_FIXTURE = textwrap.dedent("""\
    # Figures Inventory

    ## Fig 3 — Classifier ROC Curves

    Description.
""")

_TABLES_INVENTORY_FIXTURE = textwrap.dedent("""\
    # Tables Inventory

    ## Tbl 2 — Per-cohort AUC Summary

    Description.
""")


def _canned_llm(response_text: str, cost_usd: float = 0.045):
    """Construct a fake demarcator_llm_call returning the given response
    text and cost. Asserts called exactly once per run (sanity check
    that we don't accidentally call the LLM twice in a single
    demarcate_unresolved_with_llm invocation)."""
    state = {"calls": 0}

    def _call(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
        state["calls"] += 1
        return response_text, cost_usd
    _call.calls = lambda: state["calls"]  # type: ignore[attr-defined]
    return _call


def _write_inventory_inputs(
    tmp_path: Path,
    *,
    report_text: str,
    methods_provenance_text: str = _METHODS_PROVENANCE_FIXTURE,
    figures_inventory_text: str = _FIGURES_INVENTORY_FIXTURE,
    tables_inventory_text: str = _TABLES_INVENTORY_FIXTURE,
) -> tuple[Path, Path, Path, Path, Path]:
    """Write all four input files plus the output dir. Returns
    (report, methods, figures, tables, out_dir) paths."""
    report_p = tmp_path / "REPORT.md"
    methods_p = tmp_path / "methods_provenance.md"
    figs_p = tmp_path / "figures_inventory.md"
    tbls_p = tmp_path / "tables_inventory.md"
    out_dir = tmp_path / "out"
    report_p.write_text(report_text, encoding="utf-8")
    methods_p.write_text(methods_provenance_text, encoding="utf-8")
    figs_p.write_text(figures_inventory_text, encoding="utf-8")
    tbls_p.write_text(tables_inventory_text, encoding="utf-8")
    return report_p, methods_p, figs_p, tbls_p, out_dir


def _run_main(
    tmp_path: Path,
    *,
    report_p: Path,
    methods_p: Path,
    figs_p: Path,
    tbls_p: Path,
    out_dir: Path,
    no_llm: bool = False,
) -> int:
    args = [
        "--report", str(report_p),
        "--methods-provenance", str(methods_p),
        "--figures-inventory", str(figs_p),
        "--tables-inventory", str(tbls_p),
        "--output-dir", str(out_dir),
    ]
    if no_llm:
        args.append("--no-llm")
    return ci.main(args)


# ===========================================================================
# B2.c — LLM demarcation (3 tests)
# ===========================================================================


class TestB2cLLMDemarcation:
    """The LLM seam at B1.c is monkeypatched via
    `claim_inventory.demarcator_llm_call`. Each test injects a canned
    JSON-array response and verifies the orchestrator's expansion
    logic (replace unresolved row → multiple rows; renumber claim_ids;
    recompute per-row flags from the demarcated claim_text).
    """

    def test_multi_numeric_sentence_split_yields_three_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """A single multi-numeric sentence (`AUC = 0.78 with 95% CI
        [0.71, 0.85] across n=343 conditions.`) lands as ONE
        unresolved candidate. The LLM emits 3 demarcated rows; the
        orchestrator replaces the unresolved row with 3 final rows
        and renumbers C001..C003. Per-row flags are recomputed from
        each demarcated claim_text — the AUC row gets
        effect_size=yes/ci=no/pv=no, the CI row gets ci=yes,
        the n-count row gets all=no."""
        report_text = (
            "We achieved AUC = 0.78 with 95% CI [0.71, 0.85] "
            "across n=343 conditions."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "Fig 3",
                "severity_justification": "Primary classifier metric.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "Fig 3",
                "severity_justification": "Bound on the AUC point estimate.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343 conditions",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Sample size.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0
        assert fake.calls() == 1  # type: ignore[attr-defined]

        tsv_path = out_dir / "claim_inventory.tsv"
        rows = list(csv.DictReader(
            tsv_path.read_text().splitlines(), dialect="excel-tab",
        ))
        # 3 demarcated rows replace the 1 unresolved candidate.
        assert len(rows) == 3, [r["claim_text"] for r in rows]
        assert [r["claim_id"] for r in rows] == ["C001", "C002", "C003"]
        # All three rows have the resolved notebook + cell.
        assert all(
            r["source_notebook"] == "notebooks/04_classifier.ipynb"
            for r in rows
        )
        assert all(r["source_cell"] == "18" for r in rows)
        # Per-row flag recomputation: AUC → effect_size=yes;
        # CI → ci_present=yes; n=343 → all flags=no.
        auc_row = rows[0]
        ci_row = rows[1]
        n_row = rows[2]
        assert auc_row["claim_text"] == "AUC = 0.78"
        assert auc_row["effect_size_present"] == "yes"
        assert auc_row["ci_present"] == "no"
        assert auc_row["pvalue_present"] == "no"
        assert ci_row["claim_text"] == "95% CI [0.71, 0.85]"
        assert ci_row["ci_present"] == "yes"
        assert ci_row["effect_size_present"] == "no"
        assert n_row["claim_text"] == "n=343 conditions"
        assert n_row["effect_size_present"] == "no"
        assert n_row["ci_present"] == "no"
        assert n_row["pvalue_present"] == "no"
        # All resolved → no `unresolved` notes anywhere.
        assert all(r["notes"] == "" for r in rows), rows
        # Audit line records the LLM cost + cache_hit=false.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 0
        assert record["inventory_size"] == 3
        assert record["unresolved_count"] == 0
        assert record["cost_usd"] == pytest.approx(0.045)
        assert record["cache_hit"] is False

    def test_span_two_numerics_merge_emits_single_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """A sentence flagged multi-numeric by the deterministic pre-
        pass that's actually one integrated claim (e.g.,
        `Fold change of 2.5 fold over the 1.0 fold control` — two
        ratio_with_unit matches semantically denoting the same
        comparison) gets MERGED by the LLM into a SINGLE demarcated
        row. The validator's coverage rule (≥1 row per input) accepts
        N=1 emission for multi-match input. Final TSV has one row, NOT
        two."""
        report_text = (
            "Fold change of 2.5 fold over the 1.0 fold control was "
            "observed."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "2.5 fold over the 1.0 fold control",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Single comparison; merged.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0
        assert fake.calls() == 1  # type: ignore[attr-defined]

        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert len(rows) == 1, [r["claim_text"] for r in rows]
        assert rows[0]["claim_id"] == "C001"
        assert rows[0]["claim_text"] == (
            "2.5 fold over the 1.0 fold control"
        )
        assert rows[0]["notes"] == ""
        # Audit reflects 1 final row, 0 unresolved.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["inventory_size"] == 1
        assert record["unresolved_count"] == 0

    def test_ambiguous_pronoun_substring_narrowing_across_two_inputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Two unresolved candidates from two different sentences. The
        LLM emits 2 rows for the first, 1 row for the second. Tests:
          - input_candidate_index ascending order across multi-input.
          - Per-row substring narrowing (claim_text is a sub-phrase of
            the source sentence, not the full sentence).
          - Source-notebook routing: rows for index 0 cite NB04 (AUC
            sentence); the row for index 1 cites NB05 (Mann-Whitney
            subgroup sentence).
        """
        report_text = (
            "AUC = 0.78 was achieved on the held-out cohort, and p < 0.05 "
            "in subgroup A.\n\n"
            "Mann-Whitney showed Z=2.5 with p = 0.001 across n=156 samples."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        # 2 unresolved candidates expected (one per sentence). LLM
        # demarcates: first sentence → AUC + p; second sentence → 1
        # merged row covering the whole Z + p + n claim (boilerplate-
        # rejection rule lets the LLM emit one row even on multi-numeric
        # input).
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "Fig 3",
                "severity_justification": "Primary metric.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "p < 0.05",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Subgroup A significance.",
            },
            {
                "input_candidate_index": 1,
                "claim_text": "Z=2.5 with p = 0.001 across n=156 samples",
                "source_notebook": "notebooks/05_subgroup.ipynb",
                "source_cell": "12",
                "figure_or_table": "",
                "severity_justification": "Mann-Whitney composite.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0

        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert len(rows) == 3, [r["claim_text"] for r in rows]
        # First two rows came from input 0 → both cite NB04.
        assert rows[0]["source_notebook"] == "notebooks/04_classifier.ipynb"
        assert rows[1]["source_notebook"] == "notebooks/04_classifier.ipynb"
        # Third row came from input 1 → cites NB05.
        assert rows[2]["source_notebook"] == "notebooks/05_subgroup.ipynb"
        # Substring narrowing: each claim_text is a sub-phrase of its
        # source sentence (not the whole sentence).
        assert rows[0]["claim_text"] == "AUC = 0.78"
        assert rows[1]["claim_text"] == "p < 0.05"
        assert rows[2]["claim_text"].startswith("Z=2.5")
        # Renumbering increments globally across the multi-input
        # expansion.
        assert [r["claim_id"] for r in rows] == ["C001", "C002", "C003"]


# ===========================================================================
# B2.d — Validator (exit 4 on schema violation)
# ===========================================================================


class TestB2dValidator:
    """Exit-4 contract: schema-violating LLM output is rejected by the
    validator. The audit line records exit_status=4. The cache is NOT
    written on validator failure (no successful demarcations to cache).
    """

    def test_fabricated_source_notebook_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits source_notebook='notebooks/99_FAKE.ipynb' that is
        NOT a substring of methods_provenance.md → validator raises
        ValidationError → main() returns 4. Anti-fabrication
        discipline: the holistic prompt downstream would otherwise
        ground on a non-existent notebook."""
        report_text = (
            "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/99_FAKE_NOT_IN_PROVENANCE.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Fabricated cite.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real cite.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real cite.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 4
        # Audit line records the rejection.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 4
        # Cache file should NOT exist — failed runs don't pollute the
        # cache.
        cache_path = out_dir / "audit" / "claim_inventory_cache.json"
        assert not cache_path.is_file()

    def test_non_substring_claim_text_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits claim_text that is NOT a contiguous substring of
        the input sentence_text → validator raises → main() returns 4.
        Anti-fabrication: the holistic prompt would otherwise ground
        on a paraphrased number that the source doesn't contain."""
        report_text = (
            "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC was 0.823",  # NOT a substring; fabricated.
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Fabricated number.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 4

    def test_valid_pass_through_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """A schema-compliant LLM response passes the validator → exit 0
        + TSV written. Pass-through baseline."""
        report_text = (
            "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "Fig 3",
                "severity_justification": "Primary metric.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "Fig 3",
                "severity_justification": "Bound on AUC.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Sample size.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0
        assert (out_dir / "claim_inventory.tsv").is_file()


# ===========================================================================
# B2.e — Idempotency cache
# ===========================================================================


class TestB2eIdempotency:
    """Cache: SHA-256 over (report_sha, methods_sha, figures_sha,
    tables_sha, prompt_sha, parser_VERSION). On hit, the LLM is
    skipped, demarcations are re-validated, and the same TSV is
    re-emitted byte-identical. Any input change OR prompt change OR
    parser bump invalidates."""

    def test_identical_input_rerun_is_byte_stable_and_zero_llm_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Run twice on identical inputs: first run calls the LLM;
        second run hits cache, makes zero LLM calls, writes byte-
        identical TSV. Audit JSONL records both runs (cache_hit=false
        then cache_hit=true)."""
        report_text = (
            "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Primary.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Bound.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Sample size.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc1 = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc1 == 0
        first_tsv = (out_dir / "claim_inventory.tsv").read_bytes()
        assert fake.calls() == 1  # type: ignore[attr-defined]

        # Second run: cache hit, LLM should NOT be called again.
        rc2 = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc2 == 0
        second_tsv = (out_dir / "claim_inventory.tsv").read_bytes()
        assert second_tsv == first_tsv, (
            "rerun should be byte-identical to first run"
        )
        assert fake.calls() == 1, (  # type: ignore[attr-defined]
            "second run should hit cache; LLM should not be called"
        )

        # Audit confirms cache_hit=false then cache_hit=true.
        audit_lines = (
            out_dir / "audit" / "phase0.jsonl"
        ).read_text().splitlines()
        assert len(audit_lines) == 2
        rec1 = json.loads(audit_lines[0])
        rec2 = json.loads(audit_lines[1])
        assert rec1["cache_hit"] is False
        assert rec2["cache_hit"] is True
        # cost_usd on hit is 0.0 (no fresh bill).
        assert rec2["cost_usd"] == 0.0

    def test_input_change_invalidates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Mutating any input file (REPORT.md here) between runs
        invalidates the cache and forces a fresh LLM call. The
        six-tuple cache key includes report_sha → SHA change → key
        mismatch → cache miss."""
        report_text_v1 = (
            "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text_v1,
        )
        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Primary.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Bound.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Sample size.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        # First run: caches.
        rc1 = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc1 == 0
        assert fake.calls() == 1  # type: ignore[attr-defined]

        # Mutate REPORT.md → SHA changes → cache key mismatch on
        # second run. We need a new canned response that demarcates
        # the new sentence (the previous response's claim_texts won't
        # be substrings of the new REPORT). Repoint the LLM seam to a
        # response matching the new content.
        report_text_v2 = (
            "AUC = 0.92 with 95% CI [0.85, 0.99] across n=500."
        )
        report_p.write_text(report_text_v2, encoding="utf-8")
        canned_v2 = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.92",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Primary.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.85, 0.99]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Bound.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=500",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Sample size.",
            },
        ])
        fake2 = _canned_llm(canned_v2)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake2)

        rc2 = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc2 == 0
        # Fresh LLM call confirms cache miss.
        assert fake2.calls() == 1, (  # type: ignore[attr-defined]
            "input change should invalidate cache → fresh LLM call"
        )

        # Audit's second-run line shows cache_hit=false (cache miss).
        audit_lines = (
            out_dir / "audit" / "phase0.jsonl"
        ).read_text().splitlines()
        assert len(audit_lines) == 2
        rec2 = json.loads(audit_lines[-1])
        assert rec2["cache_hit"] is False
        # The new TSV reflects the new content.
        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert any("0.92" in r["claim_text"] for r in rows), rows


# ===========================================================================
# B1.e — Validator project_root fallback + regex catalog extension (D-036)
# ===========================================================================
#
# Two test classes:
#   TestB1eValidatorProjectRoot  — disk-fallback path (2 tests).
#   TestB1eRegexCatalogD036      — 7 catalog changes (7 tests, one per
#                                    pattern/extension).
#
# Filed 2026-05-07 after live-LLM smoke on `ibd_phage_targeting`
# revealed that (a) the validator's substring-only check rejected real
# notebooks not in methods_provenance.md and (b) the regex catalog
# missed 21 of 48 ground-truth patterns. See
# `smoke-test/M1_PUNCH_LIST_claim_groundtruth.md` for the data and
# `DECISIONS.md` D-036 for the architectural decision.


class TestB1eValidatorProjectRoot:
    """Validator now accepts source_notebook if EITHER (a) substring of
    methods_provenance.md (legacy path) OR (b) the path resolves to an
    existing file under project_root. This test class exercises (b)
    independently of (a)."""

    def test_real_notebook_on_disk_but_not_in_provenance_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """The bug fixture from live-LLM smoke: NB10a_kumbhari_strain_
        adaptation.ipynb exists under <project_root>/notebooks/ but
        does NOT appear in methods_provenance.md (extract_methods.py
        only catalogs notebooks with AST-detected stat tests). The
        B1.e validator accepts via the disk fallback."""
        # Build a project_root with a real notebook on disk, but a
        # methods_provenance.md that DOES NOT mention it.
        project_root = tmp_path / "project"
        (project_root / "notebooks").mkdir(parents=True)
        nb_path = project_root / "notebooks" / "NB10a_kumbhari_strain_adaptation.ipynb"
        nb_path.write_text("not actually nbformat — exists is what matters", encoding="utf-8")

        report_text = "Strain adaptation OR=1.38 with p=2.4e-6 on n=343 strains."
        report_p = tmp_path / "REPORT.md"
        # methods_provenance.md INTENTIONALLY does NOT list NB10a.
        methods_p = tmp_path / "methods_provenance.md"
        figs_p = tmp_path / "figures_inventory.md"
        tbls_p = tmp_path / "tables_inventory.md"
        out_dir = tmp_path / "out"
        report_p.write_text(report_text, encoding="utf-8")
        methods_p.write_text(_METHODS_PROVENANCE_FIXTURE, encoding="utf-8")
        figs_p.write_text(_FIGURES_INVENTORY_FIXTURE, encoding="utf-8")
        tbls_p.write_text(_TABLES_INVENTORY_FIXTURE, encoding="utf-8")

        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "OR=1.38",
                # Cite a notebook NOT in methods_provenance.md but
                # present on disk under project_root/notebooks/.
                "source_notebook": "notebooks/NB10a_kumbhari_strain_adaptation.ipynb",
                "source_cell": "5",
                "figure_or_table": "",
                "severity_justification": "Effect size for H3b.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "p=2.4e-6",
                "source_notebook": "notebooks/NB10a_kumbhari_strain_adaptation.ipynb",
                "source_cell": "5",
                "figure_or_table": "",
                "severity_justification": "Significance level for H3b.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/NB10a_kumbhari_strain_adaptation.ipynb",
                "source_cell": "5",
                "figure_or_table": "",
                "severity_justification": "Sample size for H3b.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = ci.main([
            "--report", str(report_p),
            "--methods-provenance", str(methods_p),
            "--figures-inventory", str(figs_p),
            "--tables-inventory", str(tbls_p),
            "--output-dir", str(out_dir),
            "--project-root", str(project_root),
        ])
        assert rc == 0, "validator should accept real-on-disk notebook via disk fallback"
        assert (out_dir / "claim_inventory.tsv").is_file()

    def test_nonexistent_notebook_still_rejected_with_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """The disk-fallback ONLY accepts files that actually exist.
        A path under project_root that doesn't resolve to a file is
        still rejected (anti-fabrication discipline preserved)."""
        project_root = tmp_path / "project"
        (project_root / "notebooks").mkdir(parents=True)
        # Notebook deliberately NOT created on disk.

        report_text = "AUC = 0.78 with 95% CI [0.71, 0.85] across n=343."
        report_p = tmp_path / "REPORT.md"
        methods_p = tmp_path / "methods_provenance.md"
        figs_p = tmp_path / "figures_inventory.md"
        tbls_p = tmp_path / "tables_inventory.md"
        out_dir = tmp_path / "out"
        report_p.write_text(report_text, encoding="utf-8")
        methods_p.write_text(_METHODS_PROVENANCE_FIXTURE, encoding="utf-8")
        figs_p.write_text(_FIGURES_INVENTORY_FIXTURE, encoding="utf-8")
        tbls_p.write_text(_TABLES_INVENTORY_FIXTURE, encoding="utf-8")

        canned = json.dumps([
            {
                "input_candidate_index": 0,
                "claim_text": "AUC = 0.78",
                # Path doesn't exist on disk AND not in methods_provenance.
                "source_notebook": "notebooks/99_GHOST_NOTEBOOK.ipynb",
                "source_cell": "1",
                "figure_or_table": "",
                "severity_justification": "Fabricated.",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "95% CI [0.71, 0.85]",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real (in fixture).",
            },
            {
                "input_candidate_index": 0,
                "claim_text": "n=343",
                "source_notebook": "notebooks/04_classifier.ipynb",
                "source_cell": "18",
                "figure_or_table": "",
                "severity_justification": "Real.",
            },
        ])
        fake = _canned_llm(canned)
        monkeypatch.setattr(ci, "demarcator_llm_call", fake)

        rc = ci.main([
            "--report", str(report_p),
            "--methods-provenance", str(methods_p),
            "--figures-inventory", str(figs_p),
            "--tables-inventory", str(tbls_p),
            "--output-dir", str(out_dir),
            "--project-root", str(project_root),
        ])
        # Still rejected (exit 4) — disk fallback doesn't relax the
        # anti-fabrication contract; it just broadens the ground-truth
        # source from one file to a directory tree.
        assert rc == 4
        # Audit line records billed cost honestly (B1.e closed the
        # cost_usd=0.0-on-exit-4 gap; the canned LLM billed $0.045).
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 4
        assert record["cost_usd"] == pytest.approx(0.045), (
            "B1.e: validator-rejection path must record actual billed "
            "cost, not 0.0"
        )


class TestB1eRegexCatalogD036:
    """One test per D-036 catalog change. Each asserts the regex matches
    the post-patch form AND that match_class is correctly assigned. See
    SPEC §4.6 + DECISIONS.md D-036."""

    def test_percentage_with_space_now_matched(self):
        """REPORT.md uses `95 %` (space form) throughout. Pre-B1.e the
        regex required no-space and missed 9 of 12 percentage patterns
        on ibd_phage_targeting. Post-B1.e the `\\s{0,2}` allows the
        space."""
        text = "Coverage hit 95 % of 188 strains; 88.2% no-space form also fires."
        matches = ci.extract_numeric_matches(text)
        pct_matches = [m for m in matches if m.match_class == "percentage"]
        matched_texts = [m.matched_text for m in pct_matches]
        assert any("95 %" in t for t in matched_texts), matched_texts
        # No-space form preserved.
        assert any("88.2%" in t for t in matched_texts), matched_texts

    def test_p_value_dotless_scientific_notation_now_matched(self):
        """REPORT.md emits both `p=2.4e-6` (with dot) and `p=7e-17` /
        `p<1e-31` (no dot in mantissa). Pre-B1.e the second branch
        required `\\d+\\.\\d+e-?\\d+` (dot mandatory) and missed
        dot-less forms. Post-B1.e the dot is optional and `<` joins
        `=` as a valid operator on the sci-notation branch."""
        text = (
            "Significance held at p=7e-17 across 4 cohorts. "
            "Compounded effect: ebf/ecf RPKM CD-up across 4 cohorts at p<1e-31. "
            "Decimal flavor still works: p=2.4e-6 from NB10a."
        )
        matches = ci.extract_numeric_matches(text)
        p_matches = [m for m in matches if m.match_class == "p_value"]
        matched_texts = [m.matched_text for m in p_matches]
        assert any("p=7e-17" in t for t in matched_texts), matched_texts
        assert any("p<1e-31" in t for t in matched_texts), matched_texts
        # Pre-existing dotted form still matches.
        assert any("p=2.4e-6" in t for t in matched_texts), matched_texts

    def test_p_value_unicode_le_operator_now_matched(self):
        """`≤` (U+2264) and `≥` (U+2265) join the operator class.
        BERIL Methods sections use them interchangeably with `<=`/`>=`."""
        text = "Effect size constraint p ≤ 0.05 in the primary arm; p ≥ 0.30 control."
        matches = ci.extract_numeric_matches(text)
        p_matches = [m for m in matches if m.match_class == "p_value"]
        matched_texts = [m.matched_text for m in p_matches]
        assert any("p ≤ 0.05" in t for t in matched_texts), matched_texts
        assert any("p ≥ 0.30" in t for t in matched_texts), matched_texts

    def test_correlation_class_pearson_and_spearman(self):
        """New class `correlation` matches `r = ...`, `ρ = ...` with
        optional sign + Unicode minus (U+2212)."""
        text = (
            "Cross-modality structure: r = 0.96 on CC1. "
            "Tier-A reseq replicates: ρ = 1.000 on patient 1112. "
            "Harvard subset r=+0.456 with M. gnavus. "
            "Franzosa cliff direction r=−0.747 on urobilin."
        )
        matches = ci.extract_numeric_matches(text)
        corr_matches = [m for m in matches if m.match_class == "correlation"]
        matched_texts = [m.matched_text for m in corr_matches]
        assert any("r = 0.96" in t for t in matched_texts), matched_texts
        assert any("ρ = 1.000" in t for t in matched_texts), matched_texts
        assert any("r=+0.456" in t for t in matched_texts), matched_texts
        assert any("r=−0.747" in t for t in matched_texts), matched_texts
        # Class sets the effect_size_present flag (per _CLASS_TO_FLAG).
        flags = ci._flags_from_classes(["correlation"])
        assert flags == ("yes", "no", "no")

    def test_odds_ratio_class(self):
        """New class `odds_ratio` matches `OR=1.38`, `OR = 8.1`. Decimal
        optional; word boundary blocks mid-token false positives."""
        text = (
            "Iron acquisition OR=44.4 (FDR 6e-56). "
            "Strain adaptation OR=1.38 (housekeeping OR=0.62 depleted). "
            "Theme-level OR = 8.1 dominant."
        )
        matches = ci.extract_numeric_matches(text)
        or_matches = [m for m in matches if m.match_class == "odds_ratio"]
        matched_texts = [m.matched_text for m in or_matches]
        assert any("OR=44.4" in t for t in matched_texts), matched_texts
        assert any("OR=1.38" in t for t in matched_texts), matched_texts
        assert any("OR=0.62" in t for t in matched_texts), matched_texts
        assert any("OR = 8.1" in t for t in matched_texts), matched_texts
        # Mid-token "factOR=2" must NOT match (word boundary).
        text_neg = "The factOR=2 baseline."
        matches_neg = ci.extract_numeric_matches(text_neg)
        or_neg = [m for m in matches_neg if m.match_class == "odds_ratio"]
        assert or_neg == [], f"word boundary should block 'factOR=2': {or_neg}"

    def test_log_fc_class(self):
        """New class `log_fc` matches `log₂FC +2.67` (subscript ₂ =
        U+2082), `log2FC +5.66` (ASCII), with optional sign."""
        text = (
            "C. scindens log₂FC +5.66 in raw analysis. "
            "Pooled finding log₂FC +2.67 in preliminary report. "
            "ASCII form: log2FC -1.42 sanity-check."
        )
        matches = ci.extract_numeric_matches(text)
        fc_matches = [m for m in matches if m.match_class == "log_fc"]
        matched_texts = [m.matched_text for m in fc_matches]
        assert any("log₂FC +5.66" in t for t in matched_texts), matched_texts
        assert any("log₂FC +2.67" in t for t in matched_texts), matched_texts
        assert any("log2FC -1.42" in t for t in matched_texts), matched_texts

    def test_count_of_and_cliff_delta_classes(self):
        """Two compact classes:
          - `count_of` matches `M of N` and `M / N` forms with comma
            separators in M and N.
          - `cliff_delta` matches `cliff δ = +0.50` and the no-glyph
            short form `cliff = -0.358`."""
        text = (
            "14 of 23 patients receive cocktail drafts. "
            "3,929 / 17,672 phage-strain pairs susceptible. "
            "45 / 51 candidates replicate. "
            "Composite axis: cliff δ = +0.50 (CD vs nonIBD). "
            "Urobilin: cliff δ=−0.358 across cohorts."
        )
        matches = ci.extract_numeric_matches(text)
        count_matches = [m for m in matches if m.match_class == "count_of"]
        cliff_matches = [m for m in matches if m.match_class == "cliff_delta"]
        count_texts = [m.matched_text for m in count_matches]
        cliff_texts = [m.matched_text for m in cliff_matches]
        # count_of forms.
        assert any("14 of 23" in t for t in count_texts), count_texts
        assert any("3,929 / 17,672" in t for t in count_texts), count_texts
        assert any("45 / 51" in t for t in count_texts), count_texts
        # cliff_delta forms.
        assert any("cliff δ = +0.50" in t for t in cliff_texts), cliff_texts
        assert any("cliff δ=−0.358" in t for t in cliff_texts), cliff_texts


# ===========================================================================
# B1.f — Demarcator batching (D-038)
# ===========================================================================
#
# Filed 2026-05-07 after live-LLM smoke on `ibd_phage_targeting`
# revealed that 133 unresolved candidates in a single LLM call hits
# two failure modes (output truncation; subprocess 180s timeout). The
# fix chunks unresolved_candidates into batches of `batch_size`
# (default 15), calls the LLM N times, offsets indices, sums costs,
# then validates full coverage.


class TestB1fDemarcatorBatching:
    """Two tests:
      - batching covers all candidates, costs are summed, indices are
        offset correctly back to absolute positions.
      - batch_size mixed into cache key means changing it invalidates
        a hit.
    """

    def test_batched_demarcation_covers_all_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Synthetic REPORT.md with 6 multi-numeric sentences →
        deterministic pre-pass surfaces 6 unresolved candidates. With
        batch_size=2, demarcate_unresolved_with_llm makes 3 LLM calls,
        each returning local indices [0,1]. Final demarcation list has
        all 6 absolute indices [0..5], cost is summed (3 × $0.045 =
        $0.135), and coverage validation passes.
        """
        # Six multi-numeric sentences, each with two numerics that
        # B1.b's regex catalog identifies as multi-class so they land
        # as `notes='unresolved'`. AUC + CI is the canonical
        # multi-numeric pattern from the existing test suite.
        report_text = (
            "S1: AUC = 0.78 with 95% CI [0.71, 0.85]. "
            "S2: AUC = 0.82 with 95% CI [0.74, 0.90]. "
            "S3: AUC = 0.65 with 95% CI [0.58, 0.72]. "
            "S4: AUC = 0.91 with 95% CI [0.85, 0.97]. "
            "S5: AUC = 0.55 with 95% CI [0.48, 0.62]. "
            "S6: AUC = 0.88 with 95% CI [0.82, 0.94]."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )

        # Verify the deterministic pre-pass is producing 6 unresolved
        # candidates (sanity check on the test fixture itself).
        result_no_llm = ci.run_inventory(
            report_text=report_text,
            no_llm=True,
        )
        unresolved = [c for c in result_no_llm.candidates if c.notes == "unresolved"]
        assert len(unresolved) == 6, (
            f"test fixture should produce 6 unresolved candidates; got {len(unresolved)}"
        )

        # Build a per-batch-aware fake LLM. Each call returns a JSON
        # array with local indices [0..N) covering whatever batch was
        # passed. We track call count to assert batching happened.
        call_log = []

        def fake_call(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
            # Parse N from the user prompt header to size the response.
            # The build_demarcator_user_prompt's first line reads:
            #   "You will demarcate N=<num> multi-numeric sentence(s) ..."
            import re as _re
            m = _re.search(r"N=(\d+)", usr_p)
            assert m is not None, f"unexpected prompt shape: {usr_p[:200]!r}"
            n = int(m.group(1))
            # For each batch-local index, emit two demarcation rows
            # (the AUC + the CI) — both citing the synthetic notebook
            # in the methods_provenance fixture.
            rows = []
            for i in range(n):
                rows.append({
                    "input_candidate_index": i,
                    "claim_text": f"AUC = 0.{70 + i}",  # placeholder substring
                    "source_notebook": "notebooks/04_classifier.ipynb",
                    "source_cell": "18",
                    "figure_or_table": "",
                    "severity_justification": "Primary metric.",
                })
                rows.append({
                    "input_candidate_index": i,
                    "claim_text": f"95% CI [0.{60 + i}, 0.{80 + i}]",
                    "source_notebook": "notebooks/04_classifier.ipynb",
                    "source_cell": "18",
                    "figure_or_table": "",
                    "severity_justification": "Bound.",
                })
            response = json.dumps(rows)
            call_log.append({"n": n, "response": response})
            return response, 0.045

        # Run with batch_size=2 → expect 3 LLM calls.
        # NOTE: the fake's claim_text values are crafted to be
        # substrings of each respective input sentence. Since B1.b
        # validator requires substring of input sentence, we craft per
        # actual S1..S6 content.
        # Adjust the fake to read from the prompt's actual sentence_text
        # entries — easier than per-test brittle substrings.
        call_log.clear()

        def smarter_fake(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
            import re as _re
            # Extract the sentences the user prompt is asking us about.
            # Each input is rendered as: [i] sentence_text: '...'
            sentence_lines = _re.findall(r"\[(\d+)\] sentence_text: '([^']+)'", usr_p)
            rows = []
            for idx_str, sentence in sentence_lines:
                idx = int(idx_str)
                # Find an AUC substring + a CI substring within this sentence.
                auc_m = _re.search(r"AUC = 0\.\d+", sentence)
                ci_m = _re.search(r"95% CI \[0\.\d+, 0\.\d+\]", sentence)
                if auc_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": auc_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Primary metric.",
                    })
                if ci_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": ci_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Bound.",
                    })
            response = json.dumps(rows)
            call_log.append({"n_inputs": len(sentence_lines)})
            return response, 0.045

        monkeypatch.setattr(ci, "demarcator_llm_call", smarter_fake)

        rc = ci.main([
            "--report", str(report_p),
            "--methods-provenance", str(methods_p),
            "--figures-inventory", str(figs_p),
            "--tables-inventory", str(tbls_p),
            "--output-dir", str(out_dir),
            "--batch-size", "2",
        ])
        assert rc == 0, "batched run should pass full validation"
        # 6 candidates / batch_size 2 → 3 LLM calls.
        assert len(call_log) == 3, f"expected 3 batches; got {len(call_log)}"
        for entry in call_log:
            assert entry["n_inputs"] == 2, f"each batch should be size 2; {entry}"
        # Cost is summed: 3 × $0.045 = $0.135.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["cost_usd"] == pytest.approx(3 * 0.045)
        assert record["exit_status"] == 0
        # Inventory should have 12 rows (6 × 2 demarcations each).
        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert len(rows) == 12, f"expected 12 demarcated rows; got {len(rows)}"

    def test_batch_size_invalidates_cache(self):
        """Per feedback_cache_key_chunked_only_when_chunked, batch_size
        is in the cache key only when explicitly set. Same inputs +
        different batch_size → different cache key → cache miss on
        rerun. Same inputs + None batch_size → byte-identical to
        legacy 6-tuple key (backwards compat with B1.b cache files)."""
        legacy_key = ci.compute_cache_key(
            report_sha="r" * 64,
            methods_provenance_sha="m" * 64,
            figures_inventory_sha="f" * 64,
            tables_inventory_sha="t" * 64,
            prompt_sha="p" * 64,
            parser_version="0.8.0-m1-B1.e",
        )
        # Same inputs, batch_size=None → identical to legacy.
        none_key = ci.compute_cache_key(
            report_sha="r" * 64,
            methods_provenance_sha="m" * 64,
            figures_inventory_sha="f" * 64,
            tables_inventory_sha="t" * 64,
            prompt_sha="p" * 64,
            parser_version="0.8.0-m1-B1.e",
            batch_size=None,
        )
        assert legacy_key == none_key, (
            "batch_size=None must produce byte-identical keys to legacy 6-tuple"
        )
        # Same inputs, batch_size=15 → different key.
        bs15_key = ci.compute_cache_key(
            report_sha="r" * 64,
            methods_provenance_sha="m" * 64,
            figures_inventory_sha="f" * 64,
            tables_inventory_sha="t" * 64,
            prompt_sha="p" * 64,
            parser_version="0.8.0-m1-B1.e",
            batch_size=15,
        )
        assert bs15_key != legacy_key
        # Different batch_sizes → different keys.
        bs10_key = ci.compute_cache_key(
            report_sha="r" * 64,
            methods_provenance_sha="m" * 64,
            figures_inventory_sha="f" * 64,
            tables_inventory_sha="t" * 64,
            prompt_sha="p" * 64,
            parser_version="0.8.0-m1-B1.e",
            batch_size=10,
        )
        assert bs10_key != bs15_key, (
            "different batch_sizes must produce different cache keys"
        )


# ===========================================================================
# B1.g — Bounded retry on missing indices + tolerated_missing fallback (D-039)
# ===========================================================================
#
# Filed 2026-05-07 after live-LLM smoke on `ibd_phage_targeting`
# revealed that even with B1.f batching the demarcator
# non-deterministically drops 1–3 input candidates per dense-project
# run (different indices each run). Fix: bounded retry on missing
# indices, with `tolerated_missing` carried through to the
# orchestrator's existing pass-through fallback.


class TestB1gRetryOnMissingIndices:
    """Two tests:
      - retry round recovers an LLM-dropped index → exit 0, full TSV.
      - residual missing after max_retries falls through to original
        unresolved row, exit 0, TSV has the unresolved row preserved
        + tolerated_missing recorded in the result.
    """

    def test_retry_recovers_dropped_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM drops one of 3 candidates on the initial pass; retry
        round 1 recovers it; result has all 3 covered, exit 0."""
        report_text = (
            "S1: AUC = 0.78 with 95% CI [0.71, 0.85]. "
            "S2: AUC = 0.82 with 95% CI [0.74, 0.90]. "
            "S3: AUC = 0.91 with 95% CI [0.85, 0.97]."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )

        # Stateful fake: first call drops the LAST input (returns N-1
        # rows for a batch of N); subsequent calls cover everything
        # the user prompt asked for.
        state = {"call": 0}

        def staged_fake(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
            import re as _re
            state["call"] += 1
            sentence_lines = _re.findall(
                r"\[(\d+)\] sentence_text: '([^']+)'", usr_p
            )
            rows = []
            # On the FIRST call only, drop the last input. Subsequent
            # calls cover every input the prompt asked for (including
            # the retry batch which is just the dropped one).
            if state["call"] == 1 and len(sentence_lines) > 1:
                sentence_lines = sentence_lines[:-1]
            for idx_str, sentence in sentence_lines:
                idx = int(idx_str)
                auc_m = _re.search(r"AUC = 0\.\d+", sentence)
                ci_m = _re.search(r"95% CI \[0\.\d+, 0\.\d+\]", sentence)
                if auc_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": auc_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Primary metric.",
                    })
                if ci_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": ci_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Bound.",
                    })
            return json.dumps(rows), 0.045

        monkeypatch.setattr(ci, "demarcator_llm_call", staged_fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0, "retry should recover the dropped index"
        # Two LLM calls: initial (drops one) + retry round 1 (recovers).
        assert state["call"] == 2, f"expected 2 LLM calls; got {state['call']}"
        # TSV has 6 rows (3 candidates × 2 demarcations).
        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert len(rows) == 6, f"expected 6 rows; got {len(rows)}"
        # All claim_ids present, no `unresolved` notes.
        assert all(r["notes"] != "unresolved" for r in rows), rows
        # Cost summed: 2 × $0.045 = $0.09.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["cost_usd"] == pytest.approx(2 * 0.045)
        assert record["exit_status"] == 0

    def test_residual_missing_falls_through_to_unresolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM persistently drops the same index across initial + 3
        retries; orchestrator preserves the original notes='unresolved'
        row for that index and exits 0. The B1.g fallback unblocks
        live runs from intrinsic LLM variance without abandoning the
        candidate."""
        report_text = (
            "S1: AUC = 0.78 with 95% CI [0.71, 0.85]. "
            "S2: AUC = 0.82 with 95% CI [0.74, 0.90]."
        )
        report_p, methods_p, figs_p, tbls_p, out_dir = _write_inventory_inputs(
            tmp_path, report_text=report_text,
        )

        # Always drop the LAST sentence in whatever batch is sent.
        # On retry, the retry batch contains only the dropped index,
        # which is still "the last", so it's dropped again — this
        # exercises the max_retries-exhausted fallback path.
        # However, on the SECOND retry's batch (size 1), the prompt
        # has only one input — to ensure persistent drop without
        # ever covering it, we drop ALL inputs when the batch is
        # exactly the persistent-miss target.
        TARGET_INDEX = 1  # absolute index that always gets dropped

        def persistent_drop_fake(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
            import re as _re
            sentence_lines = _re.findall(
                r"\[(\d+)\] sentence_text: '([^']+)'", usr_p
            )
            # The user prompt uses local indices [0..N) where N=len(batch);
            # the absolute index is hidden from us. We use the sentence
            # CONTENT to identify the target: "S2:" is sentence #1
            # (zero-indexed). Drop any local row whose sentence starts
            # with "S2:".
            rows = []
            for idx_str, sentence in sentence_lines:
                idx = int(idx_str)
                if sentence.startswith("S2"):
                    continue  # persistent drop
                auc_m = _re.search(r"AUC = 0\.\d+", sentence)
                ci_m = _re.search(r"95% CI \[0\.\d+, 0\.\d+\]", sentence)
                if auc_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": auc_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Primary metric.",
                    })
                if ci_m:
                    rows.append({
                        "input_candidate_index": idx,
                        "claim_text": ci_m.group(0),
                        "source_notebook": "notebooks/04_classifier.ipynb",
                        "source_cell": "18",
                        "figure_or_table": "",
                        "severity_justification": "Bound.",
                    })
            return json.dumps(rows), 0.045

        monkeypatch.setattr(ci, "demarcator_llm_call", persistent_drop_fake)

        rc = _run_main(
            tmp_path,
            report_p=report_p, methods_p=methods_p,
            figs_p=figs_p, tbls_p=tbls_p, out_dir=out_dir,
        )
        assert rc == 0, (
            "residual missing should fall through to unresolved row, "
            f"not exit 4; got {rc}"
        )
        # TSV: S1 demarcates to 2 rows (AUC + CI) + S2 stays as 1 unresolved row = 3 rows.
        rows = list(csv.DictReader(
            (out_dir / "claim_inventory.tsv").read_text().splitlines(),
            dialect="excel-tab",
        ))
        assert len(rows) == 3, f"expected 3 rows; got {len(rows)}: {rows}"
        # Exactly one row carries `notes='unresolved'` (the S2 fallback).
        unresolved = [r for r in rows if r["notes"] == "unresolved"]
        assert len(unresolved) == 1, f"expected 1 unresolved row; got: {unresolved}"
        assert "S2" in unresolved[0]["claim_text"]
        # Audit reports cost across all retry rounds.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        # 1 initial + 3 retries = 4 LLM calls × $0.045 = $0.18.
        assert record["cost_usd"] == pytest.approx(4 * 0.045)
        assert record["exit_status"] == 0
        # Cache file persists tolerated_missing for byte-stable rerun.
        cache_path = out_dir / "audit" / "claim_inventory_cache.json"
        assert cache_path.is_file()
        cache_data = json.loads(cache_path.read_text())
        # Single entry; tolerated_missing should record the residual.
        only_payload = next(iter(cache_data.values()))
        assert only_payload.get("tolerated_missing") == [TARGET_INDEX], (
            f"cache should persist tolerated_missing; got {only_payload}"
        )


# ===========================================================================
# B1.h — Allowlist extraction + user-prompt menu (D-040)
# ===========================================================================
#
# Filed 2026-05-07 after live-LLM smoke on `ibd_phage_targeting`
# revealed that the demarcator was fabricating cites despite anti-
# fabrication prompt rules:
#   - "notebooks/NB07a_H3a_falsifiability.ipynb" instead of the real
#     "notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb"
#   - "Fig NB15" (treating a notebook ID as a figure label)
# The fix puts an explicit allowlist near the top of the user prompt
# so the LLM has a copy-from menu rather than paraphrasing.


class TestB1hAllowlistsInUserPrompt:
    """Two tests:
      - extractors pull the right notebook paths and figure/table
        labels from realistic provenance/inventory text.
      - build_demarcator_user_prompt emits the allowlist sections in
        the right order with the right anti-pattern reminders.
    """

    def test_extractors_pull_notebooks_and_labels(self, tmp_path: Path):
        """`_extract_notebook_paths` returns sorted unique paths,
        merging methods_provenance mentions with disk scan when
        project_root is provided. `_extract_figure_or_table_labels`
        handles short (`Fig N`), path (`figures/X.png`), and id
        (`report_tbl_NN`) heading forms."""
        # methods_provenance lists 2 notebooks; disk has 4 (2 missing
        # from provenance, modelling the ibd_phage_targeting reality
        # where the AST extractor catalogs only ~40% of disk notebooks).
        methods_text = textwrap.dedent("""\
            # Methods Provenance

            ## Statistical Tests Detected

            ### Mann-Whitney U test

            - `scipy.stats.mannwhitneyu` in **notebooks/NB04_within_ecotype_DA.ipynb** (cell 4, line 27)
            - duplicate-text-only should dedupe: notebooks/NB04_within_ecotype_DA.ipynb again.

            ### Gradient boosting classifier

            - `sklearn.ensemble.GradientBoostingClassifier` in **notebooks/NB03_clinical_ecotype_classifier.ipynb** (cell 3)
        """)
        # Build a real project_root with 4 notebooks on disk.
        project_root = tmp_path / "project"
        (project_root / "notebooks").mkdir(parents=True)
        for name in (
            "NB03_clinical_ecotype_classifier.ipynb",     # in provenance
            "NB04_within_ecotype_DA.ipynb",                # in provenance
            "NB07a_pathway_DA_H3a_falsifiability.ipynb",   # disk-only
            "NB10a_kumbhari_strain_adaptation.ipynb",      # disk-only
        ):
            (project_root / "notebooks" / name).write_text("nb stub", encoding="utf-8")

        # Without project_root: only methods_provenance mentions.
        nb_allow_no_disk = ci._extract_notebook_paths(methods_text)
        assert nb_allow_no_disk == [
            "notebooks/NB03_clinical_ecotype_classifier.ipynb",
            "notebooks/NB04_within_ecotype_DA.ipynb",
        ], nb_allow_no_disk

        # With project_root: union with disk scan picks up disk-only
        # notebooks too — the bug fixture from live-LLM smoke.
        nb_allow_with_disk = ci._extract_notebook_paths(
            methods_text, project_root=project_root,
        )
        assert nb_allow_with_disk == [
            "notebooks/NB03_clinical_ecotype_classifier.ipynb",
            "notebooks/NB04_within_ecotype_DA.ipynb",
            "notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb",
            "notebooks/NB10a_kumbhari_strain_adaptation.ipynb",
        ], nb_allow_with_disk

        # Three label formats — all should be picked up.
        figures_text = textwrap.dedent("""\
            # Figures Inventory

            ## Fig 1 — Cohort overview

            ## Figures

            ### `figures/NB00_cohort_summary.png`

            ### figures/NB01_ecotype_by_diagnosis.png
        """)
        tables_text = textwrap.dedent("""\
            # Tables Inventory

            ## Tables

            ### report_tbl_01 — H3 falsifiability framework — verdict summary

            ## Tbl 2 — Per-cohort AUC summary

            ## Table 5 — Strain prioritization
        """)
        fig_tbl_allow = ci._extract_figure_or_table_labels(
            figures_text, tables_text,
        )
        # Short form (canonical).
        assert "Fig 1" in fig_tbl_allow
        assert "Tbl 2" in fig_tbl_allow
        assert "Table 5" in fig_tbl_allow
        # Path form (used by ibd_phage_targeting).
        assert "figures/NB00_cohort_summary.png" in fig_tbl_allow
        assert "figures/NB01_ecotype_by_diagnosis.png" in fig_tbl_allow
        # Id form (used by tables_inventory.md).
        assert "report_tbl_01" in fig_tbl_allow
        # No duplicates.
        assert len(fig_tbl_allow) == len(set(fig_tbl_allow))

    def test_user_prompt_includes_allowlist_sections_and_anti_patterns(self):
        """build_demarcator_user_prompt emits the VALID-source_notebook
        and VALID-figure_or_table sections at the top, BEFORE INPUTS,
        with the anti-pattern reminders the live-LLM smoke surfaced."""
        methods_text = (
            "Mann-Whitney in **notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb** "
            "(cell 6, line 35).\n"
        )
        figures_text = "## Fig 3 — Something\n"
        tables_text = "## Tbl 2 — Something\n"
        cand = ci.ClaimCandidate(
            claim_id="C001",
            claim_text="AUC = 0.78 with 95% CI [0.71, 0.85] across n=343.",
            source_notebook="",
            source_cell="",
            figure_or_table="",
            effect_size_present="yes",
            ci_present="yes",
            pvalue_present="no",
            notes="unresolved",
        )

        prompt = ci.build_demarcator_user_prompt(
            [cand],
            methods_provenance_text=methods_text,
            figures_inventory_text=figures_text,
            tables_inventory_text=tables_text,
        )

        # Allowlist section headers present.
        assert "VALID source_notebook values" in prompt
        assert "VALID figure_or_table values" in prompt
        # Real notebook path appears in the allowlist.
        assert (
            "notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb" in prompt
        )
        # Real fig/tbl labels appear in the allowlist.
        assert "Fig 3" in prompt
        assert "Tbl 2" in prompt
        # Anti-pattern reminders the live-LLM smoke calibrated against.
        assert "do not paraphrase" in prompt
        assert "NOTEBOOK identifiers, not" in prompt
        assert "fabrication is not" in prompt
        # The allowlist sections come BEFORE the INPUTS section.
        idx_nb_allowlist = prompt.find("VALID source_notebook values")
        idx_inputs = prompt.find("INPUTS:")
        assert idx_nb_allowlist != -1 and idx_inputs != -1
        assert idx_nb_allowlist < idx_inputs, (
            "allowlist must appear before INPUTS so the LLM sees the "
            "menu before the per-row work"
        )


# ---------------------------------------------------------------------------
# D-052 (#41) — SCIENTIFIC_NOTATION_RE + N_COUNT_RE comma support
# ---------------------------------------------------------------------------


class TestD052ScientificNotationClass:
    """`scientific_notation` class registered at the TOP of
    PATTERN_CLASSES so longer-span sci-notation matches win
    subsumption against `ratio_with_unit`'s mantissa-only partial."""

    def test_scientific_notation_re_matches_typical_forms(self):
        """The regex catches X.Y x 10^N with ASCII x, capital X,
        Unicode ×, and `*` multiplication chars."""
        from beril_paper_writer.skill.tools.claim_inventory import (
            SCIENTIFIC_NOTATION_RE,
        )
        for text in [
            "1.5 x 10^-43",
            "1.1 X 10^-130",
            "2.5 × 10^8",
            "3.0 * 10^9",
            "7.7 x 10^12",  # positive exponent
        ]:
            assert SCIENTIFIC_NOTATION_RE.search(text), (
                f"SCIENTIFIC_NOTATION_RE must match {text!r}"
            )

    def test_pattern_classes_lists_scientific_notation_first(self):
        """Position is critical: subsumption keeps the longer-span
        match; identical-span ties go to the first class. Either
        way, scientific_notation MUST appear before ratio_with_unit
        in PATTERN_CLASSES."""
        from beril_paper_writer.skill.tools.claim_inventory import (
            PATTERN_CLASSES,
        )
        names = [name for name, _ in PATTERN_CLASSES]
        sci_idx = names.index("scientific_notation")
        rwu_idx = names.index("ratio_with_unit")
        assert sci_idx < rwu_idx, (
            f"scientific_notation (idx={sci_idx}) must appear BEFORE "
            f"ratio_with_unit (idx={rwu_idx}) in PATTERN_CLASSES; "
            f"otherwise `1.5 x 10^-43` resolves as `ratio_with_unit` "
            f"'1.5 x' and the exponent is lost. Current order: {names}"
        )

    def test_extract_numeric_matches_uses_sci_notation_class_for_sci_form(self):
        """End-to-end: the manuscript form `1.5 x 10^-43` resolves to
        match_class='scientific_notation' (not 'ratio_with_unit'),
        via subsumption picking the longer match."""
        from beril_paper_writer.skill.tools.claim_inventory import (
            extract_numeric_matches,
        )
        text = "Spearman rho = 0.302, p = 1.5 x 10^-43 controlling for size."
        matches = extract_numeric_matches(text)
        sci_matches = [m for m in matches if m.match_class == "scientific_notation"]
        assert len(sci_matches) == 1, (
            f"Expected exactly one scientific_notation match; "
            f"got {[(m.match_class, m.matched_text) for m in matches]}"
        )
        assert sci_matches[0].matched_text == "1.5 x 10^-43"


class TestD052NCountCommaSupport:
    """N_COUNT_RE extended to match thousand-separator commas, so
    `n = 22,751` is captured whole, not truncated to `n = 22` at
    the comma word-boundary."""

    def test_n_count_matches_simple_integer(self):
        """Backward compatibility: no-comma form still matches."""
        from beril_paper_writer.skill.tools.claim_inventory import N_COUNT_RE
        text = "Sample size n = 343 patients."
        matches = [m.group(0) for m in N_COUNT_RE.finditer(text)]
        assert matches == ["n = 343"]

    def test_n_count_matches_with_thousand_separator(self):
        """New behavior: `n=22,751` matches full, not truncated to
        `n=22`. Closes D1's spurious `n = 22` ungrounded finding."""
        from beril_paper_writer.skill.tools.claim_inventory import N_COUNT_RE
        text = "Essential-core: n=22,751 genes, 41.9% Enzyme"
        matches = [m.group(0) for m in N_COUNT_RE.finditer(text)]
        assert matches == ["n=22,751"], (
            f"N_COUNT_RE must capture the full `n=22,751` not just "
            f"`n=22`; got {matches}"
        )
