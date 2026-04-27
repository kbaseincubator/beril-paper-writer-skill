#!/usr/bin/env python3
"""check_overclaim.py — Abstract/Discussion strong-claim cross-walk (advisory).

Standalone script invoked by the shell orchestrator after
`phase_finalize_citations` writes its rendered references and before
`phase_assemble` builds the final manuscript:

    python3 "$SKILL_DIR/tools/check_overclaim.py" "$DRAFT_DIR"

Walks `00_throughline.md`'s Evidence map, isolates the rows whose
strength glyph is ⚠ partial or ✗ contradicts (the "guarded" sub-claims),
then walks `05_abstract.md` and `03_discussion.md` for sentences that
contain strong-claim verbs (validates, demonstrates, yields, establishes,
proves, confirms, shows that, finds that, …). For each such sentence,
fuzzy-matches its keyword content against guarded sub-claims; if the
sentence overlaps substantially with a guarded sub-claim, emits a WARN
flagging the strong verb as an overclaim relative to the throughline's
own caveat.

Why this exists. The plan.v1 throughline encodes evidence strength as
glyphs (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal). The
section-drafting prompts SPEC requires the Abstract and Discussion to
respect those strengths; reframer.v1 catches some of this but is itself
an LLM call subject to discipline drift (per
`feedback_prompt_discipline_needs_post_check.md`). C1-C7 from the first
v0.1 live run and C1 from the second showed that strong-claim verbs in
the Abstract/Discussion routinely outpaced the underlying ⚠ partial
strength. A programmatic post-processor is the deterministic backstop.

Behavior:
  - Per Abstract/Discussion subsection, prints to stderr a count of
    strong-claim sentences scanned (verbose mode).
  - Emits a WARN line to stderr for each strong-claim sentence whose
    keyword content overlaps with a guarded sub-claim. The WARN
    includes the strong verb, the strength glyph, the caveat text,
    and previews of both the sentence and the sub-claim.
  - Marks `[caveat-acknowledged]` on WARN lines where the sentence
    text already contains caveat-language tokens from the
    throughline's caveat clause (e.g., "marginal", "p=0.072") —
    the user can de-prioritize these.
  - Always exits 0. Advisory only; orchestrator surfaces via
    `next_actions.md`.

The script can be imported as a module for unit testing; parsing
helpers are pure (text in, primitive out).

Future refactor note. `tools/check_scope_coherence.py`,
`tools/check_throughline_glyphs.py`, and this file share several
parsing helpers (sentence splitting, distinctive-keyword extraction,
section walking). A future consolidation could lift these into
`tools/_postcheck_helpers.py`. Deferred to keep v0.1 tier-1 deliveries
small and the import surface bash-friendly.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GLYPH_DIRECT = "✓"        # ✓
GLYPH_PARTIAL = "⚠"       # ⚠
GLYPH_CONTRADICTS = "✗"   # ✗
GLYPH_ORTHOGONAL = "◇"    # ◇
GUARDED_GLYPHS = (GLYPH_PARTIAL, GLYPH_CONTRADICTS)

# Strong-claim verb patterns. These are the verbs that assert direct
# causal/evidentiary support. Curated from v0.1 live-run C1-C7 reviews
# (see feedback_prompt_discipline_needs_post_check.md). Compiled as a
# single alternation; case-insensitive. Each pattern matches the verb
# form (and reasonable inflections) at a word boundary.
STRONG_VERB_PATTERNS = (
    r"\bvalidat(?:e|es|ed|ing)\b",
    r"\bdemonstrat(?:e|es|ed|ing)\b",
    r"\bestablish(?:|es|ed|ing)\b",
    r"\bprov(?:e|es|ed|en|ing)\b",
    r"\byield(?:s|ed|ing)?\b",
    r"\bconfirm(?:s|ed|ing)?\b",
    r"\bshow(?:s|ed|n)?\s+that\b",
    r"\bfind(?:s|ing)?\s+that\b",
    r"\bfound\s+that\b",
    r"\bshow(?:s|ed|n)\b",
    r"\bdefinitively\s+\w+\b",
    r"\bunambiguously\s+\w+\b",
    r"\bconclusively\s+\w+\b",
)
_STRONG_VERB_RE = re.compile(
    "|".join(STRONG_VERB_PATTERNS), re.IGNORECASE
)

# Subsection headers we skip. Limitations / Next steps describe caveats
# and future work, not claims; their use of strong verbs is rhetorical
# (e.g., "demonstrates that simple conservation models do NOT hold")
# and does not represent an overclaim.
SKIP_HEADERS = {
    "limitations",
    "limitations and caveats",
    "next steps",
    "future work",
    "next steps and outlook",
}

# Common English stopwords + scientific filler. Curated against
# real Discussion / sub-claim prose; extend only when a real overclaim
# match needs a token currently filtered.
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

# Numerical anchor regexes (mirrors check_scope_coherence.py). Used for
# caveat-acknowledgment detection: if a sentence's numerical anchors
# overlap the caveat's, we flag the sentence as caveat-acknowledged.
_NUMBER_PATTERNS = [
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),
    re.compile(r"\b\d+\.\d+(?:[eE][+\-]?\d+)?\b"),
    re.compile(r"\b\d+%"),
    re.compile(r"\b\d{2,}\b"),
]

# Caveat-language keywords. If any of these tokens appears in the
# strong-claim sentence AND in the guarded row's caveat text, we treat
# the sentence as "caveat-acknowledged" (medium severity rather than
# high). Curated from the four ⚠ partial caveat clauses in real
# functional_dark_matter draft_1 throughline.
CAVEAT_LANGUAGE_TOKENS = (
    "marginal",
    "lower bound",
    "lower-bound",
    "weight-sensitive",
    "weight perturbations",
    "compositional",
    "annotation vintage",
    "annotation-lagging",
    "may misclassify",
    "may be invisible",
    "guilt-by-association",
    "not experimental validation",
    "barely",
    "overstate",
    "overstates",
    "circular",
    "indistinguishable from null",
)

# Sentence splitter. Same pattern as check_scope_coherence.py.
_SENT_SPLIT = re.compile(
    r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\*\"`\[\(—–])",
    re.UNICODE,
)

# Citation patterns + project-claim signal (mirrors check_scope_coherence).
# Used to decide whether a strong verb is in literature-attribution
# scope (preceded by a citation, not by a project-claim signal).
_CITATION_PATTERNS = [
    re.compile(r"\[[A-Z][A-Za-z][A-Za-z0-9'-]*\d{4}[a-z]?\]"),
    re.compile(r"\[\d+(?:[,–\-\s]*\d+)*\]"),
]
_PROJECT_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"we\s+(?:identif\w+|find|found|show\w*|demonstrate\w*|establish\w*|"
    r"develop\w*|report\w*|observe\w*|extend\w*|present\w*|construct\w*|"
    r"investigat\w+|test\w+|measure\w+|analyz\w+|examin\w+|appli\w+|"
    r"defin\w+|comput\w+|integrat\w+|prioritiz\w+|rank\w*|score\w*|"
    r"derive\w*|map\w+|cover\w*|use\w*)"
    r"|our\s+(?:analysis|approach|study|work|results?|findings?|"
    r"framework|method\w*|prioritization|scoring|synteny|ranking|"
    r"identification|finding)"
    r"|this\s+(?:study|analysis|paper|work|approach|framework|finding)"
    r"|the\s+(?:current|present)\s+(?:study|analysis|paper|work)"
    r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Markdown / table parsing
# ---------------------------------------------------------------------------


def split_sections(text: str) -> list[tuple[str, str]]:
    """Walk H2/H3 headers; return (header, body) tuples."""
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
    return re.sub(r"```[\s\S]*?```", "", body)


def split_sentences(body: str) -> list[str]:
    """Split prose into sentences; drop bullet markers + short fragments."""
    cleaned = re.sub(
        r"(?m)^\s*(?:[-*+]|\d+\.)\s+", "", strip_code_blocks(body)
    )
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned).strip()
    if not cleaned:
        return []
    parts = _SENT_SPLIT.split(cleaned)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        if len(s.split()) < 3:
            continue
        out.append(s)
    return out


def parse_evidence_map(throughline_text: str) -> list[dict]:
    """Extract data rows from the `## Evidence map` table.

    Returns a list of dicts: {"sub_claim", "source", "strength_cell",
    "glyph", "caveat", "row_idx"}. `glyph` is one of the four constants
    (or None if no glyph found). `caveat` is the trailing text after the
    "— " (em-dash) in the strength cell, if any.
    """
    sec_re = re.compile(
        r"^##\s+Evidence\s+map\s*\n([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.IGNORECASE,
    )
    m = sec_re.search(throughline_text)
    if not m:
        return []
    body = m.group(1)
    rows: list[dict] = []
    row_idx = 0
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.match(r"^\|\s*[-:]+\s*\|", s):  # separator
            continue
        if "Sub-claim" in s and "Strength" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3:
            continue
        sub_claim = cells[0]
        source = cells[1]
        strength_cell = cells[2]
        glyph = None
        for g in (GLYPH_DIRECT, GLYPH_PARTIAL, GLYPH_CONTRADICTS, GLYPH_ORTHOGONAL):
            if g in strength_cell:
                glyph = g
                break
        caveat = ""
        # Caveat is whatever follows " — " (em-dash with spaces) in the cell.
        for sep in (" — ", " - ", " – "):
            if sep in strength_cell:
                caveat = strength_cell.split(sep, 1)[1].strip()
                break
        row_idx += 1
        rows.append({
            "row_idx": row_idx,
            "sub_claim": sub_claim,
            "source": source,
            "strength_cell": strength_cell,
            "glyph": glyph,
            "caveat": caveat,
        })
    return rows


# ---------------------------------------------------------------------------
# Keyword extraction + matching
# ---------------------------------------------------------------------------


def distinctive_keywords(text: str, top_n: int = 12) -> list[str]:
    """Return up to top_n distinctive lowercase tokens from `text`.

    Tokenize on word boundaries (allowing hyphens), drop stopwords +
    tokens shorter than 3 chars, expand hyphenated tokens into parts,
    deduplicate, keep the longest tokens.
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


def extract_numbers(text: str) -> set[str]:
    """Return the normalized numerical anchors found in `text`."""
    nums: set[str] = set()
    seen_pos: set[tuple[int, int]] = set()
    for pat in _NUMBER_PATTERNS:
        for m in pat.finditer(text):
            pos = (m.start(), m.end())
            if any(s <= m.start() < e or s < m.end() <= e for s, e in seen_pos):
                continue
            seen_pos.add(pos)
            literal = m.group(0)
            nums.add(literal.replace(",", "").rstrip("%").lower())
    return nums


def caveat_acknowledged(sentence: str, caveat_text: str) -> list[str]:
    """Return the list of caveat-language tokens AND/OR shared numerical
    anchors that appear in both the sentence and the caveat text. A
    non-empty list means the sentence acknowledges the caveat inline.
    """
    sent_low = sentence.lower()
    cav_low = caveat_text.lower()
    hits: list[str] = []
    for tok in CAVEAT_LANGUAGE_TOKENS:
        if tok in sent_low and tok in cav_low:
            hits.append(tok)
    sent_nums = extract_numbers(sentence)
    cav_nums = extract_numbers(caveat_text)
    shared_nums = sent_nums & cav_nums
    hits.extend(sorted(shared_nums))
    return hits


def find_strong_verbs(sentence: str) -> list[tuple[str, int]]:
    """Return the strong-claim verbs matched in the sentence as
    (literal, start_offset) tuples."""
    return [
        (m.group(0).lower(), m.start())
        for m in _STRONG_VERB_RE.finditer(sentence)
    ]


def is_literature_attributed_verb(
    verb_offset: int, sentence: str
) -> bool:
    """True if the closest preceding subject-signal for the verb is a
    citation rather than a project-claim signal.
    """
    preceding_citation_end = -1
    for pat in _CITATION_PATTERNS:
        for m in pat.finditer(sentence[:verb_offset]):
            if m.end() > preceding_citation_end:
                preceding_citation_end = m.end()
    preceding_signal_end = -1
    for m in _PROJECT_SIGNAL_RE.finditer(sentence[:verb_offset]):
        if m.end() > preceding_signal_end:
            preceding_signal_end = m.end()
    if preceding_citation_end == -1 and preceding_signal_end == -1:
        return False
    return preceding_citation_end > preceding_signal_end


def overlap_keywords(
    sentence: str, sub_claim_keywords: list[str], min_hits: int = 3
) -> list[str]:
    """Return the sub-claim keywords present in the sentence; non-empty
    if ≥ min_hits are present (else returns empty list to fail the
    threshold cleanly).
    """
    low = sentence.lower()
    hits = [k for k in sub_claim_keywords if k in low]
    return hits if len(hits) >= min_hits else []


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


def check(
    throughline_text: str,
    abstract_text: str,
    discussion_text: str,
    *,
    verbose: bool = True,
) -> int:
    """Walk Abstract + Discussion sentences; emit overclaim warnings to stderr.

    Returns the count of WARN lines emitted.
    """
    rows = parse_evidence_map(throughline_text)
    guarded = [r for r in rows if r["glyph"] in GUARDED_GLYPHS]

    if verbose:
        print(
            f"[check_overclaim] Evidence map: {len(rows)} row(s); "
            f"{len(guarded)} guarded (⚠/✗).",
            file=sys.stderr,
        )

    if not guarded:
        if verbose:
            print(
                "[check_overclaim] No ⚠ partial / ✗ contradicts rows; "
                "throughline asserts no caveats. Nothing to cross-walk.",
                file=sys.stderr,
            )
        return 0

    # Pre-compute keyword sets per guarded row, then derive each row's
    # *distinctive* keywords (those not shared by ANY other guarded row).
    # The distinctive-keyword requirement prevents generic vocabulary
    # ("predictions", "validation", "independent") from triggering
    # cross-claim FPs when multiple guarded sub-claims share basic
    # cross-organism evidence terms.
    for r in guarded:
        r["keywords"] = distinctive_keywords(r["sub_claim"])
    for r in guarded:
        others: set[str] = set()
        for o in guarded:
            if o is not r:
                others.update(o["keywords"])
        r["distinctive"] = [k for k in r["keywords"] if k not in others]
        if verbose:
            print(
                f"[check_overclaim] sub-claim #{r['row_idx']} "
                f"({r['glyph']}): {len(r['keywords'])} keyword(s), "
                f"{len(r['distinctive'])} distinctive: "
                f"{r['distinctive'][:5]}",
                file=sys.stderr,
            )

    n_warnings = 0

    # Walk Abstract — flat (no H2 sections in v0.1 abstract template).
    n_strong_abstract = 0
    for sent in split_sentences(abstract_text):
        verbs = find_strong_verbs(sent)
        # Drop literature-attributed verbs.
        verbs = [
            (v, off) for v, off in verbs
            if not is_literature_attributed_verb(off, sent)
        ]
        if not verbs:
            continue
        n_strong_abstract += 1
        n_warnings += _emit_overclaim_warnings(
            sent, verbs, guarded, location="Abstract"
        )
    if verbose:
        print(
            f"[check_overclaim] [Abstract] "
            f"{n_strong_abstract} strong-verb sentence(s) scanned.",
            file=sys.stderr,
        )

    # Walk Discussion subsections — skip Limitations / Next steps / etc.
    sections = split_sections(discussion_text)
    for header, body in sections:
        if header == "__preamble__":
            continue
        if header.lower() in SKIP_HEADERS:
            continue
        n_strong_sect = 0
        for sent in split_sentences(body):
            verbs = find_strong_verbs(sent)
            verbs = [
                (v, off) for v, off in verbs
                if not is_literature_attributed_verb(off, sent)
            ]
            if not verbs:
                continue
            n_strong_sect += 1
            n_warnings += _emit_overclaim_warnings(
                sent, verbs, guarded, location=f"Discussion / {header}"
            )
        if verbose and n_strong_sect:
            print(
                f"[check_overclaim] [{header}] "
                f"{n_strong_sect} strong-verb sentence(s) scanned.",
                file=sys.stderr,
            )

    return n_warnings


def _emit_overclaim_warnings(
    sentence: str,
    verbs: list[tuple[str, int]],
    guarded: list[dict],
    *,
    location: str,
) -> int:
    """Emit WARN lines for each guarded sub-claim that overlaps the
    sentence's keywords AND shares ≥1 distinctive keyword with the row.
    Returns the number of WARNs emitted (0 if no match).
    """
    n = 0
    verb_literals = sorted({v for v, _ in verbs})
    for row in guarded:
        hits = overlap_keywords(sentence, row["keywords"])
        if not hits:
            continue
        # Distinctive-keyword gate: require ≥2 distinctive overlap.
        # Single distinctive tokens are too easy to hit on polysemous
        # words ("confidence", "concordance", "conservation"); the ≥2
        # threshold eliminates the cross-claim FPs observed during
        # smoke testing on draft_1.
        distinctive_hits = [k for k in hits if k in row["distinctive"]]
        if len(distinctive_hits) < 2:
            continue

        ack = caveat_acknowledged(sentence, row["caveat"])
        marker = "[caveat-acknowledged]" if ack else "[unacknowledged]"

        sent_preview = sentence if len(sentence) <= 180 else sentence[:177] + "..."
        sub_preview = row["sub_claim"]
        if len(sub_preview) > 140:
            sub_preview = sub_preview[:137] + "..."
        caveat_preview = row["caveat"]
        if len(caveat_preview) > 140:
            caveat_preview = caveat_preview[:137] + "..."

        print(
            f"[check_overclaim] WARN {marker} [{location}] "
            f"strong verb(s) {verb_literals} on sub-claim "
            f"#{row['row_idx']} ({row['glyph']}) "
            f"overlapping via {hits} (distinctive: {distinctive_hits}): "
            f"{sent_preview} || Sub-claim: {sub_preview} || "
            f"Caveat: {caveat_preview} || "
            f"Acknowledged tokens: {ack if ack else '(none)'}",
            file=sys.stderr,
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument(
        "draft_dir",
        type=Path,
        help="Path to the draft directory (containing 00_throughline.md, "
        "03_discussion.md, 05_abstract.md).",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-section strong-verb-count summary lines.",
    )
    args = ap.parse_args(argv)

    draft_dir: Path = args.draft_dir
    if not draft_dir.is_dir():
        print(
            f"[check_overclaim] ERROR: draft_dir not found: {draft_dir}",
            file=sys.stderr,
        )
        return 0  # advisory: do not block the pipeline

    throughline_path = draft_dir / "00_throughline.md"
    abstract_path = draft_dir / "05_abstract.md"
    discussion_path = draft_dir / "03_discussion.md"

    missing = [p for p in (throughline_path, abstract_path, discussion_path) if not p.is_file()]
    if missing:
        for p in missing:
            print(
                f"[check_overclaim] WARN missing input: {p}",
                file=sys.stderr,
            )
        print(
            f"[check_overclaim] complete: skipped (missing inputs).",
            file=sys.stderr,
        )
        return 0

    throughline = throughline_path.read_text(encoding="utf-8")
    abstract = abstract_path.read_text(encoding="utf-8")
    discussion = discussion_path.read_text(encoding="utf-8")

    n = check(throughline, abstract, discussion, verbose=not args.quiet)
    print(
        f"[check_overclaim] complete: {n} warning(s).",
        file=sys.stderr,
    )
    # Always 0 — advisory only.
    return 0


if __name__ == "__main__":
    sys.exit(main())
