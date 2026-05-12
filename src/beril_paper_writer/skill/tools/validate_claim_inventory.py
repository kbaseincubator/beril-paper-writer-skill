"""validate_claim_inventory.py — Post-validator for claim_inventory.tsv.

Stage 1 Tier C of STAGED_IMPROVEMENT_PLAN.md. The orchestrator's
phase_triage produces claim_inventory.tsv via an LLM prompt
(extract_claims.v1.md). The LLM emits source_notebook paths that
look plausible but sometimes fabricates names (10% on draft_3:
NB07a_pathway_DA.ipynb instead of NB07a_pathway_DA_H3a_falsifiability.ipynb,
NB07_v18_class_enrichment.ipynb invented, etc.). Without a validator,
the manuscript's provenance chain breaks silently.

Contract (intentionally narrow for Stage 1):
  - Read the TSV produced by the LLM.
  - For every row with non-empty source_notebook:
      * Check (project_root / source_notebook).is_file().
      * If invalid: clear source_notebook and prefix notes with
        "unresolved-notebook: <original_value>".
  - Write the cleaned TSV back to the same path (idempotent).
  - Emit a validation report to audit/claim_inventory_validation.json
    with counts + the list of orphan source_notebooks.

What this does NOT do (deferred):
  - source_cell validation (vast majority empty currently).
  - figure_or_table validation (LLM uses non-standard labels like
    "Pillar 1 #3 / §3" — Stage 5 work).
  - source_test column (not in schema yet; Stage 5 reviewability).
  - Substring matching of claim_text against REPORT.md
    (Stage 3 Tier 1 deterministic cross-walks).

Idempotency: re-running on an already-validated TSV is a no-op
(the unresolved-notebook prefix is already in place; nothing changes).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from datetime import datetime, timezone


VERSION = "0.1.0-stage1-tierC"

UNRESOLVED_PREFIX = "unresolved-notebook: "


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_tsv(
    tsv_path: Path,
    project_root: Path,
    audit_path: Path | None = None,
) -> dict:
    """Validate claim_inventory.tsv in place.

    Returns a diagnostics dict suitable for audit JSON emission.

    Parameters
    ----------
    tsv_path:
        Path to the claim_inventory.tsv produced by extract_claims.
    project_root:
        Project root directory (the parent of `notebooks/`). The
        validator checks `project_root / source_notebook` for each
        row's source_notebook value.
    audit_path:
        Optional path to write a JSON diagnostic. If None, no file
        is written; the diagnostic is only returned.
    """
    if not tsv_path.is_file():
        raise FileNotFoundError(f"claim_inventory.tsv not found: {tsv_path}")

    rows: list[dict[str, str]] = []
    with tsv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(
                f"claim_inventory.tsv has no header row: {tsv_path}"
            )
        rows = list(reader)

    notebooks_dir = project_root / "notebooks"

    # Cache resolutions to avoid stat()-ing the same notebook for many rows.
    resolution_cache: dict[str, bool] = {}

    def _resolves(nb_value: str) -> bool:
        if nb_value in resolution_cache:
            return resolution_cache[nb_value]
        # Tolerate a leading "notebooks/" prefix that the LLM sometimes
        # emits and sometimes omits.
        candidate_paths = [
            project_root / nb_value,
            notebooks_dir / nb_value,
            notebooks_dir / nb_value.removeprefix("notebooks/"),
        ]
        ok = any(p.is_file() for p in candidate_paths)
        resolution_cache[nb_value] = ok
        return ok

    invalid_notebooks: list[str] = []  # original values that didn't resolve
    rows_updated = 0
    rows_already_marked = 0

    for row in rows:
        nb = (row.get("source_notebook") or "").strip()
        if not nb:
            continue
        # Already-marked rows: skip (idempotent).
        notes = (row.get("notes") or "").strip()
        if notes.startswith(UNRESOLVED_PREFIX):
            rows_already_marked += 1
            continue
        if _resolves(nb):
            continue
        # Invalid: mark + clear.
        invalid_notebooks.append(nb)
        existing_notes = (row.get("notes") or "").strip()
        new_notes = f"{UNRESOLVED_PREFIX}{nb}"
        if existing_notes:
            new_notes = f"{new_notes}; {existing_notes}"
        row["notes"] = new_notes
        row["source_notebook"] = ""
        rows_updated += 1

    # Write back idempotently. Always rewrite to keep the file canonical
    # (CSV quoting normalized).
    with tsv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            # Ensure every fieldname is populated (avoid sparse rows).
            for k in fieldnames:
                row.setdefault(k, "")
            writer.writerow(row)

    diagnostic = {
        "tool": "validate_claim_inventory",
        "version": VERSION,
        "timestamp": _utc_now_iso(),
        "input_path": str(tsv_path),
        "project_root": str(project_root),
        "total_rows": len(rows),
        "rows_with_source_notebook": sum(
            1 for r in rows if (r.get("source_notebook") or "").strip()
        ),
        "rows_updated_this_run": rows_updated,
        "rows_already_marked_unresolved": rows_already_marked,
        "unique_invalid_notebooks": sorted(set(invalid_notebooks)),
        "exit_status": 0,
    }

    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return diagnostic


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="validate_claim_inventory.py",
        description=(
            "Stage 1 Tier C validator. Reads claim_inventory.tsv "
            "produced by extract_claims.v1.md (LLM-emitted). Marks rows "
            "whose source_notebook does not resolve to a real file under "
            "<project_root>/notebooks/. See STAGED_IMPROVEMENT_PLAN.md."
        ),
    )
    p.add_argument(
        "--tsv",
        type=Path,
        required=True,
        help="Path to claim_inventory.tsv (will be rewritten in place).",
    )
    p.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help=(
            "Project root containing notebooks/ subdirectory. "
            "Typically <BERIL_PROJECTS>/<project_id>/."
        ),
    )
    p.add_argument(
        "--audit",
        type=Path,
        default=None,
        help=(
            "Optional path to write the JSON diagnostic. Recommended: "
            "<draft_dir>/audit/claim_inventory_validation.json."
        ),
    )
    args = p.parse_args(argv)

    project_root: Path = args.project_root.resolve()
    if not project_root.is_dir():
        sys.stderr.write(
            f"error: --project-root not a directory: {project_root}\n"
        )
        return 2

    try:
        diag = validate_tsv(
            tsv_path=args.tsv.resolve(),
            project_root=project_root,
            audit_path=args.audit.resolve() if args.audit else None,
        )
    except (FileNotFoundError, ValueError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 2

    # Human-readable summary to stderr.
    sys.stderr.write(
        f"validate_claim_inventory: total={diag['total_rows']}, "
        f"with_notebook={diag['rows_with_source_notebook']}, "
        f"marked_this_run={diag['rows_updated_this_run']}, "
        f"already_marked={diag['rows_already_marked_unresolved']}, "
        f"unique_invalid_notebooks={len(diag['unique_invalid_notebooks'])}\n"
    )
    if diag["unique_invalid_notebooks"]:
        sys.stderr.write(
            "  invalid notebooks (first 10): "
            + ", ".join(diag["unique_invalid_notebooks"][:10])
            + "\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
