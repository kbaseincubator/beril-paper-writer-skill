"""Tests for skill/tools/discrepancy_register.py — Phase 0 NEW tool (M1).

Coverage (Tier A1.a + A1.b + A1.c + A1.d):

  - A2.a (3 tests) Pre-pass parsing + classification
      - plan-only candidate (prescribed but not executed)
      - exec-only candidate (executed but not prescribed)
      - both-sides candidate (overlap, awaits LLM adjudication)

  - A2.b (2 tests) Normalization
      - stopword removal
      - stem-equivalence ("test" ≡ "testing" ≡ "tests")

  - A2.c (4 tests) LLM contract — uses a fake llm_call seam:
      - equivalent label drops to no register entry
      - paraphrase label drops to no register entry
      - discrepancy label produces a register entry
      - malformed JSON (trailing-comma flavor) is repaired

  - A2.d (4 tests) Validator (exit 4 on schema violation):
      - out-of-enum severity rejected
      - out-of-enum label rejected
      - non-substring quote rejected (anti-fabrication)
      - valid pass-through accepted

  - A2.e (2 tests) Idempotency cache:
      - identical-input rerun is byte-stable + zero LLM calls
      - prompt SHA bump invalidates the cache

  - A2.f (3 tests) I/O contract
      - audit JSONL emitted with required fields per SPEC §4.7
      - exit codes correct (0, 2, 3)
      - --no-llm path bypasses the LLM seam

  - A2.g (2 tests) Render
      - markdown output is well-formed for LLM-classified discrepancies
      - D-NNN numbering increments correctly across reruns with new
        findings (cache invalidates on input change → fresh ids)

  - Render-smoke (1 test) — per feedback_render_test_must_evaluate_fstring,
      evaluate format_register_md against synthetic entries rather than
      grepping the source. Format function is a regular function not an
      f-string template, but the discipline still applies.

  - JSON helper (2 tests) — exercises lenient_json_load directly.

  - Synthetic-fixture end-to-end (1 test) — exercises the
      tests/fixtures/m1/discrepancy_synthetic_001/ AC: 5 plan analyses
      + 4 exec analyses (3 overlap + 1 unprescribed) → pre-pass yields
      6 candidates: 2 plan_only + 1 exec_only + 3 overlap.
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

    def test_exit_code_3_on_llm_call_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Without --no-llm, the run drives into the LLM seam. If the
        seam raises LLMCallError (subprocess crash, malformed envelope,
        etc.) main() maps to exit 3 + audit line. Validates the
        exit-code-3 contract from M1_PUNCH_LIST A1.a."""
        plan_p = tmp_path / "RESEARCH_PLAN.md"
        prov_p = tmp_path / "methods_provenance.md"
        # Need at least one overlap candidate to drive into the LLM seam.
        plan_p.write_text(textwrap.dedent("""\
            ## Analysis Plan
            - Pearson correlation between dose and OD600.
        """), encoding="utf-8")
        prov_p.write_text(_PROVENANCE_OVERLAP_EXAMPLE, encoding="utf-8")
        out_dir = tmp_path / "out"

        # Force the LLM seam to fail synthetically — no real subprocess.
        def _boom(sys_p, usr_p, model):
            raise dr.LLMCallError("simulated subprocess crash")
        monkeypatch.setattr(dr, "classifier_llm_call", _boom)

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


# ===========================================================================
# Helpers for A2.c/d/e/g — common overlap-fixture + canned-LLM patterns.
# ===========================================================================

# A single-overlap fixture used widely: plan and exec both reference
# Pearson correlation. The LLM seam decides what the label is.
_PLAN_SINGLE_OVERLAP = textwrap.dedent("""\
    ## Analysis Plan
    - Pearson correlation between dose and OD600.
""")


def _canned_llm(response_text: str, cost_usd: float = 0.012):
    """Construct a fake llm_call seam returning the given response text
    and cost. Asserts called exactly once per run (sanity check that
    we don't accidentally call the LLM twice in a single
    classify_overlap_candidates_with_llm invocation)."""
    state = {"calls": 0}

    def _call(sys_p: str, usr_p: str, model: str) -> tuple[str, float]:
        state["calls"] += 1
        return response_text, cost_usd
    _call.calls = lambda: state["calls"]  # type: ignore[attr-defined]
    return _call


def _write_overlap_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    plan_p = tmp_path / "RESEARCH_PLAN.md"
    prov_p = tmp_path / "methods_provenance.md"
    out_dir = tmp_path / "out"
    plan_p.write_text(_PLAN_SINGLE_OVERLAP, encoding="utf-8")
    prov_p.write_text(_PROVENANCE_OVERLAP_EXAMPLE, encoding="utf-8")
    return plan_p, prov_p, out_dir


def _canned_response(
    *,
    label: str = "discrepancy",
    severity: str = "load-bearing",
    plan_quote_verbatim: str = "Pearson correlation between dose and OD600.",
    exec_quote_verbatim: str = "Pearson correlation",
    severity_justification: str = "Test families differ.",
    candidate_index: int = 0,
) -> str:
    """A valid one-element JSON array the parse + validator both accept
    (plan_quote_verbatim is a substring of the canonical plan bullet
    in _PLAN_SINGLE_OVERLAP; exec_quote_verbatim is a substring of the
    test_name in _PROVENANCE_OVERLAP_EXAMPLE)."""
    return json.dumps([{
        "candidate_index": candidate_index,
        "label": label,
        "severity": severity,
        "severity_justification": severity_justification,
        "plan_quote_verbatim": plan_quote_verbatim,
        "exec_quote_verbatim": exec_quote_verbatim,
    }])


# ===========================================================================
# A2.c — LLM contract (mocked seam: equivalent / paraphrase / discrepancy
# / malformed-JSON repair)
# ===========================================================================

class TestA2cLLMContract:
    def test_equivalent_label_drops_to_no_register_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When the LLM labels the only overlap candidate `equivalent`,
        the register has zero LLM-classified entries (per SPEC §4.5:
        equivalent + paraphrase pairs are dropped)."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(label="equivalent", severity="cosmetic"))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        md = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        # Empty LLM-classified register → no D-NNN entries surfaced.
        assert "## D-001" not in md
        # The deterministic pre-pass also emitted nothing here (the only
        # candidate is the overlap), so the register is empty by design.
        assert "Discrepancy Register" in md

    def test_paraphrase_label_drops_to_no_register_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Paraphrase is dropped just like equivalent (v0.8.0 SPEC §4.5)."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(label="paraphrase", severity="cosmetic"))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        md = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        assert "## D-001" not in md

    def test_discrepancy_label_produces_register_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When the LLM labels the overlap `discrepancy`, that pair
        becomes a register entry with type=plan-prescribed-not-executed,
        the LLM's severity, and a recommendation built from the
        severity_justification."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            severity_justification=(
                "Plan prescribed parametric Pearson; exec applied a "
                "different test family."
            ),
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        md = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        assert "## D-001 — type: plan-prescribed-not-executed" in md
        assert "Severity: load-bearing" in md
        # Recommendation echoes the severity justification.
        assert "Plan prescribed parametric Pearson" in md
        # Execution citation has the notebook+cell from the provenance.
        assert "notebooks/01.ipynb" in md
        assert "cell 4" in md

    def test_malformed_json_trailing_comma_repaired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Trailing commas in the LLM response are repairable per
        feedback_llm_json_trailing_commas_repairable. The lenient
        loader fixes them silently; the run completes with exit 0."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        # Construct a malformed-but-repairable response: trailing comma
        # before closing `]` AND inside the inner object.
        valid = _canned_response(label="discrepancy")
        # Inject a trailing comma after the last array element.
        malformed = valid.rstrip("]") + ",]"
        fake = _canned_llm(malformed)
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        # Trailing-comma repair note goes to stderr; we can't easily
        # capture it across the subprocess-free main() path here, so
        # rely on register correctness as the AC.
        md = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        assert "## D-001" in md


# ===========================================================================
# A2.d — Validator (exit 4 on schema violation)
# ===========================================================================

class TestA2dValidator:
    def test_out_of_enum_severity_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits severity='catastrophic' (not in the enum) →
        validator raises ValidationError → main() returns 4 + audit
        line records exit_status=4."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy", severity="catastrophic",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 4

    def test_out_of_enum_label_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits label='unrelated' → validator rejects → exit 4."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="unrelated", severity="cosmetic",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4

    def test_non_substring_quote_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM fabricates a plan_quote_verbatim that is NOT a substring
        of the input candidate's plan_quote → validator rejects → exit
        4. Anti-fabrication discipline."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            plan_quote_verbatim="A FABRICATED QUOTE NOT IN THE PLAN",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4

    def test_valid_pass_through_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """A schema-compliant LLM response passes the validator → exit 0
        + register written. Pass-through baseline."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy", severity="load-bearing",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        assert (out_dir / "discrepancy_register.md").is_file()

    # ---- Empty-string anti-fab regression tests (defects 1 + 2) ----------
    # Pre-2026-05-07 the validator's substring guards were truthiness-
    # gated (`if ce.plan_quote_verbatim and ...`), so an empty string
    # bypassed the substring check and slipped through. The recommendation
    # render then interpolated `severity_justification` blindly, producing
    # malformed prose ("Reconcile in Methods: . Update..."). These three
    # tests pin the tightened rule: empty severity_justification, empty
    # plan_quote_verbatim, and empty exec_quote_verbatim each land
    # exit 4. Required for ALL labels — the cache persists equivalent /
    # paraphrase rows for traceability so non-empty matters across the
    # whole adjudication output, not just discrepancy-labeled rows.

    def test_empty_severity_justification_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits severity_justification='' → validator rejects with
        exit 4 + audit line records exit_status=4. Without this guard,
        classification_to_register_entry would emit malformed prose
        like 'Reconcile in Methods: .  Update...' for discrepancy rows
        (and lose traceability content for equivalent/paraphrase rows
        carried in the cache)."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            severity_justification="",  # empty — the defect
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 4

    def test_empty_plan_quote_verbatim_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits plan_quote_verbatim='' → validator rejects with
        exit 4. The verbatim quote is the anti-fabrication anchor; an
        empty string previously slipped past the truthiness-gated
        substring check, leaving the rule unenforced."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            plan_quote_verbatim="",  # empty — the defect
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4

    def test_empty_exec_quote_verbatim_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM emits exec_quote_verbatim='' → validator rejects with
        exit 4. Symmetric with the plan-side guard. Mirrors
        claim_inventory.py:1316's discipline (explicit non-empty check
        BEFORE the substring rule)."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            exec_quote_verbatim="",  # empty — the defect
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4


# ===========================================================================
# A2.e — Idempotency cache
# ===========================================================================

class TestA2eIdempotency:
    def test_identical_input_rerun_skips_llm_and_emits_byte_stable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Running twice on identical inputs: first run calls LLM,
        second run hits cache, makes zero LLM calls, writes
        byte-identical register markdown. Audit JSONL records both
        runs (cache_hit=false then cache_hit=true)."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(label="discrepancy"))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        # First run: cache miss, fake LLM called once.
        rc1 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc1 == 0
        first_md = (out_dir / "discrepancy_register.md").read_bytes()
        assert fake.calls() == 1  # type: ignore[attr-defined]

        # Second run: cache hit, fake should NOT be called again.
        rc2 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc2 == 0
        second_md = (out_dir / "discrepancy_register.md").read_bytes()
        assert second_md == first_md, (
            "rerun should be byte-identical to first run"
        )
        assert fake.calls() == 1, (  # type: ignore[attr-defined]
            "second run should hit cache; LLM should not be called"
        )

        # Audit confirms cache_hit=false then cache_hit=true.
        audit_lines = (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()
        assert len(audit_lines) == 2
        rec1 = json.loads(audit_lines[0])
        rec2 = json.loads(audit_lines[1])
        assert rec1["cache_hit"] is False
        assert rec2["cache_hit"] is True
        # cost_usd on hit is 0.0 (no fresh bill).
        assert rec2["cost_usd"] == 0.0

    def test_prompt_sha_bump_invalidates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Bumping the prompt file's content (simulating a prompt
        version bump) invalidates the cache and forces a fresh LLM
        call. parser_VERSION is included in the cache key for the
        same reason; we exercise the prompt-change leg here because
        it's the user-facing one."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(label="discrepancy"))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        # First run: caches result.
        rc1 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc1 == 0
        assert fake.calls() == 1  # type: ignore[attr-defined]

        # Simulate a prompt SHA change by repointing _PROMPT_PATH at a
        # synthetic alternate prompt file. The validator + parser
        # side-effects are the same; only the SHA differs → cache miss.
        alt_prompt = tmp_path / "discrepancy_classify.alt.md"
        alt_prompt.write_text(
            dr._PROMPT_PATH.read_text(encoding="utf-8") + "\n# bumped\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dr, "_PROMPT_PATH", alt_prompt)

        rc2 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc2 == 0
        assert fake.calls() == 2, (  # type: ignore[attr-defined]
            "prompt SHA change should invalidate cache → fresh LLM call"
        )


# ===========================================================================
# A2.g — Render: markdown well-formed for LLM-classified discrepancies
# + D-NNN numbering across reruns
# ===========================================================================

class TestA2gRender:
    def test_register_markdown_well_formed_for_llm_classified_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """The register markdown produced after an LLM-classified
        discrepancy is well-formed per SPEC §4.5: D-NNN heading, plan
        quote with section reference, execution citation, severity,
        recommendation. Uses an evaluating render check (per
        feedback_render_test_must_evaluate_fstring) on the rendered
        bytes, not a grep of source."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(
            label="discrepancy",
            severity="load-bearing",
            severity_justification="Methods drift requires reconciliation.",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 0
        md = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")

        # SPEC §4.5 D-001 example fields all present.
        assert "# Discrepancy Register" in md
        assert "## D-001 — type: plan-prescribed-not-executed" in md
        assert '- Plan §Analysis Plan: "Pearson correlation between dose and OD600."' in md
        assert "- Execution: notebook notebooks/01.ipynb cell 4 line 12 applies Pearson correlation" in md
        assert "- Severity: load-bearing" in md
        assert "- Recommendation: Reconcile in Methods" in md
        assert "Methods drift requires reconciliation" in md

    def test_dnnn_numbering_increments_correctly_across_reruns_with_new_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """When inputs change between runs (cache invalidates), the
        register is rewritten with fresh D-NNN ids starting at D-001.
        Run 1: 1 plan-only finding → D-001. Run 2 (new input): 1
        plan-only + 1 LLM-classified discrepancy → D-001, D-002 (fresh
        run, fresh ids). The append-only spec (SPEC §4.5) is at the
        AUDIT level (jsonl); the register MARKDOWN is per-run state."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        fake = _canned_llm(_canned_response(label="discrepancy"))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        # Run 1: no plan-only candidates (overlap only); LLM produces 1
        # discrepancy → D-001 only.
        rc1 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc1 == 0
        md1 = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        assert "## D-001 — type: plan-prescribed-not-executed" in md1
        assert "## D-002" not in md1

        # Run 2: ADD a plan-only bullet to the plan. Cache invalidates
        # (input SHA changed). New register has 2 entries: D-001 (the
        # plan-only) and D-002 (the LLM discrepancy).
        plan_p.write_text(
            _PLAN_SINGLE_OVERLAP + "- Permutation test on the difference of medians.\n",
            encoding="utf-8",
        )
        rc2 = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc2 == 0
        md2 = (out_dir / "discrepancy_register.md").read_text(encoding="utf-8")
        assert "## D-001" in md2
        assert "## D-002" in md2
        assert "## D-003" not in md2
        # D-001 is the plan-only (deterministic pre-pass first); D-002
        # is the LLM-classified overlap.
        first_entry_block = md2.split("## D-002")[0]
        assert "Permutation test on the difference of medians" in first_entry_block


# ===========================================================================
# B1.e — exit-4 audit cost recording (parallel to claim_inventory's fix)
# ===========================================================================

class TestB1eAuditCostOnExit4:
    """Pre-B1.e the validator-rejection path emitted cost_usd=0.0 in
    the audit JSONL despite the LLM having been called and billed.
    classify_overlap_candidates_with_llm now reattaches cost to the
    ValidationError; main() threads it into emit_audit_line. Filed
    after the live-LLM smoke for claim_inventory revealed the same
    gap on the discrepancy_register side (per A1 audit watch-for #4)."""

    def test_validator_rejection_records_billed_cost(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """LLM was called, billed $0.012, then validator rejected the
        output. Audit line must record the billed cost, not 0.0."""
        plan_p, prov_p, out_dir = _write_overlap_inputs(tmp_path)
        # Fake LLM emits an out-of-enum severity → guaranteed validator
        # rejection. Default cost ($0.012) flows through.
        fake = _canned_llm(_canned_response(
            label="discrepancy", severity="catastrophic",
        ))
        monkeypatch.setattr(dr, "classifier_llm_call", fake)

        rc = dr.main([
            "--methods-provenance", str(prov_p),
            "--research-plan", str(plan_p),
            "--output-dir", str(out_dir),
        ])
        assert rc == 4
        record = json.loads(
            (out_dir / "audit" / "phase0.jsonl").read_text().splitlines()[-1]
        )
        assert record["exit_status"] == 4
        # B1.e closes the cost_usd=0.0-on-exit-4 gap.
        assert record["cost_usd"] == pytest.approx(0.012), (
            "B1.e: validator-rejection path must record actual billed "
            "cost, not 0.0"
        )
