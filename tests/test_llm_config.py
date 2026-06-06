"""Unit tests for the CRAFT runtime-config resolver (llm_config).

Pure-function coverage — no network, no LLM, no files. This is the bulk of
the rigor for the config change (CRAFT-CONTRACT §3.4); live smokes stay
minimal and gated.
"""

from __future__ import annotations

import pytest

from beril_paper_writer import llm_config as lc

# CBORG serves up to opus-4-7 in this fixture (mirrors the real lag behind
# Anthropic's opus-4-8) — the scenario the live smoke exposed.
CBORG_AVAILABLE = [
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-7-high",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "gpt-5.5",
]


# --- provider inference -----------------------------------------------------


def test_explicit_provider_wins():
    assert lc.infer_provider({"ACTIVE_PROVIDER": "anthropic", "CBORG_API_KEY": "x"}) == "anthropic"


def test_infer_cborg_from_key():
    assert lc.infer_provider({"CBORG_API_KEY": "x"}) == "cborg"


def test_infer_anthropic_from_key():
    assert lc.infer_provider({"ANTHROPIC_API_KEY": "x"}) == "anthropic"


def test_infer_subscription_when_no_keys():
    assert lc.infer_provider({}) == "subscription"


def test_blank_key_is_unset():
    # a present-but-empty CBORG_API_KEY must not infer cborg
    assert lc.infer_provider({"CBORG_API_KEY": "   "}) == "subscription"


def test_invalid_provider_raises():
    with pytest.raises(lc.ConfigError):
        lc.infer_provider({"ACTIVE_PROVIDER": "bedrock"})


# --- base URL split ---------------------------------------------------------


def test_bare_host_strips_v1():
    assert (
        lc.bare_host({"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"})
        == "https://api.cborg.lbl.gov"
    )


def test_bare_host_default():
    assert lc.bare_host({}) == lc.CBORG_BARE_HOST


# --- app-internal base URL (CRAFT-CONTRACT §3.4 / Stage 6) ------------------
# Symmetric /v1-keeping sibling of bare_host: same user-facing CBORG_BASE_URL
# drives both clients, so app-internal (OpenAI-style) and claude -p
# (Anthropic-style) can never disagree.


def test_app_internal_base_url_keeps_v1_form():
    assert (
        lc.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_bare_host_input_gets_v1():
    # The bugfix case: user set bare host → app-internal call would have
    # hit a /v1-less endpoint and 404'd. Helper appends /v1.
    assert (
        lc.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_trailing_slash_normalized():
    assert (
        lc.app_internal_base_url({"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1/"})
        == "https://api.cborg.lbl.gov/v1"
    )


def test_app_internal_base_url_default():
    assert lc.app_internal_base_url({}) == lc.CBORG_BARE_HOST + "/v1"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov"},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"},
        {"CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1/"},
        {"CBORG_BASE_URL": "https://proxy.example.com/cborg"},
        {"CBORG_BASE_URL": "https://proxy.example.com/cborg/v1"},
    ],
)
def test_app_internal_base_url_equals_bare_host_plus_v1(env):
    """Invariant: app_internal_base_url(env) == bare_host(env) + '/v1'."""
    assert lc.app_internal_base_url(env) == lc.bare_host(env) + "/v1"


# --- newest-model discovery -------------------------------------------------


def test_pick_newest_takes_highest_version():
    assert lc.pick_newest(CBORG_AVAILABLE, "opus") == "claude-opus-4-7"


def test_pick_newest_prefers_non_high():
    assert lc.pick_newest(CBORG_AVAILABLE, "opus") != "claude-opus-4-7-high"


def test_pick_newest_none_when_absent():
    assert lc.pick_newest(["gpt-5.5"], "opus") is None


# --- tier resolution + the drift case ---------------------------------------


def test_unset_tiers_discovered():
    models, unresolved, _ = lc.resolve_tier_models({}, CBORG_AVAILABLE)
    assert models["reasoning"] == "claude-opus-4-7"
    assert models["fast"] == "claude-haiku-4-5"
    assert unresolved == []


def test_pin_respected_when_available():
    models, unresolved, _ = lc.resolve_tier_models(
        {"MODEL_REASONING": "claude-opus-4-6"}, CBORG_AVAILABLE
    )
    assert models["reasoning"] == "claude-opus-4-6"


def test_drift_pin_not_served_is_flagged_not_used():
    # exactly the live smoke: pin opus-4-8, provider only has up to 4-7.
    models, unresolved, warnings = lc.resolve_tier_models(
        {"MODEL_REASONING": "claude-opus-4-8"}, CBORG_AVAILABLE
    )
    assert "reasoning" in unresolved  # flagged for re-pick
    assert "reasoning" not in models  # NOT silently used
    assert any("opus-4-8" in w for w in warnings)


def test_no_discovery_marks_unset_unresolved():
    models, unresolved, _ = lc.resolve_tier_models({}, None)
    assert set(unresolved) == {"reasoning", "standard", "fast"}


# --- full resolve: provider routing ----------------------------------------


def test_resolve_cborg_routes_token_and_base_url():
    r = lc.resolve({"ACTIVE_PROVIDER": "cborg", "CBORG_API_KEY": "sek"}, CBORG_AVAILABLE)
    assert r.public_env["ANTHROPIC_BASE_URL"] == "https://api.cborg.lbl.gov"
    assert r.secret_env["ANTHROPIC_AUTH_TOKEN"] == "sek"  # secret, gitignored
    assert "ANTHROPIC_AUTH_TOKEN" not in r.public_env  # never in committed file
    assert r.public_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-7"


def test_resolve_anthropic_uses_api_key_no_base_url():
    r = lc.resolve({"ACTIVE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "ak"}, CBORG_AVAILABLE)
    assert r.base_url is None
    assert r.secret_env["ANTHROPIC_API_KEY"] == "ak"
    assert "ANTHROPIC_BASE_URL" not in r.public_env


def test_resolve_subscription_is_ambient_with_warning():
    r = lc.resolve({"ACTIVE_PROVIDER": "subscription"}, CBORG_AVAILABLE)
    assert r.secret_env == {}
    assert any("ambient" in w for w in r.warnings)


def test_resolve_cborg_missing_key_raises():
    with pytest.raises(lc.ConfigError):
        lc.resolve({"ACTIVE_PROVIDER": "cborg"}, CBORG_AVAILABLE)


# --- backward-compat: old-style .env upgrades cleanly -----------------------
# Stage 7 / v1.1.0: the contract guarantees an old deployment .env (only
# CBORG_API_KEY, no ACTIVE_PROVIDER, no MODEL_*) upgrades to v1.1.0 without
# breaking. Both halves of that property are already pinned by
# `test_infer_cborg_from_key` (inference) and
# tests/unit/test_configure.py::test_compose_env_append_omits_keys_already_in_env
# (additive-only). This test wires them together on the SAME .env text — the
# realistic deployment scenario — so an "upgrade doesn't break an existing
# deployment" regression hits one named test, not a forensic stitch.


def test_old_style_env_upgrades_cleanly():
    """An old deployment .env has only CBORG_API_KEY (no ACTIVE_PROVIDER,
    no MODEL_*). After v1.1.0 / CRAFT §3.4:
      1. provider inference returns 'cborg' (the user gets the expected backend),
      2. compose_env_append's output does NOT redeclare CBORG_API_KEY (so the
         BERIL-set credential is preserved by python-dotenv's last-write-wins).
    Together: a hub running v1.0.x can re-pipx-install v1.1.0 and re-run
    `beril-paper-writer configure` without touching .env first.
    """
    from beril_paper_writer.commands import configure

    old_style_env = "CBORG_API_KEY=cb-actually-set-by-beril\n"

    # Half 1: provider inference.
    env_map = configure.parse_env_text(old_style_env)
    assert lc.infer_provider(env_map) == "cborg"

    # Half 2: additive-only compose.
    appended = configure.compose_env_append(old_style_env)
    appended_keys = configure.parse_env_text(appended).keys()
    assert "CBORG_API_KEY" not in appended_keys, (
        "v1.1.0 regression: compose_env_append redeclared CBORG_API_KEY on an "
        "old-style .env — would shadow the BERIL-set credential."
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
