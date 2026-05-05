#!/usr/bin/env python3
"""check_figures_manifest.py — figures_manifest.tsv cross-walk (advisory).

Standalone script invoked by the shell orchestrator after results.v1
writes the manifest:

    python3 "$SKILL_DIR/tools/check_figures_manifest.py" "$DRAFT_DIR"

Fifth post-processor in the v0.2 pattern (joins
`check_throughline_glyphs`, `check_scope_coherence`, `check_overclaim`,
`check_repair_scope`). Validates the figures_manifest.tsv contract from
the artifact side: schema, filesystem agreement, and cross-walk against
the prose's `(Fig. N)` callouts.

Why this exists. v0.3 Tier 2 introduces a load-bearing manifest contract
that `phase_embed_figures` consumes to inject `![Figure N: caption]`
markdown image tags. If results.v1's manifest emission drifts from its
prose callouts (or the inventory join key), the consumer phase silently
emits a figure-less manuscript. Per
`feedback_prompt_discipline_needs_post_check.md`: prompt-level
discipline can't be the only enforcement.

Four checks:
  1. **Schema:** manifest exists + has correct header
     (paper_order_n / filename / inventory_lookup_name) + every data
     row has 3 tab-separated cells with valid integer in column 1.
  2. **Filename existence:** every row's `filename` column resolves to
     a real file under `<draft_dir>/figures/`.
  3. **Orphan-figure detection:** every `fig*.png` in
     `<draft_dir>/figures/` has a manifest row (no figures sitting
     un-manifested in the directory).
  4. **Callout cross-walk:** for each `(Fig. N)` callout in the section
     files (02_results.md primarily; also 01_methods.md,
     03_discussion.md), the manifest has a row with
     `paper_order_n == N`. Catches the case where prose cites a figure
     the manifest doesn't declare.

Behavior:
  - Emits stderr WARN lines per anomaly + a final summary count.
  - Emits NOTE lines for benign cases (empty inventory, missing
    section files in REPAIR_MODE-only drafts, etc.).
  - **Always exits 0.** Advisory; orchestrator surfaces via
    `emit-next-actions`. Same contract as the four existing
    post-processors.

The script can be imported as a module for unit testing; parsing
helpers are pure (text in, list/dict out).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPECTED_HEADER = ["paper_order_n", "filename", "inventory_lookup_name"]
SECTION_FILES = ("02_results.md", "01_methods.md", "03_discussion.md")

# Match (Fig. N) or (Fig. NA) (panel suffix) or "Fig. 3" without parens
# in citation contexts. We only want callouts that drive embedding,
# i.e., the (Fig. N) form. Loose enough to match
# "(Fig. 3)" / "(Fig. 3A)" / "(Fig. 3, panel A)" / "(Fig. 3 and Fig. 5)".
CALLOUT_RE = re.compile(r"\(Fig\.\s*(\d+)[A-Z]?\b")
# Markdown inline image embedding: ![...](figures/fig3_something.png)
# Captures the figure number from the filename.
INLINE_IMAGE_RE = re.compile(
    r"!\[.*?\]\((?:\.?/?)?figures/fig(\d+)_[^)]+\)", re.IGNORECASE
)
FIGURE_FILENAME_RE = re.compile(r"^fig\d+_.+\.(?:png|jpg|jpeg|pdf|svg)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------


def parse_manifest(manifest_path: Path) -> tuple[list[dict], list[str]]:
    """Parse figures_manifest.tsv into rows + a list of schema warnings.

    Returns ([], [<warnings>]) if file missing.
    Returns (rows, warnings) on partial / full success; rows that fail
    parsing are skipped and noted in warnings.
    """
    warnings: list[str] = []
    if not manifest_path.is_file():
        warnings.append(f"figures_manifest.tsv not found at {manifest_path}")
        return [], warnings

    text = manifest_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        warnings.append(f"figures_manifest.tsv is empty: {manifest_path}")
        return [], warnings

    header = lines[0].split("\t")
    if header != EXPECTED_HEADER:
        warnings.append(
            f"figures_manifest.tsv header mismatch: got {header!r}, "
            f"expected {EXPECTED_HEADER!r}"
        )
    rows: list[dict] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 3:
            warnings.append(
                f"figures_manifest.tsv line {lineno}: expected 3 cells, "
                f"got {len(cells)}; skipping"
            )
            continue
        # Banned-tab discipline reminder: cells already split on \t, so
        # extra tabs would have made len > 3. Surface that case.
        if len(cells) > 3:
            warnings.append(
                f"figures_manifest.tsv line {lineno}: row has >3 tab-separated "
                f"cells (banned-tab discipline violation?); first 3 cells used"
            )
        try:
            n = int(cells[0].strip())
        except ValueError:
            warnings.append(
                f"figures_manifest.tsv line {lineno}: paper_order_n not an "
                f"integer: {cells[0]!r}; skipping"
            )
            continue
        # v0.4 retest finding: results.v1 occasionally emits filenames
        # with `figures/` directory prefix instead of basename. WARN to
        # surface the LLM drift; downstream parser auto-normalizes via
        # Path().name (paper_writer_helpers._parse_figures_manifest).
        raw_filename = cells[1].strip()
        raw_inv_name = cells[2].strip()
        if "/" in raw_filename or "/" in raw_inv_name:
            warnings.append(
                f"figures_manifest.tsv line {lineno}: filename or "
                f"inventory_lookup_name contains a directory separator "
                f"({raw_filename!r}, {raw_inv_name!r}); schema specifies "
                f"basename-only. Downstream parser auto-normalizes; consider "
                f"tightening results.v1.md if this recurs."
            )
        rows.append({
            "lineno": lineno,
            "paper_order_n": n,
            "filename": raw_filename,
            "inventory_lookup_name": raw_inv_name,
        })
    return rows, warnings


def collect_callouts(section_paths: Iterable[Path]) -> dict[int, list[str]]:
    """Walk section files for figure references.

    Recognises two forms:
    1. Parenthetical callouts: ``(Fig. N)``
    2. Inline image embeds: ``![...](figures/figN_something.png)``

    Returns {N: [section_filename, ...]} so a reference can be traced
    back to which section files use it.
    """
    callouts: dict[int, list[str]] = {}
    for section_path in section_paths:
        if not section_path.is_file():
            continue
        text = section_path.read_text(encoding="utf-8")
        # Parenthetical callouts: (Fig. 3)
        for m in CALLOUT_RE.finditer(text):
            n = int(m.group(1))
            callouts.setdefault(n, []).append(section_path.name)
        # Inline image embeds: ![...](figures/fig3_...)
        for m in INLINE_IMAGE_RE.finditer(text):
            n = int(m.group(1))
            callouts.setdefault(n, []).append(section_path.name)
    # Dedup section_filenames per N.
    for n in list(callouts.keys()):
        callouts[n] = sorted(set(callouts[n]))
    return callouts


# ---------------------------------------------------------------------------
# Cross-walk checks
# ---------------------------------------------------------------------------


def check_filename_existence(rows: list[dict], figures_dir: Path) -> list[str]:
    """Each row's filename must exist as a file in <figures_dir>."""
    warnings: list[str] = []
    for row in rows:
        target = figures_dir / row["filename"]
        if not target.is_file():
            warnings.append(
                f"figures_manifest.tsv row {row['lineno']} "
                f"(paper_order_n={row['paper_order_n']}): filename "
                f"{row['filename']!r} does not exist at {target}"
            )
    return warnings


def check_orphan_figures(rows: list[dict], figures_dir: Path) -> list[str]:
    """Every fig*.<ext> in figures_dir must have a manifest row."""
    warnings: list[str] = []
    if not figures_dir.is_dir():
        return warnings
    manifest_filenames = {row["filename"] for row in rows}
    for entry in sorted(figures_dir.iterdir()):
        if not entry.is_file():
            continue
        if not FIGURE_FILENAME_RE.match(entry.name):
            continue
        if entry.name not in manifest_filenames:
            warnings.append(
                f"orphan figure (no manifest row): {entry.name}; "
                f"either delete the orphan or add it to the manifest"
            )
    return warnings


def check_callouts_match_manifest(
    rows: list[dict],
    callouts: dict[int, list[str]],
) -> list[str]:
    """Every (Fig. N) callout's N must have a manifest row with that N."""
    warnings: list[str] = []
    manifest_ns = {row["paper_order_n"] for row in rows}
    for n in sorted(callouts.keys()):
        if n not in manifest_ns:
            sections = ", ".join(callouts[n])
            warnings.append(
                f"prose cites (Fig. {n}) in [{sections}] but the manifest "
                f"has no row with paper_order_n={n}; phase_embed_figures "
                f"will not be able to inject this image"
            )
    # Also: figures in manifest with no callout are a softer signal —
    # results.v1's HALT discipline should catch this, but we surface it
    # as NOTE (not WARN) because it's already covered by the prompt's
    # self-review.
    callout_ns = set(callouts.keys())
    for n in sorted(manifest_ns):
        if n not in callout_ns:
            warnings.append(
                f"NOTE: paper_order_n={n} is in manifest but no "
                f"(Fig. {n}) callout found in section prose; this figure "
                f"will not be embedded by phase_embed_figures"
            )
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_figures_manifest.py",
        description=(
            "Cross-walk figures_manifest.tsv against the figures/ directory "
            "and section prose's (Fig. N) callouts. Advisory; always exits 0."
        ),
    )
    parser.add_argument(
        "draft_dir",
        help="Path to the paper draft directory (e.g., papers/draft_1/).",
    )
    args = parser.parse_args(argv)

    draft_dir = Path(args.draft_dir).expanduser().resolve()
    if not draft_dir.is_dir():
        sys.stderr.write(
            f"[check_figures_manifest] WARN: draft_dir does not exist: "
            f"{draft_dir}; skipping checks\n"
        )
        return 0

    manifest_path = draft_dir / "figures_manifest.tsv"
    figures_dir = draft_dir / "figures"

    all_lines: list[str] = []

    # Check 1: schema
    rows, schema_warnings = parse_manifest(manifest_path)
    all_lines.extend(
        f"[check_figures_manifest] WARN: schema: {w}" for w in schema_warnings
    )

    if not manifest_path.is_file():
        # No manifest — most other checks aren't meaningful. Emit summary
        # and exit (still 0; advisory).
        all_lines.append(
            "[check_figures_manifest] NOTE: figures_manifest.tsv missing — "
            "phase_embed_figures will have nothing to inject."
        )
        for w in all_lines:
            sys.stderr.write(f"{w}\n")
        sys.stderr.write(
            f"[check_figures_manifest] summary: 0 manifest rows, "
            f"{len(schema_warnings)} schema warnings\n"
        )
        return 0

    # Check 2: filename existence
    if rows:
        existence_warnings = check_filename_existence(rows, figures_dir)
        all_lines.extend(
            f"[check_figures_manifest] WARN: file_missing: {w}"
            for w in existence_warnings
        )

    # Check 3: orphan figures
    orphan_warnings = check_orphan_figures(rows, figures_dir)
    all_lines.extend(
        f"[check_figures_manifest] WARN: orphan: {w}" for w in orphan_warnings
    )

    # Check 4: callout cross-walk
    section_paths = [draft_dir / name for name in SECTION_FILES]
    callouts = collect_callouts(section_paths)
    callout_warnings = check_callouts_match_manifest(rows, callouts)
    for w in callout_warnings:
        if w.startswith("NOTE:"):
            all_lines.append(
                f"[check_figures_manifest] NOTE: callout_unused: {w[5:].strip()}"
            )
        else:
            all_lines.append(
                f"[check_figures_manifest] WARN: callout_orphan: {w}"
            )

    # Emit.
    for w in all_lines:
        sys.stderr.write(f"{w}\n")

    n_warn = sum(1 for w in all_lines if "WARN" in w[:40])
    n_note = sum(1 for w in all_lines if "NOTE" in w[:40])
    sys.stderr.write(
        f"[check_figures_manifest] summary: {len(rows)} manifest rows, "
        f"{len(callouts)} distinct callout numbers, "
        f"{n_warn} WARN, {n_note} NOTE\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
