#!/usr/bin/env python3
"""discrepancy_register.py — Plan-vs-execution diff scanner (Phase 0, v0.8).

STATUS (Stage 1 Tier E, 2026-05-11): **M1-deferred path; not currently
called by orchestrator.py.** The active pipeline produces
`discrepancy_register.md` (and a parallel `audit_discrepancies.json`)
via `claude -p` with prompts/audit_discrepancies.v1.md (LLM-only).
This module's regex pre-pass + LLM classifier + validator
(A1.abcd shipped) is preserved as a fallback CLI path and as test
coverage. Decision per STAGED_IMPROVEMENT_PLAN.md Stage 1 Tier E:
keep, don't delete, don't invest further until a consumer surfaces.

Per SPEC §4.5 + DECISIONS.md D-034 Q1:

  Surfaces every place where RESEARCH_PLAN.md prescribed an analysis the
  notebooks did not execute, OR the notebooks executed an analysis the
  plan did not prescribe. Lifts upstream what v0.7.x's reframer.v1 prompt
  did post-hoc in prose, so the holistic write (Phase 2) sees discrepancies
  *before* it drafts.

Pipeline (all four sub-tiers shipped as A1.abcd):

  1. Deterministic pre-pass (A1.a + A1.b):
       - Parse RESEARCH_PLAN.md for analysis declarations under headings
         matching /analys[ei]s|method|test|stat/i (bullets / numbered list).
       - Parse methods_provenance.md (already a structured artifact emitted
         by extract_methods.py) for executed analyses with notebook+cell.
       - Normalize each phrase (lowercase + stopword removal + light Porter
         stem), then partition into plan_only / exec_only / overlap.

  2. LLM classification pass (A1.c):
       - Haiku-4.5 over `overlap` candidates only, deciding
         {equivalent | paraphrase | discrepancy}. Equivalent + paraphrase
         pairs do NOT become register entries; discrepancy pairs do.
       - Cost ceiling $0.05/run (SPEC §4.5; soft warning, not a hard halt).
       - LLM seam at `classifier_llm_call`; tests monkeypatch.

  3. Validator + idempotency cache (A1.d):
       - Validator: ascending candidate_index with no gaps; label ∈
         {equivalent, paraphrase, discrepancy}; severity ∈ {load-bearing,
         cosmetic, unclear}; severity_justification non-empty;
         plan_quote_verbatim non-empty AND substring of input plan_quote;
         exec_quote_verbatim non-empty AND substring of the concatenated
         exec text. The empty-string non-emptiness checks landed
         2026-05-07 alongside the prompt-tool contract clarification
         (the user prompt sends exec_test_name/exec_library/exec_notebook
         as separate fields; the substring check is over their
         concatenation `<test_name> | <library> | <notebook> cell <N> line <M>`).
       - Cache: SHA-256 over four-tuple (methods_sha, plan_sha,
         prompt_sha, parser_VERSION). Cache hit re-validates against the
         current inputs (defensive against hand-edits); on validation
         failure, falls through to a fresh LLM call.

I/O contract (this milestone):

  --methods-provenance <path>    required; markdown
  --research-plan <path>         required; markdown
  --reframing-log <path>         optional; passed through, not load-bearing
  --output-dir <path>            required; writes:
                                   <output-dir>/discrepancy_register.md
                                   <output-dir>/audit/phase0.jsonl  (append)
  --no-llm                       debug; skip A1.c. Emits only the
                                 deterministic plan_only + exec_only
                                 candidates; overlap pairs are skipped
                                 with a footer note. Used by the C1.b
                                 cost-justification ablation.

Exit codes:
  0 — success (register file written; entry count may be zero).
  1 — usage error (--help, missing required flag).
  2 — input parse error (required file missing or empty).
  3 — LLM call failure (subprocess crash, JSON unparseable after lenient
      repair, transport error). With --no-llm set, the LLM seam is never
      reached and exit 3 is impossible.
  4 — validator rejection of LLM output: out-of-enum label/severity, or
      a quote field that is not a substring of the input candidate's
      content. The LLM was called and billed; the audit JSONL line
      records the cost. Re-run after re-reading the prompt; if the
      validator keeps rejecting, the prompt needs a contract update
      (bump prompt version → cache invalidates).

Audit JSONL line schema (one line per invocation, appended to
<output-dir>/audit/phase0.jsonl):

  {
    "timestamp": "2026-05-07T14:23:01Z",
    "tool": "discrepancy_register",
    "version": "0.8.0-m1-A1.abcd",
    "inputs": {
      "methods_provenance": "<sha256>",
      "research_plan": "<sha256>"
    },
    "output_path": "<absolute path to discrepancy_register.md>",
    "entry_count": 3,
    "cost_usd": 0.0123,
    "cache_hit": false,
    "exit_status": 0
  }

Idempotency cache:

  <output-dir>/audit/discrepancy_cache.json — JSON map of
  cache_key (hex SHA-256 over (methods_provenance_sha, research_plan_sha,
  prompt_sha, parser_VERSION)) → cached classifications. On hit, the
  LLM is skipped and the cached output is re-emitted byte-identical; the
  audit JSONL line still appends with `cache_hit: true` so reruns stay
  observable (per SPEC §4.7). Any input SHA change OR prompt-file change
  OR parser_VERSION bump invalidates the cache.

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
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional


# Module version. Distinct from package version because audit consumers
# may want to track precisely which sub-milestone wrote a given line.
# Bump on contract-affecting changes (new audit fields, schema changes).
# A1.a + A1.b shipped as "0.8.0-m1-A1.ab"; A1.c + A1.d added in a
# subsequent commit; this version label corrects a stale-doc lag noted
# 2026-05-07 (the file already contained the c+d functions but the
# label still read ".cd"). Per claim_inventory.py's convention, the
# suffix tracks the FULL set shipped, not the latest delta — hence
# .abcd, not .cd.
VERSION = "0.8.0-m1-A1.abcd-B1.e"


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
      - overlap: both sides match on normalized phrase. The A1.c LLM
        step decides whether such pairs are equivalent / paraphrase /
        discrepancy. Without LLM (--no-llm flag), we skip emission and
        report the count.

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
    cache_hit: bool = False,
) -> None:
    """Append one JSONL audit line to <output-dir>/audit/phase0.jsonl.

    `cache_hit=True` records that the LLM call was skipped because the
    idempotency cache had a hit on the (methods_sha, plan_sha,
    prompt_sha, parser_VERSION) key. The append still happens — every
    invocation is observable per SPEC §4.7. Cost on a hit is 0.0 (no
    LLM bill); we don't re-charge the original cached call to the
    rerun.
    """
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
        "cache_hit": cache_hit,
        "exit_status": exit_status,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# LLM classifier seam (A1.c) + validator (A1.d)
# ---------------------------------------------------------------------------

# Resolve the prompt path relative to this module so the runtime SHA is
# stable across invocations. The prompt SHA is one of the four cache-key
# components — bumping the prompt invalidates cache entries automatically.
_MODULE_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _MODULE_DIR.parent
_PROMPT_PATH = _SKILL_DIR / "prompts" / "discrepancy_classify.v1.md"


# Enums the validator enforces. Out-of-enum values land an exit-4
# rejection with a structured error.
_VALID_LABELS: frozenset[str] = frozenset({"equivalent", "paraphrase", "discrepancy"})
_VALID_SEVERITIES: frozenset[str] = frozenset({"load-bearing", "cosmetic", "unclear"})


# Cost ceiling per SPEC §4.5. Soft warning, not a hard halt — the audit
# line records the actual spend, and the orchestrator can decide to
# escalate. Hard halts belong in the bash-level cost circuit-breaker
# (paper_writer.sh's MAX_COST_USD), not in this individual tool.
_COST_CEILING_USD = 0.05


# CRAFT-CONTRACT §3.4 / Round 2b: classification → fast tier. The literal
# string here is the Claude Code `--model` ALIAS (not a concrete model id);
# Claude Code resolves it via ANTHROPIC_DEFAULT_HAIKU_MODEL in
# <BERIL_ROOT>/.claude/settings.json (written by `beril-paper-writer
# configure`). Kept as a literal — rather than `llm_config.pick_tier("fast")`
# — so the script can be invoked standalone (`python discrepancy_register.py
# --help`) without the parent package on sys.path. Canonical source of
# truth: `beril_paper_writer.llm_config.TIER_FAMILY["fast"]`; if the family
# alias ever changes, update both call-sites. Public for CLI override and
# test monkeypatch. SPEC §4.5's cost target (~Haiku 4.5) is enforced by the
# tier alias rather than a hardcoded model id.
DEFAULT_CLASSIFIER_MODEL = "haiku"


class LLMCallError(RuntimeError):
    """Subprocess crashed, JSON unparseable, or the response body was
    empty / not a JSON array. Caller maps to exit code 3."""


class ValidationError(RuntimeError):
    """The LLM produced syntactically-valid JSON but the content
    violated the schema (out-of-enum value, non-substring quote, wrong
    array length, gap in candidate_index). Caller maps to exit code 4.

    Carries a `.diagnostics` dict so the audit line and stderr message
    can name the specific candidate and field that failed.
    """
    def __init__(self, message: str, diagnostics: Optional[dict] = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass
class ClassificationEntry:
    """One LLM-classified row — pre-validation."""
    candidate_index: int
    label: str           # equivalent | paraphrase | discrepancy
    severity: str        # load-bearing | cosmetic | unclear
    severity_justification: str
    plan_quote_verbatim: str
    exec_quote_verbatim: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Subprocess wrapper around `claude -p`. Public so tests can monkeypatch.
# ---------------------------------------------------------------------------

# Type alias for the LLM-call seam — system prompt, user prompt, model →
# (response_text, cost_usd). Tests replace this with a canned-response
# fake; the real implementation calls `claude -p --output-format json`.
ClassifierLLMCall = Callable[[str, str, str], tuple[str, float]]


def _invoke_classifier_llm_subprocess(
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> tuple[str, float]:
    """Default LLM seam: subprocess to `claude -p`. Returns
    (response_text, cost_usd).

    Uses `--output-format json` so we can capture cost from the envelope's
    `total_cost_usd` field rather than parse a stream-json event log.
    The CLI is invoked with `CLAUDECODE=` to detach from any inherited
    Claude Code session env (matches the convention in paper_writer.sh
    and adversarial_review.sh).

    No `--allowedTools` grant — this prompt produces inline JSON, not a
    file write; the LLM has no need for filesystem tools and granting
    them invites a stochastic Write-tool detour.

    Raises LLMCallError on subprocess failure or unparseable envelope.
    """
    if shutil.which("claude") is None:
        raise LLMCallError(
            "'claude' CLI not found on PATH; cannot invoke LLM "
            "classifier. Pass --no-llm for the deterministic-only path."
        )

    # CLAUDECODE= prefix matches the existing convention; ensures we
    # don't inherit a parent claude-code session that would change
    # invocation semantics.
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

    # Envelope shape (claude -p --output-format json):
    #   {"type": "result", "subtype": "success", "result": "<text>",
    #    "total_cost_usd": 0.0123, "usage": {...}, ...}
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

    # Cost may be absent on some CLI versions; default to 0.0 with a
    # stderr note rather than fail. Better to ship without cost-tracking
    # than to fail a smoke for an envelope-field rename.
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
classifier_llm_call: ClassifierLLMCall = _invoke_classifier_llm_subprocess


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def _candidate_exec_text(c: PrePassCandidate) -> str:
    """The `exec_quote` we substring-check `exec_quote_verbatim` against.

    The prompt encourages the LLM to copy the test name verbatim, but
    we permit it to also copy the library path or notebook citation —
    any of those should pass the validator's substring check, so we
    concatenate them all into the canonical exec_quote text per
    candidate.
    """
    if c.exec_ is None:
        return ""
    x = c.exec_
    return f"{x.test_name} | {x.library_path} | {x.notebook} cell {x.cell} line {x.line}"


def build_classifier_user_prompt(overlap_candidates: list[PrePassCandidate]) -> str:
    """Render the user-prompt half of the LLM call: a list of N
    candidate pairs, each with plan and exec sides, in candidate_index
    order.

    Public so tests can pin the wire format.
    """
    n = len(overlap_candidates)
    lines: list[str] = [
        f"You will classify N={n} candidate plan-vs-execution pairs.",
        "",
        "For each candidate, decide: equivalent / paraphrase / discrepancy.",
        "Quote verbatim from the candidate's plan_quote and exec content",
        "fields when populating plan_quote_verbatim and exec_quote_verbatim.",
        "",
        "Return a JSON array of exactly N entries, in candidate_index order,",
        "conforming to the schema in your system prompt. The first character",
        "of your response must be `[` and the last `]`. No prose, no fences.",
        "",
        "CANDIDATES:",
        "",
    ]
    for i, c in enumerate(overlap_candidates):
        if c.plan is None or c.exec_ is None:
            # Defensive — overlap candidates always have both sides; if
            # this fires it's an upstream bug.
            raise ValueError(
                f"overlap candidate {i} missing plan or exec side: {c.to_dict()}"
            )
        lines.append(f"[{i}] plan_section: {c.plan.plan_section!r}")
        lines.append(f"    plan_quote: {c.plan.plan_quote!r}")
        lines.append(f"    exec_test_name: {c.exec_.test_name!r}")
        lines.append(f"    exec_library: {c.exec_.library_path!r}")
        lines.append(
            f"    exec_notebook: {c.exec_.notebook!r} "
            f"cell {c.exec_.cell} line {c.exec_.line}"
        )
        lines.append("")
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
        # Drop first line through to the closing fence.
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_classifier_response(
    response_text: str,
    *,
    expected_count: int,
) -> list[ClassificationEntry]:
    """Parse the LLM's stdout response into ClassificationEntry list.

    Raises LLMCallError on JSON-level failures (unparseable after
    lenient repair, wrong top-level type, wrong length). Schema-level
    failures (out-of-enum, non-substring quotes) come later in
    validate_classifications and raise ValidationError instead — the
    distinction matters because LLMCallError → exit 3 (LLM call retry
    is the right response) while ValidationError → exit 4 (LLM was
    paid; the prompt or candidate set may need work).
    """
    cleaned = _strip_code_fences(response_text)
    try:
        data = lenient_json_load(cleaned, source="<classifier-response>")
    except json.JSONDecodeError as e:
        raise LLMCallError(
            f"classifier response was not valid JSON after lenient repair: "
            f"{e.msg} (line {e.lineno} col {e.colno})"
        ) from e

    if not isinstance(data, list):
        raise LLMCallError(
            f"classifier response top-level type was {type(data).__name__}, "
            f"expected list"
        )

    if len(data) != expected_count:
        raise LLMCallError(
            f"classifier response length {len(data)} != "
            f"expected {expected_count} candidates"
        )

    entries: list[ClassificationEntry] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise LLMCallError(
                f"classifier entry {i} was {type(item).__name__}, "
                f"expected object"
            )
        # Required-field presence check is at LLMCallError tier (the
        # response is structurally broken); content correctness is at
        # ValidationError tier later.
        try:
            entries.append(ClassificationEntry(
                candidate_index=int(item["candidate_index"]),
                label=str(item["label"]),
                severity=str(item["severity"]),
                severity_justification=str(item.get("severity_justification", "")),
                plan_quote_verbatim=str(item.get("plan_quote_verbatim", "")),
                exec_quote_verbatim=str(item.get("exec_quote_verbatim", "")),
            ))
        except KeyError as e:
            raise LLMCallError(
                f"classifier entry {i} missing required field: {e.args[0]}"
            ) from e
        except (TypeError, ValueError) as e:
            raise LLMCallError(
                f"classifier entry {i} had a field of the wrong type: {e}"
            ) from e

    return entries


# ---------------------------------------------------------------------------
# Validator (A1.d)
# ---------------------------------------------------------------------------

def validate_classifications(
    classifications: list[ClassificationEntry],
    overlap_candidates: list[PrePassCandidate],
) -> None:
    """Reject schema-violating entries with structured ValidationError.

    Checks (each → exit 4 with diagnostics):
      - candidate_index in [0, N) and matches array order with no gaps
        / duplicates.
      - label ∈ {equivalent, paraphrase, discrepancy}.
      - severity ∈ {load-bearing, cosmetic, unclear}.
      - plan_quote_verbatim is a substring of the input candidate's
        plan_quote (anti-fabrication).
      - exec_quote_verbatim is a substring of the input candidate's
        exec content (anti-fabrication).

    Pass-through is a no-op return; the caller continues with the
    validated entries.
    """
    n = len(overlap_candidates)
    if len(classifications) != n:
        raise ValidationError(
            f"validator: classification count {len(classifications)} != "
            f"candidate count {n}",
            {"expected": n, "got": len(classifications)},
        )

    seen_indices: set[int] = set()
    for pos, ce in enumerate(classifications):
        # Order + uniqueness + bounds.
        if ce.candidate_index != pos:
            raise ValidationError(
                f"validator: classification at position {pos} has "
                f"candidate_index={ce.candidate_index}; expected {pos} "
                f"(entries must be in ascending candidate_index order, "
                f"no gaps, no duplicates)",
                {"position": pos, "candidate_index": ce.candidate_index},
            )
        if ce.candidate_index in seen_indices:
            raise ValidationError(
                f"validator: duplicate candidate_index={ce.candidate_index}",
                {"candidate_index": ce.candidate_index},
            )
        if not (0 <= ce.candidate_index < n):
            raise ValidationError(
                f"validator: candidate_index={ce.candidate_index} out of "
                f"bounds [0, {n})",
                {"candidate_index": ce.candidate_index, "bound": n},
            )
        seen_indices.add(ce.candidate_index)

        # Enum checks.
        if ce.label not in _VALID_LABELS:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index} has out-of-enum "
                f"label={ce.label!r}; allowed: {sorted(_VALID_LABELS)}",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "label",
                    "value": ce.label,
                    "allowed": sorted(_VALID_LABELS),
                },
            )
        if ce.severity not in _VALID_SEVERITIES:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index} has out-of-enum "
                f"severity={ce.severity!r}; allowed: {sorted(_VALID_SEVERITIES)}",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "severity",
                    "value": ce.severity,
                    "allowed": sorted(_VALID_SEVERITIES),
                },
            )

        # Anti-fabrication: non-empty + substring checks against the
        # input candidate. The non-empty checks tighten the original
        # truthiness-gated guards (which silently accepted empty strings,
        # leaving the substring rule unenforced — asymmetric with
        # claim_inventory.py's discipline at its validator's
        # `if not e.claim_text` line).
        #
        # severity_justification is checked across ALL labels because
        # equivalent + paraphrase rows ARE persisted in the cache for
        # traceability (a reviewer wanting to know why the LLM dropped a
        # pair benefits from a grounded justification). For discrepancy
        # rows it is also load-bearing because classification_to_register_entry
        # interpolates it directly into the recommendation prose; an
        # empty string emits malformed output ("Reconcile in Methods: . ...").
        cand = overlap_candidates[ce.candidate_index]
        plan_text = cand.plan.plan_quote if cand.plan else ""
        exec_text = _candidate_exec_text(cand)

        if not ce.severity_justification:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index} has empty "
                f"severity_justification (prompt requires non-empty; "
                f"interpolated into recommendation prose for "
                f"discrepancy-labeled rows + retained in cache for "
                f"traceability of equivalent/paraphrase rows)",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "severity_justification",
                },
            )

        if not ce.plan_quote_verbatim:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index} has empty "
                f"plan_quote_verbatim (the verbatim quote is the "
                f"anti-fabrication anchor; prompt §'Field rules' requires "
                f"non-empty)",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "plan_quote_verbatim",
                },
            )
        if ce.plan_quote_verbatim not in plan_text:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index}'s "
                f"plan_quote_verbatim is not a substring of the input "
                f"candidate's plan_quote (LLM may have paraphrased or "
                f"fabricated). Got: {ce.plan_quote_verbatim!r}; expected "
                f"a substring of: {plan_text!r}",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "plan_quote_verbatim",
                    "value": ce.plan_quote_verbatim,
                    "input": plan_text,
                },
            )

        if not ce.exec_quote_verbatim:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index} has empty "
                f"exec_quote_verbatim (the verbatim quote is the "
                f"anti-fabrication anchor; prompt §'Field rules' requires "
                f"non-empty)",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "exec_quote_verbatim",
                },
            )
        if ce.exec_quote_verbatim not in exec_text:
            raise ValidationError(
                f"validator: candidate {ce.candidate_index}'s "
                f"exec_quote_verbatim is not a substring of the input "
                f"candidate's exec content. Got: {ce.exec_quote_verbatim!r}; "
                f"expected a substring of: {exec_text!r}",
                {
                    "candidate_index": ce.candidate_index,
                    "field": "exec_quote_verbatim",
                    "value": ce.exec_quote_verbatim,
                    "input": exec_text,
                },
            )


# ---------------------------------------------------------------------------
# Cache (A1.d idempotency)
# ---------------------------------------------------------------------------

def compute_cache_key(
    *,
    methods_provenance_sha: str,
    research_plan_sha: str,
    prompt_sha: str,
    parser_version: str,
) -> str:
    """SHA-256 over the four-tuple. parser_version inclusion follows
    feedback_cache_key_chunked_only_when_chunked: it's the safety net
    against silently-invisible parser fixes (e.g., the multi-line
    bullet fold from A1.b).
    """
    h = hashlib.sha256()
    payload = json.dumps(
        {
            "methods_provenance_sha": methods_provenance_sha,
            "research_plan_sha": research_plan_sha,
            "prompt_sha": prompt_sha,
            "parser_version": parser_version,
        },
        sort_keys=True,
    )
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
    # Atomic-ish: write to .tmp, then rename. Cache files are small and
    # rarely contended; this is cheap insurance.
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(cache, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(cache_path)


def _cached_payload_to_classifications(
    payload: dict,
) -> list[ClassificationEntry]:
    """Reconstruct ClassificationEntry list from a cached dict."""
    rows = payload.get("classifications", [])
    return [
        ClassificationEntry(
            candidate_index=int(r["candidate_index"]),
            label=str(r["label"]),
            severity=str(r["severity"]),
            severity_justification=str(r.get("severity_justification", "")),
            plan_quote_verbatim=str(r.get("plan_quote_verbatim", "")),
            exec_quote_verbatim=str(r.get("exec_quote_verbatim", "")),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Top-level LLM seam (replaces the M1 stub)
# ---------------------------------------------------------------------------

def classification_to_register_entry(
    ce: ClassificationEntry,
    cand: PrePassCandidate,
    entry_id: str,
) -> RegisterEntry:
    """Translate a `discrepancy`-labeled classification + its source
    candidate into a register entry. Equivalent / paraphrase entries
    are dropped UPSTREAM; this function is only called for
    `label == 'discrepancy'`.

    Type framing per SPEC §4.5: the plan prescribed something (the
    candidate has a plan side) and the execution did something
    different (the candidate has an exec side that diverges). Type =
    `plan-prescribed-not-executed` — the canonical SPEC §4.5 D-001
    framing.
    """
    assert cand.plan is not None and cand.exec_ is not None  # invariant
    x = cand.exec_
    return RegisterEntry(
        entry_id=entry_id,
        type_="plan-prescribed-not-executed",
        plan_quote=cand.plan.plan_quote,
        plan_section=cand.plan.plan_section,
        execution_citation=(
            f"notebook {x.notebook} cell {x.cell} line {x.line} applies "
            f"{x.test_name} (`{x.library_path}`)"
        ),
        severity=ce.severity,
        recommendation=(
            f"Reconcile in Methods: {ce.severity_justification.rstrip('.')}.  "
            f"Update the Hypothesis / Limitations framing if the chosen test "
            f"changes the claim's footing."
        ),
    )


def classify_overlap_candidates_with_llm(
    overlap_candidates: list[PrePassCandidate],
    *,
    model: str = DEFAULT_CLASSIFIER_MODEL,
    prompt_path: Optional[Path] = None,
    llm_call: Optional[ClassifierLLMCall] = None,
) -> tuple[list[ClassificationEntry], float]:
    """A1.c entry point. Classifies overlap candidates via the LLM seam
    and returns (validated_classifications, cost_usd).

    Equivalent + paraphrase + discrepancy entries are ALL returned;
    upstream `run_register` filters to discrepancy when emitting the
    register. Returning all three lets callers (and the cache) preserve
    the full adjudication context across reruns.

    `llm_call` defaults to the module-level `classifier_llm_call`
    seam (the subprocess wrapper). Tests pass a fake.

    Raises:
      LLMCallError on subprocess / JSON-shape failures (exit 3).
      ValidationError on schema violations (exit 4).

    The empty-overlap shortcut returns ([], 0.0) without calling the
    LLM — saves cost on projects whose deterministic pre-pass already
    surfaced everything.
    """
    if not overlap_candidates:
        return [], 0.0

    prompt_path = prompt_path or _PROMPT_PATH
    if not prompt_path.is_file():
        raise LLMCallError(
            f"discrepancy_classify prompt not found at {prompt_path}; "
            f"the skill installation is incomplete"
        )

    system_prompt = prompt_path.read_text(encoding="utf-8")
    user_prompt = build_classifier_user_prompt(overlap_candidates)

    call = llm_call or classifier_llm_call
    response_text, cost_usd = call(system_prompt, user_prompt, model)

    # B1.e cost-cap reframing (2026-05-07): the per-call ceiling is
    # tracked in the audit JSONL but no longer triggers a stderr
    # warning. Per Adam's directive, observability over enforcement
    # during M1; ceilings will be set from observed data later.
    # `_COST_CEILING_USD` remains as a documentation-only constant.

    classifications = parse_classifier_response(
        response_text, expected_count=len(overlap_candidates),
    )
    try:
        validate_classifications(classifications, overlap_candidates)
    except ValidationError as e:
        # B1.e: reattach billed cost so main()'s exit-4 audit line is
        # honest about LLM spend even on rejection.
        e.cost_usd = cost_usd  # type: ignore[attr-defined]
        raise

    return classifications, cost_usd


# ---------------------------------------------------------------------------
# Top-level orchestration (used by main() and importable from tests)
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    entries: list[RegisterEntry]
    overlap_count: int
    cost_usd: float
    cache_hit: bool = False
    # Full classifications kept on the result so the cache writer + tests
    # can introspect equivalent/paraphrase calls that didn't become
    # register entries.
    classifications: list[ClassificationEntry] = field(default_factory=list)


def run_register(
    *,
    plan_text: str,
    provenance_text: str,
    no_llm: bool,
    llm_call: Optional[ClassifierLLMCall] = None,
    cached_classifications: Optional[list[ClassificationEntry]] = None,
    cached_cost_usd: Optional[float] = None,
) -> RunResult:
    """Pure-function orchestration: parse + pre-pass + (LLM if requested)
    + assemble register entries with auto-numbered D-NNN ids.

    Doesn't touch disk; doesn't emit audit. main() handles those so this
    function is straightforward to unit-test.

    `llm_call` overrides the module-level subprocess seam — used by
    tests to inject canned classifications.

    `cached_classifications` short-circuits the LLM call when supplied
    (the cache layer in main() does this on a cache hit). `cached_cost_usd`
    is the cost from the original (cached) call; on a hit the audit
    line records cost_usd=0.0 (no fresh LLM bill) but the cached
    classifications still produce identical register entries.
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
    classifications: list[ClassificationEntry] = []
    cache_hit = False

    if not no_llm and overlap:
        if cached_classifications is not None:
            # Cache-hit path: skip LLM, re-validate cached entries
            # (defensive — the cache file could have been hand-edited
            # between runs; if validation fails we rebuild rather than
            # ship a stale entry).
            try:
                validate_classifications(cached_classifications, overlap)
                classifications = cached_classifications
                cache_hit = True
                cost_usd = 0.0  # no fresh LLM bill on a hit
            except ValidationError:
                # Cache poisoned somehow — fall through to a fresh LLM
                # call. Don't crash the run on a hand-edited cache.
                sys.stderr.write(
                    "  note: discrepancy_cache.json failed re-validation; "
                    "falling through to a fresh LLM call\n"
                )
                classifications, cost_usd = classify_overlap_candidates_with_llm(
                    overlap, llm_call=llm_call,
                )
        else:
            classifications, cost_usd = classify_overlap_candidates_with_llm(
                overlap, llm_call=llm_call,
            )

        # Splice `discrepancy`-labeled classifications into the register
        # in candidate_index order. equivalent + paraphrase entries are
        # carried in `classifications` for cache fidelity but do NOT
        # become register rows in v0.8.0 (per SPEC §4.5).
        for ce in classifications:
            if ce.label == "discrepancy":
                cand = overlap[ce.candidate_index]
                entries.append(
                    classification_to_register_entry(ce, cand, _next())
                )

    return RunResult(
        entries=entries,
        overlap_count=len(overlap),
        cost_usd=cost_usd,
        cache_hit=cache_hit,
        classifications=classifications,
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
            "SPEC §4.5 + DECISIONS.md D-034 Q1."
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

    # Idempotency cache lookup. Skipped when --no-llm is set (no LLM
    # call to cache) or when the prompt file is missing (we can't compute
    # a stable cache key without it).
    cache_path = out_dir / "audit" / "discrepancy_cache.json"
    cache_key: Optional[str] = None
    cached_classifications: Optional[list[ClassificationEntry]] = None

    if not args.no_llm and _PROMPT_PATH.is_file():
        cache_key = compute_cache_key(
            methods_provenance_sha=_sha256_of_path(methods_path),
            research_plan_sha=_sha256_of_path(plan_path),
            prompt_sha=_sha256_of_path(_PROMPT_PATH),
            parser_version=VERSION,
        )
        cache = _read_cache(cache_path)
        if cache_key in cache:
            cached_classifications = _cached_payload_to_classifications(
                cache[cache_key]
            )

    try:
        result = run_register(
            plan_text=plan_text,
            provenance_text=provenance_text,
            no_llm=args.no_llm,
            cached_classifications=cached_classifications,
        )
    except LLMCallError as e:
        print(f"error: LLM call failed: {e}", file=sys.stderr)
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
        # B1.e: the classifier reattached the actual billed cost to
        # the ValidationError. Record it in the audit so the per-run
        # spend ledger is honest even when the LLM output was
        # rejected. Synthetic-fixture tests that don't bill set 0.0.
        billed_cost = float(getattr(e, "cost_usd", 0.0) or 0.0)
        emit_audit_line(
            audit_path=audit_path,
            methods_provenance_path=methods_path,
            research_plan_path=plan_path,
            output_path=output_path,
            entry_count=0,
            cost_usd=billed_cost,
            exit_status=4,
        )
        return 4

    # Write the register markdown.
    md = format_register_md(
        entries=result.entries,
        overlap_skipped_count=result.overlap_count,
        no_llm=args.no_llm,
    )
    output_path.write_text(md, encoding="utf-8")

    # Persist the cache only after a fresh LLM call (NOT on a cache hit
    # — we don't need to re-write what we just read; NOT on --no-llm —
    # there's nothing to cache; NOT when overlap was empty — no LLM
    # call happened).
    if (
        cache_key is not None
        and not result.cache_hit
        and not args.no_llm
        and result.classifications
    ):
        cache = _read_cache(cache_path)
        cache[cache_key] = {
            "classifications": [c.to_dict() for c in result.classifications],
            "cost_usd": result.cost_usd,
            "timestamp": _utc_now_iso(),
        }
        _write_cache(cache_path, cache)

    emit_audit_line(
        audit_path=audit_path,
        methods_provenance_path=methods_path,
        research_plan_path=plan_path,
        output_path=output_path,
        entry_count=len(result.entries),
        cost_usd=result.cost_usd,
        exit_status=0,
        cache_hit=result.cache_hit,
    )

    print(
        f"Wrote {output_path} ({len(result.entries)} entries"
        + (f"; {result.overlap_count} overlap(s) skipped" if args.no_llm else "")
        + (" [cache hit]" if result.cache_hit else "")
        + ")",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
