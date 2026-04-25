"""BERIL_ROOT and skill-path discovery.

Single source of truth for resolving a BERIL deployment's layout. All
runtime path lookups go through this module.

Vendored from beril_adversarial.discovery with the SKILL_DIR_NAME
changed to 'beril-paper-writer'. The two skills coexist in the same
BERIL deployment; sharing this resolution logic verbatim avoids drift.

Discovery order for BERIL_ROOT (first match wins):

1. Explicit `beril_root` argument passed by the caller.
2. `BERIL_ROOT` environment variable.
3. Walk up from cwd until a candidate matching the BERIL marker set is
   found. A candidate must have ALL of:
     - `.env` file at root
     - `.claude/skills/` directory at root
     - At least one of the BERIL-core skill directories:
         `.claude/skills/submit/`,
         `.claude/skills/berdl/`,
         `.claude/skills/suggest-research/`
4. If none match: raise `BerilRootNotFound` with a diagnostic that names
   which markers failed where.

Tiebreaker signals (not required, boost diagnostic confidence):
  - Directory name matches /BERIL[-_]/i.
  - `.env.example` contains `KBASE_AUTH_TOKEN`.
  - `DIRECTORY_STRUCTURE.md` exists at root.

Derived paths are all relative to BERIL_ROOT:
  - .env             → `<root>/.env`
  - skill dir        → `<root>/.claude/skills/beril-paper-writer/`
  - prompts          → `<root>/.claude/skills/beril-paper-writer/prompts/`
  - references       → `<root>/.claude/skills/beril-paper-writer/references/`
  - tools            → `<root>/.claude/skills/beril-paper-writer/tools/`
  - state            → `<root>/.claude/skills/beril-paper-writer/state/`
  - projects         → `<root>/projects/`
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SKILL_DIR_NAME = "beril-paper-writer"
BERIL_CORE_SKILLS = ("submit", "berdl", "suggest-research")
DIR_NAME_PATTERN = re.compile(r"BERIL[-_]", re.IGNORECASE)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class BerilRootNotFound(RuntimeError):
    """Raised when BERIL_ROOT cannot be resolved."""


# --------------------------------------------------------------------------
# Discovery result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MarkerCheck:
    """Result of marker validation for a candidate directory."""
    path: Path
    has_env_file: bool
    has_claude_skills: bool
    beril_core_skills_found: tuple[str, ...]
    tiebreakers: tuple[str, ...]

    @property
    def is_beril_root(self) -> bool:
        return (
            self.has_env_file
            and self.has_claude_skills
            and bool(self.beril_core_skills_found)
        )

    def diagnostic(self) -> str:
        def tick(ok: bool) -> str:
            return "[x]" if ok else "[ ]"

        lines = [
            f"  {tick(self.has_env_file)} .env file at {self.path}",
            f"  {tick(self.has_claude_skills)} .claude/skills/ at {self.path}",
            f"  {tick(bool(self.beril_core_skills_found))} BERIL-core skill "
            f"(found: {self.beril_core_skills_found or 'none of '}"
            f"{BERIL_CORE_SKILLS if not self.beril_core_skills_found else ''})",
        ]
        if self.tiebreakers:
            lines.append(f"  tiebreaker signals: {', '.join(self.tiebreakers)}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _check_markers(candidate: Path) -> MarkerCheck:
    """Evaluate all markers for a candidate BERIL root directory."""
    env_file = candidate / ".env"
    claude_skills = candidate / ".claude" / "skills"

    has_env = env_file.is_file()
    has_claude = claude_skills.is_dir()

    beril_skills_found: list[str] = []
    if has_claude:
        for skill in BERIL_CORE_SKILLS:
            if (claude_skills / skill).is_dir():
                beril_skills_found.append(skill)

    tiebreakers: list[str] = []
    if DIR_NAME_PATTERN.search(candidate.name):
        tiebreakers.append("directory-name-matches-BERIL")
    env_example = candidate / ".env.example"
    if env_example.is_file():
        try:
            content = env_example.read_text(encoding="utf-8", errors="ignore")
            if "KBASE_AUTH_TOKEN" in content:
                tiebreakers.append(".env.example-has-KBASE_AUTH_TOKEN")
        except OSError:
            pass
    if (candidate / "DIRECTORY_STRUCTURE.md").is_file():
        tiebreakers.append("DIRECTORY_STRUCTURE.md-present")

    return MarkerCheck(
        path=candidate,
        has_env_file=has_env,
        has_claude_skills=has_claude,
        beril_core_skills_found=tuple(beril_skills_found),
        tiebreakers=tuple(tiebreakers),
    )


def find_beril_root(
    explicit: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> Path:
    """Resolve BERIL_ROOT.

    Args:
        explicit: Override passed from CLI / caller. Highest priority.
        env: Override process environ; defaults to os.environ.
        cwd: Override starting directory for walk-up; defaults to Path.cwd().

    Returns:
        Absolute path to BERIL_ROOT.

    Raises:
        BerilRootNotFound: if no discovery path succeeds. The error message
            names the closest-to-BERIL candidate observed during walk-up so
            the user can diagnose what's missing.
    """
    env_map = env if env is not None else os.environ

    # 1. Explicit
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        check = _check_markers(root)
        if not check.is_beril_root:
            raise BerilRootNotFound(
                f"Explicit path {root} is not a BERIL checkout.\n"
                f"Marker check:\n{check.diagnostic()}"
            )
        return root

    # 2. Environment variable
    env_val = env_map.get("BERIL_ROOT")
    if env_val:
        root = Path(env_val).expanduser().resolve()
        check = _check_markers(root)
        if not check.is_beril_root:
            raise BerilRootNotFound(
                f"BERIL_ROOT={env_val!r} does not point at a BERIL checkout.\n"
                f"Marker check:\n{check.diagnostic()}"
            )
        return root

    # 3. Walk up from cwd
    start = (cwd if cwd is not None else Path.cwd()).resolve()
    best_check: Optional[MarkerCheck] = None
    current = start
    while True:
        check = _check_markers(current)
        if check.is_beril_root:
            return current
        if best_check is None or _marker_score(check) > _marker_score(best_check):
            best_check = check
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 4. Not found
    diag_lines = [
        "could not find BERIL_ROOT.",
        "  - Pass --beril-root <path>, or",
        "  - Set BERIL_ROOT environment variable, or",
        "  - Run beril-paper-writer from inside a BERIL checkout.",
        "",
        f"Walk-up from {start} reached filesystem root without a match.",
    ]
    if best_check is not None:
        diag_lines.extend([
            "",
            "Closest candidate seen during walk-up:",
            best_check.diagnostic(),
        ])
    diag_lines.extend([
        "",
        "If you believe you're in a BERIL checkout, pass --beril-root explicitly",
        "and file an issue at "
        "https://github.com/ArkinLaboratory/beril-paper-writer-skill/issues.",
    ])
    raise BerilRootNotFound("\n".join(diag_lines))


def _marker_score(check: MarkerCheck) -> int:
    """Numeric score for ranking partial-match candidates in diagnostics."""
    score = 0
    if check.has_env_file:
        score += 4
    if check.has_claude_skills:
        score += 4
    score += len(check.beril_core_skills_found) * 2
    score += len(check.tiebreakers)
    return score


# --------------------------------------------------------------------------
# Derived paths (all rooted at BERIL_ROOT)
# --------------------------------------------------------------------------

def get_env_path(beril_root: Path) -> Path:
    return beril_root / ".env"


def get_skill_dir(beril_root: Path) -> Path:
    return beril_root / ".claude" / "skills" / SKILL_DIR_NAME


def get_prompts_dir(beril_root: Path) -> Path:
    return get_skill_dir(beril_root) / "prompts"


def get_references_dir(beril_root: Path) -> Path:
    return get_skill_dir(beril_root) / "references"


def get_tools_dir(beril_root: Path) -> Path:
    return get_skill_dir(beril_root) / "tools"


def get_state_dir(beril_root: Path) -> Path:
    return get_skill_dir(beril_root) / "state"


def get_projects_dir(beril_root: Path) -> Path:
    return beril_root / "projects"


def get_project_dir(beril_root: Path, project_id: str) -> Path:
    return get_projects_dir(beril_root) / project_id


def get_papers_dir(beril_root: Path, project_id: str) -> Path:
    """`<beril_root>/projects/<project_id>/papers/` — where draft_N/ dirs live."""
    return get_project_dir(beril_root, project_id) / "papers"


def get_draft_dir(beril_root: Path, project_id: str, draft_number: int) -> Path:
    """`<beril_root>/projects/<project_id>/papers/draft_N/`."""
    return get_papers_dir(beril_root, project_id) / f"draft_{draft_number}"


# --------------------------------------------------------------------------
# Convenience: full derived-paths bundle
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BerilPaths:
    """All derived paths for a resolved BERIL_ROOT."""
    beril_root: Path
    env_path: Path
    skill_dir: Path
    prompts_dir: Path
    references_dir: Path
    tools_dir: Path
    state_dir: Path
    projects_dir: Path


def resolve_paths(
    explicit: Optional[Path | str] = None,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> BerilPaths:
    """One-call resolution of all paths. Use this in commands."""
    root = find_beril_root(explicit=explicit, env=env, cwd=cwd)
    return BerilPaths(
        beril_root=root,
        env_path=get_env_path(root),
        skill_dir=get_skill_dir(root),
        prompts_dir=get_prompts_dir(root),
        references_dir=get_references_dir(root),
        tools_dir=get_tools_dir(root),
        state_dir=get_state_dir(root),
        projects_dir=get_projects_dir(root),
    )
