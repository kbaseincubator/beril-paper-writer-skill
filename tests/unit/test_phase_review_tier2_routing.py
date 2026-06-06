"""Tests for phase_review Tier-2 model routing — Round 2b fixup.

Background. Cowork verification of the Round 2b CRAFT runtime-config
work found that phase_review's Tier-2 "Haiku Light review" routed
via `config.haiku_model`, which defaulted to
`"claude-3-haiku-20240307"` — a Claude-3 model id that CBORG does
NOT serve by that name. Under CBORG (the default provider), the
Tier-2 call silently 404'd. The fixup routes Tier-2 through
`llm_config.pick_tier("fast")` (→ the `"haiku"` alias, resolved by
Claude Code via `ANTHROPIC_DEFAULT_HAIKU_MODEL` in
`<BERIL_ROOT>/.claude/settings.json` to the CBORG-served
`claude-haiku-4-5` line) — same mechanism the rest of the CRAFT skills
use.

Coverage:
  - `Config.haiku_model` defaults to `pick_tier("fast")` (the `"haiku"`
    tier alias) when no env override is set — NOT the Claude-3 literal.
  - `Config.haiku_model` honors an explicit `HAIKU_MODEL` env value as
    a back-compat hatch.
  - A blank `HAIKU_MODEL=` (or whitespace-only) line in .env is treated
    as unset (does NOT shadow the alias with `""`).
  - `Orchestrator._resolve_tier2_model` precedence:
      1. caller-explicit `self.model` wins over everything
      2. then `config.haiku_model` (the env knob)
      3. then `pick_tier("fast")` as the floor
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from beril_paper_writer import llm_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Minimal BERDL-like project so PaperWriterOrchestrator constructs."""
    proj = tmp_path / "test_project"
    proj.mkdir()
    (proj / "REPORT.md").write_text("# Test project\n", encoding="utf-8")
    (proj / "papers").mkdir()
    (proj / "papers" / "draft_1").mkdir()
    return proj


@pytest.fixture
def fresh_config():
    """Force-reload beril_paper_writer.config so module-level env reads run
    against the current monkeypatched environment, not import-time state.

    The module's `config = Config()` singleton is created at import; tests
    that set env vars must reload to see the change.
    """
    import beril_paper_writer.config as cfg_mod

    yield lambda: importlib.reload(cfg_mod)
    # Restore on teardown — re-import once more so subsequent tests see a
    # singleton built against whatever the post-test env looks like.
    importlib.reload(cfg_mod)


# ---------------------------------------------------------------------------
# Config.haiku_model env handling
# ---------------------------------------------------------------------------


def test_haiku_model_defaults_to_fast_tier_alias(monkeypatch, fresh_config):
    """No HAIKU_MODEL env → `Config.haiku_model` is the "haiku" alias,
    NOT the previous Claude-3 literal "claude-3-haiku-20240307".

    This is the bug Cowork caught: the old default was a model id CBORG
    does not serve, causing silent Tier-2 404s under the default
    provider.
    """
    monkeypatch.delenv("HAIKU_MODEL", raising=False)
    cfg_mod = fresh_config()
    assert cfg_mod.config.haiku_model == llm_config.pick_tier("fast")
    assert cfg_mod.config.haiku_model == "haiku"
    # Belt + suspenders: the Claude-3 literal must NOT be the default.
    assert cfg_mod.config.haiku_model != "claude-3-haiku-20240307"


def test_haiku_model_honors_explicit_env_override(monkeypatch, fresh_config):
    """Explicit HAIKU_MODEL=<value> wins (back-compat hatch for users
    who want to pin a specific Haiku revision)."""
    monkeypatch.setenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")
    cfg_mod = fresh_config()
    assert cfg_mod.config.haiku_model == "claude-haiku-4-5-20251001"


def test_haiku_model_blank_env_treated_as_unset(monkeypatch, fresh_config):
    """A blank or whitespace-only HAIKU_MODEL=... line in .env must NOT
    shadow the tier alias with "". (Same discipline as parse_env_text's
    treatment of stripped-empty values throughout llm_config.)"""
    monkeypatch.setenv("HAIKU_MODEL", "   ")
    cfg_mod = fresh_config()
    assert cfg_mod.config.haiku_model == llm_config.pick_tier("fast")
    assert cfg_mod.config.haiku_model == "haiku"


# ---------------------------------------------------------------------------
# Orchestrator._resolve_tier2_model precedence
# ---------------------------------------------------------------------------


def test_resolve_tier2_model_caller_explicit_wins(monkeypatch, project_dir, fresh_config):
    """`self.model` set (caller passed --model) wins over the env knob AND
    the tier alias. Mirrors the precedence used at every other tier-routed
    call site in the orchestrator."""
    monkeypatch.setenv("HAIKU_MODEL", "claude-haiku-4-5-pinned-by-env")
    cfg_mod = fresh_config()
    # Re-import the orchestrator module so its `config` symbol picks
    # up the reloaded singleton.
    import beril_paper_writer.orchestrator as orch_mod

    importlib.reload(orch_mod)

    draft_dir = project_dir / "papers" / "draft_1"
    orch = orch_mod.PaperWriterOrchestrator(
        draft_dir=draft_dir,
        model="claude-opus-4-7",  # caller-explicit override
    )
    assert orch._resolve_tier2_model() == "claude-opus-4-7"
    # Sanity: the env was set; without --model the env would win.
    assert cfg_mod.config.haiku_model == "claude-haiku-4-5-pinned-by-env"


def test_resolve_tier2_model_env_wins_over_tier_alias(monkeypatch, project_dir, fresh_config):
    """No `self.model`, but HAIKU_MODEL set → use the env value."""
    monkeypatch.setenv("HAIKU_MODEL", "claude-haiku-4-5-from-env")
    fresh_config()
    import beril_paper_writer.orchestrator as orch_mod

    importlib.reload(orch_mod)

    draft_dir = project_dir / "papers" / "draft_1"
    orch = orch_mod.PaperWriterOrchestrator(draft_dir=draft_dir)
    assert orch._resolve_tier2_model() == "claude-haiku-4-5-from-env"


def test_resolve_tier2_model_falls_back_to_fast_tier_alias(monkeypatch, project_dir, fresh_config):
    """Neither `self.model` nor HAIKU_MODEL → pick_tier("fast") = "haiku".

    This is the load-bearing case the fixup protects: a fresh BERIL with
    `beril-paper-writer configure` run and no manual env overrides MUST
    route Tier-2 through the alias (resolved to CBORG-served
    claude-haiku-4-5 via settings.json), not the stale Claude-3 literal.
    """
    monkeypatch.delenv("HAIKU_MODEL", raising=False)
    fresh_config()
    import beril_paper_writer.orchestrator as orch_mod

    importlib.reload(orch_mod)

    draft_dir = project_dir / "papers" / "draft_1"
    orch = orch_mod.PaperWriterOrchestrator(draft_dir=draft_dir)
    resolved = orch._resolve_tier2_model()
    assert resolved == llm_config.pick_tier("fast")
    assert resolved == "haiku"
    # Belt + suspenders: never the pre-fixup default.
    assert resolved != "claude-3-haiku-20240307"
