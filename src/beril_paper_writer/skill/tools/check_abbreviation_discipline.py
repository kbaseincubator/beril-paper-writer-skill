#!/usr/bin/env python3
"""check_abbreviation_discipline.py — Abbreviation and project-term validator (advisory).

Standalone script invoked by the shell orchestrator after final assembly:

    python3 "$SKILL_DIR/tools/check_abbreviation_discipline.py" "$DRAFT_DIR"

Validates the assembled manuscript.md for disciplined use of abbreviations and
project-specific terminology:

  1. **Abbreviation expansion order:** ALL-CAPS abbreviations must be expanded
     (Full Name (ABBR) or Full Name [ABBR]) before or at first use.
  2. **Project-term definitions:** Internal terms (Tier-A, phage GAP, ecotypes
     E0-E3, actionable) require preceding definition sentences.
  3. **Abbreviation table suggestion:** If >10 unique abbreviations, suggest
     adding an abbreviation table to Methods.

Behavior:
  - Emits stderr WARN/NOTE lines per anomaly + a final summary count.
  - Emits JSON diagnostics to stdout.
  - **Always exits 0.** Advisory; same contract as other post-checkers.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Project-internal terms that require definitions (configurable watchlist)
_PROJECT_TERMS = {
    "Tier-A": "Tier-A",
    "Tier-B": "Tier-B",
    "phage GAP": "phage GAP",
    "E0": "E0",
    "E1": "E1",
    "E2": "E2",
    "E3": "E3",
    "actionable": "actionable",
}

# Definition patterns: regex that indicate a term has been defined
_DEFINITION_PATTERNS = [
    r"defined as",
    r"refers to",
    r"denotes",
    r"means",
    r"we define",
    r"we term",
    r"is defined as",
    r"is the",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_code_blocks(text: str) -> str:
    """Remove code blocks (``` fenced) and YAML frontmatter from text."""
    # Remove YAML frontmatter (--- at start)
    text = re.sub(r"^---\n[\s\S]*?\n---\n", "", text)
    # Remove code blocks (``` ... ```)
    text = re.sub(r"```[\s\S]*?```", "", text)
    return text


def _strip_image_tags(text: str) -> str:
    """Remove image tags ![...](...)."""
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", text)
    return text


def _load_manuscript(draft_dir: Path) -> str | None:
    """Load manuscript.md from draft_dir. Falls back to scanning section files."""
    manuscript_path = draft_dir / "manuscript.md"
    if manuscript_path.is_file():
        return manuscript_path.read_text(encoding="utf-8")
    # Fallback: scan section files (numbered .md files in draft_dir)
    section_files = sorted(draft_dir.glob("[0-9][0-9]*.md"))
    if not section_files:
        return None
    sections = []
    for f in section_files:
        sections.append(f.read_text(encoding="utf-8"))
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_abbreviation_expansion_order(text: str) -> tuple[list[str], dict]:
    """Check if ALL-CAPS abbreviations are expanded before first use.

    Returns (warnings, diagnostics) where diagnostics includes:
      - unique_abbreviations: list of all found abbreviations
      - used_before_expansion: list of abbreviations used before expansion
      - never_expanded: list of abbreviations never expanded
    """
    warnings = []
    unique_abbrs = set()
    used_before_expansion = []
    never_expanded = []

    # Find all ALL-CAPS abbreviations (2+ chars, not at start of line)
    abbr_re = re.compile(r"\b([A-Z]{2,})\b")

    # Find all expansions: "Full Name (ABBR)" or "Full Name [ABBR]"
    expansion_re = re.compile(
        r"([A-Za-z\s\-/]+?)\s*[\(\[]([A-Z]{2,})[\)\]]"
    )

    # Build a map of abbreviations and their first occurrences
    abbr_positions = {}
    expansion_positions = {}

    for m in abbr_re.finditer(text):
        abbr = m.group(1)
        # Skip common words that happen to be caps
        if abbr in {"AND", "OR", "NOT", "IN", "ON", "AT", "BY"}:
            continue
        if abbr not in abbr_positions:
            abbr_positions[abbr] = m.start()
            unique_abbrs.add(abbr)

    for m in expansion_re.finditer(text):
        abbr = m.group(2)
        if abbr not in expansion_positions:
            expansion_positions[abbr] = m.start()

    # Check each abbreviation
    for abbr in sorted(unique_abbrs):
        first_use_pos = abbr_positions.get(abbr)
        expansion_pos = expansion_positions.get(abbr)

        if expansion_pos is None:
            warnings.append(
                f"WARN  abbreviation \"{abbr}\" used but never expanded "
                f"in manuscript"
            )
            never_expanded.append(abbr)
        elif first_use_pos < expansion_pos:
            # Find the line number for the warning
            line_num = text[:first_use_pos].count("\n") + 1
            expansion_line = text[:expansion_pos].count("\n") + 1
            warnings.append(
                f"WARN  abbreviation \"{abbr}\" first used at line {line_num} "
                f"before expansion at line {expansion_line}"
            )
            used_before_expansion.append(abbr)

    diagnostics = {
        "unique_abbreviations": sorted(unique_abbrs),
        "used_before_expansion": used_before_expansion,
        "never_expanded": never_expanded,
    }
    return warnings, diagnostics


def _check_project_terms(text: str) -> tuple[list[str], dict]:
    """Check if project-internal terms have preceding definitions.

    Returns (warnings, diagnostics) where diagnostics includes:
      - undefined_project_terms: list of terms used without definitions
    """
    warnings = []
    undefined_terms = []

    for term in sorted(_PROJECT_TERMS.keys()):
        # Find first occurrence of term
        term_escaped = re.escape(term)
        term_re = re.compile(rf"\b{term_escaped}\b", re.IGNORECASE)
        m = term_re.search(text)
        if not m:
            continue

        first_use_pos = m.start()
        line_num = text[:first_use_pos].count("\n") + 1

        # For ecotype labels E0-E3, only match when preceded by "ecotype"
        # or "Ecotype" within 50 chars (avoids false positives on "Fig. E1",
        # "Table E2", etc.).
        if term.startswith("E") and term[1:].isdigit():
            # Search for occurrences preceded by "ecotype" context
            ecotype_use_re = re.compile(
                r"(?:ecotype|Ecotype)\s+(?:labels?\s+)?(?:[\w,\s]*\b)?"
                + re.escape(term) + r"\b",
                re.IGNORECASE,
            )
            if not ecotype_use_re.search(text):
                # Term never appears in ecotype context — skip entirely
                # (too many false positives without ecotype prefix)
                continue
            # Check whether "ecotype" is defined before first ecotype-label use.
            # The definition may appear either as "ecotype is defined as ..."
            # or "we define ecotype as ..." — check both orderings.
            ecotype_defined = False
            text_before = text[:first_use_pos]
            for def_pattern in _DEFINITION_PATTERNS:
                # Pattern A: "ecotype ... defined as"
                if re.search(
                    r"ecotype\b.*?" + def_pattern,
                    text_before,
                    re.IGNORECASE,
                ):
                    ecotype_defined = True
                    break
                # Pattern B: "we define ... ecotype" (definition verb before term)
                if re.search(
                    def_pattern + r".*?\becotype\b",
                    text_before,
                    re.IGNORECASE,
                ):
                    ecotype_defined = True
                    break
            if not ecotype_defined:
                warnings.append(
                    f"WARN  project term \"{term}\" used at line {line_num} "
                    f"without preceding definition of 'ecotype'"
                )
                undefined_terms.append(term)
            continue

        # Check if any definition pattern appears before first use.
        # The definition may reference the term directly or appear in the
        # same sentence — we just look for any definition-signalling phrase.
        text_before = text[:first_use_pos]
        has_definition = False
        for def_pattern in _DEFINITION_PATTERNS:
            if re.search(def_pattern, text_before, re.IGNORECASE):
                has_definition = True
                break

        if not has_definition:
            warnings.append(
                f"WARN  project term \"{term}\" used at line {line_num} "
                f"without preceding definition"
            )
            undefined_terms.append(term)

    diagnostics = {"undefined_project_terms": undefined_terms}
    return warnings, diagnostics


def _check_abbreviation_table_suggestion(
    abbr_count: int,
) -> list[str]:
    """Suggest abbreviation table if >10 unique abbreviations."""
    warnings = []
    if abbr_count > 10:
        warnings.append(
            f"NOTE  {abbr_count} unique abbreviations found; "
            f"consider adding abbreviation table to Methods"
        )
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advisory validator for abbreviation and project-term discipline"
    )
    parser.add_argument(
        "draft_dir",
        help="Path to draft directory containing manuscript.md or section files",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    text = _load_manuscript(draft_dir)

    if not text:
        print(
            "NOTE: manuscript.md or section files not found — skipping checks",
            file=sys.stderr,
        )
        return 0

    # Strip code blocks, YAML, and image tags
    text = _strip_code_blocks(text)
    text = _strip_image_tags(text)

    # Run all checks
    all_warnings: list[str] = []
    all_diagnostics: dict = {}

    abbr_warnings, abbr_diags = _check_abbreviation_expansion_order(text)
    all_warnings.extend(abbr_warnings)
    all_diagnostics.update(abbr_diags)

    term_warnings, term_diags = _check_project_terms(text)
    all_warnings.extend(term_warnings)
    all_diagnostics.update(term_diags)

    abbr_count = len(all_diagnostics.get("unique_abbreviations", []))
    table_warnings = _check_abbreviation_table_suggestion(abbr_count)
    all_warnings.extend(table_warnings)

    all_diagnostics["total_abbreviations"] = abbr_count

    # Emit warnings to stderr
    warn_count = sum(1 for w in all_warnings if w.startswith("WARN"))
    note_count = sum(1 for w in all_warnings if w.startswith("NOTE"))

    for w in all_warnings:
        print(w, file=sys.stderr)

    if all_warnings:
        print(
            f"\n[check_abbreviation_discipline] {warn_count} WARN, "
            f"{note_count} NOTE; {abbr_count} unique abbreviations",
            file=sys.stderr,
        )
    else:
        print(
            "[check_abbreviation_discipline] all checks passed",
            file=sys.stderr,
        )

    # Emit JSON diagnostics to stdout
    print(json.dumps(all_diagnostics, indent=2))

    # Always exit 0 — advisory only
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
