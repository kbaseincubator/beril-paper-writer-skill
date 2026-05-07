#!/usr/bin/env python3
"""claim_inventory.py — Index of every numeric assertion in REPORT.md (Phase 0, v0.8).

Per SPEC_v0_8 §4.6 + DECISIONS.md D-034 Q2:

  v0.7.x's M7 validator (numerical claims have n + effect size + 95% CI)
  checks the manuscript AFTER drafting. v0.8 inverts: produce the
  inventory of claimable numbers up front, then the holistic write
  picks from the inventory rather than invents numbers.

  Output is a TSV (machine-readable for the holistic prompt's grounding):

    claim_id  claim_text  source_notebook  source_cell  figure_or_table
              effect_size_present  ci_present  pvalue_present  notes

  Phase 2's holistic prompt is told: "Every numeric claim in your
  manuscript must reference a `claim_id`. Claims without a `claim_id`
  are forbidden. Claims with effect_size_present=no AND ci_present=no
  AND pvalue_present=no must be qualified with the appropriate hedge."
  This collapses the M7 validator from a post-hoc regex into a
  constructive constraint at draft time.

Pipeline:

  1. Deterministic regex extraction (THIS conversation: B1.a + B1.b):
       - Six pattern classes (percentages, ratios with units, p-values,
         CIs, n-counts, metrics — AUC/R²/RMSE/MAE).
       - Sentence segmentation with carve-outs for decimals, common
         abbreviations (Mr./Dr./Fig./Tbl./et al./i.e./e.g.), and
         paragraph breaks.
       - One candidate row per sentence containing ≥1 match. Multi-
         numeric sentences collapse to ONE candidate marked
         notes="unresolved" (LLM at B1.c demarcates them).
       - Effect-size / CI / p-value flags are aggregated PER SENTENCE
         from regex class membership. Tool-emitted, not LLM-emitted —
         per feedback_llm_arithmetic_unreliable, deterministic post-
         checks beat LLM self-counts.

  2. LLM demarcation pass (NEXT conversation: B1.c — NOT in this code):
       - Haiku-4.5 over `unresolved` candidates only.
       - Splits multi-numeric sentences into distinct claim_ids; assigns
         source_notebook+cell from methods_provenance.md;
         cross-links to figures/tables when applicable.
       - Cost ceiling $0.10/run (SPEC §4.6).

  3. Validator + idempotency cache (NEXT-NEXT — B1.d).

I/O contract (this milestone):

  --report <path>                required; REPORT.md
  --methods-provenance <path>    required; methods_provenance.md
                                 (passed through; LLM uses it in B1.c)
  --figures-inventory <path>     required; figures_inventory.md
                                 (passed through; LLM uses it in B1.c)
  --tables-inventory <path>      required; tables_inventory.md
                                 (passed through; LLM uses it in B1.c)
  --output-dir <path>            required; writes:
                                   <output-dir>/claim_inventory.tsv
                                   <output-dir>/audit/phase0.jsonl  (append)
  --no-llm                       debug; runs deterministic-only mode.
                                 Multi-numeric sentences are emitted as
                                 ONE candidate row marked
                                 notes="unresolved". Used by the C2.b
                                 ground-truth completeness check.

Exit codes:
  0 — success (TSV written; inventory size may be zero).
  1 — usage error (--help, missing required flag).
  2 — input parse error (a required file is missing or empty).
  3 — LLM call failure. In this milestone the LLM seam raises
      LLMNotImplemented; with --no-llm not set AND any unresolved
      (multi-numeric) candidates exist, main() maps the stub to
      exit 3 with a clear message ("pass --no-llm for the
      deterministic-only path"). Once B1.c lands, this becomes a
      real subprocess error code.
  4 — validator rejection of LLM output. Lands when B1.d's
      validator does. Reserved here.

Audit JSONL line schema (one line per invocation, appended to
<output-dir>/audit/phase0.jsonl):

  {
    "timestamp": "2026-05-07T14:23:01Z",
    "tool": "claim_inventory",
    "version": "0.8.0-m1-B1.ab",
    "inputs": {
      "report": "<sha256>",
      "methods_provenance": "<sha256>",
      "figures_inventory": "<sha256>",
      "tables_inventory": "<sha256>"
    },
    "output_path": "<absolute path to claim_inventory.tsv>",
    "inventory_size": 12,
    "unresolved_count": 3,
    "cost_usd": 0.0,
    "cache_hit": false,
    "exit_status": 0
  }

Discipline notes baked into this module (per auto-memory):

  - feedback_llm_json_unfixable_in_parser: any LLM JSON parse site
    MUST accompany prompt-side anti-pattern rules. Stub seam at B1.c.
  - feedback_llm_json_trailing_commas_repairable: lenient_json_load
    helper IS pre-baked here so B1.c can reuse it. Pattern matches
    discrepancy_register.lenient_json_load.
  - feedback_named_columns_in_inserts: TSV header row is self-
    describing. Downstream consumers parse by header NAME not column
    POSITION. Adding a column in v0.8.x is an additive operation
    consumers handle via header-name lookup.
  - feedback_render_test_must_evaluate_fstring: the TSV emitter is a
    regular function (not an f-string template), but unit tests still
    EVALUATE format_claim_inventory_tsv against synthetic candidates
    rather than grep the source.
  - feedback_no_git_writes_in_sandbox: this module never invokes git.
  - feedback_llm_arithmetic_unreliable: effect_size/ci/pvalue flags
    are deterministic. The LLM in B1.c never touches them — it only
    demarcates multi-numeric sentences and assigns notebook+cell
    citations.

This module is importable (functions are module-level + dataclass-based)
and runnable as a script (CLI under main()).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Module version. Distinct from package version because audit consumers
# may want to track precisely which sub-milestone wrote a given line.
# Bump on contract-affecting changes (new audit fields, schema changes).
# B1.a + B1.b lands as "0.8.0-m1-B1.ab"; B1.c bump to "B1.abc"; B1.d to
# "B1.abcd"; M1 close lands as "0.8.0-m1".
VERSION = "0.8.0-m1-B1.ab"


# ---------------------------------------------------------------------------
# TSV schema (self-describing header per feedback_named_columns_in_inserts)
# ---------------------------------------------------------------------------

# Column order is canonical and must match SPEC §4.6's example.
# Downstream consumers parse by header NAME not column POSITION (so an
# additive v0.8.x column extension does not break consumers reading by
# header lookup).
TSV_COLUMNS: tuple[str, ...] = (
    "claim_id",
    "claim_text",
    "source_notebook",
    "source_cell",
    "figure_or_table",
    "effect_size_present",
    "ci_present",
    "pvalue_present",
    "notes",
)


# ---------------------------------------------------------------------------
# Pattern catalog — six classes, one per M1_PUNCH_LIST §B1.b
# ---------------------------------------------------------------------------
#
# Each class is a NAMED constant. Each pattern's docstring cites why the
# pattern is bounded the way it is. Anchors and word boundaries are
# load-bearing — without them, "Mn=2" matches the n-count pattern and
# "PaUC" matches the metrics pattern.
#
# The full ordered tuple PATTERN_CLASSES is the source of truth for
# extraction order; the per-class regex is exposed publicly so unit
# tests can pin its behavior directly.


# Class 1 — Percentages (e.g. "88.2%", "5%").
#
# Word boundary at start prevents matching mid-token cases like
# "X88.2%" inside a larger identifier. Decimal portion is optional so
# integer percentages match too. Allows arbitrary digit count before
# the decimal — projects with "1234%" expression growth don't get
# silently truncated.
PERCENTAGE_RE = re.compile(r"\b\d+(?:\.\d+)?%")


# Class 2 — Ratios with units (e.g. "16.2 mg/L", "2.5 fold", "10×",
# "10x coverage", "12.5 kDa").
#
# Whitespace between number and unit is optional zero+. The unit set
# follows M1_PUNCH_LIST literally; if recall in C2.b's ground-truth
# fails on commonly-used units (ng/mL, U/mL, OD600, Tm), file E1.b
# (D-036) and extend the catalog.
#
# Trailing-boundary nuance: `\b` works for word-character-ending units
# (mg/L, fold, kDa) but NOT for `×` (U+00D7) which is non-word — `\b`
# requires a word/non-word transition, and `×` is already non-word so
# space-after-× has no transition. We use a lookahead instead:
# `(?=\s|$|[^\w])` — matches end-of-string, whitespace, or any
# non-word character. This catches `10× coverage` (space) AND `2.5
# fold.` (period after fold) AND `kb` at EOL, while still rejecting
# embedded `xtra` from matching at the leading `x`.
RATIO_WITH_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg/L|µM|nM|kb|bp|kDa|fold|×|x)(?=\s|$|[^\w])",
    re.IGNORECASE,
)


# Class 3 — p-values. Two construct flavors per M1_PUNCH_LIST:
#
#   "p < 0.05"  →  decimal flavor: `[pP]\s*[<=]\s*0\.\d+`
#   "p = 1.2e-5" → scientific flavor: `p\s*=\s*\d+\.\d+e-?\d+`
#
# Combined into one regex via alternation. The leading `\b` on `p`
# prevents matching the `p` inside identifiers like "comp" or
# "Speedup". `≤` (Unicode ≤ = U+2264) is not in the punch-list catalog
# and we don't extend it here — C2.b will tell us if recall demands it
# (it usually does in BERIL projects' Methods sections).
P_VALUE_RE = re.compile(
    r"\b[pP]\s*(?:<|<=|=|>=|>)\s*0\.\d+"
    r"|"
    r"\b[pP]\s*=\s*\d+\.\d+[eE]-?\d+",
)


# Class 4 — 95% confidence intervals. Permissive on the bracketed
# content because authors write "95% CI: [0.71, 0.85]" but also "95%
# CI 0.71-0.85" or "95% CI [0.71-0.85]". The trailing range is loose
# (digits / dots / dashes / commas / whitespace).
#
# This regex is intentionally permissive at the edges; downstream
# class-tagging only needs to know "this sentence has a CI claim",
# not the exact CI bounds. The LLM at B1.c can extract the bounds
# precisely from claim_text.
CI_RE = re.compile(
    r"95\s*%\s*CI[:,]?\s*\[?[\d.\-,\s]+\]?",
    re.IGNORECASE,
)


# Class 5 — N-counts (e.g. "n = 343", "N=156").
#
# Word boundary on `n` is critical: without it, "Mn=2" (manganese
# concentration) and the PCA-component pattern "PCA-component n=2"
# would match. Word boundary blocks "Mn=" but cannot block
# "PCA-component n=2" — that's an irreducible deterministic limitation.
# The LLM at B1.c will filter out PCA-component cases by reading the
# surrounding context. Documented limitation; not a bug.
N_COUNT_RE = re.compile(r"\b[nN]\s*=\s*\d+\b")


# Class 6 — Metrics: AUC, R² / R^2 / R2, RMSE, MAE.
#
# Word boundary on the metric name prevents mid-token false positives
# (e.g., "PaUC" matching as "AUC"). The decimal portion is REQUIRED —
# integer "AUC = 1" without decimals is suspect and almost always a
# typo or a placeholder; the LLM in B1.c will surface those if they
# appear. Note: `R\^?2` matches "R2" and "R^2"; we extend with the
# Unicode `R²` (U+00B2) so authors using superscript-2 notation
# match. Case-insensitive flag because some projects write "auc",
# "rmse", etc.
METRIC_RE = re.compile(
    r"\b(?:AUC|R\^?2|R²|RMSE|MAE)\s*[=:]\s*\d+\.\d+",
    re.IGNORECASE,
)


# Ordered tuple of (class_name, pattern). Order is canonical so the
# regex sweep visits classes in a deterministic order.
#
# class_name is the public label used in NumericMatch.match_class and
# for flag aggregation. Renaming a class is a contract change; downstream
# tests and any future LLM prompt referencing the class enumeration
# would need to bump together.
PATTERN_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percentage", PERCENTAGE_RE),
    ("ratio_with_unit", RATIO_WITH_UNIT_RE),
    ("p_value", P_VALUE_RE),
    ("confidence_interval", CI_RE),
    ("n_count", N_COUNT_RE),
    ("metric", METRIC_RE),
)


# Mapping from match_class to the flag column it sets. Multi-class
# sentences set multiple flags. Tool-emitted, not LLM-emitted.
_CLASS_TO_FLAG: dict[str, str] = {
    "p_value": "pvalue_present",
    "confidence_interval": "ci_present",
    "metric": "effect_size_present",
    # percentage / ratio_with_unit / n_count do NOT set any of the three
    # flags — they're numeric assertions but not statistical-rigor
    # signals in the M7 sense. claim_inventory.tsv still records them
    # as candidates with all three flags = "no"; the holistic prompt's
    # constructive constraint is: such claims need a hedge.
}


# ---------------------------------------------------------------------------
# Sentence segmentation
# ---------------------------------------------------------------------------
#
# REPORT.md is narrative markdown — paragraphs separated by blank lines,
# sentences ending with `.!?`. Naive `re.split(r"\.")` over-segments
# because of decimals (`1.5` becomes `1` and `5`) and abbreviations
# (`Fig. 3` becomes `Fig` and ` 3`). We use a single-pass character
# scanner that consults a small set of carve-out rules at each
# candidate terminator.

# Common abbreviations whose terminating period is NOT a sentence end.
# Compared case-insensitively. Trailing words like "Fig" / "Tbl" /
# "Tab" are common in figure references; "et al" / "i.e" / "e.g" /
# "vs" / "Ref" are common scholarly abbreviations.
_ABBREVIATIONS: frozenset[str] = frozenset({
    "dr", "mr", "mrs", "ms",
    "fig", "figs", "tbl", "tab", "tabs",
    "ref", "refs",
    "vs", "et al",
    "i.e", "e.g", "cf",
    "no", "vol",
    "approx", "ca",
    # Single-letter "p" so "p-value." abbreviation (rare but possible)
    # doesn't open a fake sentence — but only as a standalone token.
    # Disabled by default; leave commented for future use.
    # "p",
})


def _is_decimal_period(text: str, idx: int) -> bool:
    """True iff text[idx] == '.' AND it's flanked by digits on both sides
    (i.e., it's a decimal point inside a number like "1.5" or "0.05")."""
    if idx <= 0 or idx >= len(text) - 1:
        return False
    return text[idx - 1].isdigit() and text[idx + 1].isdigit()


def _preceding_word(text: str, idx: int) -> str:
    """Return the word ending at text[idx]. Empty string if no word.
    Word characters are alphanumeric + hyphen + period (so "et al"
    detection works on "et al" and "i.e" alike)."""
    if idx <= 0:
        return ""
    end = idx
    start = end
    while start > 0:
        ch = text[start - 1]
        if ch.isalnum() or ch in "-.":
            start -= 1
        else:
            break
    return text[start:end]


def _is_abbreviation_period(text: str, idx: int) -> bool:
    """True iff text[idx] == '.' AND the preceding word is in our
    abbreviation set. Match is case-insensitive. Abbreviations
    containing a period (e.g., "i.e", "e.g") are matched whole."""
    if idx >= len(text) or text[idx] != ".":
        return False
    word = _preceding_word(text, idx)
    if not word:
        return False
    # Strip any trailing dot from the word — `_preceding_word`'s greedy
    # take-includes-period behavior pulls in "i.e" vs "i.e." nicely.
    return word.lower() in _ABBREVIATIONS


def _looks_like_sentence_start(text: str, idx: int) -> bool:
    """True iff text[idx] starts a new sentence.

    Scientific REPORT.md prose routinely opens sentences with lowercase
    statistical glyphs ("p < 0.05 throughout", "n = 343 individuals
    were screened", "log2 fold change confirmed..."). A capital-letter-
    only rule misses these — we accept any non-whitespace printable
    character as a sentence-start signal.

    The carve-outs at the terminator (decimal, abbreviation) bear the
    weight of false-positive prevention. Period-after-decimal-digit
    is gated by `_is_decimal_period`; period-after-abbreviation is
    gated by `_is_abbreviation_period`. With those two carve-outs
    intact, accepting lowercase here is safe.
    """
    if idx >= len(text):
        return True  # EOF after a terminator → close the prior sentence
    ch = text[idx]
    if ch.isspace():
        return False
    # Any non-whitespace printable is a sentence-start signal — letters
    # (any case), digits, opening quote/paren/bracket/dash glyphs.
    return True


@dataclass
class Sentence:
    """One segmented sentence with offset metadata.

    `start` and `end` are character offsets into the ORIGINAL REPORT.md
    text; `text` is the sentence content with leading/trailing whitespace
    stripped. Match-to-sentence assignment uses `start` and `end` directly.
    """
    start: int  # inclusive offset into original text
    end: int    # exclusive offset into original text
    text: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def segment_sentences(text: str) -> list[Sentence]:
    """Split REPORT.md prose into sentences.

    Boundary rules (in order):
      1. Paragraph break (`\\n\\n+`) closes a sentence even without a
         terminator.
      2. Markdown heading line (line starting with `#`) closes a
         sentence even without a terminator.
      3. `.!?` followed by whitespace+sentence-start IS a sentence
         end, EXCEPT:
           - the `.` is a decimal point (digit on both sides), or
           - the `.` immediately follows a known abbreviation.
      4. EOF closes any open sentence.

    Empty/whitespace-only sentences are filtered out. Returned list is
    ordered by position in the original text.
    """
    sentences: list[Sentence] = []
    n = len(text)
    if n == 0:
        return sentences

    # Pre-find all line starts to detect markdown headings cheaply.
    # A heading line starts at a line beginning with `#`. We don't try
    # to merge sentences that span heading lines — headings are
    # sentence-fragment by convention.
    i = 0
    cur_start: Optional[int] = None  # inclusive; None when between sentences
    while i < n:
        ch = text[i]

        # Skip whitespace at sentence boundary.
        if cur_start is None:
            if ch.isspace():
                i += 1
                continue
            # Line-leading `#` opens a heading; consume the rest of the
            # line as a single "sentence" (heading) and continue.
            if ch == "#" and (i == 0 or text[i - 1] == "\n"):
                line_end = text.find("\n", i)
                if line_end == -1:
                    line_end = n
                heading_text = text[i:line_end].strip()
                if heading_text:
                    sentences.append(Sentence(start=i, end=line_end, text=heading_text))
                i = line_end
                continue
            cur_start = i

        # Inside a sentence — look for boundary.
        # 1. Paragraph break.
        if ch == "\n" and i + 1 < n and text[i + 1] == "\n":
            sent_text = text[cur_start:i].strip()
            if sent_text:
                sentences.append(Sentence(start=cur_start, end=i, text=sent_text))
            cur_start = None
            # Skip the run of whitespace including the blank line.
            while i < n and text[i].isspace():
                i += 1
            continue

        # 2. Heading line break (newline followed by `#` at line start).
        if ch == "\n" and i + 1 < n and text[i + 1] == "#":
            sent_text = text[cur_start:i].strip()
            if sent_text:
                sentences.append(Sentence(start=cur_start, end=i, text=sent_text))
            cur_start = None
            i += 1  # advance past the newline; heading branch above will catch
            continue

        # 3. Sentence terminator `.!?` with carve-outs.
        if ch in ".!?":
            # Carve-out: decimal point.
            if ch == "." and _is_decimal_period(text, i):
                i += 1
                continue
            # Carve-out: abbreviation.
            if ch == "." and _is_abbreviation_period(text, i):
                i += 1
                continue
            # Look ahead: whitespace then sentence-start? OR EOF?
            j = i + 1
            ws_count = 0
            while j < n and text[j].isspace() and text[j] != "\n":
                j += 1
                ws_count += 1
            # If we hit a newline, treat as candidate sentence-end iff
            # the next non-whitespace char looks like a sentence start
            # OR we hit EOF.
            if j >= n:
                # EOF after the terminator — close the sentence.
                sent_text = text[cur_start : i + 1].strip()
                if sent_text:
                    sentences.append(Sentence(start=cur_start, end=i + 1, text=sent_text))
                cur_start = None
                i = j
                continue
            # Skip a single newline as inter-sentence whitespace too.
            if text[j] == "\n":
                # Look past the newline for sentence start.
                k = j + 1
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and _looks_like_sentence_start(text, k) and ws_count >= 0:
                    sent_text = text[cur_start : i + 1].strip()
                    if sent_text:
                        sentences.append(
                            Sentence(start=cur_start, end=i + 1, text=sent_text)
                        )
                    cur_start = None
                    i = k
                    continue
                # Otherwise: it's a soft-wrap, not a sentence end.
                i += 1
                continue
            # Non-newline whitespace → check for sentence-start.
            if ws_count > 0 and _looks_like_sentence_start(text, j):
                sent_text = text[cur_start : i + 1].strip()
                if sent_text:
                    sentences.append(Sentence(start=cur_start, end=i + 1, text=sent_text))
                cur_start = None
                i = j
                continue
            # No whitespace OR next char isn't a sentence-start → keep going.
            i += 1
            continue

        i += 1

    # 4. EOF closes any open sentence.
    if cur_start is not None and cur_start < n:
        sent_text = text[cur_start:n].strip()
        if sent_text:
            sentences.append(Sentence(start=cur_start, end=n, text=sent_text))

    return sentences


# ---------------------------------------------------------------------------
# Match extraction
# ---------------------------------------------------------------------------

@dataclass
class NumericMatch:
    """One regex hit in REPORT.md. Public for tests."""
    start: int            # offset into original text
    end: int              # exclusive offset
    matched_text: str     # the substring that matched
    match_class: str      # one of PATTERN_CLASSES' class_name values

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def extract_numeric_matches(text: str) -> list[NumericMatch]:
    """Run all six pattern classes over `text` and return the union of
    matches in source order.

    De-duplication is two-stage:

      1. Identical spans: when two classes match the exact same
         (start, end) span, the FIRST class in PATTERN_CLASSES order
         wins. Deterministic by canonical class order.

      2. Subsumption: when one match's span is fully contained inside
         another's (e.g., percentage `95%` inside CI `95% CI [...]`),
         the SHORTER inner match is dropped — the longer match is
         the more semantically-loaded claim. Without this dedupe,
         `"95% CI: [0.71, 0.85]"` would surface as both a percentage
         AND a confidence_interval claim, double-counting the
         numeric and falsely marking the sentence multi-numeric.

    Identical-span overlaps that aren't strict containment (e.g., two
    distinct overlapping spans of equal length) are kept as-is — the
    real cases that matter (CI subsuming `%`, ratio_with_unit
    subsuming `n_count` if a unit-bearing number includes `n=`) are
    strict-containment.
    """
    seen: dict[tuple[int, int], NumericMatch] = {}
    for class_name, pattern in PATTERN_CLASSES:
        for m in pattern.finditer(text):
            key = (m.start(), m.end())
            if key in seen:
                continue
            seen[key] = NumericMatch(
                start=m.start(),
                end=m.end(),
                matched_text=m.group(0),
                match_class=class_name,
            )

    # Subsumption: drop matches strictly contained in a longer match.
    # Sort by length DESC so the longer match is checked first; a
    # shorter match is dropped iff some kept (longer) match's span
    # strictly contains it.
    sorted_by_len = sorted(
        seen.values(),
        key=lambda m: -(m.end - m.start),
    )
    kept: list[NumericMatch] = []
    for m in sorted_by_len:
        subsumed = False
        for k in kept:
            if (
                k.start <= m.start
                and m.end <= k.end
                and (k.start, k.end) != (m.start, m.end)
            ):
                subsumed = True
                break
        if not subsumed:
            kept.append(m)
    return sorted(kept, key=lambda x: x.start)


def assign_matches_to_sentences(
    matches: list[NumericMatch],
    sentences: list[Sentence],
) -> dict[int, list[NumericMatch]]:
    """Assign each match to the sentence whose [start, end) range
    contains it. Matches outside any segmented sentence (e.g., inside
    code fences or table rows the segmenter skipped) are assigned to
    the closest preceding sentence by start; if no preceding sentence
    exists, dropped.

    Returns: {sentence_index: [matches]}.
    """
    if not sentences or not matches:
        return {}
    # Build a sorted index of (sentence_start, sentence_index).
    starts = [(s.start, s.end, idx) for idx, s in enumerate(sentences)]
    out: dict[int, list[NumericMatch]] = {}
    for m in matches:
        # Linear search is fine — sentences are ~hundreds at most.
        assigned: Optional[int] = None
        for s_start, s_end, idx in starts:
            if s_start <= m.start < s_end:
                assigned = idx
                break
        if assigned is None:
            # Fallback: find closest preceding sentence.
            for s_start, s_end, idx in reversed(starts):
                if s_start <= m.start:
                    assigned = idx
                    break
        if assigned is None:
            continue  # match before any sentence — drop
        out.setdefault(assigned, []).append(m)
    return out


# ---------------------------------------------------------------------------
# Candidate construction (one row per match-bearing sentence)
# ---------------------------------------------------------------------------

@dataclass
class ClaimCandidate:
    """One candidate row in the TSV. The deterministic stage produces
    these directly; the LLM stage at B1.c may split a multi-numeric
    candidate into multiple rows with notebook+cell+figure assignments.

    `notes` semantics:
      - "" — single-numeric sentence; demarcation is unambiguous.
      - "unresolved" — multi-numeric sentence; B1.c LLM hasn't
        demarcated yet (or --no-llm was set).
    """
    claim_id: str             # "C001", "C002", ...
    claim_text: str           # the sentence text
    source_notebook: str      # "" until B1.c (LLM assigns from methods_provenance)
    source_cell: str          # "" until B1.c
    figure_or_table: str      # "" until B1.c (LLM cross-links figures/tables)
    effect_size_present: str  # "yes" | "no"
    ci_present: str           # "yes" | "no"
    pvalue_present: str       # "yes" | "no"
    notes: str                # "" | "unresolved" | (B1.c may add others)

    def to_row(self) -> dict[str, str]:
        """Render as a dict keyed by TSV_COLUMNS — the wire format the
        TSV writer consumes. Order in the dict is irrelevant (consumer
        parses by header name).
        """
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "source_notebook": self.source_notebook,
            "source_cell": self.source_cell,
            "figure_or_table": self.figure_or_table,
            "effect_size_present": self.effect_size_present,
            "ci_present": self.ci_present,
            "pvalue_present": self.pvalue_present,
            "notes": self.notes,
        }


def _flags_from_classes(class_names: list[str]) -> tuple[str, str, str]:
    """Aggregate (effect_size_present, ci_present, pvalue_present) flags
    from the regex classes hit within a sentence. Each is "yes" iff
    ANY match in the sentence belongs to the class that maps to the
    flag (per _CLASS_TO_FLAG)."""
    flag_set = {_CLASS_TO_FLAG[c] for c in class_names if c in _CLASS_TO_FLAG}
    return (
        "yes" if "effect_size_present" in flag_set else "no",
        "yes" if "ci_present" in flag_set else "no",
        "yes" if "pvalue_present" in flag_set else "no",
    )


def build_candidates(
    matches: list[NumericMatch],
    sentences: list[Sentence],
) -> list[ClaimCandidate]:
    """One ClaimCandidate per sentence with ≥1 match. Multi-numeric
    sentences yield ONE candidate marked notes="unresolved"; single-
    match sentences yield notes="".

    Output is in source order (claim_ids are assigned C001..CNNN in
    the order sentences appear in REPORT.md).
    """
    by_sent = assign_matches_to_sentences(matches, sentences)
    if not by_sent:
        return []

    cands: list[ClaimCandidate] = []
    next_id = 1

    # Iterate sentences in source order; emit a candidate for each
    # sentence index that has matches.
    for idx, sent in enumerate(sentences):
        sent_matches = by_sent.get(idx)
        if not sent_matches:
            continue
        class_names = [m.match_class for m in sent_matches]
        eff, ci, pv = _flags_from_classes(class_names)
        notes = "unresolved" if len(sent_matches) > 1 else ""
        cands.append(
            ClaimCandidate(
                claim_id=f"C{next_id:03d}",
                claim_text=sent.text,
                source_notebook="",
                source_cell="",
                figure_or_table="",
                effect_size_present=eff,
                ci_present=ci,
                pvalue_present=pv,
                notes=notes,
            )
        )
        next_id += 1
    return cands


# ---------------------------------------------------------------------------
# TSV emission (self-describing header per feedback_named_columns_in_inserts)
# ---------------------------------------------------------------------------

def format_claim_inventory_tsv(candidates: list[ClaimCandidate]) -> str:
    """Render candidates as a TSV string with a header row in
    TSV_COLUMNS order.

    csv.writer with `dialect=excel-tab` handles tab quoting and
    embedded newline escaping. Per feedback_render_test_must_evaluate_fstring,
    unit tests should call THIS function on synthetic candidates and
    parse the result, not grep the source.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, dialect="excel-tab", lineterminator="\n")
    writer.writerow(TSV_COLUMNS)
    for c in candidates:
        row = c.to_row()
        writer.writerow([row[col] for col in TSV_COLUMNS])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Lenient JSON load (pre-baked for B1.c reuse)
#
# Per feedback_llm_json_trailing_commas_repairable: trailing commas
# before `}` / `]` are unambiguous and worth a regex repair pass. Per
# feedback_llm_json_unfixable_in_parser: unescaped `"` inside strings
# is NOT repairable here — B1.c's prompt must include the anti-pattern
# rule.
# ---------------------------------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def lenient_json_load(text: str, *, source: str = "<json>") -> object:
    """Parse JSON; on JSONDecodeError, try one trailing-comma repair
    pass before re-raising the ORIGINAL error.

    Logs to stderr when the repair fires so future runs can track LLM
    JSON malformation frequency. Pattern matches
    discrepancy_register.lenient_json_load.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as orig:
        repaired = _TRAILING_COMMA_RE.sub(r"\1", text)
        if repaired == text:
            raise
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            raise orig from None
        sys.stderr.write(
            f"  note: stripped trailing comma(s) from {source} "
            f"(LLM JSON malformation; original error at line "
            f"{orig.lineno} col {orig.colno})\n"
        )
        return data


# ---------------------------------------------------------------------------
# LLM seam stub (B1.c lands in a follow-up conversation)
# ---------------------------------------------------------------------------

class LLMNotImplemented(NotImplementedError):
    """Stub raised by demarcate_unresolved_with_llm() in this milestone.

    The LLM seam at B1.c lands in a follow-up conversation. Until then,
    --no-llm is the only path that emits a complete TSV. Without
    --no-llm, if any candidate has notes="unresolved", main() catches
    LLMNotImplemented and maps to exit 3.

    Symmetric to discrepancy_register's A1.c → A1.cd progression: that
    module's A1.b shipped with the LLM stage stubbed out; A1.c bumped
    the stub to a real subprocess call and the validator landed
    alongside in A1.d.
    """


def demarcate_unresolved_with_llm(*args: object, **kwargs: object) -> None:
    """Stub. B1.c will replace this with the real Haiku-4.5 demarcation
    call (read methods_provenance.md, split multi-numeric sentences,
    assign source_notebook+cell+figure_or_table, validate against
    fabricated cell references).

    Raises:
      LLMNotImplemented — always. Pass --no-llm for the deterministic-
      only path.
    """
    raise LLMNotImplemented(
        "B1.c LLM demarcation pass is not implemented yet. "
        "Run with --no-llm for the deterministic-only path; "
        "multi-numeric sentences will be marked notes='unresolved' "
        "in the TSV."
    )


# ---------------------------------------------------------------------------
# Audit JSONL emission
# ---------------------------------------------------------------------------

def _sha256_of_path(p: Path) -> str:
    """Return hex SHA-256 of file contents. Reads in chunks for safety
    against accidentally-large inputs."""
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            buf = f.read(64 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def emit_audit_line(
    *,
    audit_path: Path,
    report_path: Path,
    methods_provenance_path: Path,
    figures_inventory_path: Path,
    tables_inventory_path: Path,
    output_path: Path,
    inventory_size: int,
    unresolved_count: int,
    cost_usd: float,
    exit_status: int,
    cache_hit: bool = False,
) -> None:
    """Append one JSONL audit line to <output-dir>/audit/phase0.jsonl.

    `cache_hit=True` records that the LLM call was skipped because the
    idempotency cache had a hit; lands when B1.d's cache does. For
    M1-B1.ab cache_hit is always False.

    `cost_usd=0.0` in deterministic-only mode (no LLM call). When B1.c
    lands, this becomes the actual classifier subprocess cost.
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    def _safe_sha(p: Path) -> Optional[str]:
        if p.is_file():
            return _sha256_of_path(p)
        return None

    line = {
        "timestamp": _utc_now_iso(),
        "tool": "claim_inventory",
        "version": VERSION,
        "inputs": {
            "report": _safe_sha(report_path),
            "methods_provenance": _safe_sha(methods_provenance_path),
            "figures_inventory": _safe_sha(figures_inventory_path),
            "tables_inventory": _safe_sha(tables_inventory_path),
        },
        "output_path": str(output_path),
        "inventory_size": inventory_size,
        "unresolved_count": unresolved_count,
        "cost_usd": cost_usd,
        "cache_hit": cache_hit,
        "exit_status": exit_status,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Top-level orchestration (used by main() and importable from tests)
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    candidates: list[ClaimCandidate]
    inventory_size: int
    unresolved_count: int
    cost_usd: float
    cache_hit: bool = False


def run_inventory(
    *,
    report_text: str,
    no_llm: bool,
) -> RunResult:
    """Pure-function orchestration: segment + extract + build candidates
    + (LLM if requested and unresolved candidates exist).

    Doesn't touch disk; doesn't emit audit. main() handles those so
    this function is straightforward to unit-test.

    With `no_llm=True`: returns deterministic candidates only.
    Multi-numeric sentences are marked notes="unresolved".

    With `no_llm=False`: if any candidate is "unresolved",
    demarcate_unresolved_with_llm() fires — and in this milestone
    raises LLMNotImplemented (caller maps to exit 3). When B1.c lands,
    it splits the unresolved candidates into multiple resolved rows.
    """
    sentences = segment_sentences(report_text)
    matches = extract_numeric_matches(report_text)
    candidates = build_candidates(matches, sentences)
    unresolved = sum(1 for c in candidates if c.notes == "unresolved")

    if not no_llm and unresolved > 0:
        # Future B1.c lands here; for now this raises LLMNotImplemented.
        demarcate_unresolved_with_llm()  # noqa: blocking call once implemented
        # ^ unreachable in M1-B1.ab; kept here so the integration point
        # is in the orchestrator, not in main().

    return RunResult(
        candidates=candidates,
        inventory_size=len(candidates),
        unresolved_count=unresolved,
        cost_usd=0.0,
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="claim_inventory.py",
        description=(
            "Numeric-claim inventory builder (paper-writer v0.8 Phase 0). "
            "Walks REPORT.md, surfaces every numeric assertion as "
            "claim_inventory.tsv with effect_size/CI/p-value flags, "
            "appends an audit JSONL line to <output-dir>/audit/phase0.jsonl. "
            "See SPEC_v0_8 §4.6 + DECISIONS.md D-034 Q2."
        ),
    )
    p.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Path to the project's REPORT.md.",
    )
    p.add_argument(
        "--methods-provenance",
        type=Path,
        required=True,
        help=(
            "Path to methods_provenance.md (emitted by extract_methods.py). "
            "Currently passed through; B1.c LLM uses it to assign "
            "source_notebook+cell."
        ),
    )
    p.add_argument(
        "--figures-inventory",
        type=Path,
        required=True,
        help=(
            "Path to figures_inventory.md (emitted by extract_figures.py). "
            "Currently passed through; B1.c LLM uses it to cross-link "
            "figure_or_table."
        ),
    )
    p.add_argument(
        "--tables-inventory",
        type=Path,
        required=True,
        help=(
            "Path to tables_inventory.md (emitted by extract_tables.py). "
            "Currently passed through; B1.c LLM uses it to cross-link "
            "figure_or_table."
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Directory under which to write claim_inventory.tsv and "
            "audit/phase0.jsonl. Created if missing."
        ),
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Skip the LLM demarcation pass (B1.c). Multi-numeric "
            "sentences are emitted as ONE candidate marked "
            "notes='unresolved'. Used by the C2.b ground-truth "
            "completeness check + ablation pattern."
        ),
    )
    args = p.parse_args(argv)

    report_path: Path = args.report
    methods_path: Path = args.methods_provenance
    figs_path: Path = args.figures_inventory
    tbls_path: Path = args.tables_inventory
    out_dir: Path = args.output_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / "audit" / "phase0.jsonl"
    output_path = out_dir / "claim_inventory.tsv"

    # Input parse error handling (exit 2). All four required files must
    # exist. Empty file is allowed (will yield zero candidates, exit 0);
    # a missing file is exit 2.
    inputs = (
        ("report", report_path),
        ("methods_provenance", methods_path),
        ("figures_inventory", figs_path),
        ("tables_inventory", tbls_path),
    )
    for label, p_in in inputs:
        if not p_in.is_file():
            print(f"error: {label} input not found: {p_in}", file=sys.stderr)
            emit_audit_line(
                audit_path=audit_path,
                report_path=report_path,
                methods_provenance_path=methods_path,
                figures_inventory_path=figs_path,
                tables_inventory_path=tbls_path,
                output_path=output_path,
                inventory_size=0,
                unresolved_count=0,
                cost_usd=0.0,
                exit_status=2,
            )
            return 2

    try:
        report_text = report_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read REPORT.md: {e}", file=sys.stderr)
        emit_audit_line(
            audit_path=audit_path,
            report_path=report_path,
            methods_provenance_path=methods_path,
            figures_inventory_path=figs_path,
            tables_inventory_path=tbls_path,
            output_path=output_path,
            inventory_size=0,
            unresolved_count=0,
            cost_usd=0.0,
            exit_status=2,
        )
        return 2

    try:
        result = run_inventory(report_text=report_text, no_llm=args.no_llm)
    except LLMNotImplemented as e:
        print(
            f"error: LLM demarcation pass (B1.c) not implemented yet: {e}",
            file=sys.stderr,
        )
        emit_audit_line(
            audit_path=audit_path,
            report_path=report_path,
            methods_provenance_path=methods_path,
            figures_inventory_path=figs_path,
            tables_inventory_path=tbls_path,
            output_path=output_path,
            inventory_size=0,
            unresolved_count=0,
            cost_usd=0.0,
            exit_status=3,
        )
        return 3

    # Write the TSV.
    tsv_text = format_claim_inventory_tsv(result.candidates)
    output_path.write_text(tsv_text, encoding="utf-8")

    emit_audit_line(
        audit_path=audit_path,
        report_path=report_path,
        methods_provenance_path=methods_path,
        figures_inventory_path=figs_path,
        tables_inventory_path=tbls_path,
        output_path=output_path,
        inventory_size=result.inventory_size,
        unresolved_count=result.unresolved_count,
        cost_usd=result.cost_usd,
        exit_status=0,
        cache_hit=result.cache_hit,
    )

    print(
        f"Wrote {output_path} ({result.inventory_size} claim(s)"
        + (
            f"; {result.unresolved_count} unresolved (multi-numeric)"
            if result.unresolved_count
            else ""
        )
        + ")",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
