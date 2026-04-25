"""Tests for skill/tools/extract_methods.py — notebook AST + plan + reqs.

Coverage:
  - Magic/shell-call stripping (no AST crash on `!pip` / `%matplotlib`)
  - Import tracking (regular, from, with aliases)
  - Statistical-test detection (direct call, alias, dotted path)
  - Spark query extraction
  - Constant extraction (UPPER_CASE + parameter-prefixed names)
  - RESEARCH_PLAN.md section parsing + intent classification
  - requirements.txt parsing (pinned, unpinned, range, comments)
  - pyproject.toml dependencies parsing
  - environment.yml parsing
  - methods_provenance.md format (smoke-test the renderer)
  - End-to-end extract_methods on a synthetic project
  - CLI invocation (subprocess)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Direct module import for unit tests of individual functions.
from beril_paper_writer.skill.tools import extract_methods as em


# ---------------------------------------------------------------------------
# Helpers — build synthetic notebooks
# ---------------------------------------------------------------------------

def _make_notebook(cells: list[dict]) -> dict:
    """Build a minimal nbformat-4 dict from cell sources."""
    return {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


_CELL_ID_COUNTER = [0]


def _next_cell_id() -> str:
    _CELL_ID_COUNTER[0] += 1
    return f"cell-{_CELL_ID_COUNTER[0]:08d}"


def _code_cell(source: str) -> dict:
    # nbformat 5.1.4+ requires per-cell `id` field; supplying it silences
    # MissingIDFieldWarning during nbformat.write.
    return {
        "id": _next_cell_id(),
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def _markdown_cell(source: str) -> dict:
    return {
        "id": _next_cell_id(),
        "cell_type": "markdown",
        "metadata": {},
        "source": source,
    }


def _write_notebook(path: Path, cells: list[dict]) -> None:
    import nbformat
    nb = _make_notebook(cells)
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(nbformat.from_dict(nb), f)


# ---------------------------------------------------------------------------
# Magic stripping
# ---------------------------------------------------------------------------

class TestMagicStripping:
    def test_strip_line_magic(self):
        cleaned, n = em._strip_jupyter_magics("%matplotlib inline\nimport pandas")
        assert "%matplotlib" not in cleaned
        assert "import pandas" in cleaned
        assert n == 1

    def test_strip_shell_call(self):
        cleaned, n = em._strip_jupyter_magics("!pip install foo\nimport bar")
        assert "!pip" not in cleaned
        assert "import bar" in cleaned

    def test_preserves_line_numbers(self):
        cleaned, _ = em._strip_jupyter_magics("%matplotlib inline\nimport pandas")
        # Stripped line is replaced with blank, so line 2 stays line 2
        assert cleaned.split("\n")[0] == ""
        assert cleaned.split("\n")[1] == "import pandas"

    def test_no_magics_unchanged(self):
        src = "import scipy\nfoo()\n"
        cleaned, n = em._strip_jupyter_magics(src)
        assert cleaned == src
        assert n == 0


# ---------------------------------------------------------------------------
# Import tracking
# ---------------------------------------------------------------------------

class TestImportTracker:
    def test_plain_import(self):
        tracker = em._ImportTracker()
        imports, _, _, _, err = em._process_code_cell(
            "import pandas\nimport numpy as np\n", 1, tracker
        )
        assert err is None
        assert len(imports) == 2
        assert tracker.module_aliases["pandas"] == "pandas"
        assert tracker.module_aliases["np"] == "numpy"

    def test_from_import(self):
        tracker = em._ImportTracker()
        em._process_code_cell(
            "from scipy.stats import fisher_exact, ttest_ind\n", 1, tracker,
        )
        assert tracker.imported_names["fisher_exact"] == "scipy.stats.fisher_exact"
        assert tracker.imported_names["ttest_ind"] == "scipy.stats.ttest_ind"

    def test_from_import_with_alias(self):
        tracker = em._ImportTracker()
        em._process_code_cell(
            "from scipy.stats import fisher_exact as fe\n", 1, tracker,
        )
        assert tracker.imported_names["fe"] == "scipy.stats.fisher_exact"
        assert "fisher_exact" not in tracker.imported_names

    def test_resolve_aliased_call(self):
        tracker = em._ImportTracker()
        em._process_code_cell("from scipy.stats import fisher_exact as fe\n", 1, tracker)
        # Now resolve `fe(a, b)`
        import ast as _ast
        node = _ast.parse("fe(1, 2)").body[0].value  # the Call node
        assert tracker.resolve_call(node.func) == "scipy.stats.fisher_exact"

    def test_resolve_dotted_call(self):
        tracker = em._ImportTracker()
        em._process_code_cell("from scipy import stats\n", 1, tracker)
        import ast as _ast
        node = _ast.parse("stats.fisher_exact(1, 2)").body[0].value
        assert tracker.resolve_call(node.func) == "scipy.stats.fisher_exact"

    def test_resolve_bare_dotted_path(self):
        """If user writes scipy.stats.foo(...) without import, resolve as-is."""
        tracker = em._ImportTracker()
        import ast as _ast
        node = _ast.parse("scipy.stats.fisher_exact(1, 2)").body[0].value
        assert tracker.resolve_call(node.func) == "scipy.stats.fisher_exact"

    def test_dotted_import_without_alias_does_not_double_path(self):
        """`import scipy.stats` followed by `scipy.stats.X(...)` must
        resolve to `scipy.stats.X`, NOT `scipy.stats.stats.X`.

        Regression: earlier code mapped `scipy → scipy.stats` on bare
        dotted import, then `scipy.stats.X` resolved via lookup =
        `scipy.stats.stats.X`. The fix is to map `scipy → scipy` (root
        module only) and let attribute access reach `.stats`.
        """
        tracker = em._ImportTracker()
        em._process_code_cell("import scipy.stats\n", 1, tracker)
        # The local binding is the ROOT module name only.
        assert tracker.module_aliases["scipy"] == "scipy"
        # Calls through it resolve correctly.
        import ast as _ast
        node = _ast.parse("scipy.stats.fisher_exact(1, 2)").body[0].value
        assert tracker.resolve_call(node.func) == "scipy.stats.fisher_exact"

    def test_dotted_import_with_alias_keeps_full_path(self):
        """`import scipy.stats as sps` then `sps.X(...)` →
        `scipy.stats.X`."""
        tracker = em._ImportTracker()
        em._process_code_cell("import scipy.stats as sps\n", 1, tracker)
        assert tracker.module_aliases["sps"] == "scipy.stats"
        import ast as _ast
        node = _ast.parse("sps.fisher_exact(1, 2)").body[0].value
        assert tracker.resolve_call(node.func) == "scipy.stats.fisher_exact"


# ---------------------------------------------------------------------------
# Statistical-test detection
# ---------------------------------------------------------------------------

class TestStatTestDetection:
    def test_fisher_via_alias(self):
        tracker = em._ImportTracker()
        src = (
            "from scipy.stats import fisher_exact as fe\n"
            "result = fe([[10, 20], [30, 40]], alternative='two-sided')\n"
        )
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        assert err is None
        assert len(tests) == 1
        assert tests[0].test_name == "Fisher's exact test"
        assert tests[0].library_path == "scipy.stats.fisher_exact"
        assert tests[0].keyword_args == {"alternative": "'two-sided'"}

    def test_ttest_via_dotted(self):
        tracker = em._ImportTracker()
        src = (
            "from scipy import stats\n"
            "stats.ttest_ind(a, b, equal_var=False)\n"
        )
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        assert len(tests) == 1
        assert tests[0].test_name == "Two-sample t-test (Student or Welch)"
        assert tests[0].keyword_args == {"equal_var": "False"}

    def test_ols_statsmodels(self):
        tracker = em._ImportTracker()
        src = (
            "import statsmodels.api as sm\n"
            "model = sm.OLS(y, X).fit()\n"
        )
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        # The OLS call itself should be detected; .fit() is a follow-on method.
        names = [t.test_name for t in tests]
        assert "Ordinary least squares regression" in names

    def test_no_test_in_cell(self):
        tracker = em._ImportTracker()
        src = "import pandas\ndf = pandas.read_csv('x.csv')\n"
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        assert tests == []

    def test_unrelated_call_not_detected(self):
        tracker = em._ImportTracker()
        src = "result = my_function(1, 2)\n"
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        assert tests == []

    def test_chi2_contingency_detected(self):
        tracker = em._ImportTracker()
        src = (
            "from scipy.stats import chi2_contingency\n"
            "chi2, p, dof, expected = chi2_contingency(table)\n"
        )
        _, tests, _, _, err = em._process_code_cell(src, 1, tracker)
        assert any(
            t.test_name == "Chi-squared test of independence" for t in tests
        )


# ---------------------------------------------------------------------------
# Spark queries
# ---------------------------------------------------------------------------

class TestSparkQueries:
    def test_spark_sql_detected(self):
        tracker = em._ImportTracker()
        src = "df = spark.sql('SELECT * FROM kbase_genomes.proteins LIMIT 10')\n"
        _, _, spark, _, err = em._process_code_cell(src, 1, tracker)
        assert err is None
        assert len(spark) == 1
        assert "SELECT * FROM kbase_genomes.proteins" in spark[0].query_excerpt

    def test_long_query_truncated(self):
        tracker = em._ImportTracker()
        long_query = "SELECT " + ", ".join(f"col_{i}" for i in range(200)) + " FROM t"
        src = f"spark.sql({long_query!r})\n"
        _, _, spark, _, _ = em._process_code_cell(src, 1, tracker)
        assert len(spark) == 1
        assert spark[0].full_length == len(long_query)
        assert len(spark[0].query_excerpt) <= 500

    def test_pandas_sql_not_treated_as_spark(self):
        """`pd.read_sql(...)` should NOT be treated as a Spark query."""
        tracker = em._ImportTracker()
        src = "import pandas as pd\ndf = pd.read_sql('SELECT 1', conn)\n"
        _, _, spark, _, _ = em._process_code_cell(src, 1, tracker)
        assert spark == []


# ---------------------------------------------------------------------------
# Constant extraction
# ---------------------------------------------------------------------------

class TestConstants:
    def test_upper_case_constant(self):
        tracker = em._ImportTracker()
        src = "ALPHA = 0.05\nN_ITER = 1000\n"
        _, _, _, consts, _ = em._process_code_cell(src, 1, tracker)
        names = {c.name: c.value_repr for c in consts}
        assert names["ALPHA"] == "0.05"
        assert names["N_ITER"] == "1000"

    def test_parameter_prefix(self):
        tracker = em._ImportTracker()
        src = "alpha = 0.01\nn_iter = 500\nrandom_state = 42\n"
        _, _, _, consts, _ = em._process_code_cell(src, 1, tracker)
        names = {c.name for c in consts}
        assert names == {"alpha", "n_iter", "random_state"}

    def test_non_constant_assignments_skipped(self):
        tracker = em._ImportTracker()
        src = "x = 1\ny = 2\nresult = my_function()\n"
        _, _, _, consts, _ = em._process_code_cell(src, 1, tracker)
        # x, y, result don't match constant patterns
        assert consts == []

    def test_non_literal_value_marked(self):
        tracker = em._ImportTracker()
        src = "ALPHA = compute_alpha()\n"
        _, _, _, consts, _ = em._process_code_cell(src, 1, tracker)
        assert len(consts) == 1
        assert "(non-literal:" in consts[0].value_repr


# ---------------------------------------------------------------------------
# RESEARCH_PLAN.md parsing
# ---------------------------------------------------------------------------

class TestResearchPlanParsing:
    def test_extracts_known_sections(self):
        plan = textwrap.dedent("""\
            # My Research Plan

            ## Hypothesis

            X causes Y under condition Z.

            ## Methods

            We use a t-test on log-transformed data.

            ## Analysis Plan

            FDR correction via Benjamini-Hochberg at q<0.05.
        """)
        intents = em.parse_research_plan(plan)
        kinds = {i.intent_type for i in intents}
        assert "hypothesis" in kinds
        assert "methods" in kinds
        assert "analysis_plan" in kinds
        assert len(intents) == 3

    def test_unknown_sections_ignored(self):
        plan = "## Background\nstuff\n## Acknowledgments\nthanks\n"
        intents = em.parse_research_plan(plan)
        # Background and Acknowledgments are not in the recognized list
        assert intents == []

    def test_aliases_for_hypothesis(self):
        plan = "## Aim\nThe aim is X.\n## Research Question\nWhy Y?\n"
        intents = em.parse_research_plan(plan)
        kinds = {i.intent_type for i in intents}
        assert kinds == {"hypothesis"}
        assert len(intents) == 2

    def test_empty_section_skipped(self):
        plan = "## Hypothesis\n\n## Methods\nWe use X.\n"
        intents = em.parse_research_plan(plan)
        # Hypothesis has no body; should be skipped
        assert len(intents) == 1
        assert intents[0].intent_type == "methods"


# ---------------------------------------------------------------------------
# Package versions
# ---------------------------------------------------------------------------

class TestRequirementsTxt:
    def test_basic_pins(self):
        text = "scipy==1.11.0\npandas>=2.0\nnumpy~=1.24\n"
        out = em.parse_requirements_txt(text)
        assert out["scipy"] == "==1.11.0"
        assert out["pandas"] == ">=2.0"
        assert out["numpy"] == "~=1.24"

    def test_unpinned(self):
        text = "scipy\npandas\n"
        out = em.parse_requirements_txt(text)
        assert out["scipy"] == "(unpinned)"
        assert out["pandas"] == "(unpinned)"

    def test_comments_and_blanks_skipped(self):
        text = "# this is a comment\n\nscipy>=1.11\n# another\n"
        out = em.parse_requirements_txt(text)
        assert out == {"scipy": ">=1.11"}

    def test_skips_editable_and_vcs(self):
        text = (
            "-e git+https://github.com/foo/bar.git#egg=bar\n"
            "scipy>=1.11\n"
        )
        out = em.parse_requirements_txt(text)
        assert "scipy" in out
        assert "bar" not in out


class TestPyprojectToml:
    def test_extracts_dependencies(self):
        text = textwrap.dedent("""\
            [project]
            name = "foo"
            version = "0.1.0"
            dependencies = [
                "scipy>=1.11",
                "pandas==2.0.3",
                "numpy",
            ]
        """)
        out = em.parse_pyproject_toml_dependencies(text)
        assert out["scipy"] == ">=1.11"
        assert out["pandas"] == "==2.0.3"
        assert out["numpy"] == "(unpinned)"

    def test_no_dependencies_section(self):
        text = '[project]\nname = "foo"\n'
        assert em.parse_pyproject_toml_dependencies(text) == {}


class TestEnvironmentYml:
    def test_conda_format(self):
        text = textwrap.dedent("""\
            name: myenv
            dependencies:
              - python=3.11
              - scipy=1.11.0
              - pandas
        """)
        out = em.parse_environment_yml(text)
        assert out["python"] == "3.11"
        assert out["scipy"] == "1.11.0"
        assert out["pandas"] == "(unpinned)"


# ---------------------------------------------------------------------------
# methods_provenance.md formatter
# ---------------------------------------------------------------------------

class TestMdFormatter:
    def test_empty_report_renders(self):
        report = em.MethodsExtractReport(
            project_dir="/tmp/x", research_plan_found=False,
        )
        md = em.format_methods_provenance_md(report)
        assert "Methods Provenance" in md
        assert "RESEARCH_PLAN.md not found" in md

    def test_with_intent_and_tests(self):
        report = em.MethodsExtractReport(
            project_dir="/tmp/x",
            research_plan_found=True,
            plan_intents=[em.PlanIntent(
                section_name="Hypothesis",
                intent_type="hypothesis",
                text="X causes Y.",
            )],
            notebooks=[em.NotebookMethods(
                path="notebooks/01.ipynb",
                cells_total=3, cells_code=2, cells_skipped=0,
                statistical_tests=[em.StatisticalTestCall(
                    cell=2, line=5,
                    test_name="Fisher's exact test",
                    library_path="scipy.stats.fisher_exact",
                    raw_call="fe(a, b)",
                    keyword_args={},
                )],
            )],
        )
        md = em.format_methods_provenance_md(report)
        assert "Hypothesis" in md
        assert "X causes Y" in md
        assert "Fisher's exact test" in md
        assert "notebooks/01.ipynb" in md
        assert "cell 2" in md


# ---------------------------------------------------------------------------
# End-to-end against a synthetic project
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_synthetic_project(self, tmp_path: Path):
        proj = tmp_path / "myproj"
        (proj / "notebooks").mkdir(parents=True)
        # RESEARCH_PLAN
        (proj / "RESEARCH_PLAN.md").write_text(textwrap.dedent("""\
            # Research Plan

            ## Hypothesis
            X correlates with Y.

            ## Analysis Plan
            FDR correction via Benjamini-Hochberg at q<0.05.
        """), encoding="utf-8")
        # requirements.txt
        (proj / "requirements.txt").write_text(
            "scipy>=1.11\npandas==2.0.3\n", encoding="utf-8",
        )
        # One notebook with imports + a stat test
        _write_notebook(proj / "notebooks" / "01_demo.ipynb", [
            _markdown_cell("# Demo notebook"),
            _code_cell(
                "import pandas as pd\n"
                "from scipy.stats import fisher_exact as fe\n"
                "ALPHA = 0.05\n"
            ),
            _code_cell(
                "table = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})\n"
                "result = fe([[10, 20], [30, 40]], alternative='two-sided')\n"
            ),
        ])

        report = em.extract_methods(proj)
        d = report.to_dict()
        assert d["research_plan"]["found"] is True
        assert any(
            i["intent_type"] == "hypothesis" for i in d["research_plan"]["intents"]
        )
        assert len(d["notebooks"]) == 1
        nb = d["notebooks"][0]
        assert nb["statistical_tests"][0]["test_name"] == "Fisher's exact test"
        assert d["package_versions"]["from_requirements_txt"]["scipy"] == ">=1.11"
        assert d["summary"]["statistical_test_calls_total"] == 1


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
        / "extract_methods.py"
    )

    def test_cli_against_synthetic_project(self, tmp_path: Path):
        proj = tmp_path / "p"
        (proj / "notebooks").mkdir(parents=True)
        _write_notebook(proj / "notebooks" / "01.ipynb", [
            _code_cell("import scipy.stats\nscipy.stats.fisher_exact([[1,2],[3,4]])\n"),
        ])
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(proj)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        report = json.loads(proc.stdout)
        assert report["summary"]["statistical_test_calls_total"] == 1

    def test_cli_with_output_dir_writes_md(self, tmp_path: Path):
        proj = tmp_path / "p"
        (proj / "notebooks").mkdir(parents=True)
        _write_notebook(proj / "notebooks" / "01.ipynb", [
            _code_cell("import pandas\n"),
        ])
        outdir = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(proj),
             "--output-dir", str(outdir)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        assert (outdir / "methods_provenance.md").is_file()

    def test_cli_no_notebooks_returns_1(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(empty)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert "no .ipynb" in proc.stderr.lower()

    def test_cli_missing_dir_returns_1(self, tmp_path: Path):
        bogus = tmp_path / "nope"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), str(bogus)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
