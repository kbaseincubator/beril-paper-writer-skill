"""Unit tests for `beril_paper_writer.commands.configure`.

Covers the pure functions (env parsing, sentinel detection + idempotent
append, settings shaping, gitignore merge, key update/append) and the
live-helper boundaries with HTTP + subprocess mocked. No network, no LLM.
"""

from __future__ import annotations

import json
import os
from unittest import mock

from beril_paper_writer import llm_config
from beril_paper_writer.commands import configure, template_env

# ---------------------------------------------------------------------------
# .env text parsing
# ---------------------------------------------------------------------------


def test_parse_env_text_basic_kv():
    text = "CBORG_API_KEY=abc\nMODEL_REASONING=claude-opus-4-7\n"
    assert configure.parse_env_text(text) == {
        "CBORG_API_KEY": "abc",
        "MODEL_REASONING": "claude-opus-4-7",
    }


def test_parse_env_text_strips_comments_and_blank_lines():
    text = "# a comment\n\nFOO=1\n  # indented comment\nBAR=2\n"
    assert configure.parse_env_text(text) == {"FOO": "1", "BAR": "2"}


def test_parse_env_text_last_write_wins():
    text = "FOO=1\nFOO=2\n"
    assert configure.parse_env_text(text)["FOO"] == "2"


def test_parse_env_text_strips_quotes():
    text = "FOO=\"quoted value\"\nBAR='sq'\n"
    out = configure.parse_env_text(text)
    assert out["FOO"] == "quoted value"
    assert out["BAR"] == "sq"


def test_parse_env_text_ignores_malformed_lines():
    text = "notakv\nFOO=1\n=novalue\n"
    out = configure.parse_env_text(text)
    assert out == {"FOO": "1"}


# ---------------------------------------------------------------------------
# Inline-comment stripping (D)
#
# A naive parser stored a placeholder line like
#   CBORG_API_KEY=   # <-- paste your key
# as a non-empty value, masking the missing-credential failure and causing
# the 401 against CBORG that motivated this fixup. The parser now drops a
# trailing `#` comment when (a) the value is UNQUOTED and (b) the `#` is
# preceded by whitespace (or starts the value). Quoted values keep their
# content verbatim.
# ---------------------------------------------------------------------------


def test_parse_env_text_strips_trailing_comment_from_placeholder():
    """The exact line that caused the 401: a 'paste your key here' placeholder
    parses to empty, NOT the comment string."""
    text = "CBORG_API_KEY=   # <-- paste your CBORG key (cborg)\n"
    out = configure.parse_env_text(text)
    assert out == {"CBORG_API_KEY": ""}


def test_parse_env_text_strips_trailing_comment_from_real_value():
    text = "FOO=bar  # this is a comment\n"
    out = configure.parse_env_text(text)
    assert out == {"FOO": "bar"}


def test_parse_env_text_keeps_hash_inside_unquoted_url():
    """No whitespace before the `#` → the # is part of the value (URL frag)."""
    text = "URL=https://example.com/page#fragment\n"
    out = configure.parse_env_text(text)
    assert out == {"URL": "https://example.com/page#fragment"}


def test_parse_env_text_keeps_hash_inside_quoted_value():
    """Quoted values are taken verbatim — `#` inside quotes is NOT a comment."""
    text = 'FOO="quoted # not a comment"\n'
    out = configure.parse_env_text(text)
    assert out == {"FOO": "quoted # not a comment"}


def test_parse_env_text_quoted_value_ignores_trailing_comment():
    """A `#` AFTER the closing quote is a trailing comment and is dropped."""
    text = 'FOO="value"  # comment outside\n'
    out = configure.parse_env_text(text)
    assert out == {"FOO": "value"}


def test_parse_env_text_strips_value_followed_only_by_spaces():
    """Value followed only by whitespace gets rstripped (regression guard)."""
    text = "FOO=bar   \n"
    out = configure.parse_env_text(text)
    assert out == {"FOO": "bar"}


def test_parse_env_text_empty_value_with_only_a_comment():
    text = "FOO=# just a comment\n"
    out = configure.parse_env_text(text)
    assert out == {"FOO": ""}


# ---------------------------------------------------------------------------
# Sentinel detection + idempotent append
# ---------------------------------------------------------------------------


def test_compose_env_append_empty_env_writes_full_block():
    out = configure.compose_env_append("")
    assert configure.SHARED_OPEN in out
    assert configure.PER_SKILL_MARKER in out
    # exactly what template_env renders with shared=True.
    assert out == template_env.render(include_shared=True)


def test_compose_env_append_with_shared_only_writes_per_skill_only():
    # Mimic the state after presentation-maker configured first.
    text = template_env.SHARED_BLOCK + "\n# (only shared so far)\n"
    out = configure.compose_env_append(text)
    assert configure.SHARED_OPEN not in out
    assert configure.PER_SKILL_MARKER in out
    assert out == template_env.render(include_shared=False)


def test_compose_env_append_idempotent_when_both_present():
    text = template_env.render(include_shared=True)
    assert configure.compose_env_append(text) == ""


def test_compose_env_append_then_again_is_noop():
    """Round-trip: first append produces a state where a second append is empty."""
    text = ""
    first = configure.compose_env_append(text)
    text = text + first
    second = configure.compose_env_append(text)
    assert second == ""


# ---------------------------------------------------------------------------
# Additive-only .env (E.ii): compose_env_append must NEVER re-declare a key
# already present in the user's .env. Re-declaring would shadow values that
# BERIL and other processes already set. CRAFT-CONTRACT §3.4.
# ---------------------------------------------------------------------------


def test_compose_env_append_omits_keys_already_in_env():
    """The exact scenario: a BERIL .env already has CBORG_API_KEY (set up by
    /berdl_start or the operator). configure must NOT emit a CBORG_API_KEY=
    line — that would shadow the existing value with empty."""
    user_env = "CBORG_API_KEY=actually-set-by-beril\n"
    out = configure.compose_env_append(user_env)
    # Whatever the appended block looks like, it must not redeclare CBORG_API_KEY.
    parsed = configure.parse_env_text(out)
    assert "CBORG_API_KEY" not in parsed, (
        "compose_env_append redeclared CBORG_API_KEY despite it being "
        "present in the user's .env — that shadows the existing value."
    )


def test_compose_env_append_omits_keys_for_per_skill_block_too():
    """Same property applies when the shared block is already present and we
    only append the per-skill marker."""
    base = template_env.render(include_shared=True)
    # Mutate to look like the user added their own per-skill placeholder
    # for a future skill. Append the per-skill block and assert no
    # re-declaration of CBORG_API_KEY.
    user_env = base + "\nCBORG_API_KEY=set-elsewhere\n"
    out = configure.compose_env_append(user_env)
    parsed = configure.parse_env_text(out)
    assert "CBORG_API_KEY" not in parsed


def test_compose_env_append_still_emits_keys_not_yet_present():
    """The filter only drops keys ALREADY set; keys the user hasn't declared
    must still land. Otherwise the additive contract is broken in the other
    direction."""
    user_env = "SOME_OTHER_VAR=foo\n"
    out = configure.compose_env_append(user_env)
    # ACTIVE_PROVIDER is in the shared block + not in user_env → must appear.
    parsed = configure.parse_env_text(out)
    assert "ACTIVE_PROVIDER" in parsed


def test_strip_lines_for_keys_already_present_preserves_comments_and_blanks():
    """The filter must keep comments + blank lines + sentinel markers
    verbatim — only `KEY=...` lines whose KEY is in the set get dropped."""
    block = "# >>> sentinel\n# a comment\n\nFOO=keep_me\nBAR=drop_me\n# <<< sentinel\n"
    out = configure._strip_lines_for_keys_already_present(block, {"BAR"})
    assert out == ("# >>> sentinel\n# a comment\n\nFOO=keep_me\n# <<< sentinel\n")


def test_strip_lines_drops_nothing_when_no_overlap():
    block = "FOO=1\nBAR=2\n"
    assert configure._strip_lines_for_keys_already_present(block, set()) == block
    assert configure._strip_lines_for_keys_already_present(block, {"UNRELATED"}) == block


def test_has_shared_block_detects_open_or_close():
    assert configure.has_shared_block("# >>> CRAFT shared config blah\n")
    assert configure.has_shared_block("# <<< CRAFT shared config blah\n")
    assert not configure.has_shared_block("CBORG_API_KEY=x\n")


def test_has_skill_marker_detects_per_skill_line():
    assert configure.has_skill_marker("# --- beril-paper-writer-skill (per-skill) ---\n")
    assert not configure.has_skill_marker("CBORG_API_KEY=x\n")


# ---------------------------------------------------------------------------
# settings shaping
# ---------------------------------------------------------------------------


def test_shape_settings_cborg_splits_token_into_local():
    r = llm_config.resolve(
        {"ACTIVE_PROVIDER": "cborg", "CBORG_API_KEY": "sek"},
        ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    )
    pub, loc = configure.shape_settings(r)
    # public has base URL + tier models, NEVER the token.
    assert pub["env"]["ANTHROPIC_BASE_URL"] == "https://api.cborg.lbl.gov"
    assert "ANTHROPIC_AUTH_TOKEN" not in pub["env"]
    # local has the token, NEVER routing/model env.
    assert loc["env"]["ANTHROPIC_AUTH_TOKEN"] == "sek"
    assert "ANTHROPIC_BASE_URL" not in loc["env"]


def test_shape_settings_anthropic_secret_is_api_key():
    r = llm_config.resolve(
        {"ACTIVE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "ak"},
        ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    )
    pub, loc = configure.shape_settings(r)
    assert loc["env"]["ANTHROPIC_API_KEY"] == "ak"
    assert "ANTHROPIC_API_KEY" not in pub["env"]


def test_shape_settings_subscription_is_empty():
    r = llm_config.resolve({"ACTIVE_PROVIDER": "subscription"}, ["claude-opus-4-7"])
    pub, loc = configure.shape_settings(r)
    assert loc["env"] == {}


# ---------------------------------------------------------------------------
# gitignore merge
# ---------------------------------------------------------------------------


def test_merge_gitignore_appends_when_missing():
    out = configure.merge_gitignore("foo\nbar\n")
    assert out.endswith(configure.GITIGNORE_LINE + "\n")
    assert "foo" in out and "bar" in out


def test_merge_gitignore_idempotent_when_present():
    base = f"foo\n{configure.GITIGNORE_LINE}\nbar\n"
    assert configure.merge_gitignore(base) == base


def test_merge_gitignore_empty_file_gets_line_only():
    out = configure.merge_gitignore("")
    assert out == configure.GITIGNORE_LINE + "\n"


def test_merge_gitignore_no_trailing_newline_in_existing():
    out = configure.merge_gitignore("foo")
    assert out == "foo\n" + configure.GITIGNORE_LINE + "\n"


# ---------------------------------------------------------------------------
# update_or_append_kv (stamping configured-at, persisting picks)
# ---------------------------------------------------------------------------


def test_update_kv_appends_when_absent():
    out = configure.update_or_append_kv("FOO=1\n", "BAR", "2")
    assert "FOO=1\n" in out
    assert out.rstrip("\n").endswith("BAR=2")


def test_update_kv_replaces_existing_line():
    out = configure.update_or_append_kv("FOO=1\nBAR=2\n", "FOO", "9")
    assert "FOO=9" in out
    assert "FOO=1" not in out
    assert "BAR=2" in out


def test_update_kv_preserves_other_lines_and_order():
    text = "A=1\nB=2\nC=3\n"
    out = configure.update_or_append_kv(text, "B", "20")
    assert out == "A=1\nB=20\nC=3\n"


def test_update_kv_handles_no_trailing_newline():
    text = "FOO=1"  # no trailing newline
    out = configure.update_or_append_kv(text, "BAR", "2")
    assert out == "FOO=1\nBAR=2\n"


# ---------------------------------------------------------------------------
# query_provider_models — HTTP mocked
# ---------------------------------------------------------------------------


def _fake_urlopen_response(payload: dict) -> mock.MagicMock:
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    cm.__exit__.return_value = False
    return cm


def test_query_models_subscription_returns_none():
    assert configure.query_provider_models("subscription", {}) is None


def test_query_models_missing_token_returns_none():
    assert configure.query_provider_models("cborg", {}) is None
    assert configure.query_provider_models("anthropic", {}) is None


def test_query_models_cborg_hits_bare_host_v1_models():
    payload = {"data": [{"id": "claude-opus-4-7"}, {"id": "claude-sonnet-4-6"}]}
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        return _fake_urlopen_response(payload)

    env = {"CBORG_API_KEY": "sek", "CBORG_BASE_URL": "https://api.cborg.lbl.gov/v1"}
    with mock.patch.object(configure.urllib.request, "urlopen", fake_urlopen):
        out = configure.query_provider_models("cborg", env)

    assert out == ["claude-opus-4-7", "claude-sonnet-4-6"]
    # contract: bare host (no /v1 doubling)
    assert captured["url"] == "https://api.cborg.lbl.gov/v1/models"
    # bearer token shape (header keys lowercased by urllib)
    auth_header = {k.lower(): v for k, v in captured["headers"].items()}.get("authorization")
    assert auth_header == "Bearer sek"


def test_query_models_anthropic_uses_official_endpoint_and_extra_headers():
    payload = {"data": [{"id": "claude-3-5-sonnet-20240620"}]}
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _fake_urlopen_response(payload)

    with mock.patch.object(configure.urllib.request, "urlopen", fake_urlopen):
        out = configure.query_provider_models("anthropic", {"ANTHROPIC_API_KEY": "ak"})

    assert out == ["claude-3-5-sonnet-20240620"]
    assert captured["url"] == "https://api.anthropic.com/v1/models"
    assert captured["headers"].get("x-api-key") == "ak"
    assert captured["headers"].get("anthropic-version") == "2023-06-01"


def test_query_models_http_error_returns_none():
    def fake_urlopen(req, timeout):
        raise OSError("network unreachable")

    with mock.patch.object(configure.urllib.request, "urlopen", fake_urlopen):
        out = configure.query_provider_models("cborg", {"CBORG_API_KEY": "sek"})
    assert out is None


def test_query_models_unparseable_payload_returns_none():
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = b"not json"
    cm.__exit__.return_value = False
    with mock.patch.object(configure.urllib.request, "urlopen", lambda *_a, **_k: cm):
        out = configure.query_provider_models("cborg", {"CBORG_API_KEY": "sek"})
    assert out is None


# ---------------------------------------------------------------------------
# validation_ping — subprocess mocked; response-validation IS the test
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_validation_ping_accepts_ok_response():
    """Exit 0 + canonical 'ok' body → success."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(0, stdout="ok\n")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("claude-opus-4-7")
    assert ok is True
    assert "ok" in body.lower()


def test_validation_ping_rejects_generic_greeting_at_exit_zero():
    """The verified-fragile case: wrong model on CBORG returns
    a generic greeting at exit 0. Response-validation must catch it."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(
            0,
            stdout="Hello! I'm here to help. What would you like to know?",
        )

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("opus-4-8")
    assert ok is False
    assert "response was not 'ok'" in body


def test_validation_ping_rejects_okay_greeting():
    """Round-1 regression: substring match false-passes on a greeting
    that begins with "Okay,". Equality-after-normalize must reject it."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(
            0,
            stdout="Okay, what would you like to do?",
        )

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("opus-4-8")
    assert ok is False
    assert "response was not 'ok'" in body


def test_validation_ping_rejects_ready_when_you_are():
    """Round-3 regression (the case from the Hub install): a chatty model
    answers `"Ready when you are."` to `reply: ok`. Strict equality must
    catch it — substring-`"ok"` doesn't appear, but a looser any-greeting-
    is-fine check would. The system-prompt + strict equality together are
    what make the ping deterministic."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(0, stdout="Ready when you are.")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("opus-4-8")
    assert ok is False
    assert "response was not 'ok'" in body


def test_validation_ping_accepts_uppercase_ok_with_trailing_period():
    """`OK.` (uppercase + trailing punctuation) is still a clean
    canonical-token reply — the normalize step strips both."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(0, stdout="OK.")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("claude-opus-4-7")
    assert ok is True
    assert "OK" in body


def test_validation_ping_accepts_bare_ok():
    """Bare `ok` (no punctuation) is the canonical happy path."""

    def fake_run(*args, **kwargs):
        return _FakeCompleted(0, stdout="ok")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("claude-opus-4-7")
    assert ok is True
    assert body == "ok"


def test_validation_ping_handles_nonzero_exit():
    def fake_run(*args, **kwargs):
        return _FakeCompleted(1, stderr="model not found")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        ok, body = configure.validation_ping("ghost-model")
    assert ok is False
    assert "(exit 1)" in body


def test_validation_ping_no_claude_on_path():
    with mock.patch.object(configure.shutil, "which", return_value=None):
        ok, body = configure.validation_ping("anything")
    assert ok is False
    assert "claude not on PATH" in body


def test_validation_ping_runs_in_clean_tempdir_not_beril_root(tmp_path):
    """Determinism by clean cwd: the subprocess must run in a tempdir,
    NOT in beril_root, so Claude Code's cwd walk-up doesn't load
    <BERIL_ROOT>/CLAUDE.md or project skills and confound the ping."""
    seen = {}

    def fake_run(*args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return _FakeCompleted(0, stdout="ok")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        configure.validation_ping("opus-4-7")
    # cwd must be a fresh tempdir (not the test's tmp_path, not the BERIL
    # root) — assert by exclusion: it's set, it's not the cwd of the test,
    # and it's not somewhere obviously project-shaped.
    assert seen["cwd"] is not None
    assert seen["cwd"] != str(tmp_path)
    assert "craft-ping-" in seen["cwd"], (
        f"ping cwd {seen['cwd']!r} doesn't look like the TemporaryDirectory "
        "with the craft-ping- prefix — the clean-cwd contract is broken"
    )


def test_validation_ping_injects_resolved_env_explicitly():
    """Determinism by explicit env: the subprocess env must include the
    public_env + secret_env we pass, NOT whatever happens to be in
    os.environ. This pins the property that the ping tests the RESOLVED
    config (the dicts we computed), not the on-disk settings.json."""
    seen = {}

    def fake_run(*args, **kwargs):
        seen["env"] = kwargs.get("env")
        return _FakeCompleted(0, stdout="ok")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        configure.validation_ping(
            "claude-opus-4-7",
            public_env={"ANTHROPIC_BASE_URL": "https://api.cborg.lbl.gov"},
            secret_env={"ANTHROPIC_AUTH_TOKEN": "secret-XYZ"},
        )
    env = seen["env"]
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "https://api.cborg.lbl.gov"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "secret-XYZ"
    # PATH passes through (claude needs it); CLAUDECODE is suppressed.
    assert "PATH" in env
    assert env["CLAUDECODE"] == ""


def test_validation_ping_passes_system_prompt():
    """Determinism by system prompt: the subprocess must include
    --system-prompt with the canonical ok-only instruction. Chatty models
    without it answer "Ready when you are." to `reply: ok`."""
    seen = {}

    def fake_run(cmd, *args, **kwargs):
        seen["cmd"] = list(cmd)
        return _FakeCompleted(0, stdout="ok")

    with (
        mock.patch.object(configure.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(configure.subprocess, "run", fake_run),
    ):
        configure.validation_ping("claude-opus-4-7")
    cmd = seen["cmd"]
    # cmd looks like: [claude, -p, --model, M, --system-prompt, SYS, USER_PROMPT]
    assert "--system-prompt" in cmd
    sp_idx = cmd.index("--system-prompt")
    assert cmd[sp_idx + 1] == configure.PING_SYSTEM_PROMPT
    # The system prompt must constrain to exactly "ok" — that's the
    # whole point of the determinism contract.
    assert "ok" in configure.PING_SYSTEM_PROMPT.lower()
    assert "nothing else" in configure.PING_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# _tier_candidates_newest_first — feeds the run() ping-fallback loop
# ---------------------------------------------------------------------------


def test_tier_candidates_newest_first_orders_plain_then_high():
    """The ordering contract: the function returns the same first element
    as pick_newest, then walks the rest newest-first, with -high siblings
    last (matches pick_newest's preference)."""
    available = [
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-7-high",
        "claude-opus-4-5",
        "claude-sonnet-4-6",
    ]
    chain = configure._tier_candidates_newest_first(available, "opus")
    # Plain variants newest-first, then -high
    assert chain == [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-opus-4-7-high",
    ]
    # First element matches pick_newest's pick — that's the load-bearing
    # contract for the fallback loop.
    assert chain[0] == llm_config.pick_newest(available, "opus")


def test_tier_candidates_newest_first_returns_empty_when_family_absent():
    assert configure._tier_candidates_newest_first(["gpt-5.5"], "opus") == []


def test_tier_candidates_newest_first_only_high_variants():
    """If only -high variants exist, walk those newest-first."""
    available = ["claude-opus-4-6-high", "claude-opus-4-7-high"]
    chain = configure._tier_candidates_newest_first(available, "opus")
    assert chain == ["claude-opus-4-7-high", "claude-opus-4-6-high"]


# ---------------------------------------------------------------------------
# Settings + gitignore I/O
# ---------------------------------------------------------------------------


def test_write_settings_files_creates_claude_dir_and_writes_json(tmp_path):
    settings = {"env": {"ANTHROPIC_BASE_URL": "https://api.cborg.lbl.gov"}}
    settings_local = {"env": {"ANTHROPIC_AUTH_TOKEN": "sek"}}
    s_path, l_path = configure._write_settings_files(tmp_path, settings, settings_local)

    assert s_path == tmp_path / ".claude" / "settings.json"
    assert l_path == tmp_path / ".claude" / "settings.local.json"
    assert json.loads(s_path.read_text()) == settings
    assert json.loads(l_path.read_text()) == settings_local


def test_write_settings_local_file_is_chmod_0600_when_supported(tmp_path):
    pub = {"env": {}}
    loc = {"env": {"ANTHROPIC_AUTH_TOKEN": "sek"}}
    _, l_path = configure._write_settings_files(tmp_path, pub, loc)
    mode = os.stat(l_path).st_mode & 0o777
    # Owner-readable + writable; group/other should NOT have access.
    assert mode == 0o600


def test_write_settings_secret_not_in_public(tmp_path):
    """The split is contract-critical — guard it explicitly."""
    pub = {"env": {"ANTHROPIC_BASE_URL": "https://api.cborg.lbl.gov"}}
    loc = {"env": {"ANTHROPIC_AUTH_TOKEN": "secret-value-XYZ"}}
    s_path, _ = configure._write_settings_files(tmp_path, pub, loc)
    assert "secret-value-XYZ" not in s_path.read_text()


def test_ensure_gitignore_creates_when_absent(tmp_path):
    out = configure._ensure_gitignore(tmp_path)
    gi = tmp_path / ".gitignore"
    assert out == gi
    assert gi.read_text() == configure.GITIGNORE_LINE + "\n"


def test_ensure_gitignore_appends_when_partial(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("foo\n")
    out = configure._ensure_gitignore(tmp_path)
    assert out == gi
    assert gi.read_text() == "foo\n" + configure.GITIGNORE_LINE + "\n"


def test_ensure_gitignore_noop_when_already_present(tmp_path):
    gi = tmp_path / ".gitignore"
    gi.write_text("foo\n" + configure.GITIGNORE_LINE + "\n")
    out = configure._ensure_gitignore(tmp_path)
    assert out is None


# ---------------------------------------------------------------------------
# resolve_unresolved_interactively — TTY pick path (mock input)
# ---------------------------------------------------------------------------


def test_interactive_pick_accepts_index():
    cands = ["claude-opus-4-6", "claude-opus-4-7"]
    with mock.patch.object(configure, "input", create=True, return_value="2"):
        assert configure.interactive_pick("reasoning", "opus", cands) == "claude-opus-4-7"


def test_interactive_pick_accepts_verbatim_id():
    cands = ["claude-opus-4-6", "claude-opus-4-7"]
    with mock.patch.object(configure, "input", create=True, return_value="claude-opus-4-6"):
        assert configure.interactive_pick("reasoning", "opus", cands) == "claude-opus-4-6"


def test_interactive_pick_blank_returns_none():
    cands = ["claude-opus-4-6", "claude-opus-4-7"]
    with mock.patch.object(configure, "input", create=True, return_value=""):
        assert configure.interactive_pick("reasoning", "opus", cands) is None


def test_interactive_pick_eof_returns_none():
    cands = ["claude-opus-4-6"]
    with mock.patch.object(configure, "input", create=True, side_effect=EOFError):
        assert configure.interactive_pick("reasoning", "opus", cands) is None


def test_interactive_pick_retries_on_invalid_then_succeeds():
    cands = ["claude-opus-4-7"]
    inputs = iter(["99", "0", "abc", "1"])

    def feed(_prompt=None):
        return next(inputs)

    with mock.patch.object(configure, "input", create=True, side_effect=feed):
        assert configure.interactive_pick("reasoning", "opus", cands) == "claude-opus-4-7"


def test_resolve_unresolved_interactively_collects_per_tier():
    cands = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]
    # pick index 1 for each tier.
    with mock.patch.object(configure, "input", create=True, return_value="1"):
        picks = configure.resolve_unresolved_interactively({}, cands, ["reasoning", "fast"])
    assert picks == {
        "reasoning": "claude-opus-4-7",
        "fast": "claude-haiku-4-5",
    }


def test_resolve_unresolved_interactively_skips_when_no_candidates(capsys):
    """A tier whose family has zero candidates should be skipped with a
    diagnostic, not crash."""
    # Only opus available; ask about 'fast' (haiku) too — no candidates.
    cands = ["claude-opus-4-7"]
    # input() will only be called for tiers that have candidates.
    with mock.patch.object(configure, "input", create=True, return_value="1"):
        picks = configure.resolve_unresolved_interactively({}, cands, ["reasoning", "fast"])
    assert picks == {"reasoning": "claude-opus-4-7"}
    err = capsys.readouterr().err
    assert "no haiku-class model" in err


# ---------------------------------------------------------------------------
# configure.run robustness — programmatic callers must not AttributeError
# ---------------------------------------------------------------------------


def test_run_reads_optional_flags_via_getattr(tmp_path, monkeypatch):
    """`configure.run` MUST read `no_discover`, `no_ping`, and `yes` via
    getattr so a programmatic caller (e.g. install-skill on an older
    install, an orchestrator from another skill) constructing a bare
    Namespace can't AttributeError on a missing attribute.

    The Hub crash that motivated this fix:
      install_skill.run called configure.run with
      argparse.Namespace(beril_root=..., json=False) — no no_discover,
      no no_ping, no yes. configure.run did `if not args.no_discover`
      which raised AttributeError mid-install.
    """
    # Build a minimal marker-valid BERIL root so discovery succeeds.
    # The .env declares ACTIVE_PROVIDER=subscription so this test is
    # independent of whatever defaults the shared CRAFT block happens to
    # contain at the moment (cborg vs anthropic vs subscription). The test
    # cares ONLY that run() doesn't AttributeError on a bare Namespace.
    root = tmp_path / "synthetic"
    root.mkdir()
    (root / ".env").write_text("# synthetic test BERIL_ROOT\nACTIVE_PROVIDER=subscription\n")
    skills = root / ".claude" / "skills"
    skills.mkdir(parents=True)
    for name in ("submit", "berdl", "suggest-research"):
        d = skills / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"# {name}\n")

    # Patch the I/O surfaces so the test stays fast + offline:
    # - which("claude") → pretend it's there
    # - model-list query → return None (forces resolved/unresolved path)
    # - sys.stdin.isatty → False (non-interactive; fails loud on unresolved)
    monkeypatch.setattr(configure.shutil, "which", lambda _: "/bin/claude")
    monkeypatch.setattr(configure, "query_provider_models", lambda *_a, **_k: None)
    monkeypatch.setattr(configure.sys.stdin, "isatty", lambda: False)

    import argparse as _ap

    # The minimal Namespace a non-CLI caller might construct.
    bare_args = _ap.Namespace(beril_root=str(root))

    # Must not raise AttributeError — that is the regression this guards.
    try:
        rc = configure.run(bare_args)
    except AttributeError as exc:
        raise AssertionError(
            f"configure.run AttributeError'd on a bare Namespace: {exc}. "
            "All optional flags must be read via getattr with a False default."
        ) from exc

    # rc is allowed to be any of the defined exit codes — what we're
    # pinning here is the NO-ATTRIBUTEERROR contract, not a specific
    # outcome. (With no env keys and no discovery, the unresolved-tier
    # path fires and returns 1; that's fine.)
    assert isinstance(rc, int)


# ---------------------------------------------------------------------------
# `configure` argparse shape — positional BERIL_ROOT (CRAFT umbrella + doctor
# both invoke `<cli> configure <BERIL_ROOT>` positionally; the prior flag form
# would `argparse: unrecognized arguments` against any of those callers).
# ---------------------------------------------------------------------------


def test_configure_parser_accepts_positional_beril_root():
    from beril_paper_writer.cli import build_parser

    ns = build_parser().parse_args(["configure", "/tmp/x"])
    assert ns.command == "configure"
    assert ns.beril_root == "/tmp/x"


def test_configure_parser_accepts_bare_invocation():
    from beril_paper_writer.cli import build_parser

    ns = build_parser().parse_args(["configure"])
    assert ns.command == "configure"
    assert ns.beril_root is None
