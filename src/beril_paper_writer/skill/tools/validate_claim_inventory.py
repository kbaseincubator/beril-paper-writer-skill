"""validate_claim_inventory.py — Post-validator for claim_inventory.tsv.

Stage 1 Tier C of STAGED_IMPROVEMENT_PLAN.md. The orchestrator's
phase_triage produces claim_inventory.tsv via an LLM prompt
(extract_claims.v1.md). The LLM emits source_notebook paths that
look plausible but sometimes fabricates names (10% on draft_3:
NB07a_pathway_DA.ipynb instead of NB07a_pathway_DA_H3a_falsifiability.ipynb,
NB07_v18_class_enrichment.ipynb invented, etc.). Without a validator,
the manuscript's provenance chain breaks silently.

Contract:
  - Read the TSV produced by the LLM.
  - For every row with non-empty source_notebook:
      * If it resolves to a real file under project_root / notebooks/:
        leave it alone.
      * Else attempt a bounded, unambiguous repair (Stage 3 Tier I):
          - missing-extension: "<name>" + ".ipynb" is a real file;
          - notebook-ID match: the value's NBxx[L] id (e.g. "NB04",
            "NB07a") matches exactly one real notebook — recovers
            bare stems, stem-plus-parenthetical ("NB07a (pathway_DA)"),
            and wrong-descriptive-suffix ("NB07a_pathway_DA").
        On a repair, rewrite source_notebook to the full real filename
        and prefix notes with "notebook-repaired: <orig> -> <full>".
      * Else (placeholder like "—"/"N/A", slash-joined "NB04b/c", a
        value naming two notebooks, or an id with no/ambiguous match):
        clear source_notebook and prefix notes with
        "unresolved-notebook: <original_value>".
  - Write the cleaned TSV back to the same path (idempotent).
  - Emit a validation report to audit/claim_inventory_validation.json
    with counts + the lists of repaired / orphan source_notebooks.

Stage 3 Tier I (2026-05-12): the repair pass was added after draft_9
of ibd_phage_targeting blew the clear-rate from the ~10% steady-state
band to 76% — the BERIL slash-command run resolved an unpinned model
that emitted bare-stem / em-dash source_notebook values. The model
pin (orchestrator Tier G) is the actual fix; this repair pass is
defense-in-depth that recovers the bulk of such drift regardless of
which prompt version or model is live. The repair is intentionally
conservative: it only fires on an UNAMBIGUOUS single match, so
genuinely-invented names and multi-notebook references still get
cleared, correctly.

What this does NOT do (deferred):
  - source_cell validation (vast majority empty currently).
  - figure_or_table validation (LLM uses non-standard labels like
    "Pillar 1 #3 / §3" — Stage 5 work).
  - source_test column (not in schema yet; Stage 5 reviewability).
  - Substring matching of claim_text against REPORT.md
    (Stage 3 Tier 1 deterministic cross-walks).

Idempotency: re-running on an already-validated TSV is a no-op.
Unresolved rows carry the "unresolved-notebook:" prefix and are
skipped; repaired rows now hold a full real filename in
source_notebook, so they resolve cleanly on the next pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


VERSION = "0.2.0-stage3-tierI"

UNRESOLVED_PREFIX = "unresolved-notebook: "
REPAIRED_PREFIX = "notebook-repaired: "

# Notebook-ID grammar: NB + digits + optional single lowercase letter,
# anchored at the start of the string. "NB07a_pathway_DA" -> "NB07a",
# "NB04" -> "NB04", "NB12_phage_targetability.ipynb" -> "NB12".
_NB_ID_RE = re.compile(r"^NB\d+[a-z]?", re.IGNORECASE)
# A bare "NB<digits>" occurrence — used to detect values that name more
# than one notebook (e.g. "NB04 and NB05"), which cannot be disambiguated.
_NB_ANY_RE = re.compile(r"NB\d", re.IGNORECASE)
# Trailing "(...)" parenthetical the LLM sometimes appends to a stem.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
# Values that are placeholders, not notebook references.
_PLACEHOLDER_VALUES = {"", "-", "—", "–", "n/a", "na", "tbd", "none", "null"}


def _notebook_id(name: str) -> str | None:
    """Extract the canonical notebook ID (NBxx / NBxxL) from a filename
    or a bare reference. Returns the uppercased id, or None if the
    string does not start with a notebook id."""
    m = _NB_ID_RE.match(name.strip())
    return m.group(0).upper() if m else None


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

    # Index of real notebooks: full filenames + an ID -> [full names] map.
    # The ID map detects ambiguity (an id matching >1 real notebook is
    # NOT a safe repair target).
    real_notebooks: list[str] = []
    if notebooks_dir.is_dir():
        real_notebooks = sorted(
            p.name
            for p in notebooks_dir.iterdir()
            if p.suffix == ".ipynb" and p.is_file()
        )
    real_notebooks_set = set(real_notebooks)
    id_index: dict[str, list[str]] = {}
    for nb_name in real_notebooks:
        nid = _notebook_id(nb_name)
        if nid:
            id_index.setdefault(nid, []).append(nb_name)

    # Cache resolutions to avoid recomputing for many identical values.
    # Value -> (status, resolved_name): status in {"ok","repaired","unresolved"}.
    resolution_cache: dict[str, tuple[str, str | None]] = {}

    def _resolve_notebook(nb_value: str) -> tuple[str, str | None]:
        """Classify a source_notebook value.

        Returns (status, resolved_name):
          ("ok", <value>)        — already resolves to a real file as-is.
          ("repaired", <full>)   — recovered, unambiguously, to <full>.
          ("unresolved", None)   — placeholder, ambiguous, or no match.

        Repair is conservative: it only fires on an UNAMBIGUOUS single
        match (missing-extension exact hit, or a notebook-ID matching
        exactly one real notebook). Slash-joined values, values naming
        two notebooks, and placeholders are rejected outright.
        """
        if nb_value in resolution_cache:
            return resolution_cache[nb_value]

        raw = nb_value.strip()

        # 1. Already resolves as-is. Tolerate a leading "notebooks/"
        #    prefix that the LLM sometimes emits and sometimes omits.
        candidate_paths = [
            project_root / raw,
            notebooks_dir / raw,
            notebooks_dir / raw.removeprefix("notebooks/"),
        ]
        if any(p.is_file() for p in candidate_paths):
            result: tuple[str, str | None] = ("ok", raw)
            resolution_cache[nb_value] = result
            return result

        # 2. Hard-reject placeholders and values that cannot be
        #    disambiguated to a single notebook.
        if raw.lower() in _PLACEHOLDER_VALUES:
            result = ("unresolved", None)
            resolution_cache[nb_value] = result
            return result
        if (
            "/" in raw
            or "\\" in raw
            or len(_NB_ANY_RE.findall(raw)) > 1
        ):
            # slash-joined ("NB04b/c") or names two notebooks.
            result = ("unresolved", None)
            resolution_cache[nb_value] = result
            return result

        # 3. Clean: drop a trailing "(...)" parenthetical, a leading
        #    "notebooks/" prefix, and a ".ipynb" extension.
        cleaned = _TRAILING_PAREN_RE.sub("", raw).strip()
        cleaned = cleaned.removeprefix("notebooks/")
        cleaned_noext = (
            cleaned[:-6] if cleaned.endswith(".ipynb") else cleaned
        )

        # 3a. Missing-extension recovery (highest confidence): the value
        #     is a real filename minus the ".ipynb" extension.
        if cleaned_noext + ".ipynb" in real_notebooks_set:
            result = ("repaired", cleaned_noext + ".ipynb")
            resolution_cache[nb_value] = result
            return result

        # 3b. Notebook-ID recovery: the value's NBxx[L] id matches
        #     exactly one real notebook. Recovers bare stems ("NB04"),
        #     stem-plus-parenthetical ("NB07a (pathway_DA)"), and
        #     wrong-descriptive-suffix ("NB07a_pathway_DA").
        nid = _notebook_id(cleaned_noext)
        if nid and len(id_index.get(nid, [])) == 1:
            result = ("repaired", id_index[nid][0])
            resolution_cache[nb_value] = result
            return result

        # 4. No safe resolution.
        result = ("unresolved", None)
        resolution_cache[nb_value] = result
        return result

    invalid_notebooks: list[str] = []  # original values that didn't resolve
    repaired_notebooks: dict[str, str] = {}  # original -> repaired full name
    rows_updated = 0
    rows_repaired = 0
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
        status, resolved = _resolve_notebook(nb)
        if status == "ok":
            continue
        if status == "repaired":
            # Rewrite source_notebook to the full real filename and
            # record the repair in notes (audit trail).
            repaired_notebooks[nb] = resolved  # type: ignore[assignment]
            new_notes = f"{REPAIRED_PREFIX}{nb} -> {resolved}"
            if notes:
                new_notes = f"{new_notes}; {notes}"
            row["notes"] = new_notes
            row["source_notebook"] = resolved  # type: ignore[assignment]
            rows_repaired += 1
            continue
        # status == "unresolved": mark + clear.
        invalid_notebooks.append(nb)
        new_notes = f"{UNRESOLVED_PREFIX}{nb}"
        if notes:
            new_notes = f"{new_notes}; {notes}"
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
        # rows_updated_this_run: rows whose source_notebook was CLEARED
        # because it could not be resolved or repaired. Field name kept
        # for backward compatibility with existing consumers.
        "rows_updated_this_run": rows_updated,
        # rows_repaired_this_run: rows whose source_notebook was
        # REWRITTEN to a full real filename via the Stage 3 Tier I
        # repair pass (the row keeps its provenance link).
        "rows_repaired_this_run": rows_repaired,
        "rows_already_marked_unresolved": rows_already_marked,
        "unique_invalid_notebooks": sorted(set(invalid_notebooks)),
        # repaired_notebooks: {original_value: resolved_full_filename}.
        "repaired_notebooks": dict(sorted(repaired_notebooks.items())),
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
        f"repaired_this_run={diag['rows_repaired_this_run']}, "
        f"cleared_this_run={diag['rows_updated_this_run']}, "
        f"already_marked={diag['rows_already_marked_unresolved']}, "
        f"unique_invalid_notebooks={len(diag['unique_invalid_notebooks'])}\n"
    )
    if diag["repaired_notebooks"]:
        sample = list(diag["repaired_notebooks"].items())[:10]
        sys.stderr.write(
            "  repaired notebooks (first 10): "
            + ", ".join(f"{k} -> {v}" for k, v in sample)
            + "\n"
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
