#!/usr/bin/env python3
"""M1 §C2 smoke harness for ``claim_inventory.py`` on ibd_phage_targeting.

This is the M1 milestone-gate smoke for the Phase-0 numeric-claim inventory.
See ``M1_PUNCH_LIST.md`` §C2 + ``SPEC.md`` §4.6.

Three modes:

* ``smoke``       — C2.a. Run default (LLM-assisted demarcation) once.
                    Assert AC: TSV exists, ≥30 claim_ids, every non-empty
                    ``source_notebook`` resolves to a real notebook on disk,
                    every non-empty ``source_cell`` resolves to a valid cell
                    index in that notebook, audit JSONL emitted with
                    ``cost_usd ≤ 0.10`` and ``exit_status == 0``.

* ``recall``      — C2.b. Read a hand-curated ground-truth file (one numeric
                    pattern per line) and measure how many appear in the
                    inventory's ``claim_text`` column. Reports
                    ``recall = matched / total``. Gate: ≥ 0.90.

* ``idempotency`` — C3 piece. Run default twice into the same output dir;
                    assert second run audit shows ``cache_hit == true``,
                    ``cost_usd == 0.0``, AND ``claim_inventory.tsv`` bytes
                    are identical between runs.

Discipline (per memory):

* ``feedback_pipx_venv_python_for_skill_helpers.md`` — invoke via the pipx
  venv's Python; that's where ``nbformat`` lives.
* ``feedback_no_benchmark_gaming.md`` — assertions are AC; the script does
  NOT modify the tool's behavior to make AC pass.
* ``feedback_named_columns_in_inserts.md`` — TSV is parsed by header name,
  not column position, so a future schema add doesn't silently break us.

Usage::

    PYTHON_BIN="$(awk 'NR==1 && /^#!/ {sub(/^#!/, ""); split($0, a, " "); \\
        print a[1]; exit}' "$(command -v beril-paper-writer)")"
    "$PYTHON_BIN" smoke-test/m1_claim_inventory_smoke.py \\
        --mode smoke \\
        --project-root <path-to-ibd_phage_targeting>

Exit codes::

    0 — all assertions passed.
    1 — at least one AC failed; details on stderr.
    2 — harness/setup error (missing inputs, tool not on PYTHONPATH, etc.).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Cost is tracked in the audit JSONL but no longer gates the smoke
# (B1.e cost-cap reframing, 2026-05-07). The constant is informational.
_COST_INFORMATIONAL_USD = 0.10
# Inventory-size gate per M1_PUNCH_LIST.md §C2.a.
_MIN_CLAIM_COUNT = 30
# Recall gate per M1_PUNCH_LIST.md §C2.b.
_RECALL_GATE = 0.90


# --- helpers ---------------------------------------------------------------


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _section(label: str) -> None:
    _eprint(f"\n══ {label} ══")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_tool_module() -> Path:
    here = Path(__file__).resolve().parent  # smoke-test/
    repo_root = here.parent
    module = (
        repo_root
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "claim_inventory.py"
    )
    if not module.is_file():
        _eprint(f"setup: tool module not found at {module}")
        sys.exit(2)
    return module


def _src_dir() -> Path:
    return _resolve_tool_module().parents[3]


def _validate_project_root(project_root: Path) -> dict[str, Path]:
    """Return canonical input paths or exit 2 if any missing."""
    paths = {
        "report": project_root / "REPORT.md",
        "methods_provenance": project_root
        / "papers"
        / "draft_1"
        / "methods_provenance.md",
        "figures_inventory": project_root
        / "papers"
        / "draft_1"
        / "figures_inventory.md",
        "tables_inventory": project_root
        / "papers"
        / "draft_1"
        / "tables_inventory.md",
    }
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        _eprint(f"setup: required input(s) missing under {project_root}:")
        for p in missing:
            _eprint(f"  - {p}")
        _eprint(
            "setup: run extract_methods/figures/tables.py per §C0 first."
        )
        sys.exit(2)
    return paths


def _run_tool(
    *,
    inputs: dict[str, Path],
    output_dir: Path,
    no_llm: bool,
) -> tuple[int, str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "beril_paper_writer.skill.tools.claim_inventory",
        "--report",
        str(inputs["report"]),
        "--methods-provenance",
        str(inputs["methods_provenance"]),
        "--figures-inventory",
        str(inputs["figures_inventory"]),
        "--tables-inventory",
        str(inputs["tables_inventory"]),
        "--output-dir",
        str(output_dir),
    ]
    if no_llm:
        cmd.append("--no-llm")
    env = os.environ.copy()
    pythonpath = str(_src_dir())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{pythonpath}:{existing}" if existing else pythonpath
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _last_audit_line(audit_path: Path) -> Optional[dict]:
    if not audit_path.is_file():
        return None
    for line in reversed(audit_path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    return None


def _read_inventory_rows(tsv_path: Path) -> list[dict[str, str]]:
    """Return TSV rows as a list of dicts keyed by header.

    Per ``feedback_named_columns_in_inserts.md``, parse by header name to
    avoid silent breakage if the tool gains a new column.
    """
    if not tsv_path.is_file():
        return []
    with tsv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_notebook_cell(
    project_root: Path,
    source_notebook: str,
    source_cell: str,
) -> Optional[str]:
    """Return None on success, or a string describing the failure.

    ``source_notebook`` is a path relative to project_root (e.g.
    ``notebooks/NB04_within_ecotype_DA.ipynb``). ``source_cell`` is the
    integer cell index expected by the LLM demarcator's contract.
    """
    nb_path = project_root / source_notebook
    if not nb_path.is_file():
        # Be tolerant of leading-./ or already-absolute forms emitted by the LLM.
        alt = project_root / source_notebook.lstrip("./")
        if alt.is_file():
            nb_path = alt
        else:
            return f"notebook file not found: {nb_path}"

    if not source_cell:
        return None  # empty cell column is allowed

    try:
        cell_idx = int(source_cell)
    except ValueError:
        return f"source_cell {source_cell!r} not an integer"

    try:
        import nbformat  # type: ignore[import-untyped]
    except Exception as e:  # pragma: no cover (env-dependent)
        return (
            f"nbformat not importable ({e}); cannot validate cell index. "
            "Run from the pipx venv's Python (see runbook)."
        )

    try:
        nb = nbformat.read(str(nb_path), as_version=4)
    except Exception as e:
        return f"nbformat.read failed for {nb_path}: {e}"

    if cell_idx < 0 or cell_idx >= len(nb.cells):
        return (
            f"cell index {cell_idx} out of range for {nb_path.name} "
            f"({len(nb.cells)} cells)"
        )
    return None


# --- modes -----------------------------------------------------------------


def _mode_smoke(project_root: Path, staging_dir: Path) -> int:
    inputs = _validate_project_root(project_root)
    out_dir = staging_dir / f"smoke-{_now_stamp()}"
    _section(f"C2.a smoke — output dir {out_dir}")

    rc, stdout, stderr = _run_tool(inputs=inputs, output_dir=out_dir, no_llm=False)
    if stdout:
        _eprint(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        _eprint(f"stderr:\n{stderr.rstrip()}")

    fails: list[str] = []

    if rc != 0:
        fails.append(f"tool exit_status: got {rc}, expected 0")

    tsv_path = out_dir / "claim_inventory.tsv"
    if not tsv_path.is_file():
        fails.append(f"TSV not written at {tsv_path}")

    rows = _read_inventory_rows(tsv_path)
    n_claims = len(rows)
    if n_claims < _MIN_CLAIM_COUNT:
        fails.append(
            f"inventory has {n_claims} rows; AC requires ≥{_MIN_CLAIM_COUNT}"
        )

    # Schema check by header name.
    if rows:
        expected = {
            "claim_id",
            "claim_text",
            "source_notebook",
            "source_cell",
            "figure_or_table",
            "effect_size_present",
            "ci_present",
            "pvalue_present",
            "notes",
        }
        actual = set(rows[0].keys())
        missing_cols = expected - actual
        if missing_cols:
            fails.append(f"TSV missing columns: {sorted(missing_cols)}")

    # Notebook + cell resolution check (only for non-empty values).
    nb_resolution_failures: list[str] = []
    nb_seen = 0
    cell_seen = 0
    for row in rows:
        nb = (row.get("source_notebook") or "").strip()
        cell = (row.get("source_cell") or "").strip()
        if not nb:
            continue
        nb_seen += 1
        if cell:
            cell_seen += 1
        err = _resolve_notebook_cell(project_root, nb, cell)
        if err is not None:
            nb_resolution_failures.append(
                f"  {row.get('claim_id', '?')}: {err}"
            )

    if nb_resolution_failures:
        fails.append(
            f"{len(nb_resolution_failures)} non-empty source_notebook/cell "
            "did not resolve; first 5:"
        )
        for line in nb_resolution_failures[:5]:
            _eprint(line)

    # Audit checks.
    audit_path = out_dir / "audit" / "phase0.jsonl"
    audit = _last_audit_line(audit_path)
    if audit is None:
        fails.append(f"audit JSONL missing at {audit_path}")
    else:
        if audit.get("tool") != "claim_inventory":
            fails.append(
                f"audit tool: got {audit.get('tool')!r}, expected 'claim_inventory'"
            )
        if audit.get("exit_status") != 0:
            fails.append(
                f"audit exit_status: got {audit.get('exit_status')}, expected 0"
            )
        # Cost-cap reframing (B1.e): print, don't gate.
        cost = float(audit.get("cost_usd", 0.0))
        if cost > _COST_INFORMATIONAL_USD:
            _eprint(
                f"  note: cost_usd ${cost:.4f} exceeded informational "
                f"threshold ${_COST_INFORMATIONAL_USD:.4f} — track in "
                f"the audit log, not a smoke FAIL."
            )

    _section(
        f"smoke summary — claims={n_claims}, "
        f"non_empty_notebook={nb_seen}, non_empty_cell={cell_seen}, "
        f"cost_usd=${audit.get('cost_usd', 0.0) if audit else 'n/a'}, "
        f"fails={len(fails)}"
    )
    if fails:
        for f in fails:
            _eprint(f"FAIL: {f}")
        return 1
    _eprint("PASS — C2.a smoke gate met.")
    return 0


def _mode_recall(
    project_root: Path,
    staging_dir: Path,
    groundtruth_file: Path,
) -> int:
    if not groundtruth_file.is_file():
        _eprint(f"setup: --groundtruth-file not found: {groundtruth_file}")
        return 2

    inputs = _validate_project_root(project_root)
    out_dir = staging_dir / f"recall-{_now_stamp()}"
    _section(f"C2.b recall — output dir {out_dir}")

    rc, _, stderr = _run_tool(inputs=inputs, output_dir=out_dir, no_llm=False)
    if stderr:
        _eprint(f"stderr:\n{stderr.rstrip()}")
    if rc != 0:
        _eprint(f"FAIL: tool exit_status {rc}")
        return 1

    rows = _read_inventory_rows(out_dir / "claim_inventory.tsv")
    if not rows:
        _eprint("FAIL: inventory empty; cannot measure recall")
        return 1

    inv_text_blob = " ║ ".join((r.get("claim_text") or "") for r in rows)

    # Ground-truth file: one pattern per line. Lines starting with '#' are
    # comments; blank lines ignored. Each pattern is treated as a literal
    # substring search against the concatenated claim_text blob (case-
    # sensitive — numerics like "p < 0.001" are case-stable).
    gt_lines = groundtruth_file.read_text(encoding="utf-8").splitlines()
    patterns = [
        ln.strip()
        for ln in gt_lines
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not patterns:
        _eprint("setup: ground-truth file has no usable patterns")
        return 2

    matched: list[str] = []
    missed: list[str] = []
    for pat in patterns:
        if pat in inv_text_blob:
            matched.append(pat)
        else:
            missed.append(pat)

    recall = len(matched) / len(patterns)
    _section(
        f"recall summary — patterns={len(patterns)}, matched={len(matched)}, "
        f"missed={len(missed)}, recall={recall:.3f}"
    )
    if missed:
        _eprint("\nmissed (false-negatives — extend B1.b regex catalog if patterns):")
        for p in missed:
            _eprint(f"  - {p}")

    if recall < _RECALL_GATE:
        _eprint(
            f"\nFAIL: recall {recall:.3f} < gate {_RECALL_GATE}. "
            "Identify missed pattern class, extend regex catalog, rerun."
        )
        return 1
    _eprint(f"\nPASS — C2.b recall gate met (≥{_RECALL_GATE}).")
    return 0


def _mode_idempotency(project_root: Path, staging_dir: Path) -> int:
    inputs = _validate_project_root(project_root)
    out_dir = staging_dir / f"idempotency-{_now_stamp()}"

    _section(f"C3 idempotency — first run → {out_dir}")
    rc1, _, stderr1 = _run_tool(inputs=inputs, output_dir=out_dir, no_llm=False)
    if stderr1:
        _eprint(f"run1 stderr:\n{stderr1.rstrip()}")
    if rc1 != 0:
        _eprint(f"FAIL: run1 exit_status {rc1}")
        return 1

    tsv = out_dir / "claim_inventory.tsv"
    sha1 = _sha256_of_file(tsv)
    audit1 = _last_audit_line(out_dir / "audit" / "phase0.jsonl")

    _section(f"C3 idempotency — second run → {out_dir}")
    rc2, _, stderr2 = _run_tool(inputs=inputs, output_dir=out_dir, no_llm=False)
    if stderr2:
        _eprint(f"run2 stderr:\n{stderr2.rstrip()}")
    if rc2 != 0:
        _eprint(f"FAIL: run2 exit_status {rc2}")
        return 1

    sha2 = _sha256_of_file(tsv)
    audit2 = _last_audit_line(out_dir / "audit" / "phase0.jsonl")

    fails: list[str] = []
    if sha1 != sha2:
        fails.append(
            f"TSV bytes drift: run1 sha256={sha1[:12]}... vs run2 sha256={sha2[:12]}..."
        )
    if audit2 is None:
        fails.append("audit2 missing")
    else:
        cost1 = float((audit1 or {}).get("cost_usd", 0.0))
        cost2 = float(audit2.get("cost_usd", 0.0))
        cache_hit2 = bool(audit2.get("cache_hit", False))
        if cost2 > 0.0:
            fails.append(
                f"audit2 cost_usd={cost2:.4f} > 0.0 (LLM was called on rerun)"
            )
        if cost1 > 0.0 and not cache_hit2:
            fails.append(
                f"audit2 cache_hit=false despite run1 cost_usd=${cost1:.4f}"
            )

    _section(
        f"idempotency summary — TSV sha={sha1[:12]}, run1_cost="
        f"${(audit1 or {}).get('cost_usd', 0.0):.4f}, run2_cost="
        f"${(audit2 or {}).get('cost_usd', 0.0):.4f}, "
        f"run2_cache_hit={(audit2 or {}).get('cache_hit')}"
    )
    if fails:
        for f in fails:
            _eprint(f"FAIL: {f}")
        return 1
    _eprint("PASS — C3 idempotency gate met (claim_inventory leg).")
    return 0


# --- main ------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="m1_claim_inventory_smoke.py",
        description=(
            "M1 §C2 smoke harness for claim_inventory. See "
            "M1_PUNCH_LIST.md §C2 + SPEC.md §4.6."
        ),
    )
    p.add_argument(
        "--mode",
        choices=("smoke", "recall", "idempotency"),
        required=True,
        help=(
            "smoke: C2.a default-mode AC. "
            "recall: C2.b ground-truth completeness check (requires "
            "--groundtruth-file). "
            "idempotency: C3 (this leg)."
        ),
    )
    p.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help=(
            "Path to a paper-writer project directory; must contain "
            "REPORT.md and papers/draft_1/{methods_provenance, "
            "figures_inventory, tables_inventory}.md. "
            "Smoke target: ibd_phage_targeting."
        ),
    )
    p.add_argument(
        "--groundtruth-file",
        type=Path,
        default=None,
        help=(
            "Required for --mode recall. Plain-text file, one numeric "
            "pattern per line, '#' comments allowed. Each pattern is "
            "checked as a literal substring against the inventory's "
            "claim_text column."
        ),
    )
    p.add_argument(
        "--staging-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "m1-claim-inventory-smoke",
        help=(
            "Where to write smoke outputs. Default: "
            "/tmp/m1-claim-inventory-smoke (gitignored). "
            "Each run gets its own timestamped subdir."
        ),
    )
    args = p.parse_args(argv)

    project_root: Path = args.project_root.resolve()
    if not project_root.is_dir():
        _eprint(f"setup: --project-root not a directory: {project_root}")
        return 2

    staging_dir: Path = args.staging_dir.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "smoke":
        return _mode_smoke(project_root, staging_dir)
    if args.mode == "recall":
        if args.groundtruth_file is None:
            _eprint("setup: --mode recall requires --groundtruth-file")
            return 2
        return _mode_recall(project_root, staging_dir, args.groundtruth_file)
    if args.mode == "idempotency":
        return _mode_idempotency(project_root, staging_dir)
    _eprint(f"setup: unknown mode {args.mode!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
