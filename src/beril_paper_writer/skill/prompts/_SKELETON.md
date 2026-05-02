# Prompt suite skeleton — beril-paper-writer

**Purpose.** This is the section structure that every prompt in
`prompts/*.v1.md` should follow. It exists so the 10 prompts share a
predictable shape, so memoryless agents reading them encounter
consistent patterns, and so cross-cutting rules (escape hatches,
retry semantics, anti-example pairs) live in exactly one place per
prompt instead of scattered across sections.

This is a development artifact, not a runtime resource. The
orchestrator never reads `_SKELETON.md`; only the human/agent
authoring or maintaining the prompts does.

The reference implementation is [`citation_pool.v1.md`](citation_pool.v1.md)
— the first prompt drafted, reviewed, edited, and length-cut against
this skeleton. When the skeleton and the reference disagree, fix the
reference.

---

## Section ordering (top-to-bottom)

Every prompt has these sections, in this order:

1. `# {prompt name}` (H1, one line)
2. **Role and stakes** (1 paragraph; what the agent does, why it
   matters, the primary failure mode)
3. **What you produce** (1–3 paragraphs; output artifact + Write-tool
   discipline)
4. **Schema / output format for [the section]** (load-bearing; with
   worked example) — for prompts that produce structured output OR
   produce structured *fragments* of a larger artifact (e.g.,
   reframer.v1's per-entry log format, plan.v1's per-candidate
   template).
5. **Inputs the user prompt will pass** (bullet list of named
   parameters with absolute-path discipline)
6. **What to read before [doing the work]** (1 paragraph or short
   list; pointers, not duplicates of Inputs)
7. **Escape hatches when expected files are absent** (subsection of #6
   or its own section; mandatory)
8. **What the [output] needs to cover** (task-specific; for prompts
   that produce sectioned content)
9. **Tier-aware framing** (STRONG / THIN / EXPLORATORY; mandatory for
   any prompt where evidence-strength affects output)
10. **[Task-specific] discipline pass** (verification, grounding,
    drift-check, etc. — the load-bearing protocol the prompt enforces).
    For complex tasks with multiple sub-protocols, label them
    numerically (`### 1. Sub-protocol name` / `### 2. ...`); operationalize
    each independently.
11. **Caps and exhaustion / coverage limits** (only for
    budget-managed prompts; skip otherwise)
12. **Depth modes** (`quick` / `standard` / `deep`; only for prompts
    where the orchestrator passes `DEPTH`)
13. **Tool use** (one-line summary per tool; cross-reference protocols
    above rather than restate)
14. **Anti-patterns** (named failure modes specific to this task)
15. **Self-review pass** (numbered checklist + anti-example pairs in
    two groups — validator-blocking errors / silent traps)
16. **Output protocol** (numbered steps with cost checkpoint and
    bounded retry, **including a "Closing-message template
    (required exact format)" subsection at the end of the
    protocol** — see notes below)
17. **Inviolable rules** (3–5 override-everything rules; not a
    body-restatement)

Some sections are optional per the notes below. Section ordering is
not optional — agents read top-to-bottom, so the load-bearing
constraints (schema, inputs, discipline pass) come before the
process steps (output protocol, retry).

**Closing-message template placement** (Review B / 2026-04-25
clarification): all 10 prompts in the suite nest the closing-message
template as a subsection of Output protocol §16, not as a separate
sibling section §17. The earlier draft of this skeleton listed it
as §17 — that was incorrect; the convention is one section §16
(Output protocol) ending with `**Closing-message template (required
exact format):**` followed by the code block. Inviolable rules
becomes §17. Treat the template as the *final step* of the Output
protocol, not a sibling.

---

## Per-section slot signatures

### Role and stakes

One paragraph. Names the agent's role in plain language; names the
primary failure mode the prompt is designed to prevent; cites the
SPEC section that this prompt implements (e.g. `[SPEC §6.4][spec-pool]`).
Avoid generic motivational language; say what specifically goes wrong
without this discipline.

### What you produce

States the output artifact path (the user prompt passes the absolute
path), the file format, what downstream consumers do with it. Ends
with the discipline that the agent must call `Write` rather than emit
the artifact as a chat response. Final-response shape (one-line
confirmation; see Closing-message template below) is named here so
the agent doesn't drift.

### Schema / output format

Mandatory for prompts whose output has any **fixed structure** the
downstream pipeline depends on. This includes:

- Prompts producing structured data files (`citation_pool.v1`'s
  `pool.json` — 9-field per-entry schema).
- Prompts producing markdown artifacts with required subsection
  structure (`methods.v1` / `results.v1` / `discussion.v1` /
  `intro.v1`'s IMRAD subsections; `abstract.v1`'s M2 four-subsection
  structured form).
- Prompts producing structured *fragments* of a larger artifact
  (`reframer.v1`'s per-entry log-format template;
  `plan.v1`'s per-candidate throughline template).
- Reviewer prompts producing review files with frontmatter +
  required sections (`fallback_reviewer.v1`'s structured review
  format).

Skip ONLY for prompts whose output is genuinely free-form, with no
downstream parser or required structure. `rewrite.v1` is the
canonical example — it produces revised section markdown whose
shape is dictated by the section being rewritten, not by rewrite.v1
itself.

A schema section has three components:

1. **A short framing paragraph** stating what the schema is, what
   discipline it mirrors (e.g. "the adversarial reviewer's 9-field
   strict citation discipline"), and what tool enforces it (e.g.
   `citation_pool.py validate`). For audit-style prompts (reframer),
   the "tool that enforces it" is the consumer prompt or a
   downstream parser, not necessarily a Python validator.
2. **A field-by-field table** with type, required-or-not,
   constraints. Note explicitly which constraints are
   validator-enforced vs. trusted-by-convention. For markdown-
   subsection schemas, the "fields" are the subsection names with
   their required content discipline.
3. **A worked example** (5–25 lines of JSON / markdown) that an
   agent can pattern-match against. The example must validate
   against the actual tool — test it before shipping.

Schema-gotcha bullets (things a table cannot show: subtle ordering
rules, identifier-trust gradients, escape sequences) go AFTER the
table, before the worked example. Keep to 4–6 short bullets; if you
have more, you have too many gotchas and the schema is over-designed.

### Inputs the user prompt will pass

Bullet list. Each input is `NAMED_LIKE_THIS — type / optionality —
one-line description`. The list is the contract between the
orchestrator (`paper_writer.sh`) and this prompt; if the orchestrator
changes what it passes, this list changes.

Mandatory inputs to call out across all prompts:

- Absolute-path discipline. Always.
- Mode + tier (`MODE`, `TIER`) when the prompt is mode/tier-aware.
- `DEPTH` when the prompt has depth modes.
- `VALIDATOR_CMD` (or equivalent) when the prompt's output needs to
  be validated by a Python tool — orchestrator passes the exact Bash
  invocation; the prompt does not guess paths.

Optional-but-load-bearing inputs (e.g. `EXISTING_POOL_PATH`,
`EXISTING_REFERENCES_MD`) are explicitly marked optional, with the
behavior when present vs. absent stated.

### What to read before doing the work

One paragraph or short ordered list. NOT a re-enumeration of Inputs.
The point is *read order* (anchor first, then primary source, then
secondary), not *what parameters exist*.

### Escape hatches when expected files are absent

Mandatory. Bullet list. Each entry: `{file/condition} → {behavior}`.
Behavior is one of: **halt-with-error** (specific error message),
**proceed-with-degraded-context** (specific note in summary),
**halt-with-parse-error-verbatim** (don't paper over).

Why mandatory: every prompt assumes input files exist. The
orchestrator can't guarantee they do. A memoryless agent without
escape hatches will improvise — guess from REPORT.md, fabricate a
throughline, etc. Make the escape hatch explicit so improvisation
isn't tempting.

### What the output needs to cover (and tier-aware sizing)

For prompts that produce sectioned content (methods / results /
discussion / etc.), enumerate the categories the output must cover.
Then a tier table:

```
| Tier | Categories | Target size |
|---|---|---|
| STRONG | all | <range> |
| THIN | <subset> | <smaller range> |
| EXPLORATORY | <smaller subset> | <smallest range> |
```

State explicitly: **tier shifts coverage breadth, never the
verification floor / grounding floor / discipline floor.** This is
the discovery from the citation_pool review: a memoryless agent can
read "tier-aware" as "looser rules in lower tiers" if you don't
prevent it.

### Discipline pass

The task-specific protocol the prompt enforces: verification (for
citation_pool), grounding (for methods), drift-check (for reframer),
overclaim-detection (for fallback_reviewer), etc. This is the
load-bearing content — usually the longest section in the prompt.

Structure: `**Setup** → **For each [unit of work]** → numbered steps
with operational definitions of fuzzy terms ("match", "high-stakes",
"in-scope") → drop-or-flag rule for failures.`

Operationalize fuzzy terms. "Read enough of the work" is meaningless;
"abstract minimum, body required for high-stakes citations defined
as X / Y / Z" is operational. The review pass on citation_pool
caught several cases where my first draft left memoryless agents
guessing.

### Caps and exhaustion / coverage limits

Only for prompts that have a budget or coverage cap (citation_pool
has the 80-entry cap; fallback_reviewer has a comment-count cap;
others may not). Skip the section entirely otherwise — don't include
it as a stub.

When present: state the cap, distinguish cap-as-ceiling from
target-size, describe the at-cap behavior (specific summary
language), reference the SPEC section that owns the cap.

### Depth modes

Only when `DEPTH` is a passed input. State that depth modulates
*coverage*, never the verification/grounding floor. Three bullets,
one per mode (`quick` / `standard` / `deep`), each ≤4 lines.

### Tool use

One bullet per tool. The bullet says what the tool is for in this
prompt; it does NOT re-explain protocols already covered above.
Keep to ~10 lines total. If you find yourself re-explaining
WebSearch usage, that content belongs in the Discipline pass.

### Anti-patterns

Named failure modes specific to this task. 4–6 entries. Each is a
1-paragraph callout: name in bold + one-line failure description +
prevention. Drop any anti-pattern that fully duplicates a rule in
the Discipline pass — repetition past one place is noise.

### Self-review pass

Numbered checklist (5–9 items). Each item is one line. Run before
the Output protocol's Write step.

Anti-example pairs go below the checklist, in two groups:

```
**Validator-blocking errors (will fail validation):**
✗  bad form          (why it fails)
✓  good form

**Silent traps (validator passes, but the entry is wrong):**
⚠  bad form          (validator allows; downstream breaks)
✓  good form
```

The two-group separation is mandatory — they teach different
lessons. Mixing them causes the agent to assume the validator
catches everything, which is exactly the failure mode.

### Output protocol

Numbered steps. Mandatory inclusions:

1. Build the artifact.
2. **Cost checkpoint (continuous).** Track WebSearch calls (or
   equivalent expensive tool). Halt-on-thresholds per mode. Specific
   numbers per `DEPTH`.
3. Self-review pass.
4. Write the artifact via `Write` tool to the absolute path. On
   `Write` failure, halt and emit error verbatim.
5. Validate (if there's a validator). Pass warnings inline.
6. **Bounded retry on validation failure.** Up to 2 repairs (3 total
   runs). Each repair fixes ONLY the named field; do not regenerate
   from scratch. Halt after 3 with the validator output verbatim.

### Closing-message template

Inside the Output protocol section. **Required exact format**, not
"something like." A code block with placeholders and a note that
fields must be derivable from the artifact (no hand-waving).

```
{artifact} written, N {units} (cap M, mode {quick|standard|deep},
{any other mode flags}); {coverage flags}; {cost stats}.
```

### Inviolable rules

3–5 rules total. Each rule overrides everything else in the prompt
in genuine corner cases. NOT a body-restatement. If a rule appears
in the body and you also list it here, ask whether the body
statement is sufficient — usually it is.

Examples (from citation_pool):

- "No fabricated citations. Plausibility is not evidence."
- "No entry without verification (for entries newly added in this
  run)."
- "Pool cap is the ceiling, not the target."
- "Apply the throughline filter before self-review."

Counter-examples (do NOT include): "Output is the JSON file, not a
chat response" — already in `What you produce`. "Errors block
drafting" — already in the Schema section.

---

## Cross-cutting consistency rules (apply to every prompt)

These are the disciplines I learned during the citation_pool review
+ length-cut pass. They are not negotiable; if a prompt violates
them, fix the prompt.

1. **No rule appears in two sections of one prompt.** Repetition
   past one place is noise, not reinforcement. The exception is
   anti-example pairs, which by design illustrate rules stated
   elsewhere.
2. **Closing-message template is fixed exact format**, not "for
   example." A memoryless agent given an example will produce
   variations; given a template, it will produce the template.
3. **Operationalize every fuzzy term** the prompt uses to gate
   behavior. "High-stakes," "match," "in-scope," "load-bearing" all
   need definitions in the prompt itself, not only in SPEC.
4. **Escape hatches are mandatory** for every input the orchestrator
   provides. The agent must know what to do when the file is missing
   / malformed / empty. Improvisation is the failure mode.
5. **Tier shifts coverage, never the discipline floor.** State this
   explicitly in any prompt that is tier-aware.
6. **Cost checkpoint lives in the Output protocol**, not as a
   vibe-rule in Important Rules. The protocol is the agent's
   step-by-step; rules at the end don't get integrated into the
   loop.
7. **Bounded retry on any external validation.** No unbounded loops.
   3 attempts max.
8. **Inviolable rules section is short and override-only.** 3–5
   items. Body restatement is a smell.
9. **No LLM-generated attribution lines.** Section agents must NOT
   emit `Co-Authored-By:`, `Generated by:`, or similar attribution
   lines in their markdown output. Authorship is orchestrator-owned
   (title block); the assembly phase strips stray attributions, but
   prompts should not produce them.
10. **Length target.** Aim ≤500 lines. Citation_pool landed at 483.
   The adversarial reviewer's 442 is a useful comparison point —
   adversarial covers more focus areas than most paper-writer
   sections, so paper-writer prompts shouldn't be heavier than
   adversarial without a clear reason.

---

## Optional-section decision matrix

Which sections each prompt should include:

| Prompt | Schema | What output covers | Tier | Caps | Depth | Discipline kind |
|---|---|---|---|---|---|---|
| `citation_pool.v1` | ✓ JSON | ✓ 5 categories | ✓ | ✓ 80 | ✓ | Verification |
| `methods.v1` | ✓ md | ✓ subsections | ✓ | — | — | Grounding |
| `results.v1` | ✓ md | ✓ subsections | ✓ | — | — | Grounding + numerical-claim cross-check |
| `discussion.v1` | ✓ md | ✓ topics | ✓ | — | — | Citation-pool constraint + scope discipline |
| `intro.v1` | ✓ md | ✓ topics | ✓ | — | — | Setup-only-what-paper-delivers |
| `abstract.v1` | ✓ md (structured) | — | ✓ | length | — | Body-derivable claims only |
| `plan.v1` | ✓ throughline-candidates.md | ✓ 2–3 candidates | ✓ | — | — | Evidence-mapping |
| `reframer.v1` | ✓ reframing_log.md entries | — | — | — | — | Drift-detection |
| `fallback_reviewer.v1` | ✓ review.md | ✓ focus areas | ✓ | comment count | ✓ | Overclaim + citation rigor |
| `rewrite.v1` | ✓ updated section.md | — | ✓ | — | — | Apply-fixes-only-named |

`—` means skip the section entirely. `✓` means include with
task-specific content. Italics in the cell mean see notes below.

### Notes on specific prompts

- **`methods.v1`** is the most heavily grounded prompt. Its
  Discipline section is large (notebook AST + RESEARCH_PLAN
  cross-check; flag implicit-but-not-explicit steps as
  `[METHOD UNCLEAR: see notebook X cell Y]`). It probably runs ~520
  lines.
- **`abstract.v1`** is the shortest. Structured-abstract output
  (Background / Objective / Methods / Results / Conclusions per
  ICMJE IV.A.3.b); body-derivable-claims-only constraint; M2
  validator must pass. Probably ~350 lines.
- **`plan.v1`** is structurally distinct: produces 2–3 candidate
  throughlines with evidence maps, then pauses for user pick. No
  Caps section but has a Candidates-cap (2–3) and a tier-aware
  narrowed-claim candidate for THIN. Schema is the
  `throughline_candidates.md` template from SPEC §4.2.
- **`reframer.v1`** has no Schema-output-covers — it produces
  append-only log entries to `reframing_log.md`. Per SPEC §5.6, the
  log entry format is fixed; the prompt enforces it.
- **`fallback_reviewer.v1`** is small (target ~150 lines per SPEC
  §8.2). It's a *reviewer* prompt, not a writer — borrows structure
  from `adversarial_paper.v1.md` but trims to overclaim-detection +
  citation-rigor + scope-alignment. Per D-005, the user is warned
  via stderr that the fallback is in use; the prompt itself doesn't
  warn.
- **`rewrite.v1`** is the second-shortest. Targeted application of
  review-driven fixes to a single section. Constraint: do not
  re-write the whole section — change only the spans flagged by the
  review. Reuses the `apply-fixes-only-named` discipline from the
  citation_pool's bounded-retry pattern.

---

## What to do when this skeleton and a prompt disagree

Three cases.

1. **The skeleton is right, the prompt is wrong.** Fix the prompt
   against this skeleton. Common case during prompts 2–10 drafting.
2. **The prompt is right, the skeleton is wrong.** Update this
   skeleton. Less common, but happens — e.g., I might find that
   `methods.v1`'s grounding pass needs a section pattern this
   skeleton didn't anticipate.
3. **Both are partially right.** Update both, then audit the other
   prompts in the suite for consistency. Discovered patterns belong
   in the cross-cutting consistency rules above.

The skeleton is alive while the suite is in development; we freeze
it once all 10 prompts are drafted and reviewed.

[spec-pool]: ../../SPEC.md  "see §6.4 + §6.4.1"
