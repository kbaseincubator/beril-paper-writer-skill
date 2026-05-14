r"""Tests for validate_claim_inventory.py — claim_inventory.tsv post-validator.

Covers the Stage 1 Tier C base behavior (clear + mark unresolvable
source_notebook values) and the Stage 3 Tier I repair pass (recover
bare stems, stem-plus-parenthetical, wrong-descriptive-suffix, and
missing-extension values via an unambiguous notebook-ID match).

Background. draft_9 of ibd_phage_targeting blew the validator's
clear-rate from the ~10% steady-state band to 76% — the BERIL
slash-command run resolved an unpinned model that emitted bare-stem
(`NB04`), stem-plus-parenthetical (`NB07a (pathway_DA)`), and em-dash
(`—`) source_notebook values. The orchestrator model-pin (Tier G) is
the actual fix; this repair pass is defense-in-depth that recovers the
bulk of such drift. The repair is conservative: it only fires on an
UNAMBIGUOUS single match, so genuinely-invented names, slash-joined
references, and placeholders still get cleared.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

_TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "beril_paper_writer"
    / "skill"
    / "tools"
    / "validate_claim_inventory.py"
)


@pytest.fixture(scope="module")
def validator():
    """Load validate_claim_inventory.py as a module for testing."""
    spec = importlib.util.spec_from_file_location(
        "validate_claim_inventory", _TOOL_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Real ibd_phage_targeting notebook names (the ones the draft_9 failure
# modes need to resolve against). Every stem here is unique.
_REAL_NOTEBOOKS = [
    "NB00_data_audit.ipynb",
    "NB01_ecotype_training.ipynb",
    "NB01b_ecotype_refit.ipynb",
    "NB02_ecotype_projection.ipynb",
    "NB04_within_ecotype_DA.ipynb",
    "NB04b_analytical_rigor_repair.ipynb",
    "NB04c_rigor_repair_completion.ipynb",
    "NB07a_pathway_DA_H3a_falsifiability.ipynb",
    "NB07b_stratified_pathway_DA.ipynb",
    "NB12_phage_targetability.ipynb",
]

_HEADER = [
    "claim_id",
    "claim_text",
    "source_notebook",
    "source_cell",
    "figure_or_table",
    "effect_size_present",
    "ci_present",
    "pvalue_present",
    "notes",
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with a real notebooks/ directory."""
    proj = tmp_path / "proj"
    nb_dir = proj / "notebooks"
    nb_dir.mkdir(parents=True)
    for name in _REAL_NOTEBOOKS:
        (nb_dir / name).write_text("{}", encoding="utf-8")
    return proj


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_HEADER, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for r in rows:
            full = {k: "" for k in _HEADER}
            full.update(r)
            w.writerow(full)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _row(claim_id: str, source_notebook: str, notes: str = "") -> dict[str, str]:
    return {
        "claim_id": claim_id,
        "claim_text": f"claim {claim_id}",
        "source_notebook": source_notebook,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# _notebook_id helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # _notebook_id normalizes to uppercase so matching is
        # case-insensitive on both sides of the id_index lookup.
        ("NB04", "NB04"),
        ("NB07a", "NB07A"),
        ("NB07a_pathway_DA", "NB07A"),
        ("NB12_phage_targetability.ipynb", "NB12"),
        ("nb04b", "NB04B"),
        ("—", None),
        ("", None),
        ("random_file.ipynb", None),
    ],
)
def test_notebook_id_extraction(validator, value, expected):
    assert validator._notebook_id(value) == expected


# ---------------------------------------------------------------------------
# Base behavior: valid notebooks pass untouched
# ---------------------------------------------------------------------------


def test_valid_full_filenames_pass_untouched(validator, project, tmp_path):
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [
        _row("C-001", "NB00_data_audit.ipynb"),
        _row("C-002", "NB07a_pathway_DA_H3a_falsifiability.ipynb"),
    ])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_updated_this_run"] == 0
    assert diag["rows_repaired_this_run"] == 0
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB00_data_audit.ipynb"
    assert out[1]["source_notebook"] == "NB07a_pathway_DA_H3a_falsifiability.ipynb"


def test_empty_source_notebook_is_skipped(validator, project, tmp_path):
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_updated_this_run"] == 0
    assert diag["rows_repaired_this_run"] == 0


# ---------------------------------------------------------------------------
# Tier I repair pass: recover the draft_9 failure modes
# ---------------------------------------------------------------------------


def test_repair_bare_stem(validator, project, tmp_path):
    """`NB04` → NB04_within_ecotype_DA.ipynb (unique ID match)."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB04")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 1
    assert diag["rows_updated_this_run"] == 0
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB04_within_ecotype_DA.ipynb"
    assert out[0]["notes"].startswith("notebook-repaired: NB04 -> ")


def test_repair_stem_with_parenthetical(validator, project, tmp_path):
    """`NB07a (pathway_DA)` → strip paren → ID NB07a → unique match."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB07a (pathway_DA)")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB07a_pathway_DA_H3a_falsifiability.ipynb"


def test_repair_wrong_descriptive_suffix(validator, project, tmp_path):
    """draft_8-style `NB07a_pathway_DA.ipynb` → ID NB07a → unique match."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB07a_pathway_DA.ipynb")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB07a_pathway_DA_H3a_falsifiability.ipynb"


def test_repair_missing_extension(validator, project, tmp_path):
    """`NB01_ecotype_training` (no .ipynb) → exact filename + extension."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB01_ecotype_training")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB01_ecotype_training.ipynb"


def test_repair_distinguishes_stem_from_suffixed_siblings(validator, project, tmp_path):
    """`NB04` resolves to NB04_*, NOT to NB04b_* / NB04c_* — exact-ID match,
    not arbitrary prefix."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [
        _row("C-001", "NB04"),
        _row("C-002", "NB04b"),
        _row("C-003", "NB04c"),
    ])
    validator.validate_tsv(tsv, project, None)
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == "NB04_within_ecotype_DA.ipynb"
    assert out[1]["source_notebook"] == "NB04b_analytical_rigor_repair.ipynb"
    assert out[2]["source_notebook"] == "NB04c_rigor_repair_completion.ipynb"


# ---------------------------------------------------------------------------
# Tier I repair pass: still clears genuinely-unresolvable values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("placeholder", ["—", "-", "N/A", "n/a", "TBD", "None"])
def test_placeholder_values_cleared(validator, project, tmp_path, placeholder):
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", placeholder)])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 0
    assert diag["rows_updated_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == ""
    assert out[0]["notes"].startswith("unresolved-notebook: ")


def test_slash_joined_value_cleared(validator, project, tmp_path):
    """`NB04b/c` names two notebooks ambiguously — must NOT be repaired."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB04b/c")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 0
    assert diag["rows_updated_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == ""


def test_value_naming_two_notebooks_cleared(validator, project, tmp_path):
    """A value with >1 NBxx occurrence cannot be disambiguated."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB04 and NB07a")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 0
    assert diag["rows_updated_this_run"] == 1


def test_unknown_notebook_id_cleared(validator, project, tmp_path):
    """`NB99` has no matching real notebook — stays cleared."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB99_imaginary.ipynb")])
    diag = validator.validate_tsv(tsv, project, None)
    assert diag["rows_repaired_this_run"] == 0
    assert diag["rows_updated_this_run"] == 1
    out = _read_tsv(tsv)
    assert out[0]["source_notebook"] == ""
    assert "unresolved-notebook: NB99_imaginary.ipynb" in out[0]["notes"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_on_repaired_rows(validator, project, tmp_path):
    """A repaired row holds a full real filename — second pass is a no-op."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "NB04")])
    validator.validate_tsv(tsv, project, None)
    first = _read_tsv(tsv)
    diag2 = validator.validate_tsv(tsv, project, None)
    second = _read_tsv(tsv)
    assert diag2["rows_repaired_this_run"] == 0
    assert diag2["rows_updated_this_run"] == 0
    assert first == second


def test_idempotent_on_cleared_rows(validator, project, tmp_path):
    """A cleared row has an empty source_notebook on the second pass, so
    it is skipped by the empty-value guard — nothing is re-cleared and
    the file is byte-stable."""
    tsv = tmp_path / "claim_inventory.tsv"
    _write_tsv(tsv, [_row("C-001", "—")])
    validator.validate_tsv(tsv, project, None)
    first = _read_tsv(tsv)
    diag2 = validator.validate_tsv(tsv, project, None)
    second = _read_tsv(tsv)
    assert diag2["rows_updated_this_run"] == 0
    assert diag2["rows_repaired_this_run"] == 0
    assert first == second
    # The row stayed cleared with its unresolved- audit note intact.
    assert second[0]["source_notebook"] == ""
    assert second[0]["notes"].startswith("unresolved-notebook: ")


# ---------------------------------------------------------------------------
# Diagnostic JSON shape (consumer contract — presentation-maker vendors this)
# ---------------------------------------------------------------------------


def test_diagnostic_json_shape(validator, project, tmp_path):
    tsv = tmp_path / "claim_inventory.tsv"
    audit = tmp_path / "audit" / "claim_inventory_validation.json"
    _write_tsv(tsv, [
        _row("C-001", "NB00_data_audit.ipynb"),   # ok
        _row("C-002", "NB04"),                     # repaired
        _row("C-003", "—"),                        # cleared
    ])
    validator.validate_tsv(tsv, project, audit)
    diag = json.loads(audit.read_text(encoding="utf-8"))
    # Backward-compatible fields the presentation-maker team reads.
    assert diag["total_rows"] == 3
    assert diag["rows_updated_this_run"] == 1
    assert diag["unique_invalid_notebooks"] == ["—"]
    # New Tier I fields.
    assert diag["rows_repaired_this_run"] == 1
    assert diag["repaired_notebooks"] == {"NB04": "NB04_within_ecotype_DA.ipynb"}
