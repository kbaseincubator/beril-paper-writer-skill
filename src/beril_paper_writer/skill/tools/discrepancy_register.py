#!/usr/bin/env python3
"""discrepancy_register.py — Plan-vs-execution diff scanner (Phase 0, v0.8).

Per SPEC_v0_8 §4.5 + DECISIONS.md D-034 Q1:

  Surfaces every place where RESEARCH_PLAN.md prescribed an analysis the
  notebooks did not execute, OR the notebooks executed an analysis the
  plan did not prescribe. Lifts upstream what v0.7.x's reframer.v1 prompt
  did post-hoc in prose, so the holistic write (Phase 2) sees discrepancies
  *before* it drafts.

Pipeline:

  1. Deterministic pre-pass (this conversation: A1.a + A1.b):
       - Parse RESEARCH_PLAN.md for analysis declarations under headings
         matching /analys[ei]s|method|test|stat/i (bullets / numbered list).
       - Parse methods_provenance.md (already a structured artifact emitted
         by extract_methods.py) for executed analyses with notebook+cell.
       - Normalize each phrase (lowercase + stopword removal + light Porter
         stem), then partition into plan_only / exec_only / overlap.

  2. LLM classification pass (NEXT conversation: A1.c — NOT in this code):
       - Haiku-4.5 over `overlap` candidates only, deciding
         {equivalent | paraphrase | discrepancy}. Equivalent + paraphrase
         pairs do NOT become register entries; discrepancy pairs do.
       - Cost ceiling $0.05/run (SPEC §4.5).

  3. Validator + idempotency cache (NEXT-NEXT — A1.d).

I/O contract (this milestone):

  --methods-provenance <path>    required; markdown
  --research-plan <path>         required; markdown
  --reframing-log <path>         optional; passed through, not load-bearing
  --output-dir <path>            required; writes:
                                   <output-dir>/discrepancy_register.md
                                   <output-dir>/audit/phase0.jsonl  (append)
  --no-llm                       debug; skip A1.c (currently always set —
                                 A1.c not yet implemented; without this
                                 flag the tool exits 3 per the contract).

Exit codes:
  0 — success (register file written; entry count may be zero).
  1 — usage error (--help, missing required flag).
  2 — input parse error (required file missing or empty).
  3 — LLM call failure (or A1.c not yet implemented), with --no-llm not set.

Audit JSONL line schema (one line per invocation, appended to
<output-dir>/audit/phase0.jsonl):

  {
    "timestamp": "2026-05-07T14:23:01Z",
    "tool": "discrepancy_register",
    "version": "0.8.0-m1-A1.b",
    "inputs": {
      "methods_provenance": "<sha256>",
      "research_plan": "<sha256>"
    },
    "output_path": "<absolute path to discrepancy_register.md>",
    "entry_count": 3,
    "cost_usd": 0.0,
    "exit_status": 0
  }

Discipline notes baked into this module (per auto-memory):

  - feedback_llm_json_unfixable_in_parser: any LLM JSON parse site MUST
    accompany prompt-side anti-pattern rules. The parser cannot fix
    unescaped `"` inside string values. (Applied in A1.c.)
  - feedback_llm_json_trailing_commas_repairable: lenient_json_load
    helper IS pre-baked here so A1.c can reuse it. Trailing-comma repair
    is unambiguous and worth the safety net.
  - feedback_render_test_must_evaluate_fstring: the markdown emitter is
    a regular function (not an f-string template), but unit tests still
    EVALUATE format_register_md against synthetic candidates rather than
    grep the source.
  - feedback_no_git_writes_in_sandbox: this module never invokes git.

This module is importable (functions are module-level + dataclass-based)
and runnable as a script (CLI under main()).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# Module version. Distinct from package version because audit consumers
# may want to track precisely which sub-milestone wrote a given line.
# Bump on contract-affecting changes (new audit fields, schema changes).
VERSION = "0.8.0-m1-A1.b"


# ---------------------------------------------------------------------------
# Normalization (used by both pre-pass and LLM input prep)
# ---------------------------------------------------------------------------

# A small, conservative stopword set. Big enough to collapse common
# determiners/connectives in analysis-plan prose; small enough not to
# eat content words. We don't pull NLTK because pyproject.toml only
# allows pure-Python stdlib + python-docx + nbformat.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the",
    "of", "in", "on", "at", "for", "to", "from", "with", "by", "as", "into",
    "and", "or", "but", "nor", "so", "yet",
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "done",
    "we", "i", "our", "us", "you", "they", "their",
    "this", "that", "these", "those",
    "then", "than", "if", "when", "while", "because",
    "will", "would", "should", "shall",
    "can", "could", "may", "might", "must",
    "have", "has", "had",
    "across", "between", "among", "via",
    "use", "using", "used",
    # "over", "under" intentionally omitted — they can carry methodological meaning
    # ("over time", "under FDR<0.05") that we don't want to drop on principle.
})


# Light suffix-stripping (Porter-ish). Order matters: longest first.
# This is intentionally crude — we only need "test" ≡ "testing" ≡ "tests"
# and "correct" ≡ "correction" ≡ "corrected". A real Porter stemmer is
# overkill and would pull a dependency.
_SUFFIX_RULES: list[tuple[str, str]] = [
    ("ization", "ize"),
    ("izations", "ize"),
    ("ational", "ate"),
    ("tional", "tion"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ation", ""),
    ("ations", ""),
    ("ively", "ive"),
    ("ities", "ity"),
    ("iest", "y"),
    ("ements", "ement"),
    ("ically", "ic"),
    ("ility", "ile"),
    ("able", ""),
    ("ible", ""),
    ("ment", ""),
    ("ness", ""),
    ("less", ""),
    ("ful", ""),
    ("ous", ""),
    ("ing", ""),
    ("ies", "y"),
    ("ied", "y"),
    ("ed", ""),
    ("es", ""),
    ("s", ""),
]


def _stem(token: str) -> str:
    """Light suffix-stripping. Tokens of length ≤ 3 are returned as-is
    (avoids stripping "u" off "us", "is" off short identifiers, etc.).
    """
    if len(token) <= 3:
        return token
    for suffix, replacement in _SUFFIX_RULES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + replacement
    return token


# Tokenize on word characters only. Drop punctuation. Preserves digits
# inside tokens (e.g., "alpha=0.05" → "alpha", "0", "05") which are
# usually noise in this normalization but harmless.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def normalize_phrase(text: str) -> str:
    """Lowercase + tokenize + drop stopwords + stem. Returns a
    space-joined canonical form suitable for set/membership comparison.

    Examples:
      "Welch's t-test with alpha=0.05" → "welch t-test alpha"
      "Run a t-test"                   → "t-test"
      "Mann-Whitney U test"            → "mann-whitney u test"
    """
    if not text:
        return ""
    # We tokenize on letter-runs but want "t-test" / "Mann-Whitney" / "p-value"
    # to survive as single tokens. So first lowercase, then split on whitespace
    # and drop punctuation around each whitespace-token, preserving internal
    # hyphens.
    pieces: list[str] = []
    for ws_tok in text.lower().split():
        # Strip leading/trailing punctuation, keep internal hyphens.
        stripped = ws_tok.strip(".,;:()[]{}\"'`")
        if not stripped:
            continue
        # If the token contains letters, keep it; else drop (pure-numeric,
        # punctuation-only, etc.).
        if not any(c.isalpha() for c in stripped):
            continue
        pieces.append(stripped)
    out: list[str] = []
    for p in pieces:
        if p in _STOPWORDS:
            continue
        # Stem each hyphenated subpart independently so "tests"/"testing"
        # collapse, but compound terms like "t-test" stay readable.
        if "-" in p:
            sub = "-".join(_stem(s) for s in p.split("-"))
        else:
            sub = _stem(p)
        if sub and sub not in _STOPWORDS:
            out.append(sub)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Plan-side parsing: bullets/numbered-list items under analysis headings
# ---------------------------------------------------------------------------

# Heading match: captures the heading text. We look for level 1–6 ATX
# headings (`#` through `######`).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# A heading is "analysis-related" if its text matches any of these
# substrings (case-insensitive). Per A1.b spec.
_ANALYSIS_HEADING_RE = re.compile(
    r"analys[ei]s|method|test|stat",
    re.IGNORECASE,
)

# Bullet line: `-`, `*`, `+`, or `N.` / `N)` / `(N)` with optional indent.
_BULLET_RE = re.compile(
    r"^\s*(?:[-*+]|\(?\d+[.)])\s+(.+?)\s*$",
)


@dataclass
class PlanAnalysis:
    """One declared analysis from RESEARCH_PLAN.md."""
    plan_section: str       # The heading text the bullet appeared under.
    plan_quote: str         # Verbatim bullet text (≤200 chars; truncated if longer).
    normalized_phrase: str  # normalize_phrase(plan_quote).

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_plan_analyses(plan_text: str) -> list[PlanAnalysis]:
    """Walk RESEARCH_PLAN.md; collect bullet/numbered-list items under any
    heading whose text matches `/analys[ei]s|method|test|stat/i`.

    Why bullets and not section bodies: SPEC §4.5's example D-001 cites
    a single prescribed analysis ("Welch's t-test with α=0.05") as a
    single register entry. The atomic unit IS the bullet, not the
    section. Sections that mix narrative + bullets only contribute
    bullets to the register.

    Multi-line bullets: research-plan authors routinely soft-wrap
    bullets across two or more indented continuation lines:

        - Pearson correlation between dose and OD600 endpoint,
          across the dose series.
        - Two-sample t-test comparing mean OD600 of strain Y...

    The continuation lines are folded into the bullet's quote — they
    typically contain the operative methodological detail (the test
    name, the design, the threshold). Truncating to first-line only
    yields half-quotes that fail to ground M2's holistic write.

    A continuation line is: indented (whitespace prefix), AND not a
    bullet itself, AND not a heading. A bullet ends at the next bullet,
    blank line, heading, or end of section.
    """
    analyses: list[PlanAnalysis] = []
    current_heading: str = ""
    in_analysis_section = False

    # Two-pass: collect raw lines into bullet groups first, then build
    # PlanAnalysis. Streamlines the continuation-fold logic.
    @dataclass
    class _Pending:
        section: str
        first_line: str
        cont_lines: list[str] = field(default_factory=list)

    pending: Optional[_Pending] = None

    def _flush() -> None:
        nonlocal pending
        if pending is None:
            return
        full = pending.first_line.strip()
        for cont in pending.cont_lines:
            piece = cont.strip()
            if piece:
                full += " " + piece
        # Cap quote length to keep register entries scannable; leave
        # ample room for a typical 2-3 line research-plan bullet.
        if len(full) > 300:
            full = full[:297] + "..."
        if full:
            analyses.append(PlanAnalysis(
                plan_section=pending.section,
                plan_quote=full,
                normalized_phrase=normalize_phrase(full),
            ))
        pending = None

    for raw_line in plan_text.splitlines():
        # Heading: closes any open bullet, switches sections.
        m_head = _HEADING_RE.match(raw_line)
        if m_head:
            _flush()
            current_heading = m_head.group(2).strip()
            in_analysis_section = bool(_ANALYSIS_HEADING_RE.search(current_heading))
            continue
        if not in_analysis_section:
            continue

        # Blank line: closes any open bullet.
        if not raw_line.strip():
            _flush()
            continue

        m_bul = _BULLET_RE.match(raw_line)
        if m_bul:
            # New bullet: close previous, start fresh.
            _flush()
            pending = _Pending(
                section=current_heading,
                first_line=m_bul.group(1),
            )
            continue

        # Non-blank, non-bullet, non-heading line: continuation IF
        # we have an open bullet AND this line is indented (any leading
        # whitespace). An un-indented run-on line in the middle of a
        # bulleted list is a malformed plan; treat as a section-level
        # narrative break and close the bullet.
        if pending is not None and (raw_line.startswith(" ") or raw_line.startswith("\t")):
            pending.cont_lines.append(raw_line)
        else:
            _flush()

    # End-of-text: close any final open bullet.
    _flush()
    return analyses


# ---------------------------------------------------------------------------
# Execution-side parsing: methods_provenance.md → executed analyses
# ---------------------------------------------------------------------------

# methods_provenance.md emitter (from extract_methods.format_methods_provenance_md):
#
#   ## Statistical Tests Detected
#
#   ### {test_name}
#
#   - `{library_path}` in **{nb.path}** (cell {cell}, line {line}) — kw: ...
#   - ...
#
# We treat each `### test_name` as the canonical "executed analysis name",
# and each bullet as a citation tying it to (notebook_path, cell, line).
# An H2 of `## Statistical Tests Detected` opens the section; the next H2
# closes it.

_TEST_BULLET_RE = re.compile(
    r"^\s*-\s+`(?P<lib>[^`]+)`\s+in\s+\*\*(?P<nb>[^*]+)\*\*\s*"
    r"\(cell\s+(?P<cell>\d+),\s+line\s+(?P<line>\d+)\)"
)


@dataclass
class ExecAnalysis:
    """One executed analysis, with notebook/cell citation."""
    test_name: str          # The H3 under "Statistical Tests Detected".
    library_path: str       # e.g. "scipy.stats.fisher_exact".
    notebook: str           # Notebook path as recorded in provenance.
    cell: int               # 0-indexed cell number.
    line: int               # Line within the cell source.
    # The citation form we'll quote in the register (verbatim from the
    # provenance bullet) — kept un-truncated; it's already a one-liner.
    citation_quote: str
    normalized_phrase: str  # normalize_phrase(test_name).

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_provenance_executions(provenance_text: str) -> list[ExecAnalysis]:
    """Walk methods_provenance.md; collect executed analyses under
    `## Statistical Tests Detected`.

    We deliberately don't pull from `## Spark / K-BERDL Queries` or
    `## Imports by Notebook` — those aren't analysis declarations in the
    SPEC §4.5 sense. Future bumps may extend this.
    """
    executions: list[ExecAnalysis] = []
    in_stats_section = False
    current_test_name: str = ""
    for raw_line in provenance_text.splitlines():
        m_head = _HEADING_RE.match(raw_line)
        if m_head:
            level = len(m_head.group(1))
            heading = m_head.group(2).strip()
            if level == 2:
                in_stats_section = bool(
                    re.search(r"statistical\s+tests?\s+detected", heading, re.IGNORECASE)
                )
                current_test_name = ""
                continue
            if level == 3 and in_stats_section:
                current_test_name = heading
                continue
        if not in_stats_section or not current_test_name:
            continue
        m_bul = _TEST_BULLET_RE.match(raw_line)
        if not m_bul:
            continue
        executions.append(ExecAnalysis(
            test_name=current_test_name,
            library_path=m_bul.group("lib"),
            notebook=m_bul.group("nb").strip(),
            cell=int(m_bul.group("cell")),
            line=int(m_bul.group("line")),
            citation_quote=raw_line.strip().lstrip("-").strip(),
            normalized_phrase=normalize_phrase(current_test_name),
        ))
    return executions


# ---------------------------------------------------------------------------
# Pre-pass classification — partition into plan_only / exec_only / overlap
# ---------------------------------------------------------------------------

@dataclass
class PrePassCandidate:
    """One row out of the deterministic pre-pass.

    `kind` ∈ {plan_only, exec_only, overlap}.
      - plan_only: prescribed in the plan, no normalized-equivalent
        execution found.
      - exec_only: executed in a notebook, no normalized-equivalent
        prescription found.
      - overlap: both sides match on normalized phrase. The LLM step
        (A1.c — NOT in this conversation) decides whether such pairs
        are equivalent / paraphrase / discrepancy. Without LLM, we
        skip emission and report the count.

    `plan` and `exec_` are populated for plan_only/exec_only/overlap as
    appropriate (plan_only has only plan; exec_only has only exec_;
    overlap has both).
    """
    kind: str
    plan: Optional[PlanAnalysis] = None
    exec_: Optional[ExecAnalysis] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "plan": self.plan.to_dict() if self.plan else None,
            "exec": self.exec_.to_dict() if self.exec_ else None,
        }


_OVERLAP_RATIO_THRESHOLD = 0.5


def _tokens_of(phrase: str) -> frozenset[str]:
    """Token set of a normalized phrase. Empty string → empty set."""
    return frozenset(phrase.split()) if phrase else frozenset()


def _overlap_ratio(p_tokens: frozenset[str], x_tokens: frozenset[str]) -> float:
    """Containment ratio: |intersection| / min(|p|, |x|).

    Why containment-over-min, not Jaccard: plan bullets are descriptive
    natural language (e.g. "Pearson correlation between dose and OD600
    endpoint, across the dose series.") while exec test_names are
    canonical and short ("Pearson correlation"). Jaccard punishes the
    plan side for adding context — the bare exec name being entirely
    contained in the plan's content tokens should still count as
    overlap. min-denominator avoids that. Returns 0.0 if either set is
    empty.
    """
    if not p_tokens or not x_tokens:
        return 0.0
    return len(p_tokens & x_tokens) / min(len(p_tokens), len(x_tokens))


def is_overlap_match(plan_phrase: str, exec_phrase: str) -> bool:
    """Symmetric containment match used by the deterministic pre-pass.

    Per SPEC §4.5 + Q1: pre-pass should be PERMISSIVE (over-flag rather
    than under-flag); the LLM filters. But not infinitely permissive —
    we don't want every bullet that contains "test" to overlap with
    every test name. Threshold ratio = 0.5 over the smaller side keeps
    a plausible exec subset of plan as a match while rejecting cases
    where only one common content word is shared.

    Public for testing.
    """
    return _overlap_ratio(_tokens_of(plan_phrase), _tokens_of(exec_phrase)) >= (
        _OVERLAP_RATIO_THRESHOLD
    )


def pre_pass(
    plan_items: Iterable[PlanAnalysis],
    exec_items: Iterable[ExecAnalysis],
) -> list[PrePassCandidate]:
    """Partition plan + exec items into plan_only / exec_only / overlap
    candidates using token-set containment over normalized phrases.

    Algorithm:
      - For each plan item p:
          collect overlapping execs X_p = { x | is_overlap_match(p, x) };
          if X_p non-empty, emit one `overlap` per (p, x) pair so the
          LLM can adjudicate each independently;
          else emit `plan_only`.
      - For each exec item x:
          if no plan item p satisfies is_overlap_match(p, x), emit
          `exec_only`.

    Empty normalized phrase (source text had no content words after
    stopword stripping) never matches anything — emitted as plan_only
    or exec_only so it stays visible to a human reviewer.
    """
    plan_items = list(plan_items)
    exec_items = list(exec_items)
    cands: list[PrePassCandidate] = []
    matched_exec_indices: set[int] = set()
    for p in plan_items:
        any_match = False
        for i, x in enumerate(exec_items):
            if is_overlap_match(p.normalized_phrase, x.normalized_phrase):
                cands.append(PrePassCandidate(kind="overlap", plan=p, exec_=x))
                matched_exec_indices.add(i)
                any_match = True
        if not any_match:
            cands.append(PrePassCandidate(kind="plan_only", plan=p))
    for i, x in enumerate(exec_items):
        if i not in matched_exec_indices:
            cands.append(PrePassCandidate(kind="exec_only", exec_=x))
    return cands


# ---------------------------------------------------------------------------
# Register emission (markdown)
# ---------------------------------------------------------------------------

@dataclass
class RegisterEntry:
    """One entry in the rendered discrepancy_register.md."""
    entry_id: str           # e.g. "D-001".
    type_: str              # "plan-prescribed-not-executed" | "executed-not-prescribed".
    plan_quote: str         # Verbatim plan bullet, or "—" if exec_only.
    plan_section: str       # Section heading the bullet came from, or "—".
    execution_citation: str # "notebook NB foo cell N line M" or "no notebook evidence".
    severity: str           # "load-bearing" | "cosmetic" | "unclear".
    recommendation: str     # 1-line prose; downstream consumed by Phase 2.

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def candidate_to_entry(
    cand: PrePassCandidate,
    entry_id: str,
) -> RegisterEntry:
    """Render a single pre-pass candidate as a register entry, WITHOUT
    LLM input. Used by the --no-llm path.

    We only call this for plan_only / exec_only candidates (overlap
    candidates need LLM adjudication and are skipped without --no-llm
    flipped on; this is enforced upstream in main()).
    """
    if cand.kind == "plan_only":
        assert cand.plan is not None  # invariant
        return RegisterEntry(
            entry_id=entry_id,
            type_="plan-prescribed-not-executed",
            plan_quote=cand.plan.plan_quote,
            plan_section=cand.plan.plan_section,
            execution_citation="no notebook evidence",
            severity="unclear",
            recommendation=(
                "Verify whether this analysis was performed; if so, locate "
                "the notebook+cell. If not, surface in Methods or move to "
                "Limitations / Future Work."
            ),
        )
    if cand.kind == "exec_only":
        assert cand.exec_ is not None
        x = cand.exec_
        return RegisterEntry(
            entry_id=entry_id,
            type_="executed-not-prescribed",
            plan_quote="—",
            plan_section="—",
            # SPEC §4.5 example: "notebook NB04 cell 18 applies
            # Benjamini-Hochberg FDR." — citation names the test.
            execution_citation=(
                f"notebook {x.notebook} cell {x.cell} line {x.line} applies "
                f"{x.test_name} (`{x.library_path}`)"
            ),
            severity="unclear",
            recommendation=(
                "Surface in Methods. If this changes a prespecified test, "
                "note in Limitations."
            ),
        )
    raise ValueError(
        f"candidate_to_entry: kind={cand.kind!r} is not directly emittable "
        f"without LLM adjudication. Pass it through A1.c first."
    )


def format_register_md(
    entries: list[RegisterEntry],
    overlap_skipped_count: int = 0,
    no_llm: bool = True,
) -> str:
    """Render the register as markdown per SPEC §4.5.

    Note: this is a regular function returning a joined string, not an
    f-string template. Per feedback_render_test_must_evaluate_fstring,
    unit tests should call this with synthetic entries to exercise the
    actual output (not just grep the source).
    """
    lines: list[str] = ["# Discrepancy Register", ""]

    if not entries:
        if no_llm and overlap_skipped_count > 0:
            lines.append(
                "_(No unambiguous plan-vs-execution discrepancies surfaced "
                f"by the deterministic pre-pass. {overlap_skipped_count} "
                "overlap candidate(s) skipped because `--no-llm` was set.)_"
            )
        else:
            lines.append(
                "_(No plan-vs-execution discrepancies surfaced by the "
                "deterministic pre-pass.)_"
            )
        lines.append("")
        return "\n".join(lines)

    for e in entries:
        lines.append(f"## {e.entry_id} — type: {e.type_}")
        if e.type_ == "plan-prescribed-not-executed":
            lines.append(f'- Plan §{e.plan_section}: "{e.plan_quote}"')
            lines.append(f"- Execution: {e.execution_citation}")
        elif e.type_ == "executed-not-prescribed":
            lines.append(f"- Plan: silent on this analysis.")
            lines.append(f"- Execution: {e.execution_citation}")
        else:
            # Defensive: SPEC §4.5 only defines two types; future extension
            # would land here.
            lines.append(f'- Plan §{e.plan_section}: "{e.plan_quote}"')
            lines.append(f"- Execution: {e.execution_citation}")
        lines.append(f"- Severity: {e.severity}")
        lines.append(f"- Recommendation: {e.recommendation}")
        lines.append("")

    if no_llm and overlap_skipped_count > 0:
        lines.append("---")
        lines.append("")
        lines.append(
            f"_Note: {overlap_skipped_count} candidate overlap pair(s) were "
            "skipped because `--no-llm` was set. Rerun without `--no-llm` "
            "to have the LLM (A1.c) adjudicate paraphrase-equivalent vs "
            "actual discrepancy._"
        )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lenient JSON load (pre-baked for A1.c reuse)
#
# Per feedback_llm_json_trailing_commas_repairable: trailing commas before
# `}` / `]` are unambiguous and worth a regex repair pass. Per
# feedback_llm_json_unfixable_in_parser: unescaped `"` inside strings is
# NOT repairable here — A1.c's prompt must include the anti-pattern rule.
# ---------------------------------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def lenient_json_load(text: str, *, source: str = "<json>") -> object:
    """Parse JSON; on JSONDecodeError, try one trailing-comma repair pass
    before re-raising the ORIGINAL error.

    Logs to stderr when the repair fires so future runs can track LLM
    JSON malformation frequency.
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
            # Repair didn't fix it; surface the ORIGINAL error.
            raise orig from None
        sys.stderr.write(
            f"  note: stripped trailing comma(s) from {source} "
            f"(LLM JSON malformation; original error at line "
            f"{orig.lineno} col {orig.colno})\n"
        )
        return data


# ---------------------------------------------------------------------------
# Audit JSONL emission
# ---------------------------------------------------------------------------

def _sha256_of_path(p: Path) -> str:
    """Return hex SHA-256 of file contents. Reads in chunks for safety
    against accidentally-large inputs (RESEARCH_PLAN.md is usually
    small, but no point assuming)."""
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
    methods_provenance_path: Path,
    research_plan_path: Path,
    output_path: Path,
    entry_count: int,
    cost_usd: float,
    exit_status: int,
) -> None:
    """Append one JSONL audit line to <output-dir>/audit/phase0.jsonl."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    # SHA-256 only over inputs that actually exist. If an input path
    # was missing, `exit_status` is already 2; the audit line records
    # the absence as a None hash so downstream consumers can tell.
    def _safe_sha(p: Path) -> Optional[str]:
        if p.is_file():
            return _sha256_of_path(p)
        return None

    line = {
        "timestamp": _utc_now_iso(),
        "tool": "discrepancy_register",
        "version": VERSION,
        "inputs": {
            "methods_provenance": _safe_sha(methods_provenance_path),
            "research_plan": _safe_sha(research_plan_path),
        },
        "output_path": str(output_path),
        "entry_count": entry_count,
        "cost_usd": cost_usd,
        "exit_status": exit_status,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# LLM seam (A1.c — not implemented in this milestone)
# ---------------------------------------------------------------------------

class LLMNotImplemented(RuntimeError):
    """Raised when --no-llm is NOT set but A1.c is still pending.
    Caller should map to exit code 3 per the punch list."""


def classify_overlap_candidates_with_llm(
    overlap_candidates: list[PrePassCandidate],
    *,
    model: str = "claude-haiku-4-5-20251001",
    prompt_path: Optional[Path] = None,
) -> list[RegisterEntry]:
    """A1.c entry point — NOT IMPLEMENTED in this milestone.

    Wired in a separate conversation. The contract:
      Input: a list of overlap candidates from pre_pass().
      Output: a list of RegisterEntry — only those classified as
              `discrepancy` (equivalent + paraphrase pairs are dropped
              upstream, never become register entries).

    Per feedback_llm_json_unfixable_in_parser: the prompt at
    prompts/discrepancy_classify.v1.md MUST include explicit
    anti-pattern rules for unescaped quotes inside string values.
    Per feedback_llm_json_trailing_commas_repairable: the response
    parse should call lenient_json_load on the LLM output.
    """
    raise LLMNotImplemented(
        "A1.c (LLM classifier for overlap candidates) is not yet "
        "implemented in this milestone. Pass --no-llm to run the "
        "deterministic pre-pass only, or wait for the next milestone."
    )


# ---------------------------------------------------------------------------
# Top-level orchestration (used by main() and importable from tests)
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    entries: list[RegisterEntry]
    overlap_count: int
    cost_usd: float


def run_register(
    *,
    plan_text: str,
    provenance_text: str,
    no_llm: bool,
) -> RunResult:
    """Pure-function orchestration: parse + pre-pass + (LLM if requested)
    + assemble register entries with auto-numbered D-NNN ids.

    Doesn't touch disk; doesn't emit audit. main() handles those so this
    function is straightforward to unit-test.
    """
    plan_items = parse_plan_analyses(plan_text)
    exec_items = parse_provenance_executions(provenance_text)
    cands = pre_pass(plan_items, exec_items)

    plan_only = [c for c in cands if c.kind == "plan_only"]
    exec_only = [c for c in cands if c.kind == "exec_only"]
    overlap = [c for c in cands if c.kind == "overlap"]

    entries: list[RegisterEntry] = []
    next_id = 1

    def _next() -> str:
        nonlocal next_id
        s = f"D-{next_id:03d}"
        next_id += 1
        return s

    # Emit unambiguous discrepancies (plan_only + exec_only) deterministically.
    # Stable ordering: plan_only first (in source order), then exec_only.
    for c in plan_only:
        entries.append(candidate_to_entry(c, _next()))
    for c in exec_only:
        entries.append(candidate_to_entry(c, _next()))

    cost_usd = 0.0
    if not no_llm:
        # A1.c lives here. Currently raises LLMNotImplemented.
        llm_entries = classify_overlap_candidates_with_llm(overlap)
        # When implemented: `cost_usd = <observed>` and we splice the
        # LLM-classified entries into the register, also assigning fresh
        # D-NNN ids continuing from `next_id`.
        for e in llm_entries:
            e.entry_id = _next()
            entries.append(e)

    return RunResult(
        entries=entries,
        overlap_count=len(overlap),
        cost_usd=cost_usd,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="discrepancy_register.py",
        description=(
            "Plan-vs-execution diff scanner (paper-writer v0.8 Phase 0). "
            "Walks RESEARCH_PLAN.md and methods_provenance.md, surfaces "
            "discrepancies as discrepancy_register.md, appends an audit "
            "JSONL line to <output-dir>/audit/phase0.jsonl. See "
            "SPEC_v0_8 §4.5 + DECISIONS.md D-034 Q1."
        ),
    )
    p.add_argument(
        "--methods-provenance",
        type=Path,
        required=True,
        help="Path to methods_provenance.md (emitted by extract_methods.py).",
    )
    p.add_argument(
        "--research-plan",
        type=Path,
        required=True,
        help="Path to the project's RESEARCH_PLAN.md.",
    )
    p.add_argument(
        "--reframing-log",
        type=Path,
        default=None,
        help=(
            "Optional path to a prior reframing_log.md. Currently only "
            "passed through; not load-bearing for the M1 deterministic "
            "path."
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Directory under which to write discrepancy_register.md and "
            "audit/phase0.jsonl. Created if missing."
        ),
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Skip the LLM overlap-classifier (A1.c). Emits only the "
            "deterministic plan_only + exec_only discrepancies. Used by "
            "the cost-justification ablation in the C1 smoke."
        ),
    )
    args = p.parse_args(argv)

    methods_path: Path = args.methods_provenance
    plan_path: Path = args.research_plan
    out_dir: Path = args.output_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / "audit" / "phase0.jsonl"
    output_path = out_dir / "discrepancy_register.md"

    # Input parse error handling (exit 2). Both required files must exist
    # AND have content. Empty file → emit empty register (exit 0); missing
    # file → exit 2.
    for label, p_in in (
        ("methods_provenance", methods_path),
        ("research_plan", plan_path),
    ):
        if not p_in.is_file():
            print(
                f"error: {label} input not found: {p_in}",
                file=sys.stderr,
            )
            emit_audit_line(
                audit_path=audit_path,
                methods_provenance_path=methods_path,
                research_plan_path=plan_path,
                output_path=output_path,
                entry_count=0,
                cost_usd=0.0,
                exit_status=2,
            )
            return 2

    try:
        plan_text = plan_path.read_text(encoding="utf-8")
        provenance_text = methods_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read input file: {e}", file=sys.stderr)
        emit_audit_line(
            audit_path=audit_path,
            methods_provenance_path=methods_path,
            research_plan_path=plan_path,
            output_path=output_path,
            entry_count=0,
            cost_usd=0.0,
            exit_status=2,
        )
        return 2

    try:
        result = run_register(
            plan_text=plan_text,
            provenance_text=provenance_text,
            no_llm=args.no_llm,
        )
    except LLMNotImplemented as e:
        print(f"error: {e}", file=sys.stderr)
        emit_audit_line(
            audit_path=audit_path,
            methods_provenance_path=methods_path,
            research_plan_path=plan_path,
            output_path=output_path,
            entry_count=0,
            cost_usd=0.0,
            exit_status=3,
        )
        return 3

    md = format_register_md(
        entries=result.entries,
        overlap_skipped_count=result.overlap_count,
        no_llm=args.no_llm,
    )
    output_path.write_text(md, encoding="utf-8")

    emit_audit_line(
        audit_path=audit_path,
        methods_provenance_path=methods_path,
        research_plan_path=plan_path,
        output_path=output_path,
        entry_count=len(result.entries),
        cost_usd=result.cost_usd,
        exit_status=0,
    )

    print(
        f"Wrote {output_path} ({len(result.entries)} entries"
        + (f"; {result.overlap_count} overlap(s) skipped" if args.no_llm else "")
        + ")",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
