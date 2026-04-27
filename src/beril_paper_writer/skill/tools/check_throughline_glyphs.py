#!/usr/bin/env python3
"""check_throughline_glyphs.py — strength-glyph cross-walk discipline (advisory).

Standalone script invoked by the shell orchestrator after plan.v1 writes
its candidates file:

    python3 "$SKILL_DIR/tools/check_throughline_glyphs.py" \
        "$DRAFT_DIR/throughline_candidates.md"

Walks each `## Candidate TL{N}:` block, parses the Evidence map markdown
table, counts strength glyphs (✓ direct / ⚠ partial / ✗ contradicts /
◇ orthogonal), and cross-walks the Weakness inventory text for caveat
keywords that should normally surface as ⚠ partial somewhere in the map.

Why this exists. The plan.v1 prompt (SPEC §3.3, §4.2) requires sub-claim
strength glyphs that reflect operational evidence strength, not summary
flourish. Two consecutive smoke runs on `functional_dark_matter` produced
candidates whose Evidence map was 100% ✓ direct despite Weakness
inventories that named load-bearing caveats (weight-sensitivity, marginal
binomial p-values, annotation-vintage confounds, kingdom-level "may be
invisible to evidence layers" concerns). Prompt-level discipline did not
catch this; a programmatic post-processor does.

Behavior:
  - Emits per-candidate glyph counts to stderr (always).
  - Emits a WARN line to stderr for any candidate whose Evidence map has
    zero ⚠ partial AND zero ✗ contradicts entries (≥3 rows) BUT whose
    Weakness inventory contains caveat keywords or a `p=0.0XX` style
    p-value. This is the cross-walk failure mode.
  - Emits a NOTE line for the same shape with no detected caveats
    (genuinely gap-free claims are rare but possible).
  - Emits a WARN if a candidate's Evidence map appears empty.
  - Always exits 0. The script is advisory; the orchestrator surfaces
    warnings to the user in the plan.v1 closing message.

The script can be imported as a module for unit testing; parsing
helpers are pure (text in, dict/list out).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GLYPH_DIRECT = "\u2713"        # ✓
GLYPH_PARTIAL = "\u26a0"       # ⚠
GLYPH_CONTRADICTS = "\u2717"   # ✗
GLYPH_ORTHOGONAL = "\u25c7"    # ◇

GLYPHS = (GLYPH_DIRECT, GLYPH_PARTIAL, GLYPH_CONTRADICTS, GLYPH_ORTHOGONAL)

# Keywords/phrases in a Weakness inventory that normally translate to
# ⚠ partial on at least one Evidence map sub-claim. Lowercased substring
# match. Curated from the failure modes seen in plan.v1 v1/v2 smoke runs
# on functional_dark_matter; extend as new failure cases surface in real
# use. Each entry is paired with a short reason for the maintainer.
CAVEAT_KEYWORDS = (
    "marginal",                # marginal significance (p≈0.05–0.10)
    "weight-sensitive",        # scoring weights perturb top-N membership
    "weight perturbations",
    "sensitive to weight",
    "moderately sensitive",
    "coarse",                  # taxonomic/spatial coarseness
    "rejected",                # tested-and-rejected sub-hypothesis
    "compositional",           # compositional coupling / inflation
    "indistinguishable from null",
    "circular",                # circular evidence stack
    "arbitrary",               # arbitrary thresholds
    "annotation vintage",      # annotation-lagging vs functionally-dark
    "annotation-lagging",
    "barely above chance",
    "confounded",              # confounded by phylogeny / abundance / etc.
    "guilt-by-association",    # not experimental validation
    "not experimental validation",
    "may be invisible",        # evidence layers don't apply
    "overstates",              # "overstates the case"
    "overstate",
    "may misclassify",
)

# p-value pattern that catches `p = 0.072`, `p=0.05`, `p = 0.031` etc.
# Anything ≤ 0.10 is plausibly a marginal-or-just-significant result that
# should be reflected as ⚠ partial unless the author justifies otherwise.
P_VALUE_PATTERN = re.compile(r"\bp\s*=\s*0\.0\d+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_candidates(text: str) -> list[dict]:
    """Split the candidates file into per-candidate blocks.

    Returns a list of dicts: {id, title, body}. The body is everything
    between the candidate's H2 line and the next H2 line (or end of file).
    """
    parts = re.split(r"^## Candidate (TL\d+):\s*", text, flags=re.MULTILINE)
    candidates: list[dict] = []
    for i in range(1, len(parts), 2):
        cid = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        title_line, _, rest = body.partition("\n")
        # Stop body at the next H2 (--- or next candidate). We already
        # split on candidate H2; trim trailing horizontal-rule separators.
        rest = re.sub(r"\n---\s*\n.*$", "", rest, flags=re.DOTALL)
        candidates.append({"id": cid, "title": title_line.strip(), "body": rest})
    return candidates


def split_sections(body: str) -> dict[str, str]:
    """Split a candidate body by `**Section name:**` headers.

    Returns a dict mapping (case-preserved) header text to section
    content. Headers must appear at line start to count.
    """
    sections: dict[str, str] = {}
    pattern = re.compile(r"^\*\*([^*\n]+):\*\*\s*$", re.MULTILINE)
    matches = list(pattern.finditer(body))
    for j, m in enumerate(matches):
        header = m.group(1).strip()
        start = m.end()
        end = matches[j + 1].start() if j + 1 < len(matches) else len(body)
        sections[header] = body[start:end].strip()
    return sections


def count_glyphs(evidence_map: str) -> tuple[dict[str, int], int]:
    """Count strength glyphs in evidence-map markdown table rows.

    Only considers lines that begin with `|` and are not the header or
    separator. Returns ({glyph: count}, total_data_rows).
    """
    counts = {g: 0 for g in GLYPHS}
    rows = 0
    for line in evidence_map.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.match(r"^\|\s*-+\s*\|", s):  # separator row `|---|---|---|`
            continue
        if "Sub-claim" in s and "Strength" in s:  # header row
            continue
        rows += 1
        for g in GLYPHS:
            counts[g] += s.count(g)
    return counts, rows


def find_caveats(weakness: str) -> list[str]:
    """Return the caveat keywords/p-values found in the weakness inventory."""
    found: list[str] = []
    lower = weakness.lower()
    for kw in CAVEAT_KEYWORDS:
        if kw in lower:
            found.append(kw)
    found.extend(P_VALUE_PATTERN.findall(weakness))
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


def check(path: Path, verbose: bool = True) -> int:
    """Walk the candidates file; emit warnings to stderr.

    Returns the count of WARN lines emitted (NOTE lines do not count).
    Always-exit-0 contract is enforced by main(); this function returns
    the count for callers/tests.
    """
    text = path.read_text(encoding="utf-8")
    candidates = parse_candidates(text)
    if not candidates:
        print(
            f"[check_throughline_glyphs] WARN: no '## Candidate TL{{N}}:' "
            f"blocks found in {path}",
            file=sys.stderr,
        )
        return 1

    n_warnings = 0
    for c in candidates:
        sections = split_sections(c["body"])
        evidence_map = sections.get("Evidence map", "")
        weakness = sections.get("Weakness inventory", "")
        glyphs, rows = count_glyphs(evidence_map)
        n_direct = glyphs[GLYPH_DIRECT]
        n_partial = glyphs[GLYPH_PARTIAL]
        n_contradict = glyphs[GLYPH_CONTRADICTS]
        n_orth = glyphs[GLYPH_ORTHOGONAL]

        if verbose:
            print(
                f"[check_throughline_glyphs] {c['id']}: {rows} rows, "
                f"{n_direct} \u2713 / {n_partial} \u26a0 / "
                f"{n_contradict} \u2717 / {n_orth} \u25c7",
                file=sys.stderr,
            )

        # Empty / unparseable evidence map.
        if rows == 0:
            print(
                f"[check_throughline_glyphs] WARN {c['id']}: "
                f"evidence map appears empty or unparseable.",
                file=sys.stderr,
            )
            n_warnings += 1
            continue

        # Cross-walk: 100% ✓ direct AND caveats present in weakness inventory.
        if rows >= 3 and n_partial == 0 and n_contradict == 0:
            caveats = find_caveats(weakness)
            if caveats:
                print(
                    f"[check_throughline_glyphs] WARN {c['id']}: "
                    f"{n_direct}/{rows} sub-claims marked \u2713 direct "
                    f"with 0 \u26a0 / 0 \u2717, but Weakness inventory names "
                    f"caveats: {caveats}. "
                    f"Cross-walk: at least one sub-claim should likely be "
                    f"\u26a0 partial. See plan.v1 prompt §self-review item 5/6.",
                    file=sys.stderr,
                )
                n_warnings += 1
            else:
                print(
                    f"[check_throughline_glyphs] NOTE {c['id']}: "
                    f"{n_direct}/{rows} sub-claims marked \u2713 direct "
                    f"with no caveats detected in Weakness inventory. "
                    f"Genuinely gap-free claims are rare; review by hand.",
                    file=sys.stderr,
                )

    return n_warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument(
        "path",
        type=Path,
        help="Path to throughline_candidates.md",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-candidate glyph-count summary lines.",
    )
    args = ap.parse_args()

    if not args.path.exists():
        print(
            f"[check_throughline_glyphs] ERROR: file not found: {args.path}",
            file=sys.stderr,
        )
        return 2

    n = check(args.path, verbose=not args.quiet)
    print(
        f"[check_throughline_glyphs] complete: {n} warning(s).",
        file=sys.stderr,
    )
    # Always 0 — advisory only. Orchestrator surfaces warnings.
    return 0


if __name__ == "__main__":
    sys.exit(main())
