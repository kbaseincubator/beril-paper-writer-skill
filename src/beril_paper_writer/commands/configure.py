"""`beril-paper-writer configure` — minimal environment check.

Verify that:
  - `claude` CLI is installed (HARD requirement; exit 3 if missing)
  - `beril-adversarial` CLI is installed (SOFT; warn if missing — the
    writer will fall back to the inline reviewer per SPEC §8.2)
  - `python-docx` is importable (SOFT; should always be present since
    pipx pulls it in as a runtime dep, but we verify defensively)

No system-binary dependencies (no pandoc, no LaTeX). Per DECISIONS D-024,
the assembly path uses `python-docx` so a pipx install is fully
self-contained — important for remote BERIL deployments where users may
not have admin to `apt-get install` or `brew install`.

Mirrors `beril_adversarial.commands.configure` with the additional
soft check for adversarial.

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
        help="Verify the claude CLI is installed; report optional dep status.",
        description=(
            "Quick check that the `claude` CLI is on PATH (hard requirement). "
            "Also reports whether `beril-adversarial` and `python-docx` are "
            "available — both are soft requirements the writer can degrade "
            "around. The actual drafting run will surface any further "
            "configuration issues (MCP servers, WebSearch, model auth) with "
            "clear error messages."
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

    # Soft check: python-docx (assemble step). pipx install bundles this,
    # so absence indicates a manually-broken environment.
    docx_version = _import_check("docx")
    if docx_version:
        print(
            f"  [OK]      python-docx       — {docx_version}  "
            f"(used by `assemble` to render .docx)"
        )
    else:
        print(
            "  [MISSING] python-docx       — not importable; "
            "`beril-paper-writer assemble` will fail.",
            file=sys.stderr,
        )
        print(
            "            This is unexpected — pipx should bundle python-docx. "
            "Try `pipx reinstall beril-paper-writer-skill`.",
            file=sys.stderr,
        )

    return 0


def _import_check(module: str) -> str | None:
    """Attempt to import a module and return its __version__ or None.

    Used to soft-check runtime deps that should be present (pipx pulls
    them in) but might be missing if the install was tampered with.
    """
    try:
        mod = __import__(module)
    except ImportError:
        return None
    return getattr(mod, "__version__", "(version unknown)")
