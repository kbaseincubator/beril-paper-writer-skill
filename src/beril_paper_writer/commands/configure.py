"""`beril-paper-writer configure` — comprehensive environment audit.

Verify that the runtime environment can support the full paper-writer
pipeline. Surfaces problems at install/configure time rather than
mid-pipeline, where each $5–$15 retry hurts.

Checks (in order):

  Hard requirements (return 3 if missing):
  - `claude` CLI on PATH
  - The Python interpreter the orchestrator will resolve via paper_writer.sh's
    discover_python_bin (typically the pipx venv's python). Has the right
    runtime deps (`nbformat`, `python-docx`).

  Soft requirements (warn only):
  - `beril-adversarial` CLI on PATH (writer falls back to inline reviewer)
  - POSIX core utilities (basename, cat, cp, cut, date, dirname, echo,
    grep, head, hostname, ls, mkdir, readlink, rm, sed, sort, touch, which).
    These should always be available; verifying confirms a non-trivial
    container/sandbox isn't stripped down.
  - bash version (>= 3.2)

  Informational:
  - WebSearch availability — not checked directly; verified at run time
    by citation_pool.v1 and the adversarial reviewer.

Per DECISIONS D-024, the assembly path uses `python-docx` so a pipx
install is fully self-contained — important for remote BERIL deployments
where users may not have admin access.

Exit codes:
  0 — all hard requirements met
  3 — at least one hard requirement missing
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from beril_paper_writer import __version__, discovery


# POSIX core utilities the orchestrator invokes. Should always be on PATH
# on macOS and Linux; verify defensively in case of stripped-down containers.
_POSIX_UTILITIES = (
    "basename", "cat", "cp", "cut", "date", "dirname", "echo",
    "grep", "head", "hostname", "ls", "mkdir", "readlink", "rm",
    "sed", "sort", "touch",
)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "configure",
        help="Verify the claude CLI is installed; report optional dep status.",
        description=(
            "Comprehensive environment audit. Verifies hard requirements "
            "(claude CLI, Python interpreter with nbformat + python-docx) "
            "and reports soft-requirement status (beril-adversarial fallback, "
            "POSIX utilities, bash version). Surfaces problems at "
            "install time so that mid-pipeline runs don't hit unexpected "
            "blockers."
        ),
    )
    p.add_argument(
        "--beril-root",
        help="Explicit BERIL_ROOT (used only for the status banner).",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-check output; print only summary + return exit code.",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    quiet = getattr(args, "quiet", False)
    hard_failures: list[str] = []
    soft_warnings: list[str] = []

    def _info(msg: str) -> None:
        if not quiet:
            print(msg)

    def _err(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    # Resolve BERIL_ROOT for the banner; non-fatal if absent.
    try:
        beril_root = discovery.find_beril_root(
            explicit=getattr(args, "beril_root", None)
        )
    except discovery.BerilRootNotFound:
        beril_root = None

    _info(f"beril-paper-writer-skill v{__version__}")
    if beril_root is not None:
        _info(f"  BERIL_ROOT: {beril_root}")
    _info("")
    _info("=== Hard requirements ===")

    # ---- 1. claude CLI ----
    # Stage 3 Tier J: report what the ORCHESTRATOR will actually resolve,
    # not just a bare `shutil.which`. The orchestrator uses
    # resolve_claude_bin() (BERIL_CLAUDE_BIN override → PATH → well-known
    # locations) and pins the result to an absolute path. A plain
    # `shutil.which` here gave a false green when the orchestrator's
    # spawn context resolved `claude` differently from configure's.
    from beril_paper_writer.orchestrator import resolve_claude_bin

    try:
        claude_path = resolve_claude_bin()
    except RuntimeError as e:
        claude_path = None
        _err("  [MISSING] claude CLI could not be resolved.")
        for line in str(e).splitlines():
            _err(f"            {line}")
        hard_failures.append("claude CLI")
    if claude_path is not None:
        version_str = _safe_version(
            [claude_path, "--version"], default="(version unknown)"
        )
        _info(f"  [OK]      claude            — {claude_path}  {version_str}")

    # ---- 2. Python interpreter the orchestrator resolves ----
    py_path = _resolve_orchestrator_python()
    if not py_path or not py_path.is_file():
        _err("  [MISSING] Cannot resolve orchestrator's Python interpreter.")
        _err("            Expected via the pipx wrapper script's shebang.")
        _err("            Reinstall: pipx install --force beril-paper-writer-skill")
        hard_failures.append("orchestrator Python interpreter")
        py_path = None
    else:
        py_version = _safe_version([str(py_path), "--version"], default="(unknown)")
        _info(f"  [OK]      orchestrator-python — {py_path}  {py_version}")

    # ---- 3. nbformat (used by extract_methods.py) ----
    if py_path is not None:
        nb_version = _check_module_in(py_path, "nbformat")
        if nb_version:
            _info(f"  [OK]      nbformat           — {nb_version}  (used by extract_methods.py)")
        else:
            _err("  [MISSING] nbformat not importable by orchestrator-python.")
            _err("            extract_methods.py phase will fail on real notebooks.")
            _err("            Reinstall: pipx install --force beril-paper-writer-skill")
            hard_failures.append("nbformat")

    # ---- 4. python-docx (assemble step; v0.2 dependency) ----
    if py_path is not None:
        # python-docx imports as `docx`
        docx_version = _check_module_in(py_path, "docx")
        if docx_version:
            _info(f"  [OK]      python-docx        — {docx_version}  (used by `assemble` to render .docx)")
        else:
            _err("  [MISSING] python-docx not importable by orchestrator-python.")
            _err("            `beril-paper-writer assemble` will fail in v0.2 when the")
            _err("            markdown→docx step lands. v0.1's manuscript.md does not")
            _err("            need it, but pipx should have bundled it — investigate.")
            hard_failures.append("python-docx")

    _info("")
    _info("=== Soft requirements ===")

    # ---- 5. beril-adversarial CLI (soft) ----
    adv_path = shutil.which("beril-adversarial")
    if adv_path:
        _info(f"  [OK]      beril-adversarial — {adv_path}")
    else:
        _info(
            "  [absent]  beril-adversarial — not on PATH; the writer will use the "
            "inline fallback reviewer (lighter; see SPEC §8.2)."
        )
        _info(
            "            To install: pipx install --force "
            "git+ssh://git@github.com/ArkinLaboratory/beril-adversarial-skill.git"
        )

    # ---- 6. bash version ----
    bash_v = _safe_version(["bash", "--version"], default="")
    if bash_v:
        # Output format: "GNU bash, version 3.2.57(1)-release (...)"
        m = re.search(r"version\s+(\d+)\.(\d+)", bash_v)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            ver_str = f"{major}.{minor}"
            if (major, minor) >= (3, 2):
                _info(f"  [OK]      bash                — {ver_str} (>= 3.2 required)")
            else:
                _err(f"  [LOW]     bash                — {ver_str} (need >= 3.2)")
                soft_warnings.append(f"bash {ver_str} below 3.2")
        else:
            _info(f"  [OK]      bash                — version unparseable but present")
    else:
        _err("  [MISSING] bash not on PATH (very unusual).")
        soft_warnings.append("bash")

    # ---- 7. POSIX core utilities ----
    missing_utils = [u for u in _POSIX_UTILITIES if shutil.which(u) is None]
    if not missing_utils:
        _info(f"  [OK]      POSIX utilities      — all {len(_POSIX_UTILITIES)} present")
    else:
        _err(f"  [MISSING] POSIX utilities      — {len(missing_utils)} missing: {', '.join(missing_utils)}")
        _err("            Orchestrator may fail at random points. Container or")
        _err("            sandbox is unusually stripped down.")
        soft_warnings.append(f"POSIX utilities missing: {missing_utils}")

    _info("")
    _info("=== Informational ===")

    # WebSearch is used by citation_pool.v1 and (when present) the
    # adversarial reviewer. Not checked here — verified at run time.
    _info(
        "  [info]    WebSearch            — used by citation_pool.v1 and "
        "(when present) beril-adversarial. Not checked here; verified at "
        "run time."
    )

    # Summary
    _info("")
    _info("=== Summary ===")
    if hard_failures:
        _err(f"  ❌ {len(hard_failures)} hard failure(s): {', '.join(hard_failures)}")
        _err("     Pipeline will not run reliably. Fix these before invoking /beril-paper-writer.")
        return 3
    if soft_warnings:
        _info(f"  ⚠ {len(soft_warnings)} soft warning(s) (pipeline will run, may degrade): {', '.join(soft_warnings)}")
    else:
        _info("  ✓ All hard requirements met.")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_version(cmd: list[str], default: str = "(unknown)") -> str:
    """Run a command and return its first stdout line or `default` on error."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False,
        )
        out = (result.stdout or result.stderr or "").strip()
        return out.splitlines()[0] if out else default
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return default


def _resolve_orchestrator_python() -> Path | None:
    """Mirror paper_writer.sh's `discover_python_bin` logic to find the
    Python interpreter that the orchestrator will use at run time.

    Priority:
      1. $BERIL_PAPER_WRITER_PYTHON if set + executable
      2. Shebang of `which beril-paper-writer` (the pipx wrapper)
      3. `python3` on PATH (warned-against fallback)
    """
    env_python = os.environ.get("BERIL_PAPER_WRITER_PYTHON")
    if env_python:
        p = Path(env_python)
        if p.is_file() and os.access(p, os.X_OK):
            return p

    cli_path = shutil.which("beril-paper-writer")
    if cli_path:
        try:
            with open(cli_path, encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line.startswith("#!"):
                rest = first_line[2:].lstrip()
                first_word = rest.split()[0] if rest.split() else ""
                if first_word == "/usr/bin/env":
                    # /usr/bin/env-style — second token is the interpreter name
                    parts = rest.split()
                    if len(parts) >= 2:
                        resolved = shutil.which(parts[1])
                        if resolved:
                            return Path(resolved)
                else:
                    candidate = Path(first_word)
                    if candidate.is_file():
                        return candidate
        except (OSError, UnicodeDecodeError):
            pass

    sys_p = shutil.which("python3")
    if sys_p:
        return Path(sys_p)
    return None


def _check_module_in(python_path: Path, module: str) -> str | None:
    """Run the resolved Python interpreter to check if a module imports.
    Returns __version__ if importable, None otherwise.
    """
    code = (
        f"import {module}; print(getattr({module}, '__version__', '(version unknown)'))"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", code],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out if out else "(version unknown)"
