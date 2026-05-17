#!/usr/bin/env python3
"""M1 §C1 smoke harness for ``discrepancy_register.py`` on ibd_phage_targeting.

This is the M1 milestone-gate smoke for the Phase-0 plan-vs-execution diff
scanner. See ``M1_PUNCH_LIST.md`` §C1 + ``SPEC.md`` §4.5.

Three modes:

* ``smoke``       — C1.a. Run default (LLM-assisted) once. Assert AC:
                    register file exists, ≥1 entry, schema-valid markdown,
                    audit JSONL emitted with ``cost_usd ≤ 0.05`` and
                    ``exit_status == 0``.

* ``ablation``    — C1.b. Run ``--no-llm`` and default into separate output
                    dirs; compute ``delta = E_llm \\ E_strmatch``. Print a
                    summary suitable for pasting into
                    ``M1_PUNCH_LIST_ablation_notes.md``. Does NOT gate on
                    delta non-empty — Q1 verdict requires the hand-list of
                    paraphrase pairs (see ablation report).

* ``idempotency`` — C3 piece. Run default twice into the same output dir;
                    assert second run audit line shows ``cache_hit == true``,
                    ``cost_usd == 0.0``, AND ``discrepancy_register.md``
                    bytes are identical between runs.

Discipline (per memory):

* ``feedback_pipx_venv_python_for_skill_helpers.md`` — invoke via the pipx
  venv's Python, not bare ``python3``. The discover_python_bin trick is
  echoed below in the per-mode runbooks.
* ``feedback_no_benchmark_gaming.md`` — this script asserts AC; it does NOT
  modify the tool's behavior to make AC land.
* ``feedback_sandbox_bash_vs_intermediate_checks.md`` — modes ``smoke`` and
  ``ablation`` require live LLM credentials (default mode of the tool); run
  from Adam's Mac shell, not the sandbox. Mode ``idempotency`` likewise
  exercises the live default path on the first invocation (cached on second).

Usage::

    PYTHON_BIN="$(awk 'NR==1 && /^#!/ {sub(/^#!/, ""); split($0, a, " "); \\
        print a[1]; exit}' "$(command -v beril-paper-writer)")"
    "$PYTHON_BIN" smoke-test/m1_discrepancy_smoke.py \\
        --mode smoke \\
        --project-root <path-to-ibd_phage_targeting>

Exit codes::

    0 — all assertions passed.
    1 — at least one AC failed; details on stderr.
    2 — harness/setup error (missing inputs, tool not on PYTHONPATH, etc.).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Cost is tracked in the audit JSONL but no longer gates the smoke.
# Per Adam 2026-05-07: observability over enforcement during M1;
# tighten from observed data later. The constant is informational.
_COST_INFORMATIONAL_USD = 0.05


# --- helpers ---------------------------------------------------------------


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _section(label: str) -> None:
    _eprint(f"\n══ {label} ══")


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_tool_module() -> Path:
    """Return the path to the ``discrepancy_register.py`` module.

    The harness lives at ``smoke-test/m1_discrepancy_smoke.py``; the tool
    lives at ``src/beril_paper_writer/skill/tools/discrepancy_register.py``.
    Walking from this file is more reliable than guessing the cwd.
    """
    here = Path(__file__).resolve().parent  # smoke-test/
    repo_root = here.parent
    module = (
        repo_root
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "discrepancy_register.py"
    )
    if not module.is_file():
        _eprint(f"setup: tool module not found at {module}")
        sys.exit(2)
    return module


def _src_dir() -> Path:
    return _resolve_tool_module().parents[3]  # .../src


def _validate_project_root(project_root: Path) -> tuple[Path, Path]:
    """Return ``(research_plan_path, methods_provenance_path)`` or exit 2."""
    plan = project_root / "RESEARCH_PLAN.md"
    methods = project_root / "papers" / "draft_1" / "methods_provenance.md"
    missing = [p for p in (plan, methods) if not p.is_file()]
    if missing:
        _eprint(f"setup: required input(s) missing under {project_root}:")
        for p in missing:
            _eprint(f"  - {p}")
        _eprint(
            "setup: run extract_methods.py per M1_PUNCH_LIST.md §C0 first."
        )
        sys.exit(2)
    return plan, methods


def _run_tool(
    *,
    methods_provenance: Path,
    research_plan: Path,
    output_dir: Path,
    no_llm: bool,
) -> tuple[int, str, str]:
    """Invoke discrepancy_register.py via subprocess. Returns (rc, stdout, stderr)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "beril_paper_writer.skill.tools.discrepancy_register",
        "--methods-provenance",
        str(methods_provenance),
        "--research-plan",
        str(research_plan),
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
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    return None


def _entry_count(register_path: Path) -> int:
    if not register_path.is_file():
        return 0
    text = register_path.read_text(encoding="utf-8")
    # Each entry is a level-2 heading "## D-NNN — type: ..."
    return sum(
        1
        for line in text.splitlines()
        if line.startswith("## D-") and " — type: " in line
    )


def _validate_register_schema(register_path: Path) -> list[str]:
    """Per-entry schema validation. Returns list of violations (empty == ok)."""
    if not register_path.is_file():
        return [f"register file not found at {register_path}"]
    text = register_path.read_text(encoding="utf-8")
    if not text.startswith("# Discrepancy Register"):
        return ["register does not start with '# Discrepancy Register' header"]

    violations: list[str] = []
    # Walk entries: each starts with "## D-NNN — type: <kind>"; expect four
    # bullet lines per SPEC §4.5: Plan §X, Execution, Severity, Recommendation.
    lines = text.splitlines()
    i = 0
    entry_idx = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## D-") and " — type: " in line:
            entry_idx += 1
            entry_id = line.split(" — ", 1)[0].lstrip("# ").strip()
            kind = line.split(" — type: ", 1)[1].strip()
            if kind not in (
                "plan-prescribed-not-executed",
                "executed-not-prescribed",
            ):
                violations.append(
                    f"{entry_id}: unknown type '{kind}'"
                )
            # Look at next 6 lines for the four required bullets.
            block = "\n".join(lines[i : i + 8])
            for label in ("Plan", "Execution", "Severity", "Recommendation"):
                if not any(
                    f"- {label}" in ln for ln in lines[i + 1 : i + 8]
                ):
                    violations.append(
                        f"{entry_id}: missing '{label}' bullet line"
                    )
        i += 1
    if entry_idx == 0:
        violations.append("register has zero D-NNN entries")
    return violations


def _entry_keys(register_path: Path) -> set[str]:
    """Return a set of (entry-id, plan-quote, execution-line) tuples for diff.

    Used by ``ablation`` mode to compute set difference between the two runs.
    """
    if not register_path.is_file():
        return set()
    text = register_path.read_text(encoding="utf-8")
    keys: set[str] = set()
    cur_plan: Optional[str] = None
    cur_exec: Optional[str] = None
    cur_kind: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("## D-") and " — type: " in line:
            # Flush previous entry as a key (without its D-id, so identical
            # entries from different runs hash the same).
            if cur_plan is not None and cur_exec is not None:
                keys.add(f"{cur_kind}|{cur_plan}|{cur_exec}")
            cur_kind = line.split(" — type: ", 1)[1].strip()
            cur_plan = None
            cur_exec = None
        elif line.startswith("- Plan"):
            cur_plan = line[len("- Plan") :].strip()
        elif line.startswith("- Execution"):
            cur_exec = line[len("- Execution") :].strip()
    if cur_plan is not None and cur_exec is not None:
        keys.add(f"{cur_kind}|{cur_plan}|{cur_exec}")
    return keys


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# --- modes -----------------------------------------------------------------


def _mode_smoke(project_root: Path, staging_dir: Path) -> int:
    """C1.a — default LLM-assisted run, assert AC."""
    plan, methods = _validate_project_root(project_root)
    out_dir = staging_dir / f"smoke-{_now_stamp()}"
    _section(f"C1.a smoke — output dir {out_dir}")

    rc, stdout, stderr = _run_tool(
        methods_provenance=methods,
        research_plan=plan,
        output_dir=out_dir,
        no_llm=False,
    )
    if stdout:
        _eprint(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        _eprint(f"stderr:\n{stderr.rstrip()}")

    fails: list[str] = []

    # AC: subprocess exit 0
    if rc != 0:
        fails.append(f"tool exit_status: got {rc}, expected 0")

    # AC: register file exists
    register_path = out_dir / "discrepancy_register.md"
    if not register_path.is_file():
        fails.append(f"register not written at {register_path}")

    # AC: ≥1 entry
    n_entries = _entry_count(register_path)
    if n_entries < 1:
        fails.append(f"register has 0 entries; AC requires ≥1")

    # AC: schema-valid markdown
    schema_violations = _validate_register_schema(register_path)
    if schema_violations:
        for v in schema_violations:
            fails.append(f"schema: {v}")

    # AC: audit JSONL emitted with cost ≤ ceiling and exit_status 0
    audit_path = out_dir / "audit" / "phase0.jsonl"
    audit = _last_audit_line(audit_path)
    if audit is None:
        fails.append(f"audit JSONL missing at {audit_path}")
    else:
        if audit.get("tool") != "discrepancy_register":
            fails.append(
                f"audit tool: got {audit.get('tool')!r}, expected 'discrepancy_register'"
            )
        if audit.get("exit_status") != 0:
            fails.append(
                f"audit exit_status: got {audit.get('exit_status')}, expected 0"
            )
        # Cost is recorded for tracking; no gate (B1.e cost-cap reframing).
        cost = float(audit.get("cost_usd", 0.0))
        if cost > _COST_INFORMATIONAL_USD:
            _eprint(
                f"  note: cost_usd ${cost:.4f} exceeded informational "
                f"threshold ${_COST_INFORMATIONAL_USD:.4f} — track in "
                f"the audit log, not a smoke FAIL."
            )

    _section(
        f"smoke summary — entries={n_entries}, "
        f"cost_usd=${audit.get('cost_usd', 0.0) if audit else 'n/a'}, "
        f"fails={len(fails)}"
    )
    if fails:
        for f in fails:
            _eprint(f"FAIL: {f}")
        return 1
    _eprint("PASS — C1.a smoke gate met.")
    return 0


def _mode_ablation(project_root: Path, staging_dir: Path) -> int:
    """C1.b — --no-llm vs default; print delta + summary."""
    plan, methods = _validate_project_root(project_root)
    stamp = _now_stamp()
    nllm_dir = staging_dir / f"ablation-{stamp}-nollm"
    llm_dir = staging_dir / f"ablation-{stamp}-default"

    _section(f"C1.b ablation — --no-llm leg → {nllm_dir}")
    rc1, _, stderr1 = _run_tool(
        methods_provenance=methods,
        research_plan=plan,
        output_dir=nllm_dir,
        no_llm=True,
    )
    if stderr1:
        _eprint(f"--no-llm stderr:\n{stderr1.rstrip()}")
    if rc1 != 0:
        _eprint(f"FAIL: --no-llm leg exit_status {rc1}")
        return 1

    _section(f"C1.b ablation — default (LLM-assisted) leg → {llm_dir}")
    rc2, _, stderr2 = _run_tool(
        methods_provenance=methods,
        research_plan=plan,
        output_dir=llm_dir,
        no_llm=False,
    )
    if stderr2:
        _eprint(f"default stderr:\n{stderr2.rstrip()}")
    if rc2 != 0:
        _eprint(f"FAIL: default leg exit_status {rc2}")
        return 1

    n_strmatch = _entry_count(nllm_dir / "discrepancy_register.md")
    n_llm = _entry_count(llm_dir / "discrepancy_register.md")

    keys_strmatch = _entry_keys(nllm_dir / "discrepancy_register.md")
    keys_llm = _entry_keys(llm_dir / "discrepancy_register.md")

    delta = keys_llm - keys_strmatch
    inverse_delta = keys_strmatch - keys_llm

    audit_strmatch = _last_audit_line(nllm_dir / "audit" / "phase0.jsonl")
    audit_llm = _last_audit_line(llm_dir / "audit" / "phase0.jsonl")
    cost_strmatch = float(audit_strmatch.get("cost_usd", 0.0)) if audit_strmatch else 0.0
    cost_llm = float(audit_llm.get("cost_usd", 0.0)) if audit_llm else 0.0

    _section("C1.b ablation summary")
    _eprint(
        f"  E_strmatch (--no-llm)        : {n_strmatch} entries, "
        f"cost_usd=${cost_strmatch:.4f}"
    )
    _eprint(
        f"  E_llm      (default)         : {n_llm} entries, "
        f"cost_usd=${cost_llm:.4f}"
    )
    _eprint(
        f"  delta = E_llm \\ E_strmatch  : {len(delta)} entries"
    )
    _eprint(
        f"  inverse = E_strmatch \\ E_llm: {len(inverse_delta)} entries"
    )

    if delta:
        _eprint("\ndelta entries (LLM upgraded overlap → discrepancy):")
        for k in sorted(delta):
            _eprint(f"  + {k[:200]}")
    if inverse_delta:
        _eprint("\ninverse entries (string-match found, LLM filtered out):")
        for k in sorted(inverse_delta):
            _eprint(f"  - {k[:200]}")

    # Cost-cap reframing (B1.e): print the spend, don't gate.
    if cost_llm > _COST_INFORMATIONAL_USD:
        _eprint(
            f"  note: default-leg cost ${cost_llm:.4f} exceeded "
            f"informational threshold ${_COST_INFORMATIONAL_USD:.4f}."
        )

    _eprint(
        "\nablation completes. Q1 verdict requires hand-list comparison; "
        "see M1_PUNCH_LIST_ablation_notes.md."
    )
    return 0


def _mode_idempotency(project_root: Path, staging_dir: Path) -> int:
    """C3 piece — second run hits cache, byte-stable output."""
    plan, methods = _validate_project_root(project_root)
    out_dir = staging_dir / f"idempotency-{_now_stamp()}"

    _section(f"C3 idempotency — first run → {out_dir}")
    rc1, _, stderr1 = _run_tool(
        methods_provenance=methods,
        research_plan=plan,
        output_dir=out_dir,
        no_llm=False,
    )
    if stderr1:
        _eprint(f"run1 stderr:\n{stderr1.rstrip()}")
    if rc1 != 0:
        _eprint(f"FAIL: run1 exit_status {rc1}")
        return 1

    register = out_dir / "discrepancy_register.md"
    sha1 = _sha256_of_file(register)
    audit1 = _last_audit_line(out_dir / "audit" / "phase0.jsonl")

    _section(f"C3 idempotency — second run → {out_dir}")
    rc2, _, stderr2 = _run_tool(
        methods_provenance=methods,
        research_plan=plan,
        output_dir=out_dir,
        no_llm=False,
    )
    if stderr2:
        _eprint(f"run2 stderr:\n{stderr2.rstrip()}")
    if rc2 != 0:
        _eprint(f"FAIL: run2 exit_status {rc2}")
        return 1

    sha2 = _sha256_of_file(register)
    audit2 = _last_audit_line(out_dir / "audit" / "phase0.jsonl")

    fails: list[str] = []

    if sha1 != sha2:
        fails.append(
            f"register bytes drift: run1 sha256={sha1[:12]}... vs "
            f"run2 sha256={sha2[:12]}..."
        )
    if audit2 is None:
        fails.append("audit2 missing")
    else:
        # Two cases for cache_hit:
        # - Run1 actually called the LLM (had overlap candidates) → run2
        #   should show cache_hit=true + cost_usd=0.0.
        # - Run1 had zero overlap candidates → no cache write happens → run2
        #   also has zero overlaps and emits cost_usd=0.0 deterministically.
        #   This is still a valid no-LLM-on-rerun outcome; AC is satisfied.
        cost1 = float((audit1 or {}).get("cost_usd", 0.0))
        cost2 = float(audit2.get("cost_usd", 0.0))
        cache_hit2 = bool(audit2.get("cache_hit", False))
        if cost2 > 0.0:
            fails.append(
                f"audit2 cost_usd={cost2:.4f} > 0.0 (LLM was called on rerun)"
            )
        # If run1 had non-zero cost, run2 should be a cache hit.
        if cost1 > 0.0 and not cache_hit2:
            fails.append(
                f"audit2 cache_hit=false despite run1 cost_usd=${cost1:.4f} "
                f"(cache not consulted on rerun)"
            )

    _section(
        f"idempotency summary — register sha={sha1[:12]}, run1_cost="
        f"${(audit1 or {}).get('cost_usd', 0.0):.4f}, run2_cost="
        f"${(audit2 or {}).get('cost_usd', 0.0):.4f}, "
        f"run2_cache_hit={(audit2 or {}).get('cache_hit')}"
    )
    if fails:
        for f in fails:
            _eprint(f"FAIL: {f}")
        return 1
    _eprint("PASS — C3 idempotency gate met (discrepancy_register leg).")
    return 0


# --- main ------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="m1_discrepancy_smoke.py",
        description=(
            "M1 §C1 smoke harness for discrepancy_register. See "
            "M1_PUNCH_LIST.md §C1 + SPEC.md §4.5."
        ),
    )
    p.add_argument(
        "--mode",
        choices=("smoke", "ablation", "idempotency"),
        required=True,
        help=(
            "smoke: C1.a default-mode AC. "
            "ablation: C1.b --no-llm vs default. "
            "idempotency: C3 (this leg)."
        ),
    )
    p.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help=(
            "Path to a paper-writer project directory; must contain "
            "RESEARCH_PLAN.md and papers/draft_1/methods_provenance.md. "
            "Smoke target: ibd_phage_targeting."
        ),
    )
    p.add_argument(
        "--staging-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "m1-discrepancy-smoke",
        help=(
            "Where to write smoke outputs. Default: "
            "/tmp/m1-discrepancy-smoke (gitignored). "
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
    if args.mode == "ablation":
        return _mode_ablation(project_root, staging_dir)
    if args.mode == "idempotency":
        return _mode_idempotency(project_root, staging_dir)
    _eprint(f"setup: unknown mode {args.mode!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
