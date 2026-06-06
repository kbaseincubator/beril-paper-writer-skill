"""`beril-paper-writer configure` — CRAFT runtime-config bootstrapper.

Implements the configure half of CRAFT-CONTRACT §3.4 (Runtime configuration
contract v2) for the paper-writer skill — copied verbatim from the
adversarial canary (the validated reference implementation), with
paper-writer's per-skill marker and a soft `beril-adversarial` preflight
substituted in.

  1. Resolve BERIL_ROOT (via discovery.find_beril_root).
  2. Ensure `<root>/.env` contains the CRAFT shared block + this skill's
     per-skill marker — idempotently (sentinel detection; never duplicate).
  3. Discover the provider's actual model list (CBORG `/v1/models`,
     Anthropic `/v1/models`; subscription has no discovery surface).
  4. Resolve tier-models with `llm_config.resolve(env, available)`. Any
     unresolved tier (no pin, nothing to pick, or pin not served by
     provider) triggers an interactive picker on a TTY or a loud failure
     otherwise — never silent defaults.
  5. Write `<root>/.claude/settings.json` (public, safe to commit) and
     `<root>/.claude/settings.local.json` (secret, gitignored). Ensure the
     gitignore catches the local file.
  6. Run a validation ping that asserts the response token (`reply: ok`
     → must contain `ok`), not just the exit code. A wrong model on CBORG
     returns a generic greeting at exit 0 — the contract requires we
     detect that.
  7. On success, stamp BERIL_PAPER_WRITER_CONFIGURED_AT / _VERSION into
     `.env`.

Preflight (per CRAFT-CONTRACT §3.4 / Round 2b clarification): no separate
audit subcommand — the only paper-writer-specific external CLI that pip
does not ship is `beril-adversarial`, which is a SOFT dependency (the
orchestrator falls back to an inline reviewer when it is absent). Reported
advisory after the ping. The `claude` CLI is already covered by step (0)
of `configure` itself. Python dependencies (nbformat, python-docx) ship
with the package and need no runtime check.

Pure functions sit at the top (parseable env, settings shaping, gitignore
merge, sentinel detection); side-effect orchestration is in `run`. The
unit-test suite targets the pure layer; live smokes exercise the rest.

Exit codes (compatible with the prior shape):
  0 — configure succeeded
  1 — generic failure (write error, ping failure, user abort, etc.)
  3 — claude not on PATH OR BERIL_ROOT not resolvable
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from beril_paper_writer import __version__, discovery, llm_config
from beril_paper_writer.commands import template_env

# ---------------------------------------------------------------------------
# Sentinel constants — must match template_env.SHARED_BLOCK byte-for-byte.
# ---------------------------------------------------------------------------

SHARED_OPEN = "# >>> CRAFT shared config"
SHARED_CLOSE = "# <<< CRAFT shared config"
PER_SKILL_MARKER = "# --- beril-paper-writer-skill (per-skill) ---"

DEFAULT_MODELS_TIMEOUT = 15.0
DEFAULT_PING_TIMEOUT = 60.0
GITIGNORE_LINE = ".claude/settings.local.json"

# Validation-ping prompt + system prompt. Deterministic by construction:
# the system prompt forces the model to emit exactly "ok"; the user prompt
# is a short reminder. The strict match + clean-cwd + explicit env keep the
# ping from being confounded by project context (CLAUDE.md, skills) or by
# chatty-by-default models. Contract §3.4 req 3.2.
PING_PROMPT = "Reply with just: ok"
PING_SYSTEM_PROMPT = "Output exactly the two characters: ok. Nothing else."


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a `.env`-style file (KEY=VAL lines, # comments, blank lines).

    Last-write-wins. Comment-handling matches what every other dotenv parser
    does (python-dotenv, the shell `set -a; source .env`, etc.):

      - A line whose first non-whitespace char is `#` is a whole-line comment.
      - For UNQUOTED values, an inline `#` preceded by whitespace starts a
        trailing comment and is dropped:
            CBORG_API_KEY=   # paste your key      → ""
            FOO=bar  # comment                      → "bar"
            URL=https://example.com/#frag           → "https://example.com/#frag"
            (no whitespace before the #, so it stays)
      - For QUOTED values, the inner content is taken verbatim and anything
        after the closing quote is ignored:
            FOO="quoted # not a comment"  → "quoted # not a comment"

    The verified-fragile case this guards: the round-1 template_env shared
    block had `CBORG_API_KEY=   # <-- paste your CBORG key (cborg)` as a
    placeholder. A naive parser stored the comment as the value and then
    llm_config thought the key was set, masking the missing-credential
    failure and producing a 401 against CBORG.
    """
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        # Strip leading whitespace only; we'll handle the right side ourselves.
        val = val.lstrip()
        if val and val[0] in ("'", '"'):
            quote = val[0]
            close = val.find(quote, 1)
            # No closing quote → take everything after the opener (legacy
            # permissive). Otherwise take the inner content; anything after
            # the close quote (e.g. a trailing comment) is dropped.
            val = val[1:].rstrip() if close == -1 else val[1:close]
        else:
            # Unquoted: a `#` preceded by whitespace starts a trailing
            # comment. Walk char-by-char so we don't accidentally truncate
            # at a `#` inside the value (e.g. URL fragments).
            comment_start = -1
            for i, ch in enumerate(val):
                if ch == "#" and (i == 0 or val[i - 1].isspace()):
                    comment_start = i
                    break
            if comment_start != -1:
                val = val[:comment_start]
            val = val.rstrip()
        env[key] = val
    return env


def has_shared_block(env_text: str) -> bool:
    """True iff the CRAFT shared sentinel is present (open OR close)."""
    return SHARED_OPEN in env_text or SHARED_CLOSE in env_text


def has_skill_marker(env_text: str) -> bool:
    """True iff this skill's per-skill marker is present."""
    return PER_SKILL_MARKER in env_text


def _strip_lines_for_keys_already_present(block: str, already_present: set[str]) -> str:
    """Drop `KEY=...` lines from `block` whose KEY is in `already_present`.

    Comments (including the sentinel lines), blank lines, and any non-KV
    line are preserved verbatim. This is the existence-aware filter behind
    `compose_env_append`'s additive-only contract (§3.4): the shared block
    must never re-declare a key the user's .env already has.
    """
    out: list[str] = []
    for raw_line in block.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(raw_line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in already_present:
            continue
        out.append(raw_line)
    return "".join(out)


def compose_env_append(env_text: str) -> str:
    """Return the text to APPEND to `.env` to make it CRAFT-complete.

    Additive-only contract (CRAFT-CONTRACT §3.4): the appended block must
    never re-declare a key the user's .env already has — re-declaration
    would shadow values BERIL and other processes already set.

    - If the shared sentinel is absent: append the shared block + the
      per-skill block, with any `KEY=...` line removed if that KEY is
      already present in the user's .env.
    - If the shared sentinel is present but the per-skill marker is
      absent: append only the per-skill block (with the same key-filter).
    - If both are present: return empty string (idempotent no-op).

    The shared block carries its own trailing newline; the per-skill block
    starts with a blank line so it lands separated when appended.
    """
    user_keys = set(parse_env_text(env_text).keys())
    if not has_shared_block(env_text):
        block = template_env.render(include_shared=True)
    elif not has_skill_marker(env_text):
        block = template_env.render(include_shared=False)
    else:
        return ""
    return _strip_lines_for_keys_already_present(block, user_keys)


def _tier_candidates_newest_first(available: list[str], family: str) -> list[str]:
    """Return the provider's models for `family`, ordered newest-first with
    non-`-high` variants preferred. Mirrors `pick_newest`'s discipline so a
    ping-fail fallback walks the same ordering: take the first survivor.

    `pick_newest(available, family)` is equivalent to
    `_tier_candidates_newest_first(available, family)[0]` (or None on empty).
    """
    fam = [m for m in available if family in m.lower()]
    if not fam:
        return []
    plain = sorted(
        (m for m in fam if not m.lower().endswith("-high")),
        key=llm_config._version_key,
        reverse=True,
    )
    high = sorted(
        (m for m in fam if m.lower().endswith("-high")),
        key=llm_config._version_key,
        reverse=True,
    )
    # Plain variants first (cheaper / non-reasoning-mode), then -high
    # as a last resort — matching pick_newest's preference.
    return plain + high


def shape_settings(resolved: llm_config.ResolvedConfig) -> tuple[dict, dict]:
    """Shape the two settings files Claude Code reads at process start.

    Returns (settings_json, settings_local_json):

      settings.json  — { "env": <public_env> }   (committed-safe)
      settings.local.json — { "env": <secret_env> }  (gitignored)

    Empty `env` blocks are permitted (e.g., `subscription` mode has no
    public_env). Callers are expected to write both files anyway so a
    later switch back to a key-bearing provider is a one-step rewrite.
    """
    return ({"env": dict(resolved.public_env)}, {"env": dict(resolved.secret_env)})


def merge_gitignore(existing: str, line: str = GITIGNORE_LINE) -> str:
    """Return gitignore text with `line` present; idempotent.

    Treats `existing` as authoritative. If the exact line is already
    present (anywhere, on its own line), returns it unchanged. Otherwise
    appends `line` with a leading newline if the file doesn't end in one.
    """
    lines = existing.splitlines()
    if line in lines:
        return existing
    suffix = "" if existing.endswith("\n") or existing == "" else "\n"
    if existing == "":
        return line + "\n"
    return existing + suffix + line + "\n"


def update_or_append_kv(env_text: str, key: str, value: str) -> str:
    """Set/replace `key=value` in `env_text`, preserving the rest.

    If `key` appears at the start of a line (after optional whitespace), the
    matching line is replaced. Otherwise, `key=value` is appended on a new
    line. Used by the configured-at / configured-version stamp.
    """
    out_lines: list[str] = []
    replaced = False
    for raw in env_text.splitlines(keepends=True):
        stripped = raw.lstrip()
        if stripped.startswith(key + "=") or stripped.startswith(key + " ="):
            indent = raw[: len(raw) - len(stripped)]
            newline = "\n" if raw.endswith("\n") else ""
            out_lines.append(f"{indent}{key}={value}{newline}")
            replaced = True
        else:
            out_lines.append(raw)
    out = "".join(out_lines)
    if not replaced:
        sep = "" if out.endswith("\n") or out == "" else "\n"
        out = f"{out}{sep}{key}={value}\n"
    return out


# ---------------------------------------------------------------------------
# Live helpers (mocked in unit tests; exercised in live smoke)
# ---------------------------------------------------------------------------


def query_provider_models(
    provider: str,
    env: dict[str, str],
    *,
    timeout: float = DEFAULT_MODELS_TIMEOUT,
) -> list[str] | None:
    """Hit the provider's `/v1/models` endpoint, return the list of ids.

    Returns None when discovery is impossible (subscription mode, missing key,
    or HTTP failure caller should report). Caller decides whether to abort
    or fall back to pin-only resolution.
    """
    if provider == "subscription":
        return None
    if provider == "cborg":
        host = llm_config.bare_host(env)
        url = f"{host}/v1/models"
        token = (env.get("CBORG_API_KEY") or "").strip()
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        token = (env.get("ANTHROPIC_API_KEY") or "").strip()
    else:
        return None

    if not token:
        return None

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    # Anthropic also looks at x-api-key in some surfaces; provide both.
    if provider == "anthropic":
        req.add_header("x-api-key", token)
        req.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(
            f"  [WARN] could not query {provider} model list ({exc}); "
            "tiers fall back to pins-only resolution.",
            file=sys.stderr,
        )
        return None
    except (json.JSONDecodeError, KeyError) as exc:
        print(
            f"  [WARN] {provider} model-list response unparseable: {exc}",
            file=sys.stderr,
        )
        return None

    # Both providers return {"data": [{"id": "..."}], ...} (OpenAI-style).
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    return [m["id"] for m in data if isinstance(m, dict) and "id" in m]


def interactive_pick(tier: str, family: str, candidates: list[str]) -> str | None:
    """TTY prompt: pick one from filtered candidates. None on EOF/blank."""
    print(f"\n  tier {tier!r} ({family}) — provider serves these candidates:")
    for i, m in enumerate(candidates, 1):
        print(f"    [{i:>2}] {m}")
    while True:
        try:
            raw = input(f"  pick [1-{len(candidates)}] (Enter to abort): ").strip()
        except EOFError:
            return None
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1]
        # also accept the model id verbatim
        if raw in candidates:
            return raw
        print(f"  not a valid choice; try a number 1..{len(candidates)}")


def resolve_unresolved_interactively(
    env: dict[str, str],
    available: list[str],
    unresolved: list[str],
) -> dict[str, str]:
    """For each unresolved tier, show candidates + collect a pick.

    Returns a mapping {tier: chosen_model} for the tiers the user actually
    picked. A blank/EOF on any prompt leaves that tier unpicked (caller
    decides whether to abort or proceed).
    """
    picks: dict[str, str] = {}
    for tier in unresolved:
        family = llm_config.TIER_FAMILY[tier]
        cands = [m for m in available if family in m.lower()]
        if not cands:
            print(
                f"  [ERROR] provider serves no {family}-class model for "
                f"tier {tier!r}; cannot resolve. Edit .env to pin "
                f"{llm_config.TIER_ENVKEY[tier]} manually.",
                file=sys.stderr,
            )
            continue
        choice = interactive_pick(tier, family, cands)
        if choice is None:
            print(f"  [WARN] no pick for tier {tier!r}; skipping.", file=sys.stderr)
            continue
        picks[tier] = choice
    return picks


def validation_ping(
    model: str,
    *,
    public_env: dict[str, str] | None = None,
    secret_env: dict[str, str] | None = None,
    timeout: float = DEFAULT_PING_TIMEOUT,
) -> tuple[bool, str]:
    """Deterministically ask `claude -p` for the literal `ok`; assert the
    response IS `ok`.

    Three robustness properties (CRAFT-CONTRACT §3.4 req 3.2):

      1. **Determinism by system prompt.** The system prompt FORCES the
         model to output exactly the token `ok`. Chatty-by-default models
         (`"Ready when you are."`, `"Hi!"`, etc.) without the system
         prompt make the ping flaky.

      2. **No project context leakage.** Runs in a clean tempdir as cwd,
         NOT in BERIL_ROOT. Otherwise Claude Code walks up from cwd,
         loads `<BERIL_ROOT>/CLAUDE.md` + project skills, and confounds
         a "reply with just ok" with project context (slash commands,
         skill outputs, etc.).

      3. **Explicit env injection.** `public_env` + `secret_env` (the two
         halves of `llm_config.resolve(...).{public,secret}_env`) are
         passed EXPLICITLY into the subprocess env. We don't rely on
         the new `<BERIL>/.claude/settings.json` being read by the
         subprocess — that's load-bearing for the steady-state, but
         the ping is the test that the SETTINGS WE JUST RESOLVED are
         valid; conflating "did configure write the file" with "does
         the resolved config work" loses information when one of the
         two fails.

    Returns (success, response_text). Success requires exit 0 AND the
    response (lowercased + stripped of surrounding whitespace and
    punctuation) EQUALS the token `ok` — not a substring match.

    `public_env` / `secret_env` default to empty dicts so callers can
    omit them for the subscription / ambient-login path. The subprocess
    inherits PATH / HOME / USER from os.environ (needed for the `claude`
    binary itself to find Node + the user's auth config); everything
    else is injected explicitly so the test is the resolved config and
    nothing else.
    """
    claude = shutil.which("claude")
    if claude is None:
        return False, "(claude not on PATH)"

    public_env = public_env or {}
    secret_env = secret_env or {}

    # Minimal pass-through: just what the `claude` binary itself needs to
    # locate Node + the user's runtime. Everything provider-related must
    # come from public_env + secret_env so the test is the resolved config.
    base_env: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "SHELL", "TMPDIR"):
        val = os.environ.get(key)
        if val is not None:
            base_env[key] = val
    # CLAUDECODE="" suppresses any inherited test-runner Claude Code
    # subagent-detection state.
    base_env["CLAUDECODE"] = ""

    env = {**base_env, **public_env, **secret_env}

    cmd = [
        claude,
        "-p",
        "--model",
        model,
        "--system-prompt",
        PING_SYSTEM_PROMPT,
        PING_PROMPT,
    ]

    try:
        with tempfile.TemporaryDirectory(prefix="craft-ping-") as cwd:
            completed = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return False, "(timeout)"
    except OSError as exc:
        return False, f"(subprocess error: {exc})"

    body = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return False, f"(exit {completed.returncode}) {body.strip()[:400]}"
    # EQUALITY (not substring) against the canonical token. A substring
    # match false-passes on "Okay, ..." which contains "ok".
    stripped = body.strip().lower().strip(string.punctuation + string.whitespace)
    if stripped != "ok":
        return False, f"(response was not 'ok') {body.strip()[:400]}"
    return True, body.strip()[:400]


# ---------------------------------------------------------------------------
# I/O orchestration (kept thin)
# ---------------------------------------------------------------------------


def _write_settings_files(
    beril_root: Path, settings: dict, settings_local: dict
) -> tuple[Path, Path]:
    claude_dir = beril_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"
    local_path = claude_dir / "settings.local.json"
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    local_path.write_text(json.dumps(settings_local, indent=2, sort_keys=True) + "\n")
    with contextlib.suppress(OSError):
        os.chmod(local_path, 0o600)
    return settings_path, local_path


def _ensure_gitignore(beril_root: Path) -> Path | None:
    """Append the settings.local.json line to `<root>/.gitignore` idempotently.

    Returns the gitignore path if any write happened, else None.
    """
    gi = beril_root / ".gitignore"
    existing = gi.read_text() if gi.is_file() else ""
    merged = merge_gitignore(existing, GITIGNORE_LINE)
    if merged != existing:
        gi.write_text(merged)
        return gi
    return None


# ---------------------------------------------------------------------------
# argparse + entry point
# ---------------------------------------------------------------------------


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "configure",
        help="Bootstrap CRAFT runtime config in <BERIL_ROOT>/.env + .claude/.",
        description=(
            "Wire `claude -p` (and this skill's app-internal calls) to a "
            "CRAFT-contracted provider. Idempotently extends "
            "<BERIL_ROOT>/.env with the shared CRAFT block + this skill's "
            "marker, discovers the provider's actual model list, resolves "
            "tier models (interactive picker for unresolved tiers when on "
            "a TTY), writes "
            "<BERIL_ROOT>/.claude/settings.{json,local.json}, and runs a "
            "response-asserting validation ping against the reasoning tier."
        ),
    )
    p.add_argument(
        "beril_root",
        nargs="?",
        default=None,
        help="BERIL_ROOT (default: discovery walk-up).",
    )
    p.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip provider model-list discovery; resolve from .env pins only.",
    )
    p.add_argument(
        "--no-ping",
        action="store_true",
        help="Skip the validation ping (e.g., when offline / no token yet).",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive: never prompt. Fail loud on unresolved tiers.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # All optional flags are read via getattr so programmatic callers
    # constructing a bare Namespace (e.g., other skills' orchestrators,
    # tests) can't AttributeError on a missing attribute.
    no_discover = getattr(args, "no_discover", False)
    no_ping = getattr(args, "no_ping", False)
    yes = getattr(args, "yes", False)

    # 1. Resolve BERIL_ROOT.
    try:
        beril_root = discovery.find_beril_root(explicit=getattr(args, "beril_root", None))
    except discovery.BerilRootNotFound as exc:
        print(f"Error: cannot resolve BERIL_ROOT: {exc}", file=sys.stderr)
        return 3

    print(f"beril-paper-writer-skill v{__version__}")
    print(f"  BERIL_ROOT: {beril_root}")

    # claude CLI is a hard prerequisite even for non-ping mode (we'd be
    # writing config it consumes; if claude isn't here, configure is moot).
    claude_path = shutil.which("claude")
    if claude_path is None:
        print(
            "  [MISSING] claude CLI not found on PATH. Install Claude Code "
            "(https://docs.claude.com) and retry.",
            file=sys.stderr,
        )
        return 3
    print(f"  [OK] claude — {claude_path}")

    # 2. Idempotently extend .env.
    env_path = beril_root / ".env"
    env_text = env_path.read_text() if env_path.is_file() else ""
    append_text = compose_env_append(env_text)
    if append_text:
        new_text = (
            env_text + ("" if env_text.endswith("\n") or env_text == "" else "\n") + append_text
        )
        env_path.write_text(new_text)
        with contextlib.suppress(OSError):
            os.chmod(env_path, 0o600)
        print(f"  [OK] .env extended with CRAFT config block: {env_path}")
        env_text = new_text
    else:
        print("  [OK] .env already carries CRAFT shared + per-skill markers (no change)")

    env_map = parse_env_text(env_text)

    # 3. Discover the provider's model list (req 3.1 — discover-don't-hardcode).
    provider = llm_config.infer_provider(env_map)
    print(f"  [OK] ACTIVE_PROVIDER (resolved): {provider}")
    available: list[str] | None = None
    if not no_discover:
        available = query_provider_models(provider, env_map)
        if available is not None:
            print(f"  [OK] provider serves {len(available)} model id(s)")

    # 4. Resolve tier models.
    try:
        resolved = llm_config.resolve(env_map, available)
    except llm_config.ConfigError as exc:
        # Internally-contradictory .env (e.g. ACTIVE_PROVIDER=cborg with no
        # CBORG_API_KEY). Fail loud, not stack-trace.
        print(f"  [ERROR] .env is inconsistent: {exc}", file=sys.stderr)
        print(
            f"    Edit {env_path} so the credentials match ACTIVE_PROVIDER, "
            "or set ACTIVE_PROVIDER=subscription for ambient login.",
            file=sys.stderr,
        )
        return 1
    for w in resolved.warnings:
        print(f"  [WARN] {w}", file=sys.stderr)

    # 4a. Handle unresolved tiers per contract:
    #     interactive → picker; non-interactive → fail loud.
    if resolved.unresolved_tiers:
        if yes or available is None or not sys.stdin.isatty():
            print(
                f"  [ERROR] unresolved tier(s): {', '.join(resolved.unresolved_tiers)}",
                file=sys.stderr,
            )
            if available is None:
                print(
                    "    no provider model list available — set "
                    "MODEL_REASONING / MODEL_STANDARD / MODEL_FAST in "
                    f"{env_path} explicitly, or re-run without --no-discover.",
                    file=sys.stderr,
                )
            else:
                for tier in resolved.unresolved_tiers:
                    family = llm_config.TIER_FAMILY[tier]
                    cands = [m for m in available if family in m.lower()]
                    if cands:
                        print(
                            f"    tier {tier!r} ({family}) candidates: {', '.join(cands)}",
                            file=sys.stderr,
                        )
                        print(
                            f"    re-pin with: "
                            f"echo '{llm_config.TIER_ENVKEY[tier]}=<choice>' "
                            f">> {env_path}",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"    tier {tier!r} ({family}): provider serves "
                            "no model of this family — pick a substitute "
                            "manually.",
                            file=sys.stderr,
                        )
            return 1

        picks = resolve_unresolved_interactively(env_map, available, resolved.unresolved_tiers)
        if not picks:
            print("  [ERROR] no picks collected; aborting.", file=sys.stderr)
            return 1

        # Persist picks to .env, then re-resolve from the updated mapping.
        new_text = env_text
        for tier, model in picks.items():
            new_text = update_or_append_kv(new_text, llm_config.TIER_ENVKEY[tier], model)
        if new_text != env_text:
            env_path.write_text(new_text)
            env_text = new_text
            env_map = parse_env_text(env_text)
            print("  [OK] persisted picked models to .env")
        resolved = llm_config.resolve(env_map, available)
        if resolved.unresolved_tiers:
            print(
                f"  [ERROR] still unresolved after picker: {', '.join(resolved.unresolved_tiers)}",
                file=sys.stderr,
            )
            return 1

    # 5. Write settings.{json,local.json} and ensure gitignore.
    settings, settings_local = shape_settings(resolved)
    settings_path, local_path = _write_settings_files(beril_root, settings, settings_local)
    print(f"  [OK] wrote {settings_path}")
    print(f"  [OK] wrote {local_path}  (gitignored)")

    gi_updated = _ensure_gitignore(beril_root)
    if gi_updated is not None:
        print(f"  [OK] appended {GITIGNORE_LINE!r} to {gi_updated}")
    else:
        print(f"  [OK] .gitignore already covers {GITIGNORE_LINE!r}")

    # 6. Validation ping (req 3.2 — response, not exit code).
    if no_ping:
        print("  [skip] validation ping (--no-ping)")
    else:
        reasoning_model = resolved.tier_models.get("reasoning")
        if not reasoning_model:
            print(
                "  [WARN] no reasoning tier model resolved; skipping ping.",
                file=sys.stderr,
            )
        else:
            # Fallback set: if the reasoning tier was DISCOVERED (no user
            # pin in .env), and the picked model fails its ping, walk the
            # next-newest candidates for the family rather than pinning a
            # model that can't pass its own ping. A USER-pinned model
            # that fails is a hard error (we tell them; they re-pin).
            family = llm_config.TIER_FAMILY["reasoning"]
            user_pinned = bool(env_map.get(llm_config.TIER_ENVKEY["reasoning"], "").strip())
            if user_pinned or available is None:
                fallback_chain: list[str] = [reasoning_model]
            else:
                fallback_chain = _tier_candidates_newest_first(available, family)
                # Ensure the currently-picked one is first; the rest are
                # next-newest, next-next-newest, ... (no duplicates).
                if reasoning_model in fallback_chain:
                    fallback_chain.remove(reasoning_model)
                fallback_chain = [reasoning_model, *fallback_chain]

            chosen: str | None = None
            last_body = ""
            for attempt_model in fallback_chain:
                if attempt_model == reasoning_model:
                    print(f"  [..] validation ping (reasoning tier, model={attempt_model})")
                else:
                    print(f"  [..] validation ping fallback (next-newest, model={attempt_model})")
                ok, body = validation_ping(
                    attempt_model,
                    public_env=resolved.public_env,
                    secret_env=resolved.secret_env,
                )
                last_body = body
                if ok:
                    chosen = attempt_model
                    print(f"  [OK] validation ping: {body!r}")
                    break
                print(
                    f"  [WARN] ping failed on {attempt_model}: {body}",
                    file=sys.stderr,
                )

            if chosen is None:
                print(
                    f"  [ERROR] validation ping failed on all candidates "
                    f"({len(fallback_chain)} tried): last response {last_body}",
                    file=sys.stderr,
                )
                print(
                    "    Hint: contract §3.4 req 3.2 requires response-validation, "
                    "not exit-code. A wrong/unsupported model on CBORG returns a "
                    "generic greeting at exit 0; that's what this catches. If you "
                    "have a manual pin in .env for the reasoning tier, edit it to "
                    "a model the provider actually serves.",
                    file=sys.stderr,
                )
                return 1

            # If the fallback picked a different model than the originally-
            # resolved one, persist it: re-pin .env and update settings.json
            # so the steady-state behavior matches what we just verified.
            if chosen != reasoning_model:
                env_text = update_or_append_kv(
                    env_text, llm_config.TIER_ENVKEY["reasoning"], chosen
                )
                env_path.write_text(env_text)
                env_map = parse_env_text(env_text)
                print(
                    f"  [OK] re-pinned MODEL_REASONING={chosen} in .env "
                    "(was {reasoning_model}; failed ping)".format(reasoning_model=reasoning_model)
                )
                # Re-shape settings with the working model so the file on
                # disk reflects what passed.
                resolved = llm_config.resolve(env_map, available)
                settings, settings_local = shape_settings(resolved)
                settings_path, local_path = _write_settings_files(
                    beril_root, settings, settings_local
                )
                print(f"  [OK] rewrote {settings_path} (re-pinned)")

    # 7. Stamp configured-at.
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamped = update_or_append_kv(env_text, "BERIL_PAPER_WRITER_CONFIGURED_AT", now)
    stamped = update_or_append_kv(stamped, "BERIL_PAPER_WRITER_CONFIGURED_VERSION", __version__)
    if stamped != env_text:
        env_path.write_text(stamped)
        print(f"  [OK] stamped BERIL_PAPER_WRITER_CONFIGURED_AT={now}")

    # Soft preflight: paper-writer's adversarial review delegates to the
    # `beril-adversarial` CLI when present. Absent → orchestrator falls
    # back to the inline `fallback_reviewer.v1.md` prompt (degraded:
    # 3 finding classes vs 10, no literature scan, no biological-claim
    # verification). Advisory: report status, never fail. Per
    # CRAFT-CONTRACT §3.4 (Round 2b clarification): the only external
    # CLI paper-writer shells out to that pip does not provide.
    adv_path = shutil.which("beril-adversarial")
    if adv_path:
        print(f"  [OK] beril-adversarial — {adv_path}")
    else:
        print(
            "  [WARN] beril-adversarial CLI not on PATH; paper-writer will "
            "fall back to its inline reviewer (degraded review).",
            file=sys.stderr,
        )

    return 0
