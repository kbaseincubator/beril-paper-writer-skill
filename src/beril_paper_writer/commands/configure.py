"""`beril-paper-writer configure` — minimal environment check.

Verify that:
  - `claude` CLI is installed (HARD requirement; exit 3 if missing)
  - `beril-adversarial` CLI is installed (SOFT; warn if missing — the
    writer will fall back to the inline reviewer per SPEC §8.2)
  - `pandoc` is installed (SOFT; warn if missing — only needed by
    `beril-paper-writer assemble`)

Mirrors `beril_adversarial.commands.configure` with the additional
soft checks for adversarial and pandoc, both of which are dependencies
the paper writer can degrade around.

Exit codes:
  0 — claude is present (regardless of soft-check outcome)
  3 — claude not on PATH
"""

from __future__ import annotations

import argparse
import shutil
import sys

from beril_paper_writer import __version__, discovery


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "configure",
        help="Verify the claude CLI is installed; warn if optional deps absent.",
        description=(
            "Quick check that the `claude` CLI is on PATH (hard requirement). "
            "Also reports whether `beril-adversarial` and `pandoc` are "
            "available — both are optional but improve output. The actual "
            "drafting run will surface any further configuration issues "
            "(MCP servers, WebSearch, model auth) with clear error messages."
        ),
    )
    p.add_argument(
        "--beril-root",
        help="Explicit BERIL_ROOT (used only for the status banner).",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # Resolve BERIL_ROOT for the banner; non-fatal if absent.
    try:
        beril_root = discovery.find_beril_root(
            explicit=getattr(args, "beril_root", None)
        )
    except discovery.BerilRootNotFound:
        beril_root = None

    print(f"beril-paper-writer-skill v{__version__}")
    if beril_root is not None:
        print(f"  BERIL_ROOT: {beril_root}")

    # Hard requirement: claude
    claude_path = shutil.which("claude")
    if claude_path is None:
        print("  [MISSING] claude CLI not found on PATH.", file=sys.stderr)
        print(
            "  Install Claude Code (https://docs.claude.com) and retry.",
            file=sys.stderr,
        )
        return 3
    print(f"  [OK]      claude            — {claude_path}")

    # Soft check: beril-adversarial
    adversarial_path = shutil.which("beril-adversarial")
    if adversarial_path:
        print(
            f"  [OK]      beril-adversarial — {adversarial_path}  "
            f"(used for --type paper review of drafts)"
        )
    else:
        print(
            "  [absent]  beril-adversarial — not on PATH; "
            "the writer will use the inline fallback reviewer (lighter; see SPEC §8.2)."
        )
        print(
            "            To install: pipx install git+ssh://git@github.com/"
            "ArkinLaboratory/beril-adversarial-skill.git"
        )

    # Soft check: pandoc (only needed by `assemble`)
    pandoc_path = shutil.which("pandoc")
    if pandoc_path:
        print(f"  [OK]      pandoc            — {pandoc_path}  (used by `assemble`)")
    else:
        print(
            "  [absent]  pandoc            — not on PATH; "
            "`beril-paper-writer assemble` will fail until pandoc is installed."
        )
        print(
            "            macOS: brew install pandoc | "
            "Linux: apt-get install pandoc"
        )

    return 0
