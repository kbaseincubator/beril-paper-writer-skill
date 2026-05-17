# beril-paper-writer-skill — v0.3 release notes

**v0.3.0 release date:** 2026-04-27
**Status:** v0.3 — figures + docx assembly tier. Pre-1.0; expect
breaking changes between minor versions until the architectural shape
stabilizes.

This document is the authoritative release-handoff for v0.3. It lists
what's new since v0.2.1, what's deferred to v0.4, and the specific
user-visible behavior changes. The v0.1.0 ship notes
(`RELEASE_NOTES.md`) remain authoritative for the foundational
features; `RELEASE_NOTES_v0_2.md` covers the v0.2 discipline-hardening
+ auto-repair tier.

---

## What v0.3 adds since v0.2.1

The v0.3 thesis: **close the figure-embedding loop end-to-end.** v0.2
shipped a `manuscript.md` with no figure embeddings; users had to
insert figures by hand. v0.3 produces a `manuscript.docx` with inline
Pictures + Caption-styled paragraphs, end-to-end from a fresh
drafting run, with no manual intervention beyond the throughline pick.

Four architectural pieces landed (Tier 2.1 through Tier 2.4 from
`smoke-test/v0_3_punch_list.md`):

### Tier 2.4 — `commands/assemble.py` wired to `tools/assemble_docx.py`

The user-facing CLI command `beril-paper-writer assemble <draft_dir>
[--format docx|md|pdf]` is no longer a stub. Validates draft_dir +
`manuscript.md` presence; dispatches by `--format`:

- `md` is identity (manuscript.md is already the artifact; prints path).
- `docx` subprocesses `tools/assemble_docx.py` with `sys.executable`
  (so the pipx venv's Python carries through).
- `pdf` rejected with explanatory stderr; post-MVP — convert via
  Word/LibreOffice if needed.

`tools/assemble_docx.py` is located via `importlib.resources` — same
pattern as `commands/draft.py:_locate_paper_writer_sh`.

### Tier 2.3 — `tools/assemble_docx.py` — markdown→docx renderer (~470 lines)

Full hand-rolled renderer. **No new dependencies** beyond the
existing `python-docx>=1.1.0` (declared since v0.2). Markdown parsing
is line-based block + regex inline; pure stdlib aside from
`python-docx`.

**Block elements supported:** H1–H6 headings, paragraphs (blank-line
separated), bullet/numbered lists, blockquotes, markdown tables (with
`|---|` separator detection), fenced code blocks, block-level images
on their own line, horizontal rules.

**Inline elements supported:** code spans (`` `text` ``), bold
(`**text**`), italic (`*text*` / `_text_`), inline links (rendered as
text + parenthetical URL — clickable hyperlinks deferred to v0.4),
bare `[N]` citation form preserved verbatim (does NOT match link
regex; verified by unit test).

**Image rendering** (the load-bearing path per Wrinkle B
canonicalization in v0.3 punch list): block-form `![Figure N: caption
text](figures/<filename>)` → centered Picture (Inches(6) wide) +
Caption-styled paragraph with the alt-text. Path validation rejects
absolute and parent-relative paths; missing files emit a soft
`[FIGURE MISSING: …]` italic placeholder rather than crashing.

**Style fallbacks:** `Caption`, `List Bullet`, `List Number`, `Quote`,
`Table Grid` styles attempted; if the docx default template lacks
them, falls back to italic Normal / unstyled paragraph / no border.

**Test coverage:** `tests/unit/test_assemble_docx.py` — 26 tests, all
pass. Synthetic 1×1 PNG fixture (hand-crafted via stdlib `struct` +
`zlib`, no Pillow dep) exercises the Picture-embedding path.

### Tier 2.1 — `results.v1` prompt edit + figures_manifest.tsv contract

Three landings. The unifying thesis: results.v1 emits a structured
manifest declaring which figures it selected; downstream phases
consume the manifest deterministically.

- **Prompt edit (Tier 2.1a):** `prompts/results.v1.md` — six edits.
  `(Fig. N)` callouts upgraded from advisory to load-bearing; HALT
  discipline added in self-review (if figures inventory exists AND
  zero callouts in prose, halt — the orchestrator's
  `phase_embed_figures` depends on these). New manifest-emission
  step in figure selection: `<DRAFT_DIR>/figures_manifest.tsv` with
  three tab-separated columns (`paper_order_n`, `filename`,
  `inventory_lookup_name`) plus header row. Banned-tab discipline
  documented. Closing-message template extended with
  `figures_manifest.tsv emitted with K rows`. Anti-example pair
  added for the missing-callout failure mode. Output-protocol
  step 4 documents the manifest emission and links to
  `phase_embed_figures` consumer.

  **Why TSV not JSON:** memory entry
  `feedback_llm_json_unfixable_in_parser.md` (caught in beril-atlas
  L2 cache work) — unescaped `"` inside JSON string values is
  unrecoverable in the parser. Captions can contain quotes; by
  keeping captions OUT of the manifest entirely (resolved
  orchestrator-side from `figures_inventory.md`), we sidestep the
  trap. The manifest holds only filesystem-safe identifiers.

- **Post-checker (Tier 2.1b):** `tools/check_figures_manifest.py` —
  fifth post-processor in the v0.2 pattern (joins
  `check_throughline_glyphs`, `check_scope_coherence`,
  `check_overclaim`, `check_repair_scope`). Four checks:
  (1) manifest schema, (2) filename existence in figures/,
  (3) orphan-figure detection (files in figures/ without manifest
  rows — catches rewrite-loop residue), (4) callout cross-walk
  ((Fig. N) in prose must have manifest row with paper_order_n=N).
  Always exits 0; orchestrator surfaces via `emit-next-actions`.
  Wired in `paper_writer.sh` between `phase_finalize_citations` and
  `phase_embed_figures`.

- **Resolve-figures helper (Tier 2.1c):** `paper_writer_helpers.py
  resolve-figures` — joins `figures_manifest.tsv` against
  `figures_inventory.md` and emits paper_order_n → caption +
  filename for `phase_embed_figures` to consume. Captions sourced
  from inventory's caption-candidate ranking (REPORT-derived first,
  notebook-context second, filename third — already established in
  `extract_figures.py`'s output). Banned-tab + banned-newline +
  banned-bracket discipline applied to caption text (replaced with
  spaces / parentheses + stderr WARN).

### Tier 2.2 — `phase_embed_figures` + orchestrator stale-file cleanup

The consumer phase. Walks section files (`02_results.md`,
`01_methods.md`, `03_discussion.md`) for `(Fig. N)` callouts; for
each first-occurrence per N, injects
`![Figure N: <caption>](figures/<filename>)` after the sentence
containing the callout. Multi-figure callouts (`(Fig. 3 and Fig. 5)`)
inject both tags after the same sentence in N-ascending order.
Subsequent `(Fig. N)` references to an already-embedded figure stay
textual. Idempotent: re-running does not double-inject.

Sentence-end heuristic skips:
- `.` inside `Fig. <digit>` patterns (next non-space is a digit).
- `.` followed by lowercase (abbreviations like `e.g.`, `et al.`).
- `.` followed by closing brackets/quotes; the actual terminator is
  found after the bracket.

A `.` (or `!` `?`) is treated as a sentence end when the next
non-whitespace, non-bracket character is uppercase, OR when the
punctuation is followed by newline / end-of-string.

**Stale-file cleanup** (D-025, drafting-mode-only):
`phase_results` removes any pre-existing `<draft_dir>/figures/fig*.png`
before invoking results.v1. Fixes the rewrite-loop residue collisions
(`fig01_a.png` co-existing with `fig01_b.png` etc.) observed in
v0.2.1's draft_1. Skip in REPAIR_MODE.

**Rewrite-loop integration:** `phase_embed_figures` is also called
inside `phase_review_rewrite` after each rewrite-pass re-assembly.
Idempotent semantics ensure no double-injection; new (Fig. N)
callouts introduced by rewrites get embedded on the next pass.

**Test coverage:** `tests/unit/test_embed_figures.py` — 14 tests.
Sentence-end heuristic + embed core (single callout, multi-figure,
idempotency, skipped-N, multiple sections) + end-to-end fixture.

### Two new DECISIONS entries

- **D-025:** orchestrator owns `<draft_dir>/figures/` for paper-order
  names; pre-clean stale files in `phase_results`. Documents the
  contract: anything matching `figures/fig*.png` is
  orchestrator-managed and may be deleted on the next drafting run.
  User-curated illustrations should NOT live in this directory.
- **D-026:** embedded image-tag form `![Figure N: <caption>](figures/<filename>)`.
  Both figure number and caption text in alt-text. N read from
  prose's (Fig. N) callout (NOT computed by embedder). Caption
  sourced from inventory via resolve-figures.

### Test cleanup

`tests/unit/test_cli.py` — replaced stale `test_continue_stub_returns_2`
(asserted "not yet implemented" stderr text from when continue_run.py
was a stub) with `test_continue_corrupt_state_returns_2` exercising
the real OSError/ValueError catch in `continue_run.run()`.

---

## What v0.3 deliberately does NOT ship (deferred to v0.4)

| Feature | Why deferred | Workaround in v0.3 |
|---|---|---|
| **Tier 8 — figure caption richness** | v0.3 ships terse REPORT-derived captions (one noun phrase per figure). Rich ICMJE-style legends (50–200 words) defer to v0.4. Source ladder canonicalized in `smoke-test/v0_3_punch_list.md` Tier 8 — Sources 2 (notebook markdown preceding savefig) + 3 (matplotlib AST parsing) recommended starting points; Source 4 (LLM synthesis) as fallback. | User edits captions by hand in the docx before submission (standard manuscript workflow). |
| **Tier 7 — conceptual diagrams via mermaid-in-markdown** | Hard dependency on Tier 2.3 (now satisfied). v0.4 work. Renderer choice (Kroki vs mermaid-cli) is a v0.4 decision. | None — diagrams remain user-supplied. |
| **Tier 4 — interactive checkpoints (card elicitation, citation pause, throughline re-eval)** | Deferred until augmentation stream has a 2nd user. | Pump-through with scope-down default works for the operator. |
| **Item 4.3 / 5.3 — throughline re-eval + state-schema migration** | Bundled when 4.3 lands (it triggers the schema bump). | Manual user review if source artifacts changed mid-draft. |
| **Follow-up #21 — fallback_reviewer.v1 prompt tightening** | Parser is tolerant of all 3 observed forms (v0.2.0 fix); tightening the prompt is the second half of the discipline lesson. | None needed; parser handles drift. |
| **Auto-generated illustrative images** | Declined permanently 2026-04-27. | User brings illustrations via BioRender / Inkscape. |
| **PDF assembly format** | `--format pdf` rejected with explanatory stderr; post-MVP. | Use `--format docx` and convert via Word/LibreOffice. |

---

## Live retest results (2026-04-27)

Fresh `beril-paper-writer draft` + `continue --pick TL1` on
`functional_dark_matter`, end-to-end, with the v0.3 pipeline
(editable pipx install from working tree):

| Metric | Value |
|---|---|
| Total cost | **$6.49** across the full run (init → plan → throughline_pick → citation_pool → methods → results → discussion → intro → abstract → reframe_drift_audit → data_avail → finalize_citations → check_figures_manifest → embed_figures → check_scope_coherence → check_overclaim → assemble → repair_validators → review (3 passes) → emit_review_handoff). $6.49 vs ~$5 estimate; the +$1.49 is from 3 reviewer passes (rewrite loop hit hard cap) — typical for a fresh draft. |
| Wall clock | ~25 min |
| `figures_manifest.tsv` | 10 rows; schema clean; no warnings from `check_figures_manifest` |
| `phase_embed_figures` | First pass: `embedded: 10 total across 1 section(s)` (02_results.md). Rewrite-loop passes 2-3: `embedded: 0` (idempotent skip — figures already injected). |
| Final manuscript.docx | 10 inline shapes (Pictures), 10 Caption-styled paragraphs, 0 `[FIGURE MISSING]` placeholders |
| Cost circuit breaker | Did not trigger (cap was $10; cumulative $6.49) |
| Final critical findings | 2 remaining after hard-cap reached on rewrite loop — surfaced in next_actions.md per SPEC §8.3 (NOT a v0.3 regression; standard v0.2 architectural behavior). |

**Live coverage gate.** First v0.3 fresh draft passed cleanly against
all four Tier 2 acceptance criteria (manifest emission, post-checker
green, embed injection working, docx render with embedded Pictures).

---

## Architecture deltas since v0.2.1

The post-processor pattern grew from four to five:

```
v0.1.0: tools/check_throughline_glyphs.py            (plan.v1 strength glyphs)
v0.2.0: + tools/check_scope_coherence.py             (Discussion ↔ Results scope)
        + tools/check_overclaim.py                   (strong verbs vs ⚠ sub-claims)
        + tools/check_repair_scope.py                (REPAIR_MODE post-check)
v0.3.0: + tools/check_figures_manifest.py            (figures manifest + cross-walks)
```

The figure pipeline architecture introduced in v0.3:

```
phase_results
  ├─ stale-file cleanup (D-025; drafting-mode-only)
  └─ results.v1
       ├─ writes 02_results.md with (Fig. N) callouts
       ├─ copies selected figures → <DRAFT_DIR>/figures/ (paper-order rename)
       └─ emits figures_manifest.tsv  (Wrinkle A canonicalization)

phase_finalize_citations
phase_check_figures_manifest                          (advisory; surface to next_actions.md)
phase_embed_figures                                   (consumes manifest + inventory)
  └─ paper_writer_helpers.py resolve-figures          (caption resolution)
  └─ injects ![Figure N: caption](figures/<filename>) tags

phase_check_scope_coherence + phase_check_overclaim
phase_assemble                                        (concatenates → manuscript.md)

beril-paper-writer assemble <draft_dir> --format docx
  └─ tools/assemble_docx.py                           (markdown → docx via python-docx)
       └─ ![Figure N: caption](path) → Picture + Caption paragraph
```

---

## Migration notes — v0.2.1 → v0.3.0

**No state schema migration required.** `STATE_SCHEMA_VERSION = "0.1"`
unchanged in v0.3. Existing draft directories continue to work
without migration. Re-running an existing v0.2 draft via
`beril-paper-writer continue` will:
- Re-enter the resume case for the draft's current `state.json` phase
- Skip phases whose output already exists (idempotency)
- Run new v0.3 phases against existing artifacts (e.g.,
  `phase_check_figures_manifest` will run on existing draft state and
  produce `audit/figures_manifest_warnings.txt`; if the existing
  draft has no `figures_manifest.tsv`, the post-checker emits a NOTE
  rather than warnings)
- `phase_embed_figures` will no-op if no manifest exists (idempotent)

**Re-rendering an existing draft into v0.3-style docx:** use
`beril-paper-writer assemble <draft_dir> --format docx`. The
v0.3 `assemble_docx.py` renders any existing `manuscript.md`
including manuscripts authored by v0.1 / v0.2 (which won't have
`![Figure N: ...]` image tags — those manuscripts will render to
docx without embedded figures, same as v0.2 behavior, just
with proper heading/list/bold/italic styling).

---

## Known issues / caveats

- **Cosmetic: "Copied 0 figure(s)" log line in `phase_results` is
  misleading in v0.3.** The orchestrator's grep-the-prose-for-filenames
  fallback (paper_writer.sh:912-919) was meaningful in v0.1/v0.2 when
  results.v1 prose contained filenames. In v0.3 prose contains
  `(Fig. N)` callouts; the actual figure copy happens inside
  results.v1's Bash tool calls. The fallback log line reports 0 even
  on successful runs. v0.3.1 follow-up — either remove the fallback,
  reframe the log message, or change to "fallback copy: 0 (expected —
  results.v1 owns figure copy)".

- **`FIGURES_OUT_DIR` is not explicitly passed to results.v1.** The
  user_prompt template in `paper_writer.sh phase_results`
  (lines 885-902) does not include `FIGURES_OUT_DIR`; results.v1
  infers the path from `DRAFT_DIR`. This works (verified by 10/10
  Pictures in the live retest) but is brittle. v0.3.1 follow-up —
  add to the user_prompt template for explicit contract.

- **Validation failures inside the rewrite-loop are not
  auto-repaired.** `phase_repair_validators` runs ONCE in the main
  case block (after the initial assemble), not after each
  rewrite-loop re-assemble. If rewrites introduce new validator
  failures, those failures stay un-repaired through the loop. This
  is a v0.2 architectural issue, NOT a v0.3 regression. Tracked as
  v0.4 backlog (M10 architectural redesign + repair-loop integration).

- **Final critical findings remaining after hard-cap rewrite passes
  are user-edit-territory.** SPEC §8.3 hard cap of 2 rewrite passes
  is enforced; residuals surface in next_actions.md. Not a regression.

- **Tier 8 (caption richness) deferred.** Captions in v0.3 are terse
  REPORT-derived noun phrases. Rich captions are v0.4 work. See
  Tier 8 in `smoke-test/v0_3_punch_list.md` for the source ladder.

---

## Backlog leftover for v0.4+

In rough priority order:

1. **Tier 8 — figure caption richness.** Sources 2 (notebook
   markdown preceding savefig) + 3 (matplotlib AST parsing) first;
   Source 4 (LLM synthesis) as fallback. ~1–2 weeks.
2. **Tier 7 — conceptual diagrams via mermaid-in-markdown.** Hard
   dep on v0.3.0 Tier 2.3 (satisfied). Renderer pipeline + new
   `tools/check_diagrams.py` post-processor. ~2–3 weeks + ~$10–15
   smoke.
3. **Two cosmetic v0.3.1 patches** — "Copied 0 figure(s)" log and
   FIGURES_OUT_DIR not passed to user_prompt template. Both
   discoverable from a 30-min audit pass on `paper_writer.sh
   phase_results`. ~30 min total.
4. **Follow-up #21** — tighten `fallback_reviewer.v1.md` to mandate
   one canonical finding-header form.
5. **M10 architectural redesign** — route M10 (orphan citation)
   repair to a citation-pool-aware path rather than a section
   prompt.
6. **Tier 4 — interactive checkpoints.** Deferred until augmentation
   stream has a 2nd user.
7. **Items 4.3 / 5.3 — throughline re-eval + state-schema migration.**
   Bundled together when 4.3 lands.

---

*Release notes authored 2026-04-27 alongside the v0.3.0 ship cycle.
Companion to `RELEASE_NOTES.md` (v0.1.0) and `RELEASE_NOTES_v0_2.md`
(v0.2.0/v0.2.1). For per-tier scope and acceptance criteria, see
`smoke-test/v0_3_punch_list.md`.*
