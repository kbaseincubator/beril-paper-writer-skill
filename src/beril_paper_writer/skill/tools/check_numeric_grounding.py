#!/usr/bin/env python3
"""check_numeric_grounding.py — Tier 1 deterministic numeric-grounding check.

Stage 4 Tier T (2026-05-17). Companion to the canonical adversarial
reviewer's Tier 3 LLM-driven check. This tool walks the assembled
``manuscript.md``, extracts every numeric claim (using the seven-class
regex catalog from ``claim_inventory.py``), and grounds each one
against:

  Tier A — ``<draft_dir>/claim_inventory.tsv`` (the Phase 0
           extract_claims output, which carries REPORT-sourced numeric
           claims with their source notebook + cell). A match in Tier
           A means the draft-side number traces to a specific notebook.

  Tier B — ``<project_dir>/REPORT.md`` text directly. Fallback for
           narrative numbers the inventory may have missed (the
           inventory is a *demarcated* subset of REPORT, not a
           transcript). A match in Tier B means the number is somewhere
           in REPORT prose, even if it didn't make it into the
           inventory.

  Tier C — Ungrounded. The number appears in the draft but cannot be
           traced to either the inventory or REPORT.md. **Strict mode
           (the only mode in v0.1): every ungrounded number is P0.**

The ibd_phage_targeting draft_1 live test (v0.8.0, 2026-05-17)
surfaced four P0 numeric findings from the canonical reviewer that
this tool would have caught deterministically at $0 LLM cost:
  - "219,121 gene-by-species associations" — fabricated transformation
  - "105/137 (77%)" — undisclosed denominator
  - "5 iterations bootstrap" — wrong number (REPORT says 1000)
  - "31 vs 32 vs 33 notebook count" — internal inconsistency
None traced to claim_inventory.tsv or REPORT.md; Tier 1 deterministic
grounding would have surfaced them before Tier 3 burned tokens on them.

False-positive control: an allowlist filters out classes of numbers
that are textually present but not empirical claims (figure / table
references, citation bracket numbers, publication years, small
counts in trivial contexts, section / paragraph references). The
allowlist is **conservative by design** — false positives are easier
to triage and add to the list than false negatives.

Output: ``<draft_dir>/audit/numeric_grounding.json`` with a stable
schema (``schema_version: v1``). Exit 0 always (advisory); the
orchestrator decides whether to gate downstream phases on the finding
count.

CLI::

    python3 check_numeric_grounding.py <draft_dir>
                                       [--report-path REPORT.md]
                                       [--inventory-path claim_inventory.tsv]
                                       [--quiet]

Importable as a module for unit testing; pure functions for the
numeric normalization, allowlist, and grounding tiers.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Re-use the regex catalog and the manuscript section walker from
# their authoritative homes. claim_inventory owns the D-036 catalog;
# citation_pool owns the holistic-flow heading canonicalizer (added
# in Stage 4 Tier R-1). Importing here keeps both contracts in one
# place across the skill.
try:
    # Package-import path (when run via the pipx-installed skill).
    from beril_paper_writer.skill.tools.claim_inventory import (
        NumericMatch,
        extract_numeric_matches,
    )
    from beril_paper_writer.skill.tools.citation_pool import (
        _canonicalize_heading,
        _MANUSCRIPT_HEADING_RE,
    )
except ImportError:
    # Direct-execution fallback for the bash flow / debugging.
    _HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(_HERE.parent.parent.parent))
    from beril_paper_writer.skill.tools.claim_inventory import (  # type: ignore  # noqa: E501
        NumericMatch,
        extract_numeric_matches,
    )
    from beril_paper_writer.skill.tools.citation_pool import (  # type: ignore  # noqa: E501
        _canonicalize_heading,
        _MANUSCRIPT_HEADING_RE,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"
# 0.2.0-d052: D-052 (#41) added scientific-notation / K-suffix /
# trailing-zero normalization. Bumped so emitted numeric_grounding.json
# audit metadata distinguishes post-#41 output from pre-#41.
TOOL_VERSION = "0.2.0-d052"


# Allowlist patterns. Each matches an entire ``matched_text`` (anchored
# with ``re.fullmatch``) or a contextual surrounding (we inspect a
# small window around the match to decide). Order matters: the first
# matching reason wins for the audit JSON.
#
# False positives are cheap to add here once observed. False negatives
# (real fabrications slipping through) are expensive. Lean conservative.


# 1) Citation bracket numbers: [1], [12], [1,2,3], [N]. The
#    citation_pool finalize pass renders these AFTER assembly, so they
#    appear as "[N]" in the final manuscript.md.
_CITATION_BRACKET_RE = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")

# 2) Figure/Table/Panel references, including bare "Fig. 3" / "Table 2"
#    / "Panel A" forms. Case-insensitive. Note "Figure X" where X is a
#    letter (A/B/C) is structural too.
_FIG_TABLE_RE = re.compile(
    r"\b(?:fig(?:ure)?|tbl|table|panel|supplementary\s+fig(?:ure)?|"
    r"suppl?\.?\s+(?:fig(?:ure)?|table)|s\s*fig)"
    r"\.?\s*[A-Z]?\d*[a-z]?(?:[-–]\d+[a-z]?)?",
    re.IGNORECASE,
)

# 3) Section / Equation / Hypothesis references: "Section 3", "Eq. 2",
#    "Hypothesis H1a", "H3c", "Pillar 4", "Tier 3", "Phase 0".
_SECTIONAL_RE = re.compile(
    r"\b(?:section|sec\.?|equation|eq\.?|hypothesis|h\d+[a-z]?|"
    r"pillar|tier|phase|chapter|appendix|paragraph|para\.?)"
    r"\s+[A-Z]?\d+[a-z]?\b",
    re.IGNORECASE,
)

# 4) Publication years (1900-2099) — appear in citations like
#    "(Smith, 2023)" and in narrative prose like "since 2018". They
#    are real numbers but not empirical claims that need grounding.
_YEAR_RE = re.compile(r"\b(?:19\d\d|20\d\d)\b")

# 5) Trivial small-integer counts in throwaway contexts ("a 5-step
#    pipeline", "with 3 phases", "applied to 4 cohorts"). Suppressed
#    only when preceded immediately by an article or "to/with/of" and
#    followed by a noun — i.e. a noun-phrase numeric, not a measured
#    quantity. Conservative: ANY two-digit-or-more number is not
#    suppressed even in this context.
_TRIVIAL_COUNT_CTX_RE = re.compile(
    r"\b(?:a|an|the|to|with|of|in|on|for|by|each|every|all|some|"
    r"these|those|its)\s+\d{1,2}(?=[\s\-]|\b)",
    re.IGNORECASE,
)

# 6) Bibliography-style numbers in a "References" section that the
#    finalize step may have rendered with numeric prefixes. The
#    numeric_grounding walker DOES respect section boundaries, so a
#    matched_text inside the References section gets allowlisted
#    automatically (see _section_allowlist_set).

# Sections to fully allowlist (every numeric inside them is structural,
# not a claim): References, Bibliography, Acknowledgments, AI Disclosure
# (which talks about LLM model names + version numbers, not science).
_SECTION_ALLOWLIST = frozenset({
    "references",
    "bibliography",
    "acknowledgments",
    "acknowledgements",
    "ai disclosure",
    "ai-assisted writing disclosure",
    "competing interests",
    "conflicts of interest",
    "funding",
    "author contributions",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GroundingFinding:
    """One ungrounded numeric claim in the manuscript."""

    claim_text: str            # the sentence/clause containing the match
    matched_text: str          # the exact numeric substring
    normalized_value: str      # canonical form for cross-comparison
    match_class: str           # one of PATTERN_CLASSES' class names
    section: str               # canonicalized section label
    paragraph: int             # 1-indexed paragraph within section
    char_offset: int           # offset into manuscript.md
    severity: str              # always "P0" in strict mode
    rationale: str             # human-readable explanation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AllowlistedMatch:
    """One numeric match that was suppressed by the allowlist. Recorded
    in the JSON for transparency (so a reviewer can confirm the
    allowlist isn't eating real findings)."""

    matched_text: str
    section: str
    paragraph: int
    char_offset: int
    reason: str                # which allowlist rule fired

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GroundingReport:
    """Top-level JSON shape emitted to audit/numeric_grounding.json."""

    schema_version: str
    tool: str
    tool_version: str
    draft_dir: str
    manuscript_path: str
    inventory_path: Optional[str]
    report_path: Optional[str]
    totals: dict
    findings: list[dict]
    allowlisted: list[dict]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Numeric normalization
# ---------------------------------------------------------------------------

# Used to extract the bare numeric payload from a matched substring.
# Captures: optional sign, integer part, optional decimal part,
# optional exponent. Commas are stripped before the regex applies.
_NUMERIC_PAYLOAD_RE = re.compile(
    r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
)

# D-052 (#41) — drafter-form scientific notation, e.g. `1.5 x 10^-43`.
# Captured groups: (mantissa, signed_exponent). Mantissa may have a
# decimal portion; multiplication char is x/X/×/* with optional
# spaces; exponent caret optional; sign optional.
_SCI_NOTATION_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*[xX×*]\s*10\^?([-+]?\d+)"
)

# D-052 (#41) — SI suffix expansion. Applied SOURCE-SIDE ONLY in
# build_normalized_set so notebook outputs like `83K`, `1.5M` are
# expanded to integer form before the bare numeric extractor sees
# them. Lookahead `(?=\s|$|[^\w])` guards against `1.5MHz`-class
# collisions (next char being a word char fails the lookahead;
# `M` followed by `H` keeps `1.5MHz` intact, while `M` followed by
# space / punctuation / EOL expands).
_SI_SUFFIX_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "g": 1_000_000_000,
    "t": 1_000_000_000_000,
}
_SI_SUFFIX_RE = re.compile(
    r"(\d+(?:\.\d+)?)([KkMmGgTt])(?=\s|$|[^\w])"
)


def _canonical_form(raw: str) -> str:
    """Canonical string form of a numeric value for set-lookup
    comparison.

    Collapses representational variants of the same value into one
    key:
      - 82, 82.0, 8.20e1 → "82"
      - 0.30, 0.3 → "0.3"
      - 1.77e-6, 1.77e-06 → "1.77e-6"
      - 1.5e-43 → "1.5e-43"

    Strict on truncation: 0.3 is NOT collapsed with 0.302.

    Uses Python's general (`%g`) format at 10 sig figs (sufficient
    for paper-context numbers; manuscripts rarely report beyond
    4-6 sig figs). Post-strips leading zeros in scientific-notation
    exponents so `format()`'s default `1.77e-06` and the drafter's
    common `1.77e-6` collide on the same key.

    Returns `raw` unchanged when it doesn't parse as a number.
    """
    try:
        v = float(raw)
    except (ValueError, OverflowError):
        return raw
    s = format(v, ".10g")
    # Strip leading zeros in the exponent: `e-06` → `e-6`, `e+05` → `e5`.
    s = re.sub(r"e([+-])0+(\d)", r"e\1\2", s)
    # Drop explicit `+` in the exponent: `1.5e+10` → `1.5e10` (consistent
    # with most drafter conventions). Python's %g already does this on
    # most platforms but be defensive.
    s = s.replace("e+", "e")
    return s


def _expand_si_suffixes(text: str) -> str:
    """Expand K/M/G/T SI suffixes to integer form. Applied SOURCE
    side only (inventory + REPORT) so notebook-shorthand `83K`
    can ground a manuscript `83,000`. See D-052 (#41) for the
    lookahead's role in avoiding `1.5MHz`-class collisions.

    Non-integer mantissas expand to integer when the product is
    whole (e.g. `1.5K` → `1500`), else fall back to canonical
    float form. Pure text substitution; idempotent on already-
    expanded text (no double-expansion).
    """
    def expand(m: re.Match[str]) -> str:
        try:
            mantissa = float(m.group(1))
        except ValueError:
            return m.group(0)
        suffix = m.group(2).lower()
        value = mantissa * _SI_SUFFIX_MULTIPLIERS[suffix]
        if value == int(value):
            return str(int(value))
        return _canonical_form(str(value))
    return _SI_SUFFIX_RE.sub(expand, text)


def normalize_numeric(matched_text: str) -> str:
    """Return a canonical numeric string from a regex-matched substring.

    Strategy:
      1. Strip thousand-separator commas (``219,121`` → ``219121``).
      2. Strip leading/trailing whitespace.
      3. Find the first numeric payload in the cleaned text.
      4. Lowercase the exponent marker (``5E-4`` → ``5e-4``).
      5. Drop leading "+", normalize "-0" to "0".

    Returns the empty string when no numeric payload is found (e.g.
    a percentage class match that lost its number to a regex
    boundary). The caller treats empty-normalized as "skip"; the
    audit log records it under ``notes`` for future tuning.

    Examples:
      "219,121"          → "219121"
      "77%"              → "77"
      "p=4e-4"           → "4e-4"
      "n = 156"          → "156"
      "0.96"             → "0.96"
      "1.5 x 10^-43"     → "1.5e-43"  (D-052 scientific notation)
      "1.1 x 10^-130"    → "1.1e-130"
    """
    if not matched_text:
        return ""
    cleaned = matched_text.replace(",", "").strip()
    # D-052 (#41) — recognize scientific notation `X.Y x 10^N` BEFORE
    # falling back to bare numeric extraction. Without this, the
    # generic _NUMERIC_PAYLOAD_RE finds the mantissa `1.5` first and
    # drops the `10^-43` exponent entirely.
    sci_m = _SCI_NOTATION_RE.search(cleaned)
    if sci_m:
        mantissa, exp = sci_m.group(1), sci_m.group(2)
        try:
            value = float(f"{mantissa}e{exp}")
            return _canonical_form(str(value))
        except (ValueError, OverflowError):
            pass  # fall through to bare extraction
    m = _NUMERIC_PAYLOAD_RE.search(cleaned)
    if not m:
        return ""
    raw = m.group(0).lower()
    # Drop leading '+', collapse '-0' to '0'.
    if raw.startswith("+"):
        raw = raw[1:]
    if raw in ("-0", "-0.0"):
        raw = "0"
    return raw


def build_normalized_set(text: str) -> set[str]:
    """Return the set of normalized numeric payloads from ``text``.

    Used to index the claim_inventory.tsv claim_texts and REPORT.md
    prose for grounding lookups. Uses a GENERIC numeric extractor
    (every ``\\d+`` token after comma-stripping) rather than the
    claim-shaped ``extract_numeric_matches`` because the source side
    needs to find ALL numbers regardless of surrounding linguistic
    shape — a number in a table cell, a number in dense prose without
    ``n=`` or ``X of Y`` keywords, etc. must still ground a
    claim-shaped match on the manuscript side.

    Stage 7 Patch 2 (2026-05-18): switched from extract_numeric_matches
    (claim-shaped) to a generic regex extractor after D1
    (conservation_vs_fitness) surfaced false-positive ungrounded
    numerics whose source values DID appear in REPORT.md but were in
    prose shapes the claim-shaped regexes didn't catch (e.g.,
    "27,693 putative essential genes identified (18.6% of 148,826
    ... 33 organisms; range 12.9-28.9%)" — only "18.6%" was
    extracted as a claim by the prior implementation, even though
    27693, 148826, 33, 12.9, 28.9 are all present in the prose).

    Tradeoff: tiny ints (0–9) in REPORT can now ground unrelated
    tiny claims in the manuscript (e.g., "Phase 2" in REPORT
    grounds a "2 x" claim in the manuscript). Accepted for v1; v1.1
    will add context-aware matching against claim_inventory.tsv's
    ``claim_text`` column (fuzzy match on the surrounding sentence).

    Mirrors normalize_numeric's normalization rules: strip commas,
    drop leading "+", normalize "-0" / "-0.0" → "0".

    D-052 (#41) — three additional normalization passes:

      A. K/M/G/T SI suffix expansion via _expand_si_suffixes (source
         side only). `83K` → `83000`, `1.5M` → `1500000`. Closes
         D3's `83,000` (manuscript) vs `83K` (inventory) gap.

      B. Explicit scientific-notation token extraction via
         _SCI_NOTATION_RE before the bare-number sweep. The generic
         _NUMERIC_PAYLOAD_RE would otherwise tokenize `1.5e-43` and
         `1.5 x 10^-43` into different sets of payloads. The
         _SCI_NOTATION_RE pass emits the canonical form
         (`1.5e-43`) for both surface forms. Already-`eE`-form
         scientific notation (`1.5e-43`) is also picked up here so
         it canonicalizes uniformly.

      C. Canonical-form application via _canonical_form on every
         emitted value. Collapses `82.0` → `82`, `0.30` → `0.3`,
         `1.77e-06` → `1.77e-6`.
    """
    # A. SI-suffix expansion before the comma strip.
    expanded = _expand_si_suffixes(text)
    cleaned = expanded.replace(",", "")
    out: set[str] = set()
    # B. Extract scientific-notation tokens first, mask their spans
    #    so the bare-numeric sweep below doesn't double-count their
    #    mantissa/exponent digits as independent values.
    masked = list(cleaned)
    for m in _SCI_NOTATION_RE.finditer(cleaned):
        mantissa, exp = m.group(1), m.group(2)
        try:
            value = float(f"{mantissa}e{exp}")
            out.add(_canonical_form(str(value)))
        except (ValueError, OverflowError):
            continue
        # Mask out the matched span so _NUMERIC_PAYLOAD_RE skips it.
        for i in range(m.start(), m.end()):
            masked[i] = " "
    cleaned = "".join(masked)
    # Original bare-number sweep, now operating on the post-mask text.
    for m in _NUMERIC_PAYLOAD_RE.finditer(cleaned):
        raw = m.group(0).lower()
        if raw.startswith("+"):
            raw = raw[1:]
        if raw in ("-0", "-0.0"):
            raw = "0"
        # D-052 (#41) — apply canonical form so trailing-zero variants
        # collapse: `82.0` → `82`, `0.30` → `0.3`, `1.77e-06` → `1.77e-6`.
        out.add(_canonical_form(raw))
        # Range-dash carve-out: when the regex matches "-28.9" inside
        # a range like "12.9-28.9", the leading dash is regex-consumed
        # as a sign, but semantically it's a range separator. Add the
        # unsigned form too so the manuscript's "28.9%" grounds.
        # Cost: source set now stores both signed and unsigned forms
        # for every negative match — but Tier T is value-presence-
        # not sign-correctness, so this is correct behavior. Sign-
        # misuse detection is the Tier 3 adversarial reviewer's job.
        if raw.startswith("-") and len(raw) > 1:
            out.add(_canonical_form(raw[1:]))
    return out


# ---------------------------------------------------------------------------
# Allowlist evaluation
# ---------------------------------------------------------------------------


def _context_window(text: str, start: int, end: int, radius: int = 25) -> str:
    """Return a short text window around the match for context-aware
    allowlist checks. Bounded to the document edges."""
    return text[max(0, start - radius): min(len(text), end + radius)]


# Claim-shaped match classes — these are by definition empirical
# claims and must NEVER be suppressed by context-window heuristics.
# Only structural rules (section_allowlist, citation_bracket,
# figure/table refs) may suppress them. count_of in particular has
# the shape "X of Y" which trivially matches the preposition+digit
# context pattern; without this guard the tool eats every n/N ratio
# claim in the manuscript (observed on draft_1: 4 false-suppressions
# including the "105 of 137" P0).
_CLAIM_SHAPED_CLASSES = frozenset({
    "percentage",
    "ratio_with_unit",
    "p_value",
    "confidence_interval",
    "n_count",
    "metric",
    "correlation",
    "odds_ratio",
    "log_fc",
    "count_of",
    "cliff_delta",
    # D-052 (#41) — scientific_notation class added.
    "scientific_notation",
})


def allowlist_reason(
    matched_text: str,
    match_start: int,
    match_end: int,
    full_text: str,
    section: str,
    match_class: str = "",
) -> Optional[str]:
    """Return the name of the allowlist rule that suppresses this match,
    or None if no rule applies (the match passes through to grounding).

    Order matters: structural rules (section, citation brackets,
    figure refs) fire before context-window rules so the audit JSON
    records the most specific reason.

    Stage 4 Tier T-1 (2026-05-17 smoke against draft_1): pass
    ``match_class`` so claim-shaped classes (count_of, n_count,
    percentage, p_value, etc.) skip the context-window trivial-count
    heuristic — those classes ARE claims by definition. The
    structural allowlists (section, citation_bracket, figure_ref,
    sectional_ref, publication_year) still apply to all classes.
    """
    if section in _SECTION_ALLOWLIST:
        return f"section_allowlist:{section}"

    # The matched_text may itself BE the bracket / figure ref, or it
    # may be a digit string nested inside one. Inspect a small window
    # to catch the latter case (e.g. "[1]" — the regex catches "1"
    # but the brackets are at ±1).
    window = _context_window(full_text, match_start, match_end)
    # Tight check: the match's exact span maps to a citation bracket
    # iff the wider window contains "[<digits>]" overlapping this match.
    span_text = full_text[match_start:match_end]
    if _CITATION_BRACKET_RE.fullmatch(span_text):
        return "citation_bracket"
    # Or the matched_text is wholly inside a bracket pair containing
    # only digits + commas: the typical "1" in "[1,2,3]".
    pre = full_text[max(0, match_start - 1):match_start]
    post = full_text[match_end:match_end + 1]
    if pre == "[" and (post == "]" or post == ","):
        return "citation_bracket_inner"
    # Or the comma-separated middle of "[1,2,3]" — pre is "," and
    # post is "," or "]".
    if pre == "," and (post == "]" or post == ","):
        return "citation_bracket_inner"

    # Figure / Table / Panel ref in the window?
    if _FIG_TABLE_RE.search(window):
        # Only allowlist if the match is part of the fig-ref span,
        # not an unrelated number near a figure caption.
        for m in _FIG_TABLE_RE.finditer(window):
            abs_start = max(0, match_start - 25) + m.start()
            abs_end = max(0, match_start - 25) + m.end()
            if abs_start <= match_start and match_end <= abs_end:
                return "figure_or_table_ref"

    # Section/Equation/Hypothesis/Pillar reference?
    if _SECTIONAL_RE.search(window):
        for m in _SECTIONAL_RE.finditer(window):
            abs_start = max(0, match_start - 25) + m.start()
            abs_end = max(0, match_start - 25) + m.end()
            if abs_start <= match_start and match_end <= abs_end:
                return "sectional_ref"

    # Publication year (1900-2099) — only allowlist if the match is
    # exactly a 4-digit year.
    if _YEAR_RE.fullmatch(span_text):
        return "publication_year"

    # Trivial small-integer count in noun-phrase context (e.g.,
    # "a 5-step pipeline"). The matched_text must be a 1-2 digit
    # integer; the window must show an article/preposition right
    # before. NEVER applies to claim-shaped match_classes (the bug
    # caught on draft_1: count_of's "X of Y" trivially matches
    # "preposition+digit", suppressing real n/N claims).
    if match_class in _CLAIM_SHAPED_CLASSES:
        return None
    norm = normalize_numeric(span_text)
    if norm and norm.isdigit() and 0 < int(norm) < 100:
        back_start = max(0, match_start - 12)
        back_window = full_text[back_start:match_start]
        if _TRIVIAL_COUNT_CTX_RE.search(back_window + span_text):
            return "trivial_noun_phrase_count"

    return None


# ---------------------------------------------------------------------------
# Inventory / Report ingestion
# ---------------------------------------------------------------------------


def load_inventory_claim_texts(inventory_path: Path) -> list[str]:
    """Parse claim_inventory.tsv and return the list of claim_text
    values. Header row is required (TSV_COLUMNS contract from
    claim_inventory.py). Empty claim_text values are dropped.

    Tolerant of missing-file: returns [] with a recorded note.
    """
    if not inventory_path.is_file():
        return []
    with inventory_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, dialect="excel-tab")
        return [
            row.get("claim_text", "").strip()
            for row in reader
            if row.get("claim_text", "").strip()
        ]


def build_inventory_normalized_set(claim_texts: list[str]) -> set[str]:
    """Run the numeric-match extractor over every inventory claim_text
    and union the normalized payloads. This is the Tier A grounding
    set: any draft-side number whose normalized form lands in this
    set is grounded to a notebook-traceable claim."""
    out: set[str] = set()
    for txt in claim_texts:
        out.update(build_normalized_set(txt))
    return out


def build_report_normalized_set(report_path: Optional[Path]) -> set[str]:
    """Run the numeric-match extractor over the entire REPORT.md text
    (when present) and union the normalized payloads. This is the
    Tier B grounding set: a draft-side number not in Tier A may
    still appear somewhere in REPORT prose that didn't make it into
    the inventory.

    Tolerant of missing-file: returns the empty set.
    """
    if report_path is None or not report_path.is_file():
        return set()
    text = report_path.read_text(encoding="utf-8")
    return build_normalized_set(text)


# ---------------------------------------------------------------------------
# Manuscript walker
# ---------------------------------------------------------------------------


def _iter_manuscript_with_sections(text: str):
    """Yield (line, line_start_offset, current_section, paragraph_n)
    for each non-blank line in ``text``, with section labels derived
    from ``## <Heading>`` markers (canonicalized via
    ``_canonicalize_heading``). Mirrors the heading-walker contract
    used by citation_pool.extract_citekeys_from_manuscript so the
    two checks agree on section attribution.

    Paragraph numbering: 1-indexed within the current section. The
    blank lines between a heading and the first content paragraph
    do NOT advance the counter (otherwise the first paragraph of
    every section ends up at paragraph_n=2). Once at least one
    content line in the section has yielded, subsequent blank-line
    runs do advance.
    """
    current_section = "front-matter"
    paragraph_n = 1
    pending_paragraph_bump = False
    has_yielded_in_section = False
    line_start = 0
    for line in text.split("\n"):
        line_end = line_start + len(line)
        heading_match = _MANUSCRIPT_HEADING_RE.match(line)
        if heading_match:
            current_section = _canonicalize_heading(heading_match.group(1))
            paragraph_n = 1
            pending_paragraph_bump = False
            has_yielded_in_section = False
            line_start = line_end + 1  # +1 for the stripped newline
            continue
        if not line.strip():
            # Leading blank lines between heading and first content
            # are header separators (no bump). Once content has
            # appeared, set a pending bump that fires on the NEXT
            # content line — collapsing runs of blank lines into one
            # paragraph break (the standard markdown convention).
            if has_yielded_in_section:
                pending_paragraph_bump = True
            line_start = line_end + 1
            continue
        if pending_paragraph_bump:
            paragraph_n += 1
            pending_paragraph_bump = False
        yield line, line_start, current_section, paragraph_n
        has_yielded_in_section = True
        line_start = line_end + 1


def _sentence_for_offset(
    text: str, offset: int, *, max_radius: int = 250,
) -> str:
    """Return a sentence-ish window around ``offset``, bounded by
    sentence-terminator punctuation (.!?) within ``max_radius`` chars
    either side. Conservative — when in doubt, return more context."""
    start = max(0, offset - max_radius)
    end = min(len(text), offset + max_radius)
    # Find nearest sentence boundary BEFORE the offset.
    for boundary in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.rfind(boundary, start, offset)
        if idx > start:
            start = idx + len(boundary)
            break
    # Find nearest sentence boundary AFTER the offset.
    for boundary in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.find(boundary, offset, end)
        if idx >= 0:
            end = idx + 1
            break
    return text[start:end].strip()


# ---------------------------------------------------------------------------
# Core: run grounding pass
# ---------------------------------------------------------------------------


def run_grounding(
    manuscript_text: str,
    inventory_normalized: set[str],
    report_normalized: set[str],
) -> tuple[list[GroundingFinding], list[AllowlistedMatch], dict]:
    """Walk ``manuscript_text``, score every numeric match against the
    grounding sets, return (ungrounded findings, allowlisted matches,
    totals). Pure function — easy to unit-test."""

    findings: list[GroundingFinding] = []
    allowlisted: list[AllowlistedMatch] = []
    total_matches = 0
    grounded_a = 0
    grounded_b = 0
    skipped_empty_norm = 0

    for line, line_start, section, paragraph_n in _iter_manuscript_with_sections(
        manuscript_text,
    ):
        for m in extract_numeric_matches(line):
            total_matches += 1
            abs_offset = line_start + m.start
            norm = normalize_numeric(m.matched_text)

            # If normalization failed (rare), record but don't gate.
            if not norm:
                skipped_empty_norm += 1
                continue

            # Allowlist evaluation — uses the full manuscript text and
            # absolute offsets so the context window has the right
            # neighbors (line-only context misses the closing bracket
            # of "[1]\n" when the bracket lands at line end).
            reason = allowlist_reason(
                m.matched_text,
                abs_offset,
                abs_offset + (m.end - m.start),
                manuscript_text,
                section,
                match_class=m.match_class,
            )
            if reason is not None:
                allowlisted.append(AllowlistedMatch(
                    matched_text=m.matched_text,
                    section=section,
                    paragraph=paragraph_n,
                    char_offset=abs_offset,
                    reason=reason,
                ))
                continue

            # Grounding cascade. D-052 (#41) — apply canonical form
            # to the manuscript-side normalized value so trailing-zero
            # and exponent-representation variants collide with the
            # canonical-form keys in the inventory/report sets.
            canonical = _canonical_form(norm)
            if canonical in inventory_normalized:
                grounded_a += 1
                continue
            if canonical in report_normalized:
                grounded_b += 1
                continue

            # Ungrounded. Strict severity = P0.
            sentence = _sentence_for_offset(manuscript_text, abs_offset)
            findings.append(GroundingFinding(
                claim_text=sentence,
                matched_text=m.matched_text,
                normalized_value=norm,
                match_class=m.match_class,
                section=section,
                paragraph=paragraph_n,
                char_offset=abs_offset,
                severity="P0",
                rationale=(
                    f"Numeric value {norm!r} not found in "
                    f"claim_inventory.tsv (Tier A) or REPORT.md "
                    f"(Tier B). Manuscript section: {section}, "
                    f"paragraph {paragraph_n}."
                ),
            ))

    totals = {
        "numeric_matches_in_manuscript": total_matches,
        "allowlisted": len(allowlisted),
        "grounded_tier_a_inventory": grounded_a,
        "grounded_tier_b_report_md": grounded_b,
        "ungrounded": len(findings),
        "skipped_empty_normalization": skipped_empty_norm,
    }
    return findings, allowlisted, totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_default_report_path(draft_dir: Path) -> Optional[Path]:
    """The BERDL project convention: <project_root>/REPORT.md sits
    three parents up from a draft directory (``papers/draft_N/``).
    Returns the path iff it exists; else None.
    """
    candidate = draft_dir.parent.parent / "REPORT.md"
    return candidate if candidate.is_file() else None


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="check_numeric_grounding.py",
        description=(
            "Stage 4 Tier T: walk manuscript.md for numeric claims, "
            "ground each against claim_inventory.tsv (Tier A) and "
            "REPORT.md (Tier B). Strict mode: every ungrounded "
            "number is P0. Writes audit/numeric_grounding.json."
        ),
    )
    p.add_argument(
        "draft_dir", type=Path,
        help="Draft directory (papers/draft_N/) containing manuscript.md.",
    )
    p.add_argument(
        "--report-path", type=Path, default=None,
        help=(
            "Path to REPORT.md. Default: <draft_dir>/../../REPORT.md "
            "(BERDL project convention)."
        ),
    )
    p.add_argument(
        "--inventory-path", type=Path, default=None,
        help=(
            "Path to claim_inventory.tsv. Default: "
            "<draft_dir>/claim_inventory.tsv."
        ),
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-finding stderr lines (still writes JSON).",
    )
    args = p.parse_args(argv)

    draft_dir: Path = args.draft_dir
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    manuscript_path = draft_dir / "manuscript.md"
    if not manuscript_path.is_file():
        print(
            f"error: manuscript.md not found at {manuscript_path}",
            file=sys.stderr,
        )
        return 1

    inventory_path: Optional[Path] = (
        args.inventory_path
        if args.inventory_path is not None
        else draft_dir / "claim_inventory.tsv"
    )
    report_path: Optional[Path] = (
        args.report_path
        if args.report_path is not None
        else _resolve_default_report_path(draft_dir)
    )

    manuscript_text = manuscript_path.read_text(encoding="utf-8")
    inventory_claim_texts = load_inventory_claim_texts(
        inventory_path if (inventory_path and inventory_path.is_file()) else Path("/dev/null"),
    )
    inventory_normalized = build_inventory_normalized_set(inventory_claim_texts)
    report_normalized = build_report_normalized_set(report_path)

    findings, allowlisted, totals = run_grounding(
        manuscript_text, inventory_normalized, report_normalized,
    )

    notes: list[str] = []
    if not (inventory_path and inventory_path.is_file()):
        notes.append(
            "claim_inventory.tsv missing — Tier A grounding disabled. "
            "Run phase_triage to produce it."
        )
    if report_path is None or not report_path.is_file():
        notes.append(
            "REPORT.md not found via default path — Tier B grounding "
            "disabled. Pass --report-path to enable fallback grounding."
        )

    report = GroundingReport(
        schema_version=SCHEMA_VERSION,
        tool="check_numeric_grounding",
        tool_version=TOOL_VERSION,
        draft_dir=str(draft_dir),
        manuscript_path=str(manuscript_path),
        inventory_path=(
            str(inventory_path) if inventory_path else None
        ),
        report_path=str(report_path) if report_path else None,
        totals=totals,
        findings=[f.to_dict() for f in findings],
        allowlisted=[a.to_dict() for a in allowlisted],
        notes=notes,
    )

    audit_dir = draft_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_path = audit_dir / "numeric_grounding.json"
    out_path.write_text(
        json.dumps(asdict(report), indent=2) + "\n",
        encoding="utf-8",
    )

    # Human-readable summary to stderr (orchestrator logs this).
    print(
        f"check_numeric_grounding: "
        f"{totals['numeric_matches_in_manuscript']} numeric matches; "
        f"{totals['grounded_tier_a_inventory']} grounded(Tier A); "
        f"{totals['grounded_tier_b_report_md']} grounded(Tier B); "
        f"{totals['allowlisted']} allowlisted; "
        f"{totals['ungrounded']} UNGROUNDED. "
        f"→ {out_path}",
        file=sys.stderr,
    )

    if not args.quiet and findings:
        print("Top ungrounded findings (first 10):", file=sys.stderr)
        for f in findings[:10]:
            print(
                f"  [P0] {f.section} para {f.paragraph}: "
                f"{f.matched_text!r}  ({f.match_class})",
                file=sys.stderr,
            )

    # Exit 0 always — advisory. Orchestrator decides whether to gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
