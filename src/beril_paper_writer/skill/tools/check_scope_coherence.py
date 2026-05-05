#!/usr/bin/env python3
"""check_scope_coherence.py — Discussion↔Results scope cross-walk (advisory).

Standalone script invoked by the shell orchestrator after
`phase_finalize_citations` writes its rendered references and before
`phase_assemble` builds the final manuscript:

    python3 "$SKILL_DIR/tools/check_scope_coherence.py" "$DRAFT_DIR"

Walks `03_discussion.md` paragraph by paragraph, extracts sentence-level
claims (declarative sentences containing project-claim numerical
anchors or finding-pattern verb phrases), and cross-walks each claim
against (a) the numerical/textual content of `02_results.md` and
(b) the "Would NOT include if this is the throughline" bullet list in
`00_throughline.md`.

Why this exists. Two cross-walk failure modes recurred in v0.1 live runs:
  - C9 (first live run): Discussion mentioned "GapMind 1,256" without a
    matching anchor in Results.
  - C1 (second live run): Discussion claimed conclusions from sub-claims
    that the throughline had explicitly placed in the would-NOT-include
    set.
The `reframer.v1` LLM pass catches some of this but is itself subject to
discipline drift (per the architectural memory at
`feedback_prompt_discipline_needs_post_check.md`). A programmatic
post-processor is a deterministic backstop.

Behavior:
  - Per discussion subsection, prints to stderr a summary of how many
    sentences were classified as claims and how many had unresolved
    anchors (verbose mode).
  - Emits a WARN line to stderr for each Discussion sentence whose
    project-claim numerical anchors are absent from Results AND not
    adjacent to a citation marker (i.e., not a literature reference).
  - Emits a WARN line to stderr for each Discussion sentence that
    substantively overlaps with a "Would NOT include" bullet
    (≥3 keyword hits out of the top-5 distinctive keywords from a
    single bullet).
  - Always exits 0. Advisory only; orchestrator surfaces warnings via
    `next_actions.md`.

The script can be imported as a module for unit testing; parsing
helpers are pure (text in, primitive out).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Subsection headers we skip when scanning Discussion. These describe
# caveats / future work rather than claims.
SKIP_HEADERS = {
    "limitations",
    "limitations and caveats",
    "next steps",
    "future work",
    "next steps and outlook",
    "conclusions",  # most "Conclusions" subsections restate Results, but if
                    # they introduce new numbers without Results anchors that
                    # is itself a smell — keep this one in scope by NOT
                    # listing it. Left here as a comment for future tuning.
}
# Note: "Conclusions" is intentionally NOT in SKIP_HEADERS — see comment.
SKIP_HEADERS.discard("conclusions")

# Finding-pattern verb phrases that mark a strong project claim.
# Curated from prompts SPEC §3 + first-two-live-run review of overclaim
# patterns. Lowercase substring match.
CLAIM_VERB_PHRASES = (
    "we identified",
    "we identify",
    "we found",
    "we find",
    "we demonstrate",
    "we showed",
    "we show",
    "we establish",
    "we developed",
    "we report",
    "we observe",
    "we observed",
    "yields",
    "yielded",
    "demonstrates",
    "demonstrated",
    "establishes",
    "established",
    "validates",
    "validated",
    "confirms",
    "confirmed",
    "shows that",
    "showed that",
    "finds that",
    "found that",
    "reveals",
    "revealed",
    "indicates that",
    "indicated that",
    "proves",
    "proved",
)

# Common English stopwords + scientific filler to drop when extracting
# distinctive keywords from "Would NOT include" bullets. Curated; extend
# only when a real bullet's keyword set turns out to be all-stopword.
STOPWORDS = frozenset(
    """
    a an the and or but if of to in on at by for with from as is are was were
    be been being have has had do does did this that these those it its their
    they them which who whom what when where why how not no yes also so such
    very more most less least many some any all each every other another both
    either neither only just than then thus therefore however moreover via
    into onto upon over under between among across about around through
    here there now where while since before after during because although
    though even still already yet would could should might may must can will
    one two three four five six seven eight nine ten
    method methods methodology approach analysis study work paper section
    figure table data result results finding findings claim claims
    discussion includes include included including covering cover covers
    covered chosen choose throughline framework set list explore explored
    exploring deep dive details detail single specific particular general
    various overall etc particularly especially primarily mainly broadly
    described describe describes discussed discuss discusses present presents
    presented within across between among focused focuses focus
    """.split()
)

# Numerical anchor regexes. Order matters: longer / more specific first
# so that comma-separated forms aren't mis-tokenized.
_NUMBER_PATTERNS = [
    # Comma-separated thousands (e.g., 1,256 ; 57,011 ; 27,690)
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),
    # Decimals (e.g., 24.9 ; 0.072 ; 0.231 ; 1.5e-6)
    re.compile(r"\b\d+\.\d+(?:[eE][+\-]?\d+)?\b"),
    # Percentages with no decimal (e.g., 95% ; 24% — picked up first as %)
    re.compile(r"\b\d+%"),
    # Plain integers ≥10 (skip 1-9 to reduce noise from "3+ organisms")
    re.compile(r"\b\d{2,}\b"),
]

# Citation patterns (BERIL-style author-year and post-finalize numeric).
# Used to detect numbers in literature-attribution scope, which we
# skip from project-claim cross-walk.
_CITATION_PATTERNS = [
    re.compile(r"\[[A-Z][A-Za-z][A-Za-z0-9'-]*\d{4}[a-z]?\]"),  # [Price2018]
    re.compile(r"\[\d+(?:[,–\-\s]*\d+)*\]"),                    # [12] or [12,15]
]

# Project-claim subject signals. When the closest preceding signal in
# a sentence is one of these (rather than a citation), the number is
# attributed to the current paper, not to a cited work. Compiled as a
# single alternation; case-insensitive.
_PROJECT_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"we\s+(?:identif\w+|find|found|show\w*|demonstrate\w*|establish\w*|"
    r"develop\w*|report\w*|observe\w*|extend\w*|present\w*|construct\w*|"
    r"investigat\w+|test\w+|measure\w+|analyz\w+|examin\w+|appli\w+|"
    r"defin\w+|comput\w+|integrat\w+|prioritiz\w+|rank\w*|score\w*|"
    r"derive\w*|map\w+|cover\w*|use\w*)"
    r"|our\s+(?:analysis|approach|study|work|results?|findings?|"
    r"framework|method\w*|prioritization|scoring|synteny|ranking)"
    r"|this\s+(?:study|analysis|paper|work|approach|framework|finding)"
    r"|the\s+(?:current|present)\s+(?:study|analysis|paper|work)"
    r")\b",
    re.IGNORECASE,
)

# Literature-context signal: phrases that indicate numbers come from
# external databases / surveys / prior work, even without a [Cite] marker.
# Treated as a citation-equivalent in is_literature_attributed().
_LITERATURE_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"(?:across|in|from)\s+(?:all\s+)?(?:GTDB|NCBI|RefSeq|GenBank|UniProt|"
    r"KEGG|IMG|Silva|Greengenes|EBI|PDB|SEED|MG-RAST)\b"
    r"|global(?:ly)?\s+(?:is|16S|surveys?|datasets?|analyses|"
    r"metagenom\w+|census|profiling)"
    r"|(?:16S|ITS|18S)\s+surveys?"
    r"|(?:published|reported|known|established|documented|previous)\s+"
    r"(?:values?|rates?|frequencies?|prevalence|distributions?|estimates?)"
    r"|literature\s+(?:values?|reports?|suggests?|indicates?)"
    r"|(?:publicly\s+available|curated)\s+(?:databases?|datasets?|genomes?)"
    r"|(?:reference|benchmark)\s+(?:databases?|datasets?|genomes?)"
    r")\b",
    re.IGNORECASE,
)

# Sentence splitter: end-of-sentence punctuation followed by whitespace
# and a token that starts a new sentence. Tolerates `.")` / `."` /
# parenthetical wrap-ups, em-dash continuations, etc.
_SENT_SPLIT = re.compile(
    r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\*\"`\[\(—–])",
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Parsing helpers — pure functions
# ---------------------------------------------------------------------------


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split a markdown document into (header, body) tuples.

    Walks H2 (`##`) and H3 (`###`) headers. The H1 (top-level title) is
    treated as a header with empty pre-amble. Body is everything from
    the line after the header up to (but not including) the next H2/H3.
    Headers are returned with their original casing.

    Returns a list ordered as the file. The first tuple has header
    `__preamble__` if there's content above the first H2/H3.
    """
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("__preamble__", [])]
    header_re = re.compile(r"^(##{1,2})\s+(.+?)\s*$")
    for line in lines:
        m = header_re.match(line)
        if m:
            sections.append((m.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in sections if b or h != "__preamble__"]


def strip_code_blocks(body: str) -> str:
    """Remove fenced code blocks; they are not prose claims."""
    return re.sub(r"```[\s\S]*?```", "", body)


def split_sentences(body: str) -> list[str]:
    """Split prose into sentences. Drops blank lines and bullet markers."""
    # Drop markdown bullet/numbered prefixes so the splitter sees prose.
    cleaned = re.sub(
        r"(?m)^\s*(?:[-*+]|\d+\.)\s+", "", strip_code_blocks(body)
    )
    # Collapse runs of newlines to single spaces so multi-line sentences
    # inside a paragraph rejoin.
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned).strip()
    if not cleaned:
        return []
    parts = _SENT_SPLIT.split(cleaned)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        # Drop residual heading-like fragments and one-token "sentences"
        if len(s.split()) < 3:
            continue
        out.append(s)
    return out


def extract_numbers(sentence: str) -> list[tuple[str, int, int]]:
    """Find numerical-anchor spans. Returns list of (literal, start, end).

    Order is left-to-right, no de-duplication (same anchor counted once
    per occurrence). The literal is the matched substring (e.g., "1,256",
    "24.9%").
    """
    spans: list[tuple[str, int, int]] = []
    seen_pos: set[tuple[int, int]] = set()
    for pat in _NUMBER_PATTERNS:
        for m in pat.finditer(sentence):
            pos = (m.start(), m.end())
            # Skip overlaps with longer matches found earlier.
            if any(s <= m.start() < e or s < m.end() <= e for s, e in seen_pos):
                continue
            seen_pos.add(pos)
            spans.append((m.group(0), m.start(), m.end()))
    spans.sort(key=lambda x: x[1])
    return spans


def normalize_number(literal: str) -> str:
    """Strip commas + trailing %, lowercase scientific notation. Identity
    on already-normalized forms.
    """
    s = literal.replace(",", "").rstrip("%").strip()
    return s.lower()


def find_citation_spans(sentence: str) -> list[tuple[int, int]]:
    """Return (start, end) for each citation marker in the sentence."""
    spans: list[tuple[int, int]] = []
    for pat in _CITATION_PATTERNS:
        for m in pat.finditer(sentence):
            spans.append((m.start(), m.end()))
    return spans


def is_literature_attributed(
    span: tuple[int, int],
    sentence: str,
    citations: list[tuple[int, int]],
) -> bool:
    """True if the given numerical span is in literature-attribution scope.

    Heuristic: walk preceding text in the same sentence. The closest
    preceding "subject signal" decides scope:
      - A citation marker OR literature-context signal → literature.
      - A project-claim signal (we identified, our analysis, this study,
        ...) → project.
      - Nothing → project (the number stands on its own).
    Falls back to checking for a *trailing* citation within a small
    window (≤25 chars) for the "N genes [Cite]" attribution pattern,
    but only when no preceding signal is found.
    """
    s, _ = span

    # Combine bracket-citations and literature-context signals as
    # "external-attribution" spans — both indicate the number comes
    # from outside the current study.
    preceding_citation_end = -1
    for cs, ce in citations:
        if ce <= s and ce > preceding_citation_end:
            preceding_citation_end = ce
    for m in _LITERATURE_SIGNAL_RE.finditer(sentence[:s]):
        if m.end() > preceding_citation_end:
            preceding_citation_end = m.end()

    preceding_signal_end = -1
    for m in _PROJECT_SIGNAL_RE.finditer(sentence[:s]):
        if m.end() > preceding_signal_end:
            preceding_signal_end = m.end()

    if preceding_citation_end == -1 and preceding_signal_end == -1:
        # Nothing preceding — check for trailing-citation attribution
        # ("N genes [Cite2018]") as a final fallback.
        e = span[1]
        for cs, ce in citations:
            if cs >= e and (cs - e) <= 25:
                return True
        return False

    return preceding_citation_end > preceding_signal_end


def has_claim_verb(sentence: str) -> bool:
    """True if the sentence contains any finding-pattern verb phrase."""
    low = sentence.lower()
    return any(v in low for v in CLAIM_VERB_PHRASES)


# ---------------------------------------------------------------------------
# Throughline "Would NOT include" extraction
# ---------------------------------------------------------------------------


def extract_would_not_include(throughline_text: str) -> list[dict]:
    """Pull the bullets from `## Would NOT include if this is the throughline`.

    Returns a list of dicts: {"raw": full bullet text, "keywords": list[str]}.
    Keywords are the top-5 (by length, descending) lowercase non-stopword
    tokens from the bullet text, excluding the trailing `→` clause if
    present (which describes how the bullet WOULD be handled, not what
    the bullet IS).
    """
    # Find the section. Match an H2 line that begins "Would NOT include".
    sec_re = re.compile(
        r"^##\s+Would\s+NOT\s+include[^\n]*\n([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.IGNORECASE,
    )
    m = sec_re.search(throughline_text)
    if not m:
        return []
    body = m.group(1)

    bullets: list[dict] = []
    bullet_re = re.compile(r"(?m)^\s*[-*+]\s+(.+?)(?=^\s*[-*+]\s+|\Z)", re.DOTALL)
    for bm in bullet_re.finditer(body):
        raw = bm.group(1).strip()
        # Trim trailing "→ ..." clause if present (describes disposition).
        head = re.split(r"\s+(?:→|->)\s+", raw, maxsplit=1)[0]
        keywords = _distinctive_keywords(head)
        bullets.append({"raw": raw, "keywords": keywords})
    return bullets


def _distinctive_keywords(text: str, top_n: int = 8) -> list[str]:
    """Return up to top_n distinctive lowercase tokens from `text`.

    Heuristic: tokenize on word boundaries (allowing hyphens), drop
    stopwords + tokens shorter than 3 chars, deduplicate, keep the
    longest tokens (proxy for distinctiveness). Hyphenated tokens
    are also expanded into their parts so phrases like "lab-field"
    yield "lab-field", "lab", "field" — improves recall when prose
    refers to the components without the hyphen.
    """
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z\-]*[A-Za-z]", text.lower())
    expanded: list[str] = []
    for t in raw_tokens:
        expanded.append(t)
        if "-" in t:
            for part in t.split("-"):
                if part:
                    expanded.append(part)
    keep: list[str] = []
    seen: set[str] = set()
    for t in expanded:
        if t in STOPWORDS:
            continue
        if len(t) < 3:
            continue
        if t in seen:
            continue
        seen.add(t)
        keep.append(t)
    keep.sort(key=lambda s: (-len(s), s))
    return keep[:top_n]


def matches_would_not_include(
    sentence: str, bullets: list[dict], min_hits: int = 3
) -> list[dict]:
    """Return bullets whose ≥`min_hits` keywords appear in the sentence."""
    low = sentence.lower()
    hits: list[dict] = []
    for b in bullets:
        kws = b["keywords"]
        if len(kws) < min_hits:
            # Bullet was too short to discriminate; skip to avoid FP.
            continue
        n_hit = sum(1 for k in kws if k in low)
        if n_hit >= min_hits:
            hits.append({"bullet": b, "matched_keywords": [k for k in kws if k in low]})
    return hits


# ---------------------------------------------------------------------------
# Results-anchor index
# ---------------------------------------------------------------------------


def build_results_index(results_text: str) -> tuple[set[str], str]:
    """Build (numbers_set, lower_text). The numbers_set holds normalized
    numerical anchors that appear anywhere in Results; the lower_text
    is the lowercased Results body for fallback substring lookups.
    """
    numbers: set[str] = set()
    for pat in _NUMBER_PATTERNS:
        for m in pat.finditer(results_text):
            numbers.add(normalize_number(m.group(0)))
    return numbers, results_text.lower()


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


def check(
    discussion_text: str,
    results_text: str,
    throughline_text: str,
    *,
    verbose: bool = True,
) -> int:
    """Walk discussion sentences; emit warnings to stderr.

    Returns the count of WARN lines emitted (excluding NOTE / summary).
    """
    results_numbers, _results_lower = build_results_index(results_text)
    bullets = extract_would_not_include(throughline_text)

    if verbose:
        print(
            f"[check_scope_coherence] Results index: "
            f"{len(results_numbers)} normalized numerical anchor(s).",
            file=sys.stderr,
        )
        print(
            f"[check_scope_coherence] Throughline 'Would NOT include' "
            f"bullets: {len(bullets)} (with keywords).",
            file=sys.stderr,
        )

    sections = split_sections(discussion_text)
    n_warnings = 0
    n_claims_total = 0

    for header, body in sections:
        if header == "__preamble__" or header.lower() in SKIP_HEADERS:
            continue
        # H1 line is "# Discussion" — not split as a section header by
        # split_sections (which only does H2/H3). Pre-amble is the body
        # under H1 before the first H2; we already skip preamble.
        sentences = split_sentences(body)
        n_claim_sents = 0
        n_anchored = 0
        n_unanchored = 0

        for sent in sentences:
            spans = extract_numbers(sent)
            has_verb = has_claim_verb(sent)
            if not spans and not has_verb:
                continue  # not a claim by either heuristic
            n_claim_sents += 1
            n_claims_total += 1

            # Numerical-anchor cross-walk.
            citations = find_citation_spans(sent)
            unresolved: list[str] = []
            for literal, s, e in spans:
                norm = normalize_number(literal)
                if norm in results_numbers:
                    continue
                if is_literature_attributed((s, e), sent, citations):
                    # Literature-side number; out of scope for cross-walk.
                    continue
                # Skip very small integers that are likely figure refs
                # ("Fig. 5", "Table 2"). Anchor must be ≥3 digits OR
                # contain a decimal/percent to be flagged.
                stripped = literal.replace(",", "").rstrip("%")
                if "." not in literal and "%" not in literal and len(stripped) < 3:
                    continue
                unresolved.append(literal)
            if unresolved:
                n_unanchored += 1
                # Truncate sentence preview for readability.
                preview = sent if len(sent) <= 180 else sent[:177] + "..."
                print(
                    f"[check_scope_coherence] WARN [{header}] "
                    f"unresolved numerical anchor(s) {unresolved}: {preview}",
                    file=sys.stderr,
                )
                n_warnings += 1
            elif spans:
                n_anchored += 1

            # Would-NOT-include cross-walk.
            wni_hits = matches_would_not_include(sent, bullets)
            for h in wni_hits:
                bullet_preview = h["bullet"]["raw"]
                if len(bullet_preview) > 140:
                    bullet_preview = bullet_preview[:137] + "..."
                preview = sent if len(sent) <= 180 else sent[:177] + "..."
                print(
                    f"[check_scope_coherence] WARN [{header}] "
                    f"matches would-NOT-include bullet via "
                    f"{h['matched_keywords']}: {preview} "
                    f"|| Bullet: {bullet_preview}",
                    file=sys.stderr,
                )
                n_warnings += 1

        if verbose and n_claim_sents:
            print(
                f"[check_scope_coherence] [{header}] "
                f"{n_claim_sents} claim sentence(s); "
                f"{n_anchored} numerically anchored, "
                f"{n_unanchored} unresolved.",
                file=sys.stderr,
            )

    if verbose:
        print(
            f"[check_scope_coherence] total claim sentences scanned: "
            f"{n_claims_total}",
            file=sys.stderr,
        )
    return n_warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument(
        "draft_dir",
        type=Path,
        help="Path to the draft directory (containing 00_throughline.md, "
        "02_results.md, 03_discussion.md).",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-section claim-count summary lines.",
    )
    args = ap.parse_args(argv)

    draft_dir: Path = args.draft_dir
    if not draft_dir.is_dir():
        print(
            f"[check_scope_coherence] ERROR: draft_dir not found: {draft_dir}",
            file=sys.stderr,
        )
        return 0  # advisory: do not block the pipeline

    discussion_path = draft_dir / "03_discussion.md"
    results_path = draft_dir / "02_results.md"
    throughline_path = draft_dir / "00_throughline.md"

    missing = [p for p in (discussion_path, results_path, throughline_path) if not p.is_file()]
    if missing:
        for p in missing:
            print(
                f"[check_scope_coherence] WARN missing input: {p}",
                file=sys.stderr,
            )
        print(
            f"[check_scope_coherence] complete: skipped (missing inputs).",
            file=sys.stderr,
        )
        return 0  # advisory: do not block the pipeline

    discussion = discussion_path.read_text(encoding="utf-8")
    results = results_path.read_text(encoding="utf-8")
    throughline = throughline_path.read_text(encoding="utf-8")

    n = check(discussion, results, throughline, verbose=not args.quiet)
    print(
        f"[check_scope_coherence] complete: {n} warning(s).",
        file=sys.stderr,
    )
    # Always 0 — advisory only. Orchestrator surfaces warnings.
    return 0


if __name__ == "__main__":
    sys.exit(main())
