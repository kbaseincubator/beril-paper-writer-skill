"""Tests for null-coalescing safety in state accumulators.

Regression tests for triage item A1: state.get("key", default) returns
None (not default) when the key is present with a JSON null value.
See feedback_dict_get_default_vs_null.md.
"""

import json
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helpers via importlib to match the codebase's test convention
# ---------------------------------------------------------------------------
def _import_helpers():
    """Import paper_writer_helpers without package context."""
    import importlib.util

    helpers_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "paper_writer_helpers.py"
    )
    spec = importlib.util.spec_from_file_location("paper_writer_helpers", helpers_path)
    mod = types.ModuleType(spec.name)
    sys.modules[spec.name] = mod  # needed for @dataclass
    spec.loader.exec_module(mod)
    return mod


helpers = _import_helpers()


class TestNullCoalescingCostElapsed:
    """Ensure cost/elapsed accumulators handle JSON null values."""

    @staticmethod
    def _make_args(draft_dir, **kwargs):
        defaults = dict(
            draft_dir=str(draft_dir),
            phase=None,
            throughline_id=None,
            add_cost=None,
            add_elapsed_seconds=None,
            set_field=None,
        )
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_add_cost_when_null_in_state(self, tmp_path):
        """state.json has cost_so_far_usd: null — should not TypeError."""
        (tmp_path / "state.json").write_text(
            json.dumps({"phase": "drafting", "cost_so_far_usd": None, "elapsed_seconds": 0.0}),
            encoding="utf-8",
        )
        args = self._make_args(tmp_path, add_cost="1.50")
        rc = helpers.cmd_update_state(args)
        assert rc == 0

        result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert result["cost_so_far_usd"] == pytest.approx(1.50)

    def test_add_elapsed_when_null_in_state(self, tmp_path):
        """state.json has elapsed_seconds: null — should not TypeError."""
        (tmp_path / "state.json").write_text(
            json.dumps({"phase": "drafting", "cost_so_far_usd": 0.0, "elapsed_seconds": None}),
            encoding="utf-8",
        )
        args = self._make_args(tmp_path, add_elapsed_seconds="42.5")
        rc = helpers.cmd_update_state(args)
        assert rc == 0

        result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert result["elapsed_seconds"] == pytest.approx(42.5)

    def test_add_cost_accumulates_on_existing_value(self, tmp_path):
        """Non-null existing value still accumulates correctly."""
        (tmp_path / "state.json").write_text(
            json.dumps({"phase": "drafting", "cost_so_far_usd": 2.0, "elapsed_seconds": 10}),
            encoding="utf-8",
        )
        args = self._make_args(tmp_path, add_cost="3.0")
        rc = helpers.cmd_update_state(args)
        assert rc == 0

        result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert result["cost_so_far_usd"] == pytest.approx(5.0)

    def test_both_null_simultaneously(self, tmp_path):
        """Both cost and elapsed are null — both should coalesce to 0."""
        (tmp_path / "state.json").write_text(
            json.dumps({"phase": "drafting", "cost_so_far_usd": None, "elapsed_seconds": None}),
            encoding="utf-8",
        )
        args = self._make_args(tmp_path, add_cost="0.75", add_elapsed_seconds="30")
        rc = helpers.cmd_update_state(args)
        assert rc == 0

        result = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert result["cost_so_far_usd"] == pytest.approx(0.75)
        assert result["elapsed_seconds"] == pytest.approx(30.0)
