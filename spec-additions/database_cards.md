# Database knowledge cards — design spec (v0.1 forward-looking)

**Status:** spec-additions (not yet merged into SPEC.md / LAYOUT.md /
the prompts). Drafted 2026-04-25 alongside the discrepancy-register
+ Word-comments-at-assembly specs.

The third leg of the trio that came out of the smoke-test review:
per-database knowledge cards that prompts read at runtime to ground
their domain awareness, with an orchestrator-driven elicitation
checkpoint for cache misses
and a version-fetcher tool for snapshot detection.

---

## Why this exists

The smoke-test runs surfaced two complementary gaps:

1. **The methods.v1 run** correctly cited K-BERDL databases by name
   (`kescience_fitnessbrowser`, `kbase_ke_pangenome`, `nmdc_arkin`)
   but couldn't say anything substantive about *what they are* — it
   regurgitated table names from the provenance file without
   characterizing the data sources. A reviewer of the manuscript
   asks "what's K-BERDL?" and the Methods section provides only
   what the project's own RESEARCH_PLAN happened to say.
2. **The data-availability template** has a `{kberdl_databases_block}`
   placeholder the orchestrator must fill, but no per-database
   metadata to fill it from. The template lists what *needs* to be
   said; the cards say what *is true* about each database.

Cards close both gaps: they are the per-database knowledge layer
the writer skill builds up over time. Static seeds ship with the
release; users contribute new descriptions when they encounter
unknown databases; multi-user contributions accumulate and (at
skill-release time) get curated into the shipped seeds.

This is also where the cross-project pitfall catalog (point #5 from
the architecture discussion) lives at the per-database granularity:
each card has a "Schema Gotchas" section that codifies the
recurring traps for that specific database. The pitfall catalog is
*the union of all cards' Schema Gotchas*, sliced by detection
pattern.

## Card schema (per-database markdown with frontmatter)

Cards live at `reference/databases/<database_name>.md`. One file per
database. Names match the database identifier as it appears in
Spark queries (no display-name aliasing in the filename — that
introduces a name-resolution layer the prompts don't need).

```markdown
---
name: kbase_ke_pangenome
display_name: K-BERDL Pangenome Database
card_version: 0.1
last_updated: 2026-04-25
description_source: shipped | user-provided | unknown
description_attribution: []
---

# kbase_ke_pangenome — K-BERDL Pangenome Database

## Purpose

One-line domain-purpose statement.

## Description

[Multi-paragraph human-readable description. Aimed at a manuscript
reviewer who has not used K-BERDL before. Names what the database
contains, who maintains it, what's in scope vs. not.]

## Tables

### gene_cluster

- **Purpose:** Core / accessory / singleton classification per gene
  cluster across 27,690 species.
- **Notable columns:** `gene_cluster_id`, `gtdb_species_clade_id`,
  `is_core`, `is_auxiliary`, `is_singleton`
- **Row count estimate:** 132M
- **Pitfalls:** GOTCHA-pangenome-1

### eggnog_mapper_annotations

- **Purpose:** Functional annotations (eggNOG OGs) per gene cluster.
- **Notable columns:** `query_name`, `eggNOG_OGs`, `seed_eggNOG_ortholog`
- **Row count estimate:** 93M
- **Pitfalls:** GOTCHA-pangenome-2

[... more tables ...]

## Schema Gotchas

### GOTCHA-pangenome-1: Singletons are a subset of auxiliary

- **Description:** In the pangenome schema, `is_singleton=true`
  implies `is_auxiliary=true` — both can be true for the same
  cluster.
- **Where it bites:** Filtering `WHERE is_auxiliary=true OR
  is_singleton=true` is redundant; counting the two as disjoint
  classes inflates the count.
- **Detection signature:** Spark queries that filter on both
  `is_auxiliary` and `is_singleton` as if mutually exclusive.
- **Standard mitigation:** Treat singletons as a refinement of
  auxiliary, not a separate class. Document the relationship in
  Methods text where relevant.

### GOTCHA-pangenome-2: eggNOG OGs are comma-separated; explode before joining

[...]

## Snapshot Convention

- **What to record:** GTDB release version (e.g., `r214`) and
  pangenome rebuild date.
- **Where to find:**
  - `SHOW DATABASES LIKE 'kbase_ke_pangenome%'` returns versioned
    aliases (the active version is the un-suffixed default; older
    versions are suffixed with `_r214`, `_r210`, etc.)
  - Metadata table: `kbase_ke_pangenome.metadata` columns
    `rebuild_timestamp` and `gtdb_release` (when present).
- **Fallback phrasing:** "at time of writing (no snapshot SHA
  recorded; pangenome typically rebuilds quarterly per GTDB
  release cycle)"

## Update Cadence

- **Typical frequency:** Quarterly, aligned to GTDB releases.
- **Last known update:** 2026-Q1 (r214 → r215 transition planned).

## See also

- `kescience_fitnessbrowser` (related; FB-pangenome integration via
  `fb_pangenome_link.tsv`)
- `nmdc_arkin` (orthogonal; community-level data layered on top)
```

The frontmatter fields:

- `name`: the database identifier as used in Spark queries.
- `display_name`: human-readable name for prose use.
- `card_version`: the card's own format version (bump when the
  schema below changes).
- `last_updated`: ISO timestamp of last edit.
- `description_source`: provenance of the Description section.
  - `shipped`: written by the skill maintainers, ships with the
    release.
  - `user-provided`: contributed by users via the orchestrator-
    driven elicitation checkpoint (see "Card elicitation"
    section below).
  - `unknown`: the card is a stub created on cache miss; the
    Description section says "[DESCRIPTION TBD: contribute via
    `/beril-paper-writer-elicit-database-card`]" or similar.
- `description_attribution`: list of contributors (when
  description_source = user-provided). Each entry is an
  anonymized handle + role + ISO timestamp + optional note. See
  "User attribution and accumulation" below.

## How prompts consume cards

Three integration points in the existing suite:

### 1. methods.v1 — Methods Datasets / Computational Environment subsections

When the provenance file's "Spark / K-BERDL Queries" section names
a database (e.g., `kbase_ke_pangenome`), `methods.v1` reads
`reference/databases/kbase_ke_pangenome.md` and uses:

- The Description for the Datasets subsection's database
  characterization. **If multiple descriptions exist** (separated by
  `---` from accumulated multi-user contributions before maintainer
  curation), use the first description in the file as the primary
  source AND register a `missing-snapshot-version` discrepancy entry
  with proposed-resolution-path `user-resolvable` and a note like
  "Card has N user-contributed descriptions awaiting curation; used
  the first one. User should clarify which is authoritative." This
  surfaces the multi-contrib state for human judgment without
  blocking drafting.
- The Snapshot Convention's Fallback phrasing for the [METHOD
  UNCLEAR: snapshot date] placeholder if no version was determined.
- The Schema Gotchas as material for inclusion in Methods only when
  a gotcha's mitigation is something the project's analysis
  performed (e.g., "All FB numeric values were cast from string to
  FLOAT" is the mitigation for the FB-string gotcha; if the project
  did this, Methods reports it).

If a card is missing for a referenced database, methods.v1 emits a
`missing-snapshot-version` register entry (per
`spec-additions/discrepancy_register.md`) with proposed-resolution
path `user-resolvable` — the orchestrator queues the elicitor.

### 2. citation_pool.v1 — pool building for tool / database papers

When the provenance file imports a tool (e.g., `pyspark`,
`sklearn.manifold`) or queries a database, citation_pool.v1 checks
whether the corresponding card has a "Canonical citation" section
(see "Optional sections" below). If present, the card's canonical
citation is added to the pool with `assessment: supports` and
`scope: direct` for Methods provenance.

### 3. The data-availability template — orchestrator-driven

The template loader (per LAYOUT §"Orchestrator capabilities") fills
`{kberdl_databases_block}` by walking the provenance file's
"Spark / K-BERDL Queries" section, deduping database names, and
looking up each card's `display_name` and Snapshot Convention's
"What to record" / version-fetcher result.

## Card elicitation: orchestrator pre-drafting checkpoint (NOT a prompt)

**Simplified 2026-04-26.** The original spec proposed a
`database_card_elicitor.v1` *prompt* invoked by the orchestrator
mid-drafting on cache miss. The reviewer flagged that this required
a pause-and-resume contract for subskill invocation that's
under-specified and architecturally awkward. The simplified design:
**elicitation runs as an interactive checkpoint in the orchestrator
itself, not as a subskill prompt**, and it runs *before* drafting
begins rather than mid-flow.

This resolves three issues from the original design:

1. The pause-and-resume problem disappears — the orchestrator is
   already an interactive shell process with stdin/stdout; it can
   prompt the user directly without subagent contortions.
2. Mid-drafting interruption disappears — drafting prompts no
   longer pause to ask about cards. They consume whatever cards
   exist when they run.
3. The elicitor's prompt-engineering complexity (anonymization,
   privacy warnings, response parsing) collapses into orchestrator
   shell logic.

### When the checkpoint runs

After `plan.v1` produces throughline candidates and the user picks
one, but before any section drafting (citation_pool, methods, etc.)
starts. At this point the orchestrator has already invoked
`extract_methods.py` (so `methods_provenance.md` exists), so the
list of databases referenced by the project is determinable.

### What the checkpoint does

1. **Scan for referenced databases.** Walk `methods_provenance.md`'s
   "Spark / K-BERDL Queries" section; extract database names
   (deduped) from each query.
2. **Cross-check against shipped cards.** For each referenced
   database, look up `reference/databases/<name>.md`:
   - **Card exists with `description_source: shipped` or
     `user-provided`** → no action; the card is usable.
   - **Card exists with `description_source: unknown`** (a stub
     from prior runs) → eligible for elicitation; user has
     contributed something previously, but it's still a stub.
   - **No card exists** → eligible for elicitation; create a stub.
3. **Branch on interactivity:**
   - **Interactive run** (default; orchestrator is invoked from a
     shell with stdin attached): for each eligible database, prompt
     the user with name, observed tables, 1–2 sample query
     excerpts, and ask for a one-paragraph description or
     "skip" / "I don't know." Capture response inline.
   - **Batch run** (orchestrator invoked with `--no-elicit` or in a
     non-interactive shell): write/update stubs without prompting;
     append `missing-snapshot-version` reframing-log entries
     (per the extended schema) for surfacing at end-of-run.
4. **Update cards.** For substantive responses: write/update the
   card's Description section, set
   `description_source: user-provided`, append to
   `description_attribution` list (anonymized handle by default).
   For "skip" or "I don't know": write/keep the stub with
   `description_source: unknown` and a
   `missing-snapshot-version` reframing-log entry.

### What this means for the implementation

- **No new prompt file.** The elicitation logic lives in
  `paper_writer.sh` (or a small Python helper module called from
  there). Probably ~50–80 lines total.
- **No `database_card_elicitor.v1.md`.** The original spec listed
  this as a future prompt; it's not needed.
- **Offline edit-and-PR remains the alternative path.** Users who
  want to contribute richer content (multi-paragraph descriptions,
  notes on related skills, table-of-tables documentation) edit the
  card files directly in their checkout and submit PRs to the skill
  repo. Documented in `reference/databases/CONTRIBUTING.md` (a
  small future doc).

### Interactive prompt format (orchestrator shell dialog)

```
Database `kbase_ke_pangenome` is referenced by this project but
I have no shipped knowledge of it. Observed tables: gene_cluster,
eggnog_mapper_annotations, gtdb_species_clade, gtdb_metadata.

Sample query: "SELECT m.root_og, m.gene_cluster_id FROM exploded e
JOIN target_root_ogs ON ..."

Please provide a one-paragraph description (what it contains, who
maintains it, what scope it covers), or type 'skip' or 'unknown'.
Your description will be saved to the card file at
reference/databases/kbase_ke_pangenome.md and committed when the
skill is next released. Press Ctrl-D when done.

> [user types description here]
```

### Configuration flags

- `--no-elicit` — batch mode; skips the checkpoint entirely; all
  unknown cards become stubs with reframing-log entries.
- `--attribute <name>` — explicit name attribution rather than
  anonymized hash (post-MVP).
- `--anonymous` — no attribution at all (post-MVP).

## User attribution and accumulation

Multiple users may contribute descriptions for the same database
over time. The card's `description_attribution` list grows; the
Description section accumulates (we keep all contributed
paragraphs separated by `---` in the body until curation).

At skill-release time, the maintainer (the package's release
process) reviews the accumulated user contributions, synthesizes a
single coherent Description, and re-publishes the card with
`description_source: shipped` and the `description_attribution`
list preserved as historical credit. Synthesis is a human-curation
step; we don't auto-summarize because:

1. Conflicting user descriptions need editorial judgment.
2. Anonymization needs review (a contributor's note may
   inadvertently include identifying detail).
3. Quality varies; not all contributions are useful.

Anonymization rules for `description_attribution`:

- **Default:** `<short-hash-of-contributor-id>-<YYYY-MM>` (e.g.,
  `a3f9b2-2026-04`). The orchestrator computes the hash from the
  user's `CONTRIBUTOR_ID` (typically a hash of `git config
  user.email` or a configure-time-set identifier).
- **Opt-in named:** if the user passes `--attribute <name>` to the
  elicitor, the literal name + role appears in the attribution
  list.
- **Opt-out:** if the user passes `--anonymous`, no attribution at
  all.

Privacy consideration: the user's literal description text is
stored verbatim in the card and is git-committed when the card is
published. The orchestrator should warn the user before they
contribute that the text will be public (the cards ship with the
skill).

## The version-fetcher tool (`tools/fetch_db_version.py`)

A Python helper (not a prompt). Invoked by the orchestrator before
drafting begins, when filling the data-availability template's
`{kberdl_databases_block}`.

**Signature:**

```bash
python3 tools/fetch_db_version.py <database_name> [--card <path>]
```

**Behavior:**

1. Read the card at
   `reference/databases/<database_name>.md` (or the path given
   via `--card`).
2. Walk the card's `Snapshot Convention - Where to find` directives
   in order. Each directive is one of:
   - `SQL: <query>` — execute against the configured Spark
     connection (if the orchestrator's invocation environment has
     one); return the first row's first column.
   - `META_TABLE: <table>.<column>` — query `SELECT <column> FROM
     <table> LIMIT 1`; return the value.
   - `FILE: <path>` — read the file; return its content (typical
     for projects that capture snapshot SHAs in a manifest file).
3. If any directive returns a value, format as
   `"<database_name> snapshot <value>"` and exit 0.
4. If all directives fail (no SQL connection, table missing, file
   missing), fall back to the card's
   `Snapshot Convention - Fallback phrasing` and exit 0 with the
   fallback text.
5. If the card is missing entirely, exit 1 with `"No card for
   <database_name>; trigger elicitor."`

The orchestrator catches exit 1 and routes to the elicitor.

This tool is **environment-dependent** — it requires the orchestrator
to know how to connect to K-BERDL when SQL directives are listed.
For the v0.1 of this design, the SQL directive is opt-in and
defaults to "skip if no connection." The fallback phrasing path is
the always-available baseline. Real version-fetching via SQL
becomes a Phase 4b/5 enhancement once the orchestrator's
connection-management is built out.

## Integration with the discrepancy register

Three register-entry types interact with cards:

| Register entry type | Card source | Cascade |
|---|---|---|
| `missing-snapshot-version` | Card has fallback phrasing only | Methods text uses "at time of writing" framing; data-availability template emits the fallback string; assembler-comment notes that no snapshot SHA was recorded |
| `pitfall-violation` | Card's Schema Gotchas | Register entry references the gotcha ID; methods.v1 emits Methods text describing the mitigation if the project applied it, OR a register entry if it didn't |
| `unverifiable-step` | Card's Tables (when project queries a table not in the card) | Register entry says "table `X` queried but not in card; card may need updating" — proposed-resolution-path `user-resolvable` (elicitor) |

The cards make the register more informative and the register makes
the cards keep growing — a virtuous loop.

## Seed cards to ship in v0.1

Five cards seed the `reference/databases/` directory:

1. **`kescience_fitnessbrowser.md`** — the Fitness Browser database.
   The most-used in BERDL paper-writing today.
2. **`kbase_ke_pangenome.md`** — the K-BERDL pangenome (27,690
   species). High-frequency in cross-organism analyses.
3. **`paperblast.md`** — already documented in the auto-memory's
   `reference_paperblast_architecture.md`; convert to card form.
4. **`nmdc_arkin.md`** — NMDC environmental community data;
   functional_dark_matter uses it; the schema gotchas (CLR-
   transformed wide format, taxonomy_dim resolution) recur.
5. **`pubmed.md`** — citation_pool.v1's primary external resource;
   establishes the canonical-citation convention for tool/database
   papers (this is also the entry point for the canonical citation
   store).

Each card is ~80–150 lines for a well-characterized database.
Total v0.1 contribution: ~500–700 lines of static reference
material under `reference/databases/`. Light to ship.

## Optional sections per card (for richer use)

- **Canonical citation** — the foundational paper for the database.
  citation_pool.v1 reads this when the database is referenced and
  adds the citation to the pool automatically with full 9-field
  metadata.
- **Common analyses** — patterns of use across BERDL projects (one
  paragraph per pattern). Helps methods.v1 phrase the
  "Analytical Workflow" subsection in domain-conventional ways.
- **Update path** — how the database is rebuilt and what changes
  between versions. Useful for explaining "we used the r214
  release" in Methods.

These sections are optional in v0.1; the orchestrator-driven
elicitation populates them only if the user provides relevant
content (interactive runs only). For richer content (multi-paragraph
descriptions, table-of-tables documentation), users can edit cards
offline and submit PRs to the skill repo — see
`reference/databases/CONTRIBUTING.md` (a future small doc).

## Implementation sequence

1. **(Phase 4a, ~2 hours)** Define the card markdown schema (this
   spec is the schema). Create `reference/databases/SCHEMA.md`
   that's a copy-pastable template for new card authors.
2. **(Phase 4a, ~2–3 hours)** Write the 5 seed cards. Most of the
   content for `kescience_fitnessbrowser.md` and
   `kbase_ke_pangenome.md` can be lifted from this project's
   RESEARCH_PLAN.md "Known pitfalls" + "Tables Required" sections.
3. **(Phase 4a, ~2 hours)** Write `tools/fetch_db_version.py`
   (~100 lines). Ship with the SQL directive disabled by default;
   fallback path is the always-on behavior.
4. **(Phase 4a, ~30 min)** Write `reference/databases/CONTRIBUTING.md`
   describing the offline edit-and-PR path for richer
   contributions.
5. **(Phase 4b)** Wire `methods.v1`, `citation_pool.v1`, and the
   data-availability template loader to consume cards. Modify the
   three prompts with a small "Database card consumption" section
   in their Discipline pass; modify the template loader to call
   `fetch_db_version.py`.
6. **(Phase 4b)** Wire the orchestrator's pre-drafting card-
   elicitation checkpoint into `paper_writer.sh` (~50–80 lines of
   shell + Python). Add the `--no-elicit` flag for non-interactive
   batch runs (which then proceed with the unknown-card stub
   behavior + reframing-log entries).

Total: ~6.5–7 hours of focused work for the static cards + version-
fetcher + CONTRIBUTING doc; another 3–5 hours for the prompt +
orchestrator wiring. About 1.5 days end-to-end.

This is ~2 hours less than the original spec's ~9-hour estimate,
because the elicitor prompt was eliminated.

## Open questions

1. **Card discoverability.** When `methods.v1` reads the provenance
   file and sees `kbase_ke_pangenome` in a Spark query, does the
   prompt need a registry of which cards exist, or does it just
   try `reference/databases/<name>.md` and handle file-not-found?
   Current proposal: file-existence-check is sufficient; no
   registry needed. Cards are name-keyed; the directory is the
   registry.
2. **Database name normalization.** What if a project uses
   `kbase_ke_pangenome.gene_cluster` in some queries but
   `kebpangenome.gene_cluster` (an alias) in others? Should the
   card system handle aliases? Current proposal: no — cards are
   name-exact; aliases are documented in the canonical card's
   "See also" section. If a project uses an alias, methods.v1 will
   not find the card and will register a missing-card entry.
3. **Card-versioning vs database-versioning.** The card has its
   own `card_version` (the schema + content version of the card)
   distinct from the database's snapshot version. Can a card
   describe multiple database versions? Current proposal: yes,
   via Description text — "this card describes K-BERDL pangenome
   r210 through r214." The Snapshot Convention is the same across
   versions (same lookup paths); the database's actual snapshot
   value is per-project.
4. **Conflicting user contributions.** What if two users contribute
   contradictory descriptions ("X is the canonical pangenome
   database for environmental microbiology" vs "X is a deprecated
   prototype superseded by Y")? The accumulator preserves both
   until curation; the maintainer resolves at release time.
   Whether the writer skill should surface contradictions in the
   meantime is post-MVP.
5. **Cross-skill card sharing.** Does `beril-adversarial` also need
   the cards? Probably yes for Methods-section review. Whether
   they're shared via a shared-skill subdirectory or vendored into
   each skill is a future design call.
