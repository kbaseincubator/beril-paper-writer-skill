# beril-paper-writer — Contribution guide

**Status:** v0.8.0 + Stage 3 (2026-05-17). Living document.

## What can be contributed

The paper-writer skill has three contribution surfaces, in order of
expected volume and impact:

1. **Prompt improvements** — anti-pattern additions, self-review
   check refinements, worked-example updates, register-discipline
   tightening. Highest volume; directly improves manuscript quality.
2. **Validator and post-checker extensions** — new M-tier validators
   (`validate_manuscript.py`), new post-checkers (`check_*.py`), or
   extensions to existing ones. Medium volume; catches systematic
   failures mechanically.
3. **Orchestrator fixes** — `paper_writer.sh` bug fixes, phase-
   ordering changes, new pipeline phases. Lower volume; higher risk.

No other categories flow back in v0.7.x. Per-draft output
(`papers/draft_N/`), state files, and project-derived data are
strictly user-owned and never flow upstream.

## Contribution flow

### Prompt improvements

Prompts live at `src/beril_paper_writer/skill/prompts/*.v1.md`. Each
prompt has a versioned filename (e.g., `abstract.v1.md`). The `v1`
suffix is the prompt-generation version, NOT the skill version — it
changes only when the prompt's contract (inputs, outputs, escape
hatches) changes, not when anti-patterns or examples are refined.

**What qualifies as a prompt improvement:**

- A new anti-pattern observed in live manuscript output (e.g.,
  "Discussion-language leaking into Abstract Results subsection" —
  added in v0.7.1 as the "register bleed" anti-pattern).
- A new self-review check that catches a systematic failure
  (e.g., "#11 register purity" in abstract.v1.md).
- A tightened worked example that demonstrates a subtle formatting
  convention.
- A clarified escape hatch for edge cases.

**What does NOT qualify:**

- Stylistic preferences (the prompts enforce ICMJE conventions,
  not individual style).
- Journal-specific formatting (the skill produces generic IMRAD;
  journal adaptation is out of scope).
- Changes to the prompt's input/output contract (those require a
  `v2` version bump and orchestrator changes).

**How to submit:**

1. Identify the specific prompt and the specific anti-pattern or
   failure mode. Include the manuscript text that triggered it and
   why the current prompt didn't catch it.
2. Draft the anti-pattern rule, self-review check, or worked-example
   update. Follow the existing format in the target prompt.
3. Submit via PR against
   `ArkinLaboratory/beril-paper-writer-skill` or email to
   `aparkin@lbl.gov` with the prompt diff and a one-paragraph
   rationale.

**Review criteria:**

- Does the anti-pattern describe a real failure observed in live
  output (not a hypothetical)?
- Is the rule actionable by the LLM (specific enough to detect and
  fix, not vague)?
- Does it avoid conflicting with existing rules in the same prompt?
- Has the contributor run the test suite after making the change?

### Validator and post-checker extensions

Validators live at `src/beril_paper_writer/skill/tools/`:

- `validate_manuscript.py` — M1–M10 mechanized checks
  (IMRAD presence, structured abstract, AI disclosure, data
  availability, statistical reporting, citation cross-ref, etc.)
- `check_figures_manifest.py` — cross-walks figure manifest against
  section prose
- `check_tables_manifest.py` — cross-walks table manifest
- `check_scope_coherence.py` — traces Discussion claims to Results
- `check_throughline_glyphs.py` — advisory cross-walk of evidence
  glyphs

**What qualifies:**

- A new M-tier validator for a systematic manuscript deficiency not
  covered by M1–M10 (e.g., M11 for consistent terminology).
- An extension to an existing post-checker (e.g., new regex patterns
  for `check_scope_coherence.py`'s literature-signal detection).
- A false-positive fix for an existing checker (e.g., the
  `_LITERATURE_SIGNAL_RE` addition in v0.7.1 that stopped flagging
  external database phrases as unresolved).

**Requirements:**

- Every new validator or checker must include unit tests. The current
  test suite has 720 tests; new validators should add proportional
  coverage.
- Validators return `ValidatorResult` with `status` in
  `{pass, fail, soft-warning}`. New validators must document which
  status they use and why.
- Post-checkers exit 0 with advisory output. They do NOT block the
  pipeline; they surface warnings via the orchestrator.

### Orchestrator fixes

`paper_writer.sh` is the ~2700-line bash orchestrator. Changes here
have the highest blast radius. Submit with:

- The specific bug or behavior change.
- A regression test or verification command.
- Confirmation that the full test suite passes.

## What NOT to contribute

- **Per-draft output or project data.** Draft directories
  (`papers/draft_N/`), `state.json`, `REPORT.md` content, notebook
  outputs, and figures are user-owned. Never include project-specific
  data in a contribution.
- **Model-specific tuning.** Prompts are model-agnostic within the
  Claude family. Don't submit "this works better on Opus" changes
  that regress on Sonnet.
- **Journal-specific templates.** The skill produces generic IMRAD.
  Journal formatting is a downstream concern.

## Testing before submitting

```bash
cd beril-paper-writer-skill
pip install -e ".[dev]" --break-system-packages
pytest tests/ -v
```

Expected: 720+ tests pass. If your change adds new functionality,
add tests. If your change fixes a bug, add a regression test.

## Release cadence

Contributions are batched into point releases (v0.7.x). There is no
fixed schedule; releases happen when a contribution set is meaningful
and all tests pass. Each release documents what changed, what it
costs, and what users need to do (typically: `pipx install --force` +
`install-skill`).

## Cross-references

- [`PLUGIN_GUIDE.md`](PLUGIN_GUIDE.md) — comprehensive
  install→configure→test→operate guide
- [`LAYOUT.md`](LAYOUT.md) — internal architecture
- [`SPEC.md`](SPEC.md) — design rationale for the prompt suite
- [`DECISIONS.md`](DECISIONS.md) — running log of design decisions
