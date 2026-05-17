# v0.7.2 Punch List — Data Availability rewrite + reframe-repair escalation

**Filed:** 2026-05-05.
**Trigger:** Live test on `ibd_phage_targeting` draft surfaced three
classes of failure in `07_data_availability.md` and one in the
reframe-repair pipeline.

## Problem statement

### Data Availability failures (3 bugs)

The `phase_data_avail` orchestrator phase uses regex extraction on
`methods_provenance.md` and whole-document PMID scanning on
`RESEARCH_PLAN.md` + `REPORT.md`. The live output contains:

1. **Confabulated K-BERDL databases.** The regex `FROM <db>.<table>`
   matched `extract_methods.py`, `requirements.txt`,
   `research_plan.md` — interpreting filename dot-extensions as
   qualified SQL identifiers. Listed `extract_methods` as a database
   with table `py`. Root cause: when `## Spark / K-BERDL Queries` is
   absent from `methods_provenance.md`, the extractor falls back to
   scanning the entire file (line 2048:
   `body = m.group(1) if m else methods_provenance_text`).

2. **45 PMIDs listed as "accessions."** The `_ACCESSION_PATTERNS`
   regex for PMID (`\bPMID:?\s*\d+\b`) scans the full
   RESEARCH_PLAN.md + REPORT.md text and catches every literature
   citation. These are bibliography entries, not data accessions. A
   reviewer reading "Specific accessions referenced in the manuscript:
   PMID: 39188957 ..." would conclude 45 datasets were accessed.

3. **STRING listed as data source without actual usage.** The
   `_KNOWN_DATA_SOURCES` entry for STRING pattern-matches on the bare
   word in project text. This project doesn't use STRING data — the
   mention is incidental.

### Reframe-repair escalation gap (1 bug)

The live run detected 11 reframe-drift issues (ARI inflated 4.4×,
prevalence percentages wrong, wrong species named, fabricated AUC CI,
HMP2 posterior threshold misstated) but the `apply_reframing_repairs`
phase was a no-op. The reframer wrote advisory log entries but did not
escalate them to actionable repair tasks. The user saw the warnings
only in the terminal summary, not as automated fixes.

## Design: REPORT.md-sourced extraction with cross-walk

The REPORT.md `## Data` section has exactly the structured information
the Data Availability section needs:

- `### Sources` — markdown table with columns
  `Collection | Tables used | Purpose`. Contains the real K-BERDL
  collection names (`phagefoundry_strain_modelling`,
  `kescience_fitnessbrowser`, etc.) with their actual tables.
- `### Generated data` — markdown table with columns
  `File | Rows | Description`. 86 rows of derived artifacts with
  notebook provenance in descriptions.

**Cross-walk problem:** The REPORT.md Sources table lists all
collections the *project* touches. The paper uses a subset. Collections
marked "queued for NB04+" may or may not have been consumed by paper
time. Dumping the full Sources table overclaims data usage.

**Cross-walk solution:** Determine which notebooks the manuscript
actually cites (from Methods section notebook references like
`notebooks/NB13_...`), then include only the Sources table rows whose
Purpose or Tables mention those notebooks or whose Generated data
files are referenced by those notebooks. Collections with "queued"
Purpose and no corresponding manuscript reference are excluded.

**Template revision:** The `data_availability_template.md` needs
restructuring to separate K-BERDL collections, external public data
sources, and generated/derived data — with a cross-walk filter applied.

## Tiers

### Tier A — Fix the extraction pipeline (blocks everything)

**A1. Rewrite `_extract_kberdl_databases` to parse REPORT.md `### Sources` table.**

Replace the regex-on-provenance approach with a markdown table parser
that reads REPORT.md `## Data / ### Sources`. The table has a known
schema (`Collection | Tables used | Purpose`). Parse into structured
records.

- Input: REPORT.md text (already read by `cmd_extract_data_availability`)
- Output: list of `{"collection": str, "tables": str, "purpose": str}`
- AC: existing unit tests updated; new test with a synthetic REPORT.md
  that has the `## Data / ### Sources` table.
- AC: when `### Sources` section is absent, emit `[TBD]` markers (fail
  closed, not open).

**A2. Drop PMID from `_ACCESSION_PATTERNS`; add real accession types.**

PMIDs are literature identifiers, not data accessions. Remove the PMID
regex from `_ACCESSION_PATTERNS`. Keep: BioProject (`PRJ*`), GEO
(`GSE/GSM/GPL*`), SRA Study (`SRP*`), SRA Run (`SRR*`), BioSample
(`SAMN*`), GenBank (`[A-Z]{2}\d{6}`). Add: curatedMetagenomicData
version (`cMD v\d+`), PhageFoundry dataset identifiers.

- AC: existing PMID tests updated to expect no PMID output.
- AC: new test confirming real accession types (BioProject, SRA)
  still extract correctly.

**A3. Replace `_extract_named_data_sources` with REPORT.md-derived
external sources.**

Instead of pattern-matching `_KNOWN_DATA_SOURCES` against unstructured
text, extract external data sources from the REPORT.md `### Sources`
table rows that reference external URLs or public databases (entries
where Collection contains a URL or known external name like
`curatedMetagenomicData`, `HMP2`, `GTDB`, `PhageFoundry`). Also parse
explicit external-data mentions from the `### Generated data`
Description column (e.g., "HMP2 MetaPhlAn3 relative abundance").

- AC: STRING no longer appears unless it's in the Sources table.
- AC: curatedMetagenomicData and HMP2 appear (they're in the
  Generated data descriptions).

**A4. Parse `### Generated data` table into structured records.**

New helper `_extract_generated_data(report_text)` that parses the
`## Data / ### Generated data` markdown table
(`File | Rows | Description`). Returns list of dicts. This feeds the
cross-walk (Tier B) and a new "Derived data" block in the template.

- AC: parser handles `varies`, `—`, and integer row counts.
- AC: parser tolerates superseded/retracted annotations in
  Description (e.g., `**(superseded)**`, `**(retracted)**`).

### Tier B — Cross-walk filter (depends on A1, A3, A4)

**B1. Extract notebook references from manuscript Methods.**

New helper `_extract_cited_notebooks(methods_text)` that finds all
`NB\d+[a-z]?` identifiers referenced in the Methods section
(`01_methods.md`). Returns a set of notebook IDs (e.g.,
`{"NB00", "NB01b", "NB02", "NB03", ...}`).

- Input: the drafted `01_methods.md` text
- AC: extracts from both inline code references
  (`notebooks/NB13_phagefoundry...`) and prose references
  (`NB04b–e`, `NB05`).

**B2. Cross-walk Sources against cited notebooks.**

Filter the Sources table to only include rows where:
- Purpose mentions a cited notebook (e.g., "NB12+" matches if NB12 is
  cited), OR
- The collection is referenced in the methods_provenance.md SQL
  section (existing regex, but now restricted to the SQL section with
  fail-closed fallback per A1), OR
- A Generated data file's Description references both the collection
  and a cited notebook.

Exclude rows whose Purpose is "queued for ..." where the target
notebook is NOT in the cited set.

- AC: `kbase_ke_pangenome` (queued for NB04+, not used in paper)
  excluded from ibd_phage_targeting.
- AC: `phagefoundry_strain_modelling` (queued for NB12+, NB13 is
  cited) included.

**B3. Cross-walk Generated data against cited notebooks.**

Filter Generated data to rows whose Description or File path
references a cited notebook. Emit as a "Derived data artifacts"
block listing the key intermediate files the paper's analysis chain
produced.

- AC: `data/nb04_tier_a_candidates.tsv` (retracted) excluded unless
  NB04 is cited. `data/nb05_tier_a_scored.tsv` included because NB05
  is cited.
- Design question: include retracted/superseded files with annotation,
  or exclude them? Lean toward exclude — the paper shouldn't cite
  retracted intermediates in Data Availability.

### Tier C — Template and assembly (depends on B1-B3)

**C1. Revise `data_availability_template.md`.**

New template structure:

```markdown
# Data Availability

## Code

{code_block}

## Data sources — BERDL / K-BERDL

{kberdl_block}

## Data sources — external / public

{external_block}

## Derived data artifacts

{derived_block}

## Data accessions

{accessions_block}

## Restricted access

{restricted_block}

## Methods reproducibility pointer

{reproducibility_block}
```

- `{kberdl_block}` — filtered K-BERDL collections from Sources table.
- `{external_block}` — external public databases (cMD, HMP2, GTDB,
  PhageFoundry/Gaborieau, etc.) with URLs and citations.
- `{derived_block}` — key generated data files from the cross-walked
  Generated data table.
- `{accessions_block}` — real data accessions only (BioProject, SRA,
  GEO, BioSample, GenBank). Emits `[TBD]` if none found.
- Drop the current PMID dump entirely.

**C2. Update `phase_data_avail` in `paper_writer.sh`.**

Pass the new template variables from the rewritten extraction JSON.
Add `01_methods.md` as an input to the extraction command (needed for
the notebook cross-walk).

- AC: orchestrator passes `--methods-path <draft_dir>/01_methods.md`
  to `extract-data-availability`.

**C3. Update `_format_*` helpers for new block structure.**

Rewrite `_format_kberdl_block` and `_format_public_accessions_block`
to match the new template slots. Add `_format_derived_data_block` and
`_format_accessions_block`.

### Tier D — Post-checker (depends on C1-C3)

**D1. Add `check_data_availability.py` post-checker.**

Advisory post-checker (exit 0, surface warnings) that validates:

- No file-extension false positives in K-BERDL block (collection
  names ending in `.py`, `.txt`, `.md` → warning).
- No PMIDs in the accessions block (they're bibliography, not data).
- Every K-BERDL collection mentioned in Data Availability also
  appears in REPORT.md `### Sources` (catches confabulation).
- Every external data source mentioned has a URL (catches
  incomplete entries).
- `[TBD]` markers count — surfaces how many remain for user action.

- AC: checker runs after `phase_data_avail` in the orchestrator.
- AC: on the ibd_phage_targeting inputs, the checker would have
  caught all 3 bugs from this live test.

### Tier E — Reframe-repair escalation (independent of A-D)

**E1. Escalate critical reframe-drift entries to repair tasks.**

The `apply_reframing_repairs` phase currently dispatches only on
entries with `status: ESCALATED` in the reframing log. The reframer
writes entries as `ADVISORY` by default. Numerical discrepancies
(ARI inflated 4.4×, percentages wrong, wrong species named) should
auto-escalate when:

- The drift is a numerical mismatch (detectable: reframer entry
  contains "contradicts REPORT" or "vs REPORT's" or similar
  provenance-contradiction language).
- The drift magnitude exceeds a threshold (e.g., >2× inflation or
  >10 percentage-point discrepancy).

Fix: add an escalation classifier in `apply_reframing_repairs` that
promotes ADVISORY entries matching numerical-contradiction patterns
to ESCALATED, then dispatches them to the rewrite loop.

- AC: ARI = 0.58 vs REPORT's 0.131 would auto-escalate (4.4×
  inflation).
- AC: E. lenta prevalence 78% vs 70% would NOT auto-escalate
  (within 10pp threshold — advisory only).
- AC: fabricated AUC CI (no source in REPORT) would auto-escalate
  (provenance contradiction).

**E2. Surface unrepaired drift in `next_actions.md` with severity.**

Currently the terminal summary shows drift issues but
`next_actions.md` does not distinguish reframe-drift items from
other checklist items. Add a `## Reframe drift — unresolved` section
to `next_actions.md` that lists each unrepaired drift entry with:
- The specific numerical claim in the manuscript
- The contradicting value in REPORT.md
- Whether it was ADVISORY or ESCALATED
- The section and approximate location

- AC: the 11 drift issues from the ibd_phage_targeting run would
  appear in next_actions.md with actionable specificity.

## Dependencies

```
A1 ──┐
A2   │
A3 ──┼── B1 ── B2 ──┐
A4 ──┘        B3 ──┼── C1 ── C2 ── C3 ── D1
                     │
E1 (independent) ────┘
E2 (independent)
```

## Smoke test

After all tiers: re-run on ibd_phage_targeting with `--depth quick
--no-adversarial`. Verify:

1. K-BERDL block lists real collections (`phagefoundry_strain_modelling`,
   `kescience_paperblast`, etc.), NOT `extract_methods` / `requirements`.
2. No PMIDs in the accessions block.
3. STRING absent (not in REPORT.md Sources table).
4. curatedMetagenomicData and HMP2 present in external sources.
5. Derived data block lists key NB-referenced output files.
6. `check_data_availability.py` exits 0 with no warnings.
7. Reframe-drift numerical contradictions (ARI inflation, fabricated
   CI) appear in `next_actions.md` with severity annotation.

## Estimated cost

Tiers A-D are code changes to `paper_writer_helpers.py` +
`paper_writer.sh` + new checker + template. ~800-1200 LOC of new/
rewritten Python; ~50 LOC orchestrator. ~40-60 new unit tests.
No LLM cost (extraction is deterministic Python, not prompted).

Tier E is orchestrator + prompt-adjacent (escalation classifier is
heuristic, not LLM). ~200 LOC; ~10-15 new tests.

Wall clock: ~6-8h for A-D; ~2-3h for E. Can be parallelized (E is
independent).
