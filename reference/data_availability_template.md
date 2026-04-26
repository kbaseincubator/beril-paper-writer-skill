# Data Availability Template

**Purpose.** This is the boilerplate the orchestrator (`paper_writer.sh`)
reads, fills with project-specific metadata, and writes to
`<DRAFT_DIR>/07_data_availability.md`. It satisfies ICMJE IV.A's
data-availability requirement (M4 validator) without burdening any
LLM prompt with the work — data availability is project-metadata-
driven, not a synthesis task.

The orchestrator collects the placeholder values from `state.json`'s
`source_artifacts`, the project's `RESEARCH_PLAN.md` (for declared
public accessions), and configure-time settings (code-repo URL).
Placeholders use `{name}` single-brace syntax to match
`AI_DISCLOSURE_TEMPLATE`.

If the orchestrator cannot fill a placeholder (e.g., no public
accessions are declared), it substitutes the canonical
"not-applicable" string for that placeholder. The validator (M4)
checks for the section's presence and length, not for any specific
accession; the template's `[X: TBD — confirm before submission]`
markers from §10.1's pattern apply here too for items the user must
finalize.

---

## Template body (write to `<DRAFT_DIR>/07_data_availability.md`)

The orchestrator writes everything below the `---` line below
(starting at `# Data Availability`) verbatim, with placeholders
filled.

---

# Data Availability

## Code

The analysis code for this manuscript is available at
{code_repo_url} (commit / tag {code_repo_ref}). The BERIL paper-
writer pipeline that produced this draft is documented at
https://github.com/ArkinLaboratory/beril-paper-writer-skill
(version {paper_writer_version}).

## Data sources

This analysis used data from the following BERDL databases, accessed
via K-BERDL:

{kberdl_databases_block}

Where `{kberdl_databases_block}` expands to a bulleted list, one
entry per database, each in the form:

> - `{database_name}` (snapshot {snapshot_date}; cross-reference
>   `<BERDL_REGISTRY_URL>/{database_name}` for schema and update
>   history).

If no K-BERDL databases were used, this section reads:

> No K-BERDL databases were accessed for this analysis. Data sources
> are listed under "Public accessions and external data" below.

## Public accessions and external data

{public_accessions_block}

Where `{public_accessions_block}` expands to a bulleted list, one
entry per accession declared in `RESEARCH_PLAN.md` (or absent the
section, declared inline by the user via configure). Each entry:

> - {data_source_label}: {accession_id} ({source_url})

If no public accessions are declared, this section reads:

> All data used in this analysis is derived from the K-BERDL
> databases listed above. No additional public accessions are
> referenced.

## Restricted access

{restricted_access_block}

The orchestrator fills this with one of three forms:

> _If no data is restricted:_
>
> All data sources used in this analysis are publicly available
> through the channels listed above.
>
> _If some data is restricted (e.g., user's institutional dataset
> not yet released):_
>
> A subset of data used in this analysis is held under
> {restriction_rationale} and is available from {contact_entity}
> upon reasonable request, subject to {access_conditions}. Public
> accessions and K-BERDL data above are unrestricted.
>
> _If access status is unclear:_
>
> [DATA RESTRICTIONS: TBD — confirm with co-authors and institution
> before submission.]

## Methods reproducibility pointer

For the software environment (package versions, statistical-test
implementations) used to produce these analyses, see Methods
§"Software and Versions" and §"Computational Environment". A
machine-readable manifest is at `{requirements_file_path}`
(extracted from notebook imports + `requirements.txt` /
`pyproject.toml` / `environment.yml` if present).

---

## Orchestrator notes

- Placeholders the orchestrator must fill from `state.json` /
  configure / project artifacts:
  - `{code_repo_url}` — from configure (default GitHub URL of the
    project repo if available; else `[CODE REPO: TBD — fill before
    submission]`)
  - `{code_repo_ref}` — commit SHA from
    `state.json.source_artifacts` if available; else `HEAD` or the
    `[X: TBD]` marker
  - `{paper_writer_version}` — read from `__init__.py`'s
    `__version__`
  - `{kberdl_databases_block}` — built from project artifacts; see
    extraction logic below
  - `{public_accessions_block}` — parsed from `RESEARCH_PLAN.md`
    section "Data sources" or "Datasets" if present
  - `{restricted_access_block}` — defaults to the
    "all publicly available" form unless `state.json` flags
    restricted data
  - `{requirements_file_path}` — relative path within the project,
    typically `requirements.txt` / `pyproject.toml` / `environment.yml`
- Length sanity check: the assembled section should be >100
  characters of substantive content (M4's threshold). Empty / one-
  line outputs are M4 fails — escalate to user-modify.
- The `[X: TBD]` markers are intentional and survive to the
  delivered manuscript; they fail M4 with a soft warning at assembly
  time per the dispatch table, signaling user-modify before
  submission.

## K-BERDL databases extraction logic (orchestrator implementation)

To fill `{kberdl_databases_block}`, the orchestrator scans:

1. `methods_provenance.md` — "Spark / K-BERDL Queries" section. Each
   `spark.sql("SELECT ... FROM <db>.<table> ...")` call names the
   database in the FROM clause.
2. `RESEARCH_PLAN.md` — "Data sources" or "Datasets" or
   "Inputs" section if present (project-author-declared databases).
3. `state.json.source_artifacts` — any file with extension `.parquet`
   typically lives under a K-BERDL mount; the path encodes the
   database.

Deduplicate by database name. Snapshot date comes from the database's
metadata (queryable via `<BERDL_REGISTRY_URL>/{db}/info` or, in v0.1,
from a configure-time-cached map).

If neither source produces any K-BERDL database name, fall through
to the "No K-BERDL databases were accessed" branch.
