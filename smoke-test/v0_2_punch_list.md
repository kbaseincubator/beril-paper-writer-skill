# beril-paper-writer v0.2 — punch list

**Created:** 2026-04-27 (post v0.1.0 ship)
**Cadence:** one tier at a time; live retest + reassess between tiers.
**Operator:** Adam Arkin, single-user. Real-user feedback deferred until
end-to-end stable across stream.

This document is the authoritative scope for the v0.2 patch cycle. Use the
same tier structure that worked for v0.1.x (see
`v0_1_x_punch_list.md` for the proven pattern). Each item has explicit
acceptance criteria; grep `AC:` for them.

**Critical constraint (per Adam, 2026-04-27):** "we will be pushing hard"
but "want to make sure we are getting to evaluable outputs." Each tier
must produce a deliverable that can be smoke-tested against existing
artifacts (no new $5+ live runs required for tier completion) where
possible. Tiers that DO require live runs are explicitly flagged.

---

## Tier 1 — Cross-walk post-processors

**Lowest-regret tier. Start here.** Directly addresses C1 and C9 patterns
from v0.1 live runs (Abstract overclaim relative to Discussion;
Discussion claiming things Results doesn't show). Same architectural
pattern as `tools/check_throughline_glyphs.py`.

**Evaluation path:** smoke against the existing `draft_1` artifacts in
`spike/beril-extended/projects/functional_dark_matter/papers/draft_1/`.
NO new live LLM run required for tier completion. Cost: ~$0 to evaluate.

### Item 1.1 — Scope-coherence checker

**Problem.** Discussion claims things Results doesn't show (C9 pattern
from first live run; C1 from second is a related cross-section
incoherence). The throughline's "Would NOT include if chosen" list is
read-by-eye text, not a programmatic constraint. `reframer.v1` catches
some of this but is itself an LLM call subject to discipline drift.

**Fix.** New file `src/beril_paper_writer/skill/tools/check_scope_coherence.py`
(~250 lines, mirrors `check_throughline_glyphs.py`):

1. Walk `03_discussion.md` for sentence-level claims (heuristic: declarative
   sentences containing numerical findings or finding-pattern phrases like
   "we identified," "we found," "yields," "demonstrates").
2. For each claim, look up whether the same numerical anchor (e.g.,
   "1,256 organism-pathway pairs") OR a strong-claim verb phrase appears
   in `02_results.md`.
3. Emit stderr WARN for Discussion claims with no Results anchor.
4. Cross-walk against `00_throughline.md`'s "Would NOT include if chosen"
   list — flag Discussion claims that fall in the would-NOT-include set.
5. Always exit 0 (advisory); orchestrator surfaces in `next_actions.md`.

**Wiring.** Add to `paper_writer.sh` after `phase_finalize_citations`,
before `phase_assemble`. Invoke via `"$PYTHON_BIN" "$TOOLS_DIR/check_scope_coherence.py"
"$draft_dir" 2>> "$draft_dir/audit/scope_warnings.txt"`.

**Wire into `next_actions.md`.** Extend `paper_writer_helpers.py
emit-next-actions` to read `audit/scope_warnings.txt` and surface
counts + first-N warnings under a new section "## Scope-coherence
warnings."

**AC:**
- Run against existing `draft_1`: catches the C9 pattern (Discussion
  GapMind 1,256 not in Results) on the first live run's draft. (Note:
  the run that shipped v0.1 had reframer wired and the user revision
  expanded sub-claim coverage, so the C9 pattern may not recur in
  current `draft_1`. Run against an older draft if needed, or a
  synthetic fixture.)
- Output structure mirrors `check_throughline_glyphs.py`: stderr WARN
  lines + summary count + always exit 0.
- `next_actions.md` extended to include scope-coherence section.

### Item 1.2 — Overclaim checker

**Problem.** Abstract + Discussion contain causal/strong-claim verbs
("validates," "demonstrates," "yields," "establishes," "proves") that
exceed what the throughline's evidence map supports. C1-C7 from first
live run, C1 from second. The throughline's strength glyphs (`✓ direct
/ ⚠ partial / ✗ contradicts / ◇ orthogonal`) are the constraint; the
Abstract/Discussion ignore them.

**Fix.** New file `src/beril_paper_writer/skill/tools/check_overclaim.py`
(~250 lines):

1. Read `00_throughline.md`'s evidence map; extract sub-claims with
   their strength glyphs.
2. Walk `05_abstract.md` and `03_discussion.md` for sentences containing
   strong-claim verbs (curated list: validates, demonstrates,
   establishes, proves, yields, confirms, shows that, finds that).
3. For each strong-claim sentence, fuzzy-match its referent (typically
   the subject of the sentence; could be a noun phrase) against
   throughline sub-claims.
4. If matched, check the sub-claim's strength glyph: if `⚠ partial` or
   `✗ contradicts`, the Abstract/Discussion strong-claim verb is an
   overclaim; emit stderr WARN.
5. Always exit 0; orchestrator surfaces.

**Wiring.** Same hook point as 1.1.

**AC:**
- Run against existing `draft_1`: catches the C1 pattern (Abstract's
  "yields a defensible prioritized list" relative to ⚠ partial sub-claims).
- Output mirrors 1.1 + `check_throughline_glyphs.py`.
- Cross-walk discipline documented as architectural memory:
  `feedback_prompt_discipline_needs_post_check.md` already names the
  pattern; this is the third post-processor that confirms it.

### Tier 1 completion gate

- Both tools land + wired into `paper_writer.sh` + `next_actions.md`
- Smoke-tested against `draft_1` (and a synthetic fixture if needed for
  C9 pattern that doesn't recur in current draft)
- Memory entry updated noting the pattern is now battle-tested across
  three post-processors
- **Decision point:** evaluate whether Tier 2 (figures + docx) or Tier 3
  (REPAIR_MODE) is the right next step based on what real-use signal
  emerges between tiers

**Estimated effort:** 1 week focused. ~$0 in LLM cost (no new live runs).

---

## Tier 2 — Figures + docx assembly

**Biggest visible UX improvement.** v0.1's manuscript has no figure
embeddings; user must insert by hand. v0.2 closes this gap and produces
submission-ready docx.

**Evaluation path:** requires one full live LLM run on
`functional_dark_matter` (or a smaller test project) to validate the
`(Fig. N)` callouts emerge. ~$5 in LLM cost. Plus offline docx
generation testing.

### Item 2.1 — `results.v1` prompt edit: load-bearing `(Fig. N)` callouts

Per `results.v1.md` line 49-50, figure callouts are advisory ("when
applicable"). Live runs ignored them. Make them load-bearing:

- Add anti-example pair (FAIL: prose with no figure callouts; PASS:
  callouts after sentences each figure supports).
- Add HALT instruction in self-review: "if `FIGURES_INVENTORY_PATH` is
  provided AND no `(Fig. N)` callouts appear in your prose, HALT and
  re-walk. The orchestrator's figure-copy step depends on these."
- Document the contract: "results.v1 produces `(Fig. N)` callouts;
  orchestrator's embedding step injects `![caption](figures/<name>)`
  markdown image tags based on the callouts and `figures_inventory.md`."

**AC:** smoke against synthetic fixture confirms `(Fig. N)` appears
in results.v1 output for figures referenced.

### Item 2.2 — Orchestrator figure-embedding step

New phase `phase_embed_figures` in `paper_writer.sh`, between
`phase_results` and `phase_assemble`:

1. Read `figures_inventory.md` to get figure names + caption candidates.
2. Walk section files for `(Fig. N)` references.
3. For each `(Fig. N)`, look up the figure name in `figures_inventory.md`
   (results.v1's closing message names the order).
4. After the sentence containing `(Fig. N)`, inject markdown image:
   `\n\n![<caption>](figures/<name>)\n\n`
5. Captions: pick the highest-confidence candidate (REPORT-derived first,
   notebook-context second, filename third).

Orchestrator-side; non-destructive on section files (writes to
section.md but with idempotent injection).

**AC:** smoke confirms manuscript.md has `![...]` image tags inline
with prose.

### Item 2.3 — `tools/assemble_docx.py`

New file (~400 lines): markdown → docx via python-docx.

- Headings (H1, H2, H3) → docx heading styles 1, 2, 3
- Paragraphs → docx paragraphs with proper styling
- Bold/italic/code → docx character styles
- Markdown image tags → embedded docx Picture objects (sized to fit)
- Tables → docx tables
- Citations: `[N]` form preserved (numeric, post-finalize)

**AC:** Run against the v0.2 manuscript.md, produces a valid manuscript.docx
that opens in Word/Pages/LibreOffice with figures embedded inline.

### Item 2.4 — `commands/assemble.py` implementation

Currently a stub that exits with not-implemented. Wire to call
`assemble_docx.py`. CLI: `beril-paper-writer assemble <draft_dir>
[--format docx|pdf|md]`. PDF deferred (post-MVP). MD is identity (just
copies manuscript.md).

**AC:** `beril-paper-writer assemble draft_1 --format docx` produces
manuscript.docx without errors.

### Tier 2 completion gate

- Live LLM run produces results.v1 prose with `(Fig. N)` callouts
- Manuscript.md has inline image tags
- assemble produces valid docx with figures embedded
- Decision point on Tier 3

**Estimated effort:** 1.5 weeks. ~$5 in LLM cost.

---

## Tier 3 — REPAIR_MODE + rewrite loop

**Biggest architectural piece.** Converts validator failures + reviewer
critical issues from "user fixes by hand" to "writer addresses + re-validates."

**Evaluation path:** requires multiple live LLM runs to validate retry
semantics, escalation paths, and discipline-drift containment. ~$30
in LLM cost.

### Item 3.1 — REPAIR_MODE harness in `paper_writer.sh`

After `phase_assemble` (which runs validate_manuscript.py), add
`phase_repair_validators`:

1. Read `audit/validation.json`.
2. For each `fail` validator, look up the section prompt that owns
   repairs (per LAYOUT validator-dispatch table at line 419).
3. Invoke that section prompt in REPAIR_MODE with the four
   REPAIR_MODE-specific inputs (NAMED_VALIDATOR, VALIDATOR_OUTPUT_PATH,
   REPAIR_TARGET_PATH, REPAIR_MODE=true).
4. Bounded retry: 2 attempts per validator failure; if exhausted,
   surface escalation per the closing message's recommended path.

Coupled with prompt-side REPAIR_MODE handling (see 3.2).

**AC:** synthetic fixture with M9 (Limitations <150 chars) failure +
discussion.v1 in REPAIR_MODE produces an expanded Limitations subsection.

### Item 3.2 — Per-prompt REPAIR_MODE handling

Each section prompt accepts the REPAIR_MODE inputs and emits the
documented closing message. Currently the prompts SPEC says they
should; `methods.v1` has the structure; verify all 6 (methods, results,
discussion, intro, abstract; reframer doesn't repair).

**AC:** synthetic REPAIR_MODE invocation per prompt produces the
expected fix without regenerating the whole section.

### Item 3.3 — Review-rewrite loop with bounded retry

After `phase_review`, if reviewer flags ≥1 critical issue:

1. Parse the review for critical issues + section pointers.
2. For each critical, dispatch the relevant section prompt in
   REPAIR_MODE with the violation as the named target.
3. Bounded retry: max 2 rewrite passes per draft (per SPEC §8.3).
4. After rewrite passes, re-run reviewer; if critical issues remain,
   surface in next_actions.md and pause.

**AC:** live run produces a draft where the reviewer's first critical
is addressed by a rewrite pass; second-pass review is cleaner.

### Item 3.4 — REPAIR_MODE post-checkers

Per the architectural lesson: cross-walk discipline can't rely on
prompt-level enforcement. Each REPAIR_MODE invocation should be
post-checked:
- Did Write get invoked? (stream_progress.py already handles)
- Was only the named span modified? (new check_repair_scope.py?)
- Did the fix re-introduce any pre-existing validator failure?

**AC:** REPAIR_MODE post-check catches over-eager fixes that
regenerate too much.

### Tier 3 completion gate

- Live run produces a draft where validator failures auto-fix or
  escalate cleanly per the dispatch table
- Reviewer critical issues drop after rewrite pass (vs v0.1 where they
  persist)
- No discipline-drift introduces new failures during repair

**Estimated effort:** 2-3 weeks. ~$30 in LLM cost.

---

## Tier 4 — Interactive checkpoints

**Important per Adam's note: "card elicitation is a critical stage later."**

### Item 4.1 — Card elicitation pre-drafting checkpoint

Per `spec-additions/database_cards.md`. Orchestrator-driven dialog
between `phase_plan` (after pick) and `phase_citation_pool`. ~200
lines. Already designed; needs implementation.

### Item 4.2 — Citation-pool exhaustion user pause (B1 path)

When discussion.v1 surfaces `[NEEDS CITATION]` placeholders, pause
with handoff offering scope-down / citation-request /
accept-as-limitation. v0.1 pumps through with scope-down default.

### Item 4.3 — Throughline re-evaluation on artifact drift

`state.py:diff_artifacts` is wired. Add LLM-driven re-evaluation:
on resume, if source artifacts changed, prompt user with "should the
throughline be re-picked?" via a new mini-prompt.

**Estimated effort:** 1 week. ~$10 in LLM cost.

---

## Tier 5 — Defensive features

### Item 5.1 — Proper `07_data_availability.md` extraction

Replace `[TBD]` markers with real BERDL DB names (from
`methods_provenance.md` Spark queries) and accessions (from
`RESEARCH_PLAN.md`'s "Data sources" / "Datasets" sections). ~80 lines
in paper_writer_helpers.py.

### Item 5.2 — `--max-cost-usd` circuit breaker

Per-call cost is logged. Add enforcement: if estimated cost would
exceed cap, halt with handoff offering to continue or abort. ~40 lines.

### Item 5.3 — State schema migration tool

`beril-paper-writer migrate-state <draft_dir>`: bumps state.json from
0.1 → 0.2 schema. Required when STATE_SCHEMA_VERSION bumps. ~100 lines.

**Estimated effort:** 3-5 days.

---

## Tier 6 — Live retest + v0.2 ship

### Item 6.1 — End-to-end live retest

Run on `functional_dark_matter` AND one other STRONG-tier project (if
available) to test cross-project robustness. ~$10-15 in LLM cost.

### Item 6.2 — `RELEASE_NOTES.md` for v0.2

New file `RELEASE_NOTES_v0.2.md` (or extend the existing one with a
v0.2 section). Document what shipped, what's deferred to v0.3, real
numbers from retest.

### Item 6.3 — pyproject + __init__.py bump

`0.1.0` → `0.2.0`.

### Item 6.4 — Commit + push to `ArkinLaboratory/beril-paper-writer-skill`

Same workflow as v0.1.0 ship. Use the punch-list pattern from this
file as audit trail in the commit message.

---

## Sequencing summary

```
Tier 1 (post-processors, ~1 wk, $0 smoke)  →  decision point
                                                     ↓
Tier 2 (figures + docx, ~1.5 wk, $5)        OR  Tier 3 (REPAIR_MODE, ~2-3 wk, $30)
                                                     ↓
                                              decision point
                                                     ↓
                                          remaining tiers in user-priority order
                                                     ↓
                                              Tier 6 (ship)
```

Total estimated effort to v0.2.0: 6-8 weeks if all tiers run consecutively.
Real cadence likely longer with reassessment between tiers.

---

## Post-v0.2 future tiers (deferred — design notes only)

These tiers are not part of the v0.2 cycle. Captured here so future
conversations have a concrete starting point and don't re-derive scope.

### Tier 7 — Conceptual diagrams via mermaid-in-markdown (post-Tier-2)

**Hard dependency on Tier 2.** Tier 7 generates conceptual diagrams
(method overview, decision-route schematics, analytical-pipeline flow)
as `mermaid` fenced blocks in the IMRAD section files; the docx
assembler embeds rendered PNGs. The assembler is built in Tier 2.3.
Therefore Tier 7 cannot ship before Tier 2.

Adam confirmed 2026-04-27: option (a) — mermaid in markdown — is the
preferred direction over matplotlib-driven schematics. We do NOT use
pandoc (per Tier 2's `tools/assemble_docx.py` design — `python-docx`
direct), so the orchestrator owns the mermaid → PNG render pipeline.

**Out of scope (declined 2026-04-27):** auto-generated illustrative
images (cell cartoons, organism schematics, etc.). Computational
microbiology papers rarely use these and image-gen API → scientific-
manuscript pipelines have known correctness problems (hallucinated
structures, made-up labels). If a paper needs illustrations, the user
brings them via BioRender / Inkscape.

#### Item 7.1 — Mermaid renderer choice + caching

Three viable renderers, with tradeoffs:

| Renderer | Install cost | Offline | Notes |
|---|---|---|---|
| Kroki (https://kroki.io/) | None — REST API | No | Easiest prototype; rate-limited but free; data leaves the network. |
| mermaid-cli (npm) | Node + Chromium | Yes | Heavy install; reliable; recommended for production / private deployments. |
| Python wrapper (mermaid-py) | npm under the hood | Yes | Same constraint as mermaid-cli; thin wrapper. |

**v0.3 recommendation:** start with Kroki for prototype; add
mermaid-cli as a fallback for `--offline` deployments. Cache rendered
PNGs by sha256(mermaid-syntax) in `<draft_dir>/.diagrams_cache/` so
re-assembling doesn't re-render.

**AC:** `tools/render_mermaid.py <draft_dir>` walks all section files
for ` ```mermaid ` fenced blocks, renders each to a PNG in the cache
directory, replaces the fenced block in the section file with an
`![caption](path)` image tag (or leaves the fenced block in place AND
emits the image tag — TBD on assembler design). `assemble_docx.py`
embeds the PNGs as Picture objects.

#### Item 7.2 — `diagrams.v1.md` prompt — identifies + generates diagrams

New dedicated prompt (~200 lines). Recommended over embedding diagram
generation in each section prompt: section prompts already have heavy
discipline; adding "decide if a diagram is appropriate here, generate
mermaid, embed it" as a side responsibility risks scope creep.

Inputs: `THROUGHLINE_PATH`, `REPORT_PATH`, `RESEARCH_PLAN_PATH`,
`METHODS_PATH` / `RESULTS_PATH` / `DISCUSSION_PATH` (so the prompt
can place each diagram in the right section), the section file the
diagram should be inserted into, target `DIAGRAMS_OUT_PATH` (a JSON
manifest mapping diagram-id → mermaid-syntax + suggested-section +
caption).

Output: a JSON manifest. The orchestrator's diagram-embedding step
walks the manifest, inserts each ` ```mermaid ` fenced block at the
right point in the named section.

Output discipline: the prompt MUST identify diagrams from the
project's actual structure (route schematics, pipeline flows,
hypothesis trees). It MUST NOT auto-generate filler diagrams just
because slots exist. Anti-pattern: a "convergent evidence" diagram
that's just a Venn schematic with no data backing it. The prompt
self-reviews against the throughline's evidence map; if a proposed
diagram doesn't surface a sub-claim, drop it.

**AC:** running diagrams.v1 against `functional_dark_matter` produces
a manifest with 1-2 diagrams (likely: dual-route Route A vs Route B
schematic for the Discussion; multi-omics integration flow for the
Methods). Each diagram's mermaid syntax parses cleanly via
`render_mermaid.py`.

#### Item 7.3 — `tools/check_diagrams.py` — fifth post-processor

Continues the post-processor architectural pattern. Three checks:

1. **Mermaid-syntax validation** — parse each fenced block; reject if
   syntax errors (use `mermaid-cli --validate` or a lightweight
   Python parser). WARN per invalid block.
2. **Label-coverage** — extract node labels from the mermaid syntax;
   verify each label corresponds to a concept that appears in the
   section's prose (cross-walk). NOTE if any node label is text the
   prose doesn't reference.
3. **Diagram-section consistency** — diagrams placed in section X
   should reference content in section X, not other sections. WARN
   on cross-section misplacement.

Stderr WARN/NOTE; exit 0; orchestrator surfaces in next_actions.md.
Same skeleton as the four existing post-processors.

#### Item 7.4 — Tier 2 ↔ Tier 7 integration

`tools/assemble_docx.py` (built in Tier 2.3) needs an extension to:
- Detect ` ```mermaid ` fenced blocks in section files
- Replace each with a Picture object pointing at the cached PNG
- Preserve the mermaid syntax in the markdown source (don't destroy
  it during docx render — the markdown is the truth, the docx is a
  derivative)

This is a small extension (~30 lines) but cannot be done before
Tier 2.3 is implemented.

#### Tier 7 completion gate

- 1-2 diagrams render in the docx for `functional_dark_matter`
- Diagrams pass `check_diagrams.py` cleanly (or surface advisory
  WARNs that survive review)
- Mermaid → PNG cache works idempotently across re-assembles

**Estimated effort:** 2-3 weeks. ~$10-15 in LLM cost.

---

## Sequencing past v0.2

```
v0.2 ship  →  Tier 2 (figures + docx)  →  Tier 7 (diagrams; depends on Tier 2.3)
                    ↓
              Tier 4 (interactive review) — only when there's a 2nd user
```

Tier 4 (interactive checkpoints) is intentionally deferred until the
augmentation stream as a whole has a second user. Per Adam's
2026-04-27 cadence note: "no broader user feedback until end-to-end
stable across stream." Card elicitation, citation-pool exhaustion
pause, throughline re-evaluation are real but their marginal value
is low while there's exactly one human at the wheel.

---

*Punch list authored 2026-04-27 immediately after v0.1.0 ship.
Tier 1 is the recommended starting point. Future conversations can
pivot off this doc for fast start; do not re-derive scope.*

*Post-v0.2 tiers added 2026-04-27 (later) — Adam confirmed Tier 7
(diagrams via mermaid) and declined images permanently for this
skill.*
