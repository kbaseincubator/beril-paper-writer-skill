"""`beril-paper-writer template-env` — print the CRAFT `.env` config block.

Prints the stereotyped CRAFT runtime-config block to stdout. `configure`
uses this to know what to append to `<BERIL_ROOT>/.env` on first run.

Per CRAFT-CONTRACT.md §3.4 (runtime configuration contract v2), the block
has two parts:

  - A **shared CRAFT block** (provider, credentials, model tiers) that is
    written ONCE per BERIL deployment and shared by every CRAFT skill.
    `configure` detects the `# >>> CRAFT shared config` sentinel and does
    NOT duplicate it if another skill already wrote it.
  - A **per-skill marker** (`BERIL_PAPER_WRITER_CONFIGURED_*`) that each
    skill stamps independently on a successful configure.

`.env` holds app-internal config + secrets and is the single user-facing
source of truth. `configure` GENERATES `<BERIL_ROOT>/.claude/settings.json`
(+ gitignored `settings.local.json` for the token) FROM this block, so that
`claude -p` picks up provider routing and the model-tier aliases natively.
Do not put `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` here — `claude -p`
does not read `.env`; those are generated into settings.json.
"""

from __future__ import annotations

import argparse

from beril_paper_writer import __version__

# The shared block is sentinel-delimited so `configure` can detect-and-skip
# when another CRAFT skill already wrote it. Keep the sentinels byte-stable.
SHARED_BLOCK = """\
# >>> CRAFT shared config (written once; shared by all CRAFT skills) >>>
# Edit values here, then re-run any skill's `configure` to regenerate
# <BERIL_ROOT>/.claude/settings.json. See CRAFT-CONTRACT.md §3.4.

# Reasoning provider — routes BOTH `claude -p` and app-internal calls.
# One of:
#   anthropic     your own Anthropic Platform key (works anywhere, off-network)
#   cborg         LBL CBORG gateway (needs LBL network/VPN locally; free on the Hub)
#   subscription  ambient Claude Code login (capped by the monthly Agent SDK credit)
ACTIVE_PROVIDER=cborg

# CRAFT READS the provider credentials already present in this .env — it does
# NOT re-declare them (re-declaring would shadow the values BERIL and other
# processes already set). cborg uses CBORG_API_KEY (+ CBORG_BASE_URL); anthropic
# uses ANTHROPIC_API_KEY. If a needed key is missing, `configure` fails loud and
# names which one to add. `claude -p` uses the BARE host (configure strips /v1).

# Model tiers (Claude-tiered in v1). Leave BLANK → `configure` discovers the
# newest model available on your provider per tier and pins it here (visible +
# reproducible). Set a value to pin your own choice. Models drift (Opus moved
# 4-6 → 4-8; CBORG mirrors with lag), so discovery — not a hardcoded default —
# is the source of truth. reasoning = hard/unrecoverable work; fast = mechanical.
MODEL_REASONING=
MODEL_STANDARD=
MODEL_FAST=

# (Image generation in presentation-maker reads GOOGLE_AI_STUDIO_API_KEY if
# present; optional, independent of the reasoning provider. Not declared here.)
# <<< CRAFT shared config <<<
"""


def _paper_writer_block() -> str:
    return f"""\

# --- beril-paper-writer-skill (per-skill) ---
# Paper-writer routes all `claude -p` calls through tier aliases resolved by
# Claude Code via <BERIL_ROOT>/.claude/settings.json (written by `configure`):
#   throughline / synthesis / review-incorporation → reasoning (opus)
#   body drafting                                  → standard  (sonnet)
#   claim classification                           → fast      (haiku)
# A caller's explicit --model still wins per CRAFT-CONTRACT §3.4. No skill-
# specific env vars are required beyond the shared block above; the (optional)
# `beril-adversarial` CLI is detected by `configure` as a soft preflight (the
# orchestrator falls back to an inline reviewer when it's absent).

# Written by `beril-paper-writer configure` on a successful smoke.
# Do not edit by hand; re-run configure to refresh.
BERIL_PAPER_WRITER_CONFIGURED_AT=
BERIL_PAPER_WRITER_CONFIGURED_VERSION=
# beril-paper-writer-skill v{__version__}
"""


def render(include_shared: bool = True) -> str:
    """Render the .env block. `configure` calls with include_shared=False
    when the shared sentinel is already present in the target .env."""
    parts = []
    if include_shared:
        parts.append(SHARED_BLOCK)
    parts.append(_paper_writer_block())
    return "".join(parts)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "template-env",
        help="Print the CRAFT .env config block.",
        description=(
            "Print the stereotyped CRAFT runtime-config block that "
            "`configure` appends to <BERIL_ROOT>/.env. Use "
            "`--skill-only` to print just this skill's per-skill marker "
            "(omitting the shared CRAFT block)."
        ),
    )
    p.add_argument(
        "--skill-only",
        action="store_true",
        help="Print only the per-skill marker, not the shared CRAFT block.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    print(render(include_shared=not getattr(args, "skill_only", False)), end="")
    return 0
