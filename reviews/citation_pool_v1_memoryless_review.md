# Review: citation_pool.v1.md — Memoryless Agent Perspective

## 1. First Three Uncertainties for a Memoryless Reader

**Uncertainty 1: What exactly is "the 9-field strict citation discipline"?**

The prompt opens with "Your job is to ensure every citation…" and references the adversarial reviewer's "9-field strict citation discipline" without listing it. Line 33-34 says "(Authors / Year / Title / Venue / DOI / ID / Studied / Finding / Scope alignment / Assessment — see [adversarial_paper.v1.md](...)". That's 10 items, not 9. Line 36 admits the confusion: "the underlying Python data model in `citation_pool.py` calls this the '10-field schema' because identifier splits across several Python attributes." A memoryless agent reads this and is left uncertain: is it 9 or 10? Is DOI/ID one field or two? The prompt should resolve this upfront before the schema table. Currently the count is ambiguous and requires clicking a link to understand.

**Uncertainty 2: What does "end_of_segment=true" mean in the output-protocol section?**

Line 428-430 says "Write the JSON to `POOL_JSON_PATH` via the `Write` tool. Schema is `CitationPool.to_dict()`:" and then provides a JSON example. But the "Output protocol" section doesn't explain when or how to actually invoke Write vs. when to call Bash. The phrase "Schema is `CitationPool.to_dict()`" assumes the agent knows what that Python method returns. An agent unfamiliar with the codebase won't know if it needs to call the Python tool or just write the JSON directly. The prompt mentions `Write` tool in passing but never explicitly says "call Write with the JSON as content."

**Uncertainty 3: What happens if `RESEARCH_PLAN.md` doesn't exist?**

Line 150 says "Read `<PROJECT_ROOT>/REPORT.md` — the project's synthesized findings." Line 152 says "Read `<PROJECT_ROOT>/RESEARCH_PLAN.md` — design intent." Neither path is marked "optional." But the user prompt passes `PROJECT_ROOT` and `DRAFT_DIR` — it doesn't guarantee that either of these files exists. If the agent reads PROJECT_ROOT and finds no REPORT.md or RESEARCH_PLAN.md, what should it do? Continue anyway? Fail? The prompt doesn't say. An escape hatch ("if REPORT.md is absent, proceed with empty-report framing") would clarify the failure mode.

---

## 2. Internal Contradictions and Tensions

**Contradiction A: "Verification is non-negotiable" vs. "quick mode reduces verification depth."**

Line 453 (Important rules): "Verification is non-negotiable. No verification = no entry." But line 287-293 (Depth modes, quick): "trust entries from `EXISTING_POOL_PATH` verbatim (already verified). For entries from `EXISTING_REFERENCES_MD` or newly added candidates, still run the verification pass — but spot-check ~10 of them rather than exhaustively."

This is internally consistent if you read carefully — "quick" still verifies new entries, just spot-checks rather than exhaustively. But the phrasing "Verification is non-negotiable" followed immediately by "quick mode reduces verification depth" creates tension. A memoryless agent might interpret "reduce verification depth" as "reduce rigor," not "reduce coverage." The important-rules section should clarify: "Every new entry must pass the verification pass; `quick` mode spot-checks rather than exhaustively verifying, reducing *coverage*, not *verification floor*."

**Contradiction B: Pool is "capped at 80" (hard rule) but also "smaller than cap is often right."**

Line 258-276 say the pool is capped at 80 "per D-009" and "The cap is a budget control AND a signal-to-noise floor (past ~80 the pool's average quality drops). On approach: … At cap: stop. Do not add a 81st entry." Line 273-276 then say "The pool can be **smaller** than the cap, and often should be. Padding the pool to fill the budget is a failure mode. A pool of 30 verified, throughline-aligned entries is stronger than 70 entries where 40 are filler."

These statements are not contradictory in principle, but the juxtaposition creates ambiguity: is the agent supposed to aim for 80, or should it stop much earlier? The prompt later clarifies this (line 196-207, tier sizing: STRONG ~40–70, THIN ~25–45, EXPLORATORY ~15–30), but tier-aware framing is buried later. A memoryless agent reading the pool-cap section alone would assume the goal is to maximize towards 80. The cap-and-exhaustion section should front-load the tier guidance or add a sentence like "Default pool target per tier: see 'Tier-aware framing' below."

**Contradiction C: "Cost discipline" (line 464-467) conflicts with "no budget exhaustion escalation."**

Line 464-467 (Important rules): "Cost discipline. Standard mode WebSearch budget is ~25–40 calls. If you're approaching 60+ on a non-`deep` run, stop expanding and ship what you have with an honest 'pool truncated to budget' note."

But the workflow outlined in "Depth modes" doesn't mention a cost escape hatch. Line 294-296 (standard mode) just says "Verify every entry per the pass above. WebSearch budget ~25–40 calls." If the agent is verifying entries and hits 50 WebSearch calls on entry #40, does it stop, or continue? The prompt should explicitly state: "If WebSearch budget is approaching 60 on a non-deep run, halt verification, ship the pool with a truncated-to-budget note in your summary, and do not re-verify already-added entries."

---

## 3. Redundancy and Signal-to-Noise

**Rule: "No fabricated citations" appears 3+ times.**

- Line 449: "No fabricated citations. Ever. Plausibility is not evidence."
- Line 254-256 (Verification pass, rule 1): "Confirm the work exists." with warning "Do not 'fix' by overriding fields with the resolved values without re-checking."
- Line 345 (Anti-patterns): "Plausibility-as-evidence. Marking `assessment: supports` because the paper's title sounds aligned, without reading the abstract."
- Line 450-452 (Important rules): "No fabricated citations."

**Assessment:** This repetition is **helpful reinforcement**, not noise. Citation hallucination is explicitly called out as the single highest-risk failure mode (line 8: "Citation hallucination is the most-cited LLM paper-writer failure mode"). The rule appears in (1) opening framing, (2) verification discipline, (3) anti-patterns, (4) important-rules closing. This is intentional defensive depth against a known failure mode. **Verdict: Keep as-is.**

**Rule: "Scope alignment must be evaluated against the project's claim, not the cite's claim" appears twice.**

- Line 238-243 (Verification pass, step 4): "Set `scope_alignment` against the project's claim, not the cited paper's claim."
- Line 365-369 (Anti-patterns, scope creep): "The pool serves the *throughline*, not the project's broad subject area."

**Assessment:** The first instance is directive (how to set scope_alignment); the second is about what to include. These serve different purposes and reinforce the same principle from different angles. **Verdict: Helpful.**

**"Authors must be a list, not a string" appears at least 2x.**

- Line 66-68: "**Authors are a LIST, not a string.** `["Smith J", "Doe A", "Lee K"]`, not `"Smith J, Doe A, Lee K"`."
- Line 398-399 (Self-review pass): "`authors` is a list, not a joined string, on every entry."

**Assessment:** This is intentional repetition between the schema-explanation section and the pre-Write checklist. The checklist is meant as a standalone validation step, so the repetition serves a purpose. However, the two phrasings are slightly different (line 66 uses backtick-formatted examples; line 398 is terse). A memoryless agent checking the self-review list might not immediately connect item 3 to the earlier rule if it doesn't re-read. **Verdict: Acceptable, but could consolidate with a cross-reference: "See §Schema, bullet Authors."**

---

## 4. Missing Context — Assumption Gaps

**Gap 1: How to handle ToolSearch failures.**

Line 216-219 (Verification pass, step 1): "Use `WebSearch` (or BERIL's PubMed MCP via ToolSearch — `mcp__pubmed__search_articles`, `mcp__pubmed__convert_article_ids` — when available) to confirm the DOI / PMID resolves."

The phrase "when available" is vague. Does the agent check availability by calling ToolSearch and catching a missing-tool error? Should it try PubMed first, then fallback to WebSearch? Should it always try both? The prompt gives no fallback strategy. A memoryless agent might waste budget attempting PubMed on every entry if it's not available, or might skip to fallback too eagerly if it's available but slow.

**Recommendation:** Add a line: "If BERIL's PubMed MCP is available (attempt one call to mcp__pubmed__search_articles as a probe), use it for batch queries; otherwise fallback to WebSearch. Do not retry on failure; treat unavailability as a signal to use WebSearch for the full verification pass."

**Gap 2: No guidance on how to reconcile conflicting metadata from WebSearch.**

Line 221-223: "Mismatches between candidate metadata and resolved metadata are a fail — drop the candidate. Do not 'fix' by overriding fields with the resolved values without re-checking that the resolved work is the work you meant to cite."

This is good defensive guidance, but a memoryless agent doesn't know what "re-checking" looks like. If I find a DOI via WebSearch and the title differs by one word, is that a mismatch? If the year is off by 1, is that a fail? The prompt doesn't define "mismatch" operationally.

**Recommendation:** Add examples: "Minor mismatches (1–2 word differences in title, ±1 year if the DOI matches) are acceptable if the DOI and core metadata agree. Major mismatches (author names completely different, venue is a different journal) warrant dropping the candidate."

**Gap 3: Unclear what "read enough of the work" means.**

Line 226-230: "Read enough of the work to verify its claim. Title-only is not sufficient. Abstract minimum; for high-stakes citations (anything the throughline depends on, anything in the Methods provenance chain) read the body via WebSearch or the PubMed MCP."

The threshold of "high-stakes" is subjective. Is a citation in the Introduction-background high-stakes? Is a Methods citation about a package version high-stakes? The prompt gives examples but no rule.

**Recommendation:** "High-stakes citations = any citation the throughline's evidence map depends on (line 149), plus Methods-provenance citations (any method the paper claims to use per RESEARCH_PLAN or notebooks). For all others, abstract is sufficient."

---

## 5. Schema-Spec Alignment

Comparing the prompt's schema description (lines 46-103) against `citation_pool.py`'s `validate_entry` function (lines 208-287):

**Alignment ✓:**

- `authors` required, list of strings ✓ (Python 213-218)
- `year` 1900–2100 ✓ (Python 220-224)
- `title` non-empty, >500 chars is warning ✓ (Python 226-231)
- `venue` non-empty, canonical form ✓ (Python 233-237)
- At least one identifier (doi/pmid/pmcid/arxiv/biorxiv) ✓ (Python 240-244)
- DOI matches `^10\.\d{4,9}/` ✓ (Python 247-253)
- PMID numeric ✓ (Python 256-260)
- `studied` required non-empty ✓ (Python 263-268)
- `finding` required non-empty ✓ (Python 269-273)
- `scope_alignment` ∈ {direct, partial, mismatch} ✓ (Python 274-279)
- `assessment` ∈ {supports, partial, contradicts, orthogonal} ✓ (Python 280-285)

**Discrepancy 1: Glyphs in JSON.**

The prompt (lines 71-73) explicitly states: "`scope_alignment` and `assessment` are stored as plain lowercase words without glyphs in JSON. The renderer adds the ✓/⚠/✗/◇ glyphs when it produces `references.md`. Emitting `"✓ direct"` will fail validation."

The Python code (line 54-55, 59-69) confirms this: it stores plain strings (`SCOPE_ALIGNMENT_VALUES = ("direct", "partial", "mismatch")`) and applies glyphs only during rendering in `_SCOPE_GLYPHS` and `_ASSESSMENT_GLYPHS` dicts. ✓ **Alignment confirmed.**

**Discrepancy 2: PMCID and arXiv format validation.**

The prompt (line 54-56) specifies:
- `pmcid`: "e.g. `PMC1234567`"
- `arxiv`: "e.g. `2401.01234`"

The Python validator (lines 247-260) only checks DOI and PMID format explicitly. It does NOT validate PMCID or arXiv format. A malformed `arxiv: "not-a-valid-format"` would pass validation silently.

**Issue found:** The prompt claims format validation for arxiv/biorxiv/pmcid, but the Python validator doesn't enforce it. A memoryless agent following the prompt would assume these formats are validated and might not double-check them. The prompt should either (a) state that these formats are trusted-in (no validation), or (b) the validator should add checks.

**Recommendation to prompt:** Add a note in the schema section: "arXiv, bioRxiv, and PMCID formats are accepted as-is without syntax validation; verify via WebSearch that the identifier resolves before including the entry."

**Discrepancy 3: Pool size cap enforcement.**

The prompt (lines 258-261) states the pool is capped at 80 and mentions `MAX_BUDGET` parameter. The Python code `add_entry` function (lines 422-427) enforces this: `if len(pool.entries) >= POOL_SIZE_CAP: raise PoolFullError(...)`. ✓ **Alignment confirmed.** But the prompt doesn't mention that the agent can't write an invalid pool — the pool builder receives `MAX_BUDGET` as input (line 129, user-prompt inputs) and the validator will reject any entry that would exceed it. The prompt should clarify: "Do not attempt to add entry #81 if the pool is at 80; the Python validator will reject it."

---

## 6. Output-Protocol Clarity

The "Output protocol" section (lines 423-446) specifies 5 steps. Testing the flow for a memoryless agent:

**Step 1: "Build the in-memory pool by reading inputs and running the verification pass."**
- Clear. The agent knows to read the inputs from the user prompt.

**Step 2: "Run the self-review pass."**
- Clear checklist at lines 387-421.

**Step 3: "Write the JSON to `POOL_JSON_PATH` via the `Write` tool."**
- Action is clear: call Write with the file path and JSON content.
- **Gap:** The prompt doesn't say what happens if Write fails. Should the agent retry? Fail the entire task? The prompt should add: "If Write fails, emit an error and halt; do not proceed to validation."

**Step 4: "Run `VALIDATOR_CMD` via Bash; on validation pass, confirm with a one-line response."**
- Action is clear, but: "Run `VALIDATOR_CMD`" — the prompt earlier (lines 142-143) explains that `VALIDATOR_CMD` is passed verbatim by the user prompt. The agent should use it as-is. ✓ Clarity is OK here.
- **Gap:** What if the validator produces warnings (line 420-421 says "Warnings ... are acceptable but should be glanced over for typos")? Should the agent emit them? Suppress them? The protocol should say: "If warnings appear, emit them inline so the user can review. On validation errors, proceed to step 5. On validation pass, emit the one-line response."

**Step 5: "If validation fails, repair the JSON and re-write before declaring done. Do not declare done with a failing pool."**
- This is clear: retry Write → re-validate. But how many retries? Should the agent attempt to auto-repair, or ask the user? The prompt doesn't say. The flow is ambiguous for a memoryless agent encountering a validation error.

**Recommendation:** Add a retry strategy: "If validation fails, examine the error message and attempt one auto-repair (e.g., fix field names, add missing required fields). Re-validate. If it fails again, emit the error and ask the user to review the JSON before proceeding."

---

## 7. Best-Practice Gaps for Memoryless Tool-Using Agents

**Pattern 1: No explicit XML-tag or structured output format for the closing message.**

The prompt says (line 27, line 442-443): "Final response after `Write` succeeds is a one-line confirmation plus a count (e.g. `pool.json written, 47 entries`)."

This is an example, not a template. A memoryless agent might write:
- `"pool.json written with 47 entries"`
- `"✓ pool.json (47 entries)"`
- `"Written pool.json: 47 citations verified"`

These are all slightly different. **Recommendation:** Add a template: `"pool.json written, N entries (cap M); categories covered: [list]; uncovered: [list]"` (as specified in line 443, but move to this section as a required format, not a suggestion).

**Pattern 2: No "What to do if X assumption fails" escape hatches.**

The prompt assumes:
- `THROUGHLINE_PATH` contains a valid throughline (line 121: "If it contains multiple candidates, refuse...")
- `PROJECT_ROOT` has REPORT.md and RESEARCH_PLAN.md
- WebSearch always returns results (no timeout handling)

A memoryless agent hitting one of these failures doesn't know whether to retry, skip, or fail. **Recommendation:** Add a "Escape hatches" section:
- "If THROUGHLINE_PATH contains multiple candidates, emit: 'Error: throughline-pick must run first. Aborting.'"
- "If PROJECT_ROOT/REPORT.md is absent, proceed with empty-context framing; note in your summary."
- "If WebSearch times out, retry once. On second timeout, flag the entry as 'verification deferred' and continue with others."

**Pattern 3: Examples-before-rules ordering.**

The worked example (lines 80-98) comes BEFORE the detailed schema table and verification discipline. This is good. ✓ But the self-review checklist (lines 387-421) is all rules, no examples. A memoryless agent checking "authors is a list" might still not know what "not a joined string" failure looks like. **Recommendation:** Add 2-3 anti-examples in the checklist: "✗ authors: 'Smith J, Doe A, Lee K' (string, joined)  ✓ authors: ['Smith J', 'Doe A', 'Lee K'] (list)".

**Pattern 4: No explicit structured-retry semantics.**

The protocol says "If validation fails, repair the JSON and re-write" (line 444-445) but doesn't specify retry limits or exponential backoff. A memoryless agent could theoretically loop forever. **Recommendation:** "On validation failure, attempt up to 2 repairs (total 3 validation attempts). If the 3rd attempt fails, emit the error and halt."

---

## 8. Adversarial-Failure-Mode Coverage

The "Anti-patterns" section (lines 341-384) lists 9 failure modes. Are they realistic?

1. **Padding to budget** ✓ — realistic for an agent optimizing for "large pool."
2. **Plausibility-as-evidence** ✓ — classic LLM failure (title-only confirmation).
3. **Citation gloss** ✓ — using a vaguely-related cite for a specific claim.
4. **Identifier guessing** ✓ — constructing a DOI from pattern-matching.
5. **Scope creep into the project's general topic** ✓ — citing adjacent-but-not-directly-relevant work.
6. **Re-verifying unchanged entries on resume** — relevant for multi-phase work.
7. **Overusing is_review_article** ✓ — bias toward review articles.
8. **Treating `studied` as a topic label** ✓ — low-signal field population.

**Missing failure modes for a memoryless agent:**

- **Over-trusting prior pool entries.** Line 124 says "Trust their verification; do not re-verify" for `EXISTING_POOL_PATH`. But what if the prior pool was built with quick-mode spot-checking and missed a hallucination? A defensive agent might re-spot-check ~10% of prior entries. The prompt forbids this, which is a cost optimization but introduces risk. Should mention: "This assumes prior verification was rigorous; if you have reason to doubt a prior entry, surface it to the user."
  
- **Incomplete coverage of required categories.** The prompt expects the agent to track which categories (Background, Methods, Comparators, Conflicting, Orthogonal) are covered. Line 412-415 (self-review) checks "pool covers every category your tier requires," but this is a manual-verification step. A memoryless agent might miss an uncovered category. **Recommend adding:** "Before self-review, print a coverage matrix: Background [#], Methods [#], Comparators [#], Conflicting [#], Orthogonal [#]. If any required category has 0 entries, list it in your final summary."

- **WebSearch budget exceeded mid-verification.** Line 464-467 mentions cost discipline, but the protocol doesn't specify what the agent should do at 50 WebSearch calls if ~20 entries remain unverified. Does it stop and ship a partial pool, or does it push through? **Recommend:** "If WebSearch budget exceeds 60 on a non-deep run before all entries are verified, halt new verifications. Ship the pool with a note: 'Pool truncated to WebSearch budget; N entries verified, M unverified (dropped).'"

---

## 9. Cross-Skill Consistency: 9-Field Discipline

The prompt claims to mirror "the adversarial reviewer's **9-field strict citation discipline**" (line 32-34). Checking against adversarial_paper.v1.md lines 210-230:

**Adversarial's strict citation block format (lines 222-229):**
```
**[Authors ≤3, "et al." if 4+]. ([Year]). "[Title]." [Venue vol(issue):pages].** doi:[DOI] [PMID/PMCID/arXiv/bioRxiv]

- Studied: [organism / system / N]
- Finding: "[direct quote]" OR [quantitative result with units]
- Scope alignment: ✓ direct | ⚠ partial — reason | ✗ mismatch — reason
- Assessment: ✓ supports | ⚠ partial | ✗ contradicts | ◇ orthogonal
```

That's: Authors, Year, Title, Venue, DOI/ID, Studied, Finding, Scope alignment, Assessment = **9 fields.**

**Citation pool schema (lines 46-64):**
Same 9 content fields PLUS optional metadata flags (is_review_article, is_preprint, notes, bib_key).

**Discrepancy:** The prompt says the JSON has "9 required content slots plus optional metadata flags" (line 35-37) and then clarifies the Python model calls it "10-field schema because identifier splits across several Python attributes" (line 37-38). This is accurate — the schema has 9 conceptual content fields, but DOI/PMID/PMCID/arXiv/bioRxiv are 5 Python fields representing 1 logical identifier. The prompt resolves this, but the resolution is buried in a parenthetical after introducing the concept. **Verdict: Clarity could improve, but alignment is correct.**

**Check on glyph usage:** Adversarial's format includes glyphs (✓ direct, ⚠ partial, etc.) in the markdown rendering, but stores them as plain strings in JSON (per Python code and prompt line 71-73). The adversarial prompt's markdown snippet (lines 222-229) shows the glyphs, but the prompt doesn't clarify whether those are for human-readable rendering only. The citation_pool prompt is explicit: "stored as plain lowercase words without glyphs in JSON." ✓ **Alignment confirmed, but could be clearer in the adversarial prompt.**

---

## 10. Three Concrete Edits

**Edit 1: Clarify the field count upfront.**

**Current (line 32-38):**
```
This mirrors the adversarial reviewer's **9-field strict citation
discipline** (Authors / Year / Title / Venue / DOI / ID / Studied /
Finding / Scope alignment / Assessment — see
[adversarial_paper.v1.md][adv-paper] §"Biological-claim
verification"). Serialized for storage, the JSON has 9 required content
slots plus optional metadata flags; the underlying Python data model in
`citation_pool.py` calls this the "10-field schema" because identifier
splits across several Python attributes.
```

**Proposed replacement:**
```
This mirrors the adversarial reviewer's **9-field strict citation
discipline**: Authors / Year / Title / Venue / Studied / Finding / Scope
alignment / Assessment / Identifier (one of DOI/PMID/PMCID/arXiv/bioRxiv).
Serialized for storage, the JSON uses 9 required content fields. The
identifier is stored as 5 optional Python fields (doi, pmid, pmcid, arxiv,
biorxiv); at least one must be present. Optional metadata fields are
is_review_article, is_preprint, notes, and bib_key. See the worked example
below for the correct format.
```

**Rationale:** This clarifies the 9-vs-10 confusion upfront and removes the self-referential explanation. The identifier is listed as a single logical field but implemented as five Python optionals.

---

**Edit 2: Add a cost/retry escape hatch to the output protocol.**

**Current (line 423-446):**
```
## Output protocol

1. Build the in-memory pool by reading inputs and running the
   verification pass.
2. Run the self-review pass.
3. Write the JSON to `POOL_JSON_PATH` via the `Write` tool. Schema is
   `CitationPool.to_dict()`:
   ...
4. Run `VALIDATOR_CMD` via Bash; on validation pass, confirm with a
   one-line response:
   `"pool.json written, N entries (cap M); categories covered: [...]; uncovered: [...]"`.
5. If validation fails, repair the JSON and re-write before declaring
   done. Do not declare done with a failing pool.
```

**Proposed addition (after step 2, before step 3):**
```
2b. **Cost checkpoint.** If you have made >50 WebSearch calls and are not
   in `deep` mode, halt verification. Finalize the pool with all verified
   entries collected so far. Note in your final summary: "Pool truncated
   to WebSearch budget; N entries verified from M candidates considered."
```

**And update step 5:**
```
5. If validation fails, examine the error message. Attempt one auto-repair
   (e.g., fix field names, add missing authors list, remove glyph
   characters from scope_alignment/assessment). Re-validate. If it fails
   again, halt and emit the validation error. Do not attempt further
   repairs; the JSON structure may be fundamentally misaligned.
```

**Rationale:** This prevents runaway cost and sets clear retry limits for a memoryless agent.

---

**Edit 3: Strengthen the throughline-anchor rule with a concrete filtering step.**

**Current (line 447-459, Important rules):**
```
- **The throughline is the anchor.** Every entry should answer "which
  claim or sub-claim of the throughline does this support / inform?"
  If you can't answer, the entry doesn't belong.
```

**Proposed replacement:**
```
- **The throughline is the anchor.** After populating the pool,
  filter for alignment: For every entry, name which claim or sub-claim
  from the throughline's evidence map (THROUGHLINE_PATH, lines 320-324
  of SPEC §4.2) it serves. Entries that don't map to the throughline
  should be dropped unless they fill a high-priority Methods-provenance
  gap (e.g., a required statistical test or package) or fill a required
  category (Background/Methods) your tier demands. If you can't answer
  "which claim does this support?", the entry doesn't belong.
```

**Rationale:** This converts an abstract principle into a concrete filtering step. A memoryless agent can now check: "Does this entry appear in my throughline-to-entries mapping?" and drop it if not.

---

## Summary

The prompt is well-structured and comprehensive for a complex task. The major gaps are:
1. **Ambiguous field count** (9 vs. 10) — resolved by clarifying identifier as a logical field.
2. **Missing failure-case escape hatches** — especially for WebSearch budget exhaustion and validation-error retries.
3. **Uncertain cost-discipline enforcement** — the budget rule is stated but not integrated into the output protocol.

The schema-spec alignment is sound. The anti-patterns are realistic. The verification discipline is rigorous. The output protocol is mostly clear but needs explicit retry semantics and cost checkpoints.

For a 10-prompt suite, the skeleton template should include:
- Explicit example + non-example pairs for key fields.
- Structured-retry limits and escape hatches for each major tool (WebSearch, Write, Bash).
- Cost checkpoints integrated into the main flow, not just mentioned in passing.
- Clear handling of "this file/field/tool doesn't exist or is unavailable" scenarios.

The citation-pool prompt itself is among the stronger candidates for the suite's first prompt — it has load-bearing constraints (verification, cap, dedup) and clear failure modes. With the edits above, it would be exemplary.
