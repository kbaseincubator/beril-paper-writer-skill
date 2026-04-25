# beril-paper-writer — Specification (v0.1)

**Status:** v0.1 — community-facing design rationale. Implementation has not
started. Decisions captured here are load-bearing for the build; changes
require updates to [DECISIONS.md](DECISIONS.md).

This document explains *what* the skill does and *why* the design choices were
made. It is the document an external reviewer should read to decide whether to
trust an output of this skill. [LAYOUT.md](LAYOUT.md) covers internal
architecture (package shape, CLI, file paths). [README.md](README.md) is the
quick-start.

---

## 1. Purpose and scope

### 1.1 What this skill does

Takes a finished BERDL analysis project and produces a defensible, ICMJE-
conformant scientific manuscript draft from its artifacts (research plan,
report, notebooks, figures, references, optional adversarial review). Where
the underlying work cannot support a rigorous claim, the skill says so and
lists what would need to be done to bring it closer to publishability — it
does **not** paper over evidence gaps with fluent prose.

### 1.2 What this skill is NOT

- Not a writer of clinical-trial papers (CONSORT scope), systematic reviews
  (PRISMA scope), or diagnostic-test validation (STARD scope). v1 targets
  computational reanalysis of public datasets (STROBE-adjacent rigor norms).
- Not a journal-formatter. Output is generic IMRAD .docx; journal-specific
  templating is post-MVP.
- Not a figure generator. v1 reuses existing project figures only; missing
  figures become explicit gap-fill requests.
- Not a peer reviewer. The adversarial-review loop is for self-improvement
  before submission, not a substitute for journal review.
- Not a substitute for human authorship. ICMJE is unambiguous: AI tools
  cannot be authors. The skill auto-emits an AI-disclosure paragraph
  (per ICMJE January 2026); the user must fill in the actual author list,
  funding, conflicts, and ethics statements.

### 1.3 Who this skill is for

BERIL users who have completed an analysis and want a defensible first
manuscript draft, with the discipline to:

- Refuse to write what the evidence doesn't support.
- Surface alternative throughlines so the user, not the LLM, picks the story.
- Make every citation, every methods statement, every numerical claim
  traceable to a verified source (DOI/PMID for citations; notebook output
  or REPORT.md line for numbers).
- Hand off to harsh review and revise iteratively, with bounded cycles.

---

## 2. Design premises (what we're optimizing for)

In rough order of priority:

1. **Honesty.** The manuscript must not fabricate citations, methods, or
   numerical claims. Where evidence is thin, the writer says so. A paper-
   writer that produces fluent-sounding manuscripts about thin work is more
   dangerous than one that refuses; bad work made to look credible is the
   primary failure mode to design against.
2. **Auditability.** Every claim must trace to (a) a project artifact
   (notebook output, REPORT.md line, figure file) or (b) a verified citation
   in the citation pool. The reframing-log and citation-map make the trace
   explicit and human-readable.
3. **User judgment over LLM judgment** at the load-bearing decisions:
   throughline pick, gap-fill take/defer, accepting unfixable issues as
   limitations.
4. **Bounded cost and latency.** Target $5–$15 per full run, 15–40 minutes.
   Every loop has a hard cap. No infinite revise-review cycles.
5. **Reuse over generation.** Reuse existing project figures, existing
   methods text from REPORT/notebooks, existing citations from references.md.
   Generate prose only for what doesn't already exist.

---

## 3. Inputs (what the skill expects)

From the project directory (per the BERIL convention `projects/<id>/`):

- **`RESEARCH_PLAN.md`** (required) — the planned hypotheses and approach.
  Used to detect gap between plan and what was actually done; used in
  Introduction/Methods context.
- **`REPORT.md`** (required) — the canonical synthesized findings. The
  manuscript MUST NOT silently contradict REPORT; reframing is logged
  explicitly in `reframing_log.md`.
- **`README.md`** (optional) — project-level context, often includes
  one-paragraph summary.
- **`REVIEW.md`, `ADVERSARIAL_REVIEW_*.md`** (optional but strongly used) —
  prior reviews flag known weaknesses the manuscript must engage with rather
  than restate as findings.
- **Notebooks** (`*.ipynb`) — source of truth for methods (algorithms,
  parameters, package versions) and numerical claims. Methods section is
  *extracted* from notebooks, not generated from a free prompt.
- **Figures** (`figures/*.png` or similar) — reused as-is for the manuscript;
  selection logic chooses 4–8 of typically 30+ project figures.
- **`references.md`** (optional) — pre-existing citations. If absent, the
  skill builds a citation pool from scratch via literature search.

### 3.0.1 RESEARCH_PLAN.md expected structure

The Methods agent reads `RESEARCH_PLAN.md` to extract design intent
(per §6.3). Because the plan is user-authored and varies in style, the
writer expects at minimum these sections (any header level, fuzzy match
on synonyms):

- **Hypothesis / Research Question** — what is being asked
- **Planned Methods / Approach** — how it will be answered
- **Analysis Plan / Statistical Methods** — what tests, what corrections,
  what success criteria (where applicable)

If `RESEARCH_PLAN.md` is present but lacks these sections, the Methods
agent reports `"RESEARCH_PLAN.md does not contain expected sections
(Hypothesis, Planned Methods, Analysis Plan); proceeding with
methods-only grounding from notebooks. Design rationale will be
incomplete in Methods."` and treats the project as if no plan-level
intent were available. This is a soft warning, not a fail.

### 3.0.2 Empty or near-empty REPORT.md

If `REPORT.md` exists but is empty or under ~500 characters (no
synthesized findings, only headers, or a stub):

- Plan-phase triage MUST classify the project as EXPLORATORY (overriding
  any other heuristic), per §3.1.
- The writer emits a gap-fill request (type: `analysis-request`):
  *"REPORT.md is empty or stub-only. Please run `/synthesize` to produce
  a structured REPORT, or add narrative findings manually before
  drafting can proceed past Plan phase."*
- The writer pauses; on `continue` it re-reads `REPORT.md` and proceeds
  if content is now present. If the user invokes `continue` without
  populating REPORT, the writer offers `--mode report` as the only
  available output (REPORT mode does not require a structured findings
  section, only a notebooks-as-source narrative).

### 3.1 Project-quality triage

Before drafting, the skill classifies the project (this becomes part of the
Plan-phase user gate):

- **STRONG** — REPORT has clear research question, methods, numbered findings
  with effect sizes / CIs / FDR-corrected p-values, explicit limitations.
  Examples in our test corpus: `functional_dark_matter`, `cf_formulation_design`.
  → Proceed to drafting.
- **THIN** — Novel finding but methodological gaps, incomplete analyses
  (e.g., "Act II deferred"), sparse statistical reporting.
  Example: `genotype_to_phenotype_enigma` (Act I publication-ready as a
  dataset/methods paper; Act II not yet).
  → Surface scope-down options to user (write narrower paper on what's
  actually done; or wait for additional analyses).
- **EXPLORATORY** — Proof-of-concept, single analysis layer, no validation,
  small n.
  → Emit warning: "This project is EXPLORATORY-tier. The output will be an
  exploration report, not a research paper." Proceed with drafting using
  the **exploration-report template** (§3.2): same IMRAD shell, but with
  explicit title/abstract framing ("Preliminary exploration of..." /
  "Exploratory analysis suggests..."), expanded Limitations section, and
  a substantive Future Work section enumerating what would be needed to
  reach publishability. Honest reporting of what was attempted and what
  was learned — including null and negative findings — is the goal.
  Refusing to draft would lose the value of exploratory work; producing
  a paper that overclaims the evidence would be worse than refusing.
  This middle path is the v0.1 design.

The triage verdict is shown to the user before drafting begins.

### 3.1.1 What "evidence-strength framing" means

"Framing" is *prompt-driven*, not a mechanized rule. Each per-section
agent (plan, methods, results, discussion, intro, abstract) receives
the tier as a parameter; the system prompt for each section adjusts:

- **Language conservatism** — declarative ("X causes Y") vs scoped
  ("X correlates with Y in our cohort") vs preliminary ("X may relate
  to Y")
- **Claim certainty** — whether assertions are flagged as
  hypothesis-tested vs hypothesis-generating
- **Discussion scope** — engages contrasts with prior work (STRONG)
  vs engages with caveats (THIN) vs hypothesis-generating only
  (EXPLORATORY)
- **Limitations weight** — required (STRONG), required and expanded
  (THIN), substantially expanded with explicit "what would be needed
  for rigor" (EXPLORATORY)

Because framing is prompt-driven, the exact language varies with model
and prompt revision. The §3.2.1 / §3.2.2 tables describe the *intent*
per tier; per-section system prompts encode the language patterns.
Mechanical validators (M1–M10) do not enforce framing.

### 3.2 Output mode and tier (orthogonal axes)

The writer has two independent dimensions:

- **`--mode`** controls the *output shape*:
  - `paper` — IMRAD research paper with claims, abstract, references,
    discussion. Aimed at journal submission.
  - `report` — structured activity report describing what was done
    and what was observed. No claims-of-significance framing, no
    abstract-as-claim, no discussion-as-interpretation. Aimed at
    internal documentation, handoff, lab-notebook write-up.

- **Tier** (STRONG / THIN / EXPLORATORY) controls the *evidence-strength
  framing within that shape*. Determined by triage; influences how
  claims are framed and how aggressive the limitations / future-work
  discussion is.

Default mode is determined by tier but is overridable:

| Tier | Default mode | Notes |
|---|---|---|
| STRONG | `paper` | User may pick `report` for internal documentation use |
| THIN | `paper` (scope-narrowed) | User may pick `report` if scope-down is too restrictive |
| EXPLORATORY | `report` | User may pick `paper` and accept the exploration-paper template (see §3.2.2) |

### 3.2.1 `--mode paper` template per tier

| Section / Check | STRONG | THIN | EXPLORATORY (paper override) |
|---|---|---|---|
| Title framing | declarative ("X causes Y") | scoped ("In our cohort, X correlates with Y") | preliminary ("Preliminary exploration of X–Y relationship") |
| Abstract conclusions | substantive claims | narrower claims | observations + caveats |
| Methods rigor | M-tier validators all in force | M-tier in force; gaps logged | M-tier in force; expect more `[NEEDS CITATION]` and limitations |
| Results | full | scope-narrowed | includes null/negative findings prominently |
| Discussion novelty | engages contrasts with prior work | engages with caveats | hypothesis-generating, not hypothesis-testing |
| Limitations | required, substantive | required, expanded | substantially expanded; explicit "what would be needed for rigor" |
| Next Steps | optional but recommended | recommended | required, structured (data needs / analysis needs / experimental validation) |

### 3.2.2 `--mode report` template (any tier)

Activity-report structure (NOT IMRAD):

1. **Project Summary** — one paragraph: what was the question, what
   was done, what was observed.
2. **Background and Question** — context from `RESEARCH_PLAN.md`; no
   field-positioning beyond what the project itself states.
3. **What Was Done (Methods)** — narrative of the actual analysis,
   grounded in notebooks per §6.3. Same provenance discipline as
   paper mode; no methods-fabrication.
4. **What Was Observed (Findings)** — descriptive presentation of
   results; figures embedded; numerical claims traceable to artifacts.
   No abstract-of-significance framing; the reader draws their own
   conclusions.
5. **Observations and Open Questions** — what stood out; what's
   unclear; what would be worth investigating next. NOT a "Discussion"
   section: no novelty-positioning, no field-context, no claims of
   significance.
6. **Limitations and Caveats** — honest scope of what was and wasn't
   examined.
7. **Next Steps** — what would need to happen to reach a paper-grade
   claim, or what other directions look promising.
8. **Appendices** — any analyses that didn't fit the main narrative,
   per §1.

Citation pool, methods grounding, and validator subset (subset of
M1–M10) still apply. Validators that don't apply to report mode (e.g.,
M2 Structured Abstract — reports don't have abstracts) are skipped;
the writer's run log records which validators were applied vs. skipped.

All three tiers + both modes go through the same drafting pipeline
(Plan → Throughline → Drafting → Citation pool → Review → Rewrite);
the *output template* and *framing emphasis* shift, not the underlying
mechanism.

### 3.3 THIN-tier scope-down mechanism

For THIN-tier projects, the Plan-phase agent extracts both:

- **Broad throughline candidates** (the typical 2–3 from §4) that cover
  the project's stated scope, and
- **One narrowed-claim candidate** that scopes down to the strongest
  paper-ready sub-claim the project actually supports.

The narrowed candidate is presented in the same throughline-pick UI as
the broader options, with explicit annotation: *"This is the strongest
sub-claim that meets paper-grade rigor; the broader candidates require
addressing the gaps listed in `analysis_requests.md`."* The user picks
as usual. If they pick the narrowed candidate, the writer proceeds in
`--mode paper`; if they pick a broader one, the writer continues in
`--mode paper` but flags that gap-fills will be needed before any
adversarial review will accept it. If they want neither, `--mode report`
is offered as the third option.

This makes the THIN-tier UX an explicit choice, not a silent scope-down.

---

## 4. The throughline mechanism (the highest-risk step)

### 4.1 Why this matters

The throughline — the story the paper tells — determines what gets written.
An LLM left to auto-pick will favor narratives that are easy to write
(linear, single-hypothesis, dramatic) over narratives that fit the data
(often messy, multi-hypothesis, partial). This is the single most important
place to keep humans in the loop.

### 4.2 Mechanism

The Plan-phase agent extracts 2–3 candidate throughlines from the project
artifacts. Each candidate has:

- **Claim** — the central scientific assertion (one sentence).
- **Evidence map** — for the candidate's main sub-claims, which project
  artifact (notebook, figure, table) supports it, and at what strength
  (✓ direct / ⚠ partial / ✗ contradicts / ◇ orthogonal).
- **Weakness inventory** — what gaps the candidate has if used; what the
  rebuttal would be.
- **What the paper would NOT include** if this candidate is chosen.

These are written to `throughline_candidates.md` using this template
(per candidate; separator `---` between candidates):

```markdown
## Candidate TL{N}: {one-sentence claim}

**Evidence map:**

| Sub-claim | Source | Strength |
|---|---|---|
| {sub-claim 1} | notebook X cell Y | ✓ direct |
| {sub-claim 2} | REPORT.md §{n} | ⚠ partial |
| ... | ... | ... |

**Weakness inventory:**

- Gap: {what's missing}
- Rebuttal a sharp reviewer would offer: {anticipated critique}

**What this paper would NOT include if this is chosen:**

- {finding A — orthogonal; → appendix or out}
- {finding B — contradicts; → appendix with discussion}

---
```

The skill then **pauses** and prompts the user to pick one (or to
provide an alternative). On `beril-paper-writer continue`, the chosen
throughline is written to `00_throughline.md` (same template, single
candidate) and drafting proceeds.

`--throughline auto` opts into the writer's choice (highest-evidence-density
candidate). This is for non-interactive runs; default is `interactive`.

### 4.3 Throughline as load-bearing constraint

After a throughline is chosen:

- Every drafted claim must trace to the throughline's evidence map.
- Claims supported by the project but irrelevant to the throughline go to
  appendices (not deleted; demoted).
- The adversarial reviewer's `--type paper` mode reads the throughline and
  flags drift between claim and evidence map.

---

## 5. The gap-fill / analysis-request loop

### Terminology

**Gap-fill request** is the umbrella term for any item the writer
identifies as missing from the project but needed for the chosen
throughline. Each gap-fill has a **type**: `analysis-request`,
`figure-request`, `data-request`, `citation-request`, or
`validator-escalation`.

All gap-fills funnel into a single file, `analysis_requests.md`, in
the draft directory. The filename reflects the most-common type, but
the file contains all gap-fill types. (The name is sticky for backward
compatibility; renaming is post-MVP.)


### 5.1 The problem

While drafting, the skill identifies evidence gaps the chosen throughline
needs filled (e.g., "claim X requires effect size with CI; only point
estimate present" or "Methods don't specify multiple-testing correction").
Some gaps can be filled by the user re-running BERIL; others require
additional data; others are unfillable and become limitations.

### 5.2 Mechanism

Identified gaps are written to `analysis_requests.md` as structured items:

```markdown
## REQ-1: [analysis-request | figure-request | data-request | citation-request]

**What's needed:** Bonferroni-corrected p-values for the 343 conditions
in Finding 6 (currently uncorrected).

**Why:** Finding 6 claims 95 of 343 conditions show AUC > 0.75. At α=0.05
uncorrected, ~17 are expected by chance. Without correction, the claim
overstates the result.

**Where it lands:** Results §3.2; Methods §"Statistical analysis"; Limitations
if not addressed.

**Suggested action for BERIL:** *(markdown snippet the user can append
to `RESEARCH_PLAN.md` as a new analysis task, or paste as a natural-
language prompt into a fresh `/berdl_start`-initiated session)*

> Re-run the Finding 6 analysis (`02_gapmind_concordance_phylo.ipynb`,
> cell 14) with explicit FDR control: apply Benjamini-Hochberg correction
> across the 343 conditions; report corrected p-values and the
> count of conditions surviving q < 0.05. Update REPORT.md §"Finding 6"
> with the corrected counts and the corrected significance threshold.

**Note on format:** the writer emits a markdown snippet, not a literal
slash command. `/berdl` is a SQL-query skill in BERIL, not a generic
"do this analysis" entry point. The actual BERIL workflow is: the user
extends `RESEARCH_PLAN.md` with the new task, then a `/berdl_start`-
initiated Claude session executes it via the appropriate skills
(`/berdl-query`, notebook editing, etc.). The writer's job is to
articulate the request precisely; routing it through BERIL is the
user's call.

**If declined:** finding's claim must be re-scoped (e.g., "we observed N
conditions with elevated AUC, of which ~N×0.05 are expected by chance").

---
**Status:** pending  ← user updates: pending | taken | deferred | dropped
```

### 5.3 Hard caps

- Max 2 gap-fill rounds per draft (a "round" = one user response on the
  request file).
- Max 5 requests per round per type (analysis / figure / data / citation).
- If after round 2 there are still requests pending, the writer proceeds
  with a degraded scope and folds remaining gaps into Limitations or
  Next Steps.

### 5.4 Gap-fill response protocol (what the user actually does)

The writer pauses after writing `analysis_requests.md` and exits. The
user reviews the file and edits the **`Status:`** line of each request
to one of:

- `pending` (default; the writer will treat this as "still waiting")
- `taken` — user has run / will run the suggested BERIL command; new
  artifact will appear before resume
- `deferred` — defer to next paper draft / next steps; do not include in
  this draft
- `dropped` — request was not warranted; do not re-suggest in this draft
- `manual: <note>` — user resolved manually; the note explains how (e.g.,
  `manual: added correction in REPORT.md §3.2`)

**Status line grammar (load-bearing for parser):**

```
Status: <value>

where <value> matches one of:
  pending
  taken
  deferred
  dropped
  manual: <free-form note, single-line>

Parsing rule (line-based):
  ^Status:\s+(pending|taken|deferred|dropped|manual:\s+.+)$

Edge cases:
- `Status: manual:` (colon, no space, no note) → invalid; treat as
  `pending` and emit warning to user
- Multiline notes are NOT supported in v0.1; users wanting longer
  rationale put a one-line summary as the note and add the full text
  as a markdown blockquote on the next line(s) (informational only;
  the writer does not parse the blockquote)
- Anything not matching the regex → treat as `pending` and warn
```

The user then invokes `beril-paper-writer continue <draft_dir>`. The
writer parses statuses, hash-diffs source artifacts (per §5.5), and
proceeds:

- `taken` requests with new artifacts → integrate
- `taken` requests without new artifacts yet → emit warning; user likely
  meant to defer
- `deferred` / `dropped` → no action
- `manual` → re-extract from the noted location; add the note to
  `reframing_log.md`

Status entries are append-only across rounds: a `dropped` request from
round 1 is not re-surfaced in round 2 even if it would otherwise apply.

### 5.5 The intercalation problem (between BERIL and the writer)

### 5.4 The intercalation problem (between BERIL and the writer)

When the user invokes `/berdl` to address an analysis request, BERIL writes
new artifacts to the project directory while the paper draft is paused.
On `beril-paper-writer continue`:

1. The writer hashes all source artifacts (sha256 + mtime of every
   `RESEARCH_PLAN.md`, `REPORT.md`, `notebooks/*`, `figures/*`,
   `references.md`) and compares against `state.json`'s recorded hashes
   from the last build.
2. New / changed artifacts are reported to the user explicitly:
   `"Since last run: 2 new files (paths), 1 changed file (path), 3
   unchanged. Proceed?"`
3. Manuscript files (`manuscript.md`, `01_methods.md`, etc.) are also
   hashed. If the user hand-edited any, the writer **does not overwrite**
   — it offers a side-by-side diff and asks which to keep (user's edits,
   writer's regeneration, or merged).
4. If new artifacts materially change the throughline's evidence map
   (e.g., a previously unsupported claim is now supported, or vice versa),
   the writer flags `"Throughline candidate X may need re-evaluation
   given new evidence Y"` and asks the user to confirm before propagating.
   The writer never silently rebuilds the throughline. The reevaluation
   outcome (confirmed-still-valid / re-picked / abandoned) is recorded
   in `state.json`'s `throughline.reevaluations[]` array (see LAYOUT
   state.json schema).

This explicit-on-resume behavior is the answer to "how do we not lose the
thread when paper-writing pauses for BERIL analysis." Silent integration is
forbidden.

### 5.6 reframing_log.md schema

Append-only log of every honest reframing the writer made: deviations
from REPORT.md, validator failures accepted as limitations, design
discrepancies between RESEARCH_PLAN and notebooks, manual user
overrides. One markdown entry per event:

```markdown
# Reframing Log

## Entry {N} — {ISO timestamp} — type: {reframing | validator-escalated | accepted-limitation | plan-execution-discrepancy | manual-override}

- **Issue:** {what was found / changed}
- **Source:** {REPORT.md §X | validator M_n | notebook X cell Y | RESEARCH_PLAN §Z}
- **Manuscript impact:** {which section(s); what language was added}
- **Resolution:** {auto-fixed | escalated | accepted as Limitations | user-modified}
- **Note:** {context for future reviewers; one paragraph max}

---
```

Never edited or deleted by the writer; users may add their own entries
for transparency, but the writer treats user entries as informational
only (does not parse them).

---

## 6. Drafting: agent decomposition and section order

### 6.1 Section assembly order

Drafted in this order (information flows forward):

1. **Methods** — extracted from notebooks + RESEARCH_PLAN. Foundational;
   constrains how Results can be reported.
2. **Results** — extracted from REPORT.md, organized to align with the
   throughline. Numerical claims cross-checked against notebook outputs.
3. **Discussion** — written against the throughline's claims, with
   field-context citations from the verified pool.
4. **Introduction** — written last among body sections, after Methods/
   Results/Discussion are settled. Sets up exactly what the paper delivers,
   no more, no less.
5. **Abstract** — written after the body is stable; constrained to claims
   demonstrable from the body.
6. **Conclusion / Limitations / Next Steps** — limitations include any
   unfilled gap-fills and any unfixable adversarial-review findings.

### 6.2 Per-section agents

Each section has a dedicated subagent with a system prompt scoped to:

- What artifacts it reads (Methods reads notebooks + RESEARCH_PLAN; Results
  reads REPORT + notebooks for numerical verification; etc.).
- What constraints apply (Methods: extract, don't fabricate; Results:
  every number must be greppable in REPORT or a notebook output; etc.).
- What output format it produces.

Subagents do NOT see the full project artifact set; each gets only what
its section needs. This keeps each call's context focused and reduces
the chance of cross-contamination between sections.

### 6.3 Methods grounding (the second-highest-risk step)

The fastest way to make this skill dangerous is to let it write fluent
methods prose ("we performed Welch's t-test with α=0.05") that wasn't
actually done. Methods has two complementary sources, both required:

**Intent — `RESEARCH_PLAN.md`.** The plan documents *why* a method was
chosen: hypothesis structure, prespecified statistical tests, sample
size justification, dataset selection criteria, anticipated controls.
The plan provides the "design rationale" that notebooks-as-code rarely
articulate.

**Execution — notebooks + scripts.** The actual code as run.

The Methods agent:

- Reads `RESEARCH_PLAN.md` first to extract design intent and prespecified
  analyses.
- AST-walks Python notebooks (`.ipynb`) and any standalone `.py` scripts
  in the project to extract: function calls, package versions, parameter
  values, data filters, statistical tests actually invoked, software
  imports.
- Cross-checks: does the executed analysis match the prespecified plan?
  Discrepancies are noted in `methods_provenance.md` (e.g., "Plan
  prespecified Welch's t-test; notebook implements Mann-Whitney U" — this
  is not an error per se, but the Methods section must report what was
  actually done, and the discrepancy is logged for transparency).
- Forbidden from claiming any method that cannot be pointed to either
  (a) in the plan as prespecified-but-deferred-with-rationale, or (b) in
  executed code. Implied-but-not-explicit steps are flagged as
  `[METHOD UNCLEAR: see notebook X cell Y]` for user resolution.
- Produces `methods_provenance.md` listing each Methods statement
  alongside its source: plan section, notebook+cell, or script+line.

**Notebook format scope for v1:** `.ipynb` only (Python kernels). `.Rmd`,
`.qmd`, and `.R` scripts are post-MVP — they appear infrequently in the
BERIL test corpus, and AST extraction differs by language. If a project
contains non-`.ipynb` analysis files, the Methods agent reports them as
`[METHOD SOURCE NOT EXTRACTED: <path>]` and asks the user to either
provide a Methods snippet manually or wait for v0.2.

**Spark / cluster-execution caveat:** when notebooks contain `spark.sql`
or remote-execution calls (some BERIL projects use this for K-BERDL
queries), the local AST gives the *query string* but not the actual
execution path. The Methods agent extracts the query and labels the
execution context as "K-BERDL via Spark" without claiming details about
remote execution it cannot verify locally.

**Manual-snippet protocol for non-.ipynb sources.** When the Methods
agent encounters non-`.ipynb` files (e.g., `.R`, `.Rmd`, `.qmd`, plain
`.py` scripts):

1. Report each path as `[METHOD SOURCE NOT EXTRACTED: <path>]` in the
   draft and in `methods_provenance.md`.
2. Emit a gap-fill request (type: `analysis-request`) asking the user
   to either (a) add a "Manual Methods (non-.ipynb sources)" section to
   `RESEARCH_PLAN.md` with a methods snippet for each unhandled file,
   or (b) convert the analysis to `.ipynb` for v0.1 to extract.
3. On `continue`, the writer scans `RESEARCH_PLAN.md` for the new
   section and incorporates each snippet into `01_methods.md` in place
   of the corresponding `[METHOD SOURCE NOT EXTRACTED]` placeholder.
   `methods_provenance.md` records the source as `RESEARCH_PLAN.md
   §"Manual Methods" (user-provided)`.

If after one gap-fill round the user has not provided a snippet, the
unhandled files become a Limitations entry ("Methods for `<path>`
were not extracted; reproducibility may be limited; see Next Steps").

### 6.4 Citation pool and prose generation

To prevent citation hallucination during prose generation:

1. Before drafting begins, a literature-scan agent builds a **verified
   citation pool**: for each candidate citation, DOI / PMID checked via
   `WebSearch` or BERIL's PubMed MCP, full 9-field metadata captured
   (Authors / Year / Title / Venue / DOI / ID / Studied / Finding / Scope
   alignment / Assessment — same discipline as the adversarial reviewer).
2. The pool is capped at **80 references** for v1. If more are needed,
   the writer fails loud and asks the user to scope down the discussion
   or add a gap-fill request.
3. During prose generation, the writer is constrained to draw citations
   only from the pool. Claims that need citations not in the pool either
   (a) trigger an additional pool-build cycle for the missing topic
   (one round only), or (b) are flagged as `[NEEDS CITATION]` for the
   user to resolve.
4. The pool serializes to both `references.md` (human-readable, numbered
   in order of first citation) and `bibliography.bib` (BibTeX for the
   adversarial reviewer's expectations and for downstream journal
   submission).

### 6.4.1 What happens when the pool is exhausted mid-Discussion

The 80-reference cap is intended as a budget control, but Discussion
sections may exceed it (rich field engagement is aspirational per §7.2).
On exhaustion, the writer flags every remaining `[NEEDS CITATION]`
claim in Discussion and presents the user with a structured choice:

1. **Scope down (default)** — drop the `[NEEDS CITATION]` claims from
   Discussion, retain the rest. Lowest cost; smallest Discussion.
2. **Spend a gap-fill round on a citation-request** — costs one of the
   two available gap-fill rounds (§5.3), adds 5–15 verified citations
   to the pool. Heavier; may starve later gap-fills.
3. **Accept-as-limitation** — fold the unsupported claims into
   Limitations as "claims that would require additional literature
   engagement." Documents the gap honestly without inflating the pool.

The default is (1) unless the user explicitly chooses (2) or (3) on
the pause-and-resume prompt. This decision is recorded in
`reframing_log.md`.

---

## 7. Reporting-standards subset (mechanized vs aspirational)

This is the load-bearing reporting discipline. The full extraction lives
at [reference/reporting-standards-extract.md](reference/reporting-standards-extract.md);
this section is the curated subset we will actually enforce.

### 7.1 Mechanized validators (10 items, hard checks before output)

Every draft is validated against these before being considered complete.
A failure on any item blocks the draft from being marked "ready" and is
reported to the user.

| # | Check | Source | Validator |
|---|---|---|---|
| M1 | All ICMJE V.A IMRAD sections present (Title page, Abstract, Intro, Methods, Results, Discussion, References) | ICMJE IV.A.3 | Section-header presence check |
| M2 | Structured abstract (Background/Objective, Methods, Results, Conclusions) | ICMJE IV.A.3.b | Subsection check within Abstract |
| M3 | AI-disclosure paragraph present, names tool + version + task | ICMJE V.A (Jan 2026) | Regex on Methods or Acknowledgments |
| M4 | Data-availability statement present, contains URL or accession or explicit-restriction rationale | ICMJE IV.A | Section length > 100 chars + URL/accession regex |
| M5 | Methods names statistical tests with software + version (e.g., "scipy.stats.fisher_exact, SciPy 1.11") | SAMPL §1; ICMJE IV.A.3.d | **Soft-warning only in v0.1** (see §7.1.2) |
| M6 | Multiple-testing correction declared if ≥5 distinct tests reported | SAMPL §3 | p-value regex count × correction-method regex |
| M7 | Numerical claims have n + effect size + 95% CI + exact p-value (or Bayesian equivalent), not bare percentages | SAMPL §2 | Prose walk; flag bare-percentage claims |
| M8 | Counts (n) precede derivatives (%) — "42/156 (26.9%)" not "26.9%" | ICMJE IV.A.3.e | Regex |
| M9 | Limitations section present with substantive content (>150 chars) | ICMJE IV.A.3.f | Section length |
| M10 | Every citation in prose appears in references.md AND bibliography.bib | basic integrity | Cross-grep |

### 7.1.1 Four escalation paths for an M-tier failure

A failed validator does not always have an in-writer fix. Each failure
takes one of four paths:

1. **Auto-fix.** The writer can resolve the issue without external work.
   Examples: M1 (missing IMRAD section header), M2 (Abstract missing a
   subsection structure), M3 (AI-disclosure paragraph absent — emit
   the standard template). Writer attempts a single fix pass on the
   offending section; on success, re-runs the validator.
2. **Escalate as analysis-request.** The fix requires new analysis the
   writer cannot perform. Examples: M6 failure (10+ p-values reported
   without correction — the writer cannot recompute Bonferroni / FDR
   thresholds without re-running the analysis); M7 failure (effect size
   missing because it was never computed). The writer adds an entry to
   `analysis_requests.md` (per §5) tagged as a `validator-escalation`
   request, and the validator failure is recorded as `escalated` rather
   than `pass` or `fail`.
3. **User-modify.** The user opens the section file in their preferred
   editor between paused runs and fixes the validator failure manually.
   On `continue`, the writer's hash-diff (per §5.5) detects the edit
   and re-runs the validator. If pass, status becomes `user-fixed`.
   If still fails, the user is shown what the validator wants and asked
   whether to escalate-as-analysis-request, accept-as-limitation, or
   try again. This path is always available implicitly; it is documented
   here as a first-class option so users know they can resolve issues
   directly without waiting for the writer to try.
4. **Accept-as-limitation.** The user (on resume) declines to take the
   analysis-request and does not want to edit manually — either because
   it's out of scope for this draft or because the underlying data won't
   support it. The validator failure is then woven into the manuscript:
   an honest admission in Methods (e.g., "Multiple-testing correction
   was not applied to the 343-condition screen; ~17 false positives are
   expected at α=0.05"), a corresponding caveat in Results, and an
   entry in Limitations. Logged in `reframing_log.md`; validator status
   becomes `accepted-as-limitation`.

Two consecutive auto-fix failures on the same section escalate to user
review. The validator-status field in `state.json` records the
disposition (`pass | escalated | user-fixed | accepted-as-limitation`)
per validator so subsequent passes know which failures are unfixable
in-writer.

### 7.1.2 M5 (software + version) implementation note

Mechanizing M5 robustly is hard: a regex for `[tool] [version]` patterns
generates too many false positives (any space-separated token pair
followed by a version-like number triggers it), and a whitelist of
known tools is brittle (misses domain-specific tools).

For v0.1, M5 is implemented as a **soft warning**, not a hard fail:

- The validator scans Methods for any of: explicit version statements
  matching `(scipy|numpy|scikit-learn|pandas|statsmodels|R|matlab|python)\s+v?\d+\.\d+(?:\.\d+)?` (case-insensitive); references to a `requirements.txt` or `environment.yml`; or an explicit "Software" subsection.
- If Methods is longer than 200 words AND has ≥3 named statistical
  tests AND has zero matches for the patterns above, emit a soft
  warning: `"M5 (soft): Methods names statistical tests but does not
  appear to specify software versions. ICMJE IV.A.3.d / SAMPL §1
  recommend versioned tool statements."`
- The warning is recorded in `state.json` validator status as
  `soft-warning`; the user can choose to add versions manually
  (user-modify path), accept-as-limitation, or ignore.
- M5 will be tightened in v0.2 once we observe how Methods sections
  actually phrase software statements across BERIL projects.

This is the only M-tier validator demoted to soft-warning in v0.1. All
others (M1–M4, M6–M10) are hard checks per §7.1.1.

### 7.2 Aspirational guidance (in the prompts, not auto-checked)

These live in the per-section system prompts as discipline. Not mechanized
because false positives are too high or the judgment is too contextual.

- Lead with novelty, not rehash, in Abstract and Introduction.
- Distinguish prespecified from exploratory analyses explicitly.
- Discuss alternative explanations for findings; do not rest on a single
  mechanistic interpretation.
- Engage with conflicting prior findings when they exist; do not ignore.
- Translate effect sizes to biologically meaningful units where possible.
- Use conservative language for observational/correlational claims;
  reserve causal language for designs that support it.
- Validate predictions against held-out data if making predictive claims.
- For computational reanalysis: state the data snapshot date and reanalysis
  rationale (what's new vs. the original analysis).
- Acknowledge uncertainty in point estimates; report CIs / posteriors,
  not just point values.

### 7.3 Out-of-scope for v1

These items appear in some checklists but do not apply to BERIL's typical
project shape. Stated here so external reviewers know they are deliberate
omissions, not oversights:

- CONSORT flow diagrams (clinical trials only).
- Trial registration numbers (BERIL projects are not trials).
- IRB approval statements (BERIL projects typically use public data; no
  human subjects). If a future BERIL project does use human subjects,
  this becomes an M-tier check conditional on metadata.
- Sex / gender stratification (BERIL projects are typically bacterial
  datasets). Conditional on `metadata.subject_type == "human"`.
- PRISMA / STARD checklists (different study types).

### 7.4 What we don't claim

Mechanized checks pass = the manuscript meets a *minimum format bar*. This
is necessary but not sufficient for scientific rigor. Scientific rigor
comes from (a) the underlying project being sound, (b) the throughline-
selection step picking a defensible story, (c) the methods-grounding step
preventing fabrication, and (d) the adversarial-review-driven rewrite loop.
Format conformance alone is not a quality guarantee — it's the floor.

---

## 8. Review-rewrite loop

### 8.1 Coupling to the adversarial reviewer

Loose coupling. After drafting, the writer shells out to:

```bash
/beril-adversarial --type paper <draft_dir>
```

This expects `beril-adversarial` to be installed as a sibling skill. The
writer reads the resulting `papers/draft{N}-review.md`, identifies
fixable vs unfixable issues, and either:

- **Fixable** — re-invokes the affected section agents with the violation
  list, regenerates, re-validates.
- **Unfixable** (the underlying evidence won't support the claim) — folds
  the issue into Limitations or Next Steps with citation to the review.

### 8.2 Fallback inline reviewer

If `beril-adversarial` is not on PATH, the writer falls back to a minimal
inline reviewer (see `prompts/fallback_reviewer.v1.md`, ~150 lines, focused
on overclaim detection + citation rigor + scope alignment with the
throughline). Marked clearly as a fallback in the run log; the user is
warned via stderr: *"Using fallback reviewer; install beril-adversarial
for stronger review."*

### 8.3 Iteration cap

Hard cap: **2 rewrite passes**. After the second rewrite, any remaining
issues from the latest review are folded into Limitations / Next Steps,
not re-rewritten. The user can manually re-invoke for a third pass if
they want, but the default loop terminates.

This prevents the rewrite-introduces-new-issues spiral.

---

## 9. Final assembly

`beril-paper-writer assemble <draft_dir>` is a separate CLI step. It:

1. Concatenates `00_throughline.md`, `01_methods.md`, `02_results.md`,
   `03_discussion.md`, `04_introduction.md`, `05_abstract.md`,
   `06_limitations.md`, `references.md` into `manuscript.md`.
2. Runs the M1–M10 validators (final check).
3. Renders to `manuscript.docx` via `pandoc` (figures embedded inline,
   numbered references, IMRAD structure, no journal-specific styling).
4. Reports the validator pass/fail summary.

Users can stop at markdown if they want; the docx step is opt-in. This
matches the principle of separating writing from formatting.

---

## 10. Authorship, AI disclosure, and other ICMJE-required boilerplate

### 10.1 What the writer auto-emits

- An "AI-Assisted Analysis" subsection in Methods, with this template:

  > Manuscript drafting was performed with the BERIL paper-writer skill
  > (`beril-paper-writer v{X.Y}`), using `{model_id}` via the Claude Code
  > harness. Inputs were the project artifacts at
  > `projects/{project_id}/` (snapshot SHA: `{sha}`). The throughline was
  > selected by the human author from candidates surfaced by the writer.
  > Methods were extracted from the project's notebooks; statistical
  > claims were verified against notebook outputs. Citations were drawn
  > from a literature pool with DOI/PMID verification. The draft was
  > reviewed adversarially using `beril-adversarial v{X.Y}` and revised
  > over {N} pass(es). The human authors reviewed and edited the final
  > manuscript and accept full responsibility for its content. No AI tool
  > is listed as an author.

- Placeholder lines for items the writer cannot generate honestly:

  ```
  Authors: [AUTHORS: TBD — fill before submission]
  Affiliations: [AFFILIATIONS: TBD]
  Funding: [FUNDING: TBD]
  Conflicts of Interest: [CONFLICTS: TBD]
  Ethics Approval: [ETHICS: TBD or N/A — confirm before submission]
  Corresponding author: [CORRESPONDING: TBD]
  ORCIDs: [ORCIDS: TBD]
  ```

The presence of any `[X: TBD]` placeholder is a soft warning at assembly
time, not a hard fail (some projects may legitimately want to defer these
to a later editing pass).

### 10.2 ICMJE language quoted

ICMJE V.A (January 2026), on AI-assisted writing tools:

> "Chatbots (such as ChatGPT) and other AI-assisted tools should not be
> listed as authors because they cannot be responsible for the accuracy,
> integrity, and originality of the work… Authors should carefully review
> and edit the AI-generated content as the output can be incorrect,
> incomplete, or biased. … Referencing AI-generated material as the
> primary source is not acceptable. … Nondisclosure of AI use may require
> corrective action and may be construed as misconduct in some
> circumstances."

Source: https://www.icmje.org/recommendations/browse/roles-and-responsibilities/defining-the-role-of-authors-and-contributors.html

The writer's auto-emitted disclosure satisfies this; the user must still
review and confirm before submission.

---

## 11. Cost and latency

Target budget per full run (Plan + Throughline + Drafting + Citation
verification + Review + 1 rewrite pass + assembly):

- **Wall clock:** 15–40 minutes
- **Tokens:** ~500K input, ~50K output (rough)
- **Cost:** $5–$15 (Sonnet-4 rates, plus adversarial-review pass)

If a run is approaching 2× the upper bound on either dimension, the writer
fails loud with a checkpoint and asks the user whether to continue.

This is roughly 5–10× the cost of `/beril-adversarial` alone, which is
expected — paper-writing is fundamentally heavier than reviewing.

**Cost target excludes gap-fill iteration overhead.** The $5–$15 / 15–40
min budget assumes one drafting pass with no gap-fill rounds taken (or
gap-fills resolved via accept-as-limitation, which adds negligible cost).
Each gap-fill round the user takes (per §5.3 cap of 2) adds approximately
$2–$5 and 5–10 minutes — re-drafting affected sections, re-running the
citation pool builder if a citation-request was taken, etc. A worst-case
run with both gap-fill rounds + 2 rewrites + citation-pool exhaustion
escalation can reach $25 / 60 minutes. The 2× checkpoint at $30 / 80 min
catches this before runaway.

### 11.1 Persistent cost log (cross-draft tracking)

Per-invocation cost is tracked at two levels:

- **Per-draft summary** in `papers/draft_N/state.json` and
  `papers/draft_N/audit/cost-summary.md` — captures all calls within a
  single invocation.
- **Per-project rolling log** at `papers/cost-log.jsonl` — append-only
  JSON Lines, one entry per invocation across all drafts. Captures:
  invocation timestamp, draft number, phase
  (plan|throughline|drafting|review|rewrite|assembly|continue),
  command line, model, elapsed seconds, input/output token counts,
  cost in USD, exit status, validator pass/fail counts.

The rolling log enables tracking how the skill's cost profile evolves
over time: across project quality tiers, across spec/prompt revisions,
across model changes. This is what feeds the BERIL atlas's
`paper-writer` metrics for system-self-improvement tracking.

Format example:

```jsonl
{"ts":"2026-04-25T14:32:00Z","project":"functional_dark_matter","draft_number":1,"phase":"plan","model":"claude-sonnet-4-20250514","elapsed_s":142,"in_tokens":28430,"out_tokens":4200,"cost_usd":0.18,"exit":0}
{"ts":"2026-04-25T14:38:00Z","project":"functional_dark_matter","draft_number":1,"phase":"throughline","model":"claude-sonnet-4-20250514","elapsed_s":210,"in_tokens":51200,"out_tokens":9800,"cost_usd":0.30,"exit":0}
```

Never deleted by the writer; users may rotate manually if the file grows
unwieldy.

---

## 12. Open questions (deferred to v0.2 spec revisions)

1. **Multi-paper output** (e.g., a "main paper + companion methods paper"
   split). Out of scope for v1; some BERIL projects could support both
   but the splitting logic is non-trivial.
2. **Journal-specific formatting** (Nature / Cell / PLoS templates).
   Post-MVP. v1 emits generic IMRAD .docx.
3. **Supplementary materials package** (separate supplementary methods,
   supplementary figures, supplementary tables). v1 has appendices in
   the main draft; supplementary-package generation is post-MVP.
4. **Real bibliography manager integration** (Zotero, EndNote). Out of
   scope; users export the BibTeX themselves.
5. **Cross-paper citation reuse** (a project that produces multiple papers
   should reuse the same citation pool). Post-MVP.
6. **Co-authoring with human edits in flight** (more sophisticated diff/
   merge than v1's pause-and-prompt). Could become important if the skill
   is used in a tight human-in-the-loop pattern; revisit after first runs.
7. **Multi-language output.** v1 is English-only.

---

## 13. Sources and references for this spec

- ICMJE Recommendations: https://www.icmje.org/recommendations/
  - Section IV.A (Manuscript Preparation): manuscript structure
  - Section V.A (Roles and Responsibilities of Authors): AI disclosure
- SAMPL Guidelines (Statistical Analyses and Methods in the Published
  Literature): https://www.equator-network.org/wp-content/uploads/2013/07/SAMPL-Guidelines-6-27-13.pdf
- Companion documents in this directory:
  - [reference/reporting-standards-extract.md](reference/reporting-standards-extract.md)
    — full extraction from reporting-standards sources, with the carved
    subset rationale
  - [reference/prior-art-scan.md](reference/prior-art-scan.md) — survey of
    automated paper-writing systems (claude-scientific-writer, AI-Scientist-v2,
    ScienceClaw, open-coscientist, scientific-agent-skills, K-Dense
    agentic DS); patterns adopted, improved, skipped
- Sister-skill specs:
  - `../beril-adversarial-skill-draft/LAYOUT.md` — package layout we mirror
  - `../beril-adversarial-skill-draft/src/beril_adversarial/skill/prompts/adversarial_paper.v1.md`
    — paper-mode reviewer expectations the writer must produce against

---

*This spec is a working document. Before any non-trivial change to the
mechanism described here, update [DECISIONS.md](DECISIONS.md) with the
decision and rationale.*
