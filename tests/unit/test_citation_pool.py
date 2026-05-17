"""Tests for skill/tools/citation_pool.py — pool, validation, serialization.

Coverage:
  - CitationEntry round-trip + primary_id resolution
  - validate_entry: each required-field failure mode + each warning case
  - validate_pool aggregates per-entry errors
  - BibTeX key derivation from author+year, with disambiguation
  - add_entry with dedup (skip / merge / error) + cap enforcement
  - assign_citation_numbers honors prose-order
  - format_references_md: cited entries first, full 10-field block
  - format_bibliography_bib: standard BibTeX, no discipline fields,
    venue parsing
  - format_citation_map_md: table form
  - serialize_to_disk + load_from_disk round-trip
  - CLI subcommands (validate, format, load)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from beril_paper_writer.skill.tools import citation_pool as cp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_entry(**overrides) -> cp.CitationEntry:
    """Return a fully-valid CitationEntry; override fields with kwargs."""
    base = dict(
        authors=["Smith J", "Doe A"],
        year=2023,
        title="An interesting paper",
        venue="Nature 615(7951):234-241",
        doi="10.1038/s41586-023-12345",
        pmid="36123456",
        studied="Pseudomonas aeruginosa, N=156 isolates",
        finding="Phenotype X observed in 42/156 (26.9%)",
        scope_alignment="direct",
        assessment="supports",
    )
    base.update(overrides)
    return cp.CitationEntry(**base)


# ---------------------------------------------------------------------------
# CitationEntry
# ---------------------------------------------------------------------------

class TestCitationEntry:
    def test_round_trip_to_dict(self):
        e = _good_entry()
        d = e.to_dict()
        e2 = cp.CitationEntry.from_dict(d)
        assert e2.authors == e.authors
        assert e2.year == e.year
        assert e2.doi == e.doi
        assert e2.scope_alignment == e.scope_alignment

    def test_primary_id_doi_preferred(self):
        e = _good_entry(doi="10.1038/X", pmid="999")
        assert e.primary_id() == "doi:10.1038/x"

    def test_primary_id_pmid_when_no_doi(self):
        e = _good_entry(doi=None, pmid="999")
        assert e.primary_id() == "pmid:999"

    def test_primary_id_arxiv_fallback(self):
        e = _good_entry(doi=None, pmid=None, arxiv="2301.12345")
        assert e.primary_id() == "arxiv:2301.12345"

    def test_primary_id_none_when_no_ids(self):
        e = _good_entry(doi=None, pmid=None, pmcid=None, arxiv=None, biorxiv=None)
        assert e.primary_id() is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_good_entry_no_errors(self):
        errs = cp.validate_entry(_good_entry())
        assert errs == []

    def test_missing_authors(self):
        errs = cp.validate_entry(_good_entry(authors=[]))
        fields = [e.field for e in errs]
        assert "authors" in fields

    def test_missing_year(self):
        errs = cp.validate_entry(_good_entry(year=0))
        assert any(e.field == "year" for e in errs)

    def test_implausible_year(self):
        errs = cp.validate_entry(_good_entry(year=1700))
        assert any(e.field == "year" for e in errs)

    def test_missing_title(self):
        errs = cp.validate_entry(_good_entry(title=""))
        assert any(e.field == "title" for e in errs)

    def test_missing_venue(self):
        errs = cp.validate_entry(_good_entry(venue=""))
        assert any(e.field == "venue" for e in errs)

    def test_missing_all_identifiers(self):
        errs = cp.validate_entry(
            _good_entry(doi=None, pmid=None, pmcid=None, arxiv=None, biorxiv=None)
        )
        assert any(e.field == "identifiers" for e in errs)

    def test_invalid_doi_warning(self):
        errs = cp.validate_entry(_good_entry(doi="not-a-doi"))
        warns = [e for e in errs if e.severity == "warning"]
        assert any(e.field == "doi" for e in warns)

    def test_missing_studied(self):
        errs = cp.validate_entry(_good_entry(studied=""))
        assert any(e.field == "studied" for e in errs)

    def test_missing_finding(self):
        errs = cp.validate_entry(_good_entry(finding=""))
        assert any(e.field == "finding" for e in errs)

    def test_invalid_scope_alignment(self):
        errs = cp.validate_entry(_good_entry(scope_alignment="huh?"))
        assert any(e.field == "scope_alignment" for e in errs)

    def test_invalid_assessment(self):
        errs = cp.validate_entry(_good_entry(assessment="huh?"))
        assert any(e.field == "assessment" for e in errs)

    def test_validate_pool_aggregates(self):
        pool = cp.CitationPool(entries=[
            _good_entry(),
            _good_entry(authors=[]),
        ])
        result = cp.validate_pool(pool)
        assert 0 not in result
        assert 1 in result


# ---------------------------------------------------------------------------
# BibTeX key generation
# ---------------------------------------------------------------------------

class TestBibKeyDerivation:
    def test_simple_key(self):
        e = _good_entry(authors=["Smith J"])
        assert cp.derive_bib_key(e) == "Smith2023"

    def test_strips_diacritics(self):
        e = _good_entry(authors=["Müller K"])
        assert cp.derive_bib_key(e) == "Muller2023"

    def test_handles_first_last_form(self):
        e = _good_entry(authors=["John Smith"])
        assert cp.derive_bib_key(e) == "Smith2023"

    def test_handles_last_comma_first(self):
        e = _good_entry(authors=["Smith, J."])
        assert cp.derive_bib_key(e) == "Smith2023"

    def test_handles_initials_only_after_lastname(self):
        e = _good_entry(authors=["Smith JD"])
        assert cp.derive_bib_key(e) == "Smith2023"

    def test_assign_disambiguates(self):
        pool = cp.CitationPool(entries=[
            _good_entry(authors=["Smith J"], doi="10.1038/A"),
            _good_entry(authors=["Smith J"], doi="10.1038/B"),
            _good_entry(authors=["Smith J"], doi="10.1038/C"),
        ])
        cp.assign_bib_keys(pool)
        keys = [e.bib_key for e in pool.entries]
        assert keys[0] == "Smith2023"
        assert keys[1] == "Smith2023a"
        assert keys[2] == "Smith2023b"

    def test_assign_preserves_existing(self):
        pool = cp.CitationPool(entries=[
            _good_entry(bib_key="MyKey"),
            _good_entry(authors=["Doe J"]),
        ])
        cp.assign_bib_keys(pool)
        assert pool.entries[0].bib_key == "MyKey"
        assert pool.entries[1].bib_key == "Doe2023"


# ---------------------------------------------------------------------------
# Pool ops: add, dedup, cap
# ---------------------------------------------------------------------------

class TestPoolOps:
    def test_add_first_entry(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        assert len(pool.entries) == 1

    def test_dedup_skip(self):
        pool = cp.CitationPool()
        e1 = _good_entry()
        e2 = _good_entry(title="A different title")  # same DOI
        cp.add_entry(pool, e1)
        cp.add_entry(pool, e2, on_duplicate="skip")
        # Pool unchanged: only the original entry is kept.
        assert len(pool.entries) == 1
        assert pool.entries[0].title == "An interesting paper"

    def test_dedup_merge_backfills(self):
        pool = cp.CitationPool()
        e1 = _good_entry(notes="")
        e2 = _good_entry(notes="extra note")
        cp.add_entry(pool, e1)
        cp.add_entry(pool, e2, on_duplicate="merge")
        assert len(pool.entries) == 1
        assert pool.entries[0].notes == "extra note"

    def test_dedup_error_raises(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        with pytest.raises(cp.DuplicateEntryError):
            cp.add_entry(pool, _good_entry(), on_duplicate="error")

    def test_cap_enforced(self):
        pool = cp.CitationPool()
        # Fill to cap with unique DOIs
        for i in range(cp.POOL_SIZE_CAP):
            cp.add_entry(pool, _good_entry(doi=f"10.1038/X{i:04d}"))
        # The next add should raise
        with pytest.raises(cp.PoolFullError):
            cp.add_entry(pool, _good_entry(doi="10.1038/EXTRA"))

    def test_dedup_within_cap(self):
        """Adding a duplicate when at cap should NOT raise PoolFullError —
        it returns the existing entry without modifying the pool."""
        pool = cp.CitationPool()
        for i in range(cp.POOL_SIZE_CAP):
            cp.add_entry(pool, _good_entry(doi=f"10.1038/X{i:04d}"))
        # Adding the same entry as #0 — should be deduped, not capped
        result = cp.add_entry(pool, _good_entry(doi="10.1038/X0000"))
        assert result is pool.entries[0]
        assert len(pool.entries) == cp.POOL_SIZE_CAP


# ---------------------------------------------------------------------------
# Citation numbering
# ---------------------------------------------------------------------------

class TestCitationNumbering:
    def test_assign_in_order(self):
        pool = cp.CitationPool()
        for i, t in enumerate(["A", "B", "C"]):
            cp.add_entry(pool, _good_entry(doi=f"10.X/{t}", title=t))
        cp.assign_bib_keys(pool)
        keys = [e.bib_key for e in pool.entries]
        cp.assign_citation_numbers(pool, [keys[2], keys[0], keys[1]])
        # First-cited gets 1, etc.
        assert pool.citation_map[keys[2]] == 1
        assert pool.citation_map[keys[0]] == 2
        assert pool.citation_map[keys[1]] == 3

    def test_duplicates_in_prose_order_ignored(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(doi="10.X/A", title="A"))
        cp.assign_bib_keys(pool)
        key = pool.entries[0].bib_key
        cp.assign_citation_numbers(pool, [key, key, key])
        assert pool.citation_map[key] == 1
        assert len(pool.citation_map) == 1


# ---------------------------------------------------------------------------
# References.md formatter
# ---------------------------------------------------------------------------

class TestReferencesMdFormat:
    def test_full_block_rendered(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(pool, [pool.entries[0].bib_key])
        md = cp.format_references_md(pool)
        # Required pieces of a full citation block
        assert "[1]" in md
        assert "Smith J, Doe A" in md
        assert "An interesting paper" in md
        assert "Nature 615(7951):234-241" in md
        assert "doi:10.1038/s41586-023-12345" in md
        assert "PMID:36123456" in md
        assert "**Studied:**" in md
        assert "**Finding:**" in md
        assert "**Scope alignment:**" in md
        assert "**Assessment:**" in md
        assert "✓ direct" in md
        assert "✓ supports" in md

    def test_uncited_entries_separated(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(doi="10.X/A", title="cited"))
        cp.add_entry(pool, _good_entry(doi="10.X/B", title="uncited"))
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(pool, [pool.entries[0].bib_key])
        md = cp.format_references_md(pool)
        assert "Uncited" in md
        # Cited appears before Uncited section
        cited_pos = md.find("cited")
        uncited_section = md.find("Uncited")
        assert cited_pos < uncited_section

    def test_et_al_for_4_or_more_authors(self):
        e = _good_entry(authors=["A", "B", "C", "D", "E"])
        formatted = cp._format_authors_for_prose(e.authors)
        assert formatted == "A, et al."

    def test_no_double_period_with_et_al(self):
        """Regression: 'et al.' ends in period, template adds another → '..'."""
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(authors=["A", "B", "C", "D"]))
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(pool, [pool.entries[0].bib_key])
        md = cp.format_references_md(pool)
        assert "et al.." not in md
        assert "et al." in md  # but the single-period form IS present

    def test_review_article_marker(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(is_review_article=True))
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(pool, [pool.entries[0].bib_key])
        md = cp.format_references_md(pool)
        assert "[REVIEW ARTICLE]" in md


# ---------------------------------------------------------------------------
# Bibliography.bib formatter
# ---------------------------------------------------------------------------

class TestBibFormatter:
    def test_basic_article(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        bib = cp.format_bibliography_bib(pool)
        assert "@article{Smith2023," in bib
        assert "title = {An interesting paper}" in bib
        assert "year = {2023}" in bib
        assert "journal = {Nature}" in bib
        assert "volume = {615}" in bib
        assert "number = {7951}" in bib
        assert "pages = {234-241}" in bib
        assert "doi = {10.1038/s41586-023-12345}" in bib
        assert "PMID:36123456" in bib

    def test_no_discipline_fields_in_bib(self):
        """The 4 discipline fields (Studied/Finding/Scope/Assessment) are
        in references.md only — bibliography.bib stays standard."""
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(
            studied="X N=100",
            finding="some finding",
            scope_alignment="direct",
            assessment="supports",
        ))
        cp.assign_bib_keys(pool)
        bib = cp.format_bibliography_bib(pool)
        assert "studied" not in bib.lower()
        assert "finding" not in bib.lower()
        assert "scope" not in bib.lower()
        assert "assessment" not in bib.lower()

    def test_venue_without_volume_falls_back_to_journal(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(venue="bioRxiv preprint"))
        cp.assign_bib_keys(pool)
        bib = cp.format_bibliography_bib(pool)
        # Whole venue lands in the journal field as fallback
        assert "journal = {bioRxiv preprint}" in bib

    def test_arxiv_uses_eprint(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(
            doi=None, pmid=None, arxiv="2301.12345", is_preprint=True,
        ))
        cp.assign_bib_keys(pool)
        bib = cp.format_bibliography_bib(pool)
        assert "@misc{" in bib  # preprint → misc, not article
        assert "eprint = {2301.12345}" in bib
        assert "eprinttype = {arxiv}" in bib

    def test_failure_when_no_bib_key(self):
        pool = cp.CitationPool(entries=[_good_entry(bib_key=None)])
        # No assign_bib_keys called — _format_bib_entry should raise
        with pytest.raises(ValueError):
            cp._format_bib_entry(pool.entries[0])


# ---------------------------------------------------------------------------
# Citation map formatter
# ---------------------------------------------------------------------------

class TestCitationMapFormat:
    def test_table_renders(self):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(doi="10.X/A"))
        cp.add_entry(pool, _good_entry(doi="10.X/B"))
        cp.assign_bib_keys(pool)
        keys = [e.bib_key for e in pool.entries]
        cp.assign_citation_numbers(pool, keys)
        pool.first_cited_at[1] = {"section": "Introduction", "paragraph": 2}
        pool.first_cited_at[2] = {"section": "Discussion", "paragraph": 1}
        md = cp.format_citation_map_md(pool)
        assert "| 1 | `" in md and keys[0] in md
        assert "Introduction" in md
        assert "Discussion" in md

    def test_empty_pool_message(self):
        pool = cp.CitationPool()
        md = cp.format_citation_map_md(pool)
        assert "no citations" in md.lower()


# ---------------------------------------------------------------------------
# Disk round-trip
# ---------------------------------------------------------------------------

class TestDiskRoundTrip:
    def test_serialize_then_load(self, tmp_path: Path):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(doi="10.X/A", title="Paper A"))
        cp.add_entry(pool, _good_entry(doi="10.X/B", title="Paper B"))
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(
            pool, [pool.entries[0].bib_key, pool.entries[1].bib_key]
        )
        paths = cp.serialize_to_disk(pool, tmp_path)
        # All four files written
        assert paths["references.md"].is_file()
        assert paths["bibliography.bib"].is_file()
        assert paths["citation_map.md"].is_file()
        assert paths["pool.json"].is_file()
        # Load back
        loaded = cp.load_from_disk(tmp_path)
        assert len(loaded.entries) == 2
        assert loaded.citation_map == pool.citation_map

    def test_load_returns_empty_when_no_pool_json(self, tmp_path: Path):
        loaded = cp.load_from_disk(tmp_path)
        assert isinstance(loaded, cp.CitationPool)
        assert loaded.entries == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    SCRIPT = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "citation_pool.py"
    )

    def test_validate_passes_on_good_entries(self, tmp_path: Path):
        entries_path = tmp_path / "entries.json"
        entries_path.write_text(
            json.dumps([_good_entry().to_dict()]), encoding="utf-8"
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "validate", str(entries_path)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0

    def test_validate_fails_on_bad_entries(self, tmp_path: Path):
        entries_path = tmp_path / "entries.json"
        bad = _good_entry(authors=[]).to_dict()
        entries_path.write_text(json.dumps([bad]), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "validate", str(entries_path)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 1

    def test_format_writes_three_files(self, tmp_path: Path):
        # Build a pool, write its JSON, then format it
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        cp.assign_citation_numbers(pool, [pool.entries[0].bib_key])
        pool_path = tmp_path / "pool.json"
        pool_path.write_text(json.dumps(pool.to_dict()), encoding="utf-8")
        draft_dir = tmp_path / "draft"
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "format",
             str(pool_path), str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert (draft_dir / "references.md").is_file()
        assert (draft_dir / "bibliography.bib").is_file()
        assert (draft_dir / "citation_map.md").is_file()

    def test_load_emits_json(self, tmp_path: Path):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        cp.serialize_to_disk(pool, tmp_path)
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "load", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        loaded = json.loads(proc.stdout)
        assert "entries" in loaded
        assert len(loaded["entries"]) == 1


# ---------------------------------------------------------------------------
# Stage 4 Tier R-1 + R-2: holistic-flow finalize
#
# These tests cover the v0.8 holistic-draft path where there are no
# per-section files (0?_*.md). The finalize walker dispatches to
# manuscript.md, the pool path auto-detect prefers citation_pool.json,
# and serialize_to_disk preserves the input pool's filename.
# ---------------------------------------------------------------------------


def _holistic_manuscript(*citations: str) -> str:
    """Build a synthetic v0.8 manuscript.md body for tests.

    Each ``citation`` arg becomes a sentence with that single
    ``[BibKey]`` in the Results section. The first citation also
    appears in Methods (so the first-citation order test can see
    cross-section ordering when desired). The manuscript carries the
    expected v0.8 IMRAD section headings.
    """
    if not citations:
        return "# Title\n\n## Abstract\n\nNo citations here.\n"
    first = citations[0]
    results_paragraphs = "\n\n".join(
        f"Result {i+1} is supported by [{key}]."
        for i, key in enumerate(citations)
    )
    return (
        "# Manuscript Title\n\n"
        "## Abstract\n\n"
        "Some preamble with no citations.\n\n"
        "## Methods\n\n"
        f"We applied published methods [{first}] throughout.\n\n"
        "## Results\n\n"
        f"{results_paragraphs}\n\n"
        "## Discussion\n\n"
        "Broader implications are noted.\n\n"
        "## References\n\n"
        "1. ...\n"
    )


class TestStage4HolisticWalker:
    """Stage 4 Tier R-1: extract_citekeys_from_manuscript +
    extract_citekeys_in_first_citation_order dispatch."""

    def test_canonicalize_heading_strips_punctuation(self):
        assert cp._canonicalize_heading("Methods") == "methods"
        assert cp._canonicalize_heading("Data Availability:") == "data availability"
        assert cp._canonicalize_heading("  Discussion. ") == "discussion"

    def test_walker_finds_citations_in_first_citation_order(self, tmp_path: Path):
        ms = tmp_path / "manuscript.md"
        ms.write_text(_holistic_manuscript("Smith2023", "Jones2024", "Lee2022"))
        ordered, locations = cp.extract_citekeys_from_manuscript(ms)
        # Smith2023 appears first in Methods (per _holistic_manuscript).
        assert ordered == ["Smith2023", "Jones2024", "Lee2022"]
        # Smith2023 has TWO occurrences (Methods + Results).
        smith_locs = [l for l in locations if l[0] == "Smith2023"]
        assert len(smith_locs) == 2
        # All other keys have exactly one occurrence.
        assert len([l for l in locations if l[0] == "Jones2024"]) == 1
        assert len([l for l in locations if l[0] == "Lee2022"]) == 1

    def test_walker_attributes_section_labels_from_headings(self, tmp_path: Path):
        ms = tmp_path / "manuscript.md"
        ms.write_text(_holistic_manuscript("Smith2023", "Jones2024"))
        _, locations = cp.extract_citekeys_from_manuscript(ms)
        # The first Smith2023 occurrence is in Methods.
        first_smith = next(l for l in locations if l[0] == "Smith2023")
        assert first_smith[1] == "methods"
        # Jones2024 (second result paragraph) is in Results.
        jones_loc = next(l for l in locations if l[0] == "Jones2024")
        assert jones_loc[1] == "results"

    def test_walker_handles_compound_citations(self, tmp_path: Path):
        ms = tmp_path / "manuscript.md"
        ms.write_text(
            "## Results\n\n"
            "Several priors agree [Smith2023, Jones2024]. We extend.\n"
        )
        ordered, locations = cp.extract_citekeys_from_manuscript(ms)
        assert ordered == ["Smith2023", "Jones2024"]
        # Both keys attributed to the same section.
        assert {l[1] for l in locations} == {"results"}

    def test_walker_handles_no_citations(self, tmp_path: Path):
        ms = tmp_path / "manuscript.md"
        ms.write_text(_holistic_manuscript())  # empty citations
        ordered, locations = cp.extract_citekeys_from_manuscript(ms)
        assert ordered == []
        assert locations == []

    def test_walker_front_matter_label_for_pre_heading_citation(
        self, tmp_path: Path,
    ):
        ms = tmp_path / "manuscript.md"
        ms.write_text(
            "# Title\n\n"
            "Some context [Smith2023] before the first heading.\n\n"
            "## Methods\n\n"
            "More material.\n"
        )
        _, locations = cp.extract_citekeys_from_manuscript(ms)
        assert locations[0] == ("Smith2023", "front-matter", 2)

    def test_dispatcher_prefers_sectional_when_section_files_present(
        self, tmp_path: Path,
    ):
        # Both manuscript.md AND a sectional file: sectional wins.
        (tmp_path / "01_methods.md").write_text(
            "Methods text [SectionalKey2020]\n"
        )
        (tmp_path / "manuscript.md").write_text(
            _holistic_manuscript("ManuscriptKey2020")
        )
        ordered, locations = cp.extract_citekeys_in_first_citation_order(tmp_path)
        assert ordered == ["SectionalKey2020"]
        assert all(l[1] == "01_methods.md" for l in locations)

    def test_dispatcher_uses_manuscript_when_no_section_files(
        self, tmp_path: Path,
    ):
        (tmp_path / "manuscript.md").write_text(
            _holistic_manuscript("ManuscriptKey2020")
        )
        ordered, _ = cp.extract_citekeys_in_first_citation_order(tmp_path)
        assert ordered == ["ManuscriptKey2020"]

    def test_dispatcher_returns_empty_when_neither_present(
        self, tmp_path: Path,
    ):
        ordered, locations = cp.extract_citekeys_in_first_citation_order(tmp_path)
        assert ordered == []
        assert locations == []


class TestStage4SerializePoolFilename:
    """Stage 4 Tier R-2: serialize_to_disk pool_filename kwarg."""

    def test_default_pool_filename_is_pool_json(self, tmp_path: Path):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        paths = cp.serialize_to_disk(pool, tmp_path)
        assert "pool.json" in paths
        assert (tmp_path / "pool.json").is_file()
        # citation_pool.json should NOT be created when not requested.
        assert not (tmp_path / "citation_pool.json").is_file()

    def test_pool_filename_kwarg_preserves_caller_name(self, tmp_path: Path):
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry())
        cp.assign_bib_keys(pool)
        paths = cp.serialize_to_disk(
            pool, tmp_path, pool_filename="citation_pool.json",
        )
        assert "citation_pool.json" in paths
        assert (tmp_path / "citation_pool.json").is_file()
        # Legacy pool.json should NOT be created when a different name is requested.
        assert not (tmp_path / "pool.json").is_file()


class TestStage4FinalizeCLI:
    """Stage 4 Tier R-2: CLI behavior for the new --pool-path flag and
    the citation_pool.json/pool.json auto-detection."""

    SCRIPT = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "beril_paper_writer"
        / "skill"
        / "tools"
        / "citation_pool.py"
    )

    def _seed_pool(self, draft_dir: Path, *, filename: str = "pool.json") -> Path:
        """Write a 2-entry pool to <draft_dir>/<filename>; return that path."""
        draft_dir.mkdir(parents=True, exist_ok=True)
        pool = cp.CitationPool()
        cp.add_entry(pool, _good_entry(doi="10.X/A", title="Paper A"))
        cp.add_entry(pool, _good_entry(doi="10.X/B", title="Paper B"))
        cp.assign_bib_keys(pool)
        pool_path = draft_dir / filename
        pool_path.write_text(
            json.dumps(pool.to_dict(), indent=2), encoding="utf-8"
        )
        return pool_path

    def test_finalize_holistic_flow_renders_references(self, tmp_path: Path):
        """Holistic flow: manuscript.md only, citation_pool.json present.

        This is the v0.8.0 production case — the bug we are fixing.
        After finalize the rendered references.md must be non-empty
        and reference the cited keys.
        """
        draft_dir = tmp_path / "draft"
        self._seed_pool(draft_dir, filename="citation_pool.json")
        # Derive the actual bib_keys the pool assigned so the test
        # citations match.
        pool = cp.load_from_disk(draft_dir)  # legacy reads pool.json — empty
        # Reload from the v0.8-named file.
        raw = json.loads((draft_dir / "citation_pool.json").read_text())
        pool = cp.CitationPool.from_dict(raw)
        keys = [e.bib_key for e in pool.entries]
        assert len(keys) == 2 and all(keys)
        (draft_dir / "manuscript.md").write_text(
            _holistic_manuscript(*keys)
        )

        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize", str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # references.md must be non-empty.
        refs = (draft_dir / "references.md").read_text()
        assert refs.strip(), "references.md should be non-empty post-finalize"
        # citation_map.md must be non-empty.
        cmap = (draft_dir / "citation_map.md").read_text()
        assert cmap.strip(), "citation_map.md should be non-empty post-finalize"
        # Pool was overwritten with renumbered citation_map.
        raw_after = json.loads((draft_dir / "citation_pool.json").read_text())
        assert raw_after.get("citation_map"), \
            "citation_map should be populated after finalize"
        # No parallel pool.json should be created.
        assert not (draft_dir / "pool.json").is_file()

    def test_finalize_sectional_flow_backwards_compat(self, tmp_path: Path):
        """Legacy sectional flow: 0?_*.md files present, no manuscript.md."""
        draft_dir = tmp_path / "draft"
        self._seed_pool(draft_dir, filename="pool.json")
        raw = json.loads((draft_dir / "pool.json").read_text())
        pool = cp.CitationPool.from_dict(raw)
        keys = [e.bib_key for e in pool.entries]
        (draft_dir / "01_methods.md").write_text(
            f"Methods text [{keys[0]}].\n"
        )
        (draft_dir / "02_results.md").write_text(
            f"Results show [{keys[1]}] effects.\n"
        )

        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize", str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        refs = (draft_dir / "references.md").read_text()
        assert refs.strip()

    def test_finalize_explicit_pool_path_wins(self, tmp_path: Path):
        """An explicit --pool-path overrides the auto-detect search."""
        draft_dir = tmp_path / "draft"
        # Put a pool at a non-standard name.
        custom_pool = self._seed_pool(draft_dir, filename="custom_pool.json")
        raw = json.loads(custom_pool.read_text())
        pool = cp.CitationPool.from_dict(raw)
        keys = [e.bib_key for e in pool.entries]
        (draft_dir / "manuscript.md").write_text(
            _holistic_manuscript(*keys)
        )

        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize",
             str(draft_dir), "--pool-path", str(custom_pool)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # The custom-named pool was overwritten with citation_map.
        raw_after = json.loads(custom_pool.read_text())
        assert raw_after.get("citation_map")
        # No parallel files were created.
        assert not (draft_dir / "pool.json").is_file()
        assert not (draft_dir / "citation_pool.json").is_file()

    def test_finalize_autodetect_prefers_citation_pool_json(
        self, tmp_path: Path,
    ):
        """When both pool.json AND citation_pool.json are present, the
        autodetect picks citation_pool.json (v0.8 convention) first."""
        draft_dir = tmp_path / "draft"
        # Seed BOTH files with DIFFERENT contents so we can tell which
        # one finalize used to drive the render.
        draft_dir.mkdir(parents=True, exist_ok=True)
        legacy_pool = cp.CitationPool()
        cp.add_entry(legacy_pool, _good_entry(doi="10.X/legacy", title="Legacy"))
        cp.assign_bib_keys(legacy_pool)
        (draft_dir / "pool.json").write_text(
            json.dumps(legacy_pool.to_dict(), indent=2)
        )

        v08_pool = cp.CitationPool()
        cp.add_entry(v08_pool, _good_entry(doi="10.X/v08", title="V08"))
        cp.assign_bib_keys(v08_pool)
        (draft_dir / "citation_pool.json").write_text(
            json.dumps(v08_pool.to_dict(), indent=2)
        )

        # manuscript.md only cites the v0.8 pool's key.
        v08_key = v08_pool.entries[0].bib_key
        (draft_dir / "manuscript.md").write_text(
            _holistic_manuscript(v08_key)
        )

        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize", str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # citation_pool.json should now have citation_map populated.
        raw_v08 = json.loads(
            (draft_dir / "citation_pool.json").read_text()
        )
        assert raw_v08.get("citation_map"), \
            "v0.8 pool should have been chosen and renumbered"
        # The legacy pool.json should be untouched (no citation_map).
        raw_legacy = json.loads((draft_dir / "pool.json").read_text())
        assert not raw_legacy.get("citation_map")

    def test_finalize_errors_when_no_pool_present(self, tmp_path: Path):
        """Without any pool file, finalize exits non-zero with a clear error."""
        draft_dir = tmp_path / "draft"
        draft_dir.mkdir(parents=True, exist_ok=True)
        # Section file present but no pool.
        (draft_dir / "manuscript.md").write_text(
            _holistic_manuscript("Smith2023")
        )
        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize", str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0
        assert "pool file not found" in proc.stderr

    def test_finalize_writes_orphan_warnings(self, tmp_path: Path):
        """If the manuscript cites a bib_key not in the pool, finalize
        writes an orphan-warning file but still exits 0 (advisory)."""
        draft_dir = tmp_path / "draft"
        self._seed_pool(draft_dir, filename="citation_pool.json")
        (draft_dir / "manuscript.md").write_text(
            "## Results\n\nWe cite [UnknownAuthor2099] which is missing.\n"
        )

        proc = subprocess.run(
            [sys.executable, str(self.SCRIPT), "finalize", str(draft_dir)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        warnings = (draft_dir / "finalize_warnings.md").read_text()
        assert "UnknownAuthor2099" in warnings
        assert "orphan" in warnings.lower()
