"""`beril-paper-writer install-skill <BERIL_ROOT>` — copy shipped skill files into BERIL.

Copies SKILL.md, commands/, prompts/, references/, and tools/ from the
installed package's bundled skill data into
`<BERIL_ROOT>/.claude/skills/beril-paper-writer/`.

PRESERVES (never overwritten): state/  (runtime state including learned-patterns.md
and per-skill memory).
CREATES if missing: state/.

Sets executable bit on tools/*.sh and tools/*.py after copy
(belt-and-suspenders even though hatchling should preserve it through
the wheel).

Mirrors `beril_adversarial.commands.install_skill`. v0.1.0-spec note: the
shipped skill/ tree does not yet exist (lands with implementation), so the
copy operations no-op on missing source dirs. The CLI plumbing works
end-to-end so users can install from a future release without changing how
they invoke install-skill.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
from importlib import resources
from pathlib import Path

from beril_paper_writer import __version__, discovery


# Directories inside the shipped skill/ dir that should be overwritten on install
_SHIPPED_SUBDIRS = ("commands", "prompts", "references", "tools")

# Directories that must exist in the installed skill dir but are install-local
# (never shipped, never overwritten)
_LOCAL_SUBDIRS = ("state",)

# Files at the skill-dir root that ship
_SHIPPED_FILES = ("SKILL.md",)

# Files inside shipped subdirs that need executable bit set after copy.
# The loop tolerates missing entries. install copies the whole tools/
# subdir (see _SHIPPED_SUBDIRS), so this list is chmod hygiene only,
# not a copy manifest. (paper_writer.sh + the v0.x checker tools were
# retired 2026-05-20 — D-053; entries dropped here accordingly.)
_EXECUTABLE_FILES = (
    "tools/stream_progress.py",
    "tools/paper_writer_helpers.py",
    "tools/extract_methods.py",
    "tools/extract_figures.py",
    "tools/extract_tables.py",
    "tools/citation_pool.py",
    "tools/validate_manuscript.py",
    "tools/assemble_docx.py",
    "tools/aggregate_metadata.py",
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "install-skill",
        help="Copy shipped skill files into a BERIL checkout.",
        description=(
            "Copy the beril-paper-writer skill files from the installed "
            "package into <BERIL_ROOT>/.claude/skills/beril-paper-writer/. "
            "Preserves the install-local state/ subdirectory."
        ),
    )
    p.add_argument(
        "beril_root",
        nargs="?",
        default=".",
        help="Path to the BERIL checkout root (default: current directory).",
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        help=(
            "Overwrite shipped files without confirmation. Does NOT remove "
            "the install-local state/ subdirectory."
        ),
    )
    p.add_argument(
        "--no-smoke-test",
        action="store_true",
        help=(
            "Skip the post-install light check (claude on PATH + next-step "
            "hint). Default: run it advisory (non-fatal)."
        ),
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        beril_root = discovery.find_beril_root(explicit=args.beril_root)
    except discovery.BerilRootNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    skill_target = discovery.get_skill_dir(beril_root)
    skill_target.mkdir(parents=True, exist_ok=True)

    # Locate the shipped skill/ dir inside the installed package.
    try:
        skill_src_trav = resources.files("beril_paper_writer") / "skill"
    except Exception as e:
        print(
            f"Error: could not locate shipped skill data inside "
            f"beril_paper_writer package: {e}. "
            f"This is an install-level bug. Please file an issue.",
            file=sys.stderr,
        )
        return 2

    # v0.1.0-spec: skill/ may not exist in the package yet. Handle gracefully
    # so the CLI plumbing is exercised even before implementation lands.
    try:
        with resources.as_file(skill_src_trav) as skill_src:
            if not skill_src.is_dir():
                print(
                    f"Note: no shipped skill/ data in this release "
                    f"({__version__}). Skill directory created at "
                    f"{skill_target}, but no files were copied. Install a "
                    f"release with implementation to populate it.",
                    file=sys.stderr,
                )
            else:
                _copy_shipped_files(skill_src, skill_target, force=args.force)
                _copy_shipped_subdirs(skill_src, skill_target, force=args.force)
                _set_executable_bits(skill_target)
    except FileNotFoundError:
        # importlib.resources can raise FileNotFoundError on some
        # backends if `skill/` simply doesn't exist in the package.
        print(
            f"Note: no shipped skill/ data in this release "
            f"({__version__}). Skill directory created at "
            f"{skill_target}, but no files were copied.",
            file=sys.stderr,
        )

    _ensure_local_subdirs(skill_target)

    print(f"Skill directory: {skill_target}")
    print(f"Preserved (never overwritten): {', '.join(_LOCAL_SUBDIRS)}")
    print(f"Package version: {__version__}")

    if args.no_smoke_test:
        return 0

    # Light post-install check — advisory, NEVER invokes configure.
    # CRAFT-CONTRACT §3.4 req 3.5 (install-skill does NOT run configure).
    # configure has real side effects (extends .env, writes
    # .claude/settings.json + settings.local.json, runs a live `claude -p`
    # ping) and must not run silently as a sub-step of install-skill.
    # The canary's Hub-crash that motivated this decoupling was
    # install-skill calling configure.run() and that path AttributeError'ing
    # on a Namespace built without the --no-discover / --no-ping / --yes
    # flags. Match the canary's shape (beril-adversarial install-skill).
    print("")
    claude_path = shutil.which("claude")
    if claude_path is None:
        print(
            "  [WARN] claude CLI not found on PATH. Install Claude Code "
            "(https://docs.claude.com) before running configure.",
            file=sys.stderr,
        )
    else:
        print(f"  [OK] claude — {claude_path}")
    print("")
    print(
        f"Next: run `beril-paper-writer configure --beril-root {beril_root}` "
        "to bootstrap CRAFT runtime config."
    )
    return 0


def _copy_shipped_files(src: Path, dst: Path, *, force: bool) -> None:
    for name in _SHIPPED_FILES:
        s = src / name
        if not s.is_file():
            continue
        d = dst / name
        if d.exists() and not force and _files_identical(s, d):
            continue
        shutil.copy2(s, d)


def _copy_shipped_subdirs(src: Path, dst: Path, *, force: bool) -> None:
    # Ignore filter: __pycache__ and .pyc files. These can appear in the
    # source tree when running install-skill from a `pip install -e .`
    # dev install (the package data IS the source tree, and Python
    # bytecode caches accumulate there). pipx-built wheels exclude them
    # via pyproject's [tool.hatch.build.targets.sdist].exclude, but
    # editable installs need this defensive filter.
    ignore_pycache = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for subdir in _SHIPPED_SUBDIRS:
        s = src / subdir
        if not s.is_dir():
            continue
        d = dst / subdir
        # Full replacement: remove and re-copy. Preserve nothing inside
        # shipped subdirs — they're maintained by the package.
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=ignore_pycache)


def _set_executable_bits(skill_dir: Path) -> None:
    """Ensure shipped scripts have +x. Hatchling should preserve this through
    the wheel, but we set it explicitly as a safety net."""
    for rel in _EXECUTABLE_FILES:
        path = skill_dir / rel
        if path.is_file():
            current = path.stat().st_mode
            path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_local_subdirs(skill_dir: Path) -> None:
    for subdir in _LOCAL_SUBDIRS:
        p = skill_dir / subdir
        p.mkdir(exist_ok=True)
    # Write a starter README for state/ on first creation.
    state_readme = skill_dir / "state" / "README.md"
    if not state_readme.exists():
        state_readme.write_text(_STATE_README, encoding="utf-8")


def _files_identical(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


_STATE_README = """# state/ — install-local runtime state

Files in this directory are written at runtime by the paper writer and are
NEVER shipped or overwritten by `beril-paper-writer install-skill`.

## learned-patterns.md

Cross-project meta-memory of writing patterns the writer should remember
across drafts. Written when a novel generalizable pattern is identified
(NOT project-specific gotchas — those go in `<BERIL>/docs/pitfalls.md`).

Read at the start of every Plan phase. See SPEC §"Reviewer memory" for
the protocol.

Maintainer note: when this file approaches the size cap (~15K tokens),
move the current contents to
`state/learned-patterns-archive/YYYY-MM-DD.md` and consolidate into a
shorter live file.
"""
