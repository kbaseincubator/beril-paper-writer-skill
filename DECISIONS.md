# beril-paper-writer — Decision Log

A running log of design decisions, with date, rationale, and (where
relevant) the alternatives that were considered and rejected. Future
spec revisions should append entries here rather than relitigate
settled questions silently.

Entry format: ID — date — short title. Body: decision, rationale, alternatives
considered, related SPEC/LAYOUT sections.

---

## D-001 — 2026-04-25 — Skill scope: defensible manuscript drafter, not journal submitter

**Decision:** v1 produces ICMJE-conformant generic IMRAD .docx. Journal-
specific formatting, supplementary-materials packaging, real bibliography-
manager integration, and multi-paper splitting are all post-MVP.

**Rationale:** Vendor formatting is a deep rathole; ICMJE conformance gets
us 80% of the value at 20% of the complexity. Most journals accept generic
IMRAD as a starting submission and apply their template at copyediting.

**Alternatives considered:** Build journal templates upfront (Nature, Cell,
PLoS); rejected — journal templates change frequently and are mostly
typesetting; we'd be maintaining vendor formatting code instead of
improving the writer's content quality.

**Related:** [SPEC](SPEC.md) §1.2.

---

## D-002 — 2026-04-25 — User picks the throughline (interactive default)

**Decision:** Throughline selection is the load-bearing user gate. The
writer extracts 2–3 candidates with evidence maps and pauses for the user
to pick. `--throughline auto` opts into the writer's choice
(highest-evidence-density candidate) but is not the default.

**Rationale:** This is the highest-risk step in the pipeline — the throughline
determines what gets written. An LLM left to auto-pick will favor narratives
that are easy to write over narratives that fit the data. Auto-selection is
a trap that produces fluent papers about the wrong story. Adam confirmed
this in the scoping conversation.

**Alternatives considered:** Auto-pick with user-veto (rejected: vetoes
require reading the alternative, which is more work than picking from a
slate); single-throughline (rejected: removes the user's ability to see
what the writer almost did instead).

**Related:** [SPEC](SPEC.md) §4.

---

## D-003 — 2026-04-25 — Methods grounded in notebooks, not generated from prompts

**Decision:** The Methods agent reads notebooks via AST extraction,
identifies actual function calls / package versions / parameters, and
writes Methods constrained to what it can point to in the code. Anything
implied but not explicit is flagged `[METHOD UNCLEAR: see notebook X
cell Y]` for user resolution. A `methods_provenance.md` companion file
links every Methods statement to its notebook+cell.

**Rationale:** Fluent-sounding but fabricated methods is the second-
highest-risk failure mode (after throughline mis-pick). Adam confirmed
this is critical. Grounding via AST extraction is the only reliable
prevention.

**Alternatives considered:** Free-form Methods generation with prompt-
only constraints (rejected: hallucination rate is too high — Ou et al.
2020 explicitly requires methods description "with enough detail to
enable a knowledgeable reader with access to the original data to judge
its appropriateness"); Methods generated from REPORT only (rejected:
REPORTs typically do not include reproducible methods detail).

**Related:** [SPEC](SPEC.md) §6.3; [LAYOUT](LAYOUT.md) tools/extract_methods.py.

---

## D-004 — 2026-04-25 — Reuse existing project figures only; missing figures become gap-fill requests

**Decision:** v1 selects 4–8 figures from the project's existing
`figures/` directory and embeds them. No regeneration. If the writer
identifies a needed figure that doesn't exist, it goes into
`analysis_requests.md` as a `figure-request` with a paste-ready BERIL
slash-command suggestion.

**Rationale:** Figure regeneration requires re-running the underlying
analysis or having access to the raw data, both of which are out of scope
for the writer. Reusing existing figures + flagging gaps preserves
provenance and avoids the writer producing figures the project's analyst
never approved.

**Alternatives considered:** Auto-generate figures from project data
(rejected: requires data access, complicates the agent decomposition,
risks producing inconsistent figures from those in REPORT); reference-
only without embedding (rejected: degrades the manuscript).

**Related:** [SPEC](SPEC.md) §5; [README](README.md) "What it produces."

---

## D-005 — 2026-04-25 — Loose coupling to beril-adversarial; inline fallback if absent

**Decision:** The writer shells out to `beril-adversarial --type paper`
for the review-rewrite loop. If `beril-adversarial` is not installed, the
writer falls back to a minimal inline reviewer (~150-line system prompt
focused on overclaim detection + citation rigor + scope alignment with
throughline). The fallback is clearly marked in the run log; the user is
warned via stderr.

**Rationale:** Loose coupling means each skill versions independently and
can be tested in isolation. The fallback prevents the writer from being
unusable when adversarial is missing, while not pretending to substitute
for the full adversarial pipeline.

**Alternatives considered:** Tight coupling (vendoring adversarial as a
library — rejected: version drift risk, doubles the maintenance surface);
hard requirement (rejected: writer is unusable without adversarial, which
is too brittle).

**Related:** [SPEC](SPEC.md) §8.1, §8.2; [LAYOUT](LAYOUT.md) "Coupling
to beril-adversarial".

---

## D-006 — 2026-04-25 — Hard caps on iteration loops

**Decision:** Maximum 2 gap-fill rounds; maximum 5 requests per round per
type. Maximum 2 review-rewrite passes. After caps are reached, remaining
gaps fold into Limitations or Next Steps; remaining adversarial-review
issues fold into Limitations.

**Rationale:** Without caps, both loops can spin indefinitely (LLM always
finds more gaps; rewrites introduce new issues). Hard caps make cost and
latency predictable, force scope discipline, and keep the writer
honestly scoped to the project's actual evidence.

**Alternatives considered:** Soft caps with user override at each
iteration (rejected: more friction than value; users can re-invoke
explicitly if they want more passes); no caps (rejected: indefinite
spin).

**Related:** [SPEC](SPEC.md) §5.3, §8.3.

---

## D-007 — 2026-04-25 — All state on disk; explicit pause-and-resume

**Decision:** State lives in `papers/draft_N/state.json`. The skill is
restartable. Pause points (throughline pick, gap-fill response, review
acceptance) write state and exit; user invokes
`beril-paper-writer continue <draft_dir>` to resume.

**Rationale:** Multi-turn behavior inside a single slash-command
invocation is fragile (timeouts, accidental backgrounding, conversation
context loss between turns). Disk state makes the writer truly restartable
and lets users walk away mid-run without losing work.

**Alternatives considered:** In-conversation multi-turn (rejected: too
fragile, tested poorly with adversarial reviewer's `claude -p`
patterns); single-shot non-interactive run (rejected: violates the
user-judgment-over-LLM-judgment principle for throughline pick).

**Related:** [SPEC](SPEC.md) §5.5; [LAYOUT](LAYOUT.md) "state.json schema".

---

## D-008 — 2026-04-25 — Intercalation: explicit hash-diff on resume; never silent integration

**Decision:** On `continue`, the writer hashes all source artifacts
(REPORT.md, notebooks, figures, references.md, etc.) and manuscript
intermediates. Any change is reported to the user explicitly:
*"Since last run: 2 new files (paths), 1 changed file (path), 3 unchanged.
Proceed?"* User-edited manuscript files trigger a side-by-side diff with
user assent before any overwrite. New artifacts that materially affect the
chosen throughline trigger an explicit re-evaluation prompt; the writer
never silently rebuilds the throughline.

**Rationale:** The intercalation problem (paper-writer pauses → user runs
BERIL → user resumes paper-writer) is the trickiest UX challenge. Silent
integration loses the thread, overwrites user edits, or silently shifts
the story. Explicit hash-diff on resume is the discipline that prevents
all three failure modes.

**Alternatives considered:** Auto-integrate without prompting (rejected:
silent overwrites of user edits are unacceptable); refuse to resume if
artifacts changed (rejected: too brittle — small changes shouldn't block
resume).

**Related:** [SPEC](SPEC.md) §5.5.

---

## D-009 — 2026-04-25 — Citation pool with hard verification; pool capped at 80

**Decision:** Before drafting, a literature-scan agent builds a verified
citation pool (DOI/PMID checked via WebSearch or BERIL's PubMed MCP, full
9-field metadata captured). Pool is capped at 80 references. During prose
generation, the writer is constrained to draw citations only from the
pool. If more are needed, one additional pool-build cycle is allowed; then
hard fail and ask user to scope discussion or add a gap-fill request.

**Rationale:** Citation hallucination is the most-cited LLM-paper-writer
failure mode across all prior systems we surveyed (claude-scientific-
writer, AI-Scientist-v2, ScienceClaw, open-coscientist). Pre-built pool +
prose constraint is the only reliable prevention. The 9-field discipline
is inherited from beril-adversarial. The 80-reference cap is informed by
our reading of typical computational-biology papers (median ~50 refs); 80
is generous without being unbounded.

**Alternatives considered:** Verify-after-write (rejected: requires
re-prompting to fix hallucinations, which often introduces new ones);
unlimited pool (rejected: signal-to-noise drops past ~80 refs; also a
budget control).

**Related:** [SPEC](SPEC.md) §6.4.

---

## D-010 — 2026-04-25 — Mechanized validators: 10 hard checks, not 20+

**Decision:** Carve a tight subset of reporting-standards items as
mechanized validators (M1–M10 in SPEC §7.1). Everything else is
aspirational guidance in the per-section system prompts.

**Rationale:** Each validator costs implementation+maintenance effort and
has false-positive risk that erodes user trust. 10 well-chosen mechanized
checks catch the most common discipline failures (missing AI disclosure,
no data-availability statement, bare percentages without CI, no multiple-
testing correction, etc.) without overreaching. Subagent extraction
proposed 20; we trimmed for signal.

**Alternatives considered:** Mechanize all 20 ICMJE/SAMPL items (rejected:
diminishing returns and false-positive risk); mechanize none (rejected:
loses the auto-fix value of catching obvious omissions before they reach
the user).

**Related:** [SPEC](SPEC.md) §7.1; [reference/reporting-standards-extract.md](reference/reporting-standards-extract.md).

---

## D-011 — 2026-04-25 — Both references.md AND bibliography.bib produced from v0.1

**Decision:** Resolves the format-mismatch with the adversarial reviewer's
paper-mode prompt (which expects `papers/bibliography.bib`). v0.1 of the
writer produces `references.md` (human-readable, numbered), `bibliography.bib`
(BibTeX, machine-readable), and `citation_map.md` (markdown table mapping
citations).

**Rationale:** BibTeX is structured serialization of data the writer has
to track anyway for verification; not vendor formatting. Producing it
satisfies the reviewer's expectations, gives users a journal-ready export
with no extra effort, and avoids needing to update the adversarial
reviewer's prompt. Adam's earlier "BibTeX is post-MVP" call referred to
fancy bibliography-manager features, not BibTeX itself.

**Alternatives considered:** markdown only (rejected: requires updating
adversarial reviewer's paper-mode prompt to read it; brittle);
bibliography.bib only (rejected: less human-readable for users editing
the draft).

**Related:** [SPEC](SPEC.md) §6.4; [LAYOUT](LAYOUT.md) "bibliography.bib".

---

## D-012 — 2026-04-25 — IMRAD section assembly order: Methods → Results → Discussion → Introduction → Abstract

**Decision:** Body sections drafted in this order, not the read-order
(Intro → Methods → Results → Discussion → Abstract).

**Rationale:** This is how scientists actually write papers. Methods
must be settled before Results can be reported in correct frame; Results
must be settled before Discussion can interpret them; Introduction sets
up exactly what the paper delivers (which we don't know until Discussion
is settled); Abstract summarizes a stable body. Drafting in this order
reduces cross-section rewriting.

**Alternatives considered:** Read-order drafting (rejected: forces
guessing what Methods/Results will say, then rewriting Intro when they
diverge); parallel drafting of all sections (rejected: cross-section
contradictions skyrocket).

**Related:** [SPEC](SPEC.md) §6.1; corroborated by ScienceClaw and
scientific-agent-skills (per [reference/prior-art-scan.md](reference/prior-art-scan.md)).

---

## D-013 — 2026-04-25 — EXPLORATORY-tier produces an honest exploration report (not refusal)

**Decision:** Project-quality triage produces STRONG / THIN / EXPLORATORY
verdicts. STRONG proceeds to a full IMRAD paper; THIN proceeds with
scope-down options; EXPLORATORY emits a warning and proceeds with the
**exploration-report template** (same IMRAD shell, but with explicit
"Preliminary…" / "Exploratory…" framing in title and abstract,
substantially expanded Limitations, and a structured Future Work
section enumerating what's needed to reach publishability). Honest
reporting of what was attempted and learned — including null and
negative findings — is the value the writer adds for EXPLORATORY work.

**Rationale (revised 2026-04-25 from the original "refuse" position):**
Refusing to write loses the value of exploratory work. Producing a
paper that frames thin evidence as a finished study is worse than
refusing. The middle path — produce *something*, but frame it
honestly as exploration — preserves both honesty and value. Adam:
"the default should be a warning but try and do our best at reporting
what the exploration was for and what was learned from it."

**Alternatives considered:** Refuse outright (rejected per above);
produce an IMRAD paper with no special framing (rejected: invites
overclaiming); produce a free-form report (rejected: loses comparability
with peer-reviewed format). The IMRAD-shell-with-framing-shifts approach
keeps the format familiar to readers and journals (some journals do
publish "preliminary" or "brief communications" in IMRAD form) while
making the evidence-strength explicit.

**Related:** [SPEC](SPEC.md) §2 (design premise 1), §3.1, §3.2.

---

## D-014 — 2026-04-25 — AI-disclosure auto-emitted; author/funding/ethics as TBD placeholders

**Decision:** Per ICMJE V.A (January 2026), the writer auto-emits an
"AI-Assisted Analysis" subsection in Methods naming tool + version + task
+ human-review confirmation. Author list, affiliations, funding,
conflicts, ethics statements, corresponding author, and ORCIDs are
emitted as `[X: TBD]` placeholders the user must fill before submission.
Presence of any TBD placeholder is a soft warning at assembly time.

**Rationale:** ICMJE 2026 explicitly requires AI disclosure; nondisclosure
is "misconduct in some circumstances." We auto-emit because the writer
*always* knows this information. We do not auto-fill author/funding/
ethics because honest answers depend on the user's institutional context
and can't be inferred from project artifacts.

**Alternatives considered:** Auto-list user as sole author (rejected:
overreach; many BERIL projects are collaborative); omit AI disclosure
(rejected: ICMJE violation).

**Related:** [SPEC](SPEC.md) §10.

---

## D-015 — 2026-04-25 — Cost target: $5–$15 per full run, 15–40 minutes wall clock

**Decision:** State a budget up front. Fail loud if approaching 2× upper
bound on either dimension; checkpoint and ask user whether to continue.

**Rationale:** Without an explicit target, we won't know if we've over-
engineered. Adam endorsed the target. Budget is roughly 5–10× the cost
of `/beril-adversarial` alone, which is consistent with paper-writing
being fundamentally heavier than reviewing.

**Alternatives considered:** No budget target (rejected: cost can balloon
unpredictably); tighter budget (rejected: would force scope cuts we
don't want yet).

**Related:** [SPEC](SPEC.md) §11; [LAYOUT](LAYOUT.md) "Cost / latency targets".

---

## D-016 — 2026-04-25 — Documentation discipline: README + SPEC + LAYOUT + DECISIONS minimum

**Decision:** The skill ships with these four documents at minimum:
README (quick-start), SPEC (community-facing rationale, the load-bearing
doc), LAYOUT (internal architecture), DECISIONS (this file). Plus a
`reference/` directory containing supporting research (reporting-standards
extract, prior-art scan).

**Rationale:** Adam: "We need to record our spec and decisions here in
documentation. It is key for community acceptance." The four documents
have distinct audiences and purposes; collapsing them produces a worse
artifact for each audience. SPEC + DECISIONS together let an external
reviewer trace why each design choice was made.

**Alternatives considered:** Single combined doc (rejected: each
audience gets a worse artifact); README + SPEC only (rejected: LAYOUT
needs to be separate from SPEC because it changes more frequently and
has a different audience — implementers vs. reviewers).

**Related:** [README](README.md), [SPEC](SPEC.md), [LAYOUT](LAYOUT.md).

---

## D-017 — 2026-04-25 — Out-of-scope items explicitly named, not silently omitted

**Decision:** SPEC §1.2, §7.3, and §12 explicitly enumerate what the
writer does NOT do (no clinical-trial papers, no figure regeneration, no
journal templates, no IRB statements when no human subjects, etc.).

**Rationale:** Silent omissions look like oversights to external
reviewers; explicit non-goals are defensible design choices. Especially
important for "community acceptance" (per Adam in scoping).

**Alternatives considered:** Silent omission (rejected: invites the
question "did you forget X?" forever).

**Related:** [SPEC](SPEC.md) §1.2, §7.3, §12.

---
## D-018 — 2026-04-25 — Methods grounding sources include RESEARCH_PLAN.md (intent), not just notebooks (execution)

**Decision:** The Methods agent reads `RESEARCH_PLAN.md` as well as
notebooks/scripts when grounding the Methods section. Plan provides
*intent* (hypothesis structure, prespecified tests, sample-size
justification, design rationale); notebooks provide *execution* (actual
function calls, parameters, package versions). The Methods section
needs both.

The agent cross-checks the two and notes discrepancies in
`methods_provenance.md` (e.g., plan prespecifies test X, notebook
implements test Y — the manuscript reports what was done, but the
discrepancy is logged for transparency).

**Rationale:** Adam: "Methods — from plan and I think examination of
notebooks and scripts?" — yes. Notebooks rarely articulate *why* a
particular method was chosen; the plan does. Methods sections that
lack design rationale read as procedural rather than scientific. ICMJE
IV.A.3.d expects rationale alongside procedure.

**Alternatives considered:** Notebooks-only (rejected: loses design
rationale); plan-only (rejected: loses what was actually done).

**Related:** [SPEC](SPEC.md) §6.3.

---

## D-019 — 2026-04-25 — Persistent cost log at project level (cross-draft tracking)

**Decision:** Cost is tracked at two levels: per-draft summary
(`papers/draft_N/state.json`, `audit/cost-summary.md`) and per-project
rolling log (`papers/cost-log.jsonl`, append-only JSONL, one entry per
invocation across all drafts).

The project-level log enables tracking how the skill's cost profile
evolves over time — across project quality tiers, spec revisions,
model changes. Feeds the BERIL atlas's `paper-writer` metrics for
system-self-improvement tracking.

**Rationale:** Adam: "We must record the cost etc. in the directory so
we can track this over time." Per-draft summary alone doesn't enable
cross-draft analysis; we need the rolling log for any longitudinal
question about skill efficiency.

**Alternatives considered:** Per-draft only (rejected: loses
longitudinal signal); push to atlas only (rejected: atlas is
optional infrastructure; cost log must work standalone).

**Related:** [SPEC](SPEC.md) §11.1.

---

## D-020 — 2026-04-25 — M-tier validator failures take four escalation paths

**Decision (revised 2026-04-25 to add user-modify path):** Each M-tier
validator failure resolves via one of four paths:
1. **Auto-fix** — writer fixes unilaterally (e.g., missing IMRAD
   header, missing AI-disclosure paragraph).
2. **Escalate as analysis-request** — fix requires new analysis the
   writer cannot perform (e.g., FDR correction across hundreds of
   p-values); writer adds a `validator-escalation` entry to
   `analysis_requests.md`.
3. **User-modify** — user opens the section file in their preferred
   editor between paused runs and fixes manually; on `continue`, the
   writer's hash-diff detects the edit and re-runs the validator. If
   pass, status becomes `user-fixed`. Always available implicitly via
   §5.5; documented as first-class so users know they can resolve
   issues directly without waiting on the writer.
4. **Accept-as-limitation** — user declines the analysis-request and
   does not modify; writer admits the issue in Methods, caveats in
   Results, adds to Limitations; logged in `reframing_log.md`.

`state.json` records the disposition per validator
(`pass | escalated | user-fixed | accepted-as-limitation`) so subsequent
passes know which failures are unfixable in-writer.

**Rationale:** Adam: "In a 7.1 violation — this might require a BERIL
request (correct multiple testing) or for the user to accept (then this
becomes an admission in methods and a next step in results)?" — yes,
both. The original spec said "two failures escalate to user" without
distinguishing the two cases; this decision clarifies the three real
paths.

**Alternatives considered:** Auto-fix only (rejected: not all failures
are in-writer fixable); always escalate to user (rejected: throws away
the writer's ability to fix simple issues); silent acceptance (rejected:
violates honesty premise).

**Related:** [SPEC](SPEC.md) §7.1.1.

---

## D-021 — 2026-04-25 — Analysis requests emit markdown snippets, not literal slash commands

**Decision:** `analysis_requests.md` entries include a markdown snippet
the user can either (a) append to `RESEARCH_PLAN.md` as a new analysis
task, or (b) paste as a natural-language prompt into a fresh
`/berdl_start`-initiated Claude session. The writer does not emit
literal slash-command syntax (e.g., not `/berdl recompute_X`).

**Rationale:** Adam: "Does the /berdl command actually exist as
conceptualized. My guess is not and the user — in a berdl_start
initiated session just tells claude to look at the md."

Verified by reading BERIL's installed skills: `/berdl` is a SQL-query
skill, not a generic "do an analysis" entry point. The actual BERIL
workflow is plan-driven: extend `RESEARCH_PLAN.md`, then a
`/berdl_start`-initiated session executes via the appropriate skills.

**Alternatives considered:** Literal slash commands (rejected: don't
exist as written); free-form English (rejected: loses precision).
Markdown snippets give precision in a format the user can route
through any of BERIL's actual entry points.

**Related:** [SPEC](SPEC.md) §5.

---


## D-022 — 2026-04-25 — `--mode paper|report` flag (orthogonal to tier)

**Decision:** The writer has two independent dimensions:

- **`--mode`** controls output shape: `paper` (IMRAD with claims) or
  `report` (structured activity report describing what was done and
  observed; no claims-of-significance framing, no abstract-as-claim,
  no discussion-as-interpretation).
- **Tier** (STRONG / THIN / EXPLORATORY) controls evidence-strength
  framing within whichever shape is chosen.

Default mode by tier: STRONG/THIN → `paper`, EXPLORATORY → `report`.
Either mode can be forced regardless of tier.

**Rationale:** Adam: "Another option might be REPORT which instead just
reports WHAT happened during analysis?" — yes. Some users want internal
documentation, lab-notebook write-up, or handoff documents — not a
journal-bound paper. REPORT mode serves that case explicitly. Making
`--mode` orthogonal to tier (rather than a fourth tier) means a strong
project can also be reported (e.g., a strong-but-not-yet-finished
project the user wants to document) and an exploratory project can be
written as a paper if the user wants to accept the exploration-paper
framing.

REPORT-mode output structure (SPEC §3.2.2): Project Summary, Background
and Question, What Was Done (Methods), What Was Observed (Findings),
Observations and Open Questions, Limitations and Caveats, Next Steps,
Appendices. Citation pool, methods grounding, and validator subset
still apply; validators that don't apply (e.g., M2 Structured Abstract)
are skipped and logged.

**Alternatives considered:** Make REPORT a fourth tier (rejected:
conflates evidence-strength with output-shape, which are independent
choices); make REPORT a separate skill (rejected: 90% of the
infrastructure overlaps with paper writing — citation pool, methods
grounding, hash-diff, gap-fill loop are all reusable).

**Related:** [SPEC](SPEC.md) §3.2, §3.2.2; [README](README.md) "Usage";
[LAYOUT](LAYOUT.md) "Slash commands".

---

## D-023 — 2026-04-25 — Skill name stays `beril-paper-writer`; adversarial reviewer prompt gets a small cleanup

**Decision:** Keep `beril-paper-writer` as the skill name. Do NOT rename
to `beril-paper`. The adversarial reviewer's `--type paper` mode
references "the `beril-paper` skill" in `adversarial_paper.v1.md` line
22; this is descriptive context, not a hard dependency. The contract
between the two skills is the file layout under `papers/draft_N/`
(specifically `manuscript.md`, `THROUGHLINE.md`, `bibliography.bib`,
`citation-map.md` per the reviewer's prompt §"What to read"), not the
writer's name.

**Follow-up cleanup (separate change in beril-adversarial-skill-draft):**
update `adversarial_paper.v1.md` line 22 to reference "the paper-drafter
skill" (generic) rather than the specific name `beril-paper`. Tracked
as a follow-up in DECISIONS rather than executed here because it lives
in a different skill's repo.

**Rationale:** Adam: "Does adversarial need to know our command. I think
paper is just an option for the reviewer right?" — correct. Renaming
would break the existing file-layout contract for no real benefit.
`beril-paper-writer` is also clearer for users about what the skill
does.

**Alternatives considered:** Rename to `beril-paper` (rejected: the
existing name is more explicit; cleanup of the reviewer's prompt is
trivial); leave the reviewer prompt as-is (acceptable but a tiny
inconsistency that's worth fixing).

**Related:** [README](README.md); follow-up issue against
`../beril-adversarial-skill-draft/src/beril_adversarial/skill/prompts/adversarial_paper.v1.md`
line 22.

---

## D-024 — 2026-04-25 — `python-docx` for assemble; no `pandoc` system dependency

**Decision:** The assemble step (`beril-paper-writer assemble`) renders
markdown intermediates to `.docx` via `python-docx` (pure-Python PyPI
package), not via `pandoc` (system binary). A small markdown→docx
converter (~200-300 lines, planned in `tools/assemble_docx.py`) walks
the markdown the writer itself produced and emits docx via python-docx.

`python-docx>=1.1.0` and `nbformat>=5.7.0` are added as runtime
dependencies in `pyproject.toml`. Both are pure-Python (with `lxml` as
a python-docx transitive — widely available as a wheel on every
platform we ship to). No system binaries, no `apt-get`, no `brew`.

**Rationale:** Adam, while running the Phase 1 smoke test on his Mac,
flagged that requiring `pandoc` as a separate `brew install` step is
a portability problem for remote BERIL deployments where users may not
have admin access to install system packages. The principle is
"`pipx install` does everything." python-docx satisfies the principle;
pandoc does not.

Also relevant: the assembly path needs to stay model-agnostic. Using
Claude Code's loaded `anthropic-skills:docx` skill would tie us to
Claude (no codex compatibility), and the adversarial-reviewer ecosystem
already supports `--reviewer claude,codex` fusion. Even if paper-writer
v1 doesn't add `--writer codex`, defending future flexibility now is
the cheap defensive choice.

Tradeoffs the python-docx path accepts:
- We write ~200-300 lines of markdown→docx conversion code (Phase 5
  scope). Manageable because we control the markdown we emit; we don't
  need to handle arbitrary markdown.
- python-docx is less feature-rich than pandoc (no math typesetting,
  no LaTeX bridge, no exotic citation styles). For our IMRAD scope
  (prose + numbered citations + embedded PNG figures + tables) this
  is sufficient.

**Alternatives considered:**

- `pypandoc-binary` (pandoc bundled in a Python wheel, pre-built per
  platform). Pros: full pandoc, no conversion code. Cons: ~50MB per
  wheel, per-arch wheels (linux-x86_64, linux-arm64, macos-x86_64,
  macos-arm64, windows-x86_64) with availability gaps for newer Python
  versions, bundles third-party binaries (some security policies
  object). Rejected as overkill for our scope.
- `mistletoe` + `python-docx` (markdown AST + docx writer). Pros: less
  hand-rolled markdown parsing. Cons: two libraries, and our markdown
  is constrained enough that AST parsing is overkill. Deferred — could
  adopt if v0.1's hand-rolled converter proves brittle.
- Use `anthropic-skills:docx` via Claude Code skill loader. Pros: no
  Python conversion code at all. Cons: ties assembly to Claude (loses
  codex compatibility); only available inside a Claude Code session,
  not from a bare CLI invocation. Rejected.
- Keep `pandoc` and document it as a system requirement. Cons: violates
  the self-contained-pipx-install principle for remote BERIL
  deployments. Rejected.

**Related:** [SPEC](SPEC.md) §9; [LAYOUT](LAYOUT.md) "What ships vs.
what runs" + "Cross-platform" + "Open questions for revisit" §1;
[pyproject.toml](pyproject.toml) `[project] dependencies`.

---

## Known follow-ups (open work referenced from settled decisions)

- **From D-023:** update `../beril-adversarial-skill-draft/src/beril_adversarial/skill/prompts/adversarial_paper.v1.md`
  line 22 to reference "the paper-drafter skill" rather than the
  specific name `beril-paper`. Status: OPEN. Will be done as a separate
  PR against the adversarial reviewer skill, not in this skill's repo.

---

## D-025 — 2026-04-27 — orchestrator owns `<draft_dir>/figures/` for paper-order names; pre-clean stale files in `phase_results`

**Decision:** The orchestrator owns the contents of `<draft_dir>/figures/`
for any file matching the paper-order name pattern `fig*.png`. On entry
to `phase_results` in drafting mode (NOT REPAIR_MODE), the orchestrator
removes any pre-existing `fig*.png` from that directory before
re-running results.v1's figure-selection step. results.v1 then re-copies
figures from the project's source `figures/` dir with paper-order
renaming, and emits `figures_manifest.tsv` (Wrinkle A canonicalization,
v0.3 punch list).

**Rationale:** v0.2.1's live-test exposure of `draft_1/figures/`
revealed paper-order filename collisions from rewrite-loop residue:
`fig01_dark_gene_census.png` co-existing with
`fig01_dark_gene_census_fitness.png`, and similar duplicates at fig05,
fig06, fig08. The duplicates accumulate because results.v1's figure-copy
step (a) doesn't clean before copying and (b) gets re-invoked on
rewrite-loop reruns with different selections. This pollutes the
`<draft_dir>/figures/` listing and breaks any "paper-order N → file"
lookup that depends on filename uniqueness.

The cleanup runs ONLY in drafting-mode entry (where results.v1 will
re-copy fresh files). In REPAIR_MODE, results.v1 is invoked with
NAMED_VALIDATOR scope and does not re-do figure selection — the
existing figures must remain intact. The bash gate is the absence of
REPAIR_MODE state at phase_results entry, which is the natural
drafting-vs-repair boundary in the orchestrator.

The contract this establishes: anything matching `<draft_dir>/figures/fig*.png`
is orchestrator-managed and may be deleted on the next drafting run.
User-curated illustrations should NOT live in this directory; they
should live in a separate path (TBD when a 2nd user shows up — v0.4
concern). For v0.3, single-user is the operator and this contract is
acceptable.

**Tradeoffs accepted:**

- A user who manually edits `<draft_dir>/figures/fig01_x.png` between
  drafting runs will have their edit lost on re-run. Acceptable
  because (a) the operator is currently solo and (b) re-running drafting
  is the explicit "regenerate from source" gesture.
- The cleanup is destructive without confirmation. Acceptable for v0.3;
  a `--keep-existing-figures` flag could be added in v0.4 if needed.

**Alternatives considered:**

- Have results.v1 (the prompt) clean before copying. Rejected — puts
  filesystem-hygiene discipline on the LLM, which is exactly the kind
  of "trust the prompt" failure mode `feedback_prompt_discipline_needs_post_check.md`
  warns against. Orchestrator-side cleanup is bulletproof.
- A new top-level command `beril-paper-writer reset-figures <draft_dir>`.
  Rejected — adds CLI surface for a one-line orchestrator step.
- Append-only with a manifest. Rejected — collisions accumulate, no
  cleanup mechanism, and the manifest grows unboundedly across reruns.

**Related:** v0.3 punch list Tier 2.2; `paper_writer.sh phase_results`
lines 875-884; [SPEC](SPEC.md) §6 (results.v1 figure-copy step).

---

## D-026 — 2026-04-27 — embedded-image-tag form: `![Figure N: <caption>](figures/<filename>)`

**Decision:** `phase_embed_figures` (v0.3 Tier 2.2) injects markdown
image tags in the form
`![Figure N: <caption>](figures/<filename>)` after the first sentence
containing `(Fig. N)` for each N. Both the figure number and caption
text live in the markdown alt-text. The figure number N is read from
the prose's `(Fig. N)` callout (NOT computed by the embedder).
Caption text is sourced from `figures_inventory.md` via
`paper_writer_helpers.py resolve-figures` — the project-authored
caption-candidate ranking (REPORT-derived first, notebook-context
second, filename third) is the authority.

**Rationale:** Three motivations:

1. **Self-contained markdown.** Anyone reading manuscript.md alone
   (without the assembler) sees the captioned figures with proper
   numbering. The "Figure N:" prefix in the alt-text doubles as a
   readable label in plain-text rendering.

2. **N from prose, not from manifest counting.** results.v1's prose
   already declares paper-order N. If figures are reordered, the
   reordering happens in results.v1's output and propagates naturally;
   the embedder just reads what's in the prose. Counter-pattern
   (have the embedder count and renumber) doubles the renumbering
   authority and creates a drift surface.

3. **Caption from inventory, not from LLM.** Captions live in
   `figures_inventory.md` (project-authored, REPORT-derived). The LLM
   is never asked to re-emit captions in the manifest, sidestepping
   the JSON-quoting trap from `feedback_llm_json_unfixable_in_parser.md`.

**docx rendering** (D-024 path): `tools/assemble_docx.py` (v0.3 Tier
2.3) renders the markdown image tag as a python-docx `Picture` object
in a centered paragraph + a `Caption`-styled paragraph immediately
following with the alt-text as the caption text. The "Figure N:"
prefix is part of the alt-text and renders verbatim.

**Tradeoffs accepted:**

- Alt-text is overloaded (accessibility label + caption + figure
  number). For docx output this is fine; for accessibility tooling on
  manuscript.md alone, a screen reader sees "Figure N: caption" which
  is reasonable.
- Embed once per figure: subsequent `(Fig. N)` references to an
  already-embedded figure stay textual. This means a figure cited
  three times in prose is only embedded after the first citation;
  later citations are "see Fig. N" without an inline image. Standard
  scientific-manuscript convention.

**Related:** v0.3 punch list Wrinkle B; D-024 (python-docx renderer);
`paper_writer_helpers.py cmd_embed_figures` + `_embed_figures_in_text`.

---

## D-027 — 2026-04-28 — Dual-reviewer architecture: fallback + canonical

**Decision:** Two review pathways, not one. The fallback inline
reviewer (`fallback_reviewer.v1.md`, 3 classes, ~30s, no tool access)
runs inside the rewrite loop for fast iteration. The canonical
adversarial reviewer (`beril-adversarial review --type paper`, 10
classes, 5–10 min, full tool access) runs after the rewrite loop as a
final quality gate. They are complementary, not alternative.

**Rationale:** The original D-005 design assumed a single reviewer
that would either be adversarial (if installed) or fallback (if not).
Live testing revealed a cost/latency mismatch: the canonical reviewer
at 5–10 min per pass makes a 3-pass rewrite loop take 15–30 min just
in review, with diminishing returns on the shallow fixable issues the
rewrite loop targets. The fallback's 3 focused classes (overclaim,
citation rigor, scope alignment) are the right tool for the rewrite
loop; the canonical reviewer's 10 classes are the right tool for the
final audit.

**Alternatives considered:** Single reviewer with a `--fast` flag
(rejected: the two review modes have different prompt architectures
and tool-access requirements; a flag would be a leaky abstraction).
Canonical reviewer only, no rewrite loop (rejected: rewrite loop
catches mechanical issues — orphan citations, bare percentages —
that the user shouldn't have to fix by hand).

**Related:** D-005 (original loose coupling); CONTRACT.md (cross-skill
contract); paper_writer.sh lines 2242–2262.

---

## D-028 — 2026-05-02 — adversarial-review-paper.v2 schema adoption

**Decision:** Paper-writer adopts the `adversarial-review-paper.v2`
single-array JSON schema for the canonical adversarial reviewer's
output. The schema uses a flat `findings[]` array (no deck-level /
section-level split), P0/P1/P2/info severity values, 10 class
enum, and `fix_target` fields that map to paper-writer prompt names.

**Rationale:** The v1 schema (presentation-originated) had a
deck-level / slide-level split that didn't map to papers. v2
unifies both into a single `findings[]` array with manuscript-wide
vs section-level distinguished by field presence (section-level
findings have `section` + `line_range` + `paragraph_quote`;
manuscript-wide findings omit them). This eliminated the structural
mismatch and let both paper and presentation modes share validator
infrastructure.

**Alternatives considered:** Paper-specific v1 schema (rejected:
diverges from presentation, doubles validator maintenance); JSON-LD
or RDF-style (rejected: overengineered for 2-consumer audience).

**Related:** CONTRACT.md (consumer-side schema documentation);
beril-adversarial CONTRACT.md (producer-side).

---

## D-029 — 2026-05-02 — JSON-validity hardening pattern

**Decision:** When LLM-emitted JSON contains unescaped inner quotes
(e.g., `"paragraph_quote": "The "key" finding..."`) — a failure mode
that is NOT algorithmically repairable — the fix is at the prompt
level: explicit anti-pattern rule with worked examples showing correct
alternatives (`\"`, `'`, backtick, or rephrase). Additionally, a
lenient JSON loader does trailing-comma repair (`,}` → `}`) which IS
algorithmically safe. Both paper and presentation reviewer prompts
carry the anti-pattern rules.

**Rationale:** Two LLM JSON failure modes with different fixability:
unescaped quotes (ambiguous — parser can't distinguish delimiter from
content) vs trailing commas (unambiguous — regex `,(\s*[}\]])` →
`\1` is safe). Treating them differently is the correct response:
prompt-side prevention for the unfixable one, code-side repair for the
fixable one. Both are documented in memory entries
`feedback_llm_json_unfixable_in_parser.md` and
`feedback_llm_json_trailing_commas_repairable.md`.

**Alternatives considered:** Lenient JSON parser for both (rejected:
unescaped-quote ambiguity is provably unresolvable — `"a "b" c"` has
3 valid parsings); strict JSON only (rejected: trailing commas are
common enough that a simple repair saves retry cost).

**Related:** beril-adversarial v0.6.2 commit; D-028.

---

## D-030 — 2026-04-28 — Caption sufficiency redesign: Source 4 LLM synthesis

**Decision:** When deterministic caption sources (notebook walk-back
+ matplotlib AST, Source 1–3) don't produce enough signal for a usable
ICMJE figure caption, route to Source 4: LLM synthesis via
`figure_caption.v1.md`. The sufficiency gate uses
`_strip_prose_for_inline` to remove boilerplate before measuring
word count, preventing notebook critique-heavy prose from falsely
passing. Panel-count-scaled `max_words` formula: `200 + (panels - 1) * 100`.

**Rationale:** v0.3 produced "Figure N: <filename>" captions (avg 5
words) because it had no LLM fallback when deterministic sources
failed. v0.4 added Source 4 with anti-fabrication discipline
(every quantitative claim must trace to notebook output or REPORT).
v0.5 tightened the sufficiency gate: boilerplate-heavy notebook prose
(e.g., lengthy critique paragraphs) was passing the word-count gate
but producing unusable captions. Stripping boilerplate before
measurement routes these correctly to Source 4.

**Alternatives considered:** Always use LLM for captions (rejected:
deterministic sources are more reliable and cheaper; LLM is the
fallback, not the default); higher word-count threshold without
stripping (rejected: doesn't distinguish signal from noise in
notebook prose).

**Related:** RELEASE_NOTES_v0_4.md; RELEASE_NOTES_v0_5.md;
figure_caption.v1.md.

---

## D-031 — 2026-05-01 — Reframing repair dispatch via reframer.v1

**Decision:** When `check_reframing_log.py` detects entries with
status `escalated` or `new_data` in reframing_log.md, the orchestrator
dispatches `reframer.v1.md` to re-evaluate and repair affected sections
rather than halting the pipeline. Repairs are logged as new reframing-
log entries with `type: repair` for auditability.

**Rationale:** The original design (D-008, intercalation) specified
explicit re-evaluation on resume when source artifacts changed. In
practice, reframing-log entries accumulate during drafting (not just on
resume), and a halt-and-wait-for-user response to every escalated
entry makes the pipeline unusable for unattended runs. Dispatching
reframer.v1 to repair in-place preserves the audit trail while keeping
the pipeline moving. The user can review repairs in the reframing log
post-run.

**Alternatives considered:** Halt on any escalated entry (rejected:
too many halts in practice — functional_dark_matter draft_6 had 4
escalated entries); ignore escalated entries (rejected: silent drift
is the thing the reframing log exists to prevent).

**Related:** D-008 (intercalation); reframer.v1.md; paper_writer.sh
`phase_apply_reframing_repairs`.

---

## D-032 — 2026-05-01 — Tier-detection defaults to STRONG for unknown REPORT structure

**Decision:** When `check_tier.py` cannot determine the project's
evidence tier from REPORT.md (no recognizable hypothesis-test
structure, no explicit tier annotation), default to STRONG framing
rather than EXPLORATORY.

**Rationale:** STRONG framing is more restrictive (requires
explicit claims, effect sizes, CIs). Defaulting to it means the
writer produces conservative prose that a reviewer might loosen,
rather than speculative prose that a reviewer must tighten. The risk
of defaulting to EXPLORATORY is overclaim on a project that actually
has strong evidence; the risk of defaulting to STRONG is understatement
on an exploratory project. Understatement is safer.

**Alternatives considered:** Default to EXPLORATORY (rejected:
overclaim risk); require explicit tier annotation (rejected:
existing BERIL projects don't have tier annotations; would block
all current usage).

**Related:** Discussion tier-detection fix (v0.6.4); results.v1.md
tier-aware framing table.

---

## D-033 — 2026-05-03 — Unified fabrication discipline in LAYOUT.md

**Decision:** A single "Fabrication discipline" section in LAYOUT.md
defines what fabrication means across all prompts: any claim that
cannot trace to (1) canonical project sources, (2) verified bibliography,
or (3) explicit metadata. Each drafting prompt cross-references this
definition with its section-specific risk variant (results: invented
numbers; methods: invented protocols; discussion: mechanism fabrication;
intro: citation-claim mismatch; abstract: overclaim vs body; captions:
invented n-values).

**Rationale:** The prompt review (2026-05-03, CPN.3) found that
"fabrication" was defined differently in 6 prompts with no single
authoritative definition. The risk: a prompt with a weaker definition
of fabrication produces text that a prompt with a stricter definition
would flag. Centralizing the definition ensures all prompts share the
same three-category trace-back contract.

**Alternatives considered:** Per-prompt standalone definitions
(rejected: demonstrated drift across 6 prompts); shared include file
(rejected: prompt templates don't support includes; a reference link
achieves the same outcome with less tooling).

**Related:** LAYOUT.md §Fabrication discipline; C1 in consolidated
triage; prompts CPN.3 finding.

---

*Append new decisions below this line. Use the next D-NNN ID.*
