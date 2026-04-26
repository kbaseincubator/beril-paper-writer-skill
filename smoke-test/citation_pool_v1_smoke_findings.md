# citation_pool.v1 — first live-LLM smoke-test findings

**Run date:** 2026-04-25  (executed by Adam Arkin)
**Project:** `functional_dark_matter` (STRONG-tier; 14 notebooks; 271 code cells; pre-existing curated `references.md` with ~30 entries used as seed)
**Throughline:** hand-crafted single candidate (smoke-test/throughline_stub.md) — the cross-organism prioritization narrative
**Model:** `claude-opus-4-7` (Claude Code 2.x default; pin to Sonnet for dev iteration to cut cost ~3×)
**Cost / latency:** $5.42 / 524 s wall clock / 60 turns
**Verdict:** **PASS** on all 6 runbook section-6 criteria, with one substantive insight that should propagate into prompt-design memory.

---

## Pass/fail by criterion (per runbook §6)

| # | Criterion | Result |
|---|---|---|
| 1 | `pool.json` written | ✓ at `/tmp/citation_pool_smoke/draft_1/pool.json` (37 KB) |
| 2 | Schema validator returns 0 errors | ✓ `Summary: 29 entries, 0 errors, 0 warnings.` |
| 3 | Entry count in 15–30 range (target for `MAX_BUDGET=30`) | ✓ 29 entries (top of range) |
| 4 | 3/3 sampled entries spot-check correctly | ✓ Peters 2019 Mobile-CRISPRi, Price 2018 PLoS Genet, Price 2018 Nature — all real, all metadata correct |
| 5 | Closing message matches required-exact-format template | ⚠ Mostly — agent emitted a richer self-review summary BEFORE the template line; the template line itself appears verbatim at the end. Treating as pass with minor format drift; see "Closing-message format observation" below |
| 6 | Formatter step round-trips | ✓ `references.md` (23K), `bibliography.bib` (13K), `citation_map.md` (208B — empty because no prose has cited yet, correct), `pool.json` round-trips |

---

## Tool-call profile (read from stream-json log)

```
WebSearch:   39
Read:         7
Bash:         4
ToolSearch:   3
TodoWrite:    3
Grep:         1
Write:        1
Edit:         1
─────────────
Total:       59 tool calls over 60 turns
```

WebSearch dominated (~66% of all tool calls). The agent's verification work was real, not hallucinated.

**ToolSearch=3 / PubMed MCP probe.** The prompt instructs probing for `mcp__pubmed__search_articles` once at start. Three ToolSearch attempts suggest the agent tried multiple times to find a working PubMed MCP, then fell back to WebSearch for the entire run (closing message confirms: `PubMed MCP fallback-WebSearch`). The probe-once-then-fallback pattern worked as designed.

**Read=7.** Project artifacts (REPORT.md, RESEARCH_PLAN.md, references.md), the throughline stub, the schema source `citation_pool.py` (the agent self-confirmed the schema before building entries — good discipline), and a couple draft-dir checks via `Read`-equivalent.

**Edit=1.** Worth a follow-up to identify what was edited (probably mid-pool revision); not load-bearing for the test verdict.

---

## Finding A — Verification caught typos in the curated seed bibliography

**This is the unexpected-but-good behavior of the run.** The agent's verification pass found that 5 of the project's pre-existing curated `references.md` entries had errors — typos in PMIDs, an author-name misspelling, an initial-format normalization. The agent corrected them in the pool rather than carrying through the typos.

**Corrections applied (all confirmed real by Adam via PubMed lookup):**

| Reference | Field | Seed value | Corrected value |
|---|---|---|---|
| Price 2018 amino-acid GapMind (PLoS Genet) | PMID | `29324775` | `29324779` |
| Tan 2018 Pseudomonas CRISPRi (J Bacteriol) | PMID | `29311275` | `29311279` |
| Makarova 2019 archaeal dark matter | PMID | `30647141` | `30710061` |
| Peters 2019 Mobile-CRISPRi (Nat Microbiol) | author | `"Patidar R"` (third author) | `"Patino R"` |
| Vaccaro 2016 | author initials | inconsistent | normalized to `"MWW"` |

**Implication for prompt design.** The original `citation_pool.v1` prompt treated `EXISTING_REFERENCES_MD` (the project's curated bibliography) as a "seed of unverified candidates that must pass the verification pass before entering the pool" — the wording was correct but I implicitly expected verification to mostly catch *fabrications*, not *errors in the seed itself*. This run shows verification catches both. The prompt's "verify by resolution, not by trust-the-seed" rule is doing higher-value work than I designed for. **Do not regress this behavior.**

This is also a quiet vindication of the inviolable rule "No 'best-effort' identifiers": the prompt forbids carrying through guessed or constructed identifiers. Adam's curated bibliography had real but slightly-wrong PMIDs that could have looked OK to a less-careful verification pass; the prompt's discipline caught them.

**For future smoke tests** with other projects: if `EXISTING_REFERENCES_MD` exists, expect the agent to surface "seed-reference typos corrected" entries in its closing self-review summary. These are valuable; they're not noise. Treat them as a feature.

---

## Finding B — `server_tool_use.web_search_requests` counter is unreliable for our needs

The result block reported `WebSearches: 0` (`server_tool_use.web_search_requests=0`), but the actual stream-json log shows 39 `WebSearch` tool_use events. The counter evidently tallies only Anthropic's *server-side* WebSearch tool, not the WebSearch tool exposed to Claude Code agents via the standard tool harness.

**For audit logs going forward:** count `WebSearch` tool_use events directly from the stream-json log rather than trusting `server_tool_use.web_search_requests`. The Python snippet from runbook §5 (counting events by name) is what to use; the result-block counter is misleading.

**Closing-message minor drift:** the agent reported "WebSearches used: 22" in the closing template but actually made 39 calls. Either the agent counted distinct queries (some queries were retried with refined terms) or it miscounted in the close-out. The discrepancy is not load-bearing — what matters for the smoke test is that real verification happened, which the 39 events confirm.

---

## Closing-message format observation

The prompt's required-exact-format template is:

```
pool.json written, N entries (cap M, mode {quick|standard|deep},
PubMed MCP {available|fallback-WebSearch}); categories covered:
[...]; uncovered: [...]; WebSearches used: K. Next: orchestrator
must invoke `citation_pool.py format` to render references.md /
bibliography.bib / citation_map.md before discussion.v1 runs.
```

The agent emitted the template line verbatim, but **prefaced** it with a long self-review summary covering the 9-field-presence check, enum conformance, list-not-string author check, dropped citations, and the seed-reference typo corrections. Then below that, the template line.

This is not a failure — the template content is preserved and parseable — but it's a discipline drift worth noting. The prompt says "Closing-message template (required exact format)" without explicitly forbidding additional preamble. Possible refinements for future iteration:

1. Tighten the template language to "Your closing message is *only* the template line; do not preface with self-review summary." (Risk: loses useful information the user might want to see.)
2. Add a separate "Self-review summary" subsection in the prompt that the agent emits *before* the template, with explicit structure. (Codifies what the agent is already doing.)
3. Leave as-is; the template line is parseable by the orchestrator either way.

**Recommendation: option 2 or 3.** The agent's self-review preamble was substantively useful (it surfaced the typo-correction finding) — codifying it is better than suppressing it.

---

## Lessons for the orchestrator (Phase 4)

1. **`--verbose` is required when `--output-format=stream-json` + `-p`.** Add to `paper_writer.sh`'s `claude` invocations.
2. **`--permission-mode bypassPermissions` is required for non-interactive `-p` runs.** Or pre-approve all needed dirs via `--add-dir`. Either works; bypass is simpler for vetted prompts.
3. **`--model` should be pinned**, not relying on default. Claude Code's default may be Opus; for development iteration of orchestrator behavior, pin Sonnet for cost (~3× cheaper). For production runs, decide per project tier.
4. **Audit-log parsing should count tool_use events from stream-json**, not `server_tool_use.web_search_requests`. The latter is unreliable.
5. **Closing-message parsing should be lenient.** Agents may preface the template line with a self-review summary; the orchestrator's parser should grep for the template's first words ("pool.json written, ") and parse from there, ignoring earlier lines.
6. **Probe-once-then-fallback pattern (PubMed MCP)** worked as designed. Three ToolSearch attempts before fallback is fine; no need to tighten.
7. **Cost on Opus for 29-entry STRONG-tier MAX_BUDGET=30 build: $5.42.** This is a baseline. With Sonnet (`--model claude-sonnet-4-...`) expect ~$1.50–2.00.

---

## Open questions deferred

- **What did the `Edit` call modify?** One Edit event in the tool-call profile. Probably a mid-pool revision the agent made before the final Write. Not blocking; would be useful to inspect the stream-json to identify if running the smoke test again reveals it as systematic or one-off.
- **How does `quick` mode behave** vs. `standard`? The runbook offers `MAX_BUDGET=15` / `DEPTH=quick` as a fallback if `standard` was hanging. Worth a separate cheap run (~$1 on Sonnet) to confirm `quick`'s reduced-coverage behavior matches the prompt's documented intent.
- **What does the prompt do with no `EXISTING_REFERENCES_MD`?** This run had it; a fresh-build smoke test would exercise the alternative path (zero seed; build from REPORT + plan + WebSearch only). Likely cheaper run.

---

## Next-step recommendations

1. **Memorialize the seed-typo finding** in the project memory (file: `project_paper_writer_smoke_seed_typos.md`) so future prompt revisions don't accidentally regress the "verify-by-resolution-not-trust-the-seed" behavior.
2. **Commit `smoke-test/`** (this directory) so the runbook + scaffolding + this findings doc are versioned.
3. **Defer further smoke tests** until either (a) the orchestrator is far enough along that an end-to-end multi-prompt smoke test is feasible, or (b) we want to test `methods.v1` in isolation against `functional_dark_matter`'s notebook AST extraction.
4. **Phase 4 implementation can begin** with high confidence in the citation_pool.v1 prompt design.

---

*Findings written 2026-04-25 by Claude (Cowork session) based on Adam's executed run.*
