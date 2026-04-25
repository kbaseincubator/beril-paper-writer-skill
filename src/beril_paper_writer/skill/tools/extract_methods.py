#!/usr/bin/env python3
"""extract_methods.py — Methods grounding via notebook AST + plan parsing.

Per SPEC §6.3 + DECISIONS D-018: Methods grounding has two complementary
sources, both required:

  - **Intent** — RESEARCH_PLAN.md (hypothesis structure, prespecified
    tests, sample-size justification, design rationale).
  - **Execution** — *.ipynb notebooks + *.py scripts (actual function
    calls, parameters, package versions, statistical tests invoked).

This script extracts FACTS from both sources and writes:
  - JSON to stdout (structured, orchestrator-consumable)
  - methods_provenance.md to --output-dir (human-readable, links each
    fact to its source notebook+cell or plan section)

It does NOT cross-check plan vs. execution or flag implied steps —
those require LLM judgment and live in the Phase 3 Methods system
prompt. The script's job is to provide a defensible factual basis the
Methods agent can ground claims in.

Standalone-script + importable-module pattern, mirroring
validate_manuscript.py.

Usage:
    python3 extract_methods.py <project_dir> [--output-dir <dir>]
                                              [--json-only]
                                              [--no-md]

Exit codes:
  0 — extraction completed
  1 — user error (missing project_dir, no notebooks)
  2 — runtime error (notebook unparseable; reported per-notebook,
      not fatal unless ALL notebooks fail)
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Statistical test recognition — library call → canonical test name
# ---------------------------------------------------------------------------

# Each entry: dotted call path → human-readable test name. Aliases (set
# at import time, like `from scipy.stats import fisher_exact as fe`) are
# resolved during the per-notebook AST walk.
_TEST_NAME_MAP: dict[str, str] = {
    # scipy.stats parametric
    "scipy.stats.ttest_ind": "Two-sample t-test (Student or Welch)",
    "scipy.stats.ttest_rel": "Paired t-test",
    "scipy.stats.ttest_1samp": "One-sample t-test",
    "scipy.stats.f_oneway": "One-way ANOVA",
    "scipy.stats.alexandergovern": "Alexander-Govern test",
    # scipy.stats nonparametric
    "scipy.stats.mannwhitneyu": "Mann-Whitney U test",
    "scipy.stats.wilcoxon": "Wilcoxon signed-rank test",
    "scipy.stats.kruskal": "Kruskal-Wallis test",
    "scipy.stats.friedmanchisquare": "Friedman test",
    "scipy.stats.kstest": "Kolmogorov-Smirnov test",
    "scipy.stats.ks_2samp": "Two-sample Kolmogorov-Smirnov test",
    # scipy.stats categorical
    "scipy.stats.fisher_exact": "Fisher's exact test",
    "scipy.stats.chi2_contingency": "Chi-squared test of independence",
    "scipy.stats.barnard_exact": "Barnard's exact test",
    "scipy.stats.boschloo_exact": "Boschloo's exact test",
    "scipy.stats.mcnemar": "McNemar's test",
    # scipy.stats correlation
    "scipy.stats.pearsonr": "Pearson correlation",
    "scipy.stats.spearmanr": "Spearman rank correlation",
    "scipy.stats.kendalltau": "Kendall's tau",
    # statsmodels
    "statsmodels.api.OLS": "Ordinary least squares regression",
    "statsmodels.api.GLM": "Generalized linear model",
    "statsmodels.api.Logit": "Logistic regression",
    "statsmodels.api.MixedLM": "Mixed-effects linear model",
    "statsmodels.formula.api.ols": "Ordinary least squares (formula API)",
    "statsmodels.formula.api.logit": "Logistic regression (formula API)",
    "statsmodels.stats.multitest.multipletests": "Multiple-testing correction",
    "statsmodels.stats.proportion.proportions_ztest": "Two-proportion z-test",
    "statsmodels.stats.contingency_tables.mcnemar": "McNemar's test",
    "statsmodels.duration.survfunc.SurvfuncRight": "Survival function (Kaplan-Meier-equivalent)",
    "statsmodels.duration.hazard_regression.PHReg": "Cox proportional-hazards regression",
    # sklearn (model fitting; not strictly tests but worth tracking)
    "sklearn.linear_model.LinearRegression": "Linear regression (sklearn)",
    "sklearn.linear_model.LogisticRegression": "Logistic regression (sklearn)",
    "sklearn.linear_model.Ridge": "Ridge regression",
    "sklearn.linear_model.Lasso": "Lasso regression",
    "sklearn.ensemble.RandomForestClassifier": "Random forest classifier",
    "sklearn.ensemble.RandomForestRegressor": "Random forest regressor",
    "sklearn.ensemble.GradientBoostingClassifier": "Gradient boosting classifier",
    "sklearn.cluster.KMeans": "K-means clustering",
    "sklearn.decomposition.PCA": "Principal component analysis",
    "sklearn.model_selection.cross_val_score": "Cross-validation",
    "sklearn.model_selection.GridSearchCV": "Grid-search cross-validation",
    "sklearn.metrics.roc_auc_score": "ROC AUC",
    # lifelines
    "lifelines.KaplanMeierFitter": "Kaplan-Meier survival estimator",
    "lifelines.CoxPHFitter": "Cox proportional-hazards regression",
    "lifelines.statistics.logrank_test": "Log-rank test",
    # bioinformatics-relevant
    "Bio.PDB": "Biopython structural analysis",
    "scipy.cluster.hierarchy.linkage": "Hierarchical clustering (linkage)",
    "scipy.spatial.distance.pdist": "Pairwise distance matrix",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ImportRecord:
    """One import statement extracted from a notebook cell."""

    cell: int
    line: int
    module: str   # e.g. "scipy.stats" or "pandas"
    alias: Optional[str]  # e.g. "pd" for pandas; None if not aliased
    names: list[str]      # names imported from the module (empty for plain `import x`)
    name_aliases: dict[str, str] = field(default_factory=dict)  # imported_name → alias

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class StatisticalTestCall:
    """One statistical-test call detected in a notebook cell."""

    cell: int
    line: int
    test_name: str        # canonical name from _TEST_NAME_MAP
    library_path: str     # e.g. "scipy.stats.fisher_exact"
    raw_call: str         # the actual call as written, e.g. "fe(a, b, alternative='two-sided')"
    keyword_args: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class SparkQuery:
    """One spark.sql(...) call detected in a notebook cell."""

    cell: int
    line: int
    query_excerpt: str    # first ~500 chars of the SQL
    full_length: int      # full string length (so caller knows if truncated)
    execution_context: str = "K-BERDL via Spark (remote execution; query string only, not full execution path)"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ConstantAssignment:
    """An assignment to a likely-parameter name (UPPER_CASE or thresholdy names)."""

    cell: int
    line: int
    name: str
    value_repr: str  # repr of the literal value, or "(non-literal)" if not a constant

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class NotebookMethods:
    """All methods-relevant facts extracted from one notebook."""

    path: str               # relative to project_dir
    cells_total: int
    cells_code: int
    cells_skipped: int      # cells we couldn't parse (magic-only, syntax errors, etc.)
    parse_errors: list[str] = field(default_factory=list)

    imports: list[ImportRecord] = field(default_factory=list)
    statistical_tests: list[StatisticalTestCall] = field(default_factory=list)
    spark_queries: list[SparkQuery] = field(default_factory=list)
    constants: list[ConstantAssignment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "cells_total": self.cells_total,
            "cells_code": self.cells_code,
            "cells_skipped": self.cells_skipped,
            "parse_errors": list(self.parse_errors),
            "imports": [i.to_dict() for i in self.imports],
            "statistical_tests": [t.to_dict() for t in self.statistical_tests],
            "spark_queries": [q.to_dict() for q in self.spark_queries],
            "constants": [c.to_dict() for c in self.constants],
        }


@dataclass
class PlanIntent:
    """A section of intent extracted from RESEARCH_PLAN.md."""

    section_name: str       # e.g. "Hypothesis", "Analysis Plan"
    intent_type: str        # "hypothesis" | "methods" | "analysis_plan" | "other"
    text: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PackageVersions:
    """Versions extracted from requirements/environment files."""

    from_requirements_txt: dict[str, str] = field(default_factory=dict)
    from_pyproject_toml: dict[str, str] = field(default_factory=dict)
    from_environment_yml: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def all_packages(self) -> dict[str, str]:
        """Merged dict (requirements.txt wins on conflict)."""
        merged: dict[str, str] = {}
        merged.update(self.from_environment_yml)
        merged.update(self.from_pyproject_toml)
        merged.update(self.from_requirements_txt)
        return merged


@dataclass
class MethodsExtractReport:
    """The full report from running extraction on a project directory."""

    project_dir: str
    research_plan_found: bool
    plan_intents: list[PlanIntent] = field(default_factory=list)
    notebooks: list[NotebookMethods] = field(default_factory=list)
    scripts: list[NotebookMethods] = field(default_factory=list)  # .py scripts
    package_versions: PackageVersions = field(default_factory=PackageVersions)

    def to_dict(self) -> dict:
        return {
            "project_dir": self.project_dir,
            "research_plan": {
                "found": self.research_plan_found,
                "intents": [i.to_dict() for i in self.plan_intents],
            },
            "notebooks": [n.to_dict() for n in self.notebooks],
            "scripts": [s.to_dict() for s in self.scripts],
            "package_versions": self.package_versions.to_dict(),
            "summary": self._summary(),
        }

    def _summary(self) -> dict:
        all_tests = [
            t for n in (self.notebooks + self.scripts) for t in n.statistical_tests
        ]
        unique_tests = sorted({t.test_name for t in all_tests})
        all_imports = [
            i for n in (self.notebooks + self.scripts) for i in n.imports
        ]
        unique_modules = sorted({i.module for i in all_imports})
        return {
            "notebook_count": len(self.notebooks),
            "script_count": len(self.scripts),
            "code_cells_total": sum(n.cells_code for n in self.notebooks),
            "code_cells_skipped": sum(n.cells_skipped for n in self.notebooks),
            "statistical_test_calls_total": len(all_tests),
            "statistical_test_names_unique": unique_tests,
            "modules_imported_unique": unique_modules,
            "spark_query_count": sum(len(n.spark_queries) for n in self.notebooks),
            "package_count": len(self.package_versions.all_packages()),
        }


# ---------------------------------------------------------------------------
# Notebook AST walker
# ---------------------------------------------------------------------------

# Lines we strip before AST-parsing a code cell.
# - `%foo` and `%%foo` are IPython magics
# - `!cmd` is a shell call
# - `?foo` is help syntax
_MAGIC_RE = re.compile(r"^\s*[%!?]")


def _strip_jupyter_magics(source: str) -> tuple[str, int]:
    """Return source with magic/shell lines replaced by blank lines, and the
    count of stripped lines. Blanks preserve line numbers for AST locations."""
    out_lines: list[str] = []
    stripped = 0
    for line in source.split("\n"):
        if _MAGIC_RE.match(line):
            out_lines.append("")  # preserve line numbers
            stripped += 1
        else:
            out_lines.append(line)
    return "\n".join(out_lines), stripped


class _ImportTracker:
    """Tracks module aliases and from-imports for dotted-path resolution.

    State is per-notebook: each notebook starts with a fresh tracker, since
    notebooks are independent execution contexts.
    """

    def __init__(self) -> None:
        self.module_aliases: dict[str, str] = {}  # alias → fully-qualified module
        self.imported_names: dict[str, str] = {}  # local name → fully-qualified call path

    def visit_import(self, node: ast.Import) -> list[ImportRecord]:
        records: list[ImportRecord] = []
        for alias in node.names:
            module = alias.name
            if alias.asname:
                # `import scipy.stats as sps` — local 'sps' refers to
                # full module path 'scipy.stats'; later `sps.fisher_exact`
                # resolves to 'scipy.stats.fisher_exact'.
                local = alias.asname
                mapped = module
            else:
                # `import scipy.stats` — local 'scipy' refers to the
                # ROOT module (Python exposes 'scipy' as the binding;
                # 'scipy.stats' is reached via attribute access). Mapping
                # 'scipy → scipy' lets `scipy.stats.fisher_exact` resolve
                # to 'scipy.stats.fisher_exact', not the doubled
                # 'scipy.stats.stats.fisher_exact'.
                local = module.split(".")[0]
                mapped = local
            self.module_aliases[local] = mapped
            records.append(ImportRecord(
                cell=0,  # filled by caller
                line=node.lineno,
                module=module,
                alias=alias.asname,
                names=[],
                name_aliases={},
            ))
        return records

    def visit_import_from(self, node: ast.ImportFrom) -> list[ImportRecord]:
        if node.module is None:
            # Relative imports (e.g. `from . import x`); ignore for our purposes.
            return []
        module = node.module
        names: list[str] = []
        name_aliases: dict[str, str] = {}
        for alias in node.names:
            names.append(alias.name)
            local = alias.asname or alias.name
            self.imported_names[local] = f"{module}.{alias.name}"
            if alias.asname:
                name_aliases[alias.name] = alias.asname
        return [ImportRecord(
            cell=0,
            line=node.lineno,
            module=module,
            alias=None,
            names=names,
            name_aliases=name_aliases,
        )]

    def resolve_call(self, func_node: ast.AST) -> Optional[str]:
        """Resolve a function call to a fully-qualified dotted path if possible.

        Examples:
          fe(a, b)               where `fe` was imported via `from scipy.stats import fisher_exact as fe`
                                 → "scipy.stats.fisher_exact"
          stats.fisher_exact(a)  where `from scipy import stats` was done
                                 → "scipy.stats.fisher_exact"
          scipy.stats.fisher_exact(a)  → "scipy.stats.fisher_exact"
          obj.method(a)          where obj is not a tracked module
                                 → None
        """
        # Build the dotted path from the func node.
        parts: list[str] = []
        node: Optional[ast.AST] = func_node
        while True:
            if isinstance(node, ast.Attribute):
                parts.insert(0, node.attr)
                node = node.value
            elif isinstance(node, ast.Name):
                parts.insert(0, node.id)
                break
            else:
                return None  # complex call expression we can't resolve
        if not parts:
            return None
        head = parts[0]
        rest = parts[1:]
        # Resolve head against trackers
        if head in self.imported_names:
            # `fe` → `scipy.stats.fisher_exact`. If there are further attrs
            # (rest), append them.
            base = self.imported_names[head]
            return ".".join([base] + rest) if rest else base
        if head in self.module_aliases:
            # `pd` → `pandas`. Append rest.
            base = self.module_aliases[head]
            return ".".join([base] + rest) if rest else base
        # No alias info; treat the dotted path as-is (e.g., user wrote
        # `scipy.stats.fisher_exact(...)` without `import scipy`)
        return ".".join(parts)


def _extract_keyword_args(call: ast.Call) -> dict[str, str]:
    """Extract keyword arguments from a Call node as {name: repr_of_value}."""
    out: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue  # **kwargs splat; skip
        try:
            out[kw.arg] = ast.unparse(kw.value)
        except Exception:
            out[kw.arg] = "<unparseable>"
    return out


def _is_spark_sql_call(func_node: ast.AST) -> bool:
    """Return True if the func node is `<something>.sql(...)` where the
    something is plausibly a Spark session (named `spark`, `ss`, etc.)."""
    if not isinstance(func_node, ast.Attribute):
        return False
    if func_node.attr != "sql":
        return False
    # Look at what `.sql` was called on
    receiver = func_node.value
    if isinstance(receiver, ast.Name):
        return receiver.id.lower() in {"spark", "ss", "session", "spark_session"}
    if isinstance(receiver, ast.Attribute):
        return receiver.attr.lower() in {"spark", "ss", "session"}
    return False


def _looks_like_constant_name(name: str) -> bool:
    """Heuristic: ALL_CAPS_WITH_UNDERSCORES OR starts with common parameter
    naming conventions like `alpha`, `threshold`, `n_iter`, `random_state`."""
    if not name:
        return False
    if name.isupper() and len(name) >= 2:
        return True
    lower = name.lower()
    parameter_prefixes = (
        "alpha", "threshold", "n_", "min_", "max_", "tol", "epsilon",
        "random_state", "seed", "lr", "learning_rate", "batch_size",
    )
    return any(lower.startswith(p) for p in parameter_prefixes)


def _process_code_cell(
    source: str,
    cell_index: int,
    tracker: _ImportTracker,
) -> tuple[
    list[ImportRecord],
    list[StatisticalTestCall],
    list[SparkQuery],
    list[ConstantAssignment],
    Optional[str],   # parse error message, if any
]:
    """Walk the AST of one code cell. Returns (imports, tests, spark, constants, error)."""
    cleaned, _ = _strip_jupyter_magics(source)
    if not cleaned.strip():
        return [], [], [], [], None
    try:
        tree = ast.parse(cleaned)
    except SyntaxError as e:
        return [], [], [], [], f"SyntaxError in cell {cell_index}: {e.msg} at line {e.lineno}"

    imports: list[ImportRecord] = []
    tests: list[StatisticalTestCall] = []
    spark: list[SparkQuery] = []
    constants: list[ConstantAssignment] = []

    # First pass: imports (so the tracker is populated before call resolution).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            recs = tracker.visit_import(node)
            for r in recs:
                r.cell = cell_index
            imports.extend(recs)
        elif isinstance(node, ast.ImportFrom):
            recs = tracker.visit_import_from(node)
            for r in recs:
                r.cell = cell_index
            imports.extend(recs)

    # Second pass: calls + assignments.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Spark query?
            if _is_spark_sql_call(node.func) and node.args:
                first = node.args[0]
                query_str: Optional[str] = None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    query_str = first.value
                if query_str is not None:
                    spark.append(SparkQuery(
                        cell=cell_index,
                        line=node.lineno,
                        query_excerpt=query_str[:500],
                        full_length=len(query_str),
                    ))
            # Statistical test?
            resolved = tracker.resolve_call(node.func)
            if resolved and resolved in _TEST_NAME_MAP:
                try:
                    raw = ast.unparse(node)
                    if len(raw) > 200:
                        raw = raw[:197] + "..."
                except Exception:
                    raw = "(unparseable)"
                tests.append(StatisticalTestCall(
                    cell=cell_index,
                    line=node.lineno,
                    test_name=_TEST_NAME_MAP[resolved],
                    library_path=resolved,
                    raw_call=raw,
                    keyword_args=_extract_keyword_args(node),
                ))
        elif isinstance(node, ast.Assign):
            # Constants / parameters
            for target in node.targets:
                if isinstance(target, ast.Name) and _looks_like_constant_name(target.id):
                    if isinstance(node.value, ast.Constant):
                        value_repr = repr(node.value.value)
                    else:
                        try:
                            value_repr = "(non-literal: " + ast.unparse(node.value)[:60] + ")"
                        except Exception:
                            value_repr = "(non-literal)"
                    constants.append(ConstantAssignment(
                        cell=cell_index,
                        line=node.lineno,
                        name=target.id,
                        value_repr=value_repr,
                    ))

    return imports, tests, spark, constants, None


def extract_notebook_methods(notebook_path: Path, project_dir: Path) -> NotebookMethods:
    """Walk one .ipynb; return all methods-relevant facts."""
    import nbformat  # imported lazily so the module loads even without nbformat
    rel_path = str(notebook_path.relative_to(project_dir))
    try:
        nb = nbformat.read(str(notebook_path), as_version=4)
    except Exception as e:
        return NotebookMethods(
            path=rel_path,
            cells_total=0,
            cells_code=0,
            cells_skipped=0,
            parse_errors=[f"Failed to read notebook: {e}"],
        )

    cells = list(nb.cells)
    code_cells = [c for c in cells if c.cell_type == "code"]
    tracker = _ImportTracker()
    all_imports: list[ImportRecord] = []
    all_tests: list[StatisticalTestCall] = []
    all_spark: list[SparkQuery] = []
    all_constants: list[ConstantAssignment] = []
    parse_errors: list[str] = []
    skipped = 0

    for i, cell in enumerate(code_cells):
        source = cell.source if isinstance(cell.source, str) else "".join(cell.source)
        imports, tests, spark, consts, err = _process_code_cell(source, i + 1, tracker)
        all_imports.extend(imports)
        all_tests.extend(tests)
        all_spark.extend(spark)
        all_constants.extend(consts)
        if err is not None:
            parse_errors.append(err)
            skipped += 1

    return NotebookMethods(
        path=rel_path,
        cells_total=len(cells),
        cells_code=len(code_cells),
        cells_skipped=skipped,
        parse_errors=parse_errors,
        imports=all_imports,
        statistical_tests=all_tests,
        spark_queries=all_spark,
        constants=all_constants,
    )


def extract_script_methods(script_path: Path, project_dir: Path) -> NotebookMethods:
    """Walk one .py script; return facts in the same shape as a notebook."""
    rel_path = str(script_path.relative_to(project_dir))
    try:
        source = script_path.read_text(encoding="utf-8")
    except Exception as e:
        return NotebookMethods(
            path=rel_path, cells_total=0, cells_code=0, cells_skipped=0,
            parse_errors=[f"Failed to read script: {e}"],
        )
    tracker = _ImportTracker()
    imports, tests, spark, consts, err = _process_code_cell(source, 1, tracker)
    parse_errors = [err] if err else []
    return NotebookMethods(
        path=rel_path,
        cells_total=1,
        cells_code=1,
        cells_skipped=1 if err else 0,
        parse_errors=parse_errors,
        imports=imports,
        statistical_tests=tests,
        spark_queries=spark,
        constants=consts,
    )


# ---------------------------------------------------------------------------
# RESEARCH_PLAN.md parsing
# ---------------------------------------------------------------------------

# Section names we care about. Each maps to a canonical intent_type.
_PLAN_SECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^hypothes", re.IGNORECASE), "hypothesis"),
    (re.compile(r"^research\s+question", re.IGNORECASE), "hypothesis"),
    (re.compile(r"^aims?\b", re.IGNORECASE), "hypothesis"),
    (re.compile(r"^objectives?", re.IGNORECASE), "hypothesis"),
    (re.compile(r"^methods?\b", re.IGNORECASE), "methods"),
    (re.compile(r"^materials\s+and\s+methods", re.IGNORECASE), "methods"),
    (re.compile(r"^approach\b", re.IGNORECASE), "methods"),
    (re.compile(r"^planned\s+methods?", re.IGNORECASE), "methods"),
    (re.compile(r"^analysis\s+plan", re.IGNORECASE), "analysis_plan"),
    (re.compile(r"^statistical\s+(?:methods?|analysis|plan)", re.IGNORECASE), "analysis_plan"),
    (re.compile(r"^study\s+design", re.IGNORECASE), "methods"),
    (re.compile(r"^datasets?\b", re.IGNORECASE), "methods"),
    (re.compile(r"^success\s+criteria", re.IGNORECASE), "analysis_plan"),
]


def _classify_plan_section(name: str) -> Optional[str]:
    for pat, kind in _PLAN_SECTION_PATTERNS:
        if pat.match(name.strip()):
            return kind
    return None


def parse_research_plan(plan_text: str) -> list[PlanIntent]:
    """Extract design-intent sections from RESEARCH_PLAN.md.

    Walks H1 and H2 headers; each section that matches a known intent
    pattern is captured with its full body text.
    """
    intents: list[PlanIntent] = []
    lines = plan_text.split("\n")
    current_name = ""
    current_lines: list[str] = []

    def flush() -> None:
        if not current_name and not current_lines:
            return
        kind = _classify_plan_section(current_name)
        if kind is None:
            return
        body = "\n".join(current_lines).strip()
        if not body:
            return
        intents.append(PlanIntent(
            section_name=current_name,
            intent_type=kind,
            text=body,
        ))

    for line in lines:
        m = re.match(r"^(#{1,2})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            current_name = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return intents


# ---------------------------------------------------------------------------
# Package-version extraction
# ---------------------------------------------------------------------------

# requirements.txt: handles `pkg`, `pkg==1.2`, `pkg>=1.2,<2.0`, `pkg~=1.2`.
# Captures the version pin as written; downstream consumers can decide how to
# format it for Methods (e.g., display ">=1.11" vs "1.11.x").
_REQ_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][\w\-]*)\s*(\[[^\]]+\])?\s*"  # name + optional extras
    r"((?:[<>=!~]+\s*[\w\.\-]+(?:\s*,\s*[<>=!~]+\s*[\w\.\-]+)*))?"  # version specs
    r"(?:\s*#.*)?$"  # optional comment
)


def parse_requirements_txt(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or line.startswith("/") or line.startswith("git+"):
            continue  # editable installs, paths, vcs urls — skip
        m = _REQ_LINE_RE.match(line)
        if m:
            name = m.group(1).lower()
            version = (m.group(3) or "").strip() or "(unpinned)"
            out[name] = version
    return out


def parse_pyproject_toml_dependencies(text: str) -> dict[str, str]:
    """Best-effort extraction of [project] dependencies from a pyproject.toml.

    Doesn't use the tomllib / tomli dep (we want zero new deps for this tool).
    Looks for a `dependencies = [...]` block under [project] using a simple
    regex; sufficient for ~95% of real-world pyproject files.
    """
    out: dict[str, str] = {}
    # Find the [project] table's dependencies array.
    proj_match = re.search(
        r"\[project\][\s\S]*?dependencies\s*=\s*\[([\s\S]*?)\]",
        text,
    )
    if not proj_match:
        return out
    body = proj_match.group(1)
    for raw in re.findall(r'"([^"]+)"|\'([^\']+)\'', body):
        spec = raw[0] or raw[1]
        m = _REQ_LINE_RE.match(spec)
        if m:
            name = m.group(1).lower()
            version = (m.group(3) or "").strip() or "(unpinned)"
            out[name] = version
    return out


def parse_environment_yml(text: str) -> dict[str, str]:
    """Best-effort extraction of conda environment.yml dependencies."""
    out: dict[str, str] = {}
    in_deps = False
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^dependencies\s*:\s*$", stripped):
            in_deps = True
            continue
        if in_deps:
            if not stripped or not stripped.startswith("-"):
                if stripped and not stripped.startswith(" "):
                    in_deps = False
                continue
            spec = stripped.lstrip("- ").strip()
            # conda format: "pkg=1.2.3" or "pkg=1.2"
            # pip subkey: "pip:" → entries are pip-formatted
            if spec == "pip:":
                continue
            # Handle conda's `=` separator
            m = re.match(r"^([A-Za-z_][\w\-]*)(?:\s*[=<>!~]+\s*([\w\.\-]+))?", spec)
            if m:
                name = m.group(1).lower()
                version = m.group(2) if m.group(2) else "(unpinned)"
                out[name] = version
    return out


def collect_package_versions(project_dir: Path) -> PackageVersions:
    pv = PackageVersions()
    req = project_dir / "requirements.txt"
    if req.is_file():
        try:
            pv.from_requirements_txt = parse_requirements_txt(
                req.read_text(encoding="utf-8")
            )
        except OSError:
            pass
    pyp = project_dir / "pyproject.toml"
    if pyp.is_file():
        try:
            pv.from_pyproject_toml = parse_pyproject_toml_dependencies(
                pyp.read_text(encoding="utf-8")
            )
        except OSError:
            pass
    env = project_dir / "environment.yml"
    if env.is_file():
        try:
            pv.from_environment_yml = parse_environment_yml(
                env.read_text(encoding="utf-8")
            )
        except OSError:
            pass
    return pv


# ---------------------------------------------------------------------------
# Top-level extraction
# ---------------------------------------------------------------------------

def find_notebooks(project_dir: Path) -> list[Path]:
    """Find all *.ipynb files under project_dir/notebooks/ and project_dir/."""
    patterns = ["notebooks/*.ipynb", "*.ipynb", "src/*.ipynb", "analysis/*.ipynb"]
    found: set[Path] = set()
    for pat in patterns:
        for p in project_dir.glob(pat):
            if not p.name.startswith("."):
                found.add(p)
    return sorted(found)


def find_scripts(project_dir: Path) -> list[Path]:
    """Find standalone analysis .py scripts. Skips __init__.py and conftest.py."""
    skip_names = {"__init__.py", "conftest.py", "setup.py"}
    patterns = ["*.py", "src/*.py", "analysis/*.py", "scripts/*.py"]
    found: set[Path] = set()
    for pat in patterns:
        for p in project_dir.glob(pat):
            if p.name in skip_names or p.name.startswith("."):
                continue
            found.add(p)
    return sorted(found)


def extract_methods(project_dir: Path) -> MethodsExtractReport:
    """Run all extraction against a project directory."""
    report = MethodsExtractReport(project_dir=str(project_dir), research_plan_found=False)

    plan_path = project_dir / "RESEARCH_PLAN.md"
    if plan_path.is_file():
        report.research_plan_found = True
        try:
            report.plan_intents = parse_research_plan(
                plan_path.read_text(encoding="utf-8")
            )
        except OSError:
            pass

    for nb in find_notebooks(project_dir):
        report.notebooks.append(extract_notebook_methods(nb, project_dir))

    for sc in find_scripts(project_dir):
        report.scripts.append(extract_script_methods(sc, project_dir))

    report.package_versions = collect_package_versions(project_dir)
    return report


# ---------------------------------------------------------------------------
# methods_provenance.md formatter
# ---------------------------------------------------------------------------

def format_methods_provenance_md(report: MethodsExtractReport) -> str:
    """Render the report as a human-readable methods_provenance.md.

    Each Methods statement traces to a notebook+cell+line OR a plan section.
    The Methods agent (Phase 3) consumes this to ground its prose.
    """
    out: list[str] = []
    out.append("# Methods Provenance")
    out.append("")
    out.append(
        f"Auto-generated from `extract_methods.py` over `{report.project_dir}`. "
        f"Each fact below traces to a notebook cell or RESEARCH_PLAN section. "
        f"The Methods agent (Phase 3) uses this as the factual basis for the "
        f"Methods section; it MUST NOT claim any method that cannot be "
        f"pointed to here."
    )
    out.append("")

    # Plan intents
    out.append("## Design Intent (from RESEARCH_PLAN.md)")
    out.append("")
    if not report.research_plan_found:
        out.append(
            "**RESEARCH_PLAN.md not found.** Methods grounding will be "
            "execution-only; design rationale will be incomplete in the "
            "Methods section. See SPEC §3.0.1."
        )
        out.append("")
    elif not report.plan_intents:
        out.append(
            "RESEARCH_PLAN.md is present but contains no recognized "
            "intent sections (Hypothesis / Methods / Analysis Plan). "
            "See SPEC §3.0.1 for the expected structure."
        )
        out.append("")
    else:
        for intent in report.plan_intents:
            out.append(
                f"### {intent.section_name} _(intent: {intent.intent_type})_"
            )
            out.append("")
            # Truncate very long sections to keep the file readable;
            # downstream agents can re-read the plan for full context.
            text = intent.text
            if len(text) > 1500:
                text = text[:1500] + "\n\n_(truncated; full text in RESEARCH_PLAN.md)_"
            out.append(text)
            out.append("")

    # Statistical tests across all notebooks
    out.append("## Statistical Tests Detected")
    out.append("")
    all_tests = [
        (nb, t) for nb in (report.notebooks + report.scripts) for t in nb.statistical_tests
    ]
    if not all_tests:
        out.append("_(none detected in this project's notebooks/scripts)_")
        out.append("")
    else:
        # Group by canonical test name
        by_name: dict[str, list[tuple[NotebookMethods, StatisticalTestCall]]] = {}
        for nb, t in all_tests:
            by_name.setdefault(t.test_name, []).append((nb, t))
        for name in sorted(by_name):
            entries = by_name[name]
            out.append(f"### {name}")
            out.append("")
            for nb, t in entries:
                kw_str = ""
                if t.keyword_args:
                    items = ", ".join(f"{k}={v}" for k, v in t.keyword_args.items())
                    kw_str = f" — kw: {items}"
                out.append(
                    f"- `{t.library_path}` in **{nb.path}** "
                    f"(cell {t.cell}, line {t.line}){kw_str}"
                )
            out.append("")

    # Software versions
    out.append("## Software and Versions")
    out.append("")
    pkgs = report.package_versions.all_packages()
    if not pkgs:
        out.append(
            "_(no requirements.txt / pyproject.toml / environment.yml "
            "found at project root)_"
        )
        out.append("")
    else:
        for name in sorted(pkgs):
            sources = []
            if name in report.package_versions.from_requirements_txt:
                sources.append("requirements.txt")
            if name in report.package_versions.from_pyproject_toml:
                sources.append("pyproject.toml")
            if name in report.package_versions.from_environment_yml:
                sources.append("environment.yml")
            src_str = ", ".join(sources)
            out.append(f"- **{name}** {pkgs[name]}  _(from {src_str})_")
        out.append("")

    # Imports per notebook (compact)
    out.append("## Imports by Notebook")
    out.append("")
    for nb in report.notebooks:
        if not nb.imports:
            continue
        modules = sorted({i.module for i in nb.imports})
        out.append(f"- **{nb.path}**: {', '.join(modules)}")
    out.append("")

    # Spark queries
    spark_total = sum(len(nb.spark_queries) for nb in report.notebooks)
    if spark_total > 0:
        out.append("## Spark / K-BERDL Queries")
        out.append("")
        for nb in report.notebooks:
            for q in nb.spark_queries:
                out.append(f"### {nb.path}, cell {q.cell}, line {q.line}")
                out.append("")
                out.append(f"_{q.execution_context}_")
                out.append("")
                excerpt = q.query_excerpt.strip()
                out.append("```sql")
                out.append(excerpt)
                if q.full_length > len(q.query_excerpt):
                    out.append(
                        f"-- (truncated; full query is {q.full_length} chars)"
                    )
                out.append("```")
                out.append("")

    # Parameter constants
    constants = [
        (nb, c) for nb in (report.notebooks + report.scripts) for c in nb.constants
    ]
    if constants:
        out.append("## Parameters and Thresholds")
        out.append("")
        for nb, c in constants:
            out.append(
                f"- `{c.name} = {c.value_repr}` in **{nb.path}** "
                f"(cell {c.cell}, line {c.line})"
            )
        out.append("")

    # Parse errors
    errors = [(nb, e) for nb in report.notebooks for e in nb.parse_errors]
    if errors:
        out.append("## Parse Errors")
        out.append("")
        out.append(
            "The following cells/scripts could not be AST-parsed. Methods "
            "claims that depend on these sources need manual verification."
        )
        out.append("")
        for nb, err in errors:
            out.append(f"- **{nb.path}**: {err}")
        out.append("")

    # Summary
    s = report.to_dict()["summary"]
    out.append("## Summary")
    out.append("")
    out.append(f"- Notebooks scanned: {s['notebook_count']}")
    out.append(f"- Scripts scanned: {s['script_count']}")
    out.append(f"- Code cells total: {s['code_cells_total']}")
    out.append(f"- Code cells skipped (parse errors): {s['code_cells_skipped']}")
    out.append(
        f"- Statistical test calls: {s['statistical_test_calls_total']} "
        f"({len(s['statistical_test_names_unique'])} unique)"
    )
    out.append(f"- Modules imported (unique): {len(s['modules_imported_unique'])}")
    out.append(f"- Spark queries: {s['spark_query_count']}")
    out.append(f"- Packages with version info: {s['package_count']}")
    out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="extract_methods.py",
        description=(
            "Extract Methods-grounding facts from a BERIL project: "
            "imports, statistical-test calls, package versions, Spark "
            "queries, parameter constants, plan-intent sections. Writes "
            "JSON to stdout and (optionally) methods_provenance.md to "
            "--output-dir."
        ),
    )
    p.add_argument(
        "project_dir",
        type=Path,
        help="Path to the BERIL project directory (projects/<id>/).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write methods_provenance.md (default: do not "
            "write a file; JSON-only mode)."
        ),
    )
    p.add_argument(
        "--json-only",
        action="store_true",
        help=(
            "Suppress methods_provenance.md write even if --output-dir set."
        ),
    )
    p.add_argument(
        "--no-md",
        action="store_true",
        help="Same as --json-only (alias).",
    )
    args = p.parse_args(argv)

    if not args.project_dir.is_dir():
        print(
            f"Error: project_dir does not exist or is not a directory: "
            f"{args.project_dir}",
            file=sys.stderr,
        )
        return 1

    notebooks = find_notebooks(args.project_dir)
    scripts = find_scripts(args.project_dir)
    if not notebooks and not scripts:
        print(
            f"Error: no .ipynb notebooks or .py scripts found under "
            f"{args.project_dir} (looked in: notebooks/, src/, analysis/, "
            f"scripts/, and project root).",
            file=sys.stderr,
        )
        return 1

    report = extract_methods(args.project_dir)
    payload = json.dumps(report.to_dict(), indent=2)
    sys.stdout.write(payload + "\n")

    suppress_md = args.json_only or args.no_md
    if args.output_dir is not None and not suppress_md:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = args.output_dir / "methods_provenance.md"
        md_path.write_text(format_methods_provenance_md(report), encoding="utf-8")
        print(f"Wrote methods_provenance.md to {md_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
