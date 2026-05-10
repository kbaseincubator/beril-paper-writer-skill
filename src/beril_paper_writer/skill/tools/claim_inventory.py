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

  1. Deterministic regex extraction (B1.a + B1.b):
       - Six pattern classes (percentages, ratios with units, p-values,
         CIs, n-counts, metrics — AUC/R²/RMSE/MAE).
       - Sentence segmentation with carve-outs for decimals, common
         abbreviations (Mr./Dr./Fig./Tbl./et al./i.e./e.g.), and
         paragraph breaks.
       - One candidate row per sentence containing ≥1 match. Multi-
         numeric sentences collapse to ONE candidate marked
         notes="unresolved".
       - Effect-size / CI / p-value flags are aggregated PER SENTENCE
         from regex class membership. Tool-emitted, not LLM-emitted —
         per feedback_llm_arithmetic_unreliable, deterministic post-
         checks beat LLM self-counts.

  2. LLM demarcation pass (B1.c):
       - Haiku-4.5 over `unresolved` candidates only.
       - Splits multi-numeric sentences into distinct claim_ids;
         assigns source_notebook+cell from methods_provenance.md;
         cross-links to figures/tables when applicable.
       - Cost ceiling $0.10/run (SPEC §4.6); soft warning, not a hard
         halt — the orchestrator's circuit-breaker handles cumulative
         caps.
       - The LLM seam is monkeypatchable via the module-level
         `demarcator_llm_call` reference.
       - One input candidate may produce 1..N output rows; each output
         row's claim_text is a substring of the source sentence.
       - Per-row flags are RECOMPUTED from each demarcated claim_text
         via the same deterministic regex sweep — not inherited from
         the original sentence.

  3. Validator + idempotency cache (B1.d):
       - Validator: claim_text substring of input sentence; source_notebook
         substring of methods_provenance.md; source_cell shape `^\\d+$`;
         figure_or_table empty or substring of figures/tables inventory.
         Coverage: every input candidate has ≥1 row.
       - Cache: SHA-256 over six-tuple (report_sha, methods_sha,
         figures_sha, tables_sha, prompt_sha, parser_VERSION).
         Lives at <output-dir>/audit/claim_inventory_cache.json.
         Re-run is byte-stable on unchanged inputs.

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
  2 — input parse error (a required file is missing or unreadable).
  3 — LLM call failure (subprocess crash, JSON unparseable after lenient
      repair, transport error). With --no-llm set, the LLM seam is
      never reached and exit 3 is impossible.
  4 — validator rejection of LLM output: missing input coverage,
      non-substring claim_text, fabricated source_notebook, malformed
      source_cell, ungrounded figure_or_table. The LLM was called and
      billed; the audit JSONL line records the cost. Re-run after
      re-reading the prompt; if the validator keeps rejecting, the
      prompt needs a contract update (bump prompt version → cache
      invalidates).

Audit JSONL line schema (one line per invocation, appended to
<output-dir>/audit/phase0.jsonl):

  {
    "timestamp": "2026-05-07T14:23:01Z",
    "tool": "claim_inventory",
    "version": "0.8.0-m1-B1.abcd",
    "inputs": {
      "report": "<sha256>",
      "methods_provenance": "<sha256>",
      "figures_inventory": "<sha256>",
      "tables_inventory": "<sha256>"
    },
    "output_path": "<absolute path to claim_inventory.tsv>",
    "inventory_size": 12,
    "unresolved_count": 0,
    "cost_usd": 0.0234,
    "cache_hit": false,
    "exit_status": 0
  }

Idempotency cache (B1.d):

  <output-dir>/audit/claim_inventory_cache.json — JSON map of
  cache_key (hex SHA-256 over the six-tuple report_sha +
  methods_provenance_sha + figures_inventory_sha + tables_inventory_sha
  + prompt_sha + parser_VERSION) → cached payload. On hit, the LLM is
  skipped, demarcations are re-validated against the current inputs,
  and the same expanded TSV is re-emitted byte-identical. The audit
  JSONL still appends with `cache_hit: true` so reruns stay observable
  (per SPEC §4.7). Any input SHA change OR prompt-file change OR
  parser_VERSION bump invalidates the cache.

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
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


# Module version. Distinct from package version because audit consumers
# may want to track precisely which sub-milestone wrote a given line.
# Bump on contract-affecting changes (new audit fields, schema changes).
# B1.a + B1.b shipped as "0.8.0-m1-B1.ab"; B1.c + B1.d (this commit)
# bumps to "B1.abcd". M1 close will land as "0.8.0-m1" once Tier C/D/E
# wrap.
VERSION = "0.8.0-m1-B1.h"


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
#
# B1.e (D-036) — allow optional whitespace between digit and `%`.
# REPORT.md authors routinely write `95 %` (space form); the original
# regex only matched `95%` and missed 9 of 12 percentage patterns in
# the C2.b ground-truth check on ibd_phage_targeting. The space is
# constrained to `\s{0,2}` (no newline-spanning) to keep the match
# tightly scoped to the intended digit-unit pair.
PERCENTAGE_RE = re.compile(r"\b\d+(?:\.\d+)?\s{0,2}%")


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
#
# B1.e (D-036) — two extensions to close C2.b recall gaps:
#   1. Add `≤` (Unicode ≤ = U+2264) to the operator class. BERIL Methods
#      sections use it interchangeably with `<=`.
#   2. Make the dot+fractional optional in the scientific-notation
#      branch. REPORT.md uses both `p=2.4e-6` (with dot) and `p=7e-17`
#      (no dot in mantissa); the original branch missed the dot-less
#      form on `ibd_phage_targeting`.
P_VALUE_RE = re.compile(
    r"\b[pP]\s*(?:<|<=|=|>=|>|≤|≥)\s*0\.\d+"
    r"|"
    r"\b[pP]\s*[<=]\s*\d+(?:\.\d+)?[eE]-?\d+",
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


# B1.e (D-036) — five new classes added 2026-05-07. Each closes a
# documented gap from the C2.b ground-truth check on
# `ibd_phage_targeting/REPORT.md`. All five contribute to
# `effect_size_present` because they are non-rigor-flagged effect
# sizes / counts; the holistic prompt is told that any such claim
# without a CI / p-value sibling needs a hedge.

# Class 7 — Correlations: Pearson r and Spearman ρ. Forms covered:
#   "r = 0.96", "ρ=1.000", "r=+0.456", "r=−0.747"
# Sign optional (ASCII +, - and Unicode minus −=U+2212). Decimal
# REQUIRED — integer "r=1" is sus and almost always a typo or schema
# leak.
CORRELATION_RE = re.compile(
    r"\b[rρ]\s*[=:]\s*[+−-]?\d+\.\d+",
)

# Class 8 — Odds ratios. Form: "OR=1.38", "OR = 234". Decimal
# REQUIRED for the same reason. Word-boundary on OR prevents
# mid-token false positives (e.g., "factOR=2" hitting if regex were
# anchored loosely).
ODDS_RATIO_RE = re.compile(
    r"\bOR\s*[=:]\s*\d+(?:\.\d+)?",
)

# Class 9 — log fold change. Forms covered:
#   "log₂FC +2.67"  (subscript ₂ = U+2082)
#   "log2FC +5.66"  (ASCII)
#   "log_2 FC -1.4" (ASCII with underscore)
# Sign optional. Decimal REQUIRED.
LOG_FC_RE = re.compile(
    r"\blog[₂2]?(?:_2)?\s*FC\s*[+−-]?\d+\.\d+",
)

# Class 10 — Counts in "M of N" / "M / N" forms (UC Davis cohort
# coverage, candidate-list sizes). Permits commas in M and N.
# Examples: "14 of 23", "3,929 / 17,672", "45 / 51". Word-boundary
# on the leading digit; trailing context not constrained beyond the
# `of`/`/` separator. The downstream LLM in B1.c can disambiguate
# false-positive matches like "Section 14 of 23 pages" if needed
# (very rare in scientific REPORT.md).
COUNT_OF_RE = re.compile(
    r"\b\d+(?:,\d{3})*\s*(?:/|of)\s*\d+(?:,\d{3})*",
    re.IGNORECASE,
)

# Class 11 — Cliff's δ effect-delta. Forms:
#   "cliff δ = +0.50", "cliff δ=−0.358", "cliff=-0.747"
# `δ` is U+03B4. Match either with the literal δ glyph or without.
# Sign optional; decimal REQUIRED.
CLIFF_DELTA_RE = re.compile(
    r"\bcliff(?:\s*δ)?\s*[=:]?\s*[+−-]?\d+\.\d+",
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
    # B1.e (D-036) additions:
    ("correlation", CORRELATION_RE),
    ("odds_ratio", ODDS_RATIO_RE),
    ("log_fc", LOG_FC_RE),
    ("count_of", COUNT_OF_RE),
    ("cliff_delta", CLIFF_DELTA_RE),
)


# Mapping from match_class to the flag column it sets. Multi-class
# sentences set multiple flags. Tool-emitted, not LLM-emitted.
_CLASS_TO_FLAG: dict[str, str] = {
    "p_value": "pvalue_present",
    "confidence_interval": "ci_present",
    "metric": "effect_size_present",
    # B1.e additions: correlation, odds_ratio, log_fc, cliff_delta are
    # all effect-size signals (different shapes than METRIC_RE's
    # AUC/R²/RMSE/MAE keyword set, but semantically the same — they
    # report a measured effect). count_of stays unflagged like
    # n_count (it's a count, not an effect size).
    "correlation": "effect_size_present",
    "odds_ratio": "effect_size_present",
    "log_fc": "effect_size_present",
    "cliff_delta": "effect_size_present",
    # percentage / ratio_with_unit / n_count / count_of do NOT set any
    # of the three flags — they're numeric assertions but not
    # statistical-rigor signals in the M7 sense. claim_inventory.tsv
    # still records them as candidates with all three flags = "no";
    # the holistic prompt's constructive constraint is: such claims
    # need a hedge.
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
# LLM demarcation seam (B1.c) + validator (B1.d)
# ---------------------------------------------------------------------------
#
# Symmetric to discrepancy_register's A1.c/A1.d split. B1.c is the LLM
# call surface (subprocess wrapper + prompt assembly + response parse).
# B1.d is the post-LLM validator + idempotency cache.

# Resolve the prompt path relative to this module so the runtime SHA is
# stable across invocations. The prompt SHA is one of the six cache-key
# components — bumping the prompt invalidates cache entries automatically.
_MODULE_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _MODULE_DIR.parent
_PROMPT_PATH = _SKILL_DIR / "prompts" / "claim_demarcate.v1.md"


# Cost ceiling per SPEC §4.6. Soft warning, not a hard halt — the audit
# line records the actual spend; the orchestrator decides escalation.
# Hard halts belong in the bash-level cost circuit-breaker
# (paper_writer.sh's MAX_COST_USD), not in this individual tool.
_DEMARCATOR_COST_CEILING_USD = 0.10


# LLM model alias (Haiku 4.5 per SPEC §4.6 cost target). Public for
# CLI override and test monkeypatch.
DEFAULT_DEMARCATOR_MODEL = "claude-haiku-4-5-20251001"


# B1.f (D-038, 2026-05-07) — batch unresolved candidates so a single
# claude -p call doesn't exceed the model's effective output-token
# budget OR the subprocess wrapper's 180s timeout. Live-LLM smoke on
# `ibd_phage_targeting` after B1.e produced 133 unresolved candidates
# in one call → LLM dropped 42 indices (truncation) on the first
# attempt and timed out on subsequent attempts. Batching to ~15
# candidates per call yields predictable per-call latency (~30s) and
# output token counts well under the model's per-response cap. Total
# cost on the project: 9 batches × ~$0.10 each ≈ ~$0.90 (15× the
# legacy $0.10 single-call ceiling, but per D-037 ceiling is tracked
# not enforced; observed data informs M2 caps).
DEFAULT_DEMARCATOR_BATCH_SIZE = 15


# B1.g (D-039, 2026-05-07) — bounded-retry envelope on missing input
# indices. The Haiku 4.5 demarcator non-deterministically drops 1–3
# input candidates per dense-project run; retrying ONLY the missing
# candidates as a fresh batch typically recovers them within 1–2
# rounds. After exhausting retries, residual misses fall back to the
# original `notes='unresolved'` candidate via expand_with_demarcations'
# defensive empty-rows path. Three retries was chosen because (a) two
# is too tight if the first retry partially succeeds and the second
# needs a third pass, (b) more than three doubles cost without
# meaningful coverage gain in observation. Per Adam D-037 cost
# reframing, retry cost is recorded in audit JSONL but not gated.
MAX_DEMARCATOR_RETRIES = 3


# Soft cap on context texts inlined into the user prompt. Prevents a
# malformed-fixture run from passing megabytes of provenance to the
# LLM. ~12K chars is roughly 3K tokens — fits inside the SPEC §4.6
# 5K-token user-prompt budget alongside the candidate sentences.
# When the cap fires, the head + tail are kept and a marker line in the
# middle records the truncation; the LLM still sees enough surface to
# ground notebook + figure cites.
_CONTEXT_CHAR_CAP = 12000


class LLMCallError(RuntimeError):
    """Subprocess crashed, JSON unparseable, response body wrong shape.

    Caller maps to exit code 3. The validator distinguishes call-shape
    failure (this exception → retry the LLM) from schema-content
    failure (ValidationError → re-read the prompt or candidate set).
    """


class ValidationError(RuntimeError):
    """The LLM produced syntactically-valid JSON but the content
    violated the schema (out-of-bounds candidate_index, missing input
    coverage, non-substring claim_text, fabricated notebook cite,
    malformed source_cell, ungrounded figure_or_table). Caller maps to
    exit code 4.

    Carries a `.diagnostics` dict so the audit line and stderr message
    can name the specific candidate and field that failed.
    """
    def __init__(self, message: str, diagnostics: Optional[dict] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass
class DemarcatorEntry:
    """One LLM-emitted demarcated claim — pre-validation.

    Public so tests can construct fakes directly. `figure_or_table` may
    be empty string; everything else must be non-empty per the prompt
    contract (validator enforces).
    """
    input_candidate_index: int
    claim_text: str
    source_notebook: str
    source_cell: str
    figure_or_table: str
    severity_justification: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# Type alias for the LLM-call seam — system prompt, user prompt, model →
# (response_text, cost_usd). Tests replace this with a canned-response
# fake; the real implementation calls `claude -p --output-format json`.
DemarcatorLLMCall = Callable[[str, str, str], tuple[str, float]]


def _invoke_demarcator_llm_subprocess(
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> tuple[str, float]:
    """Default LLM seam: subprocess to `claude -p`. Returns
    (response_text, cost_usd).

    Mirrors discrepancy_register._invoke_classifier_llm_subprocess
    line-for-line so any envelope-shape change can be applied
    consistently across both Phase-0 tools.

    Uses `--output-format json` so we can capture cost from the
    envelope's `total_cost_usd` field rather than parse a stream-json
    event log. The CLI is invoked with `CLAUDECODE` removed from env
    to detach from any inherited Claude Code session env (matches the
    convention in paper_writer.sh and adversarial_review.sh).

    No `--allowedTools` grant — this prompt produces inline JSON, not a
    file write; the LLM has no need for filesystem tools and granting
    them invites a stochastic Write-tool detour.

    Raises LLMCallError on subprocess failure or unparseable envelope.
    """
    if shutil.which("claude") is None:
        raise LLMCallError(
            "'claude' CLI not found on PATH; cannot invoke LLM "
            "demarcator. Pass --no-llm for the deterministic-only path."
        )

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
        user_prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=180.0,
        )
    except subprocess.TimeoutExpired as e:
        raise LLMCallError(
            f"claude -p timed out after {e.timeout}s without responding"
        ) from e
    except OSError as e:
        raise LLMCallError(f"claude -p failed to launch: {e}") from e

    if proc.returncode != 0:
        raise LLMCallError(
            f"claude -p exited {proc.returncode}; stderr:\n{proc.stderr.strip()}"
        )

    raw = proc.stdout.strip()
    if not raw:
        raise LLMCallError("claude -p returned empty stdout")

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMCallError(
            f"claude -p envelope was not parseable JSON: {e.msg} "
            f"(line {e.lineno} col {e.colno})"
        ) from e

    if not isinstance(envelope, dict):
        raise LLMCallError(
            f"claude -p envelope was not a JSON object: type={type(envelope).__name__}"
        )

    if envelope.get("is_error"):
        raise LLMCallError(
            f"claude -p reported is_error=true: {envelope.get('result', '<no result>')}"
        )

    response_text = envelope.get("result")
    if not isinstance(response_text, str):
        raise LLMCallError(
            f"envelope missing string 'result' field; got: {type(response_text).__name__}"
        )

    cost_raw = envelope.get("total_cost_usd")
    if isinstance(cost_raw, (int, float)):
        cost_usd = float(cost_raw)
    else:
        sys.stderr.write(
            "  note: claude -p envelope did not include total_cost_usd; "
            "audit will record cost_usd=0.0\n"
        )
        cost_usd = 0.0

    return response_text, cost_usd


# Module-level seam reference. Tests monkeypatch this to a fake.
demarcator_llm_call: DemarcatorLLMCall = _invoke_demarcator_llm_subprocess


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _truncate_context(text: str, *, label: str) -> str:
    """Truncate a context block to <= _CONTEXT_CHAR_CAP, preserving
    head + tail with a marker. Prevents a malformed-fixture run from
    sending megabytes to the LLM.
    """
    if len(text) <= _CONTEXT_CHAR_CAP:
        return text
    head_n = _CONTEXT_CHAR_CAP // 2 - 50
    tail_n = _CONTEXT_CHAR_CAP - head_n - 80
    head = text[:head_n]
    tail = text[-tail_n:]
    return (
        f"{head}\n"
        f"\n... [{label} truncated; "
        f"{len(text) - head_n - tail_n} chars elided] ...\n\n"
        f"{tail}"
    )


def _extract_notebook_paths(
    methods_provenance_text: str,
    project_root: Optional[Path] = None,
) -> list[str]:
    """Extract every notebook path the LLM should be allowed to cite.
    Returns sorted unique list. B1.h uses these as an explicit
    allowlist in the user prompt.

    Two sources are merged:
      1. `notebooks/<basename>.ipynb` paths mentioned in
         methods_provenance.md (canonical: AST-extracted notebooks
         with detected stat-test invocations).
      2. When `project_root` is provided, every `.ipynb` file under
         `project_root/notebooks/` (covers notebooks that exist on
         disk but aren't in methods_provenance.md — the same
         coverage gap that motivated B1.e's project_root validator
         fallback). On `ibd_phage_targeting`, methods_provenance
         lists 13 of 32 disk notebooks; the union is the right
         allowlist for the LLM.
    """
    paths = re.findall(
        r"notebooks/[A-Za-z0-9._\-]+\.ipynb",
        methods_provenance_text,
    )
    paths_set: set[str] = set(paths)
    if project_root is not None:
        notebooks_dir = project_root / "notebooks"
        if notebooks_dir.is_dir():
            for nb in notebooks_dir.glob("*.ipynb"):
                paths_set.add(f"notebooks/{nb.name}")
    return sorted(paths_set)


def _extract_figure_or_table_labels(
    figures_inventory_text: str,
    tables_inventory_text: str,
) -> list[str]:
    """Extract every figure/table label the LLM should be allowed to
    cite from figures_inventory.md / tables_inventory.md headings.
    Used as an explicit allowlist in the demarcator user prompt.

    Three label formats supported in observation:
      1. `## Fig N`/`## Tbl N`/`## Table N` — short canonical form
         used by some BERIL projects (matches the v0.4 era).
      2. `### figures/<basename>.png` — path-form used by the
         extract_figures.py emitter (e.g., on `ibd_phage_targeting`).
      3. `### report_tbl_NN — Description` — id-form used by some
         tables inventories.

    We extract level-2 AND level-3 headings and emit candidate
    labels in the form the inventory uses (the validator does
    substring matching against the same blob, so the allowlist
    must mirror inventory format byte-for-byte).
    """
    labels: list[str] = []
    seen: dict[str, None] = {}

    # Pattern 1: canonical short labels.
    short_pattern = re.compile(
        r"^#{1,4}\s+("
        r"Fig(?:ure)?\s+[A-Za-z0-9._\-]+"
        r"|"
        r"T(?:bl|able)\s+[A-Za-z0-9._\-]+"
        r")",
        re.MULTILINE,
    )
    # Pattern 2: figures/PATH.png as a heading (path form).
    path_pattern = re.compile(
        r"^#{1,4}\s+`?(figures/[A-Za-z0-9._\-/]+\.(?:png|svg|jpg|jpeg|pdf))`?",
        re.MULTILINE | re.IGNORECASE,
    )
    # Pattern 3: report_tbl_NN id-form (also tbl_NN, table_NN).
    id_pattern = re.compile(
        r"^#{1,4}\s+`?(report_tbl_[A-Za-z0-9_]+|tbl_[A-Za-z0-9_]+)`?",
        re.MULTILINE,
    )

    for blob in (figures_inventory_text, tables_inventory_text):
        for pat in (short_pattern, path_pattern, id_pattern):
            for m in pat.finditer(blob):
                label = m.group(1).strip()
                if label not in seen:
                    seen[label] = None
                    labels.append(label)
    return labels


def build_demarcator_user_prompt(
    unresolved_candidates: list["ClaimCandidate"],
    *,
    methods_provenance_text: str,
    figures_inventory_text: str,
    tables_inventory_text: str,
    project_root: Optional[Path] = None,
) -> str:
    """Render the user-prompt half of the LLM call: a list of N
    multi-numeric sentences + the methods/figures/tables context.

    Public so tests can pin the wire format.

    B1.h (D-040, 2026-05-07): adds explicit "VALID source_notebook
    values" + "VALID figure_or_table values" allowlists derived from
    the input contexts and emitted at the TOP of the user prompt
    (before the inputs). Drives the LLM to copy from a menu rather
    than paraphrase a notebook by its scientific subject (the
    pre-B1.h failure mode that surfaced
    `notebooks/NB07a_H3a_falsifiability.ipynb` when the real filename
    was `notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb`, and
    `Fig NB15` when no figure with that label exists).
    """
    n = len(unresolved_candidates)
    nb_allowlist = _extract_notebook_paths(
        methods_provenance_text, project_root=project_root,
    )
    fig_tbl_allowlist = _extract_figure_or_table_labels(
        figures_inventory_text, tables_inventory_text,
    )

    lines: list[str] = [
        f"You will demarcate N={n} multi-numeric sentence(s) from REPORT.md.",
        "",
        "For each input sentence, emit one output row per distinct numeric",
        "assertion it contains. Multi-row outputs share the same",
        "input_candidate_index. Quote the claim_text verbatim from the source",
        "sentence; cite the source_notebook + source_cell from",
        "methods_provenance.md; cross-link to a figure or table from",
        "figures_inventory.md or tables_inventory.md if applicable.",
        "",
        "Return a JSON array, in input_candidate_index ascending order,",
        "conforming to the schema in your system prompt. The first character",
        "of your response must be `[` and the last `]`. No prose, no fences.",
        "",
        "============================================================",
        "VALID source_notebook values — copy verbatim, do not paraphrase:",
        "============================================================",
    ]
    if nb_allowlist:
        for nb in nb_allowlist:
            lines.append(f"  - {nb}")
    else:
        lines.append(
            "  (no notebooks listed in methods_provenance.md; if you "
            "cannot identify the source notebook from context, leave "
            "source_notebook='' and the validator will reject the row "
            "— this is correct behavior, do not invent.)"
        )
    lines.extend([
        "",
        "Picking source_notebook: COPY-PASTE one of the strings above.",
        "Do NOT 'summarize' or 'describe' a notebook by its scientific",
        "topic from RESEARCH_PLAN.md. Do NOT shorten, expand, or",
        "rephrase the filename. The validator does a literal substring",
        "or path-on-disk check — anything else fails exit 4.",
        "",
        "============================================================",
        "VALID figure_or_table values — copy verbatim, OR leave EMPTY:",
        "============================================================",
    ])
    if fig_tbl_allowlist:
        for label in fig_tbl_allowlist:
            lines.append(f"  - {label}")
    else:
        lines.append(
            "  (no figure or table labels detected; set "
            "figure_or_table='' for every row.)"
        )
    lines.extend([
        "",
        'Picking figure_or_table: COPY-PASTE one of the strings above OR',
        'set figure_or_table="". Do NOT invent labels like "Fig NB15" or',
        '"Tbl 7"; the NB-prefixed values are NOTEBOOK identifiers, not',
        "figure or table identifiers. If you cannot find a fitting cite",
        'in the allowlist, set figure_or_table="" — empty is correct,',
        "fabrication is not.",
        "",
        "INPUTS:",
        "",
    ])
    for i, c in enumerate(unresolved_candidates):
        # Single-line repr keeps the prompt compact + makes the
        # substring rule's expected target unambiguous.
        lines.append(f"[{i}] sentence_text: {c.claim_text!r}")
        lines.append("")

    lines.append("CONTEXT — methods_provenance.md (excerpt):")
    lines.append("")
    lines.append(_truncate_context(
        methods_provenance_text, label="methods_provenance.md",
    ))
    lines.append("")
    lines.append("CONTEXT — figures_inventory.md (excerpt):")
    lines.append("")
    lines.append(_truncate_context(
        figures_inventory_text, label="figures_inventory.md",
    ))
    lines.append("")
    lines.append("CONTEXT — tables_inventory.md (excerpt):")
    lines.append("")
    lines.append(_truncate_context(
        tables_inventory_text, label="tables_inventory.md",
    ))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parse
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Strip a leading ```json (or ```) fence and trailing ``` if the LLM
    ignored the no-fence rule. Defensive — the prompt forbids fences but
    cheap robustness costs nothing."""
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_demarcator_response(
    response_text: str,
) -> list[DemarcatorEntry]:
    """Parse the LLM's response into DemarcatorEntry list.

    Distinct from discrepancy_register.parse_classifier_response in
    that the array length is NOT bounded by input length — one input
    candidate may yield 1..N output entries. We do not check coverage
    here (that's the validator's job, since it needs to know the input
    list).

    Raises LLMCallError on JSON-level failures (unparseable after
    lenient repair, wrong top-level type, missing required field on an
    entry). Schema-level failures (out-of-bounds index, non-substring
    claim_text) come later in validate_demarcations and raise
    ValidationError instead.
    """
    cleaned = _strip_code_fences(response_text)
    try:
        data = lenient_json_load(cleaned, source="<demarcator-response>")
    except json.JSONDecodeError as e:
        raise LLMCallError(
            f"demarcator response was not valid JSON after lenient repair: "
            f"{e.msg} (line {e.lineno} col {e.colno})"
        ) from e

    if not isinstance(data, list):
        raise LLMCallError(
            f"demarcator response top-level type was {type(data).__name__}, "
            f"expected list"
        )

    entries: list[DemarcatorEntry] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise LLMCallError(
                f"demarcator entry {i} was {type(item).__name__}, "
                f"expected object"
            )
        try:
            entries.append(DemarcatorEntry(
                input_candidate_index=int(item["input_candidate_index"]),
                claim_text=str(item["claim_text"]),
                source_notebook=str(item["source_notebook"]),
                # source_cell is "string" per the schema (digit-only
                # rendered as a string), but tolerate an integer in the
                # JSON since the prompt's example shows a stringified
                # int and Haiku occasionally emits a bare integer.
                source_cell=str(item["source_cell"]),
                # figure_or_table is required-but-may-be-empty; default
                # to empty string if the LLM omits it (validator doesn't
                # care about empty).
                figure_or_table=str(item.get("figure_or_table", "")),
                severity_justification=str(item.get("severity_justification", "")),
            ))
        except KeyError as e:
            raise LLMCallError(
                f"demarcator entry {i} missing required field: {e.args[0]}"
            ) from e
        except (TypeError, ValueError) as e:
            raise LLMCallError(
                f"demarcator entry {i} had a field of the wrong type: {e}"
            ) from e

    return entries


# ---------------------------------------------------------------------------
# Validator (B1.d)
# ---------------------------------------------------------------------------

# source_cell shape: non-empty digit-only string. The validator does
# NOT check that the cell exists in the notebook — that's a Tier C
# smoke concern. Shape-only here.
_SOURCE_CELL_SHAPE_RE = re.compile(r"^\d+$")


def validate_demarcations(
    demarcations: list[DemarcatorEntry],
    unresolved_candidates: list["ClaimCandidate"],
    *,
    methods_provenance_text: str,
    figures_inventory_text: str,
    tables_inventory_text: str,
    project_root: Optional[Path] = None,
    allow_missing: Optional[set[int]] = None,
) -> None:
    """Reject schema-violating entries with structured ValidationError.

    Checks (each → exit 4 with diagnostics):
      - Every demarcation row's input_candidate_index in [0, N).
      - Rows are sorted by input_candidate_index ascending (within an
        index, source-order is by emission order — not separately
        validated; we trust the LLM here because we cannot detect
        source-order violations without re-running the regex pass on
        each claim_text and matching positions, which is overengineering
        for the recall payoff).
      - Coverage: every input_candidate_index in [0, N) has ≥1 row.
        No gaps allowed.
      - Per-row: claim_text non-empty AND substring of the input
        sentence_text.
      - Per-row: source_notebook non-empty AND grounded — accepted if
        EITHER (a) substring of methods_provenance.md (legacy contract
        for synthetic fixtures + tight catalogs) OR (b) when
        project_root is provided, the path resolves to an existing
        file under project_root. The disk-fallback was added in B1.e
        (2026-05-07) after live-LLM smoke on `ibd_phage_targeting`
        revealed the substring-only check rejected real notebooks
        whose AST didn't surface a stat-test invocation
        (extract_methods.py only catalogs ~40% of notebooks on dense
        projects; the rest produce numerics via pandas/SQL/custom
        code and end up legitimately cited by the LLM but not in
        methods_provenance.md). See SPEC §4.6 + DECISIONS D-036.
      - Per-row: source_cell matches `^\\d+$`.
      - Per-row: figure_or_table is empty OR substring of
        figures_inventory.md OR tables_inventory.md.

    Pass-through is a no-op return; the caller continues with the
    validated entries.

    `project_root` is optional. Tests using synthetic fixtures pass
    None (and rely on methods_provenance_text containing the cite as
    a substring). Production runs pass the real project root and the
    disk fallback unblocks notebooks not in the AST extractor's view.

    `allow_missing` (B1.g, D-039, 2026-05-07) is the set of input
    candidate indices that may be absent from the demarcation set
    without triggering a coverage failure. Used by the bounded-retry
    path in `demarcate_unresolved_with_llm` after the LLM
    non-deterministically drops a few indices that even retries
    couldn't recover. The orchestrator's `expand_with_demarcations`
    falls back to the original unresolved row for those indices,
    preserving the M2 holistic prompt's grounding contract (every
    candidate has at least one row; rare residuals carry the
    multi-numeric original sentence with notes='unresolved').
    """
    n = len(unresolved_candidates)

    if n == 0:
        # No unresolved candidates means we shouldn't have called the
        # LLM at all. If demarcations is non-empty here, that's an
        # upstream invariant violation — but we do not need to
        # validate empty against empty.
        if demarcations:
            raise ValidationError(
                f"validator: {len(demarcations)} demarcation(s) emitted but "
                "no unresolved candidates to bind them to",
                {"demarcations": len(demarcations), "unresolved": 0},
            )
        return

    # Order check (ascending by input_candidate_index). Within the same
    # index, emission order is preserved as-is.
    last_idx = -1
    for pos, e in enumerate(demarcations):
        if e.input_candidate_index < last_idx:
            raise ValidationError(
                f"validator: demarcation rows are not sorted by "
                f"input_candidate_index ascending; row {pos} has "
                f"index {e.input_candidate_index} after row "
                f"{pos - 1} index {last_idx}",
                {"position": pos, "index": e.input_candidate_index, "prev": last_idx},
            )
        last_idx = e.input_candidate_index

    # Coverage check: every index in [0, N) must appear at least once,
    # except for indices in `allow_missing` (B1.g) which the caller
    # explicitly tolerates after retries exhausted.
    seen_indices = {e.input_candidate_index for e in demarcations}
    tolerated = allow_missing or set()
    expected = set(range(n)) - tolerated
    if not expected.issubset(seen_indices):
        missing = sorted(expected - seen_indices)
        raise ValidationError(
            f"validator: demarcations do not cover all input "
            f"candidates; missing indices: {missing}",
            {
                "missing": missing,
                "total": n,
                "tolerated_missing": sorted(tolerated),
            },
        )
    extra = sorted(seen_indices - expected)
    if extra:
        raise ValidationError(
            f"validator: demarcations contain out-of-bounds "
            f"input_candidate_index value(s): {extra} (valid range: "
            f"[0, {n}))",
            {"out_of_bounds": extra, "bound": n},
        )

    # Per-row checks.
    for pos, e in enumerate(demarcations):
        cand = unresolved_candidates[e.input_candidate_index]

        # claim_text non-empty + substring of input sentence.
        if not e.claim_text:
            raise ValidationError(
                f"validator: row {pos} has empty claim_text",
                {"position": pos, "input_candidate_index": e.input_candidate_index},
            )
        if e.claim_text not in cand.claim_text:
            raise ValidationError(
                f"validator: row {pos}'s claim_text is not a substring "
                f"of the input sentence_text. Got: {e.claim_text!r}; "
                f"expected substring of: {cand.claim_text!r}",
                {
                    "position": pos,
                    "field": "claim_text",
                    "value": e.claim_text,
                    "input": cand.claim_text,
                    "input_candidate_index": e.input_candidate_index,
                },
            )

        # source_notebook non-empty + grounded (substring of
        # methods_provenance.md OR existing file under project_root).
        # See B1.e change rationale in validate_demarcations docstring.
        if not e.source_notebook:
            raise ValidationError(
                f"validator: row {pos} has empty source_notebook",
                {"position": pos, "input_candidate_index": e.input_candidate_index},
            )
        substring_ok = e.source_notebook in methods_provenance_text
        disk_ok = False
        if project_root is not None:
            # Tolerate a leading "./" emitted by some LLM responses; the
            # smoke harness's _resolve_notebook_cell normalizes the same
            # way, so we mirror it here for consistency.
            relative = e.source_notebook.lstrip("./")
            disk_ok = (project_root / e.source_notebook).is_file() or (
                project_root / relative
            ).is_file()
        if not (substring_ok or disk_ok):
            grounding_hint = (
                "neither a substring of methods_provenance.md nor an "
                "existing file under project_root"
                if project_root is not None
                else "not a substring of methods_provenance.md and no "
                     "project_root provided to fall back to disk check"
            )
            raise ValidationError(
                f"validator: row {pos}'s source_notebook is "
                f"{grounding_hint} (LLM may have fabricated the path). "
                f"Got: {e.source_notebook!r}",
                {
                    "position": pos,
                    "field": "source_notebook",
                    "value": e.source_notebook,
                    "input_candidate_index": e.input_candidate_index,
                    "project_root": str(project_root) if project_root else None,
                    "substring_ok": substring_ok,
                    "disk_ok": disk_ok,
                },
            )

        # source_cell shape check.
        if not _SOURCE_CELL_SHAPE_RE.match(e.source_cell):
            raise ValidationError(
                f"validator: row {pos}'s source_cell does not match "
                f"non-empty digit-only shape. Got: {e.source_cell!r}",
                {
                    "position": pos,
                    "field": "source_cell",
                    "value": e.source_cell,
                    "input_candidate_index": e.input_candidate_index,
                },
            )

        # figure_or_table: empty is OK; non-empty must ground.
        if e.figure_or_table:
            if (
                e.figure_or_table not in figures_inventory_text
                and e.figure_or_table not in tables_inventory_text
            ):
                raise ValidationError(
                    f"validator: row {pos}'s figure_or_table "
                    f"{e.figure_or_table!r} is not a substring of "
                    f"figures_inventory.md or tables_inventory.md "
                    f"(LLM may have fabricated the cite)",
                    {
                        "position": pos,
                        "field": "figure_or_table",
                        "value": e.figure_or_table,
                        "input_candidate_index": e.input_candidate_index,
                    },
                )


# ---------------------------------------------------------------------------
# Cache (B1.d idempotency)
# ---------------------------------------------------------------------------

def compute_cache_key(
    *,
    report_sha: str,
    methods_provenance_sha: str,
    figures_inventory_sha: str,
    tables_inventory_sha: str,
    prompt_sha: str,
    parser_version: str,
    batch_size: Optional[int] = None,
) -> str:
    """SHA-256 over the seven-tuple. parser_version inclusion follows
    feedback_cache_key_chunked_only_when_chunked: it's the safety net
    against silently-invisible parser fixes.

    Per feedback_cache_key_chunked_only_when_chunked, batch_size is mixed
    in only when explicitly set — passing the default
    DEFAULT_DEMARCATOR_BATCH_SIZE produces a different cache key than
    batch_size=None (which preserves the legacy 6-tuple key from B1.b).
    Tests calling compute_cache_key without batch_size remain
    byte-identical to legacy.

    Six base components vs A1.d's four because B1.c consumes
    figures_inventory.md and tables_inventory.md as additional grounding
    surfaces — a change in either materially affects which cross-links
    the LLM is allowed to emit, so they must invalidate the cache.

    B1.f adds the optional seventh component: batch_size. Different
    batch sizes can produce different LLM responses (the LLM's output
    distribution depends on input prompt length); cache-keying on
    batch_size enforces "if you change the chunking, rebuild the
    cache." The default-as-None pattern keeps existing tests stable.
    """
    h = hashlib.sha256()
    payload_dict: dict = {
        "report_sha": report_sha,
        "methods_provenance_sha": methods_provenance_sha,
        "figures_inventory_sha": figures_inventory_sha,
        "tables_inventory_sha": tables_inventory_sha,
        "prompt_sha": prompt_sha,
        "parser_version": parser_version,
    }
    if batch_size is not None:
        payload_dict["batch_size"] = batch_size
    payload = json.dumps(payload_dict, sort_keys=True)
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _read_cache(cache_path: Path) -> dict:
    """Return the cache dict (key → cached payload). Empty dict if the
    file doesn't exist or is unparseable."""
    if not cache_path.is_file():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corruption recovery: treat as empty. Don't crash a run because
        # a previous abort left half-written cache JSON.
        return {}


def _write_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-ish: write to .tmp, then rename. Cache files are small
    # and rarely contended; this is cheap insurance.
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(cache, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(cache_path)


def _cached_payload_to_demarcations(
    payload: dict,
) -> list[DemarcatorEntry]:
    """Reconstruct DemarcatorEntry list from a cached dict."""
    rows = payload.get("demarcations", [])
    return [
        DemarcatorEntry(
            input_candidate_index=int(r["input_candidate_index"]),
            claim_text=str(r["claim_text"]),
            source_notebook=str(r["source_notebook"]),
            source_cell=str(r["source_cell"]),
            figure_or_table=str(r.get("figure_or_table", "")),
            severity_justification=str(r.get("severity_justification", "")),
        )
        for r in rows
    ]


def _cached_payload_to_tolerated_missing(payload: dict) -> set[int]:
    """Reconstruct tolerated_missing set from a cached dict.

    Backwards-compat: payloads written before B1.g (D-039) lack the
    `tolerated_missing` key entirely → returns empty set, which keeps
    pre-B1.g cache files validating against the strict full-coverage
    rule (matching their original semantics)."""
    raw = payload.get("tolerated_missing", [])
    return {int(i) for i in raw}


# ---------------------------------------------------------------------------
# Top-level LLM seam
# ---------------------------------------------------------------------------

def demarcate_unresolved_with_llm(
    unresolved_candidates: list["ClaimCandidate"],
    *,
    methods_provenance_text: str,
    figures_inventory_text: str,
    tables_inventory_text: str,
    model: str = DEFAULT_DEMARCATOR_MODEL,
    prompt_path: Optional[Path] = None,
    llm_call: Optional[DemarcatorLLMCall] = None,
    project_root: Optional[Path] = None,
    batch_size: int = DEFAULT_DEMARCATOR_BATCH_SIZE,
    max_retries: int = MAX_DEMARCATOR_RETRIES,
) -> tuple[list[DemarcatorEntry], float, set[int]]:
    """B1.c entry point. Demarcates multi-numeric sentences via the LLM
    seam and returns (validated_demarcations, cost_usd, tolerated_missing).

    The empty-input shortcut returns ([], 0.0) without calling the LLM
    — saves cost on projects whose deterministic pre-pass already
    surfaced no multi-numeric sentences.

    Raises:
      LLMCallError on subprocess / JSON-shape failures (exit 3).
      ValidationError on schema violations (exit 4). The actual
        billed cost (summed across all batches that completed) is
        reattached to the ValidationError as ``e.cost_usd`` so the
        caller can record it in the audit line (B1.e closes the
        cost_usd=0.0-on-exit-4 gap).

    `llm_call` defaults to the module-level `demarcator_llm_call` seam
    (the subprocess wrapper). Tests pass a fake.

    `project_root` is forwarded to the validator's source_notebook
    grounding check (B1.e). When provided, the validator accepts cites
    that resolve to a real file under project_root, even if absent
    from methods_provenance.md.

    `batch_size` (B1.f, D-038, 2026-05-07): chunks unresolved candidates
    so a single LLM call doesn't blow past the model's effective
    output-token budget OR the subprocess wrapper's 180s timeout. The
    LLM sees per-batch local indices [0..batch_size); we offset back
    to absolute indices into `unresolved_candidates` before validation.
    Default 15 was calibrated on `ibd_phage_targeting` after a 133-
    candidate single call truncated to 91 demarcations. The single-
    batch path is preserved when len(unresolved) <= batch_size to keep
    behavior unchanged for small projects.

    Cost-cap reframing (B1.e D-037, 2026-05-07): the per-call ceiling
    is no longer a stderr-warning trigger. The audit line records
    cost_usd; a future tightening will set ceilings from observed
    data. Per Adam's directive, observability over enforcement during
    M1.

    Bounded retry on missing indices (B1.g, D-039, 2026-05-07): live
    Haiku 4.5 demarcator non-deterministically drops ~1–3 indices per
    dense-project run. After the initial batched sweep, missing
    indices are collected and retried in a fresh LLM call (capped at
    `max_retries`). Whatever indices remain after retries are returned
    in the third tuple element (`tolerated_missing`); the caller
    (`run_inventory` → `expand_with_demarcations`) falls back to the
    original `notes='unresolved'` candidate for those positions. The
    M2 holistic prompt's grounding contract (every candidate has at
    least one row) is preserved by the original sentence carrying
    forward when the LLM couldn't split it.
    """
    if not unresolved_candidates:
        return [], 0.0, set()

    prompt_path = prompt_path or _PROMPT_PATH
    if not prompt_path.is_file():
        raise LLMCallError(
            f"claim_demarcate prompt not found at {prompt_path}; "
            f"the skill installation is incomplete"
        )
    if batch_size < 1:
        raise ValueError(
            f"batch_size must be >= 1; got {batch_size!r}"
        )
    if max_retries < 0:
        raise ValueError(
            f"max_retries must be >= 0; got {max_retries!r}"
        )

    system_prompt = prompt_path.read_text(encoding="utf-8")
    call = llm_call or demarcator_llm_call

    n = len(unresolved_candidates)
    all_demarcations: list[DemarcatorEntry] = []
    total_cost_usd = 0.0

    # Helper that runs the LLM over a list of (absolute_index, candidate)
    # pairs, with batching. Returns (demarcations, cost_usd) where
    # demarcations have absolute input_candidate_index already set.
    def _run_demarcator_pass(
        pairs: list[tuple[int, ClaimCandidate]],
        *,
        pass_label: str,
    ) -> tuple[list[DemarcatorEntry], float]:
        if not pairs:
            return [], 0.0
        m = len(pairs)
        # Single-batch fast-path: no offset arithmetic needed; preserves
        # exact pre-B1.f behavior on small projects.
        do_batch_local = m > batch_size
        out_demarcations: list[DemarcatorEntry] = []
        out_cost = 0.0
        for batch_start_local in range(0, m, batch_size):
            batch_pairs = pairs[batch_start_local : batch_start_local + batch_size]
            batch_candidates = [c for _abs_idx, c in batch_pairs]
            user_prompt = build_demarcator_user_prompt(
                batch_candidates,
                methods_provenance_text=methods_provenance_text,
                figures_inventory_text=figures_inventory_text,
                tables_inventory_text=tables_inventory_text,
                project_root=project_root,
            )
            try:
                response_text, batch_cost = call(system_prompt, user_prompt, model)
            except LLMCallError as e:
                # Annotate so the operator can pin whether one specific
                # batch timed out vs. a model-level issue, and whether
                # the failure was on the initial pass or a retry.
                first_local = batch_pairs[0][0]
                last_local = batch_pairs[-1][0]
                if do_batch_local or pass_label != "initial":
                    raise LLMCallError(
                        f"{pass_label} batch "
                        f"{batch_start_local // batch_size + 1} of "
                        f"{(m + batch_size - 1) // batch_size} "
                        f"(absolute candidates {first_local}..{last_local}): "
                        f"{e}"
                    ) from e
                raise

            out_cost += batch_cost
            batch_demarcations = parse_demarcator_response(response_text)
            # The LLM saw local indices [0..len(batch_pairs)). Map
            # back to absolute via batch_pairs[k][0].
            for d in batch_demarcations:
                local = d.input_candidate_index
                if not (0 <= local < len(batch_pairs)):
                    # Out-of-range LLM index. Drop the row defensively;
                    # the coverage check will catch the resulting gap
                    # and trigger a retry (or fall through to
                    # tolerated_missing). Don't raise here — we want
                    # the rest of the batch to still register.
                    continue
                d.input_candidate_index = batch_pairs[local][0]
                out_demarcations.append(d)
        return out_demarcations, out_cost

    # ---- Initial pass: all candidates ---------------------------------
    initial_pairs = list(enumerate(unresolved_candidates))
    initial_demarcations, initial_cost = _run_demarcator_pass(
        initial_pairs, pass_label="initial"
    )
    all_demarcations.extend(initial_demarcations)
    total_cost_usd += initial_cost

    # ---- Bounded retry on missing indices -----------------------------
    expected = set(range(n))

    def _missing_set() -> set[int]:
        seen = {d.input_candidate_index for d in all_demarcations}
        return expected - seen

    missing = _missing_set()
    retry_round = 0
    while missing and retry_round < max_retries:
        retry_round += 1
        sys.stderr.write(
            f"  note: demarcator retry round {retry_round} of {max_retries} "
            f"on {len(missing)} missing index(es): "
            f"{sorted(missing)[:10]}"
            f"{'...' if len(missing) > 10 else ''}\n"
        )
        retry_pairs = [(idx, unresolved_candidates[idx]) for idx in sorted(missing)]
        retry_demarcations, retry_cost = _run_demarcator_pass(
            retry_pairs, pass_label=f"retry-{retry_round}"
        )
        all_demarcations.extend(retry_demarcations)
        total_cost_usd += retry_cost
        missing = _missing_set()

    # If retries exhausted without full coverage, residuals fall
    # through to `expand_with_demarcations`' empty-rows pass-through.
    tolerated_missing: set[int] = missing
    if tolerated_missing:
        sys.stderr.write(
            f"  warn: demarcator could not cover {len(tolerated_missing)} "
            f"input candidate(s) after {max_retries} retries; falling back "
            f"to original notes='unresolved' rows for indices: "
            f"{sorted(tolerated_missing)}\n"
        )

    # Sort by absolute input_candidate_index to keep
    # validate_demarcations' ascending-order check happy. Within an
    # index, preserve emission order across the initial + retry passes.
    all_demarcations.sort(key=lambda d: d.input_candidate_index)

    try:
        validate_demarcations(
            all_demarcations,
            unresolved_candidates,
            methods_provenance_text=methods_provenance_text,
            figures_inventory_text=figures_inventory_text,
            tables_inventory_text=tables_inventory_text,
            project_root=project_root,
            allow_missing=tolerated_missing,
        )
    except ValidationError as e:
        # B1.e: reattach the cumulative billed cost so main()'s exit-4
        # audit line records it. The caller catches this re-raised
        # error and threads e.cost_usd into emit_audit_line.
        e.cost_usd = total_cost_usd  # type: ignore[attr-defined]
        raise

    return all_demarcations, total_cost_usd, tolerated_missing


# ---------------------------------------------------------------------------
# Candidate expansion (replace unresolved rows with demarcated rows)
# ---------------------------------------------------------------------------

def _flags_for_claim_text(claim_text: str) -> tuple[str, str, str]:
    """Re-run the deterministic regex sweep on a single claim_text and
    return (effect_size_present, ci_present, pvalue_present).

    Used after LLM demarcation to give each split row its OWN flag
    aggregation rather than inheriting the original sentence's flags
    (which would overstate per-row rigor — e.g., the AUC row inheriting
    ci_present=yes from a sentence whose CI was a separate clause).
    """
    matches = extract_numeric_matches(claim_text)
    class_names = [m.match_class for m in matches]
    return _flags_from_classes(class_names)


def expand_with_demarcations(
    candidates: list["ClaimCandidate"],
    unresolved_candidates: list["ClaimCandidate"],
    demarcations: list[DemarcatorEntry],
) -> list["ClaimCandidate"]:
    """Replace each `notes='unresolved'` row in `candidates` with the
    LLM's demarcated rows. Renumbers all `claim_id`s C001..CMMM where
    M = len(non-unresolved) + len(demarcations).

    Source-order preservation: candidates appear in REPORT.md order;
    expanded rows for an unresolved candidate appear in
    demarcation-emission order at the unresolved row's position.

    Flag aggregation: each demarcated row's flags are RECOMPUTED from
    its own claim_text via the deterministic regex sweep — NOT inherited
    from the source unresolved sentence. Per feedback_llm_arithmetic_unreliable
    the LLM never touches flag values; flags stay deterministic.
    """
    # Index unresolved candidates by their position in the unresolved
    # list (0-based) — that's what input_candidate_index references.
    # The unresolved_candidates list is what was passed into the LLM,
    # so the indices match by construction.
    by_input_idx: dict[int, list[DemarcatorEntry]] = {}
    for d in demarcations:
        by_input_idx.setdefault(d.input_candidate_index, []).append(d)

    # Map each unresolved candidate's position in the GLOBAL `candidates`
    # list to its position in the unresolved-only list (LLM input index).
    unresolved_id_set = {id(c) for c in unresolved_candidates}
    # Use id() because ClaimCandidate doesn't have a hashable identity
    # by default (it's a mutable dataclass) — though dataclass without
    # eq/frozen actually IS hashable by id by default in Python 3.10+.
    # id() is the explicit form.

    unresolved_position_for_id: dict[int, int] = {
        id(c): i for i, c in enumerate(unresolved_candidates)
    }

    out: list[ClaimCandidate] = []
    next_id = 1

    for c in candidates:
        if c.notes != "unresolved" or id(c) not in unresolved_id_set:
            # Pass-through, just renumber.
            new = dataclasses.replace(c, claim_id=f"C{next_id:03d}")
            out.append(new)
            next_id += 1
            continue

        # Unresolved: replace with one row per demarcation.
        ipos = unresolved_position_for_id[id(c)]
        rows = by_input_idx.get(ipos, [])
        # The validator already enforced ≥1 row per index, so this
        # should be non-empty — but defensively handle the empty case
        # by passing the original unresolved row through unchanged.
        if not rows:
            new = dataclasses.replace(c, claim_id=f"C{next_id:03d}")
            out.append(new)
            next_id += 1
            continue

        for d in rows:
            eff, ci, pv = _flags_for_claim_text(d.claim_text)
            out.append(ClaimCandidate(
                claim_id=f"C{next_id:03d}",
                claim_text=d.claim_text,
                source_notebook=d.source_notebook,
                source_cell=d.source_cell,
                figure_or_table=d.figure_or_table,
                effect_size_present=eff,
                ci_present=ci,
                pvalue_present=pv,
                # notes cleared on resolved rows; severity_justification
                # is informational only, dropped at TSV emit time.
                notes="",
            ))
            next_id += 1
    return out


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
    idempotency cache had a hit on the six-tuple (report_sha,
    methods_provenance_sha, figures_inventory_sha, tables_inventory_sha,
    prompt_sha, parser_VERSION). The append still happens — every
    invocation is observable per SPEC §4.7. Cost on a hit is 0.0 (no
    LLM bill); we don't re-charge the original cached call to the
    rerun.

    `cost_usd=0.0` in deterministic-only mode (no LLM call). With
    --no-llm not set AND unresolved candidates, this is the actual
    demarcator subprocess cost.
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
    # Full demarcations kept on the result so the cache writer + tests
    # can introspect them. Empty list when no_llm or no unresolved
    # candidates surfaced.
    demarcations: list[DemarcatorEntry] = field(default_factory=list)
    # B1.g (D-039): set of input candidate indices that the LLM (after
    # max_retries) couldn't demarcate. expand_with_demarcations falls
    # through to the original `notes='unresolved'` row for these. Cached
    # alongside demarcations so reruns honor the same residual set.
    tolerated_missing: set[int] = field(default_factory=set)


def run_inventory(
    *,
    report_text: str,
    methods_provenance_text: str = "",
    figures_inventory_text: str = "",
    tables_inventory_text: str = "",
    no_llm: bool,
    llm_call: Optional[DemarcatorLLMCall] = None,
    cached_demarcations: Optional[list[DemarcatorEntry]] = None,
    cached_tolerated_missing: Optional[set[int]] = None,
    project_root: Optional[Path] = None,
    batch_size: int = DEFAULT_DEMARCATOR_BATCH_SIZE,
    max_retries: int = MAX_DEMARCATOR_RETRIES,
) -> RunResult:
    """Pure-function orchestration: segment + extract + build candidates
    + (LLM if requested and unresolved candidates exist).

    Doesn't touch disk; doesn't emit audit. main() handles those so
    this function is straightforward to unit-test.

    With `no_llm=True`: returns deterministic candidates only.
    Multi-numeric sentences are marked notes="unresolved". The methods/
    figures/tables texts are unused on this path (they default to "").

    With `no_llm=False`: if any candidate is "unresolved",
    demarcate_unresolved_with_llm() fires — splits unresolved
    candidates into multiple resolved rows, recomputes per-row flags
    via the deterministic regex sweep, and renumbers all claim_ids.
    Raises LLMCallError on subprocess/JSON-shape failures (caller maps
    to exit 3) or ValidationError on schema violations (caller maps to
    exit 4).

    `llm_call` overrides the module-level `demarcator_llm_call` seam —
    used by tests to inject canned responses.

    `cached_demarcations` short-circuits the LLM call when supplied
    (the cache layer in main() does this on a cache hit). Cached
    demarcations are STILL re-validated against current inputs — if
    a hand-edit poisoned the cache, we fall through to a fresh LLM
    call rather than ship a broken row.
    """
    sentences = segment_sentences(report_text)
    matches = extract_numeric_matches(report_text)
    candidates = build_candidates(matches, sentences)
    unresolved_candidates = [c for c in candidates if c.notes == "unresolved"]

    cost_usd = 0.0
    cache_hit = False
    demarcations: list[DemarcatorEntry] = []
    tolerated_missing: set[int] = set()

    if not no_llm and unresolved_candidates:
        if cached_demarcations is not None:
            # Cache-hit path: skip LLM, re-validate cached entries with
            # the cached tolerated_missing set so a partial-coverage
            # cache (B1.g residuals) re-validates byte-stably. Defensive
            # — the cache file could have been hand-edited between
            # runs; if validation fails we rebuild rather than ship
            # stale rows.
            try:
                validate_demarcations(
                    cached_demarcations,
                    unresolved_candidates,
                    methods_provenance_text=methods_provenance_text,
                    figures_inventory_text=figures_inventory_text,
                    tables_inventory_text=tables_inventory_text,
                    project_root=project_root,
                    allow_missing=cached_tolerated_missing or set(),
                )
                demarcations = cached_demarcations
                tolerated_missing = cached_tolerated_missing or set()
                cache_hit = True
                cost_usd = 0.0
            except ValidationError:
                sys.stderr.write(
                    "  note: claim_inventory_cache.json failed re-validation; "
                    "falling through to a fresh LLM call\n"
                )
                demarcations, cost_usd, tolerated_missing = (
                    demarcate_unresolved_with_llm(
                        unresolved_candidates,
                        methods_provenance_text=methods_provenance_text,
                        figures_inventory_text=figures_inventory_text,
                        tables_inventory_text=tables_inventory_text,
                        llm_call=llm_call,
                        project_root=project_root,
                        batch_size=batch_size,
                        max_retries=max_retries,
                    )
                )
        else:
            demarcations, cost_usd, tolerated_missing = (
                demarcate_unresolved_with_llm(
                    unresolved_candidates,
                    methods_provenance_text=methods_provenance_text,
                    figures_inventory_text=figures_inventory_text,
                    tables_inventory_text=tables_inventory_text,
                    llm_call=llm_call,
                    project_root=project_root,
                    batch_size=batch_size,
                    max_retries=max_retries,
                )
            )

        # Expand the candidate list: replace each unresolved row with
        # the LLM's demarcated rows, recompute per-row flags, renumber.
        # expand_with_demarcations' defensive empty-rows pass-through
        # handles indices in tolerated_missing — they keep their
        # original notes='unresolved' identity in the output.
        candidates = expand_with_demarcations(
            candidates, unresolved_candidates, demarcations,
        )

    return RunResult(
        candidates=candidates,
        inventory_size=len(candidates),
        unresolved_count=sum(1 for c in candidates if c.notes == "unresolved"),
        cost_usd=cost_usd,
        cache_hit=cache_hit,
        tolerated_missing=tolerated_missing,
        demarcations=demarcations,
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
            "B1.c LLM uses it to ground source_notebook + source_cell cites; "
            "validator rejects cites that don't appear in this file."
        ),
    )
    p.add_argument(
        "--figures-inventory",
        type=Path,
        required=True,
        help=(
            "Path to figures_inventory.md (emitted by extract_figures.py). "
            "B1.c LLM uses it to ground figure_or_table cross-links; "
            "validator rejects ungrounded non-empty cites."
        ),
    )
    p.add_argument(
        "--tables-inventory",
        type=Path,
        required=True,
        help=(
            "Path to tables_inventory.md (emitted by extract_tables.py). "
            "B1.c LLM uses it to ground figure_or_table cross-links; "
            "validator rejects ungrounded non-empty cites."
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
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_DEMARCATOR_BATCH_SIZE,
        help=(
            "Number of unresolved candidates to demarcate per LLM call. "
            "Default 15 — calibrated on ibd_phage_targeting where a "
            "single 133-candidate call truncated the LLM's output and "
            "later runs hit the 180s subprocess timeout. Total cost on "
            "dense projects: ceil(N/batch_size) × ~$0.10/batch. Lower "
            "values increase robustness; higher values reduce wall "
            "time when projects have few unresolved candidates. Cache "
            "key includes batch_size, so changing it invalidates the "
            "idempotency cache."
        ),
    )
    p.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help=(
            "Optional project root (the directory containing "
            "RESEARCH_PLAN.md / REPORT.md / notebooks/). When set, the "
            "B1.d validator additionally accepts source_notebook cites "
            "that resolve to a real file under <project-root>/, even "
            "if absent from methods_provenance.md (extract_methods.py "
            "only catalogs notebooks with AST-detected stat tests; "
            "real numerical claims often source from notebooks that "
            "use pandas/SQL/custom code instead). Defaults to "
            "<methods-provenance>.parent.parent (i.e. "
            "<project>/papers/draft_N/methods_provenance.md → "
            "<project>) if that resolves to a directory; otherwise "
            "remains None and the validator runs substring-only."
        ),
    )
    args = p.parse_args(argv)

    report_path: Path = args.report
    methods_path: Path = args.methods_provenance
    figs_path: Path = args.figures_inventory
    tbls_path: Path = args.tables_inventory
    out_dir: Path = args.output_dir
    batch_size: int = args.batch_size

    if batch_size < 1:
        print(
            f"error: --batch-size must be >= 1; got {batch_size}",
            file=sys.stderr,
        )
        return 2

    # Project root resolution (B1.e): prefer explicit --project-root,
    # else derive from methods_provenance.md's expected layout
    # (<project>/papers/draft_N/methods_provenance.md → parents[2]).
    # Leave as None if derivation can't ground (synthetic test
    # fixtures often write methods_provenance.md at tmp_path root).
    project_root: Optional[Path] = args.project_root
    if project_root is None:
        try:
            derived = methods_path.resolve().parents[2]
            if derived.is_dir():
                project_root = derived
        except IndexError:
            project_root = None

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
        methods_provenance_text = methods_path.read_text(encoding="utf-8")
        figures_inventory_text = figs_path.read_text(encoding="utf-8")
        tables_inventory_text = tbls_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read input file: {e}", file=sys.stderr)
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

    # Idempotency cache lookup (B1.d). Skipped when --no-llm is set
    # (no LLM call to cache) or when the prompt file is missing (we
    # can't compute a stable cache key without it). Cache key is the
    # six-tuple SHA-256 over (report_sha, methods_sha, figures_sha,
    # tables_sha, prompt_sha, parser_VERSION).
    cache_path = out_dir / "audit" / "claim_inventory_cache.json"
    cache_key: Optional[str] = None
    cached_demarcations: Optional[list[DemarcatorEntry]] = None

    if not args.no_llm and _PROMPT_PATH.is_file():
        cache_key = compute_cache_key(
            report_sha=_sha256_of_path(report_path),
            methods_provenance_sha=_sha256_of_path(methods_path),
            figures_inventory_sha=_sha256_of_path(figs_path),
            tables_inventory_sha=_sha256_of_path(tbls_path),
            prompt_sha=_sha256_of_path(_PROMPT_PATH),
            parser_version=VERSION,
            batch_size=batch_size,
        )
        cache = _read_cache(cache_path)
        if cache_key in cache:
            cached_demarcations = _cached_payload_to_demarcations(
                cache[cache_key]
            )
            cached_tolerated_missing = _cached_payload_to_tolerated_missing(
                cache[cache_key]
            )
        else:
            cached_tolerated_missing = None
    else:
        cached_tolerated_missing = None

    try:
        result = run_inventory(
            report_text=report_text,
            methods_provenance_text=methods_provenance_text,
            figures_inventory_text=figures_inventory_text,
            tables_inventory_text=tables_inventory_text,
            no_llm=args.no_llm,
            cached_demarcations=cached_demarcations,
            cached_tolerated_missing=cached_tolerated_missing,
            project_root=project_root,
            batch_size=batch_size,
        )
    except LLMCallError as e:
        print(f"error: LLM call failed: {e}", file=sys.stderr)
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
    except ValidationError as e:
        print(
            f"error: validator rejected LLM output: {e}",
            file=sys.stderr,
        )
        if e.diagnostics:
            print(
                f"  diagnostics: {json.dumps(e.diagnostics, sort_keys=True)}",
                file=sys.stderr,
            )
        # B1.e: record the actual billed cost. demarcate_unresolved_with_llm
        # reattaches the call's cost_usd to the ValidationError so the
        # audit line is honest about LLM spend even on rejection. If the
        # error came from the cache-revalidation path or validator
        # invocations that don't bill (synthetic tests, --no-llm
        # poisoning), the attribute will be missing; record 0.0 in that
        # case rather than fabricate.
        billed_cost = float(getattr(e, "cost_usd", 0.0) or 0.0)
        emit_audit_line(
            audit_path=audit_path,
            report_path=report_path,
            methods_provenance_path=methods_path,
            figures_inventory_path=figs_path,
            tables_inventory_path=tbls_path,
            output_path=output_path,
            inventory_size=0,
            unresolved_count=0,
            cost_usd=billed_cost,
            exit_status=4,
        )
        return 4

    # Write the TSV.
    tsv_text = format_claim_inventory_tsv(result.candidates)
    output_path.write_text(tsv_text, encoding="utf-8")

    # Persist the cache only after a fresh LLM call (NOT on a cache hit
    # — we don't need to re-write what we just read; NOT on --no-llm —
    # there's nothing to cache; NOT when no demarcations were emitted
    # AND no tolerated_missing — no LLM call happened). The B1.g
    # tolerated_missing set is persisted so reruns honor the same
    # residuals and don't redo retries that wouldn't recover them.
    if (
        cache_key is not None
        and not result.cache_hit
        and not args.no_llm
        and (result.demarcations or result.tolerated_missing)
    ):
        cache = _read_cache(cache_path)
        cache[cache_key] = {
            "demarcations": [d.to_dict() for d in result.demarcations],
            "tolerated_missing": sorted(result.tolerated_missing),
            "cost_usd": result.cost_usd,
            "timestamp": _utc_now_iso(),
        }
        _write_cache(cache_path, cache)

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
        + (" [cache hit]" if result.cache_hit else "")
        + ")",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
