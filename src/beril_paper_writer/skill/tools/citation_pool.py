#!/usr/bin/env python3
"""citation_pool.py — citation pool data structures + serialization.

Per SPEC §6.4 + DECISIONS D-009 + D-011. The citation pool is the
verified set of references the writer is constrained to draw from when
generating prose. The literature scan that BUILDS the pool runs in
claude via WebSearch / PubMed MCP (Phase 3 prompt); this Python module
handles everything around that:

  - Strict 10-field schema for each entry (mirrors the adversarial
    reviewer's citation discipline)
  - Pool size cap at 80 (per D-009)
  - Deduplication by DOI / PMID
  - Serialization to THREE artifacts on disk:
      references.md      — numbered prose form, full 10-field block per entry
      bibliography.bib   — standard BibTeX (DOI/PMID retained, scope/finding NOT)
      citation_map.md    — table mapping citation # ↔ bib key ↔ first-cited location
  - Load existing artifacts back into a CitationPool on resume
  - Validate prompt-produced JSON entries (catch malformed/missing fields)

Per D-011, both references.md (human-readable) AND bibliography.bib
(machine-readable for the adversarial reviewer + downstream journal
submission) are produced from v0.1.

This module deliberately makes NO network calls. DOI/PMID verification
is the prompt's job in Phase 3 (using WebSearch); here we only check
that the metadata conforms to the schema.

Standalone CLI + importable module, mirroring the other Phase 2 tools.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POOL_SIZE_CAP = 80  # per SPEC §6.4 / DECISIONS D-009

# Acceptable values for the "scope_alignment" field per the adversarial
# reviewer's citation discipline. Stored as a single character internally
# so we can render different glyphs (Unicode marks vs ASCII) downstream.
SCOPE_ALIGNMENT_VALUES = ("direct", "partial", "mismatch")
ASSESSMENT_VALUES = ("supports", "partial", "contradicts", "orthogonal")

# Glyph mapping for the human-readable forms (matches adversarial
# reviewer's convention).
_SCOPE_GLYPHS = {
    "direct": "✓ direct",
    "partial": "⚠ partial",
    "mismatch": "✗ mismatch",
}
_ASSESSMENT_GLYPHS = {
    "supports": "✓ supports",
    "partial": "⚠ partial",
    "contradicts": "✗ contradicts",
    "orthogonal": "◇ orthogonal",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CitationEntry:
    """One citation pool entry — strict 10-field schema.

    Matches the adversarial reviewer's `adversarial_paper.v1.md` citation
    block format. Required fields will fail validation if missing or
    empty; optional fields can be absent.
    """

    # Required: header (BibTeX-derivable)
    authors: list[str]            # ["Smith J", "Doe A", ...] — list, not joined string
    year: int
    title: str
    venue: str                    # e.g. "Nature 615(7951):234-241" or "bioRxiv 2024.01.123"

    # Required: identifier (at least one of doi/pmid/pmcid/arxiv/biorxiv)
    doi: Optional[str] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    arxiv: Optional[str] = None
    biorxiv: Optional[str] = None

    # Required: discipline metadata (per adversarial 9-field block)
    studied: str = ""             # e.g. "Pseudomonas aeruginosa, N=156 isolates"
    finding: str = ""             # direct quote or quantitative result
    scope_alignment: str = ""     # one of SCOPE_ALIGNMENT_VALUES
    assessment: str = ""          # one of ASSESSMENT_VALUES

    # Optional metadata
    is_review_article: bool = False  # marked [REVIEW ARTICLE] when True
    is_preprint: bool = False
    notes: str = ""                  # free-form note from the literature-scan prompt
    bib_key: Optional[str] = None    # if absent, derived from authors+year

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CitationEntry:
        return cls(
            authors=list(d.get("authors", [])),
            year=int(d.get("year", 0)),
            title=str(d.get("title", "")),
            venue=str(d.get("venue", "")),
            doi=d.get("doi"),
            pmid=d.get("pmid"),
            pmcid=d.get("pmcid"),
            arxiv=d.get("arxiv"),
            biorxiv=d.get("biorxiv"),
            studied=str(d.get("studied", "")),
            finding=str(d.get("finding", "")),
            scope_alignment=str(d.get("scope_alignment", "")),
            assessment=str(d.get("assessment", "")),
            is_review_article=bool(d.get("is_review_article", False)),
            is_preprint=bool(d.get("is_preprint", False)),
            notes=str(d.get("notes", "")),
            bib_key=d.get("bib_key"),
        )

    def primary_id(self) -> Optional[str]:
        """Return the strongest available identifier, prefixed with its type.

        Used as the dedup key. DOI takes precedence (universal), then PMID
        (mature index), then PMCID, then arXiv/bioRxiv preprints.
        """
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        if self.pmid:
            return f"pmid:{self.pmid.strip()}"
        if self.pmcid:
            return f"pmcid:{self.pmcid.strip()}"
        if self.arxiv:
            return f"arxiv:{self.arxiv.strip()}"
        if self.biorxiv:
            return f"biorxiv:{self.biorxiv.strip()}"
        return None


@dataclass
class CitationPool:
    """A bounded collection of CitationEntry, with citation-numbering map.

    Citation numbers are assigned in order of first citation in prose
    (per IEEE / ICMJE numbered convention). The pool itself is unordered;
    the citation_map tracks the prose-order assignment.
    """

    entries: list[CitationEntry] = field(default_factory=list)
    # citation_map: bib_key → assigned citation number (1-based)
    citation_map: dict[str, int] = field(default_factory=dict)
    # For each citation #, where it was first cited (informational only)
    first_cited_at: dict[int, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "citation_map": dict(self.citation_map),
            "first_cited_at": {str(k): v for k, v in self.first_cited_at.items()},
            "summary": {
                "size": len(self.entries),
                "cap": POOL_SIZE_CAP,
                "remaining": POOL_SIZE_CAP - len(self.entries),
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> CitationPool:
        return cls(
            entries=[CitationEntry.from_dict(e) for e in d.get("entries", [])],
            citation_map=dict(d.get("citation_map", {})),
            first_cited_at={
                int(k): v for k, v in d.get("first_cited_at", {}).items()
            },
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    """One validation problem with a citation entry."""

    severity: str  # "error" | "warning"
    field: str
    message: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def validate_entry(entry: CitationEntry) -> list[ValidationError]:
    """Return all validation problems with a single entry. Empty list = OK."""
    errors: list[ValidationError] = []

    # Required fields
    if not entry.authors:
        errors.append(ValidationError("error", "authors", "authors list is empty"))
    elif not all(isinstance(a, str) and a.strip() for a in entry.authors):
        errors.append(ValidationError(
            "error", "authors", "all author entries must be non-empty strings"
        ))

    if not entry.year or entry.year < 1900 or entry.year > 2100:
        errors.append(ValidationError(
            "error", "year",
            f"year must be a plausible 4-digit year; got {entry.year!r}",
        ))

    if not entry.title.strip():
        errors.append(ValidationError("error", "title", "title is required"))
    elif len(entry.title) > 500:
        errors.append(ValidationError(
            "warning", "title", f"title is unusually long ({len(entry.title)} chars)"
        ))

    if not entry.venue.strip():
        errors.append(ValidationError(
            "error", "venue",
            "venue is required (e.g. 'Nature 615(7951):234-241' or 'bioRxiv 2024.01.123')",
        ))

    # At least one identifier
    if entry.primary_id() is None:
        errors.append(ValidationError(
            "error", "identifiers",
            "at least one of doi/pmid/pmcid/arxiv/biorxiv is required",
        ))

    # DOI format
    if entry.doi:
        if not re.match(r"^10\.\d{4,9}/", entry.doi.strip()):
            errors.append(ValidationError(
                "warning", "doi",
                f"doi {entry.doi!r} does not look like a valid DOI "
                f"(expected 10.NNNN/...)",
            ))

    # PMID format
    if entry.pmid:
        if not re.match(r"^\d+$", entry.pmid.strip()):
            errors.append(ValidationError(
                "warning", "pmid", f"pmid {entry.pmid!r} should be numeric"
            ))

    # Discipline metadata
    if not entry.studied.strip():
        errors.append(ValidationError(
            "error", "studied",
            "studied is required (organism / system / N) per "
            "adversarial 9-field discipline",
        ))
    if not entry.finding.strip():
        errors.append(ValidationError(
            "error", "finding",
            "finding is required (direct quote or quantitative result)",
        ))
    if entry.scope_alignment not in SCOPE_ALIGNMENT_VALUES:
        errors.append(ValidationError(
            "error", "scope_alignment",
            f"scope_alignment must be one of {SCOPE_ALIGNMENT_VALUES}; "
            f"got {entry.scope_alignment!r}",
        ))
    if entry.assessment not in ASSESSMENT_VALUES:
        errors.append(ValidationError(
            "error", "assessment",
            f"assessment must be one of {ASSESSMENT_VALUES}; "
            f"got {entry.assessment!r}",
        ))

    return errors


def validate_pool(pool: CitationPool) -> dict[int, list[ValidationError]]:
    """Validate every entry in a pool. Returns {entry_index: errors}."""
    out: dict[int, list[ValidationError]] = {}
    for i, e in enumerate(pool.entries):
        errs = validate_entry(e)
        if errs:
            out[i] = errs
    return out


# ---------------------------------------------------------------------------
# BibTeX key generation
# ---------------------------------------------------------------------------

def _ascii_normalize(s: str) -> str:
    """Strip diacritics: 'Müller' → 'Muller'."""
    nkfd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nkfd if not unicodedata.combining(c))


def _first_author_lastname(authors: list[str]) -> str:
    """Extract a last name from the first author entry.

    Handles:
      - "Smith J"        → "Smith"
      - "Smith, J."      → "Smith"
      - "Smith"          → "Smith"
      - "John Smith"     → "Smith"
      - "Müller K"       → "Muller"
    """
    if not authors:
        return "Unknown"
    raw = authors[0].strip()
    raw = _ascii_normalize(raw)
    if "," in raw:
        # Likely "Last, First" form
        return re.sub(r"[^A-Za-z]", "", raw.split(",", 1)[0]) or "Unknown"
    parts = raw.split()
    if not parts:
        return "Unknown"
    # If last part is just initials (capital letters or with periods), use the
    # second-to-last as the lastname; otherwise use last.
    last_token = parts[-1]
    if re.match(r"^[A-Z]\.?([A-Z]\.?)*$", last_token):
        # Initials-style: "Smith J" or "Smith JD"
        if len(parts) >= 2:
            return re.sub(r"[^A-Za-z]", "", parts[-2]) or "Unknown"
    # Otherwise the last token is the lastname (e.g., "John Smith")
    return re.sub(r"[^A-Za-z]", "", last_token) or "Unknown"


def derive_bib_key(entry: CitationEntry, suffix: str = "") -> str:
    """Construct a BibTeX key from author+year[+suffix]. Suffix added when
    needed to disambiguate (e.g., 'a', 'b' for same author+year)."""
    last = _first_author_lastname(entry.authors)
    return f"{last}{entry.year}{suffix}"


def assign_bib_keys(pool: CitationPool) -> None:
    """Assign bib_key to every entry that doesn't have one, disambiguating
    same-author-same-year collisions with suffixes a, b, c, ..."""
    used: set[str] = {e.bib_key for e in pool.entries if e.bib_key}
    for entry in pool.entries:
        if entry.bib_key:
            continue
        base = derive_bib_key(entry, suffix="")
        if base not in used:
            entry.bib_key = base
            used.add(base)
            continue
        # Disambiguate
        for suffix in "abcdefghijklmnopqrstuvwxyz":
            candidate = derive_bib_key(entry, suffix=suffix)
            if candidate not in used:
                entry.bib_key = candidate
                used.add(candidate)
                break
        else:
            # >26 collisions for the same author+year — extreme edge case
            for n in range(1, 1000):
                candidate = f"{base}_{n}"
                if candidate not in used:
                    entry.bib_key = candidate
                    used.add(candidate)
                    break


# ---------------------------------------------------------------------------
# Pool operations: add, dedup, cap
# ---------------------------------------------------------------------------

class PoolFullError(RuntimeError):
    """Raised when adding to a pool would exceed POOL_SIZE_CAP."""


class DuplicateEntryError(ValueError):
    """Raised when an entry would duplicate an existing primary_id."""


def add_entry(
    pool: CitationPool,
    entry: CitationEntry,
    *,
    on_duplicate: str = "skip",
) -> Optional[CitationEntry]:
    """Add an entry to the pool, with dedup + cap enforcement.

    on_duplicate:
        "skip"  — return the existing entry without modifying pool
        "merge" — backfill missing fields on existing entry from new entry
        "error" — raise DuplicateEntryError

    Returns the entry that ended up in the pool (existing or new), or
    None if the entry could not be added because it had no primary_id
    AND title-matched no existing entry.

    Raises PoolFullError if pool is at cap and entry is genuinely new.
    """
    new_id = entry.primary_id()
    if new_id is not None:
        for existing in pool.entries:
            if existing.primary_id() == new_id:
                if on_duplicate == "skip":
                    return existing
                elif on_duplicate == "merge":
                    _merge_into(existing, entry)
                    return existing
                else:  # "error"
                    raise DuplicateEntryError(
                        f"entry with primary_id {new_id} already in pool"
                    )

    if len(pool.entries) >= POOL_SIZE_CAP:
        raise PoolFullError(
            f"citation pool is at the {POOL_SIZE_CAP}-entry cap "
            f"(per SPEC §6.4 / D-009). Either drop a Discussion claim, "
            f"escalate as a citation-request gap-fill, or accept-as-limitation."
        )

    pool.entries.append(entry)
    return entry


def _merge_into(target: CitationEntry, new: CitationEntry) -> None:
    """Backfill missing fields on `target` from `new`. Existing values win."""
    for field_name in (
        "doi", "pmid", "pmcid", "arxiv", "biorxiv",
        "studied", "finding", "scope_alignment", "assessment", "notes",
    ):
        existing_val = getattr(target, field_name)
        if not existing_val:
            new_val = getattr(new, field_name)
            if new_val:
                setattr(target, field_name, new_val)


def assign_citation_numbers(
    pool: CitationPool, prose_citation_order: list[str],
) -> None:
    """Assign citation_map entries based on the order of first citation
    in the prose.

    prose_citation_order: list of bib_keys in the order they're first
    cited in the manuscript. Duplicates are ignored (each key gets the
    lowest-index assignment).
    """
    next_n = 1
    for key in prose_citation_order:
        if key not in pool.citation_map:
            pool.citation_map[key] = next_n
            next_n += 1


# ---------------------------------------------------------------------------
# Serialization: references.md
# ---------------------------------------------------------------------------

def _format_authors_for_prose(authors: list[str]) -> str:
    """Render authors per ICMJE-style: ≤3 listed, 'et al.' if 4+."""
    if not authors:
        return "Anonymous"
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{authors[0]}, et al."


def _format_id_block(entry: CitationEntry) -> str:
    """Build the trailing identifier block: 'doi:X PMID:Y'."""
    parts: list[str] = []
    if entry.doi:
        parts.append(f"doi:{entry.doi}")
    if entry.pmid:
        parts.append(f"PMID:{entry.pmid}")
    elif entry.pmcid:
        parts.append(f"PMCID:{entry.pmcid}")
    elif entry.arxiv:
        parts.append(f"arXiv:{entry.arxiv}")
    elif entry.biorxiv:
        parts.append(f"bioRxiv:{entry.biorxiv}")
    return " ".join(parts)


def format_references_md(pool: CitationPool) -> str:
    """Render the pool as a numbered references.md with the full 10-field
    block per entry. Citation numbers come from pool.citation_map.

    Entries that are in the pool but NOT in the citation_map (uncited)
    appear after cited entries, marked '(uncited)'.
    """
    out: list[str] = []
    out.append("# References")
    out.append("")
    has_cited = bool(pool.citation_map)
    if has_cited:
        out.append(
            "Numbered in order of first citation in the manuscript. Each "
            "entry carries the full 9-field citation discipline (Authors / "
            "Year / Title / Venue / DOI / ID / Studied / Finding / Scope "
            "alignment / Assessment) used by the BERIL paper-writer's "
            "adversarial reviewer."
        )
    else:
        out.append(
            "Citation pool — drafting prompts cite entries by their "
            "BibTeX-key in square brackets (e.g., `[Price2018]`). The "
            "orchestrator's `citation_pool.py finalize` step renumbers to "
            "ICMJE-style `[N]` citations after all sections are drafted, "
            "based on first-citation order in the assembled manuscript. "
            "Each entry carries the full 9-field discipline (Authors / "
            "Year / Title / Venue / DOI / ID / Studied / Finding / Scope "
            "alignment / Assessment)."
        )
    out.append("")

    # Build (number, entry) pairs in numeric order
    by_key = {e.bib_key: e for e in pool.entries if e.bib_key}
    cited_pairs = sorted(
        ((n, by_key[k]) for k, n in pool.citation_map.items() if k in by_key),
        key=lambda x: x[0],
    )
    uncited = [
        e for e in pool.entries
        if not e.bib_key or e.bib_key not in pool.citation_map
    ]

    for n, entry in cited_pairs:
        out.append(_format_one_entry_md(n, entry))
        out.append("")

    if uncited:
        out.append("## Uncited (in pool but not yet cited in prose)")
        out.append("")
        for entry in uncited:
            out.append(_format_one_entry_md(None, entry))
            out.append("")

    return "\n".join(out)


def _format_one_entry_md(number: Optional[int], entry: CitationEntry) -> str:
    """Render a single entry's full 10-field block in markdown.

    For cited entries (number is not None): renders `[N]` numeric form.
    For uncited entries: renders `[bib_key]` so downstream prompts can
    discover the citekey to cite by. Pre-finalize, all entries are
    uncited and visible-by-citekey; post-finalize, cited entries get
    numbered and the un-numbered (uncited-pool-residual) entries
    continue to display by bib_key.
    """
    lines: list[str] = []
    if number is not None:
        n_str = f"[{number}]"
    elif entry.bib_key:
        n_str = f"[{entry.bib_key}]"
    else:
        # Should not happen if assign_bib_keys was called, but fall
        # back gracefully rather than emit something the prompts can't cite.
        n_str = "[—]"
    review_marker = " [REVIEW ARTICLE]" if entry.is_review_article else ""
    preprint_marker = " [PREPRINT]" if entry.is_preprint else ""
    authors_str = _format_authors_for_prose(entry.authors)
    id_block = _format_id_block(entry)

    # Header line. Avoid a double period when authors_str ends in "et al."
    authors_with_terminator = authors_str if authors_str.endswith(".") else f"{authors_str}."
    lines.append(
        f"**{n_str} {authors_with_terminator} ({entry.year}). \"{entry.title}.\" "
        f"{entry.venue}.**{review_marker}{preprint_marker}"
    )
    if id_block:
        lines.append(id_block)
    lines.append("")
    # 4 metadata lines
    lines.append(f"- **Studied:** {entry.studied}")
    lines.append(f"- **Finding:** {entry.finding}")
    lines.append(
        f"- **Scope alignment:** "
        f"{_SCOPE_GLYPHS.get(entry.scope_alignment, entry.scope_alignment)}"
    )
    lines.append(
        f"- **Assessment:** "
        f"{_ASSESSMENT_GLYPHS.get(entry.assessment, entry.assessment)}"
    )
    if entry.notes:
        lines.append(f"- **Notes:** {entry.notes}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Serialization: bibliography.bib (BibTeX)
# ---------------------------------------------------------------------------

def _bibtex_escape(s: str) -> str:
    """Escape characters that have special meaning in BibTeX values."""
    # Replace bare $ with \$ (math-mode trigger), {/} are LaTeX grouping but
    # we want to preserve them since BibTeX uses braces. Just escape % which
    # is a comment marker and # which is BibTeX string-concat.
    return s.replace("%", r"\%").replace("#", r"\#")


def _bibtex_authors(authors: list[str]) -> str:
    """Render authors in BibTeX 'A and B and C' form.

    BibTeX expects 'Last, First' or 'First Last' joined with ' and '.
    Our internal representation might be 'Smith J' or 'John Smith' or
    'Smith, J' — we pass through whatever's there since BibTeX's parser
    is fairly forgiving.
    """
    return " and ".join(_bibtex_escape(a) for a in authors)


def _bibtex_entry_type(entry: CitationEntry) -> str:
    """Choose a BibTeX entry type based on what's known."""
    if entry.is_preprint or entry.arxiv or entry.biorxiv:
        return "misc"  # preprints don't have @article semantics
    return "article"   # default for journal venues


def _parse_venue_for_bibtex(venue: str) -> dict[str, str]:
    """Best-effort extraction of journal/volume/number/pages from a venue
    string like 'Nature 615(7951):234-241' or 'PLoS ONE 15:e0001234'."""
    out: dict[str, str] = {}
    # Try the canonical form: <journal> <volume>(<issue>):<pages>
    m = re.match(
        r"^(.+?)\s+(\d+)(?:\((\w+)\))?\s*:\s*(.+?)\.?$",
        venue.strip(),
    )
    if m:
        out["journal"] = m.group(1).strip()
        out["volume"] = m.group(2).strip()
        if m.group(3):
            out["number"] = m.group(3).strip()
        out["pages"] = m.group(4).strip()
        return out
    # Fallback: store the whole venue string in journal
    out["journal"] = venue.strip()
    return out


def _format_bib_entry(entry: CitationEntry) -> str:
    """Render one BibTeX entry. Excludes our extra discipline fields
    (Studied/Finding/ScopeAlignment/Assessment); those live in
    references.md only."""
    if not entry.bib_key:
        # Should not happen if assign_bib_keys was called; fail loud.
        raise ValueError(
            f"entry has no bib_key: {entry.title!r}. "
            f"Call assign_bib_keys(pool) before serializing to BibTeX."
        )
    etype = _bibtex_entry_type(entry)
    fields: list[tuple[str, str]] = [
        ("author", _bibtex_authors(entry.authors)),
        ("title", _bibtex_escape(entry.title)),
        ("year", str(entry.year)),
    ]
    venue_fields = _parse_venue_for_bibtex(entry.venue)
    for k in ("journal", "volume", "number", "pages"):
        if k in venue_fields:
            fields.append((k, _bibtex_escape(venue_fields[k])))
    if entry.doi:
        fields.append(("doi", entry.doi.strip()))
    if entry.pmid:
        # PMID isn't a standard BibTeX field; emit as note for round-trip.
        fields.append(("note", f"PMID:{entry.pmid.strip()}"))
    elif entry.pmcid:
        fields.append(("note", f"PMCID:{entry.pmcid.strip()}"))
    elif entry.arxiv:
        fields.append(("eprint", entry.arxiv.strip()))
        fields.append(("eprinttype", "arxiv"))
    elif entry.biorxiv:
        fields.append(("eprint", entry.biorxiv.strip()))
        fields.append(("eprinttype", "biorxiv"))

    body_lines = [f"  {k} = {{{v}}}," for k, v in fields]
    # Trim trailing comma on last field
    if body_lines:
        body_lines[-1] = body_lines[-1].rstrip(",")
    return "@" + etype + "{" + entry.bib_key + ",\n" + "\n".join(body_lines) + "\n}"


def format_bibliography_bib(pool: CitationPool) -> str:
    """Render every entry in the pool as a BibTeX file. Order is by
    citation_map number (cited entries first), then alphabetical by
    bib_key for uncited entries."""
    by_key = {e.bib_key: e for e in pool.entries if e.bib_key}
    cited = sorted(
        (n, by_key[k]) for k, n in pool.citation_map.items() if k in by_key
    )
    cited_keys = {k for k in pool.citation_map if k in by_key}
    uncited = sorted(
        (e for e in pool.entries if e.bib_key and e.bib_key not in cited_keys),
        key=lambda e: e.bib_key or "",
    )
    blocks: list[str] = []
    blocks.append(
        "% bibliography.bib — auto-generated by beril-paper-writer\n"
        "% citation_pool.py from references.md / pool entries.\n"
        "% Cited entries first (in citation order), uncited entries after."
    )
    for _, entry in cited:
        blocks.append(_format_bib_entry(entry))
    if uncited:
        blocks.append("% --- Uncited entries (in pool but not cited in prose) ---")
        for entry in uncited:
            blocks.append(_format_bib_entry(entry))
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Serialization: citation_map.md
# ---------------------------------------------------------------------------

def format_citation_map_md(pool: CitationPool) -> str:
    """Render a markdown table mapping citation # ↔ bib key ↔ first-cited
    location. Used by humans + the adversarial reviewer for cross-reference."""
    out: list[str] = []
    out.append("# Citation Map")
    out.append("")
    out.append(
        "Mapping between numbered prose citations (`[N]`), BibTeX keys, "
        "and where each was first cited. Generated by "
        "`beril-paper-writer` citation_pool.py."
    )
    out.append("")
    if not pool.citation_map:
        out.append("_(no citations have been used in prose yet)_")
        return "\n".join(out)

    out.append("| Citation # | BibTeX key | First cited (section, paragraph) |")
    out.append("|---|---|---|")

    for n in sorted(pool.citation_map.values()):
        # Find the bib_key for this number
        bib_key = next(
            (k for k, num in pool.citation_map.items() if num == n),
            "?",
        )
        loc = pool.first_cited_at.get(n, {})
        section = loc.get("section", "?")
        paragraph = loc.get("paragraph", "?")
        out.append(f"| {n} | `{bib_key}` | {section}, paragraph {paragraph} |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def serialize_to_disk(pool: CitationPool, draft_dir: Path) -> dict[str, Path]:
    """Write references.md, bibliography.bib, citation_map.md, and
    pool.json (machine-readable internal artifact for resume) to draft_dir.

    Returns a dict of {filename: path-written}.
    """
    draft_dir.mkdir(parents=True, exist_ok=True)
    assign_bib_keys(pool)

    paths: dict[str, Path] = {}

    refs_path = draft_dir / "references.md"
    refs_path.write_text(format_references_md(pool), encoding="utf-8")
    paths["references.md"] = refs_path

    bib_path = draft_dir / "bibliography.bib"
    bib_path.write_text(format_bibliography_bib(pool), encoding="utf-8")
    paths["bibliography.bib"] = bib_path

    map_path = draft_dir / "citation_map.md"
    map_path.write_text(format_citation_map_md(pool), encoding="utf-8")
    paths["citation_map.md"] = map_path

    # Internal artifact for resume — full pool JSON.
    pool_path = draft_dir / "pool.json"
    pool_path.write_text(
        json.dumps(pool.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    paths["pool.json"] = pool_path

    return paths


def load_from_disk(draft_dir: Path) -> CitationPool:
    """Load the citation pool from draft_dir/pool.json. Returns an empty
    pool if the file doesn't exist."""
    pool_path = draft_dir / "pool.json"
    if not pool_path.is_file():
        return CitationPool()
    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    return CitationPool.from_dict(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a JSON file containing a list of citation entries."""
    raw = json.loads(args.entries_json.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "entries" in raw:
        entries_data = raw["entries"]
    elif isinstance(raw, list):
        entries_data = raw
    else:
        print(
            "Error: entries JSON must be a list of entries OR an object "
            "with an 'entries' key.",
            file=sys.stderr,
        )
        return 1

    total_errors = 0
    total_warnings = 0
    for i, ed in enumerate(entries_data):
        try:
            entry = CitationEntry.from_dict(ed)
        except (TypeError, ValueError) as e:
            print(f"  entry [{i}]: failed to load: {e}", file=sys.stderr)
            total_errors += 1
            continue
        errs = validate_entry(entry)
        if not errs:
            continue
        print(f"entry [{i}] ({entry.title[:60]!r}):")
        for err in errs:
            line = f"  {err.severity}: {err.field}: {err.message}"
            print(line, file=sys.stderr if err.severity == "error" else sys.stdout)
            if err.severity == "error":
                total_errors += 1
            else:
                total_warnings += 1

    print(
        f"\nSummary: {len(entries_data)} entries, "
        f"{total_errors} errors, {total_warnings} warnings.",
        file=sys.stderr,
    )
    return 1 if total_errors else 0


def _cmd_format(args: argparse.Namespace) -> int:
    """Read a pool.json + write references.md / bibliography.bib /
    citation_map.md / pool.json into a draft directory."""
    raw = json.loads(args.pool_json.read_text(encoding="utf-8"))
    pool = CitationPool.from_dict(raw)
    paths = serialize_to_disk(pool, args.draft_dir)
    for name, p in paths.items():
        print(f"  wrote {name} → {p}", file=sys.stderr)
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    """Read pool.json from a draft dir and emit it as JSON to stdout."""
    pool = load_from_disk(args.draft_dir)
    sys.stdout.write(json.dumps(pool.to_dict(), indent=2) + "\n")
    return 0


# ---------------------------------------------------------------------------
# Finalize: walk drafted sections for [bib_key] marks, populate citation_map,
# rewrite references.md / citation_map.md / pool.json with numbering.
# ---------------------------------------------------------------------------

# Regex matching bib_keys in section prose: `[Lastname2018]`,
# `[Price2018a]`, or extreme-edge `[Price2018_2]`. Deliberately rejects
# `[1]` (no leading uppercase letter), so this won't false-match
# pre-edit numeric citations.
_CITEKEY_PATTERN = re.compile(r"\[([A-Z][a-zA-Z]*\d{4}(?:[a-z]|_\d+)?)\]")

# IMRAD ordering for citation-number assignment. The same order is used
# for first-citation discovery walking. Results-before-Discussion
# matches journal convention.
_FINALIZE_SECTION_ORDER = (
    "01_methods.md",
    "02_results.md",
    "03_discussion.md",
    "04_introduction.md",
    "05_abstract.md",
    "06_limitations.md",
    "07_data_availability.md",
)


def extract_citekeys_in_first_citation_order(
    draft_dir: Path,
    section_order: tuple[str, ...] = _FINALIZE_SECTION_ORDER,
) -> tuple[list[str], list[tuple[str, str, int]]]:
    """Walk section files in IMRAD order; return (ordered_keys, locations).

    `ordered_keys` is the list of unique bib_keys in first-citation order
    across the IMRAD sections (each key appears once, at its first
    citation site).

    `locations` is a list of `(bib_key, section_filename, paragraph_n)`
    tuples for every citekey occurrence (including duplicates) — used
    to populate `pool.first_cited_at` for citation_map.md.

    Section files that don't exist are skipped silently (the orchestrator
    handles section presence; finalize tolerates partial drafts during
    development).
    """
    ordered: list[str] = []
    seen: set[str] = set()
    locations: list[tuple[str, str, int]] = []

    for section_name in section_order:
        section_path = draft_dir / section_name
        if not section_path.is_file():
            continue
        text = section_path.read_text(encoding="utf-8")
        # Track paragraph number (1-based) by counting blank-line separators.
        paragraph_n = 1
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                # Paragraph break (collapse runs of blank lines into one boundary).
                paragraph_n += 1
                continue
            for match in _CITEKEY_PATTERN.finditer(line):
                key = match.group(1)
                locations.append((key, section_name, paragraph_n))
                if key not in seen:
                    ordered.append(key)
                    seen.add(key)
    return ordered, locations


def _cmd_finalize(args: argparse.Namespace) -> int:
    """Renumber a draft's citations based on first-citation order.

    Reads `<draft_dir>/pool.json`, walks `<draft_dir>/0?_*.md` section
    files for `[bib_key]` marks, populates `pool.citation_map` and
    `pool.first_cited_at` from the prose, then re-runs serialize_to_disk
    to rewrite references.md / citation_map.md / pool.json with
    numbered citations.

    Section files are NOT modified — they preserve `[bib_key]` form
    (non-destructive; finalize is re-runnable). The numeric `[N]` form
    is applied at manuscript-assembly time by `paper_writer.sh
    phase_assemble`, which substitutes `[bib_key]` → `[N]` from the
    finalized citation_map when concatenating to manuscript.md.

    Emits a finalize_warnings.md file if any `[bib_key]` in the prose
    doesn't resolve to a pool entry — these are orphaned citations the
    user should fix before submission. Exit 0 always (advisory); the
    warnings file (if non-empty) signals the orchestrator to surface in
    the next-actions handoff.
    """
    draft_dir: Path = args.draft_dir
    if not draft_dir.is_dir():
        print(f"error: draft_dir not found: {draft_dir}", file=sys.stderr)
        return 1

    pool_path = draft_dir / "pool.json"
    if not pool_path.is_file():
        print(f"error: pool.json not found at {pool_path}", file=sys.stderr)
        return 1

    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    pool = CitationPool.from_dict(raw)
    assign_bib_keys(pool)  # idempotent — no-op if already assigned

    ordered_keys, locations = extract_citekeys_in_first_citation_order(draft_dir)

    pool_keys = {e.bib_key for e in pool.entries if e.bib_key}
    resolved_keys = [k for k in ordered_keys if k in pool_keys]
    orphan_keys = [k for k in ordered_keys if k not in pool_keys]

    # Wipe any prior citation_map (re-runnable) and re-assign from prose order.
    pool.citation_map = {}
    pool.first_cited_at = {}
    assign_citation_numbers(pool, resolved_keys)
    # Populate first_cited_at for citation_map.md
    for key, section, para in locations:
        if key not in pool.citation_map:
            continue
        n = pool.citation_map[key]
        if n in pool.first_cited_at:
            continue  # earliest occurrence wins
        pool.first_cited_at[n] = {"section": section, "paragraph": str(para)}

    paths = serialize_to_disk(pool, draft_dir)
    for name, p in paths.items():
        print(f"  rewrote {name} → {p}", file=sys.stderr)

    # Orphan-citation warnings file. Always written; empty body if no orphans.
    warnings_path = draft_dir / "finalize_warnings.md"
    warning_lines: list[str] = ["# Citation Finalize Warnings", ""]
    if orphan_keys:
        warning_lines.append(
            f"**{len(orphan_keys)} orphaned citation(s)** — bib_keys cited "
            f"in prose but not present in pool.json. The user must add "
            f"these to the pool (or remove the citation from prose) "
            f"before submission. M10 will continue to fail until "
            f"resolved."
        )
        warning_lines.append("")
        for key in orphan_keys:
            occurrences = [(s, p) for (k, s, p) in locations if k == key]
            warning_lines.append(f"- `[{key}]` — orphaned (not in pool):")
            for s, p in occurrences[:5]:
                warning_lines.append(f"    - {s}, paragraph {p}")
            if len(occurrences) > 5:
                warning_lines.append(
                    f"    - ...and {len(occurrences) - 5} more occurrence(s)"
                )
        warning_lines.append("")
    else:
        warning_lines.append(
            "_(No orphaned citations. Every `[bib_key]` in prose "
            "resolves to a pool entry.)_"
        )
    warnings_path.write_text("\n".join(warning_lines) + "\n", encoding="utf-8")
    print(f"  wrote finalize_warnings.md → {warnings_path}", file=sys.stderr)

    print(
        f"\nSummary: {len(resolved_keys)} cited, "
        f"{len(orphan_keys)} orphaned, "
        f"{len(pool.entries) - len(resolved_keys)} pool entries uncited.",
        file=sys.stderr,
    )
    return 0


def _cmd_render_with_numbers(args: argparse.Namespace) -> int:
    """Substitute [bib_key] → [N] in a section file using pool.json's
    citation_map. Emits to stdout. Non-destructive: the input section
    file is unchanged.

    If a [bib_key] in the section isn't in the citation_map (orphaned),
    it stays as `[bib_key]` in the output — visible to the human reader
    as a cue to fix before submission. The validator (M10) will flag
    these too.
    """
    section_path: Path = args.section_file
    pool_path: Path = args.pool_json
    if not section_path.is_file():
        print(f"error: section file not found: {section_path}", file=sys.stderr)
        return 1
    if not pool_path.is_file():
        print(f"error: pool.json not found: {pool_path}", file=sys.stderr)
        return 1
    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    pool = CitationPool.from_dict(raw)
    text = section_path.read_text(encoding="utf-8")

    # Build a replacement function: [bib_key] → [N] iff key is in
    # citation_map; else leave the [bib_key] intact.
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        n = pool.citation_map.get(key)
        return f"[{n}]" if n is not None else match.group(0)

    out = _CITEKEY_PATTERN.sub(_sub, text)
    sys.stdout.write(out)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="citation_pool.py",
        description=(
            "Citation pool data-handling for the BERIL paper-writer. "
            "Validates / formats / loads pools. Does NOT perform "
            "literature search or DOI verification — those are LLM-driven "
            "via WebSearch in the Phase 3 prompt."
        ),
    )
    sub = p.add_subparsers(dest="command", metavar="<command>", required=True)

    p_val = sub.add_parser(
        "validate",
        help="Validate a JSON file of citation entries against the 10-field schema.",
    )
    p_val.add_argument(
        "entries_json", type=Path,
        help="Path to JSON file: list of entries OR object with 'entries' key.",
    )
    p_val.set_defaults(func=_cmd_validate)

    p_fmt = sub.add_parser(
        "format",
        help="Read pool.json and write references.md / bibliography.bib / citation_map.md.",
    )
    p_fmt.add_argument("pool_json", type=Path, help="Source pool.json")
    p_fmt.add_argument(
        "draft_dir", type=Path,
        help="Target draft directory (papers/draft_N/).",
    )
    p_fmt.set_defaults(func=_cmd_format)

    p_load = sub.add_parser(
        "load",
        help="Load a draft directory's pool.json and emit it as JSON to stdout.",
    )
    p_load.add_argument(
        "draft_dir", type=Path, help="Draft directory to read pool.json from."
    )
    p_load.set_defaults(func=_cmd_load)

    p_fin = sub.add_parser(
        "finalize",
        help=(
            "Walk drafted section files for [bib_key] marks; renumber "
            "references.md / citation_map.md / pool.json based on "
            "first-citation order. Idempotent."
        ),
    )
    p_fin.add_argument(
        "draft_dir", type=Path,
        help="Draft directory (papers/draft_N/) with section files + pool.json.",
    )
    p_fin.set_defaults(func=_cmd_finalize)

    p_render = sub.add_parser(
        "render-with-numbers",
        help=(
            "Read a section markdown file, substitute [bib_key] → [N] "
            "using pool.json's citation_map, write to stdout. Used by "
            "paper_writer.sh phase_assemble to build manuscript.md "
            "non-destructively (section files preserve [bib_key] form)."
        ),
    )
    p_render.add_argument("section_file", type=Path, help="Section .md file to render.")
    p_render.add_argument(
        "pool_json", type=Path,
        help="pool.json with populated citation_map (run finalize first).",
    )
    p_render.set_defaults(func=_cmd_render_with_numbers)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
