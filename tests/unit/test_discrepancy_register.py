"""Tests for skill/tools/discrepancy_register.py — Phase 0 NEW tool (M1).

Coverage in this conversation (Tier A1.a + A1.b only — A1.c LLM
classifier deferred):

  - A2.a (3 tests) Pre-pass parsing + classification
      - plan-only candidate (prescribed but not executed)
      - exec-only candidate (executed but not prescribed)
      - both-sides candidate (overlap, awaits LLM adjudication)

  - A2.b (2 tests) Normalization
      - stopword removal
      - stem-equivalence ("test" ≡ "testing" ≡ "tests")

  - A2.f partial (3 tests) I/O contract
      - audit JSONL emitted with required fields per SPEC §4.7
      - exit codes correct (0 success, 2 input parse error, 3 LLM gap)
      - --no-llm path bypasses the LLM seam

  - Render-smoke (1 test) — per feedback_render_test_must_evaluate_fstring,
      evaluate format_register_md against synthetic entries rather than
      grepping the source. Format function is a regular function not an
      f-string template, but the discipline still applies.

  - JSON helper (2 tests) — pre-baked for A1.c reuse, smoke today.

  - Synthetic-fixture end-to-end (1 test) — exercises the
      tests/fixtures/m1/discrepancy_synthetic_001/ AC: 5 plan analyses
      + 4 exec analyses (3 overlap + 1 unprescribed) → pre-pass yields
      6 candidates: 2 plan_only + 1 exec_only + 3 overlap.

Out of scope here (filed in tasks for the next conversation):
  A2.c LLM contract, A2.d Validator, A2.e Idempotency, A2.g Render
  numbering across reruns. These need A1.c / A1.d to land first.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools import discrepancy_register as dr


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "m1" / "discrepancy_synthetic_001"
_SCRIPT_PATH = (
    _REPO_ROOT / "src" / "beril_paper_writer" / "skill" / "tools"
    / "discrepancy_register.py"
)


# ---------------------------------------------------------------------------
# Synthetic content used by multiple tests
# ---------------------------------------------------------------------------

# Three-bullet plan; matches three exec entries exactly + one extra
# plan-only + one extra exec-only.
_PLAN_PLANONLY_EXAMPLE = textwrap.dedent("""\
    # Plan

    ## Analysis Plan
    - Pearson correlation between dose and OD600.
    - Run a Kaplan-Meier survival fit with log-rank.
""")

_PROVENANCE_EXECONLY_EXAMPLE = textwrap.dedent("""\
    # Methods Provenance

    ## Statistical Tests Detected

    ### Mann-Whitney U test

    - `scipy.stats.mannwhitneyu` in **notebooks/04.ipynb** (cell 5, line 14)
""")

_PROVENANCE_OVERLAP_EXAMPLE = textwrap.dedent("""\
    # Methods Provenance

    ## Statistical Tests Detected

    ### Pearson correlation

    - `scipy.stats.pearsonr` in **notebooks/01.ipynb** (cell 4, line 12)
""")


# ===========================================================================
# A2.a — Pre-pass parsing + classification
# ===========================================================================

class TestA2aPrePass:
    def test_plan_only_candidate(self):
        """A plan analysis with no normalized-overlapping execution should
        yield a `plan_only` candidate."""
        plan_text = textwrap.dedent("""\
            ## Analysis Plan
            - Permutation test on the difference of medians.
        """)
        result = dr.run_register(
            plan_text=plan_text,
            provenance_text="# Methods Provenance\n\n## Statistical Tests Detected\n",
            no_llm=True,
        )
        assert result.overlap_count == 0
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.entry_id == "D-001"
        assert e.type_ == "plan-prescribed-not-executed"
        assert "Permutation test" in e.plan_quote
        assert e.execution_citation == "no notebook evidence"

    def test_exec_only_candidate(self):
        """An executed analysis with no normalized-overlapping plan item
        should yield an `exec_only` candidate."""
        result = dr.run_register(
            plan_text="# Plan\n\n## Analysis Plan\n",
            provenance_text=_PROVENANCE_EXECONLY_EXAMPLE,
            no_llm=True,
        )
        assert result.overlap_count == 0
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.type_ == "executed-not-prescribed"
        assert "Mann-Whitney U test" in e.execution_citation
        assert "notebooks/04.ipynb" in e.execution_citation
        assert "cell 5 line 14" in e.execution_citation

    def test_both_sides_candidate_classified_overlap(self):
        """A plan analysis with a normalized-equivalent execution should
        produce an `overlap` candidate, NOT a register entry under
        --no-llm (the LLM step adjudicates overlaps)."""
        plan_text = textwrap.dedent("""\
            ## Analysis Plan
            - Pearson correlation between dose and OD600.
        """)
        result = dr.run_register(
            plan_text=plan_text,
            provenance_text=_PROVENANCE_OVERLAP_EXAMPLE,
            no_llm=True,
        )
        assert result.overlap_count == 1, (
            "Plan + exec on Pearson correlation should produce an overlap "
            "candidate (deterministic pre-pass; LLM adjudicates)."
        )
        # Under --no-llm, overlap candidates are SKIPPED in the register —
        # they need LLM adjudication. The deterministic register has 0
        # entries; the overlap_count is reported separately.
        assert len(result.entries) == 0


# ===========================================================================
# A2.b — Normalization
# ===========================================================================

class TestA2aPlanBulletFolding:
    """Multi-line bullet support — research plans soft-wrap across
    indented continuation lines. Live smoke 2026-05-07 caught
    truncated quotes; this regression pins the fix."""

    def test_two_line_bullet_is_folded_into_one_quote(self):
        plan_text = textwrap.dedent("""\
            ## Analysis Plan
            - Kaplan-Meier survival curve fit to time-to-stationary-phase data,
              with log-rank test for treatment effect.
        """)
        items = dr.parse_plan_analyses(plan_text)
        assert len(items) == 1
        assert "log-rank test" in items[0].plan_quote, items[0].plan_quote
        assert "treatment effect" in items[0].plan_quote
        assert items[0].plan_quote.endswith("treatment effect.")

    def test_blank_line_closes_open_bullet(self):
        plan_text = textwrap.dedent("""\
            ## Analysis Plan
            - First bullet,
              second line.

            - Second bullet,
              second line.
        """)
        items = dr.parse_plan_analyses(plan_text)
        assert len(items) == 2
        assert "First bullet, second line." == items[0].plan_quote
        assert "Second bullet, second line." == items[1].plan_quote

    def test_heading_closes_open_bullet(self):
        plan_text = textwrap.dedent("""\
            ## Analysis Plan
            - Pre-registered analysis,
              with full context.
            ## Out-of-scope
            Narrative without bullets.
        """)
        items = dr.parse_plan_analyses(plan_text)
        assert len(items) == 1
        assert items[0].plan_quote == "Pre-registered analysis, with full context."


class TestA2bNormalization:
    def test_stopword_removal_drops_determiners_and_connectives(self):
        """Common stopwords (the, of, and, to, with, in, on, between, etc.)
        must NOT survive normalization. Content words (test names, organ
        identifiers, metric names) must survive."""
        norm = dr.normalize_phrase(
            "The Welch t-test of the mean OD600 between treatment and control"
        )
        # Stopwords absent.
        for sw in ("the", "of", "between", "and"):
            assert sw not in norm.split(), (
                f"stopword {sw!r} survived normalization: {norm!r}"
            )
        # Content words present (possibly stemmed).
        assert "welch" in norm
        # "t-test" is a hyphenated content compound; should survive intact.
        assert "t-test" in norm
        # "mean" is content, should survive.
        assert "mean" in norm

    def test_stem_equivalence_collapses_test_testing_tests(self):
        """Lightweight Porter-style stemming must collapse common
        morphological variants. The pre-pass relies on this for matching
        plan vs exec phrases."""
        # The bare lemma:
        n_test = dr.normalize_phrase("test")
        # "tests" should stem to the same form.
        n_tests = dr.normalize_phrase("tests")
        # "testing" should also stem to the same form.
        n_testing = dr.normalize_phrase("testing")
        assert n_test == n_tests == n_testing, (
            f"stem collapse failed: test={n_test!r} tests={n_tests!r} "
            f"testing={n_testing!r}"
        )
        # "correction" ≡ "corrections"
        assert dr.normalize_phrase("correction") == dr.normalize_phrase("corrections")


# ===========================================================================
# A2.f (partial) — I/O contract
# ===========================================================================

class TestA2fIOContract:
    def test_audit_jsonl_emitted_with_required_fields(self, tmp_path: Path):
        """One audit line per invocation, appended to
        <output-dir>/audit/phase0.jsonl, with all 8 fields per SPEC §4.7
        + M1_PUNCH_LIST A1.a."""
        plan_p = tmp_path / "RESEARCH_PLAN.md"
        prov_p = tmp_path / "methods_provenance.md"
        plan_p.write_text(_PLAN_PLANONLY_EXAMPLE, encoding="utf-8")
        prov_p.write_text(_PROVENANCE_EXECONLY_EXAMPLE, encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
            "--no-llm",
        ])
        assert rc == 0

        audit_path = out_dir / "audit" / "phase0.jsonl"
        assert audit_path.is_file(), "audit file not emitted"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        # All 8 required fields present:
        assert record["tool"] == "discrepancy_register"
        assert "timestamp" in record and record["timestamp"].endswith("Z")
        assert "version" in record
        assert "inputs" in record
        assert "methods_provenance" in record["inputs"]
        assert "research_plan" in record["inputs"]
        assert record["inputs"]["methods_provenance"] is not None
        assert record["inputs"]["research_plan"] is not None
        # Hex SHA-256 is 64 chars.
        assert len(record["inputs"]["methods_provenance"]) == 64
        assert "output_path" in record
        assert record["output_path"].endswith("discrepancy_register.md")
        assert "entry_count" in record
        assert isinstance(record["entry_count"], int)
        assert "cost_usd" in record
        assert record["cost_usd"] == 0.0  # --no-llm path
        assert record["exit_status"] == 0

    def test_exit_code_2_on_missing_input(self, tmp_path: Path):
        """Required input file missing → exit 2 + audit line records the
        absence with None hashes + exit_status=2."""
        plan_p = tmp_path / "RESEARCH_PLAN.md"  # not created
        prov_p = tmp_path / "methods_provenance.md"
        prov_p.write_text(_PROVENANCE_EXECONLY_EXAMPLE, encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
            "--no-llm",
        ])
        assert rc == 2

        audit_path = out_dir / "audit" / "phase0.jsonl"
        assert audit_path.is_file()
        record = json.loads(audit_path.read_text().splitlines()[-1])
        assert record["exit_status"] == 2
        assert record["inputs"]["research_plan"] is None  # missing
        assert record["inputs"]["methods_provenance"] is not None  # present

    def test_exit_code_3_when_llm_seam_not_implemented_and_no_llm_unset(
        self, tmp_path: Path,
    ):
        """Without --no-llm, the run should drive into the LLM seam. In
        this milestone (A1.b), A1.c is not implemented; the seam raises
        LLMNotImplemented, which main() maps to exit 3 + audit line.
        Validates the exit-code-3 contract from M1_PUNCH_LIST A1.a."""
        plan_p = tmp_path / "RESEARCH_PLAN.md"
        prov_p = tmp_path / "methods_provenance.md"
        # Need at least one overlap candidate to drive into the LLM seam.
        plan_p.write_text(textwrap.dedent("""\
            ## Analysis Plan
            - Pearson correlation between dose and OD600.
        """), encoding="utf-8")
        prov_p.write_text(_PROVENANCE_OVERLAP_EXAMPLE, encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
            # NB: no --no-llm — that's the point of this test.
        ])
        assert rc == 3

        audit_path = out_dir / "audit" / "phase0.jsonl"
        record = json.loads(audit_path.read_text().splitlines()[-1])
        assert record["exit_status"] == 3
        assert record["entry_count"] == 0
        assert record["cost_usd"] == 0.0

    def test_no_llm_path_bypasses_llm_seam(self, tmp_path: Path):
        """With --no-llm, the LLM seam is never invoked. Symmetric to
        the exit-code-3 test: same overlap-producing inputs, but
        --no-llm flag flipped → exit 0 + register has 0 entries (overlap
        skipped) + audit line records that."""
        plan_p = tmp_path / "RESEARCH_PLAN.md"
        prov_p = tmp_path / "methods_provenance.md"
        plan_p.write_text(textwrap.dedent("""\
            ## Analysis Plan
            - Pearson correlation between dose and OD600.
        """), encoding="utf-8")
        prov_p.write_text(_PROVENANCE_OVERLAP_EXAMPLE, encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
            "--no-llm",
        ])
        assert rc == 0

        # Register exists, has the "overlap skipped" footer.
        reg_path = out_dir / "discrepancy_register.md"
        assert reg_path.is_file()
        md = reg_path.read_text(encoding="utf-8")
        assert "Discrepancy Register" in md
        assert "overlap candidate" in md.lower(), (
            "Expected the overlap-skipped footer but got:\n" + md
        )

        # Audit confirms cost=0 (no LLM call) and exit=0.
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 0
        assert record["cost_usd"] == 0.0


# ===========================================================================
# Render-smoke — evaluate format_register_md, don't just grep source
# (per feedback_render_test_must_evaluate_fstring)
# ===========================================================================

class TestRenderSmoke:
    def test_format_register_md_evaluates_against_synthetic_entries(self):
        """Call the actual emitter with a non-empty list of register
        entries and check the output is well-formed markdown that
        contains the expected D-NNN headers + plan/exec lines + severity
        + recommendation lines per SPEC §4.5."""
        entries = [
            dr.RegisterEntry(
                entry_id="D-001",
                type_="plan-prescribed-not-executed",
                plan_quote="Permutation test on the difference of medians.",
                plan_section="Analysis Plan",
                execution_citation="no notebook evidence",
                severity="unclear",
                recommendation="Verify whether this analysis was performed.",
            ),
            dr.RegisterEntry(
                entry_id="D-002",
                type_="executed-not-prescribed",
                plan_quote="—",
                plan_section="—",
                execution_citation=(
                    "notebook notebooks/04.ipynb cell 5 line 14 — "
                    "`scipy.stats.mannwhitneyu`"
                ),
                severity="unclear",
                recommendation="Surface in Methods.",
            ),
        ]
        md = dr.format_register_md(entries=entries, overlap_skipped_count=0, no_llm=True)
        # Headers
        assert "# Discrepancy Register" in md
        assert "## D-001 — type: plan-prescribed-not-executed" in md
        assert "## D-002 — type: executed-not-prescribed" in md
        # Per-entry payload
        assert "Permutation test on the difference of medians" in md
        assert "no notebook evidence" in md
        assert "scipy.stats.mannwhitneyu" in md
        # SPEC-required fields
        assert "Severity: unclear" in md
        assert "Recommendation: " in md
        # No spurious overlap footer when count=0
        assert "candidate overlap pair" not in md.lower()


# ===========================================================================
# JSON helper — pre-baked for A1.c
# ===========================================================================

class TestLenientJsonLoad:
    def test_strict_json_passes_through(self):
        text = '{"a": 1, "b": [2, 3]}'
        assert dr.lenient_json_load(text) == {"a": 1, "b": [2, 3]}

    def test_trailing_comma_is_repaired(self, capsys):
        # Trailing comma before closing `]` and `}` are unambiguous.
        text = '{"a": 1, "b": [2, 3,],}'
        assert dr.lenient_json_load(text, source="<unit>") == {"a": 1, "b": [2, 3]}
        captured = capsys.readouterr()
        assert "stripped trailing comma" in captured.err


# ===========================================================================
# Synthetic-fixture end-to-end — A1.b headline AC
# ===========================================================================

class TestSyntheticFixture:
    def test_5_plan_4_exec_yields_2_planonly_1_execonly_3_overlap(self):
        """The headline AC for A1.b: synthetic plan with 5 analyses +
        synthetic provenance with 4 (3 overlapping + 1 unprescribed)
        yields 6 candidates (2 plan_only + 1 exec_only + 3 overlap)."""
        plan_text = (_FIXTURE_DIR / "RESEARCH_PLAN.md").read_text(encoding="utf-8")
        prov_text = (_FIXTURE_DIR / "methods_provenance.md").read_text(encoding="utf-8")

        plan_items = dr.parse_plan_analyses(plan_text)
        exec_items = dr.parse_provenance_executions(prov_text)
        assert len(plan_items) == 5, (
            f"Expected 5 plan analyses; got {len(plan_items)}: "
            f"{[p.plan_quote for p in plan_items]}"
        )
        assert len(exec_items) == 4, (
            f"Expected 4 exec analyses; got {len(exec_items)}: "
            f"{[x.test_name for x in exec_items]}"
        )

        cands = dr.pre_pass(plan_items, exec_items)
        plan_only = [c for c in cands if c.kind == "plan_only"]
        exec_only = [c for c in cands if c.kind == "exec_only"]
        overlap = [c for c in cands if c.kind == "overlap"]
        assert len(plan_only) == 2, (
            f"Expected 2 plan-only candidates; got {len(plan_only)}: "
            f"{[c.plan.plan_quote for c in plan_only]}"
        )
        assert len(exec_only) == 1, (
            f"Expected 1 exec-only candidate; got {len(exec_only)}: "
            f"{[c.exec_.test_name for c in exec_only]}"
        )
        assert len(overlap) == 3, (
            f"Expected 3 overlap candidates; got {len(overlap)}: "
            f"{[(c.plan.plan_quote[:30], c.exec_.test_name) for c in overlap]}"
        )

        # The exec_only one MUST be Mann-Whitney (the unprescribed analysis).
        assert "Mann-Whitney" in exec_only[0].exec_.test_name


# ===========================================================================
# CLI subprocess — tests --help renders + script is invokable as a script.
# Distinct from the in-process main() tests because Adam's smoke runs the
# script directly (paper_writer.sh-style).
# ===========================================================================

class TestCLI:
    def test_help_renders(self):
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert "discrepancy_register" in proc.stdout
        assert "--methods-provenance" in proc.stdout
        assert "--research-plan" in proc.stdout
        assert "--output-dir" in proc.stdout
        assert "--no-llm" in proc.stdout
