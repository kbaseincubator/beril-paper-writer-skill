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

**Related:** `release-notes/v0_4.md`; `release-notes/v0_5.md`;
`figure_caption.v1.md`.

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

## D-034 — 2026-05-07 — v0.8.0 architectural redesign: holistic write + 3-tier review (M0 sign-off)

**Decision.** v0.8.0 replaces the v0.4–v0.7.x per-section orchestrator
with an 8-phase pipeline: Phase 0 deterministic tooling (existing +
NEW discrepancy_register + claim_inventory) → Phase 1 interactive
story builder → Phase 2 holistic write (one pass, Opus 4.6) → Phase 3
tiered review cascade (deterministic → Haiku light → canonical
adversarial; fail-fast; 2/2/2 caps) → Phase 4 selective optimizers
(abstract + methods audit always; reviewer-flagged conditional) →
Phase 5 iterative citation rounds (5–8 adaptive; bounded supplementary
pool round when `[NEEDS CITATION]` markers exist) → Phase 6 compliance
gate (deterministic, build-fails-if-missing) → Phase 7 copy-edit
(clarity + concision; semantic-invariance post-check) → Phase 8 docx.
SPEC.md is the authoritative spec; M1–M8 are scoped milestones.

**Rationale.** Three converging signals (per the auto-memory entry
`project_paper_writer_v0_8_architecture.md`): (1) the per-section
sprawl pattern is asymptotic — every live-test failure since v0.4
produced a per-section patch, indicating the bottleneck is upstream;
(2) AI Scientist (Lu et al., *Nature* 2026) empirically reached
workshop-bar-not-main-bar for fully-AI papers with the most-engineered
per-section pipeline, validating that the bottleneck is story
selection / integrative finding curation, not per-section
sophistication; (3) the IBD one-shot exercise (2026-05-06/07) showed
BERIL won on methodology honesty (provenance, citation pool, scope
discipline — all Phase-0 outputs) but lost on integrative biology
to a single Claude conversation that saw the whole project at once.
Conclusion: BERIL's edge is the deterministic tooling, not the
section prompts. Subtraction-over-addition: most v0.4–v0.7 complexity
is downstream patches of upstream prompt weakness; v0.8 replaces
the patches with a stronger upstream pass.

**Twelve sign-off sub-decisions (Q1–Q12; all locked 2026-05-07).**

1. **Q1 — discrepancy register: LLM-assisted classification.** Pure
   string-match is too fragile for synonym/paraphrase robustness in
   hand-authored RESEARCH_PLAN.md text. (SPEC §4.5)
2. **Q2 — claim_inventory: full coverage; no salience filter.** TSV
   form scales; the holistic prompt's word budget is the natural cut.
   Revisit at v0.8.x if a project's inventory exceeds ~150 claim_ids.
   (§4.6)
3. **Q3 — STRONG/THIN/EXPLORATORY triage: rolled into story builder.**
   No separate `discovery.v1` step; outline is tier-shaped; verdict
   recorded in `00_story_outline.md` frontmatter. (§5)
4. **Q4 — holistic-write default model: Opus 4.6.** Accepted +$2–3/run
   trade-off vs Sonnet for integrative-biology quality. Sonnet
   available via `--model claude-sonnet-4-6`. (§6.7, §14)
5. **Q5 — fallback reviewer: kept, rewritten to v3 schema.**
   `fallback_reviewer.v2.md` runs at Tier 3 when beril-adversarial CLI
   absent; logged in `audit/cascade.jsonl` as `reviewer:"fallback"` for
   M7 score-sheet distinguishability. v0.8.0 does NOT hard-require
   beril-adversarial. (§7.4, §15)
6. **Q6 — `[NEEDS CITATION]` handling: bounded supplementary pool.**
   Holistic write may emit `[NEEDS CITATION: <topic>]`. Tier 1 counts;
   `phase_supplementary_pool` runs at Phase 5 boundary if any markers
   exist (≤15 entries / 5 per topic / 1 round; verified-by-resolution;
   tagged `source: "supplementary_round"` in pool.json). Residual
   markers halt to `phase=citation_gap_blocked` with three user
   choices (scope-down / accept-as-limitation / manual-citation).
   (§9.5)
7. **Q7 — copy-edit: on by default; broader scope (clarity + concision).**
   Line-diff cap replaced by per-claim semantic-invariance post-check:
   5 hard invariants (claim_id cross-walk, citation cross-walk,
   numeric-token preservation, hedge-marker non-increase, header
   preservation) + 1 budget invariant (≤15% manuscript-level
   word-count delta, retry-once on overrun). Failing 1–5 rejects
   wholesale; failing 6 retries with tightened prompt budget. (§11)
8. **Q8 — M7 cut-over reviewer: Adam-only.** Structured user-centered
   review deferred to a post-v0.8.0 launch milestone. M7 is a
   research-iteration decision, not a public launch. (§16)
9. **Q9 — release shape: v0.8.0 IS the cut-over.** No parallel-track
   v0.9.0 staging release. M7 gate is the decision point. (§17 M8)
10. **Q10 — archive layout: directory-layout interpretation.**
    `prompts/archive/v0_7/` mirrors active `prompts/` filenames;
    resurrection is path-mechanical; no PROVENANCE.md (git history
    is the canonical record). (§15)
11. **Q11 — old V0_8_0_PUNCH_LIST.md disposition: renamed.** Renamed
    to `archive-v0_8_language_quality_punch_list.md` 2026-05-07.
12. **Q12 — SPEC.md vs SPEC.md: coexist through v0.8.x.**
    Consolidate at v1.0.

**Alternatives considered.** Continuing v0.7.x patch cycle (rejected:
asymptotic; downstream symptoms of upstream weakness); a different
review-tier shape (e.g., 5-reviewer ensemble per AI Scientist —
rejected: AI Scientist's empirical failure to clear the main-conf
bar suggests reviewer-count-doubling is not the lever); requiring
beril-adversarial as a hard dependency (rejected per Q5: skill must
remain installable in environments without the sibling skill);
strict no-`[NEEDS CITATION]` policy at holistic write (rejected
per Q6: forces fabrication or premature reframing).

**Migration discipline.** v0.7.x stays default until M7 cut-over
decision. v0.8.0 ships as opt-in `--writer-version v0_8` during
M2–M6. M8 is the cut-over commit + release notes + MIGRATION_NOTES.md
+ state-schema migration script. v0.7.x section prompts move to
`prompts/archive/v0_7/` rather than being deleted (per Q10).

**Cut-over gate (M7).** A/B run on ibd_phage_targeting; v0.8.0 must
dominate v0.7.x on ≥4 of 6 metrics (token cost, wall-clock,
adversarial findings count, plan-vs-execution gap count, citation
accuracy, paper-review skill assessment) OR have documented
accepted-trade-off reason. If gate fails: keep v0.7.x default; ship
v0.8.0 as experimental flag.

**Per-milestone discipline (per `feedback_punch_list_release_pattern.md`).**
Each milestone: punch list at start (`M{N}_PUNCH_LIST.md`), smoke
test at end (`tests/smoke/m{N}_smoke.py`), decision-log entry on
non-obvious choices, memory summary at close.

**Cost target.** Typical run $7.50–$16 / 22–38 min (1.7–2.0× v0.7.x);
explicit accepted trade-off for the integrative-biology quality gain.
M7 measures actual cost; if v0.8.0 cost lands above 1.3× *and*
quality dominance is unclear, M7 go/no-go shifts toward keeping
v0.7.x as default.

**Related:** SPEC.md (authoritative spec for all 8 phases);
auto-memory `project_paper_writer_v0_8_architecture.md` (decision
context); auto-memory `project_paper_writer_v0_8_m0.md` (M0 ship
summary); the prior `archive-v0_8_language_quality_punch_list.md`
(superseded approach, kept for trail).

---

*Append new decisions below this line. Use the next D-NNN ID.*


## D-035 — Q1 cost-justification of LLM-assisted overlap classifier deferred

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 §C1.b ablation close-out.
**Authoritative analysis:** `smoke-test/M1_PUNCH_LIST_ablation_notes.md`.

**Decision.** Defer Q1 reconsideration to v0.9 architectural conversation.
The LLM-assisted overlap classifier remains the shipped path; its cost
is observed (≤ $0.05/run held trivially) but its *value* is not measurable
on the current milestone smoke target.

**Why deferred (not closed positively or reopened).** §C1.b's gate
required `delta = E_llm \ E_strmatch ≠ ∅` AND a hand-found paraphrase
list to confirm cost-justification. On `ibd_phage_targeting`:

- The deterministic pre-pass produced **0 overlap candidates**, so the
  LLM was never invoked in either ablation leg. `delta = ∅` by
  construction, not by LLM inadequacy.
- The hand-list (5 paraphrase-equivalent pairs HE1–HE5) revealed that
  HE2–HE5 live in `## Hypothesis Framework` Pillar sections that the
  plan-side regex (`analys[ei]s|method|test|stat`) does not parse.
- HE1 (in a parsed section) does not cross the
  `_OVERLAP_RATIO_THRESHOLD = 0.5` containment-over-min match because
  the plan bullet is multi-clause prose; normalized-token overlap
  with the executed phrase ≈ 0.17.

The two upstream defects (plan-parser scope + threshold restrictiveness
on prose-heavy bullets) make `overlap = ∅` the rule on dense BERIL
projects, not the exception. Re-evaluating Q1's cost-vs-value tradeoff
requires fixing the parser/threshold first; doing so is a v0.9
architectural conversation per watch-fors #1 + #3 in
`.auto-memory/project_paper_writer_v0_8_m1_a1.md`.

**Migration discipline.** No code change at this commit. The
`_OVERLAP_RATIO_THRESHOLD` constant remains hardcoded at
`discrepancy_register.py:501`. The plan-side heading regex remains at
`discrepancy_register.py:260`. M1 ships with the cost-justification
question open and a path on file.

**Related:** SPEC §4.5; M1_PUNCH_LIST.md §C1.b; the ablation report at
`smoke-test/M1_PUNCH_LIST_ablation_notes.md`; auto-memory
`project_paper_writer_v0_8_m1_a1.md` watch-fors #1 + #3.


## D-036 — B1 regex catalog extension for C2.b recall gate

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 §C2.b recall-pass +
B1.e ship.
**Authoritative analysis:** `smoke-test/M1_PUNCH_LIST_claim_groundtruth.md`.

**Decision.** Extend the `claim_inventory.py` B1.b deterministic regex
catalog from 6 classes to 11 classes, plus relax two existing classes,
to clear the §C2.b ≥ 0.90 recall gate against realistic BERIL project
content. Shipped as the B1.e patch.

**The seven catalog changes.**

1. `PERCENTAGE_RE` — relaxed from `\b\d+(?:\.\d+)?%` to
   `\b\d+(?:\.\d+)?\s{0,2}%`. REPORT.md authors routinely write `95 %`
   with a space; the prior regex missed 9 of 12 percentage patterns
   on `ibd_phage_targeting`.
2. `P_VALUE_RE` (sci-notation branch) — made the dot+fractional
   optional in the mantissa and added `<` to the operator class, so
   `p=7e-17` and `p<1e-31` match.
3. `P_VALUE_RE` (decimal branch) — added Unicode `≤` (U+2264) and `≥`
   (U+2265) to the operator class. BERIL Methods sections use them.
4. New class `correlation` — Pearson `r =` and Spearman `ρ =` with
   optional sign (incl. Unicode minus U+2212). Sets `effect_size_present`.
5. New class `odds_ratio` — `OR=1.38`, `OR = 8.1` etc., word-boundary
   anchored. Sets `effect_size_present`.
6. New class `log_fc` — `log₂FC +2.67` (subscript ₂ U+2082), `log2FC -1.42`
   (ASCII), with optional sign. Sets `effect_size_present`.
7. New class `count_of` — `M of N` and `M / N` forms with comma
   separators (`14 of 23`, `3,929 / 17,672`). Stays unflagged like
   `n_count`.
8. New class `cliff_delta` — `cliff δ = +0.50` (with U+03B4 glyph) and
   no-glyph `cliff = -0.358`. Sets `effect_size_present`.

(Counting: two existing-class relaxations + five new classes = "seven
catalog changes" per the punch-list framing; the SPEC §4.6 catalog
size goes from 6 to 11.)

**Empirical result (sandbox dry-run, 2026-05-07).** Recall on the
48-pattern ground-truth file rose from **0.562 → 1.000** on
`ibd_phage_targeting/REPORT.md`. Inventory candidate count rose from
87 → 401, with unresolved (multi-numeric) count rising from 10 → 133.

**Cost implication.** 133 unresolved demarcations × ~$0.011 each ≈
$1.50 live-LLM spend per run on `ibd_phage_targeting`. This is ~15×
the prior `_DEMARCATOR_COST_CEILING_USD = 0.10` constant. Per the
adjacent cost-cap reframing (Adam directive 2026-05-07), the ceiling
constant is no longer enforced as a stderr warning or smoke gate;
cost is recorded in the audit JSONL for later tightening.

**Anti-fabrication discipline preserved.** Every new class requires a
decimal point in the value (except `count_of`, where integer counts
are the norm) — matches the existing METRIC_RE convention. Word-
boundaries on keyword anchors (`\bOR`, `\b[rρ]`, `\bcliff`, `\blog`)
prevent mid-token false positives.

**Tests.** Six new unit tests in `TestB1eRegexCatalogD036`
(`test_claim_inventory.py`), one per change-block. Combined with the
two B1.e validator tests + two carry-over fix tests, this is +9 tests
on top of B1.b's 18 existing tests = 27 in `test_claim_inventory.py`,
with the discrepancy_register suite at 33 (also +1 for the cost-on-exit-4
fix). 60/60 unit tests green.

**Related.** SPEC §4.6; M1_PUNCH_LIST.md §C2.b; the recall report at
`smoke-test/M1_PUNCH_LIST_claim_groundtruth.md`; B1.e companion fixes
(validator project_root fallback, exit-4 cost recording, cost-cap
reframing) shipped in the same commit cycle.


## D-037 — Cost caps tracked, not enforced, through M1

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 close-out (B1.e ship).
**Decided by:** Adam, conversational 2026-05-07.

**Decision.** Per-call cost ceilings on Phase-0 tools
(`_COST_CEILING_USD = 0.05` for `discrepancy_register.py`,
`_DEMARCATOR_COST_CEILING_USD = 0.10` for `claim_inventory.py`) are
recorded as informational constants. The stderr warning emission on
overrun is removed; the smoke harnesses' cost AC gates are removed.
Audit JSONL continues to record `cost_usd` per call.

**Rationale (Adam, verbatim):** "we shouldn't take cost caps too
seriously for now. It is more that we should be tracking costs so
later we can determine what the caps should be."

**Why this is the right framing for M1.** The B1.e regex catalog
extension (D-036) raised the per-run demarcation workload from ~10
to ~133 multi-numeric sentences on `ibd_phage_targeting` — a 13×
load increase that would have triggered the prior $0.10 ceiling on
nearly every dense-prose project. Holding the gate would have
either (a) blocked legitimate work or (b) forced premature catalog
narrowing to fit an arbitrary cap. Tracking-only lets observed data
drive the cap.

**Carry-forward.** M2's holistic-write phase will reintroduce a
cost-aware orchestrator-level circuit breaker that aggregates Phase-0
+ Phase-2 spend, with a cap derived from observed M1 distributions.
At that point individual tool ceilings can be re-tuned from data.
The constants stay in code as documentation-only and forward hooks.

**Related.** SPEC §4.5 + §4.6; B1.e patch surface in
`smoke-test/M1_PUNCH_LIST_claim_groundtruth.md` §4.d.iii.


## D-038 — Demarcator batching to avoid output truncation + subprocess timeout

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 §C2 close-out (B1.f patch).
**Authoritative analysis:** Live-LLM smoke output captured in
`smoke-test/M1_PUNCH_LIST_claim_groundtruth.md` §4.f.

**Decision.** `claim_inventory.py`'s LLM demarcator pass batches the
unresolved-candidate list into chunks of `--batch-size` (default 15)
and calls the LLM N times rather than once. Per-batch local indices
[0..batch_size) are offset back to absolute positions; per-batch costs
are summed; full coverage is validated after all batches return.

**Why.** Live-LLM smoke on `ibd_phage_targeting` post-B1.e produced
**133 unresolved candidates** in a single demarcator call. Two failure
modes were observed:

1. **Output truncation.** The first attempted call billed $0.1856 and
   returned demarcations for only 91 of 133 indices — the validator's
   coverage check rejected with 42 missing indices. The LLM was hitting
   an effective per-response output-token cap (or refusal-style
   abbreviation) on a ~50K-output prompt.
2. **Subprocess timeout.** Re-running the same call hit the
   `demarcator_llm_call` 180s timeout. Wall time on a ~65K-input
   ~50K-output Haiku 4.5 call exceeded the wrapper's budget.

**Calibration of default batch_size = 15.** Per-batch input ≈ 15
sentences × ~400 chars ≈ 6K characters of candidate text + ~12K of
context = ~18K input characters ≈ ~5K input tokens. Per-batch output
≈ 15 × ~3 demarcations × ~300 chars ≈ ~13K output characters ≈ ~3K
output tokens. Wall time per call ≈ 30s on Haiku 4.5. Cost per call
≈ $0.10. The 180s subprocess timeout has 6× headroom; the model's
output budget has order-of-magnitude headroom. On
`ibd_phage_targeting` (133 unresolved): 9 batches × 30s = ~5 min wall;
9 × $0.10 ≈ $0.90 total spend.

**Cache discipline.** `compute_cache_key` adds `batch_size` as an
optional seventh component, mixed into the key only when explicitly
set. Following `feedback_cache_key_chunked_only_when_chunked.md`: a
B1.b cache file written before batching existed remains valid (legacy
6-tuple key when `batch_size=None`), but ANY explicit batch_size
mixes a new component in. Different batch sizes yield different cache
keys → switching from default 15 to 10 forces a fresh demarcator
sweep. This is correct: changing chunking changes the LLM's per-call
context, which changes the response, which means the cached
demarcations are no longer canonical for the new chunking.

**Edge cases handled.**
* `unresolved_candidates ≤ batch_size`: single-batch fast path
  preserves pre-B1.f behavior; no offset arithmetic; tests using
  small synthetic fixtures (B1.b's 18 tests, B1.e's 9 tests) all
  remain byte-identical to legacy.
* Per-batch LLM failure: the `LLMCallError` is annotated with the
  batch number ("batch 5 of 9 (candidates 60..74): ...") so a
  Mac-shell operator can pin which call to re-run. The accumulated
  cost from successful prior batches is still summed to
  `total_cost_usd` at exception time, but the partial failure
  collapses the run (caller exits 3 without writing TSV).
* Empty `unresolved_candidates`: no LLM call, returns `([], 0.0)`
  per the original short-circuit.
* `batch_size < 1`: rejected at CLI parse time + at function entry
  with a clear `ValueError`.

**Anti-fabrication discipline preserved.** The validator's full
coverage check still runs on the merged result; B1.e's
project_root-fallback substring check still gates each row. Batching
is purely an LLM-throughput concern.

**Tests.** Two new unit tests in `TestB1fDemarcatorBatching`:
* batched run with batch_size=2 on a 6-candidate fixture confirms 3
  LLM calls, summed cost, full coverage, 12 demarcated rows in TSV.
* `compute_cache_key` regression: `batch_size=None` is legacy-key-
  compatible, distinct from `batch_size=15`, distinct from
  `batch_size=10`.

Test sweep: 62/62 unit tests green (33 disc + 29 claim). 27 + 32
B1.b/A2 baseline preserved (no regression).

**Related.** SPEC §4.6; M1_PUNCH_LIST.md §C2; B1.e shipped catalog
extension (D-036) that drove the candidate-count jump from ~10 to
~133 on dense projects; D-037 cost reframing makes the ~$0.90/run
spend acceptable as observability while the M2 orchestrator-level
circuit breaker is designed.


## D-039 — Bounded retry on missing indices + tolerated_missing fallback

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 §C2 close-out (B1.g patch).
**Authoritative analysis:** `smoke-test/M1_PUNCH_LIST_claim_groundtruth.md` §4.g.

**Decision.** `claim_inventory.py`'s LLM demarcator pass now (1)
re-batches missing input_candidate_indexes into a fresh LLM call, up
to `max_retries=3` rounds, and (2) tolerates residual misses by
falling back to the original `notes='unresolved'` row through
`expand_with_demarcations`' existing defensive empty-rows path. The
validator's coverage check now accepts an `allow_missing` kwarg that
the demarcator threads in after retries exhaust.

**Why.** Live-LLM smoke on `ibd_phage_targeting` post-B1.f produced
**different missing indices on every run** (run-to-run: [69, 72]; [39,
95]; [39, 72, 95]). Three observations together drove the design:

1. Missing indices are non-deterministic — the LLM is dropping
   ~1.5–2.5% of inputs per dense-project run as an intrinsic
   variance, not a systematic gap.
2. Re-running the missing candidates as a fresh batch typically
   recovers them (the LLM doesn't drop the same row across all
   attempts).
3. After 3 retries, residuals are rare enough (<1% of inputs in
   observation) that abandoning the candidate to its original
   `notes='unresolved'` row preserves the M2 holistic prompt's
   grounding contract: every input gets ≥1 row in the inventory,
   regardless of whether B1.c actually demarcated it.

**Calibration of `max_retries=3`.** Two retries is too tight if the
first retry partially succeeds and the second needs a third pass
(observed in synthetic test stress runs). More than three doubles
cost without meaningful coverage gain in observation. Per Adam D-037
cost reframing, retry cost is recorded in audit JSONL but not gated.

**Anti-fabrication discipline preserved.** Every row that IS emitted
goes through the same per-row checks (claim_text substring,
source_notebook ground via methods_provenance OR project_root disk
check, source_cell shape, figure_or_table ground). The fallback path
preserves the original deterministic-pre-pass row verbatim — no
fabricated demarcation rows are produced. The TSV's `notes` column
distinguishes resolved (empty) from residual fallback (`unresolved`).

**Cache schema extended.** Cached payload gains `tolerated_missing:
[int]` field. Backwards-compat: payloads written before B1.g lack the
key entirely → `_cached_payload_to_tolerated_missing` returns empty
set, which keeps pre-B1.g caches validating against full coverage
(matching their original semantics; if they had partial coverage they
wouldn't have been written). On B1.g write, `tolerated_missing` is
sorted-list-serialized for stable hashes.

**Edge cases handled.**
* `unresolved_candidates ≤ batch_size`: single-batch fast-path still
  triggers when len(unresolved) is small; retry loop is no-op when
  initial pass covers everything.
* All retries succeed: `tolerated_missing = ∅`; behavior identical
  to B1.f.
* Initial pass + 3 retries persistently drop the same index: cache
  records `tolerated_missing=[k]`; rerun reads from cache, skips
  LLM, produces byte-identical TSV.
* `max_retries=0`: turns retry loop off; `allow_missing` set to
  whatever's missing after the initial pass. Useful for
  deterministic-only smoke tests + sandboxes that can't make many
  LLM calls.
* Per-batch `LLMCallError` annotated with pass-label
  ("initial batch 5 of 9..." vs "retry-2 batch 1 of 1...") for
  diagnostics.
* Out-of-range LLM index in a batch response: defensively dropped
  rather than raised; the coverage check catches the resulting gap
  and triggers retry.

**Tests.** Two new unit tests in `TestB1gRetryOnMissingIndices`:
* Stateful fake drops the last input on call #1, recovers on retry
  → 2 LLM calls, full coverage, exit 0.
* Stateful fake persistently drops S2 on every call → 1 initial +
  3 retries = 4 LLM calls, residual `[1]` falls through to original
  unresolved row, exit 0, cache persists `tolerated_missing=[1]`.

Test sweep: 64/64 unit tests green (32 disc + 32 claim). 18 + 32
B1.b/A2 baseline preserved (no regression).

**Related.** SPEC §4.6; M1_PUNCH_LIST.md §C2; B1.f shipped batching
(D-038) that exposed the residual-drop variance issue — small,
batched calls succeed but ~98% per-batch coverage means ~2% of dense-
project inputs fail without retry; D-037 cost reframing makes the
retry round acceptable as observability while M2 designs an
orchestrator-level cost circuit breaker.


## D-040 — Demarcator user prompt gains explicit cite allowlists

**Filed:** 2026-05-07. **Milestone:** v0.8.0 M1 §C2 close-out (B1.h patch).
**Authoritative analysis:** `smoke-test/M1_PUNCH_LIST_claim_groundtruth.md` §4.h.

**Decision.** `build_demarcator_user_prompt` extracts every notebook
path from methods_provenance.md and every figure/table label from
figures_inventory.md + tables_inventory.md, then emits two explicit
"VALID values" allowlists at the TOP of the user prompt — BEFORE the
INPUTS section. The system prompt at `prompts/claim_demarcate.v1.md`
gains anti-pattern worked examples that calibrate against the
specific fabrications observed live.

**Why.** Live-LLM smoke after B1.g revealed two new defects that
survive bounded retries (and would survive any number of retries
because the LLM is consistently making the same kind of error):

1. **Notebook-name truncation.** Real path:
   `notebooks/NB07a_pathway_DA_H3a_falsifiability.ipynb`.
   LLM emitted: `notebooks/NB07a_H3a_falsifiability.ipynb` — dropped
   the `pathway_DA_` substring. The model was paraphrasing the
   notebook by what it does (per RESEARCH_PLAN.md's H3a
   falsifiability framing) rather than by what it's named.
2. **Notebook-ID-as-figure-label.** LLM emitted
   `figure_or_table="Fig NB15"`. NB15 is the *notebook* that
   produced figures; it is not itself a figure label. The
   figures_inventory uses `Fig N` form; the LLM mashed two
   conventions together.

The prompt's prior anti-fabrication paragraph wasn't enough — the
model needed an explicit menu it could copy from. Reformulating
"don't fabricate" as "here are your only valid choices, copy
verbatim" is a more reliable LLM-grounding pattern (cf.
`feedback_llm_arithmetic_unreliable.md`'s general rule:
post-correction beats prompt-instruction for any
mechanically-checkable property).

**Anti-fabrication discipline preserved.** The validator's per-row
checks (substring of methods_provenance.md OR is_file() under
project_root for source_notebook; substring of figures/tables
inventory OR empty for figure_or_table) are unchanged. The
allowlist is a *guide*, not a *gate* — the validator is the gate.
A misbehaving LLM that ignores the allowlist still hits exit 4.
This is the right shape: prompt nudges the LLM toward correct
behavior, validator enforces it.

**Cache invalidation.** The allowlist content is a function of the
input files, so it's already part of the cache key via the
methods/figures/tables SHAs. The system prompt edit changes the
prompt SHA → cache key changes → previous cache entries don't hit
on rerun. Both behaviors correct: any change to grounding inputs
invalidates the cache.

**Tests.** Two new unit tests in `TestB1hAllowlistsInUserPrompt`:
* `_extract_notebook_paths` returns sorted unique paths;
  `_extract_figure_or_table_labels` returns ordered unique labels
  from `## Fig N` / `## Tbl N` / `## Table N` headings.
* `build_demarcator_user_prompt` emits both allowlist sections
  with the correct anti-pattern reminders, and the allowlist
  appears BEFORE the INPUTS section in the prompt order.

Test sweep: 66/66 unit tests green (33 disc + 33 claim). 18 + 32
B1.b/A2 baseline preserved across e/f/g/h.

**Related.** SPEC §4.6; M1_PUNCH_LIST.md §C2; the `claim_demarcate.v1.md`
prompt (in-place edit, SHA bumps automatically); B1.g shipped retry
discipline (D-039) that recovers occasional drops but cannot fix
systematic fabrication that the LLM repeats across attempts —
allowlists are the layer that addresses repeating errors.

---

## D-041 — 2026-05-12 — Stage 3 Tier A: figure staging in phase_assemble

`assemble_docx.py` resolves image paths against `manuscript.md.parent`
and rejects any path containing `..` (defensive against path
traversal). The canonical figures inventory uses paths of the form
`figures/X.png` (relative). Without staging, those resolve to
`<draft_dir>/figures/X.png` which doesn't exist; every figure renders
as `[FIGURE MISSING: ...]`.

Decision: `phase_assemble` symlinks `<project>/figures/` →
`<draft_dir>/figures/` before invoking the renderer (copy fallback for
Windows / cross-volume). Idempotent; preserves a user-managed real dir.

Latent failure mode fixed: pre-v0.7.x runs happened to wrap image
markdown in blockquotes (`> ![...]`), which the renderer silently
treated as prose — zero warnings, zero embeds. The figure-embed loop
had never actually shipped working end-to-end.

**Related.** D-042 (prompt-side pinning); D-049 (J.1 refinement —
replace empty pre-existing dirs).

---

## D-042 — 2026-05-12 — Stage 3 Tier B: holistic_draft image-block form

`holistic_draft.v1.md` had an ambiguous worked example
(`> **Figure 1.** Caption text...`) that gave the LLM license to wrap
the entire figure block in a blockquote. The renderer's block-image
parser only recognizes a line whose entire content is a bare
`![alt](path)` — wrapping silently demotes to prose.

Decision: rewrite the prompt's image-embed section to pin the
two-block pattern (bare image + adjacent `**Figure N.** caption`)
with an explicit anti-pattern callout for the blockquote form.

---

## D-043 — 2026-05-12 — Stage 3 Tier C: citation-pool schema is `entries[]`

`citation_pool.v1.md` writes `{"entries": [...]}`. A Stage 2 rewrite
of `holistic_draft.v1.md` and `supplementary_citations.v1.md`
inverted this to `citations[]` — a directionality bug. Latent because
no `[NEEDS CITATION:]` markers landed on draft_8/9; would fire the
moment supplementary phase tried to append.

Decision: reverse both prompts to match the canonical `entries[]`
shape.

---

## D-044 — 2026-05-12 — Stage 3 Tier D: state.tier population from candidates

The Python orchestrator's plan phase emits
`throughline_candidates.md` including a `**Tier:** STRONG|THIN|EXPLORATORY`
verdict. The bash flow has always parsed this via
`paper_writer_helpers extract-tier` and written `state.tier`. The
Python orchestrator never did, leaving `state.tier = None` on every
Python-flow draft. Downstream consumers (adversarial reviewer, word-
budget prompts) default to EXPLORATORY when tier is null regardless
of actual project rigor — silent degradation.

Decision: at the end of `phase_plan`, call
`paper_writer_helpers._extract_tier_from_text` (canonical regex,
same as bash flow) against `throughline_candidates.md` and write
the result to `state.tier`.

---

## D-045 — 2026-05-12 — Stage 3 Tier G: phase_triage model pin + cost tracking

`phase_triage`'s claim-extraction and discrepancy-audit `claude -p`
calls had no `--model` flag and bypassed `_run_claude_p_with_cost`
— the only major LLM calls that did so. An unpinned `claude -p`
resolves a different default model in a nested Claude Code session
than from a plain shell. On draft_9 this produced a categorical
formatting change in `source_notebook` values (bare stems / em-dash
placeholders / 76% validator clear-rate vs the ~10% steady-state
band on draft_4–8).

Decision: route both calls through `_run_claude_p_with_cost` with
`model=self.model`. Fixes the trigger AND closes a cost-tracking
hole (triage spend was invisible to `state.cost_so_far_usd`).

**Related.** D-046 (prompt amplifier) + D-047 (validator backstop)
land alongside; mechanism > prompt > backstop, in defense order.

---

## D-046 — 2026-05-12 — Stage 3 Tier H: extract_claims.v1.md exact-filename rule

`extract_claims.v1.md` was thin on `source_notebook` format — one
example, "if applicable", no exact-filename rule. The Tier G model
pin alone removes the trigger but a loose prompt invites recurrence
under future model changes.

Decision: add an explicit format-rule section
("emit the exact `.ipynb` filename from methods_provenance.md; no
abbreviation, no parenthetical, no em-dash/N/A placeholder") with
a worked counter-example table.

---

## D-047 — 2026-05-12 — Stage 3 Tier I: validator repair pass

`validate_claim_inventory.py` previously only CLEARED rows whose
`source_notebook` didn't resolve to a real file. On reconstructed
draft_9 input, this cleared 191/250 rows.

Decision: add a conservative *repair* pass keyed on the notebook-ID
grammar `^NB\d+[a-z]?`. On a successful repair, rewrites
`source_notebook` to the full real filename and notes the repair in
the `notes` column (`notebook-repaired: <orig> -> <full>`). Only
fires on unambiguous matches:

1. Missing-extension recovery (`cleaned + ".ipynb"` is a real file).
2. Notebook-ID recovery (value's ID matches exactly one real notebook).

Rejects placeholders (`—`, `N/A`, `TBD`), slash-joined refs
(`NB04b/c`), values naming >1 notebook. On reconstructed draft_9:
183/191 repaired, 8 correctly stay cleared. Diagnostic JSON gains
additive fields `rows_repaired_this_run` and `repaired_notebooks`.

---

## D-048 — 2026-05-12 — Stage 3 Tier J: absolute-path resolution for `claude`

`asyncio.create_subprocess_exec("claude", ...)` relies on a PATH
lookup at spawn time. Observed 2026-05-12: a backgrounded
`beril-paper-writer` invocation under Claude Code's Bash tool raised
`FileNotFoundError: 'claude'` while the identical foreground
invocation succeeded — the nested context's environment did not
carry the directory where `claude` lives (nvm bin), even though the
interactive shell did.

Decision: resolve `claude` to an absolute path once at orchestrator
init via module-level `resolve_claude_bin()`. Resolution order:
`BERIL_CLAUDE_BIN` env override → `shutil.which` → well-known
locations. Fails loud at init listing searched paths if unresolvable.
All four `claude -p` call sites use `self.claude_bin` (absolute) —
spawn is context-independent.

Adjacent fixes folded into Tier J:
- `draft.py` implements the documented `projects/<id>/` path fallback
  (was in `--help` but never coded).
- `draft.py` catches construction-time `RuntimeError` cleanly with a
  stillborn-draft-dir hint.
- `configure.py` calls the same `resolve_claude_bin` the orchestrator
  uses (previously a bare `shutil.which` that gave false greens when
  the spawn context's PATH differed).

---

## D-049 — 2026-05-12 — Stage 3 Tier J.1: figure staging — empty-dir override

Tier A deferred to a pre-existing real `figures/` dir assuming
user-managed content. Live draft_1 revealed an *empty* `figures/` dir
created as a side effect of
`extract_figures.py --output-dir <draft_dir>` — making Tier A a no-op
and producing 14 `WARN: image file not found`.

Decision: defer to a real directory ONLY when it has content.
If empty, `rmdir` it and create the symlink.

---

## D-050 — 2026-05-12 — Stage 3 default `model` flipped from Sonnet to Opus

`self.model` (orchestrator constructor parameter) defaulted to
Sonnet 4.5 and drove the load-bearing reasoning phases (plan,
triage, optimizer, compliance_fix, supplementary_pool) — where the
consequences of error are highest. Holistic draft was already Opus;
the silent Sonnet default for scaffolding was backwards.

Decision: flip `self.model` default to `claude-opus-4-6`. Tier-2
light review stays on Haiku by design. `--model` overrides. Applies
to bare invocations and to `continue` (which never passed a model
and always took the constructor default).

Cost trade-off: ~3× Sonnet on the affected phases. Per cycle stance:
cost is being measured, not optimized.

---

## D-051 — 2026-05-16 — Stage 3 Tier K: beril-adversarial resolution + loud-warn fallback

`phase_review` Tier 3 had a bare-name `["beril-adversarial", ...]`
spawn — same class of PATH-visibility bug Tier J fixed for `claude`.
On a live draft, the canonical reviewer silently didn't fire despite
being on PATH per configure; the manuscript shipped reviewed only by
the lighter inline fallback, letting through findings the canonical
reviewer catches (an Eggerthella-vs-Enterocloster binomial conflation
slipped through).

Decision (mirrors Tier J but with optional semantics):

- Module-level `resolve_adversarial_bin()` returns an absolute path
  or `None` (NOT raising — adversarial is required-by-default-with-
  fallback, not hard-required). `BERIL_ADVERSARIAL_BIN` env override.
- Orchestrator `__init__` logs a LOUD WARNING at init if canonical is
  missing and `--no-adversarial` not set, so the user knows minutes
  before phase_review what kind of review they're heading toward.
- `phase_review` Tier 3 branches three ways:
  1. canonical via absolute path,
  2. inline fallback with WARNING when canonical missing,
  3. inline fallback with INFO when `--no-adversarial` is explicit.
- `_run_fallback_reviewer()` invokes `fallback_reviewer.v1.md` via
  the cost-tracking helper; writes `reviews/fallback_review.md`.
- `_write_review_mode()` records the Tier-3 reviewer outcome in
  `audit/review_mode.json`. Values: `canonical` / `canonical-failed`
  / `fallback` / `fallback-failed` / `none`. Machine-discoverable
  consumer contract for downstream tooling.
- `--no-adversarial` flag plumbed through BOTH `draft.py` and
  `continue_run.py` (continue was previously accepting the flag via
  argparse but never passing it to the orchestrator constructor —
  silent drop).

Stated end-state is co-install (beril-adversarial as a hard
dependency of beril-paper-writer-skill); current behavior is
required-by-default-with-loud-fallback so missing-canonical can't go
unnoticed but doesn't hard-halt.

## D-052 — 2026-05-20 — Tier T extractor: scientific notation, K-suffix, trailing-zero normalization (#41)

Stage 7 dev runs surfaced systematic false-positive ungrounded
findings on D1/D2/D3 (V1_X_BACKLOG.md #41). Forensic reading of
the `audit/iter_1/numeric_grounding.json` against
`claim_inventory.tsv` + `REPORT.md`:

- D2 amr_pangenome_atlas — 12 of 14 ungrounded are scientific-
  notation mantissa-only matches. Manuscript `p = 1.1 x 10^-130
  [C-006]`; inventory C-006 `Wilcoxon p=1.1e-130`. Same value,
  same marker, different surface form.
- D3 phb_granule_ecology — 3 of 3 ungrounded are scientific-
  notation + K-suffix (`83,000` vs inventory's `83K`).
- D1 conservation_vs_fitness — 4 of 6 ungrounded are extractor
  artifacts: `n=22` truncated from `n=22,751` via comma-boundary
  (`N_COUNT_RE`'s `\b` at the comma); `82` vs `82.0` trailing-
  zero precision mismatch on C-006.

Root cause: `_NUMERIC_PAYLOAD_RE = [-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?`
requires an `[eE]` exponent marker. Manuscript `1.1 x 10^-130`
tokenizes as three numbers (`1.1`, `10`, `-130`); inventory
`1.1e-130` tokenizes as one. They never equal as normalized
strings. Compounding: `build_normalized_set` does not normalize
K/M/G/T suffixes; set lookup is exact-string with no trailing-zero
tolerance.

Decision: four sub-fixes in `check_numeric_grounding.py` +
`claim_inventory.py`. Approved choices (per
2026-05-20 in-chat sign-off; Q1=new class, Q2=source-side only,
Q3=`%.10g`-based canonical form, Q4=bundle comma fix):

1. **Scientific notation as a new match class.** Add
   `SCIENTIFIC_NOTATION_RE = \b\d+(?:\.\d+)?\s*[xX×*]\s*10\^?[-+]?\d+`
   to `claim_inventory.py`'s `CLASS_PATTERNS` at the TOP of the
   priority tuple so it claims the full pattern before
   `RATIO_WITH_UNIT_RE` (which currently includes `x` as a unit
   alias) can grab the mantissa-only partial. Add
   `"scientific_notation"` to `CLAIM_SHAPED_CLASSES` in
   `check_numeric_grounding.py` so it bypasses the trivial-
   noun-phrase suppressor. New class is neutral on flag-aggregation
   maps (does not set `effect_size_present` etc.).

2. **K/M/G/T SI-suffix expansion on the SOURCE side only.** New
   `_expand_si_suffixes(text)` helper. Applied to inventory +
   REPORT in `build_normalized_set`. Lookahead
   `(?=\s|$|[^\w])` guards against `1.5MHz`-class collisions
   (next char being a word char fails the lookahead). Manuscript-
   side untouched — drafters in formal scientific writing use
   expanded forms; expanding on manuscript side creates new
   false-positive surface.

3. **Trailing-zero canonicalization via `%.10g`.** New
   `_canonical_float_str(value)` helper. Uses Python's `format(v,
   '.10g')` with post-strip of leading exponent zeros via
   `re.sub(r"e([+-])0+(\d)", r"\1\2", s)`. Collapses `82` ↔
   `82.0`, `0.30` ↔ `0.3`, `1.77e-06` ↔ `1.77e-6`. Strict on
   truncation (`0.3` ≠ `0.302`). Applied symmetrically in
   `normalize_numeric` (manuscript side) and the new
   set-building paths (source side).

4. **`N_COUNT_RE` comma support.** Extend from `\b[nN]\s*=\s*\d+\b`
   to `\b[nN]\s*=\s*\d+(?:,\d{3})*\b`. Mirrors the comma-pattern
   `COUNT_OF_RE` already uses. Bundled into #41 because it's the
   same dev-set false-positive class even though it's a regex
   robustness fix, not a normalization extension.

Expected dev-set impact (D-NNN re-evaluation post-#41,
deterministic re-run of `check_numeric_grounding.py`, $0 LLM):

| Project | Pre | Post | Residual |
|---|---|---|---|
| D2 amr_pangenome_atlas | 14 | 0–2 | possibly a percentage edge case |
| D3 phb_granule_ecology | 3 | 0 | all three are sci-notation / K-suffix |
| D1 conservation_vs_fitness | 6 | 3 | 2× `95%` pangenome-definition (drafter brought from external source without claim_inventory backing; real residual for #40) + 1× `80% Tettelin` (external citation; allowlist territory) |

Non-goals (explicit deferrals):

- **Unicode superscript** (`10⁻⁴³`, `10⁻¹³⁰`) — not present in
  current dev set; file separately if it surfaces.
- **External-citation allowlist** for `80% Tettelin`-class cases
  — separate concern; lives at V1_X_BACKLOG.md #40 or a new entry.
- **Match-class-aware fuzzy matching** against
  `claim_inventory.tsv.claim_text` surrounding-sentence context —
  already deferred to v1.1 in `build_normalized_set`'s docstring
  (Stage 7 Patch 2 trade-off; same trade-off applies here).
- **`check_throughline_numerics`'s parallel sci-notation problem
  (#38)** — separate file with separate tests. #41 lands first;
  #38 reuses the canonical helper.

Test plan: ~18 new unit tests in `tests/unit/test_check_numeric_grounding.py`
(+ 2-3 in `test_claim_inventory.py`) pinned to actual D1/D2/D3
failure cases as regression fixtures. After tests pass, deterministic
re-run of `check_numeric_grounding.py` against existing dev-set
manuscripts to confirm the predicted impact above.

Related: V1_X_BACKLOG.md #41 (P0, replaces dev-set evidence of
#40); #38 (throughline-numerics, separate file scope); D-036
(B1.e PERCENTAGE_RE + N_COUNT_RE regex pedigree — N_COUNT_RE
comma fix is a continuation of B1.e robustness work);
`check_numeric_grounding.py` Stage 7 Patch 2 (2026-05-18) which
established the generic source-side extraction pattern.

## D-053 — 2026-05-20 — Retire paper_writer.sh + the v0.x checker tools

**Decision.** Delete the v0.x shell orchestrator `paper_writer.sh`
(~3468 LOC) and the 12 checker tools reachable only through it, as
part of the v1.0 ship. Amend SPEC §7.2 to match the v1.0
implementation.

**Context.** The v0.8 redesign (D-034) replaced the shell orchestrator
with the Python `PaperWriterOrchestrator`. `paper_writer.sh` was left
shipped-but-unused — a half-finished migration. The pre-v1 code +
documentation review (2026-05-20) found it was the root of: 5 docs
describing it as the live orchestrator; the `next_actions.md` artifact
cited as a real output (only paper_writer.sh wrote it); 12 checker
tools wired only into it; stale `cli.py` / `continue_run.py`
docstrings. Doc staleness and code dead-weight were one problem.

**Audit before deletion.** Each of the 12 tools was classified
against SPEC and the Python orchestrator:
- 5 SPEC-explicitly-RETIRED — `check_overclaim`, `check_scope_coherence`
  (both superseded by the canonical adversarial reviewer's Tier-2
  classes), `check_repair_scope` (no rewrite loop in v0.8),
  `check_throughline_glyphs` (story builder absorbs it),
  `ensemble_review` (never wired).
- 6 SPEC §7.2 claimed Tier 1 "subsumes" — `check_figures_manifest`,
  `check_tables_manifest`, `check_caption_provenance`,
  `check_sentence_complexity`, `check_abbreviation_discipline`,
  `check_echo_repetition`. The audit found this claim FALSE: the
  Python Tier 1 never wired them, and they are architecturally bound
  to the per-section + `*_manifest.tsv` artifacts the holistic write
  abandoned. All advisory (exit 0). Deferred to v1.1 (#48).
- 1 — `check_data_availability`: SPEC said KEEP, implementation
  orphaned it; v0.8's Data Availability path is the compliance_gate
  autofix. Deleted; folded into #48.

All 12 confirmed advisory and confirmed to have zero live imports
before deletion. 7 had test files (also deleted).

**Why defer the 6 rather than wire them in.** They are advisory-only
(never gated, even in v0.x); the Stage 7 holdouts passed v1-bar v2b
without them; and re-providing their function correctly means
v0.8-native rewrites against holistic artifacts, not as-is
integration. Bundling all of SPEC §7.2's unimplemented Tier-1 rows
into one honest v1.1 item (#48) is cleaner than shipping v1.0 with a
few freshly-wired advisory tools and the rest still missing. v1.0
ships the deterministic numeric/claim Tier-1 legs + the canonical
adversarial reviewer.

**Scope of the change.** Deleted: `paper_writer.sh`, 12 tool files, 7
test files. Edited: `install_skill.py` (chmod list), SPEC §7.2 + the
tool-disposition table, `cli.py` / `continue_run.py` / `draft.py`
docstrings, `draft.py` (removed the unreachable `PipelineHalted`
handler — `run_pipeline` catches it internally),
`check_numeric_grounding.py` `TOOL_VERSION` bump.

Related: D-034 (v0.8 holistic redesign — the "subtraction over
addition" intent this completes); V1_X_BACKLOG #48 (v1.1 Tier-1
buildout), #47 (superseded by #48).

## D-054 — 2026-05-25 — Route beril-adversarial exit codes; quarantine non-consumer-safe JSON

**Decision.** Replace `phase_review`'s binary `if rc != 0` handling of
the canonical adversarial reviewer with exit-code routing per
beril-adversarial CONTRACT.md v0.7.0.8. Exit 0/2 → `adversarial_review.json`
is consumer-safe. Exit 3/4/other → quarantine the on-disk
`adversarial_review.json` into `audit/rejected/` and fall back to the
inline reviewer. Ship as v1.0.1.

**Context.** The adversarial team shipped v0.7.0.7 (orchestrator-side
JSON auto-repair) + v0.7.0.8 (a schema-invalid-but-parseable `.json`
now exits 4, not 0) and asked consumers to confirm an `exit == 4`
branch. A compatibility review found paper-writer was NOT compatible —
and that the adversarial team's "messaging fix, not a correctness fix"
framing was wrong for paper-writer specifically:

- `phase_review` caught exit 4 in `if rc != 0` and logged ERROR, but
  then `advance_phase("p0_review")` ran unconditionally. The only
  failure record, `review_mode.json`, is write-only — nothing in
  `src/` reads it back.
- The two downstream consumers — `phase_p0_review` (via
  `p0_gate.count_p0_findings`) and `phase_optimize` — key on the
  *presence + parseability* of `adversarial_review.json`, not on the
  exit code. `p0_gate._load_json_safe` only guards against an
  *unparseable* file; a parseable-but-schema-invalid exit-4 `.json`
  sailed straight into the P0 count and the optimizer's dispatch.
- So "fail loud at the call site" did not stop the bad file being
  consumed — the loud log and the file consumption are in different
  phases joined by an unconditional advance. For a multi-phase state
  machine, catching ≠ halting.

Root cause: a stale assumption in the pre-fix branch-logic comment —
"on non-zero exit ... advance — the optimizer's missing-findings check
will skip cleanly." That assumed *failure ⇒ no parseable findings
file*. v0.7.0.8 broke it: exit 4 can ship a freshly-written,
parseable-but-invalid file.

**Why quarantine + fallback (not halt).** Quarantining the `.json`
restores the precondition the "skip cleanly" net depends on — an
absent file. The fallback is paper-writer's existing graceful-
degradation path (D-051): "exit 4 = canonical produced unusable
output" is morally identical to "canonical unavailable," which already
falls back. The manuscript still gets a Tier-3 review; `review_mode`
records `fallback` with a `canonical-exit-<N>` reason. Halting was
rejected as inconsistent with D-051. Per the adversarial contract a
single fresh re-run may clear a transient exit 4 — paper-writer does
not loop.

**Adversarial CONTRACT.md contradiction (flagged upstream).** That
document says both "exit 0 or 2 = consumer-safe" (exit-code table; bash
`case`; Python `returncode in (0, 2)` reference) and "exit 0 is the
only safe-to-parse signal" (two stray comments). paper-writer follows
the preponderance: exit 0 AND 2 are consumer-safe.
`classify_adversarial_exit()` encodes this; the adversarial team has
been asked to fix the contradiction in their CONTRACT.md.

**Resolved 2026-05-25.** The adversarial team confirmed all three
flagged points and corrected their CONTRACT.md — doc-only, no change to
the exit-4 signal, folded into v0.7.0.8. Exit 0 and 2 are now stated as
consumer-safe in the table and both reference consumers; exit 1 is
corrected to "user/usage error, non-retryable" (not "validation
failure") and exit 4 is now listed; a standing "Multi-phase consumers"
note was added (catching != halting; the not-consumer-safe signal must
cross phase boundaries). They also found and corrected the same
exposure in presentation-maker's `m6_score.py`. paper-writer v1.0.1's
routing already matches the corrected contract — no code change.

**Scope of the change.** orchestrator.py: new module-level
`classify_adversarial_exit()`; rewrote the `phase_review` Tier-3
exit-handling block; new `_quarantine_adversarial_json()` method;
updated the branch-logic comment + `_write_review_mode` docstring
(`canonical-failed` retired — never written now). tests: rewrote
`test_adversarial_interop.py::TestAdversarialExitCodeRouting` (it
asserted literal tuples and referenced the retired `paper_writer.sh`)
to exercise `classify_adversarial_exit`; added
`TestAdversarialJsonQuarantine`. CONTRACT.md: exit-code table,
review_mode table, version-compat row, fallback-coordination line.
Full suite green (1024 passed).

**Not done (deferred).** (1) A live cross-skill smoke test invoking a
real `beril-adversarial` — remains a runbook item (adversarial
CONTRACT.md asks every consumer for one; paper-writer's interop test is
fixture-only). (2) The P0 gate still advances silently when total
P0 == 0 even if the Tier-3 review degraded to fallback —
`p0_findings.md` is only written when total > 0. This predates D-054
and is a property of the fallback path generally, not the exit-4
change; left for a separate gate-observability item.

Related: D-051 (adversarial CLI resolution + loud-warn fallback);
D-005 (loose coupling); CONTRACT.md exit-code table;
`feedback_no_benchmark_gaming` (silent-wrong-answer doctrine);
`feedback_cross_skill_contract_drift` (why CONTRACT.md exists).

---

## D-055 — 2026-06-06 — CRAFT runtime-config standardization (§3.4)

**Decision (v1.1.0).** paper-writer conforms to CRAFT-CONTRACT.md §3.4
(runtime configuration contract v2). Specifically:

- `configure` is the CRAFT runtime-config bootstrapper (provider
  inference + tier-model discovery + `settings.{json,local.json}` +
  response-asserting validation ping with tier fallback). The legacy
  environment-audit incarnation of `configure` is retired; the only
  genuinely-required preflight is folded into the new flow.
- Per-phase tier mapping (Stage 6 → Stage 7): throughline / synthesis
  / review-incorporation → **reasoning**; body drafting →
  **standard**; claim classification → **fast**. Implementation routes
  through Claude Code's native `--model` aliases (opus / sonnet /
  haiku) resolved against `<BERIL_ROOT>/.claude/settings.json`. A
  caller-explicit `--model` still wins per §3.4.
- The Tier-2 narrative-light review resolves the model via the canonical
  helper, not the legacy `HAIKU_MODEL` env knob (back-compat hatch
  preserved when explicitly set).

**Rationale.** Each CRAFT skill (adversarial, paper-writer,
presentation-maker) was independently reading `.env` and constructing
provider/model wiring. That worked when one skill was the only
consumer, but coexisting on a shared BERIL `.env` required additive-
only conventions + a single resolver shape — otherwise skills shadow
each other's keys via python-dotenv's last-write-wins. §3.4 codifies
the convention and the canonical resolver shape; this skill is a
**copy-not-share** consumer of that resolver (CI conformance fixture
in craft-platform enforces the no-drift property).

**Alternatives considered.** (a) Keep paper-writer's bespoke env
parsing — rejected; that's exactly what §3.4 prevents on a shared
deployment. (b) Make the resolver a shared library — rejected per
§3.4: the conformance fixture replaces a shared library and avoids
inter-skill version dependency.

**Backward compatibility.** Old-style `.env` (only `CBORG_API_KEY`,
no `ACTIVE_PROVIDER` / `MODEL_*`) is explicitly supported: provider
inference returns `cborg`; `compose_env_append` does not redeclare
the existing key; tier models come from discovery. Pinned by
`test_old_style_env_upgrades_cleanly` in `tests/test_llm_config.py`.

**Related:** CRAFT-CONTRACT.md §3.4;
`handoffs/CRAFT-config-round2-CC-brief.md` (sub-round 2b
paper-writer brief);
`handoffs/CRAFT-config-stage6-CC-brief.md`
(`app_internal_base_url` + conformance fixture);
`handoffs/CRAFT-config-stage7-CC-brief.md` (release brief).
